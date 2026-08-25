from __future__ import annotations

import json
import os

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter
from app.commerce_v2.contracts import (
    CommerceWorkflowKind,
    CommerceWorkflowStatus,
    ConsentStatus,
)
from app.commerce_v2.registry import build_capability_snapshot
from app.config import get_settings
from app.dialogue_v2.contracts import DialogueStateV2, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.models import PendingQuestionState, SessionState
from app.openrouter_client import OpenRouterClient

from test_commerce_v2_workflows import _context, _facts, _semantic


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1" or not os.getenv("OPENROUTER_API_KEY"),
        reason="requires RUN_LIVE_LLM_TESTS=1 and OPENROUTER_API_KEY",
    ),
]


@pytest.fixture(scope="module")
def runtime():
    base = get_settings()
    model = os.getenv(
        "SEMANTIC_LIVE_MODEL",
        os.getenv("OPENROUTER_MODEL_STRONG", base.openrouter_model_strong),
    )
    settings = base.model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": os.environ["OPENROUTER_API_KEY"],
            "openrouter_model": model,
            "openrouter_model_strong": model,
            "llm_max_retries": 1,
        }
    )
    return (
        SemanticInterpreter(OpenRouterClient(settings), model=model),
        DialogueControllerV2(),
        build_capability_snapshot(_facts()),
    )


