"""Монтажные названия узлов → семейства товаров каталога.

Покупатели-профессионалы называют узел так, как он называется на объекте:
«американка», «сгон», «футорка», «контргайка», «евроконус». В фиде эти же
слова стоят в «Тип товара», то есть товар есть и находится, но маршрутизация
уводила запрос в чужую категорию: «американка» была ключевым словом кранов,
а «сгон» без слова «ppr» попадал в трубы. В результате бот задавал
бессмысленное уточнение («для чего нужен кран?») по товару, которого в этой
категории нет.

Словарь намеренно отделён от ``CATEGORY_KEYWORDS``: там ключевые слова
участвуют во взвешенном подсчёте категории, а здесь нужно однозначное
соответствие «название узла → семейство», проверенное по фактическому
каталогу. Каждое значение ``element`` совпадает с «Тип товара» в выгрузке,
поэтому его можно класть в слот ``element_type`` как фильтр подбора.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TradeTerm:
    """Одно монтажное название и семейство товаров за ним."""

    element: str
    category: str
    pattern: str
    # Резьбовые латунные узлы существуют вне деления «PPR / канализация»:
    # спрашивать у покупателя систему для американки или контргайки
    # бессмысленно, для подбора достаточно семейства и размера.
    system_agnostic: bool = False


# Порядок важен: более длинные и специфичные названия проверяются первыми,
# чтобы «ниппель переходной» не стал «переходником».
TRADE_TERMS: tuple[TradeTerm, ...] = (
    TradeTerm("американка", "fittings", r"американк\w*", system_agnostic=True),
    TradeTerm("водорозетка", "fittings", r"водорозетк\w*|настенн\w+\s+розетк\w*"),
    TradeTerm("контргайка", "fittings", r"контргайк\w*", system_agnostic=True),
    TradeTerm("евроконус", "fittings", r"евроконус\w*", system_agnostic=True),
    TradeTerm("коллектор", "fittings", r"коллектор\w*|гребенк\w*", system_agnostic=True),
    TradeTerm("крестовина", "fittings", r"крестовин\w*"),
    TradeTerm("удлинитель", "fittings", r"удлинител\w*", system_agnostic=True),
    TradeTerm("соединитель", "fittings", r"соединител\w*"),
    TradeTerm("заглушка", "fittings", r"заглушк\w*|заглушен\w*"),
    TradeTerm("футорка", "fittings", r"футорк\w*", system_agnostic=True),
    TradeTerm("ниппель", "fittings", r"ниппел\w*", system_agnostic=True),
    TradeTerm("штуцер", "fittings", r"штуцер\w*", system_agnostic=True),
    TradeTerm("бочонок", "fittings", r"бочонок|бочонк\w*", system_agnostic=True),
    TradeTerm("манжета", "fittings", r"манжет\w*"),
    TradeTerm("ревизия", "fittings", r"ревизи[ияюей]\w*"),
    # «Полусгон» — исполнение крана, а не отдельный узел: он отсекается
    # ретроспективной проверкой, иначе «кран с полусгоном» уедет в фитинги.
    TradeTerm(
        "сгон", "fittings", r"(?<!полу)\bсгон\w*", system_agnostic=True
    ),
)


# Если в реплике есть головное существительное другого семейства, монтажное
# название почти всегда описывает его исполнение: «кран с американкой»,
# «кран с полусгоном», «фильтр со штуцером». Тогда категорию менять нельзя.
COMPETING_HEAD_NOUNS_RE = re.compile(
    r"\b(?:кран\w*|вентил\w*|клапан\w*|котел\w*|котл\w*|насос\w*|"
    r"радиатор\w*|батаре\w*|водонагрев\w*|бойлер\w*|смесител\w*|"
    r"фильтр\w*|счетчик\w*|полотенцесушител\w*)\b"
)


def match_trade_term(text: str) -> TradeTerm | None:
    """Монтажное название узла, если реплика говорит именно о нём.

    ``text`` ожидается уже нормализованным (``normalize_text``).
    """

    if not text:
        return None
    if COMPETING_HEAD_NOUNS_RE.search(text):
        return None
    for term in TRADE_TERMS:
        match = re.search(term.pattern, text)
        if match:
            # «с американкой», «со сгоном» — творительный падеж описывает
            # исполнение уже обсуждаемого товара («кран … с американкой»),
            # а не новый узел. Такая реплика не меняет семейство.
            if re.search(rf"\bсо?\s+{term.pattern}", text):
                return None
            # Тот же узел в канализации — отдельное семейство каталога:
            # «крестовина 110 канализационная» не должна уходить в фитинги.
            if "канализац" in text and term.category == "fittings":
                return TradeTerm(term.element, "sewer", term.pattern)
            return term
    return None


SYSTEM_AGNOSTIC_ELEMENTS = frozenset(
    term.element for term in TRADE_TERMS if term.system_agnostic
)


def is_system_agnostic_element(element: object) -> bool:
    """Нужно ли вообще спрашивать «PPR или канализация» для этого узла."""
    return str(element or "").strip().lower() in SYSTEM_AGNOSTIC_ELEMENTS


# Переходные узлы по своей природе имеют разные размеры на концах: требовать
# у футорки одинаковый размер с обеих сторон — значит не найти ничего.
# «Футорка 1/2» означает «1/2 на одной из сторон».
REDUCER_ELEMENTS = frozenset({"футорка", "переходник", "штуцер"})


def is_reducer_element(element: object) -> bool:
    return str(element or "").strip().lower() in REDUCER_ELEMENTS
