"""Typed outcome gate for the native V2 single-category catalogue path."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from app.answer_v2.contracts import AnswerSourceSnapshot
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintPolarity,
    ConstraintStatus,
    ConstraintStrength,
    DialogueStateV2,
    NextActionKind,
    ProductCategory,
    ProductRole,
    TaskAct,
)

from .contracts import (
    CandidateStatus,
    CatalogSearchPlan,
    CatalogSearchStage,
    FactStrength,
    ProductKind,
    ReadinessStatus,
    SearchConstraint,
    SelectionConstraintDisposition,
    SelectionFactInput,
    SelectionProductCard,
    SelectionRequest,
    SelectionRequestAction,
    SelectionResult,
    SelectionResultStatus,
    PresentedSelectionProduct,
)
from .registry import ProductContractRegistry
from .registry import normalize_identity

if TYPE_CHECKING:
    from app.dialogue_v2.controller import DialogueV2Outcome


_SELECTION_ACTIONS = {
    NextActionKind.SEARCH_EXACT,
    NextActionKind.RECOMMEND_ONE,
    NextActionKind.SHOW_PRELIMINARY_OPTIONS,
    NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS,
    NextActionKind.ASK_DECISION_CHANGING_QUESTION,
}


def bind_exact_named_product(
    state: DialogueStateV2,
    catalog_snapshot: tuple[Any, ...],
) -> DialogueStateV2:
    """Bind one exact full catalogue name to SKU and kind, never fuzzily.

    The semantic layer supplies current-turn evidence; this adapter performs a
    read-only exact normalized identity lookup in the existing source snapshot.
    Zero or multiple matches remain unresolved and are never guessed.
    """

    if not catalog_snapshot:
        return state
    goals = list(state.product_goals)
    constraints = list(state.constraints)
    changed = False
    registry = ProductContractRegistry()
    for index, goal in enumerate(goals):
        if (
            goal.confirmed_turn != state.turn_number
            or goal.role != ProductRole.TARGET
            or not goal.evidence
        ):
            continue
        evidence_identity = normalize_identity(goal.evidence)
        exact_name_inputs = {evidence_identity}
        for prefix in (
            "покажите ",
            "покажи ",
            "предложите ",
            "предложи ",
            "найдите ",
            "найди ",
        ):
            if evidence_identity.startswith(prefix):
                exact_name_inputs.add(evidence_identity[len(prefix) :].strip())
        matches = tuple(
            item
            for item in catalog_snapshot
            if normalize_identity(item.name) in exact_name_inputs
        )
        if len(matches) != 1:
            continue
        product = matches[0]
        contract = registry.for_kind(product.product_kind)
        if contract is None:
            continue
        already_bound = any(
            fact.active
            and fact.goal_id == goal.goal_id
            and normalize_identity(fact.name) == "sku"
            for fact in constraints
        )
        if not already_bound:
            fact_id = hashlib.sha256(
                f"exact-name:{state.turn_number}:{goal.goal_id}:{product.sku}".encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
            constraints.append(
                ConstraintFactV2(
                    fact_id=f"fact_{fact_id}",
                    name="sku",
                    value=product.sku,
                    status=ConstraintStatus.KNOWN,
                    polarity=ConstraintPolarity.REQUIRED,
                    strength=ConstraintStrength.HARD,
                    evidence=goal.evidence,
                    source="catalog_exact_name_resolution",
                    confidence=goal.confidence,
                    goal_id=goal.goal_id,
                    source_turn=state.turn_number,
                )
            )
        # Numeric anchors inside an exact model designation describe that
        # catalogue identity; they are not independent customer requirements.
        # Keeping them as exact filters can reject the named product against a
        # more precise structured rating (for example designation head 4 vs
        # catalogue maximum 4.2 m).  Remove only same-turn semantic facts whose
        # complete evidence is embedded in this exact name.  Separately stated
        # technical requirements keep their own evidence and remain active.
        designation_fact_names = {
            "diameter_mm",
            "connection_size",
            "duty_point_flow_l_h",
            "duty_point_head_m",
            "max_flow_l_h",
            "max_head_m",
            "mounting_length_mm",
        }
        constraints = [
            fact
            for fact in constraints
            if not (
                fact.goal_id == goal.goal_id
                and fact.source_turn == state.turn_number
                and fact.source == "semantic_interpreter"
                and fact.name in designation_fact_names
                and normalize_identity(fact.evidence)
                and normalize_identity(fact.evidence) in evidence_identity
            )
        ]
        category = (
            ProductCategory(contract.category)
            if contract.category in {item.value for item in ProductCategory}
            else goal.category
        )
        goals[index] = goal.model_copy(
            update={
                "canonical_type": product.product_kind.value,
                "category": category,
                "type_locked": True,
                "category_locked": category != ProductCategory.OTHER,
            }
        )
        changed = True
    if not changed:
        return state
    return state.model_copy(
        update={
            "product_goals": tuple(goals),
            "constraints": tuple(constraints),
        }
    )


def _selection_action(kind: NextActionKind) -> SelectionRequestAction:
    if kind == NextActionKind.RECOMMEND_ONE:
        return SelectionRequestAction.RECOMMEND
    if kind in {
        NextActionKind.SEARCH_EXACT,
        NextActionKind.SHOW_PRELIMINARY_OPTIONS,
        NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS,
    }:
        return SelectionRequestAction.SHOW
    return SelectionRequestAction.CONTINUE_SELECTION


def _selection_task(
    outcome: DialogueV2Outcome,
) -> tuple[Any, Any, Any, CatalogSearchPlan | None] | None:
    plan = outcome.next_action_plan
    if plan is None or plan.primary.kind not in _SELECTION_ACTIONS:
        return None
    state = outcome.state_after
    task_id = plan.primary.task_id
    tasks = {item.task_id: item for item in state.tasks}
    task = tasks.get(task_id) if task_id is not None else None
    if task is None or task.act not in {TaskAct.FIND, TaskAct.SELECT}:
        return None
    planning = outcome.catalog_planning
    if planning is None:
        return None
    resolution = next(
        (item for item in planning.contract_resolutions if item.task_id == task.task_id),
        None,
    )
    readiness = next(
        (item for item in planning.readiness_assessments if item.task_id == task.task_id),
        None,
    )
    search = next(
        (item for item in planning.search_plans if item.task_id == task.task_id),
        None,
    )
    if resolution is None or readiness is None:
        return None
    return task, resolution, readiness, search


def _fact_inputs(
    state: DialogueStateV2,
    *,
    task_id: str,
    goal_id: str | None,
) -> tuple[SelectionFactInput, ...]:
    result: list[SelectionFactInput] = []
    for fact in state.constraints:
        if not fact.active:
            continue
        if goal_id is not None and fact.goal_id != goal_id:
            continue
        if goal_id is None and fact.task_id not in {None, task_id}:
            continue
        result.append(
            SelectionFactInput(
                name=fact.name,
                value=fact.value,
                unit=fact.unit,
                status=fact.status.value,
                polarity=fact.polarity.value,
                strength=fact.strength.value,
                evidence=fact.evidence,
                source=fact.source,
                source_turn=fact.source_turn,
            )
        )
    return tuple(result)


def build_selection_request(
    outcome: DialogueV2Outcome,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    original_utterance: str,
    previously_delivered_products: Iterable[Any] = (),
    current_product_focus: str | None = None,
) -> SelectionRequest | None:
    """Project accepted V2 state into one auditable catalogue request."""

    if source_snapshot is None:
        return None
    selected = _selection_task(outcome)
    if selected is None:
        return None
    task, resolution, readiness, search = selected
    contract = ProductContractRegistry().get(resolution.contract_id)
    if contract is None or resolution.product_kind == ProductKind.UNSUPPORTED:
        return None
    facts = _fact_inputs(
        outcome.state_after,
        task_id=task.task_id,
        goal_id=task.target_goal_id,
    )
    unknown = tuple(
        dict.fromkeys(
            item.name
            for item in facts
            if item.status in {"unknown", "refused", "deferred"}
        )
    )
    delivered = tuple(
        PresentedSelectionProduct(
            sku=str(item.sku),
            name=str(item.name),
            ordinal=index,
        )
        for index, item in enumerate(previously_delivered_products, start=1)
    )
    return SelectionRequest(
        original_utterance=original_utterance,
        action=_selection_action(outcome.next_action_plan.primary.kind),
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        category=contract.category,
        product_kind=resolution.product_kind,
        contract_id=resolution.contract_id,
        known_facts=facts,
        hard_constraints=search.hard_constraints if search is not None else (),
        soft_constraints=search.soft_constraints if search is not None else (),
        explicitly_unknown_facts=unknown,
        current_product_focus=current_product_focus,
        previously_delivered_products=delivered,
        catalog_revision=source_snapshot.source_revision,
    )


def _applied_filter(constraint: SearchConstraint) -> SelectionConstraintDisposition:
    return SelectionConstraintDisposition(
        disposition="applied",
        fact_name=constraint.name,
        requested_value=constraint.value,
        reason_codes=(
            "typed_hard_filter_applied"
            if constraint.strength == FactStrength.HARD
            else "typed_soft_filter_applied"
        ,),
    )


def build_selection_result(
    request: SelectionRequest,
    outcome: DialogueV2Outcome,
    source_snapshot: AnswerSourceSnapshot,
    response_products: Iterable[Any],
) -> SelectionResult:
    """Validate cards/no-match/clarification without reading rendered prose."""

    selected = _selection_task(outcome)
    if selected is None:
        raise ValueError("selection request no longer matches dialogue outcome")
    _task, _resolution, readiness, search = selected
    public_products = tuple(response_products)
    cards: list[SelectionProductCard] = []
    card_gate_failed = False
    for public in public_products:
        source = source_snapshot.product(str(public.sku))
        if source is None or source.product_kind != request.product_kind:
            card_gate_failed = True
            continue
        if any(
            (
                source.name != public.name,
                source.price != public.price,
                source.currency != public.currency,
                source.stock_status != public.stock_status,
                source.url != public.url,
                source.image_url != public.image_url,
            )
        ):
            card_gate_failed = True
            continue
        if source.price is None or source.currency is None or source.stock_status is None or source.url is None:
            card_gate_failed = True
            continue
        cards.append(
            SelectionProductCard(
                sku=source.sku,
                name=source.name,
                price=source.price,
                currency=source.currency,
                stock_status=source.stock_status,
                stock_qty=source.stock_qty,
                url=source.url,
                image_url=source.image_url,
            )
        )

    candidate_assessments = search.candidate_assessments if search is not None else ()
    allowed_skus = (
        tuple(
            dict.fromkeys(
                (
                    *search.eligible_skus,
                    *search.relaxed_skus,
                    *search.unverified_skus,
                )
            )
        )
        if search is not None
        else ()
    )
    dispositions: list[SelectionConstraintDisposition] = []
    exclusions: dict[str, tuple[str, ...]] = {}
    for candidate in candidate_assessments:
        if candidate.status == CandidateStatus.REJECTED:
            reasons = tuple(
                dict.fromkeys(
                    (
                        *candidate.reason_codes,
                        *(f"mismatched:{item}" for item in candidate.mismatched_hard_facts),
                        *(f"missing:{item}" for item in candidate.missing_hard_facts),
                    )
                )
            )
            exclusions[candidate.sku] = reasons or ("candidate_rejected",)
            for fact_name in (
                *candidate.mismatched_hard_facts,
                *candidate.missing_hard_facts,
            ):
                dispositions.append(
                    SelectionConstraintDisposition(
                        disposition="rejected",
                        fact_name=fact_name,
                        candidate_sku=candidate.sku,
                        reason_codes=reasons or ("candidate_rejected",),
                    )
                )
        for relaxation in candidate.relaxations:
            dispositions.append(
                SelectionConstraintDisposition(
                    disposition="relaxed",
                    fact_name=relaxation.fact_name,
                    requested_value=relaxation.requested_value,
                    candidate_sku=candidate.sku,
                    reason_codes=(relaxation.reason_code,),
                )
            )
        if candidate.status == CandidateStatus.UNVERIFIED:
            for fact_name in candidate.reason_codes or ("unverified",):
                dispositions.append(
                    SelectionConstraintDisposition(
                        disposition="unverified",
                        fact_name=fact_name,
                        candidate_sku=candidate.sku,
                        reason_codes=candidate.reason_codes,
                    )
                )

    missing_critical = (
        readiness.recommended_question_fact
        if readiness.status == ReadinessStatus.NEEDS_DECISION_FACT
        else None
    )
    honest_no_match = bool(
        search is not None
        and not allowed_skus
        and (
            CatalogSearchStage.HONEST_NO_MATCH in search.stages
            or (
                candidate_assessments
                and all(item.status == CandidateStatus.REJECTED for item in candidate_assessments)
            )
        )
    )
    if card_gate_failed:
        status = SelectionResultStatus.REJECTED
        reason = "selection_card_source_gate_failed"
    elif cards:
        status = SelectionResultStatus.SHOWN
        reason = "verified_cards_delivered"
    elif missing_critical is not None:
        status = SelectionResultStatus.NEED_CLARIFICATION
        reason = "one_critical_fact_required"
    elif honest_no_match:
        status = SelectionResultStatus.NO_MATCH
        reason = "verified_catalog_no_match"
    else:
        status = SelectionResultStatus.REJECTED
        reason = "selection_outcome_missing_cards_or_subject_reason"

    ordered_skus = tuple(item.sku for item in cards)
    selection_id = hashlib.sha256(
        "\x1f".join(
            (
                request.task_id,
                request.goal_id or "",
                request.catalog_revision,
                status.value,
                missing_critical or "",
                *ordered_skus,
            )
        ).encode("utf-8")
    ).hexdigest()[:32]
    gate_passed = status in {
        SelectionResultStatus.SHOWN,
        SelectionResultStatus.NEED_CLARIFICATION,
        SelectionResultStatus.NO_MATCH,
    }
    return SelectionResult(
        status=status,
        selection_id=selection_id,
        task_id=request.task_id,
        goal_id=request.goal_id,
        contract_id=request.contract_id,
        category=request.category,
        product_kind=request.product_kind,
        applied_facts=request.known_facts,
        hard_constraints=request.hard_constraints,
        soft_constraints=request.soft_constraints,
        applied_filters=tuple(
            _applied_filter(item)
            for item in (*request.hard_constraints, *request.soft_constraints)
        ),
        constraint_dispositions=tuple(dispositions),
        missing_critical_fact=missing_critical,
        candidates_before_filters=len(candidate_assessments),
        candidates_after_filters=len(allowed_skus),
        ordered_skus=ordered_skus,
        cards=tuple(cards),
        excluded_candidate_reason_codes=exclusions,
        catalog_revision=request.catalog_revision,
        outcome_gate_passed=gate_passed,
        customer_visible_state_updated=False,
        reason_code=reason,
    )
