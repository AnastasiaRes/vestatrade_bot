"""Edge regressions for passport provenance and pump/boiler context."""

from __future__ import annotations

import re

import pytest

from app.agents.feed_search import _builtin_part_confirmed
from app.agents.guardrails import GuardrailsAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.models import Product, SearchQuery


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
            "Встроенный циркуляционный насос с тремя скоростями и расширительный бак 6 л. "
            "Полный комплект гидравлической безопасности: предохранительный клапан, "
            "воздухоотводчик, манометр. Подключение: 3-фазное 380 В. "
            'Патрубки отопления G 3/4", подпитка G 1/2". '
            "Возможна работа с бойлером через трёхходовой клапан и датчик температуры "
            "(опции, приобретаются отдельно)."
        ),
    )


def _vrs(
    sku: str = "VRS.256.18.0",
    *,
    dn: int = 25,
    head_m: int = 6,
) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный VALTEC RS {dn}/{head_m}-180 с гайками",
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
            "максимальный напор, м": str(head_m),
            "монтажная длина, мм": "180",
        },
        description="Циркуляционный насос для систем отопления.",
    )


def _has_one_and_half(value: object) -> bool:
    text = normalize_text(str(value)).replace("½", " 1/2")
    return bool(re.search(r"\b1\s*1\s*/\s*2\b", text))


def _assert_cautious_compatibility(answer: str) -> None:
    text = normalize_text(answer)
    assert any(
        marker in text
        for marker in [
            "не подтверж",
            "нельзя подтверд",
            "не буду подтверж",
            "нужен расчет",
            "нужен расчёт",
            "нужна схема",
        ]
    )
    assert not any(
        claim in text
        for claim in [
            "да, совместим",
            "полностью совместим",
            "точно совместим",
            "подходит напрямую",
            "гарантированно подходит",
        ]
    )


def test_compatibility_uses_explicit_second_shown_pump() -> None:
    first = _vrs("VRS.254.18.0", dn=25, head_m=4)
    second = _vrs("VRS.328.18.0", dn=32, head_m=8)
    boiler = _e12()
    bot = ChatOrchestrator(products=[first, second, boiler])
    session_id = "explicit-second-pump"
    session = bot.sessions.get(session_id)
    query = SearchQuery(original_text="", category="pumps")
    first_card = bot.card_agent.build_card(first, query)
    second_card = bot.card_agent.build_card(second, query)
    assert first_card and second_card
    session.last_products = [first_card, second_card]
    session.category = "pumps"

    response = bot.handle_chat(
        session_id,
        "Совместим ли VRS.328.18.0 с котлом 2202211?",
    )
    answer = normalize_text(response.answer)

    assert "vrs.328.18.0" in answer
    assert "vrs.254.18.0" not in answer
    assert "2202211" in answer
    _assert_cautious_compatibility(response.answer)


def test_fresh_two_sku_compatibility_question_is_answered_as_comparison() -> None:
    bot = ChatOrchestrator(products=[_vrs(), _e12()])

    response = bot.handle_chat(
        "fresh-two-sku",
        "Совместим ли насос VRS.256.18.0 с котлом 2202211?",
    )
    answer = normalize_text(response.answer)

    assert "vrs.256.18.0" in answer
    assert "2202211" in answer
    _assert_cautious_compatibility(response.answer)


def test_pronoun_and_bare_boiler_sku_keep_pump_context() -> None:
    bot = ChatOrchestrator(products=[_vrs(), _e12()])
    session_id = "pronoun-bare-boiler-sku"
    bot.handle_chat(session_id, "VRS.256.18.0")

    response = bot.handle_chat(
        session_id,
        "Совместим ли он с 2202211?",
    )
    answer = normalize_text(response.answer)
    session = bot.sessions.get(session_id)

    assert "vrs.256.18.0" in answer
    assert "2202211" in answer
    assert any(card.sku == "VRS.256.18.0" for card in session.last_products)
    _assert_cautious_compatibility(response.answer)


