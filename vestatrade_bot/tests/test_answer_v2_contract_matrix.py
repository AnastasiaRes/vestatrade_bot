from __future__ import annotations

import pytest

from app.answer_v2.contracts import (
    AnswerClaim,
    ClaimKind,
    KnowledgeStatus,
    RenderedSegmentKind,
)
from app.answer_v2.planner import build_answer_plan
from app.answer_v2.progress import assess_task_progress
from app.answer_v2.renderer import deterministic_render
from app.answer_v2.sources import attach_turn_source_evidence
from app.answer_v2.strategy import select_strategy_directives
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import CatalogProductRole
from app.dialogue_v2.contracts import (
    CustomerTask,
    ConstraintStatus,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ProductCategory,
    ProductGoal,
    ProductRole,
    ResponseStrategyKind,
    TaskAct,
    TaskStatus,
    TaskStrategyState,
    TurnMetadata,
)

from test_answer_v2_planning import _catalog, _compile, _policy, _sources, _state
from test_answer_v2_progress_renderer import _readiness, _task_state


def test_assertable_claim_contract_rejects_unconfirmed_values() -> None:
    with pytest.raises(ValueError):
        AnswerClaim(
            claim_id="unsafe",
            kind=ClaimKind.PRODUCT_ATTRIBUTE,
            subject_ref="SKU",
            predicate="diameter_mm",
            value=25,
            knowledge_status=KnowledgeStatus.UNVERIFIED,
            source_ref_ids=("source",),
            allowed_in_response=True,
        )


def test_direct_answer_is_first_in_the_rendered_candidate() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    direct_item_ids = set(plan.sections[0].item_ids)
    rendered = deterministic_render(plan)
    assert rendered.segments[0].source_ids[0] in direct_item_ids
    assert rendered.segments[0].text.startswith("Цена:")


def test_missing_direct_source_uses_boundary_instead_of_claiming_an_answer() -> None:
    sources = _sources()
    product = sources.products[0].model_copy(
        update={"price": None, "currency": None}
    )
    sources = sources.model_copy(update={"products": (product,)})
    plan = _compile(sources=sources).answer_plan
    assert plan is not None
    assert plan.sections[0].kind.value == "direct_answer"
    assert plan.next_step.kind.value == "state_capability_boundary"
    rendered = deterministic_render(plan)
    assert rendered.segments[0].kind == RenderedSegmentKind.LIMITATION
    assert not any(item.kind == ClaimKind.PRICE for item in plan.claims)


def test_secondary_catalogue_action_and_candidates_are_preserved() -> None:
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id="task-pipe",
            reason_code="direct_question_first",
        ),
        secondary=NextAction(
            kind=NextActionKind.SEARCH_EXACT,
            task_id="task-pipe",
            reason_code="continue_selection_after_answer",
        ),
        task_ids=("task-pipe",),
    )
    plan = _compile(policy=policy).answer_plan
    assert plan is not None
    assert plan.primary_action == NextActionKind.ANSWER_DIRECT_QUESTION
    assert plan.secondary_action == NextActionKind.SEARCH_EXACT
    assert plan.products


