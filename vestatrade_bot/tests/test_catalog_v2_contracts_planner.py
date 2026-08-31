from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agents.semantic_interpreter import (
    GoalOperation,
    SemanticInterpretationResult,
    TurnUnderstanding,
)
from app.catalog_v2.contracts import (
    CandidateStatus,
    CatalogAvailabilityStatus,
    CatalogFact,
    CatalogProductRole,
    CatalogProductSnapshot,
    CatalogSearchStage,
    ComparisonMode,
    FactProvenance,
    ProductKind,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.catalog_v2.normalization import (
    build_catalog_snapshot,
    parse_numeric_choice_value,
    parse_numeric_range_value,
    normalize_fact_value,
    parse_pump_designation,
)
from app.catalog_v2.planner import (
    _requires_in_stock_candidates,
    _same_value,
    plan_catalog_search,
)
from app.catalog_v2.registry import ProductContractRegistry
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintPolarity,
    ConstraintStatus,
    ConstraintStrength,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    SelectionPreferenceKind,
    SelectionPreferenceSignal,
    TurnMetadata,
)
from app.dialogue_v2.controller import DialogueControllerV2
from app.feed_loader import FeedLoader
from app.models import Product, SessionState
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


def _pipe_required_constraints(*, product: int = 0) -> list[dict[str, object]]:
    return [
        _fact("pipe_service", "hot_water", None, product=product),
        _fact("operating_temperature_c", 70, "C", product=product),
        _fact("operating_pressure_bar", 6, "bar", product=product),
    ]


def _pipe_catalog_required_facts(
    provenance: FactProvenance,
) -> tuple[CatalogFact, ...]:
    return (
        CatalogFact(
            name="pipe_service",
            value="hot_water",
            provenance=provenance,
        ),
        CatalogFact(
            name="operating_temperature_c",
            value=70,
            unit="C",
            provenance=provenance,
        ),
        CatalogFact(
            name="operating_pressure_bar",
            value=10,
            unit="bar",
            provenance=provenance,
        ),
    )


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


def test_every_feed100_contract_requires_a_safe_preliminary_identity_anchor(catalog) -> None:
    """A generic ``show what you have`` command must never expose a whole kind.

    Exact readiness may ask for more facts.  This smaller invariant protects
    progressive selection: each covered product kind still has at least one
    required fact that cannot be silently omitted for a preliminary card set.
    """

    registry = ProductContractRegistry()
    feed_kinds = {
        item.product_kind
        for item in catalog
        if item.product_kind != ProductKind.UNSUPPORTED
    }
    missing_anchors = {
        kind.value
        for kind in feed_kinds
        if not registry.for_kind(kind).preliminary_identity_fact_groups
    }

    assert missing_anchors == set()
    for kind in feed_kinds:
        contract = registry.for_kind(kind)
        assert contract is not None
        known_fact_names = {fact.name for fact in contract.fact_definitions}
        assert all(
            group and set(group) <= known_fact_names
            for group in contract.preliminary_identity_fact_groups
        )


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


def test_unavailable_circulation_anchor_asks_for_remaining_safe_alternative() -> None:
    constraints = [
        _fact("connection_diameter", status="unknown"),
        _fact("max_head_m", status="refused"),
        _fact("mounting_length", status="deferred"),
    ]
    outcome = _run(
        _semantic([_product("циркуляционный насос", "pumps")], constraints)
    )
    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.unknown_facts == ("diameter_mm",)
    assert assessment.refused_facts == ("max_head_m",)
    assert assessment.deferred_facts == ("mounting_length_mm",)
    assert assessment.recommended_question_fact == "duty_point_flow_l_h"
    assert assessment.unavailable_preliminary_identity_groups == ()
    assert outcome.next_action_plan.primary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION


def test_pressure_in_metres_is_a_pump_head_alias_but_bar_is_not() -> None:
    metres = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25),
                _fact("pressure", 4.5, "m"),
                _fact("mounting_length", 180),
            ],
        )
    )
    assessment = _planning(metres).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    assert next(
        fact for fact in assessment.confirmed_hard_facts
        if fact.name == "max_head_m"
    ).value == 4.5

    bars = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25),
                _fact("pressure", 4.5, "bar"),
                _fact("mounting_length", 180),
            ],
        )
    )
    bar_assessment = _planning(bars).readiness_assessments[0]
    assert bar_assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert "max_head_m" in bar_assessment.missing_decision_facts


def test_thread_connection_and_boiler_fuel_aliases_are_canonical() -> None:
    valve = _run(
        _semantic(
            [_product("ball_valve", "valves")],
            [
                _fact("thread_size", "G1/2", None),
                _fact("connection_pattern", "female_female", None),
            ],
        )
    )
    valve_facts = _planning(valve).readiness_assessments[0].confirmed_hard_facts
    assert next(item for item in valve_facts if item.name == "connection_size").value == "1/2"

    boiler = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                _fact("fuel_type", "газовый", None),
                _fact("boiler_power_kw", 24, "kW"),
                _fact("circuit_count", 2, None),
            ],
        )
    )
    boiler_assessment = _planning(boiler).readiness_assessments[0]
    assert boiler_assessment.status == ReadinessStatus.EXACT_READY
    assert next(
        item for item in boiler_assessment.confirmed_hard_facts
        if item.name == "boiler_type"
    ).value == "gas"


@pytest.mark.parametrize(
    "designation",
    (
        "Кран шаровой 3-way 1/2",
        "Кран шаровой 3-port 1/2",
        "Кран шаровой 3-х ходовой 1/2",
        "Кран шаровой трёхходовой 1/2",
    ),
)
def test_ball_valve_port_count_name_parser_is_general(designation: str) -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="valve-three-port",
                name=designation,
                category_path="Водозапорная арматура",
                attributes_normalized={"тип товара": "Кран шаровой"},
            )
        ]
    )

    port_count = next(
        item for item in snapshot[0].facts if item.name == "port_count"
    )
    assert port_count.value == 3
    assert port_count.provenance.source == "name"
    assert port_count.provenance.parser == "port_count"


@pytest.mark.parametrize(
    ("fact_name", "raw_value", "expected"),
    (
        ("port_count", "два входа, один выход", 3),
        ("port_count", "2 inlets and 1 outlet", 3),
        ("connection_pattern", "НР-НР", "male_male"),
        ("connection_pattern", "ВР-ВР", "female_female"),
        ("connection_pattern", "ВР-НР", "female_male"),
        ("combustion_chamber", "закр.камера", "closed"),
        (
            "combustion_chamber",
            "Закрытая (принудительная тяга)",
            "closed",
        ),
    ),
)
def test_general_catalog_fact_canonicalization(
    fact_name: str,
    raw_value: str,
    expected: object,
) -> None:
    assert normalize_fact_value(fact_name, raw_value) == expected


def test_explicit_numeric_range_is_preserved_and_compared_as_an_interval() -> None:
    assert parse_numeric_range_value("10–15") == (10, 15)
    assert parse_numeric_range_value("10–15 кВт") == (10, 15)
    assert _same_value(ComparisonMode.NUMERIC, "power_kw", "10–15", 14)
    assert _same_value(ComparisonMode.NUMERIC, "power_kw", "10–15", 10)
    assert not _same_value(ComparisonMode.NUMERIC, "power_kw", "10–15", 16)

    outcome = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                _fact("boiler_type", "gas", None),
                _fact("power_kw", "10–15", "kW"),
                _fact("circuits", 1, None),
            ],
        )
    )
    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    assert next(
        item for item in assessment.confirmed_hard_facts
        if item.name == "power_kw"
    ).value == "10–15"


