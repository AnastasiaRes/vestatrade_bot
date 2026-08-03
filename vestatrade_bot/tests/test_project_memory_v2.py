from __future__ import annotations

from app.agents.engineering_requirements import EngineeringRequirementsAgent
from app.models import SessionState
from app.session_store import InMemorySessionStore


def test_pending_question_has_stable_machine_identity_and_legacy_view() -> None:
    session = SessionState(
        session_id="pending-v2",
        category="pumps",
        slots={"water_source": "колодец"},
    )

    pending = session.set_pending_question_state(
        question_id="well.horizontal_distance",
        text="Какое расстояние от колодца до дома?",
        expected_slots=["horizontal_run_m"],
        category="pumps",
        intent_type="attribute_request",
    )

    assert pending.question_id == "well.horizontal_distance"
    assert session.pending_question == pending.text
    assert session.pending_slot_keys == ["horizontal_run_m"]
    assert session.pending_category == "pumps"

    # Existing branches can still rephrase the visible text directly.  At the
    # persistence boundary the expected slot yields the same machine id.
    session.pending_question = "Сколько метров до дома?"
    session.question_repeats = 1
    session.sync_pending_question_state()

    assert session.pending_question_state is not None
    assert session.pending_question_state.question_id == "well.horizontal_distance"
    assert session.pending_question_state.expected_slots == ["horizontal_run_m"]
    assert session.pending_question_state.attempts == 1


def test_session_store_attaches_pending_question_to_active_goal() -> None:
    store = InMemorySessionStore()
    session = SessionState(
        session_id="pending-goal",
        category="pumps",
        slots={"water_source": "колодец"},
        project_context={
            "version": 2,
            "active_goal": "pumps:well",
            "active_category": "pumps",
            "goals": {
                "pumps:well": {
                    "category": "pumps",
                    "scope": "water",
                    "slots": {"water_source": "колодец"},
                    "pending": None,
                }
            },
        },
    )
    session.pending_question = "Какое расстояние до дома?"
    session.pending_category = "pumps"
    session.pending_slot_keys = ["horizontal_run_m"]

    store.save(session)

    saved = store.get("pending-goal")
    pending = saved.project_context["goals"]["pumps:well"]["pending"]
    assert pending["question_id"] == "well.horizontal_distance"
    assert pending["expected_slots"] == ["horizontal_run_m"]


def test_goal_switch_and_return_do_not_mix_pump_and_boiler_facts() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="isolated-goals",
        category="pumps",
        slots={
            "water_source": "колодец",
            "well_ring_count": 3,
            "well_depth_m": 2.7,
            "horizontal_run_m": 25,
            # A wrongly routed generic area must not become pump memory.
            "area_m2": 999,
        },
    )
    agent.remember("pumps", session.slots, session)
    agent.set_pending_question(
        session,
        question_id="well.lift_height",
        text="На какую высоту поднимать воду?",
        expected_slots=["lift_height_m"],
        category="pumps",
        intent_type="attribute_request",
    )

    agent.activate_goal(
        "Теперь нужен электрический котёл на 120 м²",
        "boilers",
        session,
        explicit_slots={"boiler_type": "электрический", "area_m2": 120},
    )
    agent.remember("boilers", session.slots, session)

    assert session.slots == {"boiler_type": "электрический", "area_m2": 120}
    assert "water_source" not in session.slots
    assert "area_m2" not in session.project_context["goals"]["pumps:well"]["slots"]
    assert (
        "well_depth_m"
        not in session.project_context["goals"]["boilers:electric"]["slots"]
    )

    restored = agent.activate_goal(
        "Вернёмся к насосу",
        "pumps",
        session,
        returning=True,
    )

    assert restored["water_source"] == "колодец"
    assert restored["well_depth_m"] == 2.7
    assert restored["horizontal_run_m"] == 25
    assert "area_m2" not in restored
    assert session.pending_question_state is not None
    assert session.pending_question_state.question_id == "well.lift_height"
    assert session.pending_slot_keys == ["lift_height_m"]


