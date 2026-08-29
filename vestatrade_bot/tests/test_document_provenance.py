"""Focused regressions for source-preserving local product documentation."""

from __future__ import annotations

import json

from app import docs_loader
from app.docs_loader import load_docs_for_products
from app.models import Product


def _product() -> Product:
    return Product(
        sku="DOC-1",
        name="Тестовый циркуляционный насос",
        category_path="Насосное оборудование",
    )


def test_old_cached_product_without_documents_remains_valid() -> None:
    old_cache_payload = {
        "sku": "OLD-1",
        "name": "Товар из старого кэша",
        "docs_text": "Старый объединённый текст паспорта",
    }

    product = Product.model_validate(old_cache_payload)

    assert product.docs_text == "Старый объединённый текст паспорта"
    assert product.documents == []
    # The new default survives the same JSON round-trip used by feed caches.
    restored = Product.model_validate(json.loads(product.model_dump_json()))
    assert restored.documents == []
    assert restored.docs_text == product.docs_text


def test_multiple_documents_keep_separate_sources_and_legacy_text(tmp_path) -> None:
    product = _product()
    (tmp_path / "passport.txt").write_text(
        "Технический паспорт. Комплект поставки: насос и паспорт.",
        encoding="utf-8",
    )
    (tmp_path / "installation.md").write_text(
        "Инструкция по монтажу и подключению насоса.",
        encoding="utf-8",
    )
    (tmp_path / "product_docs_map.json").write_text(
        json.dumps(
            {
                "passport.txt": {"sku_prefixes": ["DOC-1"]},
                "installation.md": {"sku_prefixes": ["DOC-1"]},
            }
        ),
        encoding="utf-8",
    )

    attached = load_docs_for_products([product], tmp_path)

    assert attached == 2
    assert [document.filename for document in product.documents] == [
        "installation.md",
        "passport.txt",
    ]
    assert [document.document_kind for document in product.documents] == [
        "instruction",
        "passport",
    ]
    assert all(document.page_count is None for document in product.documents)
    assert all(document.section_pages == {} for document in product.documents)
    assert "Инструкция по монтажу" in (product.docs_text or "")
    assert "Технический паспорт" in (product.docs_text or "")
    assert all("/" not in document.filename for document in product.documents)


def test_exact_sku_map_does_not_match_a_longer_sku(tmp_path) -> None:
    exact = Product(sku="MODEL-1", name="Exact model")
    sibling = Product(sku="MODEL-10", name="Different model")
    (tmp_path / "manual.txt").write_text("Паспорт точной модели", encoding="utf-8")
    (tmp_path / "product_docs_map.json").write_text(
        json.dumps({"manual.txt": {"skus": ["MODEL-1"]}}),
        encoding="utf-8",
    )

    attached = load_docs_for_products([exact, sibling], tmp_path)

    assert attached == 1
    assert [document.filename for document in exact.documents] == ["manual.txt"]
    assert sibling.documents == []


def test_pdf_evidence_records_page_count_and_best_section_pages(
    tmp_path,
    monkeypatch,
) -> None:
    product = _product()
    pdf_path = tmp_path / "DOC-1.pdf"
    pdf_path.write_bytes(b"fake local pdf; parser is isolated in this test")
    pages = [
        "ПАСПОРТ. Оглавление: 5. Комплект поставки. 7. Монтаж.",
        (
            "5. Комплект поставки. В комплект поставки входят: "
            "1. Насос, 1 шт. 2. Руководство, 1 шт."
        ),
        "7. Монтаж и подключение. Схема установки и присоединения насоса.",
    ]
    monkeypatch.setattr(docs_loader, "_read_pdf_pages", lambda _path: pages)

    load_docs_for_products([product], tmp_path)

    assert len(product.documents) == 1
    evidence = product.documents[0]
    assert evidence.filename == "DOC-1.pdf"
    assert evidence.document_kind == "passport"
    assert evidence.page_count == 3
    assert evidence.section_pages["комплект поставки"] == 2
    assert evidence.section_pages["монтаж и подключение"] == 3
    restored = Product.model_validate_json(product.model_dump_json())
    assert restored.documents[0] == evidence


def test_reloading_same_document_is_idempotent(tmp_path) -> None:
    product = _product()
    (tmp_path / "DOC-1.txt").write_text(
        "Паспорт изделия. Технические характеристики.",
        encoding="utf-8",
    )

    load_docs_for_products([product], tmp_path)
    first_legacy_text = product.docs_text
    load_docs_for_products([product], tmp_path)

    assert len(product.documents) == 1
    assert product.docs_text == first_legacy_text