def test_explicit_numeric_choices_are_preserved_without_picking_first() -> None:
    assert parse_numeric_choice_value("130 или 180") == (130, 180)
    assert parse_numeric_choice_value("130 mm or 180 mm") == (130, 180)
    assert parse_numeric_choice_value("130–180") is None
    assert _same_value(
        ComparisonMode.NUMERIC,
        "mounting_length_mm",
        "130 или 180",
        130,
    )
    assert _same_value(
        ComparisonMode.NUMERIC,
        "mounting_length_mm",
        "130 или 180",
        180,
    )
    assert not _same_value(
        ComparisonMode.NUMERIC,
        "mounting_length_mm",
        "130 или 180",
        150,
    )

    outcome = _run(
        _semantic(
            [_product("circulation_pump", "pumps")],
            [
                _fact("diameter_mm", 25, "mm"),
                _fact("max_head_m", 6, "m"),
                _fact("mounting_length_mm", "130 или 180", "mm"),
            ],
        )
    )
    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    assert next(
        item
        for item in assessment.confirmed_hard_facts
        if item.name == "mounting_length_mm"
    ).value == "130 или 180"


def test_numeric_range_is_not_applied_to_exact_text_comparison() -> None:
    assert not _same_value(ComparisonMode.EXACT, "sku", "10-15", 12)


def test_pressure_units_are_converted_or_blocked_without_relabelling() -> None:
    converted = _run(
        _semantic(
            [_product("ball_valve", "valves")],
            [_fact("operating_pressure_bar", 100, "kPa")],
        )
    )
    converted_assessment = _planning(converted).readiness_assessments[0]
    pressure = next(
        item
        for item in converted_assessment.confirmed_hard_facts
        if item.name == "operating_pressure_bar"
    )
    assert pressure.value == 1
    assert pressure.unit == "bar"

    unsupported = _run(
        _semantic(
            [_product("ball_valve", "valves")],
            [_fact("operating_pressure_bar", 100, "psi")],
        )
    )
    unsupported_assessment = _planning(unsupported).readiness_assessments[0]
    assert unsupported_assessment.status == ReadinessStatus.BLOCKED
    assert unsupported_assessment.conflicting_facts == (
        "operating_pressure_bar:unsupported_unit:psi",
    )


def test_ball_valve_keeps_applicable_hard_facts_and_ignores_context_noise() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="valve-three-port-male",
                name='Кран шаровой трехходовой 1/2"',
                category_path="Водозапорная арматура",
                brand="KRAUS",
                attributes_normalized={
                    "тип товара": "Кран шаровой",
                    "диаметр подключения, дюйм": "1/2",
                    "тип резьбы": "С наружной резьбой (mm)",
                    "рабочая среда": "Для воды",
                    "максимальная рабочая температура, °с": "150",
                    "максимальное рабочее давление, бар": "10",
                },
            )
        ]
    )
    outcome = _run(
        _semantic(
            [_product("ball_valve", "valves")],
            [
                _fact("connection_size", '1/2"', None),
                _fact("port_count", "два входа, один выход", None),
                _fact("thread_type", "НР-НР", None),
                _fact("brand", "KRAUS", None),
                _fact("application", "горячая вода", None),
                _fact("operating_temperature_c", 95, "°C"),
                _fact("operating_pressure_bar", 6, "бар"),
                _fact("has_handle", True, None),
                _fact("installation_location", "под раковиной", None),
                _fact("style_match", "к существующим кранам", None, polarity="preferred"),
                _fact("area_m2", 80, "m2"),
            ],
        ),
        snapshot,
    )

    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    hard_values = {
        fact.name: fact.value for fact in assessment.confirmed_hard_facts
    }
    assert hard_values == {
        "connection_size": "1/2",
        "port_count": 3,
        "connection_pattern": "male_male",
        "brand": "kraus",
        "application": "water",
        "operating_temperature_c": 95,
        "operating_pressure_bar": 6,
    }
    assert _planning(outcome).search_plans[0].eligible_skus == (
        "valve-three-port-male",
    )


def test_gas_boiler_chamber_is_canonical_and_non_catalog_questions_do_not_block() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="2201375",
                name=(
                    "Котел газовый настенный Arderia SB24 "
                    "(24 кВт, закр.камера, одноконтурный)"
                ),
                category_path="Котельное оборудование",
                brand="Arderia",
                attributes_normalized={
                    "тип товара": "Котёл",
                    "тип котла": "Газовый",
                    "мощность, квт": "24",
                    "количество контуров": "Одноконтурный",
                    "камера сгорания": "Закрытая (принудительная тяга)",
                },
            )
        ]
    )
    outcome = _run(
        _semantic(
            [_product("gas_boiler", "boilers")],
            [
                _fact("boiler_type", "газовый", None),
                _fact("power_kw", 24, "кВт"),
                _fact("circuits", "одноконтурный", None),
                _fact("combustion_chamber", "закр.камера", None),
                _fact("area_m2", 100, "m2"),
                _fact("minimum_power_kw", status="unknown"),
                _fact("flue_solution", status="unknown"),
            ],
        ),
        snapshot,
    )

    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    assert {fact.name for fact in assessment.confirmed_hard_facts} == {
        "boiler_type",
        "power_kw",
        "area_m2",
        "circuits",
        "combustion_chamber",
    }
    plan = _planning(outcome).search_plans[0]
    assert plan.eligible_skus == ("2201375",)
    # The area is useful customer context, but a named design power stays the
    # direct exact selector.  A card without a declared coverage must not make
    # that already exact power selection silently become preliminary.
    assert "area_m2" not in {item.name for item in plan.hard_constraints}


def test_boiler_area_is_a_source_backed_preliminary_proxy_not_a_power_formula() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="gas-short",
                name="Котёл газовый 12 кВт одноконтурный",
                category_path="Котельное оборудование",
                stock_qty=3,
                attributes_normalized={
                    "тип товара": "Котёл",
                    "тип котла": "Газовый",
                    "мощность, квт": "12",
                    "количество контуров": "Одноконтурный",
                    "отапливаемая площадь, м²": "120",
                },
            ),
            Product(
                sku="gas-coverage-160",
                name="Котёл газовый 16 кВт одноконтурный",
                category_path="Котельное оборудование",
                stock_qty=3,
                attributes_normalized={
                    "тип товара": "Котёл",
                    "тип котла": "Газовый",
                    "мощность, квт": "16",
                    "количество контуров": "Одноконтурный",
                    "отапливаемая площадь, м²": "160",
                },
            ),
            Product(
                sku="gas-coverage-240",
                name="Котёл газовый 24 кВт одноконтурный",
                category_path="Котельное оборудование",
                stock_qty=3,
                attributes_normalized={
                    "тип товара": "Котёл",
                    "тип котла": "Газовый",
                    "мощность, квт": "24",
                    "количество контуров": "Одноконтурный",
                    "отапливаемая площадь, м²": "240",
                },
            ),
            Product(
                sku="electric-coverage-240",
                name="Котёл электрический 24 кВт одноконтурный",
                category_path="Котельное оборудование",
                stock_qty=3,
                attributes_normalized={
                    "тип товара": "Котёл",
                    "тип котла": "Электрический",
                    "мощность, квт": "24",
                    "количество контуров": "Одноконтурный",
                    "отапливаемая площадь, м²": "240",
                },
            ),
        ]
    )
    outcome = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                _fact("boiler_type", "gas", None),
                _fact("area_m2", 150, "m2"),
                _fact("circuits", 1, None),
            ],
        ),
        snapshot,
    )

    assessment = _planning(outcome).readiness_assessments[0]
    assert assessment.status == ReadinessStatus.PRELIMINARY_READY
    assert assessment.reason_codes == (
        "required_fact_satisfied_by_preliminary_source_backed_proxy",
        "preliminary_path_allowed",
    )
    plan = _planning(outcome).search_plans[0]
    assert {item.name for item in plan.hard_constraints} >= {
        "boiler_type",
        "area_m2",
        "circuits",
    }
    assert plan.eligible_skus == ("gas-coverage-160", "gas-coverage-240")
    short = next(item for item in plan.candidate_assessments if item.sku == "gas-short")
    electric = next(
        item for item in plan.candidate_assessments if item.sku == "electric-coverage-240"
    )
    assert "area_m2" in short.mismatched_hard_facts
    assert "boiler_type" in electric.mismatched_hard_facts


