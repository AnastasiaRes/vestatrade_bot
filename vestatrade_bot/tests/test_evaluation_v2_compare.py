from __future__ import annotations

import pytest

from app.evaluation_v2.compare import compare_outcome_evaluations
from app.evaluation_v2.contracts import (
    CriterionAssessment,
    CriterionStatus,
    EvidenceBinding,
    EvidenceGrade,
    EvaluationStatus,
    JudgeAssessment,
    MachineAssessment,
    MachineAssessmentStatus,
    OutcomeEvaluation,
    OutcomeRelation,
    OutcomeVerdict,
)


REQUIRED_A = "criterion_required_a"
REQUIRED_B = "criterion_required_b"
PROHIBITED = "criterion_prohibited"


def _assessment(criterion_id: str, status: CriterionStatus) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=criterion_id,
        status=status,
        evidence_turn_numbers=(1,),
        rationale="Проверяемое основание.",
        confidence=0.9,
    )


def _evaluation(
    label: str,
    verdict: OutcomeVerdict,
    assessments: tuple[CriterionAssessment, ...],
    *,
    judge_status: EvaluationStatus = EvaluationStatus.EVALUATED,
) -> OutcomeEvaluation:
    binding = EvidenceBinding(
        contract_id="outcome_contract_compare",
        scenario_id="CMP-01",
        contract_sha256="a" * 64,
        transcript_sha256="b" * 64,
    )
    judge = (
        JudgeAssessment(
            status=EvaluationStatus.EVALUATED,
            proposed_verdict=verdict,
            criterion_assessments=assessments,
            confidence=0.9,
            model="independent/test-judge",
        )
        if judge_status == EvaluationStatus.EVALUATED
        else JudgeAssessment(
            status=judge_status,
            reason_codes=("judge_unavailable_for_test",),
        )
    )
    return OutcomeEvaluation(
        contract_id="outcome_contract_compare",
        scenario_id="CMP-01",
        source_label=label,
        evidence_grade=EvidenceGrade.PROVISIONAL,
        release_eligible=False,
        machine=MachineAssessment(status=MachineAssessmentStatus.COMPLETE),
        judge=judge,
        final_verdict=verdict,
        evidence_binding=binding,
    )


def test_required_regression_prevents_coarse_verdict_from_declaring_side_better() -> None:
    left = _evaluation(
        "baseline",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_A, CriterionStatus.SATISFIED),),
    )
    right = _evaluation(
        "candidate",
        OutcomeVerdict.PASS,
        (_assessment(REQUIRED_A, CriterionStatus.PARTIALLY_SATISFIED),),
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.NOT_COMPARABLE
    assert "coarse_verdict_conflicts_with_criterion_evidence" in comparison.reason_codes
    assert "right_regresses_required_criterion" in comparison.reason_codes


def test_new_prohibited_trigger_prevents_coarse_verdict_from_declaring_side_better() -> None:
    left = _evaluation(
        "baseline",
        OutcomeVerdict.PARTIAL,
        (_assessment(PROHIBITED, CriterionStatus.NOT_TRIGGERED),),
    )
    right = _evaluation(
        "candidate",
        OutcomeVerdict.PASS,
        (_assessment(PROHIBITED, CriterionStatus.TRIGGERED),),
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.NOT_COMPARABLE
    assert "right_triggers_prohibited_criterion" in comparison.reason_codes


def test_mixed_criterion_gains_and_regressions_are_not_comparable() -> None:
    left = _evaluation(
        "left",
        OutcomeVerdict.PARTIAL,
        (
            _assessment(REQUIRED_A, CriterionStatus.SATISFIED),
            _assessment(REQUIRED_B, CriterionStatus.PARTIALLY_SATISFIED),
        ),
    )
    right = _evaluation(
        "right",
        OutcomeVerdict.PARTIAL,
        (
            _assessment(REQUIRED_A, CriterionStatus.PARTIALLY_SATISFIED),
            _assessment(REQUIRED_B, CriterionStatus.SATISFIED),
        ),
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.NOT_COMPARABLE
    assert "criterion_level_mixed_gains_and_regressions" in comparison.reason_codes
    assert "left_regresses_required_criterion" in comparison.reason_codes
    assert "right_regresses_required_criterion" in comparison.reason_codes


def test_aligned_criterion_improvement_breaks_equal_coarse_verdict() -> None:
    left = _evaluation(
        "left",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_A, CriterionStatus.PARTIALLY_SATISFIED),),
    )
    right = _evaluation(
        "right",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_A, CriterionStatus.SATISFIED),),
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.RIGHT_BETTER
    assert "right_dominates_aligned_criteria" in comparison.reason_codes


