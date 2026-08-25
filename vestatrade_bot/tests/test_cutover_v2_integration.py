from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from app.agents.semantic_interpreter import SemanticInterpretationResult
from app.agents.orchestrator import ChatOrchestrator
from app.answer_v2.contracts import AnswerPlanStatus
from app.catalog_v2.contracts import ProductKind
from app.config import get_settings
from app.cutover_v2.contracts import (
    MigrationCell,
    MigrationRegistry,
    RolloutStage,
    V2TurnCandidate,
)
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatProductSummary, ChatResponse
from app.session_store import InMemorySessionStore


class _NoNetworkClient:
    def __init__(self, settings) -> None:
        self.settings = settings

    def request_budget(self):
        return nullcontext()


def _write_canary_registry(tmp_path) -> object:
    registry = MigrationRegistry(
        registry_id="test-internal-canary",
        revision="replaced-by-loader-hash",
        cells=(
            MigrationCell(
                cell_id="exact-price-ball-valve",
                task_acts=(TaskAct.CHECK_PRICE,),
                product_kinds=(ProductKind.BALL_VALVE,),
                allowed_answer_statuses=(AnswerPlanStatus.READY,),
                allowed_next_actions=(NextActionKind.ANSWER_DIRECT_QUESTION,),
                stage=RolloutStage.INTERNAL_CANARY,
                canary_percent=5,
                gate_artifact_ref="test-gate-only",
            ),
        ),
    )
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(registry.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path, registry_path):
    return get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "cutover.jsonl",
            "dialogue_v2_routing_enabled": True,
            "dialogue_v2_live_delivery_enabled": True,
            "dialogue_v2_internal_canary_enabled": True,
            "dialogue_v2_internal_canary_percent": 5,
            "dialogue_v2_migration_registry_path": registry_path,
        }
    )


def _eligible_session(registry_revision: str) -> str:
    for index in range(20_000):
        session_id = f"new-canary-{index}"
        fingerprint = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        digest = hashlib.sha256(
            f"{fingerprint}:{registry_revision}".encode("utf-8")
        ).hexdigest()
        if int(digest[:8], 16) % 100 < 5:
            return session_id
    raise AssertionError("no eligible canary session")


def _candidate(
    session_id: str,
    state_after: DialogueStateV2,
    bot: ChatOrchestrator,
) -> V2TurnCandidate:
    source = bot.answer_source_snapshot_v2.product("VT.228.N.04")
    assert source is not None
    response = ChatResponse(
        session_id=session_id,
        answer=(
            f"Кран BASE, артикул {source.sku} — {source.price:g} "
            f"{source.currency}, {source.stock_status}."
        ),
        products=[
            ChatProductSummary(
                sku=source.sku,
                name=source.name,
                price=source.price,
                currency=source.currency,
                stock_status=source.stock_status,
                url=source.url,
                image_url=source.image_url,
            )
        ],
    )
    return V2TurnCandidate(
        turn_id="client-turn-1",
        response=response,
        state_before=DialogueStateV2(),
        state_after=state_after,
        answer_plan_id="plan-1",
        rendered_answer_id="plan-1",
        source_revision=bot.answer_source_snapshot_v2.source_revision,
        catalog_revision=bot.answer_source_snapshot_v2.source_revision,
        validation_status="accepted",
        response_digest=bot._response_digest(response),
        task_acts=(TaskAct.CHECK_PRICE,),
        product_kinds=(ProductKind.BALL_VALVE,),
        contract_versions=("1.0",),
        answer_status=AnswerPlanStatus.READY,
        next_action=NextActionKind.ANSWER_DIRECT_QUESTION,
        product_statuses=("exact",),
        semantic_accepted=True,
        contracts_resolved=True,
        eligible_for_delivery=True,
    )


