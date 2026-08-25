from __future__ import annotations

from copy import deepcopy

from app.answer_v2.contracts import (
    AnswerSourceSnapshot,
    CatalogAnswerProduct,
    ClaimKind,
    ProductPresentationStatus,
)
from app.answer_v2.planner import build_answer_plan
from app.answer_v2.renderer import deterministic_render
from app.answer_v2.sources import attach_turn_source_evidence
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import (
    CandidateAssessment,
    CandidateStatus,
    CatalogFact,
    CatalogPlanningResult,
    CatalogProductRole,
    CatalogRelaxation,
    CatalogSearchPlan,
    CatalogSearchStage,
    FactProvenance,
    ProductKind,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.commerce_v2.contracts import (
    CapabilityMode,
    CommerceExecutionStatus,
    CommercePlanningResult,
    CommerceWorkflowKind,
    CommerceWorkflowState,
    CommerceWorkflowStatus,
)
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintStatus,
    CustomerTask,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ProductCategory,
    ProductGoal,
    ProductRole,
    TaskAct,
    TaskStack,
    TaskStatus,
)


def _state(
    *,
    act: TaskAct = TaskAct.CHECK_PRICE,
    constraint_status: ConstraintStatus | None = None,
) -> DialogueStateV2:
    goal = ProductGoal(
        goal_id="goal-pipe",
        canonical_type="труба",
        category=ProductCategory.PIPES,
        role=ProductRole.TARGET,
        evidence="труба",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    task = CustomerTask(
        task_id="task-pipe",
        act=act,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    constraints = ()
    if constraint_status is not None:
        constraints = (
            ConstraintFactV2(
                fact_id="fact-diameter",
                name="diameter_mm",
                value=25 if constraint_status == ConstraintStatus.KNOWN else None,
                unit="mm" if constraint_status == ConstraintStatus.KNOWN else None,
                status=constraint_status,
                evidence="diameter",
                source="test",
                confidence=1.0,
                goal_id=goal.goal_id,
                task_id=task.task_id,
                source_turn=1,
            ),
        )
    return DialogueStateV2(
        turn_number=1,
        task_stack=TaskStack(active_task_id=task.task_id),
        tasks=(task,),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
        constraints=constraints,
    )


def _sources(
    catalog: CatalogPlanningResult | None = None,
    commerce: CommercePlanningResult | None = None,
    state: DialogueStateV2 | None = None,
) -> AnswerSourceSnapshot:
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    snapshot = AnswerSourceSnapshot(
        source_revision="fixture-v1",
        products=(
            CatalogAnswerProduct(
                sku="PIPE-25",
                name="Труба PPR 25 мм",
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                price=250.0,
                currency="RUB",
                stock_status="в наличии",
                stock_qty=7,
                url="https://example.test/pipe-25",
                facts=(
                    CatalogFact(
                        name="diameter_mm",
                        value=25,
                        unit="mm",
                        provenance=provenance,
                    ),
                ),
            ),
        ),
    )
    return attach_turn_source_evidence(
        snapshot,
        catalog if catalog is not None else _catalog(),
        commerce,
        state if state is not None else _state(),
    )


def _catalog(
    *,
    candidate_status: CandidateStatus = CandidateStatus.ELIGIBLE,
    missing_hard: tuple[str, ...] = (),
    relaxation: bool = False,
) -> CatalogPlanningResult:
    relaxations = (
        (
            CatalogRelaxation(
                fact_name="colour",
                requested_value="white",
                candidate_value="grey",
                reason_code="one_soft_constraint_relaxed",
            ),
        )
        if relaxation
        else ()
    )
    candidate = CandidateAssessment(
        sku="PIPE-25",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        status=candidate_status,
        matched_hard_facts=("diameter_mm",) if not missing_hard else (),
        missing_hard_facts=missing_hard,
        mismatched_soft_facts=("colour",) if relaxation else (),
        relaxations=relaxations,
        reason_codes=("fixture_candidate",),
    )
    readiness = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        status=(
            ReadinessStatus.PRELIMINARY_READY
            if candidate_status == CandidateStatus.UNVERIFIED
            else ReadinessStatus.EXACT_READY
        ),
    )
    search = CatalogSearchPlan(
        plan_id="search-pipe",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.STRICT_SAME_KIND,),
        candidate_assessments=(candidate,),
        eligible_skus=("PIPE-25",) if candidate_status == CandidateStatus.ELIGIBLE else (),
        relaxed_skus=("PIPE-25",) if relaxation else (),
        unverified_skus=("PIPE-25",) if candidate_status == CandidateStatus.UNVERIFIED else (),
    )
    return CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
        candidate_skus=("PIPE-25",),
    )


def _policy(
    kind: NextActionKind = NextActionKind.ANSWER_DIRECT_QUESTION,
    *,
    fact_name: str | None = None,
) -> NextActionPlan:
    return NextActionPlan(
        primary=NextAction(
            kind=kind,
            task_id="task-pipe",
            fact_name=fact_name,
            reason_code="fixture_policy",
        ),
        task_ids=("task-pipe",),
    )


def _compile(**kwargs):
    state = kwargs.get("state", _state())
    catalog = kwargs.get("catalog", _catalog())
    commerce = kwargs.get("commerce")
    return build_answer_plan(
        state,
        kwargs.get("policy", _policy()),
        catalog,
        commerce,
        kwargs.get("sources", _sources(catalog, commerce, state)),
        turn_id=kwargs.get("turn_id", "turn-answer"),
    )


