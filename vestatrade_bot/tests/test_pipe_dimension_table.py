"""Размерная таблица трубы из паспорта.

Колонки таблицы — типоразмеры, и позицию товара определяет его наружный
диаметр. Ошибка в разметке колонок припишет трубе толщину стенки и вес от
соседнего размера, поэтому разбор себя проверяет, а тесты фиксируют проверку.
"""

from __future__ import annotations

from pathlib import Path

from app.docs_loader import (
    _attach_pipe_dimensions,
    _parse_pipe_dimension_lines,
    _parse_pipe_dimensions,
)
from app.models import Product


DATA = Path(__file__).parents[1] / "data"


def _one_line_header() -> list[str]:
    return [
        "2.Технические характеристики",
        "Характеристика Значение характеристики для труб размерами:",
        "20х3,4 25х4,2 32х5,4 40х6,7 50х8,3 63х10,5 75х12,5 90х15",
        "Номинальный наружный",
        "диаметр, мм 20 25 32 40 50 63 75 90",
        "Номинальная толщина",
        "стенки, мм 3,4 4,2 5,4 6,7 8,3 10,5 12,5 15",
        "Внутренний диаметр, мм 13,2 16,6 21,2 26,6 33,4 42,0 50,0 60,0",
        "Вес трубы, кг/м. п. 0,166 0,256 0,419 0,639 1,006 1,600 2,266 3,259",
        "Объём жидкости в 1 м.п.,л 0,137 0,216 0,353 0,555 0,876 1,385 1,963 2,826",
        "Максимальная рабочая",
        "температура, ºС",
        "85 85 85 85 85 85 85 85",
    ]


def test_row_is_mapped_onto_the_size_columns() -> None:
    table = _parse_pipe_dimension_lines(_one_line_header())

    assert table["25"]["внутренний диаметр, мм"] == "16.6"
    assert table["25"]["толщина стенки (мм)"] == "4.2"
    assert table["90"]["внутренний диаметр, мм"] == "60.0"


def test_label_digits_do_not_shift_the_values() -> None:
    # «Объём жидкости в 1 м.п.,л» содержит единицу в названии: счёт всех чисел
    # строки сдвинул бы значения на одну колонку.
    table = _parse_pipe_dimension_lines(_one_line_header())

    assert table["20"]["объём жидкости, л/м"] == "0.137"
    assert table["90"]["объём жидкости, л/м"] == "2.826"


def test_values_on_the_line_after_the_label_are_read() -> None:
    table = _parse_pipe_dimension_lines(_one_line_header())

    assert table["32"]["максимальная рабочая температура, °с"] == "85"


def test_header_split_across_lines_is_understood() -> None:
    # У армированных паспортов шапка переносится: «20х» и «2,8» на разных
    # строках, а иногда строка обрывается на середине размера — «… 63х».
    lines = [
        "4.Технические характеристики",
        "Характеристика Значение характеристики для труб с размерами:",
        "20х3,4 25х4,2 32х5,4 40х6,7 50х8,3 63х",
        "10,5",
        "75х",
        "12,5",
        "90х",
        "15",
        "Номинальный наружный диаметр,",
        "мм",
        "20 25 32 40 50 63 75 90",
        "Внутренний",
        "диаметр, мм 13,2 16,6 21,2 26,6 33,4 42,0 50 60",
    ]

    table = _parse_pipe_dimension_lines(lines)

    assert len(table) == 8
    assert table["63"]["внутренний диаметр, мм"] == "42.0"


def test_mismatched_outer_diameter_row_rejects_the_table() -> None:
    # Строка наружного диаметра обязана совпасть с шапкой. Не совпала —
    # колонки прочитаны неверно, доверять остальным строкам нельзя.
    lines = [
        line.replace("20 25 32", "99 25 32") if "диаметр, мм 20 25 32" in line else line
        for line in _one_line_header()
    ]

    assert _parse_pipe_dimension_lines(lines) == {}


def test_missing_table_is_a_normal_result() -> None:
    assert _parse_pipe_dimension_lines(["1. Назначение", "Трубы применяются"]) == {}


def _pipe(name: str, attributes: dict[str, str]) -> Product:
    return Product(
        sku="VTp.700.0020.25",
        name=name,
        price=100.0,
        currency="RUB",
        stock_status="в наличии",
        url="https://example.invalid/pipe",
        attributes_normalized=attributes,
    )


def test_column_is_chosen_by_the_outer_diameter() -> None:
    product = _pipe("Труба PN 20, 25 MM (белый)", {"диаметр (мм)": "25"})

    _attach_pipe_dimensions(product, _parse_pipe_dimension_lines(_one_line_header()))

    assert product.attributes_normalized["внутренний диаметр, мм"] == "16.6"
    assert product.attributes_normalized["вес трубы, кг/м"] == "0.256"


def test_feed_value_is_never_overwritten() -> None:
    product = _pipe(
        "Труба PN 20, 25 MM (белый)",
        {"диаметр (мм)": "25", "толщина стенки (мм)": "9.9"},
    )

    _attach_pipe_dimensions(product, _parse_pipe_dimension_lines(_one_line_header()))

    assert product.attributes_normalized["толщина стенки (мм)"] == "9.9"


def test_unknown_diameter_attaches_nothing() -> None:
    product = _pipe("Труба PP-R без размера", {})

    _attach_pipe_dimensions(product, _parse_pipe_dimension_lines(_one_line_header()))

    assert product.attributes_normalized == {}


def test_every_pipe_passport_parses() -> None:
    for filename in (
        "VTp.700.0020-0425.pdf",
        "VTp.700.FB20-0425.pdf",
        "VTp.700.AL25-0425.pdf",
        "VTp.700.FB25-1125.pdf",
    ):
        table = _parse_pipe_dimensions(DATA / filename)

        assert table, filename
        assert "внутренний диаметр, мм" in table["25"], filename
