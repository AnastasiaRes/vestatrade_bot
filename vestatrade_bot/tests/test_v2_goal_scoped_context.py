"""Regression coverage for returning to a paused V2 product selection.

These tests intentionally use typed state rather than rendered text.  The
customer-visible order is a delivery fact, and a later ordinal must resolve
against the restored goal rather than whatever the most recent UI list was.
"""

from __future__ import annotations

from app.agents.semantic_interpreter import SemanticInterpretationResult, TurnUnderstanding
from app.dialogue_v2.contracts import (
    CustomerTask,
    DeliveredSelectionScope,
    DialogueStateV2,
    ProductCategory,
    ProductGoal,
    ProductRole,
    TaskAct,
    TaskStatus,
    TurnMetadata,
)
from app.dialogue_v2.controller import DialogueControllerV2
from app.dialogue_v2.reactivation import resolve_goal_reactivation
from app.dialogue_v2.reducer import record_response_delivery, reduce_dialogue_state
from app.v2_visible_products import turn_product_context


def _goal(goal_id: str, canonical_type: str, category: ProductCategory) -> ProductGoal:
    return ProductGoal(
        goal_id=goal_id,
        canonical_type=canonical_type,
        category=category,
        role=ProductRole.TARGET,
        evidence=canonical_type,
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )


def _task(
    task_id: str,
    goal_id: str,
    act: TaskAct,
    status: TaskStatus,
) -> CustomerTask:
    return CustomerTask(
        task_id=task_id,
        target_goal_id=goal_id,
        act=act,
        priority=0,
        status=status,
        source="test",
        source_turn=1,
        created_turn=1,
        last_addressed_turn=1,
    )


def _selection_scope(
    goal_id: str,
    task_id: str,
    selection_id: str,
    skus: tuple[str, ...],
) -> DeliveredSelectionScope:
    return DeliveredSelectionScope(
        scope_id=f"scope-{selection_id}",
        goal_id=goal_id,
        task_id=task_id,
        selection_id=selection_id,
        ordered_skus=skus,
        catalog_revision="feed-revision",
        delivery_id=f"delivery-{selection_id}",
        source_turn=2,
    )


def _state() -> DialogueStateV2:
    pump_goal = _goal("goal-pump", "circulation_pump", ProductCategory.PUMPS)
    valve_goal = _goal("goal-valve", "ball_valve", ProductCategory.VALVES)
    return DialogueStateV2(
        turn_number=3,
        active_goal_id=valve_goal.goal_id,
        product_goals=(pump_goal, valve_goal),
        tasks=(
            _task("task-pump-select", pump_goal.goal_id, TaskAct.SELECT, TaskStatus.SUSPENDED),
            _task("task-pump-price", pump_goal.goal_id, TaskAct.CHECK_PRICE, TaskStatus.SUSPENDED),
            _task("task-valve-select", valve_goal.goal_id, TaskAct.SELECT, TaskStatus.IN_PROGRESS),
        ),
        delivered_selection_scopes=(
            _selection_scope(
                pump_goal.goal_id,
                "task-pump-select",
                "selection-pumps",
                ("PUMP-ONE", "PUMP-TWO"),
            ),
            _selection_scope(
                valve_goal.goal_id,
                "task-valve-select",
                "selection-valves",
                ("VALVE-ONE", "VALVE-TWO"),
            ),
        ),
    )


def _turn(*, operation: str = "continue", acts: list[str] | None = None) -> TurnUnderstanding:
    return TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
            "operation": operation,
            "acts": acts or [],
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }
    )


def test_delivered_selection_scope_is_committed_only_with_complete_selection_coordinates() -> None:
    state = DialogueStateV2(turn_number=2)
    committed = record_response_delivery(
        state,
        TurnMetadata(turn_id="selection-turn"),
        plan_id="selection-plan",
        response_digest="digest",
        delivery_id="selection-delivery",
        live_epoch_id="epoch",
        selection_id="selection-pumps",
        catalog_revision="feed-revision",
        selection_goal_id="goal-pump",
        selection_task_id="task-pump-select",
        selection_ordered_skus=("PUMP-ONE", "PUMP-TWO"),
    )

    assert len(committed.state.delivered_selection_scopes) == 1
    scope = committed.state.delivered_selection_scopes[0]
    assert scope.goal_id == "goal-pump"
    assert scope.ordered_skus == ("PUMP-ONE", "PUMP-TWO")


def test_explicit_return_restores_goal_scoped_cards_before_ordinal_resolution() -> None:
    state = _state()
    resolution = resolve_goal_reactivation("Вернёмся к насосу", state)

    assert resolution.status == "resolved"
    assert resolution.target_goal_id == "goal-pump"
    returned = reduce_dialogue_state(
        state,
        _turn(acts=["explain"]),
        TurnMetadata(turn_id="return-pump"),
        goal_reactivation=resolution,
    )

    assert returned.state.active_goal_id == "goal-pump"
    context = turn_product_context(
        returned.state,
        source_revision="feed-revision",
    )
    assert context.is_valid
    assert context.scope.ordinal(0).canonical_sku == "PUMP-ONE"
    assert context.scope.ordinal(1).canonical_sku == "PUMP-TWO"
    # The old single-item price task stays paused; a current request creates
    # its own task instead of letting historical price work become active.
    old_price = next(item for item in returned.state.tasks if item.task_id == "task-pump-price")
    assert old_price.status == TaskStatus.SUSPENDED
    assert any(
        item.act == TaskAct.EXPLAIN and item.target_goal_id == "goal-pump"
        for item in returned.state.tasks
    )


def test_return_with_two_old_matching_goals_is_ambiguous_not_latest_goal() -> None:
    state = _state().model_copy(
        update={
            "product_goals": (
                _goal("goal-pump-a", "circulation_pump", ProductCategory.PUMPS),
                _goal("goal-pump-b", "circulation_pump", ProductCategory.PUMPS),
                _goal("goal-valve", "ball_valve", ProductCategory.VALVES),
            ),
            "tasks": (
                _task("task-pump-a", "goal-pump-a", TaskAct.SELECT, TaskStatus.SUSPENDED),
                _task("task-pump-b", "goal-pump-b", TaskAct.SELECT, TaskStatus.SUSPENDED),
                _task("task-valve", "goal-valve", TaskAct.SELECT, TaskStatus.IN_PROGRESS),
            ),
            "delivered_selection_scopes": (
                _selection_scope("goal-pump-a", "task-pump-a", "selection-a", ("A",)),
                _selection_scope("goal-pump-b", "task-pump-b", "selection-b", ("B",)),
            ),
        }
    )

    resolution = resolve_goal_reactivation("Вернёмся к насосу", state)

    assert resolution.status == "ambiguous"
    assert set(resolution.candidate_goal_ids) == {"goal-pump-a", "goal-pump-b"}


def test_controller_uses_deterministic_return_even_when_semantic_operation_is_continue() -> None:
    state = _state()
    semantic = SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=_turn(acts=["explain"]),
        goal_reactivation=resolve_goal_reactivation("Вернёмся к насосу", state),
    )

    outcome = DialogueControllerV2().run(
        state,
        semantic,
        TurnMetadata(turn_id="controller-return-pump"),
    )

    assert outcome.status == "applied"
    assert outcome.state_after.active_goal_id == "goal-pump"
