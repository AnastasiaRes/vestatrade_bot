from __future__ import annotations

from copy import deepcopy

from app.answer_v2.contracts import (
    AnswerSourceSnapshot,
    CatalogAnswerProduct,
    ClaimKind,
    NextStepKind,
    ProductRecommendationRole,
    ProductPresentationStatus,
    RecommendationCriterion,
    VerifiedCapabilityFact,
)
from app.answer_v2.planner import (
    _presentable_candidate_shortlist,
    build_answer_plan,
)
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
    FactStrength,
    FactProvenance,
    ProductKind,
    ReadinessStatus,
    SearchConstraint,
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


def _two_selection_state() -> DialogueStateV2:
    first = _state(act=TaskAct.SELECT)
    first_task = first.tasks[0]
    second_goal = ProductGoal(
        goal_id="goal-boiler",
        canonical_type="boiler",
        category=ProductCategory.BOILERS,
        role=ProductRole.TARGET,
        evidence="boiler",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    second_task = CustomerTask(
        task_id="task-boiler",
        act=TaskAct.SELECT,
        target_goal_id=second_goal.goal_id,
        priority=1,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    return first.model_copy(
        update={
            "task_stack": TaskStack(
                active_task_id=first_task.task_id,
                pending_task_ids=(second_task.task_id,),
            ),
            "tasks": (first_task, second_task),
            "product_goals": (*first.product_goals, second_goal),
        }
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


def test_direct_stock_preserves_one_secondary_decision_question() -> None:
    base_state = _state(act=TaskAct.CHECK_STOCK)
    goal = base_state.product_goals[0].model_copy(
        update={
            "canonical_type": "circulation_pump",
            "category": ProductCategory.PUMPS,
        }
    )
    state = base_state.model_copy(update={"product_goals": (goal,)})
    base_catalog = _catalog()
    readiness = base_catalog.readiness_assessments[0].model_copy(
        update={
            "contract_id": "pump.circulation.v1",
            "product_kind": ProductKind.CIRCULATION_PUMP,
        }
    )
    search = base_catalog.search_plans[0]
    candidate = search.candidate_assessments[0].model_copy(
        update={"product_kind": ProductKind.CIRCULATION_PUMP}
    )
    search = search.model_copy(
        update={
            "contract_id": "pump.circulation.v1",
            "product_kind": ProductKind.CIRCULATION_PUMP,
            "candidate_assessments": (candidate,),
        }
    )
    catalog = base_catalog.model_copy(
        update={
            "readiness_assessments": (readiness,),
            "search_plans": (search,),
        }
    )
    sources = _sources(catalog=catalog, state=state)
    pump = sources.products[0].model_copy(
        update={
            "name": "Циркуляционный насос",
            "product_kind": ProductKind.CIRCULATION_PUMP,
        }
    )
    sources = sources.model_copy(update={"products": (pump,)})
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id="task-pipe",
            reason_code="answer_stock_first",
        ),
        secondary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id="task-pipe",
            fact_name="max_head_m",
            reason_code="head_changes_pump_selection",
        ),
        task_ids=("task-pipe",),
    )

    plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=policy,
    ).answer_plan

    assert plan is not None
    assert plan.primary_action == NextActionKind.ANSWER_DIRECT_QUESTION
    assert plan.secondary_action == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert plan.question is not None
    assert plan.question.fact_name == "max_head_m"
    assert plan.next_step.kind == NextStepKind.ASK_DECISION_FACT
    rendered = deterministic_render(plan)
    assert sum(item.kind.value == "question" for item in rendered.segments) == 1
    assert "максимальный напор" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_question_only_product_plan_is_ready_and_grounded() -> None:
    state = _state(act=TaskAct.SELECT)
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id="task-pipe",
                goal_id="goal-pipe",
                contract_id="pipe.ppr.v1",
                product_kind=ProductKind.PIPE,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                missing_decision_facts=("diameter_mm",),
                recommended_question_fact="diameter_mm",
            ),
        ),
    )
    sources = _sources(catalog=catalog, state=state)
    policy = _policy(
        NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        fact_name="diameter_mm",
    )

    plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=policy,
    ).answer_plan

    assert plan is not None
    assert plan.status.value == "ready"
    assert plan.question is not None
    assert not plan.claims
    assert not plan.products
    rendered = deterministic_render(plan)
    assert rendered.segments[0].kind.value == "question"
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_direct_answer_preserves_secondary_method_explanation_step() -> None:
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id="task-pipe",
            reason_code="answer_price_first",
        ),
        secondary=NextAction(
            kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
            task_id="task-pipe",
            fact_name="diameter_mm",
            reason_code="explain_measurement_method",
        ),
        task_ids=("task-pipe",),
    )

    plan = _compile(policy=policy).answer_plan

    assert plan is not None
    assert plan.secondary_action == NextActionKind.EXPLAIN_TERM_OR_METHOD
    assert plan.question is None
    assert plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    rendered = deterministic_render(plan)
    assert sum(item.kind.value == "next_step" for item in rendered.segments) == 1
    assert "как измерить" in rendered.text
    assert "диаметр присоединения" in rendered.text


def test_primary_explanation_keeps_one_secondary_decision_question() -> None:
    state = _state(act=TaskAct.EXPLAIN)
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
            task_id="task-pipe",
            fact_name="diameter_mm",
            reason_code="explicit_explanation_request",
        ),
        secondary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id="task-pipe",
            fact_name="length_mm",
            reason_code="linked_task_requires_length",
        ),
        task_ids=("task-pipe",),
    )

    plan = _compile(state=state, policy=policy).answer_plan

    assert plan is not None
    assert plan.primary_action == NextActionKind.EXPLAIN_TERM_OR_METHOD
    assert plan.secondary_action == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert plan.question is not None
    assert plan.question.fact_name == "length_mm"
    assert plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.next_step.fact_name == "diameter_mm"
    rendered = deterministic_render(plan)
    assert sum(item.kind.value == "question" for item in rendered.segments) == 1
    assert sum(item.kind.value == "next_step" for item in rendered.segments) == 1


def test_numeric_feed_stock_never_invents_piece_units() -> None:
    result = _compile()
    plan = result.answer_plan
    assert plan is not None
    stock_claims = [item for item in plan.claims if item.kind == ClaimKind.STOCK]
    stock = next(item for item in stock_claims if item.predicate == "stock_qty")
    status = next(item for item in stock_claims if item.predicate == "stock_status")
    assert stock.unit is None
    assert status.value == "в наличии"
    rendered = deterministic_render(plan)
    assert "единица складского учёта в фиде не указана" in rendered.text
    assert "наличие — в наличии" in rendered.text
    assert "pcs" not in rendered.text


