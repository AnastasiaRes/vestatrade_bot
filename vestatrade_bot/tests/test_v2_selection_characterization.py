from __future__ import annotations

import json

from app.agents.semantic_interpreter import (
    SemanticInterpretationResult,
    TurnUnderstanding,
    repair_grounded_semantic_payload,
)
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.contracts import SelectionResultStatus
from app.catalog_v2.normalization import build_catalog_snapshot
from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.cutover_v2.assembler import build_v2_turn_candidate
from app.cutover_v2.contracts import (
    CutoverDecision,
    ExecutionMode,
    ResponseOwner,
)
from app.dialogue_v2.contracts import DialogueStateV2, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.models import DialogueQAMode, Product


def _frame(
    *,
    operation: str = "continue",
    acts: list[str] | None = None,
    products: list[dict[str, object]] | None = None,
    constraints: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.3",
        "language": "ru",
        "operation": operation,
        "acts": acts or [],
        "products": products or [],
        "constraints": constraints or [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.98,
    }


def _known(
    name: str,
    value: str | int | float,
    evidence: str,
    *,
    unit: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "status": "known",
        "polarity": "required",
        "applies_to_product": 0,
        "evidence": evidence,
    }


def _validated_repair(
    payload: dict[str, object],
    message: str,
    *,
    authoritative_state: dict[str, object] | None = None,
) -> tuple[TurnUnderstanding, tuple[str, ...]]:
    repaired, changes = repair_grounded_semantic_payload(
        payload,
        message,
        authoritative_dialogue_state=authoritative_state,
    )
    return TurnUnderstanding.model_validate(repaired), changes


def test_explicit_show_command_authorizes_preliminary_selection() -> None:
    message = "Покажите варианты"
    understanding, changes = _validated_repair(
        _frame(acts=["find"]),
        message,
        authoritative_state={
            "active_task": {
                "task_id": "task-pump",
                "goal_id": "goal-pump",
                "act": "find",
                "status": "blocked",
            }
        },
    )

    assert [item.kind.value for item in understanding.selection_controls] == [
        "continue_with_confirmed_facts"
    ]
    assert understanding.selection_controls[0].evidence == message
    assert understanding.selection_strategy is not None
    assert understanding.selection_strategy.kind.value == (
        "continue_with_confirmed_facts"
    )
    assert "explicit_show_selection_control_recovered" in changes


def test_radiator_main_canonicalizes_pipe_service_with_provenance() -> None:
    message = (
        "Нужна ППР 25 армированная стекловолокном на радиаторную "
        "магистраль, подача 90 °С"
    )
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "ППР",
                "canonical_type": "pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "ППР",
            }
        ],
        constraints=[
            _known("diameter_mm", 25, "25", unit="mm"),
            _known("reinforcement", "glass_fiber", "стекловолокном"),
            _known("operating_temperature_c", 90, "90 °С", unit="c"),
        ],
    )

    understanding, changes = _validated_repair(payload, message)
    facts = {item.name: item for item in understanding.constraints}

    assert facts["pipe_service"].value == "heating"
    assert facts["pipe_service"].evidence == "радиаторную магистраль"
    assert "pipe_service_recovered_from_radiator_main" in changes


def test_ball_valve_internal_internal_pattern_is_not_lost() -> None:
    message = "Нужны шаровые краны BASE 1/2 вн-вн, штук двадцать"
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "шаровые краны BASE",
                "canonical_type": "ball valve",
                "category": "valves",
                "role": "target",
                "evidence": "шаровые краны BASE",
            }
        ],
        constraints=[_known("connection_size", "1/2", "1/2")],
    )

    understanding, changes = _validated_repair(payload, message)
    facts = {item.name: item for item in understanding.constraints}

    assert facts["connection_pattern"].value == "female_female"
    assert facts["connection_pattern"].evidence == "вн-вн"
    assert "connection_pattern_recovered_from_explicit_pair" in changes


