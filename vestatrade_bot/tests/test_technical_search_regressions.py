from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.slot_filling import SlotFillingAgent
from app.models import IntentResult, Product, SearchQuery, SessionState


def product(
    sku: str,
    name: str,
    category_path: str,
    *,
    brand: str | None = None,
    price: float = 1000,
    attributes: dict[str, str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category_path,
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized=attributes or {},
    )


def test_exact_sku_never_uses_substring_but_compact_feed_sku_still_matches() -> None:
    agent = FeedSearchAgent(
        [
            product("ABC-12345-X", "Товар с длинным артикулом", "Прочее"),
            product("RT06", "Кран шаровой RT06", "Краны шаровые"),
        ]
    )

    assert agent.search(SearchQuery(original_text="ABC-12345", sku="ABC-12345")) == []
    assert [
        item.sku
        for item in agent.search(SearchQuery(original_text="RT06", sku="RT06"))
    ] == ["RT06"]


def test_sewer_followup_keeps_confirmed_dn_and_treats_90_as_angle() -> None:
    result = SlotFillingAgent().fill(
        "внутренняя, 90",
        IntentResult(
            intent_type="attribute_request",
            category="sewer",
            slots={"sewer_scope": "внутренняя", "diameter_mm": 90},
        ),
        SessionState(
            session_id="sewer-angle",
            category="sewer",
            slots={"element_type": "отвод", "diameter_mm": 110},
        ),
    )

    assert result.slots["diameter_mm"] == 110
    assert result.slots["angle_deg"] == 90
    assert result.needs_clarification is False


def test_sewer_bend_search_and_alternatives_preserve_dn_and_angle() -> None:
    correct = product(
        "HTB-110-87",
        'Отвод 87°, HTB, 110"',
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Отвод", "диаметр, мм": "110"},
    )
    wrong_dn = product(
        "HTB-50-87",
        'Отвод 87°, HTB, 50"',
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Отвод", "диаметр, мм": "50"},
    )
    wrong_angle = product(
        "HTB-110-45",
        'Отвод 45°, HTB, 110"',
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Отвод", "диаметр, мм": "110"},
    )
    agent = FeedSearchAgent([wrong_dn, wrong_angle, correct])
    query = SearchQuery(
        original_text="отвод 110, внутренний, 90 градусов",
        category="sewer",
        slots={
            "element_type": "отвод",
            "sewer_scope": "внутренняя",
            "diameter_mm": 110,
            "angle_deg": 90,
        },
    )

    assert [item.sku for item in agent.search(query)] == ["HTB-110-87"]
    assert [item.sku for item in agent.search_alternatives(query)] == ["HTB-110-87"]


def test_connecting_sewer_coupling_excludes_reducers_and_repair_couplings() -> None:
    connecting = product(
        "HTM-50",
        "Муфта соединительная HTM 50",
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Муфта", "диаметр, мм": "50"},
    )
    reducer = product(
        "HTR-50-40",
        "Муфта переходная HTR 50x40",
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Муфта", "диаметр, мм": "50"},
    )
    repair = product(
        "HTU-50",
        "Муфта надвижная ремонтная HTU 50",
        "Канализационные системы / Внутренняя канализация",
        attributes={"тип товара": "Муфта", "диаметр, мм": "50"},
    )
    agent = FeedSearchAgent([reducer, repair, connecting])
    query = SearchQuery(
        original_text="муфта внутренняя 50 соединительная",
        category="sewer",
        slots={
            "element_type": "муфта",
            "coupling_type": "соединительная",
            "sewer_scope": "внутренняя",
            "diameter_mm": 50,
        },
    )

    assert [item.sku for item in agent.search(query)] == ["HTM-50"]
    assert [item.sku for item in agent.search_alternatives(query)] == ["HTM-50"]


def test_pump_alternatives_keep_connection_head_and_mounting_length() -> None:
    exact = product(
        "PUMP-25-6-130",
        "Насос циркуляционный 25/6-130",
        "Насосное оборудование",
        price=4000,
        attributes={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "диаметр подключения, мм": "25",
            "максимальный напор, м": "6",
            "монтажная длина, мм": "130",
        },
    )
    wrong_head = product(
        "PUMP-25-4-130",
        "Насос циркуляционный 25/4-130",
        "Насосное оборудование",
        price=2000,
        attributes={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "диаметр подключения, мм": "25",
            "максимальный напор, м": "4",
            "монтажная длина, мм": "130",
        },
    )
    wrong_length = product(
        "PUMP-25-6-180",
        "Насос циркуляционный 25/6-180",
        "Насосное оборудование",
        price=2500,
        attributes={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "диаметр подключения, мм": "25",
            "максимальный напор, м": "6",
            "монтажная длина, мм": "180",
        },
    )
    agent = FeedSearchAgent([wrong_head, wrong_length, exact])
    query = SearchQuery(
        original_text="насос 25/6 130 подешевле",
        category="pumps",
        cheap=True,
        slots={
            "pump_type": "циркуляционный",
            "connection_size": 25,
            "head_m": 6.0,
            "mounting_length_mm": 130,
        },
    )

    assert [item.sku for item in agent.search(query)] == ["PUMP-25-6-130"]
    assert [item.sku for item in agent.search_alternatives(query)] == [
        "PUMP-25-6-130"
    ]


def test_partial_circulation_pump_parameters_require_clarification() -> None:
    result = SlotFillingAgent().fill(
        "для отопления, 130 мм",
        IntentResult(
            intent_type="attribute_request",
            category="pumps",
            slots={
                "pump_type": "циркуляционный",
                "pump_use": "отопление",
                "mounting_length_mm": 130,
            },
        ),
        SessionState(session_id="pump-partial", category="pumps"),
    )

    assert result.needs_clarification is True
    assert "присоедин" in (result.question or "").lower()
    assert "напор" in (result.question or "").lower()


def test_parameter_shaped_pump_sku_from_non_exact_intent_is_removed() -> None:
    result = SlotFillingAgent().fill(
        "да, бренд не важен",
        IntentResult(
            intent_type="attribute_request",
            category="pumps",
            slots={"sku": "25/6-130"},
        ),
        SessionState(
            session_id="pump-false-sku",
            category="pumps",
            slots={
                "pump_type": "циркуляционный",
                "connection_size": 25,
                "head_m": 6.0,
                "mounting_length_mm": 130,
            },
        ),
    )

    assert "sku" not in result.slots
    assert result.needs_clarification is False


def test_ball_valve_request_excludes_drain_cock_and_check_valve() -> None:
    ball = product(
        "VT-BALL",
        'Кран шаровой BASE 1/2"',
        "Водозапорная арматура / Краны шаровые",
        brand="VALTEC",
        attributes={
            "тип товара": "Кран шаровой",
            "рабочая среда": "Для воды",
            "диаметр подключения, дюйм": "1/2",
        },
    )
    drain = product(
        "VT-DRAIN",
        'Кран дренажный 1/2"',
        "Водозапорная арматура / Краны шаровые",
        brand="VALTEC",
        attributes={
            "тип товара": "Кран",
            "рабочая среда": "Для воды",
            "диаметр подключения, дюйм": "1/2",
        },
    )
    check = product(
        "VT-CHECK",
        'Клапан обратный 1/2"',
        "Водозапорная арматура / Обратные клапаны",
        brand="VALTEC",
        attributes={
            "тип товара": "Клапан",
            "рабочая среда": "Для воды",
            "диаметр подключения, дюйм": "1/2",
        },
    )
    agent = FeedSearchAgent([drain, check, ball])
    query = SearchQuery(
        original_text="кран 1/2 только VALTEC для воды",
        category="valves",
        brand="VALTEC",
        slots={
            "valve_kind": "шаровый кран",
            "application": "вода",
            "size_inch": "1/2",
        },
    )

    assert [item.sku for item in agent.search(query)] == ["VT-BALL"]
    assert [item.sku for item in agent.search_alternatives(query)] == ["VT-BALL"]


def test_valve_slot_filling_remembers_ball_valve_kind_across_clarification() -> None:
    filler = SlotFillingAgent()
    first = filler.fill(
        "нужен кран 1/2, только VALTEC",
        IntentResult(
            intent_type="brand_category",
            category="valves",
            slots={"brand": "VALTEC", "size_inch": "1/2"},
        ),
        SessionState(session_id="valve-kind"),
    )
    second = filler.fill(
        "для воды, без аналогов",
        IntentResult(
            intent_type="attribute_request",
            category="valves",
            slots={"application": "вода"},
        ),
        SessionState(
            session_id="valve-kind",
            category="valves",
            slots=first.slots,
        ),
    )

    assert first.slots["valve_kind"] == "шаровый кран"
    assert second.slots["valve_kind"] == "шаровый кран"


def test_white_pipe_is_a_hard_constraint_for_search_and_alternatives() -> None:
    white = product(
        "PIPE-WHITE-20",
        "Труба PPR белая 20 мм для ГВС",
        "Трубы полипропиленовые",
        attributes={"диаметр, мм": "20", "цвет": "Белый"},
    )
    gray = product(
        "PIPE-GRAY-20",
        "Труба PPR серая 20 мм для ГВС",
        "Трубы полипропиленовые",
        price=500,
        attributes={"диаметр, мм": "20", "цвет": "Серый"},
    )
    agent = FeedSearchAgent([gray, white])
    query = SearchQuery(
        original_text="белая труба 20 мм для горячей воды",
        category="pipes",
        slots={
            "element_type": "труба",
            "diameter_mm": 20,
            "pipe_purpose": "водоснабжение",
            "water_temperature": "горячая",
            "pipe_color": "белая",
        },
    )

    assert [item.sku for item in agent.search(query)] == ["PIPE-WHITE-20"]
    assert [item.sku for item in agent.search_alternatives(query)] == [
        "PIPE-WHITE-20"
    ]


def test_radiator_fitting_alternatives_do_not_relax_inch_or_union() -> None:
    union_three_quarter = product(
        "RAD-UNION-34",
        'Клапан радиаторный 3/4" с американкой',
        "Арматура для радиаторов",
        attributes={
            "тип товара": "Клапан",
            "присоединительная резьба, дюйм": "3/4",
            "тип конструкции": "Радиаторный, с американкой",
        },
    )
    plain_half = product(
        "RAD-PLAIN-12",
        'Клапан радиаторный 1/2"',
        "Арматура для радиаторов",
        attributes={
            "тип товара": "Клапан",
            "присоединительная резьба, дюйм": "1/2",
            "тип конструкции": "Радиаторный",
        },
    )
    agent = FeedSearchAgent([union_three_quarter, plain_half])
    query = SearchQuery(
        original_text="радиаторный клапан 1/2 с американкой",
        category="radiator_fittings",
        slots={
            "application": "радиатор",
            "size_inch": "1/2",
            "union": True,
        },
    )

    assert agent.search(query) == []
    assert agent.search_alternatives(query) == []


def test_boiler_alternatives_do_not_relax_voltage() -> None:
    unknown_voltage = product(
        "BOILER-UNKNOWN",
        "Котёл электрический 12 кВт",
        "Котлы электрические",
        attributes={"тип котла": "Электрический", "мощность, квт": "12"},
    )
    voltage_220 = product(
        "BOILER-220",
        "Котёл электрический 12 кВт 220 В",
        "Котлы электрические",
        attributes={
            "тип котла": "Электрический",
            "мощность, квт": "12",
            "напряжение, в": "220",
        },
    )
    agent = FeedSearchAgent([unknown_voltage, voltage_220])
    query = SearchQuery(
        original_text="электрический котёл 380 В",
        category="boilers",
        slots={"boiler_type": "электрический", "voltage_v": 380},
    )

    assert agent.search(query) == []
    assert agent.search_alternatives(query) == []
