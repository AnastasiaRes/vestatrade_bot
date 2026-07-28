from __future__ import annotations

from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.models import IntentResult, Product, ProductCard, SearchQuery
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


def test_replacement_pump_first_asks_for_old_model_and_size(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "replacement-pump",
        "старый насос есть, нужен на замену",
    )

    assert response.products == []
    assert "модел" in response.answer.lower()
    assert "размер" in response.answer.lower()
    assert response.debug["slots"]["pump_replacement"] is True


def test_unknown_boiler_flow_asks_voltage_after_no_gas(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "unknown-boiler-voltage",
        "нужен котёл, но я не знаю какой",
    )
    second = orchestrator.handle_chat(
        "unknown-boiler-voltage",
        "70 квадратов, газа нет",
    )

    assert "газ" in first.answer.lower() and "площад" in first.answer.lower()
    assert second.products == []
    assert "220" in second.answer and "380" in second.answer


def test_voltage_followup_is_filtered_before_live_consultant() -> None:
    products = [
        Product(
            sku=f"E-{voltage}",
            name=f"Электрический котёл 12 кВт {voltage} В",
            category_path="Котлы электрические",
            url=f"https://example.test/e-{voltage}",
            price=30000 + voltage,
            stock_status="в наличии",
            attributes_normalized={
                "мощность, квт": "12",
                "тип котла": "Электрический",
                "напряжение": str(voltage),
            },
        )
        for voltage in (220, 380)
    ]
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://fake.test",
            "ollama_model": "fake",
        }
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=products,
        llm_client=BadRewriteLLM("Ошибочно игнорирую напряжение."),
    )
    bot.handle_chat("voltage-live", "электрический котёл на 100 м²")

    response = bot.handle_chat("voltage-live", "380")

    assert response.debug["slots"]["voltage_v"] == 380
    assert "ConsultantAgent" not in response.debug["agents_used"]
    assert response.products
    assert {card.sku for card in response.products} == {"E-380"}


def test_underpowered_warning_keeps_area_and_power_for_followup(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "warning-context",
        "6 кВт на 100 метров хватит?",
    )
    second = orchestrator.handle_chat(
        "warning-context",
        "но сосед говорит хватит",
    )

    assert "не хват" in first.answer.lower()
    assert "6 кВт" in second.answer and "100 м²" in second.answer
    assert "не хват" in second.answer.lower()


def test_power_tradeoff_is_deterministic_before_catalog_consult(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "power-tradeoff",
        "12 кВт или 15 кВт на дом 100 м²?",
    )
    second = orchestrator.handle_chat(
        "power-tradeoff",
        "обычный дом, без суперутепления",
    )

    assert first.products == []
    assert "утеп" in first.answer.lower()
    assert "12" in second.answer and "15" in second.answer
    assert "запас" in second.answer.lower()
    assert "12 кВт работает" not in first.answer.lower()
    assert "начать проверку с 12" in second.answer.lower()


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


def test_ppr_is_not_recommended_for_warm_floor_loops(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "wf-ppr",
        "Что такое PPR и можно ли из неё сделать петли тёплого пола?",
    )

    assert response.products == []
    assert "не используют" in response.answer.lower()
    assert "PEX" in response.answer and "PE-RT" in response.answer
    assert "подводящей магистрали" in response.answer.lower()


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
    response = bot.handle_chat("wf-cart", "50м2, водяной от котла")

    assert first.products == []
    assert response.products
    names = " ".join(product.name for product in response.products)
    assert "Угольник" not in names
    assert "FIT-ANGLE-20" not in response.answer
    assert {"PUMP-25-40", "VALVE-20-ANGLE"}.issubset(
        {product.sku for product in response.products}
    )
    assert "VTp.700.0.020" not in {product.sku for product in response.products}
    assert "обычную PPR" in response.answer
    assert "PEX" in response.answer and "PE-RT" in response.answer
    assert "Почему:" in response.answer
    assert response.debug["slots"]["project_scope"] == "warm_floor"


def test_project_cart_summary_returns_discussed_articles(orchestrator) -> None:
    orchestrator.handle_chat("wf-summary", "хочу сделать теплые полы, что для этого нужно?")
    selected = orchestrator.handle_chat("wf-summary", "50м2, водяной от котла")
    response = orchestrator.handle_chat("wf-summary", "собери артикулы корзиной")

    assert response.products
    assert {product.sku for product in response.products} == {product.sku for product in selected.products}
    assert "корзину" in response.answer.lower()
    assert "PUMP-25-40" in response.answer
    assert "не буду выдумывать количество" in response.answer.lower()
    assert "VTp.700.0.020" not in response.answer


def test_warm_floor_uses_compatible_loop_pipe_when_feed_has_one(sample_products) -> None:
    loop_pipe = Product(
        sku="PERT-16",
        name="Труба PE-RT для тёплого пола 16 мм",
        category_path="Трубы для тёплого пола",
        brand="VESTA",
        url="https://example.test/pert16",
        price=90,
        stock_status="в наличии",
        stock_qty=500,
        attributes_normalized={"материал": "PE-RT", "диаметр (мм)": "16"},
    )
    bot = ChatOrchestrator(products=[*sample_products, loop_pipe])

    bot.handle_chat("wf-loop", "хочу сделать водяной тёплый пол, что нужно?")
    response = bot.handle_chat("wf-loop", "50 м2")

    assert "PERT-16" in {product.sku for product in response.products}
    assert "VTp.700.0.020" not in {product.sku for product in response.products}


def test_project_more_question_does_not_repeat_same_selection(orchestrator) -> None:
    orchestrator.handle_chat("wf-more", "хочу сделать теплые полы, что нужно?")
    selected = orchestrator.handle_chat("wf-more", "50 м2, водяной от котла")
    response = orchestrator.handle_chat("wf-more", "а еще что нужно?")

    assert response.answer != selected.answer
    assert "Повторять тот же список не буду" in response.answer
    assert "PEX" in response.answer and "PE-RT" in response.answer


def test_project_cart_updates_component_after_followup(orchestrator) -> None:
    orchestrator.handle_chat("wf-update", "хочу сделать теплые полы, что для этого нужно?")
    orchestrator.handle_chat("wf-update", "50м2, водяной от котла")
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
    orchestrator.handle_chat("project-switch", "50м2, водяной от котла")
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

    assert response.products == []
    assert response.debug["slots"]["area_m2"] == 50.0
    assert "водяной" in response.answer.lower() and "электрический" in response.answer.lower()

    selected = bot.handle_chat("wf-area-llm", "водяной от котла")
    assert selected.products
    assert "Угольник" not in selected.answer


def test_electric_warm_floor_does_not_receive_hydronic_products(orchestrator) -> None:
    orchestrator.handle_chat("wf-electric", "хочу сделать тёплый пол, что нужно?")
    response = orchestrator.handle_chat("wf-electric", "50 м2, электрический пол")

    assert response.products == []
    assert "нагревательных матов" in response.answer.lower()
    assert "не буду подставлять" in response.answer.lower()
    assert response.debug["slots"]["warm_floor_type"] == "электрический"


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

    assert response.products == []
    assert "Дренажный" not in response.answer
    assert "циркуляц" in response.answer.lower()
    assert "расчётный расход" in response.answer.lower()
    assert "напор" in response.answer.lower()


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


@pytest.mark.parametrize(
    "message",
    [
        "канашка",
        "нужна труба для канашки 50 мм",
        "что есть для канашки?",
        "занимаюсь канашкой, нужна труба",
    ],
)
def test_sewer_colloquial_kanashka_is_understood(orchestrator, message: str) -> None:
    response = orchestrator.handle_chat(f"kanashka-{message}", message)

    assert response.debug["category"] == "sewer"
    assert response.debug["slots"].get("pipe_purpose") == "канализация"
    assert "канализац" in response.answer.lower()


