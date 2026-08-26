from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_outcome_evaluation_v2 import (
    DEFAULT_CATALOG,
    DEFAULT_TESTSET,
    _build_judge,
    _validate_source_manifest,
    run,
)
from app.evaluation_v2.provenance import canonical_catalog_sha256
from app.feed_loader import FeedLoader


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _args(tmp_path: Path, transcripts: Path) -> argparse.Namespace:
    return argparse.Namespace(
        testset=str(DEFAULT_TESTSET),
        transcripts=str(transcripts),
        catalog=str(PROJECT_ROOT / "data/feed_showcase_100_2026-06-14.xml"),
        output_dir=str(tmp_path / "out"),
        source_label="offline-fixture",
        only=None,
        limit=2,
        judge=False,
        judge_model=None,
        bot_model=None,
        capability_contract=None,
        source_manifest=None,
        normalization_registry=None,
        allow_partial_normalization=False,
        allow_unverified_source_provenance=True,
        progress=False,
    )


def test_offline_runner_rescores_saved_records_without_bot_or_network(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "transcripts.jsonl"
    records = [
        {
            "id": scenario_id,
            "execution_status": "valid",
            "turns": [
                {
                    "n": 1,
                    "user": "Нужен товар, телефон +7 999 123-45-67",
                    "bot": "Уточните обязательный параметр.",
                    "products": [],
                }
            ],
        }
        for scenario_id in ("A01", "A02")
    ]
    transcripts.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )

    manifest = run(_args(tmp_path, transcripts))

    assert manifest["requested_scenarios"] == 2
    assert manifest["total_contract_scenarios"] == 100
    assert manifest["partial_run"] is True
    assert manifest["release_ready_scenarios"] == 0
    assert manifest["models"]["judge"] is None
    evaluations = (tmp_path / "out/outcome_evaluations.jsonl").read_text(
        encoding="utf-8"
    )
    assert "+7 999 123-45-67" not in evaluations
    assert "user_text" not in evaluations
    summary = json.loads((tmp_path / "out/summary.json").read_text())
    assert summary["denominator"]["requested"] == 2
    assert summary["judge_status_counts"]["unavailable"] == 2


def test_runner_refuses_unverified_saved_transcript_by_default(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "transcripts.jsonl"
    transcripts.write_text(
        json.dumps(
            {
                "id": "A01",
                "execution_status": "valid",
                "turns": [
                    {
                        "n": 1,
                        "user": "Нужен товар",
                        "bot": "Уточните",
                        "products": [],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, transcripts)
    args.allow_unverified_source_provenance = False

    with pytest.raises(ValueError, match="provenance is not verified"):
        run(args)

    assert not Path(args.output_dir).exists()


def test_runner_requires_manifest_scenarios_to_equal_transcript_artifact(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "transcripts.jsonl"
    transcript_payload = (
        json.dumps(
            {
                "id": "A01",
                "execution_status": "valid",
                "turns": [{"n": 1, "user": "Нужен товар", "bot": "Уточните"}],
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    transcripts.write_bytes(transcript_payload)
    catalog_path = PROJECT_ROOT / "data/feed_showcase_100_2026-06-14.xml"
    catalog_products = FeedLoader().parse_xml(catalog_path.read_bytes())
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "inputs": {
                    "testset_sha256": hashlib.sha256(
                        DEFAULT_TESTSET.read_bytes()
                    ).hexdigest(),
                    "catalog_sha256": canonical_catalog_sha256(catalog_products),
                    "scenario_ids": ["A01", "A02"],
                },
                "artifacts": {
                    "transcripts_sha256": hashlib.sha256(
                        transcript_payload
                    ).hexdigest()
                },
                "llm": {
                    "provider": "openrouter",
                    "model": "qwen/qwen3-vl-8b-instruct",
                    "strong_model": "qwen/qwen3-vl-8b-instruct",
                },
                "run": {"mode": "live"},
            }
        ),
        encoding="utf-8",
    )
    args = _args(tmp_path, transcripts)
    args.source_manifest = str(manifest_path)
    args.allow_unverified_source_provenance = False

    with pytest.raises(
        ValueError,
        match="source_manifest_transcript_scenario_set_mismatch",
    ):
        run(args)

    assert not Path(args.output_dir).exists()


def test_missing_full_suite_transcripts_never_claim_full_suite_evidence(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "transcripts.jsonl"
    transcripts.write_text(
        json.dumps(
            {
                "id": "A01",
                "execution_status": "valid",
                "turns": [{"n": 1, "user": "Нужен товар", "bot": "Уточните"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, transcripts)
    args.limit = None

    manifest = run(args)

    assert manifest["selected_scenarios"] == 100
    assert manifest["evaluation_complete"] is False
    evaluation = json.loads(
        (tmp_path / "out/outcome_evaluations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert evaluation["release_run_evidence"]["full_suite_selected"] is False


def test_runner_defaults_to_same_full_catalog_universe_as_live_full100() -> None:
    assert DEFAULT_CATALOG == PROJECT_ROOT / "data/products_all.xml"


def test_paid_judge_requires_two_flags_and_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)
    monkeypatch.delenv("RUN_OUTCOME_JUDGE_EVALS", raising=False)
    with pytest.raises(RuntimeError, match="requires RUN_LIVE_LLM_TESTS"):
        _build_judge(
            requested=True,
            judge_model_arg="anthropic/claude-sonnet-4",
            bot_model_arg="qwen/qwen3-vl-8b-instruct",
        )

    monkeypatch.setenv("RUN_LIVE_LLM_TESTS", "1")
    monkeypatch.setenv("RUN_OUTCOME_JUDGE_EVALS", "1")
    monkeypatch.delenv("OUTCOME_JUDGE_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="explicit --judge-model"):
        _build_judge(
            requested=True,
            judge_model_arg=None,
            bot_model_arg="qwen/qwen3-vl-8b-instruct",
        )


def test_source_manifest_prevents_catalog_universe_mismatch() -> None:
    with pytest.raises(ValueError, match="catalog_sha256_missing"):
        _validate_source_manifest(
            {
                "schema_version": 1,
                "inputs": {
                    "testset_sha256": "a" * 64,
                    "catalog_products": 14_035,
                    "scenario_ids": ["A01"],
                },
                "llm": {
                    "provider": "openrouter",
                    "model": "qwen/qwen3-vl-8b-instruct",
                    "strong_model": "qwen/qwen3-vl-8b-instruct",
                },
                "run": {"mode": "live"},
            },
            testset_sha256="a" * 64,
            catalog_sha256="b" * 64,
            transcript_scenario_ids={"A01"},
        )
