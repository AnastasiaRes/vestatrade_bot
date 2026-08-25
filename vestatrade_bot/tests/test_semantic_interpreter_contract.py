from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from app.agents.semantic_interpreter import (
    SemanticInterpreter,
    TurnUnderstanding,
)
from app.models import SessionState


class SemanticJSONClient:
    def __init__(self, payload: dict[str, Any], *, used: bool = True) -> None:
        self.payload = payload
        self.used = used
        self.settings = SimpleNamespace(
            llm_enabled=True,
            llm_model="test/semantic-model",
        )
        self.last_fallback_reason = None
        self.messages: list[dict[str, str]] = []

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        assert agent in {
            "SemanticInterpreter.shadow",
            "SemanticInterpreter.shadow.audit",
        }
        self.messages = messages
        return (self.payload if self.used else fallback), self.used

    def request_budget(self):
        return nullcontext()


def valid_understanding() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "language": "ru",
        "operation": "new",
        "acts": ["select", "check_price"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "циркуляционный насос",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            },
            {
                "text": "радиаторы",
                "canonical_type": "радиатор",
                "category": "radiators",
                "role": "existing",
                "evidence": "радиаторы",
            },
        ],
        "constraints": [
            {
                "name": "mounting_length",
                "value": None,
                "unit": None,
                "status": "unknown",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "длину не знаю",
            }
        ],
        "references": [],
        "ambiguities": [],
        "answers_pending_question": False,
        "confidence": 0.92,
    }


def test_contract_accepts_multi_act_target_context_and_unknown_parameter() -> None:
    message = (
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю."
    )
    client = SemanticJSONClient(valid_understanding())

    result = SemanticInterpreter(client).interpret(message, SessionState(session_id="s"))

    assert result.status == "accepted"
    assert result.output_accepted is True
    assert result.understanding is not None
    assert [item.role.value for item in result.understanding.products] == [
        "target",
        "existing",
    ]
    assert {act.value for act in result.understanding.acts} == {
        "select",
        "check_price",
    }
    assert result.understanding.constraints[0].status.value == "unknown"
    assert result.understanding.constraints[0].value is None


def test_contract_rejects_evidence_copied_from_history_or_invented() -> None:
    payload = valid_understanding()
    payload["constraints"][0]["evidence"] = "монтажная длина 180 мм"
    state = SessionState(
        session_id="s",
        history=[
            {"role": "user", "content": "раньше обсуждали длину 180 мм"},
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Подберите такой насос, длину не знаю.",
        state,
    )

    assert result.status == "rejected"
    assert result.output_accepted is False
    assert "absent from current_message" in (result.rejection_reason or "")


def test_contract_forbids_reply_calculation_and_catalogue_choice_fields() -> None:
    payload = valid_understanding()
    payload["reply"] = "Берите SKU-123, я рассчитал 6 метров напора"

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю.",
        SessionState(session_id="s"),
    )

    assert result.status == "rejected"
    assert "Extra inputs are not permitted" in (result.rejection_reason or "")
    assert "reply" not in TurnUnderstanding.model_fields


def test_contract_rejects_invalid_product_reference_index() -> None:
    payload = valid_understanding()
    payload["constraints"][0]["applies_to_product"] = 7

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю.",
        SessionState(session_id="s"),
    )

    assert result.status == "rejected"
    assert "missing product mention" in (result.rejection_reason or "")


def test_semantic_prompt_receives_only_bounded_redacted_context() -> None:
    client = SemanticJSONClient(valid_understanding())
    state = SessionState(
        session_id="s",
        contact="buyer@example.test",
        history=[
            {"role": "user", "content": "мой телефон +7 999 123-45-67"},
            {"role": "assistant", "content": "Принято"},
        ],
    )

    SemanticInterpreter(client).interpret(
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю.",
        state,
    )

    serialized_prompt = client.messages[-1]["content"]
    assert "+7 999 123-45-67" not in serialized_prompt
    assert "buyer@example.test" not in serialized_prompt
    assert "output_schema" in serialized_prompt
