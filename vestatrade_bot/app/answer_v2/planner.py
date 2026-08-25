"""Pure compiler from typed V2 decisions to a grounded AnswerPlan."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.catalog_v2.contracts import (
    CandidateStatus,
    CatalogPlanningResult,
    ReadinessStatus,
)
from app.commerce_v2.contracts import (
    CommerceExecutionStatus,
    CommercePlanningResult,
    CommerceWorkflowStatus,
)
from app.dialogue_v2.contracts import (
    ConstraintStatus,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ResponseStrategyKind,
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
    ClaimKind,
    KnowledgeStatus,
    LimitationPlan,
    LimitationStatus,
    NextStepKind,
    NextStepPlan,
    ProductPresentationPlan,
    ProductPresentationStatus,
    QuestionPlan,
    RejectedClaim,
    SourceReference,
    SourceType,
)


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


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
    readiness_by_task = {
        item.task_id: item
        for item in (catalog_planning.readiness_assessments if catalog_planning else ())
    }

    def remember(ref: SourceReference) -> SourceReference:
        sources.setdefault(ref.source_ref_id, ref)
        return ref

    for fact in dialogue_state.constraints:
        if not fact.active:
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

                fact_names = set(
                    (*candidate.matched_hard_facts, *candidate.matched_soft_facts)
                )
                for fact in product.facts:
                    if fact.name not in fact_names:
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
                        reason_codes=("catalog_attribute_confirmed",),
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
                if product.stock_qty is not None or (
                    product.stock_status
                    and product.stock_status.casefold() not in {"unknown", "неизвестно"}
                ):
                    stock_ref = remember(
                        _source(
                            SourceType.CATALOG_STOCK,
                            candidate.sku,
                            field_name=("stock_qty" if product.stock_qty is not None else "stock_status"),
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                        )
                    )
                    stock_value: str | int = (
                        product.stock_qty
                        if product.stock_qty is not None
                        else str(product.stock_status)
                    )
                    item = _claim(
                        ClaimKind.STOCK,
                        candidate.sku,
                        "stock_qty" if product.stock_qty is not None else "stock_status",
                        stock_value,
                        (stock_ref,),
                        unit="pcs" if product.stock_qty is not None else None,
                        task_id=search_plan.task_id,
                        goal_id=search_plan.goal_id,
                    )
                    claims.setdefault(item.claim_id, item)
                    product_claim_ids.append(item.claim_id)
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
                            )
                            if item is not None
                        ),
                        reason_codes=candidate.reason_codes,
                    )
                )
                for fact_name in candidate.missing_hard_facts:
                    limitations.append(
                        LimitationPlan(
                            limitation_id=_stable_id(
                                "limit", search_plan.task_id, candidate.sku, fact_name
                            ),
                            status=LimitationStatus.CATALOGUE_MISSING,
                            reason_code="candidate_hard_fact_absent_from_catalogue",
                            task_id=search_plan.task_id,
                            goal_id=search_plan.goal_id,
                            fact_name=fact_name,
                            source_ref_ids=(candidate_ref.source_ref_id,),
                            allowed_strategy_kinds=_strategy_options(
                                LimitationStatus.CATALOGUE_MISSING
                            ),
                        )
                    )
            if "no_verified_contract_match" in search_plan.reason_codes:
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

        if catalog_planning.solution_plan is not None:
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

    if commerce_planning is not None:
        for workflow in commerce_planning.workflows:
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
            assertable = True
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
                        reason_code=f"commerce_{workflow.status.value}",
                        task_id=(workflow.task_ids[0] if workflow.task_ids else None),
                        source_ref_ids=(workflow_ref.source_ref_id,),
                        allowed_strategy_kinds=(
                            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                        ),
                    )
                )
        for boundary in commerce_planning.capability_boundaries:
            workflow_id, _, reason = boundary.partition(":")
            limitations.append(
                LimitationPlan(
                    limitation_id=_stable_id("limit", boundary),
                    status=LimitationStatus.CAPABILITY_BOUNDARY,
                    reason_code=reason or "commerce_capability_boundary",
                    source_ref_ids=tuple(
                        item.source_ref_id
                        for item in sources.values()
                        if item.source_type == SourceType.COMMERCE_WORKFLOW
                        and item.source_id == workflow_id
                    ),
                    allowed_strategy_kinds=(
                        ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                    ),
                )
            )

    for capability in source_snapshot.capability_facts:
        if not capability.confirmed:
            rejected.append(
                RejectedClaim(
                    subject_ref=capability.task_id or "capability",
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
                task_id=capability.task_id,
            )
        )
        item = _claim(
            ClaimKind.CAPABILITY_FACT,
            capability.task_id or capability.fact_id,
            capability.name,
            capability.value,
            (ref,),
            unit=capability.unit,
            task_id=capability.task_id,
            reason_codes=("verified_deterministic_capability_fact",),
        )
        claims.setdefault(item.claim_id, item)

    active_action = next_action_plan.primary
    active_task = tasks.get(active_action.task_id or "")
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
            item.task_id == active_action.task_id
            or (
                active_task is not None
                and active_task.target_goal_id is not None
                and item.goal_id == active_task.target_goal_id
            )
        )
    }
    question = None
    if active_action.kind in {
        NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        NextActionKind.COLLECT_COMMERCE_FACT,
    } and active_action.task_id and active_action.fact_name:
        if active_action.fact_name in terminal_facts:
            rejected.append(
                RejectedClaim(
                    subject_ref=active_action.task_id,
                    predicate=active_action.fact_name,
                    reason_code="question_fact_already_terminal",
                )
            )
        else:
            readiness = readiness_by_task.get(active_action.task_id)
            policy_ref = remember(
                _source(
                    SourceType.POLICY_REASON,
                    active_action.reason_code,
                    field_name=active_action.fact_name,
                    task_id=active_action.task_id,
                    source_turn=dialogue_state.turn_number,
                )
            )
            question = QuestionPlan(
                question_id=_stable_id(
                    "question", active_action.task_id, active_action.fact_name
                ),
                task_id=active_action.task_id,
                fact_name=active_action.fact_name,
                decision_impact_code=active_action.reason_code,
                learn_method_code=(readiness.learn_method_code if readiness else None),
                source_ref_ids=(policy_ref.source_ref_id,),
                reason_codes=(active_action.reason_code,),
            )

    next_step = NextStepPlan(
        next_step_id=_stable_id(
            "next_step",
            active_action.kind.value,
            active_action.task_id,
            active_action.fact_name,
        ),
        kind=_next_step(active_action),
        task_id=active_action.task_id,
        fact_name=active_action.fact_name,
        reason_codes=(active_action.reason_code,),
    )

    direct_claim_ids: list[str] = []
    direct_limitation_ids: list[str] = []
    direct_task = active_task
    direct_kind = {
        TaskAct.CHECK_PRICE: ClaimKind.PRICE,
        TaskAct.CHECK_STOCK: ClaimKind.STOCK,
        TaskAct.GET_LINK: ClaimKind.LINK,
    }.get(direct_task.act if direct_task else None)
    if direct_kind is not None:
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

    if direct_limitation_ids and not direct_claim_ids:
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

    unique_limitations = {
        item.limitation_id: item for item in limitations
    }
    limitations = list(unique_limitations.values())
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
    if nondirect_claims:
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
    if remaining_limitation_ids:
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
            (item.sku, item.status.value, item.difference_ids) for item in products
        ],
        "limitations": sorted(
            (item.status.value, item.fact_name, item.reason_code)
            for item in limitations
        ),
        "question": question.fact_name if question else None,
        "next_step": next_step.kind.value,
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    has_content = bool(claims or products)
    if has_content and limitations:
        status = AnswerPlanStatus.PARTIAL
    elif has_content:
        status = AnswerPlanStatus.READY
    elif limitations:
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