def test_sewer_dialog_accumulates_slots_and_asks_only_missing(orchestrator) -> None:
    orchestrator.handle_chat("s2c", "Привет! Нужна труба")
    second = orchestrator.handle_chat("s2c", "Канализационная 50 мм")
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
    orchestrator.handle_chat("s2d", "Канализационная 50 мм")
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


def test_circulation_pump_followup_keeps_mounting_length_and_head(orchestrator) -> None:
    orchestrator.handle_chat("s3b", "циркуляционный насос, подешевле")
    response = orchestrator.handle_chat("s3b", "180 мм, напор 4 метра")

    assert response.products == []
    assert response.debug["slots"]["mounting_length_mm"] == 180
    assert response.debug["slots"]["head_m"] == 4.0
    assert "присоединение" in response.answer.lower()


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
    assert "Альтернатива" not in response.answer
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


def test_pump_for_boiler_after_boiler_selection_asks_for_duty_point(orchestrator) -> None:
    orchestrator.handle_chat("boiler-pump", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("boiler-pump", "насос к котлу")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert "allow_basic_option" not in response.debug["slots"]
    assert response.products == []
    assert "расчётный расход" in response.answer.lower()
    assert "напор" in response.answer.lower()
    assert "замена" in response.answer.lower()


def _pump_domain_products(sample_products: list[Product]) -> list[Product]:
    return [
        *sample_products,
        Product(
            sku="DRAIN-350",
            name="Дренажный насос 350 Вт",
            category_path="Насосы дренажные",
            brand="VESTA",
            url="https://example.test/drain350",
            price=2500,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=5,
            attributes_normalized={
                "артикул": "DRAIN-350",
                "тип товара": "Дренажный насос",
                "напор": "5 м",
                "мощность": "350 Вт",
            },
        ),
        Product(
            sku="WELL-550",
            name="Скважинный насос 550 Вт",
            category_path="Насосы скважинные",
            brand="VESTA",
            url="https://example.test/well550",
            price=9500,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={
                "артикул": "WELL-550",
                "тип товара": "Скважинный насос",
                "напор": "60 м",
                "мощность": "550 Вт",
            },
        ),
    ]


def test_generic_pump_inventory_question_is_not_complectation(orchestrator) -> None:
    response = orchestrator.handle_chat("pump-assortment", "Есть насосы?")

    assert response.debug["category"] == "pumps"
    assert response.products == []
    assert response.need_handoff is False
    answer = response.answer.lower()
    assert "для какой задачи нужен насос" in answer
    assert "котл" not in answer
    assert "комплектац" not in answer


def test_pump_irrigation_followup_explains_source_options(orchestrator) -> None:
    orchestrator.handle_chat("pump-irrigation", "Есть насосы?")
    response = orchestrator.handle_chat("pump-irrigation", "Для полива?")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_use"] == "полив"
    assert response.products == []
    assert response.need_handoff is False
    answer = response.answer.lower()
    assert "дренаж" in answer
    assert "скваж" in answer
    assert "циркуляцион" in answer
    assert "откуда" in answer
    assert "котл" not in answer


def test_drainage_pump_irrigation_followup_answers_suitability(sample_products) -> None:
    bot = ChatOrchestrator(products=_pump_domain_products(sample_products))
    first = bot.handle_chat("pump-fit-irrigation", "дренажный насос DRAIN-350")
    response = bot.handle_chat("pump-fit-irrigation", "Он для полива пойдет?")

    assert first.products[0].sku == "DRAIN-350"
    assert response.products[0].sku == "DRAIN-350"
    answer = response.answer.lower()
    assert "полив" in answer
    assert "можно" in answer
    assert "скважин" in answer
    assert "циркуляцион" in answer


def test_well_pump_request_after_drainage_context_starts_new_search(sample_products) -> None:
    bot = ChatOrchestrator(products=_pump_domain_products(sample_products))
    bot.handle_chat("pump-well-after-drain", "дренажный насос")
    response = bot.handle_chat("pump-well-after-drain", "Насос для скважины есть?")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_type"] == "скважинный"
    assert response.debug["slots"]["pump_use"] == "водоснабжение"
    assert response.products == []
    assert "динамическ" in response.answer.lower()
    assert "высот" in response.answer.lower()
    assert "трасс" in response.answer.lower()
    assert "комплектац" not in response.answer.lower()
    assert "котл" not in response.answer.lower()


def test_user_pump_domain_correction_is_acknowledged(sample_products) -> None:
    bot = ChatOrchestrator(products=_pump_domain_products(sample_products))
    response = bot.handle_chat(
        "pump-correction",
        "Циркуляционный насос для отопления а для полива дренажный",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "верно" in answer
    assert "циркуляцион" in answer
    assert "отоплен" in answer
    assert "дренаж" in answer
    assert "полив" in answer
    assert "монтажную длину" not in answer


def test_explicit_circulation_pump_clears_old_irrigation_purpose(orchestrator) -> None:
    session_id = "pump-purpose-switch"
    orchestrator.handle_chat(session_id, "Есть насосы?")
    orchestrator.handle_chat(session_id, "Для полива?")
    response = orchestrator.handle_chat(session_id, "Циркуляционный насос есть?")

    assert response.debug["slots"]["pump_type"] == "циркуляционный"
    assert response.debug["slots"]["pump_use"] == "отопление"
    assert "монтажн" in response.answer.lower()
    assert "полив" not in response.answer.lower()


def test_vague_wrong_answer_complaint_recovers_pump_domain(sample_products) -> None:
    bot = ChatOrchestrator(products=_pump_domain_products(sample_products))
    bot.handle_chat("pump-wrong-answer", "дренажный насос")
    bot.handle_chat("pump-wrong-answer", "Насос для скважины есть?")
    response = bot.handle_chat("pump-wrong-answer", "Что-то ты не то ответил")

    answer = response.answer.lower()
    assert response.products == []
    assert "предыдущий ответ" in answer
    assert "циркуляционн" in answer
    assert "дренаж" in answer
    assert "скважин" in answer
    assert "как я могу помочь" not in answer


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
    assert response.debug["response_llm_used"] is False
    assert response.debug["final_answer_source"] == "deterministic"


def test_pump_funnel_accepts_water_pumping_without_repeating_question(sample_products) -> None:
    drainage = Product(
        sku="68/2/8",
        name="Дренажный насос Вихрь ДН-350",
        category_path="Насосы дренажные",
        brand="Вихрь",
        url="https://example.test/drainage",
        price=2814,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={
            "артикул": "68/2/8",
            "тип товара": "Дренажный насос",
            "высота напора, м": "5",
            "мощность, вт": "350",
        },
        description=(
            "Дренажный насос используется, когда нужно откачать воду из затопленных "
            "подвалов, резервуаров или водоёмов. Подходит для грязной воды с частицами до 35 мм."
        ),
        docs_text=(
            "7. Комплект поставки. В комплект поставки входят: "
            "1. Насос. 2. Поплавковый выключатель. 3. Руководство по эксплуатации."
        ),
    )
    orchestrator = ChatOrchestrator(products=[*sample_products, drainage])

    first = orchestrator.handle_chat("drainage-purpose", "помоги выбрать насос")
    second = orchestrator.handle_chat("drainage-purpose", "откачка воды")

    assert "Для какой задачи нужен насос" in first.answer
    assert "Для какой задачи нужен насос" not in second.answer
    assert second.debug["slots"]["pump_type"] == "дренажный"
    assert second.debug["slots"]["pump_use"] == "откачка воды"
    assert second.products == []
    assert "какая вода" in second.answer.lower()
    assert "напор" in second.answer.lower()
    assert "подъём" in second.answer.lower()
    assert "не сливая систему" not in second.answer


def test_drainage_pump_purpose_and_package_do_not_use_boiler_template(sample_products) -> None:
    drainage = Product(
        sku="68/2/8",
        name="Дренажный насос Вихрь ДН-350",
        category_path="Насосы дренажные",
        brand="Вихрь",
        url="https://example.test/drainage",
        price=2814,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={"тип товара": "Дренажный насос"},
        description=(
            "Дренажный насос используется, когда нужно откачать воду из затопленных "
            "подвалов или резервуаров. Подходит для грязной воды с частицами до 35 мм."
        ),
        docs_text=(
            "7. Комплект поставки. В комплект поставки входят: "
            "1. Насос. 2. Поплавковый выключатель. 3. Руководство по эксплуатации."
        ),
    )
    orchestrator = ChatOrchestrator(products=[*sample_products, drainage])
    orchestrator.handle_chat("drainage-package", "дренажный насос 68/2/8")

    response = orchestrator.handle_chat(
        "drainage-package", "а для чего этот насос и что в него входит?"
    )
    answer = response.answer.lower()

    assert "откачать воду" in answer
    assert "поплавковый выключатель" in answer
    assert "руководство по эксплуатации" in answer
    assert "в котёл" not in answer
    assert "циркуляционный насос" not in answer
    assert [product.sku for product in response.products] == ["68/2/8"]


def test_product_purpose_and_package_handler_is_not_limited_to_pumps(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    pipe = next(product for product in products if product.sku == "VTp.700.0.020")
    pipe.docs_text = (
        "7. Комплект поставки. В комплект поставки входят: "
        "1. Труба PPR. 2. Защитная упаковка."
    )
    orchestrator = ChatOrchestrator(products=products)
    orchestrator.handle_chat("pipe-purpose-package", "артикул VTp.700.0.020")

    response = orchestrator.handle_chat(
        "pipe-purpose-package", "для чего эта труба и что входит в комплект?"
    )
    answer = response.answer.lower()

    assert "назначение" in answer
    assert "водоснабжение" in answer
    assert "отопление" in answer
    assert "защитная упаковка" in answer
    assert "в котёл" not in answer
    assert [product.sku for product in response.products] == ["VTp.700.0.020"]


def test_shown_valve_purpose_uses_feed_attribute(sample_products) -> None:
    orchestrator = ChatOrchestrator(products=sample_products)
    orchestrator.handle_chat("valve-purpose", "артикул VT.228.N.04")

    response = orchestrator.handle_chat("valve-purpose", "какое назначение у этого крана?")

    assert "Вода, отопление" in response.answer
    assert "не буду додумывать" not in response.answer.lower()
    assert [product.sku for product in response.products] == ["VT.228.N.04"]


def test_confirmed_component_answer_has_no_boiler_only_advice(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    boiler = next(product for product in products if product.sku == "ARD-E9")
    boiler.docs_text = "В котёл встроен циркуляционный насос."
    orchestrator = ChatOrchestrator(products=products)
    orchestrator.handle_chat("neutral-component", "артикул ARD-E9")

    response = orchestrator.handle_chat("neutral-component", "есть ли в нём насос?")
    answer = response.answer.lower()

    assert "подтверждение: насос" in answer
    assert "стандартной схемы" not in answer
    assert "тёплые полы" not in answer


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
    assert "предварительный" in response.answer


def test_oversized_boiler_is_only_presented_as_nearest_assortment_option(sample_products) -> None:
    gas_boiler = Product(
        sku="GAS-24",
        name="Котёл газовый одноконтурный 24 кВт",
        category_path="Котлы газовые",
        brand="VESTA",
        url="https://example.test/gas24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "мощность": "24 кВт",
            "тип котла": "Газовый",
            "количество контуров": "Одноконтурный",
        },
    )
    larger_boiler = gas_boiler.model_copy(
        update={
            "sku": "GAS-28",
            "name": "Котёл газовый одноконтурный 28 кВт",
            "url": "https://example.test/gas28",
            "price": 38000,
            "attributes_normalized": {
                "мощность": "28 кВт",
                "тип котла": "Газовый",
                "количество контуров": "Одноконтурный",
            },
        }
    )
    bot = ChatOrchestrator(products=[*sample_products, gas_boiler, larger_boiler])

    response = bot.handle_chat("oversize", "газовый одноконтурный котёл на 100 м2")

    assert [product.sku for product in response.products] == ["GAS-24"]
    assert "10–13 кВт" in response.answer
    assert "ближайший вариант" in response.answer.lower()
    assert "автоматически оптимальный" in response.answer.lower()
    assert "фид" not in response.answer.lower()


def test_boiler_consultation_remembers_shorthand_area_and_uses_passport(sample_products) -> None:
    gas_boiler = Product(
        sku="2201376",
        name=(
            "Котел газовый настенный Arderia SB28 (28 кВт, закр.камера, "
            "одноконтурный, 3х-ход.клапан)"
        ),
        category_path="Котлы газовые настенные Arderia",
        brand="Arderia",
        url="https://example.test/arderia-sb28",
        price=38535,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "мощность, квт": "28",
            "тип котла": "Газовый",
            "количество контуров": "Одноконтурный",
        },
    )
    bot = ChatOrchestrator(products=[*sample_products, gas_boiler])

    first = bot.handle_chat("strong-heating-consult", "мне нужен котел на 100")
    second = bot.handle_chat("strong-heating-consult", "газовый")
    final = bot.handle_chat("strong-heating-consult", "отопление")

    assert first.debug["slots"]["area_m2"] == 100.0
    assert "примерно на 100 м²" in first.answer.lower()
    assert "площад" not in second.answer.lower()
    assert "только для отопления" in second.answer.lower()
    assert "горячей воды" in second.answer.lower()
    assert final.debug["slots"]["area_m2"] == 100.0
    assert final.debug["slots"]["boiler_type"] == "газовый"
    assert final.debug["slots"]["contours"] == "одноконтурный"
    assert final.products and final.products[0].sku == "2201376"
    assert "5–28 кВт" in final.answer
    assert "техническому паспорту" in final.answer.lower()
    assert "фид" not in final.answer.lower()

    enriched = next(product for product in bot.search_agent.products if product.sku == "2201376")
    assert enriched.attributes_normalized["теплопроизводительность отопления, мин., квт"] == "5"
    assert enriched.attributes_normalized["теплопроизводительность отопления, макс., квт"] == "28"
    assert "стр. 16" in enriched.attributes_normalized["источник диапазона мощности"]


def test_repeated_boiler_filter_keeps_existing_selection(sample_products) -> None:
    gas_boilers = [
        Product(
            sku=f"GAS-{power}",
            name=f"Котёл газовый одноконтурный {power} кВт",
            category_path="Котлы газовые",
            url=f"https://example.test/gas{power}",
            price=35000 + power,
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={
                "мощность": f"{power} кВт",
                "тип котла": "Газовый",
                "количество контуров": "Одноконтурный",
            },
        )
        for power in [24, 28]
    ]
    bot = ChatOrchestrator(
        products=[*sample_products, *gas_boilers],
        llm_client=BadRewriteLLM("Ошибочно рекомендую другую модель."),
    )
    first = bot.handle_chat("repeat-filter", "газовый одноконтурный котёл на 100 м2")
    response = bot.handle_chat("repeat-filter", "одноконтурный")

    assert {product.sku for product in response.products} == {
        product.sku for product in first.products
    }
    assert "подборку не меняю" in response.answer.lower()
    assert "Ошибочно" not in response.answer


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
    first = orchestrator.handle_chat("s6", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("s6", "дай ссылку")

    assert "https://example.test/arde9" in response.answer
    assert response.products
    assert {card.sku for card in response.products} == {
        card.sku for card in first.products
    }


def test_link_summary_includes_name_sku_url_and_card(orchestrator) -> None:
    first = orchestrator.handle_chat("s6-summary", "PUMP-25-60")
    response = orchestrator.handle_chat(
        "s6-summary",
        "Дай итог: название, артикул и ссылку именно на этот товар.",
    )

    assert first.products and response.products
    card = first.products[0]
    assert card.name in response.answer
    assert card.sku in response.answer
    assert card.url in response.answer
    assert response.products[0].sku == card.sku


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

    # В тестовом каталоге американка есть только 1/2. Явные 3/4 нельзя
    # ослаблять на alternatives-пути и подменять другим размером.
    assert response.products == []
    assert "не наш" in response.answer.lower() or "не виж" in response.answer.lower()


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
    assert "allow_basic_option" not in response.debug["slots"]
    assert response.products == []
    assert "расчётный расход" in response.answer.lower()
    assert "напор" in response.answer.lower()


def test_same_question_is_not_asked_three_times(orchestrator) -> None:
    first = orchestrator.handle_chat("repeat1", "циркуляционный насос")
    assert "монтажную длину" in first.answer
    assert first.products == []

    second = orchestrator.handle_chat("repeat1", "я не знаю")
    assert "монтажную длину" in second.answer
    assert "измерить" in second.answer.lower() or "посмотреть" in second.answer.lower()
    assert second.products == []

    third = orchestrator.handle_chat("repeat1", "ну не знаю я")
    assert third.products == []
    assert "случайный насос" in third.answer.lower()
    assert "маркировку старого насоса" in third.answer.lower()
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

    assert "только для отопления" in response.answer.lower()
    assert "горячей воды" in response.answer.lower()
    assert response.products == []

    final = orchestrator.handle_chat("gas1", "двухконтурный")
    assert final.debug["slots"]["contours"] == "двухконтурный"


@pytest.mark.parametrize("area_answer", ["240", "240 м", "240 метров"])
def test_bare_area_answer_is_understood_while_boiler_area_is_pending(
    orchestrator,
    area_answer: str,
) -> None:
    session_id = f"bare-boiler-area-{area_answer}"
    orchestrator.handle_chat(session_id, "Котлы есть?")
    area_question = orchestrator.handle_chat(session_id, "Газовый")
    response = orchestrator.handle_chat(session_id, area_answer)

    assert "площад" in area_question.answer.lower()
    assert response.debug["slots"]["area_m2"] == 240.0
    assert response.debug["slots"]["boiler_type"] == "газовый"
    assert "только для отопления" in response.answer.lower()
    assert "горячей воды" in response.answer.lower()
    assert response.products == []


def test_more_boilers_does_not_reset_pending_type_and_area(orchestrator) -> None:
    session_id = "more-boilers-pending"
    orchestrator.handle_chat(session_id, "Котлы есть?")
    orchestrator.handle_chat(session_id, "Газовый")
    orchestrator.handle_chat(session_id, "240")
    response = orchestrator.handle_chat(session_id, "Какие еще котлы есть?")

    assert response.debug["slots"]["boiler_type"] == "газовый"
    assert response.debug["slots"]["area_m2"] == 240.0
    assert "только для отопления" in response.answer.lower()
    assert "горячей воды" in response.answer.lower()
    assert "газовый или электрический" not in response.answer.lower()


def test_boiler_type_correction_uses_feed_and_respects_negation(sample_products) -> None:
    products = [
        *sample_products,
        Product(
            sku="3301679",
            name="Котел газовый Ariston CLAS XC SYSTEM 24 FF NG",
            category_path="Котлы газовые",
            brand="Ariston",
            url="https://example.test/ariston-clas",
            price=78571,
            currency="RUB",
            stock_status="нет в наличии",
            stock_qty=0,
            attributes_normalized={
                "тип котла": "Газовый",
                "количество контуров": "Одноконтурный",
                "мощность": "24 кВт",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    first = bot.handle_chat(
        "boiler-type-correction",
        "газовый одноконтурный котел Ariston на 240 м²",
    )
    response = bot.handle_chat(
        "boiler-type-correction",
        "Ты дал котел, а он не электрический, а газовый",
    )

    assert first.products and first.products[0].sku == "3301679"
    assert response.debug["slots"]["boiler_type"] == "газовый"
    assert response.products and response.products[0].sku == "3301679"
    assert "вы правы" in response.answer.lower()
    assert "газовый котёл" in response.answer.lower()
    assert "а не электрический" in response.answer.lower()


def test_question_about_shown_ariston_type_is_answered_from_feed(sample_products) -> None:
    products = [
        *sample_products,
        Product(
            sku="3301679",
            name="Котел газовый Ariston CLAS XC SYSTEM 24 FF NG",
            category_path="Котлы газовые",
            brand="Ariston",
            url="https://example.test/ariston-clas",
            price=78571,
            currency="RUB",
            stock_status="нет в наличии",
            stock_qty=0,
            attributes_normalized={
                "тип котла": "Газовый",
                "количество контуров": "Одноконтурный",
                "мощность": "24 кВт",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    first = bot.handle_chat(
        "shown-ariston-type",
        "газовый одноконтурный котел Аристон на 240 м²",
    )
    yes_no = bot.handle_chat("shown-ariston-type", "Он электрический?")
    open_question = bot.handle_chat("shown-ariston-type", "Тогда какой он?")

    assert first.products and first.products[0].sku == "3301679"
    assert first.debug["slots"]["brand"] == "Ariston"
    assert yes_no.products and yes_no.products[0].sku == "3301679"
    assert yes_no.debug["slots"]["boiler_type"] == "газовый"
    assert "нет:" in yes_no.answer.lower()
    assert "газовый котёл" in yes_no.answer.lower()
    assert "площад" not in yes_no.answer.lower()
    assert "газовый котёл" in open_question.answer.lower()


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
    preview = orchestrator.handle_chat("h1", "передай менеджеру")

    assert preview.need_handoff is True
    assert preview.handoff_status == "awaiting_contact"
    assert "менеджер" in preview.answer.lower()
    assert "Более дешёвых" not in preview.answer
    assert not (tmp_path / "handoff.jsonl").exists()

    consent = orchestrator.handle_chat("h1", "+7 999 123-45-67")
    assert consent.handoff_status == "awaiting_consent"
    assert "подтверд" in consent.answer.lower()
    assert not (tmp_path / "handoff.jsonl").exists()

    response = orchestrator.handle_chat("h1", "подтверждаю передачу")
    assert response.handoff_status == "locally_recorded"
    assert response.handoff_ticket_id
    assert response.handoff_ticket_id in response.answer
    log_text = (tmp_path / "handoff.jsonl").read_text(encoding="utf-8")
    assert '"session_id": "h1"' in log_text
    assert "ARD-E9" in log_text
    assert response.handoff_ticket_id in log_text


def test_manager_contact_question_does_not_create_handoff(sample_products, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    response = orchestrator.handle_chat("h-contact", "как связаться с менеджером?")

    assert response.need_handoff is False
    assert response.products == []
    assert "оставьте телефон" in response.answer.lower()
    assert "email" in response.answer.lower()
    assert not (tmp_path / "handoff.jsonl").exists()


def test_human_contact_synonyms_do_not_create_handoff(sample_products, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    messages = [
        "как связаться с консультантом?",
        "как связаться с сотрудником?",
        "как связаться с продавцом?",
        "как связаться с продовцом?",
        "как связаться с администратором?",
        "как связаться с реальным человеком?",
    ]

    for index, message in enumerate(messages):
        response = orchestrator.handle_chat(f"h-contact-syn-{index}", message)

        assert response.need_handoff is False
        assert response.products == []
        assert "оставьте телефон" in response.answer.lower()
        assert "email" in response.answer.lower()

    assert not (tmp_path / "handoff.jsonl").exists()


def test_human_contact_typos_do_not_create_handoff(sample_products, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    messages = [
        "как свезаться с минеджером?",
        "как связатсья с кансультантом?",
        "как связаться с сатрудником?",
        "как связаться с прадавцом?",
        "как связаться с адмнистратором?",
        "как связаться с риальным челавеком?",
        "какие кантакты?",
    ]

    for index, message in enumerate(messages):
        response = orchestrator.handle_chat(f"h-contact-typo-{index}", message)

        assert response.need_handoff is False
        assert response.products == []
        assert "оставьте телефон" in response.answer.lower()
        assert "email" in response.answer.lower()

    assert not (tmp_path / "handoff.jsonl").exists()


def test_handoff_without_contact_does_not_promise_callback(sample_products, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    response = orchestrator.handle_chat("h-no-contact", "передай менеджеру")

    assert response.need_handoff is True
    assert "оставьте телефон" in response.answer.lower()
    assert "свяжется с вами" not in response.answer.lower()


def test_human_transfer_synonyms_create_handoff_without_callback_promise(
    sample_products,
    tmp_path,
) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    messages = [
        "позови консультанта",
        "соедини с сотрудником",
        "нужен продавец",
        "позови продовца",
        "передай администратору",
        "хочу реального человека",
        "дайте оператора",
    ]

    for index, message in enumerate(messages):
        response = orchestrator.handle_chat(f"h-transfer-syn-{index}", message)

        assert response.need_handoff is True
        assert "оставьте телефон" in response.answer.lower()
        assert "свяжется с вами" not in response.answer.lower()

    assert not (tmp_path / "handoff.jsonl").exists()


def test_human_transfer_typos_create_handoff_without_callback_promise(
    sample_products,
    tmp_path,
) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    orchestrator = ChatOrchestrator(products=sample_products, settings=settings)

    messages = [
        "пазови кансультанта",
        "соедените с сатрудником",
        "нужин прадавец",
        "периключи на адмнистратора",
        "хачу реального челавека",
        "даите опиратора",
    ]

    for index, message in enumerate(messages):
        response = orchestrator.handle_chat(f"h-transfer-typo-{index}", message)

        assert response.need_handoff is True
        assert "оставьте телефон" in response.answer.lower()
        assert "свяжется с вами" not in response.answer.lower()

    assert not (tmp_path / "handoff.jsonl").exists()


def test_handoff_process_challenge_is_answered_transparently(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "h-challenge",
        "а как ты это передал, если никакую информацию я не давал?",
    )

    assert response.need_handoff is False
    assert "вы правы" in response.answer.lower()
    assert "нужен контакт" in response.answer.lower()


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


def test_exact_sku_context_followup_keeps_card_and_category(sample_products) -> None:
    llm = _CtxLLM(
        "Артикул PUMP-25-60: циркуляционный насос, напор 6 м, монтажная длина 180 мм."
    )
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)

    first = orchestrator.handle_chat("ctx-exact", "PUMP-25-60")
    response = orchestrator.handle_chat(
        "ctx-exact",
        "Какие основные характеристики у показанного товара? Назови его артикул.",
    )

    assert first.products and first.products[0].sku == "PUMP-25-60"
    assert response.products and response.products[0].sku == "PUMP-25-60"
    assert "PUMP-25-60" in response.answer
    assert "Для какой задачи нужен насос" not in response.answer
    assert response.debug["response_llm_requested"] is False
    assert "напор: 6 м" in response.answer.lower()
    assert "монтажная длина: 180 мм" in response.answer.lower()


def test_context_answer_that_omits_requested_sku_uses_grounded_fallback(
    sample_products,
) -> None:
    llm = _CtxLLM("Это циркуляционный насос с напором 6 м.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx-required-sku", "PUMP-25-60")

    response = orchestrator.handle_chat(
        "ctx-required-sku",
        "Какой практический вывод можно сделать по этой карточке? Назови артикул.",
    )

    assert response.products and response.products[0].sku == "PUMP-25-60"
    assert "Артикул: PUMP-25-60" in response.answer
    assert response.debug["response_llm_output_accepted"] is False
    assert "omitted requested SKU" in str(
        response.debug["response_llm_rejection_reason"]
    )


def test_price_question_is_not_mistaken_for_repeated_boiler_filter(sample_products) -> None:
    llm = _CtxLLM("Самый дорогой из показанных — ARD-E9, он стоит 38000 RUB.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx-price", "что есть в наличии из котлов?")

    response = orchestrator.handle_chat("ctx-price", "а сколько стоит самый дорогой?")

    assert "38000" in response.answer
    assert "параметр уже учтён" not in response.answer.lower()


def test_exact_sku_pronoun_price_followup_keeps_card(sample_products) -> None:
    llm = _CtxLLM("unused")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    first = orchestrator.handle_chat("ctx-pronoun-price", "PUMP-25-60")

    response = orchestrator.handle_chat(
        "ctx-pronoun-price",
        "Сколько он стоит? Назови также артикул.",
    )

    assert first.products and first.products[0].sku == "PUMP-25-60"
    assert response.products and response.products[0].sku == "PUMP-25-60"
    assert "PUMP-25-60" in response.answer
    assert "цена" in response.answer.lower()


def test_explicit_card_followup_overrides_small_talk_intent(sample_products) -> None:
    llm = _CtxLLM("Артикул PUMP-25-60. Проверьте данные карточки перед покупкой.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("ctx-card-small-talk", "PUMP-25-60")
    orchestrator.intent_router.route = lambda _message, _session: IntentResult(
        intent_type="small_talk",
        category="other",
        confidence=0.8,
        is_topic_change=True,
    )

    response = orchestrator.handle_chat(
        "ctx-card-small-talk",
        "Дай осторожную рекомендацию по этой карточке и назови артикул.",
    )

    assert response.products and response.products[0].sku == "PUMP-25-60"
    assert response.debug["response_llm_requested"] is True
    assert "PUMP-25-60" in response.answer


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


def test_passport_question_is_answered_deterministically_from_passport(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.docs_text = "ПАСПОРТ. В комплект поставки входит котёл и руководство по эксплуатации."
    llm = _CtxLLM("В паспорте нет информации о комплекте.")
    orchestrator = ChatOrchestrator(products=products, llm_client=llm)
    orchestrator.handle_chat("psp", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("psp", "ответь по паспорту, что входит в полную комплектацию")

    assert response.debug["response_llm_used"] is False
    assert "руководство" in response.answer.lower()
    assert "нет информации" not in response.answer.lower()


def test_numbered_passport_package_is_extracted_without_following_sections(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ARD-E9":
            product.docs_text = (
                "7. Комплект поставки. В комплект поставки входят: "
                "1. Котёл. 2. Руководство. Паспорт. Гарантийный талон. "
                "3. Табличка с маркировкой. 4. Монтажная планка. "
                "8. Серийный номер котла. 1. Код серии."
            )
    orchestrator = ChatOrchestrator(products=products)
    orchestrator.handle_chat("psp-numbered", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat(
        "psp-numbered", "ответь по паспорту, что входит в полную комплектацию"
    )

    assert "Монтажная планка" in response.answer
    assert "Серийный номер" not in response.answer
    assert "Код серии" not in response.answer


def test_common_boiler_typos_still_start_boiler_funnel(orchestrator) -> None:
    response = orchestrator.handle_chat("typo-boiler", "нужон кател газовы")

    assert response.products == []
    assert response.debug["category"] == "boilers"
    assert response.debug["slots"]["boiler_type"] == "газовый"
    assert "площад" in response.answer.lower()


def test_common_boiler_typos_bypass_freeform_consultant(sample_products) -> None:
    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://llm.test",
            "ollama_model": "test-model",
            "ollama_model_strong": "test-model",
        }
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=sample_products,
        llm_client=BadRewriteLLM("Сразу показываю котлы без уточнения площади."),
    )

    response = bot.handle_chat("typo-boiler-live", "нужон кател газовы")

    assert response.products == []
    assert "площад" in response.answer.lower()
    assert "сразу показываю" not in response.answer.lower()
    assert "ConsultantAgent" not in response.debug["agents_used"]

    price = bot.handle_chat("typo-boiler-live", "сколько стоют")
    assert price.products == []
    assert "сначала нужна площадь" in price.answer.lower()
    assert "ConsultantAgent" not in price.debug["agents_used"]


def test_pump_fit_question_uses_context_not_new_boiler_search(sample_products) -> None:
    llm = _CtxLLM("Это циркуляционный насос для отопления, подходит к котлам отопления.")
    orchestrator = ChatOrchestrator(products=sample_products, llm_client=llm)
    orchestrator.handle_chat("fit", "циркуляционный насос 25/6 180")
    response = orchestrator.handle_chat("fit", "а под какой котёл этот насос подходит?")

    assert "газовый или электрический" not in response.answer.lower()
    assert "циркуляционный насос" in response.answer.lower()
    assert "не указана привязка" in response.answer.lower()
    assert "не буду подтверждать совместимость" in response.answer.lower()
    assert "выходное отверстие" not in response.answer.lower()


def test_open_complectation_distinguishes_package_from_builtin_components(sample_products) -> None:
    orchestrator = ChatOrchestrator(products=_boiler_with_builtins(sample_products))
    orchestrator.handle_chat("ck", "электрический котёл на 100 м²")
    response = orchestrator.handle_chat("ck", "а что входит в комплект?")

    assert response.need_handoff is False
    assert response.debug["intent"] == "complectation"
    assert "насос" in response.answer.lower()
    assert "бак" in response.answer.lower()
    assert "не перечень содержимого коробки" in response.answer.lower()
    assert "ARD-E9" in response.answer


def test_complectation_asks_which_product_when_several_are_shown(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    for product in products:
        if product.sku == "ECA-6":
            product.description = "Электрический котёл. Встроенный циркуляционный насос."
    orchestrator = ChatOrchestrator(products=products)
    # «что есть из котлов» показывает несколько позиций
    orchestrator.handle_chat("multi", "что есть в наличии из котлов?")
    response = orchestrator.handle_chat("multi", "а насос в комплекте идёт?")

    assert "по какой из показанных моделей" in response.answer.lower()
    assert "ARD-E9" in response.answer and "ECA-6" in response.answer
    assert response.need_handoff is False
    assert response.debug["intent"] == "complectation"

    selected = orchestrator.handle_chat("multi", "ECA-6")

    assert "ECA-6" in selected.answer
    assert "насос" in selected.answer.lower()
    assert selected.need_handoff is False


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


def test_two_contour_boiler_filter_does_not_relax_contours_implicitly(sample_products) -> None:
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

    assert response.products == []
    assert "не вижу точного совпадения" in response.answer.lower()
    assert "GAS-ONE-24" not in response.answer


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


def test_gas_vs_electric_question_does_not_reask_area_from_same_message(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "gve-area",
        "что лучше для дома 100 м2: газовый или электрический котёл?",
    )

    assert "Площадь 100 м² уже учёл" in response.answer
    assert "какая площадь" not in response.answer.lower()


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

    # The fixture has no product whose card confirms two circuits. Do not
    # silently show an unconfirmed model merely to satisfy the old funnel.
    assert response.products == []
    assert response.debug["slots"]["area_m2"] == 90.0
    assert "На какую площадь" not in response.answer
    assert "точного совпадения" in response.answer
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

    assert response.products == []
    assert "electric" not in response.answer.lower()
    assert "электрический" in response.answer.lower()
    assert "двухконтурный" in response.answer.lower()


def test_exact_product_name_returns_product_without_interrogation(orchestrator) -> None:
    response = orchestrator.handle_chat("name1", "Труба PPR 20 мм PN20")

    assert response.products
    assert response.products[0].sku == "VTp.700.0.020"
    assert "Труба для чего" not in response.answer


def test_pipe_meters_are_quantity_not_diameter(orchestrator) -> None:
    session_id = "pipe-total-meters"
    orchestrator.handle_chat(session_id, "Нужна труба")
    orchestrator.handle_chat(
        session_id,
        "для горячей воды внутри дома, PPR, температура 70 °C, давление 6 бар",
    )
    meters = orchestrator.handle_chat(session_id, "20 метров")

    assert meters.debug["slots"]["total_length_m"] == 20.0
    assert "diameter_mm" not in meters.debug["slots"]
    assert "диаметр" in meters.answer.lower()
    assert meters.products == []

    final = orchestrator.handle_chat(session_id, "20 мм")
    assert final.debug["slots"]["diameter_mm"] == 20
    assert final.debug["slots"]["total_length_m"] == 20.0
    assert final.products
    assert all("20" in product.name for product in final.products)
    assert "общий метраж 20 м" in final.answer.lower()


@pytest.mark.parametrize(
    ("message", "diameter", "length"),
    [
        ("нужна наружная канализационная труба 110 мм длиной 1000 мм", 110, 1000),
        ("нужна наружная канализационная труба 110х1000", 110, 1000),
    ],
)
def test_sewer_diameter_and_piece_length_are_separate(
    orchestrator,
    message: str,
    diameter: int,
    length: int,
) -> None:
    response = orchestrator.handle_chat(f"sewer-units-{message}", message)

    assert response.debug["slots"]["diameter_mm"] == diameter
    assert response.debug["slots"]["length_mm"] == length
    assert response.products
    assert response.products[0].sku == "OUT-110-1000"


def test_circulation_pump_bare_meters_followup_is_head(orchestrator) -> None:
    session_id = "pump-head-meters"
    orchestrator.handle_chat(session_id, "циркуляционный насос")
    orchestrator.handle_chat(session_id, "180 мм")
    response = orchestrator.handle_chat(session_id, "4 метра")

    assert response.debug["slots"]["mounting_length_mm"] == 180
    assert response.debug["slots"]["head_m"] == 4.0
    assert response.products == []
    assert "присоединение" in response.answer.lower()


def test_circulation_pump_bare_meters_in_full_request_are_head(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "pump-head-direct",
        "циркуляционный насос для отопления 180 мм 4 метра",
    )

    assert response.debug["slots"]["mounting_length_mm"] == 180
    assert response.debug["slots"]["head_m"] == 4.0
    assert response.products == []
    assert "присоединение" in response.answer.lower()


def test_direct_boiler_meters_are_area(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "boiler-direct-meters",
        "газовый одноконтурный котёл на 240 м",
    )

    assert response.debug["slots"]["area_m2"] == 240.0


def test_radiator_dimensions_select_radiator_not_valve(sample_products) -> None:
    radiator = Product(
        sku="RAD-500-6",
        name="Радиатор биметаллический 500 х 80 6 секций",
        category_path="Радиаторы отопления",
        url="https://example.test/rad-500-6",
        price=10000,
        stock_status="в наличии",
        attributes_normalized={
            "межосевое расстояние, мм": "500",
            "количество секций": "6",
        },
    )
    bot = ChatOrchestrator(products=[*sample_products, radiator])

    response = bot.handle_chat("radiator-dimensions", "нужен радиатор 500 мм 6 секций")

    assert response.debug["category"] == "radiators"
    assert response.debug["slots"]["radiator_size_mm"] == 500
    assert response.debug["slots"]["sections"] == 6
    assert [product.sku for product in response.products] == ["RAD-500-6"]


def test_ppr_reducer_keeps_both_diameters(sample_products) -> None:
    reducer = Product(
        sku="PPR-40-25",
        name="Муфта переходная PPR 40-25 мм",
        category_path="Фитинги PPR",
        url="https://example.test/ppr-40-25",
        price=200,
        stock_status="в наличии",
        attributes_normalized={"диаметр (мм)": "40"},
    )
    bot = ChatOrchestrator(products=[*sample_products, reducer])

    response = bot.handle_chat("ppr-reducer", "нужна муфта PPR 40 на 25 мм")

    assert response.debug["category"] == "fittings"
    assert response.debug["slots"]["diameter_mm"] == 40
    assert response.debug["slots"]["secondary_diameter_mm"] == 25
    assert [product.sku for product in response.products] == ["PPR-40-25"]


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


def test_long_product_doc_keeps_actual_package_section(tmp_path, sample_products) -> None:
    from app.docs_loader import load_docs_for_products

    products = [product.model_copy(deep=True) for product in sample_products]
    long_manual = (
        "Оглавление. 7. Комплект поставки 22. "
        + ("Общие сведения и правила эксплуатации. " * 350)
        + "7. Комплект поставки. В комплект поставки входят: 1. Котёл. "
        "2. Руководство по эксплуатации. 3. Монтажная планка."
    )
    (tmp_path / "ARD-E9.txt").write_text(long_manual, encoding="utf-8")

    load_docs_for_products(products, tmp_path)

    product = next(product for product in products if product.sku == "ARD-E9")
    assert product.docs_text is not None
    assert "Монтажная планка" in product.docs_text
    assert len(product.docs_text) <= 8000


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


def test_incomplete_hot_water_pipe_request_does_not_show_products(orchestrator) -> None:
    orchestrator.handle_chat("nf", "нужна труба")
    orchestrator.handle_chat("nf", "горячая вода")
    response = orchestrator.handle_chat("nf", "16 мм")

    assert response.products == []
    assert "участ" in response.answer.lower()
    assert "температур" in response.answer.lower()
    assert "давлен" in response.answer.lower()

    followup = orchestrator.handle_chat("nf", "Мне нужна труба диаметром 16 мм а не фитинги")
    assert followup.products == []
    assert "участ" in followup.answer.lower()


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


def test_water_leak_emergency_bypasses_catalog_and_keeps_safe_followup(orchestrator) -> None:
    orchestrator.handle_chat("emergency", "нужна система отопления")

    first = orchestrator.handle_chat(
        "emergency",
        "ПОМОГИТЕ прорвало трубу под мойкой, заливает всё и соседей снизу!",
    )

    assert first.products == []
    assert first.debug["intent"] == "emergency"
    assert first.debug["any_llm_used"] is False
    assert "перекройте" in first.answer.lower()
    assert "электр" in first.answer.lower()
    assert "аварийн" in first.answer.lower()
    assert "сосед" in first.answer.lower()

    contained = orchestrator.handle_chat(
        "emergency",
        "воду перекрыл. теперь что нужно купить чтобы починить?",
    )

    assert contained.products == []
    assert contained.debug["intent"] == "emergency"
    assert "где именно" in contained.answer.lower()
    assert "материал" in contained.answer.lower()
    assert "диаметр" in contained.answer.lower() or "резьб" in contained.answer.lower()
    assert "площад" not in contained.answer.lower()
    assert "источник тепла" not in contained.answer.lower()


def test_hot_radiator_rupture_uses_burn_safe_emergency_flow(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "heating-emergency",
        "Прорвало радиатор, льётся кипяток!",
    )

    assert first.products == []
    assert first.debug["intent"] == "emergency"
    assert first.debug["any_llm_used"] is False
    assert "ожог" in first.answer.lower()
    assert "не касайтесь" in first.answer.lower()
    assert "аварийн" in first.answer.lower()

    contained = orchestrator.handle_chat(
        "heating-emergency",
        "радиатор перекрыл, больше не течёт",
    )
    assert contained.products == []
    assert "остын" in contained.answer.lower()
    assert "модель" in contained.answer.lower()


def test_vague_under_sink_request_keeps_drain_followup_out_of_valve_flow(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "sink-flow",
        "нужна эта фигня под раковину",
    )
    second = orchestrator.handle_chat("sink-flow", "слив")

    first_answer = first.answer.lower()
    second_answer = second.answer.lower()
    assert first.products == []
    assert "слив" in first_answer
    assert "сифон" in first_answer
    assert "кран" in first_answer
    assert second.products == []
    assert "слив" in second_answer or "сифон" in second_answer
    assert "размер выпуска" in second_answer
    assert "подключ" in second_answer
    assert "кран" not in second_answer
    assert "площад" not in second_answer


def test_project_cart_rejects_accessories_misclassified_as_main_roles() -> None:
    products = [
        Product(
            sku="24432",
            name="Трос из нерж. стали для крепления скважинного насоса",
            category_path="Насосное оборудование",
            url="https://example.test/cable",
            price=100,
            stock_status="в наличии",
            stock_qty=5,
        ),
        Product(
            sku="SK-CASE",
            name="Кожух для трубы 16 (диаметр 25) синий",
            category_path="Трубы и кожухи",
            url="https://example.test/casing",
            price=20,
            stock_status="в наличии",
            stock_qty=20,
            description="Не предназначен для транспортировки жидкости или газа.",
        ),
        Product(
            sku="DECOR-CUP",
            name="Чашка декоративная хромированная",
            category_path="Краны и запорная арматура",
            url="https://example.test/cup",
            price=30,
            stock_status="в наличии",
            stock_qty=30,
        ),
        Product(
            sku="WELL-PUMP",
            name="Насос скважинный 3-40",
            category_path="Насосы скважинные",
            url="https://example.test/well-pump",
            price=8000,
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={"тип товара": "Насос"},
        ),
    ]
    bot = ChatOrchestrator(products=products)
    bot.handle_chat("safe-cart", "собери водоснабжение для дома")

    selected = bot.handle_chat(
        "safe-cart",
        "скважина, глубина 40 м, собери комплект",
    )

    assert {product.sku for product in selected.products} == {"WELL-PUMP"}
    for bad_sku in ["24432", "SK-CASE", "DECOR-CUP"]:
        assert bad_sku not in selected.answer
    assert "не добавил артикулы" in selected.answer.lower()
    assert "аксессуар вместо основного узла" in selected.answer.lower()

    summary = bot.handle_chat("safe-cart", "собери корзину")
    assert {product.sku for product in summary.products} == {"WELL-PUMP"}
    assert all(bad_sku not in summary.answer for bad_sku in ["24432", "SK-CASE", "DECOR-CUP"])


def test_handoff_keeps_original_boiler_requirement(sample_products, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "handoff.jsonl"}
    )
    bot = ChatOrchestrator(products=sample_products, settings=settings)

    bot.handle_chat("handoff-boiler", "нужен котел на большой коттедж 400 м2 с бойлером")
    preview = bot.handle_chat("handoff-boiler", "передай менеджеру")
    assert preview.need_handoff is True
    assert "с бойлером" in preview.answer.lower()
    assert not (tmp_path / "handoff.jsonl").exists()

    bot.handle_chat("handoff-boiler", "client@example.test")
    response = bot.handle_chat("handoff-boiler", "подтверждаю передачу")
    log_text = (tmp_path / "handoff.jsonl").read_text(encoding="utf-8")
    assert response.handoff_status == "locally_recorded"
    assert "с бойлером" in log_text.lower()
    assert "key_requirements" in log_text
    assert "400" in log_text


def test_complex_boiler_binding_clarifies_then_creates_grounded_handoff(
    sample_products,
    tmp_path,
) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "complex-handoff.jsonl"}
    )
    bot = ChatOrchestrator(products=sample_products, settings=settings)

    first = bot.handle_chat(
        "complex-handoff",
        "подберите обвязку котла, бойлера и теплого пола, я вообще не разбираюсь",
    )
    second = bot.handle_chat(
        "complex-handoff",
        "дом 180 метров, котёл не выбран, нужен ещё бойлер",
    )
    third = bot.handle_chat(
        "complex-handoff",
        "бойлер 150 л, тёплый пол 60 м², 6 контуров",
    )

    first_answer = first.answer.lower()
    assert first.need_handoff is False
    assert first.products == []
    assert "площад" in first_answer
    assert "кот" in first_answer
    assert "бойлер" in first_answer
    assert second.need_handoff is False
    assert second.products == []
    assert "объём" in second.answer.lower() or "модел" in second.answer.lower()
    assert "площадь тёплого пола" in second.answer.lower()
    assert "число контуров" in second.answer.lower()
    assert third.need_handoff is True
    assert third.products == []
    assert "менеджер" in third.answer.lower()
    assert "с бойлером" in third.answer.lower()
    assert third.debug["slots"]["area_m2"] == 180.0
    assert third.debug["slots"]["boiler_volume_l"] == 150.0
    assert third.debug["slots"]["warm_floor_area_m2"] == 60.0
    assert third.debug["slots"]["warm_floor_contours"] == 6
    assert not (tmp_path / "complex-handoff.jsonl").exists()

    bot.handle_chat("complex-handoff", "+7 999 555-44-33")
    submitted = bot.handle_chat("complex-handoff", "подтверждаю передачу")
    assert submitted.handoff_status == "locally_recorded"
    assert submitted.handoff_ticket_id
    log_text = (tmp_path / "complex-handoff.jsonl").read_text(encoding="utf-8").lower()
    assert "180" in log_text
    assert "150" in log_text
    assert "60" in log_text
    assert "6" in log_text
    assert "обвяз" in log_text
    assert "бойлер" in log_text
    assert "тепл" in log_text


def test_complex_handoff_does_not_treat_warm_floor_subarea_as_house_area(
    sample_products,
    tmp_path,
) -> None:
    settings = get_settings().model_copy(
        update={"handoff_log_path": tmp_path / "complex-missing-house.jsonl"}
    )
    bot = ChatOrchestrator(products=sample_products, settings=settings)

    bot.handle_chat(
        "complex-missing-house",
        "подберите обвязку котла, бойлера и теплого пола, я не разбираюсь",
    )
    response = bot.handle_chat(
        "complex-missing-house",
        "котёл не выбран, бойлер 150 л, тёплый пол 60 м², 6 контуров",
    )

    assert response.need_handoff is False
    assert "площадь дома" in response.answer.lower()
    assert response.debug["slots"].get("area_m2") is None
    assert response.debug["slots"]["warm_floor_area_m2"] == 60.0


def test_underpowered_boiler_question_is_answered_before_consultant(sample_products) -> None:
    bot = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Да, 6 кВт хватит с большим запасом."),
    )

    response = bot.handle_chat("underpowered", "Хватит ли 6 кВт на дом 100 м²?")

    assert response.products == []
    assert "не хватит" in response.answer.lower()
    assert "около 10 квт" in response.answer.lower()
    assert "большим запасом" not in response.answer.lower()
    assert response.debug["consultant_llm_used"] is False


def test_pump_stock_without_context_keeps_task_and_parameter_clarification(
    sample_products,
) -> None:
    bot = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Напишите артикул или модель."),
    )

    response = bot.handle_chat("pump-stock", "есть насос в наличии?")

    answer = response.answer.lower()
    assert response.products == []
    assert "какой насос" in answer
    assert "для какой задачи" in answer
    assert "тип" in answer
    assert "напор" in answer or "источник воды" in answer
    assert response.debug["response_llm_requested"] is False


def test_unresolved_builtin_boiler_question_survives_exact_sku(sample_products) -> None:
    bot = ChatOrchestrator(products=sample_products)

    def route(message: str, _session) -> IntentResult:
        if "ARD" in message.upper():
            return IntentResult(
                intent_type="exact_sku",
                category="boilers",
                confidence=1.0,
                slots={"sku": "ARD-E9"},
            )
        return IntentResult(
            intent_type="broad_category",
            category="boilers",
            confidence=1.0,
        )

    bot.intent_router.route = route  # type: ignore[method-assign]
    first = bot.handle_chat("pending-boiler", "у этого котла встроенный бойлер есть?")
    response = bot.handle_chat("pending-boiler", "ARD-E9")

    assert "модель" in first.answer.lower() or "артикул" in first.answer.lower()
    assert response.need_handoff is True
    assert response.products == []
    assert "бойлер" in response.answer.lower()
    assert "Не вижу подтверждения комплектации" in response.answer


def test_unknown_boiler_binding_asks_model_and_system_before_consultant(
    sample_products,
) -> None:
    bot = ChatOrchestrator(
        products=sample_products,
        llm_client=BadRewriteLLM("Уточните площадь дома и источник тепла."),
    )

    response = bot.handle_chat("unknown-binding", "чем его обвязать?")

    answer = response.answer.lower()
    assert response.products == []
    assert "котл" in answer
    assert "модель" in answer or "артикул" in answer
    assert "систем" in answer
    assert "площад" not in answer
    assert response.debug["consultant_llm_used"] is False


def test_explicit_confirmation_keeps_last_product_even_if_router_says_pumps(
    orchestrator,
) -> None:
    first = orchestrator.handle_chat("confirm-category", "PUMP-25-60")
    assert first.products and first.products[0].sku == "PUMP-25-60"

    orchestrator.intent_router.route = lambda _message, _session: IntentResult(  # type: ignore[method-assign]
        intent_type="unknown",
        category="pumps",
        confidence=0.5,
    )
    response = orchestrator.handle_chat("confirm-category", "это точно он?")

    assert response.products
    assert response.products[0].sku == "PUMP-25-60"
    assert "PUMP-25-60" in response.answer
    assert "https://example.test/pump2560" in response.answer


def test_underpowered_filter_prefers_structured_sku_power_over_series_description() -> None:
    weak = Product(
        sku="SOLO-3",
        name="Котел электрический ZOTA Solo 3",
        description="Линейка Solo выпускается в вариантах до 9 кВт.",
        category_path="Котлы электрические",
        url="https://example.test/solo-3",
        price=30000,
        stock_status="в наличии",
        attributes_normalized={"мощность, кВт": "3", "тип котла": "Электрический"},
    )
    adequate = Product(
        sku="SOLO-12",
        name="Котел электрический ZOTA Solo 12",
        description="Линейка также включает модели 3 кВт.",
        category_path="Котлы электрические",
        url="https://example.test/solo-12",
        price=40000,
        stock_status="в наличии",
        attributes_normalized={"мощность, кВт": "12", "тип котла": "Электрический"},
    )
    bot = ChatOrchestrator(products=[weak, adequate])
    query = SearchQuery(
        original_text="электрический котел на 100 м²",
        category="boilers",
        slots={"area_m2": 100.0},
    )

    assert bot.guardrails._extract_power_kw(weak) == 3.0
    assert bot.ranking_agent._extract_power_kw(weak) == 3.0
    assert [p.sku for p in bot._drop_underpowered_boilers([weak, adequate], query)] == [
        "SOLO-12"
    ]


def test_inflected_pipe_request_keeps_context_for_water_followup(orchestrator) -> None:
    first = orchestrator.handle_chat("pipe-inflection", "надо трубу, не знаю какую")
    second = orchestrator.handle_chat("pipe-inflection", "в квартиру, для воды")

    assert first.debug["category"] == "pipes"
    assert "отоп" in first.answer.lower() and "канал" in first.answer.lower()
    assert second.debug["category"] == "pipes"
    assert "холод" in second.answer.lower()
    assert "диаметр" in second.answer.lower()


def test_compact_typo_pump_notation_256_130_is_parsed() -> None:
    product = Product(
        sku="PUMP-25-6-130",
        name="Насос циркуляционный 25/6 130",
        category_path="Насосы циркуляционные",
        url="https://example.test/pump-25-6-130",
        price=3000,
        stock_status="в наличии",
        attributes_normalized={
            "тип товара": "Циркуляционный насос",
            "присоединение": "25",
            "максимальный напор, м": "6",
            "монтажная длина, мм": "130",
        },
    )
    bot = ChatOrchestrator(products=[product])

    response = bot.handle_chat("compact-pump", "нсос 256 130")

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["connection_size"] == 25
    assert response.debug["slots"]["head_m"] == 6.0
    assert response.debug["slots"]["mounting_length_mm"] == 130
    assert response.products and response.products[0].sku == product.sku


def test_boiler_power_challenge_reuses_previous_sizing_context() -> None:
    bot = ChatOrchestrator(products=[])

    first = bot.handle_chat("power-followup", "6 кВт на 100 метров хватит?")
    second = bot.handle_chat("power-followup", "точно? а то ты раньше 12 советовал")

    assert "не хват" in first.answer.lower()
    assert all(marker in second.answer.lower() for marker in ["6", "100", "10", "12"])
    assert "теплопотер" in second.answer.lower()
    assert "недостаточно" in second.answer.lower()
