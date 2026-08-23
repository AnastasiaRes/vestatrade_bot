"""Регрессии QA-прогона 2026-08-22: подмена запрошенного параметра.

Каждый блок закрывает класс ошибок, а не одну формулировку: для всех правок
проверяется несколько живых перефразов, потому что подмена размера или ручки
воспроизводилась именно на переформулировке запроса.
"""

from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.utils import resolve_preferred_option
from app.models import Product


HANDLE_OPTIONS = (("бабоч", "butterfly"), ("рычаг|рукоят", "lever"))


@pytest.mark.parametrize(
    "message",
    [
        "А с рычагом вместо бабочки?",
        "Нужен такой же, но с рычагом, а не с бабочкой.",
        "не бабочку, а рычаг",
        "замени бабочку на рычаг",
        "бабочку не надо, дайте рычаг",
        "поменяй бабочку на рычаг",
        "без бабочки",
    ],
)
def test_rejected_handle_never_wins(message: str) -> None:
    """Названное «чтобы отказаться» значение не должно становиться требованием."""
    assert resolve_preferred_option(message, HANDLE_OPTIONS) == "lever"


@pytest.mark.parametrize(
    "message",
    ["дайте с бабочкой", "кран 1/2 бабочка", "поменяй рычаг на бабочку"],
)
def test_requested_handle_is_kept(message: str) -> None:
    assert resolve_preferred_option(message, HANDLE_OPTIONS) == "butterfly"


@pytest.mark.parametrize(
    "message",
    [
        "Покажи кран 3/4 вн-вн для воды с рычагом вместо бабочки",
        "нужен шаровой кран 3/4 вн-вн, рычаг, а не бабочка",
    ],
)
def test_router_keeps_requested_handle(message: str) -> None:
    slots = IntentRouterAgent().route(message).slots
    assert slots.get("handle_type") == "lever"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Нужен шаровой кран 1 1/2 вн-вн для воды", "11/2"),
        ('Нужен шаровой кран 1 1/2" вн-вн', "11/2"),
        ("Нужен шаровой кран 1½ вн-вн", "11/2"),
        ("Нужен шаровой кран 1-1/2 вн-вн", "11/2"),
        ("Нужен шаровой кран полтора дюйма вн-вн", "11/2"),
        ("Нужен кран 3/4 вн-вн", "3/4"),
        ("Нужен кран полдюйма", "1/2"),
    ],
)
def test_composite_inch_sizes_are_not_truncated(message: str, expected: str) -> None:
    """«1 1/2» не должно усекаться до «1/2» — это размер втрое меньше."""
    assert IntentRouterAgent().route(message).slots.get("size_inch") == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Радиатор панельный Royal Thermo VENTIL COMPACT VC22-500-900 RAL9016", (500, 900)),
        ("Радиатор стальной панельный AXIS 22 500 x 1000 Ventil", (500, 1000)),
        ("Радиатор 11/500/1000 стальной панельный нижнее подключение Ventil ROMMER", (500, 1000)),
        ("Радиатор KERMI FK O тип 22 высота 300 длина 900", (300, 900)),
        ("Радиатор стальной панельный Gekon Ventil Compact CV 22-500-1000", (500, 1000)),
    ],
)
def test_radiator_dimensions_are_read_from_name(name: str, expected: tuple) -> None:
    """Габариты панельных радиаторов есть только в названии, не в <param>."""
    assert FeedSearchAgent._radiator_dimensions_from_name(name) == expected


def _radiator(sku: str, name: str) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Радиаторы отопления",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=7000.0,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип": "22", "тип подключения": "Нижнее"},
    )


def test_radiator_length_filters_out_neighbouring_size() -> None:
    """Длина 1000 не должна проходить у радиатора длиной 900."""
    agent = FeedSearchAgent()
    exact = _radiator("EXACT", "Радиатор стальной панельный AXIS 22 500 x 1000 Ventil")
    neighbour = _radiator("NEAR", "Радиатор панельный VC22-500-900 RAL9016")

    assert agent._dimension_matches(exact, 1000, ["длина"]) is True
    assert agent._dimension_matches(neighbour, 1000, ["длина"]) is False


