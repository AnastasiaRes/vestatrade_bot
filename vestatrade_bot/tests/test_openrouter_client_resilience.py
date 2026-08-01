from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import Callable

import httpx
import pytest

import app.openrouter_client as client_module
from app.config import get_settings
from app.openrouter_client import OpenRouterClient


_ENDPOINT = "http://ollama.test/v1/chat/completions"


class _Budget:
    def can_call(self) -> bool:
        return True

    def record_call(self, **_kwargs) -> float:
        return 0.0


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


Outcome = httpx.Response | Exception | Callable[[], httpx.Response]


class _FakeHTTPClient:
    def __init__(self, factory: "_ClientFactory") -> None:
        self.factory = factory

    def __enter__(self) -> "_FakeHTTPClient":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, *_args, **_kwargs) -> httpx.Response:
        with self.factory.lock:
            self.factory.post_count += 1
            outcome = self.factory.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


class _ClientFactory:
    def __init__(self, outcomes: list[Outcome]) -> None:
        self.outcomes = deque(outcomes)
        self.timeouts: list[httpx.Timeout] = []
        self.post_count = 0
        self.lock = Lock()

    def __call__(self, **kwargs) -> _FakeHTTPClient:
        self.timeouts.append(kwargs["timeout"])
        return _FakeHTTPClient(self)


def _response(status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", _ENDPOINT),
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        },
    )


def _request_error(kind: str) -> httpx.RequestError:
    request = httpx.Request("POST", _ENDPOINT)
    if kind == "connect":
        return httpx.ConnectError("offline", request=request)
    return httpx.ReadTimeout("too slow", request=request)


def _client() -> OpenRouterClient:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://ollama.test",
            "ollama_model": "unit-model",
            "llm_timeout_seconds": 30.0,
            "llm_max_retries": 3,
            "llm_retry_delay_seconds": 0.0,
        }
    )
    return OpenRouterClient(settings=settings, budget_manager=_Budget())


def _complete(client: OpenRouterClient):
    return client.complete(agent="test", messages=[{"role": "user", "content": "hi"}])


@pytest.fixture(autouse=True)
def _reset_shared_circuits():
    with OpenRouterClient._circuit_lock:
        OpenRouterClient._circuits.clear()
    yield
    with OpenRouterClient._circuit_lock:
        OpenRouterClient._circuits.clear()


@pytest.mark.parametrize("kind", ["connect", "read"])
def test_ollama_request_error_is_retried_before_fallback(monkeypatch, kind: str) -> None:
    factory = _ClientFactory([_request_error(kind), _response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    recovered = _complete(client)

    assert recovered.llm_used is True
    assert recovered.content == "ok"
    assert factory.post_count == 2


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_ollama_transient_http_status_is_retried(
    monkeypatch, status_code: int
) -> None:
    factory = _ClientFactory([_response(status_code), _response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    recovered = _complete(client)

    assert recovered.llm_used is True
    assert recovered.content == "ok"
    assert factory.post_count == 2


def test_ollama_opens_circuit_only_after_all_retries_fail(monkeypatch) -> None:
    factory = _ClientFactory([_request_error("connect") for _ in range(4)])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    failed = _complete(client)
    skipped = _complete(client)

    assert failed.llm_used is False
    assert "ollama request failed" in (failed.fallback_reason or "")
    assert skipped.llm_used is False
    assert "circuit is open" in (skipped.fallback_reason or "")
    assert factory.post_count == 4


def test_non_retryable_4xx_does_not_retry_or_open_circuit(monkeypatch) -> None:
    factory = _ClientFactory([_response(400), _response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    rejected = _complete(client)
    next_call = _complete(client)

    assert rejected.llm_used is False
    assert next_call.llm_used is True
    assert next_call.content == "ok"
    assert factory.post_count == 2


def test_only_one_half_open_probe_is_allowed(monkeypatch) -> None:
    clock = _Clock()
    probe_entered = Event()
    release_probe = Event()

    def blocking_probe() -> httpx.Response:
        probe_entered.set()
        assert release_probe.wait(timeout=1)
        return _response()

    factory = _ClientFactory(
        [*[_request_error("connect") for _ in range(4)], blocking_probe, _response()]
    )
    monkeypatch.setattr(client_module, "monotonic", clock)
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    assert _complete(client).llm_used is False
    clock.advance(19.9)
    assert "circuit is open" in (_complete(client).fallback_reason or "")
    clock.advance(0.1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        probe = pool.submit(_complete, client)
        assert probe_entered.wait(timeout=1)
        concurrent = _complete(client)
        assert concurrent.llm_used is False
        assert "circuit is open" in (concurrent.fallback_reason or "")
        assert factory.post_count == 5
        release_probe.set()
        assert probe.result(timeout=1).llm_used is True

    assert _complete(client).llm_used is True
    assert factory.post_count == 6


def test_http_client_uses_split_fail_fast_timeouts(monkeypatch) -> None:
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)

    assert _complete(_client()).llm_used is True

    timeout = factory.timeouts[0]
    assert timeout.connect == 3.0
    assert timeout.read == 30.0
    assert timeout.write == 5.0
    assert timeout.pool == 2.0
