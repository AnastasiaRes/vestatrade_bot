from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.evaluation_v2.compare import compare_outcome_evaluations
from app.evaluation_v2.compiler import compile_outcome_contract
from app.evaluation_v2.contracts import (
    CatalogTruthProduct,
    CriterionImportance,
    CriterionAssessment,
    CriterionEvaluationMode,
    CriterionPolarity,
    CriterionSource,
    CriterionStatus,
    DialogueTranscript,
    EvaluationStatus,
    ExecutionFailureActor,
    JudgeAssessment,
    OutcomeContract,
    OutcomeDisposition,
    OutcomeEvaluation,
    OutcomeRelation,
    OutcomeVerdict,
    TranscriptProduct,
    TranscriptTurn,
    ViolationSeverity,
    ReleaseRunEvidence,
    TerminalState,
)
from app.evaluation_v2.deterministic import (
    evaluate_machine_assessment,
    finalize_outcome_evaluation,
)
from app.evaluation_v2.evidence import build_evidence_binding
from app.evaluation_v2.judge import unavailable_judge
from app.evaluation_v2.normalization import (
    ContractNormalization,
    CriterionNormalization,
    apply_reviewed_normalization,
)


def _contract(*, expects_cards: bool = False) -> OutcomeContract:
    return compile_outcome_contract(
        {
            "id": "TEST-01",
            "block": "Outcome evaluation",
            "category": "Catalog consultation",
            "priority": "P0",
            "difficulty": "2",
            "buyer_mode": "normal",
            "goal": "Подобрать подходящий товар и довести диалог до полезного итога",
            "pass_criteria": "Учитывает ограничения. Даёт проверяемый следующий шаг.",
            "red_flags": "Выдумывает товар; игнорирует прямой вопрос",
            "checks": "goal · grounding",
            "expects_cards": expects_cards,
        },
        dataset_sha256="a" * 64,
        dataset_id="unit-test",
    )


def _transcript(
    *turns: TranscriptTurn,
    source_label: str = "candidate",
    execution_status: str = "valid",
    execution_error_code: str | None = None,
) -> DialogueTranscript:
    return DialogueTranscript(
        scenario_id="TEST-01",
        source_label=source_label,
        execution_status=execution_status,
        execution_error_code=execution_error_code,
        turns=turns,
    )


def _turn(
    number: int = 1,
    *,
    answer: str = "Подобрал проверяемый вариант.",
    products: tuple[TranscriptProduct, ...] = (),
    error_code: str | None = None,
) -> TranscriptTurn:
    return TranscriptTurn(
        turn_number=number,
        user_text="Нужен товар",
        assistant_text=answer,
        products=products,
        error_code=error_code,
    )


def _truth() -> CatalogTruthProduct:
    return CatalogTruthProduct(
        sku="SKU-1",
        name="Проверяемый товар",
        price=100.0,
        currency="RUB",
        stock_status="in_stock",
        stock_qty=3,
        url="https://catalog.test/sku-1",
    )


def _approved_contract() -> OutcomeContract:
    source = _contract()
    normalization = ContractNormalization(
        normalization_id="approved-test-normalization",
        contract_id=source.contract_id,
        dataset_sha256=source.source_revision.dataset_sha256,
        scenario_sha256=source.source_revision.scenario_sha256,
        user_goal="Получить проверяемый результат подбора",
        test_objective="Проверить достижение цели без выдуманных фактов",
        disposition=OutcomeDisposition.FULFILL,
        expected_terminal_states=(TerminalState.RESOLVED,),
        reviewer="unit-test-reviewer",
        criteria=tuple(
            CriterionNormalization(
                criterion_id=item.criterion_id,
                importance=(
                    CriterionImportance.MINIMUM_GOAL
                    if item.source == CriterionSource.GOAL
                    else CriterionImportance.REQUIRED
                ),
                failure_effect=item.failure_effect,
                temporal_scope=item.temporal_scope,
            )
            for item in source.criteria
        ),
    )
    return apply_reviewed_normalization(
        source,
        normalization,
        registry_sha256="f" * 64,
        approval_verified=True,
    )


