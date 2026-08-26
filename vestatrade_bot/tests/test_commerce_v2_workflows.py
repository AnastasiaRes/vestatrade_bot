from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents.semantic_interpreter import (
    SemanticInterpretationResult,
    TurnUnderstanding,
)
from app.commerce_v2.contracts import (
    CapabilityMode,
    CommerceContextSnapshot,
    CommerceExecutionResult,
    CommerceExecutionStatus,
    CommerceFieldStatus,
    CommerceReadinessStatus,
    CommerceWorkflowKind,
    CommerceWorkflowStatus,
    ConsentStatus,
    OutboxStatus,
    SensitiveValueKind,
    SensitiveValueRef,
)
from app.commerce_v2.gateway import UnavailableCommerceGateway
from app.commerce_v2.context import build_commerce_context_snapshot
from app.commerce_v2.planner import plan_commerce_workflow
from app.commerce_v2.registry import (
    CommerceWorkflowRegistry,
    build_capability_snapshot,
)
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.dialogue_v2.reducer import record_commerce_execution_result
from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    CatalogProductRole,
    ProductKind,
    SolutionComponent,
    SolutionPlan,
)
from app.models import SessionState
from app.session_store import InMemorySessionStore, RedisSessionStore


def _facts(**overrides):
    values = {
        "delivery": "verified delivery policy",
        "payment": "draft payment policy",
        "returns": "draft return policy",
        "warranty": "draft warranty policy",
        "business_hours": "9-18",
        "pickup_points": ("branch",),
        "branches": (),
        "response_time": "one business day",
        "lead_times": {"quote": "1-2 days"},
        "drafted_sections": ("payment", "returns", "warranty"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(*, contact: bool = False) -> CommerceContextSnapshot:
    return CommerceContextSnapshot(
        contact_ref=(
            SensitiveValueRef(
                ref_id="session_customer_contact",
                kind=SensitiveValueKind.CONTACT,
                field_name="contact_ref",
                status=CommerceFieldStatus.KNOWN,
                source="test_contact_adapter",
                source_turn=0,
            )
            if contact
            else None
        ),
        business_fact_keys=("delivery", "payment", "returns", "warranty"),
        drafted_business_fact_keys=("payment", "returns", "warranty"),
    )


def _semantic(
    acts: list[str] | None = None,
    *,
    controls: list[str] | None = None,
    constraints: list[dict] | None = None,
    products: list[dict] | None = None,
    operation: str = "new",
) -> SemanticInterpretationResult:
    if products is None:
        products = [
            {
                "text": "насос",
                "canonical_type": "циркуляционный насос",
                "category": "pumps",
                "role": "target",
                "evidence": "насос",
            }
        ]
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.1",
            "language": "ru",
            "operation": operation,
            "acts": acts or [],
            "products": products,
            "constraints": constraints or [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [
                {"kind": kind, "evidence": kind} for kind in controls or []
            ],
            "answers_pending_question": bool(controls),
            "confidence": 0.99,
        }
    )
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def _run(
    semantic: SemanticInterpretationResult,
    *,
    state: DialogueStateV2 | None = None,
    turn_id: str = "turn-1",
    contact: bool = False,
    outbox: bool = True,
    capabilities=None,
):
    return DialogueControllerV2().run(
        state,
        semantic,
        TurnMetadata(turn_id=turn_id),
        policy_enabled=True,
        commerce_workflows_enabled=True,
        handoff_workflow_enabled=True,
        commerce_outbox_enabled=outbox,
        commerce_context=_context(contact=contact),
        commerce_capabilities=(capabilities or build_capability_snapshot(_facts())),
    )


def _transactional_capabilities():
    snapshot = build_capability_snapshot(_facts())
    return snapshot.model_copy(
        update={
            "capabilities": tuple(
                (
                    item.model_copy(
                        update={
                            "mode": CapabilityMode.TRANSACTIONAL_EXTERNAL,
                            "has_verifiable_receipt": True,
                            "result_verifiable": True,
                            "reason_code": "fake_transactional_test_capability",
                        }
                    )
                    if item.operation == CommerceWorkflowKind.HANDOFF
                    else item
                )
                for item in snapshot.capabilities
            )
        }
    )


class _FakeTransactionalGateway:
    def __init__(self, capabilities) -> None:
        self._capabilities = capabilities

    def describe_capabilities(self):
        return self._capabilities

    def execute(self, command):
        return CommerceExecutionResult(
            command_id=command.command_id,
            capability_id=command.capability_id,
            status=CommerceExecutionStatus.DELIVERED,
            receipt_ref="verified-test-receipt",
            receipt_verified=True,
            reason_code="fake_transactional_acknowledged",
        )


@pytest.mark.parametrize(
    ("act", "kind"),
    [
        ("request_quote", CommerceWorkflowKind.REQUEST_QUOTE),
        ("request_invoice", CommerceWorkflowKind.REQUEST_INVOICE),
        ("reserve_product", CommerceWorkflowKind.RESERVE_PRODUCT),
        ("place_order", CommerceWorkflowKind.PLACE_ORDER),
        ("order_status", CommerceWorkflowKind.ORDER_STATUS),
        ("modify_order", CommerceWorkflowKind.MODIFY_ORDER),
        ("cancel_order", CommerceWorkflowKind.CANCEL_ORDER),
        ("check_delivery", CommerceWorkflowKind.CHECK_DELIVERY),
        ("return_product", CommerceWorkflowKind.RETURN_PRODUCT),
        ("warranty", CommerceWorkflowKind.WARRANTY),
        ("complaint", CommerceWorkflowKind.COMPLAINT),
        ("handoff", CommerceWorkflowKind.HANDOFF),
    ],
)
def test_each_commerce_act_has_a_distinct_declarative_workflow(act, kind) -> None:
    outcome = _run(_semantic([act]), contact=True)

    assert outcome.status == "applied"
    assert outcome.commerce_planning is not None
    assert outcome.commerce_planning.workflow_resolutions[0].workflow_kind == kind
    assert outcome.state_after.commerce_workflows[0].workflow_kind == kind


def test_order_number_is_kept_only_as_opaque_reference_not_sku_or_pii() -> None:
    outcome = _run(
        _semantic(
            ["order_status"],
            constraints=[
                {
                    "name": "order_number",
                    "value": "148237",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "148237",
                }
            ],
        ),
        contact=True,
    )

    serialized = outcome.state_after.model_dump_json()
    assert "148237" not in serialized
    assert not any(fact.name == "sku" for fact in outcome.state_after.constraints)
    assert outcome.state_after.commerce_sensitive_values[0].kind == (
        SensitiveValueKind.ORDER_REFERENCE
    )


def test_contact_presence_is_not_consent() -> None:
    outcome = _run(_semantic(["handoff"]), contact=True)
    workflow = outcome.state_after.commerce_workflows[0]

    assert workflow.status == CommerceWorkflowStatus.AWAITING_CONSENT
    assert workflow.consent.status == ConsentStatus.AWAITING
    assert not outcome.state_after.commerce_outbox
    assert outcome.next_action_plan.primary.kind == (
        NextActionKind.PREVIEW_COMMERCE_REQUEST
    )


def test_scoped_confirmation_prepares_one_idempotent_shadow_command() -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id="handoff-1")
    second = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="handoff-2",
    )

    workflow = second.state_after.commerce_workflows[0]
    assert workflow.consent.status == ConsentStatus.GRANTED
    assert workflow.execution_status == CommerceExecutionStatus.PREPARED
    assert workflow.status == CommerceWorkflowStatus.READY_TO_EXECUTE
    assert len(second.state_after.commerce_outbox) == 1
    assert second.state_after.commerce_outbox[0].status == OutboxStatus.READY
    assert second.next_action_plan.primary.kind == (
        NextActionKind.PREPARE_COMMERCE_COMMAND
    )

    duplicate = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=second.state_after,
        contact=True,
        turn_id="handoff-2",
    )
    assert duplicate.state_after.commerce_outbox == second.state_after.commerce_outbox
    assert len(duplicate.state_after.commerce_outbox) == 1


