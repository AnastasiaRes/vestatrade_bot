from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agents.semantic_interpreter import (
    SemanticInterpretationResult,
    TurnUnderstanding,
)
from app.catalog_v2.contracts import (
    CandidateStatus,
    CatalogFact,
    CatalogProductRole,
    CatalogProductSnapshot,
    CatalogSearchStage,
    FactProvenance,
    ProductKind,
    ReadinessStatus,
)
from app.catalog_v2.normalization import build_catalog_snapshot
from app.catalog_v2.registry import ProductContractRegistry
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.feed_loader import FeedLoader
from app.models import SessionState
from app.session_store import InMemorySessionStore, RedisSessionStore


FEED100 = Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml"


@pytest.fixture(scope="module")
def catalog():
    products = FeedLoader().parse_xml(FEED100.read_bytes())
    return build_catalog_snapshot(products)


def _product(kind: str, category: str, role: str = "target") -> dict[str, object]:
    return {
        "text": kind,
        "canonical_type": kind,
        "category": category,
        "role": role,
        "evidence": kind,
    }


def _fact(
    name: str,
    value: object = 25,
    unit: str | None = "mm",
    *,
    status: str = "known",
    polarity: str = "required",
    product: int = 0,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value if status == "known" else None,
        "unit": unit if status == "known" else None,
        "status": status,
        "polarity": polarity,
        "applies_to_product": product,
        "evidence": name,
    }


def _semantic(
    products: list[dict[str, object]],
    constraints: list[dict[str, object]] | None = None,
    acts: list[str] | None = None,
) -> SemanticInterpretationResult:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.0",
            "language": "ru",
            "operation": "new",
            "acts": acts or ["select"],
            "products": products,
            "constraints": constraints or [],
            "references": [],
            "ambiguities": [],
            "answers_pending_question": False,
            "confidence": 0.96,
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
    catalog=(),
    *,
    solution: bool = False,
):
    return DialogueControllerV2().run(
        None,
        semantic,
        TurnMetadata(turn_id="offline-stage3"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        solution_plan_enabled=solution,
        catalog_snapshot=tuple(catalog),
    )


def _planning(outcome):
    assert outcome.catalog_planning is not None
    return outcome.catalog_planning


def test_feed100_is_fully_and_explicitly_covered(catalog) -> None:
    assert len(catalog) == 100
    assert not [item for item in catalog if item.product_kind == ProductKind.UNSUPPORTED]
    assert len({item.product_kind for item in catalog}) == 19
    assert all(item.role != CatalogProductRole.UNKNOWN for item in catalog)


def test_machine_readable_feed100_audit_matches_fixture(catalog) -> None:
    audit = json.loads(
        (Path(__file__).parents[1] / "reports/stage3_feed100_contract_audit.json").read_text()
    )
    assert audit["source_sha256"] == "81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f"
    assert audit["sanitized_product_count"] == len(catalog) == 100
    assert audit["unsupported_count"] == 0
    assert sum(item["count"] for item in audit["entries"]) == 100
    assert all(item["contract_id"] for item in audit["entries"])


@pytest.mark.parametrize(
    ("semantic_kind", "category", "expected"),
    [
        ("труба", "pipes", ProductKind.PIPE),
        ("канализационная труба", "sewer", ProductKind.SEWER_PIPE),
        ("угольник", "fittings", ProductKind.ELBOW),
        ("отвод", "sewer", ProductKind.SEWER_ELBOW),
        ("тройник", "sewer", ProductKind.TEE),
        ("муфта", "sewer", ProductKind.COUPLING),
        ("переходная муфта", "fittings", ProductKind.REDUCING_COUPLING),
    ],
)
def test_fitting_families_resolve_to_distinct_contracts(
    semantic_kind, category, expected
) -> None:
    outcome = _run(_semantic([_product(semantic_kind, category)]))
    resolution = _planning(outcome).contract_resolutions[0]
    assert resolution.product_kind == expected


def test_contracts_encode_product_specific_required_facts() -> None:
    registry = ProductContractRegistry()
    required = {
        kind: {
            item.name for item in registry.for_kind(kind).fact_definitions
            if item.required_for_exact
        }
        for kind in (ProductKind.SEWER_PIPE, ProductKind.TEE, ProductKind.REDUCING_COUPLING)
    }
    assert "length_mm" in required[ProductKind.SEWER_PIPE]
    assert "length_mm" not in required[ProductKind.TEE]
    assert "secondary_diameter_mm" in required[ProductKind.TEE]
    assert "secondary_diameter_mm" in required[ProductKind.REDUCING_COUPLING]


def test_target_pump_survives_radiator_context(catalog) -> None:
    outcome = _run(
        _semantic(
            [
                _product("циркуляционный насос", "pumps"),
                _product("радиатор", "radiators", "existing"),
            ]
        ),
        catalog,
    )
    assert _planning(outcome).contract_resolutions[0].product_kind == ProductKind.CIRCULATION_PUMP
    assert all(plan.product_kind != ProductKind.RADIATOR for plan in _planning(outcome).search_plans)


def test_tee_remains_target_when_pipe_is_context(catalog) -> None:
    outcome = _run(
        _semantic(
            [
                _product("тройник", "sewer"),
                _product("труба", "sewer", "context"),
            ]
        ),
        catalog,
    )
    assert _planning(outcome).contract_resolutions[0].product_kind == ProductKind.TEE


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("циркуляционный насос", ProductKind.CIRCULATION_PUMP),
        ("насос рециркуляции гвс", ProductKind.DHW_CIRCULATION_PUMP),
        ("скважинный насос", ProductKind.BOREHOLE_PUMP),
        ("дренажный насос", ProductKind.DRAINAGE_PUMP),
        ("насосная станция", ProductKind.PUMP_STATION),
    ],
)
def test_pump_types_do_not_mix(kind, expected) -> None:
    outcome = _run(_semantic([_product(kind, "pumps")]))
    assert _planning(outcome).contract_resolutions[0].product_kind == expected


