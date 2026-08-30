"""Привязка документации товаров (паспорта, инструкции) к карточкам фида.

Поддерживаются два каталога (по умолчанию app/data/product_docs и data/)
и три способа привязки документа к товарам:

1. Карта product_docs_map.json в каталоге с документами — для серийных
   паспортов, покрывающих несколько артикулов:

   {
     "exact-model.pdf": {"skus": ["EXACT.ARTICLE"]},
     "VT.033-034-0425.pdf": {"sku_prefixes": ["VT.033", "VT.034"]},
     "газовые котлы ARDERIA.pdf": {"brand": "Arderia", "name_contains_any": ["газовый"]}
   }

2. Имя файла, равное артикулу: `VT.1500.0.0.pdf` (слэши -> дефисы: 68/2/8 -> 68-2-8.pdf).
3. Имя файла вида `VT.226-227-228-1248в.pdf` — серии раскрываются по общему префиксу.

Поддерживаются .pdf, .txt и .md. Каждый источник сохраняется отдельно в
Product.documents вместе с именем файла, типом документа, числом страниц и
картой найденных разделов. Объединённый текст также попадает в Product.docs_text
для совместимости со старыми кэшами и существующими агентами. В поиск по
категориям этот текст намеренно не подмешивается, чтобы упоминание насоса в
паспорте котла не превращало котёл в насос.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.agents.utils import normalize_sku, normalize_text
from app.models import (
    Product,
    ProductDocument,
    ProductDocumentFact,
    ProductDocumentFlowHeadPoint,
)


logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 8000
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}
MAP_FILENAME = "product_docs_map.json"

_DOCUMENT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("комплект поставки", ("комплект поставки", "комплектность", "комплектация")),
    (
        "технические характеристики",
        ("технические характеристики", "основные характеристики"),
    ),
    ("меры безопасности", ("меры безопасности", "требования безопасности")),
    ("монтаж и подключение", ("монтаж", "подключение")),
)
_IMPORTANT_SECTION_MARKERS: tuple[tuple[str, ...], ...] = tuple(
    markers for _, markers in _DOCUMENT_SECTIONS
)

# Кэш извлечённого текста, чтобы не перечитывать PDF при каждом создании оркестратора.
_TEXT_CACHE: dict[tuple[str, float, str], str] = {}
_DOCUMENT_CACHE: dict[tuple[str, float, str], ProductDocument] = {}
_BOILER_POWER_RANGE_CACHE: dict[
    tuple[str, float], dict[str, tuple[float, float, int]]
] = {}
_UNIPUMP_ECO_VINT_FLOW_CACHE: dict[tuple[str, float], dict[str, float]] = {}
_UNIPUMP_ECO_VINT_QH_CACHE: dict[
    tuple[str, float], dict[str, tuple[tuple[float, float], ...]]
] = {}

SERIES_FILENAME_RE = re.compile(r"^([A-Za-z]+\.)(\d+(?:[-–]\w+)+)")


def _doc_key(value: str) -> str:
    return normalize_sku(value).replace("/", "-")


def _read_pdf_pages(path: Path, extraction_mode: str = "plain") -> list[str]:
    """Return one extracted string per PDF page, preserving page boundaries."""

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if extraction_mode == "layout":
            return [
                (page.extract_text(extraction_mode="layout") or "")
                for page in reader.pages
            ]
        return [(page.extract_text() or "") for page in reader.pages]
    except ImportError:
        logger.warning("pypdf не установлен — пропускаю %s", path.name)
    except Exception as exc:
        logger.warning("Не удалось прочитать PDF %s: %s", path.name, exc)
    return []


def _document_kind(path: Path, text: str) -> str:
    evidence = normalize_text(f"{path.stem} {text[:1600]}")
    if "сертификат" in evidence or "декларация соответствия" in evidence:
        return "certificate"
    if "паспорт" in evidence:
        return "passport"
    if any(marker in evidence for marker in ("руководство", "инструкция")):
        return "instruction"
    return "technical_document"


def _section_page_map(pages: list[str]) -> dict[str, int]:
    """Locate the most useful page for common manual sections.

    A contents page can mention every section, so candidates are scored using
    nearby body-language (for example ``в комплект поставки входят``).  Page
    numbers are one-based, matching the page numbering used by PDF readers.
    """

    result: dict[str, int] = {}
    normalized_pages = [normalize_text(page) for page in pages]
    for section_name, markers in _DOCUMENT_SECTIONS:
        candidates: list[tuple[int, int]] = []
        for page_number, text in enumerate(normalized_pages, start=1):
            if not any(marker in text for marker in markers):
                continue
            score = 1
            if section_name == "комплект поставки":
                if "в комплект поставки входят" in text:
                    score += 20
                if "поставляются в комплекте" in text:
                    score += 10
                if any(marker in text for marker in ("1.", "2.", "шт.")):
                    score += 3
            elif section_name == "технические характеристики":
                if any(marker in text for marker in ("параметр", "значение", "единица")):
                    score += 5
            elif section_name == "меры безопасности":
                if any(marker in text for marker in ("запрещается", "опасност", "внимание")):
                    score += 5
            elif section_name == "монтаж и подключение":
                if any(marker in text for marker in ("установ", "схема", "присоедин")):
                    score += 5
            # On an otherwise equal score, prefer the later body occurrence
            # over an early table-of-contents reference.
            candidates.append((score, page_number))
        if candidates:
            result[section_name] = max(candidates)[1]
    return result


def _extract_document(path: Path, text_mode: str = "plain") -> ProductDocument:
    cache_key = (str(path), path.stat().st_mtime, text_mode)
    if cache_key in _DOCUMENT_CACHE:
        return _DOCUMENT_CACHE[cache_key].model_copy(deep=True)
    if path.suffix.lower() == ".pdf":
        pages = (
            _read_pdf_pages(path, extraction_mode=text_mode)
            if text_mode != "plain"
            else _read_pdf_pages(path)
        )
        raw_text = "\n".join(pages)
        page_count: int | None = len(pages) or None
        section_pages = _section_page_map(pages)
    else:
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
        page_count = None
        section_pages = {}
    text = _compact_document_text(raw_text)
    document = ProductDocument(
        filename=path.name,
        document_kind=_document_kind(path, text),
        text=text,
        page_count=page_count,
        section_pages=section_pages,
    )
    _DOCUMENT_CACHE[cache_key] = document
    _TEXT_CACHE[cache_key] = text
    return document.model_copy(deep=True)


def _extract_text(path: Path) -> str:
    """Compatibility wrapper for callers that only need flattened text."""

    return _extract_document(path).text


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


_VRS_SPEC_CACHE: dict[tuple[str, float], dict[str, dict[str, str]]] = {}

# Строки таблицы, которые переносим в атрибуты.
#
# Имена намеренно содержат скорость. В фиде «максимальный напор, м» означает
# разное: у VRS.254 и VRS.256 это третья скорость (4,2 и 6), а у VRS.258 —
# вторая (8 при паспортных 8,5 на третьей). Записать паспортное значение под
# фидовым именем значило бы смешать два разных числа — ровно та путаница, из-за
# которой запрос «напор 4» не находит насос с маркировкой 25/4.
#
# Монтажная длина здесь не для записи, а для проверки разметки колонок.
_VRS_SPEC_ROWS: tuple[tuple[str, str, str], ...] = (
    ("3.3", "максимальный расход (скорость iii), м3/ч", "speed"),
    ("3.1", "максимальный расход (скорость i), м3/ч", "speed"),
    ("2.3", "максимальный напор (скорость iii), м", "speed"),
    ("9", "минимальное статическое давление, бар", "speed"),
    ("5", "вес, кг", "column"),
)


def _vrs_numbers(line: str) -> list[str]:
    """Вытащить числовые значения строки таблицы, отбросив её номер и единицу.

    Единица «м3/час» сама содержит цифру, и обрезка «до первой цифры»
    останавливалась на ней, превращая тройку значений в четвёрку и ломая
    сопоставление с колонками. Поэтому составные единицы убираются явно.
    """

    body = re.sub(r"^\s*\d+(?:\.\d+)?\s*", "", line)
    body = re.sub(r"м\s*3\s*/\s*час|м³\s*/\s*ч", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"^[^0-9]*", "", body)
    return re.findall(r"\d+(?:[.,]\d+)?", body)


def _parse_vrs_specification(path: Path) -> dict[str, dict[str, str]]:
    """Разобрать таблицу характеристик паспорта циркуляционных насосов VRS.

    Таблица использует объединённые ячейки, и после извлечения текста от них
    остаётся только число значений в строке. Оно и определяет, к чему значение
    относится:

    * восемь значений — по одному на колонку (254.130, 254.180, 324.180, …);
    * шесть — по одному на семейство (254, 324, 256, 326, 258, 328);
    * три — по одному на группу напора: {254, 324}, {256, 326}, {258, 328}.

    Догадка здесь недопустима, поэтому разбор себя проверяет: строка
    «Монтажная длина» обязана совпасть с суффиксами колонок из шапки. Если не
    совпала — разметка колонок прочитана неверно, и вся таблица отбрасывается.
    """

    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _VRS_SPEC_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, dict[str, str]] = {}
    try:
        from pypdf import PdfReader

        lines: list[str] = []
        for page in PdfReader(str(path)).pages:
            lines.extend((page.extract_text() or "").splitlines())
        result = _parse_vrs_lines(lines)
    except Exception as exc:  # pragma: no cover - защита от битого PDF
        logger.warning("Не удалось разобрать таблицу VRS из %s: %s", path.name, exc)
        result = {}
    _VRS_SPEC_CACHE[cache_key] = result
    return result


def _parse_vrs_lines(lines: list[str]) -> dict[str, dict[str, str]]:
    stripped = [line.strip() for line in lines if line.strip()]

    # Шапка: пары строк «254.» и «130» идут подряд после «Значение для типа».
    try:
        head = next(
            index
            for index, line in enumerate(stripped)
            if "Значение для типа" in line
        )
    except StopIteration:
        return {}
    columns: list[tuple[str, str]] = []
    index = head + 1
    while index + 1 < len(stripped):
        family = re.fullmatch(r"(\d{3})\.", stripped[index])
        mounting = re.fullmatch(r"(\d{3})", stripped[index + 1])
        if not family or not mounting:
            break
        columns.append((family.group(1), mounting.group(1)))
        index += 2
    if len(columns) < 4:
        return {}

    families: list[str] = []
    for family, _ in columns:
        if family not in families:
            families.append(family)
    # Группа напора — это цифра напора в номере семейства: 254 и 324 → 4.
    head_groups: list[str] = []
    for family in families:
        digit = family[2]
        if digit not in head_groups:
            head_groups.append(digit)

    # Строки таблицы читаем только после её шапки. Выше по документу лежит
    # легенда расшифровки маркировки, где строка « 1 2 3 4 5 6» нумерует части
    # обозначения: она выглядит как строка таблицы №1 и перехватывала её.
    rows: dict[str, list[str]] = {}
    body = stripped[index:]
    for position, line in enumerate(body):
        marker = re.match(r"^(\d+(?:\.\d+)?)\s", line)
        if not marker:
            continue
        values = _vrs_numbers(line)
        if not values:
            # Длинное название переносится, и значения оказываются на
            # следующей строке вместе с единицей: «9 Минимальное статическое» /
            # «давление» / «бар 0,7 0,9 1,0».
            for offset in (1, 2):
                if position + offset >= len(body):
                    break
                nxt = body[position + offset]
                if re.match(r"^\d+(?:\.\d+)?\s", nxt):
                    break
                values = _vrs_numbers(nxt)
                if values:
                    break
        if values:
            rows.setdefault(marker.group(1), values)

    mounting_row = rows.get("1")
    if not mounting_row or len(mounting_row) != len(columns):
        return {}
    if [value for value in mounting_row] != [mounting for _, mounting in columns]:
        # Колонки прочитаны неверно: дальше сопоставлять нечего.
        return {}

    result: dict[str, dict[str, str]] = {
        f"{family}.{mounting}": {} for family, mounting in columns
    }
    for row_key, attribute, layout in _VRS_SPEC_ROWS:
        values = rows.get(row_key)
        if not values:
            continue
        if layout == "column" and len(values) == len(columns):
            for (family, mounting), value in zip(columns, values):
                result[f"{family}.{mounting}"][attribute] = value.replace(",", ".")
            continue
        if len(values) == len(head_groups):
            per_group = dict(zip(head_groups, values))
            for family, mounting in columns:
                value = per_group.get(family[2])
                if value:
                    result[f"{family}.{mounting}"][attribute] = value.replace(",", ".")
            continue
        if len(values) == len(families):
            per_family = dict(zip(families, values))
            for family, mounting in columns:
                value = per_family.get(family)
                if value:
                    result[f"{family}.{mounting}"][attribute] = value.replace(",", ".")
    return {key: value for key, value in result.items() if value}


def _attach_vrs_pump_specification(
    product: Product,
    path: Path,
    spec: dict[str, dict[str, str]],
) -> None:
    """Перенести характеристики из таблицы паспорта VRS в атрибуты насоса.

    Расхода в фиде нет ни у одной позиции, поэтому подбор по рабочей точке
    Q–H был невозможен в принципе: половина условия отсутствовала. В паспорте
    он есть, причём по скоростям.

    Значение фида остаётся авторитетным: ``setdefault`` не перезаписывает уже
    известное, а имена паспортных атрибутов содержат скорость и потому не
    сталкиваются с фидовыми.
    """

    if not spec:
        return
    model = re.match(r"^vrs\.(\d{3})\.(\d{2})\b", normalize_text(product.sku))
    if not model:
        return
    column = f"{model.group(1)}.{model.group(2)}0"
    values = spec.get(column)
    if not values:
        return
    for key, value in values.items():
        product.attributes_normalized.setdefault(key, value)


_PIPE_CLASS_CACHE: dict[tuple[str, float], dict[str, str]] = {}

# Классы эксплуатации по ГОСТ 32415-2013 в том виде, в каком паспорт их
# называет. Ключ — обозначение класса в таблице, значение — имя атрибута.
_PIPE_CLASS_ATTRIBUTES: dict[str, str] = {
    "1": "рабочее давление, гвс 60 °с, бар",
    "2": "рабочее давление, гвс 70 °с, бар",
    "4": "рабочее давление, напольное отопление, бар",
    "5": "рабочее давление, радиаторное отопление, бар",
    "хв": "рабочее давление, холодное водоснабжение, бар",
}

_PIPE_CLASS_START_RE = re.compile(r"(?<![\w,.])(ХВ|[1245])\s+(?=[А-ЯЁ])")
_PIPE_TEMPERATURE_RE = re.compile(r"(\d{2,3})\s*[º°o]\s*[СC]")


def _parse_pipe_operating_classes(path: Path) -> dict[str, str]:
    """Прочитать таблицу классов эксплуатации из паспорта полипропиленовой трубы.

    Именно этих значений боту не хватает: на запрос по отоплению он отвечает,
    что рабочая температура и давление не подтверждены, хотя в паспорте они
    расписаны по классам — 90 °С и 6 бар для радиаторного отопления у PP-FIBER
    PN20 против 95 °С и 10 бар у PP-ALUX PN25.

    Разбирается только табличная форма «класс — описание с температурой —
    давление». У неармированной серии давления даны в МПа внутри размерной
    таблицы и без температур; догадываться о них по номеру класса нельзя,
    поэтому такой паспорт возвращает пустой результат.
    """

    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _PIPE_CLASS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, str] = {}
    try:
        from pypdf import PdfReader

        lines: list[str] = []
        for page in PdfReader(str(path)).pages:
            lines.extend((page.extract_text() or "").splitlines())
        result = _parse_pipe_class_lines(lines)
    except Exception as exc:  # pragma: no cover - защита от битого PDF
        logger.warning("Не удалось разобрать классы эксплуатации из %s: %s", path.name, exc)
        result = {}
    _PIPE_CLASS_CACHE[cache_key] = result
    return result


def _parse_pipe_class_lines(lines: list[str]) -> dict[str, str]:
    stripped = [line.strip() for line in lines if line.strip()]
    try:
        start = next(
            index
            for index, line in enumerate(stripped)
            if "давление, бар" in normalize_text(line)
        )
    except StopIteration:
        return {}

    # Таблица заканчивается там, где начинается следующий раздел паспорта.
    block: list[str] = []
    for line in stripped[start + 1 :]:
        if re.match(r"^\d+\s*\.\s*[А-ЯЁ]", line) or "Технические характеристики" in line:
            break
        block.append(line)
    if not block:
        return {}

    joined = " ".join(block)
    marks = list(_PIPE_CLASS_START_RE.finditer(joined))
    if not marks:
        return {}

    result: dict[str, str] = {}
    temperatures: list[int] = []
    for position, mark in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(joined)
        segment = joined[mark.end() : end]
        key = normalize_text(mark.group(1))
        attribute = _PIPE_CLASS_ATTRIBUTES.get(key)
        if not attribute:
            continue
        temperature = _PIPE_TEMPERATURE_RE.search(segment)
        if temperature:
            temperatures.append(int(temperature.group(1)))
            segment = segment[: temperature.start()] + segment[temperature.end() :]
        pressures = re.findall(r"\d+(?:[.,]\d+)?", segment)
        if not pressures:
            continue
        result[attribute] = pressures[-1].replace(",", ".")

    if temperatures:
        result["максимальная рабочая температура, °с"] = str(max(temperatures))
    return result


def _attach_pipe_operating_classes(
    product: Product,
    classes: dict[str, str],
) -> None:
    """Перенести классы эксплуатации в атрибуты трубы, не трогая данные фида."""

    for key, value in classes.items():
        product.attributes_normalized.setdefault(key, value)


_PIPE_DIMENSION_CACHE: dict[tuple[str, float], dict[str, dict[str, str]]] = {}

# Строки размерной таблицы, которые переносим в атрибуты. Ключ — узнаваемый
# фрагмент названия строки в паспорте.
_PIPE_DIMENSION_ROWS: tuple[tuple[str, str], ...] = (
    ("внутренний диаметр", "внутренний диаметр, мм"),
    ("номинальная толщина стенки", "толщина стенки (мм)"),
    ("вес трубы", "вес трубы, кг/м"),
    ("объем жидкости", "объём жидкости, л/м"),
    ("стандартное размерное соотношение", "sdr"),
    ("максимальная рабочая температура", "максимальная рабочая температура, °с"),
)

_PIPE_SIZE_RE = re.compile(r"(\d{2,3})\s*[хx]\s*(\d{1,2}(?:[.,]\d+)?)")


def _trailing_numbers(line: str, count: int) -> list[str] | None:
    """Вернуть ровно ``count`` чисел, если строка ими заканчивается.

    Названия строк сами содержат цифры — «Объём жидкости в 1 м.п.», «PN, МПа».
    Считать все числа подряд нельзя, поэтому берём только замыкающую серию
    нужной длины.
    """

    pattern = r"(?<![\d,.])" + r"\s+".join([r"\d+(?:[.,]\d+)?"] * count) + r"\s*$"
    match = re.search(pattern, line)
    if not match:
        return None
    return re.findall(r"\d+(?:[.,]\d+)?", match.group(0))


def _parse_pipe_dimension_lines(lines: list[str]) -> dict[str, dict[str, str]]:
    """Разобрать таблицу «значение характеристики для труб с размерами».

    Колонки — типоразмеры трубы, поэтому позицию определяет её наружный
    диаметр. Разбор себя проверяет: строка «Номинальный наружный диаметр»
    обязана совпасть с диаметрами из шапки. Не совпала — колонки прочитаны
    неверно, и таблица отбрасывается целиком.
    """

    stripped = [line.strip() for line in lines if line.strip()]
    try:
        start = next(
            index
            for index, line in enumerate(stripped)
            if "значение характеристики для труб" in normalize_text(line)
        )
    except StopIteration:
        return {}

    # Шапка типоразмеров: либо одной строкой «20х3,4 25х4,2 …», либо разбитая
    # переносами на «20х» / «3,4».
    diameters: list[str] = []
    cursor = start + 1
    # Строка шапки может оборваться на середине типоразмера: «… 50х8,3 63х», а
    # толщина от него уходит на следующую строку. Такой висящий размер нужно
    # учесть, иначе колонок насчитается меньше, чем значений в строках.
    pending_wall = False
    while cursor < len(stripped):
        line = stripped[cursor]
        if pending_wall:
            pending_wall = False
            if re.fullmatch(r"\d{1,2}(?:[.,]\d+)?", line):
                cursor += 1
                continue
        found = _PIPE_SIZE_RE.findall(line)
        tail = re.search(r"(\d{2,3})\s*[хx]\s*$", line)
        if found or tail:
            diameters.extend(size[0] for size in found)
            if tail:
                diameters.append(tail.group(1))
                pending_wall = True
            cursor += 1
            continue
        head = re.fullmatch(r"(\d{2,3})\s*[хx]", line)
        if head and cursor + 1 < len(stripped):
            diameters.append(head.group(1))
            cursor += 2
            continue
        break
    if len(diameters) < 3:
        return {}

    columns = len(diameters)
    rows: dict[str, list[str]] = {}
    label_parts: list[str] = []
    for line in stripped[cursor:]:
        values = _trailing_numbers(line, columns)
        if values is None:
            if re.match(r"^\d+\s*\.\s*[А-ЯЁ]", line):
                break
            label_parts.append(line)
            if len(label_parts) > 4:
                label_parts = label_parts[-4:]
            continue
        label = normalize_text(" ".join(label_parts + [line]))
        label = re.sub(r"[\d.,]+\s*$", "", label).strip()
        rows.setdefault(label, values)
        label_parts = []

    outer = next(
        (values for label, values in rows.items() if "наружный диаметр" in label),
        None,
    )
    if not outer or [value.split(".")[0] for value in outer] != diameters:
        return {}

    result: dict[str, dict[str, str]] = {diameter: {} for diameter in diameters}
    for label, values in rows.items():
        for marker, attribute in _PIPE_DIMENSION_ROWS:
            if marker not in label:
                continue
            for diameter, value in zip(diameters, values):
                result[diameter][attribute] = value.replace(",", ".")
            break
    return {key: value for key, value in result.items() if value}


def _parse_pipe_dimensions(path: Path) -> dict[str, dict[str, str]]:
    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _PIPE_DIMENSION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result: dict[str, dict[str, str]] = {}
    try:
        from pypdf import PdfReader

        lines: list[str] = []
        for page in PdfReader(str(path)).pages:
            lines.extend((page.extract_text() or "").splitlines())
        result = _parse_pipe_dimension_lines(lines)
    except Exception as exc:  # pragma: no cover - защита от битого PDF
        logger.warning("Не удалось разобрать размеры труб из %s: %s", path.name, exc)
        result = {}
    _PIPE_DIMENSION_CACHE[cache_key] = result
    return result


def _attach_pipe_dimensions(
    product: Product,
    dimensions: dict[str, dict[str, str]],
) -> None:
    """Перенести строку размерной таблицы, соответствующую диаметру трубы."""

    if not dimensions:
        return
    diameter = product.attributes_normalized.get("диаметр (мм)")
    if not diameter:
        match = re.search(r"(\d{2,3})\s*(?:мм|mm)", normalize_text(product.name))
        diameter = match.group(1) if match else None
    if not diameter:
        return
    values = dimensions.get(str(diameter).split(".")[0])
    if not values:
        return
    for key, value in values.items():
        product.attributes_normalized.setdefault(key, value)


_VALVE_SPEC_CACHE: dict[tuple[str, float], dict[str, str]] = {}

# Вертикальная таблица «№ | Характеристика, ед. изм. | Значение | Пояснение»
# из паспортов радиаторной арматуры. У каждой строки одно значение, но рядом
# стоит колонка пояснения — обычный текст с числами, поэтому значение берётся
# строго по образцу, а не «первым числом строки».
_VALVE_SPEC_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        "максимальная температура рабочей среды",
        "максимальная температура рабочей среды, °с",
        r"[ºo°]\s*с\s*([+-]?\d{1,3})",
    ),
    (
        "номинальное давление",
        "номинальное давление, мпа",
        r"мпа\s*(\d+(?:[.,]\d+)?)",
    ),
    (
        "пропускная способность при полностью открытом",
        "пропускная способность kvs, м3/ч",
        r"kvs\s*(\d+(?:[.,]\d+)?)",
    ),
    (
        "номинальный диаметр",
        "номинальный диаметр dn, мм",
        r"мм\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)",
    ),
    (
        "резьба под термостатическую головку",
        "резьба под термоголовку",
        r"(м\s*\d{2}\s*[хx]\s*\d(?:[.,]\d)?)",
    ),
    (
        "средний полный срок службы",
        "срок службы, лет",
        r"лет\s*(\d{1,2})",
    ),
)


def _parse_valve_specification(path: Path) -> dict[str, str]:
    """Прочитать характеристики радиаторной арматуры из паспорта.

    Шестнадцать позиций радиаторной арматуры не имели ни одного содержательного
    атрибута: фид отдаёт только идентификаторы, а разбор названия для клапанов
    ничего не даёт. При этом в паспорте расписаны и температура среды, и Kvs, и
    резьба под термоголовку — последняя определяет, встанет ли головка на
    клапан.
    """

    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _VALVE_SPEC_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result: dict[str, str] = {}
    try:
        from pypdf import PdfReader

        lines: list[str] = []
        for page in PdfReader(str(path)).pages:
            lines.extend((page.extract_text() or "").splitlines())
        result = _parse_valve_spec_lines(lines)
    except Exception as exc:  # pragma: no cover - защита от битого PDF
        logger.warning("Не удалось разобрать характеристики из %s: %s", path.name, exc)
        result = {}
    _VALVE_SPEC_CACHE[cache_key] = result
    return result


def _parse_valve_spec_lines(lines: list[str]) -> dict[str, str]:
    stripped = [line.strip() for line in lines if line.strip()]
    try:
        start = next(
            index
            for index, line in enumerate(stripped)
            if "технические характеристики" in normalize_text(line)
        )
    except StopIteration:
        return {}

    # Строки таблицы нумерованы, а их названия и значения переносятся. Собираем
    # каждую строку целиком до следующего номера.
    rows: list[str] = []
    current: list[str] = []
    for line in stripped[start + 1 :]:
        if re.match(r"^\d{1,2}\s+[А-ЯЁA-Z]", line):
            if current:
                rows.append(" ".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        rows.append(" ".join(current))

    result: dict[str, str] = {}
    for row in rows:
        text = normalize_text(row)
        for marker, attribute, pattern in _VALVE_SPEC_ROWS:
            if marker not in text or attribute in result:
                continue
            match = re.search(pattern, text)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip()
                # Запятая означает разное: в «1,0» это десятичный разделитель, а
                # в «15, 20» — перечисление двух типоразмеров. Заменять её на
                # точку можно только в одиночном числе, иначе DN 15 и 20
                # склеиваются в несуществующий размер «15.20».
                if re.fullmatch(r"\d+,\d+", value):
                    value = value.replace(",", ".")
                result[attribute] = value
            break
    return result


def _attach_valve_specification(product: Product, values: dict[str, str]) -> None:
    """Дополнить арматуру характеристиками из паспорта, не трогая фид."""

    for key, value in values.items():
        product.attributes_normalized.setdefault(key, value)


def _attach_confirmed_connection_facts(
    product: Product,
    path: Path,
    docs_text: str,
) -> None:
    """Enrich structured fields only when a model table proves the mapping.

    The VRS passport defines the first two digits after ``VRS.`` as nominal DN
    and its technical table maps DN25 to G 1 1/2 and DN32 to G 2.  Keeping those
    facts as structured attributes lets cards and deterministic follow-ups cite
    them without asking an LLM to reconstruct a flattened PDF table.
    """
    sku = normalize_text(product.sku)
    model = re.match(r"^vrs\.(25|32)[468]\.", sku)
    text = normalize_text(docs_text)
    if not model:
        return
    if not all(
        marker in text
        for marker in [
            "диаметр условного прохода",
            "присоединительная резьба",
            "типы vrs.254",
        ]
    ):
        return
    nominal_dn = model.group(1)
    thread = '1 1/2"' if nominal_dn == "25" else '2"'
    product.attributes_normalized.setdefault(
        "диаметр условного прохода, мм",
        nominal_dn,
    )
    product.attributes_normalized.setdefault(
        "присоединительная резьба, дюйм",
        thread,
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
    exact_skus = {_doc_key(str(sku)) for sku in rule.get("skus", []) if str(sku)}
    prefixes = [_doc_key(prefix) for prefix in rule.get("sku_prefixes", [])]
    brand = normalize_text(str(rule["brand"])) if rule.get("brand") else None
    name_needles = [normalize_text(str(n)) for n in rule.get("name_contains_any", [])]
    matched: list[Product] = []
    for product in products:
        if exact_skus:
            if _doc_key(product.sku) in exact_skus:
                matched.append(product)
            continue
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


def _attach_document_evidence(
    product: Product,
    document: ProductDocument,
) -> None:
    """Attach one source without flattening away its identity.

    Reloading the same directory is idempotent.  If a file changed in place,
    its structured record is replaced and the corresponding legacy text is
    updated when it can be identified safely.
    """

    previous: ProductDocument | None = None
    for index, existing in enumerate(product.documents):
        if existing.filename != document.filename:
            continue
        previous = existing
        if existing != document:
            product.documents[index] = document.model_copy(deep=True)
        break
    else:
        product.documents.append(document.model_copy(deep=True))

    current_text = product.docs_text or ""
    if previous and previous.text != document.text and previous.text in current_text:
        current_text = current_text.replace(previous.text, document.text, 1)
    elif document.text and document.text not in current_text:
        current_text = (
            f"{current_text}\n\n{document.text}" if current_text else document.text
        )
    product.docs_text = current_text[:MAX_DOC_CHARS] or None


def _attach_document_fact(product: Product, fact: ProductDocumentFact) -> None:
    """Attach a model-scoped, deterministic document fact idempotently."""

    identity = (fact.name, fact.document, fact.section)
    for index, existing in enumerate(product.document_facts):
        if (existing.name, existing.document, existing.section) == identity:
            product.document_facts[index] = fact
            return
    product.document_facts.append(fact)


def _attach_document_flow_head_point(
    product: Product,
    point: ProductDocumentFlowHeadPoint,
) -> None:
    """Attach one exact document curve point idempotently."""

    identity = (point.flow_l_h, point.document, point.section)
    for index, existing in enumerate(product.document_flow_head_points):
        if (existing.flow_l_h, existing.document, existing.section) == identity:
            product.document_flow_head_points[index] = point
            return
    product.document_flow_head_points.append(point)


def _parse_unipump_eco_vint_max_flow(path: Path) -> dict[str, float]:
    """Read the shared maximum-flow row of the ECO VINT model table.

    This is intentionally a narrow table parser, not a semantic search over
    arbitrary passport text.  It accepts a value only when the table heading
    explicitly names all three ECO VINT models and the flow row supplies the
    value in m³/h.  A shared row is then safe to attach to each named model.
    """

    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _UNIPUMP_ECO_VINT_FLOW_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    result: dict[str, float] = {}
    pages = _read_pdf_pages(path)
    for page in pages:
        # The Russian passport types the series as ``ЕСО VINT`` with Cyrillic
        # letters while the feed uses Latin ``ECO VINT``.  This is a bounded
        # document-label normalization, not a fuzzy model match.
        normalized = normalize_text(page).replace("есо", "eco")
        if not all(model in normalized for model in ("eco vint 1", "eco vint 2", "eco vint 3")):
            continue
        match = re.search(
            r"макс\.?\s*производительност[^\n]{0,100}?"
            r"\(\s*м[3³]\s*/\s*ч\s*\)\s*"
            r"\d+(?:[.,]\d+)?\s*\(\s*(?P<flow>\d+(?:[.,]\d+)?)\s*\)",
            page,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        value = float(match.group("flow").replace(",", "."))
        if value <= 0:
            continue
        result = {f"eco vint {number}": value * 1000 for number in ("1", "2", "3")}
        break
    _UNIPUMP_ECO_VINT_FLOW_CACHE[cache_key] = dict(result)
    return result


def _parse_unipump_eco_vint_flow_head_table(
    path: Path,
) -> dict[str, tuple[tuple[float, float], ...]]:
    """Read only exact rows of the ECO VINT Q/H table.

    The parser intentionally requires all three model rows and the explicit
    m³/h header.  It does not interpolate points, approximate a chart, or
    infer a curve from independent maximum values.
    """

    if path.suffix.lower() != ".pdf":
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    cached = _UNIPUMP_ECO_VINT_QH_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    result: dict[str, tuple[tuple[float, float], ...]] = {}
    for page in _read_pdf_pages(path):
        normalized = normalize_text(page).replace("есо", "eco")
        if "напорно-расходные характеристики" not in normalized:
            continue
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        flow_values: tuple[float, ...] | None = None
        flow_line_index: int | None = None
        for line_index, line in enumerate(lines):
            normalized_line = normalize_text(line)
            if not re.search(r"\bq\s*,?\s*м[3³]\s*/\s*ч\b", normalized_line):
                continue
            suffix = re.sub(
                r"(?iu)^\s*q\s*,?\s*м[3³]\s*/\s*ч\s*",
                "",
                line,
            )
            values = tuple(
                float(item.replace(",", "."))
                for item in re.findall(r"\d+(?:[.,]\d+)?", suffix)
            )
            if len(values) == 6 and values == (0.0, 0.3, 0.6, 0.9, 1.2, 1.5):
                flow_values = values
                flow_line_index = line_index
                break
        if flow_values is None or flow_line_index is None:
            continue

        parsed: dict[str, tuple[tuple[float, float], ...]] = {}
        # Do not scan the dimensional table above or the plot legend below.
        # Both repeat the model labels and are not Q/H table rows.
        for relative_index, line in enumerate(lines[flow_line_index + 1 :]):
            index = flow_line_index + 1 + relative_index
            model_match = re.search(
                r"(?iu)^\s*(?:eco|есо)\s+vint\s+(?P<model>[123])\b",
                line,
            )
            if model_match is None:
                continue
            values: tuple[float, ...] = ()
            # ECO VINT 2 and 3 have their six head values in the same
            # extracted table row, after the power column.  Remove the model
            # label before reading numbers so the «2»/«3» does not become a
            # fake table value.
            same_line_values = tuple(
                float(item.replace(",", "."))
                for item in re.findall(
                    r"\d+(?:[.,]\d+)?",
                    line[model_match.end() :],
                )
            )
            if len(same_line_values) == len(flow_values) + 1:
                values = same_line_values[1:]
            # In the extracted table, the label «Напор (H), м» may occupy a
            # line between the model/power and six head values.
            if not values:
                for next_line in lines[index + 1 : index + 4]:
                    numbers = tuple(
                        float(item.replace(",", "."))
                        for item in re.findall(r"\d+(?:[.,]\d+)?", next_line)
                    )
                    if len(numbers) == len(flow_values):
                        values = numbers
                        break
            if len(values) != len(flow_values):
                continue
            model = f"eco vint {model_match.group('model')}"
            parsed[model] = tuple(
                (flow_m3_h * 1000, head_m)
                for flow_m3_h, head_m in zip(flow_values, values, strict=True)
            )
        if set(parsed) == {"eco vint 1", "eco vint 2", "eco vint 3"}:
            result = parsed
            break
    _UNIPUMP_ECO_VINT_QH_CACHE[cache_key] = dict(result)
    return result


def _unipump_eco_vint_model(product: Product) -> str | None:
    """Return an exact model label only when the product itself names one."""

    match = re.search(
        r"\beco\s+vint\s+(?P<model>[123])\b",
        normalize_text(product.name),
        flags=re.IGNORECASE,
    )
    return f"eco vint {match.group('model')}" if match is not None else None


def _attach_unipump_eco_vint_document_facts(
    product: Product,
    document: ProductDocument,
    max_flows_l_h: dict[str, float],
) -> None:
    """Project one exact model-table value without modifying the feed card."""

    model = _unipump_eco_vint_model(product)
    value = max_flows_l_h.get(model or "")
    if value is None:
        return
    rendered = int(value) if value.is_integer() else value
    _attach_document_fact(
        product,
        ProductDocumentFact(
            name="max_flow_l_h",
            value=rendered,
            unit="l/h",
            document=document.filename,
            section=(
                "3.2 Технические характеристики, модель "
                f"{model.upper()}"
            ),
            evidence=(
                "Макс. производительность, л/мин (м³/ч): "
                f"25 (1,5); {model.upper()}"
            ),
            parser="unipump_eco_vint_shared_flow_table_v1",
        ),
    )


def _attach_unipump_eco_vint_flow_head_points(
    product: Product,
    document: ProductDocument,
    table: dict[str, tuple[tuple[float, float], ...]],
) -> None:
    """Project a model's exact passport Q/H points into typed source data."""

    model = _unipump_eco_vint_model(product)
    points = table.get(model or "")
    if not points:
        return
    for flow_l_h, head_m in points:
        rendered_flow = int(flow_l_h) if flow_l_h.is_integer() else flow_l_h
        rendered_head = int(head_m) if head_m.is_integer() else head_m
        _attach_document_flow_head_point(
            product,
            ProductDocumentFlowHeadPoint(
                flow_l_h=flow_l_h,
                head_m=head_m,
                document=document.filename,
                section=(
                    "3.4 Напорно-расходные характеристики, модель "
                    f"{model.upper()}"
                ),
                evidence=(
                    f"{model.upper()}: Q={rendered_flow} л/ч; "
                    f"H={rendered_head} м"
                ),
                parser="unipump_eco_vint_exact_qh_table_v1",
            ),
        )


