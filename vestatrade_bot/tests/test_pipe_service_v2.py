from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.semantic_interpreter import (
    SemanticInterpretationResult,
    TurnUnderstanding,
)
from app.catalog_v2.contracts import (
    CandidateStatus,
    FactStrength,
    ProductKind,
    ReadinessStatus,
)
from app.catalog_v2.normalization import build_catalog_snapshot, normalize_fact_value
from app.catalog_v2.readiness import assess_task_readiness
from app.catalog_v2.registry import DEFAULT_CONTRACTS
from app.dialogue_v2.contracts import DialogueStateV2, NextActionKind, TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.dialogue_v2.reducer import reduce_dialogue_state
from app.feed_loader import FeedLoader
from app.models import Product


_PIPE_RATINGS = {
    "максимальная рабочая температура, °с": "95",
    "максимальное рабочее давление, бар": "10",
}

_FEED100 = Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml"


def _feed_pipe(
    sku: str,
    name: str,
    rating_text: str,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Трубы",
        description=(
            "Для холодного и горячего водоснабжения и отопления. "
            f"{rating_text}"
        ),
    )


def _semantic_for_hot_water_pipe(
    service: str = "горячая вода",
) -> SemanticInterpretationResult:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.2",
            "language": "ru",
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
                    "name": "diameter_mm",
                    "value": 20,
                    "unit": "mm",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "20 мм",
                },
                {
                    # Preserve compatibility with names emitted by older
                    # semantic-model prompts; the product contract owns the
                    # canonical mapping.
                    "name": "application_type",
                    "value": service,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": service,
                },
                {
                    "name": "operating_temperature_c",
                    "value": 80,
                    "unit": "C",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "80 градусов",
                },
                {
                    "name": "operating_pressure_bar",
                    "value": 6,
                    "unit": "bar",
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": "6 бар",
                },
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [],
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.98,
        }
    )
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def _semantic_for_ppr(
    constraints: list[dict[str, object]],
    *,
    continue_with_confirmed_facts: bool = False,
) -> SemanticInterpretationResult:
    control = (
        [
            {
                "kind": "continue_with_confirmed_facts",
                "evidence": "покажите по известным данным",
            }
        ]
        if continue_with_confirmed_facts
        else []
    )
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
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
            "constraints": constraints,
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": control,
            "selection_strategy": {
                "kind": (
                    "continue_with_confirmed_facts"
                    if continue_with_confirmed_facts
                    else "standard"
                ),
                "evidence": (
                    "покажите по известным данным"
                    if continue_with_confirmed_facts
                    else None
                ),
            },
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.98,
        }
    )
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def _constraint(
    name: str,
    value: object,
    *,
    status: str = "known",
    unit: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "status": status,
        "polarity": "required",
        "applies_to_product": 0,
        "evidence": f"{name}: {value if value is not None else status}",
    }


def _ppr_readiness(
    constraints: list[dict[str, object]],
    *,
    continue_with_confirmed_facts: bool = False,
):
    reduction = reduce_dialogue_state(
        DialogueStateV2(),
        _semantic_for_ppr(
            constraints,
            continue_with_confirmed_facts=continue_with_confirmed_facts,
        ).understanding,
        TurnMetadata(turn_id="ppr-readiness"),
    )
    task = reduction.state.tasks[0]
    contract = next(
        item for item in DEFAULT_CONTRACTS if item.contract_id == "pipe.ppr.v1"
    )
    return assess_task_readiness(reduction.state, task, contract)


def test_pipe_service_normalization_preserves_hot_and_cold_distinction() -> None:
    assert normalize_fact_value("pipe_service", "для горячей воды") == "hot_water"
    assert normalize_fact_value("pipe_service", "для холодной воды") == "cold_water"
    assert normalize_fact_value(
        "pipe_service",
        "холодное и горячее водоснабжение, отопление",
    ) == "cold_water hot_water heating"