def test_internal_canary_has_one_owner_and_does_not_execute_legacy(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    state_after = DialogueStateV2(turn_number=1)
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=DialogueStateV2(),
        state_after=state_after,
    )
    semantic_calls = 0

    def semantic(*_args, **_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        return {"status": "accepted"}

    monkeypatch.setattr(bot.semantic_interpreter, "interpret", semantic)
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", lambda *_args: outcome)
    monkeypatch.setattr(
        "app.agents.orchestrator.build_v2_turn_candidate",
        lambda *_args, **_kwargs: _candidate(session_id, state_after, bot),
    )

    def legacy_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy executed for a V2-owned canary turn")

    monkeypatch.setattr(bot, "_handle_chat", legacy_must_not_run)
    first = bot.handle_chat(
        session_id,
        "Сколько стоит VT.228.N.04?",
        "client-turn-1",
    )
    second = bot.handle_chat(
        session_id,
        "Сколько стоит VT.228.N.04?",
        "client-turn-1",
    )

    assert first == second
    assert semantic_calls == 1
    assert [item.sku for item in first.products] == ["VT.228.N.04"]
    assert first.debug == {}
    stored = bot.sessions.snapshot(session_id)
    assert stored.dialogue_state_v2 is None
    assert stored.live_dialogue_state_v2 is not None
    assert stored.live_dialogue_state_v2.live_epoch_id
    assert stored.v2_sticky_assignment_id
    assert stored.v2_migration_cell_id == "exact-price-ball-valve"
    assert stored.session_revision == 1
    assert [item.sku for item in stored.last_products] == ["VT.228.N.04"]
    assert len(stored.idempotent_responses) == 1

    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["cutover_v2"]["decision"]["owner_candidate"] == "v2"
    assert trace["cutover_v2"]["commit"]["committed"] is True
    assert trace["cutover_v2"]["commit"]["live_epoch_id"]
    assert trace["cutover_v2"]["rollout_registry"]["revision"]
    assert trace["cutover_v2"]["runtime_controls"]["force_legacy"] is False
    assert trace["cutover_v2"]["fallback_attempted"] is False
    assert trace["cutover_v2"]["candidate"].get("response") is None


def test_canary_candidate_failure_falls_back_before_commit(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: {"status": "accepted"},
    )
    monkeypatch.setattr(
        bot,
        "_run_stage6_v2_candidate",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("candidate failed")),
    )
    legacy = ChatResponse(session_id=session_id, answer="Legacy fallback")
    monkeypatch.setattr(bot, "_handle_chat", lambda *_args: legacy)

    response = bot.handle_chat(session_id, "Сколько стоит кран?")
    assert response.answer == "Legacy fallback"
    stored = bot.sessions.snapshot(session_id)
    assert stored.live_dialogue_state_v2 is None
    assert stored.session_revision == 1
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["cutover_v2"]["error"] == (
        "RuntimeError:stage6_candidate_or_commit_failed"
    )


def test_early_safety_prevents_semantic_and_v2_execution(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("semantic/V2 executed after safety intercept")

    monkeypatch.setattr(bot.semantic_interpreter, "interpret", forbidden)
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", forbidden)
    safety = ChatResponse(session_id=session_id, answer="Выйдите и звоните 104/112.")
    monkeypatch.setattr(bot, "_handle_chat", lambda *_args: safety)

    response = bot.handle_chat(session_id, "Пахнет газом возле котла, что делать?")
    assert response == safety
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["cutover_v2"]["decision"]["owner_candidate"] == "safety"


def test_pii_turn_stays_legacy_and_trace_contains_no_contact(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    raw_phone = "+7 999 123-45-67"
    legacy = ChatResponse(session_id=session_id, answer="Контакт принят.")
    monkeypatch.setattr(bot, "_handle_chat", lambda *_args: legacy)
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: (_ for _ in ()).throw(AssertionError("PII canary semantic call")),
    )

    response = bot.handle_chat(session_id, f"Мой телефон {raw_phone}")
    assert response == legacy
    raw_trace = settings.diagnostic_trace_path.read_text()
    assert raw_phone not in raw_trace
    trace = json.loads(raw_trace.splitlines()[0])
    assert trace["current_message"] is None
    assert trace["cutover_v2"]["decision"]["owner_candidate"] == "legacy"


def test_all_stage6_flags_off_do_not_execute_semantic_or_v2(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": False,
            "semantic_shadow_enabled": False,
            "dialogue_state_v2_shadow_enabled": False,
            "seller_policy_v2_shadow_enabled": False,
            "product_contracts_v2_shadow_enabled": False,
            "catalog_planner_v2_shadow_enabled": False,
            "solution_plan_v2_shadow_enabled": False,
            "answer_plan_v2_shadow_enabled": False,
            "response_renderer_v2_shadow_enabled": False,
            "response_grounding_v2_shadow_enabled": False,
            "progress_guard_v2_shadow_enabled": False,
            "dialogue_v2_routing_enabled": False,
            "dialogue_v2_shadow_compare_enabled": False,
            "dialogue_v2_live_delivery_enabled": False,
            "dialogue_v2_internal_canary_enabled": False,
            "dialogue_v2_internal_canary_percent": 0,
            "dialogue_v2_force_legacy": False,
        }
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Stage 6 executed with every flag off")

    monkeypatch.setattr(bot.semantic_interpreter, "interpret", forbidden)
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", forbidden)
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda session_id, _message: ChatResponse(
            session_id=session_id,
            answer="Legacy baseline",
        ),
    )

    response = bot.handle_chat("flags-off", "Сколько стоит кран?")
    stored = bot.sessions.snapshot("flags-off")
    assert response.answer == "Legacy baseline"
    assert stored.session_revision == 0
    assert stored.dialogue_state_v2 is None
    assert stored.live_dialogue_state_v2 is None
    assert stored.idempotent_responses == []
    assert stored.history == []