def test_criterion_alignment_uses_ids_not_assessment_order() -> None:
    left = _evaluation(
        "left",
        OutcomeVerdict.PARTIAL,
        (
            _assessment(REQUIRED_A, CriterionStatus.SATISFIED),
            _assessment(PROHIBITED, CriterionStatus.NOT_TRIGGERED),
        ),
    )
    right = _evaluation(
        "right",
        OutcomeVerdict.PARTIAL,
        (
            _assessment(PROHIBITED, CriterionStatus.NOT_TRIGGERED),
            _assessment(REQUIRED_A, CriterionStatus.SATISFIED),
        ),
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.EQUIVALENT_PARTIAL


def test_missing_or_unknown_criterion_evidence_is_not_comparable() -> None:
    complete = _evaluation(
        "complete",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_A, CriterionStatus.SATISFIED),),
    )
    different_coverage = _evaluation(
        "different",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_B, CriterionStatus.SATISFIED),),
    )
    unknown = _evaluation(
        "unknown",
        OutcomeVerdict.PARTIAL,
        (_assessment(REQUIRED_A, CriterionStatus.UNKNOWN),),
    )

    missing_result = compare_outcome_evaluations(complete, different_coverage)
    unknown_result = compare_outcome_evaluations(complete, unknown)

    assert missing_result.relation == OutcomeRelation.NOT_COMPARABLE
    assert "criterion_coverage_mismatch" in missing_result.reason_codes
    assert unknown_result.relation == OutcomeRelation.NOT_COMPARABLE
    assert "criterion_assessment_unknown" in unknown_result.reason_codes


@pytest.mark.parametrize(
    "status",
    [EvaluationStatus.UNAVAILABLE, EvaluationStatus.REJECTED],
)
def test_unavailable_judge_evidence_remains_not_comparable(
    status: EvaluationStatus,
) -> None:
    evaluated = _evaluation(
        "evaluated",
        OutcomeVerdict.FAIL,
        (_assessment(REQUIRED_A, CriterionStatus.NOT_SATISFIED),),
    )
    unavailable = _evaluation(
        "machine-only",
        OutcomeVerdict.FAIL,
        (),
        judge_status=status,
    )

    comparison = compare_outcome_evaluations(evaluated, unavailable)

    assert comparison.relation == OutcomeRelation.NOT_COMPARABLE
    assert comparison.reason_codes == ("criterion_assessment_unavailable",)


def test_different_normalized_contract_digests_are_not_comparable() -> None:
    left = _evaluation(
        "left",
        OutcomeVerdict.PASS,
        (_assessment(REQUIRED_A, CriterionStatus.SATISFIED),),
    )
    right = _evaluation(
        "right",
        OutcomeVerdict.PASS,
        (_assessment(REQUIRED_A, CriterionStatus.SATISFIED),),
    )
    right = right.model_copy(
        update={
            "evidence_binding": right.evidence_binding.model_copy(
                update={"contract_sha256": "c" * 64}
            )
        }
    )

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == OutcomeRelation.NOT_COMPARABLE
    assert comparison.release_eligible is False
    assert comparison.reason_codes == ("normalized_contract_revision_mismatch",)
