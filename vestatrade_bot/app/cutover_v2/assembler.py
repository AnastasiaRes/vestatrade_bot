"""Build a public response only from a validated Stage 5 answer candidate."""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import (
    AnswerPlanStatus,
    AnswerSourceSnapshot,
    ProductPresentationStatus,
)
from app.catalog_v2.contracts import ProductKind
from app.catalog_v2.selection import (
    build_selection_request,
    build_selection_result,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.dialogue_v2.contracts import TaskAct
from app.models import ChatProductSummary, ChatResponse

from .contracts import V2TurnCandidate


_NON_DELIVERABLE_ANSWER_STATUSES = frozenset(
    {
        AnswerPlanStatus.BOUNDARY,
        AnswerPlanStatus.UNSUPPORTED,
        AnswerPlanStatus.REJECTED,
    }
)
_MAX_RENDERED_ANSWER_LENGTH = 12_000
_PRODUCT_CONTRACT_TASK_ACTS = frozenset(
    {
        TaskAct.FIND,
        TaskAct.SELECT,
        TaskAct.COMPARE,
        TaskAct.CHECK_PRICE,
        TaskAct.CHECK_STOCK,
        TaskAct.GET_LINK,
    }
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


def build_v2_turn_candidate(
    outcome: DialogueV2Outcome,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
    turn_id: str,
    original_utterance: str = "",
    previously_delivered_products: tuple[object, ...] = (),
    current_product_focus: str | None = None,
) -> V2TurnCandidate:
    """Fail closed unless text, cards, sources, contracts and state agree."""

    state_before = outcome.state_before
    state_after = outcome.state_after
    reasons: list[str] = []
    planning = outcome.answer_planning
    rendering = outcome.response_rendering
    validation = outcome.grounding_validation
    answer_plan = planning.answer_plan if planning is not None else None
    rendered = rendering.rendered_answer if rendering is not None else None

    if outcome.status != "applied":
        reasons.append(f"dialogue_v2_{outcome.status}")
    if outcome.stage5_error:
        reasons.append("stage5_pipeline_error")
    if answer_plan is None:
        reasons.append("answer_plan_missing")
    elif answer_plan.status in _NON_DELIVERABLE_ANSWER_STATUSES:
        reasons.append(f"answer_plan_status_{answer_plan.status.value}_not_deliverable")
    if rendered is None:
        reasons.append("rendered_answer_missing")
    elif len(rendered.text) >= _MAX_RENDERED_ANSWER_LENGTH:
        reasons.append("rendered_answer_length_limit_exceeded")
    if validation is None or validation.status != "accepted":
        reasons.append("grounding_not_accepted")
    if source_snapshot is None:
        reasons.append("answer_source_snapshot_missing")

    cards: list[ChatProductSummary] = []
    product_statuses: list[str] = []
    if answer_plan is not None and source_snapshot is not None:
        for presentation in answer_plan.products:
            source = source_snapshot.product(presentation.sku)
            if source is None:
                reasons.append("presented_product_source_missing")
                continue
            if source.product_kind != presentation.product_kind or source.role != presentation.role:
                reasons.append("presented_product_identity_mismatch")
                continue
            if source.price is None or not source.currency:
                reasons.append("presented_product_price_missing")
                continue
            if not source.stock_status:
                reasons.append("presented_product_stock_missing")
                continue
            if not source.url:
                reasons.append("presented_product_url_missing")
                continue
            cards.append(
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
            product_statuses.append(presentation.status.value)

        if len(cards) != len(answer_plan.products):
            reasons.append("answer_plan_card_set_incomplete")
        if len(cards) > 5:
            reasons.append("public_card_limit_exceeded")

    response = None
    if answer_plan is not None and rendered is not None and not reasons:
        response = ChatResponse(
            session_id=session_id,
            answer=rendered.text,
            products=cards,
            need_handoff=False,
            handoff_status="none",
            handoff_ticket_id=None,
            debug={},
        )

    selection_request = build_selection_request(
        outcome,
        source_snapshot,
        original_utterance=original_utterance,
        previously_delivered_products=previously_delivered_products,
        current_product_focus=current_product_focus,
    )
    selection_result = None
    if selection_request is not None and source_snapshot is not None:
        selection_result = build_selection_result(
            selection_request,
            outcome,
            source_snapshot,
            response.products if response is not None else (),
        )
        if not selection_result.outcome_gate_passed:
            reasons.append(selection_result.reason_code)

    catalog = outcome.catalog_planning
    resolutions = catalog.contract_resolutions if catalog is not None else ()
    current_task_ids = set(answer_plan.task_ids if answer_plan is not None else ())
    task_by_id = {task.task_id: task for task in state_after.tasks}
    task_acts = tuple(
        dict.fromkeys(
            task_by_id[task_id].act
            for task_id in (answer_plan.task_ids if answer_plan is not None else ())
            if task_id in task_by_id
        )
    )
    # Product contracts prove catalogue compatibility.  Independent tasks in
    # the same turn (delivery explanation, general explanation or handoff) do
    # not need a product contract.  Unknown task ids remain contract-required
    # so a malformed/missing product task cannot bypass this fail-closed gate.
    contract_required_task_ids = {
        task_id
        for task_id in current_task_ids
        if task_id not in task_by_id
        or task_by_id[task_id].act in _PRODUCT_CONTRACT_TASK_ACTS
    }
    relevant_resolutions = tuple(
        item
        for item in resolutions
        if item.task_id in contract_required_task_ids
    )
    # Rollout cells need the typed product identity even when the requested act
    # itself is not a catalogue operation (for example an explanation or a
    # delivery question about the selected product).  That identity can come
    # from the task resolution itself or another task sharing the same typed
    # goal.  It is metadata only: it does not relax the contract gate above.
    current_goal_ids = {
        task_by_id[task_id].target_goal_id
        for task_id in current_task_ids
        if task_id in task_by_id
        and task_by_id[task_id].target_goal_id is not None
    }
    identity_resolutions = tuple(
        item
        for item in resolutions
        if item.task_id in current_task_ids
        or (item.goal_id is not None and item.goal_id in current_goal_ids)
    )
    product_kinds = tuple(
        dict.fromkeys(
            item.product_kind
            for item in identity_resolutions
            if item.product_kind != ProductKind.UNSUPPORTED
        )
    )
    resolved_task_ids = {
        item.task_id
        for item in relevant_resolutions
        if item.status.value == "resolved" and item.contract_id
    }
    contracts_resolved = bool(answer_plan is not None) and (
        not contract_required_task_ids
        or (
            resolved_task_ids == contract_required_task_ids
            and all(
                item.status.value == "resolved" and item.contract_id
                for item in relevant_resolutions
            )
        )
    )
    for resolution in relevant_resolutions:
        if resolution.status.value == "resolved":
            if resolution.contract_id:
                continue
            reasons.append("product_contract_id_missing")
            reasons.extend(resolution.reason_codes)
            continue
        reasons.append(f"product_contract_{resolution.status.value}")
        reasons.extend(resolution.reason_codes)
    if contract_required_task_ids and not relevant_resolutions:
        reasons.append("product_contract_resolution_missing")
    if contract_required_task_ids and resolved_task_ids != contract_required_task_ids:
        contracts_resolved = False
        reasons.append("not_all_answer_tasks_have_contracts")

    pending_commands = tuple(
        entry.command.command_id
        for entry in state_after.commerce_outbox
        if entry.command is not None
    )
    if pending_commands:
        reasons.append("pending_external_commands_not_live_enabled")

    contract_versions = ("1.0",) if relevant_resolutions else ()
    semantic_accepted = outcome.status == "applied" and outcome.reduction is not None
    eligible = bool(
        response is not None
        and semantic_accepted
        and contracts_resolved
        and not reasons
    )
    if not eligible and not reasons:
        # This should be unreachable, but an opaque rejected candidate is not
        # actionable in canary telemetry. Preserve a stable fail-closed cause.
        reasons.append("v2_candidate_ineligible_without_specific_gate")
    return V2TurnCandidate(
        turn_id=turn_id,
        response=response,
        state_before=state_before,
        state_after=state_after,
        answer_plan_id=(answer_plan.plan_id if answer_plan else None),
        rendered_answer_id=(rendered.plan_id if rendered else None),
        source_revision=(source_snapshot.source_revision if source_snapshot else None),
        catalog_revision=(source_snapshot.source_revision if source_snapshot else None),
        validation_status=(validation.status if validation else "not_run"),
        response_digest=(_digest_response(response) if response else None),
        pending_command_ids=pending_commands,
        task_acts=task_acts,
        product_kinds=product_kinds,
        contract_versions=contract_versions,
        answer_status=(answer_plan.status if answer_plan else None),
        next_action=(answer_plan.primary_action if answer_plan else None),
        product_statuses=tuple(product_statuses),
        response_product_kinds=tuple(
            item.product_kind for item in (answer_plan.products if answer_plan else ())
        ),
        response_product_roles=tuple(
            item.role for item in (answer_plan.products if answer_plan else ())
        ),
        selection_request=selection_request,
        selection_result=selection_result,
        semantic_accepted=semantic_accepted,
        contracts_resolved=contracts_resolved,
        external_side_effect_started=False,
        eligible_for_delivery=eligible,
        rejection_reason_codes=tuple(dict.fromkeys(reasons)),
    )


class ChatResponseAssemblerV2:
    """Stateless named boundary used by delivery controllers and tests."""

    assemble = staticmethod(build_v2_turn_candidate)
