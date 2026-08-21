"""Three gaps closed after the live dialogues of 2026-08-05.

1. «Сколько стоит труба 25 мм?» returned the clarification funnel and no price.
2. «Что значит ВР/ВР?» — the model confidently answered «врезное/врезное… крепится
   фитингами, а не резьбой», i.e. the opposite of internal thread.
3. «Вода еле течёт из крана» triggered the flood-emergency script («перекройте
   вводной кран, звоните в аварийную службу») although it means low pressure.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.response_composer import ResponseComposerAgent
from app.models import Product


def _pipe(sku: str, name: str, price: float, stock: int = 5) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Трубы полипропиленовые",
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=price,
        currency="RUB",
        stock_status="в наличии" if stock else "нет в наличии",
        stock_qty=stock,
        attributes_normalized={
            "материал": "Полипропилен",
            "назначение": "Отопление, водоснабжение",
            "диаметр (мм)": "25",
        },
    )


# ------------------------------------------------------------------ 1. цена


def _price_bot() -> ChatOrchestrator:
    return ChatOrchestrator(
        products=[
            _pipe("PPR-25-A", "Труба PPR 25 мм PN20", 110),
            _pipe("PPR-25-B", "Труба PPR 25 мм армированная алюминием PN25", 168),
            _pipe("PPR-25-C", "Труба PPR 25 мм армированная стекловолокном PN25", 413, stock=0),
        ]
    )


def test_price_question_returns_a_real_range_from_the_feed() -> None:
    response = _price_bot().handle_chat("price", "сколько стоит труба 25 мм полипропилен?")

    answer = response.answer
    # The range must be the actual min/max of matching cards, not a guess.
    assert "110" in answer and "413" in answer
    assert "от" in answer.lower()
    # And it must still invite the customer to narrow down.
    assert "уточните" in answer.lower()


def test_price_range_reports_how_many_positions_are_in_stock() -> None:
    response = _price_bot().handle_chat("price-stock", "сколько стоит труба 25 мм полипропилен?")

    assert "3" in response.answer  # positions found
    assert "в наличии 2" in response.answer


def test_price_range_aggregates_every_match_beyond_the_display_limit() -> None:
    products = [
        _pipe(
            f"PPR-25-{index:02d}",
            "Труба PPR 25 мм PN20",
            100 + index,
        )
        for index in range(30)
    ]
    products.append(
        _pipe(
            "PPR-25-OUTLIER",
            "Труба PPR 25 мм PN20",
            9999,
            stock=0,
        )
    )
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "price-full-aggregate",
        "сколько стоит труба 25 мм полипропилен?",
    )

    assert "от 100 до 9999" in response.answer
    assert "подходящих позиций в каталоге 31" in response.answer
    assert "в наличии 30" in response.answer


def test_price_question_about_a_shown_product_keeps_the_exact_answer() -> None:
    # A range would be wrong once a concrete card is on the table.
    bot = _price_bot()
    bot.handle_chat("price-shown", "Труба PPR 25 мм PN20")
    response = bot.handle_chat("price-shown", "а сколько стоит?")

    assert "от 110 до 413" not in response.answer


def test_plain_product_request_is_not_turned_into_a_price_range(orchestrator) -> None:
    response = orchestrator.handle_chat("no-price", "нужна труба 20 мм для отопления")

    assert "разброс зависит" not in response.answer.lower()


# -------------------------------------------------------------- 2. глоссарий


@pytest.mark.parametrize(
    ("question", "must_contain", "must_not_contain"),
    [
        ("а что значит ВР/ВР?", "внутренняя резьба", "врезн"),
        ("что значит вн.-нар.?", "наружная", "врезн"),
        ("что значит полнопроходной?", "не сужается", "врезн"),
        ("что такое американка?", "накидной гайкой", "врезн"),
        ("что такое гребёнка?", "коллектор", "врезн"),
    ],
)
def test_known_terms_are_answered_from_the_verified_glossary(
    question: str, must_contain: str, must_not_contain: str
) -> None:
    answer = ResponseComposerAgent().compose_term_consult(question).lower()

    assert must_contain.lower() in answer
    assert must_not_contain.lower() not in answer


def test_unknown_term_is_still_refused_honestly() -> None:
    answer = ResponseComposerAgent().compose_term_consult("что такое кавитация?").lower()

    assert "не подскажу без проверки" in answer


def test_glossary_prefers_the_longer_more_specific_match() -> None:
    # «монтажная длина» must win over the bare «напор» entry.
    answer = ResponseComposerAgent().compose_term_consult(
        "что такое монтажная длина и напор?"
    ).lower()

    assert "между плоскостями подключений" in answer


# --------------------------------------------------------------- 3. авария


@pytest.mark.parametrize(
    "message",
    [
        "у меня на втором этаже вода еле течёт из крана",
        "слабый напор, вода еле идёт",
        "вода не течет на верхнем этаже",
    ],
)
def test_weak_flow_is_not_treated_as_a_flood(orchestrator, message: str) -> None:
    response = orchestrator.handle_chat(f"weak-{hash(message)}", message)

    answer = response.answer.lower()
    assert "остановите аварийную ситуацию" not in answer
    assert "перекройте вводной кран" not in answer
    assert "аварийно-диспетчерскую" not in answer


def test_weak_flow_from_a_tap_is_routed_to_pressure_not_valve_sizing(orchestrator) -> None:
    # «кран» here is where the water comes out, not the product wanted.
    response = orchestrator.handle_chat("weak-route", "вода еле течёт из крана")

    assert response.debug["category"] == "pumps"
    assert "1/2" not in response.answer


def test_real_leak_still_triggers_the_emergency_flow(orchestrator) -> None:
    response = orchestrator.handle_chat("leak", "Прорвало радиатор, льётся кипяток!")

    assert "остановите аварийную ситуацию" in response.answer.lower()


def test_flood_wording_still_triggers_the_emergency_flow(orchestrator) -> None:
    response = orchestrator.handle_chat("flood", "затопило соседей, вода из трубы")

    assert "остановите аварийную ситуацию" in response.answer.lower()


def test_explicit_valve_request_stays_in_valves(orchestrator) -> None:
    # Guard against overcorrecting the symptom override.
    response = orchestrator.handle_chat("valve-ok", "нужен кран шаровой 1/2 для воды")

    assert response.debug["category"] == "valves"
