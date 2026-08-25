"""Pure deterministic Stage 3 catalogue planning and candidate verification."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, NextActionPlan

from .contracts import (
    CandidateAssessment,
    CandidateStatus,
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
from .normalization import normalize_fact_value
from .registry import ProductContractRegistry


_CATALOG_ACTIONS = {
    NextActionKind.SEARCH_EXACT,
    NextActionKind.SHOW_PRELIMINARY_OPTIONS,
    NextActionKind.COMPARE,
    NextActionKind.ANSWER_DIRECT_QUESTION,
}


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
    left = normalize_fact_value(name, requested)
    right = normalize_fact_value(name, actual)
    if mode == ComparisonMode.NUMERIC:
        try:
            return abs(float(left) - float(right)) <= 1e-9
        except (TypeError, ValueError):
            return False
    if mode == ComparisonMode.BOOLEAN:
        return bool(left) is bool(right)
    if mode == ComparisonMode.CONTAINS:
        return str(left) in str(right) or str(right) in str(left)
    return str(left).casefold() == str(right).casefold()


def _assess_candidate(
    product: CatalogProductSnapshot,
    contract: ProductContract,
    hard: tuple[SearchConstraint, ...],
    soft: tuple[SearchConstraint, ...],
    unavailable_hard: tuple[str, ...] = (),
) -> CandidateAssessment:
    fact_map = {item.name: item for item in product.facts}
    definitions = {item.name: item for item in contract.fact_definitions}
    matched_hard: list[str] = []
    mismatched_hard: list[str] = []
    missing_hard: list[str] = []
    matched_soft: list[str] = []
    mismatched_soft: list[str] = []
    provenance = []

    if product.role not in contract.allowed_catalog_roles:
        return CandidateAssessment(
            sku=product.sku,
            product_kind=product.product_kind,
            role=product.role,
            status=CandidateStatus.REJECTED,
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
) -> CatalogSearchPlan:
    hard, soft = _constraints(assessment)
    definitions = {item.name: item for item in contract.fact_definitions}
    unavailable_hard = tuple(
        name
        for name in (
            *assessment.unknown_facts,
            *assessment.refused_facts,
            *assessment.deferred_facts,
        )
        if name in definitions
        and definitions[name].strength == FactStrength.HARD
    )
    compatible_kinds = set(contract.candidate_kinds or (contract.product_kind,))
    pool = tuple(
        product for product in catalog_snapshot
        if product.product_kind in compatible_kinds
    )
    assessments = tuple(
        _assess_candidate(product, contract, hard, soft, unavailable_hard)
        for product in sorted(pool, key=lambda item: item.sku)
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
    )

    stages: list[CatalogSearchStage] = []
    if any(item.name == "sku" for item in hard):
        stages.append(CatalogSearchStage.EXACT_IDENTITY)
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
    if unverified:
        reasons.append("some_candidates_cannot_be_verified_from_feed")
    if CatalogSearchStage.HONEST_NO_MATCH in stages:
        reasons.append("no_verified_contract_match")
    return CatalogSearchPlan(
        plan_id=_stable_id("catalog_plan", assessment.task_id, contract.contract_id, hard, soft),
        task_id=assessment.task_id,
        goal_id=assessment.goal_id,
        contract_id=contract.contract_id,
        product_kind=contract.product_kind,
        requested_role=contract.allowed_catalog_roles[0],
        stages=tuple(stages),
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
    if next_action_plan.primary.kind not in _CATALOG_ACTIONS and (
        next_action_plan.secondary is None
        or next_action_plan.secondary.kind not in _CATALOG_ACTIONS
    ):
        return CatalogPlanningResult(
            status="skipped",
            contract_resolutions=resolutions,
            readiness_assessments=assessments,
            reason_codes=("next_action_does_not_require_catalogue",),
        )

    plans: list[CatalogSearchPlan] = []
    unsupported: list[str] = []
    for assessment in assessments:
        if assessment.status in {ReadinessStatus.UNSUPPORTED, ReadinessStatus.AMBIGUOUS}:
            unsupported.append(assessment.task_id)
            continue
        contract = contract_registry.get(assessment.contract_id)
        if contract is None:
            unsupported.append(assessment.task_id)
            continue
        plans.append(_make_search_plan(assessment, contract, snapshot))

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
        ),
    )
