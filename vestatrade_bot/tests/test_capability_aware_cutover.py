from __future__ import annotations

import json

from app.answer_v2.contracts import AnswerPlanStatus
from app.answer_v2.contracts import AnswerSourceSnapshot
from app.catalog_v2.contracts import ProductKind
from app.cutover_v2.capability_boundary import (
    build_v2_uncovered_capability_boundary_candidate,
)
from app.cutover_v2.capability_registry import resolve_capability_coverage
from app.cutover_v2.contracts import (
    CapabilityCoverageStatus,
    CapabilityOwner,
    CapabilityTurnContext,
    EarlyControlResult,
    ExecutionMode,
    ResponseOwner,
    V2TurnCandidate,
)
from app.cutover_v2.policy import CutoverRuntime, decide_cutover
from app.cutover_v2.registry import default_registry, load_registry
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TaskAct
from app.models import ChatResponse


def _candidate(
    *,
    task_act: TaskAct = TaskAct.SELECT,
    next_action: NextActionKind = NextActionKind.SHOW_PRELIMINARY_OPTIONS,
    product_kind: ProductKind = ProductKind.PIPE,
) -> V2TurnCandidate:
    response = ChatResponse(session_id="session", answer="Проверенный ответ.")
    return V2TurnCandidate(
        turn_id="turn-1",
        response=response,
        state_before=DialogueStateV2(),
        state_after=DialogueStateV2(turn_number=1),
        answer_plan_id="plan-1",
        rendered_answer_id="render-1",
        source_revision="source-1",
        catalog_revision="source-1",
        validation_status="accepted",
        response_digest="digest-1",
        task_acts=(task_act,),
        product_kinds=(product_kind,),
        contract_versions=("1.0",),
        answer_status=AnswerPlanStatus.READY,
        next_action=next_action,
        semantic_accepted=True,
        contracts_resolved=True,
        eligible_for_delivery=True,
    )


def _runtime() -> CutoverRuntime:
    return CutoverRuntime(
        routing_enabled=True,
        live_delivery_enabled=True,
        public_primary_enabled=True,
    )


def test_builtin_registry_declares_versioned_capability_owners() -> None:
    registry = default_registry()

    assert registry.capability_registry_version == "1.0"
    assert registry.capabilities
    assert len({item.capability_id for item in registry.capabilities}) == len(
        registry.capabilities
    )
    assert any(
        item.owner == CapabilityOwner.V2 and item.capability_id == "v2.catalogue_turn"
        for item in registry.capabilities
    )


def test_legacy_external_registry_inherits_versioned_capabilities(tmp_path) -> None:
    legacy_registry = default_registry().model_dump(mode="json")
    legacy_registry.pop("capability_registry_version")
    legacy_registry.pop("capabilities")
    path = tmp_path / "legacy-registry.json"
    path.write_text(json.dumps(legacy_registry), encoding="utf-8")

    loaded = load_registry(path)

    assert loaded.valid is True
    assert loaded.capability_registry_version == "1.0"
    assert loaded.capabilities == default_registry().capabilities
    assert any(
        item.owner == CapabilityOwner.LEGACY
        and item.capability_id == "legacy.item_list"
        for item in loaded.capabilities
    )


def test_delivery_ready_v2_candidate_remains_v2_owned() -> None:
    registry = default_registry()
    coverage = resolve_capability_coverage(
        "Нужна ППР труба 25 мм",
        _candidate(),
        registry,
    )

    assert coverage.status == CapabilityCoverageStatus.V2_READY
    assert coverage.owner == CapabilityOwner.V2
    assert coverage.enforced is True


def test_item_list_preempts_a_generic_v2_selection_candidate() -> None:
    registry = default_registry()
    coverage = resolve_capability_coverage(
        "Угольник PPR 20 — 30 шт, муфта PPR 25 — 5 шт",
        _candidate(),
        registry,
    )

    assert coverage.status == CapabilityCoverageStatus.LEGACY_READY
    assert coverage.owner == CapabilityOwner.LEGACY
    assert coverage.capability_ids == ("legacy.item_list",)
    assert coverage.enforced is True


def test_active_pump_pending_answer_cannot_be_preempted_by_legacy_item_list() -> None:
    coverage = resolve_capability_coverage(
        "При работе вода опускается до 12 метров, по участку 35 метров "
        "трубы ПНД 32, одновременно два разбрызгивателя.",
        _candidate(product_kind=ProductKind.PUMP),
        default_registry(),
        turn_context=CapabilityTurnContext(
            active_goal_canonical_type="borehole_pump",
            pending_question_fact="dynamic_water_level_m",
        ),
    )

    assert coverage.status == CapabilityCoverageStatus.V2_READY
    assert coverage.owner == CapabilityOwner.V2
    assert "legacy_item_list_blocked_by_active_engineering_answer" in (
        coverage.reason_codes
    )


