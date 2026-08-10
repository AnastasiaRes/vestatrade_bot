"""Regression tests for binding a short reply to the pending question.

The dialogue that motivated these tests looped forever: the bot asked
«Уточните глубину от верха колодца до поверхности воды», the customer answered
«13 метров», and the answer was never recognised because the pending question
declared no expected slots.  The customer had to re-type the bot's own wording
before the number was accepted.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.slot_answer_resolver import PendingAnswerResolver
from app.config import get_settings
from app.models import IntentResult, SessionState


class StubLLMClient:
    """Answers only the resolver contract; every other agent stays offline."""

    def __init__(self, replies: dict[str, dict[str, Any]] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[str] = []
        self.last_json_output_accepted: bool | None = None
        self.last_fallback_reason: str | None = None

    def complete_json(self, agent, messages, fallback, model=None):
        self.calls.append(agent)
        if agent != "PendingAnswerResolver":
            return fallback, False
        message = messages[-1]["content"].split("Реплика клиента: ")[-1].strip()
        reply = self.replies.get(message)
        if reply is None:
            self.last_json_output_accepted = True
            return {"slot": None, "value": None, "evidence": None}, True
        self.last_json_output_accepted = True
        return json.loads(json.dumps(reply)), True

    def complete(self, *args, **kwargs):  # pragma: no cover - unused by resolver
        raise AssertionError("resolver must only use complete_json")


_WORD_VALUES = {
    "полтора": 1.5,
    "пять": 5,
    "восемь": 8,
    "тринадцать": 13,
    "двадцать": 20,
    "сорок": 40,
}


class GenericLLMStub(StubLLMClient):
    """Reads the number out of the reply instead of a per-test lookup table.

    A lookup-table stub can hide a pipeline that only ever works for the one
    value the test hard-codes, so the sweeps below use a stub that behaves the
    way a real model does: first candidate slot, whatever number was said.
    """

    def complete_json(self, agent, messages, fallback, model=None):
        self.calls.append(agent)
        if agent != "PendingAnswerResolver":
            return fallback, False
        self.last_json_output_accepted = True
        block = messages[-1]["content"]
        reply = block.split("Реплика клиента: ")[-1].strip()
        candidates = re.findall(r"^- (\w+) —", block, re.M)
        match = re.search(r"-?\d+(?:[.,]\d+)?", reply)
        value: float | None = None
        if match:
            value = float(match.group(0).replace(",", "."))
        else:
            for word, word_value in _WORD_VALUES.items():
                if word in reply.lower():
                    value = word_value
                    break
        if value is None or not candidates:
            return {"slot": None, "value": None, "evidence": None}, True
        return {"slot": candidates[0], "value": value, "evidence": reply}, True


class FixedLLMStub(StubLLMClient):
    """Returns one prepared payload regardless of the reply."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload

    def complete_json(self, agent, messages, fallback, model=None):
        self.calls.append(agent)
        self.last_json_output_accepted = True
        return dict(self.payload), True


