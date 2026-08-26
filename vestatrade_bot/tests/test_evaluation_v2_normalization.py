from __future__ import annotations

import pytest

from app.evaluation_v2.compiler import compile_outcome_contract
from app.evaluation_v2.contracts import (
    CriterionImportance,
    CriterionSource,
    FailureEffect,
    OutcomeDisposition,
    TerminalState,
)
from app.evaluation_v2.normalization import (
    ContractNormalization,
    CriterionNormalization,
    apply_normalization_registry,
    apply_reviewed_normalization,
)


def _source_contract():
    return compile_outcome_contract(
        {
            "id": "N01",
            "block": "normalization",
            "category": "selection",
            "priority": "P0",
            "goal": "Подобрать товар без выдуманных характеристик",
            "pass_criteria": "Даёт предварительный результат при неизвестном размере.",
            "red_flags": "выдумывает размер; повторяет тот же вопрос",
            "checks": "подбор · честная граница",
            "expects_cards": True,
        },
        dataset_sha256="b" * 64,
    )


def _normalization(contract):
    return ContractNormalization(
        normalization_id="review-N01-v1",
        contract_id=contract.contract_id,
        dataset_sha256=contract.source_revision.dataset_sha256,
        scenario_sha256=contract.source_revision.scenario_sha256,
        user_goal="Получить подходящий товар по известным параметрам",
        test_objective="Проверить честную работу с неизвестным размером",
        disposition=OutcomeDisposition.FULFILL,
        expected_terminal_states=(TerminalState.PRELIMINARY_RESULT,),
        criteria=tuple(
            CriterionNormalization(
                criterion_id=item.criterion_id,
                importance=(
                    CriterionImportance.MINIMUM_GOAL
                    if item.source == CriterionSource.GOAL
                    else CriterionImportance.REQUIRED
                ),
                failure_effect=(
                    FailureEffect.FAIL
                    if item.source
                    in {CriterionSource.GOAL, CriterionSource.RED_FLAG}
                    else FailureEffect.PARTIAL
                ),
                temporal_scope=item.temporal_scope,
            )
            for item in contract.criteria
        ),
        reviewer="developer-review",
    )


def test_reviewed_normalization_is_complete_revision_locked_overlay() -> None:
    source = _source_contract()
    reviewed = apply_reviewed_normalization(source, _normalization(source))

    assert source.release_ready is False
    assert reviewed.release_ready is False
    assert reviewed.normalization_id == "review-N01-v1"
    assert reviewed.normalization_reviewer == "developer-review"
    assert reviewed.request.user_goal == (
        "Получить подходящий товар по известным параметрам"
    )
    assert all(
        item.importance != CriterionImportance.UNCLASSIFIED
        for item in reviewed.criteria
    )
    assert all(not item.conditional_semantics_unresolved for item in reviewed.criteria)

    approved = apply_reviewed_normalization(
        source,
        _normalization(source),
        registry_sha256="d" * 64,
        approval_verified=True,
    )
    assert approved.release_ready is True


def test_stale_or_incomplete_normalization_fails_closed() -> None:
    source = _source_contract()
    normalization = _normalization(source)

    with pytest.raises(ValueError, match="stale"):
        apply_reviewed_normalization(
            source,
            normalization.model_copy(update={"scenario_sha256": "c" * 64}),
        )
    with pytest.raises(ValueError, match="every contract criterion"):
        apply_reviewed_normalization(
            source,
            normalization.model_copy(update={"criteria": normalization.criteria[:-1]}),
        )


def test_minimum_goal_cannot_move_to_a_non_goal_or_become_non_failing() -> None:
    source = _source_contract()
    normalization = _normalization(source)
    goal = next(
        item
        for item in normalization.criteria
        if item.importance == CriterionImportance.MINIMUM_GOAL
    )
    other = next(
        item
        for item in normalization.criteria
        if item.criterion_id != goal.criterion_id
    )
    moved = tuple(
        item.model_copy(update={"importance": CriterionImportance.REQUIRED})
        if item.criterion_id == goal.criterion_id
        else item.model_copy(update={"importance": CriterionImportance.MINIMUM_GOAL})
        if item.criterion_id == other.criterion_id
        else item
        for item in normalization.criteria
    )
    weakened = tuple(
        item.model_copy(update={"failure_effect": FailureEffect.PARTIAL})
        if item.criterion_id == goal.criterion_id
        else item
        for item in normalization.criteria
    )

    with pytest.raises(ValueError, match="match source goal"):
        apply_reviewed_normalization(
            source,
            normalization.model_copy(update={"criteria": moved}),
        )
    with pytest.raises(ValueError, match="must fail"):
        apply_reviewed_normalization(
            source,
            normalization.model_copy(update={"criteria": weakened}),
        )


def test_registry_can_require_full_contract_coverage() -> None:
    first = _source_contract()
    second = first.model_copy(
        update={"contract_id": "outcome_contract_second", "scenario_id": "N02"}
    )
    with pytest.raises(ValueError, match="does not cover every contract"):
        apply_normalization_registry(
            (first, second),
            (_normalization(first),),
        )
