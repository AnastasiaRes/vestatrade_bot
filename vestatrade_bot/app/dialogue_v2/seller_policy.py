"""Deterministic seller policy for the Stage 2 shadow controller."""

from __future__ import annotations

from .contracts import (
    ConstraintStatus,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    TaskAct,
    TaskStatus,
)


_DIRECT_ACTS = {
    TaskAct.CHECK_PRICE,
    TaskAct.CHECK_STOCK,
    TaskAct.GET_LINK,
    TaskAct.REQUEST_QUOTE,
    TaskAct.ORDER_STATUS,
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

        current_tasks = sorted(
            (
                task
                for task in state.tasks
                if task.source_turn == state.turn_number
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
        task_ids = tuple(task.task_id for task in current_tasks)

        direct = [task for task in current_tasks if task.act in _DIRECT_ACTS]
        selections = [task for task in actionable if task.act in _SELECTION_ACTS]
        explanations = [task for task in current_tasks if task.act == TaskAct.EXPLAIN]
        comparisons = [task for task in current_tasks if task.act == TaskAct.COMPARE]
        calculations = [task for task in current_tasks if task.act == TaskAct.CALCULATE]
        handoffs = [task for task in current_tasks if task.act == TaskAct.HANDOFF]

        if direct:
            primary = NextAction(
                kind=NextActionKind.ANSWER_DIRECT_QUESTION,
                task_id=direct[0].task_id,
                reason_code="direct_question_has_priority",
            )
            secondary = self._selection_action(state, selections[0]) if selections else None
            reasons = ["direct_question_has_priority"]
            if secondary:
                reasons.append("unfinished_selection_preserved")
            return NextActionPlan(
                primary=primary,
                secondary=secondary,
                reason_codes=tuple(reasons),
                task_ids=task_ids,
                required_facts=self._required_facts(state),
                blocking_facts=self._blocking_facts(state),
            )

        if explanations:
            return self._single(
                NextActionKind.EXPLAIN_TERM_OR_METHOD,
                explanations[0].task_id,
                "explicit_explanation_request",
                task_ids,
                state,
                secondary=self._selection_action(state, selections[0]) if selections else None,
            )

        if comparisons:
            return self._single(
                NextActionKind.COMPARE,
                comparisons[0].task_id,
                "explicit_comparison_request",
                task_ids,
                state,
                secondary=self._selection_action(state, selections[0]) if selections else None,
            )

        if calculations:
            return self._single(
                NextActionKind.CALCULATE_PRELIMINARY,
                calculations[0].task_id,
                "explicit_calculation_request",
                task_ids,
                state,
                secondary=self._selection_action(state, selections[0]) if selections else None,
            )

        if handoffs:
            return self._single(
                NextActionKind.START_OR_CONTINUE_HANDOFF,
                handoffs[0].task_id,
                "explicit_handoff_request",
                task_ids,
                state,
            )

        if selections:
            action = self._selection_action(state, selections[0])
            return NextActionPlan(
                primary=action,
                reason_codes=(action.reason_code,),
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

    def _selection_action(self, state: DialogueStateV2, task: object) -> NextAction:
        task_id = task.task_id
        goal_id = task.target_goal_id
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
            if task.source_turn == state.turn_number and task.target_goal_id
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
        secondary: NextAction | None = None,
    ) -> NextActionPlan:
        return NextActionPlan(
            primary=NextAction(
                kind=kind,
                task_id=task_id,
                reason_code=reason,
            ),
            secondary=secondary,
            reason_codes=(reason,),
            task_ids=task_ids,
            required_facts=self._required_facts(state),
            blocking_facts=self._blocking_facts(state),
        )
