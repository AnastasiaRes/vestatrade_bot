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
    PEX_PIPE = "pex_pipe"
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
    MINIMUM_RATING = "minimum_rating"
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
    # Some facts are necessary to derive a customer requirement but do not
    # describe a property of a catalogue card. Keep them in the one product
    # contract so semantic validation and readiness can see them, while
    # preventing the search planner from treating e.g. a route length as a
    # product filter.
    candidate_filterable: bool = True
    # Some safety-critical requirements may be compared to a card only when
    # the corresponding catalogue rating is explicitly present. A missing Q/H
    # maximum for a borehole pump is not an "unverified alternative" that may
    # be shown to a buyer: it cannot prove the pump clears even this limited
    # preliminary boundary.
    candidate_evidence_required: bool = False
    # A customer requirement and a scalar declared by a catalogue card can
    # have different meanings while still being compared deterministically.
    # For example, the heated area of a building is a customer requirement;
    # ``declared_heated_area_m2`` is a manufacturer/card property of a model.
    # Keeping the projection explicit prevents the reducer from treating a
    # product rating as if the customer had supplied it.
    candidate_fact_name: str | None = None
    # Some customer facts are safe source-backed proxies for an otherwise
    # required product fact, but only when that primary fact is absent.  The
    # boiler's heated building area is the first such case: it is compared to
    # a model's declared coverage only when the customer has not supplied a
    # project/design power.  This avoids silently adding a second, unrelated
    # filter to an already exact selection.
    candidate_required_when_missing: str | None = None
    # A fact may make a catalogue shortlist meaningful without being an
    # engineering calculation.  If it is used as the only alternative for a
    # required fact, readiness remains preliminary even though every
    # individual card comparison is source-backed.
    preliminary_only_for_exact: bool = False
    general_parsers: tuple[str, ...] = ()
    learn_method_code: str | None = None
    catalog_verifiable: bool = True
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
    required_fact_alternatives: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # An availability analogue is a deliberately narrower capability than a
    # normal soft-preference relaxation.  It may be used only after every
    # exact candidate has confirmed ``out_of_stock`` status, and only for the
    # declaratively listed facts of this product family.
    availability_analog_relaxable_facts: tuple[str, ...] = ()
    # Additional all-of groups that are relevant only to a *preliminary*
    # result.  Each group is an any-of set; exact readiness never depends on
    # these facts.
    preliminary_required_fact_groups: tuple[tuple[str, ...], ...] = ()
    # Each group is an ``any-of`` set of facts that makes a preliminary
    # catalogue result meaningful and safe enough to show.  Exact readiness
    # may still require more facts.  A group is deliberately owned by the
    # existing product contract rather than by a parallel dialogue taxonomy.
    preliminary_identity_fact_groups: tuple[tuple[str, ...], ...] = ()
    # Some product families can safely show a clearly labelled preliminary
    # shortlist as soon as their declared preliminary safety groups are
    # satisfied.  This is deliberately opt-in: it must not turn every
    # incomplete catalogue request into a broad result.
    auto_preliminary_when_safety_facts_known: bool = False


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
    source: Literal["attribute", "name", "description", "identity", "passport"]
    source_field: str
    raw_value: str = Field(max_length=500)
    parser: str
    # Kept separately from ``source_field`` so a renderer or evidence gate
    # does not have to parse a display string to recover the exact passport
    # and its scoped table/section.
    source_document: str | None = None
    source_section: str | None = None


class CatalogFact(FrozenModel):
    name: str
    value: str | int | float | bool
    unit: str | None = None
    provenance: FactProvenance


class CatalogFactIssue(FrozenModel):
    """Source-preserving reason why a catalogue field is not a scalar fact."""

    name: str
    status: Literal["ambiguous"] = "ambiguous"
    provenance: FactProvenance


class CatalogFlowHeadPoint(FrozenModel):
    """An exact manufacturer Q/H table point, never an interpolated value."""

    flow_l_h: float = Field(ge=0)
    head_m: float = Field(ge=0)
    provenance: FactProvenance


class PassportFlowHeadEvaluation(FrozenModel):
    """A source-backed check of one exact Q/H table point.

    This deliberately models a single row of a manufacturer table.  It is
    neither an interpolation nor a hydraulic-system calculation.
    """

    sku: str
    requested_flow_l_h: float = Field(ge=0)
    required_head_m: float = Field(ge=0)
    passport_point: CatalogFlowHeadPoint
    status: Literal["clears_required_head", "below_required_head"]