def test_unknown_refused_and_deferred_are_preliminary_not_reasked() -> None:
    constraints = [
        _fact("connection_diameter", status="unknown"),
        _fact("max_head_m", status="refused"),
        _fact("mounting_length", status="deferred"),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints)
    )
    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.PRELIMINARY_READY
    assert assessment.unknown_facts == ("diameter_mm",)
    assert assessment.refused_facts == ("max_head_m",)
    assert assessment.deferred_facts == ("mounting_length_mm",)
    assert assessment.recommended_question_fact is None
    assert outcome.next_action_plan.primary.kind != NextActionKind.ASK_DECISION_CHANGING_QUESTION


def test_preliminary_candidates_are_unverified_until_unknown_hard_facts_are_known(catalog) -> None:
    constraints = [
        _fact("connection_diameter", 25),
        _fact("max_head_m", status="unknown"),
        _fact("mounting_length", status="deferred"),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints),
        catalog,
    )
    plan = _planning(outcome).search_plans[0]
    assert plan.unverified_skus
    assert not plan.eligible_skus
    assert CatalogSearchStage.HONEST_NO_MATCH not in plan.stages
    assert all(
        item.reason_codes == ("required_customer_fact_unavailable",)
        for item in plan.candidate_assessments
        if item.status == CandidateStatus.UNVERIFIED
    )


def test_exact_pump_plan_enforces_every_hard_constraint(catalog) -> None:
    constraints = [
        _fact("connection_diameter", 25, "mm"),
        _fact("max_head_m", 6, "m"),
        _fact("mounting_length", 13, "cm"),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints),
        catalog,
    )
    plan = _planning(outcome).search_plans[0]
    assert outcome.next_action_plan.primary.kind == NextActionKind.SEARCH_EXACT
    assert "VRS.256.13.0" in plan.eligible_skus
    rejected = next(item for item in plan.candidate_assessments if item.sku == "VRS.256.18.0")
    assert rejected.status == CandidateStatus.REJECTED
    assert "mounting_length_mm" in rejected.mismatched_hard_facts


def test_missing_catalog_hard_fact_is_unverified_not_match(catalog) -> None:
    target = next(item for item in catalog if item.sku == "53843")
    without_mount = target.model_copy(
        update={"facts": tuple(x for x in target.facts if x.name != "mounting_length_mm")}
    )
    constraints = [
        _fact("connection_diameter", 25),
        _fact("max_head_m", 4.5, "m"),
        _fact("mounting_length", 180),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints),
        [without_mount],
    )
    candidate = _planning(outcome).search_plans[0].candidate_assessments[0]
    assert candidate.status == CandidateStatus.UNVERIFIED
    assert candidate.missing_hard_facts == ("mounting_length_mm",)


