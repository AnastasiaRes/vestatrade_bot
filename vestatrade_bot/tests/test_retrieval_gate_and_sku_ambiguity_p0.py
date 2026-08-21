from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _product(
    sku: str,
    name: str,
    *,
    category: str,
    attributes: dict[str, str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        url=f"https://example.test/{sku}",
        price=100,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized=attributes or {},
    )


def test_ambiguous_explicit_sku_typo_asks_instead_of_selecting_one_product() -> None:
    products = [
        _product(
            f"15100{digit}",
            f"Водонагреватель THERMEX серия {digit}",
            category="Водонагреватели",
            attributes={"Тип товара": "Водонагреватель"},
        )
        for digit in range(1, 10)
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat("sku-ambiguous", "Найди артикул 15100Z")

    assert response.products == []
    assert "151001" in response.answer
    assert "151002" in response.answer
    assert "несколько" in response.answer.lower()
    assert "уточните" in response.answer.lower()


def test_fuzzy_sewer_name_cannot_bypass_blocking_length_question() -> None:
    products = [
        _product(
            "HTEM-50-250",
            "Труба канализационная внутренняя 50x250",
            category="Канализация внутренняя",
            attributes={
                "Тип товара": "Труба",
                "Диаметр (мм)": "50",
                "Длина": "250 мм",
            },
        ),
        _product(
            "HTEM-50-500",
            "Труба канализационная внутренняя 50x500",
            category="Канализация внутренняя",
            attributes={
                "Тип товара": "Труба",
                "Диаметр (мм)": "50",
                "Длина": "500 мм",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "sewer-length-gate",
        "Нужна внутренняя канализационная труба 50 мм",
    )

    assert response.products == []
    assert "длин" in response.answer.lower()


def test_exact_full_sewer_product_name_remains_an_identity_lookup() -> None:
    product = _product(
        "HTEM-50-500",
        "Труба канализационная внутренняя 50x500",
        category="Канализация внутренняя",
        attributes={
            "Тип товара": "Труба",
            "Диаметр (мм)": "50",
            "Длина": "500 мм",
        },
    )
    bot = ChatOrchestrator(products=[product])

    response = bot.handle_chat(
        "sewer-exact-name",
        "Труба канализационная внутренняя 50x500",
    )

    assert [card.sku for card in response.products] == ["HTEM-50-500"]

