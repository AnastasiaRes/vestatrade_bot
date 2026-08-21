from __future__ import annotations

import html
import re
from dataclasses import dataclass

from app.models import Product

from .utils import normalize_text


THREAD_PAIRS = {"ff", "fm", "mm"}

_FEMALE = r"(?:вр|вн|внутренн\w*|мам\w*)\.?"
_MALE = r"(?:нр|нар|наружн\w*|пап\w*)\.?"


@dataclass(frozen=True)
class ThreadFacts:
    """Grounded thread facts for one catalogue row.

    ``pair`` is set only when two threaded ends are explicitly evidenced.
    ``genders`` also represents a single threaded end of a combined fitting
    (for example PPR socket x 1/2" male).  Keeping those facts separate avoids
    treating the feed value ``Наружная`` as an НР/НР product.
    """

    pair: str | None
    genders: frozenset[str]


@dataclass(frozen=True)
class InchConnectionFacts:
    """Conservative inch-size topology for one catalogue row.

    ``sizes`` contains every distinct inch size evidenced by identity fields.
    A product with two equal ports (``1/2 x 1/2``) is therefore uniform, while
    a reducing/appliance valve (``1/2 x 3/4``) is explicitly mixed.  Free-form
    descriptions are intentionally excluded because they often mention sibling
    sizes from the same series.
    """

    sizes: frozenset[str]

    @property
    def is_mixed(self) -> bool:
        return len(self.sizes) > 1


_INCH_FRACTION = r"(?:[1-7]\s*/\s*8|[1-3]\s*/\s*4|1\s*/\s*2)"
_INCH_MIXED = rf"(?:[1-4]\s+{_INCH_FRACTION})"
_QUOTED_INCH_RE = re.compile(
    rf"(?<![\d/])({_INCH_MIXED}|{_INCH_FRACTION}|[1-4])\s*"
    r'(?:"|″|дюйм(?:а|ов)?)',
    re.IGNORECASE,
)


def _fully_unescape(value: object) -> str:
    """Decode the one- or two-layer HTML escaping found in the XML feed."""
    text = str(value)
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def normalize_inch_size(value: object) -> str | None:
    """Normalize one nominal inch size to the slot representation.

    Existing dialogue slots compact mixed numbers (``1 1/2`` -> ``11/2``), so
    the shared matcher keeps that backwards-compatible representation.
    """
    text = _fully_unescape(value).lower().replace("ё", "е").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^g\s*", "", text)
    text = text.strip(' "″')

    mixed = re.fullmatch(rf"([1-4])(?:\s+|\s*[\"″]\s*)({_INCH_FRACTION})", text)
    if mixed:
        return re.sub(r"\s+", "", "".join(mixed.groups()))
    # Router/engineering slots historically store a compact mixed number.
    compact_mixed = re.fullmatch(r"([1-4])([1-7]/8|[1-3]/4|1/2)", text)
    if compact_mixed:
        return "".join(compact_mixed.groups())
    if re.fullmatch(_INCH_FRACTION, text):
        return re.sub(r"\s+", "", text)
    if re.fullmatch(r"[1-4]", text):
        return text
    return None


def _quoted_inch_sizes(value: object) -> set[str]:
    raw = _fully_unescape(value).lower().replace("ё", "е")
    # Several catalogue names encode 1 1/2" as ``1"1/2``.  Convert that
    # malformed-but-stable feed notation before tokenisation, otherwise it is
    # misread as two sizes: ``1`` and ``1/2``.
    raw = re.sub(
        rf"(?<!\d)([1-4])\s*[\"″]\s*({_INCH_FRACTION})(?!\s*[\"″])",
        r'\1 \2"',
        raw,
    )
    result: set[str] = set()
    for match in _QUOTED_INCH_RE.finditer(raw):
        if normalized := normalize_inch_size(match.group(1)):
            result.add(normalized)
    return result


def product_inch_connection_facts(product: Product) -> InchConnectionFacts:
    """Extract all distinct inch connection sizes from trusted identity data."""
    sizes: set[str] = set()
    for key, value in product.attributes_normalized.items():
        key_norm = normalize_text(str(key))
        if "дюйм" not in key_norm and "резьб" not in key_norm:
            continue
        if normalized := normalize_inch_size(value):
            sizes.add(normalized)
        else:
            sizes.update(_quoted_inch_sizes(value))

    # The title/full name is authoritative identity evidence and is the only
    # size source for sparse rows such as VT.392.N.05.
    sizes.update(_quoted_inch_sizes(product.name))
    for key, value in product.attributes_normalized.items():
        if "полное наименование" in normalize_text(str(key)):
            sizes.update(_quoted_inch_sizes(value))
    return InchConnectionFacts(sizes=frozenset(sizes))


def product_inch_sizes(product: Product) -> set[str]:
    """Backward-friendly set view used by reference-slot extraction."""
    return set(product_inch_connection_facts(product).sizes)


