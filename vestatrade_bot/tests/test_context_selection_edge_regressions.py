"""Edge regressions for active-goal context and strict single selection."""

from __future__ import annotations

import pytest


def test_explicit_pipe_request_replaces_pending_pump_goal(orchestrator) -> None:
    session_id = "pending-pump-to-explicit-pipe"
    orchestrator.handle_chat(session_id, "Нужен насос")

    response = orchestrator.handle_chat(
        session_id,
        "Нужна труба 20 мм для отопления",
    )

    assert response.debug["category"] == "pipes"
    assert response.debug["topic_changed"] is True
    assert response.debug["slots"]["element_type"] == "труба"
    assert response.debug["slots"]["diameter_mm"] == 20
    assert "pump_type" not in response.debug["slots"]
    assert "для какой задачи нужен насос" not in response.answer.lower()


def test_explicit_correction_replaces_pending_pump_goal(orchestrator) -> None:
    session_id = "pending-pump-corrected-to-pipe"
    orchestrator.handle_chat(session_id, "Нужен насос")

    response = orchestrator.handle_chat(
        session_id,
        "Нет, я спрашиваю про трубу 20 мм",
    )

    assert response.debug["category"] == "pipes"
    assert response.debug["topic_changed"] is True
    assert response.debug["slots"]["element_type"] == "труба"
    assert response.debug["slots"]["diameter_mm"] == 20
    assert "pump_type" not in response.debug["slots"]
    assert "для какой задачи нужен насос" not in response.answer.lower()


@pytest.mark.parametrize(
    ("message", "expected_use", "expected_type"),
    [
        (
            "Котёл уже есть, нужен насос для водоснабжения",
            "водоснабжение",
            None,
        ),
        (
            "Котёл уже есть, нужен насос для повышения давления",
            "повышение давления",
            None,
        ),
        (
            "Котёл уже есть, нужен насос для откачки воды",
            "откачка воды",
            "дренажный",
        ),
    ],
)
def test_existing_boiler_does_not_override_explicit_pump_task(
    orchestrator,
    message: str,
    expected_use: str,
    expected_type: str | None,
) -> None:
    response = orchestrator.handle_chat(
        f"existing-boiler-{expected_use}",
        message,
    )

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_use"] == expected_use
    assert response.debug["slots"].get("pump_type") != "циркуляционный"
    if expected_type is not None:
        assert response.debug["slots"]["pump_type"] == expected_type
    assert "нужен циркуляционный насос" not in response.answer.lower()


def test_complectation_after_pending_pump_clears_all_pending_goal_state(
    orchestrator,
) -> None:
    session_id = "pump-then-boiler-complectation"
    orchestrator.handle_chat(session_id, "Нужен насос")
    orchestrator.handle_chat(session_id, "В этом котле есть встроенный насос?")

    response = orchestrator.handle_chat(session_id, "ARD-E9")
    session = orchestrator.sessions.get(session_id)

    assert response.debug["intent"] == "complectation"
    assert "ARD-E9" in response.answer
    assert session.category == "boilers"
    assert session.pending_question is None
    assert session.pending_intent_type is None
    assert session.pending_category is None
    assert session.pending_slot_keys == []
    assert session.pending_complectation_parts == []

    followup = orchestrator.handle_chat(session_id, "Нужна труба 20 мм")
    assert followup.debug["category"] == "pipes"
    assert "для какой задачи нужен насос" not in followup.answer.lower()


def test_choose_one_in_stock_does_not_recommend_shown_out_of_stock_item(
    orchestrator,
) -> None:
    session_id = "choose-one-in-stock"
    orchestrator.handle_chat(
        session_id,
        "Наружная канализационная труба 110 мм",
    )
    shown = orchestrator.handle_chat(session_id, "1000 мм")
    assert shown.products
    assert shown.products[0].sku == "OUT-110-1000"
    assert "нет в наличии" in shown.products[0].stock_status.lower()

    response = orchestrator.handle_chat(
        session_id,
        "Назови один вариант в наличии",
    )

    assert all(
        "нет в наличии" not in product.stock_status.lower()
        for product in response.products
    )
    assert "рекомендую: out-110-1000" not in response.answer.lower()
