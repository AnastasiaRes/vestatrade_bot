from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.agents.orchestrator import ChatOrchestrator, WARM_FLOOR_FUNNEL
from app.config import get_settings
from app.openrouter_client import LLMResult


class _EngineeringJSONLLM:
    last_json_output_accepted = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.engineering_prompt = ""

    def complete_json(self, agent, messages, fallback):
        if agent == "EngineeringInterpreterAgent":
            self.engineering_prompt = messages[-1]["content"]
            return self.payload, True
        return fallback, False

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=None, llm_used=False, fallback_reason="not needed")


class _MalformedThenValidLLM(_EngineeringJSONLLM):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.last_json_output_accepted = False
        self.calls = 0

    def complete_json(self, agent, messages, fallback):
        if not agent.startswith("EngineeringInterpreterAgent"):
            return fallback, False
        self.calls += 1
        if self.calls == 1:
            self.last_json_output_accepted = False
            return fallback, True
        self.last_json_output_accepted = True
        return self.payload, True


class _BudgetTrackingLLM(_EngineeringJSONLLM):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.budget_active = False
        self.budget_entries = 0

    @contextmanager
    def request_budget(self):
        self.budget_entries += 1
        self.budget_active = True
        try:
            yield
        finally:
            self.budget_active = False

    def complete_json(self, agent, messages, fallback):
        assert self.budget_active is True
        return super().complete_json(agent, messages, fallback)


def _live_settings():
    return get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )


def test_llm_interpreter_keeps_warm_floor_context_for_bare_area() -> None:
    llm = _EngineeringJSONLLM(
        {
            "handled": True,
            "continuation": True,
            "intent_type": "attribute_request",
            "category": "pipes",
            "project_scope": "warm_floor",
            "slots": {
                "warm_floor_area_m2": 240,
                "area_m2": 240,
                "warm_floor_pipe_min_m": 1560,
                "warm_floor_pipe_max_m": 1680,
                "warm_floor_contours": 20,
                "warm_floor_collector_count": 2,
            },
            "assumptions": ["шаг укладки 15 см", "контур ориентировочно 80 м"],
            "missing_slot_keys": ["warm_floor_type"],
            "needs_clarification": True,
            "clarifying_question": "Пол водяной от котла или электрический?",
            "ready_for_catalog_selection": False,
            "response_mode": "project_progress",
            "reply": (
                "Принято: 240 м² тёплого пола. Ориентир — 1560–1680 м трубы, "
                "около 20 контуров и два коллектора 10+10. "
                "Пол водяной от котла или электрический?"
            ),
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)
    session = bot.sessions.get("llm-warm-floor")
    session.slots.update(
        {
            "project_scope": "warm_floor",
            "scope_funnel": "warm_floor",
            "has_warm_floor": True,
        }
    )
    session.pending_question = WARM_FLOOR_FUNNEL
    session.pending_category = "pipes"
    session.pending_slot_keys = ["warm_floor_area_m2"]
    session.history = [
        {"role": "assistant", "content": WARM_FLOOR_FUNNEL},
    ]
    bot.sessions.save(session)

    response = bot.handle_chat("llm-warm-floor", "240 метров")

    assert "1560–1680" in response.answer
    assert "Труба для чего" not in response.answer
    assert response.debug["slots"]["warm_floor_area_m2"] == 240
    assert response.debug["slots"]["warm_floor_contours"] == 20
    # The LLM extracts the area; the deterministic layer owns the calculation
    # and the final engineering response.
    assert response.debug["final_answer_source"] == "deterministic"
    assert response.debug["engineering_llm_output_accepted"] is True
    assert "какая площадь" in llm.engineering_prompt.lower()


def test_llm_interpreter_does_not_override_well_conversions_or_ambiguity() -> None:
    llm = _EngineeringJSONLLM(
        {
            "handled": True,
            "continuation": True,
            "intent_type": "attribute_request",
            "category": "pumps",
            "project_scope": "water",
            "slots": {
                "water_source": "колодец",
                "pump_use": "водоснабжение",
                "well_ring_count": 3,
                "well_depth_m": 2.7,
                "water_level_ring_count": 2,
                "dynamic_water_level_m": 1.8,
                "required_flow_l_min": 100,
                "required_flow_m3_h": 6,
                "flow_unit_assumed": True,
                "ring_height_assumed": True,
            },
            "assumptions": ["одно кольцо принято за 0,9 м"],
            "missing_slot_keys": ["flow_unit_confirmation"],
            "needs_clarification": True,
            "clarifying_question": "100 литров в минуту или это общий объём?",
            "ready_for_catalog_selection": False,
            "response_mode": "clarify",
            "reply": (
                "Принял: три кольца — около 2,7 м, зеркало воды — около 1,8 м. "
                "100 л предварительно считаю как 100 л/мин, то есть 6 м³/ч. "
                "Подтвердите: это литры в минуту или общий объём?"
            ),
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)

    response = bot.handle_chat(
        "llm-well",
        "Три кольца, зеркало воды на двух кольцах, расход литров 100 или больше",
    )

    assert "2,7 м" in response.answer
    assert "от верха" in response.answer and "от дна" in response.answer
    assert response.debug["slots"]["required_flow_m3_h"] == 6
    assert response.debug["slots"]["water_level_reference"] == "ambiguous"
    assert "dynamic_water_level_m" not in response.debug["slots"]
    assert response.debug["final_answer_source"] == "deterministic"


def test_engineering_interpreter_retries_malformed_json_before_fallback() -> None:
    llm = _MalformedThenValidLLM(
        {
            "handled": True,
            "continuation": True,
            "intent_type": "attribute_request",
            "category": "pumps",
            "project_scope": "water",
            "slots": {"water_source": "колодец", "well_depth_m": 2.7},
            "assumptions": ["кольцо 0,9 м"],
            "missing_slot_keys": ["horizontal_run_m"],
            "needs_clarification": True,
            "clarifying_question": "Какое расстояние от колодца до дома?",
            "ready_for_catalog_selection": False,
            "response_mode": "clarify",
            "reply": "Принял глубину около 2,7 м. Какое расстояние от колодца до дома?",
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)

    response = bot.handle_chat("llm-json-retry", "Колодец три кольца")

    assert llm.calls == 2
    assert response.debug["final_answer_source"] == "deterministic"
    assert response.debug["engineering_llm_output_accepted"] is True


def test_deterministic_safety_net_keeps_warm_floor_context_without_llm() -> None:
    bot = ChatOrchestrator(products=[])

    first = bot.handle_chat("fallback-warm-floor", "Тёплые полы есть?")
    second = bot.handle_chat("fallback-warm-floor", "240 метров")

    assert "площад" in first.answer.lower()
    assert "1560–1680" in second.answer
    assert "около 20 контуров" in second.answer
    assert "Труба для чего" not in second.answer


def test_deterministic_safety_net_understands_ring_phrasing_without_llm() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "fallback-well",
        "Три кольца, зеркало воды на двух кольцах, расход литров 100 или больше",
    )

    assert "от верха" in response.answer and "от дна" in response.answer
    assert response.debug["slots"]["well_depth_m"] == 2.7
    assert response.debug["slots"]["well_ring_count"] == 3
    assert response.debug["slots"]["water_level_reference"] == "ambiguous"
    assert response.debug["slots"]["required_flow_m3_h"] == 6
    assert "dynamic_water_level_m" not in response.debug["slots"]
    assert "water_quality" not in response.debug["slots"]


def test_orchestrator_opens_one_shared_llm_budget_per_turn() -> None:
    llm = _BudgetTrackingLLM(
        {
            "handled": True,
            "continuation": True,
            "intent_type": "attribute_request",
            "category": "pumps",
            "project_scope": "water",
            "slots": {"water_source": "колодец", "well_depth_m": 2.7},
            "assumptions": ["кольцо 0,9 м"],
            "missing_slot_keys": ["horizontal_run_m"],
            "needs_clarification": True,
            "clarifying_question": "Какое расстояние от колодца до дома?",
            "ready_for_catalog_selection": False,
            "response_mode": "clarify",
            "reply": "Принял глубину 2,7 м. Какое расстояние до дома?",
        }
    )
    bot = ChatOrchestrator(settings=_live_settings(), products=[], llm_client=llm)

    response = bot.handle_chat("shared-budget", "Колодец три кольца")

    assert response.debug["final_answer_source"] == "deterministic"
    assert llm.budget_entries == 1
    assert llm.budget_active is False
