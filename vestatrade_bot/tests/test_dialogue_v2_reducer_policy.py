from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agents.semantic_interpreter import TurnUnderstanding
from app.agents.semantic_interpreter import SemanticInterpretationResult
from app.dialogue_v2.contracts import (
    ConstraintStatus,
    DialogueStateV2,
    NextActionKind,
    ProgressKind,
    TaskStatus,
    TurnMetadata,
)
from app.dialogue_v2.reducer import reduce_dialogue_state
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
            "answers_pending_question": pending_answer,
            "confidence": 0.94,
        }
    )


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


def _active_goal(state: DialogueStateV2):
    return next(
        goal for goal in state.product_goals if goal.goal_id == state.active_goal_id
    )


def _active_facts(state: DialogueStateV2):
    return [fact for fact in state.constraints if fact.active]


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
    assert plan.secondary.kind == NextActionKind.SEARCH_EXACT


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
