from __future__ import annotations

import asyncio
from threading import Event

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.models import ChatRequest, ChatResponse


def test_health_stays_available_while_chat_waits_in_worker(monkeypatch) -> None:
    chat_started = Event()
    release_chat = Event()

    def blocking_chat(session_id: str, _message: str) -> ChatResponse:
        chat_started.set()
        assert release_chat.wait(timeout=2)
        return ChatResponse(session_id=session_id, answer="готово")

    monkeypatch.setattr(main.orchestrator, "handle_chat", blocking_chat)
    monkeypatch.setattr(main.chat_logger, "log_turn", lambda *_args: None)

    async def scenario() -> None:
        chat_task = asyncio.create_task(
            main.chat(ChatRequest(session_id="slow-llm", message="проверка"))
        )
        try:
            assert await asyncio.to_thread(chat_started.wait, 1)
            health_payload = await asyncio.wait_for(main.health(), timeout=0.2)
            assert health_payload["status"] == "ok"
            assert health_payload["llm_request_timeout_seconds"] == 180
        finally:
            release_chat.set()
        response = await asyncio.wait_for(chat_task, timeout=1)
        assert response.answer == "готово"

    asyncio.run(scenario())


def test_ready_is_distinct_from_liveness(monkeypatch) -> None:
    monkeypatch.setattr(main.orchestrator.search_agent, "products", [])

    response = asyncio.run(main.ready())

    assert response.status_code == 503
    assert b'"status":"not_ready"' in response.body


def test_unhandled_error_has_stable_redacted_contract() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
    }

    response = asyncio.run(
        main.unhandled_error(Request(scope), RuntimeError("secret internal failure"))
    )

    assert response.status_code == 500
    assert b'"code":"INTERNAL_ERROR"' in response.body
    assert b'"trace_id"' in response.body
    assert b"secret internal failure" not in response.body


def test_reload_feed_is_disabled_without_admin_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "reload_feed_token", None)

    with pytest.raises(HTTPException) as captured:
        main.reload_feed(x_admin_token=None)

    assert captured.value.status_code == 503
    assert captured.value.detail == "feed reload is disabled"


def test_reload_feed_requires_matching_admin_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "reload_feed_token", "server-secret")

    with pytest.raises(HTTPException) as captured:
        main.reload_feed(x_admin_token="wrong")

    assert captured.value.status_code == 403


def test_reload_feed_error_is_redacted(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "reload_feed_token", "server-secret")

    def fail_reload(*, refresh: bool):
        assert refresh is True
        raise RuntimeError("credential-bearing internal detail")

    monkeypatch.setattr(main.orchestrator, "reload_products", fail_reload)

    with pytest.raises(HTTPException) as captured:
        main.reload_feed(x_admin_token="server-secret")

    assert captured.value.status_code == 503
    assert "credential-bearing" not in str(captured.value.detail)
    assert captured.value.detail["code"] == "FEED_RELOAD_FAILED"
    assert captured.value.detail["trace_id"]


def test_qa_mode_is_rejected_without_server_authorization(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "dialogue_v2_qa_controls_enabled", False)
    monkeypatch.setattr(main.settings, "dialogue_v2_qa_control_token", None)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            main.chat(
                ChatRequest(
                    session_id="qa-shadow",
                    message="Покажите насос",
                    qa_mode="shadow",
                ),
                x_dialogue_qa_token="wrong",
            )
        )

    assert captured.value.status_code == 403


def test_authorized_qa_mode_is_forwarded_to_the_same_chat_path(monkeypatch) -> None:
    calls = []

    def qa_chat(session_id, message, client_turn_id, qa_mode):
        calls.append((session_id, message, client_turn_id, qa_mode.value))
        return ChatResponse(session_id=session_id, answer="QA answer")

    monkeypatch.setattr(main.settings, "dialogue_v2_qa_controls_enabled", True)
    monkeypatch.setattr(main.settings, "dialogue_v2_qa_control_token", "qa-secret")
    monkeypatch.setattr(main.orchestrator, "handle_chat", qa_chat)
    monkeypatch.setattr(main.chat_logger, "log_turn", lambda *_args: None)

    response = asyncio.run(
        main.chat(
            ChatRequest(
                session_id="qa-shadow",
                message="Покажите насос",
                qa_mode="shadow",
            ),
            x_dialogue_qa_token="qa-secret",
        )
    )

    assert response.answer == "QA answer"
    assert calls == [("qa-shadow", "Покажите насос", None, "shadow")]