def test_semantic_timeout_falls_back_before_any_v2_commit(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("provider-secret")),
    )
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda *_args: ChatResponse(session_id=session_id, answer="Legacy timeout fallback"),
    )

    response = bot.handle_chat(session_id, "Сколько стоит кран?")
    assert response.answer == "Legacy timeout fallback"
    stored = bot.sessions.snapshot(session_id)
    assert stored.live_dialogue_state_v2 is None
    trace_text = settings.diagnostic_trace_path.read_text()
    assert "provider-secret" not in trace_text
    trace = json.loads(trace_text.splitlines()[0])
    assert trace["cutover_v2"]["error"] == (
        "TimeoutError:stage6_candidate_or_commit_failed"
    )


class _FailFirstV2SaveStore(InMemorySessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def save(self, state) -> None:
        if state.live_dialogue_state_v2 is not None and not self.failed:
            self.failed = True
            raise RuntimeError("redis-credential-like-secret")
        super().save(state)


def test_v2_store_failure_rolls_back_then_legacy_commits_once(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    store = _FailFirstV2SaveStore()
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
        session_store=store,
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    state_after = DialogueStateV2(turn_number=1)
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=DialogueStateV2(),
        state_after=state_after,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: {"status": "accepted"},
    )
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", lambda *_args: outcome)
    monkeypatch.setattr(
        "app.agents.orchestrator.build_v2_turn_candidate",
        lambda *_args, **_kwargs: _candidate(session_id, state_after, bot),
    )
    legacy_calls = 0

    def legacy(*_args):
        nonlocal legacy_calls
        legacy_calls += 1
        return ChatResponse(session_id=session_id, answer="Legacy after rollback")

    monkeypatch.setattr(bot, "_handle_chat", legacy)
    response = bot.handle_chat(session_id, "Сколько стоит кран?", "store-failure-1")

    assert response.answer == "Legacy after rollback"
    assert legacy_calls == 1
    stored = store.snapshot(session_id)
    assert stored.live_dialogue_state_v2 is None
    assert stored.session_revision == 1
    assert len(stored.idempotent_responses) == 1
    trace_text = settings.diagnostic_trace_path.read_text()
    assert "redis-credential-like-secret" not in trace_text


def test_rejected_semantic_protocol_result_uses_legacy_without_live_state(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    semantic = SemanticInterpretationResult(
        status="rejected",
        requested=True,
        transport_succeeded=True,
        output_accepted=False,
        rejection_reason="malformed semantic payload",
    )
    monkeypatch.setattr(bot.semantic_interpreter, "interpret", lambda *_args: semantic)
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda *_args: ChatResponse(session_id=session_id, answer="Legacy protocol fallback"),
    )

    response = bot.handle_chat(session_id, "Сколько стоит кран?")
    assert response.answer == "Legacy protocol fallback"
    stored = bot.sessions.snapshot(session_id)
    assert stored.live_dialogue_state_v2 is None
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["cutover_v2"]["decision"]["owner_candidate"] == "legacy"
    assert "semantic_result_unavailable" in trace["cutover_v2"]["decision"][
        "reason_codes"
    ]


def test_parallel_retry_with_same_client_turn_id_commits_v2_once(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    state_after = DialogueStateV2(turn_number=1)
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=DialogueStateV2(),
        state_after=state_after,
    )
    semantic_calls = 0
    counter_lock = Lock()

    def semantic(*_args):
        nonlocal semantic_calls
        with counter_lock:
            semantic_calls += 1
        return {"status": "accepted"}

    monkeypatch.setattr(bot.semantic_interpreter, "interpret", semantic)
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", lambda *_args: outcome)
    monkeypatch.setattr(
        "app.agents.orchestrator.build_v2_turn_candidate",
        lambda *_args, **_kwargs: _candidate(session_id, state_after, bot),
    )
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("legacy executed for a valid concurrent canary retry")
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                bot.handle_chat,
                session_id,
                "Сколько стоит VT.228.N.04?",
                "same-client-turn",
            )
            for _ in range(2)
        ]
        responses = [future.result(timeout=5) for future in futures]

    assert responses[0] == responses[1]
    assert semantic_calls == 1
    stored = bot.sessions.snapshot(session_id)
    assert stored.session_revision == 1
    assert len(stored.idempotent_responses) == 1
    assert len(stored.live_dialogue_state_v2.response_delivery_history) == 1


