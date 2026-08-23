"""Перенос проектного контекста в соседнюю подсистему.

После подбора котла и радиаторов вопрос «а трубы какие?» получал общее
уточнение «труба для чего: вода, отопление или канализация?», хотя проект уже
однозначно отопительный. Вывод делается по состоянию проекта, а не по
формулировке реплики, поэтому «а трубы какие», «чем разводить» и «что по
трубам» ведут себя одинаково.
"""

from __future__ import annotations

import pytest

from app.agents.engineering_requirements import EngineeringRequirementsAgent
from app.agents.orchestrator import ChatOrchestrator


def _context(*goals: dict) -> dict:
    return {"goals": {f"g{i}": goal for i, goal in enumerate(goals)}}


def test_heating_project_answers_the_pipe_purpose() -> None:
    agent = EngineeringRequirementsAgent()
    context = _context(
        {"category": "boilers", "scope": None, "slots": {"boiler_type": "газовый", "area_m2": 120.0}},
        {"category": "radiators", "scope": None, "slots": {"radiator_type": "панельный"}},
    )
    facts = agent._project_inherited_facts("pipes", context)

    assert facts.get("pipe_purpose") == "отопление"
    assert facts.get("warm_floor_heat_source") == "газовый котёл"


def test_house_area_is_not_carried_into_pipes() -> None:
    """120 м² дома — это не 120 м² тёплого пола.

    Перенос площади в трубы превращал «посчитай, сколько фитингов уйдёт» в
    расчёт петель тёплого пола на площадь всего дома.
    """
    agent = EngineeringRequirementsAgent()
    context = _context(
        {"category": "boilers", "scope": None, "slots": {"area_m2": 120.0}}
    )

    assert "area_m2" not in agent._project_inherited_facts("pipes", context)
    assert agent._project_inherited_facts("radiators", context).get("area_m2") == 120.0


def test_water_supply_project_does_not_imply_heating() -> None:
    """Проект с насосом из колодца — не отопление, догадка недопустима."""
    agent = EngineeringRequirementsAgent()
    context = _context(
        {"category": "pumps", "scope": None, "slots": {"water_source": "колодец"}}
    )
    assert "pipe_purpose" not in agent._project_inherited_facts("pipes", context)


def test_warm_floor_scope_counts_as_heating() -> None:
    agent = EngineeringRequirementsAgent()
    context = _context({"category": "pipes", "scope": "warm_floor", "slots": {}})
    assert agent._project_inherited_facts("pipes", context).get("pipe_purpose") == "отопление"


def test_empty_project_infers_nothing() -> None:
    agent = EngineeringRequirementsAgent()
    assert agent._project_inherited_facts("pipes", {"goals": {}}) == {}


@pytest.mark.parametrize(
    "message",
    ["а какие трубы нужны?", "подбери радиаторы", "какой котёл посоветуешь", "давай кран"],
)
def test_request_for_another_subsystem_leaves_the_funnel(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._asks_about_other_subsystem(message, "pumps") is True


@pytest.mark.parametrize(
    "message",
    [
        "диаметр трубы 32",
        "труба 32 от скважины",
        "8 метров",
        "глубина 10",
    ],
)
def test_funnel_answers_stay_in_the_funnel(message: str) -> None:
    """Числовой ответ — это ответ воронке, а не смена подсистемы."""
    bot = ChatOrchestrator(products=[])
    assert bot._asks_about_other_subsystem(message, "pumps") is False


def test_same_subsystem_request_is_not_a_switch() -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._asks_about_other_subsystem("какой насос нужен", "pumps") is False