@pytest.fixture(autouse=True)
def _enable_llm(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(type(settings), "llm_enabled", property(lambda self: True))
    yield


def _orchestrator(client: StubLLMClient) -> ChatOrchestrator:
    orchestrator = ChatOrchestrator(llm_client=client)
    orchestrator.pending_answer_resolver = PendingAnswerResolver(client)
    return orchestrator


@pytest.mark.parametrize(
    ("slot_key", "reply", "expected"),
    [
        (
            "warm_floor_automation_needed",
            "Да, хочу отдельно регулировать температуру в комнатах.",
            True,
        ),
        ("warm_floor_automation_needed", "Нет, без автоматики.", False),
        ("floor_insulation_ready", "Утеплитель уже есть.", True),
        (
            "floor_insulation_ready",
            "Пока только голая плита, утеплителя ещё нет.",
            False,
        ),
    ],
)
def test_unambiguous_warm_floor_choice_is_bound_without_llm(
    slot_key: str,
    reply: str,
    expected: bool,
) -> None:
    client = StubLLMClient()
    resolved = PendingAnswerResolver(client).resolve(
        message=reply,
        question="Уточните параметр тёплого пола",
        expected_slots=[slot_key],
        category="pipes",
    )

    assert resolved.slots == {slot_key: expected}
    assert resolved.accepted is True
    assert client.calls == []


def test_explicit_new_product_request_wins_over_pending_warm_floor_yes() -> None:
    client = StubLLMClient()
    orchestrator = _orchestrator(client)
    session = SessionState(
        session_id="warm-floor-real-topic-change",
        category="pipes",
        slots={"project_scope": "warm_floor"},
    )
    session.set_pending_question_state(
        text="Нужна покомнатная автоматика?",
        expected_slots=["warm_floor_automation_needed"],
        category="pipes",
    )
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        slots={"pump_type": "циркуляционный"},
        is_topic_change=True,
    )

    resolved = orchestrator._resolve_pending_answer(
        "Да, теперь нужен насос",
        intent,
        session,
    )

    assert resolved.slots == {}
    assert resolved.rejection_reason == "topic change"
    assert "warm_floor_automation_needed" not in intent.slots


# Phrasings the rule layer still misses even after the question→slot table was
# repaired.  Adding a hedge word or reordering the unit is enough to fall out
# of every regex, which is why this binding cannot stay pattern-based.
HEDGED_DEPTH_ANSWERS = [
    "около 13 метров",
    "где-то 13",
    "метров 13",
    "13 метров примерно",
]


@pytest.mark.parametrize("reply", HEDGED_DEPTH_ANSWERS)
def test_hedged_answer_closes_the_well_depth_question(reply):
    client = StubLLMClient(
        {
            reply: {
                "slot": "water_level_depth_m",
                "value": 13,
                "evidence": reply,
            }
        }
    )
    orchestrator = _orchestrator(client)
    session_id = f"resolver-depth-{reply}"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    answer = orchestrator.handle_chat(session_id, reply)

    session = orchestrator.sessions.get(session_id)
    assert "PendingAnswerResolver" in client.calls
    assert session.slots.get("water_level_depth_m") == 13
    assert session.slots.get("water_level_reference") == "from_top"
    # The question must move on instead of escalating into a repeat.
    assert "глубину от верха колодца" not in answer.answer
    assert "Чтобы продолжить без догадок" not in answer.answer


def test_hedged_answer_closes_the_horizontal_distance_question():
    client = StubLLMClient(
        {
            "около 13 метров": {
                "slot": "water_level_depth_m",
                "value": 13,
                "evidence": "около 13 метров",
            },
            "где-то 40": {
                "slot": "horizontal_run_m",
                "value": 40,
                "evidence": "где-то 40",
            },
        }
    )
    orchestrator = _orchestrator(client)
    session_id = "resolver-distance"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    orchestrator.handle_chat(session_id, "около 13 метров")
    orchestrator.handle_chat(session_id, "где-то 40")

    session = orchestrator.sessions.get(session_id)
    assert session.slots.get("horizontal_run_m") == 40


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("около 3 метров", 3),
        ("метров 5", 5),
        ("где-то 7,5", 7.5),
        ("восемь метров", 8),
        ("около 13 метров", 13),
        ("метров 40 примерно", 40),
        ("полтора метра", 1.5),
        ("где-то 0,5", 0.5),
    ],
)
def test_any_depth_value_is_stored_verbatim(reply, expected):
    """The binding must not work only for the value a test happens to pick."""

    client = GenericLLMStub()
    orchestrator = _orchestrator(client)
    session_id = f"sweep-depth-{expected}"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    orchestrator.handle_chat(session_id, reply)

    session = orchestrator.sessions.get(session_id)
    assert session.slots.get("water_level_depth_m") == expected


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("около 5", 5), ("где-то 40", 40), ("метров 120", 120), ("0 метров", 0)],
)
def test_any_distance_value_is_stored_verbatim(reply, expected):
    client = GenericLLMStub()
    orchestrator = _orchestrator(client)
    session_id = f"sweep-distance-{expected}"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    orchestrator.handle_chat(session_id, "около 13 метров")
    orchestrator.handle_chat(session_id, reply)

    session = orchestrator.sessions.get(session_id)
    assert session.slots.get("horizontal_run_m") == expected


