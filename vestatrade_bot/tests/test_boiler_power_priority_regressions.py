from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def _boiler(
    sku: str,
    power_kw: float,
    *,
    in_stock: bool,
    brand: str = "TEST",
    price: float = 30_000,
) -> Product:
    return Product(
        sku=sku,
        name=f"Котёл электрический {brand} {power_kw:g} кВт",
        category_path="Котлы электрические",
        brand=brand,
        url=f"https://example.test/{sku}",
        price=price,
        stock_status="в наличии" if in_stock else "нет в наличии",
        stock_qty=2 if in_stock else 0,
        attributes_normalized={
            "мощность, кВт": f"{power_kw:g}",
            "тип котла": "Электрический",
            "количество контуров": "Одноконтурный",
        },
    )


def _power_query(power_kw: float) -> SearchQuery:
    return SearchQuery(
        original_text=f"Нужен электрический котёл на {power_kw:g} кВт",
        category="boilers",
        slots={"boiler_type": "электрический", "power_kw": power_kw},
    )


@pytest.mark.parametrize("requested_kw", [4.5, 9, 14, 24.4])
def test_exact_boiler_power_and_stock_outrank_other_factors_for_any_rating(
    requested_kw: float,
) -> None:
    exact_stock = _boiler(
        "EXACT-STOCK",
        requested_kw,
        in_stock=True,
        price=40_000,
    )
    exact_no_stock = _boiler(
        "EXACT-NO-STOCK",
        requested_kw,
        in_stock=False,
        price=20_000,
    )
    preferred_nearby = _boiler(
        "VALTEC-NEARBY",
        requested_kw + 1,
        in_stock=True,
        brand="VALTEC",
        price=10_000,
    )
    unknown_power = Product(
        sku="UNKNOWN",
        name="Котёл электрический без указанной мощности",
        category_path="Котлы электрические",
        brand="VALTEC",
        url="https://example.test/unknown",
        price=5_000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип котла": "Электрический"},
    )
    products = [preferred_nearby, unknown_power, exact_no_stock, exact_stock]
    query = _power_query(requested_kw)

    searched = FeedSearchAgent(products).search(query)
    ranked = RankingAgent().rank(products, query)

    expected_prefix = ["EXACT-STOCK", "EXACT-NO-STOCK", "VALTEC-NEARBY"]
    assert [product.sku for product in searched[:3]] == expected_prefix
    assert [product.sku for product in ranked[:3]] == expected_prefix


def test_search_keeps_every_exact_match_beyond_the_normal_result_limit() -> None:
    exact = [
        _boiler(f"EXACT-{index}", 14, in_stock=False)
        for index in range(35)
    ]
    nearby = [
        _boiler(f"NEAR-{index}", 15 + index, in_stock=True)
        for index in range(6)
    ]

    result = FeedSearchAgent([*nearby, *exact]).search(_power_query(14))

    assert len(result) == 41
    assert {product.sku for product in result[:35]} == {
        product.sku for product in exact
    }
    assert all(product.stock_status == "нет в наличии" for product in result[:35])


@pytest.mark.parametrize(
    ("message_power", "expected_kw"),
    [("4,5", 4.5), ("14", 14.0), ("24,4", 24.4)],
)
def test_chat_honors_any_explicit_integer_or_decimal_rating(
    message_power: str,
    expected_kw: float,
) -> None:
    bot = ChatOrchestrator(
        products=[
            _boiler("EXACT", expected_kw, in_stock=True),
            _boiler("NEARBY", expected_kw + 1, in_stock=True, brand="VALTEC"),
        ]
    )

    response = bot.handle_chat(
        f"explicit-{message_power}",
        f"Нужен электрический котёл на {message_power} кВт",
    )

    assert response.debug["slots"]["power_kw"] == expected_kw
    assert response.products[0].sku == "EXACT"


