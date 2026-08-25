from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.models import SessionState


class Stage4SemanticClient:
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
    ):
        if agent not in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }:
            return fallback, False
        self.semantic_calls += 1
        payload = json.loads(messages[-1]["content"])
        message = payload["current_message"].casefold()
        if "подтверж" in message:
            return {
                "schema_version": "1.1",
                "language": "ru",
                "operation": "continue",
                "acts": [],
                "products": [],
                "constraints": [],
                "references": [],
                "ambiguities": [],
                "workflow_controls": [{"kind": "confirm", "evidence": "Подтверждаю"}],
                "answers_pending_question": True,
                "confidence": 0.99,
            }, True
        handoff = "менеджер" in message
        invoice = "счёт" in message or "счет" in message
        acts = []
        if invoice:
            acts.append("request_invoice")
        if handoff:
            acts.append("handoff")
        if not acts:
            acts.append("check_delivery")
        return {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "new",
            "acts": acts,
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.97,
        }, True


def _settings(tmp_path, *, stage4: bool, name: str):
    return get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / f"{name}.jsonl",
            "handoff_log_path": tmp_path / f"{name}-handoff.jsonl",
            "semantic_shadow_enabled": False,
            "dialogue_state_v2_shadow_enabled": stage4,
            "seller_policy_v2_shadow_enabled": stage4,
            "commerce_workflows_v2_shadow_enabled": stage4,
            "handoff_workflow_v2_shadow_enabled": stage4,
            "commerce_outbox_v2_shadow_enabled": stage4,
            "commerce_external_execution_enabled": False,
        }
    )


def _legacy_state(state: SessionState) -> dict[str, Any]:
    return state.model_dump(
        mode="json",
        exclude={"session_id", "dialogue_state_v2"},
    )


def test_stage4_shadow_on_off_preserves_legacy_result_and_handoff_file(
    sample_products,
    tmp_path,
) -> None:
    off_settings = _settings(tmp_path, stage4=False, name="stage4-off")
    on_settings = _settings(tmp_path, stage4=True, name="stage4-on")
    off = ChatOrchestrator(
        settings=off_settings,
        products=sample_products,
        llm_client=Stage4SemanticClient(off_settings),
    )
    on_client = Stage4SemanticClient(on_settings)
    on = ChatOrchestrator(
        settings=on_settings,
        products=sample_products,
        llm_client=on_client,
    )

    message = "Передайте менеджеру запрос по насосу"
    off_response = off.handle_chat("stage4-off", message)
    on_response = on.handle_chat("stage4-on", message)

    assert on_client.semantic_calls == 2
    assert on_response.model_dump(exclude={"session_id"}) == (
        off_response.model_dump(exclude={"session_id"})
    )
    assert _legacy_state(on.sessions.snapshot("stage4-on")) == _legacy_state(
        off.sessions.snapshot("stage4-off")
    )
    assert not off_settings.handoff_log_path.exists()
    assert not on_settings.handoff_log_path.exists()
    v2 = on.sessions.snapshot("stage4-on").dialogue_state_v2
    assert v2 is not None
    assert v2.commerce_planning is not None
    assert v2.commerce_workflows

    trace = json.loads(
        on_settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert trace["current_message"] is None
    assert trace["commerce_workflows_v2_shadow"] is not None
    assert trace["dialogue_v2_shadow"]["commerce_planning"]["status"] == "planned"


def test_stage4_trace_and_state_do_not_contain_customer_contact(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, stage4=True, name="stage4-pii")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage4SemanticClient(settings),
    )
    email = "synthetic-buyer@example.test"

    response = bot.handle_chat(
        "stage4-pii",
        f"Передайте менеджеру, мой email {email}",
    )

    assert response.answer
    state = bot.sessions.snapshot("stage4-pii")
    assert state.dialogue_state_v2 is not None
    assert email not in state.dialogue_state_v2.model_dump_json()
    trace_text = settings.diagnostic_trace_path.read_text(encoding="utf-8")
    assert email not in trace_text
    assert "legacy_session_customer_contact" in trace_text


def test_stage4_failure_is_fail_open_for_legacy_response(
    sample_products, tmp_path
) -> None:
    settings = _settings(tmp_path, stage4=True, name="stage4-failure")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage4SemanticClient(settings),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("commerce shadow failed")

    bot.dialogue_controller_v2.run = fail
    response = bot.handle_chat("stage4-failure", "Счёт на насос")

    assert response.answer
    assert bot.sessions.snapshot("stage4-failure").dialogue_state_v2 is None
    trace = json.loads(
        settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert trace["dialogue_v2_shadow"]["status"] == "failed"
    assert "commerce shadow failed" in trace["dialogue_v2_shadow"]["error"]


def test_parallel_sessions_do_not_mix_stage4_workflows(
    sample_products, tmp_path
) -> None:
    settings = _settings(tmp_path, stage4=True, name="stage4-parallel")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage4SemanticClient(settings),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        invoice = pool.submit(
            bot.handle_chat,
            "invoice-session",
            "Нужен счёт на насос",
        )
        handoff = pool.submit(
            bot.handle_chat,
            "handoff-session",
            "Передайте менеджеру",
        )
        assert invoice.result().answer
        assert handoff.result().answer

    invoice_state = bot.sessions.snapshot("invoice-session").dialogue_state_v2
    handoff_state = bot.sessions.snapshot("handoff-session").dialogue_state_v2
    assert invoice_state is not None and handoff_state is not None
    assert {item.workflow_kind.value for item in invoice_state.commerce_workflows} == {
        "request_invoice"
    }
    assert {item.workflow_kind.value for item in handoff_state.commerce_workflows} == {
        "handoff"
    }
    assert set(invoice_state.applied_turn_ids).isdisjoint(
        handoff_state.applied_turn_ids
    )


def test_parallel_consent_turns_prepare_only_one_v2_command(
    sample_products,
    tmp_path,
) -> None:
    settings = _settings(tmp_path, stage4=True, name="stage4-consent-race")
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=Stage4SemanticClient(settings),
    )
    bot.sessions.save(
        SessionState(
            session_id="consent-race",
            contact="synthetic@example.test",
            contact_turn=0,
        )
    )
    assert bot.handle_chat(
        "consent-race",
        "Передайте менеджеру запрос по насосу",
    ).answer

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = tuple(
            pool.submit(
                bot.handle_chat,
                "consent-race",
                "Подтверждаю",
            )
            for _ in range(2)
        )
        assert all(item.result().answer for item in responses)

    state = bot.sessions.snapshot("consent-race").dialogue_state_v2
    assert state is not None
    assert len(state.commerce_outbox) == 1
    assert len({item.command.idempotency_key for item in state.commerce_outbox}) == 1
