from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.models import Product

from .utils import normalize_text


SkuSuggestionStatus = Literal["unique", "ambiguous", "none"]


@dataclass(frozen=True)
class SkuSuggestionResult:
    """A fail-closed resolution for an explicitly marked catalogue identity.

    A typo is not necessarily a single-product typo.  Several catalogue SKUs
    can have the same minimum edit distance (for example ``151001`` through
    ``151009`` for ``15100Z``).  Callers must ask the customer to disambiguate
    that set instead of silently choosing whichever product happened to be
    loaded first.
    """

    status: SkuSuggestionStatus
    candidates: tuple[Product, ...] = ()
    distance: int | None = None


def sku_edit_key(value: object) -> str:
    """Return a punctuation-insensitive key used only for explicit SKU repair."""

    return re.sub(r"[^a-zа-я0-9]", "", normalize_text(str(value)))


def _bounded_levenshtein(left: str, right: str, limit: int) -> int:
    """Compute edit distance, stopping once a row cannot get below ``limit``."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        row_min = row_index
        for column_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1] + (left_char != right_char),
                )
            )
            row_min = min(row_min, current[-1])
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def resolve_sku_suggestion(
    needle: object,
    products: Iterable[Product],
) -> SkuSuggestionResult:
    """Resolve a close explicit SKU as unique, ambiguous, or absent.

    The caller must already have explicit evidence that the user supplied an
    article/SKU.  This helper intentionally does not inspect ordinary prose.
    Exact identity lookup stays exact.  Only candidates at the shared minimum
    edit distance are returned, so a more distant item can never make an
    ambiguous nearest set look unique.
    """

    key = sku_edit_key(needle)
    if len(key) < 5 or sum(char.isdigit() for char in key) < 3:
        return SkuSuggestionResult(status="none")
    limit = 1 if len(key) <= 7 else 2
    best_distance = limit + 1
    best: dict[str, Product] = {}
    for product in products:
        candidate_key = sku_edit_key(product.sku)
        if not candidate_key or candidate_key == key:
            continue
        distance = _bounded_levenshtein(key, candidate_key, limit)
        if distance > limit:
            continue
        if distance < best_distance:
            best_distance = distance
            best = {candidate_key: product}
        elif distance == best_distance:
            best.setdefault(candidate_key, product)
    if not best:
        return SkuSuggestionResult(status="none")
    candidates = tuple(best[key] for key in sorted(best))
    return SkuSuggestionResult(
        status="unique" if len(candidates) == 1 else "ambiguous",
        candidates=candidates,
        distance=best_distance,
    )


def suggest_unique_sku(needle: object, products: Iterable[Product]) -> Product | None:
    """Backward-compatible unique-only view of :func:`resolve_sku_suggestion`."""

    result = resolve_sku_suggestion(needle, products)
    if result.status != "unique":
        return None
    return result.candidates[0]