def test_boiler_area_first_asks_fuel_then_circuits() -> None:
    controller = DialogueControllerV2()
    first = controller.run(
        None,
        _semantic(
            [_product("boiler", "boilers")],
            [_fact("area_m2", 150, "m2")],
        ),
        TurnMetadata(turn_id="boiler-area-1"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
    )
    assert first.next_action_plan.primary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert first.next_action_plan.primary.fact_name == "boiler_type"

    second = controller.run(
        first.state_after,
        _semantic(
            [],
            [
                _fact("boiler_type", "gas", None)
                | {"applies_to_product": None},
            ],
        ),
        TurnMetadata(turn_id="boiler-area-2"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
    )
    assert second.next_action_plan.primary.kind == NextActionKind.ASK_DECISION_CHANGING_QUESTION
    assert second.next_action_plan.primary.fact_name == "circuits"


def test_bare_product_explanation_waits_without_catalogue_guess() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="gas-24-closed-single",
                name=(
                    "Котел газовый 24 кВт, закрытая камера, "
                    "одноконтурный"
                ),
                category_path="Котельное оборудование",
                attributes_normalized={
                    "тип товара": "Котёл",
                    "мощность, квт": "24",
                    "количество контуров": "Одноконтурный",
                    "камера сгорания": "Закрытая",
                },
            )
        ]
    )
    controller = DialogueControllerV2()
    first = controller.run(
        None,
        _semantic(
            [_product("gas_boiler", "boilers")],
            [
                _fact("power_kw", 24, "kW"),
                _fact("circuits", 1, None),
                _fact("combustion_chamber", "closed", None),
            ],
        ),
        TurnMetadata(turn_id="product-explanation-first"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )
    minimum_power = _fact("minimum_power_kw", status="unknown")
    flue_solution = _fact("flue_solution", status="unknown")
    minimum_power["applies_to_product"] = None
    flue_solution["applies_to_product"] = None
    followup = _semantic(
        [],
        [minimum_power, flue_solution],
        acts=["explain"],
    )
    followup = followup.model_copy(
        update={
            "understanding": followup.understanding.model_copy(
                update={"operation": GoalOperation.REFINE}
            )
        }
    )
    second = controller.run(
        first.state_after,
        followup,
        TurnMetadata(turn_id="product-explanation-followup"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )

    assert (
        second.next_action_plan.primary.kind
        == NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING
    )
    assert second.next_action_plan.primary.fact_name is None
    assert second.next_action_plan.primary.reason_code == (
        "explanation_missing_typed_information_request"
    )
    assert second.catalog_planning is not None
    assert second.catalog_planning.candidate_skus == ()


def test_three_port_ball_valve_beats_missing_port_count_without_inferring_two() -> None:
    common_attributes = {
        "тип товара": "Кран шаровой",
        "диаметр подключения, дюйм": "1/2",
        "тип резьбы": "внутренняя/внутренняя",
    }
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="valve-explicit-three",
                name='Кран шаровой трёхходовой 1/2"',
                category_path="Водозапорная арматура",
                attributes_normalized=common_attributes,
            ),
            Product(
                sku="valve-explicit-two",
                name='Кран шаровой 2-way 1/2"',
                category_path="Водозапорная арматура",
                attributes_normalized=common_attributes,
            ),
            Product(
                sku="valve-port-absent",
                name='Кран шаровой 1/2"',
                category_path="Водозапорная арматура",
                attributes_normalized=common_attributes,
            ),
        ]
    )
    facts_by_sku = {
        item.sku: {fact.name: fact.value for fact in item.facts}
        for item in snapshot
    }
    assert facts_by_sku["valve-explicit-three"]["port_count"] == 3
    assert facts_by_sku["valve-explicit-two"]["port_count"] == 2
    assert "port_count" not in facts_by_sku["valve-port-absent"]

    outcome = _run(
        _semantic(
            [_product("ball_valve", "valves")],
            [
                _fact("thread_size", "G1/2", None),
                _fact("connection_pattern", "female_female", None),
                _fact("number_of_ports", 3, None),
            ],
        ),
        snapshot,
    )
    plan = _planning(outcome).search_plans[0]
    assert plan.eligible_skus == ("valve-explicit-three",)
    assert plan.unverified_skus == ("valve-port-absent",)
    assert _planning(outcome).candidate_skus[:2] == (
        "valve-explicit-three",
        "valve-port-absent",
    )
    explicit_two = next(
        item
        for item in plan.candidate_assessments
        if item.sku == "valve-explicit-two"
    )
    unknown_two = next(
        item
        for item in plan.candidate_assessments
        if item.sku == "valve-port-absent"
    )
    assert explicit_two.status == CandidateStatus.REJECTED
    assert explicit_two.mismatched_hard_facts == ("port_count",)
    assert unknown_two.status == CandidateStatus.UNVERIFIED
    assert unknown_two.missing_hard_facts == ("port_count",)


def test_contract_scoped_pump_and_boiler_followup_aliases_are_canonical() -> None:
    pump = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_size", "25 мм", None),
                _fact("напор", 4.2, "м"),
                _fact("mounting_length", 130, "mm"),
            ],
        )
    )
    pump_assessment = _planning(pump).readiness_assessments[0]
    assert pump_assessment.status == ReadinessStatus.EXACT_READY
    pump_values = {
        item.name: item.value for item in pump_assessment.confirmed_hard_facts
    }
    assert pump_values["diameter_mm"] == 25
    assert pump_values["max_head_m"] == 4.2

    boiler = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                _fact("type", "газовый", None),
                _fact("needs_hot_water", True, None),
                _fact("power", 35, "kW", polarity="preferred"),
            ],
        )
    )
    boiler_assessment = _planning(boiler).readiness_assessments[0]
    values = {
        item.name: item.value
        for item in (
            *boiler_assessment.confirmed_hard_facts,
            *boiler_assessment.confirmed_soft_facts,
        )
    }
    assert values["boiler_type"] == "gas"
    assert values["circuits"] == 2
    assert values["power_kw"] == 35
    assert {item.name for item in boiler_assessment.confirmed_soft_facts} == {
        "power_kw"
    }
    assert boiler_assessment.status == ReadinessStatus.EXACT_READY


