from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.product_constraints import (
    product_inch_connection_facts,
    single_inch_size_constraint_matches,
)
from app.models import Product, ProductCard, SearchQuery


def _valve(
    sku: str,
    name: str,
    *,
    attrs: dict[str, str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Водозапорная арматура",
        brand="VALTEC",
        url=f"https://example.test/{sku}",
        price=500,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип товара": "Кран шаровой", **(attrs or {})},
    )


def _card(product: Product) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        price=product.price or 0,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
    )


def test_single_size_query_rejects_two_and_three_port_reducing_valves() -> None:
    mixed_two_port = _valve(
        "VT.392.N.05",
        'Кран шаровой угловой для приборов 1/2&quot;х3/4&quot;',
    )
    mixed_three_port = _valve(
        "VT.256.N.04",
        'Кран шаровой для приборов 1/2&quot;х3/4&quot;х1/2&quot;',
    )
    uniform = _valve(
        "VT.217.N.05",
        'Кран шаровой BASE, бабочка 3/4&quot; вн.-вн.',
        attrs={"диаметр подключения, дюйм": "3/4"},
    )
    query = SearchQuery(
        original_text='кран 3/4"',
        category="valves",
        slots={"size_inch": "3/4"},
    )

    found = FeedSearchAgent([mixed_two_port, mixed_three_port, uniform]).search(query)

    assert [product.sku for product in found] == ["VT.217.N.05"]


def test_uniform_name_only_size_is_accepted_but_mixed_topology_is_not() -> None:
    name_only = _valve(
        "NAME-ONLY-34",
        'Кран шаровой BASE 3/4&quot; ВР-ВР',
    )
    equal_ports = _valve(
        "EQUAL-PORTS-12",
        'Кран шаровой угловой 1/2&quot;х1/2&quot;',
    )
    mixed = _valve(
        "MIXED-12-34",
        'Кран шаровой угловой 1/2&quot;х3/4&quot;',
    )

    assert single_inch_size_constraint_matches(name_only, "3/4") is True
    assert single_inch_size_constraint_matches(equal_ports, "1/2") is True
    assert single_inch_size_constraint_matches(mixed, "3/4") is False
    assert product_inch_connection_facts(mixed).is_mixed is True


def test_malformed_catalogue_mixed_number_is_one_nominal_size() -> None:
    valve = _valve(
        "ITAP-112",
        'Кран шаровой Itap IDEAL 1&quot;1/2 вн.-нар.',
        attrs={"диаметр подключения, дюйм": "1 1/2"},
    )

    facts = product_inch_connection_facts(valve)

    assert facts.sizes == frozenset({"11/2"})
    assert facts.is_mixed is False
    assert single_inch_size_constraint_matches(valve, "1 1/2") is True


def test_guardrails_rejects_mixed_port_card_for_single_size_contract() -> None:
    mixed = _valve(
        "VT.392.N.05",
        'Кран шаровой угловой для приборов 1/2&quot;х3/4&quot;',
    )
    query = SearchQuery(
        original_text='кран 3/4"',
        category="valves",
        slots={"size_inch": "3/4"},
    )

    result = GuardrailsAgent().validate_cards([_card(mixed)], [mixed], query)

    assert result.ok is False
    assert any("mandatory category characteristics" in issue for issue in result.issues)


def test_exact_sku_cannot_bypass_mixed_port_size_boundary() -> None:
    mixed = _valve(
        "VT.392.N.05",
        'Кран шаровой угловой для приборов 1/2&quot;х3/4&quot;',
    )
    query = SearchQuery(
        original_text='артикул VT.392.N.05, нужен 3/4"',
        category="valves",
        sku="VT.392.N.05",
        slots={"size_inch": "3/4"},
    )

    assert FeedSearchAgent([mixed]).search(query) == []


def test_alternative_path_keeps_uniform_size_boundary() -> None:
    mixed = _valve(
        "VT.392.N.05",
        'Кран шаровой угловой для приборов 1/2&quot;х3/4&quot;',
    )
    uniform = _valve(
        "VT.217.N.05",
        'Кран шаровой BASE, бабочка 3/4&quot; вн.-вн.',
    )
    query = SearchQuery(
        original_text='есть аналог крана 3/4"',
        category="valves",
        slots={"size_inch": "3/4"},
    )
    agent = FeedSearchAgent([mixed, uniform])

    assert agent._alternative_hard_slots_match(mixed, query, query.slots) is False
    assert agent._alternative_hard_slots_match(uniform, query, query.slots) is True
