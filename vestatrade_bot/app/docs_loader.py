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

_IMPORTANT_SECTION_MARKERS: tuple[tuple[str, ...], ...] = (
    ("комплект поставки", "комплектность", "комплектация"),
    ("технические характеристики", "основные характеристики"),
    ("меры безопасности", "требования безопасности"),
    ("монтаж", "подключение"),
)

# Кэш извлечённого текста, чтобы не перечитывать PDF при каждом создании оркестратора.
_TEXT_CACHE: dict[tuple[str, float], str] = {}
_BOILER_POWER_RANGE_CACHE: dict[
    tuple[str, float], dict[str, tuple[float, float, int]]
] = {}

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
    text = _compact_document_text(text)
    _TEXT_CACHE[cache_key] = text
    return text


def _extract_boiler_power_ranges(path: Path) -> dict[str, tuple[float, float, int]]:
    """Read min/max heating output from a model table in a PDF passport.

    Series passports often use merged cells, so normal text extraction loses the
    relationship between a model and a shared minimum value. Layout extraction
    preserves horizontal positions; the value nearest to each model column is
    therefore the correct table value, including merged cells.
    """
    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    if cache_key in _BOILER_POWER_RANGE_CACHE:
        return _BOILER_POWER_RANGE_CACHE[cache_key]
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        ranges: dict[str, tuple[float, float, int]] = {}
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                layout = page.extract_text(extraction_mode="layout") or ""
            except TypeError:  # pragma: no cover - compatibility with older pypdf
                layout = page.extract_text() or ""
            ranges.update(_parse_boiler_power_table(layout, page_number))
    except Exception as exc:
        logger.warning("Не удалось извлечь диапазоны мощности из %s: %s", path.name, exc)
        ranges = {}
    _BOILER_POWER_RANGE_CACHE[cache_key] = ranges
    return ranges


def _parse_boiler_power_table(
    layout_text: str,
    page_number: int,
) -> dict[str, tuple[float, float, int]]:
    lines = layout_text.splitlines()
    output_row_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "теплопроизво" in normalize_text(line) and "макс" in normalize_text(line)
        ),
        None,
    )
    if output_row_index is None:
        return {}

    model_positions: dict[str, float] = {}
    for line in lines[max(0, output_row_index - 24) : output_row_index]:
        for match in re.finditer(r"\b(?:SB|D|B)\s?\d{2}\b", line, re.IGNORECASE):
            model = re.sub(r"\s+", "", match.group(0)).upper()
            model_positions[model] = (match.start() + match.end()) / 2
    if not model_positions:
        return {}

    max_values = _positioned_decimal_values(lines[output_row_index])
    min_values: list[tuple[float, float]] = []
    for line in lines[output_row_index + 1 : output_row_index + 4]:
        if "мин" in normalize_text(line):
            min_values = _positioned_decimal_values(line)
            break
    if not max_values or not min_values:
        return {}

    ranges: dict[str, tuple[float, float, int]] = {}
    for model, position in model_positions.items():
        maximum = min(max_values, key=lambda item: abs(item[0] - position))[1]
        minimum = min(min_values, key=lambda item: abs(item[0] - position))[1]
        if 0 < minimum <= maximum <= 200:
            ranges[model] = (minimum, maximum, page_number)
    return ranges


def _positioned_decimal_values(line: str) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    for match in re.finditer(r"\d+[.,]\d+", line):
        values.append(
            (
                (match.start() + match.end()) / 2,
                float(match.group(0).replace(",", ".")),
            )
        )
    return values


def _attach_passport_power_range(
    product: Product,
    path: Path,
    ranges: dict[str, tuple[float, float, int]],
) -> None:
    name = normalize_text(product.name).upper()
    model_match = re.search(r"\b(?:SB|D|B)\s?\d{2}\b", name)
    if not model_match:
        return
    model = re.sub(r"\s+", "", model_match.group(0)).upper()
    power_range = ranges.get(model)
    if not power_range:
        return
    minimum, maximum, page_number = power_range
    min_text = f"{minimum:g}".replace(".", ",")
    max_text = f"{maximum:g}".replace(".", ",")
    product.attributes_normalized.setdefault(
        "теплопроизводительность отопления, мин., квт",
        min_text,
    )
    product.attributes_normalized.setdefault(
        "теплопроизводительность отопления, макс., квт",
        max_text,
    )
    product.attributes_normalized.setdefault(
        "диапазон мощности отопления по паспорту",
        f"{min_text}–{max_text} кВт",
    )
    product.attributes_normalized.setdefault(
        "источник диапазона мощности",
        f"{path.name}, стр. {page_number}",
    )


def _compact_document_text(text: str, limit: int = MAX_DOC_CHARS) -> str:
    """Keep the beginning and useful body sections of a long product manual.

    Taking only the first N characters preserved the table of contents but often
    discarded the actual ``Комплект поставки`` section.  That made the chat agent
    claim that a passport had no package information even though it did.  The
    compact form stays bounded for prompts while retaining the best occurrence of
    the sections customers most often ask about.
    """
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized

    chunks: list[str] = [normalized[:2200]]
    occupied: list[tuple[int, int]] = [(0, 2200)]
    budgets = [2400, 1300, 900, 900]
    for markers, budget in zip(_IMPORTANT_SECTION_MARKERS, budgets):
        index = _best_section_index(normalized, markers)
        if index is None:
            continue
        start = max(0, index - 120)
        end = min(len(normalized), start + budget)
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        chunks.append(normalized[start:end])
        occupied.append((start, end))

    compact = "\n\n".join(chunks)
    return compact[:limit]


def _best_section_index(text: str, markers: tuple[str, ...]) -> int | None:
    lower = text.lower()
    candidates: list[tuple[int, int]] = []
    for marker in markers:
        for match in re.finditer(re.escape(marker), lower):
            index = match.start()
            before = lower[max(0, index - 40) : index]
            after = lower[index : index + 900]
            score = 0
            # A numbered heading in the body is more useful than a mention in the
            # introduction or table of contents.
            if re.search(r"(?:^|\s)\d{1,2}[.)]?\s*$", before):
                score += 4
            if "в комплект поставки входят" in after:
                score += 10
            if "поставляются в комплекте" in after:
                score += 5
            if any(anchor in after for anchor in ["входят:", "1.", "2.", "технические данные"]):
                score += 3
            # Prefer actual body occurrences over an early contents entry when
            # scores otherwise tie.
            score += min(index // 5000, 5)
            candidates.append((score, index))
    if not candidates:
        return None
    return max(candidates)[1]


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
            has_boiler_target = any(
                "кот" in normalize_text(f"{product.category_path} {product.name}")
                for product in targets
            )
            boiler_power_ranges = (
                _extract_boiler_power_ranges(path) if has_boiler_target else {}
            )
            for product in targets:
                if product.docs_text:
                    product.docs_text = (product.docs_text + " " + text)[:MAX_DOC_CHARS]
                else:
                    product.docs_text = text
                _attach_passport_power_range(product, path, boiler_power_ranges)
            attached_docs += 1
    return attached_docs