def test_machine_stock_status_is_localized_in_public_copy() -> None:
    catalog = _catalog()
    sources = _sources(catalog=catalog)
    product = sources.products[0].model_copy(
        update={"stock_qty": None, "stock_status": "in_stock"}
    )
    sources = sources.model_copy(update={"products": (product,)})
    plan = _compile(catalog=catalog, sources=sources).answer_plan

    assert plan is not None
    rendered = deterministic_render(plan)
    assert "наличие — в наличии" in rendered.text
    assert "in_stock" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_verified_site_is_offered_only_for_typed_commerce_task() -> None:
    state = _state(act=TaskAct.CHECK_DELIVERY)
    policy = _policy(NextActionKind.STATE_COMMERCE_CAPABILITY_BOUNDARY)
    sources = AnswerSourceSnapshot(
        source_revision="business-v1",
        capability_facts=(
            VerifiedCapabilityFact(
                fact_id="business-site",
                name="site_url",
                value="https://www.vestatrade.ru",
                source="data/business_config.json",
                source_revision="2026-08-24",
            ),
        ),
    )

    plan = _compile(
        state=state,
        policy=policy,
        catalog=None,
        sources=sources,
    ).answer_plan

    assert plan is not None
    rendered = deterministic_render(plan)
    assert "https://www.vestatrade.ru" in rendered.text
    assert "официальном сайте" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"

    technical_state = _state(act=TaskAct.SELECT)
    technical = _compile(
        state=technical_state,
        policy=_policy(NextActionKind.SHOW_PRELIMINARY_OPTIONS),
        catalog=None,
        sources=sources,
    ).answer_plan
    assert technical is not None
    assert "vestatrade" not in deterministic_render(technical).text


def test_handoff_without_live_workflow_states_honest_capability_boundary() -> None:
    state = _state(act=TaskAct.HANDOFF)
    policy = _policy(NextActionKind.START_OR_CONTINUE_HANDOFF)
    policy = policy.model_copy(
        update={
            "primary": policy.primary.model_copy(
                update={"reason_code": "explicit_handoff_request"}
            )
        }
    )
    sources = AnswerSourceSnapshot(
        source_revision="business-v1",
        capability_facts=(
            VerifiedCapabilityFact(
                fact_id="business-site",
                name="site_url",
                value="https://www.vestatrade.ru",
                source="data/business_config.json",
                source_revision="2026-08-24",
            ),
        ),
    )

    plan = _compile(
        state=state,
        policy=policy,
        catalog=None,
        sources=sources,
    ).answer_plan

    assert plan is not None
    rendered = deterministic_render(plan)
    assert "не могу напрямую передать этот чат" in rendered.text
    assert "https://www.vestatrade.ru" in rendered.text
    assert "Заявка отправлена" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_handoff_without_verified_site_does_not_refer_to_link_above() -> None:
    state = _state(act=TaskAct.HANDOFF)
    policy = _policy(NextActionKind.START_OR_CONTINUE_HANDOFF)
    policy = policy.model_copy(
        update={
            "primary": policy.primary.model_copy(
                update={"reason_code": "explicit_handoff_request"}
            )
        }
    )
    sources = AnswerSourceSnapshot(source_revision="no-business-site")

    plan = _compile(
        state=state,
        policy=policy,
        catalog=None,
        sources=sources,
    ).answer_plan

    assert plan is not None
    rendered = deterministic_render(plan)
    assert "не могу напрямую передать этот чат" in rendered.text
    assert "сайте выше" not in rendered.text
    assert "официальную ссылку" not in rendered.text
    assert "нет контакта" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


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
    assert plan.products[0].missing_hard_facts == ("pressure_bar",)
    assert not any(
        item.reason_code == "candidate_hard_fact_absent_from_catalogue"
        for item in plan.limitations
    )
    rendered = deterministic_render(plan)
    product_line = next(
        item.text for item in rendered.segments
        if item.kind.value == "product"
    )
    assert "уточняемая характеристика" in product_line
    assert "по фиду не подтверждены" in product_line
    assert "По параметру" not in rendered.text


def test_missing_feed_facts_remain_scoped_to_each_candidate_card() -> None:
    first = CandidateAssessment(
        sku="PIPE-A",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        status=CandidateStatus.UNVERIFIED,
        missing_hard_facts=("operating_pressure_bar",),
        reason_codes=("catalogue_hard_fact_missing",),
    )
    second = CandidateAssessment(
        sku="PIPE-B",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        status=CandidateStatus.UNVERIFIED,
        missing_hard_facts=("length_mm",),
        reason_codes=("catalogue_hard_fact_missing",),
    )
    search = CatalogSearchPlan(
        plan_id="candidate-scoped-missing",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe.ppr.v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.STRICT_SAME_KIND,),
        candidate_assessments=(first, second),
        unverified_skus=(first.sku, second.sku),
    )
    readiness = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe.ppr.v1",
        product_kind=ProductKind.PIPE,
        status=ReadinessStatus.EXACT_READY,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
        candidate_skus=(first.sku, second.sku),
    )
    source_snapshot = attach_turn_source_evidence(
        AnswerSourceSnapshot(
            source_revision="candidate-scoped-v1",
            products=(
                CatalogAnswerProduct(
                    sku=first.sku,
                    name="Pipe A",
                    product_kind=ProductKind.PIPE,
                    role=CatalogProductRole.BASE_PRODUCT,
                    stock_status="в наличии",
                    stock_qty=1,
                ),
                CatalogAnswerProduct(
                    sku=second.sku,
                    name="Pipe B",
                    product_kind=ProductKind.PIPE,
                    role=CatalogProductRole.BASE_PRODUCT,
                    stock_status="в наличии",
                    stock_qty=2,
                ),
            ),
        ),
        catalog,
        None,
        _state(act=TaskAct.SELECT),
    )
    result = build_answer_plan(
        _state(act=TaskAct.SELECT),
        _policy(NextActionKind.SHOW_PRELIMINARY_OPTIONS),
        catalog,
        None,
        source_snapshot,
        turn_id="candidate-scoped-missing",
    )
    assert result.answer_plan is not None
    rendered = deterministic_render(result.answer_plan)
    lines = {
        product.sku: next(
            segment.text
            for segment in rendered.segments
            if segment.kind.value == "product" and product.sku in segment.text
        )
        for product in result.answer_plan.products
    }

    assert "рабочее давление" in lines["PIPE-A"]
    assert "длина" not in lines["PIPE-A"]
    assert "длина" in lines["PIPE-B"]
    assert "рабочее давление" not in lines["PIPE-B"]