def test_direct_price_answer_is_first_and_every_claim_has_provenance() -> None:
    result = _compile()
    plan = result.answer_plan
    assert plan is not None
    assert plan.sections[0].kind.value == "direct_answer"
    assert any(item.kind == ClaimKind.PRICE for item in plan.claims)
    assert all(item.source_ref_ids for item in plan.claims if item.allowed_in_response)
    assert plan.question is None
    assert plan.next_step is not None


def test_unverified_candidate_and_missing_feed_fact_stay_explicit() -> None:
    result = _compile(
        catalog=_catalog(
            candidate_status=CandidateStatus.UNVERIFIED,
            missing_hard=("pressure_bar",),
        )
    )
    plan = result.answer_plan
    assert plan is not None
    assert plan.products[0].status == ProductPresentationStatus.UNVERIFIED
    assert any(
        item.fact_name == "pressure_bar" and item.status.value == "catalogue_missing"
        for item in plan.limitations
    )


def test_analog_preserves_machine_readable_difference() -> None:
    plan = _compile(catalog=_catalog(relaxation=True)).answer_plan
    assert plan is not None
    assert plan.products[0].status == ProductPresentationStatus.ANALOG
    assert len(plan.analog_differences) == 1
    assert plan.analog_differences[0].requested_value == "white"
    assert plan.analog_differences[0].candidate_value == "grey"


def test_unknown_refused_and_deferred_never_become_values_or_questions() -> None:
    for status in (
        ConstraintStatus.UNKNOWN,
        ConstraintStatus.REFUSED,
        ConstraintStatus.DEFERRED,
    ):
        plan = _compile(
            state=_state(act=TaskAct.SELECT, constraint_status=status),
            policy=_policy(
                NextActionKind.ASK_DECISION_CHANGING_QUESTION,
                fact_name="diameter_mm",
            ),
        ).answer_plan
        assert plan is not None
        assert plan.question is None
        assert any(item.fact_name == "diameter_mm" for item in plan.limitations)
        assert not [
            item
            for item in plan.claims
            if item.kind == ClaimKind.CUSTOMER_CONSTRAINT
            and item.predicate == "diameter_mm"
            and item.value is not None
        ]


def test_deterministic_render_is_grounded_and_has_one_next_step() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    validation = validate_rendered_answer(plan, rendered, _sources())
    assert validation.status == "accepted", validation
    assert len([item for item in rendered.segments if item.kind.value == "next_step"]) == 1


def test_extra_number_sku_link_and_promise_are_rejected() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    additions = (
        " Допустимо 999 мм.",
        " Возьмите SKU XZ-999.",
        " Подробнее https://invented.example/offer.",
        " Заявка отправлена.",
    )
    for suffix in additions:
        first = rendered.segments[0].model_copy(
            update={"text": rendered.segments[0].text + suffix}
        )
        altered = rendered.model_copy(
            update={
                "segments": (first, *rendered.segments[1:]),
                "text": rendered.text + suffix,
            }
        )
        validation = validate_rendered_answer(plan, altered, _sources())
        assert validation.status == "rejected", suffix


def _commerce(status, execution, receipt=None) -> CommercePlanningResult:
    workflow = CommerceWorkflowState(
        workflow_id="workflow-1",
        contract_id="handoff-v1",
        workflow_kind=CommerceWorkflowKind.HANDOFF,
        task_ids=("task-pipe",),
        status=status,
        capability_id="handoff",
        capability_mode=CapabilityMode.TRANSACTIONAL_EXTERNAL,
        execution_status=execution,
        external_receipt_ref=receipt,
        created_turn=1,
        updated_turn=1,
    )
    return CommercePlanningResult(status="planned", workflows=(workflow,))


def test_commerce_prepared_is_not_delivered_and_delivery_requires_receipt() -> None:
    prepared = _compile(
        commerce=_commerce(
            CommerceWorkflowStatus.READY_TO_EXECUTE,
            CommerceExecutionStatus.PREPARED,
        )
    ).answer_plan
    assert prepared is not None
    assert any(
        item.kind == ClaimKind.COMMERCE_STATUS and item.value == "prepared"
        for item in prepared.claims
    )
    assert not any(item.value == "delivered" for item in prepared.claims)

    invalid_delivery = _compile(
        commerce=_commerce(
            CommerceWorkflowStatus.DELIVERED,
            CommerceExecutionStatus.DELIVERED,
        )
    )
    assert any(
        item.reason_code == "delivered_status_without_verified_receipt"
        for item in invalid_delivery.rejected_claims
    )
    assert not any(
        item.value == "delivered"
        for item in invalid_delivery.answer_plan.claims
    )

    delivered = _compile(
        commerce=_commerce(
            CommerceWorkflowStatus.DELIVERED,
            CommerceExecutionStatus.DELIVERED,
            receipt="receipt-verified",
        )
    ).answer_plan
    assert delivered is not None
    claim = next(item for item in delivered.claims if item.value == "delivered")
    assert any(
        source.source_type.value == "commerce_receipt"
        and source.source_ref_id in claim.source_ref_ids
        for source in delivered.sources
    )


def test_compiler_is_deterministic_and_does_not_mutate_inputs() -> None:
    state = _state()
    catalog = _catalog()
    sources = _sources()
    before = deepcopy(
        (
            state.model_dump(mode="json"),
            catalog.model_dump(mode="json"),
            sources.model_dump(mode="json"),
        )
    )
    first = _compile(state=state, catalog=catalog, sources=sources)
    second = _compile(state=state, catalog=catalog, sources=sources)
    assert first == second
    assert before == (
        state.model_dump(mode="json"),
        catalog.model_dump(mode="json"),
        sources.model_dump(mode="json"),
    )
