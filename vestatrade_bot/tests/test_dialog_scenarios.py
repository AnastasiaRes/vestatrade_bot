from __future__ import annotations

from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.models import Product, ProductCard, SearchQuery
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


def test_heating_system_is_funneled_not_equated_with_boiler(orchestrator) -> None:
    response = orchestrator.handle_chat("sf1", "нужна система отопления")

    # Отопление — это система, а не только котёл: воронка перечисляет узлы и не
    # вываливает товары и не спрашивает сразу «газовый или электрический».
    assert response.products == []
    answer = response.answer.lower()
    assert "котёл" in answer or "котел" in answer
    assert "насос" in answer
    assert "радиатор" in answer
    assert "газовый или электрический" not in answer


def test_heating_funnel_narrows_to_boiler_when_user_picks_one(orchestrator) -> None:
    orchestrator.handle_chat("sf2", "нужна система отопления")
    response = orchestrator.handle_chat("sf2", "котёл")

    assert response.debug["category"] == "boilers"
    assert "газовый или электрический" in response.answer.lower()


def test_vague_house_request_does_not_dump_random_products(orchestrator) -> None:
    response = orchestrator.handle_chat("sf3", "нужно в дом сантехнику")

    assert response.products == []
    answer = response.answer.lower()
    assert "котлы" in answer and "насосы" in answer and "трубы" in answer


def test_bare_query_with_no_signal_asks_instead_of_searching(orchestrator) -> None:
    # «хочу что-нибудь» не несёт ни категории, ни параметров — нельзя искать вслепую.
    response = orchestrator.handle_chat("sf4", "хочу обустроить ванную")

    assert response.products == []
    assert "ванной" in response.answer.lower()
    assert "водоснабжение" in response.answer.lower()
    assert "канализация" in response.answer.lower()


def test_warm_floor_all_followup_keeps_project_context(orchestrator) -> None:
    first = orchestrator.handle_chat("wf1", "всё для тёплого пола")
    response = orchestrator.handle_chat("wf1", "всё")

    assert first.products == []
    assert response.products == []
    assert response.debug["slots"]["scope_funnel"] == "warm_floor"
    assert "тёплого пола" in response.answer.lower()
    assert "площадь" in response.answer.lower()
    assert "водяной" in response.answer.lower()


