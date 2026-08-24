from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from .utils import normalize_text


@dataclass(frozen=True)
class NumericMention:
    """A scalar number tied to one unambiguous measurement dimension."""

    value: float
    start: int
    end: int


_SCALAR_NUMBER = r"-?\d{1,9}(?:[,.]\d+)?"
_SCALAR_NUMBER_RE = re.compile(
    r"(?<![a-zа-я0-9])"
    r"(?P<value>-?(?:\d{1,3}(?: \d{3})+|\d{1,9})(?:[,.]\d+)?)"
    r"(?![a-zа-я0-9])"
)

# A Cyrillic ``с`` is deliberately not a standalone temperature unit here.
# In normal Russian prose it is overwhelmingly the preposition "with":
# ``3/4 с американкой``.  ``°С``, ``градусов`` and Latin ``C`` remain explicit
# and unambiguous temperature notation.
_TEMPERATURE_UNIT = r"(?:°\s*[cс]|градус\w*|c\b)"
_PRESSURE_UNIT = r"(?:бар\w*|bar\b|атм(?:осфер\w*)?)"
_MONEY_UNIT = r"(?:руб\w*|р\b|тыс(?:яч\w*)?|т\s*\.?\s*р\.?|к\b)"
_METRE_UNIT = r"(?:м\b|метр(?:а|ов)?)"
_AREA_UNIT = r"(?:м\s*(?:2|²)|кв\.?\s*м|квадрат\w*(?:\s+метр\w*)?)"


def _as_float(value: str) -> float:
    return float(value.replace(" ", "").replace(",", "."))


def _is_compound_component(text: str, start: int, end: int) -> bool:
    """Reject a member of a fraction, dimension pair or model code.

    These forms are structured identifiers rather than standalone quantities:
    ``3/4`` is an inch size, ``16x2`` is a pipe dimension and ``25-6`` is a pump
    marking.  A pending question must not reinterpret one component as a scalar.
    """

    left = text[:start].rstrip()
    right = text[end:].lstrip()
    separators = "/xх×-"
    return bool(
        (left and left[-1] in separators)
        or (right and right[0] in separators)
    )


def _mentions_for_value(text: str, value: float) -> list[NumericMention]:
    normalized = normalize_text(text)
    mentions: list[NumericMention] = []
    for match in _SCALAR_NUMBER_RE.finditer(normalized):
        number = _as_float(match.group("value"))
        if not math.isclose(number, float(value), rel_tol=0.0, abs_tol=0.0001):
            continue
        mentions.append(NumericMention(number, match.start("value"), match.end("value")))
    return mentions


def _unit_family_after(text: str, end: int) -> str | None:
    """Return the explicit unit immediately following a numeric span."""

    tail = text[end:].lstrip()
    families: tuple[tuple[str, str], ...] = (
        ("temperature", rf"{_TEMPERATURE_UNIT}"),
        ("pressure", rf"{_PRESSURE_UNIT}"),
        ("flow", r"(?:л\s*/\s*мин|м\s*(?:3|³)\s*/\s*ч)\b"),
        ("area", rf"{_AREA_UNIT}\b"),
        ("power", r"(?:квт|вт|w|kw)\b"),
        ("voltage", r"(?:вольт\w*|v)\b"),
        ("volume", r"(?:л\b|литр\w*)"),
        ("length_mm", r"(?:мм\b|миллиметр\w*)"),
        ("length_cm", r"(?:см\b|сантиметр\w*)"),
        ("length_m", r"(?:м\b|метр\w*)"),
        ("angle", r"(?:углов\w*\s+градус\w*)"),
        ("count", r"(?:шт\.?\b|секц\w*|контур\w*|кол(?:ьц|ец)\w*)"),
        ("money", rf"{_MONEY_UNIT}"),
    )
    for family, pattern in families:
        if re.match(pattern, tail):
            return family
    return None


