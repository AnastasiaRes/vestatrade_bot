"""Map an everyday problem description to a product-domain conversation frame.

Product keywords answer "what item was named?".  They cannot answer the earlier
question "what system could explain this symptom?".  Keeping that inference in
one small layer prevents each catalogue funnel from growing its own collection
of one-off phrases, while the orchestrator still owns the safe dialogue policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .utils import normalize_text


@dataclass(frozen=True)
class ProblemFrame:
    code: str
    category: str
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)


_ODOR_RE = re.compile(r"\b(?:пахн\w*|запах\w*|вон\w*|тянет|нес[её]т\w*)\b")
_SEWER_CONTEXT_RE = re.compile(
    r"\b(?:канализац\w*|слив\w*|сифон\w*|трап\w*|ванн\w*|душ\w*)\b"
)
_WATER_QUALITY_RE = re.compile(
    r"\b(?:нал[её]т\w*|накип\w*|мутн\w*|ржав\w*|желт\w*|"
    r"бел[её]с\w*|желез\w*|металл\w*|привкус\w*|вкус\w*|осад\w*)\b"
)
_SHORTAGE_RE = re.compile(
    r"\b(?:заканчива\w*|конча\w*|не\s+хват\w*|мало|быстро\s+остыва\w*|"
    r"становит\w*\s+холодн\w*)\b"
)
_FAILURE_RE = re.compile(
    r"\b(?:сломал\w*|перестал\w*\s+работ\w*|не\s+работа\w*|"
    r"умер\w*|накрыл\w*|приказал\w*\s+долго\w*\s+жить|похоже\s*,?\s*вс[её])\b"
)
_STANDING_WATER_PLACE_RE = re.compile(
    r"\b(?:подвал\w*|погреб\w*|приям\w*|техподполь\w*|цоколь\w*)\b"
)
_STANDING_WATER_RE = re.compile(
    r"\b(?:вод\w*|затаплива\w*|подтаплива\w*|вычерпыва\w*|"
    r"в[её]др\w*|откач\w*|болот\w*)\b"
)
_FLOOR_RE = re.compile(r"\bпол(?:а|у|ом|е|ы)?\b")
_FLOOR_COMFORT_RE = re.compile(
    r"\b(?:холодн\w*|ледян\w*|не\s+ледян\w*|т[её]пл\w*|согрева\w*)\b"
)
_WEAK_FLOW_RE = re.compile(
    r"\b(?:слаб\w*|еле(?:-еле)?|тонк\w*|вял\w*|грустн\w*|едва|почти\s+не)\b"
)
_WATER_OUTLET_RE = re.compile(
    r"\b(?:стру[яи]\w*|напор\w*|кран\w*|душ\w*|вод\w*)\b"
)
_LEVEL_CONTRAST_RE = re.compile(
    r"\b(?:этаж\w*|мансард\w*|наверх\w*|вверх\w*|внизу|наверху)\b"
)
_HEATING_EMITTER_RE = re.compile(
    r"\b(?:батаре\w*|радиатор\w*|отоплен\w*|дом\w*)\b"
)
_NO_HEAT_RE = re.compile(
    r"\b(?:холодн\w*|ледян\w*|остыл\w*|не\s+гре\w*|молчит\w*)\b"
)
_HEAT_EQUIPMENT_CONTEXT_RE = re.compile(
    r"\b(?:котельн\w*|кот(?:[её]л|л\w*)|аппарат\w*|оборудован\w*|"
    r"коробк\w*|теплогенератор\w*)\b"
)
_HOT_WATER_CONTEXT_RE = re.compile(
    r"\b(?:душ\w*|ванн\w*|кухн\w*|кран\w*|горяч\w*)\b"
)
_HOT_WATER_LOSS_RE = re.compile(
    r"\b(?:холодн\w*|остыва\w*|тепл\w*)\b[^.?!]{0,24}\b(?:теч\w*|ид[её]т)\b"
    r"|\b(?:теч\w*|ид[её]т)\b[^.?!]{0,24}\b(?:холодн\w*|тепл\w*)\b"
)
_FLOOR_LIFESTYLE_RE = re.compile(r"\b(?:босик\w*|ног\w*)\b")
_FLOOR_PROJECT_CONTEXT_RE = re.compile(
    r"\b(?:ремонт\w*|покрыти\w*|стяжк\w*|квартир\w*|дом\w*)\b"
)


def frame_customer_problem(message: str) -> ProblemFrame | None:
    """Return a stable domain hypothesis only when concepts support it.

    Rules intentionally combine a symptom with an object/location.  A lone
    word such as ``запах`` or ``холодно`` is too ambiguous to start an
    engineering funnel.
    """

    text = normalize_text(message)
    if not text:
        return None

    # ``не знаю, холодная или горячая, вода течёт из крана`` is an attempt to
    # classify a service pipe, not evidence that hot water is disappearing.
    # The hot-water-loss pattern sees the same words, so keep an explicit
    # either/or temperature question out of the diagnostic frame unless a real
    # shortage symptom is also present.
    temperature_classification = bool(
        re.search(
            r"\b(?:холодн\w*\s+или\s+горяч\w*|"
            r"горяч\w*\s+или\s+холодн\w*)\b",
            text,
        )
        and not _SHORTAGE_RE.search(text)
    )

    # Odour from a bathroom/drain is a diagnostic sewer problem, not a broad
    # bathroom renovation and not evidence that a particular pipe is needed.
    if _ODOR_RE.search(text) and _SEWER_CONTEXT_RE.search(text):
        return ProblemFrame("sewer_odor", "sewer", 0.96)

    # Location wins over appearance: ``мутная вода с песком в погребе после
    # ливня`` is water to pump out, not drinking water to filter.  Keep this
    # before the generic quality symptoms so natural mentions of water cannot
    # flip the task to filters.
    if _STANDING_WATER_PLACE_RE.search(text) and _STANDING_WATER_RE.search(text):
        return ProblemFrame(
            "standing_water",
            "pumps",
            0.96,
            {"pump_use": "откачка воды", "pump_type": "дренажный"},
        )

    # Water symptoms do not prove a filter technology.  The frame deliberately
    # records only the task; analysis/source questions come before retrieval.
    if "вод" in text and _WATER_QUALITY_RE.search(text):
        return ProblemFrame("water_quality", "filters", 0.94)
    if "вод" in text and _ODOR_RE.search(text) and "канализац" not in text:
        return ProblemFrame("water_quality", "filters", 0.88)

    # Weak flow plus an upper/lower level contrast identifies a pressure task,
    # not yet its cause.  The dialogue must still distinguish the source,
    # system-wide pressure and a clogged local outlet before any pump search.
    if (
        _WEAK_FLOW_RE.search(text)
        and _WATER_OUTLET_RE.search(text)
        and _LEVEL_CONTRAST_RE.search(text)
    ):
        return ProblemFrame(
            "weak_pressure",
            "pumps",
            0.95,
            {"pump_use": "повышение давления"},
        )

    # A hot-water shortage identifies the appliance domain but not storage vs
    # instantaneous geometry or a volume.
    if (
        ("горяч" in text and "вод" in text)
        or any(marker in text for marker in ("душ", "ванн"))
    ) and _SHORTAGE_RE.search(text):
        return ProblemFrame("hot_water_shortage", "water_heaters", 0.94)
    if (
        _HOT_WATER_CONTEXT_RE.search(text)
        and _HOT_WATER_LOSS_RE.search(text)
        and any(marker in text for marker in ("душ", "кухн", "кран"))
        and not temperature_classification
    ):
        return ProblemFrame("hot_water_shortage", "water_heaters", 0.93)

    if (
        re.search(r"\bкот(?:[её]л|л\w*)\b", text)
        and _FAILURE_RE.search(text)
    ):
        return ProblemFrame("boiler_failure", "boilers", 0.97)
    if (
        _HEATING_EMITTER_RE.search(text)
        and _NO_HEAT_RE.search(text)
        and _HEAT_EQUIPMENT_CONTEXT_RE.search(text)
    ):
        return ProblemFrame("boiler_failure", "boilers", 0.96)

    # The wish for a comfortable floor is intentionally framed before a
    # hydronic/electric choice.  Mentioning a water floor in a question does
    # not constitute consent to build a hydronic basket.
    names_floor_component = bool(
        re.search(
            r"\b(?:ppr|ппр|pex|pe-?rt|труб\w*|мат\w*|кабел\w*|"
            r"насос\w*|коллектор\w*|термостат\w*|кот(?:[её]л|л\w*)|"
            r"бойлер\w*|обвяз\w*)\b",
            text,
        )
    )
    explicitly_chose_floor_type = bool(
        re.search(
            r"\b(?:хочу|нужен|нужны|надо|делаем|сделать|собра\w*|будет|выбра\w*)\b"
            r"[^.?!]{0,35}\b(?:водян\w*|электр\w*)\b[^.?!]{0,20}\bпол\w*",
            text,
        )
    ) and not warm_floor_type_is_uncertain(message)
    rejects_warm_floor = bool(
        re.search(
            r"\b(?:без|не\s+нуж\w*|не\s+будет|исключ\w*)\b"
            r"[^.?!,;]{0,24}\bт[её]пл\w*[^.?!,;]{0,12}\bпол\w*",
            text,
        )
    )
    if (
        (
            (_FLOOR_RE.search(text) and _FLOOR_COMFORT_RE.search(text))
            or (
                _FLOOR_LIFESTYLE_RE.search(text)
                and _FLOOR_PROJECT_CONTEXT_RE.search(text)
            )
        )
        and not names_floor_component
        and not explicitly_chose_floor_type
        and not rejects_warm_floor
    ):
        return ProblemFrame(
            "floor_comfort",
            "pipes",
            0.9,
            {"project_scope": "warm_floor", "has_warm_floor": True},
        )

    if (
        any(marker in text for marker in ("под мойк", "под раковин", "под кухн"))
        and any(marker in text for marker in ("капает", "течет", "течёт", "сыр", "мокр"))
        and any(marker in text for marker in ("кран", "ручк", "перекры", "на трубе"))
    ):
        return ProblemFrame(
            "undersink_shutoff_leak",
            "valves",
            0.95,
            {"application": "вода"},
        )

    return None


def warm_floor_type_is_uncertain(message: str) -> bool:
    """Return true when a floor type is mentioned as a question, not a choice."""

    text = normalize_text(message)
    if "водян" not in text and "электр" not in text:
        return False
    if re.search(
        r"\b(?:можно\s+ли|подойд[её]т\s+ли|допустим\w*\s+ли|"
        r"разреш[её]н\w*\s+ли)\b",
        text,
    ):
        return True
    if re.search(
        r"\b(?:не\s+знаю|не\s+уверен\w*|не\s+понимаю|"
        r"не\s+выбра\w*)\b[^.?!]{0,60}\b(?:водян\w*|электр\w*)\b",
        text,
    ):
        return True
    return bool(
        "?" in message
        and any(marker in text for marker in ("или", "подойд", "можно", "какой"))
    )


def continues_problem_frame(code: str, message: str) -> bool:
    """Recognise a follow-up that omits the noun already present in context."""

    text = normalize_text(message)
    markers: dict[str, tuple[str, ...]] = {
        "water_quality": (
            "фильтр",
            "картридж",
            "осад",
            "налет",
            "налёт",
            "накип",
            "привкус",
            "очист",
            "анализ",
            "лаборатор",
            "проб",
            "водоканал",
            "протокол",
        ),
        "hot_water_shortage": (
            "горяч",
            "душ",
            "нагрев",
            "литр",
            "бойлер",
            "бак",
            "мощност",
            "модель",
            "табличк",
            "подвал",
            "поток",
        ),
        "sewer_odor": ("запах", "пах", "слив", "сифон", "канализац"),
        "floor_comfort": (
            "пол",
            "водян",
            "электр",
            "мат",
            "кабель",
            "квартир",
            "центральн",
            "автоном",
            "площад",
            "электрик",
            "мощност",
            "гидравл",
            "подключ",
            "измер",
            "провер",
            "управляющ",
            "тсж",
            "разреш",
            "согласов",
            "документ",
            "служб",
            "проектиров",
            "письменн",
            "техпаспорт",
            "техническ паспорт",
            "план дома",
            "планах дома",
            "схем дома",
        ),
        "standing_water": (
            "подвал",
            "погреб",
            "мутн",
            "частиц",
            "откач",
            "насос",
            "пес",
            "мусор",
            "шланг",
            "подъем",
            "подъём",
            "монтаж",
            "производ",
            "мощност",
            "глубин",
            "зон",
            "выкачать",
            "рассчит",
            "кубометр",
            "куб",
            "м3/ч",
            "м³/ч",
            "класс гряз",
            "рабочей точк",
            "q-h",
            "q–h",
            "крив",
            # Once the observable conditions have been collected, a short
            # catalogue action is still part of this frame.  The product noun
            # is commonly omitted in natural dialogue: «покажи, что
            # подойдёт» must not reset a drainage session to ``other``.
            "покаж",
            "подой",
            "подбер",
            "вариант",
            "из каталог",
        ),
        "undersink_shutoff_leak": (
            "кран",
            "размер",
            "маркиров",
            "резьб",
            "цифр",
            "ручк",
            "под мойк",
            "под раковин",
            "сфотограф",
            "снимать",
            "снимок",
            "фото",
            "соединен",
            "подключен",
            "измер",
            "мокр",
            "сыр",
        ),
        "boiler_failure": (
            "котел",
            "котёл",
            "отоплен",
            "газ",
            "площад",
            "почему",
            "провер",
            "молчит",
            "запуск",
            "слома",
            "ремонт",
        ),
        "weak_pressure": (
            "напор",
            "струя",
            "давлен",
            "мансард",
            "этаж",
            "скважин",
            "колод",
            "водопровод",
        ),
    }
    return any(marker in text for marker in markers.get(code, ()))


_FRAME_DEFAULTS: dict[str, tuple[str, float, dict[str, Any]]] = {
    "water_quality": ("filters", 0.94, {}),
    "hot_water_shortage": ("water_heaters", 0.94, {}),
    "sewer_odor": ("sewer", 0.96, {}),
    "floor_comfort": (
        "pipes",
        0.9,
        {"project_scope": "warm_floor", "has_warm_floor": True},
    ),
    "standing_water": (
        "pumps",
        0.96,
        {"pump_use": "откачка воды", "pump_type": "дренажный"},
    ),
    "undersink_shutoff_leak": ("valves", 0.95, {"application": "вода"}),
    "boiler_failure": ("boilers", 0.97, {}),
    "weak_pressure": ("pumps", 0.95, {"pump_use": "повышение давления"}),
}


def resume_problem_frame(code: str, message: str) -> ProblemFrame | None:
    """Restore a frame when the follow-up uses only contextual shorthand."""

    configured = _FRAME_DEFAULTS.get(code)
    if configured is None or not continues_problem_frame(code, message):
        return None
    category, confidence, slots = configured
    return ProblemFrame(code, category, confidence, dict(slots))
