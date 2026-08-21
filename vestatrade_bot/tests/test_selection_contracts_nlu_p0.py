from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.slot_filling import SlotFillingAgent
from app.models import SessionState


def _fill(message: str, session: SessionState | None = None):
    session = session or SessionState(session_id="selection-contract")
    intent = IntentRouterAgent().route(message, session=session)
    return intent, SlotFillingAgent().fill(message, intent, session)


@pytest.mark.parametrize(
    "message",
    [
        "Нужен кран на воду полдюйма",
        "Подберите полдюймовый шаровый кран для воды",
        "Ищу водяной кран ½ дюйма",
    ],
)
def test_generic_threaded_ball_valve_requires_thread_pair(message: str) -> None:
    intent, result = _fill(message)

    assert intent.category == "valves"
    assert result.needs_clarification is True
    assert result.slots["size_inch"] == "1/2"
    assert "резьб" in (result.question or "").lower()
    assert "вр-вр" in (result.question or "").lower()
    assert result.expected_slots == ["thread_type"]
    assert result.blocking is True


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("мама-мама", "ff"),
        ("две внутренние", "ff"),
        ("внутренняя резьба с обеих сторон", "ff"),
        ("папа-папа", "mm"),
        ("обе наружные", "mm"),
        ("с одной стороны внутренняя, с другой наружная", "fm"),
    ],
)
def test_thread_pair_paraphrases_are_canonical(phrase: str, expected: str) -> None:
    intent, result = _fill(f"Нужен кран на воду 1/2, {phrase}")

    assert intent.slots["thread_type"] == expected
    assert result.needs_clarification is False


@pytest.mark.parametrize(
    "message",
    [
        "Нужен уголок на пластиковую трубу 20",
        "Для пластиковой трубы 20 нужен угольник",
        "Ищу колено для пластиковой трубы d20",
        "Нужен поворот 90 градусов для PPR трубы 20",
    ],
)
def test_requested_fitting_head_wins_over_compatibility_pipe(message: str) -> None:
    result = IntentRouterAgent().route(message)

    assert result.category == "fittings"
    assert result.slots["element_type"] == "угольник"
    assert result.slots["product_kind"] == "elbow"
    assert result.slots["diameter_mm"] == 20


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("½", "1/2"),
        ("¾", "3/4"),
        ("⅜", "3/8"),
        ("¼", "1/4"),
        ("половина дюйма", "1/2"),
        ("четверть дюйма", "1/4"),
    ],
)
def test_unicode_and_spoken_inch_fractions(literal: str, expected: str) -> None:
    result = IntentRouterAgent().route(f"Нужен шаровый кран {literal} ВР-ВР для воды")

    assert result.category == "valves"
    assert result.slots["size_inch"] == expected


@pytest.mark.parametrize("form", ["прямой", "угловой"])
def test_thermostatic_valve_does_not_reask_control_mode(form: str) -> None:
    intent, result = _fill(
        f"Нужен термостатический клапан {form} 1/2 для радиатора"
    )

    assert intent.slots["product_kind"] == "thermostatic_valve"
    assert result.needs_clarification is False
    assert "регулировать" not in (result.question or "").lower()
    assert "перекрывать" not in (result.question or "").lower()


def test_thermostatic_valve_form_correction_preserves_size_and_kind() -> None:
    session = SessionState(
        session_id="radiator-correction",
        category="radiator_fittings",
        slots={
            "product_kind": "thermostatic_valve",
            "connection_form": "прямое",
            "body_form": "прямой",
            "size_inch": "1/2",
        },
    )

    intent, result = _fill(
        "Нет, перепутал: нужен угловой, остальные параметры те же",
        session,
    )

    assert intent.category == "radiator_fittings"
    assert result.slots["product_kind"] == "thermostatic_valve"
    assert result.slots["size_inch"] == "1/2"
    assert result.slots["connection_form"] == "угловое"
    assert result.needs_clarification is False


def test_generic_radiator_valve_still_asks_control_mode() -> None:
    intent, result = _fill("Нужен клапан для радиатора прямой 1/2")

    assert intent.category == "radiator_fittings"
    assert intent.slots.get("product_kind") is None
    assert result.needs_clarification is True
    assert "регулировать" in (result.question or "").lower()
    assert "перекрывать" in (result.question or "").lower()


def test_thermostatic_head_does_not_treat_half_inch_as_head_interface() -> None:
    intent, result = _fill("Нужна термоголовка на батарею 1/2")

    assert intent.slots["product_kind"] == "thermostatic_head"
    assert result.needs_clarification is True
    assert "m30" in (result.question or "").lower()
    assert result.expected_slots == ["metric_thread", "valve_model"]
    assert result.blocking is True
