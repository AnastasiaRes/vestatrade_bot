from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.product_card import ProductCardAgent
from app.models import Product, SearchQuery


def _pump(
    sku: str,
    name: str,
    *,
    price: float,
    stock_qty: int,
    brand: str = "TEST",
) -> Product:
    safe_slug = sku.lower().replace(" ", "-").replace("/", "-")
    return Product(
        sku=sku,
        name=name,
        category_path="Насосы циркуляционные",
        brand=brand,
        url=f"https://example.test/{safe_slug}",
        price=price,
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized={
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "напор": "6 м",
            "монтажная длина": "180 мм",
        },
    )


def test_explicit_two_sku_comparison_keeps_both_products() -> None:
    vrs = _pump(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180 с гайками",
        price=4186,
        stock_qty=5,
        brand="VALTEC",
    )
    unipump = _pump(
        "50058",
        "Насос циркуляционный UNIPUMP UPS 25-60 180",
        price=4777,
        stock_qty=7,
        brand="UNIPUMP",
    )
    bot = ChatOrchestrator(products=[vrs, unipump])

    response = bot.handle_chat(
        "compare-explicit-skus",
        "Сравни артикулы VRS.256.18.0 и 50058",
    )

    returned_skus = {product.sku for product in response.products}
    assert {"VRS.256.18.0", "50058"}.issubset(returned_skus)
    assert "VRS.256.18.0" in response.answer
    assert "50058" in response.answer
    answer = response.answer.lower()
    assert "4186" in answer and "4777" in answer
    assert "напор" in answer
    assert "монтажная длина" in answer
    assert "5 шт" in answer and "7 шт" in answer


def test_first_cold_request_loads_catalog_before_resolving_two_skus(
    monkeypatch,
) -> None:
    vrs = _pump(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180 с гайками",
        price=4186,
        stock_qty=5,
        brand="VALTEC",
    )
    unipump = _pump(
        "50058",
        "Насос циркуляционный UNIPUMP UPS 25-60 180",
        price=4777,
        stock_qty=7,
        brand="UNIPUMP",
    )
    bot = ChatOrchestrator()
    reload_calls: list[bool] = []

    def fake_reload_products(refresh: bool = True) -> tuple[int, str]:
        reload_calls.append(refresh)
        bot.search_agent.set_products([vrs, unipump])
        bot.products_loaded_from = "test-catalog"
        return 2, "test-catalog"

    monkeypatch.setattr(bot, "reload_products", fake_reload_products)

    response = bot.handle_chat(
        "compare-cold-first-request",
        "Сравни VRS.256.18.0 и 50058",
    )

    assert reload_calls == [False]
    assert {product.sku for product in response.products} == {
        "VRS.256.18.0",
        "50058",
    }
    assert "VRS.256.18.0" in response.answer
    assert "50058" in response.answer


def test_comparison_correction_adds_previously_ignored_numeric_sku() -> None:
    vrs = _pump(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180 с гайками",
        price=4186,
        stock_qty=5,
        brand="VALTEC",
    )
    unipump = _pump(
        "50058",
        "Насос циркуляционный UNIPUMP UPS 25-60 180",
        price=4777,
        stock_qty=7,
        brand="UNIPUMP",
    )
    bot = ChatOrchestrator(products=[vrs, unipump])
    session_id = "compare-correction"
    bot.handle_chat(session_id, "Покажи артикул VRS.256.18.0")

    response = bot.handle_chat(
        session_id,
        "Ты проигнорировал второй товар 50058 — сравни оба.",
    )

    assert {product.sku for product in response.products} == {
        "VRS.256.18.0",
        "50058",
    }
    assert "VRS.256.18.0" in response.answer
    assert "50058" in response.answer


