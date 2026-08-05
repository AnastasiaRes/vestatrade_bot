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
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Facts that follow from answering this question at all, mirroring what the
    # rule layer sets alongside the number.
    companions: tuple[tuple[str, Any], ...] = ()
    examples: tuple[str, ...] = ()

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
            allowed = ", ".join(value for value, _ in self.choices)
            parts.append(f" (одно из: {allowed})")
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
            key="area_m2",
            label="отапливаемая площадь дома",
            unit="квадратных метрах",
            minimum=1,
            maximum=5000,
            examples=("100 м2", "сто квадратов"),
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
        "required_pressure_bar",
    ),
    "pipes": ("warm_floor_area_m2",),
    "boilers": ("area_m2", "voltage_v"),
    "water_heaters": ("volume_l", "voltage_v"),
}

RESOLVER_PROMPT = """
Ты сопоставляешь короткий ответ клиента с параметром, который бот только что
спросил. Ты не консультант: ничего не советуешь, не считаешь и не переводишь
единицы.

ПРАВИЛА:
- Выбери ровно один параметр из списка «Ожидаемые параметры» или null.
- Значение обязано присутствовать в реплике клиента дословно. Не бери числа
  из вопроса бота, из истории или из своих предположений.
- Ничего не вычисляй и не конвертируй: «30 минут» — это не расход, «5 колец» —
  это не метры. В таких случаях верни null.
- Если клиент отказался отвечать, спросил что-то своё, сменил тему или
  попросил посчитать за него — верни null.
- В evidence положи дословный фрагмент реплики клиента, из которого взято
  значение.

Верни только JSON:
{"slot": "ключ_параметра_или_null", "value": число_или_строка_или_null, "evidence": "фрагмент реплики"}
""".strip()

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
    llm_requested: bool = False
    llm_used: bool = False
    accepted: bool = False
    rejection_reason: str | None = None

    def __bool__(self) -> bool:
        return bool(self.slots)


class PendingAnswerResolver:
    """Match a customer's reply to the parameter the pending question asked."""

    _MAX_CANDIDATES = 8

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
        fallback: dict[str, Any] = {"slot": None, "value": None, "evidence": None}
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
        if not used or not isinstance(data, dict):
            result.rejection_reason = "LLM unavailable"
            return result
        if getattr(self.llm_client, "last_json_output_accepted", None) is False:
            result.rejection_reason = "LLM answer was not valid JSON"
            return result

        by_key = {spec.key: spec for spec in candidates}
        spec = by_key.get(str(data.get("slot") or "").strip())
        if spec is None:
            result.rejection_reason = "slot outside the candidate list"
            return result

        value = self._validate(spec, data.get("value"), message)
        if value is None:
            result.rejection_reason = f"value rejected for {spec.key}"
            return result

        slots: dict[str, Any] = {spec.target_key: value}
        for companion_key, companion_value in spec.companions:
            slots[companion_key] = companion_value
        result.slots = slots
        result.slot_key = spec.key
        result.evidence = str(data.get("evidence") or "")[:120] or None
        result.accepted = True
        return result

    # ------------------------------------------------------------- internals

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

    def _validate(self, spec: SlotSpec, raw: Any, message: str) -> Any:
        if raw is None:
            return None
        if spec.kind == "enum":
            return self._validate_enum(spec, raw, message)
        return self._validate_number(spec, raw, message)

    def _validate_enum(self, spec: SlotSpec, raw: Any, message: str) -> str | None:
        text = normalize_text(message)
        candidate = normalize_text(str(raw))
        for value, markers in spec.choices:
            if candidate != normalize_text(value):
                continue
            # The customer's own words must support the choice; otherwise the
            # model is guessing from context rather than reading the reply.
            if any(marker in text for marker in markers):
                return value
            return None
        return None

    def _validate_number(self, spec: SlotSpec, raw: Any, message: str) -> Any:
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