def test_redundant_semantic_act_during_confirmation_reuses_prepared_workflow() -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id="noisy-confirm-1")
    confirmed = _run(
        _semantic(
            ["handoff"],
            controls=["confirm"],
            products=[],
            operation="continue",
        ),
        state=first.state_after,
        contact=True,
        turn_id="noisy-confirm-2",
    )

    assert len(confirmed.state_after.commerce_workflows) == 1
    workflow = confirmed.state_after.commerce_workflows[0]
    assert workflow.consent.status == ConsentStatus.GRANTED
    assert workflow.status == CommerceWorkflowStatus.READY_TO_EXECUTE
    assert len(confirmed.state_after.commerce_outbox) == 1


def test_ambiguous_confirmation_never_creates_a_command() -> None:
    first_handoff = _run(_semantic(["handoff"]), contact=True, turn_id="ambiguous-1")
    invoice = _run(
        _semantic(
            ["request_invoice"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 1,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "1",
                },
                {
                    "name": "company_requisites",
                    "value": "synthetic",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "synthetic",
                },
            ],
        ),
        state=first_handoff.state_after,
        contact=True,
        turn_id="ambiguous-2",
    )
    confirmed = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=invoice.state_after,
        contact=True,
        turn_id="ambiguous-3",
    )

    assert not confirmed.state_after.commerce_outbox
    assert any(
        item.reason_code == "workflow_control_target_ambiguous"
        for item in confirmed.commerce_planning.rejected_proposals
    )


