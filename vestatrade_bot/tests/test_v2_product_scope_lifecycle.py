from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.catalog_v2.contracts import ProductKind
from app.compatibility_v2.contracts import CompatibilityScopeOrigin
from app.compatibility_v2.service import build_compatibility_request
from app.config import get_settings
from app.cutover_v2.contracts import (
    CutoverDecision,
    ExecutionMode,
    ResponseOwner,
    V2TurnCandidate,
)
from app.cutover_v2.product_fact import build_v2_product_fact_candidate
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
from app.models import Product, ProductCard, SessionState
from app.product_fact_evidence import (
    ProductFactEvidence,
    ProductFactRequest,
    ProductFactStatus,
    ProductReference,
    ProductReferenceKind,
)


def _pump(sku: str, model: str, mounting_length: int) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {model}",
        category_path="Насосное оборудование",
        price=10_000 + mounting_length,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        url=f"https://example.test/{sku}",
        attributes_normalized={
            "Тип товара": "Насос циркуляционный",
            "Монтажная длина, мм": str(mounting_length),
        },
    )


def _card(product: Product) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        price=product.price or 0,
        currency=product.currency,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
        image_url=product.image_url,
    )


def _selection_state(selection_id: str, source_revision: str) -> DialogueStateV2:
    return DialogueStateV2(
        turn_number=2,
        answer_plan_summary=AnswerPlanSummary(
            plan_id="selection-plan",
            semantic_signature="selection-signature",
            task_ids=(),
            primary_action=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            next_step_kind="show_preliminary_options",
            validation_status="accepted",
            delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
            selection_id=selection_id,
            catalog_revision=source_revision,
            source_turn=2,
        ),
    )


def _product_fact_candidate(
    bot: ChatOrchestrator,
    state: DialogueStateV2,
    first: Product,
    *,
    session_id: str = "scope-lifecycle",
    turn_id: str = "fact-turn",
) -> V2TurnCandidate:
    snapshot = bot.answer_source_snapshot_v2
    outcome = DialogueV2Outcome(
        status="applied",
        state_before=state,
        state_after=state,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.ANSWER_DIRECT_QUESTION,
                reason_code="explicit_product_fact",
            )
        ),
    )
    base = V2TurnCandidate(
        turn_id=turn_id,
        state_before=state,
        state_after=state,
        source_revision=snapshot.source_revision,
        catalog_revision=snapshot.source_revision,
        validation_status="accepted",
        task_acts=(TaskAct.EXPLAIN,),
        product_kinds=(ProductKind.CIRCULATION_PUMP,),
        contract_versions=("1.0",),
        semantic_accepted=True,
        contracts_resolved=True,
    )
    evidence = ProductFactEvidence(
        status=ProductFactStatus.ANSWERED,
        request=ProductFactRequest(
            question="Какая у первого монтажная длина?",
            predicate="installation_length_mm",
            product_ref=ProductReference(
                kind=ProductReferenceKind.ORDINAL,
                raw="1",
                canonical_sku=first.sku,
                candidate_skus=(first.sku,),
                reason_code="ordinal_in_customer_visible_cards",
            ),
        ),
        product_name=first.name,
        value=180,
        unit="мм",
        source_kind="catalog_card",
        verifier_status="catalog_snapshot_exact",
        reason_code="catalog_fact_exact_match",
    )
    candidate = build_v2_product_fact_candidate(
        outcome,
        base,
        evidence,
        snapshot,
        session_id=session_id,
        turn_id=turn_id,
    )
    assert candidate is not None
    return candidate


def _compatibility_outcome() -> DialogueV2Outcome:
    task = CustomerTask(
        task_id="compatibility-task",
        act=TaskAct.COMPATIBILITY,
        target_goal_id="compatibility-goal",
        priority=0,
        source="semantic_interpreter",
        source_turn=3,
    )
    state = DialogueStateV2(turn_number=3, tasks=(task,))
    return DialogueV2Outcome(
        status="applied",
        state_before=state.model_copy(update={"turn_number": 2}),
        state_after=state,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.CHECK_COMPATIBILITY,
                task_id=task.task_id,
                reason_code="explicit_compatibility_request",
            ),
            task_ids=(task.task_id,),
        ),
    )


