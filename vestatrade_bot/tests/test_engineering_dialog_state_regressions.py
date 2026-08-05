from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.intent_router import IntentRouterAgent
from app.config import get_settings
from app.models import SessionState
from app.openrouter_client import LLMResult


def _interpretation(
    *,
    category: str,
    project_scope: str,
    slots: dict[str, Any] | None = None,
    continuation: bool = True,
) -> dict[str, Any]:
    """A valid LLM extraction candidate without a user-facing LLM answer."""

    return {
        "handled": True,
        "continuation": continuation,
        "intent_type": "attribute_request",
        "category": category,
        "project_scope": project_scope,
        "slots": slots or {},
        "assumptions": [],
        "missing_slot_keys": [],
        "needs_clarification": False,
        "clarifying_question": None,
        "ready_for_catalog_selection": False,
        "response_mode": "none",
        "reply": None,
    }


class _QueuedEngineeringLLM:
    """Return deterministic engineering candidates and never call a real LLM."""

    last_json_output_accepted = True
    last_fallback_reason = None

    def __init__(self, payloads: Iterable[dict[str, Any]]) -> None:
        self._payloads = iter(payloads)
        self.engineering_calls = 0

    def complete_json(self, agent, messages, fallback):
        if not agent.startswith("EngineeringInterpreterAgent"):
            return fallback, False
        self.engineering_calls += 1
        self.last_json_output_accepted = True
        return next(self._payloads), True

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=None, llm_used=False, fallback_reason="not needed")


def _bot(*payloads: dict[str, Any]) -> ChatOrchestrator:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    return ChatOrchestrator(
        settings=settings,
        products=[],
        llm_client=_QueuedEngineeringLLM(payloads),
    )


@pytest.mark.parametrize(
    ("area_m2", "pipe_min_m", "pipe_max_m", "contours", "collectors"),
    [
        (60, 390, 420, 5, 1),
        (80, 520, 560, 7, 1),
        (100, 650, 700, 9, 1),
        (240, 1560, 1680, 20, 2),
    ],
)
def test_warm_floor_derived_values_are_owned_by_deterministic_code(
    area_m2: int,
    pipe_min_m: int,
    pipe_max_m: int,
    contours: int,
    collectors: int,
) -> None:
    bot = _bot(
        _interpretation(
            category="pipes",
            project_scope="warm_floor",
            slots={"warm_floor_area_m2": area_m2},
        )
    )

    response = bot.handle_chat(
        f"warm-floor-{area_m2}",
        f"Нужен водяной тёплый пол площадью {area_m2} м²",
    )
    slots = response.debug["slots"]

    assert slots["warm_floor_area_m2"] == area_m2
    assert slots["warm_floor_pipe_min_m"] == pipe_min_m
    assert slots["warm_floor_pipe_max_m"] == pipe_max_m
    assert slots["warm_floor_contours"] == contours
    assert slots["warm_floor_collector_count"] == collectors


def test_explicit_warm_floor_area_wins_over_stale_llm_example() -> None:
    bot = _bot(
        _interpretation(
            category="pipes",
            project_scope="warm_floor",
            slots={
                "warm_floor_area_m2": 240,
                "area_m2": 240,
                "warm_floor_pipe_min_m": 1560,
                "warm_floor_pipe_max_m": 1680,
                "warm_floor_contours": 20,
                "warm_floor_collector_count": 2,
            },
        )
    )

    response = bot.handle_chat(
        "warm-floor-explicit-wins",
        "Нужен водяной тёплый пол площадью 60 м²",
    )
    slots = response.debug["slots"]

    assert slots["warm_floor_area_m2"] == 60
    assert slots["warm_floor_pipe_min_m"] == 390
    assert slots["warm_floor_pipe_max_m"] == 420
    assert slots["warm_floor_contours"] == 5
    assert slots["warm_floor_collector_count"] == 1