def test_flow_unit_can_be_grounded_in_evidence_without_inferring_value() -> None:
    flow = _fact("flow_rate", 18, None)
    flow["evidence"] = "расходом 18 л/мин"
    outcome = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25),
                _fact("max_head_m", 4.5, "m"),
                _fact("mounting_length", 180),
                flow,
            ],
        )
    )

    assessment = _planning(outcome).readiness_assessments[0]
    fact = next(
        item
        for item in (
            *assessment.confirmed_hard_facts,
            *assessment.confirmed_soft_facts,
        )
        if item.name == "max_flow_l_h"
    )
    assert fact.value == 1080
    assert fact.unit == "l/h"


def test_known_preferred_required_fact_allows_nearest_same_fuel_boiler() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="gas-32",
                name="Котел газовый двухконтурный 32 кВт",
                category_path="Котлы газовые",
                stock_qty=2,
            ),
            Product(
                sku="electric-35",
                name="Котел электрический двухконтурный 35 кВт",
                category_path="Котлы электрические",
                stock_qty=3,
            ),
        ]
    )
    outcome = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                _fact("type", "газовый", None),
                _fact("needs_hot_water", True, None),
                _fact("power", 35, "kW", polarity="preferred"),
            ],
        ),
        snapshot,
    )

    plan = _planning(outcome).search_plans[0]
    assert plan.relaxed_skus == ("gas-32",)
    assert "electric-35" not in (*plan.eligible_skus, *plan.relaxed_skus)
    electric = next(
        item for item in plan.candidate_assessments
        if item.sku == "electric-35"
    )
    assert electric.status == CandidateStatus.REJECTED
    assert "boiler_type" in electric.mismatched_hard_facts


def test_boiler_power_relaxes_only_when_preferred_and_fuel_circuits_stay_hard() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="gas-32-two-circuit",
                name="Котел газовый двухконтурный 32 кВт",
                category_path="Котлы газовые",
                stock_qty=2,
            ),
            Product(
                sku="gas-35-one-circuit",
                name="Котел газовый одноконтурный 35 кВт",
                category_path="Котлы газовые",
                stock_qty=1,
            ),
            Product(
                sku="electric-35-two-circuit",
                name="Котел электрический двухконтурный 35 кВт",
                category_path="Котлы электрические",
                stock_qty=3,
            ),
        ]
    )
    invariant_constraints = [
        _fact("fuel_type", "газовый", None, polarity="preferred"),
        _fact("needs_hot_water", True, None, polarity="preferred"),
    ]
    preferred = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [
                *invariant_constraints,
                _fact("power", 35, "kW", polarity="preferred"),
            ],
        ),
        snapshot,
    )

    readiness = _planning(preferred).readiness_assessments[0]
    assert {item.name for item in readiness.confirmed_hard_facts} >= {
        "boiler_type",
        "circuits",
    }
    assert {item.name for item in readiness.confirmed_soft_facts} == {"power_kw"}
    preferred_plan = _planning(preferred).search_plans[0]
    assert preferred_plan.relaxed_skus == ("gas-32-two-circuit",)
    wrong_circuits = next(
        item
        for item in preferred_plan.candidate_assessments
        if item.sku == "gas-35-one-circuit"
    )
    wrong_fuel = next(
        item
        for item in preferred_plan.candidate_assessments
        if item.sku == "electric-35-two-circuit"
    )
    assert "circuits" in wrong_circuits.mismatched_hard_facts
    assert "boiler_type" in wrong_fuel.mismatched_hard_facts

    required = _run(
        _semantic(
            [_product("boiler", "boilers")],
            [*invariant_constraints, _fact("power", 35, "kW")],
        ),
        snapshot,
    )
    required_plan = _planning(required).search_plans[0]
    gas_nearest = next(
        item
        for item in required_plan.candidate_assessments
        if item.sku == "gas-32-two-circuit"
    )
    assert gas_nearest.status == CandidateStatus.REJECTED
    assert "power_kw" in gas_nearest.mismatched_hard_facts

    specialized_preferred = _run(
        _semantic(
            [_product("gas boiler", "boilers")],
            [
                _fact("needs_hot_water", True, None),
                _fact("power", 35, "kW", polarity="preferred"),
            ],
        ),
        snapshot,
    )
    specialized_plan = _planning(specialized_preferred).search_plans[0]
    assert specialized_plan.relaxed_skus == ("gas-32-two-circuit",)
    assert "electric-35-two-circuit" not in {
        item.sku for item in specialized_plan.candidate_assessments
    }


def test_brand_fact_is_source_preserving_and_required_or_preferred_by_polarity() -> None:
    registry = ProductContractRegistry()
    assert all(
        "brand" in {fact.name for fact in contract.fact_definitions}
        for contract in registry.contracts
    )
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="pipe-brand-a",
                name="Труба PPR 20 мм",
                category_path="Трубы",
                brand="Brand Alpha",
                description=(
                    "Для горячего водоснабжения. Давление при температуре "
                    "воды 70 °C — 10 бар."
                ),
            ),
            Product(
                sku="pipe-brand-b",
                name="Труба PPR 20 мм",
                category_path="Трубы",
                brand="Brand Beta",
                description=(
                    "Для горячего водоснабжения. Давление при температуре "
                    "воды 70 °C — 10 бар."
                ),
            ),
        ]
    )
    brand_fact = next(item for item in snapshot[0].facts if item.name == "brand")
    assert brand_fact.value == "brand alpha"
    assert brand_fact.provenance.source == "attribute"
    assert brand_fact.provenance.source_field == "brand"
    assert brand_fact.provenance.raw_value == "Brand Alpha"

    required = _run(
        _semantic(
            [_product("труба", "pipes")],
            [
                _fact("diameter", 20),
                *_pipe_required_constraints(),
                _fact("brand", "Brand Alpha", None),
            ],
        ),
        snapshot,
    )
    required_plan = _planning(required).search_plans[0]
    assert required_plan.eligible_skus == ("pipe-brand-a",)
    wrong_brand = next(
        item
        for item in required_plan.candidate_assessments
        if item.sku == "pipe-brand-b"
    )
    assert wrong_brand.status == CandidateStatus.REJECTED
    assert wrong_brand.mismatched_hard_facts == ("brand",)

    preferred = _run(
        _semantic(
            [_product("труба", "pipes")],
            [
                _fact("diameter", 20),
                *_pipe_required_constraints(),
                _fact("manufacturer", "Brand Alpha", None, polarity="preferred"),
            ],
        ),
        snapshot,
    )
    preferred_plan = _planning(preferred).search_plans[0]
    assert preferred_plan.eligible_skus == ("pipe-brand-a",)
    assert preferred_plan.relaxed_skus == ("pipe-brand-b",)
    relaxation = next(
        item.relaxations[0]
        for item in preferred_plan.candidate_assessments
        if item.sku == "pipe-brand-b"
    )
    assert relaxation.fact_name == "brand"
    assert relaxation.requested_value == "brand alpha"
    assert relaxation.candidate_value == "brand beta"


