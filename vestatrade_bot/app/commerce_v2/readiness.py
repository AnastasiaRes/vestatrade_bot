"""Pure commerce workflow materialization and readiness assessment."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import (
    CapabilityMode,
    CommerceCapabilitySnapshot,
    CommerceContextSnapshot,
    CommerceFieldStatus,
    CommerceLineItem,
    CommercePayloadField,
    CommerceReadinessAssessment,
    CommerceReadinessStatus,
    CommerceWorkflowContract,
    CommerceWorkflowKind,
    CommerceWorkflowResolution,
    CommerceWorkflowState,
    CommerceWorkflowStatus,
    ConsentStatus,
)
from .registry import canonical_commerce_field_name


_PURCHASE_KINDS = {
    CommerceWorkflowKind.REQUEST_QUOTE,
    CommerceWorkflowKind.REQUEST_INVOICE,
    CommerceWorkflowKind.RESERVE_PRODUCT,
    CommerceWorkflowKind.PLACE_ORDER,
}


def _status(value: object) -> CommerceFieldStatus:
    raw = getattr(value, "value", value)
    return CommerceFieldStatus(str(raw))


def _active_constraints(dialogue_state: Any, goal_ids: tuple[str, ...]) -> list[Any]:
    return [
        fact
        for fact in getattr(dialogue_state, "constraints", ())
        if fact.active
        and (not goal_ids or fact.goal_id in goal_ids or fact.goal_id is None)
    ]


def _payload_fingerprint(
    fields: tuple[CommercePayloadField, ...],
    line_items: tuple[CommerceLineItem, ...],
    product_refs: tuple[str, ...],
    solution_id: str | None,
) -> str:
    payload = {
        "fields": [item.model_dump(mode="json") for item in fields],
        "line_items": [item.model_dump(mode="json") for item in line_items],
        "product_refs": product_refs,
        "solution_id": solution_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def materialize_workflow_state(
    dialogue_state: Any,
    resolution: CommerceWorkflowResolution,
    contract: CommerceWorkflowContract,
    capability_snapshot: CommerceCapabilitySnapshot,
    context: CommerceContextSnapshot,
) -> CommerceWorkflowState:
    """Build the proposed current workflow state from typed state only."""

    existing = next(
        (
            item
            for item in getattr(dialogue_state, "commerce_workflows", ())
            if item.workflow_id == resolution.workflow_id
        ),
        None,
    )
    task_ids = tuple(
        dict.fromkeys(
            (*((existing.task_ids) if existing else ()), *resolution.task_ids)
        )
    )
    goal_ids = tuple(
        dict.fromkeys(
            task.target_goal_id
            for task in getattr(dialogue_state, "tasks", ())
            if task.task_id in task_ids and task.target_goal_id
        )
    )
    constraints = _active_constraints(dialogue_state, goal_ids)
    allowed_field_names = {item.name for item in contract.field_definitions}
    if contract.requires_product_selection:
        allowed_field_names.add("sku")
    if contract.requires_quantities:
        allowed_field_names.add("quantity")
    fields_by_name: dict[str, CommercePayloadField] = {}
    constraints_by_goal: dict[str | None, dict[str, Any]] = {}
    for fact in constraints:
        name = canonical_commerce_field_name(fact.name)
        if name not in allowed_field_names:
            continue
        constraints_by_goal.setdefault(fact.goal_id, {})[name] = fact
        if contract.workflow_kind in _PURCHASE_KINDS and name in {"sku", "quantity"}:
            continue
        status = _status(fact.status)
        fields_by_name[name] = CommercePayloadField(
            name=name,
            status=status,
            value=fact.value if status == CommerceFieldStatus.KNOWN else None,
            unit=fact.unit,
            source_fact_id=fact.fact_id,
        )
    sensitive_refs = [
        ref
        for ref in getattr(dialogue_state, "commerce_sensitive_values", ())
        if ref.active
    ]
    if context.contact_ref is not None:
        sensitive_refs.append(context.contact_ref)
    for ref in sensitive_refs:
        name = canonical_commerce_field_name(ref.field_name)
        if name not in allowed_field_names and not (
            name == "contact_ref" and contract.requires_contact
        ):
            continue
        fields_by_name[name] = CommercePayloadField(
            name=name,
            status=ref.status,
            sensitive_ref_id=(
                ref.ref_id if ref.status == CommerceFieldStatus.KNOWN else None
            ),
        )

    # Explicit selections and quantities remain separate per goal. Candidate
    # SKUs from CatalogPlannerV2 are deliberately excluded: a candidate is not
    # an order line selected by the customer.
    line_items: list[CommerceLineItem] = []
    if contract.workflow_kind in _PURCHASE_KINDS:
        line_goal_ids: tuple[str | None, ...] = goal_ids or (None,)
        for goal_id in line_goal_ids:
            facts = constraints_by_goal.get(goal_id, {})
            sku_fact = facts.get("sku")
            quantity_fact = facts.get("quantity")
            if sku_fact is None and quantity_fact is None:
                continue
            sku_status = _status(sku_fact.status) if sku_fact is not None else None
            quantity_status = (
                _status(quantity_fact.status) if quantity_fact is not None else None
            )
            line_items.append(
                CommerceLineItem(
                    line_id=hashlib.sha256(
                        f"{resolution.workflow_id}:{goal_id or 'unscoped'}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:24],
                    goal_id=goal_id,
                    product_ref=(
                        str(sku_fact.value)
                        if sku_fact is not None
                        and sku_status == CommerceFieldStatus.KNOWN
                        else None
                    ),
                    product_status=sku_status,
                    quantity=(
                        quantity_fact.value
                        if quantity_fact is not None
                        and quantity_status == CommerceFieldStatus.KNOWN
                        else None
                    ),
                    quantity_unit=(
                        quantity_fact.unit if quantity_fact is not None else None
                    ),
                    quantity_status=quantity_status,
                    source_fact_ids=tuple(
                        fact.fact_id
                        for fact in (sku_fact, quantity_fact)
                        if fact is not None
                    ),
                )
            )
    product_refs = tuple(
        dict.fromkeys(
            item.product_ref
            for item in line_items
            if item.product_status == CommerceFieldStatus.KNOWN
            and item.product_ref is not None
        )
    )
    if contract.workflow_kind not in _PURCHASE_KINDS and not product_refs:
        # For service workflows an explicit ProductGoal is a safe typed
        # reference to the complained-about/returned object, not a chosen SKU.
        product_refs = goal_ids

    catalog_planning = getattr(dialogue_state, "catalog_planning", None)
    solution_id = (
        catalog_planning.solution_plan.solution_id
        if catalog_planning is not None and catalog_planning.solution_plan is not None
        else None
    )
    fields = tuple(sorted(fields_by_name.values(), key=lambda item: item.name))
    typed_line_items = tuple(line_items)
    fingerprint = _payload_fingerprint(
        fields,
        typed_line_items,
        product_refs,
        solution_id,
    )
    revision = existing.payload_revision if existing is not None else 1
    consent = existing.consent if existing is not None else None
    status = (
        existing.status if existing is not None else CommerceWorkflowStatus.COLLECTING
    )
    preview_revision = existing.preview_revision if existing is not None else None
    reason_codes = list(existing.reason_codes if existing is not None else ())
    if existing is not None and existing.payload_fingerprint != fingerprint:
        revision += 1
        preview_revision = None
        if consent is not None and consent.status == ConsentStatus.GRANTED:
            consent = consent.model_copy(
                update={
                    "status": ConsentStatus.STALE,
                    "invalidation_reason": "commerce_payload_changed",
                }
            )
        status = CommerceWorkflowStatus.COLLECTING
        reason_codes.append("commerce_payload_revised")

    capability = capability_snapshot.get(contract.capability_id)
    capability_mode = (
        capability.mode if capability is not None else CapabilityMode.UNAVAILABLE
    )
    return CommerceWorkflowState(
        workflow_id=resolution.workflow_id,
        contract_id=contract.contract_id,
        workflow_kind=contract.workflow_kind,
        task_ids=task_ids,
        goal_ids=goal_ids,
        payload_revision=revision,
        payload_fingerprint=fingerprint,
        status=status,
        fields=fields,
        line_items=typed_line_items,
        product_refs=product_refs,
        solution_id=solution_id,
        preview_revision=preview_revision,
        consent=consent
        or CommerceWorkflowState.model_fields["consent"].default_factory(),
        opt_out=existing.opt_out if existing is not None else False,
        capability_id=contract.capability_id,
        capability_mode=capability_mode,
        command_id=existing.command_id if existing is not None else None,
        execution_status=(
            existing.execution_status
            if existing is not None
            else CommerceWorkflowState.model_fields["execution_status"].default
        ),
        external_receipt_ref=(
            existing.external_receipt_ref if existing is not None else None
        ),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        created_turn=(
            existing.created_turn
            if existing is not None
            else dialogue_state.turn_number
        ),
        updated_turn=dialogue_state.turn_number,
        applied_control_ids=(
            existing.applied_control_ids if existing is not None else ()
        ),
    )


def assess_commerce_readiness(
    dialogue_state: Any,
    workflow: CommerceWorkflowState,
    contract: CommerceWorkflowContract,
    capability_snapshot: CommerceCapabilitySnapshot,
) -> CommerceReadinessAssessment:
    """Assess one workflow without choosing text, SKU or external action."""

    capability = capability_snapshot.get(contract.capability_id)
    mode = capability.mode if capability is not None else CapabilityMode.UNAVAILABLE
    fields = {item.name: item for item in workflow.fields}
    line_statuses = {
        "product_selection": tuple(
            item.product_status
            for item in workflow.line_items
            if item.product_status is not None
        ),
        "quantity": tuple(
            item.quantity_status
            for item in workflow.line_items
            if item.quantity_status is not None
        ),
    }
    known_names = {
        name
        for name, fact in fields.items()
        if fact.status == CommerceFieldStatus.KNOWN
    }
    unavailable_names: dict[CommerceFieldStatus, set[str]] = {
        CommerceFieldStatus.UNKNOWN: {
            name
            for name, fact in fields.items()
            if fact.status == CommerceFieldStatus.UNKNOWN
        },
        CommerceFieldStatus.REFUSED: {
            name
            for name, fact in fields.items()
            if fact.status == CommerceFieldStatus.REFUSED
        },
        CommerceFieldStatus.DEFERRED: {
            name
            for name, fact in fields.items()
            if fact.status == CommerceFieldStatus.DEFERRED
        },
    }
    for name, statuses in line_statuses.items():
        if statuses and all(status == CommerceFieldStatus.KNOWN for status in statuses):
            known_names.add(name)
        for status in unavailable_names:
            if status in statuses:
                unavailable_names[status].add(name)
    known = tuple(sorted(known_names))
    unknown = tuple(sorted(unavailable_names[CommerceFieldStatus.UNKNOWN]))
    refused = tuple(sorted(unavailable_names[CommerceFieldStatus.REFUSED]))
    deferred = tuple(sorted(unavailable_names[CommerceFieldStatus.DEFERRED]))
    terminal_unavailable = set((*unknown, *refused, *deferred))

    required = list(contract.required_fields)
    if contract.requires_contact:
        required.append("contact_ref")
    missing = tuple(
        name
        for name in dict.fromkeys(required)
        if name not in fields and name not in terminal_unavailable
    )
    sensitive_refs = tuple(
        field.sensitive_ref_id
        for field in fields.values()
        if field.sensitive_ref_id is not None
    )

    line_goal_ids = {item.goal_id for item in workflow.line_items if item.goal_id}
    selected_goal_ids = {
        item.goal_id
        for item in workflow.line_items
        if item.goal_id
        and item.product_status == CommerceFieldStatus.KNOWN
        and item.product_ref is not None
    }
    product_missing = bool(
        contract.requires_product_selection
        and (
            not workflow.product_refs
            or (line_goal_ids and selected_goal_ids != set(workflow.goal_ids))
        )
    )
    quantity_missing = bool(
        contract.requires_quantities
        and (
            not workflow.line_items
            or any(
                item.product_status == CommerceFieldStatus.KNOWN
                and item.quantity_status is None
                for item in workflow.line_items
            )
        )
    )
    reason_codes: list[str] = []
    recommended: str | None = None

    if workflow.opt_out or workflow.status == CommerceWorkflowStatus.CANCELLED:
        status = CommerceReadinessStatus.CANCELLED
        reason_codes.append("commerce_workflow_opted_out_or_cancelled")
    elif workflow.status in {
        CommerceWorkflowStatus.DELIVERED,
        CommerceWorkflowStatus.COMPLETED,
        CommerceWorkflowStatus.LOCAL_DRAFT_SAVED,
    }:
        status = CommerceReadinessStatus.COMPLETED
        reason_codes.append("commerce_workflow_terminal")
    elif mode == CapabilityMode.UNAVAILABLE:
        status = CommerceReadinessStatus.CAPABILITY_UNAVAILABLE
        reason_codes.append(
            capability.reason_code
            if capability is not None
            else contract.unavailable_reason_code
        )
    elif terminal_unavailable.intersection(required):
        status = CommerceReadinessStatus.BLOCKED
        reason_codes.append("required_commerce_fact_explicitly_unavailable")
    elif product_missing:
        status = CommerceReadinessStatus.NEEDS_PRODUCT_SELECTION
        recommended = "product_selection"
        reason_codes.append("confirmed_product_selection_required")
    elif quantity_missing:
        status = CommerceReadinessStatus.NEEDS_CUSTOMER_FACT
        recommended = None if "quantity" in terminal_unavailable else "quantity"
        reason_codes.append("explicit_quantity_required")
    elif missing:
        status = CommerceReadinessStatus.NEEDS_CUSTOMER_FACT
        recommended = missing[0]
        reason_codes.append("commerce_required_field_missing")
    elif not contract.requires_preview and not contract.requires_consent:
        status = CommerceReadinessStatus.READY_TO_PREPARE
        reason_codes.append("verified_informational_capability_ready")
    elif workflow.preview_revision != workflow.payload_revision:
        status = CommerceReadinessStatus.NEEDS_PREVIEW
        reason_codes.append("commerce_preview_required")
    elif workflow.consent.status != ConsentStatus.GRANTED:
        status = CommerceReadinessStatus.NEEDS_CONSENT
        reason_codes.append("scoped_consent_required")
    elif mode == CapabilityMode.LOCAL_DRAFT_ONLY:
        status = CommerceReadinessStatus.LOCAL_DRAFT_ONLY
        reason_codes.append("only_local_draft_can_be_prepared")
    else:
        status = CommerceReadinessStatus.READY_TO_PREPARE
        reason_codes.append("commerce_workflow_ready_to_prepare")

    blocking = tuple(
        dict.fromkeys(
            (*missing, *(name for name in required if name in terminal_unavailable))
        )
    )
    return CommerceReadinessAssessment(
        workflow_id=workflow.workflow_id,
        task_ids=workflow.task_ids,
        contract_id=contract.contract_id,
        workflow_kind=contract.workflow_kind,
        status=status,
        confirmed_fields=known,
        missing_fields=missing,
        unknown_fields=unknown,
        refused_fields=refused,
        deferred_fields=deferred,
        sensitive_ref_ids=sensitive_refs,
        product_refs=workflow.product_refs,
        consent_status=workflow.consent.status,
        capability_mode=mode,
        blocking_facts=blocking,
        reason_codes=tuple(reason_codes),
        recommended_next_field=recommended,
    )
