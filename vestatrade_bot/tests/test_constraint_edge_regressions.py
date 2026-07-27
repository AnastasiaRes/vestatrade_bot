from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.product_card import ProductCardAgent
from app.agents.utils import normalize_text
from app.models import Product, ProductCard, SearchQuery


def _boiler(
    sku: str,
    *,
    price: float,
    wifi: str | None = None,
    contours: str = "Двухконтурный",
    description: str | None = None,
    docs_text: str | None = None,
) -> Product:
    attributes = {
        "тип товара": "Котёл",
        "тип котла": "Электрический",
        "количество контуров": contours,
        "мощность, кВт": "12",
    }
    if wifi is not None:
        attributes["Wi-Fi"] = wifi
    return Product(
        sku=sku,
        name=f"Котел электрический {sku} 12 кВт",
        category_path="Котлы электрические",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized=attributes,
        description=description,
        docs_text=docs_text,
    )


def _pump(
    sku: str,
    *,
    price: float,
    in_stock: bool = True,
) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {sku} 25/6 180",
        category_path="Насосы циркуляционные",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии" if in_stock else "нет в наличии",
        stock_qty=2 if in_stock else 0,
        attributes_normalized={
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "напор": "6 м",
            "монтажная длина": "180 мм",
        },
    )


def _card(product: Product, category: str) -> ProductCard:
    card = ProductCardAgent().build_card(
        product,
        SearchQuery(original_text=product.name, category=category),
    )
    assert card is not None
    return card


def test_analog_followup_applies_and_persists_new_hard_constraints() -> None:
    good = _boiler("GOOD", price=36000, wifi="Нет")
    over_budget = _boiler("OVER", price=39000, wifi="Нет")
    with_wifi = _boiler("WIFI", price=35000, wifi="Да")
    previously_shown = _boiler("OLD", price=42000, wifi="Да")
    bot = ChatOrchestrator(
        products=[good, over_budget, with_wifi, previously_shown]
    )
    session = bot.sessions.get("analog-new-constraints")
    session.category = "boilers"
    session.slots = {
        "boiler_type": "электрический",
        "contours": "двухконтурный",
        "allow_alternatives": False,
    }
    session.last_products = [_card(previously_shown, "boilers")]
    bot.sessions.save(session)

    response = bot.handle_chat(
        "analog-new-constraints",
        "Покажи аналог котла без Wi-Fi и не дороже 37 000 рублей",
    )

    assert [product.sku for product in response.products] == ["GOOD"]
    assert response.debug["slots"]["max_price"] == 37000
    assert response.debug["slots"]["excluded_features"] == ["wifi"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("до 6000 ₽", 6000),
        ("до 6 000 ₽", 6000),
        ("не дороже 37 т.р.", 37000),
    ],
)
def test_price_parser_accepts_common_ruble_formats(
    text: str,
    expected: float,
) -> None:
    assert (
        IntentRouterAgent._extract_price_bound(
            normalize_text(text),
            upper=True,
        )
        == expected
    )


@pytest.mark.parametrize(
    "text",
    [
        "котел мощностью до 12000 Вт",
        "котел для площади до 12000 м2",
    ],
)
def test_price_parser_does_not_treat_power_or_area_as_money(text: str) -> None:
    assert (
        IntentRouterAgent._extract_price_bound(
            normalize_text(text),
            upper=True,
        )
        is None
    )


def test_guardrails_reject_contour_mismatch() -> None:
    one_contour = _boiler(
        "ONE",
        price=30000,
        wifi="Нет",
        contours="Одноконтурный",
    )
    query = SearchQuery(
        original_text="нужен двухконтурный электрический котел без Wi-Fi",
        category="boilers",
        slots={
            "boiler_type": "электрический",
            "contours": "двухконтурный",
            "excluded_features": ["wifi"],
        },
    )
    card = _card(one_contour, "boilers")

    result = GuardrailsAgent().validate_cards([card], [one_contour], query)

    assert not result.ok
    assert any(
        "contour" in issue.lower() or "контур" in issue.lower()
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "message",
    [
        "Нужен котел без модуля Wi-Fi",
        "Нужен котел, Wi-Fi не нужен",
        "Нужен котел без вайфая",
    ],
)
def test_wifi_exclusion_phrasings_are_recognized(message: str) -> None:
    intent = IntentRouterAgent()._rule_based(message, None)

    assert intent.slots["excluded_features"] == ["wifi"]


