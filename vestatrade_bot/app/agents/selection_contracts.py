"""Declarative minimum facts required before a catalogue selection.

The contracts in this module are intentionally small and deterministic.  They
describe *which canonical facts* are required; wording and natural-language
extraction stay in the router/slot-filling agents.  Keeping the requirement
groups independent from question text prevents a paraphrase from changing the
engineering meaning of a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SlotRequirement:
    """One required fact, optionally satisfied by one of several slot keys."""

    key: str
    any_of: tuple[str, ...]
    prompt: str

    def is_satisfied(self, slots: Mapping[str, Any]) -> bool:
        return any(
            candidate in slots
            and slots[candidate] is not None
            and slots[candidate] not in ("", [], {})
            for candidate in self.any_of
        )


@dataclass(frozen=True)
class SelectionContract:
    """Ordered, product-kind-specific preconditions for safe retrieval."""

    requirements: tuple[SlotRequirement, ...]

    def missing(self, slots: Mapping[str, Any]) -> list[SlotRequirement]:
        return [
            requirement
            for requirement in self.requirements
            if not requirement.is_satisfied(slots)
        ]


@dataclass(frozen=True)
class ObservableSelectionGuide:
    """A non-technical route to facts needed by a selection contract.

    ``technical_slots`` are deliberately kept separate from the wording.  The
    dialogue controller can therefore remember that a value is unknown and
    still ask for an observation which resolves the same fact.  This avoids a
    parallel set of phrase-specific funnels for every catalogue family.
    """

    technical_slots: tuple[str, ...]
    prompt: str
    expected_slots: tuple[str, ...] = ()

    def applies(self, missing: set[str], deferred: set[str]) -> bool:
        relevant = set(self.technical_slots)
        return bool(relevant.intersection(missing) and relevant.intersection(deferred))


# These prompts ask about things a householder can normally observe: location,
# colour, geometry, a body marking or an existing installation.  They never
# supply the missing engineering value on the customer's behalf.
OBSERVABLE_SELECTION_GUIDES: dict[str, tuple[ObservableSelectionGuide, ...]] = {
    "fittings": (
        ObservableSelectionGuide(
            ("fitting_system", "element_type", "diameter_mm", "size_inch"),
            (
                "Техническое название знать не обязательно. Опишите соединение: "
                "белые/серые пластиковые трубы соединяются нагревом (PPR) или это "
                "раструбная канализация; нужно соединить прямо, повернуть, сделать "
                "ответвление либо перейти на другой диаметр/резьбу. Размер обычно "
                "напечатан на трубе — например 20, 25, 32, DN50 или DN110. Если "
                "маркировки нет, напишите наружный диаметр и что находится с обеих сторон."
            ),
            ("fitting_system", "element_type", "diameter_mm", "size_inch"),
        ),
    ),
    "sewer": (
        ObservableSelectionGuide(
            ("sewer_scope", "element_type", "diameter_mm", "length_mm"),
            (
                "Специальные обозначения знать не нужно. Подскажите, труба находится "
                "внутри помещения или в земле/на улице; она серая или оранжевая; нужен "
                "прямой участок, поворот, ответвление либо ремонтная муфта. Диаметр можно "
                "прочитать как DN50/DN110 или измерить наружный размер, а для прямой трубы "
                "нужна ещё длина заменяемого участка."
            ),
            ("sewer_scope", "element_type", "diameter_mm", "length_mm"),
        ),
    ),
    "valves": (
        ObservableSelectionGuide(
            ("application", "diameter_mm", "size_inch", "connection_size", "thread_type"),
            (
                "Размер и тип резьбы можно не называть по памяти. Напишите, что перекрывает "
                "кран — холодную/горячую воду или отопление; что выбито на корпусе "
                "(например 1/2 или 3/4); и где резьба видна снаружи, а где находится "
                "внутри отверстия. К сожалению, загрузка фотографий в этом чате пока "
                "не поддерживается. "
                "Если маркировка не читается, опишите обе ответные детали словами или "
                "попросите мастера снять размер после безопасного перекрытия воды."
            ),
            ("application", "diameter_mm", "size_inch", "connection_size", "thread_type"),
        ),
    ),
    "radiator_fittings": (
        ObservableSelectionGuide(
            ("metric_thread", "valve_model", "valve_brand"),
            (
                "Резьбу измерять на глаз не нужно. Посмотрите марку и модель на корпусе "
                "термостатического клапана или в паспорте радиатора; если читается только "
                "марка — напишите её. К сожалению, загрузка фотографий в этом чате пока "
                "не поддерживается, "
                "поэтому перепишите всю видимую маркировку и словами опишите посадочное "
                "место. Внешний вид сам по себе не подтверждает M30x1,5."
            ),
            ("metric_thread", "valve_model", "valve_brand"),
        ),
        ObservableSelectionGuide(
            ("connection_form", "body_form", "diameter_mm", "size_inch", "control_mode"),
            (
                "Опишите существующий узел: труба подходит к радиатору прямо или с "
                "поворотом; на корпусе написано 1/2 либо 3/4; нужно автоматически "
                "регулировать температуру или только перекрывать поток. По этим "
                "наблюдаемым признакам можно выбрать исполнение без знания терминов."
            ),
            ("connection_form", "body_form", "diameter_mm", "size_inch", "control_mode"),
        ),
    ),
    "radiators": (
        ObservableSelectionGuide(
            ("heating_system_type",),
            (
                "Тип системы можно определить без паспорта: если дом отапливает общая "
                "котельная/ТЭЦ — отопление центральное; если вашим котлом — автономное. "
                "Уточните, система центральная или автономная."
            ),
            ("heating_system_type",),
        ),
        ObservableSelectionGuide(
            ("operating_pressure_bar",),
            (
                "Рабочее и опрессовочное давление для центрального отопления лучше "
                "запросить у управляющей организации — этаж и материал старой батареи "
                "его не заменяют. Пока давления нет, можно показать предварительные "
                "варианты по комнате и подключению, но не обещать совместимость."
            ),
            ("operating_pressure_bar",),
        ),
        ObservableSelectionGuide(
            (
                "radiator_size_mm",
                "radiator_height_mm",
                "length_mm",
                "sections",
                "heat_load_w",
                "heat_output_w",
            ),
            (
                "Для предварительного подбора достаточно измерить комнату, высоту и "
                "длину места под окном, межосевое расстояние старой батареи и посмотреть, "
                "с какой стороны подходят трубы. Если старой батареи нет — напишите "
                "площадь комнаты, число наружных стен и размер окна; тепловую мощность "
                "тогда обозначу только как ориентир, не как проектный расчёт."
            ),
            (
                "area_m2",
                "radiator_size_mm",
                "radiator_height_mm",
                "length_mm",
                "sections",
                "heat_load_w",
                "heat_output_w",
            ),
        ),
    ),
    "boilers": (
        ObservableSelectionGuide(
            ("boiler_type",),
            (
                "Мощность старого котла для первого шага не нужна. Сначала достаточно "
                "сказать, подведён ли к дому газ и какая электрическая мощность доступна: "
                "это определит газовую или электрическую ветку подбора."
            ),
            ("boiler_type", "has_gas", "has_electricity"),
        ),
        ObservableSelectionGuide(
            ("area_m2", "power_kw"),
            (
                "Если мощность неизвестна, напишите отапливаемую площадь, регион, этажность "
                "и насколько дом утеплён. По площади можно дать только предварительный "
                "диапазон, а окончательную мощность проверяют по теплопотерям."
            ),
            ("area_m2", "floors"),
        ),
        ObservableSelectionGuide(
            ("contours", "needs_hot_water"),
            (
                "Контурность можно не знать: скажите, должен ли котёл также готовить "
                "горячую воду и сколько душей/кранов могут работать одновременно."
            ),
            ("contours", "needs_hot_water"),
        ),
    ),
}


def observable_selection_guidance(
    category: str,
    missing_slots: list[str] | tuple[str, ...] | set[str],
    deferred_slots: list[str] | tuple[str, ...] | set[str],
) -> tuple[str, list[str]] | None:
    """Return the first applicable observation route for an unknown fact."""

    missing = {str(key) for key in missing_slots}
    deferred = {str(key) for key in deferred_slots}
    if not missing or not deferred:
        return None
    for guide in OBSERVABLE_SELECTION_GUIDES.get(str(category), ()):
        if guide.applies(missing, deferred):
            expected = list(guide.expected_slots or guide.technical_slots)
            return guide.prompt, expected
    return None


VALVE_BASE_CONTRACT = SelectionContract(
    requirements=(
        SlotRequirement(
            key="application",
            any_of=("application",),
            prompt=(
                "для чего нужен кран: вода (холодная/горячая), отопление "
                "или радиатор"
            ),
        ),
        SlotRequirement(
            key="connection_size",
            any_of=("diameter_mm", "size_inch", "connection_size"),
            prompt="размер: 1/2, 3/4 или диаметр в мм",
        ),
    )
)


THREADED_BALL_VALVE_CONTRACT = SelectionContract(
    requirements=(
        SlotRequirement(
            key="thread_type",
            any_of=("thread_type",),
            prompt="тип резьбы: ВР-ВР, ВР-НР или НР-НР",
        ),
    )
)


RADIATOR_VALVE_CONTRACT = SelectionContract(
    requirements=(
        SlotRequirement(
            key="connection_form",
            any_of=("connection_form", "body_form"),
            prompt="прямое или угловое подключение",
        ),
        SlotRequirement(
            key="connection_size",
            any_of=("diameter_mm", "size_inch"),
            prompt="размер 1/2 или 3/4",
        ),
    )
)


GENERIC_RADIATOR_FITTING_CONTRACT = SelectionContract(
    requirements=(
        *RADIATOR_VALVE_CONTRACT.requirements,
        SlotRequirement(
            key="control_mode",
            any_of=("control_mode",),
            prompt=(
                "регулировать температуру термоголовкой или просто "
                "перекрывать поток"
            ),
        ),
    )
)


THERMOSTATIC_HEAD_CONTRACT = SelectionContract(
    requirements=(
        SlotRequirement(
            key="head_interface",
            any_of=("metric_thread", "valve_model", "valve_brand"),
            prompt=(
                "марку/модель термостатического клапана или резьбу под "
                "термоголовку, например M30x1,5"
            ),
        ),
    )
)


# Повторять вопрос дословно бесполезно: если покупатель не ответил, значит он
# не знает параметр. Подсказка объясняет, где его взять или какое значение
# считается стандартным, — это выход из тупика без выдумывания данных.
SLOT_ANSWER_HINTS: dict[str, str] = {
    "metric_thread": (
        "Если модель клапана неизвестна, посмотрите марку/модель на его корпусе. "
        "К сожалению, загрузка фотографий в этом чате пока не поддерживается, поэтому перепишите "
        "маркировку и опишите посадочное место словами. M30x1,5 распространена, "
        "но считать её вашей резьбой без проверки нельзя."
    ),
    "valve_model": (
        "Модель обычно выбита на корпусе клапана или есть в паспорте радиатора."
    ),
    "water_level_depth_m": (
        "Это расстояние от верха колодца до зеркала воды: видно по мокрому "
        "следу на кольцах либо замеряется верёвкой с грузом. Глубина самого "
        "колодца — другая величина, для подбора нужна именно вода."
    ),
    "lift_height_m": (
        "Это перепад высот между насосом и самой верхней точкой разбора: "
        "примерно 3 метра на этаж."
    ),
    "required_flow_m3_h": (
        "Ориентир: одна точка разбора — около 0,6 м³/ч, душ — около 0,7 м³/ч."
    ),
    "dynamic_water_level_m": (
        "Динамический уровень — расстояние от верха скважины до воды во время "
        "устоявшейся работы насоса. Его берут из паспорта/акта прокачки или "
        "измеряет специалист при работающем насосе; глубина скважины его не заменяет."
    ),
    "volume_l": (
        "Если выбираете новый водонагреватель, объём не нужно знать заранее: "
        "назовите число пользователей, сколько душей/кранов работают одновременно "
        "и доступную электрическую мощность — сначала определим подходящий тип и диапазон."
    ),
    "size_inch": (
        "Размер крана ищут в маркировке на корпусе (например 1/2 или 3/4) и "
        "сверяют по обоим присоединениям. К сожалению, загрузка фотографий в этом "
        "чате пока не поддерживается. Если маркировка не читается, опишите оба соединения словами "
        "или попросите мастера снять размер после перекрытия воды."
    ),
    "connection_size": (
        "Размер ищут в маркировке на корпусе и сверяют по обоим присоединениям; "
        "для резьбовых деталей наружный диаметр линейкой не равен дюймовому названию."
    ),
    "solids_mm": (
        "Допустимый размер частиц — характеристика самого дренажного насоса, а не "
        "число, которое покупатель обязан знать заранее. Опишите воду: только муть/песок, "
        "мелкие камешки, листья или волокнистый мусор; по этому наблюдению сначала "
        "выбирают класс насоса, а точный предел сверяют в его паспорте."
    ),
}


def slot_answer_hint(expected_slots: list[str] | tuple[str, ...]) -> str:
    """Подсказка для первого повтора вопроса, если она известна."""
    for key in expected_slots or ():
        hint = SLOT_ANSWER_HINTS.get(str(key))
        if hint:
            return hint
    return ""


def missing_requirements(
    slots: Mapping[str, Any],
    *contracts: SelectionContract,
) -> list[SlotRequirement]:
    """Return ordered missing requirements without duplicates."""

    result: list[SlotRequirement] = []
    seen: set[str] = set()
    for contract in contracts:
        for requirement in contract.missing(slots):
            if requirement.key in seen:
                continue
            seen.add(requirement.key)
            result.append(requirement)
    return result
