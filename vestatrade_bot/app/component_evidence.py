"""Source-preserving evidence for components integrated into a product.

This is deliberately a tiny lexical evidence reader, not a catalogue search
or an engineering advisor.  In particular, an absent mention of a pump never
means that a boiler has no pump.  ``False`` is returned only for an explicit
statement that the component is not built in / is supplied separately.

The module is shared by Legacy and V2 so both paths apply the same safety
boundary to component questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.utils import normalize_text
from app.models import Product


_BUILTIN_PART_TARGETS: dict[str, str] = {
    "насос": r"(?:циркуляционн\w*\s+)?насос",
    "бак": r"(?:расширительн\w*\s+)?бак",
    "3-ходовой клапан": r"(?:трех|3)[- ]?ходов\w*\s+клапан",
    "манометр": r"манометр",
    "камера": r"камер\w*\s+сгоран",
    "бойлер": r"(?:накопительн\w*\s+)?бойлер",
    "группа безопасности": r"групп\w*\s+безопасн",
}


def builtin_part_state_from_text(text: str, part: str) -> bool | None:
    """Return an explicit built-in state without inverting negations."""

    normalized = normalize_text(text)
    canonical = normalize_text(part)
    target = _BUILTIN_PART_TARGETS.get(canonical)
    if not target:
        return None

    negative_patterns = (
        # A package-list phrase such as ``не входит в комплект поставки`` does
        # not disprove an assembled component inside the product.
        rf"(?:\bбез\b|"
        rf"\bне\s+встроен\w*\b|\bне\s+предусмотрен\w*\b|"
        rf"\bотсутств\w*\b)(?:\s+\w+){{0,5}}\s+{target}",
        rf"{target}(?:\s+\w+){{0,8}}\s+(?:\bнет\b|\bне\s+встроен\w*\b|"
        rf"\bне\s+предусмотрен\w*\b|\bотсутств\w*\b)",
        rf"{target}(?:[^.!?]{{0,140}})(?:приобрета\w*|поставля\w*)\s+отдельно",
        rf"(?:приобрета\w*|поставля\w*)\s+отдельно(?:[^.!?]{{0,100}}){target}",
    )
    if any(re.search(pattern, normalized) for pattern in negative_patterns):
        return False

    positive_patterns: dict[str, tuple[str, ...]] = {
        "насос": (
            r"встроен\w*\s+(?:циркуляционн\w*\s+)?насос",
            r"(?:циркуляционн\w*\s+)?насос[^.!?]{0,45}встроен",
        ),
        "бак": (
            r"встроенн\w*[^.!?]{0,100}(?:расширительн\w*\s+)?бак",
            r"(?:расширительн\w*\s+)?бак[^.!?]{0,45}встроен",
        ),
        "3-ходовой клапан": (
            r"встроенн\w*[^.!?]{0,35}(?:трех|3)[- ]?ходов\w*\s+клапан",
        ),
        "манометр": (r"встроенн\w*[^.!?]{0,45}манометр",),
        "камера": (r"закрыт\w*\s+камер\w*\s+сгоран",),
        "бойлер": (
            r"встроенн\w*\s+(?:накопительн\w*\s+)?бойлер",
            r"(?:накопительн\w*\s+)?бойлер[^.!?]{0,45}встроен",
        ),
        "группа безопасности": (
            r"встроенн\w*[^.!?]{0,45}групп\w*\s+безопасн",
            r"(?:полный\s+)?комплект\s+гидравлическ\w*\s+безопасн",
        ),
    }
    if any(
        re.search(pattern, normalized)
        for pattern in positive_patterns.get(canonical, ())
    ):
        return True
    return None


def _excerpt(text: str, part: str) -> str:
    """Return a bounded sentence containing the component rather than a blob."""

    canonical = normalize_text(part)
    target = _BUILTIN_PART_TARGETS.get(canonical)
    if not target:
        return text[:500]
    match = re.search(rf"[^.!?]{{0,180}}{target}[^.!?]{{0,180}}[.!?]?", text, re.I)
    return (match.group(0) if match else text)[:500].strip()


@dataclass(frozen=True)
class BuiltinPartEvidence:
    """One source-bound integrated-component state.

    ``source_kind`` is ``catalogue`` for current feed fields and ``passport``
    for a document attached to exactly this product.
    """

    state: bool | None
    source_kind: str | None = None
    document: str | None = None
    section: str | None = None
    excerpt: str | None = None
    source_conflict: bool = False


def builtin_part_evidence(product: Product, part: str) -> BuiltinPartEvidence:
    """Resolve a component state from this product's card and attached docs.

    Explicit disagreement is reported as a conflict.  A document is preferred
    as the displayed source when all explicit sources agree; card-only evidence
    remains valid when no passport is attached.
    """

    card_text = " ".join(
        (
            product.name,
            product.description or "",
            " ".join(
                f"{key} {value}"
                for key, value in product.attributes_normalized.items()
            ),
        )
    )
    sources: list[BuiltinPartEvidence] = []
    card_state = builtin_part_state_from_text(card_text, part)
    if card_state is not None:
        sources.append(
            BuiltinPartEvidence(
                state=card_state,
                source_kind="catalogue",
                document="catalogue",
                section="name/description/attributes",
                excerpt=_excerpt(card_text, part),
            )
        )
    if product.documents:
        for document in product.documents:
            state = builtin_part_state_from_text(document.text, part)
            if state is not None:
                sources.append(
                    BuiltinPartEvidence(
                        state=state,
                        source_kind="passport",
                        document=document.filename,
                        section=(
                            "комплект/конструкция"
                            if "комплект" in normalize_text(document.text)
                            else None
                        ),
                        excerpt=_excerpt(document.text, part),
                    )
                )
    elif product.docs_text:
        state = builtin_part_state_from_text(product.docs_text, part)
        if state is not None:
            sources.append(
                BuiltinPartEvidence(
                    state=state,
                    source_kind="passport",
                    document="attached_product_document",
                    excerpt=_excerpt(product.docs_text, part),
                )
            )

    states = {item.state for item in sources if item.state is not None}
    if len(states) != 1:
        return BuiltinPartEvidence(state=None, source_conflict=bool(states))
    # Prefer the bound technical document over duplicated card copy.
    return next(
        item for item in sources if item.source_kind == "passport"
    ) if any(item.source_kind == "passport" for item in sources) else sources[0]


def builtin_part_state(product: Product, part: str) -> bool | None:
    """Legacy-compatible scalar facade over :func:`builtin_part_evidence`."""

    return builtin_part_evidence(product, part).state