def test_current_turn_fact_overrides_restored_goal_fact() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="return-correction",
        category="pumps",
        slots={
            "water_source": "колодец",
            "horizontal_run_m": 25,
        },
    )
    agent.remember("pumps", session.slots, session)
    agent.activate_goal(
        "Теперь нужен котёл",
        "boilers",
        session,
        explicit_slots={"boiler_type": "электрический", "area_m2": 100},
    )

    restored = agent.activate_goal(
        "Вернёмся к насосу, до дома не 25, а 30 метров",
        "pumps",
        session,
        explicit_slots={"horizontal_run_m": 30},
        returning=True,
    )

    assert restored["horizontal_run_m"] == 30
    assert restored["water_source"] == "колодец"
    assert "area_m2" not in restored


def test_warm_floor_area_is_scope_owned_not_global() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="warm-floor-scope",
        category="pipes",
        slots={
            "project_scope": "warm_floor",
            "has_warm_floor": True,
            "warm_floor_area_m2": 60,
            "warm_floor_pipe_min_m": 390,
            "warm_floor_pipe_max_m": 420,
            "warm_floor_contours": 5,
        },
    )
    agent.remember("pipes", session.slots, session)

    agent.activate_goal(
        "Теперь нужен газовый котёл на дом 120 м²",
        "boilers",
        session,
        explicit_slots={"boiler_type": "газовый", "area_m2": 120},
    )
    agent.remember("boilers", session.slots, session)

    assert session.slots["area_m2"] == 120
    assert "warm_floor_area_m2" not in session.slots
    assert "area_m2" not in session.project_context["known_facts"]

    restored = agent.activate_goal(
        "Вернёмся к тёплому полу",
        "pipes",
        session,
        returning=True,
    )
    assert restored["warm_floor_area_m2"] == 60
    assert restored["warm_floor_contours"] == 5
    assert "area_m2" not in restored


def test_legacy_category_context_is_migrated_without_data_loss() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="legacy-memory",
        category="boilers",
        project_context={
            "active_category": "pipes",
            "categories": {
                "pipes": {
                    "pipe_material": "PPR",
                    "diameter_mm": 25,
                    "operating_temperature_c": 80,
                }
            },
        },
    )

    restored = agent.activate_goal(
        "Вернёмся к прежней трубе",
        "pipes",
        session,
        returning=True,
    )

    assert restored["pipe_material"] == "PPR"
    assert restored["diameter_mm"] == 25
    assert session.project_context["version"] == 2
    assert session.project_context["active_goal"] == "pipes"


def test_legacy_migration_filters_foreign_slots_and_known_facts() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="legacy-pollution",
        category="boilers",
        project_context={
            "active_category": "pumps",
            "categories": {
                "pumps": {
                    "water_source": "колодец",
                    "well_depth_m": 2.7,
                    # These fields could enter every category through the old
                    # broad GLOBAL_KEYS set and must not survive migration.
                    "area_m2": 120,
                    "boiler_type": "электрический",
                    "warm_floor_area_m2": 60,
                }
            },
            "known_facts": {
                "project": "загородный дом",
                "area_m2": 120,
                "water_source": "колодец",
            },
        },
    )

    restored = agent.activate_goal(
        "Вернёмся к насосу",
        "pumps",
        session,
        returning=True,
    )
    pump_goal = session.project_context["goals"]["pumps:well"]

    assert restored == {"water_source": "колодец", "well_depth_m": 2.7}
    assert pump_goal["slots"] == restored
    assert session.project_context["known_facts"] == {"project": "загородный дом"}