def test_pex_pipe_resolves_separately_and_never_matches_pex_tool() -> None:
    products = [
        Product(
            sku="pex-pipe-16",
            name="Труба полимерная PEX, c барьерным слоем, 16(2,0) бухта 200м",
            category_path="Трубы",
            stock_qty=1200,
            attributes_normalized={"тип товара": "Труба"},
            description=(
                "Для горячего водоснабжения. Давление при температуре "
                "воды 70 °C — 10 бар."
            ),
        ),
        Product(
            sku="PEX-16-tool",
            name="Расширительная насадка для инструмента PEX, диаметр 16",
            category_path="Инструменты",
            stock_qty=5,
        ),
    ]
    snapshot = build_catalog_snapshot(products)
    assert snapshot[0].product_kind == ProductKind.PEX_PIPE
    assert snapshot[0].role == CatalogProductRole.BASE_PRODUCT
    diameter = next(item for item in snapshot[0].facts if item.name == "diameter_mm")
    assert diameter.value == 16
    assert diameter.provenance.parser == "pipe_outer_diameter"
    assert snapshot[1].product_kind == ProductKind.UNSUPPORTED

    semantic_product = _product("pipe", "pipes")
    semantic_product["text"] = "труба PEX 16"
    semantic_product["evidence"] = "труба PEX 16"
    outcome = _run(
        _semantic(
            [semantic_product],
            [_fact("diameter", 16), *_pipe_required_constraints()],
        ),
        snapshot,
    )
    planning = _planning(outcome)
    assert planning.contract_resolutions[0].contract_id == "pipe.pex.v1"
    assert planning.contract_resolutions[0].product_kind == ProductKind.PEX_PIPE
    assert planning.search_plans[0].eligible_skus == ("pex-pipe-16",)
    assert "PEX-16-tool" not in planning.candidate_skus


def test_glycol_facts_remain_hard_and_missing_catalog_support_is_unverified() -> None:
    provenance = FactProvenance(
        source="attribute", source_field="test", raw_value="explicit", parser="test"
    )
    candidate = CatalogProductSnapshot(
        sku="pump-glycol-unverified",
        name="circulation pump",
        category="pumps",
        product_kind=ProductKind.CIRCULATION_PUMP,
        role=CatalogProductRole.BASE_PRODUCT,
        facts=(
            CatalogFact(name="diameter_mm", value=25, unit="mm", provenance=provenance),
            CatalogFact(name="max_head_m", value=4.5, unit="m", provenance=provenance),
            CatalogFact(name="mounting_length_mm", value=180, unit="mm", provenance=provenance),
        ),
    )
    outcome = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25),
                _fact("pressure", 4.5, "m"),
                _fact("mounting_length", 180),
                _fact("coolant", "propylene glycol", None),
                _fact("glycol_concentration", 30, "%"),
            ],
        ),
        [candidate],
    )
    assessment = _planning(outcome).readiness_assessments[0]
    values = {item.name: item.value for item in assessment.confirmed_hard_facts}
    assert values["coolant_type"] == "propylene_glycol"
    assert values["glycol_concentration_percent"] == 30
    candidate_assessment = _planning(outcome).search_plans[0].candidate_assessments[0]
    assert candidate_assessment.status == CandidateStatus.UNVERIFIED
    assert set(candidate_assessment.missing_hard_facts) == {
        "coolant_type",
        "glycol_concentration_percent",
    }


def test_pump_duty_point_requires_curve_and_is_never_compared_to_maxima() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="test",
        raw_value="explicit maximum values",
        parser="test",
    )
    candidate = CatalogProductSnapshot(
        sku="pump-maxima-only",
        name="circulation pump 25/6-180",
        category="pumps",
        product_kind=ProductKind.CIRCULATION_PUMP,
        role=CatalogProductRole.BASE_PRODUCT,
        facts=(
            CatalogFact(name="diameter_mm", value=25, unit="mm", provenance=provenance),
            CatalogFact(name="max_head_m", value=6, unit="m", provenance=provenance),
            CatalogFact(name="max_flow_l_h", value=3000, unit="l/h", provenance=provenance),
            CatalogFact(name="mounting_length_mm", value=180, unit="mm", provenance=provenance),
        ),
    )
    outcome = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25),
                _fact("mounting_length", 180),
                _fact("duty_point_head_m", 4.2, "m"),
                _fact("duty_point_flow_l_h", 1700, "l/h"),
            ],
        ),
        [candidate],
    )

    planning = _planning(outcome)
    readiness = planning.readiness_assessments[0]
    assert readiness.status == ReadinessStatus.PRELIMINARY_READY
    assert set(readiness.catalog_unverifiable_facts) == {
        "duty_point_head_m",
        "duty_point_flow_l_h",
    }
    assert readiness.recommended_question_fact is None
    assessed = planning.search_plans[0].candidate_assessments[0]
    assert assessed.status == CandidateStatus.UNVERIFIED
    assert set(assessed.missing_hard_facts) == {
        "duty_point_head_m",
        "duty_point_flow_l_h",
    }
    assert "max_head_m" not in assessed.mismatched_hard_facts
    assert "max_flow_l_h" not in assessed.mismatched_hard_facts


def test_real_water_filter_contract_separates_filter_from_cartridge() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="filter-1",
                name='Фильтр сетчатый 1/2"',
                category_path="Фильтры",
                attributes_normalized={"тип товара": "Фильтр"},
            ),
            Product(
                sku="cartridge-1",
                name="Картридж мех. очистки 5 мкм",
                category_path="Фильтры",
            ),
        ]
    )
    assert snapshot[0].product_kind == ProductKind.FILTER
    assert snapshot[0].role == CatalogProductRole.BASE_PRODUCT
    assert snapshot[1].product_kind == ProductKind.FILTER
    assert snapshot[1].role == CatalogProductRole.CONSUMABLE

    outcome = _run(
        _semantic(
            [_product("water_filter", "filters")],
            [
                _fact("filter_type", "механическая очистка", None),
                _fact("thread_size", "G1/2", None),
            ],
        ),
        snapshot,
    )
    planning = _planning(outcome)
    assert planning.contract_resolutions[0].product_kind == ProductKind.FILTER
    assert planning.readiness_assessments[0].status == ReadinessStatus.EXACT_READY
    plan = planning.search_plans[0]
    assert plan.eligible_skus == ("filter-1",)
    cartridge = next(item for item in plan.candidate_assessments if item.sku == "cartridge-1")
    assert cartridge.status == CandidateStatus.REJECTED
    assert cartridge.reason_codes == ("catalog_role_incompatible",)


def test_filter_fact_provenance_uses_description_when_name_has_no_method() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="filter-description",
                name='Фильтр линейный 1/2"',
                category_path="Фильтры",
                description="Для механической очистки частиц размером 50 мкм.",
            )
        ]
    )
    facts = {item.name: item for item in snapshot[0].facts}
    assert facts["filter_method"].value == "mechanical"
    assert facts["filter_method"].provenance.source == "description"
    assert facts["micron_rating_um"].value == 50
    assert facts["micron_rating_um"].provenance.source == "description"


