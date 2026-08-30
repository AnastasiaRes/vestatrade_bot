"""Characterization tests for Legacy-style preference intent in V2 Selection."""

from __future__ import annotations

from app.agents.semantic_interpreter import (
    TurnUnderstanding,
    repair_grounded_semantic_payload,
)
from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.answer_v2.planner import _presentable_candidate_shortlist
from app.catalog_v2.contracts import (
    CandidateAssessment,
    CandidateStatus,
    CatalogProductRole,
    CatalogSearchPlan,
    CatalogSearchStage,
    CatalogFact,
    FactProvenance,
    ProductKind,
)
from app.catalog_v2.registry import canonical_brand, resolve_brand_mentions
from app.dialogue_v2.contracts import (
    CustomerTask,
    DeliveredSelectionScope,
    DialogueStateV2,
    ProductCategory,
    SelectionPreferenceKind,
    SelectionPreferenceSignal,
    TaskAct,
    TaskStatus,
    TurnMetadata,
)
from app.dialogue_v2.reducer import reduce_dialogue_state


def _frame(*, acts: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.3",
        "language": "ru",
        "operation": "continue",
        "acts": acts or ["find"],
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_preferences": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.99,
    }


def _repair(message: str, *, acts: list[str] | None = None) -> TurnUnderstanding:
    payload, _changes = repair_grounded_semantic_payload(_frame(acts=acts), message)
    return TurnUnderstanding.model_validate(payload)


def _task() -> CustomerTask:
    return CustomerTask(
        task_id="pump-task",
        target_goal_id="pump-goal",
        act=TaskAct.FIND,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
        created_turn=1,
        last_addressed_turn=1,
    )


def _state(*preferences: SelectionPreferenceSignal) -> DialogueStateV2:
    return DialogueStateV2(
        turn_number=1,
        active_goal_id="pump-goal",
        tasks=(_task(),),
        selection_preferences=preferences,
    )


def _preference(kind: SelectionPreferenceKind, *, value: str | bool | None = None) -> SelectionPreferenceSignal:
    return SelectionPreferenceSignal(
        preference_id=f"pref-{kind.value}",
        kind=kind,
        task_id="pump-task",
        goal_id="pump-goal",
        value=value,
        evidence=kind.value,
        source="test",
        source_turn=2,
    )


def _source(*items: tuple[str, float, str]) -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="feed-revision",
        products=tuple(
            CatalogAnswerProduct(
                sku=sku,
                name=sku,
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                price=price,
                currency="RUB",
                stock_status="в наличии",
                stock_qty=5,
                url=f"https://example.test/{sku}",
                facts=(
                    CatalogFact(
                        name="brand",
                        value=brand,
                        provenance=FactProvenance(
                            source="identity",
                            source_field="brand",
                            raw_value=brand,
                            parser="test",
                        ),
                    ),
                ),
            )
            for sku, price, brand in items
        ),
    )


def _plan(*skus: str) -> CatalogSearchPlan:
    return CatalogSearchPlan(
        plan_id="pump-plan",
        task_id="pump-task",
        goal_id="pump-goal",
        contract_id="circulation_pump",
        product_kind=ProductKind.CIRCULATION_PUMP,
        requested_role=CatalogProductRole.BASE_PRODUCT,
        stages=(CatalogSearchStage.STRICT_SAME_KIND,),
        candidate_assessments=tuple(
            CandidateAssessment(
                sku=sku,
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                status=CandidateStatus.ELIGIBLE,
            )
            for sku in skus
        ),
        eligible_skus=skus,
    )


def test_semantic_repair_keeps_brand_price_and_stock_preferences_typed() -> None:
    frame = _repair("Нужен насос только VALTEC, подешевле и только в наличии")

    assert {(item.kind.value, item.value) for item in frame.selection_preferences} == {
        ("brand_required", "VALTEC"),
        ("price_lowest", None),
        ("stock_required", True),
    }
    facts = {(item.name, item.value, item.polarity.value) for item in frame.constraints}
    assert ("brand", "VALTEC", "required") in facts
    assert ("stock_availability", True, "required") in facts


