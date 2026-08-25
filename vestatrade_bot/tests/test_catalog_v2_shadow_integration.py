from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.models import SessionState


class Stage3SemanticClient:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.last_fallback_reason = None
        self.semantic_interpreter_calls = 0

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
        if agent not in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }:
            return fallback, False
        if agent == "SemanticInterpreter.shadow":
            self.semantic_interpreter_calls += 1
        payload = json.loads(messages[-1]["content"])
        message = payload["current_message"]
        return {
            "schema_version": "1.0",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "труба",
                    "canonical_type": "труба",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "труба",
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }, True


def _settings(tmp_path, *, stage3: bool, name: str):
    return get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / f"{name}.jsonl",
            "semantic_shadow_enabled": False,
            "dialogue_state_v2_shadow_enabled": stage3,
            "seller_policy_v2_shadow_enabled": stage3,
            "product_contracts_v2_shadow_enabled": stage3,
            "catalog_planner_v2_shadow_enabled": stage3,
            "solution_plan_v2_shadow_enabled": stage3,
        }
    )


def _legacy(state: SessionState):
    return state.model_dump(mode="json", exclude={"session_id", "dialogue_state_v2"})


def test_stage3_shadow_on_off_preserves_legacy_result_and_emits_telemetry(
    sample_products, tmp_path
) -> None:
    off_settings = _settings(tmp_path, stage3=False, name="stage3-off")
    on_settings = _settings(tmp_path, stage3=True, name="stage3-on")
    off = ChatOrchestrator(
        settings=off_settings,
        products=sample_products,
        llm_client=Stage3SemanticClient(off_settings),
    )
    on_client = Stage3SemanticClient(on_settings)
    on = ChatOrchestrator(
        settings=on_settings,
        products=sample_products,
        llm_client=on_client,
    )

    off_response = off.handle_chat("stage3-off", "Покажите труба")
    on_response = on.handle_chat("stage3-on", "Покажите труба")

    assert on_response.model_dump(exclude={"session_id"}) == off_response.model_dump(exclude={"session_id"})
    assert _legacy(on.sessions.snapshot("stage3-on")) == _legacy(off.sessions.snapshot("stage3-off"))
    assert on_client.semantic_interpreter_calls == 1
    trace = json.loads(on_settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["catalog_planner_v2_shadow"] is not None
    assert "contract_resolutions" in trace["catalog_planner_v2_shadow"]
    assert "readiness_assessments" in trace["catalog_planner_v2_shadow"]
    assert "v2_candidate_skus" in trace
    assert "v2_legacy_catalog_divergence" in trace


def test_stage3_failure_cannot_break_successful_legacy_turn(
    sample_products, tmp_path
) -> None:
    settings = _settings(tmp_path, stage3=True, name="stage3-failure")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage3SemanticClient(settings),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("catalog planner shadow failure")

    bot.dialogue_controller_v2.contract_registry.resolve_task = fail
    response = bot.handle_chat("stage3-failure", "Покажите труба")

    assert response.answer
    trace = json.loads(settings.diagnostic_trace_path.read_text().splitlines()[0])
    assert trace["dialogue_v2_shadow"]["status"] == "failed"
    assert "catalog planner shadow failure" in trace["dialogue_v2_shadow"]["error"]
    assert trace["error"] is None


def test_two_parallel_stage3_sessions_do_not_mix_catalogue_state(
    sample_products, tmp_path
) -> None:
    settings = _settings(tmp_path, stage3=True, name="stage3-parallel")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage3SemanticClient(settings),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(bot.handle_chat, session_id, "Покажите труба")
            for session_id in ("stage3-parallel-a", "stage3-parallel-b")
        ]
        assert all(item.result().answer for item in results)

    first = bot.sessions.snapshot("stage3-parallel-a").dialogue_state_v2
    second = bot.sessions.snapshot("stage3-parallel-b").dialogue_state_v2
    assert first is not None and second is not None
    assert set(first.applied_turn_ids).isdisjoint(second.applied_turn_ids)
    assert first.catalog_planning is not None
    assert second.catalog_planning is not None
    assert first.tasks[0].task_id != second.tasks[0].task_id
