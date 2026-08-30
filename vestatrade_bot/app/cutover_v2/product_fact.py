"""Assemble a V2-owned direct product-fact candidate from checked evidence."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.diagnostic_telemetry import record_passport_event
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    NextAction,
    NextActionKind,
    NextActionPlan,
    RequestedInformationOutput,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatProductSummary, ChatResponse
from app.product_fact_evidence import (
    ProductFactEvidence,
    ProductFactStatus,
    ProductReferenceKind,
    render_product_fact_evidence,
)

from .contracts import ProductScopeEffect, V2TurnCandidate


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


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


def build_v2_product_fact_candidate(
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    evidence: ProductFactEvidence,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> V2TurnCandidate | None:
    """Replace only a typed direct-answer boundary with an evidence result.

    The cutover safety gate and delivery policy remain mandatory.  The bounded
    product-fact detector may correct an overly broad planner action label and
    may supply a minimal typed turn when the general-purpose semantic form was
    rejected.  That rescue is intentionally limited to a recognized predicate
    plus an explicit/scoped product reference (or its fail-closed ambiguity).
    It cannot create an answer from an arbitrary rejected semantic frame.
    """

    if source_snapshot is None:
        return None
    action = (
        outcome.next_action_plan.primary
        if outcome.next_action_plan is not None
        else NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            reason_code="bounded_product_fact_parser",
        )
    )
    reference = evidence.request.product_ref

    products = tuple(
        item
        for sku in reference.candidate_skus
        if (item := source_snapshot.product(sku)) is not None
    )
    if reference.candidate_skus and len(products) != len(reference.candidate_skus):
        return None
    product_kinds = tuple(dict.fromkeys(item.product_kind for item in products))

    public_products: list[ChatProductSummary] = []
    product_statuses: tuple[str, ...] = ()
    response_kinds = ()
    response_roles = ()
    if reference.canonical_sku:
        source = source_snapshot.product(reference.canonical_sku)
        if (
            source is not None
            and source.price is not None
            and source.currency
            and source.stock_status
            and source.url
        ):
            public_products.append(
                ChatProductSummary(
                    sku=source.sku,
                    name=source.name,
                    price=source.price,
                    currency=source.currency,
                    stock_status=source.stock_status,
                    url=source.url,
                    image_url=source.image_url,
                )
            )
            product_statuses = ("exact",)
            response_kinds = (source.product_kind,)
            response_roles = (source.role,)

    plan_id = _stable_id(
        "product_fact_plan",
        turn_id,
        evidence.request.predicate,
        *reference.candidate_skus,
        evidence.status.value,
        evidence.value,
        evidence.document,
    )
    response = ChatResponse(
        session_id=session_id,
        answer=render_product_fact_evidence(evidence),
        products=public_products,
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )

    state_after = outcome.state_after
    summary = state_after.answer_plan_summary
    if summary is None:
        turn_number = max(
            state_after.turn_number,
            outcome.state_before.turn_number + 1,
        )
        semantic_signature = hashlib.sha256(
            (
                "bounded_product_fact_parser\x1f"
                f"{turn_id}\x1f{evidence.request.predicate}\x1f"
                f"{evidence.request.product_ref.raw}"
            ).encode("utf-8")
        ).hexdigest()
        summary = AnswerPlanSummary(
            plan_id=plan_id,
            semantic_signature=semantic_signature,
            task_ids=(),
            primary_action=NextActionKind.ANSWER_DIRECT_QUESTION,
            next_step_kind="provide_direct_answer",
            validation_status="accepted",
            source_turn=turn_number,
        )
        state_after = state_after.model_copy(
            update={
                "turn_number": turn_number,
                "last_policy": NextActionPlan(
                    primary=NextAction(
                        kind=NextActionKind.ANSWER_DIRECT_QUESTION,
                        reason_code="bounded_product_fact_parser",
                    ),
                    reason_codes=("bounded_product_fact_parser",),
                ),
                "answer_plan_summary": summary,
                "applied_turn_ids": tuple(
                    dict.fromkeys((*state_after.applied_turn_ids, turn_id))
                ),
            }
        )
    requested_outputs = summary.information_requested_outputs
    if evidence.status == ProductFactStatus.ANSWERED:
        fulfilled = requested_outputs or (RequestedInformationOutput.EXPLANATION,)
        unavailable = ()
        information_reasons = ("verified_product_fact_answered",)
    else:
        fulfilled = ()
        unavailable = requested_outputs
        information_reasons = (evidence.reason_code,)
    summary = summary.model_copy(
        update={
            "plan_id": plan_id,
            "primary_action": NextActionKind.ANSWER_DIRECT_QUESTION,
            "question_fact": None,
            "question_id": None,
            "question_task_id": None,
            "question_goal_id": None,
            "next_step_kind": "provide_direct_answer",
            "validation_status": "accepted",
            "delivery_status": ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
            "information_fulfilled_outputs": fulfilled,
            "information_unavailable_outputs": unavailable,
            "information_reason_codes": information_reasons,
        }
    )
    state_after = state_after.model_copy(update={"answer_plan_summary": summary})

    task_acts = base_candidate.task_acts
    if not task_acts:
        task = next(
            (
                item
                for item in state_after.tasks
                if action.task_id is not None and item.task_id == action.task_id
            ),
            None,
        )
        task_acts = (task.act,) if task is not None else (TaskAct.EXPLAIN,)

    record_passport_event(
        event="product_fact_v2_candidate",
        status="accepted",
        owner_candidate="v2",
        predicate=evidence.request.predicate,
        product_reference_kind=reference.kind.value,
        raw_product_reference=reference.raw,
        canonical_sku=reference.canonical_sku,
        candidate_skus=list(reference.candidate_skus),
        evidence_status=evidence.status.value,
        evidence_source=evidence.source_kind,
        evidence_document=evidence.document,
        evidence_section=evidence.section,
        evidence_fragment=evidence.quote,
        evidence_value=evidence.value,
        evidence_unit=evidence.unit,
        evidence_verifier_status=evidence.verifier_status,
        reason=evidence.reason_code,
        semantic_source=(
            "general_semantic_form"
            if outcome.status == "applied" and base_candidate.semantic_accepted
            else "bounded_product_fact_parser"
        ),
        general_semantic_status=outcome.status,
        general_semantic_skip_reason=outcome.skip_reason,
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
            "task_acts": task_acts,
            "product_kinds": product_kinds,
            "contract_versions": ("1.0",),
            "answer_status": (
                AnswerPlanStatus.READY
                if evidence.status == ProductFactStatus.ANSWERED
                else AnswerPlanStatus.PARTIAL
            ),
            "next_action": NextActionKind.ANSWER_DIRECT_QUESTION,
            "product_statuses": product_statuses,
            "response_product_kinds": response_kinds,
            "response_product_roles": response_roles,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": (
                reference.canonical_sku
                if reference.canonical_sku
                and source_snapshot.product(reference.canonical_sku) is not None
                else None
            ),
            "semantic_accepted": True,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