def test_pipe_reinforcement_canonical_values_and_synonyms_are_stable() -> None:
    assert normalize_fact_value("reinforcement", "glass_fiber") == "glass_fiber"
    assert normalize_fact_value("reinforcement", "стекловолокно") == "glass_fiber"
    assert normalize_fact_value("reinforcement", "aluminium") == "aluminium"
    assert normalize_fact_value("reinforcement", "PP-ALUX") == "aluminium"
    assert normalize_fact_value("reinforcement", "unreinforced") == "unreinforced"


def test_pipe_operating_point_requires_one_explicit_temperature_pressure_pair() -> None:
    snapshot = build_catalog_snapshot(
        (
            _feed_pipe(
                "PLAIN-20",
                "Труба PN 20, 20 MM",
                (
                    "Допустимое рабочее давление при температуре воды "
                    "70 °C — 10 бар, для холодной воды — 20 бар."
                ),
            ),
            _feed_pipe(
                "MULTI-POINT",
                "Труба PP-FIBER PN 20, 20 MM",
                (
                    "При температуре 70 °C допустимо 10 бар. "
                    "При температуре 90 °C допустимо 6 бар."
                ),
            ),
            _feed_pipe(
                "PN-ONLY",
                "Труба PP-ALUX PN 25, 20 MM",
                "Номинальное давление для холодной воды — 25 бар.",
            ),
            Product(
                sku="PARTIAL-STRUCTURED",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description=(
                    "Для горячего водоснабжения. Давление при температуре "
                    "воды 70 °C — 10 бар."
                ),
                attributes_normalized={
                    "максимальная рабочая температура, °с": "95",
                },
            ),
        )
    )
    facts = {
        item.sku: {fact.name: fact for fact in item.facts}
        for item in snapshot
    }

    assert facts["PLAIN-20"]["operating_temperature_c"].value == 70
    assert facts["PLAIN-20"]["operating_pressure_bar"].value == 10
    assert facts["PLAIN-20"]["operating_pressure_bar"].provenance.parser == (
        "pipe_single_operating_point"
    )
    assert "operating_temperature_c" not in facts["MULTI-POINT"]
    assert "operating_pressure_bar" not in facts["MULTI-POINT"]
    assert "operating_temperature_c" not in facts["PN-ONLY"]
    assert "operating_pressure_bar" not in facts["PN-ONLY"]
    assert facts["PARTIAL-STRUCTURED"]["operating_temperature_c"].value == 95
    assert "operating_pressure_bar" not in facts["PARTIAL-STRUCTURED"]


