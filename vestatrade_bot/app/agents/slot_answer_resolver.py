"""Bind a short customer answer to the slot the bot actually asked about.

Historically this binding was done by matching the *wording* of the bot's own
question against a hand-written keyword table.  Every new phrasing of a
question needed a new entry in that table, and a forgotten entry produced an
unbreakable loop: the pending question expected no slots at all, so no answer
could ever close it and the customer was effectively asked to re-type the
question back to the bot.

Matching a free-form reply to a known parameter is the one part of this
pipeline that a language model does well and a regular expression does badly,
so it is the part that moves.  Everything the model is allowed to produce here
stays deliberately small:

* it may only choose a key from the candidate list the caller passes in;
* it may only return a value that is literally present in the customer's
  message — no arithmetic, no unit conversion, no recall from context;
* every value is re-validated against a typed spec (range, unit, choices)
  before it reaches the session.

Derived engineering values are still owned by ``engineering_calculations``.
The resolver writes *raw* facts (``explicit_water_level_depth_m``,
``required_flow_l_min``) exactly like the rule layer does, and normalisation
computes the canonical depths, heads and flows afterwards.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .numeric_semantics import (
    is_bare_numeric_answer,
    numeric_slot_has_compatible_context,
)
from .utils import normalize_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotSpec:
    """A parameter the dialogue may ask for, described well enough to prompt."""

    key: str
    label: str
    kind: str = "number"
    # The raw slot actually written to the session.  ``water_level_depth_m`` is
    # a calculated value, so an answer to that question is stored as
    # ``explicit_water_level_depth_m`` and normalised downstream.
    write_key: str | None = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False
    # Canonical value -> spellings that must appear in the message for the
    # canonical value to be accepted.
    choices: tuple[tuple[Any, tuple[str, ...]], ...] = ()
    # Facts that follow from answering this question at all, mirroring what the
    # rule layer sets alongside the number.
    companions: tuple[tuple[str, Any], ...] = ()
    examples: tuple[str, ...] = ()
    # Stable word stems a customer may use to name this parameter in a
    # refusal.  They complement the human-facing label: Russian case endings
    # make a literal six-character prefix too brittle (``резьба`` vs
    # ``резьбу``), while keeping the vocabulary on the typed slot prevents a
    # dialogue controller from accumulating category-specific phrase hacks.
    mention_stems: tuple[str, ...] = ()

    @property
    def target_key(self) -> str:
        return self.write_key or self.key

    def describe(self) -> str:
        parts = [f"- {self.key} — {self.label}"]
        if self.kind == "number":
            unit = f", в {self.unit}" if self.unit else ""
            bounds = ""
            if self.minimum is not None and self.maximum is not None:
                bounds = f", разумный диапазон {self.minimum:g}–{self.maximum:g}"
            parts.append(f" (число{unit}{bounds})")
        elif self.kind == "enum":
            allowed = ", ".join(str(value) for value, _ in self.choices)
            parts.append(f" (одно из: {allowed})")
        elif self.kind == "text":
            parts.append(" (текст дословно из реплики клиента)")
        if self.examples:
            parts.append(f"; примеры ответов: {'; '.join(self.examples)}")
        return "".join(parts)


def _spec(**kwargs: Any) -> SlotSpec:
    return SlotSpec(**kwargs)


SLOT_SPECS: dict[str, SlotSpec] = {
    spec.key: spec
    for spec in [
        _spec(
            key="water_level_depth_m",
            label="глубина от верха колодца до поверхности воды",
            write_key="explicit_water_level_depth_m",
            unit="метрах",
            minimum=0.1,
            maximum=300,
            companions=(("water_level_reference", "from_top"),),
            examples=("13 метров", "13м", "примерно 8"),
        ),
        _spec(
            key="water_column_depth_m",
            label="высота столба воды от дна колодца до поверхности",
            write_key="explicit_water_column_depth_m",
            unit="метрах",
            minimum=0.1,
            maximum=300,
            companions=(("water_level_reference", "from_bottom"),),
            examples=("2 метра воды", "полтора"),
        ),
        _spec(
            key="well_depth_m",
            label="общая глубина колодца",
            write_key="explicit_well_depth_m",
            unit="метрах",
            minimum=0.1,
            maximum=300,
            examples=("15 метров",),
        ),
        _spec(
            key="horizontal_run_m",
            label="расстояние по горизонтали от источника до дома или точки полива",
            unit="метрах",
            minimum=0,
            maximum=5000,
            examples=("40 метров", "40м", "метров сорок"),
        ),
        _spec(
            key="lift_height_m",
            label=(
                "дополнительный перепад высоты, на который нужно поднять воду "
                "выше её поверхности"
            ),
            unit="метрах",
            minimum=0,
            maximum=300,
            examples=("5 метров", "0", "участок ровный"),
        ),
        _spec(
            key="required_flow_m3_h",
            label="требуемый расход воды в литрах в минуту",
            write_key="required_flow_l_min",
            unit="литрах в минуту",
            minimum=0.1,
            maximum=10000,
            companions=(
                ("flow_unit_status", "confirmed_per_minute"),
                ("flow_unit_assumed", False),
            ),
            examples=("20 литров в минуту", "20 л/мин"),
        ),
        _spec(
            key="static_water_level_m",
            label="статический уровень воды в скважине",
            unit="метрах",
            minimum=0.1,
            maximum=300,
        ),
        _spec(
            key="dynamic_water_level_m",
            label="динамический уровень воды в скважине",
            unit="метрах",
            minimum=0.1,
            maximum=300,
        ),
        _spec(
            key="required_pressure_bar",
            label="давление, которое нужно получить после насоса",
            unit="барах",
            minimum=0.1,
            maximum=25,
            examples=("2 бара", "3 атмосферы"),
        ),
        _spec(
            key="inlet_pressure_bar",
            label="давление, которое есть на входе сейчас",
            unit="барах",
            minimum=0,
            maximum=25,
        ),
        _spec(
            key="mounting_length_mm",
            label="монтажная длина насоса",
            unit="миллиметрах",
            minimum=50,
            maximum=500,
            integer=True,
            examples=("180 мм", "130"),
        ),
        _spec(
            key="water_source",
            label="откуда берётся вода",
            kind="enum",
            choices=(
                ("колодец", ("колодц", "колодец", "колодца", "колодце")),
                ("скважина", ("скважин",)),
                ("центральный водопровод", ("водопровод", "централь", "магистрал")),
                ("ёмкость", ("емкост", "ёмкост", "бочк", "бак", "накопительн")),
                ("водоём", ("водоем", "водоём", "река", "реки", "пруд", "озер")),
            ),
            examples=("из колодца", "скважина", "из бочки"),
        ),
        _spec(
            key="water_level_reference",
            label="от чего отсчитаны кольца: от верха колодца или от дна",
            kind="enum",
            choices=(
                ("from_top", ("от верха", "сверху", "от края", "до воды")),
                ("from_bottom", ("от дна", "со дна", "столб воды", "снизу")),
            ),
        ),
        _spec(
            key="warm_floor_area_m2",
            label="площадь тёплого пола",
            unit="квадратных метрах",
            minimum=1,
            maximum=5000,
            examples=("60 квадратов", "60 м2"),
        ),
        _spec(
            key="warm_floor_type",
            label="тип тёплого пола",
            kind="enum",
            choices=(
                ("водяной", ("водян", "от котл")),
                ("электрический", ("электрическ", "нагревательн", "кабельн")),
            ),
            examples=("водяной от котла", "электрический"),
        ),
        _spec(
            key="floor_insulation_ready",
            label="подготовлено ли утепление или маты под трубу",
            kind="enum",
            choices=(
                (
                    True,
                    (
                        "да",
                        "уже есть",
                        "уложен",
                        "подготовлен",
                        "готово",
                    ),
                ),
                (
                    False,
                    (
                        "нет",
                        "еще нет",
                        "ещё нет",
                        "не уложен",
                        "не готов",
                        "голая плита",
                    ),
                ),
            ),
            examples=("утеплитель уже есть", "нет, пока голая плита"),
        ),
        _spec(
            key="warm_floor_heat_source",
            label="источник тепла для водяного тёплого пола",
            kind="enum",
            choices=(
                ("газовый котёл", ("газов",)),
                ("электрический котёл", ("электрическ",)),
                ("тепловой насос", ("тепловой насос",)),
                ("котёл", ("котл", "котел", "котёл")),
            ),
            examples=("газовый котёл", "электрический котёл", "тепловой насос"),
        ),
        _spec(
            key="warm_floor_automation_needed",
            label="нужна ли покомнатная автоматика тёплого пола",
            kind="enum",
            choices=(
                (
                    True,
                    (
                        "да",
                        "хочу",
                        "нужна",
                        "регулировать",
                        "по комнат",
                        "отдельно",
                    ),
                ),
                (
                    False,
                    (
                        "нет",
                        "не нужна",
                        "не хочу",
                        "без автомат",
                        "без термостат",
                    ),
                ),
            ),
            examples=(
                "да, хочу отдельно регулировать комнаты",
                "нет, без автоматики",
            ),
        ),
        _spec(
            key="area_m2",
            label="отапливаемая площадь дома",
            unit="квадратных метрах",
            minimum=1,
            maximum=5000,
            examples=("100 м2", "сто квадратов"),
        ),
        _spec(
            key="boiler_type",
            label="источник энергии котла: газовый или электрический",
            kind="enum",
            choices=(
                ("газовый", ("газ", "газов")),
                ("электрический", ("электр", "электрическ")),
            ),
        ),
        _spec(
            key="contours",
            label="нужен котёл только для отопления или также для горячей воды",
            kind="enum",
            choices=(
                ("одноконтурный", ("только отоплен", "без горяч", "одноконтур")),
                ("двухконтурный", ("горячая вод", "гвс", "двухконтур")),
            ),
        ),
        _spec(
            key="needs_hot_water",
            label="должен ли котёл готовить горячую воду",
            kind="enum",
            choices=(
                (True, ("нужна горяч", "горячая вод", "гвс", "да")),
                (False, ("не нужна горяч", "только отоплен", "без гвс", "нет")),
            ),
        ),
        _spec(
            key="fitting_system",
            label="система соединения фитинга: PPR или канализация",
            mention_stems=("систем", "тип труб"),
            kind="enum",
            choices=(
                ("ppr", ("ppr", "ппр", "полипроп", "под пайк", "нагрев")),
                ("канализация", ("канализац", "раструб", "серые", "оранжев")),
            ),
        ),
        _spec(
            key="element_type",
            label="тип детали: труба, муфта, угольник, отвод, тройник или переходник",
            kind="enum",
            choices=(
                ("труба", ("труб", "прямой участок", "прямой кусок")),
                ("муфта", ("муфт", "соединить прямо")),
                ("угольник", ("угольник", "уголок", "повернуть")),
                ("отвод", ("отвод", "поворот")),
                ("тройник", ("тройник", "ответвлен")),
                ("переходник", ("переход", "другой диаметр")),
            ),
        ),
        _spec(
            key="sewer_scope",
            label="канализация внутри помещения или наружная в земле/на улице",
            kind="enum",
            choices=(
                ("внутренняя", ("внутр", "в помещ", "под раков", "серая", "серые")),
                ("наружная", ("наруж", "в земле", "на улице", "оранж", "рыж")),
            ),
        ),
        _spec(
            key="length_mm",
            label="длина одного отрезка или заменяемого участка",
            unit="миллиметрах",
            minimum=10,
            maximum=100000,
            integer=True,
            examples=("2000 мм", "2 метра"),
        ),
        _spec(
            key="size_inch",
            label="дюймовый размер присоединения или резьбы",
            mention_stems=("размер", "дюйм", "резьб"),
            kind="enum",
            choices=(
                ("1/2", ("1/2", "полдюйм")),
                ("3/4", ("3/4", "три четверт")),
                ("1", ("1 дюйм", "дюймов")),
            ),
        ),
        _spec(
            key="thread_type",
            label="расположение внутренней и наружной резьбы на двух портах",
            kind="enum",
            choices=(
                ("ff", ("вр-вр", "внутренняя с обеих", "две внутрен")),
                ("fm", ("вр-нр", "внутренняя наружная", "мама папа")),
                ("mm", ("нр-нр", "наружная с обеих", "две наруж")),
            ),
        ),
        _spec(
            key="metric_thread",
            label="метрическая резьба соединения термоголовки",
            kind="text",
            mention_stems=("резьб",),
            examples=("M30x1,5",),
        ),
        _spec(
            key="valve_model",
            label="марка или модель существующего термостатического клапана",
            kind="text",
            mention_stems=("модел", "маркиров"),
        ),
        _spec(
            key="valve_brand",
            label="марка существующего термостатического клапана",
            kind="text",
            mention_stems=("марк", "бренд"),
        ),
        _spec(
            key="connection_form",
            label="подключение детали прямое или угловое",
            kind="enum",
            choices=(
                ("прямое", ("прям", "по одной линии")),
                ("угловое", ("углов", "с поворотом", "под углом")),
            ),
        ),
        _spec(
            key="heating_system_type",
            label="отопление центральное от общей котельной или автономное от своего котла",
            kind="enum",
            choices=(
                ("центральное", ("централь", "общая котельн", "тэц")),
                ("автономное", ("автоном", "свой котел", "свой котёл")),
            ),
        ),
        _spec(
            key="radiator_type",
            label="тип радиатора: панельный, биметаллический или алюминиевый",
            kind="enum",
            choices=(
                ("панельный", ("панельн", "стальной")),
                ("биметаллический", ("биметалл",)),
                ("алюминиевый", ("алюмин",)),
            ),
        ),
        # Параметры труб: их спрашивают тремя пунктами в одном вопросе, поэтому
        # раньше резолвер для этой ветки не имел кандидатов вообще и молча
        # выходил с «no candidate slots».
        _spec(
            key="operating_temperature_c",
            label="максимальная рабочая температура теплоносителя или воды",
            unit="градусах Цельсия",
            minimum=-80,
            maximum=300,
            examples=("70 градусов", "до 90 °C", "95"),
        ),
        _spec(
            key="operating_pressure_bar",
            label="рабочее давление системы",
            unit="барах",
            minimum=0.1,
            maximum=40,
            examples=("2 бара", "1,5 бар", "10"),
        ),
        _spec(
            key="pipe_service",
            label="участок системы, для которого нужна труба",
            kind="enum",
            choices=(
                ("петля тёплого пола", ("тепл", "тёпл", "пол")),
                ("радиаторная разводка", ("радиаторн", "к батаре", "подключение радиатор")),
                ("магистраль отопления", ("магистрал", "стояк")),
                ("обвязка котла", ("обвязк", "от котл", "к котл", "котельн")),
                ("подземный ввод от источника", ("скваж", "колод", "ввод")),
                ("рециркуляция гвс", ("рециркуляц",)),
                (
                    "разводка внутри дома",
                    (
                        "внутри дом",
                        "по дому",
                        "по квартир",
                        "разводк",
                        "от стояк",
                        "к кран",
                        "к точк",
                    ),
                ),
            ),
            examples=("радиаторная разводка", "обвязка котла", "петля тёплого пола"),
        ),
        _spec(
            key="diameter_mm",
            label="расчётный диаметр трубы",
            unit="миллиметрах",
            minimum=6,
            maximum=2500,
            integer=True,
            examples=("20 мм", "диаметр 25", "не знаю диаметр"),
            mention_stems=("диаметр", "размер"),
        ),
        _spec(
            key="volume_l",
            label="объём водонагревателя",
            unit="литрах",
            minimum=1,
            maximum=5000,
            integer=True,
            examples=("80 литров",),
        ),
        _spec(
            key="voltage_v",
            label="напряжение питания",
            kind="enum",
            choices=(("220", ("220", "однофазн")), ("380", ("380", "трехфазн", "трёхфазн"))),
        ),
    ]
}


# Used only when a pending question failed to declare its expected slots.
# Letting the model choose from the category's parameters is what keeps a
# forgotten mapping from turning into an unbreakable question loop.
CATEGORY_SLOTS: dict[str, tuple[str, ...]] = {
    "pumps": (
        "water_source",
        "water_level_depth_m",
        "water_column_depth_m",
        "well_depth_m",
        "horizontal_run_m",
        "lift_height_m",
        "required_flow_m3_h",
        "inlet_pressure_bar",
        "required_pressure_bar",
    ),
    "pipes": (
        "warm_floor_area_m2",
        "warm_floor_type",
        "floor_insulation_ready",
        "warm_floor_heat_source",
        "warm_floor_automation_needed",
        "pipe_service",
        "operating_temperature_c",
        "operating_pressure_bar",
        "diameter_mm",
    ),
    "fittings": ("fitting_system", "element_type", "diameter_mm", "size_inch"),
    "sewer": ("sewer_scope", "element_type", "diameter_mm", "length_mm"),
    "valves": (
        "size_inch",
        "diameter_mm",
        "thread_type",
        "operating_temperature_c",
        "operating_pressure_bar",
    ),
    "radiator_fittings": (
        "metric_thread",
        "valve_model",
        "valve_brand",
        "connection_form",
        "size_inch",
    ),
    "radiators": (
        "heating_system_type",
        "radiator_type",
        "area_m2",
        "operating_pressure_bar",
    ),
    "boilers": (
        "boiler_type",
        "area_m2",
        "contours",
        "needs_hot_water",
        "voltage_v",
    ),
    "water_heaters": ("volume_l", "voltage_v"),
}

RESOLVER_PROMPT = """
Ты сопоставляешь ответ клиента с параметрами, которые бот только что спросил.
Ты не консультант: ничего не советуешь, не считаешь и не переводишь единицы.

