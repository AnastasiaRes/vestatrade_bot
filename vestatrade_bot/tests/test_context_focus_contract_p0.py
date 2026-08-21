"""P0 contracts for stable product focus and relation-aware follow-ups."""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.models import (
    IntentResult,
    Product,
    ProductBranchState,
    ProductSelectionSnapshot,
)


def _referent_valve(
    sku: str,
    *,
    handle: str,
    price: float,
) -> Product:
    return Product(
        sku=sku,
        name=f'Кран шаровой 1/2" {handle}',
        category_path="Краны шаровые",
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=price,
        stock_status="в наличии",
        stock_qty=10,
        attributes_normalized={
            "артикул": sku,
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "1/2",
            "тип ручки": handle,
        },
    )


def _seed_ordered_valve_displays(bot: ChatOrchestrator, session_id: str) -> None:
    first = ProductSelectionSnapshot(
        category="valves",
        product_skus=["VT.331.N.04", "VT.217.N.04"],
        constraints={
            "application": "вода",
            "size_inch": "1/2",
            "valve_kind": "шаровый кран",
        },
        user_message="Покажи кран 1/2 для воды",
        display_index=0,
    )
    later = ProductSelectionSnapshot(
        category="valves",
        product_skus=["VT.217.N.04"],
        constraints={
            "application": "вода",
            "size_inch": "3/4",
            "valve_kind": "шаровый кран",
            "handle_type": "butterfly",
        },
        user_message="А такой же 3/4 с бабочкой?",
        display_index=1,
    )
    session = bot.sessions.get(session_id)
    session.category = "valves"
    session.slots = dict(later.constraints)
    session.product_branches["valves"] = ProductBranchState(
        selections=[first, later],
        first_display=first.model_copy(deep=True),
        next_display_index=2,
    )
    bot.sessions.save(session)


def test_exact_out_of_stock_identity_survives_hidden_card(orchestrator) -> None:
    session_id = "focus-hidden-exact-sku"

    hidden = orchestrator.handle_chat(
        session_id,
        "Покажи артикул OUT-110-1000, только в наличии",
    )

    assert hidden.products == []
    assert hidden.debug["focused_product_sku"] == "OUT-110-1000"

    fact = orchestrator.handle_chat(
        session_id,
        "Сколько он стоит и какой у него артикул?",
    )

    assert [product.sku for product in fact.products] == ["OUT-110-1000"]
    answer = normalize_text(fact.answer)
    assert "out-110-1000" in answer
    assert "436" in answer
    assert "для какой задачи" not in answer


def test_short_exact_product_fact_outranks_selection_funnel() -> None:
    product = Product(
        sku="WH-80-E",
        name="Водонагреватель накопительный 80 л",
        category_path="Водонагреватели накопительные",
        brand="VESTA",
        url="https://example.test/wh80e",
        price=15000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "WH-80-E",
            "объем бака": "80 л",
            "вид нагрева": "Электрический",
        },
    )
    orchestrator = ChatOrchestrator(products=[product])
    session_id = "focus-heating-method"
    shown = orchestrator.handle_chat(session_id, "Покажи артикул WH-80-E")
    assert [item.sku for item in shown.products] == ["WH-80-E"]

    fact = orchestrator.handle_chat(session_id, "Способ нагрева?")

    assert [item.sku for item in fact.products] == ["WH-80-E"]
    assert "электрическ" in normalize_text(fact.answer)
    assert "накопительный или проточный" not in normalize_text(fact.answer)
    assert fact.debug["focused_product_sku"] == "WH-80-E"


