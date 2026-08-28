"""Pure deterministic rollout selection and single-owner arbitration."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    CutoverDecision,
    EarlyControlOutcome,
    EarlyControlResult,
    ExecutionMode,
    MigrationCell,
    MigrationRegistry,
    ResponseOwner,
    RolloutStage,
    TurnArbitration,
    V2TurnCandidate,
)


class CutoverRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routing_enabled: bool = False
    shadow_compare_enabled: bool = False
    live_delivery_enabled: bool = False
    internal_canary_enabled: bool = False
    internal_canary_percent: int = Field(default=0, ge=0, le=5)
    local_preview_enabled: bool = False
    # Protected per-request QA preview.  It bypasses rollout registry/cohort
    # assignment, but never semantic, contract, grounding or safety gates.
    qa_preview_enabled: bool = False
    external_actions_enabled: bool = False
    force_legacy: bool = False
    registry_valid: bool = True
    is_existing_session: bool = False
    sticky_cell_id: str | None = None
    sticky_assignment_id: str | None = None


def _decision(
    owner: ResponseOwner,
    mode: ExecutionMode,
    *reasons: str,
    eligible: bool = False,
    cell: MigrationCell | None = None,
    bucket: int | None = None,
    assignment: str | None = None,
    candidate: V2TurnCandidate | None = None,
) -> CutoverDecision:
    return CutoverDecision(
        owner_candidate=owner,
        execution_mode=mode,
        cell_id=cell.cell_id if cell else None,
        cohort_bucket=bucket,
        sticky_assignment_id=assignment,
        eligible=eligible,
        reason_codes=tuple(dict.fromkeys(reasons)),
        required_stage_versions={
            "semantic": "1.1",
            "dialogue_state": "2.0",
            "catalog_contract": "1.0",
            "answer_plan": "1.0",
            "cutover": "1.0",
        },
        catalog_revision=(candidate.catalog_revision if candidate else None),
        fallback_allowed=not bool(
            candidate and candidate.external_side_effect_started
        ),
    )


def _cell_matches(cell: MigrationCell, candidate: V2TurnCandidate) -> bool:
    if not candidate.task_acts or not candidate.product_kinds:
        return False
    if not set(candidate.task_acts).issubset(cell.task_acts):
        return False
    if not set(candidate.product_kinds).issubset(cell.product_kinds):
        return False
    if candidate.answer_status not in cell.allowed_answer_statuses:
        return False
    if candidate.next_action not in cell.allowed_next_actions:
        return False
    if not set(candidate.contract_versions).issubset(cell.product_contract_versions):
        return False
    if (
        cell.required_catalog_revision
        and candidate.catalog_revision != cell.required_catalog_revision
    ):
        return False
    if candidate.pending_command_ids and not cell.external_actions_allowed:
        return False
    if cell.require_single_exact_product and (
        candidate.response is None
        or len(candidate.response.products) != 1
        or candidate.product_statuses != ("exact",)
    ):
        return False
    return True


def _bucket(session_fingerprint: str, registry_revision: str) -> int:
    digest = hashlib.sha256(
        f"{session_fingerprint}:{registry_revision}".encode("utf-8")
    ).hexdigest()
    return int(digest[:8], 16) % 100


def _assignment(session_fingerprint: str, cell_id: str) -> str:
    return hashlib.sha256(
        f"{session_fingerprint}:{cell_id}".encode("utf-8")
    ).hexdigest()[:24]


def decide_cutover(
    early_control: EarlyControlResult,
    candidate: V2TurnCandidate | None,
    registry: MigrationRegistry,
    runtime: CutoverRuntime,
    *,
    session_fingerprint: str,
) -> CutoverDecision:
    """Choose an owner from typed inputs only, without performing I/O."""

    if early_control.outcome in {
        EarlyControlOutcome.SAFETY_RESPONSE,
        EarlyControlOutcome.EMERGENCY_RESPONSE,
        EarlyControlOutcome.BLOCKED,
    }:
        return _decision(
            ResponseOwner.SAFETY,
            ExecutionMode.SAFETY_INTERCEPT,
            *early_control.reason_codes,
            "early_control_preempts_dialogue",
        )
    if early_control.outcome == EarlyControlOutcome.PII_CONTROL:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
            *early_control.reason_codes,
            "pii_turn_not_canary_eligible",
        )
    if runtime.force_legacy:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
            "force_legacy_kill_switch",
        )
    if not runtime.routing_enabled:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
            "v2_routing_disabled",
        )
    if not runtime.registry_valid and not runtime.qa_preview_enabled:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
            "migration_registry_invalid",
        )
    if candidate is None or not candidate.semantic_accepted:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_FALLBACK,
            "semantic_result_unavailable",
            candidate=candidate,
        )
    if not candidate.eligible_for_delivery or not candidate.contracts_resolved:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_FALLBACK,
            *(candidate.rejection_reason_codes or ("v2_candidate_rejected",)),
            candidate=candidate,
        )
    if (
        candidate.validation_status != "accepted"
        or candidate.response is None
        or candidate.response_digest is None
        or candidate.answer_plan_id is None
        or candidate.rendered_answer_id is None
        or candidate.source_revision is None
        or candidate.catalog_revision is None
    ):
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_FALLBACK,
            "v2_candidate_delivery_proof_incomplete",
            candidate=candidate,
        )

    if runtime.qa_preview_enabled:
        if (
            runtime.external_actions_enabled
            or candidate.pending_command_ids
            or candidate.external_side_effect_started
        ):
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "qa_preview_external_actions_not_disabled",
                candidate=candidate,
            )
        return _decision(
            ResponseOwner.V2,
            ExecutionMode.V2_PRIMARY,
            "approved_protected_qa_v2_preview",
            eligible=True,
            candidate=candidate,
        )

    matching = tuple(cell for cell in registry.cells if _cell_matches(cell, candidate))
    if runtime.sticky_cell_id:
        matching = tuple(
            cell for cell in matching if cell.cell_id == runtime.sticky_cell_id
        )
    if len(matching) != 1:
        reason = "no_matching_rollout_cell" if not matching else "ambiguous_rollout_cell"
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_FALLBACK,
            reason,
            candidate=candidate,
        )

    cell = matching[0]
    if runtime.sticky_assignment_id:
        expected_assignment = _assignment(session_fingerprint, cell.cell_id)
        if runtime.sticky_assignment_id != expected_assignment:
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "sticky_assignment_invalid",
                cell=cell,
                candidate=candidate,
            )
    if cell.stage in {RolloutStage.LEGACY, RolloutStage.SHADOW}:
        mode = (
            ExecutionMode.SHADOW_COMPARE
            if cell.stage == RolloutStage.SHADOW and runtime.shadow_compare_enabled
            else ExecutionMode.LEGACY_ONLY
        )
        return _decision(
            ResponseOwner.LEGACY,
            mode,
            f"rollout_cell_{cell.stage.value}",
            cell=cell,
            candidate=candidate,
        )
    if cell.stage == RolloutStage.RETIRED:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
            "retired_cell_cannot_be_enabled_by_runtime_flag",
            cell=cell,
            candidate=candidate,
        )
    if not runtime.live_delivery_enabled:
        return _decision(
            ResponseOwner.LEGACY,
            ExecutionMode.SHADOW_COMPARE,
            "v2_live_delivery_disabled",
            cell=cell,
            candidate=candidate,
        )
    if cell.stage == RolloutStage.INTERNAL_CANARY:
        if not runtime.internal_canary_enabled:
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.SHADOW_COMPARE,
                "internal_canary_disabled",
                cell=cell,
                candidate=candidate,
            )
        if runtime.is_existing_session and not (
            cell.existing_sessions_allowed or runtime.sticky_assignment_id
        ):
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "existing_session_not_canary_eligible",
                cell=cell,
                candidate=candidate,
            )
        cohort = _bucket(session_fingerprint, registry.revision)
        threshold = min(runtime.internal_canary_percent, cell.canary_percent)
        if runtime.sticky_assignment_id:
            assignment = runtime.sticky_assignment_id
        else:
            assignment = _assignment(session_fingerprint, cell.cell_id)
        if not runtime.sticky_assignment_id and cohort >= threshold:
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "session_outside_canary_cohort",
                cell=cell,
                bucket=cohort,
                assignment=assignment,
                candidate=candidate,
            )
        return _decision(
            ResponseOwner.V2,
            ExecutionMode.V2_INTERNAL_CANARY,
            "approved_internal_canary_cell",
            eligible=True,
            cell=cell,
            bucket=cohort,
            assignment=assignment,
            candidate=candidate,
        )

    if cell.stage == RolloutStage.V2_PRIMARY:
        if not runtime.local_preview_enabled:
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "v2_primary_not_enabled_in_stage6a",
                cell=cell,
                candidate=candidate,
            )
        if (
            runtime.external_actions_enabled
            or cell.external_actions_allowed
            or candidate.pending_command_ids
            or candidate.external_side_effect_started
        ):
            return _decision(
                ResponseOwner.LEGACY,
                ExecutionMode.LEGACY_ONLY,
                "local_preview_external_actions_not_disabled",
                cell=cell,
                candidate=candidate,
            )
        return _decision(
            ResponseOwner.V2,
            ExecutionMode.V2_PRIMARY,
            "approved_local_v2_primary_preview",
            eligible=True,
            cell=cell,
            candidate=candidate,
        )

    # Stage 6A contains internal canary infrastructure only.  An external
    # registry must not be able to turn the future primary stage into public
    # traffic before the later rollout stage adds a separately reviewed gate.
    return _decision(
        ResponseOwner.LEGACY,
        ExecutionMode.LEGACY_ONLY,
        "v2_primary_not_enabled_in_stage6a",
        cell=cell,
        candidate=candidate,
    )


def arbitrate_turn(
    decision: CutoverDecision,
    candidate: V2TurnCandidate | None,
) -> TurnArbitration:
    """Select one complete candidate; legacy execution remains outside this pure step."""

    if decision.owner_candidate == ResponseOwner.V2:
        if (
            decision.eligible
            and candidate is not None
            and candidate.eligible_for_delivery
            and candidate.response is not None
        ):
            return TurnArbitration(
                response_owner=ResponseOwner.V2,
                execution_mode=decision.execution_mode,
                response=candidate.response,
                selected_state=candidate.state_after,
                reason_codes=decision.reason_codes,
            )
        return TurnArbitration(
            response_owner=ResponseOwner.LEGACY,
            execution_mode=ExecutionMode.LEGACY_FALLBACK,
            fallback_required=not bool(
                candidate and candidate.external_side_effect_started
            ),
            external_fallback_forbidden=bool(
                candidate and candidate.external_side_effect_started
            ),
            reason_codes=("v2_arbitration_candidate_invalid",),
        )
    legacy_fallback = decision.owner_candidate == ResponseOwner.LEGACY
    fallback_allowed = legacy_fallback and decision.fallback_allowed
    return TurnArbitration(
        response_owner=decision.owner_candidate,
        execution_mode=decision.execution_mode,
        fallback_required=fallback_allowed,
        external_fallback_forbidden=(
            legacy_fallback and not decision.fallback_allowed
        ),
        reason_codes=decision.reason_codes,
    )


class CutoverPolicy:
    """Named stateless policy facade for dependency injection boundaries."""

    decide = staticmethod(decide_cutover)


class TurnArbiter:
    """Named stateless single-owner arbitration facade."""

    select = staticmethod(arbitrate_turn)
