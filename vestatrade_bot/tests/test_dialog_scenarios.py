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
    assert response.debug["slots"]["pipe_purpose"] == "водоснабжение"
    assert "холодная или горячая" in response.answer
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
    assert "длина одного отрезка трубы" in response.answer
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


def test_sewer_total_meters_and_repeated_diameter_do_not_loop(orchestrator) -> None:
    orchestrator.handle_chat("s2meters", "канализационная труба 50")
    response = orchestrator.handle_chat("s2meters", "наружная, 50 метров")

    assert response.debug["slots"]["sewer_scope"] == "наружная"
    assert response.debug["slots"]["diameter_mm"] == 50
    assert response.debug["slots"]["total_length_m"] == 50
    assert "общий метраж" in response.answer
    assert "одного отрезка" in response.answer

    repeated = orchestrator.handle_chat("s2meters", "50 мм")

    assert repeated.debug["slots"]["diameter_mm"] == 50
    assert "Диаметр 50 мм уже понял" in repeated.answer
    assert "500, 1000, 1500 или 2000 мм" in repeated.answer

    final = orchestrator.handle_chat("s2meters", "1000 мм")

    assert final.products
    assert final.products[0].sku == "OUT-50-1000"
    assert final.debug["slots"]["length_mm"] == 1000


def test_sewer_scope_only_followup_is_remembered(orchestrator) -> None:
    orchestrator.handle_chat("s2scope", "канализационная труба 50")
    response = orchestrator.handle_chat("s2scope", "наружная")

    assert response.debug["category"] == "sewer"
    assert response.debug["slots"]["sewer_scope"] == "наружная"
    assert response.debug["slots"]["diameter_mm"] == 50
    assert "внутренняя или наружная" not in response.answer.lower()
    assert "длина" in response.answer.lower()

    followup = orchestrator.handle_chat("s2scope", "50 мм и 5 метров")

    assert followup.debug["slots"]["sewer_scope"] == "наружная"
    assert followup.debug["slots"]["diameter_mm"] == 50
    assert followup.debug["slots"]["total_length_m"] == 5
    assert "общий метраж" in followup.answer
    assert "внутренняя или наружная" not in followup.answer.lower()


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


def test_why_followup_explains_last_pump_selection(orchestrator) -> None:
    orchestrator.handle_chat("s3why", "нужен насос для отопления")
    orchestrator.handle_chat("s3why", "25/6 180")
    response = orchestrator.handle_chat("s3why", "а почему ты это предлагаешь?")

    assert response.products
    assert "потому" in response.answer.lower()
    assert "напор" in response.answer.lower()
    assert "монтаж" in response.answer.lower()
    assert "PUMP-25-60" in response.answer


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


def test_reference_brand_is_not_strict_filter_for_cheaper_pump(orchestrator) -> None:
    response = orchestrator.handle_chat("s3brand", "насос как Grundfos, но дешевле")

    assert response.debug["slots"]["reference_brand"] == "GRUNDFOS"
    assert "brand" not in response.debug["slots"]
    assert "модель старого насоса" in response.answer
    assert "25-40/25-60" in response.answer


def test_choose_one_in_current_search_returns_single_main_product(orchestrator) -> None:
    response = orchestrator.handle_chat("s3choose", "циркуляционный насос 25/6 180 выбери один")

    assert len(response.products) == 1
    assert response.products[0].sku == "PUMP-25-60"
    assert "Рекомендую" in response.answer
    assert "Почему" in response.answer
    assert "Когда не подойдёт" in response.answer
    assert "Альтернатива" in response.answer
    assert "https://example.test/pump2560" in response.answer


def test_choose_one_followup_uses_last_products(orchestrator) -> None:
    orchestrator.handle_chat("s3choose2", "насос 25/6 180")
    response = orchestrator.handle_chat("s3choose2", "какой лучше выбрать?")

    assert len(response.products) == 1
    assert response.products[0].sku == "PUMP-25-60"
    assert "PUMP-25-60" in response.answer


def test_term_explanation_is_simple(orchestrator) -> None:
    response = orchestrator.handle_chat("term1", "не понимаю, что такое монтажная длина?")

    assert response.products == []
    assert "расстояние" in response.answer.lower()
    assert "130" in response.answer
    assert "180" in response.answer


