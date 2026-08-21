from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.product_constraints import (
    normalize_thread_pair,
    product_thread_facts,
)
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def _product(
    sku: str,
    name: str,
    *,
    category: str = "Фитинги",
    attributes: dict[str, str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="TEST",
        url=f"https://example.test/{sku}",
        price=500,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized=attributes or {},
    )


@pytest.mark.parametrize(
    ("notation", "expected"),
    [
        ("ВР/ВР", "ff"),
        ("вн.-нар.", "fm"),
        ("мама-папа", "fm"),
        ("С внутренней наружной резьбой (fm)", "fm"),
        ("НР-НР", "mm"),
    ],
)
def test_shared_thread_parser_normalizes_real_notation(
    notation: str,
    expected: str,
) -> None:
    assert normalize_thread_pair(notation) == expected


def test_single_threaded_ppr_end_is_not_invented_as_a_two_end_pair() -> None:
    fitting = _product(
        "VTp.753.0.02004",
        'Угольник PPR с переходом на нар. р. 20х1/2"',
        attributes={
            "материал": "Полипропилен, Латунь",
            "тип товара": "Угольник",
            "тип присоединения": "Под сварку",
            "тип резьбы": "Наружная",
            "присоединительная резьба, дюйм": "1/2",
            "диаметр (мм)": "20",
            "угол (градусы)": "90",
        },
    )

    facts = product_thread_facts(fitting)

    assert facts.pair is None
    assert facts.genders == frozenset({"male"})


def test_ppr_socket_geometry_is_not_mistaken_for_a_thread_pair() -> None:
    socket_elbow = _product(
        "PA13608P",
        "Уголок PPRC 20 вн/нар. Pro Aqua",
        attributes={
            "материал": "Полипропилен",
            "тип товара": "Угольник",
            "тип присоединения": "Под сварку",
            "диаметр (мм)": "20",
            "угол (градусы)": "90",
        },
    )

    facts = product_thread_facts(socket_elbow)

    assert facts.pair is None
    assert facts.genders == frozenset()


def test_explicit_thread_pair_is_a_hard_filter_and_no_longer_crashes() -> None:
    products = [
        _product(
            "FF",
            'Кран шаровой 1/2" ВР/ВР ручка рычаг',
            category="Краны шаровые",
            attributes={
                "тип товара": "Кран шаровой",
                "тип резьбы": "С внутренней резьбой (ff)",
                "тип ручки": "Рычаг",
                "диаметр подключения, дюйм": "1/2",
            },
        ),
        _product(
            "FM",
            'Кран шаровой 1/2" ВР/НР ручка рычаг',
            category="Краны шаровые",
            attributes={
                "тип товара": "Кран шаровой",
                "тип резьбы": "Внутренняя-наружная",
                "тип ручки": "Рычаг",
                "диаметр подключения, дюйм": "1/2",
            },
        ),
        _product(
            "UNKNOWN",
            'Кран шаровой 1/2" ручка рычаг',
            category="Краны шаровые",
            attributes={
                "тип товара": "Кран шаровой",
                "тип ручки": "Рычаг",
                "диаметр подключения, дюйм": "1/2",
            },
        ),
    ]
    query = SearchQuery(
        original_text='кран шаровой 1/2 ВР-НР',
        category="valves",
        slots={"size_inch": "1/2", "thread_type": "fm"},
    )

    results = FeedSearchAgent(products).search(query)

    assert [product.sku for product in results] == ["FM"]
    assert RankingAgent()._thread_code(products[1]) == "fm"


def test_ppr_connection_size_gender_and_angle_are_all_hard_constraints() -> None:
    common = {
        "материал": "Полипропилен, Латунь",
        "тип товара": "Угольник",
        "тип присоединения": "Под сварку",
        "присоединительная резьба, дюйм": "1/2",
        "диаметр (мм)": "20",
    }
    products = [
        _product(
            "PPR-M-90",
            'Угольник PPR с переходом на нар. р. 20х1/2"',
            attributes={**common, "тип резьбы": "Наружная", "угол (градусы)": "90"},
        ),
        _product(
            "PPR-F-90",
            'Угольник PPR с переходом на вн. р. 20х1/2"',
            attributes={**common, "тип резьбы": "Внутренняя", "угол (градусы)": "90"},
        ),
        _product(
            "PPR-M-45",
            'Угольник PPR 45 с переходом на нар. р. 20х1/2"',
            attributes={**common, "тип резьбы": "Наружная", "угол (градусы)": "45"},
        ),
        _product(
            "PRESS-M-90",
            'Угольник пресс с наружной резьбой 20х1/2"',
            attributes={
                **common,
                "материал": "Нержавеющая сталь",
                "тип присоединения": "Пресс",
                "тип резьбы": "Наружная",
                "угол (градусы)": "90",
            },
        ),
    ]
    query = SearchQuery(
        original_text='PPR угол 20x1/2 НР 90 градусов',
        category="fittings",
        slots={
            "fitting_system": "ppr",
            "diameter_mm": 20,
            "size_inch": "1/2",
            "thread_gender": "male",
            "angle_deg": 90,
            "product_kind": "elbow",
        },
    )

    assert [product.sku for product in FeedSearchAgent(products).search(query)] == [
        "PPR-M-90"
    ]


def test_missing_exact_ppr_combination_returns_no_match() -> None:
    only_90 = _product(
        "VTp.753.0.02004",
        'Угольник PPR с переходом на нар. р. 20х1/2"',
        attributes={
            "материал": "Полипропилен, Латунь",
            "тип товара": "Угольник",
            "тип присоединения": "Под сварку",
            "тип резьбы": "Наружная",
            "присоединительная резьба, дюйм": "1/2",
            "диаметр (мм)": "20",
            "угол (градусы)": "90",
        },
    )
    query = SearchQuery(
        original_text='PPR угол 20x1/2 НР 45 градусов',
        category="fittings",
        slots={
            "fitting_system": "ppr",
            "diameter_mm": 20,
            "size_inch": "1/2",
            "thread_gender": "male",
            "angle_deg": 45,
            "product_kind": "elbow",
        },
    )
    agent = FeedSearchAgent([only_90])

    assert agent.search(query) == []
    assert agent.search_alternatives(query) == []


def test_raw_dialog_preserves_ppr_elbow_angle_until_retrieval() -> None:
    common = {
        "материал": "Полипропилен, Латунь",
        "тип товара": "Угольник",
        "тип присоединения": "Под сварку",
        "тип резьбы": "Наружная",
        "присоединительная резьба, дюйм": "1/2",
        "диаметр (мм)": "20",
    }
    elbow_45 = _product(
        "PPR-M-45",
        'Угольник PPR 45° с переходом на нар. р. 20х1/2"',
        attributes={**common, "угол (градусы)": "45"},
    )
    elbow_90 = _product(
        "PPR-M-90",
        'Угольник PPR 90° с переходом на нар. р. 20х1/2"',
        attributes={**common, "угол (градусы)": "90"},
    )
    bot = ChatOrchestrator(products=[elbow_45, elbow_90])

    response = bot.handle_chat(
        "raw-ppr-angle",
        "PPR угол 20×1/2 НР, строго 45 градусов",
    )

    assert response.debug["slots"]["angle_deg"] == 45
    assert [product.sku for product in response.products] == ["PPR-M-45"]


def test_handle_bore_and_body_form_are_hard_constraints() -> None:
    base_attributes = {
        "тип товара": "Кран шаровой",
        "тип резьбы": "Внутренняя-наружная",
        "диаметр подключения, дюйм": "3/4",
    }
    matching = _product(
        "MATCH",
        'Кран шаровой полнопроходной прямой 3/4" ВР/НР, ручка бабочка',
        category="Краны шаровые",
        attributes={
            **base_attributes,
            "тип ручки": "Бабочка",
            "форма корпуса": "Прямой",
        },
    )
    neighbours = [
        _product(
            "LEVER",
            'Кран шаровой полнопроходной прямой 3/4" ВР/НР, ручка рычаг',
            category="Краны шаровые",
            attributes={**base_attributes, "тип ручки": "Рычаг", "форма корпуса": "Прямой"},
        ),
        _product(
            "REDUCED",
            'Кран шаровой стандартнопроходной прямой 3/4" ВР/НР, ручка бабочка',
            category="Краны шаровые",
            attributes={**base_attributes, "тип ручки": "Бабочка", "форма корпуса": "Прямой"},
        ),
        _product(
            "ANGLED",
            'Кран шаровой полнопроходной угловой 3/4" ВР/НР, ручка бабочка',
            category="Краны шаровые",
            attributes={**base_attributes, "тип ручки": "Бабочка", "форма корпуса": "Угловой"},
        ),
    ]
    query = SearchQuery(
        original_text='прямой полнопроходной кран 3/4 ВР-НР с бабочкой',
        category="valves",
        slots={
            "size_inch": "3/4",
            "thread_type": "fm",
            "handle_type": "butterfly",
            "full_bore": True,
            "body_form": "straight",
            "product_kind": "ball_valve",
        },
    )

    assert [
        product.sku
        for product in FeedSearchAgent([*neighbours, matching]).search(query)
    ] == ["MATCH"]


def test_product_kind_separates_head_valve_and_radiator_body() -> None:
    head = _product(
        "HEAD",
        "Термостатическая головка для радиатора",
        category="Радиаторная арматура",
        attributes={"тип товара": "Термостатическая головка"},
    )
    valve = _product(
        "VALVE",
        'Клапан термостатический радиаторный прямой 1/2"',
        category="Радиаторная арматура",
        attributes={"тип товара": "Клапан термостатический"},
    )
    valve_with_head = _product(
        "VALVE-WITH-HEAD",
        'Клапан с термостатической головкой для радиатора 1/2"',
        category="Радиаторная арматура",
        attributes={"тип товара": "Клапан термостатический"},
    )
    radiator = _product(
        "RADIATOR",
        "Радиатор стальной панельный",
        category="Радиаторы отопления",
        attributes={"тип товара": "Радиатор"},
    )

    results = FeedSearchAgent([head, valve, valve_with_head, radiator]).search(
        SearchQuery(
            original_text="термоголовка для радиатора",
            category="radiator_fittings",
            slots={"product_kind": "thermostatic_head"},
        )
    )

    assert [product.sku for product in results] == ["HEAD"]


def test_exact_sku_does_not_bypass_explicit_constraint_mismatch() -> None:
    lever = _product(
        "VALVE-LEVER",
        'Кран шаровой полнопроходной 1/2" ВР/НР, ручка рычаг',
        category="Краны шаровые",
        attributes={
            "тип товара": "Кран шаровой",
            "тип резьбы": "Внутренняя-наружная",
            "тип ручки": "Рычаг",
            "диаметр подключения, дюйм": "1/2",
        },
    )

    result = FeedSearchAgent([lever]).search(
        SearchQuery(
            original_text="VALVE-LEVER, но нужна бабочка",
            category="valves",
            sku="VALVE-LEVER",
            slots={"handle_type": "butterfly"},
        )
    )

    assert result == []