def test_warm_floor_correction_recalculates_all_derived_values() -> None:
    bot = _bot(
        _interpretation(
            category="pipes",
            project_scope="warm_floor",
            slots={"warm_floor_area_m2": 100},
        ),
        # Simulate a model that ignored the correction and repeated old facts.
        _interpretation(
            category="pipes",
            project_scope="warm_floor",
            slots={
                "warm_floor_area_m2": 100,
                "warm_floor_pipe_min_m": 650,
                "warm_floor_pipe_max_m": 700,
                "warm_floor_contours": 9,
            },
        ),
    )

    bot.handle_chat(
        "warm-floor-correction",
        "Нужен водяной тёплый пол площадью 100 м²",
    )
    corrected = bot.handle_chat(
        "warm-floor-correction",
        "Исправление: площадь 80 м², не 100",
    )
    slots = corrected.debug["slots"]

    assert slots["warm_floor_area_m2"] == 80
    assert slots["warm_floor_pipe_min_m"] == 520
    assert slots["warm_floor_pipe_max_m"] == 560
    assert slots["warm_floor_contours"] == 7
    assert slots["warm_floor_collector_count"] == 1


def test_well_dialog_understands_distance_lift_and_flow_confirmation() -> None:
    pump_turn = _interpretation(
        category="pumps",
        project_scope="water",
        # The fake model deliberately extracts nothing: conversational forms
        # must remain understood by the deterministic safety layer.
        slots={},
    )
    bot = _bot(*(pump_turn for _ in range(5)))
    session_id = "well-conversational"

    bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 метра",
    )
    distance = bot.handle_chat(session_id, "до дома 25 метров")
    assert distance.debug["slots"]["horizontal_run_m"] == 25

    lift = bot.handle_chat(session_id, "поднять ещё на 4 метра")
    assert lift.debug["slots"]["lift_height_m"] == 4

    assumed = bot.handle_chat(session_id, "расход литров 100")
    assumed_slots = assumed.debug["slots"]
    assert assumed_slots["required_flow_l_min"] == 100
    assert assumed_slots["required_flow_m3_h"] == 6
    assert assumed_slots["flow_unit_assumed"] is True
    assert "flow_unit_confirmation" in bot.sessions.get(session_id).pending_slot_keys

    confirmed = bot.handle_chat(session_id, "да, именно в минуту")
    confirmed_slots = confirmed.debug["slots"]
    assert confirmed_slots["required_flow_l_min"] == 100
    assert confirmed_slots["required_flow_m3_h"] == 6
    assert not confirmed_slots.get("flow_unit_assumed")


def test_explicit_litres_per_minute_never_sets_an_assumption() -> None:
    bot = _bot(
        _interpretation(category="pumps", project_scope="water", slots={})
    )
    session_id = "well-explicit-flow-unit"

    response = bot.handle_chat(
        session_id,
        "Насос для колодца, расход 100 литров в минуту",
    )
    slots = response.debug["slots"]

    assert slots["required_flow_l_min"] == 100
    assert slots["required_flow_m3_h"] == 6
    assert not slots.get("flow_unit_assumed")
    assert "flow_unit_confirmation" not in bot.sessions.get(session_id).pending_slot_keys


def test_ambiguous_well_mirror_is_not_silently_converted() -> None:
    bot = _bot(
        _interpretation(
            category="pumps",
            project_scope="water",
            # Rings are raw observations. Their physical meaning and converted
            # depths remain deterministic-code responsibilities.
            slots={"well_ring_count": 3, "water_level_ring_count": 2},
        )
    )
    session_id = "well-ambiguous-mirror"

    response = bot.handle_chat(
        session_id,
        "Колодец три кольца, зеркало воды на двух кольцах",
    )
    slots = response.debug["slots"]
    session = bot.sessions.get(session_id)

    assert slots["well_ring_count"] == 3
    assert slots["well_depth_m"] == pytest.approx(2.7)
    assert slots["water_level_ring_count"] == 2
    assert slots["water_level_reference"] == "ambiguous"
    assert "dynamic_water_level_m" not in slots
    assert "water_column_depth_m" not in slots
    assert session.pending_question_id == "well.water_level_reference"
    assert session.pending_slot_keys == ["water_level_reference"]