def test_well_and_borehole_are_kept_as_separate_pump_goals() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="two-pump-sources",
        category="pumps",
        slots={"water_source": "колодец", "well_depth_m": 2.7},
    )
    agent.remember("pumps", session.slots, session)

    session.slots = {"water_source": "скважина", "well_depth_m": 60}
    agent.remember("pumps", session.slots, session)

    goals = session.project_context["goals"]
    assert goals["pumps:well"]["slots"] == {
        "water_source": "колодец",
        "well_depth_m": 2.7,
    }
    assert goals["pumps:borehole"]["slots"] == {
        "water_source": "скважина",
        "well_depth_m": 60,
    }
    assert session.project_context["active_goal"] == "pumps:borehole"


def test_return_to_named_well_does_not_restore_latest_borehole_goal() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="named-pump-return",
        category="pumps",
        slots={"water_source": "колодец", "well_depth_m": 2.7},
    )
    agent.remember("pumps", session.slots, session)

    session.slots = {"water_source": "скважина", "well_depth_m": 60}
    agent.remember("pumps", session.slots, session)
    agent.activate_goal(
        "Теперь нужен электрический котёл",
        "boilers",
        session,
        explicit_slots={"boiler_type": "электрический"},
    )
    agent.remember("boilers", session.slots, session)

    restored = agent.activate_goal(
        "Вернёмся именно к колодцу",
        "pumps",
        session,
        returning=True,
    )

    assert session.project_context["active_goal"] == "pumps:well"
    assert restored["water_source"] == "колодец"
    assert restored["well_depth_m"] == 2.7
    assert restored["well_depth_m"] != 60


def test_boiler_energy_facts_survive_return_without_project_scope() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="plain-boiler-energy",
        category="boilers",
        slots={
            "boiler_type": "электрический",
            "heat_sources": ["электричество"],
            "has_electricity": True,
            "has_gas": False,
            "area_m2": 120,
        },
    )
    agent.remember("boilers", session.slots, session)

    agent.activate_goal(
        "Теперь нужен насос для колодца",
        "pumps",
        session,
        explicit_slots={"water_source": "колодец"},
    )
    restored = agent.activate_goal(
        "Вернёмся к электрическому котлу",
        "boilers",
        session,
        returning=True,
    )

    assert restored["boiler_type"] == "электрический"
    assert restored["heat_sources"] == ["электричество"]
    assert restored["has_electricity"] is True
    assert restored["has_gas"] is False
    assert restored["area_m2"] == 120
    assert "water_source" not in restored


def test_clearing_legacy_pending_question_resets_stale_counters() -> None:
    session = SessionState(
        session_id="stale-pending-counter",
        category="pumps",
        slots={"water_source": "колодец"},
    )
    session.set_pending_question_state(
        text="Какое расстояние до дома?",
        expected_slots=["horizontal_run_m"],
        question_id="well.horizontal_distance",
        category="pumps",
        intent_type="attribute_request",
        attempts=2,
    )

    # A mature legacy branch can still clear only the text. Synchronisation at
    # the store boundary must make this equivalent to an atomic full clear.
    session.pending_question = None
    session.sync_pending_question_state()

    assert session.pending_question_state is None
    assert session.pending_category is None
    assert session.pending_intent_type is None
    assert session.pending_slot_keys == []
    assert session.question_repeats == 0

    session.pending_question = "На какую высоту поднимать воду?"
    session.pending_category = "pumps"
    session.pending_intent_type = "attribute_request"
    session.pending_slot_keys = ["lift_height_m"]
    new_pending = session.sync_pending_question_state()

    assert new_pending is not None
    assert new_pending.question_id == "well.lift_height"
    assert new_pending.attempts == 0


