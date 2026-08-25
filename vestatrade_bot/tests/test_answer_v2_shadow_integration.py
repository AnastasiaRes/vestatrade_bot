from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.dialogue_v2.contracts import DialogueStateV2
from app.models import SessionState
from app.session_store import InMemorySessionStore, RedisSessionStore


class Stage5Client:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.last_fallback_reason = None
        self.semantic_calls = 0
        self.renderer_calls = 0

    def request_budget(self):
        return nullcontext()

    def begin_turn_recording(self) -> None:
        pass

    def recorded_completions(self) -> list[str]:
        return []

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ):
        del model
        if agent == "ResponseRendererV2.shadow":
            self.renderer_calls += 1
            return json.loads(json.dumps(fallback)), True
        if agent not in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }:
            return fallback, False
        self.semantic_calls += 1
        payload = json.loads(messages[-1]["content"])
        current = payload.get("current_message") or ""
        product = "труба"
        category = "pipes"
        if "насос" in current.casefold():
            product = "циркуляционный насос"
            category = "pumps"
        evidence = "насос" if "насос" in current.casefold() else product
        return {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": product,
                    "canonical_type": product,
                    "category": category,
                    "role": "target",
                    "evidence": evidence,
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.98,
        }, True


def _settings(tmp_path, *, stage5: bool, name: str):
    return get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / f"{name}.jsonl",
            "semantic_shadow_enabled": False,
            "dialogue_state_v2_shadow_enabled": stage5,
            "seller_policy_v2_shadow_enabled": stage5,
            "product_contracts_v2_shadow_enabled": stage5,
            "catalog_planner_v2_shadow_enabled": stage5,
            "solution_plan_v2_shadow_enabled": stage5,
            "answer_plan_v2_shadow_enabled": stage5,
            "response_renderer_v2_shadow_enabled": stage5,
            "response_grounding_v2_shadow_enabled": stage5,
            "progress_guard_v2_shadow_enabled": stage5,
        }
    )


def _legacy(state: SessionState):
    return state.model_dump(mode="json", exclude={"session_id", "dialogue_state_v2"})