def test_composite_catalog_sku_is_exact_and_never_replaced_by_analogs() -> None:
    exact = _pump(
        "PS 25/6G 180",
        "Насос циркуляционный Kromwell PS 25/6G 180",
        price=3900,
        stock_qty=4,
        brand="KROMWELL",
    )
    analog = _pump(
        "ANALOG-25-60",
        "Насос циркуляционный аналог 25/6 180",
        price=3200,
        stock_qty=9,
    )
    bot = ChatOrchestrator(products=[analog, exact])

    response = bot.handle_chat(
        "composite-exact-sku",
        "Найди точный артикул PS 25/6G 180",
    )

    assert response.debug["intent"] == "exact_sku"
    assert [product.sku for product in response.products] == ["PS 25/6G 180"]
    assert "PS 25/6G 180" in response.answer
    assert "ANALOG-25-60" not in response.answer


def test_in_stock_only_end_to_end_never_returns_zero_quantity() -> None:
    available = _pump(
        "PUMP-IN",
        "Насос циркуляционный PUMP-IN 25/6 180",
        price=4300,
        stock_qty=3,
    )
    unavailable = _pump(
        "PUMP-OUT",
        "Насос циркуляционный PUMP-OUT 25/6 180",
        price=3000,
        stock_qty=0,
    )
    bot = ChatOrchestrator(products=[unavailable, available])

    response = bot.handle_chat(
        "in-stock-end-to-end",
        "Покажи только циркуляционные насосы 25/6 180 в наличии",
    )

    assert response.products
    assert [product.sku for product in response.products] == ["PUMP-IN"]
    assert all(
        card.stock_qty is not None and card.stock_qty > 0
        for card in bot.sessions.get("in-stock-end-to-end").last_products
    )


def test_consultant_style_retrieval_honors_in_stock_constraint() -> None:
    available = _pump(
        "CONSULT-IN",
        "Насос циркуляционный CONSULT-IN 25/6 180",
        price=4300,
        stock_qty=2,
    )
    unavailable = _pump(
        "CONSULT-OUT",
        "Насос циркуляционный CONSULT-OUT 25/6 180",
        price=3100,
        stock_qty=0,
    )

    products = FeedSearchAgent([unavailable, available]).retrieve_for_consult(
        ["pumps"],
        {
            "pump_type": "циркуляционный",
            "in_stock": True,
        },
        per_category=4,
    )

    assert products
    assert all(product.stock_qty is not None and product.stock_qty > 0 for product in products)
    assert "CONSULT-OUT" not in {product.sku for product in products}


def test_final_card_guard_rejects_zero_quantity_for_in_stock_only_query() -> None:
    unavailable = _pump(
        "GUARD-OUT",
        "Насос циркуляционный GUARD-OUT 25/6 180",
        price=3000,
        stock_qty=0,
    )
    query = SearchQuery(
        original_text="Покажи только циркуляционные насосы в наличии",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "in_stock": True,
        },
        in_stock_only=True,
    )
    card = ProductCardAgent().build_card(unavailable, query)
    assert card is not None

    result = GuardrailsAgent().validate_cards([card], [unavailable], query)

    assert not result.ok
    assert any(
        "stock" in issue.lower() or "налич" in issue.lower()
        for issue in result.issues
    )


def test_exact_unavailable_sku_is_reported_without_card_under_stock_filter() -> None:
    unavailable = _pump(
        "EXACT-OUT",
        "Насос циркуляционный EXACT-OUT 25/6 180",
        price=3000,
        stock_qty=0,
    )
    bot = ChatOrchestrator(products=[unavailable])

    response = bot.handle_chat(
        "exact-out-stock-boundary",
        "Покажи точный артикул EXACT-OUT, только если он в наличии",
    )

    assert response.products == []
    assert "не в наличии" in response.answer.lower()
    assert "карточку товара не показываю" in response.answer.lower()
    assert response.need_handoff is False


def test_exact_unavailable_sku_is_shown_for_a_stock_question_not_a_stock_filter() -> None:
    unavailable = _pump(
        "11677",
        "Насос циркуляционный 11677",
        price=3000,
        stock_qty=0,
    )
    bot = ChatOrchestrator(products=[unavailable])

    response = bot.handle_chat(
        "exact-out-stock-question",
        "Покажи товар 11677: есть ли он в наличии?",
    )

    assert [product.sku for product in response.products] == ["11677"]
    assert "остатк" in response.answer.lower()
