"""Pure deterministic reducer for the V2 dialogue state."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.agents.domain_ontology import (
    CONSTRAINT_FACT_ONTOLOGY,
    PRODUCT_TYPE_ONTOLOGY,
    RANGE_CAPABLE_CONSTRAINT_FACTS,
)
from app.agents.engineering_requirements import EngineeringRequirementsAgent
from app.agents.semantic_interpreter import TurnUnderstanding
from app.answer_v2.contracts import (
    AnswerPlanningResult,
    AnswerValidationResult,
    NextStepKind,
    TaskProgressAssessment,
)
from app.catalog_v2.contracts import CandidateStatus, CatalogPlanningResult
from app.catalog_v2.normalization import (
    normalize_unit_label,
    parse_numeric_choice_value,
    parse_numeric_range_value,
)
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
    AnswerPlanCreated,
    AnswerPlanRejected,
    AnswerPlanSummary,
    AnswerPlanValidated,
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
    InformationOutputRelation,
    InformationPurpose,
    InformationRequestRegistered,
    InformationRequestResolved,
    InformationRequestStatus,
    InformationRequestUnavailable,
    InformationRequestV2,
    InformationSourceKind,
    InformationSubjectScope,
    NextActionPlan,
    PolicyDecisionRecorded,
    PresentedCandidateSummary,
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
    RequestedInformationOutput,
    SelectionControlKind,
    SelectionControlRegistered,
    SelectionControlSignal,
    TaskAct,
    TaskAddressed,
    TaskCompleted,
    TaskCreated,
    TaskResumed,
    TaskStack,
    TaskStatus,
    TaskSuspended,
    TaskReadinessAssessed,
    TaskProgressRecorded,
    TaskStrategyState,
    TurnIgnoredAsDuplicate,
    TurnMetadata,
    SolutionPlanCreated,
    ResponseStrategyEscalated,
    ResponseStrategyKind,
    ResponseStrategySelected,
    ShadowDeliveryStatus,
    ShadowResponseNotDelivered,
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


# These acts operate on catalogue products and therefore need an explicit
# task-to-goal association.  Semantic interpretation can legitimately encode
# the product being replaced as ``existing``, ``context`` or ``alternative``
# rather than inventing a second ``target`` mention.  The reducer resolves that
# typed relationship; it never inspects the raw customer message.
_PRODUCT_SCOPED_ACTS = {
    TaskAct.FIND,
    TaskAct.SELECT,
    TaskAct.COMPARE,
    TaskAct.COMPATIBILITY,
    TaskAct.CHECK_PRICE,
    TaskAct.CHECK_STOCK,
    TaskAct.GET_LINK,
}

_REPLACEMENT_ROLES = {
    ProductRole.EXISTING,
    ProductRole.ALTERNATIVE,
}

_DISCOVERY_ACTS = {TaskAct.FIND, TaskAct.SELECT}

_NON_TERMINAL_TASK_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
    TaskStatus.SUSPENDED,
}


def _constraint_numeric_interval(
    value: object,
) -> tuple[float, float] | None:
    """Return an explicit scalar/range as a closed numeric interval.

    The reducer uses this only to decide whether an already confirmed fact is
    being safely refined. It does not infer, convert or calculate values.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric, numeric
    parsed = parse_numeric_range_value(value)
    if parsed is None:
        return None
    return float(parsed[0]), float(parsed[1])


def _normalized_constraint_unit(unit: str | None) -> str | None:
    return normalize_unit_label(unit)


def _is_safe_known_fact_refinement(
    existing: ConstraintFactV2,
    *,
    proposed_value: object,
    proposed_unit: str | None,
    proposed_polarity: ConstraintPolarity,
) -> bool:
    """Allow only monotonic refinements of a confirmed typed fact.

    A normal ``refine`` turn may strengthen/soften the same value or replace a
    scalar with an explicitly stated range that still contains it (and the
    inverse narrowing operation). Disjoint values still require an explicit
    semantic correction, preserving the reducer's fail-closed contract.
    """

    if _normalized_constraint_unit(existing.unit) != _normalized_constraint_unit(
        proposed_unit
    ):
        return False
    if existing.value == proposed_value:
        return existing.polarity != proposed_polarity

    # Numeric containment is meaningful only for continuous engineering
    # magnitudes declared by the shared ontology.  Discrete cardinalities
    # (for example boiler circuits or collector ports) must still arrive as
    # an explicit correction instead of being silently widened/narrowed.
    if existing.name not in RANGE_CAPABLE_CONSTRAINT_FACTS:
        return False

    existing_choices = parse_numeric_choice_value(existing.value)
    proposed_choices = parse_numeric_choice_value(proposed_value)
    existing_values = (
        {float(item) for item in existing_choices}
        if existing_choices is not None
        else (
            {float(existing.value)}
            if isinstance(existing.value, (int, float))
            and not isinstance(existing.value, bool)
            else None
        )
    )
    proposed_values = (
        {float(item) for item in proposed_choices}
        if proposed_choices is not None
        else (
            {float(proposed_value)}
            if isinstance(proposed_value, (int, float))
            and not isinstance(proposed_value, bool)
            else None
        )
    )
    if existing_values is not None and proposed_values is not None:
        return bool(
            existing_values.issubset(proposed_values)
            or proposed_values.issubset(existing_values)
        )

    existing_interval = _constraint_numeric_interval(existing.value)
    proposed_interval = _constraint_numeric_interval(proposed_value)
    if existing_interval is None or proposed_interval is None:
        return False

    existing_inside_proposed = (
        proposed_interval[0] <= existing_interval[0]
        and existing_interval[1] <= proposed_interval[1]
    )
    proposed_inside_existing = (
        existing_interval[0] <= proposed_interval[0]
        and proposed_interval[1] <= existing_interval[1]
    )
    return existing_inside_proposed or proposed_inside_existing

_PRODUCT_ROLE_PRIORITY = {
    ProductRole.TARGET: 30,
    ProductRole.EXISTING: 20,
    ProductRole.ALTERNATIVE: 20,
    ProductRole.ACCESSORY: 15,
    ProductRole.CONTEXT: 10,
    ProductRole.UNKNOWN: 0,
}

# Applicability comes from the existing typed engineering memory schema, not
# from words in the current message.  A fact is rejected only when that schema
# positively assigns it to other product categories.  Unknown/new fact names
# remain unbound-compatible so an incomplete ontology cannot silently discard
# customer data.
_FACT_OWNER_CATEGORIES: dict[str, frozenset[str]] = {}
for _category_name, _category_fact_names in (
    EngineeringRequirementsAgent.CATEGORY_KEYS.items()
):
    for _fact_name in _category_fact_names:
        _FACT_OWNER_CATEGORIES[_fact_name] = frozenset(
            {
                *_FACT_OWNER_CATEGORIES.get(_fact_name, frozenset()),
                _category_name,
            }
        )

_CROSS_PRODUCT_FACT_NAMES = frozenset(
    {
        *EngineeringRequirementsAgent.SHARED_KEYS,
        *EngineeringRequirementsAgent.COMMERCIAL_KEYS,
    }
)

_ONTOLOGY_CATEGORY_BY_PRODUCT_TYPE = {
    str(item["canonical_type"]): str(item["category"])
    for item in PRODUCT_TYPE_ONTOLOGY
}

_ONTOLOGY_FACT_NAMES_BY_PRODUCT_TYPE = {
    product_type: frozenset(
        str(definition["name"])
        for definition in definitions
        if definition.get("name")
    )
    for product_type, definitions in CONSTRAINT_FACT_ONTOLOGY.items()
}

