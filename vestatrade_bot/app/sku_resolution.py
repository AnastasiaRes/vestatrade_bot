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


@dataclass(frozen=True)
class CatalogSkuAnchor(Generic[SkuItem]):
    """A catalogue-proven SKU mention from one customer turn.

    Extraction alone is deliberately not identity proof.  This result is
    emitted only after the token has been resolved against the supplied,
    frozen catalogue view.  It gives semantic interpretation a small,
    deterministic input without letting that layer invent a product.
    """

    text: str
    start: int
    end: int
    resolution: SkuResolution[SkuItem]
    match_kind: str

    @property
    def canonical_sku(self) -> str | None:
        return self.resolution.canonical_sku

    @property
    def candidate_skus(self) -> tuple[str, ...]:
        return tuple(str(item.sku) for item in self.resolution.candidates)


_SKU_GROUP_RE = re.compile(r"[a-zа-я]+|\d+", flags=re.IGNORECASE)

# A catalogue article can be a structured vendor identifier (``VT.1500``) or
# a pure numeric article (``2202210``).  The latter must be long enough that a
# measurement such as ``9 кВт`` or ``150 м²`` cannot accidentally become a
# product identity.  This helper only extracts *candidates*; callers must
# still pass each candidate through ``resolve_catalog_sku`` before treating it
# as a product reference.
_EXPLICIT_STRUCTURED_SKU_RE = re.compile(
    r"(?<![a-zа-я0-9])"
    r"(?:[a-zа-я]{1,8}[.\-]\d+(?:[.\-][a-zа-я0-9]+){0,5})"
    r"(?![a-zа-я0-9])",
    re.IGNORECASE,
)
_EXPLICIT_NUMERIC_SKU_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")

# Five-digit articles and slash-only articles exist in the catalogue, but
# neither form is sufficiently distinctive to treat as a product reference in
# arbitrary prose.  They are candidates only for the context-bound resolver
# below and are never added to the general extractor above.
_CONTEXTUAL_NUMERIC_SKU_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")
_CONTEXTUAL_SLASH_SKU_RE = re.compile(r"(?<![\w/])\d+(?:/\d+){2,}(?![\w/])")
_SKU_IDENTITY_PREFIX_RE = re.compile(
    r"(?iu)(?:\b(?:артикул|sku|код(?:\s+товара)?|товар|товара|позиция|позиции|модель)\b"
    r"\s*(?:№|#|:)?\s*)$"
)
_NUMERIC_OR_SLASH_UNIT_SUFFIX_RE = re.compile(
    r"(?iu)^\s*(?:₽|руб(?:\.|\w*)|шт(?:\.|\w*)|ед(?:\.|\w*)|"
    r"мм|mm|см|cm|м(?![а-яё])|m(?![a-z])|квт|kw|вт|w|бар|bar|%)\b"
)
_NUMERIC_OR_SLASH_VALUE_PREFIX_RE = re.compile(
    r"(?iu)(?:\b(?:за|бюджет|цена|стоимость)\s*)$"
)
_LEADING_OFFER_FACT_QUESTION_RE = re.compile(
    r"(?iu)^\s*(?:есть\s+ли|есть\s+в\s+наличии|в\s+наличии|"
    r"сколько\s+стоит|какая\s+цена|цена|ссылк\w*)\b"
)

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


def extract_explicit_sku_tokens(text: object) -> tuple[str, ...]:
    """Return literal SKU-shaped spans without resolving them.

    Keeping extraction beside the canonical resolver prevents the semantic,
    selection and ProductFact paths from disagreeing about whether a numeric
    article is even eligible for exact identity lookup.  A returned token is
    never proof that a product exists.
    """

    source = str(text or "")
    tokens = [match.group(0).strip() for match in _EXPLICIT_STRUCTURED_SKU_RE.finditer(source)]
    tokens.extend(match.group(0) for match in _EXPLICIT_NUMERIC_SKU_RE.finditer(source))
    return tuple(dict.fromkeys(token for token in tokens if token))


