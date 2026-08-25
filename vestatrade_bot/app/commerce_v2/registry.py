"""Declarative Stage 4 workflow and capability registries."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .contracts import (
    CapabilityMode,
    CommerceCapability,
    CommerceCapabilitySnapshot,
    CommerceWorkflowContract,
    CommerceWorkflowKind,
    CommerceWorkflowResolution,
    CommerceWorkflowStatus,
    SensitiveValueKind,
    WorkflowFieldDefinition,
    WorkflowResolutionStatus,
)


_FIELD_ALIASES = {
    "sku": "sku",
    "article": "sku",
    "quantity": "quantity",
    "count": "quantity",
    "amount": "quantity",
    "order_id": "order_reference",
    "order_number": "order_reference",
    "order_reference": "order_reference",
    "purchase_reference": "order_reference",
    "phone": "contact_ref",
    "email": "contact_ref",
    "contact": "contact_ref",
    "customer_contact": "contact_ref",
    "contact_ref": "contact_ref",
    "address": "delivery_address",
    "delivery_address": "delivery_address",
    "city": "destination_region",
    "region": "destination_region",
    "destination": "destination_region",
    "destination_region": "destination_region",
    "reserve_until": "reserve_until",
    "reservation_deadline": "reserve_until",
    "requested_change": "requested_change",
    "change_request": "requested_change",
    "return_reason": "return_reason",
    "issue": "issue_description",
    "issue_description": "issue_description",
    "complaint_subject": "complaint_subject",
    "company_requisites": "company_requisites",
    "requisites": "company_requisites",
    "inn": "tax_identifier",
    "tax_identifier": "tax_identifier",
}


_SENSITIVE_FIELDS = {
    "contact_ref": SensitiveValueKind.CONTACT,
    "order_reference": SensitiveValueKind.ORDER_REFERENCE,
    "delivery_address": SensitiveValueKind.DELIVERY_ADDRESS,
    "company_requisites": SensitiveValueKind.COMPANY_REQUISITES,
    "tax_identifier": SensitiveValueKind.TAX_IDENTIFIER,
}


def canonical_commerce_field_name(value: str) -> str:
    """Canonicalize semantic field identifiers, never customer message text."""

    normalized = "_".join(str(value or "").strip().casefold().split())
    return _FIELD_ALIASES.get(normalized, normalized)


def sensitive_value_kind(field_name: str) -> SensitiveValueKind | None:
    return _SENSITIVE_FIELDS.get(canonical_commerce_field_name(field_name))


def _field(
    name: str,
    *,
    required: bool = False,
    decision: bool = False,
    sensitive: SensitiveValueKind | None = None,
) -> WorkflowFieldDefinition:
    return WorkflowFieldDefinition(
        name=name,
        required=required,
        decision_changing=decision,
        sensitive=sensitive is not None,
        sensitive_kind=sensitive,
    )


_COMMON_TRANSITIONS = (
    "collecting->ready_for_preview",
    "ready_for_preview->awaiting_consent",
    "awaiting_consent->consented",
    "consented->ready_to_execute",
    "ready_to_execute->queued",
    "queued->local_draft_saved",
    "queued->delivered",
    "queued->delivery_failed",
    "queued->delivery_unknown",
    "*->suspended",
    "*->cancelled",
)


def _contract(
    kind: CommerceWorkflowKind,
    acts: tuple[str, ...],
    capability_id: str,
    *,
    fields: tuple[WorkflowFieldDefinition, ...] = (),
    product: bool = False,
    quantities: bool = False,
    contact: bool = False,
    preview: bool = True,
    consent: bool = True,
    local_draft: bool = True,
) -> CommerceWorkflowContract:
    required = tuple(item.name for item in fields if item.required)
    decision = tuple(item.name for item in fields if item.decision_changing)
    sensitive = tuple(item.name for item in fields if item.sensitive)
    return CommerceWorkflowContract(
        contract_id=f"commerce.{kind.value}.v1",
        workflow_kind=kind,
        supported_task_acts=acts,
        field_definitions=fields,
        required_fields=required,
        decision_changing_fields=decision,
        sensitive_fields=sensitive,
        requires_product_selection=product,
        requires_quantities=quantities,
        requires_contact=contact,
        requires_preview=preview,
        requires_consent=consent,
        capability_id=capability_id,
        local_draft_allowed=local_draft,
        allowed_transitions=_COMMON_TRANSITIONS,
        consent_invalidation_fields=(*required, "product_refs"),
        unavailable_reason_code=f"{kind.value}_capability_unavailable",
    )


DEFAULT_WORKFLOW_CONTRACTS: tuple[CommerceWorkflowContract, ...] = (
    _contract(
        CommerceWorkflowKind.REQUEST_QUOTE,
        ("request_quote",),
        "manager_quote_draft",
        fields=(
            _field("quantity", decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        product=True,
        quantities=True,
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.REQUEST_INVOICE,
        ("request_invoice",),
        "manager_invoice_draft",
        fields=(
            _field("quantity", decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
            _field(
                "company_requisites",
                decision=True,
                sensitive=SensitiveValueKind.COMPANY_REQUISITES,
            ),
        ),
        product=True,
        quantities=True,
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.RESERVE_PRODUCT,
        ("reserve_product",),
        "manager_reservation_draft",
        fields=(
            _field("quantity", decision=True),
            _field("reserve_until", decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        product=True,
        quantities=True,
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.PLACE_ORDER,
        ("place_order",),
        "order_write",
        fields=(
            _field("quantity", decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        product=True,
        quantities=True,
        contact=True,
        local_draft=False,
    ),
    _contract(
        CommerceWorkflowKind.ORDER_STATUS,
        ("order_status",),
        "order_status_read",
        fields=(
            _field(
                "order_reference",
                required=True,
                decision=True,
                sensitive=SensitiveValueKind.ORDER_REFERENCE,
            ),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.MODIFY_ORDER,
        ("modify_order",),
        "order_modify_write",
        fields=(
            _field(
                "order_reference",
                required=True,
                decision=True,
                sensitive=SensitiveValueKind.ORDER_REFERENCE,
            ),
            _field("requested_change", required=True, decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.CANCEL_ORDER,
        ("cancel_order",),
        "order_cancel_write",
        fields=(
            _field(
                "order_reference",
                required=True,
                decision=True,
                sensitive=SensitiveValueKind.ORDER_REFERENCE,
            ),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.CHECK_DELIVERY,
        ("check_delivery",),
        "delivery_policy_read",
        fields=(_field("destination_region", decision=True),),
        product=False,
        quantities=False,
        contact=False,
        preview=False,
        consent=False,
        local_draft=False,
    ),
    _contract(
        CommerceWorkflowKind.RETURN_PRODUCT,
        ("return_product",),
        "manager_return_draft",
        fields=(
            _field(
                "order_reference",
                required=True,
                decision=True,
                sensitive=SensitiveValueKind.ORDER_REFERENCE,
            ),
            _field("return_reason", required=True, decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        product=True,
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.WARRANTY,
        ("warranty",),
        "manager_warranty_draft",
        fields=(
            _field(
                "order_reference",
                decision=True,
                sensitive=SensitiveValueKind.ORDER_REFERENCE,
            ),
            _field("issue_description", required=True, decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        product=True,
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.COMPLAINT,
        ("complaint",),
        "manager_complaint_draft",
        fields=(
            _field("complaint_subject", required=True, decision=True),
            _field("contact_ref", sensitive=SensitiveValueKind.CONTACT),
        ),
        contact=True,
    ),
    _contract(
        CommerceWorkflowKind.HANDOFF,
        ("handoff",),
        "manager_handoff_local_draft",
        fields=(_field("contact_ref", sensitive=SensitiveValueKind.CONTACT),),
        contact=True,
    ),
)


class CommerceWorkflowRegistry:
    def __init__(
        self,
        contracts: Iterable[CommerceWorkflowContract] = DEFAULT_WORKFLOW_CONTRACTS,
    ) -> None:
        self._contracts = tuple(contracts)
        self._by_id = {item.contract_id: item for item in self._contracts}
        self._by_act = {
            act: item for item in self._contracts for act in item.supported_task_acts
        }

    @property
    def contracts(self) -> tuple[CommerceWorkflowContract, ...]:
        return self._contracts

    def get(self, contract_id: str | None) -> CommerceWorkflowContract | None:
        return self._by_id.get(contract_id or "")

    def for_act(self, act: object) -> CommerceWorkflowContract | None:
        raw = getattr(act, "value", act)
        return self._by_act.get(str(raw))


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def resolve_commerce_workflows(
    dialogue_state: Any,
    registry: CommerceWorkflowRegistry,
) -> tuple[CommerceWorkflowResolution, ...]:
    """Resolve typed tasks to workflows without looking at customer text."""

    current_tasks = [
        task
        for task in dialogue_state.tasks
        if task.source_turn == dialogue_state.turn_number
        and registry.for_act(task.act) is not None
        and getattr(task.status, "value", task.status)
        not in {"satisfied", "cancelled", "suspended"}
    ]
    groups: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for task in current_tasks:
        contract = registry.for_act(task.act)
        if contract is not None:
            groups[(contract.contract_id, task.source_turn)].append(task)

    controls_now = [
        control
        for control in getattr(dialogue_state, "commerce_controls", ())
        if control.source_turn == dialogue_state.turn_number
        and control.applied_workflow_id is None
    ]
    resume_requested = any(
        control.kind.value == "resume_after_opt_out" for control in controls_now
    )
    if not groups and controls_now:
        for workflow in getattr(dialogue_state, "commerce_workflows", ()):
            if workflow.status not in {
                CommerceWorkflowStatus.DELIVERED,
                CommerceWorkflowStatus.COMPLETED,
                CommerceWorkflowStatus.CANCELLED,
            } or (resume_requested and workflow.opt_out):
                contract = registry.get(workflow.contract_id)
                if contract is None:
                    continue
                pseudo_tasks = [
                    task
                    for task in dialogue_state.tasks
                    if task.task_id in workflow.task_ids
                ]
                groups[(contract.contract_id, dialogue_state.turn_number)].extend(
                    pseudo_tasks
                )

    resolutions: list[CommerceWorkflowResolution] = []
    for (contract_id, _source_turn), tasks in sorted(groups.items()):
        contract = registry.get(contract_id)
        task_ids = tuple(sorted({task.task_id for task in tasks}))
        goal_ids = tuple(
            sorted({task.target_goal_id for task in tasks if task.target_goal_id})
        )
        if contract is None:
            resolutions.append(
                CommerceWorkflowResolution(
                    status=WorkflowResolutionStatus.UNSUPPORTED,
                    workflow_id=_stable_id("commerce", contract_id, *task_ids),
                    task_ids=task_ids,
                    reason_codes=("commerce_contract_missing",),
                )
            )
            continue
        open_same_kind = tuple(
            workflow
            for workflow in reversed(getattr(dialogue_state, "commerce_workflows", ()))
            if workflow.workflow_kind == contract.workflow_kind
            and workflow.status
            not in {
                CommerceWorkflowStatus.DELIVERED,
                CommerceWorkflowStatus.COMPLETED,
                CommerceWorkflowStatus.CANCELLED,
            }
        )
        existing = next(
            (
                workflow
                for workflow in open_same_kind
                if (
                    set(workflow.task_ids).intersection(task_ids)
                    or set(workflow.goal_ids).intersection(goal_ids)
                    or (
                        not task_ids
                        and workflow.status == CommerceWorkflowStatus.AWAITING_CONSENT
                    )
                )
            ),
            None,
        )
        # A semantic model may redundantly repeat the domain act while also
        # returning an explicit workflow control.  If exactly one open workflow
        # of that kind already exists, keep its stable identity so the reducer
        # can bind the control to the prepared scope.  Multiple candidates stay
        # ambiguous and are never guessed.
        if existing is None and controls_now and len(open_same_kind) == 1:
            existing = open_same_kind[0]
        workflow_id = (
            existing.workflow_id
            if existing is not None
            else _stable_id("commerce", contract.contract_id, *task_ids)
        )
        resolutions.append(
            CommerceWorkflowResolution(
                status=WorkflowResolutionStatus.RESOLVED,
                workflow_id=workflow_id,
                task_ids=task_ids or (existing.task_ids if existing else ()),
                contract_id=contract.contract_id,
                workflow_kind=contract.workflow_kind,
                capability_id=contract.capability_id,
                reason_codes=("commerce_contract_resolved",),
            )
        )
    return tuple(resolutions)


def build_capability_snapshot(
    business_facts: Any | None = None,
) -> CommerceCapabilitySnapshot:
    """Describe verified local capabilities without copying their values."""

    present: list[str] = []
    drafted: tuple[str, ...] = ()
    if business_facts is not None:
        drafted = tuple(getattr(business_facts, "drafted_sections", ()) or ())
        for key in (
            "delivery",
            "payment",
            "returns",
            "warranty",
            "business_hours",
            "pickup_points",
            "branches",
            "response_time",
            "lead_times",
        ):
            if getattr(business_facts, key, None) and key not in drafted:
                present.append(key)

    local_drafts = {
        CommerceWorkflowKind.REQUEST_QUOTE: "manager_quote_draft",
        CommerceWorkflowKind.REQUEST_INVOICE: "manager_invoice_draft",
        CommerceWorkflowKind.RESERVE_PRODUCT: "manager_reservation_draft",
        CommerceWorkflowKind.ORDER_STATUS: "order_status_read",
        CommerceWorkflowKind.MODIFY_ORDER: "order_modify_write",
        CommerceWorkflowKind.CANCEL_ORDER: "order_cancel_write",
        CommerceWorkflowKind.RETURN_PRODUCT: "manager_return_draft",
        CommerceWorkflowKind.WARRANTY: "manager_warranty_draft",
        CommerceWorkflowKind.COMPLAINT: "manager_complaint_draft",
        CommerceWorkflowKind.HANDOFF: "manager_handoff_local_draft",
    }
    capabilities: list[CommerceCapability] = []
    for contract in DEFAULT_WORKFLOW_CONTRACTS:
        kind = contract.workflow_kind
        if kind == CommerceWorkflowKind.CHECK_DELIVERY:
            mode = (
                CapabilityMode.VERIFIED_STATIC
                if "delivery" in present
                else CapabilityMode.UNAVAILABLE
            )
            reason = (
                "verified_business_delivery_snapshot"
                if mode == CapabilityMode.VERIFIED_STATIC
                else "delivery_policy_not_configured"
            )
        elif kind == CommerceWorkflowKind.PLACE_ORDER:
            mode = CapabilityMode.UNAVAILABLE
            reason = "no_transactional_order_system"
        elif kind in local_drafts:
            mode = CapabilityMode.LOCAL_DRAFT_ONLY
            reason = "legacy_handoff_is_local_draft_only"
        else:
            mode = CapabilityMode.UNAVAILABLE
            reason = "commerce_capability_unavailable"
        capabilities.append(
            CommerceCapability(
                capability_id=contract.capability_id,
                operation=kind,
                mode=mode,
                source=(
                    "data/business_config.json"
                    if mode == CapabilityMode.VERIFIED_STATIC
                    else (
                        "app/agents/handoff.py:HandoffAgent.record"
                        if mode == CapabilityMode.LOCAL_DRAFT_ONLY
                        else "capability_audit"
                    )
                ),
                required_fields=contract.required_fields,
                contains_pii=contract.requires_contact,
                requires_consent=contract.requires_consent,
                has_verifiable_receipt=False,
                result_verifiable=mode == CapabilityMode.VERIFIED_STATIC,
                verified_fact_keys=tuple(present),
                drafted_fact_keys=drafted,
                reason_code=reason,
            )
        )
    return CommerceCapabilitySnapshot(capabilities=tuple(capabilities))
