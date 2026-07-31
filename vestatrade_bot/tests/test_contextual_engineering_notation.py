from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product, SearchQuery, SessionState


def _product(
    sku: str,
    name: str,
    path: str,
    attrs: dict[str, str],
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=path,
        brand="VALTEC",
        url=f"https://example.test/{sku.lower()}",
        price=100,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized=attrs,
    )


@pytest.mark.parametrize(
    ("message", "category", "expected"),
    [
        (
            "труба PEX-a EVOH 16×2,0 для ВТП, t=70 °С, p=6 бар",
            "pipes",
            {
                "pipe_material": "pex",
                "oxygen_barrier": True,
                "diameter_mm": 16,
                "wall_thickness_mm": 2.0,
                "pipe_service": "петля тёплого пола",
                "operating_temperature_c": 70.0,
                "operating_pressure_bar": 6.0,
            },
        ),
        (
            "полипропиленовая труба для системы отопления, номинальное давление 20 бар",
            "pipes",
            {
                "pipe_material": "ppr",
                "pipe_purpose": "отопление",
                "pressure_class_bar": 20.0,
            },
        ),
        (
            "фитинг PPSU G1/2 с внутренней резьбой, профиль TH",
            "fittings",
            {
                "fitting_material": "ppsu",
                "thread_standard": "g",
                "thread_gender": "female",
                "size_inch": "1/2",
                "press_profile": "th",
            },
        ),
        (
            "канализация HTEA DN110 SN4",
            "sewer",
            {
                "sewer_scope": "внутренняя",
                "sewer_system_code": "htea",
                "element_type": "тройник",
                "nominal_diameter_dn": 110,
                "ring_stiffness_sn": 4,
            },
        ),
        (
            "насос Q=50 л/мин, H=8 м, DN32, 180 мм, IP44",
            "pumps",
            {
                "required_flow_m3_h": 3.0,
                "required_head_m": 8.0,
                "connection_size": 32,
                "mounting_length_mm": 180,
                "ip_rating": "ip44",
            },
        ),
        (
            "газовый котёл 2К ЗКС NG, дымоход 60/100",
            "boilers",
            {
                "contours": "двухконтурный",
                "combustion_chamber": "закрытая",
                "gas_type": "природный",
                "chimney_size": "60/100",
            },
        ),
        (
            "ЭВН 80 л с сухим ТЭНом IPX4 230 В",
            "water_heaters",
            {
                "heater_type": "накопительный",
                "energy_source": "электрический",
                "volume_l": 80,
                "heating_element_type": "сухой",
                "ip_rating": "ipx4",
                "voltage_v": 230,
            },
        ),
        (
            "радиатор тип 22, м/о 500, ΔT70, нижнее подключение",
            "radiators",
            {
                "radiator_panel_type": 22,
                "radiator_size_mm": 500,
                "rating_delta_t_c": 70,
                "radiator_connection": "нижнее",
            },
        ),
        (
            "клапан DN20 PN16 Kvs=2,5 НЗ",
            "valves",
            {
                "nominal_diameter_dn": 20,
                "pressure_class_bar": 16.0,
                "flow_coefficient_kind": "kvs",
                "flow_coefficient": 2.5,
                "normal_state": "нормально закрытый",
            },
        ),
        (
            "картридж 20BB CTO 5 мкм для ХВС",
            "filters",
            {
                "filter_format": "20bb",
                "filter_technology": "cto",
                "filtration_microns": 5.0,
                "water_temperature": "холодная",
            },
        ),
        (
            "сервопривод NC 230V DC, сигнал 0-10V",
            "controls",
            {
                "control_kind": "сервопривод",
                "normal_state": "нормально закрытый",
                "voltage_v": 230,
                "current_type": "dc",
                "control_signal": "0-10v",
            },
        ),
    ],
)
def test_contextual_notation_extracts_canonical_slots(
    message: str,
    category: str,
    expected: dict[str, object],
) -> None:
    result = IntentRouterAgent().route(message)

    assert result.category == category
    for key, value in expected.items():
        assert result.slots[key] == value