def test_brand_aliases_are_catalogue_bound_and_not_valtec_only() -> None:
    assert canonical_brand("Вальтек") == "VALTEC"
    assert canonical_brand("Вило") == "WILO"
    assert canonical_brand("аристон") == "ARISTON"
    assert canonical_brand("Неизвестная марка") is None

    mentions = resolve_brand_mentions("Нужен Вило или Вальтек")
    assert [(item.canonical, item.alias) for item in mentions] == [
        ("WILO", "Вило"),
        ("VALTEC", "Вальтек"),
    ]


def test_semantic_repair_recovers_preference_for_any_known_brand() -> None:
    frame = _repair("Нужен насос только Вило, недорогой")

    assert {(item.kind.value, item.value) for item in frame.selection_preferences} == {
        ("brand_required", "WILO"),
        ("price_lowest", None),
    }
    facts = {(item.name, item.value, item.polarity.value) for item in frame.constraints}
    assert ("brand", "WILO", "required") in facts


def test_semantic_repair_treats_one_named_brand_in_selection_as_required() -> None:
    frame = _repair(
        "Нужен циркуляционный насос Вило, покажите варианты",
        acts=["find"],
    )

    assert {(item.kind.value, item.value) for item in frame.selection_preferences} == {
        ("brand_required", "WILO"),
    }
    facts = {(item.name, item.value, item.polarity.value) for item in frame.constraints}
    assert ("brand", "WILO", "required") in facts


def test_price_synonyms_become_price_preference_without_changing_category() -> None:
    for message in (
        "Нужен бюджетный насос",
        "Нужен недорогой насос",
        "Нужен самый дешёвый насос",
        "Нужен насос не дороже",
    ):
        frame = _repair(message)
        assert {(item.kind.value, item.value) for item in frame.selection_preferences} == {
            ("price_lowest", None),
        }


def test_visible_scope_cheapest_question_remains_compare_not_new_search_preference() -> None:
    frame = _repair("Какой из показанных дешевле?", acts=["compare"])

    assert frame.acts == ["compare"]
    assert frame.selection_preferences == []


def test_stock_check_does_not_become_an_in_stock_filter() -> None:
    frame = _repair("11677 есть в наличии?", acts=["check_stock"])

    assert frame.selection_preferences == []
    assert not any(item.name == "stock_availability" for item in frame.constraints)


def test_reducer_binds_preference_to_current_discovery_task() -> None:
    understanding = TurnUnderstanding.model_validate(
        {
            **_frame(),
            "selection_preferences": [
                {
                    "kind": "price_lowest",
                    "value": None,
                    "evidence": "подешевле",
                }
            ],
        }
    )
    reduced = reduce_dialogue_state(
        _state(),
        understanding,
        TurnMetadata(turn_id="preference-turn"),
    )

    assert len(reduced.state.selection_preferences) == 1
    signal = reduced.state.selection_preferences[0]
    assert signal.task_id == "pump-task"
    assert signal.goal_id == "pump-goal"
    assert signal.kind == SelectionPreferenceKind.PRICE_LOWEST


def test_price_preference_orders_only_already_presentable_candidates() -> None:
    source = _source(("PUMP-500", 500, "OTHER"), ("PUMP-300", 300, "OTHER"))
    selected, order, _ = _presentable_candidate_shortlist(
        (_plan("PUMP-500", "PUMP-300"),),
        source,
        dialogue_state=_state(_preference(SelectionPreferenceKind.PRICE_LOWEST)),
        task_order=("pump-task",),
    )

    assert selected == {("pump-plan", "PUMP-500"), ("pump-plan", "PUMP-300")}
    assert order[("pump-plan", "PUMP-300")] == 0
    assert order[("pump-plan", "PUMP-500")] == 1


def test_default_valtec_tiebreak_precedes_price_for_ordinary_selection() -> None:
    source = _source(
        ("A-OTHER", 500, "OTHER"),
        ("Z-VALTEC", 500, "VALTEC"),
        ("CHEAPER-OTHER", 300, "OTHER"),
    )
    selected, order, _ = _presentable_candidate_shortlist(
        (_plan("A-OTHER", "Z-VALTEC", "CHEAPER-OTHER"),),
        source,
        dialogue_state=_state(),
        task_order=("pump-task",),
    )

    assert selected == {
        ("pump-plan", "A-OTHER"),
        ("pump-plan", "Z-VALTEC"),
        ("pump-plan", "CHEAPER-OTHER"),
    }
    # Restore the historical default commercial policy: among technically
    # equal, available cards VALTEC is listed first.  This is overridden only
    # by an explicit preference such as «подешевле».
    assert order[("pump-plan", "Z-VALTEC")] == 0
    assert order[("pump-plan", "CHEAPER-OTHER")] == 1
    assert order[("pump-plan", "A-OTHER")] == 2


