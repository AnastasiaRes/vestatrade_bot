from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.catalog_v2.contracts import (
    CandidateStatus,
    CatalogSearchStage,
    ProductKind,
    ReadinessStatus,
)
from app.catalog_v2.normalization import build_catalog_snapshot
from app.config import get_settings
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TurnMetadata
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


def _runtime():
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
    products = FeedLoader().parse_xml(
        (Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml").read_bytes()
    )
    return (
        SemanticInterpreter(OpenRouterClient(settings), model=model),
        DialogueControllerV2(),
        build_catalog_snapshot(products),
    )


@pytest.fixture(scope="module")
def runtime():
    return _runtime()


def _run(runtime, message: str, case: str):
    interpreter, controller, catalog = runtime
    context = SessionState(session_id=f"stage3-live-{case}")
    semantic = interpreter.interpret(message, context)
    if semantic.status != "accepted" or semantic.understanding is None:
        pytest.xfail(
            "Stage 1 semantic rejection, not Stage 3: "
            f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
        )
    outcome = controller.run(
        DialogueStateV2(),
        semantic,
        TurnMetadata(turn_id=f"stage3-live-{case}"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=True,
        catalog_snapshot=catalog,
    )
    assert outcome.status == "applied"
    assert outcome.catalog_planning is not None
    diagnostic = json.dumps(
        {
            "understanding": semantic.understanding.model_dump(mode="json"),
            "events": [item.model_dump(mode="json") for item in outcome.reduction.events],
            "state": outcome.state_after.model_dump(mode="json"),
            "action": outcome.next_action_plan.model_dump(mode="json"),
            "catalog": outcome.catalog_planning.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return outcome, semantic.understanding, diagnostic


def test_live_target_pump_beats_radiator_context(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "В системе уже стоят радиаторы; приобрести хочу именно циркуляционный насос.",
        "target-context",
    )
    assert outcome.catalog_planning.contract_resolutions[0].product_kind == ProductKind.CIRCULATION_PUMP, diagnostic


def test_live_pipe_is_not_confused_with_installation_tool(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Паяльник и насадки у монтажника уже есть. Мне нужна сама белая PPR-труба диаметром 25 мм.",
        "pipe-tool",
    )
    assert outcome.catalog_planning.contract_resolutions[0].product_kind == ProductKind.PIPE, diagnostic
    assert all(plan.requested_role.value == "base_product" for plan in outcome.catalog_planning.search_plans), diagnostic


def test_live_reducing_coupling_keeps_both_diameters(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Ищу PPR-переход: с пятидесятой трубы на тридцать вторую, нужна именно переходная муфта.",
        "reducer-two-diameters",
    )
    resolution = outcome.catalog_planning.contract_resolutions[0]
    assessment = outcome.catalog_planning.readiness_assessments[0]
    assert resolution.product_kind == ProductKind.REDUCING_COUPLING, diagnostic
    known = {item.name for item in assessment.confirmed_hard_facts}
    assert {"diameter_mm", "secondary_diameter_mm"} <= known, diagnostic


def test_live_tee_contract_never_requires_pipe_length(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Нужен канализационный тройник 110 на 50 под 87 градусов, для внутренней канализации.",
        "tee-no-length",
    )
    assessment = outcome.catalog_planning.readiness_assessments[0]
    assert assessment.product_kind == ProductKind.TEE, diagnostic
    assert "length_mm" not in assessment.missing_decision_facts, diagnostic
    assert assessment.recommended_question_fact != "length_mm", diagnostic


def test_live_drainage_pump_does_not_mix_with_circulation(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Надо откачивать воду из затопленного подвала — подберите дренажный, не отопительный циркуляционный насос.",
        "drainage-kind",
    )
    assert outcome.catalog_planning.contract_resolutions[0].product_kind == ProductKind.DRAINAGE_PUMP, diagnostic


def test_live_unknown_parameter_takes_preliminary_path(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Для отопления ищу циркуляционный 25/6; монтажную длину не знаю, покажите предварительно по этим данным.",
        "unknown-parameter",
    )
    assessment = outcome.catalog_planning.readiness_assessments[0]
    assert "mounting_length_mm" in assessment.unknown_facts, diagnostic
    assert assessment.status == ReadinessStatus.PRELIMINARY_READY, diagnostic
    assert outcome.next_action_plan.primary.kind != NextActionKind.ASK_DECISION_CHANGING_QUESTION, diagnostic


def test_live_required_fact_absent_in_feed_is_unverified(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Нужен циркуляционный насос 25/6 длиной 130 мм, и расход ровно 1234 л/ч для меня обязателен.",
        "missing-feed-fact",
    )
    plans = outcome.catalog_planning.search_plans
    assert plans, diagnostic
    assert any(
        item.status == CandidateStatus.UNVERIFIED and "max_flow_l_h" in item.missing_hard_facts
        for item in plans[0].candidate_assessments
    ), diagnostic


def test_live_analogue_request_never_relaxes_hard_size(runtime) -> None:
    outcome, understanding, diagnostic = _run(
        runtime,
        "Найдите аналог шарового крана: строго 3/4 дюйма и резьба внутренняя-наружная, а форма корпуса не так важна.",
        "analogue-hard-soft",
    )
    plan = outcome.catalog_planning.search_plans[0]
    assert all(not item.mismatched_hard_facts for item in plan.candidate_assessments if item.status == CandidateStatus.ELIGIBLE), diagnostic
    assert all(relax.fact_name not in {"connection_size", "connection_pattern"} for item in plan.candidate_assessments for relax in item.relaxations), diagnostic


def test_live_two_products_create_bom_not_one_sku(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Подберите две отдельные позиции: PPR-трубу 20 мм и шаровой кран 1/2 дюйма внутренняя-внутренняя резьба.",
        "two-products",
    )
    solution = outcome.catalog_planning.solution_plan
    assert solution is not None, diagnostic
    assert len(solution.components) == 2, diagnostic
    assert all(item.quantity is None for item in solution.components), diagnostic


def test_live_no_match_ends_in_typed_honest_stage(runtime) -> None:
    outcome, _, diagnostic = _run(
        runtime,
        "Нужна именно PPR-труба диаметром 999 мм, другой диаметр не подойдёт.",
        "typed-no-match",
    )
    plan = outcome.catalog_planning.search_plans[0]
    assert CatalogSearchStage.HONEST_NO_MATCH in plan.stages, diagnostic
    assert not plan.eligible_skus and not plan.relaxed_skus, diagnostic
