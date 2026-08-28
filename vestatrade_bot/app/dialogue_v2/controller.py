"""Single coordinating component for the shadow V2 reducer and policy."""

from __future__ import annotations

from time import monotonic
from typing import Literal

from pydantic import Field

from app.agents.semantic_interpreter import SemanticInterpretationResult
from app.answer_v2.contracts import (
    AnswerPlanningResult,
    AnswerSourceSnapshot,
    AnswerValidationResult,
    RenderedAnswerResult,
    StrategyDirective,
    TaskProgressAssessment,
)
from app.answer_v2.planner import build_answer_plan
from app.answer_v2.progress import assess_task_progress
from app.answer_v2.renderer import ResponseRendererV2
from app.answer_v2.sources import attach_turn_source_evidence
from app.answer_v2.strategy import select_strategy_directives
from app.answer_v2.validator import validate_rendered_answer
from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    CatalogProductSnapshot,
    ContractResolutionStatus,
)
from app.catalog_v2.planner import plan_catalog_search
from app.catalog_v2.readiness import assess_task_readiness
from app.catalog_v2.registry import ProductContractRegistry
from app.catalog_v2.selection import bind_exact_named_product
from app.commerce_v2.contracts import (
    CommerceCapabilitySnapshot,
    CommerceContextSnapshot,
    CommercePlanningResult,
    CommerceWorkflowKind,
    WorkflowResolutionStatus,
)
from app.commerce_v2.planner import (
    apply_workflow_controls,
    plan_commerce_workflow,
)
from app.commerce_v2.readiness import (
    assess_commerce_readiness,
    materialize_workflow_state,
)
from app.commerce_v2.registry import (
    CommerceWorkflowRegistry,
    build_capability_snapshot,
    resolve_commerce_workflows,
)
from app.semantic_v2.bridge import adapt_delta_to_turn_understanding

from .contracts import (
    DialogueStateV2,
    FrozenModel,
    NextActionPlan,
    ReductionResult,
    TaskAct,
    TaskStatus,
    TurnMetadata,
)
from .reducer import (
    record_answer_shadow,
    record_catalog_planning,
    record_commerce_planning,
    record_policy_decision,
    reduce_dialogue_state,
)
from .seller_policy import SellerPolicy


class DialogueV2Outcome(FrozenModel):
    status: Literal["applied", "skipped", "failed"]
    state_before: DialogueStateV2
    state_after: DialogueStateV2
    reduction: ReductionResult | None = None
    next_action_plan: NextActionPlan | None = None
    catalog_planning: CatalogPlanningResult | None = None
    commerce_planning: CommercePlanningResult | None = None
    progress_assessments: tuple[TaskProgressAssessment, ...] = ()
    strategy_directives: tuple[StrategyDirective, ...] = ()
    answer_planning: AnswerPlanningResult | None = None
    response_rendering: RenderedAnswerResult | None = None
    grounding_validation: AnswerValidationResult | None = None
    stage5_error: str | None = None
    skip_reason: str | None = None
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)