def test_warm_floor_area_builds_article_cart_without_random_fittings(sample_products) -> None:
    products = [
        *sample_products,
        Product(
            sku="FIT-ANGLE-20",
            name="Угольник 90 PPR 20мм",
            category_path="Фитинги полипропиленовые",
            brand="VALTEC",
            url="https://example.test/fitting-angle-20",
            price=15,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=200,
            attributes_normalized={
                "артикул": "FIT-ANGLE-20",
                "тип товара": "Угольник",
                "диаметр (мм)": "20",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)

    first = bot.handle_chat("wf-cart", "хочу сделать теплые полы, что для этого нужно?")
    response = bot.handle_chat("wf-cart", "50м2")

    assert first.products == []
    assert response.products
    names = " ".join(product.name for product in response.products)
    assert "Угольник" not in names
    assert "FIT-ANGLE-20" not in response.answer
    assert {"VTp.700.0.020", "PUMP-25-40", "VALVE-20-ANGLE"}.issubset(
        {product.sku for product in response.products}
    )
    assert "Почему:" in response.answer
    assert response.debug["slots"]["project_scope"] == "warm_floor"


def test_project_cart_summary_returns_discussed_articles(orchestrator) -> None:
    orchestrator.handle_chat("wf-summary", "хочу сделать теплые полы, что для этого нужно?")
    selected = orchestrator.handle_chat("wf-summary", "50м2")
    response = orchestrator.handle_chat("wf-summary", "собери артикулы корзиной")

    assert response.products
    assert {product.sku for product in response.products} == {product.sku for product in selected.products}
    assert "корзину" in response.answer.lower()
    assert "VTp.700.0.020" in response.answer
    assert "PUMP-25-40" in response.answer
    assert "не буду выдумывать количество" in response.answer.lower()


def test_project_cart_updates_component_after_followup(orchestrator) -> None:
    orchestrator.handle_chat("wf-update", "хочу сделать теплые полы, что для этого нужно?")
    orchestrator.handle_chat("wf-update", "50м2")
    pump = orchestrator.handle_chat("wf-update", "насос 25/6 180")
    response = orchestrator.handle_chat("wf-update", "собери артикулы корзиной")

    assert pump.products
    assert pump.products[0].sku == "PUMP-25-60"
    assert "PUMP-25-60" in response.answer
    assert "PUMP-25-40" not in response.answer


def test_project_negated_warm_floor_does_not_switch_to_warm_floor(orchestrator) -> None:
    orchestrator.handle_chat("bath-no-wf", "обустраиваю санузел в доме, нужен список по артикулам")
    response = orchestrator.handle_chat(
        "bath-no-wf",
        "давай без тёплого пола, только вода и канализация",
    )

    assert "без тёплого пола" in response.answer.lower()
    assert "какая площадь" not in response.answer.lower()
    assert response.debug["slots"].get("scope_funnel") != "warm_floor"


def test_project_cart_handles_paraphrased_warm_floor_flow(orchestrator) -> None:
    orchestrator.handle_chat("wf-phrased", "надо собрать комплект на водяной теплый пол")
    selected = orchestrator.handle_chat("wf-phrased", "площадь 60 квадратов")
    response = orchestrator.handle_chat("wf-phrased", "дайте итог по артикулам")

    assert selected.products
    assert response.products
    assert {product.sku for product in response.products} == {product.sku for product in selected.products}
    assert response.debug["slots"]["project_scope"] == "warm_floor"
    assert "корзину" in response.answer.lower()


def test_project_scope_change_starts_new_cart(orchestrator) -> None:
    orchestrator.handle_chat("project-switch", "хочу сделать теплые полы, что нужно?")
    orchestrator.handle_chat("project-switch", "50м2")
    bathroom = orchestrator.handle_chat(
        "project-switch",
        "теперь собери всё для ванной по артикулам",
    )
    response = orchestrator.handle_chat("project-switch", "собери корзину")

    assert bathroom.products
    assert response.products
    assert response.debug["slots"]["project_scope"] == "bathroom"
    assert "PUMP-25-40" not in response.answer
    assert "PUMP-25-60" not in response.answer
    assert all("насос" not in product.name.lower() for product in response.products)


def test_scope_followup_is_generic_for_water_supply(orchestrator) -> None:
    first = orchestrator.handle_chat("scope-water", "нужно водоснабжение")
    response = orchestrator.handle_chat("scope-water", "комплектом")

    assert first.products == []
    assert response.products == []
    assert response.debug["slots"]["scope_funnel"] == "water"
    answer = response.answer.lower()
    assert "источник воды" in answer
    assert "центральный водопровод" in answer
    assert "скважина" in answer


def test_system_scope_is_not_hijacked_by_llm(sample_products) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    llm = BadRewriteLLM(
        "Насос — артикул 001-2345, цена 15000 руб.; трубы — артикул 002-3456."
    )
    bot = ChatOrchestrator(settings=settings, products=sample_products, llm_client=llm)

    response = bot.handle_chat("water-safe-llm", "нужно водоснабжение")

    assert response.products == []
    assert "001-2345" not in response.answer
    assert "водоснабжение" in response.answer.lower()
    assert "насос" in response.answer.lower()
    assert response.debug["any_llm_used"] is False


def test_project_area_followup_survives_llm_lost_slots(sample_products) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    llm = BadRewriteLLM("Окей, уточните площадь.")
    bot = ChatOrchestrator(settings=settings, products=sample_products, llm_client=llm)

    bot.handle_chat("wf-area-llm", "хочу сделать теплые полы, что нужно?")
    response = bot.handle_chat("wf-area-llm", "50м2")

    assert response.products
    assert response.debug["slots"]["area_m2"] == 50.0
    assert "Угольник" not in response.answer


def test_concrete_heating_pump_request_is_not_free_llm_consult(sample_products) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    llm = BadRewriteLLM("Этот насос имеет мощность 72 кВт и точно подходит.")
    bot = ChatOrchestrator(settings=settings, products=sample_products, llm_client=llm)

    response = bot.handle_chat("pump-safe-llm", "ладно, нужен насос для отопления")

    assert "72 кВт" not in response.answer
    assert "точно подходит" not in response.answer.lower()
    assert "монтаж" in response.answer.lower() or response.products


def test_boiler_context_pump_request_skips_drainage_pumps(sample_products) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    drain = Product(
        sku="DRAIN-350",
        name="Дренажный насос 350 Вт",
        category_path="Насосы дренажные",
        url="https://example.test/drain",
        price=2500,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип товара": "Дренажный насос"},
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=[*sample_products, drain],
        llm_client=BadRewriteLLM("Покажу дренажный насос."),
    )
    session = bot.sessions.get("pump-boiler-context")
    session.category = "boilers"
    session.slots["boiler_type"] = "газовый"
    session.last_products = [
        ProductCard(
            sku="GAS-24",
            name="Котёл газовый 24 кВт",
            price=36000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=1,
            url="https://example.test/gas24",
        )
    ]
    bot.sessions.save(session)

    response = bot.handle_chat("pump-boiler-context", "а насос к нему")

    assert response.products
    assert "DRAIN-350" not in {product.sku for product in response.products}
    assert "Дренажный" not in response.answer
    assert "циркуляц" in response.answer.lower()


def test_scope_followup_is_generic_for_heating(orchestrator) -> None:
    first = orchestrator.handle_chat("scope-heat", "нужно отопление в дом")
    response = orchestrator.handle_chat("scope-heat", "под ключ")

    assert first.products == []
    assert response.products == []
    assert response.debug["slots"]["scope_funnel"] == "heating"
    answer = response.answer.lower()
    assert "площадь" in answer
    assert "источник тепла" in answer


def test_arbitrary_plumbing_project_starts_discovery_not_random_search(orchestrator) -> None:
    response = orchestrator.handle_chat("scope-random", "делаю ремонт кухни по сантехнике")

    assert response.products == []
    answer = response.answer.lower()
    assert "котлы" in answer and "насосы" in answer and "трубы" in answer


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


def test_sewer_scope_and_diameter_defaults_to_pipe_and_accepts_bare_length(orchestrator) -> None:
    orchestrator.handle_chat("s2bare", "теперь канализация 50 внутренняя")
    response = orchestrator.handle_chat("s2bare", "1500")

    assert response.products
    assert response.products[0].sku == "HTEM-50-1500"
    assert response.debug["slots"]["element_type"] == "труба"
    assert response.debug["slots"]["length_mm"] == 1500


def test_concrete_sewer_request_bypasses_consultant_llm(sample_products, monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "openrouter_api_key": "test-key",
            "llm_enabled": True,
        }
    )
    monkeypatch.setattr("app.agents.orchestrator.get_settings", lambda: settings)
    bot = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Уточните площадь дома и есть ли газ."),
    )

    first = bot.handle_chat("sewer-llm", "теперь канализация 50 внутренняя")
    response = bot.handle_chat("sewer-llm", "1500")

    assert "площадь дома" not in first.answer.lower()
    assert "газ" not in first.answer.lower()
    assert response.products
    assert response.products[0].sku == "HTEM-50-1500"
    assert response.debug["consultant_llm_used"] is False


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


