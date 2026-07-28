from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _pipe(
    sku: str,
    name: str,
    *,
    purpose: str,
    material: str,
    maximum_temperature_c: float,
    maximum_pressure_bar: float,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Трубы напорные",
        url=f"https://example.test/{sku.lower()}",
        price=200,
        stock_status="в наличии",
        stock_qty=10,
        attributes_normalized={
            "тип товара": "Труба",
            "назначение": purpose,
            "материал": material,
            "диаметр (мм)": "25",
            "максимальная рабочая температура, °с": str(maximum_temperature_c),
            "максимальное рабочее давление, бар": str(maximum_pressure_bar),
        },
    )


def _well_pump(
    sku: str,
    *,
    maximum_head_m: float,
    maximum_flow_l_min: float,
) -> Product:
    return Product(
        sku=sku,
        name=f"Насос скважинный {sku}",
        category_path="Насосы скважинные",
        url=f"https://example.test/{sku.lower()}",
        price=12000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "тип товара": "Насос",
            "тип насоса": "Скважинный",
            "максимальный напор, м": str(maximum_head_m),
            "производительность, л/мин": str(maximum_flow_l_min),
        },
    )


def test_heating_pipe_requires_service_temperature_pressure_and_material(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "pipe-requirements",
        "Нужна труба 25 мм для отопления",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "участ" in answer
    assert "температур" in answer
    assert "давлен" in answer
    assert response.debug["project_context"]["categories"]["pipes"]["diameter_mm"] == 25


def test_hot_water_pipe_is_not_selected_from_words_hot_water_and_diameter(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "hot-pipe-requirements",
        "Нужна труба 20 мм для горячей воды",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "участ" in answer
    assert "температур" in answer
    assert "давлен" in answer


def test_pipe_service_and_ratings_are_hard_catalog_filters() -> None:
    heating = _pipe(
        "HEAT-25",
        "Труба PPR армированная 25 мм для отопления",
        purpose="Отопление, горячее водоснабжение",
        material="PPR",
        maximum_temperature_c=95,
        maximum_pressure_bar=10,
    )
    well = _pipe(
        "WELL-25",
        "Труба напорная ПЭ100 25 мм для холодного водоснабжения",
        purpose="Холодное водоснабжение",
        material="ПЭ100",
        maximum_temperature_c=40,
        maximum_pressure_bar=16,
    )
    bot = ChatOrchestrator(products=[heating, well])

    response = bot.handle_chat(
        "pipe-hard-filter",
        (
            "Нужна труба PPR 25 мм для радиаторной разводки отопления, "
            "максимальная температура 80 °C, рабочее давление 6 бар"
        ),
    )

    assert [product.sku for product in response.products] == ["HEAT-25"]
    assert "WELL-25" not in response.answer
    assert response.debug["slots"]["pipe_service"] == "радиаторная разводка"


def test_circulation_pump_for_new_system_asks_for_duty_point(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "pump-duty",
        "Нужен циркуляционный насос для отопления дома 140 м², два этажа",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "расход" in answer
    assert "напор" in answer
    assert "замена" in answer
    assert response.debug["slots"]["floors"] == 2


def test_explicit_circulation_pump_marking_remains_actionable(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "pump-explicit-marking",
        "Насос для отопления 25/6 180",
    )

    assert response.products
    assert response.products[0].sku == "PUMP-25-60"
    assert response.debug["slots"]["pump_selection_mode"] == "по заданным параметрам"


def test_new_circulation_pump_requires_system_type_after_numeric_parameters(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "pump-system-type",
        (
            "Новый циркуляционный насос 25/6 180, "
            "расчётный расход 2 м3/ч"
        ),
    )

    assert response.products == []
    assert "схема системы" in response.answer.lower()
    assert "радиатор" in response.answer.lower()


def test_well_depth_alone_is_not_enough_for_pump_selection() -> None:
    bot = ChatOrchestrator(
        products=[_well_pump("WELL-PUMP", maximum_head_m=60, maximum_flow_l_min=50)]
    )

    response = bot.handle_chat(
        "well-requirements",
        "Нужен насос для скважины глубиной 60 м",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "глубины скважины недостаточно" in answer
    assert "динамическ" in answer
    assert "высот" in answer


def test_well_pump_components_without_calculated_head_do_not_unlock_products() -> None:
    bot = ChatOrchestrator(
        products=[_well_pump("WELL-PUMP", maximum_head_m=60, maximum_flow_l_min=50)]
    )

    response = bot.handle_chat(
        "well-missing-head",
        (
            "Скважинный насос: динамический уровень 20 м, высота подъёма 5 м, "
            "горизонтальная трасса 30 м, давление 3 бар, расход 2 м3/ч"
        ),
    )

    assert response.products == []
    assert "расчётный напор" in response.answer.lower()
    assert "потерь" in response.answer.lower()


def test_well_pump_flow_and_head_are_hard_filters() -> None:
    weak = _well_pump("WELL-WEAK", maximum_head_m=25, maximum_flow_l_min=20)
    suitable = _well_pump("WELL-OK", maximum_head_m=60, maximum_flow_l_min=50)
    bot = ChatOrchestrator(products=[weak, suitable])

    response = bot.handle_chat(
        "well-duty-filter",
        (
            "Скважинный насос: глубина скважины 60 м, динамический уровень 20 м, "
            "высота подъёма 5 м, горизонтальная трасса 30 м, нужно давление 3 бар, "
            "расход 2 м3/ч, расчётный напор 45 м"
        ),
    )

    assert [product.sku for product in response.products] == ["WELL-OK"]
    assert "WELL-WEAK" not in response.answer
    assert "насосной кривой" in response.answer.lower()


def test_booster_pressure_delta_and_flow_are_hard_filters() -> None:
    weak = Product(
        sku="BOOST-WEAK",
        name="Насос повысительный BOOST-WEAK",
        category_path="Насосы повысительные",
        url="https://example.test/boost-weak",
        price=5000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "тип товара": "Повысительный насос",
            "максимальный напор, м": "15",
            "производительность, л/мин": "30",
        },
    )
    suitable = weak.model_copy(
        update={
            "sku": "BOOST-OK",
            "name": "Насос повысительный BOOST-OK",
            "url": "https://example.test/boost-ok",
            "attributes_normalized": {
                "тип товара": "Повысительный насос",
                "максимальный напор, м": "35",
                "производительность, л/мин": "50",
            },
        }
    )
    bot = ChatOrchestrator(products=[weak, suitable])

    response = bot.handle_chat(
        "booster-duty-filter",
        (
            "Повысительный насос: давление на входе 1 бар, нужно 3 бар, "
            "расход 2 м3/ч, центральный водопровод, подключение 25"
        ),
    )

    assert [product.sku for product in response.products] == ["BOOST-OK"]
    assert "BOOST-WEAK" not in response.answer


def test_structured_context_survives_category_switch_and_can_be_restored() -> None:
    heating = _pipe(
        "HEAT-25",
        "Труба PPR армированная 25 мм для отопления",
        purpose="Отопление",
        material="PPR",
        maximum_temperature_c=95,
        maximum_pressure_bar=10,
    )
    bot = ChatOrchestrator(products=[heating])
    first = bot.handle_chat(
        "structured-memory",
        (
            "Труба PPR 25 мм для радиаторной разводки отопления, "
            "температура 80 °C, давление 6 бар"
        ),
    )
    assert first.products

    bot.handle_chat("structured-memory", "Теперь нужен насос")
    restored = bot.handle_chat(
        "structured-memory",
        "Вернёмся к прежней трубе",
    )

    assert restored.debug["category"] == "pipes"
    assert restored.debug["slots"]["pipe_service"] == "радиаторная разводка"
    assert restored.debug["slots"]["operating_temperature_c"] == 80
    assert restored.products
    assert restored.products[0].sku == "HEAT-25"
