from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from pydantic import ValidationError

from app.answer_v2.contracts import AnswerPlanStatus
from app.catalog_v2.contracts import ProductKind
from app.cutover_v2.contracts import (
    EarlyControlOutcome,
    EarlyControlResult,
    ExecutionMode,
    MigrationCell,
    MigrationRegistry,
    ResponseOwner,
    RolloutStage,
    V2TurnCandidate,
)
from app.cutover_v2.parity import assess_response_parity
from app.cutover_v2.policy import CutoverRuntime, arbitrate_turn, decide_cutover
from app.cutover_v2.registry import (
    build_migration_readiness_matrix,
    default_registry,
    load_registry,
)
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintStatus,
    ConstraintStrength,
    DialogueStateV2,
    NextActionKind,
    TaskAct,
)
from app.models import ChatProductSummary, ChatResponse, SessionState


def _response(sku: str = "SKU-1") -> ChatResponse:
    return ChatResponse(
        session_id="session",
        answer="Подтверждённая цена — 100 RUB.",
        products=[
            ChatProductSummary(
                sku=sku,
                name="Труба",
                price=100,
                currency="RUB",
                stock_status="в наличии",
                url="https://example.test/sku-1",
            )
        ],
    )


def _candidate(**updates) -> V2TurnCandidate:
    response = _response()
    payload = dict(
        turn_id="turn-1",
        response=response,
        state_before=DialogueStateV2(),
        state_after=DialogueStateV2(turn_number=1),
        answer_plan_id="plan-1",
        rendered_answer_id="plan-1",
        source_revision="catalog-rev",
        catalog_revision="catalog-rev",
        validation_status="accepted",
        response_digest="digest",
        task_acts=(TaskAct.CHECK_PRICE,),
        product_kinds=(ProductKind.PIPE,),
        contract_versions=("1.0",),
        answer_status=AnswerPlanStatus.READY,
        next_action=NextActionKind.ANSWER_DIRECT_QUESTION,
        product_statuses=("exact",),
        semantic_accepted=True,
        contracts_resolved=True,
        eligible_for_delivery=True,
    )
    payload.update(updates)
    return V2TurnCandidate(**payload)


def _cell(**updates) -> MigrationCell:
    payload = dict(
        cell_id="exact-facts-canary",
        task_acts=(TaskAct.CHECK_PRICE, TaskAct.CHECK_STOCK, TaskAct.GET_LINK),
        product_kinds=(ProductKind.PIPE,),
        stage=RolloutStage.INTERNAL_CANARY,
        canary_percent=5,
        gate_artifact_ref="gate-v1",
    )
    payload.update(updates)
    return MigrationCell(**payload)


def _registry(cell: MigrationCell | None = None) -> MigrationRegistry:
    return MigrationRegistry(
        registry_id="test",
        revision="revision-1",
        cells=(cell or _cell(),),
    )


def _runtime(**updates) -> CutoverRuntime:
    payload = dict(
        routing_enabled=True,
        shadow_compare_enabled=True,
        live_delivery_enabled=True,
        internal_canary_enabled=True,
        internal_canary_percent=5,
    )
    payload.update(updates)
    return CutoverRuntime(**payload)


def _eligible_fingerprint(registry_revision: str = "revision-1") -> str:
    for index in range(10_000):
        value = f"fingerprint-{index}"
        digest = hashlib.sha256(
            f"{value}:{registry_revision}".encode("utf-8")
        ).hexdigest()
        if int(digest[:8], 16) % 100 < 5:
            return value
    raise AssertionError("no deterministic canary bucket found")


def _fingerprint_in_bucket_range(low: int, high: int) -> str:
    for index in range(10_000):
        value = f"bounded-fingerprint-{index}"
        digest = hashlib.sha256(f"{value}:revision-1".encode()).hexdigest()
        bucket = int(digest[:8], 16) % 100
        if low <= bucket < high:
            return value
    raise AssertionError("no fingerprint in requested range")