def test_pure_greeting_gets_fixed_branded_reply(orchestrator) -> None:
    expected = (
        "Добрый день. Веста Трейдинг, консультант на связи. "
        "Подскажите, что подбираем: котельную, отопление, водоснабжение или канализацию?"
    )
    for message in ["привет", "Привет!", "Здравствуйте", "добрый день"]:
        response = orchestrator.handle_chat(f"greet-{message}", message)
        assert response.answer == expected, message
        assert response.products == []


def test_small_talk_then_product_continues_to_selection(orchestrator) -> None:
    response = orchestrator.handle_chat("s7", "как дела? нужен насос")

    assert response.debug["category"] == "pumps"
    assert response.answer.startswith("Дела хорошо")
    assert "Для какой задачи нужен насос" in response.answer


def test_pure_small_talk_uses_llm_but_keeps_safe_fallback(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Неподходящая перепись от LLM."),
    )

    response = orchestrator.handle_chat("s7b", "ты классный")

    assert response.debug["intent"] == "small_talk"
    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert response.answer.startswith("Спасибо")
    assert "Неподходящая" not in response.answer


def test_pure_small_talk_accepts_safe_llm_reply(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Спасибо за добрые слова. Помогу подобрать котёл, насос или трубы из ассортимента Vesta Trading."
        ),
    )

    response = orchestrator.handle_chat("s7c", "ты классный")

    assert response.debug["intent"] == "small_talk"
    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert response.answer.startswith("Спасибо за добрые слова")


