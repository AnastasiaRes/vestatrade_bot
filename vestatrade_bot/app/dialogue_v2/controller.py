"""Single coordinating component for the shadow V2 reducer and policy."""

from __future__ import annotations

from time import monotonic
from typing import Literal

from pydantic import Field

from app.agents.semantic_interpreter import SemanticInterpretationResult
from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    CatalogProductSnapshot,
    ContractResolutionStatus,
)
from app.catalog_v2.planner import plan_catalog_search
from app.catalog_v2.readiness import assess_task_readiness
from app.catalog_v2.registry import ProductContractRegistry

from .contracts import (
    DialogueStateV2,
    FrozenModel,
    NextActionPlan,
    ReductionResult,
    TurnMetadata,
)
from .reducer import (
    record_catalog_planning,
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
    skip_reason: str | None = None
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)


class DialogueControllerV2:
    """Own the accepted semantic -> reducer -> policy shadow pipeline."""

    def __init__(
        self,
        policy: SellerPolicy | None = None,
        contract_registry: ProductContractRegistry | None = None,
    ) -> None:
        self.policy = policy or SellerPolicy()
        self.contract_registry = contract_registry or ProductContractRegistry()

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

        reduction = reduce_dialogue_state(
            state_before,
            semantic_result.understanding,
            turn_metadata,
        )
        resolutions = ()
        readiness = ()
        if product_contracts_enabled:
            tasks = tuple(
                task for task in reduction.state.tasks
                if task.target_goal_id is not None
                and (
                    task.source_turn == reduction.state.turn_number
                    or task.task_id == reduction.state.task_stack.active_task_id
                )
            )
            resolutions = tuple(
                self.contract_registry.resolve_task(reduction.state, task)
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
        plan = self.policy.decide(
            reduction.state,
            policy_enabled=policy_enabled,
            readiness_assessments=readiness,
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
        return DialogueV2Outcome(
            status="applied",
            state_before=state_before,
            state_after=recorded.state,
            reduction=recorded,
            next_action_plan=plan,
            catalog_planning=catalog_planning,
            latency_ms=int((monotonic() - started) * 1000),
        )
