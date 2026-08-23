"""Серия и исполнение — такие же обязательные требования, как размер.

Оставались два FAIL регрессионного прогона: «Серия: FTV» приносила исполнение
FKO, а «Материал: Полипропилен, Латунь» — чистый полипропилен. Обе подмены
функциональные: FKO — боковое подключение, FTV — нижнее со встроенным
клапаном; комбинированный фитинг имеет латунную резьбовую часть, а полимерный
её не имеет.
"""

from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.models import Product


def _radiator(sku: str, name: str, series: str) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Радиаторы отопления",
        brand="KERMI",
        url=f"https://example.test/{sku.lower()}",
        price=9000.0,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип товара": "Радиатор отопления", "тип": "22", "серия": series},
    )


def test_named_series_does_not_leak_a_sibling_series() -> None:
    agent = FeedSearchAgent(
        products=[
            _radiator("FKO-1", "Радиатор KERMI FK O тип 22 высота 500 длина 1000", "FKO"),
            _radiator("FTV-1", "Радиатор KERMI FT V тип 22 высота 500 длина 1000", "FTV"),
        ]
    )
    found = agent.find_named_models(
        name_tokens=["kermi", "fko"],
        message="радиатор KERMI FKO тип 22 500 на 1000",
        category="radiators",
    )
    assert [product.sku for product in found] == ["FKO-1"]


def _coupling(sku: str, name: str, material: str) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Фитинги",
        brand="РОСТерм",
        url=f"https://example.test/{sku.lower()}",
        price=100.0,
        stock_status="в наличии",
        stock_qty=10,
        attributes_normalized={
            "тип товара": "Муфта",
            "диаметр (мм)": "25",
            "материал": material,
        },
    )


@pytest.mark.parametrize(
    "message",
    [
        "нужна комбинированная муфта РОСТерм 25 с латунью",
        "муфта комбинированная 25 с латунной резьбой",
        "нужен комбинированный уголок 20х1/2",
    ],
)
def test_combined_execution_is_extracted(message: str) -> None:
    assert IntentRouterAgent().route(message).slots.get("combined_metal") is True


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Нужен Муфта, РОСТерм, Материал: Полипропилен, Латунь", "Полипропилен, Латунь"),
        ("Нужен Заглушка, STOUT, Материал: Латунь", "Латунь"),
    ],
)
def test_material_specification_becomes_a_constraint(message: str, expected: str) -> None:
    """«Материал: Латунь» — спецификация материала, а не комбинированное исполнение."""
    slots = IntentRouterAgent()._slots_from_spec_lines(message)
    assert slots.get("material_spec") == expected
    assert IntentRouterAgent().route(message).slots.get("combined_metal") is None


def test_material_specification_needs_every_named_component() -> None:
    agent = FeedSearchAgent(products=[])
    combined = _coupling("MF", "Муфта комбинированная ВР 25", "Полипропилен, Латунь")
    plain = _coupling("PP", "Муфта 25мм", "Полипропилен")

    assert agent._material_spec_matches(combined, "Полипропилен, Латунь") is True
    assert agent._material_spec_matches(plain, "Полипропилен, Латунь") is False
    assert agent._material_spec_matches(plain, "Полипропилен") is True


def test_plain_polymer_is_not_offered_for_a_combined_request() -> None:
    agent = FeedSearchAgent(products=[])
    combined = _coupling("MF", 'Муфта комбинированная ВР 25-1/2"', "Полипропилен, Латунь")
    plain = _coupling("PP", "Муфта 25мм (белый)", "Полипропилен")

    assert agent._combined_metal_matches(combined) is True
    assert agent._combined_metal_matches(plain) is False


def test_combined_fitting_skips_the_system_question() -> None:
    """«PPR или канализация» для комбинированного фитинга — лишний вопрос."""
    from app.agents.slot_filling import SlotFillingAgent

    result = SlotFillingAgent()._fittings(
        {"element_type": "муфта", "combined_metal": True, "diameter_mm": 25}
    )
    assert result.needs_clarification is False