def test_terminal_fact_on_another_task_does_not_suppress_question() -> None:
    first = _state(constraint_status=ConstraintStatus.KNOWN)
    second_goal = ProductGoal(
        goal_id="goal-pump",
        canonical_type="циркуляционный насос",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="насос",
        source="test",
        confidence=1.0,
        confirmed_turn=2,
        type_locked=True,
    )
    second_task = CustomerTask(
        task_id="task-pump",
        act=TaskAct.SELECT,
        target_goal_id=second_goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=2,
    )
    state = first.model_copy(
        update={
            "turn_number": 2,
            "tasks": (*first.tasks, second_task),
            "product_goals": (*first.product_goals, second_goal),
            "active_goal_id": second_goal.goal_id,
        }
    )
    policy = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            task_id=second_task.task_id,
            fact_name="diameter_mm",
            reason_code="pump_contract_requires_diameter",
        ),
        task_ids=(second_task.task_id,),
    )
    sources = attach_turn_source_evidence(_sources(), None, None, state)
    result = build_answer_plan(
        state,
        policy,
        None,
        None,
        sources,
        turn_id="task-scoped-question",
    )
    assert result.answer_plan is not None
    assert result.answer_plan.question is not None
    assert result.answer_plan.question.task_id == second_task.task_id


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("not_selected", "candidate_not_selected_by_catalog_planner"),
        ("wrong_role", "candidate_kind_or_role_mismatch"),
        ("hard_mismatch", "hard_constraint_violation_not_presentable"),
    ],
)
def test_compiler_fail_closes_on_invalid_upstream_candidate(
    mutation: str,
    reason_code: str,
) -> None:
    catalog = _catalog()
    search = catalog.search_plans[0]
    candidate = search.candidate_assessments[0]
    update = {}
    search_update = {}
    if mutation == "not_selected":
        search_update = {
            "eligible_skus": (),
            "relaxed_skus": (),
            "unverified_skus": (),
        }
    elif mutation == "wrong_role":
        update = {"role": CatalogProductRole.ACCESSORY}
    else:
        update = {"mismatched_hard_facts": ("diameter_mm",)}
    mutated_candidate = candidate.model_copy(update=update)
    mutated_search = search.model_copy(
        update={"candidate_assessments": (mutated_candidate,), **search_update}
    )
    mutated_catalog = catalog.model_copy(update={"search_plans": (mutated_search,)})
    result = _compile(catalog=mutated_catalog)
    assert result.answer_plan is not None
    assert result.answer_plan.products == ()
    assert any(item.reason_code == reason_code for item in result.rejected_claims)


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("price", "price_source_mismatch"),
        ("stock", "stock_source_mismatch"),
        ("link", "link_source_mismatch"),
        ("technical", "catalog_attribute_source_mismatch"),
    ],
)
def test_grounding_rejects_tampered_independent_sources(
    field: str,
    expected_code: str,
) -> None:
    plan = _compile().answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    sources = _sources()
    product = sources.products[0]
    if field == "price":
        product = product.model_copy(update={"price": 999.0})
    elif field == "stock":
        product = product.model_copy(update={"stock_qty": 999})
    elif field == "link":
        product = product.model_copy(update={"url": "https://changed.test/item"})
    else:
        fact = product.facts[0].model_copy(update={"value": 999})
        product = product.model_copy(update={"facts": (fact,)})
    tampered = sources.model_copy(update={"products": (product,)})
    validation = validate_rendered_answer(plan, rendered, tampered)
    assert validation.status == "rejected"
    assert expected_code in {item.code for item in validation.violations}


def test_renderer_cannot_rewrite_uncertainty_or_add_free_prose() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    product_index = next(
        index
        for index, item in enumerate(rendered.segments)
        if item.kind == RenderedSegmentKind.PRODUCT
    )
    product = rendered.segments[product_index]
    changed = product.model_copy(
        update={"text": product.text.replace("точное подтверждённое", "лучшее")}
    )
    segments = list(rendered.segments)
    segments[product_index] = changed
    altered = rendered.model_copy(
        update={
            "segments": tuple(segments),
            "text": "\n".join(item.text for item in segments),
        }
    )
    validation = validate_rendered_answer(plan, altered, _sources())
    assert validation.status == "rejected"
    assert "protected_content_segment_changed" in {
        item.code for item in validation.violations
    }


@pytest.mark.parametrize("duplicate_kind", ["question", "next_step"])
def test_grounding_rejects_second_question_or_next_step(duplicate_kind: str) -> None:
    policy = _policy(
        NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        fact_name="pressure_bar",
    )
    plan = _compile(policy=policy).answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    wanted = (
        RenderedSegmentKind.QUESTION
        if duplicate_kind == "question"
        else RenderedSegmentKind.NEXT_STEP
    )
    original = next(item for item in rendered.segments if item.kind == wanted)
    duplicate = original.model_copy(update={"segment_id": f"duplicate-{original.segment_id}"})
    segments = (*rendered.segments, duplicate)
    altered = rendered.model_copy(
        update={"segments": segments, "text": "\n".join(item.text for item in segments)}
    )
    validation = validate_rendered_answer(plan, altered, _sources())
    assert validation.status == "rejected"
    expected = (
        "multiple_questions_rendered"
        if duplicate_kind == "question"
        else "next_step_count_invalid"
    )
    assert expected in {item.code for item in validation.violations}


