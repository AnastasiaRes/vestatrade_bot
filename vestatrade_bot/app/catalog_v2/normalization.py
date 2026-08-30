"""Source-preserving, deterministic catalogue fact normalization."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.component_evidence import builtin_part_state_from_text
from app.models import Product

from .contracts import (
    CatalogFact,
    CatalogFactIssue,
    CatalogFlowHeadPoint,
    CatalogProductSnapshot,
    FactProvenance,
    ProductKind,
)
from .registry import ProductContractRegistry, canonical_brand, normalize_identity


_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_NUMERIC_RANGE_VALUE_RE = re.compile(
    r"^\s*(?P<minimum>[-+]?\d+(?:[.,]\d+)?)\s*"
    r"(?:\.\.|[-\u2013\u2014]|(?:to|до))\s*"
    r"(?P<maximum>[-+]?\d+(?:[.,]\d+)?)\s*"
    r"(?:(?:к\s*вт|kw|вт|w|мм|mm|см|cm|м|m|бар|bar|%))?\s*$",
    re.IGNORECASE,
)
_NUMERIC_CHOICE_SPLIT_RE = re.compile(
    r"\s+(?:или|либо|or)\s+",
    re.IGNORECASE,
)
_NUMERIC_CHOICE_ITEM_RE = re.compile(
    r"^\s*(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*"
    r"(?:(?:к\s*вт|kw|вт|w|мм|mm|см|cm|м|m|бар|bar|%))?\s*$",
    re.IGNORECASE,
)
_COMPOUND_DIMENSION_RE = re.compile(
    r"^\s*(?P<primary>\d+(?:[.,]\d+)?)\s*[xх×]\s*"
    r"(?P<secondary>\d+(?:[.,]\d+)?)\s*(?:mm|мм)?\s*$",
    re.IGNORECASE,
)
_PIPE_TEMPERATURE_THEN_PRESSURE_RE = re.compile(
    r"температур\w*[^.!?]{0,55}?"
    r"(?P<temperature>-?\d{1,3}(?:[.,]\d+)?)\s*(?:°\s*)?[cс]"
    r"(?![a-zа-яё])[^.!?]{0,55}?"
    r"(?P<pressure>\d+(?:[.,]\d+)?)\s*(?:бар|bar)\b",
    re.IGNORECASE,
)
_PIPE_PRESSURE_THEN_TEMPERATURE_RE = re.compile(
    r"давлен\w*[^.!?]{0,55}?"
    r"(?P<pressure>\d+(?:[.,]\d+)?)\s*(?:бар|bar)\b"
    r"[^.!?]{0,55}?температур\w*[^.!?]{0,35}?"
    r"(?P<temperature>-?\d{1,3}(?:[.,]\d+)?)\s*(?:°\s*)?[cс]"
    r"(?![a-zа-яё])",
    re.IGNORECASE,
)
_PIPE_COLD_WATER_PRESSURE_RE = re.compile(
    r"(?:транспортиров\w*|для)\s+холодн\w*\s+вод\w*\s*\)?"
    r"[^.!?]{0,25}?(?P<pressure>\d+(?:[.,]\d+)?)\s*(?:бар|bar)\b",
    re.IGNORECASE,
)
_PARENTHETICAL_PIPE_DIMENSION_RE = re.compile(
    r"^\s*(?P<primary>\d+(?:[.,]\d+)?)\s*\(\s*"
    r"(?P<secondary>\d+(?:[.,]\d+)?)\s*\)\s*(?:mm|мм)?\s*$",
    re.IGNORECASE,
)
_SCALAR_NUMERIC_FACT_NAMES = frozenset(
    {
        "diameter_mm",
        "mounting_length_mm",
        "max_head_m",
        "max_flow_l_h",
        "power_kw",
        "declared_heated_area_m2",
        "center_distance_mm",
        "heat_output_w",
        "suction_depth_m",
        "circuits",
        "port_count",
        "glycol_concentration_percent",
        "micron_rating_um",
        "operating_temperature_c",
        "operating_pressure_bar",
    }
)

_UNIT_LABEL_ALIASES = {
    "мм": "mm",
    "см": "cm",
    "м": "m",
    "квт": "kw",
    "вт": "w",
    "бар": "bar",
    "мпа": "mpa",
    "кпа": "kpa",
    "па": "pa",
    "атм": "atm",
    "л/ч": "l/h",
    "л/мин": "l/min",
    "м3/ч": "m3/h",
    "м³/ч": "m3/h",
    "м2": "m2",
    "м²": "m2",
    "m²": "m2",
    "мкм": "um",
    "°с": "c",
    "℃": "c",
}


def _parsed_number(value: str) -> float | int:
    parsed = float(value.replace(",", "."))
    return int(parsed) if parsed.is_integer() else parsed


def normalize_unit_label(unit: str | None) -> str | None:
    """Canonicalize spelling only; never convert a numeric value."""

    if unit is None:
        return None
    compact = "".join(str(unit).casefold().replace("³", "3").split())
    if not compact:
        return None
    return _UNIT_LABEL_ALIASES.get(compact, compact)


def parse_numeric_range_value(
    value: object,
) -> tuple[float | int, float | int] | None:
    """Parse an explicit closed numeric interval without choosing an endpoint.

    This helper is deliberately syntax-only.  The semantic layer must already
    have assigned the interval to a typed fact and unit; the catalogue layer
    merely preserves the two stated bounds instead of silently treating
    ``10-15`` as the scalar ``10``.
    """

    if not isinstance(value, str):
        return None
    match = _NUMERIC_RANGE_VALUE_RE.fullmatch(value)
    if match is None:
        return None
    minimum = _parsed_number(match.group("minimum"))
    maximum = _parsed_number(match.group("maximum"))
    if float(minimum) > float(maximum):
        return None
    return minimum, maximum


def format_numeric_range_value(
    minimum: float | int,
    maximum: float | int,
) -> str:
    """Return the stable source-independent representation used in V2 state."""

    return f"{minimum}\u2013{maximum}"


def parse_numeric_choice_value(
    value: object,
) -> tuple[float | int, ...] | None:
    """Parse an explicit discrete numeric choice without choosing an item.

    ``130 или 180`` is not the continuous interval ``130–180``.  Keeping the
    alternatives distinct prevents readiness from silently taking the first
    number and lets a later customer turn safely narrow the set.
    """

    if not isinstance(value, str):
        return None
    parts = _NUMERIC_CHOICE_SPLIT_RE.split(value.strip())
    if len(parts) < 2:
        return None
    parsed: list[float | int] = []
    for part in parts:
        match = _NUMERIC_CHOICE_ITEM_RE.fullmatch(part)
        if match is None:
            return None
        parsed.append(_parsed_number(match.group("value")))
    unique = tuple(dict.fromkeys(parsed))
    return unique if len(unique) >= 2 else None


def format_numeric_choice_value(values: Iterable[float | int]) -> str:
    """Return the stable representation of a discrete numeric choice."""

    return " или ".join(str(value) for value in values)


def _structured_scalar_number(
    name: str,
    raw: object,
) -> tuple[float | int | None, bool]:
    """Return an exact scalar and whether the structured source is ambiguous.

    A range or a list of distinct values is not a scalar fact.  The only
    multi-number forms interpreted here are declarative product dimensions:
    ``primary x secondary`` and, for an outer pipe diameter, ``outer(wall)``.
    No endpoint of an unrecognised multi-value source is selected.
    """

    if isinstance(raw, bool):
        return None, False
    if isinstance(raw, (int, float)):
        return raw, False
    source = str(raw or "")
    compound = _COMPOUND_DIMENSION_RE.fullmatch(source)
    if compound is not None and name in {
        "diameter_mm",
        "secondary_diameter_mm",
        "length_mm",
    }:
        component = (
            compound.group("primary")
            if name == "diameter_mm"
            else compound.group("secondary")
        )
        return _parsed_number(component), False
    parenthetical = _PARENTHETICAL_PIPE_DIMENSION_RE.fullmatch(source)
    if parenthetical is not None and name == "diameter_mm":
        primary = _parsed_number(parenthetical.group("primary"))
        secondary = _parsed_number(parenthetical.group("secondary"))
        # ``16(2.2)`` is the established outer-diameter/wall notation.  A
        # larger parenthesised value such as ``130(180)`` is an alternative,
        # not a wall thickness, and therefore remains ambiguous.
        if float(secondary) < float(primary):
            return primary, False
        return None, True

    literals = tuple(_NUMBER_RE.finditer(source))
    if not literals:
        return None, False
    values = tuple(_parsed_number(match.group(0)) for match in literals)
    if len(values) > 1:
        return None, True
    return values[0], False


def _fact(
    name: str,
    value: str | int | float | bool | None,
    *,
    source: str,
    field: str,
    raw: object,
    parser: str,
    unit: str | None = None,
    source_document: str | None = None,
    source_section: str | None = None,
) -> CatalogFact | None:
    if value is None or value == "":
        return None
    return CatalogFact(
        name=name,
        value=value,
        unit=unit,
        provenance=FactProvenance(
            source=source,
            source_field=field,
            raw_value=str(raw)[:500],
            parser=parser,
            source_document=source_document,
            source_section=source_section,
        ),
    )


def _single_pipe_operating_point(
    description: str,
) -> tuple[float | int, float | int, str] | None:
    """Return one explicit temperature/pressure rating point, never PN.

    Pipe pressure is temperature-dependent.  Consequently the two values are
    emitted only when the description binds them in one sentence and contains
    exactly one such point.  Multiple points are a curve/table fragment rather
    than a scalar rating and remain unverified for the planner.
    """

    matches = [
        *list(_PIPE_TEMPERATURE_THEN_PRESSURE_RE.finditer(description)),
        *list(_PIPE_PRESSURE_THEN_TEMPERATURE_RE.finditer(description)),
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    return (
        _parsed_number(match.group("temperature")),
        _parsed_number(match.group("pressure")),
        match.group(0),
    )


def _structured_fact(name: str, raw: object, field: str) -> CatalogFact | None:
    unit = {
        "diameter_mm": "mm",
        "mounting_length_mm": "mm",
        "max_head_m": "m",
        "max_flow_l_h": "l/h",
        "power_kw": "kW",
        "declared_heated_area_m2": "m2",
        "center_distance_mm": "mm",
        "heat_output_w": "W",
        "suction_depth_m": "m",
        "glycol_concentration_percent": "%",
        "micron_rating_um": "um",
        "operating_temperature_c": "C",
        "operating_pressure_bar": "bar",
    }.get(name)
    if name in _SCALAR_NUMERIC_FACT_NAMES:
        normalized = normalize_fact_value(name, raw)
        scalar, ambiguous = _structured_scalar_number(name, raw)
        if ambiguous:
            return None
        value: object = scalar
        if value is None and isinstance(normalized, (int, float)) and not isinstance(
            normalized,
            bool,
        ):
            value = normalized
    else:
        value = normalize_fact_value(name, raw)
    return _fact(
        name,
        value,
        source="attribute",
        field=field,
        raw=raw,
        parser="structured_attribute",
        unit=unit,
    )


def normalize_fact_value(name: str, value: object) -> str | int | float | bool:
    """Normalize only explicit, compatible values; never infer a missing fact."""

    if name == "integrated_circulation_pump":
        state = builtin_part_state_from_text(str(value or ""), "насос")
        # An omitted / merely mentioned component must not become a false
        # catalogue fact.  ``_fact`` drops this empty result.
        return state if state is not None else ""
    if name == "circuits" and isinstance(value, bool):
        return 2 if value else 1
    if isinstance(value, (int, float, bool)):
        return value
    text = normalize_identity(value)
    if name == "brand":
        # Product cards and customer constraints must use the same canonical
        # value; unknown brand text remains unproven rather than being mapped
        # to the nearest catalogue manufacturer.
        return canonical_brand(value) or text
    if name == "reinforcement":
        markers = set()
        if any(
            marker in text
            for marker in (
                "pp fiber",
                "glass fiber",
                "glass fibre",
                "fiberglass",
                "стекловолок",
            )
        ):
            markers.add("glass_fiber")
        if any(
            marker in text
            for marker in (
                "pp alux",
                "alux",
                "aluminium",
                "aluminum",
                "алюмин",
                "фольг",
            )
        ):
            markers.add("aluminium")
        if (
            text == "unreinforced"
            or "без армирован" in text
            or "неармирован" in text
        ):
            markers.add("unreinforced")
        if len(markers) == 1:
            return next(iter(markers))
    if name == "material":
        if any(
            marker in text
            for marker in (
                "ppr",
                "pp r",
                "pp fiber",
                "pp alux",
                "polypropylene",
                "полипропилен",
            )
        ):
            return "ppr"
        if any(marker in text for marker in ("pex", "pe xa", "сшитого полиэтилен")):
            return "pex"
        if "алюмин" in text or text == "aluminium":
            return "aluminium"
    if name == "pressure_class":
        match = re.fullmatch(r"(?:pn|пн)\s*(\d{1,3}(?:[.,]\d+)?)", text)
        if match:
            return f"pn{match.group(1).replace(',', '.')}"
    if name == "connection_size":
        # G/R/Rp/NPT describe a thread standard; the connection-size fact is
        # only its nominal dimension.  Standards remain separate facts rather
        # than making G1/2 fail to compare with a feed value of 1/2.
        match = re.fullmatch(
            r"(?:g|r|rp|npt)?\s*(\d+(?:\s+\d+/\d+)?|\d+/\d+)\s*(?:inch|дюйм(?:а|ов)?|[\"\u2033])?",
            text,
        )
        if match:
            return " ".join(match.group(1).split())
    if name == "connection_pattern":
        compact = re.sub(r"[^a-zа-яе]+", " ", text).strip()
        if "внутренняя/внутренняя" in text or re.search(
            r"(?<![a-z])ff(?![a-z])", text
        ) or re.fullmatch(
            r"(?:вр|вн|внутренняя)\s+(?:вр|вн|внутренняя)", compact
        ):
            return "female_female"
        if any(marker in text for marker in ("внутренняя/наружная", "внутренней наружной")) or re.search(
            r"(?<![a-z])fm(?![a-z])", text
        ) or re.fullmatch(
            r"(?:вр|вн|внутренняя)\s+(?:нр|нар|наружная)", compact
        ):
            return "female_male"
        if any(marker in text for marker in ("наружная/внутренняя", "наружной внутренней")) or re.search(
            r"(?<![a-z])mf(?![a-z])", text
        ) or re.fullmatch(
            r"(?:нр|нар|наружная)\s+(?:вр|вн|внутренняя)", compact
        ):
            return "male_female"
        if "наружная/наружная" in text or re.search(
            r"(?<![a-z])mm(?![a-z])", text
        ) or re.fullmatch(
            r"(?:нр|нар|наружная)\s+(?:нр|нар|наружная)", compact
        ):
            return "male_male"
    if name == "valve_shape":
        if "углов" in text:
            return "angle"
        if "прям" in text:
            return "straight"
    if name == "boiler_type":
        if "газ" in text or text in {"gas", "natural gas"}:
            return "gas"
        if "электр" in text or text in {"electric", "electricity"}:
            return "electric"
    if name == "port_count":
        numeric = re.search(
            r"(?<!\d)([2-9])\s*(?:[-–—]\s*[xх]?\s*|[xх]\s*)?"
            r"(?:way|ways|ход(?:а|ов|овой|овый)?|"
            r"port|ports|порт(?:а|ов|овый)?)\b",
            text,
        )
        if numeric:
            return int(numeric.group(1))
        if re.search(
            r"(?:three|тр[её]х)\s*(?:way|ход(?:овой|овый)?|port|порт(?:овый)?)\b",
            text,
        ):
            return 3
        number_words = {
            "one": 1,
            "один": 1,
            "одна": 1,
            "одно": 1,
            "two": 2,
            "два": 2,
            "две": 2,
            "three": 3,
            "три": 3,
            "four": 4,
            "четыре": 4,
        }
        directional = re.findall(
            r"(?<![a-zа-яеё\d])"
            r"(\d+|one|two|three|four|один|одна|одно|два|две|три|четыре)\s*"
            r"(?:inlets?|outlets?|вход(?:а|ов)?|выход(?:а|ов)?)\b",
            text,
        )
        if directional:
            counts = [
                int(item) if item.isdigit() else number_words[item]
                for item in directional
            ]
            total = sum(counts)
            if 2 <= total <= 9:
                return total
    if name == "combustion_chamber":
        if any(marker in text for marker in ("closed", "закр", "герметич")):
            return "closed"
        if any(marker in text for marker in ("open", "откр", "естественная тяга")):
            return "open"
    if name == "application":
        if any(marker in text for marker in ("вод", "water", "гвс")):
            return "water"
        if any(marker in text for marker in ("газ", "gas")):
            return "gas"
        if any(marker in text for marker in ("пар", "steam")):
            return "steam"
    if name == "pipe_service":
        services: list[str] = []
        if any(
            marker in text
            for marker in ("cold water", "cold_water", "холодн", "хвс")
        ):
            services.append("cold_water")
        if any(
            marker in text
            for marker in ("hot water", "hot_water", "горяч", "гвс", "dhw")
        ):
            services.append("hot_water")
        if any(
            marker in text
            for marker in ("heating", "отоплен", "теплоносител")
        ):
            services.append("heating")
        if services:
            return " ".join(dict.fromkeys(services))
        if any(marker in text for marker in ("water", "вод")):
            return "water_unspecified"
    if name == "washable":
        if any(
            marker in text
            for marker in (
                "непромывн",
                "не промывн",
                "без промывки",
                "non washable",
                "not washable",
                "not self cleaning",
            )
        ) or text in {"false", "no", "нет"}:
            return False
        if any(
            marker in text
            for marker in (
                "самопромыв",
                "промывн",
                "self cleaning",
                "backwash",
                "washable",
                "flushable",
            )
        ) or text in {"true", "yes", "да"}:
            return True
    if name == "coolant_type":
        if "пропиленгликол" in text or "propylene glycol" in text:
            return "propylene_glycol"
        if "этиленгликол" in text or "ethylene glycol" in text:
            return "ethylene_glycol"
        if "гликол" in text or "glycol" in text or "антифриз" in text:
            return "glycol_unspecified"
        if text in {"water", "for water"} or "для воды" in text:
            return "water"
    if name == "filter_method":
        if any(marker in text for marker in ("механич", "mechanical", "сетчат", "грязевик", "косой")):
            return "mechanical"
        if "магнит" in text or "magnetic" in text:
            return "magnetic"
        if "обратн" in text and "осмос" in text or "reverse osmosis" in text:
            return "reverse_osmosis"
        if "угол" in text or "carbon" in text:
            return "carbon"
        if "умягч" in text or "soften" in text:
            return "softening"
        if "обезжелез" in text or "iron removal" in text:
            return "iron_removal"
    if name == "sewer_scope":
        if "наруж" in text or "external" in text:
            return "external"
        if "внутр" in text or "internal" in text:
            return "internal"
    if name == "circuits":
        if (
            "двух" in text
            or text == "2"
            or ("отоп" in text and ("горяч" in text or "гвс" in text))
            or text in {"true", "yes", "да"}
        ):
            return 2
        if "одно" in text or text == "1" or text in {"false", "no", "нет"}:
            return 1
    return text


def parse_pump_designation(
    value: object,
) -> dict[str, tuple[int | float, str, str]]:
    """Parse common circulation-pump size codes without product/SKU rules.

    Supported families include ``25/6-130``, ``25-40 180`` and variable-head
    designations such as ``30/1-8``.  The parser is invoked only after the
    product has already been typed as a pump.  Parenthesised alternative
    mounting sizes (``130(180)``) deliberately remain absent instead of being
    collapsed to the first number.
    """

    text = str(value or "")
    if not text:
        return {}

    variable = re.search(
        r"(?<!\d)(?P<dn>\d{2})\s*/\s*"
        r"(?P<minimum>\d{1,2}(?:[.,]\d+)?)\s*[-–]\s*"
        r"(?P<maximum>\d{1,2}(?:[.,]\d+)?)(?!\d)",
        text,
    )
    if variable is not None:
        minimum = float(variable.group("minimum").replace(",", "."))
        maximum = float(variable.group("maximum").replace(",", "."))
        if 0 <= minimum <= maximum <= 20:
            maximum_value: int | float = (
                int(maximum) if maximum.is_integer() else maximum
            )
            return {
                "diameter_mm": (int(variable.group("dn")), "mm", variable.group(0)),
                "max_head_m": (maximum_value, "m", variable.group(0)),
            }

    slash = re.search(
        r"(?<!\d)(?P<dn>\d{2})\s*/\s*(?P<head>\d{1,2})"
        r"(?:\s*[-–]\s*(?P<length>\d{3}))?(?!\d)",
        text,
    )
    if slash is not None:
        facts: dict[str, tuple[int | float, str, str]] = {
            "diameter_mm": (int(slash.group("dn")), "mm", slash.group(0)),
            "max_head_m": (int(slash.group("head")), "m", slash.group(0)),
        }
        suffix = text[slash.end():]
        if slash.group("length") and not re.match(r"\s*\(\s*\d", suffix):
            facts["mounting_length_mm"] = (
                int(slash.group("length")),
                "mm",
                slash.group(0),
            )
        return facts

    hyphen = re.search(
        r"(?<!\d)(?P<dn>\d{2})\s*[-–]\s*(?P<head_code>\d{1,2})"
        r"(?:\s*(?:[-–]\s*)?(?P<length>\d{3}))?(?!\d)",
        text,
    )
    if hyphen is None:
        return {}
    raw_head = int(hyphen.group("head_code"))
    head: int | float = raw_head / 10 if raw_head >= 20 else raw_head
    if isinstance(head, float) and head.is_integer():
        head = int(head)
    facts = {
        "diameter_mm": (int(hyphen.group("dn")), "mm", hyphen.group(0)),
        "max_head_m": (head, "m", hyphen.group(0)),
    }
    suffix = text[hyphen.end():]
    if hyphen.group("length") and not re.match(r"\s*\(\s*\d", suffix):
        facts["mounting_length_mm"] = (
            int(hyphen.group("length")),
            "mm",
            hyphen.group(0),
        )
    return facts


def _generic_facts(
    product: Product,
    kind: ProductKind,
    parsers: set[str],
) -> list[CatalogFact]:
    name = product.name or ""
    description = product.description or ""
    name_norm = normalize_identity(name)
    description_norm = normalize_identity(description)
    result: list[CatalogFact | None] = []

    if "primary_metric_size" in parsers:
        match = re.search(r"(?<![/\d])(?P<a>\d{2,3})\s*[*xх-]\s*(?P<b>\d{2,4})(?!\d)", name, re.I)
        if match:
            result.append(_fact("diameter_mm", int(match.group("a")), source="name", field="name", raw=match.group(0), parser="primary_metric_size", unit="mm"))
        else:
            values = re.findall(r"(?<![/\d])(\d{2,3})\s*(?:mm|мм)\b", name, re.I)
            if values:
                result.append(_fact("diameter_mm", int(values[-1]), source="name", field="name", raw=values[-1], parser="primary_metric_size", unit="mm"))

    if "pipe_outer_diameter" in parsers and kind == ProductKind.PEX_PIPE:
        # PEX/PE-X pipe names normally encode outer diameter followed by wall
        # thickness (16x2.0 or 16(2.0)).  This parser only reads that explicit
        # product designation; it never derives a missing size.
        match = re.search(
            r"(?<!\d)(?P<outer>\d{2,3})\s*(?:[xх]\s*\d(?:[.,]\d+)?|\(\s*\d(?:[.,]\d+)?\s*\))",
            name,
            re.I,
        )
        if match:
            result.append(
                _fact(
                    "diameter_mm",
                    int(match.group("outer")),
                    source="name",
                    field="name",
                    raw=match.group(0),
                    parser="pipe_outer_diameter",
                    unit="mm",
                )
            )

    if "secondary_metric_size" in parsers:
        match = re.search(r"(?<![/\d])(?P<a>\d{2,3})\s*[*xх-]\s*(?P<b>\d{2,4})(?!\d)", name, re.I)
        if match:
            second = int(match.group("b"))
            fact_name = "length_mm" if kind == ProductKind.SEWER_PIPE else "secondary_diameter_mm"
            result.append(_fact(fact_name, second, source="name", field="name", raw=match.group(0), parser="secondary_metric_size", unit="mm"))

    if "angle" in parsers:
        match = re.search(r"(?<!\d)(15|30|45|67|87|88|90)\s*(?:°|град)", name, re.I)
        if not match and kind == ProductKind.ELBOW:
            match = re.search(r"угольник\s+(15|30|45|67|87|88|90)\b", name_norm)
        if match:
            result.append(_fact("angle_deg", int(match.group(1)), source="name", field="name", raw=match.group(0), parser="angle", unit="deg"))

    if "explicit_length" in parsers and kind == ProductKind.PIPE:
        match = re.search(r"длин(?:а|ой)\s+(\d+(?:[.,]\d+)?)\s*м\b", description_norm)
        if match:
            result.append(_fact("length_mm", float(match.group(1).replace(",", ".")) * 1000, source="description", field="description", raw=match.group(0), parser="explicit_length", unit="mm"))

    if "sewer_scope" in parsers:
        scope = "external" if "наруж" in name_norm else "internal" if any(x in name_norm for x in ("htem", "htb", "htea", "htu")) else None
        result.append(_fact("sewer_scope", scope, source="name", field="name", raw=name, parser="sewer_scope"))

    if "pressure_class" in parsers:
        match = re.search(r"(?<![a-zа-яё])(?:pn|пн)\s*(\d{1,3})\b", name, re.I)
        if match:
            result.append(_fact("pressure_class", f"pn{match.group(1)}", source="name", field="name", raw=match.group(0), parser="pressure_class"))

    if "material_family" in parsers:
        material = next((canonical for marker, canonical in (("pex", "pex"), ("pe xa", "pex_a"), ("сшитого полиэтилена", "pex"), ("pp fiber", "ppr"), ("pp alux", "ppr"), ("pp r", "ppr"), ("полипропилен", "ppr"), ("биметал", "bimetal"), ("алюмин", "aluminium"), ("стал", "steel")) if marker in f"{name_norm} {description_norm}"), None)
        result.append(_fact("material", material, source="name", field="name", raw=name, parser="material_family"))

    if "pipe_service" in parsers and kind in {ProductKind.PIPE, ProductKind.PEX_PIPE}:
        service = normalize_fact_value("pipe_service", description_norm)
        supported_services = {"cold_water", "hot_water", "heating"}
        parsed_services = set(str(service).split())
        if parsed_services and parsed_services <= supported_services:
            result.append(
                _fact(
                    "pipe_service",
                    service,
                    source="description",
                    field="description",
                    raw=description,
                    parser="pipe_service",
                )
            )

    if "pipe_operating_point" in parsers and kind in {
        ProductKind.PIPE,
        ProductKind.PEX_PIPE,
    }:
        operating_point = _single_pipe_operating_point(description)
        if operating_point is not None:
            temperature, pressure, raw_point = operating_point
            result.extend(
                (
                    _fact(
                        "operating_temperature_c",
                        temperature,
                        source="description",
                        field="description",
                        raw=raw_point,
                        parser="pipe_single_operating_point",
                        unit="C",
                    ),
                    _fact(
                        "operating_pressure_bar",
                        pressure,
                        source="description",
                        field="description",
                        raw=raw_point,
                        parser="pipe_single_operating_point",
                        unit="bar",
                    ),
                )
            )
        cold_pressure_matches = list(
            _PIPE_COLD_WATER_PRESSURE_RE.finditer(description)
        )
        if len(cold_pressure_matches) == 1:
            match = cold_pressure_matches[0]
            result.append(
                _fact(
                    "cold_water_pressure_bar",
                    _parsed_number(match.group("pressure")),
                    source="description",
                    field="description",
                    raw=match.group(0),
                    parser="pipe_explicit_cold_water_pressure",
                    unit="bar",
                )
            )

    if "pipe_reinforcement" in parsers and kind in {
        ProductKind.PIPE,
        ProductKind.PEX_PIPE,
    }:
        for source, field, raw in (
            ("name", "name", name),
            ("description", "description", description),
        ):
            reinforcement = normalize_fact_value("reinforcement", raw)
            if reinforcement in {"glass_fiber", "aluminium", "unreinforced"}:
                result.append(
                    _fact(
                        "reinforcement",
                        reinforcement,
                        source=source,
                        field=field,
                        raw=raw,
                        parser="pipe_reinforcement",
                    )
                )
                break

    if "inch_size" in parsers:
        match = re.search(r"(?<!\d)(\d+(?:\s+\d+/\d+)?|\d+/\d+)\s*[\"″]", name)
        if match:
            result.append(_fact("connection_size", " ".join(match.group(1).split()), source="name", field="name", raw=match.group(0), parser="inch_size", unit="inch"))

    if "port_count" in parsers:
        numeric_match = re.search(
            r"(?<!\d)(?P<count>[2-9])\s*(?:[-–—]\s*[xх]?\s*|[xх]\s*)?"
            r"(?:way|ways|ход(?:а|ов|овой|овый)?|port|ports|"
            r"порт(?:а|ов|овый)?)\b",
            name,
            re.I,
        )
        word_match = re.search(
            r"(?:three|тр[её]х)\s*(?:[-–—]\s*)?"
            r"(?:way|ход(?:овой|овый)?|port|порт(?:овый)?)\b",
            name,
            re.I,
        )
        match = numeric_match or word_match
        if match:
            result.append(
                _fact(
                    "port_count",
                    int(numeric_match.group("count")) if numeric_match else 3,
                    source="name",
                    field="name",
                    raw=match.group(0),
                    parser="port_count",
                )
            )

    if "washable" in parsers:
        for source, field, raw in (
            ("name", "name", name),
            ("description", "description", description),
        ):
            washable = normalize_fact_value("washable", raw)
            if isinstance(washable, bool):
                result.append(
                    _fact(
                        "washable",
                        washable,
                        source=source,
                        field=field,
                        raw=raw,
                        parser="washable",
                    )
                )
                break

    if "filter_method" in parsers:
        name_method = normalize_fact_value("filter_method", name_norm)
        description_method = normalize_fact_value("filter_method", description_norm)
        supported_methods = {
            "mechanical",
            "magnetic",
            "reverse_osmosis",
            "carbon",
            "softening",
            "iron_removal",
        }
        method = (
            name_method
            if name_method in supported_methods
            else description_method
        )
        if method in {
            "mechanical",
            "magnetic",
            "reverse_osmosis",
            "carbon",
            "softening",
            "iron_removal",
        }:
            from_name = name_method in supported_methods
            result.append(_fact(
                "filter_method",
                method,
                source="name" if from_name else "description",
                field="name" if from_name else "description",
                raw=name if from_name else description,
                parser="filter_method",
            ))

    if "micron_rating" in parsers:
        micron_pattern = r"(?<![\d-])(\d+(?:[.,]\d+)?)(?!\s*[-–]\s*\d)\s*(?:мкм|micron|um)\b"
        name_match = re.search(micron_pattern, name, re.I)
        description_match = re.search(micron_pattern, description, re.I)
        match = name_match or description_match
        if match:
            rating = float(match.group(1).replace(",", "."))
            from_name = name_match is not None
            result.append(_fact(
                "micron_rating_um",
                int(rating) if rating.is_integer() else rating,
                source="name" if from_name else "description",
                field="name" if from_name else "description",
                raw=match.group(0),
                parser="micron_rating",
                unit="um",
            ))

    if "connection_pattern" in parsers:
        pattern = normalize_fact_value("connection_pattern", name)
        if pattern in {"female_female", "female_male", "male_female", "male_male"}:
            result.append(_fact("connection_pattern", pattern, source="name", field="name", raw=name, parser="connection_pattern"))

    if "straight_or_angle" in parsers:
        shape = "angle" if "углов" in name_norm else "straight" if "прям" in name_norm else None
        result.append(_fact("valve_shape", shape, source="name", field="name", raw=name, parser="straight_or_angle"))

    if "handle_type" in parsers:
        match = re.search(
            r"(?:рукоятк[а-я]*\s+бабочк[а-я]*|стальн[а-я]*\s+рукоятк[а-я]*)",
            name,
            re.IGNORECASE,
        )
        if match:
            # The value is the exact explicit title fragment, rather than an
            # inferred engineering classification of the handle.
            result.append(
                _fact(
                    "handle_type",
                    match.group(0),
                    source="name",
                    field="name",
                    raw=match.group(0),
                    parser="explicit_valve_handle_title",
                )
            )

    if "metric_thread" in parsers:
        match = re.search(r"\bм\s*(\d{1,2})\s*[xх]\s*(\d+(?:[.,]\d+)?)", description, re.I)
        if match:
            result.append(_fact("control_thread", f"M{match.group(1)}x{match.group(2).replace(',', '.')}", source="description", field="description", raw=match.group(0), parser="metric_thread"))

    if "pump_designation_diameter" in parsers or "pump_designation_head" in parsers:
        designation = parse_pump_designation(name)
        for fact_name, (fact_value, unit, raw) in designation.items():
            if fact_name == "diameter_mm" and "pump_designation_diameter" not in parsers:
                continue
            if fact_name == "max_head_m" and "pump_designation_head" not in parsers:
                continue
            result.append(
                _fact(
                    fact_name,
                    fact_value,
                    source="name",
                    field="name",
                    raw=raw,
                    parser=(
                        "pump_mounting_length"
                        if fact_name == "mounting_length_mm"
                        else f"pump_designation_{'diameter' if fact_name == 'diameter_mm' else 'head'}"
                    ),
                    unit=unit,
                )
            )

    if "power_kw" in parsers:
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*квт\b", name_norm)
        if match:
            result.append(_fact("power_kw", float(match.group(1).replace(",", ".")), source="name", field="name", raw=match.group(0), parser="power_kw", unit="kW"))

    if "boiler_fuel" in parsers:
        fuel = "gas" if "газов" in name_norm else "electric" if "электр" in name_norm else None
        result.append(_fact("boiler_type", fuel, source="name", field="name", raw=name, parser="boiler_fuel"))

    if "circuit_count" in parsers:
        circuits = 2 if "двухконтур" in name_norm else 1 if "одноконтур" in name_norm or "1 контур" in name_norm else None
        result.append(_fact("circuits", circuits, source="name", field="name", raw=name, parser="circuit_count"))

    if "integrated_circulation_pump" in parsers:
        state = builtin_part_state_from_text(description, "насос")
        result.append(
            _fact(
                "integrated_circulation_pump",
                state,
                source="description",
                field="description",
                raw=description,
                parser="integrated_circulation_pump",
            )
        )

    if "combustion_chamber" in parsers:
        chamber = "closed" if "закр" in name_norm and "камер" in name_norm else None
        result.append(_fact("combustion_chamber", chamber, source="name", field="name", raw=name, parser="combustion_chamber"))

    return [item for item in result if item is not None]


def normalize_catalog_product(
    product: Product,
    registry: ProductContractRegistry,
) -> CatalogProductSnapshot:
    attrs = {normalize_identity(key): value for key, value in (product.attributes_normalized or {}).items()}
    product_type = str(attrs.get("тип товара") or "")
    kind, role, unsupported = registry.classify_catalog_identity(
        category=product.category_path,
        product_type=product_type,
        name=product.name,
    )
    contract = registry.for_kind(kind)
    facts: list[CatalogFact] = []
    fact_issues: list[CatalogFactIssue] = []
    flow_head_points: list[CatalogFlowHeadPoint] = []
    facts.append(_fact("sku", product.sku, source="identity", field="sku", raw=product.sku, parser="catalog_identity"))
    brand = _fact(
        "brand",
        normalize_fact_value("brand", product.brand),
        source="attribute",
        field="brand",
        raw=product.brand,
        parser="product_brand_field",
    )
    if brand is not None:
        facts.append(brand)
    identity_facts: dict[ProductKind, tuple[str, str]] = {
        ProductKind.GAS_BOILER: ("boiler_type", "gas"),
        ProductKind.ELECTRIC_BOILER: ("boiler_type", "electric"),
        ProductKind.CIRCULATION_PUMP: ("pump_type", "circulation"),
        ProductKind.DHW_CIRCULATION_PUMP: ("pump_type", "dhw_circulation"),
        ProductKind.BOREHOLE_PUMP: ("pump_type", "borehole"),
        ProductKind.DRAINAGE_PUMP: ("pump_type", "drainage"),
        ProductKind.SEWAGE_PUMP: ("pump_type", "sewage"),
        ProductKind.PUMP_STATION: ("pump_type", "pump_station"),
        ProductKind.PEX_PIPE: ("material", "pex"),
    }
    identity_fact = identity_facts.get(kind)
    if identity_fact is not None:
        facts.append(
            _fact(
                identity_fact[0],
                identity_fact[1],
                source="identity",
                field="product_kind",
                raw=kind.value,
                parser="catalog_product_kind",
            )
        )
    if contract is not None:
        ambiguous_structured_fact_names: set[str] = set()
        for definition in contract.fact_definitions:
            if definition.name == "sku":
                continue
            for field in definition.catalog_fields:
                raw = attrs.get(normalize_identity(field))
                if raw not in (None, ""):
                    if definition.name in _SCALAR_NUMERIC_FACT_NAMES:
                        _scalar, ambiguous = _structured_scalar_number(
                            definition.name,
                            raw,
                        )
                        if ambiguous:
                            ambiguous_structured_fact_names.add(definition.name)
                            fact_issues.append(
                                CatalogFactIssue(
                                    name=definition.name,
                                    provenance=FactProvenance(
                                        source="attribute",
                                        source_field=field,
                                        raw_value=str(raw)[:500],
                                        parser="structured_attribute_ambiguous",
                                    ),
                                )
                            )
                            break
                    parsed = _structured_fact(definition.name, raw, field)
                    if parsed is not None:
                        facts.append(parsed)
                    break
        parsers = {parser for definition in contract.fact_definitions for parser in definition.general_parsers}
        generic_facts = _generic_facts(product, kind, parsers)
        rating_names = {
            "operating_temperature_c",
            "operating_pressure_bar",
        }
        has_structured_rating = any(
            fact.name in rating_names and fact.provenance.source == "attribute"
            for fact in facts
        )
        structured_rating_ambiguous = bool(
            ambiguous_structured_fact_names & rating_names
        )
        facts.extend(
            fact
            for fact in generic_facts
            if fact.name not in ambiguous_structured_fact_names
            and not (
                fact.provenance.parser == "pipe_single_operating_point"
                and (has_structured_rating or structured_rating_ambiguous)
            )
        )

    # A passport table may safely supplement a missing card rating only after
    # its parser has proved the exact model/row.  It never rewrites the feed:
    # a conflicting card value becomes a source issue and is unusable by both
    # Selection and ProductFact until reconciled.
    for document_fact in product.document_facts:
        if contract is None or not any(
            definition.name == document_fact.name
            for definition in contract.fact_definitions
        ):
            continue
        matching = [item for item in facts if item.name == document_fact.name]
        document_catalog_fact = _fact(
            document_fact.name,
            document_fact.value,
            unit=document_fact.unit,
            source="passport",
            field=document_fact.name,
            raw=document_fact.evidence,
            parser=document_fact.parser,
            source_document=document_fact.document,
            source_section=document_fact.section,
        )
        if document_catalog_fact is None:
            continue
        if not matching:
            facts.append(document_catalog_fact)
            continue
        known_values = {(str(item.value), item.unit) for item in matching}
        if (str(document_catalog_fact.value), document_catalog_fact.unit) not in known_values:
            fact_issues.append(
                CatalogFactIssue(
                    name=document_fact.name,
                    provenance=document_catalog_fact.provenance,
                )
            )

    # Q/H points are a relation rather than ordinary scalar attributes. They
    # can only come from an exact model row in a document. A conflict at one
    # flow is fail-closed: the planner will never choose between sources.
    points_by_flow: dict[float, CatalogFlowHeadPoint] = {}
    conflicting_flows: set[float] = set()
    for document_point in product.document_flow_head_points:
        point = CatalogFlowHeadPoint(
            flow_l_h=float(document_point.flow_l_h),
            head_m=float(document_point.head_m),
            provenance=FactProvenance(
                source="passport",
                source_field="flow_head_curve",
                raw_value=document_point.evidence,
                parser=document_point.parser,
                source_document=document_point.document,
                source_section=document_point.section,
            ),
        )
        previous = points_by_flow.get(point.flow_l_h)
        if previous is None:
            points_by_flow[point.flow_l_h] = point
        elif previous.head_m != point.head_m:
            conflicting_flows.add(point.flow_l_h)
    for flow_l_h in conflicting_flows:
        points_by_flow.pop(flow_l_h, None)
        fact_issues.append(
            CatalogFactIssue(
                name="flow_head_curve",
                provenance=FactProvenance(
                    source="passport",
                    source_field="flow_head_curve",
                    raw_value=f"conflicting document points at Q={flow_l_h:g} l/h",
                    parser="document_flow_head_conflict",
                ),
            )
        )
    flow_head_points.extend(
        points_by_flow[item] for item in sorted(points_by_flow)
    )

    unique: dict[str, CatalogFact] = {}
    for fact in facts:
        unique.setdefault(fact.name, fact)
    return CatalogProductSnapshot(
        sku=product.sku,
        name=product.name,
        category=product.category_path,
        product_kind=kind,
        role=role,
        stock_status=product.stock_status or None,
        stock_qty=product.stock_qty,
        facts=tuple(unique.values()),
        fact_issues=tuple(
            {
                (item.name, item.provenance.source_field): item
                for item in fact_issues
            }.values()
        ),
        flow_head_points=tuple(flow_head_points),
        unsupported_reason=unsupported,
    )


def build_catalog_snapshot(
    products: Iterable[Product],
    registry: ProductContractRegistry | None = None,
) -> tuple[CatalogProductSnapshot, ...]:
    selected_registry = registry or ProductContractRegistry()
    return tuple(
        normalize_catalog_product(product, selected_registry)
        for product in products
    )
