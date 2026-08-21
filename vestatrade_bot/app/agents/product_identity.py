from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Product

from .utils import normalize_text


VALVE_PRIMARY_KINDS = frozenset(
    {
        "valve",
        "ball_valve",
        "check_valve",
        "drain_valve",
        "thermostatic_valve",
    }
)
FITTING_PRIMARY_KINDS = frozenset(
    {"fitting", "tee", "elbow", "coupling", "adapter"}
)
FILTER_PRIMARY_KINDS = frozenset({"filter", "filter_system"})


@dataclass(frozen=True)
class ProductIdentityFacts:
    """The item being sold, kept separate from components mentioned in its title.

    Catalogue titles commonly describe compound products: a PPR *tee with a
    ball valve*, a filter supplied with a drinking tap, or a boiler containing
    a pump.  A keyword bag cannot distinguish those components from the head
    product.  ``primary_kind`` is therefore the title's head object, while
    ``embedded_components`` contains later component mentions.

    The feed's ``тип товара`` is supporting evidence only.  Some rows label a
    compound product by its built-in component, so a structured value may
    refine a generic head (``кран`` -> ``ball_valve``) but may not replace a
    different explicit head (``tee`` -> ``ball_valve``).
    """

    primary_kind: str | None
    embedded_components: frozenset[str] = frozenset()
    evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @property
    def primary_category(self) -> str | None:
        if self.primary_kind in VALVE_PRIMARY_KINDS:
            return "valves"
        if self.primary_kind in FITTING_PRIMARY_KINDS:
            return "fittings"
        if self.primary_kind in FILTER_PRIMARY_KINDS:
            return "filters"
        if self.primary_kind == "thermostatic_head":
            return "radiator_fittings"
        if self.primary_kind == "pipe":
            return "pipes"
        return None


@dataclass(frozen=True)
class _IdentityMention:
    start: int
    end: int
    kind: str
    text: str


# Phrase-level heads must be considered before their nested noun.  The first
# non-overlapping entity in a title is its grammatical/product head; later
# entities are components, compatibility targets, or package contents.
_ENTITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "filter_system",
        re.compile(r"\bкомплект\w*\s+фильтр\w*\b|\bсистем\w*\s+фильтрац\w*\b"),
    ),
    (
        "valve_set",
        re.compile(r"\bкомплект\w*(?:\s+\w+){0,3}\s+кран\w*\b"),
    ),
    (
        "safety_group",
        re.compile(r"\bгрупп\w*\s+безопасност\w*\b"),
    ),
    (
        "thermostatic_head",
        re.compile(
            r"\bтермоголов\w*\b|"
            r"\bтермостатическ\w*\s+головк\w*\b|"
            r"\bголовк\w*\s+термостатическ\w*\b"
        ),
    ),
    ("tee", re.compile(r"\bтройник\w*\b")),
    ("elbow", re.compile(r"\b(?:угольник|уголок|отвод)\w*\b")),
    ("coupling", re.compile(r"\bмуфт\w*\b")),
    (
        "adapter",
        re.compile(r"\b(?:переходник|переход|штуцер|ниппель|полусгон)\w*\b"),
    ),
    ("fitting", re.compile(r"\bфитинг\w*\b")),
    (
        "filter",
        re.compile(
            r"\b(?:фильтр|предфильтр|постфильтр|картридж|мембрана|корпус)\w*\b"
        ),
    ),
    ("pipe", re.compile(r"\bтруб\w*\b")),
    (
        "valve",
        re.compile(
            r"\b(?:"
            r"кран(?:ы|а|у|ом|е|ов|ами|ах)?|"
            r"клапан(?:ы|а|у|ом|е|ов|ами|ах)?|"
            r"вентил(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)"
            r")\b"
        ),
    ),
)


def _refine_valve_kind(text: str, fallback: str = "valve") -> str:
    if "термостат" in text and re.search(r"\b(?:клапан|вентиль)\w*\b", text):
        return "thermostatic_valve"
    if "обратн" in text and "клапан" in text:
        return "check_valve"
    if any(marker in text for marker in ["дренаж", "сливн"]):
        return "drain_valve"
    if "шаров" in text and "кран" in text:
        return "ball_valve"
    return fallback


