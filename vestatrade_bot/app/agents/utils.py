from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    # Покупатели могут называть канализацию «канашкой». Приводим разговорное
    # слово и его падежные формы к каноническому термину до классификации,
    # извлечения слотов и поиска по ассортименту.
    text = re.sub(r"\bканашк(?:а|и|у|е|ой|ою)?\b", "канализация", text)
    text = re.sub(r"[^a-zа-я0-9./,\-+² ]+", " ", text)
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
