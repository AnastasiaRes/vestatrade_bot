"""Место установки не должно подменять предмет запроса.

«Фильтр перед насосом» уходил в насосы: ранняя проверка головного
существительного срабатывала на любом упоминании насоса, хотя здесь он
ориентир монтажа. Покупатель описывает узел десятком способов, поэтому
проверяется не одна формулировка, а семейство.
"""

from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent


@pytest.fixture(scope="module")
def router() -> IntentRouterAgent:
    return IntentRouterAgent()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Нужен фильтр перед насосом", "filters"),
        ("Нужен фильтр грубой очистки перед насосной станцией", "filters"),
        ("Нужен кран перед насосной станцией", "valves"),
        ("Нужен кран после насоса", "valves"),
        ("Фильтр на входе в насос", "filters"),
        ("Кран, который стоит перед насосной станцией", "valves"),
        ("Нужен кран между насосом и баком", "valves"),
        ("Фильтр на линии к насосу", "filters"),
        ("Кран сразу за насосом", "valves"),
        ("Нужен фильтр до насоса", "filters"),
    ],
)
def test_landmark_does_not_capture_the_request(
    router: IntentRouterAgent,
    message: str,
    expected: str,
) -> None:
    assert router.route(message, None).category == expected


@pytest.mark.parametrize(
    "message",
    [
        "Нужен циркуляционный насос",
        "Нужен насос с фильтром",
        "насос для канализации",
        "Нужен насос для отопления частного дома",
        "Подберите насос к котлу",
    ],
)
def test_pump_as_the_subject_still_routes_to_pumps(
    router: IntentRouterAgent,
    message: str,
) -> None:
    # Обратная сторона границы: «для», «с» и «к» вводят назначение, а не место
    # установки, и насос в таких фразах остаётся предметом запроса.
    assert router.route(message, None).category == "pumps"
