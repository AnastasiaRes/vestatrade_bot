from __future__ import annotations

import json
import os

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.config import get_settings
from app.dialogue_v2.contracts import ConstraintStatus, DialogueStateV2, NextActionKind, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.models import SessionState
from app.openrouter_client import OpenRouterClient


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1"
        or not os.getenv("OPENROUTER_API_KEY"),
        reason="requires RUN_LIVE_LLM_TESTS=1 and OPENROUTER_API_KEY",
    ),
]


def _runtime() -> tuple[SemanticInterpreter, DialogueControllerV2]:
    base = get_settings()
    model = os.getenv(
        "SEMANTIC_LIVE_MODEL",
        os.getenv("OPENROUTER_MODEL_STRONG", base.openrouter_model_strong),
    )
    settings = base.model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": os.environ["OPENROUTER_API_KEY"],
            "openrouter_model": model,
            "openrouter_model_strong": model,
            "llm_max_retries": 1,
        }
    )
    return SemanticInterpreter(OpenRouterClient(settings), model=model), DialogueControllerV2()


def _apply(
    interpreter: SemanticInterpreter,
    controller: DialogueControllerV2,
    message: str,
    *,
    case: str,
    state: DialogueStateV2 | None = None,
    semantic_context: SessionState | None = None,
):
    context = semantic_context or SessionState(session_id=f"live-v2-{case}")
    semantic = interpreter.interpret(message, context)
    assert semantic.status == "accepted", semantic.rejection_reason
    assert semantic.understanding is not None
    outcome = controller.run(
        state,
        semantic,
        TurnMetadata(turn_id=f"{case}-{(state.turn_number if state else 0) + 1}"),
    )
    assert outcome.status == "applied", outcome.error
    assert outcome.reduction is not None
    assert outcome.next_action_plan is not None
    diagnostic = json.dumps(
        {
            "understanding": semantic.understanding.model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in outcome.reduction.events
            ],
            "state": outcome.state_after.model_dump(mode="json"),
            "plan": outcome.next_action_plan.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    context.history.append({"role": "user", "content": message})
    return outcome, semantic.understanding, diagnostic, context


def test_live_v2_keeps_requested_pump_above_radiator_context() -> None:
    interpreter, controller = _runtime()
    outcome, understanding, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Радиаторы у меня уже установлены. Сейчас нужен именно циркуляционный насос — подбери подходящий.",
        case="target-context",
    )

    active = next(
        goal for goal in outcome.state_after.product_goals
        if goal.goal_id == outcome.state_after.active_goal_id
    )
    assert active.category.value == "pumps", diagnostic
    assert all(
        product.category.value != "radiators" or product.role.value != "target"
        for product in understanding.products
    ), diagnostic


def test_live_v2_applies_explicit_goal_correction() -> None:
    interpreter, controller = _runtime()
    first, _, _, context = _apply(
        interpreter,
        controller,
        "Подбери циркуляционный насос для отопления.",
        case="goal-correction",
    )
    corrected, understanding, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Поправлю себя: нужен не циркуляционный, а повысительный насос.",
        case="goal-correction",
        state=first.state_after,
        semantic_context=context,
    )

    active = next(
        goal for goal in corrected.state_after.product_goals
        if goal.goal_id == corrected.state_after.active_goal_id
    )
    assert understanding.operation.value == "correct", diagnostic
    assert (active.canonical_type or "").casefold() in {
        "booster_pump",
        "повысительный насос",
    }, diagnostic
    assert any(
        event.event_type == "product_goal_corrected"
        for event in corrected.reduction.events
    ), diagnostic


def test_live_v2_keeps_unknown_parameter_without_invention() -> None:
    interpreter, controller = _runtime()
    outcome, _, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Нужен циркуляционный насос, но монтажную длину я не знаю; предложи по тем данным, что есть.",
        case="unknown",
    )

    facts = [fact for fact in outcome.state_after.constraints if fact.active]
    assert any(
        fact.status == ConstraintStatus.UNKNOWN and fact.value is None
        for fact in facts
    ), diagnostic
    assert (
        outcome.next_action_plan.primary.kind
        == NextActionKind.SHOW_PRELIMINARY_OPTIONS
    ), diagnostic


