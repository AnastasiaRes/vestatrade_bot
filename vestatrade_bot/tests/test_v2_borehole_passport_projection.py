"""Passport-backed, fail-closed Selection facts for the ECO VINT borehole pump."""

from __future__ import annotations

from pathlib import Path

from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.normalization import build_catalog_snapshot
from app.config import PROJECT_ROOT, get_settings
from app.docs_loader import load_docs_for_products
from app.models import Product, ProductDocumentFact, SessionState
from app.product_fact_evidence import ProductFactEvidenceService, ProductFactStatus, render_product_fact_evidence


class _NoNetworkClient:
    def embed(self, _texts):
        raise AssertionError("a deterministic passport-table fact must not invoke retrieval")


def _eco_vint_product(*, max_flow_l_h: str | None = None) -> Product:
    attributes = {
        "Тип товара": "Скважинный насос",
        "Максимальный напор, м": "90",
    }
    if max_flow_l_h is not None:
        attributes["Макс. производительность, л/ч"] = max_flow_l_h
    return Product(
        sku="11677",
        name='Винтовой скважинный насос Unipump 3" ECO VINT 2 (550 Вт, кабель-20м)',
        brand="UNIPUMP",
        category_path="Насосное оборудование",
        price=9_528,
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
        attributes_normalized=attributes,
    )


def test_exact_ecovint_table_row_becomes_passport_provenance_in_v2_snapshot() -> None:
    """The model name, not the broad brand mapping, owns the projected value."""

    product = _eco_vint_product()
    attached = load_docs_for_products([product], Path(PROJECT_ROOT) / "data")

    assert attached >= 1
    assert len(product.document_facts) == 1
    document_fact = product.document_facts[0]
    assert document_fact.name == "max_flow_l_h"
    assert document_fact.value == 1500
    assert document_fact.unit == "l/h"
    assert document_fact.document == "pasport-nasosy-skvazhinnye-unipump-ecovint.pdf"
    assert document_fact.section.endswith("ECO VINT 2")
    assert [
        (point.flow_l_h, point.head_m)
        for point in product.document_flow_head_points
    ] == [
        (0, 102),
        (300, 91),
        (600, 76),
        (900, 61),
        (1200, 44),
        (1500, 26),
    ]

    snapshot = build_catalog_snapshot([product])
    fact = next(item for item in snapshot[0].facts if item.name == "max_flow_l_h")
    assert fact.value == 1500
    assert fact.unit == "l/h"
    assert fact.provenance.source == "passport"
    assert fact.provenance.source_document == document_fact.document
    assert fact.provenance.source_section == document_fact.section

    source = build_answer_source_snapshot([product], snapshot)
    source_fact = next(
        item for item in source.product("11677").facts if item.name == "max_flow_l_h"
    )
    assert source_fact.provenance.source == "passport"
    source_point = next(
        point
        for point in source.product("11677").flow_head_points
        if point.flow_l_h == 1200
    )
    assert source_point.head_m == 44
    assert source_point.provenance.source == "passport"
    assert source_point.provenance.source_document == document_fact.document
    assert source_point.provenance.source_section.endswith("ECO VINT 2")


def test_passport_projection_never_overwrites_a_conflicting_feed_rating() -> None:
    product = _eco_vint_product(max_flow_l_h="2000")
    product.document_facts.append(
        ProductDocumentFact(
            name="max_flow_l_h",
            value=1500,
            unit="l/h",
            document="pasport-nasosy-skvazhinnye-unipump-ecovint.pdf",
            section="3.2 Технические характеристики, модель ECO VINT 2",
            evidence="Макс. производительность, л/мин (м³/ч): 25 (1,5); ECO VINT 2",
            parser="unipump_eco_vint_shared_flow_table_v1",
        )
    )

    snapshot = build_catalog_snapshot([product])[0]

    assert any(item.name == "max_flow_l_h" for item in snapshot.fact_issues)
    # The existing feed value remains present for audit, but any V2 consumer
    # must reject it because ``fact_issues`` takes precedence.
    assert any(
        item.name == "max_flow_l_h" and item.value == 2000
        for item in snapshot.facts
    )


def test_source_revision_tracks_the_verified_passport_value() -> None:
    product = _eco_vint_product()
    product.document_facts.append(
        ProductDocumentFact(
            name="max_flow_l_h",
            value=1500,
            unit="l/h",
            document="pasport-nasosy-skvazhinnye-unipump-ecovint.pdf",
            section="3.2 Технические характеристики, модель ECO VINT 2",
            evidence="Макс. производительность, л/мин (м³/ч): 25 (1,5); ECO VINT 2",
            parser="unipump_eco_vint_shared_flow_table_v1",
        )
    )
    first = build_answer_source_snapshot([product], build_catalog_snapshot([product]))

    product.document_facts[0] = product.document_facts[0].model_copy(
        update={"value": 1400, "evidence": "изменённая проверенная строка"}
    )
    second = build_answer_source_snapshot([product], build_catalog_snapshot([product]))

    assert first.source_revision != second.source_revision


def test_direct_fact_renders_the_passport_source_not_a_mislabelled_card() -> None:
    product = _eco_vint_product()
    load_docs_for_products([product], Path(PROJECT_ROOT) / "data")
    snapshot = build_catalog_snapshot([product])
    settings = get_settings().model_copy(update={"embeddings_enabled": False})
    service = ProductFactEvidenceService(
        settings,
        _NoNetworkClient(),
        [product],
        catalog_snapshot=snapshot,
    )

    evidence = service.evaluate(
        "Какая максимальная производительность у товара 11677?",
        SessionState(session_id="eco-vint-product-fact"),
        semantic_fact_name="max_flow_l_h",
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.value == 1500
    assert evidence.unit == "l/h"
    assert evidence.source_kind == "passport_document_exact"
    assert evidence.document == "pasport-nasosy-skvazhinnye-unipump-ecovint.pdf"
    assert evidence.verifier_status == "document_table_exact"
    rendered = render_product_fact_evidence(evidence)
    assert "1500 л/ч" in rendered
    assert "В привязанной документации" in rendered
