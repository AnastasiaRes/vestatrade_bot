"""Edge regressions for gas safety and multi-turn boiler selection.

These tests intentionally exercise wording and state transitions that are easy
to miss with single-turn happy-path coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.config import get_settings
from app.models import Product


GAS_SAFETY_KEYS = {
    "gas_safety_active",
    "gas_safety_expires_at",
    "gas_safety_bathroom",
    "gas_safety_no_window",
    "gas_safety_ventilation_blocked",
}


def _boiler(
    sku: str,
    name: str,
    *,
    boiler_type: str,
    description: str,
    power_kw: int,
    voltage_v: int = 220,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=f"Котлы {boiler_type.lower()}",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=30_000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "артикул": sku,
            "тип котла": boiler_type,
            "мощность, кВт": str(power_kw),
            "напряжение питания, В": str(voltage_v),
        },
        description=description,
    )


def _gas_boiler() -> Product:
    return _boiler(
        "GAS-24",
        "Газовый котёл TestGas 24 кВт",
        boiler_type="Газовый",
        description="Газовый котёл с закрытой камерой сгорания.",
        power_kw=24,
    )


def _electric_boiler() -> Product:
    return _boiler(
        "ELEC-6",
        "Электрический котёл TestHeat 6 кВт",
        boiler_type="Электрический",
        description="Электрический котёл для системы отопления.",
        power_kw=6,
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
    return (
        ChatOrchestrator(
            products=products or [_gas_boiler(), _electric_boiler()],
            settings=settings,
        ),
        handoff_path,
    )


def _assert_gas_installation_stop(response) -> None:
    answer = normalize_text(response.answer)

    assert response.debug["intent"] == "gas_safety"
    assert response.products == []
    assert any(
        marker in answer
        for marker in [
            "нельзя",
            "не допускается",
            "не устанавливайте",
            "не ставьте",
            "не запускайте",
        ]
    )
    assert "вентиляц" in answer
    assert any(
        marker in answer
        for marker in ["специалист", "проект", "газов", "аварийн", "служб"]
    )


def test_selection_wording_cannot_bypass_gas_installation_stop(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "gas-selection-wording",
        "Подберите газовый котёл в ванную без окна, вентиляция заглушена.",
    )

    _assert_gas_installation_stop(response)


def test_referential_followup_cannot_override_active_gas_stop(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)
    session_id = "gas-referential-override"
    first = bot.handle_chat(
        session_id,
        "Можно поставить газовый котёл в ванной без окна? Вентиляция заглушена.",
    )
    _assert_gas_installation_stop(first)

    response = bot.handle_chat(session_id, "А если всё равно так сделать?")

    _assert_gas_installation_stop(response)


def test_sanuzel_and_extractor_synonyms_trigger_gas_stop(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "gas-synonyms",
        (
            "Можно поставить газовый котёл в санузле: окно отсутствует, "
            "вытяжка заглушена?"
        ),
    )

    _assert_gas_installation_stop(response)


def test_explicitly_negated_gas_leak_does_not_trigger_emergency_reply(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "negated-gas-leak",
        (
            "Утечка газа исключена, запах газа отсутствует. "
            "Продолжим подбор электрического котла."
        ),
    )
    answer = normalize_text(response.answer)

    assert response.debug["intent"] != "gas_safety"
    assert "104" not in answer
    assert "112" not in answer
    assert "возможная утечка газа" not in answer


@pytest.mark.parametrize(
    "message",
    [
        "Не понимаю, запах газа есть или нет, не уверен.",
        "Утечка газа есть или нет?",
        "Нет, запах газа всё-таки есть!",
    ],
    ids=["uncertain-smell", "leak-question", "corrected-denial"],
)
def test_uncertain_or_corrected_gas_leak_uses_emergency_reply(
    tmp_path: Path,
    message: str,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(f"uncertain-leak-{message}", message)
    answer = normalize_text(response.answer)

    assert response.debug["intent"] == "gas_safety"
    assert response.products == []
    assert "возможная утечка газа" in answer
    assert "104" in answer or "112" in answer


def test_gasobeton_does_not_turn_electric_boiler_request_into_gas_warning(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "gasobeton-false-positive",
        "Можно поставить электрический котёл в ванной без окна в доме из газобетона?",
    )
    answer = normalize_text(response.answer)

    assert response.debug["intent"] != "gas_safety"
    assert "газовым оборудованием нельзя" not in answer
    assert "аварийную газовую службу" not in answer


def test_working_unblocked_ventilation_does_not_trigger_installation_stop(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)

    response = bot.handle_chat(
        "safe-gas-ventilation",
        "Можно поставить газовый котёл в котельной? Вентиляция не закрыта и работает.",
    )
    answer = normalize_text(response.answer)

    assert response.debug["intent"] != "gas_safety"
    assert "заглушать или перекрывать вентиляцию" not in answer
    assert "аварийную газовую службу" not in answer


def test_partial_remediation_keeps_only_unresolved_gas_hazards(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)
    session_id = "partial-gas-remediation"
    first = bot.handle_chat(
        session_id,
        "Можно поставить газовый котёл в ванной без окна? Вентиляция заглушена.",
    )
    _assert_gas_installation_stop(first)

    response = bot.handle_chat(
        session_id,
        "Вентиляция восстановлена, теперь можно ставить?",
    )
    slots = bot.sessions.get(session_id).slots

    _assert_gas_installation_stop(response)
    assert slots["gas_safety_no_window"] is True
    assert slots["gas_safety_ventilation_blocked"] is False
    assert "сначала восстановите вентиляцию" not in normalize_text(response.answer)


def test_explicit_switch_to_electric_clears_gas_safety_state(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)
    session_id = "switch-from-gas-to-electric"
    first = bot.handle_chat(
        session_id,
        "Можно поставить газовый котёл в ванной без окна? Вентиляция заглушена.",
    )
    _assert_gas_installation_stop(first)

    response = bot.handle_chat(session_id, "А если взять электрический котёл?")
    slots = bot.sessions.get(session_id).slots

    assert response.debug["intent"] != "gas_safety"
    assert slots["boiler_type"] == "электрический"
    assert GAS_SAFETY_KEYS.isdisjoint(slots)
    assert "не устанавливайте и не запускайте газовый котёл" not in normalize_text(
        response.answer
    )


def test_gas_safety_cancels_stale_handoff_and_late_confirmation(
    tmp_path: Path,
) -> None:
    bot, handoff_path = _orchestrator(tmp_path)
    session_id = "stale-handoff-after-gas-stop"

    requested = bot.handle_chat(
        session_id,
        "Передай менеджеру, мой телефон +7 999 000-00-00.",
    )
    pending = bot.sessions.get(session_id)
    assert requested.handoff_status == "awaiting_consent"
    assert pending.pending_handoff is not None
    assert not handoff_path.exists()

    safety = bot.handle_chat(
        session_id,
        "Можно поставить газовый котёл в ванной без окна? Вентиляция заглушена.",
    )
    state_after_safety = bot.sessions.get(session_id)
    _assert_gas_installation_stop(safety)
    assert state_after_safety.pending_handoff is None
    assert state_after_safety.handoff_status == "none"

    late_confirmation = bot.handle_chat(session_id, "Подтверждаю передачу.")
    final_state = bot.sessions.get(session_id)

    assert late_confirmation.handoff_status != "locally_recorded"
    assert late_confirmation.handoff_ticket_id is None
    assert final_state.pending_handoff is None
    assert final_state.handoff_ticket_id is None
    assert not handoff_path.exists()


@pytest.mark.parametrize(
    ("reply", "expected_type"),
    [
        ("Не электрический, газовый", "газовый"),
        ("Электрический не нужен, газовый", "газовый"),
        ("Электричество есть, но хочу газовый", "газовый"),
        ("Газовый не нужен, электрический", "электрический"),
        ("Не газовый, электрический", "электрический"),
    ],
    ids=[
        "reject-electric",
        "electric-not-needed",
        "electricity-available-but-wants-gas",
        "gas-not-needed",
        "reject-gas",
    ],
)
def test_boiler_type_reply_respects_negation_and_explicit_choice(
    tmp_path: Path,
    reply: str,
    expected_type: str,
) -> None:
    bot, _ = _orchestrator(tmp_path)
    session_id = f"boiler-type-{expected_type}-{reply}"
    first = bot.handle_chat(session_id, "Нужен котёл")
    assert "газовый или электрический" in normalize_text(first.answer)

    response = bot.handle_chat(session_id, reply)

    assert response.debug["slots"]["boiler_type"] == expected_type
    assert "газовый или электрический" not in normalize_text(response.answer)


def test_uncertain_boiler_type_reply_does_not_invent_electric_choice(
    tmp_path: Path,
) -> None:
    bot, _ = _orchestrator(tmp_path)
    session_id = "uncertain-boiler-type"
    first = bot.handle_chat(session_id, "Нужен котёл")
    assert "газовый или электрический" in normalize_text(first.answer)

    response = bot.handle_chat(
        session_id,
        "Не знаю, газовый или электрический.",
    )
    answer = normalize_text(response.answer)

    assert "boiler_type" not in response.debug["slots"]
    assert response.products == []
    assert "газ" in answer and "электр" in answer


def test_multiturn_builtin_pump_refinement_filters_previous_boiler_results(
    tmp_path: Path,
) -> None:
    with_pump = _boiler(
        "WITH-PUMP",
        "Электрический котёл TestHeat Pump 6 кВт",
        boiler_type="Электрический",
        description="Встроенный циркуляционный насос.",
        power_kw=6,
    )
    without_pump = _boiler(
        "WITHOUT-PUMP",
        "Электрический котёл TestHeat Base 6 кВт",
        boiler_type="Электрический",
        description="Насос не встроен; приобретается отдельно.",
        power_kw=6,
    )
    bot, _ = _orchestrator(
        tmp_path,
        products=[with_pump, without_pump],
    )
    session_id = "multiturn-builtin-refinement"

    first = bot.handle_chat(
        session_id,
        "Нужен электрический котёл на 50 м², 220 В.",
    )
    assert {product.sku for product in first.products} == {
        "WITH-PUMP",
        "WITHOUT-PUMP",
    }

    response = bot.handle_chat(
        session_id,
        (
            "Электрический, 220 В, до 40000 рублей, "
            "только со встроенным насосом."
        ),
    )

    assert response.debug["category"] == "boilers"
    assert response.debug["intent"] != "complectation"
    assert response.debug["slots"]["required_builtin_parts"] == ["насос"]
    assert response.debug["slots"]["max_price"] == 40_000
    assert "sku" not in response.debug["slots"]
    assert [product.sku for product in response.products] == ["WITH-PUMP"]
    assert "по какой модели проверить комплектацию" not in normalize_text(
        response.answer
    )
