"""Immutable versioned contracts for the Stage 3 shadow catalogue path."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CATALOG_CONTRACT_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ProductKind(str, Enum):
    PIPE = "pipe"
    SEWER_PIPE = "sewer_pipe"
    ELBOW = "elbow"
    SEWER_ELBOW = "sewer_elbow"
    TEE = "tee"
    COUPLING = "coupling"
    REDUCING_COUPLING = "reducing_coupling"
    BALL_VALVE = "ball_valve"
    MANUAL_VALVE = "manual_valve"
    CHECK_VALVE = "check_valve"
    BALANCING_VALVE = "balancing_valve"
    THREE_WAY_VALVE = "three_way_valve"
    THERMOSTATIC_HEAD = "thermostatic_head"
    RADIATOR_VALVE = "radiator_valve"
    RADIATOR_VALVE_KIT = "radiator_valve_kit"
    PUMP = "pump"
    CIRCULATION_PUMP = "circulation_pump"
    DHW_CIRCULATION_PUMP = "dhw_circulation_pump"
    BOOSTER_PUMP = "booster_pump"
    BOREHOLE_PUMP = "borehole_pump"
    WELL_PUMP = "well_pump"
    DRAINAGE_PUMP = "drainage_pump"
    SEWAGE_PUMP = "sewage_pump"
    PUMP_STATION = "pump_station"
    BOILER = "boiler"
    GAS_BOILER = "gas_boiler"
    ELECTRIC_BOILER = "electric_boiler"
    WATER_HEATER = "water_heater"
    HYDRAULIC_ACCUMULATOR = "hydraulic_accumulator"
    FILTER = "filter"
    RADIATOR = "radiator"
    COLLECTOR = "collector"
    CONTROLS = "controls"
    UNSUPPORTED = "unsupported"


class CatalogProductRole(str, Enum):
    BASE_PRODUCT = "base_product"
    ACCESSORY = "accessory"
    SPARE_PART = "spare_part"
    TOOL = "tool"
    COMPONENT = "component"
    CONSUMABLE = "consumable"
    UNKNOWN = "unknown"


class FactValueType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"


class FactStrength(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class ComparisonMode(str, Enum):
    EXACT = "exact"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    CONTAINS = "contains"


class MissingCatalogBehavior(str, Enum):
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


class ContractFactDefinition(FrozenModel):
    name: str
    aliases: tuple[str, ...] = ()
    value_type: FactValueType = FactValueType.TEXT
    unit_family: str | None = None
    unit_conversions: dict[str, float] = Field(default_factory=dict)
    strength: FactStrength = FactStrength.HARD
    required_for_exact: bool = False
    decision_changing: bool = False
    preliminary_allowed_without: bool = True
    comparison: ComparisonMode = ComparisonMode.EXACT
    catalog_fields: tuple[str, ...] = ()
    general_parsers: tuple[str, ...] = ()
    learn_method_code: str | None = None
    missing_catalog_behavior: MissingCatalogBehavior = (
        MissingCatalogBehavior.UNVERIFIED
    )


class ProductContract(FrozenModel):
    schema_version: Literal["1.0"] = CATALOG_CONTRACT_SCHEMA_VERSION
    contract_id: str
    product_kind: ProductKind
    category: str
    semantic_aliases: tuple[str, ...]
    catalog_type_aliases: tuple[str, ...] = ()
    catalog_category_aliases: tuple[str, ...] = ()
    allowed_catalog_roles: tuple[CatalogProductRole, ...]
    supported_acts: tuple[str, ...]
    fact_definitions: tuple[ContractFactDefinition, ...]
    analog_invariants: tuple[str, ...] = ()
    incompatible_kinds: tuple[ProductKind, ...] = ()
    candidate_kinds: tuple[ProductKind, ...] = ()
    alternative_kinds: tuple[ProductKind, ...] = ()


class ContractResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class ContractResolution(FrozenModel):
    task_id: str
    goal_id: str | None = None
    status: ContractResolutionStatus
    contract_id: str | None = None
    product_kind: ProductKind = ProductKind.UNSUPPORTED
    reason_codes: tuple[str, ...] = ()


class FactProvenance(FrozenModel):
    source: Literal["attribute", "name", "description", "identity"]
    source_field: str
    raw_value: str = Field(max_length=500)
    parser: str


class CatalogFact(FrozenModel):
    name: str
    value: str | int | float | bool
    unit: str | None = None
    provenance: FactProvenance


class CatalogProductSnapshot(FrozenModel):
    sku: str
    name: str
    category: str
    product_kind: ProductKind
    role: CatalogProductRole
    facts: tuple[CatalogFact, ...] = ()
    unsupported_reason: str | None = None


class ReadinessStatus(str, Enum):
    EXACT_READY = "exact_ready"
    PRELIMINARY_READY = "preliminary_ready"
    NEEDS_DECISION_FACT = "needs_decision_fact"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


class ReadinessFact(FrozenModel):
    name: str
    status: Literal["known", "unknown", "refused", "deferred", "missing"]
    value: str | int | float | bool | None = None
    unit: str | None = None
    strength: FactStrength
    polarity: Literal["required", "preferred", "excluded"] = "required"


class TaskReadinessAssessment(FrozenModel):
    task_id: str
    goal_id: str | None = None
    contract_id: str | None = None
    product_kind: ProductKind = ProductKind.UNSUPPORTED
    status: ReadinessStatus
    confirmed_hard_facts: tuple[ReadinessFact, ...] = ()
    confirmed_soft_facts: tuple[ReadinessFact, ...] = ()
    missing_decision_facts: tuple[str, ...] = ()
    unknown_facts: tuple[str, ...] = ()
    refused_facts: tuple[str, ...] = ()
    deferred_facts: tuple[str, ...] = ()
    conflicting_facts: tuple[str, ...] = ()
    catalog_unverifiable_facts: tuple[str, ...] = ()
    recommended_question_fact: str | None = None
    learn_method_code: str | None = None
    reason_codes: tuple[str, ...] = ()


class CatalogSearchStage(str, Enum):
    EXACT_IDENTITY = "exact_identity"
    STRICT_SAME_KIND = "strict_same_kind"
    COMPATIBLE_ANALOG = "compatible_analog"
    RELAX_ONE_SOFT_CONSTRAINT = "relax_one_soft_constraint"
    ALTERNATIVE_SOLUTION = "alternative_solution"
    HONEST_NO_MATCH = "honest_no_match"


class CandidateStatus(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class SearchConstraint(FrozenModel):
    name: str
    value: str | int | float | bool
    unit: str | None = None
    strength: FactStrength
    polarity: Literal["required", "preferred", "excluded"] = "required"


class CatalogRelaxation(FrozenModel):
    fact_name: str
    requested_value: str | int | float | bool
    candidate_value: str | int | float | bool | None = None
    reason_code: str


class CandidateAssessment(FrozenModel):
    sku: str
    product_kind: ProductKind
    role: CatalogProductRole
    status: CandidateStatus
    matched_hard_facts: tuple[str, ...] = ()
    mismatched_hard_facts: tuple[str, ...] = ()
    missing_hard_facts: tuple[str, ...] = ()
    matched_soft_facts: tuple[str, ...] = ()
    mismatched_soft_facts: tuple[str, ...] = ()
    relaxations: tuple[CatalogRelaxation, ...] = ()
    provenance: tuple[FactProvenance, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CatalogSearchPlan(FrozenModel):
    plan_id: str
    task_id: str
    goal_id: str | None = None
    contract_id: str
    product_kind: ProductKind
    requested_role: CatalogProductRole
    stages: tuple[CatalogSearchStage, ...]
    hard_constraints: tuple[SearchConstraint, ...] = ()
    soft_constraints: tuple[SearchConstraint, ...] = ()
    unavailable_constraints: tuple[str, ...] = ()
    candidate_assessments: tuple[CandidateAssessment, ...] = ()
    eligible_skus: tuple[str, ...] = ()
    relaxed_skus: tuple[str, ...] = ()
    unverified_skus: tuple[str, ...] = ()
    excluded_kind_count: int = 0
    reason_codes: tuple[str, ...] = ()


class SolutionComponent(FrozenModel):
    component_id: str
    task_id: str
    product_kind: ProductKind
    role: CatalogProductRole
    required: bool = True
    depends_on: tuple[str, ...] = ()
    constraint_names: tuple[str, ...] = ()
    quantity: float | None = None
    status: Literal["planned", "unverified", "unsupported"] = "planned"


class SolutionPlan(FrozenModel):
    solution_id: str
    task_ids: tuple[str, ...]
    components: tuple[SolutionComponent, ...]
    unresolved_dependencies: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CatalogPlanningResult(FrozenModel):
    schema_version: Literal["1.0"] = CATALOG_CONTRACT_SCHEMA_VERSION
    status: Literal["planned", "skipped", "failed"]
    contract_resolutions: tuple[ContractResolution, ...] = ()
    readiness_assessments: tuple[TaskReadinessAssessment, ...] = ()
    search_plans: tuple[CatalogSearchPlan, ...] = ()
    solution_plan: SolutionPlan | None = None
    candidate_skus: tuple[str, ...] = ()
    unsupported_task_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None


class CoverageEntry(FrozenModel):
    product_kind: ProductKind
    role: CatalogProductRole
    count: int = Field(ge=0)
    contract_id: str | None = None
    categories: tuple[str, ...] = ()
    catalog_type_values: tuple[str, ...] = ()
    fact_presence_coverage: dict[str, float] = Field(default_factory=dict)
    missing_fact_fraction: dict[str, float] = Field(default_factory=dict)
    structured_attribute_coverage: dict[str, float] = Field(default_factory=dict)
    name_or_description_facts: tuple[str, ...] = ()
    ambiguity_codes: tuple[str, ...] = ()


class FeedCoverageAudit(FrozenModel):
    schema_version: Literal["1.0"] = CATALOG_CONTRACT_SCHEMA_VERSION
    source_path: str
    source_sha256: str
    raw_offer_count: int
    sanitized_product_count: int
    entries: tuple[CoverageEntry, ...]
    unsupported_count: int = 0
    unsupported_reason_counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