def test_required_washable_filter_rejects_false_and_keeps_absent_unverified() -> None:
    common_attributes = {"тип товара": "Фильтр"}
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="filter-washable",
                name='Фильтр сетчатый 1/2"',
                category_path="Фильтры",
                description="Самопромывной фильтр для механической очистки.",
                attributes_normalized=common_attributes,
            ),
            Product(
                sku="filter-known-not-washable",
                name='Фильтр сетчатый непромывной 1/2"',
                category_path="Фильтры",
                attributes_normalized=common_attributes,
            ),
            Product(
                sku="filter-washability-absent",
                name='Фильтр сетчатый 1/2"',
                category_path="Фильтры",
                attributes_normalized=common_attributes,
            ),
        ]
    )
    facts_by_sku = {
        item.sku: {fact.name: fact for fact in item.facts}
        for item in snapshot
    }
    washable = facts_by_sku["filter-washable"]["washable"]
    assert washable.value is True
    assert washable.provenance.source == "description"
    assert facts_by_sku["filter-known-not-washable"]["washable"].value is False
    assert "washable" not in facts_by_sku["filter-washability-absent"]
    assert facts_by_sku["filter-washable"]["filter_method"].value == "mechanical"

    outcome = _run(
        _semantic(
            [_product("water_filter", "filters")],
            [
                _fact("filter_type", "mechanical", None),
                _fact("thread_size", "G1/2", None),
                _fact("self_cleaning", True, None),
            ],
        ),
        snapshot,
    )
    plan = _planning(outcome).search_plans[0]
    assert plan.eligible_skus == ("filter-washable",)
    known_false = next(
        item
        for item in plan.candidate_assessments
        if item.sku == "filter-known-not-washable"
    )
    absent = next(
        item
        for item in plan.candidate_assessments
        if item.sku == "filter-washability-absent"
    )
    assert known_false.status == CandidateStatus.REJECTED
    assert known_false.mismatched_hard_facts == ("washable",)
    assert absent.status == CandidateStatus.UNVERIFIED
    assert absent.missing_hard_facts == ("washable",)


def test_missing_required_hard_facts_fail_closed_for_executable_task(catalog) -> None:
    outcome = _run(
        _semantic(
            [_product("boiler", "boilers")],
            acts=["find", "check_price"],
        ),
        catalog,
    )
    planning = _planning(outcome)
    assert len(planning.search_plans) == 1
    plan = planning.search_plans[0]
    assert plan.task_id == outcome.next_action_plan.primary.task_id
    assert not plan.eligible_skus
    assert not plan.relaxed_skus
    assert not plan.unverified_skus
    assert not plan.candidate_assessments
    assert CatalogSearchStage.HONEST_NO_MATCH not in plan.stages
    assert "catalog_search_blocked_missing_required_hard_facts" in plan.reason_codes
    assert "duplicate_catalog_search_plans_deduplicated" not in planning.reason_codes


def test_preliminary_candidates_are_unverified_until_unknown_hard_facts_are_known(catalog) -> None:
    constraints = [
        _fact("connection_diameter", 25),
        _fact("duty_point_flow_l_h", 1500, "l/h"),
        _fact("duty_point_head_m", 4, "m"),
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
        set(item.reason_codes).intersection(
            {"required_customer_fact_unavailable", "catalogue_hard_fact_missing"}
        )
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
    assert outcome.next_action_plan.primary.kind == NextActionKind.RECOMMEND_ONE
    assert "VRS.256.13.0" in plan.eligible_skus
    rejected = next(item for item in plan.candidate_assessments if item.sku == "VRS.256.18.0")
    assert rejected.status == CandidateStatus.REJECTED
    assert "mounting_length_mm" in rejected.mismatched_hard_facts


def test_sewer_nearest_shorter_relaxes_only_length_and_requires_stock() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="test",
        raw_value="test",
        parser="test",
    )

    def sewer(sku: str, length: int, stock: int) -> CatalogProductSnapshot:
        return CatalogProductSnapshot(
            sku=sku,
            name=sku,
            category="sewer",
            product_kind=ProductKind.SEWER_PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="в наличии" if stock else "нет в наличии",
            stock_qty=stock,
            facts=(
                CatalogFact(name="diameter_mm", value=110, unit="mm", provenance=provenance),
                CatalogFact(name="length_mm", value=length, unit="mm", provenance=provenance),
                CatalogFact(name="sewer_scope", value="external", provenance=provenance),
            ),
        )

    snapshot = (
        sewer("EXACT-OUT", 3000, 0),
        sewer("SHORT-IN", 2000, 3),
        sewer("LONG-IN", 4000, 3),
    )
    outcome = _run(
        _semantic(
            [_product("sewer pipe", "sewer")],
            [
                _fact("diameter_mm", 110, "mm"),
                _fact("length_mm", 3000, "mm"),
                _fact("sewer_scope", "external", None),
            ],
            acts=["find"],
        ),
        snapshot,
    )
    task_id = outcome.next_action_plan.primary.task_id
    assert task_id is not None
    goal_id = outcome.state_after.active_goal_id
    state = outcome.state_after.model_copy(
        update={
            "selection_preferences": (
                SelectionPreferenceSignal(
                    preference_id="nearest-shorter",
                    kind=SelectionPreferenceKind.LENGTH_NEAREST_SHORTER,
                    # A follow-up FIND may be a new discovery task for the
                    # same sewer goal.  The explicit engineering relaxation
                    # belongs to that goal and must not disappear merely
                    # because SELECT/FIND task identity changed.
                    task_id="earlier-selection-task",
                    goal_id=goal_id,
                    value=3000,
                    evidence="ближайшую короче",
                    source="test",
                    source_turn=2,
                ),
            )
        }
    )
    planning = plan_catalog_search(
        state,
        outcome.next_action_plan,
        outcome.catalog_planning.readiness_assessments,
        snapshot,
        ProductContractRegistry(),
        contract_resolutions=outcome.catalog_planning.contract_resolutions,
    )
    plan = planning.search_plans[0]

    assert plan.in_stock_required is True
    assert plan.relaxed_skus == ("SHORT-IN",)
    assert "EXACT-OUT" not in (*plan.eligible_skus, *plan.relaxed_skus)
    longer = next(item for item in plan.candidate_assessments if item.sku == "LONG-IN")
    shorter = next(item for item in plan.candidate_assessments if item.sku == "SHORT-IN")
    assert longer.status == CandidateStatus.REJECTED
    assert "controlled_relaxation_rejects_longer_length" in longer.reason_codes
    assert shorter.controlled_customer_relaxation is True
    assert shorter.relaxations[0].candidate_value == 2000


def test_continue_with_confirmed_facts_still_runs_catalogue_verification(catalog) -> None:
    outcome = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25, "mm"),
                _fact("max_head_m", 6, "m"),
                _fact("mounting_length", 130, "mm"),
            ],
        ),
        catalog,
    )
    task_id = outcome.next_action_plan.primary.task_id
    assert task_id is not None

    continued = plan_catalog_search(
        outcome.state_after,
        NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.CONTINUE_WITH_CONFIRMED_FACTS,
                task_id=task_id,
                reason_code="loop_recovery_after_explicit_find",
            ),
            task_ids=(task_id,),
        ),
        _planning(outcome).readiness_assessments,
        catalog,
        ProductContractRegistry(),
        contract_resolutions=_planning(outcome).contract_resolutions,
    )

    assert continued.search_plans
    assert continued.candidate_skus


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
        _fact("duty_point_flow_l_h", 1500, "l/h"),
        _fact("duty_point_head_m", 4, "m"),
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
        *_pipe_required_constraints(),
        _fact("reinforcement", "aluminium", None, polarity="preferred"),
    ]
    outcome = _run(_semantic([_product("труба", "pipes")], constraints), catalog)
    plan = _planning(outcome).search_plans[0]
    assert plan.relaxed_skus
    candidate = next(
        item
        for item in plan.candidate_assessments
        if item.relaxations and item.relaxations[0].candidate_value is not None
    )
    assert len(candidate.relaxations) == 1
    assert candidate.relaxations[0].reason_code == "soft_preference_differs"
    assert candidate.relaxations[0].candidate_value is not None


