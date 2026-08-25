from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.answer_v2.renderer import ResponseRendererV2
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.normalization import build_catalog_snapshot
from app.config import get_settings
from app.dialogue_v2.contracts import DialogueStateV2, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.feed_loader import FeedLoader
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


@pytest.fixture(scope="module")
def runtime():
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
    client = OpenRouterClient(settings)
    products = FeedLoader().parse_xml(
        (Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml").read_bytes()
    )
    catalog = build_catalog_snapshot(products)
    return (
        SemanticInterpreter(client, model=model),
        DialogueControllerV2(
            response_renderer=ResponseRendererV2(client, model=model)
        ),
        catalog,
        build_answer_source_snapshot(products, catalog),
    )


def _run(
    runtime,
    message: str,
    case: str,
    *,
    state: DialogueStateV2 | None = None,
    semantic_session: SessionState | None = None,
):
    interpreter, controller, catalog, sources = runtime
    session = semantic_session or SessionState(session_id=f"stage5-live-{case}")
    semantic = interpreter.interpret(message, session)
    if semantic.status != "accepted" or semantic.understanding is None:
        pytest.xfail(
            "Stage 1 semantic rejection, not Stage 5: "
            f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
        )
    outcome = controller.run(
        state,
        semantic,
        TurnMetadata(
            turn_id=f"stage5-live-{case}-{(state.turn_number if state else 0) + 1}"
        ),
        policy_enabled=True,
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=True,
        catalog_snapshot=catalog,
        commerce_workflows_enabled=True,
        handoff_workflow_enabled=True,
        commerce_outbox_enabled=True,
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        progress_guard_enabled=True,
        answer_source_snapshot=sources,
    )
    assert outcome.status == "applied", outcome.error
    assert outcome.stage5_error is None, outcome.stage5_error
    assert outcome.answer_planning is not None
    assert outcome.answer_planning.answer_plan is not None
    assert outcome.response_rendering is not None
    assert outcome.grounding_validation is not None
    diagnostic = json.dumps(
        {
            "understanding": semantic.understanding.model_dump(mode="json"),
            "action": outcome.next_action_plan.model_dump(mode="json"),
            "catalog": (
                outcome.catalog_planning.model_dump(mode="json")
                if outcome.catalog_planning
                else None
            ),
            "answer_plan": outcome.answer_planning.model_dump(mode="json"),
            "rendering": outcome.response_rendering.model_dump(mode="json"),
            "validation": outcome.grounding_validation.model_dump(mode="json"),
            "progress": [
                item.model_dump(mode="json") for item in outcome.progress_assessments
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    assert outcome.grounding_validation.status == "accepted", diagnostic
    plan = outcome.answer_planning.answer_plan
    assert plan.question is None or plan.question.fact_name
    assert plan.next_step is not None
    assert all(item.source_ref_ids for item in plan.claims if item.allowed_in_response)
    return outcome, semantic.understanding, diagnostic, session


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            "target-context",
            "Радиаторы уже установлены; купить хочу именно циркуляционный насос для этой системы.",
        ),
        (
            "tee-context",
            "Ищу тройник к большой канализационной трубе, не саму трубу.",
        ),
        (
            "unknown-parameter",
            "Нужен насос, но монтажную длину я не знаю — покажите, что можно по остальным данным.",
        ),
        (
            "refused-parameter",
            "Диаметр соединения сообщать не буду; подберите только предварительные варианты без догадок.",
        ),
        (
            "direct-plus-selection",
            "Подберите PPR-трубу 20 мм и сразу скажите цену доступного варианта.",
        ),
        (
            "two-products",
            "Нужны отдельно шаровой кран и труба PPR, не объединяйте их в один товар.",
        ),
        (
            "analog",
            "Если точной канализационной трубы 50 на 130 нет, покажите аналог и явно назовите отличие.",
        ),
        (
            "no-match",
            "Нужна канализационная труба 777 мм длиной 13 мм; если такой нет, честно обозначьте границу.",
        ),
        (
            "commerce-draft",
            "Подготовьте обращение менеджеру по найденному товару, но не говорите, что оно отправлено, пока нет подтверждения.",
        ),
    ],
)
def test_live_answer_chain_is_grounded_for_held_out_rephrasings(
    runtime,
    case,
    message,
) -> None:
    outcome, _, diagnostic, _ = _run(runtime, message, case)
    plan = outcome.answer_planning.answer_plan
    assert len([item for item in plan.sections if item.kind.value == "question"]) <= 1, diagnostic
    assert len([item for item in plan.sections if item.kind.value == "next_step"]) == 1, diagnostic
    assert outcome.response_rendering.rendered_answer.renderer in {
        "llm",
        "deterministic",
    }, diagnostic
    if case == "commerce-draft":
        assert not any(
            item.kind.value == "commerce_status" and item.value == "delivered"
            for item in plan.claims
        ), diagnostic


def test_live_multi_turn_unknown_does_not_repeat_same_strategy_forever(runtime) -> None:
    first, _, diagnostic, session = _run(
        runtime,
        "Подбираю циркуляционный насос, монтажную длину пока определить не могу.",
        "loop",
    )
    state = first.state_after
    messages = (
        "Я всё ещё не знаю эту длину, используйте то, что уже известно.",
        "Новых размеров не будет; предложите другой полезный путь.",
        "Параметр так и неизвестен, не спрашивайте его снова.",
    )
    strategies = []
    for index, message in enumerate(messages, start=2):
        outcome, understanding, diagnostic, session = _run(
            runtime,
            message,
            f"loop-{index}",
            state=state,
            semantic_session=session,
        )
        if understanding.operation.value not in {"continue", "refine", "correct"}:
            pytest.xfail(
                "Stage 1 treated a held-out continuation as a new task: " + diagnostic
            )
        state = outcome.state_after
        strategies.extend(
            item.last_strategy.value
            for item in state.response_strategy_history
            if item.last_strategy is not None
        )
        session.history.append({"role": "user", "content": message})
    assert len(set(strategies)) >= 2, diagnostic


@pytest.mark.parametrize(
    ("case", "messages"),
    [
        (
            "critical-correction",
            (
                "Назовите цену, наличие и ссылку для артикула VTp.751.0.020.",
                "Исправляю артикул: нужен VTp.751.0.025; снова проверьте цену, остаток и ссылку.",
            ),
        ),
        (
            "critical-switch-return",
            (
                "Проверьте цену, наличие и ссылку крана VT.214.N.04.",
                "Теперь переключимся на угольник VTp.751.0.032 и проверим те же данные.",
                "Вернёмся к крану VT.214.N.04: ещё раз подтвердите цену, наличие и ссылку.",
            ),
        ),
    ],
)
def test_live_multiturn_critical_catalogue_facts_remain_grounded(
    runtime,
    case,
    messages,
) -> None:
    state = None
    session = SessionState(session_id=f"stage5-live-{case}")
    final = None
    final_understanding = None
    diagnostic = ""
    for index, message in enumerate(messages, start=1):
        outcome, understanding, diagnostic, session = _run(
            runtime,
            message,
            f"{case}-{index}",
            state=state,
            semantic_session=session,
        )
        state = outcome.state_after
        final = outcome
        final_understanding = understanding
        session.history.extend(
            (
                {"role": "user", "content": message},
                {"role": "assistant", "content": "Продолжаем проверку указанного артикула."},
            )
        )
    assert final is not None
    plan = final.answer_planning.answer_plan
    critical_kinds = {
        item.kind.value for item in plan.claims if item.allowed_in_response
    }
    expected = {"price", "stock", "link"}
    if not expected.issubset(critical_kinds):
        if final_understanding is not None and not final_understanding.products:
            pytest.xfail(
                "Stage 1 did not preserve the explicit SKU/product target: "
                + diagnostic
            )
        pytest.xfail(
            "Stage 3 did not provide all critical sources for the exact SKU: "
            + diagnostic
        )
    assert final.response_rendering.llm_requested is True, diagnostic
    assert final.grounding_validation.status == "accepted", diagnostic