def test_ambiguous_well_reference_question_is_asked_only_once() -> None:
    pump_turn = _interpretation(category="pumps", project_scope="water", slots={})
    bot = _bot(pump_turn, pump_turn)
    session_id = "well-reference-once"

    first = bot.handle_chat(
        session_id,
        "Колодец три кольца, зеркало воды на двух кольцах",
    )
    second = bot.handle_chat(session_id, "до дома 25 метров")
    session = bot.sessions.get(session_id)

    assert "от верха" in first.answer and "от дна" in first.answer
    assert not ("от верха" in second.answer and "от дна" in second.answer)
    assert second.debug["slots"]["horizontal_run_m"] == 25
    assert session.pending_question_id == "well.lift_height"


@pytest.mark.parametrize(
    (
        "clarification",
        "reference",
        "present_depth_key",
        "present_depth_m",
    ),
    [
        (
            "От верха колодца до воды",
            "from_top",
            "dynamic_water_level_m",
            1.8,
        ),
        (
            "От дна колодца до воды",
            "from_bottom",
            "water_column_depth_m",
            1.8,
        ),
    ],
)
def test_ambiguous_well_mirror_is_resolved_from_context(
    clarification: str,
    reference: str,
    present_depth_key: str,
    present_depth_m: float,
) -> None:
    bot = _bot(
        _interpretation(
            category="pumps",
            project_scope="water",
            slots={"well_ring_count": 3, "water_level_ring_count": 2},
        ),
        _interpretation(category="pumps", project_scope="water", slots={}),
    )
    session_id = f"well-reference-{reference}"

    bot.handle_chat(
        session_id,
        "Колодец три кольца, зеркало воды на двух кольцах",
    )
    resolved = bot.handle_chat(session_id, clarification)
    slots = resolved.debug["slots"]

    assert slots["water_level_ring_count"] == 2
    assert slots["water_level_reference"] == reference
    assert slots[present_depth_key] == pytest.approx(present_depth_m)
    # Once the reference direction is known, the total three-ring depth makes
    # the complementary value deterministic too: 2 rings one way leave 1 ring
    # the other way.  This is code-owned geometry, not an LLM assumption.
    assert slots["water_level_depth_m"] == pytest.approx(
        1.8 if reference == "from_top" else 0.9
    )
    assert slots["water_column_depth_m"] == pytest.approx(
        0.9 if reference == "from_top" else 1.8
    )
    if reference == "from_bottom":
        assert "dynamic_water_level_m" not in slots


def test_topic_switch_and_return_restore_only_the_pump_goal() -> None:
    bot = _bot(
        _interpretation(
            category="pumps",
            project_scope="water",
            slots={
                "water_source": "колодец",
                "pump_use": "водоснабжение",
                "dynamic_water_level_m": 1.8,
            },
        ),
        _interpretation(
            category="boilers",
            project_scope="heating",
            slots={"boiler_type": "электрический", "area_m2": 120},
        ),
        _interpretation(
            category="pumps",
            project_scope="water",
            slots={},
            continuation=True,
        ),
    )
    session_id = "separate-engineering-goals"

    pump = bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 м",
    )
    assert pump.debug["category"] == "pumps"

    boiler = bot.handle_chat(
        session_id,
        "Теперь нужен электрический котёл для дома 120 м²",
    )
    assert boiler.debug["category"] == "boilers"
    assert boiler.debug["slots"]["area_m2"] == 120
    assert "dynamic_water_level_m" not in boiler.debug["slots"]
    assert "water_source" not in boiler.debug["slots"]

    restored = bot.handle_chat(session_id, "Вернёмся к насосу")
    restored_slots = restored.debug["slots"]
    assert restored.debug["category"] == "pumps"
    assert restored_slots["water_source"] == "колодец"
    assert restored_slots["dynamic_water_level_m"] == pytest.approx(1.8)
    assert "area_m2" not in restored_slots
    assert "boiler_type" not in restored_slots


