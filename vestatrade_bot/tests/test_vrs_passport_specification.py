"""Извлечение таблицы характеристик из паспорта циркуляционных насосов VRS.

Таблица использует объединённые ячейки, и после извлечения текста от них
остаётся только количество значений в строке. Ошибка в разметке колонок тихо
припишет насосу чужой расход, поэтому тесты фиксируют и разбор, и его отказ.
"""

from __future__ import annotations

from pathlib import Path

from app.agents.feed_search import FeedSearchAgent
from app.docs_loader import (
    _attach_vrs_pump_specification,
    _parse_vrs_lines,
    _parse_vrs_specification,
)
from app.models import Product


PASSPORT = Path(__file__).parents[1] / "data" / "VRS-0725.pdf"


def _table_lines() -> list[str]:
    return [
        "3. Технические характеристики",
        "№ Характеристика Ед.",
        "изм",
        "Значение для типа VRS.",
        "254.", "130",
        "254.", "180",
        "324.", "180",
        "256.", "130",
        "256.", "180",
        "326.", "180",
        "258.", "180",
        "328.", "180",
        "1 Монтажная длина мм 130 180 180 130 180 180 180 180",
        "2 Максимальный напор:",
        "2.3 -скорость III м 4,2 6 8,5",
        "3 Максимальный расход:",
        "3.1 -скорость I м3/час 1,26 1,32 2,70",
        "3.3 -скорость III м3/час 2,94 3,3 6,90",
        "5 Вес кг 2,3 2,4 2,5 2,4 2,5 2,7 4,2 4,8",
        "9 Минимальное статическое",
        "давление",
        "бар 0,7 0,9 1,0",
    ]


def test_three_values_are_spread_across_head_series() -> None:
    # «2,94 3,3 6,90» — это три группы напора: {254,324}, {256,326}, {258,328}.
    spec = _parse_vrs_lines(_table_lines())

    assert spec["254.180"]["максимальный расход (скорость iii), м3/ч"] == "2.94"
    assert spec["324.180"]["максимальный расход (скорость iii), м3/ч"] == "2.94"
    assert spec["256.130"]["максимальный расход (скорость iii), м3/ч"] == "3.3"
    assert spec["258.180"]["максимальный расход (скорость iii), м3/ч"] == "6.90"


def test_eight_values_are_spread_across_columns() -> None:
    spec = _parse_vrs_lines(_table_lines())

    assert spec["254.130"]["вес, кг"] == "2.3"
    assert spec["254.180"]["вес, кг"] == "2.4"
    assert spec["328.180"]["вес, кг"] == "4.8"


def test_unit_with_a_digit_does_not_shift_the_values() -> None:
    # «м3/час» содержит цифру: без отдельной обработки тройка значений
    # превращалась в четвёрку и строка отбрасывалась целиком.
    spec = _parse_vrs_lines(_table_lines())

    assert spec["254.180"]["максимальный расход (скорость i), м3/ч"] == "1.26"


def test_values_wrapped_onto_the_next_line_are_read() -> None:
    spec = _parse_vrs_lines(_table_lines())

    assert spec["254.180"]["минимальное статическое давление, бар"] == "0.7"
    assert spec["258.180"]["минимальное статическое давление, бар"] == "1.0"


def test_mismatched_mounting_row_rejects_the_whole_table() -> None:
    # Строка монтажной длины обязана совпасть с суффиксами колонок. Если не
    # совпала, колонки прочитаны неверно, и доверять остальным строкам нельзя.
    lines = [
        line.replace("130 180 180 130", "999 180 180 130") if line.startswith("1 Монтажная") else line
        for line in _table_lines()
    ]

    assert _parse_vrs_lines(lines) == {}


def test_legend_above_the_table_is_not_read_as_a_row() -> None:
    # Под расшифровкой маркировки идёт строка « 1 2 3 4 5 6», нумерующая части
    # обозначения. Она выглядит как строка таблицы №1 и перехватывала её.
    lines = ["VALTEC VRS. 25 4. 130.0", "1 2 3 4 5 6", *_table_lines()]

    spec = _parse_vrs_lines(lines)

    assert spec["254.130"]["вес, кг"] == "2.3"


def _pump(sku: str, attributes: dict[str, str]) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный VALTEC RS {sku}",
        price=3989.0,
        currency="RUB",
        stock_status="в наличии",
        url="https://example.invalid/pump",
        attributes_normalized=attributes,
    )


def test_specification_is_attached_by_model_column() -> None:
    product = _pump("VRS.254.18.0", {})

    _attach_vrs_pump_specification(product, PASSPORT, _parse_vrs_lines(_table_lines()))

    assert product.attributes_normalized[
        "максимальный расход (скорость iii), м3/ч"
    ] == "2.94"


def test_feed_value_is_never_overwritten() -> None:
    product = _pump("VRS.254.18.0", {"вес, кг": "9.9"})

    _attach_vrs_pump_specification(product, PASSPORT, _parse_vrs_lines(_table_lines()))

    assert product.attributes_normalized["вес, кг"] == "9.9"


def test_real_passport_parses() -> None:
    spec = _parse_vrs_specification(PASSPORT)

    assert spec["256.180"]["максимальный расход (скорость iii), м3/ч"] == "3.3"
    assert spec["256.180"]["максимальный напор (скорость iii), м"] == "6"


def test_stated_nominal_head_matches_the_rated_maximum() -> None:
    # Паспорт: в маркировке «25/4» цифра 4 — номинал, каталог хранит
    # фактические 4,2. Покупатель называет цифру с шильдика.
    agent = FeedSearchAgent([])
    product = _pump("VRS.254.18.0", {"максимальный напор, м": "4.2"})

    assert agent._head_matches(product, 4.0) is True
    assert agent._head_matches(product, 4.2) is True


def test_neighbouring_head_series_stay_separated() -> None:
    agent = FeedSearchAgent([])
    product = _pump("VRS.256.18.0", {"максимальный напор, м": "6"})

    assert agent._head_matches(product, 4.0) is False
    assert agent._head_matches(product, 8.0) is False
    assert agent._head_matches(product, 6.0) is True