@pytest.mark.parametrize("control", ["decline", "withdraw_consent", "opt_out"])
def test_decline_withdraw_and_opt_out_never_dispatch(control: str) -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id=f"{control}-1")
    if control == "withdraw_consent":
        first = _run(
            _semantic([], controls=["confirm"], products=[]),
            state=first.state_after,
            contact=True,
            turn_id=f"{control}-2",
            outbox=False,
        )
    result = _run(
        _semantic([], controls=[control], products=[]),
        state=first.state_after,
        contact=True,
        turn_id=f"{control}-3",
    )
    workflow = result.state_after.commerce_workflows[0]

    assert workflow.status == CommerceWorkflowStatus.CANCELLED
    assert workflow.execution_status == CommerceExecutionStatus.CANCELLED
    assert all(
        item.status in {OutboxStatus.CANCELLED, OutboxStatus.DUPLICATE_IGNORED}
        for item in result.state_after.commerce_outbox
    )


def test_explicit_resume_after_opt_out_reopens_only_handoff() -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id="resume-1")
    opted = _run(
        _semantic([], controls=["opt_out"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="resume-2",
    )
    resumed = _run(
        _semantic([], controls=["resume_after_opt_out"], products=[]),
        state=opted.state_after,
        contact=True,
        turn_id="resume-3",
    )

    workflow = resumed.state_after.commerce_workflows[0]
    assert workflow.opt_out is False
    assert workflow.status == CommerceWorkflowStatus.AWAITING_CONSENT
    assert workflow.consent.status == ConsentStatus.AWAITING


def test_payload_correction_invalidates_old_consent_and_changes_revision() -> None:
    base_constraints = [
        {
            "name": "sku",
            "value": "SKU-1",
            "status": "known",
            "polarity": "required",
            "evidence": "SKU-1",
        },
        {
            "name": "quantity",
            "value": 1,
            "status": "known",
            "polarity": "required",
            "evidence": "1",
        },
    ]
    first = _run(
        _semantic(["request_quote"], constraints=base_constraints),
        contact=True,
        turn_id="revision-1",
    )
    granted = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="revision-2",
        outbox=False,
    )
    corrected_constraints = [
        {
            "name": "quantity",
            "value": 2,
            "status": "known",
            "polarity": "required",
            "evidence": "2",
        },
    ]
    corrected = _run(
        _semantic(
            ["request_quote"], constraints=corrected_constraints, operation="correct"
        ),
        state=granted.state_after,
        contact=True,
        turn_id="revision-3",
        outbox=False,
    )
    workflow = corrected.state_after.commerce_workflows[0]

    assert workflow.payload_revision == 2
    assert workflow.consent.status == ConsentStatus.AWAITING
    assert workflow.preview_revision == 2


@pytest.mark.parametrize("status", ["unknown", "refused", "deferred"])
def test_terminal_quantity_status_is_not_reasked_forever(status: str) -> None:
    outcome = _run(
        _semantic(
            ["request_quote"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": None,
                    "status": status,
                    "polarity": "required",
                    "evidence": status,
                },
            ],
        ),
        contact=True,
    )
    assessment = outcome.commerce_planning.readiness_assessments[0]

    assert assessment.recommended_next_field is None
    assert getattr(assessment, f"{status}_fields") == ("quantity",)


