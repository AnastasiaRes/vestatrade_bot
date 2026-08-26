from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

import app.evaluation_v2.reporting as reporting
from app.evaluation_v2.compiler import compile_outcome_contract
from app.evaluation_v2.contracts import (
    CriterionAssessment,
    CriterionStatus,
    DialogueTranscript,
    EvidenceGrade,
    ReleaseRunEvidence,
    TranscriptTurn,
)
from app.evaluation_v2.deterministic import (
    evaluate_machine_assessment,
    finalize_outcome_evaluation,
)
from app.evaluation_v2.judge import (
    OUTCOME_JUDGE_PROMPT_HASH,
    OUTCOME_JUDGE_PROMPT_VERSION,
    unavailable_judge,
)
from app.evaluation_v2.reporting import (
    ARTIFACT_FILENAMES,
    build_aggregate_summary,
    build_junit_xml,
    write_evaluation_artifacts,
)


def _contract():
    return compile_outcome_contract(
        {
            "id": "R01",
            "block": "report",
            "category": "report",
            "priority": "P0",
            "goal": "Ответить покупателю",
            "pass_criteria": "Доводит задачу до полезного результата.",
            "red_flags": "выдумывает факт",
            "checks": "результат",
            "expects_cards": False,
        },
        dataset_sha256="d" * 64,
    )


def _evaluation(*, empty: bool = False):
    contract = _contract()
    transcript = DialogueTranscript(
        scenario_id="R01",
        source_label="saved-stage6",
        execution_status="valid",
        turns=(
            TranscriptTurn(
                turn_number=1,
                user_text="Мой session_id и телефон не должны попасть в отчёт",
                assistant_text="" if empty else "Полезный ответ",
            ),
        ),
    )
    machine = evaluate_machine_assessment(contract, transcript)
    return contract, finalize_outcome_evaluation(
        contract,
        transcript,
        machine,
        unavailable_judge("offline_no_judge"),
    )


def test_summary_keeps_unavailable_in_explicit_requested_denominator() -> None:
    contract, evaluation = _evaluation()
    summary = build_aggregate_summary(
        (contract,),
        (evaluation,),
        requested_scenario_ids=("R01",),
        source_label="saved-stage6",
    )

    assert summary["denominator"]["requested"] == 1
    assert summary["denominator"]["release_ready"] == 0
    assert summary["denominator"]["decisive_machine_failures"] == 0
    assert summary["denominator"]["provisional_evaluated"] == 0
    assert summary["denominator"]["unavailable_or_missing"] == 1
    assert summary["denominator"]["release_ready_display"] == "0/1"
    assert summary["verdict_counts"]["UNAVAILABLE"] == 1


def test_writer_emits_complete_privacy_bounded_artifact_set(tmp_path: Path) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    manifest = write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"testset": "a" * 64, "transcripts": "b" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="1" * 40,
        requested_scenario_ids=("R01",),
    )

    assert {item.name for item in output.iterdir()} == set(ARTIFACT_FILENAMES)
    assert manifest["requested_scenarios"] == 1
    assert manifest["release_ready_scenarios"] == 0
    assert manifest["provisional_evaluated_scenarios"] == 0
    assert manifest["unavailable_or_missing_scenarios"] == 1
    assert manifest["non_unavailable_scenarios"] == 0
    assert "not_release_coverage" in manifest["evaluated_scenarios_semantics"]
    report_text = (output / "report.md").read_text(encoding="utf-8")
    assert "Definitive" not in report_text
    assert "Release-ready evidence" in report_text
    assert "scope: **undeclared**" in report_text
    combined = "\n".join(
        item.read_text(encoding="utf-8")
        for item in output.iterdir()
        if item.suffix in {".json", ".jsonl", ".md", ".xml"}
    )
    assert "Мой session_id" not in combined
    assert "user_text" not in combined
    assert "assistant_text" not in combined


def test_junit_never_skips_and_unavailable_is_error() -> None:
    contract, evaluation = _evaluation()
    root = ElementTree.fromstring(build_junit_xml((contract,), (evaluation,)))

    assert root.attrib == {
        "name": "outcome-evaluation-v2",
        "tests": "1",
        "failures": "0",
        "errors": "1",
        "skipped": "0",
        "time": "0",
    }
    assert root.find("./testcase/error") is not None