@pytest.mark.parametrize(
    ("temperature", "pressure", "eligible"),
    [
        (70, 10, {"PLAIN-20"}),
        (90, 6, {"FB20-20", "FB25-20"}),
        (80, 8, {"FB25-20"}),
    ],
)
def test_feed_pipe_operating_pairs_filter_without_using_pn_as_hot_pressure(
    temperature: int,
    pressure: int,
    eligible: set[str],
) -> None:
    catalog = build_catalog_snapshot(
        (
            _feed_pipe(
                "PLAIN-20",
                "Труба PN 20, 20 MM",
                "Допустимое давление при температуре воды 70 °C — 10 бар.",
            ),
            _feed_pipe(
                "FB20-20",
                "Труба PP-FIBER арм. стекл., PN 20, 20 MM",
                "Рабочее давление при температуре теплоносителя 90 °C — 6 бар.",
            ),
            _feed_pipe(
                "FB25-20",
                "Труба PP-FIBER арм. стекл., PN 25, 20 MM",
                "Рабочее давление при температуре теплоносителя 90 °C — 9 бар.",
            ),
            _feed_pipe(
                "ALUX-20",
                "Труба PP-ALUX, арм. алюминием, PN 25, 20 MM",
                "Номинальное давление холодной воды — 25 бар.",
            ),
        )
    )
    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_ppr(
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", temperature, unit="C"),
                _constraint("operating_pressure_bar", pressure, unit="bar"),
            ]
        ),
        TurnMetadata(turn_id=f"pipe-point-{temperature}-{pressure}"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert {
        sku for sku, item in candidates.items()
        if item.status == CandidateStatus.ELIGIBLE
    } == eligible
    assert candidates["ALUX-20"].status == CandidateStatus.UNVERIFIED


def test_explicit_cold_water_pressure_is_used_only_for_exclusive_cold_service() -> None:
    catalog = build_catalog_snapshot(
        (
            _feed_pipe(
                "COLD-EXPLICIT",
                "Труба PN 20, 20 MM",
                (
                    "Давление при температуре воды 70 °C — 10 бар, "
                    "при транспортировке холодной воды — 20 бар."
                ),
            ),
            _feed_pipe(
                "COLD-UNSPECIFIED",
                "Труба PN 20, 20 MM без отдельного рейтинга ХВС",
                "Давление при температуре воды 70 °C — 10 бар.",
            ),
        )
    )

    def candidates_for(service: str):
        outcome = DialogueControllerV2().run(
            DialogueStateV2(),
            _semantic_for_ppr(
                [
                    _constraint("pipe_service", service),
                    _constraint("diameter_mm", 20, unit="mm"),
                    _constraint("operating_temperature_c", 20, unit="C"),
                    _constraint("operating_pressure_bar", 15, unit="bar"),
                ]
            ),
            TurnMetadata(turn_id=f"pipe-cold-rating-{service}"),
            product_contracts_enabled=True,
            catalog_planner_enabled=True,
            catalog_snapshot=catalog,
        )
        assert outcome.catalog_planning is not None
        return {
            item.sku: item
            for item in outcome.catalog_planning.search_plans[0].candidate_assessments
        }

    cold = candidates_for("холодная вода")
    assert cold["COLD-EXPLICIT"].status == CandidateStatus.ELIGIBLE
    assert cold["COLD-UNSPECIFIED"].status == CandidateStatus.REJECTED

    hot = candidates_for("горячая вода")
    assert hot["COLD-EXPLICIT"].status == CandidateStatus.REJECTED
    assert "operating_pressure_bar" in hot["COLD-EXPLICIT"].mismatched_hard_facts


def test_glass_fiber_requirement_filters_pipe_series_without_guessing_plain() -> None:
    catalog = build_catalog_snapshot(
        (
            _feed_pipe(
                "PLAIN-20",
                "Труба PN 20, 20 MM",
                "Допустимое давление при температуре воды 70 °C — 10 бар.",
            ),
            _feed_pipe(
                "FB20-20",
                "Труба PP-FIBER арм. стекл., PN 20, 20 MM",
                "Рабочее давление при температуре теплоносителя 90 °C — 6 бар.",
            ),
            _feed_pipe(
                "FB25-20",
                "Труба PP-FIBER арм. стекл., PN 25, 20 MM",
                "Рабочее давление при температуре теплоносителя 90 °C — 9 бар.",
            ),
            _feed_pipe(
                "ALUX-20",
                "Труба PP-ALUX, арм. алюминием, PN 25, 20 MM",
                "Рабочее давление при температуре теплоносителя 90 °C — 9 бар.",
            ),
        )
    )
    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_ppr(
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", 70, unit="C"),
                _constraint("operating_pressure_bar", 6, unit="bar"),
                _constraint("reinforcement", "стекловолокно"),
            ]
        ),
        TurnMetadata(turn_id="pipe-glass-fiber"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["FB20-20"].status == CandidateStatus.ELIGIBLE
    assert candidates["FB25-20"].status == CandidateStatus.ELIGIBLE
    assert candidates["PLAIN-20"].status == CandidateStatus.UNVERIFIED
    assert "reinforcement" in candidates["PLAIN-20"].missing_hard_facts
    assert candidates["ALUX-20"].status == CandidateStatus.REJECTED
    assert "reinforcement" in candidates["ALUX-20"].mismatched_hard_facts


def test_ppr_material_keeps_plain_fiber_and_alux_as_same_base_material() -> None:
    catalog = build_catalog_snapshot(
        tuple(
            _feed_pipe(sku, name, rating)
            for sku, name, rating in (
                (
                    "PLAIN-20",
                    "Труба полипропиленовая PN 20, 20 MM",
                    "Давление при температуре воды 70 °C — 10 бар.",
                ),
                (
                    "FB20-20",
                    "Труба PP-FIBER арм. стекл., PN 20, 20 MM",
                    "Давление при температуре воды 70 °C — 10 бар.",
                ),
                (
                    "ALUX-20",
                    "Труба PP-ALUX, арм. алюминием, PN 25, 20 MM",
                    "Давление при температуре воды 70 °C — 10 бар.",
                ),
            )
        )
    )
    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_ppr(
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", 70, unit="C"),
                _constraint("operating_pressure_bar", 6, unit="bar"),
                _constraint("material", "полипропиленовая"),
            ]
        ),
        TurnMetadata(turn_id="pipe-ppr-material"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert {
        sku for sku, item in candidates.items()
        if item.status == CandidateStatus.ELIGIBLE
    } == {"PLAIN-20", "FB20-20", "ALUX-20"}


def test_pressure_class_normalization_filters_pn20_from_pn25() -> None:
    catalog = build_catalog_snapshot(
        (
            _feed_pipe(
                "PN20-20",
                "Труба полипропиленовая PN 20, 20 MM",
                "Давление при температуре воды 70 °C — 10 бар.",
            ),
            _feed_pipe(
                "PN25-20",
                "Труба PP-FIBER ПН 25, 20 MM",
                "Давление при температуре воды 70 °C — 10 бар.",
            ),
        )
    )
    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_ppr(
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", 70, unit="C"),
                _constraint("operating_pressure_bar", 6, unit="bar"),
                _constraint("pressure_class", "ПН 20"),
            ]
        ),
        TurnMetadata(turn_id="pipe-pn20"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["PN20-20"].status == CandidateStatus.ELIGIBLE
    assert candidates["PN25-20"].status == CandidateStatus.REJECTED
    assert "pressure_class" in candidates["PN25-20"].mismatched_hard_facts


def test_ppr_unknown_diameter_still_asks_missing_service_first() -> None:
    assessment = _ppr_readiness(
        [_constraint("diameter_mm", None, status="unknown")]
    )

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "pipe_service"
    assert assessment.unknown_facts == ("diameter_mm",)


def test_terminal_diameter_does_not_skip_later_pipe_questions() -> None:
    assessment = _ppr_readiness(
        [
            _constraint("pipe_service", "горячая вода"),
            _constraint("diameter_mm", None, status="unknown"),
        ]
    )

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "operating_temperature_c"
    assert assessment.unknown_facts == ("diameter_mm",)


def test_explicit_confirmed_facts_control_skips_all_remaining_pipe_questions() -> None:
    assessment = _ppr_readiness(
        [_constraint("diameter_mm", None, status="unknown")],
        continue_with_confirmed_facts=True,
    )

    assert assessment.status == ReadinessStatus.PRELIMINARY_READY
    assert assessment.recommended_question_fact is None
    assert assessment.missing_decision_facts == (
        "pipe_service",
        "operating_temperature_c",
        "operating_pressure_bar",
    )
    assert assessment.unknown_facts == ("diameter_mm",)
    assert "customer_requested_confirmed_facts_only" in assessment.reason_codes


def test_unknown_temperature_with_missing_pressure_asks_pressure_next() -> None:
    assessment = _ppr_readiness(
        [
            _constraint("pipe_service", "горячая вода"),
            _constraint("diameter_mm", 20, unit="mm"),
            _constraint(
                "operating_temperature_c",
                None,
                status="unknown",
                unit="C",
            ),
        ]
    )

    assert assessment.status == ReadinessStatus.NEEDS_DECISION_FACT
    assert assessment.recommended_question_fact == "operating_pressure_bar"
    assert assessment.unknown_facts == ("operating_temperature_c",)


def test_terminal_temperature_and_pressure_allow_preliminary_search() -> None:
    assessment = _ppr_readiness(
        [
            _constraint("pipe_service", "горячая вода"),
            _constraint("diameter_mm", 20, unit="mm"),
            _constraint("operating_temperature_c", None, status="unknown"),
            _constraint("operating_pressure_bar", None, status="deferred"),
        ]
    )

    assert assessment.status == ReadinessStatus.PRELIMINARY_READY
    assert assessment.recommended_question_fact is None
    assert assessment.unknown_facts == ("operating_temperature_c",)
    assert assessment.deferred_facts == ("operating_pressure_bar",)


def test_all_required_ppr_facts_are_exact_ready() -> None:
    assessment = _ppr_readiness(
        [
            _constraint("pipe_service", "горячая вода"),
            _constraint("diameter_mm", 20, unit="mm"),
            _constraint("operating_temperature_c", 80, unit="C"),
            _constraint("operating_pressure_bar", 6, unit="bar"),
        ]
    )

    assert assessment.status == ReadinessStatus.EXACT_READY
    assert assessment.missing_decision_facts == ()
    assert assessment.catalog_unverifiable_facts == ()


def test_ppr_and_pex_require_service_diameter_temperature_and_pressure() -> None:
    contracts = {
        item.contract_id: item
        for item in DEFAULT_CONTRACTS
        if item.contract_id in {"pipe.ppr.v1", "pipe.pex.v1"}
    }

    for contract in contracts.values():
        required = [
            item.name for item in contract.fact_definitions if item.required_for_exact
        ]
        definitions = {item.name: item for item in contract.fact_definitions}
        assert required[:4] == [
            "pipe_service",
            "diameter_mm",
            "operating_temperature_c",
            "operating_pressure_bar",
        ]
        for name in ("operating_temperature_c", "operating_pressure_bar"):
            assert definitions[name].strength == FactStrength.HARD
            assert definitions[name].required_for_exact is True
            assert definitions[name].decision_changing is True
            assert definitions[name].learn_method_code


@pytest.mark.parametrize(
    (
        "constraints",
        "continue_with_confirmed_facts",
        "expected_status",
        "expected_question",
        "expected_action",
        "expects_search_plan",
    ),
    [
        (
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
            ],
            False,
            ReadinessStatus.NEEDS_DECISION_FACT,
            "operating_temperature_c",
            NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            False,
        ),
        (
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", None, status="unknown"),
            ],
            False,
            ReadinessStatus.NEEDS_DECISION_FACT,
            "operating_pressure_bar",
            NextActionKind.ASK_DECISION_CHANGING_QUESTION,
            False,
        ),
        (
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
                _constraint("operating_temperature_c", None, status="unknown"),
                _constraint("operating_pressure_bar", None, status="refused"),
            ],
            False,
            ReadinessStatus.PRELIMINARY_READY,
            None,
            NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            True,
        ),
        (
            [
                _constraint("pipe_service", "горячая вода"),
                _constraint("diameter_mm", 20, unit="mm"),
            ],
            True,
            ReadinessStatus.PRELIMINARY_READY,
            None,
            NextActionKind.SHOW_PRELIMINARY_OPTIONS,
            True,
        ),
    ],
)
def test_ppr_offline_pipeline_progresses_without_relaxing_hard_facts(
    constraints: list[dict[str, object]],
    continue_with_confirmed_facts: bool,
    expected_status: ReadinessStatus,
    expected_question: str | None,
    expected_action: NextActionKind,
    expects_search_plan: bool,
) -> None:
    catalog = build_catalog_snapshot(
        (
            _feed_pipe(
                "HOT-20",
                "Труба полипропиленовая PN 20, 20 MM",
                "Давление при температуре воды 90 °C — 9 бар.",
            ),
            _feed_pipe(
                "HOT-25",
                "Труба полипропиленовая PN 20, 25 MM",
                "Давление при температуре воды 90 °C — 9 бар.",
            ),
            Product(
                sku="COLD-20",
                name="Труба полипропиленовая PN 20, 20 MM",
                category_path="Трубы",
                description=(
                    "Только для холодного водоснабжения. Давление при "
                    "температуре воды 70 °C — 10 бар."
                ),
            ),
        )
    )
    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_ppr(
            constraints,
            continue_with_confirmed_facts=continue_with_confirmed_facts,
        ),
        TurnMetadata(
            turn_id=(
                f"ppr-offline-{expected_status.value}-"
                f"{continue_with_confirmed_facts}"
            )
        ),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.status == "applied"
    assert outcome.catalog_planning is not None
    assessment = outcome.catalog_planning.readiness_assessments[0]
    assert assessment.status == expected_status
    assert assessment.recommended_question_fact == expected_question
    assert outcome.next_action_plan is not None
    assert outcome.next_action_plan.primary.kind == expected_action
    assert bool(outcome.catalog_planning.search_plans) is expects_search_plan

    if expects_search_plan:
        plan = outcome.catalog_planning.search_plans[0]
        hard_names = {item.name for item in plan.hard_constraints}
        assert {"pipe_service", "diameter_mm"} <= hard_names
        assert not {
            "reinforcement",
            "pressure_class",
            "material",
        } & hard_names
        by_sku = {item.sku: item for item in plan.candidate_assessments}
        assert by_sku["HOT-25"].status == CandidateStatus.REJECTED
        assert "diameter_mm" in by_sku["HOT-25"].mismatched_hard_facts
        assert by_sku["COLD-20"].status == CandidateStatus.REJECTED
        assert "pipe_service" in by_sku["COLD-20"].mismatched_hard_facts
        assert all(not item.relaxations for item in by_sku.values())


