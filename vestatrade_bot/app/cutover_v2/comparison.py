"""Assemble a V2-owned response from a checked ComparisonResult."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.comparison_v2.contracts import ComparisonResult, ComparisonResultStatus
from app.comparison_v2.renderer import render_comparison_result
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
    return f"{prefix}_{hashlib.sha256(chr(31).join(map(str, parts)).encode()).hexdigest()[:20]}"


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_v2_comparison_candidate(
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    result: ComparisonResult,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> V2TurnCandidate | None:
    """Replace the placeholder plan only after comparison outcome-gate passes."""

    if source_snapshot is None or not result.outcome_gate_passed:
        return None
    if result.status not in {
        ComparisonResultStatus.COMPARED,
        ComparisonResultStatus.NEED_CLARIFICATION,
        ComparisonResultStatus.NOT_COMPARABLE,
        ComparisonResultStatus.SOURCE_CONFLICT,
    }:
        return None
    products = tuple(
        item
        for sku in result.compared_skus
        if (item := source_snapshot.product(sku)) is not None
    )
    if result.status == ComparisonResultStatus.COMPARED and len(products) != len(result.compared_skus):
        return None
    names = {item.sku: item.name for item in products}
    plan_id = _stable_id(
        "comparison_plan",
        turn_id,
        result.status.value,
        result.selection_id,
        result.source_revision,
        *result.compared_skus,
    )
    response = ChatResponse(
        session_id=session_id,
        answer=render_comparison_result(result, names=names),
        # A comparison never re-delivers cards: preserving the existing V2
        # scope is a comparison outcome-gate requirement.
        products=[],
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    state_after = outcome.state_after
    previous_summary = state_after.answer_plan_summary or outcome.state_before.answer_plan_summary
    # An empty customer-visible scope must not be resurrected merely because a
    # stale typed state still remembers an old selection.
    if result.selection_id is None:
        previous_summary = None
    if previous_summary is None:
        # A compare request without shown cards is still a useful, safe V2
        # answer: explain the single missing prerequisite.  It creates no
        # customer-visible product scope and cannot trigger a global search.
        previous_summary = AnswerPlanSummary(
            plan_id=plan_id,
            semantic_signature=hashlib.sha256(
                f"comparison-no-scope:{turn_id}".encode()
            ).hexdigest(),
            task_ids=((result.task_id,) if result.task_id else ()),
            primary_action=NextActionKind.COMPARE,
            next_step_kind="compare_candidates",
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
            "primary_action": NextActionKind.COMPARE,
            "next_step_kind": "compare_candidates",
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
                    kind=NextActionKind.COMPARE,
                    task_id=result.task_id,
                    reason_code="grounded_v2_comparison",
                ),
                reason_codes=("grounded_v2_comparison",),
            ),
        }
    )
    kinds = tuple(dict.fromkeys(item.product_kind for item in products))
    if not kinds and base_candidate.product_kinds:
        kinds = base_candidate.product_kinds
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
            "task_acts": (TaskAct.COMPARE,),
            "product_kinds": kinds,
            "contract_versions": ("1.0",),
            "answer_status": AnswerPlanStatus.READY,
            "next_action": NextActionKind.COMPARE,
            "product_statuses": (),
            "response_product_kinds": (),
            "response_product_roles": (),
            "comparison_result": result,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": None,
            "semantic_accepted": True,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
