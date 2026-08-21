from __future__ import annotations

from app.agents.guardrails import GuardrailsAgent
from app.models import Product, ProductCard, SearchQuery


def _product(sku: str, name: str, attrs: dict[str, str]) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Краны шаровые",
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=500,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized=attrs,
    )


def _card(product: Product) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        price=product.price or 0,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
    )


def test_guard_rechecks_full_size_thread_and_handle_contract() -> None:
    wrong = _product(
        "WRONG",
        'Кран шаровой 3/4" ВР/ВР ручка рычаг',
        {
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "3/4",
            "тип резьбы": "Внутренняя-внутренняя (ff)",
            "тип ручки": "Рычаг",
        },
    )
    query = SearchQuery(
        original_text='кран 1/2 ВР-НР с бабочкой',
        category="valves",
        brand="VALTEC",
        slots={
            "size_inch": "1/2",
            "thread_type": "fm",
            "handle_type": "butterfly",
            "product_kind": "ball_valve",
        },
    )

    result = GuardrailsAgent().validate_cards([_card(wrong)], [wrong], query)

    assert result.ok is False
    assert any("mandatory category characteristics" in issue for issue in result.issues)


def test_guard_rejects_cross_category_card_even_without_slots() -> None:
    radiator = Product(
        sku="RAD",
        name="Радиатор стальной панельный",
        category_path="Радиаторы отопления",
        brand="TEST",
        url="https://example.test/rad",
        price=1000,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип товара": "Радиатор отопления"},
    )

    result = GuardrailsAgent().validate_cards(
        [_card(radiator)],
        [radiator],
        SearchQuery(original_text="термостатический клапан", category="radiator_fittings"),
    )

    assert result.ok is False
    assert any("not radiator_fittings" in issue for issue in result.issues)
