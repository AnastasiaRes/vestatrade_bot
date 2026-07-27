"""Regressions for keeping the customer's pump-selection goal in context."""

from __future__ import annotations


def test_pending_pump_purpose_answer_with_system_context_stays_pumps(
    orchestrator,
) -> None:
    session_id = "pump-purpose-with-system-context"
    first = orchestrator.handle_chat(session_id, "Нужен насос подешевле")

    session = orchestrator.sessions.get(session_id)
    assert first.debug["category"] == "pumps"
    assert session.category == "pumps"
    assert session.pending_question
    assert "для какой задачи" in session.pending_question.lower()

    response = orchestrator.handle_chat(
        session_id,
        (
            "Для отопления: котёл уже есть, дом в два этажа, 140 м², "
            "радиаторы и труба 25 мм."
        ),
    )

    assert response.debug["category"] == "pumps"
    assert response.debug["topic_changed"] is False
    assert response.debug["intent"] != "complectation"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["pump_use"] == "отопление"
    assert "газовый или электрический" not in response.answer.lower()
    assert "тип радиатора" not in response.answer.lower()


def test_existing_boiler_then_explicit_pump_request_is_not_complectation(
    orchestrator,
) -> None:
    session_id = "existing-boiler-needs-pump"
    orchestrator.handle_chat(session_id, "Нужен котёл")

    response = orchestrator.handle_chat(
        session_id,
        "Котёл уже есть, нужен циркуляционный насос",
    )

    assert response.debug["category"] == "pumps"
    assert response.debug["intent"] != "complectation"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["pump_use"] == "отопление"
    assert "по какому котлу" not in response.answer.lower()
    assert "проверить комплектацию" not in response.answer.lower()


def test_not_boiler_but_pump_correction_switches_to_pumps(orchestrator) -> None:
    session_id = "not-boiler-but-pump"
    orchestrator.handle_chat(session_id, "Нужен котёл")

    response = orchestrator.handle_chat(session_id, "Не котёл, а насос")

    assert response.debug["category"] == "pumps"
    assert response.debug["intent"] != "complectation"
    assert response.debug["topic_changed"] is True
    assert "для какой задачи нужен насос" in response.answer.lower()
    assert "газовый или электрический" not in response.answer.lower()


def test_builtin_pump_question_about_boiler_remains_complectation(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "boiler-builtin-pump-control",
        "В этом котле есть встроенный насос?",
    )

    assert response.debug["intent"] == "complectation"
    assert response.products == []
    assert "модель" in response.answer.lower() or "артикул" in response.answer.lower()
    assert "комплектац" in response.answer.lower()


def test_existing_boiler_builtin_pump_wording_is_not_new_pump_selection(
    orchestrator,
) -> None:
    response = orchestrator.handle_chat(
        "existing-boiler-builtin-pump",
        "Котёл уже есть, насос встроен?",
    )

    assert response.debug["intent"] == "complectation"
    assert response.products == []
    assert "модель" in response.answer.lower() or "артикул" in response.answer.lower()


def test_explicit_switch_from_pending_pump_to_boiler_is_respected(
    orchestrator,
) -> None:
    session_id = "explicit-pump-to-boiler-switch"
    orchestrator.handle_chat(session_id, "Нужен насос подешевле")

    response = orchestrator.handle_chat(
        session_id,
        "Насос больше не нужен, теперь подберите электрический котёл на 100 м²",
    )

    assert response.debug["category"] == "boilers"
    assert response.debug["topic_changed"] is True
    assert response.debug["slots"]["boiler_type"] == "электрический"
    assert "для какой задачи нужен насос" not in response.answer.lower()
