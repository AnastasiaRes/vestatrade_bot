from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogProductRole,
    FactProvenance,
    ProductKind,
)
from app.cutover_v2.contracts import V2TurnCandidate
from app.cutover_v2.offer_fact import build_v2_offer_fact_candidate
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    CustomerTask,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ProductCard, ProductFocusState, SessionState
from app.offer_fact_v2.contracts import (
    OfferFactKind,
    OfferFactReferenceKind,
    OfferFactStatus,
)
from app.offer_fact_v2.service import (
    build_offer_fact_request,
    build_offer_fact_result,
    render_offer_fact_result,
)


def _product(sku: str, price: float, stock: str = "в наличии") -> CatalogAnswerProduct:
    return CatalogAnswerProduct(
        sku=sku,
        name=f"Насос {sku}",
        product_kind=ProductKind.CIRCULATION_PUMP,
        role=CatalogProductRole.BASE_PRODUCT,
        price=price,
        currency="RUB",
        stock_status=stock,
        stock_qty=0 if stock == "нет в наличии" else 5,
        url=f"https://example.test/{sku}",
        image_url=None,
    )


def _snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product("53843", 4_500),
            _product("68/2/8", 5_000, stock="нет в наличии"),
        ),
    )


def _boiler_snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-boiler-v1",
        products=(
            CatalogAnswerProduct(
                sku="2202210",
                name="Котел электрический Arderia E9, 9 кВт",
                product_kind=ProductKind.ELECTRIC_BOILER,
                role=CatalogProductRole.BASE_PRODUCT,
                price=35_365,
                currency="RUB",
                stock_status="в наличии",
                stock_qty=2,
                url="https://example.test/2202210",
                facts=(
                    CatalogFact(
                        name="brand",
                        value="Arderia",
                        provenance=FactProvenance(
                            source="identity",
                            source_field="brand",
                            raw_value="Arderia",
                            parser="test",
                        ),
                    ),
                ),
            ),
        ),
    )


def _session(snapshot: AnswerSourceSnapshot) -> SessionState:
    cards = [
        ProductCard(
            sku=item.sku,
            name=item.name,
            price=item.price or 0,
            currency=item.currency or "RUB",
            stock_status=item.stock_status or "",
            stock_qty=item.stock_qty,
            url=item.url or "",
        )
        for item in snapshot.products
    ]
    return SessionState(
        session_id="offer-fact",
        v2_last_products=cards,
        last_products=cards,
        v2_selection_id="selection-pumps",
        v2_source_revision=snapshot.source_revision,
    )


def _outcome() -> DialogueV2Outcome:
    task = CustomerTask(
        task_id="price-task",
        act=TaskAct.CHECK_PRICE,
        target_goal_id="goal-pump",
        priority=0,
        source="test",
        source_turn=2,
    )
    state = DialogueStateV2(
        turn_number=2,
        active_goal_id="goal-pump",
        tasks=(task,),
        answer_plan_summary=AnswerPlanSummary(
            plan_id="shown-plan",
            semantic_signature="shown",
            task_ids=(task.task_id,),
            primary_action=NextActionKind.ANSWER_DIRECT_QUESTION,
            next_step_kind="provide_direct_answer",
            validation_status="accepted",
            delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
            selection_id="selection-pumps",
            catalog_revision="source-v1",
            source_turn=2,
        ),
    )
    return DialogueV2Outcome(
        status="applied",
        state_before=state.model_copy(update={"turn_number": 1}),
        state_after=state,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.ANSWER_DIRECT_QUESTION,
                task_id=task.task_id,
                reason_code="direct_price_question",
            ),
            task_ids=(task.task_id,),
        ),
    )


def test_second_visible_card_price_is_a_direct_offer_fact_not_calculation() -> None:
    snapshot = _snapshot()
    request = build_offer_fact_request(
        _outcome(),
        _session(snapshot),
        snapshot,
        original_utterance="Сколько стоит второй вариант?",
    )

    assert request is not None
    assert request.product_ref.canonical_sku == "68/2/8"
    result = build_offer_fact_result(request, snapshot)
    assert result.status == OfferFactStatus.ANSWERED
    assert result.sku == "68/2/8"
    assert result.value == 5_000
    assert result.outcome_gate_passed is True


def test_explicit_numeric_and_slash_sku_read_stock_without_in_stock_filtering() -> None:
    snapshot = _snapshot()
    session = _session(snapshot)
    for message, expected in (
        ("У товара 53843 какая цена?", "53843"),
        ("68/2/8 есть в наличии?", "68/2/8"),
    ):
        request = build_offer_fact_request(
            _outcome(), session, snapshot, original_utterance=message
        )
        assert request is not None
        result = build_offer_fact_result(request, snapshot)
        assert result.status == OfferFactStatus.ANSWERED
        assert result.sku == expected
    assert build_offer_fact_result(
        build_offer_fact_request(
            _outcome(), session, snapshot, original_utterance="68/2/8 есть в наличии?"
        ),
        snapshot,
    ).value == "нет в наличии"


