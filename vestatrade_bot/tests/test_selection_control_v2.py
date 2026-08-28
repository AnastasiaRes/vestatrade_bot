from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import pytest

from app.agents.semantic_interpreter import SemanticInterpreter, TurnUnderstanding, semantic_context
from app.catalog_v2.contracts import ReadinessStatus
from app.catalog_v2.normalization import build_catalog_snapshot
from app.catalog_v2.planner import plan_catalog_search
from app.catalog_v2.readiness import assess_task_readiness
from app.catalog_v2.registry import DEFAULT_CONTRACTS, ProductContractRegistry
from app.dialogue_v2.contracts import (
    AnswerPlanSummary,
    CustomerTask,
    DialogueStateV2,
    NextActionKind,
    ProductCategory,
    ProductGoal,
    ProductRole,
    SelectionControlKind,
    ShadowDeliveryStatus,
    TaskAct,
    TaskStack,
    TaskStatus,
    TurnMetadata,
)
from app.dialogue_v2.reducer import reduce_dialogue_state
from app.dialogue_v2.seller_policy import SellerPolicy
from app.models import Product, SessionState


class _SemanticClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.settings = SimpleNamespace(
            llm_enabled=True,
            llm_model="test/semantic-model",
        )
        self.last_fallback_reason = None

    def complete_json(self, *_args, **_kwargs):
        return self.payload, True

    def request_budget(self):
        return nullcontext()


def _pipe_state(*, with_delivered_question: bool = False) -> DialogueStateV2:
    goal = ProductGoal(
        goal_id="goal-pipe",
        canonical_type="pipe",
        category=ProductCategory.PIPES,
        role=ProductRole.TARGET,
        evidence="труба",
        source="test",
        confidence=1.0,
        confirmed_turn=1,
        type_locked=True,
        category_locked=True,
    )
    task = CustomerTask(
        task_id="task-pipe",
        act=TaskAct.SELECT,
        target_goal_id=goal.goal_id,
        priority=0,
        status=TaskStatus.BLOCKED if with_delivered_question else TaskStatus.IN_PROGRESS,
        source="test",
        source_turn=1,
        created_turn=1,
        last_addressed_turn=1,
    )
    answer_summary = (
        AnswerPlanSummary(
            plan_id="plan-question",
            semantic_signature="semantic-signature",
            task_ids=(task.task_id,),
            primary_action=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            question_fact="diameter_mm",
            question_id="question-diameter",
            question_task_id=task.task_id,
            question_goal_id=goal.goal_id,
            next_step_kind="ask_question",
            validation_status="accepted",
            delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
            source_turn=1,
        )
        if with_delivered_question
        else None
    )
    return DialogueStateV2(
        turn_number=1,
        task_stack=TaskStack(active_task_id=task.task_id),
        tasks=(task,),
        product_goals=(goal,),
        active_goal_id=goal.goal_id,
        answer_plan_summary=answer_summary,
        applied_turn_ids=("turn-1",),
    )


def _pipe_state_with_pending_fact(
    fact_name: str,
    *,
    canonical_type: str = "pipe",
) -> DialogueStateV2:
    state = _pipe_state(with_delivered_question=True)
    assert state.answer_plan_summary is not None
    return state.model_copy(
        update={
            "product_goals": (
                state.product_goals[0].model_copy(
                    update={"canonical_type": canonical_type}
                ),
            ),
            "answer_plan_summary": state.answer_plan_summary.model_copy(
                update={"question_fact": fact_name}
            )
        }
    )


def _continue_control() -> TurnUnderstanding:
    return TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
            "operation": "continue",
            "acts": [],
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [
                {
                    "kind": "continue_with_confirmed_facts",
                    "evidence": "Покажите по тем данным, что уже есть",
                }
            ],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": "Покажите по тем данным, что уже есть",
            },
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.96,
        }
    )


def _new_pipe_payload(*, canonical_type: str = "pipe") -> dict[str, object]:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "полипропиленовая труба",
                    "canonical_type": canonical_type,
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовая труба",
                }
            ],
            "constraints": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    return payload


