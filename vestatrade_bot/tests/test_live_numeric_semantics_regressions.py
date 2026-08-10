from __future__ import annotations

from typing import Any

import pytest

from app.agents.engineering_interpreter import EngineeringInterpreterAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.slot_answer_resolver import PendingAnswerResolver
from app.models import IntentResult, SessionState


class _NumericPayloadLLM:
    last_json_output_accepted = True

    def __init__(self, slots: dict[str, Any], evidence: dict[str, str]) -> None:
        self.slots = slots
        self.evidence = evidence

    def complete_json(self, agent, messages, fallback):
        return (
            {
                "handled": True,
                "continuation": True,
                "dialog_act": "continue",
                "intent_type": "attribute_request",
                "category": "valves",
                "target_category": None,
                "requested_fields": [],
                "project_scope": "heating",
                "slots": self.slots,
                "slot_evidence": self.evidence,
                "slot_provenance": {
                    key: "current_message" for key in self.slots
                },
                "assumptions": [],
                "missing_slot_keys": [],
                "needs_clarification": False,
                "clarifying_question": None,
                "ready_for_catalog_selection": True,
                "response_mode": "catalog_search",
                "reply": None,
            },
            True,
        )


class _ResolverPayloadLLM:
    last_json_output_accepted = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, agent, messages, fallback):
        assert agent == "PendingAnswerResolver"
        return self.payload, True


def _interpret_numeric_payload(
    message: str,
    slots: dict[str, Any],
    evidence: dict[str, str],
    *,
    pending: list[str] | None = None,
):
    session = SessionState(session_id="numeric-grounding")
    session.pending_slot_keys = pending or []
    return EngineeringInterpreterAgent(
        _NumericPayloadLLM(slots, evidence)
    ).interpret(
        message,
        IntentResult(intent_type="attribute_request", category="valves"),
        session,
    )


@pytest.mark.parametrize(
    "message",
    [
        "труба для отопления, максимум 70 градусов",
        "труба для отопления, максимум 95 °С",
        "труба для отопления, максимум 80 C",
    ],
)
def test_temperature_bound_never_becomes_a_price_bound(message: str) -> None:
    result = IntentRouterAgent().route(message)

    assert result.category == "pipes"
    assert "max_price" not in result.slots
    assert result.slots["operating_temperature_c"] in {70.0, 80.0, 95.0}


@pytest.mark.parametrize("size", ["1/4", "3/8", "1/2", "3/4"])
def test_fractional_inch_size_before_russian_s_is_not_temperature(size: str) -> None:
    result = IntentRouterAgent().route(f"кран для воды {size} с американкой")

    assert result.category == "valves"
    assert result.slots["size_inch"] == size
    assert result.slots["union"] is True
    assert "operating_temperature_c" not in result.slots


def test_inch_fraction_does_not_mask_a_later_real_temperature() -> None:
    result = IntentRouterAgent().route(
        "кран 3/4 с американкой, рабочая температура до 95 °С"
    )

    assert result.slots["size_inch"] == "3/4"
    assert result.slots["operating_temperature_c"] == 95.0


@pytest.mark.parametrize(
    "text",
    [
        "максимум 70 градусов",
        "максимум 10 бар",
        "максимум 25 мм",
        "максимум 12 кВт",
        "максимум 80 литров",
    ],
)
def test_explicit_engineering_units_cannot_be_parsed_as_money(text: str) -> None:
    assert IntentRouterAgent._extract_price_bound(text, upper=True) is None


def test_pressure_unit_cannot_become_a_weak_diameter_guess() -> None:
    result = IntentRouterAgent().route(
        "кран 3/4 с американкой, температура 70, давление 10 бар"
    )

    assert result.slots["operating_temperature_c"] == 70.0
    assert result.slots["operating_pressure_bar"] == 10.0
    assert "diameter_mm" not in result.slots


@pytest.mark.parametrize(
    ("message", "expected_slot", "expected_value"),
    [
        ("труба температура 70", "operating_temperature_c", 70.0),
        ("труба давление 25", "operating_pressure_bar", 25.0),
    ],
)
def test_quantity_label_before_number_blocks_weak_diameter(
    message: str,
    expected_slot: str,
    expected_value: float,
) -> None:
    result = IntentRouterAgent().route(message)

    assert result.slots[expected_slot] == expected_value
    assert "diameter_mm" not in result.slots


def test_pipe_metres_are_quantity_while_millimetres_are_diameter() -> None:
    metres = IntentRouterAgent().route("труба 20 метров")
    millimetres = IntentRouterAgent().route("труба 20 мм")

    assert metres.slots["total_length_m"] == 20.0
    assert "diameter_mm" not in metres.slots
    assert millimetres.slots["diameter_mm"] == 20


@pytest.mark.parametrize(
    "message",
    [
        "трубы для тёплого пола, около 85 квадратов",
        "трубы для тёплого пола на 85 м²",
        "трубы для тёплого пола, площадь 85 кв. м",
    ],
)
def test_warm_floor_area_never_becomes_pipe_diameter(message: str) -> None:
    result = IntentRouterAgent().route(message)

    assert result.slots["warm_floor_area_m2"] == 85
    assert "diameter_mm" not in result.slots


def test_engineering_llm_slots_are_filtered_by_dimension_before_merge() -> None:
    message = "кран 3/4 с американкой, максимум 70 градусов"
    result = _interpret_numeric_payload(
        message,
        {
            "size_inch": "3/4",
            "operating_temperature_c": 70,
            # Both values are literally present, but their dimensions are wrong.
            "max_price": 70,
            "diameter_mm": 4,
        },
        {
            "size_inch": "3/4",
            "operating_temperature_c": "70 градусов",
            "max_price": "максимум 70 градусов",
            "diameter_mm": "4",
        },
    )

    assert result.output_accepted is True
    assert result.slots == {
        "size_inch": "3/4",
        "operating_temperature_c": 70,
    }