def test_price_preference_overrides_default_valtec_tiebreak() -> None:
    source = _source(
        ("PUMP-VALTEC-500", 500, "VALTEC"),
        ("PUMP-OTHER-300", 300, "OTHER"),
    )
    selected, order, _ = _presentable_candidate_shortlist(
        (_plan("PUMP-VALTEC-500", "PUMP-OTHER-300"),),
        source,
        dialogue_state=_state(_preference(SelectionPreferenceKind.PRICE_LOWEST)),
        task_order=("pump-task",),
    )

    assert selected == {
        ("pump-plan", "PUMP-VALTEC-500"),
        ("pump-plan", "PUMP-OTHER-300"),
    }
    assert order[("pump-plan", "PUMP-OTHER-300")] == 0
    assert order[("pump-plan", "PUMP-VALTEC-500")] == 1


def test_relative_price_preference_uses_only_same_goal_delivered_scope() -> None:
    source = _source(
        ("PUMP-500", 500, "OTHER"),
        ("PUMP-300", 300, "OTHER"),
        ("PUMP-700", 700, "OTHER"),
    )
    state = _state(_preference(SelectionPreferenceKind.PRICE_BELOW_REFERENCE))
    state = state.model_copy(
        update={
            "delivered_selection_scopes": (
                DeliveredSelectionScope(
                    scope_id="scope-old-pumps",
                    goal_id="pump-goal",
                    task_id="pump-task",
                    selection_id="selection-old-pumps",
                    ordered_skus=("PUMP-500",),
                    catalog_revision="feed-revision",
                    delivery_id="delivery-old-pumps",
                    source_turn=1,
                ),
            )
        }
    )
    selected, order, _ = _presentable_candidate_shortlist(
        (_plan("PUMP-500", "PUMP-300", "PUMP-700"),),
        source,
        dialogue_state=state,
        task_order=("pump-task",),
    )

    assert selected == {("pump-plan", "PUMP-300")}
    assert order == {("pump-plan", "PUMP-300"): 0}


def test_relative_price_preference_cannot_leak_to_another_goal() -> None:
    source = _source(
        ("PUMP-500", 500, "OTHER"),
        ("PUMP-300", 300, "OTHER"),
        ("VALVE-500", 500, "OTHER"),
        ("VALVE-300", 300, "OTHER"),
    )
    valve_task = CustomerTask(
        task_id="valve-task",
        target_goal_id="valve-goal",
        act=TaskAct.FIND,
        priority=1,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
        created_turn=1,
        last_addressed_turn=1,
    )
    state = _state(_preference(SelectionPreferenceKind.PRICE_BELOW_REFERENCE))
    state = state.model_copy(
        update={
            "tasks": (_task(), valve_task),
            "delivered_selection_scopes": (
                DeliveredSelectionScope(
                    scope_id="scope-old-pumps",
                    goal_id="pump-goal",
                    task_id="pump-task",
                    selection_id="selection-old-pumps",
                    ordered_skus=("PUMP-500",),
                    catalog_revision="feed-revision",
                    delivery_id="delivery-old-pumps",
                    source_turn=1,
                ),
            ),
        }
    )
    valve_plan = _plan("VALVE-500", "VALVE-300").model_copy(
        update={
            "plan_id": "valve-plan",
            "task_id": "valve-task",
            "goal_id": "valve-goal",
        }
    )
    selected, _order, _ = _presentable_candidate_shortlist(
        (_plan("PUMP-500", "PUMP-300"), valve_plan),
        source,
        dialogue_state=state,
        task_order=("pump-task", "valve-task"),
    )

    # Only the pump task has both the request and the reference scope.  The
    # other task keeps its whole technically presentable candidate set.
    assert selected == {
        ("pump-plan", "PUMP-300"),
        ("valve-plan", "VALVE-500"),
        ("valve-plan", "VALVE-300"),
    }