def test_semantic_contract_accepts_confirmed_facts_only_control() -> None:
    payload = _continue_control().model_dump(mode="json")
    message = "Покажите по тем данным, что уже есть"

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="selection-control-semantic"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert [item.kind for item in result.understanding.selection_controls] == [
        SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS
    ]


def test_semantic_contract_accepts_control_on_first_product_turn() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["find"],
            "products": [
                {
                    "text": "полипропиленовые трубы",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовые трубы",
                }
            ],
        }
    )
    message = "Покажите полипропиленовые трубы по тем данным, что есть"
    payload["selection_controls"][0]["evidence"] = "по тем данным, что есть"
    payload["selection_strategy"]["evidence"] = "по тем данным, что есть"

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="first-turn-selection-control"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.operation.value == "new"
    assert result.understanding.selection_controls[0].kind == (
        SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS
    )


def test_grounded_continue_strategy_recovers_missing_typed_control() -> None:
    message = "Можно без остальных уточнений, покажите по тому, что известно"
    evidence = "покажите по тому, что известно"
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "selection_controls": [],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": evidence,
            },
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="strategy-recovers-control"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.evidence for item in result.understanding.selection_controls] == [
        evidence
    ]
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.evidence == evidence
    assert "selection_control_recovered_from_grounded_strategy" in (
        result.structural_repairs
    )


def test_grounded_control_recovers_missing_continue_strategy_evidence() -> None:
    message = "Давление сообщать не хочу; покажите по тому, что уже известно"
    evidence = "покажите по тому, что уже известно"
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "selection_controls": [
                {
                    "kind": "continue_with_confirmed_facts",
                    "evidence": evidence,
                }
            ],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": None,
            },
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="control-recovers-strategy-evidence"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.evidence == evidence
    assert result.understanding.selection_controls[0].evidence == evidence
    assert "selection_strategy_evidence_recovered_from_control" in (
        result.structural_repairs
    )


