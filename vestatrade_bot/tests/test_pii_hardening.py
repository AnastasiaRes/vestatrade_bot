from __future__ import annotations

import json

from app.agents.semantic_interpreter import semantic_context
from app.chat_logger import ChatLogger
from app.diagnostic_telemetry import _json_safe
from app.models import ChatResponse, SessionState
from app.openrouter_client import OpenRouterClient
from app.pii import redact_pii_for_model


def test_russian_identity_and_precise_address_are_redacted_but_city_is_kept() -> None:
    source = (
        "Получатель: Анна Сергеевна Петрова; доставка в Самару, "
        "ул. 40 лет Победы, дом 15к1, квартира 42. "
        "Нужен насос Grundfos ALPHA2 25-40 180."
    )

    redacted = redact_pii_for_model(source)

    assert "Анна" not in redacted
    assert "Петрова" not in redacted
    assert "40 лет Победы" not in redacted
    assert "15к1" not in redacted
    assert "42" not in redacted
    assert "Получатель: [name redacted]" in redacted
    assert "Самару, [address redacted]" in redacted
    assert "Grundfos ALPHA2 25-40 180" in redacted


def test_lowercase_introduction_is_redacted_when_it_is_an_explicit_clause() -> None:
    source = "меня зовут иван петров, подберите котёл"

    assert redact_pii_for_model(source) == (
        "меня зовут [name redacted], подберите котёл"
    )
    assert "иван иванов" not in redact_pii_for_model(
        "получатель иван иванов телефон +7 999 123-45-67"
    )


def test_english_identity_and_address_are_redacted_without_losing_city() -> None:
    source = (
        "My name is John O'Connor. Ship to London, "
        "221B Baker Street, Apt 4. Need a 25/6-130 circulation pump."
    )

    redacted = redact_pii_for_model(source)

    assert "John" not in redacted
    assert "O'Connor" not in redacted
    assert "Baker" not in redacted
    assert "Apt 4" not in redacted
    assert "My name is [name redacted]" in redacted
    assert "London, [address redacted]" in redacted
    assert "25/6-130" in redacted


def test_labeled_address_without_street_marker_keeps_city_and_next_sentence() -> None:
    source = (
        "Адрес доставки: Москва, Тверская 12, кв. 3. "
        "Нужен насос 25-40 180."
    )

    assert redact_pii_for_model(source) == (
        "Адрес доставки: Москва, [address redacted]. "
        "Нужен насос 25-40 180."
    )


def test_phone_email_and_address_classes_are_redacted_together() -> None:
    source = (
        "Контакт customer@example.test, +7 999 123-45-67, "
        "офис 314, получатель — Иван Иванов."
    )

    redacted = redact_pii_for_model(source)

    assert "customer@example.test" not in redacted
    assert "+7 999 123-45-67" not in redacted
    assert "314" not in redacted
    assert "Иван Иванов" not in redacted
    assert "[email redacted]" in redacted
    assert "[phone redacted]" in redacted
    assert "[address redacted]" in redacted
    assert "[name redacted]" in redacted


def test_product_models_dimensions_and_identifiers_are_not_pii() -> None:
    source = (
        "Насос Grundfos ALPHA2 25-40 180; котёл Baxi ECO Four 24 F; "
        "труба PPR 20x3,4 длиной 4 м; муфта VTp.704.0.040025; "
        "заказ № 1234567890; улица 40 лет Победы."
        " Адрес магазина в карточке товара отсутствует."
        " Конвектор SCN 110.240.1000; шкаф 670-760/494/125-195;"
        " AquaBast Line Квартира 1/2; THERMEX Flat 80 V Combi."
    )

    assert redact_pii_for_model(source) == source


def test_redaction_is_idempotent() -> None:
    source = (
        "Recipient: Jane Doe; Moscow, 12 Main Street, apartment 5; "
        "jane@example.test"
    )
    once = redact_pii_for_model(source)

    assert redact_pii_for_model(once) == once


def test_openrouter_transport_sanitizes_every_role_without_mutating_input() -> None:
    source = [
        {
            "role": "system",
            "content": "Recipient: Jane Doe, 12 Main Street, Apt 4",
        },
        {
            "role": "user",
            "content": "Получатель Анна Петрова, ул. Ленина 12, кв. 7",
        },
    ]

    sanitized = OpenRouterClient.sanitize_messages(source)
    serialized = json.dumps(sanitized, ensure_ascii=False)

    assert "Jane Doe" not in serialized
    assert "Main Street" not in serialized
    assert "Анна Петрова" not in serialized
    assert "Ленина" not in serialized
    assert "Jane Doe" in source[0]["content"]
    assert "Анна Петрова" in source[1]["content"]


def test_semantic_context_redacts_recent_dialogue_but_keeps_delivery_region() -> None:
    state = SessionState(
        session_id="pii-context",
        history=[
            {
                "role": "user",
                "content": (
                    "Меня зовут Анна Петрова. Доставка в Казань, "
                    "ул. Баумана 10, кв. 2"
                ),
            }
        ],
    )

    serialized = json.dumps(semantic_context(state), ensure_ascii=False)

    assert "Анна Петрова" not in serialized
    assert "Баумана" not in serialized
    assert "кв. 2" not in serialized
    assert "Казань" in serialized
    assert "[name redacted]" in serialized
    assert "[address redacted]" in serialized


def test_diagnostic_json_boundary_scrubs_nested_raw_text() -> None:
    source = (
        "Получатель: Иван Петров, Москва, проспект Мира, дом 10, кв. 5; "
        "насос 25-40 180"
    )
    payload = {
        "current_message": source,
        "semantic": {"evidence": source},
        "events": [{"value": source}],
        "Получатель Иван Петров": "safe-value",
    }

    serialized = json.dumps(_json_safe(payload), ensure_ascii=False)

    assert "Иван Петров" not in serialized
    assert "проспект Мира" not in serialized
    assert "дом 10" not in serialized
    assert "кв. 5" not in serialized
    assert serialized.count("[name redacted]") == 4
    assert serialized.count("[address redacted]") == 3
    assert serialized.count("насос 25-40 180") == 3


def test_chat_transcript_uses_the_shared_name_and_address_redactor(tmp_path) -> None:
    logger = ChatLogger(tmp_path)
    response = ChatResponse(
        session_id="pii-log",
        answer="Записал получателя Анну Петрову и адрес ул. Ленина 12, кв. 7.",
    )

    logger.log_turn(
        "pii-log",
        "Получатель: Анна Петрова; Самара, ул. Ленина 12, кв. 7",
        response,
    )

    content = next(tmp_path.rglob("*.md")).read_text(encoding="utf-8")
    assert "Анна Петрова" not in content
    assert "Ленина" not in content
    assert "кв. 7" not in content
    assert "Самара" in content
    assert "[имя скрыто]" in content
    assert "[адрес скрыт]" in content
