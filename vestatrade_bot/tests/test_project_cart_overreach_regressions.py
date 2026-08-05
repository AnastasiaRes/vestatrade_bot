"""Regressions for the project-cart over-triggering bug (found 2026-07-21).

A plain single-product boiler request used to be routed into the whole-house
"project cart" flow, answering "Подберите электрический котёл для дома 100 м²"
with a bundle of boiler + pump + warm-floor pipe + valve + sewer coupling.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


BUNDLE_MARKER = "стартовую подборку"


def _boiler(sku: str, name: str, description: str | None = None) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Котлы электрические",
        brand="ARDERIA",
        url=f"https://example.test/{sku}",
        price=38000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"артикул": sku, "мощность": "9 кВт", "тип котла": "Электрический"},
        description=description,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Подберите электрический котёл для дома площадью 100 м²",
        "подберите котел для дома 100 м2",
        "нужен котел в дом 100 м2",
        "подберите насос для дома",
    ],
)
def test_single_product_request_never_returns_whole_house_bundle(orchestrator, message) -> None:
    response = orchestrator.handle_chat(f"no-bundle-{hash(message)}", message)

    assert BUNDLE_MARKER not in response.answer
    assert response.debug["slots"].get("project_scope") is None
    # A sewer coupling has no business in a boiler/pump request.
    assert "манжета" not in response.answer.lower()


def test_polite_podberite_with_named_product_is_not_a_cart_request(orchestrator) -> None:
    # "подберите" is the ordinary polite imperative, not a request to assemble a
    # multi-category set. It should only imply a cart when no product is named.
    assert orchestrator._wants_project_selection("подберите котел на 100 м2") is False
    assert orchestrator._wants_project_selection("подберите всё для отопления") is True


def test_dlya_doma_is_not_a_whole_house_project_scope(orchestrator) -> None:
    assert orchestrator._explicit_project_scope_from_text("котел для дома 100 м2") is None
    # Genuine whole-system wording still opens the general funnel.
    assert orchestrator._explicit_project_scope_from_text("нужно в дом сантехнику") == "general"


def test_project_scope_does_not_latch_onto_later_product_requests(orchestrator) -> None:
    # Once a real project was started, an unrelated single-product request must
    # not be swallowed by the still-active scope. Previously project_scope stuck
    # for the whole session and every later message carrying an area re-entered
    # the bundle.
    orchestrator.handle_chat("no-latch", "нужно отопление под ключ")
    orchestrator.handle_chat("no-latch", "120 м2, газ есть")
    response = orchestrator.handle_chat("no-latch", "мне нужен котел на 40м2")

    # Falls back into the boiler mini-scenario (a clarifying question), not a
    # re-run of the whole-house bundle. The exact question depends on what the
    # session already knows, so only assert it is about the boiler.
    assert BUNDLE_MARKER not in response.answer
    assert response.products == []
    assert "кот" in response.answer.lower()


def test_genuine_project_followups_still_build_the_cart(orchestrator) -> None:
    # Guard against overcorrecting: bare parameter answers ("50м2", "водяной от
    # котла") must still be read as project follow-ups even though the second
    # one mentions a boiler.
    orchestrator.handle_chat("still-works", "хочу сделать теплые полы, что нужно?")
    response = orchestrator.handle_chat("still-works", "50м2, водяной от котла")

    assert response.debug["slots"]["project_scope"] == "warm_floor"
    assert response.products


def test_warm_floor_updates_do_not_replay_the_whole_cart(orchestrator) -> None:
    session_id = "warm-floor-compact-updates"

    opening = orchestrator.handle_chat(
        session_id,
        "Нужен водяной тёплый пол",
    )
    assert "площад" in opening.answer.lower()
    assert "электричес" not in opening.answer.lower()

    initial = orchestrator.handle_chat(session_id, "80 м²")
    assert initial.products
    assert orchestrator.sessions.get(session_id).slots.get("project_cart")

    insulation = orchestrator.handle_chat(session_id, "Утеплитель уже есть")
    assert insulation.products == []
    assert BUNDLE_MARKER not in insulation.answer
    assert "утеплитель" in insulation.answer.lower()
    assert "источник тепла" in insulation.answer.lower()

    corrected = orchestrator.handle_chat(
        session_id,
        "Нет, площадь не 80, а 100 м²",
    )
    assert corrected.products == []
    assert BUNDLE_MARKER not in corrected.answer
    assert "650–700" in corrected.answer
    assert "9 контур" in corrected.answer

    heat_source = orchestrator.handle_chat(session_id, "Газовый котёл")
    assert heat_source.products == []
    assert BUNDLE_MARKER not in heat_source.answer
    assert "газовый котел" in heat_source.answer.lower().replace("ё", "е")
    assert "автоматик" in heat_source.answer.lower()

    refreshed = orchestrator.handle_chat(
        session_id,
        "Покажи обновлённую подборку",
    )
    assert refreshed.products
    assert "артикул" in refreshed.answer.lower()


@pytest.mark.parametrize(
    "message",
    [
        "Собери комплект с автоматикой",
        "Покажи обновлённую подборку с автоматикой",
    ],
)
def test_explicit_warm_floor_cart_refresh_rebuilds_after_slot_delta(
    orchestrator,
    message,
) -> None:
    session_id = f"warm-floor-explicit-refresh-{hash(message)}"
    orchestrator.handle_chat(session_id, "Нужен водяной тёплый пол")
    orchestrator.handle_chat(session_id, "80 м²")

    refreshed = orchestrator.handle_chat(session_id, message)

    assert refreshed.debug["slots"]["warm_floor_automation_needed"] is True
    assert refreshed.products
    assert "FeedSearchAgent" in refreshed.debug["agents_used"]


def test_switch_to_electric_floor_invalidates_water_floor_cart(
    orchestrator,
) -> None:
    session_id = "warm-floor-switch-to-electric"
    orchestrator.handle_chat(session_id, "Нужен водяной тёплый пол")
    water_cart = orchestrator.handle_chat(session_id, "80 м²")
    assert water_cart.products
    assert water_cart.debug["slots"].get("project_cart")

    switched = orchestrator.handle_chat(
        session_id,
        "Нет, пол будет электрический",
    )
    session = orchestrator.sessions.get(session_id)

    assert switched.debug["slots"]["warm_floor_type"] == "электрический"
    assert switched.products == []
    assert "project_cart" not in switched.debug["slots"]
    assert "электричес" in switched.answer.lower()
    assert "труба для чего" not in switched.answer.lower()
    assert session.pending_slot_keys == []

    summary = orchestrator.handle_chat(
        session_id,
        "Покажи обновлённую подборку",
    )
    assert summary.products == []
    assert "project_cart" not in summary.debug["slots"]
    assert "электричес" in summary.answer.lower()
    assert "PUMP-25-40" not in summary.answer


def test_cart_omits_pump_already_built_into_the_boiler() -> None:
    # Arderia E9's own card states "Встроенный циркуляционный насос", yet the
    # cart used to list a separate 3844 RUB pump beside it — selling hardware
    # the customer already owns.
    products = [
        _boiler(
            "ARD-E9",
            "Электрический котёл Arderia E9, 9 кВт",
            "Встроенный циркуляционный насос с тремя скоростями и расширительный бак.",
        ),
        Product(
            sku="PUMP-25-40",
            name="Насос циркуляционный 25-40 180 мм",
            category_path="Насосы циркуляционные",
            brand="VESTA",
            url="https://example.test/pump2540",
            price=4300,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=3,
            attributes_normalized={
                "артикул": "PUMP-25-40",
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "4 м",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    bot.handle_chat("builtin-pump", "нужно отопление под ключ")
    response = bot.handle_chat("builtin-pump", "100 м2, электричество")

    assert "PUMP-25-40" not in response.answer
    assert "уже встроен" in response.answer.lower()
    # The omission must be explained as built-in, never as "not found in stock".
    assert "не добавил артикулы для категорий: насосы" not in response.answer.lower()


def test_cart_keeps_pump_when_boiler_does_not_confirm_a_builtin_one() -> None:
    # No confirmation in the card => the pump stays. The guardrail is
    # deliberately conservative; it must not drop parts on a guess.
    products = [
        _boiler("ECA-6", "Электрический котёл E.C.A. Arceus ST, 6 кВт", "Электрический котёл 6 кВт."),
        Product(
            sku="PUMP-25-40",
            name="Насос циркуляционный 25-40 180 мм",
            category_path="Насосы циркуляционные",
            brand="VESTA",
            url="https://example.test/pump2540",
            price=4300,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=3,
            attributes_normalized={
                "артикул": "PUMP-25-40",
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "4 м",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    bot.handle_chat("no-builtin", "нужно отопление под ключ")
    response = bot.handle_chat("no-builtin", "100 м2, электричество")

    assert "PUMP-25-40" in response.answer
    assert "уже встроен" not in response.answer.lower()
