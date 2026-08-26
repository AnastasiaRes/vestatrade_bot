from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from app.agents.diagnostic_feed_search import DiagnosticFeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.diagnostic_telemetry import (
    activate_turn_trace,
    build_turn_trace,
    catalogue_manifest,
    finish_turn_trace,
)
from app.models import ChatResponse, SearchQuery, SessionState
from app.openrouter_client import OpenRouterClient


class ShadowAwareClient:
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
        return {
            "schema_version": "1.0",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "насос",
                    "canonical_type": "насос",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "насос",
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "answers_pending_question": False,
            "confidence": 0.9,
        }, True


def _settings(tmp_path, *, shadow: bool):
    return get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / ("shadow.jsonl" if shadow else "legacy.jsonl"),
            "semantic_shadow_enabled": shadow,
            "semantic_shadow_model": "test/semantic-model",
        }
    )


def test_shadow_result_does_not_change_response_or_session(sample_products, tmp_path) -> None:
    baseline_settings = _settings(tmp_path, shadow=False)
    shadow_settings = _settings(tmp_path, shadow=True)
    baseline_client = ShadowAwareClient(baseline_settings)
    shadow_client = ShadowAwareClient(shadow_settings)
    baseline = ChatOrchestrator(
        settings=baseline_settings,
        products=sample_products,
        llm_client=baseline_client,
    )
    shadow = ChatOrchestrator(
        settings=shadow_settings,
        products=sample_products,
        llm_client=shadow_client,
    )

    baseline_response = baseline.handle_chat("baseline", "Покажите насос")
    shadow_response = shadow.handle_chat("shadow", "Покажите насос")

    assert shadow_client.semantic_calls == 2
    assert shadow_response.answer == baseline_response.answer
    assert shadow_response.products == baseline_response.products
    baseline_state = baseline.sessions.snapshot("baseline").model_dump(
        exclude={"session_id"}
    )
    shadow_state = shadow.sessions.snapshot("shadow").model_dump(
        exclude={"session_id"}
    )
    assert shadow_state == baseline_state


def test_trace_contains_full_turn_layers_and_redacts_pii(sample_products, tmp_path) -> None:
    settings = _settings(tmp_path, shadow=True)
    client = ShadowAwareClient(settings)
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=client,
    )

    bot.handle_chat(
        "trace-session",
        "Покажите насос, мой телефон +7 999 123-45-67",
    )

    lines = settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["schema_version"] == "1.0"
    assert len(trace["session_fingerprint"]) == 64
    assert trace["session_fingerprint"].isalpha()
    assert trace["state_before"]["category"] is None
    assert trace["state_after"]["category"] == trace["legacy_decision"]["category"]
    assert trace["turn_understanding"]["status"] == "accepted"
    assert trace["legacy_decision"]["turn_actions"] is not None
    assert isinstance(trace["search_plan_events"], list)
    assert trace["selected_next_action"]["source"].startswith("legacy_")
    assert trace["selected_next_action"]["primary"]
    assert "llm_output_accepted" in trace["legacy_decision"]["llm_acceptance"]
    assert trace["legacy_answer_plan"]["final_answer_source"]
    assert trace["runtime"]["catalog"]["product_count"] == len(sample_products)
    assert trace["runtime"]["catalog"]["sha256"]
    assert trace["runtime"]["semantic_prompt_hash"]
    serialized = json.dumps(trace, ensure_ascii=False)
    assert "+7 999 123-45-67" not in serialized
    assert "[phone redacted]" in serialized


def test_shadow_validation_failure_is_observable_but_fail_open(sample_products, tmp_path) -> None:
    settings = _settings(tmp_path, shadow=True)
    client = ShadowAwareClient(settings)
    original_complete = client.complete_json

    def invalid_complete(agent, messages, fallback, model=None):
        payload, used = original_complete(agent, messages, fallback, model)
        if agent in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }:
            payload["reply"] = "Это поле запрещено"
        return payload, used

    client.complete_json = invalid_complete
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=client,
    )

    response = bot.handle_chat("invalid-shadow", "Покажите насос")

    assert response.answer
    trace = json.loads(
        settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert trace["turn_understanding"]["status"] == "rejected"
    assert trace["turn_understanding"]["output_accepted"] is False
    assert trace["error"] is None


def test_transport_and_search_events_are_recorded_at_shared_boundaries(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, shadow=False).model_copy(
        update={
            "llm_provider": "disabled",
            "openrouter_api_key": None,
            "usage_budget_path": tmp_path / "usage.json",
        }
    )
    state = SessionState(session_id="boundary-events")
    trace = build_turn_trace(
        settings,
        session_id=state.session_id,
        message="Покажите насос",
        state_before=state,
        catalog=catalogue_manifest(sample_products, "test"),
    )
    client = OpenRouterClient(settings)
    search = DiagnosticFeedSearchAgent(sample_products)

    with activate_turn_trace(trace):
        llm_result = client.complete(
            "diagnostic-test",
            [{"role": "user", "content": "Покажите насос"}],
        )
        products = search.search(
            SearchQuery(
                original_text="Покажите насос",
                category="pumps",
            )
        )
        response = ChatResponse(
            session_id=state.session_id,
            answer="Диагностический ответ",
            debug={"intent": "product_search", "category": "pumps"},
        )
        finish_turn_trace(
            trace,
            response=response,
            state_after=state,
        )

    assert llm_result.llm_used is False
    payload = json.loads(
        settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert payload["llm_calls"][0]["agent"] == "diagnostic-test"
    assert payload["llm_calls"][0]["transport_succeeded"] is False
    assert payload["llm_calls"][0]["fallback_reason"]
    assert payload["search_plan_events"][0]["operation"] == "search"
    assert payload["search_plan_events"][0]["query"]["category"] == "pumps"
    assert payload["search_plan_events"][0]["result_skus"] == [
        product.sku for product in products
    ]