def test_stock_no_match_is_rendered_without_unavailable_product_cards() -> None:
    search = CatalogSearchPlan(
        plan_id="stock-no-match",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe.ppr.v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.HONEST_NO_MATCH,),
        in_stock_required=True,
        reason_codes=(
            "in_stock_requirement_from_typed_task",
            "no_verified_in_stock_contract_match",
            "no_in_stock_contract_candidate",
        ),
    )
    readiness = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe.ppr.v1",
        product_kind=ProductKind.PIPE,
        status=ReadinessStatus.EXACT_READY,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
    )
    state = _state(act=TaskAct.CHECK_STOCK)
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(source_revision="stock-no-match-v1"),
        catalog,
        None,
        state,
    )
    result = build_answer_plan(
        state,
        _policy(NextActionKind.ANSWER_DIRECT_QUESTION),
        catalog,
        None,
        sources,
        turn_id="stock-no-match",
    )

    assert result.answer_plan is not None
    assert result.answer_plan.products == ()
    rendered = deterministic_render(result.answer_plan)
    text = rendered.text
    assert "Среди товаров с подтверждённым наличием не найден вариант" in text
    assert "без подтверждённого остатка" in text
    assert "По параметру значение нельзя подтвердить" not in text
    no_match = next(
        item
        for item in result.answer_plan.limitations
        if item.reason_code == "no_verified_in_stock_contract_match"
    )
    redundant_source_boundary = next(
        item
        for item in result.answer_plan.limitations
        if item.reason_code == "verified_stock_source_missing"
    )
    no_match_segment = next(
        item
        for item in rendered.segments
        if no_match.limitation_id in item.source_ids
    )
    assert redundant_source_boundary.limitation_id in no_match_segment.source_ids
    validation = validate_rendered_answer(result.answer_plan, rendered, sources)
    assert validation.status == "accepted", validation