def test_identical_messages_with_different_client_turn_ids_are_distinct_turns(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "dialogue_v2_routing_enabled": False,
            "dialogue_v2_shadow_compare_enabled": False,
            "dialogue_v2_live_delivery_enabled": False,
            "dialogue_v2_internal_canary_enabled": False,
        }
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    calls = 0

    def legacy(session_id, _message):
        nonlocal calls
        calls += 1
        return ChatResponse(session_id=session_id, answer=f"Legacy response {calls}")

    monkeypatch.setattr(bot, "_handle_chat", legacy)
    first = bot.handle_chat("distinct-turns", "Повторяю вопрос", "turn-a")
    second = bot.handle_chat("distinct-turns", "Повторяю вопрос", "turn-b")

    assert first.answer != second.answer
    assert calls == 2
    stored = bot.sessions.snapshot("distinct-turns")
    assert stored.session_revision == 2
    assert [item.client_turn_id for item in stored.idempotent_responses] == [
        "turn-a",
        "turn-b",
    ]


def test_stale_catalog_revision_is_rejected_before_commit(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    state_after = DialogueStateV2(turn_number=1)
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=DialogueStateV2(),
        state_after=state_after,
    )
    stale = _candidate(session_id, state_after, bot).model_copy(
        update={
            "source_revision": "stale-revision",
            "catalog_revision": "stale-revision",
        }
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: {"status": "accepted"},
    )
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", lambda *_args: outcome)
    monkeypatch.setattr(
        "app.agents.orchestrator.build_v2_turn_candidate",
        lambda *_args, **_kwargs: stale,
    )
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda *_args: ChatResponse(
            session_id=session_id,
            answer="Legacy after stale catalogue",
        ),
    )

    response = bot.handle_chat(session_id, "Сколько стоит кран?")
    assert response.answer == "Legacy after stale catalogue"
    stored = bot.sessions.snapshot(session_id)
    assert stored.live_dialogue_state_v2 is None
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["cutover_v2"]["error"] == (
        "RuntimeError:stage6_candidate_or_commit_failed"
    )


def test_diagnostic_failure_cannot_rollback_committed_v2_response(
    sample_products,
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = _write_canary_registry(tmp_path)
    settings = _settings(tmp_path, registry_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=_NoNetworkClient(settings),
    )
    session_id = _eligible_session(bot.cutover_registry_v2.revision)
    state_after = DialogueStateV2(turn_number=1)
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=DialogueStateV2(),
        state_after=state_after,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args: {"status": "accepted"},
    )
    monkeypatch.setattr(bot, "_run_stage6_v2_candidate", lambda *_args: outcome)
    monkeypatch.setattr(
        "app.agents.orchestrator.build_v2_turn_candidate",
        lambda *_args, **_kwargs: _candidate(session_id, state_after, bot),
    )
    monkeypatch.setattr(
        "app.diagnostic_telemetry.TurnTrace.record_cutover_v2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("telemetry-secret")
        ),
    )
    monkeypatch.setattr(
        bot,
        "_handle_chat",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("telemetry failure triggered legacy fallback")
        ),
    )

    response = bot.handle_chat(session_id, "Сколько стоит кран?", "telemetry-1")
    stored = bot.sessions.snapshot(session_id)
    assert [item.sku for item in response.products] == ["VT.228.N.04"]
    assert stored.live_dialogue_state_v2 is not None
    assert stored.session_revision == 1
    assert "telemetry-secret" not in settings.diagnostic_trace_path.read_text()
