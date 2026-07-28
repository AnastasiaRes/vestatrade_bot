from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _pump(
    sku: str,
    *,
    price: float = 4_000,
    stock_qty: int = 3,
) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {sku}",
        category_path="Насосы циркуляционные",
        brand="TEST",
        url=f"https://example.test/{sku.replace(' ', '-').replace('/', '-')}",
        price=price,
        currency="RUB",
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized={
            "артикул": sku,
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "напор": "6 м",
            "монтажная длина": "180 мм",
        },
    )


def test_longer_overlapping_sku_suppresses_shorter_catalog_identity() -> None:
    short = _pump("200001")
    long = _pump("200001.1")
    search = FeedSearchAgent([short, long])

    resolved = search.resolve_sku_mentions("Покажи артикул 200001.1")

    assert [product.sku for product in resolved] == ["200001.1"]


def test_price_in_comparison_is_not_resolved_as_numeric_sku() -> None:
    named = _pump("VRS.256.18.0", price=4_186)
    numeric_sku = _pump("50058", price=4_777)
    search = FeedSearchAgent([named, numeric_sku])

    resolved = search.resolve_sku_mentions(
        "Сравни VRS.256.18.0 с насосом за 50058 рублей"
    )

    assert [product.sku for product in resolved] == ["VRS.256.18.0"]


def test_bare_composite_sku_opens_the_exact_catalog_product() -> None:
    exact = _pump("PS 25/6G 180", price=3_900)
    analog = _pump("ANALOG-25-60", price=3_200)
    bot = ChatOrchestrator(products=[analog, exact])

    response = bot.handle_chat("bare-composite-sku", "PS 25/6G 180")

    assert response.debug["intent"] == "exact_sku"
    assert [product.sku for product in response.products] == ["PS 25/6G 180"]
    assert "ANALOG-25-60" not in response.answer


def test_saved_project_cart_stock_only_omits_zero_stock_products() -> None:
    available = _pump("CART-IN", stock_qty=3)
    unavailable = _pump("CART-OUT", stock_qty=0)
    bot = ChatOrchestrator(products=[available, unavailable])
    session_id = "saved-cart-stock-only"
    session = bot.sessions.get(session_id)
    session.slots = {
        "project_scope": "heating",
        "in_stock": True,
        "project_cart": {
            "pumps": ["CART-IN", "CART-OUT"],
        },
    }
    bot.sessions.save(session)

    response = bot.handle_chat(
        session_id,
        "Собери корзину, только товары в наличии",
    )

    assert [product.sku for product in response.products] == ["CART-IN"]
    assert "CART-OUT" not in response.answer
