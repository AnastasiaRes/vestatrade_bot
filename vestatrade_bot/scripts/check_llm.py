from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.openrouter_client import OpenRouterClient


def main() -> int:
    settings = get_settings()
    client = OpenRouterClient(settings=settings)

    endpoint = client._endpoint()  # quick diagnostic script; keep endpoint formatting in one place
    print(f"LLM provider: {settings.llm_provider}")
    print(f"LLM enabled by config: {settings.llm_enabled}")
    print(f"Endpoint: {endpoint or '-'}")
    print(f"Model: {settings.llm_model}")
    print(f"Timeout: {settings.llm_timeout_seconds:g}s")
    print(f"Retries: {settings.llm_max_retries}")

    if not settings.llm_enabled:
        print("Result: LLM is not configured, bot will use fallback logic.")
        return 1

    result = client.complete(
        agent="LLMHealthcheck",
        messages=[
            {
                "role": "system",
                "content": "Ответь одним коротким русским предложением: LLM работает.",
            },
            {"role": "user", "content": "Проверка связи"},
        ],
        temperature=0.0,
        max_tokens=40,
    )
    if result.llm_used and result.content:
        print("Result: OK")
        print(f"Reply: {result.content.strip()}")
        return 0

    print(f"Result: FAILED ({result.fallback_reason or 'unknown error'})")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
