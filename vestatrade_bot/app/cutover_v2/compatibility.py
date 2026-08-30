"""Assemble a checked CompatibilityResult into a V2-owned turn candidate."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.compatibility_v2.contracts import CompatibilityResult, CompatibilityResultStatus
from app.compatibility_v2.renderer import render_compatibility_result
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatResponse

from .contracts import ProductScopeEffect, V2TurnCandidate


def _stable_id(prefix: str, *parts: object) -> str:
    material = chr(31).join(map(str, parts))
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_v2_compatibility_candidate(
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    result: CompatibilityResult,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> V2TurnCandidate | None:
    """Replace a Compatibility placeholder only after its outcome gate passes."""

    if source_snapshot is None or not result.outcome_gate_passed:
        return None
    if result.status not in {
        CompatibilityResultStatus.COMPATIBLE,
        CompatibilityResultStatus.INCOMPATIBLE,
        CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
        CompatibilityResultStatus.SOURCE_CONFLICT,
    }:
        return None
    plan_id = _stable_id(
        "compatibility_plan",
        turn_id,
        result.status.value,
        result.left.canonical_sku,
        result.right.canonical_sku,
        result.source_revision,
    )
    response = ChatResponse(
        session_id=session_id,
        answer=render_compatibility_result(result),
        products=[],
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    state_after = outcome.state_after
    previous_summary = state_after.answer_plan_summary or outcome.state_before.answer_plan_summary
    if result.selection_id is None:
        previous_summary = None
    if previous_summary is None:
        previous_summary = AnswerPlanSummary(
            plan_id=plan_id,
            semantic_signature=hashlib.sha256(
                f"compatibility:{turn_id}".encode()
            ).hexdigest(),
            task_ids=((result.task_id,) if result.task_id else ()),
            primary_action=NextActionKind.CHECK_COMPATIBILITY,
            next_step_kind="check_compatibility",
            validation_status="accepted",
            selection_id=result.selection_id,
            catalog_revision=result.source_revision,
            source_turn=state_after.turn_number,
        )
    if (
        result.selection_id is not None
        and previous_summary.selection_id is not None
        and previous_summary.selection_id != result.selection_id
    ):
        return None
    summary = previous_summary.model_copy(
        update={
            "plan_id": plan_id,
            "primary_action": NextActionKind.CHECK_COMPATIBILITY,
            "next_step_kind": "check_compatibility",
            "validation_status": "accepted",
            "delivery_status": ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
            "selection_id": result.selection_id or previous_summary.selection_id,
            "catalog_revision": result.source_revision or previous_summary.catalog_revision,
            "source_turn": state_after.turn_number,
        }
    )
    state_after = state_after.model_copy(
        update={
            "answer_plan_summary": summary,
            "last_policy": NextActionPlan(
                primary=NextAction(
                    kind=NextActionKind.CHECK_COMPATIBILITY,
                    task_id=result.task_id,
                    reason_code="grounded_v2_compatibility",
                ),
                reason_codes=("grounded_v2_compatibility",),
            ),
        }
    )
    products = tuple(
        item
        for sku in (result.left.canonical_sku, result.right.canonical_sku)
        if sku and (item := source_snapshot.product(sku)) is not None
    )
    kinds = tuple(dict.fromkeys(item.product_kind for item in products))
    return base_candidate.model_copy(
        update={
            "response": response,
            "state_after": state_after,
            "answer_plan_id": plan_id,
            "rendered_answer_id": plan_id,
            "source_revision": source_snapshot.source_revision,
            "catalog_revision": source_snapshot.source_revision,
            "validation_status": "accepted",
            "response_digest": _digest_response(response),
            "pending_command_ids": (),
            "task_acts": (TaskAct.COMPATIBILITY,),
            "product_kinds": kinds or base_candidate.product_kinds,
            "contract_versions": ("1.0",),
            "answer_status": AnswerPlanStatus.READY,
            "next_action": NextActionKind.CHECK_COMPATIBILITY,
            "product_statuses": (),
            "response_product_kinds": (),
            "response_product_roles": (),
            "compatibility_result": result,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": None,
            "semantic_accepted": True,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
