"""Typed outcome gate for the native V2 single-category catalogue path."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from app.answer_v2.contracts import AnswerSourceSnapshot
from app.sku_resolution import (
    SkuResolutionStatus,
    extract_explicit_sku_tokens,
    resolve_catalog_sku,
)
from app.v2_presentation import format_public_fact_value, public_fact_label
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
    SelectionPresentationGroup,
    SelectionRequest,
    SelectionRequestAction,
    SelectionResult,
    SelectionResultStatus,
    PresentedSelectionProduct,
    SelectionSourceConflict,
)


def _presentation_label(fact_name: str) -> str:
    return public_fact_label(fact_name)


def _presentation_value(value: object, unit: str | None) -> str:
    return format_public_fact_value(value, unit=unit)


def _exact_source_fact(source, fact_name: str):
    if any(item.name == fact_name for item in source.fact_issues):
        return None
    facts = tuple(item for item in source.facts if item.name == fact_name)
    distinct = {(str(item.value), item.unit) for item in facts}
    return facts[0] if len(distinct) == 1 and facts else None


def _preliminary_groups(
    cards: tuple[SelectionProductCard, ...],
    source_snapshot: AnswerSourceSnapshot,
    fact_names: tuple[str, ...],
) -> tuple[SelectionPresentationGroup, ...]:
    """Group only on an unknown fact with an exact value for every card.

    Missing or ambiguous values are intentionally not rendered as a group: a
    visual heading must never make an unproved property look like a fact.
    """

    groups: list[SelectionPresentationGroup] = []
    for fact_name in fact_names:
        by_value: dict[tuple[str, str | None], list[str]] = {}
        complete = True
        for card in cards:
            source = source_snapshot.product(card.sku)
            fact = _exact_source_fact(source, fact_name) if source is not None else None
            if fact is None:
                complete = False
                break
            key = (str(fact.value), fact.unit)
            by_value.setdefault(key, []).append(card.sku)
        if not complete or len(by_value) < 2:
            continue
        for value, skus in by_value.items():
            groups.append(
                SelectionPresentationGroup(
                    fact_name=fact_name,
                    label=_presentation_label(fact_name),
                    value=_presentation_value(*value),
                    card_skus=tuple(skus),
                )
            )
        # One fact is enough for a readable grouping.  Multiple independent
        # headings would duplicate the same cards and obscure the choice.
        break
    return tuple(groups)
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
    """Bind an explicit SKU or exact full catalogue name, never fuzzily.

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
        # SKU resolution has priority over a stale or overly broad semantic
        # product kind.  A numeric article is still only an identity candidate
        # until the shared resolver proves an exact/unique catalogue match.
        sku_matches = []
        for token in extract_explicit_sku_tokens(goal.evidence):
            resolution = resolve_catalog_sku(token, catalog_snapshot)
            if resolution.status in {
                SkuResolutionStatus.EXACT,
                SkuResolutionStatus.UNIQUE_PREFIX,
            }:
                sku_matches.extend(resolution.candidates)
        unique_sku_matches = tuple(
            {item.sku: item for item in sku_matches}.values()
        )
        matches = (
            unique_sku_matches
            if len(unique_sku_matches) == 1
            else tuple(
                item
                for item in catalog_snapshot
                if normalize_identity(item.name) in exact_name_inputs
            )
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
                    source=(
                        "catalog_exact_sku_resolution"
                        if unique_sku_matches
                        else "catalog_exact_name_resolution"
                    ),
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
    if plan is None:
        return None
    state = outcome.state_after
    tasks = {item.task_id: item for item in state.tasks}
    task = None
    if plan.primary.kind in _SELECTION_ACTIONS:
        task_id = plan.primary.task_id
        selected = tasks.get(task_id) if task_id is not None else None
        if selected is not None and selected.act in {TaskAct.FIND, TaskAct.SELECT}:
            task = selected
    if task is None:
        # A compound turn may answer an information question and show the
        # preliminary cards of one sibling Selection task in the same checked
        # AnswerPlan.  The primary action is then EXPLAIN_TERM_OR_METHOD, but
        # the delivered cards still require the ordinary SelectionResult and
        # outcome gate before they may become customer-visible scope.
        answer_plan = (
            outcome.answer_planning.answer_plan
            if outcome.answer_planning is not None
            else None
        )
        product_task_ids = tuple(
            dict.fromkeys(
                item.task_id
                for item in (answer_plan.products if answer_plan is not None else ())
            )
        )
        selection_tasks = tuple(
            tasks[task_id]
            for task_id in product_task_ids
            if task_id in tasks
            and tasks[task_id].act in {TaskAct.FIND, TaskAct.SELECT}
        )
        # A single-category SelectionResult must never collapse cards from two
        # independent selection tasks or a future multi-category project.
        if len(selection_tasks) == 1:
            task = selection_tasks[0]
    if task is None:
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
    primary_kind = outcome.next_action_plan.primary.kind
    action = (
        _selection_action(primary_kind)
        if primary_kind in _SELECTION_ACTIONS
        else SelectionRequestAction.SHOW
    )
    return SelectionRequest(
        original_utterance=original_utterance,
        action=action,
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


def _source_backed_power_area_conflicts(
    request: SelectionRequest,
    cards: tuple[SelectionProductCard, ...],
    source_snapshot: AnswerSourceSnapshot,
) -> tuple[SelectionSourceConflict, ...]:
    """Expose an explicit power/coverage contradiction without calculating.

    ``area_m2`` is deliberately omitted from candidate filtering when the
    customer has stated a design power: it must not be treated as a power
    formula.  A shown exact-power card can nevertheless have a manufacturer
    declared heated area below the customer's stated building area.  That is
    enough to prevent an exact recommendation, but not enough to invent a
    replacement power or silently discard the expressly requested model.
    """

    known = {
        fact.name: fact
        for fact in request.known_facts
        if fact.status == "known" and fact.value is not None
    }
    customer_area = known.get("area_m2")
    customer_power = known.get("power_kw")
    if customer_area is None or customer_power is None:
        return ()
    if not isinstance(customer_area.value, (int, float)) or isinstance(
        customer_area.value,
        bool,
    ):
        return ()

    conflicts: list[SelectionSourceConflict] = []
    for card in cards:
        source = source_snapshot.product(card.sku)
        if source is None or any(
            issue.name == "declared_heated_area_m2"
            for issue in source.fact_issues
        ):
            continue
        values = tuple(
            fact
            for fact in source.facts
            if fact.name == "declared_heated_area_m2"
        )
        if len(values) != 1:
            continue
        coverage = values[0]
        if not isinstance(coverage.value, (int, float)) or isinstance(
            coverage.value,
            bool,
        ):
            continue
        if float(coverage.value) >= float(customer_area.value):
            continue
        conflicts.append(
            SelectionSourceConflict(
                customer_fact_name="area_m2",
                customer_value=customer_area.value,
                customer_unit=customer_area.unit,
                card_sku=card.sku,
                card_fact_name="declared_heated_area_m2",
                card_value=coverage.value,
                card_unit=coverage.unit,
                source_field=coverage.provenance.source_field,
                reason_code="declared_coverage_below_customer_area_with_explicit_power",
            )
        )
    return tuple(conflicts)


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
    contract = ProductContractRegistry().get(request.contract_id)
    allowed_product_kinds = (
        set(contract.candidate_kinds or (contract.product_kind,))
        if contract is not None
        else {request.product_kind}
    )
    cards: list[SelectionProductCard] = []
    card_gate_failed = False
    for public in public_products:
        source = source_snapshot.product(str(public.sku))
        # A generic request can validly resolve to a contract-declared subtype
        # (for example boiler → gas/electric boiler).  Do not weaken this
        # source gate beyond that explicit contract set: the SKU, source
        # revision and every public card field still must match exactly.
        if source is None or source.product_kind not in allowed_product_kinds:
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
    candidate_by_sku = {item.sku: item for item in candidate_assessments}
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

    availability_analog = bool(
        cards
        and search is not None
        and search.availability_analog_exact_out_of_stock_skus
        and all(
            candidate_by_sku.get(card.sku) is not None
            and candidate_by_sku[card.sku].availability_analog
            and candidate_by_sku[card.sku].availability_status.value == "in_stock"
            for card in cards
        )
    )
    if (
        cards
        and search is not None
        and search.availability_analog_exact_out_of_stock_skus
        and not availability_analog
    ):
        # The answer planner must not combine a hidden unavailable exact card
        # with its availability analogue.  A selection scope is a customer
        # contract, so this stale/mixed response fails closed.
        card_gate_failed = True

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
        reason = (
            "availability_analog_after_confirmed_out_of_stock_exact_match"
            if availability_analog
            else "verified_cards_delivered"
        )
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
    source_backed_conflicts = _source_backed_power_area_conflicts(
        request,
        tuple(cards),
        source_snapshot,
    )
    if status == SelectionResultStatus.SHOWN and source_backed_conflicts:
        reason = "source_backed_power_area_conflict_preliminary"
    is_preliminary = bool(
        cards
        and (
            readiness.status == ReadinessStatus.PRELIMINARY_READY
            or source_backed_conflicts
            or availability_analog
        )
    )
    availability_analog_differences = tuple(
        relaxation
        for card in cards
        if (candidate := candidate_by_sku.get(card.sku)) is not None
        and candidate.availability_analog
        for relaxation in candidate.relaxations
    )
    preliminary_fact_names = tuple(
        dict.fromkeys(
            (
                *(
                    item.name
                    for item in request.known_facts
                    if item.status in {"unknown", "refused", "deferred"}
                ),
                *readiness.unknown_facts,
                *readiness.refused_facts,
                *readiness.deferred_facts,
                # A card can be deliberately preliminary because the buyer
                # asked to see the confirmed part of the shortlist before
                # supplying a still-askable installation fact (for example
                # pump DN or mounting length).  Keep that missing fact visible
                # instead of presenting the cards as merely vaguely limited.
                *readiness.missing_decision_facts,
            )
        )
    )
    presentation_groups = (
        _preliminary_groups(
            tuple(cards),
            source_snapshot,
            preliminary_fact_names,
        )
        if is_preliminary and not source_backed_conflicts
        else ()
    )
    selection_id = hashlib.sha256(
        "\x1f".join(
            (
                request.task_id,
                request.goal_id or "",
                request.catalog_revision,
                status.value,
                missing_critical or "",
                reason,
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
        is_preliminary=is_preliminary,
        preliminary_fact_names=preliminary_fact_names,
        presentation_groups=presentation_groups,
        availability_analog=availability_analog,
        availability_analog_differences=availability_analog_differences,
        source_backed_conflicts=source_backed_conflicts,
        excluded_candidate_reason_codes=exclusions,
        catalog_revision=request.catalog_revision,
        outcome_gate_passed=gate_passed,
        customer_visible_state_updated=False,
        reason_code=reason,
    )
