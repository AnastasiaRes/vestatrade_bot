"""Protected-Preview delivery for an already active V2 clarification.

The normal rollout remains fail-closed: an unsupported answer plan never
replaces Legacy.  In protected QA Preview, however, replacing a verified V2
clarification with Legacy hides the exact semantic/planning outcome under
test.  This adapter permits only the renderer's already grounded, typed
clarification for a task that V2 itself had established on an earlier turn.
"""

from __future__ import annotations

import hashlib
import json

from app.answer_v2.contracts import AnswerPlanStatus, AnswerSourceSnapshot
from app.dialogue_v2.contracts import ShadowDeliveryStatus
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import ChatResponse

from .contracts import ProductScopeEffect, V2TurnCandidate


def _digest_response(response: ChatResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"debug"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_v2_preview_continuation_candidate(
    outcome: DialogueV2Outcome,
    base_candidate: V2TurnCandidate,
    source_snapshot: AnswerSourceSnapshot | None,
    *,
    session_id: str,
) -> V2TurnCandidate | None:
    """Expose one grounded V2 clarification only in protected Preview.

    This does not execute a capability, retrieve products, alter selection
    scope or relax a gate.  It simply carries the existing deterministic
    renderer result across the otherwise non-deliverable `unsupported` plan
    status.  The caller is responsible for invoking it exclusively for
    `v2_preview` requests.
    """

    if (
        source_snapshot is None
        or base_candidate.response is not None
        or base_candidate.answer_status != AnswerPlanStatus.UNSUPPORTED
        or not base_candidate.semantic_accepted
        or not base_candidate.contracts_resolved
        or not base_candidate.state_before.tasks
    ):
        return None
    rendering = outcome.response_rendering
    validation = outcome.grounding_validation
    rendered = rendering.rendered_answer if rendering is not None else None
    if (
        rendered is None
        or validation is None
        or validation.status != "accepted"
        or not rendered.text.strip()
    ):
        return None
    summary = outcome.state_after.answer_plan_summary
    if summary is None:
        return None
    response = ChatResponse(
        session_id=session_id,
        answer=rendered.text,
        products=[],
        need_handoff=False,
        handoff_status="none",
        handoff_ticket_id=None,
        debug={},
    )
    state_after = outcome.state_after.model_copy(
        update={
            "answer_plan_summary": summary.model_copy(
                update={
                    "validation_status": "accepted",
                    "delivery_status": ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
                }
            )
        }
    )
    return base_candidate.model_copy(
        update={
            "response": response,
            "state_after": state_after,
            "validation_status": "accepted",
            "response_digest": _digest_response(response),
            "answer_status": AnswerPlanStatus.READY,
            "product_scope_effect": ProductScopeEffect.PRESERVE,
            "focus_product_sku": None,
            "eligible_for_delivery": True,
            "rejection_reason_codes": (),
        }
    )
