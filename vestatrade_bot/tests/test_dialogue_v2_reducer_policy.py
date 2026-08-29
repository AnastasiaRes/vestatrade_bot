from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agents.semantic_interpreter import TurnUnderstanding
from app.agents.semantic_interpreter import SemanticInterpretationResult
from app.answer_v2.contracts import (
    AnswerPlan,
    AnswerPlanningResult,
    AnswerPlanStatus,
    AnswerSection,
    AnswerSectionKind,
    AnswerValidationResult,
    NextStepKind,
    NextStepPlan,
    QuestionPlan,
    StrategyDirective,
)
from app.answer_v2.progress import assess_task_progress
from app.catalog_v2.contracts import (
    ProductKind,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    ConstraintStatus,
    DialogueStateV2,
    InformationOutputRelation,
    InformationPurpose,
    InformationRequestStatus,
    NextAction,
    NextActionKind,
    NextActionPlan,
    PresentedCandidateSummary,
    ProgressKind,
    RequestedInformationOutput,
    ResponseStrategyKind,
    ShadowDeliveryStatus,
    TaskStatus,
    TurnMetadata,
)
from app.dialogue_v2.reducer import (
    record_answer_shadow,
    record_policy_decision,
    record_response_delivery,
    reduce_dialogue_state,
)
from app.dialogue_v2.seller_policy import SellerPolicy
from app.dialogue_v2.controller import DialogueControllerV2


def _product(
    canonical_type: str,
    category: str,
    role: str = "target",
) -> dict[str, object]:
    return {
        "text": canonical_type,
        "canonical_type": canonical_type,
        "category": category,
        "role": role,
        "evidence": canonical_type,
    }


def _fact(
    name: str,
    value: object = 25,
    *,
    status: str = "known",
    product: int | None = 0,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value if status == "known" else None,
        "unit": "mm" if status == "known" else None,
        "status": status,
        "polarity": "required",
        "applies_to_product": product,
        "evidence": f"{name}-{status}",
    }


def _turn(
    *,
    operation: str = "continue",
    acts: list[str] | None = None,
    products: list[dict[str, object]] | None = None,
    constraints: list[dict[str, object]] | None = None,
    ambiguities: list[dict[str, str]] | None = None,
    information_requests: list[dict[str, object]] | None = None,
    pending_answer: bool = False,
) -> TurnUnderstanding:
    return TurnUnderstanding.model_validate(
        {
            "schema_version": "1.0",
            "language": "ru",
            "operation": operation,
            "acts": acts or [],
            "products": products or [],
            "constraints": constraints or [],
            "references": [],
            "ambiguities": ambiguities or [],
            "information_requests": information_requests or [],
            "answers_pending_question": pending_answer,
            "confidence": 0.94,
        }
    )


def _information_request(
    *,
    fact_name: str | None,
    purpose: str,
    outputs: list[str],
    act: str = "explain",
    product: int | None = 0,
    relation: str = "all",
    source_kind: str | None = None,
    subject_scope: str = "customer_goal",
) -> dict[str, object]:
    return {
        "fact_name": fact_name,
        "purpose": purpose,
        "requested_outputs": outputs,
        "output_relation": relation,
        "source_kind": source_kind,
        "act": act,
        "subject_scope": subject_scope,
        "applies_to_product": product,
        "evidence": "typed information request",
    }


def _reduce(
    previous: DialogueStateV2 | None,
    understanding: TurnUnderstanding,
    turn_id: str,
):
    return reduce_dialogue_state(
        previous,
        understanding,
        TurnMetadata(turn_id=turn_id),
    )


def _accepted(understanding: TurnUnderstanding) -> SemanticInterpretationResult:
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def _active_goal(state: DialogueStateV2):
    return next(
        goal for goal in state.product_goals if goal.goal_id == state.active_goal_id
    )


def _active_facts(state: DialogueStateV2):
    return [fact for fact in state.constraints if fact.active]


def _with_information_summary(
    state: DialogueStateV2,
    plan: NextActionPlan,
    *,
    plan_id: str,
    request_id: str | None = None,
    fulfilled: tuple[RequestedInformationOutput, ...] = (),
    unavailable: tuple[RequestedInformationOutput, ...] = (),
    validation_status: str = "accepted",
) -> DialogueStateV2:
    action = plan.primary
    return state.model_copy(
        update={
            "answer_plan_summary": AnswerPlanSummary(
                plan_id=plan_id,
                semantic_signature=f"signature-{plan_id}",
                task_ids=plan.task_ids,
                primary_action=action.kind,
                next_step_kind="typed_information_test_step",
                validation_status=validation_status,
                delivery_status=ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
                information_request_id=(
                    request_id
                    if request_id is not None
                    else action.information_request_id
                ),
                information_requested_outputs=action.requested_outputs,
                information_output_relation=action.output_relation,
                information_fulfilled_outputs=fulfilled,
                information_unavailable_outputs=unavailable,
                information_reason_codes=("typed_information_test_boundary",),
                source_turn=state.turn_number,
            )
        }
    )


def test_target_is_not_replaced_by_existing_or_context_product() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("циркуляционный насос", "pumps"),
                _product("радиатор", "radiators", "existing"),
            ],
        ),
        "target-context",
    )

    assert _active_goal(result.state).canonical_type == "циркуляционный насос"
    assert _active_goal(result.state).category.value == "pumps"
    assert {goal.role.value for goal in result.state.product_goals} == {
        "target",
        "existing",
    }


def test_explicit_type_and_diameter_correction_replaces_prior_values() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("циркуляционный насос", "pumps")],
            constraints=[_fact("connection_diameter", 25)],
        ),
        "correction-1",
    )
    second = _reduce(
        first.state,
        _turn(
            operation="correct",
            products=[_product("повысительный насос", "pumps")],
            constraints=[_fact("connection_diameter", 32)],
        ),
        "correction-2",
    )

    assert _active_goal(second.state).canonical_type == "повысительный насос"
    active = _active_facts(second.state)
    assert len(active) == 1
    assert active[0].value == 32
    assert active[0].replaces_fact_id is not None
    assert {event.event_type for event in second.events} >= {
        "product_goal_corrected",
        "constraint_corrected",
    }


def test_refine_can_broaden_scalar_into_containing_numeric_range() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("газовый котёл", "boilers")],
            constraints=[
                {
                    **_fact("power_kw", 15),
                    "unit": "kW",
                    "evidence": "15 кВт",
                }
            ],
        ),
        "range-refinement-1",
    )

    refined = _reduce(
        first.state,
        _turn(
            operation="refine",
            constraints=[
                {
                    **_fact("power_kw", "15–20", product=None),
                    "unit": "кВт",
                    "evidence": "15–20 кВт",
                }
            ],
        ),
        "range-refinement-2",
    )

    active = _active_facts(refined.state)
    assert len(active) == 1
    assert active[0].value == "15–20"
    assert active[0].replaces_fact_id is not None
    assert "constraint_corrected" in {
        event.event_type for event in refined.events
    }


def test_refine_still_rejects_disjoint_confirmed_numeric_value() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("газовый котёл", "boilers")],
            constraints=[
                {
                    **_fact("power_kw", 15),
                    "unit": "kW",
                    "evidence": "15 кВт",
                }
            ],
        ),
        "disjoint-refinement-1",
    )

    rejected = _reduce(
        first.state,
        _turn(
            operation="refine",
            constraints=[
                {
                    **_fact("power_kw", "20–25", product=None),
                    "unit": "kW",
                    "evidence": "20–25 кВт",
                }
            ],
        ),
        "disjoint-refinement-2",
    )

    active = _active_facts(rejected.state)
    assert len(active) == 1
    assert active[0].value == 15
    assert {
        item.reason_code for item in rejected.rejected_proposals
    } >= {"confirmed_fact_requires_explicit_correction"}


def test_refine_does_not_treat_discrete_cardinality_as_numeric_range() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("газовый котёл", "boilers")],
            constraints=[
                {
                    **_fact("circuits", 1),
                    "evidence": "одноконтурный",
                }
            ],
        ),
        "discrete-range-refinement-1",
    )

    rejected = _reduce(
        first.state,
        _turn(
            operation="refine",
            constraints=[
                {
                    **_fact("circuits", "1–2", product=None),
                    "evidence": "1–2 контура",
                }
            ],
        ),
        "discrete-range-refinement-2",
    )

    active = _active_facts(rejected.state)
    assert len(active) == 1
    assert active[0].value == 1
    assert {
        item.reason_code for item in rejected.rejected_proposals
    } >= {"confirmed_fact_requires_explicit_correction"}


def test_continue_can_narrow_explicit_numeric_choices_without_correction() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("циркуляционный насос", "pumps")],
            constraints=[
                {
                    **_fact("mounting_length_mm", "130 или 180"),
                    "unit": "mm",
                    "evidence": "130 или 180 мм",
                }
            ],
        ),
        "choice-refinement-1",
    )

    narrowed = _reduce(
        first.state,
        _turn(
            operation="continue",
            acts=["select"],
            constraints=[
                {
                    **_fact("mounting_length_mm", 130, product=None),
                    "unit": "mm",
                    "evidence": "именно 130 мм",
                }
            ],
        ),
        "choice-refinement-2",
    )

    active = _active_facts(narrowed.state)
    assert len(active) == 1
    assert active[0].value == 130
    assert active[0].replaces_fact_id is not None
    assert "constraint_corrected" in {
        event.event_type for event in narrowed.events
    }


def test_implicit_context_word_cannot_overwrite_locked_product_type() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
        ),
        "locked-1",
    )
    second = _reduce(
        first.state,
        _turn(
            products=[_product("радиатор", "radiators", "context")],
        ),
        "locked-2",
    )

    assert _active_goal(second.state).canonical_type == "насос"
    assert _active_goal(second.state).type_locked is True


def test_unknown_never_becomes_a_value() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("mounting_length", status="unknown")],
        ),
        "unknown",
    )

    fact = _active_facts(result.state)[0]
    assert fact.status == ConstraintStatus.UNKNOWN
    assert fact.value is None
    assert "constraint_marked_unknown" in {
        event.event_type for event in result.events
    }


@pytest.mark.parametrize("missing_status", ["unknown", "refused", "deferred"])
@pytest.mark.parametrize("known_first", [True, False])
def test_same_turn_known_value_wins_over_missing_status_duplicate(
    missing_status: str,
    known_first: bool,
) -> None:
    known = {
        **_fact("power_kw", 35),
        "unit": "kW",
        "evidence": "35 kW",
    }
    unavailable = {
        **_fact("power_kw", status=missing_status),
        "unit": "kW",
        "evidence": f"power status {missing_status}",
    }
    constraints = (
        [known, unavailable]
        if known_first
        else [unavailable, known]
    )
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("boiler", "boilers")],
            constraints=constraints,
            ambiguities=[
                {
                    "kind": "power_kw",
                    "description": "Power is needed",
                    "evidence": "power",
                }
            ],
        ),
        f"known-{missing_status}-{known_first}",
    )

    facts = _active_facts(result.state)
    assert len(facts) == 1
    assert facts[0].name == "power_kw"
    assert facts[0].status == ConstraintStatus.KNOWN
    assert facts[0].value == 35
    assert {
        item.reason_code for item in result.rejected_proposals
    } >= {"current_known_fact_preferred_over_missing_status_duplicate"}
    assert (
        SellerPolicy().decide(result.state).primary.kind
        != NextActionKind.ASK_DECISION_CHANGING_QUESTION
    )

    duplicate = _reduce(
        result.state,
        _turn(operation="continue"),
        f"known-{missing_status}-{known_first}",
    )
    assert duplicate.state == result.state
    assert [event.event_type for event in duplicate.events] == [
        "turn_ignored_as_duplicate"
    ]


