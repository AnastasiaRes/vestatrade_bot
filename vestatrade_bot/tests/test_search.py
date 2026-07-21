from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.ranking import RankingAgent
from app.models import Product, SearchQuery


def test_search_by_sku(sample_products: list[Product]) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(original_text="VT.228.N.04", sku="VT.228.N.04")
    )

    assert results[0].sku == "VT.228.N.04"


def test_missing_exact_sku_never_falls_through_to_another_product(
    sample_products: list[Product],
) -> None:
    results = FeedSearchAgent(sample_products).search(
        SearchQuery(
            original_text="MISSING-999",
            category="boilers",
            sku="MISSING-999",
        )
    )

    assert results == []


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


def test_valve_inch_filter_supports_large_and_mixed_sizes() -> None:
    products = [
        Product(
            sku="VALVE-1-1-2",
            name='Кран шаровой 1"1/2 вн.-нар.',
            category_path="Водозапорная арматура",
            url="https://example.test/valve-mixed",
            price=1000,
            stock_status="в наличии",
            attributes_normalized={"диаметр подключения, дюйм": "1 1/2"},
        ),
        Product(
            sku="VALVE-3",
            name='Кран шаровой 3" вн.-вн.',
            category_path="Водозапорная арматура",
            url="https://example.test/valve-3",
            price=2000,
            stock_status="в наличии",
            attributes_normalized={"диаметр подключения, дюйм": "3"},
        ),
    ]
    agent = FeedSearchAgent(products)

    mixed = agent.search(
        SearchQuery(
            original_text="кран 1 1/2",
            category="valves",
            slots={"size_inch": "1 1/2"},
        )
    )
    large = agent.search(
        SearchQuery(
            original_text="кран 3 дюйма",
            category="valves",
            slots={"size_inch": "3"},
        )
    )

    assert [product.sku for product in mixed] == ["VALVE-1-1-2"]
    assert [product.sku for product in large] == ["VALVE-3"]


def test_decimal_comma_pump_head_matches_decimal_query() -> None:
    pump = Product(
        sku="PUMP-6-5",
        name="Насос циркуляционный 25/60-180",
        category_path="Насосное оборудование",
        url="https://example.test/pump-6-5",
        price=5000,
        stock_status="в наличии",
        attributes_normalized={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "максимальный напор, м": "6,5",
        },
    )

    results = FeedSearchAgent([pump]).search(
        SearchQuery(
            original_text="насос напор 6,5 м",
            category="pumps",
            slots={"pump_type": "циркуляционный", "head_m": 6.5},
        )
    )

    assert [product.sku for product in results] == ["PUMP-6-5"]


def test_ht_sewer_fitting_is_not_put_in_generic_fittings_bucket() -> None:
    fitting = Product(
        sku="HTU-32",
        name='Муфта надвижная HTU 32"20',
        category_path="Акционные товары",
        url="https://example.test/htu-32",
        price=100,
        stock_status="в наличии",
        attributes_normalized={"тип товара": "Муфта"},
    )

    assert FeedSearchAgent([fitting]).canonical_category(fitting) == "sewer"


def test_electric_boiler_voltage_is_a_hard_filter() -> None:
    products = [
        Product(
            sku=f"E-{voltage}",
            name=f"Котёл электрический 9 кВт {voltage} В",
            category_path="Котлы электрические",
            url=f"https://example.test/e-{voltage}",
            price=30000,
            stock_status="в наличии",
            attributes_normalized={
                "тип котла": "Электрический",
                "напряжение": str(voltage),
            },
        )
        for voltage in (220, 380)
    ]

    results = FeedSearchAgent(products).search(
        SearchQuery(
            original_text="электрический котёл 380",
            category="boilers",
            slots={"boiler_type": "электрический", "voltage_v": 380},
        )
    )

    assert [product.sku for product in results] == ["E-380"]


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


def test_boiler_power_is_read_when_unit_is_in_attribute_key() -> None:
    weak = Product(
        sku="SOLO-3",
        name="Котёл электрический ZOTA Solo - 3",
        category_path="Котлы электрические",
        url="https://example.test/solo3",
        price=25000,
        stock_status="в наличии",
        attributes_normalized={"мощность, квт": "3"},
    )
    adequate = Product(
        sku="SOLO-12",
        name="Котёл электрический ZOTA Solo - 12",
        category_path="Котлы электрические",
        url="https://example.test/solo12",
        price=32000,
        stock_status="в наличии",
        attributes_normalized={"мощность, квт": "12"},
    )
    agent = FeedSearchAgent([weak, adequate])

    assert agent._extract_power_kw(weak) == 3
    retrieved = agent.retrieve_for_consult(
        ["boilers"],
        {"area_m2": 95},
        per_category=2,
    )
    assert retrieved[0].sku == "SOLO-12"


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


def test_broad_feed_sections_do_not_turn_accessories_into_equipment() -> None:
    products = [
        Product(
            sku="2702213",
            name="Датчик NTC для бойлера Arderia, 10 кОм",
            category_path="Котельное оборудование",
            url="https://example.test/2702213",
            price=1000,
            attributes_normalized={"тип товара": "Датчик NTC"},
        ),
        Product(
            sku="RCA-6010-001000",
            name="Удлинитель дымохода коакс. 60/100 L 1000",
            category_path="Котельное оборудование",
            url="https://example.test/chimney",
            price=2000,
            attributes_normalized={"тип товара": "Дымоход"},
        ),
        Product(
            sku="2201375",
            name="Котел газовый Arderia SB24 одноконтурный",
            category_path="Акционные товары",
            url="https://example.test/boiler",
            price=35000,
            attributes_normalized={"тип товара": "Котёл"},
        ),
        Product(
            sku="24432",
            name="Трос из нерж. стали 30м для крепления насоса",
            category_path="Насосное оборудование",
            url="https://example.test/rope",
            price=1500,
            attributes_normalized={"тип товара": "Трос"},
        ),
        Product(
            sku="11677",
            name="Винтовой скважинный насос Unipump ECO VINT 2",
            category_path="Насосное оборудование",
            url="https://example.test/pump",
            price=9000,
            attributes_normalized={"тип товара": "Насос"},
        ),
        Product(
            sku="SK 40025с",
            name="Кожух для трубы 16 (диаметр 25) синий",
            category_path="Трубы",
            url="https://example.test/sleeve",
            price=20,
        ),
        Product(
            sku="VTp.700.0020.25",
            name="Труба PN 20, 25 MM (белый)",
            category_path="Трубы",
            url="https://example.test/pipe",
            price=180,
        ),
        Product(
            sku="VT.514.C.04",
            name="Чашка декоративная (хромированная)",
            category_path="Водозапорная арматура",
            url="https://example.test/cup",
            price=100,
            attributes_normalized={"тип товара": "Чашка"},
        ),
    ]
    agent = FeedSearchAgent(products)

    assert [p.sku for p in agent.retrieve_for_consult(["boilers"], {}, 10)] == ["2201375"]
    assert [
        p.sku
        for p in agent.retrieve_for_consult(
            ["pumps"], {"pump_type": "скважинный", "pump_use": "водоснабжение"}, 10
        )
    ] == ["11677"]
    assert agent.canonical_category(products[5]) == "other"
    assert agent.canonical_category(products[6]) == "pipes"
    assert agent.canonical_category(products[7]) == "other"


def test_pipe_purpose_and_temperature_are_hard_filters_for_report_skus() -> None:
    hot_ppr = Product(
        sku="STR025P20X",
        name="Труба ППР PN20 для систем ХВС и ГВС 25x4,2 Ekoplastik",
        category_path="Трубы",
        url="https://example.test/hot-ppr",
        price=110,
        description="Применяется для горячего и холодного водоснабжения и отопления.",
    )
    cold_only = Product(
        sku="68046",
        name="Труба напорн. для хол/водосн. Unipump ПЭ100 25х2,0",
        category_path="Трубы",
        url="https://example.test/cold",
        price=55,
        attributes_normalized={"назначение": "Холодное водоснабжение"},
        # A conflicting marketing paragraph must not override the structured fact.
        description="Ошибочный текст другого SKU: горячее водоснабжение и отопление.",
    )
    sleeve = Product(
        sku="25CВ/25",
        name="Кожух гофрированный ПНД 25 мм синий (под 16 трубу)",
        category_path="Трубы",
        url="https://example.test/sleeve25",
        price=20,
        attributes_normalized={"диаметр (мм)": "25"},
        description="Защитный кожух для систем горячего водоснабжения.",
    )
    heating = Product(
        sku="HEAT-32",
        name="Труба отопит. 32х4,4 мм",
        category_path="Трубы",
        url="https://example.test/heat32",
        price=500,
        description="Труба для систем отопления.",
    )
    heating_wrong = Product(
        sku="45375",
        name="Труба напорн. для хол/водосн. Unipump ПЭ100 32х3,0",
        category_path="Трубы",
        url="https://example.test/cold32",
        price=70,
        description="Только холодное водоснабжение до +40 °С.",
    )
    agent = FeedSearchAgent([cold_only, sleeve, hot_ppr, heating_wrong, heating])

    hot_results = agent.search(
        SearchQuery(
            original_text="PPR для горячей воды 25 мм",
            category="pipes",
            slots={
                "pipe_purpose": "водоснабжение",
                "water_temperature": "горячая",
                "diameter_mm": 25,
            },
        )
    )
    heating_results = agent.search(
        SearchQuery(
            original_text="труба для отопления 32 мм",
            category="pipes",
            slots={"pipe_purpose": "отопление", "diameter_mm": 32},
        )
    )

    assert [product.sku for product in hot_results] == ["STR025P20X"]
    assert [product.sku for product in heating_results] == ["HEAT-32"]


def test_boiler_type_and_contours_ignore_conflicting_marketing_description() -> None:
    conflicting = Product(
        sku="CMSR02CA28",
        name="Котел газовый настенный двухконтурный Fondital MAIORCA CTFS 28",
        category_path="Котельное оборудование",
        url="https://example.test/cmsr02ca28",
        price=86250,
        attributes_normalized={
            "тип товара": "Котёл",
            "тип котла": "Газовый",
            "количество контуров": "Двухконтурный",
        },
        description="Другой SKU CMSR02RF28: газовый одноконтурный котёл.",
    )
    one_contour = Product(
        sku="2201375",
        name="Котел газовый Arderia SB24 одноконтурный",
        category_path="Акционные товары",
        url="https://example.test/sb24",
        price=35869,
        attributes_normalized={
            "тип товара": "Котёл",
            "тип котла": "Газовый",
            "количество контуров": "Одноконтурный",
        },
    )
    electric_with_gas_in_description = Product(
        sku="2202210",
        name="Котел электрический Arderia E9 одноконтурный",
        category_path="Котельное оборудование",
        url="https://example.test/e9",
        price=35365,
        attributes_normalized={"тип котла": "Электрический", "количество контуров": "Одноконтурный"},
        description="Для сравнения в тексте упомянут газовый котёл.",
    )
    agent = FeedSearchAgent([conflicting, electric_with_gas_in_description, one_contour])
    one_query = SearchQuery(
        original_text="газовый одноконтурный котел",
        category="boilers",
        slots={"boiler_type": "газовый", "contours": "одноконтурный"},
    )

    assert [product.sku for product in agent.search(one_query)] == ["2201375"]
    assert [
        product.sku
        for product in agent.retrieve_for_consult(
            ["boilers"], {"boiler_type": "газовый", "contours": "одноконтурный"}, 10
        )
    ] == ["2201375"]
    assert [
        product.sku
        for product in agent.search(
            SearchQuery(
                original_text="газовый двухконтурный котел",
                category="boilers",
                slots={"boiler_type": "газовый", "contours": "двухконтурный"},
            )
        )
    ] == ["CMSR02CA28"]


