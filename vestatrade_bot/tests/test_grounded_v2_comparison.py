from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.contracts import CatalogFact, CatalogProductRole, FactProvenance, ProductKind
from app.comparison_v2.contracts import ComparisonResultStatus
from app.comparison_v2.renderer import render_comparison_result
from app.comparison_v2.service import (
    build_comparison_request,
    build_comparison_result,
    validate_comparison_result,
)
from app.cutover_v2.comparison import build_v2_comparison_candidate
from app.cutover_v2.contracts import (
    CutoverDecision,
    ExecutionMode,
    ResponseOwner,
    V2TurnCandidate,
)
from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    AnswerPlanSummary,
    NextAction,
    NextActionKind,
    NextActionPlan,
    PresentedCandidateSummary,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.dialogue_v2.seller_policy import SellerPolicy
from app.models import ProductCard, SessionState
from app.models import Product


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


def _product(sku: str, *, price: float, length: int, stock: str = "в наличии") -> CatalogAnswerProduct:
    return CatalogAnswerProduct(
        sku=sku,
        name=f"Насос {sku}",
        product_kind=ProductKind.CIRCULATION_PUMP,
        role=CatalogProductRole.BASE_PRODUCT,
        price=price,
        currency="RUB",
        stock_status=stock,
        stock_qty=10,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        facts=(_fact("installation_length_mm", length, "мм"),),
    )


def _snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product("PUMP-180", price=5000, length=180),
            _product("PUMP-130", price=4500, length=130, stock="под заказ"),
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
        presented_candidates=(
            PresentedCandidateSummary(
                sku="PUMP-180",
                name="Насос PUMP-180",
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                task_id="selection-task",
                goal_id="goal-pump",
                search_plan_id="search-v1",
                source_turn=1,
            ),
            PresentedCandidateSummary(
                sku="PUMP-130",
                name="Насос PUMP-130",
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                task_id="selection-task",
                goal_id="goal-pump",
                search_plan_id="search-v1",
                source_turn=1,
            ),
        ),
        source_turn=1,
    )
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="compare-task",
                act=TaskAct.COMPARE,
                target_goal_id="goal-pump",
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
                kind=NextActionKind.COMPARE,
                task_id="compare-task",
                reason_code="comparison_requested",
            ),
            task_ids=("compare-task",),
        ),
    )


def _session(snapshot: AnswerSourceSnapshot) -> SessionState:
    return SessionState(
        session_id="compare",
        v2_last_products=[_card(item) for item in snapshot.products],
        last_products=[_card(item) for item in snapshot.products],
        v2_selection_id="selection-v1",
        v2_source_revision=snapshot.source_revision,
    )


def _base_candidate(outcome: DialogueV2Outcome) -> V2TurnCandidate:
    return V2TurnCandidate(
        turn_id="compare-turn",
        state_before=outcome.state_before,
        state_after=outcome.state_after,
        validation_status="not_run",
    )


def test_compare_reads_only_visible_scope_and_proves_price_and_length() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)

    request = build_comparison_request(
        outcome, session, original_utterance="Сравните их по монтажной длине"
    )
    assert request is not None
    assert request.ordered_skus == ("PUMP-180", "PUMP-130")
    assert request.selection_id == "selection-v1"
    assert request.requested_predicates == ("installation_length_mm",)

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert {item.predicate for item in result.dimensions} >= {
        "price",
        "availability",
        "installation_length_mm",
    }
    length = next(item for item in result.dimensions if item.predicate == "installation_length_mm")
    assert [item.value for item in length.values] == [180, 130]
    assert all(item.source_ref_ids for item in length.values)
    assert all(item.sku in request.ordered_skus for item in result.sources)


def test_cheapest_result_is_a_proved_price_conclusion_not_free_recommendation() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(
        outcome, session, original_utterance="Какой из показанных дешевле?"
    )
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.outcome_gate_passed is True
    assert result.recommendation is not None
    assert result.recommendation.sku == "PUMP-130"
    assert result.recommendation.reason_code == "lowest_confirmed_price"
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )
    assert "дешевле Насос PUMP-130 (PUMP-130) — 4500 ₽" in rendered
    assert "None" not in rendered
    assert "Какой критерий" not in rendered


def test_generic_compare_hides_identity_dimension_and_uses_human_criteria() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )

    assert result.outcome_gate_passed is True
    assert "• Sku:" not in rendered
    assert "None" not in rendered
    assert "Какой критерий для вас решающий: цена, наличие, монтажная длина?" in rendered


def test_single_visible_card_gets_one_subject_clarification() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    session.v2_last_products = session.v2_last_products[:1]
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("comparison_requires_two_visible_cards",)


