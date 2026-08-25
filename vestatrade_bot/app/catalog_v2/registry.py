"""Declarative product-kind registry for semantic and catalogue identities."""

from __future__ import annotations

from dataclasses import dataclass

from app.dialogue_v2.contracts import CustomerTask, DialogueStateV2, ProductGoal

from .contracts import (
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
    parsers: tuple[str, ...] = (),
    learn: str | None = None,
) -> ContractFactDefinition:
    conversions = {
        "length_mm": {"mm": 1.0, "cm": 10.0, "m": 1000.0},
        "length_m": {"m": 1.0, "cm": 0.01, "mm": 0.001},
        "head_m": {"m": 1.0, "cm": 0.01},
        "angle_deg": {"deg": 1.0, "°": 1.0},
        "power_kw": {"kw": 1.0, "квт": 1.0, "w": 0.001, "вт": 0.001},
        "power_w": {"w": 1.0, "вт": 1.0, "kw": 1000.0, "квт": 1000.0},
        "flow": {"l/h": 1.0, "л/ч": 1.0, "l/min": 60.0, "л/мин": 60.0, "m3/h": 1000.0, "м3/ч": 1000.0},
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
        general_parsers=parsers,
        learn_method_code=learn,
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
    aliases=("thread_size", "connection_diameter_inch"),
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
    aliases=("required_head", "head_m", "maximum_head", "lift_height_m"),
    unit_family="head_m",
    required=True,
    decision=True,
    fields=("максимальный напор, м", "высота напора, м"),
    parsers=("pump_designation_head",),
    learn="estimate_required_system_head",
)
MAX_FLOW = _fact(
    "max_flow_l_h",
    aliases=("flow", "flow_rate", "required_flow_l_h", "required_flow_l_min", "required_flow_m3_h"),
    unit_family="flow",
    strength=FactStrength.SOFT,
    fields=("макс. производительность, л/ч",),
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
FUEL_TYPE = _fact(
    "boiler_type",
    aliases=("fuel_type", "energy_source"),
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
    aliases=("circuit_count", "number_of_circuits"),
    required=True,
    decision=True,
    fields=("количество контуров",),
    parsers=("circuit_count",),
    learn="decide_heating_only_or_dhw",
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
        fact_definitions=(SKU, *facts),
        analog_invariants=("product_kind", *invariants),
        candidate_kinds=candidates or (kind,),
    )


COMPONENT = (CatalogProductRole.COMPONENT,)
BASE = (CatalogProductRole.BASE_PRODUCT,)


DEFAULT_CONTRACTS: tuple[ProductContract, ...] = (
    _contract(
        "pipe.ppr.v1", ProductKind.PIPE, "pipes",
        ("pipe", "ppr pipe", "polypropylene pipe", "труба", "полипропиленовая труба"),
        BASE, (DIAMETER, PRESSURE_CLASS, MATERIAL),
        catalog_categories=("трубы",), invariants=("diameter_mm",),
    ),
    _contract(
        "pipe.sewer.v1", ProductKind.SEWER_PIPE, "sewer",
        ("sewer pipe", "канализационная труба", "труба канализации"),
        BASE, (DIAMETER, LENGTH, SEWER_SCOPE),
        catalog_types=("труба",), catalog_categories=("канализационные системы",),
        invariants=("diameter_mm", "sewer_scope"),
    ),
    _contract(
        "fitting.elbow.v1", ProductKind.ELBOW, "fittings",
        ("elbow", "ppr elbow", "угольник", "ппр угольник"),
        COMPONENT, (DIAMETER, ANGLE, CONNECTION_SIZE),
        catalog_types=("угольник",), catalog_categories=("фитинги",),
        invariants=("diameter_mm",),
    ),
    _contract(
        "sewer.elbow.v1", ProductKind.SEWER_ELBOW, "sewer",
        ("sewer elbow", "отвод", "канализационный отвод"),
        COMPONENT, (DIAMETER, ANGLE, SEWER_SCOPE),
        catalog_types=("отвод",), catalog_categories=("канализационные системы",),
        invariants=("diameter_mm", "sewer_scope"),
    ),
    _contract(
        "sewer.tee.v1", ProductKind.TEE, "sewer",
        ("tee", "sewer tee", "тройник", "канализационный тройник"),
        COMPONENT, (DIAMETER, SECONDARY_DIAMETER, ANGLE, SEWER_SCOPE),
        catalog_types=("тройник",), invariants=("diameter_mm", "secondary_diameter_mm"),
    ),
    _contract(
        "sewer.coupling.v1", ProductKind.COUPLING, "sewer",
        ("coupling", "sewer coupling", "муфта", "ремонтная муфта"),
        COMPONENT, (DIAMETER, SEWER_SCOPE), catalog_types=("муфта",),
        catalog_categories=("канализационные системы",), invariants=("diameter_mm",),
    ),
    _contract(
        "fitting.reducing_coupling.v1", ProductKind.REDUCING_COUPLING, "fittings",
        ("reducing coupling", "transition coupling", "reducer", "переходник", "переходная муфта", "муфта переходная"),
        COMPONENT, (DIAMETER, SECONDARY_DIAMETER, MATERIAL),
        catalog_types=("муфта",), catalog_categories=("фитинги",),
        invariants=("diameter_mm", "secondary_diameter_mm"),
    ),
    _contract(
        "valve.ball.v1", ProductKind.BALL_VALVE, "valves",
        ("ball valve", "шаровой кран", "кран шаровой"),
        COMPONENT, (CONNECTION_SIZE, CONNECTION_PATTERN, VALVE_SHAPE, MATERIAL),
        catalog_types=("кран шаровой", "кран шаровой угловой"),
        catalog_categories=("водозапорная арматура",),
        invariants=("connection_size", "connection_pattern"),
    ),
    _contract(
        "radiator.thermostatic_head.v1", ProductKind.THERMOSTATIC_HEAD, "radiator_fittings",
        ("thermostatic head", "thermostat head", "термоголовка", "термостатическая головка"),
        COMPONENT, (CONTROL_THREAD,), catalog_categories=("арматура для радиаторов",),
        invariants=("control_thread",),
    ),
    _contract(
        "radiator.valve.v1", ProductKind.RADIATOR_VALVE, "radiator_fittings",
        ("radiator valve", "thermostatic valve", "радиаторный клапан", "термостатический клапан"),
        COMPONENT, (CONNECTION_SIZE, VALVE_SHAPE, CONTROL_THREAD),
        catalog_categories=("арматура для радиаторов",),
        invariants=("connection_size",),
    ),
    _contract(
        "radiator.valve_kit.v1", ProductKind.RADIATOR_VALVE_KIT, "radiator_fittings",
        ("radiator valve kit", "thermostatic kit", "комплект терморегулирования", "комплект радиаторной арматуры"),
        COMPONENT, (CONNECTION_SIZE, VALVE_SHAPE),
        catalog_categories=("арматура для радиаторов",), invariants=("connection_size",),
    ),
    _contract(
        "pump.generic.v1", ProductKind.PUMP, "pumps",
        ("pump", "насос"), BASE,
        (_fact("pump_type", aliases=("water_source",), value_type=FactValueType.TEXT,
               required=True, decision=True, comparison=ComparisonMode.EXACT,
               learn="identify_pump_application"),),
        candidates=(ProductKind.CIRCULATION_PUMP, ProductKind.DHW_CIRCULATION_PUMP,
                    ProductKind.BOREHOLE_PUMP, ProductKind.DRAINAGE_PUMP,
                    ProductKind.PUMP_STATION),
    ),
    _contract(
        "pump.circulation.v1", ProductKind.CIRCULATION_PUMP, "pumps",
        ("circulation pump", "circulating pump", "циркуляционный насос"),
        BASE, (DIAMETER, MAX_HEAD, MOUNTING_LENGTH, MAX_FLOW),
        catalog_types=("насос",), catalog_categories=("насосное оборудование", "прокачиваем скидки"),
        invariants=("diameter_mm", "mounting_length_mm"),
    ),
    _contract(
        "pump.dhw_circulation.v1", ProductKind.DHW_CIRCULATION_PUMP, "pumps",
        ("dhw circulation pump", "гвс насос", "насос рециркуляции гвс"),
        BASE, (MAX_HEAD, MOUNTING_LENGTH), catalog_types=("насос",),
        invariants=("mounting_length_mm",),
    ),
    _contract(
        "pump.borehole.v1", ProductKind.BOREHOLE_PUMP, "pumps",
        ("borehole pump", "submersible borehole pump", "скважинный насос"),
        BASE, (MAX_HEAD, MAX_FLOW), catalog_categories=("насосное оборудование",),
        invariants=("max_head_m",),
    ),
    _contract(
        "pump.drainage.v1", ProductKind.DRAINAGE_PUMP, "pumps",
        ("drainage pump", "дренажный насос"), BASE, (MAX_HEAD, MAX_FLOW),
        catalog_categories=("насосное оборудование", "прокачиваем скидки"),
        invariants=("max_head_m",),
    ),
    _contract(
        "pump.station.v1", ProductKind.PUMP_STATION, "pumps",
        ("pump station", "насосная станция"), BASE,
        (_fact("suction_depth_m", aliases=("suction_depth",), unit_family="length_m",
               required=True, decision=True, fields=("глубина всасывания, м",),
               learn="measure_suction_depth"), MAX_HEAD, MAX_FLOW),
        catalog_types=("насосная станция",), invariants=("suction_depth_m",),
    ),
    _contract(
        "boiler.generic.v1", ProductKind.BOILER, "boilers",
        ("boiler", "котел", "котел отопления"), BASE,
        (FUEL_TYPE, POWER_KW, CIRCUITS, CHAMBER),
        candidates=(ProductKind.GAS_BOILER, ProductKind.ELECTRIC_BOILER),
    ),
    _contract(
        "boiler.gas.v1", ProductKind.GAS_BOILER, "boilers",
        ("gas boiler", "газовый котел"), BASE, (POWER_KW, CIRCUITS, CHAMBER),
        catalog_types=("котел",), invariants=("power_kw", "circuits"),
    ),
    _contract(
        "boiler.electric.v1", ProductKind.ELECTRIC_BOILER, "boilers",
        ("electric boiler", "электрический котел", "электрокотел"), BASE,
        (POWER_KW, CIRCUITS), catalog_types=("котел",),
        invariants=("power_kw", "circuits"),
    ),
    _contract(
        "radiator.v1", ProductKind.RADIATOR, "radiators",
        ("radiator", "heating radiator", "радиатор", "радиатор отопления"), BASE,
        (RADIATOR_MATERIAL, CENTER_DISTANCE, CONNECTION_SIZE, HEAT_OUTPUT),
        catalog_types=("радиатор отопления",), catalog_categories=("радиаторы отопления",),
        invariants=("material", "center_distance_mm"),
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