@pytest.mark.parametrize("missing_status", ["unknown", "refused", "deferred"])
def test_explicit_missing_status_correction_is_kept_without_current_known(
    missing_status: str,
) -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("boiler", "boilers")],
            constraints=[
                {
                    **_fact("power_kw", 35),
                    "unit": "kW",
                    "evidence": "35 kW",
                }
            ],
        ),
        f"missing-correction-{missing_status}-1",
    )
    corrected = _reduce(
        first.state,
        _turn(
            operation="correct",
            constraints=[
                {
                    **_fact("power_kw", status=missing_status, product=None),
                    "unit": "kW",
                    "evidence": f"explicit {missing_status}",
                }
            ],
        ),
        f"missing-correction-{missing_status}-2",
    )

    active = _active_facts(corrected.state)
    assert len(active) == 1
    assert active[0].status.value == missing_status
    assert active[0].value is None
    assert active[0].replaces_fact_id is not None


@pytest.mark.parametrize("missing_status", ["unknown", "refused", "deferred"])
@pytest.mark.parametrize("known_first", [True, False])
def test_explicit_known_correction_wins_over_same_turn_missing_duplicate(
    missing_status: str,
    known_first: bool,
) -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("boiler", "boilers")],
            constraints=[{**_fact("power_kw", 35), "unit": "kW"}],
        ),
        f"known-correction-{missing_status}-{known_first}-1",
    )
    known = {
        **_fact("power_kw", 40, product=None),
        "unit": "kW",
        "evidence": "correct value 40 kW",
    }
    unavailable = {
        **_fact("power_kw", status=missing_status, product=None),
        "unit": "kW",
        "evidence": f"duplicate {missing_status}",
    }
    corrected = _reduce(
        first.state,
        _turn(
            operation="correct",
            constraints=(
                [known, unavailable]
                if known_first
                else [unavailable, known]
            ),
        ),
        f"known-correction-{missing_status}-{known_first}-2",
    )

    active = _active_facts(corrected.state)
    assert len(active) == 1
    assert active[0].status == ConstraintStatus.KNOWN
    assert active[0].value == 40
    assert active[0].replaces_fact_id is not None
    assert "constraint_corrected" in {
        event.event_type for event in corrected.events
    }


def test_productless_pump_facts_do_not_contaminate_active_filter_goal() -> None:
    selected_filter = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("water_filter", "filters")],
        ),
        "filter-applicability-1",
    )
    contaminated_follow_up = _reduce(
        selected_filter.state,
        _turn(
            operation="continue",
            constraints=[
                _fact("dynamic_water_level_m", 12, product=None),
                _fact("lift_height_m", 8, product=None),
                _fact("horizontal_run_m", 35, product=None),
            ],
        ),
        "filter-applicability-2",
    )

    assert _active_goal(contaminated_follow_up.state).canonical_type == "water_filter"
    assert _active_facts(contaminated_follow_up.state) == []
    rejected = [
        item
        for item in contaminated_follow_up.rejected_proposals
        if item.reason_code == "constraint_incompatible_with_product_goal"
    ]
    assert {item.details["fact_name"] for item in rejected} == {
        "dynamic_water_level_m",
        "lift_height_m",
        "horizontal_run_m",
    }
    plan = SellerPolicy().decide(contaminated_follow_up.state)
    assert not {
        "dynamic_water_level_m",
        "lift_height_m",
        "horizontal_run_m",
    } & set(plan.blocking_facts)


def test_product_kind_ontology_can_allow_fact_missing_from_category_schema() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("water_filter", "filters")],
            constraints=[
                {
                    **_fact("connection_size", "G1/2"),
                    "unit": None,
                }
            ],
        ),
        "filter-kind-fact",
    )

    assert [fact.name for fact in _active_facts(result.state)] == [
        "connection_size"
    ]


def test_v2_state_does_not_retain_phone_or_email_evidence() -> None:
    understanding = _turn(
        operation="new",
        acts=["select"],
        products=[_product("насос", "pumps")],
        constraints=[_fact("mounting_length", status="unknown")],
    )
    payload = understanding.model_dump(mode="json")
    payload["products"][0]["evidence"] = "насос +7 999 123-45-67"
    payload["constraints"][0]["evidence"] = "buyer@example.test"

    result = _reduce(
        None,
        TurnUnderstanding.model_validate(payload),
        "pii-redaction",
    )
    serialized = json.dumps(result.state.model_dump(mode="json"), ensure_ascii=False)

    assert "+7 999 123-45-67" not in serialized
    assert "buyer@example.test" not in serialized
    assert "[phone redacted]" in serialized
    assert "[email redacted]" in serialized


def test_refused_and_deferred_are_preserved_as_distinct_statuses() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
            constraints=[
                _fact("mounting_length", status="refused"),
                _fact("required_head", status="deferred"),
            ],
        ),
        "refused-deferred",
    )

    facts = {fact.name: fact for fact in _active_facts(result.state)}
    assert facts["mounting_length"].status == ConstraintStatus.REFUSED
    assert facts["required_head"].status == ConstraintStatus.DEFERRED
    assert all(fact.value is None for fact in facts.values())


@pytest.mark.parametrize("status", ["known", "unknown", "refused", "deferred"])
def test_policy_does_not_ask_for_resolved_or_unavailable_fact(status: str) -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("mounting_length", 180, status=status)],
            ambiguities=[
                {
                    "kind": "mounting_length",
                    "description": "Нужна монтажная длина",
                    "evidence": "монтажная длина",
                }
            ],
        ),
        f"no-repeat-{status}",
    )

    plan = SellerPolicy().decide(result.state)

    assert plan.primary.kind != NextActionKind.ASK_DECISION_CHANGING_QUESTION
    if status != "known":
        assert plan.primary.kind == NextActionKind.SHOW_PRELIMINARY_OPTIONS


@pytest.mark.parametrize("status", ["known", "unknown"])
def test_repeated_selection_and_same_fact_are_not_false_progress(status: str) -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("mounting_length", 180, status=status)],
        ),
        f"repeat-{status}-1",
    )
    repeated = _reduce(
        first.state,
        _turn(
            operation="continue",
            acts=["select"],
            constraints=[
                _fact(
                    "mounting_length",
                    180,
                    status=status,
                    product=None,
                )
            ],
        ),
        f"repeat-{status}-2",
    )

    assert len(repeated.state.tasks) == 1
    assert repeated.progress.primary == ProgressKind.NO_PROGRESS
    assert {item.reason_code for item in repeated.rejected_proposals} >= {
        "existing_selection_task_reused",
        "duplicate_constraint_fact",
    }
    plan = SellerPolicy().decide(repeated.state)
    assert plan.primary.kind != NextActionKind.ASK_DECISION_CHANGING_QUESTION


def test_direct_question_has_priority_and_selection_survives_as_secondary() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select", "check_price"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("connection_diameter", 25)],
        ),
        "direct-plus-selection",
    )

    plan = SellerPolicy().decide(result.state)

    assert plan.primary.kind == NextActionKind.ANSWER_DIRECT_QUESTION
    assert plan.secondary is not None
    assert plan.secondary.kind == NextActionKind.RECOMMEND_ONE


def test_price_and_stock_remain_two_independent_tasks() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["check_price", "check_stock"],
            products=[_product("котёл", "boilers")],
        ),
        "price-stock",
    )

    assert [task.act.value for task in result.state.tasks] == [
        "check_price",
        "check_stock",
    ]
    assert len(result.state.direct_questions) == 2


def test_two_products_create_two_linked_tasks_instead_of_last_product_wins() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("насос", "pumps"),
                _product("котёл", "boilers"),
            ],
        ),
        "two-products",
    )

    assert len(result.state.tasks) == 2
    assert len({task.target_goal_id for task in result.state.tasks}) == 2
    assert all(task.related_task_ids for task in result.state.tasks)
    active_task = next(
        task for task in result.state.tasks
        if task.task_id == result.state.task_stack.active_task_id
    )
    assert active_task.target_goal_id == result.state.active_goal_id


def test_independent_selection_goals_execute_only_the_first_task_per_turn() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("pipe", "pipes"),
                _product("circulation_pump", "pumps"),
            ],
        ),
        "ordered-pipe-then-pump",
    )

    plan = SellerPolicy().decide(result.state, ordered_multi_goal=True)

    assert plan.primary.task_id == result.state.tasks[0].task_id
    assert plan.secondary is None
    assert "ordered_multi_goal_first_task_only" in plan.reason_codes
    assert result.state.tasks[1].status == TaskStatus.PENDING


def test_named_alternative_refocuses_retained_task_without_becoming_context() -> None:
    initial = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("gas_boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
        ),
        "replacement-focus-1",
    )
    pump_goal = next(
        goal
        for goal in initial.state.product_goals
        if goal.canonical_type == "circulation_pump"
    )
    pump_task = next(
        task
        for task in initial.state.tasks
        if task.target_goal_id == pump_goal.goal_id
    )

    continued = _reduce(
        initial.state,
        _turn(
            operation="continue",
            acts=["select"],
            products=[
                _product("circulation_pump", "pumps", "alternative"),
            ],
        ),
        "replacement-focus-2",
    )

    assert continued.state.active_goal_id == pump_goal.goal_id
    assert continued.state.task_stack.active_task_id == pump_task.task_id
    addressed = [
        task
        for task in continued.state.tasks
        if task.was_addressed_on(continued.state.turn_number)
    ]
    assert [task.task_id for task in addressed] == [pump_task.task_id]
    assert addressed[0].target_goal_id == pump_goal.goal_id
    assert next(
        goal
        for goal in continued.state.product_goals
        if goal.goal_id == pump_goal.goal_id
    ).role.value == "alternative"