def test_application_union_and_radiator_controls_are_hard_filters() -> None:
    heating_valve = Product(
        sku="PA41010",
        name="Кран шаровой для рад. с амер. прямой 25-3/4\"",
        category_path="Акционные товары",
        url="https://example.test/pa41010",
        price=299,
        attributes_normalized={
            "назначение": "Отопление",
            "тип товара": "Кран шаровый",
            "присоединительная резьба, дюйм": "3/4",
            "тип конструкции": "Радиаторный, Прямой, С американкой",
        },
    )
    water_valve = Product(
        sku="LD 47.346.20",
        name="Кран шаровой 3/4\" с накидной гайкой",
        category_path="Водозапорная арматура",
        url="https://example.test/ld",
        price=764,
        attributes_normalized={
            "рабочая среда": "Для воды",
            "диаметр подключения, дюйм": "3/4",
        },
    )
    no_union = Product(
        sku="SVF 0001 000020",
        name="Кран шаровой с фильтром 3/4\"",
        category_path="Водозапорная арматура",
        url="https://example.test/filter-valve",
        price=500,
        attributes_normalized={"рабочая среда": "Для воды", "диаметр подключения, дюйм": "3/4"},
        description="Наличие американки: нет.",
    )
    thermostat = Product(
        sku="VT.031.N.04",
        name="Клапан термостатический для рад. угловой 1/2\"",
        category_path="Арматура для радиаторов",
        url="https://example.test/thermostat",
        price=1200,
    )
    manual_ppr = Product(
        sku="VTp.718.0.02004",
        name="Клапан PPR для подключения радиатора угловой 20х1/2\"",
        category_path="Фитинги",
        url="https://example.test/manual-ppr",
        price=608,
        attributes_normalized={"назначение": "Отопление", "тип товара": "Кран шаровый", "присоединительная резьба, дюйм": "1/2", "тип конструкции": "Радиаторный, Угловой"},
    )
    convector = Product(
        sku="RT-A-75/300/800-DG-U-NA",
        name="Конвектор внутрипольный Royal Thermo ATRIUM",
        category_path="Радиаторы отопления",
        url="https://example.test/convector",
        price=12985,
        attributes_normalized={"тип товара": "Конвектор", "диаметр соединения (дюймы)": "1/2", "подключение": "Прямое правое"},
    )
    agent = FeedSearchAgent(
        [heating_valve, water_valve, no_union, thermostat, manual_ppr, convector]
    )

    water_results = agent.search(
        SearchQuery(
            original_text="кран для воды 3/4 с американкой",
            category="valves",
            slots={"application": "вода", "size_inch": "3/4", "union": True},
        )
    )
    thermostat_results = agent.search(
        SearchQuery(
            original_text="для радиатора угловой 1/2 регулировать температуру",
            category="radiator_fittings",
            slots={
                "application": "радиатор",
                "connection_form": "угловое",
                "size_inch": "1/2",
                "thermostatic_head": True,
            },
        )
    )

    assert [product.sku for product in water_results] == ["LD 47.346.20"]
    assert [product.sku for product in thermostat_results] == ["VT.031.N.04"]


def test_explicit_brand_and_in_stock_are_hard_boundaries() -> None:
    in_stock_other_brand = Product(
        sku="VESTA-25-60",
        name="Насос циркуляционный VESTA 25-60",
        category_path="Насосное оборудование",
        brand="VESTA",
        url="https://example.test/vesta",
        price=4000,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={"тип товара": "Насос"},
    )
    out_of_stock_wilo = Product(
        sku="WILO-25-60",
        name="Насос циркуляционный Wilo 25-60",
        category_path="Насосное оборудование",
        brand="Wilo",
        url="https://example.test/wilo",
        price=7000,
        stock_status="нет в наличии",
        stock_qty=0,
        attributes_normalized={"тип товара": "Насос"},
    )
    agent = FeedSearchAgent([in_stock_other_brand, out_of_stock_wilo])
    query = SearchQuery(
        original_text="только Wilo циркуляционный насос в наличии",
        category="pumps",
        brand="Wilo",
        in_stock_only=True,
        slots={"pump_type": "циркуляционный"},
    )

    assert agent.search(query) == []
    assert agent.search_alternatives(query) == []
    assert agent.search(
        SearchQuery(
            original_text="Grundfos насос",
            category="pumps",
            brand="Grundfos",
        )
    ) == []


def test_ppr_in_message_is_a_hard_material_constraint_for_alternatives() -> None:
    pex_16 = Product(
        sku="16PAEVOH24T",
        name="Труба PE-Xa EVOH 16x2.0 для теплого пола",
        category_path="Трубы",
        url="https://example.test/pex16",
        price=80,
        description="Для систем отопления.",
    )
    ppr_20 = Product(
        sku="VTp.700.FB25.20",
        name="Труба PP-FIBER PN 25, 20 MM",
        category_path="Трубы полипропиленовые",
        url="https://example.test/ppr20",
        price=120,
        description="Полипропиленовая труба для водяного отопления.",
    )
    agent = FeedSearchAgent([pex_16, ppr_20])
    query = SearchQuery(
        original_text="труба PPR 16 мм для отопления",
        category="pipes",
        slots={"diameter_mm": 16, "pipe_purpose": "отопление"},
    )

    assert agent.search(query) == []
    assert [product.sku for product in agent.search_alternatives(query)] == [
        "VTp.700.FB25.20"
    ]
