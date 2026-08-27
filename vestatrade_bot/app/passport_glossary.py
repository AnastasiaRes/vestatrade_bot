"""Индекс определений, собранный из паспортов товаров.

Живой прогон объяснений показал, что бот отказывается растолковать термины,
значения которых уже лежат в системе: «мокрый ротор» описан в паспорте VRS,
Kvs — в колонке «Пояснение» таблицы клапана, расшифровка маркировки — в
разделе «Обозначение». Отказ честный, но данные есть.

Модуль собирает эти места в индекс «термин → цитата + источник». Текст ответа
берётся из документа дословно и проверяется на вхождение в него: объяснение,
которое покупатель понесёт в магазин, должно быть цитатой производителя, а не
пересказом. Модель здесь не участвует.

Три источника, все структурные:

1. Колонка «Пояснение» таблицы характеристик — готовое определение параметра.
2. Легенда раздела «Обозначение» — расшифровка позиций маркировки.
3. Определительные обороты в тексте: «X представляет собой…»,
   «X предполагает, что…», «Назначение X — …».
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.agents.utils import normalize_text


logger = logging.getLogger(__name__)

MAX_DEFINITION_CHARS = 400


@dataclass(frozen=True)
class PassportDefinition:
    """Определение термина с указанием, откуда оно взято."""

    term: str
    text: str
    source: str
    section: str

    def cite(self) -> str:
        return f"{self.text} (по паспорту {self.source}, {self.section})"


# ВНИМАНИЕ: образцы ищутся с re.IGNORECASE, и под этим флагом класс
# [А-ЯЁ] совпадает и со строчными буквами. Границу «начало следующей
# строки таблицы» через заглавную букву здесь строить нельзя — она
# сработает на «1 бар». Такие границы задаёт табличный извлекатель ниже,
# который ищет без этого флага.
#
# Термины, ради которых индекс и собирается. Ключ — как о термине спрашивает
# покупатель, значение — образец, которым его определение находится в тексте
# паспорта. Образец описывает форму фразы, а не её содержание: текст ответа
# всегда берётся из документа.
_TERM_PATTERNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        # Единственный образец, которому нужен учёт регистра: границей служит
        # номер следующей строки таблицы перед заглавной буквой, а под
        # re.IGNORECASE класс [А-ЯЁ] совпал бы и со строчной «б» из «1 бар».
        "kvs!",
        (
            r"Пропускная способность при полностью открытом клапане[^.]{0,40}?"
            r"Kvs\s*[\d,]+\s+([А-ЯЁ][^.]{10,90}?)(?=\s+\d{1,2}\s+[А-ЯЁ])",
        ),
        "таблица характеристик",
    ),
    (
        "мокрый ротор",
        (
            r"конструктивное исполнение «с мокрым ротором»[^.]{0,300}\.",
            r"исполнение с мокрым ротором[^.]{0,300}\.",
        ),
        "описание конструкции",
    ),

    (
        "армирование алюминием",
        (r"назначение алюминиевого слоя[^.]{0,200}\.",),
        "конструктивные особенности",
    ),
    (
        "класс эксплуатации",
        (
            r"трубы могут применяться для[^.]{0,80}классов эксплуатации[^.]{0,80}\d{4}",
            r"условия применения труб[^.]{0,120}\.",
        ),
        "условия применения",
    ),
    (
        "полифузионная сварка",
        (r"соединения методом полифузионной сварки[^.]{0,200}\.",),
        "назначение и область применения",
    ),
)

# Легенда обозначения: «3 – номинальный диаметр DN в мм (25,32)». Позиции
# разделены точкой с запятой, но она же стоит внутри скобок — «(4;6;8)», —
# поэтому границей служит начало следующей позиции, а не первый разделитель.
_MARKING_LEGEND_RE = re.compile(
    r"(?<![\d.])(\d)\s*[-–]\s*(.+?)(?=\s*;?\s*\d\s*[-–]\s*[а-яёa-z]|$)",
    re.IGNORECASE | re.DOTALL,
)

# Строка таблицы с колонкой пояснения: название, единица, число, затем текст.
# Пояснение может содержать число («перепад давления 1 бар»), поэтому границей
# служит не первая цифра, а начало следующей строки таблицы — её номер перед
# заглавной буквой. Запрет цифр обрывал определение Kvs на «1 бар».
_TABLE_EXPLANATION_RE = re.compile(
    r"\d{1,2}\s+([А-ЯЁ][^0-9]{6,90}?)\s*,?\s*"
    # Только единица измерения. Кириллические слова сюда пускать нельзя: группа
    # начинает откусывать хвост названия, и в индекс попадают обрубки вроде
    # «номинальный д» с чужим пояснением.
    r"([A-Za-zА-Яё³·/%°º,\s]{1,16}?)?"
    r"(\d+(?:[,.]\d+)?)\s+([А-ЯЁ][^.]{15,170}?)"
    r"(?=\s+\d{1,2}\s+[А-ЯЁ]|$)"
)


def _sentences(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _shorten(text: str) -> str:
    cleaned = _sentences(text).strip(" ;.,")
    if len(cleaned) > MAX_DEFINITION_CHARS:
        cleaned = cleaned[:MAX_DEFINITION_CHARS].rsplit(" ", 1)[0]
    return cleaned + "." if not cleaned.endswith(".") else cleaned


def _definitions_from_patterns(
    raw: str,
    normalized: str,
    source: str,
) -> list[PassportDefinition]:
    found: list[PassportDefinition] = []
    for term, patterns, section in _TERM_PATTERNS:
        for pattern in patterns:
            # Образец применяется к исходному тексту, а не к нормализованному.
            # Нормализация меняет длину строки, и позиции совпадения из неё не
            # переносятся на оригинал: цитата уезжала на несколько слов назад и
            # начиналась с середины предыдущего предложения.
            case_sensitive = term.endswith("!")
            match = re.search(
                pattern, raw, 0 if case_sensitive else re.IGNORECASE
            )
            if not match:
                continue
            excerpt = match.group(1) if match.groups() else match.group(0)
            found.append(
                PassportDefinition(
                    term=term.rstrip("!"),
                    text=_shorten(excerpt),
                    source=source,
                    section=section,
                )
            )
            break
    return found


def _definitions_from_marking(
    raw: str,
    normalized: str,
    source: str,
) -> list[PassportDefinition]:
    start = normalized.find("обозначение")
    if start < 0:
        return []
    window = raw[start : start + 600]
    found: list[PassportDefinition] = []
    for match in _MARKING_LEGEND_RE.finditer(window):
        item = _sentences(match.group(2)).strip(" ;.")
        lowered = normalize_text(item)
        # Позиция легенды коротка. Длинный кусок означает, что разбор ушёл за
        # её пределы — в следующий раздел паспорта, где начинается таблица.
        if len(item) > 90 or "характеристик" in lowered:
            continue
        # Из легенды берём только позиции, которые объясняют параметр. Товарный
        # знак и «дополнительные опции» ничего не растолковывают.
        if any(marker in lowered for marker in ("товарный знак", "дополнительн")):
            continue
        term = re.split(r"\s+в\s+мм|\s+в\s+м\.|\s*\(", item)[0].strip()
        if len(term) < 4:
            continue
        definition = PassportDefinition(
            term=normalize_text(term),
            text=_shorten(item),
            source=source,
            section="раздел «Обозначение»",
        )
        found.append(definition)
        # Сокращение внутри позиции — самостоятельный термин: покупатель
        # спрашивает «что такое DN», а не «что такое номинальный диаметр DN».
        abbreviation = re.search(r"\b([A-Z]{2,4})\b", term)
        if abbreviation:
            found.append(
                PassportDefinition(
                    term=normalize_text(abbreviation.group(1)),
                    text=definition.text,
                    source=source,
                    section=definition.section,
                )
            )
    return found


def _definitions_from_table(raw: str, source: str) -> list[PassportDefinition]:
    """Собрать определения из колонки «Пояснение» таблицы характеристик.

    Границы обязательны. Без них образец «название, число, текст с заглавной»
    находит случайные пары на любом многостраничном руководстве: в паспорте
    Arderia так склеивались «в котлах типа atmo» и «параметр работы датчика
    ГВС». Проверка на дословность цитаты такое не ловит — фраза в документе
    есть, неверна привязка к термину. Поэтому таблица берётся только там, где
    колонка «Пояснение» объявлена в шапке, и только до конца этого раздела.
    """

    text = _sentences(raw)
    header = re.search(r"Характеристика.{0,40}?Значение\s+Пояснение", text)
    if not header:
        return []
    tail = text[header.end() :]
    section_end = re.search(r"\s\d{1,2}\s*\.\s*[А-ЯЁ][а-яё]", tail)
    block = tail[: section_end.start()] if section_end else tail[:2500]

    found: list[PassportDefinition] = []
    for match in _TABLE_EXPLANATION_RE.finditer(block):
        label = _sentences(match.group(1)).strip(" ,;")
        unit = _sentences(match.group(2) or "").strip(" ,;")
        explanation = _sentences(match.group(4)).strip(" ,;")
        if len(explanation.split()) < 4:
            continue
        # На границах блока в колонку пояснения попадает колонтитул: «КЛАПАНЫ
        # РАДИАТОРНЫЕ C ПРЕДНАСТРОЙКОЙ Модели: VT.». Заголовок узнаётся по
        # длинному прописному фрагменту и по двоеточию перед артикулом.
        if re.search(r"[А-ЯЁA-Z]{4,}", explanation) or ":" in explanation:
            continue
        term = normalize_text(label)
        # Обрывок в конце названия («уровень шума, д») означает, что колонка
        # прочитана не до конца, и пара «термин — пояснение» ненадёжна.
        if re.search(r",\s*\S{1,2}$", term) or term.endswith("см. раздел"):
            continue
        found.append(
            PassportDefinition(
                term=term,
                text=_shorten(explanation),
                source=source,
                section="таблица характеристик",
            )
        )
        # Хвостовое сокращение в названии — самостоятельный термин: покупатель
        # спрашивает «что такое Kvs», а не «что такое пропускная способность
        # при полностью открытом клапане, м3/час, Kvs».
        alias = re.search(r"(?:^|[,\s])([A-Za-z]{2,5})$", f"{label} {unit}".strip())
        if alias:
            found.append(
                PassportDefinition(
                    term=normalize_text(alias.group(1)),
                    text=_shorten(explanation),
                    source=source,
                    section="таблица характеристик",
                )
            )
    return found


def build_index(docs_dir: Path) -> dict[str, PassportDefinition]:
    """Собрать индекс определений из всех паспортов каталога.

    Ключ — нормализованный термин. При совпадении выигрывает первое найденное:
    паспорта одной серии повторяют формулировки, и брать нужно одну.
    """

    index: dict[str, PassportDefinition] = {}
    if not docs_dir.exists():
        return index
    for path in sorted(docs_dir.iterdir()):
        if path.suffix.lower() != ".pdf":
            continue
        try:
            from pypdf import PdfReader

            raw = _sentences(
                " ".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
            )
        except Exception as exc:  # pragma: no cover - защита от битого PDF
            logger.warning("Не удалось прочитать %s для глоссария: %s", path.name, exc)
            continue
        normalized = normalize_text(raw)
        for definition in (
            *_definitions_from_patterns(raw, normalized, path.name),
            *_definitions_from_marking(raw, normalized, path.name),
            *_definitions_from_table(raw, path.name),
        ):
            # Цитата обязана дословно встречаться в документе: это единственная
            # гарантия, что покупатель получит текст производителя, а не
            # результат склейки regexp-групп.
            quote = definition.text.rstrip(".")
            if quote and quote not in raw:
                continue
            index.setdefault(definition.term, definition)
    return index


def find_definition(
    index: dict[str, PassportDefinition],
    message: str,
) -> PassportDefinition | None:
    """Найти определение термина, о котором спрашивает покупатель.

    Побеждает самый длинный из встретившихся терминов: «номинальный диаметр»
    точнее, чем «диаметр», и покупателю нужен именно он.
    """

    text = normalize_text(message)
    best: PassportDefinition | None = None
    for term, definition in index.items():
        if len(term) < 2:
            continue
        # Короткое обозначение вроде «DN» или «Kvs» ищется как отдельное
        # слово: как подстрока оно нашлось бы внутри чужих слов.
        if len(term) <= 4:
            # Цифра справа означает типоразмер, а не вопрос о термине:
            # «нужен кран DN25» — это запрос товара.
            if re.search(
                rf"(?<![a-zа-яё0-9]){re.escape(term)}(?![a-zа-яё0-9])", text
            ):
                if best is None or len(term) > len(best.term):
                    best = definition
            continue
        # Термин из паспорта бывает длиннее вопроса: индекс хранит
        # «номинальный диаметр dn», покупатель пишет «номинальный диаметр».
        # Совпадением считается вхождение в любую сторону, но короткая часть
        # должна оставаться содержательной.
        matched = term in text or (
            len(term) > 12 and _drop_trailing_abbreviation(term) in text
        )
        if not matched:
            continue
        if best is None or len(term) > len(best.term):
            best = definition
    return best


def _drop_trailing_abbreviation(term: str) -> str:
    """Отбросить хвостовое сокращение: «номинальный диаметр dn» -> «…диаметр»."""

    parts = term.split()
    if len(parts) > 2 and len(parts[-1]) <= 3:
        return " ".join(parts[:-1])
    return term


_DEFAULT_INDEX: dict[str, PassportDefinition] | None = None


def default_index(docs_dirs: list[Path] | None = None) -> dict[str, PassportDefinition]:
    """Общий индекс, собранный один раз на процесс.

    Разбор двух десятков PDF стоит секунд, а результат не меняется между
    запросами, поэтому индекс строится лениво и переиспользуется.
    """

    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        index: dict[str, PassportDefinition] = {}
        for directory in docs_dirs or []:
            for term, definition in build_index(Path(directory)).items():
                index.setdefault(term, definition)
        _DEFAULT_INDEX = index
    return _DEFAULT_INDEX


def reset_default_index() -> None:
    """Сбросить кэш — нужно тестам и перезагрузке каталога."""

    global _DEFAULT_INDEX
    _DEFAULT_INDEX = None
