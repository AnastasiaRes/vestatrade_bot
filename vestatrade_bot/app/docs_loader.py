"""Привязка документации товаров (паспорта, характеристики) к карточкам фида.

Файлы кладутся в каталог product_docs (по умолчанию app/data/product_docs)
с именем, равным артикулу товара: `VT.1500.0.0.pdf`, `ARD-E9.txt` и т.п.
Слэши в артикуле заменяются дефисами: товар `68/2/8` -> файл `68-2-8.pdf`.

Поддерживаются .pdf, .txt и .md. Извлечённый текст попадает в Product.docs_text
и используется ботом для подтверждения комплектации и ответов на вопросы
о конкретном товаре. В поиск по категориям этот текст намеренно не подмешивается,
чтобы упоминание насоса в паспорте котла не превращало котёл в насос.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agents.utils import normalize_sku
from app.models import Product


logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 8000
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


def _doc_key(value: str) -> str:
    return normalize_sku(value).replace("/", "-")


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf не установлен — пропускаю %s", path.name)
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.warning("Не удалось прочитать PDF %s: %s", path.name, exc)
        return ""


def load_docs_for_products(products: list[Product], docs_dir: Path) -> int:
    """Attach document text to matching products; returns the number attached."""
    if not docs_dir.exists():
        return 0
    by_key = {_doc_key(product.sku): product for product in products}
    attached = 0
    for path in sorted(docs_dir.iterdir()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        product = by_key.get(_doc_key(path.stem))
        if not product:
            logger.warning("Документ %s не совпал ни с одним артикулом фида", path.name)
            continue
        if path.suffix.lower() == ".pdf":
            text = _extract_pdf_text(path)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        text = " ".join(text.split())
        if not text:
            continue
        product.docs_text = text[:MAX_DOC_CHARS]
        attached += 1
    return attached