def test_small_talk_rejects_awkward_personal_refusal(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Все в порядке, спасибо. Как у вас дела? Я не могу обсуждать персональные вопросы."
        ),
    )

    response = orchestrator.handle_chat("s7d", "как дела?")

    assert response.debug["intent"] == "small_talk"
    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert "персональные вопросы" not in response.answer
    assert response.answer.startswith("Дела хорошо")


def test_small_talk_rejects_reciprocal_personal_question(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Привет! Я в порядке. Как у вас дела? Помогу подобрать котлы, насосы или трубы."
        ),
    )

    response = orchestrator.handle_chat("s7e", "как дела?")

    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert "Как у вас дела" not in response.answer
    assert response.answer.startswith("Дела хорошо")


def test_small_talk_keeps_how_are_you_acknowledgement(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Привет! Как я могу помочь вам сегодня? Подберу котлы, насосы или трубы."
        ),
    )

    response = orchestrator.handle_chat("s7g", "как дела?")

    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert response.answer.startswith("Дела хорошо")


def test_small_talk_rejects_premature_technical_questions(sample_products) -> None:
    orchestrator = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM(
            "Отлично! Уточните тип котла, контурность, диаметр и материал трубы или тип насоса."
        ),
    )

    response = orchestrator.handle_chat("s7f", "ладно, к делу")

    assert response.debug["response_llm_requested"] is True
    assert response.debug["response_llm_used"] is True
    assert "тип котла" not in response.answer
    assert "контурность" not in response.answer
    assert response.answer.startswith("Конечно")


def test_informal_greeting_with_product_stays_respectful(orchestrator) -> None:
    response = orchestrator.handle_chat("tone1", "привет, насос")

    assert response.debug["category"] == "pumps"
    assert response.answer.startswith("Здравствуйте.")
    assert "Для какой задачи нужен насос" in response.answer
    assert not response.answer.startswith("Привет.")


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


def test_boiler_with_pump_in_description_is_not_a_pump(sample_products) -> None:
    from app.agents.feed_search import FeedSearchAgent

    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.description = "Электрический котёл со встроенным циркуляционным насосом."
            product.category_path = "ПРОКАЧИВАЕМ СКИДКИ"
    agent = FeedSearchAgent(products)
    boiler = next(p for p in products if p.sku == "ARD-E9")

    assert agent._category_matches(boiler, "boilers") is True
    assert agent._category_matches(boiler, "pumps") is False


def test_americanka_filter_excludes_valves_without_union(orchestrator) -> None:
    orchestrator.handle_chat("am", "кран шаровый для воды")
    orchestrator.handle_chat("am", "3/4")
    response = orchestrator.handle_chat("am", "с американкой")

    assert response.products
    for product in response.products:
        name = product.name.lower()
        assert "полусгон" in name or "американк" in name, product.name


def test_hot_water_followup_is_acknowledged_as_two_contour(orchestrator) -> None:
    orchestrator.handle_chat("hw", "нужен газовый котёл")
    response = orchestrator.handle_chat("hw", "и чтобы горячую воду грел")

    assert response.debug["slots"].get("contours") == "двухконтурный"
    assert "двухконтурный" in response.answer.lower()
    assert "площад" in response.answer.lower()


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
    assert "измерить" in second.answer.lower() or "посмотреть" in second.answer.lower()
    assert second.products == []

    third = orchestrator.handle_chat("repeat1", "ну не знаю я")
    assert third.products
    assert "по кругу" in third.answer
    assert "180 мм" in third.answer
    assert "чаще смотрят насосы 25/6" not in third.answer


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