def test_two_products_create_independent_plans_and_solution_without_quantity(catalog) -> None:
    outcome = _run(
        _semantic(
            [_product("труба", "pipes"), _product("шаровой кран", "valves")],
            [
                _fact("diameter", 20, product=0),
                *_pipe_required_constraints(product=0),
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


def test_catalog_plans_only_tasks_with_current_executable_catalog_actions(catalog) -> None:
    """Linked readiness is continuity, not permission to re-run an old task."""

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
    )
    initial = _planning(outcome)
    readiness_by_kind = {
        item.product_kind: item for item in initial.readiness_assessments
    }
    pipe = readiness_by_kind[ProductKind.PIPE]
    valve = readiness_by_kind[ProductKind.BALL_VALVE]

    # Mirrors a follow-up after a delivered question: dialogue continuity
    # still names both tasks, but only the valve has an executable action now.
    valve_only = plan_catalog_search(
        outcome.state_after,
        NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.SEARCH_EXACT,
                task_id=valve.task_id,
                reason_code="current_task_exact_ready",
            ),
            task_ids=(pipe.task_id, valve.task_id),
        ),
        initial.readiness_assessments,
        catalog,
        ProductContractRegistry(),
        solution_enabled=True,
        contract_resolutions=initial.contract_resolutions,
    )

    assert [plan.task_id for plan in valve_only.search_plans] == [valve.task_id]
    assert all(
        plan.product_kind == ProductKind.BALL_VALVE
        for plan in valve_only.search_plans
    )
    assert valve_only.solution_plan is None
    # Readiness for linked work remains available for telemetry and a later
    # explicit return; it simply does not execute on this turn.
    assert valve_only.readiness_assessments == initial.readiness_assessments

    both_actions = plan_catalog_search(
        outcome.state_after,
        NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.SEARCH_EXACT,
                task_id=pipe.task_id,
                reason_code="first_component_ready",
            ),
            secondary=NextAction(
                kind=NextActionKind.SEARCH_EXACT,
                task_id=valve.task_id,
                reason_code="second_component_ready",
            ),
            task_ids=(pipe.task_id, valve.task_id),
        ),
        initial.readiness_assessments,
        catalog,
        ProductContractRegistry(),
        solution_enabled=True,
        contract_resolutions=initial.contract_resolutions,
    )

    assert {plan.task_id for plan in both_actions.search_plans} == {
        pipe.task_id,
        valve.task_id,
    }
    assert both_actions.solution_plan is not None


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


def test_typed_stock_requirement_filters_candidates_without_reading_text() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    common = (
        CatalogFact(
            name="diameter_mm",
            value=25,
            unit="mm",
            provenance=provenance,
        ),
        *_pipe_catalog_required_facts(provenance),
    )
    snapshot = (
        CatalogProductSnapshot(
            sku="PIPE-OUT",
            name="Pipe 25 unavailable",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="нет в наличии",
            stock_qty=0,
            facts=common,
        ),
        CatalogProductSnapshot(
            sku="PIPE-IN",
            name="Pipe 25 available",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="в наличии",
            stock_qty=3,
            facts=common,
        ),
    )

    outcome = _run(
        _semantic(
            [_product("pipe", "pipes")],
            [
                _fact("diameter", 25),
                *_pipe_required_constraints(),
                _fact("stock_availability", True, None),
            ],
            acts=["select", "check_stock"],
        ),
        snapshot,
    )
    planning = _planning(outcome)
    plan = planning.search_plans[0]
    by_sku = {item.sku: item for item in plan.candidate_assessments}

    assert plan.in_stock_required is True
    assert planning.candidate_skus == ("PIPE-IN",)
    assert plan.eligible_skus == ("PIPE-IN",)
    assert by_sku["PIPE-IN"].availability_status == CatalogAvailabilityStatus.IN_STOCK
    assert by_sku["PIPE-OUT"].status == CandidateStatus.REJECTED
    assert "required_stock_unavailable" in by_sku["PIPE-OUT"].reason_codes


def test_stock_question_keeps_exact_out_of_stock_candidate_with_honest_status() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    snapshot = (
        CatalogProductSnapshot(
            sku="PIPE-OUT",
            name="Pipe 25 unavailable",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="нет в наличии",
            stock_qty=0,
            facts=(
                CatalogFact(
                    name="diameter_mm",
                    value=25,
                    unit="mm",
                    provenance=provenance,
                ),
                *_pipe_catalog_required_facts(provenance),
            ),
        ),
    )

    outcome = _run(
        _semantic(
            [_product("pipe", "pipes")],
            [_fact("diameter", 25), *_pipe_required_constraints()],
            acts=["select", "check_stock"],
        ),
        snapshot,
    )
    plan = _planning(outcome).search_plans[0]

    assert plan.in_stock_required is False
    assert plan.eligible_skus == ("PIPE-OUT",)
    assessment = plan.candidate_assessments[0]
    assert assessment.status == CandidateStatus.ELIGIBLE
    assert assessment.availability_status == CatalogAvailabilityStatus.OUT_OF_STOCK
    assert "no_verified_in_stock_contract_match" not in plan.reason_codes
    assert _planning(outcome).candidate_skus == ("PIPE-OUT",)


