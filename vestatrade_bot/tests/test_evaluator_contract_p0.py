from __future__ import annotations

import run_bot_evaluation as evaluator


def test_unrelated_question_is_not_a_valid_clarification() -> None:
    constraints = {
        "product_kind": "ball_valve",
        "size_inch": "3/4",
        "thread": "fm",
        "application": "water",
    }

    assert not evaluator.clarification_is_relevant(
        "Уточните: система PPR или канализация и какой нужен фитинг?",
        constraints,
        {"category": "fittings"},
    )
    assert evaluator.clarification_is_relevant(
        "Какая нужна ручка: бабочка или рычаг?",
        constraints,
        {"category": "valves"},
    )


def test_alternative_must_name_each_relaxed_field() -> None:
    assert not evaluator.alternative_discloses_mismatch(
        "Нашел ближайший аналог, он немного отличается.",
        "thread=fm",
    )
    assert evaluator.alternative_discloses_mismatch(
        "Ближайший аналог отличается резьбой: у него ВР-НР.",
        "thread=fm",
    )


def test_cheapest_answer_must_explicitly_bind_sku_to_comparison() -> None:
    assert evaluator.answer_identifies_cheapest(
        "Самый дешевый — VT.227.N.04, цена 580 рублей.",
        ["VT.227.N.04"],
    )
    assert not evaluator.answer_identifies_cheapest(
        "Варианты: VT.226.N.04 — 737; VT.227.N.04 — 580.",
        ["VT.227.N.04"],
    )


def test_correction_target_wins_over_negated_lengths() -> None:
    state = {"length_mm": "1000"}

    evaluator.update_constraints(
        state,
        "Не 1000 и не 2000 мм — нужна 1500. Есть точное совпадение?",
    )

    assert state["length_mm"] == "1500"
