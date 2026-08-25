"""Pure deterministic reducer for the V2 dialogue state."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.agents.semantic_interpreter import TurnUnderstanding
from app.catalog_v2.contracts import CandidateStatus, CatalogPlanningResult
from app.commerce_v2.contracts import (
    CapabilityMode,
    CommerceExecutionResult,
    CommerceExecutionStatus,
    CommerceFieldStatus,
    CommercePlanningResult,
    CommerceWorkflowStatus,
    OutboxStatus,
    SensitiveValueRef,
    WorkflowControlKind,
    WorkflowControlSignal,
)
from app.commerce_v2.registry import (
    canonical_commerce_field_name,
    sensitive_value_kind,
)
from app.pii import redact_pii_for_model

from .contracts import (
    Ambiguity,
    AmbiguityRegistered,
    CatalogCandidateRejected,
    CatalogNoMatchRecorded,
    CatalogPlanCreated,
    CatalogRelaxationRecorded,
    CommerceCapabilityBoundaryRecorded,
    CommerceCommandIgnoredAsDuplicate,
    CommerceCommandPrepared,
    CommerceConsentChanged,
    CommerceDeliveryConfirmed,
    CommerceDeliveryFailed,
    CommerceDeliveryUnknown,
    CommerceLocalDraftRecorded,
    CommercePayloadRevised,
    CommercePreviewPrepared,
    CommerceSensitiveFactLinked,
    CommerceWorkflowControlRegistered,
    CommerceWorkflowCreated,
    ConstraintAdded,
    ConstraintCorrected,
    ConstraintDeferred,
    ConstraintFactV2,
    ConstraintMarkedUnknown,
    ConstraintPolarity,
    ConstraintRefused,
    ConstraintStatus,
    ConstraintStrength,
    CustomerTask,
    DiagnosticConflict,
    DialogueStateV2,
    DirectQuestion,
    DirectQuestionRegistered,
    NextActionPlan,
    PolicyDecisionRecorded,
    ProductContractResolved,
    ProductCategory,
    ProductGoal,
    ProductGoalConfirmed,
    ProductGoalCorrected,
    ProductRole,
    ProgressKind,
    ProgressState,
    ReductionResult,
    RejectedProposal,
    TaskAct,
    TaskCompleted,
    TaskCreated,
    TaskResumed,
    TaskStack,
    TaskStatus,
    TaskSuspended,
    TaskReadinessAssessed,
    TurnIgnoredAsDuplicate,
    TurnMetadata,
    SolutionPlanCreated,
)


_DIRECT_ACTS = {
    TaskAct.CHECK_PRICE,
    TaskAct.CHECK_STOCK,
    TaskAct.GET_LINK,
    TaskAct.REQUEST_QUOTE,
    TaskAct.REQUEST_INVOICE,
    TaskAct.RESERVE_PRODUCT,
    TaskAct.PLACE_ORDER,
    TaskAct.MODIFY_ORDER,
    TaskAct.CANCEL_ORDER,
    TaskAct.ORDER_STATUS,
    TaskAct.CHECK_DELIVERY,
    TaskAct.RETURN_PRODUCT,
    TaskAct.WARRANTY,
    TaskAct.COMPLAINT,
    TaskAct.CONTACT_STORE,
}


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _short_evidence(value: str) -> str:
    # SemanticInterpreter has already redacted PII and validated this excerpt
    # against the current turn.  The reducer keeps only the bounded excerpt,
    # never the complete message or dialogue history.
    return redact_pii_for_model(str(value or ""))[:240]


def _task_act(value: object) -> TaskAct:
    raw = getattr(value, "value", value)
    try:
        return TaskAct(str(raw))
    except ValueError:
        return TaskAct.OTHER


def _product_role(value: object) -> ProductRole:
    raw = getattr(value, "value", value)
    try:
        return ProductRole(str(raw))
    except ValueError:
        return ProductRole.UNKNOWN


def _product_category(value: object) -> ProductCategory:
    raw = getattr(value, "value", value)
    try:
        return ProductCategory(str(raw))
    except ValueError:
        return ProductCategory.OTHER


def _constraint_status(value: object) -> ConstraintStatus:
    raw = getattr(value, "value", value)
    return ConstraintStatus(str(raw))


def _constraint_polarity(value: object) -> ConstraintPolarity:
    raw = getattr(value, "value", value)
    return ConstraintPolarity(str(raw))


def _same_goal(goal: ProductGoal, canonical_type: str | None, category: ProductCategory) -> bool:
    if canonical_type and goal.canonical_type:
        return canonical_type.casefold() == goal.canonical_type.casefold()
    return category != ProductCategory.OTHER and category == goal.category


def _goal_by_id(goals: list[ProductGoal], goal_id: str | None) -> ProductGoal | None:
    return next((goal for goal in goals if goal.goal_id == goal_id), None)


def _replace_goal(goals: list[ProductGoal], replacement: ProductGoal) -> None:
    for index, goal in enumerate(goals):
        if goal.goal_id == replacement.goal_id:
            goals[index] = replacement
            return
    goals.append(replacement)


def _replace_task(tasks: list[CustomerTask], replacement: CustomerTask) -> None:
    for index, task in enumerate(tasks):
        if task.task_id == replacement.task_id:
            tasks[index] = replacement
            return
    tasks.append(replacement)


def _progress(changes: Iterable[ProgressKind], turn_number: int) -> ProgressState:
    unique = tuple(dict.fromkeys(changes))
    if not unique:
        return ProgressState(
            primary=ProgressKind.NO_PROGRESS,
            changes=(ProgressKind.NO_PROGRESS,),
            reason_codes=("no_state_change",),
            source_turn=turn_number,
        )
    priority = {
        ProgressKind.TASK_RETURNED: 100,
        ProgressKind.TASK_SWITCHED: 90,
        ProgressKind.DIRECT_QUESTION_REGISTERED: 80,
        ProgressKind.CONSTRAINT_CORRECTED: 70,
        ProgressKind.UNKNOWN_REGISTERED: 65,
        ProgressKind.CONSTRAINT_ADDED: 60,
        ProgressKind.GOAL_REFINED: 50,
        ProgressKind.NEW_TASK_CREATED: 40,
        ProgressKind.DIRECT_QUESTION_ANSWERED: 30,
        ProgressKind.NO_PROGRESS: 0,
    }
    primary = max(unique, key=lambda item: priority[item])
    return ProgressState(
        primary=primary,
        changes=unique,
        reason_codes=tuple(f"progress:{item.value}" for item in unique),
        source_turn=turn_number,
    )


def _stack(tasks: list[CustomerTask], preferred_active: str | None) -> TaskStack:
    active = next(
        (
            task.task_id
            for task in tasks
            if task.task_id == preferred_active
            and task.status == TaskStatus.IN_PROGRESS
        ),
        None,
    )
    if active is None:
        active = next(
            (task.task_id for task in tasks if task.status == TaskStatus.IN_PROGRESS),
            None,
        )
    return TaskStack(
        active_task_id=active,
        pending_task_ids=tuple(
            task.task_id
            for task in tasks
            if task.status in {TaskStatus.PENDING, TaskStatus.BLOCKED}
        ),
        suspended_task_ids=tuple(
            task.task_id for task in tasks if task.status == TaskStatus.SUSPENDED
        ),
        completed_task_ids=tuple(
            task.task_id
            for task in tasks
            if task.status in {
                TaskStatus.SATISFIED,
                TaskStatus.CANCELLED,
            }
        ),
    )


def _suspend_goal_tasks(
    tasks: list[CustomerTask],
    goal_id: str | None,
    *,
    metadata: TurnMetadata,
    turn_number: int,
    events: list[object],
    reason_code: str,
) -> None:
    if goal_id is None:
        return
    for task in tuple(tasks):
        if task.target_goal_id != goal_id or task.status not in {
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
        }:
            continue
        _replace_task(
            tasks,
            task.model_copy(
                update={
                    "status": TaskStatus.SUSPENDED,
                    "blocking_reason": reason_code,
                }
            ),
        )
        events.append(
            TaskSuspended(
                turn_id=metadata.turn_id,
                turn_number=turn_number,
                task_id=task.task_id,
                reason_code=reason_code,
            )
        )


def _resume_goal_tasks(
    tasks: list[CustomerTask],
    goal_id: str,
    *,
    metadata: TurnMetadata,
    turn_number: int,
    events: list[object],
) -> str | None:
    resumable = [
        task for task in tasks
        if task.target_goal_id == goal_id and task.status == TaskStatus.SUSPENDED
    ]
    active: str | None = None
    for index, task in enumerate(resumable):
        status = TaskStatus.IN_PROGRESS if index == 0 else TaskStatus.PENDING
        _replace_task(
            tasks,
            task.model_copy(update={"status": status, "blocking_reason": None}),
        )
        events.append(
            TaskResumed(
                turn_id=metadata.turn_id,
                turn_number=turn_number,
                task_id=task.task_id,
            )
        )
        if index == 0:
            active = task.task_id
    return active


def _find_return_goal(
    goals: list[ProductGoal],
    tasks: list[CustomerTask],
    targets: list[tuple[int, object]],
) -> ProductGoal | None:
    suspended_goal_ids = {
        task.target_goal_id
        for task in tasks
        if task.status == TaskStatus.SUSPENDED and task.target_goal_id
    }
    if targets:
        mention = targets[0][1]
        category = _product_category(mention.category)
        for goal in reversed(goals):
            if goal.goal_id in suspended_goal_ids and _same_goal(
                goal,
                mention.canonical_type,
                category,
            ):
                return goal
    for goal in reversed(goals):
        if goal.goal_id in suspended_goal_ids:
            return goal
    return None


def reduce_dialogue_state(
    previous_state: DialogueStateV2 | None,
    turn_understanding: TurnUnderstanding,
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Return a new immutable state from one accepted semantic turn."""

    previous = previous_state or DialogueStateV2()
    if turn_metadata.turn_id in previous.applied_turn_ids:
        progress = _progress((), previous.turn_number)
        return ReductionResult(
            state=previous,
            events=(
                TurnIgnoredAsDuplicate(
                    turn_id=turn_metadata.turn_id,
                    turn_number=previous.turn_number,
                ),
            ),
            progress=progress,
        )

    turn_number = previous.turn_number + 1
    operation = turn_understanding.operation.value
    goals = list(previous.product_goals)
    tasks = list(previous.tasks)
    constraints = list(previous.constraints)
    commerce_sensitive_values = list(previous.commerce_sensitive_values)
    commerce_controls = list(previous.commerce_controls)
    questions = list(previous.direct_questions)
    ambiguities = list(previous.ambiguities)
    events: list[object] = []
    rejected: list[RejectedProposal] = []
    conflicts: list[DiagnosticConflict] = []
    progress_changes: list[ProgressKind] = []
    active_goal_id = previous.active_goal_id
    preferred_active_task = previous.task_stack.active_task_id
    activated_target_this_turn = False

    for control_index, semantic_control in enumerate(
        turn_understanding.workflow_controls
    ):
        control_id = _stable_id(
            "commerce_control",
            turn_metadata.turn_id,
            control_index,
            semantic_control.kind.value,
        )
        control = WorkflowControlSignal(
            control_id=control_id,
            kind=WorkflowControlKind(semantic_control.kind.value),
            source_turn=turn_number,
            source=turn_metadata.source,
        )
        commerce_controls.append(control)
        events.append(
            CommerceWorkflowControlRegistered(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                control_id=control_id,
                control_kind=control.kind,
            )
        )

    targets = [
        (index, mention)
        for index, mention in enumerate(turn_understanding.products)
        if _product_role(mention.role) == ProductRole.TARGET
    ]

    if operation == "cancel":
        for task in tuple(tasks):
            if task.task_id != previous.task_stack.active_task_id:
                continue
            _replace_task(
                tasks,
                task.model_copy(
                    update={
                        "status": TaskStatus.CANCELLED,
                        "blocking_reason": "cancelled_by_customer",
                    }
                ),
            )
            events.append(
                TaskCompleted(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    task_id=task.task_id,
                    final_status=TaskStatus.CANCELLED,
                )
            )
            preferred_active_task = None

    mention_goal_ids: dict[int, str] = {}
    return_goal: ProductGoal | None = None
    if operation == "return":
        return_goal = _find_return_goal(goals, tasks, targets)
        if return_goal is None:
            rejected.append(
                RejectedProposal(
                    proposal_type="return",
                    reason_code="return_target_not_found",
                )
            )
        else:
            if active_goal_id and active_goal_id != return_goal.goal_id:
                _suspend_goal_tasks(
                    tasks,
                    active_goal_id,
                    metadata=turn_metadata,
                    turn_number=turn_number,
                    events=events,
                    reason_code="task_returned_elsewhere",
                )
            active_goal_id = return_goal.goal_id
            preferred_active_task = _resume_goal_tasks(
                tasks,
                return_goal.goal_id,
                metadata=turn_metadata,
                turn_number=turn_number,
                events=events,
            )
            progress_changes.append(ProgressKind.TASK_RETURNED)
            if targets:
                mention_goal_ids[targets[0][0]] = return_goal.goal_id

    for mention_index, mention in enumerate(turn_understanding.products):
        role = _product_role(mention.role)
        category = _product_category(mention.category)
        canonical_type = mention.canonical_type or None

        if mention_index in mention_goal_ids:
            continue

        active_goal = _goal_by_id(goals, active_goal_id)
        if role == ProductRole.TARGET and operation == "correct" and active_goal:
            changed: list[str] = []
            update: dict[str, object] = {
                "role": ProductRole.TARGET,
                "evidence": _short_evidence(mention.evidence),
                "source": turn_metadata.source,
                "confidence": turn_understanding.confidence,
                "confirmed_turn": turn_number,
            }
            if canonical_type and canonical_type != active_goal.canonical_type:
                update.update({"canonical_type": canonical_type, "type_locked": True})
                changed.append("canonical_type")
            if category != ProductCategory.OTHER and category != active_goal.category:
                update.update({"category": category, "category_locked": True})
                changed.append("category")
            corrected = active_goal.model_copy(update=update)
            _replace_goal(goals, corrected)
            mention_goal_ids[mention_index] = corrected.goal_id
            if changed:
                events.append(
                    ProductGoalCorrected(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        goal_id=corrected.goal_id,
                        changed_fields=tuple(changed),
                    )
                )
                progress_changes.append(ProgressKind.GOAL_REFINED)
            continue

        if role == ProductRole.TARGET and operation == "return" and return_goal:
            mention_goal_ids[mention_index] = return_goal.goal_id
            continue

        if role == ProductRole.TARGET and active_goal and _same_goal(
            active_goal,
            canonical_type,
            category,
        ):
            updates: dict[str, object] = {}
            changed: list[str] = []
            if canonical_type and not active_goal.canonical_type:
                updates.update({"canonical_type": canonical_type, "type_locked": True})
                changed.append("canonical_type")
            if category != ProductCategory.OTHER and active_goal.category == ProductCategory.OTHER:
                updates.update({"category": category, "category_locked": True})
                changed.append("category")
            if updates:
                updates.update(
                    {
                        "evidence": _short_evidence(mention.evidence),
                        "source": turn_metadata.source,
                        "confidence": turn_understanding.confidence,
                        "confirmed_turn": turn_number,
                    }
                )
                active_goal = active_goal.model_copy(update=updates)
                _replace_goal(goals, active_goal)
                events.append(
                    ProductGoalCorrected(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        goal_id=active_goal.goal_id,
                        changed_fields=tuple(changed),
                    )
                )
                progress_changes.append(ProgressKind.GOAL_REFINED)
            mention_goal_ids[mention_index] = active_goal.goal_id
            continue

        goal_id = _stable_id("goal", turn_metadata.turn_id, mention_index)
        goal = ProductGoal(
            goal_id=goal_id,
            canonical_type=canonical_type,
            category=category,
            role=role,
            evidence=_short_evidence(mention.evidence),
            source=turn_metadata.source,
            confidence=turn_understanding.confidence,
            confirmed_turn=turn_number,
            type_locked=bool(canonical_type and role == ProductRole.TARGET),
            category_locked=bool(
                category != ProductCategory.OTHER and role == ProductRole.TARGET
            ),
        )
        goals.append(goal)
        mention_goal_ids[mention_index] = goal_id
        events.append(
            ProductGoalConfirmed(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                goal_id=goal_id,
                role=role,
            )
        )

        if role != ProductRole.TARGET:
            continue
        if active_goal is None:
            active_goal_id = goal_id
            activated_target_this_turn = True
            progress_changes.append(ProgressKind.GOAL_REFINED)
            continue
        if operation in {"switch", "new"}:
            if activated_target_this_turn:
                # A compound request may name several equally explicit target
                # products.  Keep the first as the active focus and the rest
                # as linked pending tasks instead of repeatedly applying
                # "last product wins" inside one semantic frame.
                continue
            _suspend_goal_tasks(
                tasks,
                active_goal_id,
                metadata=turn_metadata,
                turn_number=turn_number,
                events=events,
                reason_code="explicit_task_switch",
            )
            active_goal_id = goal_id
            activated_target_this_turn = True
            preferred_active_task = None
            progress_changes.append(ProgressKind.TASK_SWITCHED)
            continue

        # A second target is retained as its own goal/task, but an implicit
        # interpretation cannot overwrite the locked active target.
        rejected.append(
            RejectedProposal(
                proposal_type="active_product_goal",
                reason_code="confirmed_target_requires_explicit_operation",
                evidence=_short_evidence(mention.evidence),
                details={"retained_goal_id": goal_id},
            )
        )
        conflicts.append(
            DiagnosticConflict(
                conflict_type="product_goal",
                reason_code="implicit_target_conflicts_with_locked_goal",
                existing_id=active_goal.goal_id,
                proposed_value=canonical_type or category.value,
            )
        )

    target_goal_ids = [
        mention_goal_ids[index]
        for index, mention in enumerate(turn_understanding.products)
        if _product_role(mention.role) == ProductRole.TARGET
        and index in mention_goal_ids
    ]
    target_goal_ids = list(dict.fromkeys(target_goal_ids))
    if not target_goal_ids and active_goal_id:
        target_goal_ids = [active_goal_id]

    created_tasks: list[CustomerTask] = []
    for act_index, semantic_act in enumerate(turn_understanding.acts):
        act = _task_act(semantic_act)
        task_goal_ids: list[str | None] = target_goal_ids or [None]
        for goal_position, goal_id in enumerate(task_goal_ids):
            existing_task = next(
                (
                    task
                    for task in tasks
                    if task.act == act
                    and task.target_goal_id == goal_id
                    and task.status
                    not in {TaskStatus.SATISFIED, TaskStatus.CANCELLED}
                ),
                None,
            )
            if (
                existing_task is not None
                and act in {TaskAct.FIND, TaskAct.SELECT}
                and operation in {"continue", "refine", "correct", "return"}
            ):
                rejected.append(
                    RejectedProposal(
                        proposal_type="task",
                        reason_code="existing_selection_task_reused",
                        details={"existing_task_id": existing_task.task_id},
                    )
                )
                continue
            task_id = _stable_id(
                "task",
                turn_metadata.turn_id,
                act_index,
                goal_position,
                goal_id,
            )
            status = (
                TaskStatus.IN_PROGRESS
                if preferred_active_task is None and not created_tasks
                else TaskStatus.PENDING
            )
            task = CustomerTask(
                task_id=task_id,
                act=act,
                target_goal_id=goal_id,
                priority=act_index * 100 + goal_position,
                status=status,
                source=turn_metadata.source,
                source_turn=turn_number,
            )
            tasks.append(task)
            created_tasks.append(task)
            events.append(
                TaskCreated(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    task_id=task_id,
                    act=act,
                    goal_id=goal_id,
                )
            )
            progress_changes.append(ProgressKind.NEW_TASK_CREATED)
            if status == TaskStatus.IN_PROGRESS:
                preferred_active_task = task_id

    if len(created_tasks) > 1:
        related_ids = tuple(task.task_id for task in created_tasks)
        for task in created_tasks:
            replacement = task.model_copy(
                update={
                    "related_task_ids": tuple(
                        task_id for task_id in related_ids if task_id != task.task_id
                    )
                }
            )
            _replace_task(tasks, replacement)
        created_tasks = [
            next(task for task in tasks if task.task_id == created.task_id)
            for created in created_tasks
        ]

    for task in created_tasks:
        if task.act not in _DIRECT_ACTS:
            continue
        question_id = _stable_id("question", task.task_id)
        question = DirectQuestion(
            question_id=question_id,
            act=task.act,
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            source_turn=turn_number,
        )
        questions.append(question)
        events.append(
            DirectQuestionRegistered(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                question_id=question_id,
                task_id=task.task_id,
                act=task.act,
            )
        )
        progress_changes.append(ProgressKind.DIRECT_QUESTION_REGISTERED)

    for ambiguity_index, semantic_ambiguity in enumerate(turn_understanding.ambiguities):
        ambiguity_id = _stable_id(
            "ambiguity", turn_metadata.turn_id, ambiguity_index
        )
        ambiguity = Ambiguity(
            ambiguity_id=ambiguity_id,
            kind=semantic_ambiguity.kind,
            description=semantic_ambiguity.description,
            evidence=_short_evidence(semantic_ambiguity.evidence),
            source_turn=turn_number,
        )
        ambiguities.append(ambiguity)
        events.append(
            AmbiguityRegistered(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                ambiguity_id=ambiguity_id,
                kind=ambiguity.kind,
            )
        )

    for constraint_index, semantic_fact in enumerate(turn_understanding.constraints):
        status = _constraint_status(semantic_fact.status)
        polarity = _constraint_polarity(semantic_fact.polarity)
        strength = (
            ConstraintStrength.SOFT
            if polarity == ConstraintPolarity.PREFERRED
            else ConstraintStrength.HARD
        )
        goal_id = (
            mention_goal_ids.get(semantic_fact.applies_to_product)
            if semantic_fact.applies_to_product is not None
            else active_goal_id
        )
        task_id = next(
            (
                task.task_id
                for task in created_tasks
                if task.target_goal_id == goal_id
            ),
            preferred_active_task,
        )
        canonical_field = canonical_commerce_field_name(semantic_fact.name)
        sensitive_kind = sensitive_value_kind(canonical_field)
        if sensitive_kind is not None:
            existing_sensitive = next(
                (
                    item
                    for item in reversed(commerce_sensitive_values)
                    if item.active and item.field_name == canonical_field
                ),
                None,
            )
            commerce_status = CommerceFieldStatus(status.value)
            if (
                existing_sensitive is not None
                and existing_sensitive.status == commerce_status
                and operation != "correct"
            ):
                rejected.append(
                    RejectedProposal(
                        proposal_type="commerce_sensitive_fact",
                        reason_code="duplicate_sensitive_fact_reference",
                        details={"existing_ref_id": existing_sensitive.ref_id},
                    )
                )
                continue
            ref_id = _stable_id(
                "sensitive_ref",
                turn_metadata.turn_id,
                constraint_index,
                canonical_field,
            )
            if existing_sensitive is not None:
                commerce_sensitive_values = [
                    (
                        item.model_copy(update={"active": False})
                        if item.ref_id == existing_sensitive.ref_id
                        else item
                    )
                    for item in commerce_sensitive_values
                ]
            commerce_sensitive_values.append(
                SensitiveValueRef(
                    ref_id=ref_id,
                    kind=sensitive_kind,
                    field_name=canonical_field,
                    status=commerce_status,
                    source=turn_metadata.source,
                    source_turn=turn_number,
                    replaces_ref_id=(
                        existing_sensitive.ref_id if existing_sensitive else None
                    ),
                )
            )
            events.append(
                CommerceSensitiveFactLinked(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    ref_id=ref_id,
                    field_name=canonical_field,
                )
            )
            progress_changes.append(
                ProgressKind.UNKNOWN_REGISTERED
                if commerce_status != CommerceFieldStatus.KNOWN
                else ProgressKind.CONSTRAINT_ADDED
            )
            continue
        existing = next(
            (
                fact
                for fact in reversed(constraints)
                if fact.active
                and fact.name == semantic_fact.name
                and fact.goal_id == goal_id
            ),
            None,
        )
        same_as_existing = bool(
            existing
            and existing.value == semantic_fact.value
            and existing.unit == semantic_fact.unit
            and existing.status == status
            and existing.polarity == polarity
        )
        if same_as_existing:
            rejected.append(
                RejectedProposal(
                    proposal_type="constraint",
                    reason_code="duplicate_constraint_fact",
                    evidence=_short_evidence(semantic_fact.evidence),
                    details={"existing_fact_id": existing.fact_id},
                )
            )
            continue

        if (
            existing
            and existing.status == ConstraintStatus.KNOWN
            and status == ConstraintStatus.KNOWN
            and operation != "correct"
        ):
            rejected.append(
                RejectedProposal(
                    proposal_type="constraint",
                    reason_code="confirmed_fact_requires_explicit_correction",
                    evidence=_short_evidence(semantic_fact.evidence),
                    details={"existing_fact_id": existing.fact_id},
                )
            )
            conflicts.append(
                DiagnosticConflict(
                    conflict_type="constraint",
                    reason_code="known_value_conflict",
                    existing_id=existing.fact_id,
                    proposed_value=semantic_fact.value,
                )
            )
            continue

        fact_id = _stable_id(
            "fact", turn_metadata.turn_id, constraint_index, goal_id
        )
        fact = ConstraintFactV2(
            fact_id=fact_id,
            name=semantic_fact.name,
            value=semantic_fact.value if status == ConstraintStatus.KNOWN else None,
            unit=semantic_fact.unit,
            status=status,
            polarity=polarity,
            strength=strength,
            evidence=_short_evidence(semantic_fact.evidence),
            source=(
                "pending_question_answer"
                if turn_understanding.answers_pending_question
                else turn_metadata.source
            ),
            confidence=turn_understanding.confidence,
            goal_id=goal_id,
            task_id=task_id,
            source_turn=turn_number,
            replaces_fact_id=existing.fact_id if existing else None,
        )
        if existing:
            _replace_index = next(
                index for index, item in enumerate(constraints)
                if item.fact_id == existing.fact_id
            )
            constraints[_replace_index] = existing.model_copy(update={"active": False})
        constraints.append(fact)

        event_kwargs = {
            "turn_id": turn_metadata.turn_id,
            "turn_number": turn_number,
            "fact_id": fact_id,
            "name": fact.name,
        }
        if status == ConstraintStatus.UNKNOWN:
            events.append(ConstraintMarkedUnknown(**event_kwargs))
            progress_changes.append(ProgressKind.UNKNOWN_REGISTERED)
        elif status == ConstraintStatus.REFUSED:
            events.append(ConstraintRefused(**event_kwargs))
            progress_changes.append(ProgressKind.UNKNOWN_REGISTERED)
        elif status == ConstraintStatus.DEFERRED:
            events.append(ConstraintDeferred(**event_kwargs))
            progress_changes.append(ProgressKind.UNKNOWN_REGISTERED)
        elif existing:
            events.append(
                ConstraintCorrected(
                    **event_kwargs,
                    replaced_fact_id=existing.fact_id,
                )
            )
            progress_changes.append(ProgressKind.CONSTRAINT_CORRECTED)
        else:
            events.append(ConstraintAdded(**event_kwargs))
            progress_changes.append(ProgressKind.CONSTRAINT_ADDED)

    progress = _progress(progress_changes, turn_number)
    state = DialogueStateV2(
        turn_number=turn_number,
        task_stack=_stack(tasks, preferred_active_task),
        tasks=tuple(tasks),
        product_goals=tuple(goals),
        active_goal_id=active_goal_id,
        constraints=tuple(constraints),
        direct_questions=tuple(questions),
        ambiguities=tuple(ambiguities),
        progress=progress,
        last_policy=previous.last_policy,
        catalog_planning=previous.catalog_planning,
        commerce_workflows=previous.commerce_workflows,
        commerce_sensitive_values=tuple(commerce_sensitive_values),
        commerce_controls=tuple(commerce_controls[-100:]),
        commerce_outbox=previous.commerce_outbox,
        commerce_planning=previous.commerce_planning,
        applied_turn_ids=(*previous.applied_turn_ids, turn_metadata.turn_id),
    )
    return ReductionResult(
        state=state,
        events=tuple(events),
        rejected_proposals=tuple(rejected),
        progress=progress,
        conflicts=tuple(conflicts),
    )


