"""Нарезка паспортов на куски, пригодные для поиска.

Замер на четырнадцати вопросах, заданных словами покупателя, показал: в пяти
случаях из шести нужный пункт в документе есть, но не поднимается в выдаче, а
в одном теряется при нарезке вовсе. Значит, качество нарезки — отдельная
задача, а не подготовительный шаг.

Правила, которые из этого следуют:

* Ничего не теряем. Длинный фрагмент режется на перекрывающиеся окна, а не
  отбрасывается по длине — иначе абзац про гликолевые жидкости исчезает из
  корпуса целиком.
* Перекрытие обязательно: факт у границы куска должен находиться с обеих
  сторон.
* Колонтитулы вырезаются. «ПАСПОРТ. РУКОВОДСТВО ПО ЭКСПЛУАТАЦИИ… ГОСТ Р
  2.601-2019» повторяется на каждой странице и делает все куски похожими друг
  на друга — для поиска по смыслу это чистый шум.
* Таблица — самостоятельный кусок. Ответ «выдержит ли труба 95 °С» живёт в
  таблице классов эксплуатации, а не в нумерованном пункте.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TARGET_CHARS = 700
OVERLAP_CHARS = 150
MIN_CHARS = 80


@dataclass(frozen=True)
class Chunk:
    """Единица поиска: кусок паспорта со своим адресом."""

    document: str
    text: str
    section: str
    ordinal: int


_CLAUSE_RE = re.compile(r"(?=(?<![\d.])\d{1,2}\.\d{1,2}\.?\s+[А-ЯЁA-Z«])")
_TABLE_MARKERS = (
    "значение характеристики для труб",
    "класс эксплуатации",
    "значение для типа",
    "характеристика ед",
    "характеристика, ед",
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_boilerplate(pages: list[str]) -> str:
    """Убрать строки, повторяющиеся на большинстве страниц.

    Колонтитул паспорта занимает до четверти извлечённого текста страницы. Он
    одинаков везде, поэтому по нему любой кусок похож на любой другой.
    """

    if len(pages) < 3:
        return _collapse(" ".join(pages))

    counts: Counter[str] = Counter()
    for page in pages:
        for line in {_collapse(line) for line in page.splitlines()}:
            if len(line) > 12:
                counts[line] += 1

    threshold = max(3, int(len(pages) * 0.6))
    boilerplate = {line for line, hits in counts.items() if hits >= threshold}

    kept: list[str] = []
    for page in pages:
        for line in page.splitlines():
            if _collapse(line) not in boilerplate:
                kept.append(line)
    return _collapse(" ".join(kept))


def _windows(text: str, section: str) -> list[tuple[str, str]]:
    """Порезать длинный фрагмент на перекрывающиеся окна по границам предложений."""

    if len(text) <= TARGET_CHARS:
        return [(text, section)]
    pieces: list[tuple[str, str]] = []
    sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        # Таблица — это один «абзац» без точек: без принудительной резки она
        # остаётся куском на тысячи знаков, где нужная строка тонет.
        while len(sentence) > TARGET_CHARS:
            cut = sentence.rfind(" ", 0, TARGET_CHARS) or TARGET_CHARS
            sentences.append(sentence[:cut])
            sentence = sentence[max(0, cut - OVERLAP_CHARS) :]
        sentences.append(sentence)
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > TARGET_CHARS:
            pieces.append((current.strip(), section))
            # Хвост предыдущего окна уходит в начало следующего: факт на стыке
            # иначе не находится ни в одном из них.
            current = current[-OVERLAP_CHARS:] + " "
        current += sentence + " "
    if current.strip():
        pieces.append((current.strip(), section))
    return pieces


def _section_of(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in _TABLE_MARKERS):
        return "таблица характеристик"
    heading = re.match(r"\s*(\d{1,2})\.\s*([А-ЯЁ][^.]{4,60})", text)
    if heading:
        return _collapse(heading.group(2)).lower()
    clause = re.match(r"\s*(\d{1,2})\.\d{1,2}\.?", text)
    if clause:
        return f"пункт {clause.group(0).strip()}"
    return "текст"


def chunk_pages(pages: list[str], document: str) -> list[Chunk]:
    """Нарезать один документ."""

    text = _strip_boilerplate(pages)
    if not text:
        return []

    # Таблицы вырезаются первыми и целиком: разрезанная таблица теряет связь
    # между строкой и её значением.
    spans: list[tuple[int, int]] = []
    for marker in _TABLE_MARKERS:
        for match in re.finditer(re.escape(marker), text, re.IGNORECASE):
            start = match.start()
            end = min(len(text), start + 1400)
            spans.append((start, end))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    raw_pieces: list[tuple[str, str]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            raw_pieces.extend(_split_prose(text[cursor:start]))
        raw_pieces.extend(_windows(text[start:end], "таблица характеристик"))
        cursor = end
    if cursor < len(text):
        raw_pieces.extend(_split_prose(text[cursor:]))

    chunks: list[Chunk] = []
    for piece, section in raw_pieces:
        piece = piece.strip()
        if len(piece) < MIN_CHARS and chunks:
            # Обрывок приклеивается к предыдущему куску, а не выбрасывается.
            previous = chunks[-1]
            chunks[-1] = Chunk(
                document=previous.document,
                text=f"{previous.text} {piece}".strip(),
                section=previous.section,
                ordinal=previous.ordinal,
            )
            continue
        # Приклеивать не к чему: короткий документ состоит из одного абзаца, и
        # выбросить его значило бы потерять весь документ целиком.
        chunks.append(
            Chunk(
                document=document,
                text=piece,
                section=section,
                ordinal=len(chunks),
            )
        )
    return chunks


# Заголовок раздела в документе без нумерации: два и более слова прописными.
# У руководств Вихря и Unipump это единственная различимая граница разделов, а
# без неё такие документы резались по предложениям — и в выдачу попадали
# случайные куски вместо начала нужного раздела.
_CAPS_HEADING_RE = re.compile(
    r"(?=(?<![А-ЯЁA-Z])[А-ЯЁ]{3,}(?:\s+[А-ЯЁ]{2,}){1,5}\b)"
)


def _split_prose(text: str) -> list[tuple[str, str]]:
    """Порезать прозу: по нумерованным пунктам, по заголовкам, иначе по окнам."""

    parts = [part for part in _CLAUSE_RE.split(text) if part.strip()]
    if len(parts) < 3:
        by_heading = [
            part for part in _CAPS_HEADING_RE.split(text) if part.strip()
        ]
        if len(by_heading) >= 3:
            parts = by_heading
    if len(parts) < 3:
        return _windows(_collapse(text), _section_of(text))
    pieces: list[tuple[str, str]] = []
    for part in parts:
        collapsed = _collapse(part)
        pieces.extend(_windows(collapsed, _section_of(collapsed)))
    return pieces