def test_engineering_llm_accepts_several_explicitly_dimensioned_values() -> None:
    message = (
        "кран 3/4 с американкой, до 95 °С, давление 10 бар, "
        "бюджет до 5 000 рублей"
    )
    result = _interpret_numeric_payload(
        message,
        {
            "size_inch": "3/4",
            "operating_temperature_c": 95,
            "operating_pressure_bar": 10,
            "max_price": 5000,
        },
        {
            "size_inch": "3/4",
            "operating_temperature_c": "95 °С",
            "operating_pressure_bar": "давление 10 бар",
            "max_price": "бюджет до 5 000 рублей",
        },
    )

    assert result.slots == {
        "size_inch": "3/4",
        "operating_temperature_c": 95,
        "operating_pressure_bar": 10,
        "max_price": 5000,
    }


def test_intent_llm_sanity_guard_uses_the_same_numeric_semantics() -> None:
    message = "кран 3/4 с американкой, максимум 70 градусов"
    llm_result = IntentResult(
        intent_type="attribute_request",
        category="valves",
        confidence=0.8,
        slots={
            "operating_temperature_c": 70,
            "max_price": 70,
            "diameter_mm": 4,
        },
    )
    rule_result = IntentResult(
        intent_type="attribute_request",
        category="valves",
        confidence=0.8,
    )

    checked = IntentRouterAgent()._sanity_check_llm_intent(
        llm_result,
        rule_result,
        message,
    )

    assert checked.slots == {"operating_temperature_c": 70}
    assert checked.raw["llm_output_accepted"] is False


def test_bare_numeric_temperature_is_accepted_only_for_pending_temperature() -> None:
    accepted = _interpret_numeric_payload(
        "70",
        {"operating_temperature_c": 70},
        {"operating_temperature_c": "70"},
        pending=["operating_temperature_c"],
    )
    rejected = _interpret_numeric_payload(
        "70",
        {"operating_temperature_c": 70},
        {"operating_temperature_c": "70"},
    )

    assert accepted.slots == {"operating_temperature_c": 70}
    assert rejected.slots == {}


@pytest.mark.parametrize(
    ("message", "slot", "value", "evidence"),
    [
        ("16x2", "operating_temperature_c", 2, "2"),
        ("3/4", "operating_pressure_bar", 4, "4 градуса"),
        ("25-6", "operating_temperature_c", 25, "25"),
        ("25-6", "operating_pressure_bar", 6, "6"),
    ],
)
def test_pending_resolver_rejects_components_as_scalar_measurements(
    message: str,
    slot: str,
    value: float,
    evidence: str,
) -> None:
    resolved = PendingAnswerResolver(
        _ResolverPayloadLLM(
            {
                "slots": [{"slot": slot, "value": value, "evidence": evidence}],
                "refused": [],
            }
        )
    ).resolve(
        message=message,
        question="Укажите один параметр.",
        expected_slots=[slot],
        category="pipes",
    )

    assert resolved.slots == {}
    assert resolved.accepted is False


def test_pending_resolver_accepts_bare_scalar_only_for_one_declared_slot() -> None:
    payload = {
        "slots": [
            {
                "slot": "operating_temperature_c",
                "value": 70,
                "evidence": "70",
            }
        ],
        "refused": [],
    }
    single = PendingAnswerResolver(_ResolverPayloadLLM(payload)).resolve(
        message="70",
        question="Какая максимальная температура?",
        expected_slots=["operating_temperature_c"],
        category="pipes",
    )
    multiple = PendingAnswerResolver(_ResolverPayloadLLM(payload)).resolve(
        message="70",
        question="Какие температура и давление?",
        expected_slots=["operating_temperature_c", "operating_pressure_bar"],
        category="pipes",
    )
    category_fallback = PendingAnswerResolver(_ResolverPayloadLLM(payload)).resolve(
        message="70",
        question="Новая формулировка без структурированных expected slots.",
        expected_slots=[],
        category="pipes",
    )

    assert single.slots == {"operating_temperature_c": 70.0}
    assert multiple.slots == {}
    assert category_fallback.slots == {}


def test_pending_resolver_accepts_explicit_units_with_multiple_expectations() -> None:
    resolved = PendingAnswerResolver(
        _ResolverPayloadLLM(
            {
                "slots": [
                    {
                        "slot": "operating_temperature_c",
                        "value": 70,
                        "evidence": "70 градусов",
                    },
                    {
                        "slot": "operating_pressure_bar",
                        "value": 2,
                        "evidence": "2 бара",
                    },
                ],
                "refused": [],
            }
        )
    ).resolve(
        message="70 градусов, 2 бара",
        question="Какие температура и давление?",
        expected_slots=["operating_temperature_c", "operating_pressure_bar"],
        category="pipes",
    )

    assert resolved.slots == {
        "operating_temperature_c": 70.0,
        "operating_pressure_bar": 2.0,
    }


def test_pending_resolver_accepts_named_unitless_value_in_multi_question() -> None:
    resolved = PendingAnswerResolver(
        _ResolverPayloadLLM(
            {
                "slots": [
                    {
                        "slot": "operating_pressure_bar",
                        "value": 25,
                        "evidence": "давление 25",
                    }
                ],
                "refused": [],
            }
        )
    ).resolve(
        message="давление 25",
        question="Какие температура и давление?",
        expected_slots=["operating_temperature_c", "operating_pressure_bar"],
        category="pipes",
    )

    assert resolved.slots == {"operating_pressure_bar": 25.0}
