"""Regressions for gas-boiler installation safety and boiler-type context."""

from __future__ import annotations

from pathlib import Path

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.config import get_settings
from app.models import Product


def _gas_boiler() -> Product:
    return Product(
        sku="GAS-24",
        name="Газовый котёл TestGas 24 кВт",
        category_path="Котлы газовые",
        brand="TESTGAS",
        url="https://example.test/gas-24",
        price=50_000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "GAS-24",
            "тип котла": "Газовый",
            "мощность, кВт": "24",
            "камера сгорания": "Закрытая",
        },
        description="Газовый котёл с закрытой камерой сгорания.",
    )


def _electric_boiler() -> Product:
    return Product(
        sku="ELEC-9",
        name="Электрический котёл TestHeat 9 кВт",
        category_path="Котлы электрические",
        brand="TESTHEAT",
        url="https://example.test/electric-9",
        price=35_000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "ELEC-9",
            "тип котла": "Электрический",
            "мощность, кВт": "9",
        },
        description="Электрический котёл для системы отопления.",
    )


def _orchestrator(tmp_path: Path) -> ChatOrchestrator:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"},
    )
    return ChatOrchestrator(
        products=[_gas_boiler(), _electric_boiler()],
        settings=settings,
    )


def _assert_gas_installation_stop(response) -> None:
    answer = normalize_text(response.answer)

    assert response.products == []
    assert any(
        marker in answer
        for marker in [
            "нельзя",
            "не допускается",
            "не устанавливайте",
            "не ставьте",
            "не используйте",
            "не запускайте",
        ]
    )
    assert "вентиляц" in answer
    assert any(
        marker in answer
        for marker in ["специалист", "проект", "газов", "аварийн", "служб"]
    )


def test_gas_boiler_in_unventilated_windowless_bathroom_is_stopped_before_catalog(
    tmp_path: Path,
) -> None:
    bot = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "gas-boiler-unsafe-room",
        (
            "Можно ли поставить газовый котёл с закрытой камерой в ванной без окна? "
            "Вентиляция заглушена."
        ),
    )

    _assert_gas_installation_stop(response)


def test_gas_boiler_installation_warning_persists_on_followup(
    tmp_path: Path,
) -> None:
    bot = _orchestrator(tmp_path)
    session_id = "gas-boiler-unsafe-followup"
    bot.handle_chat(
        session_id,
        (
            "Можно ли поставить газовый котёл с закрытой камерой в ванной без окна? "
            "Вентиляция заглушена."
        ),
    )

    response = bot.handle_chat(
        session_id,
        "А если окно не делать и оставить вентиляцию заглушенной, можно всё равно?",
    )

    _assert_gas_installation_stop(response)
    assert "газовый или электрический" not in normalize_text(response.answer)


def test_electric_boiler_choice_is_not_asked_again(tmp_path: Path) -> None:
    bot = _orchestrator(tmp_path)
    session_id = "boiler-type-context"

    first = bot.handle_chat(session_id, "Нужен котёл")
    assert "газовый или электрический" in normalize_text(first.answer)

    response = bot.handle_chat(session_id, "Электрический")

    assert response.debug["slots"]["boiler_type"] == "электрический"
    assert "газовый или электрический" not in normalize_text(response.answer)


def test_electric_choice_with_repeated_constraints_keeps_context_and_does_not_loop(
    tmp_path: Path,
) -> None:
    electric = _electric_boiler().model_copy(
        update={
            "attributes_normalized": {
                **_electric_boiler().attributes_normalized,
                "напряжение питания, В": "220",
            },
            "description": (
                "Электрический котёл для системы отопления. "
                "Встроенный циркуляционный насос."
            ),
        }
    )
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"},
    )
    bot = ChatOrchestrator(
        products=[_gas_boiler(), electric],
        settings=settings,
    )
    session_id = "boiler-type-full-context"

    first = bot.handle_chat(
        session_id,
        (
            "Нужен котёл на дом 100 квадратов, только 220 вольт, бюджет до "
            "40 тысяч, со встроенным насосом, один вариант и только в наличии."
        ),
    )
    assert "газовый или электрический" in normalize_text(first.answer)

    response = bot.handle_chat(
        session_id,
        (
            "Электрический. Остальные условия сохрани: 220 В, до 40 000, "
            "встроенный насос, один вариант, в наличии."
        ),
    )
    slots = response.debug["slots"]

    assert "газовый или электрический" not in normalize_text(response.answer)
    assert slots["boiler_type"] == "электрический"
    assert slots["voltage_v"] == 220
    assert slots["max_price"] == 40_000
    assert slots["in_stock"] is True
    assert slots["required_builtin_parts"] == ["насос"]
    assert [product.sku for product in response.products] == ["ELEC-9"]
    answer = normalize_text(response.answer)
    assert "220 в" in answer
    assert "встроено насос" in answer
    assert "только в наличии" in answer