def test_pending_question_has_stable_id_and_does_not_loop_verbatim() -> None:
    pump_turn = _interpretation(category="pumps", project_scope="water", slots={})
    bot = _bot(pump_turn, pump_turn, pump_turn)
    session_id = "well-question-repeat"

    first = bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 м",
    )
    first_session = bot.sessions.get(session_id)
    assert first_session.pending_question_id == "well.horizontal_distance"
    assert first_session.pending_slot_keys == ["horizontal_run_m"]

    second = bot.handle_chat(session_id, "Не знаю")
    second_session = bot.sessions.get(session_id)
    assert second_session.pending_question_id == "well.horizontal_distance"
    assert second_session.pending_slot_keys == ["horizontal_run_m"]
    assert second.answer.strip() != first.answer.strip()

    third = bot.handle_chat(session_id, "Пока не могу сказать")
    third_session = bot.sessions.get(session_id)
    assert third.answer.strip() != second.answer.strip()
    assert third.products == []
    assert third_session.pending_question_id != "well.horizontal_distance"


def test_household_well_phrase_uses_explicit_ring_height_and_water_column() -> None:
    bot = _bot(
        _interpretation(category="pumps", project_scope="water", slots={})
    )

    response = bot.handle_chat(
        "well-household-explanation",
        (
            "Колодец делают из колец бетонных:) кольцо 1 метр, три кольца "
            "3 метра:) зеркало воды, это когда колодец из 3 колец, но вода "
            "от дна до поверхности воды, это 2 метра"
        ),
    )
    slots = response.debug["slots"]

    assert response.debug["category"] == "pumps"
    assert slots["water_source"] == "колодец"
    assert slots["well_ring_count"] == 3
    assert slots["ring_height_m"] == 1
    assert slots["well_depth_m"] == pytest.approx(3)
    assert slots["water_level_reference"] == "from_bottom"
    assert slots["explicit_water_column_depth_m"] == pytest.approx(2)
    assert slots["water_column_depth_m"] == pytest.approx(2)
    assert slots["water_level_depth_m"] == pytest.approx(1)


def test_total_volume_correction_clears_assumed_flow() -> None:
    pump_turn = _interpretation(category="pumps", project_scope="water", slots={})
    bot = _bot(*(pump_turn for _ in range(5)))
    session_id = "well-total-volume-correction"

    bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 метра",
    )
    bot.handle_chat(session_id, "до дома 25 метров")
    bot.handle_chat(session_id, "поднять ещё на 4 метра")
    assumed = bot.handle_chat(session_id, "расход литров 100")
    assert assumed.debug["slots"]["required_flow_l_min"] == 100
    assert assumed.debug["slots"]["required_flow_m3_h"] == 6
    assert assumed.debug["slots"]["flow_unit_assumed"] is True

    corrected = bot.handle_chat(session_id, "Нет, это общий объём")
    slots = corrected.debug["slots"]

    assert slots["flow_unit_status"] == "total_volume"
    assert slots["stated_volume_l"] == 100
    assert "required_flow_l_min" not in slots
    assert "required_flow_m3_h" not in slots
    assert "flow_unit_assumed" not in slots


def test_well_head_is_recalculated_from_confirmed_geometry() -> None:
    pump_turn = _interpretation(category="pumps", project_scope="water", slots={})
    bot = _bot(*(pump_turn for _ in range(4)))
    session_id = "well-deterministic-head"

    bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 метра",
    )
    bot.handle_chat(session_id, "до дома 25 метров")
    lifted = bot.handle_chat(session_id, "поднять ещё на 4 метра")

    assert lifted.debug["slots"]["geometric_lift_m"] == pytest.approx(5.8)
    assert lifted.debug["slots"]["horizontal_loss_allowance_m"] == pytest.approx(2.5)
    assert lifted.debug["slots"]["outlet_pressure_head_m"] == pytest.approx(0)
    assert lifted.debug["slots"]["calculated_static_head_m"] == pytest.approx(8.3)
    assert lifted.debug["slots"]["required_head_m"] == pytest.approx(8.3)
    assert lifted.debug["slots"]["required_head_calculated"] is True


