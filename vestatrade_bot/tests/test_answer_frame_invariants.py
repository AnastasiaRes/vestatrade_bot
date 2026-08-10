"""Инварианты класса A: ответ клиента не должен теряться.

Разбор: reports/architecture_error_classes_2026-08-06.md

Корневая причина найденного бага была не в парсинге чисел, а в том, что
категория пересчитывалась по словам самой реплики: «радиаторная разводка,
70 градусов, 2 бара» уезжала в `radiators` из-за слова «радиаторн», блок
извлечения для `pipes` не выполнялся, и все три названных параметра терялись.
По отдельности каждое значение распознавалось — поэтому обычные точечные тесты
это не ловили.

Здесь проверяется инвариант, а не формулировка: если бот задал вопрос и клиент
назвал значения, они обязаны попасть в слоты — в любом порядке и в любой
комбинации. Это ловит весь класс, а не конкретную фразу.
"""

from __future__ import annotations

import itertools

import pytest

from app.agents.orchestrator import ChatOrchestrator


PIPE_ANSWER_PARTS = {
    "радиаторная разводка": ("pipe_service", "радиаторная разводка"),
    "70 градусов": ("operating_temperature_c", 70.0),
    "2 бара": ("operating_pressure_bar", 2.0),
}


@pytest.mark.parametrize("order", list(itertools.permutations(PIPE_ANSWER_PARTS)))
def test_compound_answer_fills_every_named_parameter(orchestrator, order) -> None:
    """Любая перестановка составного ответа заполняет все три слота."""
    session_id = f"compound-{'-'.join(order)}"
    orchestrator.handle_chat(session_id, "нужны трубы для отопления")
    orchestrator.handle_chat(session_id, ", ".join(order))

    slots = orchestrator.sessions.get(session_id).slots
    for part in order:
        key, expected = PIPE_ANSWER_PARTS[part]
        assert slots.get(key) == expected, (part, key, slots.get(key))


@pytest.mark.parametrize(
    "parts",
    [
        ("радиаторная разводка", "70 градусов"),
        ("70 градусов", "2 бара"),
        ("радиаторная разводка", "2 бара"),
    ],
)
def test_partial_compound_answer_keeps_what_was_said(orchestrator, parts) -> None:
    session_id = f"partial-{'-'.join(parts)}"
    orchestrator.handle_chat(session_id, "нужны трубы для отопления")
    orchestrator.handle_chat(session_id, ", ".join(parts))

    slots = orchestrator.sessions.get(session_id).slots
    for part in parts:
        key, expected = PIPE_ANSWER_PARTS[part]
        assert slots.get(key) == expected, (part, key, slots.get(key))


def test_answer_does_not_get_the_same_question_back(orchestrator) -> None:
    orchestrator.handle_chat("frame-noloop", "нужны трубы для отопления")
    asked = orchestrator.handle_chat("frame-noloop", "нужны трубы для отопления").answer
    answered = orchestrator.handle_chat(
        "frame-noloop", "радиаторная разводка, 70 градусов, 2 бара"
    ).answer

    assert answered != asked
    assert "уточню ещё раз" not in answered.lower()


# --------------------------------------------------------- рамка не поглощает всё


def test_explicit_correction_may_still_switch_the_topic(orchestrator) -> None:
    # «Нет, я спрашиваю про трубу» — исправление, а не ответ.
    orchestrator.handle_chat("frame-fix", "Нужен насос")
    response = orchestrator.handle_chat("frame-fix", "Нет, я спрашиваю про трубу 20 мм")

    assert response.debug["category"] == "pipes"


def test_naming_another_product_switches_the_topic(orchestrator) -> None:
    # «насос 25/6 180» прямо называет товар, значит это новый предмет.
    orchestrator.handle_chat("frame-prod", "нужны трубы для отопления")
    response = orchestrator.handle_chat("frame-prod", "насос 25/6 180")

    assert response.debug["category"] == "pumps"


def test_request_verb_switches_the_topic(orchestrator) -> None:
    orchestrator.handle_chat("frame-verb", "нужны трубы для отопления")
    response = orchestrator.handle_chat("frame-verb", "подберите канализацию 110 наружную")

    assert response.debug["category"] == "sewer"


def test_adjective_of_another_category_is_not_a_topic_switch(orchestrator) -> None:
    # «радиаторная» — прилагательное про участок трубы, а не запрос радиатора.
    orchestrator.handle_chat("frame-adj", "нужны трубы для отопления")
    response = orchestrator.handle_chat("frame-adj", "радиаторная разводка")

    assert response.debug["category"] == "pipes"


def test_radiator_request_still_reaches_radiators(orchestrator) -> None:
    # Защита от перекоррекции: настоящий запрос радиатора не должен залипнуть.
    orchestrator.handle_chat("frame-rad", "нужны трубы для отопления")
    response = orchestrator.handle_chat("frame-rad", "нужен радиатор 500 мм 8 секций")

    assert response.debug["category"] == "radiators"