def _judge(
    contract: OutcomeContract,
    *,
    transcript: DialogueTranscript | None = None,
    verdict: OutcomeVerdict = OutcomeVerdict.PASS,
    status_for: Callable[[object], CriterionStatus] | None = None,
) -> JudgeAssessment:
    def default_status(criterion: object) -> CriterionStatus:
        polarity = getattr(criterion, "polarity")
        source = getattr(criterion, "source")
        if source == CriterionSource.GOAL and verdict == OutcomeVerdict.FAIL:
            return CriterionStatus.NOT_SATISFIED
        if source == CriterionSource.GOAL and verdict == OutcomeVerdict.PARTIAL:
            return CriterionStatus.PARTIALLY_SATISFIED
        return (
            CriterionStatus.SATISFIED
            if polarity == CriterionPolarity.REQUIRED
            else CriterionStatus.NOT_TRIGGERED
        )

    choose_status = status_for or default_status
    semantic_criteria = tuple(
        item
        for item in contract.criteria
        if item.evaluation_mode == CriterionEvaluationMode.INDEPENDENT_JUDGE
    )
    assessments = tuple(
        CriterionAssessment(
            criterion_id=item.criterion_id,
            status=choose_status(item),
            evidence_turn_numbers=(1,),
            rationale="Краткое проверяемое основание.",
            confidence=0.9,
        )
        for item in semantic_criteria
    )
    triggered = tuple(
        item.criterion_id
        for item, assessment in zip(semantic_criteria, assessments, strict=True)
        if assessment.status == CriterionStatus.TRIGGERED
    )
    return JudgeAssessment(
        status=EvaluationStatus.EVALUATED,
        proposed_verdict=verdict,
        criterion_assessments=assessments,
        detected_red_flag_ids=triggered,
        confidence=0.9,
        model="independent/test-judge",
        evidence_binding=build_evidence_binding(
            contract,
            transcript or _transcript(_turn()),
        ),
    )


@pytest.mark.parametrize(
    ("shown_product", "expected_code"),
    [
        (TranscriptProduct(sku="NOT-IN-CATALOG"), "UNKNOWN_CARD_SKU"),
        (
            TranscriptProduct(
                sku="SKU-1",
                name="Другой товар",
                price=100.0,
                currency="RUB",
                stock_status="in_stock",
                stock_qty=3,
                url="https://catalog.test/sku-1",
            ),
            "CARD_NAME_MISMATCH",
        ),
        (
            TranscriptProduct(
                sku="SKU-1",
                price=101.0,
                currency="RUB",
                stock_status="in_stock",
                url="https://catalog.test/sku-1",
            ),
            "CARD_PRICE_MISMATCH",
        ),
        (
            TranscriptProduct(
                sku="SKU-1",
                price=100.0,
                currency="RUB",
                stock_status="out_of_stock",
                url="https://catalog.test/sku-1",
            ),
            "CARD_STOCK_MISMATCH",
        ),
        (
            TranscriptProduct(
                sku="SKU-1",
                name="Проверяемый товар",
                price=100.0,
                currency="RUB",
                stock_status="in_stock",
                stock_qty=99,
                url="https://catalog.test/sku-1",
            ),
            "CARD_STOCK_QTY_MISMATCH",
        ),
        (
            TranscriptProduct(
                sku="SKU-1",
                price=100.0,
                currency="RUB",
                stock_status="in_stock",
                url="https://catalog.test/not-the-product",
            ),
            "CARD_URL_MISMATCH",
        ),
    ],
)
def test_card_grounding_mismatches_are_p0(
    shown_product: TranscriptProduct,
    expected_code: str,
) -> None:
    contract = _contract()
    transcript = _transcript(_turn(products=(shown_product,)))

    result = evaluate_machine_assessment(contract, transcript, (_truth(),))
    matched = [item for item in result.violations if item.code == expected_code]

    assert len(matched) == 1
    assert matched[0].severity == ViolationSeverity.P0
    assert matched[0].verdict_cap == OutcomeVerdict.FAIL


def test_absent_saved_card_fields_are_evaluator_limitations_not_failures() -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(products=(TranscriptProduct(sku="SKU-1"),)),
    )

    result = evaluate_machine_assessment(contract, transcript, (_truth(),))

    assert result.violations == ()
    assert set(result.limitation_reason_codes) == {
        "hard_gate_coverage_incomplete",
        "transcript_card_name_unavailable",
        "transcript_card_price_unavailable",
        "transcript_card_currency_unavailable",
        "transcript_card_stock_unavailable",
        "transcript_card_stock_qty_unavailable",
        "transcript_card_url_unavailable",
    }