def test_live_v2_preserves_explicit_refusal_without_reasking() -> None:
    interpreter, controller = _runtime()
    outcome, _, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Диаметр подключения сообщать не буду. Покажи предварительно подходящие насосы без этого параметра.",
        case="refused",
    )

    assert any(
        fact.active
        and fact.status == ConstraintStatus.REFUSED
        and fact.value is None
        for fact in outcome.state_after.constraints
    ), diagnostic
    assert (
        outcome.next_action_plan.primary.kind
        != NextActionKind.ASK_DECISION_CHANGING_QUESTION
    ), diagnostic


def test_live_v2_prioritizes_price_question_and_keeps_selection_secondary() -> None:
    interpreter, controller = _runtime()
    outcome, understanding, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Подбери настенный электрический котёл и сразу скажи, сколько он будет стоить.",
        case="direct-selection",
    )

    assert {act.value for act in understanding.acts} >= {
        "select",
        "check_price",
    }, diagnostic
    assert (
        outcome.next_action_plan.primary.kind
        == NextActionKind.ANSWER_DIRECT_QUESTION
    ), diagnostic
    assert outcome.next_action_plan.secondary is not None, diagnostic


def test_live_v2_preserves_price_stock_and_link_as_separate_actions() -> None:
    interpreter, controller = _runtime()
    outcome, understanding, diagnostic, _ = _apply(
        interpreter,
        controller,
        "По этому водонагревателю проверь отдельно цену, остаток и дай ссылку на карточку.",
        case="multi-actions",
    )

    acts = {act.value for act in understanding.acts}
    assert acts >= {"check_price", "check_stock", "get_link"}, diagnostic
    current_tasks = [
        task for task in outcome.state_after.tasks
        if task.source_turn == outcome.state_after.turn_number
    ]
    assert {task.act.value for task in current_tasks} >= {
        "check_price",
        "check_stock",
        "get_link",
    }, diagnostic


def test_live_v2_keeps_two_requested_products_as_separate_goals_and_tasks() -> None:
    interpreter, controller = _runtime()
    outcome, _, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Подбери для проекта и циркуляционный насос, и электрический котёл — нужны оба товара.",
        case="two-products",
    )

    target_goals = [
        goal for goal in outcome.state_after.product_goals
        if goal.role.value == "target"
    ]
    assert {goal.category.value for goal in target_goals} >= {
        "pumps",
        "boilers",
    }, diagnostic
    current_goal_ids = {
        task.target_goal_id
        for task in outcome.state_after.tasks
        if task.source_turn == outcome.state_after.turn_number
    }
    assert len(current_goal_ids) >= 2, diagnostic


def test_live_v2_switches_and_then_returns_to_suspended_task() -> None:
    interpreter, controller = _runtime()
    first, _, _, context = _apply(
        interpreter,
        controller,
        "Сначала помоги выбрать циркуляционный насос с подключением 25 миллиметров.",
        case="switch-return",
    )
    pump_goal_id = first.state_after.active_goal_id
    switched, switched_understanding, diagnostic, context = _apply(
        interpreter,
        controller,
        "Насос пока отложим, переключимся на электрический котёл.",
        case="switch-return",
        state=first.state_after,
        semantic_context=context,
    )
    assert switched_understanding.operation.value == "switch", diagnostic
    returned, return_understanding, diagnostic, _ = _apply(
        interpreter,
        controller,
        "Электрический котёл поставим на паузу. Вернёмся: насос из первой задачи снова в приоритете.",
        case="switch-return",
        state=switched.state_after,
        semantic_context=context,
    )

    assert return_understanding.operation.value == "return", diagnostic
    assert returned.state_after.active_goal_id == pump_goal_id, diagnostic
    assert any(
        fact.active and fact.goal_id == pump_goal_id and "25" in str(fact.value)
        for fact in returned.state_after.constraints
    ), diagnostic
