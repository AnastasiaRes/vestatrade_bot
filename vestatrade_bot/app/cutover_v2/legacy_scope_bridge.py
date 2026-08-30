"""Validated continuity from a Legacy selection to a protected V2 follow-up.

Legacy owns its response and remains free to render it in its own way.  V2 may
only reuse the structured ``ChatResponse.products`` payload after every
customer-visible card is proven identical to the current immutable source
snapshot and one active typed selection task is unambiguous.  In particular,
this module never parses Legacy prose or searches for a replacement product.
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.answer_v2.contracts import AnswerSourceSnapshot
from app.dialogue_v2.contracts import DialogueStateV2, TaskAct, TaskStatus
from app.dialogue_v2.reducer import record_validated_legacy_selection_scope
from app.models import ChatResponse


class LegacyScopeBridgeStatus(str, Enum):
    IMPORTED = "imported"
    NOT_APPLICABLE = "not_applicable"
    REJECTED = "rejected"


class LegacyScopeBridgeAudit(BaseModel):
    """Telemetry-safe result of one attempted bridge; no response prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LegacyScopeBridgeStatus
    goal_id: str | None = None
    task_id: str | None = None
    selection_id: str | None = None
    source_revision: str | None = None
    ordered_skus: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class LegacyScopeBridgeResult(BaseModel):
    """A checked state projection kept separate from the Legacy response."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    audit: LegacyScopeBridgeAudit
    state_after: DialogueStateV2


_ACTIVE_SELECTION_STATUSES = frozenset(
    {TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
)


def _stable_id(prefix: str, *parts: object) -> str:
    material = chr(31).join(str(value) for value in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _active_selection_task(state: DialogueStateV2):
    """Return the one active search/selection task, never a best guess."""

    if state.active_goal_id is None:
        return None, "legacy_scope_active_goal_missing"
    candidates = [
        task
        for task in state.tasks
        if (
            task.target_goal_id == state.active_goal_id
            and task.act in {TaskAct.FIND, TaskAct.SELECT}
            and task.status in _ACTIVE_SELECTION_STATUSES
        )
    ]
    if not candidates:
        return None, "legacy_scope_no_active_selection_task"
    active_task_id = state.task_stack.active_task_id
    active = next(
        (item for item in candidates if item.task_id == active_task_id),
        None,
    )
    if active is not None:
        return active, "legacy_scope_active_task_resolved"
    select_tasks = [item for item in candidates if item.act == TaskAct.SELECT]
    if len(select_tasks) == 1:
        return select_tasks[0], "legacy_scope_unique_select_task_resolved"
    if len(candidates) == 1:
        return candidates[0], "legacy_scope_unique_find_task_resolved"
    return None, "legacy_scope_active_selection_task_ambiguous"


def _source_cards_match(
    response: ChatResponse,
    snapshot: AnswerSourceSnapshot,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Verify all fields a Legacy structured card actually exposes."""

    if not response.products:
        return (), ("legacy_scope_legacy_response_has_no_structured_cards",)
    if len(response.products) > 12:
        return (), ("legacy_scope_card_limit_exceeded",)

    ordered_skus = tuple(item.sku for item in response.products)
    if len(ordered_skus) != len(set(ordered_skus)):
        return (), ("legacy_scope_duplicate_public_sku",)
    for card in response.products:
        source = snapshot.product(card.sku)
        if source is None:
            return (), ("legacy_scope_sku_missing_from_source_snapshot",)
        if (
            source.price is None
            or not source.url
            or not source.currency
            or not source.stock_status
        ):
            return (), ("legacy_scope_source_card_incomplete",)
        for field_name in (
            "name",
            "price",
            "currency",
            "stock_status",
            "url",
            "image_url",
        ):
            if getattr(card, field_name) != getattr(source, field_name):
                return (), (f"legacy_scope_card_{field_name}_mismatch",)
    return ordered_skus, ()


def bridge_validated_legacy_selection_scope(
    state: DialogueStateV2,
    response: ChatResponse,
    snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> LegacyScopeBridgeResult:
    """Import a Legacy selection into typed V2 scope only when safe.

    Callers decide *where* this may be enabled (currently protected Preview).
    The function is pure: the caller performs the session write only after the
    Legacy response itself has been produced successfully.
    """

    if snapshot is None:
        return LegacyScopeBridgeResult(
            audit=LegacyScopeBridgeAudit(
                status=LegacyScopeBridgeStatus.NOT_APPLICABLE,
                reason_codes=("legacy_scope_source_snapshot_missing",),
            ),
            state_after=state,
        )
    task, task_reason = _active_selection_task(state)
    if task is None:
        return LegacyScopeBridgeResult(
            audit=LegacyScopeBridgeAudit(
                status=LegacyScopeBridgeStatus.NOT_APPLICABLE,
                goal_id=state.active_goal_id,
                source_revision=snapshot.source_revision,
                reason_codes=(task_reason,),
            ),
            state_after=state,
        )

    ordered_skus, card_reasons = _source_cards_match(response, snapshot)
    if card_reasons:
        return LegacyScopeBridgeResult(
            audit=LegacyScopeBridgeAudit(
                status=LegacyScopeBridgeStatus.REJECTED,
                goal_id=state.active_goal_id,
                task_id=task.task_id,
                source_revision=snapshot.source_revision,
                reason_codes=card_reasons,
            ),
            state_after=state,
        )

    assert state.active_goal_id is not None
    selection_id = _stable_id(
        "legacy_validated_selection",
        session_id,
        turn_id,
        state.active_goal_id,
        task.task_id,
        snapshot.source_revision,
        *ordered_skus,
    )
    delivery_id = _stable_id(
        "legacy_validated_delivery",
        session_id,
        turn_id,
        selection_id,
    )
    state_after = record_validated_legacy_selection_scope(
        state,
        selection_id=selection_id,
        catalog_revision=snapshot.source_revision,
        goal_id=state.active_goal_id,
        task_id=task.task_id,
        ordered_skus=ordered_skus,
        delivery_id=delivery_id,
        focus_sku=ordered_skus[0] if len(ordered_skus) == 1 else None,
    )
    return LegacyScopeBridgeResult(
        audit=LegacyScopeBridgeAudit(
            status=LegacyScopeBridgeStatus.IMPORTED,
            goal_id=state.active_goal_id,
            task_id=task.task_id,
            selection_id=selection_id,
            source_revision=snapshot.source_revision,
            ordered_skus=ordered_skus,
            reason_codes=(task_reason, "legacy_scope_cards_source_snapshot_exact"),
        ),
        state_after=state_after,
    )
