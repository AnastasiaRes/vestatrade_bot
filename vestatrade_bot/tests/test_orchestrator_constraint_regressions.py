from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


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


def _boiler(sku: str, price: float, wifi: str | None) -> Product:
    attributes = {
        "тип котла": "Электрический",
        "количество контуров": "Двухконтурный",
        "мощность, кВт": "12",
    }
    if wifi is not None:
        attributes["Wi-Fi"] = wifi
    return Product(
        sku=sku,
        name=f"Котел электрический {sku} 12 кВт",
        category_path="Котлы электрические",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized=attributes,
    )


def test_orchestrator_parses_budget_and_never_returns_over_limit() -> None:
    bot = ChatOrchestrator(
        products=[
            _pump("P-4186", 4186),
            _pump("P-4777", 4777),
            _pump("P-10521", 10521),
        ]
    )

    response = bot.handle_chat(
        "budget-end-to-end",
        "Циркуляционный насос 25/6 180, бюджет до 6 000 рублей",
    )

    assert response.products
    assert response.debug["slots"]["max_price"] == 6000
    assert all(product.price <= 6000 for product in response.products)
    assert "P-10521" not in response.answer


def test_followup_price_and_without_wifi_refilter_previous_boiler_search() -> None:
    bot = ChatOrchestrator(
        products=[
            _boiler("NO-WIFI", 36000, "Нет"),
            _boiler("WITH-WIFI", 35000, "Да"),
            _boiler("UNKNOWN-WIFI", 34000, None),
            _boiler("OVER-BUDGET", 39000, "Нет"),
        ]
    )
    bot.handle_chat(
        "boiler-hard-refinement",
        "Электрический двухконтурный котёл 12 кВт",
    )

    response = bot.handle_chat(
        "boiler-hard-refinement",
        "Без Wi-Fi и не дороже 37 000 рублей",
    )

    assert [product.sku for product in response.products] == ["NO-WIFI"]
    assert response.debug["slots"]["max_price"] == 37000
    assert response.debug["slots"]["excluded_features"] == ["wifi"]


def test_name_one_cheapest_returns_exactly_one_previous_candidate() -> None:
    bot = ChatOrchestrator(
        products=[
            _pump("P-6100", 6100),
            _pump("P-4777", 4777),
            _pump("P-5200", 5200),
        ]
    )
    first = bot.handle_chat(
        "single-cheapest",
        "Покажи циркуляционные насосы 25/6 180",
    )
    assert len(first.products) > 1

    response = bot.handle_chat(
        "single-cheapest",
        "Назови один самый дешёвый подходящий",
    )

    assert [product.sku for product in response.products] == ["P-4777"]
    assert "P-6100" not in response.answer
    assert "P-5200" not in response.answer
    assert "Альтернатива" not in response.answer


def test_single_choice_applies_new_budget_before_choosing() -> None:
    bot = ChatOrchestrator(
        products=[
            _pump("P-6100", 6100),
            _pump("P-4777", 4777),
            _pump("P-5200", 5200),
        ]
    )
    bot.handle_chat(
        "single-new-budget",
        "Покажи циркуляционные насосы 25/6 180",
    )

    response = bot.handle_chat(
        "single-new-budget",
        "Назови один по цене до 5 000 рублей",
    )

    assert [product.sku for product in response.products] == ["P-4777"]
    assert response.debug["slots"]["max_price"] == 5000
    assert "P-6100" not in response.answer
    assert "P-5200" not in response.answer