ПРАВИЛА:
- Бот часто спрашивает несколько параметров одним вопросом, и клиент отвечает
  на них сразу: «радиаторная разводка, 70 градусов, 2 бара». Верни ВСЕ
  параметры, которые клиент назвал, а не один.
- Ключи бери только из списка «Ожидаемые параметры».
- Значение обязано присутствовать в реплике клиента дословно. Не бери числа
  из вопроса бота, из истории или из своих предположений.
- Ничего не вычисляй и не конвертируй: «30 минут» — это не расход, «5 колец» —
  это не метры. Такой параметр просто не включай.
- Числа внутри дробного размера, размерной пары или маркировки («3/4»,
  «16x2», «25-6») не являются отдельными температурами или давлениями.
- Ответ одной цифрой связывай со слотом только когда в списке ожидается ровно
  один параметр; при нескольких параметрах нужна явная единица или название.
- Отказ — это отдельный результат, а не отсутствие ответа. Если клиент прямо
  говорит, что не знает или не может назвать параметр («не знаю расход»,
  «диаметр не знаю», «без понятия»), положи этот параметр в refused. Так бот
  перестанет спрашивать то же самое и спросит следующее.
- Если реплика вообще не про параметры (клиент сменил тему, задал свой вопрос
  или попросил посчитать за него) — верни пустые списки.
