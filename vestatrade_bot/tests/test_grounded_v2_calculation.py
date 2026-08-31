from __future__ import annotations

from decimal import Decimal

from app.agents.orchestrator import ChatOrchestrator
from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.answer_v2.sources import build_answer_source_snapshot
from app.calculation_v2.contracts import (
    CalculationResultStatus,
    CalculationScopeOrigin,
    CalculationUnit,
    StockAssessment,
)
from app.calculation_v2.renderer import render_calculation_result
from app.calculation_v2.service import (
    build_calculation_request,
    build_calculation_result,
    validate_calculation_result,
)
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogProductRole,
    CatalogProductSnapshot,
    FactProvenance,
    ProductKind,
)
from app.config import get_settings
from app.cutover_v2.calculation import build_v2_calculation_candidate
from app.cutover_v2.engineering_boundary import (
    build_v2_hydraulic_system_boundary_candidate,
    hydraulic_system_calculation_evidence,
)
from app.cutover_v2.contracts import (
    CutoverDecision,
    ExecutionMode,
    ResponseOwner,
    V2TurnCandidate,
)
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
from app.dialogue_v2.seller_policy import SellerPolicy
from app.models import Product, ProductCard, ProductFocusState, SessionState


def _fact(name: str, value: object, unit: str | None = None) -> CatalogFact:
    return CatalogFact(
        name=name,
        value=value,  # type: ignore[arg-type]
        unit=unit,
        provenance=FactProvenance(
            source="attribute",
            source_field=name,
            raw_value=str(value),
            parser="test",
        ),
    )


def _product(
    sku: str,
    *,
    price: float = 452,
    stock_qty: int | None = 57,
    facts: tuple[CatalogFact, ...] = (),
) -> CatalogAnswerProduct:
    return CatalogAnswerProduct(
        sku=sku,
        name=f"Кран {sku}",
        product_kind=ProductKind.BALL_VALVE,
        role=CatalogProductRole.BASE_PRODUCT,
        price=price,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=stock_qty,
        url=f"https://example.test/{sku}",
        image_url=None,
        facts=facts,
    )


def _snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product("VT.217.N.04", facts=(_fact("stock_unit", "pcs"),)),
            _product(
                "VT.214.N.04",
                price=498,
                stock_qty=5,
                facts=(_fact("stock_unit", "pcs"),),
            ),
            _product(
                "PIPE-M",
                price=114,
                facts=(_fact("price_unit", "m"),),
            ),
            _product("PIPE-UNKNOWN", price=114),
        ),
    )


def _card(product: CatalogAnswerProduct) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        price=product.price or 0,
        currency=product.currency or "RUB",
        stock_status=product.stock_status or "",
        stock_qty=product.stock_qty,
        url=product.url or "",
        image_url=product.image_url,
    )


def _outcome(*, selection_id: str = "selection-v1") -> DialogueV2Outcome:
    summary = AnswerPlanSummary(
        plan_id="shown-plan",
        semantic_signature="shown-signature",
        task_ids=("selection-task",),
        primary_action=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
        next_step_kind="show_preliminary_options",
        validation_status="accepted",
        delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
        selection_id=selection_id,
        catalog_revision="source-v1",
        source_turn=1,
    )
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="calculate-task",
                act=TaskAct.CALCULATE,
                target_goal_id="goal-valve",
                priority=0,
                source="semantic_interpreter",
                source_turn=2,
            ),
        ),
        answer_plan_summary=summary,
    )
    return DialogueV2Outcome(
        status="applied",
        state_before=state.model_copy(update={"turn_number": 1}),
        state_after=state,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.CALCULATE_PRELIMINARY,
                task_id="calculate-task",
                reason_code="explicit_calculation_request",
            ),
            task_ids=("calculate-task",),
        ),
    )


