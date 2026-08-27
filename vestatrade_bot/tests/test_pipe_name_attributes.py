"""Разбор параметров трубы из названия.

Ошибка здесь дороже пропуска: неизвлечённый параметр оставляет поведение
прежним, а извлечённый неверно уходит покупателю как подтверждённый факт.
Поэтому тесты фиксируют не только то, что разбирается, но и то, что
намеренно не разбирается.
"""

from __future__ import annotations

import pytest

from app.agents.pipe_name_attributes import extract_pipe_attributes, is_pipe_name
from app.feed_loader import FeedLoader
from app.models import Product


def test_pressure_class_and_reinforcement_come_from_the_name() -> None:
    attributes = extract_pipe_attributes(
        "Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)"
    )

    assert attributes["класс давления pn"] == "20"
    assert attributes["диаметр (мм)"] == "25"
    assert attributes["основной материал"] == "PP-FIBER"
    assert attributes["армирование"] == "стекловолокно"


def test_aluminium_reinforcement_is_distinguished_from_fibre() -> None:
    attributes = extract_pipe_attributes(
        "Труба PP-ALUX, арм. алюминием, PN 25, 25 MM (белый)"
    )

    assert attributes["основной материал"] == "PP-ALUX"
    assert attributes["армирование"] == "алюминий"


def test_wall_thickness_is_read_from_a_dimension_pair() -> None:
    attributes = extract_pipe_attributes(
        "Труба РОСТерм неармированная PN 25 (SDR 6) белый 110ммх18,3мм 4м"
    )

    assert attributes["диаметр (мм)"] == "110"
    assert attributes["толщина стенки (мм)"] == "18.3"
    assert attributes["sdr"] == "6"
    assert attributes["армирование"] == "нет"


def test_segment_length_is_never_read_as_wall_thickness() -> None:
    # «110*1500» у канализационной трубы — диаметр и длина отрезка. Записать
    # 1500 как толщину стенки значит выдать бессмыслицу за паспортный факт.
    attributes = extract_pipe_attributes("Труба канализационная, HTEM, 110*1500")

    assert attributes["диаметр (мм)"] == "110"
    assert "толщина стенки (мм)" not in attributes


def test_coil_length_is_captured_in_metres() -> None:
    attributes = extract_pipe_attributes(
        "Труба из сшитого полиэтилена ROMMER 16х2,0 (бухта 500 метров) PE-Xa с "
        "кислородным слоем"
    )

    assert attributes["длина бухты (м)"] == "500"
    assert attributes["кислородный барьер"] == "есть"
    assert attributes["основной материал"] == "PE-Xa"


def test_pipe_word_is_recognised_after_a_brand_and_size() -> None:
    attributes = extract_pipe_attributes(
        "STOUT 20х2,9 (бухта 100 метров) труба стабильная PE-Xa/Al/PE-RT, серая"
    )

    assert attributes["диаметр (мм)"] == "20"
    assert attributes["толщина стенки (мм)"] == "2.9"


@pytest.mark.parametrize(
    "name",
    [
        "Пресс-угольник 45° из нержавеющей стали Kromwell раструб-труба 15х15",
        "Коллектор из стали (труба ДУ-40), с м-о расст вых. 100мм, 1\"х 3 вых.",
        "Расширительные насадки для инструмента PEXcase/PexTool (стабильная труба)",
        "Евроконус для м/п трубы 20(2,0)",
        "Калибр для м/п трубы 16-20-26, с ножами для снятия фаски",
        "Высокоточный труборез KRAFTOOL до 42 мм",
    ],
)
def test_accessories_and_fittings_are_not_parsed_as_pipes(name: str) -> None:
    # Родительный падеж «для ... трубы» означает принадлежность к трубе, то
    # есть фитинг или оснастку. Приписать им диаметр и класс давления трубы
    # значит подставить покупателю чужой параметр.
    assert is_pipe_name(name) is False
    assert extract_pipe_attributes(name) == {}


def test_inch_sizes_are_left_alone() -> None:
    attributes = extract_pipe_attributes('Гибкая труба Ани 1 1/4"*40/50')

    assert "толщина стенки (мм)" not in attributes


def _pipe_product(name: str, attributes: dict[str, str]) -> Product:
    return Product(
        sku="TEST.1",
        name=name,
        price=100.0,
        currency="RUB",
        stock_status="в наличии",
        url="https://example.invalid/pipe",
        attributes_normalized=attributes,
    )


def test_feed_loader_adds_derived_attributes() -> None:
    product = _pipe_product(
        "Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        {"артикул": "TEST.1"},
    )

    enriched = FeedLoader._derive_pipe_attributes(product)

    assert enriched.attributes_normalized["класс давления pn"] == "20"
    assert enriched.attributes_normalized["артикул"] == "TEST.1"


def test_feed_value_wins_over_the_parsed_name() -> None:
    # Фид — источник, разбор названия — догадка. Расхождение разрешается в
    # пользу поставщика, иначе опечатка в названии перепишет реальный размер.
    product = _pipe_product(
        "Труба PP-FIBER PN 20, 25 MM",
        {"диаметр (мм)": "32"},
    )

    enriched = FeedLoader._derive_pipe_attributes(product)

    assert enriched.attributes_normalized["диаметр (мм)"] == "32"


def test_non_pipe_products_are_untouched() -> None:
    product = Product(
        sku="VT.214.N.04",
        name='Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн.',
        price=503.0,
        currency="RUB",
        stock_status="в наличии",
        url="https://example.invalid/valve",
        attributes_normalized={},
    )

    assert FeedLoader._derive_pipe_attributes(product) is product