def test_two_selection_tasks_keep_first_search_and_second_decision_question() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("pipe", "pipes"),
                _product("boiler", "boilers"),
            ],
        ),
        "two-selection-actions",
    )
    first_task, second_task = result.state.tasks
    readiness = (
        TaskReadinessAssessment(
            task_id=first_task.task_id,
            goal_id=first_task.target_goal_id,
            contract_id="pipe.ppr.v1",
            product_kind=ProductKind.PIPE,
            status=ReadinessStatus.EXACT_READY,
        ),
        TaskReadinessAssessment(
            task_id=second_task.task_id,
            goal_id=second_task.target_goal_id,
            contract_id="boiler.generic.v1",
            product_kind=ProductKind.BOILER,
            status=ReadinessStatus.NEEDS_DECISION_FACT,
            missing_decision_facts=("power_kw",),
            recommended_question_fact="power_kw",
        ),
    )

    plan = SellerPolicy().decide(
        result.state,
        readiness_assessments=readiness,
    )

    assert plan.primary.kind == NextActionKind.RECOMMEND_ONE
    assert plan.primary.task_id == first_task.task_id
    assert plan.secondary is not None
    assert plan.secondary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert plan.secondary.task_id == second_task.task_id
    assert plan.secondary.fact_name == "power_kw"
    assert plan.task_ids == (first_task.task_id, second_task.task_id)

    explained = SellerPolicy().decide(
        result.state,
        readiness_assessments=readiness,
        strategy_directives=(
            StrategyDirective(
                task_id=second_task.task_id,
                strategy=ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
                fact_name="power_kw",
                reason_codes=("offer_learn_method_for_second_task",),
            ),
        ),
    )
    assert explained.primary.kind == NextActionKind.RECOMMEND_ONE
    assert explained.primary.task_id == first_task.task_id
    assert explained.secondary is not None
    assert explained.secondary.kind == NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
    assert explained.secondary.task_id == second_task.task_id
    assert explained.secondary.fact_name == "power_kw"


def test_pending_answer_readdresses_original_compound_task_and_advances_sibling() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("gas_boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
            constraints=[
                {**_fact("power_kw", 35, product=0), "unit": "kW"},
                _fact("diameter_mm", 30, product=1),
                {**_fact("max_head_m", 8, product=1), "unit": "m"},
            ],
        ),
        "compound-pending-1",
    )
    boiler_task, pump_task = first.state.tasks
    initial_readiness = (
        TaskReadinessAssessment(
            task_id=boiler_task.task_id,
            goal_id=boiler_task.target_goal_id,
            contract_id="boiler.gas.v1",
            product_kind=ProductKind.GAS_BOILER,
            status=ReadinessStatus.NEEDS_DECISION_FACT,
            missing_decision_facts=("circuits",),
            recommended_question_fact="circuits",
        ),
        TaskReadinessAssessment(
            task_id=pump_task.task_id,
            goal_id=pump_task.target_goal_id,
            contract_id="pump.circulation.v1",
            product_kind=ProductKind.CIRCULATION_PUMP,
            status=ReadinessStatus.NEEDS_DECISION_FACT,
            missing_decision_facts=("mounting_length_mm",),
            recommended_question_fact="mounting_length_mm",
        ),
    )
    initial_plan = SellerPolicy().decide(
        first.state,
        readiness_assessments=initial_readiness,
    )
    recorded = record_policy_decision(
        first,
        initial_plan,
        TurnMetadata(turn_id="compound-pending-1"),
    )

    answered = _reduce(
        recorded.state,
        _turn(
            operation="refine",
            acts=[],
            products=[_product("gas_boiler", "boilers")],
            constraints=[
                {
                    **_fact("circuits", "single", product=0),
                    "unit": None,
                }
            ],
            pending_answer=True,
        ),
        "compound-pending-2",
    )
    active_circuits = next(
        fact
        for fact in answered.state.constraints
        if fact.active and fact.name == "circuits"
    )
    assert active_circuits.task_id == boiler_task.task_id
    assert next(
        task
        for task in answered.state.tasks
        if task.task_id == boiler_task.task_id
    ).was_addressed_on(2)
    assert not any(task.act.value == "explain" for task in answered.state.tasks)

    continued_readiness = (
        initial_readiness[0].model_copy(
            update={
                "status": ReadinessStatus.EXACT_READY,
                "missing_decision_facts": (),
                "recommended_question_fact": None,
            }
        ),
        initial_readiness[1],
    )
    continued_plan = SellerPolicy().decide(
        answered.state,
        readiness_assessments=continued_readiness,
    )
    assert continued_plan.primary.kind == NextActionKind.RECOMMEND_ONE
    assert continued_plan.primary.task_id == boiler_task.task_id
    assert continued_plan.secondary is not None
    assert continued_plan.secondary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert continued_plan.secondary.task_id == pump_task.task_id
    assert continued_plan.secondary.fact_name == "mounting_length_mm"
    assert continued_plan.task_ids == (boiler_task.task_id, pump_task.task_id)


def test_delivered_secondary_question_binds_unscoped_fact_to_its_product_task() -> None:
    """R04: ``250 mm`` belongs to the pump question, not active boiler work."""

    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("gas_boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
            constraints=[
                {**_fact("power_kw", 35, product=0), "unit": "kW"},
                _fact("diameter_mm", 30, product=1),
                {**_fact("max_head_m", 8, product=1), "unit": "m"},
            ],
        ),
        "delivered-question-r04-1",
    )
    boiler_task, pump_task = first.state.tasks
    refined = _reduce(
        first.state,
        _turn(
            operation="refine",
            products=[_product("gas_boiler", "boilers")],
            constraints=[
                {**_fact("circuits", 1, product=0), "unit": None},
            ],
        ),
        "delivered-question-r04-2",
    )
    policy = SellerPolicy().decide(
        refined.state,
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=boiler_task.task_id,
                goal_id=boiler_task.target_goal_id,
                contract_id="boiler.gas.v1",
                product_kind=ProductKind.GAS_BOILER,
                status=ReadinessStatus.EXACT_READY,
            ),
            TaskReadinessAssessment(
                task_id=pump_task.task_id,
                goal_id=pump_task.target_goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                missing_decision_facts=("mounting_length_mm",),
                recommended_question_fact="mounting_length_mm",
            ),
        ),
    )
    assert policy.primary.task_id == boiler_task.task_id
    assert policy.secondary is not None
    assert policy.secondary.task_id == pump_task.task_id

    question = QuestionPlan(
        question_id="question-r04-mounting-length",
        task_id=pump_task.task_id,
        fact_name="mounting_length_mm",
        decision_impact_code="product_contract_requires_decision_fact",
        expected_unit="mm",
    )
    next_step = NextStepPlan(
        next_step_id="next-r04-mounting-length",
        kind=NextStepKind.ASK_DECISION_FACT,
        task_id=pump_task.task_id,
        fact_name="mounting_length_mm",
        contract_fact_recognized=True,
        fact_decision_changing=True,
        fact_required_for_exact=True,
    )
    answer_plan = AnswerPlan(
        plan_id="answer-r04-pump-question",
        turn_id="delivered-question-r04-2",
        turn_number=refined.state.turn_number,
        task_ids=(boiler_task.task_id, pump_task.task_id),
        goal_ids=(boiler_task.target_goal_id, pump_task.target_goal_id),
        primary_action=policy.primary.kind,
        secondary_action=policy.secondary.kind,
        status=AnswerPlanStatus.READY,
        sections=(
            AnswerSection(
                section_id="section-r04-question",
                kind=AnswerSectionKind.QUESTION,
                item_ids=(question.question_id,),
            ),
            AnswerSection(
                section_id="section-r04-next",
                kind=AnswerSectionKind.NEXT_STEP,
                item_ids=(next_step.next_step_id,),
            ),
        ),
        question=question,
        next_step=next_step,
        semantic_signature="signature-r04-pump-question",
    )
    shadow = record_answer_shadow(
        refined,
        AnswerPlanningResult(status="planned", answer_plan=answer_plan),
        AnswerValidationResult(
            status="accepted",
            plan_id=answer_plan.plan_id,
            accepted_segment_ids=("segment-r04-question",),
        ),
        policy,
        (),
        TurnMetadata(turn_id="delivered-question-r04-2"),
    )
    summary = shadow.state.answer_plan_summary
    assert summary is not None
    assert summary.question_id == question.question_id
    assert summary.question_task_id == pump_task.task_id
    assert summary.question_goal_id == pump_task.target_goal_id
    assert summary.delivery_status == ShadowDeliveryStatus.SHADOW_NOT_DELIVERED

    # A merely planned question is not conversational evidence.  Without a
    # commit, the same unscoped fact is still evaluated against active boiler
    # work and is rejected by the typed product applicability rule.
    undelivered_answer = _reduce(
        shadow.state,
        _turn(
            constraints=[
                _fact("mounting_length_mm", 250, product=None),
            ],
        ),
        "delivered-question-r04-shadow-only",
    )
    assert not any(
        fact.active and fact.name == "mounting_length_mm"
        for fact in undelivered_answer.state.constraints
    )
    assert any(
        item.reason_code == "constraint_incompatible_with_product_goal"
        for item in undelivered_answer.rejected_proposals
    )

    delivered = record_response_delivery(
        shadow.state,
        TurnMetadata(turn_id="delivered-question-r04-2"),
        plan_id=answer_plan.plan_id,
        response_digest="digest-r04-pump-question",
        delivery_id="delivery-r04-pump-question",
        live_epoch_id="epoch-r04",
    )
    assert delivered.state.task_stack.active_task_id == pump_task.task_id
    assert delivered.state.active_goal_id == pump_task.target_goal_id
    assert next(
        task
        for task in delivered.state.tasks
        if task.task_id == boiler_task.task_id
    ).status == TaskStatus.PENDING

    answered = _reduce(
        delivered.state,
        _turn(
            operation="continue",
            acts=["check_stock"],
            constraints=[
                _fact("mounting_length_mm", 250, product=None),
            ],
            pending_answer=False,
        ),
        "delivered-question-r04-3",
    )
    mounting_length = next(
        fact
        for fact in answered.state.constraints
        if fact.active and fact.name == "mounting_length_mm"
    )
    assert mounting_length.value == 250
    assert mounting_length.goal_id == pump_task.target_goal_id
    assert mounting_length.task_id == pump_task.task_id
    assert mounting_length.source == "delivered_question_answer"
    assert all(
        task.target_goal_id == pump_task.target_goal_id
        for task in answered.state.tasks
        if task.was_addressed_on(answered.state.turn_number)
    )
    assert not any(
        item.reason_code == "constraint_incompatible_with_product_goal"
        for item in answered.rejected_proposals
    )

    delivered_summary = delivered.state.answer_plan_summary
    assert delivered_summary is not None
    with_committed_boiler_cards = delivered.state.model_copy(
        update={
            "answer_plan_summary": delivered_summary.model_copy(
                update={
                    "presented_candidates": (
                        PresentedCandidateSummary(
                            sku="BOILER-35",
                            name="Previously presented boiler",
                            product_kind=ProductKind.GAS_BOILER,
                            role="base_product",
                            task_id=boiler_task.task_id,
                            goal_id=boiler_task.target_goal_id,
                            search_plan_id="boiler-search",
                            source_turn=2,
                        ),
                    )
                }
            )
        }
    )
    pump_followup = _reduce(
        with_committed_boiler_cards,
        _turn(
            operation="continue",
            acts=[],
            constraints=[_fact("mounting_length_mm", 250, product=None)],
        ),
        "delivered-question-r04-pump-only",
    )
    scoped_policy = SellerPolicy().decide(
        pump_followup.state,
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=boiler_task.task_id,
                goal_id=boiler_task.target_goal_id,
                contract_id="boiler.gas.v1",
                product_kind=ProductKind.GAS_BOILER,
                status=ReadinessStatus.EXACT_READY,
            ),
            TaskReadinessAssessment(
                task_id=pump_task.task_id,
                goal_id=pump_task.target_goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.EXACT_READY,
            ),
        ),
    )
    assert scoped_policy.primary.task_id == pump_task.task_id
    assert scoped_policy.primary.kind == NextActionKind.RECOMMEND_ONE
    assert scoped_policy.secondary is None
    assert scoped_policy.task_ids == (pump_task.task_id,)


