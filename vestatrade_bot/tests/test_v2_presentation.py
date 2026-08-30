from __future__ import annotations

from app.compatibility_v2.contracts import (
    CompatibilityProductReference,
    CompatibilityReferenceKind,
    CompatibilityRelationKind,
    CompatibilityResult,
    CompatibilityResultStatus,
    InterfaceFact,
    InterfaceSourceKind,
)
from app.compatibility_v2.renderer import render_compatibility_result
from app.catalog_v2.selection import _presentation_value
from app.comparison_v2.contracts import (
    ComparisonDimension,
    ComparisonResult,
    ComparisonResultStatus,
    ComparisonSourceKind,
    ComparisonSourceReference,
    ComparisonValue,
)
from app.comparison_v2.renderer import render_comparison_result
from app.product_fact_evidence import (
    ProductFactEvidence,
    ProductFactRequest,
    ProductFactStatus,
    ProductReference,
    ProductReferenceKind,
    render_product_fact_evidence,
)
from app.v2_presentation import (
    clarification_presentation,
    format_public_fact_value,
    public_fact_label,
)


def test_shared_v2_formatter_localizes_canonical_values_units_and_labels() -> None:
    assert public_fact_label("reinforcement") == "тип армирования"
    assert public_fact_label("thread_standard") == "стандарт резьбы"
    assert public_fact_label("unregistered_internal_key") == "характеристика товара"
    assert (
        format_public_fact_value("glass_fiber", predicate="reinforcement")
        == "стекловолокно"
    )
    assert format_public_fact_value(25, predicate="diameter_mm", unit="mm") == "25 мм"
    assert (
        format_public_fact_value(1500, predicate="duty_point_flow_l_h", unit="l/h")
        == "1,5 м³/ч"
    )
    assert _presentation_value(25, "mm") == "25 мм"


def test_clarification_copy_is_human_and_does_not_repeat_service_words() -> None:
    circuits = clarification_presentation("circuits")

    assert circuits.question == (
        "Котёл будет только отапливать дом или ещё готовить горячую воду? "
        "Если горячую воду обеспечивает отдельный водонагреватель, тоже напишите."
    )
    assert circuits.include_learn_instruction is False
    assert "уточните" not in circuits.question.casefold()


def test_comparison_never_shows_canonical_predicates_values_or_units() -> None:
    sources = (
        ComparisonSourceReference(
            source_ref_id="s1",
            sku="PPR-1",
            predicate="reinforcement",
            source_kind=ComparisonSourceKind.CATALOG_ATTRIBUTE,
            source_revision="feed-r1",
        ),
        ComparisonSourceReference(
            source_ref_id="s2",
            sku="PPR-2",
            predicate="reinforcement",
            source_kind=ComparisonSourceKind.CATALOG_ATTRIBUTE,
            source_revision="feed-r1",
        ),
    )
    result = ComparisonResult(
        status=ComparisonResultStatus.COMPARED,
        compared_skus=("PPR-1", "PPR-2"),
        dimensions=(
            ComparisonDimension(
                predicate="reinforcement",
                label="тип армирования",
                values=(
                    ComparisonValue(
                        sku="PPR-1",
                        predicate="reinforcement",
                        value="glass_fiber",
                        source_ref_ids=("s1",),
                    ),
                    ComparisonValue(
                        sku="PPR-2",
                        predicate="reinforcement",
                        value="aluminium",
                        source_ref_ids=("s2",),
                    ),
                ),
            ),
        ),
        sources=sources,
        source_revision="feed-r1",
        outcome_gate_passed=True,
    )

    text = render_comparison_result(
        result,
        names={"PPR-1": "Труба 1", "PPR-2": "Труба 2"},
    )

    assert "Тип армирования" in text
    assert "стекловолокно" in text
    assert "алюминий" in text
    assert "glass_fiber" not in text
    assert "Reinforcement" not in text