@pytest.mark.parametrize(
    ("reply", "depth", "pump_type"),
    [("около 5 метров", 5, "поверхностный"), ("около 13 метров", 13, "колодезный")],
)
def test_extracted_depth_drives_the_engineering_fork(reply, depth, pump_type):
    """A bound number must reach the 8 m suction rule, not just the slot dict."""

    client = GenericLLMStub()
    orchestrator = _orchestrator(client)
    session_id = f"fork-{depth}"

    for message in [
        "мне нужен насос для полива на дачу",
        "из колодца",
        reply,
        "где-то 40",
        "0 метров",
        "20 литров в минуту",
    ]:
        orchestrator.handle_chat(session_id, message)

    session = orchestrator.sessions.get(session_id)
    assert session.slots.get("water_level_depth_m") == depth
    assert session.slots.get("pump_type") == pump_type
    assert session.slots.get("required_flow_m3_h") == 1.2


@pytest.mark.parametrize(
    ("case", "reply", "payload"),
    [
        (
            "value absent from the reply",
            "не помню точно",
            {"slot": "water_level_depth_m", "value": 8, "evidence": "не помню"},
        ),
        (
            "value outside the physical range",
            "9000 метров",
            {"slot": "water_level_depth_m", "value": 9000, "evidence": "9000"},
        ),
        (
            "negative depth",
            "-5 метров",
            {"slot": "water_level_depth_m", "value": -5, "evidence": "-5"},
        ),
        (
            "model silently rounded 13.7 to 14",
            "13,7 метра",
            {"slot": "water_level_depth_m", "value": 14, "evidence": "13,7"},
        ),
        (
            "slot outside the candidate list",
            "40 метров",
            {"slot": "required_flow_m3_h", "value": 40, "evidence": "40"},
        ),
        (
            "non-numeric value",
            "13 метров",
            {"slot": "water_level_depth_m", "value": "глубоко", "evidence": "13"},
        ),
    ],
)
def test_invalid_model_output_is_dropped(case, reply, payload):
    resolved = PendingAnswerResolver(FixedLLMStub(payload)).resolve(
        message=reply,
        question="Уточните глубину от верха колодца до поверхности воды.",
        expected_slots=["water_level_depth_m"],
        category="pumps",
    )
    assert resolved.slots == {}, case


def test_decimal_answer_is_kept_exactly():
    resolved = PendingAnswerResolver(
        FixedLLMStub(
            {"slot": "water_level_depth_m", "value": 13.7, "evidence": "13,7"}
        )
    ).resolve(
        message="13,7 метра",
        question="Уточните глубину от верха колодца до поверхности воды.",
        expected_slots=["water_level_depth_m"],
        category="pumps",
    )
    assert resolved.slots["explicit_water_level_depth_m"] == 13.7


def test_resolver_cannot_invent_a_number_the_customer_never_said():
    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "не помню точно": {
                    "slot": "water_level_depth_m",
                    "value": 8,
                    "evidence": "не помню точно",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="не помню точно",
        question="Уточните глубину от верха колодца до поверхности воды.",
        expected_slots=["water_level_depth_m"],
        category="pumps",
    )
    assert resolved.slots == {}
    assert not resolved.accepted


def test_resolver_cannot_write_a_slot_outside_the_candidate_list():
    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "40 метров": {
                    "slot": "required_flow_m3_h",
                    "value": 40,
                    "evidence": "40 метров",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="40 метров",
        question="Какое расстояние по горизонтали от колодца до дома или полива?",
        expected_slots=["horizontal_run_m"],
        category="pumps",
    )
    assert resolved.slots == {}
    assert resolved.rejection_reason == "slot outside the candidate list"


