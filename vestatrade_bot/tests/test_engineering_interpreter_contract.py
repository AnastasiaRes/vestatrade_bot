from __future__ import annotations

from typing import Any

import pytest

from app.agents.engineering_interpreter import (
    ENGINEERING_INTERPRETER_PROMPT,
    EngineeringInterpreterAgent,
)
from app.models import IntentResult, SessionState


class _JSONLLM:
    last_json_output_accepted = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, agent, messages, fallback):
        return self.payload, True


def _interpret(
    message: str,
    slots: dict[str, Any],
    *,
    evidence: dict[str, str] | None = None,
    provenance: dict[str, str] | None = None,
    pending: list[str] | None = None,
    reply: str | None = None,
):
    payload = {
        "handled": True,
        "continuation": True,
        "intent_type": "attribute_request",
        "category": "pumps",
        "project_scope": "water",
        "slots": slots,
        "slot_evidence": evidence or {},
        "slot_provenance": provenance or {},
        "assumptions": [],
        "missing_slot_keys": [],
        "needs_clarification": False,
        "clarifying_question": None,
        "ready_for_catalog_selection": False,
        "response_mode": "project_progress",
        "reply": reply,
    }
    session = SessionState(session_id="contract")
    session.pending_slot_keys = pending or []
    return EngineeringInterpreterAgent(_JSONLLM(payload)).interpret(
        message,
        IntentResult(intent_type="attribute_request", category="pumps"),
        session,
    )


def test_prompt_has_formulas_but_no_concrete_240_square_metre_example() -> None:
    normalized = ENGINEERING_INTERPRETER_PROMPT.lower()

    assert "area*6.5" in normalized
    assert "well_depth_m = well_ring_count*0.9" in normalized
    assert "240" not in normalized


def test_llm_warm_floor_calculations_are_removed_even_with_valid_json() -> None:
    message = "60 метров"
    result = _interpret(
        message,
        {
            "warm_floor_area_m2": 60,
            "area_m2": 60,
            "warm_floor_pipe_min_m": 1560,
            "warm_floor_pipe_max_m": 1680,
            "warm_floor_contours": 20,
            "warm_floor_collector_count": 2,
            "warm_floor_collector_outlets": 10,
        },
        evidence={
            "warm_floor_area_m2": message,
            "area_m2": message,
            "warm_floor_pipe_min_m": message,
            "warm_floor_pipe_max_m": message,
            "warm_floor_contours": message,
            "warm_floor_collector_count": message,
            "warm_floor_collector_outlets": message,
        },
        provenance={
            key: "pending_answer"
            for key in (
                "warm_floor_area_m2",
                "area_m2",
                "warm_floor_pipe_min_m",
                "warm_floor_pipe_max_m",
                "warm_floor_contours",
                "warm_floor_collector_count",
                "warm_floor_collector_outlets",
            )
        },
        pending=["warm_floor_area_m2"],
        reply="Для 60 м² нужно 1560–1680 м трубы и 20 контуров.",
    )

    assert result.output_accepted is True
    assert result.slots == {"warm_floor_area_m2": 60}
    assert result.slot_evidence == {"warm_floor_area_m2": message}
    assert result.slot_provenance == {"warm_floor_area_m2": "pending_answer"}
    assert result.reply is None


def test_llm_cannot_persist_depth_and_flow_conversions_from_rings_and_litres() -> None:
    message = "Три кольца, зеркало воды на двух кольцах, расход литров 100"
    source_slots = {
        "water_source": "колодец",
        "well_ring_count": 3,
        "well_depth_m": 2.7,
        "water_level_ring_count": 2,
        "water_level_reference": "ambiguous",
        "dynamic_water_level_m": 1.8,
        "required_flow_l_min": 100,
        "required_flow_m3_h": 6,
        "flow_unit_assumed": True,
        "flow_unit_status": "assumed",
        "ring_height_assumed": True,
    }
    result = _interpret(
        message,
        source_slots,
        evidence={key: message for key in source_slots},
        provenance={key: "current_message" for key in source_slots},
        reply=(
            "Три кольца — 2,7 м, зеркало — 1,8 м; 100 л считаю как "
            "100 л/мин, то есть 6 м³/ч."
        ),
    )

    assert result.slots == {
        "water_source": "колодец",
        "well_ring_count": 3,
        "water_level_ring_count": 2,
        "water_level_reference": "ambiguous",
        "flow_unit_status": "assumed",
    }
    assert result.reply is None
    for derived in (
        "well_depth_m",
        "dynamic_water_level_m",
        "required_flow_m3_h",
        "flow_unit_assumed",
        "ring_height_assumed",
    ):
        assert derived not in result.slots


def test_current_message_numbers_and_provenance_guard_raw_facts() -> None:
    message = "100 литров в минуту, до дома 25 метров, поднять на 4 метра"
    result = _interpret(
        message,
        {
            "required_flow_l_min": 100,
            "required_flow_m3_h": 6,
            "flow_unit_status": "confirmed_per_minute",
            "horizontal_run_m": 25,
            "lift_height_m": 8,
            "area_m2": 120,
        },
        evidence={
            "required_flow_l_min": "100 литров в минуту",
            "required_flow_m3_h": "100 литров в минуту",
            "flow_unit_status": "100 литров в минуту",
            "horizontal_run_m": "до дома 25 метров",
            "lift_height_m": "поднять на 4 метра",
            "area_m2": "120 м²",
        },
        provenance={
            "required_flow_l_min": "current_message",
            "required_flow_m3_h": "current_message",
            "flow_unit_status": "current_message",
            "horizontal_run_m": "current_message",
            "lift_height_m": "current_message",
            "area_m2": "dialog_context",
        },
    )

    assert result.slots == {
        "required_flow_l_min": 100,
        "flow_unit_status": "confirmed_per_minute",
        "horizontal_run_m": 25,
    }
    assert "required_flow_m3_h" not in result.slots
    assert "lift_height_m" not in result.slots
    assert "area_m2" not in result.slots