def test_accessory_tool_and_spare_part_cannot_replace_base_product() -> None:
    provenance = FactProvenance(source="attribute", source_field="x", raw_value="25", parser="test")
    common = (CatalogFact(name="diameter_mm", value=25, unit="mm", provenance=provenance),)
    impostors = tuple(
        CatalogProductSnapshot(
            sku=f"role-{role.value}", name=role.value, category="pumps",
            product_kind=ProductKind.CIRCULATION_PUMP, role=role, facts=common,
        )
        for role in (CatalogProductRole.ACCESSORY, CatalogProductRole.TOOL, CatalogProductRole.SPARE_PART)
    )
    constraints = [
        _fact("connection_diameter", 25),
        _fact("max_head_m", status="unknown"),
        _fact("mounting_length", status="unknown"),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints),
        impostors,
    )
    assessments = _planning(outcome).search_plans[0].candidate_assessments
    assert all(item.status == CandidateStatus.REJECTED for item in assessments)
    assert all(item.reason_codes == ("catalog_role_incompatible",) for item in assessments)


def test_soft_constraint_is_relaxed_one_at_a_time_with_difference(catalog) -> None:
    constraints = [
        _fact("diameter", 20),
        _fact("material", "pp_alux", None, polarity="preferred"),
    ]
    outcome = _run(_semantic([_product("труба", "pipes")], constraints), catalog)
    plan = _planning(outcome).search_plans[0]
    assert plan.relaxed_skus
    candidate = next(item for item in plan.candidate_assessments if item.relaxations)
    assert len(candidate.relaxations) == 1
    assert candidate.relaxations[0].reason_code == "soft_preference_differs"
    assert candidate.relaxations[0].candidate_value is not None


def test_two_products_create_independent_plans_and_solution_without_quantity(catalog) -> None:
    outcome = _run(
        _semantic(
            [_product("труба", "pipes"), _product("шаровой кран", "valves")],
            [
                _fact("diameter", 20, product=0),
                _fact("connection_size", "1/2", "inch", product=1),
                _fact("connection_pattern", "female_female", None, product=1),
            ],
        ),
        catalog,
        solution=True,
    )
    planning = _planning(outcome)
    assert len({plan.goal_id for plan in planning.search_plans}) == 2
    assert planning.solution_plan is not None
    assert len(planning.solution_plan.components) == 2
    assert all(component.quantity is None for component in planning.solution_plan.components)


def test_price_and_stock_remain_distinct_tasks_without_fake_bom(catalog) -> None:
    outcome = _run(
        _semantic(
            [_product("труба", "pipes")],
            [_fact("diameter", 20)],
            acts=["check_price", "check_stock"],
        ),
        catalog,
        solution=True,
    )
    current_tasks = [task for task in outcome.state_after.tasks if task.source_turn == 1]
    assert {task.act.value for task in current_tasks} == {"check_price", "check_stock"}
    assert _planning(outcome).solution_plan is None


def test_planner_is_deterministic_and_does_not_mutate_inputs(catalog) -> None:
    semantic = _semantic([_product("труба", "pipes")], [_fact("diameter", 20)])
    before = deepcopy(catalog)
    first = _run(semantic, catalog)
    second = _run(semantic, catalog)
    assert _planning(first) == _planning(second)
    assert catalog == before


def test_old_v2_state_and_session_serializers_accept_new_optional_field() -> None:
    old = DialogueStateV2.model_validate({"schema_version": "2.0", "turn_number": 2})
    assert old.catalog_planning is None
    session = SessionState(session_id="stage3", dialogue_state_v2=old)
    memory = InMemorySessionStore()
    memory.save(session)
    assert memory.snapshot("stage3").dialogue_state_v2 == old
    assert RedisSessionStore._decode(RedisSessionStore._encode(session)).dialogue_state_v2 == old


def test_populated_catalog_planning_round_trips_session_stores(catalog) -> None:
    outcome = _run(
        _semantic([_product("труба", "pipes")], [_fact("diameter", 20)]),
        catalog,
    )
    state = SessionState(session_id="stage3-populated", dialogue_state_v2=outcome.state_after)
    memory = InMemorySessionStore()
    memory.save(state)
    restored_memory = memory.snapshot("stage3-populated")
    restored_redis = RedisSessionStore._decode(RedisSessionStore._encode(state))
    assert restored_memory.dialogue_state_v2.catalog_planning == outcome.catalog_planning
    assert restored_redis.dialogue_state_v2.catalog_planning == outcome.catalog_planning


def test_catalogue_state_is_only_assigned_inside_reducer_source() -> None:
    root = Path(__file__).parents[1] / "app"
    offenders = []
    needle = '"catalog_planning": planning'
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if needle in text and path.name != "reducer.py":
            offenders.append(path)
    assert offenders == []


def test_no_sku_specific_branches_or_raw_user_text_in_stage3_sources(catalog) -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).parents[1] / "app/catalog_v2").glob("*.py")
    )
    assert "current_message" not in source
    assert "user_message" not in source
    for item in catalog:
        assert f'== "{item.sku}"' not in source
