"""Cold-start ordering between passport onboarding and V2 source snapshots."""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.normalization import build_catalog_snapshot
from app.config import get_settings
from app.models import Product


def test_v2_snapshot_includes_passport_enriched_facts_on_cold_start(
    monkeypatch,
) -> None:
    """Selection must see the same proven ratings as ProductFact after startup."""

    product = Product(
        sku="VTp.700.FB20.25",
        name="Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        category_path="Трубы полипропиленовые",
        url="https://example.test/ppr-25",
        price=168,
        stock_status="в наличии",
    )

    def _attach_passport_facts(products, _directories) -> int:
        assert products == [product]
        products[0].attributes_normalized[
            "максимальная рабочая температура, °с"
        ] = "90"
        products[0].attributes_normalized[
            "рабочее давление, радиаторное отопление, бар"
        ] = "6"
        return 1

    monkeypatch.setattr(
        "app.agents.orchestrator.load_docs_for_products",
        _attach_passport_facts,
    )
    settings = get_settings().model_copy(
        update={
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "test-only-token",
        }
    )

    bot = ChatOrchestrator(settings=settings, products=[product])

    snapshot = next(item for item in bot.catalog_snapshot_v2 if item.sku == product.sku)
    facts = {item.name: item for item in snapshot.facts}
    assert facts["operating_temperature_c"].value == 90
    assert facts["operating_pressure_bar"].value == 6
    assert facts["operating_pressure_bar"].provenance.source == "attribute"
    assert facts["operating_pressure_bar"].provenance.source_field == (
        "рабочее давление, радиаторное отопление, бар"
    )

    answer_product = bot.answer_source_snapshot_v2.product(product.sku)
    assert answer_product is not None
    assert {item.name for item in answer_product.facts} >= {
        "operating_temperature_c",
        "operating_pressure_bar",
    }


def test_source_revision_changes_when_a_passport_fact_changes() -> None:
    product = Product(
        sku="VTp.700.FB20.25",
        name="Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        category_path="Трубы полипропиленовые",
        url="https://example.test/ppr-25",
        price=168,
        stock_status="в наличии",
        attributes_normalized={
            "рабочее давление, радиаторное отопление, бар": "6",
        },
    )
    first = build_answer_source_snapshot(
        [product],
        build_catalog_snapshot([product]),
    )

    product.attributes_normalized[
        "рабочее давление, радиаторное отопление, бар"
    ] = "8"
    second = build_answer_source_snapshot(
        [product],
        build_catalog_snapshot([product]),
    )

    assert first.source_revision != second.source_revision
