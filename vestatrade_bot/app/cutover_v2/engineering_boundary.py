"""Grounded V2 boundary for non-catalogue hydraulic-system calculations.

This is deliberately not a calculator and not a handoff agent.  It prevents a
system-design question from leaking into the catalogue price-calculation seam
while preserving the existing customer-visible product scope.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.answer_v2.contracts import AnswerPlanStatus
from app.answer_v2.contracts import AnswerSourceSnapshot
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

from .contracts import (
    EngineeringBoundaryResult,
    ProductScopeEffect,
    V2TurnCandidate,
)


_HYDRAULIC_SYSTEM_RE = re.compile(
    r"(?iu)\b(?:гидравлическ\w*\s+(?:расч[её]т\w*|сопротивлен\w*)|"
    r"(?:рассчита\w*|посчита\w*)\s+(?:систем\w*|сопротивлен\w*))\b"
)
_SYSTEM_CONTEXT_RE = re.compile(
    r"(?iu)\b(?:систем\w*|контур\w*|двухтруб\w*|отоплен\w*|т[её]пл\w*\s+пол\w*|дом\w*)\b"
)
_REQUIRED_INPUTS = (
    "длины и диаметры участков",
    "материал труб",
    "расходы по контурам",
    "установленная арматура и оборудование",
)


def _stable_id(prefix: str, *parts: object) -> str:
    material = chr(31).join(map(str, parts))
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def hydraulic_system_calculation_evidence(message: str) -> str | None:
    """Return a precise customer span only for a system design calculation."""

    match = _HYDRAULIC_SYSTEM_RE.search(message)
    if match is None or _SYSTEM_CONTEXT_RE.search(message) is None:
        return None
    return match.group(0)


def build_v2_hydraulic_system_boundary_candidate(
    message: str,
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> V2TurnCandidate | None:
    """Build a delivery-ready V2 boundary without invoking Calculate or Legacy."""

    evidence = hydraulic_system_calculation_evidence(message)
    if evidence is None or source_snapshot is None:
        return None
    task_id = outcome.next_action_plan.primary.task_id if outcome.next_action_plan and outcome.next_action_plan.primary else None
    goal_id = next(
        (
            task.target_goal_id
            for task in outcome.state_after.tasks
            if task.task_id == task_id
        ),
        None,
    )
    result = EngineeringBoundaryResult(
        status="capability_not_ready",
        topic="hydraulic_system_calculation",
        task_id=task_id,
        goal_id=goal_id,
        source_revision=source_snapshot.source_revision,
        evidence=evidence,
        required_inputs=_REQUIRED_INPUTS,
        reason_codes=("hydraulic_system_calculation_capability_not_ready",),
    )
    plan_id = _stable_id(
        "hydraulic_boundary_plan",
        turn_id,
        source_snapshot.source_revision,
        evidence,
    )
    answer = (
        "По одной площади гидравлическое сопротивление системы рассчитать нельзя. "
        "Нужны длины и диаметры участков, материал труб, расходы по контурам "
        "и установленная арматура. Могу помочь собрать эти данные для расчёта."
    )
    response = ChatResponse(
        session_id=session_id,
        answer=answer,
        products=[],
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    summary = AnswerPlanSummary(
        plan_id=plan_id,
        semantic_signature=hashlib.sha256(
            f"hydraulic-system-boundary:{turn_id}".encode()
        ).hexdigest(),
        task_ids=((task_id,) if task_id else ()),
        primary_action=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        next_step_kind="hydraulic_system_calculation_boundary",
        validation_status="accepted",
        delivery_status=ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
        selection_id=(
            outcome.state_after.answer_plan_summary.selection_id
            if outcome.state_after.answer_plan_summary is not None
            else None
        ),
        catalog_revision=source_snapshot.source_revision,
        source_turn=outcome.state_after.turn_number,
    )
    state_after = outcome.state_after.model_copy(
        update={
            "answer_plan_summary": summary,
            "last_policy": NextActionPlan(
                primary=NextAction(
                    kind=NextActionKind.STATE_CAPABILITY_BOUNDARY,
                    task_id=task_id,
                    reason_code="hydraulic_system_calculation_capability_not_ready",
                ),
                reason_codes=("hydraulic_system_calculation_capability_not_ready",),
            ),
        }
    )
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
            "task_acts": (TaskAct.OTHER,),
            "contract_versions": ("1.0",),
            "answer_status": AnswerPlanStatus.READY,
            "next_action": NextActionKind.STATE_CAPABILITY_BOUNDARY,
            "product_statuses": (),
            "response_product_kinds": (),
            "response_product_roles": (),
            "engineering_boundary_result": result,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": None,
            "semantic_accepted": True,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
