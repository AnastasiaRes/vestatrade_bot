"""Characterization for the safe Legacy-to-V2 product-scope bridge.

The bridge is intentionally about customer-visible *structured cards*, not
Legacy prose.  It lets a protected V2 follow-up resolve ``второй`` after a
Legacy-owned selection only when every card still matches the current source
snapshot and the typed state identifies exactly one active selection goal.
"""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.cutover_v2.legacy_scope_bridge import (
    LegacyCapabilityResultStatus,
    LegacyScopeBridgeStatus,
    bridge_validated_legacy_selection_scope,
    validate_legacy_capability_result,
)
from app.cutover_v2.capability_registry import resolve_capability_coverage
from app.cutover_v2.registry import default_registry
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    ProductCategory,
    ProductGoal,
    ProductRole,
    TaskAct,
    TaskStatus,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatProductSummary, ChatResponse, Product, SessionState
from app.offer_fact_v2.service import build_offer_fact_request, build_offer_fact_result
from app.v2_visible_products import turn_product_context


def _product(sku: str, price: float) -> Product:
    return Product(
        sku=sku,
        name=f"Насос {sku}",
        category_path="Насосное оборудование",
        price=price,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=3,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        attributes_normalized={"Тип товара": "Насос циркуляционный"},
    )


def _state() -> DialogueStateV2:
    goal = ProductGoal(
        goal_id="pump-goal",
        canonical_type="circulation_pump",
        category=ProductCategory.PUMPS,
        role=ProductRole.TARGET,
        evidence="насосы",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    task = CustomerTask(
        task_id="pump-selection-task",
        target_goal_id=goal.goal_id,
        act=TaskAct.SELECT,
        priority=0,
        status=TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
        created_turn=1,
        last_addressed_turn=1,
    )
    return DialogueStateV2(
        turn_number=2,
        active_goal_id=goal.goal_id,
        product_goals=(goal,),
        tasks=(task,),
    )


def _legacy_response(bot: ChatOrchestrator, skus: tuple[str, ...]) -> ChatResponse:
    snapshot = bot.answer_source_snapshot_v2
    assert snapshot is not None
    cards: list[ChatProductSummary] = []
    for sku in skus:
        product = snapshot.product(sku)
        assert product is not None
        cards.append(
            ChatProductSummary(
                sku=product.sku,
                name=product.name,
                price=product.price or 0,
                currency=product.currency,
                stock_status=product.stock_status,
                url=product.url,
                image_url=product.image_url,
            )
        )
    return ChatResponse(
        session_id="legacy-bridge",
        answer="Legacy показал варианты.",
        products=cards,
    )


def _bot(tmp_path) -> ChatOrchestrator:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "legacy-scope-bridge.jsonl",
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "legacy-scope-bridge-secret",
        }
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=[_product("PUMP-ONE", 4_000), _product("PUMP-TWO", 5_000)],
    )
    bot._ensure_products_loaded()
    return bot


def test_validated_legacy_cards_create_goal_scoped_context_for_v2_offer_fact(
    tmp_path,
) -> None:
    bot = _bot(tmp_path)
    snapshot = bot.answer_source_snapshot_v2
    assert snapshot is not None

    bridged = bridge_validated_legacy_selection_scope(
        _state(),
        _legacy_response(bot, ("PUMP-ONE", "PUMP-TWO")),
        snapshot,
        session_id="legacy-bridge",
        turn_id="legacy-selection-turn",
    )

    assert bridged.audit.status == LegacyScopeBridgeStatus.IMPORTED
    assert bridged.audit.goal_id == "pump-goal"
    assert bridged.audit.task_id == "pump-selection-task"
    assert bridged.audit.ordered_skus == ("PUMP-ONE", "PUMP-TWO")
    assert bridged.state_after != _state()
    scope = bridged.state_after.delivered_selection_scopes[-1]
    assert scope.delivery_owner == "legacy_validated"
    assert scope.ordered_skus == ("PUMP-ONE", "PUMP-TWO")

    context = turn_product_context(
        bridged.state_after,
        source_revision=snapshot.source_revision,
    )
    assert context.is_valid
    assert context.scope.ordinal(1).canonical_sku == "PUMP-TWO"

    capability_session = bot._session_for_v2_turn_product_context(
        SessionState(session_id="legacy-bridge"),
        bridged.state_after,
    )
    request = build_offer_fact_request(
        DialogueV2Outcome(
            status="applied",
            state_before=bridged.state_after,
            state_after=bridged.state_after,
        ),
        capability_session,
        snapshot,
        original_utterance="Сколько стоит второй вариант?",
    )
    assert request is not None
    assert request.product_ref.canonical_sku == "PUMP-TWO"
    result = build_offer_fact_result(request, snapshot)
    assert result.sku == "PUMP-TWO"
    assert result.value == 5_000


