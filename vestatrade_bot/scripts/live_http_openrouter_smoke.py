"""Exercise the real ASGI /chat boundary with OpenRouter and local cache only.

ASGITransport intentionally does not run the application's startup lifespan,
because production startup refreshes the remote feed.  The test explicitly
loads ``refresh=False`` first, so the Vestatrade site is never contacted.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import main as main_module  # noqa: E402


async def _run() -> None:
    count, source = main_module.orchestrator.reload_products(refresh=False)
    assert count > 1000
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://local-test",
        timeout=240.0,
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        health_data = health.json()
        assert health_data["products_loaded"] == count
        assert health_data["products_loaded_from"] == source
        assert health_data["llm_provider"] == "openrouter"
        assert health_data["llm_configured"] is True

        page = await client.get("/")
        assert page.status_code == 200
        assert "app.js" in page.text
        browser_script = await client.get("/app.js")
        assert browser_script.status_code == 200
        assert "/chat" in browser_script.text

        chat = await client.post(
            "/chat",
            json={
                "session_id": "live-http-openrouter",
                "message": (
                    "Нужна PPR труба 20 мм для горячей воды, "
                    "максимум 70 градусов и 10 бар"
                ),
            },
        )
        assert chat.status_code == 200, chat.text
        payload = chat.json()
        slots = payload["debug"]["slots"]
        assert slots["diameter_mm"] == 20
        assert slots["operating_temperature_c"] == 70
        assert slots["operating_pressure_bar"] == 10
        assert "max_price" not in slots
        assert payload["debug"]["any_llm_used"] is True

    print(
        "LIVE HTTP OPENROUTER PASSED: "
        f"products={count}; source={source}; status={chat.status_code}; "
        f"llm_used={payload['debug']['any_llm_used']}"
    )
    print("BOT:", " ".join(payload["answer"].split()))


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