def test_stock_requirement_persists_for_related_goal_after_followup() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    common = (
        CatalogFact(
            name="diameter_mm",
            value=25,
            unit="mm",
            provenance=provenance,
        ),
        *_pipe_catalog_required_facts(provenance),
    )
    snapshot = (
        CatalogProductSnapshot(
            sku="PIPE-OUT",
            name="Pipe 25 unavailable",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="нет в наличии",
            stock_qty=0,
            facts=common,
        ),
        CatalogProductSnapshot(
            sku="PIPE-IN",
            name="Pipe 25 available",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="в наличии",
            stock_qty=3,
            facts=common,
        ),
    )
    controller = DialogueControllerV2()
    first = controller.run(
        None,
        _semantic(
            [_product("pipe", "pipes")],
            [
                _fact("diameter", 25),
                *_pipe_required_constraints(),
                _fact("stock_availability", True, None),
            ],
            acts=["select", "check_stock"],
        ),
        TurnMetadata(turn_id="stock-goal-turn-1"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )
    continuation = _semantic([], acts=["select"])
    continuation = continuation.model_copy(
        update={
            "understanding": continuation.understanding.model_copy(
                update={"operation": GoalOperation.CONTINUE}
            )
        }
    )

    second = controller.run(
        first.state_after,
        continuation,
        TurnMetadata(turn_id="stock-goal-turn-2"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )

    plan = _planning(second).search_plans[0]
    assert plan.in_stock_required is True
    assert plan.eligible_skus == ("PIPE-IN",)
    assert "PIPE-OUT" not in _planning(second).candidate_skus


def test_explicit_stock_relaxation_removes_filter_for_same_goal() -> None:
    provenance = FactProvenance(
        source="attribute",
        source_field="diameter",
        raw_value="25",
        parser="test",
    )
    snapshot = (
        CatalogProductSnapshot(
            sku="PIPE-OUT",
            name="Pipe 25 unavailable",
            category="pipes",
            product_kind=ProductKind.PIPE,
            role=CatalogProductRole.BASE_PRODUCT,
            stock_status="нет в наличии",
            stock_qty=0,
            facts=(
                CatalogFact(
                    name="diameter_mm",
                    value=25,
                    unit="mm",
                    provenance=provenance,
                ),
                *_pipe_catalog_required_facts(provenance),
            ),
        ),
    )
    controller = DialogueControllerV2()
    first = controller.run(
        None,
        _semantic(
            [_product("pipe", "pipes")],
            [
                _fact("diameter", 25),
                *_pipe_required_constraints(),
                _fact("stock_availability", True, None),
            ],
            acts=["select", "check_stock"],
        ),
        TurnMetadata(turn_id="stock-relax-turn-1"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )
    relaxation = _semantic(
        [],
        [
            _fact(
                "stock_availability",
                True,
                None,
                polarity="excluded",
                product=None,
            )
        ],
        acts=["select"],
    )
    relaxation = relaxation.model_copy(
        update={
            "understanding": relaxation.understanding.model_copy(
                update={"operation": GoalOperation.REFINE}
            )
        }
    )

    second = controller.run(
        first.state_after,
        relaxation,
        TurnMetadata(turn_id="stock-relax-turn-2"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
    )

    plan = _planning(second).search_plans[0]
    assert plan.in_stock_required is False
    assert plan.eligible_skus == ("PIPE-OUT",)
    active_stock_facts = [
        fact
        for fact in second.state_after.constraints
        if fact.active and fact.name == "stock_availability"
    ]
    assert len(active_stock_facts) == 1
    assert active_stock_facts[0].polarity.value == "excluded"


def test_stock_requirement_is_scoped_to_its_product_goal() -> None:
    state = DialogueStateV2(
        constraints=(
            ConstraintFactV2(
                fact_id="stock-pipe",
                name="stock_availability",
                value=True,
                status=ConstraintStatus.KNOWN,
                polarity=ConstraintPolarity.REQUIRED,
                strength=ConstraintStrength.HARD,
                evidence="из наличия",
                source="semantic_interpreter",
                confidence=0.95,
                goal_id="goal-pipe",
                task_id="task-pipe",
                source_turn=1,
            ),
        )
    )
    pipe = TaskReadinessAssessment(
        task_id="task-pipe",
        goal_id="goal-pipe",
        status=ReadinessStatus.EXACT_READY,
    )
    boiler = TaskReadinessAssessment(
        task_id="task-boiler",
        goal_id="goal-boiler",
        status=ReadinessStatus.EXACT_READY,
    )

    assert _requires_in_stock_candidates(state, pipe) is True
    assert _requires_in_stock_candidates(state, boiler) is False


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


def _pump_with_structured_mounting_length(raw_length: str) -> Product:
    return Product(
        sku=f"pump-length-{raw_length}",
        name="Насос циркуляционный Model 25/6-130(180)",
        category_path="Насосное оборудование",
        attributes_normalized={
            "тип товара": "Насос",
            "диаметр (мм)": "25",
            "максимальный напор, м": "6",
            "монтажная длина, мм": raw_length,
        },
    )


@pytest.mark.parametrize(
    ("designation", "expected"),
    [
        (
            "ALPHA2 25-40 180",
            {"diameter_mm": 25, "max_head_m": 4, "mounting_length_mm": 180},
        ),
        (
            "Yonos 25/6-130",
            {"diameter_mm": 25, "max_head_m": 6, "mounting_length_mm": 130},
        ),
        ("Stratos 30/1-8", {"diameter_mm": 30, "max_head_m": 8}),
    ],
)
def test_common_circulation_pump_designations_are_parsed(
    designation: str,
    expected: dict[str, int],
) -> None:
    assert {
        name: value
        for name, (value, _unit, _evidence) in parse_pump_designation(
            designation
        ).items()
    } == expected


def test_parenthesised_alternative_pump_length_remains_ambiguous() -> None:
    facts = parse_pump_designation("Model 25/6-130(180)")

    assert facts["diameter_mm"][0] == 25
    assert facts["max_head_m"][0] == 6
    assert "mounting_length_mm" not in facts


@pytest.mark.parametrize("raw_length", ("130-180", "130(180)"))
def test_ambiguous_structured_scalar_length_is_not_reduced_to_one_endpoint(
    raw_length: str,
) -> None:
    snapshot = build_catalog_snapshot(
        [_pump_with_structured_mounting_length(raw_length)]
    )
    facts = {item.name: item for item in snapshot[0].facts}

    assert facts["diameter_mm"].value == 25
    assert facts["max_head_m"].value == 6
    assert "mounting_length_mm" not in facts
    assert len(snapshot[0].fact_issues) == 1
    issue = snapshot[0].fact_issues[0]
    assert issue.name == "mounting_length_mm"
    assert issue.status == "ambiguous"
    assert issue.provenance.raw_value == raw_length
    assert issue.provenance.parser == "structured_attribute_ambiguous"


def test_ordinary_structured_scalar_length_remains_exact() -> None:
    snapshot = build_catalog_snapshot(
        [_pump_with_structured_mounting_length("180")]
    )
    fact = next(
        item for item in snapshot[0].facts if item.name == "mounting_length_mm"
    )

    assert fact.value == 180
    assert fact.unit == "mm"
    assert fact.provenance.raw_value == "180"


def test_ambiguous_catalogue_scalar_is_unverified_for_hard_match() -> None:
    snapshot = build_catalog_snapshot(
        [_pump_with_structured_mounting_length("130-180")]
    )
    outcome = _run(
        _semantic(
            [_product("циркуляционный насос", "pumps")],
            [
                _fact("connection_diameter", 25, "mm"),
                _fact("max_head_m", 6, "m"),
                _fact("mounting_length", 130, "mm"),
            ],
        ),
        snapshot,
    )
    candidate = _planning(outcome).search_plans[0].candidate_assessments[0]

    assert candidate.status == CandidateStatus.UNVERIFIED
    assert candidate.missing_hard_facts == ("mounting_length_mm",)
    assert candidate.mismatched_hard_facts == ()


def test_compound_pipe_dimension_keeps_outer_diameter_parser() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="pex-compound-dimension",
                name="Труба PEX без размерного обозначения",
                category_path="Трубы",
                attributes_normalized={
                    "тип товара": "Труба",
                    "диаметр (мм)": "16x2.2",
                },
            )
        ]
    )
    diameter = next(
        item for item in snapshot[0].facts if item.name == "diameter_mm"
    )

    assert diameter.value == 16
    assert diameter.provenance.raw_value == "16x2.2"


def test_generic_primary_and_secondary_dimension_parsers_remain_distinct() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="tee-compound-dimension",
                name="Тройник канализационный 50x32",
                category_path="Канализационные системы",
                attributes_normalized={"тип товара": "Тройник"},
            )
        ]
    )
    facts = {item.name: item.value for item in snapshot[0].facts}

    assert facts["diameter_mm"] == 50
    assert facts["secondary_diameter_mm"] == 32


def test_ball_valve_handle_is_a_typed_card_fact_only_when_explicit_in_title() -> None:
    snapshot = build_catalog_snapshot(
        [
            Product(
                sku="VT.217.N.04",
                name='Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.',
                category_path="Водозапорная арматура",
                attributes_normalized={"тип товара": "Кран шаровой"},
            )
        ]
    )

    handle = next(item for item in snapshot[0].facts if item.name == "handle_type")

    assert snapshot[0].product_kind == ProductKind.BALL_VALVE
    assert handle.value == "рукоятка бабочка"
    assert handle.provenance.source == "name"
    assert handle.provenance.raw_value == "рукоятка бабочка"
