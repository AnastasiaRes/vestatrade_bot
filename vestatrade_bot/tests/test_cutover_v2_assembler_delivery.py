from __future__ import annotations

from app.answer_v2.contracts import (
    AnswerPlan,
    AnswerPlanStatus,
    AnswerPlanningResult,
    AnswerSection,
    AnswerSectionKind,
    AnswerSourceSnapshot,
    AnswerValidationResult,
    CatalogAnswerProduct,
    NextStepKind,
    NextStepPlan,
    ProductPresentationPlan,
    ProductPresentationStatus,
    QuestionPlan,
    RenderedAnswer,
    RenderedAnswerResult,
    RenderedSegment,
    RenderedSegmentKind,
)
from app.catalog_v2.contracts import (
    CatalogPlanningResult,
    CatalogProductRole,
    ContractResolution,
    ContractResolutionStatus,
    ProductKind,
)
from app.cutover_v2.assembler import build_v2_turn_candidate
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    NextActionKind,
    ProgressState,
    ReductionResult,
    ShadowDeliveryStatus,
    TaskAct,
    TurnMetadata,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.dialogue_v2.reducer import record_response_delivery


def _answer_plan() -> AnswerPlan:
    product = ProductPresentationPlan(
        product_plan_id="product-1",
        sku="PIPE-20",
        name="Труба 20 мм",
        product_kind=ProductKind.PIPE,
        role=CatalogProductRole.BASE_PRODUCT,
        task_id="task-1",
        goal_id="goal-1",
        search_plan_id="search-1",
        status=ProductPresentationStatus.EXACT,
        source_ref_ids=("source-product",),
    )
    return AnswerPlan(
        plan_id="plan-1",
        turn_id="turn-1",
        turn_number=1,
        task_ids=("task-1",),
        goal_ids=("goal-1",),
        primary_action=NextActionKind.ANSWER_DIRECT_QUESTION,
        status=AnswerPlanStatus.READY,
        sections=(
            AnswerSection(
                section_id="products",
                kind=AnswerSectionKind.PRODUCTS,
                item_ids=("product-1",),
            ),
        ),
        products=(product,),
        next_step=NextStepPlan(
            next_step_id="next-1",
            kind=NextStepKind.WAIT_FOR_CUSTOMER,
            task_id="task-1",
        ),
        semantic_signature="signature-1",
    )