def test_36_preliminary_candidates_are_compacted_without_false_exact_claims() -> None:
    base_state = _state(act=TaskAct.SELECT)
    goal = base_state.product_goals[0].model_copy(
        update={
            "canonical_type": "circulation_pump",
            "category": ProductCategory.PUMPS,
        }
    )
    task = base_state.tasks[0]
    constraints = (
        ConstraintFactV2(
            fact_id="fact-diameter",
            name="connection_diameter_mm",
            value=25,
            unit="mm",
            source="test",
            confidence=1.0,
            goal_id=goal.goal_id,
            task_id=task.task_id,
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-head",
            name="max_head_m",
            value=6,
            unit="m",
            source="test",
            confidence=1.0,
            goal_id=goal.goal_id,
            task_id=task.task_id,
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-mounting",
            name="mounting_length_mm",
            status=ConstraintStatus.UNKNOWN,
            source="test",
            confidence=1.0,
            goal_id=goal.goal_id,
            task_id=task.task_id,
            source_turn=1,
        ),
    )
    state = base_state.model_copy(
        update={"product_goals": (goal,), "constraints": constraints}
    )
    provenance = FactProvenance(
        source="attribute",
        source_field="fixture",
        raw_value="fixture",
        parser="test",
    )
    candidates = []
    products = []
    for index in range(36):
        sku = f"PUMP-{index:02d}"
        matches_every_known_hard_fact = index < 23
        candidates.append(
            CandidateAssessment(
                sku=sku,
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                status=CandidateStatus.UNVERIFIED,
                matched_hard_facts=(
                    ("connection_diameter_mm", "max_head_m")
                    if matches_every_known_hard_fact
                    else ("connection_diameter_mm",)
                ),
                missing_hard_facts=(
                    () if matches_every_known_hard_fact else ("max_head_m",)
                ),
                reason_codes=(
                    ("required_customer_fact_unavailable",)
                    if matches_every_known_hard_fact
                    else ("catalogue_hard_fact_missing",)
                ),
            )
        )
        facts = [
            CatalogFact(
                name="connection_diameter_mm",
                value=25,
                unit="mm",
                provenance=provenance,
            ),
            CatalogFact(
                name="mounting_length_mm",
                value=(130 if index == 17 else 130 + index),
                unit="mm",
                provenance=FactProvenance(
                    source="attribute",
                    source_field="mounting_length",
                    raw_value=("130-180" if index == 17 else str(130 + index)),
                    parser="structured_attribute",
                ),
            ),
        ]
        if matches_every_known_hard_fact:
            facts.append(
                CatalogFact(
                    name="max_head_m",
                    value=6,
                    unit="m",
                    provenance=provenance,
                )
            )
        stock_qty = 1 if index >= 17 else 0
        products.append(
            CatalogAnswerProduct(
                sku=sku,
                name=f"Pump option {index:02d}",
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                price=1000.0 + index,
                currency="RUB",
                stock_status=("in_stock" if stock_qty else "out_of_stock"),
                stock_qty=stock_qty,
                url=f"https://example.test/{sku.casefold()}",
                facts=tuple(facts),
            )
        )
    candidate_tuple = tuple(candidates)
    search = CatalogSearchPlan(
        plan_id="search-pump-preliminary",
        task_id=task.task_id,
        goal_id=goal.goal_id,
        contract_id="circulation-pump-v1",
        product_kind=ProductKind.CIRCULATION_PUMP,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.STRICT_SAME_KIND,),
        hard_constraints=(
            SearchConstraint(
                name="connection_diameter_mm",
                value=25,
                unit="mm",
                strength=FactStrength.HARD,
            ),
            SearchConstraint(
                name="max_head_m",
                value=6,
                unit="m",
                strength=FactStrength.HARD,
            ),
        ),
        unavailable_constraints=("mounting_length_mm",),
        candidate_assessments=tuple(reversed(candidate_tuple)),
        unverified_skus=tuple(item.sku for item in candidate_tuple),
    )
    readiness = TaskReadinessAssessment(
        task_id=task.task_id,
        goal_id=goal.goal_id,
        contract_id="circulation-pump-v1",
        product_kind=ProductKind.CIRCULATION_PUMP,
        status=ReadinessStatus.PRELIMINARY_READY,
        unknown_facts=("mounting_length_mm",),
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
        candidate_skus=tuple(item.sku for item in candidate_tuple),
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(
            source_revision="fixture-36-v1",
            products=tuple(products),
        ),
        catalog,
        None,
        state,
    )
    result = build_answer_plan(
        state,
        _policy(NextActionKind.SHOW_PRELIMINARY_OPTIONS),
        catalog,
        None,
        sources,
        turn_id="turn-36-preliminary",
    )

    plan = result.answer_plan
    assert plan is not None
    assert [item.sku for item in plan.products] == [
        "PUMP-17",
        "PUMP-18",
        "PUMP-19",
        "PUMP-20",
        "PUMP-21",
    ]
    assert len(plan.products) == 5
    assert all(
        item.status == ProductPresentationStatus.UNVERIFIED
        for item in plan.products
    )
    assert all(not item.missing_hard_facts for item in plan.products)
    mounting_claims = [
        item
        for item in plan.claims
        if item.kind == ClaimKind.PRODUCT_ATTRIBUTE
        and item.predicate == "mounting_length_mm"
    ]
    assert {item.subject_ref: item.value for item in mounting_claims} == {
        "PUMP-18": 148,
        "PUMP-19": 149,
        "PUMP-20": 150,
        "PUMP-21": 151,
    }
    assert all(
        "catalog_attribute_confirmed_for_unavailable_customer_fact"
        in item.reason_codes
        for item in mounting_claims
    )
    assert all(
        "mounting_length_mm" not in item.matched_hard_facts
        for item in plan.products
    )
    assert any(
        item.subject_ref == "PUMP-17"
        and item.predicate == "mounting_length_mm"
        and item.reason_code
        == "catalog_attribute_ambiguous_provenance_not_displayed"
        for item in result.rejected_claims
    )
    assert len(catalog.search_plans[0].candidate_assessments) == 36
    assert len(sources.catalog_candidates) == 36
    assert "presentable_candidate_shortlist_applied" in plan.reason_codes
    assert sum(
        item.reason_code == "candidate_not_in_presentable_shortlist"
        for item in result.rejected_claims
    ) == 31
    assert any(
        item.fact_name == "mounting_length_mm"
        and item.status.value == "unknown"
        for item in plan.limitations
    )

    rendered = deterministic_render(plan)
    assert len(rendered.text) < 12_000
    assert "предварительные варианты" in rendered.text
    rendered_positions = [
        rendered.text.index(f"Pump option {index:02d}")
        for index in range(17, 22)
    ]
    assert rendered_positions == sorted(rendered_positions)
    disclaimer = "не буду подставлять значение без подтверждения"
    assert disclaimer in rendered.text
    assert "монтажная длина — 130 мм" not in rendered.text
    assert "монтажная длина — 148 мм" in rendered.text
    assert "монтажная длина — 151 мм" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_analog_preserves_machine_readable_difference() -> None:
    catalog = _catalog(relaxation=True)
    sources = _sources(catalog=catalog)
    plan = _compile(catalog=catalog, sources=sources).answer_plan
    assert plan is not None
    assert plan.products[0].status == ProductPresentationStatus.ANALOG
    assert len(plan.analog_differences) == 1
    assert plan.analog_differences[0].requested_value == "white"
    assert plan.analog_differences[0].candidate_value == "grey"
    rendered = deterministic_render(plan)
    assert "цвет" in rendered.text
    assert "белый" in rendered.text
    assert "серый" in rendered.text
    assert "white" not in rendered.text
    assert "grey" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_honest_catalog_no_match_does_not_claim_that_selection_completed() -> None:
    state = _state(act=TaskAct.SELECT, constraint_status=ConstraintStatus.KNOWN)
    catalog = _catalog()
    search = catalog.search_plans[0].model_copy(
        update={
            "candidate_assessments": (),
            "eligible_skus": (),
            "unverified_skus": (),
            "relaxed_skus": (),
            "reason_codes": ("no_verified_contract_match",),
        }
    )
    catalog = catalog.model_copy(
        update={"search_plans": (search,), "candidate_skus": ()}
    )
    sources = _sources(catalog=catalog, state=state)

    result = build_answer_plan(
        state,
        _policy(NextActionKind.SEARCH_EXACT),
        catalog,
        None,
        sources,
        turn_id="turn-honest-no-match",
    )

    plan = result.answer_plan
    assert plan is not None
    assert not plan.products
    assert plan.next_step.kind == NextStepKind.STATE_CAPABILITY_BOUNDARY
    rendered = deterministic_render(plan)
    assert "нет товара" in rendered.text
    assert "Обязательные параметры я не ослаблял" in rendered.text
    assert "Повторный поиск по тем же подтверждённым требованиям" in rendered.text
    assert "какое одно обязательное требование допустимо изменить" in rendered.text
    assert "Подбор выполнен" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_no_verified_match_with_unverified_cards_stays_preliminary() -> None:
    state = _state(act=TaskAct.SELECT, constraint_status=ConstraintStatus.KNOWN)
    catalog = _catalog(candidate_status=CandidateStatus.UNVERIFIED)
    search = catalog.search_plans[0].model_copy(
        update={
            "reason_codes": (
                "some_candidates_cannot_be_verified_from_feed",
                "no_verified_contract_match",
            ),
        }
    )
    catalog = catalog.model_copy(update={"search_plans": (search,)})
    sources = _sources(catalog=catalog, state=state)

    plan = build_answer_plan(
        state,
        _policy(NextActionKind.SEARCH_EXACT),
        catalog,
        None,
        sources,
        turn_id="turn-unverified-no-match",
    ).answer_plan

    assert plan is not None
    assert plan.products
    assert all(
        item.status == ProductPresentationStatus.UNVERIFIED
        for item in plan.products
    )
    assert plan.next_step.kind == NextStepKind.SHOW_PRELIMINARY_OPTIONS
    assert plan.next_step.reason_codes == (
        "no_verified_match_preliminary_candidates_only",
    )
    rendered = deterministic_render(plan)
    assert "предварительные варианты" in rendered.text
    assert "Подбор выполнен" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_validator_rejects_exact_product_combined_with_strict_no_match() -> None:
    state = _state(act=TaskAct.SELECT, constraint_status=ConstraintStatus.KNOWN)
    catalog = _catalog()
    sources = _sources(catalog=catalog, state=state)
    exact_plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=_policy(NextActionKind.SEARCH_EXACT),
    ).answer_plan
    assert exact_plan is not None
    assert exact_plan.products[0].status == ProductPresentationStatus.EXACT

    no_match_search = catalog.search_plans[0].model_copy(
        update={
            "candidate_assessments": (),
            "eligible_skus": (),
            "unverified_skus": (),
            "relaxed_skus": (),
            "reason_codes": ("no_verified_contract_match",),
        }
    )
    no_match_catalog = catalog.model_copy(
        update={"search_plans": (no_match_search,), "candidate_skus": ()}
    )
    no_match_sources = _sources(catalog=no_match_catalog, state=state)
    no_match_plan = build_answer_plan(
        state,
        _policy(NextActionKind.SEARCH_EXACT),
        no_match_catalog,
        None,
        no_match_sources,
        turn_id="turn-no-match-limitation-source",
    ).answer_plan
    assert no_match_plan is not None

    contradictory = exact_plan.model_copy(
        update={"limitations": no_match_plan.limitations}
    )
    validation = validate_rendered_answer(
        contradictory,
        deterministic_render(contradictory),
        sources,
    )

    assert validation.status == "rejected"
    assert {
        item.code for item in validation.violations
    } >= {"catalog_no_match_contradicts_verified_product"}