def test_external_sewer_context_corrects_stale_ppr_scope() -> None:
    message = "Мне на улицу, от дома до септика. Покажите что есть"
    payload = _frame(acts=["find"])

    understanding, changes = _validated_repair(
        payload,
        message,
        authoritative_state={
            "active_goal": {
                "goal_id": "goal-pipe",
                "canonical_type": "pipe",
                "category": "pipes",
            },
            "active_task": {
                "task_id": "task-pipe",
                "goal_id": "goal-pipe",
                "act": "find",
                "status": "blocked",
            },
        },
    )

    assert len(understanding.products) == 1
    assert understanding.products[0].canonical_type == "sewer pipe"
    assert understanding.products[0].category.value == "sewer"
    facts = {item.name: item for item in understanding.constraints}
    assert facts["sewer_scope"].value == "external"
    assert "external_sewer_goal_recovered" in changes
    assert understanding.selection_strategy is not None
    assert understanding.selection_strategy.kind.value == (
        "continue_with_confirmed_facts"
    )


def test_bare_pipe_request_does_not_invent_category_or_cards_permission() -> None:
    message = "Нужна труба"
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "труба",
                "canonical_type": "pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "труба",
            }
        ],
    )

    understanding, changes = _validated_repair(payload, message)

    assert understanding.products[0].category.value == "pipes"
    assert understanding.constraints == []
    assert understanding.selection_controls == []
    assert "external_sewer_goal_recovered" not in changes
    assert "explicit_show_selection_control_recovered" not in changes


def _semantic(understanding: TurnUnderstanding) -> SemanticInterpretationResult:
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        model="test/semantic",
        latency_ms=0,
        understanding=understanding,
    )


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    attributes: dict[str, str] | None = None,
    description: str | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        price=1000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=10,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        attributes_normalized=attributes or {},
        description=description,
    )


def _run_v2_turn(
    controller: DialogueControllerV2,
    previous: DialogueStateV2 | None,
    understanding: TurnUnderstanding,
    turn_id: str,
    products: list[Product],
):
    catalog = build_catalog_snapshot(products)
    sources = build_answer_source_snapshot(products, catalog)
    outcome = controller.run(
        previous,
        _semantic(understanding),
        TurnMetadata(turn_id=turn_id),
        policy_enabled=True,
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=False,
        catalog_snapshot=catalog,
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        progress_guard_enabled=True,
        answer_source_snapshot=sources,
    )
    return outcome, sources


