from __future__ import annotations

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


def _six_kw_query() -> SearchQuery:
    return SearchQuery(
        original_text="Нужен электрический котёл на 6 кВт",
        category="boilers",
        slots={"boiler_type": "электрический", "power_kw": 6.0},
    )


def test_exact_boiler_power_and_stock_outrank_brand_price_and_nearby_power() -> None:
    exact_stock = _boiler("EXACT-STOCK", 6, in_stock=True, price=40_000)
    exact_no_stock = _boiler("EXACT-NO-STOCK", 6, in_stock=False, price=20_000)
    preferred_nearby = _boiler(
        "VALTEC-NEARBY",
        7,
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
    query = _six_kw_query()

    searched = FeedSearchAgent(products).search(query)
    ranked = RankingAgent().rank(products, query)

    expected_prefix = ["EXACT-STOCK", "EXACT-NO-STOCK", "VALTEC-NEARBY"]
    assert [product.sku for product in searched[:3]] == expected_prefix
    assert [product.sku for product in ranked[:3]] == expected_prefix


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
    assert "следующие котлы ровно 6 квт в наличии" in more.answer.lower()

    exhausted = bot.handle_chat("all-exact-six-kw", "Покажи ещё")
    assert exhausted.products == []
