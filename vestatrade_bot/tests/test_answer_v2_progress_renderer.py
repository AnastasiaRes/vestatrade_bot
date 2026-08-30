from __future__ import annotations

import json
from types import SimpleNamespace

from app.answer_v2.contracts import (
    AnswerSourceSnapshot,
    RenderedSegmentKind,
)
from app.answer_v2.progress import assess_task_progress
from app.answer_v2.renderer import ResponseRendererV2, deterministic_render
from app.answer_v2.strategy import select_strategy_directives
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import (
    FactStrength,
    ProductKind,
    ReadinessFact,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ProductCategory,
    ProductGoal,
    ProductRole,
    ResponseStrategyKind,
    TaskAct,
    TaskStack,
    TaskStatus,
    TaskStrategyState,
    TurnMetadata,
)

from test_answer_v2_planning import _compile, _sources


def _task_state(
    *,
    turn: int,
    streak: int = 0,
    attempted: tuple[ResponseStrategyKind, ...] = (),
    task_id: str = "task-loop",
) -> DialogueStateV2:
    goal = ProductGoal(
        goal_id=f"goal-{task_id}",
        canonical_type="циркуляционный насос",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="насос",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
    )
    task = CustomerTask(
        task_id=task_id,
        act=TaskAct.SELECT,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
    )
    history = ()
    if streak or attempted:
        history = (
            TaskStrategyState(
                task_id=task_id,
                consecutive_no_progress=streak,
                attempted_strategies=attempted,
                last_strategy=(attempted[-1] if attempted else None),
                last_turn=max(0, turn - 1),
            ),
        )
    return DialogueStateV2(
        turn_number=turn,
        task_stack=TaskStack(active_task_id=task_id),
        tasks=(task,),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
        response_strategy_history=history,
    )


def _readiness() -> TaskReadinessAssessment:
    return TaskReadinessAssessment(
        task_id="task-loop",
        goal_id="goal-task-loop",
        contract_id="pump-v1",
        product_kind=ProductKind.CIRCULATION_PUMP,
        status=ReadinessStatus.PRELIMINARY_READY,
        confirmed_hard_facts=(
            ReadinessFact(
                name="diameter_mm",
                status="known",
                value=25,
                unit="mm",
                strength=FactStrength.HARD,
            ),
        ),
        unknown_facts=("mounting_length_mm",),
        learn_method_code="measure_between_union_faces",
    )


def test_text_changes_are_absent_from_progress_and_second_stall_escalates() -> None:
    previous = _task_state(
        turn=1,
        streak=1,
        attempted=(ResponseStrategyKind.ASK_DECISION_FACT,),
    )
    current = _task_state(turn=2)
    assessment = assess_task_progress(
        previous,
        current,
        TurnMetadata(turn_id="loop-turn-2"),
    )[0]
    assert assessment.status.value == "no_progress"
    assert assessment.consecutive_no_progress == 2
    assert assessment.strategy_change_required is True
    assert not any("text" in item or "hash" in item for item in assessment.reason_codes)

    directive = select_strategy_directives(
        current,
        (assessment,),
        (_readiness(),),
    )[0]
    assert directive.strategy == ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT
    assert directive.fact_name == "mounting_length_mm"


def test_attempted_strategy_advances_to_preliminary_instead_of_looping() -> None:
    previous = _task_state(
        turn=2,
        streak=1,
        attempted=(
            ResponseStrategyKind.ASK_DECISION_FACT,
            ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
        ),
    )
    current = _task_state(turn=3)
    assessment = assess_task_progress(
        previous,
        current,
        TurnMetadata(turn_id="loop-turn-3"),
    )[0]
    directive = select_strategy_directives(
        current,
        (assessment,),
        (_readiness(),),
    )[0]
    assert directive.strategy == ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS


def test_progress_history_is_scoped_by_task() -> None:
    first = _task_state(
        turn=2,
        streak=4,
        attempted=(ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,),
        task_id="task-a",
    )
    second = _task_state(turn=3, task_id="task-b")
    assessment = assess_task_progress(
        first,
        second,
        TurnMetadata(turn_id="new-task"),
    )[0]
    assert assessment.task_id == "task-b"
    assert assessment.status.value == "progress"
    assert assessment.consecutive_no_progress == 0


