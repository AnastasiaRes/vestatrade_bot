from __future__ import annotations

from copy import deepcopy

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text


def _assert_not_asking_pipe_purpose(
    pending_slot_keys: list[str],
    answer: str,
    *,
    after_message: str,
) -> None:
    text = normalize_text(answer)

    assert "pipe_purpose" not in pending_slot_keys, after_message
    assert "труба для чего" not in text, after_message
    assert "назначение трубы" not in text, after_message


def test_central_water_supply_does_not_ask_for_the_source_again() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-central-water"

    bot.handle_chat(session_id, "Нужно сделать водоснабжение для дома")
    source = bot.handle_chat(session_id, "Центральный водопровод")
    followup = bot.handle_chat(session_id, "Для постоянного проживания")

    for response in (source, followup):
        slots = response.debug["slots"]
        session = bot.sessions.get(session_id)

        assert slots["project_scope"] == "water"
        assert slots["water_source"] == "центральный водопровод"
        assert "water_source" not in session.pending_slot_keys
        assert "источник воды какой" not in normalize_text(response.answer)


def test_household_pump_dialog_advances_through_central_main_parameters() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-household-central-pump"

    bot.handle_chat(session_id, "Мне нужно подобрать насос")
    use = bot.handle_chat(session_id, "Вода дома")
    source = bot.handle_chat(session_id, "Центральный водопровод")
    source_pending = list(bot.sessions.get(session_id).pending_slot_keys)
    pressure = bot.handle_chat(
        session_id,
        "Давление сейчас 1 бар, нужно 3 бара",
    )
    pressure_pending = list(bot.sessions.get(session_id).pending_slot_keys)
    flow = bot.handle_chat(session_id, "30 литров в минуту")

    assert use.debug["slots"]["pump_use"] == "водоснабжение"
    assert "источник воды" in normalize_text(use.answer)

    assert source.debug["slots"]["water_source"] == "центральный водопровод"
    assert source.debug["slots"]["pump_type"] == "повысительный"
    assert "источник воды какой" not in normalize_text(source.answer)
    assert source_pending == ["inlet_pressure_bar"]

    pressure_slots = pressure.debug["slots"]
    assert pressure_slots["inlet_pressure_bar"] == 1
    assert pressure_slots["required_pressure_bar"] == 3
    assert "источник воды" not in normalize_text(pressure.answer)
    assert pressure_pending == ["required_flow_m3_h"]

    final_slots = flow.debug["slots"]
    assert final_slots["required_flow_m3_h"] == 1.8
    assert final_slots["required_flow_l_min"] == 30
    assert "источник воды какой" not in normalize_text(flow.answer)


def test_well_total_rings_and_water_rings_are_kept_as_separate_facts() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-ambiguous-well-rings"

    bot.handle_chat(session_id, "Нужен насос для колодца")
    ambiguous = bot.handle_chat(
        session_id,
        "Всего 3 кольца, воды 2 кольца",
    )
    ambiguous_slots = ambiguous.debug["slots"]

    assert ambiguous_slots["well_ring_count"] == 3
    assert ambiguous_slots["well_depth_m"] == 2.7
    assert ambiguous_slots["water_level_ring_count"] == 2
    assert ambiguous_slots["water_level_reference"] == "ambiguous"
    assert bot.sessions.get(session_id).pending_slot_keys == [
        "water_level_reference"
    ]
    assert "от верха" in normalize_text(ambiguous.answer)
    assert "от дна" in normalize_text(ambiguous.answer)

    resolved = bot.handle_chat(
        session_id,
        "Это два кольца воды от дна",
    )
    resolved_slots = resolved.debug["slots"]

    assert resolved_slots["well_ring_count"] == 3
    assert resolved_slots["water_column_ring_count"] == 2
    assert resolved_slots["water_level_reference"] == "from_bottom"
    assert resolved_slots["water_column_depth_m"] == 1.8
    assert resolved_slots["water_level_depth_m"] == 0.9
    assert bot.sessions.get(session_id).pending_slot_keys == ["horizontal_run_m"]


def test_deferred_ambiguous_well_direction_stays_machine_readable() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-deferred-well-direction"

    for message in [
        "Нужен насос для колодца",
        "Всего 3 кольца, воды 2 кольца",
        "До дома 25 метров",
        "Поднять ещё на 4 метра",
    ]:
        bot.handle_chat(session_id, message)
    unresolved = bot.handle_chat(session_id, "30 литров в минуту")
    pending = bot.sessions.get(session_id)

    assert "сверху или снизу" in normalize_text(unresolved.answer)
    assert pending.pending_slot_keys == ["water_level_reference"]
    assert pending.pending_question_id == "well.water_level_reference"

    resolved = bot.handle_chat(session_id, "От дна")
    slots = resolved.debug["slots"]

    assert resolved.debug["category"] == "pumps"
    assert slots["well_ring_count"] == 3
    assert slots["water_level_reference"] == "from_bottom"
    assert slots["water_column_depth_m"] == 1.8
    assert slots["water_level_depth_m"] == 0.9


