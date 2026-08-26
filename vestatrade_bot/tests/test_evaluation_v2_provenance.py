from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from app.evaluation_v2.provenance import (
    canonical_catalog_sha256,
    is_pinned_model_identifier,
    validate_source_run_provenance,
)


TESTSET_SHA = "a" * 64


def _catalog(*, second_price: int = 200) -> list[dict]:
    return [
        {
            "sku": "SKU-Б",
            "name": "Второй",
            "price": second_price,
            "updated_at": "2026-08-25T10:00:00Z",
        },
        {
            "sku": "SKU-А",
            "name": "Первый",
            "price": 100,
            "updated_at": "2026-08-25T11:00:00Z",
        },
    ]


def _manifest(
    catalog_sha256: str,
    *,
    scenario_ids: list[str] | None = None,
    mode: str = "live",
    model: str = "qwen/qwen3-vl-8b-instruct",
    strong_model: str = "qwen/qwen3-vl-8b-instruct",
    transcripts_sha256: str | None = None,
) -> dict:
    manifest = {
        "schema_version": 1,
        "inputs": {
            "testset_sha256": TESTSET_SHA,
            "catalog_sha256": catalog_sha256,
            "catalog_products": 2,
            "catalog_source": "cache",
            "scenario_ids": scenario_ids or ["A01", "A02", "A03"],
        },
        "llm": {
            "provider": "openrouter",
            "model": model,
            "strong_model": strong_model,
        },
        "run": {"mode": mode},
    }
    if transcripts_sha256 is not None:
        manifest["artifacts"] = {"transcripts_sha256": transcripts_sha256}
    return manifest


def _validate(manifest: dict, catalog_hash: str, **kwargs):
    return validate_source_run_provenance(
        manifest,
        expected_testset_sha256=TESTSET_SHA,
        expected_catalog_sha256=catalog_hash,
        requested_scenario_ids=kwargs.pop("requested_scenario_ids", ("A01",)),
        **kwargs,
    )


def test_catalog_hash_matches_stage6_canonical_algorithm_exactly() -> None:
    # Independent fixed vector: sorted by SKU/name/canonical JSON, UTF-8 JSON,
    # compact separators, and no load-time ``updated_at`` field.
    canonical = [
        {"name": "Первый", "price": 100, "sku": "SKU-А"},
        {"name": "Второй", "price": 200, "sku": "SKU-Б"},
    ]
    expected = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    first = canonical_catalog_sha256(_catalog())
    reversed_with_new_times = canonical_catalog_sha256(
        [
            {**item, "updated_at": "2030-01-01T00:00:00Z"}
            for item in reversed(_catalog())
        ]
    )

    assert first == expected
    assert reversed_with_new_times == expected


def test_equal_catalog_count_with_different_hash_fails_closed() -> None:
    expected_hash = canonical_catalog_sha256(_catalog())
    different_hash = canonical_catalog_sha256(_catalog(second_price=201))
    manifest = _manifest(different_hash)
    assert manifest["inputs"]["catalog_products"] == len(_catalog())

    result = _validate(manifest, expected_hash)

    assert result.accepted is False
    assert result.provenance is None
    assert result.reason_codes == ("source_manifest_catalog_sha256_mismatch",)


def test_missing_catalog_hash_fails_with_explicit_reason() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    manifest = _manifest(catalog_hash)
    manifest["inputs"].pop("catalog_sha256")

    result = _validate(manifest, catalog_hash)

    assert result.accepted is False
    assert "source_manifest_catalog_sha256_missing" in result.reason_codes


def test_transcript_digest_is_required_and_exact_when_requested() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    expected_transcripts = "c" * 64
    missing = validate_source_run_provenance(
        _manifest(catalog_hash),
        expected_testset_sha256=TESTSET_SHA,
        expected_catalog_sha256=catalog_hash,
        expected_transcripts_sha256=expected_transcripts,
        requested_scenario_ids=("A01",),
    )
    mismatched = validate_source_run_provenance(
        _manifest(catalog_hash, transcripts_sha256="d" * 64),
        expected_testset_sha256=TESTSET_SHA,
        expected_catalog_sha256=catalog_hash,
        expected_transcripts_sha256=expected_transcripts,
        requested_scenario_ids=("A01",),
    )
    accepted = validate_source_run_provenance(
        _manifest(catalog_hash, transcripts_sha256=expected_transcripts),
        expected_testset_sha256=TESTSET_SHA,
        expected_catalog_sha256=catalog_hash,
        expected_transcripts_sha256=expected_transcripts,
        requested_scenario_ids=("A01",),
    )

    assert "source_manifest_transcripts_sha256_missing" in missing.reason_codes
    assert "source_manifest_transcripts_sha256_mismatch" in mismatched.reason_codes
    assert accepted.accepted is True
    assert accepted.provenance is not None
    assert accepted.provenance.transcripts_sha256 == expected_transcripts