def test_product_fact_card_preserves_multi_card_selection_for_later_ordinals(
    tmp_path,
) -> None:
    products = [
        _pump("VRS.254.18.0", "VALTEC RS 25/4-180", 180),
        _pump("VRS.256.18.0", "VALTEC RS 25/6-180", 180),
        _pump("VRS.258.18.0", "VALTEC RS 25/8-180", 180),
        _pump("VRS.2510.18.0", "VALTEC RS 25/10-180", 180),
        _pump("VRS.2512.18.0", "VALTEC RS 25/12-180", 180),
    ]
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "scope-lifecycle.jsonl",
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "scope-lifecycle-secret",
        }
    )
    bot = ChatOrchestrator(settings=settings, products=products)
    snapshot = bot.answer_source_snapshot_v2
    selection_id = "selection-three-pumps"
    state = _selection_state(selection_id, snapshot.source_revision)
    cards = [_card(product) for product in products]
    before = SessionState(
        session_id="scope-lifecycle",
        last_products=list(cards),
        v2_last_products=list(cards),
        shown_product_skus=[product.sku for product in products],
        shown_result_signature=selection_id,
        v2_selection_id=selection_id,
        v2_source_revision=snapshot.source_revision,
        live_dialogue_state_v2=state,
    )
    bot.sessions.save(before)
    unfocused_request = build_compatibility_request(
        _compatibility_outcome(),
        before,
        snapshot,
        original_utterance="Подойдёт ли этот ко второму?",
    )
    assert unfocused_request is not None
    assert unfocused_request.left.canonical_sku == products[1].sku
    assert unfocused_request.right.canonical_sku is None
    candidate = _product_fact_candidate(bot, state, products[0])

    response, commit = bot._commit_v2_response(
        before,
        "Какая у первого монтажная длина?",
        "scope-lifecycle-client-fact",
        "fact-turn",
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
    assert [item.sku for item in response.products] == [products[0].sku]
    assert [item.sku for item in stored.v2_last_products] == [
        product.sku for product in products
    ]
    assert [item.sku for item in stored.last_products] == [
        product.sku for product in products
    ]
    assert stored.shown_product_skus == [product.sku for product in products]
    assert stored.shown_result_signature == selection_id
    assert stored.v2_selection_id == selection_id
    assert stored.v2_source_revision == snapshot.source_revision
    assert stored.product_focus is not None
    assert stored.product_focus.sku == products[0].sku
    assert stored.live_dialogue_state_v2 is not None
    assert stored.live_dialogue_state_v2.answer_plan_summary is not None
    assert (
        stored.live_dialogue_state_v2.answer_plan_summary.selection_id
        == selection_id
    )
    assert (
        stored.live_dialogue_state_v2.answer_plan_summary.catalog_revision
        == snapshot.source_revision
    )
    assert "product_scope_preserve" in commit.reason_codes

    for question, expected_indices in (
        ("Подойдёт ли первый ко второму?", (0, 1)),
        ("Подойдёт ли первый к третьему?", (0, 2)),
        ("Подойдёт ли первый к четвёртому?", (0, 3)),
        ("Подойдёт ли первый к пятому?", (0, 4)),
    ):
        request = build_compatibility_request(
            _compatibility_outcome(),
            stored,
            snapshot,
            original_utterance=question,
        )

        assert request is not None
        assert request.scope_origin == CompatibilityScopeOrigin.V2_DELIVERED
        assert request.left.canonical_sku == products[expected_indices[0]].sku
        assert request.right.canonical_sku == products[expected_indices[1]].sku
        assert request.selection_id == selection_id
        assert request.ordered_skus == tuple(product.sku for product in products)

    focused_request = build_compatibility_request(
        _compatibility_outcome(),
        stored,
        snapshot,
        original_utterance="Подойдёт ли этот к третьему?",
    )

    assert focused_request is not None
    assert focused_request.scope_origin == CompatibilityScopeOrigin.V2_DELIVERED
    assert {
        focused_request.left.canonical_sku,
        focused_request.right.canonical_sku,
    } == {products[0].sku, products[2].sku}

    duplicate_focus_request = build_compatibility_request(
        _compatibility_outcome(),
        stored,
        snapshot,
        original_utterance="Подойдёт ли этот к первому?",
    )

    assert duplicate_focus_request is not None
    assert duplicate_focus_request.left.canonical_sku == products[0].sku
    assert duplicate_focus_request.right.canonical_sku is None


def test_standalone_product_fact_sets_focus_without_phantom_v2_selection(
    tmp_path,
) -> None:
    product = _pump("VRS.254.18.0", "VALTEC RS 25/4-180", 180)
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "standalone-fact.jsonl",
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "scope-lifecycle-secret",
        }
    )
    bot = ChatOrchestrator(settings=settings, products=[product])
    snapshot = bot.answer_source_snapshot_v2
    state = DialogueStateV2()
    before = SessionState(session_id="standalone-product-fact")
    candidate = _product_fact_candidate(
        bot,
        state,
        product,
        session_id=before.session_id,
        turn_id="standalone-fact-turn",
    )

    response, commit = bot._commit_v2_response(
        before,
        "Какая монтажная длина у VRS.254.18.0?",
        "standalone-fact-client-turn",
        "standalone-fact-turn",
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
    assert [item.sku for item in response.products] == [product.sku]
    assert [item.sku for item in stored.last_products] == [product.sku]
    assert stored.v2_last_products == []
    assert stored.v2_selection_id is None
    assert stored.v2_source_revision is None
    assert stored.shown_product_skus == []
    assert stored.shown_result_signature is None
    assert stored.product_focus is not None
    assert stored.product_focus.sku == product.sku
