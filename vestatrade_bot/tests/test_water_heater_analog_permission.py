from __future__ import annotations

import pytest

from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _water_heater(
    sku: str,
    *,
    volume_l: int,
    stock_qty: int = 3,
    price: float = 9_000,
    heater_type: str = "Накопительный",
    energy_source: str = "Электрический",
) -> Product:
    return Product(
        sku=sku,
        name=(
            f"Водонагреватель {energy_source.lower()} "
            f"{heater_type.lower()} TEST {volume_l} л"
        ),
        category_path="Водонагреватели",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": heater_type,
            "источник энергии": energy_source,
            "объём, л": str(volume_l),
        },
    )


@pytest.mark.parametrize(
    "permission",
    [
        "можно аналог",
        "аналоги допустимы",
        "альтернативы допустимы",
    ],
)
def test_explicit_water_heater_analog_permission_is_parsed(
    permission: str,
) -> None:
    result = IntentRouterAgent().route(
        "Нужен электрический накопительный водонагреватель 80 л "
        f"до 10 000 рублей, {permission}"
    )

    assert result.category == "water_heaters"
    assert result.slots["allow_alternatives"] is True
    assert result.slots["volume_l"] == 80
    assert result.slots["heater_type"] == "накопительный"
    assert result.slots["energy_source"] == "электрический"
    assert result.slots["max_price"] == 10_000


def test_permitted_analogs_do_not_relax_water_heater_hard_constraints() -> None:
    bot = ChatOrchestrator(
        products=[
            _water_heater("WRONG-VOLUME", volume_l=50),
            _water_heater("OUT-OF-STOCK", volume_l=80, stock_qty=0),
            _water_heater(
                "WRONG-SOURCE",
                volume_l=80,
                energy_source="Газовый",
            ),
        ]
    )

    response = bot.handle_chat(
        "heater-analog-hard-constraints",
        "Нужен электрический накопительный водонагреватель 80 л до 10 000 рублей, "
        "только в наличии, можно аналог",
    )
    session = bot.sessions.get("heater-analog-hard-constraints")

    assert response.products == []
    assert session.slots["allow_alternatives"] is True
    assert session.slots["volume_l"] == 80
    assert session.slots["heater_type"] == "накопительный"
    assert session.slots["energy_source"] == "электрический"
    assert session.slots["max_price"] == 10_000
    assert session.slots["in_stock"] is True
    assert "какое одно условие можно изменить" in response.answer.lower()
    assert "разрешите аналоги" not in response.answer.lower()


def test_followup_permission_overrides_previous_search_branch_but_not_constraints() -> None:
    bot = ChatOrchestrator(
        products=[
            _water_heater("WRONG-VOLUME", volume_l=50),
            _water_heater("OUT-OF-STOCK", volume_l=80, stock_qty=0),
        ]
    )
    initial = bot.handle_chat(
        "heater-analog-followup",
        "Нужен электрический накопительный водонагреватель 80 л до 10 000 рублей, "
        "только в наличии",
    )

    response = bot.handle_chat(
        "heater-analog-followup",
        "альтернативы допустимы",
    )
    session = bot.sessions.get("heater-analog-followup")

    assert initial.products == []
    assert response.products == []
    assert session.category == "water_heaters"
    assert session.slots["allow_alternatives"] is True
    assert session.slots["volume_l"] == 80
    assert session.slots["max_price"] == 10_000
    assert session.slots["in_stock"] is True
    assert "какое одно условие можно изменить" in response.answer.lower()


def test_analog_followup_after_result_asks_which_constraint_may_change() -> None:
    bot = ChatOrchestrator(
        products=[
            _water_heater("MATCH-80", volume_l=80),
            _water_heater("WRONG-VOLUME", volume_l=50),
        ]
    )
    initial = bot.handle_chat(
        "heater-analog-after-result",
        "Нужен электрический накопительный водонагреватель 80 л, только в наличии",
    )

    response = bot.handle_chat(
        "heater-analog-after-result",
        "можно аналог",
    )

    assert [card.sku for card in initial.products] == ["MATCH-80"]
    assert response.products == []
    assert "какое одно условие можно изменить" in response.answer.lower()
    assert "разрешите аналоги" not in response.answer.lower()