class DialogueControllerV2:
    """Own the accepted semantic -> reducer -> policy shadow pipeline."""

    def __init__(
        self,
        policy: SellerPolicy | None = None,
        contract_registry: ProductContractRegistry | None = None,
        commerce_registry: CommerceWorkflowRegistry | None = None,
        response_renderer: ResponseRendererV2 | None = None,
    ) -> None:
        self.policy = policy or SellerPolicy()
        self.contract_registry = contract_registry or ProductContractRegistry()
        self.commerce_registry = commerce_registry or CommerceWorkflowRegistry()
        self.response_renderer = response_renderer or ResponseRendererV2()

    def run(
        self,
        previous_state: DialogueStateV2 | None,
        semantic_result: SemanticInterpretationResult,
        turn_metadata: TurnMetadata,
        *,
        policy_enabled: bool = True,
        product_contracts_enabled: bool = False,
        catalog_planner_enabled: bool = False,
        solution_plan_enabled: bool = False,
        catalog_snapshot: tuple[CatalogProductSnapshot, ...] = (),
        commerce_workflows_enabled: bool = False,
        handoff_workflow_enabled: bool = False,
        commerce_outbox_enabled: bool = False,
        commerce_context: CommerceContextSnapshot | None = None,
        commerce_capabilities: CommerceCapabilitySnapshot | None = None,
        answer_plan_enabled: bool = False,
        response_renderer_enabled: bool = False,
        response_grounding_enabled: bool = False,
        progress_guard_enabled: bool = False,
        answer_source_snapshot: AnswerSourceSnapshot | None = None,
    ) -> DialogueV2Outcome:
        started = monotonic()
        state_before = previous_state or DialogueStateV2()
        if (
            semantic_result.status != "accepted"
            or not semantic_result.output_accepted
            or semantic_result.understanding is None
        ):
            reason = (
                semantic_result.rejection_reason
                or semantic_result.fallback_reason
                or f"semantic_status:{semantic_result.status}"
            )
            return DialogueV2Outcome(
                status="skipped",
                state_before=state_before,
                state_after=state_before,
                next_action_plan=self.policy.decide(
                    state_before,
                    semantic_available=False,
                ),
                skip_reason=reason,
                latency_ms=int((monotonic() - started) * 1000),
            )

        reducer_input = semantic_result.understanding
        if semantic_result.semantic_delta is not None:
            reducer_input = adapt_delta_to_turn_understanding(
                semantic_result.semantic_delta,
                semantic_result.understanding,
            )
        if reducer_input is None:
            return DialogueV2Outcome(
                status="skipped",
                state_before=state_before,
                state_after=state_before,
                next_action_plan=self.policy.decide(
                    state_before,
                    semantic_available=False,
                ),
                skip_reason="semantic_delta_rejected",
                latency_ms=int((monotonic() - started) * 1000),
            )

        reduction = reduce_dialogue_state(
            state_before,
            reducer_input,
            turn_metadata,
        )
        if product_contracts_enabled and catalog_snapshot:
            reduction = reduction.model_copy(
                update={
                    "state": bind_exact_named_product(
                        reduction.state,
                        catalog_snapshot,
                    )
                }
            )
        resolutions = ()
        readiness = ()
        if product_contracts_enabled:
            foreground_tasks = tuple(
                task
                for task in reduction.state.tasks
                if task.target_goal_id is not None
                and (
                    task.was_addressed_on(reduction.state.turn_number)
                    or task.task_id == reduction.state.task_stack.active_task_id
                )
            )
            foreground_ids = {task.task_id for task in foreground_tasks}
            related_ids = {
                related_id
                for task in foreground_tasks
                for related_id in task.related_task_ids
            }
            related_ids.update(
                task.task_id
                for task in reduction.state.tasks
                if foreground_ids.intersection(task.related_task_ids)
            )
            tasks = tuple(
                task for task in reduction.state.tasks
                if task.target_goal_id is not None
                and (
                    task.task_id in foreground_ids
                    or (
                        task.task_id in related_ids
                        and task.act in {TaskAct.FIND, TaskAct.SELECT}
                        and task.status in {
                            TaskStatus.PENDING,
                            TaskStatus.IN_PROGRESS,
                            TaskStatus.BLOCKED,
                        }
                    )
                )
            )
            resolutions = tuple(
                self.contract_registry.resolve_task(
                    reduction.state,
                    task,
                    catalog_snapshot,
                )
                for task in tasks
            )
            readiness = tuple(
                assess_task_readiness(
                    reduction.state,
                    task,
                    self.contract_registry.get(resolution.contract_id),
                    resolution,
                )
                for task, resolution in zip(tasks, resolutions, strict=True)
            )
        commerce_resolutions = ()
        commerce_workflows = ()
        commerce_readiness = ()
        processed_controls = ()
        control_rejections = ()
        stage4_enabled = commerce_workflows_enabled or handoff_workflow_enabled
        capability_snapshot = commerce_capabilities or build_capability_snapshot()
        context_snapshot = commerce_context or CommerceContextSnapshot()
        if stage4_enabled:
            resolved = resolve_commerce_workflows(
                reduction.state,
                self.commerce_registry,
            )
            commerce_resolutions = tuple(
                item
                for item in resolved
                if (
                    item.workflow_kind == CommerceWorkflowKind.HANDOFF
                    and handoff_workflow_enabled
                )
                or (
                    item.workflow_kind != CommerceWorkflowKind.HANDOFF
                    and commerce_workflows_enabled
                )
            )
            materialized = []
            for resolution in commerce_resolutions:
                if resolution.status != WorkflowResolutionStatus.RESOLVED:
                    continue
                contract = self.commerce_registry.get(resolution.contract_id)
                if contract is None:
                    continue
                materialized.append(
                    materialize_workflow_state(
                        reduction.state,
                        resolution,
                        contract,
                        capability_snapshot,
                        context_snapshot,
                    )
                )
            current_controls = tuple(
                item
                for item in reduction.state.commerce_controls
                if item.source_turn == reduction.state.turn_number
                and item.applied_workflow_id is None
                and item.rejected_reason is None
            )
            (
                commerce_workflows,
                processed_controls,
                control_rejections,
            ) = apply_workflow_controls(
                materialized,
                current_controls,
                turn_number=reduction.state.turn_number,
            )
            commerce_readiness = tuple(
                assess_commerce_readiness(
                    reduction.state,
                    workflow,
                    self.commerce_registry.get(workflow.contract_id),
                    capability_snapshot,
                )
                for workflow in commerce_workflows
                if self.commerce_registry.get(workflow.contract_id) is not None
            )
        stage5_enabled = bool(
            answer_plan_enabled
            or response_renderer_enabled
            or response_grounding_enabled
            or progress_guard_enabled
        )
        stage5_error = None
        progress_assessments = ()
        strategy_directives = ()
        if stage5_enabled:
            try:
                progress_assessments = assess_task_progress(
                    state_before,
                    reduction.state,
                    turn_metadata,
                )
                if progress_guard_enabled:
                    strategy_directives = select_strategy_directives(
                        reduction.state,
                        progress_assessments,
                        readiness,
                    )
            except Exception as exc:
                stage5_error = (
                    f"progress_or_strategy:{type(exc).__name__}: {exc}"
                )[:1200]
        plan = self.policy.decide(
            reduction.state,
            policy_enabled=policy_enabled,
            readiness_assessments=readiness,
            commerce_readiness_assessments=commerce_readiness,
            strategy_directives=strategy_directives,
        )
        recorded = record_policy_decision(reduction, plan, turn_metadata)
        catalog_planning = None
        if product_contracts_enabled:
            if catalog_planner_enabled:
                catalog_planning = plan_catalog_search(
                    recorded.state,
                    plan,
                    readiness,
                    catalog_snapshot,
                    self.contract_registry,
                    solution_enabled=solution_plan_enabled,
                    contract_resolutions=resolutions,
                )
            else:
                catalog_planning = CatalogPlanningResult(
                    status="skipped",
                    contract_resolutions=resolutions,
                    readiness_assessments=readiness,
                    unsupported_task_ids=tuple(
                        item.task_id for item in resolutions
                        if item.status != ContractResolutionStatus.RESOLVED
                    ),
                    reason_codes=("catalog_planner_v2_shadow_disabled",),
                )
            recorded = record_catalog_planning(
                recorded,
                catalog_planning,
                turn_metadata,
            )
        commerce_planning = None
        if stage4_enabled:
            commerce_planning = plan_commerce_workflow(
                recorded.state,
                plan,
                commerce_resolutions,
                commerce_workflows,
                commerce_readiness,
                catalog_planning,
                capability_snapshot,
                self.commerce_registry,
                controls=processed_controls,
                control_rejections=control_rejections,
                outbox_enabled=commerce_outbox_enabled,
            )
            processed_by_id = {
                item.control_id: item for item in commerce_planning.controls
            }
            merged_controls = tuple(
                processed_by_id.get(item.control_id, item)
                for item in recorded.state.commerce_controls
            )
            known_control_ids = {item.control_id for item in merged_controls}
            merged_controls = (
                *merged_controls,
                *(
                    item for item in commerce_planning.controls
                    if item.control_id not in known_control_ids
                ),
            )
            commerce_planning = commerce_planning.model_copy(
                update={
                    "controls": merged_controls[-100:],
                }
            )
            recorded = record_commerce_planning(
                recorded,
                commerce_planning,
                turn_metadata,
            )
        answer_planning = None
        response_rendering = None
        grounding_validation = None
        if stage5_enabled:
            try:
                progress_assessments = assess_task_progress(
                    state_before,
                    recorded.state,
                    turn_metadata,
                    catalog_planning=catalog_planning,
                    commerce_planning=commerce_planning,
                )
                if answer_plan_enabled:
                    source_snapshot = answer_source_snapshot or AnswerSourceSnapshot(
                        source_revision="empty_stage5_source_snapshot"
                    )
                    source_snapshot = attach_turn_source_evidence(
                        source_snapshot,
                        catalog_planning,
                        commerce_planning,
                        recorded.state,
                    )
                    answer_planning = build_answer_plan(
                        recorded.state,
                        plan,
                        catalog_planning,
                        commerce_planning,
                        source_snapshot,
                        turn_id=turn_metadata.turn_id,
                    )
                    if (
                        answer_planning.answer_plan is not None
                        and (response_renderer_enabled or response_grounding_enabled)
                    ):
                        response_rendering = self.response_renderer.render(
                            answer_planning.answer_plan,
                            naturalize=response_renderer_enabled,
                        )
                    if (
                        response_grounding_enabled
                        and answer_planning.answer_plan is not None
                        and response_rendering is not None
                        and response_rendering.rendered_answer is not None
                    ):
                        grounding_validation = validate_rendered_answer(
                            answer_planning.answer_plan,
                            response_rendering.rendered_answer,
                            source_snapshot,
                        )
                        if (
                            grounding_validation.status == "rejected"
                            and response_rendering.rendered_answer.renderer == "llm"
                            and response_rendering.deterministic_fallback is not None
                        ):
                            fallback_validation = validate_rendered_answer(
                                answer_planning.answer_plan,
                                response_rendering.deterministic_fallback,
                                source_snapshot,
                            )
                            if fallback_validation.status == "accepted":
                                response_rendering = response_rendering.model_copy(
                                    update={
                                        "status": "fallback",
                                        "rendered_answer": (
                                            response_rendering.deterministic_fallback
                                        ),
                                        "llm_output_accepted": False,
                                        "rejection_reason": (
                                            "llm_draft_failed_grounding_validation"
                                        ),
                                        "reason_codes": tuple(
                                            dict.fromkeys(
                                                (
                                                    *response_rendering.reason_codes,
                                                    "deterministic_fallback_selected",
                                                )
                                            )
                                        ),
                                    }
                                )
                                grounding_validation = fallback_validation.model_copy(
                                    update={
                                        "reason_codes": (
                                            *fallback_validation.reason_codes,
                                            "llm_draft_rejected_before_fallback",
                                        )
                                    }
                                )
                recorded = record_answer_shadow(
                    recorded,
                    answer_planning,
                    grounding_validation,
                    plan,
                    progress_assessments,
                    turn_metadata,
                )
            except Exception as exc:
                # Stage 5 is independently fail-open.  Earlier V2 state stays
                # valid and the legacy response has already been produced.
                failure = f"answer_pipeline:{type(exc).__name__}: {exc}"[:1200]
                stage5_error = (
                    f"{stage5_error}; {failure}" if stage5_error else failure
                )[:1200]
        return DialogueV2Outcome(
            status="applied",
            state_before=state_before,
            state_after=recorded.state,
            reduction=recorded,
            next_action_plan=plan,
            catalog_planning=catalog_planning,
            commerce_planning=commerce_planning,
            progress_assessments=tuple(progress_assessments),
            strategy_directives=tuple(strategy_directives),
            answer_planning=answer_planning,
            response_rendering=response_rendering,
            grounding_validation=grounding_validation,
            stage5_error=stage5_error,
            latency_ms=int((monotonic() - started) * 1000),
        )
