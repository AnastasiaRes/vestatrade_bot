from __future__ import annotations

from app.agents.engineering_interpreter import EngineeringInterpreterAgent
from app.agents.engineering_requirements import EngineeringRequirementsAgent
from app.agents.intent_router import IntentRouterAgent
from app.models import SessionState


def test_ppr_elbow_is_not_routed_as_pipe_and_keeps_connection_facts() -> None:
    result = IntentRouterAgent().route("PPR угол 20×1/2 НР, строго 45 градусов")

    assert result.category == "fittings"
    assert result.slots["fitting_system"] == "ppr"
    assert result.slots["diameter_mm"] == 20
    assert result.slots["size_inch"] == "1/2"
    assert result.slots["thread_gender"] == "male"
    assert result.slots["angle_deg"] == 45
    assert result.slots["product_kind"] == "elbow"
    assert "wall_thickness_mm" not in result.slots
    assert "operating_temperature_c" not in result.slots


def test_valve_handle_thread_and_bore_are_explicit_constraints() -> None:
    result = IntentRouterAgent().route(
        "Нужен шаровой полнопроходной 3/4 ВР-НР с бабочкой"
    )

    assert result.category == "valves"
    assert result.slots["thread_type"] == "fm"
    assert result.slots["handle_type"] == "butterfly"
    assert result.slots["full_bore"] is True
    assert result.slots["product_kind"] == "ball_valve"


def test_counter_shorthand_routes_to_valves_without_product_noun() -> None:
    result = IntentRouterAgent(catalog_brands=["VALTEC"]).route(
        "валтек 3/4 бабочка"
    )

    assert result.category == "valves"
    assert result.slots["brand"] == "VALTEC"
    assert result.slots["size_inch"] == "3/4"
    assert result.slots["handle_type"] == "butterfly"


def test_spoken_mama_mama_is_an_ff_thread_pair() -> None:
    result = IntentRouterAgent().route("кран пол дюйма мама мама")

    assert result.category == "valves"
    assert result.slots["size_inch"] == "1/2"
    assert result.slots["thread_type"] == "ff"


def test_new_hard_product_facets_are_durable_project_facts() -> None:
    session = SessionState(session_id="durable-valve-facets", category="valves")
    slots = {
        "brand": "VALTEC",
        "size_inch": "3/4",
        "thread_type": "fm",
        "handle_type": "butterfly",
        "full_bore": True,
        "product_kind": "ball_valve",
    }

    EngineeringRequirementsAgent().remember("valves", slots, session)

    goal_id = session.project_context["active_goal"]
    assert session.project_context["goals"][goal_id]["slots"] == slots


def test_radiator_temperature_regulator_and_common_typo_route_to_fittings() -> None:
    router = IntentRouterAgent()

    regulator = router.route("Хочу поставить регулятор температуры на батарею")
    typo = router.route("Нужна термогаловка")

    assert regulator.category == "radiator_fittings"
    assert regulator.slots["thermostatic_head"] is True
    assert typo.category == "radiator_fittings"
    assert typo.slots["product_kind"] == "thermostatic_head"


def test_negated_old_lengths_do_not_override_explicit_replacement() -> None:
    result = IntentRouterAgent().route(
        "Не 1000 и не 2000 мм — нужна 1500. Есть точное совпадение?",
        SessionState(
            session_id="replace-length",
            category="sewer",
            slots={"element_type": "труба", "diameter_mm": 50, "length_mm": 1000},
        ),
    )

    assert result.slots["length_mm"] == 1500


def test_engineering_interpreter_accepts_new_grounded_product_slots() -> None:
    clean = EngineeringInterpreterAgent._clean_slots
    agent = object.__new__(EngineeringInterpreterAgent)
    message = "PPR шаровой кран полнопроходной с ручкой бабочкой"

    slots = clean(
        agent,
        {
            "fitting_system": "ppr",
            "handle_type": "butterfly",
            "full_bore": True,
            "product_kind": "ball_valve",
        },
        message=message,
    )

    assert slots == {
        "fitting_system": "ppr",
        "handle_type": "butterfly",
        "full_bore": True,
        "product_kind": "ball_valve",
    }
