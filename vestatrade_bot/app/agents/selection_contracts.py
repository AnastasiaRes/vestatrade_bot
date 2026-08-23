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
            any_of=("metric_thread", "valve_model"),
            prompt=(
                "модель термостатического клапана или резьбу под "
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
        "Если модель клапана неизвестна: у большинства современных "
        "термостатических клапанов присоединение M30x1,5. Напишите «M30x1,5» — "
        "подберу под него, это подойдёт к большинству клапанов."
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