def test_chat_pages_exact_in_stock_boilers_and_puts_valtec_first() -> None:
    exact = [
        _boiler(
            f"EXACT-{index}",
            6,
            in_stock=True,
            brand="VALTEC" if index == 4 else f"BRAND-{index}",
        )
        for index in range(1, 5)
    ]
    bot = ChatOrchestrator(
        products=[
            _boiler("NEARBY-IN-STOCK", 7, in_stock=True, brand="VALTEC", price=10_000),
            _boiler("EXACT-NO-STOCK", 6, in_stock=False, price=20_000),
            *exact,
        ]
    )

    response = bot.handle_chat(
        "all-exact-six-kw",
        "Нужен электрический котёл на 6 кВт",
    )

    assert [product.sku for product in response.products] == [
        "EXACT-4",
        "EXACT-1",
        "EXACT-2",
    ]
    assert len(response.products) == 3
    assert "в наличии есть ещё 1 шт. с теми же параметрами" in response.answer.lower()

    more = bot.handle_chat("all-exact-six-kw", "Покажи ещё")

    assert more.products[0].sku == "EXACT-3"
    assert all(
        product.sku not in {"EXACT-4", "EXACT-1", "EXACT-2"}
        for product in more.products
    )
    answer = more.answer.lower()
    assert "продолжаю показывать точные котлы 6 квт в наличии" in answer
    assert "точные совпадения без остатка" in answer
    assert "показать ближайшие мощности" in answer
    assert all(product.sku != "NEARBY-IN-STOCK" for product in more.products)

    alternatives = bot.handle_chat("all-exact-six-kw", "Да")
    assert [product.sku for product in alternatives.products] == [
        "NEARBY-IN-STOCK"
    ]
    assert "только для сравнения" in alternatives.answer.lower()
    assert "не подтверждённая замена" in alternatives.answer.lower()

    exhausted = bot.handle_chat("all-exact-six-kw", "Покажи ещё")
    assert exhausted.products == []
    assert "все котлы по текущему запросу уже показаны" in exhausted.answer.lower()


def test_arbitrary_power_pages_have_truthful_stock_and_alternative_notes() -> None:
    products = [
        _boiler("EXACT-IN-1", 14, in_stock=True),
        _boiler("EXACT-IN-2", 14, in_stock=True),
        *[
            _boiler(f"EXACT-OUT-{index}", 14, in_stock=False)
            for index in range(1, 6)
        ],
        _boiler("NEAR-13", 13, in_stock=True),
        _boiler("NEAR-15", 15, in_stock=True),
    ]
    bot = ChatOrchestrator(products=products)
    session_id = "arbitrary-fourteen-kw-pages"

    first = bot.handle_chat(session_id, "Нужен электрический котёл на 14 кВт")
    second = bot.handle_chat(session_id, "Покажи ещё")
    third = bot.handle_chat(session_id, "Покажи ещё")
    alternatives = bot.handle_chat(session_id, "Да, покажи ближайшие")
    exhausted = bot.handle_chat(session_id, "Покажи ещё")

    assert [product.sku for product in first.products] == [
        "EXACT-IN-1",
        "EXACT-IN-2",
        "EXACT-OUT-1",
    ]
    assert "после доступных" in first.answer.lower()
    assert "есть ещё" not in first.answer.lower()
    assert all(product.stock_status == "нет в наличии" for product in second.products)
    assert "точные котлы 14 квт без наличия" in second.answer.lower()
    assert "продолжаю показывать" not in second.answer.lower()
    assert [product.sku for product in third.products] == ["EXACT-OUT-5"]
    assert "показать ближайшие мощности" in third.answer.lower()
    assert [product.sku for product in alternatives.products] == [
        "NEAR-13",
        "NEAR-15",
    ]
    assert "только для сравнения" in alternatives.answer.lower()
    shown = [
        product.sku
        for response in [first, second, third, alternatives]
        for product in response.products
    ]
    assert len(shown) == len(set(shown)) == len(products)
    assert exhausted.products == []


