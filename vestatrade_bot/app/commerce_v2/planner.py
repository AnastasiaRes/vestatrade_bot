"""Pure deterministic Stage 4 commerce workflow and outbox planner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .contracts import (
    CapabilityMode,
    CommerceCapabilitySnapshot,
    CommerceCommand,
    CommerceCommandStatus,
    CommerceExecutionStatus,
    CommerceOutboxEntry,
    CommercePlanningResult,
    CommerceReadinessAssessment,
    CommerceReadinessStatus,
    CommerceRejectedProposal,
    CommerceWorkflowResolution,
    CommerceWorkflowState,
    CommerceWorkflowStatus,
    ConsentState,
    ConsentStatus,
    OutboxStatus,
    WorkflowControlKind,
    WorkflowControlSignal,
)
from .registry import CommerceWorkflowRegistry


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _scope_fingerprint(workflow: CommerceWorkflowState) -> str:
    material = (
        f"{workflow.workflow_id}:{workflow.payload_revision}:"
        f"{workflow.capability_id}:{workflow.payload_fingerprint}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _link_current_solution(
    workflow: CommerceWorkflowState,
    dialogue_state: Any,
    catalog_planning: Any,
) -> CommerceWorkflowState:
    """Link a typed BOM by id without promoting its candidates to order lines."""

    solution = getattr(catalog_planning, "solution_plan", None)
    if solution is None:
        return workflow
    related_task_ids = set(workflow.task_ids)
    for task in getattr(dialogue_state, "tasks", ()):
        if task.task_id in related_task_ids:
            related_task_ids.update(task.related_task_ids)
    if not related_task_ids.intersection(solution.task_ids):
        return workflow
    if workflow.solution_id == solution.solution_id:
        return workflow

    previous = next(
        (
            item
            for item in getattr(dialogue_state, "commerce_workflows", ())
            if item.workflow_id == workflow.workflow_id
        ),
        None,
    )
    revision = workflow.payload_revision + (1 if previous is not None else 0)
    payload = {
        "fields": [item.model_dump(mode="json") for item in workflow.fields],
        "line_items": [item.model_dump(mode="json") for item in workflow.line_items],
        "product_refs": workflow.product_refs,
        "solution_id": solution.solution_id,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    consent = workflow.consent
    if consent.status == ConsentStatus.GRANTED:
        consent = consent.model_copy(
            update={
                "status": ConsentStatus.STALE,
                "invalidation_reason": "commerce_solution_plan_changed",
            }
        )
    return workflow.model_copy(
        update={
            "solution_id": solution.solution_id,
            "payload_revision": revision,
            "payload_fingerprint": fingerprint,
            "preview_revision": None,
            "consent": consent,
            "status": CommerceWorkflowStatus.COLLECTING,
            "reason_codes": tuple(
                dict.fromkeys((*workflow.reason_codes, "typed_solution_plan_linked"))
            ),
        }
    )


def apply_workflow_controls(
    workflows: Iterable[CommerceWorkflowState],
    controls: Iterable[WorkflowControlSignal],
    *,
    turn_number: int,
) -> tuple[
    tuple[CommerceWorkflowState, ...],
    tuple[WorkflowControlSignal, ...],
    tuple[CommerceRejectedProposal, ...],
]:
    """Bind semantic control acts to one unambiguous pending workflow."""

    updated = list(workflows)
    resulting_controls: list[WorkflowControlSignal] = []
    rejected: list[CommerceRejectedProposal] = []

    for control in controls:
        if any(control.control_id in item.applied_control_ids for item in updated):
            resulting_controls.append(
                control.model_copy(update={"rejected_reason": "duplicate_control"})
            )
            rejected.append(
                CommerceRejectedProposal(
                    proposal_type="workflow_control",
                    reason_code="duplicate_workflow_control",
                    control_id=control.control_id,
                )
            )
            continue
        if control.kind == WorkflowControlKind.CONFIRM:
            candidates = [
                item
                for item in updated
                if item.status == CommerceWorkflowStatus.AWAITING_CONSENT
                and item.consent.status == ConsentStatus.AWAITING
            ]
        elif control.kind == WorkflowControlKind.DECLINE:
            candidates = [
                item
                for item in updated
                if item.status == CommerceWorkflowStatus.AWAITING_CONSENT
            ]
        elif control.kind == WorkflowControlKind.WITHDRAW_CONSENT:
            candidates = [
                item
                for item in updated
                if item.consent.status == ConsentStatus.GRANTED
                and item.status
                not in {
                    CommerceWorkflowStatus.DELIVERED,
                    CommerceWorkflowStatus.COMPLETED,
                }
            ]
        elif control.kind == WorkflowControlKind.OPT_OUT:
            candidates = [
                item
                for item in updated
                if item.workflow_kind.value == "handoff"
                and item.status
                not in {
                    CommerceWorkflowStatus.DELIVERED,
                    CommerceWorkflowStatus.COMPLETED,
                }
            ]
        else:
            candidates = [
                item
                for item in updated
                if item.workflow_kind.value == "handoff" and item.opt_out
            ]

        if len(candidates) != 1:
            reason = (
                "workflow_control_has_no_target"
                if not candidates
                else "workflow_control_target_ambiguous"
            )
            resulting_controls.append(
                control.model_copy(update={"rejected_reason": reason})
            )
            rejected.append(
                CommerceRejectedProposal(
                    proposal_type="workflow_control",
                    reason_code=reason,
                    control_id=control.control_id,
                )
            )
            continue

        target = candidates[0]
        scope = _scope_fingerprint(target)
        if control.kind == WorkflowControlKind.CONFIRM:
            consent = ConsentState(
                status=ConsentStatus.GRANTED,
                workflow_id=target.workflow_id,
                operation=target.workflow_kind,
                payload_revision=target.payload_revision,
                capability_id=target.capability_id,
                source_turn=turn_number,
                source=control.source,
                scope_fingerprint=scope,
            )
            replacement = target.model_copy(
                update={
                    "consent": consent,
                    "status": CommerceWorkflowStatus.CONSENTED,
                    "updated_turn": turn_number,
                    "applied_control_ids": (
                        *target.applied_control_ids,
                        control.control_id,
                    ),
                }
            )
        elif control.kind == WorkflowControlKind.DECLINE:
            replacement = target.model_copy(
                update={
                    "consent": ConsentState(
                        status=ConsentStatus.DENIED,
                        workflow_id=target.workflow_id,
                        operation=target.workflow_kind,
                        payload_revision=target.payload_revision,
                        capability_id=target.capability_id,
                        source_turn=turn_number,
                        source=control.source,
                        scope_fingerprint=scope,
                    ),
                    "status": CommerceWorkflowStatus.CANCELLED,
                    "execution_status": CommerceExecutionStatus.CANCELLED,
                    "updated_turn": turn_number,
                    "applied_control_ids": (
                        *target.applied_control_ids,
                        control.control_id,
                    ),
                }
            )
        elif control.kind == WorkflowControlKind.WITHDRAW_CONSENT:
            replacement = target.model_copy(
                update={
                    "consent": target.consent.model_copy(
                        update={
                            "status": ConsentStatus.WITHDRAWN,
                            "source_turn": turn_number,
                            "source": control.source,
                            "invalidation_reason": "customer_withdrew_consent",
                        }
                    ),
                    "status": CommerceWorkflowStatus.CANCELLED,
                    "execution_status": CommerceExecutionStatus.CANCELLED,
                    "updated_turn": turn_number,
                    "applied_control_ids": (
                        *target.applied_control_ids,
                        control.control_id,
                    ),
                }
            )
        elif control.kind == WorkflowControlKind.OPT_OUT:
            replacement = target.model_copy(
                update={
                    "opt_out": True,
                    "consent": target.consent.model_copy(
                        update={
                            "status": ConsentStatus.WITHDRAWN,
                            "source_turn": turn_number,
                            "source": control.source,
                            "invalidation_reason": "handoff_opt_out",
                        }
                    ),
                    "status": CommerceWorkflowStatus.CANCELLED,
                    "execution_status": CommerceExecutionStatus.CANCELLED,
                    "updated_turn": turn_number,
                    "applied_control_ids": (
                        *target.applied_control_ids,
                        control.control_id,
                    ),
                }
            )
        else:
            replacement = target.model_copy(
                update={
                    "opt_out": False,
                    "consent": ConsentState(),
                    "status": CommerceWorkflowStatus.COLLECTING,
                    "updated_turn": turn_number,
                    "applied_control_ids": (
                        *target.applied_control_ids,
                        control.control_id,
                    ),
                }
            )
        index = next(
            index
            for index, item in enumerate(updated)
            if item.workflow_id == target.workflow_id
        )
        updated[index] = replacement
        resulting_controls.append(
            control.model_copy(update={"applied_workflow_id": target.workflow_id})
        )

    return tuple(updated), tuple(resulting_controls), tuple(rejected)


def plan_commerce_workflow(
    dialogue_state: Any,
    next_action_plan: Any,
    workflow_resolutions: Iterable[CommerceWorkflowResolution],
    proposed_workflows: Iterable[CommerceWorkflowState],
    readiness_assessments: Iterable[CommerceReadinessAssessment],
    catalog_planning: Any,
    capability_snapshot: CommerceCapabilitySnapshot,
    workflow_registry: CommerceWorkflowRegistry,
    *,
    controls: Iterable[WorkflowControlSignal] = (),
    control_rejections: Iterable[CommerceRejectedProposal] = (),
    outbox_enabled: bool = True,
) -> CommercePlanningResult:
    """Plan workflow transitions and outbox entries without executing I/O."""

    del next_action_plan
    resolutions = tuple(workflow_resolutions)
    assessments = {item.workflow_id: item for item in readiness_assessments}
    planned = [
        _link_current_solution(item, dialogue_state, catalog_planning)
        for item in proposed_workflows
    ]
    previous_workflows = list(getattr(dialogue_state, "commerce_workflows", ()))
    previous_outbox = list(getattr(dialogue_state, "commerce_outbox", ()))
    prepared_commands: list[CommerceCommand] = []
    rejected = list(control_rejections)
    boundaries: list[str] = []
    reason_codes: list[str] = []

    for index, workflow in enumerate(tuple(planned)):
        assessment = assessments.get(workflow.workflow_id)
        if assessment is None:
            continue
        update: dict[str, Any] = {
            "missing_fields": assessment.missing_fields,
            "unknown_fields": assessment.unknown_fields,
            "refused_fields": assessment.refused_fields,
            "deferred_fields": assessment.deferred_fields,
            "reason_codes": tuple(
                dict.fromkeys((*workflow.reason_codes, *assessment.reason_codes))
            ),
            "updated_turn": dialogue_state.turn_number,
        }
        if assessment.status == CommerceReadinessStatus.NEEDS_PREVIEW:
            scope = _scope_fingerprint(workflow)
            update.update(
                {
                    "preview_revision": workflow.payload_revision,
                    "consent": ConsentState(
                        status=ConsentStatus.AWAITING,
                        workflow_id=workflow.workflow_id,
                        operation=workflow.workflow_kind,
                        payload_revision=workflow.payload_revision,
                        capability_id=workflow.capability_id,
                        source_turn=dialogue_state.turn_number,
                        source="commerce_planner_v2",
                        scope_fingerprint=scope,
                    ),
                    "status": CommerceWorkflowStatus.AWAITING_CONSENT,
                }
            )
        elif assessment.status == CommerceReadinessStatus.NEEDS_CONSENT:
            update["status"] = CommerceWorkflowStatus.AWAITING_CONSENT
            if workflow.consent.status != ConsentStatus.AWAITING:
                update["consent"] = ConsentState(
                    status=ConsentStatus.AWAITING,
                    workflow_id=workflow.workflow_id,
                    operation=workflow.workflow_kind,
                    payload_revision=workflow.payload_revision,
                    capability_id=workflow.capability_id,
                    source_turn=dialogue_state.turn_number,
                    source="commerce_planner_v2",
                    scope_fingerprint=_scope_fingerprint(workflow),
                )
        elif assessment.status in {
            CommerceReadinessStatus.NEEDS_CUSTOMER_FACT,
            CommerceReadinessStatus.NEEDS_PRODUCT_SELECTION,
            CommerceReadinessStatus.NEEDS_BUSINESS_FACT,
        }:
            update["status"] = CommerceWorkflowStatus.COLLECTING
        elif assessment.status == CommerceReadinessStatus.CAPABILITY_UNAVAILABLE:
            update["status"] = CommerceWorkflowStatus.BLOCKED
            boundaries.append(f"{workflow.workflow_id}:capability_unavailable")
        elif assessment.status == CommerceReadinessStatus.BLOCKED:
            update["status"] = CommerceWorkflowStatus.BLOCKED
        elif assessment.status == CommerceReadinessStatus.CANCELLED:
            update.update(
                {
                    "status": CommerceWorkflowStatus.CANCELLED,
                    "execution_status": CommerceExecutionStatus.CANCELLED,
                }
            )
        elif assessment.status == CommerceReadinessStatus.COMPLETED:
            pass
        elif (
            workflow.capability_mode == CapabilityMode.VERIFIED_STATIC
            and not workflow.consent.status == ConsentStatus.GRANTED
        ):
            # A verified policy fact is ready to be answered by Stage 5 later;
            # Stage 4 neither creates a command nor claims it was answered.
            update["status"] = CommerceWorkflowStatus.READY_TO_EXECUTE
            reason_codes.append("verified_commerce_fact_ready")
        else:
            contract = workflow_registry.get(workflow.contract_id)
            consent_ok = bool(
                contract
                and (
                    not contract.requires_consent
                    or (
                        workflow.consent.status == ConsentStatus.GRANTED
                        and workflow.consent.payload_revision
                        == workflow.payload_revision
                        and workflow.consent.scope_fingerprint
                        == _scope_fingerprint(workflow)
                    )
                )
            )
            if not consent_ok:
                update["status"] = CommerceWorkflowStatus.AWAITING_CONSENT
                rejected.append(
                    CommerceRejectedProposal(
                        proposal_type="commerce_command",
                        reason_code="valid_scoped_consent_missing",
                        workflow_id=workflow.workflow_id,
                    )
                )
            elif workflow.capability_mode in {
                CapabilityMode.LOCAL_DRAFT_ONLY,
                CapabilityMode.TRANSACTIONAL_EXTERNAL,
            }:
                if not outbox_enabled:
                    update.update(
                        {
                            "status": CommerceWorkflowStatus.READY_TO_EXECUTE,
                            "execution_status": CommerceExecutionStatus.NOT_REQUESTED,
                        }
                    )
                    boundaries.append(
                        f"{workflow.workflow_id}:commerce_outbox_shadow_disabled"
                    )
                    planned[index] = workflow.model_copy(update=update)
                    continue
                command_id = _stable_id(
                    "command",
                    workflow.workflow_id,
                    workflow.payload_revision,
                    workflow.capability_id,
                )
                idempotency_key = hashlib.sha256(
                    (
                        f"{workflow.workflow_id}:{workflow.payload_revision}:"
                        f"{workflow.capability_id}"
                    ).encode("utf-8")
                ).hexdigest()
                existing_entry = next(
                    (
                        entry
                        for entry in previous_outbox
                        if entry.command.workflow_id == workflow.workflow_id
                        and entry.command.payload_revision == workflow.payload_revision
                    ),
                    None,
                )
                if existing_entry is None:
                    command = CommerceCommand(
                        command_id=command_id,
                        workflow_id=workflow.workflow_id,
                        payload_revision=workflow.payload_revision,
                        operation=workflow.workflow_kind,
                        capability_id=workflow.capability_id,
                        idempotency_key=idempotency_key,
                        payload_ref_ids=tuple(
                            item.sensitive_ref_id or item.source_fact_id
                            for item in workflow.fields
                            if item.sensitive_ref_id or item.source_fact_id
                        ),
                        task_ids=workflow.task_ids,
                        product_refs=workflow.product_refs,
                        line_items=workflow.line_items,
                        solution_id=workflow.solution_id,
                        consent_scope_fingerprint=workflow.consent.scope_fingerprint,
                        status=CommerceCommandStatus.READY,
                        reason_codes=("shadow_command_prepared_not_executed",),
                    )
                    previous_outbox.append(
                        CommerceOutboxEntry(
                            command=command,
                            status=OutboxStatus.READY,
                            last_reason_code="shadow_execution_disabled",
                        )
                    )
                    prepared_commands.append(command)
                else:
                    command = existing_entry.command
                    rejected.append(
                        CommerceRejectedProposal(
                            proposal_type="commerce_command",
                            reason_code="duplicate_command_ignored",
                            workflow_id=workflow.workflow_id,
                        )
                    )
                update.update(
                    {
                        "command_id": command.command_id,
                        "status": CommerceWorkflowStatus.READY_TO_EXECUTE,
                        "execution_status": CommerceExecutionStatus.PREPARED,
                    }
                )
                if workflow.capability_mode == CapabilityMode.LOCAL_DRAFT_ONLY:
                    boundaries.append(f"{workflow.workflow_id}:local_draft_only")
            else:
                update["status"] = CommerceWorkflowStatus.BLOCKED
                boundaries.append(f"{workflow.workflow_id}:unsupported_capability_mode")
        planned[index] = workflow.model_copy(update=update)

    # Preserve workflows unrelated to the current planning turn.
    planned_ids = {item.workflow_id for item in planned}
    all_workflows = tuple(
        [item for item in previous_workflows if item.workflow_id not in planned_ids]
        + planned
    )
    cancelled_ids = {
        item.workflow_id
        for item in all_workflows
        if item.status == CommerceWorkflowStatus.CANCELLED
    }
    outbox = tuple(
        (
            entry.model_copy(
                update={
                    "status": OutboxStatus.CANCELLED,
                    "last_reason_code": "workflow_cancelled_before_dispatch",
                }
            )
            if entry.command.workflow_id in cancelled_ids
            and entry.status in {OutboxStatus.PREPARED, OutboxStatus.READY}
            else entry
        )
        for entry in previous_outbox
    )
    if not resolutions and not controls:
        return CommercePlanningResult(
            status="skipped",
            workflows=tuple(previous_workflows),
            controls=tuple(getattr(dialogue_state, "commerce_controls", ())),
            outbox=tuple(getattr(dialogue_state, "commerce_outbox", ())),
            reason_codes=("no_commerce_task_or_control",),
        )
    return CommercePlanningResult(
        status="planned",
        workflow_resolutions=resolutions,
        readiness_assessments=tuple(readiness_assessments),
        workflows=all_workflows,
        controls=tuple(controls),
        outbox=outbox,
        prepared_commands=tuple(prepared_commands),
        rejected_proposals=tuple(rejected),
        capability_boundaries=tuple(boundaries),
        reason_codes=tuple(dict.fromkeys((*reason_codes, "commerce_shadow_only"))),
    )
