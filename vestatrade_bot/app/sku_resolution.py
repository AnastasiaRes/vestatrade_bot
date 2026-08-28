"""Deterministic, fail-closed catalogue SKU resolution.

Partial SKU lookup is an identity operation, not fuzzy retrieval.  A customer
may omit trailing vendor segments (``VT.1500`` for ``VT.1500.0.0``), but every
segment they did provide must match a complete catalogue segment.  The helper
therefore never performs raw substring or character-prefix matching.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar


class HasSku(Protocol):
    sku: str


SkuItem = TypeVar("SkuItem", bound=HasSku)


class SkuResolutionStatus(str, Enum):
    EXACT = "exact"
    UNIQUE_PREFIX = "unique_prefix"
    AMBIGUOUS_PREFIX = "ambiguous_prefix"
    NONE = "none"


@dataclass(frozen=True)
class SkuResolution(Generic[SkuItem]):
    status: SkuResolutionStatus
    query: str
    candidates: tuple[SkuItem, ...] = ()
    canonical_sku: str | None = None
    reason_code: str | None = None


_SKU_GROUP_RE = re.compile(r"[a-zа-я]+|\d+", flags=re.IGNORECASE)

# Customers often read Latin vendor prefixes by their Russian letter names.
# The mapping deliberately applies only while comparing an alphabetic SKU
# segment and cannot turn ordinary prose into a SKU on its own.
_SPOKEN_CYRILLIC = {
    "a": "а",
    "b": "б",
    "c": "с",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "x": "х",
    "y": "ы",
    "z": "з",
}


def sku_segments(value: object) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    normalized = normalized.casefold().replace("ё", "е")
    return tuple(_SKU_GROUP_RE.findall(normalized))


def _segments_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left.isalpha() or not right.isalpha() or len(left) != len(right):
        return False
    if re.fullmatch(r"[a-z]+", right):
        return all(
            actual == expected
            or _SPOKEN_CYRILLIC.get(expected) == actual
            for actual, expected in zip(left, right)
        )
    if re.fullmatch(r"[a-z]+", left):
        return all(
            actual == expected
            or _SPOKEN_CYRILLIC.get(actual) == expected
            for actual, expected in zip(left, right)
        )
    return False


def _is_safe_partial_identity(groups: tuple[str, ...]) -> bool:
    """Keep series-prefix lookup narrow enough to remain an identity check."""

    compact_length = sum(len(group) for group in groups)
    return (
        len(groups) >= 2
        and compact_length >= 5
        and any(group.isalpha() for group in groups)
        and any(group.isdigit() for group in groups)
    )


def resolve_catalog_sku(
    query: object,
    items: Iterable[SkuItem],
) -> SkuResolution[SkuItem]:
    """Resolve one exact or safely abbreviated SKU against one catalogue.

    Exact identities accept separator differences.  Partial identities accept
    only omitted *trailing* segments, only when the supplied prefix contains
    both a vendor segment and a numeric segment, and only for structured full
    identities of at least four segments.  The latter preserves the exact-only
    boundary for a simple ``ABC-12345-X`` article.  Duplicate exact identities
    and non-unique prefixes fail closed as ambiguity.
    """

    query_text = str(query or "").strip()
    groups = sku_segments(query_text)
    if not groups:
        return SkuResolution(
            status=SkuResolutionStatus.NONE,
            query=query_text,
            reason_code="empty_sku",
        )

    catalogue = tuple(items)
    exact: list[SkuItem] = []
    prefixes: list[SkuItem] = []
    allow_partial = _is_safe_partial_identity(groups)
    for item in catalogue:
        candidate_groups = sku_segments(item.sku)
        if not candidate_groups or len(groups) > len(candidate_groups):
            continue
        if not all(
            _segments_equal(supplied, candidate)
            for supplied, candidate in zip(groups, candidate_groups)
        ):
            continue
        if len(groups) == len(candidate_groups):
            exact.append(item)
        elif allow_partial and len(candidate_groups) >= 4:
            prefixes.append(item)

    exact_candidates = tuple(
        sorted(exact, key=lambda item: str(item.sku).casefold())
    )
    if len(exact_candidates) == 1:
        candidate = exact_candidates[0]
        return SkuResolution(
            status=SkuResolutionStatus.EXACT,
            query=query_text,
            candidates=(candidate,),
            canonical_sku=str(candidate.sku),
            reason_code="full_segment_identity_match",
        )
    if len(exact_candidates) > 1:
        return SkuResolution(
            status=SkuResolutionStatus.AMBIGUOUS_PREFIX,
            query=query_text,
            candidates=exact_candidates,
            reason_code="duplicate_exact_identity",
        )

    prefix_candidates = tuple(
        sorted(prefixes, key=lambda item: str(item.sku).casefold())
    )
    if len(prefix_candidates) == 1:
        candidate = prefix_candidates[0]
        return SkuResolution(
            status=SkuResolutionStatus.UNIQUE_PREFIX,
            query=query_text,
            candidates=(candidate,),
            canonical_sku=str(candidate.sku),
            reason_code="unique_trailing_segment_prefix",
        )
    if len(prefix_candidates) > 1:
        return SkuResolution(
            status=SkuResolutionStatus.AMBIGUOUS_PREFIX,
            query=query_text,
            candidates=prefix_candidates,
            reason_code="multiple_trailing_segment_prefix_matches",
        )
    return SkuResolution(
        status=SkuResolutionStatus.NONE,
        query=query_text,
        reason_code=(
            "unsafe_partial_identity_shape"
            if any(len(groups) < len(sku_segments(item.sku)) for item in catalogue)
            and not allow_partial
            else "catalogue_identity_not_found"
        ),
    )