def test_filled_expected_slot_clears_pending_and_active_goal_snapshot() -> None:
    store = InMemorySessionStore()
    session = SessionState(
        session_id="filled-pending-slot",
        category="pipes",
        slots={
            "project_scope": "warm_floor",
            "has_warm_floor": True,
        },
        project_context={
            "version": 2,
            "active_goal": "pipes:warm_floor",
            "active_category": "pipes",
            "goals": {
                "pipes:warm_floor": {
                    "category": "pipes",
                    "scope": "warm_floor",
                    "slots": {"has_warm_floor": True},
                    "pending": None,
                }
            },
        },
    )
    session.set_pending_question_state(
        text="Пирог пола уже готов, утеплитель есть?",
        expected_slots=["floor_insulation_ready"],
        question_id="warm_floor.floor_insulation_ready",
        category="pipes",
    )

    # A negative answer is still a completed answer, not an empty slot.
    session.slots["floor_insulation_ready"] = False
    store.save(session)

    assert session.pending_question_state is None
    assert session.pending_question is None
    assert session.pending_slot_keys == []
    assert (
        session.project_context["goals"]["pipes:warm_floor"]["pending"]
        is None
    )


def test_text_only_known_pending_question_recovers_expected_slot_then_closes() -> None:
    session = SessionState(
        session_id="known-text-only-pending",
        category="pipes",
        slots={"project_scope": "warm_floor", "has_warm_floor": True},
        pending_question=(
            "Каким котлом или другим источником тепла будете греть?"
        ),
        pending_category="pipes",
    )

    pending = session.sync_pending_question_state()

    assert pending is not None
    assert pending.expected_slots == ["warm_floor_heat_source"]
    assert session.pending_slot_keys == ["warm_floor_heat_source"]

    session.slots["warm_floor_heat_source"] = "газовый котёл"
    assert session.sync_pending_question_state() is None
    assert session.pending_question is None


def test_unknown_text_only_pending_question_is_not_persisted_as_generic_loop() -> None:
    store = InMemorySessionStore()
    session = SessionState(
        session_id="unknown-text-only-pending",
        category="pipes",
        slots={"project_scope": "warm_floor"},
        pending_question=(
            "Уточните, пожалуйста, ещё некоторые данные."
        ),
        pending_category="pipes",
        question_repeats=7,
    )

    store.save(session)

    assert session.pending_question_state is None
    assert session.pending_question is None
    assert session.pending_slot_keys == []
    assert session.question_repeats == 0


def test_return_drops_stale_warm_floor_pending_without_losing_saved_facts() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="return-with-stale-warm-floor-pending",
        category="boilers",
        slots={"boiler_type": "электрический", "area_m2": 100},
        project_context={
            "version": 2,
            "active_goal": "boilers:electric",
            "active_category": "boilers",
            "category_last_goal": {
                "boilers": "boilers:electric",
                "pipes": "pipes:warm_floor",
            },
            "goals": {
                "boilers:electric": {
                    "category": "boilers",
                    "scope": None,
                    "slots": {"boiler_type": "электрический", "area_m2": 100},
                    "pending": None,
                },
                "pipes:warm_floor": {
                    "category": "pipes",
                    "scope": "warm_floor",
                    "slots": {
                        "project_scope": "warm_floor",
                        "has_warm_floor": True,
                        "warm_floor_area_m2": 80,
                        "floor_insulation_ready": True,
                        "warm_floor_heat_source": "газовый котёл",
                    },
                    # Old sessions could persist this question without a slot
                    # identity even after both facts had already been saved.
                    "pending": {
                        "question_id": "dialog.clarification.stale_warm_floor",
                        "text": (
                            "Пирог пола готов, утеплитель уже есть?"
                        ),
                        "expected_slots": [],
                        "attempts": 3,
                        "category": "pipes",
                        "intent_type": "attribute_request",
                    },
                },
            },
            "shared_by_scope": {},
            "known_facts": {},
            "categories": {},
        },
    )

    restored = agent.activate_goal(
        "Вернёмся к тёплому полу",
        "pipes",
        session,
        returning=True,
    )

    assert restored["floor_insulation_ready"] is True
    assert restored["warm_floor_heat_source"] == "газовый котёл"
    assert session.pending_question_state is None
    assert session.pending_question is None
    assert (
        session.project_context["goals"]["pipes:warm_floor"]["pending"]
        is None
    )


