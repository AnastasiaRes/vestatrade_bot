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
    def __init__(self) -> None:
        self.record_count = 0

    def can_call(self) -> bool:
        return True

    def record_call(self, **_kwargs) -> float:
        self.record_count += 1
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
            self.factory.payloads.append(_kwargs.get("json") or {})
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
        self.payloads: list[dict] = []
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


def _embedding_response(status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", _ENDPOINT),
        json={"data": [{"embedding": [0.25, 0.75]}]},
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
            "llm_request_timeout_seconds": 180.0,
            "llm_max_retries": 3,
            "llm_retry_delay_seconds": 0.0,
        }
    )
    return OpenRouterClient(settings=settings, budget_manager=_Budget())


def _openrouter_client() -> OpenRouterClient:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "openrouter_model": "qwen/test-model",
            "llm_timeout_seconds": 30.0,
            "llm_request_timeout_seconds": 180.0,
            "llm_max_retries": 0,
            "llm_retry_delay_seconds": 0.0,
        }
    )
    return OpenRouterClient(settings=settings, budget_manager=_Budget())


def _complete(client: OpenRouterClient):
    return client.complete(agent="test", messages=[{"role": "user", "content": "hi"}])


def test_model_transport_redacts_contacts_but_preserves_numeric_articles(
    monkeypatch,
) -> None:
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _openrouter_client()

    result = client.complete(
        agent="test",
        messages=[
            {
                "role": "user",
                "content": (
                    "Мой email buyer@example.com, телефон +7 999 111-22-33. "
                    "Артикул 1234567890."
                ),
            }
        ],
    )

    assert result.llm_used is True
    sent = factory.payloads[0]["messages"][0]["content"]
    assert "buyer@example.com" not in sent
    assert "+7 999 111-22-33" not in sent
    assert "[email redacted]" in sent
    assert "[phone redacted]" in sent
    assert "1234567890" in sent


@pytest.mark.parametrize(
    "email",
    [
        "иван@пример.рф",
        "buyer@xn--e1afmkfd.xn--p1ai",
    ],
)
def test_model_transport_redacts_idn_email_as_one_value(
    monkeypatch,
    email: str,
) -> None:
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _openrouter_client()

    result = client.complete(
        agent="test",
        messages=[{"role": "user", "content": f"Мой email {email}."}],
    )

    assert result.llm_used is True
    sent = factory.payloads[0]["messages"][0]["content"]
    assert email not in sent
    assert "[email redacted]" in sent
    assert "--p1ai" not in sent


@pytest.mark.parametrize(
    "phone",
    [
        "Мой телефон 123-45-67",
        "Мой телефон +7 999/123-45-67",
    ],
)
def test_model_transport_redacts_explicit_local_phone(
    monkeypatch,
    phone: str,
) -> None:
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _openrouter_client()

    result = client.complete(
        agent="test",
        messages=[{"role": "user", "content": phone}],
    )

    assert result.llm_used is True
    sent = factory.payloads[0]["messages"][0]["content"]
    assert "123-45-67" not in sent
    assert "[phone redacted]" in sent


def test_model_transport_preserves_a_list_of_numeric_articles(monkeypatch) -> None:
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _openrouter_client()

    result = client.complete(
        agent="test",
        messages=[
            {
                "role": "user",
                "content": "Артикулы 1234567890 и 0987654321.",
            }
        ],
    )

    assert result.llm_used is True
    sent = factory.payloads[0]["messages"][0]["content"]
    assert "1234567890" in sent
    assert "0987654321" in sent
    assert "[phone redacted]" not in sent


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


def test_embedding_retries_a_transient_provider_failure(monkeypatch) -> None:
    factory = _ClientFactory([_embedding_response(429), _embedding_response()])
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    settings = _openrouter_client().settings.model_copy(
        update={"llm_max_retries": 1, "llm_retry_delay_seconds": 0.0}
    )

    vectors = OpenRouterClient(settings=settings).embed(["passport fragment"])

    assert vectors == [[0.25, 0.75]]
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


