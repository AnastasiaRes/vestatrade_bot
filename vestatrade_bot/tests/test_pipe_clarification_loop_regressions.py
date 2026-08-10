"""Regressions for the ГВС pipe dialogue that could never end.

Reported transcript: the customer asked for a 16 mm hot-water pipe, answered
the temperature three times in a row and received the identical question back
every time; after finally naming the section the bot asked about the material
and then forgot it had asked, greeting the customer from scratch.

Three independent defects produced that:

* the repeat counter compared the reply against the expectation recomputed
  *after* the answer was merged, so the slot just filled had already been
  removed and every reply looked like an answer to a different question —
  ``attempts`` stayed at zero and the loop breaker never fired;
* the pipes question→slot table did not know the word «материал» but did match
  «петл» inside «петля тёплого пола», so the material question was labelled
  with an already answered slot, ended up with no expectations at all and was
  dropped by ``sync_pending_question_state``;
* the material question offers the laying method as an alternative answer, but
  the gate that emits it only ever looked at ``pipe_material``.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


@pytest.fixture
def pipe_products() -> list[Product]:
    return [
        Product(
            sku="VTm.200.0.16",
            name="Труба металлопластиковая VALTEC PEX-AL-PEX 16x2.0",
            category_path="Трубы металлопластиковые",
            brand="VALTEC",
            url="https://example.test/mp16",
            price=95,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=500,
            attributes_normalized={
                "артикул": "VTm.200.0.16",
                "материал": "Металлопластик",
                "назначение": "Водоснабжение, Отопление",
                "диаметр (мм)": "16",
                "максимальная рабочая температура": "95 °C",
                "максимальное рабочее давление": "10 бар",
            },
        ),
    ]


@pytest.fixture
def bot(pipe_products: list[Product]) -> ChatOrchestrator:
    return ChatOrchestrator(products=pipe_products)


def test_repeating_a_known_fact_advances_the_repeat_counter(
    bot: ChatOrchestrator,
) -> None:
    """A reply that carries nothing new must not reset the loop breaker."""

    bot.handle_chat("gvs-repeat", "трубы для гвс 16 мм")
    session = bot.sessions.get("gvs-repeat")
    assert session.pending_question_state is not None
    assert session.pending_question_state.attempts == 0

    # New information: the temperature was still missing, so this is progress
    # and the customer must not be penalised for it.
    bot.handle_chat("gvs-repeat", "80 градусов, остальное не знаю")
    session = bot.sessions.get("gvs-repeat")
    assert session.slots["operating_temperature_c"] == 80.0
    assert session.pending_question_state.attempts == 0

    # The same temperature again carries nothing new. Before the fix the
    # counter stayed at zero here and the dialogue could loop forever.
    bot.handle_chat("gvs-repeat", "80 градусов")
    session = bot.sessions.get("gvs-repeat")
    assert session.pending_question_state.attempts == 1
    assert session.question_repeats == 1


def test_pipe_question_stops_repeating_after_two_useless_replies(
    bot: ChatOrchestrator,
) -> None:
    """The loop breaker must be reachable for multi-parameter questions."""

    bot.handle_chat("gvs-break", "трубы для гвс 16 мм")
    first = bot.handle_chat("gvs-break", "80 градусов, остальное не знаю").answer
    second = bot.handle_chat("gvs-break", "80 градусов").answer
    third = bot.handle_chat("gvs-break", "120 градусов, остальное не знаю").answer

    # The reported symptom: three byte-identical questions in a row.
    assert not (first == second == third)
    session = bot.sessions.get("gvs-break")
    assert session.pending_question_state.attempts >= 2


def test_material_question_is_remembered_with_its_expected_slots(
    bot: ChatOrchestrator,
) -> None:
    """«петля тёплого пола» in the text must not relabel a material question."""

    bot.handle_chat("gvs-material", "трубы для гвс 16 мм")
    answer = bot.handle_chat("gvs-material", "внутри дома, 100 градусов, 1 бар").answer
    assert "материал" in answer.lower()

    session = bot.sessions.get("gvs-material")
    state = session.pending_question_state
    # Before the fix this was ``None``: the question was asked and instantly
    # forgotten, so the next reply hit the out-of-scope greeting.
    assert state is not None
    assert state.question_id == "pipes.pipe_material"
    assert set(state.expected_slots) == {"pipe_material", "installation_method"}


def test_laying_method_closes_the_material_question(
    bot: ChatOrchestrator,
) -> None:
    """The question invites «скрытая», so «скрытая» has to close it."""

    bot.handle_chat("gvs-hidden", "трубы для гвс 16 мм")
    bot.handle_chat("gvs-hidden", "внутри дома, 100 градусов, 1 бар")
    answer = bot.handle_chat("gvs-hidden", "скрытая").answer

    session = bot.sessions.get("gvs-hidden")
    assert session.slots["installation_method"] == "скрытая"
    assert session.pending_question_state is None
    assert "какой материал" not in answer.lower()
    # The earlier facts must survive the answer.
    assert session.slots["pipe_service"] == "разводка внутри дома"
    assert session.slots["operating_temperature_c"] == 100.0
    assert session.slots["operating_pressure_bar"] == 1.0
    assert session.slots["diameter_mm"] == 16


def test_recorded_facts_are_named_back_instead_of_repeating_the_question(
    bot: ChatOrchestrator,
) -> None:
    """Seeing the identical text again is why the customer retyped the value."""

    bot.handle_chat("gvs-ack", "трубы для гвс 16 мм")
    answer = bot.handle_chat("gvs-ack", "80 градусов, остальное не знаю").answer

    assert "80" in answer
    assert "записал" in answer.lower()
    # The temperature is known now, so it must not be requested again.
    assert "максимальную температуру" not in answer
    assert "рабочее давление" in answer


def test_progress_on_a_pending_slot_does_not_escalate(
    bot: ChatOrchestrator,
) -> None:
    """A cooperative customer answering one field per turn is not penalised."""

    bot.handle_chat("gvs-progress", "трубы для гвс 16 мм")
    bot.handle_chat("gvs-progress", "80 градусов")
    session = bot.sessions.get("gvs-progress")
    assert session.pending_question_state.attempts == 0

    bot.handle_chat("gvs-progress", "2 бара")
    session = bot.sessions.get("gvs-progress")
    assert session.slots["operating_pressure_bar"] == 2.0
    assert session.pending_question_state.attempts == 0