def test_builtin_registry_is_shadow_only() -> None:
    registry = default_registry()
    assert registry.cells
    assert {cell.stage for cell in registry.cells} == {RolloutStage.SHADOW}
    assert all(cell.canary_percent == 0 for cell in registry.cells)
    matrix = build_migration_readiness_matrix(
        registry,
        catalog_revision="catalog-rev",
    )
    assert matrix
    assert not any(item.canary_eligible for item in matrix)
    assert all("rollout_stage_shadow" in item.blocked_reason_codes for item in matrix)


def test_internal_canary_requires_bounded_percent_and_gate() -> None:
    with pytest.raises(ValidationError):
        _cell(gate_artifact_ref=None)
    with pytest.raises(ValidationError):
        _cell(canary_percent=6)
    with pytest.raises(ValidationError):
        _cell(product_kinds=(ProductKind.UNSUPPORTED,))
    matrix = build_migration_readiness_matrix(
        _registry(),
        catalog_revision="catalog-rev",
    )
    assert matrix
    assert all(item.canary_eligible for item in matrix)


def test_invalid_registry_fails_closed(tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("not json", encoding="utf-8")
    result = load_registry(path)
    assert result.valid is False
    assert result.cells == ()
    assert result.error


def test_cutover_policy_is_deterministic_and_canary_is_sticky() -> None:
    fingerprint = _eligible_fingerprint()
    first = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(),
        session_fingerprint=fingerprint,
    )
    second = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(),
        session_fingerprint=fingerprint,
    )
    assert first == second
    assert first.owner_candidate == ResponseOwner.V2
    assert first.execution_mode == ExecutionMode.V2_INTERNAL_CANARY
    sticky = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(
            is_existing_session=True,
            sticky_cell_id=first.cell_id,
            sticky_assignment_id=first.sticky_assignment_id,
        ),
        session_fingerprint=fingerprint,
    )
    assert sticky.owner_candidate == ResponseOwner.V2
    assert sticky.sticky_assignment_id == first.sticky_assignment_id


def test_internal_canary_can_explicitly_admit_typed_recommend_one() -> None:
    cell = _cell(
        task_acts=(TaskAct.SELECT,),
        product_kinds=(ProductKind.CIRCULATION_PUMP,),
        allowed_next_actions=(NextActionKind.RECOMMEND_ONE,),
    )
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(
            task_acts=(TaskAct.SELECT,),
            product_kinds=(ProductKind.CIRCULATION_PUMP,),
            next_action=NextActionKind.RECOMMEND_ONE,
        ),
        _registry(cell),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )

    assert decision.owner_candidate == ResponseOwner.V2
    assert decision.execution_mode == ExecutionMode.V2_INTERNAL_CANARY


@pytest.mark.parametrize(
    ("early", "owner", "mode"),
    [
        (
            EarlyControlResult(
                outcome=EarlyControlOutcome.EMERGENCY_RESPONSE,
                reason_codes=("water_emergency",),
            ),
            ResponseOwner.SAFETY,
            ExecutionMode.SAFETY_INTERCEPT,
        ),
        (
            EarlyControlResult(
                outcome=EarlyControlOutcome.PII_CONTROL,
                reason_codes=("pii",),
            ),
            ResponseOwner.LEGACY,
            ExecutionMode.LEGACY_ONLY,
        ),
    ],
)
def test_early_control_preempts_canary(early, owner, mode) -> None:
    decision = decide_cutover(
        early,
        _candidate(),
        _registry(),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == owner
    assert decision.execution_mode == mode


@pytest.mark.parametrize(
    ("runtime", "reason"),
    [
        (_runtime(force_legacy=True), "force_legacy_kill_switch"),
        (_runtime(registry_valid=False), "migration_registry_invalid"),
        (_runtime(live_delivery_enabled=False), "v2_live_delivery_disabled"),
    ],
)
def test_flags_and_kill_switch_fail_closed(runtime, reason) -> None:
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        runtime,
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert reason in decision.reason_codes


def test_existing_session_without_assignment_is_not_switched_mid_task() -> None:
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(is_existing_session=True),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert "existing_session_not_canary_eligible" in decision.reason_codes


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(semantic_accepted=False, eligible_for_delivery=False),
        _candidate(contracts_resolved=False, eligible_for_delivery=False),
        _candidate(task_acts=(TaskAct.CHECK_PRICE, TaskAct.SELECT)),
        _candidate(product_kinds=(ProductKind.PIPE, ProductKind.PUMP)),
        _candidate(product_statuses=("unverified",)),
        _candidate(pending_command_ids=("command-1",)),
    ],
)
def test_unsupported_or_unverified_candidate_remains_legacy(candidate) -> None:
    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        _registry(),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY


def test_arbitration_selects_one_complete_owner() -> None:
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    arbitration = arbitrate_turn(decision, _candidate())
    assert arbitration.response_owner == ResponseOwner.V2
    assert arbitration.response is not None
    assert arbitration.selected_state is not None
    assert arbitration.fallback_required is False


def test_external_side_effect_forbids_fallback() -> None:
    candidate = _candidate(
        eligible_for_delivery=False,
        external_side_effect_started=True,
        rejection_reason_codes=("late_failure",),
    )
    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        _registry(),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.fallback_allowed is False
    arbitration = arbitrate_turn(decision, candidate)
    assert arbitration.fallback_required is False
    assert arbitration.external_fallback_forbidden is True


def test_tampered_sticky_assignment_fails_closed() -> None:
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(
            is_existing_session=True,
            sticky_cell_id="exact-facts-canary",
            sticky_assignment_id="tampered-assignment",
        ),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert "sticky_assignment_invalid" in decision.reason_codes


def test_existing_sticky_task_is_not_switched_when_percentage_decreases() -> None:
    fingerprint = _fingerprint_in_bucket_range(1, 5)
    first = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(internal_canary_percent=5),
        session_fingerprint=fingerprint,
    )
    assert first.owner_candidate == ResponseOwner.V2
    continued = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(),
        _runtime(
            internal_canary_percent=1,
            is_existing_session=True,
            sticky_cell_id=first.cell_id,
            sticky_assignment_id=first.sticky_assignment_id,
        ),
        session_fingerprint=fingerprint,
    )
    assert continued.owner_candidate == ResponseOwner.V2
    assert continued.sticky_assignment_id == first.sticky_assignment_id


def test_supported_typed_ambiguity_can_stay_in_v2_for_clarification() -> None:
    response = ChatResponse(
        session_id="session",
        answer="Нужно уточнить один параметр, который меняет решение.",
    )
    candidate = _candidate(
        response=response,
        response_digest="clarification-digest",
        answer_status=AnswerPlanStatus.PARTIAL,
        next_action=NextActionKind.ASK_DECISION_CHANGING_QUESTION,
        product_statuses=(),
    )
    cell = _cell(
        allowed_answer_statuses=(AnswerPlanStatus.PARTIAL,),
        allowed_next_actions=(NextActionKind.ASK_DECISION_CHANGING_QUESTION,),
        require_single_exact_product=False,
    )
    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        _registry(cell),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.V2
    assert decision.execution_mode == ExecutionMode.V2_INTERNAL_CANARY


def test_future_primary_registry_cannot_enable_public_traffic_in_stage6a() -> None:
    primary = _cell(
        stage=RolloutStage.V2_PRIMARY,
        canary_percent=0,
        gate_artifact_ref="future-stage-evidence",
    )
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(primary),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert "v2_primary_not_enabled_in_stage6a" in decision.reason_codes


def test_primary_cell_requires_explicit_local_preview_gate() -> None:
    primary = _cell(
        stage=RolloutStage.V2_PRIMARY,
        canary_percent=0,
        gate_artifact_ref="local-preview-evidence",
    )
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(primary),
        _runtime(local_preview_enabled=True),
        session_fingerprint=_eligible_fingerprint(),
    )

    assert decision.owner_candidate == ResponseOwner.V2
    assert decision.execution_mode == ExecutionMode.V2_PRIMARY
    assert decision.eligible is True
    assert "approved_local_v2_primary_preview" in decision.reason_codes


