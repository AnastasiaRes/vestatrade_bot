from __future__ import annotations

from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from app.models import ChatRequest, LastSearchOutcome, SessionState
from app.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    build_session_store,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes | str] = {}
        self.setex_calls: list[tuple[str, int, bytes | str]] = []
        self.lock_calls: list[tuple[str, float, float]] = []

    def get(self, key: str):
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: bytes | str) -> None:
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))

    @contextmanager
    def lock(self, key: str, *, timeout: float, blocking_timeout: float):
        self.lock_calls.append((key, timeout, blocking_timeout))
        yield


def test_redis_store_round_trip_is_shared_ttl_bound_and_uses_hashed_keys() -> None:
    client = _FakeRedis()
    first = RedisSessionStore("redis://unused", client=client, ttl_seconds=600)
    second = RedisSessionStore("redis://unused", client=client, ttl_seconds=600)
    session_id = "customer-visible-session"

    state = first.get(session_id)
    state.history.append({"role": "user", "content": "Нужен кран"})
    state.last_search_outcome = LastSearchOutcome(
        category="fittings",
        constraints={"diameter_mm": 20, "angle_deg": 45},
        answer_text="Точного совпадения нет.",
    )
    first.save(state)

    restored = second.get(session_id)
    assert restored.session_id == session_id
    assert restored.history[-1]["content"] == "Нужен кран"
    assert restored.last_search_outcome is not None
    assert restored.last_search_outcome.category == "fittings"
    assert restored.last_search_outcome.constraints["angle_deg"] == 45
    key, ttl, _payload = client.setex_calls[-1]
    assert ttl == 600
    assert session_id not in key
    assert key.startswith("vestatrade:session:data:")


def test_redis_turn_lock_is_scoped_to_hashed_session_key() -> None:
    client = _FakeRedis()
    store = RedisSessionStore(
        "redis://unused",
        client=client,
        lock_timeout_seconds=12.5,
    )

    with store.turn_lock("private-user-id"):
        pass

    key, timeout, blocking_timeout = client.lock_calls[-1]
    assert "private-user-id" not in key
    assert key.startswith("vestatrade:session:lock:")
    assert timeout == 12.5
    assert blocking_timeout == 12.5


def test_session_store_factory_keeps_local_default() -> None:
    assert isinstance(build_session_store(None), InMemorySessionStore)


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "", "message": "ok"},
        {"session_id": "contains whitespace", "message": "ok"},
        {"session_id": "x" * 129, "message": "ok"},
        {"session_id": "valid-id", "message": ""},
        {"session_id": "valid-id", "message": "x" * 8_001},
    ],
)
def test_chat_request_rejects_unbounded_or_unsafe_input(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(**payload)


def test_redis_restore_rolls_back_a_failed_turn_snapshot() -> None:
    client = _FakeRedis()
    store = RedisSessionStore("redis://unused", client=client)
    state = SessionState(session_id="rollback")
    state.history.append({"role": "user", "content": "before"})
    store.save(state)
    snapshot = store.snapshot("rollback")

    state.history.append({"role": "assistant", "content": "partial"})
    store.save(state)
    store.restore(snapshot)

    restored = store.get("rollback")
    assert [item["content"] for item in restored.history] == ["before"]
