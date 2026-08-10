"""Monteur slang must become slots, not a repeated question.

Live dialogues (reports/jargon_terminology_2026-08-05.md) showed the bot asking
for information the customer had already given:

    — нужен кран полдюйма мама-папа
    — Уточните … и размер: 1/2, 3/4 или диаметр в мм.

«полдюйма» is the size and «мама-папа» is the thread pairing. Translating slang
into an existing slot is a lexical job, so it stays in the rule layer — the LLM
is not needed to know that «мама» means a female thread.
"""

from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.utils import normalize_text


@pytest.fixture
def router() -> IntentRouterAgent:
    return IntentRouterAgent()


@pytest.mark.parametrize(
    ("message", "size", "thread"),
    [
        ("нужен кран полдюйма мама-папа", "1/2", "fm"),
        ("кран три четверти мама-мама", "3/4", "ff"),
        ("кран на дюйм папа-папа", "1", "mm"),
        ("нужен кран полудюймовый", "1/2", None),
    ],
)
def test_spoken_size_and_slang_thread_become_slots(
    router: IntentRouterAgent, message: str, size: str, thread: str | None
) -> None:
    slots = router.route(message, session=None).slots

    assert slots.get("size_inch") == size
    assert slots.get("thread_type") == thread


def test_amerikanka_sets_the_union_slot(router: IntentRouterAgent) -> None:
    slots = router.route("нужна американка на дюйм", session=None).slots

    assert slots.get("union") is True
    assert slots.get("size_inch") == "1"


def test_dyuymovka_is_read_as_a_size_for_pipes(router: IntentRouterAgent) -> None:
    result = router.route("дайте трубу дюймовку для воды", session=None)

    assert result.category == "pipes"
    assert result.slots.get("size_inch") == "1"


def test_grebenka_is_normalised_to_collector() -> None:
    # Same trick the project already uses for «канашка» → «канализация», so
    # categorisation and feed search both see one canonical word.
    assert "коллектор" in normalize_text("нужна гребёнка на тёплый пол")
    assert "гребенк" not in normalize_text("нужна гребёнка на тёплый пол")


def test_explicit_inch_notation_still_wins(router: IntentRouterAgent) -> None:
    # Digits are more precise than words and must not be overridden by them.
    slots = router.route('нужен кран 3/4" вр/вр', session=None).slots

    assert slots.get("size_inch") == "3/4"
    assert slots.get("thread_type") == "ff"


def test_plain_valve_request_gains_no_phantom_slots(router: IntentRouterAgent) -> None:
    slots = router.route("нужен кран шаровой для воды", session=None).slots

    assert "size_inch" not in slots
    assert "thread_type" not in slots
    assert "union" not in slots


def test_bot_stops_asking_for_a_size_the_customer_already_gave(orchestrator) -> None:
    response = orchestrator.handle_chat("slang-size", "нужен кран полдюйма мама-папа")

    answer = response.answer.lower()
    # The reported symptom: the funnel asked for the size again.
    assert "1/2, 3/4" not in answer
    assert "диаметр в мм" not in answer