def test_mismatching_legacy_card_is_rejected_without_phantom_scope(tmp_path) -> None:
    bot = _bot(tmp_path)
    snapshot = bot.answer_source_snapshot_v2
    assert snapshot is not None
    response = _legacy_response(bot, ("PUMP-ONE",))
    response.products[0].price = 1

    bridged = bridge_validated_legacy_selection_scope(
        _state(),
        response,
        snapshot,
        session_id="legacy-bridge",
        turn_id="tampered-selection-turn",
    )

    assert bridged.audit.status == LegacyScopeBridgeStatus.REJECTED
    assert "legacy_scope_card_price_mismatch" in bridged.audit.reason_codes
    assert bridged.state_after == _state()
    assert bridged.state_after.delivered_selection_scopes == ()


def test_legacy_direct_offer_card_cannot_be_promoted_to_selection_scope(tmp_path) -> None:
    bot = _bot(tmp_path)
    snapshot = bot.answer_source_snapshot_v2
    assert snapshot is not None
    state = _state().model_copy(
        update={
            "tasks": (
                _state().tasks[0].model_copy(
                    update={"act": TaskAct.CHECK_PRICE}
                ),
            )
        }
    )

    bridged = bridge_validated_legacy_selection_scope(
        state,
        _legacy_response(bot, ("PUMP-ONE",)),
        snapshot,
        session_id="legacy-bridge",
        turn_id="legacy-price-turn",
    )

    assert bridged.audit.status == LegacyScopeBridgeStatus.NOT_APPLICABLE
    assert "legacy_scope_no_active_selection_task" in bridged.audit.reason_codes
    assert bridged.state_after == state


def test_allowlisted_legacy_item_list_cards_pass_source_gate(tmp_path) -> None:
    bot = _bot(tmp_path)
    coverage = resolve_capability_coverage(
        "Насос PUMP-ONE — 2 шт, насос PUMP-TWO — 3 шт",
        None,
        default_registry(),
    )

    audit = validate_legacy_capability_result(
        coverage,
        _legacy_response(bot, ("PUMP-ONE", "PUMP-TWO")),
        bot.answer_source_snapshot_v2,
    )

    assert audit.status == LegacyCapabilityResultStatus.ACCEPTED
    assert audit.ordered_skus == ("PUMP-ONE", "PUMP-TWO")


def test_allowlisted_legacy_item_list_duplicate_cards_fail_closed(tmp_path) -> None:
    bot = _bot(tmp_path)
    coverage = resolve_capability_coverage(
        "Насос PUMP-ONE — 2 шт, насос PUMP-TWO — 3 шт",
        None,
        default_registry(),
    )

    audit = validate_legacy_capability_result(
        coverage,
        _legacy_response(bot, ("PUMP-ONE", "PUMP-ONE")),
        bot.answer_source_snapshot_v2,
    )

    assert audit.status == LegacyCapabilityResultStatus.REJECTED
    assert audit.reason_codes == ("legacy_scope_duplicate_public_sku",)
