"""Pure deterministic gates and monotonic verdict composition."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable

from .contracts import (
    CatalogTruthProduct,
    CriterionEvaluationMode,
    CriterionPolarity,
    CriterionSource,
    CriterionStatus,
    DialogueTranscript,
    EvidenceGrade,
    EvaluationStatus,
    ExecutionFailureActor,
    FailureEffect,
    JudgeAssessment,
    MachineAssessment,
    MachineAssessmentStatus,
    MachineViolation,
    OutcomeContract,
    OutcomeEvaluation,
    OutcomeVerdict,
    ReleaseRunEvidence,
    ViolationSeverity,
)
from .evidence import build_evidence_binding


def _normalized_sku(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def _normalized_url(value: str | None) -> str:
    return str(value or "").strip().rstrip(".,;/")


def _violation(
    code: str,
    *,
    severity: ViolationSeverity,
    verdict_cap: OutcomeVerdict,
    turn_numbers: Iterable[int] = (),
    product_sku: str | None = None,
    reason_code: str,
    evidence: dict | None = None,
) -> MachineViolation:
    turns = tuple(sorted(set(int(item) for item in turn_numbers)))
    material = json.dumps(
        {
            "code": code,
            "turns": turns,
            "sku": product_sku,
            "reason": reason_code,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return MachineViolation(
        violation_id=f"violation_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}",
        code=code,
        severity=severity,
        verdict_cap=verdict_cap,
        turn_numbers=turns,
        product_sku=product_sku,
        reason_code=reason_code,
        evidence=evidence or {},
    )


def evaluate_machine_assessment(
    contract: OutcomeContract,
    transcript: DialogueTranscript,
    catalog_truth: Iterable[CatalogTruthProduct] = (),
    *,
    supplied_violations: Iterable[MachineViolation] = (),
    supplied_checked_rule_codes: Iterable[str] = (),
    supplied_limitation_reason_codes: Iterable[str] = (),
    supplied_outcome_blocking_reason_codes: Iterable[str] = (),
) -> MachineAssessment:
    """Return only facts established without interpreting natural language.

    A saved harness may contain only SKU strings rather than complete public
    card payloads. In that case absent price/stock/url fields are an evaluator
    limitation, not evidence that the bot invented or omitted those values.
    """

    binding = build_evidence_binding(contract, transcript)
    truth_items = tuple(catalog_truth)
    truth_groups: dict[str, list[CatalogTruthProduct]] = {}
    for item in truth_items:
        truth_groups.setdefault(_normalized_sku(item.sku), []).append(item)
    truth = {
        sku: items[0]
        for sku, items in truth_groups.items()
        if len(items) == 1
    }
    ambiguous_skus = {sku for sku, items in truth_groups.items() if len(items) > 1}

    violations: list[MachineViolation] = list(supplied_violations)
    limitations: list[str] = list(supplied_limitation_reason_codes)
    outcome_blockers: list[str] = list(supplied_outcome_blocking_reason_codes)
    checked: list[str] = [
        "PUBLIC_CARD_LIMIT_EXCEEDED",
        "TRIPLE_IDENTICAL_RESPONSE",
    ]
    checked.extend(str(item) for item in supplied_checked_rule_codes)
    checked.extend(item.code for item in violations)
    if truth_items:
        checked.append("UNKNOWN_CARD_SKU")

    execution_actor = transcript.execution_failure_actor
    execution_complete = bool(
        transcript.execution_status == "valid"
        and execution_actor == ExecutionFailureActor.NONE
        and not transcript.execution_error_code
    )
    if execution_actor in {
        ExecutionFailureActor.BOT,
        ExecutionFailureActor.TRANSPORT,
    }:
        checked.append("TRANSPORT_ERROR")
        violations.append(
            _violation(
                "TRANSPORT_ERROR",
                severity=ViolationSeverity.P0,
                verdict_cap=OutcomeVerdict.FAIL,
                reason_code="dialogue_execution_invalid",
                evidence={
                    "execution_status": transcript.execution_status,
                    "failure_actor": execution_actor.value,
                    "error_code": transcript.execution_error_code,
                },
            )
        )
    elif execution_complete:
        checked.append("TRANSPORT_ERROR")
    elif execution_actor in {
        ExecutionFailureActor.BUYER,
        ExecutionFailureActor.HARNESS,
    }:
        limitations.append(f"{execution_actor.value}_execution_incomplete")
        outcome_blockers.append("dialogue_execution_incomplete_outside_bot")
    else:
        limitations.append("execution_failure_actor_unverified")
        outcome_blockers.append("dialogue_execution_provenance_unavailable")

    if not transcript.turns and execution_actor not in {
        ExecutionFailureActor.BUYER,
        ExecutionFailureActor.HARNESS,
    }:
        checked.append("EMPTY_ANSWER")
        violations.append(
            _violation(
                "EMPTY_ANSWER",
                severity=ViolationSeverity.P0,
                verdict_cap=OutcomeVerdict.FAIL,
                reason_code="dialogue_contains_no_customer_response_turns",
            )
        )

    if transcript.turns:
        checked.append("EMPTY_ANSWER")

    shown_cards = 0
    unavailable_catalog_rules: set[str] = set()
    catalog_field_rule_codes = {
        "CARD_NAME_MISMATCH",
        "CARD_PRICE_MISMATCH",
        "CARD_CURRENCY_MISMATCH",
        "CARD_STOCK_MISMATCH",
        "CARD_STOCK_QTY_MISMATCH",
        "CARD_URL_MISMATCH",
    }
    response_signatures: Counter[str] = Counter()
    for turn in transcript.turns:
        if turn.error_code:
            if execution_actor in {
                ExecutionFailureActor.BUYER,
                ExecutionFailureActor.HARNESS,
            }:
                limitations.append(f"{execution_actor.value}_turn_error")
                outcome_blockers.append("dialogue_turn_error_outside_bot")
            elif execution_actor == ExecutionFailureActor.UNKNOWN:
                limitations.append("turn_error_actor_unverified")
                outcome_blockers.append("dialogue_turn_error_provenance_unavailable")
            else:
                checked.append("TRANSPORT_ERROR")
                violations.append(
                    _violation(
                        "TRANSPORT_ERROR",
                        severity=ViolationSeverity.P0,
                        verdict_cap=OutcomeVerdict.FAIL,
                        turn_numbers=(turn.turn_number,),
                        reason_code="transport_error_on_customer_turn",
                        evidence={"error_code": turn.error_code},
                    )
                )
        if not turn.assistant_text.strip():
            violations.append(
                _violation(
                    "EMPTY_ANSWER",
                    severity=ViolationSeverity.P0,
                    verdict_cap=OutcomeVerdict.FAIL,
                    turn_numbers=(turn.turn_number,),
                    reason_code="customer_turn_has_no_answer",
                )
            )
        else:
            signature = " ".join(turn.assistant_text.casefold().split())
            response_signatures[signature] += 1

        shown_cards += len(turn.products)
        if len(turn.products) > 5:
            violations.append(
                _violation(
                    "PUBLIC_CARD_LIMIT_EXCEEDED",
                    severity=ViolationSeverity.P1,
                    verdict_cap=OutcomeVerdict.PARTIAL,
                    turn_numbers=(turn.turn_number,),
                    reason_code="more_than_five_cards_on_one_turn",
                    evidence={"card_count": len(turn.products)},
                )
            )
        for product in turn.products:
            sku = _normalized_sku(product.sku)
            if sku in ambiguous_skus:
                limitations.append("catalog_truth_sku_ambiguous")
                unavailable_catalog_rules.update(catalog_field_rule_codes)
                continue
            source = truth.get(sku)
            if source is None:
                if truth_items:
                    violations.append(
                        _violation(
                            "UNKNOWN_CARD_SKU",
                            severity=ViolationSeverity.P0,
                            verdict_cap=OutcomeVerdict.FAIL,
                            turn_numbers=(turn.turn_number,),
                            product_sku=product.sku,
                            reason_code="card_sku_absent_from_catalog_truth",
                        )
                    )
                else:
                    limitations.append("catalog_truth_unavailable")
                    unavailable_catalog_rules.add("UNKNOWN_CARD_SKU")
                    unavailable_catalog_rules.update(catalog_field_rule_codes)
                continue

            field_pairs = {
                "name": (product.name, source.name, "CARD_NAME_MISMATCH"),
                "price": (product.price, source.price, "CARD_PRICE_MISMATCH"),
                "currency": (
                    product.currency,
                    source.currency,
                    "CARD_CURRENCY_MISMATCH",
                ),
                "stock": (
                    product.stock_status,
                    source.stock_status,
                    "CARD_STOCK_MISMATCH",
                ),
                "stock_qty": (
                    product.stock_qty,
                    source.stock_qty,
                    "CARD_STOCK_QTY_MISMATCH",
                ),
                "url": (product.url, source.url, "CARD_URL_MISMATCH"),
            }
            for field_name, (shown, expected, rule_code) in field_pairs.items():
                if shown is None:
                    limitations.append(f"transcript_card_{field_name}_unavailable")
                    unavailable_catalog_rules.add(rule_code)
                    continue
                if expected is None:
                    limitations.append(f"catalog_truth_{field_name}_unavailable")
                    unavailable_catalog_rules.add(rule_code)
                    continue
                checked.append(rule_code)
                if field_name == "price":
                    differs = abs(float(shown) - float(expected)) > 0.01
                elif field_name == "stock_qty":
                    differs = int(shown) != int(expected)
                elif field_name == "url":
                    differs = _normalized_url(str(shown)) != _normalized_url(
                        str(expected)
                    )
                else:
                    differs = str(shown).casefold() != str(expected).casefold()
                if differs:
                    violations.append(
                        _violation(
                            rule_code,
                            severity=ViolationSeverity.P0,
                            verdict_cap=OutcomeVerdict.FAIL,
                            turn_numbers=(turn.turn_number,),
                            product_sku=product.sku,
                            reason_code=(
                                f"card_{field_name}_differs_from_catalog_truth"
                            ),
                            evidence={"shown": shown, "catalog": expected},
                        )
                    )

    if shown_cards == 0:
        # These gates quantify over public cards. With no card presentation,
        # card-field fabrication is vacuously absent; whether cards should have
        # been shown is a separate reviewed outcome criterion.
        checked.extend(catalog_field_rule_codes)
        if truth_items:
            checked.append("UNKNOWN_CARD_SKU")

    if contract.expects_cards and shown_cards == 0:
        expected_cards_criterion = next(
            (
                item
                for item in contract.criteria
                if "EXPECTED_CARDS_MISSING" in item.deterministic_rule_codes
            ),
            None,
        )
        if not execution_complete:
            limitations.append("expected_cards_not_assessed_on_incomplete_dialogue")
        elif (
            expected_cards_criterion is None
            or expected_cards_criterion.conditional_semantics_unresolved
        ):
            limitations.append("expected_cards_applicability_unreviewed")
        elif expected_cards_criterion.conditional:
            limitations.append("expected_cards_condition_activation_unavailable")
        else:
            checked.append("EXPECTED_CARDS_MISSING")
            violations.append(
                _violation(
                    "EXPECTED_CARDS_MISSING",
                    severity=ViolationSeverity.P1,
                    verdict_cap=(
                        OutcomeVerdict.FAIL
                        if expected_cards_criterion.failure_effect
                        in {FailureEffect.FAIL, FailureEffect.CRITICAL_FAIL}
                        else OutcomeVerdict.PARTIAL
                    ),
                    reason_code="reviewed_contract_requires_product_cards",
                )
            )
    for signature, count in response_signatures.items():
        if signature and count >= 3:
            turns = tuple(
                item.turn_number
                for item in transcript.turns
                if " ".join(item.assistant_text.casefold().split()) == signature
            )
            violations.append(
                _violation(
                    "TRIPLE_IDENTICAL_RESPONSE",
                    severity=ViolationSeverity.P1,
                    verdict_cap=OutcomeVerdict.PARTIAL,
                    turn_numbers=turns,
                    reason_code="same_answer_returned_three_or_more_times",
                    evidence={"repeat_count": count},
                )
            )

    # Coverage is currently represented per rule, while catalogue evidence is
    # gathered per card.  If one card proves a mismatch and another lacks that
    # field, retain the proven violation as checked and keep the limitation to
    # signal that coverage across all cards was partial.
    checked_codes = (set(checked) - unavailable_catalog_rules) | {
        item.code for item in violations
    }
    unchecked_hard_gates = tuple(
        sorted(set(contract.hard_gate_codes) - checked_codes)
    )
    if unchecked_hard_gates:
        limitations.append("hard_gate_coverage_incomplete")
    unique = {item.violation_id: item for item in violations}
    limitation_codes = tuple(dict.fromkeys(limitations))
    return MachineAssessment(
        status=(
            MachineAssessmentStatus.PARTIAL
            if limitation_codes
            else MachineAssessmentStatus.COMPLETE
        ),
        checked_rule_codes=tuple(sorted(checked_codes)),
        unchecked_hard_gate_codes=unchecked_hard_gates,
        violations=tuple(unique[key] for key in sorted(unique)),
        limitation_reason_codes=limitation_codes,
        outcome_blocking_reason_codes=tuple(dict.fromkeys(outcome_blockers)),
        evidence_binding=binding,
    )


def evaluate_machine_violations(
    contract: OutcomeContract,
    transcript: DialogueTranscript,
    catalog_truth: Iterable[CatalogTruthProduct] = (),
    *,
    supplied_violations: Iterable[MachineViolation] = (),
) -> tuple[MachineViolation, ...]:
    """Compatibility helper returning only the violation collection."""

    return evaluate_machine_assessment(
        contract,
        transcript,
        catalog_truth,
        supplied_violations=supplied_violations,
    ).violations


_VERDICT_RANK = {
    OutcomeVerdict.FAIL: 0,
    OutcomeVerdict.PARTIAL: 1,
    OutcomeVerdict.PASS: 2,
}


def _cap_verdict(verdict: OutcomeVerdict, cap: OutcomeVerdict) -> OutcomeVerdict:
    if verdict == OutcomeVerdict.UNAVAILABLE:
        return OutcomeVerdict.FAIL if cap == OutcomeVerdict.FAIL else verdict
    return verdict if _VERDICT_RANK[verdict] <= _VERDICT_RANK[cap] else cap


def finalize_outcome_evaluation(
    contract: OutcomeContract,
    transcript: DialogueTranscript,
    machine: MachineAssessment | Iterable[MachineViolation],
    judge: JudgeAssessment,
    *,
    release_run_evidence: ReleaseRunEvidence | None = None,
) -> OutcomeEvaluation:
    """Merge evidence monotonically; a model can never clear a machine gate."""

    expected_binding = build_evidence_binding(contract, transcript)
    run_evidence = release_run_evidence or ReleaseRunEvidence(
        reason_codes=("release_run_provenance_not_supplied",)
    )

    if isinstance(machine, MachineAssessment):
        machine_assessment = machine
    else:
        legacy_violations = tuple(machine)
        machine_assessment = MachineAssessment(
            status=MachineAssessmentStatus.PARTIAL,
            checked_rule_codes=tuple(
                dict.fromkeys(item.code for item in legacy_violations)
            ),
            violations=legacy_violations,
            limitation_reason_codes=("legacy_machine_assessment_adapter",),
        )

    reason_codes: list[str] = []
    blockers: list[str] = []
    if not contract.release_ready:
        reason_codes.append("contract_not_approved_for_release_evaluation")
    reason_codes.extend(machine_assessment.limitation_reason_codes)
    reason_codes.extend(run_evidence.reason_codes)
    machine_binding_valid = machine_assessment.evidence_binding == expected_binding
    if not machine_binding_valid:
        blockers.append("machine_evidence_binding_invalid")
        reason_codes.append("machine_evidence_not_bound_to_contract_transcript")
    judge_binding_valid = bool(
        judge.status == EvaluationStatus.EVALUATED
        and judge.evidence_binding == expected_binding
    )
    judge_confident = bool(
        judge.status == EvaluationStatus.EVALUATED
        and judge.confidence >= 0.6
        and all(item.confidence >= 0.5 for item in judge.criterion_assessments)
    )
    if judge.status != EvaluationStatus.EVALUATED:
        reason_codes.append("independent_judge_unavailable")
    elif not judge_binding_valid:
        blockers.append("judge_evidence_binding_invalid")
    if judge.status == EvaluationStatus.EVALUATED and not judge_confident:
        blockers.append("judge_confidence_below_release_threshold")

    judge_criteria = {
        item.criterion_id: item
        for item in contract.criteria
        if item.evaluation_mode == CriterionEvaluationMode.INDEPENDENT_JUDGE
    }
    assessment_by_id = {
        item.criterion_id: item for item in judge.criterion_assessments
    }
    judge_coverage_valid = bool(
        judge.status == EvaluationStatus.EVALUATED
        and set(assessment_by_id) == set(judge_criteria)
    )
    if judge.status == EvaluationStatus.EVALUATED and not judge_coverage_valid:
        blockers.append("judge_criterion_coverage_invalid")
    human_criteria = tuple(
        item
        for item in contract.criteria
        if item.evaluation_mode == CriterionEvaluationMode.HUMAN
    )
    if human_criteria:
        blockers.append("human_criterion_assessment_unavailable")
        reason_codes.append("human_evaluation_channel_not_implemented")

    judge_usable = bool(
        judge_binding_valid
        and judge_confident
        and judge_coverage_valid
        and not human_criteria
    )
    verdict = judge.proposed_verdict if judge_usable else OutcomeVerdict.UNAVAILABLE
    if judge_usable:
        for criterion_id, assessment in assessment_by_id.items():
            criterion = judge_criteria.get(criterion_id)
            if criterion is None:
                continue
            if criterion.polarity == CriterionPolarity.PROHIBITED:
                if assessment.status == CriterionStatus.TRIGGERED:
                    verdict = OutcomeVerdict.FAIL
                    blockers.append("developer_red_flag_triggered")
                elif assessment.status == CriterionStatus.NOT_APPLICABLE:
                    if not criterion.conditional:
                        verdict = OutcomeVerdict.UNAVAILABLE
                        blockers.append("unconditional_red_flag_marked_not_applicable")
                elif assessment.status == CriterionStatus.UNKNOWN:
                    verdict = OutcomeVerdict.UNAVAILABLE
                    blockers.append("red_flag_evaluation_unknown")
                continue
            if assessment.status == CriterionStatus.NOT_APPLICABLE:
                if (
                    not criterion.conditional
                    or criterion.conditional_semantics_unresolved
                    or criterion.source == CriterionSource.GOAL
                ):
                    verdict = OutcomeVerdict.UNAVAILABLE
                    blockers.append("required_criterion_not_applicable_invalid")
                continue
            if assessment.status == CriterionStatus.UNKNOWN:
                verdict = OutcomeVerdict.UNAVAILABLE
                blockers.append("required_criterion_evaluation_unknown")
                continue
            if assessment.status in {
                CriterionStatus.NOT_SATISFIED,
                CriterionStatus.PARTIALLY_SATISFIED,
            }:
                cap = (
                    OutcomeVerdict.PARTIAL
                    if assessment.status == CriterionStatus.PARTIALLY_SATISFIED
                    else OutcomeVerdict.FAIL
                    if criterion.failure_effect
                    in {FailureEffect.FAIL, FailureEffect.CRITICAL_FAIL}
                    else OutcomeVerdict.PARTIAL
                )
                verdict = _cap_verdict(verdict, cap)
                reason_codes.append("required_criterion_not_fully_satisfied")

    if machine_binding_valid and machine_assessment.outcome_blocking_reason_codes:
        verdict = OutcomeVerdict.UNAVAILABLE
        blockers.extend(machine_assessment.outcome_blocking_reason_codes)

    deterministic_failure = False
    if machine_binding_valid:
        for violation in machine_assessment.violations:
            reason_codes.append(violation.reason_code)
            if violation.severity == ViolationSeverity.P0:
                verdict = OutcomeVerdict.FAIL
                blockers.append(violation.code)
                deterministic_failure = True
            else:
                verdict = _cap_verdict(verdict, violation.verdict_cap)
                if violation.verdict_cap == OutcomeVerdict.FAIL:
                    blockers.append(violation.code)
                    deterministic_failure = True

    if machine_assessment.unchecked_hard_gate_codes:
        blockers.append("hard_gate_coverage_incomplete")
        reason_codes.extend(
            f"unchecked_hard_gate:{code}"
            for code in machine_assessment.unchecked_hard_gate_codes
        )
    if not run_evidence.complete:
        blockers.append("release_run_provenance_incomplete")

    decisive_failure = bool(
        deterministic_failure
        or (judge_usable and verdict == OutcomeVerdict.FAIL)
    )
    complete_success_evidence = bool(
        machine_assessment.status == MachineAssessmentStatus.COMPLETE
        and not machine_assessment.unchecked_hard_gate_codes
        and judge_usable
        and not machine_assessment.outcome_blocking_reason_codes
        and verdict != OutcomeVerdict.UNAVAILABLE
    )
    release_eligible = bool(
        contract.release_ready
        and run_evidence.complete
        and machine_binding_valid
        and verdict != OutcomeVerdict.UNAVAILABLE
        and (decisive_failure or complete_success_evidence)
    )
    return OutcomeEvaluation(
        contract_id=contract.contract_id,
        scenario_id=contract.scenario_id,
        source_label=transcript.source_label,
        evidence_grade=(
            EvidenceGrade.RELEASE_READY
            if release_eligible
            else EvidenceGrade.PROVISIONAL
        ),
        release_eligible=release_eligible,
        machine=machine_assessment,
        judge=judge,
        final_verdict=verdict,
        gate_blocking_reason_codes=tuple(dict.fromkeys(blockers)),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        evidence_binding=expected_binding,
        release_run_evidence=run_evidence,
    )