def test_ollama_generation_receives_whole_turn_budget(monkeypatch) -> None:
    clock = _Clock()
    factory = _ClientFactory([_response()])
    monkeypatch.setattr(client_module, "monotonic", clock)
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    with client.request_budget():
        result = _complete(client)

    assert result.llm_used is True
    assert factory.post_count == 1
    assert factory.timeouts[0].read == pytest.approx(180.0)
    assert factory.timeouts[0].connect == 3.0


def test_multiple_llm_agents_share_one_turn_deadline(monkeypatch) -> None:
    clock = _Clock()

    def first_agent_response() -> httpx.Response:
        clock.advance(120.0)
        return _response()

    factory = _ClientFactory([first_agent_response, _response()])
    monkeypatch.setattr(client_module, "monotonic", clock)
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    with client.request_budget():
        first = _complete(client)
        second = _complete(client)

    assert first.llm_used is True
    assert second.llm_used is True
    assert [timeout.read for timeout in factory.timeouts] == pytest.approx(
        [180.0, 60.0]
    )


def test_response_after_turn_deadline_falls_back_deterministically(monkeypatch) -> None:
    clock = _Clock()

    def late_response() -> httpx.Response:
        clock.advance(181.0)
        return _response()

    factory = _ClientFactory([late_response])
    monkeypatch.setattr(client_module, "monotonic", clock)
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    with client.request_budget():
        result = _complete(client)

    assert result.llm_used is False
    assert "180s" in (result.fallback_reason or "")
    assert "budget" in (result.fallback_reason or "")
    assert factory.post_count == 1
    # The HTTP request succeeded and may already have been billed even though
    # its content missed the customer-facing deadline.
    assert client.budget.record_count == 1


def test_exhausted_turn_budget_skips_downstream_llm_agent(monkeypatch) -> None:
    clock = _Clock()

    def first_agent_response() -> httpx.Response:
        clock.advance(179.0)
        return _response()

    factory = _ClientFactory([first_agent_response])
    monkeypatch.setattr(client_module, "monotonic", clock)
    monkeypatch.setattr(client_module.httpx, "Client", factory)
    client = _client()

    with client.request_budget():
        first = _complete(client)
        clock.advance(1.1)
        downstream = _complete(client)

    assert first.llm_used is True
    assert downstream.llm_used is False
    assert "budget" in (downstream.fallback_reason or "")
    assert factory.post_count == 1


def test_openrouter_json_agents_request_structured_output(monkeypatch) -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", OpenRouterClient.openrouter_endpoint),
        json={
            "choices": [{"message": {"content": '{"kind":"other"}'}}],
            "usage": {},
        },
    )
    factory = _ClientFactory([response])
    monkeypatch.setattr(client_module.httpx, "Client", factory)

    parsed, used = _openrouter_client().complete_json(
        "TurnClassifierAgent",
        [{"role": "user", "content": "test"}],
        {"kind": "other"},
    )

    assert used is True
    assert parsed == {"kind": "other"}
    assert factory.payloads[0]["response_format"] == {"type": "json_object"}
    assert factory.payloads[0]["provider"] == {"require_parameters": True}


def test_ollama_json_agents_request_structured_output(monkeypatch) -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", _ENDPOINT),
        json={
            "choices": [{"message": {"content": '{"kind":"other"}'}}],
            "usage": {},
        },
    )
    factory = _ClientFactory([response])
    monkeypatch.setattr(client_module.httpx, "Client", factory)

    parsed, used = _client().complete_json(
        "TurnClassifierAgent",
        [{"role": "user", "content": "test"}],
        {"kind": "other"},
    )

    assert used is True
    assert parsed == {"kind": "other"}
    assert factory.payloads[0]["response_format"] == {"type": "json_object"}
    assert "provider" not in factory.payloads[0]


def test_plain_openrouter_generation_does_not_force_json_mode(monkeypatch) -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("POST", OpenRouterClient.openrouter_endpoint),
        json={"choices": [{"message": {"content": "plain text"}}], "usage": {}},
    )
    factory = _ClientFactory([response])
    monkeypatch.setattr(client_module.httpx, "Client", factory)

    result = _openrouter_client().complete(
        "ResponseComposerAgent",
        [{"role": "user", "content": "test"}],
    )

    assert result.content == "plain text"
    assert "response_format" not in factory.payloads[0]