def _is_context_bound_identity_span(
    source: str,
    *,
    start: int,
    end: int,
) -> bool:
    """Return whether a weak numeric/slash token is locally a SKU mention.

    A five-digit number is often an amount, a count or a measurement.  A
    short slash fragment is even more ambiguous (``1/2`` and ``25/6`` are
    normal engineering notation).  We allow such a span only if it is the
    whole turn or follows an explicit identity label, and reject a nearby unit
    or amount marker even when a label is present.
    """

    before = source[max(0, start - 80):start]
    after = source[end:min(len(source), end + 32)]
    remaining = (source[:start] + source[end:]).strip(" \t\r\n.,!?;:—–-()[]{}")
    if not remaining:
        return True
    if _has_numeric_or_slash_value_context(before, after):
        return False
    # A slash-only feed SKU may be the grammatical subject of a direct offer
    # question (``68/2/8 есть в наличии?``).  This is still bounded: the token
    # must be at the start of the turn, must have at least two slashes (the
    # caller's candidate rule) and may only be followed by an offer-fact
    # phrase.  Measurements such as ``25/6`` never enter this branch.
    if not source[:start].strip() and _LEADING_OFFER_FACT_QUESTION_RE.match(after):
        return True
    return _SKU_IDENTITY_PREFIX_RE.search(before) is not None


def _has_numeric_or_slash_value_context(before: str, after: str) -> bool:
    return bool(
        _NUMERIC_OR_SLASH_UNIT_SUFFIX_RE.match(after)
        or _NUMERIC_OR_SLASH_VALUE_PREFIX_RE.search(before)
    )


def resolve_catalog_sku_anchors(
    text: object,
    items: Iterable[SkuItem],
) -> tuple[CatalogSkuAnchor[SkuItem], ...]:
    """Resolve safe SKU mentions against the catalogue used for this turn.

    Structured identifiers retain the established exact/unique-partial
    behaviour.  Five-or-more-digit and slash-only tokens are exact-only and
    require an identity context (or a turn consisting solely of the token).
    No unresolved raw token is returned, so callers cannot accidentally turn
    a number into a product before catalogue proof exists.
    """

    source = str(text or "")
    catalogue = tuple(items)
    candidates: list[tuple[int, int, str, str]] = []
    candidates.extend(
        (match.start(), match.end(), match.group(0).strip(), "structured")
        for match in _EXPLICIT_STRUCTURED_SKU_RE.finditer(source)
    )
    candidates.extend(
        (match.start(), match.end(), match.group(0), "numeric_long")
        for match in _EXPLICIT_NUMERIC_SKU_RE.finditer(source)
    )
    candidates.extend(
        (match.start(), match.end(), match.group(0), "numeric")
        for match in _CONTEXTUAL_NUMERIC_SKU_RE.finditer(source)
    )
    candidates.extend(
        (match.start(), match.end(), match.group(0), "slash")
        for match in _CONTEXTUAL_SLASH_SKU_RE.finditer(source)
    )

    # A slash candidate can contain smaller numeric spans; retain only the
    # longest span at any position.  This is essential for ``68/2/8``.
    ordered = sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0])))
    seen_spans: list[tuple[int, int]] = []
    anchors: list[CatalogSkuAnchor[SkuItem]] = []
    for start, end, token, match_kind in ordered:
        if any(start >= left and end <= right for left, right in seen_spans):
            continue
        if match_kind in {"numeric", "slash"}:
            if not _is_context_bound_identity_span(source, start=start, end=end):
                continue
        elif match_kind == "numeric_long":
            before = source[max(0, start - 80):start]
            after = source[end:min(len(source), end + 32)]
            if _has_numeric_or_slash_value_context(before, after):
                continue
        resolution = resolve_catalog_sku(token, catalogue)
        if resolution.status not in {
            SkuResolutionStatus.EXACT,
            SkuResolutionStatus.UNIQUE_PREFIX,
            SkuResolutionStatus.AMBIGUOUS_PREFIX,
        }:
            continue
        # Numeric/slash forms must be exact identities.  They are never
        # allowed to act as a short series prefix.
        if match_kind in {"numeric", "slash"} and resolution.status is not SkuResolutionStatus.EXACT:
            continue
        anchors.append(
            CatalogSkuAnchor(
                text=token,
                start=start,
                end=end,
                resolution=resolution,
                match_kind=match_kind,
            )
        )
        seen_spans.append((start, end))
    return tuple(anchors)


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
