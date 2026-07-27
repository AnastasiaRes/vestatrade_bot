"""Regressions for the complectation follow-up dialog (found 2026-07-27).

Reported transcript: after three boilers were shown, «у него есть насос
встроенный?» asked which model, and answering it by quoting the model name
(«1. 2202210 — Котел электрический Arderia E9, 9 кВт») produced
«Да: ... — электрический котёл» — an answer about the boiler TYPE, because the
word «электрический» inside the quoted name looked like a type question.
"""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _boiler(sku: str, name: str, power: str, description: str) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Котлы электрические",
        brand="ARDERIA",
        url=f"https://example.test/{sku}",
        price=35000 + int(power) * 100,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "мощность, квт": power,
            "тип котла": "Электрический",
            "количество контуров": "Одноконтурный",
        },
        description=description,
    )


WITH_PUMP = _boiler(
    "2202210",
    "Котел электрический Arderia E9, 9 кВт",
    "9",
    "Встроенный циркуляционный насос с тремя скоростями и расширительный бак.",
)
WITHOUT_PUMP = _boiler(
    "511705",
    "Котел электрический THERMEX Sonne 12",
    "12",
    "Электрический котёл мощностью 12 кВт с Wi-Fi управлением.",
)


def _bot() -> ChatOrchestrator:
    return ChatOrchestrator(products=[WITH_PUMP, WITHOUT_PUMP])


def test_quoted_model_name_answers_the_pending_pump_question() -> None:
    bot = _bot()
    bot.handle_chat("quoted", "котел электрический на 100м2")
    asked = bot.handle_chat("quoted", "у него есть насос встроенный?")
    assert "по какой из показанных моделей" in asked.answer.lower()

    response = bot.handle_chat("quoted", "1. 2202210 — Котел электрический Arderia E9, 9 кВт")

    # Must answer the pump question, not re-describe the boiler type.
    assert "электрический котёл" not in response.answer.lower()
    assert "тип взят из карточки" not in response.answer.lower()
    assert "насос" in response.answer.lower()
    assert "2202210" in response.answer


def test_type_question_about_shown_boiler_still_works() -> None:
    # Guard against overcorrecting: a real type question must still be answered,
    # and it is only a type question when nothing is pending.
    bot = _bot()
    bot.handle_chat("type-q", "котел электрический на 100м2")
    response = bot.handle_chat("type-q", "а он газовый?")

    # Answered from the cards (wording may be singular or plural).
    assert "электрическ" in response.answer.lower()
    assert "карточ" in response.answer.lower()


def test_question_about_all_shown_products_is_answered_per_product() -> None:
    bot = _bot()
    bot.handle_chat("all-shown", "котел электрический на 100м2")
    response = bot.handle_chat(
        "all-shown", "из предложенных тобой котлов, какие имеют встроенный насос?"
    )

    # One verdict per card instead of "which model did you mean?".
    assert "по какой из показанных моделей" not in response.answer.lower()
    assert "2202210" in response.answer and "511705" in response.answer
    lines = [line for line in response.answer.split("\n") if line.startswith("-")]
    assert len(lines) == 2, response.answer


def test_pronoun_referring_to_part_from_previous_reply_is_resolved() -> None:
    # After the companion hint mentions a built-in pump, «а тут в каких он
    # добавлен?» refers to that pump by pronoun. Nothing linked the two, so the
    # question fell through to the small-talk reply («Я на связи…»).
    bot = _bot()
    bot.handle_chat("pronoun", "котел электрический на 100м2")
    bot.handle_chat("pronoun", "у каких из них есть встроенный насос?")

    response = bot.handle_chat("pronoun", "а тут в каких он добавлен?")

    assert "я на связи" not in response.answer.lower()
    assert "насос" in response.answer.lower()
    assert "2202210" in response.answer


def test_stock_question_with_pronoun_is_not_hijacked_as_complectation() -> None:
    # «а он есть в наличии?» contains both a pronoun and the presence word
    # «есть», but it asks about stock — it must not become a parts check.
    bot = _bot()
    bot.handle_chat("stock-pron", "котел электрический на 100м2")
    bot.handle_chat("stock-pron", "у каких из них есть встроенный насос?")

    response = bot.handle_chat("stock-pron", "а он есть в наличии?")

    assert "подтверждено" not in response.answer.lower()
    assert "налич" in response.answer.lower()


def test_explicit_part_question_keeps_its_richer_existing_answer() -> None:
    # When the part is named outright, the pre-existing handlers must stay in
    # charge — they also explain what the product is for.
    bot = _bot()
    bot.handle_chat("explicit", "котел электрический на 100м2")

    assert bot._part_question_about_shown_products(
        "а что в него входит и есть ли насос?", bot.sessions.get("explicit")
    ) == []


def test_all_shown_overview_never_claims_unconfirmed_parts() -> None:
    bot = _bot()
    bot.handle_chat("honest", "котел электрический на 100м2")
    response = bot.handle_chat("honest", "у каких из них есть встроенный насос?")

    confirmed_line = next(line for line in response.answer.split("\n") if "2202210" in line)
    unconfirmed_line = next(line for line in response.answer.split("\n") if "511705" in line)

    assert "да, подтверждено" in confirmed_line.lower()
    assert "подтверждения нет" in unconfirmed_line.lower()