def test_valve_analogs_inherit_source_facets_and_exclude_neighbour_skus() -> None:
    common = {
        "тип товара": "Кран шаровой",
        "диаметр подключения, дюйм": "1/2",
        "тип резьбы": "С внутренней резьбой (ff)",
        "тип ручки": "Бабочка",
        "пропускная способность": "Полнопроходной",
        "форма корпуса": "Прямой",
    }

    def valve(sku: str, name: str, attributes: dict[str, str]) -> Product:
        return Product(
            sku=sku,
            name=name,
            category_path="Краны шаровые",
            brand="TEST",
            url=f"https://example.test/{sku}",
            price=500,
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized=attributes,
        )

    source = valve(
        "VALVE-SOURCE",
        'Кран шаровой полнопроходной 1/2" ВР-ВР, бабочка',
        common,
    )
    compatible = valve(
        "VALVE-PEER",
        'Кран шаровой полнопроходной 1/2" ВР-ВР, бабочка, серия 2',
        {**common, "тип резьбы": "Внутренняя"},
    )
    wrong_thread = valve(
        "VALVE-FM",
        'Кран шаровой полнопроходной 1/2" ВР-НР, бабочка',
        {**common, "тип резьбы": "Внутренняя-наружная"},
    )
    wrong_handle = valve(
        "VALVE-LEVER",
        'Кран шаровой полнопроходной 1/2" ВР-ВР, рычаг',
        {**common, "тип ручки": "Рычаг"},
    )
    bot = ChatOrchestrator(
        products=[source, compatible, wrong_thread, wrong_handle]
    )

    shown = bot.handle_chat("valve-analogs", "Покажи артикул VALVE-SOURCE")
    assert [card.sku for card in shown.products] == ["VALVE-SOURCE"]

    analogs = bot.handle_chat("valve-analogs", "Покажи аналоги")

    assert [card.sku for card in analogs.products] == ["VALVE-PEER"]
    assert analogs.debug["product_relation"]["source_sku"] == "VALVE-SOURCE"

    comparison = bot.handle_chat(
        "valve-analogs",
        "Сравни исходный и первый аналог",
    )

    assert [card.sku for card in comparison.products] == [
        "VALVE-SOURCE",
        "VALVE-PEER",
    ]
    assert "Главное отличие — тип резьбы" not in comparison.answer


def test_analogue_relation_preserves_source_for_comparison(sample_products) -> None:
    source_product = Product(
        sku="PUMP-25-60",
        name="Насос циркуляционный 25-60 180 мм",
        category_path="Насосы циркуляционные",
        brand="VESTA",
        url="https://example.test/pump2560",
        price=6100,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "PUMP-25-60",
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "монтажная длина": "180 мм",
            "напор": "6 м",
        },
    )
    compatible_peer = Product(
        sku="PUMP-25-60-ALT",
        name="Насос циркуляционный 25-60 180 мм, альтернативная серия",
        category_path="Насосы циркуляционные",
        brand="VESTA",
        url="https://example.test/pump2560alt",
        price=5900,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={
            "артикул": "PUMP-25-60-ALT",
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "монтажная длина": "180 мм",
            "напор": "6 м",
        },
    )
    orchestrator = ChatOrchestrator(
        products=[
            *[
                product
                for product in sample_products
                if not product.sku.startswith("PUMP-")
            ],
            source_product,
            compatible_peer,
        ]
    )
    session_id = "focus-analogue-source"
    source = orchestrator.handle_chat(
        session_id,
        "Покажи артикул PUMP-25-60",
    )
    assert [product.sku for product in source.products] == ["PUMP-25-60"]

    analogues = orchestrator.handle_chat(session_id, "Покажи ближайший аналог")

    assert analogues.products
    assert "PUMP-25-60" not in [product.sku for product in analogues.products]
    relation = analogues.debug["product_relation"]
    assert relation["source_sku"] == "PUMP-25-60"
    assert relation["alternative_skus"] == [
        product.sku for product in analogues.products
    ]

    comparison = orchestrator.handle_chat(
        session_id,
        "Сравни их с исходным товаром",
    )

    compared_skus = [product.sku for product in comparison.products]
    assert compared_skus[0] == "PUMP-25-60"
    assert set(relation["alternative_skus"]).issubset(compared_skus)
    assert "PUMP-25-60" in comparison.answer
    assert comparison.debug["focused_product_sku"] == "PUMP-25-60"


def test_first_shown_product_selects_oldest_snapshot_not_latest(orchestrator) -> None:
    session_id = "focus-first-shown-across-snapshots"
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-40")
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-60")

    returned = orchestrator.handle_chat(
        session_id,
        "Вернёмся к первому показанному насосу",
    )

    assert [product.sku for product in returned.products] == ["PUMP-25-40"]
    assert returned.debug["focused_product_sku"] == "PUMP-25-40"


