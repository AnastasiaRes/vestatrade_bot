#!/usr/bin/env python3
"""Evaluate saved dialogues against versioned developer outcome contracts.

The command never calls the bot and never changes customer-visible state. LLM
judging is separately opt-in; deterministic rescoring works fully offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.config import get_settings
from app.evaluation_v2.adapters import (
    adapt_catalog_products,
    load_dialogue_transcripts_jsonl_bytes,
)
from app.evaluation_v2.compiler import compile_outcome_contracts
from app.evaluation_v2.contracts import (
    ReleaseRunEvidence,
    ViolationSeverity,
)
from app.evaluation_v2.deterministic import (
    evaluate_machine_assessment,
    finalize_outcome_evaluation,
)
from app.evaluation_v2.judge import (
    MODEL_LINEAGE_REGISTRY_VERSION,
    OUTCOME_JUDGE_PROMPT_HASH,
    OUTCOME_JUDGE_PROMPT_VERSION,
    OutcomeJudge,
    judge_model_is_independent,
    unavailable_judge,
)
from app.evaluation_v2.normalization import (
    APPROVED_NORMALIZATION_REGISTRY_SHA256,
    ContractNormalization,
    apply_normalization_registry,
)
from app.evaluation_v2.provenance import (
    SourceRunProvenanceValidation,
    canonical_catalog_sha256,
    validate_source_run_provenance,
)
from app.evaluation_v2.reporting import write_evaluation_artifacts
from app.feed_loader import FeedLoader
from app.openrouter_client import OpenRouterClient


DEFAULT_TESTSET = PROJECT_ROOT / "data/live_dialogue_feed_testset_2026-08-25.json"
# The 100 scenarios focus on the showcase products, but the live bot was run
# against the complete 14k catalogue. Grounding must use the same catalogue
# universe or valid cards outside the showcase would be falsely marked unknown.
DEFAULT_CATALOG = PROJECT_ROOT / "data/products_all.xml"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    return _load_json_object_bytes(path.read_bytes(), source_name=str(path))


def _load_json_object_bytes(
    raw: bytes,
    *,
    source_name: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"expected UTF-8 JSON in {source_name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {source_name}")
    return payload


def _source_manifest_path(
    transcript_path: Path,
    explicit: str | None,
) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    adjacent = transcript_path.parent / "manifest.json"
    return adjacent if adjacent.is_file() else None


def _validate_source_manifest(
    manifest: dict[str, Any],
    *,
    testset_sha256: str,
    catalog_sha256: str,
    transcript_scenario_ids: set[str],
    full_suite_scenario_ids: tuple[str, ...] = (),
    require_full_suite: bool = False,
) -> SourceRunProvenanceValidation:
    result = validate_source_run_provenance(
        manifest,
        expected_testset_sha256=testset_sha256,
        expected_catalog_sha256=catalog_sha256,
        requested_scenario_ids=tuple(sorted(transcript_scenario_ids)),
        full_suite_scenario_ids=full_suite_scenario_ids,
        require_full_suite=require_full_suite,
        require_exact_scenario_set=True,
    )
    if not result.accepted:
        raise ValueError(
            "source run provenance rejected: " + ",".join(result.reason_codes)
        )
    return result


def _git_state() -> tuple[str, bool | None, str | None]:
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        value = commit_result.stdout.strip().casefold()
        if not (
            7 <= len(value) <= 64
            and all(char in "0123456789abcdef" for char in value)
        ):
            return "unknown", None, None
        status_result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                "app",
                "scripts/run_outcome_evaluation_v2.py",
                "data/live_dialogue_feed_testset_2026-08-25.json",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        status = status_result.stdout
        return (
            value,
            bool(status),
            hashlib.sha256(status.encode("utf-8")).hexdigest(),
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown", None, None


def _runtime_source_hashes() -> dict[str, str]:
    paths = sorted((PROJECT_ROOT / "app/evaluation_v2").glob("*.py"))
    paths.extend(
        [
            Path(__file__).resolve(),
            PROJECT_ROOT / "app/config.py",
            PROJECT_ROOT / "app/feed_loader.py",
            PROJECT_ROOT / "app/models.py",
            PROJECT_ROOT / "app/openrouter_client.py",
            PROJECT_ROOT / "app/pii.py",
            PROJECT_ROOT / "requirements.txt",
        ]
    )
    result: dict[str, str] = {}
    for path in paths:
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        label = "runtime_" + relative.replace("/", "_").replace(".", "_")
        result[label] = _sha256_file(path)
    return result


def _selected_ids(
    available: tuple[str, ...],
    only: str | None,
    limit: int | None,
) -> tuple[str, ...]:
    selected = available
    if only:
        requested = tuple(
            dict.fromkeys(item.strip() for item in only.split(",") if item.strip())
        )
        unknown = set(requested) - set(available)
        if unknown:
            raise ValueError(f"unknown scenario ids: {sorted(unknown)}")
        selected = tuple(item for item in available if item in set(requested))
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive")
        selected = selected[:limit]
    if not selected:
        raise ValueError("no scenarios selected")
    return selected


def _build_judge(
    *,
    requested: bool,
    judge_model_arg: str | None,
    bot_model_arg: str | None,
    source_bot_models: tuple[str, ...] = (),
) -> tuple[OutcomeJudge | None, str | None, str | None]:
    settings = get_settings()
    bot_model = source_bot_models[0] if source_bot_models else None
    if not requested:
        if bot_model_arg and (
            not source_bot_models or bot_model_arg.strip() not in source_bot_models
        ):
            raise RuntimeError(
                "--bot-model may only confirm a model pinned by the source manifest"
            )
        return None, None, bot_model
    if os.getenv("RUN_LIVE_LLM_TESTS") != "1" or os.getenv(
        "RUN_OUTCOME_JUDGE_EVALS"
    ) != "1":
        raise RuntimeError(
            "paid outcome judge requires RUN_LIVE_LLM_TESTS=1 and "
            "RUN_OUTCOME_JUDGE_EVALS=1"
        )
    judge_model = (judge_model_arg or os.getenv("OUTCOME_JUDGE_MODEL") or "").strip()
    if not judge_model:
        raise RuntimeError("an explicit --judge-model or OUTCOME_JUDGE_MODEL is required")
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for the outcome judge")
    if not source_bot_models:
        raise RuntimeError(
            "outcome judge requires bot-model provenance from a verified source run"
        )
    if bot_model_arg and bot_model_arg.strip() not in source_bot_models:
        raise RuntimeError(
            "--bot-model may only confirm a model pinned by the source manifest"
        )
    if not all(
        judge_model_is_independent(source_model, judge_model)
        for source_model in source_bot_models
    ):
        raise RuntimeError(
            "judge model must use a different foundation family than every bot model"
        )
    openrouter_settings = settings.model_copy(update={"llm_provider": "openrouter"})
    return (
        OutcomeJudge(
            OpenRouterClient(openrouter_settings),
            judge_model=judge_model,
            bot_model=source_bot_models[0],
        ),
        judge_model,
        bot_model,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    testset_path = Path(args.testset).expanduser().resolve()
    transcript_path = Path(args.transcripts).expanduser().resolve()
    catalog_path = Path(args.catalog).expanduser().resolve()
    capability_path = (
        Path(args.capability_contract).expanduser().resolve()
        if args.capability_contract
        else None
    )
    normalization_path = (
        Path(args.normalization_registry).expanduser().resolve()
        if args.normalization_registry
        else None
    )
    source_manifest_path = _source_manifest_path(
        transcript_path,
        args.source_manifest,
    )
    for path in (testset_path, transcript_path, catalog_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    # Every parsed input is read once. Digests below are calculated from these
    # exact immutable buffers, preventing a concurrent file replacement from
    # making the manifest describe bytes other than those evaluated.
    testset_bytes = testset_path.read_bytes()
    transcript_bytes = transcript_path.read_bytes()
    catalog_bytes = catalog_path.read_bytes()
    capability_bytes = capability_path.read_bytes() if capability_path else None
    normalization_bytes = (
        normalization_path.read_bytes() if normalization_path else None
    )
    source_manifest_bytes = (
        source_manifest_path.read_bytes() if source_manifest_path else None
    )
    runtime_hashes_before = _runtime_source_hashes()

    testset = _load_json_object_bytes(testset_bytes, source_name=str(testset_path))
    all_contracts = compile_outcome_contracts(testset)
    if normalization_path is not None:
        assert normalization_bytes is not None
        raw_registry = json.loads(normalization_bytes.decode("utf-8"))
        if isinstance(raw_registry, dict):
            raw_registry = raw_registry.get("normalizations")
        if not isinstance(raw_registry, list):
            raise ValueError("normalization registry must be an array")
        normalizations = tuple(
            ContractNormalization.model_validate(item) for item in raw_registry
        )
        normalization_sha256 = _sha256_bytes(normalization_bytes)
        all_contracts = apply_normalization_registry(
            all_contracts,
            normalizations,
            require_full_coverage=not args.allow_partial_normalization,
            registry_sha256=normalization_sha256,
            approval_verified=(
                normalization_sha256
                in APPROVED_NORMALIZATION_REGISTRY_SHA256
            ),
        )
    contract_by_id = {item.scenario_id: item for item in all_contracts}
    available_ids = tuple(item.scenario_id for item in all_contracts)
    requested_ids = _selected_ids(available_ids, args.only, args.limit)
    requested_set = set(requested_ids)
    contracts = tuple(contract_by_id[item] for item in requested_ids)

    loaded_transcripts = load_dialogue_transcripts_jsonl_bytes(
        transcript_bytes,
        source_label=args.source_label,
        source_name=str(transcript_path),
    )
    unknown_transcripts = {
        item.scenario_id for item in loaded_transcripts
    } - set(contract_by_id)
    if unknown_transcripts:
        raise ValueError(
            f"transcripts without outcome contracts: {sorted(unknown_transcripts)}"
        )
    transcripts = tuple(
        item for item in loaded_transcripts if item.scenario_id in requested_set
    )
    transcript_by_id = {item.scenario_id: item for item in transcripts}

    catalog_products = FeedLoader().parse_xml(catalog_bytes)
    catalog_truth = adapt_catalog_products(catalog_products)
    catalog_canonical_sha256 = canonical_catalog_sha256(catalog_products)
    transcript_file_sha256 = _sha256_bytes(transcript_bytes)
    source_manifest: dict[str, Any] | None = None
    provenance_validation: SourceRunProvenanceValidation | None = None
    if source_manifest_path is not None:
        assert source_manifest_bytes is not None
        source_manifest = _load_json_object_bytes(
            source_manifest_bytes,
            source_name=str(source_manifest_path),
        )
        provenance_validation = validate_source_run_provenance(
            source_manifest,
            expected_testset_sha256=_sha256_bytes(testset_bytes),
            expected_catalog_sha256=catalog_canonical_sha256,
            expected_transcripts_sha256=transcript_file_sha256,
            requested_scenario_ids=tuple(
                sorted(item.scenario_id for item in loaded_transcripts)
            ),
            full_suite_scenario_ids=available_ids,
            require_full_suite=True,
            require_exact_scenario_set=True,
        )
    provenance_accepted = bool(
        provenance_validation is not None and provenance_validation.accepted
    )
    if not provenance_accepted and not getattr(
        args,
        "allow_unverified_source_provenance",
        False,
    ):
        reasons = (
            provenance_validation.reason_codes
            if provenance_validation is not None
            else ("source_manifest_missing",)
        )
        raise ValueError(
            "saved dialogue provenance is not verified; rerun only for "
            "non-release diagnostics with --allow-unverified-source-provenance: "
            + ",".join(reasons)
        )
    source_provenance = (
        provenance_validation.provenance
        if provenance_accepted and provenance_validation is not None
        else None
    )
    catalog_truth_for_evaluation = catalog_truth if provenance_accepted else ()
    provenance_limitations = (
        ()
        if provenance_accepted
        else (
            "source_run_provenance_unverified",
            *(
                provenance_validation.reason_codes
                if provenance_validation is not None
                else ("source_manifest_missing",)
            ),
        )
    )
    source_bot_models = (
        source_provenance.bot_models_for_independence
        if source_provenance is not None
        else ()
    )
    capability_contract = (
        _load_json_object_bytes(
            capability_bytes,
            source_name=str(capability_path),
        )
        if capability_path is not None and capability_bytes is not None
        else {}
    )
    judge, judge_model, bot_model = _build_judge(
        requested=args.judge,
        judge_model_arg=args.judge_model,
        bot_model_arg=args.bot_model,
        source_bot_models=source_bot_models,
    )

    loaded_transcript_ids = {item.scenario_id for item in loaded_transcripts}
    full_suite_selected = bool(
        set(requested_ids) == set(available_ids)
        and loaded_transcript_ids == set(available_ids)
    )
    release_reason_codes: list[str] = []
    if not provenance_accepted:
        release_reason_codes.append("source_run_provenance_unverified")
    transcript_digest_bound = bool(
        source_provenance is not None
        and source_provenance.transcripts_sha256 == transcript_file_sha256
    )
    if not transcript_digest_bound:
        release_reason_codes.append("source_manifest_does_not_bind_transcript_digest")
    if not full_suite_selected:
        release_reason_codes.append("partial_evaluation_selection")
    normalization_approved = bool(contracts) and all(
        item.release_ready for item in contracts
    )
    if not normalization_approved:
        release_reason_codes.append("normalization_registry_not_approved")
    release_run_evidence = ReleaseRunEvidence(
        source_manifest_verified=provenance_accepted,
        transcript_digest_bound=transcript_digest_bound,
        testset_revision_verified=provenance_accepted,
        catalog_revision_verified=provenance_accepted,
        bot_model_verified=provenance_accepted,
        live_run_verified=provenance_accepted,
        full_suite_selected=full_suite_selected,
        normalization_registry_approved=normalization_approved,
        reason_codes=tuple(release_reason_codes),
    )

    evaluations = []
    for index, contract in enumerate(contracts, start=1):
        transcript = transcript_by_id.get(contract.scenario_id)
        if transcript is None:
            continue
        machine = evaluate_machine_assessment(
            contract,
            transcript,
            catalog_truth_for_evaluation,
            supplied_limitation_reason_codes=provenance_limitations,
        )
        fatal_machine_failure = any(
            item.severity == ViolationSeverity.P0
            for item in machine.violations
        )
        outcome_unassessable = bool(machine.outcome_blocking_reason_codes)
        judgment = (
            judge.evaluate(
                contract,
                transcript,
                catalog_truth=catalog_truth_for_evaluation,
                machine_violations=machine.violations,
                capability_contract=capability_contract,
            )
            if judge is not None
            and not fatal_machine_failure
            and not outcome_unassessable
            else unavailable_judge(
                "judge_skipped_due_fatal_machine_failure"
                if judge is not None and fatal_machine_failure
                else "judge_skipped_due_incomplete_dialogue"
                if judge is not None and outcome_unassessable
                else "judge_not_requested_offline_run"
                ,
                contract=contract,
                transcript=transcript,
            )
        )
        evaluations.append(
            finalize_outcome_evaluation(
                contract,
                transcript,
                machine,
                judgment,
                release_run_evidence=release_run_evidence,
            )
        )
        if args.progress:
            print(
                f"[{index}/{len(contracts)}] {contract.scenario_id}: "
                f"{evaluations[-1].final_verdict.value}"
            )

    input_hashes = {
        "testset": _sha256_bytes(testset_bytes),
        "transcripts": transcript_file_sha256,
        "catalog": _sha256_bytes(catalog_bytes),
        "evaluation_catalog_snapshot": catalog_canonical_sha256,
    }
    runtime_hashes_after = _runtime_source_hashes()
    if runtime_hashes_after != runtime_hashes_before:
        raise RuntimeError("evaluator runtime sources changed during evaluation")
    input_hashes.update(runtime_hashes_before)
    if capability_bytes is not None:
        input_hashes["capability_contract"] = _sha256_bytes(capability_bytes)
    if normalization_bytes is not None:
        input_hashes["normalization_registry"] = _sha256_bytes(
            normalization_bytes
        )
    if source_manifest_bytes is not None:
        input_hashes["source_manifest"] = _sha256_bytes(source_manifest_bytes)
    if source_manifest is not None:
        source_catalog_sha256 = str(
            (source_manifest.get("inputs") or {}).get("catalog_sha256") or ""
        )
        if len(source_catalog_sha256) == 64:
            input_hashes[
                "source_catalog_snapshot_verified"
                if provenance_accepted
                else "source_catalog_snapshot_claimed_unverified"
            ] = source_catalog_sha256
    git_hash, git_dirty, git_status_sha256 = _git_state()
    manifest = write_evaluation_artifacts(
        args.output_dir,
        contracts=contracts,
        evaluations=tuple(evaluations),
        source_label=args.source_label,
        input_hashes=input_hashes,
        prompt_version=OUTCOME_JUDGE_PROMPT_VERSION,
        prompt_hash=OUTCOME_JUDGE_PROMPT_HASH,
        judge_model=judge_model,
        bot_model=bot_model,
        bot_strong_model=(
            source_provenance.bot_strong_model
            if source_provenance is not None
            else None
        ),
        judge_lineage_registry_version=MODEL_LINEAGE_REGISTRY_VERSION,
        git_hash=git_hash,
        git_dirty=git_dirty,
        git_status_sha256=git_status_sha256,
        requested_scenario_ids=requested_ids,
        total_scenario_ids=available_ids,
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testset", default=str(DEFAULT_TESTSET))
    parser.add_argument("--transcripts", required=True)
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-label", default="candidate")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--judge-model")
    parser.add_argument("--bot-model")
    parser.add_argument("--capability-contract")
    parser.add_argument("--source-manifest")
    parser.add_argument("--normalization-registry")
    parser.add_argument("--allow-partial-normalization", action="store_true")
    parser.add_argument(
        "--allow-unverified-source-provenance",
        action="store_true",
        help="continue as explicitly non-release diagnostic evidence",
    )
    parser.add_argument("--progress", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = run(args)
    print(
        json.dumps(
            {
                "output_dir": str(Path(args.output_dir).expanduser().resolve()),
                "requested_scenarios": manifest["requested_scenarios"],
                "evaluated_scenarios": manifest["evaluated_scenarios"],
                "judge_model": manifest["models"]["judge"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
