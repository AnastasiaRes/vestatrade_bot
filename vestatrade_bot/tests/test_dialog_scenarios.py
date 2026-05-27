from __future__ import annotations

from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.openrouter_client import LLMResult


class BadRewriteLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(
        self,
        agent: str,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 500,
    ) -> LLMResult:
        return LLMResult(content=self.content, llm_used=True)

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        return fallback, False


def test_pipe_starts_with_purpose_question(orchestrator) -> None:
    response = orchestrator.handle_chat("s1", "нужна труба")

    assert "отопления" in response.answer
    assert "канализации" in response.answer
    assert response.products == []


def test_pipe_purpose_followup_continues_context(orchestrator) -> None:
    orchestrator.handle_chat("s1b", "нужна труба")
    response = orchestrator.handle_chat("s1b", "водоснабжения")

    assert response.debug["category"] == "pipes"
    assert response.debug["slots"]["pipe_purpose"] == "отопление/водоснабжение"
    assert "диаметр" in response.answer.lower()


def test_sewer_pipe_50_asks_scope_and_length(orchestrator) -> None:
    response = orchestrator.handle_chat("s2", "канализационная труба 50")

    assert "Внутренняя или наружная канализация" in response.answer
    assert "какая длина" in response.answer
    assert response.products == []


def test_sewer_dialog_accumulates_slots_and_asks_only_missing(orchestrator) -> None:
    orchestrator.handle_chat("s2c", "Привет! Нужна труба")
    second = orchestrator.handle_chat("s2c", "Канализационная 50 м")
    response = orchestrator.handle_chat("s2c", "наружная канализация, труба")

    assert second.debug["topic_changed"] is False
    assert response.debug["category"] == "sewer"
    assert response.debug["slots"]["diameter_mm"] == 50
    assert response.debug["slots"]["sewer_scope"] == "наружная"
    assert response.debug["slots"]["element_type"] == "труба"
    assert "Какая длина трубы нужна" in response.answer
    assert "внутренняя или наружная" not in response.answer.lower()


def test_sewer_dialog_uses_collected_slots_for_final_search(orchestrator) -> None:
    orchestrator.handle_chat("s2d", "Привет! Нужна труба")
    orchestrator.handle_chat("s2d", "Канализационная 50 м")
    orchestrator.handle_chat("s2d", "наружная канализация, труба")
    response = orchestrator.handle_chat("s2d", "1000 мм")

    assert response.products
    assert response.products[0].sku == "OUT-50-1000"
    assert response.debug["slots"]["diameter_mm"] == 50
    assert response.debug["slots"]["length_mm"] == 1000


def test_sewer_no_exact_match_shows_safe_alternatives(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "s2e",
        "нужна наружная канализационная труба 75 длина 1000 мм",
    )

    assert response.products
    assert response.need_handoff is False
    assert "Точного совпадения" in response.answer
    assert "альтернатив" in response.answer.lower()
    assert response.products[0].sku in {"OUT-50-1000", "OUT-110-1000"}


def test_sewer_strictly_respects_scope_diameter_and_length(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "s2b",
        "нужна внутренняя канализационная труба 50 длина 500 мм",
    )

    assert [product.sku for product in response.products] == ["HTEM-50-500"]
    assert "HTEM-50-1500" not in response.answer
    assert "OUT-110-1000" not in response.answer


def test_circulation_pump_cheap_asks_relevant_params(orchestrator) -> None:
    response = orchestrator.handle_chat("s3", "циркуляционный насос, подешевле")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["cheap"] is True
    assert "монтажную длину" in response.answer


def test_circulation_pump_followup_uses_mounting_length_and_head(orchestrator) -> None:
    orchestrator.handle_chat("s3b", "циркуляционный насос, подешевле")
    response = orchestrator.handle_chat("s3b", "180 мм, напор 4 метра")

    assert response.products
    assert response.products[0].sku == "PUMP-25-40"
    assert response.debug["slots"]["mounting_length_mm"] == 180
    assert response.debug["slots"]["head_m"] == 4.0
    assert "монтажную длину" not in response.answer


def test_circulation_pump_old_model_is_used_for_alternative_search(orchestrator) -> None:
    orchestrator.handle_chat("s3h", "циркуляционный насос, подешевле")
    response = orchestrator.handle_chat(
        "s3h",
        "Старый насос Grundfos UPS 25-60, нужна более дешёвая альтернатива. "
        "Покажи варианты в наличии с ценой и ссылкой.",
    )

    assert response.products
    assert response.products[0].sku == "PUMP-25-60"
    assert response.debug["slots"]["old_model"] == "GRUNDFOS UPS 25-60"
    assert response.debug["slots"]["old_model_brand"] == "GRUNDFOS"
    assert response.debug["slots"]["connection_size"] == 25
    assert response.debug["slots"]["head_m"] == 6.0
    assert "brand" not in response.debug["slots"]
    assert "Уточните монтажную длину" not in response.answer
    assert "https://example.test/pump2560" in response.answer


def test_pump_for_dacha_followup_continues_context(orchestrator) -> None:
    orchestrator.handle_chat("s3c", "насос")
    response = orchestrator.handle_chat("s3c", "для дачи")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["application"] == "дача"
    assert "водоснабжения" in response.answer
    assert "полива" in response.answer
    assert response.need_handoff is False


def test_pump_water_supply_followup_asks_source(orchestrator) -> None:
    orchestrator.handle_chat("s3d", "насос")
    orchestrator.handle_chat("s3d", "для дачи")
    response = orchestrator.handle_chat("s3d", "водоснабжения")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_use"] == "водоснабжение"
    assert "скважина" in response.answer.lower()
    assert "колодец" in response.answer.lower()


def test_generic_pump_request_clears_stale_pump_type(orchestrator) -> None:
    orchestrator.handle_chat("s3e", "циркуляционный насос, подешевле")
    response = orchestrator.handle_chat("s3e", "Здравствуйте! Насос")

    assert response.debug["category"] == "pumps"
    assert "pump_type" not in response.debug["slots"]
    assert response.answer.startswith("Здравствуйте")
    assert "Для какой задачи нужен насос" in response.answer
    assert "монтажную длину" not in response.answer


def test_guardrails_restore_clarification_if_llm_drops_options(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Для какой задачи нужен насос?"),
    )

    response = orchestrator.handle_chat("s3f", "насос")

    assert "водоснабжение/полив" in response.answer
    assert "повышение давления" in response.answer
    assert "откачка воды" in response.answer
    assert response.debug["response_llm_used"] is True


def test_guardrails_restore_greeting_if_llm_changes_it(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Дела хорошо, спасибо. Для какой задачи нужен насос: отопление, "
            "водоснабжение/полив, повышение давления или откачка воды?"
        ),
    )

    response = orchestrator.handle_chat("s3g", "Здравствуйте! Насос")

    assert response.answer.startswith("Здравствуйте")
    assert "Дела хорошо" not in response.answer


def test_electric_boiler_for_100m2_does_not_show_weak_equal_option(orchestrator) -> None:
    response = orchestrator.handle_chat("s4", "электрический котёл на 100 м²")

    assert response.products
    assert all(product.sku != "ECA-6" for product in response.products)
    assert "приблизительный" in response.answer


def test_complectation_without_confirmation_falls_back(orchestrator) -> None:
    orchestrator.handle_chat("s5", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s5", "в котле есть насос и бак?")

    assert response.need_handoff is True
    assert "Не вижу подтверждения комплектации" in response.answer


def test_link_request_uses_last_product(orchestrator) -> None:
    orchestrator.handle_chat("s6", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s6", "дай ссылку")

    assert "https://example.test/arde9" in response.answer


def test_small_talk_then_product_continues_to_selection(orchestrator) -> None:
    response = orchestrator.handle_chat("s7", "как дела? нужен насос")

    assert response.debug["category"] == "pumps"
    assert response.answer.startswith("Дела хорошо")
    assert "Для какой задачи нужен насос" in response.answer


def test_topic_change_resets_old_slots(orchestrator) -> None:
    orchestrator.handle_chat("s8", 'кран шаровый для воды 20 угловой')
    response = orchestrator.handle_chat("s8", "теперь нужен котёл")

    assert response.debug["topic_changed"] is True
    assert response.debug["category"] == "boilers"
    assert "body_form" not in response.debug["slots"]
    assert "газовый или электрический" in response.answer


def test_identity_question_after_products_does_not_repeat_old_search(orchestrator) -> None:
    orchestrator.handle_chat("s9", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s9", "Как тебя зовут?")

    assert response.products == []
    assert response.debug["intent"] == "small_talk"
    assert "AI-консультант Vesta Trading" in response.answer
    assert "Нашёл подходящие варианты" not in response.answer


def test_unknown_non_product_message_does_not_reuse_old_context(orchestrator) -> None:
    orchestrator.handle_chat("s10", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s10", "какие у тебя планы?")

    assert response.products == []
    assert response.debug["intent"] == "unknown"
    assert "консультант по товарам Vesta Trading" in response.answer


def test_vague_battery_request_asks_clarification_not_random_products(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "s11",
        "мне надо что-нибудь нормальное для батареи чтобы крутилось сбоку",
    )

    assert response.products == []
    assert response.debug["category"] == "radiator_fittings"
    assert "прямое или угловое" in response.answer


def test_valve_understands_vody_case(orchestrator) -> None:
    response = orchestrator.handle_chat("s12", "кран шаровый для воды 20 угловой")

    assert response.products
    assert response.products[0].sku == "VALVE-20-ANGLE"
    assert "Уточните" not in response.answer


def test_stock_request_for_boilers_shows_available_products(orchestrator) -> None:
    response = orchestrator.handle_chat("s13", "что есть в наличии из котлов?")

    assert response.products
    assert all("в наличии" in product.stock_status for product in response.products)
    assert "газовый или электрический" not in response.answer


def test_cheaper_followup_does_not_present_same_product_as_cheaper(orchestrator) -> None:
    orchestrator.handle_chat("s14", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s14", "покажи дешевле")

    assert response.products == []
    assert "Более дешёвых подходящих" in response.answer


def test_out_of_scope_does_not_handoff_to_manager(orchestrator) -> None:
    response = orchestrator.handle_chat("s15", "расскажи анекдот")

    assert response.need_handoff is False
    assert response.products == []
    assert "нетоварные" in response.answer


def test_guardrails_restore_product_answer_if_llm_drops_card_facts(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Нашёл подходящий товар."),
    )

    response = orchestrator.handle_chat("s16", "электрический котёл на 100 м²")

    assert "ARD-E9" in response.answer
    assert "https://example.test/arde9" in response.answer
    assert "38000 RUB" in response.answer
    assert response.products