def test_pump_show_command_produces_verified_structured_cards() -> None:
    products = [
        _product(
            "VRS.254.18.0",
            "Насос циркуляционный VALTEC RS 25/4-180",
            "Насосное оборудование",
            attributes={"Тип товара": "Насос"},
        ),
        _product(
            "VRS.256.13.0",
            "Насос циркуляционный VALTEC RS 25/6-130",
            "Насосное оборудование",
            attributes={"Тип товара": "Насос"},
        ),
    ]
    first_message = (
        "Циркуляционный насос: расчётный расход 1,5 м3/ч, напор 4 м, "
        "схема радиаторная"
    )
    first = TurnUnderstanding.model_validate(
        _frame(
            operation="new",
            acts=["find"],
            products=[
                {
                    "text": "Циркуляционный насос",
                    "canonical_type": "circulation pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "Циркуляционный насос",
                }
            ],
            constraints=[
                _known(
                    "duty_point_flow_l_h",
                    1.5,
                    "расход 1,5 м3/ч",
                    unit="m3/h",
                ),
                _known("duty_point_head_m", 4, "напор 4 м", unit="m"),
            ],
        )
    )
    controller = DialogueControllerV2()
    opening, _sources = _run_v2_turn(
        controller,
        None,
        first,
        "pump-1",
        products,
    )
    show, _changes = _validated_repair(_frame(acts=["find"]), "Покажите варианты")
    outcome, sources = _run_v2_turn(
        controller,
        opening.state_after,
        show,
        "pump-2",
        products,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="pump-selection",
        turn_id="pump-2",
        original_utterance="Покажите варианты",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_request is not None
    assert candidate.selection_request.action.value == "show"
    assert candidate.selection_result is not None
    assert candidate.selection_result.status == SelectionResultStatus.SHOWN
    assert candidate.selection_result.outcome_gate_passed is True
    assert candidate.response is not None
    assert candidate.selection_result.ordered_skus == tuple(
        item.sku for item in candidate.response.products
    )
    assert candidate.selection_result.cards


def test_ppr_selection_applies_service_diameter_and_reinforcement() -> None:
    target = _product(
        "VTp.700.FB20.25",
        "Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        "Трубы",
        attributes={"Максимальная рабочая температура, °С": "95"},
        description="Для холодного и горячего водоснабжения и отопления.",
    )
    wrong_diameter = _product(
        "VTp.700.FB20.20",
        "Труба PP-FIBER арм. стекл., PN 20, 20 MM (белый)",
        "Трубы",
        attributes={"Максимальная рабочая температура, °С": "95"},
        description="Для холодного и горячего водоснабжения и отопления.",
    )
    wrong_reinforcement = _product(
        "VTp.700.AL25.25",
        "Труба PP-ALUX арм. алюминием, PN 25, 25 MM",
        "Трубы",
        attributes={"Максимальная рабочая температура, °С": "95"},
        description="Для холодного и горячего водоснабжения и отопления.",
    )
    products = [target, wrong_diameter, wrong_reinforcement]
    message = (
        "Нужна ППР 25 армированная стекловолокном на радиаторную "
        "магистраль, подача 90 °С"
    )
    first, _changes = _validated_repair(
        _frame(
            operation="new",
            acts=["find"],
            products=[
                {
                    "text": "ППР",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "ППР",
                }
            ],
            constraints=[
                _known("diameter_mm", 25, "25", unit="mm"),
                _known("reinforcement", "glass_fiber", "стекловолокном"),
                _known("operating_temperature_c", 90, "90 °С", unit="c"),
            ],
        ),
        message,
    )
    controller = DialogueControllerV2()
    opening, _sources = _run_v2_turn(
        controller,
        None,
        first,
        "ppr-1",
        products,
    )
    show, _changes = _validated_repair(_frame(acts=["find"]), "Покажите варианты")
    outcome, sources = _run_v2_turn(
        controller,
        opening.state_after,
        show,
        "ppr-2",
        products,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="ppr-selection",
        turn_id="ppr-2",
        original_utterance="Покажите варианты",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    assert candidate.selection_result.status == SelectionResultStatus.SHOWN
    assert candidate.selection_result.ordered_skus == (target.sku,)
    assert {item.name for item in candidate.selection_result.hard_constraints} >= {
        "diameter_mm",
        "pipe_service",
        "reinforcement",
    }


def test_base_internal_internal_selection_keeps_connection_pattern() -> None:
    target = _product(
        "VT.214.N.04",
        "Кран шаровой BASE 1/2 внутренняя/внутренняя",
        "Водозапорная арматура",
        attributes={"Тип товара": "Кран шаровой"},
    )
    wrong = _product(
        "VT.214.NR.04",
        "Кран шаровой BASE 1/2 внутренняя/наружная",
        "Водозапорная арматура",
        attributes={"Тип товара": "Кран шаровой"},
    )
    products = [target, wrong]
    message = "Нужны шаровые краны BASE 1/2 вн-вн, штук двадцать"
    first, _changes = _validated_repair(
        _frame(
            operation="new",
            acts=["find"],
            products=[
                {
                    "text": "шаровые краны BASE",
                    "canonical_type": "ball valve",
                    "category": "valves",
                    "role": "target",
                    "evidence": "шаровые краны BASE",
                }
            ],
            constraints=[_known("connection_size", "1/2", "1/2")],
        ),
        message,
    )
    controller = DialogueControllerV2()
    opening, _sources = _run_v2_turn(
        controller,
        None,
        first,
        "valve-1",
        products,
    )
    show, _changes = _validated_repair(_frame(acts=["find"]), "Покажите варианты")
    outcome, sources = _run_v2_turn(
        controller,
        opening.state_after,
        show,
        "valve-2",
        products,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="valve-selection",
        turn_id="valve-2",
        original_utterance="Покажите варианты",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    assert candidate.selection_result.status == SelectionResultStatus.SHOWN
    assert candidate.selection_result.ordered_skus == (target.sku,)
    assert any(
        item.name == "connection_pattern" and item.value == "female_female"
        for item in candidate.selection_request.known_facts
    )


def test_explicit_sku_overrides_stale_pipe_product_kind() -> None:
    valve = _product(
        "VT.214.N.04",
        "Кран шаровой BASE 1/2 внутренняя/внутренняя",
        "Водозапорная арматура",
        attributes={"Тип товара": "Кран шаровой"},
    )
    pipe = _product("PIPE-25", "Труба PP-R PN20 25 MM", "Трубы")
    message = "Покажите варианты VT.214.N.04"
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "VT.214.N.04",
                "canonical_type": "pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "VT.214.N.04",
            }
        ],
        constraints=[_known("sku", "VT.214.N.04", "VT.214.N.04")],
    )
    payload["selection_controls"] = [
        {
            "kind": "continue_with_confirmed_facts",
            "evidence": "Покажите варианты",
        }
    ]
    payload["selection_strategy"] = {
        "kind": "continue_with_confirmed_facts",
        "evidence": "Покажите варианты",
    }
    understanding = TurnUnderstanding.model_validate(payload)
    outcome, sources = _run_v2_turn(
        DialogueControllerV2(),
        None,
        understanding,
        "sku-priority-1",
        [valve, pipe],
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="sku-priority",
        turn_id="sku-priority-1",
        original_utterance=message,
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_request is not None
    assert candidate.selection_request.product_kind.value == "ball_valve"
    assert candidate.selection_result is not None
    assert candidate.selection_result.ordered_skus == (valve.sku,)
    assert any(
        "explicit_sku_overrode_stale_product_goal" in item.reason_codes
        for item in outcome.catalog_planning.contract_resolutions
    )


def test_exact_full_product_name_resolves_to_one_catalogue_identity() -> None:
    named = _product(
        "VRS.254.18.0",
        "Насос циркуляционный VALTEC RS 25/4-180 с гайками",
        "Насосное оборудование",
        attributes={"Тип товара": "Насос"},
    )
    other = _product(
        "VRS.256.13.0",
        "Насос циркуляционный VALTEC RS 25/6-130 с гайками",
        "Насосное оборудование",
        attributes={"Тип товара": "Насос"},
    )
    name = named.name
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": name,
                "canonical_type": "circulation pump",
                "category": "pumps",
                "role": "target",
                "evidence": name,
            }
        ],
        constraints=[
            _known("diameter_mm", 25, "25/4-180", unit="mm"),
            _known("max_head_m", 4, "25/4-180", unit="m"),
            _known("mounting_length_mm", 180, "25/4-180", unit="mm"),
        ],
    )
    payload["selection_controls"] = [
        {
            "kind": "continue_with_confirmed_facts",
            "evidence": "Покажите",
        }
    ]
    payload["selection_strategy"] = {
        "kind": "continue_with_confirmed_facts",
        "evidence": "Покажите",
    }
    message = f"Покажите {name}"
    outcome, sources = _run_v2_turn(
        DialogueControllerV2(),
        None,
        TurnUnderstanding.model_validate(payload),
        "named-product-1",
        [named, other],
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="named-product",
        turn_id="named-product-1",
        original_utterance=message,
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    assert candidate.selection_result.ordered_skus == (named.sku,)
    assert any(
        item.name == "sku"
        and item.value == named.sku
        and item.source == "catalog_exact_name_resolution"
        for item in candidate.selection_request.known_facts
    )
    assert not {
        "diameter_mm",
        "max_head_m",
        "mounting_length_mm",
    }.intersection(item.name for item in candidate.selection_request.known_facts)


def test_external_sewer_request_returns_typed_no_match_not_ppr_cards() -> None:
    products = [
        _product(
            "HTEM-50-750",
            "Труба канализационная, HTEM, 50*750",
            "Канализационные системы",
            attributes={"Тип товара": "Труба"},
        ),
        _product(
            "VTp.700.FB20.25",
            "Труба PP-FIBER арм. стекл., PN 20, 25 MM",
            "Трубы",
        ),
    ]
    message = "Мне на улицу, от дома до септика. Покажите что есть"
    understanding, _changes = _validated_repair(
        _frame(acts=["find"]),
        message,
        authoritative_state={
            "active_goal": {
                "goal_id": "goal-pipe",
                "canonical_type": "pipe",
                "category": "pipes",
            }
        },
    )
    outcome, sources = _run_v2_turn(
        DialogueControllerV2(),
        None,
        understanding,
        "sewer-1",
        products,
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="sewer-selection",
        turn_id="sewer-1",
        original_utterance=message,
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    assert candidate.selection_result.product_kind.value == "sewer_pipe"
    assert candidate.selection_result.status == SelectionResultStatus.NO_MATCH
    assert candidate.selection_result.ordered_skus == ()
    assert candidate.response is not None
    assert candidate.response.products == []


def test_bare_pipe_yields_one_typed_critical_question() -> None:
    product = _product(
        "PIPE-25",
        "Труба PP-R PN 20, 25 MM",
        "Трубы",
    )
    understanding, _changes = _validated_repair(
        _frame(
            operation="new",
            acts=["find"],
            products=[
                {
                    "text": "труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "труба",
                }
            ],
        ),
        "Нужна труба",
    )
    outcome, sources = _run_v2_turn(
        DialogueControllerV2(),
        None,
        understanding,
        "bare-pipe-1",
        [product],
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="bare-pipe",
        turn_id="bare-pipe-1",
        original_utterance="Нужна труба",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    assert candidate.selection_result.status == SelectionResultStatus.NEED_CLARIFICATION
    assert candidate.selection_result.missing_critical_fact == "pipe_service"
    assert candidate.selection_result.cards == ()


def test_committed_selection_atomically_updates_customer_visible_scope(
    tmp_path,
) -> None:
    valve = _product(
        "VT.214.N.04",
        "Кран шаровой BASE 1/2 внутренняя/внутренняя",
        "Водозапорная арматура",
        attributes={"Тип товара": "Кран шаровой"},
    )
    message = "Покажите варианты VT.214.N.04"
    payload = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "VT.214.N.04",
                "canonical_type": "ball valve",
                "category": "valves",
                "role": "target",
                "evidence": "VT.214.N.04",
            }
        ],
        constraints=[_known("sku", "VT.214.N.04", "VT.214.N.04")],
    )
    payload["selection_controls"] = [
        {
            "kind": "continue_with_confirmed_facts",
            "evidence": "Покажите варианты",
        }
    ]
    payload["selection_strategy"] = {
        "kind": "continue_with_confirmed_facts",
        "evidence": "Покажите варианты",
    }
    outcome, sources = _run_v2_turn(
        DialogueControllerV2(),
        None,
        TurnUnderstanding.model_validate(payload),
        "commit-selection-1",
        [valve],
    )
    candidate = build_v2_turn_candidate(
        outcome,
        sources,
        session_id="commit-selection",
        turn_id="commit-selection-1",
        original_utterance=message,
    )
    assert candidate.eligible_for_delivery is True
    assert candidate.selection_result is not None
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "selection-commit.jsonl",
        }
    )
    bot = ChatOrchestrator(settings=settings, products=[valve])
    # The commit gate compares against the exact immutable source snapshot
    # used to assemble this candidate; unit setup bypasses the normal single
    # orchestrator pipeline, so bind that same snapshot explicitly.
    bot.answer_source_snapshot_v2 = sources
    before = bot.sessions.snapshot("commit-selection")
    decision = CutoverDecision(
        owner_candidate=ResponseOwner.V2,
        execution_mode=ExecutionMode.V2_PRIMARY,
        eligible=True,
        reason_codes=("test_protected_preview",),
        catalog_revision=sources.source_revision,
    )

    response, commit = bot._commit_v2_response(
        before,
        message,
        "client-commit-selection-1",
        "commit-selection-1",
        decision,
        candidate,
    )
    stored = bot.sessions.snapshot("commit-selection")

    assert commit.committed is True
    assert [item.sku for item in response.products] == [valve.sku]
    assert [item.sku for item in stored.last_products] == [valve.sku]
    assert [item.sku for item in stored.v2_last_products] == [valve.sku]
    assert stored.shown_product_skus == [valve.sku]
    assert stored.shown_result_signature == candidate.selection_result.selection_id
    assert stored.product_focus is not None
    assert stored.product_focus.sku == valve.sku
    assert stored.live_dialogue_state_v2 is not None