class CatalogProductSnapshot(FrozenModel):
    sku: str
    name: str
    category: str
    product_kind: ProductKind
    role: CatalogProductRole
    # Availability is catalogue evidence, not a technical compatibility fact.
    # Keep it alongside normalized facts so the pure planner can answer a
    # typed CHECK_STOCK query and honour a separate product-scoped stock
    # requirement without consulting the legacy Product object.
    stock_status: str | None = None
    stock_qty: int | None = None
    facts: tuple[CatalogFact, ...] = ()
    fact_issues: tuple[CatalogFactIssue, ...] = ()
    flow_head_points: tuple[CatalogFlowHeadPoint, ...] = ()
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
    # These fields explain why a broad preliminary search is either allowed
    # or fail-closed.  They are diagnostic metadata; candidate filtering stays
    # in the existing catalogue planner.
    missing_preliminary_identity_facts: tuple[str, ...] = ()
    unavailable_preliminary_identity_groups: tuple[tuple[str, ...], ...] = ()
    missing_preliminary_required_facts: tuple[str, ...] = ()
    unavailable_preliminary_required_groups: tuple[tuple[str, ...], ...] = ()
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


class CatalogAvailabilityStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


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
    availability_status: CatalogAvailabilityStatus = CatalogAvailabilityStatus.UNKNOWN
    matched_hard_facts: tuple[str, ...] = ()
    mismatched_hard_facts: tuple[str, ...] = ()
    missing_hard_facts: tuple[str, ...] = ()
    matched_soft_facts: tuple[str, ...] = ()
    mismatched_soft_facts: tuple[str, ...] = ()
    relaxations: tuple[CatalogRelaxation, ...] = ()
    # Set only by the catalogue planner's fail-closed availability-analogue
    # pass.  It is never inferred from a generic relaxed candidate.
    availability_analog: bool = False
    # True only when the buyer explicitly authorised this exact directional
    # relaxation and the planner re-checked it against the source snapshot.
    controlled_customer_relaxation: bool = False
    provenance: tuple[FactProvenance, ...] = ()
    # Present only when the exact requested flow is a verified table point.
    # It proves that point of the curve, not that the entire installation is
    # engineered correctly.
    passport_flow_head_evaluation: PassportFlowHeadEvaluation | None = None
    reason_codes: tuple[str, ...] = ()


class CatalogSearchPlan(FrozenModel):
    plan_id: str
    task_id: str
    goal_id: str | None = None
    contract_id: str
    product_kind: ProductKind
    requested_role: CatalogProductRole
    stages: tuple[CatalogSearchStage, ...]
    in_stock_required: bool = False
    hard_constraints: tuple[SearchConstraint, ...] = ()
    soft_constraints: tuple[SearchConstraint, ...] = ()
    unavailable_constraints: tuple[str, ...] = ()
    candidate_assessments: tuple[CandidateAssessment, ...] = ()
    eligible_skus: tuple[str, ...] = ()
    relaxed_skus: tuple[str, ...] = ()
    unverified_skus: tuple[str, ...] = ()
    # Exact candidates with a confirmed zero/negative stock balance that
    # justified an explicitly labelled in-stock availability analogue.
    availability_analog_exact_out_of_stock_skus: tuple[str, ...] = ()
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


class SelectionRequestAction(str, Enum):
    SHOW = "show"
    RECOMMEND = "recommend"
    CONTINUE_SELECTION = "continue_selection"


class SelectionResultStatus(str, Enum):
    SHOWN = "shown"
    NEED_CLARIFICATION = "need_clarification"
    NO_MATCH = "no_match"
    REJECTED = "rejected"


class SelectionFactInput(FrozenModel):
    name: str
    value: str | int | float | bool | None = None
    unit: str | None = None
    status: Literal["known", "unknown", "refused", "deferred"]
    polarity: Literal["required", "preferred", "excluded"] = "required"
    strength: Literal["hard", "soft"] = "hard"
    evidence: str = Field(default="", max_length=240)
    source: str
    source_turn: int = Field(ge=1)


class PresentedSelectionProduct(FrozenModel):
    sku: str
    name: str
    ordinal: int = Field(ge=1)