def test_first_task_no_match_keeps_second_task_decision_question_deliverable() -> None:
    state = _two_selection_state()
    first_task, second_task = state.tasks
    catalog = _catalog()
    first_readiness = catalog.readiness_assessments[0].model_copy(
        update={
            "task_id": first_task.task_id,
            "goal_id": first_task.target_goal_id,
            "status": ReadinessStatus.EXACT_READY,
        }
    )
    second_readiness = TaskReadinessAssessment(
        task_id=second_task.task_id,
        goal_id=second_task.target_goal_id,
        contract_id="boiler.generic.v1",
        product_kind=ProductKind.BOILER,
        status=ReadinessStatus.NEEDS_DECISION_FACT,
        missing_decision_facts=("power_kw",),
        recommended_question_fact="power_kw",
        learn_method_code="read_boiler_nameplate",
    )
    no_match = catalog.search_plans[0].model_copy(
        update={
            "task_id": first_task.task_id,
            "goal_id": first_task.target_goal_id,
            "candidate_assessments": (),
            "eligible_skus": (),
            "unverified_skus": (),
            "relaxed_skus": (),
            "reason_codes": ("no_verified_contract_match",),
        }
    )
    catalog = catalog.model_copy(
        update={
            "readiness_assessments": (first_readiness, second_readiness),
            "search_plans": (no_match,),
            "candidate_skus": (),
        }
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.SEARCH_EXACT,
            task_id=first_task.task_id,
            reason_code="search_first_product",
        ),
        secondary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id=second_task.task_id,
            fact_name="power_kw",
            reason_code="second_product_requires_power",
        ),
        task_ids=(first_task.task_id, second_task.task_id),
    )
    sources = _sources(catalog=catalog, state=state)

    plan = build_answer_plan(
        state,
        policy,
        catalog,
        None,
        sources,
        turn_id="turn-two-products-no-match",
    ).answer_plan

    assert plan is not None
    assert plan.primary_action == NextActionKind.SEARCH_EXACT
    assert plan.secondary_action == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert plan.task_ids == (first_task.task_id, second_task.task_id)
    assert not plan.products
    assert any(
        item.task_id == first_task.task_id
        and item.reason_code == "no_verified_contract_match"
        for item in plan.limitations
    )
    assert plan.question is not None
    assert plan.question.task_id == second_task.task_id
    assert plan.question.fact_name == "power_kw"
    assert plan.next_step.kind == NextStepKind.ASK_DECISION_FACT
    assert plan.next_step.task_id == second_task.task_id
    rendered = deterministic_render(plan)
    assert sum(item.kind.value == "question" for item in rendered.segments) == 1
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_first_preliminary_task_keeps_second_task_learn_method_step() -> None:
    state = _two_selection_state()
    first_task, second_task = state.tasks
    catalog = _catalog(candidate_status=CandidateStatus.UNVERIFIED)
    first_readiness = catalog.readiness_assessments[0].model_copy(
        update={
            "task_id": first_task.task_id,
            "goal_id": first_task.target_goal_id,
            "status": ReadinessStatus.PRELIMINARY_READY,
        }
    )
    second_readiness = TaskReadinessAssessment(
        task_id=second_task.task_id,
        goal_id=second_task.target_goal_id,
        contract_id="boiler.generic.v1",
        product_kind=ProductKind.BOILER,
        status=ReadinessStatus.NEEDS_DECISION_FACT,
        missing_decision_facts=("power_kw",),
        recommended_question_fact="power_kw",
        learn_method_code="read_boiler_nameplate",
    )
    first_search = catalog.search_plans[0].model_copy(
        update={
            "task_id": first_task.task_id,
            "goal_id": first_task.target_goal_id,
        }
    )
    catalog = catalog.model_copy(
        update={
            "readiness_assessments": (first_readiness, second_readiness),
            "search_plans": (first_search,),
        }
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            task_id=first_task.task_id,
            reason_code="first_product_preliminary",
        ),
        secondary=NextAction(
            kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
            task_id=second_task.task_id,
            fact_name="power_kw",
            reason_code="explain_second_product_fact",
        ),
        task_ids=(first_task.task_id, second_task.task_id),
    )
    sources = _sources(catalog=catalog, state=state)

    plan = build_answer_plan(
        state,
        policy,
        catalog,
        None,
        sources,
        turn_id="turn-two-products-preliminary",
    ).answer_plan

    assert plan is not None
    assert plan.primary_action == NextActionKind.SHOW_PRELIMINARY_OPTIONS
    assert plan.secondary_action == NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.task_ids == (first_task.task_id, second_task.task_id)
    assert plan.products
    assert {item.task_id for item in plan.products} == {first_task.task_id}
    assert plan.question is None
    assert plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.next_step.task_id == second_task.task_id
    assert plan.next_step.fact_name == "power_kw"
    rendered = deterministic_render(plan)
    assert sum(item.kind.value == "next_step" for item in rendered.segments) == 1
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_confirmed_quantity_and_destination_remain_visible_request_coordinates() -> None:
    state = _state(act=TaskAct.CHECK_STOCK)
    state = state.model_copy(
        update={
            "constraints": (
                ConstraintFactV2(
                    fact_id="fact-quantity",
                    name="requested_quantity_m",
                    value=800,
                    unit="m",
                    evidence="800 метров",
                    source="test",
                    confidence=1.0,
                    goal_id="goal-pipe",
                    task_id="task-pipe",
                    source_turn=1,
                ),
                ConstraintFactV2(
                    fact_id="fact-destination",
                    name="destination_region",
                    value="Казахстан",
                    evidence="в Казахстане",
                    source="test",
                    confidence=1.0,
                    goal_id="goal-pipe",
                    task_id="task-pipe",
                    source_turn=1,
                ),
            )
        }
    )
    catalog = _catalog()
    sources = _sources(catalog=catalog, state=state)

    result = build_answer_plan(
        state,
        _policy(NextActionKind.ANSWER_DIRECT_QUESTION),
        catalog,
        None,
        sources,
        turn_id="turn-request-coordinates",
    )

    plan = result.answer_plan
    assert plan is not None
    assert {"requested_quantity_m", "destination_region"}.issubset(
        {claim.predicate for claim in plan.claims}
    )
    rendered = deterministic_render(plan)
    assert "Требуемое количество: 800 м" in rendered.text
    assert "Пункт назначения: Казахстан" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_unavailable_delivery_capability_keeps_packaging_coordinates_honest() -> None:
    state = _state(act=TaskAct.CHECK_DELIVERY)
    state = state.model_copy(
        update={
            "constraints": (
                ConstraintFactV2(
                    fact_id="fact-whole-bundles",
                    name="delivery_whole_bundles",
                    value=True,
                    evidence="только целыми упаковками",
                    source="test",
                    confidence=1.0,
                    goal_id="goal-pipe",
                    task_id="task-pipe",
                    source_turn=1,
                ),
                ConstraintFactV2(
                    fact_id="fact-no-repack",
                    name="delivery_no_repack",
                    value=True,
                    evidence="без переупаковки",
                    source="test",
                    confidence=1.0,
                    goal_id="goal-pipe",
                    task_id="task-pipe",
                    source_turn=1,
                ),
            )
        }
    )
    workflow = CommerceWorkflowState(
        workflow_id="workflow-delivery",
        contract_id="delivery-v1",
        workflow_kind=CommerceWorkflowKind.CHECK_DELIVERY,
        task_ids=("task-pipe",),
        status=CommerceWorkflowStatus.BLOCKED,
        capability_id="delivery-policy",
        capability_mode=CapabilityMode.UNAVAILABLE,
        execution_status=CommerceExecutionStatus.NOT_REQUESTED,
        created_turn=1,
        updated_turn=1,
    )
    commerce = CommercePlanningResult(
        status="planned",
        workflows=(workflow,),
        capability_boundaries=(
            "workflow-delivery:delivery_policy_not_configured",
        ),
    )
    catalog = _catalog()
    sources = _sources(catalog=catalog, commerce=commerce, state=state)
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.STATE_COMMERCE_CAPABILITY_BOUNDARY,
            task_id="task-pipe",
            reason_code="delivery_capability_unavailable",
        ),
        task_ids=("task-pipe",),
    )

    plan = _compile(
        state=state,
        catalog=catalog,
        commerce=commerce,
        sources=sources,
        policy=policy,
    ).answer_plan

    assert plan is not None
    assert {"delivery_whole_bundles", "delivery_no_repack"}.issubset(
        {item.predicate for item in plan.claims}
    )
    rendered = deterministic_render(plan)
    assert "Отгрузка целыми упаковками: да" in rendered.text
    assert "Без переупаковки: да" in rendered.text
    assert "не подтверждает склад и город отгрузки" in rendered.text
    assert "целыми упаковками без переупаковки" in rendered.text
    assert "передам" not in rendered.text.casefold()
    assert "заявк" not in rendered.text.casefold()
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_identical_public_limitations_render_once_with_all_typed_ids() -> None:
    state = _state(constraint_status=ConstraintStatus.UNKNOWN)
    sources = _sources(state=state)
    plan = _compile(state=state, sources=sources).answer_plan
    assert plan is not None and plan.limitations
    original = plan.limitations[0]
    duplicate = original.model_copy(
        update={
            "limitation_id": "limit-same-public-copy",
            "reason_code": "different_machine_reason",
        }
    )
    sections = tuple(
        section.model_copy(
            update={"item_ids": (*section.item_ids, duplicate.limitation_id)}
        )
        if section.kind.value == "limitations"
        else section
        for section in plan.sections
    )
    plan = plan.model_copy(
        update={
            "limitations": (*plan.limitations, duplicate),
            "sections": sections,
        }
    )

    rendered = deterministic_render(plan)

    assert rendered.text.count("Я не буду подставлять значение") == 1
    matching = [
        segment
        for segment in rendered.segments
        if duplicate.limitation_id in segment.source_ids
    ]
    assert len(matching) == 1
    assert original.limitation_id in matching[0].source_ids
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_numeric_soft_relaxations_prefer_nearest_in_stock_value() -> None:
    candidates = tuple(
        CandidateAssessment(
            sku=sku,
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            status=CandidateStatus.ELIGIBLE,
            mismatched_soft_facts=("length_m",),
            relaxations=(
                CatalogRelaxation(
                    fact_name="length_m",
                    requested_value=35,
                    candidate_value=value,
                    reason_code="one_soft_constraint_relaxed",
                ),
            ),
        )
        for sku, value in (("PIPE-A", 40), ("PIPE-B", 34), ("PIPE-C", 30))
    )
    search = CatalogSearchPlan(
        plan_id="search-nearest",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.RELAX_ONE_SOFT_CONSTRAINT,),
        candidate_assessments=candidates,
        relaxed_skus=tuple(item.sku for item in candidates),
    )
    readiness = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        status=ReadinessStatus.PRELIMINARY_READY,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
        candidate_skus=tuple(item.sku for item in candidates),
    )
    provenance = FactProvenance(
        source="attribute",
        source_field="length",
        raw_value="fixture",
        parser="test",
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(
            source_revision="nearest-v1",
            products=tuple(
                CatalogAnswerProduct(
                    sku=item.sku,
                    name=item.sku,
                    product_kind=ProductKind.PIPE,
                    role=CatalogProductRole.BASE_PRODUCT,
                    stock_status="in_stock",
                    stock_qty=1,
                    facts=(
                        CatalogFact(
                            name="length_m",
                            value=item.relaxations[0].candidate_value,
                            unit="m",
                            provenance=provenance,
                        ),
                    ),
                )
                for item in candidates
            ),
        ),
        catalog,
        None,
        _state(act=TaskAct.SELECT),
    )

    result = build_answer_plan(
        _state(act=TaskAct.SELECT),
        _policy(NextActionKind.SHOW_PRELIMINARY_OPTIONS),
        catalog,
        None,
        sources,
        turn_id="turn-nearest",
    )

    assert result.answer_plan is not None
    assert [item.sku for item in result.answer_plan.products] == [
        "PIPE-B",
        "PIPE-A",
        "PIPE-C",
    ]