def test_v2_cards_feed_next_ordinal_product_fact_without_legacy_setup(
    tmp_path,
    monkeypatch,
) -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос циркуляционный VALTEC RS 25/4-180",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Насос",
            "Монтажная длина, мм": "180",
        },
    )
    first = TurnUnderstanding.model_validate(
        _frame(
            operation="new",
            acts=["find"],
            products=[
                {
                    "text": "Циркуляционный насос",
                    "canonical_type": "circulation pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "Циркуляционный насос",
                }
            ],
            constraints=[
                _known(
                    "duty_point_flow_l_h",
                    1.5,
                    "расход 1,5 м3/ч",
                    unit="m3/h",
                ),
                _known("duty_point_head_m", 4, "напор 4 м", unit="m"),
            ],
        )
    )
    show, _changes = _validated_repair(_frame(acts=["find"]), "Покажите варианты")
    direct = TurnUnderstanding.model_validate(
        {
            **_frame(operation="continue", acts=["explain"]),
            "references": [
                {
                    "kind": "ordinal",
                    "text": "первого",
                    "target_hint": "1",
                    "evidence": "первого",
                }
            ],
            "information_requests": [
                {
                    "fact_name": "mounting_length_mm",
                    "purpose": "value",
                    "requested_outputs": ["explanation"],
                    "output_relation": "all",
                    "source_kind": "technical_documentation",
                    "act": "explain",
                    "subject_scope": "presented_candidates",
                    "applies_to_product": None,
                    "evidence": "Какая у первого монтажная длина?",
                }
            ],
        }
    )
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "embeddings_enabled": False,
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "cards-to-fact.jsonl",
            "dialogue_v2_routing_enabled": False,
            "dialogue_v2_shadow_compare_enabled": False,
            "dialogue_v2_live_delivery_enabled": False,
            "dialogue_v2_internal_canary_enabled": False,
            "dialogue_v2_internal_canary_percent": 0,
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "qa-secret",
            "commerce_external_execution_enabled": False,
        }
    )
    bot = ChatOrchestrator(settings=settings, products=[pump])
    semantic_results = iter((_semantic(first), _semantic(show), _semantic(direct)))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda *_args, **_kwargs: next(semantic_results),
    )

    bot.handle_chat(
        "cards-to-fact",
        "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
        client_turn_id="cards-to-fact-1",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    cards = bot.handle_chat(
        "cards-to-fact",
        "Покажите варианты",
        client_turn_id="cards-to-fact-2",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    fact = bot.handle_chat(
        "cards-to-fact",
        "Какая у первого монтажная длина?",
        client_turn_id="cards-to-fact-3",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )

    assert [item.sku for item in cards.products] == [pump.sku]
    assert "180 мм" in fact.answer
    assert [item.sku for item in fact.products] == [pump.sku]
    stored = bot.sessions.snapshot("cards-to-fact")
    assert [item.sku for item in stored.last_products] == [pump.sku]
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert traces[-2]["cutover_v2"]["decision"]["owner_candidate"] == "v2"
    assert traces[-2]["cutover_v2"]["selection_delivery"]["status"] == "shown"
    assert traces[-2]["cutover_v2"]["selection_delivery"][
        "customer_visible_state_updated"
    ] is True
    assert traces[-1]["cutover_v2"]["decision"]["owner_candidate"] == "v2"