def test_proven_mismatch_and_missing_same_field_on_another_card_do_not_crash() -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(
            products=(
                TranscriptProduct(sku="SKU-1", name="Неверное имя"),
                TranscriptProduct(sku="SKU-2"),
            )
        )
    )
    truth = (
        _truth(),
        CatalogTruthProduct(sku="SKU-2", name="Второй товар"),
    )

    result = evaluate_machine_assessment(contract, transcript, truth)

    assert "CARD_NAME_MISMATCH" in {item.code for item in result.violations}
    assert "CARD_NAME_MISMATCH" in result.checked_rule_codes
    assert "transcript_card_name_unavailable" in result.limitation_reason_codes


@pytest.mark.parametrize(
    ("transcript", "expected_code"),
    [
        (_transcript(), "EMPTY_ANSWER"),
        (
            _transcript(
                _turn(error_code="timeout"),
                execution_status="invalid",
                execution_error_code="transport_failed",
            ),
            "TRANSPORT_ERROR",
        ),
    ],
)
def test_empty_or_transport_failure_is_a_deterministic_fail(
    transcript: DialogueTranscript,
    expected_code: str,
) -> None:
    contract = _contract()
    machine = evaluate_machine_assessment(contract, transcript)
    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        unavailable_judge(),
    )

    assert expected_code in {item.code for item in machine.violations}
    assert evaluation.final_verdict == OutcomeVerdict.FAIL
    assert expected_code in evaluation.gate_blocking_reason_codes


def test_three_identical_responses_are_detected_without_becoming_a_false_p0() -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(1, answer="Один и тот же ответ"),
        _turn(2, answer="  один И ТОТ же   ответ "),
        _turn(3, answer="Один и тот же ответ"),
    )

    machine = evaluate_machine_assessment(contract, transcript)
    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        unavailable_judge(),
    )

    repeats = [
        item for item in machine.violations if item.code == "TRIPLE_IDENTICAL_RESPONSE"
    ]
    assert len(repeats) == 1
    assert repeats[0].turn_numbers == (1, 2, 3)
    assert repeats[0].verdict_cap == OutcomeVerdict.PARTIAL
    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE


def test_clean_machine_evidence_never_becomes_an_automatic_pass() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    machine = evaluate_machine_assessment(contract, transcript)

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        unavailable_judge(),
    )

    assert machine.violations == ()
    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert "independent_judge_unavailable" in evaluation.reason_codes


def test_p0_machine_gate_overrides_judge_pass() -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(products=(TranscriptProduct(sku="UNKNOWN-SKU"),)),
    )
    machine = evaluate_machine_assessment(contract, transcript, (_truth(),))

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
    )

    assert evaluation.judge.proposed_verdict == OutcomeVerdict.PASS
    assert evaluation.final_verdict == OutcomeVerdict.FAIL
    assert "UNKNOWN_CARD_SKU" in evaluation.gate_blocking_reason_codes


def test_triggered_developer_red_flag_overrides_judge_pass() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    red_flag_id = next(
        item.criterion_id
        for item in contract.criteria
        if item.source == CriterionSource.RED_FLAG
    )

    judge = _judge(
        contract,
        transcript=transcript,
        status_for=lambda criterion: (
            CriterionStatus.TRIGGERED
            if getattr(criterion, "criterion_id") == red_flag_id
            else (
                CriterionStatus.SATISFIED
                if getattr(criterion, "polarity") == CriterionPolarity.REQUIRED
                else CriterionStatus.NOT_TRIGGERED
            )
        ),
    )
    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        judge,
    )

    assert evaluation.final_verdict == OutcomeVerdict.FAIL
    assert "developer_red_flag_triggered" in evaluation.gate_blocking_reason_codes