def test_interpreter_accepts_explicit_raw_metric_facts_not_derived_depths() -> None:
    message = (
        "Колодец глубиной 5 метров; от верха колодца до воды 2 метра; "
        "столб воды 3 метра; высота кольца 1 метр"
    )
    raw_slots = {
        "ring_height_m": 1,
        "explicit_well_depth_m": 5,
        "explicit_water_level_depth_m": 2,
        "explicit_water_column_depth_m": 3,
        # A local model may also return the corresponding calculations; those
        # are deliberately untrusted even when numerically consistent.
        "well_depth_m": 5,
        "water_level_depth_m": 2,
        "water_column_depth_m": 3,
    }
    result = _interpret(
        message,
        raw_slots,
        evidence={
            "ring_height_m": "высота кольца 1 метр",
            "explicit_well_depth_m": "Колодец глубиной 5 метров",
            "explicit_water_level_depth_m": "от верха колодца до воды 2 метра",
            "explicit_water_column_depth_m": "столб воды 3 метра",
            "well_depth_m": "Колодец глубиной 5 метров",
            "water_level_depth_m": "от верха колодца до воды 2 метра",
            "water_column_depth_m": "столб воды 3 метра",
        },
        provenance={key: "current_message" for key in raw_slots},
    )

    assert result.slots == {
        "ring_height_m": 1,
        "explicit_well_depth_m": 5,
        "explicit_water_level_depth_m": 2,
        "explicit_water_column_depth_m": 3,
    }
    assert "well_depth_m" not in result.slots
    assert "water_level_depth_m" not in result.slots
    assert "water_column_depth_m" not in result.slots


def test_water_level_reference_accepts_only_meaning_supported_by_message() -> None:
    ambiguous = _interpret(
        "зеркало воды на двух кольцах",
        {
            "water_level_ring_count": 2,
            "water_level_reference": "ambiguous",
        },
    )
    fabricated_direction = _interpret(
        "зеркало воды на двух кольцах",
        {"water_level_reference": "from_top"},
    )
    explicit_direction = _interpret(
        "два кольца от верха до воды",
        {"water_level_reference": "from_top"},
    )

    assert ambiguous.slots == {
        "water_level_ring_count": 2,
        "water_level_reference": "ambiguous",
    }
    assert fabricated_direction.slots == {}
    assert explicit_direction.slots == {"water_level_reference": "from_top"}


def test_pressure_roles_are_grounded_by_pending_question_and_wording() -> None:
    inlet = _interpret(
        "Давление сейчас 1 бар",
        {
            "inlet_pressure_bar": 1,
            "required_pressure_bar": 1,
        },
        pending=["inlet_pressure_bar"],
    )
    required = _interpret(
        "Нужно 3 бара после насоса",
        {
            "inlet_pressure_bar": 3,
            "required_pressure_bar": 3,
        },
        pending=["required_pressure_bar"],
    )

    assert inlet.slots == {"inlet_pressure_bar": 1}
    assert required.slots == {"required_pressure_bar": 3}


def test_explicit_pressure_role_overrides_opposite_pending_slot() -> None:
    required_while_waiting_for_inlet = _interpret(
        "Мне нужно 3 бара после насоса",
        {
            "inlet_pressure_bar": 3,
            "required_pressure_bar": 3,
        },
        pending=["inlet_pressure_bar"],
    )
    inlet_while_waiting_for_required = _interpret(
        "Давление сейчас 1 бар",
        {
            "inlet_pressure_bar": 1,
            "required_pressure_bar": 1,
        },
        pending=["required_pressure_bar"],
    )

    assert required_while_waiting_for_inlet.slots == {
        "required_pressure_bar": 3
    }
    assert inlet_while_waiting_for_required.slots == {
        "inlet_pressure_bar": 1
    }


def test_slot_evidence_prevents_llm_from_swapping_two_pressures() -> None:
    message = "Давление сейчас 1 бар, нужно 3 бара после насоса"
    swapped = _interpret(
        message,
        {
            "inlet_pressure_bar": 3,
            "required_pressure_bar": 1,
        },
        evidence={
            "inlet_pressure_bar": "нужно 3 бара после насоса",
            "required_pressure_bar": "Давление сейчас 1 бар",
        },
        provenance={
            "inlet_pressure_bar": "current_message",
            "required_pressure_bar": "current_message",
        },
    )

    assert swapped.slots == {}


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {"inlet_pressure_bar": "нужно 3 бара после насоса"},
    ],
)
def test_two_pressure_facts_require_evidence_for_each_slot(
    evidence,
) -> None:
    message = "Давление сейчас 1 бар, нужно 3 бара после насоса"
    swapped = _interpret(
        message,
        {
            "inlet_pressure_bar": 3,
            "required_pressure_bar": 1,
        },
        evidence=evidence,
        provenance={
            "inlet_pressure_bar": "current_message",
            "required_pressure_bar": "current_message",
        },
    )

    assert swapped.slots == {}
