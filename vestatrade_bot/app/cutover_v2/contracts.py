"""Immutable, versioned contracts for the Stage 6A cutover boundary."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.answer_v2.contracts import AnswerPlanStatus
from app.catalog_v2.contracts import (
    CatalogProductRole,
    ProductKind,
    SelectionRequest,
    SelectionResult,
    SelectionResultStatus,
)
from app.comparison_v2.contracts import ComparisonRequest, ComparisonResult
from app.calculation_v2.contracts import CalculationRequest, CalculationResult
from app.compatibility_v2.contracts import CompatibilityRequest, CompatibilityResult
from app.offer_fact_v2.contracts import OfferFactRequest, OfferFactResult
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TaskAct
from app.models import ChatResponse


CUTOVER_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ResponseOwner(str, Enum):
    SAFETY = "safety"
    LEGACY = "legacy"
    V2 = "v2"


class ExecutionMode(str, Enum):
    SAFETY_INTERCEPT = "safety_intercept"
    LEGACY_ONLY = "legacy_only"
    SHADOW_COMPARE = "shadow_compare"
    V2_INTERNAL_CANARY = "v2_internal_canary"
    V2_PRIMARY = "v2_primary"
    LEGACY_FALLBACK = "legacy_fallback"


class ProductScopeEffect(str, Enum):
    """Explicit persistent-scope effect of one delivered V2 response.

    ``ChatResponse.products`` is a presentation payload, not a state command.
    A ProductFact may repeat one contextual card while the active Selection
    still contains several ordered products.  Only a checked SelectionResult
    may replace that ordered customer-visible scope.
    """

    PRESERVE = "preserve"
    REPLACE_FROM_SELECTION = "replace_from_selection"


class RolloutStage(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    INTERNAL_CANARY = "internal_canary"
    V2_PRIMARY = "v2_primary"
    RETIRED = "retired"


class EarlyControlOutcome(str, Enum):
    PASS = "pass"
    SAFETY_RESPONSE = "safety_response"
    EMERGENCY_RESPONSE = "emergency_response"
    PII_CONTROL = "pii_control"
    BLOCKED = "blocked"


class EarlyControlResult(FrozenModel):
    outcome: EarlyControlOutcome = EarlyControlOutcome.PASS
    response: ChatResponse | None = None
    reason_codes: tuple[str, ...] = ()


class MigrationCell(FrozenModel):
    schema_version: Literal["1.0"] = CUTOVER_SCHEMA_VERSION
    cell_id: str = Field(min_length=1, max_length=120)
    task_acts: tuple[TaskAct, ...]
    product_kinds: tuple[ProductKind, ...]
    product_contract_versions: tuple[str, ...] = ("1.0",)
    allowed_answer_statuses: tuple[AnswerPlanStatus, ...] = (
        AnswerPlanStatus.READY,
    )
    allowed_next_actions: tuple[NextActionKind, ...] = (
        NextActionKind.ANSWER_DIRECT_QUESTION,
    )
    required_catalog_revision: str | None = None
    stage: RolloutStage = RolloutStage.LEGACY
    canary_percent: int = Field(default=0, ge=0, le=100)
    external_actions_allowed: bool = False
    existing_sessions_allowed: bool = False
    require_single_exact_product: bool = True
    gate_artifact_ref: str | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_rollout_evidence(self) -> "MigrationCell":
        if not self.task_acts or not self.product_kinds:
            raise ValueError("migration cell requires typed acts and product kinds")
        if (
            ProductKind.UNSUPPORTED in self.product_kinds
            and self.stage
            in {
                RolloutStage.INTERNAL_CANARY,
                RolloutStage.V2_PRIMARY,
                RolloutStage.RETIRED,
            }
        ):
            raise ValueError("unsupported product kind cannot enter a live rollout stage")
        if self.stage == RolloutStage.INTERNAL_CANARY:
            if not self.gate_artifact_ref:
                raise ValueError("internal canary requires a gate artifact")
            if not 1 <= self.canary_percent <= 5:
                raise ValueError("internal canary must be bounded to 1-5 percent")
        elif self.stage in {RolloutStage.V2_PRIMARY, RolloutStage.RETIRED}:
            if not self.gate_artifact_ref:
                raise ValueError("primary/retired cells require rollout evidence")
        elif self.canary_percent:
            raise ValueError("legacy/shadow cells cannot assign canary traffic")
        return self


class MigrationRegistry(FrozenModel):
    schema_version: Literal["1.0"] = CUTOVER_SCHEMA_VERSION
    registry_id: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=160)
    cells: tuple[MigrationCell, ...] = ()

    @model_validator(mode="after")
    def unique_cells(self) -> "MigrationRegistry":
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("migration cell ids must be unique")
        return self


class MigrationReadinessRow(FrozenModel):
    cell_id: str
    task_act: TaskAct
    product_kind: ProductKind
    product_contract_version: str
    catalog_revision: str | None = None
    answer_status: AnswerPlanStatus
    rollout_stage: RolloutStage
    canary_eligible: bool = False
    blocked_reason_codes: tuple[str, ...] = ()


class CutoverDecision(FrozenModel):
    schema_version: Literal["1.0"] = CUTOVER_SCHEMA_VERSION
    owner_candidate: ResponseOwner
    execution_mode: ExecutionMode
    cell_id: str | None = None
    cohort_bucket: int | None = Field(default=None, ge=0, le=99)
    sticky_assignment_id: str | None = None
    eligible: bool = False
    reason_codes: tuple[str, ...] = ()
    required_stage_versions: dict[str, str] = Field(default_factory=dict)
    catalog_revision: str | None = None
    fallback_allowed: bool = True


class ProductFactDelivery(FrozenModel):
    """Checked direct-product fact carried across the cutover seam.

    This is deliberately a compact projection of the evidence service result,
    rather than a second evidence model.  It makes the delivered SKU,
    predicate and verifier decision observable in V2 telemetry without asking
    a renderer or an evaluator to recover them from response prose.
    """

    status: str
    canonical_sku: str | None = None
    predicate: str
    value: str | int | float | bool | None = None
    unit: str | None = None
    source_kind: str | None = None
    document: str | None = None
    section: str | None = None
    evidence_fragment: str | None = None
    verifier_status: str | None = None
    reason_code: str


class EngineeringBoundaryResult(FrozenModel):
    """A truthful boundary for an engineering calculation V2 cannot perform."""

    status: Literal["capability_not_ready"]
    topic: Literal["hydraulic_system_calculation"]
    task_id: str | None = None
    goal_id: str | None = None
    source_revision: str | None = None
    evidence: str
    required_inputs: tuple[str, ...]
    reason_codes: tuple[str, ...]


class V2TurnCandidate(FrozenModel):
    schema_version: Literal["1.0"] = CUTOVER_SCHEMA_VERSION
    turn_id: str
    response: ChatResponse | None = None
    state_before: DialogueStateV2
    state_after: DialogueStateV2
    answer_plan_id: str | None = None
    rendered_answer_id: str | None = None
    source_revision: str | None = None
    catalog_revision: str | None = None
    validation_status: str
    response_digest: str | None = None
    pending_command_ids: tuple[str, ...] = ()
    task_acts: tuple[TaskAct, ...] = ()
    product_kinds: tuple[ProductKind, ...] = ()
    contract_versions: tuple[str, ...] = ()
    answer_status: AnswerPlanStatus | None = None
    next_action: NextActionKind | None = None
    product_statuses: tuple[str, ...] = ()
    response_product_kinds: tuple[ProductKind, ...] = ()
    response_product_roles: tuple[CatalogProductRole, ...] = ()
    selection_request: SelectionRequest | None = None
    selection_result: SelectionResult | None = None
    comparison_request: ComparisonRequest | None = None
    comparison_result: ComparisonResult | None = None
    calculation_request: CalculationRequest | None = None
    calculation_result: CalculationResult | None = None
    product_fact_delivery: ProductFactDelivery | None = None
    offer_fact_request: OfferFactRequest | None = None
    offer_fact_result: OfferFactResult | None = None
    compatibility_request: CompatibilityRequest | None = None
    compatibility_result: CompatibilityResult | None = None
    engineering_boundary_result: EngineeringBoundaryResult | None = None
    product_scope_effect: ProductScopeEffect = ProductScopeEffect.PRESERVE
    # A direct fact may move the deictic focus (``этот``) without changing the
    # ordinal Selection scope (``первый/второй/...``).
    focus_product_sku: str | None = None
    semantic_accepted: bool = False
    contracts_resolved: bool = False
    external_side_effect_started: bool = False
    eligible_for_delivery: bool = False
    rejection_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_product_scope_effect(self) -> "V2TurnCandidate":
        if self.product_scope_effect != ProductScopeEffect.REPLACE_FROM_SELECTION:
            return self
        result = self.selection_result
        if (
            result is None
            or result.status != SelectionResultStatus.SHOWN
            or not result.outcome_gate_passed
            or self.response is None
        ):
            raise ValueError(
                "only a gated shown SelectionResult may replace product scope"
            )
        response_skus = tuple(product.sku for product in self.response.products)
        if response_skus != result.ordered_skus:
            raise ValueError(
                "selection scope replacement must match the delivered card order"
            )
        return self


class ParityDifference(FrozenModel):
    dimension: str
    severity: Literal["p2", "p1", "p0"]
    legacy_value: str | int | float | bool | tuple[str, ...] | None = None
    v2_value: str | int | float | bool | tuple[str, ...] | None = None
    reason_code: str


class ParityAssessment(FrozenModel):
    status: Literal[
        "parity",
        "acceptable_difference",
        "regression",
        "unavailable",
    ]
    severity: Literal["none", "p2", "p1", "p0"] = "none"
    compared_dimensions: tuple[str, ...] = ()
    differences: tuple[ParityDifference, ...] = ()
    gate_blocking_reason_codes: tuple[str, ...] = ()


class TurnArbitration(FrozenModel):
    response_owner: ResponseOwner
    execution_mode: ExecutionMode
    response: ChatResponse | None = None
    selected_state: DialogueStateV2 | None = None
    fallback_required: bool = False
    external_fallback_forbidden: bool = False
    reason_codes: tuple[str, ...] = ()


class TurnCommit(FrozenModel):
    schema_version: Literal["1.0"] = CUTOVER_SCHEMA_VERSION
    commit_id: str
    turn_id: str
    response_owner: ResponseOwner
    execution_mode: ExecutionMode
    session_revision_before: int = Field(ge=0)
    session_revision_after: int = Field(ge=0)
    response_digest: str
    selected_state_revision: str
    answer_plan_id: str | None = None
    catalog_revision: str | None = None
    live_epoch_id: str | None = None
    external_command_ids: tuple[str, ...] = ()
    committed: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def monotonic_revision(self) -> "TurnCommit":
        if self.committed and self.session_revision_after <= self.session_revision_before:
            raise ValueError("committed turn must advance the session revision")
        return self
