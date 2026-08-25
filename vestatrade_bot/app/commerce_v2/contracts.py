"""Versioned immutable contracts for commerce/order/handoff shadow workflows.

The models intentionally store only catalogue/task identifiers and opaque
references to sensitive values.  They never contain contact details, order
numbers, addresses, company requisites, raw messages or generated reply text.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


COMMERCE_SCHEMA_VERSION = "1.0"


class CommerceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CommerceWorkflowKind(str, Enum):
    REQUEST_QUOTE = "request_quote"
    REQUEST_INVOICE = "request_invoice"
    RESERVE_PRODUCT = "reserve_product"
    PLACE_ORDER = "place_order"
    ORDER_STATUS = "order_status"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    CHECK_DELIVERY = "check_delivery"
    RETURN_PRODUCT = "return_product"
    WARRANTY = "warranty"
    COMPLAINT = "complaint"
    HANDOFF = "handoff"


class CommerceWorkflowStatus(str, Enum):
    COLLECTING = "collecting"
    READY_FOR_PREVIEW = "ready_for_preview"
    AWAITING_CONSENT = "awaiting_consent"
    CONSENTED = "consented"
    READY_TO_EXECUTE = "ready_to_execute"
    QUEUED = "queued"
    LOCAL_DRAFT_SAVED = "local_draft_saved"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    BLOCKED = "blocked"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class CommerceFieldStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    REFUSED = "refused"
    DEFERRED = "deferred"


class SensitiveValueKind(str, Enum):
    CONTACT = "contact"
    ORDER_REFERENCE = "order_reference"
    DELIVERY_ADDRESS = "delivery_address"
    PERSON_NAME = "person_name"
    COMPANY_REQUISITES = "company_requisites"
    TAX_IDENTIFIER = "tax_identifier"
    OTHER = "other"


class ConsentStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    AWAITING = "awaiting"
    GRANTED = "granted"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    STALE = "stale"


class WorkflowControlKind(str, Enum):
    CONFIRM = "confirm"
    DECLINE = "decline"
    WITHDRAW_CONSENT = "withdraw_consent"
    OPT_OUT = "opt_out"
    RESUME_AFTER_OPT_OUT = "resume_after_opt_out"


class CapabilityMode(str, Enum):
    VERIFIED_STATIC = "verified_static"
    READ_ONLY_EXTERNAL = "read_only_external"
    LOCAL_DRAFT_ONLY = "local_draft_only"
    TRANSACTIONAL_EXTERNAL = "transactional_external"
    UNAVAILABLE = "unavailable"


class CommerceExecutionStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PREPARED = "prepared"
    QUEUED = "queued"
    LOCAL_DRAFT_SAVED = "local_draft_saved"
    DELIVERED = "delivered"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    CANCELLED = "cancelled"


class OutboxStatus(str, Enum):
    PREPARED = "prepared"
    READY = "ready"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    CANCELLED = "cancelled"
    DUPLICATE_IGNORED = "duplicate_ignored"


class CommerceReadinessStatus(str, Enum):
    NEEDS_BUSINESS_FACT = "needs_business_fact"
    NEEDS_CUSTOMER_FACT = "needs_customer_fact"
    NEEDS_PRODUCT_SELECTION = "needs_product_selection"
    NEEDS_PREVIEW = "needs_preview"
    NEEDS_CONSENT = "needs_consent"
    READY_TO_PREPARE = "ready_to_prepare"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    LOCAL_DRAFT_ONLY = "local_draft_only"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class WorkflowResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNSUPPORTED = "unsupported"


class CommerceCommandStatus(str, Enum):
    PREPARED = "prepared"
    READY = "ready"
    CANCELLED = "cancelled"


class SensitiveValueRef(CommerceModel):
    """Opaque pointer to a value kept outside DialogueStateV2."""

    ref_id: str = Field(min_length=1, max_length=160)
    kind: SensitiveValueKind
    field_name: str = Field(min_length=1, max_length=120)
    status: CommerceFieldStatus = CommerceFieldStatus.KNOWN
    source: str = Field(min_length=1, max_length=80)
    source_turn: int = Field(ge=0)
    active: bool = True
    replaces_ref_id: str | None = None


class WorkflowControlSignal(CommerceModel):
    control_id: str
    kind: WorkflowControlKind
    source_turn: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=80)
    applied_workflow_id: str | None = None
    rejected_reason: str | None = None


class CommercePayloadField(CommerceModel):
    name: str = Field(min_length=1, max_length=120)
    status: CommerceFieldStatus
    value: str | int | float | bool | None = None
    unit: str | None = Field(default=None, max_length=40)
    source_fact_id: str | None = None
    sensitive_ref_id: str | None = None

    @model_validator(mode="after")
    def validate_value_source(self) -> "CommercePayloadField":
        if self.status == CommerceFieldStatus.KNOWN:
            if self.value is None and self.sensitive_ref_id is None:
                raise ValueError(
                    "known commerce field requires value or opaque reference"
                )
        elif self.value is not None or self.sensitive_ref_id is not None:
            raise ValueError("unknown/refused/deferred fields cannot contain values")
        return self


class CommerceLineItem(CommerceModel):
    """One explicitly identified product line; catalogue candidates never qualify."""

    line_id: str
    goal_id: str | None = None
    product_ref: str | None = None
    product_status: CommerceFieldStatus | None = None
    quantity: str | int | float | None = None
    quantity_unit: str | None = Field(default=None, max_length=40)
    quantity_status: CommerceFieldStatus | None = None
    source_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_explicit_line_values(self) -> "CommerceLineItem":
        if self.product_status == CommerceFieldStatus.KNOWN and not self.product_ref:
            raise ValueError("known commerce line product requires an explicit ref")
        if (
            self.product_status is not None
            and self.product_status != CommerceFieldStatus.KNOWN
            and self.product_ref is not None
        ):
            raise ValueError("unavailable commerce line product cannot contain a ref")
        if self.quantity_status == CommerceFieldStatus.KNOWN and self.quantity is None:
            raise ValueError("known commerce line quantity requires a value")
        if (
            self.quantity_status is not None
            and self.quantity_status != CommerceFieldStatus.KNOWN
            and self.quantity is not None
        ):
            raise ValueError(
                "unavailable commerce line quantity cannot contain a value"
            )
        return self


class ConsentState(CommerceModel):
    status: ConsentStatus = ConsentStatus.NOT_REQUESTED
    workflow_id: str | None = None
    operation: CommerceWorkflowKind | None = None
    payload_revision: int | None = Field(default=None, ge=1)
    capability_id: str | None = None
    source_turn: int | None = Field(default=None, ge=1)
    source: str | None = None
    scope_fingerprint: str | None = None
    invalidation_reason: str | None = None


class CommerceWorkflowState(CommerceModel):
    schema_version: Literal["1.0"] = COMMERCE_SCHEMA_VERSION
    workflow_id: str
    contract_id: str
    workflow_kind: CommerceWorkflowKind
    task_ids: tuple[str, ...]
    goal_ids: tuple[str, ...] = ()
    payload_revision: int = Field(default=1, ge=1)
    payload_fingerprint: str = ""
    status: CommerceWorkflowStatus = CommerceWorkflowStatus.COLLECTING
    fields: tuple[CommercePayloadField, ...] = ()
    line_items: tuple[CommerceLineItem, ...] = ()
    missing_fields: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    refused_fields: tuple[str, ...] = ()
    deferred_fields: tuple[str, ...] = ()
    product_refs: tuple[str, ...] = ()
    solution_id: str | None = None
    preview_revision: int | None = Field(default=None, ge=1)
    consent: ConsentState = Field(default_factory=ConsentState)
    opt_out: bool = False
    capability_id: str
    capability_mode: CapabilityMode
    command_id: str | None = None
    execution_status: CommerceExecutionStatus = CommerceExecutionStatus.NOT_REQUESTED
    external_receipt_ref: str | None = None
    reason_codes: tuple[str, ...] = ()
    created_turn: int = Field(ge=1)
    updated_turn: int = Field(ge=1)
    applied_control_ids: tuple[str, ...] = ()


class WorkflowFieldDefinition(CommerceModel):
    name: str
    required: bool = False
    decision_changing: bool = False
    sensitive: bool = False
    sensitive_kind: SensitiveValueKind | None = None
    allows_unknown: bool = True


class CommerceWorkflowContract(CommerceModel):
    schema_version: Literal["1.0"] = COMMERCE_SCHEMA_VERSION
    contract_id: str
    workflow_kind: CommerceWorkflowKind
    supported_task_acts: tuple[str, ...]
    field_definitions: tuple[WorkflowFieldDefinition, ...] = ()
    required_fields: tuple[str, ...] = ()
    decision_changing_fields: tuple[str, ...] = ()
    sensitive_fields: tuple[str, ...] = ()
    requires_product_selection: bool = False
    requires_quantities: bool = False
    requires_contact: bool = False
    requires_preview: bool = True
    requires_consent: bool = True
    capability_id: str
    local_draft_allowed: bool = False
    allowed_transitions: tuple[str, ...] = ()
    terminal_statuses: tuple[CommerceWorkflowStatus, ...] = (
        CommerceWorkflowStatus.DELIVERED,
        CommerceWorkflowStatus.CANCELLED,
        CommerceWorkflowStatus.COMPLETED,
    )
    consent_invalidation_fields: tuple[str, ...] = ()
    idempotency_scope: tuple[str, ...] = ("workflow_id", "payload_revision")
    unavailable_reason_code: str = "commerce_capability_unavailable"


class CommerceCapability(CommerceModel):
    capability_id: str
    operation: CommerceWorkflowKind
    mode: CapabilityMode
    source: str
    required_fields: tuple[str, ...] = ()
    contains_pii: bool = False
    requires_consent: bool = False
    has_verifiable_receipt: bool = False
    result_verifiable: bool = False
    verified_fact_keys: tuple[str, ...] = ()
    drafted_fact_keys: tuple[str, ...] = ()
    reason_code: str


class CommerceCapabilitySnapshot(CommerceModel):
    schema_version: Literal["1.0"] = COMMERCE_SCHEMA_VERSION
    capabilities: tuple[CommerceCapability, ...] = ()
    source_revision: str = "stage4-audit-v1"

    def get(self, capability_id: str) -> CommerceCapability | None:
        return next(
            (item for item in self.capabilities if item.capability_id == capability_id),
            None,
        )


class CommerceContextSnapshot(CommerceModel):
    contact_ref: SensitiveValueRef | None = None
    business_fact_keys: tuple[str, ...] = ()
    drafted_business_fact_keys: tuple[str, ...] = ()
    legacy_handoff_status: str | None = None
    legacy_has_pending_handoff: bool = False
    legacy_handoff_ticket_present: bool = False


class CommerceWorkflowResolution(CommerceModel):
    status: WorkflowResolutionStatus
    workflow_id: str
    task_ids: tuple[str, ...]
    contract_id: str | None = None
    workflow_kind: CommerceWorkflowKind | None = None
    capability_id: str | None = None
    reason_codes: tuple[str, ...] = ()


class CommerceReadinessAssessment(CommerceModel):
    workflow_id: str
    task_ids: tuple[str, ...]
    contract_id: str
    workflow_kind: CommerceWorkflowKind
    status: CommerceReadinessStatus
    confirmed_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    refused_fields: tuple[str, ...] = ()
    deferred_fields: tuple[str, ...] = ()
    sensitive_ref_ids: tuple[str, ...] = ()
    product_refs: tuple[str, ...] = ()
    consent_status: ConsentStatus = ConsentStatus.NOT_REQUESTED
    capability_mode: CapabilityMode = CapabilityMode.UNAVAILABLE
    blocking_facts: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    recommended_next_field: str | None = None


class CommerceCommand(CommerceModel):
    schema_version: Literal["1.0"] = COMMERCE_SCHEMA_VERSION
    command_id: str
    workflow_id: str
    payload_revision: int = Field(ge=1)
    operation: CommerceWorkflowKind
    capability_id: str
    idempotency_key: str
    payload_ref_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    product_refs: tuple[str, ...] = ()
    line_items: tuple[CommerceLineItem, ...] = ()
    solution_id: str | None = None
    consent_scope_fingerprint: str | None = None
    status: CommerceCommandStatus = CommerceCommandStatus.PREPARED
    reason_codes: tuple[str, ...] = ()


class CommerceOutboxEntry(CommerceModel):
    command: CommerceCommand
    status: OutboxStatus = OutboxStatus.PREPARED
    attempts: int = Field(default=0, ge=0)
    receipt_ref: str | None = None
    last_reason_code: str | None = None


class CommerceRejectedProposal(CommerceModel):
    proposal_type: str
    reason_code: str
    workflow_id: str | None = None
    control_id: str | None = None


class CommercePlanningResult(CommerceModel):
    schema_version: Literal["1.0"] = COMMERCE_SCHEMA_VERSION
    status: Literal["planned", "skipped", "failed"]
    workflow_resolutions: tuple[CommerceWorkflowResolution, ...] = ()
    readiness_assessments: tuple[CommerceReadinessAssessment, ...] = ()
    workflows: tuple[CommerceWorkflowState, ...] = ()
    controls: tuple[WorkflowControlSignal, ...] = ()
    outbox: tuple[CommerceOutboxEntry, ...] = ()
    prepared_commands: tuple[CommerceCommand, ...] = ()
    rejected_proposals: tuple[CommerceRejectedProposal, ...] = ()
    capability_boundaries: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CommerceExecutionResult(CommerceModel):
    command_id: str
    capability_id: str
    status: CommerceExecutionStatus
    receipt_ref: str | None = None
    receipt_verified: bool = False
    duplicate: bool = False
    reason_code: str

    @model_validator(mode="after")
    def delivered_requires_receipt(self) -> "CommerceExecutionResult":
        if self.status == CommerceExecutionStatus.DELIVERED and (
            not self.receipt_ref or not self.receipt_verified
        ):
            raise ValueError("delivered commerce result requires a verified receipt")
        if self.receipt_verified and not self.receipt_ref:
            raise ValueError("verified receipt flag requires a receipt reference")
        return self


class CommerceGateway(Protocol):
    def describe_capabilities(self) -> CommerceCapabilitySnapshot: ...

    def execute(self, command: CommerceCommand) -> CommerceExecutionResult: ...