def test_valid_provenance_extracts_both_pinned_bot_models() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    manifest = _manifest(
        catalog_hash,
        model="qwen/qwen3-vl-8b-instruct",
        strong_model="anthropic/claude-sonnet-4",
    )

    result = _validate(manifest, catalog_hash)

    assert result.accepted is True
    assert result.reason_codes == ()
    assert result.provenance is not None
    assert result.provenance.bot_model == "qwen/qwen3-vl-8b-instruct"
    assert result.provenance.bot_strong_model == "anthropic/claude-sonnet-4"
    assert result.provenance.bot_models_for_independence == (
        "qwen/qwen3-vl-8b-instruct",
        "anthropic/claude-sonnet-4",
    )
    with pytest.raises(ValidationError):
        result.provenance.bot_model = "changed/model"  # type: ignore[misc]


@pytest.mark.parametrize(
    "alias",
    [
        "openrouter/auto",
        "openrouter/auto:online",
        "provider/latest",
        "provider/model-latest",
        "auto",
    ],
)
def test_dynamic_model_aliases_are_not_pinned(alias: str) -> None:
    assert is_pinned_model_identifier(alias) is False


def test_alias_in_source_manifest_rejects_independent_judge_provenance() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())

    result = _validate(
        _manifest(catalog_hash, model="openrouter/auto"),
        catalog_hash,
    )

    assert result.accepted is False
    assert "source_manifest_bot_model_not_pinned" in result.reason_codes


def test_partial_evaluation_is_valid_when_requested_ids_are_covered() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    result = _validate(
        _manifest(catalog_hash, scenario_ids=["A01", "A02", "A03"]),
        catalog_hash,
        requested_scenario_ids=("A01", "A03"),
    )

    assert result.accepted is True
    assert result.requested_scenario_ids == ("A01", "A03")
    assert result.full_suite_required is False


def test_exact_transcript_scenario_set_rejects_manifest_only_scenarios() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    manifest = _manifest(catalog_hash, scenario_ids=["A01", "A02"])

    subset = _validate(
        manifest,
        catalog_hash,
        requested_scenario_ids=("A01",),
    )
    exact = _validate(
        manifest,
        catalog_hash,
        requested_scenario_ids=("A01",),
        require_exact_scenario_set=True,
    )

    assert subset.accepted is True
    assert exact.accepted is False
    assert exact.exact_scenario_set_required is True
    assert exact.unexpected_requested_scenario_ids == ("A02",)
    assert (
        "source_manifest_transcript_scenario_set_mismatch"
        in exact.reason_codes
    )


def test_full_suite_validation_is_exact_even_for_partial_evaluation() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    complete = _validate(
        _manifest(catalog_hash, scenario_ids=["A01", "A02", "A03"]),
        catalog_hash,
        requested_scenario_ids=("A01",),
        full_suite_scenario_ids=("A01", "A02", "A03"),
        require_full_suite=True,
    )
    incomplete = _validate(
        _manifest(catalog_hash, scenario_ids=["A01", "A02"]),
        catalog_hash,
        requested_scenario_ids=("A01",),
        full_suite_scenario_ids=("A01", "A02", "A03"),
        require_full_suite=True,
    )

    assert complete.accepted is True
    assert complete.full_suite_covered is True
    assert incomplete.accepted is False
    assert incomplete.missing_full_suite_scenario_ids == ("A03",)
    assert "source_manifest_full_suite_coverage_mismatch" in incomplete.reason_codes


def test_missing_requested_scenario_and_non_live_mode_fail_explicitly() -> None:
    catalog_hash = canonical_catalog_sha256(_catalog())
    result = _validate(
        _manifest(catalog_hash, scenario_ids=["A01"], mode="replay"),
        catalog_hash,
        requested_scenario_ids=("A01", "A02"),
    )

    assert result.accepted is False
    assert result.missing_requested_scenario_ids == ("A02",)
    assert "source_manifest_requested_scenario_missing" in result.reason_codes
    assert "source_manifest_run_mode_not_live" in result.reason_codes


def test_missing_required_sections_fail_closed_without_exception() -> None:
    result = validate_source_run_provenance(
        {"schema_version": 1},
        expected_testset_sha256=TESTSET_SHA,
        expected_catalog_sha256="b" * 64,
        requested_scenario_ids=("A01",),
    )

    assert result.accepted is False
    assert result.provenance is None
    assert {
        "source_manifest_inputs_missing",
        "source_manifest_llm_missing",
        "source_manifest_run_missing",
        "source_manifest_testset_sha256_missing",
        "source_manifest_catalog_sha256_missing",
        "source_manifest_scenario_ids_missing",
        "source_manifest_run_mode_missing",
        "source_manifest_bot_model_missing",
        "source_manifest_bot_strong_model_missing",
    }.issubset(result.reason_codes)
