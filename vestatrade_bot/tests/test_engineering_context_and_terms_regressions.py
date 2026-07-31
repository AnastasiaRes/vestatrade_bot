from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product, SessionState


def _product(
    sku: str,
    name: str,
    category_path: str,
    *,
    price: float = 1000,
    qty: int = 1,
    brand: str = "TEST",
    attrs: dict[str, str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category_path,
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=price,
        currency="RUB",
        stock_status="в наличии" if qty > 0 else "нет в наличии",
        stock_qty=qty,
        attributes_normalized=attrs or {},
    )


def _boiler(
    sku: str,
    *,
    contours: str,
    chamber: str,
    power: int = 16,
    qty: int = 2,
    boiler_type: str = "Газовый",
) -> Product:
    return _product(
        sku,
        f"Котёл {boiler_type.lower()} {sku} {power} кВт",
        "Котлы",
        qty=qty,
        attrs={
            "тип товара": "Котёл",
            "тип котла": boiler_type,
            "мощность, кВт": str(power),
            "количество контуров": contours,
            "камера сгорания": chamber,
        },
    )


def test_hydraulic_accumulator_is_not_a_pump_or_random_cheap_product() -> None:
    products = [
        _product(
            "WATER-24",
            "Гидроаккумулятор горизонтальный 24 л",
            "Баки мембранные",
            price=3200,
            attrs={
                "тип товара": "Гидроаккумулятор (водоснабжение)",
                "объем бака, л": "24",
                "ориентация бака": "Горизонтальный",
            },
        ),
        _product(
            "HEAT-24",
            "Расширительный бак отопления 24 л",
            "Баки мембранные",
            price=1800,
            attrs={
                "тип товара": "Расширительный бак (отопление)",
                "объем бака, л": "24",
            },
        ),
        _product(
            "BRACKET-20",
            "Кронштейн пластиковый 20 мм",
            "Крепёж",
            price=6,
            qty=100,
            attrs={"тип товара": "Кронштейн"},
        ),
    ]
    bot = ChatOrchestrator(products=products)

    first = bot.handle_chat(
        "hydraulic-purpose",
        "бак для поддержания давления и защиты насоса от частых включений",
    )
    assert first.debug["category"] == "hydraulic_accumulators"
    assert first.debug["slots"]["tank_application"] == "водоснабжение"
    assert "объём" in first.answer.lower()
    assert first.products == []

    second = bot.handle_chat("hydraulic-purpose", "24 л")
    assert [card.sku for card in second.products] == ["WATER-24"]
    assert "HEAT-24" not in second.answer
    assert "BRACKET-20" not in second.answer


def test_cheapest_hydraulic_accumulator_without_volume_asks_for_sizing() -> None:
    bot = ChatOrchestrator(
        products=[
            _product(
                "WATER-12",
                "Гидроаккумулятор 12 л",
                "Баки мембранные",
                price=2000,
                attrs={
                    "тип товара": "Гидроаккумулятор (водоснабжение)",
                    "объем бака, л": "12",
                },
            ),
            _product(
                "BRACKET",
                "Кронштейн",
                "Крепёж",
                price=6,
                attrs={"тип товара": "Кронштейн"},
            ),
        ]
    )

    response = bot.handle_chat(
        "hydraulic-cheap",
        "самый дешёвый гидроаккумулятор в наличии",
    )

    assert response.debug["category"] == "hydraulic_accumulators"
    assert "расчётный объём" in response.answer.lower()
    assert response.products == []
    assert "кронштейн" not in response.answer.lower()


@pytest.mark.parametrize(
    ("message", "category", "expected"),
    [
        ("БКН 100 л", "water_heaters", {"heater_type": "косвенного нагрева", "volume_l": 100}),
        ("ЭВН 80 л", "water_heaters", {"heater_type": "накопительный", "energy_source": "электрический"}),
        ("радиатор м/о 500, 10 секций", "radiators", {"radiator_size_mm": 500, "sections": 10}),
        ("кран ДУ20 РУ16 ВР/НР", "valves", {"diameter_mm": 20, "pressure_class_bar": 16.0, "thread_type": "fm"}),
        ("ПНД труба ПЭ100 от скважины до дома SDR11", "pipes", {"pipe_material": "пэ100", "sdr": 11.0}),
    ],
)
def test_common_engineering_abbreviations_are_structured(
    message: str,
    category: str,
    expected: dict[str, object],
) -> None:
    result = IntentRouterAgent().route(message)
    assert result.category == category
    for key, value in expected.items():
        assert result.slots[key] == value
    if category == "pipes":
        assert "pump_type" not in result.slots
        assert "well_depth_m" not in result.slots


def test_short_pipe_answers_are_extracted_in_pending_pipe_context() -> None:
    router = IntentRouterAgent()
    session = SessionState(session_id="pipe-short")
    session.category = "pipes"
    session.pending_category = "pipes"
    session.pending_question = "Укажите материал, участок, температуру и давление."

    material = router.route("м/п", session)
    parameters = router.route("внутри дома, 70 °C, 6 бар", session)

    assert material.category == "pipes"
    assert material.slots["pipe_material"] == "металлопластик"
    assert parameters.category == "pipes"
    assert parameters.slots["pipe_service"] == "разводка внутри дома"
    assert parameters.slots["operating_temperature_c"] == 70.0
    assert parameters.slots["operating_pressure_bar"] == 6.0


def test_full_circulation_pump_duty_including_connection_is_extracted() -> None:
    result = IntentRouterAgent().route(
        "циркуляционный насос: Q=2 м³/ч, H=6 м, "
        "монтажная длина 180 мм, присоединение 25"
    )

    assert result.category == "pumps"
    assert result.slots["required_flow_m3_h"] == 2.0
    assert result.slots["required_head_m"] == 6.0
    assert result.slots["mounting_length_mm"] == 180
    assert result.slots["connection_size"] == 25


def test_natural_lift_height_wording_is_understood() -> None:
    result = IntentRouterAgent().route("скважинный насос, дом выше на 5 м")
    assert result.slots["lift_height_m"] == 5.0


def test_hyphenated_contours_and_closed_chamber_are_hard_filters() -> None:
    products = [
        _boiler("CLOSED-2", contours="Двухконтурный", chamber="Закрытая"),
        _boiler("OPEN-2", contours="Двухконтурный", chamber="Открытая"),
        _boiler("CLOSED-1", contours="Одноконтурный", chamber="Закрытая"),
    ]
    bot = ChatOrchestrator(products=products)

    first = bot.handle_chat(
        "gas-closed",
        "нужен 2-х контурный газовый котёл с закрытой камерой сгорания "
        "и дымоход к нему",
    )
    assert first.products == []
    assert first.debug["slots"]["contours"] == "двухконтурный"
    assert first.debug["slots"]["combustion_chamber"] == "закрытая"
    assert first.debug["slots"]["needs_chimney"] is True

    second = bot.handle_chat("gas-closed", "120 м²")
    assert [card.sku for card in second.products] == ["CLOSED-2"]
    assert "OPEN-2" not in second.answer
    assert "CLOSED-1" not in second.answer
    assert "Дымоход зафиксировал" in second.answer


def test_only_from_stock_is_understood_for_exact_boiler_power() -> None:
    products = [
        _boiler(
            "E6-STOCK",
            contours="Одноконтурный",
            chamber="Закрытая",
            power=6,
            qty=2,
            boiler_type="Электрический",
        ),
        _boiler(
            "E6-ZERO",
            contours="Одноконтурный",
            chamber="Закрытая",
            power=6,
            qty=0,
            boiler_type="Электрический",
        ),
        _boiler(
            "E9-STOCK",
            contours="Одноконтурный",
            chamber="Закрытая",
            power=9,
            qty=3,
            boiler_type="Электрический",
        ),
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "boiler-stock-phrase",
        "нужен электрический котёл 6 кВт только из наличия",
    )

    assert response.debug["slots"]["in_stock"] is True
    assert [card.sku for card in response.products] == ["E6-STOCK"]


def test_complex_heating_context_keeps_hot_water_and_warm_floor_negation() -> None:
    products = [
        _boiler("GAS-2", contours="Двухконтурный", chamber="Закрытая", qty=2),
        _product(
            "RANDOM-FITTING",
            "Ниппель латунный",
            "Фитинги",
            price=50,
            attrs={"тип товара": "Ниппель"},
        ),
    ]
    bot = ChatOrchestrator(products=products)

    first = bot.handle_chat(
        "heating-context",
        "Дом 140 м², два этажа, газ есть, нужна ГВС, "
        "только радиаторы без тёплого пола.",
    )

    assert first.debug["category"] == "boilers"
    assert first.debug["slots"]["area_m2"] == 140.0
    assert first.debug["slots"]["floors"] == 2
    assert first.debug["slots"]["needs_hot_water"] is True
    assert first.debug["slots"]["contours"] == "двухконтурный"
    assert first.debug["slots"]["has_warm_floor"] is False
    assert first.debug["slots"]["system_type"] == "радиаторы"
    assert first.debug["slots"]["project_scope"] == "heating"

    second = bot.handle_chat(
        "heating-context",
        "Будут только радиаторы, тёплого пола не будет.",
    )
    assert second.debug["category"] == "boilers"
    assert second.debug["slots"]["has_warm_floor"] is False
    assert second.debug["slots"]["system_type"] == "радиаторы"
    assert second.debug["slots"]["project_scope"] == "heating"

    third = bot.handle_chat("heating-context", "Нужна также ГВС")
    assert third.debug["category"] == "boilers"
    assert third.debug["slots"]["needs_hot_water"] is True
    assert third.debug["slots"]["contours"] == "двухконтурный"
    assert all(card.sku != "RANDOM-FITTING" for card in third.products)


def test_hydraulic_accumulator_term_explanation_rejects_fictitious_types() -> None:
    bot = ChatOrchestrator(products=[])
    response = bot.handle_chat("hydraulic-term", "что такое гидроаккумулятор?")

    assert "мембранный бак" in response.answer.lower()
    assert "не делят на «паровой и водяной»" in response.answer.lower()