def test_catalog_candidates_are_not_silently_promoted_to_order_lines() -> None:
    outcome = _run(_semantic(["place_order"]), contact=True)
    assessment = outcome.commerce_planning.readiness_assessments[0]

    assert assessment.status == CommerceReadinessStatus.CAPABILITY_UNAVAILABLE
    assert not assessment.product_refs
    assert not outcome.state_after.commerce_outbox


def test_delivery_information_uses_verified_static_capability_without_command() -> None:
    outcome = _run(
        _semantic(
            ["check_delivery"],
            products=[],
            constraints=[
                {
                    "name": "destination_region",
                    "value": "Самара",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "Самара",
                }
            ],
        )
    )
    assessment = outcome.commerce_planning.readiness_assessments[0]

    assert assessment.capability_mode == CapabilityMode.VERIFIED_STATIC
    assert assessment.status == CommerceReadinessStatus.READY_TO_PREPARE
    assert outcome.next_action_plan.primary.kind == (
        NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION
    )
    assert not outcome.state_after.commerce_outbox


def test_local_draft_and_external_delivery_are_distinct_reducer_results() -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id="delivery-1")
    prepared = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="delivery-2",
    )
    command = prepared.state_after.commerce_outbox[0].command

    local = record_commerce_execution_result(
        prepared.reduction,
        CommerceExecutionResult(
            command_id=command.command_id,
            capability_id=command.capability_id,
            status=CommerceExecutionStatus.LOCAL_DRAFT_SAVED,
            reason_code="local_draft_saved",
        ),
        TurnMetadata(turn_id="delivery-local"),
    )
    assert local.state.commerce_workflows[0].status == (
        CommerceWorkflowStatus.LOCAL_DRAFT_SAVED
    )
    assert local.state.commerce_workflows[0].status != CommerceWorkflowStatus.DELIVERED

    capabilities = _transactional_capabilities()
    transactional = _run(
        _semantic(["handoff"]),
        contact=True,
        turn_id="delivery-external-1",
        capabilities=capabilities,
    )
    transactional = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=transactional.state_after,
        contact=True,
        turn_id="delivery-external-2",
        capabilities=capabilities,
    )
    gateway = _FakeTransactionalGateway(capabilities)
    delivered = record_commerce_execution_result(
        transactional.reduction,
        gateway.execute(transactional.state_after.commerce_outbox[0].command),
        TurnMetadata(turn_id="delivery-external"),
    )
    assert delivered.state.commerce_workflows[0].status == (
        CommerceWorkflowStatus.DELIVERED
    )
    assert delivered.state.commerce_workflows[0].external_receipt_ref == (
        "verified-test-receipt"
    )


def test_local_draft_capability_cannot_forge_delivered_status() -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id="forged-1")
    prepared = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="forged-2",
    )
    command = prepared.state_after.commerce_outbox[0].command
    rejected = record_commerce_execution_result(
        prepared.reduction,
        CommerceExecutionResult(
            command_id=command.command_id,
            capability_id=command.capability_id,
            status=CommerceExecutionStatus.DELIVERED,
            receipt_ref="untrusted-local-receipt",
            receipt_verified=True,
            reason_code="forged",
        ),
        TurnMetadata(turn_id="forged-result"),
    )

    assert rejected.state == prepared.reduction.state
    assert rejected.rejected_proposals[-1].reason_code == (
        "unverified_transactional_delivery_result"
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CommerceExecutionStatus.FAILED, CommerceWorkflowStatus.DELIVERY_FAILED),
        (
            CommerceExecutionStatus.DELIVERY_UNKNOWN,
            CommerceWorkflowStatus.DELIVERY_UNKNOWN,
        ),
    ],
)
def test_failed_or_unknown_gateway_result_never_becomes_delivered(
    status, expected
) -> None:
    first = _run(_semantic(["handoff"]), contact=True, turn_id=f"gateway-{status}-1")
    prepared = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id=f"gateway-{status}-2",
    )
    command = prepared.state_after.commerce_outbox[0].command
    recorded = record_commerce_execution_result(
        prepared.reduction,
        CommerceExecutionResult(
            command_id=command.command_id,
            capability_id=command.capability_id,
            status=status,
            reason_code="gateway_result",
        ),
        TurnMetadata(turn_id=f"gateway-{status}-result"),
    )

    assert recorded.state.commerce_workflows[0].status == expected
    assert recorded.state.commerce_outbox[0].command.idempotency_key == (
        command.idempotency_key
    )