def test_definite_machine_failure_is_junit_failure_not_skip() -> None:
    contract, evaluation = _evaluation(empty=True)
    root = ElementTree.fromstring(build_junit_xml((contract,), (evaluation,)))

    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "0"
    assert root.attrib["skipped"] == "0"


def test_machine_failure_and_provisional_result_are_named_separately() -> None:
    contract, evaluation = _evaluation(empty=True)
    summary = build_aggregate_summary((contract,), (evaluation,))

    assert summary["denominator"]["release_ready"] == 0
    assert summary["denominator"]["decisive_machine_failures"] == 1
    assert summary["denominator"]["provisional_evaluated"] == 1
    assert summary["denominator"]["unavailable_or_missing"] == 0


def test_writer_refuses_nonempty_destination(tmp_path: Path) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    output.mkdir()
    (output / "existing.txt").write_text("owned", encoding="utf-8")

    with pytest.raises(ValueError, match="must not already exist"):
        write_evaluation_artifacts(
            output,
            contracts=(contract,),
            evaluations=(evaluation,),
            source_label="saved-stage6",
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )
    assert (output / "existing.txt").read_text(encoding="utf-8") == "owned"


def test_writer_refuses_even_empty_existing_destination(tmp_path: Path) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    output.mkdir()

    with pytest.raises(ValueError, match="must not already exist"):
        write_evaluation_artifacts(
            output,
            contracts=(contract,),
            evaluations=(evaluation,),
            source_label="saved-stage6",
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )


def test_declared_full_suite_exposes_partial_scope_and_junit_denominator(
    tmp_path: Path,
) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    manifest = write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"testset": "a" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="unknown",
        requested_scenario_ids=("R01",),
        total_scenario_ids=("R01", "R02"),
    )

    assert manifest["total_contract_scenarios"] == 2
    assert manifest["selected_scenario_ids"] == ["R01"]
    assert manifest["partial_run"] is True
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["scope"] == {
        "evaluation_complete": True,
        "partial_run": True,
        "selection_partial": True,
        "scope_declared": True,
        "selected_scenario_ids": ["R01"],
        "total_contract_scenarios": 2,
    }
    junit = ElementTree.parse(output / "junit.xml").getroot()
    assert junit.attrib["tests"] == "2"
    assert junit.attrib["errors"] == "2"
    assert junit.find("./testcase[@name='R02']/error[@type='NOT_SELECTED']") is not None
    assert "**1/2** scenarios selected (partial selection)" in (
        output / "report.md"
    ).read_text(encoding="utf-8")


def test_full_selection_with_missing_record_is_explicitly_incomplete(
    tmp_path: Path,
) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    manifest = write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"testset": "a" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="unknown",
        requested_scenario_ids=("R01", "R02"),
        total_scenario_ids=("R01", "R02"),
    )

    assert manifest["selection_partial"] is False
    assert manifest["evaluation_complete"] is False
    assert manifest["partial_run"] is True
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "full selection" in report
    assert "**1/2** evaluation records (incomplete)" in report
    junit = ElementTree.parse(output / "junit.xml").getroot()
    assert junit.attrib["tests"] == "2"
    assert junit.attrib["errors"] == "2"


def test_artifact_publication_is_transactional_and_manifest_is_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract, evaluation = _evaluation()
    original = reporting._atomic_write
    attempted: list[str] = []

    def failing_write(path: Path, payload: bytes) -> None:
        attempted.append(path.name)
        if path.name == "outcome_evaluations.jsonl":
            raise OSError("injected publication failure")
        original(path, payload)

    monkeypatch.setattr(reporting, "_atomic_write", failing_write)
    output = tmp_path / "report"
    with pytest.raises(OSError, match="injected publication failure"):
        write_evaluation_artifacts(
            output,
            contracts=(contract,),
            evaluations=(evaluation,),
            source_label="saved-stage6",
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )

    assert not output.exists()
    assert "manifest.json" not in attempted
    assert list(tmp_path.glob(".report.publishing-*")) == []

    attempted.clear()

    def recording_write(path: Path, payload: bytes) -> None:
        attempted.append(path.name)
        original(path, payload)

    monkeypatch.setattr(reporting, "_atomic_write", recording_write)
    write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"testset": "a" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="unknown",
    )
    assert attempted[-1] == "manifest.json"