@pytest.mark.parametrize(
    "message",
    [
        "Нужен котел с поддержкой Wi-Fi",
        "Нужен котел с вайфаем",
    ],
)
def test_wifi_requirement_phrasings_are_recognized(message: str) -> None:
    intent = IntentRouterAgent()._rule_based(message, None)

    assert intent.slots["required_features"] == ["wifi"]


def test_opposite_wifi_correction_replaces_previous_constraint() -> None:
    with_wifi = _boiler("WITH-WIFI", price=35000, wifi="Да")
    without_wifi = _boiler("WITHOUT-WIFI", price=36000, wifi="Нет")
    bot = ChatOrchestrator(products=[with_wifi, without_wifi])
    session = bot.sessions.get("wifi-correction")
    session.category = "boilers"
    session.slots = {
        "boiler_type": "электрический",
        "contours": "двухконтурный",
        "required_features": ["wifi"],
        "allow_alternatives": False,
    }
    session.last_products = [_card(with_wifi, "boilers")]
    bot.sessions.save(session)

    response = bot.handle_chat(
        "wifi-correction",
        "Нет, нужен котел без Wi-Fi",
    )

    assert response.debug["slots"]["excluded_features"] == ["wifi"]
    assert "required_features" not in response.debug["slots"]
    assert [product.sku for product in response.products] == ["WITHOUT-WIFI"]


@pytest.mark.parametrize("evidence_field", ["description", "docs_text"])
def test_required_wifi_can_be_grounded_from_product_text(
    evidence_field: str,
) -> None:
    product = _boiler(
        "TEXT-EVIDENCE",
        price=33000,
        **{evidence_field: "Встроенный Wi-Fi модуль для дистанционного управления."},
    )
    query = SearchQuery(
        original_text="электрический котел с поддержкой Wi-Fi",
        category="boilers",
        slots={
            "boiler_type": "электрический",
            "required_features": ["wifi"],
        },
    )

    results = FeedSearchAgent([product]).search(query)

    assert [item.sku for item in results] == ["TEXT-EVIDENCE"]


def test_cheap_stock_first_order_is_accepted_by_guardrails() -> None:
    unavailable_cheaper = _pump("OUT-CHEAP", price=1000, in_stock=False)
    available_dearer = _pump("IN-DEAR", price=2000, in_stock=True)
    query = SearchQuery(
        original_text="покажи самый дешевый циркуляционный насос",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "sort_mode": "price_asc",
        },
        cheap=True,
    )
    search_results = FeedSearchAgent(
        [unavailable_cheaper, available_dearer]
    ).search(query)
    cards = ProductCardAgent().build_cards(search_results, query, limit=3)

    guard = GuardrailsAgent().validate_cards(cards, search_results, query)

    assert [product.sku for product in search_results] == [
        "IN-DEAR",
        "OUT-CHEAP",
    ]
    assert guard.ok, guard.issues


def test_global_cheapest_followup_searches_beyond_shown_cards() -> None:
    globally_cheapest = _pump("GLOBAL-CHEAP", price=1000)
    previously_shown = _pump("SHOWN-DEAR", price=5000)
    bot = ChatOrchestrator(products=[globally_cheapest, previously_shown])
    session = bot.sessions.get("global-cheapest")
    session.category = "pumps"
    session.slots = {
        "pump_type": "циркуляционный",
        "connection_size": 25,
        "head_m": 6,
        "mounting_length_mm": 180,
    }
    session.last_products = [_card(previously_shown, "pumps")]
    bot.sessions.save(session)

    response = bot.handle_chat(
        "global-cheapest",
        "Назови один самый дешевый подходящий",
    )

    assert [product.sku for product in response.products] == ["GLOBAL-CHEAP"]
    assert "SHOWN-DEAR" not in response.answer


def test_single_result_command_does_not_limit_later_search() -> None:
    first = _pump("FIRST", price=1000)
    second = _pump("SECOND", price=2000)
    bot = ChatOrchestrator(products=[first, second])

    one = bot.handle_chat(
        "single-is-transient",
        "Назови один циркуляционный насос 25/6 180",
    )
    many = bot.handle_chat(
        "single-is-transient",
        "Покажи циркуляционные насосы 25/6 180",
    )

    assert len(one.products) == 1
    assert len(many.products) == 2
    assert "result_limit" not in many.debug["slots"]