def test_delivered_result_without_verified_receipt_is_invalid() -> None:
    with pytest.raises(ValueError, match="requires a verified receipt"):
        CommerceExecutionResult(
            command_id="command",
            capability_id="capability",
            status=CommerceExecutionStatus.DELIVERED,
            reason_code="invalid",
        )


def test_unavailable_gateway_cannot_claim_success() -> None:
    gateway = UnavailableCommerceGateway()
    first = _run(_semantic(["handoff"]), contact=True, turn_id="unavailable-1")
    prepared = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="unavailable-2",
    )
    result = gateway.execute(prepared.state_after.commerce_outbox[0].command)

    assert result.status == CommerceExecutionStatus.FAILED
    assert result.receipt_ref is None


def test_planner_is_deterministic_and_does_not_mutate_inputs() -> None:
    semantic = _semantic(["handoff"])
    state = DialogueStateV2()
    before = deepcopy(state)

    first = _run(semantic, state=state, contact=True, turn_id="deterministic")
    second = _run(semantic, state=state, contact=True, turn_id="deterministic")

    assert first == second
    assert state == before


def test_stage4_state_round_trips_in_memory_and_redis_and_old_v2_loads() -> None:
    outcome = _run(_semantic(["handoff"]), contact=True)
    state = SessionState(
        session_id="stage4-roundtrip", dialogue_state_v2=outcome.state_after
    )

    memory = InMemorySessionStore()
    memory.save(state)
    assert memory.snapshot(state.session_id).dialogue_state_v2 == outcome.state_after

    encoded = RedisSessionStore._encode(state)
    decoded = RedisSessionStore._decode(encoded)
    assert decoded.dialogue_state_v2 == outcome.state_after

    old_payload = json.dumps(
        {
            "session_id": "old-v2",
            "dialogue_state_v2": {
                "schema_version": "2.0",
                "turn_number": 1,
                "applied_turn_ids": ["old-turn"],
            },
        }
    )
    restored = SessionState.model_validate_json(old_payload)
    assert restored.dialogue_state_v2 is not None
    assert restored.dialogue_state_v2.commerce_workflows == ()
    assert restored.dialogue_state_v2.commerce_outbox == ()


def test_commerce_modules_do_not_analyze_raw_user_text() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "app/commerce_v2"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert "current_message" not in source
    assert "user_message" not in source
    assert "re.compile" not in source


def test_contact_without_explicit_handoff_creates_no_workflow() -> None:
    outcome = _run(_semantic([], products=[]), contact=True)

    assert outcome.state_after.commerce_workflows == ()
    assert outcome.commerce_planning.reason_codes == ("no_commerce_task_or_control",)


def test_repeated_handoff_readdresses_one_task_and_one_workflow() -> None:
    first = _run(
        _semantic(["handoff"]),
        turn_id="repeat-handoff-1",
    )
    first_handoff = next(
        task for task in first.state_after.tasks if task.act.value == "handoff"
    )
    first_workflow = next(
        workflow
        for workflow in first.state_after.commerce_workflows
        if workflow.workflow_kind == CommerceWorkflowKind.HANDOFF
    )

    repeated = _run(
        _semantic(["handoff"], operation="new"),
        state=first.state_after,
        turn_id="repeat-handoff-2",
    )
    handoff_tasks = [
        task for task in repeated.state_after.tasks if task.act.value == "handoff"
    ]
    handoff_workflows = [
        workflow
        for workflow in repeated.state_after.commerce_workflows
        if workflow.workflow_kind == CommerceWorkflowKind.HANDOFF
    ]

    assert len(handoff_tasks) == 1
    assert handoff_tasks[0].task_id == first_handoff.task_id
    assert handoff_tasks[0].origin_turn == first_handoff.origin_turn
    assert handoff_tasks[0].source_turn == repeated.state_after.turn_number
    assert len(handoff_workflows) == 1
    assert handoff_workflows[0].workflow_id == first_workflow.workflow_id
    assert handoff_workflows[0].task_ids == (first_handoff.task_id,)


