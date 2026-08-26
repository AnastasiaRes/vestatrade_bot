"""Pure compiler from typed V2 decisions to a grounded AnswerPlan."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable

from app.catalog_v2.contracts import (
    CandidateAssessment,
    CandidateStatus,
    CatalogFact,
    CatalogPlanningResult,
    CatalogSearchPlan,
    ProductKind,
    ProductContract,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.catalog_v2.readiness import canonical_fact_name
from app.catalog_v2.registry import ProductContractRegistry
from app.commerce_v2.contracts import (
    CapabilityMode,
    CommerceExecutionStatus,
    CommercePlanningResult,
    CommerceWorkflowStatus,
)
from app.commerce_v2.registry import canonical_commerce_field_name
from app.dialogue_v2.contracts import (
    ConstraintStatus,
    DialogueStateV2,
    InformationOutputRelation,
    InformationPurpose,
    InformationSubjectScope,
    NextAction,
    NextActionKind,
    NextActionPlan,
    RequestedInformationOutput,
    ResponseStrategyKind,
    ShadowDeliveryStatus,
    TaskAct,
)

from .contracts import (
    AnalogDifference,
    AnswerClaim,
    AnswerPlan,
    AnswerPlanningResult,
    AnswerPlanStatus,
    AnswerSection,
    AnswerSectionKind,
    AnswerSourceSnapshot,
    CandidateFactReport,
    CandidateFactReportItem,
    CandidateFactStatus,
    ClaimKind,
    KnowledgeStatus,
    LimitationPlan,
    LimitationStatus,
    NextStepKind,
    NextStepPlan,
    ProductRecommendationRole,
    ProductPresentationPlan,
    ProductPresentationStatus,
    QuestionPlan,
    RecommendationCriterion,
    RejectedClaim,
    SourceReference,
    SourceType,
)


MAX_PRESENTABLE_CANDIDATES = 5

_EXPECTED_UNIT_BY_FAMILY = {
    "angle_deg": "°",
    "flow": "л/ч",
    "head_m": "м",
    "length_m": "м",
    "length_mm": "мм",
    "micron": "мкм",
    "percent": "%",
    "power_kw": "кВт",
    "power_w": "Вт",
    "pressure_bar": "бар",
    "temperature_c": "°C",
}

# These are confirmed request coordinates, not catalogue compatibility facts.
# Keeping them visible lets the seller acknowledge e.g. "800 m to Kazakhstan"
# without treating the feed balance as metres or promising delivery.
_DISPLAY_ONLY_COMMERCE_FACTS = frozenset(
    {
        "quantity",
        "destination_region",
        "delivery_whole_bundles",
        "delivery_no_repack",
    }
)
_DISPLAY_ONLY_COMMERCE_ACTS = frozenset(
    {
        TaskAct.FIND,
        TaskAct.SELECT,
        TaskAct.CHECK_STOCK,
        TaskAct.CHECK_DELIVERY,
        TaskAct.REQUEST_QUOTE,
    }
)

# Public business verification channels are intentionally scoped to typed
# commerce/service tasks.  Keeping them out of ordinary selection turns avoids
# appending a generic website advertisement to every technical answer.
_CAPABILITY_FACT_ACTS: dict[str, frozenset[TaskAct]] = {
    "site_url": frozenset(
        {
            TaskAct.REQUEST_QUOTE,
            TaskAct.REQUEST_INVOICE,
            TaskAct.RESERVE_PRODUCT,
            TaskAct.PLACE_ORDER,
            TaskAct.MODIFY_ORDER,
            TaskAct.CANCEL_ORDER,
            TaskAct.ORDER_STATUS,
            TaskAct.CHECK_DELIVERY,
            TaskAct.RETURN_PRODUCT,
            TaskAct.WARRANTY,
            TaskAct.COMPLAINT,
            TaskAct.CONTACT_STORE,
            TaskAct.HANDOFF,
        }
    ),
}


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _readiness_fact_names(
    readiness: TaskReadinessAssessment,
) -> frozenset[str]:
    """Facts a readiness record can legitimately explain or request."""

    return frozenset(
        item
        for item in (
            readiness.recommended_question_fact,
            *readiness.missing_decision_facts,
            *readiness.unknown_facts,
            *readiness.refused_facts,
            *readiness.deferred_facts,
        )
        if item
    )


def _contract_fact_guidance(
    contract: ProductContract | None,
    fact_name: str | None,
) -> tuple[str | None, str | None]:
    """Return declarative learn method and display unit for a typed fact."""

    if contract is None or not fact_name:
        return None, None
    canonical = canonical_fact_name(contract, fact_name)
    if canonical is None:
        return None, None
    definition = next(
        (item for item in contract.fact_definitions if item.name == canonical),
        None,
    )
    if definition is None:
        return None, None
    return (
        definition.learn_method_code,
        _EXPECTED_UNIT_BY_FAMILY.get(str(definition.unit_family or "")),
    )


def _source(
    source_type: SourceType,
    source_id: str,
    *,
    field_name: str | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    source_turn: int | None = None,
) -> SourceReference:
    return SourceReference(
        source_ref_id=_stable_id("source", source_type.value, source_id, field_name),
        source_type=source_type,
        source_id=source_id,
        field_name=field_name,
        task_id=task_id,
        goal_id=goal_id,
        source_turn=source_turn,
    )


def _claim(
    kind: ClaimKind,
    subject_ref: str,
    predicate: str,
    value: str | int | float | bool,
    source_refs: Iterable[SourceReference],
    *,
    unit: str | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    reason_codes: tuple[str, ...] = (),
) -> AnswerClaim:
    refs = tuple(source_refs)
    return AnswerClaim(
        claim_id=_stable_id("claim", kind.value, subject_ref, predicate, value, unit),
        kind=kind,
        subject_ref=subject_ref,
        predicate=predicate,
        value=value,
        unit=unit,
        knowledge_status=KnowledgeStatus.CONFIRMED,
        source_ref_ids=tuple(item.source_ref_id for item in refs),
        allowed_in_response=True,
        task_id=task_id,
        goal_id=goal_id,
        reason_codes=reason_codes,
    )


def _next_step(action: NextAction) -> NextStepKind:
    mapping = {
        NextActionKind.ANSWER_DIRECT_QUESTION: NextStepKind.PROVIDE_DIRECT_ANSWER,
        NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION: NextStepKind.PROVIDE_DIRECT_ANSWER,
        NextActionKind.ASK_DECISION_CHANGING_QUESTION: NextStepKind.ASK_DECISION_FACT,
        NextActionKind.COLLECT_COMMERCE_FACT: NextStepKind.ASK_DECISION_FACT,
        NextActionKind.EXPLAIN_HOW_TO_FIND_FACT: NextStepKind.EXPLAIN_HOW_TO_FIND_FACT,
        NextActionKind.EXPLAIN_TERM_OR_METHOD: NextStepKind.EXPLAIN_HOW_TO_FIND_FACT,
        NextActionKind.SEARCH_EXACT: NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS,
        NextActionKind.RECOMMEND_ONE: NextStepKind.RECOMMEND_ONE,
        NextActionKind.SHOW_PRELIMINARY_OPTIONS: NextStepKind.SHOW_PRELIMINARY_OPTIONS,
        NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS: NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS,
        NextActionKind.COMPARE: NextStepKind.COMPARE_CANDIDATES,
        NextActionKind.PRESENT_CONTROLLED_ANALOG: NextStepKind.PRESENT_ANALOG_DIFFERENCES,
        NextActionKind.OFFER_VERIFIABLE_EXTERNAL_STEP: NextStepKind.OFFER_VERIFIABLE_EXTERNAL_STEP,
        NextActionKind.START_OR_CONTINUE_HANDOFF: NextStepKind.OFFER_VERIFIABLE_EXTERNAL_STEP,
        NextActionKind.PREVIEW_COMMERCE_REQUEST: NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS,
        NextActionKind.REQUEST_SCOPED_CONSENT: NextStepKind.WAIT_FOR_CUSTOMER,
        NextActionKind.PREPARE_COMMERCE_COMMAND: NextStepKind.STATE_CAPABILITY_BOUNDARY,
        NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS: NextStepKind.PROVIDE_DIRECT_ANSWER,
        NextActionKind.STATE_COMMERCE_CAPABILITY_BOUNDARY: NextStepKind.STATE_CAPABILITY_BOUNDARY,
        NextActionKind.STATE_CAPABILITY_BOUNDARY: NextStepKind.STATE_CAPABILITY_BOUNDARY,
        NextActionKind.CLOSE_TASK: NextStepKind.CLOSE_TASK,
        NextActionKind.ACKNOWLEDGE_COMMERCE_OPT_OUT: NextStepKind.CLOSE_TASK,
        NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING: NextStepKind.WAIT_FOR_CUSTOMER,
    }
    return mapping.get(action.kind, NextStepKind.WAIT_FOR_CUSTOMER)


def _limitation_status(status: ConstraintStatus) -> LimitationStatus:
    return {
        ConstraintStatus.UNKNOWN: LimitationStatus.UNKNOWN,
        ConstraintStatus.REFUSED: LimitationStatus.REFUSED,
        ConstraintStatus.DEFERRED: LimitationStatus.DEFERRED,
    }.get(status, LimitationStatus.UNVERIFIED)


def _presentation_status(candidate, readiness) -> ProductPresentationStatus:
    if candidate.status == CandidateStatus.UNVERIFIED or candidate.missing_hard_facts:
        return ProductPresentationStatus.UNVERIFIED
    if candidate.relaxations:
        return ProductPresentationStatus.ANALOG
    if readiness is not None and readiness.status == ReadinessStatus.PRELIMINARY_READY:
        return ProductPresentationStatus.PRELIMINARY
    return ProductPresentationStatus.EXACT


def _strategy_options(status: LimitationStatus) -> tuple[ResponseStrategyKind, ...]:
    if status in {
        LimitationStatus.UNKNOWN,
        LimitationStatus.REFUSED,
        LimitationStatus.DEFERRED,
    }:
        return (
            ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
            ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS,
            ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS,
            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
        )
    return (
        ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS,
        ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
    )


def _merge_duplicate_limitations(
    limitations: Iterable[LimitationPlan],
) -> list[LimitationPlan]:
    """Compact repeated evidence without losing its typed provenance."""

    merged: dict[
        tuple[object, str, str | None, str | None, str | None],
        LimitationPlan,
    ] = {}
    for limitation in limitations:
        key = (
            limitation.status,
            limitation.reason_code,
            limitation.task_id,
            limitation.goal_id,
            limitation.fact_name,
        )
        previous = merged.get(key)
        if previous is None:
            merged[key] = limitation
            continue
        merged[key] = previous.model_copy(
            update={
                "source_ref_ids": tuple(
                    dict.fromkeys((*previous.source_ref_ids, *limitation.source_ref_ids))
                ),
                "allowed_strategy_kinds": tuple(
                    dict.fromkeys(
                        (
                            *previous.allowed_strategy_kinds,
                            *limitation.allowed_strategy_kinds,
                        )
                    )
                ),
            }
        )
    return list(merged.values())


def _candidate_is_presentable(
    search_plan: CatalogSearchPlan,
    candidate: CandidateAssessment,
    source_snapshot: AnswerSourceSnapshot,
) -> bool:
    allowed_candidate_skus = {
        *search_plan.eligible_skus,
        *search_plan.relaxed_skus,
        *search_plan.unverified_skus,
    }
    hard_constraint_names = {item.name for item in search_plan.hard_constraints}
    product = source_snapshot.product(candidate.sku)
    return bool(
        candidate.status != CandidateStatus.REJECTED
        and candidate.sku in allowed_candidate_skus
        and candidate.product_kind == search_plan.product_kind
        and candidate.role == search_plan.requested_role
        and not candidate.mismatched_hard_facts
        and not any(
            item.fact_name in hard_constraint_names
            for item in candidate.relaxations
        )
        and product is not None
        and product.product_kind == candidate.product_kind
        and product.role == candidate.role
    )


def _candidate_tier(
    search_plan: CatalogSearchPlan,
    candidate: CandidateAssessment,
) -> int:
    """Order typed catalogue evidence without adding marketing ranking."""

    if candidate.status == CandidateStatus.ELIGIBLE and not candidate.relaxations:
        return 0
    known_hard_facts = {item.name for item in search_plan.hard_constraints}
    matched_hard_facts = set(candidate.matched_hard_facts)
    unavailable_customer_fact_only = bool(
        candidate.status == CandidateStatus.UNVERIFIED
        and set(candidate.reason_codes) == {"required_customer_fact_unavailable"}
        and not candidate.missing_hard_facts
        and not candidate.mismatched_hard_facts
        and known_hard_facts.issubset(matched_hard_facts)
    )
    if unavailable_customer_fact_only:
        return 1
    if candidate.status == CandidateStatus.ELIGIBLE:
        return 2
    return 3


def _has_positive_stock(source_snapshot: AnswerSourceSnapshot, sku: str) -> bool:
    product = source_snapshot.product(sku)
    return bool(
        product is not None
        and product.stock_qty is not None
        and product.stock_qty > 0
    )


def _candidate_is_exact_recommendable(
    search_plan: CatalogSearchPlan,
    candidate: CandidateAssessment,
    source_snapshot: AnswerSourceSnapshot,
) -> bool:
    """Allow recommendations only from fully verified exact candidates."""

    required_hard = {item.name for item in search_plan.hard_constraints}
    return bool(
        _candidate_is_presentable(search_plan, candidate, source_snapshot)
        and candidate.status == CandidateStatus.ELIGIBLE
        and not candidate.missing_hard_facts
        and not candidate.mismatched_hard_facts
        and not candidate.relaxations
        and required_hard.issubset(candidate.matched_hard_facts)
    )


def _recommendation_order(
    options: Iterable[tuple[CatalogSearchPlan, CandidateAssessment]],
    source_snapshot: AnswerSourceSnapshot,
) -> tuple[
    tuple[tuple[CatalogSearchPlan, CandidateAssessment], ...],
    RecommendationCriterion,
    tuple[str, ...],
]:
    """Order equally exact candidates using only verified catalogue facts."""

    unique: dict[str, tuple[CatalogSearchPlan, CandidateAssessment]] = {}
    for search_plan, candidate in options:
        unique.setdefault(candidate.sku, (search_plan, candidate))
    exact = tuple(unique.values())
    if len(exact) <= 1:
        return (
            exact,
            RecommendationCriterion.ONLY_EXACT_ELIGIBLE,
            ("only_exact_eligible_candidate",),
        )

    priced: list[
        tuple[CatalogSearchPlan, CandidateAssessment, float, str]
    ] = []
    unpriced: list[tuple[CatalogSearchPlan, CandidateAssessment]] = []
    for search_plan, candidate in exact:
        product = source_snapshot.product(candidate.sku)
        if (
            product is not None
            and product.price is not None
            and not isinstance(product.price, bool)
            and math.isfinite(float(product.price))
            and float(product.price) >= 0
            and product.currency
        ):
            priced.append(
                (
                    search_plan,
                    candidate,
                    float(product.price),
                    product.currency,
                )
            )
        else:
            unpriced.append((search_plan, candidate))

    currencies = {item[3] for item in priced}
    if priced and len(currencies) == 1:
        ordered_priced = sorted(
            priced,
            key=lambda item: (
                item[2],
                item[1].sku.casefold(),
                item[1].sku,
                item[0].plan_id,
            ),
        )
        ordered_unpriced = sorted(
            unpriced,
            key=lambda item: (
                item[1].sku.casefold(),
                item[1].sku,
                item[0].plan_id,
            ),
        )
        lowest_price = ordered_priced[0][2]
        tied_lowest = sum(
            math.isclose(
                item[2],
                lowest_price,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            for item in ordered_priced
        )
        reasons = ["lowest_confirmed_price_among_priced_exact_candidates"]
        if tied_lowest > 1:
            reasons.append("stable_sku_tiebreak_among_equal_lowest_prices")
        return (
            tuple(
                (search_plan, candidate)
                for search_plan, candidate, _price, _currency in ordered_priced
            )
            + tuple(ordered_unpriced),
            RecommendationCriterion.LOWEST_CONFIRMED_PRICE,
            tuple(reasons),
        )

    return (
        tuple(
            sorted(
                exact,
                key=lambda item: (
                    item[1].sku.casefold(),
                    item[1].sku,
                    item[0].plan_id,
                ),
            )
        ),
        RecommendationCriterion.STABLE_SKU_TIEBREAK,
        ("stable_sku_tiebreak_without_comparable_confirmed_price",),
    )


def _recommendation_metadata(
    search_plans: tuple[CatalogSearchPlan, ...],
    source_snapshot: AnswerSourceSnapshot,
    selected: frozenset[tuple[str, str]],
    recommendation_task_ids: frozenset[str],
) -> dict[
    tuple[str, str],
    tuple[
        ProductRecommendationRole,
        int,
        RecommendationCriterion,
        tuple[str, ...],
    ],
]:
    by_task: dict[str, list[tuple[CatalogSearchPlan, CandidateAssessment]]] = {}
    for search_plan in search_plans:
        if search_plan.task_id not in recommendation_task_ids:
            continue
        for candidate in search_plan.candidate_assessments:
            if _candidate_is_exact_recommendable(
                search_plan,
                candidate,
                source_snapshot,
            ):
                by_task.setdefault(search_plan.task_id, []).append(
                    (search_plan, candidate)
                )

    result: dict[
        tuple[str, str],
        tuple[
            ProductRecommendationRole,
            int,
            RecommendationCriterion,
            tuple[str, ...],
        ],
    ] = {}
    for options in by_task.values():
        ordered, criterion, primary_reasons = _recommendation_order(
            options,
            source_snapshot,
        )
        selected_ordered = tuple(
            item
            for item in ordered[:3]
            if (item[0].plan_id, item[1].sku) in selected
        )
        for index, (search_plan, candidate) in enumerate(
            selected_ordered,
            start=1,
        ):
            result[(search_plan.plan_id, candidate.sku)] = (
                (
                    ProductRecommendationRole.PRIMARY
                    if index == 1
                    else ProductRecommendationRole.ALTERNATIVE
                ),
                index,
                criterion,
                (
                    primary_reasons
                    if index == 1
                    else ("exact_eligible_recommendation_alternative",)
                ),
            )
    return result


def _numeric_relaxation_distance(candidate: CandidateAssessment) -> float:
    """Prefer the nearest normalized value when exactly one numeric soft fact changed."""

    if len(candidate.relaxations) != 1:
        return math.inf
    relaxation = candidate.relaxations[0]
    requested = relaxation.requested_value
    actual = relaxation.candidate_value
    if (
        isinstance(requested, bool)
        or isinstance(actual, bool)
        or not isinstance(requested, (int, float))
        or not isinstance(actual, (int, float))
    ):
        return math.inf
    requested_number = float(requested)
    actual_number = float(actual)
    if not math.isfinite(requested_number) or not math.isfinite(actual_number):
        return math.inf
    return abs(actual_number - requested_number) / max(abs(requested_number), 1.0)


def _catalog_fact_is_unambiguous_for_display(fact: CatalogFact) -> bool:
    """Conservatively reject a scalar normalized from a composite numeric raw value."""

    value = getattr(fact, "value", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    provenance = getattr(fact, "provenance", None)
    raw_value = str(getattr(provenance, "raw_value", "") or "")
    raw_numbers = {
        float(token.replace(",", "."))
        for token in re.findall(r"(?<!\d)\d+(?:[.,]\d+)?", raw_value)
    }
    return len(raw_numbers) == 1 and math.isclose(
        next(iter(raw_numbers)),
        float(value),
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def _presentable_candidate_shortlist(
    search_plans: tuple[CatalogSearchPlan, ...],
    source_snapshot: AnswerSourceSnapshot,
    *,
    task_order: tuple[str, ...] = (),
    recommendation_task_ids: frozenset[str] = frozenset(),
) -> tuple[frozenset[tuple[str, str]], dict[tuple[str, str], int], bool]:
    """Choose a globally bounded, task-fair shortlist.

    Candidate quality is evaluated inside each independent task.  The public
    five-card budget is then allocated round-robin in typed task order so one
    large result set cannot crowd every other requested product out.
    """

    by_task: dict[str, list[tuple[CatalogSearchPlan, CandidateAssessment]]] = {}
    for search_plan in search_plans:
        for candidate in search_plan.candidate_assessments:
            if _candidate_is_presentable(search_plan, candidate, source_snapshot):
                by_task.setdefault(search_plan.task_id, []).append(
                    (search_plan, candidate)
                )

    options_by_task: dict[
        str,
        list[tuple[CatalogSearchPlan, CandidateAssessment]],
    ] = {}
    for task_id, options in by_task.items():
        if task_id in recommendation_task_ids:
            exact_options = tuple(
                item
                for item in options
                if _candidate_is_exact_recommendable(
                    item[0],
                    item[1],
                    source_snapshot,
                )
            )
            ordered, _criterion, _reasons = _recommendation_order(
                exact_options,
                source_snapshot,
            )
        else:
            ordered = tuple(
                sorted(
                    options,
                    key=lambda item: (
                        0
                        if _has_positive_stock(source_snapshot, item[1].sku)
                        else 1,
                        _candidate_tier(item[0], item[1]),
                        _numeric_relaxation_distance(item[1]),
                        item[1].sku.casefold(),
                        item[1].sku,
                        item[0].plan_id,
                    ),
                )
            )
        seen_skus: set[str] = set()
        unique_options: list[tuple[CatalogSearchPlan, CandidateAssessment]] = []
        for search_plan, candidate in ordered:
            if candidate.sku in seen_skus:
                continue
            seen_skus.add(candidate.sku)
            unique_options.append((search_plan, candidate))
        options_by_task[task_id] = (
            unique_options[:3]
            if task_id in recommendation_task_ids
            else unique_options
        )

    ordered_task_ids = tuple(
        dict.fromkeys(
            (
                *(task_id for task_id in task_order if task_id in options_by_task),
                *(plan.task_id for plan in search_plans if plan.task_id in options_by_task),
            )
        )
    )
    selected: set[tuple[str, str]] = set()
    shortlist_order: dict[tuple[str, str], int] = {}
    task_offsets = {task_id: 0 for task_id in ordered_task_ids}
    while len(selected) < MAX_PRESENTABLE_CANDIDATES:
        added_this_round = False
        for task_id in ordered_task_ids:
            offset = task_offsets[task_id]
            options = options_by_task[task_id]
            if offset >= len(options):
                continue
            search_plan, candidate = options[offset]
            task_offsets[task_id] = offset + 1
            key = (search_plan.plan_id, candidate.sku)
            selected.add(key)
            shortlist_order[key] = offset
            added_this_round = True
            if len(selected) >= MAX_PRESENTABLE_CANDIDATES:
                break
        if not added_this_round:
            break
    shortlist_applied = sum(map(len, options_by_task.values())) > len(selected)
    return frozenset(selected), shortlist_order, shortlist_applied


def build_answer_plan(
    dialogue_state: DialogueStateV2,
    next_action_plan: NextActionPlan,
    catalog_planning: CatalogPlanningResult | None,
    commerce_planning: CommercePlanningResult | None,
    source_snapshot: AnswerSourceSnapshot,
    *,
    turn_id: str,
) -> AnswerPlanningResult:
    """Compile source-linked response content without reading reply text."""

    sources: dict[str, SourceReference] = {}
    claims: dict[str, AnswerClaim] = {}
    products: list[ProductPresentationPlan] = []
    differences: list[AnalogDifference] = []
    limitations: list[LimitationPlan] = []
    rejected: list[RejectedClaim] = []
    missing_sources: list[str] = []
    tasks = {item.task_id: item for item in dialogue_state.tasks}
    response_task_order = tuple(
        dict.fromkeys(
            (
                *next_action_plan.task_ids,
                *(
                    (next_action_plan.primary.task_id,)
                    if next_action_plan.primary.task_id is not None
                    else ()
                ),
                *(
                    (next_action_plan.secondary.task_id,)
                    if next_action_plan.secondary is not None
                    and next_action_plan.secondary.task_id is not None
                    else ()
                ),
            )
        )
    )
    response_task_ids = set(response_task_order)
    response_goal_ids = {
        task.target_goal_id
        for task_id in response_task_ids
        if (task := tasks.get(task_id)) is not None
        and task.target_goal_id is not None
    }
    readiness_by_task = {
        item.task_id: item
        for item in (catalog_planning.readiness_assessments if catalog_planning else ())
    }
    readiness_by_goal: dict[str, list[object]] = {}
    for readiness in readiness_by_task.values():
        if readiness.goal_id is not None:
            readiness_by_goal.setdefault(readiness.goal_id, []).append(readiness)
    contract_registry = ProductContractRegistry()
    typed_contract_by_task: dict[str, ProductContract] = {}
    typed_contracts_by_goal: dict[str, dict[str, ProductContract]] = {}
    for resolution in (
        catalog_planning.contract_resolutions if catalog_planning else ()
    ):
        contract = contract_registry.get(resolution.contract_id)
        if contract is None and resolution.product_kind != ProductKind.UNSUPPORTED:
            contract = contract_registry.for_kind(resolution.product_kind)
        if contract is None:
            continue
        typed_contract_by_task[resolution.task_id] = contract
        if resolution.goal_id is not None:
            typed_contracts_by_goal.setdefault(resolution.goal_id, {})[
                contract.contract_id
            ] = contract
    # Readiness/search plans already carry the canonical kind selected by the
    # catalogue layer.  Keep using that typed identity when an older fixture or
    # partially populated shadow result has no ContractResolution record; this
    # does not infer anything from the raw user text.
    for readiness in readiness_by_task.values():
        contract = contract_registry.get(readiness.contract_id)
        if contract is None and readiness.product_kind != ProductKind.UNSUPPORTED:
            contract = contract_registry.for_kind(readiness.product_kind)
        if contract is None:
            continue
        typed_contract_by_task.setdefault(readiness.task_id, contract)
        if readiness.goal_id is not None:
            typed_contracts_by_goal.setdefault(readiness.goal_id, {})[
                contract.contract_id
            ] = contract
    for search_plan in (
        catalog_planning.search_plans if catalog_planning else ()
    ):
        contract = contract_registry.get(search_plan.contract_id)
        if contract is None and search_plan.product_kind != ProductKind.UNSUPPORTED:
            contract = contract_registry.for_kind(search_plan.product_kind)
        if contract is None:
            continue
        typed_contract_by_task.setdefault(search_plan.task_id, contract)
        if search_plan.goal_id is not None:
            typed_contracts_by_goal.setdefault(search_plan.goal_id, {})[
                contract.contract_id
            ] = contract
    unavailable_catalog_facts_by_task: dict[str, set[str]] = {}
    for fact in dialogue_state.constraints:
        if not fact.active or fact.status not in {
            ConstraintStatus.UNKNOWN,
            ConstraintStatus.REFUSED,
            ConstraintStatus.DEFERRED,
        }:
            continue
        scoped_task_ids = {
            *(
                (fact.task_id,)
                if fact.task_id is not None and fact.task_id in response_task_ids
                else ()
            ),
            *(
                task_id
                for task_id in response_task_ids
                if fact.goal_id is not None
                and (task := tasks.get(task_id)) is not None
                and task.target_goal_id == fact.goal_id
            ),
        }
        for task_id in scoped_task_ids:
            contract = typed_contract_by_task.get(task_id)
            if contract is None:
                continue
            canonical_name = canonical_fact_name(contract, fact.name, fact.unit)
            if canonical_name is not None:
                unavailable_catalog_facts_by_task.setdefault(task_id, set()).add(
                    canonical_name
                )
    commerce_fact_names_by_task: dict[str, set[str]] = {}
    if commerce_planning is not None:
        for assessment in commerce_planning.readiness_assessments:
            fact_names = {
                *assessment.confirmed_fields,
                *assessment.missing_fields,
                *assessment.unknown_fields,
                *assessment.refused_fields,
                *assessment.deferred_fields,
                *assessment.blocking_facts,
            }
            for task_id in assessment.task_ids:
                commerce_fact_names_by_task.setdefault(task_id, set()).update(
                    fact_names
                )
    shortlisted_candidate_keys: frozenset[tuple[str, str]] = frozenset()
    shortlisted_candidate_order: dict[tuple[str, str], int] = {}
    recommendation_metadata: dict[
        tuple[str, str],
        tuple[
            ProductRecommendationRole,
            int,
            RecommendationCriterion,
            tuple[str, ...],
        ],
    ] = {}
    recommendation_task_ids = frozenset(
        action.task_id
        for action in (next_action_plan.primary, next_action_plan.secondary)
        if action is not None
        and action.kind == NextActionKind.RECOMMEND_ONE
        and action.task_id is not None
    )
    candidate_shortlist_applied = False
    if catalog_planning is not None:
        scoped_search_plans = tuple(
            plan
            for plan in catalog_planning.search_plans
            if plan.task_id in response_task_ids
        )
        (
            shortlisted_candidate_keys,
            shortlisted_candidate_order,
            candidate_shortlist_applied,
        ) = _presentable_candidate_shortlist(
            scoped_search_plans,
            source_snapshot,
            task_order=response_task_order,
            recommendation_task_ids=recommendation_task_ids,
        )
        recommendation_metadata = _recommendation_metadata(
            scoped_search_plans,
            source_snapshot,
            shortlisted_candidate_keys,
            recommendation_task_ids,
        )

    def remember(ref: SourceReference) -> SourceReference:
        sources.setdefault(ref.source_ref_id, ref)
        return ref

    for fact in dialogue_state.constraints:
        if not fact.active:
            continue
        if (
            fact.goal_id is not None
            and fact.goal_id not in response_goal_ids
        ) or (
            fact.goal_id is None
            and fact.task_id is not None
            and fact.task_id not in response_task_ids
        ):
            rejected.append(
                RejectedClaim(
                    subject_ref=fact.goal_id or fact.task_id or "dialogue",
                    predicate=fact.name,
                    reason_code="constraint_outside_answer_task_scope",
                )
            )
            continue
        related_readiness = []
        if fact.task_id is not None and fact.task_id in readiness_by_task:
            related_readiness.append(readiness_by_task[fact.task_id])
        if fact.goal_id is not None:
            related_readiness.extend(readiness_by_goal.get(fact.goal_id, ()))
        related_readiness = list(
            {
                item.task_id: item
                for item in related_readiness
                if item.contract_id is not None
            }.values()
        )
        related_contract_map: dict[str, ProductContract] = {}
        for item in related_readiness:
            contract = contract_registry.get(item.contract_id)
            if contract is not None:
                related_contract_map[contract.contract_id] = contract
        if fact.task_id is not None:
            contract = typed_contract_by_task.get(fact.task_id)
            if contract is not None:
                related_contract_map[contract.contract_id] = contract
        if fact.goal_id is not None:
            related_contract_map.update(
                typed_contracts_by_goal.get(fact.goal_id, {})
            )
        elif fact.task_id is None:
            for task_id in response_task_ids:
                contract = typed_contract_by_task.get(task_id)
                if contract is not None:
                    related_contract_map[contract.contract_id] = contract
        related_contracts = tuple(related_contract_map.values())
        catalogue_applicable = any(
            canonical_fact_name(contract, fact.name, fact.unit) is not None
            for contract in related_contracts
        )
        commerce_name = canonical_commerce_field_name(fact.name)
        commerce_scope_task_ids = {
            *(
                (fact.task_id,)
                if fact.task_id is not None and fact.task_id in response_task_ids
                else ()
            ),
            *(
                task_id
                for task_id in response_task_ids
                if fact.goal_id is not None
                and (task := tasks.get(task_id)) is not None
                and task.target_goal_id == fact.goal_id
            ),
            *(
                response_task_ids
                if fact.task_id is None and fact.goal_id is None
                else ()
            ),
        }
        commerce_schema_available = any(
            task_id in commerce_fact_names_by_task
            for task_id in commerce_scope_task_ids
        )
        commerce_applicable = any(
            commerce_name in commerce_fact_names_by_task.get(task_id, set())
            for task_id in commerce_scope_task_ids
        )
        request_coordinate_applicable = bool(
            fact.status == ConstraintStatus.KNOWN
            and commerce_name in _DISPLAY_ONLY_COMMERCE_FACTS
            and any(
                (task := tasks.get(task_id)) is not None
                and task.act in _DISPLAY_ONLY_COMMERCE_ACTS
                for task_id in commerce_scope_task_ids
            )
        )
        if (
            (related_contracts or commerce_schema_available)
            and not catalogue_applicable
            and not commerce_applicable
            and not request_coordinate_applicable
        ) or (
            fact.status != ConstraintStatus.KNOWN
            and not related_contracts
            and not commerce_schema_available
        ):
            rejected.append(
                RejectedClaim(
                    subject_ref=fact.goal_id or fact.task_id or "dialogue",
                    predicate=fact.name,
                    reason_code="constraint_not_applicable_to_resolved_task_contract",
                )
            )
            continue
        ref = remember(
            _source(
                SourceType.CONSTRAINT_FACT,
                fact.fact_id,
                field_name=fact.name,
                task_id=fact.task_id,
                goal_id=fact.goal_id,
                source_turn=fact.source_turn,
            )
        )
        if fact.status == ConstraintStatus.KNOWN and fact.value is not None:
            item = _claim(
                ClaimKind.CUSTOMER_CONSTRAINT,
                fact.goal_id or fact.task_id or "dialogue",
                fact.name,
                fact.value,
                (ref,),
                unit=fact.unit,
                task_id=fact.task_id,
                goal_id=fact.goal_id,
                reason_codes=("confirmed_customer_constraint",),
            )
            claims.setdefault(item.claim_id, item)
        else:
            status = _limitation_status(fact.status)
            limitations.append(
                LimitationPlan(
                    limitation_id=_stable_id("limit", fact.fact_id, fact.status.value),
                    status=status,
                    reason_code=f"customer_fact_{fact.status.value}",
                    task_id=fact.task_id,
                    goal_id=fact.goal_id,
                    fact_name=fact.name,
                    source_ref_ids=(ref.source_ref_id,),
                    allowed_strategy_kinds=_strategy_options(status),
                )
            )

    if catalog_planning is not None:
        for search_plan in catalog_planning.search_plans:
            if search_plan.task_id not in response_task_ids:
                continue
            readiness = readiness_by_task.get(search_plan.task_id)
            goal_ref = (
                remember(
                    _source(
                        SourceType.PRODUCT_GOAL,
                        search_plan.goal_id,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        source_turn=dialogue_state.turn_number,
                    )
                )
                if search_plan.goal_id is not None
                else None
            )
            plan_ref = remember(
                _source(
                    SourceType.CATALOG_SEARCH_PLAN,
                    search_plan.plan_id,
                    task_id=search_plan.task_id,
                    goal_id=search_plan.goal_id,
                    source_turn=dialogue_state.turn_number,
                )
            )
            allowed_candidate_skus = {
                *search_plan.eligible_skus,
                *search_plan.relaxed_skus,
                *search_plan.unverified_skus,
            }
            hard_constraint_names = {
                item.name for item in search_plan.hard_constraints
            }
            for candidate in search_plan.candidate_assessments:
                if candidate.status == CandidateStatus.REJECTED:
                    continue
                if candidate.sku not in allowed_candidate_skus:
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_presentation",
                            reason_code="candidate_not_selected_by_catalog_planner",
                        )
                    )
                    continue
                if (
                    candidate.product_kind != search_plan.product_kind
                    or candidate.role != search_plan.requested_role
                ):
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_presentation",
                            reason_code="candidate_kind_or_role_mismatch",
                        )
                    )
                    continue
                if candidate.mismatched_hard_facts or any(
                    item.fact_name in hard_constraint_names
                    for item in candidate.relaxations
                ):
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_presentation",
                            reason_code="hard_constraint_violation_not_presentable",
                        )
                    )
                    continue
                product = source_snapshot.product(candidate.sku)
                if product is None:
                    missing_sources.append(f"catalog_product:{candidate.sku}")
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_identity",
                            reason_code="catalog_answer_source_missing",
                        )
                    )
                    continue
                if (
                    product.product_kind != candidate.product_kind
                    or product.role != candidate.role
                ):
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_identity",
                            reason_code="catalog_source_kind_or_role_mismatch",
                        )
                    )
                    continue
                if (search_plan.plan_id, candidate.sku) not in shortlisted_candidate_keys:
                    rejected.append(
                        RejectedClaim(
                            subject_ref=candidate.sku,
                            predicate="product_presentation",
                            reason_code="candidate_not_in_presentable_shortlist",
                        )
                    )
                    continue
                candidate_ref = remember(
                    _source(
                        SourceType.CANDIDATE_ASSESSMENT,
                        f"{search_plan.plan_id}:{candidate.sku}",
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        source_turn=dialogue_state.turn_number,
                    )
                )
                identity_ref = remember(
                    _source(
                        SourceType.CATALOG_IDENTITY,
                        candidate.sku,
                        field_name="name",
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                    )
                )
                product_plan_id = _stable_id(
                    "product_plan", search_plan.plan_id, candidate.sku
                )
                product_claim_ids: list[str] = []
                identity = _claim(
                    ClaimKind.PRODUCT_IDENTITY,
                    candidate.sku,
                    "name",
                    product.name,
                    (identity_ref, candidate_ref),
                    task_id=search_plan.task_id,
                    goal_id=search_plan.goal_id,
                    reason_codes=("catalog_identity_confirmed",),
                )
                claims.setdefault(identity.claim_id, identity)
                product_claim_ids.append(identity.claim_id)

                fact_names = {
                    *candidate.matched_hard_facts,
                    *candidate.matched_soft_facts,
                    *unavailable_catalog_facts_by_task.get(search_plan.task_id, ()),
                }
                for fact in product.facts:
                    if fact.name not in fact_names:
                        continue
                    displayed_for_unavailable_customer_fact = bool(
                        fact.name
                        in unavailable_catalog_facts_by_task.get(
                            search_plan.task_id,
                            (),
                        )
                        and fact.name
                        not in {
                            *candidate.matched_hard_facts,
                            *candidate.matched_soft_facts,
                        }
                    )
                    if (
                        displayed_for_unavailable_customer_fact
                        and not _catalog_fact_is_unambiguous_for_display(fact)
                    ):
                        rejected.append(
                            RejectedClaim(
                                subject_ref=candidate.sku,
                                predicate=fact.name,
                                reason_code=(
                                    "catalog_attribute_ambiguous_provenance_not_displayed"
                                ),
                            )
                        )
                        continue
                    fact_ref = remember(
                        _source(
                            SourceType.CATALOG_ATTRIBUTE,
                            candidate.sku,
                            field_name=fact.name,
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    item = _claim(
                        ClaimKind.PRODUCT_ATTRIBUTE,
                        candidate.sku,
                        fact.name,
                        fact.value,
                        (fact_ref, candidate_ref),
                        unit=fact.unit,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        reason_codes=(
                            (
                                "catalog_attribute_confirmed_for_unavailable_customer_fact"
                                if displayed_for_unavailable_customer_fact
                                else "catalog_attribute_confirmed"
                            ),
                        ),
                    )
                    claims.setdefault(item.claim_id, item)
                    product_claim_ids.append(item.claim_id)

                if product.price is not None:
                    price_ref = remember(
                        _source(
                            SourceType.CATALOG_PRICE,
                            candidate.sku,
                            field_name="price",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    item = _claim(
                        ClaimKind.PRICE,
                        candidate.sku,
                        "price",
                        product.price,
                        (price_ref,),
                        unit=product.currency,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                    )
                    claims.setdefault(item.claim_id, item)
                    product_claim_ids.append(item.claim_id)
                if product.stock_qty is not None:
                    stock_ref = remember(
                        _source(
                            SourceType.CATALOG_STOCK,
                            candidate.sku,
                            field_name="stock_qty",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    item = _claim(
                        ClaimKind.STOCK,
                        candidate.sku,
                        "stock_qty",
                        product.stock_qty,
                        (stock_ref,),
                        # The feed exposes a numeric balance but not its unit
                        # of accounting.  Pipes may be stocked in metres or
                        # coils, so claiming "pcs" here would invent meaning.
                        unit=None,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                    )
                    claims.setdefault(item.claim_id, item)
                    product_claim_ids.append(item.claim_id)
                # Quantity and availability status answer different customer
                # questions.  A feed balance without its accounting unit must
                # stay labelled as such, but it must not hide an independently
                # supplied, confirmed ``in stock / out of stock`` status.
                if (
                    product.stock_status
                    and product.stock_status.casefold()
                    not in {"unknown", "неизвестно"}
                ):
                    stock_status_ref = remember(
                        _source(
                            SourceType.CATALOG_STOCK,
                            candidate.sku,
                            field_name="stock_status",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    status_item = _claim(
                        ClaimKind.STOCK,
                        candidate.sku,
                        "stock_status",
                        str(product.stock_status),
                        (stock_status_ref,),
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        reason_codes=("catalog_stock_status_confirmed",),
                    )
                    claims.setdefault(status_item.claim_id, status_item)
                    product_claim_ids.append(status_item.claim_id)
                if product.url and product.url.startswith(("https://", "http://")):
                    link_ref = remember(
                        _source(
                            SourceType.CATALOG_LINK,
                            candidate.sku,
                            field_name="url",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    item = _claim(
                        ClaimKind.LINK,
                        candidate.sku,
                        "url",
                        product.url,
                        (link_ref,),
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                    )
                    claims.setdefault(item.claim_id, item)
                    product_claim_ids.append(item.claim_id)

                difference_ids: list[str] = []
                for relaxation in candidate.relaxations:
                    difference = AnalogDifference(
                        difference_id=_stable_id(
                            "difference",
                            product_plan_id,
                            relaxation.fact_name,
                            relaxation.requested_value,
                            relaxation.candidate_value,
                        ),
                        product_plan_id=product_plan_id,
                        fact_name=relaxation.fact_name,
                        requested_value=relaxation.requested_value,
                        candidate_value=relaxation.candidate_value,
                        source_ref_ids=(candidate_ref.source_ref_id,),
                        reason_code=relaxation.reason_code,
                    )
                    differences.append(difference)
                    difference_ids.append(difference.difference_id)

                presentation_status = _presentation_status(candidate, readiness)
                recommendation = recommendation_metadata.get(
                    (search_plan.plan_id, candidate.sku)
                )
                recommendation_ref = None
                if recommendation is not None:
                    (
                        recommendation_role,
                        recommendation_rank,
                        recommendation_criterion,
                        recommendation_reasons,
                    ) = recommendation
                    recommendation_ref = remember(
                        _source(
                            SourceType.POLICY_REASON,
                            recommendation_reasons[0],
                            field_name="recommendation",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                            source_turn=dialogue_state.turn_number,
                        )
                    )
                else:
                    recommendation_role = None
                    recommendation_rank = None
                    recommendation_criterion = None
                    recommendation_reasons = ()
                products.append(
                    ProductPresentationPlan(
                        product_plan_id=product_plan_id,
                        sku=candidate.sku,
                        name=product.name,
                        product_kind=candidate.product_kind,
                        role=candidate.role,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        search_plan_id=search_plan.plan_id,
                        status=presentation_status,
                        matched_hard_facts=candidate.matched_hard_facts,
                        missing_hard_facts=candidate.missing_hard_facts,
                        matched_soft_facts=candidate.matched_soft_facts,
                        mismatched_soft_facts=candidate.mismatched_soft_facts,
                        claim_ids=tuple(product_claim_ids),
                        difference_ids=tuple(difference_ids),
                        source_ref_ids=tuple(
                            item
                            for item in (
                                plan_ref.source_ref_id,
                                candidate_ref.source_ref_id,
                                goal_ref.source_ref_id if goal_ref is not None else None,
                                (
                                    recommendation_ref.source_ref_id
                                    if recommendation_ref is not None
                                    else None
                                ),
                            )
                            if item is not None
                        ),
                        reason_codes=candidate.reason_codes,
                        recommendation_role=recommendation_role,
                        recommendation_rank=recommendation_rank,
                        recommendation_criterion=recommendation_criterion,
                        recommendation_reason_codes=recommendation_reasons,
                    )
                )
                # Missing feed facts belong to this candidate, not to the
                # whole task.  ProductPresentationPlan already carries the
                # exact candidate-scoped list and the renderer exposes it on
                # the corresponding card.  Promoting these records to generic
                # task limitations made a fact absent on one SKU sound absent
                # on every shown SKU.
            if "no_verified_in_stock_contract_match" in search_plan.reason_codes:
                limitations.append(
                    LimitationPlan(
                        limitation_id=_stable_id(
                            "limit", search_plan.plan_id, "no_verified_in_stock"
                        ),
                        status=LimitationStatus.UNSUPPORTED,
                        reason_code="no_verified_in_stock_contract_match",
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        source_ref_ids=(plan_ref.source_ref_id,),
                        allowed_strategy_kinds=(
                            ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS,
                            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                        ),
                    )
                )
            elif "no_verified_contract_match" in search_plan.reason_codes:
                limitations.append(
                    LimitationPlan(
                        limitation_id=_stable_id("limit", search_plan.plan_id, "no_match"),
                        status=LimitationStatus.UNSUPPORTED,
                        reason_code="no_verified_contract_match",
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                        source_ref_ids=(plan_ref.source_ref_id,),
                        allowed_strategy_kinds=(
                            ResponseStrategyKind.PRESENT_CONTROLLED_ANALOG,
                            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                        ),
                    )
                )

        if (
            catalog_planning.solution_plan is not None
            and response_task_ids.intersection(
                catalog_planning.solution_plan.task_ids
            )
        ):
            solution = catalog_planning.solution_plan
            remember(
                _source(
                    SourceType.SOLUTION_PLAN,
                    solution.solution_id,
                    source_turn=dialogue_state.turn_number,
                )
            )
            for dependency in solution.unresolved_dependencies:
                limitations.append(
                    LimitationPlan(
                        limitation_id=_stable_id("limit", solution.solution_id, dependency),
                        status=LimitationStatus.UNVERIFIED,
                        reason_code="solution_dependency_unresolved",
                        fact_name=dependency,
                        allowed_strategy_kinds=(
                            ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS,
                            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                        ),
                    )
                )

        task_order = {
            task_id: index for index, task_id in enumerate(next_action_plan.task_ids)
        }
        products.sort(
            key=lambda item: (
                task_order.get(item.task_id, len(task_order)),
                item.task_id,
                shortlisted_candidate_order.get(
                    (item.search_plan_id, item.sku),
                    MAX_PRESENTABLE_CANDIDATES,
                ),
                item.sku.casefold(),
                item.sku,
            )
        )
        difference_order = {
            difference_id: index
            for index, product in enumerate(products)
            for difference_id in product.difference_ids
        }
        differences.sort(
            key=lambda item: (
                difference_order.get(item.difference_id, len(difference_order)),
                item.difference_id,
            )
        )

    if commerce_planning is not None:
        for workflow in commerce_planning.workflows:
            if workflow.task_ids and not response_task_ids.intersection(
                workflow.task_ids
            ):
                continue
            workflow_ref = remember(
                _source(
                    SourceType.COMMERCE_WORKFLOW,
                    workflow.workflow_id,
                    task_id=(workflow.task_ids[0] if workflow.task_ids else None),
                    source_turn=workflow.updated_turn,
                )
            )
            status_value = workflow.execution_status.value
            claim_sources = [workflow_ref]
            # ``not_requested`` is internal workflow state, not a useful
            # customer-facing fact.  Keeping it in telemetry is enough; a
            # rendered line such as "operation was not requested" only adds
            # noise and can be repeated once several workflows exist.
            assertable = workflow.execution_status != CommerceExecutionStatus.NOT_REQUESTED
            if not assertable:
                rejected.append(
                    RejectedClaim(
                        subject_ref=workflow.workflow_id,
                        predicate="commerce_status",
                        reason_code="commerce_status_not_customer_visible",
                    )
                )
            if workflow.execution_status == CommerceExecutionStatus.DELIVERED:
                if not workflow.external_receipt_ref:
                    assertable = False
                    rejected.append(
                        RejectedClaim(
                            subject_ref=workflow.workflow_id,
                            predicate="commerce_status",
                            reason_code="delivered_status_without_verified_receipt",
                        )
                    )
                else:
                    claim_sources.append(
                        remember(
                            _source(
                                SourceType.COMMERCE_RECEIPT,
                                workflow.external_receipt_ref,
                                task_id=(workflow.task_ids[0] if workflow.task_ids else None),
                                source_turn=workflow.updated_turn,
                            )
                        )
                    )
            if assertable:
                item = _claim(
                    ClaimKind.COMMERCE_STATUS,
                    workflow.workflow_id,
                    "execution_status",
                    status_value,
                    claim_sources,
                    task_id=(workflow.task_ids[0] if workflow.task_ids else None),
                    reason_codes=("typed_commerce_workflow_status",),
                )
                claims.setdefault(item.claim_id, item)
            if workflow.status in {
                CommerceWorkflowStatus.BLOCKED,
                CommerceWorkflowStatus.DELIVERY_FAILED,
                CommerceWorkflowStatus.DELIVERY_UNKNOWN,
            }:
                limitations.append(
                    LimitationPlan(
                        limitation_id=_stable_id("limit", workflow.workflow_id, workflow.status.value),
                        status=LimitationStatus.CAPABILITY_BOUNDARY,
                        reason_code=(
                            "capability_unavailable"
                            if workflow.capability_mode == CapabilityMode.UNAVAILABLE
                            else f"commerce_{workflow.status.value}"
                        ),
                        task_id=(workflow.task_ids[0] if workflow.task_ids else None),
                        source_ref_ids=(workflow_ref.source_ref_id,),
                        allowed_strategy_kinds=(
                            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                        ),
                    )
                )
        for boundary in commerce_planning.capability_boundaries:
            workflow_id, _, reason = boundary.partition(":")
            boundary_source_ref_ids = tuple(
                item.source_ref_id
                for item in sources.values()
                if item.source_type == SourceType.COMMERCE_WORKFLOW
                and item.source_id == workflow_id
            )
            if not boundary_source_ref_ids:
                continue
            limitations.append(
                LimitationPlan(
                    limitation_id=_stable_id("limit", boundary),
                    status=LimitationStatus.CAPABILITY_BOUNDARY,
                    reason_code=reason or "commerce_capability_boundary",
                    source_ref_ids=boundary_source_ref_ids,
                    allowed_strategy_kinds=(
                        ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                    ),
                )
            )

    for capability in source_snapshot.capability_facts:
        capability_task_id = capability.task_id
        if capability_task_id is not None:
            if capability_task_id not in response_task_ids:
                continue
        else:
            allowed_acts = _CAPABILITY_FACT_ACTS.get(capability.name)
            capability_task_id = next(
                (
                    task_id
                    for task_id in response_task_order
                    if (task := tasks.get(task_id)) is not None
                    and allowed_acts is not None
                    and task.act in allowed_acts
                ),
                None,
            )
            if capability_task_id is None:
                continue
        if not capability.confirmed:
            rejected.append(
                RejectedClaim(
                    subject_ref=capability_task_id or "capability",
                    predicate=capability.name,
                    reason_code="capability_fact_not_confirmed",
                )
            )
            continue
        ref = remember(
            _source(
                SourceType.CAPABILITY_RESULT,
                capability.fact_id,
                field_name=capability.name,
                task_id=capability_task_id,
            )
        )
        item = _claim(
            ClaimKind.CAPABILITY_FACT,
            capability_task_id or capability.fact_id,
            capability.name,
            capability.value,
            (ref,),
            unit=capability.unit,
            task_id=capability_task_id,
            reason_codes=("verified_deterministic_capability_fact",),
        )
        claims.setdefault(item.claim_id, item)

    def readiness_for_action(
        action: NextAction,
    ) -> TaskReadinessAssessment | None:
        """Resolve readiness by task, then by the same typed product goal.

        Explain/check tasks may be independent from the catalogue selection
        task while still pointing at the same ProductGoal.  In that case only
        a readiness record that actually owns the requested fact is eligible.
        """

        fact_name = action.fact_name
        direct = readiness_by_task.get(action.task_id or "")
        if direct is not None and (
            not fact_name or fact_name in _readiness_fact_names(direct)
        ):
            return direct
        task = tasks.get(action.task_id or "")
        if task is None or task.target_goal_id is None or not fact_name:
            return direct
        matching = sorted(
            (
                item
                for item in readiness_by_goal.get(task.target_goal_id, ())
                if fact_name in _readiness_fact_names(item)
            ),
            key=lambda item: (
                item.task_id,
                str(item.contract_id or ""),
                item.product_kind.value,
            ),
        )
        return matching[0] if matching else direct

    def contract_for_action(action: NextAction) -> ProductContract | None:
        """Resolve only an already-typed contract for the action's fact."""

        readiness = readiness_for_action(action)
        contract = None
        if readiness is not None:
            contract = typed_contract_by_task.get(readiness.task_id)
        if contract is None:
            contract = typed_contract_by_task.get(action.task_id or "")
        if contract is None:
            task = tasks.get(action.task_id or "")
            goal_id = task.target_goal_id if task is not None else None
            matching_contracts = sorted(
                (
                    contract
                    for contract in typed_contracts_by_goal.get(goal_id or "", {}).values()
                    if canonical_fact_name(contract, action.fact_name or "") is not None
                ),
                key=lambda item: item.contract_id,
            )
            contract = matching_contracts[0] if matching_contracts else None
        return contract

    def guidance_for_action(
        action: NextAction,
    ) -> tuple[str | None, str | None]:
        readiness = readiness_for_action(action)
        contract = contract_for_action(action)
        contract_method, expected_unit = _contract_fact_guidance(
            contract,
            action.fact_name,
        )
        readiness_names = (
            _readiness_fact_names(readiness) if readiness is not None else frozenset()
        )
        readiness_method = (
            readiness.learn_method_code
            if readiness is not None
            and (
                action.fact_name == readiness.recommended_question_fact
                or (
                    readiness.recommended_question_fact is None
                    and len(readiness_names) == 1
                    and action.fact_name in readiness_names
                )
            )
            else None
        )
        # The contract definition is fact-scoped, whereas the convenience
        # code on a readiness assessment describes its recommended *next*
        # question.  Prefer the contract so a request about an already marked
        # unknown diameter cannot accidentally receive the method for the next
        # missing head/pressure fact.
        return contract_method or readiness_method, expected_unit

    def contract_fact_metadata(
        action: NextAction,
    ) -> tuple[bool, bool, bool]:
        """Expose declarative relevance without inventing engineering claims."""

        contract = contract_for_action(action)
        if contract is None or not action.fact_name:
            return False, False, False
        canonical = canonical_fact_name(contract, action.fact_name)
        if canonical is None:
            return False, False, False
        definition = next(
            (item for item in contract.fact_definitions if item.name == canonical),
            None,
        )
        if definition is None:
            return False, False, False
        return (
            True,
            bool(definition.decision_changing),
            bool(definition.required_for_exact),
        )

    def information_next_step_kind(
        action: NextAction,
        *,
        learn_method_code: str | None,
    ) -> NextStepKind:
        """Map a typed information deliverable to a grounded answer operation."""

        if action.information_request_id is None:
            return _next_step(action)
        outputs = set(action.requested_outputs)
        if (
            action.output_relation == InformationOutputRelation.ANY
            and RequestedInformationOutput.INSTRUCTION in outputs
        ):
            return (
                NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
                if learn_method_code
                else NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY
            )
        if (
            action.reason_code == "verified_information_source_unavailable"
            or action.information_purpose == InformationPurpose.PROVENANCE
            or RequestedInformationOutput.VERIFIED_LINK in outputs
        ):
            return NextStepKind.STATE_INFORMATION_SOURCE_BOUNDARY
        if (
            action.information_purpose == InformationPurpose.DETERMINATION_METHOD
            or RequestedInformationOutput.INSTRUCTION in outputs
        ):
            return (
                NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
                if learn_method_code
                else NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY
            )
        if action.information_purpose == InformationPurpose.DECISION_RELEVANCE:
            return NextStepKind.EXPLAIN_DECISION_RELEVANCE
        if action.information_purpose == InformationPurpose.COMPATIBILITY:
            return NextStepKind.STATE_COMPATIBILITY_BOUNDARY
        if action.information_purpose == InformationPurpose.MEANING:
            return NextStepKind.STATE_INFORMATION_MEANING_BOUNDARY
        if action.information_purpose == InformationPurpose.VALUE:
            return NextStepKind.PROVIDE_DIRECT_ANSWER
        return NextStepKind.STATE_CAPABILITY_BOUNDARY

    def candidate_fact_report_for_action(
        action: NextAction,
    ) -> CandidateFactReport | None:
        """Read one fact across the exact cards committed in the last V2 answer.

        The customer's own unknown goal fact is deliberately irrelevant here:
        this report inspects catalogue facts attached to each previously shown
        candidate.  It never re-runs a search, reads reply text or promotes an
        ambiguous/missing catalogue field to a scalar value.
        """

        if (
            action.information_request_id is None
            or action.information_subject_scope
            != InformationSubjectScope.PRESENTED_CANDIDATES
            or action.information_purpose != InformationPurpose.VALUE
            or not action.fact_name
        ):
            return None
        summary = dialogue_state.answer_plan_summary
        if (
            summary is None
            or summary.delivery_status
            != ShadowDeliveryStatus.COMMITTED_TO_SESSION
            or not summary.presented_candidates
        ):
            return None
        task = tasks.get(action.task_id or "")
        goal_id = task.target_goal_id if task is not None else None
        presented = tuple(
            item
            for item in summary.presented_candidates
            if goal_id is None or item.goal_id == goal_id
        )
        if not presented:
            return None
        contract = contract_for_action(action)
        canonical = (
            canonical_fact_name(contract, action.fact_name)
            if contract is not None
            else None
        )
        fact_name = canonical or action.fact_name
        report_items: list[CandidateFactReportItem] = []
        for candidate in presented:
            product = source_snapshot.product(candidate.sku)
            identity_ref = remember(
                _source(
                    SourceType.CATALOG_IDENTITY,
                    candidate.sku,
                    field_name="name",
                    task_id=action.task_id,
                    goal_id=goal_id,
                )
            )
            if (
                product is None
                or product.name != candidate.name
                or product.product_kind != candidate.product_kind
                or product.role != candidate.role
            ):
                report_items.append(
                    CandidateFactReportItem(
                        item_id=_stable_id(
                            "candidate_fact_item",
                            action.information_request_id,
                            candidate.sku,
                            fact_name,
                        ),
                        sku=candidate.sku,
                        name=candidate.name,
                        fact_name=fact_name,
                        status=CandidateFactStatus.MISSING,
                        source_ref_ids=(identity_ref.source_ref_id,),
                        reason_codes=("catalog_product_source_missing",),
                    )
                )
                continue
            matching_facts = tuple(
                item for item in product.facts if item.name == fact_name
            )
            fact_issues = tuple(
                item for item in product.fact_issues if item.name == fact_name
            )
            distinct_values = {
                (str(item.value), str(item.unit or "")) for item in matching_facts
            }
            ambiguous = bool(
                fact_issues
                or len(distinct_values) > 1
                or any(
                    not _catalog_fact_is_unambiguous_for_display(item)
                    for item in matching_facts
                )
            )
            if ambiguous:
                issue_ref = remember(
                    _source(
                        SourceType.CATALOG_ATTRIBUTE,
                        candidate.sku,
                        field_name=fact_name,
                        task_id=action.task_id,
                        goal_id=goal_id,
                    )
                )
                report_items.append(
                    CandidateFactReportItem(
                        item_id=_stable_id(
                            "candidate_fact_item",
                            action.information_request_id,
                            candidate.sku,
                            fact_name,
                        ),
                        sku=candidate.sku,
                        name=product.name,
                        fact_name=fact_name,
                        status=CandidateFactStatus.AMBIGUOUS,
                        source_ref_ids=(
                            identity_ref.source_ref_id,
                            issue_ref.source_ref_id,
                        ),
                        reason_codes=("catalog_candidate_fact_ambiguous",),
                    )
                )
                continue
            if len(matching_facts) == 1:
                fact = matching_facts[0]
                fact_ref = remember(
                    _source(
                        SourceType.CATALOG_ATTRIBUTE,
                        candidate.sku,
                        field_name=fact_name,
                        task_id=action.task_id,
                        goal_id=goal_id,
                    )
                )
                report_items.append(
                    CandidateFactReportItem(
                        item_id=_stable_id(
                            "candidate_fact_item",
                            action.information_request_id,
                            candidate.sku,
                            fact_name,
                        ),
                        sku=candidate.sku,
                        name=product.name,
                        fact_name=fact_name,
                        status=CandidateFactStatus.CONFIRMED,
                        value=fact.value,
                        unit=fact.unit,
                        source_ref_ids=(
                            identity_ref.source_ref_id,
                            fact_ref.source_ref_id,
                        ),
                        reason_codes=("catalog_candidate_fact_confirmed",),
                    )
                )
                continue
            report_items.append(
                CandidateFactReportItem(
                    item_id=_stable_id(
                        "candidate_fact_item",
                        action.information_request_id,
                        candidate.sku,
                        fact_name,
                    ),
                    sku=candidate.sku,
                    name=product.name,
                    fact_name=fact_name,
                    status=CandidateFactStatus.MISSING,
                    source_ref_ids=(identity_ref.source_ref_id,),
                    reason_codes=("catalog_candidate_fact_missing",),
                )
            )
        return CandidateFactReport(
            report_id=_stable_id(
                "candidate_fact_report",
                action.information_request_id,
                fact_name,
                *(item.sku for item in report_items),
            ),
            information_request_id=action.information_request_id,
            task_id=action.task_id or "information_request",
            goal_id=goal_id,
            fact_name=fact_name,
            items=tuple(report_items),
            reason_codes=("last_committed_candidate_set_inspected",),
        )

    active_action = next_action_plan.primary
    secondary_action = next_action_plan.secondary
    followup_action = (
        secondary_action
        if secondary_action is not None
        and active_action.kind
        in {
            NextActionKind.ANSWER_DIRECT_QUESTION,
            NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
            NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS,
            NextActionKind.SEARCH_EXACT,
            NextActionKind.RECOMMEND_ONE,
            NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS,
            NextActionKind.PRESENT_CONTROLLED_ANALOG,
            NextActionKind.STATE_CAPABILITY_BOUNDARY,
            NextActionKind.STATE_COMMERCE_CAPABILITY_BOUNDARY,
            NextActionKind.EXPLAIN_TERM_OR_METHOD,
            NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
        }
        and secondary_action.kind
        in {
            NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            NextActionKind.COLLECT_COMMERCE_FACT,
            NextActionKind.EXPLAIN_TERM_OR_METHOD,
            NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
        }
        else None
    )
    question_action = followup_action or active_action
    active_task = tasks.get(active_action.task_id or "")
    question_task = tasks.get(question_action.task_id or "")
    terminal_facts = {
        item.name
        for item in dialogue_state.constraints
        if item.active
        and item.status in {
            ConstraintStatus.KNOWN,
            ConstraintStatus.UNKNOWN,
            ConstraintStatus.REFUSED,
            ConstraintStatus.DEFERRED,
        }
        and (
            item.task_id == question_action.task_id
            or (
                question_task is not None
                and question_task.target_goal_id is not None
                and item.goal_id == question_task.target_goal_id
            )
        )
    }
    question = None
    question_fact_already_terminal = False
    if question_action.kind in {
        NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        NextActionKind.COLLECT_COMMERCE_FACT,
    } and question_action.task_id and question_action.fact_name:
        if question_action.fact_name in terminal_facts:
            question_fact_already_terminal = True
            rejected.append(
                RejectedClaim(
                    subject_ref=question_action.task_id,
                    predicate=question_action.fact_name,
                    reason_code="question_fact_already_terminal",
                )
            )
        else:
            learn_method_code, expected_unit = guidance_for_action(question_action)
            policy_ref = remember(
                _source(
                    SourceType.POLICY_REASON,
                    question_action.reason_code,
                    field_name=question_action.fact_name,
                    task_id=question_action.task_id,
                    source_turn=dialogue_state.turn_number,
                )
            )
            question = QuestionPlan(
                question_id=_stable_id(
                    "question", question_action.task_id, question_action.fact_name
                ),
                task_id=question_action.task_id,
                fact_name=question_action.fact_name,
                decision_impact_code=question_action.reason_code,
                learn_method_code=learn_method_code,
                expected_unit=expected_unit,
                source_ref_ids=(policy_ref.source_ref_id,),
                reason_codes=(question_action.reason_code,),
            )

    # A primary explanation is itself rendered through the typed next-step
    # method.  Preserve that method while still allowing the one secondary
    # decision question for a linked task to appear in the question section.
    next_step_action = (
        active_action
        if followup_action is not None
        and (
            active_action.information_request_id is not None
            or active_action.kind
            in {
                NextActionKind.EXPLAIN_TERM_OR_METHOD,
                NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
            }
        )
        else followup_action or active_action
    )
    next_step_learn_method, next_step_expected_unit = guidance_for_action(
        next_step_action
    )
    (
        contract_fact_recognized,
        fact_decision_changing,
        fact_required_for_exact,
    ) = contract_fact_metadata(next_step_action)
    next_step_kind = information_next_step_kind(
        next_step_action,
        learn_method_code=next_step_learn_method,
    )
    candidate_fact_report = candidate_fact_report_for_action(next_step_action)
    if candidate_fact_report is not None:
        next_step_kind = NextStepKind.REPORT_CANDIDATE_FACTS
    next_step = NextStepPlan(
        next_step_id=_stable_id(
            "next_step",
            next_step_action.kind.value,
            next_step_action.task_id,
            next_step_action.fact_name,
            next_step_action.information_request_id,
            next_step_action.information_purpose,
            next_step_action.requested_outputs,
            next_step_action.output_relation,
            next_step_action.source_kind,
            next_step_action.information_subject_scope,
            (
                candidate_fact_report.report_id
                if candidate_fact_report is not None
                else None
            ),
        ),
        kind=next_step_kind,
        task_id=next_step_action.task_id,
        fact_name=next_step_action.fact_name,
        learn_method_code=next_step_learn_method,
        expected_unit=next_step_expected_unit,
        information_request_id=next_step_action.information_request_id,
        information_purpose=next_step_action.information_purpose,
        requested_outputs=next_step_action.requested_outputs,
        output_relation=next_step_action.output_relation,
        source_kind=next_step_action.source_kind,
        information_subject_scope=next_step_action.information_subject_scope,
        candidate_fact_report=candidate_fact_report,
        contract_fact_recognized=contract_fact_recognized,
        fact_decision_changing=fact_decision_changing,
        fact_required_for_exact=fact_required_for_exact,
        reason_codes=(next_step_action.reason_code,),
    )
    if (
        question_fact_already_terminal
        and next_step_action.information_request_id is None
    ):
        next_step = NextStepPlan(
            next_step_id=_stable_id(
                "next_step",
                NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
                question_action.task_id,
                "question_fact_already_terminal",
            ),
            kind=NextStepKind.CONTINUE_WITH_CONFIRMED_FACTS,
            task_id=question_action.task_id,
            reason_codes=("question_fact_already_terminal",),
        )

    active_catalog_no_match = bool(
        catalog_planning is not None
        and active_action.task_id is not None
        and any(
            search_plan.task_id == active_action.task_id
            and "no_verified_contract_match" in search_plan.reason_codes
            for search_plan in catalog_planning.search_plans
        )
    )
    if (
        active_catalog_no_match
        and followup_action is None
        and active_action.information_request_id is None
    ):
        active_products = tuple(
            product
            for product in products
            if product.task_id == active_action.task_id
        )
        # The policy correctly asked for an exact search; the catalogue result
        # is the new information that makes "continue with confirmed facts"
        # misleading.  Unverified cards are explicitly preliminary; with no
        # cards at all the honest capability boundary remains terminal for the
        # unchanged hard constraints.
        no_match_next_kind = (
            NextStepKind.SHOW_PRELIMINARY_OPTIONS
            if active_products
            and all(
                product.status == ProductPresentationStatus.UNVERIFIED
                for product in active_products
            )
            else NextStepKind.STATE_CAPABILITY_BOUNDARY
        )
        next_step = NextStepPlan(
            next_step_id=_stable_id(
                "next_step",
                no_match_next_kind.value,
                active_action.task_id,
                "no_verified_contract_match",
            ),
            kind=no_match_next_kind,
            task_id=active_action.task_id,
            reason_codes=(
                "no_verified_match_preliminary_candidates_only"
                if no_match_next_kind == NextStepKind.SHOW_PRELIMINARY_OPTIONS
                else "no_verified_contract_match",
            ),
        )

    direct_claim_ids: list[str] = []
    direct_limitation_ids: list[str] = []
    direct_task = active_task
    direct_kind = {
        TaskAct.CHECK_PRICE: ClaimKind.PRICE,
        TaskAct.CHECK_STOCK: ClaimKind.STOCK,
        TaskAct.GET_LINK: ClaimKind.LINK,
    }.get(direct_task.act if direct_task else None)
    typed_information_action = (
        active_action if active_action.information_request_id is not None else None
    )
    if candidate_fact_report is not None:
        # A fact report is an answer *about* the cards already shown, not a new
        # catalogue result page.  Do not repeat the shortlist or re-render the
        # customer's still-unknown installation value alongside it.
        products.clear()
        differences.clear()
        claims = {
            claim_id: item
            for claim_id, item in claims.items()
            if item.kind
            not in {
                ClaimKind.PRODUCT_IDENTITY,
                ClaimKind.PRODUCT_ATTRIBUTE,
                ClaimKind.PRICE,
                ClaimKind.STOCK,
                ClaimKind.LINK,
            }
        }
    if (
        typed_information_action is not None
        and typed_information_action.information_purpose == InformationPurpose.VALUE
    ):
        contract = contract_for_action(typed_information_action)
        canonical = (
            canonical_fact_name(contract, typed_information_action.fact_name or "")
            if contract is not None
            else None
        )
        requested_fact_names = {
            item
            for item in (typed_information_action.fact_name, canonical)
            if item
        }

        def in_information_scope(item: AnswerClaim) -> bool:
            return bool(
                item.task_id == typed_information_action.task_id
                or (
                    direct_task is not None
                    and direct_task.target_goal_id is not None
                    and item.goal_id == direct_task.target_goal_id
                )
            )

        if candidate_fact_report is None:
            matching_constraints = [
                item.claim_id
                for item in claims.values()
                if item.kind == ClaimKind.CUSTOMER_CONSTRAINT
                and item.predicate in requested_fact_names
                and in_information_scope(item)
            ]
            direct_claim_ids = matching_constraints or [
                item.claim_id
                for item in claims.values()
                if item.kind in {ClaimKind.CAPABILITY_FACT, ClaimKind.PRODUCT_ATTRIBUTE}
                and item.predicate in requested_fact_names
                and in_information_scope(item)
            ]
        if not direct_claim_ids and candidate_fact_report is None:
            next_step = next_step.model_copy(
                update={
                    "next_step_id": _stable_id(
                        "next_step",
                        NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY.value,
                        typed_information_action.task_id,
                        typed_information_action.fact_name,
                        typed_information_action.information_request_id,
                    ),
                    "kind": NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY,
                    "reason_codes": (
                        "verified_information_value_source_unavailable",
                    ),
                }
            )
    elif typed_information_action is not None:
        # Explanations, methods and document requests are fulfilled by the
        # typed next-step renderer.  In particular a catalogue product URL is
        # not promoted to a manufacturer passport or technical document.
        pass
    elif direct_kind is not None:
        direct_claim_ids = [
            item.claim_id
            for item in claims.values()
            if item.kind == direct_kind
            and (
                item.task_id == active_action.task_id
                or (
                    direct_task is not None
                    and direct_task.target_goal_id is not None
                    and item.goal_id == direct_task.target_goal_id
                )
            )
        ]
        if not direct_claim_ids:
            direct_limitation = LimitationPlan(
                limitation_id=_stable_id(
                    "limit", active_action.task_id, direct_kind.value
                ),
                status=LimitationStatus.UNVERIFIED,
                reason_code=f"verified_{direct_kind.value}_source_missing",
                task_id=active_action.task_id,
                goal_id=(direct_task.target_goal_id if direct_task else None),
                allowed_strategy_kinds=(
                    ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                ),
            )
            limitations.append(direct_limitation)
            direct_limitation_ids.append(direct_limitation.limitation_id)
    elif active_action.kind in {
        NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
        NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS,
    }:
        direct_claim_ids = [
            item.claim_id
            for item in claims.values()
            if item.kind == ClaimKind.COMMERCE_STATUS
            and item.task_id == active_action.task_id
        ]
        if not direct_claim_ids:
            direct_limitation = LimitationPlan(
                limitation_id=_stable_id(
                    "limit", active_action.task_id, "commerce_status"
                ),
                status=LimitationStatus.CAPABILITY_BOUNDARY,
                reason_code="verified_commerce_status_source_missing",
                task_id=active_action.task_id,
                allowed_strategy_kinds=(
                    ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                ),
            )
            limitations.append(direct_limitation)
            direct_limitation_ids.append(direct_limitation.limitation_id)
    elif active_action.kind == NextActionKind.ANSWER_DIRECT_QUESTION:
        direct_claim_ids = [
            item.claim_id
            for item in claims.values()
            if item.task_id == active_action.task_id
            and item.kind in {ClaimKind.CAPABILITY_FACT, ClaimKind.PRODUCT_ATTRIBUTE}
        ]
        if not direct_claim_ids:
            direct_limitation = LimitationPlan(
                limitation_id=_stable_id(
                    "limit", active_action.task_id, "direct_answer"
                ),
                status=LimitationStatus.CAPABILITY_BOUNDARY,
                reason_code="verified_direct_answer_source_missing",
                task_id=active_action.task_id,
                allowed_strategy_kinds=(
                    ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                ),
            )
            limitations.append(direct_limitation)
            direct_limitation_ids.append(direct_limitation.limitation_id)

    if (
        direct_limitation_ids
        and not direct_claim_ids
        and followup_action is None
        and typed_information_action is None
    ):
        next_step = NextStepPlan(
            next_step_id=_stable_id(
                "next_step",
                NextStepKind.STATE_CAPABILITY_BOUNDARY.value,
                active_action.task_id,
                active_action.fact_name,
            ),
            kind=NextStepKind.STATE_CAPABILITY_BOUNDARY,
            task_id=active_action.task_id,
            fact_name=active_action.fact_name,
            reason_codes=("direct_answer_source_missing",),
        )

    limitations = _merge_duplicate_limitations(limitations)
    sections: list[AnswerSection] = []
    if direct_claim_ids or direct_limitation_ids:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "direct"),
                kind=AnswerSectionKind.DIRECT_ANSWER,
                item_ids=tuple((*direct_claim_ids, *direct_limitation_ids)),
            )
        )
    nondirect_claims = tuple(
        item.claim_id for item in claims.values() if item.claim_id not in direct_claim_ids
    )
    if nondirect_claims and candidate_fact_report is None:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "facts"),
                kind=AnswerSectionKind.CONFIRMED_FACTS,
                item_ids=nondirect_claims,
            )
        )
    if products:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "products"),
                kind=AnswerSectionKind.PRODUCTS,
                item_ids=tuple(item.product_plan_id for item in products),
            )
        )
    if differences:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "differences"),
                kind=AnswerSectionKind.ANALOG_DIFFERENCES,
                item_ids=tuple(item.difference_id for item in differences),
            )
        )
    if limitations:
        remaining_limitation_ids = tuple(
            item.limitation_id
            for item in limitations
            if item.limitation_id not in direct_limitation_ids
        )
    else:
        remaining_limitation_ids = ()
    if remaining_limitation_ids and candidate_fact_report is None:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "limitations"),
                kind=AnswerSectionKind.LIMITATIONS,
                item_ids=remaining_limitation_ids,
            )
        )
    if question is not None:
        sections.append(
            AnswerSection(
                section_id=_stable_id("section", turn_id, "question"),
                kind=AnswerSectionKind.QUESTION,
                item_ids=(question.question_id,),
            )
        )
    sections.append(
        AnswerSection(
            section_id=_stable_id("section", turn_id, "next"),
            kind=AnswerSectionKind.NEXT_STEP,
            item_ids=(next_step.next_step_id,),
        )
    )

    goal_ids = tuple(
        dict.fromkeys(
            item.target_goal_id
            for task_id in next_action_plan.task_ids
            if (item := tasks.get(task_id)) is not None and item.target_goal_id
        )
    )
    signature_payload = {
        "primary": active_action.kind.value,
        "secondary": (
            next_action_plan.secondary.kind.value
            if next_action_plan.secondary is not None
            else None
        ),
        "tasks": next_action_plan.task_ids,
        "claims": sorted(claims),
        "products": [
            (
                item.sku,
                item.status.value,
                item.difference_ids,
                (
                    item.recommendation_role.value
                    if item.recommendation_role is not None
                    else None
                ),
                item.recommendation_rank,
                (
                    item.recommendation_criterion.value
                    if item.recommendation_criterion is not None
                    else None
                ),
                item.recommendation_reason_codes,
            )
            for item in products
        ],
        "limitations": sorted(
            (item.status.value, item.fact_name, item.reason_code)
            for item in limitations
        ),
        "question": question.fact_name if question else None,
        "next_step": next_step.kind.value,
        "information_request": (
            next_step.information_request_id,
            (
                next_step.information_purpose.value
                if next_step.information_purpose is not None
                else None
            ),
            tuple(item.value for item in next_step.requested_outputs),
            (
                next_step.output_relation.value
                if next_step.output_relation is not None
                else None
            ),
            next_step.source_kind.value if next_step.source_kind is not None else None,
            next_step.information_subject_scope.value,
            (
                (
                    next_step.candidate_fact_report.report_id,
                    tuple(
                        (
                            item.sku,
                            item.fact_name,
                            item.status.value,
                            item.value,
                            item.unit,
                        )
                        for item in next_step.candidate_fact_report.items
                    ),
                )
                if next_step.candidate_fact_report is not None
                else None
            ),
            next_step.contract_fact_recognized,
            next_step.fact_decision_changing,
            next_step.fact_required_for_exact,
        ),
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    has_content = bool(claims or products or candidate_fact_report is not None)
    has_valid_typed_information_answer = bool(
        next_step.information_request_id
        and next_step.information_purpose is not None
        and next_step.requested_outputs
    )
    if has_content and limitations:
        status = AnswerPlanStatus.PARTIAL
    elif has_content:
        status = AnswerPlanStatus.READY
    elif question is not None and limitations:
        status = AnswerPlanStatus.PARTIAL
    elif question is not None:
        status = AnswerPlanStatus.READY
    elif followup_action is not None and limitations:
        status = AnswerPlanStatus.PARTIAL
    elif followup_action is not None:
        status = AnswerPlanStatus.READY
    elif has_valid_typed_information_answer and limitations:
        # A typed information boundary is itself a complete grounded answer;
        # unrelated limitations remain visible without making it undeliverable.
        status = AnswerPlanStatus.PARTIAL
    elif has_valid_typed_information_answer:
        status = AnswerPlanStatus.READY
    elif limitations:
        status = AnswerPlanStatus.BOUNDARY
    elif next_step.kind == NextStepKind.STATE_CAPABILITY_BOUNDARY:
        # Keep the legacy generic boundary fail-closed.  Only the explicit,
        # typed information contract above is promoted to a deliverable answer.
        status = AnswerPlanStatus.BOUNDARY
    else:
        status = AnswerPlanStatus.UNSUPPORTED
    plan_id = _stable_id("answer_plan", turn_id, signature)
    answer_plan = AnswerPlan(
        plan_id=plan_id,
        turn_id=turn_id,
        turn_number=dialogue_state.turn_number,
        task_ids=next_action_plan.task_ids,
        goal_ids=goal_ids,
        primary_action=active_action.kind,
        secondary_action=(
            next_action_plan.secondary.kind
            if next_action_plan.secondary is not None
            else None
        ),
        status=status,
        sections=tuple(sections),
        sources=tuple(sources.values()),
        claims=tuple(claims.values()),
        products=tuple(products),
        analog_differences=tuple(differences),
        limitations=tuple(limitations),
        question=question,
        next_step=next_step,
        semantic_signature=signature,
        reason_codes=tuple(
            dict.fromkeys(
                (
                    "answer_plan_compiled_from_typed_sources",
                    *next_action_plan.reason_codes,
                    *(
                        ("presentable_candidate_shortlist_applied",)
                        if candidate_shortlist_applied
                        else ()
                    ),
                    *(("missing_sources_recorded",) if missing_sources else ()),
                )
            )
        ),
    )
    return AnswerPlanningResult(
        status="planned",
        answer_plan=answer_plan,
        accepted_claim_ids=tuple(claims),
        rejected_claims=tuple(rejected),
        missing_source_ids=tuple(dict.fromkeys(missing_sources)),
        reason_codes=("answer_plan_v2_compiled",),
    )
