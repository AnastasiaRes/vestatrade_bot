"""Deterministic strategy escalation for stalled customer tasks."""

from __future__ import annotations

from collections.abc import Iterable

from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.dialogue_v2.contracts import DialogueStateV2, ResponseStrategyKind

from .contracts import StrategyDirective, TaskProgressAssessment


def select_strategy_directives(
    state: DialogueStateV2,
    progress_assessments: Iterable[TaskProgressAssessment],
    readiness_assessments: Iterable[TaskReadinessAssessment] = (),
    *,
    catalog_planning: CatalogPlanningResult | None = None,
    verifiable_external_task_ids: Iterable[str] = (),
) -> tuple[StrategyDirective, ...]:
    readiness = {item.task_id: item for item in readiness_assessments}
    external = set(verifiable_external_task_ids)
    directives: list[StrategyDirective] = []
    for progress in progress_assessments:
        if not progress.strategy_change_required:
            continue
        assessment = readiness.get(progress.task_id)
        attempted = set(progress.attempted_strategies)
        if progress.previous_strategy is not None:
            attempted.add(progress.previous_strategy)
        candidates: list[tuple[ResponseStrategyKind, str | None]] = []
        if assessment is not None and assessment.learn_method_code:
            candidates.append(
                (
                    ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
                    progress.unresolved_blocker
                    or assessment.recommended_question_fact
                    or (assessment.unknown_facts[0] if assessment.unknown_facts else None),
                )
            )
        if assessment is not None and (
            assessment.status == ReadinessStatus.PRELIMINARY_READY
            or assessment.unknown_facts
            or assessment.refused_facts
            or assessment.deferred_facts
        ):
            candidates.append((ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS, None))
        if assessment is not None and (
            assessment.confirmed_hard_facts or assessment.confirmed_soft_facts
        ):
            candidates.append((ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS, None))
        previous_catalog = catalog_planning or state.catalog_planning
        if previous_catalog is not None and any(
            item.task_id == progress.task_id and item.relaxed_skus
            for item in previous_catalog.search_plans
        ):
            candidates.append((ResponseStrategyKind.PRESENT_CONTROLLED_ANALOG, None))
        if progress.task_id in external:
            candidates.append(
                (ResponseStrategyKind.OFFER_VERIFIABLE_EXTERNAL_STEP, None)
            )
        candidates.extend(
            (
                (ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY, None),
                (ResponseStrategyKind.CLOSE_TASK, None),
            )
        )
        selected = next(
            ((strategy, fact) for strategy, fact in candidates if strategy not in attempted),
            (ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY, None),
        )
        directives.append(
            StrategyDirective(
                task_id=progress.task_id,
                strategy=selected[0],
                fact_name=selected[1],
                reason_codes=(
                    "two_consecutive_content_turns_without_progress",
                    "strategy_changed_for_same_blocker",
                ),
            )
        )
    return tuple(directives)