def test_llm_cannot_replace_well_source_on_short_distance_answer() -> None:
    bot = _bot(
        _interpretation(
            category="pumps",
            project_scope="water",
            slots={"water_source": "колодец"},
        ),
        _interpretation(
            category="pumps",
            project_scope="water",
            slots={"water_source": "скважина"},
        ),
    )
    session_id = "well-source-priority"

    bot.handle_chat(
        session_id,
        "Нужен насос для колодца, динамический уровень воды 1,8 метра",
    )
    distance = bot.handle_chat(session_id, "25 метров")
    slots = distance.debug["slots"]

    assert distance.debug["category"] == "pumps"
    assert slots["water_source"] == "колодец"
    assert slots["horizontal_run_m"] == 25


def test_warm_floor_context_treats_gas_boiler_as_heat_source() -> None:
    warm_floor_turn = _interpretation(
        category="pipes",
        project_scope="warm_floor",
        slots={},
    )
    bot = _bot(*(warm_floor_turn for _ in range(3)))
    session_id = "warm-floor-gas-boiler-source"

    bot.handle_chat(session_id, "Нужен водяной тёплый пол")
    bot.handle_chat(session_id, "80 м²")
    source = bot.handle_chat(session_id, "От газового котла")
    slots = source.debug["slots"]

    assert source.debug["category"] == "pipes"
    assert slots["project_scope"] == "warm_floor"
    assert slots["warm_floor_area_m2"] == 80
    assert slots["warm_floor_heat_source"] == "газовый котёл"


def test_household_pump_for_home_water_sets_water_supply_use() -> None:
    bot = _bot(
        _interpretation(category="pumps", project_scope="water", slots={})
    )

    response = bot.handle_chat("pump-for-home-water", "Насос для воды дома")
    slots = response.debug["slots"]

    assert response.debug["category"] == "pumps"
    assert slots["pump_use"] == "водоснабжение"


def test_household_ring_answer_separates_well_depth_and_water_column() -> None:
    # The deterministic router must understand this household wording even
    # when the semantic model contributes no pump category or slots.
    bot = _bot(
        _interpretation(category="other", project_scope="water", slots={})
    )
    session_id = "well-rings-from-bottom"

    response = bot.handle_chat(
        session_id,
        "Всего 3 кольца, воды 2 кольца от дна",
    )
    slots = response.debug["slots"]

    assert response.debug["category"] == "pumps"
    assert slots["water_source"] == "колодец"
    assert slots["well_ring_count"] == 3
    assert slots["water_column_ring_count"] == 2
    assert slots["water_level_reference"] == "from_bottom"
    assert slots["well_depth_m"] == pytest.approx(2.7)
    assert slots["water_column_depth_m"] == pytest.approx(1.8)
    assert slots["water_level_depth_m"] == pytest.approx(0.9)


def test_metric_first_answer_sets_water_level_from_top() -> None:
    pump_turn = _interpretation(category="pumps", project_scope="water", slots={})
    bot = _bot(pump_turn, pump_turn)
    session_id = "well-level-metric-first"

    bot.handle_chat(session_id, "Насос для колодца, всего 3 кольца")
    response = bot.handle_chat(
        session_id,
        "1 метр от верха колодца до воды",
    )
    slots = response.debug["slots"]

    assert response.debug["category"] == "pumps"
    assert slots["water_level_reference"] == "from_top"
    assert slots["explicit_water_level_depth_m"] == pytest.approx(1)
    assert slots["water_level_depth_m"] == pytest.approx(1)
    assert slots["water_column_depth_m"] == pytest.approx(1.7)


def test_distance_from_well_edge_without_water_is_not_a_water_level() -> None:
    router = IntentRouterAgent()
    session = SessionState(
        session_id="well-edge-distance",
        category="pumps",
        slots={"water_source": "колодец", "pump_use": "водоснабжение"},
    )

    intent = router.route("Дом в 1 метре от края колодца", session)

    assert "explicit_water_level_depth_m" not in intent.slots
    assert "water_level_depth_m" not in intent.slots
    assert "water_level_reference" not in intent.slots