def test_old_answer_plan_summary_without_question_scope_restores_safely() -> None:
    payload = AnswerPlanSummary(
        plan_id="old-answer-plan",
        semantic_signature="old-signature",
        primary_action=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        question_fact="mounting_length_mm",
        next_step_kind="ask_decision_fact",
        validation_status="accepted",
        source_turn=1,
    ).model_dump(mode="json")
    payload.pop("question_id")
    payload.pop("question_task_id")
    payload.pop("question_goal_id")

    restored = AnswerPlanSummary.model_validate(payload)

    assert restored.question_id is None
    assert restored.question_task_id is None
    assert restored.question_goal_id is None


def test_delivered_question_does_not_capture_fact_after_task_switch() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "stale-question-switch-1",
    )
    pump_task = selected.state.tasks[0]
    with_delivered_question = selected.state.model_copy(
        update={
            "answer_plan_summary": AnswerPlanSummary(
                plan_id="pump-diameter-question",
                semantic_signature="pump-diameter-question-signature",
                task_ids=(pump_task.task_id,),
                primary_action=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
                question_fact="diameter_mm",
                question_id="question-pump-diameter",
                question_task_id=pump_task.task_id,
                question_goal_id=pump_task.target_goal_id,
                next_step_kind="ask_decision_fact",
                validation_status="accepted",
                delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
                source_turn=1,
            )
        }
    )
    switched = _reduce(
        with_delivered_question,
        _turn(
            operation="switch",
            acts=["select"],
            products=[_product("pipe", "pipes")],
        ),
        "stale-question-switch-2",
    )
    assert next(
        task for task in switched.state.tasks if task.task_id == pump_task.task_id
    ).status == TaskStatus.SUSPENDED

    answered = _reduce(
        switched.state,
        _turn(
            operation="continue",
            constraints=[_fact("diameter_mm", 50, product=None)],
        ),
        "stale-question-switch-3",
    )
    diameter = next(
        fact
        for fact in answered.state.constraints
        if fact.active and fact.name == "diameter_mm"
    )
    assert diameter.goal_id == answered.state.active_goal_id
    assert diameter.goal_id != pump_task.target_goal_id
    assert diameter.source != "delivered_question_answer"


def test_noisy_explain_on_pending_answer_cannot_hide_ready_selection() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("gas_boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
        ),
        "compound-noisy-answer-1",
    )
    boiler_task, pump_task = first.state.tasks
    initial_plan = SellerPolicy().decide(
        first.state,
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=boiler_task.task_id,
                goal_id=boiler_task.target_goal_id,
                contract_id="boiler.gas.v1",
                product_kind=ProductKind.GAS_BOILER,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                missing_decision_facts=("circuits",),
                recommended_question_fact="circuits",
            ),
            TaskReadinessAssessment(
                task_id=pump_task.task_id,
                goal_id=pump_task.target_goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                missing_decision_facts=("mounting_length_mm",),
                recommended_question_fact="mounting_length_mm",
            ),
        ),
    )
    recorded = record_policy_decision(
        first,
        initial_plan,
        TurnMetadata(turn_id="compound-noisy-answer-1"),
    )

    answered = _reduce(
        recorded.state,
        _turn(
            operation="refine",
            acts=["explain"],
            products=[_product("gas_boiler", "boilers")],
            constraints=[
                {
                    **_fact("circuits", "single", product=0),
                    "unit": None,
                }
            ],
            pending_answer=True,
        ),
        "compound-noisy-answer-2",
    )
    policy = SellerPolicy().decide(
        answered.state,
        readiness_assessments=(
            TaskReadinessAssessment(
                task_id=boiler_task.task_id,
                goal_id=boiler_task.target_goal_id,
                contract_id="boiler.gas.v1",
                product_kind=ProductKind.GAS_BOILER,
                status=ReadinessStatus.EXACT_READY,
            ),
            TaskReadinessAssessment(
                task_id=pump_task.task_id,
                goal_id=pump_task.target_goal_id,
                contract_id="pump.circulation.v1",
                product_kind=ProductKind.CIRCULATION_PUMP,
                status=ReadinessStatus.NEEDS_DECISION_FACT,
                missing_decision_facts=("mounting_length_mm",),
                recommended_question_fact="mounting_length_mm",
            ),
        ),
    )
    assert policy.primary.kind == NextActionKind.RECOMMEND_ONE
    assert policy.primary.task_id == boiler_task.task_id
    assert policy.secondary is not None
    assert policy.secondary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert policy.secondary.task_id == pump_task.task_id
    assert policy.secondary.fact_name == "mounting_length_mm"
    assert boiler_task.task_id in policy.task_ids
    assert (
        "bare_explanation_deferred_missing_typed_information_request"
        in policy.reason_codes
    )


def test_controller_assesses_linked_pending_product_after_foreground_answer() -> None:
    controller = DialogueControllerV2()
    first = controller.run(
        None,
        _accepted(
            _turn(
                operation="new",
                acts=["select"],
                products=[
                    _product("gas_boiler", "boilers", "alternative"),
                    _product("circulation_pump", "pumps", "alternative"),
                ],
                constraints=[
                    {**_fact("power_kw", 35, product=0), "unit": "kW"},
                    _fact("diameter_mm", 30, product=1),
                    {**_fact("max_head_m", 8, product=1), "unit": "m"},
                ],
            )
        ),
        TurnMetadata(turn_id="controller-compound-1"),
        product_contracts_enabled=True,
    )
    assert first.next_action_plan is not None
    assert first.next_action_plan.primary.fact_name == "circuits"
    assert first.next_action_plan.secondary is not None
    assert first.next_action_plan.secondary.fact_name == "mounting_length_mm"
    boiler_task_id = first.next_action_plan.primary.task_id
    pump_task_id = first.next_action_plan.secondary.task_id

    second = controller.run(
        first.state_after,
        _accepted(
            _turn(
                operation="refine",
                acts=[],
                products=[_product("gas_boiler", "boilers")],
                constraints=[
                    {
                        **_fact("circuits", "single", product=0),
                        "unit": None,
                    }
                ],
                pending_answer=True,
            )
        ),
        TurnMetadata(turn_id="controller-compound-2"),
        product_contracts_enabled=True,
    )

    assert second.next_action_plan is not None
    assert second.next_action_plan.primary.kind == NextActionKind.RECOMMEND_ONE
    assert second.next_action_plan.primary.task_id == boiler_task_id
    assert second.next_action_plan.secondary is not None
    assert second.next_action_plan.secondary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert second.next_action_plan.secondary.task_id == pump_task_id
    assert second.next_action_plan.secondary.fact_name == "mounting_length_mm"
    assert second.catalog_planning is not None
    readiness_by_task = {
        item.task_id: item
        for item in second.catalog_planning.readiness_assessments
    }
    assert readiness_by_task[boiler_task_id].status == ReadinessStatus.EXACT_READY
    assert readiness_by_task[pump_task_id].status == ReadinessStatus.NEEDS_DECISION_FACT


def test_information_requests_bind_to_exact_product_goal_and_act_without_evidence() -> None:
    understanding = _turn(
        operation="new",
        acts=["explain"],
        products=[
            _product("circulation_pump", "pumps"),
            _product("gas_boiler", "boilers"),
        ],
        information_requests=[
            _information_request(
                fact_name="mounting_length_mm",
                purpose="determination_method",
                outputs=["instruction"],
                product=0,
            ),
            _information_request(
                fact_name="power_kw",
                purpose="decision_relevance",
                outputs=["explanation"],
                product=1,
            ),
        ],
    )

    result = _reduce(None, understanding, "information-two-products")

    assert len(result.state.information_requests) == 2
    goals_by_type = {
        goal.canonical_type: goal.goal_id for goal in result.state.product_goals
    }
    for request, product_type in zip(
        result.state.information_requests,
        ("circulation_pump", "gas_boiler"),
        strict=True,
    ):
        task = next(
            item for item in result.state.tasks if item.task_id == request.task_id
        )
        assert task.act.value == "explain"
        assert request.goal_id == goals_by_type[product_type]
        assert task.target_goal_id == request.goal_id
        assert request.status == InformationRequestStatus.PENDING
        assert "evidence" not in request.model_dump(mode="json")
    assert [
        event.event_type
        for event in result.events
        if event.event_type == "information_request_registered"
    ] == ["information_request_registered", "information_request_registered"]
    assert result.progress.primary == ProgressKind.INFORMATION_REQUEST_REGISTERED

    duplicate = _reduce(
        result.state,
        understanding,
        "information-two-products",
    )
    assert duplicate.state is result.state
    assert len(duplicate.state.information_requests) == 2


@pytest.mark.parametrize(
    ("purpose", "expected_kind"),
    [
        ("determination_method", NextActionKind.EXPLAIN_HOW_TO_FIND_FACT),
        ("meaning", NextActionKind.EXPLAIN_TERM_OR_METHOD),
        ("decision_relevance", NextActionKind.EXPLAIN_TERM_OR_METHOD),
        ("compatibility", NextActionKind.EXPLAIN_TERM_OR_METHOD),
        ("value", NextActionKind.ANSWER_DIRECT_QUESTION),
    ],
)
def test_typed_information_request_has_policy_priority_without_fact_guessing(
    purpose: str,
    expected_kind: NextActionKind,
) -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name=None,
                    purpose=purpose,
                    outputs=(
                        ["instruction"]
                        if purpose == "determination_method"
                        else ["explanation"]
                    ),
                )
            ],
        ),
        f"information-purpose-{purpose}",
    )

    plan = SellerPolicy().decide(result.state)

    request = result.state.information_requests[0]
    assert plan.primary.kind == expected_kind
    assert plan.primary.information_request_id == request.request_id
    assert plan.primary.information_purpose == InformationPurpose(purpose)
    assert plan.primary.fact_name is None
    assert plan.primary.requested_outputs == request.requested_outputs
    assert plan.primary.output_relation == InformationOutputRelation.ALL
    assert plan.reason_codes[0] == "typed_information_request_has_priority"


def test_presented_candidate_information_scope_survives_reducer_and_policy() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="value",
                    outputs=["explanation"],
                    subject_scope="presented_candidates",
                )
            ],
        ),
        "candidate-information-scope",
    )

    request = result.state.information_requests[0]
    plan = SellerPolicy().decide(result.state)
    assert request.subject_scope.value == "presented_candidates"
    assert plan.primary.information_subject_scope.value == "presented_candidates"
    assert plan.primary.information_request_id == request.request_id