def test_household_problem_uses_the_existing_legacy_problem_frame() -> None:
    coverage = resolve_capability_coverage(
        "Из слива в душе пахнет канализацией",
        _candidate(),
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.LEGACY_READY
    assert coverage.owner == CapabilityOwner.LEGACY
    assert coverage.capability_ids == ("legacy.problem_frame",)


def test_engineering_norm_is_legacy_ready_but_hydraulic_design_stays_v2_boundary() -> None:
    registry = default_registry()
    norm = resolve_capability_coverage(
        "Какой уклон нужен для канализационной трубы 110 мм?",
        _candidate(),
        registry,
    )
    boundary = resolve_capability_coverage(
        "Рассчитайте гидравлическое сопротивление системы отопления дома",
        _candidate(
            task_act=TaskAct.OTHER,
            next_action=NextActionKind.STATE_CAPABILITY_BOUNDARY,
        ),
        registry,
    )

    assert norm.status == CapabilityCoverageStatus.LEGACY_READY
    assert norm.capability_ids == ("legacy.engineering_norm",)
    assert boundary.status == CapabilityCoverageStatus.V2_READY
    assert boundary.owner == CapabilityOwner.V2
    assert boundary.capability_ids == ("v2.engineering_boundary",)


def test_verified_v2_commerce_answer_is_not_preempted_by_legacy_topic_detection() -> None:
    coverage = resolve_capability_coverage(
        "Какие у вас условия доставки?",
        _candidate(
            task_act=TaskAct.OTHER,
            next_action=NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
        ),
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.V2_READY
    assert coverage.owner == CapabilityOwner.V2
    assert coverage.capability_ids == ("v2.verified_commerce",)


def test_partial_v2_commerce_collection_stays_with_mature_legacy_topic() -> None:
    coverage = resolve_capability_coverage(
        "Где мой заказ?",
        _candidate(
            task_act=TaskAct.ORDER_STATUS,
            next_action=NextActionKind.COLLECT_COMMERCE_FACT,
        ),
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.LEGACY_READY
    assert coverage.owner == CapabilityOwner.LEGACY
    assert coverage.capability_ids == ("legacy.commerce_topic",)


def test_short_order_reference_continues_only_an_active_legacy_order_topic() -> None:
    without_context = resolve_capability_coverage(
        "Номер 148237",
        _candidate(),
        default_registry(),
    )
    with_context = resolve_capability_coverage(
        "Номер 148237",
        _candidate(),
        default_registry(),
        turn_context=CapabilityTurnContext(
            legacy_commerce_topic="order_status",
        ),
    )

    assert without_context.status == CapabilityCoverageStatus.V2_READY
    assert with_context.status == CapabilityCoverageStatus.LEGACY_READY
    assert with_context.capability_ids == ("legacy.commerce_topic",)
    assert "capability_owner_legacy_continuation" in with_context.reason_codes


def test_legacy_commerce_preempts_a_false_positive_generic_catalogue_candidate() -> None:
    coverage = resolve_capability_coverage(
        "Где мой заказ 148237?",
        _candidate(),
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.LEGACY_READY
    assert coverage.owner == CapabilityOwner.LEGACY
    assert coverage.capability_ids == ("legacy.commerce_topic",)


def test_unknown_uncovered_turn_is_observed_but_not_mislabeled_safe_legacy() -> None:
    coverage = resolve_capability_coverage(
        "Расскажите что-нибудь необычное",
        None,
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.UNRESOLVED
    assert coverage.owner is None
    assert coverage.enforced is False


def test_pure_small_talk_is_an_explicit_safe_legacy_capability() -> None:
    coverage = resolve_capability_coverage(
        "Здравствуйте!",
        None,
        default_registry(),
    )

    assert coverage.status == CapabilityCoverageStatus.LEGACY_READY
    assert coverage.capability_ids == ("legacy.small_talk",)


def test_uncovered_boundary_preserves_typed_state_and_does_not_claim_semantic_acceptance() -> None:
    base = _candidate().model_copy(
        update={
            "response": None,
            "response_digest": None,
            "answer_plan_id": None,
            "rendered_answer_id": None,
            "validation_status": "rejected",
            "semantic_accepted": False,
            "contracts_resolved": False,
            "eligible_for_delivery": False,
        }
    )
    boundary = build_v2_uncovered_capability_boundary_candidate(
        base,
        AnswerSourceSnapshot(source_revision="source-1"),
        session_id="session",
        turn_id="turn-1",
    )

    assert boundary is not None
    assert boundary.state_after == base.state_before
    assert boundary.semantic_accepted is False
    assert boundary.capability_boundary_result is not None
    assert boundary.product_scope_effect.value == "preserve"
    coverage = resolve_capability_coverage(
        "Расскажите что-нибудь необычное",
        boundary,
        default_registry(),
    )
    assert coverage.status == CapabilityCoverageStatus.V2_READY
    assert coverage.capability_ids == ("v2.uncovered_boundary",)
    decision = decide_cutover(
        EarlyControlResult(),
        boundary,
        default_registry(),
        _runtime(),
        session_fingerprint="session",
        capability_coverage=coverage,
    )
    assert decision.owner_candidate == ResponseOwner.V2


def test_public_primary_honours_an_enforced_legacy_capability() -> None:
    registry = default_registry()
    candidate = _candidate()
    coverage = resolve_capability_coverage(
        "Угольник PPR 20 — 30 шт, муфта PPR 25 — 5 шт",
        candidate,
        registry,
    )

    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        registry,
        _runtime(),
        session_fingerprint="session",
        capability_coverage=coverage,
    )

    assert decision.owner_candidate == ResponseOwner.LEGACY
    assert decision.execution_mode == ExecutionMode.LEGACY_ONLY
    assert "capability_owner_legacy" in decision.reason_codes
    assert "legacy.item_list" in decision.reason_codes


def test_public_primary_keeps_delivery_ready_v2_capability() -> None:
    registry = default_registry()
    candidate = _candidate()
    coverage = resolve_capability_coverage(
        "Нужна ППР труба 25 мм",
        candidate,
        registry,
    )

    decision = decide_cutover(
        EarlyControlResult(),
        candidate,
        registry,
        _runtime(),
        session_fingerprint="session",
        capability_coverage=coverage,
    )

    assert decision.owner_candidate == ResponseOwner.V2
    assert "approved_explicit_public_v2_primary" in decision.reason_codes
