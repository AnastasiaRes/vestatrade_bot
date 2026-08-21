from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.slot_filling import SlotFillingAgent
from app.models import SessionState


def test_accusative_water_is_understood_before_thread_clarification() -> None:
    message = "кран на воду полдюйма"
    intent = IntentRouterAgent().route(message, session=None)

    assert intent.category == "valves"
    assert intent.slots["size_inch"] == "1/2"
    assert intent.slots["application"] == "вода"

    filled = SlotFillingAgent().fill(
        message,
        intent,
        SessionState(session_id="water-accusative"),
    )

    assert filled.slots["application"] == "вода"
    assert filled.needs_clarification is True
    assert "для чего нужен кран" not in (filled.question or "").lower()
    assert "резьб" in (filled.question or "").lower()


@pytest.mark.parametrize(
    "message",
    [
        "Найди артикул 15100Z",
        "Покажи артикул: 15100Z",
        "Что есть по арт. 15100Z?",
    ],
)
def test_short_mixed_sku_is_exact_only_after_explicit_marker(message: str) -> None:
    result = IntentRouterAgent().route(message, session=None)

    assert result.intent_type == "exact_sku"
    assert result.slots["sku"] == "15100Z"
    assert result.raw["llm_requested"] is False


def test_short_mixed_sku_does_not_weaken_general_sku_detection() -> None:
    router = IntentRouterAgent()

    assert router._is_valid_sku_candidate("15100Z") is False
    unmarked = router._rule_based("Покажи 15100Z", session=None)
    assert unmarked.intent_type != "exact_sku"
    assert "sku" not in unmarked.slots


@pytest.mark.parametrize("message", ["Подскажи артикул", "артикул модели", "артикул 1/2"])
def test_explicit_marker_without_sku_like_token_is_not_exact(message: str) -> None:
    result = IntentRouterAgent()._rule_based(message, session=None)

    assert result.intent_type != "exact_sku"
    assert "sku" not in result.slots
