from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.evaluation_v2.compiler import (
    canonical_payload_sha256,
    compile_outcome_contracts,
    validate_contract_provenance,
)
from app.evaluation_v2.contracts import (
    ContractNormalizationStatus,
    CriterionPolarity,
    CriterionSource,
    FailureEffect,
)
from app.openrouter_client import OpenRouterClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTSET_PATH = PROJECT_ROOT / "data" / "live_dialogue_feed_testset_2026-08-25.json"


def _load_testset() -> dict:
    return json.loads(TESTSET_PATH.read_text(encoding="utf-8"))


def test_feed100_compiles_to_exactly_one_unique_source_contract_per_scenario() -> None:
    payload = _load_testset()

    contracts = compile_outcome_contracts(payload)

    assert len(payload["scenarios"]) == 100
    assert len(contracts) == 100
    assert len({item.scenario_id for item in contracts}) == 100
    assert len({item.contract_id for item in contracts}) == 100
    assert all(
        item.normalization_status == ContractNormalizationStatus.SOURCE_IMPORTED
        for item in contracts
    )
    assert all(
        item.source_revision.dataset_sha256 == canonical_payload_sha256(payload)
        for item in contracts
    )


def test_every_source_span_is_exact_and_stale_source_is_rejected() -> None:
    payload = _load_testset()
    contracts = compile_outcome_contracts(payload)
    scenarios = {str(item["id"]): item for item in payload["scenarios"]}

    for contract in contracts:
        scenario = scenarios[contract.scenario_id]
        validate_contract_provenance(contract, scenario)
        source_values = {
            "goal": str(scenario.get("goal") or ""),
            "pass_criteria": str(scenario.get("pass_criteria") or ""),
            "red_flags": str(scenario.get("red_flags") or ""),
            "checks": str(scenario.get("checks") or ""),
            "expects_cards": str(bool(scenario.get("expects_cards"))).lower(),
        }
        spans = [
            span
            for criterion in contract.criteria
            for span in criterion.provenance
        ] + [item.provenance for item in contract.capability_expectations]
        for span in spans:
            source = source_values[span.field.value]
            assert source[span.start_char : span.end_char] == span.exact_text

    stale = copy.deepcopy(scenarios[contracts[0].scenario_id])
    stale["goal"] = f"{stale['goal']} (изменено после компиляции)"
    with pytest.raises(ValueError, match="stale"):
        validate_contract_provenance(contracts[0], stale)


def test_all_308_developer_red_flags_remain_prohibited_fail_criteria() -> None:
    contracts = compile_outcome_contracts(_load_testset())
    red_flags = [
        criterion
        for contract in contracts
        for criterion in contract.criteria
        if criterion.source == CriterionSource.RED_FLAG
    ]

    assert len(red_flags) == 308
    assert all(item.polarity == CriterionPolarity.PROHIBITED for item in red_flags)
    assert all(item.failure_effect == FailureEffect.FAIL for item in red_flags)
    assert all(item.required for item in red_flags)
    assert all(item.provenance for item in red_flags)


def test_compilation_is_stable_and_does_not_call_an_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _load_testset()

    def _unexpected_llm_call(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("contract compilation must not call an LLM")

    monkeypatch.setattr(OpenRouterClient, "complete_json", _unexpected_llm_call)
    first = compile_outcome_contracts(copy.deepcopy(payload))
    second = compile_outcome_contracts(copy.deepcopy(payload))

    assert first == second
    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]
