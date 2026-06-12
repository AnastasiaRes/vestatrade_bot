"""Привязка документации товаров (паспорта, инструкции) к карточкам фида.

Поддерживаются два каталога (по умолчанию app/data/product_docs и data/)
и три способа привязки документа к товарам:

1. Карта product_docs_map.json в каталоге с документами — для серийных
   паспортов, покрывающих несколько артикулов:

   {
     "VT.033-034-0425.pdf": {"sku_prefixes": ["VT.033", "VT.034"]},
     "газовые котлы ARDERIA.pdf": {"brand": "Arderia", "name_contains_any": ["газовый"]}
   }

2. Имя файла, равное артикулу: `VT.1500.0.0.pdf` (слэши -> дефисы: 68/2/8 -> 68-2-8.pdf).
3. Имя файла вида `VT.226-227-228-1248в.pdf` — серии раскрываются по общему префиксу.

Поддерживаются .pdf, .txt и .md. Извлечённый текст попадает в Product.docs_text
и используется ботом для подтверждения комплектации и ответов на вопросы
о конкретном товаре. В поиск по категориям этот текст намеренно не подмешивается,
чтобы упоминание насоса в паспорте котла не превращало котёл в насос.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.agents.utils import normalize_sku, normalize_text
from app.models import Product


logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 8000
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}
MAP_FILENAME = "product_docs_map.json"

# Кэш извлечённого текста, чтобы не перечитывать PDF при каждом создании оркестратора.
_TEXT_CACHE: dict[tuple[str, float], str] = {}

SERIES_FILENAME_RE = re.compile(r"^([A-Za-z]+\.)(\d+(?:[-–]\w+)+)")


def _doc_key(value: str) -> str:
    return normalize_sku(value).replace("/", "-")


def _extract_text(path: Path) -> str:
    cache_key = (str(path), path.stat().st_mtime)
    if cache_key in _TEXT_CACHE:
        return _TEXT_CACHE[cache_key]
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except ImportError:
            logger.warning("pypdf не установлен — пропускаю %s", path.name)
            text = ""
        except Exception as exc:
            logger.warning("Не удалось прочитать PDF %s: %s", path.name, exc)
            text = ""
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    text = " ".join(text.split())[:MAX_DOC_CHARS]
    _TEXT_CACHE[cache_key] = text
    return text


def _load_map(docs_dir: Path) -> dict[str, dict[str, Any]]:
    map_path = docs_dir / MAP_FILENAME
    if not map_path.exists():
        return {}
    try:
        data = json.loads(map_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Не удалось прочитать %s: %s", map_path, exc)
        return {}


def _match_by_rule(products: list[Product], rule: dict[str, Any]) -> list[Product]:
    prefixes = [_doc_key(prefix) for prefix in rule.get("sku_prefixes", [])]
    brand = normalize_text(str(rule["brand"])) if rule.get("brand") else None
    name_needles = [normalize_text(str(n)) for n in rule.get("name_contains_any", [])]
    matched: list[Product] = []
    for product in products:
        if prefixes:
            sku_key = _doc_key(product.sku)
            if any(sku_key.startswith(prefix) for prefix in prefixes):
                matched.append(product)
            continue
        if brand and normalize_text(product.brand) != brand:
            continue
        if name_needles:
            name_norm = normalize_text(product.name)
            if not any(needle in name_norm for needle in name_needles):
                continue
        if brand or name_needles:
            matched.append(product)
    return matched


def _match_by_filename(products: list[Product], stem: str) -> list[Product]:
    file_key = _doc_key(stem)
    exact = [product for product in products if _doc_key(product.sku) == file_key]
    if exact:
        return exact
    series_match = SERIES_FILENAME_RE.match(stem)
    if not series_match:
        return []
    base, tail = series_match.groups()
    prefixes = [_doc_key(f"{base}{part}") for part in re.split(r"[-–]", tail)]
    return [
        product
        for product in products
        if any(_doc_key(product.sku).startswith(prefix + ".") for prefix in prefixes)
    ]


def load_docs_for_products(
    products: list[Product],
    docs_dirs: Path | list[Path],
) -> int:
    """Attach document text to matching products; returns the number of attached docs."""
    dirs = [docs_dirs] if isinstance(docs_dirs, Path) else list(docs_dirs)
    attached_docs = 0
    for docs_dir in dirs:
        if not docs_dir.exists():
            continue
        mapping = _load_map(docs_dir)
        for path in sorted(docs_dir.iterdir()):
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            rule = mapping.get(path.name)
            if rule:
                targets = _match_by_rule(products, rule)
            else:
                targets = _match_by_filename(products, path.stem)
            if not targets:
                logger.warning("Документ %s не совпал ни с одним товаром фида", path.name)
                continue
            text = _extract_text(path)
            if not text:
                logger.warning("Документ %s без текстового слоя — пропускаю", path.name)
                continue
            for product in targets:
                if product.docs_text:
                    product.docs_text = (product.docs_text + " " + text)[:MAX_DOC_CHARS]
                else:
                    product.docs_text = text
            attached_docs += 1
    return attached_docs
