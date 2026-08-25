from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.answer_v2.contracts import AnswerPlanStatus
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.contracts import ProductKind
from app.catalog_v2.normalization import build_catalog_snapshot
from app.config import get_settings
from app.cutover_v2.assembler import build_v2_turn_candidate
from app.cutover_v2.contracts import (
    EarlyControlResult,
    ExecutionMode,
    MigrationCell,
    MigrationRegistry,
    ResponseOwner,
    RolloutStage,
)
from app.cutover_v2.policy import CutoverRuntime, arbitrate_turn, decide_cutover
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TaskAct, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.feed_loader import FeedLoader
from app.models import SessionState
from app.openrouter_client import OpenRouterClient


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1"
        or os.getenv("RUN_STAGE6_RELEASE_EVALS") != "1"
        or not os.getenv("OPENROUTER_API_KEY"),
        reason=(
            "requires RUN_LIVE_LLM_TESTS=1, RUN_STAGE6_RELEASE_EVALS=1 "
            "and OPENROUTER_API_KEY"
        ),
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
    products = FeedLoader().parse_xml(
        (Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml").read_bytes()
    )
    catalog = build_catalog_snapshot(products)
    sources = build_answer_source_snapshot(products, catalog)
    registry = MigrationRegistry(
        registry_id="stage6-live-gate",
        revision="stage6-live-gate-v1",
        cells=(
            MigrationCell(
                cell_id="exact-catalog-fact-canary",
                task_acts=(
                    TaskAct.CHECK_PRICE,
                    TaskAct.CHECK_STOCK,
                    TaskAct.GET_LINK,
                ),
                product_kinds=tuple(
                    kind for kind in ProductKind if kind != ProductKind.UNSUPPORTED
                ),
                allowed_answer_statuses=(AnswerPlanStatus.READY,),
                allowed_next_actions=(NextActionKind.ANSWER_DIRECT_QUESTION,),
                stage=RolloutStage.INTERNAL_CANARY,
                canary_percent=5,
                gate_artifact_ref="opt-in-live-llm-stage6-gate",
            ),
        ),
    )
    return (
        SemanticInterpreter(OpenRouterClient(settings), model=model),
        DialogueControllerV2(),
        catalog,
        sources,
        registry,
    )


def _eligible_fingerprint(registry_revision: str) -> str:
    for index in range(10_000):
        fingerprint = hashlib.sha256(f"stage6-live-{index}".encode()).hexdigest()
        digest = hashlib.sha256(
            f"{fingerprint}:{registry_revision}".encode()
        ).hexdigest()
        if int(digest[:8], 16) % 100 < 5:
            return fingerprint
    raise AssertionError("could not construct deterministic canary cohort")


@pytest.mark.parametrize(
    ("case", "message", "expected_sku", "expected_act"),
    [
        (
            "price-colloquial",
            "Подскажите, пожалуйста, почём сейчас шаровый кран VT.228.N.04? Нужна цена именно этой позиции.",
            "VT.228.N.04",
            TaskAct.CHECK_PRICE,
        ),
        (
            "price-rephrased",
            "Во сколько обойдётся циркуляционный насос VRS.256.13.0, если брать конкретно его?",
            "VRS.256.13.0",
            TaskAct.CHECK_PRICE,
        ),
        (
            "stock-negative",
            "Есть ли прямо сейчас в продаже кран VT.214.N.09 или его нет на складе?",
            "VT.214.N.09",
            TaskAct.CHECK_STOCK,
        ),
        (
            "stock-positive",
            "Проверьте остаток по PPR-трубе VTp.700.0020.20 — она доступна к покупке?",
            "VTp.700.0020.20",
            TaskAct.CHECK_STOCK,
        ),
        (
            "link-pump",
            "Дайте, пожалуйста, ссылку именно на насос Wilo с артикулом 9168934.",
            "9168934",
            TaskAct.GET_LINK,
        ),
        (
            "link-boiler",
            "Где открыть карточку электрического котла Arderia E9, код товара 2202210?",
            "2202210",
            TaskAct.GET_LINK,
        ),
        (
            "price-sewer-pipe",
            "Сколько стоит конкретная канализационная труба HTEM 50×1500, артикул 112050?",
            "112050",
            TaskAct.CHECK_PRICE,
        ),
        (
            "stock-radiator-valve",
            "Можно уточнить наличие радиаторного клапана VT.037.NRC.04, без подбора замены?",
            "VT.037.NRC.04",
            TaskAct.CHECK_STOCK,
        ),
        (
            "price-typo",
            "Скока щас стоит именно насос VRS.256.18.0, цену по нему глянете?",
            "VRS.256.18.0",
            TaskAct.CHECK_PRICE,
        ),
        (
            "price-irritated",
            "Мне не нужен очередной подбор. Просто назовите цену VT.217.N.04, пожалуйста.",
            "VT.217.N.04",
            TaskAct.CHECK_PRICE,
        ),
        (
            "corrected-identity",
            "Я ошибся: не VT.214.N.04, а VT.215.N.04. Сколько стоит второй кран?",
            "VT.215.N.04",
            TaskAct.CHECK_PRICE,
        ),
        (
            "price-and-stock",
            "По котлу 2201376 ответьте сразу две вещи: какая цена и есть ли он в наличии?",
            "2201376",
            TaskAct.CHECK_PRICE,
        ),
    ],
)
def test_live_exact_catalog_fact_reaches_delivery_gate(
    runtime,
    case: str,
    message: str,
    expected_sku: str,
    expected_act: TaskAct,
) -> None:
    interpreter, controller, catalog, sources, registry = runtime
    session_id = f"stage6-live-{case}"
    semantic = interpreter.interpret(message, SessionState(session_id=session_id))
    if semantic.status != "accepted" or semantic.understanding is None:
        pytest.xfail(
            "Stage 1 semantic rejection, not Stage 6: "
            f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
        )
    turn_id = f"stage6-live-turn-{case}"
    outcome = controller.run(
        DialogueStateV2(),
        semantic,
        TurnMetadata(turn_id=turn_id),
        policy_enabled=True,
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=True,
        catalog_snapshot=catalog,
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        progress_guard_enabled=True,
        answer_source_snapshot=sources,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id=session_id,
        turn_id=turn_id,
    )
    diagnostic = json.dumps(
        {
            "understanding": semantic.understanding.model_dump(mode="json"),
            "outcome_status": outcome.status,
            "stage5_error": outcome.stage5_error,
            "answer_status": (
                outcome.answer_planning.answer_plan.status
                if outcome.answer_planning and outcome.answer_planning.answer_plan
                else None
            ),
            "candidate_skus": (
                outcome.catalog_planning.candidate_skus
                if outcome.catalog_planning
                else ()
            ),
            "candidate": candidate.model_dump(mode="json", exclude={"response"}),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    assert outcome.status == "applied", diagnostic
    assert outcome.stage5_error is None, diagnostic
    assert expected_act in candidate.task_acts, diagnostic
    assert candidate.pending_command_ids == (), diagnostic

    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        registry,
        CutoverRuntime(
            routing_enabled=True,
            shadow_compare_enabled=True,
            live_delivery_enabled=True,
            internal_canary_enabled=True,
            internal_canary_percent=5,
        ),
        session_fingerprint=_eligible_fingerprint(registry.revision),
    )
    arbitration = arbitrate_turn(decision, candidate)
    if decision.owner_candidate == ResponseOwner.V2:
        assert candidate.eligible_for_delivery, diagnostic
        assert candidate.response is not None, diagnostic
        assert [item.sku for item in candidate.response.products] == [expected_sku], diagnostic
        assert candidate.response.debug == {}, diagnostic
        assert decision.execution_mode == ExecutionMode.V2_INTERNAL_CANARY, diagnostic
        assert arbitration.response_owner == ResponseOwner.V2, diagnostic
        assert arbitration.response == candidate.response, diagnostic
    else:
        # A live semantic/upstream limitation is a rollout-gate failure, not a
        # reason to weaken the delivery contract or silently choose a SKU.
        assert (
            candidate.rejection_reason_codes or decision.reason_codes
        ), diagnostic
        assert arbitration.response_owner == ResponseOwner.LEGACY, diagnostic


def test_live_four_turn_references_and_correction_never_deliver_wrong_sku(
    runtime,
) -> None:
    interpreter, controller, catalog, sources, registry = runtime
    session_id = "stage6-live-long-reference"
    semantic_session = SessionState(session_id=session_id)
    state = DialogueStateV2()
    turns = (
        ("Почём сейчас кран VT.228.N.04?", "VT.228.N.04"),
        ("А он точно есть в наличии?", "VT.228.N.04"),
        ("Поправка: я имел в виду VT.218.N.04, сколько стоит он?", "VT.218.N.04"),
        ("Хорошо, дайте ссылку именно на этот исправленный вариант.", "VT.218.N.04"),
    )
    for index, (message, expected_sku) in enumerate(turns, start=1):
        semantic = interpreter.interpret(message, semantic_session)
        if semantic.status != "accepted" or semantic.understanding is None:
            pytest.xfail(
                f"Stage 1 rejected turn {index}, not Stage 6: "
                f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
            )
        outcome = controller.run(
            state,
            semantic,
            TurnMetadata(turn_id=f"stage6-live-long-{index}"),
            policy_enabled=True,
            product_contracts_enabled=True,
            catalog_planner_enabled=True,
            solution_plan_enabled=True,
            catalog_snapshot=catalog,
            answer_plan_enabled=True,
            response_renderer_enabled=True,
            response_grounding_enabled=True,
            progress_guard_enabled=True,
            answer_source_snapshot=sources,
        )
        candidate = build_v2_turn_candidate(
            outcome,
            sources,
            session_id=session_id,
            turn_id=f"stage6-live-long-{index}",
        )
        decision = decide_cutover(
            EarlyControlResult(),
            candidate,
            registry,
            CutoverRuntime(
                routing_enabled=True,
                shadow_compare_enabled=True,
                live_delivery_enabled=True,
                internal_canary_enabled=True,
                internal_canary_percent=5,
            ),
            session_fingerprint=_eligible_fingerprint(registry.revision),
        )
        if decision.owner_candidate == ResponseOwner.V2:
            assert candidate.response is not None
            assert [item.sku for item in candidate.response.products] == [expected_sku]
        else:
            assert candidate.rejection_reason_codes
        state = outcome.state_after
        semantic_session.history.extend(
            (
                {"role": "user", "content": message},
                {"role": "assistant", "content": "Ответ по проверенному каталогу."},
            )
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        (
            "unknown-identity",
            "Артикул я не знаю и размер тоже пока неизвестен; сколько примерно стоит PPR-труба?",
        ),
        (
            "deferred-identity",
            "Код канализационной трубы пришлю позже, а сейчас скажите её наличие без догадок.",
        ),
        (
            "two-identities",
            "Сравните цену и наличие сразу двух кранов: VT.217.N.04 и VT.218.N.04.",
        ),
    ],
)
def test_live_out_of_cell_turn_is_kept_out_of_canary(
    runtime,
    case: str,
    message: str,
) -> None:
    interpreter, controller, catalog, sources, registry = runtime
    session_id = f"stage6-live-blocked-{case}"
    semantic = interpreter.interpret(message, SessionState(session_id=session_id))
    if semantic.status != "accepted" or semantic.understanding is None:
        pytest.xfail(
            "Stage 1 semantic rejection, not Stage 6: "
            f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
        )
    turn_id = f"stage6-live-blocked-turn-{case}"
    outcome = controller.run(
        DialogueStateV2(),
        semantic,
        TurnMetadata(turn_id=turn_id),
        policy_enabled=True,
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=True,
        catalog_snapshot=catalog,
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        progress_guard_enabled=True,
        answer_source_snapshot=sources,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id=session_id,
        turn_id=turn_id,
    )
    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        registry,
        CutoverRuntime(
            routing_enabled=True,
            shadow_compare_enabled=True,
            live_delivery_enabled=True,
            internal_canary_enabled=True,
            internal_canary_percent=5,
        ),
        session_fingerprint=_eligible_fingerprint(registry.revision),
    )

    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert not candidate.eligible_for_delivery
    assert candidate.rejection_reason_codes
