from __future__ import annotations

from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.models import Product
from app.openrouter_client import LLMResult


class _AdversarialEngineeringLLM:
    """Return one grounded-looking but contextually wrong engineering payload."""

    last_json_output_accepted = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, agent, messages, fallback):
        if agent.startswith("EngineeringInterpreterAgent"):
            self.last_json_output_accepted = True
            return self.payload, True
        self.last_json_output_accepted = False
        return fallback, False

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=None, llm_used=False, fallback_reason="not needed")


class _AdversarialIntentLLM(_AdversarialEngineeringLLM):
    def complete_json(self, agent, messages, fallback):
        if agent == "IntentRouterAgent":
            self.last_json_output_accepted = True
            return self.payload, True
        self.last_json_output_accepted = False
        return fallback, False


def _live_settings():
    return get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )


def _pump(sku: str, *, price: float, brand: str = "TEST") -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {brand} 25/6-130",
        category_path="Насосы циркуляционные",
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "максимальный напор, м": "6",
            "монтажная длина, мм": "130",
            "диаметр условного прохода, мм": "25",
        },
    )


def test_llm_warm_floor_subarea_cannot_overwrite_house_area() -> None:
    message = "бойлер 150 л, тёплый пол 60 м², 6 контуров"
    llm = _AdversarialEngineeringLLM(
        {
            "handled": True,
            "continuation": True,
            "dialog_act": "continue",
            "intent_type": "attribute_request",
            "category": "pipes",
            "project_scope": "warm_floor",
            "slots": {"area_m2": 60, "warm_floor_area_m2": 60},
            "slot_evidence": {
                "area_m2": "тёплый пол 60 м²",
                "warm_floor_area_m2": "тёплый пол 60 м²",
            },
            "slot_provenance": {
                "area_m2": "current_message",
                "warm_floor_area_m2": "current_message",
            },
            "assumptions": [],
            "missing_slot_keys": [],
            "needs_clarification": False,
            "clarifying_question": None,
            "ready_for_catalog_selection": False,
            "response_mode": "project_progress",
            "reply": None,
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)
    session = bot.sessions.get("complex-area-live")
    session.category = "boilers"
    session.slots.update(
        {
            "complex_engineering_request": (
                "обвязка котла, бойлера и водяного тёплого пола"
            ),
            "boiler_requirement": "с бойлером",
            "warm_floor_requirement": "тёплый пол",
            "has_warm_floor": True,
            "area_m2": 180.0,
            "boiler_status_known": True,
        }
    )
    bot.sessions.save(session)

    response = bot.handle_chat("complex-area-live", message)

    assert response.need_handoff is True
    assert response.debug["slots"]["area_m2"] == 180.0
    assert response.debug["slots"]["warm_floor_area_m2"] == 60.0


def test_llm_bare_sewer_angle_cannot_replace_confirmed_diameter() -> None:
    message = "внутренняя, 90"
    llm = _AdversarialEngineeringLLM(
        {
            "handled": True,
            "continuation": True,
            "dialog_act": "continue",
            "intent_type": "attribute_request",
            "category": "sewer",
            "project_scope": None,
            "slots": {"diameter_mm": 90, "angle_deg": 90},
            "slot_evidence": {"diameter_mm": "90", "angle_deg": "90"},
            "slot_provenance": {
                "diameter_mm": "current_message",
                "angle_deg": "current_message",
            },
            "assumptions": [],
            "missing_slot_keys": [],
            "needs_clarification": False,
            "clarifying_question": None,
            "ready_for_catalog_selection": True,
            "response_mode": "catalog_search",
            "reply": None,
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)
    session = bot.sessions.get("sewer-angle-live")
    session.category = "sewer"
    session.slots.update({"element_type": "отвод", "diameter_mm": 110})
    bot.sessions.save(session)

    response = bot.handle_chat("sewer-angle-live", message)

    assert response.debug["slots"]["diameter_mm"] == 110
    assert response.debug["slots"]["angle_deg"] == 90


def test_cheaper_followup_after_exact_sku_uses_shown_pump_dimensions() -> None:
    shown = _pump("PUMP-SHOWN", price=5000, brand="VALTEC")
    cheaper = _pump("PUMP-CHEAPER", price=3000, brand="KROMWELL")
    bot = ChatOrchestrator(products=[shown, cheaper])

    first = bot.handle_chat("exact-cheaper-live", shown.sku)
    response = bot.handle_chat("exact-cheaper-live", "есть что подешевле?")

    assert [card.sku for card in first.products] == [shown.sku]
    assert [card.sku for card in response.products] == [cheaper.sku]
    assert response.products[0].price < first.products[0].price


def test_cheap_pump_cards_are_sorted_by_stock_then_price() -> None:
    products = [
        _pump("VALTEC-EXPENSIVE", price=4300, brand="VALTEC"),
        _pump("KROMWELL-CHEAP", price=2900, brand="KROMWELL"),
        _pump("UNIPUMP-MIDDLE", price=3200, brand="UNIPUMP"),
    ]
    bot = ChatOrchestrator(products=products)

    bot.handle_chat("cheap-order-live", "циркуляционный насос, подешевле")
    response = bot.handle_chat("cheap-order-live", "25/6, 130 мм")

    assert response.products
    prices = [card.price for card in response.products]
    assert prices == sorted(prices)
    assert response.need_handoff is False


def test_pipe_followup_does_not_request_an_already_known_diameter() -> None:
    bot = ChatOrchestrator(products=[])

    bot.handle_chat("pipe-diameter-live", "труба для воды")
    response = bot.handle_chat("pipe-diameter-live", "для горячей, 20 мм")

    assert response.debug["slots"]["diameter_mm"] == 20
    assert "20 мм" in response.answer
    assert "укажите расчётный диаметр" not in response.answer.lower()


def test_boiler_sizing_challenge_is_answered_before_adversarial_llm() -> None:
    message = "точно? а то ты раньше 12 советовал"
    llm = _AdversarialIntentLLM(
        {
            "intent_type": "unknown",
            "category": "boilers",
            "slots": {"power_kw": 12},
            "flags": {},
            "confidence": 0.9,
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)
    session = bot.sessions.get("power-challenge-live")
    session.category = "boilers"
    session.slots.update({"power_kw": 6.0, "area_m2": 100.0})
    session.history = [
        {"role": "user", "content": "6 кВт на 100 метров хватит?"},
        {
            "role": "assistant",
            "content": "6 кВт на 100 м² скорее не хватит; ориентир около 10 кВт.",
        },
    ]
    bot.sessions.save(session)

    response = bot.handle_chat("power-challenge-live", message)

    assert all(marker in response.answer.lower() for marker in ["6", "100", "10", "12"])
    assert "теплопотер" in response.answer.lower()
    assert response.debug["engineering_llm_requested"] is False
