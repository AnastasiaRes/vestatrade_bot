"""Edge regressions for electrical safety and handoff consent state."""

from __future__ import annotations

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
            "Подключение: трёхфазное 380 В. "
            "Установка и подключение выполняются квалифицированным специалистом."
        ),
    )


def _e9_product() -> Product:
    return Product(
        sku="2202210",
        name="Котел электрический Arderia E9, 9 кВт",
        category_path="Котлы электрические",
        brand="ARDERIA",
        url="https://example.test/arderia-e9",
        price=33500,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={
            "артикул": "2202210",
            "мощность, кВт": "9",
            "тип котла": "Электрический",
        },
        description=(
            "Подключение: однофазное 220 В. "
            "Установка и подключение выполняются квалифицированным специалистом."
        ),
    )


def _orchestrator(tmp_path: Path) -> tuple[ChatOrchestrator, Path]:
    handoff_path = tmp_path / "handoff.jsonl"
    settings = get_settings().model_copy(
        update={"handoff_log_path": handoff_path},
    )
    return (
        ChatOrchestrator(
            products=[_e12_product(), _e9_product()],
            settings=settings,
        ),
        handoff_path,
    )


def _submit_handoff(
    bot: ChatOrchestrator,
    session_id: str,
) -> str:
    bot.handle_chat(session_id, "Нужен электрический котёл Arderia")
    bot.handle_chat(session_id, "Передай менеджеру")
    contact = bot.handle_chat(session_id, "client@example.test")
    assert contact.handoff_status == "awaiting_consent"

    submitted = bot.handle_chat(session_id, "Подтверждаю передачу")
    assert submitted.handoff_status == "locally_recorded"
    assert submitted.handoff_ticket_id
    return submitted.handoff_ticket_id


def test_explicit_consent_refusal_blocks_later_confirmation(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)
    session_id = "handoff-refusal"

    bot.handle_chat(session_id, "Нужен электрический котёл Arderia")
    bot.handle_chat(session_id, "Передай менеджеру")
    contact = bot.handle_chat(session_id, "client@example.test")
    assert contact.handoff_status == "awaiting_consent"

    refused = bot.handle_chat(session_id, "Нет, я не согласен на передачу")
    late_confirmation = bot.handle_chat(session_id, "Подтверждаю передачу")

    assert refused.handoff_status != "locally_recorded"
    assert late_confirmation.handoff_status != "locally_recorded"
    assert late_confirmation.handoff_ticket_id is None
    assert not handoff_path.exists()


def test_opt_out_after_submission_acknowledges_existing_request(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)
    session_id = "handoff-post-submit-opt-out"
    ticket_id = _submit_handoff(bot, session_id)

    opt_out = bot.handle_chat(
        session_id,
        "Не передавайте менеджеру, отмените обращение.",
    )
    answer = normalize_text(opt_out.answer)

    assert ticket_id in opt_out.answer
    assert "локальный черновик уже сохранен" in answer
    assert "заявку не создаю" not in answer
    assert "заявка не создана" not in answer
    assert len(handoff_path.read_text(encoding="utf-8").splitlines()) == 1


def test_bare_e12_sku_socket_question_hits_safety_gate(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "bare-sku-electrical-risk",
        "Артикул 2202211. Можно подключить к обычной розетке 220 В?",
    )
    answer = normalize_text(response.answer)

    assert response.products == []
    assert any(marker in answer for marker in ["не подключайте", "нельзя подключать"])
    assert "380" in answer
    assert any(marker in answer for marker in ["электрик", "квалифицирован", "специалист"])
    assert not handoff_path.exists()


def test_electrical_followup_stays_safe_but_passport_question_is_not_hijacked(
    tmp_path: Path,
) -> None:
    bot, handoff_path = _orchestrator(tmp_path)
    session_id = "electrical-followup-boundary"

    bot.handle_chat(
        session_id,
        "Котёл 2202211 можно подключить к обычной розетке?",
    )
    adapter = bot.handle_chat(session_id, "А через переходник?")
    passport = bot.handle_chat(
        session_id,
        "Есть ли в этом котле группа безопасности?",
    )

    assert "не подключайте" in normalize_text(adapter.answer)
    assert "380" in adapter.answer
    assert "380" not in passport.answer
    assert "группа безопасности" in normalize_text(passport.answer)
    assert not handoff_path.exists()


def test_explicit_e9_followup_does_not_reuse_e12_safety_target(tmp_path: Path) -> None:
    bot, handoff_path = _orchestrator(tmp_path)
    session_id = "switch-electrical-target"

    first = bot.handle_chat(
        session_id,
        "Котёл 2202211 Arderia E12 12 кВт можно подключить к обычной розетке 220 В?",
    )
    second = bot.handle_chat(
        session_id,
        "А Arderia E9 можно подключить к 220 В?",
    )
    first_answer = normalize_text(first.answer)
    second_answer = normalize_text(second.answer)

    assert "380" in first_answer
    assert "e12" not in second_answer
    assert "2202211" not in second_answer
    assert "380" not in second_answer
    assert second.products == []
    assert not handoff_path.exists()


def test_product_consultation_request_does_not_start_manager_handoff(
    tmp_path: Path,
) -> None:
    bot, handoff_path = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "ordinary-product-consultation",
        "Нужна консультация по котлу.",
    )
    answer = normalize_text(response.answer)

    assert response.need_handoff is False
    assert response.handoff_status not in {"awaiting_contact", "awaiting_consent"}
    assert "оставьте телефон" not in answer
    assert "подтвердите согласие" not in answer
    assert not handoff_path.exists()