def _document_binding_scope(
    product: Product,
    rule: dict[str, Any] | None,
    *,
    filename_fallback: bool,
) -> tuple[str, str | None]:
    """Return provenance for one already matched product/document pair.

    This does not change document matching.  Its purpose is to distinguish an
    exact SKU binding from a helpful but wider series/brand mapping when a
    downstream V2 workflow needs a model-specific interface fact.
    """

    if rule:
        exact_skus = {_doc_key(str(sku)) for sku in rule.get("skus", []) if str(sku)}
        if _doc_key(product.sku) in exact_skus:
            return "exact_sku", product.sku
        prefixes = [_doc_key(prefix) for prefix in rule.get("sku_prefixes", [])]
        matched_prefix = next(
            (prefix for prefix in prefixes if _doc_key(product.sku).startswith(prefix)),
            None,
        )
        if matched_prefix:
            return "sku_prefix", matched_prefix
        if rule.get("brand") or rule.get("name_contains_any"):
            return "catalogue_query", None
    if filename_fallback:
        return "filename_match", product.sku
    return "unknown", None


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
            if rule and rule.get("enabled") is False:
                logger.info("Документ %s отключён в карте — пропускаю", path.name)
                continue
            if rule:
                targets = _match_by_rule(products, rule)
            else:
                targets = _match_by_filename(products, path.stem)
            if not targets:
                logger.warning("Документ %s не совпал ни с одним товаром фида", path.name)
                continue
            text_mode = str((rule or {}).get("pdf_text_mode") or "plain")
            if text_mode not in {"plain", "layout"}:
                logger.warning(
                    "Документ %s: неизвестный pdf_text_mode=%s — использую plain",
                    path.name,
                    text_mode,
                )
                text_mode = "plain"
            document = _extract_document(path, text_mode=text_mode)
            text = document.text
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
            has_vrs_target = any(
                normalize_text(product.sku).startswith("vrs.") for product in targets
            )
            vrs_spec = _parse_vrs_specification(path) if has_vrs_target else {}
            has_pipe_target = any(
                "труба" in normalize_text(product.name) for product in targets
            )
            pipe_classes = (
                _parse_pipe_operating_classes(path) if has_pipe_target else {}
            )
            pipe_dimensions = _parse_pipe_dimensions(path) if has_pipe_target else {}
            has_fitting_target = any(
                any(
                    marker in normalize_text(f"{product.category_path} {product.name}")
                    for marker in ("клапан", "термоголов", "кран", "арматур")
                )
                for product in targets
            )
            valve_spec = (
                _parse_valve_specification(path) if has_fitting_target else {}
            )
            has_unipump_eco_vint_target = any(
                _unipump_eco_vint_model(product) is not None
                for product in targets
            )
            unipump_eco_vint_max_flows = (
                _parse_unipump_eco_vint_max_flow(path)
                if has_unipump_eco_vint_target
                else {}
            )
            unipump_eco_vint_flow_head_table = (
                _parse_unipump_eco_vint_flow_head_table(path)
                if has_unipump_eco_vint_target
                else {}
            )
            for product in targets:
                binding_scope, binding_value = _document_binding_scope(
                    product,
                    rule,
                    filename_fallback=rule is None,
                )
                _attach_document_evidence(
                    product,
                    document.model_copy(
                        update={
                            "binding_scope": binding_scope,
                            "binding_value": binding_value,
                        }
                    ),
                )
                _attach_passport_power_range(product, path, boiler_power_ranges)
                _attach_confirmed_connection_facts(product, path, text)
                _attach_vrs_pump_specification(product, path, vrs_spec)
                _attach_unipump_eco_vint_document_facts(
                    product,
                    document,
                    unipump_eco_vint_max_flows,
                )
                _attach_unipump_eco_vint_flow_head_points(
                    product,
                    document,
                    unipump_eco_vint_flow_head_table,
                )
                if "труба" in normalize_text(product.name):
                    _attach_pipe_operating_classes(product, pipe_classes)
                    _attach_pipe_dimensions(product, pipe_dimensions)
                elif valve_spec:
                    _attach_valve_specification(product, valve_spec)
            attached_docs += 1
    return attached_docs
