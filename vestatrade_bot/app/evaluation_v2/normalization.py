"""Reviewed normalization overlay for losslessly imported source contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .contracts import (
    ContractNormalizationStatus,
    CriterionImportance,
    FailureEffect,
    FrozenModel,
    OutcomeContract,
    OutcomeCriterion,
    OutcomeDisposition,
    RequestSpec,
    TerminalState,
    TemporalScope,
)


# A registry becomes release-authoritative only through an ordinary reviewed
# code change that pins its digest here. CLI input cannot self-approve review.
APPROVED_NORMALIZATION_REGISTRY_SHA256: frozenset[str] = frozenset()


class CriterionNormalization(FrozenModel):
    criterion_id: str = Field(min_length=8, max_length=120)
    importance: CriterionImportance
    failure_effect: FailureEffect
    temporal_scope: TemporalScope
    conditional: bool = False
    activation_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_classification(self) -> "CriterionNormalization":
        if self.importance == CriterionImportance.UNCLASSIFIED:
            raise ValueError("reviewed criterion cannot remain unclassified")
        if self.conditional and not self.activation_note:
            raise ValueError("conditional criterion requires an activation note")
        if not self.conditional and self.activation_note is not None:
            raise ValueError("unconditional criterion cannot have an activation note")
        return self


class ContractNormalization(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    normalization_id: str = Field(min_length=8, max_length=160)
    contract_id: str = Field(min_length=8, max_length=120)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    user_goal: str = Field(min_length=1, max_length=3000)
    test_objective: str = Field(min_length=1, max_length=3000)
    disposition: OutcomeDisposition
    expected_terminal_states: tuple[TerminalState, ...]
    criteria: tuple[CriterionNormalization, ...]
    reviewer: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_terminal_state(self) -> "ContractNormalization":
        if not self.expected_terminal_states:
            raise ValueError("normalization requires at least one terminal state")
        if self.disposition == OutcomeDisposition.UNCLASSIFIED:
            raise ValueError("normalization requires an outcome disposition")
        ids = [item.criterion_id for item in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion normalizations must be unique")
        if not any(
            item.importance == CriterionImportance.MINIMUM_GOAL
            for item in self.criteria
        ):
            raise ValueError("normalization requires a minimum-goal criterion")
        return self


def apply_reviewed_normalization(
    contract: OutcomeContract,
    normalization: ContractNormalization,
    *,
    registry_sha256: str | None = None,
    approval_verified: bool = False,
) -> OutcomeContract:
    """Apply a complete, revision-locked human review or fail as stale."""

    if normalization.contract_id != contract.contract_id:
        raise ValueError("normalization belongs to a different contract")
    if normalization.dataset_sha256 != contract.source_revision.dataset_sha256:
        raise ValueError("normalization dataset revision is stale")
    if normalization.scenario_sha256 != contract.source_revision.scenario_sha256:
        raise ValueError("normalization scenario revision is stale")
    by_id = {item.criterion_id: item for item in normalization.criteria}
    contract_ids = {item.criterion_id for item in contract.criteria}
    if set(by_id) != contract_ids:
        raise ValueError("normalization must classify every contract criterion")
    goal_ids = {
        item.criterion_id
        for item in contract.criteria
        if item.source.value == "goal"
    }
    minimum_goal_ids = {
        item.criterion_id
        for item in normalization.criteria
        if item.importance == CriterionImportance.MINIMUM_GOAL
    }
    if minimum_goal_ids != goal_ids:
        raise ValueError(
            "reviewed minimum-goal classification must match source goal criteria"
        )
    if any(
        item.criterion_id in minimum_goal_ids
        and item.failure_effect not in {FailureEffect.FAIL, FailureEffect.CRITICAL_FAIL}
        for item in normalization.criteria
    ):
        raise ValueError("minimum-goal criterion must fail when not satisfied")
    if registry_sha256 is None:
        registry_sha256 = hashlib.sha256(
            json.dumps(
                normalization.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    criteria = []
    for criterion in contract.criteria:
        reviewed = by_id[criterion.criterion_id]
        if criterion.polarity.value == "prohibited" and (
            reviewed.failure_effect == FailureEffect.PARTIAL
        ):
            raise ValueError("a reviewed red flag cannot have partial failure effect")
        criteria.append(
            OutcomeCriterion.model_validate(
                {
                    **criterion.model_dump(mode="python"),
                    "importance": reviewed.importance,
                    "failure_effect": reviewed.failure_effect,
                    "temporal_scope": reviewed.temporal_scope,
                    "conditional": reviewed.conditional,
                    "activation_note": reviewed.activation_note,
                    "conditional_semantics_unresolved": False,
                }
            )
        )

    return OutcomeContract.model_validate(
        {
            **contract.model_dump(mode="python"),
            "normalization_status": ContractNormalizationStatus.REVIEWED,
            "normalization_id": normalization.normalization_id,
            "normalization_reviewer": normalization.reviewer,
            "normalization_registry_sha256": registry_sha256,
            "normalization_approval_verified": approval_verified,
            "request": RequestSpec(
                raw_goal=contract.request.raw_goal,
                user_goal=normalization.user_goal,
                test_objective=normalization.test_objective,
                disposition=normalization.disposition,
                expected_terminal_states=normalization.expected_terminal_states,
            ),
            "criteria": tuple(criteria),
        }
    )


def apply_normalization_registry(
    contracts: tuple[OutcomeContract, ...],
    normalizations: tuple[ContractNormalization, ...],
    *,
    require_full_coverage: bool = True,
    registry_sha256: str | None = None,
    approval_verified: bool = False,
) -> tuple[OutcomeContract, ...]:
    """Apply a one-to-one checked-in registry deterministically."""

    by_contract = {item.contract_id: item for item in normalizations}
    if len(by_contract) != len(normalizations):
        raise ValueError("normalization registry contains duplicate contracts")
    known_ids = {item.contract_id for item in contracts}
    orphaned = set(by_contract) - known_ids
    if orphaned:
        raise ValueError("normalization registry contains unknown contracts")
    if require_full_coverage and set(by_contract) != known_ids:
        raise ValueError("normalization registry does not cover every contract")
    return tuple(
        apply_reviewed_normalization(
            contract,
            by_contract[contract.contract_id],
            registry_sha256=registry_sha256,
            approval_verified=approval_verified,
        )
        if contract.contract_id in by_contract
        else contract
        for contract in contracts
    )