def test_warm_floor_short_answers_keep_scope_and_recalculate_correction() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-warm-floor-context"

    responses = []
    dialog_snapshots: list[tuple[str, str, list[str]]] = []
    for message in [
        "Нужен водяной тёплый пол",
        "80 м²",
        "Утеплитель уже есть",
        "Нет, не 80 м², а 100 м²",
        "Газовый котёл",
    ]:
        response = bot.handle_chat(session_id, message)
        responses.append(response)
        dialog_snapshots.append(
            (
                message,
                response.answer,
                list(bot.sessions.get(session_id).pending_slot_keys),
            )
        )

    final = responses[-1]
    slots = final.debug["slots"]

    assert final.debug["category"] == "pipes"
    assert slots["project_scope"] == "warm_floor"
    assert slots["warm_floor_type"] == "водяной"
    assert slots["floor_insulation_ready"] is True
    assert slots["warm_floor_area_m2"] == 100
    assert slots["warm_floor_pipe_min_m"] == 650
    assert slots["warm_floor_pipe_max_m"] == 700
    assert slots["warm_floor_contours"] == 9
    assert slots["warm_floor_collector_count"] == 1
    assert slots["warm_floor_heat_source"] == "газовый котёл"

    for message, answer, pending_slot_keys in dialog_snapshots:
        _assert_not_asking_pipe_purpose(
            pending_slot_keys,
            answer,
            after_message=message,
        )


def test_watery_warm_floor_opening_keeps_area_question_for_bare_metres() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-warm-floor-bare-metres"

    bot.handle_chat(session_id, "Нужен водяной тёплый пол")
    pending = bot.sessions.get(session_id)

    assert pending.pending_slot_keys == ["warm_floor_area_m2"]
    assert pending.pending_question_id == "warm_floor.area"

    response = bot.handle_chat(session_id, "240 метров")
    slots = response.debug["slots"]

    assert response.debug["category"] == "pipes"
    assert slots["project_scope"] == "warm_floor"
    assert slots["warm_floor_area_m2"] == 240
    assert slots["warm_floor_pipe_min_m"] == 1560
    assert slots["warm_floor_pipe_max_m"] == 1680
    assert slots["warm_floor_contours"] == 20


def test_return_to_warm_floor_recalls_summary_without_mutating_well_goal() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-return-to-warm-floor"

    bot.handle_chat(session_id, "Нужен водяной тёплый пол")
    bot.handle_chat(session_id, "80 м²")
    bot.handle_chat(session_id, "Утеплитель уже есть")
    bot.handle_chat(session_id, "Нет, не 80 м², а 100 м²")
    bot.handle_chat(session_id, "Газовый котёл")

    well = bot.handle_chat(
        session_id,
        "Теперь нужен насос для колодца: три кольца, от верха до воды 1,8 м",
    )
    assert well.debug["category"] == "pumps"
    assert well.debug["slots"]["water_source"] == "колодец"

    before_return = bot.sessions.get(session_id)
    pump_goal_before = deepcopy(
        before_return.project_context["goals"]["pumps:well"]
    )

    recalled = bot.handle_chat(
        session_id,
        "Вернёмся к тёплому полу, напомни, что мы уже рассчитали",
    )
    slots = recalled.debug["slots"]
    answer = normalize_text(recalled.answer)

    assert recalled.debug["category"] == "pipes"
    assert slots["project_scope"] == "warm_floor"
    assert slots["warm_floor_area_m2"] == 100
    assert slots["warm_floor_pipe_min_m"] == 650
    assert slots["warm_floor_pipe_max_m"] == 700
    assert slots["warm_floor_contours"] == 9
    assert slots["warm_floor_heat_source"] == "газовый котёл"
    assert "water_source" not in slots
    assert "well_ring_count" not in slots

    # A recall answer must surface the restored facts, not merely restore them
    # invisibly in state.
    assert "100" in answer
    assert "9" in answer
    assert "газ" in answer

    after_return = bot.sessions.get(session_id)
    assert after_return.project_context["goals"]["pumps:well"] == pump_goal_before


def test_recall_of_unseen_central_main_does_not_relabel_well_facts() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "live-unseen-central-main-recall"

    bot.handle_chat(
        session_id,
        "Нужен насос для колодца: всего 3 кольца, воды 2 кольца от дна",
    )
    recalled = bot.handle_chat(
        session_id,
        "Вернёмся к насосу от центрального водопровода, напомни, что знаешь",
    )
    slots = recalled.debug["slots"]
    answer = normalize_text(recalled.answer)

    assert recalled.debug["category"] == "pumps"
    assert slots["water_source"] == "центральный водопровод"
    assert slots["pump_use"] == "повышение давления"
    assert "well_ring_count" not in slots
    assert "well_depth_m" not in slots
    assert "центральный водопровод" in answer
    assert "колодец" not in answer
    assert bot.sessions.get(session_id).project_context["active_goal"] == (
        "pumps:pressure"
    )