@pytest.mark.parametrize(
    ("runtime_updates", "cell_updates"),
    [
        ({"routing_enabled": False}, {}),
        ({"live_delivery_enabled": False}, {}),
        ({"registry_valid": False}, {}),
        ({"external_actions_enabled": True}, {}),
        ({}, {"external_actions_allowed": True}),
    ],
)
def test_local_primary_preview_remains_fail_closed(
    runtime_updates: dict[str, bool],
    cell_updates: dict[str, bool],
) -> None:
    primary = _cell(
        stage=RolloutStage.V2_PRIMARY,
        canary_percent=0,
        gate_artifact_ref="local-preview-evidence",
        **cell_updates,
    )
    decision = decide_cutover(
        EarlyControlResult(),
        _candidate(),
        _registry(primary),
        _runtime(local_preview_enabled=True, **runtime_updates),
        session_fingerprint=_eligible_fingerprint(),
    )

    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert decision.eligible is False


def test_candidate_without_complete_delivery_proof_fails_closed() -> None:
    candidate = _candidate(
        validation_status="rejected",
        response_digest=None,
        eligible_for_delivery=True,
    )
    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        _registry(),
        _runtime(),
        session_fingerprint=_eligible_fingerprint(),
    )
    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert "v2_candidate_delivery_proof_incomplete" in decision.reason_codes


def test_parity_compares_outcomes_not_literal_wording() -> None:
    legacy = _response()
    v2_response = _response().model_copy(update={"answer": "Цена товара: 100 RUB."})
    candidate = _candidate(response=v2_response)
    result = assess_response_parity(legacy, candidate)
    assert result.status == "parity"
    assert "answer_text" not in result.compared_dimensions


def test_parity_marks_foreign_sku_as_gate_blocking() -> None:
    candidate = _candidate(response=_response("OTHER-SKU"))
    result = assess_response_parity(_response(), candidate)
    assert result.status == "regression"
    assert result.severity == "p1"
    assert "legacy_v2_product_set_differs" in result.gate_blocking_reason_codes


def test_parity_marks_changed_catalog_fact_for_same_sku_as_p0() -> None:
    changed = _response().model_copy(deep=True)
    changed.products[0].price = 999
    result = assess_response_parity(_response(), _candidate(response=changed))
    assert result.status == "regression"
    assert result.severity == "p0"
    assert "legacy_v2_price_differs_for_same_sku" in result.gate_blocking_reason_codes


def test_parity_marks_hard_constraint_regression_as_p0() -> None:
    state = DialogueStateV2(
        turn_number=1,
        constraints=(
            ConstraintFactV2(
                fact_id="fact-1",
                name="diameter_mm",
                value=20,
                status=ConstraintStatus.KNOWN,
                strength=ConstraintStrength.HARD,
                evidence="20 мм",
                source="semantic_interpreter",
                confidence=1.0,
                source_turn=1,
            ),
        ),
    )
    candidate = _candidate(state_after=state)
    legacy_state = SessionState(
        session_id="session",
        slots={"diameter_mm": 25},
    )
    result = assess_response_parity(_response(), candidate, legacy_state)
    assert result.status == "regression"
    assert result.severity == "p0"
    assert "legacy_v2_hard_constraint_differs" in result.gate_blocking_reason_codes


def test_parity_marks_foreign_product_kind_as_p0() -> None:
    candidate = _candidate(
        response_product_kinds=(ProductKind.PUMP,),
    )
    result = assess_response_parity(
        _response(),
        candidate,
        legacy_product_kinds={"SKU-1": ProductKind.PIPE},
    )
    assert result.status == "regression"
    assert result.severity == "p0"
    assert "legacy_v2_product_kind_differs" in result.gate_blocking_reason_codes


def test_policy_source_has_no_raw_message_or_regex_logic() -> None:
    source = inspect.getsource(decide_cutover)
    assert "raw_text" not in source
    assert "message" not in source
    assert "re." not in source


def test_registry_json_contains_no_sku_specific_key() -> None:
    payload = json.dumps(default_registry().model_dump(mode="json"), sort_keys=True)
    assert '"sku"' not in payload.casefold()