def test_in_stock_allowed_analog_precedes_unavailable_exact_option() -> None:
    exact = CandidateAssessment(
        sku="EXACT-OUT",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        status=CandidateStatus.ELIGIBLE,
    )
    analog = CandidateAssessment(
        sku="ANALOG-IN",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        status=CandidateStatus.ELIGIBLE,
        mismatched_soft_facts=("length_m",),
        relaxations=(
            CatalogRelaxation(
                fact_name="length_m",
                requested_value=35,
                candidate_value=34,
                reason_code="one_soft_constraint_relaxed",
            ),
        ),
    )
    plan = CatalogSearchPlan(
        plan_id="stock-aware",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.RELAX_ONE_SOFT_CONSTRAINT,),
        candidate_assessments=(exact, analog),
        eligible_skus=(exact.sku,),
        relaxed_skus=(analog.sku,),
    )
    snapshot = AnswerSourceSnapshot(
        source_revision="stock-aware-v1",
        products=(
            CatalogAnswerProduct(
                sku=exact.sku,
                name="Exact but unavailable",
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                stock_status="out_of_stock",
                stock_qty=0,
            ),
            CatalogAnswerProduct(
                sku=analog.sku,
                name="Allowed nearby analog in stock",
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                stock_status="in_stock",
                stock_qty=2,
            ),
        ),
    )

    selected, order, _ = _presentable_candidate_shortlist((plan,), snapshot)

    assert selected == {
        (plan.plan_id, analog.sku),
        (plan.plan_id, exact.sku),
    }
    assert order[(plan.plan_id, analog.sku)] == 0
    assert order[(plan.plan_id, exact.sku)] == 1