def _quantity_family_before(text: str, start: int) -> str | None:
    """Return a quantity named immediately before a numeric span.

    Unitless engineering shorthand is common (``температура 70``,
    ``давление 6``).  Such a label is stronger than a category-level weak guess
    such as interpreting every standard-looking two-digit number as a diameter.
    """

    before = normalize_text(text[:start])[-64:]
    families: tuple[tuple[str, str], ...] = (
        (
            "temperature",
            r"(?:температур\w*|темп\w*|(?<![a-zа-я])t)"
            r"(?:\s*(?:до|макс\w*|рабоч\w*|=))*\s*$",
        ),
        (
            "pressure",
            r"(?:давлен\w*|напор\w*|(?<![a-zа-я])(?:p|pn|ру))"
            r"(?:\s*(?:до|макс\w*|рабоч\w*|=))*\s*$",
        ),
        (
            "diameter",
            r"(?:диаметр\w*|размер\w*|условн\w*\s+проход\w*|"
            r"(?<![a-zа-я])(?:dn|ду|дн)|[øф])\s*$",
        ),
        ("length_m", r"(?:длин\w*|метраж\w*|расстоян\w*)\s*$"),
        ("area", r"(?:площад\w*|квадрат\w*)\s*$"),
        ("power", r"(?:мощност\w*)\s*$"),
        ("voltage", r"(?:напряжен\w*|питани\w*)\s*$"),
        ("money", r"(?:бюджет\w*|цен\w*|стоимост\w*)\s*$"),
    )
    for family, pattern in families:
        if re.search(pattern, before):
            return family
    return None


def numeric_span_has_incompatible_unit(
    text: str,
    number_end: int,
    *,
    expected_families: Iterable[str],
) -> bool:
    """Whether a number is explicitly labelled with a different dimension.

    This is useful for permissive deterministic extractors that accept an
    otherwise bare standard size or price.  The extractor may stay permissive,
    but an adjacent engineering unit always wins over that weak guess.
    """

    # Normalize only the suffix: normalizing the whole sentence can remove a
    # symbol before ``number_end`` and invalidate a caller-provided span.
    family = _unit_family_after(normalize_text(text[number_end:]), 0)
    return bool(family and family not in set(expected_families))


def numeric_span_has_incompatible_context(
    text: str,
    number_start: int,
    number_end: int,
    *,
    expected_families: Iterable[str],
) -> bool:
    """Check both the unit after a number and the quantity label before it."""

    expected = set(expected_families)
    after = _unit_family_after(normalize_text(text[number_end:]), 0)
    before = _quantity_family_before(text, number_start)
    return bool(
        (after and after not in expected)
        or (before and before not in expected)
    )


def _temperature_mentions(text: str) -> list[NumericMention]:
    normalized = normalize_text(text)
    mentions: list[NumericMention] = []
    patterns = (
        # Explicit unit works without a label: ``до 95 °C`` / ``70 градусов``.
        re.compile(
            rf"(?<![a-zа-я0-9])(?P<value>{_SCALAR_NUMBER})\s*"
            rf"{_TEMPERATURE_UNIT}"
        ),
        # A clear label makes the unit optional: ``температура 70`` / ``t=70``.
        re.compile(
            rf"(?<![a-zа-я])(?:t|темп\w*|температур\w*)\s*"
            rf"(?:=\s*|до\s*|макс(?:им\w*)?\s*)?"
            rf"(?P<value>{_SCALAR_NUMBER})(?:\s*{_TEMPERATURE_UNIT})?"
        ),
    )
    seen: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            start, end = match.start("value"), match.end("value")
            if (start, end) in seen or _is_compound_component(normalized, start, end):
                continue
            family = _unit_family_after(normalized, end)
            if family and family != "temperature":
                continue
            seen.add((start, end))
            mentions.append(
                NumericMention(_as_float(match.group("value")), start, end)
            )
    return sorted(mentions, key=lambda mention: mention.start)


def extract_temperature_c(text: str) -> float | None:
    """Extract an explicitly temperature-labelled Celsius value.

    The function intentionally returns no value for a bare Cyrillic ``с`` and
    for components of fractions/model codes.  Callers that are answering a
    structured temperature question can use :func:`numeric_slot_has_compatible_context`
    to admit a genuinely bare scalar response.
    """

    mentions = _temperature_mentions(text)
    return mentions[0].value if mentions else None


