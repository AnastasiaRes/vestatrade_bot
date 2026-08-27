"""Классы эксплуатации полипропиленовой трубы из паспорта.

Именно этих значений боту не хватало: на запрос по отоплению он отвечал, что
рабочая температура и давление не подтверждены, хотя паспорт расписывает их по
классам. Ошибка разбора здесь тихо припишет трубе чужое давление, поэтому
тесты фиксируют и разбор, и его отказ.
"""

from __future__ import annotations

from pathlib import Path

from app.docs_loader import (
    _attach_pipe_operating_classes,
    _parse_pipe_class_lines,
    _parse_pipe_operating_classes,
)
from app.models import Product


DATA = Path(__file__).parents[1] / "data"


def _class_table() -> list[str]:
    return [
        "3. Условия применения труб для гарантированного срока службы 50 лет",
        "Класс",
        "эксплуатации",
        "Описание класса эксплуатации Расчетное",
        "рабочее",
        "давление, бар",
        "1 Горячее водоснабжение с температурой",
        "60ºС",
        "13",
        "2 Горячее водоснабжение с температурой",
        "70ºС",
        "10",
        "4 Высокотемпературное напольное",
        "отопление с температурой 70ºС",
        "10",
        "5 Высокотемпературное радиаторное",
        "отопление 90ºС",
        "6",
        "ХВ Холодное водоснабжение 20",
        "4.Технические характеристики",
        "Характеристика Значение характеристики для труб с размерами:",
    ]


def test_heating_classes_are_read_with_their_pressures() -> None:
    classes = _parse_pipe_class_lines(_class_table())

    assert classes["рабочее давление, радиаторное отопление, бар"] == "6"
    assert classes["рабочее давление, напольное отопление, бар"] == "10"


def test_hot_and_cold_water_classes_are_read() -> None:
    classes = _parse_pipe_class_lines(_class_table())

    assert classes["рабочее давление, гвс 60 °с, бар"] == "13"
    assert classes["рабочее давление, гвс 70 °с, бар"] == "10"
    assert classes["рабочее давление, холодное водоснабжение, бар"] == "20"


def test_maximum_temperature_is_the_highest_class_temperature() -> None:
    classes = _parse_pipe_class_lines(_class_table())

    assert classes["максимальная рабочая температура, °с"] == "90"


def test_temperature_is_not_mistaken_for_pressure() -> None:
    # «отопление 90ºС 6» — 90 это температура, 6 давление. Разбор без
    # отделения температуры взял бы последним числом 6 у одних строк и 70 у
    # других, перепутав столбцы местами.
    classes = _parse_pipe_class_lines(_class_table())

    assert "90" not in {
        classes["рабочее давление, радиаторное отопление, бар"],
        classes["рабочее давление, напольное отопление, бар"],
    }


def test_table_end_stops_at_the_next_section() -> None:
    # После таблицы идёт раздел «4.Технические характеристики» с числами
    # размеров: если не остановиться, они утекут в давления.
    lines = _class_table() + ["20х 2,8 25х 3,5 32х 4,4", "Внутренний диаметр, мм 14,4 18"]

    classes = _parse_pipe_class_lines(lines)

    assert classes["рабочее давление, холодное водоснабжение, бар"] == "20"


def test_missing_table_is_a_normal_result() -> None:
    assert _parse_pipe_class_lines(["1. Назначение", "Трубы применяются"]) == {}


def _pipe(attributes: dict[str, str]) -> Product:
    return Product(
        sku="VTp.700.FB20.25",
        name="Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        price=168.0,
        currency="RUB",
        stock_status="в наличии",
        url="https://example.invalid/pipe",
        attributes_normalized=attributes,
    )


def test_feed_value_is_never_overwritten() -> None:
    product = _pipe({"максимальная рабочая температура, °с": "70"})

    _attach_pipe_operating_classes(product, _parse_pipe_class_lines(_class_table()))

    assert product.attributes_normalized["максимальная рабочая температура, °с"] == "70"


def test_fiber_and_alux_passports_differ_where_it_matters() -> None:
    # Ради этой пары всё и делалось: на радиаторной магистрали PP-FIBER PN20
    # держит 6 бар при 90 °С, а PP-ALUX PN25 — 10 бар при 95 °С.
    fiber = _parse_pipe_operating_classes(DATA / "VTp.700.FB20-0425.pdf")
    alux = _parse_pipe_operating_classes(DATA / "VTp.700.AL25-0425.pdf")

    assert fiber["рабочее давление, радиаторное отопление, бар"] == "6"
    assert alux["рабочее давление, радиаторное отопление, бар"] == "10"
    assert fiber["максимальная рабочая температура, °с"] == "90"
    assert alux["максимальная рабочая температура, °с"] == "95"


def test_passport_without_a_class_table_yields_nothing() -> None:
    # У неармированной серии давления даны в МПа внутри размерной таблицы и без
    # температур. Догадываться о них по номеру класса нельзя.
    assert _parse_pipe_operating_classes(DATA / "VTp.700.0020-0425.pdf") == {}