class FakeRendererClient:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.settings = SimpleNamespace(llm_model_strong="test/renderer")
        self.last_fallback_reason = None
        self.messages = None

    def complete_json(self, *, messages, fallback, **_kwargs):
        self.messages = messages
        if self.mode == "transport":
            self.last_fallback_reason = "timeout"
            return fallback, False
        if self.mode == "malformed":
            return {}, True
        if self.mode == "truncated":
            return '{"plan_id":', True
        result = json.loads(json.dumps(fallback))
        if self.mode == "valid-transition":
            payload = json.loads(messages[-1]["content"])
            if len(payload["segment_outline"]) > 1:
                result["transitions"] = [
                    {
                        "before_segment_id": payload["segment_outline"][1]["segment_id"],
                        "style": "also",
                    }
                ]
        if self.mode == "extra-number":
            result["unapproved_text"] = "Ещё 999 мм."
        if self.mode == "unknown-id":
            result["transitions"] = [
                {"before_segment_id": "segment_invented", "style": "also"}
            ]
        return result, True


def test_renderer_prompt_contains_only_plan_segments_and_safe_context() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    client = FakeRendererClient("valid")
    result = ResponseRendererV2(client).render(plan, naturalize=True)
    assert result.status == "rendered"
    payload = json.loads(client.messages[-1]["content"])
    assert set(payload) == {
        "prompt_version",
        "locale",
        "segment_outline",
        "output_schema",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "current_message" not in serialized
    assert "dialogue" not in serialized
    assert "legacy" not in serialized


def test_renderer_transport_and_malformed_output_use_deterministic_fallback() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    for mode in ("transport", "malformed", "truncated"):
        result = ResponseRendererV2(FakeRendererClient(mode)).render(
            plan,
            naturalize=True,
        )
        assert result.status == "fallback"
        assert result.rendered_answer.renderer == "deterministic"
        assert validate_rendered_answer(plan, result.rendered_answer, _sources()).status == "accepted"


def test_valid_layout_only_adds_allowlisted_transition() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    fallback = deterministic_render(plan)
    result = ResponseRendererV2(FakeRendererClient("valid-transition")).render(
        plan,
        naturalize=True,
    )
    assert result.status == "rendered"
    assert result.rendered_answer.renderer == "llm"
    factual = tuple(
        item
        for item in result.rendered_answer.segments
        if item.kind != RenderedSegmentKind.TRANSITION
    )
    assert factual == fallback.segments
    assert validate_rendered_answer(plan, result.rendered_answer, _sources()).status == "accepted"


def test_response_llm_cannot_submit_factual_prose_or_unknown_ids() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    for mode in ("extra-number", "unknown-id"):
        result = ResponseRendererV2(FakeRendererClient(mode)).render(
            plan,
            naturalize=True,
        )
        assert result.status == "fallback"
        assert result.rendered_answer.renderer == "deterministic"
        assert result.llm_output_accepted is False
        validation = validate_rendered_answer(
            plan,
            result.rendered_answer,
            _sources(),
        )
        assert validation.status == "accepted"


def test_seller_policy_keeps_direct_answer_primary_while_loop_guard_changes_secondary() -> None:
    from app.dialogue_v2.seller_policy import SellerPolicy
    from app.answer_v2.contracts import StrategyDirective

    state = _task_state(turn=2)
    # A compact policy contract test: the direct action remains untouched.
    direct = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id="task-loop",
            reason_code="direct",
        )
    )
    applied = SellerPolicy._apply_strategy_directive(
        direct,
        (
            StrategyDirective(
                task_id="task-loop",
                strategy=ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
                reason_codes=("stalled",),
            ),
        ),
    )
    assert applied.primary.kind == NextActionKind.ANSWER_DIRECT_QUESTION


def test_seller_policy_keeps_explicit_capability_actions_ahead_of_loop_guard() -> None:
    from app.dialogue_v2.seller_policy import SellerPolicy
    from app.answer_v2.contracts import StrategyDirective

    directive = StrategyDirective(
        task_id="task-loop",
        strategy=ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
        reason_codes=("stalled",),
    )
    for kind in (
        NextActionKind.COMPARE,
        NextActionKind.CHECK_COMPATIBILITY,
        NextActionKind.CALCULATE_PRELIMINARY,
    ):
        plan = NextActionPlan(
            primary=NextAction(
                kind=kind,
                task_id="task-loop",
                reason_code="explicit_customer_action",
            )
        )

        applied = SellerPolicy._apply_strategy_directive(plan, (directive,))

        assert applied.primary.kind == kind
        assert "progress_guard_strategy_directive_applied" not in applied.reason_codes


def test_deterministic_renderer_always_emits_one_question_at_most_and_one_next_step() -> None:
    plan = _compile().answer_plan
    assert plan is not None
    rendered = deterministic_render(plan)
    assert len([item for item in rendered.segments if item.kind == RenderedSegmentKind.QUESTION]) <= 1
    assert len([item for item in rendered.segments if item.kind == RenderedSegmentKind.NEXT_STEP]) == 1