def _run(
    runtime,
    message: str,
    case: str,
    *,
    state: DialogueStateV2 | None = None,
    semantic_session: SessionState | None = None,
):
    interpreter, controller, capabilities = runtime
    session = semantic_session or SessionState(session_id=f"stage4-live-{case}")
    semantic = interpreter.interpret(message, session)
    if semantic.status != "accepted" or semantic.understanding is None:
        pytest.xfail(
            "Stage 1 semantic rejection, not Stage 4: "
            f"{semantic.rejection_reason or semantic.fallback_reason or semantic.status}"
        )
    outcome = controller.run(
        state,
        semantic,
        TurnMetadata(
            turn_id=f"stage4-live-{case}-{(state.turn_number if state else 0) + 1}"
        ),
        policy_enabled=True,
        commerce_workflows_enabled=True,
        handoff_workflow_enabled=True,
        commerce_outbox_enabled=True,
        commerce_context=_context(contact=True),
        commerce_capabilities=capabilities,
    )
    assert outcome.status == "applied", outcome.error
    assert outcome.commerce_planning is not None
    diagnostic = json.dumps(
        {
            "understanding": semantic.understanding.model_dump(mode="json"),
            "events": [
                item.model_dump(mode="json") for item in outcome.reduction.events
            ],
            "state": outcome.state_after.model_dump(mode="json"),
            "action": outcome.next_action_plan.model_dump(mode="json"),
            "commerce": outcome.commerce_planning.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    session.history.append({"role": "user", "content": message})
    return outcome, semantic.understanding, diagnostic, session


@pytest.mark.parametrize(
    ("case", "message", "expected"),
    [
        (
            "invoice",
            "Нужны три радиаторных клапана; подготовьте именно счёт для организации.",
            CommerceWorkflowKind.REQUEST_INVOICE,
        ),
        (
            "reserve",
            "Этот насос придержите до завтрашнего вечера, если возможно.",
            CommerceWorkflowKind.RESERVE_PRODUCT,
        ),
        (
            "order-status",
            "Проверьте, пожалуйста, что сейчас с заказом, который я уже оформил.",
            CommerceWorkflowKind.ORDER_STATUS,
        ),
        (
            "modify",
            "В уже оформленном заказе замените один радиатор на двухсекционный.",
            CommerceWorkflowKind.MODIFY_ORDER,
        ),
        (
            "cancel",
            "Ранее оформленный заказ больше не нужен — хочу его отменить.",
            CommerceWorkflowKind.CANCEL_ORDER,
        ),
        (
            "delivery",
            "Во сколько примерно обойдётся доставка насоса в Самару? Заказ пока не оформляю.",
            CommerceWorkflowKind.CHECK_DELIVERY,
        ),
        (
            "return",
            "Купленный клапан не подошёл по резьбе, хочу оформить возврат.",
            CommerceWorkflowKind.RETURN_PRODUCT,
        ),
        (
            "warranty",
            "Насос куплен недавно и перестал включаться — нужно обращение по гарантии.",
            CommerceWorkflowKind.WARRANTY,
        ),
        (
            "complaint",
            "Хочу оставить претензию: в комплекте котла не оказалось заявленной детали.",
            CommerceWorkflowKind.COMPLAINT,
        ),
        (
            "handoff",
            "Подготовьте мой вопрос о насосе и передайте его менеджеру после подтверждения.",
            CommerceWorkflowKind.HANDOFF,
        ),
    ],
)
def test_live_commerce_paraphrases_resolve_typed_workflow(
    runtime,
    case,
    message,
    expected,
) -> None:
    outcome, understanding, diagnostic, _ = _run(runtime, message, case)

    assert expected.value in {act.value for act in understanding.acts}, diagnostic
    assert expected in {
        item.workflow_kind
        for item in outcome.commerce_planning.workflow_resolutions
        if item.workflow_kind is not None
    }, diagnostic


def test_live_manager_mention_without_transfer_does_not_create_handoff(runtime) -> None:
    outcome, understanding, diagnostic, _ = _run(
        runtime,
        "Зачем здесь менеджер? Никому ничего не передавайте, просто объясните условия доставки.",
        "manager-meta-mention",
    )

    assert "handoff" not in {act.value for act in understanding.acts}, diagnostic
    assert all(
        item.workflow_kind != CommerceWorkflowKind.HANDOFF
        for item in outcome.state_after.commerce_workflows
    ), diagnostic


def test_live_selection_and_invoice_remain_separate_tasks(runtime) -> None:
    outcome, understanding, diagnostic, _ = _run(
        runtime,
        "Подберите два подходящих коллектора и отдельно подготовьте счёт на выбранные позиции.",
        "selection-invoice",
    )

    acts = {item.value for item in understanding.acts}
    assert {"select", "request_invoice"} <= acts, diagnostic
    current_acts = {
        task.act.value
        for task in outcome.state_after.tasks
        if task.source_turn == outcome.state_after.turn_number
    }
    assert {"select", "request_invoice"} <= current_acts, diagnostic


def test_live_explicit_confirmation_is_bound_by_reducer_not_llm(runtime) -> None:
    _, controller, capabilities = runtime
    first = controller.run(
        None,
        _semantic(["handoff"], products=[]),
        TurnMetadata(turn_id="stage4-live-consent-setup"),
        policy_enabled=True,
        commerce_workflows_enabled=True,
        handoff_workflow_enabled=True,
        commerce_outbox_enabled=True,
        commerce_context=_context(contact=True),
        commerce_capabilities=capabilities,
    )
    session = SessionState(
        session_id="stage4-live-consent",
        history=[
            {
                "role": "user",
                "content": "Передайте вопрос менеджеру после моего подтверждения.",
            }
        ],
        pending_question_state=PendingQuestionState(
            question_id="commerce.handoff.consent",
            text="Подтверждаете передачу подготовленного обращения менеджеру?",
            intent_type="handoff",
        ),
    )
    confirmed, understanding, diagnostic, _ = _run(
        runtime,
        "Да, подтверждаю.",
        "consent",
        state=first.state_after,
        semantic_session=session,
    )

    assert any(
        item.kind.value == "confirm" for item in understanding.workflow_controls
    ), diagnostic
    workflow = next(
        item
        for item in confirmed.state_after.commerce_workflows
        if item.workflow_kind == CommerceWorkflowKind.HANDOFF
    )
    assert workflow.consent.status == ConsentStatus.GRANTED, diagnostic
    assert workflow.status == CommerceWorkflowStatus.READY_TO_EXECUTE, diagnostic
    assert confirmed.state_after.commerce_outbox, diagnostic


def test_live_explicit_decline_cancels_only_pending_workflow(runtime) -> None:
    _, controller, capabilities = runtime
    first = controller.run(
        None,
        _semantic(["handoff"], products=[]),
        TurnMetadata(turn_id="stage4-live-decline-setup"),
        policy_enabled=True,
        commerce_workflows_enabled=True,
        handoff_workflow_enabled=True,
        commerce_outbox_enabled=True,
        commerce_context=_context(contact=True),
        commerce_capabilities=capabilities,
    )
    session = SessionState(
        session_id="stage4-live-decline",
        history=[
            {
                "role": "user",
                "content": "Передайте вопрос менеджеру после моего подтверждения.",
            }
        ],
        pending_question_state=PendingQuestionState(
            question_id="commerce.handoff.consent",
            text="Подтверждаете передачу подготовленного обращения менеджеру?",
            intent_type="handoff",
        ),
    )
    assert any(
        item.workflow_kind == CommerceWorkflowKind.HANDOFF
        for item in first.state_after.commerce_workflows
    )
    declined, understanding, diagnostic, _ = _run(
        runtime,
        "Нет, не подтверждаю.",
        "decline",
        state=first.state_after,
        semantic_session=session,
    )

    assert any(
        item.kind.value == "decline" for item in understanding.workflow_controls
    ), diagnostic
    workflow = next(
        item
        for item in declined.state_after.commerce_workflows
        if item.workflow_kind == CommerceWorkflowKind.HANDOFF
    )
    assert workflow.status == CommerceWorkflowStatus.CANCELLED, diagnostic
    assert workflow.consent.status == ConsentStatus.DENIED, diagnostic
    assert not declined.state_after.commerce_outbox, diagnostic