def extract_piece_length_mm(text: str, *, allow_bare: bool = False) -> int | None:
    """Read a pipe/section *item length* stated in metres and return millimetres.

    ``2 метра трубы`` normally describes the required total quantity, whereas
    ``длина трубы 2 метра`` describes the catalogue dimension of one item.
    Keeping that distinction in the shared numeric layer prevents the router
    and slot filler from inventing their own, subtly different conversions.

    ``allow_bare`` is reserved for a typed pending question which explicitly
    waits for ``length_mm``; in that context the preceding question supplies
    the otherwise omitted ``длина одного отрезка`` label.
    """

    normalized = normalize_text(text)
    value_pattern = rf"(?P<value>{_SCALAR_NUMBER})\s*{_METRE_UNIT}"
    labelled_patterns = (
        re.compile(
            rf"\bдлин\w*"
            rf"(?:\s+(?:одн\w+\s+)?(?:труб\w*|отрезк\w*|секци\w*))?"
            rf"\s*[:=\-]?\s*{value_pattern}"
        ),
        re.compile(
            rf"\b(?:труб\w*|отрезк\w*|секци\w*)\s+длин\w*"
            rf"\s*[:=\-]?\s*{value_pattern}"
        ),
    )
    match: re.Match[str] | None = None
    for pattern in labelled_patterns:
        match = pattern.search(normalized)
        if match:
            # ``общая длина`` and ``суммарная длина`` are quantities, not a
            # sellable item's dimension, even though the word ``длина`` exists.
            before = normalized[max(0, match.start() - 24) : match.start()]
            if re.search(r"(?:общ\w*|суммарн\w*|итогов\w*)\s*$", before):
                match = None
                continue
            break

    if match is None and allow_bare:
        match = re.fullmatch(
            rf"\s*(?:(?:труб\w*|отрез\w*|палк\w*)\s+)?"
            rf"(?:примерно\s+|около\s+)?{value_pattern}\s*",
            normalized,
        )
    if match is None:
        # Natural catalogue wording often omits the word «длина» entirely:
        # «нужен отрезок 2 метра» still names the size of one sellable item,
        # not the total route quantity.
        match = re.search(
            rf"\b(?:(?:од(?:ин|на)\s+труб\w*)|отрез\w*|палк\w*)\s+"
            rf"(?:примерно\s+|около\s+)?{value_pattern}",
            normalized,
        )
    if match is None and re.search(
        r"\bполметра\b[^.!?]{0,18}\bдлин\w*|"
        r"\bдлин\w*[^.!?]{0,18}\bполметра\b",
        normalized,
    ):
        return 500
    if match is None:
        return None

    metres = _as_float(match.group("value"))
    if not 0 < metres <= 100:
        return None
    millimetres = metres * 1000
    if not math.isclose(millimetres, round(millimetres), abs_tol=0.0001):
        return None
    return int(round(millimetres))


def extract_total_length_m(text: str) -> float | None:
    """Read the requested *total route quantity* without stealing item length.

    A customer can state both values in one sentence: «палка 2 м, всего по
    трассе 20 м».  The old router treated these as mutually exclusive and
    deleted the total as soon as it saw the item length.  Explicit total/route
    wording is therefore parsed independently and may coexist with
    ``length_mm``.
    """

    normalized = normalize_text(text)
    value = rf"(?P<value>{_SCALAR_NUMBER})\s*{_METRE_UNIT}"
    explicit_patterns = (
        re.compile(
            rf"\b(?:всего|суммарн\w*|итого|общ(?:ая|ий|его)\s+"
            rf"(?:длин\w*|метраж\w*)|метраж\w*)[^\d]{{0,28}}{value}"
        ),
        re.compile(
            rf"\b(?:по\s+)?трасс\w*[^\d]{{0,28}}"
            rf"(?:нужн\w*|требу\w*|получа\w*)?[^\d]{{0,12}}{value}"
        ),
        re.compile(
            rf"{value}[^.!?]{{0,24}}\b(?:всего|суммарн\w*|итого|"
            rf"по\s+(?:всей\s+)?трасс\w*|общ(?:ая|ий)\s+метраж\w*)\b"
        ),
        re.compile(
            rf"\b(?:по\s+)?трасс\w*[^.!?]{{0,18}}"
            rf"метр(?:а|ов)?\s+(?P<value>{_SCALAR_NUMBER})(?![a-zа-я0-9])"
        ),
    )
    for pattern in explicit_patterns:
        match = pattern.search(normalized)
        if match:
            metres = _as_float(match.group("value"))
            return metres if 0 < metres <= 100000 else None

    # With no explicit total label, a plain «нужно 20 метров трубы» is a
    # quantity.  Do not reuse a value already identified as one item's length.
    if extract_piece_length_mm(normalized) is not None:
        return None
    generic = re.search(
        rf"(?<!\d)(?P<value>{_SCALAR_NUMBER})\s*{_METRE_UNIT}",
        normalized,
    )
    if not generic:
        return None
    metres = _as_float(generic.group("value"))
    return metres if 0 < metres <= 100000 else None


