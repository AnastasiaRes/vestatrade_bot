from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.dialogue_v2.contracts import DialogueStateV2
from app.models import SessionState
from app.session_store import InMemorySessionStore, RedisSessionStore


class V2SemanticClient:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.last_fallback_reason = None
        self.semantic_calls = 0
        self._completions: list[str] = []

    def request_budget(self):
        return nullcontext()

    def begin_turn_recording(self) -> None:
        self._completions = []

    def recorded_completions(self) -> list[str]:
        return list(self._completions)

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if agent not in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }:
            return fallback, False
        self.semantic_calls += 1
        payload = json.loads(messages[-1]["content"])
        current_message = payload["current_message"]
        is_boiler = "котёл" in current_message.casefold()
        product = "котёл" if is_boiler else "насос"
        category = "boilers" if is_boiler else "pumps"
        return {
            "schema_version": "1.0",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": product,
                    "canonical_type": product,
                    "category": category,
                    "role": "target",
                    "evidence": product,
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }, True


def _settings(tmp_path, *, v2: bool, name: str):
    return get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / f"{name}.jsonl",
            "semantic_shadow_enabled": False,
            "semantic_shadow_model": "test/semantic-model",
            "dialogue_state_v2_shadow_enabled": v2,
            "seller_policy_v2_shadow_enabled": v2,
        }
    )


def _legacy_state(state: SessionState) -> dict[str, Any]:
    return state.model_dump(
        mode="json",
        exclude={"session_id", "dialogue_state_v2"},
    )


def test_shadow_on_off_preserves_legacy_response_and_state(
    sample_products,
    tmp_path,
) -> None:
    baseline_settings = _settings(tmp_path, v2=False, name="off")
    shadow_settings = _settings(tmp_path, v2=True, name="on")
    baseline = ChatOrchestrator(
        settings=baseline_settings,
        products=sample_products,
        llm_client=V2SemanticClient(baseline_settings),
    )
    shadow_client = V2SemanticClient(shadow_settings)
    shadow = ChatOrchestrator(
        settings=shadow_settings,
        products=sample_products,
        llm_client=shadow_client,
    )

    baseline_response = baseline.handle_chat("off-session", "Покажите насос")
    shadow_response = shadow.handle_chat("on-session", "Покажите насос")

    assert shadow_client.semantic_calls == 2
    assert shadow_response.model_dump(exclude={"session_id"}) == (
        baseline_response.model_dump(exclude={"session_id"})
    )
    baseline_state = baseline.sessions.snapshot("off-session")
    shadow_state = shadow.sessions.snapshot("on-session")
    assert _legacy_state(shadow_state) == _legacy_state(baseline_state)
    assert baseline_state.dialogue_state_v2 is None
    assert shadow_state.dialogue_state_v2 is not None

    trace = json.loads(
        shadow_settings.diagnostic_trace_path.read_text(
            encoding="utf-8"
        ).splitlines()[0]
    )
    assert trace["dialogue_v2_shadow"]["status"] == "applied"
    assert trace["dialogue_v2_shadow"]["state_before"]["schema_version"] == "2.0"
    assert trace["dialogue_v2_shadow"]["state_after"]["turn_number"] == 1
    assert trace["dialogue_v2_shadow"]["reduction"]["events"]
    assert trace["v2_next_action"]["primary"]["kind"]
    assert "selected_next_action" in trace
    assert "v2_legacy_decision_divergence" in trace


def test_reducer_or_policy_failure_does_not_break_legacy_response(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, v2=True, name="failure")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=V2SemanticClient(settings),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("shadow reducer failed")

    bot.dialogue_controller_v2.run = fail
    response = bot.handle_chat("failure-session", "Покажите насос")

    assert response.answer
    state = bot.sessions.snapshot("failure-session")
    assert state.dialogue_state_v2 is None
    trace = json.loads(
        settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert trace["dialogue_v2_shadow"]["status"] == "failed"
    assert "shadow reducer failed" in trace["dialogue_v2_shadow"]["error"]
    assert trace["error"] is None


def test_two_parallel_sessions_do_not_mix_v2_state(sample_products, tmp_path) -> None:
    settings = _settings(tmp_path, v2=True, name="parallel")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=V2SemanticClient(settings),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bot.handle_chat, "pump-session", "Покажите насос")
        second = pool.submit(bot.handle_chat, "boiler-session", "Покажите котёл")
        assert first.result().answer
        assert second.result().answer

    pump = bot.sessions.snapshot("pump-session").dialogue_state_v2
    boiler = bot.sessions.snapshot("boiler-session").dialogue_state_v2
    assert pump is not None and boiler is not None
    pump_goal = next(goal for goal in pump.product_goals if goal.goal_id == pump.active_goal_id)
    boiler_goal = next(
        goal for goal in boiler.product_goals if goal.goal_id == boiler.active_goal_id
    )
    assert pump_goal.category.value == "pumps"
    assert boiler_goal.category.value == "boilers"
    assert set(pump.applied_turn_ids).isdisjoint(boiler.applied_turn_ids)


def test_v2_state_round_trips_through_in_memory_and_redis_serializers() -> None:
    v2 = DialogueStateV2(turn_number=3, applied_turn_ids=("a", "b", "c"))
    state = SessionState(session_id="roundtrip", dialogue_state_v2=v2)

    memory = InMemorySessionStore()
    memory.save(state)
    assert memory.snapshot("roundtrip").dialogue_state_v2 == v2

    encoded = RedisSessionStore._encode(state)
    restored = RedisSessionStore._decode(encoded)
    assert restored.dialogue_state_v2 == v2
    assert restored.dialogue_state_v2.schema_version == "2.0"


def test_old_serialized_session_without_v2_field_remains_compatible() -> None:
    restored = SessionState.model_validate_json('{"session_id":"legacy"}')

    assert restored.dialogue_state_v2 is None