def test_repeated_same_question_changes_strategy_after_one_stalled_turn() -> None:
    previous = _task_state(turn=1).model_copy(
        update={
            "response_strategy_history": (
                TaskStrategyState(
                    task_id="task-loop",
                    consecutive_no_progress=0,
                    attempted_strategies=(ResponseStrategyKind.ASK_DECISION_FACT,),
                    last_strategy=ResponseStrategyKind.ASK_DECISION_FACT,
                    last_question_fact="mounting_length_mm",
                    last_turn=1,
                ),
            )
        }
    )
    current = _task_state(turn=2)
    progress = assess_task_progress(
        previous,
        current,
        TurnMetadata(turn_id="same-question"),
    )[0]
    assert progress.consecutive_no_progress == 1
    assert progress.strategy_change_required is True
    directive = select_strategy_directives(current, (progress,), (_readiness(),))[0]
    assert directive.strategy != ResponseStrategyKind.ASK_DECISION_FACT
    assert directive.fact_name == "mounting_length_mm"


def test_new_confirmed_fact_resets_streak_but_repeat_does_not() -> None:
    previous = _state(act=TaskAct.SELECT).model_copy(
        update={
            "response_strategy_history": (
                TaskStrategyState(
                    task_id="task-pipe",
                    consecutive_no_progress=3,
                    last_strategy=ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                    last_turn=1,
                ),
            )
        }
    )
    with_fact = _state(
        act=TaskAct.SELECT,
        constraint_status=ConstraintStatus.KNOWN,
    ).model_copy(
        update={"turn_number": 2}
    )
    progress = assess_task_progress(
        previous,
        with_fact,
        TurnMetadata(turn_id="new-fact"),
    )[0]
    assert progress.status.value == "progress"
    assert progress.consecutive_no_progress == 0

    repeated = with_fact.model_copy(
        update={
            "turn_number": 3,
            "response_strategy_history": (
                TaskStrategyState(
                    task_id="task-pipe",
                    consecutive_no_progress=0,
                    last_turn=2,
                ),
            ),
        }
    )
    no_progress = assess_task_progress(
        repeated,
        repeated.model_copy(update={"turn_number": 4}),
        TurnMetadata(turn_id="repeat-fact"),
    )[0]
    assert no_progress.status.value == "no_progress"
    assert no_progress.consecutive_no_progress == 1


def test_exhausted_strategies_end_at_boundary_not_handoff_or_cycle() -> None:
    attempted = tuple(ResponseStrategyKind)
    previous = _task_state(turn=4, streak=2, attempted=attempted)
    progress = assess_task_progress(
        previous,
        _task_state(turn=5),
        TurnMetadata(turn_id="strategies-exhausted"),
    )[0]
    directive = select_strategy_directives(
        _task_state(turn=5),
        (progress,),
        (_readiness(),),
    )[0]
    assert directive.strategy == ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY


def test_social_task_is_neutral_and_does_not_increment_streak() -> None:
    previous = _state(act=TaskAct.GREETING).model_copy(
        update={
            "response_strategy_history": (
                TaskStrategyState(
                    task_id="task-pipe",
                    consecutive_no_progress=2,
                    last_turn=1,
                ),
            )
        }
    )
    current = previous.model_copy(update={"turn_number": 2})
    progress = assess_task_progress(
        previous,
        current,
        TurnMetadata(turn_id="social"),
    )[0]
    assert progress.status.value == "neutral"
    assert progress.consecutive_no_progress == 2
