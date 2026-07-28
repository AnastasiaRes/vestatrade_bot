from __future__ import annotations

import pytest

from app.agents.consultant import ConsultantAgent
from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_sku
from app.models import Product, SearchQuery


def _product(
    sku: str,
    name: str,
    *,
    price: float = 18_000,
    stock_qty: int = 3,
    attributes: dict[str, str] | None = None,
    description: str = "",
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Водонагреватели",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized=attributes or {},
        description=description,
    )


def _heater(
    sku: str,
    *,
    volume_l: int = 80,
    heater_type: str = "Накопительный",
    energy_source: str = "Электрический",
    price: float = 18_000,
    stock_qty: int = 3,
    mounting: str | None = None,
    orientation: str | None = None,
    name: str | None = None,
) -> Product:
    attributes = {
        "тип товара": "Водонагреватель",
        "тип водонагревателя": heater_type,
        "источник энергии": energy_source,
        "объём, л": str(volume_l),
    }
    if mounting:
        attributes["монтаж"] = mounting
    if orientation:
        attributes["ориентация"] = orientation
    return _product(
        sku,
        name
        or (
            f"Водонагреватель {energy_source.lower()} "
            f"{heater_type.lower()} TEST {volume_l} л"
        ),
        price=price,
        stock_qty=stock_qty,
        attributes=attributes,
    )


def _storage_80(
    sku: str = "RWH-80",
    *,
    price: float = 18_990,
    stock_qty: int = 5,
) -> Product:
    return _heater(
        sku,
        price=price,
        stock_qty=stock_qty,
        mounting="Настенный",
        orientation="Вертикальная",
        name=(
            "Водонагреватель электрический накопительный "
            f"Royal Thermo {sku} Citadel Unic 80 л"
        ),
    )


@pytest.fixture
def mixed_heater_catalog() -> list[Product]:
    return [
        _storage_80(),
        _heater("WH-50", volume_l=50),
        _heater(
            "WH-FLOW",
            heater_type="Проточный",
            # Deliberately equal to the query so type, not just volume,
            # remains a hard boundary.
            volume_l=80,
        ),
        _heater(
            "WH-INDIRECT",
            heater_type="Косвенного нагрева",
            energy_source="Косвенный",
            name="Бойлер косвенного нагрева TEST 80 л",
        ),
        _heater(
            "WH-OUT",
            stock_qty=0,
        ),
        _product(
            "HEATER-ELEMENT-80",
            "ТЭН для водонагревателя 80 л",
            price=2_000,
            attributes={"тип товара": "ТЭН"},
        ),
        _product(
            "WH-UNKNOWN",
            "Водонагреватель TEST Mystery 80",
            attributes={"тип товара": "Водонагреватель"},
        ),
        Product(
            sku="BOILER-E9",
            name="Котёл электрический TEST 9 кВт",
            category_path="Котлы электрические",
            brand="TEST",
            url="https://example.test/boiler-e9",
            price=35_000,
            stock_status="в наличии",
            stock_qty=4,
            attributes_normalized={
                "тип товара": "Котёл",
                "тип котла": "Электрический",
                "мощность, кВт": "9",
            },
        ),
    ]


def test_electric_storage_80l_in_stock_returns_only_matching_water_heater(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)

    response = bot.handle_chat(
        "heater-80",
        "Нужен электрический накопительный водонагреватель на 80 литров, "
        "только в наличии",
    )

    assert [product.sku for product in response.products] == ["RWH-80"]
    assert all("кот" not in product.name.lower() for product in response.products)


def test_standalone_boiler_means_water_heater_not_heating_boiler_clarification(
    mixed_heater_catalog: list[Product],
) -> None:
    router_result = IntentRouterAgent().route("бойлер")
    bot = ChatOrchestrator(products=mixed_heater_catalog)

    response = bot.handle_chat("standalone-boiler", "бойлер")
    session = bot.sessions.get("standalone-boiler")

    assert router_result.category == "water_heaters"
    assert session.category == "water_heaters"
    assert "газовый или электрический кот" not in response.answer.lower()


def test_heating_boiler_with_water_tank_keeps_boiler_context(
    mixed_heater_catalog: list[Product],
) -> None:
    router_result = IntentRouterAgent().route("Нужен котёл с бойлером")
    bot = ChatOrchestrator(products=mixed_heater_catalog)

    bot.handle_chat("boiler-with-tank", "Нужен котёл с бойлером")
    session = bot.sessions.get("boiler-with-tank")

    assert router_result.category == "boilers"
    assert session.category == "boilers"