- В evidence положи дословный фрагмент реплики клиента.

Верни только JSON:
{"slots": [{"slot": "ключ", "value": число_или_строка, "evidence": "фрагмент"}],
 "refused": [{"slot": "ключ", "evidence": "фрагмент"}]}
""".strip()


_REFUSAL_RE = re.compile(
    r"\bне\s+знаю\b|\bне\s+помню\b|"
    r"\bне\s+в\s+курсе\b|\bбез\s+понятия\b|"
    r"\bне\s+могу\s+(?:сказать|назвать|измерить|посмотреть)\b|"
    r"\bнеизвестн\w*\b|"
    r"\b(?:нет|неизвестн\w*)\s+данных\b|"
    r"\bне\s+(?:мерил|считал)\w*\b|"
    r"\bне\s+видн\w*\b|\bне\s+читается\b|\bстерл\w*\b"
)


def bind_local_refusals(
    message: str,
    parameter_patterns: dict[str, re.Pattern[str]],
) -> list[str]:
    """Bind uncertainty markers to parameter names in the same local clause.

    The helper is shared by the generic pending-answer resolver and typed
    engineering funnels.  A whole-message check cannot safely handle a reply
    containing both an unknown and known facts, while duplicating slightly
    different scoping rules in every funnel recreates the same bug later.
    """

    text = normalize_text(message)
    if not _REFUSAL_RE.search(text):
        return []
    # A comma can delimit either another fact or another member of the same
    # refusal list.  Preserve the latter (``не знаю ни систему, ни размер``),
    # while an ordinary comma still scopes mixed replies such as ``расход не
    # знаю, напор 6 м`` to the parameter immediately next to the refusal.
    text = re.sub(
        r",\s*(?=(?:и|или|ни|как|какой|какая|какое|какие)\b)",
        " ",
        text,
    )
    clauses = re.split(r"[,;.!?]+|\b(?:а|но|зато)\b", text)
    refused: list[str] = []
    for clause in clauses:
        for refusal in _REFUSAL_RE.finditer(clause):
            positions: dict[str, list[tuple[int, int]]] = {}
            for key, pattern in parameter_patterns.items():
                matches = sorted(
                    {
                        (match.start(), match.end())
                        for match in pattern.finditer(clause)
                    }
                )
                if matches:
                    positions[key] = matches

            before = {
                key: max(
                    (span for span in spans if span[1] <= refusal.start()),
                    default=None,
                    key=lambda span: span[1],
                )
                for key, spans in positions.items()
            }
            before = {key: span for key, span in before.items() if span is not None}
            if before:
                closest_end = max(span[1] for span in before.values())
                closest_start = max(
                    span[0] for span in before.values() if span[1] == closest_end
                )
                for key, span in before.items():
                    is_closest = span[1] == closest_end
                    list_separator = clause[span[1] : closest_start]
                    is_listed_with_closest = bool(
                        not re.search(r"\d", clause[span[1] : refusal.start()])
                        and re.search(r"\b(?:и|или|ни)\b", list_separator)
                    )
                    if (is_closest or is_listed_with_closest) and key not in refused:
                        refused.append(key)
                continue

            # In ``не знаю расход и напор`` the refusal precedes the
            # named fields, so all locally named fields are intentional.
            for key, spans in positions.items():
                if any(span[0] >= refusal.end() for span in spans) and key not in refused:
                    refused.append(key)
    return refused

_WORD_NUMBERS: dict[str, float] = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "полтора": 1.5,
    "полторы": 1.5,
}


@dataclass
class ResolvedAnswer:
    slots: dict[str, Any] = field(default_factory=dict)
    slot_key: str | None = None
    evidence: str | None = None
    # Параметры, от которых клиент прямо отказался («не знаю расход»). Это
    # отдельный результат: раньше отказ и «реплика не про параметры» приходили
    # одинаковым null, и бот повторял тот же вопрос.
    refused: list[str] = field(default_factory=list)
    llm_requested: bool = False
    llm_used: bool = False
    accepted: bool = False
    rejection_reason: str | None = None

    def __bool__(self) -> bool:
        return bool(self.slots or self.refused)


class PendingAnswerResolver:
    """Match a customer's reply to the parameter the pending question asked."""

    _MAX_CANDIDATES = 12

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    # ------------------------------------------------------------------ API

    def resolve(
        self,
        *,
        message: str,
        question: str,
        expected_slots: list[str] | tuple[str, ...] | None,
        category: str | None,
    ) -> ResolvedAnswer:
        candidates = self._candidates(expected_slots, category)
        if not candidates:
            return ResolvedAnswer(rejection_reason="no candidate slots")
        if not str(message or "").strip():
            return ResolvedAnswer(rejection_reason="empty message")

        # A direct answer to one declared choice does not need a probabilistic
        # round trip.  This is especially important for yes/no design questions:
        # a hosted model may return malformed JSON and otherwise leave the user
        # stuck on the same question despite an unambiguous «да, хочу».
        local_enum = self._resolve_single_grounded_enum(message, candidates)
        if local_enum is not None:
            return local_enum

        messages = [
            {"role": "system", "content": RESOLVER_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Вопрос бота: {question}\n\n"
                    "Ожидаемые параметры:\n"
                    + "\n".join(spec.describe() for spec in candidates)
                    + f"\n\nРеплика клиента: {message}"
                ),
            },
        ]
        fallback: dict[str, Any] = {"slots": [], "refused": []}
        try:
            data, used = self.llm_client.complete_json(
                "PendingAnswerResolver",
                messages,
                fallback,
            )
        except Exception as exc:  # pragma: no cover - defensive integration guard
            logger.warning("Pending answer resolution failed: %s", exc)
            return ResolvedAnswer(llm_requested=True, rejection_reason=str(exc))

        result = ResolvedAnswer(llm_requested=True, llm_used=bool(used))
        by_key = {spec.key: spec for spec in candidates}
        declared_slots = [str(key) for key in (expected_slots or [])]
        single_structured_expectation = bool(
            len(declared_slots) == 1
            and declared_slots[0] in by_key
            and len(candidates) == 1
        )
        if not used or not isinstance(data, dict):
            result.rejection_reason = "LLM unavailable"
            # Отказ распознаётся и без LLM: провайдер отваливался в проде по
            # таймауту, и бот не должен из-за этого снова зацикливать вопрос.
            self._apply_offline_refusals(result, message, by_key)
            return result
        if getattr(self.llm_client, "last_json_output_accepted", None) is False:
            result.rejection_reason = "LLM answer was not valid JSON"
            self._apply_offline_refusals(result, message, by_key)
            return result

        slots: dict[str, Any] = {}
        rejected: list[str] = []
        for entry in self._slot_entries(data):
            spec = by_key.get(str(entry.get("slot") or "").strip())
            if spec is None:
                rejected.append("slot outside the candidate list")
                continue
            raw_evidence = str(entry.get("evidence") or "").strip()
            grounded_evidence = (
                raw_evidence
                if raw_evidence
                and normalize_text(raw_evidence) in normalize_text(message)
                else None
            )
            value = self._validate(
                spec,
                entry.get("value"),
                message,
                evidence=grounded_evidence,
                allow_bare_numeric=single_structured_expectation,
            )
            if value is None:
                rejected.append(f"value rejected for {spec.key}")
                continue
            slots[spec.target_key] = value
            for companion_key, companion_value in spec.companions:
                slots[companion_key] = companion_value
            if result.slot_key is None:
                result.slot_key = spec.key
                result.evidence = str(entry.get("evidence") or "")[:120] or None

        supported_refusals = set(
            self._refused_parameter_keys(message, by_key)
        )
        for entry in self._entries(data.get("refused")):
            spec = by_key.get(str(entry.get("slot") or "").strip())
            # Отказ принимается только по ожидаемому параметру и только если
            # слова клиента называют именно его. Иначе модель отказывается за
            # клиента не от того: на «не знаю расход» она возвращала уровень
            # воды и длину трассы, и оба параметра молча пропадали из воронки.
            if spec is None or spec.target_key in slots:
                continue
            if spec.key not in supported_refusals:
                continue
            if spec.key not in result.refused:
                result.refused.append(spec.key)

        if not slots:
            self._apply_offline_refusals(result, message, by_key)
            if not result.refused:
                result.rejection_reason = "; ".join(rejected) or "nothing recognised"
                return result
        result.slots = slots
        result.accepted = True
        return result

    def resolve_local(
        self,
        *,
        message: str,
        expected_slots: list[str] | tuple[str, ...] | None,
        category: str | None,
    ) -> ResolvedAnswer | None:
        """Resolve one explicit choice without requiring an enabled provider."""

        candidates = self._candidates(expected_slots, category)
        if not str(message or "").strip():
            return None
        return self._resolve_single_grounded_enum(message, candidates)

    def detect_refusals(
        self,
        *,
        message: str,
        expected_slots: list[str] | tuple[str, ...] | None,
        category: str | None,
    ) -> list[str]:
        """Отказы по конкретным параметрам, без обращения к LLM.

        Нужен отдельно от ``resolve``: правила могут заполнить часть слотов
        этого же хода, и тогда полное разрешение не запускается — а отказ по
        другому параметру («давление не знаю») терялся вместе с ним.
        """
        candidates = self._candidates(expected_slots, category)
        if not candidates:
            return []
        result = ResolvedAnswer()
        self._apply_offline_refusals(
            result, message, {spec.key: spec for spec in candidates}
        )
        return result.refused

    # ------------------------------------------------------------- internals

    @staticmethod
    def _entries(raw: Any) -> list[dict[str, Any]]:
        """Список записей из ответа модели, устойчивый к одиночному объекту."""
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return [entry for entry in raw if isinstance(entry, dict)]
        return []

    @staticmethod
    def _resolve_single_grounded_enum(
        message: str,
        candidates: list[SlotSpec],
    ) -> ResolvedAnswer | None:
        """Bind one explicit enum/boolean answer without consulting the LLM."""

        if len(candidates) != 1 or candidates[0].kind != "enum":
            return None
        spec = candidates[0]
        text = normalize_text(message)
        matches: list[tuple[int, Any, str]] = []
        for value, markers in spec.choices:
            for marker in markers:
                marker_text = normalize_text(marker)
                if marker_text and PendingAnswerResolver._enum_marker_matches(
                    marker_text,
                    text,
                ):
                    matches.append((len(marker_text), value, marker_text))
        if not matches:
            return None

        # Prefer the most specific phrase: «не нужна» must beat the shorter
        # substring «нужна», and «электрический котёл» must beat «котёл».
        best_length = max(length for length, _, _ in matches)
        winners = [
            (value, marker)
            for length, value, marker in matches
            if length == best_length
        ]
        values = {repr(value): value for value, _ in winners}
        if len(values) != 1:
            return None
        value = next(iter(values.values()))
        evidence = next(marker for candidate, marker in winners if candidate == value)
        slots = {spec.target_key: value}
        slots.update(dict(spec.companions))
        return ResolvedAnswer(
            slots=slots,
            slot_key=spec.key,
            evidence=evidence,
            accepted=True,
        )

    @staticmethod
    def _enum_marker_matches(marker: str, text: str) -> bool:
        if marker in {"да", "нет", "ок"}:
            return bool(
                re.search(
                    rf"(?<![a-zа-я0-9]){re.escape(marker)}(?![a-zа-я0-9])",
                    text,
                )
            )
        return marker in text

    @classmethod
    def _slot_entries(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Записи со значениями, в новой схеме или в одиночной старой.

        Модель может вернуть как ``{"slots": [...]}``, так и одиночный
        ``{"slot": ..., "value": ...}``. Принимаем обе формы: сужать вход
        незачем, а устойчивость к формату дешевле, чем ретрай.
        """
        entries = cls._entries(data.get("slots"))
        if entries:
            return entries
        if data.get("slot") is not None:
            return [data]
        return []

    @staticmethod
    def _looks_like_refusal(message: str) -> bool:
        return bool(_REFUSAL_RE.search(normalize_text(message)))

    @staticmethod
    def _names_parameter(spec: SlotSpec, message: str) -> bool:
        """Названы ли в реплике слова самого параметра.

        Обрезка до 6 символов нужна, чтобы «расход» находился в «расхода», а
        «давление» — в «давлении», без морфологии.
        """
        text = normalize_text(message)
        haystack = normalize_text(f"{spec.label} {spec.key}")
        words = [word for word in haystack.split() if len(word) >= 5]
        return any(word[:6] in text for word in words)

    @classmethod
    def _refused_parameter_keys(
        cls,
        message: str,
        by_key: dict[str, SlotSpec],
    ) -> list[str]:
        """Bind a refusal to the parameter named in the same local clause.

        A message may contain both a refusal and valid values, for example
        ``расход не знаю, напор 6 м, монтажная длина 180 мм``.  Looking for
        ``не знаю`` and a parameter name anywhere in the whole message
        incorrectly deferred the already supplied mounting length.  Refusals
        are therefore scoped to punctuation/disjunctive clauses, and when
        several parameter names precede the marker the closest one wins unless
        the names form an unnumbered list (``расход и напор не знаю``).
        """

        patterns: dict[str, re.Pattern[str]] = {}
        for key, spec in by_key.items():
            haystack = normalize_text(f"{spec.label} {spec.key}")
            stems = {
                word[:6]
                for word in haystack.split()
                if len(word) >= 5
            }
            stems.update(
                normalize_text(stem).strip()
                for stem in spec.mention_stems
                if normalize_text(stem).strip()
            )
            if stems:
                patterns[key] = re.compile(
                    "|".join(
                        rf"(?<![a-zа-я0-9]){re.escape(stem)}\w*"
                        for stem in sorted(stems, key=len, reverse=True)
                    )
                )
        return bind_local_refusals(message, patterns)

    def _apply_offline_refusals(
        self,
        result: ResolvedAnswer,
        message: str,
        by_key: dict[str, SlotSpec],
    ) -> None:
        """Пометить отказ без LLM: «не знаю» + название параметра в реплике.

        Работает при недоступном провайдере и в тестах. Без названия параметра
        отказ не помечается — «не знаю» вообще относится ко всему вопросу, и
        решать, что именно отложить, должен вызывающий, а не эта эвристика.
        """
        if result.refused or not self._looks_like_refusal(message):
            return
        for key in self._refused_parameter_keys(message, by_key):
            if key not in result.refused:
                result.refused.append(key)

    def _candidates(
        self,
        expected_slots: list[str] | tuple[str, ...] | None,
        category: str | None,
    ) -> list[SlotSpec]:
        keys = [str(key) for key in (expected_slots or []) if str(key) in SLOT_SPECS]
        if not keys:
            # A question that declared no expected slots is exactly the failure
            # this resolver exists to absorb.  Offer the category's parameters
            # instead of giving up and looping.
            keys = [
                key
                for key in CATEGORY_SLOTS.get(str(category or ""), ())
                if key in SLOT_SPECS
            ]
        seen: dict[str, SlotSpec] = {}
        for key in keys[: self._MAX_CANDIDATES]:
            seen.setdefault(key, SLOT_SPECS[key])
        return list(seen.values())

    def _validate(
        self,
        spec: SlotSpec,
        raw: Any,
        message: str,
        *,
        evidence: str | None,
        allow_bare_numeric: bool,
    ) -> Any:
        if raw is None:
            return None
        if spec.kind == "enum":
            return self._validate_enum(spec, raw, message)
        if spec.kind == "text":
            candidate = str(raw).strip()
            if not candidate:
                return None
            candidate_text = normalize_text(candidate)
            if candidate_text and candidate_text in normalize_text(message):
                return candidate
            if evidence and normalize_text(evidence) in normalize_text(message):
                return evidence
            return None
        return self._validate_number(
            spec,
            raw,
            message,
            evidence=evidence,
            allow_bare_numeric=allow_bare_numeric,
        )

    def _validate_enum(self, spec: SlotSpec, raw: Any, message: str) -> Any | None:
        text = normalize_text(message)
        candidate = normalize_text(str(raw))
        for value, markers in spec.choices:
            if candidate != normalize_text(value):
                continue
            # The customer's own words must support the choice; otherwise the
            # model is guessing from context rather than reading the reply.
            if any(
                self._enum_marker_matches(normalize_text(marker), text)
                for marker in markers
            ):
                return value
            return None
        return None

    def _validate_number(
        self,
        spec: SlotSpec,
        raw: Any,
        message: str,
        *,
        evidence: str | None,
        allow_bare_numeric: bool,
    ) -> Any:
        try:
            value = float(str(raw).replace(",", ".").strip())
        except (TypeError, ValueError):
            return None
        if value != value or value in (float("inf"), float("-inf")):
            return None
        if spec.minimum is not None and value < spec.minimum:
            return None
        if spec.maximum is not None and value > spec.maximum:
            return None
        if not self._number_present(value, message):
            return None
        if is_bare_numeric_answer(message, value) and not allow_bare_numeric:
            return None
        if not numeric_slot_has_compatible_context(
            spec.key,
            value,
            message=message,
            evidence=evidence,
            pending_slot_keys=(spec.key,) if allow_bare_numeric else (),
        ):
            return None
        if spec.key in {"inlet_pressure_bar", "required_pressure_bar"}:
            text = normalize_text(message)
            inlet_marked = bool(
                re.search(
                    r"(?:на\s+вход\w*|входн\w*\s+давлен\w*|"
                    r"исходн\w*\s+давлен\w*|сейчас|имеетс\w*)",
                    text,
                )
            )
            required_marked = bool(
                re.search(
                    r"(?:нужн\w*|требуем\w*|целев\w*|"
                    r"после\s+насос\w*|на\s+выход\w*)",
                    text,
                )
            )
            # A structured pending slot disambiguates a neutral ``3 бара``,
            # but it cannot overrule the role the customer states explicitly.
            # A turn naming both roles carries more than one fact and must be
            # handled by the deterministic/full interpreter path.
            if inlet_marked and required_marked:
                return None
            if inlet_marked and spec.key != "inlet_pressure_bar":
                return None
            if required_marked and spec.key != "required_pressure_bar":
                return None
        if spec.integer:
            if abs(value - round(value)) > 1e-9:
                return None
            return int(round(value))
        return round(value, 3)

    @staticmethod
    def _number_present(value: float, message: str) -> bool:
        """Reject values the customer never said, however plausible they look."""

        text = normalize_text(message)
        for token in re.findall(r"\d+(?:[.,]\d+)?", text):
            try:
                if abs(float(token.replace(",", ".")) - value) < 1e-9:
                    return True
            except ValueError:
                continue
        for word, word_value in _WORD_NUMBERS.items():
            if word in text and abs(word_value - value) < 1e-9:
                return True
        return False
