"""Build a V2-owned response from a checked OfferFactResult."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatProductSummary, ChatResponse
from app.offer_fact_v2.contracts import OfferFactKind, OfferFactResult, OfferFactStatus
from app.offer_fact_v2.service import render_offer_fact_result

from .contracts import ProductScopeEffect, V2TurnCandidate


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(map(str, parts)).encode()).hexdigest()[:20]}"


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_v2_offer_fact_candidate(
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    result: OfferFactResult,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
) -> V2TurnCandidate | None:
    if source_snapshot is None or not result.outcome_gate_passed:
        return None
    if result.status not in {OfferFactStatus.ANSWERED, OfferFactStatus.NEED_CLARIFICATION}:
        return None
    plan_id = _stable_id(
        "offer_fact_plan",
        turn_id,
        result.fact_kind.value,
        result.selection_id,
        result.sku,
        result.status.value,
    )
    public_products: list[ChatProductSummary] = []
    product_kinds = base_candidate.product_kinds
    roles = ()
    if result.sku is not None:
        product = source_snapshot.product(result.sku)
        if (
            product is None
            or product.price is None
            or not product.currency
            or not product.stock_status
            or not product.url
        ):
            return None
        public_products.append(
            ChatProductSummary(
                sku=product.sku,
                name=product.name,
                price=product.price,
                currency=product.currency,
                stock_status=product.stock_status,
                url=product.url,
                image_url=product.image_url,
            )
        )
        product_kinds = (product.product_kind,)
        roles = (product.role,)
    response = ChatResponse(
        session_id=session_id,
        answer=render_offer_fact_result(result),
        products=public_products,
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    # The dialogue policy exposes all direct offer facts through one stable
    # action kind.  Their exact type remains in ``OfferFactResult`` and the
    # associated ``TaskAct`` below.
    primary_action = NextActionKind.ANSWER_DIRECT_QUESTION
    task_act = {
        OfferFactKind.PRICE: TaskAct.CHECK_PRICE,
        OfferFactKind.STOCK: TaskAct.CHECK_STOCK,
        OfferFactKind.LINK: TaskAct.GET_LINK,
    }[result.fact_kind]
    state_after = outcome.state_after
    summary = state_after.answer_plan_summary or outcome.state_before.answer_plan_summary
    if summary is None:
        summary = AnswerPlanSummary(
            plan_id=plan_id,
            semantic_signature=hashlib.sha256(
                f"offer-fact:{turn_id}:{result.fact_kind.value}".encode()
            ).hexdigest(),
            task_ids=((result.task_id,) if result.task_id else ()),
            primary_action=primary_action,
            next_step_kind="provide_offer_fact",
            validation_status="accepted",
            source_turn=state_after.turn_number,
        )
    if (
        result.selection_id is not None
        and summary.selection_id is not None
        and result.selection_id != summary.selection_id
    ):
        return None
    summary = summary.model_copy(
        update={
            "plan_id": plan_id,
            "primary_action": primary_action,
            "next_step_kind": "provide_offer_fact",
            "validation_status": "accepted",
            "delivery_status": ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
            "selection_id": result.selection_id or summary.selection_id,
            "catalog_revision": result.source_revision or summary.catalog_revision,
            "source_turn": state_after.turn_number,
            "question_fact": None,
            "question_id": None,
            "question_task_id": None,
            "question_goal_id": None,
        }
    )
    state_after = state_after.model_copy(
        update={
            "answer_plan_summary": summary,
            "last_policy": NextActionPlan(
                primary=NextAction(
                    kind=primary_action,
                    task_id=result.task_id,
                    reason_code="grounded_v2_offer_fact",
                ),
                reason_codes=("grounded_v2_offer_fact",),
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
            "task_acts": (task_act,),
            "product_kinds": product_kinds,
            "contract_versions": ("1.0",),
            "answer_status": AnswerPlanStatus.READY,
            "next_action": primary_action,
            "product_statuses": (("exact",) if public_products else ()),
            "response_product_kinds": product_kinds if public_products else (),
            "response_product_roles": roles,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": result.sku,
            "semantic_accepted": True,
            "contracts_resolved": True,
            "external_side_effect_started": False,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
