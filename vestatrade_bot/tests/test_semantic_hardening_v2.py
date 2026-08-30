from __future__ import annotations

from app.agents.semantic_interpreter import (
    CustomerAct,
    TurnUnderstanding,
    repair_grounded_semantic_payload,
    validate_semantic_content_coverage,
    validate_product_modifier_coverage,
)


def _candidate(*, acts: tuple[str, ...] = ("select",)) -> dict[str, object]:
    return {
        "schema_version": "1.3",
        "language": "ru",
        "operation": "new",
        "acts": list(acts),
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }


def _repair(
    message: str,
    *,
    acts: tuple[str, ...] = ("select",),
    state: dict[str, object] | None = None,
) -> tuple[TurnUnderstanding, tuple[str, ...]]:
    repaired, repairs = repair_grounded_semantic_payload(
        _candidate(acts=acts),
        message,
        authoritative_dialogue_state=state,
    )
    return TurnUnderstanding.model_validate(repaired), repairs


def _facts(frame: TurnUnderstanding) -> dict[str, tuple[object, str | None]]:
    return {item.name: (item.value, item.unit) for item in frame.constraints}


def _active_state(kind: str, category: str) -> dict[str, object]:
    return {
        "active_goal_id": "goal-1",
        "goals": [
            {
                "goal_id": "goal-1",
                "canonical_type": kind,
                "category": category,
            }
        ],
    }


def test_ppr_slang_recovers_product_and_all_explicit_facts() -> None:
    frame, _ = _repair(
        "Нужна ппэровская двадцать пятая со стеклом на батареи, подача девяносто"
    )

    assert [(item.canonical_type, item.category.value) for item in frame.products] == [
        ("pipe", "pipes")
    ]
    assert _facts(frame) == {
        "diameter_mm": (25, "mm"),
        "reinforcement": ("glass_fiber", None),
        "pipe_service": ("heating", None),
        "operating_temperature_c": (90, "c"),
    }


def test_short_pipe_fragments_bind_to_existing_pipe_goal() -> None:
    state = _active_state("pipe", "pipes")
    frame, _ = _repair("Со стеклом", state=state)
    assert _facts(frame)["reinforcement"] == ("glass_fiber", None)

    frame, _ = _repair("Для батарей, 90 градусов", state=state)
    assert _facts(frame)["pipe_service"] == ("heating", None)
    assert _facts(frame)["operating_temperature_c"] == (90, "c")


