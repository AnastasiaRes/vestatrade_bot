from __future__ import annotations

from app.catalog_v2.product_reference import (
    NamedProductResolutionStatus,
    resolve_strict_named_catalog_product,
    resolve_strict_named_catalog_products,
)
from app.models import Product


def _product(sku: str, name: str, brand: str) -> Product:
    return Product(sku=sku, name=name, brand=brand)


def test_strict_named_product_reference_requires_unique_brand_and_model() -> None:
    products = [
        _product("2202210", "Котел электрический Arderia E9, 9 кВт", "Arderia"),
        _product("2202211", "Котел электрический Arderia E12, 12 кВт", "Arderia"),
    ]

    exact = resolve_strict_named_catalog_product("Arderia E9", products)
    assert exact.status == NamedProductResolutionStatus.EXACT
    assert exact.canonical_sku == "2202210"

    assert (
        resolve_strict_named_catalog_product("Arderia", products).status
        == NamedProductResolutionStatus.NONE
    )
    assert (
        resolve_strict_named_catalog_product("E9", products).status
        == NamedProductResolutionStatus.NONE
    )


def test_strict_named_product_reference_refuses_duplicate_model_rows() -> None:
    products = [
        _product("A", "Котел электрический Arderia E9", "Arderia"),
        _product("B", "Котел Arderia E9, другая поставка", "Arderia"),
    ]

    resolution = resolve_strict_named_catalog_product("Arderia E9", products)

    assert resolution.status == NamedProductResolutionStatus.AMBIGUOUS
    assert resolution.canonical_sku is None
    assert resolution.candidate_skus == ("A", "B")


def test_strict_named_product_pair_resolves_each_model_without_fuzzy_search() -> None:
    products = (
        _product("SB28", "Котел газовый Arderia SB28 28 кВт", "Arderia"),
        _product("SB32", "Котел газовый Arderia SB32 32 кВт", "Arderia"),
        _product("E9", "Котел электрический Arderia E9 9 кВт", "Arderia"),
    )

    resolved = resolve_strict_named_catalog_products(
        "Чем Arderia SB28 отличается от Arderia SB32?",
        products,
    )

    assert [item.status for item in resolved] == [
        NamedProductResolutionStatus.EXACT,
        NamedProductResolutionStatus.EXACT,
    ]
    assert [item.canonical_sku for item in resolved] == ["SB28", "SB32"]


def test_named_model_identity_tolerates_harmless_catalogue_punctuation() -> None:
    products = (
        _product(
            "8216262000",
            "Котел электрический E.C.A. Arceus ST - 6 кВт",
            "E.C.A.",
        ),
    )

    resolved = resolve_strict_named_catalog_product(
        "У электрокотла E.C.A. Arceus ST 6 есть встроенный насос?",
        products,
    )

    assert resolved.status == NamedProductResolutionStatus.EXACT
    assert resolved.canonical_sku == "8216262000"