class SelectionRequest(FrozenModel):
    schema_version: Literal["1.0"] = CATALOG_CONTRACT_SCHEMA_VERSION
    original_utterance: str = Field(max_length=8_000)
    action: SelectionRequestAction
    task_id: str
    goal_id: str | None = None
    category: str
    product_kind: ProductKind
    contract_id: str | None = None
    known_facts: tuple[SelectionFactInput, ...] = ()
    hard_constraints: tuple[SearchConstraint, ...] = ()
    soft_constraints: tuple[SearchConstraint, ...] = ()
    explicitly_unknown_facts: tuple[str, ...] = ()
    current_product_focus: str | None = None
    previously_delivered_products: tuple[PresentedSelectionProduct, ...] = ()
    catalog_revision: str


class SelectionConstraintDisposition(FrozenModel):
    disposition: Literal["applied", "relaxed", "rejected", "unverified"]
    fact_name: str
    requested_value: str | int | float | bool | None = None
    candidate_sku: str | None = None
    reason_codes: tuple[str, ...] = ()


class SelectionProductCard(FrozenModel):
    sku: str
    name: str
    price: float
    currency: str
    stock_status: str
    stock_qty: int | None = None
    url: str
    image_url: str | None = None


class SelectionPresentationGroup(FrozenModel):
    """A source-checked grouping for preliminary cards.

    A group may only reference cards already included in ``SelectionResult``.
    Its fact value is intentionally presentation metadata, not a new search
    constraint or a customer fact.
    """

    fact_name: str
    label: str
    value: str
    card_skus: tuple[str, ...] = Field(min_length=1, max_length=12)


class SelectionSourceConflict(FrozenModel):
    """A customer requirement contradicted by a fact on a shown card.

    The conflict is presentation metadata derived from the same immutable
    source snapshot as the card.  It never weakens search constraints or turns
    a source value into a calculated suitability verdict.
    """

    customer_fact_name: str
    customer_value: str | int | float | bool
    customer_unit: str | None = None
    card_sku: str
    card_fact_name: str
    card_value: str | int | float | bool
    card_unit: str | None = None
    source_field: str
    reason_code: str


class SelectionResult(FrozenModel):
    schema_version: Literal["1.0"] = CATALOG_CONTRACT_SCHEMA_VERSION
    status: SelectionResultStatus
    selection_id: str
    task_id: str
    goal_id: str | None = None
    contract_id: str | None = None
    category: str
    product_kind: ProductKind
    applied_facts: tuple[SelectionFactInput, ...] = ()
    hard_constraints: tuple[SearchConstraint, ...] = ()
    soft_constraints: tuple[SearchConstraint, ...] = ()
    applied_filters: tuple[SelectionConstraintDisposition, ...] = ()
    constraint_dispositions: tuple[SelectionConstraintDisposition, ...] = ()
    missing_critical_fact: str | None = None
    candidates_before_filters: int = Field(default=0, ge=0)
    candidates_after_filters: int = Field(default=0, ge=0)
    ordered_skus: tuple[str, ...] = ()
    cards: tuple[SelectionProductCard, ...] = ()
    # Ordering is a customer-visible part of a delivered shortlist.  Keep the
    # reason typed so the renderer can explain price ordering without looking
    # back into customer prose or re-ranking cards.
    ordering_reason_codes: tuple[str, ...] = ()
    price_reference_selection_id: str | None = None
    price_reference_amount: float | None = None
    is_preliminary: bool = False
    preliminary_fact_names: tuple[str, ...] = ()
    presentation_groups: tuple[SelectionPresentationGroup, ...] = ()
    # A checked fallback for a confirmed unavailable exact boiler.  It is not
    # an exact fit and is rendered with the factual difference(s) below.
    availability_analog: bool = False
    availability_analog_differences: tuple[CatalogRelaxation, ...] = ()
    controlled_relaxation_differences: tuple[CatalogRelaxation, ...] = ()
    source_backed_conflicts: tuple[SelectionSourceConflict, ...] = ()
    passport_flow_head_evidence: tuple[PassportFlowHeadEvaluation, ...] = ()
    excluded_candidate_reason_codes: dict[str, tuple[str, ...]] = Field(
        default_factory=dict
    )
    catalog_revision: str
    outcome_gate_passed: bool = False
    customer_visible_state_updated: bool = False
    reason_code: str


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
