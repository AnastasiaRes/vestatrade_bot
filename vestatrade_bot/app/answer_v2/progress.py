"""Task-scoped progress based on typed state changes, never reply text."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from app.catalog_v2.contracts import CatalogPlanningResult
from app.commerce_v2.contracts import CommercePlanningResult
from app.dialogue_v2.contracts import (
    ConstraintStatus,
    DialogueStateV2,
    TaskAct,
    TaskStatus,
    TurnMetadata,
)

from .contracts import TaskProgressAssessment, TaskProgressStatus


_NEUTRAL_ACTS = {TaskAct.GREETING, TaskAct.GRATITUDE}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _task_fingerprint(state: DialogueStateV2, task_id: str) -> str | None:
    task = next((item for item in state.tasks if item.task_id == task_id), None)
    if task is None:
        return None
    goal = next(
        (item for item in state.product_goals if item.goal_id == task.target_goal_id),
        None,
    )
    facts = sorted(
        (
            item.name,
            item.value,
            item.unit,
            item.status.value,
            item.polarity.value,
            item.strength.value,
        )
        for item in state.constraints
        if item.active
        and (
            item.task_id == task_id
            or (
                task.target_goal_id is not None
                and item.goal_id == task.target_goal_id
            )
        )
    )
    direct = sorted(
        (item.act.value, item.resolved)
        for item in state.direct_questions
        if item.task_id == task_id
    )
    return _digest(
        {
            "act": task.act.value,
            "status": task.status.value,
            "blocking_reason": task.blocking_reason,
            "goal": (
                None
                if goal is None
                else {
                    "type": goal.canonical_type,
                    "category": goal.category.value,
                    "role": goal.role.value,
                    "type_locked": goal.type_locked,
                    "category_locked": goal.category_locked,
                }
            ),
            "facts": facts,
            "direct": direct,
        }
    )


def _catalog_signature(
    planning: CatalogPlanningResult | None,
    task_id: str,
) -> str | None:
    if planning is None:
        return None
    plans = [item for item in planning.search_plans if item.task_id == task_id]
    if not plans:
        return None
    return _digest(
        [
            {
                "eligible": item.eligible_skus,
                "relaxed": item.relaxed_skus,
                "unverified": item.unverified_skus,
                "unavailable": item.unavailable_constraints,
                "stages": [stage.value for stage in item.stages],
            }
            for item in plans
        ]
    )


def _commerce_signature(
    planning: CommercePlanningResult | None,
    task_id: str,
) -> str | None:
    if planning is None:
        return None
    workflows = [item for item in planning.workflows if task_id in item.task_ids]
    if not workflows:
        return None
    return _digest(
        [
            {
                "kind": item.workflow_kind.value,
                "status": item.status.value,
                "revision": item.payload_revision,
                "consent": item.consent.status.value,
                "execution": item.execution_status.value,
                "receipt": bool(item.external_receipt_ref),
            }
            for item in workflows
        ]
    )


def assess_task_progress(
    previous_state: DialogueStateV2,
    reduced_state: DialogueStateV2,
    turn_metadata: TurnMetadata,
    *,
    catalog_planning: CatalogPlanningResult | None = None,
    commerce_planning: CommercePlanningResult | None = None,
) -> tuple[TaskProgressAssessment, ...]:
    """Compare task facts/artifacts; generated words and plan ids are absent."""

    history = {item.task_id: item for item in previous_state.response_strategy_history}
    previous_catalog = previous_state.catalog_planning
    previous_commerce = previous_state.commerce_planning
    current_tasks = [
        item
        for item in reduced_state.tasks
        if item.source_turn == reduced_state.turn_number
        or item.task_id == reduced_state.task_stack.active_task_id
    ]
    if not current_tasks:
        return ()
    results: list[TaskProgressAssessment] = []
    for task in sorted(current_tasks, key=lambda item: (item.priority, item.task_id)):
        previous_history = history.get(task.task_id)
        previous_fp = _task_fingerprint(previous_state, task.task_id)
        current_fp = _task_fingerprint(reduced_state, task.task_id)
        current_catalog = _catalog_signature(catalog_planning, task.task_id)
        old_catalog = _catalog_signature(previous_catalog, task.task_id)
        current_commerce = _commerce_signature(commerce_planning, task.task_id)
        old_commerce = _commerce_signature(previous_commerce, task.task_id)
        changes: list[str] = []
        if previous_fp is None:
            changes.append("task_created")
        elif previous_fp != current_fp:
            changes.append("task_state_changed")
        if current_catalog is not None and current_catalog != old_catalog:
            changes.append("catalogue_result_changed")
        if current_commerce is not None and current_commerce != old_commerce:
            changes.append("commerce_workflow_changed")

        if task.act in _NEUTRAL_ACTS or task.status in {
            TaskStatus.CANCELLED,
            TaskStatus.SATISFIED,
        }:
            status = TaskProgressStatus.NEUTRAL
        elif changes:
            status = TaskProgressStatus.PROGRESS
        else:
            status = TaskProgressStatus.NO_PROGRESS

        previous_streak = (
            previous_history.consecutive_no_progress if previous_history else 0
        )
        streak = (
            previous_streak + 1
            if status == TaskProgressStatus.NO_PROGRESS
            else 0
            if status == TaskProgressStatus.PROGRESS
            else previous_streak
        )
        blockers = [
            item.name
            for item in reduced_state.constraints
            if item.active
            and item.status != ConstraintStatus.KNOWN
            and (
                item.task_id == task.task_id
                or (
                    task.target_goal_id is not None
                    and item.goal_id == task.target_goal_id
                )
            )
        ]
        unresolved_blocker = (
            blockers[0]
            if blockers
            else task.blocking_reason
            or (previous_history.last_question_fact if previous_history else None)
        )
        repeated_question = bool(
            status == TaskProgressStatus.NO_PROGRESS
            and previous_history is not None
            and previous_history.last_strategy is not None
            and previous_history.last_strategy.value == "ask_decision_fact"
            and previous_history.last_question_fact
            and previous_history.last_question_fact == unresolved_blocker
        )
        results.append(
            TaskProgressAssessment(
                task_id=task.task_id,
                turn_id=turn_metadata.turn_id,
                turn_number=reduced_state.turn_number,
                status=status,
                changes=tuple(changes),
                unresolved_blocker=unresolved_blocker,
                previous_strategy=(
                    previous_history.last_strategy if previous_history else None
                ),
                consecutive_no_progress=streak,
                attempted_strategies=(
                    previous_history.attempted_strategies
                    if previous_history
                    else ()
                ),
                strategy_change_required=(
                    status == TaskProgressStatus.NO_PROGRESS
                    and (streak >= 2 or repeated_question)
                ),
                catalog_signature=current_catalog or old_catalog,
                commerce_signature=current_commerce or old_commerce,
                reason_codes=(
                    ("typed_task_progress",)
                    if status == TaskProgressStatus.PROGRESS
                    else (
                        "typed_task_no_progress",
                        *(("same_question_requires_strategy_change",) if repeated_question else ()),
                    )
                    if status == TaskProgressStatus.NO_PROGRESS
                    else ("neutral_turn_does_not_change_progress",)
                ),
            )
        )
    return tuple(results)
