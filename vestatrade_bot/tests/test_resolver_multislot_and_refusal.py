"""Резолвер отвечает списком слотов и умеет отличать отказ от непонимания.

Раньше в промпте было «выбери ровно один параметр», а отказ и «реплика не про
параметры» приходили одинаковым ``null`` — различить их код не мог в принципе.
Поэтому «не знаю расход» получал в ответ тот же вопрос про другие параметры
(находка #7 в reports/live_dialogs_round3_2026-08-06.md).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.slot_answer_resolver import (
    SLOT_SPECS,
    PendingAnswerResolver,
    RESOLVER_PROMPT,
)
from app.openrouter_client import LLMResult


class _JsonLLM:
    """Отдаёт заранее заданный JSON резолверу."""

    last_json_output_accepted = True
    last_fallback_reason = None

    def __init__(self, payload: dict[str, Any] | None, available: bool = True) -> None:
        self.payload = payload
        self.available = available
        self.prompts: list[str] = []

    def complete_json(self, agent, messages, fallback):
        self.prompts.append(messages[0]["content"])
        if not self.available:
            return fallback, False
        return self.payload, True

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=None, llm_used=False, fallback_reason="not needed")


PIPE_QUESTION = (
    "Для какого участка отопления нужна труба… Также укажите максимальную "
    "температуру и рабочее давление системы."
)
PIPE_SLOTS = ["pipe_service", "operating_temperature_c", "operating_pressure_bar"]


def _resolve(payload, message, expected=None, category="pipes", available=True):
    llm = _JsonLLM(payload, available=available)
    resolver = PendingAnswerResolver(llm)
    return resolver.resolve(
        message=message,
        question=PIPE_QUESTION,
        expected_slots=expected if expected is not None else PIPE_SLOTS,
        category=category,
    ), llm


# ------------------------------------------------------------------ спеки


@pytest.mark.parametrize("key", PIPE_SLOTS)
def test_pipe_parameters_have_specs(key: str) -> None:
    # Без спек резолвер выходил с «no candidate slots» и не участвовал в ветке труб.
    assert key in SLOT_SPECS


# --------------------------------------------------------------- многослотность


def test_several_named_parameters_are_all_returned() -> None:
    resolved, _ = _resolve(
        {
            "slots": [
                {"slot": "pipe_service", "value": "радиаторная разводка", "evidence": "радиаторная разводка"},
                {"slot": "operating_temperature_c", "value": 70, "evidence": "70 градусов"},
                {"slot": "operating_pressure_bar", "value": 2, "evidence": "2 бара"},
            ],
            "refused": [],
        },
        "радиаторная разводка, 70 градусов, 2 бара",
    )

    assert resolved.accepted is True
    assert resolved.slots["pipe_service"] == "радиаторная разводка"
    assert resolved.slots["operating_temperature_c"] == 70
    assert resolved.slots["operating_pressure_bar"] == 2


def test_one_invalid_value_does_not_discard_the_valid_ones() -> None:
    resolved, _ = _resolve(
        {
            "slots": [
                {"slot": "operating_temperature_c", "value": 70, "evidence": "70 градусов"},
                # 900 бар вне диапазона спеки — эта запись должна отвалиться одна.
                {"slot": "operating_pressure_bar", "value": 900, "evidence": "2 бара"},
            ]
        },
        "70 градусов, 2 бара",
    )

    assert resolved.slots == {"operating_temperature_c": 70}


def test_value_absent_from_the_message_is_rejected() -> None:
    # Модель не вправе брать число из вопроса или из своей памяти.
    resolved, _ = _resolve(
        {"slots": [{"slot": "operating_temperature_c", "value": 95, "evidence": "95"}]},
        "радиаторная разводка",
    )

    assert resolved.slots == {}


def test_slot_outside_the_candidate_list_is_ignored() -> None:
    resolved, _ = _resolve(
        {"slots": [{"slot": "required_flow_m3_h", "value": 2, "evidence": "2"}]},
        "2 бара",
    )

    assert resolved.slots == {}


def test_legacy_single_slot_shape_is_still_accepted() -> None:
    # Устойчивость к формату дешевле ретрая: старую форму тоже принимаем.
    resolved, _ = _resolve(
        {"slot": "operating_temperature_c", "value": 70, "evidence": "70 градусов"},
        "70 градусов",
    )

    assert resolved.slots["operating_temperature_c"] == 70


# --------------------------------------------------------------------- отказ


def test_refusal_is_reported_separately_from_a_value() -> None:
    resolved, _ = _resolve(
        {
            "slots": [],
            "refused": [{"slot": "operating_pressure_bar", "evidence": "давление не знаю"}],
        },
        "радиаторная разводка, давление не знаю",
    )

    assert resolved.refused == ["operating_pressure_bar"]
    assert bool(resolved) is True  # результат есть, хотя значений нет


def test_refusal_needs_support_from_the_customers_words() -> None:
    # Модель не может «отказаться» за клиента, если тот ничего такого не сказал.
    resolved, _ = _resolve(
        {"slots": [], "refused": [{"slot": "operating_pressure_bar", "evidence": "2 бара"}]},
        "радиаторная разводка, 2 бара",
    )

    assert resolved.refused == []


def test_refusal_is_detected_without_the_llm() -> None:
    # Провайдер отваливался в проде по таймауту — отказ должен работать и так,
    # иначе вопрос снова зацикливается.
    resolved, _ = _resolve(None, "давление не знаю", available=False)

    assert resolved.refused == ["operating_pressure_bar"]


def test_bare_dont_know_defers_nothing_by_itself() -> None:
    # «Не знаю» без названия параметра относится ко всему вопросу; решать, что
    # отложить, должен вызывающий, а не эвристика резолвера.
    resolved, _ = _resolve(None, "не знаю", available=False)

    assert resolved.refused == []


# --------------------------------------------------------------------- промпт


def test_prompt_asks_for_all_named_parameters_and_for_refusals() -> None:
    assert "ВСЕ" in RESOLVER_PROMPT
    assert "refused" in RESOLVER_PROMPT
    assert "ровно один" not in RESOLVER_PROMPT


def test_llm_refusal_must_name_the_parameter_it_refuses() -> None:
    # Живой прогон: на «не знаю расход» модель вернула refused по уровню воды и
    # длине трассы — оба молча выпадали из воронки. Отказ принимается только по
    # тому параметру, который клиент действительно назвал.
    resolved, _ = _resolve(
        {
            "slots": [],
            "refused": [
                {"slot": "dynamic_water_level_m", "evidence": "не знаю"},
                {"slot": "horizontal_run_m", "evidence": "не знаю"},
                {"slot": "required_flow_m3_h", "evidence": "не знаю расход"},
            ],
        },
        "не знаю расход",
        expected=["dynamic_water_level_m", "horizontal_run_m", "required_flow_m3_h"],
        category="pumps",
    )

    assert resolved.refused == ["required_flow_m3_h"]