def test_pump_for_boiler_after_boiler_selection_shows_basic_circulation_option(orchestrator) -> None:
    orchestrator.handle_chat("boiler-pump", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("boiler-pump", "насос к котлу")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["allow_basic_option"] is True
    assert len(response.products) == 1
    assert response.products[0].sku == "PUMP-25-40"
    assert "базовый вариант" in response.answer.lower()
    assert "монтажная длина" in response.answer.lower()


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


def test_weak_water_pressure_asks_source_not_generic_pump_type(orchestrator) -> None:
    orchestrator.handle_chat("s3pressure", "надо чтобы вода шла")
    response = orchestrator.handle_chat("s3pressure", "слабый напор в доме")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_use"] == "повышение давления"
    assert "напор" in response.answer.lower()
    assert "центральный водопровод" in response.answer.lower()
    assert "скважина" in response.answer.lower()


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


def test_builtin_boiler_is_not_confirmed_from_indirect_boiler_mentions(orchestrator) -> None:
    orchestrator.handle_chat("s5b", "у этого котла встроенный бойлер есть?")
    response = orchestrator.handle_chat("s5b", "ARD-E9")

    assert response.need_handoff is True
    assert response.products == []
    assert "Не вижу подтверждения комплектации" in response.answer
    assert "Бойлер. Карточка товара" not in response.answer


def test_link_request_uses_last_product(orchestrator) -> None:
    orchestrator.handle_chat("s6", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s6", "дай ссылку")

    assert "https://example.test/arde9" in response.answer


def test_confirmation_followup_repeats_same_last_product(orchestrator) -> None:
    orchestrator.handle_chat("s6b", "кран шаровый для воды 20 угловой")
    response = orchestrator.handle_chat("s6b", "ты точно тот же товар прислал?")

    assert response.products
    assert response.products[0].sku == "VALVE-20-ANGLE"
    assert "VALVE-20-ANGLE" in response.answer
    assert "https://example.test/valve20" in response.answer


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


def test_radiator_shutoff_followup_is_remembered(orchestrator) -> None:
    orchestrator.handle_chat("s11b", "нужна штука для батареи")
    response = orchestrator.handle_chat("s11b", "перекрывать")

    assert response.debug["category"] == "radiator_fittings"
    assert response.debug["slots"]["thermostatic_head"] is False
    assert "регулировать температуру" not in response.answer
    assert "прямое или угловое" in response.answer
    assert "1/2 или 3/4" in response.answer


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
    assert any(marker in response.answer.lower() for marker in ["вне", "нетовар", "не отвлек"])
    assert "vesta trading" in response.answer.lower() or any(
        cat in response.answer.lower()
        for cat in ["труб", "насос", "котел", "котёл", "кран", "канализац", "радиатор"]
    )


def test_engineering_risk_does_not_fantasize(orchestrator) -> None:
    response = orchestrator.handle_chat("risk1", "сделай гидравлический расчет системы отопления")

    assert response.need_handoff is True
    assert response.products == []
    assert "инженерно рискованный" in response.answer
    assert "не буду делать расчёт" in response.answer


def test_pump_anaphora_to_boiler_is_treated_as_circulation_pump(orchestrator) -> None:
    orchestrator.handle_chat("anaphora", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("anaphora", "и насос к нему")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["allow_basic_option"] is True
    assert response.products


def test_same_question_is_not_asked_three_times(orchestrator) -> None:
    first = orchestrator.handle_chat("repeat1", "циркуляционный насос")
    assert "монтажную длину" in first.answer
    assert first.products == []

    second = orchestrator.handle_chat("repeat1", "я не знаю")
    assert "монтажную длину" in second.answer
    assert second.products == []

    third = orchestrator.handle_chat("repeat1", "ну не знаю я")
    assert third.products
    assert "по кругу" in third.answer
    assert "180 мм" in third.answer


def test_comparison_request_lists_differences_not_duplicate_cards(orchestrator) -> None:
    orchestrator.handle_chat("cmp1", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("cmp1", "а чем они отличаются?")

    assert "Главное отличие" in response.answer
    assert "ARD-E9" in response.answer
    assert "ECA-6" in response.answer
    assert "Нашёл подходящие варианты" not in response.answer


def test_gas_boiler_scenario_asks_contours_after_area(orchestrator) -> None:
    orchestrator.handle_chat("gas1", "газовый котёл")
    response = orchestrator.handle_chat("gas1", "на 240 квадратов")

    assert "одноконтурный" in response.answer.lower()
    assert "двухконтурный" in response.answer.lower()
    assert response.products == []

    final = orchestrator.handle_chat("gas1", "двухконтурный")
    assert final.debug["slots"]["contours"] == "двухконтурный"


def test_what_is_boiler_gets_explanation_not_interrogation(orchestrator) -> None:
    response = orchestrator.handle_chat("term3", "что такое котел")

    assert response.products == []
    assert "отоплени" in response.answer.lower()
    assert "Котёл нужен газовый или электрический?" not in response.answer


def test_what_is_unknown_term_does_not_fall_into_product_flow(orchestrator) -> None:
    response = orchestrator.handle_chat("term4", "что такое сильфон?")

    assert response.products == []
    assert "Уточните" not in response.answer
    assert "не подскажу" in response.answer.lower() or "объясн" in response.answer.lower()


def test_term_explanation_two_contour_boiler(orchestrator) -> None:
    response = orchestrator.handle_chat("term2", "что такое двухконтурный котёл?")

    assert response.products == []
    assert "горяч" in response.answer.lower()
    assert "отоплен" in response.answer.lower()


def test_manager_handoff_is_recorded_and_confirmed(sample_products, tmp_path) -> None:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    orchestrator.handle_chat("h1", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("h1", "передай менеджеру")

    assert response.need_handoff is True
    assert "менеджер" in response.answer.lower()
    assert "Более дешёвых" not in response.answer

    log_text = (tmp_path / "handoff.jsonl").read_text(encoding="utf-8")
    assert '"session_id": "h1"' in log_text
    assert "ARD-E9" in log_text


def test_show_analogs_followup_excludes_already_shown_products(orchestrator) -> None:
    first = orchestrator.handle_chat("analog1", "насос 25/6 180")
    assert first.products
    assert first.products[0].sku == "PUMP-25-60"

    response = orchestrator.handle_chat("analog1", "покажи аналоги")

    assert response.products
    skus = [product.sku for product in response.products]
    assert "PUMP-25-40" in skus
    assert "PUMP-25-60" not in skus
    assert "Аналоги" in response.answer


def test_smart_reply_does_not_parrot_previous_answer() -> None:
    from app.agents.response_composer import ResponseComposerAgent

    previous_answer = (
        "Более дешёвых подходящих вариантов в текущем фиде нет. "
        "Последний подходящий — 2202210. Могу показать аналоги или передать вопрос менеджеру."
    )
    composer = ResponseComposerAgent(llm_client=BadRewriteLLM(previous_answer))
    composer.set_history(
        [
            {"role": "user", "content": "а есть что подешевле?"},
            {"role": "assistant", "content": previous_answer},
        ]
    )

    answer = composer.compose_unknown("ну и что мне делать дальше?")

    assert answer != previous_answer
    assert "консультант" in answer.lower()


def test_product_answer_suggests_companion_components_once(orchestrator) -> None:
    response = orchestrator.handle_chat("comp1", "электрический котёл на 100 м²")

    assert response.products
    assert "насос" in response.answer.lower()
    assert "групп" in response.answer.lower()

    again = orchestrator.handle_chat("comp1", "покажи дешевле")
    assert "группу безопасности" not in again.answer


def test_gas_vs_electric_question_gets_advice_not_silent_assumption(orchestrator) -> None:
    response = orchestrator.handle_chat("gve1", "что лучше: газовый или электрический котёл?")

    assert response.products == []
    assert "газ" in response.answer.lower()
    assert "электрическ" in response.answer.lower()
    assert "площадь" in response.answer.lower()

    followup = orchestrator.handle_chat("gve1", "газа нет, дом 100 квадратов")

    assert followup.products
    assert followup.debug["slots"]["boiler_type"] == "электрический"
    assert followup.debug["slots"]["area_m2"] == 100.0


def test_exact_product_name_returns_product_without_interrogation(orchestrator) -> None:
    response = orchestrator.handle_chat("name1", "Труба PPR 20 мм PN20")

    assert response.products
    assert response.products[0].sku == "VTp.700.0.020"
    assert "Труба для чего" not in response.answer


def test_product_docs_confirm_complectation(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.docs_text = (
                "Паспорт изделия. В комплект поставки входят встроенный циркуляционный "
                "насос и расширительный бак на 6 литров."
            )
    orchestrator = ChatOrchestrator(products=products)

    orchestrator.handle_chat("docs1", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("docs1", "в котле есть насос и бак?")

    assert response.need_handoff is False
    assert "ARD-E9" in response.answer
    assert "насос" in response.answer.lower()


def test_product_docs_loader_attaches_by_sku(tmp_path, sample_products) -> None:
    from app.docs_loader import load_docs_for_products

    products = [product.model_copy(deep=True) for product in sample_products]
    (tmp_path / "ARD-E9.txt").write_text("Технический паспорт котла", encoding="utf-8")
    (tmp_path / "UNKNOWN-SKU.txt").write_text("ничейный документ", encoding="utf-8")

    attached = load_docs_for_products(products, tmp_path)

    assert attached == 1
    by_sku = {product.sku: product for product in products}
    assert by_sku["ARD-E9"].docs_text == "Технический паспорт котла"
    assert by_sku["ECA-6"].docs_text is None


def test_product_docs_loader_supports_series_map_and_brand_rules(tmp_path, sample_products) -> None:
    import json

    from app.docs_loader import load_docs_for_products

    products = [product.model_copy(deep=True) for product in sample_products]
    (tmp_path / "VT.227-228-0425.txt").write_text("Паспорт кранов BASE", encoding="utf-8")
    (tmp_path / "котлы бренда.txt").write_text("Паспорт электрических котлов", encoding="utf-8")
    (tmp_path / "product_docs_map.json").write_text(
        json.dumps(
            {
                "котлы бренда.txt": {
                    "brand": "ARDERIA",
                    "name_contains_any": ["электрический"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    attached = load_docs_for_products(products, tmp_path)

    assert attached == 2
    by_sku = {product.sku: product for product in products}
    # серия из имени файла: VT.227 + VT.228 -> кран VT.228.N.04 из фикстуры
    assert by_sku["VT.228.N.04"].docs_text == "Паспорт кранов BASE"
    # правило бренда из карты: Arderia + «электрический»
    assert by_sku["ARD-E9"].docs_text == "Паспорт электрических котлов"
    assert by_sku["ECA-6"].docs_text is None


def test_bare_pipe_with_diameter_is_not_asked_for_diameter_again(orchestrator) -> None:
    response = orchestrator.handle_chat("p50", "труба 50")

    assert response.debug["slots"]["diameter_mm"] == 50
    assert "50 мм" in response.answer
    assert "какой диаметр" not in response.answer.lower()
    assert "канализации" in response.answer

    followup = orchestrator.handle_chat("p50", "для канализации")
    assert followup.debug["slots"]["diameter_mm"] == 50
    assert "внутренняя или наружная" in followup.answer.lower()


def test_degrees_are_not_turned_into_diameter(orchestrator) -> None:
    response = orchestrator.handle_chat("dim1", "отвод 87 градусов на 110")

    assert response.debug["slots"].get("diameter_mm") == 110

    response = orchestrator.handle_chat("dim2", "термоголовка на 28 градусов")
    assert "diameter_mm" not in response.debug["slots"]


def test_no_phantom_dimensions_for_unstated_constraints(orchestrator) -> None:
    response = orchestrator.handle_chat("dim3", "кран шаровый 1/2")

    slots = response.debug["slots"]
    assert slots.get("size_inch") == "1/2"
    for key in ["diameter_mm", "length_mm", "area_m2", "power_kw", "head_m", "mounting_length_mm"]:
        assert key not in slots, f"фантомное ограничение: {key}={slots[key]}"


def test_chat_logger_writes_readable_transcript(tmp_path, orchestrator) -> None:
    from app.chat_logger import ChatLogger

    chat_logger = ChatLogger(tmp_path)
    response = orchestrator.handle_chat("log1", "электрический котёл на 100 м²")
    chat_logger.log_turn("log1", "электрический котёл на 100 м²", response)
    followup = orchestrator.handle_chat("log1", "дай ссылку")
    chat_logger.log_turn("log1", "дай ссылку", followup)

    files = list(tmp_path.rglob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Клиент:** электрический котёл на 100 м²" in content
    assert "Бот:**" in content
    assert "Показанные товары: ARD-E9" in content
    assert "дай ссылку" in content


def test_chat_logger_sanitizes_session_id_path(tmp_path, orchestrator) -> None:
    from app.chat_logger import ChatLogger

    chat_logger = ChatLogger(tmp_path)
    response = orchestrator.handle_chat("evil", "привет")
    chat_logger.log_turn("../../evil", "привет", response)

    files = list(tmp_path.rglob("*.md"))
    assert len(files) == 1
    assert files[0].is_relative_to(tmp_path)
    assert ".." not in files[0].name


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
