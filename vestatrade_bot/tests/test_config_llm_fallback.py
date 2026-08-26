from __future__ import annotations

import pytest

from app import openrouter_client as client_module
from app.config import get_settings
from app.openrouter_client import OpenRouterClient


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("missing_key", (None, "", "   "))
def test_openrouter_without_key_resolves_every_runtime_model_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
    missing_key: str | None,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    if missing_key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", missing_key)
    monkeypatch.setenv("OPENROUTER_MODEL", "hosted/base")
    monkeypatch.setenv("OPENROUTER_MODEL_STRONG", "hosted/strong")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local/base")
    monkeypatch.setenv("OLLAMA_MODEL_STRONG", "local/strong")
    monkeypatch.setenv("SEMANTIC_SHADOW_MODEL", "hosted/semantic")

    settings = get_settings()
    client = OpenRouterClient(settings)

    assert settings.llm_provider == "ollama"
    assert settings.llm_enabled is True
    assert settings.llm_model == "local/base"
    assert settings.llm_model_strong == "local/strong"
    assert settings.semantic_shadow_model is None
    assert client._endpoint() == "http://127.0.0.1:11434/v1/chat/completions"
    assert client._headers() == {"Content-Type": "application/json"}


def test_openrouter_with_key_keeps_openrouter_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "hosted/base")
    monkeypatch.setenv("OPENROUTER_MODEL_STRONG", "hosted/strong")
    monkeypatch.setenv("SEMANTIC_SHADOW_MODEL", "hosted/semantic")

    settings = get_settings()

    assert settings.llm_provider == "openrouter"
    assert settings.llm_enabled is True
    assert settings.llm_model == "hosted/base"
    assert settings.llm_model_strong == "hosted/strong"
    assert settings.semantic_shadow_model == "hosted/semantic"


def test_missing_provider_and_openrouter_key_use_ollama_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "local/default")

    settings = get_settings()

    assert settings.llm_provider == "ollama"
    assert settings.llm_enabled is True
    assert settings.llm_model == "local/default"


def test_explicit_disabled_provider_does_not_auto_enable_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "local/base")

    settings = get_settings()

    assert settings.llm_provider == "disabled"
    assert settings.llm_enabled is False


def test_automatic_fallback_sends_ollama_model_to_local_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local/runtime")
    captured: dict[str, object] = {}

    class FakeBudget:
        def can_call(self) -> bool:
            return True

        def reserve_call(self, **_kwargs: object) -> str:
            return "reservation"

        def release_reservation(self, _reservation_id: str) -> None:
            return None

        def record_call(self, **_kwargs: object) -> float:
            return 0.0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {"content": "Локальная модель работает."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            }

    class FakeHTTPClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeHTTPClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(
            self,
            endpoint: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> FakeResponse:
            captured.update(
                endpoint=endpoint,
                headers=headers,
                payload=json,
            )
            return FakeResponse()

    monkeypatch.setattr(client_module.httpx, "Client", FakeHTTPClient)
    settings = get_settings()
    result = OpenRouterClient(settings, FakeBudget()).complete(
        "FallbackTransportTest",
        [{"role": "user", "content": "Проверка"}],
    )

    assert result.llm_used is True
    assert captured["endpoint"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in captured["headers"]
    assert captured["payload"]["model"] == "local/runtime"
