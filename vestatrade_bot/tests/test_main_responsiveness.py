from __future__ import annotations

import asyncio
from threading import Event

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