def test_pipe_pressure_anchor_is_canonicalized_in_an_active_pipe_context() -> None:
    candidate = _candidate()
    candidate["constraints"] = [
        {
            "name": "operating_pressure_bar",
            "value": "6 бар",
            "unit": "бар",
            "status": "known",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "давление 6 бар",
        }
    ]
    repaired, _ = repair_grounded_semantic_payload(
        candidate,
        "Подача 90 °C, давление 6 бар",
        authoritative_product_hints=(
            {"canonical_type": "pipe", "category": "pipes"},
        ),
        authoritative_dialogue_state=_active_state("pipe", "pipes"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["operating_pressure_bar"] == (6, "bar")


def test_explicit_radiator_material_corrects_an_earlier_unknown() -> None:
    candidate = _candidate()
    candidate["constraints"] = [
        {
            "name": "material",
            "value": None,
            "unit": None,
            "status": "unknown",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "Материал пока не знаю",
        }
    ]

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Нужен биметаллический.",
        authoritative_dialogue_state=_active_state("radiator", "radiators"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["material"] == ("биметалл", None)
    assert "radiator_material_recovered_from_explicit_alias" in changes


def test_unknown_valve_pattern_does_not_erase_a_known_connection_size() -> None:
    candidate = _candidate()
    candidate["answers_pending_question"] = True
    candidate["constraints"] = [
        {
            "name": "connection_size",
            "value": None,
            "unit": None,
            "status": "unknown",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "Тип резьбы пока не знаю",
        }
    ]
    state = _active_state("ball_valve", "valves")
    state["pending_decision_question"] = {"fact_name": "connection_pattern"}

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Тип резьбы пока не знаю.",
        authoritative_product_hints=(
            {"canonical_type": "ball_valve", "category": "valves"},
        ),
        authoritative_dialogue_state=state,
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert [(item.name, item.status.value) for item in frame.constraints] == [
        ("connection_pattern", "unknown")
    ]
    assert "valve_thread_type_unknown_rebound_to_pattern" in changes


def test_mounting_length_anchor_canonicalizes_a_numeric_string() -> None:
    candidate = _candidate()
    candidate["constraints"] = [
        {
            "name": "mounting_length_mm",
            "value": "180",
            "unit": "мм",
            "status": "known",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "Монтажная длина 180 мм",
        }
    ]

    repaired, _ = repair_grounded_semantic_payload(
        candidate,
        "Монтажная длина 180 мм.",
        authoritative_product_hints=(
            {"canonical_type": "circulation_pump", "category": "pumps"},
        ),
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["mounting_length_mm"] == (180, "mm")


def test_pump_dn_anchor_canonicalizes_connection_diameter() -> None:
    repaired, _ = repair_grounded_semantic_payload(
        _candidate(),
        "DN25, монтажная длина 180 мм.",
        authoritative_product_hints=(
            {"canonical_type": "circulation_pump", "category": "pumps"},
        ),
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["diameter_mm"] == (25, "mm")
    assert _facts(frame)["mounting_length_mm"] == (180, "mm")


def test_boiler_area_anchor_is_customer_requirement_not_power() -> None:
    repaired, _ = repair_grounded_semantic_payload(
        _candidate(),
        "Для газового котла дом 150 квадратных метров.",
        authoritative_product_hints=(
            {"canonical_type": "gas_boiler", "category": "boilers"},
        ),
        authoritative_dialogue_state=_active_state("gas_boiler", "boilers"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["area_m2"] == (150, "m2")
    assert "power_kw" not in _facts(frame)
    assert "circuits" not in _facts(frame)


def test_pending_boiler_dhw_answer_recovers_two_circuits() -> None:
    candidate = _candidate()
    candidate["answers_pending_question"] = True
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Нужна ещё горячая вода.",
        authoritative_product_hints=(
            {"canonical_type": "gas_boiler", "category": "boilers"},
        ),
        authoritative_dialogue_state=_active_state("gas_boiler", "boilers"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["circuits"] == (2, None)
    assert "boiler_circuits_recovered_from_closed_alias" in changes


def test_short_boiler_fuel_and_circuit_reply_recovers_both_facts() -> None:
    candidate = _candidate()
    candidate["answers_pending_question"] = True
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Газовый, только отопление.",
        authoritative_dialogue_state=_active_state("boiler", "boilers"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["boiler_type"] == ("gas", None)
    assert _facts(frame)["circuits"] == (1, None)
    assert "boiler_type_recovered_from_closed_alias" in changes


def test_explicit_boiler_circuit_count_recovers_without_confusing_power() -> None:
    repaired, changes = repair_grounded_semantic_payload(
        _candidate(),
        "Нужен котёл на два контура.",
        authoritative_product_hints=(
            {"canonical_type": "gas_boiler", "category": "boilers"},
        ),
        authoritative_dialogue_state=_active_state("gas_boiler", "boilers"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["circuits"] == (2, None)
    assert "boiler_circuits_recovered_from_closed_alias" in changes


def test_radiator_center_distance_anchor_canonicalizes_a_numeric_string() -> None:
    candidate = _candidate()
    candidate["constraints"] = [
        {
            "name": "center_distance_mm",
            "value": "500",
            "unit": "мм",
            "status": "known",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "межосевым расстоянием 500 мм",
        }
    ]

    repaired, _ = repair_grounded_semantic_payload(
        candidate,
        "Нужен радиатор с межосевым расстоянием 500 мм.",
        authoritative_product_hints=(
            {"canonical_type": "radiator", "category": "radiators"},
        ),
        authoritative_dialogue_state=_active_state("radiator", "radiators"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert _facts(frame)["center_distance_mm"] == (500, "mm")


def test_pipe_service_phrase_drops_model_radiator_false_positive() -> None:
    candidate = _candidate(acts=("select",))
    candidate["products"] = [
        {
            "text": "Для батарей, 90 градусов",
            "canonical_type": "radiator",
            "category": "radiators",
            "role": "target",
            "evidence": "Для батарей, 90 градусов",
        }
    ]
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Для батарей, 90 градусов",
        authoritative_dialogue_state=_active_state("pipe", "pipes"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.products == []
    assert _facts(frame)["pipe_service"] == ("heating", None)
    assert _facts(frame)["operating_temperature_c"] == (90, "c")
    assert "pipe_service_radiator_product_false_positive_dropped" in changes


def test_radiator_distribution_is_pipe_service_not_radiator_product() -> None:
    frame, _ = _repair("PPR на радиаторную разводку")
    assert frame.products[0].canonical_type == "pipe"
    assert frame.products[0].category.value == "pipes"
    assert _facts(frame)["pipe_service"] == ("heating", None)


def test_holdout_pipe_service_and_glass_aliases_use_canonical_ontology() -> None:
    frame, _ = _repair(
        "Труба PPR 25, армирование волокном стекла, в отопительный контур"
    )

    assert frame.products[0].canonical_type == "pipe"
    assert _facts(frame)["reinforcement"] == ("glass_fiber", None)
    assert _facts(frame)["pipe_service"] == ("heating", None)


def test_colloquial_pump_working_point_is_canonicalized() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    frame, _ = _repair(
        "Нужен циркуляционник на батареи: полтора куба в час при четырёх метрах напора"
    )
    assert frame.products[0].canonical_type == "circulation_pump"
    assert frame.products[0].category.value == "pumps"
    facts = _facts(frame)
    assert facts["duty_point_flow_l_h"] == (1.5, "m3/h")
    assert facts["duty_point_head_m"] == (4, "m")
    delta, gate = build_semantic_turn_delta(frame, message="Нужен циркуляционник на батареи: полтора куба в час при четырёх метрах напора", turn_id="pump-spoken")
    canonical = {item.predicate: (item.canonical_value, item.canonical_unit) for item in delta.fact_updates}
    assert gate.accepted
    assert canonical["duty_point_flow_l_h"] == (1500, "l/h")


def test_engineering_pump_notation_has_same_working_point() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    frame, _ = _repair("Циркуляционный насос: Q=1.5 м³/ч, H=4 м")
    facts = _facts(frame)
    assert facts["duty_point_flow_l_h"] == (1.5, "м³/ч")
    assert facts["duty_point_head_m"] == (4, "m")
    delta, gate = build_semantic_turn_delta(frame, message="Циркуляционный насос: Q=1.5 м³/ч, H=4 м", turn_id="pump-qh")
    canonical = {item.predicate: (item.canonical_value, item.canonical_unit) for item in delta.fact_updates}
    assert gate.accepted
    assert canonical["duty_point_flow_l_h"] == (1500, "l/h")


def test_valve_spoken_connection_pattern_and_size_are_recovered() -> None:
    from app.semantic_v2.bridge import (
        adapt_delta_to_turn_understanding,
        build_semantic_turn_delta,
    )

    frame, _ = _repair("VALTEC BASE DN15, обе резьбы внутренние")
    assert frame.products[0].canonical_type == "ball_valve"
    assert frame.products[0].category.value == "valves"
    facts = _facts(frame)
    assert facts["connection_size"] == ("1/2", None)
    assert facts["connection_pattern"] == ("female_female", None)
    model_style = frame.model_copy(
        update={
            "constraints": [
                item.model_copy(update={"value": "female_female"})
                if item.name == "connection_pattern"
                else item
                for item in frame.constraints
            ]
        }
    )
    delta, gate = build_semantic_turn_delta(
        model_style,
        message="VALTEC BASE DN15, обе резьбы внутренние",
        turn_id="valve-canonical",
    )
    adapted = adapt_delta_to_turn_understanding(delta, model_style)
    assert gate.accepted and adapted is not None
    assert _facts(adapted)["connection_pattern"] == ("female_female", None)


def test_holdout_valve_connection_aliases_use_canonical_ontology() -> None:
    state = _active_state("ball_valve", "valves")
    for message in (
        "Соединения ВР с двух сторон",
        "Оба присоединения с внутренней резьбой",
    ):
        frame, _ = _repair(message, state=state)
        assert _facts(frame)["connection_pattern"] == ("female_female", None)


def test_short_valve_size_rebinds_to_active_goal_and_drops_stale_product() -> None:
    candidate = _candidate(acts=("select",))
    candidate["products"] = [
        {
            "text": "шаровый кран BASE",
            "canonical_type": "ball valve",
            "category": "valves",
            "role": "target",
            "evidence": "шаровый кран BASE",
        }
    ]
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Размер G 1/2",
        authoritative_dialogue_state=_active_state("ball_valve", "valves"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.operation.value == "continue"
    assert frame.products == []
    assert _facts(frame)["connection_size"] == ("1/2", None)
    assert "bounded_fact_followup_rebound_to_active_goal" in changes
    assert "stale_typed_product_evidence_dropped" in changes


def test_sewer_slang_never_becomes_ppr() -> None:
    frame, _ = _repair("Каналия по улице до септика, диаметр 110")
    assert frame.products[0].canonical_type == "sewer_pipe"
    assert frame.products[0].category.value == "sewer"


def test_sewer_anchor_rebinds_model_paraphrase_to_exact_turn_evidence() -> None:
    candidate = _candidate(acts=("select",))
    candidate["products"] = [
        {
            "text": "труба для канализации",
            "canonical_type": "sewer pipe",
            "category": "sewer",
            "role": "target",
            "evidence": "труба для канализации",
        }
    ]
    message = "В туалете пахнет канализацией, похоже проблема с трубой"
    repaired, changes = repair_grounded_semantic_payload(candidate, message)
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.products[0].canonical_type == "sewer_pipe"
    assert frame.products[0].evidence in message
    assert "product_evidence_rebound_to_current_message" in changes


def test_short_external_sewer_scope_and_spoken_diameter_bind_to_active_goal() -> None:
    state = _active_state("sewer_pipe", "sewer")
    scope, scope_changes = _repair("Трасса пойдёт наружу от дома", state=state)
    diameter, diameter_changes = _repair("Сто десятый диаметр", state=state)

    assert scope.operation.value == "continue"
    assert _facts(scope)["sewer_scope"] == ("external", None)
    assert "external_sewer_scope_recovered" in scope_changes
    assert diameter.operation.value == "continue"
    assert _facts(diameter)["diameter_mm"] == (110, "mm")
    assert "spoken_sewer_diameter_anchor_recovered" in diameter_changes

    model_candidate = _candidate(acts=("select",))
    model_candidate["constraints"] = [
        {
            "name": "diameter_mm",
            "value": "сто десятый",
            "unit": None,
            "status": "known",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "Сто десятый диаметр",
        }
    ]
    repaired, model_changes = repair_grounded_semantic_payload(
        model_candidate,
        "Сто десятый диаметр",
        authoritative_dialogue_state=state,
    )
    model_frame = TurnUnderstanding.model_validate(repaired)
    assert _facts(model_frame)["diameter_mm"] == (110, "mm")
    assert "spoken_sewer_diameter_anchor_canonicalized" in model_changes


def test_generic_show_product_type_is_canonicalized_from_shared_registry() -> None:
    candidate = _candidate(acts=("find",))
    candidate["products"] = [
        {
            "text": "Покажи варианты",
            "canonical_type": "sewer pipe",
            "category": "sewer",
            "role": "target",
            "evidence": "Покажи варианты",
        }
    ]
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Покажи варианты",
        authoritative_dialogue_state=_active_state("sewer_pipe", "sewer"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.operation.value == "continue"
    assert frame.products[0].canonical_type == "sewer_pipe"
    assert "product_type_canonicalized_from_registry" in changes


def test_future_actions_are_preserved_in_semantic_delta() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    expectations = {
        "Сравни первый и второй": "compare",
        "Посчитай стоимость двадцати штук": "calculate",
        "Сколько будет стоить 20 шт. первого?": "calculate",
        "Почему именно такая мощность?": "rationale",
        "Эти два товара совместимы?": "compatibility",
        "Собери проект котельной": "project",
    }
    for message, action in expectations.items():
        frame, _ = _repair(message, acts=())
        delta, gate = build_semantic_turn_delta(
            frame,
            message=message,
            turn_id=f"turn-{action}",
        )
        assert action in {item.action for item in delta.action_candidates}
        assert gate.accepted


def test_delta_adapter_preserves_valid_turn_understanding() -> None:
    from app.semantic_v2.bridge import (
        adapt_delta_to_turn_understanding,
        build_semantic_turn_delta,
    )

    frame, _ = _repair("PPR 25 со стекловолокном для отопления, 90 градусов")
    delta, gate = build_semantic_turn_delta(frame, message="PPR 25 со стекловолокном для отопления, 90 градусов", turn_id="turn-1")
    adapted = adapt_delta_to_turn_understanding(delta, frame)

    assert gate.accepted
    assert adapted.model_dump(mode="json") == frame.model_dump(mode="json")


def test_semantic_delta_carries_session_metadata() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    frame, _ = _repair("Нужна ППР труба")
    delta, gate = build_semantic_turn_delta(
        frame,
        message="Нужна ППР труба",
        turn_id="turn-with-session",
        session_id="qa-session-1",
    )

    assert gate.accepted
    assert delta.session_id == "qa-session-1"


def test_rejected_delta_does_not_become_reducer_input() -> None:
    from app.semantic_v2.bridge import adapt_delta_to_turn_understanding
    from app.semantic_v2.contracts import SemanticTurnDeltaV1

    frame, _ = _repair("Нужна труба")
    rejected = SemanticTurnDeltaV1(
        turn_id="turn-rejected",
        status="rejected",
        registry_version="test",
        rejection_reason_codes=("semantic_anchor_conflict",),
    )
    assert adapt_delta_to_turn_understanding(rejected, frame) is None


def test_facts_accumulate_monotonically_across_turns() -> None:
    from app.dialogue_v2.contracts import DialogueStateV2, TurnMetadata
    from app.dialogue_v2.reducer import reduce_dialogue_state

    first, _ = _repair("Нужна ППР труба, диаметр 25 мм")
    state = reduce_dialogue_state(
        DialogueStateV2(),
        first,
        TurnMetadata(turn_id="semantic-state-1"),
    ).state
    second, _ = _repair(
        "Со стеклом",
        state=_active_state("pipe", "pipes"),
    )
    state = reduce_dialogue_state(
        state,
        second,
        TurnMetadata(turn_id="semantic-state-2"),
    ).state

    active = {item.name: item.value for item in state.constraints if item.active}
    assert active["diameter_mm"] == 25
    assert active["reinforcement"] == "glass_fiber"


def test_unsupported_future_action_is_not_adapted_to_selection() -> None:
    from app.semantic_v2.bridge import (
        adapt_delta_to_turn_understanding,
        build_semantic_turn_delta,
    )

    frame, _ = _repair("Собери проект котельной", acts=("select",))
    delta, gate = build_semantic_turn_delta(
        frame,
        message="Собери проект котельной",
        turn_id="future-project",
    )
    adapted = adapt_delta_to_turn_understanding(delta, frame)

    assert gate.accepted
    assert adapted is not None
    assert "project" in {item.action for item in delta.action_candidates}
    assert adapted.acts == []


def test_future_boundaries_preserve_action_and_reuse_existing_explain_path() -> None:
    from app.semantic_v2.bridge import (
        adapt_delta_to_turn_understanding,
        build_semantic_turn_delta,
    )

    for message, action in (
        ("Почему именно такая мощность?", "rationale"),
        ("Эти два товара совместимы?", "compatibility"),
    ):
        frame, _ = _repair(message, acts=("explain",))
        delta, gate = build_semantic_turn_delta(
            frame,
            message=message,
            turn_id=f"boundary-{action}",
        )
        adapted = adapt_delta_to_turn_understanding(delta, frame)

        assert gate.accepted
        assert adapted is not None
        assert action in {item.action for item in delta.action_candidates}
        assert [item.value for item in adapted.acts] == (
            ["explain", "compatibility"]
            if action == "compatibility"
            else ["explain"]
        )


def test_product_fact_predicate_paraphrases_share_existing_evidence_path() -> None:
    from app.product_fact_evidence import ProductFactEvidenceService

    assert ProductFactEvidenceService._predicate(
        "Сколько миллиметров у первого насоса по монтажу?",
        None,
    ) == "installation_length_mm"
    assert ProductFactEvidenceService._predicate(
        "Первый вариант какой длины между присоединениями?",
        None,
    ) == "installation_length_mm"
    assert ProductFactEvidenceService._predicate(
        "У первой карточки сколько миллиметров между патрубками?",
        None,
    ) == "installation_length_mm"
    assert ProductFactEvidenceService._predicate(
        "Какой монтажный размер у второй позиции?",
        None,
    ) == "installation_length_mm"
    assert ProductFactEvidenceService._predicate(
        "А первый по длине монтажа какой?",
        None,
    ) == "installation_length_mm"


def test_generic_show_discards_only_stale_pending_information_request() -> None:
    candidate = _candidate(acts=("find", "explain"))
    candidate["operation"] = "continue"
    candidate["information_requests"] = [
        {
            "fact_name": "operating_pressure_bar",
            "purpose": "value",
            "requested_outputs": ["explanation"],
            "output_relation": "all",
            "source_kind": None,
            "act": "explain",
            "subject_scope": "customer_goal",
            "applies_to_product": None,
            "evidence": "Что есть",
        }
    ]
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Что есть?",
        authoritative_dialogue_state=_active_state("pipe", "pipes"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.information_requests == []
    assert [item.value for item in frame.acts] == ["find"]
    assert frame.selection_strategy is not None
    assert frame.selection_strategy.kind.value == "continue_with_confirmed_facts"
    assert "generic_show_stale_information_request_removed" in changes


def test_unknown_pending_answer_cannot_become_a_spurious_fact_question() -> None:
    candidate = _candidate(acts=("explain",))
    candidate["operation"] = "refine"
    candidate["answers_pending_question"] = True
    candidate["constraints"] = [
        {
            "name": "operating_temperature_c",
            "value": None,
            "unit": None,
            "status": "unknown",
            "polarity": "required",
            "applies_to_product": None,
            "evidence": "Температуру сейчас не знаю",
        }
    ]
    candidate["information_requests"] = [
        {
            "fact_name": "operating_temperature_c",
            "purpose": "value",
            "requested_outputs": ["explanation"],
            "output_relation": "all",
            "source_kind": "any_verified",
            "act": "explain",
            "subject_scope": "customer_goal",
            "applies_to_product": None,
            "evidence": "Температуру сейчас не знаю",
        }
    ]

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Температуру сейчас не знаю.",
        authoritative_dialogue_state=_active_state("pipe", "pipes"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.information_requests == []
    assert frame.acts == []
    assert frame.constraints[0].status.value == "unknown"
    assert "pending_terminal_answer_spurious_information_request_removed" in changes


def test_generic_show_anchor_overrides_model_ambiguous_strategy() -> None:
    candidate = _candidate(acts=("find",))
    candidate["operation"] = "new"
    candidate["selection_strategy"] = {
        "kind": "ambiguous",
        "evidence": "Покажи товары",
    }
    candidate["ambiguities"] = [
        {
            "kind": "selection_strategy_ambiguous",
            "description": "model uncertainty",
            "evidence": "Покажи товары",
        }
    ]
    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Покажи товары",
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.operation.value == "continue"
    assert frame.selection_strategy is not None
    assert frame.selection_strategy.kind.value == "continue_with_confirmed_facts"
    assert frame.ambiguities == []
    assert "generic_show_rebound_to_active_goal" in changes
    assert "generic_show_anchor_forced_continue" in changes


def test_availability_wording_is_selection_when_product_is_not_yet_shown() -> None:
    state = _active_state("circulation_pump", "pumps")
    for message in (
        "Что доступно?",
        "Какие есть в наличии?",
        "Покажи наличие",
        "Подбери доступные позиции",
    ):
        frame, changes = _repair(message, acts=("check_stock",), state=state)
        assert [item.value for item in frame.acts] == ["find"]
        assert frame.selection_strategy is not None
        assert frame.selection_strategy.kind.value == "continue_with_confirmed_facts"
        assert "generic_show_anchor_forced_continue" in changes


def test_plural_difference_question_preserves_compare_alongside_broad_explain() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    frame = TurnUnderstanding.model_validate(
        {
            **_candidate(acts=("explain",)),
            "operation": "continue",
        }
    )

    delta, gate = build_semantic_turn_delta(
        frame,
        message="А чем они отличаются?",
        turn_id="compare-plural",
    )

    assert gate.accepted is True
    assert {item.action for item in delta.action_candidates} >= {"fact", "compare"}
    compare = next(item for item in delta.action_candidates if item.action == "compare")
    assert compare.downstream_action == "compare"
    assert compare.evidence == "чем они отличаются"


def test_visible_scope_natural_difference_question_repairs_raw_act_to_compare() -> None:
    candidate = _candidate(acts=("explain",))
    candidate["operation"] = "continue"

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "А чем они отличаются?",
        shown_product_cards=("VT.217.N.04", "VT.214.N.04"),
        authoritative_dialogue_state=_active_state("ball_valve", "valves"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert [item.value for item in frame.acts] == ["explain", "compare"]
    assert "visible_scope_compare_action_recovered" in changes


def test_visible_scope_natural_compatibility_question_repairs_empty_frame() -> None:
    candidate = _candidate(acts=())
    candidate["operation"] = "continue"

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "А этот подойдёт к третьему?",
        shown_product_cards=("FIRST", "SECOND", "THIRD"),
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)
    validate_semantic_content_coverage(frame, "А этот подойдёт к третьему?", changes)

    assert [item.value for item in frame.acts] == ["compatibility"]
    assert "visible_scope_compatibility_action_recovered" in changes


def test_visible_scope_colloquial_compatibility_variants_recover_the_action() -> None:
    for message in (
        "Эта головка состыкуется со вторым клапаном?",
        "Первый подходит к третьему?",
        "Эти два товара будут работать вместе?",
    ):
        candidate = _candidate(acts=())
        candidate["operation"] = "continue"
        repaired, changes = repair_grounded_semantic_payload(
            candidate,
            message,
            shown_product_cards=("FIRST", "SECOND", "THIRD"),
            authoritative_dialogue_state=_active_state("radiator_valve", "radiator_fittings"),
        )
        frame = TurnUnderstanding.model_validate(repaired)

        assert CustomerAct.COMPATIBILITY in frame.acts
        assert "visible_scope_compatibility_action_recovered" in changes


def test_malformed_llm_control_does_not_drop_visible_scope_compatibility() -> None:
    """Pre-validation de-duplication must never crash on an object field.

    A live interpreter response once supplied an object where a workflow
    control evidence string belongs.  The malformed field is disposable; the
    explicit relationship in the customer message and the delivered scope are
    still enough to preserve the grounded Compatibility action.
    """

    candidate = _candidate(acts=())
    candidate["operation"] = "continue"
    candidate["workflow_controls"] = [
        {"kind": "confirm", "evidence": {"raw": "первый и второй"}}
    ]

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Первый совместим со вторым?",
        shown_product_cards=("FIRST", "SECOND", "THIRD"),
        authoritative_dialogue_state=_active_state("ball_valve", "valves"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert CustomerAct.COMPATIBILITY in frame.acts
    assert "invalid_workflow_control_schema_dropped" in changes
    assert "visible_scope_compatibility_action_recovered" in changes


def test_compatibility_action_is_not_invented_without_multi_card_scope() -> None:
    candidate = _candidate(acts=())
    candidate["operation"] = "continue"

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "А этот подойдёт к третьему?",
        shown_product_cards=("FIRST",),
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert frame.acts == []
    assert "visible_scope_compatibility_action_recovered" not in changes


def test_explicit_pair_compatibility_recovers_before_direct_fact_routing() -> None:
    """Two concrete articles are enough to preserve the relationship action.

    Resolution still happens only in CompatibilityRequest against its source
    snapshot; this repair cannot make an arbitrary numeric span a product.
    """

    candidate = _candidate(acts=("explain",))
    candidate["operation"] = "continue"

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Подойдет ли насос 53843 к котлу 8216262000?",
        authoritative_dialogue_state=_active_state("circulation_pump", "pumps"),
    )
    frame = TurnUnderstanding.model_validate(repaired)
    validate_semantic_content_coverage(
        frame,
        "Подойдет ли насос 53843 к котлу 8216262000?",
        changes,
    )

    assert [item.value for item in frame.acts] == ["explain", "compatibility"]
    assert "explicit_pair_compatibility_action_recovered" in changes


def test_five_digit_article_in_compatibility_product_evidence_is_not_a_fake_fact() -> None:
    candidate = _candidate(acts=("compatibility",))
    candidate["operation"] = "continue"
    candidate["products"] = [
        {
            "text": "насос 53843",
            "canonical_type": "circulation_pump",
            "category": "pumps",
            "role": "existing",
            "evidence": "насос 53843",
        },
        {
            "text": "котлу 8216262000",
            "canonical_type": "electric_boiler",
            "category": "boilers",
            "role": "existing",
            "evidence": "котлу 8216262000",
        },
    ]
    repaired, _ = repair_grounded_semantic_payload(
        candidate,
        "Подойдет ли насос 53843 к котлу 8216262000?",
    )
    frame = TurnUnderstanding.model_validate(repaired)

    validate_product_modifier_coverage(frame)


def test_generic_typed_product_question_recovers_selection_not_product_fact() -> None:
    candidate = _candidate(acts=("select", "explain"))
    candidate["products"] = [
        {
            "text": "газовый котёл",
            "canonical_type": "gas_boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "газовый котёл",
        }
    ]
    candidate["information_requests"] = [
        {
            "fact_name": "power_kw",
            "purpose": "value",
            "requested_outputs": ["explanation"],
            "output_relation": "all",
            "source_kind": None,
            "act": "explain",
            "subject_scope": "customer_goal",
            "applies_to_product": 0,
            "evidence": "Какой смотреть",
        }
    ]

    repaired, changes = repair_grounded_semantic_payload(
        candidate,
        "Дом 150 м², хочу газовый котёл. Какой смотреть?",
    )
    frame = TurnUnderstanding.model_validate(repaired)

    assert [item.value for item in frame.acts] == ["select"]
    assert frame.information_requests == []
    assert "generic_typed_product_selection_explain_dropped" in changes


def test_numeric_article_in_product_mention_is_not_forced_into_a_fact() -> None:
    frame = TurnUnderstanding.model_validate(
        {
            **_candidate(acts=("explain",)),
            "operation": "continue",
            "products": [
                {
                    "text": "Arderia E9 2202210",
                    "canonical_type": "electric boiler",
                    "category": "boilers",
                    "role": "target",
                    "evidence": "Arderia E9 2202210",
                }
            ],
        }
    )

    validate_product_modifier_coverage(frame)


def test_calculation_quantity_in_product_reference_is_not_a_product_modifier() -> None:
    frame = TurnUnderstanding.model_validate(
        {
            **_candidate(acts=("calculate",)),
            "operation": "continue",
            "products": [
                {
                    "text": "первый",
                    "canonical_type": "ball_valve",
                    "category": "valves",
                    "role": "target",
                    "evidence": "20 шт. первого",
                }
            ],
        }
    )

    validate_product_modifier_coverage(frame)


def test_explicit_total_price_phrase_recovers_calculate_action() -> None:
    frame, changes = _repair(
        "Сколько будет стоить 20 шт. первого?", acts=("explain",)
    )

    assert {item.value for item in frame.acts} >= {"calculate"}
    assert "explicit_calculation_action_recovered" in changes


def test_ordered_multiple_typed_targets_are_preserved_as_project_intent() -> None:
    from app.semantic_v2.bridge import build_semantic_turn_delta

    frame = TurnUnderstanding.model_validate(
        {
            **_candidate(acts=("select",)),
            "products": [
                {
                    "text": "труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "труба",
                },
                {
                    "text": "насос",
                    "canonical_type": "circulation pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "насос",
                },
            ],
        }
    )

    delta, gate = build_semantic_turn_delta(
        frame,
        message="Сначала нужна труба, потом насос.",
        turn_id="ordered-project",
    )

    assert gate.accepted is True
    assert any(item.action == "project" and item.downstream_action is None for item in delta.action_candidates)
