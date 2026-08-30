"""V2 regression gate for historical dialogue-continuity failures.

The legacy suite contains valuable buyer scenarios, but its assertions are
coupled to the old orchestrator's mutable slots and response wording. This
module carries the same customer invariants through protected V2 delivery.
"""

from __future__ import annotations

import json

from app.agents.orchestrator import ChatOrchestrator
from app.agents.semantic_interpreter import SemanticInterpretationResult, TurnUnderstanding
from app.config import get_settings
from app.dialogue_v2.reactivation import resolve_goal_reactivation
from app.models import DialogueQAMode, Product


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    price: float,
    attributes: dict[str, str],
    stock_status: str = "в наличии",
    stock_qty: int = 5,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        price=price,
        currency="RUB",
        stock_status=stock_status,
        stock_qty=stock_qty,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        attributes_normalized=attributes,
    )


def _frame(
    *,
    operation: str,
    acts: list[str],
    products: list[dict[str, object]] | None = None,
    constraints: list[dict[str, object]] | None = None,
    show: str | None = None,
) -> TurnUnderstanding:
    payload: dict[str, object] = {
        "schema_version": "1.3",
        "language": "ru",
        "operation": operation,
        "acts": acts,
        "products": products or [],
        "constraints": constraints or [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.99,
    }
    if show is not None:
        payload["selection_controls"] = [
            {"kind": "continue_with_confirmed_facts", "evidence": show}
        ]
        payload["selection_strategy"] = {
            "kind": "continue_with_confirmed_facts",
            "evidence": show,
        }
    return TurnUnderstanding.model_validate(payload)


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


def _semantic(
    understanding: TurnUnderstanding,
    *,
    goal_reactivation=None,
) -> SemanticInterpretationResult:
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        model="test/semantic",
        latency_ms=0,
        understanding=understanding,
        goal_reactivation=goal_reactivation,
    )


def _preview_settings(tmp_path):
    return get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "embeddings_enabled": False,
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "v2-continuity.jsonl",
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


