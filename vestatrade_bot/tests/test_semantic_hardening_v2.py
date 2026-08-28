from __future__ import annotations

from app.agents.semantic_interpreter import (
    TurnUnderstanding,
    repair_grounded_semantic_payload,
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
        assert [item.value for item in adapted.acts] == ["explain"]


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
