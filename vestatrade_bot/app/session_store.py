from __future__ import annotations

from threading import RLock

from app.models import SessionState


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = RLock()

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState(session_id=session_id)
            return self._sessions[session_id]

    def save(self, state: SessionState) -> None:
        with self._lock:
            # Product-specific branches may still write the legacy pending
            # fields directly.  Normalise them at the persistence boundary so
            # every subsequent turn can rely on ``pending_question_state``.
            state.sync_pending_question_state()
            state.sync_pending_into_project_context()
            self._sessions[state.session_id] = state

    def reset(self, session_id: str) -> SessionState:
        with self._lock:
            self._sessions[session_id] = SessionState(session_id=session_id)
            return self._sessions[session_id]
