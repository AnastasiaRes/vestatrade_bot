"""Pure deterministic Stage 3 catalogue planning and candidate verification."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.dialogue_v2.contracts import (
    ConstraintPolarity,
    ConstraintStatus,
    DialogueStateV2,
    NextActionKind,
    NextActionPlan,
)

from .contracts import (
    CandidateAssessment,
    CandidateStatus,
    CatalogAvailabilityStatus,
    CatalogPlanningResult,
    CatalogProductRole,
    CatalogProductSnapshot,
    CatalogRelaxation,
    CatalogSearchPlan,
    CatalogSearchStage,
    ComparisonMode,
    ContractResolution,
    ContractResolutionStatus,
    FactStrength,
    ProductContract,
    ProductKind,
    ReadinessFact,
    ReadinessStatus,
    SearchConstraint,
    SolutionComponent,
    SolutionPlan,
    TaskReadinessAssessment,
)
from .normalization import (
    normalize_fact_value,
    parse_numeric_choice_value,
    parse_numeric_range_value,
)
from .registry import ProductContractRegistry


_CATALOG_ACTIONS = {
    NextActionKind.SEARCH_EXACT,
    NextActionKind.RECOMMEND_ONE,
    NextActionKind.SHOW_PRELIMINARY_OPTIONS,
    # Loop recovery must still execute the requested catalogue capability.
    # These strategies change how an existing candidate set is presented;
    # they are not permission to skip candidate verification altogether.
    NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS,
    NextActionKind.PRESENT_CONTROLLED_ANALOG,
    NextActionKind.COMPARE,
    NextActionKind.ANSWER_DIRECT_QUESTION,
    # A product-specific follow-up may be classified as an explanation
    # (passport field, compatibility method, technical term).  The catalogue
    # plan is still useful as grounded support for the already active product
    # task; pure glossary turns simply have no resolved product assessment.
    NextActionKind.EXPLAIN_TERM_OR_METHOD,
}

_STOCK_REQUIREMENT_FACT = "stock_availability"


def _requires_in_stock_candidates(
    dialogue_state: DialogueStateV2,
    assessment: TaskReadinessAssessment,
) -> bool:
    """Resolve a durable stock filter from typed product-scoped state.

    ``CHECK_STOCK`` is intentionally absent here: it is a one-turn request to
    report availability and must not silently remove an otherwise exact
    out-of-stock product.  Only an active, known, required capability fact in
    the same goal/task scope authorizes filtering candidates.
    """

    applicable = tuple(
        fact
        for fact in dialogue_state.constraints
        if fact.active
        and fact.name == _STOCK_REQUIREMENT_FACT
        and fact.status == ConstraintStatus.KNOWN
        and (
            (
                assessment.goal_id is not None
                and fact.goal_id == assessment.goal_id
            )
            or (
                assessment.goal_id is None
                and fact.goal_id is None
                and fact.task_id in {None, assessment.task_id}
            )
        )
    )
    if not applicable:
        return False
    latest = max(applicable, key=lambda fact: fact.source_turn)
    return (
        latest.polarity == ConstraintPolarity.REQUIRED
        and latest.value is True
    )


def _executable_catalog_task_ids(
    next_action_plan: NextActionPlan,
) -> tuple[str, ...]:
    """Return the exact task scopes authorized for catalogue execution.

    ``NextActionPlan.task_ids`` preserves dialogue continuity and can contain
    linked or suspended work.  It is not an execution list.  Only typed
    primary/secondary catalogue actions authorize a search on this turn.
    """

    return tuple(
        dict.fromkeys(
            action.task_id
            for action in (
                next_action_plan.primary,
                next_action_plan.secondary,
            )
            if action is not None
            and action.kind in _CATALOG_ACTIONS
            and action.task_id is not None
        )
    )


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _constraints(
    assessment: TaskReadinessAssessment,
) -> tuple[tuple[SearchConstraint, ...], tuple[SearchConstraint, ...]]:
    hard = tuple(
        SearchConstraint(
            name=item.name,
            value=item.value,
            unit=item.unit,
            strength=FactStrength.HARD,
            polarity=item.polarity,
        )
        for item in assessment.confirmed_hard_facts
        if item.status == "known" and item.value is not None
    )
    soft = tuple(
        SearchConstraint(
            name=item.name,
            value=item.value,
            unit=item.unit,
            strength=FactStrength.SOFT,
            polarity=item.polarity,
        )
        for item in assessment.confirmed_soft_facts
        if item.status == "known" and item.value is not None
    )
    return hard, soft


def _same_value(mode: ComparisonMode, name: str, requested: object, actual: object) -> bool:
    requested_choices = (
        parse_numeric_choice_value(requested)
        if mode in {ComparisonMode.NUMERIC, ComparisonMode.MINIMUM_RATING}
        else None
    )
    if requested_choices is not None:
        right = normalize_fact_value(name, actual)
        try:
            numeric = float(right)
        except (TypeError, ValueError):
            return False
        return any(abs(float(item) - numeric) <= 1e-9 for item in requested_choices)
    requested_range = (
        parse_numeric_range_value(requested)
        if mode in {ComparisonMode.NUMERIC, ComparisonMode.MINIMUM_RATING}
        else None
    )
    if requested_range is not None:
        right = normalize_fact_value(name, actual)
        try:
            numeric = float(right)
        except (TypeError, ValueError):
            return False
        return float(requested_range[0]) <= numeric <= float(requested_range[1])
    left = normalize_fact_value(name, requested)
    right = normalize_fact_value(name, actual)
    if mode == ComparisonMode.NUMERIC:
        try:
            return abs(float(left) - float(right)) <= 1e-9
        except (TypeError, ValueError):
            return False
    if mode == ComparisonMode.MINIMUM_RATING:
        try:
            return float(right) >= float(left)
        except (TypeError, ValueError):
            return False
    if mode == ComparisonMode.BOOLEAN:
        return bool(left) is bool(right)
    if mode == ComparisonMode.CONTAINS:
        return str(left) in str(right) or str(right) in str(left)
    return str(left).casefold() == str(right).casefold()


def _catalog_availability(
    product: CatalogProductSnapshot,
) -> CatalogAvailabilityStatus:
    """Conservatively normalize the feed's two availability coordinates.

    A numeric balance is stronger than a textual status.  Conflicting
    positive signals are not silently resolved in favour of availability:
    only an unambiguous positive balance/status can satisfy an explicit typed
    stock request.
    """

    raw_status = str(product.stock_status or "").strip().casefold()
    positive_statuses = {
        "in_stock",
        "in stock",
        "available",
        "в наличии",
        "есть в наличии",
    }
    negative_statuses = {
        "out_of_stock",
        "out of stock",
        "unavailable",
        "нет в наличии",
        "отсутствует",
    }
    if product.stock_qty is not None:
        if product.stock_qty <= 0:
            return CatalogAvailabilityStatus.OUT_OF_STOCK
        if raw_status in negative_statuses:
            return CatalogAvailabilityStatus.UNKNOWN
        return CatalogAvailabilityStatus.IN_STOCK
    if raw_status in positive_statuses:
        return CatalogAvailabilityStatus.IN_STOCK
    if raw_status in negative_statuses:
        return CatalogAvailabilityStatus.OUT_OF_STOCK
    return CatalogAvailabilityStatus.UNKNOWN


def _assess_candidate(
    product: CatalogProductSnapshot,
    contract: ProductContract,
    hard: tuple[SearchConstraint, ...],
    soft: tuple[SearchConstraint, ...],
    unavailable_hard: tuple[str, ...] = (),
    *,
    in_stock_required: bool = False,
) -> CandidateAssessment:
    fact_map = {item.name: item for item in product.facts}
    definitions = {item.name: item for item in contract.fact_definitions}
    matched_hard: list[str] = []
    mismatched_hard: list[str] = []
    missing_hard: list[str] = []
    matched_soft: list[str] = []
    mismatched_soft: list[str] = []
    provenance = []
    availability_status = _catalog_availability(product)

    if product.role not in contract.allowed_catalog_roles:
        return CandidateAssessment(
            sku=product.sku,
            product_kind=product.product_kind,
            role=product.role,
            status=CandidateStatus.REJECTED,
            availability_status=availability_status,
            reason_codes=("catalog_role_incompatible",),
        )

    for constraint in hard:
        actual = fact_map.get(constraint.name)
        if actual is None:
            missing_hard.append(constraint.name)
            continue
        provenance.append(actual.provenance)
        definition = definitions.get(constraint.name)
        same = _same_value(
            definition.comparison if definition else ComparisonMode.EXACT,
            constraint.name,
            constraint.value,
            actual.value,
        )
        if constraint.polarity == "excluded":
            same = not same
        (matched_hard if same else mismatched_hard).append(constraint.name)

    for constraint in soft:
        actual = fact_map.get(constraint.name)
        if actual is None:
            mismatched_soft.append(constraint.name)
            continue
        provenance.append(actual.provenance)
        definition = definitions.get(constraint.name)
        same = _same_value(
            definition.comparison if definition else ComparisonMode.EXACT,
            constraint.name,
            constraint.value,
            actual.value,
        )
        if constraint.polarity == "excluded":
            same = not same
        (matched_soft if same else mismatched_soft).append(constraint.name)

    if mismatched_hard:
        status = CandidateStatus.REJECTED
        reasons = ("hard_constraint_mismatch",)
    elif missing_hard:
        status = CandidateStatus.UNVERIFIED
        reasons = ("catalogue_hard_fact_missing",)
    elif unavailable_hard:
        status = CandidateStatus.UNVERIFIED
        reasons = ("required_customer_fact_unavailable",)
    elif len(mismatched_soft) > 1:
        status = CandidateStatus.REJECTED
        reasons = ("more_than_one_soft_relaxation_required",)
    else:
        status = CandidateStatus.ELIGIBLE
        reasons = (
            "one_soft_constraint_relaxed" if mismatched_soft else "strict_contract_match",
        )

    if in_stock_required and status != CandidateStatus.REJECTED:
        if availability_status == CatalogAvailabilityStatus.OUT_OF_STOCK:
            status = CandidateStatus.REJECTED
            reasons = (*reasons, "required_stock_unavailable")
        elif availability_status == CatalogAvailabilityStatus.UNKNOWN:
            status = CandidateStatus.UNVERIFIED
            reasons = (*reasons, "required_stock_not_confirmed")
        else:
            reasons = (*reasons, "required_stock_confirmed")

    relaxations = tuple(
        CatalogRelaxation(
            fact_name=name,
            requested_value=next(x.value for x in soft if x.name == name),
            candidate_value=(fact_map[name].value if name in fact_map else None),
            reason_code="soft_preference_differs",
        )
        for name in mismatched_soft
        if len(mismatched_soft) == 1 and not mismatched_hard and not missing_hard
    )
    return CandidateAssessment(
        sku=product.sku,
        product_kind=product.product_kind,
        role=product.role,
        status=status,
        availability_status=availability_status,
        matched_hard_facts=tuple(matched_hard),
        mismatched_hard_facts=tuple(mismatched_hard),
        missing_hard_facts=tuple(missing_hard),
        matched_soft_facts=tuple(matched_soft),
        mismatched_soft_facts=tuple(mismatched_soft),
        relaxations=relaxations,
        provenance=tuple(dict.fromkeys(provenance)),
        reason_codes=reasons,
    )


def _make_search_plan(
    assessment: TaskReadinessAssessment,
    contract: ProductContract,
    catalog_snapshot: tuple[CatalogProductSnapshot, ...],
    *,
    in_stock_required: bool = False,
) -> CatalogSearchPlan:
    hard, soft = _constraints(assessment)
    hard_names = {item.name for item in hard}
    known_constraint_names = {
        item.name for item in (*hard, *soft)
        if item.value is not None
    }
    unavailable_hard = tuple(
        dict.fromkeys(
            (
                *assessment.unknown_facts,
                *assessment.refused_facts,
                *assessment.deferred_facts,
            )
        )
    )
    required_hard = {
        item.name
        for item in contract.fact_definitions
        if item.required_for_exact and item.strength == FactStrength.HARD
    }
    required_alternatives = dict(contract.required_fact_alternatives)
    available_or_terminal = known_constraint_names | set(unavailable_hard)
    unresolved_required_hard = {
        name
        for name in required_hard
        if name not in available_or_terminal
        and not any(
            alternative in available_or_terminal
            for alternative in required_alternatives.get(name, ())
        )
    }
    if "sku" in hard_names:
        unresolved_required_hard = set()
    search_blocked = (
        assessment.status in {
            ReadinessStatus.NEEDS_DECISION_FACT,
            ReadinessStatus.BLOCKED,
        }
        or bool(unresolved_required_hard)
    )
    compatible_kinds = set(contract.candidate_kinds or (contract.product_kind,))
    pool = tuple(
        product for product in catalog_snapshot
        if product.product_kind in compatible_kinds
    )
    # Missing decision-changing facts are not a licence to return every item
    # of a broad category.  Keep an empty, typed plan for diagnostics and let
    # SellerPolicy ask the selected question.  Explicitly unavailable facts
    # take the preliminary path below and make candidates unverified.
    assessments = (
        ()
        if search_blocked
        else tuple(
            _assess_candidate(
                product,
                contract,
                hard,
                soft,
                unavailable_hard,
                in_stock_required=in_stock_required,
            )
            for product in sorted(pool, key=lambda item: item.sku)
        )
    )
    eligible = tuple(
        item.sku for item in assessments
        if item.status == CandidateStatus.ELIGIBLE and not item.relaxations
    )
    relaxed = tuple(
        item.sku for item in assessments
        if item.status == CandidateStatus.ELIGIBLE and item.relaxations
    )
    unverified = tuple(
        item.sku for item in assessments
        if item.status == CandidateStatus.UNVERIFIED
        and (
            not in_stock_required
            or item.availability_status == CatalogAvailabilityStatus.IN_STOCK
        )
    )

    stages: list[CatalogSearchStage] = []
    if any(item.name == "sku" for item in hard):
        stages.append(CatalogSearchStage.EXACT_IDENTITY)
    if not search_blocked:
        stages.append(CatalogSearchStage.STRICT_SAME_KIND)
        if compatible_kinds != {contract.product_kind}:
            stages.append(CatalogSearchStage.COMPATIBLE_ANALOG)
        if soft:
            stages.append(CatalogSearchStage.RELAX_ONE_SOFT_CONSTRAINT)
        if not eligible and not relaxed and (
            assessment.status != ReadinessStatus.PRELIMINARY_READY or not unverified
        ):
            stages.append(CatalogSearchStage.HONEST_NO_MATCH)

    unavailable = tuple(
        dict.fromkeys(
            (*assessment.missing_decision_facts, *assessment.unknown_facts,
             *assessment.refused_facts, *assessment.deferred_facts)
        )
    )
    reasons = ["deterministic_contract_search_plan"]
    if in_stock_required:
        reasons.append("in_stock_requirement_from_typed_fact")
    if search_blocked:
        reasons.append("catalog_search_blocked_missing_required_hard_facts")
    if unverified:
        reasons.append("some_candidates_cannot_be_verified_from_feed")
    if CatalogSearchStage.HONEST_NO_MATCH in stages and not search_blocked:
        reasons.append("no_verified_contract_match")
    if in_stock_required and not eligible and not relaxed:
        reasons.append("no_verified_in_stock_contract_match")
        if not unverified:
            reasons.append("no_in_stock_contract_candidate")
    return CatalogSearchPlan(
        plan_id=_stable_id("catalog_plan", assessment.task_id, contract.contract_id, hard, soft),
        task_id=assessment.task_id,
        goal_id=assessment.goal_id,
        contract_id=contract.contract_id,
        product_kind=contract.product_kind,
        requested_role=contract.allowed_catalog_roles[0],
        stages=tuple(stages),
        in_stock_required=in_stock_required,
        hard_constraints=hard,
        soft_constraints=soft,
        unavailable_constraints=unavailable,
        candidate_assessments=assessments,
        eligible_skus=eligible,
        relaxed_skus=relaxed,
        unverified_skus=unverified,
        excluded_kind_count=len(catalog_snapshot) - len(pool),
        reason_codes=tuple(reasons),
    )


def _search_plan_signature(
    assessment: TaskReadinessAssessment,
    contract: ProductContract,
    *,
    in_stock_required: bool = False,
) -> tuple[object, ...]:
    """Identity of catalogue work, excluding the act-specific task id."""

    hard, soft = _constraints(assessment)
    return (
        assessment.goal_id or assessment.task_id,
        contract.contract_id,
        assessment.product_kind,
        assessment.status,
        hard,
        soft,
        assessment.missing_decision_facts,
        assessment.unknown_facts,
        assessment.refused_facts,
        assessment.deferred_facts,
        in_stock_required,
    )


def _solution_plan(
    plans: tuple[CatalogSearchPlan, ...],
    assessments: tuple[TaskReadinessAssessment, ...],
) -> SolutionPlan | None:
    plans_by_goal: dict[str, CatalogSearchPlan] = {}
    for plan in plans:
        if plan.goal_id is not None:
            plans_by_goal.setdefault(plan.goal_id, plan)
    component_plans = tuple(plans_by_goal.values())
    if len(component_plans) < 2:
        return None
    unique_tasks = tuple(plan.task_id for plan in component_plans)
    by_task = {item.task_id: item for item in assessments}
    components = tuple(
        SolutionComponent(
            component_id=_stable_id("component", plan.task_id, plan.product_kind.value),
            task_id=plan.task_id,
            product_kind=plan.product_kind,
            role=plan.requested_role,
            constraint_names=tuple(
                item.name for item in (*plan.hard_constraints, *plan.soft_constraints)
            ),
            quantity=None,
            status=(
                "unsupported"
                if by_task[plan.task_id].status == ReadinessStatus.UNSUPPORTED
                else "unverified"
                if not plan.eligible_skus and not plan.relaxed_skus
                else "planned"
            ),
        )
        for plan in component_plans
    )
    return SolutionPlan(
        solution_id=_stable_id("solution", *unique_tasks),
        task_ids=unique_tasks,
        components=components,
        reason_codes=("multi_product_tasks_preserved_as_bom",),
    )


def plan_catalog_search(
    dialogue_state: DialogueStateV2,
    next_action_plan: NextActionPlan,
    readiness_assessments: Iterable[TaskReadinessAssessment],
    catalog_snapshot: Iterable[CatalogProductSnapshot],
    contract_registry: ProductContractRegistry,
    *,
    solution_enabled: bool = False,
    contract_resolutions: Iterable[ContractResolution] = (),
) -> CatalogPlanningResult:
    """Build verifiable candidate groups without ranking or mutating inputs."""

    assessments = tuple(readiness_assessments)
    snapshot = tuple(catalog_snapshot)
    resolutions = tuple(contract_resolutions)
    executable_task_ids = _executable_catalog_task_ids(next_action_plan)
    if not executable_task_ids:
        return CatalogPlanningResult(
            status="skipped",
            contract_resolutions=resolutions,
            readiness_assessments=assessments,
            reason_codes=(
                "next_action_does_not_require_catalogue"
                if next_action_plan.primary.kind not in _CATALOG_ACTIONS
                and (
                    next_action_plan.secondary is None
                    or next_action_plan.secondary.kind not in _CATALOG_ACTIONS
                )
                else "catalog_action_task_scope_missing",
            ),
        )

    plans: list[CatalogSearchPlan] = []
    unsupported: list[str] = []
    seen_searches: set[tuple[object, ...]] = set()
    duplicate_searches = 0
    task_rank = {
        task_id: index for index, task_id in enumerate(executable_task_ids)
    }
    ordered_assessments = tuple(
        item
        for _, item in sorted(
            (
                pair
                for pair in enumerate(assessments)
                if pair[1].task_id in task_rank
            ),
            key=lambda pair: (
                task_rank[pair[1].task_id],
                pair[0],
            ),
        )
    )
    for assessment in ordered_assessments:
        if assessment.status in {ReadinessStatus.UNSUPPORTED, ReadinessStatus.AMBIGUOUS}:
            unsupported.append(assessment.task_id)
            continue
        contract = contract_registry.get(assessment.contract_id)
        if contract is None or assessment.product_kind != contract.product_kind:
            unsupported.append(assessment.task_id)
            continue
        in_stock_required = _requires_in_stock_candidates(
            dialogue_state,
            assessment,
        )
        signature = _search_plan_signature(
            assessment,
            contract,
            in_stock_required=in_stock_required,
        )
        if signature in seen_searches:
            duplicate_searches += 1
            continue
        seen_searches.add(signature)
        plans.append(
            _make_search_plan(
                assessment,
                contract,
                snapshot,
                in_stock_required=in_stock_required,
            )
        )

    plan_tuple = tuple(plans)
    solution = _solution_plan(plan_tuple, assessments) if solution_enabled else None
    candidate_skus = tuple(
        dict.fromkeys(
            sku for plan in plan_tuple
            for sku in (*plan.eligible_skus, *plan.relaxed_skus, *plan.unverified_skus)
        )
    )
    return CatalogPlanningResult(
        status="planned" if plan_tuple or solution else "skipped",
        contract_resolutions=resolutions,
        readiness_assessments=assessments,
        search_plans=plan_tuple,
        solution_plan=solution,
        candidate_skus=candidate_skus,
        unsupported_task_ids=tuple(unsupported),
        reason_codes=(
            "catalog_planner_v2_completed",
            *(("solution_plan_created",) if solution else ()),
            *(("unsupported_tasks_skipped",) if unsupported else ()),
            *(("duplicate_catalog_search_plans_deduplicated",) if duplicate_searches else ()),
        ),
    )