def test_unknown_required_criterion_makes_outcome_unavailable() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    goal_id = next(
        item.criterion_id
        for item in contract.criteria
        if item.source == CriterionSource.GOAL
    )
    judge = _judge(
        contract,
        transcript=transcript,
        status_for=lambda criterion: (
            CriterionStatus.UNKNOWN
            if getattr(criterion, "criterion_id") == goal_id
            else (
                CriterionStatus.SATISFIED
                if getattr(criterion, "polarity") == CriterionPolarity.REQUIRED
                else CriterionStatus.NOT_TRIGGERED
            )
        ),
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        judge,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert "required_criterion_evaluation_unknown" in (
        evaluation.gate_blocking_reason_codes
    )


def test_unconditional_goal_not_applicable_fails_closed() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    goal_id = next(
        item.criterion_id
        for item in contract.criteria
        if item.source == CriterionSource.GOAL
    )
    judge = _judge(
        contract,
        transcript=transcript,
        status_for=lambda criterion: (
            CriterionStatus.NOT_APPLICABLE
            if getattr(criterion, "criterion_id") == goal_id
            else (
                CriterionStatus.SATISFIED
                if getattr(criterion, "polarity") == CriterionPolarity.REQUIRED
                else CriterionStatus.NOT_TRIGGERED
            )
        ),
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        judge,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert "required_criterion_not_applicable_invalid" in (
        evaluation.gate_blocking_reason_codes
    )


def test_zero_confidence_judge_cannot_produce_a_pass() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    judge = _judge(contract, transcript=transcript).model_copy(
        update={
            "confidence": 0.0,
            "criterion_assessments": tuple(
                item.model_copy(update={"confidence": 0.0})
                for item in _judge(
                    contract,
                    transcript=transcript,
                ).criterion_assessments
            ),
        }
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        judge,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert "judge_confidence_below_release_threshold" in (
        evaluation.gate_blocking_reason_codes
    )


def test_low_confidence_negative_judge_cannot_force_a_false_fail() -> None:
    contract = _contract()
    transcript = _transcript(_turn())
    judge = _judge(
        contract,
        transcript=transcript,
        verdict=OutcomeVerdict.FAIL,
    ).model_copy(
        update={
            "confidence": 0.0,
            "criterion_assessments": tuple(
                item.model_copy(update={"confidence": 0.0})
                for item in _judge(
                    contract,
                    transcript=transcript,
                    verdict=OutcomeVerdict.FAIL,
                ).criterion_assessments
            ),
        }
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        judge,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert "judge_confidence_below_release_threshold" in (
        evaluation.gate_blocking_reason_codes
    )


def test_machine_assessment_bound_to_another_transcript_has_no_verdict_effect() -> None:
    contract = _contract()
    good_transcript = _transcript(_turn())
    wrong_machine = evaluate_machine_assessment(contract, _transcript())

    evaluation = finalize_outcome_evaluation(
        contract,
        good_transcript,
        wrong_machine,
        _judge(contract, transcript=good_transcript),
    )

    assert evaluation.final_verdict == OutcomeVerdict.PASS
    assert evaluation.release_eligible is False
    assert "machine_evidence_binding_invalid" in evaluation.gate_blocking_reason_codes


def test_incomplete_hard_gate_coverage_is_never_release_eligible() -> None:
    contract = _approved_contract()
    transcript = _transcript(_turn())
    machine = evaluate_machine_assessment(contract, transcript)
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
        release_run_evidence=run_evidence,
    )

    assert machine.unchecked_hard_gate_codes
    assert evaluation.final_verdict == OutcomeVerdict.PASS
    assert evaluation.release_eligible is False
    assert "hard_gate_coverage_incomplete" in evaluation.gate_blocking_reason_codes


def test_release_eligibility_requires_complete_bound_provenance_and_all_gates() -> None:
    contract = _approved_contract()
    transcript = _transcript(_turn())
    machine = evaluate_machine_assessment(
        contract,
        transcript,
        supplied_checked_rule_codes=contract.hard_gate_codes,
    )
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
        release_run_evidence=run_evidence,
    )

    assert machine.unchecked_hard_gate_codes == ()
    assert machine.status.value == "complete"
    assert evaluation.final_verdict == OutcomeVerdict.PASS
    assert evaluation.release_eligible is True

    forged = evaluation.model_dump(mode="python")
    forged["release_run_evidence"] = ReleaseRunEvidence()
    with pytest.raises(ValidationError, match="complete run provenance"):
        OutcomeEvaluation.model_validate(forged)


def test_unknown_semantic_result_is_never_release_eligible() -> None:
    contract = _approved_contract()
    transcript = _transcript(_turn())
    goal_id = next(
        item.criterion_id
        for item in contract.criteria
        if item.source == CriterionSource.GOAL
    )
    machine = evaluate_machine_assessment(
        contract,
        transcript,
        supplied_checked_rule_codes=contract.hard_gate_codes,
    )
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )
    judge = _judge(
        contract,
        transcript=transcript,
        status_for=lambda criterion: (
            CriterionStatus.UNKNOWN
            if getattr(criterion, "criterion_id") == goal_id
            else CriterionStatus.SATISFIED
            if getattr(criterion, "polarity") == CriterionPolarity.REQUIRED
            else CriterionStatus.NOT_TRIGGERED
        ),
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        judge,
        release_run_evidence=run_evidence,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert evaluation.release_eligible is False


def test_release_evaluation_model_rejects_verdicts_contradicting_evidence() -> None:
    contract = _approved_contract()
    transcript = _transcript()
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )
    failed = finalize_outcome_evaluation(
        contract,
        transcript,
        evaluate_machine_assessment(contract, transcript),
        unavailable_judge(contract=contract, transcript=transcript),
        release_run_evidence=run_evidence,
    )
    assert failed.release_eligible is True
    assert failed.final_verdict == OutcomeVerdict.FAIL

    forged_pass = failed.model_dump(mode="python")
    forged_pass["final_verdict"] = OutcomeVerdict.PASS
    with pytest.raises(ValidationError, match="deterministic failure"):
        OutcomeEvaluation.model_validate(forged_pass)

    forged_unavailable = failed.model_dump(mode="python")
    forged_unavailable["final_verdict"] = OutcomeVerdict.UNAVAILABLE
    with pytest.raises(ValidationError, match="cannot be unavailable"):
        OutcomeEvaluation.model_validate(forged_unavailable)


def test_release_evaluation_model_rejects_partial_or_low_confidence_channels() -> None:
    contract = _approved_contract()
    transcript = _transcript(_turn())
    machine = evaluate_machine_assessment(
        contract,
        transcript,
        supplied_checked_rule_codes=contract.hard_gate_codes,
    )
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )
    valid = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
        release_run_evidence=run_evidence,
    )

    partial_machine = valid.model_dump(mode="python")
    partial_machine["machine"] = machine.model_copy(
        update={"status": "partial"}
    )
    with pytest.raises(ValidationError, match="complete machine evidence"):
        OutcomeEvaluation.model_validate(partial_machine)

    weak_judge = valid.model_dump(mode="python")
    weak_judge["judge"] = valid.judge.model_copy(update={"confidence": 0.0})
    with pytest.raises(ValidationError, match="confident judge evidence"):
        OutcomeEvaluation.model_validate(weak_judge)


