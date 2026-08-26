"""Goal-based comparison of independently evaluated system outcomes."""

from __future__ import annotations

from .contracts import (
    CriterionStatus,
    EvaluationStatus,
    OutcomeComparison,
    OutcomeEvaluation,
    OutcomeRelation,
    OutcomeVerdict,
    ViolationSeverity,
)


_RANK = {
    OutcomeVerdict.FAIL: 0,
    OutcomeVerdict.PARTIAL: 1,
    OutcomeVerdict.PASS: 2,
}

_REQUIRED_CRITERION_RANK = {
    CriterionStatus.NOT_SATISFIED: 0,
    CriterionStatus.PARTIALLY_SATISFIED: 1,
    CriterionStatus.SATISFIED: 2,
}
_PROHIBITED_CRITERION_RANK = {
    CriterionStatus.TRIGGERED: 0,
    CriterionStatus.NOT_TRIGGERED: 1,
}
_LEFT = "left"
_RIGHT = "right"


def _unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _comparison(
    left: OutcomeEvaluation,
    right: OutcomeEvaluation,
    *,
    relation: OutcomeRelation,
    release_eligible: bool,
    reason_codes: tuple[str, ...],
) -> OutcomeComparison:
    return OutcomeComparison(
        scenario_id=left.scenario_id,
        contract_id=left.contract_id,
        left_label=left.source_label,
        right_label=right.source_label,
        relation=relation,
        left_verdict=left.final_verdict,
        right_verdict=right.final_verdict,
        release_eligible=release_eligible,
        reason_codes=reason_codes,
    )


def _criterion_index(
    evaluation: OutcomeEvaluation,
) -> tuple[dict[str, CriterionStatus] | None, str | None]:
    if evaluation.judge.status != EvaluationStatus.EVALUATED:
        return None, "criterion_assessment_unavailable"
    assessments = evaluation.judge.criterion_assessments
    if not assessments:
        return None, "criterion_assessment_missing"
    identifiers = [item.criterion_id for item in assessments]
    if len(identifiers) != len(set(identifiers)):
        return None, "criterion_alignment_contains_duplicate_ids"
    return {item.criterion_id: item.status for item in assessments}, None


def _criterion_direction(
    left_status: CriterionStatus,
    right_status: CriterionStatus,
) -> tuple[str | None, tuple[str, ...], str | None]:
    """Compare one aligned criterion without guessing its missing semantics."""

    if CriterionStatus.UNKNOWN in {left_status, right_status}:
        return None, (), "criterion_assessment_unknown"
    if CriterionStatus.NOT_APPLICABLE in {left_status, right_status}:
        if left_status == right_status:
            return None, (), None
        return None, (), "criterion_applicability_changed"

    if (
        left_status in _REQUIRED_CRITERION_RANK
        and right_status in _REQUIRED_CRITERION_RANK
    ):
        left_rank = _REQUIRED_CRITERION_RANK[left_status]
        right_rank = _REQUIRED_CRITERION_RANK[right_status]
        if left_rank == right_rank:
            return None, (), None
        if left_rank > right_rank:
            return (
                _LEFT,
                (
                    "left_improves_required_criterion",
                    "right_regresses_required_criterion",
                ),
                None,
            )
        return (
            _RIGHT,
            (
                "right_improves_required_criterion",
                "left_regresses_required_criterion",
            ),
            None,
        )

    if (
        left_status in _PROHIBITED_CRITERION_RANK
        and right_status in _PROHIBITED_CRITERION_RANK
    ):
        left_rank = _PROHIBITED_CRITERION_RANK[left_status]
        right_rank = _PROHIBITED_CRITERION_RANK[right_status]
        if left_rank == right_rank:
            return None, (), None
        if left_rank > right_rank:
            return _LEFT, ("right_triggers_prohibited_criterion",), None
        return _RIGHT, ("left_triggers_prohibited_criterion",), None

    return None, (), "criterion_status_family_mismatch"


