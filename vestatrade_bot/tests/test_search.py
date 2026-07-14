from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def test_search_by_sku(sample_products: list[Product]) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(original_text="VT.228.N.04", sku="VT.228.N.04")
    )

    assert results[0].sku == "VT.228.N.04"


def test_search_by_name(sample_products: list[Product]) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(original_text="угловой кран 1/2", category="valves")
    )

    assert results
    assert results[0].sku == "VT.228.N.04"


def test_search_by_name_tolerates_typos() -> None:
    product = Product(
        sku="VALVE-20-ANGLE",
        name="Кран шаровый угловой для воды 20 мм",
        category_path="Краны шаровые",
        brand="VALTEC",
        url="https://example.test/valve20",
        price=500,
        stock_status="в наличии",
        stock_qty=7,
    )
    agent = FeedSearchAgent([product])

    results = agent.search_by_name(
        "Кран шаровыи углавой для вады 20 мм",
        SearchQuery(original_text="Кран шаровыи углавой для вады 20 мм"),
    )

    assert [result.sku for result in results] == ["VALVE-20-ANGLE"]


def test_valve_inch_filter_does_not_confuse_one_two_and_fractions() -> None:
    products = [
        Product(
            sku=f"VALVE-{size.replace(' ', '').replace('/', '-')}",
            name=f'Кран шаровой {size}&quot; вн.-вн.',
            category_path="Краны шаровые",
            url=f"https://example.test/valve-{index}",
            price=1000 + index,
            stock_status="в наличии",
            attributes_normalized={"диаметр подключения, дюйм": size},
        )
        for index, size in enumerate(["1/2", "3/4", "1", "1 1/4", "2"], start=1)
    ]
    agent = FeedSearchAgent(products)

    expected = {
        "1/2": "VALVE-1-2",
        "3/4": "VALVE-3-4",
        "1": "VALVE-1",
        "1 1/4": "VALVE-11-4",
        "2": "VALVE-2",
    }
    for size, sku in expected.items():
        results = agent.search(
            SearchQuery(
                original_text=f"кран {size} дюйма",
                category="valves",
                slots={"size_inch": size},
            )
        )
        assert [result.sku for result in results] == [sku]


def test_fitting_is_not_classified_as_pipe() -> None:
    fitting = Product(
        sku="VTp.751.0.025",
        name="Угольник 90 PPR 25мм",
        category_path="Фитинги/Фитинги полипропиленовые",
        brand="VALTEC",
        url="https://example.test/ugol",
        price=22,
        stock_status="в наличии",
        stock_qty=10,
    )
    pipe = Product(
        sku="VTp.700.0020.25",
        name="Труба PN 20, 25 MM (белый)",
        category_path="Трубы/Трубы полипропиленовые",
        brand="VALTEC",
        url="https://example.test/truba",
        price=182,
        stock_status="в наличии",
        stock_qty=10,
    )
    agent = FeedSearchAgent([fitting, pipe])

    assert agent.canonical_category(fitting) == "fittings"
    assert agent.canonical_category(pipe) == "pipes"
    # Запрос трубы не должен поднимать угольник как «трубу».
    retrieved = agent.retrieve_for_consult(["pipes"], {}, per_category=4)
    assert [p.sku for p in retrieved] == ["VTp.700.0020.25"]


def test_consult_retrieval_boilers_prefers_adequate_power() -> None:
    weak = Product(
        sku="ECA-6", name="Котел электрический Arceus 6 кВт", category_path="Акции",
        url="https://example.test/eca6", price=38010, stock_status="в наличии", stock_qty=1,
        attributes_normalized={"мощность, квт": "6"},
    )
    strong = Product(
        sku="SB32", name="Котел газовый Arderia SB32 32 кВт", category_path="Акции",
        url="https://example.test/sb32", price=38535, stock_status="в наличии", stock_qty=2,
        attributes_normalized={"мощность, квт": "32"},
    )
    agent = FeedSearchAgent([weak, strong])

    retrieved = agent.retrieve_for_consult(["boilers"], {"area_m2": 240}, per_category=4)
    # Для 240 м² (≈24 кВт) адекватный по мощности котёл идёт первым.
    assert retrieved[0].sku == "SB32"


def test_consult_retrieval_respects_boiler_contours() -> None:
    one_contour = Product(
        sku="SB24",
        name="Котел газовый Arderia SB24 одноконтурный 24 кВт",
        category_path="Котлы газовые",
        url="https://example.test/sb24",
        price=36000,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип котла": "Газовый", "контуры": "одноконтурный"},
    )
    two_contour = Product(
        sku="D24",
        name="Котел газовый Arderia D24 двухконтурный 24 кВт",
        category_path="Котлы газовые",
        url="https://example.test/d24",
        price=39000,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип котла": "Газовый", "контуры": "двухконтурный"},
    )
    agent = FeedSearchAgent([one_contour, two_contour])

    retrieved = agent.retrieve_for_consult(
        ["boilers"],
        {"boiler_type": "газовый", "contours": "двухконтурный"},
        per_category=4,
    )

    assert [product.sku for product in retrieved] == ["D24"]


