"""Regressions for category-grounded product links across dialogue branches."""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text


def test_return_to_empty_valve_branch_never_links_active_pump(
    orchestrator: ChatOrchestrator,
) -> None:
    session_id = "empty-valve-branch-pump-link"

    missing_valve = orchestrator.handle_chat(
        session_id,
        'Нужен шаровой кран 3/4" с американкой для воды',
    )
    pump = orchestrator.handle_chat(
        session_id,
        "Покажи артикул PUMP-25-40",
    )
    returned = orchestrator.handle_chat(
        session_id,
        "Вернёмся к крану: дай ссылку на первый",
    )

    assert missing_valve.products == []
    assert "valves" not in orchestrator.sessions.get(session_id).product_branches
    assert [product.sku for product in pump.products] == ["PUMP-25-40"]

    answer = normalize_text(returned.answer)
    assert returned.products == []
    assert "pump-25-40" not in answer
    assert "https://example.test/pump2540" not in returned.answer
    assert "подходящая карточка не была показана" in answer
    assert "ссылку из другой категории не подставляю" in answer

    session = orchestrator.sessions.get(session_id)
    assert session.last_products == []
    assert session.category == "valves"
    assert session.product_branches["pumps"].selections[-1].product_skus == [
        "PUMP-25-40"
    ]


def test_return_to_saved_valve_branch_links_restored_valve_only(
    orchestrator: ChatOrchestrator,
) -> None:
    session_id = "saved-valve-branch-pump-link"

    valve = orchestrator.handle_chat(
        session_id,
        "Покажи кран шаровый угловой для воды 20 мм",
    )
    orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-40")
    returned = orchestrator.handle_chat(
        session_id,
        "Вернёмся к крану: дай ссылку на первый",
    )

    assert [product.sku for product in valve.products] == ["VALVE-20-ANGLE"]
    assert [product.sku for product in returned.products] == ["VALVE-20-ANGLE"]
    assert "https://example.test/valve20" in returned.answer
    assert "https://example.test/pump2540" not in returned.answer
    assert returned.debug["restored_product_skus"] == ["VALVE-20-ANGLE"]


def test_unqualified_all_links_preserve_every_category_in_mixed_active_view(
    orchestrator: ChatOrchestrator,
) -> None:
    session_id = "mixed-active-view-all-links"
    pump_response = orchestrator.handle_chat(session_id, "Покажи артикул PUMP-25-40")
    pump_card = list(orchestrator.sessions.get(session_id).last_products)
    valve_response = orchestrator.handle_chat(
        session_id,
        "Покажи артикул VALVE-20-ANGLE",
    )
    valve_card = list(orchestrator.sessions.get(session_id).last_products)
    assert [product.sku for product in pump_response.products] == ["PUMP-25-40"]
    assert [product.sku for product in valve_response.products] == ["VALVE-20-ANGLE"]

    session = orchestrator.sessions.get(session_id)
    session.last_products = [*pump_card, *valve_card]
    session.shown_product_skus = ["PUMP-25-40", "VALVE-20-ANGLE"]
    session.category = "pumps"
    orchestrator.sessions.save(session)

    response = orchestrator.handle_chat(
        session_id,
        "Дай ссылку на все показанные товары",
    )

    assert [product.sku for product in response.products] == [
        "PUMP-25-40",
        "VALVE-20-ANGLE",
    ]
    assert "https://example.test/pump2540" in response.answer
    assert "https://example.test/valve20" in response.answer
    assert [card.sku for card in session.last_products] == [
        "PUMP-25-40",
        "VALVE-20-ANGLE",
    ]


def test_plural_link_wording_is_routed_without_llm_guessing() -> None:
    bot = ChatOrchestrator(products=[])
    session = bot.sessions.get("plural-link-routing")

    intent = bot.intent_router.route(
        "Дай ссылки на все показанные товары",
        session,
    )

    assert intent.intent_type == "link_request"