def single_inch_size_constraint_matches(product: Product, requested: object) -> bool:
    """Match a generic single-size request without accepting reducing SKUs.

    ``size_inch=3/4`` means a uniform 3/4 product.  It must not silently select
    a 1/2 x 3/4 appliance valve merely because one of its ports is 3/4.  A
    product with repeated equal ports remains a valid uniform match.
    """
    expected = normalize_inch_size(requested)
    if expected is None:
        return False
    return product_inch_connection_facts(product).sizes == frozenset({expected})


def normalize_thread_pair(value: object) -> str | None:
    """Normalize user/feed notation to ``ff`` / ``fm`` / ``mm``."""
    text = normalize_text(str(value))
    if not text:
        return None

    tokens = set(text.split())
    if "ff" in tokens:
        return "ff"
    if tokens.intersection({"fm", "mf"}):
        return "fm"
    if "mm" in tokens:
        return "mm"

    separator = r"\s*(?:[-/xх×]|\s+)\s*"
    if re.search(rf"(?<!\w){_FEMALE}{separator}{_FEMALE}(?!\w)", text):
        return "ff"
    if re.search(
        rf"(?<!\w)(?:{_FEMALE}{separator}{_MALE}|"
        rf"{_MALE}{separator}{_FEMALE})(?!\w)",
        text,
    ):
        return "fm"
    if re.search(rf"(?<!\w){_MALE}{separator}{_MALE}(?!\w)", text):
        return "mm"
    return None


def normalize_thread_gender(value: object) -> str | None:
    """Normalize a one-ended gender constraint to ``female`` or ``male``."""
    text = normalize_text(str(value))
    if not text:
        return None
    if text in {"female", "f", "вр", "вн", "внутренняя", "мама"}:
        return "female"
    if text in {"male", "m", "нр", "нар", "наружная", "папа"}:
        return "male"
    if re.fullmatch(r"(?:с\s+)?внутренн\w*(?:\s+резьб\w*)?", text):
        return "female"
    if re.fullmatch(r"(?:с\s+)?наружн\w*(?:\s+резьб\w*)?", text):
        return "male"
    return None


def _single_genders(text: str) -> set[str]:
    """Extract one-ended evidence without inferring a two-ended pair."""
    normalized = normalize_text(text)
    result: set[str] = set()
    if normalize_thread_gender(normalized) == "female" or re.search(
        r"(?<!\w)(?:вр|вн)\.?(?!\w)", normalized
    ):
        result.add("female")
    if normalize_thread_gender(normalized) == "male" or re.search(
        r"(?<!\w)(?:нр|нар)\.?(?!\w)", normalized
    ):
        result.add("male")
    return result


def product_thread_facts(product: Product) -> ThreadFacts:
    """Return conservative thread facts from identity and feed attributes.

    Marketing descriptions are deliberately excluded: they frequently discuss
    sibling products or installation counterparts.  Conflicting pair evidence
    becomes unknown so an explicit constraint fails closed.
    """
    thread_attributes = [
        str(value)
        for key, value in product.attributes_normalized.items()
        if "резьб" in normalize_text(str(key))
        and not any(
            marker in normalize_text(str(key))
            for marker in ["диаметр", "размер", "дюйм", "стандарт"]
        )
    ]
    normalized_name = normalize_text(product.name)
    # In polymer fitting names ``вн/нар`` may describe socket/spigot geometry,
    # not a metal thread.  Read a pair from the name only when the row contains
    # independent thread evidence or is itself threaded shut-off equipment.
    name_has_thread_evidence = bool(
        thread_attributes
        or "резьб" in normalized_name
        or re.search(r"\b(?:кран|клапан|вентиль)\b", normalized_name)
    )
    sources = [*thread_attributes]
    if name_has_thread_evidence:
        sources.append(product.name)
    pairs = {
        pair
        for source in sources
        if (pair := normalize_thread_pair(source)) is not None
    }
    pair = pairs.pop() if len(pairs) == 1 else None

    genders: set[str] = set()
    if pair:
        genders.update("female" if code == "f" else "male" for code in pair)
    else:
        for source in thread_attributes:
            genders.update(_single_genders(source))
        # Sparse combined fittings often state the only threaded end in the
        # name (``20x1/2\" нар.``) and omit ``тип резьбы``.
        if not genders and name_has_thread_evidence:
            genders.update(_single_genders(product.name))
    return ThreadFacts(pair=pair, genders=frozenset(genders))


def product_thread_pair(product: Product) -> str | None:
    return product_thread_facts(product).pair


def thread_constraint_matches(
    product: Product,
    *,
    thread_type: object | None = None,
    thread_gender: object | None = None,
) -> bool:
    """Fail-closed match for explicitly requested thread facts."""
    facts = product_thread_facts(product)
    if thread_type is not None:
        expected_pair = normalize_thread_pair(thread_type)
        if expected_pair is None or facts.pair != expected_pair:
            return False
    if thread_gender is not None:
        expected_gender = normalize_thread_gender(thread_gender)
        if expected_gender is None or expected_gender not in facts.genders:
            return False
    return True