def test_stage5_shadow_on_off_preserves_legacy_response_and_emits_grounded_trace(
    sample_products,
    tmp_path,
) -> None:
    off_settings = _settings(tmp_path, stage5=False, name="stage5-off")
    on_settings = _settings(tmp_path, stage5=True, name="stage5-on")
    off_client = Stage5Client(off_settings)
    on_client = Stage5Client(on_settings)
    off = ChatOrchestrator(
        settings=off_settings,
        products=sample_products,
        llm_client=off_client,
    )
    on = ChatOrchestrator(
        settings=on_settings,
        products=sample_products,
        llm_client=on_client,
    )

    off_response = off.handle_chat("stage5-off", "Покажите труба")
    on_response = on.handle_chat("stage5-on", "Покажите труба")

    assert on_response.model_dump(exclude={"session_id"}) == off_response.model_dump(
        exclude={"session_id"}
    )
    assert _legacy(on.sessions.snapshot("stage5-on")) == _legacy(
        off.sessions.snapshot("stage5-off")
    )
    assert off_client.renderer_calls == 0
    assert on_client.renderer_calls == 1
    state = on.sessions.snapshot("stage5-on").dialogue_state_v2
    assert state is not None
    assert state.answer_plan_summary is not None
    assert state.answer_plan_summary.delivery_status.value == "shadow_not_delivered"

    trace = json.loads(on_settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["current_message"] is None
    assert trace["answer_plan_v2_shadow"]["status"] == "planned"
    assert trace["response_renderer_v2_shadow"] is not None
    assert trace["response_grounding_v2_shadow"]["status"] == "accepted"
    assert trace["task_progress_v2_shadow"]
    assert trace["stage5_error"] is None
    assert trace["stage5_latency_ms"] >= 0
    assert trace["answer_plan_v2_legacy_divergence"] is not None


def test_stage5_renderer_failure_is_contained_and_legacy_succeeds(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, stage5=True, name="stage5-failure")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage5Client(settings),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("stage5 renderer failed")

    bot.dialogue_controller_v2.response_renderer.render = fail
    response = bot.handle_chat("stage5-failure", "Покажите труба")
    assert response.answer
    state = bot.sessions.snapshot("stage5-failure").dialogue_state_v2
    assert state is not None
    assert state.catalog_planning is not None
    assert state.answer_plan_summary is None
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert "stage5 renderer failed" in trace["stage5_error"]
    assert trace["error"] is None


@pytest.mark.parametrize(
    ("component", "error_prefix"),
    [
        ("compiler", "answer_pipeline"),
        ("validator", "answer_pipeline"),
        ("progress", "progress_or_strategy"),
    ],
)
def test_other_stage5_component_failures_are_contained_and_localized(
    sample_products,
    tmp_path,
    monkeypatch,
    component,
    error_prefix,
) -> None:
    import app.dialogue_v2.controller as controller_module

    settings = _settings(tmp_path, stage5=True, name=f"stage5-{component}-failure")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage5Client(settings),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{component} failed")

    target = {
        "compiler": "build_answer_plan",
        "validator": "validate_rendered_answer",
        "progress": "assess_task_progress",
    }[component]
    monkeypatch.setattr(controller_module, target, fail)
    response = bot.handle_chat(f"stage5-{component}", "Покажите труба")
    assert response.answer
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["error"] is None
    assert error_prefix in trace["stage5_error"]
    assert f"{component} failed" in trace["stage5_error"]


def test_stage5_telemetry_does_not_store_raw_contact_or_message(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, stage5=True, name="stage5-pii")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage5Client(settings),
    )
    raw_contact = "+7 999 123-45-67"
    response = bot.handle_chat(
        "stage5-pii",
        f"Покажите труба, мой телефон {raw_contact}",
    )
    assert response.answer
    raw_trace = settings.diagnostic_trace_path.read_text()
    trace = json.loads(raw_trace.splitlines()[0])
    assert trace["current_message"] is None
    assert raw_contact not in raw_trace
    state = bot.sessions.snapshot("stage5-pii").dialogue_state_v2
    assert state is not None
    assert raw_contact not in json.dumps(state.model_dump(mode="json"), ensure_ascii=False)


def test_parallel_sessions_do_not_mix_answer_plans_or_strategy_history(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, stage5=True, name="stage5-parallel")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage5Client(settings),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        pipe = pool.submit(bot.handle_chat, "stage5-pipe", "Покажите труба")
        pump = pool.submit(bot.handle_chat, "stage5-pump", "Покажите насос")
        assert pipe.result().answer
        assert pump.result().answer
    first = bot.sessions.snapshot("stage5-pipe").dialogue_state_v2
    second = bot.sessions.snapshot("stage5-pump").dialogue_state_v2
    assert first is not None and second is not None
    assert first.answer_plan_summary.plan_id != second.answer_plan_summary.plan_id
    assert set(first.applied_turn_ids).isdisjoint(second.applied_turn_ids)
    assert {item.task_id for item in first.response_strategy_history}.isdisjoint(
        {item.task_id for item in second.response_strategy_history}
    )


def test_stage5_state_round_trips_and_old_v2_state_remains_compatible() -> None:
    old = DialogueStateV2.model_validate({"schema_version": "2.0", "turn_number": 2})
    assert old.answer_plan_summary is None
    assert old.response_strategy_history == ()
    state = SessionState(session_id="stage5-roundtrip", dialogue_state_v2=old)

    memory = InMemorySessionStore()
    memory.save(state)
    assert memory.snapshot("stage5-roundtrip").dialogue_state_v2 == old

    encoded = RedisSessionStore._encode(state)
    restored = RedisSessionStore._decode(encoded)
    assert restored.dialogue_state_v2 == old