@pytest.mark.parametrize(
    ("abbreviated", "expanded", "keys"),
    [
        (
            "труба PPR для СО PN20",
            "полипропиленовая труба для системы отопления, номинальное давление 20 бар",
            ["pipe_material", "pipe_purpose", "pressure_class_bar"],
        ),
        (
            "клапан Ду20 Ру16 Kvs 2,5",
            "клапан: условный проход 20, номинальное давление 16 бар, пропускная способность 2,5",
            ["nominal_diameter_dn", "pressure_class_bar", "flow_coefficient"],
        ),
        (
            "сервопривод NC 230V",
            "нормально закрытый сервопривод с питанием 230 вольт",
            ["control_kind", "normal_state", "voltage_v"],
        ),
        (
            "картридж CTO 20BB",
            "картридж из прессованного угля типоразмера 20BB",
            ["filter_element_type", "filter_technology", "filter_format"],
        ),
        (
            "механический картридж 20BB",
            "механический картридж Big Blue 20",
            ["filter_element_type", "filter_technology", "filter_format"],
        ),
    ],
)
def test_abbreviation_and_full_text_have_the_same_meaning(
    abbreviated: str,
    expanded: str,
    keys: list[str],
) -> None:
    router = IntentRouterAgent()
    short = router.route(abbreviated)
    full = router.route(expanded)

    assert short.category == full.category
    for key in keys:
        assert short.slots[key] == full.slots[key]


def test_ambiguous_codes_are_gated_by_category_and_negation() -> None:
    router = IntentRouterAgent()

    radiator = router.route("радиатор Gekon CV22 500")
    assert radiator.category == "radiators"
    assert radiator.slots["radiator_panel_type"] == 22
    assert "flow_coefficient" not in radiator.slots

    ordinary_conjunction = router.route("но нужен клапан без сервопривода")
    assert ordinary_conjunction.category == "valves"
    assert "normal_state" not in ordinary_conjunction.slots
    assert "control_kind" not in ordinary_conjunction.slots

    mixer_height = router.route("смеситель, H=180 мм")
    assert mixer_height.category != "pumps"
    assert "required_head_m" not in mixer_height.slots


def test_q_without_unit_is_not_assumed_to_be_cubic_metres_per_hour() -> None:
    result = IntentRouterAgent().route("циркуляционный насос Q=50, H=6 м")

    assert result.category == "pumps"
    assert "required_flow_m3_h" not in result.slots
    assert result.slots["required_head_m"] == 6.0


def test_integer_wall_thickness_notation_is_preserved() -> None:
    result = IntentRouterAgent().route("труба PE-X EVOH 20×2 мм")

    assert result.category == "pipes"
    assert result.slots["diameter_mm"] == 20
    assert result.slots["wall_thickness_mm"] == 2.0


def test_radiator_height_and_interaxial_distance_are_not_conflated() -> None:
    height = IntentRouterAgent().route("панельный радиатор тип 22, высота 500 мм")
    interaxial = IntentRouterAgent().route("радиатор м/о 500")

    assert height.slots["radiator_height_mm"] == 500
    assert "radiator_size_mm" not in height.slots
    assert interaxial.slots["radiator_size_mm"] == 500
    assert "radiator_height_mm" not in interaxial.slots


def test_bare_number_is_bound_only_to_pending_volume_question() -> None:
    router = IntentRouterAgent()
    session = SessionState(session_id="pending-volume")
    session.category = "hydraulic_accumulators"
    session.pending_category = "hydraulic_accumulators"
    session.pending_question = "Какой нужен объём бака в литрах?"
    session.pending_slot_keys = ["volume_l"]

    result = router.route("24", session)

    assert result.category == "hydraulic_accumulators"
    assert result.slots["volume_l"] == 24


