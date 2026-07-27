"""Regressions for relevance/ranking and card-quality bugs (found 2026-07-21).

Reported case: «Нужен шаровой кран BASE со стальной рукояткой, 1/2", ВР/ВР»
returned a ROMMER ВН/НР valve first purely because it was the cheapest, and the
one valve matching both BASE and ВР/ВР came third.
"""

from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.product_card import ProductCardAgent, RELEVANT_ATTRS
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def _valve(sku: str, name: str, brand: str, price: float, thread: str, handle: str = "Бабочка") -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Краны шаровые",
        brand=brand,
        url=f"https://example.test/{sku}",
        price=price,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "полное наименование": name,
            "штрихкод": "8050043053404",
            "диаметр подключения, дюйм": "1/2",
            "тип резьбы": thread,
            "тип ручки": handle,
            "тип присоединения": "Резьбовой",
        },
    )


ROMMER = _valve("RBV-0005", 'Кран шаровый Rommer с американкой 1/2" ВН/НР, ручка бабочка',
                "ROMMER", 395, "С внутренней наружной резьбой (fm)")
MINI = _valve("VT.331.N.04", 'Кран шаровой MINI 1/2" вн.-нар.', "VALTEC", 449,
              "С внутренней наружной резьбой (fm)", handle="Мини")
BASE = _valve("VT.217S.N.04", 'Кран шаровой BASE-ГОСТ полнопроходной вн.-вн. DN15 PN40 1/2"',
              "VALTEC", 485, "Внутренняя")


def test_thread_type_and_series_are_extracted_from_the_request() -> None:
    result = IntentRouterAgent().route(
        'Нужен шаровой кран BASE со стальной рукояткой, 1/2", ВР/ВР', session=None
    )

    assert result.slots["thread_type"] == "ff"
    assert "base" in result.slots["name_tokens"]


def test_matching_product_outranks_a_cheaper_mismatching_one() -> None:
    # The previous implementation applied relevance sorts and then a final
    # unconditional sort by (stock, price), which silently discarded them.
    query = SearchQuery(
        original_text='кран BASE 1/2 вр/вр',
        category="valves",
        slots={"thread_type": "ff", "name_tokens": ["base"], "size_inch": "1/2"},
    )

    order = [product.sku for product in RankingAgent().rank([ROMMER, MINI, BASE], query)]

    assert order[0] == "VT.217S.N.04"


def test_brand_request_is_not_overridden_by_price() -> None:
    query = SearchQuery(original_text="кран valtec 1/2", category="valves", brand="VALTEC")

    order = [product.sku for product in RankingAgent().rank([ROMMER, MINI, BASE], query)]

    assert order[0].startswith("VT."), order


def test_unconstrained_search_still_prefers_stock_then_price() -> None:
    # Without stated constraints the old cheap-first behaviour must remain.
    query = SearchQuery(original_text="кран шаровой", category="valves")

    order = [product.sku for product in RankingAgent().rank([BASE, MINI, ROMMER], query)]

    assert order == ["RBV-0005", "VT.331.N.04", "VT.217S.N.04"]


def test_identity_attributes_are_not_shown_as_characteristics() -> None:
    # For ~31% of the feed these filled every slot, so "чем отличаются"
    # compared names and barcodes instead of specs.
    card = ProductCardAgent().build_card(
        BASE, SearchQuery(original_text="", category="valves", slots={})
    )

    assert card is not None
    assert "полное наименование" not in card.characteristics
    assert "штрихкод" not in card.characteristics
    assert card.characteristics


def test_valve_cards_expose_thread_and_handle_for_comparison() -> None:
    # Valves of one diameter almost always share "тип присоединения: Резьбовой",
    # so the comparison used to report price as the only difference.
    assert RELEVANT_ATTRS["valves"].index("тип резьбы") < RELEVANT_ATTRS["valves"].index(
        "тип присоединения"
    )
    card = ProductCardAgent().build_card(
        BASE, SearchQuery(original_text="", category="valves", slots={})
    )

    assert card is not None
    assert "тип резьбы" in card.characteristics


def test_generic_category_request_is_not_treated_as_an_exact_name_lookup() -> None:
    # «кран шаровой 1/2 для воды» consists only of category + spec words. The
    # word «воды» was satisfied by an unrelated category_path ("Системы
    # контроля протечки воды"), so this single out-of-stock actuator valve
    # replaced the whole ranked result set.
    actuator = Product(
        sku="163",
        name='Шаровый кран Бастион 1/2" с электроприводом, 12V',
        category_path="Системы контроля протечки воды",
        brand="БАСТИОН",
        url="https://example.test/163",
        price=5271,
        currency="RUB",
        stock_status="нет в наличии",
        stock_qty=0,
        attributes_normalized={"тип товара": "Кран шаровой"},
    )
    agent = FeedSearchAgent()
    agent.set_products([actuator, ROMMER, MINI, BASE])
    query = SearchQuery(original_text="кран шаровой 1/2 для воды", category="valves")

    assert agent.search_by_name("кран шаровой 1/2 для воды", query) == []


def test_full_product_name_lookup_still_works() -> None:
    # Guard against overcorrecting: a pasted product name must still resolve,
    # because it carries distinctive words beyond category and size.
    agent = FeedSearchAgent()
    agent.set_products([ROMMER, MINI, BASE])
    query = SearchQuery(original_text="", category="valves")

    found = agent.search_by_name(
        'Кран шаровой BASE-ГОСТ полнопроходной вн.-вн. DN15 PN40 1/2"', query
    )

    assert [product.sku for product in found][:1] == ["VT.217S.N.04"]