def test_pipe_service_is_checked_from_description_with_provenance() -> None:
    catalog = build_catalog_snapshot(
        (
            Product(
                sku="PIPE-BOTH",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-COLD",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Только для холодного водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-UNVERIFIED",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description=None,
                attributes_normalized=_PIPE_RATINGS,
            ),
        )
    )

    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_hot_water_pipe(),
        TurnMetadata(turn_id="pipe-hot-water"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.status == "applied"
    assert outcome.catalog_planning is not None
    assessment = outcome.catalog_planning.readiness_assessments[0]
    canonical_facts = {
        item.name: item.value for item in assessment.confirmed_hard_facts
    }
    assert canonical_facts["pipe_service"] == "hot_water"
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["PIPE-BOTH"].status == CandidateStatus.ELIGIBLE
    assert any(
        item.source == "description"
        for item in candidates["PIPE-BOTH"].provenance
    )
    assert candidates["PIPE-COLD"].status == CandidateStatus.REJECTED
    assert "pipe_service" in candidates["PIPE-COLD"].mismatched_hard_facts
    assert candidates["PIPE-UNVERIFIED"].status == CandidateStatus.UNVERIFIED
    assert "pipe_service" in candidates["PIPE-UNVERIFIED"].missing_hard_facts


def test_pipe_temperature_and_pressure_are_enforced_when_customer_knows_them() -> None:
    catalog = build_catalog_snapshot(
        (
            Product(
                sku="PIPE-RATED",
                name="Труба полипропиленовая PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
                attributes_normalized={
                    "максимальная рабочая температура, °с": "95",
                    "максимальное рабочее давление, бар": "10",
                },
            ),
            Product(
                sku="PIPE-LOW-RATING",
                name="Труба полипропиленовая PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
                attributes_normalized={
                    "максимальная рабочая температура, °с": "60",
                    "максимальное рабочее давление, бар": "4",
                },
            ),
            Product(
                sku="PIPE-RATING-ABSENT",
                name="Труба полипропиленовая PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
            ),
        )
    )
    semantic = _semantic_for_ppr(
        [
            _constraint("pipe_service", "горячая вода"),
            _constraint("diameter_mm", 20, unit="mm"),
            _constraint("operating_temperature_c", 80, unit="C"),
            _constraint("operating_pressure_bar", 6, unit="bar"),
        ]
    )

    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        semantic,
        TurnMetadata(turn_id="pipe-known-ratings"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    assessment = outcome.catalog_planning.readiness_assessments[0]
    assert assessment.status == ReadinessStatus.EXACT_READY
    hard_values = {
        item.name: item.value for item in assessment.confirmed_hard_facts
    }
    assert hard_values["operating_temperature_c"] == 80
    assert hard_values["operating_pressure_bar"] == 6
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["PIPE-RATED"].status == CandidateStatus.ELIGIBLE
    assert candidates["PIPE-LOW-RATING"].status == CandidateStatus.REJECTED
    assert set(candidates["PIPE-LOW-RATING"].mismatched_hard_facts) >= {
        "operating_temperature_c",
        "operating_pressure_bar",
    }
    assert candidates["PIPE-RATING-ABSENT"].status == CandidateStatus.UNVERIFIED
    assert set(candidates["PIPE-RATING-ABSENT"].missing_hard_facts) >= {
        "operating_temperature_c",
        "operating_pressure_bar",
    }


def test_requested_pipe_services_must_all_be_supported_by_candidate() -> None:
    catalog = build_catalog_snapshot(
        (
            Product(
                sku="PIPE-BOTH",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-COLD",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Только для холодного водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-HOT",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Только для горячего водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
        )
    )

    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_hot_water_pipe("холодная и горячая вода"),
        TurnMetadata(turn_id="pipe-both-services"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["PIPE-BOTH"].status == CandidateStatus.ELIGIBLE
    assert candidates["PIPE-COLD"].status == CandidateStatus.REJECTED
    assert candidates["PIPE-HOT"].status == CandidateStatus.REJECTED


def test_single_pipe_service_accepts_candidate_supporting_multiple_services() -> None:
    catalog = build_catalog_snapshot(
        (
            Product(
                sku="PIPE-BOTH",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Для холодного и горячего водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-COLD",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Только для холодного водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
            Product(
                sku="PIPE-HOT",
                name="Труба PN 20, 20 MM",
                category_path="Трубы",
                description="Только для горячего водоснабжения.",
                attributes_normalized=_PIPE_RATINGS,
            ),
        )
    )

    outcome = DialogueControllerV2().run(
        DialogueStateV2(),
        _semantic_for_hot_water_pipe("холодная вода"),
        TurnMetadata(turn_id="pipe-cold-service"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=catalog,
    )

    assert outcome.catalog_planning is not None
    candidates = {
        item.sku: item
        for item in outcome.catalog_planning.search_plans[0].candidate_assessments
    }
    assert candidates["PIPE-BOTH"].status == CandidateStatus.ELIGIBLE
    assert candidates["PIPE-COLD"].status == CandidateStatus.ELIGIBLE
    assert candidates["PIPE-HOT"].status == CandidateStatus.REJECTED


def _semantic_for_sewer_pipe(
    *,
    diameter_mm: object,
    length_mm: object,
    sewer_scope: object,
) -> SemanticInterpretationResult:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
            "operation": "new",
            "acts": ["select"],
            "products": [
                {
                    "text": "канализационная труба",
                    "canonical_type": "sewer_pipe",
                    "category": "sewer",
                    "role": "target",
                    "evidence": "канализационная труба",
                }
            ],
            "constraints": [
                _constraint("diameter_mm", diameter_mm, unit="mm"),
                _constraint("length_mm", length_mm, unit="mm"),
                _constraint("sewer_scope", sewer_scope),
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [],
            "selection_strategy": {"kind": "standard", "evidence": None},
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 1.0,
        }
    )
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def test_every_actual_feed100_pipe_is_reachable_without_cross_kind_substitution() -> None:
    catalog = build_catalog_snapshot(
        FeedLoader().parse_xml(_FEED100.read_bytes())
    )
    actual_pipes = tuple(
        item
        for item in catalog
        if item.product_kind in {ProductKind.PIPE, ProductKind.SEWER_PIPE}
    )
    ppr_pipes = tuple(
        item for item in actual_pipes if item.product_kind == ProductKind.PIPE
    )
    sewer_pipes = tuple(
        item for item in actual_pipes if item.product_kind == ProductKind.SEWER_PIPE
    )

    assert len(ppr_pipes) == 14
    assert len(sewer_pipes) == 8
    assert all(item.role.value == "base_product" for item in actual_pipes)

    for product in ppr_pipes:
        facts = {item.name: item.value for item in product.facts}
        constraints = [
            _constraint("pipe_service", "hot_water"),
            _constraint("diameter_mm", facts["diameter_mm"], unit="mm"),
            _constraint("pressure_class", facts["pressure_class"]),
        ]
        if reinforcement := facts.get("reinforcement"):
            constraints.append(_constraint("reinforcement", reinforcement))

        ratings_known = all(
            name in facts
            for name in ("operating_temperature_c", "operating_pressure_bar")
        )
        if ratings_known:
            constraints.extend(
                (
                    _constraint(
                        "operating_temperature_c",
                        facts["operating_temperature_c"],
                        unit="C",
                    ),
                    _constraint(
                        "operating_pressure_bar",
                        facts["operating_pressure_bar"],
                        unit="bar",
                    ),
                )
            )
        else:
            constraints.extend(
                (
                    _constraint(
                        "operating_temperature_c",
                        None,
                        status="unknown",
                    ),
                    _constraint(
                        "operating_pressure_bar",
                        None,
                        status="unknown",
                    ),
                )
            )

        outcome = DialogueControllerV2().run(
            DialogueStateV2(),
            _semantic_for_ppr(constraints),
            TurnMetadata(turn_id=f"feed100-ppr-{product.sku}"),
            product_contracts_enabled=True,
            catalog_planner_enabled=True,
            catalog_snapshot=catalog,
        )
        assert outcome.catalog_planning is not None
        assert len(outcome.catalog_planning.search_plans) == 1
        candidates = {
            item.sku: item
            for item in outcome.catalog_planning.search_plans[0].candidate_assessments
        }
        assert product.sku in candidates
        expected_status = (
            CandidateStatus.ELIGIBLE
            if ratings_known
            else CandidateStatus.UNVERIFIED
        )
        assert candidates[product.sku].status == expected_status
        assert all(
            item.product_kind == ProductKind.PIPE
            for item in candidates.values()
        )

    for product in sewer_pipes:
        facts = {item.name: item.value for item in product.facts}
        outcome = DialogueControllerV2().run(
            DialogueStateV2(),
            _semantic_for_sewer_pipe(
                diameter_mm=facts["diameter_mm"],
                length_mm=facts["length_mm"],
                sewer_scope=facts["sewer_scope"],
            ),
            TurnMetadata(turn_id=f"feed100-sewer-{product.sku}"),
            product_contracts_enabled=True,
            catalog_planner_enabled=True,
            catalog_snapshot=catalog,
        )
        assert outcome.catalog_planning is not None
        assert len(outcome.catalog_planning.search_plans) == 1
        candidates = {
            item.sku: item
            for item in outcome.catalog_planning.search_plans[0].candidate_assessments
        }
        assert candidates[product.sku].status == CandidateStatus.ELIGIBLE
        assert all(
            item.product_kind == ProductKind.SEWER_PIPE
            for item in candidates.values()
        )