def _session(snapshot: AnswerSourceSnapshot, skus: tuple[str, ...] = ("VT.217.N.04",)) -> SessionState:
    products = tuple(snapshot.product(sku) for sku in skus)
    cards = [_card(item) for item in products if item is not None]
    return SessionState(
        session_id="calculation",
        v2_last_products=cards,
        last_products=cards,
        v2_selection_id="selection-v1",
        v2_source_revision=snapshot.source_revision,
    )


def _base_candidate(outcome: DialogueV2Outcome) -> V2TurnCandidate:
    return V2TurnCandidate(
        turn_id="calculate-turn",
        state_before=outcome.state_before,
        state_after=outcome.state_after,
        validation_status="not_run",
    )


def _result(message: str, *, session: SessionState | None = None):
    snapshot = _snapshot()
    request = build_calculation_request(
        _outcome(),
        session or _session(snapshot),
        snapshot,
        original_utterance=message,
    )
    assert request is not None
    return request, validate_calculation_result(
        request,
        build_calculation_result(request, snapshot),
        snapshot,
    )


def test_single_visible_card_calculates_catalogue_price_and_proven_stock() -> None:
    request, result = _result("Сколько выйдет за двадцать штук?")

    assert request.scope_origin == CalculationScopeOrigin.V2_DELIVERED
    assert request.quantity == Decimal("20")
    assert result.status == CalculationResultStatus.CALCULATED
    assert result.outcome_gate_passed is True
    assert result.sku == "VT.217.N.04"
    assert result.total == Decimal("9040")
    assert result.stock_assessment == StockAssessment.SUFFICIENT
    assert result.stock_delta == Decimal("37")
    assert "9040 ₽" in render_calculation_result(result)


def test_multiple_visible_cards_require_a_subject_instead_of_multiplying_all() -> None:
    snapshot = _snapshot()
    request, result = _result(
        "Посчитай двадцать штук",
        session=_session(snapshot, ("VT.217.N.04", "VT.214.N.04")),
    )

    assert request.product_ref.canonical_sku is None
    assert result.status == CalculationResultStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True
    assert "какой из показанных" in result.clarification.lower()
    assert result.total is None


def test_ordinal_binds_to_the_actual_customer_visible_card_and_checks_shortage() -> None:
    snapshot = _snapshot()
    request, result = _result(
        "Посчитай 20 шт. второго",
        session=_session(snapshot, ("VT.217.N.04", "VT.214.N.04")),
    )

    assert request.product_ref.canonical_sku == "VT.214.N.04"
    assert result.status == CalculationResultStatus.CALCULATED
    assert result.total == Decimal("9960")
    assert result.stock_assessment == StockAssessment.INSUFFICIENT
    assert result.stock_delta == Decimal("-15")
    assert "не хватает 15 шт." in render_calculation_result(result)


def test_deictic_calculation_uses_only_focus_inside_the_versioned_v2_scope() -> None:
    snapshot = _snapshot()
    session = _session(snapshot, ("VT.217.N.04", "VT.214.N.04"))
    session.product_focus = ProductFocusState(sku="VT.214.N.04", category="valves")

    request, result = _result("Посчитай 2 шт. этого", session=session)

    assert request.product_ref.kind.value == "current_focus"
    assert request.product_ref.canonical_sku == "VT.214.N.04"
    assert result.status == CalculationResultStatus.CALCULATED
    assert result.total == Decimal("996")


def test_deictic_calculation_refuses_focus_outside_the_versioned_v2_scope() -> None:
    snapshot = _snapshot()
    session = _session(snapshot, ("VT.217.N.04", "VT.214.N.04"))
    session.product_focus = ProductFocusState(sku="PIPE-M", category="pipes")

    request, result = _result("Посчитай 2 шт. этого", session=session)

    assert request.product_ref.canonical_sku is None
    assert result.status == CalculationResultStatus.NEED_CLARIFICATION
    assert request.product_ref.reason_code == (
        "deictic_focus_missing_or_outside_customer_visible_v2_scope"
    )