def test_no_visible_card_returns_v2_clarification_without_global_catalogue_scope() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = SessionState(session_id="empty")
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=()),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id="empty",
        turn_id="compare-empty",
    )

    assert result.status == ComparisonResultStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True
    assert candidate is not None
    assert candidate.response is not None
    assert candidate.response.products == []
    assert "минимум две" in candidate.response.answer


def test_explicit_compare_beats_a_bare_explain_candidate_from_the_same_turn() -> None:
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="compare-task",
                act=TaskAct.COMPARE,
                target_goal_id="goal-pump",
                priority=0,
                source="semantic_interpreter",
                source_turn=2,
            ),
            CustomerTask(
                task_id="bare-explain",
                act=TaskAct.EXPLAIN,
                target_goal_id="goal-pump",
                priority=1,
                source="semantic_interpreter",
                source_turn=2,
            ),
            CustomerTask(
                task_id="selection-task",
                act=TaskAct.SELECT,
                target_goal_id="goal-pump",
                priority=2,
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
    )

    plan = SellerPolicy().decide(state)

    assert plan.primary.kind == NextActionKind.COMPARE
    assert plan.primary.task_id == "compare-task"


def test_stale_or_unversioned_scope_is_rejected_before_comparison() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    session.v2_source_revision = "old-source"
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    stale = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    assert stale.status == ComparisonResultStatus.REJECTED
    assert stale.outcome_gate_passed is False
    assert "comparison_source_revision_stale" in stale.reason_codes

    legacy = SessionState(session_id="legacy", last_products=session.last_products)
    legacy_request = build_comparison_request(outcome, legacy, original_utterance="Сравните их")
    assert legacy_request is not None
    legacy_result = validate_comparison_result(
        legacy_request,
        build_comparison_result(legacy_request, snapshot, visible_cards=legacy.last_products),
        snapshot,
    )
    assert legacy_result.status == ComparisonResultStatus.REJECTED
    assert "comparison_scope_not_v2_versioned" in legacy_result.reason_codes


def test_comparison_gate_rejects_a_value_or_winner_that_does_not_match_snapshot() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Какой из показанных дешевле?")
    assert request is not None
    accepted = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    price = next(item for item in accepted.dimensions if item.predicate == "price")
    changed_price = price.model_copy(
        update={"values": (price.values[0].model_copy(update={"value": 1}), *price.values[1:])}
    )
    tampered = accepted.model_copy(update={"dimensions": (changed_price, *accepted.dimensions[1:])})
    rejected = validate_comparison_result(request, tampered, snapshot)

    assert rejected.outcome_gate_passed is False
    assert "comparison_value_does_not_match_source_snapshot" in rejected.reason_codes


def test_comparison_candidate_preserves_selection_scope_and_never_reissues_cards() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id="compare",
        turn_id="compare-turn",
    )

    assert candidate is not None
    assert candidate.eligible_for_delivery is True
    assert candidate.response is not None
    assert candidate.response.products == []
    assert "Сравнение показанных вариантов" in candidate.response.answer
    assert candidate.state_after.answer_plan_summary is not None
    assert candidate.state_after.answer_plan_summary.selection_id == "selection-v1"
    assert [item.sku for item in candidate.state_after.answer_plan_summary.presented_candidates] == [
        "PUMP-180",
        "PUMP-130",
    ]


def test_committed_comparison_keeps_the_visible_order_and_selection_identity(tmp_path) -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    before = _session(snapshot)
    request = build_comparison_request(outcome, before, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=before.v2_last_products),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id=before.session_id,
        turn_id="compare-turn",
    )
    assert candidate is not None
    products = [
        Product(
            sku=item.sku,
            name=item.name,
            category_path="Насосы",
            price=item.price,
            currency=item.currency or "RUB",
            stock_status=item.stock_status or "",
            stock_qty=item.stock_qty,
            url=item.url,
            image_url=item.image_url,
        )
        for item in snapshot.products
    ]
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "comparison-commit.jsonl",
        }
    )
    bot = ChatOrchestrator(settings=settings, products=products)
    bot.answer_source_snapshot_v2 = snapshot
    decision = CutoverDecision(
        owner_candidate=ResponseOwner.V2,
        execution_mode=ExecutionMode.V2_PRIMARY,
        eligible=True,
        catalog_revision=snapshot.source_revision,
    )

    response, commit = bot._commit_v2_response(
        before,
        "Сравните их",
        "comparison-client-turn",
        "compare-turn",
        decision,
        candidate,
    )
    stored = bot.sessions.snapshot(before.session_id)

    assert commit.committed is True
    assert response.products == []
    assert [item.sku for item in stored.v2_last_products] == ["PUMP-180", "PUMP-130"]
    assert [item.sku for item in stored.last_products] == ["PUMP-180", "PUMP-130"]
    assert stored.v2_selection_id == "selection-v1"
    assert stored.v2_source_revision == "source-v1"