def test_non_customer_contact_is_not_exposed_by_context_adapter() -> None:
    session = SessionState(session_id="third-party-contact")
    session.slots["manufacturer_phone"] = "+7 000 000-00-00"
    session.slots["store_email"] = "store@example.test"

    context = build_commerce_context_snapshot(session, _facts())

    assert context.contact_ref is None
    assert "+7 000 000-00-00" not in context.model_dump_json()
    assert "store@example.test" not in context.model_dump_json()


def test_selection_and_invoice_remain_separate_typed_tasks() -> None:
    outcome = _run(
        _semantic(
            ["select", "request_invoice"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 2,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "2",
                },
                {
                    "name": "company_requisites",
                    "value": "synthetic",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "synthetic",
                },
            ],
        ),
        contact=True,
    )

    assert {task.act.value for task in outcome.state_after.tasks} == {
        "select",
        "request_invoice",
    }
    assert {
        workflow.workflow_kind for workflow in outcome.state_after.commerce_workflows
    } == {CommerceWorkflowKind.REQUEST_INVOICE}


def test_price_stock_and_reservation_are_not_collapsed() -> None:
    outcome = _run(
        _semantic(
            ["check_price", "check_stock", "reserve_product"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 1,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "1",
                },
            ],
        ),
        contact=True,
    )

    assert {task.act.value for task in outcome.state_after.tasks} == {
        "check_price",
        "check_stock",
        "reserve_product",
    }
    assert len(outcome.state_after.commerce_workflows) == 1
    assert outcome.state_after.commerce_workflows[0].workflow_kind == (
        CommerceWorkflowKind.RESERVE_PRODUCT
    )


def test_two_explicit_products_keep_separate_line_items_and_quantities() -> None:
    products = [
        {
            "text": "насос",
            "canonical_type": "циркуляционный насос",
            "category": "pumps",
            "role": "target",
            "evidence": "насос",
        },
        {
            "text": "клапан",
            "canonical_type": "радиаторный клапан",
            "category": "radiator_fittings",
            "role": "target",
            "evidence": "клапан",
        },
    ]
    outcome = _run(
        _semantic(
            ["reserve_product"],
            products=products,
            constraints=[
                {
                    "name": "sku",
                    "value": "PUMP-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "PUMP-1",
                    "applies_to_product": 0,
                },
                {
                    "name": "quantity",
                    "value": 1,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "1",
                    "applies_to_product": 0,
                },
                {
                    "name": "sku",
                    "value": "VALVE-2",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "VALVE-2",
                    "applies_to_product": 1,
                },
                {
                    "name": "quantity",
                    "value": 3,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "3",
                    "applies_to_product": 1,
                },
            ],
        ),
        contact=True,
    )

    workflow = outcome.state_after.commerce_workflows[0]
    assert len(workflow.task_ids) == 2
    assert len(workflow.goal_ids) == 2
    assert [(item.product_ref, item.quantity) for item in workflow.line_items] == [
        ("PUMP-1", 1),
        ("VALVE-2", 3),
    ]
    assert workflow.product_refs == ("PUMP-1", "VALVE-2")


def test_quantity_is_never_invented_for_selected_product() -> None:
    outcome = _run(
        _semantic(
            ["reserve_product"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                }
            ],
        ),
        contact=True,
    )
    workflow = outcome.state_after.commerce_workflows[0]
    assessment = outcome.commerce_planning.readiness_assessments[0]

    assert workflow.line_items[0].quantity is None
    assert assessment.status == CommerceReadinessStatus.NEEDS_CUSTOMER_FACT
    assert assessment.recommended_next_field == "quantity"


def test_known_commerce_fields_are_not_requested_again() -> None:
    outcome = _run(
        _semantic(
            ["request_quote"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 2,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "2",
                },
            ],
        ),
        contact=True,
    )
    assessment = outcome.commerce_planning.readiness_assessments[0]

    assert {"product_selection", "quantity", "contact_ref"} <= set(
        assessment.confirmed_fields
    )
    assert assessment.recommended_next_field is None


