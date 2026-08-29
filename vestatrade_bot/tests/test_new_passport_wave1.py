"""Exact product scope for the first approved passport onboarding wave."""

from __future__ import annotations

from app.config import PROJECT_ROOT
from app.docs_loader import load_docs_for_products
from app.models import Product
from app.passport_retrieval import read_chunks


WAVE1 = {
    "VT.5000.0.0": "0962d51dab5c3219f584820a92d556aa.pdf",
    "8216262000": "63109b6ad4cd19.27758769.pdf",
    "RBM-0210-050006": "Rommer_pasport алюминиевые.pdf",
    "RAL-1210-050006": "Rommer_pasport алюминиевые.pdf",
    "2202210": "Руководство_электрические_котлы_ARDERIA_2023.pdf",
}


def _product(sku: str, name: str, brand: str = "") -> Product:
    return Product(sku=sku, name=name, brand=brand, category_path="test")


def test_wave1_passports_attach_only_to_approved_exact_skus() -> None:
    approved = [
        _product("VT.5000.0.0", "Термоголовка VALTEC VT.5000", "VALTEC"),
        _product("8216262000", "Котел E.C.A. Arceus ST 6", "E.C.A"),
        _product("RBM-0210-050006", "ROMMER Optima BM 500 6 секций", "ROMMER"),
        _product("RAL-1210-050006", "ROMMER Profi 500 6 секций", "ROMMER"),
        _product("2202210", "Котел Arderia E9", "Arderia"),
    ]
    excluded = [
        _product("800019", "Насосная станция THERMEX Mark", "Thermex"),
        _product("3301679", "Ariston CLAS XC SYSTEM 24 FF", "ARISTON"),
        _product("RRS-2020-115140", "ROMMER Ventil 11/500/1400", "ROMMER"),
        _product("RRS-2020-223100", "ROMMER Ventil 22/300/1000", "ROMMER"),
        # A longer value proves that exact mapping is not prefix matching.
        _product("22022101", "Unrelated future SKU", "Arderia"),
    ]

    load_docs_for_products(approved + excluded, PROJECT_ROOT / "data")

    for product in approved:
        assert {document.filename for document in product.documents} == {
            WAVE1[product.sku]
        }
        assert product.docs_text
    for product in excluded:
        assert not ({document.filename for document in product.documents} & set(WAVE1.values()))


def test_wave1_documents_keep_expected_model_evidence() -> None:
    products = [
        _product("VT.5000.0.0", "Термоголовка VALTEC VT.5000", "VALTEC"),
        _product("8216262000", "Котел E.C.A. Arceus ST 6", "E.C.A"),
        _product("RBM-0210-050006", "ROMMER Optima BM 500 6 секций", "ROMMER"),
        _product("RAL-1210-050006", "ROMMER Profi 500 6 секций", "ROMMER"),
        _product("2202210", "Котел Arderia E9", "Arderia"),
    ]

    load_docs_for_products(products, PROJECT_ROOT / "data")
    by_sku = {product.sku: product for product in products}

    assert "VT.5000" in (by_sku["VT.5000.0.0"].docs_text or "")
    assert "ARCEUS" in (by_sku["8216262000"].docs_text or "").upper()
    assert "Алюминиевые и биметаллические" in (
        by_sku["RBM-0210-050006"].docs_text or ""
    )
    assert "Алюминиевые и биметаллические" in (
        by_sku["RAL-1210-050006"].docs_text or ""
    )
    assert "E9" in (by_sku["2202210"].docs_text or "")


def test_rommer_model_table_is_present_in_retrieval_chunks() -> None:
    chunks = read_chunks([PROJECT_ROOT / "data"])
    documents = {chunk.document for chunk in chunks}
    text = " ".join(
        chunk.text
        for chunk in chunks
        if chunk.document == "Rommer_pasport алюминиевые.pdf"
    )

    assert "Optima Bm 500" in text
    assert "0,129" in text
    assert "Proﬁ 500" in text
    assert "0,157" in text
    assert not (
        documents
        & {
            "132779.pdf",
            "Instrukciya.pdf",
            "a93621c2b5b44dcdd178ce52c8155937.pdf",
            "rommer stalnyie panelnyie_2.pdf",
            "user-manual-pumps-grundfos-ups-25-40.pdf",
        }
    )
