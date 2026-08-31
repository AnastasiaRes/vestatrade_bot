from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from app.agents.semantic_interpreter import (
    SEMANTIC_AUDIT_PROMPT,
    SEMANTIC_INTERPRETER_PROMPT,
    SemanticInterpreter,
    TurnUnderstanding,
    semantic_context,
)
from app.agents.domain_ontology import RANGE_CAPABLE_CONSTRAINT_FACTS
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    ConstraintFactV2,
    CustomerTask,
    DialogueStateV2,
    PresentedCandidateSummary,
    ProductGoal,
    ShadowDeliveryStatus,
    TaskStack,
)
from app.models import Product, ProductCard, SessionState


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


def information_request_payload(
    *,
    acts: list[str],
    products: list[dict[str, Any]],
    information_requests: list[dict[str, Any]],
    operation: str = "continue",
) -> dict[str, Any]:
    return {
        "schema_version": "1.2",
        "language": "ru",
        "operation": operation,
        "acts": acts,
        "products": products,
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": information_requests,
        "answers_pending_question": False,
        "confidence": 0.94,
    }


def typed_product(
    *,
    text: str,
    canonical_type: str,
    category: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "text": text,
        "canonical_type": canonical_type,
        "category": category,
        "role": "target",
        "evidence": evidence,
    }


def test_stock_condition_is_an_explicit_multi_act_semantic_invariant() -> None:
    assert "разовый вопрос" in SEMANTIC_INTERPRETER_PROMPT
    assert "stock_availability=true" in SEMANTIC_INTERPRETER_PROMPT
    assert "Разовый вопрос о наличии" in SEMANTIC_AUDIT_PROMPT
    assert "stock_availability=true" in SEMANTIC_AUDIT_PROMPT


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
    assert result.understanding.information_requests == []


def test_catalog_bound_numeric_sku_anchor_survives_an_untyped_semantic_candidate() -> None:
    message = "Проверьте цену и наличие товара 11677"
    payload = valid_understanding()
    payload.update(
        {
            "acts": ["check_price", "check_stock"],
            "products": [
                {
                    "text": "11677",
                    "canonical_type": "",
                    "category": "other",
                    "role": "target",
                    "evidence": "11677",
                }
            ],
            "constraints": [],
            "information_requests": [],
        }
    )
    product = Product(
        sku="11677",
        name="Тестовый товар",
        category_path="Арматура",
        price=100,
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
    )

    result = SemanticInterpreter(
        SemanticJSONClient(payload),
        catalog_products=[product],
    ).interpret(message, SessionState(session_id="catalog-bound-five-digit-sku"))

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].canonical_type == "catalog_product"
    assert [(item.name, item.value) for item in result.understanding.constraints] == [
        ("sku", "11677")
    ]
    assert "catalog_bound_sku_product_scope_recovered" in result.structural_repairs


