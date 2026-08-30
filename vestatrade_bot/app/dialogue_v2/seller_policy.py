"""Deterministic seller policy for the Stage 2 shadow controller."""

from __future__ import annotations

from collections.abc import Iterable

from app.answer_v2.contracts import StrategyDirective
from app.catalog_v2.contracts import ReadinessStatus, TaskReadinessAssessment
from app.commerce_v2.contracts import (
    CapabilityMode,
    CommerceReadinessAssessment,
    CommerceReadinessStatus,
    CommerceWorkflowKind,
)

from .contracts import (
    ConstraintStatus,
    DialogueStateV2,
    InformationOutputRelation,
    InformationPurpose,
    InformationRequestStatus,
    InformationRequestV2,
    InformationSubjectScope,
    NextAction,
    NextActionKind,
    NextActionPlan,
    ResponseStrategyKind,
    RequestedInformationOutput,
    ShadowDeliveryStatus,
    TaskAct,
    TaskStatus,
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
_SELECTION_ACTS = {TaskAct.FIND, TaskAct.SELECT}


class SellerPolicy:
    """Choose an action from typed state only; never inspect message text."""

    def decide(
        self,
        state: DialogueStateV2,
        *,
        semantic_available: bool = True,
        policy_enabled: bool = True,
        ordered_multi_goal: bool = False,
        readiness_assessments: Iterable[TaskReadinessAssessment] = (),
        commerce_readiness_assessments: Iterable[
            CommerceReadinessAssessment
        ] = (),
        strategy_directives: Iterable[StrategyDirective] = (),
    ) -> NextActionPlan:
        plan = self._decide_base(
            state,
            semantic_available=semantic_available,
            policy_enabled=policy_enabled,
            ordered_multi_goal=ordered_multi_goal,
            readiness_assessments=readiness_assessments,
            commerce_readiness_assessments=commerce_readiness_assessments,
        )
        return self._apply_strategy_directive(plan, tuple(strategy_directives))

    def _decide_base(
        self,
        state: DialogueStateV2,
        *,
        semantic_available: bool = True,
        policy_enabled: bool = True,
        ordered_multi_goal: bool = False,
        readiness_assessments: Iterable[TaskReadinessAssessment] = (),
        commerce_readiness_assessments: Iterable[
            CommerceReadinessAssessment
        ] = (),
    ) -> NextActionPlan:
        if not semantic_available:
            return NextActionPlan(
                primary=NextAction(
                    kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
                    reason_code="semantic_result_unavailable",
                ),
                reason_codes=("semantic_result_unavailable",),
            )
        if not policy_enabled:
            return NextActionPlan(
                primary=NextAction(
                    kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
                    reason_code="seller_policy_v2_shadow_disabled",
                ),
                reason_codes=("seller_policy_v2_shadow_disabled",),
            )

        readiness_by_task = {
            item.task_id: item for item in readiness_assessments
        }
        commerce_by_task = {
            task_id: item
            for item in commerce_readiness_assessments
            for task_id in item.task_ids
        }
        current_tasks = sorted(
            (
                task
                for task in state.tasks
                if task.was_addressed_on(state.turn_number)
                and task.status not in {
                    TaskStatus.CANCELLED,
                    TaskStatus.SATISFIED,
                    TaskStatus.SUSPENDED,
                }
            ),
            key=lambda task: (task.priority, task.task_id),
        )
        actionable = current_tasks or [
            task
            for task in state.tasks
            if task.task_id == state.task_stack.active_task_id
            and task.status == TaskStatus.IN_PROGRESS
        ]
        # A compound request is represented as linked tasks.  After the user
        # answers a question for the foreground product, the next linked
        # product remains pending even though it was not repeated in the new
        # utterance.  Keep at most the policy's normal primary/secondary
        # cardinality, but make those typed sibling tasks eligible instead of
        # silently orphaning them until the customer names the product again.
        anchor_ids = {task.task_id for task in actionable}
        related_ids = {
            related_id
            for task in actionable
            for related_id in task.related_task_ids
        }
        related_ids.update(
            task.task_id
            for task in state.tasks
            if anchor_ids.intersection(task.related_task_ids)
        )
        committed_presented_task_ids = {
            candidate.task_id
            for candidate in (
                state.answer_plan_summary.presented_candidates
                if state.answer_plan_summary is not None
                and state.answer_plan_summary.delivery_status
                == ShadowDeliveryStatus.COMMITTED_TO_SESSION
                else ()
            )
        }
        related_selections = sorted(
            (
                task
                for task in state.tasks
                if task.task_id in related_ids
                and task.task_id not in anchor_ids
                # A linked sibling that already received catalogue cards in
                # the last committed answer remains available for an explicit
                # return, but must not be executed again merely because the
                # customer answered the other product's question.
                and task.task_id not in committed_presented_task_ids
                and task.act in _SELECTION_ACTS
                and task.status in {
                    TaskStatus.PENDING,
                    TaskStatus.IN_PROGRESS,
                    TaskStatus.BLOCKED,
                }
            ),
            key=lambda task: (task.priority, task.task_id),
        )
        task_ids = tuple(
            dict.fromkeys(
                (
                    *(task.task_id for task in (current_tasks or actionable)),
                    *(task.task_id for task in related_selections),
                )
            )
        )

        direct = [task for task in current_tasks if task.act in _DIRECT_ACTS]
        commerce_actionable = sorted(
            (
                task
                for task in state.tasks
                if task.task_id in commerce_by_task
                and task.status
                not in {
                    TaskStatus.CANCELLED,
                    TaskStatus.SATISFIED,
                    TaskStatus.SUSPENDED,
                }
            ),
            key=lambda task: (task.priority, task.task_id),
        )
        selections = [
            task for task in actionable if task.act in _SELECTION_ACTS
        ]
        selections.extend(related_selections)
        explanations = [task for task in current_tasks if task.act == TaskAct.EXPLAIN]
        comparisons = [task for task in current_tasks if task.act == TaskAct.COMPARE]
        compatibilities = [
            task for task in current_tasks if task.act == TaskAct.COMPATIBILITY
        ]
        calculations = [task for task in current_tasks if task.act == TaskAct.CALCULATE]
        handoffs = [task for task in current_tasks if task.act == TaskAct.HANDOFF]

        # A total-price request can legitimately carry a broad CHECK_PRICE
        # interpretation as well ("сколько выйдет за 20 шт.").  Treating the
        # latter as a regular card fact would discard the explicit arithmetic
        # task and return only a unit price.  This is intentionally narrow:
        # only CHECK_PRICE from the same turn/goal yields to CALCULATE; every
        # other direct product fact keeps its established priority.
        if calculations:
            calculation_goal_ids = {
                task.target_goal_id
                for task in calculations
                if task.target_goal_id is not None
            }
            calculation_turns = {task.source_turn for task in calculations}
            direct = [
                task
                for task in direct
                if not (
                    task.act == TaskAct.CHECK_PRICE
                    and (
                        (
                            task.target_goal_id is not None
                            and task.target_goal_id in calculation_goal_ids
                        )
                        or task.source_turn in calculation_turns
                    )
                )
            ]

        # Compatibility is a two-sided relationship request.  It owns the
        # turn before a generic information request or one-product direct
        # fact can reduce it to an answer about only one side.
        if compatibilities:
            return self._single(
                NextActionKind.CHECK_COMPATIBILITY,
                compatibilities[0].task_id,
                "explicit_compatibility_request",
                task_ids,
                state,
                secondary=self._selection_action(
                    state, selections[0], readiness_by_task
                ) if selections else None,
            )

        current_information_requests = tuple(
            request
            for request in state.information_requests
            if request.source_turn == state.turn_number
            and request.status == InformationRequestStatus.PENDING
        )
        pending_information_requests = current_information_requests
        reselected_old_information_request = False
        if not pending_information_requests:
            addressed_task_by_id = {
                task.task_id: task
                for task in current_tasks
                if task.act not in {TaskAct.GREETING, TaskAct.GRATITUDE}
                and task.status
                in {
                    TaskStatus.PENDING,
                    TaskStatus.IN_PROGRESS,
                    TaskStatus.BLOCKED,
                }
            }
            presented_summary = state.answer_plan_summary

            def request_scope_is_still_active(
                request: InformationRequestV2,
            ) -> bool:
                if (
                    request.subject_scope
                    != InformationSubjectScope.PRESENTED_CANDIDATES
                ):
                    return True
                return bool(
                    presented_summary is not None
                    and any(
                        (
                            request.goal_id is None
                            or candidate.goal_id == request.goal_id
                        )
                        and (
                            presented_summary.delivery_status
                            == ShadowDeliveryStatus.COMMITTED_TO_SESSION
                            or candidate.source_turn
                            < presented_summary.source_turn
                        )
                        for candidate in presented_summary.presented_candidates
                    )
                )

            retriable = sorted(
                (
                    request
                    for request in state.information_requests
                    if request.source_turn < state.turn_number
                    and request.status == InformationRequestStatus.PENDING
                    and request.task_id in addressed_task_by_id
                    and addressed_task_by_id[request.task_id].target_goal_id
                    == request.goal_id
                    and request_scope_is_still_active(request)
                ),
                key=lambda request: (request.source_turn, request.request_id),
            )
            if retriable:
                # Retry only one oldest still-scoped request, and only because
                # the customer explicitly readdressed its task this turn.  An
                # unrelated or neutral turn cannot create a response loop.
                pending_information_requests = (retriable[0],)
                reselected_old_information_request = True
        if pending_information_requests:
            primary_request = pending_information_requests[0]
            primary = self._information_request_action(primary_request)
            secondary = None
            if len(pending_information_requests) > 1:
                secondary = self._information_request_action(
                    pending_information_requests[1]
                )
            else:
                same_goal_selections = sorted(
                    (
                        task
                        for task in state.tasks
                        if task.target_goal_id == primary_request.goal_id
                        and task.act in _SELECTION_ACTS
                        and task.status in {
                            TaskStatus.PENDING,
                            TaskStatus.IN_PROGRESS,
                            TaskStatus.BLOCKED,
                        }
                    ),
                    key=lambda task: (task.priority, task.task_id),
                )
                selection_candidates = tuple(
                    {
                        task.task_id: task
                        for task in (*same_goal_selections, *selections)
                    }.values()
                )
                if selection_candidates:
                    secondary = self._selection_action(
                        state,
                        selection_candidates[0],
                        readiness_by_task,
                    )
            scoped_task_ids = tuple(
                dict.fromkeys(
                    (
                        primary_request.task_id,
                        *(
                            request.task_id
                            for request in pending_information_requests[1:2]
                        ),
                        *task_ids,
                        *(
                            (secondary.task_id,)
                            if secondary is not None and secondary.task_id is not None
                            else ()
                        ),
                    )
                )
            )
            reasons = [
                (
                    "pending_typed_information_request_reselected"
                    if reselected_old_information_request
                    else "typed_information_request_has_priority"
                ),
                primary.reason_code,
            ]
            if secondary is not None:
                reasons.extend(
                    (secondary.reason_code, "additional_customer_action_preserved")
                )
            return NextActionPlan(
                primary=primary,
                secondary=secondary,
                reason_codes=tuple(dict.fromkeys(reasons)),
                task_ids=scoped_task_ids,
                required_facts=self._required_facts(state),
                blocking_facts=self._blocking_facts(state),
            )

        if direct:
            primary = self._direct_or_commerce_action(
                direct[0], commerce_by_task
            )
            secondary = None
            if len(direct) > 1:
                secondary = self._direct_or_commerce_action(
                    direct[1], commerce_by_task
                )
            elif selections:
                secondary = self._selection_action(
                    state, selections[0], readiness_by_task
                )
            reasons = ["direct_question_has_priority"]
            if secondary:
                reasons.append("additional_customer_action_preserved")
            return NextActionPlan(
                primary=primary,
                secondary=secondary,
                reason_codes=tuple(reasons),
                task_ids=task_ids,
                required_facts=self._required_facts(state),
                blocking_facts=self._blocking_facts(state),
            )

        if commerce_actionable:
            task = commerce_actionable[0]
            action = self._commerce_action(
                task.task_id,
                commerce_by_task[task.task_id],
            )
            return NextActionPlan(
                primary=action,
                reason_codes=(action.reason_code,),
                task_ids=tuple(item.task_id for item in commerce_actionable),
                required_facts=self._required_facts(state),
                blocking_facts=self._blocking_facts(state),
            )

        # A typed comparison is an explicit user action.  The semantic frame
        # may also carry a broad ``explain`` candidate for wording such as
        # "какой дешевле"; that incomplete secondary interpretation must not
        # send the user back into Selection instead of comparing the cards
        # they already saw.  Fully typed direct fact requests were handled
        # above through ``pending_information_requests`` / ``direct``.
        if comparisons:
            return self._single(
                NextActionKind.COMPARE,
                comparisons[0].task_id,
                "explicit_comparison_request",
                task_ids,
                state,
                secondary=self._selection_action(
                    state, selections[0], readiness_by_task
                ) if selections else None,
            )

        if explanations:
            # A bare ``explain`` task says neither what information the
            # customer wants nor whether it concerns their installation or
            # previously presented candidates.  Inferring the sole unknown
            # goal fact here conflates those subjects and can turn a request
            # to inspect shown cards into instructions for measuring the
            # customer's system.  Typed InformationRequestV2 instances were
            # handled above; without one the safe action is to wait for a
            # complete semantic frame.
            # A separate, typed selection from the same compound turn may
            # still advance without interpreting the incomplete explanation.
            # This preserves independent customer actions while keeping the
            # ambiguous explanation itself out of the execution path.
            if selections:
                primary = self._selection_action(
                    state,
                    selections[0],
                    readiness_by_task,
                )
                independent_selection_goals = {
                    task.target_goal_id
                    for task in selections
                    if task.target_goal_id is not None
                }
                secondary = (
                    self._selection_action(
                        state,
                        selections[1],
                        readiness_by_task,
                    )
                    if len(selections) > 1
                    and (
                        not ordered_multi_goal
                        or len(independent_selection_goals) <= 1
                    )
                    else None
                )
                return NextActionPlan(
                    primary=primary,
                    secondary=secondary,
                    reason_codes=tuple(
                        dict.fromkeys(
                            (
                                "bare_explanation_deferred_missing_typed_information_request",
                                primary.reason_code,
                                *(
                                    ("ordered_multi_goal_first_task_only",)
                                    if ordered_multi_goal
                                    and len(independent_selection_goals) > 1
                                    else ()
                                ),
                                *(
                                    (secondary.reason_code,)
                                    if secondary is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                    task_ids=task_ids,
                    required_facts=self._required_facts(state),
                    blocking_facts=self._blocking_facts(state),
                )
            return self._single(
                NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
                explanations[0].task_id,
                "explanation_missing_typed_information_request",
                task_ids,
                state,
            )

        if calculations:
            return self._single(
                NextActionKind.CALCULATE_PRELIMINARY,
                calculations[0].task_id,
                "explicit_calculation_request",
                task_ids,
                state,
                secondary=self._selection_action(
                    state, selections[0], readiness_by_task
                ) if selections else None,
            )

        if handoffs:
            commerce = commerce_by_task.get(handoffs[0].task_id)
            if commerce is not None:
                action = self._commerce_action(handoffs[0].task_id, commerce)
                return NextActionPlan(
                    primary=action,
                    reason_codes=(action.reason_code,),
                    task_ids=task_ids,
                    required_facts=self._required_facts(state),
                    blocking_facts=self._blocking_facts(state),
                )
            return self._single(
                NextActionKind.START_OR_CONTINUE_HANDOFF,
                handoffs[0].task_id,
                "explicit_handoff_request",
                task_ids,
                state,
            )

        if selections:
            primary = self._selection_action(
                state, selections[0], readiness_by_task
            )
            # An explicitly ordered multi-goal request needs one result per
            # turn: SelectionResult has one contract/product kind and one
            # customer-visible scope.  Rendering two independent goals in the
            # same response would mix categories and make the outcome gate
            # (correctly) reject it.  The reducer has already retained the
            # sibling task with the customer's source-order priority, so the
            # controller sets ``ordered_multi_goal`` only for that case.
            independent_selection_goals = {
                task.target_goal_id
                for task in selections
                if task.target_goal_id is not None
            }
            secondary = (
                self._selection_action(
                    state,
                    selections[1],
                    readiness_by_task,
                )
                if len(selections) > 1
                and (
                    not ordered_multi_goal
                    or len(independent_selection_goals) <= 1
                )
                else None
            )
            return NextActionPlan(
                primary=primary,
                secondary=secondary,
                reason_codes=tuple(
                    dict.fromkeys(
                            (
                                primary.reason_code,
                                *(
                                    ("ordered_multi_goal_first_task_only",)
                                    if ordered_multi_goal
                                    and len(independent_selection_goals) > 1
                                    else ()
                                ),
                                *(
                                (
                                    secondary.reason_code,
                                    "additional_customer_action_preserved",
                                )
                                if secondary is not None
                                else ()
                            ),
                        )
                    )
                ),
                task_ids=task_ids or (selections[0].task_id,),
                required_facts=self._required_facts(state),
                blocking_facts=self._blocking_facts(state),
            )

        if actionable:
            task = actionable[0]
            if task.act in {TaskAct.GREETING, TaskAct.GRATITUDE}:
                reason = "social_turn_complete"
            else:
                reason = "no_stage2_capability_action"
            return self._single(
                NextActionKind.CLOSE_TASK,
                task.task_id,
                reason,
                task_ids or (task.task_id,),
                state,
            )

        return NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
                reason_code="accepted_semantics_has_no_actionable_task",
            ),
            reason_codes=("accepted_semantics_has_no_actionable_task",),
            required_facts=self._required_facts(state),
            blocking_facts=self._blocking_facts(state),
        )

    @staticmethod
    def _apply_strategy_directive(
        plan: NextActionPlan,
        directives: tuple[StrategyDirective, ...],
    ) -> NextActionPlan:
        """Apply a task-scoped loop decision without inspecting reply text."""

        if not directives:
            return plan
        by_task = {item.task_id: item for item in directives}
        mapping = {
            ResponseStrategyKind.ASK_DECISION_FACT: (
                NextActionKind.ASK_DECISION_CHANGING_QUESTION
            ),
            ResponseStrategyKind.EXPLAIN_HOW_TO_FIND_FACT: (
                NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
            ),
            ResponseStrategyKind.SHOW_PRELIMINARY_OPTIONS: (
                NextActionKind.SHOW_PRELIMINARY_OPTIONS
            ),
            ResponseStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS: (
                NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS
            ),
            ResponseStrategyKind.PRESENT_CONTROLLED_ANALOG: (
                NextActionKind.PRESENT_CONTROLLED_ANALOG
            ),
            ResponseStrategyKind.OFFER_VERIFIABLE_EXTERNAL_STEP: (
                NextActionKind.OFFER_VERIFIABLE_EXTERNAL_STEP
            ),
            ResponseStrategyKind.STATE_CAPABILITY_BOUNDARY: (
                NextActionKind.STATE_CAPABILITY_BOUNDARY
            ),
            ResponseStrategyKind.CLOSE_TASK: NextActionKind.CLOSE_TASK,
        }

        def replace(action: NextAction | None) -> tuple[NextAction | None, bool]:
            if action is None or action.task_id not in by_task:
                return action, False
            directive = by_task[action.task_id]
            # A direct customer question keeps priority.  A selection stored as
            # secondary may still change strategy independently.  Explicit
            # Compare, Compatibility and Calculate actions are executable
            # customer requests too; a stalled selection questionnaire must
            # not replace them with its escalation step.
            if action.kind in {
                NextActionKind.ANSWER_DIRECT_QUESTION,
                NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
                NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS,
                NextActionKind.COMPARE,
                NextActionKind.CHECK_COMPATIBILITY,
                NextActionKind.CALCULATE_PRELIMINARY,
                NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
            }:
                return action, False
            return (
                action.model_copy(
                    update={
                        "kind": mapping[directive.strategy],
                        # An information request's fact binding is part of its
                        # accepted semantic contract.  A loop strategy may
                        # change presentation, never the requested subject.
                        "fact_name": (
                            action.fact_name
                            if action.information_request_id is not None
                            else directive.fact_name
                        ),
                        "reason_code": directive.reason_codes[0],
                    }
                ),
                True,
            )

        primary, primary_changed = replace(plan.primary)
        secondary, secondary_changed = replace(plan.secondary)
        if not primary_changed and not secondary_changed:
            return plan
        return plan.model_copy(
            update={
                "primary": primary,
                "secondary": secondary,
                "reason_codes": tuple(
                    dict.fromkeys(
                        (
                            *plan.reason_codes,
                            "progress_guard_strategy_directive_applied",
                        )
                    )
                ),
            }
        )

    def _direct_or_commerce_action(
        self,
        task: object,
        commerce_by_task: dict[str, CommerceReadinessAssessment],
    ) -> NextAction:
        assessment = commerce_by_task.get(task.task_id)
        if assessment is not None:
            return self._commerce_action(task.task_id, assessment)
        return NextAction(
            kind=NextActionKind.ANSWER_DIRECT_QUESTION,
            task_id=task.task_id,
            reason_code="direct_question_has_priority",
        )

    @staticmethod
    def _commerce_action(
        task_id: str,
        assessment: CommerceReadinessAssessment,
    ) -> NextAction:
        if assessment.status in {
            CommerceReadinessStatus.NEEDS_CUSTOMER_FACT,
            CommerceReadinessStatus.NEEDS_PRODUCT_SELECTION,
            CommerceReadinessStatus.NEEDS_BUSINESS_FACT,
        }:
            return NextAction(
                kind=NextActionKind.COLLECT_COMMERCE_FACT,
                task_id=task_id,
                fact_name=assessment.recommended_next_field,
                reason_code=assessment.reason_codes[0],
            )
        if assessment.status == CommerceReadinessStatus.NEEDS_PREVIEW:
            return NextAction(
                kind=NextActionKind.PREVIEW_COMMERCE_REQUEST,
                task_id=task_id,
                reason_code="commerce_preview_required",
            )
        if assessment.status == CommerceReadinessStatus.NEEDS_CONSENT:
            return NextAction(
                kind=NextActionKind.REQUEST_SCOPED_CONSENT,
                task_id=task_id,
                reason_code="scoped_consent_required",
            )
        if assessment.status in {
            CommerceReadinessStatus.CAPABILITY_UNAVAILABLE,
            CommerceReadinessStatus.BLOCKED,
        }:
            return NextAction(
                kind=NextActionKind.STATE_COMMERCE_CAPABILITY_BOUNDARY,
                task_id=task_id,
                reason_code=assessment.reason_codes[0],
            )
        if assessment.status == CommerceReadinessStatus.CANCELLED:
            return NextAction(
                kind=(
                    NextActionKind.ACKNOWLEDGE_COMMERCE_OPT_OUT
                    if assessment.workflow_kind == CommerceWorkflowKind.HANDOFF
                    else NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS
                ),
                task_id=task_id,
                reason_code="commerce_workflow_cancelled",
            )
        if assessment.status == CommerceReadinessStatus.COMPLETED:
            return NextAction(
                kind=NextActionKind.REPORT_COMMERCE_EXECUTION_STATUS,
                task_id=task_id,
                reason_code="commerce_workflow_terminal",
            )
        if (
            assessment.status == CommerceReadinessStatus.READY_TO_PREPARE
            and assessment.capability_mode == CapabilityMode.VERIFIED_STATIC
        ):
            return NextAction(
                kind=NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
                task_id=task_id,
                reason_code="verified_commerce_fact_available",
            )
        return NextAction(
            kind=NextActionKind.PREPARE_COMMERCE_COMMAND,
            task_id=task_id,
            reason_code="commerce_command_ready_for_shadow_outbox",
        )

    @staticmethod
    def _information_request_action(
        request: InformationRequestV2,
    ) -> NextAction:
        """Map an explicit typed deliverable without inferring its subject."""

        outputs = set(request.requested_outputs)
        if (
            request.output_relation == InformationOutputRelation.ANY
            and RequestedInformationOutput.INSTRUCTION in outputs
            and request.fact_name is not None
        ):
            kind = NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
            reason = "typed_information_instruction_selected"
        elif (
            request.purpose == InformationPurpose.PROVENANCE
            or RequestedInformationOutput.VERIFIED_LINK in outputs
        ):
            kind = NextActionKind.STATE_CAPABILITY_BOUNDARY
            reason = "verified_information_source_unavailable"
        elif request.purpose == InformationPurpose.DETERMINATION_METHOD:
            kind = NextActionKind.EXPLAIN_HOW_TO_FIND_FACT
            reason = "typed_information_determination_method"
        elif request.purpose == InformationPurpose.VALUE:
            kind = NextActionKind.ANSWER_DIRECT_QUESTION
            reason = "typed_information_value_request"
        else:
            kind = NextActionKind.EXPLAIN_TERM_OR_METHOD
            reason = "typed_information_explanation_request"
        return NextAction(
            kind=kind,
            task_id=request.task_id,
            fact_name=request.fact_name,
            information_request_id=request.request_id,
            information_purpose=request.purpose,
            requested_outputs=request.requested_outputs,
            output_relation=request.output_relation,
            source_kind=request.source_kind,
            information_subject_scope=request.subject_scope,
            reason_code=reason,
        )

    def _selection_action(
        self,
        state: DialogueStateV2,
        task: object,
        readiness_by_task: dict[str, TaskReadinessAssessment] | None = None,
    ) -> NextAction:
        task_id = task.task_id
        goal_id = task.target_goal_id
        readiness = (readiness_by_task or {}).get(task_id)
        if readiness is not None:
            if readiness.status == ReadinessStatus.NEEDS_DECISION_FACT:
                return NextAction(
                    kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
                    task_id=task_id,
                    fact_name=readiness.recommended_question_fact,
                    reason_code="product_contract_requires_decision_fact",
                )
            if readiness.status == ReadinessStatus.EXACT_READY:
                if task.act == TaskAct.SELECT:
                    return NextAction(
                        kind=NextActionKind.RECOMMEND_ONE,
                        task_id=task_id,
                        reason_code="select_exact_candidates_recommend_one",
                    )
                return NextAction(
                    kind=NextActionKind.SEARCH_EXACT,
                    task_id=task_id,
                    reason_code="product_contract_exact_ready",
                )
            if readiness.status == ReadinessStatus.PRELIMINARY_READY:
                return NextAction(
                    kind=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
                    task_id=task_id,
                    reason_code="product_contract_preliminary_ready",
                )
            if readiness.status in {
                ReadinessStatus.BLOCKED,
                ReadinessStatus.UNSUPPORTED,
                ReadinessStatus.AMBIGUOUS,
            }:
                return NextAction(
                    kind=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
                    task_id=task_id,
                    reason_code="product_contract_honest_boundary",
                )
        active_facts = [
            fact for fact in state.constraints
            if fact.active and (goal_id is None or fact.goal_id == goal_id)
        ]
        terminal_fact_names = {
            fact.name
            for fact in active_facts
            if fact.status in {
                ConstraintStatus.KNOWN,
                ConstraintStatus.UNKNOWN,
                ConstraintStatus.REFUSED,
                ConstraintStatus.DEFERRED,
            }
        }
        ambiguity = next(
            (
                item
                for item in state.ambiguities
                if item.source_turn == state.turn_number
                and item.decision_changing
                and not item.resolved
                and item.kind not in terminal_fact_names
            ),
            None,
        )
        if ambiguity is not None:
            return NextAction(
                kind=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
                task_id=task_id,
                fact_name=ambiguity.kind,
                reason_code="unresolved_decision_changing_ambiguity",
            )

        non_known = [
            fact for fact in active_facts if fact.status != ConstraintStatus.KNOWN
        ]
        known = [
            fact for fact in active_facts if fact.status == ConstraintStatus.KNOWN
        ]
        goal = next(
            (goal for goal in state.product_goals if goal.goal_id == goal_id),
            None,
        )
        if non_known:
            return NextAction(
                kind=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
                task_id=task_id,
                reason_code="unavailable_fact_must_not_be_reasked",
            )
        if goal is not None and (goal.canonical_type or goal.category.value != "other") and known:
            if task.act == TaskAct.SELECT:
                return NextAction(
                    kind=NextActionKind.RECOMMEND_ONE,
                    task_id=task_id,
                    reason_code="select_confirmed_goal_recommend_one",
                )
            return NextAction(
                kind=NextActionKind.SEARCH_EXACT,
                task_id=task_id,
                reason_code="confirmed_goal_and_constraints_available",
            )
        return NextAction(
            kind=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            task_id=task_id,
            reason_code="insufficient_contract_data_for_exact_search",
        )

    @staticmethod
    def _required_facts(state: DialogueStateV2) -> tuple[str, ...]:
        terminal = {
            fact.name for fact in state.constraints if fact.active
        }
        return tuple(
            dict.fromkeys(
                item.kind
                for item in state.ambiguities
                if item.source_turn == state.turn_number
                and item.decision_changing
                and not item.resolved
                and item.kind not in terminal
            )
        )

    @staticmethod
    def _blocking_facts(state: DialogueStateV2) -> tuple[str, ...]:
        current_goal_ids = {
            task.target_goal_id
            for task in state.tasks
            if task.was_addressed_on(state.turn_number) and task.target_goal_id
        }
        if not current_goal_ids and state.active_goal_id:
            current_goal_ids = {state.active_goal_id}
        return tuple(
            dict.fromkeys(
                fact.name
                for fact in state.constraints
                if fact.active
                and fact.status != ConstraintStatus.KNOWN
                and (
                    not current_goal_ids
                    or fact.goal_id in current_goal_ids
                )
            )
        )

    def _single(
        self,
        kind: NextActionKind,
        task_id: str,
        reason: str,
        task_ids: tuple[str, ...],
        state: DialogueStateV2,
        *,
        fact_name: str | None = None,
        secondary: NextAction | None = None,
    ) -> NextActionPlan:
        return NextActionPlan(
            primary=NextAction(
                kind=kind,
                task_id=task_id,
                fact_name=fact_name,
                reason_code=reason,
            ),
            secondary=secondary,
            reason_codes=(reason,),
            task_ids=task_ids,
            required_facts=self._required_facts(state),
            blocking_facts=self._blocking_facts(state),
        )
