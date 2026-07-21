from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Event, Lock

from app.agents import orchestrator as orchestrator_module
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import ChatResponse, IntentResult, SessionState


class _RequestScopedComposer:
    """Models the mutable draft/history fields that leaked in production."""

    rendezvous = Barrier(2, timeout=1)

    def __init__(self, _llm_client) -> None:
        self.current_context = ""
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_fallback_reason = None
        self.last_draft: str | None = None

    def reset_usage(self) -> None:
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_fallback_reason = None
        self.last_draft = None

    def set_history(self, _history) -> None:
        return None

    def set_state(self, *_args) -> None:
        return None

    def compose_identity_or_service(self, _message: str) -> None:
        return None

    def compose_small_talk(self, message: str) -> str:
        self.current_context = message
        try:
            self.rendezvous.wait()
        except BrokenBarrierError as exc:  # a process-wide lock would land here
            raise AssertionError("independent sessions were serialized") from exc
        self.last_draft = self.current_context
        return self.current_context


def test_parallel_sessions_have_isolated_request_agents(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "ResponseComposerAgent",
        _RequestScopedComposer,
    )
    bot = ChatOrchestrator(products=[])
    bot.intent_router.route = lambda _message, _session: IntentResult(  # type: ignore[method-assign]
        intent_type="small_talk",
        category="other",
        confidence=1.0,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(bot.handle_chat, "customer-a", "context-a")
        second = executor.submit(bot.handle_chat, "customer-b", "context-b")
        first_response = first.result(timeout=2)
        second_response = second.result(timeout=2)

    assert first_response.answer == "context-a"
    assert second_response.answer == "context-b"


def test_parallel_turns_of_same_session_are_serialized(monkeypatch) -> None:
    bot = ChatOrchestrator(products=[])
    first_entered = Event()
    release_first = Event()
    state_lock = Lock()
    active = 0
    max_active = 0

    def fake_handle(session_id: str, message: str) -> ChatResponse:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        if message == "first":
            first_entered.set()
            assert release_first.wait(timeout=1)
        with state_lock:
            active -= 1
        return ChatResponse(session_id=session_id, answer=message)

    monkeypatch.setattr(bot, "_handle_chat", fake_handle)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(bot.handle_chat, "same-session", "first")
        assert first_entered.wait(timeout=1)
        second = executor.submit(bot.handle_chat, "same-session", "second")
        release_first.set()
        assert first.result(timeout=2).answer == "first"
        assert second.result(timeout=2).answer == "second"

    assert max_active == 1


def test_intent_cache_does_not_retain_request_mutations() -> None:
    router = IntentRouterAgent()
    first_session = SessionState(session_id="first")
    second_session = SessionState(session_id="second")

    first = router.route("нужен насос", first_session)
    first.slots["session_only"] = "customer-a"

    second = router.route("нужен насос", second_session)

    assert "session_only" not in second.slots
