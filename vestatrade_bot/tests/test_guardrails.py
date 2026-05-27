from __future__ import annotations

from app.agents.guardrails import GuardrailsAgent
from app.agents.product_card import ProductCardAgent
from app.models import Product, ProductCard, SearchQuery


def test_product_without_url_is_not_carded() -> None:
    product = Product(
        sku="NOURL",
        name="Товар без ссылки",
        price=10,
        stock_status="в наличии",
    )

    card = ProductCardAgent().build_card(product, SearchQuery(original_text="товар"))

    assert card is None


def test_guardrails_reject_invented_characteristic(sample_products: list[Product]) -> None:
    product = sample_products[0]
    card = ProductCard(
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        price=product.price or 0,
        currency=product.currency,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
        characteristics={"мощность": "99 кВт"},
    )

    result = GuardrailsAgent().validate_cards(
        [card],
        [product],
        SearchQuery(original_text="кран", category="valves"),
    )

    assert not result.ok
    assert any("invented characteristic" in issue for issue in result.issues)


def test_guardrails_reject_unsorted_cheap(sample_products: list[Product]) -> None:
    products = [product for product in sample_products if product.sku in {"PUMP-25-40", "PUMP-25-60"}]
    cards = [
        ProductCardAgent().build_card(product, SearchQuery(original_text="насос", category="pumps"))
        for product in reversed(products)
    ]

    result = GuardrailsAgent().validate_cards(
        [card for card in cards if card is not None],
        products,
        SearchQuery(original_text="насос подешевле", category="pumps", cheap=True),
    )

    assert not result.ok
    assert "cheap request was not sorted by ascending price" in result.issues


def test_complectation_requires_feed_confirmation(sample_products: list[Product]) -> None:
    product = next(product for product in sample_products if product.sku == "ARD-E9")

    result = GuardrailsAgent().validate_complectation_answer(product, ["насос", "бак"])

    assert not result.ok
    assert result.safe_message
    assert "Не вижу подтверждения комплектации" in result.safe_message

