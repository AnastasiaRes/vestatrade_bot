"""Отраслевые нормы, на которые бот отвечает по существу.

Живой прогон показал устойчивый класс отказов: покупатель задаёт вопрос с
однозначным отраслевым ответом — «какой уклон у канализации 110?», «сталь 3/4,
брать ПП 20 или 25?», «труба рыжая или серая?» — и получает «точное значение
этого термина не подскажу без проверки» либо встречный вопрос про артикул.
Осторожность здесь применяется не по адресу: это не характеристика товара из
фида, которую нельзя выдумывать, а справочная величина, одинаковая для всего
рынка.

Почему таблицей, а не знаниями модели: числа — это то, по чему покупатель
поедет копать траншею. Свободно сгенерированный уклон звучит убедительно и не
проверяется, а таблицу можно прочитать глазами, и она даёт одинаковый ответ от
прогона к прогону. Тот же принцип уже применён в ``project_specification``.

Каждая норма несёт три части: значение, объяснение «почему» и оговорку о том,
что окончательно решает проект. Оговорка обязательна — норма задаёт ориентир,
а не заменяет расчёт.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .utils import normalize_text


@dataclass(frozen=True)
class NormAnswer:
    """Готовый ответ по отраслевой норме."""

    key: str
    text: str


@dataclass(frozen=True)
class Norm:
    """Одна проверяемая норма.

    ``matches`` намеренно требует вопросительной рамки: норма отвечает на
    вопрос, но не должна перехватывать просьбу подобрать товар.
    """

    key: str
    matches: Callable[[str], bool]
    build: Callable[[str, bool], str | None]


# --- уклон безнапорной канализации ------------------------------------------

# СП 30.13330 / СП 32.13330: нормативный уклон самотёчной канализации зависит
# от диаметра. Значения ниже — общепринятые ориентиры для бытовых систем.
SEWER_SLOPE_BY_DIAMETER: dict[int, tuple[float, float]] = {
    # диаметр мм: (нормативный уклон, минимально допустимый)
    50: (0.03, 0.025),
    110: (0.02, 0.008),
    160: (0.008, 0.007),
    200: (0.007, 0.005),
}


def _diameter_from_text(text: str) -> int | None:
    for match in re.finditer(r"(?<!\d)(\d{2,3})(?!\d)", text):
        value = int(match.group(1))
        if value in SEWER_SLOPE_BY_DIAMETER:
            return value
    return None


def _length_from_text(text: str) -> float | None:
    match = re.search(
        r"(?<!\d)(\d{1,4}(?:[,.]\d+)?)\s*(?:м\b|метр\w*)", text
    )
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 1 <= value <= 500 else None


def _sewer_slope_matches(text: str) -> bool:
    return "уклон" in text and any(
        marker in text for marker in ("канализац", "септик", "стоки", "самотечн", "самотёчн")
    )


def _sewer_slope_answer(text: str, follow_up: bool = False) -> str | None:
    diameter = _diameter_from_text(text) or 110
    normative, minimum = SEWER_SLOPE_BY_DIAMETER[diameter]
    def ru(value: float) -> str:
        return f"{value:g}".replace(".", ",")

    if follow_up:
        length = _length_from_text(text)
        stated_drop = re.search(
            r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:см|сантиметр\w*)",
            text,
        )
        asks_direct_confirmation = bool(
            any(marker in text for marker in ["можно ли", "да или нет", "без риска"])
            and length
            and stated_drop
        )
        if asks_direct_confirmation:
            actual_drop_cm = float(stated_drop.group(1).replace(",", "."))
            required_drop_cm = normative * length * 100
            matches = abs(actual_drop_cm - required_drop_cm) <= 0.5
            direct = "Да" if matches else "Нет"
            arithmetic = (
                f"{length:g} м × {ru(normative * 100)} см/м = "
                f"{ru(required_drop_cm)} см"
            )
            return (
                f"{direct}: по арифметике уклона {arithmetic}; заявленный перепад "
                f"{ru(actual_drop_cm)} см {'совпадает' if matches else 'не совпадает'} "
                "с этим значением. Это подтверждает именно геометрию уклона, но не "
                "гарантирует отсутствие засоров: нужен непрерывный уклон без провисов, "
                "правильная опора, доступ для прочистки и допустимая отметка входа в септик."
            )
        example = (
            f" Для участка {length:g} м расчётный перепад при 0,02 равен "
            f"{ru(normative * length * 100)} см."
            if length
            else ""
        )
        return (
            f"Сам арифметический расчёт сделать можно: перепад = длина трассы × "
            f"уклон. Для трубы {diameter} мм берите ориентир {ru(normative)} "
            f"({ru(normative * 100)} см на метр).{example} Сначала измеряют отметку "
            "низа выпуска из дома и отметку входа в септик; доступный перепад между "
            "ними должен покрыть расчётный перепад трубы, при этом по всей трассе "
            "сохраняются глубина, опора и защита от промерзания. Придумывать глубину "
            "септика нельзя — это фактический размер конкретного участка. Если отметок "
            "пока нет, правильный следующий шаг — нивелиром/лазерным уровнем измерить "
            "обе точки, а не назначать их по примеру из чужого проекта."
        )

    parts = [
        f"Для самотёчной канализации {diameter} мм нормативный уклон — "
        f"{ru(normative)} ({ru(normative * 100)} см на метр), минимально "
        f"допустимый — {ru(minimum)}."
    ]
    length = _length_from_text(text)
    if length:
        drop_cm = normative * length * 100
        parts.append(
            f"На участке {length:g} м это перепад примерно "
            f"{ru(drop_cm)} см между началом и концом."
        )
    parts.append(
        "Меньший уклон оставляет твёрдые включения в трубе, больший — "
        "разгоняет воду, и стоки уходят вперёд, а взвесь оседает."
    )
    parts.append(
        "Точную отметку врезки в септик и глубину заложения ниже промерзания "
        "определяет проект: от них зависит, выдержится ли уклон на всей длине."
    )
    return " ".join(parts)


# --- цвет трубы наружной и внутренней канализации ---------------------------


def _sewer_colour_matches(text: str) -> bool:
    mentions_colour = any(
        marker in text for marker in ("рыж", "оранжев", "сер")
    )
    asks = any(
        marker in text
        for marker in ("или", "какая", "какую", "чем отлич", "разница", "?")
    )
    return mentions_colour and asks and any(
        marker in text for marker in ("труб", "канализац")
    )


def _sewer_colour_answer(_: str, follow_up: bool = False) -> str:
    return (
        "Цвет здесь означает область применения, а не оттенок. "
        "Рыжая (оранжевая) труба — наружная: рассчитана на укладку в грунт и "
        "на нагрузку от засыпки, обозначается классом кольцевой жёсткости SN. "
        "Серая — внутренняя, для разводки по дому; в грунт её не кладут, она "
        "не держит нагрузку засыпки и хуже переносит перепады температуры. "
        "Для трассы от дома до септика нужна рыжая; класс жёсткости выбирают "
        "по глубине заложения и наличию проезда над трассой."
    )


# --- ревизии и колодцы -------------------------------------------------------


def _sewer_inspection_matches(text: str) -> bool:
    mentions = any(marker in text for marker in ("ревизи", "колодц", "прочистк"))
    asks = any(
        marker in text
        for marker in ("нужн", "надо", "какой", "какие", "что ставить", "?")
    )
    return mentions and asks


def _sewer_inspection_answer(_: str, follow_up: bool = False) -> str:
    return (
        "Да, точки доступа обязательны — без них трассу нечем прочищать. "
        "Ставят их на каждом повороте, при смене диаметра и уклона, а на "
        "прямых участках — с шагом по проекту. Поворот на 90° в грунте лучше "
        "не делать вовсе: собирают из двух отводов по 45° с ревизией, так "
        "трасса остаётся проходимой для троса. "
        "Конкретный шаг колодцев на прямом участке задаёт СП и проект: он "
        "зависит от диаметра и глубины заложения."
    )


# --- переход со стали на полипропилен ---------------------------------------

# PPR маркируется по НАРУЖНОМУ диаметру, стальная резьбовая труба — по
# условному проходу. Отсюда классическая ошибка «3/4 дюйма → ПП 20».
STEEL_TO_PPR: dict[str, tuple[int, float, int, float]] = {
    # дюймы: (условный проход мм, внутренний стали мм, минимальный PPR PN20, его внутренний)
    "1/2": (15, 15.7, 20, 13.2),
    "3/4": (20, 21.2, 25, 16.6),
    "1": (25, 27.1, 32, 21.2),
}

PPR_EXACT_MATCH: dict[str, int] = {"1/2": 25, "3/4": 32, "1": 40}


def _inch_from_text(text: str) -> str | None:
    for inch in ("3/4", "1/2", "1"):
        if inch in text:
            return inch
    return None


def _steel_to_ppr_matches(text: str) -> bool:
    mentions_steel = any(marker in text for marker in ("сталь", "стальн", "железн"))
    mentions_ppr = any(
        marker in text for marker in ("полипропилен", "ппр", "ppr", "пп ")
    )
    return mentions_steel and mentions_ppr and _inch_from_text(text) is not None


def _steel_to_ppr_answer(text: str, follow_up: bool = False) -> str | None:
    inch = _inch_from_text(text)
    if inch is None and follow_up:
        # «А почему? У 3/4 же диаметр 20 мм» — дюйм назван в прошлой реплике.
        inch = "3/4"
    if inch is None:
        return None
    dn, steel_bore, minimal, minimal_bore = STEEL_TO_PPR[inch]
    exact = PPR_EXACT_MATCH[inch]
    # Десятичные показываем по-русски: «21,2 мм», а не «21.2 мм».
    steel_text = f"{steel_bore:g}".replace(".", ",")
    minimal_text = f"{minimal_bore:g}".replace(".", ",")
    if follow_up:
        # Отвечаем на само заблуждение, а не повторяем прежний абзац.
        direct_choice = (
            "Да: если ваша цель именно сохранить проходное сечение стальной "
            f"трубы {inch}\", из названных 20/25/32 нужен PPR {exact}. "
            if any(marker in text for marker in ["единствен", "сохранить сечение"])
            else ""
        )
        return (
            direct_choice
            + f"Потому что {dn} мм у стальной {inch}\" — это условный проход DN{dn}, "
            f"внутренний размер. У полипропилена цифра в маркировке — наружный "
            f"диаметр, и внутри остаётся заметно меньше: у PPR 20 PN20 проход "
            f"всего 13,2 мм против {steel_text} мм у стальной. "
            f"Одинаковое число «20» в двух системах маркировки означает разные "
            f"вещи — отсюда и путаница. Чтобы не потерять сечение, нужен "
            f"PPR {minimal} (проход {minimal_text} мм) или PPR {exact}, у "
            f"которого проход совпадает со стальной трубой."
        )
    return (
        f"Берите {minimal}, не меньше. Причина в разной системе маркировки: "
        f"стальная труба {inch}\" — это условный проход DN{dn}, внутренний "
        f"диаметр около {steel_text} мм, а полипропилен маркируется по "
        f"НАРУЖНОМУ диаметру. У PPR {minimal} PN20 внутренний проход "
        f"примерно {minimal_text} мм, у PPR 20 — всего 13,2 мм: это вдвое "
        f"меньше сечения, и на длинной ветке вы получите заметную потерю напора. "
        f"Если хотите сохранить сечение точно, ставьте PPR {exact} — его "
        f"внутренний диаметр совпадает со стальной {inch}\". "
        f"Выбор между {minimal} и {exact} зависит от длины ветки и числа точек "
        f"разбора: на коротком подводе достаточно {minimal}."
    )


NORMS: tuple[Norm, ...] = (
    Norm("sewer_slope", _sewer_slope_matches, _sewer_slope_answer),
    Norm("steel_to_ppr", _steel_to_ppr_matches, _steel_to_ppr_answer),
    Norm("sewer_colour", _sewer_colour_matches, _sewer_colour_answer),
    Norm("sewer_inspection", _sewer_inspection_matches, _sewer_inspection_answer),
)


# Просьба подобрать/купить — это не вопрос о норме. Без этой проверки
# «нужна труба канализационная 110 мм, 5 метров» получало бы лекцию об уклоне.
_PRODUCT_REQUEST_RE = re.compile(
    r"\b(?:нужн\w*|нужен|куп\w*|закаж\w*|подбер\w*|подобра\w*|"
    r"посчита\w*|привез\w*|достав\w*|в\s+наличии|скольк\w*\s+стоит)\b"
)


_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:а\s+)?(?:как\w*|что|чем|почему|зачем|нужн\w*|надо|сколько|где|куда)\b"
)

# Сомнение и просьба обосновать — один класс реплик, а не список формулировок.
# «Почему?», «ты уверен?», «с чего вы взяли?», «обоснуй», «не может быть» —
# всё это продолжение той же темы: покупатель не принял ответ и просит
# объяснение. Ловить их поимённо бессмысленно, поэтому здесь описан класс.
_CHALLENGE_MARKERS = (
    "почему",
    "зачем",
    "отчего",
    "с чего",
    "как так",
    "уверен",
    "уверены",
    "точно",
    "правда",
    "серьезно",
    "действительно",
    "обоснуй",
    "обоснуйте",
    "докажи",
    "докажите",
    "объясни",
    "поясни",
    "не может быть",
    "странно",
    "сомнева",
    "не верю",
    "разве",
)

# Слова, которые вводят новую тему: тогда это не уточнение прежнего ответа.
_NEW_SUBJECT_MARKERS = (
    "котел",
    "котёл",
    "насос",
    "радиатор",
    "водонагрев",
    "бойлер",
    "смесител",
    "фильтр",
    "счетчик",
    "счётчик",
    "заказ",
    "достав",
    "оплат",
    "цена",
    "стоит",
    "гаранти",
)


def _is_challenge_follow_up(text: str) -> bool:
    """Короткая реплика-сомнение без новой темы."""

    if not text:
        return False
    # Длинная реплика почти всегда вводит новые факты, а не оспаривает ответ.
    if len(text.split()) > 8:
        return False
    if any(marker in text for marker in _NEW_SUBJECT_MARKERS):
        return False
    return any(marker in text for marker in _CHALLENGE_MARKERS)


def _is_topical_follow_up(key: str, text: str) -> bool:
    """Continue a norm when the next question adds facts in the same domain."""

    if any(marker in text for marker in _NEW_SUBJECT_MARKERS):
        return False
    if key == "sewer_slope":
        asks_same_calculation = bool(
            _INTERROGATIVE_RE.match(text)
            or any(
                marker in text
                for marker in [
                    "какой",
                    "как ",
                    "если",
                    "получится",
                    "нужно",
                    "нужен",
                ]
            )
        )
        return bool(
            any(
                marker in text
                for marker in [
                    "уклон",
                    "септик",
                    "глубин",
                    "перепад",
                    "проект",
                    "в земле",
                    "посчитать",
                    "рассчитать",
                ]
            )
            and asks_same_calculation
        )
    if key == "steel_to_ppr":
        return bool(
            any(marker in text for marker in ["ppr", "ппр", "полипроп", "сечен", "проход"])
            and any(
                marker in text
                for marker in [
                    "20",
                    "25",
                    "32",
                    "3/4",
                    "какой",
                    "почему",
                    "единствен",
                ]
            )
        )
    return False


def match_engineering_norm(
    message: str,
    *,
    previous_norm: str | None = None,
    previous_message: str | None = None,
) -> NormAnswer | None:
    """Найти отраслевую норму, которая прямо отвечает на вопрос покупателя.

    ``previous_norm`` позволяет ответить на короткий уточняющий «а почему?»:
    без него такая реплика теряет тему и уходит в поиск товара.
    """

    text = normalize_text(message)
    if not text:
        return None

    # Сомнение в прошлом ответе разбирается первым: у него своя рамка, и
    # вопросительного знака в нём может не быть вовсе («не может быть»).
    if previous_norm and (
        _is_challenge_follow_up(text) or _is_topical_follow_up(previous_norm, text)
    ):
        for norm in NORMS:
            if norm.key != previous_norm:
                continue
            context_text = " ".join(
                part for part in [normalize_text(previous_message or ""), text] if part
            )
            answer = norm.build(context_text or text, True)
            if answer:
                return NormAnswer(key=norm.key, text=answer)

    explicit_question = "?" in message or bool(_INTERROGATIVE_RE.match(text))
    asks_question = explicit_question or any(
        marker in text
        for marker in ("какой", "какая", "какую", "какие", "нужн", "надо", "чем отлич", "разница")
    )
    if not asks_question:
        return None
    # Просьба купить/подобрать — не вопрос о норме. Но явный вопрос («ревизии
    # нужны?») вето не подлежит: там «нужн» — часть вопроса, а не заказа.
    if not explicit_question and _PRODUCT_REQUEST_RE.search(text):
        return None

    for norm in NORMS:
        if not norm.matches(text):
            continue
        answer = norm.build(text, False)
        if answer:
            return NormAnswer(key=norm.key, text=answer)

    return None
