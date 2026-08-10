"""Regressions for mixed pump facts, targeted refusals and replacements."""

from __future__ import annotations

from app.agents.slot_answer_resolver import PendingAnswerResolver
from app.agents.slot_filling import SlotFillingAgent
from app.models import IntentResult, SessionState


class _UnavailableLLM:
    last_json_output_accepted = True

    def complete_json(self, agent, messages, fallback):
        return fallback, False


def test_mixed_refusal_is_bound_only_to_flow_not_supplied_mounting_length() -> None:
    resolver = PendingAnswerResolver(_UnavailableLLM())

    refused = resolver.detect_refusals(
        message=("расход не знаю, напор нужен 6 метров, " "монтажная длина 180 мм"),
        expected_slots=[
            "required_flow_m3_h",
            "head_m",
            "mounting_length_mm",
            "connection_size",
        ],
        category="pumps",
    )

    assert refused == ["required_flow_m3_h"]


def test_known_head_and_length_clear_stale_deferrals_and_shape_next_question() -> None:
    session = SessionState(
        session_id="mixed-pump-facts",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "pump_use": "отопление",
            "pump_selection_mode": "новый подбор",
            "deferred_slot_keys": [
                "required_flow_m3_h",
                "head_m",
                "mounting_length_mm",
            ],
        },
    )
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        slots={"head_m": 6.0, "mounting_length_mm": 180},
    )

    result = SlotFillingAgent().fill(
        "расход не знаю, напор нужен 6 метров, монтажная длина 180 мм",
        intent,
        session,
    )

    assert result.slots["head_m"] == 6.0
    assert result.slots["mounting_length_mm"] == 180
    assert result.slots["deferred_slot_keys"] == ["required_flow_m3_h"]
    assert result.needs_clarification is True
    answer = (result.question or "").lower()
    assert "записал: напор 6 м" in answer
    assert "монтажная длина 180 мм" in answer
    assert "расчётный расход" in answer
    assert "присоединение" in answer
    assert "без расчётного расхода и напора" not in answer
    assert "напор обычно пишется" not in answer


def test_bare_unknown_does_not_erase_core_parameters_from_previous_turn() -> None:
    session = SessionState(
        session_id="bare-unknown-after-known-facts",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "pump_selection_mode": "новый подбор",
            "head_m": 6.0,
            "mounting_length_mm": 180,
        },
    )

    result = SlotFillingAgent().fill(
        "не знаю",
        IntentResult(intent_type="attribute_request", category="pumps"),
        session,
    )

    answer = (result.question or "").lower()
    assert "записал: напор 6 м" in answer
    assert "монтажная длина 180 мм" in answer
    assert "напор обычно пишется" not in answer
    assert "без расчётного расхода и напора" not in answer


def test_unknown_old_marking_moves_replacement_funnel_to_installation_type(
    orchestrator,
) -> None:
    first = orchestrator.handle_chat(
        "replacement-without-marking",
        "старый насос есть, нужен на замену",
    )
    second = orchestrator.handle_chat(
        "replacement-without-marking",
        "маркировку не знаю",
    )

    assert "модел" in first.answer.lower() or "маркиров" in first.answer.lower()
    answer = second.answer.lower()
    assert "не буду просить её повторно" in answer
    assert "где он работал" in answer
    assert "отоплен" in answer and "откач" in answer
    assert second.debug["slots"]["pump_replacement"] is True
    assert "old_model" in second.debug["slots"]["deferred_slot_keys"]


def test_known_replacement_mode_is_not_asked_again() -> None:
    session = SessionState(
        session_id="known-replacement",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "pump_use": "отопление",
            "pump_replacement": True,
            "pump_selection_mode": "замена",
            "deferred_slot_keys": ["old_model"],
        },
    )
    result = SlotFillingAgent().fill(
        "маркировку не знаю",
        IntentResult(intent_type="attribute_request", category="pumps"),
        session,
    )

    answer = (result.question or "").lower()
    assert "для замены не хватает" in answer
    assert "маркировку оставил неизвестной" in answer
    assert "это замена старого или новый подбор" not in answer
