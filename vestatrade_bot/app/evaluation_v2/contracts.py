"""Versioned contracts for outcome-based dialogue evaluation.

This package is an offline/release-evaluation boundary. The contracts never
participate in customer response routing and deliberately contain no dialogue
state, session identifiers or full raw transcripts.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


OUTCOME_EVALUATION_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class OutcomePriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class SourceField(str, Enum):
    GOAL = "goal"
    PASS_CRITERIA = "pass_criteria"
    RED_FLAGS = "red_flags"
    CHECKS = "checks"
    EXPECTS_CARDS = "expects_cards"


class ContractNormalizationStatus(str, Enum):
    """Whether developer prose has received a reviewed typed normalization."""

    SOURCE_IMPORTED = "source_imported"
    REVIEWED = "reviewed"


class CriterionSource(str, Enum):
    GOAL = "goal"
    PASS_CRITERIA = "pass_criteria"
    RED_FLAG = "red_flag"
    DETERMINISTIC_GATE = "deterministic_gate"


class CriterionPolarity(str, Enum):
    REQUIRED = "required"
    PROHIBITED = "prohibited"


class CriterionEvaluationMode(str, Enum):
    DETERMINISTIC = "deterministic"
    INDEPENDENT_JUDGE = "independent_judge"
    HUMAN = "human"


class CriterionImportance(str, Enum):
    """Source prose does not reliably distinguish these automatically."""

    UNCLASSIFIED = "unclassified"
    MINIMUM_GOAL = "minimum_goal"
    REQUIRED = "required"
    QUALITY = "quality"


class FailureEffect(str, Enum):
    PARTIAL = "partial"
    FAIL = "fail"
    CRITICAL_FAIL = "critical_fail"


class TemporalScope(str, Enum):
    DIALOGUE = "dialogue"
    TURN = "turn"
    END_STATE = "end_state"


class OutcomeDisposition(str, Enum):
    UNCLASSIFIED = "unclassified"
    FULFILL = "fulfill"
    DECLINE_SAFELY = "decline_safely"
    CORRECT_FALSE_PREMISE = "correct_false_premise"
    PROTECT_PRIVATE_DATA = "protect_private_data"
    STATE_CAPABILITY_BOUNDARY = "state_capability_boundary"
    ROUTE_OR_HANDOFF = "route_or_handoff"


class TerminalState(str, Enum):
    RESOLVED = "resolved"
    PRELIMINARY_RESULT = "preliminary_result"
    BLOCKED_WITH_METHOD = "blocked_with_method"
    SAFE_REFUSAL = "safe_refusal"
    HANDOFF_CONFIRMED = "handoff_confirmed"
    HONEST_BOUNDARY = "honest_boundary"
    CLOSED = "closed"


class CriterionStatus(str, Enum):
    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    NOT_SATISFIED = "not_satisfied"
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class OutcomeVerdict(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceGrade(str, Enum):
    PROVISIONAL = "provisional"
    RELEASE_READY = "release_ready"


class EvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


class MachineAssessmentStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class ExecutionFailureActor(str, Enum):
    NONE = "none"
    BOT = "bot"
    TRANSPORT = "transport"
    BUYER = "buyer"
    HARNESS = "harness"
    UNKNOWN = "unknown"


class ViolationSeverity(str, Enum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"


class OutcomeRelation(str, Enum):
    LEFT_BETTER = "left_better"
    RIGHT_BETTER = "right_better"
    BOTH_VALID = "both_valid"
    BOTH_INVALID = "both_invalid"
    EQUIVALENT_PARTIAL = "equivalent_partial"
    NOT_COMPARABLE = "not_comparable"


class SourceRevision(FrozenModel):
    dataset_id: str = Field(min_length=1, max_length=160)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SourceSpan(FrozenModel):
    """Exact, hash-addressed slice of a developer-authored source field."""

    field: SourceField
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    exact_text: str = Field(min_length=1, max_length=5000)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_coordinates_and_hash(self) -> "SourceSpan":
        if self.end_char <= self.start_char:
            raise ValueError("source span must have positive width")
        if self.end_char - self.start_char != len(self.exact_text):
            raise ValueError("source span coordinates must match exact_text")
        digest = hashlib.sha256(self.exact_text.encode("utf-8")).hexdigest()
        if digest != self.text_sha256:
            raise ValueError("source span hash does not match exact_text")
        return self


class RequestSpec(FrozenModel):
    """Reviewed meaning is optional until a human normalization pass exists."""

    raw_goal: str = Field(min_length=1, max_length=3000)
    user_goal: str | None = Field(default=None, max_length=3000)
    test_objective: str | None = Field(default=None, max_length=3000)
    disposition: OutcomeDisposition = OutcomeDisposition.UNCLASSIFIED
    expected_terminal_states: tuple[TerminalState, ...] = ()


class OutcomeCriterion(FrozenModel):
    criterion_id: str = Field(min_length=8, max_length=120)
    source: CriterionSource
    polarity: CriterionPolarity
    description: str = Field(min_length=1, max_length=5000)
    evaluation_mode: CriterionEvaluationMode
    importance: CriterionImportance = CriterionImportance.UNCLASSIFIED
    failure_effect: FailureEffect = FailureEffect.FAIL
    temporal_scope: TemporalScope = TemporalScope.DIALOGUE
    required: bool = True
    conditional: bool = False
    activation_note: str | None = Field(default=None, max_length=1000)
    conditional_semantics_unresolved: bool = False
    deterministic_rule_codes: tuple[str, ...] = ()
    provenance: tuple[SourceSpan, ...] = ()

    @model_validator(mode="after")
    def validate_rule_authority(self) -> "OutcomeCriterion":
        if (
            self.evaluation_mode == CriterionEvaluationMode.DETERMINISTIC
            and not self.deterministic_rule_codes
        ):
            raise ValueError("deterministic criterion requires a rule code")
        if self.source != CriterionSource.DETERMINISTIC_GATE and not self.provenance:
            raise ValueError("source criterion requires provenance")
        if (
            self.source == CriterionSource.RED_FLAG
            and self.polarity != CriterionPolarity.PROHIBITED
        ):
            raise ValueError("red-flag criterion must be prohibited")
        if self.source == CriterionSource.GOAL and self.conditional:
            raise ValueError("the scenario goal cannot be conditional")
        if self.conditional and not self.activation_note:
            raise ValueError("conditional criterion requires an activation note")
        if not self.conditional and self.activation_note is not None:
            raise ValueError("unconditional criterion cannot have an activation note")
        return self


class CapabilityExpectation(FrozenModel):
    """Lossless source tag; not yet a claim that the capability exists."""

    expectation_id: str = Field(min_length=8, max_length=120)
    source_text: str = Field(min_length=1, max_length=500)
    provenance: SourceSpan


class OutcomeContract(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    contract_id: str = Field(min_length=8, max_length=120)
    scenario_id: str = Field(min_length=1, max_length=80)
    source_revision: SourceRevision
    normalization_status: ContractNormalizationStatus = (
        ContractNormalizationStatus.SOURCE_IMPORTED
    )
    normalization_id: str | None = Field(default=None, max_length=160)
    normalization_reviewer: str | None = Field(default=None, max_length=160)
    normalization_registry_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    normalization_approval_verified: bool = False
    block: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=300)
    priority: OutcomePriority
    difficulty: str = Field(default="", max_length=40)
    buyer_mode: str = Field(default="", max_length=80)
    request: RequestSpec
    expects_cards: bool = False
    criteria: tuple[OutcomeCriterion, ...]
    capability_expectations: tuple[CapabilityExpectation, ...] = ()
    hard_gate_codes: tuple[str, ...] = (
        "EMPTY_ANSWER",
        "TRANSPORT_ERROR",
        "UNKNOWN_CARD_SKU",
        "CARD_NAME_MISMATCH",
        "CARD_PRICE_MISMATCH",
        "CARD_CURRENCY_MISMATCH",
        "CARD_STOCK_MISMATCH",
        "CARD_STOCK_QTY_MISMATCH",
        "CARD_URL_MISMATCH",
        "INVENTED_TECHNICAL_FACT",
        "UNSAFE_INSTRUCTION",
        "PII_LEAK",
        "HARD_CONSTRAINT_LOST",
    )

    @property
    def release_ready(self) -> bool:
        return bool(
            self.normalization_status == ContractNormalizationStatus.REVIEWED
            and self.normalization_approval_verified
            and self.normalization_id
            and self.normalization_reviewer
            and self.normalization_registry_sha256
        )

    @model_validator(mode="after")
    def validate_criteria(self) -> "OutcomeContract":
        ids = [item.criterion_id for item in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("outcome criterion ids must be unique")
        if not any(item.source == CriterionSource.GOAL for item in self.criteria):
            raise ValueError("outcome contract requires a goal criterion")
        if self.normalization_status == ContractNormalizationStatus.REVIEWED:
            if not self.normalization_id or not self.normalization_reviewer:
                raise ValueError("reviewed contract requires review provenance")
            if not self.normalization_registry_sha256:
                raise ValueError("reviewed contract requires a registry digest")
            if not self.request.user_goal:
                raise ValueError("reviewed contract requires a separated user goal")
            if self.request.disposition == OutcomeDisposition.UNCLASSIFIED:
                raise ValueError("reviewed contract requires an outcome disposition")
            if not self.request.expected_terminal_states:
                raise ValueError("reviewed contract requires terminal states")
            if any(
                item.importance == CriterionImportance.UNCLASSIFIED
                for item in self.criteria
            ):
                raise ValueError("reviewed contract cannot contain unclassified criteria")
            if any(item.conditional_semantics_unresolved for item in self.criteria):
                raise ValueError("reviewed contract cannot retain unresolved semantics")
            if not any(
                item.importance == CriterionImportance.MINIMUM_GOAL
                for item in self.criteria
            ):
                raise ValueError("reviewed contract requires a minimum-goal criterion")
            minimum_goal_ids = {
                item.criterion_id
                for item in self.criteria
                if item.importance == CriterionImportance.MINIMUM_GOAL
            }
            source_goal_ids = {
                item.criterion_id
                for item in self.criteria
                if item.source == CriterionSource.GOAL
            }
            if minimum_goal_ids != source_goal_ids:
                raise ValueError(
                    "reviewed minimum-goal classification must match source goals"
                )
            if any(
                item.criterion_id in minimum_goal_ids
                and item.failure_effect
                not in {FailureEffect.FAIL, FailureEffect.CRITICAL_FAIL}
                for item in self.criteria
            ):
                raise ValueError("reviewed minimum goal must fail when unsatisfied")
        elif any(
            (
                self.normalization_id,
                self.normalization_reviewer,
                self.normalization_registry_sha256,
                self.normalization_approval_verified,
            )
        ):
            raise ValueError("source-imported contract cannot claim review provenance")
        return self


class TranscriptProduct(FrozenModel):
    sku: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=1000)
    price: FiniteFloat | None = None
    currency: str | None = Field(default=None, max_length=20)
    stock_status: str | None = Field(default=None, max_length=120)
    stock_qty: int | None = None
    url: str | None = Field(default=None, max_length=3000)
    product_kind: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    presentation_status: str | None = Field(default=None, max_length=120)


class TranscriptTurn(FrozenModel):
    turn_number: int = Field(ge=1)
    user_text: str = Field(default="", max_length=20000)
    assistant_text: str = Field(default="", max_length=30000)
    products: tuple[TranscriptProduct, ...] = ()
    error_code: str | None = Field(default=None, max_length=300)
    response_owner: str | None = Field(default=None, max_length=80)


class DialogueTranscript(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    scenario_id: str = Field(min_length=1, max_length=80)
    source_label: str = Field(default="candidate", min_length=1, max_length=120)
    execution_status: str = Field(default="unknown", max_length=80)
    execution_failure_actor: ExecutionFailureActor = ExecutionFailureActor.UNKNOWN
    execution_error_code: str | None = Field(default=None, max_length=160)
    turns: tuple[TranscriptTurn, ...]

    @model_validator(mode="before")
    @classmethod
    def infer_failure_actor(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "execution_failure_actor" in value:
            return value
        payload = dict(value)
        status = str(payload.get("execution_status") or "unknown").casefold()
        if status == "valid":
            actor = ExecutionFailureActor.NONE
        elif status in {"bot_error"}:
            actor = ExecutionFailureActor.BOT
        elif status in {"transport_error", "timeout", "invalid"}:
            actor = ExecutionFailureActor.TRANSPORT
        elif status.startswith("buyer_"):
            actor = ExecutionFailureActor.BUYER
        elif status == "harness_error":
            actor = ExecutionFailureActor.HARNESS
        else:
            actor = ExecutionFailureActor.UNKNOWN
        payload["execution_failure_actor"] = actor
        return payload

    @model_validator(mode="after")
    def validate_turn_order(self) -> "DialogueTranscript":
        numbers = [item.turn_number for item in self.turns]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("transcript turns must be unique and ordered")
        return self


class CatalogTruthProduct(FrozenModel):
    sku: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=1000)
    price: FiniteFloat | None = None
    currency: str | None = Field(default=None, max_length=20)
    stock_status: str | None = Field(default=None, max_length=120)
    stock_qty: int | None = None
    url: str | None = Field(default=None, max_length=3000)
    product_kind: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)


class MachineViolation(FrozenModel):
    violation_id: str = Field(min_length=8, max_length=100)
    code: str = Field(min_length=2, max_length=120)
    severity: ViolationSeverity
    verdict_cap: OutcomeVerdict
    turn_numbers: tuple[int, ...] = ()
    product_sku: str | None = Field(default=None, max_length=200)
    reason_code: str = Field(min_length=2, max_length=300)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cap(self) -> "MachineViolation":
        if self.verdict_cap == OutcomeVerdict.UNAVAILABLE:
            raise ValueError("a machine violation cannot cap to unavailable")
        return self


class MachineAssessment(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    status: MachineAssessmentStatus
    checked_rule_codes: tuple[str, ...] = ()
    unchecked_hard_gate_codes: tuple[str, ...] = ()
    violations: tuple[MachineViolation, ...] = ()
    limitation_reason_codes: tuple[str, ...] = ()
    outcome_blocking_reason_codes: tuple[str, ...] = ()
    evidence_binding: EvidenceBinding | None = None

    @model_validator(mode="after")
    def validate_gate_coverage(self) -> "MachineAssessment":
        checked = set(self.checked_rule_codes)
        unchecked = set(self.unchecked_hard_gate_codes)
        if len(checked) != len(self.checked_rule_codes):
            raise ValueError("checked machine rule codes must be unique")
        if len(unchecked) != len(self.unchecked_hard_gate_codes):
            raise ValueError("unchecked hard gate codes must be unique")
        if checked & unchecked:
            raise ValueError("a hard gate cannot be both checked and unchecked")
        if any(item.code not in checked for item in self.violations):
            raise ValueError("every machine violation must come from a checked rule")
        if self.status == MachineAssessmentStatus.COMPLETE and (
            self.unchecked_hard_gate_codes
            or self.limitation_reason_codes
            or self.outcome_blocking_reason_codes
        ):
            raise ValueError("complete machine assessment cannot retain limitations")
        return self


class EvidenceBinding(FrozenModel):
    contract_id: str = Field(min_length=8, max_length=120)
    scenario_id: str = Field(min_length=1, max_length=80)
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReleaseRunEvidence(FrozenModel):
    """Fail-closed provenance required before an outcome can gate release."""

    source_manifest_verified: bool = False
    transcript_digest_bound: bool = False
    testset_revision_verified: bool = False
    catalog_revision_verified: bool = False
    bot_model_verified: bool = False
    live_run_verified: bool = False
    full_suite_selected: bool = False
    normalization_registry_approved: bool = False
    reason_codes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(
            (
                self.source_manifest_verified,
                self.transcript_digest_bound,
                self.testset_revision_verified,
                self.catalog_revision_verified,
                self.bot_model_verified,
                self.live_run_verified,
                self.full_suite_selected,
                self.normalization_registry_approved,
            )
        )


class CriterionAssessment(FrozenModel):
    criterion_id: str = Field(min_length=8, max_length=120)
    status: CriterionStatus
    evidence_turn_numbers: tuple[int, ...] = ()
    rationale: str = Field(default="", max_length=1500)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class JudgeAssessment(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    status: EvaluationStatus
    proposed_verdict: OutcomeVerdict = OutcomeVerdict.UNAVAILABLE
    criterion_assessments: tuple[CriterionAssessment, ...] = ()
    detected_red_flag_ids: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model: str | None = Field(default=None, max_length=200)
    reason_codes: tuple[str, ...] = ()
    evidence_binding: EvidenceBinding | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "JudgeAssessment":
        if self.status == EvaluationStatus.EVALUATED:
            if self.proposed_verdict == OutcomeVerdict.UNAVAILABLE:
                raise ValueError("evaluated judge cannot propose unavailable")
            if not self.criterion_assessments:
                raise ValueError("evaluated judge requires criterion assessments")
            if not self.model:
                raise ValueError("evaluated judge requires model identity")
        elif self.proposed_verdict != OutcomeVerdict.UNAVAILABLE:
            raise ValueError("non-evaluated judge must use unavailable verdict")
        return self


class OutcomeEvaluation(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    contract_id: str = Field(min_length=8, max_length=120)
    scenario_id: str = Field(min_length=1, max_length=80)
    source_label: str = Field(min_length=1, max_length=120)
    evidence_grade: EvidenceGrade
    release_eligible: bool = False
    machine: MachineAssessment
    judge: JudgeAssessment
    final_verdict: OutcomeVerdict
    gate_blocking_reason_codes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    evidence_binding: EvidenceBinding
    release_run_evidence: ReleaseRunEvidence = Field(
        default_factory=ReleaseRunEvidence
    )

    @model_validator(mode="after")
    def validate_release_evidence(self) -> "OutcomeEvaluation":
        if self.release_eligible and self.evidence_grade != EvidenceGrade.RELEASE_READY:
            raise ValueError("release-eligible result requires release-ready evidence")
        if self.release_eligible and not self.release_run_evidence.complete:
            raise ValueError("release-eligible result requires complete run provenance")
        if self.release_eligible and self.machine.evidence_binding != self.evidence_binding:
            raise ValueError("release-eligible machine evidence must be bound")
        deterministic_failure = any(
            item.severity == ViolationSeverity.P0
            or item.verdict_cap == OutcomeVerdict.FAIL
            for item in self.machine.violations
        )
        if self.release_eligible and self.final_verdict == OutcomeVerdict.UNAVAILABLE:
            raise ValueError("release-eligible result cannot be unavailable")
        if (
            self.release_eligible
            and deterministic_failure
            and self.final_verdict != OutcomeVerdict.FAIL
        ):
            raise ValueError(
                "release-eligible deterministic failure requires a fail verdict"
            )
        if (
            self.release_eligible
            and not deterministic_failure
            and self.judge.evidence_binding != self.evidence_binding
        ):
            raise ValueError("release-eligible semantic evidence must be bound")
        if self.release_eligible and not deterministic_failure:
            if self.machine.status != MachineAssessmentStatus.COMPLETE:
                raise ValueError(
                    "release-eligible semantic result requires complete machine evidence"
                )
            if (
                self.machine.unchecked_hard_gate_codes
                or self.machine.limitation_reason_codes
                or self.machine.outcome_blocking_reason_codes
            ):
                raise ValueError(
                    "release-eligible semantic result cannot retain machine gaps"
                )
            if self.judge.status != EvaluationStatus.EVALUATED:
                raise ValueError(
                    "release-eligible semantic result requires an evaluated judge"
                )
            if self.judge.confidence < 0.6 or any(
                item.confidence < 0.5
                for item in self.judge.criterion_assessments
            ):
                raise ValueError(
                    "release-eligible semantic result requires confident judge evidence"
                )
        if (
            self.release_eligible
            and self.final_verdict != OutcomeVerdict.FAIL
            and self.machine.unchecked_hard_gate_codes
        ):
            raise ValueError("release-eligible success requires every hard gate")
        return self


class OutcomeComparison(FrozenModel):
    schema_version: Literal["1.0"] = OUTCOME_EVALUATION_SCHEMA_VERSION
    scenario_id: str = Field(min_length=1, max_length=80)
    contract_id: str = Field(min_length=8, max_length=120)
    left_label: str = Field(min_length=1, max_length=120)
    right_label: str = Field(min_length=1, max_length=120)
    relation: OutcomeRelation
    left_verdict: OutcomeVerdict
    right_verdict: OutcomeVerdict
    release_eligible: bool = False
    reason_codes: tuple[str, ...] = ()