def test_verified_information_request_stays_pending_at_policy_boundary() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["get_link"],
            products=[_product("gas_boiler", "boilers")],
            information_requests=[
                _information_request(
                    fact_name=None,
                    purpose="provenance",
                    outputs=["verified_link"],
                    act="get_link",
                    source_kind="manufacturer_documentation",
                )
            ],
        ),
        "information-verified-unavailable",
    )
    plan = SellerPolicy().decide(reduction.state)

    assert plan.primary.kind == NextActionKind.STATE_CAPABILITY_BOUNDARY
    assert plan.primary.reason_code == "verified_information_source_unavailable"
    assert plan.primary.source_kind is not None
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id="information-verified-unavailable"),
    )
    assert (
        recorded.state.information_requests[0].status
        == InformationRequestStatus.PENDING
    )
    assert not any(
        event.event_type == "information_request_unavailable"
        for event in recorded.events
    )


def test_any_information_output_prefers_available_instruction_over_missing_link() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction", "verified_link"],
                    relation="any",
                    source_kind="technical_documentation",
                )
            ],
        ),
        "information-any-instruction",
    )

    plan = SellerPolicy().decide(reduction.state)

    assert plan.primary.kind == NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.primary.reason_code == "typed_information_instruction_selected"
    assert plan.primary.output_relation == InformationOutputRelation.ANY
    assert plan.primary.requested_outputs == (
        RequestedInformationOutput.INSTRUCTION,
        RequestedInformationOutput.VERIFIED_LINK,
    )


def test_any_instruction_without_typed_fact_does_not_guess_what_to_explain() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("gas_boiler", "boilers")],
            information_requests=[
                _information_request(
                    fact_name=None,
                    purpose="provenance",
                    outputs=["instruction", "verified_link"],
                    relation="any",
                    source_kind="manufacturer_documentation",
                )
            ],
        ),
        "information-any-no-fact",
    )

    plan = SellerPolicy().decide(reduction.state)

    assert plan.primary.fact_name is None
    assert plan.primary.kind == NextActionKind.STATE_CAPABILITY_BOUNDARY
    assert plan.primary.reason_code == "verified_information_source_unavailable"


def test_two_current_information_requests_fill_primary_and_secondary_only() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                ),
                _information_request(
                    fact_name="max_head_m",
                    purpose="determination_method",
                    outputs=["instruction"],
                ),
            ],
        ),
        "information-two-deliverables",
    )

    plan = SellerPolicy().decide(reduction.state)

    first_request, second_request = reduction.state.information_requests
    assert plan.primary.information_request_id == first_request.request_id
    assert plan.primary.kind == NextActionKind.EXPLAIN_TERM_OR_METHOD
    assert plan.secondary is not None
    assert plan.secondary.information_request_id == second_request.request_id
    assert plan.secondary.kind == NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
    assert len(
        {
            item.information_request_id
            for item in (plan.primary, plan.secondary)
            if item is not None
        }
    ) == 2


def test_pending_information_request_from_old_turn_does_not_preempt_new_turn() -> None:
    requested = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                )
            ],
        ),
        "information-current-only-1",
    )
    continued = _reduce(
        requested.state,
        _turn(operation="continue", acts=["gratitude"]),
        "information-current-only-2",
    )

    plan = SellerPolicy().decide(continued.state)

    assert requested.state.information_requests[0].status == InformationRequestStatus.PENDING
    assert plan.primary.information_request_id is None
    assert plan.primary.kind == NextActionKind.CLOSE_TASK


def test_information_request_keeps_existing_selection_as_secondary() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "information-selection-1",
    )
    selection_task = selected.state.tasks[0]
    explained = _reduce(
        selected.state,
        _turn(
            operation="continue",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction"],
                )
            ],
        ),
        "information-selection-2",
    )
    readiness = TaskReadinessAssessment(
        task_id=selection_task.task_id,
        goal_id=selection_task.target_goal_id,
        contract_id="pump.circulation.v1",
        product_kind=ProductKind.CIRCULATION_PUMP,
        status=ReadinessStatus.NEEDS_DECISION_FACT,
        missing_decision_facts=("mounting_length_mm",),
        recommended_question_fact="mounting_length_mm",
    )

    plan = SellerPolicy().decide(
        explained.state,
        readiness_assessments=(readiness,),
    )

    assert plan.primary.kind == NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
    assert plan.primary.fact_name == "mounting_length_mm"
    assert plan.secondary is not None
    assert plan.secondary.task_id == selection_task.task_id
    assert plan.secondary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert selection_task.task_id in plan.task_ids


def test_delivered_information_action_resolves_request_through_reducer() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction"],
                )
            ],
        ),
        "information-resolved",
    )
    plan = SellerPolicy().decide(reduction.state)
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id="information-resolved"),
    )
    state = recorded.state.model_copy(
        update={
            "answer_plan_summary": AnswerPlanSummary(
                plan_id="answer-information",
                semantic_signature="typed-information",
                task_ids=plan.task_ids,
                primary_action=plan.primary.kind,
                next_step_kind="explain_how_to_find_fact",
                validation_status="accepted",
                delivery_status=ShadowDeliveryStatus.SELECTED,
                information_request_id=plan.primary.information_request_id,
                information_requested_outputs=plan.primary.requested_outputs,
                information_output_relation=plan.primary.output_relation,
                information_fulfilled_outputs=(
                    RequestedInformationOutput.INSTRUCTION,
                ),
                source_turn=recorded.state.turn_number,
            )
        }
    )

    delivered = record_response_delivery(
        state,
        TurnMetadata(turn_id="information-resolved"),
        plan_id="answer-information",
        response_digest="digest-information",
        delivery_id="delivery-information",
        live_epoch_id="epoch-information",
    )

    assert (
        delivered.state.information_requests[0].status
        == InformationRequestStatus.RESOLVED
    )
    assert any(
        event.event_type == "information_request_resolved"
        for event in delivered.events
    )


def test_delivery_resolves_only_information_request_rendered_as_next_step() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="decision_relevance",
                    outputs=["explanation"],
                ),
                _information_request(
                    fact_name="max_head_m",
                    purpose="determination_method",
                    outputs=["instruction"],
                ),
            ],
        ),
        "information-primary-only",
    )
    plan = SellerPolicy().decide(reduction.state)
    assert plan.secondary is not None
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id="information-primary-only"),
    )
    state = _with_information_summary(
        recorded.state,
        plan,
        plan_id="answer-primary-only",
        fulfilled=(RequestedInformationOutput.EXPLANATION,),
    )

    delivered = record_response_delivery(
        state,
        TurnMetadata(turn_id="information-primary-only"),
        plan_id="answer-primary-only",
        response_digest="digest-primary-only",
        delivery_id="delivery-primary-only",
        live_epoch_id="epoch-primary-only",
    )

    first, second = delivered.state.information_requests
    assert first.status == InformationRequestStatus.RESOLVED
    assert second.status == InformationRequestStatus.PENDING
    resolved_events = [
        event
        for event in delivered.events
        if event.event_type == "information_request_resolved"
    ]
    assert [event.request_id for event in resolved_events] == [first.request_id]


def test_rejected_or_undelivered_information_plan_does_not_close_request() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction"],
                )
            ],
        ),
        "information-rejected",
    )
    plan = SellerPolicy().decide(reduction.state)
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id="information-rejected"),
    )
    shadow_only = _with_information_summary(
        recorded.state,
        plan,
        plan_id="answer-shadow-only",
        fulfilled=(RequestedInformationOutput.INSTRUCTION,),
    )
    assert (
        shadow_only.information_requests[0].status
        == InformationRequestStatus.PENDING
    )

    rejected = _with_information_summary(
        recorded.state,
        plan,
        plan_id="answer-rejected",
        fulfilled=(RequestedInformationOutput.INSTRUCTION,),
        validation_status="rejected",
    )
    delivered = record_response_delivery(
        rejected,
        TurnMetadata(turn_id="information-rejected"),
        plan_id="answer-rejected",
        response_digest="digest-rejected",
        delivery_id="delivery-rejected",
        live_epoch_id="epoch-rejected",
    )
    assert (
        delivered.state.information_requests[0].status
        == InformationRequestStatus.PENDING
    )
    assert not any(
        event.event_type
        in {"information_request_resolved", "information_request_unavailable"}
        for event in delivered.events
    )


@pytest.mark.parametrize(
    ("relation", "expected_status"),
    [
        ("all", InformationRequestStatus.PENDING),
        ("any", InformationRequestStatus.RESOLVED),
    ],
)
def test_information_output_relation_controls_partial_delivery(
    relation: str,
    expected_status: InformationRequestStatus,
) -> None:
    turn_id = f"information-relation-{relation}"
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction", "verified_link"],
                    relation=relation,
                    source_kind="technical_documentation",
                )
            ],
        ),
        turn_id,
    )
    plan = SellerPolicy().decide(reduction.state)
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id=turn_id),
    )
    state = _with_information_summary(
        recorded.state,
        plan,
        plan_id=f"answer-relation-{relation}",
        fulfilled=(RequestedInformationOutput.INSTRUCTION,),
    )
    delivered = record_response_delivery(
        state,
        TurnMetadata(turn_id=turn_id),
        plan_id=f"answer-relation-{relation}",
        response_digest=f"digest-relation-{relation}",
        delivery_id=f"delivery-relation-{relation}",
        live_epoch_id=f"epoch-relation-{relation}",
    )
    assert delivered.state.information_requests[0].status == expected_status


def test_answer_shadow_projects_only_output_rendered_by_sole_next_step() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="determination_method",
                    outputs=["instruction", "verified_link"],
                    relation="any",
                    source_kind="technical_documentation",
                )
            ],
        ),
        "information-shadow-projection",
    )
    policy = SellerPolicy().decide(reduction.state)
    recorded = record_policy_decision(
        reduction,
        policy,
        TurnMetadata(turn_id="information-shadow-projection"),
    )
    action = policy.primary
    next_step = NextStepPlan(
        next_step_id="next-information-shadow-projection",
        kind=NextStepKind.EXPLAIN_HOW_TO_FIND_FACT,
        task_id=action.task_id,
        fact_name=action.fact_name,
        information_request_id=action.information_request_id,
        information_purpose=action.information_purpose,
        requested_outputs=action.requested_outputs,
        output_relation=action.output_relation,
        source_kind=action.source_kind,
        information_subject_scope=action.information_subject_scope,
        reason_codes=(action.reason_code,),
    )
    answer_plan = AnswerPlan(
        plan_id="answer-shadow-projection",
        turn_id="information-shadow-projection",
        turn_number=recorded.state.turn_number,
        task_ids=policy.task_ids,
        primary_action=action.kind,
        status=AnswerPlanStatus.READY,
        sections=(
            AnswerSection(
                section_id="section-shadow-projection",
                kind=AnswerSectionKind.NEXT_STEP,
                item_ids=(next_step.next_step_id,),
            ),
        ),
        next_step=next_step,
        semantic_signature="signature-shadow-projection",
    )
    shadow = record_answer_shadow(
        recorded,
        AnswerPlanningResult(status="planned", answer_plan=answer_plan),
        AnswerValidationResult(
            status="accepted",
            plan_id=answer_plan.plan_id,
            accepted_segment_ids=("segment-shadow-projection",),
        ),
        policy,
        (),
        TurnMetadata(turn_id="information-shadow-projection"),
    )

    summary = shadow.state.answer_plan_summary
    assert summary is not None
    assert summary.information_request_id == action.information_request_id
    assert summary.information_fulfilled_outputs == (
        RequestedInformationOutput.INSTRUCTION,
    )
    assert summary.information_unavailable_outputs == ()


