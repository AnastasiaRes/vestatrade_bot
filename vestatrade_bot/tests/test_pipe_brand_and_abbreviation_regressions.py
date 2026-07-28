from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _ppr_pipe(
    sku: str,
    brand: str,
    *,
    price: float,
    description_only_ratings: bool = False,
) -> Product:
    attributes = {
        "тип товара": "Труба",
        "диаметр (мм)": "20",
        "материал": "Полипропилен",
        "назначение": "ХВС, ГВС",
    }
    if not description_only_ratings:
        attributes.update(
            {
                "максимальная рабочая температура": "95 °C",
                "максимальное рабочее давление": "20 бар",
            }
        )
    return Product(
        sku=sku,
        name=f"Труба PPR PN20 20 мм {brand}",
        category_path="Трубы",
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=10,
        attributes_normalized=attributes,
        description=(
            "Труба из полипропилена для холодного и горячего водоснабжения. "
            "Допустимое рабочее давление при температуре воды 70 °C — 10 бар."
        ),
    )


def _metal_plastic_valtec() -> Product:
    return Product(
        sku="V1620.100",
        name="Труба м/п VALTEC 16(2,0) бухта 100 м",
        category_path="Трубы",
        brand="VALTEC",
        url="https://example.test/v1620",
        price=125,
        stock_status="в наличии",
        stock_qty=100,
        attributes_normalized={
            "тип товара": "Труба",
            "полное наименование": "Труба металлопластиковая VALTEC 16(2,0)",
        },
        description=(
            "Металлополимерная труба PEX-AL-PEX для холодного и горячего "
            "водоснабжения."
        ),
    )


@pytest.mark.parametrize(
    ("message", "temperature"),
    [
        ("труба 16 мм для ГВС", "горячая"),
        ("труба 16 мм для Г.В.С.", "горячая"),
        ("труба 16 мм для ХВС", "холодная"),
        ("труба 16 мм для Х В С", "холодная"),
    ],
)
def test_pipe_water_abbreviations_are_understood(
    message: str,
    temperature: str,
) -> None:
    result = IntentRouterAgent().route(message)

    assert result.category == "pipes"
    assert result.slots["pipe_purpose"] == "водоснабжение"
    assert result.slots["water_temperature"] == temperature
    assert result.slots["diameter_mm"] == 16


def test_gvs_pipe_without_material_asks_instead_of_guessing() -> None:
    bot = ChatOrchestrator(products=[_metal_plastic_valtec()])

    response = bot.handle_chat(
        "gvs-material",
        (
            "Труба для ГВС 16 мм внутри дома, "
            "температура 70 C, давление 6 бар"
        ),
    )

    assert response.products == []
    assert response.debug["slots"]["water_temperature"] == "горячая"
    assert "металлопласт" in response.answer.lower()
    assert "valtec" in response.answer.lower()


def test_metal_plastic_abbreviation_selects_valtec_pipe() -> None:
    bot = ChatOrchestrator(products=[_metal_plastic_valtec()])

    response = bot.handle_chat(
        "gvs-metal-plastic",
        (
            "Нужна м/п труба VALTEC для ГВС 16 мм внутри дома, "
            "температура 70 C, давление 6 бар"
        ),
    )

    assert response.debug["slots"]["pipe_material"] == "металлопластик"
    assert [product.sku for product in response.products] == ["V1620.100"]
    assert "нет числового подтверждения" in response.answer.lower()


def test_valtec_is_ranked_first_when_brand_was_not_requested() -> None:
    products = [
        _ppr_pipe("EKO-20", "Ekoplastik", price=70),
        _ppr_pipe("ROSTERM-20", "РОСТерм", price=76),
        _ppr_pipe(
            "VTp.700.0020.20",
            "VALTEC",
            price=117,
            description_only_ratings=True,
        ),
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "ppr-default-brand",
        (
            "Полипропиленовая труба для ГВС 20 мм внутри дома, "
            "температура 70 C, давление 6 бар"
        ),
    )

    assert response.products
    assert response.products[0].sku == "VTp.700.0020.20"
    assert "VALTEC" in response.products[0].name


def test_explicit_catalog_brand_overrides_valtec_priority() -> None:
    products = [
        _ppr_pipe(
            "VTp.700.0020.20",
            "VALTEC",
            price=117,
            description_only_ratings=True,
        ),
        _ppr_pipe("ROSTERM-20", "РОСТерм", price=76),
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "ppr-explicit-brand",
        (
            "Полипропиленовая труба РОСТерм для ГВС 20 мм внутри дома, "
            "температура 70 C, давление 6 бар"
        ),
    )

    assert response.debug["slots"]["brand"] == "РОСТерм"
    assert [product.sku for product in response.products] == ["ROSTERM-20"]