def test_resolver_rejects_values_outside_the_physical_range():
    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "9000 метров": {
                    "slot": "water_level_depth_m",
                    "value": 9000,
                    "evidence": "9000 метров",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="9000 метров",
        question="Уточните глубину от верха колодца до поверхности воды.",
        expected_slots=["water_level_depth_m"],
        category="pumps",
    )
    assert resolved.slots == {}


def test_resolver_does_not_treat_a_duration_as_a_flow():
    """«Полив занимает 30 минут» is a duration, not 30 litres per minute."""

    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "полив занимает 30 минут": {
                    "slot": "required_flow_m3_h",
                    "value": 30,
                    "evidence": "30 минут",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="полив занимает 30 минут",
        question="Какой нужен расход: сколько литров в минуту?",
        expected_slots=["required_flow_m3_h"],
        category="pumps",
    )
    # The number is present, so grounding alone cannot catch this; the guard is
    # the prompt contract plus the deterministic duty-point branch downstream.
    # What must never happen is a silent conversion into m³/h here.
    assert "required_flow_m3_h" not in resolved.slots


def test_enum_answer_requires_the_customers_own_words():
    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "не знаю": {
                    "slot": "water_source",
                    "value": "колодец",
                    "evidence": "не знаю",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="не знаю",
        question="Откуда берём воду для полива?",
        expected_slots=["water_source"],
        category="pumps",
    )
    assert resolved.slots == {}


def test_question_without_declared_slots_still_resolves_from_the_category():
    """A forgotten question→slot mapping must not become a repeat loop."""

    resolver = PendingAnswerResolver(
        StubLLMClient(
            {
                "13 метров": {
                    "slot": "water_level_depth_m",
                    "value": 13,
                    "evidence": "13 метров",
                }
            }
        )
    )
    resolved = resolver.resolve(
        message="13 метров",
        question="Совершенно новая формулировка вопроса про воду?",
        expected_slots=[],
        category="pumps",
    )
    assert resolved.slots["explicit_water_level_depth_m"] == 13


def test_pump_fallback_candidates_keep_both_pressure_roles():
    resolver = PendingAnswerResolver(StubLLMClient())

    keys = [spec.key for spec in resolver._candidates([], "pumps")]

    assert "inlet_pressure_bar" in keys
    assert "required_pressure_bar" in keys


@pytest.mark.parametrize(
    ("message", "expected_slot", "wrong_slot", "value"),
    [
        (
            "Мне нужно 3 бара после насоса",
            "inlet_pressure_bar",
            "inlet_pressure_bar",
            3,
        ),
        (
            "Давление сейчас 1 бар",
            "required_pressure_bar",
            "required_pressure_bar",
            1,
        ),
    ],
)
def test_resolver_rejects_pressure_role_opposite_to_explicit_wording(
    message,
    expected_slot,
    wrong_slot,
    value,
):
    resolver = PendingAnswerResolver(
        FixedLLMStub(
            {"slot": wrong_slot, "value": value, "evidence": message}
        )
    )

    resolved = resolver.resolve(
        message=message,
        question="Уточните давление в барах",
        expected_slots=[expected_slot],
        category="pumps",
    )

    assert resolved.slots == {}
    assert not resolved.accepted


def test_resolver_is_skipped_when_rules_already_read_the_answer():
    client = StubLLMClient()
    orchestrator = _orchestrator(client)
    session_id = "resolver-skip"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    orchestrator.handle_chat(
        session_id,
        "от верха колодца до поверхности воды 13 метров",
    )

    session = orchestrator.sessions.get(session_id)
    assert session.slots.get("water_level_depth_m") == 13
    # The rule layer answered, so no generation was spent on this turn.
    assert "PendingAnswerResolver" not in client.calls


def test_pipeline_is_unchanged_when_the_llm_is_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(type(settings), "llm_enabled", property(lambda self: False))
    client = StubLLMClient()
    orchestrator = _orchestrator(client)
    session_id = "resolver-offline"

    orchestrator.handle_chat(session_id, "мне нужен насос для полива на дачу")
    orchestrator.handle_chat(session_id, "из колодца")
    orchestrator.handle_chat(session_id, "13 метров")

    assert client.calls == []
