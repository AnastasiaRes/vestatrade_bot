"""Versioned, immutable contracts for the Stage 2 shadow dialogue path.

The models deliberately contain neither catalogue results nor response text.
They describe the customer's tasks and facts only.  Frozen models and tuples
make accidental state mutation outside ``reducer.py`` fail immediately.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.catalog_v2.contracts import CatalogPlanningResult, ProductKind, ReadinessStatus
from app.commerce_v2.contracts import (
    CommerceOutboxEntry,
    CommercePlanningResult,
    CommerceWorkflowKind,
    CommerceWorkflowState,
    ConsentStatus,
    SensitiveValueRef,
    WorkflowControlKind,
    WorkflowControlSignal,
)


DIALOGUE_STATE_SCHEMA_VERSION = "2.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class TaskAct(str, Enum):
    FIND = "find"
    SELECT = "select"
    COMPARE = "compare"
    EXPLAIN = "explain"
    CALCULATE = "calculate"
    CHECK_PRICE = "check_price"
    CHECK_STOCK = "check_stock"
    GET_LINK = "get_link"
    REQUEST_QUOTE = "request_quote"
    REQUEST_INVOICE = "request_invoice"
    RESERVE_PRODUCT = "reserve_product"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    ORDER_STATUS = "order_status"
    CHECK_DELIVERY = "check_delivery"
    RETURN_PRODUCT = "return_product"
    WARRANTY = "warranty"
    COMPLAINT = "complaint"
    CONTACT_STORE = "contact_store"
    HANDOFF = "handoff"
    GREETING = "greeting"
    GRATITUDE = "gratitude"
    OTHER = "other"


class ProductRole(str, Enum):
    TARGET = "target"
    CONTEXT = "context"
    EXISTING = "existing"
    ACCESSORY = "accessory"
    ALTERNATIVE = "alternative"
    UNKNOWN = "unknown"


class ProductCategory(str, Enum):
    PUMPS = "pumps"
    PIPES = "pipes"
    BOILERS = "boilers"
    WATER_HEATERS = "water_heaters"
    HYDRAULIC_ACCUMULATORS = "hydraulic_accumulators"
    FILTERS = "filters"
    CONTROLS = "controls"
    VALVES = "valves"
    SEWER = "sewer"
    RADIATOR_FITTINGS = "radiator_fittings"
    RADIATORS = "radiators"
    FITTINGS = "fittings"
    METERS = "meters"
    SANITARY_WARE = "sanitary_ware"
    INSTALLATION_SYSTEMS = "installation_systems"
    OTHER = "other"


class ConstraintStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    REFUSED = "refused"
    DEFERRED = "deferred"


class ConstraintPolarity(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    EXCLUDED = "excluded"


class ConstraintStrength(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class ProgressKind(str, Enum):
    NEW_TASK_CREATED = "new_task_created"
    GOAL_REFINED = "goal_refined"
    CONSTRAINT_ADDED = "constraint_added"
    CONSTRAINT_CORRECTED = "constraint_corrected"
    UNKNOWN_REGISTERED = "unknown_registered"
    DIRECT_QUESTION_REGISTERED = "direct_question_registered"
    DIRECT_QUESTION_ANSWERED = "direct_question_answered"
    TASK_SWITCHED = "task_switched"
    TASK_RETURNED = "task_returned"
    NO_PROGRESS = "no_progress"


class NextActionKind(str, Enum):
    ANSWER_DIRECT_QUESTION = "answer_direct_question"
    ASK_DECISION_CHANGING_QUESTION = "ask_decision_changing_question"
    EXPLAIN_TERM_OR_METHOD = "explain_term_or_method"
    SEARCH_EXACT = "search_exact"
    SHOW_PRELIMINARY_OPTIONS = "show_preliminary_options"
    COMPARE = "compare"
    CALCULATE_PRELIMINARY = "calculate_preliminary"
    START_OR_CONTINUE_HANDOFF = "start_or_continue_handoff"
    CLOSE_TASK = "close_task"
    WAIT_FOR_SEMANTIC_UNDERSTANDING = "wait_for_semantic_understanding"
    ANSWER_VERIFIED_COMMERCE_QUESTION = "answer_verified_commerce_question"
    COLLECT_COMMERCE_FACT = "collect_commerce_fact"
    PREVIEW_COMMERCE_REQUEST = "preview_commerce_request"
    REQUEST_SCOPED_CONSENT = "request_scoped_consent"
    PREPARE_COMMERCE_COMMAND = "prepare_commerce_command"
    REPORT_COMMERCE_EXECUTION_STATUS = "report_commerce_execution_status"
    STATE_COMMERCE_CAPABILITY_BOUNDARY = "state_commerce_capability_boundary"
    ACKNOWLEDGE_COMMERCE_OPT_OUT = "acknowledge_commerce_opt_out"
    EXPLAIN_HOW_TO_FIND_FACT = "explain_how_to_find_fact"
    CONTINUE_WITH_CONFIRMED_FACTS = "continue_with_confirmed_facts"
    PRESENT_CONTROLLED_ANALOG = "present_controlled_analog"
    OFFER_VERIFIABLE_EXTERNAL_STEP = "offer_verifiable_external_step"
    STATE_CAPABILITY_BOUNDARY = "state_capability_boundary"


class ResponseStrategyKind(str, Enum):
    ASK_DECISION_FACT = "ask_decision_fact"
    EXPLAIN_HOW_TO_FIND_FACT = "explain_how_to_find_fact"
    SHOW_PRELIMINARY_OPTIONS = "show_preliminary_options"
    CONTINUE_WITH_CONFIRMED_FACTS = "continue_with_confirmed_facts"
    PRESENT_CONTROLLED_ANALOG = "present_controlled_analog"
    OFFER_VERIFIABLE_EXTERNAL_STEP = "offer_verifiable_external_step"
    STATE_CAPABILITY_BOUNDARY = "state_capability_boundary"
    CLOSE_TASK = "close_task"


class ShadowDeliveryStatus(str, Enum):
    NOT_PLANNED = "not_planned"
    SHADOW_NOT_DELIVERED = "shadow_not_delivered"
    REJECTED = "rejected"


class TurnMetadata(FrozenModel):
    turn_id: str = Field(min_length=1, max_length=160)
    source: str = Field(default="semantic_interpreter", min_length=1, max_length=80)


class ProductGoal(FrozenModel):
    goal_id: str
    canonical_type: str | None = None
    category: ProductCategory = ProductCategory.OTHER
    role: ProductRole = ProductRole.UNKNOWN
    evidence: str = Field(default="", max_length=240)
    source: str
    confidence: float = Field(ge=0.0, le=1.0)
    confirmed_turn: int = Field(ge=1)
    type_locked: bool = False
    category_locked: bool = False


class CustomerTask(FrozenModel):
    task_id: str
    act: TaskAct
    target_goal_id: str | None = None
    priority: int = Field(ge=0)
    status: TaskStatus = TaskStatus.PENDING
    source: str
    source_turn: int = Field(ge=1)
    blocking_reason: str | None = None
    completed_subtasks: tuple[str, ...] = ()
    pending_subtasks: tuple[str, ...] = ()
    related_task_ids: tuple[str, ...] = ()


class ConstraintFactV2(FrozenModel):
    fact_id: str
    name: str = Field(min_length=1, max_length=120)
    value: str | int | float | bool | None = None
    unit: str | None = Field(default=None, max_length=40)
    status: ConstraintStatus = ConstraintStatus.KNOWN
    polarity: ConstraintPolarity = ConstraintPolarity.REQUIRED
    strength: ConstraintStrength = ConstraintStrength.HARD
    evidence: str = Field(default="", max_length=240)
    source: str
    confidence: float = Field(ge=0.0, le=1.0)
    goal_id: str | None = None
    task_id: str | None = None
    source_turn: int = Field(ge=1)
    replaces_fact_id: str | None = None
    active: bool = True

    @model_validator(mode="after")
    def validate_status_value(self) -> "ConstraintFactV2":
        if self.status == ConstraintStatus.KNOWN and self.value is None:
            raise ValueError("known constraint must contain a value")
        if self.status != ConstraintStatus.KNOWN and self.value is not None:
            raise ValueError("unknown/refused/deferred constraint cannot contain a value")
        return self


class DirectQuestion(FrozenModel):
    question_id: str
    act: TaskAct
    task_id: str
    goal_id: str | None = None
    source_turn: int = Field(ge=1)
    resolved: bool = False


class Ambiguity(FrozenModel):
    ambiguity_id: str
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    evidence: str = Field(default="", max_length=240)
    source_turn: int = Field(ge=1)
    decision_changing: bool = True
    resolved: bool = False


class ProgressState(FrozenModel):
    primary: ProgressKind = ProgressKind.NO_PROGRESS
    changes: tuple[ProgressKind, ...] = ()
    reason_codes: tuple[str, ...] = ()
    source_turn: int = Field(default=0, ge=0)


class NextAction(FrozenModel):
    kind: NextActionKind
    task_id: str | None = None
    fact_name: str | None = None
    reason_code: str


class NextActionPlan(FrozenModel):
    primary: NextAction
    secondary: NextAction | None = None
    reason_codes: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    blocking_facts: tuple[str, ...] = ()


class TaskStack(FrozenModel):
    active_task_id: str | None = None
    pending_task_ids: tuple[str, ...] = ()
    suspended_task_ids: tuple[str, ...] = ()
    completed_task_ids: tuple[str, ...] = ()


class AnswerPlanSummary(FrozenModel):
    plan_id: str
    semantic_signature: str
    task_ids: tuple[str, ...] = ()
    primary_action: NextActionKind
    question_fact: str | None = None
    next_step_kind: str
    validation_status: str
    delivery_status: ShadowDeliveryStatus = ShadowDeliveryStatus.SHADOW_NOT_DELIVERED
    source_turn: int = Field(ge=0)


class TaskStrategyState(FrozenModel):
    task_id: str
    consecutive_no_progress: int = Field(default=0, ge=0)
    attempted_strategies: tuple[ResponseStrategyKind, ...] = ()
    last_strategy: ResponseStrategyKind | None = None
    last_question_fact: str | None = None
    last_plan_signature: str | None = None
    last_catalog_signature: str | None = None
    last_commerce_signature: str | None = None
    last_turn: int = Field(default=0, ge=0)
    delivery_status: ShadowDeliveryStatus = ShadowDeliveryStatus.NOT_PLANNED


class DialogueStateV2(FrozenModel):
    schema_version: Literal["2.0"] = DIALOGUE_STATE_SCHEMA_VERSION
    turn_number: int = Field(default=0, ge=0)
    task_stack: TaskStack = Field(default_factory=TaskStack)
    tasks: tuple[CustomerTask, ...] = ()
    product_goals: tuple[ProductGoal, ...] = ()
    active_goal_id: str | None = None
    constraints: tuple[ConstraintFactV2, ...] = ()
    direct_questions: tuple[DirectQuestion, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    progress: ProgressState = Field(default_factory=ProgressState)
    last_policy: NextActionPlan | None = None
    catalog_planning: CatalogPlanningResult | None = None
    commerce_workflows: tuple[CommerceWorkflowState, ...] = ()
    commerce_sensitive_values: tuple[SensitiveValueRef, ...] = ()
    commerce_controls: tuple[WorkflowControlSignal, ...] = ()
    commerce_outbox: tuple[CommerceOutboxEntry, ...] = ()
    commerce_planning: CommercePlanningResult | None = None
    answer_plan_summary: AnswerPlanSummary | None = None
    response_strategy_history: tuple[TaskStrategyState, ...] = ()
    applied_turn_ids: tuple[str, ...] = ()


class StateEventBase(FrozenModel):
    turn_id: str
    turn_number: int = Field(ge=0)


class TaskCreated(StateEventBase):
    event_type: Literal["task_created"] = "task_created"
    task_id: str
    act: TaskAct
    goal_id: str | None = None


class TaskSuspended(StateEventBase):
    event_type: Literal["task_suspended"] = "task_suspended"
    task_id: str
    reason_code: str


class TaskResumed(StateEventBase):
    event_type: Literal["task_resumed"] = "task_resumed"
    task_id: str


class TaskCompleted(StateEventBase):
    event_type: Literal["task_completed"] = "task_completed"
    task_id: str
    final_status: TaskStatus


class ProductGoalConfirmed(StateEventBase):
    event_type: Literal["product_goal_confirmed"] = "product_goal_confirmed"
    goal_id: str
    role: ProductRole


class ProductGoalCorrected(StateEventBase):
    event_type: Literal["product_goal_corrected"] = "product_goal_corrected"
    goal_id: str
    changed_fields: tuple[str, ...]


class ConstraintAdded(StateEventBase):
    event_type: Literal["constraint_added"] = "constraint_added"
    fact_id: str
    name: str


class ConstraintCorrected(StateEventBase):
    event_type: Literal["constraint_corrected"] = "constraint_corrected"
    fact_id: str
    replaced_fact_id: str
    name: str


class ConstraintMarkedUnknown(StateEventBase):
    event_type: Literal["constraint_marked_unknown"] = "constraint_marked_unknown"
    fact_id: str
    name: str


class ConstraintRefused(StateEventBase):
    event_type: Literal["constraint_refused"] = "constraint_refused"
    fact_id: str
    name: str


class ConstraintDeferred(StateEventBase):
    event_type: Literal["constraint_deferred"] = "constraint_deferred"
    fact_id: str
    name: str


class DirectQuestionRegistered(StateEventBase):
    event_type: Literal["direct_question_registered"] = "direct_question_registered"
    question_id: str
    task_id: str
    act: TaskAct


class AmbiguityRegistered(StateEventBase):
    event_type: Literal["ambiguity_registered"] = "ambiguity_registered"
    ambiguity_id: str
    kind: str


class TurnIgnoredAsDuplicate(StateEventBase):
    event_type: Literal["turn_ignored_as_duplicate"] = "turn_ignored_as_duplicate"
    reason_code: str = "duplicate_turn_id"


class PolicyDecisionRecorded(StateEventBase):
    event_type: Literal["policy_decision_recorded"] = "policy_decision_recorded"
    primary: NextActionKind
    secondary: NextActionKind | None = None


class ProductContractResolved(StateEventBase):
    event_type: Literal["product_contract_resolved"] = "product_contract_resolved"
    task_id: str
    contract_id: str
    product_kind: ProductKind


class TaskReadinessAssessed(StateEventBase):
    event_type: Literal["task_readiness_assessed"] = "task_readiness_assessed"
    task_id: str
    status: ReadinessStatus


class CatalogPlanCreated(StateEventBase):
    event_type: Literal["catalog_plan_created"] = "catalog_plan_created"
    task_id: str
    plan_id: str


class CatalogCandidateRejected(StateEventBase):
    event_type: Literal["catalog_candidate_rejected"] = "catalog_candidate_rejected"
    task_id: str
    sku: str
    reason_codes: tuple[str, ...]


class CatalogRelaxationRecorded(StateEventBase):
    event_type: Literal["catalog_relaxation_recorded"] = "catalog_relaxation_recorded"
    task_id: str
    sku: str
    fact_name: str


class CatalogNoMatchRecorded(StateEventBase):
    event_type: Literal["catalog_no_match_recorded"] = "catalog_no_match_recorded"
    task_id: str
    reason_code: str


class SolutionPlanCreated(StateEventBase):
    event_type: Literal["solution_plan_created"] = "solution_plan_created"
    solution_id: str
    task_ids: tuple[str, ...]


class CommerceSensitiveFactLinked(StateEventBase):
    event_type: Literal["commerce_sensitive_fact_linked"] = (
        "commerce_sensitive_fact_linked"
    )
    ref_id: str
    field_name: str


class CommerceWorkflowControlRegistered(StateEventBase):
    event_type: Literal["commerce_workflow_control_registered"] = (
        "commerce_workflow_control_registered"
    )
    control_id: str
    control_kind: WorkflowControlKind


class CommerceWorkflowCreated(StateEventBase):
    event_type: Literal["commerce_workflow_created"] = "commerce_workflow_created"
    workflow_id: str
    workflow_kind: CommerceWorkflowKind


class CommercePayloadRevised(StateEventBase):
    event_type: Literal["commerce_payload_revised"] = "commerce_payload_revised"
    workflow_id: str
    payload_revision: int


class CommercePreviewPrepared(StateEventBase):
    event_type: Literal["commerce_preview_prepared"] = "commerce_preview_prepared"
    workflow_id: str
    payload_revision: int


class CommerceConsentChanged(StateEventBase):
    event_type: Literal["commerce_consent_changed"] = "commerce_consent_changed"
    workflow_id: str
    consent_status: ConsentStatus


class CommerceCapabilityBoundaryRecorded(StateEventBase):
    event_type: Literal["commerce_capability_boundary_recorded"] = (
        "commerce_capability_boundary_recorded"
    )
    workflow_id: str
    reason_code: str


class CommerceCommandPrepared(StateEventBase):
    event_type: Literal["commerce_command_prepared"] = "commerce_command_prepared"
    workflow_id: str
    command_id: str
    payload_revision: int


class CommerceCommandIgnoredAsDuplicate(StateEventBase):
    event_type: Literal["commerce_command_ignored_as_duplicate"] = (
        "commerce_command_ignored_as_duplicate"
    )
    workflow_id: str
    reason_code: str = "duplicate_command_ignored"


class CommerceLocalDraftRecorded(StateEventBase):
    event_type: Literal["commerce_local_draft_recorded"] = (
        "commerce_local_draft_recorded"
    )
    workflow_id: str
    command_id: str


class CommerceDeliveryConfirmed(StateEventBase):
    event_type: Literal["commerce_delivery_confirmed"] = (
        "commerce_delivery_confirmed"
    )
    workflow_id: str
    command_id: str
    receipt_ref: str


class CommerceDeliveryFailed(StateEventBase):
    event_type: Literal["commerce_delivery_failed"] = "commerce_delivery_failed"
    workflow_id: str
    command_id: str
    reason_code: str


class CommerceDeliveryUnknown(StateEventBase):
    event_type: Literal["commerce_delivery_unknown"] = "commerce_delivery_unknown"
    workflow_id: str
    command_id: str
    reason_code: str


class TaskProgressRecorded(StateEventBase):
    event_type: Literal["task_progress_recorded"] = "task_progress_recorded"
    task_id: str
    progress_status: Literal["progress", "no_progress", "neutral"]
    consecutive_no_progress: int = Field(ge=0)


class ResponseStrategySelected(StateEventBase):
    event_type: Literal["response_strategy_selected"] = "response_strategy_selected"
    task_id: str
    strategy: ResponseStrategyKind


class ResponseStrategyEscalated(StateEventBase):
    event_type: Literal["response_strategy_escalated"] = (
        "response_strategy_escalated"
    )
    task_id: str
    previous_strategy: ResponseStrategyKind | None = None
    strategy: ResponseStrategyKind


class AnswerPlanCreated(StateEventBase):
    event_type: Literal["answer_plan_created"] = "answer_plan_created"
    plan_id: str
    semantic_signature: str


class AnswerPlanValidated(StateEventBase):
    event_type: Literal["answer_plan_validated"] = "answer_plan_validated"
    plan_id: str
    validation_status: str


class AnswerPlanRejected(StateEventBase):
    event_type: Literal["answer_plan_rejected"] = "answer_plan_rejected"
    plan_id: str
    reason_codes: tuple[str, ...] = ()


class ShadowResponseNotDelivered(StateEventBase):
    event_type: Literal["shadow_response_not_delivered"] = (
        "shadow_response_not_delivered"
    )
    plan_id: str


ReducerEvent: TypeAlias = (
    TaskCreated
    | TaskSuspended
    | TaskResumed
    | TaskCompleted
    | ProductGoalConfirmed
    | ProductGoalCorrected
    | ConstraintAdded
    | ConstraintCorrected
    | ConstraintMarkedUnknown
    | ConstraintRefused
    | ConstraintDeferred
    | DirectQuestionRegistered
    | AmbiguityRegistered
    | TurnIgnoredAsDuplicate
    | PolicyDecisionRecorded
    | ProductContractResolved
    | TaskReadinessAssessed
    | CatalogPlanCreated
    | CatalogCandidateRejected
    | CatalogRelaxationRecorded
    | CatalogNoMatchRecorded
    | SolutionPlanCreated
    | CommerceSensitiveFactLinked
    | CommerceWorkflowControlRegistered
    | CommerceWorkflowCreated
    | CommercePayloadRevised
    | CommercePreviewPrepared
    | CommerceConsentChanged
    | CommerceCapabilityBoundaryRecorded
    | CommerceCommandPrepared
    | CommerceCommandIgnoredAsDuplicate
    | CommerceLocalDraftRecorded
    | CommerceDeliveryConfirmed
    | CommerceDeliveryFailed
    | CommerceDeliveryUnknown
    | TaskProgressRecorded
    | ResponseStrategySelected
    | ResponseStrategyEscalated
    | AnswerPlanCreated
    | AnswerPlanValidated
    | AnswerPlanRejected
    | ShadowResponseNotDelivered
)


class RejectedProposal(FrozenModel):
    proposal_type: str
    reason_code: str
    evidence: str = Field(default="", max_length=240)
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticConflict(FrozenModel):
    conflict_type: str
    reason_code: str
    existing_id: str | None = None
    proposed_value: str | int | float | bool | None = None


class ReductionResult(FrozenModel):
    state: DialogueStateV2
    events: tuple[ReducerEvent, ...] = ()
    rejected_proposals: tuple[RejectedProposal, ...] = ()
    progress: ProgressState
    conflicts: tuple[DiagnosticConflict, ...] = ()