def test_validated_delivered_information_boundary_marks_single_output_unavailable() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["get_link"],
            products=[_product("gas_boiler", "boilers")],
            information_requests=[
                _information_request(
                    fact_name=None,
                    purpose="provenance",
                    outputs=["verified_link"],
                    act="get_link",
                    source_kind="manufacturer_documentation",
                )
            ],
        ),
        "information-unavailable-delivered",
    )
    plan = SellerPolicy().decide(reduction.state)
    recorded = record_policy_decision(
        reduction,
        plan,
        TurnMetadata(turn_id="information-unavailable-delivered"),
    )
    assert (
        recorded.state.information_requests[0].status
        == InformationRequestStatus.PENDING
    )
    state = _with_information_summary(
        recorded.state,
        plan,
        plan_id="answer-unavailable-delivered",
        unavailable=(RequestedInformationOutput.VERIFIED_LINK,),
    )
    delivered = record_response_delivery(
        state,
        TurnMetadata(turn_id="information-unavailable-delivered"),
        plan_id="answer-unavailable-delivered",
        response_digest="digest-unavailable-delivered",
        delivery_id="delivery-unavailable-delivered",
        live_epoch_id="epoch-unavailable-delivered",
    )
    assert (
        delivered.state.information_requests[0].status
        == InformationRequestStatus.UNAVAILABLE
    )
    assert any(
        event.event_type == "information_request_unavailable"
        for event in delivered.events
    )


def test_progress_directive_preserves_typed_information_metadata() -> None:
    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                    subject_scope="presented_candidates",
                )
            ],
        ),
        "information-progress-metadata",
    )
    original = SellerPolicy().decide(reduction.state).primary
    changed = SellerPolicy().decide(
        reduction.state,
        strategy_directives=(
            StrategyDirective(
                task_id=original.task_id,
                strategy=ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                fact_name="different_fact_must_not_replace_request",
                reason_codes=("progress_boundary",),
            ),
        ),
    ).primary

    assert changed.kind == NextActionKind.STATE_CAPABILITY_BOUNDARY
    for field_name in (
        "task_id",
        "fact_name",
        "information_request_id",
        "information_purpose",
        "requested_outputs",
        "output_relation",
        "source_kind",
        "information_subject_scope",
    ):
        assert getattr(changed, field_name) == getattr(original, field_name)


def test_registering_information_request_is_typed_task_progress() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "information-progress-1",
    )
    second = _reduce(
        first.state,
        _turn(
            operation="continue",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                )
            ],
        ),
        "information-progress-2",
    )
    progress = assess_task_progress(
        first.state,
        second.state,
        TurnMetadata(turn_id="information-progress-2"),
    )

    assert len(progress) == 1
    assert progress[0].status.value == "progress"
    assert "task_state_changed" in progress[0].changes
    assert progress[0].consecutive_no_progress == 0


def test_old_pending_information_request_reselected_only_when_task_readdressed() -> None:
    requested = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                )
            ],
        ),
        "information-retry-1",
    )
    retried = _reduce(
        requested.state,
        _turn(
            operation="continue",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "information-retry-2",
    )
    plan = SellerPolicy().decide(retried.state)

    assert plan.primary.information_request_id == (
        requested.state.information_requests[0].request_id
    )
    assert "pending_typed_information_request_reselected" in plan.reason_codes


def test_old_candidate_scoped_request_is_not_reselected_without_committed_cards() -> None:
    requested = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="value",
                    outputs=["explanation"],
                    subject_scope="presented_candidates",
                )
            ],
        ),
        "candidate-information-retry-1",
    )
    retried = _reduce(
        requested.state,
        _turn(
            operation="continue",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "candidate-information-retry-2",
    )

    plan = SellerPolicy().decide(retried.state)
    assert plan.primary.information_request_id is None
    assert "pending_typed_information_request_reselected" not in plan.reason_codes


def test_information_request_state_serialization_is_backward_compatible() -> None:
    old_state = DialogueStateV2(
        last_policy=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
                reason_code="legacy-action",
            )
        )
    )
    old_payload = old_state.model_dump(mode="json")
    old_payload.pop("information_requests")
    for field_name in (
        "information_request_id",
        "information_purpose",
        "requested_outputs",
        "output_relation",
        "source_kind",
        "information_subject_scope",
    ):
        old_payload["last_policy"]["primary"].pop(field_name)
    # Pre-scope request records restore as customer-goal questions.
    for item in old_payload.get("information_requests", []):
        item.pop("subject_scope", None)
    restored_old = DialogueStateV2.model_validate(old_payload)
    assert restored_old.information_requests == ()
    assert restored_old.last_policy is not None
    assert restored_old.last_policy.primary.information_request_id is None
    assert restored_old.last_policy.primary.requested_outputs == ()

    reduction = _reduce(
        None,
        _turn(
            operation="new",
            acts=["explain"],
            products=[_product("circulation_pump", "pumps")],
            information_requests=[
                _information_request(
                    fact_name="mounting_length_mm",
                    purpose="meaning",
                    outputs=["explanation"],
                )
            ],
        ),
        "information-serialization",
    )
    payload = reduction.state.model_dump(mode="json")
    restored = DialogueStateV2.model_validate_json(
        reduction.state.model_dump_json()
    )
    assert restored == reduction.state
    assert "evidence" not in payload["information_requests"][0]


def test_bare_explanation_does_not_infer_unique_unresolved_fact() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("circulation_pump", "pumps")],
            constraints=[
                _fact("mounting_length_mm", None, status="unknown"),
            ],
        ),
        "method-target-1",
    )
    selected_task = next(
        task for task in selected.state.tasks if task.act.value == "select"
    )
    state_with_committed_cards = selected.state.model_copy(
        update={
            "answer_plan_summary": AnswerPlanSummary(
                plan_id="previous-card-plan",
                semantic_signature="previous-card-signature",
                task_ids=(selected_task.task_id,),
                primary_action=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
                next_step_kind="show_preliminary_options",
                validation_status="accepted",
                delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
                presented_candidates=(
                    PresentedCandidateSummary(
                        sku="PUMP-CANDIDATE",
                        name="Previously shown pump",
                        product_kind=ProductKind.CIRCULATION_PUMP,
                        role="base_product",
                        task_id=selected_task.task_id,
                        goal_id=selected_task.target_goal_id,
                        search_plan_id="previous-search-plan",
                        source_turn=1,
                    ),
                ),
                source_turn=1,
            )
        }
    )
    explained = _reduce(
        state_with_committed_cards,
        _turn(operation="continue", acts=["explain"]),
        "method-target-2",
    )
    selection_task = next(
        task for task in explained.state.tasks if task.act.value == "select"
    )
    explanation_task = next(
        task for task in explained.state.tasks if task.act.value == "explain"
    )
    readiness = (
        TaskReadinessAssessment(
            task_id=selection_task.task_id,
            goal_id=selection_task.target_goal_id,
            contract_id="pump.circulation.v1",
            product_kind=ProductKind.CIRCULATION_PUMP,
            status=ReadinessStatus.PRELIMINARY_READY,
            unknown_facts=("mounting_length_mm",),
            learn_method_code="measure_old_pump_mounting_length",
        ),
        TaskReadinessAssessment(
            task_id=explanation_task.task_id,
            goal_id=explanation_task.target_goal_id,
            status=ReadinessStatus.UNSUPPORTED,
        ),
    )

    plan = SellerPolicy().decide(
        explained.state,
        readiness_assessments=readiness,
    )

    assert plan.primary.kind == NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING
    assert plan.primary.task_id == explanation_task.task_id
    assert plan.primary.fact_name is None
    assert plan.primary.reason_code == (
        "explanation_missing_typed_information_request"
    )
    assert plan.secondary is None

    guarded_plan = SellerPolicy().decide(
        explained.state,
        readiness_assessments=readiness,
        strategy_directives=(
            StrategyDirective(
                task_id=explanation_task.task_id,
                strategy=ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
                fact_name="mounting_length_mm",
                reason_codes=("stale_progress_directive",),
            ),
        ),
    )
    assert (
        guarded_plan.primary.kind
        == NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING
    )
    assert guarded_plan.primary.fact_name is None

    ambiguous = readiness[0].model_copy(
        update={
            "unknown_facts": (),
            "missing_decision_facts": (
                "mounting_length_mm",
                "max_head_m",
            ),
        }
    )
    ambiguous_plan = SellerPolicy().decide(
        explained.state,
        readiness_assessments=(ambiguous, readiness[1]),
    )
    assert (
        ambiguous_plan.primary.kind
        == NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING
    )
    assert ambiguous_plan.primary.fact_name is None


def test_answer_shadow_does_not_carry_presented_cards_across_product_goal() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("circulation_pump", "pumps")],
        ),
        "presentation-scope-1",
    )
    pump_task = selected.state.tasks[0]
    with_committed_pump = selected.state.model_copy(
        update={
            "answer_plan_summary": AnswerPlanSummary(
                plan_id="pump-cards",
                semantic_signature="pump-cards-signature",
                task_ids=(pump_task.task_id,),
                primary_action=NextActionKind.SEARCH_EXACT,
                next_step_kind="continue_with_confirmed_facts",
                validation_status="accepted",
                delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
                presented_candidates=(
                    PresentedCandidateSummary(
                        sku="PUMP-1",
                        name="Pump card",
                        product_kind=ProductKind.CIRCULATION_PUMP,
                        role="base_product",
                        task_id=pump_task.task_id,
                        goal_id=pump_task.target_goal_id,
                        search_plan_id="pump-search",
                        source_turn=1,
                    ),
                ),
                source_turn=1,
            )
        }
    )
    switched = _reduce(
        with_committed_pump,
        _turn(
            operation="switch",
            acts=["explain"],
            products=[_product("gas_boiler", "boilers")],
        ),
        "presentation-scope-2",
    )
    boiler_task = next(
        task
        for task in switched.state.tasks
        if task.target_goal_id == switched.state.active_goal_id
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
            task_id=boiler_task.task_id,
            reason_code="typed_test_wait",
        ),
        task_ids=(boiler_task.task_id,),
    )
    next_step = NextStepPlan(
        next_step_id="presentation-scope-wait",
        kind=NextStepKind.WAIT_FOR_CUSTOMER,
        task_id=boiler_task.task_id,
        reason_codes=("typed_test_wait",),
    )
    answer_plan = AnswerPlan(
        plan_id="boiler-answer-without-products",
        turn_id="presentation-scope-2",
        turn_number=switched.state.turn_number,
        task_ids=(boiler_task.task_id,),
        primary_action=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
        status=AnswerPlanStatus.PARTIAL,
        sections=(
            AnswerSection(
                section_id="presentation-scope-next",
                kind=AnswerSectionKind.NEXT_STEP,
                item_ids=(next_step.next_step_id,),
            ),
        ),
        next_step=next_step,
        semantic_signature="boiler-answer-signature",
    )
    shadow = record_answer_shadow(
        switched,
        AnswerPlanningResult(status="planned", answer_plan=answer_plan),
        AnswerValidationResult(
            status="accepted",
            plan_id=answer_plan.plan_id,
            accepted_segment_ids=("presentation-scope-segment",),
        ),
        policy,
        (),
        TurnMetadata(turn_id="presentation-scope-2"),
    )

    assert shadow.state.answer_plan_summary is not None
    assert shadow.state.answer_plan_summary.presented_candidates == ()


