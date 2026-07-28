"""Regressions for passport evidence and cross-product compatibility context."""

from __future__ import annotations

import re

from app.agents.guardrails import GuardrailsAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.config import PROJECT_ROOT
from app.docs_loader import load_docs_for_products
from app.models import Product


def _e12() -> Product:
    return Product(
        sku="2202211",
        name="Котел электрический Arderia E12, 12 кВт",
        category_path="Котельное оборудование",
        brand="Arderia",
        url="https://example.test/arderia-e12",
        price=36534,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "2202211",
            "мощность, кВт": "12",
            "тип котла": "Электрический",
        },
        description=(
            "Конструкция содержит полный набор встроенных элементов. "
            "Встроенный циркуляционный насос с тремя скоростями и расширительный бак 6 л. "
            "Полный комплект гидравлической безопасности: предохранительный клапан, "
            "воздухоотводчик, манометр. Подключение: 3-фазное 380 В. "
            'Патрубки отопления G 3/4", подпитка G 1/2". '
            "Возможна работа с бойлером через трёхходовой клапан и датчик температуры "
            "(опции, приобретаются отдельно)."
        ),
    )


def _vrs(sku: str, name: str) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Насосное оборудование",
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=4186,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "артикул": sku,
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "максимальный напор, м": "6",
            "монтажная длина, мм": "180",
        },
        description="Циркуляционный насос для систем отопления.",
    )


def _classic_vrs() -> Product:
    return _vrs(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180 с гайками",
    )


def _has_one_and_half(value: object) -> bool:
    text = normalize_text(str(value)).replace("½", " 1/2")
    return bool(re.search(r"\b1\s*1\s*/\s*2\b", text))


def test_e12_confirms_pump_and_hydraulic_safety_without_optional_valve() -> None:
    product = _e12()
    guardrails = GuardrailsAgent()

    components = guardrails.list_builtin_components(product)

    assert "циркуляционный насос" in components
    assert "расширительный бак" in components
    assert any("безопас" in normalize_text(component) for component in components)
    assert all("3-ход" not in component and "трехход" not in normalize_text(component) for component in components)

    bot = ChatOrchestrator(products=[product])
    bot.handle_chat("e12-hydraulic-safety", "2202211")
    response = bot.handle_chat(
        "e12-hydraulic-safety",
        "Есть ли в этом котле встроенный насос, расширительный бак и группа безопасности?",
    )
    answer = normalize_text(response.answer)

    assert response.need_handoff is False
    assert "насос" in answer
    assert "бак" in answer
    assert "безопас" in answer
    assert "не вижу подтверждения" not in answer
    assert "3-ход" not in answer and "трехход" not in answer


def test_e12_followup_says_optional_three_way_valve_is_not_built_in() -> None:
    bot = ChatOrchestrator(products=[_e12()])
    bot.handle_chat("e12-optional-valve", "Покажи котёл 2202211")

    response = bot.handle_chat(
        "e12-optional-valve",
        "Трёхходовой клапан уже внутри?",
    )
    answer = normalize_text(response.answer)

    assert response.need_handoff is False
    assert [product.sku for product in response.products] == ["2202211"]
    assert answer.startswith("нет ")
    assert "3-ходовой клапан" in answer
    assert "приобрета" in answer or "не встро" in answer


def test_vrs_passport_scope_and_connection_specs_are_exact() -> None:
    classic = _classic_vrs()
    electronic = _vrs(
        "VRS.256EA.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180 EA с гайками",
    )
    booster = _vrs(
        "VRS.129G.15.0",
        "Насос повышения давления VALTEC VRS12/9G",
    )

    load_docs_for_products(
        [classic, electronic, booster],
        PROJECT_ROOT / "data",
    )

    assert classic.docs_text
    assert electronic.docs_text is None
    assert booster.docs_text is None

    normalized_attrs = {
        normalize_text(key): normalize_text(value)
        for key, value in classic.attributes_normalized.items()
    }
    dn_values = [
        value
        for key, value in normalized_attrs.items()
        if key == "dn" or ("диаметр" in key and "проход" in key)
    ]
    connection_values = [
        value
        for key, value in normalized_attrs.items()
        if "присоедин" in key or "резьб" in key
    ]
    assert any(re.search(r"\b25\b", value) for value in dn_values)
    assert any(_has_one_and_half(value) for value in connection_values)


