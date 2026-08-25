"""Source-preserving, deterministic catalogue fact normalization."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.models import Product

from .contracts import (
    CatalogFact,
    CatalogProductSnapshot,
    FactProvenance,
    ProductKind,
)
from .registry import ProductContractRegistry, normalize_identity


_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _number(value: object) -> float | int | None:
    match = _NUMBER_RE.search(str(value or ""))
    if not match:
        return None
    parsed = float(match.group(0).replace(",", "."))
    return int(parsed) if parsed.is_integer() else parsed


def _fact(
    name: str,
    value: str | int | float | bool | None,
    *,
    source: str,
    field: str,
    raw: object,
    parser: str,
    unit: str | None = None,
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
        ),
    )


def _structured_fact(name: str, raw: object, field: str) -> CatalogFact | None:
    numeric_names = {
        "diameter_mm",
        "mounting_length_mm",
        "max_head_m",
        "max_flow_l_h",
        "power_kw",
        "center_distance_mm",
        "heat_output_w",
        "suction_depth_m",
        "circuits",
    }
    unit = {
        "diameter_mm": "mm",
        "mounting_length_mm": "mm",
        "max_head_m": "m",
        "max_flow_l_h": "l/h",
        "power_kw": "kW",
        "center_distance_mm": "mm",
        "heat_output_w": "W",
        "suction_depth_m": "m",
    }.get(name)
    value: object = _number(raw) if name in numeric_names else normalize_fact_value(name, raw)
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

    if isinstance(value, (int, float, bool)):
        return value
    text = normalize_identity(value)
    if name == "connection_pattern":
        if any(marker in text for marker in ("внутренняя/внутренняя", "вн. вн", "ff")):
            return "female_female"
        if any(marker in text for marker in ("внутренняя/наружная", "вн. нар", "fm")):
            return "female_male"
    if name == "valve_shape":
        if "углов" in text:
            return "angle"
        if "прям" in text:
            return "straight"
    if name == "boiler_type":
        if "газ" in text:
            return "gas"
        if "электр" in text:
            return "electric"
    if name == "sewer_scope":
        if "наруж" in text or "external" in text:
            return "external"
        if "внутр" in text or "internal" in text:
            return "internal"
    if name == "circuits":
        if "двух" in text or text == "2":
            return 2
        if "одно" in text or text == "1":
            return 1
    return text


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
        match = re.search(r"\bpn\s*(\d{1,3})\b", name, re.I)
        if match:
            result.append(_fact("pressure_class", f"PN{match.group(1)}", source="name", field="name", raw=match.group(0), parser="pressure_class"))

    if "material_family" in parsers:
        material = next((canonical for marker, canonical in (("pp fiber", "pp_fiber"), ("pp alux", "pp_alux"), ("pp r", "ppr"), ("биметал", "bimetal"), ("алюмин", "aluminium"), ("стал", "steel")) if marker in f"{name_norm} {description_norm}"), None)
        result.append(_fact("material", material, source="name", field="name", raw=name, parser="material_family"))

    if "inch_size" in parsers:
        match = re.search(r"(?<!\d)(\d+(?:\s+\d+/\d+)?|\d+/\d+)\s*[\"″]", name)
        if match:
            result.append(_fact("connection_size", " ".join(match.group(1).split()), source="name", field="name", raw=match.group(0), parser="inch_size", unit="inch"))

    if "connection_pattern" in parsers:
        pattern = normalize_fact_value("connection_pattern", name)
        if pattern in {"female_female", "female_male"}:
            result.append(_fact("connection_pattern", pattern, source="name", field="name", raw=name, parser="connection_pattern"))

    if "straight_or_angle" in parsers:
        shape = "angle" if "углов" in name_norm else "straight" if "прям" in name_norm else None
        result.append(_fact("valve_shape", shape, source="name", field="name", raw=name, parser="straight_or_angle"))

    if "metric_thread" in parsers:
        match = re.search(r"\bм\s*(\d{1,2})\s*[xх]\s*(\d+(?:[.,]\d+)?)", description, re.I)
        if match:
            result.append(_fact("control_thread", f"M{match.group(1)}x{match.group(2).replace(',', '.')}", source="description", field="description", raw=match.group(0), parser="metric_thread"))

    if "pump_designation_diameter" in parsers or "pump_designation_head" in parsers:
        match = re.search(r"(?<!\d)(\d{2})\s*/\s*(\d{1,2})(?:\s*-\s*(\d{2,3}))?", name)
        if match:
            if "pump_designation_diameter" in parsers:
                result.append(_fact("diameter_mm", int(match.group(1)), source="name", field="name", raw=match.group(0), parser="pump_designation_diameter", unit="mm"))
            if "pump_designation_head" in parsers:
                result.append(_fact("max_head_m", int(match.group(2)), source="name", field="name", raw=match.group(0), parser="pump_designation_head", unit="m"))
            if match.group(3):
                result.append(_fact("mounting_length_mm", int(match.group(3)), source="name", field="name", raw=match.group(0), parser="pump_mounting_length", unit="mm"))

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
    facts.append(_fact("sku", product.sku, source="identity", field="sku", raw=product.sku, parser="catalog_identity"))
    if contract is not None:
        for definition in contract.fact_definitions:
            if definition.name == "sku":
                continue
            for field in definition.catalog_fields:
                raw = attrs.get(normalize_identity(field))
                if raw not in (None, ""):
                    parsed = _structured_fact(definition.name, raw, field)
                    if parsed is not None:
                        facts.append(parsed)
                    break
        parsers = {parser for definition in contract.fact_definitions for parser in definition.general_parsers}
        facts.extend(_generic_facts(product, kind, parsers))

    unique: dict[str, CatalogFact] = {}
    for fact in facts:
        unique.setdefault(fact.name, fact)
    return CatalogProductSnapshot(
        sku=product.sku,
        name=product.name,
        category=product.category_path,
        product_kind=kind,
        role=role,
        facts=tuple(unique.values()),
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
