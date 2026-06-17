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


def test_fitting_is_not_classified_as_pipe() -> None:
    fitting = Product(
        sku="VTp.751.0.025",
        name="Угольник 90 PPR 25мм",
        category_path="Фитинги/Фитинги полипропиленовые",
        brand="VALTEC",
        url="https://example.test/ugol",
        price=22,
        stock_status="в наличии",
        stock_qty=10,
    )
    pipe = Product(
        sku="VTp.700.0020.25",
        name="Труба PN 20, 25 MM (белый)",
        category_path="Трубы/Трубы полипропиленовые",
        brand="VALTEC",
        url="https://example.test/truba",
        price=182,
        stock_status="в наличии",
        stock_qty=10,
    )
    agent = FeedSearchAgent([fitting, pipe])

    assert agent.canonical_category(fitting) == "fittings"
    assert agent.canonical_category(pipe) == "pipes"
    # Запрос трубы не должен поднимать угольник как «трубу».
    retrieved = agent.retrieve_for_consult(["pipes"], {}, per_category=4)
    assert [p.sku for p in retrieved] == ["VTp.700.0020.25"]


def test_consult_retrieval_boilers_prefers_adequate_power() -> None:
    weak = Product(
        sku="ECA-6", name="Котел электрический Arceus 6 кВт", category_path="Акции",
        url="https://example.test/eca6", price=38010, stock_status="в наличии", stock_qty=1,
        attributes_normalized={"мощность, квт": "6"},
    )
    strong = Product(
        sku="SB32", name="Котел газовый Arderia SB32 32 кВт", category_path="Акции",
        url="https://example.test/sb32", price=38535, stock_status="в наличии", stock_qty=2,
        attributes_normalized={"мощность, квт": "32"},
    )
    agent = FeedSearchAgent([weak, strong])

    retrieved = agent.retrieve_for_consult(["boilers"], {"area_m2": 240}, per_category=4)
    # Для 240 м² (≈24 кВт) адекватный по мощности котёл идёт первым.
    assert retrieved[0].sku == "SB32"


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