def test_changing_requested_power_starts_a_new_result_set() -> None:
    bot = ChatOrchestrator(
        products=[
            _boiler("SIX", 6, in_stock=True),
            _boiler("NINE", 9, in_stock=True),
            _boiler("TWELVE", 12, in_stock=True),
        ]
    )
    session_id = "change-explicit-power"

    first = bot.handle_chat(session_id, "Нужен электрический котёл на 6 кВт")
    second = bot.handle_chat(session_id, "Теперь нужен электрический котёл на 9 кВт")
    session = bot.sessions.get(session_id)

    assert first.products[0].sku == "SIX"
    assert second.products[0].sku == "NINE"
    assert session.shown_product_skus == [product.sku for product in second.products]


def test_no_exact_rating_asks_before_showing_available_nearby_power() -> None:
    gas_nearby = _boiler("GAS-NEARBY", 13.9, in_stock=True)
    gas_nearby = gas_nearby.model_copy(
        update={
            "name": "Котёл газовый TEST 13.9 кВт",
            "category_path": "Котлы газовые",
            "attributes_normalized": {
                **gas_nearby.attributes_normalized,
                "тип котла": "Газовый",
            },
        }
    )
    bot = ChatOrchestrator(
        products=[
            gas_nearby,
            _boiler("ELECTRIC-13", 13, in_stock=True),
            _boiler("ELECTRIC-15", 15, in_stock=True),
            _boiler("ELECTRIC-16-OUT", 16, in_stock=False),
        ]
    )
    session_id = "no-exact-rating"

    offer = bot.handle_chat(
        session_id,
        "Нужен электрический котёл на 14 кВт",
    )

    assert offer.products == []
    assert "показать ближайшие мощности" in offer.answer.lower()
    assert "не подтверждённая замена" in offer.answer.lower()

    accepted = bot.handle_chat(session_id, "Да")

    assert [product.sku for product in accepted.products] == [
        "ELECTRIC-13",
        "ELECTRIC-15",
    ]
    assert all(product.stock_status == "в наличии" for product in accepted.products)
    assert "GAS-NEARBY" not in {product.sku for product in accepted.products}
    assert "ELECTRIC-16-OUT" not in {
        product.sku for product in accepted.products
    }


def test_user_can_decline_nearby_power_and_new_rating_requires_new_consent() -> None:
    bot = ChatOrchestrator(
        products=[
            _boiler("SEVEN", 7, in_stock=True),
            _boiler("TEN", 10, in_stock=True),
        ]
    )
    session_id = "power-alternative-consent-scope"

    first_offer = bot.handle_chat(
        session_id,
        "Нужен электрический котёл на 6 кВт",
    )
    declined = bot.handle_chat(session_id, "Нет, не показывай")
    second_offer = bot.handle_chat(
        session_id,
        "Теперь нужен электрический котёл на 9 кВт",
    )

    assert first_offer.products == []
    assert declined.products == []
    assert "другой мощности не показываю" in declined.answer.lower()
    assert second_offer.products == []
    assert "показать ближайшие мощности" in second_offer.answer.lower()


def test_available_power_alternatives_are_paginated_three_at_a_time() -> None:
    bot = ChatOrchestrator(
        products=[
            _boiler(f"NEAR-{power}", power, in_stock=True)
            for power in [7, 8, 9, 11, 12, 13]
        ]
    )
    session_id = "power-alternative-pages"

    offer = bot.handle_chat(
        session_id,
        "Нужен электрический котёл на 10 кВт",
    )
    first = bot.handle_chat(session_id, "Да")
    second = bot.handle_chat(session_id, "Покажи ещё")

    assert offer.products == []
    assert len(first.products) == 3
    assert "есть ещё 3 доступных вариантов другой мощности" in first.answer.lower()
    assert len(second.products) == 3
    shown = [
        product.sku
        for response in [first, second]
        for product in response.products
    ]
    assert len(shown) == len(set(shown)) == 6
