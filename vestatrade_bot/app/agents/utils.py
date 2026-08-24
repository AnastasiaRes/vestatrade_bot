from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
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


_WATER_APPLICATION_RE = re.compile(
    r"\bвод(?:а|ы|е|у|ой|ою)\b|\bводян\w*\b|"
    r"\bводоснаб\w*\b|\b(?:гвс|хвс)\b"
)


def mentions_water_application(text: str | None) -> bool:
    """Recognise Russian case forms of ``вода`` in a product application."""
    return bool(_WATER_APPLICATION_RE.search(normalize_text(text)))


# Замена и отрицание меняют смысл реплики целиком: «с рычагом вместо бабочки»
# и «не бабочка, а рычаг» называют оба значения, но просят только одно. Раньше
# извлекалось первое встреченное слово, поэтому бот подбирал ровно то, от чего
# покупатель отказался. Спаны отказа считаются один раз и переиспользуются
# всеми атрибутами, чтобы этот класс ошибок не пришлось чинить для каждого
# слота заново.
_REJECTION_PATTERNS = (
    r"(?:вместо|взамен)\s+(?P<span>[^,.;!?]{1,40})",
    r"(?:замен\w*|поменя\w*|смени\w*)\s+(?P<span>[^,.;!?]{1,30}?)\s+на\b",
    r"(?:,|\bа)\s*не\s+(?P<span>[^,.;!?]{1,40})",
    r"(?:^|[.;!?]\s*)не\s+(?P<span>[^,.;!?]{1,40})",
    r"\bбез\s+(?P<span>[^,.;!?]{1,40})",
    r"\bне\s+(?P<span>[^,.;!?]{1,25})",
)


def rejected_spans(text: str | None) -> list[tuple[int, int]]:
    """Участки реплики, которые покупатель назвал, чтобы от них отказаться."""
    normalized = normalize_text(text)
    spans: list[tuple[int, int]] = []
    for pattern in _REJECTION_PATTERNS:
        for match in re.finditer(pattern, normalized):
            spans.append((match.start("span"), match.end("span")))
    return spans


def resolve_preferred_option(
    text: str | None,
    options: Sequence[tuple[str, str]],
    *,
    infer_binary_opposite: bool = True,
) -> str | None:
    """Выбрать значение, которое покупатель действительно просит.

    ``options`` — пары «regex-маркер, каноническое значение». Значение внутри
    спана отказа не выигрывает никогда. Из нескольких принятых значений
    выбирается последнее: в одной реплике поздняя формулировка обычно уточняет
    раннюю («бабочку не надо, дайте рычаг»).

    При ``infer_binary_opposite`` отказ от единственного названного значения в
    паре («без бабочки») трактуется как выбор второго.
    """
    normalized = normalize_text(text)
    if not normalized:
        return None
    negative_spans = rejected_spans(normalized)

    def is_rejected(position: int) -> bool:
        return any(start <= position < end for start, end in negative_spans)

    accepted: list[tuple[int, str]] = []
    rejected_values: set[str] = set()
    for pattern, value in options:
        for match in re.finditer(pattern, normalized):
            if is_rejected(match.start()):
                rejected_values.add(value)
            else:
                accepted.append((match.start(), value))
    if accepted:
        return max(accepted, key=lambda item: item[0])[1]
    if infer_binary_opposite and len(options) == 2 and len(rejected_values) == 1:
        return next(
            value for _, value in options if value not in rejected_values
        )
    return None


# В фиде встречаются смешанные алфавиты: у насоса «UPС 25-40 180» буква «С»
# кириллическая, и текстовый поиск по латинскому «UPC» его не находил. Ключ
# модели складывает похожие буквы в латиницу и выбрасывает разделители, поэтому
# «UPС 25-40», «UPC 25/40» и «upc2540» дают одно и то же значение.
_MODEL_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h",
        "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
        "і": "i", "ј": "j", "ѕ": "s",
    }
)


def fold_model_key(text: str | None) -> str:
    """Ключ модели: латиница и цифры, без разделителей и алфавитных различий."""
    folded = normalize_text(text).translate(_MODEL_HOMOGLYPHS)
    return re.sub(r"[^a-z0-9]", "", folded)


# Кириллица → латиница по звучанию. Нужна там, где покупатель пишет марку
# по-русски: «Ардерия» и «Arderia» — одна и та же марка, а свёртка гомоглифов
# их не сближает (совпадают только визуально одинаковые буквы).
_TRANSLIT_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "iu", "я": "ia",
}


def transliterate_model_key(text: str | None) -> str:
    """Ключ модели с переводом кириллицы в латиницу по звучанию."""
    folded = normalize_text(text)
    out = "".join(_TRANSLIT_TABLE.get(ch, ch) for ch in folded)
    return re.sub(r"[^a-z0-9]", "", out)


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