def test_consult_retrieval_keeps_heating_pump_circulation_only() -> None:
    drainage = Product(
        sku="DRAIN-350",
        name="Дренажный насос 350 Вт",
        category_path="Насосы дренажные",
        url="https://example.test/drain",
        price=2500,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип товара": "Дренажный насос"},
    )
    circulation = Product(
        sku="CIRC-25-60",
        name="Насос циркуляционный 25-60 180 мм",
        category_path="Насосы циркуляционные",
        url="https://example.test/circ",
        price=6100,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип товара": "Циркуляционный насос"},
    )
    agent = FeedSearchAgent([drainage, circulation])

    retrieved = agent.retrieve_for_consult(
        ["pumps"],
        {"pump_type": "циркуляционный", "pump_use": "отопление"},
        per_category=4,
    )

    assert [product.sku for product in retrieved] == ["CIRC-25-60"]


def test_consult_retrieval_for_well_water_supply_skips_drainage_pump() -> None:
    drainage = Product(
        sku="DRAIN-350",
        name="Дренажный насос 350 Вт",
        category_path="Насосы дренажные",
        url="https://example.test/drain",
        price=2500,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип товара": "Дренажный насос"},
    )
    well = Product(
        sku="WELL-550",
        name="Винтовой скважинный насос 550 Вт",
        category_path="Насосы скважинные",
        url="https://example.test/well",
        price=9500,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип товара": "Скважинный насос"},
    )
    agent = FeedSearchAgent([drainage, well])

    retrieved = agent.retrieve_for_consult(
        ["pumps"],
        {"pump_type": "скважинный", "pump_use": "водоснабжение"},
        per_category=4,
    )

    assert [product.sku for product in retrieved] == ["WELL-550"]


def test_consult_retrieval_for_irrigation_prefers_drainage_and_skips_circulation() -> None:
    drainage = Product(
        sku="DRAIN-350",
        name="Дренажный насос 350 Вт",
        category_path="Насосы дренажные",
        url="https://example.test/drain",
        price=2500,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип товара": "Дренажный насос"},
    )
    well = Product(
        sku="WELL-550",
        name="Винтовой скважинный насос 550 Вт",
        category_path="Насосы скважинные",
        url="https://example.test/well",
        price=9500,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип товара": "Скважинный насос"},
    )
    circulation = Product(
        sku="CIRC-25-60",
        name="Насос циркуляционный 25-60 180 мм",
        category_path="Насосы циркуляционные",
        url="https://example.test/circ",
        price=6100,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип товара": "Циркуляционный насос"},
    )
    agent = FeedSearchAgent([circulation, well, drainage])

    retrieved = agent.retrieve_for_consult(
        ["pumps"],
        {"pump_use": "полив"},
        per_category=4,
    )

    assert [product.sku for product in retrieved] == ["DRAIN-350", "WELL-550"]


def test_consult_retrieval_sewer_project_can_prefer_pipe_over_cheaper_bend() -> None:
    bend = Product(
        sku="BEND-50",
        name="Отвод 87°, HTB, 50",
        category_path="Канализация внутренняя",
        url="https://example.test/bend",
        price=50,
        stock_status="в наличии",
        stock_qty=50,
        attributes_normalized={"тип товара": "Отвод"},
    )
    pipe = Product(
        sku="PIPE-50",
        name="Труба канализационная внутренняя 50x1500",
        category_path="Канализация внутренняя",
        url="https://example.test/pipe",
        price=280,
        stock_status="в наличии",
        stock_qty=20,
        attributes_normalized={"тип товара": "Труба"},
    )
    agent = FeedSearchAgent([bend, pipe])

    retrieved = agent.retrieve_for_consult(
        ["sewer"],
        {"element_type": "труба"},
        per_category=4,
    )

    assert [product.sku for product in retrieved] == ["PIPE-50"]


def test_cheap_sorting(sample_products: list[Product]) -> None:
    search = FeedSearchAgent(sample_products)
    products = search.search(
        SearchQuery(
            original_text="циркуляционный насос подешевле",
            category="pumps",
            slots={"pump_type": "циркуляционный"},
            cheap=True,
        )
    )
    ranked = RankingAgent().rank(products, SearchQuery(
        original_text="циркуляционный насос подешевле",
        category="pumps",
        cheap=True,
    ))

    assert [product.sku for product in ranked[:2]] == ["PUMP-25-40", "PUMP-25-60"]