def test_global_candidate_budget_is_fair_across_product_tasks() -> None:
    def make_plan(task_id: str, prefix: str) -> CatalogSearchPlan:
        candidates = tuple(
            CandidateAssessment(
                sku=f"{prefix}-{index}",
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                status=CandidateStatus.ELIGIBLE,
            )
            for index in range(4)
        )
        return CatalogSearchPlan(
            plan_id=f"plan-{task_id}",
            task_id=task_id,
            goal_id=f"goal-{task_id}",
            contract_id="pipe.ppr.v1",
            product_kind=ProductKind.PIPE,
            requested_role=CatalogProductRole.BASE_PRODUCT,
            stages=(CatalogSearchStage.STRICT_SAME_KIND,),
            candidate_assessments=candidates,
            eligible_skus=tuple(item.sku for item in candidates),
        )

    first = make_plan("task-a", "A")
    second = make_plan("task-b", "B")
    snapshot = AnswerSourceSnapshot(
        source_revision="fair-global-budget-v1",
        products=tuple(
            CatalogAnswerProduct(
                sku=candidate.sku,
                name=candidate.sku,
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                stock_status="in_stock",
                stock_qty=1,
            )
            for plan in (first, second)
            for candidate in plan.candidate_assessments
        ),
    )

    selected, order, applied = _presentable_candidate_shortlist(
        (first, second),
        snapshot,
        task_order=("task-b", "task-a"),
    )

    assert applied is True
    assert len(selected) == 5
    assert selected == {
        (second.plan_id, "B-0"),
        (first.plan_id, "A-0"),
        (second.plan_id, "B-1"),
        (first.plan_id, "A-1"),
        (second.plan_id, "B-2"),
    }
    assert order[(second.plan_id, "B-0")] == 0
    assert order[(first.plan_id, "A-0")] == 0


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
        assert plan.next_step.kind != NextStepKind.ASK_DECISION_FACT
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


def test_validator_rejects_catalogue_claim_without_product_presentation() -> None:
    sources = _sources()
    plan = _compile(sources=sources).answer_plan
    assert plan is not None
    plan_without_cards = plan.model_copy(update={"products": ()})

    rendered = deterministic_render(plan_without_cards)
    validation = validate_rendered_answer(
        plan_without_cards,
        rendered,
        sources,
    )

    assert validation.status == "rejected"
    assert any(
        item.code == "catalog_claim_without_single_product_presentation"
        for item in validation.violations
    )


def test_answer_plan_omits_constraints_outside_resolved_product_contract() -> None:
    state = _state(act=TaskAct.SELECT)
    facts = (
        ConstraintFactV2(
            fact_id="fact-diameter-applicable",
            name="diameter_mm",
            value=25,
            unit="mm",
            evidence="25 мм",
            source="test",
            confidence=1.0,
            goal_id="goal-pipe",
            task_id="task-pipe",
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-unrelated-hydraulics",
            name="dynamic_water_level_m",
            value=12,
            unit="m",
            evidence="12 м",
            source="test",
            confidence=1.0,
            goal_id="goal-pipe",
            task_id="task-pipe",
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-unrelated-unknown",
            name="suction_depth_m",
            status=ConstraintStatus.UNKNOWN,
            evidence="не знаю",
            source="test",
            confidence=1.0,
            goal_id="goal-pipe",
            task_id="task-pipe",
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-other-goal",
            name="power_kw",
            value=24,
            unit="kW",
            evidence="24 кВт",
            source="test",
            confidence=1.0,
            goal_id="goal-other",
            task_id="task-other",
            source_turn=1,
        ),
    )
    state = state.model_copy(update={"constraints": facts})
    catalog = _catalog()
    catalog = catalog.model_copy(
        update={
            "readiness_assessments": tuple(
                item.model_copy(update={"contract_id": "pipe.ppr.v1"})
                for item in catalog.readiness_assessments
            ),
            "search_plans": tuple(
                item.model_copy(update={"contract_id": "pipe.ppr.v1"})
                for item in catalog.search_plans
            ),
        }
    )
    sources = _sources(catalog=catalog, state=state)
    result = _compile(state=state, catalog=catalog, sources=sources)
    plan = result.answer_plan

    assert plan is not None
    assert "diameter_mm" in {item.predicate for item in plan.claims}
    assert "dynamic_water_level_m" not in {
        item.predicate for item in plan.claims
    }
    assert "power_kw" not in {item.predicate for item in plan.claims}
    assert "suction_depth_m" not in {
        item.fact_name for item in plan.limitations
    }
    assert any(
        item.predicate == "dynamic_water_level_m"
        and item.reason_code
        == "constraint_not_applicable_to_resolved_task_contract"
        for item in result.rejected_claims
    )
    assert any(
        item.predicate == "suction_depth_m"
        and item.reason_code
        == "constraint_not_applicable_to_resolved_task_contract"
        for item in result.rejected_claims
    )
    assert any(
        item.predicate == "power_kw"
        and item.reason_code == "constraint_outside_answer_task_scope"
        for item in result.rejected_claims
    )


def test_renderer_does_not_repeat_a_unit_already_present_in_value() -> None:
    state = _state(act=TaskAct.SELECT)
    fact = ConstraintFactV2(
        fact_id="fact-size-with-unit",
        name="diameter_mm",
        value="25 мм",
        unit="mm",
        evidence="25 мм",
        source="test",
        confidence=1.0,
        goal_id="goal-pipe",
        task_id="task-pipe",
        source_turn=1,
    )
    state = state.model_copy(update={"constraints": (fact,)})
    plan = _compile(state=state, sources=_sources(state=state)).answer_plan

    assert plan is not None
    rendered = deterministic_render(plan)
    assert "25 мм mm" not in rendered.text
    assert validate_rendered_answer(plan, rendered, _sources(state=state)).status == "accepted"


def test_not_requested_commerce_status_stays_internal() -> None:
    commerce = _commerce(
        CommerceWorkflowStatus.COLLECTING,
        CommerceExecutionStatus.NOT_REQUESTED,
    )
    plan = _compile(
        commerce=commerce,
        sources=_sources(commerce=commerce),
    ).answer_plan

    assert plan is not None
    assert not [
        item for item in plan.claims if item.kind == ClaimKind.COMMERCE_STATUS
    ]
    assert "операция не запрошена" not in deterministic_render(plan).text


def test_confirmed_claim_predicates_are_grounded_without_allowing_tampering() -> None:
    state = _state()
    facts = (
        ConstraintFactV2(
            fact_id="fact-area",
            name="area_m2",
            value=120,
            unit="m²",
            evidence="120 м²",
            source="test",
            confidence=1.0,
            goal_id="goal-pipe",
            task_id="task-pipe",
            source_turn=1,
        ),
        ConstraintFactV2(
            fact_id="fact-area-zone",
            name="area2",
            value=2,
            evidence="area2",
            source="test",
            confidence=1.0,
            goal_id="goal-pipe",
            task_id="task-pipe",
            source_turn=1,
        ),
    )
    state = state.model_copy(update={"constraints": facts})
    sources = _sources(catalog=None, state=state)
    plan = _compile(state=state, catalog=None, sources=sources).answer_plan
    assert plan is not None
    assert {"area_m2", "area2"}.issubset(
        {item.predicate for item in plan.claims}
    )

    rendered = deterministic_render(plan)
    validation = validate_rendered_answer(plan, rendered, sources)
    assert validation.status == "accepted", validation

    area2_claim = next(item for item in plan.claims if item.predicate == "area2")
    segment_index = next(
        index
        for index, item in enumerate(rendered.segments)
        if item.segment_id == f"segment_{area2_claim.claim_id}"
    )
    segments = list(rendered.segments)
    segments[segment_index] = segments[segment_index].model_copy(
        update={"text": f"{segments[segment_index].text} fake3."}
    )
    tampered = rendered.model_copy(
        update={
            "segments": tuple(segments),
            "text": "\n".join(item.text for item in segments),
        }
    )
    rejected = validate_rendered_answer(plan, tampered, sources)
    assert rejected.status == "rejected"
    assert "fake3" in rejected.extra_critical_literals


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


