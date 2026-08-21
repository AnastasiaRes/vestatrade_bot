from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from threading import RLock
from typing import Any, Iterator, Protocol

from app.models import SessionState


class SessionStore(Protocol):
    def get(self, session_id: str) -> SessionState: ...
    def save(self, state: SessionState) -> None: ...
    def snapshot(self, session_id: str) -> SessionState: ...
    def restore(self, state: SessionState) -> None: ...
    def reset(self, session_id: str) -> SessionState: ...

    def turn_lock(self, session_id: str): ...


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

    @staticmethod
    def _deep_copy(state: SessionState) -> SessionState:
        """Return a Pydantic-version-independent transaction snapshot."""

        model_copy = getattr(state, "model_copy", None)
        if callable(model_copy):
            return model_copy(deep=True)
        return state.copy(deep=True)

    def snapshot(self, session_id: str) -> SessionState:
        """Capture a session before a turn mutates its live in-memory object."""

        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState(session_id=session_id)
            return self._deep_copy(self._sessions[session_id])

    def restore(self, state: SessionState) -> None:
        """Atomically roll a failed turn back to its pre-turn state."""

        with self._lock:
            self._sessions[state.session_id] = self._deep_copy(state)

    def reset(self, session_id: str) -> SessionState:
        with self._lock:
            self._sessions[session_id] = SessionState(session_id=session_id)
            return self._sessions[session_id]

    @contextmanager
    def turn_lock(self, _session_id: str) -> Iterator[None]:
        # ChatOrchestrator already owns the process-local per-session lock.
        yield


class RedisSessionStore:
    """Shared, TTL-bound dialogue state with per-session distributed locks."""

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = 86_400,
        lock_timeout_seconds: float = 30.0,
        client: Any | None = None,
        key_prefix: str = "vestatrade:session",
    ) -> None:
        if client is None:
            try:
                from redis import Redis
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise RuntimeError(
                    "SESSION_STORE_URL is configured but the redis package is not installed"
                ) from exc
            client = Redis.from_url(url, decode_responses=False)
        self._client = client
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._lock_timeout_seconds = max(1.0, float(lock_timeout_seconds))
        self._key_prefix = key_prefix.rstrip(":")

    @staticmethod
    def _digest(session_id: str) -> str:
        return sha256(session_id.encode("utf-8")).hexdigest()

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}:data:{self._digest(session_id)}"

    def _lock_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:lock:{self._digest(session_id)}"

    @staticmethod
    def _decode(payload: bytes | str) -> SessionState:
        if hasattr(SessionState, "model_validate_json"):
            return SessionState.model_validate_json(payload)
        return SessionState.parse_raw(payload)

    @staticmethod
    def _encode(state: SessionState) -> str:
        if hasattr(state, "model_dump_json"):
            return state.model_dump_json()
        return state.json()

    def get(self, session_id: str) -> SessionState:
        payload = self._client.get(self._key(session_id))
        if payload is None:
            return SessionState(session_id=session_id)
        state = self._decode(payload)
        if state.session_id != session_id:
            raise RuntimeError("session identity mismatch in shared store")
        return state

    def save(self, state: SessionState) -> None:
        state.sync_pending_question_state()
        state.sync_pending_into_project_context()
        self._client.setex(
            self._key(state.session_id),
            self._ttl_seconds,
            self._encode(state),
        )

    def snapshot(self, session_id: str) -> SessionState:
        state = self.get(session_id)
        return self._decode(self._encode(state))

    def restore(self, state: SessionState) -> None:
        self.save(self._decode(self._encode(state)))

    def reset(self, session_id: str) -> SessionState:
        state = SessionState(session_id=session_id)
        self.save(state)
        return state

    @contextmanager
    def turn_lock(self, session_id: str) -> Iterator[None]:
        lock = self._client.lock(
            self._lock_key(session_id),
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._lock_timeout_seconds,
        )
        with lock:
            yield


def build_session_store(
    url: str | None,
    *,
    ttl_seconds: int = 86_400,
    lock_timeout_seconds: float = 30.0,
) -> SessionStore:
    if not url:
        return InMemorySessionStore()
    return RedisSessionStore(
        url,
        ttl_seconds=ttl_seconds,
        lock_timeout_seconds=lock_timeout_seconds,
    )