def test_return_to_pump_uses_its_scope_after_valve_selection_for_second_price(
    tmp_path,
    monkeypatch,
) -> None:
    """A return binds the second option to pumps, not the later valve list."""

    pumps = [
        _product(
            "PUMP-ONE",
            "Насос циркуляционный 25/4-180",
            "Насосное оборудование",
            price=4_000,
            attributes={
                "Тип товара": "Насос циркуляционный",
                "Монтажная длина, мм": "180",
            },
        ),
        _product(
            "PUMP-TWO",
            "Насос циркуляционный 25/6-180",
            "Насосное оборудование",
            price=5_000,
            attributes={
                "Тип товара": "Насос циркуляционный",
                "Монтажная длина, мм": "180",
            },
        ),
    ]
    valves = [
        _product(
            "VALVE-ONE",
            "Кран шаровой BASE 1/2 вн-вн, бабочка",
            "Водозапорная арматура",
            price=452,
            attributes={
                "Тип товара": "Кран шаровой",
                "Диаметр подключения, дюйм": "1/2",
                "Тип резьбы": "С внутренней резьбой (ff)",
            },
        ),
        _product(
            "VALVE-TWO",
            "Кран шаровой BASE 1/2 вн-вн, рычаг",
            "Водозапорная арматура",
            price=503,
            attributes={
                "Тип товара": "Кран шаровой",
                "Диаметр подключения, дюйм": "1/2",
                "Тип резьбы": "С внутренней резьбой (ff)",
            },
        ),
    ]
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[*pumps, *valves])

    pump_opening = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        constraints=[
            _known("duty_point_flow_l_h", 1.5, "расход 1,5 м3/ч", unit="m3/h"),
            _known("duty_point_head_m", 4, "напор 4 м", unit="m"),
        ],
    )
    pump_show = _frame(
        operation="continue",
        acts=["find"],
        show="Покажите насосы",
    )
    valve_show = _frame(
        operation="new",
        acts=["find"],
        products=[
            {
                "text": "краны BASE",
                "canonical_type": "ball valve",
                "category": "valves",
                "role": "target",
                "evidence": "краны BASE",
            }
        ],
        constraints=[
            _known("connection_size", "1/2", "1/2"),
            _known("connection_pattern", "female_female", "вн-вн"),
        ],
        show="Теперь ещё нужны краны BASE 1/2 вн-вн",
    )
    price_second = _frame(operation="continue", acts=["check_price"])
    semantic_frames = iter((pump_opening, pump_show, valve_show, price_second))

    def interpret(message: str, before):
        return _semantic(
            next(semantic_frames),
            goal_reactivation=resolve_goal_reactivation(
                message,
                before.live_dialogue_state_v2 or before.dialogue_state_v2,
            ),
        )

    monkeypatch.setattr(bot.semantic_interpreter, "interpret", interpret)
    session_id = "v2-history-topic-switch"
    bot.handle_chat(
        session_id,
        "Нужен циркуляционный насос: расход 1,5 м3/ч, напор 4 м.",
        client_turn_id="v2-history-1",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    pump_cards = bot.handle_chat(
        session_id,
        "Покажите насосы",
        client_turn_id="v2-history-2",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    valve_cards = bot.handle_chat(
        session_id,
        "Теперь ещё нужны краны BASE 1/2 вн-вн",
        client_turn_id="v2-history-3",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    price = bot.handle_chat(
        session_id,
        "Вернёмся к насосу: сколько стоит второй вариант?",
        client_turn_id="v2-history-4",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )

    assert [item.sku for item in pump_cards.products] == [item.sku for item in pumps]
    assert [item.sku for item in valve_cards.products] == [item.sku for item in valves]
    assert [item.sku for item in price.products] == ["PUMP-TWO"]
    assert "5000" in price.answer

    stored = bot.sessions.snapshot(session_id)
    state = stored.live_dialogue_state_v2
    assert state is not None
    assert state.active_goal_id is not None
    active_goal = next(item for item in state.product_goals if item.goal_id == state.active_goal_id)
    assert active_goal.category.value == "pumps"
    assert active_goal.canonical_type.replace(" ", "_") == "circulation_pump"
    scopes = {item.goal_id: item.ordered_skus for item in state.delivered_selection_scopes}
    assert tuple(item.sku for item in pumps) in scopes.values()
    assert tuple(item.sku for item in valves) in scopes.values()

    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 4
    assert all(
        trace["cutover_v2"]["decision"]["owner_candidate"] == "v2"
        for trace in traces
    )
    assert traces[-1]["cutover_v2"]["offer_fact_delivery"]["sku"] == "PUMP-TWO"
    assert traces[-1]["cutover_v2"]["offer_fact_delivery"]["value"] == 5_000


def test_catalog_bound_numeric_and_slash_sku_keep_v2_ownership_and_report_stock(
    tmp_path,
    monkeypatch,
) -> None:
    """An exact SKU beats surface number syntax and never means ``in stock only``.

    The full-feed sweep found that ``11677`` and slash-shaped articles could
    be resolved by the catalogue but rejected earlier by semantics.  This
    gate goes through protected Preview instead: resolution is accepted only
    when the checked offer fact is actually delivered by V2.
    """

    products = [
        _product(
            "11677",
            "Кран шаровой с числовым артикулом",
            "Водозапорная арматура",
            price=1_167,
            stock_status="нет в наличии",
            stock_qty=0,
            attributes={"Тип товара": "Кран шаровой"},
        ),
        _product(
            "68/2/8",
            "Кран шаровой со slash-артикулом",
            "Водозапорная арматура",
            price=6_828,
            attributes={"Тип товара": "Кран шаровой"},
        ),
    ]
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=products)

    stock_turn = _frame(
        operation="new",
        acts=["check_stock"],
        products=[
            {
                "text": "11677",
                "canonical_type": "ball valve",
                "category": "valves",
                "role": "target",
                "evidence": "11677",
            }
        ],
        constraints=[_known("sku", "11677", "11677")],
    )
    price_turn = _frame(
        operation="new",
        acts=["check_price"],
        products=[
            {
                "text": "68/2/8",
                "canonical_type": "ball valve",
                "category": "valves",
                "role": "target",
                "evidence": "68/2/8",
            }
        ],
        constraints=[_known("sku", "68/2/8", "68/2/8")],
    )
    frames = iter((stock_turn, price_turn))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    out_of_stock = bot.handle_chat(
        "v2-numeric-sku",
        "11677 есть в наличии?",
        client_turn_id="v2-numeric-sku-1",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )
    slash_price = bot.handle_chat(
        "v2-slash-sku",
        "Сколько стоит 68/2/8?",
        client_turn_id="v2-slash-sku-1",
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )

    assert [item.sku for item in out_of_stock.products] == ["11677"]
    assert "нет в наличии" in out_of_stock.answer.lower()
    assert [item.sku for item in slash_price.products] == ["68/2/8"]
    assert "6828" in slash_price.answer

    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [
        trace["cutover_v2"]["decision"]["owner_candidate"]
        for trace in traces
    ] == ["v2", "v2"]
    assert traces[0]["cutover_v2"]["offer_fact_delivery"]["sku"] == "11677"
    assert traces[0]["cutover_v2"]["offer_fact_delivery"]["value"] == "нет в наличии"
    assert traces[1]["cutover_v2"]["offer_fact_delivery"]["sku"] == "68/2/8"
    assert traces[1]["cutover_v2"]["offer_fact_delivery"]["value"] == 6_828
