"""Словарь монтажных названий: сленг покупателя → семейство товаров.

Живые запросы «американка», «сгон», «футорка», «контргайка», «евроконус»
уходили в чужую категорию, и бот спрашивал «для чего нужен кран?» по товару,
которого в кранах нет. Тесты фиксируют и маршрутизацию, и защиту от
обратного эффекта: то же слово в творительном падеже описывает исполнение
уже обсуждаемого товара, а не новый узел.
"""

from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.trade_vocabulary import (
    is_reducer_element,
    is_system_agnostic_element,
    match_trade_term,
)
from app.agents.utils import normalize_text


@pytest.mark.parametrize(
    ("message", "element"),
    [
        ("нужна американка полдюйма вр", "американка"),
        ("дай сгон 3/4", "сгон"),
        ("нужна футорка 1/2 на 3/4", "футорка"),
        ("контргайка 1/2", "контргайка"),
        ("нужен ниппель 1 дюйм", "ниппель"),
        ("штуцер 3/4 наружная", "штуцер"),
        ("водорозетка 20х1/2", "водорозетка"),
        ("евроконус 16 на гребёнку", "евроконус"),
        ("бочонок 1/2 100 мм", "бочонок"),
        ("заглушка ппр 20", "заглушка"),
    ],
)
def test_trade_terms_are_recognised(message: str, element: str) -> None:
    term = match_trade_term(normalize_text(message))
    assert term is not None and term.element == element
    assert term.category == "fittings"


@pytest.mark.parametrize(
    "message",
    [
        "кран шаровой с американкой 1/2",
        "кран с полусгоном 3/4",
        "с американкой",
        "со сгоном",
        "нужен котёл на 24 квт",
        "фильтр со штуцером",
    ],
)
def test_feature_phrasing_does_not_switch_family(message: str) -> None:
    """«С американкой» — исполнение обсуждаемого товара, а не новый узел."""
    assert match_trade_term(normalize_text(message)) is None


def test_sewer_context_keeps_its_own_family() -> None:
    term = match_trade_term(normalize_text("крестовина канализационная 110"))
    assert term is not None and term.category == "sewer"


@pytest.mark.parametrize(
    ("message", "element"),
    [
        ("нужна американка полдюйма вр", "американка"),
        ("дай сгон 3/4", "сгон"),
        ("контргайка 1/2", "контргайка"),
    ],
)
def test_router_routes_slang_to_fittings(message: str, element: str) -> None:
    result = IntentRouterAgent().route(message)
    assert result.category == "fittings"
    assert result.slots.get("element_type") == element
    assert result.slots.get("trade_element") == element


def test_router_keeps_valve_when_term_describes_execution() -> None:
    result = IntentRouterAgent().route("кран шаровой 1/2 вн-вн для воды с американкой")
    assert result.category == "valves"
    assert "trade_element" not in result.slots


@pytest.mark.parametrize(
    ("element", "expected"),
    [("американка", True), ("контргайка", True), ("сгон", True), ("заглушка", False)],
)
def test_threaded_families_skip_the_system_question(element: str, expected: bool) -> None:
    """Для латунных резьбовых узлов «PPR или канализация» — бессмысленный вопрос."""
    assert is_system_agnostic_element(element) is expected


@pytest.mark.parametrize(
    ("element", "expected"),
    [("футорка", True), ("переходник", True), ("контргайка", False)],
)
def test_reducer_families_match_size_by_port(element: str, expected: bool) -> None:
    """«Футорка 1/2» — это 1/2 с одной стороны, а не одинаковый размер с обеих."""
    assert is_reducer_element(element) is expected
