from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def test_search_by_sku(sample_products: list[Product]) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(original_text="VT.228.N.04", sku="VT.228.N.04")
    )

    assert results[0].sku == "VT.228.N.04"


def test_search_by_name(sample_products: list[Product]) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(original_text="угловой кран 1/2", category="valves")
    )

    assert results
    assert results[0].sku == "VT.228.N.04"


def test_cheap_sorting(sample_products: list[Product]) -> None:
    search = FeedSearchAgent(sample_products)
    products = search.search(
        SearchQuery(
            original_text="циркуляционный насос подешевле",
            category="pumps",
            slots={"pump_type": "циркуляционный"},
            cheap=True,
        )
    )
    ranked = RankingAgent().rank(products, SearchQuery(
        original_text="циркуляционный насос подешевле",
        category="pumps",
        cheap=True,
    ))

    assert [product.sku for product in ranked[:2]] == ["PUMP-25-40", "PUMP-25-60"]