def test_repeated_delivery_question_reuses_task_and_accumulates_no_progress() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["check_delivery"],
            products=[_product("pex_pipe", "pipes")],
        ),
        "delivery-repeat-1",
    )
    task_id = first.state.tasks[0].task_id

    second = _reduce(
        first.state,
        _turn(operation="continue", acts=["check_delivery"]),
        "delivery-repeat-2",
    )
    assert len(second.state.tasks) == 1
    assert second.state.tasks[0].task_id == task_id
    assert second.state.tasks[0].origin_turn == 1
    assert second.state.tasks[0].last_addressed_turn == 2
    assert second.progress.primary == ProgressKind.NO_PROGRESS
    assert any(
        item.reason_code == "existing_task_readdressed"
        for item in second.rejected_proposals
    )

    second_progress = assess_task_progress(
        first.state,
        second.state,
        TurnMetadata(turn_id="delivery-repeat-2"),
    )
    assert len(second_progress) == 1
    assert second_progress[0].task_id == task_id
    assert second_progress[0].consecutive_no_progress == 1
    recorded = record_answer_shadow(
        second,
        None,
        None,
        SellerPolicy().decide(second.state),
        second_progress,
        TurnMetadata(turn_id="delivery-repeat-2"),
    )

    third = _reduce(
        recorded.state,
        _turn(operation="refine", acts=["check_delivery"]),
        "delivery-repeat-3",
    )
    third_progress = assess_task_progress(
        recorded.state,
        third.state,
        TurnMetadata(turn_id="delivery-repeat-3"),
    )
    assert len(third.state.tasks) == 1
    assert third.state.tasks[0].task_id == task_id
    assert third.progress.primary == ProgressKind.NO_PROGRESS
    assert third_progress[0].consecutive_no_progress == 2
    assert third_progress[0].strategy_change_required is True


def test_terminal_delivery_task_is_not_reopened_by_reuse() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["check_delivery"],
            products=[_product("pex_pipe", "pipes")],
        ),
        "delivery-terminal-1",
    )
    original = first.state.tasks[0]
    terminal_state = first.state.model_copy(
        update={
            "tasks": (
                original.model_copy(update={"status": TaskStatus.SATISFIED}),
            ),
            "task_stack": first.state.task_stack.model_copy(
                update={
                    "active_task_id": None,
                    "pending_task_ids": (),
                    "completed_task_ids": (original.task_id,),
                }
            ),
        }
    )

    repeated = _reduce(
        terminal_state,
        _turn(operation="continue", acts=["check_delivery"]),
        "delivery-terminal-2",
    )

    assert len(repeated.state.tasks) == 2
    assert repeated.state.tasks[0].task_id == original.task_id
    assert repeated.state.tasks[0].status == TaskStatus.SATISFIED
    assert repeated.state.tasks[1].task_id != original.task_id


def test_two_explicit_same_act_instances_in_one_turn_remain_independent() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["check_delivery", "check_delivery"],
            products=[_product("pex_pipe", "pipes")],
        ),
        "two-independent-deliveries",
    )

    assert len(result.state.tasks) == 2
    assert {task.act.value for task in result.state.tasks} == {"check_delivery"}
    assert len({task.task_id for task in result.state.tasks}) == 2
    assert len({task.target_goal_id for task in result.state.tasks}) == 1

    active_task_id = result.state.task_stack.active_task_id
    continued = _reduce(
        result.state,
        _turn(operation="continue", acts=["check_delivery"]),
        "two-independent-deliveries-followup",
    )
    addressed = [
        task
        for task in continued.state.tasks
        if task.last_addressed_turn == 2
    ]
    assert len(addressed) == 1
    assert addressed[0].task_id == active_task_id
    assert len(continued.state.tasks) == 2


def test_existing_alpha2_is_the_typed_replacement_goal_for_selection() -> None:
    result = _reduce(
        None,
        _turn(
            operation="switch",
            acts=["select"],
            products=[_product("circulation_pump", "pumps", "existing")],
            constraints=[
                _fact("analog_performance", status="unknown", product=0)
            ],
        ),
        "alpha2-replacement",
    )

    goal = result.state.product_goals[0]
    task = result.state.tasks[0]
    fact = _active_facts(result.state)[0]

    assert goal.role.value == "existing"
    assert goal.canonical_type == "circulation_pump"
    assert result.state.active_goal_id == goal.goal_id
    assert task.target_goal_id == goal.goal_id
    assert fact.goal_id == goal.goal_id
    assert fact.task_id == task.task_id


def test_existing_pump_outranks_radiator_context_for_discovery_task() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("circulation_pump", "pumps", "existing"),
                _product("radiator", "radiators", "context"),
            ],
        ),
        "pump-existing-radiator-context",
    )

    pump = next(
        goal
        for goal in result.state.product_goals
        if goal.canonical_type == "circulation_pump"
    )

    assert len(result.state.product_goals) == 2
    assert len(result.state.tasks) == 1
    assert result.state.tasks[0].target_goal_id == pump.goal_id
    assert result.state.active_goal_id == pump.goal_id


def test_select_then_find_reuses_one_discovery_task_for_same_goal() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("boiler", "boilers", "alternative")],
        ),
        "select-find-1",
    )
    original_task_id = selected.state.tasks[0].task_id

    found = _reduce(
        selected.state,
        _turn(operation="continue", acts=["find"]),
        "select-find-2",
    )

    assert len(found.state.tasks) == 1
    assert found.state.tasks[0].task_id == original_task_id
    assert found.state.tasks[0].act.value == "select"
    assert found.state.tasks[0].origin_turn == 1
    assert found.state.tasks[0].source_turn == 2
    assert "task_addressed" in {event.event_type for event in found.events}


def test_two_alternative_products_keep_two_tasks_while_find_reuses_active() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
        ),
        "two-alternatives-1",
    )
    boiler_goal = next(
        goal for goal in first.state.product_goals if goal.canonical_type == "boiler"
    )
    boiler_task = next(
        task for task in first.state.tasks if task.target_goal_id == boiler_goal.goal_id
    )

    continued = _reduce(
        first.state,
        _turn(operation="continue", acts=["find"]),
        "two-alternatives-2",
    )

    assert len(continued.state.product_goals) == 2
    assert len(continued.state.tasks) == 2
    assert next(
        task
        for task in continued.state.tasks
        if task.target_goal_id == boiler_goal.goal_id
    ).task_id == boiler_task.task_id


def test_explicit_target_promotes_matching_alternative_goal() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("boiler", "boilers", "alternative")],
        ),
        "promote-alternative-1",
    )
    goal_id = first.state.active_goal_id

    promoted = _reduce(
        first.state,
        _turn(
            operation="continue",
            acts=["find"],
            products=[_product("boiler", "boilers", "target")],
        ),
        "promote-alternative-2",
    )
    goal = _active_goal(promoted.state)

    assert promoted.state.active_goal_id == goal_id
    assert len(promoted.state.product_goals) == 1
    assert len(promoted.state.tasks) == 1
    assert goal.role.value == "target"
    assert goal.type_locked is True
    assert goal.category_locked is True


def test_generic_followup_keeps_more_specific_active_alternative_goal() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[
                _product("gas_boiler", "boilers", "alternative"),
                _product("circulation_pump", "pumps", "alternative"),
            ],
        ),
        "generic-refine-1",
    )
    gas_goal = next(
        item for item in first.state.product_goals
        if item.canonical_type == "gas_boiler"
    )

    refined = _reduce(
        first.state,
        _turn(
            operation="refine",
            acts=["select"],
            products=[_product("boiler", "boilers")],
        ),
        "generic-refine-2",
    )

    active = _active_goal(refined.state)
    assert active.goal_id == gas_goal.goal_id
    assert active.canonical_type == "gas_boiler"
    assert active.role.value == "target"
    assert not [
        item for item in refined.state.product_goals
        if item.canonical_type == "boiler"
    ]


def test_context_only_boiler_and_pump_replacement_request_creates_two_tasks() -> None:
    result = _reduce(
        None,
        _turn(
            operation="refine",
            acts=["select"],
            products=[
                _product("boiler", "boilers", "context"),
                _product("circulation_pump", "pumps", "context"),
            ],
        ),
        "boiler-pump-replacements",
    )

    goals = {goal.canonical_type: goal for goal in result.state.product_goals}
    tasks_by_goal = {task.target_goal_id: task for task in result.state.tasks}

    assert set(goals) == {"boiler", "circulation_pump"}
    assert len(tasks_by_goal) == 2
    assert set(tasks_by_goal) == {goal.goal_id for goal in goals.values()}
    assert all(task.related_task_ids for task in tasks_by_goal.values())
    assert result.state.active_goal_id == goals["boiler"].goal_id


def test_d03_compound_system_preserves_three_goals_without_context_takeover() -> None:
    result = _reduce(
        None,
        _turn(
            operation="refine",
            acts=["select"],
            products=[
                _product("radiator", "radiators", "target"),
                _product("boiler", "boilers", "context"),
                _product("water_filter", "filters", "context"),
            ],
            constraints=[_fact("budget", 200000, product=None)],
        ),
        "d03-compound-system",
    )

    goals = {goal.canonical_type: goal for goal in result.state.product_goals}

    assert set(goals) == {"radiator", "boiler", "water_filter"}
    assert result.state.active_goal_id == goals["radiator"].goal_id
    assert len(result.state.tasks) == 1
    assert result.state.tasks[0].target_goal_id == goals["radiator"].goal_id
    assert {goal.role.value for goal in goals.values()} == {"target", "context"}


def test_d03_three_explicit_targets_create_three_linked_selection_tasks() -> None:
    result = _reduce(
        None,
        _turn(
            operation="refine",
            acts=["select"],
            products=[
                _product("radiator", "radiators", "target"),
                _product("boiler", "boilers", "target"),
                _product("water_filter", "filters", "target"),
            ],
        ),
        "d03-three-explicit-targets",
    )

    goal_ids = {goal.goal_id for goal in result.state.product_goals}

    assert len(goal_ids) == 3
    assert {task.target_goal_id for task in result.state.tasks} == goal_ids
    assert len(result.state.tasks) == 3
    assert all(len(task.related_task_ids) == 2 for task in result.state.tasks)