@pytest.mark.parametrize(
    "question",
    [
        "Как подключить его к электросети?",
        "Какое у него электрическое подключение?",
        "Можно ли подключить его к обычной розетке?",
    ],
)
def test_electrical_pump_connection_is_not_answered_with_hydraulic_size(
    question: str,
) -> None:
    bot = ChatOrchestrator(products=[_vrs()])
    session_id = f"pump-electrical-{abs(hash(question))}"
    bot.handle_chat(session_id, "VRS.256.18.0")

    response = bot.handle_chat(session_id, question)
    answer = normalize_text(response.answer)

    assert "dn 25" not in answer and "dn25" not in answer
    assert "резьб" not in answer
    assert not _has_one_and_half(response.answer)
    assert any(
        marker in answer
        for marker in ["220", "электр", "зазем", "узо", "кабел", "клемм", "питан"]
    )


def test_no_doc_connection_does_not_claim_passport_or_g_thread() -> None:
    product = Product(
        sku="PUMP-NODOC",
        name="Насос циркуляционный TEST 25/6-180",
        category_path="Насосное оборудование",
        brand="TEST",
        url="https://example.test/pump-nodoc",
        price=1000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={
            "тип товара": "Насос",
            "присоединительная резьба": '1 1/2"',
        },
        description="Циркуляционный насос.",
    )
    bot = ChatOrchestrator(products=[product])
    session_id = "connection-without-passport"
    bot.handle_chat(session_id, "PUMP-NODOC")
    assert product.docs_text is None

    response = bot.handle_chat(session_id, "Какое у него присоединение?")
    answer = normalize_text(response.answer)

    assert _has_one_and_half(response.answer)
    assert "паспорт" not in answer
    assert not re.search(r"\bg\s*1\s*1\s*/\s*2\b", answer)


@pytest.mark.parametrize(
    ("part", "component_label", "description"),
    [
        ("насос", "циркуляционный насос", "В комплект не входит циркуляционный насос."),
        ("бак", "расширительный бак", "В комплект не входит расширительный бак."),
        (
            "3-ходовой клапан",
            "3-ходовой клапан",
            "В комплект не входит трёхходовой клапан.",
        ),
        (
            "группа безопасности",
            "группа безопасности",
            "В комплект не входит группа безопасности.",
        ),
    ],
)
def test_negative_package_wording_never_confirms_builtin_component(
    part: str,
    component_label: str,
    description: str,
) -> None:
    product = Product(
        sku=f"NEG-{part}",
        name="Котел тестовый",
        category_path="Котельное оборудование",
        brand="TEST",
        url="https://example.test/negative",
        price=1000,
        currency="RUB",
        stock_status="в наличии",
        attributes_normalized={},
        description=description,
    )
    guardrails = GuardrailsAgent()

    assert component_label not in guardrails.list_builtin_components(product)
    if part in {"насос", "бак", "группа безопасности"}:
        assert not _builtin_part_confirmed(product, part)


@pytest.mark.parametrize(
    ("message", "forbidden_required_part"),
    [
        (
            "Нужен котел со встроенным насосом, но без расширительного бака",
            "бак",
        ),
        (
            "Подбери котел со встроенным насосом без группы безопасности",
            "группа безопасности",
        ),
        (
            "Ищу котел без встроенного насоса, но со встроенным баком",
            "насос",
        ),
    ],
)
def test_negative_builtin_request_is_not_inverted_into_required_part(
    message: str,
    forbidden_required_part: str,
) -> None:
    intent = IntentRouterAgent().route(message)

    assert forbidden_required_part not in intent.slots.get(
        "required_builtin_parts",
        [],
    )


def test_external_safety_trio_is_not_reported_as_builtin_group() -> None:
    product = Product(
        sku="EXTERNAL-SAFETY",
        name="Котел тестовый",
        category_path="Котельное оборудование",
        brand="TEST",
        url="https://example.test/external-safety",
        price=1000,
        currency="RUB",
        stock_status="в наличии",
        attributes_normalized={},
        description=(
            "Для безопасного монтажа отдельно установите предохранительный клапан, "
            "затем воздухоотводчик и манометр."
        ),
    )

    assert not _builtin_part_confirmed(product, "группа безопасности")
    assert "группа безопасности" not in GuardrailsAgent().list_builtin_components(
        product
    )
