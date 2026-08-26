"""Lossless compiler from the existing developer testset to contracts.

The compiler intentionally does not infer product kinds, minimum success,
conditional branches or user intent from Russian prose. Those meanings need a
reviewed normalization map. It does preserve every source fragment with a
stable hash so a later map cannot silently survive a changed testset.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import (
    CapabilityExpectation,
    ContractNormalizationStatus,
    CriterionEvaluationMode,
    CriterionImportance,
    CriterionPolarity,
    CriterionSource,
    FailureEffect,
    OutcomeContract,
    OutcomeCriterion,
    OutcomePriority,
    RequestSpec,
    SourceField,
    SourceRevision,
    SourceSpan,
    TemporalScope,
)


DEFAULT_DATASET_ID = "feed100-workbook-2026-08-25"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(*parts: str, prefix: str) -> str:
    material = "\x1f".join(parts)
    return f"{prefix}_{_sha256_text(material)[:20]}"


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    return _sha256_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


# Public compatibility name used by the initial Stage 7 scaffold.
canonical_testset_sha256 = canonical_payload_sha256


def _span(field: SourceField, source: str, start: int, end: int) -> SourceSpan:
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    exact = source[start:end]
    if not exact:
        raise ValueError(f"empty source span for {field.value}")
    return SourceSpan(
        field=field,
        start_char=start,
        end_char=end,
        exact_text=exact,
        text_sha256=_sha256_text(exact),
    )


def _whole_field_span(field: SourceField, source: str) -> SourceSpan:
    return _span(field, source, 0, len(source))


def _semicolon_spans(field: SourceField, source: str) -> tuple[SourceSpan, ...]:
    """Treat an explicit semicolon list as clauses without interpreting text."""

    result: list[SourceSpan] = []
    start = 0
    for index, character in enumerate(source):
        if character != ";":
            continue
        if source[start:index].strip():
            result.append(_span(field, source, start, index))
        start = index + 1
    if source[start:].strip():
        result.append(_span(field, source, start, len(source)))
    return tuple(result)


def _sentence_spans(field: SourceField, source: str) -> tuple[SourceSpan, ...]:
    """Split only at explicit sentence punctuation, preserving exact slices.

    This is structural tokenization rather than semantic normalization. A
    conditional sentence stays intact and is marked unresolved in the result.
    """

    result: list[SourceSpan] = []
    start = 0
    length = len(source)
    for index, character in enumerate(source):
        if character not in ".!?":
            continue
        next_index = index + 1
        if next_index < length and not source[next_index].isspace():
            continue
        if source[start:next_index].strip():
            result.append(_span(field, source, start, next_index))
        start = next_index
    if source[start:].strip():
        result.append(_span(field, source, start, length))
    return tuple(result)


def _capability_spans(source: str) -> tuple[SourceSpan, ...]:
    result: list[SourceSpan] = []
    start = 0
    for index, character in enumerate(source):
        if character not in {"·", ";"}:
            continue
        if source[start:index].strip():
            result.append(_span(SourceField.CHECKS, source, start, index))
        start = index + 1
    if source[start:].strip():
        result.append(_span(SourceField.CHECKS, source, start, len(source)))
    return tuple(result)


def _scenario_sha256(scenario: Mapping[str, Any]) -> str:
    return canonical_payload_sha256(scenario)


def validate_contract_provenance(
    contract: OutcomeContract,
    scenario: Mapping[str, Any],
) -> None:
    """Fail when any source span no longer matches its testset field."""

    source_values = {
        SourceField.GOAL: str(scenario.get("goal") or ""),
        SourceField.PASS_CRITERIA: str(scenario.get("pass_criteria") or ""),
        SourceField.RED_FLAGS: str(scenario.get("red_flags") or ""),
        SourceField.CHECKS: str(scenario.get("checks") or ""),
        SourceField.EXPECTS_CARDS: str(bool(scenario.get("expects_cards"))).lower(),
    }
    spans = [
        span
        for criterion in contract.criteria
        for span in criterion.provenance
    ] + [item.provenance for item in contract.capability_expectations]
    for span in spans:
        source = source_values[span.field]
        if source[span.start_char : span.end_char] != span.exact_text:
            raise ValueError(
                f"stale provenance for {contract.scenario_id}:{span.field.value}"
            )
    if contract.source_revision.scenario_sha256 != _scenario_sha256(scenario):
        raise ValueError(f"stale scenario revision for {contract.scenario_id}")


def compile_outcome_contract(
    scenario: Mapping[str, Any],
    *,
    dataset_sha256: str | None = None,
    testset_sha256: str | None = None,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> OutcomeContract:
    """Import one scenario without asking an LLM to reinterpret it."""

    dataset_digest = dataset_sha256 or testset_sha256
    if not dataset_digest:
        raise ValueError("dataset_sha256 is required")
    scenario_id = str(scenario.get("id") or "").strip()
    goal = str(scenario.get("goal") or "")
    pass_criteria = str(scenario.get("pass_criteria") or "")
    red_flags = str(scenario.get("red_flags") or "")
    checks = str(scenario.get("checks") or "")
    if not scenario_id or not goal.strip():
        raise ValueError("scenario requires id and goal")

    priority = OutcomePriority(
        str(scenario.get("priority") or "P2").strip().upper()
    )
    criteria: list[OutcomeCriterion] = []
    goal_span = _whole_field_span(SourceField.GOAL, goal)
    criteria.append(
        OutcomeCriterion(
            criterion_id=_stable_id(
                scenario_id,
                SourceField.GOAL.value,
                goal_span.text_sha256,
                prefix="criterion",
            ),
            source=CriterionSource.GOAL,
            polarity=CriterionPolarity.REQUIRED,
            description=goal_span.exact_text,
            evaluation_mode=CriterionEvaluationMode.INDEPENDENT_JUDGE,
            importance=CriterionImportance.UNCLASSIFIED,
            failure_effect=FailureEffect.FAIL,
            temporal_scope=TemporalScope.END_STATE,
            conditional_semantics_unresolved=True,
            provenance=(goal_span,),
        )
    )
    for span in _sentence_spans(SourceField.PASS_CRITERIA, pass_criteria):
        criteria.append(
            OutcomeCriterion(
                criterion_id=_stable_id(
                    scenario_id,
                    SourceField.PASS_CRITERIA.value,
                    span.text_sha256,
                    prefix="criterion",
                ),
                source=CriterionSource.PASS_CRITERIA,
                polarity=CriterionPolarity.REQUIRED,
                description=span.exact_text,
                evaluation_mode=CriterionEvaluationMode.INDEPENDENT_JUDGE,
                importance=CriterionImportance.UNCLASSIFIED,
                # Until minimum/full success is reviewed, one unmet PASS
                # sentence may only cap the provisional verdict to PARTIAL.
                failure_effect=FailureEffect.PARTIAL,
                temporal_scope=TemporalScope.DIALOGUE,
                conditional_semantics_unresolved=True,
                provenance=(span,),
            )
        )
    for span in _semicolon_spans(SourceField.RED_FLAGS, red_flags):
        criteria.append(
            OutcomeCriterion(
                criterion_id=_stable_id(
                    scenario_id,
                    SourceField.RED_FLAGS.value,
                    span.text_sha256,
                    prefix="criterion",
                ),
                source=CriterionSource.RED_FLAG,
                polarity=CriterionPolarity.PROHIBITED,
                description=span.exact_text,
                evaluation_mode=CriterionEvaluationMode.INDEPENDENT_JUDGE,
                importance=CriterionImportance.REQUIRED,
                failure_effect=FailureEffect.FAIL,
                temporal_scope=TemporalScope.DIALOGUE,
                conditional_semantics_unresolved=False,
                provenance=(span,),
            )
        )
    if bool(scenario.get("expects_cards")):
        criteria.append(
            OutcomeCriterion(
                criterion_id=_stable_id(
                    scenario_id,
                    SourceField.EXPECTS_CARDS.value,
                    "true",
                    prefix="criterion",
                ),
                source=CriterionSource.DETERMINISTIC_GATE,
                polarity=CriterionPolarity.REQUIRED,
                description=(
                    "Тест-набор помечает карточки как ожидаемые; отсутствие "
                    "карточек требует проверки результата."
                ),
                evaluation_mode=CriterionEvaluationMode.DETERMINISTIC,
                importance=CriterionImportance.UNCLASSIFIED,
                failure_effect=FailureEffect.PARTIAL,
                temporal_scope=TemporalScope.DIALOGUE,
                conditional_semantics_unresolved=True,
                deterministic_rule_codes=("EXPECTED_CARDS_MISSING",),
            )
        )

    capabilities = tuple(
        CapabilityExpectation(
            expectation_id=_stable_id(
                scenario_id,
                SourceField.CHECKS.value,
                span.text_sha256,
                prefix="capability",
            ),
            source_text=span.exact_text,
            provenance=span,
        )
        for span in _capability_spans(checks)
    )
    scenario_digest = _scenario_sha256(scenario)
    contract = OutcomeContract(
        contract_id=_stable_id(
            scenario_id,
            dataset_digest,
            scenario_digest,
            prefix="outcome_contract",
        ),
        scenario_id=scenario_id,
        source_revision=SourceRevision(
            dataset_id=dataset_id,
            dataset_sha256=dataset_digest,
            scenario_sha256=scenario_digest,
        ),
        normalization_status=ContractNormalizationStatus.SOURCE_IMPORTED,
        block=str(scenario.get("block") or "unknown"),
        category=str(scenario.get("category") or "unknown"),
        priority=priority,
        difficulty=str(scenario.get("difficulty") or ""),
        buyer_mode=str(scenario.get("buyer_mode") or ""),
        request=RequestSpec(
            raw_goal=goal_span.exact_text,
            test_objective=goal_span.exact_text,
        ),
        expects_cards=bool(scenario.get("expects_cards")),
        criteria=tuple(criteria),
        capability_expectations=capabilities,
    )
    validate_contract_provenance(contract, scenario)
    return contract


def compile_outcome_contracts(
    payload: Mapping[str, Any],
    *,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> tuple[OutcomeContract, ...]:
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, Iterable) or isinstance(scenarios, (str, bytes)):
        raise ValueError("testset must contain a scenarios collection")
    scenario_items = list(scenarios)
    if not all(isinstance(item, Mapping) for item in scenario_items):
        raise ValueError("every testset scenario must be an object")
    digest = canonical_payload_sha256(payload)
    contracts = tuple(
        compile_outcome_contract(
            item,
            dataset_sha256=digest,
            dataset_id=dataset_id,
        )
        for item in scenario_items
    )
    ids = [item.scenario_id for item in contracts]
    if len(ids) != len(set(ids)):
        raise ValueError("testset scenario ids must be unique")
    if not contracts:
        raise ValueError("testset contains no scenarios")
    return contracts
