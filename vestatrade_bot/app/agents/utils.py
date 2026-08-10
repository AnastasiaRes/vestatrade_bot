from __future__ import annotations

import html
import re
import unicodedata
from typing import Any


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(unicodedata.normalize("NFKC", text)).lower().replace("ё", "е")
    # Общепринятые инженерные сокращения должны переживать точки, пробелы и
    # дефисы: «Г.В.С.», «Г В С» и «ГВС» означают одно и то же.
    text = re.sub(r"(?<![а-яa-z])г\s*[./-]?\s*в\s*[./-]?\s*с(?![а-яa-z])", "гвс", text)
    text = re.sub(r"(?<![а-яa-z])х\s*[./-]?\s*в\s*[./-]?\s*с(?![а-яa-z])", "хвс", text)
    # Покупатели могут называть канализацию «канашкой». Приводим разговорное
    # слово и его падежные формы к каноническому термину до классификации,
    # извлечения слотов и поиска по ассортименту.
    text = re.sub(r"\bканашк(?:а|и|у|е|ой|ою)?\b", "канализация", text)
    # «Гребёнка» — монтажное название коллектора. Приводим к каноническому
    # термину здесь же, чтобы и категоризация, и поиск по фиду видели одно слово.
    text = re.sub(r"\bгребенк(?:а|и|у|е|ой|ою)?\b", "коллектор", text)
    # Preserve engineering symbols used as semantic separators/units.  They
    # remain harmless for ordinary lexical search but let the notation layer
    # distinguish ``ΔT``, ``Ø20``, ``16×2`` and Cyrillic ``°С`` reliably.
    text = re.sub(r"[^a-zа-я0-9./,=\-+²°×µμδ∆~ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_sku(text: str | None) -> str:
    return normalize_text(text).replace(" ", "")


def collapse_sku_spaces(text: str) -> str:
    """Collapse whitespace around dots/dashes so `vrs . 256 . 18 . 0` reads as `vrs.256.18.0`."""
    return re.sub(r"\s*([.\-])\s*", r"\1", text)


def contains_any(text: str, needles: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(needle) in normalized for needle in needles)


def extract_first_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, normalize_text(text), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except (ValueError, IndexError):
        return None


def merge_slots(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in new.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged
