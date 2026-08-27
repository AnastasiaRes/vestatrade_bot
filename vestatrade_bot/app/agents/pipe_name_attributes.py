"""Извлечение параметров трубы из её названия.

У труб в фиде структурных атрибутов почти нет: у 204 из 496 позиций остаются
только артикул, полное наименование и штрихкод, а они отсеиваются как
идентификационные. В итоге ``characteristics`` карточки пуст, и весь код,
который работает с атрибутами — поиск, фильтрация, ранжирование и сравнение —
остаётся без входных данных. Само сравнение при этом написано правильно: оно
ищет ключ, по которому значения расходятся, и откатывается на цену только
когда таких ключей нет.

Спецификация трубы при этом написана прямо в названии: «PP-FIBER арм. стекл.,
PN 20, 25 MM», «РОСТерм неармированная PN 25 (SDR 6) белый 110ммх18,3мм 4м».
Этот модуль переносит её в атрибуты.

Разбор намеренно консервативный. Неизвлечённый параметр означает лишь, что
код продолжит работать как раньше; неверно извлечённый уйдёт покупателю как
подтверждённый факт и приведёт к покупке не той трубы. Поэтому всё
неоднозначное пропускается молча.
"""

from __future__ import annotations

import re

# Ключи повторяют написание тех, что уже приходят из фида: так извлечённый
# параметр и присланный поставщиком попадают в одну колонку сравнения, а не в
# две похожие.
DIAMETER_KEY = "диаметр (мм)"
WALL_KEY = "толщина стенки (мм)"
MATERIAL_KEY = "основной материал"
PRESSURE_CLASS_KEY = "класс давления pn"
SDR_KEY = "sdr"
REINFORCEMENT_KEY = "армирование"
OXYGEN_BARRIER_KEY = "кислородный барьер"
COIL_LENGTH_KEY = "длина бухты (м)"


# Слово «труба» встречается и у того, что трубой не является: у оснастки
# («Расширительные насадки для инструмента PEXcase (стабильная труба)»), у
# фитингов («Пресс-угольник ... раструб-труба 15х15») и у сборных узлов
# («Коллектор из стали (труба ДУ-40)»). Приписать им диаметр и класс давления
# трубы значит выдать покупателю чужой параметр как подтверждённый.
#
# Список закрытый и короткий намеренно: это перечень товарных существительных,
# а не попытка описать все возможные названия.
_NOT_A_PIPE = (
    "насадк",
    "инструмент",
    "коллектор",
    "ножниц",
    "резак",
    "труборез",
    "паяльник",
    "клипс",
    "держател",
    "крепление",
    "хомут",
    "угольник",
    "тройник",
    "крестовина",
    "муфта",
    "отвод",
    "переход",
    "заглушк",
    "ревизи",
    "компенсатор",
    "седелк",
    "штуцер",
    "ниппель",
    "сгон",
    "американка",
    "фитинг",
    "система",
    "коаксиальн",
    "евроконус",
    "калибр",
    "торцеватель",
)

# Головное слово не всегда первое: «STOUT 20х2,9 (бухта 100 метров) труба
# стабильная PE-Xa/Al/PE-RT» — обычная труба, просто название начинается с
# бренда и размера. Поэтому ищем слово в любом месте названия.
#
# Падеж здесь несёт смысл, а не грамматику: «труба» в именительном — это сам
# товар, «для м/п трубы» в родительном — принадлежность к трубе, то есть
# евроконус, калибр или торцеватель. Родительный не берём.
_PIPE_WORD = re.compile(r"\bтруба\b", re.IGNORECASE)

_PN = re.compile(r"\bp\s*n\s*[-\s]?(\d{1,3}(?:[.,]\d)?)\b", re.IGNORECASE)
_SDR = re.compile(r"\bsdr\s*[-\s]?(\d{1,2}(?:[.,]\d)?)\b", re.IGNORECASE)

# «16х2,0», «110ммх18,3мм», «63x8,6», «110*1500», «д.50*2000».
_DIAMETER_PAIR = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[.,]\d+)?)\s*(?:мм|mm)?\s*[хx×*]\s*"
    r"(\d{1,4}(?:[.,]\d+)?)\s*(?:мм|mm)?",
    re.IGNORECASE,
)
_DIAMETER_ALONE = re.compile(
    r"(?:^|[\s,(])(?:д\.?\s*)?(\d{1,3}(?:[.,]\d+)?)\s*(?:мм|mm)\b",
    re.IGNORECASE,
)
_COIL = re.compile(
    r"бухта\s*(\d{1,4})\s*(?:м|метр\w*)\b|\((?:бухта\s*)?(\d{1,4})\s*метр\w*\)",
    re.IGNORECASE,
)

# Порядок важен: «PP-ALUX» должен сработать раньше «PP», иначе труба потеряет
# марку и станет просто полипропиленовой.
_MATERIALS: tuple[tuple[str, str], ...] = (
    (r"pp[-\s]?alux", "PP-ALUX"),
    (r"pp[-\s]?fiber", "PP-FIBER"),
    (r"pp[-\s]?rc|pprc", "PPRC"),
    (r"pe[-\s]?xa|pex[-\s]?a", "PE-Xa"),
    (r"pe[-\s]?rt", "PE-RT"),
    (r"\bpex\b", "PEX"),
    (r"пэ\s?100|pe\s?100", "ПЭ100"),
    (r"\bhtem\b|\bht\b", "HTEM"),
    (r"\bkgem\b|\bkg\b", "KGEM"),
    (r"\bпвх\b|\bpvc\b", "ПВХ"),
    (r"металлопластик\w*|\bм/п\b", "металлопластик"),
    (r"нержавеющ\w*|нерж\.?", "нержавеющая сталь"),
    (r"\bppr\b|ппр|pp[-\s]?r\b", "PPR"),
    (r"полипропилен\w*", "полипропилен"),
    (r"\bpp\b", "PP"),
)

_REINFORCEMENTS: tuple[tuple[str, str], ...] = (
    (r"неармированн\w*", "нет"),
    (r"арм\w*\.?\s*алюмини\w*|альюмини\w*|\bstabi\b|alux", "алюминий"),
    (r"арм\w*\.?\s*стекл\w*|стекловолокн\w*|fiber", "стекловолокно"),
)


def _number(raw: str) -> str:
    """Привести число к единому виду: «18,3» и «18.3» — одно значение."""

    value = float(raw.replace(",", "."))
    return f"{value:g}"


def is_pipe_name(name: str) -> bool:
    lowered = name.lower()
    if any(marker in lowered for marker in _NOT_A_PIPE):
        return False
    return bool(_PIPE_WORD.search(lowered))


def extract_pipe_attributes(name: str) -> dict[str, str]:
    """Собрать параметры трубы из названия.

    Возвращает только уверенно разобранное. Пустой словарь — нормальный
    результат, а не ошибка.
    """

    if not name or not is_pipe_name(name):
        return {}

    lowered = name.lower()
    attributes: dict[str, str] = {}

    pn = _PN.search(lowered)
    if pn:
        attributes[PRESSURE_CLASS_KEY] = _number(pn.group(1))

    sdr = _SDR.search(lowered)
    if sdr:
        attributes[SDR_KEY] = _number(sdr.group(1))

    diameter, wall = _diameter_and_wall(lowered)
    if diameter is not None:
        attributes[DIAMETER_KEY] = diameter
    if wall is not None:
        attributes[WALL_KEY] = wall

    for pattern, label in _MATERIALS:
        if re.search(pattern, lowered, re.IGNORECASE):
            attributes[MATERIAL_KEY] = label
            break

    for pattern, label in _REINFORCEMENTS:
        if re.search(pattern, lowered, re.IGNORECASE):
            attributes[REINFORCEMENT_KEY] = label
            break

    if re.search(r"\bevoh\b|кислородн\w*", lowered, re.IGNORECASE):
        attributes[OXYGEN_BARRIER_KEY] = "есть"

    coil = _COIL.search(lowered)
    if coil:
        attributes[COIL_LENGTH_KEY] = coil.group(1) or coil.group(2)

    return attributes


def _diameter_and_wall(lowered: str) -> tuple[str | None, str | None]:
    """Разобрать пару чисел «A x B» из названия.

    Вторая цифра значит разное: у напорной трубы это толщина стенки
    («16х2,0»), у канализационной — длина отрезка («110*1500»). Перепутать их
    нельзя: стенка 1500 мм и длина 2 мм одинаково бессмысленны, но уйдут в
    карточку как факт. Разводим по диапазону, а всё, что между, не трогаем.
    """

    if '"' in lowered or "'" in lowered:
        # Дюймовые обозначения вида «1 1/4"*40/50» этой парой не разбираются.
        return None, None

    for match in _DIAMETER_PAIR.finditer(lowered):
        diameter = float(match.group(1).replace(",", "."))
        second = float(match.group(2).replace(",", "."))
        if not 4 <= diameter <= 500:
            continue
        if second <= 30 and second < diameter:
            return _number(match.group(1)), _number(match.group(2))
        if second >= 100:
            # Длина отрезка. Диаметр из такой пары берём, длину — нет: в
            # миллиметрах она не совпадает с «длиной бухты» в метрах, и
            # смешивать их в одном ключе нельзя.
            return _number(match.group(1)), None
        return None, None

    alone = _DIAMETER_ALONE.search(lowered)
    if alone:
        diameter = float(alone.group(1).replace(",", "."))
        if 4 <= diameter <= 500:
            return _number(alone.group(1)), None
    return None, None
