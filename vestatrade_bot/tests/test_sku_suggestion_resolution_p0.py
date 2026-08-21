from __future__ import annotations

from app.agents.sku_suggestions import resolve_sku_suggestion
from app.models import Product


def _product(sku: str, name: str | None = None) -> Product:
    return Product(
        sku=sku,
        name=name or f"Товар {sku}",
        category_path="Тест",
        url=f"https://example.test/{sku}",
        price=1,
        stock_status="в наличии",
        stock_qty=1,
    )


def test_explicit_sku_typo_reports_one_unique_neighbour() -> None:
    result = resolve_sku_suggestion(
        "VT331N0Z",
        [_product("VT.331.N.04"), _product("UNRELATED-100")],
    )

    assert result.status == "unique"
    assert [product.sku for product in result.candidates] == ["VT.331.N.04"]
    assert result.distance == 1


def test_explicit_sku_typo_keeps_all_equally_near_catalogue_candidates() -> None:
    products = [_product(f"15100{digit}") for digit in range(1, 10)]

    result = resolve_sku_suggestion("15100Z", products)

    assert result.status == "ambiguous"
    assert [product.sku for product in result.candidates] == [
        "151001",
        "151002",
        "151003",
        "151004",
        "151005",
        "151006",
        "151007",
        "151008",
        "151009",
    ]
    assert result.distance == 1


def test_sku_repair_is_fail_closed_for_ordinary_short_tokens() -> None:
    result = resolve_sku_suggestion("кран", [_product("КРАН1")])

    assert result.status == "none"
    assert result.candidates == ()