def test_required_human_criterion_blocks_release_until_a_channel_exists() -> None:
    source = _approved_contract()
    contract = OutcomeContract.model_validate(
        {
            **source.model_dump(mode="python"),
            "criteria": tuple(
                item.model_copy(
                    update={"evaluation_mode": CriterionEvaluationMode.HUMAN}
                )
                if item.source == CriterionSource.GOAL
                else item
                for item in source.criteria
            ),
        }
    )
    transcript = _transcript(_turn())
    machine = evaluate_machine_assessment(
        contract,
        transcript,
        supplied_checked_rule_codes=contract.hard_gate_codes,
    )
    run_evidence = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )

    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
        release_run_evidence=run_evidence,
    )

    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
    assert evaluation.release_eligible is False
    assert "human_criterion_assessment_unavailable" in (
        evaluation.gate_blocking_reason_codes
    )


def test_buyer_protocol_failure_is_unavailable_not_a_bot_transport_failure() -> None:
    contract = _contract(expects_cards=True)
    transcript = _transcript(
        _turn(),
        execution_status="buyer_protocol_error",
        execution_error_code="BUYER_STOPPED_EARLY",
    ).model_copy(
        update={"execution_failure_actor": ExecutionFailureActor.BUYER}
    )

    machine = evaluate_machine_assessment(contract, transcript)
    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        unavailable_judge(contract=contract, transcript=transcript),
    )

    assert "TRANSPORT_ERROR" not in {item.code for item in machine.violations}
    assert "EXPECTED_CARDS_MISSING" not in {
        item.code for item in machine.violations
    }
    assert "buyer_execution_incomplete" in machine.limitation_reason_codes
    assert evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE


@pytest.mark.parametrize(
    ("status", "actor", "expected_limitation"),
    [
        (
            "buyer_protocol_error",
            ExecutionFailureActor.BUYER,
            "buyer_turn_error",
        ),
        ("unknown", ExecutionFailureActor.UNKNOWN, "turn_error_actor_unverified"),
    ],
)
def test_non_bot_turn_errors_do_not_abort_or_become_transport_failures(
    status: str,
    actor: ExecutionFailureActor,
    expected_limitation: str,
) -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(error_code="UNTRUSTED_FAILURE"),
        execution_status=status,
        execution_error_code="UNTRUSTED_FAILURE",
    ).model_copy(update={"execution_failure_actor": actor})

    machine = evaluate_machine_assessment(contract, transcript)

    assert "TRANSPORT_ERROR" not in {item.code for item in machine.violations}
    assert expected_limitation in machine.limitation_reason_codes


def test_contract_and_transcript_scenario_must_match() -> None:
    contract = _contract()
    transcript = _transcript(_turn()).model_copy(update={"scenario_id": "OTHER"})

    with pytest.raises(ValueError, match="scenario"):
        evaluate_machine_assessment(contract, transcript)


def test_non_finite_card_price_is_rejected_at_the_typed_boundary() -> None:
    with pytest.raises(ValidationError):
        TranscriptProduct(sku="SKU-1", price=float("nan"))


def _outcome(
    contract: OutcomeContract,
    *,
    source_label: str,
    verdict: OutcomeVerdict,
) -> OutcomeEvaluation:
    transcript = _transcript(_turn(), source_label=source_label)
    machine = evaluate_machine_assessment(contract, transcript)
    judge = (
        unavailable_judge()
        if verdict == OutcomeVerdict.UNAVAILABLE
        else _judge(contract, transcript=transcript, verdict=verdict)
    )
    return finalize_outcome_evaluation(contract, transcript, machine, judge)


@pytest.mark.parametrize(
    ("left_verdict", "right_verdict", "expected_relation"),
    [
        (OutcomeVerdict.PASS, OutcomeVerdict.PASS, OutcomeRelation.BOTH_VALID),
        (OutcomeVerdict.PASS, OutcomeVerdict.FAIL, OutcomeRelation.LEFT_BETTER),
        (OutcomeVerdict.FAIL, OutcomeVerdict.PASS, OutcomeRelation.RIGHT_BETTER),
        (OutcomeVerdict.FAIL, OutcomeVerdict.FAIL, OutcomeRelation.BOTH_INVALID),
        (
            OutcomeVerdict.UNAVAILABLE,
            OutcomeVerdict.PASS,
            OutcomeRelation.NOT_COMPARABLE,
        ),
    ],
)
def test_comparator_describes_both_valid_improvement_regression_and_invalidity(
    left_verdict: OutcomeVerdict,
    right_verdict: OutcomeVerdict,
    expected_relation: OutcomeRelation,
) -> None:
    contract = _contract()
    left = _outcome(contract, source_label="left", verdict=left_verdict)
    right = _outcome(contract, source_label="right", verdict=right_verdict)

    comparison = compare_outcome_evaluations(left, right)

    assert comparison.relation == expected_relation


def test_evaluation_and_comparison_do_not_mutate_any_input() -> None:
    contract = _contract()
    transcript = _transcript(
        _turn(products=(TranscriptProduct(sku="SKU-1"),)),
    )
    catalog = (_truth(),)
    contract_before = contract.model_dump(mode="json")
    transcript_before = transcript.model_dump(mode="json")
    catalog_before = [item.model_dump(mode="json") for item in catalog]

    machine = evaluate_machine_assessment(contract, transcript, catalog)
    evaluation = finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        _judge(contract, transcript=transcript),
    )
    peer = _outcome(contract, source_label="peer", verdict=OutcomeVerdict.PASS)
    left_before = evaluation.model_dump(mode="json")
    right_before = peer.model_dump(mode="json")
    compare_outcome_evaluations(evaluation, peer)

    assert contract.model_dump(mode="json") == contract_before
    assert transcript.model_dump(mode="json") == transcript_before
    assert [item.model_dump(mode="json") for item in catalog] == catalog_before
    assert evaluation.model_dump(mode="json") == left_before
    assert peer.model_dump(mode="json") == right_before
    with pytest.raises(ValidationError):
        transcript.source_label = "mutated"  # type: ignore[misc]