def test_return_names_warm_floor_instead_of_latest_generic_pipe_goal() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="return-specific-warm-floor-goal",
        category="pumps",
        slots={"water_source": "колодец", "well_ring_count": 3},
        project_context={
            "version": 2,
            "active_goal": "pumps:well",
            "active_category": "pumps",
            "category_last_goal": {
                "pumps": "pumps:well",
                "pipes": "pipes",
            },
            "goals": {
                "pumps:well": {
                    "category": "pumps",
                    "scope": None,
                    "slots": {"water_source": "колодец", "well_ring_count": 3},
                    "pending": None,
                },
                "pipes": {
                    "category": "pipes",
                    "scope": None,
                    "slots": {"pipe_purpose": "холодная вода"},
                    "pending": None,
                },
                "pipes:warm_floor": {
                    "category": "pipes",
                    "scope": "warm_floor",
                    "slots": {"warm_floor_area_m2": 100},
                    "pending": None,
                },
            },
            "shared_by_scope": {
                "warm_floor": {
                    "has_warm_floor": True,
                    "warm_floor_area_m2": 100,
                    "warm_floor_contours": 9,
                }
            },
        },
    )

    restored = agent.activate_goal(
        "Вернёмся к тёплому полу",
        "pipes",
        session,
        returning=True,
    )

    assert session.project_context["active_goal"] == "pipes:warm_floor"
    assert restored["project_scope"] == "warm_floor"
    assert restored["warm_floor_area_m2"] == 100
    assert restored["warm_floor_contours"] == 9
    assert "pipe_purpose" not in restored


def test_return_names_central_main_instead_of_latest_well_pump_goal() -> None:
    agent = EngineeringRequirementsAgent()
    session = SessionState(
        session_id="return-specific-pressure-pump-goal",
        category="pipes",
        slots={"pipe_purpose": "отопление"},
        project_context={
            "version": 2,
            "active_goal": "pipes",
            "active_category": "pipes",
            "category_last_goal": {
                "pipes": "pipes",
                "pumps": "pumps:well",
            },
            "goals": {
                "pipes": {
                    "category": "pipes",
                    "scope": None,
                    "slots": {"pipe_purpose": "отопление"},
                    "pending": None,
                },
                "pumps:well": {
                    "category": "pumps",
                    "scope": None,
                    "slots": {"water_source": "колодец", "well_ring_count": 3},
                    "pending": None,
                },
                "pumps:pressure": {
                    "category": "pumps",
                    "scope": None,
                    "slots": {
                        "water_source": "центральный водопровод",
                        "pump_use": "повышение давления",
                        "inlet_pressure_bar": 1,
                        "required_pressure_bar": 3,
                    },
                    "pending": None,
                },
            },
        },
    )

    restored = agent.activate_goal(
        "Вернёмся к насосу от центрального водопровода",
        "pumps",
        session,
        returning=True,
    )

    assert session.project_context["active_goal"] == "pumps:pressure"
    assert restored["water_source"] == "центральный водопровод"
    assert restored["pump_use"] == "повышение давления"
    assert restored["inlet_pressure_bar"] == 1
    assert restored["required_pressure_bar"] == 3
    assert "well_ring_count" not in restored


def test_old_flow_confirmation_question_is_not_closed_by_assumed_flow() -> None:
    session = SessionState(
        session_id="migrate-flow-confirmation",
        category="pumps",
        slots={
            "water_source": "колодец",
            "required_flow_l_min": 100,
            "required_flow_m3_h": 6,
            "flow_unit_status": "assumed",
        },
        pending_question=(
            "100 литров — это литры в минуту или общий объём?"
        ),
        pending_category="pumps",
    )

    pending = session.sync_pending_question_state()

    assert pending is not None
    assert pending.question_id == "well.flow_unit_confirmation"
    assert pending.expected_slots == ["flow_unit_confirmation"]