def test_confirmation_without_pending_workflow_is_rejected() -> None:
    outcome = _run(
        _semantic([], controls=["confirm"], products=[]),
        contact=True,
    )

    assert not outcome.state_after.commerce_outbox
    assert any(
        item.reason_code == "workflow_control_has_no_target"
        for item in outcome.commerce_planning.rejected_proposals
    )


def test_handoff_consent_is_not_transferred_to_order_workflow() -> None:
    handoff = _run(_semantic(["handoff"]), contact=True, turn_id="scope-1")
    with_order = _run(
        _semantic(
            ["place_order"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 1,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "1",
                },
            ],
        ),
        state=handoff.state_after,
        contact=True,
        turn_id="scope-2",
    )
    confirmed = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=with_order.state_after,
        contact=True,
        turn_id="scope-3",
    )

    workflows = {
        item.workflow_kind: item for item in confirmed.state_after.commerce_workflows
    }
    assert workflows[CommerceWorkflowKind.HANDOFF].consent.status == (
        ConsentStatus.GRANTED
    )
    assert workflows[CommerceWorkflowKind.PLACE_ORDER].consent.status != (
        ConsentStatus.GRANTED
    )
    assert workflows[CommerceWorkflowKind.PLACE_ORDER].status == (
        CommerceWorkflowStatus.BLOCKED
    )


def test_payload_revision_gets_a_new_idempotency_key_after_new_consent() -> None:
    first = _run(
        _semantic(
            ["request_quote"],
            constraints=[
                {
                    "name": "sku",
                    "value": "SKU-1",
                    "status": "known",
                    "polarity": "required",
                    "evidence": "SKU-1",
                },
                {
                    "name": "quantity",
                    "value": 1,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "1",
                },
            ],
        ),
        contact=True,
        turn_id="key-1",
    )
    prepared_v1 = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=first.state_after,
        contact=True,
        turn_id="key-2",
    )
    corrected = _run(
        _semantic(
            ["request_quote"],
            operation="correct",
            constraints=[
                {
                    "name": "quantity",
                    "value": 2,
                    "status": "known",
                    "polarity": "required",
                    "evidence": "2",
                }
            ],
        ),
        state=prepared_v1.state_after,
        contact=True,
        turn_id="key-3",
        outbox=False,
    )
    prepared_v2 = _run(
        _semantic([], controls=["confirm"], products=[]),
        state=corrected.state_after,
        contact=True,
        turn_id="key-4",
    )

    entries = prepared_v2.state_after.commerce_outbox
    assert len(entries) == 2
    assert [item.command.payload_revision for item in entries] == [1, 2]
    assert entries[0].command.idempotency_key != entries[1].command.idempotency_key


def test_current_solution_plan_is_linked_without_becoming_a_fake_sku() -> None:
    outcome = _run(_semantic(["request_quote"]), contact=True, outbox=False)
    workflow = outcome.commerce_planning.workflows[0]
    task_id = workflow.task_ids[0]
    catalog = CatalogPlanningResult(
        status="planned",
        solution_plan=SolutionPlan(
            solution_id="solution-1",
            task_ids=(task_id,),
            components=(
                SolutionComponent(
                    component_id="component-1",
                    task_id=task_id,
                    product_kind=ProductKind.CIRCULATION_PUMP,
                    role=CatalogProductRole.COMPONENT,
                ),
            ),
        ),
    )

    replanned = plan_commerce_workflow(
        outcome.state_after,
        outcome.next_action_plan,
        outcome.commerce_planning.workflow_resolutions,
        outcome.commerce_planning.workflows,
        outcome.commerce_planning.readiness_assessments,
        catalog,
        build_capability_snapshot(_facts()),
        CommerceWorkflowRegistry(),
        outbox_enabled=False,
    )

    assert replanned.workflows[0].solution_id == "solution-1"
    assert replanned.workflows[0].product_refs == ()
    assert replanned.workflows[0].line_items == ()


def test_state4_fields_are_written_only_at_the_reducer_boundary() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "app"
    forbidden = (
        '"commerce_workflows":',
        '"commerce_controls":',
        '"commerce_outbox":',
        '"commerce_planning":',
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "reducer.py":
            continue
        source = path.read_text(encoding="utf-8")
        if any(marker in source for marker in forbidden):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []
