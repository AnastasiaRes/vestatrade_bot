from __future__ import annotations

from app.answer_v2.contracts import (
    AnswerPlanStatus,
    AnswerSourceSnapshot,
    CatalogAnswerProduct,
    CandidateFactStatus,
    NextStepKind,
)
from app.answer_v2.planner import build_answer_plan
from app.answer_v2.renderer import deterministic_render
from app.answer_v2.sources import attach_turn_source_evidence
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogFactIssue,
    CatalogPlanningResult,
    CatalogProductRole,
    FactProvenance,
    ProductKind,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    CustomerTask,
    DialogueStateV2,
    InformationOutputRelation,
    InformationPurpose,
    InformationSourceKind,
    InformationSubjectScope,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ProductCategory,
    ProductGoal,
    ProductRole,
    PresentedCandidateSummary,
    RequestedInformationOutput,
    TaskAct,
    TaskStack,
    TaskStatus,
    AnswerPlanSummary,
    ShadowDeliveryStatus,
)


def _information_answer(
    *,
    purpose: InformationPurpose,
    outputs: tuple[RequestedInformationOutput, ...],
    action_kind: NextActionKind,
    reason_code: str,
    fact_name: str = "mounting_length_mm",
    output_relation: InformationOutputRelation = InformationOutputRelation.ALL,
    source_kind: InformationSourceKind | None = None,
    product_kind: ProductKind = ProductKind.CIRCULATION_PUMP,
    category: ProductCategory = ProductCategory.PUMPS,
    contract_id: str = "pump.circulation.v1",
    known_value: int | None = None,
    secondary: NextAction | None = None,
    information_request_id: str | None = "information-request",
):
    goal = ProductGoal(
        goal_id="goal-information",
        canonical_type=product_kind.value,
        category=category,
        role=ProductRole.TARGET,
        evidence="typed fixture",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    selection_task = CustomerTask(
        task_id="task-selection",
        act=TaskAct.SELECT,
        target_goal_id=goal.goal_id,
        priority=1,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    information_task = CustomerTask(
        task_id="task-information",
        act=(
            TaskAct.GET_LINK
            if RequestedInformationOutput.VERIFIED_LINK in outputs
            else TaskAct.EXPLAIN
        ),
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=2,
    )
    constraints = (
        (
            ConstraintFactV2(
                fact_id="fact-known-information-value",
                name=fact_name,
                value=known_value,
                unit="kW" if fact_name == "power_kw" else None,
                evidence="typed fixture value",
                source="test",
                confidence=1.0,
                goal_id=goal.goal_id,
                task_id=selection_task.task_id,
                source_turn=1,
            ),
        )
        if known_value is not None
        else ()
    )
    state = DialogueStateV2(
        turn_number=2,
        task_stack=TaskStack(
            active_task_id=information_task.task_id,
            pending_task_ids=(selection_task.task_id,),
        ),
        tasks=(selection_task, information_task),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
        constraints=constraints,
    )
    unknown_facts = (
        (fact_name,)
        if product_kind == ProductKind.CIRCULATION_PUMP and known_value is None
        else ()
    )
    readiness = TaskReadinessAssessment(
        task_id=selection_task.task_id,
        goal_id=goal.goal_id,
        contract_id=contract_id,
        product_kind=product_kind,
        status=(
            ReadinessStatus.PRELIMINARY_READY
            if unknown_facts
            else ReadinessStatus.EXACT_READY
        ),
        unknown_facts=unknown_facts,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(readiness,),
    )
    primary = NextAction(
        kind=action_kind,
        task_id=information_task.task_id,
        fact_name=fact_name,
        information_request_id=information_request_id,
        information_purpose=(purpose if information_request_id else None),
        requested_outputs=(outputs if information_request_id else ()),
        output_relation=(output_relation if information_request_id else None),
        source_kind=(source_kind if information_request_id else None),
        reason_code=reason_code,
    )
    policy = NextActionPlan(
        primary=primary,
        secondary=secondary,
        task_ids=(information_task.task_id, selection_task.task_id),
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(source_revision="typed-information-fixture"),
        catalog,
        None,
        state,
    )
    result = build_answer_plan(
        state,
        policy,
        catalog,
        None,
        sources,
        turn_id="typed-information-turn",
    )
    assert result.answer_plan is not None
    rendered = deterministic_render(result.answer_plan)
    return result.answer_plan, rendered, sources


def test_decision_relevance_uses_contract_flags_without_measurement_story() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.DECISION_RELEVANCE,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
        reason_code="typed_information_explanation_request",
    )

    assert plan.next_step.kind == NextStepKind.EXPLAIN_DECISION_RELEVANCE
    assert plan.status == AnswerPlanStatus.READY
    assert plan.next_step.contract_fact_recognized is True
    assert plan.next_step.fact_decision_changing is True
    assert plan.next_step.fact_required_for_exact is True
    assert "обязателен для точного подбора" in rendered.text
    assert "может изменить выбор" in rendered.text
    assert "точную совместимость подтвердить нельзя" in rendered.text
    assert "вдоль оси трубопровода" not in rendered.text
    assert "как измерить" not in rendered.text.lower()
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_determination_instruction_uses_only_declared_contract_learn_method() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.DETERMINATION_METHOD,
        outputs=(RequestedInformationOutput.INSTRUCTION,),
        action_kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
        reason_code="typed_information_determination_method",
    )

    assert plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.status == AnswerPlanStatus.READY
    assert plan.next_step.learn_method_code == "measure_old_pump_mounting_length"
    assert "вдоль оси трубопровода" in rendered.text
    assert "между ответными уплотнительными плоскостями" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_determination_without_contract_method_has_honest_boundary() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.DETERMINATION_METHOD,
        outputs=(RequestedInformationOutput.INSTRUCTION,),
        action_kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
        reason_code="typed_information_determination_method",
        fact_name="max_flow_l_h",
    )

    assert plan.next_step.kind == NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY
    assert plan.status == AnswerPlanStatus.READY
    assert plan.next_step.learn_method_code is None
    assert "в проверенных правилах подбора нет проверенной инструкции" in rendered.text
    assert "по маркировке или паспорту" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_technical_passport_link_states_verified_source_boundary() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.PROVENANCE,
        outputs=(RequestedInformationOutput.VERIFIED_LINK,),
        action_kind=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        reason_code="verified_information_source_unavailable",
        source_kind=InformationSourceKind.TECHNICAL_DOCUMENTATION,
    )

    assert plan.next_step.kind == NextStepKind.STATE_INFORMATION_SOURCE_BOUNDARY
    assert plan.status == AnswerPlanStatus.READY
    assert "подключённых проверенных источниках" in rendered.text
    assert "технической документации или технического паспорта" in rendered.text
    assert "Карточка магазина и общий сайт не заменяют" in rendered.text
    assert "http" not in rendered.text
    assert "вдоль оси трубопровода" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_catalog_product_page_boundary_does_not_claim_document_semantics() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.PROVENANCE,
        outputs=(RequestedInformationOutput.VERIFIED_LINK,),
        action_kind=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        reason_code="verified_information_source_unavailable",
        source_kind=InformationSourceKind.CATALOG_PRODUCT_PAGE,
    )

    assert "нет проверенной ссылки на карточку точного товара" in rendered.text
    assert "другой товар" in rendered.text
    assert "технического паспорта" not in rendered.text
    assert "http" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_official_site_boundary_does_not_call_requested_site_a_substitute() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.PROVENANCE,
        outputs=(RequestedInformationOutput.VERIFIED_LINK,),
        action_kind=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        reason_code="verified_information_source_unavailable",
        source_kind=InformationSourceKind.OFFICIAL_BUSINESS_SITE,
    )

    assert "нет проверенной ссылки на официальный сайт организации" in rendered.text
    assert "непроверенной ссылкой" in rendered.text
    assert "общий сайт не заменяет" not in rendered.text
    assert "http" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_link_or_instruction_can_fulfil_instruction_without_inventing_url() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.DETERMINATION_METHOD,
        outputs=(
            RequestedInformationOutput.VERIFIED_LINK,
            RequestedInformationOutput.INSTRUCTION,
        ),
        output_relation=InformationOutputRelation.ANY,
        action_kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
        reason_code="typed_information_instruction_selected",
        source_kind=InformationSourceKind.ANY_VERIFIED,
    )

    assert plan.next_step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    assert "вдоль оси трубопровода" in rendered.text
    assert "http" not in rendered.text
    assert "нет запрошенного проверенного источника" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_meaning_without_curated_source_is_not_replaced_by_measurement() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.MEANING,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
        reason_code="typed_information_explanation_request",
    )

    assert plan.next_step.kind == NextStepKind.STATE_INFORMATION_MEANING_BOUNDARY
    assert plan.status == AnswerPlanStatus.READY
    assert "нет отдельного подтверждённого определения" in rendered.text
    assert "не буду подменять значение термина инструкцией по измерению" in rendered.text
    assert "вдоль оси трубопровода" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_compatibility_request_states_exact_match_boundary_only() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.COMPATIBILITY,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
        reason_code="typed_information_explanation_request",
    )

    assert plan.next_step.kind == NextStepKind.STATE_COMPATIBILITY_BOUNDARY
    assert plan.status == AnswerPlanStatus.READY
    assert "должны совпасть все обязательные параметры подбора" in rendered.text
    assert "непроверенное значение не считается совпадением" in rendered.text
    assert "вдоль оси трубопровода" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_known_boiler_power_value_is_answered_without_generic_missing_fact_lie() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.VALUE,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.ANSWER_DIRECT_QUESTION,
        reason_code="typed_information_value_request",
        fact_name="power_kw",
        product_kind=ProductKind.BOILER,
        category=ProductCategory.BOILERS,
        contract_id="boiler.generic.v1",
        known_value=15,
    )

    assert plan.next_step.kind == NextStepKind.PROVIDE_DIRECT_ANSWER
    assert plan.status == AnswerPlanStatus.READY
    assert any(
        claim.predicate == "power_kw" and claim.value == 15
        for claim in plan.claims
    )
    assert "15 кВт" in rendered.text
    assert "недостающий параметр" not in rendered.text
    assert "не хватает подтверждённых" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_unknown_information_value_is_a_deliverable_honest_boundary() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.VALUE,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.ANSWER_DIRECT_QUESTION,
        reason_code="typed_information_value_request",
    )

    assert plan.next_step.kind == NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY
    assert plan.status == AnswerPlanStatus.READY
    assert "Подтверждённого значения" in rendered.text
    assert "не буду подставлять" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_legacy_untyped_capability_boundary_remains_nondeliverable() -> None:
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.MEANING,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        reason_code="legacy_capability_boundary",
        information_request_id=None,
    )

    assert plan.next_step.kind == NextStepKind.STATE_CAPABILITY_BOUNDARY
    assert plan.status == AnswerPlanStatus.BOUNDARY
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_information_answer_preserves_one_secondary_selection_question() -> None:
    secondary = NextAction(
        kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        task_id="task-selection",
        fact_name="max_head_m",
        reason_code="max_head_changes_selection",
    )
    plan, rendered, sources = _information_answer(
        purpose=InformationPurpose.DECISION_RELEVANCE,
        outputs=(RequestedInformationOutput.EXPLANATION,),
        action_kind=NextActionKind.EXPLAIN_TERM_OR_METHOD,
        reason_code="typed_information_explanation_request",
        secondary=secondary,
    )

    assert plan.next_step.kind == NextStepKind.EXPLAIN_DECISION_RELEVANCE
    assert plan.question is not None
    assert plan.question.fact_name == "max_head_m"
    assert "обязателен для точного подбора" in rendered.text
    assert "максимальный напор" in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def _candidate_fact_followup_answer():
    goal = ProductGoal(
        goal_id="goal-candidate-report",
        canonical_type="circulation_pump",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="typed fixture",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    selection_task = CustomerTask(
        task_id="task-candidate-selection",
        act=TaskAct.SELECT,
        target_goal_id=goal.goal_id,
        priority=1,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    information_task = CustomerTask(
        task_id="task-candidate-information",
        act=TaskAct.EXPLAIN,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=2,
    )
    presented = tuple(
        PresentedCandidateSummary(
            sku=sku,
            name=name,
            product_kind=ProductKind.CIRCULATION_PUMP,
            role=CatalogProductRole.BASE_PRODUCT,
            task_id=selection_task.task_id,
            goal_id=goal.goal_id,
            search_plan_id="previous-search",
            source_turn=1,
        )
        for sku, name in (
            ("PUMP-180", "Pump Exact 180"),
            ("PUMP-130", "Pump Exact 130"),
            ("PUMP-AMB", "Pump Ambiguous"),
            ("PUMP-MISSING", "Pump Missing"),
        )
    )
    state = DialogueStateV2(
        turn_number=2,
        task_stack=TaskStack(
            active_task_id=information_task.task_id,
            pending_task_ids=(selection_task.task_id,),
        ),
        tasks=(selection_task, information_task),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
        constraints=(
            ConstraintFactV2(
                fact_id="customer-mounting-length-unknown",
                name="mounting_length_mm",
                status="unknown",
                evidence="typed fixture unknown",
                source="test",
                confidence=1.0,
                goal_id=goal.goal_id,
                task_id=selection_task.task_id,
                source_turn=1,
            ),
        ),
        answer_plan_summary=AnswerPlanSummary(
            plan_id="previous-delivered-plan",
            semantic_signature="previous-signature",
            task_ids=(selection_task.task_id,),
            primary_action=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            next_step_kind="show_preliminary_options",
            validation_status="accepted",
            delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
            presented_candidates=presented,
            source_turn=1,
        ),
    )
    provenance_180 = FactProvenance(
        source="attribute",
        source_field="Монтажная длина",
        raw_value="180",
        parser="structured_attribute",
    )
    provenance_130 = provenance_180.model_copy(update={"raw_value": "130"})
    ambiguous = FactProvenance(
        source="attribute",
        source_field="Монтажная длина",
        raw_value="130(180)",
        parser="structured_attribute_ambiguous",
    )
    products = (
        CatalogAnswerProduct(
            sku="PUMP-180",
            name="Pump Exact 180",
            product_kind=ProductKind.CIRCULATION_PUMP,
            role=CatalogProductRole.BASE_PRODUCT,
            url="https://catalog.invalid/pump-180",
            facts=(
                CatalogFact(
                    name="mounting_length_mm",
                    value=180,
                    unit="mm",
                    provenance=provenance_180,
                ),
            ),
        ),
        CatalogAnswerProduct(
            sku="PUMP-130",
            name="Pump Exact 130",
            product_kind=ProductKind.CIRCULATION_PUMP,
            role=CatalogProductRole.BASE_PRODUCT,
            url="https://catalog.invalid/pump-130",
            facts=(
                CatalogFact(
                    name="mounting_length_mm",
                    value=130,
                    unit="mm",
                    provenance=provenance_130,
                ),
            ),
        ),
        CatalogAnswerProduct(
            sku="PUMP-AMB",
            name="Pump Ambiguous",
            product_kind=ProductKind.CIRCULATION_PUMP,
            role=CatalogProductRole.BASE_PRODUCT,
            url="https://catalog.invalid/pump-amb",
            fact_issues=(
                CatalogFactIssue(
                    name="mounting_length_mm",
                    provenance=ambiguous,
                ),
            ),
        ),
        CatalogAnswerProduct(
            sku="PUMP-MISSING",
            name="Pump Missing",
            product_kind=ProductKind.CIRCULATION_PUMP,
            role=CatalogProductRole.BASE_PRODUCT,
            url="https://catalog.invalid/pump-missing",
        ),
    )
    catalog = CatalogPlanningResult(
        status="skipped",
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=selection_task.task_id,
                goal_id=goal.goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.PRELIMINARY_READY,
                unknown_facts=("mounting_length_mm",),
            ),
        ),
        reason_codes=("information_followup_does_not_require_new_search",),
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(
            source_revision="candidate-fact-followup",
            products=products,
        ),
        catalog,
        None,
        state,
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id=information_task.task_id,
            fact_name="mounting_length_mm",
            information_request_id="candidate-fact-request",
            information_purpose=InformationPurpose.VALUE,
            requested_outputs=(RequestedInformationOutput.EXPLANATION,),
            output_relation=InformationOutputRelation.ALL,
            information_subject_scope=(
                InformationSubjectScope.PRESENTED_CANDIDATES
            ),
            reason_code="typed_information_value_request",
        ),
        task_ids=(information_task.task_id, selection_task.task_id),
    )
    result = build_answer_plan(
        state,
        policy,
        catalog,
        None,
        sources,
        turn_id="candidate-fact-followup",
    )
    assert result.answer_plan is not None
    rendered = deterministic_render(result.answer_plan)
    return result.answer_plan, rendered, sources


