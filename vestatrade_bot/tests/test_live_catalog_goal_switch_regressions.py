"""Regressions for catalogue refinements and returning to an earlier goal.

These are deliberately multi-turn tests.  Single-turn routing can parse every
individual phrase while the persistent session still keeps an incompatible
constraint from the product shown one turn earlier.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.engineering_interpreter import EngineeringInterpretation
from app.agents.utils import normalize_text
from app.models import (
    IntentResult,
    Product,
    ProductBranchState,
    ProductSelectionSnapshot,
)


def _product(
    sku: str,
    name: str,
    category_path: str,
    attributes: dict[str, str],
    *,
    brand: str = "TEST",
    price: float = 1_000,
    stock_qty: int = 5,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category_path,
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=price,
        currency="RUB",
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized=attributes,
    )


@pytest.fixture
def catalog_bot() -> ChatOrchestrator:
    products = [
        _product(
            "VALVE-BASE-FF",
            'Кран шаровой BASE 1/2" ВР/ВР со стальной рукояткой',
            "Краны шаровые",
            {
                "тип товара": "Кран шаровой",
                "серия": "BASE",
                "диаметр подключения, дюйм": "1/2",
                "тип резьбы": "Внутренняя/внутренняя",
                "тип ручки": "Стальная рукоятка",
                "назначение": "Вода, отопление",
            },
            brand="VALTEC",
        ),
        _product(
            "VALVE-BASE-FM",
            'Кран шаровой BASE 1/2" ВР/НР, ручка бабочка',
            "Краны шаровые",
            {
                "тип товара": "Кран шаровой",
                "серия": "BASE",
                "диаметр подключения, дюйм": "1/2",
                "тип резьбы": "Внутренняя/наружная",
                "тип ручки": "Бабочка",
                "назначение": "Вода, отопление",
            },
            brand="VALTEC",
            price=800,
        ),
        _product(
            "PUMP-25-6-180",
            "Насос циркуляционный 25/6 180 мм",
            "Насосы циркуляционные",
            {
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "максимальный напор, м": "6",
                "монтажная длина, мм": "180",
                "потребляемая мощность, Вт": "93",
            },
        ),
        _product(
            "PUMP-25-6-130",
            "Насос циркуляционный 25/6 130 мм",
            "Насосы циркуляционные",
            {
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "максимальный напор, м": "6",
                "монтажная длина, мм": "130",
            },
            price=900,
        ),
        _product(
            "PIPE-PPR-GF-20",
            "Труба PPR 20 мм армированная стекловолокном для отопления",
            "Трубы полипропиленовые",
            {
                "тип товара": "Труба",
                "материал": "PPR",
                "диаметр, мм": "20",
                "армирование": "Стекловолокно",
                "назначение": "Отопление",
                "максимальная рабочая температура": "95 C",
                "максимальное рабочее давление": "10 бар",
            },
        ),
        _product(
            "PIPE-PPR-AL-20",
            "Труба PPR 20 мм армированная алюминием для отопления",
            "Трубы полипропиленовые",
            {
                "тип товара": "Труба",
                "материал": "PPR",
                "диаметр, мм": "20",
                "армирование": "Алюминий",
                "назначение": "Отопление",
                "максимальная рабочая температура": "95 C",
                "максимальное рабочее давление": "10 бар",
            },
            price=1_100,
        ),
        _product(
            "SEWER-PIPE-50-500",
            "Труба канализационная внутренняя 50x500",
            "Внутренняя канализация",
            {
                "тип товара": "Труба",
                "диаметр, мм": "50",
                "длина, мм": "500",
            },
        ),
        _product(
            "SEWER-BEND-50-87",
            "Отвод канализационный внутренний 50 мм 87 градусов",
            "Внутренняя канализация",
            {
                "тип товара": "Отвод",
                "диаметр, мм": "50",
                "угол": "87°",
            },
            price=200,
        ),
        _product(
            "BOILER-GAS-9",
            "Газовый одноконтурный котёл 9 кВт с закрытой камерой",
            "Котлы газовые",
            {
                "тип товара": "Котёл",
                "тип котла": "Газовый",
                "мощность, кВт": "9",
                "количество контуров": "Одноконтурный",
                "камера сгорания": "Закрытая",
                "дымоход": "Коаксиальный",
            },
        ),
        _product(
            "BOILER-ELECTRIC-9",
            "Электрический одноконтурный котёл 9 кВт 380 В",
            "Котлы электрические",
            {
                "тип товара": "Котёл",
                "тип котла": "Электрический",
                "мощность, кВт": "9",
                "количество контуров": "Одноконтурный",
                "напряжение, В": "380",
            },
            price=1_200,
        ),
        _product(
            "THERMO-M30",
            "Термостатическая головка жидкостная M30x1,5",
            "Радиаторная арматура",
            {
                "тип товара": "Термостатическая головка",
                "диапазон регулирования температуры": "6,5–28 °C",
                "присоединительная резьба": "M30x1,5",
            },
            brand="VALTEC",
            price=1_044,
        ),
        _product(
            "RAD-BIMETAL-500-6",
            "Радиатор биметаллический 500 мм 6 секций",
            "Радиаторы биметаллические",
            {
                "тип товара": "Радиатор биметаллический",
                "тип радиатора": "Биметаллический",
                "межосевое расстояние, мм": "500",
                "количество секций": "6",
            },
            brand="ROMMER",
            price=3_050,
            stock_qty=0,
        ),
        _product(
            "RAD-ALUMINIUM-500-6",
            "Радиатор алюминиевый 500 мм 6 секций",
            "Радиаторы алюминиевые",
            {
                "тип товара": "Радиатор алюминиевый",
                "тип радиатора": "Алюминиевый",
                "межосевое расстояние, мм": "500",
                "количество секций": "6",
            },
            brand="ROMMER",
            price=2_850,
            stock_qty=0,
        ),
        _product(
            "RAD-ALUMINIUM-500-6-IN",
            "Радиатор алюминиевый 500 мм 6 секций складская позиция",
            "Радиаторы алюминиевые",
            {
                "тип товара": "Радиатор алюминиевый",
                "тип радиатора": "Алюминиевый",
                "межосевое расстояние, мм": "500",
                "количество секций": "6",
            },
            brand="ROMMER",
            price=2_950,
            stock_qty=5,
        ),
    ]
    return ChatOrchestrator(products=products)


def test_exact_valve_traits_open_product_without_application_clarification(
    catalog_bot: ChatOrchestrator,
) -> None:
    response = catalog_bot.handle_chat(
        "catalog-exact-valve-traits",
        'Нужен шаровой кран BASE со стальной рукояткой, 1/2", ВР/ВР',
    )

    assert [product.sku for product in response.products] == ["VALVE-BASE-FF"]
    assert "для чего нужен кран" not in normalize_text(response.answer)
    assert catalog_bot.sessions.get(
        "catalog-exact-valve-traits"
    ).pending_question_state is None


def test_pump_mounting_length_can_change_and_return_to_180(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-pump-180-130-180"

    first = catalog_bot.handle_chat(
        session_id,
        "Покажи циркуляционный насос 25/6 180 мм",
    )
    shorter = catalog_bot.handle_chat(
        session_id,
        "А теперь такой же, но 130 мм",
    )
    restored = catalog_bot.handle_chat(
        session_id,
        "Вернёмся к насосу 180 мм",
    )

    assert [product.sku for product in first.products] == ["PUMP-25-6-180"]
    assert [product.sku for product in shorter.products] == ["PUMP-25-6-130"]
    assert shorter.debug["slots"]["mounting_length_mm"] == 130
    assert [product.sku for product in restored.products] == ["PUMP-25-6-180"]
    assert restored.debug["slots"]["mounting_length_mm"] == 180
    assert restored.debug["slots"]["connection_size"] == 25
    assert restored.debug["slots"]["head_m"] == 6


def test_return_to_pump_restores_pump_goal_without_valve_slots(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-pump-valve-pump"

    pump = catalog_bot.handle_chat(
        session_id,
        "Покажи циркуляционный насос 25/6 180 мм",
    )
    valve = catalog_bot.handle_chat(
        session_id,
        'Теперь нужен шаровой кран BASE 1/2" ВР/ВР для воды',
    )
    restored = catalog_bot.handle_chat(session_id, "Вернёмся к насосу")

    assert [product.sku for product in pump.products] == ["PUMP-25-6-180"]
    # Exact valve filtering is covered separately above.  This scenario is
    # about isolating two catalogue goals and restoring the pump afterwards.
    assert valve.products
    assert valve.products[0].sku == "VALVE-BASE-FF"
    assert [product.sku for product in restored.products] == ["PUMP-25-6-180"]
    assert restored.debug["category"] == "pumps"
    assert restored.debug["slots"]["pump_type"] == "циркуляционный"
    assert restored.debug["slots"]["mounting_length_mm"] == 180
    assert "valve_kind" not in restored.debug["slots"]
    assert "thread_type" not in restored.debug["slots"]
    assert restored.debug["project_context"]["active_category"] == "pumps"
    assert restored.debug["project_context"]["active_goal"].startswith("pumps")


def test_pipe_reinforcement_switch_replaces_glass_fibre_with_aluminium(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-pipe-reinforcement-switch"
    first = catalog_bot.handle_chat(
        session_id,
        (
            "Покажи трубу PPR 20 мм, армированную стекловолокном, "
            "для отопления, радиаторная разводка, температура 80 C, "
            "давление 6 бар"
        ),
    )
    aluminium = catalog_bot.handle_chat(
        session_id,
        "Теперь такую же трубу, но армированную алюминием",
    )

    assert [product.sku for product in first.products] == ["PIPE-PPR-GF-20"]
    assert first.debug["slots"]["reinforcement"] == "стекловолокно"
    assert [product.sku for product in aluminium.products] == ["PIPE-PPR-AL-20"]
    assert aluminium.debug["slots"]["reinforcement"] == "алюминий"
    assert aluminium.debug["slots"]["diameter_mm"] == 20
    assert "стекловолокно" not in normalize_text(aluminium.answer)


def test_switching_sewer_pipe_to_bend_clears_pipe_only_length(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-sewer-pipe-to-bend"

    pipe = catalog_bot.handle_chat(
        session_id,
        "Нужна труба для внутренней канализации 50 мм длиной 500 мм",
    )
    bend = catalog_bot.handle_chat(
        session_id,
        "Теперь отвод 50 мм 87 градусов",
    )

    assert [product.sku for product in pipe.products] == ["SEWER-PIPE-50-500"]
    assert [product.sku for product in bend.products] == ["SEWER-BEND-50-87"]
    assert bend.debug["slots"]["element_type"] == "отвод"
    assert bend.debug["slots"]["angle_deg"] == 87
    assert "length_mm" not in bend.debug["slots"]


def test_sewer_pipe_bend_pipe_correction_clears_bend_angle(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-sewer-pipe-bend-pipe"

    catalog_bot.handle_chat(
        session_id,
        "Нужна труба для внутренней канализации 50 мм длиной 500 мм",
    )
    catalog_bot.handle_chat(
        session_id,
        "Теперь отвод 50 мм 87 градусов",
    )
    corrected = catalog_bot.handle_chat(session_id, "Трубу, не отвод")

    assert [product.sku for product in corrected.products] == [
        "SEWER-PIPE-50-500"
    ]
    assert corrected.debug["slots"]["element_type"] == "труба"
    assert corrected.debug["slots"]["length_mm"] == 500
    assert "angle_deg" not in corrected.debug["slots"]
    assert "SEWER-BEND-50-87" not in corrected.answer


def test_switching_gas_boiler_to_electric_clears_gas_only_constraints(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-gas-to-electric-boiler"

    gas = catalog_bot.handle_chat(
        session_id,
        (
            "Нужен газовый одноконтурный котёл 9 кВт "
            "с закрытой камерой и коаксиальным дымоходом"
        ),
    )
    electric = catalog_bot.handle_chat(
        session_id,
        "Нет, теперь электрический котёл 9 кВт 380 В",
    )

    assert [product.sku for product in gas.products] == ["BOILER-GAS-9"]
    assert gas.debug["slots"]["boiler_type"] == "газовый"
    assert gas.debug["slots"]["needs_chimney"] is True

    assert [product.sku for product in electric.products] == [
        "BOILER-ELECTRIC-9"
    ]
    assert electric.debug["slots"]["boiler_type"] == "электрический"
    assert electric.debug["slots"]["voltage_v"] == 380
    assert "boiler_types" not in electric.debug["slots"]
    for gas_only_key in {
        "combustion_chamber",
        "needs_chimney",
        "chimney_type",
        "chimney_size",
        "gas_type",
        "has_gas",
    }:
        assert gas_only_key not in electric.debug["slots"]
    assert "газ" not in normalize_text(
        str(electric.debug["slots"].get("heat_sources") or "")
    )


def test_followup_returns_every_requested_price_and_stock_field(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-requested-price-stock"
    catalog_bot.handle_chat(session_id, "Покажи артикул THERMO-M30")

    response = catalog_bot.handle_chat(
        session_id,
        "Сколько она стоит и сколько сейчас в наличии?",
    )

    answer = normalize_text(response.answer)
    assert "1044" in answer
    assert "5 шт" in answer
    assert [product.sku for product in response.products] == ["THERMO-M30"]


def test_followup_reads_requested_attributes_from_full_grounded_product(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-requested-thermostat-attributes"
    catalog_bot.handle_chat(session_id, "Покажи артикул THERMO-M30")

    response = catalog_bot.handle_chat(
        session_id,
        "Какой у неё диапазон температуры и какая резьба?",
    )

    answer = normalize_text(response.answer)
    assert "6,5" in answer and "28" in answer
    assert "m30x1,5" in answer
    assert [product.sku for product in response.products] == ["THERMO-M30"]


def test_followup_reads_pump_head_and_power_from_full_product(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-requested-pump-attributes"
    catalog_bot.handle_chat(session_id, "Покажи артикул PUMP-25-6-180")

    response = catalog_bot.handle_chat(
        session_id,
        "Какой у него напор и мощность?",
    )

    answer = normalize_text(response.answer)
    assert "6" in answer and "напор" in answer
    assert "93" in answer and "мощност" in answer
    assert [product.sku for product in response.products] == ["PUMP-25-6-180"]


def test_everyday_radiator_knob_language_leads_to_thermostatic_head(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-everyday-thermostatic-head"

    first = catalog_bot.handle_chat(
        session_id,
        "На батарее хочу крутилку, чтобы сама держала температуру в комнате",
    )
    selected = catalog_bot.handle_chat(
        session_id,
        "Для клапана Valtec, резьба M30x1,5",
    )
    facts = catalog_bot.handle_chat(
        session_id,
        "Какая температура регулируется и сколько стоит?",
    )

    assert "тип радиатора" not in normalize_text(first.answer)
    assert "резьб" in normalize_text(first.answer)
    assert [product.sku for product in selected.products] == ["THERMO-M30"]
    facts_text = normalize_text(facts.answer)
    assert "6,5" in facts_text and "28" in facts_text
    assert "1044" in facts_text


def test_radiator_type_correction_and_explicit_stock_relaxation_do_not_mix(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-radiator-type-stock-relaxation"

    bimetal = catalog_bot.handle_chat(
        session_id,
        (
            "Нужен биметаллический радиатор ROMMER, межосевое 500 мм, "
            "6 секций, только в наличии"
        ),
    )
    aluminium = catalog_bot.handle_chat(session_id, "А алюминиевый?")
    unavailable = catalog_bot.handle_chat(
        session_id,
        "Покажи даже если сейчас нет в наличии",
    )

    assert not bimetal.products
    assert bimetal.debug["slots"]["radiator_type"] == "биметаллический"
    assert bimetal.debug["slots"]["in_stock"] is True
    assert [product.sku for product in aluminium.products] == [
        "RAD-ALUMINIUM-500-6-IN"
    ]
    assert aluminium.debug["slots"]["radiator_type"] == "алюминиевый"
    assert aluminium.debug["slots"]["in_stock"] is True
    assert {product.sku for product in unavailable.products} == {
        "RAD-ALUMINIUM-500-6",
        "RAD-ALUMINIUM-500-6-IN",
    }
    assert unavailable.debug["slots"]["radiator_type"] == "алюминиевый"
    assert unavailable.debug["slots"]["in_stock"] is False
    assert "RAD-BIMETAL-500-6" not in unavailable.answer


def test_product_snapshot_keeps_complete_effective_selection_constraints(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-complete-selection-snapshot"

    for message in [
        "Нужен циркуляционный насос для отопления",
        "Монтажная длина 180 мм",
        "Напор 6 метров",
        "Присоединение 25",
    ]:
        response = catalog_bot.handle_chat(session_id, message)

    assert [product.sku for product in response.products] == ["PUMP-25-6-180"]
    snapshot = catalog_bot.sessions.get(session_id).product_branches[
        "pumps"
    ].selections[-1]
    assert snapshot.product_skus == ["PUMP-25-6-180"]
    assert snapshot.constraints["pump_type"] == "циркуляционный"
    assert snapshot.constraints["mounting_length_mm"] == 180
    assert snapshot.constraints["head_m"] == 6
    assert snapshot.constraints["connection_size"] == 25


def test_price_question_about_first_shown_card_returns_only_first_card(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-first-card-price"
    shown = catalog_bot.handle_chat(
        session_id,
        'Покажи шаровые краны BASE 1/2" для воды',
    )
    response = catalog_bot.handle_chat(session_id, "Сколько стоит первый?")

    assert len(shown.products) >= 2
    assert [product.sku for product in response.products] == [shown.products[0].sku]
    assert f"{shown.products[0].price:g}" in response.answer
    for other in shown.products[1:]:
        if other.price != shown.products[0].price:
            assert f"{other.price:g}" not in response.answer


def test_quantity_digit_is_not_misread_as_product_ordinal(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-quantity-not-ordinal"
    shown = catalog_bot.handle_chat(
        session_id,
        'Покажи шаровые краны BASE 1/2" для воды',
    )
    response = catalog_bot.handle_chat(session_id, "Сколько стоят 2 штуки?")

    assert len(shown.products) >= 2
    assert len(response.products) >= 2
    assert {product.sku for product in response.products} == {
        product.sku for product in shown.products
    }


def test_plain_radiator_type_without_size_still_requires_size(
    catalog_bot: ChatOrchestrator,
) -> None:
    response = catalog_bot.handle_chat(
        "catalog-radiator-type-needs-size",
        "Нужен алюминиевый радиатор",
    )

    assert not response.products
    assert response.need_handoff is False
    assert "размер" in normalize_text(response.answer)


def test_aluminium_radiator_context_does_not_become_pipe_reinforcement(
    catalog_bot: ChatOrchestrator,
) -> None:
    response = catalog_bot.intent_router.route(
        "Нужна PPR труба 20 мм к алюминиевому радиатору",
        None,
    )

    assert "reinforcement" not in response.slots


def test_return_clears_pending_complectation_from_the_branch_being_left(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-return-clears-complectation"
    catalog_bot.handle_chat(
        session_id,
        "Покажи циркуляционный насос 25/6 180 мм",
    )
    catalog_bot.handle_chat(
        session_id,
        'Теперь нужны шаровые краны BASE 1/2" для воды',
    )
    pending = catalog_bot.handle_chat(session_id, "Что входит в комплект?")
    restored = catalog_bot.handle_chat(session_id, "Вернёмся к насосу")

    assert "по какой из показанных" in normalize_text(pending.answer)
    assert [product.sku for product in restored.products] == ["PUMP-25-6-180"]
    assert catalog_bot.sessions.get(session_id).pending_complectation_parts == []
    assert "паспорта" not in normalize_text(restored.answer)


def test_colloquial_price_question_has_deterministic_fallback_without_llm(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-colloquial-price"
    catalog_bot.handle_chat(session_id, "Покажи артикул BOILER-GAS-9")
    response = catalog_bot.handle_chat(session_id, "Почём он?")

    assert [product.sku for product in response.products] == ["BOILER-GAS-9"]
    assert "1000" in normalize_text(response.answer)
    assert "газовый или электрический" not in normalize_text(response.answer)


def test_explicit_rule_category_wins_over_hallucinated_llm_return_target(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-explicit-return-category-priority"
    catalog_bot.handle_chat(
        session_id,
        "Покажи циркуляционный насос 25/6 180 мм",
    )
    catalog_bot.handle_chat(
        session_id,
        'Теперь нужен шаровой кран BASE 1/2" ВР/ВР для воды',
    )
    session = catalog_bot.sessions.get(session_id)
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        confidence=0.9,
        is_topic_change=True,
    )
    interpretation = EngineeringInterpretation(
        handled=True,
        output_accepted=True,
        dialog_act="return",
        target_category="valves",
    )

    catalog_bot._overlay_engineering_interpretation(
        "Вернёмся к насосу",
        intent,
        interpretation,
        session,
    )

    assert intent.category == "pumps"
    assert catalog_bot._referenced_product_category(
        "Вернёмся к насосу",
        intent,
        session,
    ) == "pumps"


def test_qualified_return_filters_cards_inside_one_remembered_result_set(
    catalog_bot: ChatOrchestrator,
) -> None:
    session = catalog_bot.sessions.get("catalog-qualified-card-return")
    session.category = "valves"
    session.product_branches["pumps"] = ProductBranchState(
        selections=[
            ProductSelectionSnapshot(
                category="pumps",
                product_skus=["PUMP-25-6-180", "PUMP-25-6-130"],
                constraints={
                    "pump_type": "циркуляционный",
                    "head_m": 6,
                    "connection_size": 25,
                },
            )
        ]
    )
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        confidence=0.95,
        slots={"mounting_length_mm": 180},
    )

    restored = catalog_bot._restore_product_reference(
        "Вернёмся к насосу 180 мм",
        intent,
        session,
    )

    assert restored is True
    assert [card.sku for card in session.last_products] == ["PUMP-25-6-180"]


def test_ordinal_return_selects_card_not_historical_snapshot(
    catalog_bot: ChatOrchestrator,
) -> None:
    session = catalog_bot.sessions.get("catalog-ordinal-card-return")
    session.category = "valves"
    session.product_branches["pumps"] = ProductBranchState(
        selections=[
            ProductSelectionSnapshot(
                category="pumps",
                product_skus=["PUMP-25-6-180", "PUMP-25-6-130"],
                constraints={
                    "pump_type": "циркуляционный",
                    "head_m": 6,
                    "connection_size": 25,
                },
            )
        ]
    )
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        confidence=0.95,
    )

    restored = catalog_bot._restore_product_reference(
        "Вернёмся к первому насосу",
        intent,
        session,
    )

    assert restored is True
    assert [card.sku for card in session.last_products] == ["PUMP-25-6-180"]


def test_missing_requested_card_field_is_reported_explicitly(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-missing-card-field"
    catalog_bot.handle_chat(session_id, "Покажи артикул PUMP-25-6-180")
    response = catalog_bot.handle_chat(session_id, "Какой у него диаметр?")

    assert [product.sku for product in response.products] == ["PUMP-25-6-180"]
    answer = normalize_text(response.answer)
    assert "диаметр" in answer
    assert "не указано" in answer


def test_explicit_boiler_type_beats_a_separate_available_energy_source(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-explicit-boiler-type-with-gas-available"
    catalog_bot.handle_chat(
        session_id,
        "Нужен газовый одноконтурный котёл 9 кВт с закрытой камерой",
    )
    response = catalog_bot.handle_chat(
        session_id,
        "Теперь электрический котёл 9 кВт 380 В, но газ у дома тоже есть",
    )

    assert [product.sku for product in response.products] == [
        "BOILER-ELECTRIC-9"
    ]
    assert response.debug["slots"]["boiler_type"] == "электрический"
    assert response.debug["slots"]["has_gas"] is True
    assert response.debug["slots"]["has_electricity"] is True


def test_exact_sku_category_switch_does_not_leak_previous_product_slots(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-exact-sku-category-isolation"
    catalog_bot.handle_chat(session_id, "Покажи артикул PUMP-25-6-180")

    valve = catalog_bot.handle_chat(
        session_id,
        "Покажи артикул VALVE-BASE-FF",
    )
    session = catalog_bot.sessions.get(session_id)

    assert [product.sku for product in valve.products] == ["VALVE-BASE-FF"]
    for pump_only_key in {
        "pump_type",
        "head_m",
        "mounting_length_mm",
        "connection_size",
    }:
        assert pump_only_key not in valve.debug["slots"]
    valve_goal_id = session.project_context["category_last_goal"]["valves"]
    valve_goal_slots = session.project_context["goals"][valve_goal_id]["slots"]
    assert "connection_size" not in valve_goal_slots
    valve_snapshot = session.product_branches["valves"].selections[-1]
    assert "connection_size" not in valve_snapshot.constraints


def test_natural_stock_relaxation_removes_previous_strict_filter(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-natural-stock-relaxation"
    catalog_bot.handle_chat(
        session_id,
        (
            "Покажи алюминиевый радиатор ROMMER 500 мм 6 секций, "
            "только в наличии"
        ),
    )

    relaxed = catalog_bot.handle_chat(
        session_id,
        "Можно и те, которых сейчас нет",
    )

    assert relaxed.debug["slots"]["in_stock"] is False
    assert {product.sku for product in relaxed.products} == {
        "RAD-ALUMINIUM-500-6",
        "RAD-ALUMINIUM-500-6-IN",
    }


def test_requested_field_with_ordinal_reads_only_that_shown_card(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-ordinal-card-field"
    shown = catalog_bot.handle_chat(
        session_id,
        'Покажи шаровые краны BASE 1/2" для воды',
    )
    assert len(shown.products) == 2

    answer = catalog_bot.handle_chat(
        session_id,
        "Какая резьба у второго крана?",
    )

    assert [product.sku for product in answer.products] == [shown.products[1].sku]
    assert "резьб" in normalize_text(answer.answer)


def test_return_to_first_product_uses_card_ordinal_not_snapshot_ordinal(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-first-valve-return"
    shown = catalog_bot.handle_chat(
        session_id,
        'Покажи шаровые краны BASE 1/2" для воды',
    )
    catalog_bot.handle_chat(session_id, "Покажи артикул PUMP-25-6-180")

    returned = catalog_bot.handle_chat(
        session_id,
        "Вернёмся к первому крану",
    )

    assert [product.sku for product in returned.products] == [shown.products[0].sku]


def test_elliptical_boiler_type_correction_beats_site_capability_clause(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-elliptical-electric-boiler"
    catalog_bot.handle_chat(
        session_id,
        "Нужен газовый одноконтурный котёл 9 кВт с закрытой камерой",
    )

    electric = catalog_bot.handle_chat(
        session_id,
        "Теперь электрический 9 кВт 380 В, но газ у дома тоже есть",
    )

    assert [product.sku for product in electric.products] == ["BOILER-ELECTRIC-9"]
    assert electric.debug["slots"]["boiler_type"] == "электрический"
    assert electric.debug["slots"]["voltage_v"] == 380
    assert electric.debug["slots"]["has_gas"] is True
    assert "combustion_chamber" not in electric.debug["slots"]


def test_switching_electric_boiler_to_gas_clears_electrical_constraints(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-electric-to-gas-boiler"
    catalog_bot.handle_chat(
        session_id,
        "Нужен электрический одноконтурный котёл 9 кВт 380 В",
    )

    gas = catalog_bot.handle_chat(
        session_id,
        (
            "Теперь газовый котёл 9 кВт с закрытой камерой, "
            "но электричество дома тоже есть"
        ),
    )

    assert [product.sku for product in gas.products] == ["BOILER-GAS-9"]
    assert gas.debug["slots"]["boiler_type"] == "газовый"
    assert gas.debug["slots"]["has_electricity"] is True
    assert gas.debug["slots"]["has_gas"] is True
    for electrical_key in {"voltage_v", "phase_count", "current_type"}:
        assert electrical_key not in gas.debug["slots"]


@pytest.mark.parametrize(
    ("sku", "question", "stock_phrase"),
    [
        ("RAD-BIMETAL-500-6", "Его нет в наличии?", "нет в наличии"),
        ("THERMO-M30", "Он есть в наличии?", "в наличии 5"),
    ],
)
def test_pronoun_stock_question_reads_shown_card_without_persisting_filter(
    catalog_bot: ChatOrchestrator,
    sku: str,
    question: str,
    stock_phrase: str,
) -> None:
    session_id = f"catalog-pronoun-stock-{sku}"
    catalog_bot.handle_chat(session_id, f"Покажи артикул {sku}")

    stock = catalog_bot.handle_chat(session_id, question)
    session = catalog_bot.sessions.get(session_id)

    assert [product.sku for product in stock.products] == [sku]
    assert stock_phrase in normalize_text(stock.answer)
    assert "in_stock" not in session.slots


def test_semantic_product_question_cannot_override_explicit_refinement(
    catalog_bot: ChatOrchestrator,
) -> None:
    session_id = "catalog-semantic-product-question-precedence"
    catalog_bot.handle_chat(
        session_id,
        "Покажи артикул RAD-BIMETAL-500-6",
    )
    session = catalog_bot.sessions.get(session_id)
    intent = IntentResult(
        intent_type="attribute_request",
        category="radiators",
        confidence=0.9,
        slots={"radiator_type": "алюминиевый"},
    )
    interpretation = EngineeringInterpretation(
        handled=True,
        output_accepted=True,
        dialog_act="product_question",
        requested_fields=["characteristics"],
    )

    catalog_bot._overlay_engineering_interpretation(
        "А алюминиевый?",
        intent,
        interpretation,
        session,
    )
    intent.raw["current_turn_slot_keys"] = ["radiator_type"]

    assert intent.slots["radiator_type"] == "алюминиевый"
    assert catalog_bot._is_contextual_followup(
        "А алюминиевый?",
        intent,
        session,
    ) is False