def test_g_half_followups_reuse_product_tasks_and_inherit_goal_fact() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["find"],
            products=[_product("ball_valve", "valves")],
            constraints=[
                {
                    **_fact("thread_size", "G1/2", product=0),
                    "unit": None,
                }
            ],
        ),
        "g-half-1",
    )
    continued = _reduce(
        first.state,
        _turn(operation="continue", acts=["find", "select"]),
        "g-half-2",
    )
    repeated = _reduce(
        continued.state,
        _turn(operation="switch", acts=["find", "select"]),
        "g-half-3",
    )

    goal_id = first.state.active_goal_id
    assert goal_id is not None
    assert [task.act.value for task in repeated.state.tasks] == ["select"]
    assert {task.target_goal_id for task in repeated.state.tasks} == {goal_id}
    assert len({task.task_id for task in repeated.state.tasks}) == 1
    assert any(
        fact.active
        and fact.goal_id == goal_id
        and fact.name == "thread_size"
        and fact.value == "G1/2"
        for fact in repeated.state.constraints
    )
    assert repeated.progress.primary == ProgressKind.NO_PROGRESS
    assert [item.reason_code for item in repeated.rejected_proposals].count(
        "existing_selection_task_reused"
    ) == 2
    assert [event.event_type for event in repeated.events].count(
        "task_addressed"
    ) == 2
    assert {task.last_addressed_turn for task in repeated.state.tasks} == {3}
    assert {task.source_turn for task in repeated.state.tasks} == {3}
    assert {task.origin_turn for task in repeated.state.tasks} == {1}

    plan = SellerPolicy().decide(repeated.state)
    assert plan.primary.kind == NextActionKind.RECOMMEND_ONE
    assert set(plan.task_ids) == {task.task_id for task in repeated.state.tasks}


def test_goal_less_photo_discovery_binds_to_ball_valve_and_refine_fact() -> None:
    photo = _reduce(
        None,
        _turn(operation="new", acts=["find", "select"]),
        "photo-valve-1",
    )

    assert len(photo.state.tasks) == 1
    discovery_task_id = photo.state.tasks[0].task_id
    assert photo.state.tasks[0].target_goal_id is None
    assert photo.state.tasks[0].act.value == "select"

    identified = _reduce(
        photo.state,
        _turn(
            operation="continue",
            acts=["select"],
            products=[_product("ball_valve", "valves", "target")],
            constraints=[_fact("thread_size", "G1/2", product=0)],
        ),
        "photo-valve-2",
    )
    goal_id = identified.state.active_goal_id
    task = identified.state.tasks[0]

    assert goal_id is not None
    assert len(identified.state.tasks) == 1
    assert task.task_id == discovery_task_id
    assert task.target_goal_id == goal_id
    assert task.status == TaskStatus.IN_PROGRESS
    assert identified.state.task_stack.active_task_id == task.task_id
    assert _active_facts(identified.state)[0].task_id == task.task_id

    refined = _reduce(
        identified.state,
        _turn(
            operation="refine",
            constraints=[_fact("thread_type", status="unknown", product=None)],
        ),
        "photo-valve-3",
    )
    latest = next(
        fact
        for fact in refined.state.constraints
        if fact.active and fact.name == "thread_type"
    )

    assert latest.goal_id == goal_id
    assert latest.task_id == task.task_id


def test_repeated_explain_and_handoff_readdress_stable_tasks_and_goal() -> None:
    selected = _reduce(
        None,
        _turn(
            operation="new",
            acts=["find"],
            products=[_product("circulation_pump", "pumps", "target")],
        ),
        "repeat-non-discovery-1",
    )
    goal_id = selected.state.active_goal_id
    explained = _reduce(
        selected.state,
        _turn(operation="continue", acts=["explain"]),
        "repeat-non-discovery-2",
    )
    explained_again = _reduce(
        explained.state,
        _turn(operation="continue", acts=["explain"]),
        "repeat-non-discovery-3",
    )
    handed = _reduce(
        explained_again.state,
        _turn(operation="switch", acts=["handoff"]),
        "repeat-non-discovery-4",
    )
    handed_again = _reduce(
        handed.state,
        _turn(operation="new", acts=["handoff"]),
        "repeat-non-discovery-5",
    )

    explain_tasks = [task for task in handed_again.state.tasks if task.act.value == "explain"]
    handoff_tasks = [task for task in handed_again.state.tasks if task.act.value == "handoff"]

    assert len(explain_tasks) == 1
    assert len(handoff_tasks) == 1
    assert handoff_tasks[0].source_turn == handed_again.state.turn_number
    assert handoff_tasks[0].origin_turn == 4
    assert handoff_tasks[0].target_goal_id == goal_id
    assert handed_again.state.active_goal_id == goal_id
    assert len(handed_again.state.product_goals) == 1
    assert "task_addressed" in {event.event_type for event in handed_again.events}


def test_untyped_context_does_not_poison_existing_product_goal() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["check_stock"],
            products=[_product("pipe", "pipes", "target")],
        ),
        "untyped-context-1",
    )
    goal_id = first.state.active_goal_id
    payload = _product("destination", "other", "context")
    payload.update(
        {
            "text": "объект",
            "canonical_type": None,
            "evidence": "объект",
        }
    )

    continued = _reduce(
        first.state,
        _turn(
            operation="new",
            acts=["handoff"],
            products=[_product("pipe", "pipes", "target"), payload],
        ),
        "untyped-context-2",
    )

    assert continued.state.active_goal_id == goal_id
    assert len(continued.state.product_goals) == 1
    assert {item.reason_code for item in continued.rejected_proposals} >= {
        "untyped_context_goal_ignored"
    }


def test_switch_suspends_current_task() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
        ),
        "switch-1",
    )
    old_task_id = first.state.tasks[0].task_id
    second = _reduce(
        first.state,
        _turn(
            operation="switch",
            acts=["select"],
            products=[_product("котёл", "boilers")],
        ),
        "switch-2",
    )

    old_task = next(task for task in second.state.tasks if task.task_id == old_task_id)
    assert old_task.status == TaskStatus.SUSPENDED
    assert _active_goal(second.state).canonical_type == "котёл"
    assert second.progress.primary == ProgressKind.TASK_SWITCHED


def test_return_restores_suspended_task_and_its_constraints() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("connection_diameter", 25)],
        ),
        "return-1",
    )
    pump_goal_id = first.state.active_goal_id
    pump_task_id = first.state.tasks[0].task_id
    switched = _reduce(
        first.state,
        _turn(
            operation="switch",
            acts=["select"],
            products=[_product("котёл", "boilers")],
        ),
        "return-2",
    )
    returned = _reduce(
        switched.state,
        _turn(
            operation="return",
            products=[_product("насос", "pumps")],
        ),
        "return-3",
    )

    assert returned.state.active_goal_id == pump_goal_id
    task = next(task for task in returned.state.tasks if task.task_id == pump_task_id)
    assert task.status == TaskStatus.IN_PROGRESS
    assert any(
        fact.active and fact.goal_id == pump_goal_id and fact.value == 25
        for fact in returned.state.constraints
    )
    assert returned.progress.primary == ProgressKind.TASK_RETURNED


def test_duplicate_turn_id_is_idempotent() -> None:
    understanding = _turn(
        operation="new",
        acts=["select"],
        products=[_product("насос", "pumps")],
    )
    first = _reduce(None, understanding, "duplicate")
    duplicate = _reduce(first.state, understanding, "duplicate")

    assert duplicate.state is first.state
    assert duplicate.events[0].event_type == "turn_ignored_as_duplicate"


def test_old_v2_task_without_last_addressed_turn_restores_safely() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["find"],
            products=[_product("ball_valve", "valves")],
        ),
        "old-task-schema",
    )
    payload = result.state.model_dump(mode="json")
    payload["tasks"][0].pop("last_addressed_turn")
    payload["tasks"][0].pop("created_turn")

    restored = DialogueStateV2.model_validate(payload)

    assert restored.tasks[0].last_addressed_turn is None
    assert restored.tasks[0].created_turn is None
    assert restored.tasks[0].was_addressed_on(restored.tasks[0].source_turn)
    assert restored.tasks[0].origin_turn == restored.tasks[0].source_turn


def test_reducer_does_not_mutate_input_state() -> None:
    first = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select"],
            products=[_product("насос", "pumps")],
        ),
        "immutable-1",
    )
    snapshot = deepcopy(first.state.model_dump(mode="json"))

    second = _reduce(
        first.state,
        _turn(
            constraints=[
                _fact("mounting_length", status="unknown", product=None)
            ]
        ),
        "immutable-2",
    )

    assert first.state.model_dump(mode="json") == snapshot
    assert second.state is not first.state


def test_seller_policy_is_deterministic_and_plan_cardinality_is_bounded() -> None:
    result = _reduce(
        None,
        _turn(
            operation="new",
            acts=["select", "check_price", "check_stock"],
            products=[_product("насос", "pumps")],
            constraints=[_fact("connection_diameter", 25)],
        ),
        "deterministic-policy",
    )
    policy = SellerPolicy()

    first = policy.decide(result.state)
    second = policy.decide(result.state)

    assert first == second
    assert first.primary is not None
    assert first.secondary is None or first.secondary is not first.primary


def test_v2_models_are_frozen_outside_reducer() -> None:
    state = DialogueStateV2()

    with pytest.raises(ValidationError):
        state.turn_number = 99


@pytest.mark.parametrize(
    ("act", "expected"),
    (
        ("select", NextActionKind.RECOMMEND_ONE),
        ("find", NextActionKind.SEARCH_EXACT),
    ),
)
def test_exact_ready_select_recommends_while_find_lists_options(
    act: str,
    expected: NextActionKind,
) -> None:
    reduced = _reduce(
        None,
        _turn(
            operation="new",
            acts=[act],
            products=[_product("pipe", "pipes")],
            constraints=[_fact("diameter_mm", 25)],
        ),
        f"exact-ready-{act}",
    )
    task = reduced.state.tasks[0]
    readiness = TaskReadinessAssessment(
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        contract_id="pipe.ppr.v1",
        product_kind=ProductKind.PIPE,
        status=ReadinessStatus.EXACT_READY,
    )

    decision = SellerPolicy().decide(
        reduced.state,
        readiness_assessments=(readiness,),
    )

    assert decision.primary.kind == expected


@pytest.mark.parametrize("status", ["rejected", "skipped"])
def test_unavailable_semantics_skips_reducer_with_typed_wait_action(status: str) -> None:
    before = DialogueStateV2()
    semantic = SemanticInterpretationResult(
        status=status,
        output_accepted=False,
        rejection_reason="invalid semantic frame" if status == "rejected" else None,
        fallback_reason="llm unavailable" if status == "skipped" else None,
    )

    outcome = DialogueControllerV2().run(
        before,
        semantic,
        TurnMetadata(turn_id=f"semantic-{status}"),
    )

    assert outcome.status == "skipped"
    assert outcome.state_after is before
    assert outcome.reduction is None
    assert outcome.next_action_plan is not None
    assert (
        outcome.next_action_plan.primary.kind
        == NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING
    )