def compare_outcome_evaluations(
    left: OutcomeEvaluation,
    right: OutcomeEvaluation,
) -> OutcomeComparison:
    """Compare developer-goal results; never treat legacy as the oracle."""

    if left.scenario_id != right.scenario_id or left.contract_id != right.contract_id:
        raise ValueError("outcome evaluations must use the same scenario contract")
    if (
        left.evidence_binding.contract_sha256
        != right.evidence_binding.contract_sha256
    ):
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=("normalized_contract_revision_mismatch",),
        )
    release_eligible = left.release_eligible and right.release_eligible
    if (
        left.final_verdict == OutcomeVerdict.UNAVAILABLE
        or right.final_verdict == OutcomeVerdict.UNAVAILABLE
    ):
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=("comparable_goal_evaluation_unavailable",),
        )

    left_criteria, left_error = _criterion_index(left)
    right_criteria, right_error = _criterion_index(right)
    evidence_errors = _unique(
        [item for item in (left_error, right_error) if item is not None]
    )
    if evidence_errors:
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=evidence_errors,
        )
    assert left_criteria is not None and right_criteria is not None
    if set(left_criteria) != set(right_criteria):
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=("criterion_coverage_mismatch",),
        )

    left_rank = _RANK[left.final_verdict]
    right_rank = _RANK[right.final_verdict]
    coarse_direction: str | None = None
    reasons: list[str] = []
    if left_rank != right_rank:
        coarse_direction = _LEFT if left_rank > right_rank else _RIGHT
        reasons.append("developer_goal_verdict_differs")

    criterion_directions: set[str] = set()
    criterion_reasons: list[str] = []
    criterion_errors: list[str] = []
    for criterion_id in sorted(left_criteria):
        direction, detail_reasons, error = _criterion_direction(
            left_criteria[criterion_id],
            right_criteria[criterion_id],
        )
        if error is not None:
            criterion_errors.append(error)
            continue
        if direction is not None:
            criterion_directions.add(direction)
        criterion_reasons.extend(detail_reasons)

    if criterion_errors:
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=_unique(criterion_errors),
        )

    directions = set(criterion_directions)
    if coarse_direction is not None:
        directions.add(coarse_direction)
    if len(directions) > 1:
        mixed_reasons = ["criterion_level_mixed_gains_and_regressions"]
        if coarse_direction is not None and (
            _LEFT if coarse_direction == _RIGHT else _RIGHT
        ) in criterion_directions:
            mixed_reasons.append(
                "coarse_verdict_conflicts_with_criterion_evidence"
            )
        mixed_reasons.extend(reasons)
        mixed_reasons.extend(criterion_reasons)
        return _comparison(
            left,
            right,
            relation=OutcomeRelation.NOT_COMPARABLE,
            release_eligible=False,
            reason_codes=_unique(mixed_reasons),
        )

    if directions:
        direction = next(iter(directions))
        if criterion_directions:
            reasons.append(f"{direction}_dominates_aligned_criteria")
        reasons.extend(criterion_reasons)
        return _comparison(
            left,
            right,
            relation=(
                OutcomeRelation.LEFT_BETTER
                if direction == _LEFT
                else OutcomeRelation.RIGHT_BETTER
            ),
            release_eligible=release_eligible,
            reason_codes=_unique(reasons),
        )

    if left.final_verdict == OutcomeVerdict.PASS:
        relation = OutcomeRelation.BOTH_VALID
        reason = "both_satisfy_same_outcome_contract"
    elif left.final_verdict == OutcomeVerdict.FAIL:
        relation = OutcomeRelation.BOTH_INVALID
        reason = "both_fail_same_outcome_contract"
    else:
        relation = OutcomeRelation.EQUIVALENT_PARTIAL
        reason = "both_partially_satisfy_same_outcome_contract"

    left_p0 = sum(
        item.severity == ViolationSeverity.P0
        for item in left.machine.violations
    )
    right_p0 = sum(
        item.severity == ViolationSeverity.P0
        for item in right.machine.violations
    )
    reasons = [reason]
    if left_p0 != right_p0:
        reasons.append(
            "left_has_fewer_deterministic_p0_violations"
            if left_p0 < right_p0
            else "right_has_fewer_deterministic_p0_violations"
        )

    return _comparison(
        left,
        right,
        relation=relation,
        release_eligible=release_eligible,
        reason_codes=tuple(reasons),
    )
