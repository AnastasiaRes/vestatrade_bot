from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter, TurnUnderstanding
from app.models import SessionState


class CommerceSemanticClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.settings = SimpleNamespace(
            llm_enabled=True,
            llm_model="test/commerce-semantic",
        )
        self.last_fallback_reason = None
        self.calls = 0

    def request_budget(self):
        return nullcontext()

    def complete_json(self, agent, messages, fallback, model=None):
        self.calls += 1
        return self.payload, True


def _payload() -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "continue",
        "acts": ["request_invoice", "check_delivery"],
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [{"kind": "confirm", "evidence": "подтверждаю передачу"}],
        "answers_pending_question": True,
        "confidence": 0.98,
    }


def test_semantic_contract_supports_typed_commerce_acts_and_control() -> None:
    client = CommerceSemanticClient(_payload())
    result = SemanticInterpreter(client).interpret(
        "Счёт нужен, доставку тоже проверьте; подтверждаю передачу.",
        SessionState(session_id="commerce-semantic"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert {item.value for item in result.understanding.acts} == {
        "request_invoice",
        "check_delivery",
    }
    assert result.understanding.workflow_controls[0].kind.value == "confirm"
    # One SemanticInterpreter invocation retains its existing first/audit
    # passes; Stage 4 must not invoke the interpreter a second time.
    assert client.calls == 2


def test_semantic_contract_rejects_workflow_control_without_current_evidence() -> None:
    client = CommerceSemanticClient(_payload())
    result = SemanticInterpreter(client).interpret(
        "Счёт нужен, доставку тоже проверьте.",
        SessionState(session_id="commerce-evidence"),
    )

    assert result.status == "rejected"
    assert "absent from current_message" in (result.rejection_reason or "")


def test_old_semantic_schema_remains_compatible_with_empty_controls() -> None:
    payload = _payload()
    payload["schema_version"] = "1.0"
    payload.pop("workflow_controls")

    understanding = TurnUnderstanding.model_validate(payload)

    assert understanding.schema_version == "1.0"
    assert understanding.workflow_controls == []


@pytest.mark.parametrize(
    ("mutate", "expected_act", "expected_control", "repair_code"),
    [
        (
            {"operation": "confirm", "acts": [], "workflow_controls": []},
            None,
            "confirm",
            "operation_control_moved_to_workflow_controls",
        ),
        (
            {
                "operation": "continue",
                "acts": ["withdraw_consent"],
                "workflow_controls": [],
            },
            None,
            "withdraw_consent",
            "act_control_moved_to_workflow_controls",
        ),
        (
            {
                "operation": "new",
                "acts": [],
                "workflow_controls": [{"kind": "handoff", "evidence": "передайте"}],
            },
            "handoff",
            None,
            "workflow_control_act_moved_to_acts",
        ),
        (
            {"operation": "modify_order", "acts": [], "workflow_controls": []},
            "modify_order",
            None,
            "operation_act_moved_to_acts",
        ),
    ],
)
def test_semantic_interpreter_repairs_only_misplaced_known_enums(
    mutate,
    expected_act,
    expected_control,
    repair_code,
) -> None:
    payload = _payload()
    payload.update(mutate)
    client = CommerceSemanticClient(payload)
    result = SemanticInterpreter(client).interpret(
        "Подтверждаю, передайте и измените заказ.",
        SessionState(session_id=f"repair-{repair_code}"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert repair_code in result.structural_repairs
    assert expected_act is None or expected_act in {
        item.value for item in result.understanding.acts
    }
    assert expected_control is None or expected_control in {
        item.kind.value for item in result.understanding.workflow_controls
    }


def test_structural_repair_does_not_infer_control_from_message_text() -> None:
    payload = _payload()
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "workflow_controls": [],
        }
    )
    result = SemanticInterpreter(CommerceSemanticClient(payload)).interpret(
        "Подтверждаю.",
        SessionState(session_id="repair-does-not-classify"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.workflow_controls == []
    assert result.structural_repairs == ()