def test_raw_stock_count_is_not_silently_interpreted_as_pieces() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(_product("VT.217.N.04"),),
    )
    request = build_calculation_request(
        _outcome(), _session(snapshot), snapshot, original_utterance="Посчитай 20 шт."
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)

    assert result.status == CalculationResultStatus.CALCULATED
    assert result.stock_assessment == StockAssessment.UNIT_UNCONFIRMED
    assert result.stock_delta is None
    assert "единица складского учёта не подтверждена" in render_calculation_result(result)


def test_unique_partial_sku_is_a_safe_explicit_calculation_scope() -> None:
    request, result = _result("Посчитай 3 шт. VT.217")

    assert request.scope_origin == CalculationScopeOrigin.V2_DELIVERED
    assert request.product_ref.kind.value == "partial_sku"
    assert request.product_ref.canonical_sku == "VT.217.N.04"
    assert result.total == Decimal("1356")
    assert result.outcome_gate_passed is True


def test_calculation_uses_context_bound_five_digit_and_slash_sku_anchors() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(_product("53843"), _product("68/2/8")),
    )

    numeric = build_calculation_request(
        _outcome(),
        _session(snapshot, ("53843",)),
        snapshot,
        original_utterance="Посчитай 2 шт товара 53843",
    )
    slash = build_calculation_request(
        _outcome(),
        _session(snapshot, ("68/2/8",)),
        snapshot,
        original_utterance="Посчитай 2 шт SKU 68/2/8",
    )

    assert numeric is not None
    assert numeric.product_ref.canonical_sku == "53843"
    assert slash is not None
    assert slash.product_ref.canonical_sku == "68/2/8"


def test_metre_calculation_requires_a_confirmed_price_basis() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    request = build_calculation_request(
        outcome,
        _session(snapshot, ("PIPE-UNKNOWN",)),
        snapshot,
        original_utterance="Посчитай 15 м",
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)

    assert result.status == CalculationResultStatus.NOT_CALCULABLE
    assert result.outcome_gate_passed is True
    assert result.total is None
    assert "цена указана за метр" in render_calculation_result(result)


def test_metre_calculation_uses_only_explicit_price_unit_fact() -> None:
    snapshot = _snapshot()
    request = build_calculation_request(
        _outcome(),
        _session(snapshot, ("PIPE-M",)),
        snapshot,
        original_utterance="Посчитай 15 метров",
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)

    assert result.status == CalculationResultStatus.CALCULATED
    assert result.price_basis_unit == CalculationUnit.METRE
    assert result.total == Decimal("1710")
    assert result.outcome_gate_passed is True


def test_stale_snapshot_is_rejected_and_cannot_be_delivered() -> None:
    snapshot = _snapshot()
    session = _session(snapshot)
    session.v2_source_revision = "old-source"
    request = build_calculation_request(
        _outcome(), session, snapshot, original_utterance="Посчитай 20 шт."
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)

    assert result.status == CalculationResultStatus.REJECTED
    assert result.outcome_gate_passed is False
    assert "calculation_source_revision_stale" in result.reason_codes


def test_calculation_candidate_preserves_selection_scope_and_does_not_reissue_cards() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    before = _session(snapshot)
    request = build_calculation_request(
        outcome, before, snapshot, original_utterance="Посчитай 20 шт."
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)
    candidate = build_v2_calculation_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id=before.session_id,
        turn_id="calculate-turn",
    )

    assert candidate is not None
    assert candidate.eligible_for_delivery is True
    assert candidate.response is not None
    assert candidate.response.products == []
    assert candidate.state_after.answer_plan_summary is not None
    assert candidate.state_after.answer_plan_summary.selection_id == "selection-v1"


def test_committed_calculation_keeps_customer_visible_cards_and_selection_id(tmp_path) -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    before = _session(snapshot)
    request = build_calculation_request(
        outcome, before, snapshot, original_utterance="Посчитай 20 шт."
    )
    assert request is not None
    result = validate_calculation_result(request, build_calculation_result(request, snapshot), snapshot)
    candidate = build_v2_calculation_candidate(
        outcome, _base_candidate(outcome), result, snapshot,
        session_id=before.session_id, turn_id="calculate-turn",
    )
    assert candidate is not None
    products = [
        Product(
            sku=item.sku, name=item.name, category_path="Краны", price=item.price,
            currency=item.currency or "RUB", stock_status=item.stock_status or "",
            stock_qty=item.stock_qty, url=item.url, image_url=item.image_url,
        )
        for item in snapshot.products
    ]
    settings = get_settings().model_copy(update={
        "llm_provider": "disabled", "diagnostic_telemetry_enabled": True,
        "diagnostic_trace_path": tmp_path / "calculation-commit.jsonl",
    })
    bot = ChatOrchestrator(settings=settings, products=products)
    bot.answer_source_snapshot_v2 = snapshot
    response, commit = bot._commit_v2_response(
        before, "Посчитай 20 шт.", "calculation-client-turn", "calculate-turn",
        CutoverDecision(
            owner_candidate=ResponseOwner.V2,
            execution_mode=ExecutionMode.V2_PRIMARY,
            eligible=True,
            catalog_revision=snapshot.source_revision,
        ),
        candidate,
    )
    stored = bot.sessions.snapshot(before.session_id)

    assert commit.committed is True
    assert response.products == []
    assert [item.sku for item in stored.v2_last_products] == ["VT.217.N.04"]
    assert stored.v2_selection_id == "selection-v1"


def test_calculate_wins_only_over_same_turn_price_fact() -> None:
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="calculate", act=TaskAct.CALCULATE,
                target_goal_id="goal", priority=0, source="semantic", source_turn=2,
            ),
            CustomerTask(
                task_id="price", act=TaskAct.CHECK_PRICE,
                target_goal_id="goal", priority=1, source="semantic", source_turn=2,
            ),
        ),
    )
    assert SellerPolicy().decide(state).primary.kind == NextActionKind.CALCULATE_PRELIMINARY


def test_hydraulic_system_question_never_enters_catalogue_price_calculate() -> None:
    """Port the existing Legacy safety boundary through the V2 candidate seam."""

    message = "Рассчитайте гидравлическое сопротивление двухтрубной системы для дома 250 м²"
    snapshot = _snapshot()
    outcome = _outcome()

    assert hydraulic_system_calculation_evidence(message) == "гидравлическое сопротивление"
    candidate = build_v2_hydraulic_system_boundary_candidate(
        message,
        outcome,
        _base_candidate(outcome),
        snapshot,
        session_id="hydraulic-boundary",
        turn_id="hydraulic-boundary-turn",
    )

    assert candidate is not None
    assert candidate.eligible_for_delivery is True
    assert candidate.calculation_result is None
    assert candidate.engineering_boundary_result is not None
    assert candidate.engineering_boundary_result.topic == "hydraulic_system_calculation"
    assert candidate.response is not None
    assert candidate.response.products == []
    assert "гидравлическое сопротивление" in candidate.response.answer.lower()
    assert "цен" not in candidate.response.answer.lower()
    assert candidate.product_scope_effect.value == "preserve"


def test_product_price_calculation_is_not_mistaken_for_hydraulic_system_design() -> None:
    assert hydraulic_system_calculation_evidence("Посчитай 2 штуки второго") is None


def test_source_snapshot_exposes_only_explicit_price_per_metre_fact() -> None:
    product = Product(
        sku="PIPE-M", name="Труба", category_path="Трубы", price=114,
        url="https://example.test/pipe", description="В карточке приведена цена одного погонного метра.",
    )
    catalog = (
        CatalogProductSnapshot(
            sku="PIPE-M", name="Труба", category="pipes", product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
        ),
    )
    snapshot = build_answer_source_snapshot([product], catalog)
    source_product = snapshot.product("PIPE-M")

    assert source_product is not None
    assert [(fact.name, fact.value) for fact in source_product.facts] == [("price_unit", "m")]