def record_policy_decision(
    reduction: ReductionResult,
    plan: NextActionPlan,
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Record a policy choice through the reducer's only mutation boundary."""

    state = reduction.state.model_copy(update={"last_policy": plan})
    event = PolicyDecisionRecorded(
        turn_id=turn_metadata.turn_id,
        turn_number=state.turn_number,
        primary=plan.primary.kind,
        secondary=plan.secondary.kind if plan.secondary else None,
    )
    return reduction.model_copy(
        update={
            "state": state,
            "events": (*reduction.events, event),
        }
    )


def record_catalog_planning(
    reduction: ReductionResult,
    planning: CatalogPlanningResult,
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Apply a completed shadow catalogue decision at the reducer boundary."""

    events: list[object] = list(reduction.events)
    turn_number = reduction.state.turn_number
    for resolution in planning.contract_resolutions:
        if resolution.contract_id is not None:
            events.append(
                ProductContractResolved(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    task_id=resolution.task_id,
                    contract_id=resolution.contract_id,
                    product_kind=resolution.product_kind,
                )
            )
    for assessment in planning.readiness_assessments:
        events.append(
            TaskReadinessAssessed(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                task_id=assessment.task_id,
                status=assessment.status,
            )
        )
    for search_plan in planning.search_plans:
        events.append(
            CatalogPlanCreated(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                task_id=search_plan.task_id,
                plan_id=search_plan.plan_id,
            )
        )
        for candidate in search_plan.candidate_assessments:
            if candidate.status == CandidateStatus.REJECTED:
                events.append(
                    CatalogCandidateRejected(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        task_id=search_plan.task_id,
                        sku=candidate.sku,
                        reason_codes=candidate.reason_codes,
                    )
                )
            for relaxation in candidate.relaxations:
                events.append(
                    CatalogRelaxationRecorded(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        task_id=search_plan.task_id,
                        sku=candidate.sku,
                        fact_name=relaxation.fact_name,
                    )
                )
        if "honest_no_match" in {
            stage.value for stage in search_plan.stages
        }:
            events.append(
                CatalogNoMatchRecorded(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    task_id=search_plan.task_id,
                    reason_code="no_verified_contract_match",
                )
            )
    if planning.solution_plan is not None:
        events.append(
            SolutionPlanCreated(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                solution_id=planning.solution_plan.solution_id,
                task_ids=planning.solution_plan.task_ids,
            )
        )
    state = reduction.state.model_copy(update={"catalog_planning": planning})
    return reduction.model_copy(update={"state": state, "events": tuple(events)})


def record_commerce_planning(
    reduction: ReductionResult,
    planning: CommercePlanningResult,
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Apply one complete commerce shadow decision at the reducer boundary."""

    events: list[object] = list(reduction.events)
    before = {item.workflow_id: item for item in reduction.state.commerce_workflows}
    for workflow in planning.workflows:
        previous = before.get(workflow.workflow_id)
        if previous is None:
            events.append(
                CommerceWorkflowCreated(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    workflow_id=workflow.workflow_id,
                    workflow_kind=workflow.workflow_kind,
                )
            )
        elif previous.payload_revision != workflow.payload_revision:
            events.append(
                CommercePayloadRevised(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    workflow_id=workflow.workflow_id,
                    payload_revision=workflow.payload_revision,
                )
            )
        if workflow.preview_revision is not None and (
            previous is None or previous.preview_revision != workflow.preview_revision
        ):
            events.append(
                CommercePreviewPrepared(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    workflow_id=workflow.workflow_id,
                    payload_revision=workflow.preview_revision,
                )
            )
        if previous is None or previous.consent.status != workflow.consent.status:
            events.append(
                CommerceConsentChanged(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    workflow_id=workflow.workflow_id,
                    consent_status=workflow.consent.status,
                )
            )
    for boundary in planning.capability_boundaries:
        workflow_id, _, reason = boundary.partition(":")
        events.append(
            CommerceCapabilityBoundaryRecorded(
                turn_id=turn_metadata.turn_id,
                turn_number=reduction.state.turn_number,
                workflow_id=workflow_id,
                reason_code=reason or "commerce_capability_boundary",
            )
        )
    for command in planning.prepared_commands:
        events.append(
            CommerceCommandPrepared(
                turn_id=turn_metadata.turn_id,
                turn_number=reduction.state.turn_number,
                workflow_id=command.workflow_id,
                command_id=command.command_id,
                payload_revision=command.payload_revision,
            )
        )
    for rejected in planning.rejected_proposals:
        if rejected.reason_code == "duplicate_command_ignored" and rejected.workflow_id:
            events.append(
                CommerceCommandIgnoredAsDuplicate(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    workflow_id=rejected.workflow_id,
                )
            )
    state = reduction.state.model_copy(
        update={
            "commerce_workflows": planning.workflows,
            "commerce_controls": planning.controls,
            "commerce_outbox": planning.outbox,
            "commerce_planning": planning,
        }
    )
    return reduction.model_copy(update={"state": state, "events": tuple(events)})


def record_commerce_execution_result(
    reduction: ReductionResult,
    result: CommerceExecutionResult,
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Record a gateway result; gateway code never mutates dialogue state."""

    entry = next(
        (
            item
            for item in reduction.state.commerce_outbox
            if item.command.command_id == result.command_id
        ),
        None,
    )
    if entry is None:
        return reduction.model_copy(
            update={
                "rejected_proposals": (
                    *reduction.rejected_proposals,
                    RejectedProposal(
                        proposal_type="commerce_execution",
                        reason_code="commerce_command_not_found",
                    ),
                )
            }
        )
    if result.capability_id != entry.command.capability_id:
        return reduction.model_copy(
            update={
                "rejected_proposals": (
                    *reduction.rejected_proposals,
                    RejectedProposal(
                        proposal_type="commerce_execution",
                        reason_code="commerce_execution_capability_mismatch",
                    ),
                )
            }
        )
    workflows = list(reduction.state.commerce_workflows)
    workflow = next(
        item for item in workflows if item.workflow_id == entry.command.workflow_id
    )
    if result.status == CommerceExecutionStatus.DELIVERED and (
        workflow.capability_mode != CapabilityMode.TRANSACTIONAL_EXTERNAL
        or not result.receipt_verified
    ):
        return reduction.model_copy(
            update={
                "rejected_proposals": (
                    *reduction.rejected_proposals,
                    RejectedProposal(
                        proposal_type="commerce_execution",
                        reason_code="unverified_transactional_delivery_result",
                    ),
                )
            }
        )
    event: object
    if result.status == CommerceExecutionStatus.LOCAL_DRAFT_SAVED:
        workflow_status = CommerceWorkflowStatus.LOCAL_DRAFT_SAVED
        outbox_status = OutboxStatus.ACKNOWLEDGED
        event = CommerceLocalDraftRecorded(
            turn_id=turn_metadata.turn_id,
            turn_number=reduction.state.turn_number,
            workflow_id=workflow.workflow_id,
            command_id=result.command_id,
        )
    elif result.status == CommerceExecutionStatus.DELIVERED:
        workflow_status = CommerceWorkflowStatus.DELIVERED
        outbox_status = OutboxStatus.ACKNOWLEDGED
        event = CommerceDeliveryConfirmed(
            turn_id=turn_metadata.turn_id,
            turn_number=reduction.state.turn_number,
            workflow_id=workflow.workflow_id,
            command_id=result.command_id,
            receipt_ref=result.receipt_ref or "",
        )
    elif result.status == CommerceExecutionStatus.DELIVERY_UNKNOWN:
        workflow_status = CommerceWorkflowStatus.DELIVERY_UNKNOWN
        outbox_status = OutboxStatus.DELIVERY_UNKNOWN
        event = CommerceDeliveryUnknown(
            turn_id=turn_metadata.turn_id,
            turn_number=reduction.state.turn_number,
            workflow_id=workflow.workflow_id,
            command_id=result.command_id,
            reason_code=result.reason_code,
        )
    else:
        workflow_status = CommerceWorkflowStatus.DELIVERY_FAILED
        outbox_status = OutboxStatus.FAILED
        event = CommerceDeliveryFailed(
            turn_id=turn_metadata.turn_id,
            turn_number=reduction.state.turn_number,
            workflow_id=workflow.workflow_id,
            command_id=result.command_id,
            reason_code=result.reason_code,
        )
    workflows = [
        (
            item.model_copy(
                update={
                    "status": workflow_status,
                    "execution_status": result.status,
                    "external_receipt_ref": result.receipt_ref,
                }
            )
            if item.workflow_id == workflow.workflow_id
            else item
        )
        for item in workflows
    ]
    outbox = tuple(
        (
            item.model_copy(
                update={
                    "status": outbox_status,
                    "receipt_ref": result.receipt_ref,
                    "last_reason_code": result.reason_code,
                }
            )
            if item.command.command_id == result.command_id
            else item
        )
        for item in reduction.state.commerce_outbox
    )
    state = reduction.state.model_copy(
        update={"commerce_workflows": tuple(workflows), "commerce_outbox": outbox}
    )
    return reduction.model_copy(
        update={"state": state, "events": (*reduction.events, event)}
    )
