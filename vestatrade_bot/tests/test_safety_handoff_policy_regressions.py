"""P0 regressions for electrical safety, domain routing, and handoff consent.

These tests describe the required user-visible policy.  They intentionally keep
catalogue grounding in the fixture, while requiring the safety/handoff decision
to happen before ordinary product search or persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.config import get_settings
from app.models import Product


def _e12_product() -> Product:
    return Product(
        sku="2202211",
        name="Котел электрический Arderia E12, 12 кВт",
        category_path="Котлы электрические",
        brand="ARDERIA",
        url="https://example.test/arderia-e12",
        price=36534,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": "2202211",
            "мощность, кВт": "12",
            "тип котла": "Электрический",
        },
        description=(
            "Модель предназначена для закрытых систем отопления. "
            "Подключение: 3-фазное 380 В. "
            "Установка и подключение должны выполняться квалифицированными специалистами."
        ),
    )


def _orchestrator(
    tmp_path: Path,
    *,
    products: list[Product] | None = None,
) -> tuple[ChatOrchestrator, Path]:
    handoff_path = tmp_path / "handoff.jsonl"
    settings = get_settings().model_copy(
        update={"handoff_log_path": handoff_path},
    )
    return ChatOrchestrator(products=products or [], settings=settings), handoff_path


def test_high_power_boiler_socket_question_is_stopped_before_catalog(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path, products=[_e12_product()])

    response = bot.handle_chat(
        "electrical-risk",
        (
            "Артикул 2202211. Можно ли подключить этот котёл 12 кВт к обычной "
            "розетке 220 В кабелем 2,5 мм²? Это безопасно?"
        ),
    )

    answer = normalize_text(response.answer)
    assert response.products == []
    assert any(marker in answer for marker in ["не подключайте", "нельзя подключать"])
    assert "380" in answer
    assert any(marker in answer for marker in ["квалифицирован", "электрик", "специалист"])
    assert "группа безопасности" not in answer
    assert response.need_handoff is False
    assert not handoff_path.exists()


def test_store_promotions_question_is_not_misclassified_as_finance(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat("store-promotions", "Какие есть акции?")

    answer = normalize_text(response.answer)
    assert response.products == []
    assert "скидк" in answer or "акци" in answer
    assert not any(marker in answer for marker in ["инвестиц", "ценные бумаги", "бирж"])
    assert response.need_handoff is False
    assert not handoff_path.exists()


def test_financial_stocks_question_stays_out_of_catalog_and_handoff(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "financial-stocks",
        "Какие акции купить на 500 000 рублей, чтобы гарантированно заработать 20% за три месяца?",
    )

    answer = normalize_text(response.answer)
    assert response.products == []
    assert any(marker in answer for marker in ["финансов", "инвестиц", "ценные бумаги"])
    assert any(marker in answer for marker in ["вне", "не консульт", "не занима"])
    assert "скидки и акции" not in answer
    assert "передай менеджеру" not in answer
    assert response.need_handoff is False
    assert not handoff_path.exists()


def test_named_company_stock_is_finance_but_product_promotion_is_not(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    finance = bot.handle_chat(
        "named-stock",
        "Что думаете про акции Газпрома?",
    )
    promotion = bot.handle_chat(
        "named-stock-promotion",
        "Есть акции на котлы?",
    )

    assert "финансов" in normalize_text(finance.answer)
    assert "скидк" in normalize_text(promotion.answer) or "акци" in normalize_text(
        promotion.answer
    )
    assert not handoff_path.exists()


def test_explicit_handoff_opt_out_never_records_request(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "handoff-opt-out",
        "Не передавайте менеджеру, продолжим подбор здесь.",
    )

    answer = normalize_text(response.answer)
    assert not handoff_path.exists()
    assert response.need_handoff is False
    assert "передаю вопрос менеджеру" not in answer
    assert "я сохранил обращение" not in answer
    assert "заявка зафиксирована" not in answer


def test_transfer_without_contact_and_consent_does_not_record_or_claim_success(
    tmp_path: Path,
) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat("handoff-no-consent", "Передай менеджеру.")

    answer = normalize_text(response.answer)
    assert not handoff_path.exists()
    assert "передаю вопрос менеджеру" not in answer
    assert "я сохранил обращение" not in answer
    assert "заявка зафиксирована" not in answer
    assert "менеджер увидит" not in answer
    assert any(
        marker in answer
        for marker in ["подтверд", "соглас", "контакт", "телефон", "email"]
    )


def test_consented_handoff_returns_ticket_and_is_idempotent(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path, products=[_e12_product()])

    bot.handle_chat("handoff-idempotent", "Нужен котёл Arderia E12")
    bot.handle_chat("handoff-idempotent", "Передай менеджеру")
    contact = bot.handle_chat("handoff-idempotent", "client@example.test")

    assert contact.handoff_status == "awaiting_consent"
    assert "client@example.test" not in contact.answer
    assert not handoff_path.exists()

    submitted = bot.handle_chat(
        "handoff-idempotent",
        "Подтверждаю передачу",
    )
    repeated = bot.handle_chat(
        "handoff-idempotent",
        "Подтверждаю передачу",
    )

    assert submitted.handoff_status == "locally_recorded"
    assert submitted.handoff_ticket_id
    assert submitted.handoff_ticket_id in submitted.answer
    assert repeated.handoff_ticket_id == submitted.handoff_ticket_id
    assert "повторную запись не создаю" in normalize_text(repeated.answer)
    saved = handoff_path.read_text(encoding="utf-8")
    assert len(saved.splitlines()) == 1
    assert "client@example.test" in saved
    assert "client@example.test" not in json.loads(saved)["wanted"]
