"""Состав проекта: «что ещё нужно?» должен давать спецификацию, а не поиск.

До правки бот умел показать уже выбранное («покажи подборку»), но на вопрос
о составе отвечал «по указанным ограничениям не нашёл подтверждённого товара».
Состав описан таблицей, а не генерацией модели: количества по нему проверяемы
и одинаковы от прогона к прогону.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.project_specification import heating_project_nodes


@pytest.mark.parametrize(
    "message",
    [
        "что ещё нужно?",
        "что докупить?",
        "чего не хватает?",
        "собери комплект",
        "полный список",
        "перечисли, что нужно для системы",
        "что понадобится ещё",
        "состав проекта",
    ],
)
def test_specification_request_is_recognised(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._wants_project_specification(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "покажи подборку",
        "что входит в комплектацию котла",
        "ответь по паспорту, что входит в полную комплектацию",
        "нужен кран 1/2 вн-вн",
        "что там по котлу",
    ],
)
def test_other_requests_are_not_specification(message: str) -> None:
    """Комплектация товара и показ корзины — не вопрос о составе проекта."""
    bot = ChatOrchestrator(products=[])
    assert bot._wants_project_specification(message) is False


def test_gas_boiler_project_lists_chimney_as_missing_from_catalogue() -> None:
    """Дымоход нужен, но его нет в ассортименте — молчать об этом нельзя."""
    nodes = heating_project_nodes({"boiler_type": "газовый", "has_radiators": True})
    chimney = next(node for node in nodes if node.key == "chimney")

    assert chimney.category is None
    assert any(node.key == "radiator_fittings" for node in nodes)


def test_electric_boiler_project_has_no_chimney() -> None:
    nodes = heating_project_nodes({"boiler_type": "электрический"})
    assert all(node.key != "chimney" for node in nodes)


def test_warm_floor_project_adds_a_collector() -> None:
    nodes = heating_project_nodes({"has_warm_floor": True, "warm_floor_type": "водяной"})
    assert any(node.key == "collector" for node in nodes)


def test_plain_radiator_project_has_no_collector() -> None:
    nodes = heating_project_nodes({"boiler_type": "газовый", "has_radiators": True})
    assert all(node.key != "collector" for node in nodes)


def test_every_node_states_its_purpose_and_has_no_invented_quantity() -> None:
    """Норма выдаётся формулой, а не готовым числом: цифра без схемы — выдумка."""
    for node in heating_project_nodes({"boiler_type": "газовый", "has_radiators": True}):
        assert node.purpose, node.key
        assert not any(character.isdigit() for character in node.rate), node.key


def test_specification_needs_a_running_project(orchestrator) -> None:
    """«Хочу тёплый пол, что нужно?» в первой реплике — начало задачи."""
    response = orchestrator.handle_chat("spec-cold", "хочу сделать тёплый пол, что нужно?")
    assert not response.answer.startswith("Состав системы отопления")


# --- Санузел: подключение прибора --------------------------------------------


def test_toilet_nodes_cover_the_connection_and_state_catalogue_gaps() -> None:
    from app.agents.project_specification import toilet_installation_nodes

    nodes = toilet_installation_nodes()
    keys = {node.key for node in nodes}
    assert {"outlet", "water_hose", "shutoff", "mounting"} <= keys

    mounting = next(node for node in nodes if node.key == "mounting")
    assert mounting.category is None, "крепежа и герметика в каталоге нет"

    for node in nodes:
        assert node.purpose, node.key
        assert not any(character.isdigit() for character in node.rate), node.key


@pytest.mark.parametrize(
    "message",
    ["что именно брать?", "перечисли что брать", "что конкретно нужно"],
)
def test_filler_words_do_not_break_the_specification_request(message: str) -> None:
    """«Что именно брать» — тот же вопрос, что и «что брать»."""
    bot = ChatOrchestrator(products=[])
    assert bot._wants_project_specification(message) is True