def _outcome() -> DialogueV2Outcome:
    state_before = DialogueStateV2()
    state_after = DialogueStateV2(
        turn_number=1,
        tasks=(
            CustomerTask(
                task_id="task-1",
                act=TaskAct.CHECK_PRICE,
                target_goal_id="goal-1",
                priority=0,
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
    )
    answer_plan = _answer_plan()
    rendered = RenderedAnswer(
        plan_id="plan-1",
        renderer="deterministic",
        segments=(
            RenderedSegment(
                segment_id="product-segment",
                kind=RenderedSegmentKind.PRODUCT,
                source_ids=("product-1",),
                text="Труба 20 мм, артикул PIPE-20 — 100 RUB, в наличии.",
                critical_literals=("PIPE-20", "100"),
            ),
        ),
        text="Труба 20 мм, артикул PIPE-20 — 100 RUB, в наличии.",
    )
    reduction = ReductionResult(
        state=state_after,
        progress=ProgressState(source_turn=1),
    )
    return DialogueV2Outcome(
        status="applied",
        state_before=state_before,
        state_after=state_after,
        reduction=reduction,
        catalog_planning=CatalogPlanningResult(
            status="planned",
            contract_resolutions=(
                ContractResolution(
                    task_id="task-1",
                    goal_id="goal-1",
                    status=ContractResolutionStatus.RESOLVED,
                    contract_id="pipe-v1",
                    product_kind=ProductKind.PIPE,
                ),
            ),
        ),
        answer_planning=AnswerPlanningResult(
            status="planned",
            answer_plan=answer_plan,
        ),
        response_rendering=RenderedAnswerResult(
            status="rendered",
            rendered_answer=rendered,
        ),
        grounding_validation=AnswerValidationResult(
            status="accepted",
            plan_id="plan-1",
            accepted_segment_ids=("product-segment",),
        ),
    )


def _snapshot(*, price=100.0, url="https://example.test/pipe-20"):
    return AnswerSourceSnapshot(
        source_revision="catalog-revision",
        products=(
            CatalogAnswerProduct(
                sku="PIPE-20",
                name="Труба 20 мм",
                product_kind=ProductKind.PIPE,
                role=CatalogProductRole.BASE_PRODUCT,
                price=price,
                currency="RUB",
                stock_status="в наличии",
                stock_qty=5,
                url=url,
                image_url="https://example.test/pipe-20.png",
            ),
        ),
    )


def test_assembler_selects_text_and_cards_from_one_grounded_plan() -> None:
    candidate = build_v2_turn_candidate(
        _outcome(),
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )
    assert candidate.eligible_for_delivery is True
    assert candidate.response is not None
    assert [item.sku for item in candidate.response.products] == ["PIPE-20"]
    assert candidate.response.products[0].price == 100
    assert candidate.response.products[0].image_url.endswith(".png")
    assert candidate.product_statuses == ("exact",)
    assert candidate.task_acts == (TaskAct.CHECK_PRICE,)
    assert candidate.product_kinds == (ProductKind.PIPE,)


def test_assembler_delivers_grounded_question_only_product_turn() -> None:
    outcome = _outcome()
    task = outcome.state_after.tasks[0].model_copy(update={"act": TaskAct.SELECT})
    question = QuestionPlan(
        question_id="question-diameter",
        task_id=task.task_id,
        fact_name="diameter_mm",
        decision_impact_code="diameter_changes_selection",
        source_ref_ids=("policy-diameter",),
    )
    next_step = NextStepPlan(
        next_step_id="next-question",
        kind=NextStepKind.ASK_DECISION_FACT,
        task_id=task.task_id,
        fact_name="diameter_mm",
    )
    plan = _answer_plan().model_copy(
        update={
            "primary_action": NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            "status": AnswerPlanStatus.READY,
            "products": (),
            "question": question,
            "next_step": next_step,
            "sections": (
                AnswerSection(
                    section_id="question",
                    kind=AnswerSectionKind.QUESTION,
                    item_ids=(question.question_id,),
                ),
                AnswerSection(
                    section_id="next",
                    kind=AnswerSectionKind.NEXT_STEP,
                    item_ids=(next_step.next_step_id,),
                ),
            ),
        }
    )
    rendered = RenderedAnswer(
        plan_id=plan.plan_id,
        renderer="deterministic",
        segments=(
            RenderedSegment(
                segment_id="question-segment",
                kind=RenderedSegmentKind.QUESTION,
                source_ids=(question.question_id,),
                text="Уточните диаметр подключения.",
            ),
            RenderedSegment(
                segment_id="next-segment",
                kind=RenderedSegmentKind.NEXT_STEP,
                source_ids=(next_step.next_step_id,),
                text="После этого продолжу подбор.",
            ),
        ),
        text="Уточните диаметр подключения.\nПосле этого продолжу подбор.",
    )
    outcome = outcome.model_copy(
        update={
            "state_after": outcome.state_after.model_copy(update={"tasks": (task,)}),
            "answer_planning": AnswerPlanningResult(
                status="planned",
                answer_plan=plan,
            ),
            "response_rendering": RenderedAnswerResult(
                status="rendered",
                rendered_answer=rendered,
            ),
            "grounding_validation": AnswerValidationResult(
                status="accepted",
                plan_id=plan.plan_id,
                accepted_segment_ids=("question-segment", "next-segment"),
            ),
        }
    )

    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-question-only",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.response is not None
    assert candidate.response.products == []
    assert candidate.answer_status == AnswerPlanStatus.READY


def test_assembler_does_not_require_product_contract_for_delivery_task() -> None:
    outcome = _outcome()
    stock_task = outcome.state_after.tasks[0].model_copy(
        update={"act": TaskAct.CHECK_STOCK}
    )
    delivery_task = CustomerTask(
        task_id="task-delivery",
        act=TaskAct.CHECK_DELIVERY,
        target_goal_id="goal-1",
        priority=1,
        source="semantic_interpreter",
        source_turn=1,
    )
    state_after = outcome.state_after.model_copy(
        update={"tasks": (stock_task, delivery_task)}
    )
    plan = _answer_plan().model_copy(
        update={"task_ids": (stock_task.task_id, delivery_task.task_id)}
    )
    outcome = outcome.model_copy(
        update={
            "state_after": state_after,
            "answer_planning": AnswerPlanningResult(
                status="planned",
                answer_plan=plan,
            ),
        }
    )

    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is True
    assert candidate.contracts_resolved is True
    assert candidate.task_acts == (TaskAct.CHECK_STOCK, TaskAct.CHECK_DELIVERY)
    assert "not_all_answer_tasks_have_contracts" not in candidate.rejection_reason_codes


def test_non_catalogue_task_inherits_typed_goal_kind_for_rollout_matching() -> None:
    for act in (TaskAct.EXPLAIN, TaskAct.CHECK_DELIVERY):
        outcome = _outcome()
        task = outcome.state_after.tasks[0].model_copy(update={"act": act})
        outcome = outcome.model_copy(
            update={
                "state_after": outcome.state_after.model_copy(
                    update={"tasks": (task,)}
                ),
                "catalog_planning": CatalogPlanningResult(
                    status="planned",
                    contract_resolutions=(
                        ContractResolution(
                            task_id=task.task_id,
                            goal_id=task.target_goal_id,
                            status=ContractResolutionStatus.UNSUPPORTED,
                            product_kind=ProductKind.PIPE,
                            reason_codes=("act_does_not_require_catalogue_contract",),
                        ),
                    ),
                ),
            }
        )

        candidate = build_v2_turn_candidate(
            outcome,
            _snapshot(),
            session_id="session",
            turn_id=f"turn-{act.value}",
        )

        assert candidate.eligible_for_delivery is True
        assert candidate.contracts_resolved is True
        assert candidate.product_kinds == (ProductKind.PIPE,)
        assert "product_contract_unsupported" not in candidate.rejection_reason_codes


def test_assembler_still_fails_closed_when_mixed_turn_product_task_is_unresolved() -> None:
    outcome = _outcome()
    delivery_task = CustomerTask(
        task_id="task-delivery",
        act=TaskAct.CHECK_DELIVERY,
        target_goal_id="goal-1",
        priority=1,
        source="semantic_interpreter",
        source_turn=1,
    )
    state_after = outcome.state_after.model_copy(
        update={"tasks": (*outcome.state_after.tasks, delivery_task)}
    )
    plan = _answer_plan().model_copy(
        update={"task_ids": ("task-1", delivery_task.task_id)}
    )
    outcome = outcome.model_copy(
        update={
            "state_after": state_after,
            "answer_planning": AnswerPlanningResult(
                status="planned",
                answer_plan=plan,
            ),
            "catalog_planning": CatalogPlanningResult(status="planned"),
        }
    )

    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is False
    assert candidate.contracts_resolved is False
    assert "product_contract_resolution_missing" in candidate.rejection_reason_codes
    assert "not_all_answer_tasks_have_contracts" in candidate.rejection_reason_codes


def test_assembler_rejects_product_when_public_card_fact_is_missing() -> None:
    candidate = build_v2_turn_candidate(
        _outcome(),
        _snapshot(price=None),
        session_id="session",
        turn_id="turn-1",
    )
    assert candidate.eligible_for_delivery is False
    assert candidate.response is None
    assert "presented_product_price_missing" in candidate.rejection_reason_codes


def test_assembler_rejects_rejected_grounding() -> None:
    outcome = _outcome().model_copy(
        update={
            "grounding_validation": AnswerValidationResult(
                status="rejected",
                plan_id="plan-1",
                reason_codes=("invented_literal",),
            )
        }
    )
    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )
    assert candidate.eligible_for_delivery is False
    assert "grounding_not_accepted" in candidate.rejection_reason_codes


def test_assembler_never_delivers_boundary_or_unsupported_answer_plan() -> None:
    for status in (
        AnswerPlanStatus.BOUNDARY,
        AnswerPlanStatus.UNSUPPORTED,
        AnswerPlanStatus.REJECTED,
    ):
        outcome = _outcome()
        answer_plan = _answer_plan().model_copy(update={"status": status})
        outcome = outcome.model_copy(
            update={
                "answer_planning": AnswerPlanningResult(
                    status="planned",
                    answer_plan=answer_plan,
                )
            }
        )

        candidate = build_v2_turn_candidate(
            outcome,
            _snapshot(),
            session_id="session",
            turn_id="turn-1",
        )

        assert candidate.eligible_for_delivery is False
        assert candidate.response is None
        assert (
            f"answer_plan_status_{status.value}_not_deliverable"
            in candidate.rejection_reason_codes
        )


def test_assembler_requires_rendered_response_to_stay_below_12k() -> None:
    outcome = _outcome()
    rendering = outcome.response_rendering
    assert rendering is not None and rendering.rendered_answer is not None
    rendered = rendering.rendered_answer.model_copy(update={"text": "x" * 12_000})
    outcome = outcome.model_copy(
        update={
            "response_rendering": RenderedAnswerResult(
                status="rendered",
                rendered_answer=rendered,
            )
        }
    )

    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is False
    assert candidate.response is None
    assert "rendered_answer_length_limit_exceeded" in candidate.rejection_reason_codes


def test_assembler_preserves_unresolved_contract_reason_codes() -> None:
    outcome = _outcome()
    outcome = outcome.model_copy(
        update={
            "catalog_planning": CatalogPlanningResult(
                status="planned",
                contract_resolutions=(
                    ContractResolution(
                        task_id="task-1",
                        goal_id="goal-1",
                        status=ContractResolutionStatus.UNSUPPORTED,
                        product_kind=ProductKind.UNSUPPORTED,
                        reason_codes=("no_product_contract_for_goal",),
                    ),
                ),
            )
        }
    )
    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is False
    assert "product_contract_unsupported" in candidate.rejection_reason_codes
    assert "no_product_contract_for_goal" in candidate.rejection_reason_codes


def test_assembler_explains_missing_contract_resolution() -> None:
    outcome = _outcome().model_copy(
        update={"catalog_planning": CatalogPlanningResult(status="planned")}
    )
    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is False
    assert "product_contract_resolution_missing" in candidate.rejection_reason_codes
    assert "not_all_answer_tasks_have_contracts" in candidate.rejection_reason_codes


def test_assembler_names_resolved_contract_without_id_precisely() -> None:
    outcome = _outcome()
    outcome = outcome.model_copy(
        update={
            "catalog_planning": CatalogPlanningResult(
                status="planned",
                contract_resolutions=(
                    ContractResolution(
                        task_id="task-1",
                        goal_id="goal-1",
                        status=ContractResolutionStatus.RESOLVED,
                        product_kind=ProductKind.RADIATOR,
                    ),
                ),
            )
        }
    )

    candidate = build_v2_turn_candidate(
        outcome,
        _snapshot(),
        session_id="session",
        turn_id="turn-1",
    )

    assert candidate.eligible_for_delivery is False
    assert "product_contract_id_missing" in candidate.rejection_reason_codes
    assert "product_contract_resolved" not in candidate.rejection_reason_codes


def test_delivery_reducer_separates_shadow_and_committed_history() -> None:
    state = DialogueStateV2(
        turn_number=1,
        answer_plan_summary=None,
    )
    result = record_response_delivery(
        state,
        TurnMetadata(turn_id="turn-1"),
        plan_id="plan-1",
        response_digest="digest",
        delivery_id="delivery-1",
        live_epoch_id="epoch-1",
    )
    assert state.live_epoch_id is None
    assert result.state.live_epoch_id == "epoch-1"
    assert result.state.response_delivery_history[0].status == "committed_to_session"
    assert [event.event_type for event in result.events] == [
        "v2_live_epoch_started",
        "response_selected_for_delivery",
        "response_commit_succeeded",
    ]


def test_delivery_reducer_marks_matching_plan_committed() -> None:
    plan = _answer_plan()
    from app.dialogue_v2.contracts import AnswerPlanSummary

    state = DialogueStateV2(
        turn_number=1,
        answer_plan_summary=AnswerPlanSummary(
            plan_id=plan.plan_id,
            semantic_signature=plan.semantic_signature,
            task_ids=plan.task_ids,
            primary_action=plan.primary_action,
            next_step_kind=plan.next_step.kind.value,
            validation_status="accepted",
            delivery_status=ShadowDeliveryStatus.SHADOW_NOT_DELIVERED,
            source_turn=1,
        ),
    )
    result = record_response_delivery(
        state,
        TurnMetadata(turn_id="turn-1"),
        plan_id="plan-1",
        response_digest="digest",
        delivery_id="delivery-1",
        live_epoch_id="epoch-1",
    )
    assert result.state.answer_plan_summary is not None
    assert (
        result.state.answer_plan_summary.delivery_status
        == ShadowDeliveryStatus.COMMITTED_TO_SESSION
    )