def _is_bare_scalar_answer(text: str, value: float, *, unit_pattern: str = "") -> bool:
    normalized = normalize_text(text)
    unit = rf"(?:\s*{unit_pattern})?" if unit_pattern else ""
    match = re.fullmatch(
        rf"\s*(?:(?:до|около|примерно|максимум|минимум)\s+)?"
        rf"(?P<value>{_SCALAR_NUMBER}){unit}\s*",
        normalized,
    )
    return bool(
        match
        and math.isclose(
            _as_float(match.group("value")),
            float(value),
            rel_tol=0.0,
            abs_tol=0.0001,
        )
        and not _is_compound_component(
            normalized,
            match.start("value"),
            match.end("value"),
        )
    )


def is_bare_numeric_answer(text: str, value: float) -> bool:
    """Whether the whole turn is one unitless scalar (with harmless modifiers)."""

    return _is_bare_scalar_answer(text, value)


def _value_has_temperature_context(text: str, value: float) -> bool:
    mentions = _temperature_mentions(text)
    if any(
        math.isclose(mention.value, value, rel_tol=0.0, abs_tol=0.0001)
        for mention in mentions
    ):
        return True
    # Number words are already value-grounded by the interpreter.  This branch
    # only supplies their missing dimension, never their numeric conversion.
    normalized = normalize_text(text)
    return not re.search(r"\d", normalized) and bool(
        re.search(rf"температур|темп\w*|{_TEMPERATURE_UNIT}", normalized)
    )


def _value_has_pressure_context(text: str, value: float) -> bool:
    normalized = normalize_text(text)
    for mention in _mentions_for_value(normalized, value):
        if _is_compound_component(normalized, mention.start, mention.end):
            continue
        after = _unit_family_after(normalized, mention.end)
        if after and after != "pressure":
            continue
        if after == "pressure" or _quantity_family_before(
            normalized,
            mention.start,
        ) == "pressure":
            return True
    return False


def _value_has_money_context(text: str, value: float) -> bool:
    normalized = normalize_text(text)
    has_ruble_symbol = "₽" in text
    for mention in _mentions_for_value(normalized, value):
        if _is_compound_component(normalized, mention.start, mention.end):
            continue
        family = _unit_family_after(normalized, mention.end)
        if family and family != "money":
            continue
        if family == "money" or has_ruble_symbol:
            return True
        before = normalized[max(0, mention.start - 42) : mention.start]
        if re.search(
            r"(?:бюджет\w*|по\s+цене|цен\w*\s+(?:до|от)|"
            r"не\s+(?:дороже|дешевле)|стоимост\w*)\s*$",
            before,
        ):
            return True
        # A large scalar following a directional word is conventional shorthand
        # for a price.  Small values stay ambiguous unless money was named.
        if value >= 1000 and re.search(
            r"(?:^|\s)(?:до|от|максимум|минимум|в\s+пределах)\s*$",
            before,
        ):
            return True
    return False


def _value_has_metric_dimension_context(text: str, value: float) -> bool:
    normalized = normalize_text(text)
    for mention in _mentions_for_value(normalized, value):
        if _is_compound_component(normalized, mention.start, mention.end):
            continue
        family = _unit_family_after(normalized, mention.end)
        if family:
            if family == "length_mm":
                return True
            continue
        before = normalized[max(0, mention.start - 32) : mention.start]
        if re.search(r"(?:диаметр\w*|размер\w*|\b(?:dn|ду|дн)\s*|[øф]\s*)$", before):
            return True
    return False


def _value_has_area_context(text: str, value: float) -> bool:
    """Whether this number is stated as an area rather than some other quantity.

    The live run showed why a bare positional match is not enough: in
    ``труба 16х2,0 pe-rt, шаг 15`` both ``2,0`` and ``15`` are numbers, but one
    is a wall thickness and the other a laying pitch in centimetres.  Neither
    may become a floor area, so an explicit unit or an explicit label is
    required here.
    """

    normalized = normalize_text(text)
    for mention in _mentions_for_value(normalized, value):
        if _is_compound_component(normalized, mention.start, mention.end):
            continue
        family = _unit_family_after(normalized, mention.end)
        if family:
            if family == "area":
                return True
            continue
        if _quantity_family_before(normalized, mention.start) == "area":
            return True
    return False


