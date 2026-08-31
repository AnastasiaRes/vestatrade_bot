"""Strict, catalogue-bound resolution of an explicitly named product.

This is intentionally narrower than catalogue search.  It may resolve a
single product only when the customer's text contains both the brand present in
the feed and a model marker that occurs in exactly one product name.  The
helper is shared by ProductFact and the V2 offer path so they cannot disagree
about a phrase such as ``Arderia E9``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Protocol


class _CatalogProduct(Protocol):
    sku: str
    name: str


class NamedProductResolutionStatus(str, Enum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


@dataclass(frozen=True)
class NamedProductResolution:
    status: NamedProductResolutionStatus
    raw: str = ""
    canonical_sku: str | None = None
    candidate_skus: tuple[str, ...] = ()
    reason_code: str = "named_product_not_resolved"


_MODEL_MARKER_RE = re.compile(
    r"(?<![\w])(?:[a-zа-я]{1,12}\s*[-/]?\s*\d{1,4})(?![\w])",
    re.IGNORECASE,
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _model_identity(value: object) -> str:
    """Compare an already extracted model marker across harmless punctuation."""

    return re.sub(r"[^a-zа-я0-9]+", "", _normalise(value))


def _brand(product: _CatalogProduct) -> str:
    """Read the feed brand from either a Product or source-snapshot facts."""

    direct = getattr(product, "brand", None)
    if direct:
        return _normalise(direct)
    for fact in getattr(product, "facts", ()):
        if str(getattr(fact, "name", "")) == "brand":
            return _normalise(getattr(fact, "value", ""))
    return ""


def resolve_strict_named_catalog_product(
    utterance: str,
    products: Iterable[_CatalogProduct],
) -> NamedProductResolution:
    """Resolve only one explicit ``brand + model`` reference from the feed.

    A brand on its own, a model token on its own, and a multi-model phrase do
    not identify a product.  The caller must ask a subject-specific question
    instead of silently picking the nearest product.
    """

    normalized = _normalise(utterance)
    model_markers = tuple(
        _model_identity(match.group(0))
        for match in _MODEL_MARKER_RE.finditer(utterance)
    )
    if not model_markers:
        return NamedProductResolution(
            status=NamedProductResolutionStatus.NONE,
            reason_code="named_product_model_marker_missing",
        )

    candidates: list[_CatalogProduct] = []
    for product in products:
        brand = _brand(product)
        if not brand or brand not in normalized:
            continue
        product_name = _model_identity(product.name)
        if any(marker and marker in product_name for marker in model_markers):
            candidates.append(product)

    unique = tuple({product.sku: product for product in candidates}.values())
    if len(unique) == 1:
        return NamedProductResolution(
            status=NamedProductResolutionStatus.EXACT,
            raw=utterance[:240],
            canonical_sku=unique[0].sku,
            candidate_skus=(unique[0].sku,),
            reason_code="strict_brand_model_catalogue_match",
        )
    if len(unique) > 1:
        return NamedProductResolution(
            status=NamedProductResolutionStatus.AMBIGUOUS,
            raw=utterance[:240],
            candidate_skus=tuple(product.sku for product in unique),
            reason_code="strict_brand_model_catalogue_match_ambiguous",
        )
    return NamedProductResolution(
        status=NamedProductResolutionStatus.NONE,
        raw=utterance[:240],
        reason_code="strict_brand_model_catalogue_match_missing",
    )


def resolve_strict_named_catalog_products(
    utterance: str,
    products: Iterable[_CatalogProduct],
) -> tuple[NamedProductResolution, ...]:
    """Resolve each explicit brand/model marker independently and exactly.

    This plural form is for relations such as Compare.  It does not perform
    fuzzy search: every model marker present in the customer span must map to
    exactly one feed product whose brand is also written in that utterance.
    A duplicated series row or a missing model remains unresolved instead of
    selecting the nearest title.
    """

    materialized = tuple(products)
    normalized = _normalise(utterance)
    markers = tuple(
        dict.fromkeys(
            (
                match.group(0),
                _model_identity(match.group(0)),
            )
            for match in _MODEL_MARKER_RE.finditer(utterance)
        )
    )
    resolutions: list[NamedProductResolution] = []
    seen_skus: set[str] = set()
    for raw_marker, marker in markers:
        candidates: list[_CatalogProduct] = []
        for product in materialized:
            brand = _brand(product)
            product_name = _model_identity(product.name)
            if (
                brand
                and brand in normalized
                and marker
                and marker in product_name
            ):
                candidates.append(product)
        unique = tuple({item.sku: item for item in candidates}.values())
        if len(unique) == 1:
            if unique[0].sku in seen_skus:
                continue
            seen_skus.add(unique[0].sku)
            resolutions.append(
                NamedProductResolution(
                    status=NamedProductResolutionStatus.EXACT,
                    raw=raw_marker,
                    canonical_sku=unique[0].sku,
                    candidate_skus=(unique[0].sku,),
                    reason_code="strict_brand_model_catalogue_pair_match",
                )
            )
        elif len(unique) > 1:
            resolutions.append(
                NamedProductResolution(
                    status=NamedProductResolutionStatus.AMBIGUOUS,
                    raw=raw_marker,
                    candidate_skus=tuple(item.sku for item in unique),
                    reason_code="strict_brand_model_catalogue_pair_ambiguous",
                )
            )
        else:
            resolutions.append(
                NamedProductResolution(
                    status=NamedProductResolutionStatus.NONE,
                    raw=raw_marker,
                    reason_code="strict_brand_model_catalogue_pair_missing",
                )
            )
    return tuple(resolutions)