def test_price_without_reference_asks_for_one_product_instead_of_arithmetic() -> None:
    snapshot = _snapshot()
    request = build_offer_fact_request(
        _outcome(),
        _session(snapshot),
        snapshot,
        original_utterance="Сколько стоит?",
    )
    assert request is not None
    result = build_offer_fact_result(request, snapshot)
    assert result.status == OfferFactStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True


def test_quantity_request_is_left_to_existing_calculation_service() -> None:
    assert build_offer_fact_request(
        _outcome(),
        _session(_snapshot()),
        _snapshot(),
        original_utterance="Посчитай 2 шт. второго",
    ) is None


def test_offer_candidate_preserves_selection_identity_and_cards_scope() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    request = build_offer_fact_request(
        outcome,
        _session(snapshot),
        snapshot,
        original_utterance="Сколько стоит второй вариант?",
    )
    assert request is not None
    result = build_offer_fact_result(request, snapshot)
    base = V2TurnCandidate(
        turn_id="offer-turn",
        state_before=outcome.state_before,
        state_after=outcome.state_after,
        validation_status="not_run",
    )
    candidate = build_v2_offer_fact_candidate(
        outcome,
        base,
        result,
        snapshot,
        session_id="offer-fact",
        turn_id="offer-turn",
    )

    assert candidate is not None
    assert candidate.product_scope_effect.value == "preserve"
    assert candidate.state_after.answer_plan_summary is not None
    assert candidate.state_after.answer_plan_summary.selection_id == "selection-pumps"
    assert [item.sku for item in candidate.response.products] == ["68/2/8"]


def test_named_single_product_card_uses_catalogue_identity_without_readiness() -> None:
    snapshot = _boiler_snapshot()
    request = build_offer_fact_request(
        _outcome(),
        SessionState(session_id="named-boiler"),
        snapshot,
        original_utterance="Котёл электрический Arderia E9. Покажите карточку.",
    )

    assert request is not None
    assert request.fact_kind == OfferFactKind.CARD
    assert request.product_ref.kind == OfferFactReferenceKind.NAMED_PRODUCT
    assert request.product_ref.canonical_sku == "2202210"
    result = build_offer_fact_result(request, snapshot)
    assert result.status == OfferFactStatus.ANSWERED
    assert result.sku == "2202210"
    assert result.source is not None
    assert result.source.field_name == "product_card"

    candidate = build_v2_offer_fact_candidate(
        _outcome(),
        V2TurnCandidate(
            turn_id="named-boiler-card",
            state_before=_outcome().state_before,
            state_after=_outcome().state_after,
            validation_status="not_run",
        ),
        result,
        snapshot,
        session_id="named-boiler",
        turn_id="named-boiler-card",
    )
    assert candidate is not None
    assert candidate.focus_product_sku == "2202210"
    assert [item.sku for item in candidate.response.products] == ["2202210"]


def test_named_product_price_accepts_inflected_price_and_stock_wording() -> None:
    snapshot = _boiler_snapshot()
    request = build_offer_fact_request(
        _outcome(),
        SessionState(session_id="named-boiler-price"),
        snapshot,
        original_utterance="Покажите цену и наличие Arderia E9",
    )

    assert request is not None
    assert request.fact_kind == OfferFactKind.PRICE
    assert request.product_ref.kind == OfferFactReferenceKind.NAMED_PRODUCT
    assert request.product_ref.canonical_sku == "2202210"
    result = build_offer_fact_result(request, snapshot)
    assert result.status == OfferFactStatus.ANSWERED
    assert result.sku == "2202210"
    assert result.value == 35_365


def test_quantified_stock_checks_resolved_product_without_calculation() -> None:
    snapshot = _boiler_snapshot()
    for message, expected in (
        ("Котёл электрический Arderia E9: есть 2 шт?", True),
        ("Котёл электрический Arderia E9: есть 3 шт?", False),
    ):
        request = build_offer_fact_request(
            _outcome(),
            SessionState(session_id="named-boiler-stock"),
            snapshot,
            original_utterance=message,
        )
        assert request is not None
        assert request.fact_kind == OfferFactKind.STOCK
        assert request.requested_quantity in {2, 3}
        assert request.product_ref.canonical_sku == "2202210"
        result = build_offer_fact_result(request, snapshot)
        assert result.status == OfferFactStatus.ANSWERED
        assert result.available_quantity == 2
        rendered = render_offer_fact_result(result)
        assert ("Да," in rendered) is expected
        assert ("Нет," in rendered) is not expected


def test_quantified_stock_can_follow_a_v2_contextual_product_focus() -> None:
    snapshot = _boiler_snapshot()
    session = SessionState(
        session_id="named-boiler-focus",
        product_focus=ProductFocusState(
            sku="2202210",
            category="Котлы",
            origin="v2_contextual_product",
        ),
    )
    request = build_offer_fact_request(
        _outcome(),
        session,
        snapshot,
        original_utterance="А есть 2 шт?",
    )

    assert request is not None
    assert request.fact_kind == OfferFactKind.STOCK
    assert request.product_ref.kind == OfferFactReferenceKind.CURRENT_FOCUS
    assert request.product_ref.canonical_sku == "2202210"
    assert build_offer_fact_result(request, snapshot).available_quantity == 2