def test_vrs_tabular_passport_package_is_parsed() -> None:
    product = _classic_vrs()
    bot = ChatOrchestrator(products=[product])
    bot.handle_chat("vrs-package-table", "VRS.256.18.0")

    response = bot.handle_chat(
        "vrs-package-table",
        "Что входит в комплект поставки по паспорту?",
    )
    answer = normalize_text(response.answer)

    assert "насос с клеммной коробкой" in answer
    assert "присоединительные гайки" in answer
    assert "прокладки" in answer
    assert "технический паспорт" in answer
    assert "упаковка" in answer
    assert "не нахожу явного перечня" not in answer


def test_vrs_connection_followup_answers_from_attached_passport() -> None:
    product = _classic_vrs()
    bot = ChatOrchestrator(products=[product])
    bot.handle_chat("vrs-connection", "VRS.256.18.0")

    response = bot.handle_chat(
        "vrs-connection",
        "Какое у него присоединение?",
    )
    answer = normalize_text(response.answer)

    assert re.search(r"\bdn\s*25\b|\bdn25\b", answer)
    assert _has_one_and_half(answer)


def test_cross_sku_compatibility_keeps_pump_context_without_claiming_fit() -> None:
    pump = _classic_vrs()
    boiler = _e12()
    bot = ChatOrchestrator(products=[pump, boiler])
    session_id = "vrs-e12-compatibility"
    bot.handle_chat(session_id, "VRS.256.18.0")

    response = bot.handle_chat(
        session_id,
        "Совместим ли он с котлом 2202211?",
    )
    answer = normalize_text(response.answer)
    session = bot.sessions.get(session_id)

    assert "vrs.256.18.0" in answer
    assert "2202211" in answer
    assert "встро" in answer and "насос" in answer
    assert any(card.sku == "VRS.256.18.0" for card in session.last_products)
    assert not any(
        claim in answer
        for claim in [
            "да, совместим",
            "полностью совместим",
            "точно совместим",
            "подходит напрямую",
            "гарантированно подходит",
        ]
    )
    assert any(
        caution in answer
        for caution in [
            "не подтверж",
            "нельзя подтверд",
            "не буду подтверж",
            "нужен расчет",
            "нужен расчёт",
            "нужна схема",
        ]
    )


def test_pump_union_valves_are_selected_only_after_system_side_size() -> None:
    pump = _classic_vrs()
    valve = Product(
        sku="VALVE-UNION-12",
        name='Кран шаровой 1/2" с американкой для отопления',
        category_path="Краны шаровые",
        brand="TEST",
        url="https://example.test/valve-union-12",
        price=800,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "тип товара": "Кран шаровой",
            "назначение": "Отопление",
            "диаметр": '1/2"',
            "тип присоединения": "С американкой",
        },
    )
    bot = ChatOrchestrator(products=[pump, valve])
    session_id = "pump-union-valves"
    bot.handle_chat(session_id, "VRS.256.18.0")

    clarification = bot.handle_chat(
        session_id,
        (
            "Ты сам предложил краны с американкой. Подбери два к этому насосу, "
            "только если точно подходят, и назови присоединительный размер."
        ),
    )
    clarification_answer = normalize_text(clarification.answer)

    assert clarification.products == []
    assert "dn 25" in clarification_answer
    assert _has_one_and_half(clarification.answer)
    assert "со стороны трубопровода" in clarification_answer

    result = bot.handle_chat(
        session_id,
        "1/2 дюйма, для отопления, только в наличии.",
    )
    answer = normalize_text(result.answer)

    assert [product.sku for product in result.products] == ["VALVE-UNION-12"]
    assert "количество 2 шт" in answer
    assert "цена за единицу 800 rub" in answer
    assert "итого 1600 rub" in answer
    assert "остаток сейчас 5 шт" in answer
    assert "останется 3 шт" in answer