def _identity_mentions(text: str) -> list[_IdentityMention]:
    candidates: list[tuple[int, int, int, str, str]] = []
    for priority, (kind, pattern) in enumerate(_ENTITY_PATTERNS):
        for match in pattern.finditer(text):
            candidates.append(
                (match.start(), -(match.end() - match.start()), priority, kind, match.group())
            )
    candidates.sort()

    mentions: list[_IdentityMention] = []
    occupied: list[tuple[int, int]] = []
    for start, negative_length, _, kind, matched_text in candidates:
        end = start - negative_length
        if any(other_start <= start and end <= other_end for other_start, other_end in occupied):
            continue
        if kind == "valve":
            # Include nearby adjectives on both sides (``шаровой кран`` and
            # ``кран шаровой``), but not an unrelated earlier/later component.
            local = text[max(0, start - 28) : min(len(text), end + 34)]
            kind = _refine_valve_kind(local)
        mentions.append(_IdentityMention(start, end, kind, matched_text))
        occupied.append((start, end))
    mentions.sort(key=lambda item: item.start)
    return mentions


def _mention_is_relational(text: str, start: int) -> bool:
    """Whether a noun is introduced as a component/target, not the sold item."""
    prefix = text[:start]
    if prefix.count("(") > prefix.count(")"):
        return True
    tail = prefix[-90:]
    if re.search(
        r"(?:^|\s)(?:с|со|без|для|под|к|и|в\s+комплекте)"
        r"(?:\s+[a-zа-я0-9./+\-]+){0,5}\s*$",
        tail,
    ):
        return True
    # Genitive component names often omit a preposition: ``рукоятка шарового
    # крана``, ``привод клапана``.  A preceding concrete head noun is stronger
    # evidence than the later component token.
    return bool(
        re.search(
            r"\b(?:комплект|рукоятк|электропривод|сервопривод|привод|"
            r"коллектор|групп|стрелк|шланг|соединени|регулятор|манометр|"
            r"сифон|корпус|насос)\w*\b",
            prefix,
        )
    )


def _kind_from_type_attribute(product: Product) -> str | None:
    values = [
        normalize_text(str(value))
        for key, value in product.attributes_normalized.items()
        if "тип товара" in normalize_text(str(key))
    ]
    kinds: set[str] = set()
    for value in values:
        mentions = _identity_mentions(value)
        if mentions:
            kinds.add(mentions[0].kind)
    return kinds.pop() if len(kinds) == 1 else None


def _compatible_refinement(primary: str, structured: str) -> str | None:
    if primary == structured:
        return primary
    if primary == "valve" and structured in VALVE_PRIMARY_KINDS:
        return structured
    if primary == "fitting" and structured in FITTING_PRIMARY_KINDS:
        return structured
    if primary == "filter" and structured == "filter_system":
        return structured
    return None


def _path_fallback_kind(product: Product) -> str | None:
    path = normalize_text(product.category_path)
    if "фитинг" in path:
        return "fitting"
    return None


def product_identity_facts(product: Product) -> ProductIdentityFacts:
    """Return conservative primary/embedded identity facts for one feed row."""

    title = normalize_text(product.name)
    mentions = _identity_mentions(title)
    primary_mention = next(
        (
            mention
            for mention in mentions
            if not _mention_is_relational(title, mention.start)
        ),
        None,
    )
    primary = primary_mention.kind if primary_mention else None
    evidence: list[str] = [f"title:{primary}"] if primary else []
    embedded = {
        mention.kind
        for mention in mentions
        if mention is not primary_mention and mention.kind != primary
    }

    structured = _kind_from_type_attribute(product)
    conflicts: list[str] = []
    if primary is None and structured is not None:
        primary = structured
        evidence.append(f"type:{structured}")
    elif primary is not None and structured is not None:
        refined = _compatible_refinement(primary, structured)
        if refined is not None:
            primary = refined
            evidence.append(f"type:{structured}")
        else:
            # Keep the explicit title head.  The conflicting structured value
            # often names an integrated component rather than the sold item.
            conflicts.append(f"title:{primary}!=type:{structured}")

    if primary is None:
        primary = _path_fallback_kind(product)
        if primary:
            evidence.append(f"path:{primary}")

    return ProductIdentityFacts(
        primary_kind=primary,
        embedded_components=frozenset(embedded),
        evidence=tuple(evidence),
        conflicts=tuple(conflicts),
    )
