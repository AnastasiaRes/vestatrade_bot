"""Реплика-список позиций: «угольник 20 — 30 шт, муфта 40-25 — 5 шт».

Зачем. Монтажник и снабженец пишут закупку одним сообщением, а не по позиции
за ход. В живом прогоне 25.08 такой список схлопывался в один запрос: слоты
всех позиций складывались в одну корзину («диаметр 40 и 25 и резьба 1/2»),
ни один товар им не удовлетворял, и бот отвечал «Не нашёл подходящие товары»
на список, каждая позиция которого лежала в каталоге с большим остатком
(A16, A08, D10).

Здесь список разбирается на отдельные позиции до подбора: каждая ищется своим
запросом, и ответ строится по позициям — что есть, чего нет, сколько стоит.
Это же требование PASS-критерия тест-набора: «Ничего из списка не теряет».

Разбор намеренно консервативен. Позицией считается фрагмент с количеством в
штуках, метрах или комплектах — то есть строка закупки, а не любое
перечисление. «Комнаты 18 и 14 м²» списком позиций не становится: квадратные
метры в единицы количества не входят.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Единицы закупки. «м2», «м²» и «кв. м» сюда намеренно не входят: это площадь
# помещения, а не количество товара.
_QUANTITY_RE = re.compile(
    r"(?<!\d)(\d{1,5})\s*"
    r"(шт\.?|штук\w*|компл\w*|бухт\w*|упак\w*|"
    r"пог\.?\s*м\b|метр\w*|м\b)"
    r"(?!\s*(?:2|²|кв))",
    re.IGNORECASE,
)

# Преамбула до двоеточия — «Товар:», «Нужны фитинги Valtec:», «Список:».
_PREAMBLE_RE = re.compile(r"^[^:]{0,120}:\s*", re.DOTALL)

# Разделители позиций внутри списка.
_SPLIT_RE = re.compile(r"\s*(?:[,;\n]|\bи\b(?=\s*[а-яёa-z]))\s*", re.IGNORECASE)

# Хвост количества отрезается от текста запроса: «— 30 шт» в поиске мешает.
_QUANTITY_TAIL_RE = re.compile(
    r"\s*[-–—]?\s*(?<!\d)\d{1,5}\s*"
    r"(?:шт\.?|штук\w*|компл\w*|бухт\w*|упак\w*|пог\.?\s*м\b|метр\w*|м\b)"
    r"(?!\s*(?:2|²|кв))\.?\s*$",
    re.IGNORECASE,
)

_UNIT_CANON = (
    ("шт", ("шт", "штук")),
    ("компл", ("компл",)),
    ("бухт", ("бухт",)),
    ("упак", ("упак",)),
    ("м", ("пог", "метр", "м")),
)

# Слишком короткий фрагмент позицией не бывает: «и 5 шт» товар не называет.
_MIN_WORDS = 2

# A Legacy item-list turn must contain a product noun in every counted row.
# This is deliberately narrower than the general catalogue vocabulary: the
# parser is an execution boundary, not a discovery classifier.  Engineering
# answers such as ``уровень 12 м, трасса 35 м`` must not become purchase rows.
_PRODUCT_ANCHOR_RE = re.compile(
    r"\b(?:труб\w*|угольник\w*|муфт\w*|фитинг\w*|кран\w*|"
    r"клапан\w*|насос\w*|кот[её]л\w*|радиатор\w*|термоголовк\w*|"
    r"тройник\w*|переходник\w*|ниппел\w*|футорк\w*|штуцер\w*|"
    r"коллектор\w*|заглушк\w*|ревизи\w*|манжет\w*)\b",
    re.IGNORECASE,
)
_PURCHASE_PREFIX_RE = re.compile(
    r"\b(?:нуж\w*|куп\w*|заказ\w*|возьм\w*|взять|позиц\w*|"
    r"количеств\w*|требу\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RequestedItem:
    """Одна строка закупки."""

    query: str
    quantity: int | None
    unit: str
    raw: str


def _canon_unit(raw_unit: str) -> str:
    lowered = raw_unit.lower().strip(". ")
    for canon, prefixes in _UNIT_CANON:
        if any(lowered.startswith(prefix) for prefix in prefixes):
            return canon
    return ""


def _strip_leading_noise(fragment: str) -> str:
    return fragment.strip(" \t-–—•*").lstrip("0123456789) .")


def split_item_list(message: str, *, min_items: int = 2) -> list[RequestedItem]:
    """Разобрать реплику на позиции закупки; пустой список — это не список."""

    text = str(message or "").strip()
    if not text:
        return []

    body = text
    preamble = _PREAMBLE_RE.match(text)
    if preamble and len(_QUANTITY_RE.findall(text[preamble.end() :])) >= min_items:
        body = text[preamble.end() :]

    items: list[RequestedItem] = []
    for fragment in _SPLIT_RE.split(body):
        fragment = _strip_leading_noise(fragment)
        if not fragment:
            continue
        quantity_match = _QUANTITY_RE.search(fragment)
        if not quantity_match:
            continue
        # Количество вырезается вместе со всем, что идёт после него: хвост
        # «— 5 шт. Всё есть?» в поисковый запрос попадать не должен.
        head = fragment[: quantity_match.start()].strip(" \t-–—.,:")
        tail = fragment[quantity_match.end() :].strip(" \t-–—.,:")
        head_has_product = _PRODUCT_ANCHOR_RE.search(head) is not None
        tail_has_product = _PRODUCT_ANCHOR_RE.search(tail) is not None
        if head_has_product:
            query = head
        elif tail_has_product and _PURCHASE_PREFIX_RE.search(head):
            # ``Нужно 30 м трубы ПНД`` is a valid row.  A measurement such as
            # ``по участку 35 м трубы`` is not: its prefix has no purchase act.
            query = tail
        else:
            continue
        query = _QUANTITY_TAIL_RE.sub("", query).strip(" \t-–—.")
        if len(query.split()) < _MIN_WORDS:
            continue
        # Во фрагменте должно остаться название товара, а не только размер.
        if not _PRODUCT_ANCHOR_RE.search(query):
            continue
        items.append(
            RequestedItem(
                query=query,
                quantity=int(quantity_match.group(1)),
                unit=_canon_unit(quantity_match.group(2)),
                raw=fragment,
            )
        )

    if len(items) < min_items:
        return []
    return items


# Нумерованный список вопросов: «1) есть? 2) сколько стоит? 3) доставите?».
_NUMBERED_QUESTION_RE = re.compile(r"(?:^|\s)(\d{1,2})\s*[).]\s+", re.MULTILINE)


def split_question_list(message: str, *, min_items: int = 3) -> list[str]:
    """Разбить пронумерованный список вопросов на отдельные вопросы.

    Живой прогон 25.08 (D04, P0): на пять пронумерованных вопросов бот отвечал
    на два — про доставку и скидку, — а наличие, цену и оплату терял. Здесь
    вопросы разделяются до маршрутизации, чтобы каждый получил свой ответ.

    Разбор узкий: нужен именно нумерованный список из трёх и более пунктов.
    Просто несколько вопросительных знаков в реплике списком не считаются —
    иначе обычный разговорный ход распадался бы на куски.
    """

    text = str(message or "").strip()
    if not text:
        return []
    marks = list(_NUMBERED_QUESTION_RE.finditer(text))
    if len(marks) < min_items:
        return []
    # Нумерация должна идти подряд с единицы: «1) … 2) … 3) …». Иначе это
    # размеры («20) 30)») или цитата, а не список вопросов.
    numbers = [int(match.group(1)) for match in marks]
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return []

    parts: list[str] = []
    for index, match in enumerate(marks):
        start = match.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        part = text[start:end].strip(" \t\n-–—;,")
        if len(part.split()) >= 2:
            parts.append(part)
    return parts if len(parts) >= min_items else []
