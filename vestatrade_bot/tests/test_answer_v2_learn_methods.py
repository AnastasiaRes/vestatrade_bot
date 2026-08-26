from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, NextStepKind
from app.answer_v2.planner import build_answer_plan
from app.answer_v2.renderer import (
    _LEARN_METHOD_INSTRUCTIONS,
    deterministic_render,
)
from app.answer_v2.sources import attach_turn_source_evidence
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    ProductKind,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.catalog_v2.registry import ProductContractRegistry
from app.dialogue_v2.contracts import (
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

from test_answer_v2_planning import _compile, _sources, _state


def _declared_learn_method_codes() -> set[str]:
    return {
        definition.learn_method_code
        for contract in ProductContractRegistry().contracts
        for definition in contract.fact_definitions
        if definition.learn_method_code
    }


def test_every_declared_contract_learn_method_has_actionable_copy() -> None:
    declared = _declared_learn_method_codes()

    assert declared
    assert declared.issubset(_LEARN_METHOD_INSTRUCTIONS)
    assert all(len(_LEARN_METHOD_INSTRUCTIONS[code].split()) >= 8 for code in declared)


def test_question_uses_contract_learn_method_and_expected_unit() -> None:
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
                learn_method_code="measure_outer_or_nominal_diameter",
            ),
        ),
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id="task-pipe",
            fact_name="diameter_mm",
            reason_code="diameter_changes_selection",
        ),
        task_ids=("task-pipe",),
    )
    sources = _sources(catalog=catalog, state=state)

    plan = _compile(
        state=state,
        catalog=catalog,
        policy=policy,
        sources=sources,
    ).answer_plan

    assert plan is not None and plan.question is not None
    assert plan.question.learn_method_code == "measure_outer_or_nominal_diameter"
    assert plan.question.expected_unit == "мм"
    rendered = deterministic_render(plan)
    assert "штангенциркулем" in rendered.text
    assert "миллиметрах (мм)" in rendered.text


def test_pipe_service_question_explains_supported_system_choices() -> None:
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
                missing_decision_facts=("pipe_service",),
                recommended_question_fact="pipe_service",
                learn_method_code="identify_pipe_service",
            ),
        ),
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id="task-pipe",
            fact_name="pipe_service",
            reason_code="pipe_service_changes_selection",
        ),
        task_ids=("task-pipe",),
    )
    sources = _sources(catalog=catalog, state=state)

    plan = _compile(
        state=state,
        catalog=catalog,
        policy=policy,
        sources=sources,
    ).answer_plan

    assert plan is not None and plan.question is not None
    assert plan.question.learn_method_code == "identify_pipe_service"
    rendered = deterministic_render(plan)
    assert "холодное или горячее водоснабжение" in rendered.text
    assert "для канализации нужен отдельный тип трубы" in rendered.text.lower()


def test_independent_explain_task_inherits_same_goal_readiness_guidance() -> None:
    goal = ProductGoal(
        goal_id="goal-pump",
        canonical_type="циркуляционный насос",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="насос",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    select_task = CustomerTask(
        task_id="task-pump-select",
        act=TaskAct.SELECT,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    explain_task = CustomerTask(
        task_id="task-pump-explain",
        act=TaskAct.EXPLAIN,
        target_goal_id=goal.goal_id,
        priority=1,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=2,
    )
    state = DialogueStateV2(
        turn_number=2,
        task_stack=TaskStack(
            active_task_id=explain_task.task_id,
            pending_task_ids=(select_task.task_id,),
        ),
        tasks=(select_task, explain_task),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=select_task.task_id,
                goal_id=goal.goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.PRELIMINARY_READY,
                unknown_facts=("mounting_length_mm",),
                learn_method_code="measure_old_pump_mounting_length",
            ),
        ),
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
            task_id=explain_task.task_id,
            fact_name="mounting_length_mm",
            reason_code="explain_unknown_fact_method",
        ),
        task_ids=(explain_task.task_id, select_task.task_id),
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(source_revision="learn-method-test"),
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
        turn_id="learn-method-turn",
    )

    assert result.answer_plan is not None
    step = result.answer_plan.next_step
    assert step.kind == NextStepKind.EXPLAIN_HOW_TO_FIND_FACT
    assert step.learn_method_code == "measure_old_pump_mounting_length"
    assert step.expected_unit == "мм"
    rendered = deterministic_render(result.answer_plan)
    assert "вдоль оси трубопровода" in rendered.text
    assert "между ответными уплотнительными плоскостями" in rendered.text
    assert "шильдике или в паспорте" in rendered.text
    assert "встанет ли насос в существующий разрыв" in rendered.text
    assert "относятся к кандидатам" in rendered.text
    assert "миллиметрах (мм)" in rendered.text
    assert "не разбирайте горячую" in rendered.text.lower()
    assert validate_rendered_answer(result.answer_plan, rendered, sources).status == "accepted"


def test_connection_pattern_method_inspects_both_ends_without_disassembly() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    step = plan.next_step.model_copy(
        update={
            "kind": NextStepKind.EXPLAIN_HOW_TO_FIND_FACT,
            "fact_name": "connection_pattern",
            "learn_method_code": "inspect_both_connection_threads",
            "expected_unit": None,
        }
    )
    rendered = deterministic_render(plan.model_copy(update={"next_step": step}))

    assert "оба конца детали" in rendered.text
    assert "ВР" in rendered.text and "НР" in rendered.text
    assert "не разбирайте" in rendered.text.lower()


def test_fact_scoped_contract_method_beats_different_readiness_next_question() -> None:
    goal = ProductGoal(
        goal_id="goal-pump-method-scope",
        canonical_type="circulation_pump",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="pump",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
    )
    task = CustomerTask(
        task_id="task-pump-method-scope",
        act=TaskAct.EXPLAIN,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    state = DialogueStateV2(
        turn_number=1,
        task_stack=TaskStack(active_task_id=task.task_id),
        tasks=(task,),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
    )
    catalog = CatalogPlanningResult(
        status="planned",
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=task.task_id,
                goal_id=goal.goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                unknown_facts=("diameter_mm",),
                missing_decision_facts=("max_head_m", "mounting_length_mm"),
                recommended_question_fact="max_head_m",
                learn_method_code="estimate_required_system_head",
            ),
        ),
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.EXPLAIN_HOW_TO_FIND_FACT,
            task_id=task.task_id,
            fact_name="diameter_mm",
            reason_code="explain_diameter",
        ),
        task_ids=(task.task_id,),
    )
    sources = attach_turn_source_evidence(
        AnswerSourceSnapshot(source_revision="method-scope"),
        catalog,
        None,
        state,
    )

    plan = build_answer_plan(
        state,
        policy,
        catalog,
        None,
        sources,
        turn_id="method-scope-turn",
    ).answer_plan

    assert plan is not None
    assert plan.next_step.learn_method_code == "measure_outer_or_nominal_diameter"
    rendered = deterministic_render(plan)
    assert "штангенциркулем" in rendered.text
    assert "гидравлического расчёта" not in rendered.text