def test_new_notation_is_enforced_as_hard_catalog_constraints() -> None:
    products = [
        _product(
            "FILTER-5",
            "Картридж CTO 20BB 5 мкм",
            "Фильтры",
            {"тип товара": "Картридж", "тонкость фильтрации, мкм": "5"},
        ),
        _product(
            "FILTER-10",
            "Картридж CTO 20BB 10 мкм",
            "Фильтры",
            {"тип товара": "Картридж", "тонкость фильтрации, мкм": "10"},
        ),
        _product(
            "FILTER-SL",
            "Картридж CTO 10SL 5 мкм",
            "Фильтры",
            {"тип товара": "Картридж", "тонкость фильтрации, мкм": "5"},
        ),
        _product(
            "CONTROL-NC",
            "Сервопривод NC 230V 0-10V",
            "Автоматика для систем отопления",
            {"тип товара": "Сервопривод", "напряжение, В": "230"},
        ),
        _product(
            "CONTROL-NO",
            "Сервопривод NO 230V 0-10V",
            "Автоматика для систем отопления",
            {"тип товара": "Сервопривод", "напряжение, В": "230"},
        ),
        _product(
            "PEX-EVOH",
            "Труба PE-Xa EVOH 16×2,0",
            "Трубы",
            {
                "тип товара": "Труба",
                "материал": "PE-Xa",
                "диаметр (мм)": "16",
                "толщина стенки (мм)": "2,0",
            },
        ),
        _product(
            "PEX-NO-BARRIER",
            "Труба PE-Xa 16×2,0",
            "Трубы",
            {
                "тип товара": "Труба",
                "материал": "PE-Xa",
                "диаметр (мм)": "16",
                "толщина стенки (мм)": "2,0",
            },
        ),
        _product(
            "RAD-22",
            "Радиатор панельный CV22-500-1000",
            "Радиаторы отопления",
            {"тип товара": "Радиатор отопления", "тип": "22", "межосевое расстояние, мм": "500"},
        ),
        _product(
            "RAD-11",
            "Радиатор панельный C11-500-1000",
            "Радиаторы отопления",
            {"тип товара": "Радиатор отопления", "тип": "11", "межосевое расстояние, мм": "500"},
        ),
    ]
    search = FeedSearchAgent(products)

    assert [
        product.sku
        for product in search.search(
            SearchQuery(
                original_text="картридж CTO 20BB 5 мкм",
                category="filters",
                slots={
                    "filter_element_type": "картридж",
                    "filter_technology": "cto",
                    "filter_format": "20bb",
                    "filtration_microns": 5,
                },
            )
        )
    ] == ["FILTER-5"]
    assert [
        product.sku
        for product in search.search(
            SearchQuery(
                original_text="сервопривод NC 230V 0-10V",
                category="controls",
                slots={
                    "control_kind": "сервопривод",
                    "normal_state": "нормально закрытый",
                    "voltage_v": 230,
                    "control_signal": "0-10v",
                },
            )
        )
    ] == ["CONTROL-NC"]
    assert [
        product.sku
        for product in search.search(
            SearchQuery(
                original_text="труба PEX EVOH 16×2,0",
                category="pipes",
                slots={
                    "pipe_material": "pex",
                    "diameter_mm": 16,
                    "wall_thickness_mm": 2.0,
                    "oxygen_barrier": True,
                },
            )
        )
    ] == ["PEX-EVOH"]
    assert [
        product.sku
        for product in search.search(
            SearchQuery(
                original_text="радиатор тип 22 м/о 500",
                category="radiators",
                slots={"radiator_panel_type": 22, "radiator_size_mm": 500},
            )
        )
    ] == ["RAD-22"]


def test_filter_clarification_context_survives_short_answers() -> None:
    products = [
        _product(
            "FILTER-5",
            "Картридж механической очистки 20BB 5 мкм",
            "Фильтры",
            {"тип товара": "Картридж", "тонкость фильтрации, мкм": "5"},
        ),
        _product(
            "FILTER-10",
            "Картридж механической очистки 20BB 10 мкм",
            "Фильтры",
            {"тип товара": "Картридж", "тонкость фильтрации, мкм": "10"},
        ),
    ]
    bot = ChatOrchestrator(products=products)

    first = bot.handle_chat("filter-context", "нужен картридж")
    assert "типоразмер" in first.answer.lower()

    second = bot.handle_chat("filter-context", "20BB")
    assert second.debug["slots"]["filter_format"] == "20bb"
    assert "для чего" in second.answer.lower()

    final = bot.handle_chat("filter-context", "механическая очистка, 5 мкм")
    assert final.debug["slots"]["filter_format"] == "20bb"
    assert final.debug["slots"]["filter_technology"] == "mechanical"
    assert [card.sku for card in final.products] == ["FILTER-5"]


def test_cto_does_not_match_an_unrelated_10bb_cartridge() -> None:
    search = FeedSearchAgent(
        [
            _product(
                "CTO",
                "Картридж из прессованного угля CTO 10BB",
                "Фильтры",
                {"тип товара": "Картридж"},
            ),
            _product(
                "FE",
                "Картридж обезжелезивания 10BB, ионообменное волокно",
                "Фильтры",
                {"тип товара": "Картридж"},
            ),
        ]
    )

    found = search.search(
        SearchQuery(
            original_text="картридж 10BB CTO",
            category="filters",
            slots={
                "filter_element_type": "картридж",
                "filter_format": "10bb",
                "filter_technology": "cto",
            },
        )
    )

    assert [product.sku for product in found] == ["CTO"]


def test_230v_request_matches_220v_feed_label_in_same_mains_class() -> None:
    search = FeedSearchAgent(
        [
            _product(
                "SERVO-220-NC",
                "Электротермический сервопривод, норм. ЗАКР., питание 220 В",
                "Автоматика для систем отопления",
                {"тип товара": "Сервопривод", "напряжение, В": "220"},
            ),
            _product(
                "SERVO-24-NC",
                "Сервопривод NC 24 В",
                "Автоматика для систем отопления",
                {"тип товара": "Сервопривод", "напряжение, В": "24"},
            ),
        ]
    )

    found = search.search(
        SearchQuery(
            original_text="сервопривод НЗ 230 В",
            category="controls",
            slots={
                "control_kind": "сервопривод",
                "normal_state": "нормально закрытый",
                "voltage_v": 230,
            },
        )
    )

    assert [product.sku for product in found] == ["SERVO-220-NC"]
