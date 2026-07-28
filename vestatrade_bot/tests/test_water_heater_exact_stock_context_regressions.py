from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


def _water_heater(
    sku: str,
    name: str,
    *,
    volume_l: int = 80,
    heater_type: str = "Накопительный",
    energy_source: str = "Электрический",
    stock_qty: int = 3,
    mounting: str = "Настенный",
    orientation: str = "Вертикальная",
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Водонагреватели",
        brand="THERMEX",
        url=f"https://example.test/{sku.lower()}",
        price=15_000,
        stock_status="в наличии" if stock_qty > 0 else "нет в наличии",
        stock_qty=stock_qty,
        attributes_normalized={
            "тип товара": "Водонагреватель",
            "тип водонагревателя": heater_type,
            "источник энергии": energy_source,
            "объём, л": str(volume_l),
            "монтаж": mounting,
            "ориентация": orientation,
        },
    )


def _catalog() -> list[Product]:
    return [
        _water_heater(
            "111086",
            "Водонагреватель THERMEX TitaniumHeat 80 V",
            stock_qty=0,
        ),
        _water_heater(
            "COMPATIBLE-80",
            "Водонагреватель THERMEX SmartHeat 80 V",
        ),
        _water_heater(
            "FLOW-80",
            "Водонагреватель THERMEX Ton 3000",
            heater_type="Проточный",
        ),
        _water_heater(
            "STORAGE-50",
            "Водонагреватель THERMEX MK 50 V",
            volume_l=50,
        ),
        _water_heater(
            "GAS-80",
            "Газовый водонагреватель THERMEX Gas 80",
            energy_source="Газовый",
        ),
    ]


def test_only_in_stock_followup_keeps_exact_unavailable_identity_hidden() -> None:
    bot = ChatOrchestrator(products=_catalog())
    session_id = "exact-name-stock"

    exact = bot.handle_chat(
        session_id,
        "Водонагреватель THERMEX TitaniumHeat 80 V",
    )
    stock = bot.handle_chat(session_id, "только в наличии")
    session = bot.sessions.get(session_id)

    assert [card.sku for card in exact.products] == ["111086"]
    assert stock.products == []
    assert "111086" in stock.answer
    assert "нет в наличии" in stock.answer.lower()
    assert session.slots["in_stock"] is True
    assert [card.sku for card in session.last_products] == ["111086"]


def test_analogs_after_exact_stock_followup_keep_verified_compatibility() -> None:
    bot = ChatOrchestrator(products=_catalog())
    session_id = "exact-name-compatible-analogs"

    bot.handle_chat(
        session_id,
        "Водонагреватель THERMEX TitaniumHeat 80 V",
    )
    bot.handle_chat(session_id, "только в наличии")
    analogs = bot.handle_chat(session_id, "Покажи аналоги")
    session = bot.sessions.get(session_id)

    assert [card.sku for card in analogs.products] == ["COMPATIBLE-80"]
    assert all(card.stock_status == "в наличии" for card in analogs.products)
    assert "FLOW-80" not in {card.sku for card in analogs.products}
    assert "STORAGE-50" not in {card.sku for card in analogs.products}
    assert "GAS-80" not in {card.sku for card in analogs.products}
    assert session.slots["heater_type"] == "накопительный"
    assert session.slots["energy_source"] == "электрический"
    assert session.slots["volume_l"] == 80


def test_in_stock_exact_sku_followup_returns_only_same_identity() -> None:
    bot = ChatOrchestrator(products=_catalog())
    session_id = "exact-sku-stock"

    exact = bot.handle_chat(session_id, "COMPATIBLE-80")
    stock = bot.handle_chat(session_id, "а он в наличии?")

    assert [card.sku for card in exact.products] == ["COMPATIBLE-80"]
    assert [card.sku for card in stock.products] == ["COMPATIBLE-80"]
    assert "COMPATIBLE-80" in stock.answer
    assert "FLOW-80" not in {card.sku for card in stock.products}
