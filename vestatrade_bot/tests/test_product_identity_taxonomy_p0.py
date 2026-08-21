from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.product_identity import product_identity_facts
from app.models import Product, ProductCard, SearchQuery


def _product(
    sku: str,
    name: str,
    *,
    category: str,
    product_type: str | None = None,
    attributes: dict[str, str] | None = None,
) -> Product:
    attrs = dict(attributes or {})
    if product_type is not None:
        attrs["тип товара"] = product_type
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=700,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized=attrs,
    )


def _collector_tee() -> Product:
    return _product(
        "VTp.781.0.04005",
        'Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус)',
        category="Фитинги",
        product_type="Кран шаровый",
        attributes={
            "присоединительная резьба, дюйм": "3/4",
            "диаметр (мм)": "40",
            "тип присоединения": "Под сварку",
            "тип резьбы": "Наружная",
        },
    )


def _ppr_valve() -> Product:
    return _product(
        "VTp.742.0.02505",
        'Кран под PPR 25х3/4"',
        category="Фитинги",
        product_type="Кран шаровый",
        attributes={
            "присоединительная резьба, дюйм": "3/4",
            "диаметр (мм)": "25",
            "тип присоединения": "Под сварку",
        },
    )


def test_composite_tee_keeps_primary_kind_separate_from_embedded_valve() -> None:
    tee = _collector_tee()
    facts = product_identity_facts(tee)
    search = FeedSearchAgent([tee])

    assert facts.primary_kind == "tee"
    assert facts.embedded_components == frozenset({"ball_valve"})
    assert facts.conflicts == ("title:tee!=type:ball_valve",)
    assert search.canonical_category(tee) == "fittings"
    assert search._product_kind_matches(tee, "ball_valve") is False
    assert search._valve_kind_matches(tee, "шаровый кран") is False


def test_real_ppr_standalone_valve_remains_a_ball_valve() -> None:
    valve = _ppr_valve()
    facts = product_identity_facts(valve)
    search = FeedSearchAgent([valve])

    assert facts.primary_kind == "ball_valve"
    assert facts.embedded_components == frozenset()
    assert facts.conflicts == ()
    assert search.canonical_category(valve) == "valves"
    assert search._product_kind_matches(valve, "ball_valve") is True
    assert search._valve_kind_matches(valve, "шаровый кран") is True


def test_valve_search_never_returns_a_fitting_with_an_embedded_valve() -> None:
    tee = _collector_tee()
    valve = _ppr_valve()
    search = FeedSearchAgent([tee, valve])
    query = SearchQuery(
        original_text='такой же, но 3/4"',
        category="valves",
        slots={"size_inch": "3/4", "valve_kind": "шаровый кран"},
    )

    assert [product.sku for product in search.search(query)] == [valve.sku]
    assert search.matches_constraints(tee, "valves", query.slots) is False


def test_composite_product_remains_discoverable_as_a_fitting() -> None:
    tee = _collector_tee()
    search = FeedSearchAgent([tee])
    query = SearchQuery(
        original_text='тройник PPR 40 х 3/4" с краном',
        category="fittings",
        slots={
            "fitting_system": "ppr",
            "diameter_mm": 40,
            "size_inch": "3/4",
        },
    )

    assert [product.sku for product in search.search(query)] == [tee.sku]
    assert search.matches_constraints(tee, "fittings", query.slots) is True


def test_filter_with_supplied_tap_is_not_reclassified_as_a_valve() -> None:
    water_filter = _product(
        "FILTER-1",
        "Фильтр для воды (бак 12 л, кран питьевой в комплекте)",
        category="Фильтры",
    )
    facts = product_identity_facts(water_filter)
    search = FeedSearchAgent([water_filter])

    assert facts.primary_kind == "filter"
    assert facts.embedded_components == frozenset({"valve"})
    assert search.canonical_category(water_filter) == "filters"
    assert search.matches_constraints(water_filter, "valves", {}) is False


def test_guardrail_rejects_injected_composite_card_for_valve_query() -> None:
    tee = _collector_tee()
    card = ProductCard(
        sku=tee.sku,
        name=tee.name,
        brand=tee.brand,
        price=tee.price or 0,
        stock_status=tee.stock_status,
        stock_qty=tee.stock_qty,
        url=tee.url or "",
    )
    query = SearchQuery(
        original_text='шаровый кран 3/4"',
        category="valves",
        slots={"size_inch": "3/4", "product_kind": "ball_valve"},
    )

    result = GuardrailsAgent().validate_cards([card], [tee], query)

    assert result.ok is False
    assert any("not valves" in issue for issue in result.issues)