def test_information_request_captures_decision_relevance_of_mounting_length() -> None:
    payload = information_request_payload(
        acts=["explain"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            )
        ],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "decision_relevance",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 0,
                "evidence": "Почему монтажная длина важна",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Почему монтажная длина важна для этого насоса?",
        SessionState(session_id="information-decision-relevance"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    request = result.understanding.information_requests[0]
    assert request.fact_name == "mounting_length_mm"
    assert request.purpose.value == "decision_relevance"
    assert [item.value for item in request.requested_outputs] == ["explanation"]
    assert request.act.value == "explain"
    assert request.applies_to_product == 0


def test_information_request_captures_determination_instruction() -> None:
    payload = information_request_payload(
        acts=["explain"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            )
        ],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "determination_method",
                "requested_outputs": ["instruction"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 0,
                "evidence": "Как определить монтажную длину насоса",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Как определить монтажную длину насоса?",
        SessionState(session_id="information-determination-method"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    request = result.understanding.information_requests[0]
    assert request.purpose.value == "determination_method"
    assert [item.value for item in request.requested_outputs] == ["instruction"]
    assert request.source_kind is None


def test_information_request_captures_technical_passport_link() -> None:
    payload = information_request_payload(
        acts=["get_link"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            )
        ],
        information_requests=[
            {
                "fact_name": None,
                "purpose": "provenance",
                "requested_outputs": ["verified_link"],
                "output_relation": "all",
                "source_kind": "technical_documentation",
                "act": "get_link",
                "applies_to_product": 0,
                "evidence": "ссылку на технический паспорт этого насоса",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Дайте ссылку на технический паспорт этого насоса.",
        SessionState(session_id="information-technical-passport"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    request = result.understanding.information_requests[0]
    assert request.purpose.value == "provenance"
    assert request.requested_outputs[0].value == "verified_link"
    assert request.source_kind is not None
    assert request.source_kind.value == "technical_documentation"
    assert request.act.value == "get_link"


def test_information_request_preserves_link_or_instruction_alternative() -> None:
    payload = information_request_payload(
        acts=["get_link", "explain"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            )
        ],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "determination_method",
                "requested_outputs": ["verified_link", "instruction"],
                "output_relation": "any",
                "source_kind": "any_verified",
                "act": "get_link",
                "applies_to_product": 0,
                "evidence": "проверенную ссылку или инструкцию",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Дайте проверенную ссылку или инструкцию, как узнать длину насоса.",
        SessionState(session_id="information-link-or-instruction"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    request = result.understanding.information_requests[0]
    assert {item.value for item in request.requested_outputs} == {
        "verified_link",
        "instruction",
    }
    assert request.output_relation.value == "any"
    assert request.source_kind is not None
    assert request.source_kind.value == "any_verified"


def test_information_requests_keep_independent_two_product_scope() -> None:
    payload = information_request_payload(
        acts=["explain", "get_link"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            ),
            typed_product(
                text="котёл",
                canonical_type="boiler",
                category="boilers",
                evidence="котла",
            ),
        ],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "meaning",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 0,
                "evidence": "Для насоса объясните монтажную длину",
            },
            {
                "fact_name": None,
                "purpose": "provenance",
                "requested_outputs": ["verified_link"],
                "output_relation": "all",
                "source_kind": "manufacturer_documentation",
                "act": "get_link",
                "applies_to_product": 1,
                "evidence": "для котла дайте ссылку на техпаспорт",
            },
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Для насоса объясните монтажную длину, а для котла дайте ссылку на техпаспорт.",
        SessionState(session_id="information-two-products"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [
        (item.act.value, item.applies_to_product)
        for item in result.understanding.information_requests
    ] == [("explain", 0), ("get_link", 1)]


def test_information_request_missing_turn_act_is_safely_dropped() -> None:
    payload = information_request_payload(
        acts=["gratitude"],
        products=[],
        information_requests=[
            {
                "fact_name": None,
                "purpose": "provenance",
                "requested_outputs": ["verified_link"],
                "output_relation": "all",
                "source_kind": "any_verified",
                "act": "get_link",
                "applies_to_product": None,
                "evidence": "где подтверждающий источник",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Спасибо, а где подтверждающий источник?",
        SessionState(session_id="information-missing-act"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.information_requests == []
    assert "information_request_missing_turn_act_dropped" in (
        result.structural_repairs
    )


def test_invalid_information_request_contracts_do_not_poison_valid_turn() -> None:
    payload = information_request_payload(
        acts=["explain", "get_link"],
        products=[
            typed_product(
                text="насос",
                canonical_type="circulation_pump",
                category="pumps",
                evidence="насоса",
            )
        ],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "meaning",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 0,
                "evidence": "что означает монтажная длина",
            },
            {
                "fact_name": None,
                "purpose": "provenance",
                "requested_outputs": ["verified_link"],
                "output_relation": "all",
                "source_kind": None,
                "act": "get_link",
                "applies_to_product": 0,
                "evidence": "ссылку на паспорт",
            },
            {
                "fact_name": None,
                "purpose": "provenance",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 0,
                "evidence": "подтверждение характеристик",
            },
            {
                "fact_name": "mounting_length_mm",
                "purpose": "meaning",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": 9,
                "evidence": "монтажная длина насоса",
            },
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Что означает монтажная длина насоса; дайте ссылку на паспорт и "
        "подтверждение характеристик.",
        SessionState(session_id="information-invalid-contracts"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.information_requests) == 1
    assert result.understanding.information_requests[0].purpose.value == "meaning"
    assert "information_request_verified_link_without_source_dropped" in (
        result.structural_repairs
    )
    assert "information_request_provenance_without_verified_link_dropped" in (
        result.structural_repairs
    )
    assert "information_request_invalid_product_binding_dropped" in (
        result.structural_repairs
    )


def test_information_request_evidence_is_rebound_to_exact_current_turn_text() -> None:
    payload = information_request_payload(
        acts=["explain"],
        products=[],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "determination_method",
                "requested_outputs": ["instruction"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": None,
                "evidence": "как определить длину",
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Kak opredelit dlinu?",
        SessionState(session_id="information-exact-translit-evidence"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.information_requests[0].evidence == "opredelit dlinu"
    assert "information_request_evidence_rebound_to_current_message" in (
        result.structural_repairs
    )


def test_stale_constraint_is_dropped_without_poisoning_grounded_turn() -> None:
    payload = valid_understanding()
    payload["constraints"][0]["evidence"] = "монтажная длина 180 мм"
    state = SessionState(
        session_id="s",
        history=[
            {"role": "user", "content": "раньше обсуждали длину 180 мм"},
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Подберите циркуляционный насос для радиаторов.",
        state,
    )

    assert result.status == "accepted"
    assert result.output_accepted is True
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "constraint_ungrounded_evidence_dropped" in result.structural_repairs


def test_closed_categorical_fact_rejects_value_invented_from_related_object() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            },
            {
                "text": "радиаторы",
                "canonical_type": "radiator",
                "category": "radiators",
                "role": "existing",
                "evidence": "пять радиаторов",
            },
        ],
        "constraints": [
            {
                "name": "coolant_type",
                "value": "water",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "пять радиаторов",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен циркуляционный насос, в системе пять радиаторов.",
        SessionState(session_id="closed-value-radiators-not-water"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert [item.kind for item in result.understanding.ambiguities] == [
        "constraint_closed_value_not_grounded"
    ]
    assert "constraint_closed_value_not_grounded_dropped" in (
        result.structural_repairs
    )
    assert "constraint_categorical_ambiguity_added" in result.structural_repairs


def test_closed_categorical_fact_accepts_explicit_water_alias() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        "constraints": [
            {
                "name": "coolant_type",
                "value": "water",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "на воде",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен циркуляционный насос, система работает на воде.",
        SessionState(session_id="closed-value-explicit-water"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [(item.name, item.value) for item in result.understanding.constraints] == [
        ("coolant_type", "water")
    ]
    assert result.understanding.ambiguities == []


def test_closed_categorical_aliases_preserve_translit_boiler_and_circuits() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru-Latn",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "elektricheskiy dvuhkonturnyy kotel",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "elektricheskiy dvuhkonturnyy kotel",
            }
        ],
        "constraints": [
            {
                "name": "boiler_type",
                "value": "electric",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "elektricheskiy",
            },
            {
                "name": "circuits",
                "value": 2,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "dvuhkonturnyy",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Nuzhen elektricheskiy dvuhkonturnyy kotel.",
        SessionState(session_id="closed-value-translit-boiler"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [(item.name, item.value) for item in result.understanding.constraints] == [
        ("boiler_type", "electric"),
        ("circuits", 2),
    ]
    assert result.understanding.ambiguities == []


def test_closed_filter_and_boolean_aliases_remain_grounded() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "промывной сетчатый фильтр",
                "canonical_type": "water_filter",
                "category": "filters",
                "role": "target",
                "evidence": "промывной сетчатый фильтр",
            }
        ],
        "constraints": [
            {
                "name": "filter_method",
                "value": "mechanical",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "сетчатый",
            },
            {
                "name": "washable",
                "value": True,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "промывной",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите промывной сетчатый фильтр.",
        SessionState(session_id="closed-value-filter-boolean"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [(item.name, item.value) for item in result.understanding.constraints] == [
        ("filter_method", "mechanical"),
        ("washable", True),
    ]
    assert result.understanding.ambiguities == []


def test_closed_glycol_and_gas_aliases_remain_grounded() -> None:
    cases = (
        (
            "Нужен циркуляционный насос на пропиленгликоле.",
            "circulation_pump",
            "pumps",
            "циркуляционный насос",
            "coolant_type",
            "propylene_glycol",
            "пропиленгликоле",
        ),
        (
            "Нужен газовый котёл.",
            "boiler",
            "boilers",
            "газовый котёл",
            "boiler_type",
            "gas",
            "газовый",
        ),
    )
    for (
        message,
        canonical_type,
        category,
        product_evidence,
        fact_name,
        value,
        evidence,
    ) in cases:
        payload = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": product_evidence,
                    "canonical_type": canonical_type,
                    "category": category,
                    "role": "target",
                    "evidence": product_evidence,
                }
            ],
            "constraints": [
                {
                    "name": fact_name,
                    "value": value,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": evidence,
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            message,
            SessionState(session_id=f"closed-value-{fact_name}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert [(item.name, item.value) for item in result.understanding.constraints] == [
            (fact_name, value)
        ]
        assert result.understanding.ambiguities == []


def test_single_circuit_accepts_explicit_heating_only_variants() -> None:
    for evidence in (
        "только отопление",
        "только для отопления",
        "только для отопления, без ГВС",
    ):
        payload = {
            "schema_version": "1.2",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "котёл",
                    "canonical_type": "boiler",
                    "category": "boilers",
                    "role": "target",
                    "evidence": "котёл",
                }
            ],
            "constraints": [
                {
                    "name": "circuits",
                    "value": 1,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": evidence,
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.94,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            f"Нужен котёл {evidence}.",
            SessionState(session_id=f"single-circuit-{evidence}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert [(item.name, item.value) for item in result.understanding.constraints] == [
            ("circuits", 1)
        ]
        assert result.understanding.ambiguities == []


def test_heating_context_alone_does_not_ground_single_circuit() -> None:
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "circuits",
                "value": 1,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "отопления",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл для отопления дома.",
        SessionState(session_id="heating-context-not-single-circuit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert result.understanding.ambiguities[0].kind == (
        "constraint_closed_value_not_grounded"
    )


def test_standalone_dhw_does_not_ground_one_or_two_circuits() -> None:
    for proposed_value in (1, 2):
        payload = {
            "schema_version": "1.2",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "котёл",
                    "canonical_type": "boiler",
                    "category": "boilers",
                    "role": "target",
                    "evidence": "котёл",
                }
            ],
            "constraints": [
                {
                    "name": "circuits",
                    "value": proposed_value,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "ГВС",
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.91,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            "Нужен котёл, рядом есть ГВС.",
            SessionState(session_id=f"standalone-dhw-not-circuits-{proposed_value}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert result.understanding.constraints == []
        assert result.understanding.ambiguities[0].kind == (
            "constraint_closed_value_not_grounded"
        )


def test_water_heater_context_alone_does_not_ground_single_circuit() -> None:
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            },
            {
                "text": "водонагреватель",
                "canonical_type": "water_heater",
                "category": "water_heaters",
                "role": "context",
                "evidence": "водонагревателем",
            },
        ],
        "constraints": [
            {
                "name": "circuits",
                "value": 1,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "водонагревателем",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Подберите котёл рядом с водонагревателем.",
        SessionState(session_id="water-heater-context-not-single-circuit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert result.understanding.ambiguities[0].kind == (
        "constraint_closed_value_not_grounded"
    )


def test_closed_boolean_alias_prefers_explicit_negative_form() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "непромывной фильтр",
                "canonical_type": "water_filter",
                "category": "filters",
                "role": "target",
                "evidence": "непромывной фильтр",
            }
        ],
        "constraints": [
            {
                "name": "washable",
                "value": False,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "непромывной",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите непромывной фильтр.",
        SessionState(session_id="closed-value-negative-boolean"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [(item.name, item.value) for item in result.understanding.constraints] == [
        ("washable", False)
    ]
    assert result.understanding.ambiguities == []


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


def test_interpreter_removes_invalid_product_binding_without_guessing() -> None:
    payload = valid_understanding()
    payload["constraints"][0]["applies_to_product"] = 7

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю.",
        SessionState(session_id="s"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints[0].applies_to_product is None
    assert "constraint_invalid_product_binding_removed" in result.structural_repairs


def test_evidence_repair_accepts_russian_inflection_and_keeps_exact_source() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите ближайший аналог котла с такими же характеристиками.",
        SessionState(session_id="inflection"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].evidence == "котла"
    assert "product_evidence_rebound_to_current_message" in result.structural_repairs


def test_evidence_repair_rebinds_cyrillic_evidence_to_exact_translit_source() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru-Latn",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "настенный газовый котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "настенный газовый котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": 24,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "24 кВт",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Nuzhen nastennyy gazovyy kotel 24 kVt dlya doma.",
        SessionState(session_id="translit-source"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert (
        result.understanding.products[0].evidence
        == "nastennyy gazovyy kotel"
    )
    assert result.understanding.constraints[0].evidence == "24 kVt"
    assert "product_evidence_rebound_to_current_message" in result.structural_repairs
    assert "constraint_evidence_rebound_to_current_message" in (
        result.structural_repairs
    )


def test_numeric_modifier_inside_product_mention_is_recovered_from_typed_unit() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "настенный газовый котёл 35 кВт",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "настенный газовый котёл 35 кВт",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.92,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен настенный газовый котёл 35 кВт.",
        SessionState(session_id="modifier-coverage"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    # Fuel is independently grounded and intentionally retained.  The
    # invariant under test is that the numeric modifier becomes exactly one
    # power fact, rather than that it is the only fact in the turn.
    assert {
        (item.name, item.value) for item in result.understanding.constraints
    } >= {("boiler_type", "gas")}
    power_constraints = [
        item for item in result.understanding.constraints if item.name == "power_kw"
    ]
    assert len(power_constraints) == 1
    constraint = power_constraints[0]
    assert constraint.name == "power_kw"
    assert constraint.value == 35
    assert constraint.unit == "кВт"
    assert constraint.evidence == "35 кВт"
    assert constraint.applies_to_product == 0
    assert "constraint_typed_numeric_anchor_recovered" in (
        result.structural_repairs
    )


def test_evidence_repair_tolerates_inflection_and_one_omitted_context_word() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["select"],
        "products": [],
        "constraints": [
            {
                "name": "fluid_composition",
                "value": "30% пропиленгликоль",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": (
                    "система с теплоносителем на 30% пропиленгликоль"
                ),
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.92,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Насос будет работать в системе с 30% пропиленгликолем, расход известен.",
        SessionState(session_id="inflected-context"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert (
        result.understanding.constraints[0].evidence
        == "системе с 30% пропиленгликолем"
    )
    assert "constraint_evidence_rebound_to_current_message" in (
        result.structural_repairs
    )


def test_evidence_repair_may_bridge_omitted_words_but_returns_source_span() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "en",
        "operation": "new",
        "acts": ["check_delivery"],
        "products": [],
        "constraints": [],
        "references": [
            {
                "kind": "other",
                "text": "shipping destination",
                "target_hint": "Kazakhstan",
                "evidence": "ship to Kazakhstan",
            }
        ],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.88,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Can you ship underfloor heating pipes to Kazakhstan?",
        SessionState(session_id="source-span"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert (
        result.understanding.references[0].evidence
        == "ship underfloor heating pipes to Kazakhstan"
    )


def test_changed_number_drops_constraint_instead_of_guessing_or_rejecting_turn() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": [],
        "products": [],
        "constraints": [
            {
                "name": "length",
                "value": 50,
                "unit": "м",
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "50 метров",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": True,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужно ровно 40 метров.",
        SessionState(session_id="number-mismatch"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "constraint_ungrounded_evidence_dropped" in result.structural_repairs


def test_overlong_reference_text_is_replaced_only_with_grounded_evidence() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "continue",
        "acts": ["calculate"],
        "products": [],
        "constraints": [],
        "references": [
            {
                "kind": "pending_question",
                "text": "Предыдущий ответ консультанта. " * 20,
                "target_hint": None,
                "evidence": "по площади",
            }
        ],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": True,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Это нужно рассчитать по площади?",
        SessionState(session_id="long-reference"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.references[0].text == "по площади"
    assert "reference_text_replaced_with_grounded_evidence" in (
        result.structural_repairs
    )


def test_invalid_polarity_drops_only_unsafe_constraint_proposal() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["find"],
        "products": [],
        "constraints": [
            {
                "name": "installation_state",
                "value": "не уложен",
                "unit": None,
                "status": "known",
                "polarity": "context",
                "applies_to_product": None,
                "evidence": "утеплитель не уложен",
            },
            {
                "name": "application",
                "value": "тёплый пол",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "для тёплого пола",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Утеплитель не уложен; покажите варианты для тёплого пола.",
        SessionState(session_id="invalid-polarity"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [fact.name for fact in result.understanding.constraints] == ["application"]
    assert "constraint_invalid_polarity_dropped" in result.structural_repairs


def test_invalid_stock_status_is_repaired_only_with_durable_requirement_evidence() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "availability",
                "value": True,
                "unit": None,
                "status": "available",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "из наличия",
            },
            {
                "name": "power_kw",
                "value": 24,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "24 кВт",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл 24 кВт из наличия.",
        SessionState(session_id="invalid-status"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [fact.name for fact in result.understanding.constraints] == [
        "stock_availability",
        "power_kw",
    ]
    stock = result.understanding.constraints[0]
    assert stock.status.value == "known"
    assert stock.value is True
    assert stock.polarity.value == "required"
    assert "availability_requirement_status_repaired" in result.structural_repairs


def test_typed_availability_constraint_remains_persistent_selection_fact() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "availability",
                "value": True,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "из наличия",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл из наличия.",
        SessionState(session_id="typed-availability"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["select"]
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].name == "stock_availability"
    assert result.understanding.constraints[0].value is True
    assert result.understanding.constraints[0].polarity.value == "required"
    assert "typed_availability_requirement_added_check_stock" not in (
        result.structural_repairs
    )


def test_stock_action_without_availability_evidence_is_dropped() -> None:
    message = "Проверьте, подойдёт ли насосу монтажная длина 250 мм."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "continue",
        "acts": ["check_stock"],
        "products": [],
        "constraints": [
            {
                "name": "mounting_length_mm",
                "value": 250,
                "unit": "мм",
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "монтажная длина 250 мм",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert "check_stock" not in {
        item.value for item in result.understanding.acts
    }
    assert "capability_action_without_turn_evidence_dropped" in (
        result.structural_repairs
    )


def test_numeric_physical_fact_cannot_be_copied_without_current_number() -> None:
    message = "Для газового котла подскажите ближайший по мощности вариант."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "refine",
        "acts": ["find"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газового котла",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": 15,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": message,
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="no-stale-number"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.name for item in result.understanding.constraints] == [
        "boiler_type"
    ]
    assert all(item.name != "power_kw" for item in result.understanding.constraints)
    assert "constraint_numeric_value_not_in_evidence_dropped" in (
        result.structural_repairs
    )


def test_stock_availability_schema_aliases_remain_selection_requirement() -> None:
    for fact_name in (
        "stock_availability",
        "availability_status",
        "stock_available",
        "is_in_stock",
        "inventory_availability",
    ):
        payload = {
            "schema_version": "1.2",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "котёл",
                    "canonical_type": "boiler",
                    "category": "boilers",
                    "role": "target",
                    "evidence": "котёл",
                }
            ],
            "constraints": [
                {
                    "name": fact_name,
                    "value": True,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "из наличия",
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.93,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            "Подберите котёл из наличия.",
            SessionState(session_id=f"typed-availability-{fact_name}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert [item.value for item in result.understanding.acts] == ["select"]
        assert len(result.understanding.constraints) == 1
        fact = result.understanding.constraints[0]
        assert fact.name == "stock_availability"
        assert fact.value is True
        assert fact.polarity.value == "required"
        assert fact.applies_to_product == 0
        assert "availability_requirement_retained_as_typed_fact" in (
            result.structural_repairs
        )


def test_non_positive_availability_capability_explicitly_removes_requirement() -> None:
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "stock_availability",
                "value": False,
                "unit": None,
                "status": "known",
                "polarity": "preferred",
                "applies_to_product": 0,
                "evidence": "наличие неважно",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл, наличие неважно.",
        SessionState(session_id="typed-availability-non-positive"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["select"]
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "stock_availability"
    assert fact.value is True
    assert fact.polarity.value == "excluded"
    assert fact.applies_to_product == 0
    assert "availability_relaxation_canonicalized_to_excluded" in result.structural_repairs


def test_stock_question_remains_action_without_persistent_requirement() -> None:
    message = "Есть ли этот котёл в наличии?"
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "continue",
        "acts": ["check_stock"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="stock-question-not-filter"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["check_stock"]
    assert result.understanding.constraints == []


def test_stock_question_drops_erroneous_persistent_constraint() -> None:
    message = "Есть ли этот котёл в наличии?"
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "continue",
        "acts": ["check_stock"],
        "products": [],
        "constraints": [
            {
                "name": "stock_availability",
                "value": True,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "в наличии",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_boiler_state(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["check_stock"]
    assert result.understanding.constraints == []
    assert "availability_constraint_without_durable_requirement_dropped" in (
        result.structural_repairs
    )


def test_rejected_unavailable_candidate_repairs_stock_requirement_polarity() -> None:
    message = "Нет в наличии — значит, не подойдёт. Дайте другой вариант."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "refine",
        "acts": ["select", "check_stock"],
        "products": [],
        "constraints": [
            {
                "name": "availability_status",
                "value": False,
                "unit": None,
                "status": "known",
                "polarity": "excluded",
                "applies_to_product": None,
                "evidence": "Нет в наличии",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_boiler_state(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "stock_availability"
    assert fact.status.value == "known"
    assert fact.value is True
    assert fact.polarity.value == "required"
    assert "availability_requirement_polarity_repaired" in (
        result.structural_repairs
    )


def test_generic_existential_product_question_cannot_become_stock_semantics() -> None:
    message = "Есть ли хоть один подходящий товар?"
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "continue",
        "acts": ["find", "check_stock"],
        "products": [],
        "constraints": [
            {
                "name": "stock_availability",
                "value": True,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": message,
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["find"]
    assert result.understanding.constraints == []
    assert "capability_action_without_turn_evidence_dropped" in (
        result.structural_repairs
    )
    assert "capability_constraint_without_turn_evidence_dropped" in (
        result.structural_repairs
    )


def test_explicit_permission_for_unconfirmed_stock_becomes_excluded_requirement() -> None:
    message = "Можно включить и отсутствующие товары."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "refine",
        "acts": ["find", "check_stock"],
        "products": [],
        "constraints": [
            {
                "name": "availability_status",
                "value": False,
                "unit": None,
                "status": "known",
                "polarity": "preferred",
                "applies_to_product": None,
                "evidence": "отсутствующие",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.93,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["find"]
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "stock_availability"
    assert fact.value is True
    assert fact.polarity.value == "excluded"
    assert fact.evidence == "отсутствующие"


def test_untyped_context_product_is_dropped_without_losing_typed_target() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            },
            {
                "text": "система отопления",
                "canonical_type": None,
                "category": "other",
                "role": "context",
                "evidence": "система отопления",
            },
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Подберите циркуляционный насос для системы отопления.",
        SessionState(session_id="untyped-context"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [product.canonical_type for product in result.understanding.products] == [
        "circulation_pump"
    ]
    assert "product_missing_canonical_type_dropped" in result.structural_repairs


def test_untyped_target_cannot_redirect_product_act_to_typed_context() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "correct",
        "acts": ["select"],
        "products": [
            {
                "text": "нужная штука",
                "canonical_type": None,
                "category": "other",
                "role": "target",
                "evidence": "нужная штука",
            },
            {
                "text": "радиаторы",
                "canonical_type": "radiator",
                "category": "radiators",
                "role": "context",
                "evidence": "радиаторов",
            },
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.7,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нет, нужна штука для радиаторов.",
        SessionState(session_id="untyped-target"),
    )

    assert result.status == "rejected"
    assert "target product is missing canonical_type" in (
        result.rejection_reason or ""
    )


def test_contentful_request_cannot_be_accepted_as_empty_continue_frame() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru-Latn",
        "operation": "continue",
        "acts": [],
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.8,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Nuzhen gazovyy kotel dlya doma.",
        SessionState(session_id="empty-contentful"),
    )

    assert result.status == "rejected"
    assert "contentful current_message reduced to empty semantic frame" in (
        result.rejection_reason or ""
    )


def test_short_acknowledgement_may_remain_empty_continue_frame() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "continue",
        "acts": [],
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Да, понятно.",
        SessionState(session_id="short-ack"),
    )

    assert result.status == "accepted"

    translit_result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Ok, spasibo.",
        SessionState(session_id="short-ack-translit"),
    )
    assert translit_result.status == "accepted"


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


def test_mixed_cyrillic_latin_evidence_rebinds_to_exact_translit_source() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru-Latn",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовyi kotel",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Nuzhen gazovyi kotel dlya doma.",
        SessionState(session_id="mixed-translit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].evidence == "gazovyi kotel"
    assert "product_evidence_rebound_to_current_message" in (
        result.structural_repairs
    )


def test_followup_drops_stale_typed_product_and_detaches_grounded_fact() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["find"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        "constraints": [
            {
                "name": "connection_size_mm",
                "value": 25,
                "unit": "мм",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "25 мм",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Присоединение 25 мм, покажите варианты.",
        SessionState(session_id="stale-followup-product"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products == []
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].applies_to_product is None
    assert "stale_typed_product_evidence_dropped" in result.structural_repairs
    assert "constraint_stale_product_binding_detached" in (
        result.structural_repairs
    )


def test_new_and_switch_keep_ungrounded_typed_product_fail_closed() -> None:
    for operation in ("new", "switch"):
        payload = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": operation,
            "acts": ["find"],
            "products": [
                {
                    "text": "циркуляционный насос",
                    "canonical_type": "circulation_pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "циркуляционный насос",
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.9,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            "Покажите подходящие варианты.",
            SessionState(session_id=f"fail-closed-{operation}"),
        )

        assert result.status == "rejected"
        assert "evidence is absent from current_message" in (
            result.rejection_reason or ""
        )


def test_wrong_numeric_value_is_dropped_then_exact_unit_anchor_is_recovered() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "насос",
            }
        ],
        "constraints": [
            {
                "name": "flow_l_min",
                "value": 180,
                "unit": "л/мин",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "расходом 18 л/мин",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен насос с расходом 18 л/мин.",
        SessionState(session_id="numeric-value-evidence"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    constraint = result.understanding.constraints[0]
    assert constraint.name == "duty_point_flow_l_h"
    assert constraint.value == 18
    assert constraint.unit == "л/мин"
    assert constraint.evidence == "18 л/мин"
    assert "constraint_numeric_value_not_in_evidence_dropped" in (
        result.structural_repairs
    )
    assert "constraint_typed_numeric_anchor_recovered" in (
        result.structural_repairs
    )


def test_typed_circulation_pump_designation_recovers_grounded_constraints() -> None:
    cases = (
        (
            "Grundfos ALPHA2 25-40 180",
            {"diameter_mm": 25, "max_head_m": 4, "mounting_length_mm": 180},
        ),
        (
            "Wilo Yonos 25/6-130",
            {"diameter_mm": 25, "max_head_m": 6, "mounting_length_mm": 130},
        ),
        (
            "Wilo Stratos 30/1-8",
            {"diameter_mm": 30, "max_head_m": 8},
        ),
    )
    for evidence, expected in cases:
        payload = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "new",
            "acts": ["find"],
            "products": [
                {
                    "text": evidence,
                    "canonical_type": "circulation_pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": evidence,
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            f"Покажите {evidence}.",
            SessionState(session_id=f"pump-designation-{len(evidence)}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert result.understanding.products[0].evidence == evidence
        actual = {
            item.name: item.value for item in result.understanding.constraints
        }
        # A catalogue-known brand in the same exact designation is a useful
        # typed selection coordinate, not a spurious engineering dimension.
        if evidence.casefold().startswith("wilo"):
            expected = {**expected, "brand": "WILO"}
        assert actual == expected
        assert all(
            item.applies_to_product == 0
            for item in result.understanding.constraints
        )
        assert "pump_designation_constraint_recovered" in result.structural_repairs


def test_irrigation_pump_overrides_ungrounded_circulation_candidate() -> None:
    """An explicit irrigation purpose wins over an LLM's pump-family guess."""

    payload = {
        "schema_version": "1.3",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "насос",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_preferences": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен насос для полива на даче",
        SessionState(session_id="irrigation-purpose-anchor"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].canonical_type == "irrigation_pump"
    assert result.understanding.products[0].evidence == "насос для полива"
    assert "irrigation_pump_goal_recovered" in result.structural_repairs


def test_irrigation_borehole_depth_is_not_misread_as_pump_head() -> None:
    """Water depth is a typed borehole input, never a ready-made pump head."""

    payload = {
        "schema_version": "1.3",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "насос",
            }
        ],
        "constraints": [
            {
                # A live LLM can mistake a depth stated as "до воды" for a
                # dynamic level.  The deterministic anchor must correct that
                # without treating the number as pump head.
                "name": "dynamic_water_level_m",
                "value": 18,
                "unit": "m",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "до воды около 18 метров",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_preferences": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен насос для полива из скважины, до воды около 18 метров",
        SessionState(session_id="irrigation-borehole-depth"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].canonical_type == "borehole_pump"
    facts = {item.name: item for item in result.understanding.constraints}
    assert facts["water_source"].value == "borehole"
    assert facts["static_water_level_m"].value == 18
    assert facts["static_water_level_m"].unit == "m"
    assert "max_head_m" not in facts
    assert "required_head_m" not in facts
    assert "dynamic_water_level_m" not in facts
    assert "irrigation_borehole_water_level_recovered" in result.structural_repairs


def test_irrigation_tank_uses_existing_open_water_pump_path() -> None:
    """A barrel is not a circulation-loop request and keeps a typed source."""

    payload = {
        "schema_version": "1.3",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "насос",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "selection_controls": [],
        "selection_preferences": [],
        "selection_strategy": {"kind": "standard", "evidence": None},
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен насос для полива из бочки",
        SessionState(session_id="irrigation-tank"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].canonical_type == "drainage_pump"
    assert {
        item.name: item.value for item in result.understanding.constraints
    } == {"water_source": "tank"}


def test_ambiguous_pump_mounting_designation_does_not_invent_length() -> None:
    evidence = "Model 25/6-130(180)"
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": evidence,
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "alternative",
                "evidence": evidence,
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        f"Нужна замена {evidence}.",
        SessionState(session_id="ambiguous-pump-designation"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    facts = {item.name: item.value for item in result.understanding.constraints}
    assert facts == {"diameter_mm": 25, "max_head_m": 6}
    assert "mounting_length_mm" not in facts


def test_pump_designation_never_overwrites_existing_unknown_fact() -> None:
    evidence = "Model 25/6-130"
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": evidence,
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": evidence,
            }
        ],
        "constraints": [
            {
                "name": "mounting_length_mm",
                "value": None,
                "unit": None,
                "status": "unknown",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "не знаю монтажную длину",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.95,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        f"Нужен {evidence}, не знаю монтажную длину.",
        SessionState(session_id="pump-designation-preserve-unknown"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    facts = {item.name: item for item in result.understanding.constraints}
    assert facts["mounting_length_mm"].status.value == "unknown"
    assert facts["mounting_length_mm"].value is None
    assert facts["diameter_mm"].value == 25
    assert facts["max_head_m"].value == 6
    assert "pump_designation_existing_constraint_preserved" in (
        result.structural_repairs
    )


def test_semantic_context_includes_bounded_pii_free_authoritative_v2_state() -> None:
    typed_state = DialogueStateV2(
        turn_number=4,
        task_stack=TaskStack(active_task_id="task-pump"),
        tasks=(
            CustomerTask(
                task_id="task-pump",
                act="select",
                target_goal_id="goal-pump",
                priority=0,
                status="in_progress",
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
        product_goals=(
            ProductGoal(
                goal_id="goal-pump",
                canonical_type="circulation_pump",
                category="pumps",
                role="target",
                evidence="мой телефон +7 999 123-45-67",
                source="semantic_interpreter",
                confidence=0.94,
                confirmed_turn=1,
                type_locked=True,
                category_locked=True,
            ),
        ),
        active_goal_id="goal-pump",
        constraints=(
            ConstraintFactV2(
                fact_id="fact-length",
                name="mounting_length_mm",
                value=180,
                unit="мм",
                status="known",
                polarity="required",
                strength="hard",
                evidence="180 мм",
                source="semantic_interpreter",
                confidence=0.93,
                goal_id="goal-pump",
                task_id="task-pump",
                source_turn=2,
            ),
            ConstraintFactV2(
                fact_id="fact-contact",
                name="contact_email",
                value="buyer@example.test",
                status="known",
                polarity="required",
                strength="hard",
                evidence="buyer@example.test",
                source="semantic_interpreter",
                confidence=0.93,
                goal_id="goal-pump",
                task_id="task-pump",
                source_turn=3,
            ),
        ),
    )
    client = SemanticJSONClient(valid_understanding())
    state = SessionState(session_id="typed-context", dialogue_state_v2=typed_state)

    SemanticInterpreter(client).interpret(
        "В системе уже стоят радиаторы, подберите циркуляционный насос и "
        "скажите цену; монтажную длину не знаю.",
        state,
    )

    audit_payload = client.messages[-1]["content"]
    decoded = json.loads(audit_payload)
    authoritative = decoded["context_before_turn"][
        "authoritative_dialogue_state_v2"
    ]
    assert authoritative["active_goal_id"] == "goal-pump"
    assert authoritative["goals"][0]["canonical_type"] == "circulation_pump"
    assert authoritative["tasks"][0]["act"] == "select"
    assert authoritative["active_facts"] == [
        {
            "fact_id": "fact-length",
            "name": "mounting_length_mm",
            "value": 180,
            "unit": "мм",
            "status": "known",
            "polarity": "required",
            "strength": "hard",
            "goal_id": "goal-pump",
            "task_id": "task-pump",
            "source_turn": 2,
        }
    ]
    assert "buyer@example.test" not in audit_payload
    assert "+7 999 123-45-67" not in audit_payload


def test_semantic_context_exposes_union_and_only_committed_v2_presentation() -> None:
    presented = (
        PresentedCandidateSummary(
            sku="V2-1",
            name="Насос V2 один",
            product_kind="circulation_pump",
            role="base_product",
            task_id="task-pump",
            goal_id="goal-pump",
            search_plan_id="search-pump",
            source_turn=4,
        ),
        PresentedCandidateSummary(
            sku="V2-2",
            name="Насос V2 два",
            product_kind="circulation_pump",
            role="base_product",
            task_id="task-pump",
            goal_id="goal-pump",
            search_plan_id="search-pump",
            source_turn=4,
        ),
    )
    summary = AnswerPlanSummary(
        plan_id="committed-plan",
        semantic_signature="committed-signature",
        task_ids=("task-pump",),
        primary_action="show_preliminary_options",
        next_step_kind="show_preliminary_options",
        validation_status="accepted",
        delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
        presented_candidates=presented,
        source_turn=4,
    )
    state = SessionState(
        session_id="committed-presentation-context",
        live_dialogue_state_v2=DialogueStateV2(
            turn_number=4,
            answer_plan_summary=summary,
        ),
        v2_last_products=[
            ProductCard(
                sku="V2-1",
                name="Насос V2 один",
                price=100.0,
                stock_status="in_stock",
                url="https://example.test/v2-1",
            )
        ],
        last_products=[
            ProductCard(
                sku="v2-1",
                name="Дубликат того же артикула",
                price=100.0,
                stock_status="in_stock",
                url="https://example.test/v2-1-duplicate",
            ),
            ProductCard(
                sku="LEGACY-1",
                name="Legacy card",
                price=200.0,
                stock_status="in_stock",
                url="https://example.test/legacy-1",
            ),
        ],
    )

    context = semantic_context(state)

    assert context["active_product_skus"] == ["V2-1", "LEGACY-1"]
    assert context["last_committed_presentation"] == {
        "plan_id": "committed-plan",
        "source_turn": 4,
        "candidates": [
            {
                "sku": "V2-1",
                "name": "Насос V2 один",
                "product_kind": "circulation_pump",
                "role": "base_product",
                "task_id": "task-pump",
                "goal_id": "goal-pump",
                "source_turn": 4,
            },
            {
                "sku": "V2-2",
                "name": "Насос V2 два",
                "product_kind": "circulation_pump",
                "role": "base_product",
                "task_id": "task-pump",
                "goal_id": "goal-pump",
                "source_turn": 4,
            },
        ],
    }

    shadow_state = state.model_copy(deep=True)
    shadow_state.live_dialogue_state_v2 = DialogueStateV2(
        turn_number=4,
        answer_plan_summary=summary.model_copy(
            update={
                "delivery_status": ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
            }
        ),
    )
    assert semantic_context(shadow_state)["last_committed_presentation"] is None


def test_prompts_require_delivery_act_and_only_explicit_unknown_states() -> None:
    for prompt in (SEMANTIC_INTERPRETER_PROMPT, SEMANTIC_AUDIT_PROMPT):
        assert "check_delivery" in prompt
        assert "unknown/refused/deferred" in prompt
        assert "подтверд" in prompt or "подтверж" in prompt
        assert "объясн" in prompt
        assert "отсутств" in prompt


def test_preferred_unknown_duplicate_relaxes_known_fact_without_losing_value() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": 35,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "35 кВт",
            },
            {
                "name": "power_kw",
                "value": None,
                "unit": "кВт",
                "status": "unknown",
                "polarity": "preferred",
                "applies_to_product": 0,
                "evidence": "можно ближайшую мощность",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.92,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл 35 кВт, но можно ближайшую мощность.",
        SessionState(session_id="known-preferred-merge"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    constraint = result.understanding.constraints[0]
    assert constraint.name == "power_kw"
    assert constraint.value == 35
    assert constraint.status.value == "known"
    assert constraint.polarity.value == "preferred"
    assert constraint.evidence == "35 кВт, но можно ближайшую мощность"
    assert "constraint_known_value_preferred_unknown_merged" in (
        result.structural_repairs
    )


def test_required_unknown_duplicate_is_not_merged_into_known_fact() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": 35,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "35 кВт",
            },
            {
                "name": "power_kw",
                "value": None,
                "unit": "кВт",
                "status": "unknown",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "точную мощность не знаю",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.88,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен котёл 35 кВт, хотя точную мощность не знаю.",
        SessionState(session_id="required-unknown-not-merged"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 2
    assert "constraint_known_value_preferred_unknown_merged" not in (
        result.structural_repairs
    )


def test_non_known_constraints_require_explicit_customer_status_evidence() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "промывной фильтр",
                "canonical_type": "water_filter",
                "category": "filters",
                "role": "target",
                "evidence": "промывной фильтр",
            }
        ],
        "constraints": [
            {
                "name": "connection_size",
                "value": None,
                "unit": None,
                "status": "unknown",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "фильтр",
            },
            {
                "name": "micron_rating_um",
                "value": None,
                "unit": None,
                "status": "deferred",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "промывной фильтр",
            },
            {
                "name": "brand",
                "value": None,
                "unit": None,
                "status": "refused",
                "polarity": "preferred",
                "applies_to_product": 0,
                "evidence": "промывной фильтр",
            },
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите промывной фильтр.",
        SessionState(session_id="non-known-list"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "constraint_non_known_without_explicit_status_dropped" in (
        result.structural_repairs
    )


def test_each_non_known_status_is_kept_only_with_matching_explicit_signal() -> None:
    cases = (
        ("unknown", "Монтажную длину не знаю."),
        ("refused", "Монтажную длину не хочу сообщать."),
        ("deferred", "Монтажную длину измерю позже."),
    )
    for status, message in cases:
        evidence = message.rstrip(".")
        payload = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "refine",
            "acts": ["select"],
            "products": [],
            "constraints": [
                {
                    "name": "mounting_length_mm",
                    "value": None,
                    "unit": "мм",
                    "status": status,
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": evidence,
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": True,
            "confidence": 0.93,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            message,
            SessionState(session_id=f"explicit-{status}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert result.understanding.constraints[0].status.value == status


def test_pump_working_point_unit_anchors_are_recovered_without_conversion() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Подберите циркуляционный насос: 18 л/мин при напоре 4,2 м.",
        SessionState(session_id="pump-unit-anchors"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    facts = {item.name: item for item in result.understanding.constraints}
    assert facts["duty_point_flow_l_h"].value == 18
    assert facts["duty_point_flow_l_h"].unit == "л/мин"
    assert facts["duty_point_flow_l_h"].evidence == "18 л/мин"
    assert facts["duty_point_head_m"].value == 4.2
    assert facts["duty_point_head_m"].unit == "м"
    assert facts["duty_point_head_m"].evidence == "напоре 4,2 м"


def test_max_head_rejects_pressure_units_and_records_typed_ambiguity() -> None:
    for unit, evidence in (("bar", "4,2 бар"), ("kPa", "4,2 кПа")):
        payload = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "циркуляционный насос",
                    "canonical_type": "circulation_pump",
                    "category": "pumps",
                    "role": "target",
                    "evidence": "циркуляционный насос",
                }
            ],
            "constraints": [
                {
                    "name": "max_head_m",
                    "value": 4.2,
                    "unit": unit,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": evidence,
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.9,
        }

        result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
            f"Нужен циркуляционный насос с максимальным напором {evidence}.",
            SessionState(session_id=f"head-pressure-unit-{unit}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        assert result.understanding.constraints == []
        assert len(result.understanding.ambiguities) == 1
        ambiguity = result.understanding.ambiguities[0]
        assert ambiguity.kind == "constraint_unit_incompatible"
        assert ambiguity.evidence == evidence
        assert "pressure" in ambiguity.description
        assert "length" in ambiguity.description
        assert "constraint_incompatible_unit_dropped" in (
            result.structural_repairs
        )
        assert "constraint_unit_ambiguity_added" in result.structural_repairs


def test_max_head_rejects_pressure_unit_found_only_in_evidence() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        "constraints": [
            {
                "name": "max_head_m",
                "value": 6,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "6 бар",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен циркуляционный насос, указано 6 бар.",
        SessionState(session_id="head-pressure-evidence-only"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert result.understanding.ambiguities[0].evidence == "6 бар"
    assert "constraint_incompatible_unit_dropped" in result.structural_repairs


def test_operating_pressure_accepts_pressure_unit_without_conversion() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "шаровой кран",
                "canonical_type": "ball_valve",
                "category": "valves",
                "role": "target",
                "evidence": "шаровой кран",
            }
        ],
        "constraints": [
            {
                "name": "operating_pressure_bar",
                "value": 6,
                "unit": "bar",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "6 бар",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен шаровой кран на рабочее давление 6 бар.",
        SessionState(session_id="operating-pressure-bar"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "operating_pressure_bar"
    assert fact.value == 6
    assert fact.unit == "bar"
    assert result.understanding.ambiguities == []
    assert "constraint_incompatible_unit_dropped" not in result.structural_repairs


def test_max_head_accepts_length_unit_without_conversion() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "циркуляционный насос",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "target",
                "evidence": "циркуляционный насос",
            }
        ],
        "constraints": [
            {
                "name": "max_head_m",
                "value": 420,
                "unit": "cm",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "420 см",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужен циркуляционный насос с напором 420 см.",
        SessionState(session_id="head-length-unit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    fact = result.understanding.constraints[0]
    assert fact.name == "max_head_m"
    assert fact.value == 420
    assert fact.unit == "cm"
    assert result.understanding.ambiguities == []


def test_numeric_followup_uses_only_authoritative_active_product_type() -> None:
    typed_state = DialogueStateV2(
        turn_number=2,
        task_stack=TaskStack(active_task_id="task-pump"),
        tasks=(
            CustomerTask(
                task_id="task-pump",
                act="select",
                target_goal_id="goal-pump",
                priority=0,
                status="in_progress",
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
        product_goals=(
            ProductGoal(
                goal_id="goal-pump",
                canonical_type="circulation_pump",
                category="pumps",
                role="target",
                evidence="циркуляционный насос",
                source="semantic_interpreter",
                confidence=0.95,
                confirmed_turn=1,
                type_locked=True,
                category_locked=True,
            ),
        ),
        active_goal_id="goal-pump",
    )
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "refine",
        "acts": ["select"],
        "products": [],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Рабочая точка — 17,5 л/мин и 4.1 м напора.",
        SessionState(session_id="typed-followup", dialogue_state_v2=typed_state),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products == []
    facts = {item.name: item for item in result.understanding.constraints}
    assert facts["duty_point_flow_l_h"].value == 17.5
    assert facts["duty_point_flow_l_h"].applies_to_product is None
    assert facts["duty_point_head_m"].value == 4.1
    assert facts["duty_point_head_m"].applies_to_product is None


def test_typed_pex_designation_recovers_outer_diameter() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "труба PEX 16",
                "canonical_type": "pex_pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "труба PEX 16",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите трубу PEX 16.",
        SessionState(session_id="pex-diameter-anchor"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "diameter_mm"
    assert fact.value == 16
    assert fact.evidence == "PEX 16"


def test_replacement_request_promotes_single_source_product_to_alternative() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "Grundfos ALPHA2 25-40 180",
                "canonical_type": "circulation_pump",
                "category": "pumps",
                "role": "context",
                "evidence": "Grundfos ALPHA2 25-40 180",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужна замена Grundfos ALPHA2 25-40 180.",
        SessionState(session_id="replacement-role"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.products[0].role.value == "alternative"
    assert (
        "replacement_product_role_promoted_to_alternative"
        in result.structural_repairs
    )


def test_model_identifier_does_not_exempt_real_power_unit_anchor() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "газовый котёл ThermoX2 35 кВт",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовый котёл ThermoX2 35 кВт",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Покажите газовый котёл ThermoX2 35 кВт.",
        SessionState(session_id="model-plus-real-unit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    power_constraints = [
        item for item in result.understanding.constraints if item.name == "power_kw"
    ]
    assert len(power_constraints) == 1
    assert power_constraints[0].value == 35


def test_explicit_power_range_collapses_model_endpoint_facts() -> None:
    message = "Покажите газовый котёл мощностью в районе 10–15 кВт."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовый котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": 10,
                "unit": "кВт",
                "status": "known",
                "polarity": "preferred",
                "applies_to_product": 0,
                "evidence": "мощностью в районе 10–15 кВт",
            },
            {
                "name": "power_kw",
                "value": 15,
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "15 кВт",
            },
        ],
        "information_requests": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="power-range-endpoints"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    power_constraints = [
        item for item in result.understanding.constraints if item.name == "power_kw"
    ]
    assert len(power_constraints) == 1
    constraint = power_constraints[0]
    assert constraint.name == "power_kw"
    assert constraint.value == "10–15"
    assert constraint.unit == "кВт"
    assert constraint.polarity.value == "preferred"
    assert constraint.evidence == "10–15 кВт"
    assert "constraint_numeric_range_endpoints_collapsed" in (
        result.structural_repairs
    )
    assert "constraint_typed_numeric_range_recovered" in (
        result.structural_repairs
    )


def test_direct_model_power_range_is_canonicalized_without_duplication() -> None:
    message = "Нужен котёл от 10 до 15 кВт."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "котёл",
                "canonical_type": "boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "котёл",
            }
        ],
        "constraints": [
            {
                "name": "boiler_power_kw",
                "value": "10-15 кВт",
                "unit": "kw",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "от 10 до 15 кВт",
            }
        ],
        "information_requests": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="power-range-direct"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    constraint = result.understanding.constraints[0]
    assert constraint.name == "power_kw"
    assert constraint.value == "10–15"
    assert constraint.unit == "кВт"
    assert constraint.evidence == "от 10 до 15 кВт"
    assert "constraint_numeric_range_anchor_canonicalized" in (
        result.structural_repairs
    )
    assert "constraint_numeric_value_not_in_evidence_dropped" not in (
        result.structural_repairs
    )


def test_string_numeric_value_without_current_evidence_is_dropped() -> None:
    message = "Подберите газовый котёл, мощность пока не уточняю."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовый котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": "120",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": message,
            }
        ],
        "information_requests": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="stale-string-number"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.name for item in result.understanding.constraints] == [
        "boiler_type"
    ]
    assert all(item.name != "power_kw" for item in result.understanding.constraints)
    assert "constraint_numeric_value_not_in_evidence_dropped" in (
        result.structural_repairs
    )


def test_numeric_range_is_rejected_for_discrete_circuit_count() -> None:
    message = "Нужен газовый котёл на 1–2 контура."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовый котёл",
            }
        ],
        "constraints": [
            {
                "name": "circuits",
                "value": "1–2",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "1–2 контура",
            }
        ],
        "information_requests": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="circuits-range-fails-closed"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.name for item in result.understanding.constraints] == [
        "boiler_type"
    ]
    assert all(item.name != "circuits" for item in result.understanding.constraints)
    assert "constraint_numeric_range_not_allowed_dropped" in (
        result.structural_repairs
    )
    assert "power_kw" in RANGE_CAPABLE_CONSTRAINT_FACTS
    assert "circuits" not in RANGE_CAPABLE_CONSTRAINT_FACTS
    assert "port_count" not in RANGE_CAPABLE_CONSTRAINT_FACTS


def test_string_numeric_unit_must_be_present_in_current_evidence() -> None:
    message = "Нужен газовый котёл в диапазоне 15–20."
    payload = {
        "schema_version": "1.2",
        "language": "ru",
        "operation": "new",
        "acts": ["select"],
        "products": [
            {
                "text": "газовый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "газовый котёл",
            }
        ],
        "constraints": [
            {
                "name": "power_kw",
                "value": "15–20",
                "unit": "кВт",
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "15–20",
            }
        ],
        "information_requests": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        SessionState(session_id="string-unit-not-grounded"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.name for item in result.understanding.constraints] == [
        "boiler_type"
    ]
    assert all(item.name != "power_kw" for item in result.understanding.constraints)
    assert "constraint_numeric_unit_not_in_evidence_dropped" in (
        result.structural_repairs
    )
    assert result.understanding.ambiguities[0].kind == (
        "constraint_unit_not_grounded"
    )


def test_typed_numeric_anchor_with_two_possible_products_fails_closed() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["compare"],
        "products": [
            {
                "text": "первый котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "target",
                "evidence": "первый котёл",
            },
            {
                "text": "второй котёл",
                "canonical_type": "gas_boiler",
                "category": "boilers",
                "role": "alternative",
                "evidence": "второй котёл",
            },
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.9,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Сравните первый котёл и второй котёл; требование 35 кВт.",
        SessionState(session_id="ambiguous-power-binding"),
    )

    assert result.status == "rejected"
    assert "ambiguous product binding" in (result.rejection_reason or "")


def _shown_card(sku: str, name: str) -> ProductCard:
    return ProductCard(
        sku=sku,
        name=name,
        price=100.0,
        stock_status="in_stock",
        url=f"https://example.test/{sku}",
    )


def _active_boiler_state(*cards: ProductCard, use_v2_cards: bool = False) -> SessionState:
    typed_state = DialogueStateV2(
        turn_number=3,
        task_stack=TaskStack(active_task_id="task-boiler"),
        tasks=(
            CustomerTask(
                task_id="task-boiler",
                act="select",
                target_goal_id="goal-boiler",
                priority=0,
                status="in_progress",
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
        product_goals=(
            ProductGoal(
                goal_id="goal-boiler",
                canonical_type="gas_boiler",
                category="boilers",
                role="target",
                evidence="газовый котёл",
                source="semantic_interpreter",
                confidence=0.95,
                confirmed_turn=1,
                type_locked=True,
                category_locked=True,
            ),
        ),
        active_goal_id="goal-boiler",
    )
    card_list = list(cards)
    return SessionState(
        session_id="shown-card-reference",
        dialogue_state_v2=typed_state,
        last_products=[] if use_v2_cards else card_list,
        v2_last_products=card_list if use_v2_cards else [],
    )


def _reference_payload(*, constraints: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "continue",
        "acts": ["explain"],
        "products": [],
        "constraints": constraints or [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.92,
    }


def test_unique_identifier_of_shown_card_becomes_grounded_sku_fact() -> None:
    state = _active_boiler_state(
        _shown_card("2202209", "Котёл газовый BaltGaz Turbo SB24"),
        use_v2_cards=True,
    )

    result = SemanticInterpreter(SemanticJSONClient(_reference_payload())).interpret(
        "Какая минимальная мощность у SB24?",
        state,
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "sku"
    assert fact.value == "2202209"
    assert fact.evidence == "SB24"
    assert fact.applies_to_product is None
    assert "constraint_shown_card_identifier_resolved_to_sku" in (
        result.structural_repairs
    )


def test_ambiguous_identifier_in_two_shown_cards_does_not_choose_a_sku() -> None:
    state = _active_boiler_state(
        _shown_card("boiler-a", "Котёл Alpha SB24"),
        _shown_card("boiler-b", "Котёл Beta SB24"),
    )

    result = SemanticInterpreter(SemanticJSONClient(_reference_payload())).interpret(
        "Сравни для SB24 минимальную мощность.",
        state,
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "shown_card_identifier_ambiguous" in result.structural_repairs


def test_unseen_mixed_identifier_does_not_create_catalogue_identity() -> None:
    state = _active_boiler_state(
        _shown_card("2202209", "Котёл газовый BaltGaz Turbo SB24"),
    )

    result = SemanticInterpreter(SemanticJSONClient(_reference_payload())).interpret(
        "А для ZX91 какой диапазон мощности?",
        state,
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "shown_card_identifier_unmatched" in result.structural_repairs


def test_bare_number_never_becomes_a_shown_card_reference() -> None:
    state = _active_boiler_state(
        _shown_card("24", "Котёл газовый 24 кВт"),
    )

    result = SemanticInterpreter(SemanticJSONClient(_reference_payload())).interpret(
        "Что означает 24 в характеристиках?",
        state,
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "constraint_shown_card_identifier_resolved_to_sku" not in (
        result.structural_repairs
    )


def test_existing_full_shown_sku_anchor_is_verified_and_preserved() -> None:
    state = _active_boiler_state(
        _shown_card("2202209", "Котёл газовый BaltGaz Turbo SB24"),
    )
    payload = _reference_payload(
        constraints=[
            {
                "name": "sku",
                "value": "2202209",
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "2202209",
            }
        ]
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Проверь характеристики артикула 2202209.",
        state,
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].name == "sku"
    assert result.understanding.constraints[0].value == "2202209"
    assert result.understanding.constraints[0].evidence == "2202209"
    assert "constraint_shown_card_sku_verified" in result.structural_repairs


def _active_pump_session() -> SessionState:
    typed_state = DialogueStateV2(
        turn_number=3,
        task_stack=TaskStack(active_task_id="task-pump-followup"),
        tasks=(
            CustomerTask(
                task_id="task-pump-followup",
                act="select",
                target_goal_id="goal-pump-followup",
                priority=0,
                status="in_progress",
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
        product_goals=(
            ProductGoal(
                goal_id="goal-pump-followup",
                canonical_type="circulation_pump",
                category="pumps",
                role="target",
                evidence="циркуляционный насос",
                source="semantic_interpreter",
                confidence=0.95,
                confirmed_turn=1,
                type_locked=True,
                category_locked=True,
            ),
        ),
        active_goal_id="goal-pump-followup",
    )
    return SessionState(
        session_id="active-pump-followup",
        dialogue_state_v2=typed_state,
    )


def _active_boiler_power_session(value: int | float = 15) -> SessionState:
    typed_state = DialogueStateV2(
        turn_number=3,
        task_stack=TaskStack(active_task_id="task-boiler-power"),
        tasks=(
            CustomerTask(
                task_id="task-boiler-power",
                act="select",
                target_goal_id="goal-boiler-power",
                priority=0,
                status="in_progress",
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
        product_goals=(
            ProductGoal(
                goal_id="goal-boiler-power",
                canonical_type="gas_boiler",
                category="boilers",
                role="target",
                evidence="газовый котёл",
                source="semantic_interpreter",
                confidence=0.95,
                confirmed_turn=1,
                type_locked=True,
                category_locked=True,
            ),
        ),
        constraints=(
            ConstraintFactV2(
                fact_id="fact-boiler-power",
                name="power_kw",
                value=value,
                unit="кВт",
                status="known",
                polarity="required",
                strength="hard",
                evidence=f"{value} кВт",
                source="semantic_interpreter",
                confidence=0.95,
                goal_id="goal-boiler-power",
                task_id="task-boiler-power",
                source_turn=2,
            ),
        ),
        active_goal_id="goal-boiler-power",
    )
    return SessionState(
        session_id="active-boiler-power-followup",
        dialogue_state_v2=typed_state,
    )


def _boiler_power_refinement_payload(
    constraints: list[dict[str, Any]],
    *,
    operation: str = "refine",
) -> dict[str, Any]:
    return {
        "schema_version": "1.2",
        "language": "ru",
        "operation": operation,
        "acts": ["find"],
        "products": [],
        "constraints": constraints,
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "information_requests": [],
        "answers_pending_question": False,
        "confidence": 0.94,
    }


def _known_power(value: Any, evidence: str) -> dict[str, Any]:
    return {
        "name": "power_kw",
        "value": value,
        "unit": "кВт",
        "status": "known",
        "polarity": "required",
        "applies_to_product": None,
        "evidence": evidence,
    }


def test_unique_grounded_active_fact_change_promotes_operation_to_correct() -> None:
    message = "Тогда 12 кВт."
    payload = _boiler_power_refinement_payload(
        [
            _known_power("12", "12 кВт"),
            _known_power(12, "12 кВт"),
        ],
        operation="continue",
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_boiler_power_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "correct"
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "power_kw"
    assert fact.value == 12
    assert not isinstance(fact.value, str)
    assert fact.evidence == "12 кВт"
    assert "constraint_equivalent_numeric_duplicate_preferred_typed" in (
        result.structural_repairs
    )
    assert "operation_promoted_to_correct_from_active_fact_change" in (
        result.structural_repairs
    )


def test_numeric_anchor_recovery_does_not_compete_with_grounded_model_target() -> None:
    message = "Уменьшим мощность на 2 кВт — до 13 кВт."
    payload = _boiler_power_refinement_payload(
        [_known_power(13, message)],
        operation="refine",
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_boiler_power_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "correct"
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].name == "power_kw"
    assert result.understanding.constraints[0].value == 13
    assert all(
        fact.value != 2 for fact in result.understanding.constraints
    )
    assert "typed_numeric_anchor_skipped_model_fact_present" in (
        result.structural_repairs
    )


def test_equivalent_active_fact_repeat_does_not_promote_correction() -> None:
    message = "Да, 15 кВт."
    payload = _boiler_power_refinement_payload(
        [_known_power("15", "15 кВт"), _known_power(15, "15 кВт")]
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_boiler_power_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "refine"
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].value == 15
    assert "operation_promoted_to_correct_from_active_fact_change" not in (
        result.structural_repairs
    )


def test_range_or_multiple_current_values_do_not_promote_correction() -> None:
    for message, constraints in (
        ("Подойдёт диапазон 12–14 кВт.", [_known_power("12–14", "12–14 кВт")]),
        (
            "Можно 12 или 14 кВт.",
            [_known_power(12, "12 кВт"), _known_power(14, "14 кВт")],
        ),
    ):
        result = SemanticInterpreter(SemanticJSONClient(
            _boiler_power_refinement_payload(constraints)
        )).interpret(message, _active_boiler_power_session())

        assert result.status == "accepted"
        assert result.understanding is not None
        assert result.understanding.operation.value == "refine"
        assert "operation_promoted_to_correct_from_active_fact_change" not in (
            result.structural_repairs
        )


def test_unknown_current_fact_does_not_promote_correction() -> None:
    message = "Мощность не знаю."
    constraint = _known_power(None, "не знаю")
    constraint["status"] = "unknown"

    result = SemanticInterpreter(SemanticJSONClient(
        _boiler_power_refinement_payload([constraint])
    )).interpret(message, _active_boiler_power_session())

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "refine"
    assert "operation_promoted_to_correct_from_active_fact_change" not in (
        result.structural_repairs
    )


def test_generic_technical_check_cannot_become_delivery_act() -> None:
    payload = _reference_payload()
    payload["acts"] = ["check_delivery"]

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Проверь это сам по Q-H кривой.",
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["explain"]
    assert "delivery_act_reclassified_as_technical_explain" in (
        result.structural_repairs
    )


def test_explicit_logistics_question_preserves_delivery_act() -> None:
    payload = _reference_payload()
    payload["acts"] = ["check_delivery"]

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Какой срок доставки до пункта выдачи?",
        _active_boiler_state(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.value for item in result.understanding.acts] == ["check_delivery"]
    assert "delivery_act_reclassified_as_technical_explain" not in (
        result.structural_repairs
    )


def test_typed_characteristic_question_continues_active_goal_and_adds_explain() -> None:
    payload = _reference_payload(
        constraints=[
            {
                "name": "mounting_length_mm",
                "value": None,
                "unit": "мм",
                "status": "unknown",
                "polarity": "required",
                "applies_to_product": None,
                "evidence": "монтажная длина",
            }
        ]
    )
    payload["operation"] = "new"
    payload["acts"] = []
    payload["references"] = [
        {
            "kind": "deictic",
            "text": "у них",
            "target_hint": None,
            "evidence": "у них",
        }
    ]

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Какая у них монтажная длина?",
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "continue"
    assert [item.value for item in result.understanding.acts] == ["explain"]
    assert result.understanding.products == []
    assert result.understanding.constraints == []
    assert "typed_characteristic_question_rebound_to_active_goal" in (
        result.structural_repairs
    )
    assert "typed_characteristic_question_explain_act_added" in (
        result.structural_repairs
    )


def test_pipe_quantity_unit_anchor_recovers_requested_metres_without_conversion() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "труба PEX",
                "canonical_type": "pex_pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "трубы PEX",
            }
        ],
        "constraints": [],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Нужно 800 метров трубы PEX.",
        SessionState(session_id="pipe-quantity-unit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "requested_quantity_m"
    assert fact.value == 800
    assert fact.unit == "m"
    assert fact.evidence == "800 метров"
    assert "constraint_typed_numeric_anchor_recovered" in (
        result.structural_repairs
    )


def test_pipe_quantity_anchor_fills_missing_unit_on_existing_fact() -> None:
    payload = {
        "schema_version": "1.1",
        "language": "ru",
        "operation": "new",
        "acts": ["find"],
        "products": [
            {
                "text": "труба PEX",
                "canonical_type": "pex_pipe",
                "category": "pipes",
                "role": "target",
                "evidence": "трубы PEX",
            }
        ],
        "constraints": [
            {
                "name": "requested_quantity_m",
                "value": 800,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": "800 метров",
            }
        ],
        "references": [],
        "ambiguities": [],
        "workflow_controls": [],
        "answers_pending_question": False,
        "confidence": 0.91,
    }

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        "Для объекта нужно 800 метров трубы PEX.",
        SessionState(session_id="pipe-quantity-existing-unit"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    assert result.understanding.constraints[0].value == 800
    assert result.understanding.constraints[0].unit == "m"
    assert "constraint_numeric_anchor_unit_recovered" in (
        result.structural_repairs
    )


def test_information_request_preserves_presented_candidate_scope() -> None:
    message = "Какая монтажная длина у этих показанных моделей?"
    payload = information_request_payload(
        acts=["explain"],
        products=[],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "value",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "subject_scope": "presented_candidates",
                "applies_to_product": None,
                "evidence": message,
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    request = result.understanding.information_requests[0]
    assert request.subject_scope.value == "presented_candidates"
    assert result.understanding.constraints == []


def test_old_information_request_defaults_to_customer_goal_scope() -> None:
    message = "Какая монтажная длина моего старого насоса?"
    payload = information_request_payload(
        acts=["explain"],
        products=[],
        information_requests=[
            {
                "fact_name": "mounting_length_mm",
                "purpose": "value",
                "requested_outputs": ["explanation"],
                "output_relation": "all",
                "source_kind": None,
                "act": "explain",
                "applies_to_product": None,
                "evidence": message,
            }
        ],
    )

    result = SemanticInterpreter(SemanticJSONClient(payload)).interpret(
        message,
        _active_pump_session(),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert (
        result.understanding.information_requests[0].subject_scope.value
        == "customer_goal"
    )