def test_radiator_height_is_not_matched_by_length_number() -> None:
    """У «22 300 x 500» высота 300, а не 500: свободный поиск числа запрещён."""
    agent = FeedSearchAgent()
    product = _radiator("AXIS", "Радиатор стальной панельный AXIS 22 300 x 500 Ventil")

    assert agent._dimension_matches(product, 300, ["высот"]) is True
    assert agent._dimension_matches(product, 500, ["высот"]) is False


@pytest.mark.parametrize(
    "message",
    [
        "Нужен стальной панельный радиатор тип 22, высота 500, длина 1000, нижнее подключение",
        "Нужен панельный радиатор 22 типа 500 на 1000, нижнее подключение",
        "радиатор стальной 22 тип 500х1000 нижнее",
    ],
)
def test_radiator_dimensions_and_type_survive_paraphrase(message: str) -> None:
    slots = IntentRouterAgent().route(message).slots
    assert slots.get("radiator_panel_type") == 22
    assert slots.get("radiator_height_mm") == 500
    assert slots.get("length_mm") == 1000


def test_interaxial_distance_is_not_invented_from_height() -> None:
    """Правка габаритов не должна воскрешать смешение высоты и межосевого."""
    slots = IntentRouterAgent().route("панельный радиатор тип 22, высота 500 мм").slots
    assert slots.get("radiator_height_mm") == 500
    assert "radiator_size_mm" not in slots


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Найди артикул 65/54", "65/54"),
        ("Найди артикул 65/2", "65/2"),
        ("арт. 65/54", "65/54"),
        ("Найди артикул 68/2/8", "68/2/8"),
    ],
)
def test_numeric_slash_articles_are_recognised(message: str, expected: str) -> None:
    """«65/54» — реальный артикул в фиде, а не дюймовый размер."""
    assert IntentRouterAgent().route(message).slots.get("sku") == expected


@pytest.mark.parametrize("fraction", ["1/2", "3/4", "5/8", "1/16", "3/8"])
def test_inch_fractions_are_never_treated_as_articles(fraction: str) -> None:
    assert IntentRouterAgent._is_valid_explicit_sku_candidate(fraction) is False


def test_inch_size_request_is_not_hijacked_by_article_rule() -> None:
    slots = IntentRouterAgent().route("Нужен кран 1/2 вн-вн для воды").slots
    assert slots.get("size_inch") == "1/2"
    assert "sku" not in slots


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Тип резьбы: Внутренняя", {"thread_gender": "female"}),
        ("Тип резьбы: Наружная", {"thread_gender": "male"}),
        ("Тип резьбы: С внутренней резьбой (ff)", {"thread_type": "ff"}),
        ("Диаметр подключения, дюйм: 1 1/2", {"size_inch": "11/2"}),
        ("Межосевое расстояние, мм: 346", {"radiator_size_mm": 346}),
        ("Тип ручки: Бабочка", {"handle_type": "butterfly"}),
        ("Угол (градусы): 45", {"angle_deg": 45}),
        ("Диаметр (мм): 25", {"diameter_mm": 25}),
    ],
)
def test_spec_lines_become_constraints(message: str, expected: dict) -> None:
    """Покупатели копируют строки характеристик с сайта — это тоже требование."""
    assert IntentRouterAgent._slots_from_spec_lines(message) == expected


@pytest.mark.parametrize(
    "message",
    ["Привет! Мне нужно: кран", "Нужен кран 1/2 вн-вн для воды", "Вопрос: что лучше?"],
)
def test_ordinary_speech_does_not_create_spec_constraints(message: str) -> None:
    assert IntentRouterAgent._slots_from_spec_lines(message) == {}