def test_comparison_localizes_missing_predicates_and_keeps_card_brand_spelling() -> None:
    sources = (
        ComparisonSourceReference(
            source_ref_id="brand-1",
            sku="PUMP-1",
            predicate="brand",
            source_kind=ComparisonSourceKind.CATALOG_IDENTITY,
            source_revision="feed-r1",
            raw_value="Wilo",
        ),
        ComparisonSourceReference(
            source_ref_id="brand-2",
            sku="PUMP-2",
            predicate="brand",
            source_kind=ComparisonSourceKind.CATALOG_IDENTITY,
            source_revision="feed-r1",
            raw_value="VALTEC",
        ),
    )
    result = ComparisonResult(
        status=ComparisonResultStatus.COMPARED,
        compared_skus=("PUMP-1", "PUMP-2"),
        dimensions=(
            ComparisonDimension(
                predicate="brand",
                label="бренд",
                values=(
                    ComparisonValue(
                        sku="PUMP-1",
                        predicate="brand",
                        value="wilo",
                        source_ref_ids=("brand-1",),
                    ),
                    ComparisonValue(
                        sku="PUMP-2",
                        predicate="brand",
                        value="valtec",
                        source_ref_ids=("brand-2",),
                    ),
                ),
            ),
        ),
        sources=sources,
        missing_data=("installation_length_mm",),
        source_revision="feed-r1",
        outcome_gate_passed=True,
    )

    text = render_comparison_result(
        result,
        names={"PUMP-1": "Насос 1", "PUMP-2": "Насос 2"},
    )

    assert "1 — Wilo; 2 — VALTEC" in text
    assert "Не хватает подтверждённых данных о характеристиках: монтажная длина." in text
    assert "installation_length_mm" not in text


def test_compatibility_localizes_units_and_missing_interface_label() -> None:
    left = CompatibilityProductReference(
        kind=CompatibilityReferenceKind.EXACT_SKU,
        raw="A",
        canonical_sku="A",
        reason_code="exact_sku",
    )
    right = CompatibilityProductReference(
        kind=CompatibilityReferenceKind.EXACT_SKU,
        raw="B",
        canonical_sku="B",
        reason_code="exact_sku",
    )
    facts = tuple(
        InterfaceFact(
            sku=sku,
            predicate="diameter_mm",
            value=50,
            unit="mm",
            source_kind=InterfaceSourceKind.CATALOG_ATTRIBUTE,
            source_revision="feed-r1",
            document="карточка",
            excerpt="50 mm",
            verifier_status="accepted",
        )
        for sku in ("A", "B")
    )
    compatible = CompatibilityResult(
        status=CompatibilityResultStatus.COMPATIBLE,
        relation=CompatibilityRelationKind.SEWER_CONNECTION,
        left=left,
        right=right,
        interface_predicates=("diameter_mm",),
        facts=facts,
        outcome_gate_passed=True,
    )
    insufficient = CompatibilityResult(
        status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
        relation=CompatibilityRelationKind.THREADED_CONNECTION,
        left=left,
        right=right,
        missing_predicates=("A:connection_pattern",),
        reason_codes=("compatibility_interface_facts_missing",),
    )

    compatible_text = render_compatibility_result(compatible)
    insufficient_text = render_compatibility_result(insufficient)

    assert "50 мм" in compatible_text
    assert "50 mm" not in compatible_text
    assert "тип резьбового соединения" in insufficient_text
    assert "connection pattern" not in insufficient_text


def test_generic_product_fact_uses_shared_canonical_value_presentation() -> None:
    reference = ProductReference(
        kind=ProductReferenceKind.EXACT_SKU,
        raw="PPR-1",
        canonical_sku="PPR-1",
        reason_code="exact_sku",
    )
    evidence = ProductFactEvidence(
        status=ProductFactStatus.ANSWERED,
        request=ProductFactRequest(
            question="Какое армирование?",
            predicate="reinforcement",
            product_ref=reference,
        ),
        product_name="Труба",
        value="glass_fiber",
        source_kind="catalog_card",
        verifier_status="accepted",
        reason_code="ok",
    )

    text = render_product_fact_evidence(evidence)

    assert "Армирование — стекловолокно" in text
    assert "glass_fiber" not in text