_GENERIC_PRODUCT_TYPES_BY_CATEGORY = {
    ProductCategory.PUMPS: {"pump", "насос"},
    ProductCategory.PIPES: {"pipe", "труба"},
    ProductCategory.BOILERS: {"boiler", "котел", "котёл"},
    ProductCategory.FILTERS: {"filter", "фильтр"},
    ProductCategory.VALVES: {"valve", "клапан", "кран"},
    ProductCategory.RADIATORS: {"radiator", "радиатор"},
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


def _fact_applicability(
    fact_name: str,
    goal: ProductGoal | None,
) -> tuple[bool, tuple[str, ...]]:
    """Check a canonical fact against typed product/category ownership.

    The guard is deliberately conservative: it rejects only a fact already
    owned by one or more *other* engineering categories.  A name absent from
    the ontology is allowed because absence is not proof of incompatibility.
    Product-kind facts supplement the older category schema where it is less
    precise (for example a water filter's connection size).
    """

    if goal is None or fact_name in _CROSS_PRODUCT_FACT_NAMES:
        return True, ()

    owner_categories = _FACT_OWNER_CATEGORIES.get(fact_name, frozenset())
    if not owner_categories:
        return True, ()

    canonical_type = str(goal.canonical_type or "")
    goal_categories = {goal.category.value}
    ontology_category = _ONTOLOGY_CATEGORY_BY_PRODUCT_TYPE.get(canonical_type)
    if ontology_category:
        goal_categories.add(ontology_category)
    if goal_categories & owner_categories:
        return True, tuple(sorted(owner_categories))

    kind_fact_names = _ONTOLOGY_FACT_NAMES_BY_PRODUCT_TYPE.get(
        canonical_type,
        frozenset(),
    )
    if fact_name in kind_fact_names:
        return True, tuple(sorted(owner_categories))
    return False, tuple(sorted(owner_categories))


def _same_goal(goal: ProductGoal, canonical_type: str | None, category: ProductCategory) -> bool:
    if canonical_type and goal.canonical_type:
        return canonical_type.casefold() == goal.canonical_type.casefold()
    return category != ProductCategory.OTHER and category == goal.category


def _generic_target_refines_active_goal(
    goal: ProductGoal,
    canonical_type: str | None,
    category: ProductCategory,
) -> bool:
    """Keep a more specific type when a follow-up names only its family."""

    if not canonical_type or category == ProductCategory.OTHER:
        return False
    if goal.category != category or not goal.canonical_type:
        return False
    generic = {
        item.casefold()
        for item in _GENERIC_PRODUCT_TYPES_BY_CATEGORY.get(category, set())
    }
    return (
        canonical_type.casefold() in generic
        and goal.canonical_type.casefold() not in generic
    )


def _goal_by_id(goals: list[ProductGoal], goal_id: str | None) -> ProductGoal | None:
    return next((goal for goal in goals if goal.goal_id == goal_id), None)


def _matching_goal(
    goals: list[ProductGoal],
    active_goal_id: str | None,
    canonical_type: str | None,
    category: ProductCategory,
) -> ProductGoal | None:
    """Prefer the active compatible goal, then the most recent compatible one."""

    active = _goal_by_id(goals, active_goal_id)
    if active is not None and _same_goal(active, canonical_type, category):
        return active
    return next(
        (
            goal
            for goal in reversed(goals)
            if _same_goal(goal, canonical_type, category)
        ),
        None,
    )


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


def _unique_goal_ids(values: Iterable[str | None]) -> list[str]:
    """Return stable, non-null goal ids without changing semantic order."""

    return list(dict.fromkeys(value for value in values if value is not None))


def _task_acts_share_identity(existing: TaskAct, requested: TaskAct) -> bool:
    """Whether two typed acts address one continuing customer task.

    Product discovery has the intentional ``find`` -> ``select`` refinement.
    Every other act keeps its exact identity.  Goal identity and terminal-state
    guards are applied separately by :func:`_find_reusable_task`, so this
    cannot merge price with stock, cross two products, or reopen completed
    work.  No reply text is involved.
    """

    if existing in _DISCOVERY_ACTS and requested in _DISCOVERY_ACTS:
        return True
    return existing == requested


def _find_reusable_task(
    tasks: list[CustomerTask],
    *,
    act: TaskAct,
    goal_id: str | None,
    turn_number: int,
    preferred_task_id: str | None,
) -> CustomerTask | None:
    exact_candidates = [
        task
        for task in tasks
        if task.target_goal_id == goal_id
        and task.status in _NON_TERMINAL_TASK_STATUSES
        and _task_acts_share_identity(task.act, act)
        and (
            not task.was_addressed_on(turn_number)
            or (
                task.act in _DISCOVERY_ACTS
                and act in _DISCOVERY_ACTS
            )
        )
    ]
    exact = next(
        (
            task
            for task in exact_candidates
            if task.task_id == preferred_task_id
        ),
        None,
    )
    if exact is None and exact_candidates:
        exact = max(
            exact_candidates,
            key=lambda task: (
                task.last_addressed_turn or task.origin_turn,
                task.origin_turn,
                task.task_id,
            ),
        )
    if exact is not None:
        return exact
    if goal_id is None or act not in _DISCOVERY_ACTS:
        return None
    # A goal-less discovery task represents an earlier request whose product
    # had not yet been identified (for example, a typed photo description).
    # Bind that stable task to the newly confirmed goal instead of creating a
    # second selection task.
    return next(
        (
            task
            for task in tasks
            if task.target_goal_id is None
            and task.status in _NON_TERMINAL_TASK_STATUSES
            and task.act in _DISCOVERY_ACTS
        ),
        None,
    )


def _discovery_act(existing: TaskAct, requested: TaskAct) -> TaskAct:
    if TaskAct.SELECT in {existing, requested}:
        return TaskAct.SELECT
    return TaskAct.FIND


def _suspend_unscoped_discovery_tasks(
    tasks: list[CustomerTask],
    *,
    except_task_id: str,
    metadata: TurnMetadata,
    turn_number: int,
    events: list[object],
) -> None:
    for task in tuple(tasks):
        if (
            task.task_id == except_task_id
            or task.target_goal_id is not None
            or task.act not in _DISCOVERY_ACTS
            or task.status not in {
                TaskStatus.PENDING,
                TaskStatus.IN_PROGRESS,
                TaskStatus.BLOCKED,
            }
        ):
            continue
        _replace_task(
            tasks,
            task.model_copy(
                update={
                    "status": TaskStatus.SUSPENDED,
                    "blocking_reason": "typed_product_goal_established",
                }
            ),
        )
        events.append(
            TaskSuspended(
                turn_id=metadata.turn_id,
                turn_number=turn_number,
                task_id=task.task_id,
                reason_code="typed_product_goal_established",
            )
        )


def _compatible_task_for_fact(
    tasks: list[CustomerTask],
    addressed_tasks: list[CustomerTask],
    *,
    goal_id: str | None,
    preferred_active_task: str | None,
) -> str | None:
    """Bind a fact only to a task with the same goal (or both unscoped)."""

    addressed = next(
        (
            task
            for task in reversed(addressed_tasks)
            if task.target_goal_id == goal_id
            and task.status not in {TaskStatus.SATISFIED, TaskStatus.CANCELLED}
        ),
        None,
    )
    if addressed is not None:
        return addressed.task_id
    preferred = next(
        (
            task
            for task in tasks
            if task.task_id == preferred_active_task
            and task.target_goal_id == goal_id
            and task.status not in {TaskStatus.SATISFIED, TaskStatus.CANCELLED}
        ),
        None,
    )
    if preferred is not None:
        return preferred.task_id
    compatible = next(
        (
            task
            for task in tasks
            if task.target_goal_id == goal_id
            and task.status
            not in {
                TaskStatus.SATISFIED,
                TaskStatus.CANCELLED,
                TaskStatus.SUSPENDED,
            }
        ),
        None,
    )
    return compatible.task_id if compatible is not None else None


def _pending_question_task_for_fact(
    previous: DialogueStateV2,
    tasks: list[CustomerTask],
    *,
    fact_name: str,
    goal_id: str | None,
) -> CustomerTask | None:
    """Resolve a typed answer back to the task that asked for the fact.

    A semantic answer turn may legitimately have no discovery act (and a
    noisy interpreter may additionally emit an unrelated act).  The previous
    policy decision is the authoritative typed record of which task asked for
    which fact, so it must win over a newly created same-goal task when the
    turn is explicitly marked as an answer to the pending question.
    """

    if previous.last_policy is None:
        return None
    by_id = {task.task_id: task for task in tasks}
    for action in (previous.last_policy.primary, previous.last_policy.secondary):
        if (
            action is None
            or action.fact_name != fact_name
            or action.task_id is None
        ):
            continue
        task = by_id.get(action.task_id)
        if (
            task is None
            or task.status not in _NON_TERMINAL_TASK_STATUSES
            or task.target_goal_id != goal_id
        ):
            continue
        return task
    return None


def _delivered_question_task_for_fact(
    previous: DialogueStateV2,
    tasks: list[CustomerTask],
    *,
    fact_name: str,
) -> CustomerTask | None:
    """Resolve an unscoped fact through the one question actually delivered.

    ``last_policy`` and a shadow answer candidate are not proof that the
    customer saw a question.  The committed, accepted AnswerPlan summary is
    the authoritative continuation edge.  Its task and goal identity makes
    this binding deterministic even when semantic interpretation does not set
    ``answers_pending_question``.
    """

    summary = previous.answer_plan_summary
    if (
        summary is None
        or summary.delivery_status != ShadowDeliveryStatus.COMMITTED_TO_SESSION
        or summary.validation_status != "accepted"
        or summary.question_fact != fact_name
        or summary.question_id is None
        or summary.question_task_id is None
    ):
        return None
    task = next(
        (item for item in tasks if item.task_id == summary.question_task_id),
        None,
    )
    if (
        task is None
        # A delivered question is a continuation edge only while its task is
        # still conversationally foregroundable.  An explicit switch
        # suspends the old task and invalidates that edge; otherwise an
        # unscoped value for the new product could leak back into the old one.
        or task.status
        not in {
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
        }
        or task.target_goal_id != summary.question_goal_id
    ):
        return None
    return task


def _task_for_information_request(
    addressed_tasks: list[CustomerTask],
    *,
    act: TaskAct,
    goal_id: str | None,
) -> CustomerTask | None:
    """Bind an information deliverable only to its exact typed act and goal."""

    candidates = [
        task
        for task in addressed_tasks
        if task.act == act
        and task.target_goal_id == goal_id
        and task.status
        not in {TaskStatus.SATISFIED, TaskStatus.CANCELLED, TaskStatus.SUSPENDED}
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda task: (task.priority, task.task_id))


def _task_for_selection_control(
    tasks: list[CustomerTask],
    addressed_tasks: list[CustomerTask],
    *,
    preferred_active_task: str | None,
    active_goal_id: str | None,
) -> CustomerTask | None:
    """Bind a strategy control to one existing discovery task only."""

    eligible_statuses = {
        TaskStatus.PENDING,
        TaskStatus.IN_PROGRESS,
        TaskStatus.BLOCKED,
    }
    addressed = [
        task
        for task in addressed_tasks
        if task.act in _DISCOVERY_ACTS and task.status in eligible_statuses
    ]
    if addressed:
        return min(addressed, key=lambda task: (task.priority, task.task_id))
    preferred = next(
        (
            task
            for task in tasks
            if task.task_id == preferred_active_task
            and task.act in _DISCOVERY_ACTS
            and task.status in eligible_statuses
        ),
        None,
    )
    if preferred is not None:
        return preferred
    candidates = [
        task
        for task in tasks
        if task.act in _DISCOVERY_ACTS
        and task.status in eligible_statuses
        and task.target_goal_id == active_goal_id
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda task: (task.priority, task.task_id))


def _task_goal_ids(
    *,
    act: TaskAct,
    explicit_target_goal_ids: list[str],
    reference_goal_ids: list[str],
    active_goal_id: str | None,
    replacement_scope_selected: bool,
) -> list[str | None]:
    """Resolve the product goals addressed by one typed customer act.

    Explicit targets always win.  A replacement-style reference is used when
    no target was emitted and there is no continuing active goal, or when the
    semantic operation explicitly starts/switches the task.  On an ordinary
    continuation the active goal wins over a contextual mention, preserving
    the target/context invariant.
    """

    if explicit_target_goal_ids:
        return list(explicit_target_goal_ids)
    if act not in _PRODUCT_SCOPED_ACTS:
        return [active_goal_id]
    if reference_goal_ids and replacement_scope_selected:
        return list(reference_goal_ids)
    if active_goal_id is not None:
        return [active_goal_id]
    if reference_goal_ids:
        return list(reference_goal_ids)
    return [None]


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
        ProgressKind.INFORMATION_REQUEST_REGISTERED: 80,
        ProgressKind.SELECTION_STRATEGY_CHANGED: 75,
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
    selection_controls = list(previous.selection_controls)
    questions = list(previous.direct_questions)
    information_requests = list(previous.information_requests)
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

        if (
            role == ProductRole.CONTEXT
            and canonical_type is None
            and category == ProductCategory.OTHER
        ):
            rejected.append(
                RejectedProposal(
                    proposal_type="product_goal",
                    reason_code="untyped_context_goal_ignored",
                    evidence=_short_evidence(mention.evidence),
                )
            )
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
            if active_goal.role != ProductRole.TARGET:
                changed.append("role")
            if canonical_type and canonical_type != active_goal.canonical_type:
                update.update({"canonical_type": canonical_type, "type_locked": True})
                changed.append("canonical_type")
            elif canonical_type and not active_goal.type_locked:
                update["type_locked"] = True
                changed.append("type_locked")
            if category != ProductCategory.OTHER and category != active_goal.category:
                update.update({"category": category, "category_locked": True})
                changed.append("category")
            elif category != ProductCategory.OTHER and not active_goal.category_locked:
                update["category_locked"] = True
                changed.append("category_locked")
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

        if (
            role == ProductRole.TARGET
            and active_goal
            and operation in {"continue", "refine"}
            and _generic_target_refines_active_goal(
                active_goal,
                canonical_type,
                category,
            )
        ):
            updates: dict[str, object] = {
                "evidence": _short_evidence(mention.evidence),
                "source": turn_metadata.source,
                "confidence": turn_understanding.confidence,
                "confirmed_turn": turn_number,
            }
            changed: list[str] = []
            if active_goal.role != ProductRole.TARGET:
                updates["role"] = ProductRole.TARGET
                changed.append("role")
            refined = active_goal.model_copy(update=updates)
            _replace_goal(goals, refined)
            mention_goal_ids[mention_index] = refined.goal_id
            if changed:
                events.append(
                    ProductGoalCorrected(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        goal_id=refined.goal_id,
                        changed_fields=tuple(changed),
                    )
                )
            progress_changes.append(ProgressKind.GOAL_REFINED)
            continue

        if role == ProductRole.TARGET and active_goal and _same_goal(
            active_goal,
            canonical_type,
            category,
        ):
            updates: dict[str, object] = {}
            changed: list[str] = []
            if active_goal.role != ProductRole.TARGET:
                updates["role"] = ProductRole.TARGET
                changed.append("role")
            if canonical_type and not active_goal.canonical_type:
                updates["canonical_type"] = canonical_type
                changed.append("canonical_type")
            if canonical_type and not active_goal.type_locked:
                updates["type_locked"] = True
                changed.append("type_locked")
            if category != ProductCategory.OTHER and active_goal.category == ProductCategory.OTHER:
                updates["category"] = category
                changed.append("category")
            if category != ProductCategory.OTHER and not active_goal.category_locked:
                updates["category_locked"] = True
                changed.append("category_locked")
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

        if role != ProductRole.TARGET:
            matching_goal = _matching_goal(
                goals,
                active_goal_id,
                canonical_type,
                category,
            )
            if matching_goal is not None:
                updates: dict[str, object] = {}
                changed: list[str] = []
                if (
                    _PRODUCT_ROLE_PRIORITY[role]
                    > _PRODUCT_ROLE_PRIORITY[matching_goal.role]
                ):
                    updates["role"] = role
                    changed.append("role")
                if canonical_type and not matching_goal.canonical_type:
                    updates["canonical_type"] = canonical_type
                    changed.append("canonical_type")
                if (
                    category != ProductCategory.OTHER
                    and matching_goal.category == ProductCategory.OTHER
                ):
                    updates["category"] = category
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
                    matching_goal = matching_goal.model_copy(update=updates)
                    _replace_goal(goals, matching_goal)
                    events.append(
                        ProductGoalCorrected(
                            turn_id=turn_metadata.turn_id,
                            turn_number=turn_number,
                            goal_id=matching_goal.goal_id,
                            changed_fields=tuple(changed),
                        )
                    )
                    progress_changes.append(ProgressKind.GOAL_REFINED)
                mention_goal_ids[mention_index] = matching_goal.goal_id
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

    target_goal_ids = _unique_goal_ids(
        mention_goal_ids[index]
        for index, mention in enumerate(turn_understanding.products)
        if _product_role(mention.role) == ProductRole.TARGET
        and index in mention_goal_ids
    )
    replacement_goal_ids = _unique_goal_ids(
        mention_goal_ids[index]
        for index, mention in enumerate(turn_understanding.products)
        if _product_role(mention.role) in _REPLACEMENT_ROLES
        and index in mention_goal_ids
    )
    context_goal_ids = _unique_goal_ids(
        mention_goal_ids[index]
        for index, mention in enumerate(turn_understanding.products)
        if _product_role(mention.role) == ProductRole.CONTEXT
        and index in mention_goal_ids
    )
    reference_goal_ids = replacement_goal_ids or context_goal_ids

    # A replacement request may contain only the already installed/compared
    # products.  Give the task a deterministic active scope so later unscoped
    # follow-ups and constraints inherit the same goal.  This does not change
    # the ProductGoal role and therefore cannot turn ordinary context into a
    # confirmed target.  Explicit targets retain absolute priority.
    replacement_scope_selected = bool(
        not target_goal_ids
        and reference_goal_ids
        and (active_goal_id is None or operation in {"new", "switch"})
    )
    if replacement_scope_selected:
        replacement_active_goal_id = reference_goal_ids[0]
        if active_goal_id and active_goal_id != replacement_active_goal_id:
            _suspend_goal_tasks(
                tasks,
                active_goal_id,
                metadata=turn_metadata,
                turn_number=turn_number,
                events=events,
                reason_code="explicit_replacement_task_switch",
            )
            progress_changes.append(ProgressKind.TASK_SWITCHED)
            preferred_active_task = None
        elif active_goal_id is None:
            progress_changes.append(ProgressKind.GOAL_REFINED)
        active_goal_id = replacement_active_goal_id

    # In a compound replacement request the installed/alternative products
    # are separate retained goals.  When a later turn explicitly names one of
    # those retained products, that reference is the requested focus even if
    # the semantic model correctly keeps its role as ``existing`` or
    # ``alternative`` rather than relabelling it as a new target.  Context-only
    # mentions do not enter this branch and therefore still cannot replace the
    # active goal.
    referenced_focus_goal_id: str | None = None
    if (
        not target_goal_ids
        and replacement_goal_ids
        and operation in {"continue", "refine"}
        and any(
            _task_act(item) in _PRODUCT_SCOPED_ACTS
            for item in turn_understanding.acts
        )
    ):
        referenced_focus_goal_id = replacement_goal_ids[0]
        if active_goal_id != referenced_focus_goal_id:
            progress_changes.append(ProgressKind.TASK_SWITCHED)
        active_goal_id = referenced_focus_goal_id
        preferred_active_task = next(
            (
                task.task_id
                for task in tasks
                if task.target_goal_id == referenced_focus_goal_id
                and task.status in _NON_TERMINAL_TASK_STATUSES
            ),
            preferred_active_task,
        )

    created_tasks: list[CustomerTask] = []
    addressed_tasks: list[CustomerTask] = []
    for act_index, semantic_act in enumerate(turn_understanding.acts):
        act = _task_act(semantic_act)
        task_goal_ids = _task_goal_ids(
            act=act,
            explicit_target_goal_ids=target_goal_ids,
            reference_goal_ids=reference_goal_ids,
            active_goal_id=active_goal_id,
            replacement_scope_selected=replacement_scope_selected,
        )
        for goal_position, goal_id in enumerate(task_goal_ids):
            existing_task = _find_reusable_task(
                tasks,
                act=act,
                goal_id=goal_id,
                turn_number=turn_number,
                preferred_task_id=preferred_active_task,
            )
            if existing_task is not None:
                rebound_to_goal = (
                    existing_task.target_goal_id is None and goal_id is not None
                )
                addressed_act = (
                    _discovery_act(existing_task.act, act)
                    if existing_task.act in _DISCOVERY_ACTS
                    and act in _DISCOVERY_ACTS
                    else existing_task.act
                )
                addressed_task = existing_task.model_copy(
                    update={
                        "act": addressed_act,
                        "target_goal_id": goal_id,
                        "status": (
                            TaskStatus.IN_PROGRESS
                            if rebound_to_goal
                            or existing_task.task_id == preferred_active_task
                            else existing_task.status
                        ),
                        "source_turn": turn_number,
                        "created_turn": existing_task.origin_turn,
                        "last_addressed_turn": turn_number,
                        "blocking_reason": (
                            None
                            if rebound_to_goal
                            else existing_task.blocking_reason
                        ),
                    }
                )
                _replace_task(tasks, addressed_task)
                if rebound_to_goal:
                    _suspend_unscoped_discovery_tasks(
                        tasks,
                        except_task_id=addressed_task.task_id,
                        metadata=turn_metadata,
                        turn_number=turn_number,
                        events=events,
                    )
                    preferred_active_task = addressed_task.task_id
                rejected.append(
                    RejectedProposal(
                        proposal_type="task",
                        reason_code=(
                            "existing_selection_task_reused"
                            if act in {TaskAct.FIND, TaskAct.SELECT}
                            else (
                                "existing_product_task_reused"
                                if act in _PRODUCT_SCOPED_ACTS
                                else "existing_task_readdressed"
                            )
                        ),
                        details={"existing_task_id": existing_task.task_id},
                    )
                )
                events.append(
                    TaskAddressed(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        task_id=addressed_task.task_id,
                        act=act,
                        goal_id=addressed_task.target_goal_id,
                    )
                )
                addressed_tasks.append(addressed_task)
                continue
            task_id = _stable_id(
                "task",
                turn_metadata.turn_id,
                act_index,
                goal_position,
                goal_id,
            )
            preferred_task = next(
                (
                    task
                    for task in tasks
                    if task.task_id == preferred_active_task
                ),
                None,
            )
            focus_typed_task = bool(
                goal_id is not None
                and preferred_task is not None
                and preferred_task.target_goal_id is None
                and preferred_task.act in _DISCOVERY_ACTS
            )
            status = (
                TaskStatus.IN_PROGRESS
                if (
                    (preferred_active_task is None and not created_tasks)
                    or focus_typed_task
                )
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
                created_turn=turn_number,
                last_addressed_turn=turn_number,
            )
            tasks.append(task)
            created_tasks.append(task)
            addressed_tasks.append(task)
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
                if focus_typed_task:
                    _suspend_unscoped_discovery_tasks(
                        tasks,
                        except_task_id=task.task_id,
                        metadata=turn_metadata,
                        turn_number=turn_number,
                        events=events,
                    )
                preferred_active_task = task_id

    addressed_ids = tuple(dict.fromkeys(task.task_id for task in addressed_tasks))
    addressed_tasks = [
        next(task for task in tasks if task.task_id == task_id)
        for task_id in addressed_ids
    ]
    if len(addressed_tasks) > 1:
        related_ids = tuple(task.task_id for task in addressed_tasks)
        for task in addressed_tasks:
            replacement = task.model_copy(
                update={
                    "related_task_ids": tuple(
                        dict.fromkeys(
                            (
                                *task.related_task_ids,
                                *(
                                    task_id
                                    for task_id in related_ids
                                    if task_id != task.task_id
                                ),
                            )
                        )
                    )
                }
            )
            _replace_task(tasks, replacement)
        created_tasks = [
            next(task for task in tasks if task.task_id == created.task_id)
            for created in created_tasks
        ]

    for control_index, semantic_control in enumerate(
        turn_understanding.selection_controls
    ):
        task = _task_for_selection_control(
            tasks,
            addressed_tasks,
            preferred_active_task=preferred_active_task,
            active_goal_id=active_goal_id,
        )
        if task is None:
            rejected.append(
                RejectedProposal(
                    proposal_type="selection_control",
                    reason_code="selection_control_has_no_active_discovery_task",
                    evidence=_short_evidence(semantic_control.evidence),
                )
            )
            continue
        if not task.was_addressed_on(turn_number):
            task = task.model_copy(
                update={
                    "status": TaskStatus.IN_PROGRESS,
                    "source_turn": turn_number,
                    "created_turn": task.origin_turn,
                    "last_addressed_turn": turn_number,
                    "blocking_reason": None,
                }
            )
            _replace_task(tasks, task)
            preferred_active_task = task.task_id
            events.append(
                TaskAddressed(
                    turn_id=turn_metadata.turn_id,
                    turn_number=turn_number,
                    task_id=task.task_id,
                    act=task.act,
                    goal_id=task.target_goal_id,
                )
            )
        kind = SelectionControlKind(semantic_control.kind.value)
        existing_control = next(
            (
                item
                for item in reversed(selection_controls)
                if item.task_id == task.task_id and item.kind == kind
            ),
            None,
        )
        if existing_control is not None:
            rejected.append(
                RejectedProposal(
                    proposal_type="selection_control",
                    reason_code="selection_control_already_active",
                    evidence=_short_evidence(semantic_control.evidence),
                    details={"control_id": existing_control.control_id},
                )
            )
            continue
        control_id = _stable_id(
            "selection_control",
            turn_metadata.turn_id,
            control_index,
            task.task_id,
            kind.value,
        )
        signal = SelectionControlSignal(
            control_id=control_id,
            kind=kind,
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            evidence=_short_evidence(semantic_control.evidence),
            source=turn_metadata.source,
            source_turn=turn_number,
        )
        selection_controls.append(signal)
        events.append(
            SelectionControlRegistered(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                control_id=control_id,
                control_kind=kind,
                task_id=task.task_id,
                goal_id=task.target_goal_id,
            )
        )
        progress_changes.append(ProgressKind.SELECTION_STRATEGY_CHANGED)

    for request_index, semantic_request in enumerate(
        turn_understanding.information_requests
    ):
        goal_id = (
            mention_goal_ids.get(semantic_request.applies_to_product)
            if semantic_request.applies_to_product is not None
            else active_goal_id
        )
        request_act = _task_act(semantic_request.act)
        task = _task_for_information_request(
            addressed_tasks,
            act=request_act,
            goal_id=goal_id,
        )
        if task is None:
            rejected.append(
                RejectedProposal(
                    proposal_type="information_request",
                    reason_code="information_request_exact_task_scope_unresolved",
                    details={
                        "request_index": request_index,
                        "act": request_act.value,
                        "goal_id": goal_id,
                    },
                )
            )
            continue
        purpose = InformationPurpose(semantic_request.purpose.value)
        requested_outputs = tuple(
            RequestedInformationOutput(item.value)
            for item in semantic_request.requested_outputs
        )
        output_relation = InformationOutputRelation(
            semantic_request.output_relation.value
        )
        source_kind = (
            InformationSourceKind(semantic_request.source_kind.value)
            if semantic_request.source_kind is not None
            else None
        )
        subject_scope = InformationSubjectScope(
            semantic_request.subject_scope.value
        )
        request_id = _stable_id(
            "information_request",
            turn_metadata.turn_id,
            request_index,
            task.task_id,
            goal_id,
            semantic_request.fact_name,
            purpose.value,
            *(item.value for item in requested_outputs),
            output_relation.value,
            source_kind.value if source_kind is not None else None,
            subject_scope.value,
        )
        information_request = InformationRequestV2(
            request_id=request_id,
            task_id=task.task_id,
            goal_id=goal_id,
            fact_name=semantic_request.fact_name,
            purpose=purpose,
            requested_outputs=requested_outputs,
            output_relation=output_relation,
            source_kind=source_kind,
            subject_scope=subject_scope,
            source_turn=turn_number,
        )
        information_requests.append(information_request)
        events.append(
            InformationRequestRegistered(
                turn_id=turn_metadata.turn_id,
                turn_number=turn_number,
                request_id=request_id,
                task_id=task.task_id,
                goal_id=goal_id,
                purpose=purpose,
            )
        )
        progress_changes.append(ProgressKind.INFORMATION_REQUEST_REGISTERED)

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

    # Resolve every fact's typed scope once.  The same accepted semantic frame
    # can contain a grounded known value and an LLM-produced missing-status
    # duplicate for that value.  Resolve that conflict before mutating the
    # working copy so the outcome cannot depend on array order: an explicit
    # current-turn value wins; a genuine unknown/refused/deferred remains
    # authoritative when no known value exists in the same goal/task scope.
    constraint_bindings: list[tuple[str | None, str | None]] = []
    pending_answer_task_ids: set[str] = set()
    delivered_answer_constraint_indexes: set[int] = set()
    known_constraint_indexes: dict[
        tuple[str, str | None, str | None],
        list[int],
    ] = {}
    for constraint_index, semantic_fact in enumerate(
        turn_understanding.constraints
    ):
        delivered_question_task = (
            _delivered_question_task_for_fact(
                previous,
                tasks,
                fact_name=semantic_fact.name,
            )
            if semantic_fact.applies_to_product is None
            else None
        )
        if delivered_question_task is not None:
            goal_id = delivered_question_task.target_goal_id
            pending_task = delivered_question_task
            delivered_answer_constraint_indexes.add(constraint_index)
        else:
            goal_id = (
                mention_goal_ids.get(semantic_fact.applies_to_product)
                if semantic_fact.applies_to_product is not None
                else active_goal_id
            )
            pending_task = (
                _pending_question_task_for_fact(
                    previous,
                    tasks,
                    fact_name=semantic_fact.name,
                    goal_id=goal_id,
                )
                if turn_understanding.answers_pending_question
                else None
            )
        if pending_task is not None:
            task_id = pending_task.task_id
            if (
                pending_task.task_id not in pending_answer_task_ids
                and not pending_task.was_addressed_on(turn_number)
            ):
                addressed_task = pending_task.model_copy(
                    update={
                        "source_turn": turn_number,
                        "created_turn": pending_task.origin_turn,
                        "last_addressed_turn": turn_number,
                        "blocking_reason": None,
                    }
                )
                _replace_task(tasks, addressed_task)
                addressed_tasks.append(addressed_task)
                events.append(
                    TaskAddressed(
                        turn_id=turn_metadata.turn_id,
                        turn_number=turn_number,
                        task_id=addressed_task.task_id,
                        act=addressed_task.act,
                        goal_id=addressed_task.target_goal_id,
                    )
                )
                rejected.append(
                    RejectedProposal(
                        proposal_type="constraint_task_binding",
                        reason_code=(
                            "delivered_question_task_readdressed"
                            if constraint_index in delivered_answer_constraint_indexes
                            else "pending_question_task_readdressed"
                        ),
                        evidence=_short_evidence(semantic_fact.evidence),
                        details={"task_id": addressed_task.task_id},
                    )
                )
            pending_answer_task_ids.add(pending_task.task_id)
        else:
            task_id = _compatible_task_for_fact(
                tasks,
                addressed_tasks,
                goal_id=goal_id,
                preferred_active_task=preferred_active_task,
            )
        constraint_bindings.append((goal_id, task_id))
        if _constraint_status(semantic_fact.status) == ConstraintStatus.KNOWN:
            scope = (semantic_fact.name, goal_id, task_id)
            known_constraint_indexes.setdefault(scope, []).append(
                constraint_index
            )

    suppressed_missing_status_indexes: dict[int, int] = {}
    for constraint_index, semantic_fact in enumerate(
        turn_understanding.constraints
    ):
        if _constraint_status(semantic_fact.status) == ConstraintStatus.KNOWN:
            continue
        goal_id, task_id = constraint_bindings[constraint_index]
        scope = (semantic_fact.name, goal_id, task_id)
        known_indexes = known_constraint_indexes.get(scope)
        if not known_indexes:
            continue
        # Under an explicit correction the last known value retains normal
        # reducer ordering semantics.  Otherwise the first known value is the
        # fail-closed winner and any conflicting known duplicate is diagnosed
        # by the existing confirmed-fact rule below.
        winner_index = (
            known_indexes[-1] if operation == "correct" else known_indexes[0]
        )
        suppressed_missing_status_indexes[constraint_index] = winner_index

    for constraint_index, semantic_fact in enumerate(turn_understanding.constraints):
        status = _constraint_status(semantic_fact.status)
        polarity = _constraint_polarity(semantic_fact.polarity)
        strength = (
            ConstraintStrength.SOFT
            if polarity == ConstraintPolarity.PREFERRED
            else ConstraintStrength.HARD
        )
        goal_id, task_id = constraint_bindings[constraint_index]
        preferred_known_index = suppressed_missing_status_indexes.get(
            constraint_index
        )
        if preferred_known_index is not None:
            preferred_known = turn_understanding.constraints[
                preferred_known_index
            ]
            rejected.append(
                RejectedProposal(
                    proposal_type="constraint",
                    reason_code=(
                        "current_known_fact_preferred_over_missing_status_duplicate"
                    ),
                    evidence=_short_evidence(semantic_fact.evidence),
                    details={
                        "fact_name": semantic_fact.name,
                        "discarded_status": status.value,
                        "preferred_constraint_index": preferred_known_index,
                        "preferred_status": _constraint_status(
                            preferred_known.status
                        ).value,
                    },
                )
            )
            continue

        goal = _goal_by_id(goals, goal_id)
        fact_is_applicable, owner_categories = _fact_applicability(
            semantic_fact.name,
            goal,
        )
        if not fact_is_applicable:
            rejected.append(
                RejectedProposal(
                    proposal_type="constraint",
                    reason_code="constraint_incompatible_with_product_goal",
                    evidence=_short_evidence(semantic_fact.evidence),
                    details={
                        "fact_name": semantic_fact.name,
                        "goal_id": goal_id,
                        "goal_product_type": (
                            goal.canonical_type if goal is not None else None
                        ),
                        "goal_category": (
                            goal.category.value if goal is not None else None
                        ),
                        "owner_categories": list(owner_categories),
                    },
                )
            )
            conflicts.append(
                DiagnosticConflict(
                    conflict_type="constraint_applicability",
                    reason_code="constraint_incompatible_with_product_goal",
                    existing_id=goal_id,
                    proposed_value=semantic_fact.name,
                )
            )
            continue

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
            and not (
                operation in {"continue", "refine"}
                and _is_safe_known_fact_refinement(
                    existing,
                    proposed_value=semantic_fact.value,
                    proposed_unit=semantic_fact.unit,
                    proposed_polarity=polarity,
                )
            )
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
                "delivered_question_answer"
                if constraint_index in delivered_answer_constraint_indexes
                else (
                    "pending_question_answer"
                    if turn_understanding.answers_pending_question
                    else turn_metadata.source
                )
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
        information_requests=tuple(information_requests),
        direct_questions=tuple(questions),
        ambiguities=tuple(ambiguities),
        selection_controls=tuple(selection_controls[-100:]),
        progress=progress,
        last_policy=previous.last_policy,
        catalog_planning=previous.catalog_planning,
        commerce_workflows=previous.commerce_workflows,
        commerce_sensitive_values=tuple(commerce_sensitive_values),
        commerce_controls=tuple(commerce_controls[-100:]),
        commerce_outbox=previous.commerce_outbox,
        commerce_planning=previous.commerce_planning,
        answer_plan_summary=previous.answer_plan_summary,
        response_strategy_history=previous.response_strategy_history,
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

    state = reduction.state.model_copy(
        update={"last_policy": plan}
    )
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


_ACTION_STRATEGIES = {
    "ask_decision_changing_question": ResponseStrategyKind.ASK_DECISION_FACT,
    "collect_commerce_fact": ResponseStrategyKind.ASK_DECISION_FACT,
    "explain_how_to_find_fact": ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
    "explain_term_or_method": ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT,
    "show_preliminary_options": ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS,
    "search_exact": ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS,
    "continue_with_confirmed_facts": ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS,
    "present_controlled_analog": ResponseStrategyKind.PRESENT_CONTROLLED_ANALOG,
    "offer_verifiable_external_step": ResponseStrategyKind.OFFER_VERIFIABLE_EXTERNAL_STEP,
    "start_or_continue_handoff": ResponseStrategyKind.OFFER_VERIFIABLE_EXTERNAL_STEP,
    "state_capability_boundary": ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
    "state_commerce_capability_boundary": ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY,
    "close_task": ResponseStrategyKind.CLOSE_TASK,
}


_FULFILLED_INFORMATION_OUTPUT_BY_NEXT_STEP = {
    NextStepKind.PROVIDE_DIRECT_ANSWER: RequestedInformationOutput.EXPLANATION,
    NextStepKind.EXPLAIN_HOW_TO_FIND_FACT: RequestedInformationOutput.INSTRUCTION,
    NextStepKind.EXPLAIN_DECISION_RELEVANCE: RequestedInformationOutput.EXPLANATION,
    NextStepKind.REPORT_CANDIDATE_FACTS: RequestedInformationOutput.EXPLANATION,
}

_UNAVAILABLE_INFORMATION_OUTPUT_BY_NEXT_STEP = {
    NextStepKind.STATE_INFORMATION_SOURCE_BOUNDARY: (
        RequestedInformationOutput.VERIFIED_LINK
    ),
    NextStepKind.STATE_INFORMATION_MEANING_BOUNDARY: (
        RequestedInformationOutput.EXPLANATION
    ),
    NextStepKind.STATE_DETERMINATION_METHOD_BOUNDARY: (
        RequestedInformationOutput.INSTRUCTION
    ),
    NextStepKind.STATE_INFORMATION_VALUE_BOUNDARY: (
        RequestedInformationOutput.EXPLANATION
    ),
    NextStepKind.STATE_COMPATIBILITY_BOUNDARY: (
        RequestedInformationOutput.EXPLANATION
    ),
}


def _information_output_projection(
    planning: AnswerPlanningResult | None,
) -> tuple[
    str | None,
    tuple[RequestedInformationOutput, ...],
    InformationOutputRelation | None,
    tuple[RequestedInformationOutput, ...],
    tuple[RequestedInformationOutput, ...],
    tuple[str, ...],
]:
    """Describe only the typed output represented by the plan's sole next step.

    This is deliberately a projection, not a lifecycle transition.  A shadow
    plan may be rejected or never delivered.  ``record_response_delivery`` is
    the only place that can apply the projected result to an information
    request after the exact plan has been validated and committed.
    """

    if planning is None or planning.answer_plan is None:
        return None, (), None, (), (), ()
    next_step = planning.answer_plan.next_step
    if next_step.information_request_id is None:
        return None, (), None, (), (), ()
    requested = tuple(next_step.requested_outputs)
    requested_set = set(requested)
    fulfilled_output = _FULFILLED_INFORMATION_OUTPUT_BY_NEXT_STEP.get(
        next_step.kind
    )
    unavailable_output = _UNAVAILABLE_INFORMATION_OUTPUT_BY_NEXT_STEP.get(
        next_step.kind
    )
    fulfilled = (
        (fulfilled_output,)
        if fulfilled_output is not None and fulfilled_output in requested_set
        else ()
    )
    unavailable = (
        (unavailable_output,)
        if unavailable_output is not None and unavailable_output in requested_set
        else ()
    )
    return (
        next_step.information_request_id,
        requested,
        next_step.output_relation,
        fulfilled,
        unavailable,
        tuple(next_step.reason_codes),
    )


def record_answer_shadow(
    reduction: ReductionResult,
    planning: AnswerPlanningResult | None,
    validation: AnswerValidationResult | None,
    policy: NextActionPlan,
    progress_assessments: Iterable[TaskProgressAssessment],
    turn_metadata: TurnMetadata,
) -> ReductionResult:
    """Record bounded Stage 5 summaries; a shadow draft is never delivered."""

    events: list[object] = list(reduction.events)
    previous_history = {
        item.task_id: item for item in reduction.state.response_strategy_history
    }
    actions = {
        item.task_id: item
        for item in (policy.primary, policy.secondary)
        if item is not None and item.task_id is not None
    }
    updated_history = dict(previous_history)
    assessments = tuple(progress_assessments)
    for assessment in assessments:
        previous = previous_history.get(assessment.task_id)
        action = actions.get(assessment.task_id)
        strategy = (
            _ACTION_STRATEGIES.get(action.kind.value)
            if action is not None
            else previous.last_strategy if previous is not None else None
        )
        attempted = list(previous.attempted_strategies if previous else ())
        if strategy is not None and strategy not in attempted:
            attempted.append(strategy)
            attempted = attempted[-12:]
        updated_history[assessment.task_id] = TaskStrategyState(
            task_id=assessment.task_id,
            consecutive_no_progress=assessment.consecutive_no_progress,
            attempted_strategies=tuple(attempted),
            last_strategy=strategy,
            last_question_fact=(
                action.fact_name
                if action is not None and action.fact_name
                else previous.last_question_fact if previous else None
            ),
            last_plan_signature=(
                planning.answer_plan.semantic_signature
                if planning is not None
                and planning.answer_plan is not None
                and assessment.task_id in planning.answer_plan.task_ids
                else previous.last_plan_signature if previous else None
            ),
            last_catalog_signature=assessment.catalog_signature,
            last_commerce_signature=assessment.commerce_signature,
            last_turn=reduction.state.turn_number,
            delivery_status=(
                ShadowDeliveryStatus.REJECTED
                if validation is not None and validation.status == "rejected"
                else ShadowDeliveryStatus.SHADOW_NOT_DELIVERED
                if planning is not None and planning.answer_plan is not None
                else ShadowDeliveryStatus.NOT_PLANNED
            ),
        )
        events.append(
            TaskProgressRecorded(
                turn_id=turn_metadata.turn_id,
                turn_number=reduction.state.turn_number,
                task_id=assessment.task_id,
                progress_status=assessment.status.value,
                consecutive_no_progress=assessment.consecutive_no_progress,
            )
        )
        if strategy is not None:
            events.append(
                ResponseStrategySelected(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    task_id=assessment.task_id,
                    strategy=strategy,
                )
            )
            if assessment.strategy_change_required and (
                previous is None or previous.last_strategy != strategy
            ):
                events.append(
                    ResponseStrategyEscalated(
                        turn_id=turn_metadata.turn_id,
                        turn_number=reduction.state.turn_number,
                        task_id=assessment.task_id,
                        previous_strategy=(previous.last_strategy if previous else None),
                        strategy=strategy,
                    )
                )

    summary = reduction.state.answer_plan_summary
    if planning is not None and planning.answer_plan is not None:
        plan = planning.answer_plan
        question_task = (
            next(
                (
                    task
                    for task in reduction.state.tasks
                    if plan.question is not None
                    and task.task_id == plan.question.task_id
                ),
                None,
            )
            if plan.question is not None
            else None
        )
        (
            information_request_id,
            information_requested_outputs,
            information_output_relation,
            information_fulfilled_outputs,
            information_unavailable_outputs,
            information_reason_codes,
        ) = _information_output_projection(planning)
        validation_status = validation.status if validation else "not_run"
        delivery = (
            ShadowDeliveryStatus.REJECTED
            if validation_status == "rejected"
            else ShadowDeliveryStatus.SHADOW_NOT_DELIVERED
        )
        current_plan_goal_ids = {
            task.target_goal_id
            for task in reduction.state.tasks
            if task.task_id in plan.task_ids and task.target_goal_id is not None
        }
        previous_presented_candidates = (
            tuple(
                candidate
                for candidate in (
                    reduction.state.answer_plan_summary.presented_candidates
                    if reduction.state.answer_plan_summary is not None
                    and reduction.state.answer_plan_summary.delivery_status
                    == ShadowDeliveryStatus.COMMITTED_TO_SESSION
                    else ()
                )
                if candidate.task_id in plan.task_ids
                or (
                    candidate.goal_id is not None
                    and candidate.goal_id in current_plan_goal_ids
                )
            )
        )
        previous_summary = reduction.state.answer_plan_summary
        preserve_visible_selection = bool(
            not plan.products
            and previous_presented_candidates
            and previous_summary is not None
        )
        summary = AnswerPlanSummary(
            plan_id=plan.plan_id,
            semantic_signature=plan.semantic_signature,
            task_ids=plan.task_ids,
            primary_action=plan.primary_action,
            question_fact=(plan.question.fact_name if plan.question else None),
            question_id=(plan.question.question_id if plan.question else None),
            question_task_id=(plan.question.task_id if plan.question else None),
            question_goal_id=(
                question_task.target_goal_id if question_task is not None else None
            ),
            next_step_kind=plan.next_step.kind.value,
            validation_status=validation_status,
            delivery_status=delivery,
            information_request_id=information_request_id,
            information_requested_outputs=information_requested_outputs,
            information_output_relation=information_output_relation,
            information_fulfilled_outputs=information_fulfilled_outputs,
            information_unavailable_outputs=information_unavailable_outputs,
            information_reason_codes=information_reason_codes,
            presented_candidates=(
                tuple(
                    PresentedCandidateSummary(
                        sku=item.sku,
                        name=item.name,
                        product_kind=item.product_kind,
                        role=item.role,
                        task_id=item.task_id,
                        goal_id=item.goal_id,
                        search_plan_id=item.search_plan_id,
                        source_turn=reduction.state.turn_number,
                    )
                    for item in plan.products
                )
                if plan.products
                else previous_presented_candidates
            ),
            selection_id=(
                previous_summary.selection_id if preserve_visible_selection else None
            ),
            catalog_revision=(
                previous_summary.catalog_revision if preserve_visible_selection else None
            ),
            source_turn=reduction.state.turn_number,
        )
        events.append(
            AnswerPlanCreated(
                turn_id=turn_metadata.turn_id,
                turn_number=reduction.state.turn_number,
                plan_id=plan.plan_id,
                semantic_signature=plan.semantic_signature,
            )
        )
        if validation is not None and validation.status == "rejected":
            events.append(
                AnswerPlanRejected(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    plan_id=plan.plan_id,
                    reason_codes=validation.reason_codes,
                )
            )
        elif validation is not None:
            events.append(
                AnswerPlanValidated(
                    turn_id=turn_metadata.turn_id,
                    turn_number=reduction.state.turn_number,
                    plan_id=plan.plan_id,
                    validation_status=validation.status,
                )
            )
        events.append(
            ShadowResponseNotDelivered(
                turn_id=turn_metadata.turn_id,
                turn_number=reduction.state.turn_number,
                plan_id=plan.plan_id,
            )
        )

    state = reduction.state.model_copy(
        update={
            "answer_plan_summary": summary,
            "response_strategy_history": tuple(updated_history.values())[-100:],
        }
    )
    return reduction.model_copy(update={"state": state, "events": tuple(events)})


def record_response_delivery(
    state: DialogueStateV2,
    turn_metadata: TurnMetadata,
    *,
    plan_id: str,
    response_digest: str,
    delivery_id: str,
    live_epoch_id: str,
    selection_id: str | None = None,
    catalog_revision: str | None = None,
) -> ReductionResult:
    """Commit a selected V2 response without treating old shadow turns as delivered."""

    from .contracts import (
        ResponseCommitSucceeded,
        ResponseDeliveryRecord,
        ResponseSelectedForDelivery,
        V2LiveEpochStarted,
    )

    events: list[object] = []
    if state.live_epoch_id is None:
        events.append(
            V2LiveEpochStarted(
                turn_id=turn_metadata.turn_id,
                turn_number=state.turn_number,
                live_epoch_id=live_epoch_id,
            )
        )
    events.append(
        ResponseSelectedForDelivery(
            turn_id=turn_metadata.turn_id,
            turn_number=state.turn_number,
            plan_id=plan_id,
            response_digest=response_digest,
        )
    )
    record = ResponseDeliveryRecord(
        delivery_id=delivery_id,
        turn_id=turn_metadata.turn_id,
        plan_id=plan_id,
        response_digest=response_digest,
        live_epoch_id=live_epoch_id,
        source_turn=state.turn_number,
    )
    summary = state.answer_plan_summary
    plan_matches_summary = bool(
        summary is not None
        and summary.plan_id == plan_id
        and summary.source_turn == state.turn_number
    )
    if plan_matches_summary:
        summary = summary.model_copy(
            update={
                "delivery_status": ShadowDeliveryStatus.COMMITTED_TO_SESSION,
                # Only a delivered SelectionResult supplies these fields.
                # Direct product facts and comparisons preserve the previous
                # selection identity rather than creating a phantom scope.
                "selection_id": selection_id or summary.selection_id,
                "catalog_revision": catalog_revision or summary.catalog_revision,
            }
        )
    tasks = list(state.tasks)
    task_stack = state.task_stack
    active_goal_id = state.active_goal_id
    if (
        plan_matches_summary
        and summary is not None
        and summary.validation_status == "accepted"
        and summary.question_id is not None
        and summary.question_task_id is not None
    ):
        question_task = next(
            (task for task in tasks if task.task_id == summary.question_task_id),
            None,
        )
        if (
            question_task is not None
            and question_task.status in _NON_TERMINAL_TASK_STATUSES
            and question_task.target_goal_id == summary.question_goal_id
        ):
            # The response ends by asking this task's typed question, so the
            # next unscoped continuation belongs to it.  Keep sibling work
            # pending rather than losing it or allowing two active tasks.
            tasks = [
                (
                    task.model_copy(update={"status": TaskStatus.PENDING})
                    if task.task_id != question_task.task_id
                    and task.status == TaskStatus.IN_PROGRESS
                    else (
                        task.model_copy(update={"status": TaskStatus.IN_PROGRESS})
                        if task.task_id == question_task.task_id
                        else task
                    )
                )
                for task in tasks
            ]
            task_stack = _stack(tasks, question_task.task_id)
            active_goal_id = summary.question_goal_id
    delivered = tuple(
        item.model_copy(
            update={"delivery_status": ShadowDeliveryStatus.COMMITTED_TO_SESSION}
        )
        for item in state.response_strategy_history
        if item.last_turn == state.turn_number
    )
    delivered_by_task = {
        item.task_id: item for item in state.delivered_response_strategy_history
    }
    delivered_by_task.update({item.task_id: item for item in delivered})
    information_requests = list(state.information_requests)
    if (
        plan_matches_summary
        and summary is not None
        and summary.validation_status == "accepted"
        and summary.information_request_id is not None
    ):
        request = next(
            (
                item
                for item in information_requests
                if item.request_id == summary.information_request_id
            ),
            None,
        )
        summary_matches_request = bool(
            request is not None
            and request.status == InformationRequestStatus.PENDING
            and summary.information_requested_outputs == request.requested_outputs
            and summary.information_output_relation == request.output_relation
        )
        if summary_matches_request and request is not None:
            requested = set(request.requested_outputs)
            fulfilled = requested.intersection(
                summary.information_fulfilled_outputs
            )
            unavailable = requested.intersection(
                summary.information_unavailable_outputs
            )
            if request.output_relation == InformationOutputRelation.ALL:
                resolved = bool(requested and requested.issubset(fulfilled))
                unavailable_terminal = bool(
                    requested and requested.issubset(unavailable)
                )
            else:
                resolved = bool(requested.intersection(fulfilled))
                # For ANY, an honest boundary is terminal only when every
                # alternative output has been checked and found unavailable.
                unavailable_terminal = bool(
                    requested and requested.issubset(unavailable)
                )
            terminal_status = (
                InformationRequestStatus.RESOLVED
                if resolved
                else InformationRequestStatus.UNAVAILABLE
                if unavailable_terminal
                else None
            )
            if terminal_status is not None:
                replacement = request.model_copy(
                    update={"status": terminal_status}
                )
                information_requests = [
                    replacement if item.request_id == request.request_id else item
                    for item in information_requests
                ]
                if terminal_status == InformationRequestStatus.RESOLVED:
                    events.append(
                        InformationRequestResolved(
                            turn_id=turn_metadata.turn_id,
                            turn_number=state.turn_number,
                            request_id=request.request_id,
                        )
                    )
                else:
                    events.append(
                        InformationRequestUnavailable(
                            turn_id=turn_metadata.turn_id,
                            turn_number=state.turn_number,
                            request_id=request.request_id,
                            reason_code=(
                                summary.information_reason_codes[0]
                                if summary.information_reason_codes
                                else "validated_information_output_unavailable"
                            ),
                        )
                    )
    new_state = state.model_copy(
        update={
            "answer_plan_summary": summary,
            "tasks": tuple(tasks),
            "task_stack": task_stack,
            "active_goal_id": active_goal_id,
            "information_requests": tuple(information_requests),
            "delivered_response_strategy_history": tuple(
                delivered_by_task.values()
            )[-100:],
            "response_delivery_history": (
                *state.response_delivery_history,
                record,
            )[-100:],
            "live_epoch_id": state.live_epoch_id or live_epoch_id,
        }
    )
    events.append(
        ResponseCommitSucceeded(
            turn_id=turn_metadata.turn_id,
            turn_number=state.turn_number,
            delivery_id=delivery_id,
            plan_id=plan_id,
        )
    )
    return ReductionResult(
        state=new_state,
        events=tuple(events),
        progress=state.progress,
    )
