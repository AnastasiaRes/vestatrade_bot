from __future__ import annotations

from app.models import ProductCard, ProductFocusState, SessionState
from app.v2_visible_products import customer_visible_v2_scope, ordinal_indices


def _card(sku: str) -> ProductCard:
    return ProductCard(
        sku=sku,
        name=f"Товар {sku}",
        price=100,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=1,
        url=f"https://example.test/{sku}",
    )


def test_visible_scope_keeps_order_and_resolves_ordinals_in_customer_order() -> None:
    session = SessionState(
        session_id="visible-reference-order",
        v2_last_products=[_card("A"), _card("B"), _card("C"), _card("D"), _card("E")],
        v2_selection_id="selection-1",
        v2_source_revision="source-1",
        product_focus=ProductFocusState(sku="C", category="pumps"),
    )

    scope = customer_visible_v2_scope(session)

    assert scope.is_valid is True
    assert ordinal_indices("Сначала четвёртый, затем первый и пятый") == (3, 0, 4)
    assert scope.ordinal(0).canonical_sku == "A"
    assert scope.ordinal(4).canonical_sku == "E"
    assert scope.current_focus().canonical_sku == "C"


def test_visible_scope_never_turns_out_of_scope_focus_or_bad_order_into_a_product() -> None:
    session = SessionState(
        session_id="visible-reference-invalid",
        v2_last_products=[_card("A"), _card("A")],
        v2_selection_id="selection-1",
        v2_source_revision="source-1",
        product_focus=ProductFocusState(sku="OUTSIDE", category="pumps"),
    )

    scope = customer_visible_v2_scope(session)

    assert scope.is_valid is False
    assert scope.ordinal(0).canonical_sku is None
    assert scope.current_focus().canonical_sku is None