def test_cheaper_analog_does_not_return_more_expensive_products(orchestrator) -> None:
    first = orchestrator.handle_chat("analog-cheap", "покажи насос для отопления 25 6 180")
    response = orchestrator.handle_chat("analog-cheap", "есть аналог подешевле?")

    assert first.products
    assert response.products == []
    assert "деш" in response.answer.lower()
    assert "Аналоги к показанным" not in response.answer


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
    assert "нужен не всегда" in response.answer.lower() or "не всегда" in response.answer.lower()

    again = orchestrator.handle_chat("comp1", "покажи дешевле")
    assert "группу безопасности" not in again.answer


def _boiler_with_builtins(sample_products):
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.description = (
                "Электрический котёл. Встроенный циркуляционный насос, встроенный "
                "расширительный бак, манометр, закрытая камера сгорания."
            )
    return products


class _CtxLLM:
    """Fake LLM that echoes a fixed reply for the context agent and ignores the rest."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.system_seen = ""

    def complete(self, agent, messages, temperature=0.1, max_tokens=500):
        if agent == "ResponseComposerAgent.context":
            self.system_seen = messages[0]["content"]
            return LLMResult(content=self.reply, llm_used=True)
        return LLMResult(content=None, llm_used=False, fallback_reason="off")

    def complete_json(self, agent, messages, fallback):
        return fallback, False


def test_context_agent_answers_followup_about_shown_products(sample_products) -> None:
    llm = _CtxLLM("Дешевле всех ECA-6 за 30000 RUB, и его 5 шт в наличии.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("ctx", "а что в наличии и что посоветуешь?")

    assert "ECA-6" in response.answer
    # карточные данные реально попали в промпт агента
    assert "ARD-E9" in llm.system_seen and "38000" in llm.system_seen


def test_what_is_better_from_shown_uses_deterministic_choice(orchestrator) -> None:
    orchestrator.handle_chat("better", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("better", "а что лучше из показанных?")

    assert "По показанным товарам уточните" not in response.answer
    assert response.products
    assert "рекоменд" in response.answer.lower() or "выбрал" in response.answer.lower()


def test_context_agent_invented_price_is_rejected(sample_products) -> None:
    llm = _CtxLLM("Рекомендую ARD-E9 за 19999 RUB — отличная цена.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx2", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("ctx2", "а что посоветуешь?")

    assert "19999" not in response.answer  # выдуманная цена отброшена guardrail'ом


def test_context_agent_hyphenated_word_is_not_treated_as_sku(sample_products) -> None:
    llm = _CtxLLM("Из-за цены я бы выбрал ECA-6 за 30000 RUB.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx-hyphen", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("ctx-hyphen", "а что в наличии и что посоветуешь?")

    assert "Из-за" in response.answer
    assert "ECA-6" in response.answer


def test_context_agent_invented_measurement_is_rejected(sample_products) -> None:
    llm = _CtxLLM("У этого насоса мощность 72 кВт, длина шланга 4 м.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx-measure", "циркуляционный насос 25/6 180")
    response = orchestrator.handle_chat("ctx-measure", "а что по мощности?")

    assert "72 кВт" not in response.answer
    assert "шланг" not in response.answer.lower()


def test_builtin_pump_question_bypasses_context_llm(sample_products) -> None:
    llm = _CtxLLM("Да, насос встроен, мощность 72 кВт.")
    orchestrator = ChatOrchestrator(products=_boiler_with_builtins(sample_products), llm_client=llm)
    orchestrator.handle_chat("builtin-pump", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("builtin-pump", "а есть встроенный насос?")

    assert "насос" in response.answer.lower()
    assert "72 кВт" not in response.answer
    assert response.debug["response_llm_used"] is False


def test_meta_questions_are_deflected_without_inventing(orchestrator) -> None:
    orchestrator.handle_chat("meta", "газовый котёл на 100 м²")
    for message in ["а скидка есть?", "когда доставите?", "какая гарантия?"]:
        response = orchestrator.handle_chat("meta", message)
        assert "менеджер" in response.answer.lower()
        assert "%" not in response.answer  # не выдумывает скидку
        assert "Нашёл подходящие варианты" not in response.answer  # не вываливает список


def test_transition_phrase_does_not_dump_products(orchestrator) -> None:
    orchestrator.handle_chat("tr", "привет")
    orchestrator.handle_chat("tr", "как дела")
    response = orchestrator.handle_chat("tr", "ладно, к делу")

    assert response.products == []
    assert "Артикул" not in response.answer


def test_context_agent_degenerate_loop_is_rejected(sample_products) -> None:
    loop = "\n".join(["Если нужен другой вариант — есть модели." for _ in range(8)])
    llm = _CtxLLM(loop)
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("loop", "циркуляционный насос 25/6 180")
    response = orchestrator.handle_chat("loop", "а под какой котёл этот насос подходит?")

    assert response.answer.count("Если нужен другой вариант") <= 1  # зацикливание отброшено


def test_passport_question_routes_to_context_agent_with_passport_text(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.docs_text = "ПАСПОРТ. В комплект поставки входит котёл и руководство по эксплуатации."
    llm = _CtxLLM("По паспорту в комплект входит котёл и руководство.")
    orchestrator = ChatOrchestrator(products=products, llm_client=llm)
    orchestrator.handle_chat("psp", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("psp", "ответь по паспорту, что входит в полную комплектацию")

    # вопрос ушёл к контекст-агенту, и текст паспорта попал ему в промпт
    assert "руководство" in llm.system_seen.lower()
    assert "руководство" in response.answer.lower()


def test_pump_fit_question_uses_context_not_new_boiler_search(sample_products) -> None:
    llm = _CtxLLM("Это циркуляционный насос для отопления, подходит к котлам отопления.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("fit", "циркуляционный насос 25/6 180")
    response = orchestrator.handle_chat("fit", "а под какой котёл этот насос подходит?")

    assert "газовый или электрический" not in response.answer.lower()
    assert "циркуляционный насос" in response.answer.lower()
    assert "не вижу привязки" in response.answer.lower()
    assert "не буду подтверждать совместимость" in response.answer.lower()
    assert "выходное отверстие" not in response.answer.lower()


def test_open_complectation_lists_builtin_components_from_card(sample_products) -> None:
    orchestrator = ChatOrchestrator(products=_boiler_with_builtins(sample_products))
    orchestrator.handle_chat("ck", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("ck", "а что входит в комплект?")

    assert response.need_handoff is False
    assert response.debug["intent"] == "complectation"
    assert "насос" in response.answer.lower()
    assert "бак" in response.answer.lower()
    assert "ARD-E9" in response.answer


def test_complectation_targets_first_when_several_products_shown(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ECA-6":
            product.description = "Электрический котёл. Встроенный циркуляционный насос."
    orchestrator = ChatOrchestrator(products=products)
    # «что есть из котлов» показывает несколько позиций
    orchestrator.handle_chat("multi", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("multi", "а гайки в комплекте идут?")

    # отвечает по первому показанному товару, а не переспрашивает «по какому товару»
    assert "По какому котлу или товару" not in response.answer
    assert response.debug["intent"] == "complectation"


def test_check_documentation_request_does_not_fabricate(sample_products) -> None:
    # Котёл без встроенных узлов в описании — бот не должен выдумывать «документацию»
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.description = "Электрический котёл мощностью 9 кВт."
            product.docs_text = None
    orchestrator = ChatOrchestrator(products=products)
    orchestrator.handle_chat("doc", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("doc", "проверь документацию и ответь, что входит")

    assert response.debug["intent"] == "complectation"
    assert "не детализирован" in response.answer or "не вижу" in response.answer.lower()
    # никаких выдуманных списков датчиков
    assert "датчик температуры воды в трубах" not in response.answer


def test_part_question_after_boiler_routes_to_complectation(sample_products) -> None:
    orchestrator = ChatOrchestrator(products=_boiler_with_builtins(sample_products))
    orchestrator.handle_chat("pq", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("pq", "продолжи, есть ли там насос?")

    assert response.debug["intent"] == "complectation"
    assert "насос" in response.answer.lower()
    assert "Для какой задачи нужен насос" not in response.answer


def test_boiler_built_in_pump_question_is_answered_from_description(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.description = "Электрический котёл со встроенным циркуляционным насосом."
    orchestrator = ChatOrchestrator(products=products)

    orchestrator.handle_chat("bp", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("bp", "то есть в котёл не входит насос?")

    assert response.debug["intent"] == "complectation"
    assert "ARD-E9" in response.answer
    assert "насос" in response.answer.lower()
    # слоты не должны обнуляться вопросом о комплектации
    assert response.debug["slots"].get("boiler_type") == "электрический"
    assert response.debug["slots"].get("area_m2") == 100.0


def test_complectation_yes_no_followup_answers_directly(sample_products) -> None:
    orchestrator = ChatOrchestrator(products=_boiler_with_builtins(sample_products))
    orchestrator.handle_chat("yn", "электрический котёл на 100 м²")
    first = orchestrator.handle_chat("yn", "насос туда включен или нет?")
    response = orchestrator.handle_chat("yn", "да или нет?")

    assert first.answer.lower().startswith("да")
    assert response.answer.lower().startswith("да")
    assert "ARD-E9" in response.answer
    assert "насос" in response.answer.lower()


def test_electric_choice_never_shows_gas_boilers(orchestrator) -> None:
    orchestrator.handle_chat("eg", "нужен котёл")
    orchestrator.handle_chat("eg", "да нужна горячая вода от котла")
    orchestrator.handle_chat("eg", "электрический")
    response = orchestrator.handle_chat("eg", "100 м2")

    for product in response.products:
        assert "газов" not in product.name.lower(), product.name
    assert "бойлер" in response.answer.lower()


def test_difference_question_after_boiler_type_clarification_explains(orchestrator) -> None:
    orchestrator.handle_chat("diff1", "нужен котёл")
    asked = orchestrator.handle_chat("diff1", "ой нужен котел")
    assert "газовый или электрический" in asked.answer.lower()

    response = orchestrator.handle_chat("diff1", "а в чём разница?")
    assert response.answer != asked.answer
    assert "газ" in response.answer.lower()
    assert "электрическ" in response.answer.lower()
    assert "дымоход" in response.answer.lower()
    assert response.products == []

    # Повторный вопрос тоже объясняет, а не вываливает список товаров
    again = orchestrator.handle_chat("diff1", "есть ли разница?")
    assert again.products == []
    assert "газ" in again.answer.lower()


def test_difference_question_wins_over_card_comparison_for_concept(orchestrator) -> None:
    orchestrator.handle_chat("diff2", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("diff2", "а чем отличается газовый от электрического?")

    assert "дымоход" in response.answer.lower()
    assert "Главное отличие — мощность" not in response.answer


def test_two_contour_boiler_filter_marks_one_contour_alternatives(sample_products) -> None:
    products = [
        *sample_products,
        Product(
            sku="GAS-ONE-24",
            name="Котел газовый Arderia SB24 одноконтурный 24 кВт",
            category_path="Котлы газовые",
            brand="ARDERIA",
            url="https://example.test/gas-one-24",
            price=36000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={
                "артикул": "GAS-ONE-24",
                "мощность": "24 кВт",
                "тип котла": "Газовый",
                "количество контуров": "одноконтурный",
            },
        ),
    ]
    orchestrator = ChatOrchestrator(products=products)

    response = orchestrator.handle_chat("dual", "газовый двухконтурный котёл на 100 м²")

    assert response.products
    assert response.products[0].sku == "GAS-ONE-24"
    answer = response.answer.lower()
    assert "двухконтур" in answer
    assert "горячую воду" in answer or "гвс" in answer


def test_one_contour_boiler_hot_water_is_not_presented_as_direct_gvs(orchestrator) -> None:
    orchestrator.handle_chat("one-gvs", "хочу газовый двухконтурный котёл на 120 квадратов")
    response = orchestrator.handle_chat("one-gvs", "а одноконтурный подойдет для горячей воды?")

    answer = response.answer.lower()
    assert "не готовит горячую воду" in answer
    assert "бойлер" in answer
    assert "не буду выдавать" in answer


def test_explicit_boiler_request_does_not_go_to_free_llm(sample_products, monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "openrouter_api_key": "test-key",
            "llm_enabled": True,
        }
    )
    monkeypatch.setattr("app.agents.orchestrator.get_settings", lambda: settings)
    llm = BadRewriteLLM(
        "Котёл KOT-2500G, насос NAS-250, трубы TUB-25, фитинги FIT-25. Итого 23000 руб."
    )
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)

    response = orchestrator.handle_chat("dual-llm", "газовый двухконтурный котел на 100 м2")

    answer = response.answer.lower()
    assert "kot-2500g" not in answer
    assert "nas-250" not in answer
    assert "tub-25" not in answer
    assert response.debug["any_llm_used"] is False
    assert "двухконтур" in answer


def test_one_vs_two_contour_question_is_explained(orchestrator) -> None:
    orchestrator.handle_chat("contour", "нужен газовый котёл")
    response = orchestrator.handle_chat("contour", "а в чём разница между одноконтурным и двухконтурным?")

    assert "горяч" in response.answer.lower()
    assert "бойлер" in response.answer.lower()
    assert response.products == []


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


def test_pending_boiler_area_followup_bypasses_consultant_llm(sample_products, monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={
            "openrouter_api_key": "test-key",
            "llm_enabled": True,
        }
    )
    monkeypatch.setattr("app.agents.orchestrator.get_settings", lambda: settings)
    bot = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Извините за путаницу. На какую площадь подбираете котёл?"),
    )

    bot.handle_chat("egvs", "нужен электрический котел")
    bot.handle_chat("egvs", "и чтобы горячую воду грел")
    response = bot.handle_chat("egvs", "на 90 м2")

    assert response.products
    assert response.debug["slots"]["area_m2"] == 90.0
    assert "На какую площадь" not in response.answer
    assert response.debug["consultant_llm_used"] is False


def test_english_llm_boiler_slots_are_normalized_before_search(sample_products) -> None:
    bot = ChatOrchestrator(products=sample_products)
    session = bot.sessions.get("slot-normalize")
    session.category = "boilers"
    session.pending_question = "На какую площадь подбираете котёл?"
    session.pending_intent_type = "broad_category"
    session.slots.update(
        {
            "boiler_type": "electric",
            "contours": "two_contour",
        }
    )
    bot.sessions.save(session)

    response = bot.handle_chat("slot-normalize", "на 90 м2")

    assert response.products
    assert response.products[0].sku == "ARD-E9"
    assert "electric" not in response.answer.lower()
    assert "Электрического двухконтурного" in response.answer


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


def test_missing_diameter_pipe_does_not_show_fittings_or_sewer(orchestrator) -> None:
    orchestrator.handle_chat("nf", "нужна труба")
    orchestrator.handle_chat("nf", "горячая вода")
    response = orchestrator.handle_chat("nf", "16 мм")

    # 16 мм трубы PPR в фиде нет — честно показываем ближайшие PPR-трубы, не фитинги/канализацию
    assert "Точного совпадения" in response.answer
    for product in response.products:
        assert "труба" in product.name.lower()
        assert "канализац" not in product.name.lower()
        assert "угольник" not in product.name.lower()
        assert "муфта" not in product.name.lower()

    followup = orchestrator.handle_chat("nf", "Мне нужна труба диаметром 16 мм а не фитинги")
    for product in followup.products:
        assert "канализац" not in product.name.lower()
        assert "110" not in product.name


def test_search_by_name_rejects_constraint_violating_match(orchestrator) -> None:
    # Раньше «труба 16 мм а не фитинги» по словам цеплялась за канализацию 110 мм
    cards = orchestrator.search_agent.search_by_name(
        "Мне нужна труба диаметром 16 мм а не фитинги",
        SearchQuery(original_text="x", category="pipes", slots={"element_type": "труба", "diameter_mm": 16}),
    )
    assert all("канализац" not in card.name.lower() for card in cards)


def test_complectation_polish_cannot_invent_pump_specs(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.description = "Электрический котёл со встроенным циркуляционным насосом."
    orchestrator = ChatOrchestrator(
        products=products,
        llm_client=BadRewriteLLM(
            "Для котла ARD-E9 есть насос — типовой для отопления, 25/6 на 180 мм. "
            "Карточка товара: https://example.test/arde9"
        ),
    )
    orchestrator.handle_chat("inv", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("inv", "а насос встроенный есть?")

    assert "25/6" not in response.answer
    assert "180 мм" not in response.answer
    assert "ARD-E9" in response.answer


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
