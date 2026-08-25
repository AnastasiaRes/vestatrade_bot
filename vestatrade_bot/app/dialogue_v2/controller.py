"""Single coordinating component for the shadow V2 reducer and policy."""

from __future__ import annotations

from time import monotonic
from typing import Literal

from pydantic import Field

from app.agents.semantic_interpreter import SemanticInterpretationResult

from .contracts import (
    DialogueStateV2,
    FrozenModel,
    NextActionPlan,
    ReductionResult,
    TurnMetadata,
)
from .reducer import record_policy_decision, reduce_dialogue_state
from .seller_policy import SellerPolicy


class DialogueV2Outcome(FrozenModel):
    status: Literal["applied", "skipped", "failed"]
    state_before: DialogueStateV2
    state_after: DialogueStateV2
    reduction: ReductionResult | None = None
    next_action_plan: NextActionPlan | None = None
    skip_reason: str | None = None
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)


class DialogueControllerV2:
    """Own the accepted semantic -> reducer -> policy shadow pipeline."""

    def __init__(self, policy: SellerPolicy | None = None) -> None:
        self.policy = policy or SellerPolicy()

    def run(
        self,
        previous_state: DialogueStateV2 | None,
        semantic_result: SemanticInterpretationResult,
        turn_metadata: TurnMetadata,
        *,
        policy_enabled: bool = True,
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
        plan = self.policy.decide(
            reduction.state,
            policy_enabled=policy_enabled,
        )
        recorded = record_policy_decision(reduction, plan, turn_metadata)
        return DialogueV2Outcome(
            status="applied",
            state_before=state_before,
            state_after=recorded.state,
            reduction=recorded,
            next_action_plan=plan,
            latency_ms=int((monotonic() - started) * 1000),
        )