def _exact_recommendation_fixture(
    prices: tuple[tuple[str, float | None, str | None], ...],
) -> tuple[DialogueStateV2, CatalogPlanningResult, AnswerSourceSnapshot]:
    state = _state(act=TaskAct.SELECT, constraint_status=ConstraintStatus.KNOWN)
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    candidates = tuple(
        CandidateAssessment(
            sku=sku,
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            status=CandidateStatus.ELIGIBLE,
            matched_hard_facts=("diameter_mm",),
            reason_codes=("strict_contract_match",),
        )
        for sku, _price, _currency in reversed(prices)
    )
    readiness = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        status=ReadinessStatus.EXACT_READY,
    )
    search = CatalogSearchPlan(
        plan_id="search-recommend-pipe",
        task_id="task-pipe",
        goal_id="goal-pipe",
        contract_id="pipe-v1",
        product_kind=ProductKind.PIPE,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.STRICT_SAME_KIND,),
        hard_constraints=(
            SearchConstraint(
                name="diameter_mm",
                value=25,
                unit="mm",
                strength=FactStrength.HARD,
            ),
        ),
        candidate_assessments=candidates,
        eligible_skus=tuple(candidate.sku for candidate in candidates),
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
        search_plans=(search,),
        candidate_skus=tuple(candidate.sku for candidate in candidates),
    )
    products = tuple(
        CatalogAnswerProduct(
            sku=sku,
            name=f"Труба {sku}",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            price=price,
            currency=currency,
            facts=(
                CatalogFact(
                    name="diameter_mm",
                    value=25,
                    unit="mm",
                    provenance=provenance,
                ),
            ),
        )
        for sku, price, currency in prices
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(
            source_revision="recommend-fixture-v1",
            products=products,
        ),
        catalog,
        None,
        state,
    )
    return state, catalog, sources


def test_select_recommends_one_exact_candidate_by_confirmed_price() -> None:
    state, catalog, sources = _exact_recommendation_fixture(
        (
            ("PIPE-A", 300.0, "RUB"),
            ("PIPE-B", 100.0, "RUB"),
            ("PIPE-C", 200.0, "RUB"),
            ("PIPE-D", 400.0, "RUB"),
        )
    )
    result = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=_policy(NextActionKind.RECOMMEND_ONE),
    )
    plan = result.answer_plan
    assert plan is not None
    assert plan.next_step.kind == NextStepKind.RECOMMEND_ONE
    assert [item.sku for item in plan.products] == ["PIPE-B", "PIPE-C", "PIPE-A"]
    assert [item.recommendation_role for item in plan.products] == [
        ProductRecommendationRole.PRIMARY,
        ProductRecommendationRole.ALTERNATIVE,
        ProductRecommendationRole.ALTERNATIVE,
    ]
    assert [item.recommendation_rank for item in plan.products] == [1, 2, 3]
    assert all(
        item.recommendation_criterion
        == RecommendationCriterion.LOWEST_CONFIRMED_PRICE
        for item in plan.products
    )
    assert "lowest_confirmed_price_among_priced_exact_candidates" in (
        plan.products[0].recommendation_reason_codes
    )
    assert "PIPE-D" not in {item.sku for item in plan.products}

    rendered = deterministic_render(plan)
    assert rendered.text.count("Рекомендую") == 1
    assert rendered.text.count("дополнительный точный вариант") == 2
    validation = validate_rendered_answer(plan, rendered, sources)
    assert validation.status == "accepted", validation


def test_equal_lowest_price_uses_stable_sku_tiebreak() -> None:
    state, catalog, sources = _exact_recommendation_fixture(
        (
            ("PIPE-B", 100.0, "RUB"),
            ("PIPE-A", 100.0, "RUB"),
            ("PIPE-C", 200.0, "RUB"),
        )
    )
    plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=_policy(NextActionKind.RECOMMEND_ONE),
    ).answer_plan
    assert plan is not None
    assert [item.sku for item in plan.products] == ["PIPE-A", "PIPE-B", "PIPE-C"]
    assert "stable_sku_tiebreak_among_equal_lowest_prices" in (
        plan.products[0].recommendation_reason_codes
    )
    rendered = deterministic_render(plan)
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_find_keeps_options_without_recommendation_metadata() -> None:
    state, catalog, sources = _exact_recommendation_fixture(
        (
            ("PIPE-A", 300.0, "RUB"),
            ("PIPE-B", 100.0, "RUB"),
            ("PIPE-C", 200.0, "RUB"),
            ("PIPE-D", 400.0, "RUB"),
        )
    )
    find_task = state.tasks[0].model_copy(update={"act": TaskAct.FIND})
    state = state.model_copy(update={"tasks": (find_task,)})
    plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=_policy(NextActionKind.SEARCH_EXACT),
    ).answer_plan
    assert plan is not None
    assert plan.next_step.kind != NextStepKind.RECOMMEND_ONE
    assert len(plan.products) == 4
    assert all(item.recommendation_role is None for item in plan.products)


def test_validator_rejects_non_deterministic_recommendation_choice() -> None:
    state, catalog, sources = _exact_recommendation_fixture(
        (
            ("PIPE-A", 300.0, "RUB"),
            ("PIPE-B", 100.0, "RUB"),
            ("PIPE-C", 200.0, "RUB"),
        )
    )
    plan = _compile(
        state=state,
        catalog=catalog,
        sources=sources,
        policy=_policy(NextActionKind.RECOMMEND_ONE),
    ).answer_plan
    assert plan is not None
    cheapest, expensive, *rest = plan.products
    tampered_products = (
        cheapest.model_copy(
            update={
                "recommendation_role": ProductRecommendationRole.ALTERNATIVE,
                "recommendation_rank": 2,
                "recommendation_reason_codes": (
                    "exact_eligible_recommendation_alternative",
                ),
            }
        ),
        expensive.model_copy(
            update={
                "recommendation_role": ProductRecommendationRole.PRIMARY,
                "recommendation_rank": 1,
                "recommendation_reason_codes": (
                    "lowest_confirmed_price_among_priced_exact_candidates",
                ),
            }
        ),
        *rest,
    )
    tampered_plan = plan.model_copy(update={"products": tampered_products})
    rendered = deterministic_render(tampered_plan)
    validation = validate_rendered_answer(tampered_plan, rendered, sources)
    assert validation.status != "accepted"
    assert any(
        item.code == "recommendation_order_not_source_grounded"
        for item in validation.violations
    )