# Бытовые роли чисел: этаж, время суток, реквизит, номер квартиры. Ни одна из
# них не является техническим параметром, и валидатор обязан знать их наравне
# с единицами измерения. Живой прогон: «нужно до 18:00» стало бюджетом
# 1800 ₽ (C03), «на 22 этаже» — диаметром 22 мм (B13).
_DOMESTIC_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("этаж", r"\b{value}\s*(?:-?[йяе]\s*)?этаж\w*"),
    ("этаж", r"\bна\s+{value}\s*(?:-?[йыя]\s*)?\s*этаж\w*"),
    ("квартира", r"\b(?:кв\.?|квартир\w*|офис\w*|подъезд\w*)\s*№?\s*{value}\b"),
    ("реквизит", r"\b(?:инн|огрн(?:ип)?|кпп|окпо|бик|счет\w*|счёт\w*)\s*№?\s*{value}\b"),
    ("номер заказа", r"\b(?:заказ\w*|накладн\w*)\s*№?\s*{value}\b"),
    ("дом", r"\b(?:дом|д\.)\s*{value}\b"),
)
# Время суток: «до 18:00», «к 12 часам», «в 9 утра».
_TIME_OF_DAY_RE = re.compile(
    r"\b\d{1,2}\s*[:.]\s*\d{2}\b"
    r"|\b\d{1,2}\s*(?:час\w*|утра|вечера|дня|ночи)\b",
    re.IGNORECASE,
)


def number_has_domestic_role(text: str, value: float) -> str | None:
    """Занято ли это число бытовой ролью — этажом, временем, реквизитом.

    Возвращает название роли или ``None``. Проверка идёт по конкретному
    значению, а не по всей реплике: «доставка до 18:00, бюджет 20 000» — здесь
    18 занято временем, а 20 000 остаётся ценой.
    """

    haystack = str(text or "")
    if not haystack:
        return None
    # ``%g`` переводит большие целые в научную нотацию («7.71412e+09»), и
    # десятизначный ИНН переставал совпадать сам с собой.
    rendered = str(int(value)) if float(value).is_integer() else f"{value:g}"
    escaped = re.escape(rendered)
    for role, pattern in _DOMESTIC_ROLE_PATTERNS:
        if re.search(pattern.format(value=escaped), haystack, re.IGNORECASE):
            return role
    return None


def numeric_slot_has_compatible_context(
    key: str,
    value: float,
    *,
    message: str,
    evidence: str | None = None,
    pending_slot_keys: Iterable[str] = (),
) -> bool:
    """Validate a numeric slot against its physical dimension and turn context.

    Literal equality alone is insufficient: the number ``4`` is present in
    ``3/4``, but it is not a temperature; ``70`` may be a temperature and must
    not simultaneously become a price.  Explicit units/labels are strongest,
    while a bare scalar is admitted only for the slot the bot explicitly asked.
    Unknown slots are left unchanged so this validator can be introduced
    incrementally without redefining every product attribute at once.
    """

    pending = set(pending_slot_keys)
    candidates = [part for part in (evidence, message) if part]

    # Число, занятое бытовой ролью, техническим параметром быть не может — что
    # бы ни говорили единицы рядом. Проверка стоит до разбора по слотам, чтобы
    # её не обходили запасные, более снисходительные ветки извлечения.
    if any(number_has_domestic_role(part, value) for part in candidates):
        return False

    if key == "operating_temperature_c":
        if any(_value_has_temperature_context(part, value) for part in candidates):
            return True
        return key in pending and _is_bare_scalar_answer(
            message,
            value,
            unit_pattern=_TEMPERATURE_UNIT,
        )

    if key in {
        "operating_pressure_bar",
        "pressure_class_bar",
        "inlet_pressure_bar",
        "required_pressure_bar",
    }:
        if any(_value_has_pressure_context(part, value) for part in candidates):
            return True
        return key in pending and _is_bare_scalar_answer(
            message,
            value,
            unit_pattern=_PRESSURE_UNIT,
        )

    if key in {"max_price", "min_price"}:
        if any(_value_has_money_context(part, value) for part in candidates):
            return True
        return key in pending and _is_bare_scalar_answer(message, value)

    if key in {"area_m2", "warm_floor_area_m2"}:
        if any(_value_has_area_context(part, value) for part in candidates):
            return True
        # A bare number is admitted only as the answer to a question the bot
        # actually asked; otherwise any digit in the turn could become an area.
        # Plain metres count there: asked «какая площадь тёплого пола?», people
        # routinely answer «240 метров» and mean square metres.
        return key in pending and _is_bare_scalar_answer(
            message,
            value,
            unit_pattern=rf"(?:{_AREA_UNIT}|{_METRE_UNIT})",
        )

    if key == "diameter_mm":
        if any(_value_has_metric_dimension_context(part, value) for part in candidates):
            return True
        return key in pending and _is_bare_scalar_answer(
            message,
            value,
            unit_pattern=r"(?:мм\b|миллиметр\w*)",
        )

    return True
