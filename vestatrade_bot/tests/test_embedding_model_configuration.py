from __future__ import annotations

from app.config import get_settings


def test_openrouter_uses_bge_m3_unless_deployment_overrides_it(monkeypatch) -> None:
    """The deployment may pin another model, but the safe default is BGE-M3."""

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-key")
    monkeypatch.delenv("OPENROUTER_EMBEDDING_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().embedding_model == "baai/bge-m3"
    finally:
        # Settings are cached process-wide; do not leak this temporary test
        # configuration into a later test.
        get_settings.cache_clear()
