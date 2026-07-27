from __future__ import annotations

from app.agents.feed_search import FeedSearchAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.product_card import ProductCardAgent
from app.models import Product, SearchQuery


def _boiler(
    sku: str,
    *,
    price: float,
    name_suffix: str = "",
    wifi: str | None = None,
    contours: str = "Двухконтурный",
) -> Product:
    attributes = {
        "тип котла": "Электрический",
        "количество контуров": contours,
        "мощность, кВт": "12",
    }
    if wifi is not None:
        attributes["Wi-Fi"] = wifi
    return Product(
        sku=sku,
        name=f"Котел электрический Test 12 кВт{name_suffix}",
        category_path="Котлы электрические",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized=attributes,
    )


def _pump(sku: str, price: float) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {sku} 25/6 180",
        category_path="Насосы циркуляционные",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "напор": "6 м",
            "монтажная длина": "180 мм",
        },
    )


def test_search_applies_price_bounds_before_returning_products() -> None:
    products = [
        _pump("P-4186", 4186),
        _pump("P-4777", 4777),
        _pump("P-10521", 10521),
    ]
    query = SearchQuery(
        original_text="насос 25/6 180, бюджет до 6000",
        category="pumps",
        slots={
            "pump_type": "циркуляционный",
            "connection_size": 25,
            "head_m": 6,
            "mounting_length_mm": 180,
            "min_price": "4 500 руб.",
            "max_price": "6 000 руб.",
        },
    )

    results = FeedSearchAgent(products).search(query)

    assert [product.sku for product in results] == ["P-4777"]


def test_feature_constraints_are_hard_and_wifi_false_is_not_positive() -> None:
    wifi_name = _boiler("WIFI-NAME", price=36000, name_suffix=" Wi-Fi")
    wifi_yes = _boiler("WIFI-YES", price=35000, wifi="Да")
    wifi_no = _boiler("WIFI-NO", price=34000, wifi="Нет")
    unknown = _boiler("WIFI-UNKNOWN", price=33000)
    agent = FeedSearchAgent([wifi_name, wifi_yes, wifi_no, unknown])

    without_wifi = agent.search(
        SearchQuery(
            original_text="двухконтурный электрический котел без wi-fi",
            category="boilers",
            slots={
                "boiler_type": "электрический",
                "contours": "двухконтурный",
                "excluded_features": ["wi-fi"],
            },
        )
    )
    with_wifi = agent.search(
        SearchQuery(
            original_text="двухконтурный электрический котел с wi-fi",
            category="boilers",
            slots={
                "boiler_type": "электрический",
                "contours": "двухконтурный",
                "required_features": {"wifi": True},
            },
        )
    )

    # An omitted Wi-Fi field is unknown, not proof that the boiler has no Wi-Fi.
    assert {product.sku for product in without_wifi} == {"WIFI-NO"}
    assert {product.sku for product in with_wifi} == {"WIFI-NAME", "WIFI-YES"}


def test_hard_constraints_apply_to_consult_retrieval_and_result_limit() -> None:
    products = [
        _boiler("BUDGET", price=30000, wifi="Нет"),
        _boiler("OVER", price=41000, wifi="Нет"),
        _boiler("WIFI", price=29000, wifi="Да"),
    ]
    agent = FeedSearchAgent(products)

    results = agent.retrieve_for_consult(
        ["boilers"],
        {
            "max_price": 37000,
            "excluded_features": ["wifi"],
            "result_limit": 1,
        },
        per_category=4,
    )

    assert [product.sku for product in results] == ["BUDGET"]


def test_explicit_no_alternatives_stops_relaxed_alternative_search() -> None:
    one_contour = _boiler(
        "ONE",
        price=30000,
        wifi="Нет",
        contours="Одноконтурный",
    )
    agent = FeedSearchAgent([one_contour])
    query = SearchQuery(
        original_text="нужен только двухконтурный, альтернативы не показывать",
        category="boilers",
        slots={
            "boiler_type": "электрический",
            "contours": "двухконтурный",
            "allow_alternatives": False,
        },
    )

    assert agent.search(query) == []
    assert agent.search_alternatives(query) == []


def test_guardrails_reject_price_feature_and_result_limit_violations() -> None:
    wifi = _boiler("WIFI", price=41000, wifi="Да")
    no_wifi = _boiler("NO-WIFI", price=30000, wifi="Нет")
    query = SearchQuery(
        original_text="до 37000, без wifi, один вариант",
        category="boilers",
        slots={
            "max_price": 37000,
            "excluded_features": ["wifi"],
            "result_limit": 1,
        },
    )
    card_agent = ProductCardAgent()
    cards = [
        card
        for product in [wifi, no_wifi]
        if (card := card_agent.build_card(product, query)) is not None
    ]

    result = GuardrailsAgent().validate_cards(cards, [wifi, no_wifi], query)

    assert not result.ok
    assert "response has 2 cards but result_limit is 1" in result.issues
    assert "card WIFI price 41000 exceeds max_price 37000" in result.issues
    assert "card WIFI contains excluded feature wifi" in result.issues


def test_guardrails_require_positive_feature_evidence() -> None:
    unknown = _boiler("UNKNOWN", price=30000)
    query = SearchQuery(
        original_text="котел обязательно с wifi",
        category="boilers",
        slots={"required_features": ["wifi"]},
    )
    card = ProductCardAgent().build_card(unknown, query)
    assert card is not None

    result = GuardrailsAgent().validate_cards([card], [unknown], query)

    assert not result.ok
    assert "card UNKNOWN does not confirm required feature wifi" in result.issues