def test_first_shown_reference_without_product_noun_uses_oldest_card(
    orchestrator,
) -> None:
    """The adjective itself is a grounded ordinal, even before punctuation."""

    session_id = "focus-first-shown-no-noun"
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-40")
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-60")

    returned = orchestrator.handle_chat(
        session_id,
        "Вернёмся к первому показанному. Какой у него артикул?",
    )

    assert [product.sku for product in returned.products] == ["PUMP-25-40"]
    assert "PUMP-25-40" in returned.answer
    assert returned.debug["focused_product_sku"] == "PUMP-25-40"


def test_first_product_reference_uses_oldest_exact_sku(orchestrator) -> None:
    session_id = "focus-first-product-two-exact-skus"
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-40")
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-60")

    returned = orchestrator.handle_chat(
        session_id,
        "Вернёмся к первому товару. Какой у него артикул?",
    )

    assert [product.sku for product in returned.products] == ["PUMP-25-40"]
    assert "PUMP-25-40" in returned.answer
    assert returned.debug["focused_product_sku"] == "PUMP-25-40"


@pytest.mark.parametrize(
    "message",
    [
        "Вернёмся к первому показанному. Какой у него артикул?",
        "Вернёмся к самому первому. Назови его артикул.",
        "Какой товар ты показал сначала? Назови артикул.",
        "Дай артикул того, что показал самым первым.",
        "Вернёмся к первой позиции из первого списка. Какой артикул?",
    ],
)
def test_first_display_paraphrases_ignore_context_only_llm_slots(
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copied previous constraint cannot move presentation ordinal one."""

    mini = _referent_valve("VT.331.N.04", handle="Мини", price=449)
    butterfly = _referent_valve(
        "VT.217.N.04",
        handle="Бабочка",
        price=452,
    )
    bot = ChatOrchestrator(products=[mini, butterfly])
    session_id = "first-display-stale-llm"
    _seed_ordered_valve_displays(bot, session_id)

    stale_llm_intent = IntentResult(
        intent_type="unknown",
        category="valves",
        confidence=0.45,
        slots={"handle_type": "butterfly"},
        llm_used=True,
        raw={"llm_output_accepted": True},
    )
    monkeypatch.setattr(
        bot.intent_router,
        "route",
        lambda current_message, session: stale_llm_intent,
    )

    returned = bot.handle_chat(session_id, message)

    assert [card.sku for card in returned.products] == ["VT.331.N.04"]
    assert "VT.331.N.04" in returned.answer
    assert returned.debug["focused_product_sku"] == "VT.331.N.04"
    assert stale_llm_intent.raw["explicit_current_slot_keys"] == []
    assert stale_llm_intent.raw["context_only_slot_keys"] == ["handle_type"]


def test_product_branch_keeps_immutable_first_ordered_display() -> None:
    mini = _referent_valve("VT.331.N.04", handle="Мини", price=449)
    butterfly = _referent_valve(
        "VT.217.N.04",
        handle="Бабочка",
        price=452,
    )
    bot = ChatOrchestrator(products=[mini, butterfly])
    session = bot.sessions.get("append-only-display-events")
    cards = [
        bot._card_for_sku("VT.331.N.04", category="valves"),
        bot._card_for_sku("VT.217.N.04", category="valves"),
    ]
    assert all(card is not None for card in cards)
    grounded_cards = [card for card in cards if card is not None]
    agents = ["FeedSearchAgent", "ProductCardAgent"]

    for index in range(14):
        session.history.extend(
            [
                {"role": "user", "content": f"display {index}"},
                {"role": "assistant", "content": "products"},
            ]
        )
        intent = IntentResult(
            intent_type="attribute_request",
            category="valves",
            confidence=1.0,
            slots={
                "size_inch": "1/2",
                **({"handle_type": "butterfly"} if index else {}),
            },
        )
        bot._remember_product_selection(
            session,
            list(reversed(grounded_cards)) if index else grounded_cards,
            intent,
            agents,
        )

    branch = session.product_branches["valves"]
    assert len(branch.selections) == 12
    assert branch.next_display_index == 14
    assert branch.first_display is not None
    assert branch.first_display.display_index == 0
    assert branch.first_display.product_skus == ["VT.331.N.04", "VT.217.N.04"]
    assert "handle_type" not in branch.first_display.constraints
    assert branch.selections[-1].display_index == 13
    assert branch.selections[-1].product_skus == ["VT.217.N.04", "VT.331.N.04"]


def test_add_component_followup_preserves_same_category_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "radiator-add-component-keeps-size"
    session = bot.sessions.get(session_id)
    session.category = "radiator_fittings"
    session.slots = {
        "product_kind": "thermostatic_head",
        "size_inch": "1/2",
    }
    bot.sessions.save(session)

    intent = IntentResult(
        intent_type="broad_category",
        category="radiator_fittings",
        confidence=0.9,
        slots={"product_kind": "thermostatic_valve"},
        is_topic_change=False,
    )
    monkeypatch.setattr(
        bot.intent_router,
        "route",
        lambda current_message, current_session: intent,
    )

    response = bot.handle_chat(session_id, "Нужна головка вместе с клапаном.")

    assert response.debug["slots"]["size_inch"] == "1/2"
    assert intent.raw["dialog_act"] == "add_component"
    assert "размер 1/2 или 3/4" not in normalize_text(response.answer)


def test_no_exact_match_confirmation_preserves_fitting_goal() -> None:
    only_ninety_degree = Product(
        sku="PPR-M-90",
        name='Угольник PPR 90° с переходом на нар. р. 20х1/2"',
        category_path="Фитинги полипропиленовые",
        brand="TEST",
        url="https://example.test/ppr-m-90",
        price=300,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "артикул": "PPR-M-90",
            "материал": "Полипропилен, латунь",
            "тип товара": "Угольник",
            "тип присоединения": "Под сварку",
            "тип резьбы": "Наружная",
            "присоединительная резьба, дюйм": "1/2",
            "диаметр (мм)": "20",
            "угол (градусы)": "90",
        },
    )
    bot = ChatOrchestrator(products=[only_ninety_degree])
    session_id = "no-exact-ppr-confirmation"

    missing = bot.handle_chat(
        session_id,
        "Нужен PPR угол 20×1/2 с наружной резьбой, но строго 45 градусов.",
    )
    assert missing.products == []
    assert missing.debug["category"] == "fittings"

    confirmation = bot.handle_chat(
        session_id,
        "То есть точного PPR 20×1/2 НР на 45° в каталоге нет?",
    )

    assert confirmation.products == []
    assert confirmation.debug["category"] == "fittings"
    assert confirmation.debug["last_search_outcome"]["status"] == "no_exact_match"
    assert confirmation.debug["last_search_outcome"]["category"] == "fittings"
    assert confirmation.answer.startswith("Да.")
    assert "точного совпадения" in normalize_text(confirmation.answer)
    assert "труб" not in normalize_text(confirmation.answer)
    session = bot.sessions.get(session_id)
    assert session.category == "fittings"
    assert "pipes" not in (session.project_context.get("goals") or {})


def test_failed_turn_rolls_back_even_after_intermediate_save(
    orchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "transactional-turn-rollback"
    session = orchestrator.sessions.get(session_id)
    session.slots["stable"] = "before"
    session.history.append({"role": "user", "content": "earlier"})
    orchestrator.sessions.save(session)

    def fail_after_mutation(current_session_id: str, message: str):
        live = orchestrator.sessions.get(current_session_id)
        live.slots["stable"] = "partially-mutated"
        live.history.append({"role": "user", "content": message})
        orchestrator.sessions.save(live)
        raise RuntimeError("synthetic turn failure")

    monkeypatch.setattr(orchestrator, "_handle_chat", fail_after_mutation)

    with pytest.raises(RuntimeError, match="synthetic turn failure"):
        orchestrator.handle_chat(session_id, "failing turn")

    restored = orchestrator.sessions.get(session_id)
    assert restored.slots["stable"] == "before"
    assert restored.history == [{"role": "user", "content": "earlier"}]