def test_manifest_contains_hashes_not_input_paths_or_keys(tmp_path: Path) -> None:
    contract, evaluation = _evaluation()
    output = tmp_path / "report"
    write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"catalog": "c" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="unknown",
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["input_hashes"] == {"catalog": "c" * 64}
    assert "OPENROUTER_API_KEY" not in json.dumps(manifest)


def test_writer_rejects_pii_leaking_from_supplied_evaluator_reason(
    tmp_path: Path,
) -> None:
    contract, evaluation = _evaluation()
    unsafe_judge = evaluation.judge.model_copy(
        update={"reason_codes": ("contact +7 999 123-45-67",)}
    )
    unsafe_evaluation = evaluation.model_copy(update={"judge": unsafe_judge})

    with pytest.raises(ValueError, match="unsafe machine-readable code|secret-like"):
        write_evaluation_artifacts(
            tmp_path / "report",
            contracts=(contract,),
            evaluations=(unsafe_evaluation,),
            source_label="saved-stage6",
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )


def test_writer_structurally_redacts_extensible_evidence_and_rationale(
    tmp_path: Path,
) -> None:
    contract, evaluation = _evaluation(empty=True)
    violation = evaluation.machine.violations[0].model_copy(
        update={
            "product_sku": "ORDER-Anastasia-Red-Square",
            "evidence": {
                "raw_dialogue": "Customer Anastasia lives at Red Square 1",
                "shown": "private-value",
                "catalog": "https://example.test/item?token=supersecret",
                "repeat_count": 3,
            },
        }
    )
    machine = evaluation.machine.model_copy(update={"violations": (violation,)})
    judge = evaluation.judge.model_copy(
        update={
            "criterion_assessments": (
                CriterionAssessment(
                    criterion_id="criterion_safe_id",
                    status=CriterionStatus.UNKNOWN,
                    rationale="Customer Anastasia lives at Red Square 1",
                ),
            )
        }
    )
    evaluation = evaluation.model_copy(update={"machine": machine, "judge": judge})
    output = tmp_path / "report"

    write_evaluation_artifacts(
        output,
        contracts=(contract,),
        evaluations=(evaluation,),
        source_label="saved-stage6",
        input_hashes={"testset": "a" * 64},
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=None,
        bot_model="qwen/qwen3-vl-8b-instruct",
        git_hash="unknown",
    )

    serialized = (output / "outcome_evaluations.jsonl").read_text(encoding="utf-8")
    assert "Anastasia" not in serialized
    assert "Red Square" not in serialized
    assert "supersecret" not in serialized
    assert "ORDER-Anastasia" not in serialized
    assert '"rationale"' not in serialized
    assert '"shown"' not in serialized
    assert '"catalog"' not in serialized
    assert '"repeat_count":3' in serialized
    assert '"evidence_redacted":true' in serialized


def test_writer_rejects_release_claim_for_unapproved_contract(
    tmp_path: Path,
) -> None:
    contract, evaluation = _evaluation(empty=True)
    complete_run = ReleaseRunEvidence(
        source_manifest_verified=True,
        transcript_digest_bound=True,
        testset_revision_verified=True,
        catalog_revision_verified=True,
        bot_model_verified=True,
        live_run_verified=True,
        full_suite_selected=True,
        normalization_registry_approved=True,
    )
    forged = evaluation.model_copy(
        update={
            "release_eligible": True,
            "evidence_grade": EvidenceGrade.RELEASE_READY,
            "release_run_evidence": complete_run,
        }
    )

    with pytest.raises(ValueError, match="unapproved contract"):
        write_evaluation_artifacts(
            tmp_path / "report",
            contracts=(contract,),
            evaluations=(forged,),
            source_label="saved-stage6",
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )


@pytest.mark.parametrize("source_label", ["bad label", "bad\nlabel", "**markdown**"])
def test_writer_rejects_unsafe_source_label(
    tmp_path: Path,
    source_label: str,
) -> None:
    contract, evaluation = _evaluation()
    unsafe_evaluation = evaluation.model_copy(update={"source_label": source_label})

    with pytest.raises(ValueError, match="safe logical label"):
        write_evaluation_artifacts(
            tmp_path / "report",
            contracts=(contract,),
            evaluations=(unsafe_evaluation,),
            source_label=source_label,
            input_hashes={"testset": "a" * 64},
            prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
            prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
            judge_model=None,
            bot_model="qwen/qwen3-vl-8b-instruct",
            git_hash="unknown",
        )