def test_candidate_scoped_value_reports_each_previously_presented_card() -> None:
    plan, rendered, sources = _candidate_fact_followup_answer()

    assert plan.next_step.kind == NextStepKind.REPORT_CANDIDATE_FACTS
    report = plan.next_step.candidate_fact_report
    assert report is not None
    assert [item.status for item in report.items] == [
        CandidateFactStatus.CONFIRMED,
        CandidateFactStatus.CONFIRMED,
        CandidateFactStatus.AMBIGUOUS,
        CandidateFactStatus.MISSING,
    ]
    assert [(item.value, item.unit) for item in report.items[:2]] == [
        (180, "mm"),
        (130, "mm"),
    ]
    assert plan.products == ()
    assert "Pump Exact 180" in rendered.text
    assert "артикул PUMP-180" in rendered.text
    assert "180 мм" in rendered.text
    assert "130 мм" in rendered.text
    assert "указано неоднозначно" in rendered.text
    assert "однозначного значения" in rendered.text
    assert "По параметру «монтажная длина» значение пока неизвестно" not in rendered.text
    assert "цена" not in rendered.text.lower()
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"


def test_candidate_fact_report_does_not_invent_or_echo_catalogue_urls() -> None:
    plan, rendered, sources = _candidate_fact_followup_answer()

    assert "http" not in rendered.text
    assert all(
        item.value is None
        for item in plan.next_step.candidate_fact_report.items[2:]
    )
    assert "130(180) мм" not in rendered.text
    assert validate_rendered_answer(plan, rendered, sources).status == "accepted"
