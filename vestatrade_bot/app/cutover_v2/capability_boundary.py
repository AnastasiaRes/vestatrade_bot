"""Typed fail-closed response for a turn with no safely declared executor."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.models import ChatResponse

from .contracts import (
    CapabilityBoundaryResult,
    ProductScopeEffect,
    V2TurnCandidate,
)


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_v2_uncovered_capability_boundary_candidate(
    base_candidate: V2TurnCandidate,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
    additional_reason_codes: tuple[str, ...] = (),
) -> V2TurnCandidate | None:
    """Replace an unresolved candidate without committing its proposed state.

    This is intentionally useful but narrow.  It neither executes Legacy nor
    claims that the failed semantic interpretation was accepted.  The prior
    typed state and customer-visible product scope are preserved unchanged.
    """

    if source_snapshot is None:
        return None
    reason_codes = tuple(
        dict.fromkeys(
            (
                "capability_coverage_unresolved",
                "legacy_fallback_not_allowlisted",
                *additional_reason_codes,
            )
        )
    )
    result = CapabilityBoundaryResult(
        status="capability_not_ready",
        capability_id="uncovered_turn",
        source_revision=source_snapshot.source_revision,
        reason_codes=reason_codes,
    )
    plan_id = "capability_boundary_" + hashlib.sha256(
        f"{turn_id}:{source_snapshot.source_revision}".encode("utf-8")
    ).hexdigest()[:20]
    response = ChatResponse(
        session_id=session_id,
        answer=(
            "Я пока не могу надёжно обработать этот запрос, поэтому не буду "
            "догадываться. Могу помочь с подбором товара, ценой и наличием, "
            "характеристиками, сравнением, стоимостью конкретной позиции или "
            "совместимостью. Уточните, пожалуйста, товар и что именно нужно "
            "узнать."
        ),
        products=[],
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    return base_candidate.model_copy(
        update={
            "response": response,
            "state_after": base_candidate.state_before,
            "answer_plan_id": plan_id,
            "rendered_answer_id": plan_id,
            "source_revision": source_snapshot.source_revision,
            "catalog_revision": source_snapshot.source_revision,
            "validation_status": "accepted",
            "response_digest": _digest_response(response),
            "pending_command_ids": (),
            "task_acts": (TaskAct.OTHER,),
            "product_kinds": (),
            "contract_versions": ("1.0",),
            "answer_status": AnswerPlanStatus.BOUNDARY,
            "next_action": NextActionKind.STATE_CAPABILITY_BOUNDARY,
            "product_statuses": (),
            "response_product_kinds": (),
            "response_product_roles": (),
            "selection_request": None,
            "selection_result": None,
            "comparison_request": None,
            "comparison_result": None,
            "calculation_request": None,
            "calculation_result": None,
            "product_fact_delivery": None,
            "product_fact_deliveries": (),
            "offer_fact_request": None,
            "offer_fact_result": None,
            "compatibility_request": None,
            "compatibility_result": None,
            "engineering_boundary_result": None,
            "capability_boundary_result": result,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": None,
            "semantic_accepted": False,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": reason_codes,
        }
    )
