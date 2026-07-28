from __future__ import annotations

from app.agents.product_card import ProductCardAgent
from app.models import Product, SearchQuery


def _gas_water_heater(power_kw: str) -> Product:
    return Product(
        sku="351113",
        name="Водонагреватель газовый проточный THERMEX T 20 D",
        category_path="Водонагреватели / Колонки газовые",
        brand="Thermex",
        url="https://example.test/351113",
        price=13_160,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": "Проточный",
            "вид нагрева": "Газовый",
            "монтаж": "Настенный",
            "мощность, квт": power_kw,
        },
        description=(
            "Модель T 20 D мощностью 20 кВт, производительность 10 литров "
            "воды в минуту."
        ),
    )


def test_water_heater_card_suppresses_implausible_primary_power_from_feed() -> None:
    card = ProductCardAgent().build_card(
        _gas_water_heater("0.02"),
        SearchQuery(
            original_text="газовая колонка",
            category="water_heaters",
        ),
    )

    assert card is not None
    assert "мощность, квт" not in card.characteristics
    assert "0.02" not in card.characteristics.values()


def test_water_heater_card_keeps_plausible_grounded_power() -> None:
    card = ProductCardAgent().build_card(
        _gas_water_heater("20"),
        SearchQuery(
            original_text="газовая колонка",
            category="water_heaters",
        ),
    )

    assert card is not None
    assert card.characteristics["мощность, квт"] == "20"
