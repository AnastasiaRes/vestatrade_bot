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
        _normalise(match.group(0)).replace(" ", "")
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
        product_name = _normalise(product.name).replace(" ", "")
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
