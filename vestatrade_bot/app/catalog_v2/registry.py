"""Declarative product-kind registry for semantic and catalogue identities."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.dialogue_v2.contracts import CustomerTask, DialogueStateV2, ProductGoal
from app.sku_resolution import SkuResolutionStatus, resolve_catalog_sku

from .contracts import (
    CatalogProductSnapshot,
    CatalogProductRole,
    ComparisonMode,
    ContractFactDefinition,
    ContractResolution,
    ContractResolutionStatus,
    FactStrength,
    FactValueType,
    ProductContract,
    ProductKind,
)


_CATALOG_ACTS = (
    "find",
    "select",
    "compare",
    "check_price",
    "check_stock",
    "get_link",
)


def normalize_identity(value: object) -> str:
    return " ".join(
        str(value or "")
        .casefold()
        .replace("ё", "е")
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


# One catalogue-bound vocabulary for brand facts.  These are aliases for
# values already present in feed100, not an alternative product ontology or a
# fuzzy manufacturer search.  The semantic layer consumes this table through
# the helpers below; catalogue normalization uses the same canonical values.
BRAND_VALUE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARISTON", ("ariston", "аристон")),
    ("ARDERIA", ("arderia", "ардерия")),
    ("E.C.A", ("e.c.a", "e c a", "eca", "е.с.а", "е с а", "еса")),
    ("OSTENDORF", ("ostendorf", "остендорф")),
    ("ROMMER", ("rommer", "роммер")),
    ("THERMEX", ("thermex", "термекс")),
    ("UNIPUMP", ("unipump", "uni pump", "юнипамп")),
    ("VALTEC", ("valtec", "валтек", "вальтек")),
    ("WILO", ("wilo", "вило")),
    ("ВИХРЬ", ("вихрь", "vihr", "vikhr")),
    ("ХЕМКОР", ("хемкор", "hemkor")),
)


@dataclass(frozen=True)
class BrandMention:
    """One exact, catalogue-known brand mention in customer text."""

    canonical: str
    alias: str
    start: int
    end: int


def canonical_brand(value: object) -> str | None:
    """Resolve one full brand value through the shared feed100 vocabulary.

    Unknown strings remain unknown rather than becoming a fuzzy supplier
    constraint.  This makes a typo safe: the interpreter can ask or leave it
    unresolved instead of silently filtering the catalogue to nothing.
    """

    identity = normalize_identity(value)
    if not identity:
        return None
    for canonical, aliases in BRAND_VALUE_ALIASES:
        known_values = {
            normalize_identity(canonical),
            *(normalize_identity(item) for item in aliases),
        }
        if identity in known_values:
            return canonical
    return None


def resolve_brand_mentions(text: str) -> tuple[BrandMention, ...]:
    """Find exact aliases with source spans; never use unrestricted fuzzy text."""

    candidates: list[BrandMention] = []
    for canonical, aliases in BRAND_VALUE_ALIASES:
        for alias in (canonical, *aliases):
            pattern = re.compile(
                rf"(?<![0-9A-Za-zА-Яа-яЁё]){re.escape(alias)}(?![0-9A-Za-zА-Яа-яЁё])",
                re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                candidates.append(
                    BrandMention(
                        canonical=canonical,
                        alias=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )
    # Prefer the longest concrete alias at one span and do not double-count a
    # canonical name that is also listed as an alias.
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.canonical))
    resolved: list[BrandMention] = []
    occupied: list[tuple[int, int]] = []
    for item in candidates:
        if any(start <= item.start and item.end <= end for start, end in occupied):
            continue
        resolved.append(item)
        occupied.append((item.start, item.end))
    return tuple(resolved)


def brand_ontology_values() -> tuple[dict[str, object], ...]:
    """Expose the same value vocabulary to the bounded semantic prompt."""

    return tuple(
        {"value": canonical, "aliases": list(aliases)}
        for canonical, aliases in BRAND_VALUE_ALIASES
    )


def _fact(
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    value_type: FactValueType = FactValueType.NUMBER,
    unit_family: str | None = None,
    strength: FactStrength = FactStrength.HARD,
    required: bool = False,
    decision: bool = False,
    preliminary: bool = True,
    comparison: ComparisonMode = ComparisonMode.NUMERIC,
    fields: tuple[str, ...] = (),
    candidate_filterable: bool = True,
    candidate_evidence_required: bool = False,
    candidate_fact_name: str | None = None,
    candidate_required_when_missing: str | None = None,
    preliminary_only_for_exact: bool = False,
    parsers: tuple[str, ...] = (),
    learn: str | None = None,
    catalog_verifiable: bool = True,
) -> ContractFactDefinition:
    conversions = {
        "length_mm": {"mm": 1.0, "cm": 10.0, "m": 1000.0},
        "length_m": {"m": 1.0, "cm": 0.01, "mm": 0.001},
        "head_m": {"m": 1.0, "cm": 0.01},
        "angle_deg": {"deg": 1.0, "°": 1.0},
        "power_kw": {"kw": 1.0, "квт": 1.0, "w": 0.001, "вт": 0.001},
        "area_m2": {"m2": 1.0, "м2": 1.0, "m²": 1.0, "м²": 1.0},
        "power_w": {"w": 1.0, "вт": 1.0, "kw": 1000.0, "квт": 1000.0},
        "volume_l": {
            "l": 1.0,
            "л": 1.0,
            "литр": 1.0,
            "литра": 1.0,
            "литров": 1.0,
        },
        "flow": {"l/h": 1.0, "л/ч": 1.0, "l/min": 60.0, "л/мин": 60.0, "m3/h": 1000.0, "м3/ч": 1000.0},
        "percent": {"%": 1.0, "percent": 1.0, "процент": 1.0},
        "pressure_bar": {
            "bar": 1.0,
            "бар": 1.0,
            "mpa": 10.0,
            "мпа": 10.0,
            "kpa": 0.01,
            "кпа": 0.01,
            "pa": 0.00001,
            "па": 0.00001,
            "atm": 1.01325,
            "атм": 1.01325,
        },
    }.get(unit_family or "", {})
    return ContractFactDefinition(
        name=name,
        aliases=aliases,
        value_type=value_type,
        unit_family=unit_family,
        unit_conversions=conversions,
        strength=strength,
        required_for_exact=required,
        decision_changing=decision,
        preliminary_allowed_without=preliminary,
        comparison=comparison,
        catalog_fields=fields,
        candidate_filterable=candidate_filterable,
        candidate_evidence_required=candidate_evidence_required,
        candidate_fact_name=candidate_fact_name,
        candidate_required_when_missing=candidate_required_when_missing,
        preliminary_only_for_exact=preliminary_only_for_exact,
        general_parsers=parsers,
        learn_method_code=learn,
        catalog_verifiable=catalog_verifiable,
    )


SKU = _fact(
    "sku",
    aliases=("article", "артикул"),
    value_type=FactValueType.TEXT,
    comparison=ComparisonMode.EXACT,
    fields=("артикул",),
)
DIAMETER = _fact(
    "diameter_mm",
    aliases=(
        "diameter",
        "connection_diameter",
        "pipe_diameter",
        "main_diameter",
        "nominal_diameter",
        "pipe_diameter_from",
    ),
    unit_family="length_mm",
    required=True,
    decision=True,
    fields=("диаметр (мм)", "диаметр условного прохода"),
    parsers=("primary_metric_size", "pump_designation_diameter"),
    learn="measure_outer_or_nominal_diameter",
)
PUMP_DIAMETER = DIAMETER.model_copy(
    update={
        "aliases": tuple(
            dict.fromkeys(
                (
                    *DIAMETER.aliases,
                    "connection_diameter_mm",
                    "connection_size",
                    "connection",
                    "inlet_connection_diameter",
                    "inlet_diameter",
                    "присоединение",
                    "присоединение_вход",
                )
            )
        )
    }
)
SECONDARY_DIAMETER = _fact(
    "secondary_diameter_mm",
    aliases=("second_diameter", "branch_diameter", "outlet_diameter", "pipe_diameter_to"),
    unit_family="length_mm",
    required=True,
    decision=True,
    parsers=("secondary_metric_size",),
    learn="measure_second_connection_diameter",
)
ANGLE = _fact(
    "angle_deg",
    aliases=("angle", "bend_angle"),
    unit_family="angle_deg",
    required=True,
    decision=True,
    fields=("угол (градусы)",),
    parsers=("angle",),
    learn="read_angle_marking",
)
LENGTH = _fact(
    "length_mm",
    aliases=("length", "pipe_length", "mount_length"),
    unit_family="length_mm",
    required=True,
    decision=True,
    parsers=("secondary_metric_size", "explicit_length"),
    learn="measure_product_length",
)
SEWER_SCOPE = _fact(
    "sewer_scope",
    aliases=("installation_scope", "sewer_type"),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    parsers=("sewer_scope",),
    learn="identify_internal_or_external_sewer",
)
PRESSURE_CLASS = _fact(
    "pressure_class",
    aliases=("pn", "pipe_pn"),
    value_type=FactValueType.TEXT,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.EXACT,
    parsers=("pressure_class",),
)
MATERIAL = _fact(
    "material",
    aliases=("pipe_material", "radiator_material"),
    value_type=FactValueType.TEXT,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.CONTAINS,
    fields=("материал", "материал корпуса"),
    parsers=("material_family",),
)
CONNECTION_SIZE = _fact(
    "connection_size",
    aliases=(
        "thread_size",
        "connection_thread_size",
        "nominal_thread_size",
        "port_size",
        "connection_diameter_inch",
        "thread",
        "резьба",
        "размер_резьбы",
    ),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    fields=("диаметр подключения, дюйм", "присоединительная резьба, дюйм"),
    parsers=("inch_size",),
    learn="read_connection_marking",
)
CONNECTION_PATTERN = _fact(
    "connection_pattern",
    aliases=("thread_pair", "connection_type", "thread_type"),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    fields=("тип резьбы", "тип присоединения"),
    parsers=("connection_pattern",),
    learn="inspect_both_connection_threads",
)
VALVE_SHAPE = _fact(
    "valve_shape",
    aliases=("shape", "body_shape", "installation_shape"),
    value_type=FactValueType.TEXT,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.EXACT,
    fields=("форма корпуса", "тип конструкции"),
    parsers=("straight_or_angle",),
)
HANDLE_TYPE = _fact(
    "handle_type",
    aliases=("handle", "lever", "ручка", "рукоятка", "бабочка"),
    value_type=FactValueType.TEXT,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.EXACT,
    parsers=("handle_type",),
)
CONTROL_THREAD = _fact(
    "control_thread",
    aliases=("head_thread", "thermostatic_thread", "connection_thread"),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    parsers=("metric_thread",),
    learn="read_valve_or_head_thread",
)
MOUNTING_LENGTH = _fact(
    "mounting_length_mm",
    aliases=("mounting_length", "installation_length", "length"),
    unit_family="length_mm",
    required=True,
    decision=True,
    fields=("монтажная длина, мм",),
    parsers=("pump_mounting_length",),
    learn="measure_old_pump_mounting_length",
)
MAX_HEAD = _fact(
    "max_head_m",
    aliases=(
        "head",
        "required_head",
        "head_m",
        "maximum_head",
        "lift_height_m",
        # Semantic models commonly call a pump head expressed in metres
        # "pressure".  Readiness accepts this alias only with a head/length
        # unit, so a pressure in bar is never silently converted to metres.
        "pressure",
        "required_pressure",
        "pressure_head",
        "system_head",
        "напор",
    ),
    unit_family="head_m",
    required=True,
    decision=True,
    fields=("максимальный напор, м", "высота напора, м"),
    parsers=("pump_designation_head",),
    learn="estimate_required_system_head",
)
MAX_FLOW = _fact(
    "max_flow_l_h",
    aliases=(
        "flow",
        "flow_rate",
        "required_flow_l_h",
        "required_flow_l_min",
        "required_flow_m3_h",
        "подача",
        "расход",
    ),
    unit_family="flow",
    strength=FactStrength.SOFT,
    fields=("макс. производительность, л/ч",),
)
# The source is a customer-side fact, not an attribute of a catalogue card.
# It belongs to the existing generic pump contract so the same registry is the
# authority for semantic grounding, readiness and telemetry.  It becomes
# required only for the ``irrigation_pump`` selection goal in readiness below.
PUMP_WATER_SOURCE = _fact(
    "water_source",
    aliases=("source_water", "источник воды", "откуда вода"),
    value_type=FactValueType.TEXT,
    decision=True,
    comparison=ComparisonMode.EXACT,
    candidate_filterable=False,
    catalog_verifiable=False,
    learn="identify_irrigation_water_source",
)
PUMP_TYPE = _fact(
    "pump_type",
    aliases=("pump_application", "назначение насоса", "тип насоса"),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    learn="identify_pump_application",
)
OPEN_WATER_REQUIRED_HEAD = MAX_HEAD.model_copy(
    update={"learn_method_code": "estimate_open_water_delivery_head"}
)
# The borehole contract deliberately distinguishes the customer's required
# duty from catalogue *maximum* ratings. A maximum head/flow can reject a
# clearly insufficient pump, but it does not prove the working point on a
# manufacturer's Q/H curve; readiness therefore stays preliminary.
BOREHOLE_REQUIRED_HEAD = _fact(
    "required_head_m",
    aliases=("head_m", "required_head", "расчётный напор", "нужный напор"),
    unit_family="head_m",
    required=True,
    decision=True,
    preliminary=False,
    comparison=ComparisonMode.MINIMUM_RATING,
    candidate_fact_name="max_head_m",
    candidate_evidence_required=True,
    catalog_verifiable=False,
    learn="calculate_borehole_required_head",
)
BOREHOLE_REQUIRED_FLOW = _fact(
    "required_flow_l_h",
    aliases=(
        "required_flow_m3_h",
        "required_flow_l_min",
        "flow",
        "flow_rate",
        "требуемый расход",
        "нужный расход",
    ),
    unit_family="flow",
    required=True,
    decision=True,
    preliminary=False,
    comparison=ComparisonMode.MINIMUM_RATING,
    candidate_fact_name="max_flow_l_h",
    candidate_evidence_required=True,
    catalog_verifiable=False,
    learn="confirm_borehole_required_flow",
)
# Source-only card ratings. They stay distinct from the customer requirements
# above, yet make their existing feed extractors available to the common
# catalogue snapshot builder.
BOREHOLE_CATALOG_MAX_HEAD = MAX_HEAD.model_copy(
    update={
        "required_for_exact": False,
        "decision_changing": False,
        "candidate_filterable": False,
    }
)
BOREHOLE_CATALOG_MAX_FLOW = MAX_FLOW.model_copy(
    update={
        "required_for_exact": False,
        "decision_changing": False,
        "candidate_filterable": False,
    }
)

# These inputs exist in the same contract to make their semantic meaning and
# units canonical. They are never catalogue filters: the shared deterministic
# hydraulic adapter converts them into ``required_head_m`` or asks for the one
# next missing input.
BOREHOLE_WATER_LEVEL = _fact(
    "dynamic_water_level_m",
    aliases=("уровень воды", "динамический уровень"),
    unit_family="length_m",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="measure_dynamic_water_level",
)
BOREHOLE_STATIC_WATER_LEVEL = _fact(
    "static_water_level_m",
    aliases=("статический уровень",),
    unit_family="length_m",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="measure_static_water_level",
)
BOREHOLE_LIFT_HEIGHT = _fact(
    "lift_height_m",
    aliases=("высота подъёма", "подъём до дома", "высота до верхней точки"),
    unit_family="length_m",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="measure_borehole_lift_height",
)
BOREHOLE_HORIZONTAL_RUN = _fact(
    "horizontal_run_m",
    aliases=("длина трассы", "горизонтальная трасса", "от скважины до дома"),
    unit_family="length_m",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="measure_borehole_horizontal_run",
)
BOREHOLE_REQUIRED_PRESSURE = _fact(
    "required_pressure_bar",
    aliases=("давление в доме", "требуемое давление", "нужное давление"),
    unit_family="pressure_bar",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="confirm_borehole_required_pressure",
)
BOREHOLE_DISCHARGE_DIAMETER = _fact(
    "discharge_diameter_mm",
    aliases=("диаметр напорной трубы", "диаметр трубы от насоса"),
    unit_family="length_mm",
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="measure_borehole_discharge_diameter",
)
BOREHOLE_DISCHARGE_SDR = _fact(
    "discharge_sdr",
    aliases=("sdr трубы", "sdr"),
    catalog_verifiable=False,
    candidate_filterable=False,
    learn="read_borehole_discharge_sdr",
)
DUTY_POINT_HEAD = _fact(
    "duty_point_head_m",
    aliases=("duty_head_m", "working_point_head_m", "required_duty_head_m"),
    unit_family="head_m",
    decision=True,
    catalog_verifiable=False,
)
DUTY_POINT_FLOW = _fact(
    "duty_point_flow_l_h",
    aliases=(
        "duty_flow_l_h",
        "working_point_flow_l_h",
        "required_duty_flow_l_h",
    ),
    unit_family="flow",
    decision=True,
    catalog_verifiable=False,
)
POWER_KW = _fact(
    "power_kw",
    aliases=("power", "boiler_power_kw"),
    unit_family="power_kw",
    required=True,
    decision=True,
    fields=("мощность, квт",),
    parsers=("power_kw",),
    learn="calculate_heat_loss_or_read_project_power",
)
# ``area_m2`` belongs to the customer's heating task, not to the product
# itself.  It can describe a house for a boiler or a room for a radiator; in
# both cases it is intentionally not a universal power calculation.  The
# planner compares it only with the documented coverage field of a card.  It
# consequently authorises a preliminary result, never an exact engineering
# recommendation.
BUILDING_AREA = _fact(
    "area_m2",
    aliases=(
        "heated_area_m2",
        "building_area_m2",
        "heating_area_m2",
        "area",
        "площадь",
        "отапливаемая площадь",
    ),
    unit_family="area_m2",
    strength=FactStrength.HARD,
    decision=True,
    comparison=ComparisonMode.MINIMUM_RATING,
    candidate_fact_name="declared_heated_area_m2",
    candidate_required_when_missing="power_kw",
    # The customer's area is not a model attribute.  It is nevertheless
    # verifiable against the explicitly mapped declared model coverage.  When
    # it substitutes for an absent power, the selection stays preliminary and
    # never becomes a heat-loss calculation.
    preliminary_only_for_exact=True,
    learn="calculate_heat_loss_or_read_project_power",
)
DECLARED_HEATED_AREA = _fact(
    "declared_heated_area_m2",
    unit_family="area_m2",
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.MINIMUM_RATING,
    fields=(
        "отапливаемая площадь, м²",
        # Radiator cards use a different, but equally explicit, manufacturer
        # label.  Keeping it in the shared source-fact definition lets the
        # normalizer preserve provenance instead of a Legacy-only special
        # case.
        "площадь обогрева, м²",
        "площадь обогрева, м2",
    ),
)
FUEL_TYPE = _fact(
    "boiler_type",
    aliases=(
        "fuel_type",
        "boiler_fuel_type",
        "boiler_fuel",
        "fuel",
        "fuel_kind",
        "energy_source",
        "heating_source",
        "type",
        "тип",
    ),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    fields=("тип котла",),
    parsers=("boiler_fuel",),
    learn="identify_available_energy_source",
)
CIRCUITS = _fact(
    "circuits",
    aliases=(
        "circuit_count",
        "number_of_circuits",
        "contours",
        "contour_count",
        "needs_hot_water",
        "functionality",
        "functions",
        "контуры",
        "контур",
        "количество контуров",
        "сколько контуров",
        "число контуров",
        "функциональность",
    ),
    required=True,
    decision=True,
    # The choice between heating-only and DHW changes the boiler family.
    # A generic preliminary list cannot safely stand in for that decision:
    # the current renderer has no separate one-/two-circuit groups.  Keep
    # area as a declared-coverage preliminary proxy, but require this fact
    # even when the buyer asks to see options before completing the funnel.
    preliminary=False,
    fields=("количество контуров",),
    parsers=("circuit_count",),
    learn="decide_heating_only_or_dhw",
)
INTEGRATED_CIRCULATION_PUMP = _fact(
    "integrated_circulation_pump",
    aliases=(
        "built_in_pump",
        "builtin_pump",
        "integrated_pump",
        "circulation_pump_included",
        "встроенный насос",
        "насос в котле",
    ),
    value_type=FactValueType.BOOLEAN,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.EXACT,
    fields=("насос", "циркуляционный насос"),
    parsers=("integrated_circulation_pump",),
)
EXPANSION_TANK_VOLUME = _fact(
    "expansion_tank_volume_l",
    aliases=(
        "expansion_tank_volume",
        "объем расширительного бака",
        "объём расширительного бака",
        "емкость расширительного бака",
        "ёмкость расширительного бака",
    ),
    unit_family="volume_l",
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.NUMERIC,
    candidate_filterable=False,
    catalog_verifiable=False,
)
CHAMBER = _fact(
    "combustion_chamber",
    aliases=("chamber_type",),
    value_type=FactValueType.TEXT,
    strength=FactStrength.SOFT,
    comparison=ComparisonMode.CONTAINS,
    fields=("камера сгорания",),
    parsers=("combustion_chamber",),
)
APPLICATION = _fact(
    "application",
    aliases=(
        "intended_use",
        "working_medium",
        "service_medium",
        "medium",
        "fluid",
        "рабочая_среда",
        "среда",
        "назначение",
    ),
    value_type=FactValueType.TEXT,
    comparison=ComparisonMode.EXACT,
    fields=("рабочая среда",),
)
PIPE_SERVICE = _fact(
    "pipe_service",
    aliases=(
        "application",
        "application_type",
        "water_type",
        "service_type",
        "intended_use",
        "назначение",
        "применение",
    ),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.CONTAINS,
    parsers=("pipe_service",),
    learn="identify_pipe_service",
)
OPERATING_TEMPERATURE = _fact(
    "operating_temperature_c",
    aliases=(
        "max_operating_temperature_c",
        "maximum_operating_temperature_c",
        "max_temperature_c",
        "working_temperature_c",
        "temperature_c",
    ),
    unit_family="temperature_c",
    comparison=ComparisonMode.MINIMUM_RATING,
    fields=(
        "максимальная рабочая температура, °с",
        "макс. температура воды, °c",
        "максимальная температура воды, ℃",
    ),
)
OPERATING_PRESSURE = _fact(
    "operating_pressure_bar",
    aliases=(
        "max_operating_pressure_bar",
        "maximum_operating_pressure_bar",
        "max_pressure_bar",
        "working_pressure_bar",
        "pressure_bar",
    ),
    unit_family="pressure_bar",
    comparison=ComparisonMode.MINIMUM_RATING,
    fields=(
        "максимальное рабочее давление, бар",
        "рабочее давление, бар",
        "максимальное давление, бар",
        "максимальное давление в системе, бар",
        "давление, бар",
    ),
)
PIPE_OPERATING_TEMPERATURE = OPERATING_TEMPERATURE.model_copy(
    update={
        "required_for_exact": True,
        "decision_changing": True,
        "learn_method_code": "identify_pipe_operating_temperature",
        "general_parsers": tuple(
            dict.fromkeys(
                (*OPERATING_TEMPERATURE.general_parsers, "pipe_operating_point")
            )
        ),
    }
)
PIPE_OPERATING_PRESSURE = OPERATING_PRESSURE.model_copy(
    update={
        "required_for_exact": True,
        "decision_changing": True,
        "learn_method_code": "identify_pipe_operating_pressure",
        # ``docs_loader`` writes the proven operating-class value from a pipe
        # passport under this precise field.  It is a conservative heating
        # rating, not a PN inference, and must be visible to the same typed
        # catalogue path that consumes ordinary feed attributes.
        "catalog_fields": (
            *OPERATING_PRESSURE.catalog_fields,
            "рабочее давление, радиаторное отопление, бар",
        ),
        "general_parsers": tuple(
            dict.fromkeys(
                (*OPERATING_PRESSURE.general_parsers, "pipe_operating_point")
            )
        ),
    }
)
PIPE_REINFORCEMENT = _fact(
    "reinforcement",
    aliases=(
        "reinforcement_type",
        "pipe_reinforcement",
        "армирование",
        "тип_армирования",
    ),
    value_type=FactValueType.TEXT,
    comparison=ComparisonMode.EXACT,
    fields=("армирование", "тип армирования"),
    parsers=("pipe_reinforcement",),
)
BRAND = _fact(
    "brand",
    aliases=(
        "brand_name",
        "manufacturer",
        "manufacturer_name",
        "vendor",
        "бренд",
        "производитель",
    ),
    value_type=FactValueType.TEXT,
    comparison=ComparisonMode.EXACT,
)
PORT_COUNT = _fact(
    "port_count",
    aliases=(
        "ports",
        "number_of_ports",
        "port_number",
        "way_count",
        "ways",
        "количество_портов",
        "количество_ходов",
        "число_портов",
        "ходы",
    ),
    decision=True,
    fields=("количество портов", "количество ходов", "число портов"),
    parsers=("port_count",),
    learn="read_valve_port_count",
)
RADIATOR_MATERIAL = MATERIAL.model_copy(
    update={"required_for_exact": True, "decision_changing": True}
)
CENTER_DISTANCE = _fact(
    "center_distance_mm",
    aliases=("axis_distance", "interaxial_distance"),
    unit_family="length_mm",
    required=True,
    decision=True,
    fields=("межосевое расстояние, мм",),
    learn="measure_radiator_centers",
)
HEAT_OUTPUT = _fact(
    "heat_output_w",
    aliases=("required_heat_output_w", "thermal_output"),
    unit_family="power_w",
    strength=FactStrength.SOFT,
    fields=("теплоотдача, вт",),
)

COOLANT_TYPE = _fact(
    "coolant_type",
    aliases=(
        "coolant",
        "working_fluid",
        "heat_transfer_fluid",
        "fluid_type",
        "working_medium",
        "heat_carrying_medium",
        "теплоноситель",
    ),
    value_type=FactValueType.TEXT,
    required=False,
    decision=True,
    comparison=ComparisonMode.EXACT,
    fields=("рабочая среда", "теплоноситель"),
)
GLYCOL_CONCENTRATION = _fact(
    "glycol_concentration_percent",
    aliases=(
        "glycol_concentration",
        "glycol_content_percent",
        "glycol_percent",
        "propylene_glycol_concentration",
        "propylene_glycol_percent",
        "ethylene_glycol_concentration",
        "ethylene_glycol_percent",
        "antifreeze_concentration",
        "coolant_concentration",
    ),
    unit_family="percent",
    required=False,
    decision=True,
    comparison=ComparisonMode.NUMERIC,
    fields=("концентрация гликоля, %", "концентрация теплоносителя, %"),
)
FILTER_METHOD = _fact(
    "filter_method",
    aliases=(
        "filter_type",
        "filtration_type",
        "filtration_method",
        "cleaning_type",
        "type",
        "тип_фильтра",
        "метод_фильтрации",
    ),
    value_type=FactValueType.TEXT,
    required=True,
    decision=True,
    comparison=ComparisonMode.EXACT,
    parsers=("filter_method",),
    learn="identify_required_water_treatment",
)
MICRON_RATING = _fact(
    "micron_rating_um",
    aliases=(
        "micron_rating",
        "filtration_rating_um",
        "filter_mesh_um",
        "particle_size_um",
    ),
    unit_family="micron",
    required=False,
    decision=True,
    comparison=ComparisonMode.NUMERIC,
    parsers=("micron_rating",),
    learn="read_filter_micron_rating",
)
WASHABLE = _fact(
    "washable",
    aliases=(
        "is_washable",
        "flushable",
        "backwashable",
        "self_cleaning",
        "self_cleaning_filter",
        "промывной",
        "самопромывной",
    ),
    value_type=FactValueType.BOOLEAN,
    decision=True,
    comparison=ComparisonMode.BOOLEAN,
    fields=("промывной", "самопромывной", "возможность промывки"),
    parsers=("washable",),
    learn="read_filter_cleaning_method",
)


SPECIALIZED_FUEL_TYPE = FUEL_TYPE.model_copy(
    update={"required_for_exact": False}
)


def _contract(
    contract_id: str,
    kind: ProductKind,
    category: str,
    aliases: tuple[str, ...],
    roles: tuple[CatalogProductRole, ...],
    facts: tuple[ContractFactDefinition, ...],
    *,
    catalog_types: tuple[str, ...] = (),
    catalog_categories: tuple[str, ...] = (),
    candidates: tuple[ProductKind, ...] = (),
    invariants: tuple[str, ...] = (),
    required_alternatives: tuple[tuple[str, tuple[str, ...]], ...] = (),
    preliminary_identity_fact_groups: tuple[tuple[str, ...], ...] = (),
    availability_analog_relaxable_facts: tuple[str, ...] = (),
    preliminary_required_fact_groups: tuple[tuple[str, ...], ...] = (),
    auto_preliminary_when_safety_facts_known: bool = False,
) -> ProductContract:
    return ProductContract(
        contract_id=contract_id,
        product_kind=kind,
        category=category,
        semantic_aliases=aliases,
        catalog_type_aliases=catalog_types,
        catalog_category_aliases=catalog_categories,
        allowed_catalog_roles=roles,
        supported_acts=_CATALOG_ACTS,
        fact_definitions=(SKU, BRAND, *facts),
        analog_invariants=("product_kind", *invariants),
        candidate_kinds=candidates or (kind,),
        required_fact_alternatives=required_alternatives,
        preliminary_identity_fact_groups=preliminary_identity_fact_groups,
        availability_analog_relaxable_facts=availability_analog_relaxable_facts,
        preliminary_required_fact_groups=preliminary_required_fact_groups,
        auto_preliminary_when_safety_facts_known=(
            auto_preliminary_when_safety_facts_known
        ),
    )


COMPONENT = (CatalogProductRole.COMPONENT,)
BASE = (CatalogProductRole.BASE_PRODUCT,)


DEFAULT_CONTRACTS: tuple[ProductContract, ...] = (
    _contract(
        "pipe.pex.v1", ProductKind.PEX_PIPE, "pipes",
        (
            "pex pipe",
            "pex_pipe",
            "pipe pex",
            "pe xa pipe",
            "труба pex",
            "труба pe xa",
            "труба из сшитого полиэтилена",
        ),
        BASE,
        (
            PIPE_SERVICE,
            DIAMETER.model_copy(
                update={
                    "general_parsers": tuple(
                        dict.fromkeys((*DIAMETER.general_parsers, "pipe_outer_diameter"))
                    )
                }
            ),
            PIPE_OPERATING_TEMPERATURE,
            PIPE_OPERATING_PRESSURE,
            PIPE_REINFORCEMENT,
            MATERIAL,
        ),
        catalog_types=("труба",),
        catalog_categories=("трубы",),
        invariants=("diameter_mm",),
        preliminary_identity_fact_groups=(("pipe_service",), ("diameter_mm",)),
    ),
    _contract(
        "pipe.ppr.v1", ProductKind.PIPE, "pipes",
        ("pipe", "ppr pipe", "polypropylene pipe", "труба", "полипропиленовая труба"),
        BASE,
        (
            PIPE_SERVICE,
            DIAMETER,
            PIPE_OPERATING_TEMPERATURE,
            PIPE_OPERATING_PRESSURE,
            PIPE_REINFORCEMENT,
            PRESSURE_CLASS,
            MATERIAL,
        ),
        catalog_categories=("трубы",), invariants=("diameter_mm",),
        preliminary_identity_fact_groups=(("pipe_service",), ("diameter_mm",)),
    ),
    _contract(
        "pipe.sewer.v1", ProductKind.SEWER_PIPE, "sewer",
        ("sewer pipe", "канализационная труба", "труба канализации"),
        BASE, (DIAMETER, LENGTH, SEWER_SCOPE),
        catalog_types=("труба",), catalog_categories=("канализационные системы",),
        invariants=("diameter_mm", "sewer_scope"),
        preliminary_identity_fact_groups=(("sewer_scope",),),
    ),
    _contract(
        "fitting.elbow.v1", ProductKind.ELBOW, "fittings",
        ("elbow", "ppr elbow", "угольник", "ппр угольник"),
        COMPONENT, (DIAMETER, ANGLE, CONNECTION_SIZE),
        catalog_types=("угольник",), catalog_categories=("фитинги",),
        invariants=("diameter_mm",),
        preliminary_identity_fact_groups=(("diameter_mm",),),
    ),
    _contract(
        "sewer.elbow.v1", ProductKind.SEWER_ELBOW, "sewer",
        ("sewer elbow", "отвод", "канализационный отвод"),
        COMPONENT, (DIAMETER, ANGLE, SEWER_SCOPE),
        catalog_types=("отвод",), catalog_categories=("канализационные системы",),
        invariants=("diameter_mm", "sewer_scope"),
        preliminary_identity_fact_groups=(("sewer_scope",), ("diameter_mm",)),
    ),
    _contract(
        "sewer.tee.v1", ProductKind.TEE, "sewer",
        ("tee", "sewer tee", "тройник", "канализационный тройник"),
        COMPONENT, (DIAMETER, SECONDARY_DIAMETER, ANGLE, SEWER_SCOPE),
        catalog_types=("тройник",), invariants=("diameter_mm", "secondary_diameter_mm"),
        preliminary_identity_fact_groups=(("sewer_scope",), ("diameter_mm",)),
    ),
    _contract(
        "sewer.coupling.v1", ProductKind.COUPLING, "sewer",
        ("coupling", "sewer coupling", "муфта", "ремонтная муфта"),
        COMPONENT, (DIAMETER, SEWER_SCOPE), catalog_types=("муфта",),
        catalog_categories=("канализационные системы",), invariants=("diameter_mm",),
        preliminary_identity_fact_groups=(("sewer_scope",), ("diameter_mm",)),
    ),
    _contract(
        "fitting.reducing_coupling.v1", ProductKind.REDUCING_COUPLING, "fittings",
        ("reducing coupling", "transition coupling", "reducer", "переходник", "переходная муфта", "муфта переходная"),
        COMPONENT, (DIAMETER, SECONDARY_DIAMETER, MATERIAL),
        catalog_types=("муфта",), catalog_categories=("фитинги",),
        invariants=("diameter_mm", "secondary_diameter_mm"),
        preliminary_identity_fact_groups=(("diameter_mm",), ("secondary_diameter_mm",)),
    ),
    _contract(
        "valve.ball.v1", ProductKind.BALL_VALVE, "valves",
        ("ball valve", "шаровой кран", "кран шаровой"),
        COMPONENT,
        (
            CONNECTION_SIZE,
            CONNECTION_PATTERN,
            PORT_COUNT,
            APPLICATION,
            OPERATING_TEMPERATURE,
            OPERATING_PRESSURE,
            VALVE_SHAPE,
            HANDLE_TYPE,
            MATERIAL,
        ),
        catalog_types=("кран шаровой", "кран шаровой угловой"),
        catalog_categories=("водозапорная арматура",),
        invariants=("connection_size", "connection_pattern", "port_count"),
        preliminary_identity_fact_groups=(("connection_size",),),
    ),
    _contract(
        "radiator.thermostatic_head.v1", ProductKind.THERMOSTATIC_HEAD, "radiator_fittings",
        ("thermostatic head", "thermostat head", "термоголовка", "термостатическая головка"),
        COMPONENT, (CONTROL_THREAD,), catalog_categories=("арматура для радиаторов",),
        invariants=("control_thread",),
        preliminary_identity_fact_groups=(("control_thread",),),
    ),
    _contract(
        "radiator.valve.v1", ProductKind.RADIATOR_VALVE, "radiator_fittings",
        ("radiator valve", "thermostatic valve", "радиаторный клапан", "термостатический клапан"),
        COMPONENT, (CONNECTION_SIZE, VALVE_SHAPE, CONTROL_THREAD),
        catalog_categories=("арматура для радиаторов",),
        invariants=("connection_size",),
        preliminary_identity_fact_groups=(("connection_size",),),
    ),
    _contract(
        "radiator.valve_kit.v1", ProductKind.RADIATOR_VALVE_KIT, "radiator_fittings",
        ("radiator valve kit", "thermostatic kit", "комплект терморегулирования", "комплект радиаторной арматуры"),
        COMPONENT, (CONNECTION_SIZE, VALVE_SHAPE),
        catalog_categories=("арматура для радиаторов",), invariants=("connection_size",),
        preliminary_identity_fact_groups=(("connection_size",),),
    ),
    _contract(
        "pump.generic.v1", ProductKind.PUMP, "pumps",
        ("pump", "насос", "irrigation pump", "насос для полива"), BASE,
        (PUMP_TYPE, PUMP_WATER_SOURCE),
        candidates=(ProductKind.CIRCULATION_PUMP, ProductKind.DHW_CIRCULATION_PUMP,
                    ProductKind.BOREHOLE_PUMP, ProductKind.DRAINAGE_PUMP,
                    ProductKind.PUMP_STATION),
        preliminary_identity_fact_groups=(("pump_type",),),
    ),
    _contract(
        "pump.circulation.v1", ProductKind.CIRCULATION_PUMP, "pumps",
        ("circulation pump", "circulating pump", "циркуляционный насос"),
        BASE, (PUMP_DIAMETER, MAX_HEAD, MOUNTING_LENGTH, MAX_FLOW,
               DUTY_POINT_HEAD, DUTY_POINT_FLOW,
               COOLANT_TYPE, GLYCOL_CONCENTRATION),
        catalog_types=("насос",), catalog_categories=("насосное оборудование", "прокачиваем скидки"),
        invariants=("diameter_mm", "mounting_length_mm", "coolant_type",
                    "glycol_concentration_percent"),
        required_alternatives=(("max_head_m", ("duty_point_head_m",)),),
        preliminary_identity_fact_groups=(
            ("diameter_mm", "max_head_m", "duty_point_head_m", "mounting_length_mm"),
        ),
        # A circulation-pump shortlist can be useful before the mounting
        # dimensions are known, but only once the customer supplied a real
        # duty point.  These groups affect only the explicitly requested
        # preliminary path; DN and mounting length remain hard facts for an
        # exact installation match.
        preliminary_required_fact_groups=(
            ("duty_point_flow_l_h",),
            ("max_head_m", "duty_point_head_m"),
        ),
        # Flow and head describe a meaningful preliminary hydraulic target.
        # DN and mounting length still remain mandatory before the customer
        # can treat a card as a confirmed installation match.
        auto_preliminary_when_safety_facts_known=True,
    ),
    _contract(
        "pump.dhw_circulation.v1", ProductKind.DHW_CIRCULATION_PUMP, "pumps",
        ("dhw circulation pump", "гвс насос", "насос рециркуляции гвс"),
        BASE, (MAX_HEAD, MOUNTING_LENGTH), catalog_types=("насос",),
        invariants=("mounting_length_mm",),
        preliminary_identity_fact_groups=(("max_head_m", "mounting_length_mm"),),
    ),
    _contract(
        "pump.borehole.v1", ProductKind.BOREHOLE_PUMP, "pumps",
        ("borehole pump", "submersible borehole pump", "скважинный насос"),
        BASE,
        (
            BOREHOLE_REQUIRED_HEAD,
            BOREHOLE_REQUIRED_FLOW,
            BOREHOLE_CATALOG_MAX_HEAD,
            BOREHOLE_CATALOG_MAX_FLOW,
            BOREHOLE_WATER_LEVEL,
            BOREHOLE_STATIC_WATER_LEVEL,
            BOREHOLE_LIFT_HEIGHT,
            BOREHOLE_HORIZONTAL_RUN,
            BOREHOLE_REQUIRED_PRESSURE,
            BOREHOLE_DISCHARGE_DIAMETER,
            BOREHOLE_DISCHARGE_SDR,
        ),
        catalog_categories=("насосное оборудование",),
        invariants=("required_head_m", "required_flow_l_h"),
        preliminary_identity_fact_groups=(("required_head_m", "required_flow_l_h"),),
    ),
    _contract(
        "pump.drainage.v1", ProductKind.DRAINAGE_PUMP, "pumps",
        ("drainage pump", "дренажный насос"), BASE,
        (OPEN_WATER_REQUIRED_HEAD, MAX_FLOW),
        catalog_categories=("насосное оборудование", "прокачиваем скидки"),
        invariants=("max_head_m",),
        preliminary_identity_fact_groups=(("max_head_m", "max_flow_l_h"),),
    ),
    _contract(
        "pump.station.v1", ProductKind.PUMP_STATION, "pumps",
        ("pump station", "насосная станция"), BASE,
        (_fact("suction_depth_m", aliases=("suction_depth",), unit_family="length_m",
               required=True, decision=True, fields=("глубина всасывания, м",),
               learn="measure_suction_depth"), MAX_HEAD, MAX_FLOW),
        catalog_types=("насосная станция",), invariants=("suction_depth_m",),
        preliminary_identity_fact_groups=(("suction_depth_m", "max_head_m"),),
    ),
    _contract(
        "boiler.generic.v1", ProductKind.BOILER, "boilers",
        ("boiler", "котел", "котел отопления"), BASE,
        (FUEL_TYPE, POWER_KW, BUILDING_AREA, CIRCUITS, INTEGRATED_CIRCULATION_PUMP, EXPANSION_TANK_VOLUME, CHAMBER, DECLARED_HEATED_AREA),
        candidates=(ProductKind.GAS_BOILER, ProductKind.ELECTRIC_BOILER),
        invariants=("boiler_type", "circuits"),
        required_alternatives=(("power_kw", ("area_m2",)),),
        preliminary_identity_fact_groups=(("boiler_type",), ("power_kw", "area_m2")),
        availability_analog_relaxable_facts=("power_kw",),
    ),
    _contract(
        "boiler.gas.v1", ProductKind.GAS_BOILER, "boilers",
        ("gas boiler", "газовый котел"), BASE,
        (SPECIALIZED_FUEL_TYPE, POWER_KW, BUILDING_AREA, CIRCUITS, INTEGRATED_CIRCULATION_PUMP, EXPANSION_TANK_VOLUME, CHAMBER, DECLARED_HEATED_AREA),
        catalog_types=("котел",), invariants=("boiler_type", "circuits"),
        required_alternatives=(("power_kw", ("area_m2",)),),
        preliminary_identity_fact_groups=(("power_kw", "area_m2"),),
        availability_analog_relaxable_facts=("power_kw",),
    ),
    _contract(
        "boiler.electric.v1", ProductKind.ELECTRIC_BOILER, "boilers",
        ("electric boiler", "электрический котел", "электрокотел"), BASE,
        (SPECIALIZED_FUEL_TYPE, POWER_KW, BUILDING_AREA, CIRCUITS, INTEGRATED_CIRCULATION_PUMP, EXPANSION_TANK_VOLUME, DECLARED_HEATED_AREA), catalog_types=("котел",),
        invariants=("boiler_type", "circuits"),
        required_alternatives=(("power_kw", ("area_m2",)),),
        preliminary_identity_fact_groups=(("power_kw", "area_m2"),),
        availability_analog_relaxable_facts=("power_kw",),
    ),
    _contract(
        "radiator.v1", ProductKind.RADIATOR, "radiators",
        ("radiator", "heating radiator", "радиатор", "радиатор отопления"), BASE,
        (
            RADIATOR_MATERIAL,
            CENTER_DISTANCE,
            CONNECTION_SIZE,
            HEAT_OUTPUT,
            # The source-backed declared coverage of a radiator can narrow a
            # preliminary card set for a stated room area.  It never replaces
            # a heat-loss calculation or the physical mounting dimensions.
            BUILDING_AREA,
            DECLARED_HEATED_AREA,
        ),
        catalog_types=("радиатор отопления",), catalog_categories=("радиаторы отопления",),
        invariants=("material", "center_distance_mm"),
        # The order also determines the single question for an empty request.
        # Prefer the physical installation dimension; a stated room area still
        # satisfies the same any-of group and unlocks only a preliminary list.
        preliminary_identity_fact_groups=(("center_distance_mm", "heat_output_w", "area_m2"),),
    ),
    _contract(
        "filter.water.v1", ProductKind.FILTER, "filters",
        (
            "filter",
            "water filter",
            "water_filter",
            "mechanical water filter",
            "фильтр",
            "фильтр для воды",
            "фильтр механической очистки",
            "механический фильтр",
            "грязевик",
        ),
        BASE, (FILTER_METHOD, CONNECTION_SIZE, MICRON_RATING, WASHABLE),
        catalog_types=("фильтр", "фильтр косой"),
        catalog_categories=("фильтры",),
        invariants=("filter_method", "connection_size"),
        preliminary_identity_fact_groups=(("filter_method",),),
    ),
)


@dataclass(frozen=True)
class CatalogKindRule:
    kind: ProductKind
    role: CatalogProductRole
    category_markers: tuple[str, ...] = ()
    type_markers: tuple[str, ...] = ()
    name_markers: tuple[str, ...] = ()
    excluded_name_markers: tuple[str, ...] = ()

    def matches(self, category: str, product_type: str, name: str) -> bool:
        if self.category_markers and not any(x in category for x in self.category_markers):
            return False
        if self.type_markers and not any(x in product_type for x in self.type_markers):
            return False
        if self.name_markers and not any(x in name for x in self.name_markers):
            return False
        return not any(x in name for x in self.excluded_name_markers)


CATALOG_KIND_RULES: tuple[CatalogKindRule, ...] = (
    # A filter catalogue contains complete filters together with cartridges,
    # housings and service parts.  Keep those roles machine-readable so a
    # target water-filter task can never receive a cartridge or housing.
    CatalogKindRule(ProductKind.FILTER, CatalogProductRole.CONSUMABLE,
                    ("фильтры",),
                    name_markers=("картридж", "мембран", "фильтрующий элемент",
                                  "комплект сменных")),
    CatalogKindRule(ProductKind.FILTER, CatalogProductRole.ACCESSORY,
                    ("фильтры",),
                    name_markers=("корпус", "колба", "крышк", "ключ", "чехол", "инвертор")),
    CatalogKindRule(ProductKind.FILTER, CatalogProductRole.BASE_PRODUCT,
                    ("фильтры",), name_markers=("фильтр",),
                    excluded_name_markers=("картридж", "корпус", "мембран", "фильтрующий элемент",
                                           "комплект сменных", "колба", "крышк", "ключ", "чехол", "инвертор")),
    CatalogKindRule(ProductKind.FILTER, CatalogProductRole.BASE_PRODUCT,
                    ("фитинги",), type_markers=("фильтр",),
                    name_markers=("фильтр",)),
    CatalogKindRule(ProductKind.RADIATOR_VALVE_KIT, CatalogProductRole.COMPONENT,
                    ("арматура для радиаторов",), name_markers=("комплект терморег",)),
    CatalogKindRule(ProductKind.THERMOSTATIC_HEAD, CatalogProductRole.COMPONENT,
                    ("арматура для радиаторов",), name_markers=("термоголов", "термостатическая голов")),
    CatalogKindRule(ProductKind.RADIATOR_VALVE, CatalogProductRole.COMPONENT,
                    ("арматура для радиаторов",), name_markers=("клапан",)),
    CatalogKindRule(ProductKind.REDUCING_COUPLING, CatalogProductRole.COMPONENT,
                    ("фитинги",), ("муфта",), ("переход",)),
    CatalogKindRule(ProductKind.ELBOW, CatalogProductRole.COMPONENT,
                    ("фитинги",), ("угольник",)),
    CatalogKindRule(ProductKind.SEWAGE_PUMP, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("канализационный насос", "фекальный насос")),
    CatalogKindRule(ProductKind.DRAINAGE_PUMP, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("дренажный насос",)),
    CatalogKindRule(ProductKind.BOREHOLE_PUMP, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("скважинный насос",)),
    CatalogKindRule(ProductKind.DHW_CIRCULATION_PUMP, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("насос цирк. для гвс", "насос циркуляционный для гвс")),
    CatalogKindRule(ProductKind.CIRCULATION_PUMP, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("насос циркуляц", "циркуляционный насос")),
    CatalogKindRule(ProductKind.PUMP_STATION, CatalogProductRole.BASE_PRODUCT,
                    type_markers=("насосная станция",), name_markers=("насосная станция",)),
    CatalogKindRule(ProductKind.GAS_BOILER, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("котел газовый", "газовый котел")),
    CatalogKindRule(ProductKind.ELECTRIC_BOILER, CatalogProductRole.BASE_PRODUCT,
                    name_markers=("котел электрический", "электрический котел")),
    CatalogKindRule(ProductKind.SEWAGE_PUMP, CatalogProductRole.BASE_PRODUCT,
                    category_markers=("насос",),
                    name_markers=("канализацион", "фекальн")),
    CatalogKindRule(ProductKind.BALL_VALVE, CatalogProductRole.COMPONENT,
                    ("водозапорная арматура",), type_markers=("кран шаровой",)),
    CatalogKindRule(ProductKind.SEWER_PIPE, CatalogProductRole.BASE_PRODUCT,
                    type_markers=("труба",), category_markers=("канализационные системы", "акционные товары")),
    CatalogKindRule(ProductKind.SEWER_ELBOW, CatalogProductRole.COMPONENT,
                    type_markers=("отвод",), category_markers=("канализационные системы",)),
    CatalogKindRule(ProductKind.TEE, CatalogProductRole.COMPONENT,
                    type_markers=("тройник",)),
    CatalogKindRule(ProductKind.COUPLING, CatalogProductRole.COMPONENT,
                    type_markers=("муфта",), category_markers=("канализационные системы",)),
    CatalogKindRule(ProductKind.PEX_PIPE, CatalogProductRole.BASE_PRODUCT,
                    category_markers=("трубы",),
                    name_markers=("pex", "pe xa", "сшитого полиэтилена")),
    CatalogKindRule(ProductKind.PIPE, CatalogProductRole.BASE_PRODUCT,
                    category_markers=("трубы",), name_markers=("труба",)),
    CatalogKindRule(ProductKind.RADIATOR, CatalogProductRole.BASE_PRODUCT,
                    type_markers=("радиатор отопления",), category_markers=("радиаторы отопления",)),
)


class ProductContractRegistry:
    def __init__(self, contracts: tuple[ProductContract, ...] = DEFAULT_CONTRACTS) -> None:
        self.contracts = contracts
        self._by_id = {item.contract_id: item for item in contracts}
        self._by_kind = {item.product_kind: item for item in contracts}

    def get(self, contract_id: str | None) -> ProductContract | None:
        return self._by_id.get(str(contract_id or ""))

    def for_kind(self, kind: ProductKind) -> ProductContract | None:
        return self._by_kind.get(kind)

    def resolve_task(
        self,
        state: DialogueStateV2,
        task: CustomerTask,
        catalog_snapshot: tuple[CatalogProductSnapshot, ...] = (),
    ) -> ContractResolution:
        goal = next(
            (item for item in state.product_goals if item.goal_id == task.target_goal_id),
            None,
        )
        if goal is None:
            return ContractResolution(
                task_id=task.task_id,
                status=ContractResolutionStatus.UNSUPPORTED,
                reason_codes=("task_has_no_product_goal",),
            )
        explicit_sku_facts = tuple(
            fact
            for fact in state.constraints
            if fact.active
            and fact.status.value == "known"
            and fact.polarity.value == "required"
            and normalize_identity(fact.name)
            in {"sku", "article", "vendor code", "vendorcode", "артикул"}
            and (
                fact.goal_id == goal.goal_id
                or (fact.goal_id is None and fact.task_id in {None, task.task_id})
            )
        )
        if len(explicit_sku_facts) == 1 and catalog_snapshot:
            sku_resolution = resolve_catalog_sku(
                explicit_sku_facts[0].value,
                catalog_snapshot,
            )
            if sku_resolution.status in {
                SkuResolutionStatus.EXACT,
                SkuResolutionStatus.UNIQUE_PREFIX,
            } and sku_resolution.candidates:
                resolved_product = sku_resolution.candidates[0]
                contract = self.for_kind(resolved_product.product_kind)
                if contract is None:
                    return ContractResolution(
                        task_id=task.task_id,
                        goal_id=goal.goal_id,
                        status=ContractResolutionStatus.UNSUPPORTED,
                        product_kind=resolved_product.product_kind,
                        reason_codes=("explicit_sku_product_kind_unsupported",),
                    )
                if task.act.value not in contract.supported_acts:
                    return ContractResolution(
                        task_id=task.task_id,
                        goal_id=goal.goal_id,
                        status=ContractResolutionStatus.UNSUPPORTED,
                        product_kind=contract.product_kind,
                        reason_codes=(
                            "customer_act_not_supported_by_explicit_sku_contract",
                        ),
                    )
                return ContractResolution(
                    task_id=task.task_id,
                    goal_id=goal.goal_id,
                    status=ContractResolutionStatus.RESOLVED,
                    contract_id=contract.contract_id,
                    product_kind=contract.product_kind,
                    reason_codes=(
                        "explicit_sku_overrode_stale_product_goal",
                        f"sku_resolution_{sku_resolution.status.value}",
                    ),
                )
        matches = self._semantic_matches(goal)
        if not matches:
            return ContractResolution(
                task_id=task.task_id,
                goal_id=goal.goal_id,
                status=ContractResolutionStatus.UNSUPPORTED,
                reason_codes=("no_product_contract_for_goal",),
            )
        if len(matches) > 1:
            exact_category = [item for item in matches if item.category == goal.category.value]
            if len(exact_category) == 1:
                matches = exact_category
        if len(matches) != 1:
            return ContractResolution(
                task_id=task.task_id,
                goal_id=goal.goal_id,
                status=ContractResolutionStatus.AMBIGUOUS,
                reason_codes=("multiple_product_contracts_match_goal",),
            )
        contract = matches[0]
        if task.act.value not in contract.supported_acts:
            return ContractResolution(
                task_id=task.task_id,
                goal_id=goal.goal_id,
                status=ContractResolutionStatus.UNSUPPORTED,
                product_kind=contract.product_kind,
                reason_codes=("customer_act_not_supported_by_product_contract",),
            )
        return ContractResolution(
            task_id=task.task_id,
            goal_id=goal.goal_id,
            status=ContractResolutionStatus.RESOLVED,
            contract_id=contract.contract_id,
            product_kind=contract.product_kind,
            reason_codes=("semantic_product_kind_resolved",),
        )

    def _semantic_matches(self, goal: ProductGoal) -> list[ProductContract]:
        identity = normalize_identity(goal.canonical_type or "")
        category = goal.category.value
        evidence = normalize_identity(goal.evidence or "")
        if identity in {"pipe", "труба"} and any(
            marker in evidence
            for marker in ("pex", "pe xa", "сшитого полиэтилена")
        ):
            pex = self._by_kind.get(ProductKind.PEX_PIPE)
            return [pex] if pex else []
        exact_matches: list[ProductContract] = []
        partial_matches: list[ProductContract] = []
        for contract in self.contracts:
            aliases = tuple(normalize_identity(alias) for alias in contract.semantic_aliases)
            if identity and identity in aliases:
                exact_matches.append(contract)
            elif identity and any(identity in alias or alias in identity for alias in aliases):
                partial_matches.append(contract)
        if identity in {"pipe", "труба"} and category == "sewer":
            return [self._by_kind[ProductKind.SEWER_PIPE]]
        if identity in {"coupling", "муфта"} and category == "fittings":
            reducing = self._by_kind.get(ProductKind.REDUCING_COUPLING)
            return [reducing] if reducing else []
        matches = exact_matches or partial_matches
        category_matches = [item for item in matches if item.category == category]
        if category_matches:
            matches = category_matches
        return list({item.contract_id: item for item in matches}.values())

    def classify_catalog_identity(
        self,
        *,
        category: str,
        product_type: str,
        name: str,
    ) -> tuple[ProductKind, CatalogProductRole, str | None]:
        normalized_category = normalize_identity(category)
        normalized_type = normalize_identity(product_type)
        normalized_name = normalize_identity(name)
        for rule in CATALOG_KIND_RULES:
            if rule.matches(normalized_category, normalized_type, normalized_name):
                return rule.kind, rule.role, None
        return (
            ProductKind.UNSUPPORTED,
            CatalogProductRole.UNKNOWN,
            "catalog_product_kind_not_covered",
        )