def test_evidenceless_continue_verdict_cannot_authorize_selection_control() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "selection_controls": [],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": None,
            },
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Продолжайте",
        SessionState(session_id="evidenceless-strategy-is-narrowed"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.selection_controls == []
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.kind.value == "standard"
    assert "selection_strategy_safely_defaulted_to_standard" in (
        result.structural_repairs
    )


def test_untyped_ambiguous_verdict_does_not_discard_grounded_turn_fields() -> None:
    payload = _new_pipe_payload()
    payload.update(
        {
            "selection_controls": [],
            "selection_strategy": {"kind": "ambiguous", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Нужна полипропиленовая труба",
        SessionState(session_id="untyped-ambiguous-strategy-is-narrowed"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.products) == 1
    assert result.understanding.products[0].canonical_type == "pipe"
    assert result.understanding.constraints == []
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.kind.value == "standard"
    assert (
        "untyped_ambiguous_selection_strategy_defaulted_to_standard"
        in result.structural_repairs
    )


def test_non_known_fact_name_is_rebound_by_product_ontology() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["find"],
            "products": [
                {
                    "text": "полипропиленовая труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовая труба",
                }
            ],
            "constraints": [
                {
                    "name": "pipe_service",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "Диаметр не знаю",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    message = "Нужна полипропиленовая труба. Диаметр не знаю."

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="non-known-fact-grounding"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints[0].name == "diameter_mm"
    assert result.understanding.constraints[0].status.value == "unknown"
    assert "constraint_non_known_fact_name_rebound" in result.structural_repairs


def test_explicit_unknown_fact_is_recovered_when_model_omits_it() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["find"],
            "products": [
                {
                    "text": "полипропиленовая труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовая труба",
                }
            ],
            "constraints": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    message = "Нужна полипропиленовая труба. Диаметр не знаю."

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(session_id="recover-explicit-unknown"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    constraint = result.understanding.constraints[0]
    assert constraint.name == "diameter_mm"
    assert constraint.status.value == "unknown"
    assert constraint.value is None
    assert constraint.evidence == "Диаметр не знаю"
    assert "constraint_explicit_non_known_fact_recovered" in (
        result.structural_repairs
    )


def test_pipe_operating_temperature_unknown_is_recovered() -> None:
    result = SemanticInterpreter(_SemanticClient(_new_pipe_payload())).interpret(
        "Нужна полипропиленовая труба. Температуру не знаю.",
        SessionState(session_id="pipe-temperature-unknown"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [
        (item.name, item.status.value)
        for item in result.understanding.constraints
    ] == [("operating_temperature_c", "unknown")]


def test_pex_operating_pressure_deferred_is_recovered() -> None:
    result = SemanticInterpreter(
        _SemanticClient(_new_pipe_payload(canonical_type="pex_pipe"))
    ).interpret(
        "Нужна полипропиленовая труба. Давление уточню позже.",
        SessionState(session_id="pex-pressure-deferred"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [
        (item.name, item.status.value)
        for item in result.understanding.constraints
    ] == [("operating_pressure_bar", "deferred")]


def test_pipe_temperature_and_pressure_statuses_do_not_collapse() -> None:
    result = SemanticInterpreter(_SemanticClient(_new_pipe_payload())).interpret(
        (
            "Нужна полипропиленовая труба. Температуру не знаю, "
            "давление уточню позже."
        ),
        SessionState(session_id="pipe-two-unavailable-facts"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert {
        item.name: item.status.value
        for item in result.understanding.constraints
    } == {
        "operating_temperature_c": "unknown",
        "operating_pressure_bar": "deferred",
    }


@pytest.mark.parametrize(
    "group_evidence",
    (
        "Температуру и давление не знаю",
        "Не знаю ни температуру, ни давление",
        "Ни температуры, ни давления выяснить не получится",
        "Температура и давление мне не важны",
    ),
)
def test_coordinated_unknown_pipe_facts_are_recovered_as_a_group(
    group_evidence: str,
) -> None:
    payload = _new_pipe_payload()
    # A lossy model may emit one proposal for the whole group. The repair must
    # reject that ambiguous binding, then reconstruct every explicitly grouped
    # declarative fact rather than choosing one field.
    payload["constraints"] = [
        {
            "name": "operating_temperature_c",
            "value": None,
            "unit": None,
            "status": "unknown",
            "polarity": "required",
            "applies_to_product": 0,
            "evidence": group_evidence,
        }
    ]

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        f"Нужна полипропиленовая труба. {group_evidence}.",
        SessionState(session_id=f"coordinated-{len(group_evidence)}"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    expected_status = (
        "refused" if "не важны" in group_evidence else "unknown"
    )
    assert {
        item.name: item.status.value
        for item in result.understanding.constraints
    } == {
        "operating_temperature_c": expected_status,
        "operating_pressure_bar": expected_status,
    }
    assert not any(
        item.kind == "constraint_non_known_fact_unresolved"
        for item in result.understanding.ambiguities
    )
    assert "constraint_coordinated_non_known_fact_recovered" in (
        result.structural_repairs
    )


@pytest.mark.parametrize(
    ("fact_name", "message", "expected_status"),
    (
        (
            "operating_pressure_bar",
            "Давление сообщать не хочу, подберите по остальному",
            "refused",
        ),
        (
            "operating_pressure_bar",
            "С давлением вернусь позднее; пока покажите ориентировочно",
            "deferred",
        ),
        (
            "operating_temperature_c",
            "Температуру сейчас выяснить не получится",
            "unknown",
        ),
        (
            "operating_pressure_bar",
            "По давлению без разницы, подберите по остальному",
            "refused",
        ),
        (
            "operating_temperature_c",
            "Температура не принципиальна, покажите предварительно",
            "refused",
        ),
    ),
)
def test_pending_pipe_fact_status_survives_held_out_word_order(
    fact_name: str,
    message: str,
    expected_status: str,
) -> None:
    typed_state = _pipe_state_with_pending_fact(fact_name)
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "constraints": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        message,
        SessionState(
            session_id=f"pending-held-out-{fact_name}-{expected_status}",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [
        (item.name, item.status.value)
        for item in result.understanding.constraints
    ] == [(fact_name, expected_status)]
    assert result.understanding.answers_pending_question is True


def test_mixed_status_and_known_value_is_not_treated_as_unknown_group() -> None:
    evidence = "Температуру не знаю и давление 6 бар"
    payload = _new_pipe_payload()
    payload["constraints"] = [
        {
            "name": "operating_temperature_c",
            "value": None,
            "unit": None,
            "status": "unknown",
            "polarity": "required",
            "applies_to_product": 0,
            "evidence": evidence,
        }
    ]

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        f"Нужна полипропиленовая труба. {evidence}.",
        SessionState(session_id="mixed-non-known-and-known"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert any(
        item.kind == "constraint_non_known_fact_unresolved"
        for item in result.understanding.ambiguities
    )


def test_elliptic_unknown_rebinds_to_pending_pipe_pressure_only() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_pressure_bar")
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "products": [],
            "constraints": [
                {
                    "name": "operating_temperature_c",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "Не знаю",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    state = SessionState(
        session_id="pending-pipe-pressure",
        live_dialogue_state_v2=typed_state,
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret("Не знаю", state)

    assert result.status == "accepted"
    assert result.understanding is not None
    assert [item.name for item in result.understanding.constraints] == [
        "operating_pressure_bar"
    ]


def test_numeric_answer_to_pending_temperature_is_canonicalized_and_reduced() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_temperature_c")
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "products": [],
            "constraints": [
                {
                    "name": "operating_temperature_c",
                    "value": 80,
                    "unit": "градусов",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "Максимум 80 градусов",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
            "answers_pending_question": False,
        }
    )
    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Максимум 80 градусов",
        SessionState(
            session_id="pending-pipe-temperature-value",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.answers_pending_question is True
    fact = result.understanding.constraints[0]
    assert (fact.name, fact.value, fact.unit, fact.status.value) == (
        "operating_temperature_c",
        80,
        "C",
        "known",
    )
    assert "pending_numeric_contextual_unit_canonicalized" in (
        result.structural_repairs
    )

    reduction = reduce_dialogue_state(
        typed_state,
        result.understanding,
        TurnMetadata(turn_id="pending-temperature-value-turn"),
    )

    stored = next(
        item
        for item in reduction.state.constraints
        if item.name == "operating_temperature_c" and item.active
    )
    assert (stored.value, stored.unit, stored.status.value) == (80, "C", "known")
    assert "constraint_added" in {item.event_type for item in reduction.events}


def test_pending_temperature_uses_contextual_unit_at_exact_numeric_anchor() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_temperature_c")
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "constraints": [
                {
                    "name": "operating_temperature_c",
                    "value": 80,
                    # The model already returned the canonical family.  The
                    # evidence parser must not independently reinterpret the
                    # attached word "градусов" as an angle.
                    "unit": "C",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "Предельная температура около 80 градусов",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
            "answers_pending_question": False,
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Предельная температура около 80 градусов",
        SessionState(
            session_id="pending-temperature-contextual-evidence-unit",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.answers_pending_question is True
    fact = result.understanding.constraints[0]
    assert (fact.name, fact.value, fact.unit) == (
        "operating_temperature_c",
        80,
        "C",
    )
    assert "pending_numeric_contextual_evidence_disambiguated" in (
        result.structural_repairs
    )


def test_pending_temperature_does_not_accept_incompatible_pressure_unit() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_temperature_c")
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "constraints": [
                {
                    "name": "operating_temperature_c",
                    "value": 80,
                    "unit": "бар",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "80 бар",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "80 бар",
        SessionState(
            session_id="pending-temperature-wrong-unit",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert result.understanding.answers_pending_question is False
    assert "constraint_incompatible_unit_dropped" in result.structural_repairs


def test_terminal_pending_answer_survives_irrelevant_malformed_strategy() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_temperature_c")
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "products": [],
            # The model omitted the fact even though the exact typed pending
            # field and terminal status are grounded in this turn.
            "constraints": [],
            "ambiguities": [],
            "selection_controls": [],
            "selection_strategy": {
                "kind": "ambiguous",
                "evidence": "Температуру не знаю",
            },
            "answers_pending_question": False,
        }
    )
    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Температуру не знаю",
        SessionState(
            session_id="pending-temperature-malformed-strategy",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.kind.value == "standard"
    assert result.understanding.answers_pending_question is True
    assert [
        (item.name, item.status.value)
        for item in result.understanding.constraints
    ] == [("operating_temperature_c", "unknown")]
    assert "terminal_pending_answer_selection_strategy_normalized" in (
        result.structural_repairs
    )
    assert "terminal_pending_answer_confirmed" in result.structural_repairs

    reduction = reduce_dialogue_state(
        typed_state,
        result.understanding,
        TurnMetadata(turn_id="pending-temperature-unknown-turn"),
    )
    stored = next(
        item
        for item in reduction.state.constraints
        if item.name == "operating_temperature_c" and item.active
    )
    assert stored.status.value == "unknown"
    assert "constraint_marked_unknown" in {
        item.event_type for item in reduction.events
    }


def test_terminal_pending_fact_survives_continue_strategy_without_evidence() -> None:
    typed_state = _pipe_state_with_pending_fact("operating_pressure_bar")
    evidence = (
        "Давление тоже неизвестно, покажите что можно по остальным данным"
    )
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "products": [],
            "constraints": [
                {
                    "name": "operating_pressure_bar",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "Давление тоже неизвестно",
                }
            ],
            "ambiguities": [],
            "selection_controls": [
                {
                    "kind": "continue_with_confirmed_facts",
                    "evidence": None,
                }
            ],
            # This cannot authorize confirmed-facts-only selection because the
            # mandatory strategy evidence and matching typed control are absent.
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": None,
            },
            "answers_pending_question": False,
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        evidence,
        SessionState(
            session_id="pending-pressure-malformed-continue-strategy",
            live_dialogue_state_v2=typed_state,
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.kind.value == "standard"
    assert result.understanding.selection_controls == []
    assert result.understanding.answers_pending_question is True
    assert [
        (item.name, item.status.value)
        for item in result.understanding.constraints
    ] == [("operating_pressure_bar", "unknown")]
    assert "selection_strategy_safely_defaulted_to_standard" in (
        result.structural_repairs
    )
    assert "ungrounded_selection_control_dropped" in result.structural_repairs
    assert "terminal_pending_answer_confirmed" in result.structural_repairs

    reduction = reduce_dialogue_state(
        typed_state,
        result.understanding,
        TurnMetadata(turn_id="pending-pressure-unknown-malformed-strategy"),
    )
    stored = next(
        item
        for item in reduction.state.constraints
        if item.name == "operating_pressure_bar" and item.active
    )
    assert stored.status.value == "unknown"


def test_elliptic_pipe_service_values_are_closed_grounded_and_required() -> None:
    for evidence, value in (
        ("для холодной", "cold_water"),
        ("для горячей", "hot_water"),
        ("холодная", "cold_water"),
        ("горячая", "hot_water"),
    ):
        payload = _new_pipe_payload()
        payload["constraints"] = [
            {
                "name": "pipe_service",
                "value": value,
                "unit": None,
                "status": "known",
                "polarity": "required",
                "applies_to_product": 0,
                "evidence": evidence,
            }
        ]
        message = f"Нужна полипропиленовая труба, {evidence}."

        result = SemanticInterpreter(_SemanticClient(payload)).interpret(
            message,
            SessionState(session_id=f"pipe-service-{value}-{len(evidence)}"),
        )

        assert result.status == "accepted"
        assert result.understanding is not None
        fact = result.understanding.constraints[0]
        assert fact.name == "pipe_service"
        assert fact.value == value
        assert fact.polarity.value == "required"


@pytest.mark.parametrize(
    ("canonical_type", "evidence", "expected", "opposite"),
    (
        (
            "pipe",
            "Пойдёт на горячее водоснабжение",
            "hot_water",
            "cold_water",
        ),
        (
            "pex_pipe",
            "Для системы горячего водоснабжения",
            "hot_water",
            "cold_water",
        ),
        (
            "pipe",
            "Пойдёт на холодное водоснабжение",
            "cold_water",
            "hot_water",
        ),
        (
            "pex_pipe",
            "Для системы холодного водоснабжения",
            "cold_water",
            "hot_water",
        ),
    ),
)
def test_pending_pipe_service_long_forms_are_grounded_without_hot_cold_mixup(
    canonical_type: str,
    evidence: str,
    expected: str,
    opposite: str,
) -> None:
    typed_state = _pipe_state_with_pending_fact(
        "pipe_service",
        canonical_type=canonical_type,
    )

    def interpret(value: str, session_suffix: str):
        payload = _continue_control().model_dump(mode="json")
        payload.update(
            {
                "operation": "continue",
                "constraints": [
                    {
                        "name": "pipe_service",
                        "value": value,
                        "unit": None,
                        "status": "known",
                        "polarity": "required",
                        "applies_to_product": None,
                        "evidence": evidence,
                    }
                ],
                "selection_controls": [],
                "selection_strategy": {"kind": "standard", "evidence": None},
                "answers_pending_question": True,
            }
        )
        return SemanticInterpreter(_SemanticClient(payload)).interpret(
            evidence,
            SessionState(
                session_id=(
                    f"pending-{canonical_type}-{expected}-{session_suffix}"
                ),
                live_dialogue_state_v2=typed_state,
            ),
        )

    accepted = interpret(expected, "accepted")
    assert accepted.status == "accepted"
    assert accepted.understanding is not None
    assert [item.value for item in accepted.understanding.constraints] == [expected]

    rejected_opposite = interpret(opposite, "opposite")
    assert rejected_opposite.status == "accepted"
    assert rejected_opposite.understanding is not None
    assert rejected_opposite.understanding.constraints == []
    assert "constraint_closed_value_not_grounded_dropped" in (
        rejected_opposite.structural_repairs
    )


@pytest.mark.parametrize(
    ("canonical_type", "evidence", "value", "polarity"),
    (
        ("pipe", "армированная стекловолокном", "glass_fiber", "required"),
        ("pex_pipe", "предпочтительно PP-FIBER", "glass_fiber", "preferred"),
        ("pipe", "не алюминий", "aluminium", "excluded"),
        ("pex_pipe", "без армирования", "unreinforced", "required"),
    ),
)
def test_pipe_reinforcement_is_closed_grounded_with_explicit_polarity(
    canonical_type: str,
    evidence: str,
    value: str,
    polarity: str,
) -> None:
    payload = _new_pipe_payload(canonical_type=canonical_type)
    payload["constraints"] = [
        {
            "name": "reinforcement",
            "value": value,
            "unit": None,
            "status": "known",
            "polarity": polarity,
            "applies_to_product": 0,
            "evidence": evidence,
        }
    ]

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        f"Нужна полипропиленовая труба, {evidence}.",
        SessionState(
            session_id=f"reinforcement-{canonical_type}-{value}-{polarity}"
        ),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert len(result.understanding.constraints) == 1
    fact = result.understanding.constraints[0]
    assert fact.name == "reinforcement"
    assert fact.value == value
    assert fact.status.value == "known"
    assert fact.polarity.value == polarity
    assert "constraint_closed_value_not_grounded_dropped" not in (
        result.structural_repairs
    )


def test_schema_1_3_without_selection_strategy_is_rejected() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.pop("selection_strategy")

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Покажите по тем данным, что уже есть",
        SessionState(session_id="missing-selection-strategy"),
    )

    assert result.status == "rejected"
    assert "schema 1.3 requires selection_strategy" in (result.rejection_reason or "")


def test_legacy_schema_is_migrated_to_standard_strategy() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "schema_version": "1.2",
            "selection_controls": [],
        }
    )
    payload.pop("selection_strategy")

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Спасибо",
        SessionState(session_id="legacy-selection-strategy"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.schema_version == "1.3"
    assert result.understanding.selection_strategy is not None
    assert result.understanding.selection_strategy.kind.value == "standard"


def test_standard_strategy_with_control_is_rejected() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload["selection_strategy"] = {"kind": "standard", "evidence": None}

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Покажите по тем данным, что уже есть",
        SessionState(session_id="inconsistent-selection-strategy"),
    )

    assert result.status == "rejected"
    assert "standard strategy cannot contain selection controls" in (
        result.rejection_reason or ""
    )


def test_non_known_fact_without_declared_alias_fails_closed() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "полипропиленовая труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовая труба",
                }
            ],
            "constraints": [
                {
                    "name": "pipe_service",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "не знаю",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Нужна полипропиленовая труба, но параметр не знаю.",
        SessionState(session_id="ungrounded-non-known-fact"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert any(
        item.kind == "constraint_non_known_fact_unresolved"
        for item in result.understanding.ambiguities
    )


def test_multiple_non_known_fact_aliases_fail_closed() -> None:
    evidence = "Диаметр не знаю при назначении трубы для горячей воды"
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "труба",
                }
            ],
            "constraints": [
                {
                    "name": "pipe_service",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": evidence,
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        f"Нужна труба. {evidence}.",
        SessionState(session_id="ambiguous-non-known-facts"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []
    assert "constraint_non_known_fact_unresolved_dropped" in (
        result.structural_repairs
    )


def test_elliptic_non_known_fact_rebinds_only_to_typed_pending_question() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "continue",
            "acts": [],
            "products": [],
            "constraints": [
                {
                    "name": "pipe_service",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": None,
                    "evidence": "Не знаю",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )
    state = SessionState(
        session_id="pending-non-known-fact",
        live_dialogue_state_v2=_pipe_state(with_delivered_question=True),
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret("Не знаю", state)

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints[0].name == "diameter_mm"
    assert "constraint_non_known_fact_name_rebound" in result.structural_repairs


def test_generic_size_word_does_not_recover_pipe_diameter() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "труба",
                }
            ],
            "constraints": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Нужна труба. Размер упаковки не знаю.",
        SessionState(session_id="generic-size-is-not-diameter"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints == []


def test_product_type_without_vocabulary_keeps_legacy_non_known_fact() -> None:
    payload = _continue_control().model_dump(mode="json")
    payload.update(
        {
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "custom sensor",
                    "canonical_type": "custom_sensor",
                    "category": "other",
                    "role": "target",
                    "evidence": "custom sensor",
                }
            ],
            "constraints": [
                {
                    "name": "custom_mode",
                    "value": None,
                    "unit": None,
                    "status": "unknown",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "режим не знаю",
                }
            ],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
        }
    )

    result = SemanticInterpreter(_SemanticClient(payload)).interpret(
        "Нужен custom sensor, режим не знаю.",
        SessionState(session_id="legacy-non-known-compatibility"),
    )

    assert result.status == "accepted"
    assert result.understanding is not None
    assert result.understanding.constraints[0].name == "custom_mode"


def test_semantic_context_exposes_only_the_committed_typed_pending_question() -> None:
    state = SessionState(
        session_id="typed-pending-selection",
        live_dialogue_state_v2=_pipe_state(with_delivered_question=True),
    )

    context = semantic_context(state)["authoritative_dialogue_state_v2"]

    assert context is not None
    assert context["pending_decision_question"] == {
        "question_id": "question-diameter",
        "fact_name": "diameter_mm",
        "task_id": "task-pipe",
        "goal_id": "goal-pipe",
    }


def test_control_changes_missing_fact_question_to_preliminary_search() -> None:
    state_before = _pipe_state(with_delivered_question=True)
    contract = next(
        item for item in DEFAULT_CONTRACTS if item.contract_id == "pipe.ppr.v1"
    )
    task_before = state_before.tasks[0]
    before = assess_task_readiness(state_before, task_before, contract)
    assert before.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert before.recommended_question_fact == "pipe_service"

    reduction = reduce_dialogue_state(
        state_before,
        _continue_control(),
        TurnMetadata(turn_id="turn-2"),
    )
    task_after = reduction.state.tasks[0]
    after = assess_task_readiness(reduction.state, task_after, contract)
    plan = SellerPolicy().decide(
        reduction.state,
        readiness_assessments=(after,),
    )
    catalog_plan = plan_catalog_search(
        reduction.state,
        plan,
        (after,),
        build_catalog_snapshot(
            (
                Product(
                    sku="PIPE-20",
                    name="Труба PN 20, 20 MM",
                    category_path="Трубы",
                ),
                Product(
                    sku="PIPE-25",
                    name="Труба PN 20, 25 MM",
                    category_path="Трубы",
                ),
            )
        ),
        ProductContractRegistry(),
    )

    assert [item.event_type for item in reduction.events].count(
        "selection_control_registered"
    ) == 1
    assert reduction.state.selection_controls[0].task_id == task_after.task_id
    assert task_after.last_addressed_turn == reduction.state.turn_number
    assert after.status == ReadinessStatus.PRELIMINARY_READY
    assert after.recommended_question_fact is None
    assert "customer_requested_confirmed_facts_only" in after.reason_codes
    assert plan.primary.kind == NextActionKind.SHOW_PRELIMINARY_OPTIONS
    assert catalog_plan.search_plans[0].eligible_skus == ()
    assert set(catalog_plan.search_plans[0].unverified_skus) == {
        "PIPE-20",
        "PIPE-25",
    }
    assert all(
        "required_customer_fact_unavailable" in item.reason_codes
        for item in catalog_plan.search_plans[0].candidate_assessments
    )


def test_find_without_control_still_asks_decision_fact() -> None:
    state = _pipe_state()
    find_task = state.tasks[0].model_copy(update={"act": TaskAct.FIND})
    find_state = state.model_copy(update={"tasks": (find_task,)})
    contract = next(
        item for item in DEFAULT_CONTRACTS if item.contract_id == "pipe.ppr.v1"
    )

    assessment = assess_task_readiness(find_state, find_task, contract)

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "pipe_service"


@pytest.mark.parametrize("act", ["find", "select"])
def test_first_turn_control_allows_preliminary_for_discovery_acts(
    act: str,
) -> None:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
            "operation": "new",
            "acts": [act],
            "products": [
                {
                    "text": "полипропиленовая труба",
                    "canonical_type": "pipe",
                    "category": "pipes",
                    "role": "target",
                    "evidence": "полипропиленовая труба",
                }
            ],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [
                {
                    "kind": "continue_with_confirmed_facts",
                    "evidence": "по тем данным, что есть",
                }
            ],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts",
                "evidence": "по тем данным, что есть",
            },
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.98,
        }
    )
    reduction = reduce_dialogue_state(
        DialogueStateV2(),
        understanding,
        TurnMetadata(turn_id=f"first-turn-{act}-control"),
    )
    task = reduction.state.tasks[0]
    contract = next(
        item for item in DEFAULT_CONTRACTS if item.contract_id == "pipe.ppr.v1"
    )

    assessment = assess_task_readiness(reduction.state, task, contract)

    assert task.act.value == act
    assert reduction.state.selection_controls[0].task_id == task.task_id
    assert assessment.status == ReadinessStatus.PRELIMINARY_READY
    assert assessment.recommended_question_fact is None
    assert "customer_requested_confirmed_facts_only" in assessment.reason_codes


def test_select_without_control_still_asks_decision_fact() -> None:
    state = _pipe_state()
    contract = next(
        item for item in DEFAULT_CONTRACTS if item.contract_id == "pipe.ppr.v1"
    )

    assessment = assess_task_readiness(state, state.tasks[0], contract)

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "pipe_service"


@pytest.mark.parametrize("status", ["unknown", "refused", "deferred"])
def test_terminal_fact_only_suppresses_its_own_question(
    status: str,
) -> None:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
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
                    "value": None,
                    "unit": None,
                    "status": status,
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": f"напор {status}",
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.98,
        }
    )
    reduction = reduce_dialogue_state(
        DialogueStateV2(),
        understanding,
        TurnMetadata(turn_id=f"terminal-fact-{status}"),
    )
    task = reduction.state.tasks[0]
    contract = next(
        item
        for item in DEFAULT_CONTRACTS
        if item.contract_id == "pump.circulation.v1"
    )

    assessment = assess_task_readiness(reduction.state, task, contract)

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "diameter_mm"
    assert "diameter_mm" in assessment.missing_decision_facts
    assert "mounting_length_mm" in assessment.missing_decision_facts
    assert getattr(assessment, f"{status}_facts") == ("max_head_m",)
    fact = next(
        item
        for item in reduction.state.constraints
        if item.name == "max_head_m"
    )
    assert fact.value is None


def test_control_never_invents_or_relaxes_a_technical_fact() -> None:
    reduction = reduce_dialogue_state(
        _pipe_state(),
        _continue_control(),
        TurnMetadata(turn_id="turn-2"),
    )

    assert reduction.state.constraints == ()
    assert all(
        event.event_type
        not in {
            "constraint_added",
            "constraint_corrected",
            "constraint_marked_unknown",
            "constraint_refused",
            "constraint_deferred",
        }
        for event in reduction.events
    )