def test_separate_boiler_and_water_heater_request_is_not_silently_reduced(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    session_id = "boiler-and-separate-heater"

    first = bot.handle_chat(session_id, "Нужен котёл и бойлер")
    second = bot.handle_chat(session_id, "Два отдельных")
    session = bot.sessions.get(session_id)

    assert first.products == []
    assert "два отдельных" in first.answer.lower()
    assert "встроенн" in first.answer.lower()
    assert "отдельно подбер" in second.answer.lower()
    assert "водонагревател" in second.answer.lower()
    assert session.slots["boiler_water_heater_relation"] == "отдельные приборы"


def test_negative_flow_heater_correction_clears_old_type(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    bot.handle_chat(
        "heater-correction",
        "Нужен проточный электрический водонагреватель",
    )

    response = bot.handle_chat(
        "heater-correction",
        "Нет, не проточный, накопительный на 80 л, только в наличии",
    )
    session = bot.sessions.get("heater-correction")

    assert [product.sku for product in response.products] == ["RWH-80"]
    assert session.slots["heater_type"] == "накопительный"
    assert session.slots["volume_l"] == 80
    assert session.slots["energy_source"] == "электрический"


def test_unknown_critical_water_heater_attributes_are_not_assumed(
    mixed_heater_catalog: list[Product],
) -> None:
    result = FeedSearchAgent(mixed_heater_catalog).search(
        SearchQuery(
            original_text="электрический накопительный водонагреватель 80 л",
            category="water_heaters",
            slots={
                "heater_type": "накопительный",
                "energy_source": "электрический",
                "volume_l": 80,
            },
        )
    )

    assert {product.sku for product in result} == {"RWH-80", "WH-OUT"}
    assert "WH-UNKNOWN" not in {product.sku for product in result}


def test_water_heater_mounting_and_orientation_are_parsed_and_strict() -> None:
    vertical = _storage_80()
    horizontal = _heater(
        "WH-HORIZONTAL",
        mounting="Настенный",
        orientation="Горизонтальная",
    )
    message = (
        "Нужен настенный вертикальный электрический накопительный "
        "водонагреватель 80 л"
    )
    route = IntentRouterAgent().route(message)
    result = FeedSearchAgent([vertical, horizontal]).search(
        SearchQuery(
            original_text=message,
            category="water_heaters",
            slots=route.slots,
        )
    )

    assert route.slots["mounting"] == "настенный"
    assert route.slots["orientation"] == "вертикальный"
    assert [product.sku for product in result] == ["RWH-80"]


def test_water_heater_analogs_keep_type_energy_volume_stock_and_budget() -> None:
    candidates = [
        _storage_80("ALT-OK", price=19_500),
        _storage_80("ALT-OVER-BUDGET", price=20_500),
        _storage_80("ALT-OUT", price=19_000, stock_qty=0),
        _heater("ALT-50", volume_l=50),
        _heater(
            "ALT-FLOW",
            heater_type="Проточный",
        ),
        _heater(
            "ALT-INDIRECT",
            heater_type="Косвенного нагрева",
            energy_source="Косвенный",
            name="Бойлер косвенного нагрева ALT 80 л",
        ),
    ]
    result = FeedSearchAgent(candidates).search_alternatives(
        SearchQuery(
            original_text="Покажи аналог, только в наличии и до 20000 рублей",
            category="water_heaters",
            slots={
                "heater_type": "накопительный",
                "energy_source": "электрический",
                "volume_l": 80,
                "max_price": 20_000,
                "in_stock": True,
            },
            in_stock_only=True,
        )
    )

    assert [product.sku for product in result] == ["ALT-OK"]


def test_more_water_heaters_keeps_persisted_stock_filter(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    bot.handle_chat(
        "heater-more-stock",
        "Нужен электрический накопительный водонагреватель 80 л, "
        "только в наличии",
    )

    response = bot.handle_chat("heater-more-stock", "Покажи ещё варианты")
    session = bot.sessions.get("heater-more-stock")

    assert all(product.stock_status != "нет в наличии" for product in response.products)
    assert all(product.sku != "WH-OUT" for product in response.products)
    assert "heat_sources" not in session.slots


def test_full_water_heater_name_uses_precise_name_path(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)

    response = bot.handle_chat(
        "heater-full-name",
        "Водонагреватель электрический накопительный "
        "Royal Thermo RWH-80 Citadel Unic 80 л",
    )

    assert [product.sku for product in response.products] == ["RWH-80"]


def test_full_spaced_name_wins_over_same_series_other_volumes() -> None:
    products = [
        _heater(
            f"RWH {volume} Citadel Unic",
            volume_l=volume,
            name=(
                "Водонагреватель Royal Thermo "
                f"RWH {volume} Citadel Unic"
            ),
        )
        for volume in [30, 50, 80]
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "heater-spaced-full-name",
        "Водонагреватель Royal Thermo RWH 80 Citadel Unic",
    )

    assert [product.sku for product in response.products] == [
        "RWH 80 Citadel Unic"
    ]


def test_standalone_stock_followup_becomes_persistent_filter(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    session_id = "heater-step-stock"
    for message in [
        "бойлер",
        "накопительный",
        "электрический",
        "80 литров",
    ]:
        bot.handle_chat(session_id, message)

    response = bot.handle_chat(session_id, "только в наличии")
    session = bot.sessions.get(session_id)

    assert session.slots["in_stock"] is True
    assert [product.sku for product in response.products] == ["RWH-80"]

    cheaper = bot.handle_chat(session_id, "Покажи дешевле")
    assert all(product.stock_status != "нет в наличии" for product in cheaper.products)


def test_everything_for_toilet_installation_never_returns_boilers(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    bot.handle_chat(
        "toilet-installation",
        "Нужен электрический котёл 9 кВт",
    )

    response = bot.handle_chat(
        "toilet-installation",
        "нужно всё для установки туалета",
    )
    answer = response.answer.lower()
    session = bot.sessions.get("toilet-installation")

    assert response.products == []
    assert session.category != "boilers"
    assert "газовый или электрический кот" not in answer
    assert any(
        marker in answer
        for marker in ["унитаз", "инсталляц", "канализац", "подвод"]
    )
    assert "?" in answer


@pytest.mark.parametrize(
    "message",
    [
        "Для бойлера нужен ТЭН",
        "Для водонагревателя нужен анод",
        "ТЭН для бойлера",
    ],
)
def test_water_heater_spare_part_is_not_routed_as_appliance(message: str) -> None:
    result = IntentRouterAgent()._rule_based(message, None)

    assert result.category != "water_heaters"
    assert "heater_type" not in result.slots


def test_water_heater_category_honours_product_negation_and_keeps_boiler() -> None:
    router = IntentRouterAgent()

    correction = router._rule_based(
        "Не водонагреватель, нужен электрический котёл",
        None,
    )
    combined_request = router._rule_based("Нужен котёл и бойлер", None)
    reverse_correction = router._rule_based("Не котёл, а бойлер", None)

    assert correction.category == "boilers"
    assert correction.slots["boiler_type"] == "электрический"
    assert combined_request.category == "boilers"
    assert reverse_correction.category == "water_heaters"


@pytest.mark.parametrize(
    "message",
    [
        "Проточный фильтр для воды",
        "Накопительный бак 100 литров",
    ],
)
def test_water_heater_adjective_alone_does_not_override_other_product(
    message: str,
) -> None:
    result = IntentRouterAgent()._rule_based(message, None)

    assert result.category != "water_heaters"


def test_negated_mounting_and_orientation_use_the_positive_replacement() -> None:
    router = IntentRouterAgent()

    orientation = router.route(
        "Нужен не вертикальный, а горизонтальный водонагреватель"
    )
    mounting = router.route(
        "Нужен не настенный, а напольный водонагреватель"
    )

    assert orientation.slots["orientation"] == "горизонтальный"
    assert mounting.slots["mounting"] == "напольный"


def test_unitless_volume_is_kept_when_answered_with_energy_source(
    mixed_heater_catalog: list[Product],
) -> None:
    bot = ChatOrchestrator(products=mixed_heater_catalog)
    session_id = "heater-unitless-volume"

    bot.handle_chat(session_id, "бойлер")
    bot.handle_chat(session_id, "накопительный")
    response = bot.handle_chat(
        session_id,
        "электрический, 80, только в наличии",
    )
    session = bot.sessions.get(session_id)

    assert session.slots["energy_source"] == "электрический"
    assert session.slots["volume_l"] == 80
    assert [product.sku for product in response.products] == ["RWH-80"]


def test_unitless_volume_correction_replaces_old_value() -> None:
    products = [_storage_80(), _heater("WH-100", volume_l=100)]
    bot = ChatOrchestrator(products=products)
    session_id = "heater-volume-correction"

    bot.handle_chat(
        session_id,
        "Нужен электрический накопительный водонагреватель 80 л",
    )
    response = bot.handle_chat(session_id, "Не 80, а 100")
    session = bot.sessions.get(session_id)

    assert session.slots["volume_l"] == 100
    assert [product.sku for product in response.products] == ["WH-100"]


def test_marketing_word_universal_does_not_prove_universal_orientation() -> None:
    product = _product(
        "WH-MARKETING",
        "Водонагреватель электрический накопительный TEST 80 л",
        attributes={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": "Накопительный",
            "источник энергии": "Электрический",
            "объём, л": "80",
        },
        description=(
            "Простая установка. Универсальный шаблон для быстрого монтажа."
        ),
    )
    result = FeedSearchAgent([product]).search(
        SearchQuery(
            original_text="горизонтальный водонагреватель 80 л",
            category="water_heaters",
            slots={
                "heater_type": "накопительный",
                "energy_source": "электрический",
                "volume_l": 80,
                "orientation": "горизонтальный",
            },
        )
    )

    assert result == []


def test_structured_placement_wins_over_conflicting_marketing_description() -> None:
    product = _product(
        "WH-PLACEMENT",
        "Водонагреватель электрический проточный TEST",
        attributes={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": "Проточный",
            "источник энергии": "Электрический",
            "тип размещения": "Над раковиной",
        },
        description="Компактный корпус удобно помещается под раковиной.",
    )
    search = FeedSearchAgent([product])
    base_slots = {
        "heater_type": "проточный",
        "energy_source": "электрический",
    }

    under_sink = search.search(
        SearchQuery(
            original_text="проточный водонагреватель под мойку",
            category="water_heaters",
            slots={**base_slots, "mounting": "под мойкой"},
        )
    )
    over_sink = search.search(
        SearchQuery(
            original_text="проточный водонагреватель над мойкой",
            category="water_heaters",
            slots={**base_slots, "mounting": "над мойкой"},
        )
    )

    assert under_sink == []
    assert [candidate.sku for candidate in over_sink] == ["WH-PLACEMENT"]


def test_combined_indirect_heater_is_compatible_only_with_explicit_evidence() -> None:
    product = _product(
        "RTWX-F-80",
        "Бойлер косвенного нагрева TEST 80 л",
        stock_qty=1,
        attributes={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": "Накопительный",
            "вид нагрева": "Комбинированный",
            "объём, л": "80",
        },
    )
    result = FeedSearchAgent([product]).search(
        SearchQuery(
            original_text="бойлер косвенного нагрева 80 л в наличии",
            category="water_heaters",
            slots={
                "heater_type": "косвенного нагрева",
                "energy_source": "косвенный",
                "volume_l": 80,
                "in_stock": True,
            },
            in_stock_only=True,
        )
    )

    assert [candidate.sku for candidate in result] == ["RTWX-F-80"]


def test_combined_water_heater_keeps_storage_type_separate_from_energy() -> None:
    product = _product(
        "THERMEX-COMBI-200",
        "Водонагреватель комбинированный TEST 200 л",
        attributes={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": "Комбинированный",
            "вид нагрева": "Комбинированный",
            "объём, л": "200",
        },
    )
    bot = ChatOrchestrator(products=[product])

    response = bot.handle_chat(
        "heater-combined",
        "Нужен комбинированный водонагреватель 200 л",
    )
    session = bot.sessions.get("heater-combined")

    assert session.slots["heater_type"] == "накопительный"
    assert session.slots["energy_source"] == "комбинированный"
    assert [candidate.sku for candidate in response.products] == [
        "THERMEX-COMBI-200"
    ]


def test_water_heater_volume_can_be_grounded_in_exact_description() -> None:
    product = _product(
        "RTWX-SF200",
        "Бойлер косвенного нагрева Royal Thermo SF200 White",
        stock_qty=4,
        attributes={
            "тип товара": "Водонагреватель",
            "вид нагрева": "От внешнего источника энергии",
        },
        description=(
            "Бойлер косвенного нагрева. Полезный объём бака: 200 литров."
        ),
    )
    result = FeedSearchAgent([product]).search(
        SearchQuery(
            original_text="бойлер косвенного нагрева 200 л в наличии",
            category="water_heaters",
            slots={
                "heater_type": "косвенного нагрева",
                "energy_source": "косвенный",
                "volume_l": 200,
                "in_stock": True,
            },
            in_stock_only=True,
        )
    )

    assert [candidate.sku for candidate in result] == ["RTWX-SF200"]


def test_consultant_rejects_wrong_water_heater_energy_claim() -> None:
    product = _storage_80()
    by_sku = {normalize_sku(product.sku): product}
    consultant = ConsultantAgent()

    wrong = consultant._grounding_violations(
        "Газовый водонагреватель Royal Thermo RWH-80, артикул RWH-80.",
        by_sku,
    )
    correct = consultant._grounding_violations(
        "Электрический водонагреватель Royal Thermo RWH-80, артикул RWH-80.",
        by_sku,
    )

    assert any("водонагревателя" in issue for issue in wrong)
    assert not any("водонагревателя" in issue for issue in correct)
