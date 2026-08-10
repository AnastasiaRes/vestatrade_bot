"""Natural consultant regressions for pipes, pumps and protected terminology."""

from __future__ import annotations

from typing import Any

from app.agents.feed_search import FeedSearchAgent, _builtin_part_state
from app.agents.engineering_calculations import normalize_engineering_slots
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.response_composer import ResponseComposerAgent
from app.models import Product, ProductCard, ProductDocument, SearchQuery, SessionState
from app.openrouter_client import LLMResult


class _OfflineLLM:
    last_json_output_accepted = False
    last_fallback_reason = "offline regression"

    def complete_json(
        self,
        _agent: str,
        _messages: list[dict[str, str]],
        fallback: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        return fallback, False

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=None,
            llm_used=False,
            fallback_reason="offline regression",
        )


class _TermPoisonLLM(_OfflineLLM):
    """Make an accidental glossary route visible instead of silently falling back."""

    def complete(self, *args: Any, **kwargs: Any) -> LLMResult:
        if str(kwargs.get("agent") or "").endswith("term_consult"):
            return LLMResult(content="Это точно поддон с решёткой.", llm_used=True)
        return super().complete(*args, **kwargs)


def _product(
    sku: str,
    name: str,
    category: str,
    attributes: dict[str, str],
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=100,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized=attributes,
    )


def test_grey_plastic_under_sink_is_understood_as_sewer_pipe() -> None:
    result = IntentRouterAgent(llm_client=_OfflineLLM()).route(
        "Под раковиной серая пластиковая штука 50 мм, нужна длиной полметра"
    )

    assert result.category == "sewer"
    assert result.slots["element_type"] == "труба"
    assert result.slots["sewer_scope"] == "внутренняя"
    assert result.slots["diameter_mm"] == 50
    assert result.slots["length_mm"] == 500


def test_unnamed_sewer_part_continues_typed_selection_not_free_term_answer() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_TermPoisonLLM())

    response = bot.handle_chat(
        "unnamed-sewer-live-regression",
        (
            "Под раковиной треснула серая пластиковая штука примерно 50 мм "
            "толщиной и полметра длиной. Не знаю, как называется. Что искать?"
        ),
    )

    assert response.debug["category"] == "sewer"
    assert response.debug["slots"]["element_type"] == "труба"
    assert response.debug["slots"]["diameter_mm"] == 50
    assert "поддон" not in response.answer.lower()


def test_dirty_water_description_enters_drainage_funnel_without_reasking_quality() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "dirty-water-description",
        "Нужен насос для грязной воды с песком и мусором",
    )

    assert response.debug["slots"]["pump_type"] == "дренажный"
    assert response.debug["slots"]["water_quality"] == "грязная"
    assert response.products == []
    assert "чистая, грязная или фекальная" not in response.answer.lower()
    assert "напор" in response.answer.lower()


def test_unnamed_dirty_water_pump_asks_hydraulics_not_motor_power() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_TermPoisonLLM())

    response = bot.handle_chat(
        "unnamed-dirty-pump-live-regression",
        (
            "В подвале вода с песком и мелким мусором. Нужно откачать, "
            "не знаю, как называется такой насос."
        ),
    )

    answer = response.answer.lower()
    assert response.debug["slots"]["pump_type"] == "дренажный"
    assert "размер частиц" in answer
    assert "вертикальный подъём" in answer
    assert "мощност" not in answer
    assert "поддон" not in answer


def test_colloquial_well_water_level_is_preserved_for_confirmation() -> None:
    result = IntentRouterAgent(llm_client=_OfflineLLM()).route(
        "Колодец 5 колец, вода начинается на третьем, до дома 40 м"
    )

    assert result.category == "pumps"
    assert result.slots["well_ring_count"] == 5
    assert result.slots["water_level_ring_count"] == 3
    assert result.slots["water_level_reference"] == "ambiguous"
    assert result.slots["horizontal_run_m"] == 40


def test_approximate_ordinal_well_level_is_not_discarded() -> None:
    result = IntentRouterAgent(llm_client=_OfflineLLM()).route(
        "Колодец пять колец, вода примерно с третьего. До дома 40 метров"
    )

    assert result.slots["well_ring_count"] == 5
    assert result.slots["water_level_ring_count"] == 3
    assert result.slots["water_level_reference"] == "ambiguous"


def test_weak_central_water_is_diagnosed_before_booster_selection() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "weak-central-water",
        "Вода из центрального водопровода еле течёт, что поставить?",
    )

    answer = response.answer.lower()
    assert response.need_handoff is False
    assert response.products == []
    assert response.debug["slots"]["pump_type"] == "повысительный"
    assert "динамическ" in answer and "давлен" in answer
    assert "аэратор" in answer or "фильтр" in answer
    assert "минимум 3" not in answer


def test_novice_ppr_dialog_explains_what_to_collect_for_diameter() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    first = bot.handle_chat(
        "novice-ppr-measurements",
        (
            "Делаю воду в квартире. Нужны белые пластиковые палки, которые "
            "паяют утюгом. Будет и горячая, и холодная вода, названия не знаю."
        ),
    )
    first_answer = first.answer.lower()
    assert "ppr" in first_answer and "полипропилен" in first_answer
    assert "для начала" in first_answer
    assert "рабочее давление" not in first_answer
    response = bot.handle_chat(
        "novice-ppr-measurements",
        "От стояка к кранам, спрячем в стену. Диаметр не знаю — что измерить?",
    )

    answer = response.answer.lower()
    assert response.debug["slots"]["pipe_material"] == "ppr"
    assert response.debug["slots"]["pipe_service"] == "разводка внутри дома"
    assert "точки водоразбора" in answer
    assert "одновременно" in answer and "длину" in answer
    assert "управляющ" in answer


def test_hdpe_companion_hint_keeps_pipe_diameter_and_does_not_invent_half_inch() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    session = bot.sessions.get("hdpe-companion")
    session.slots.update({"pipe_material": "пэ100", "diameter_mm": 32})

    answer = bot._append_companion_hint("Основной ответ.", session, "pipes")

    assert "32 мм" in answer
    assert "компрессион" in answer
    assert "1/2" not in answer


def test_toilet_shutoff_description_is_understood_as_cold_water_valve() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "toilet-shutoff-novice",
        "Нужна штука с ручкой, чтобы перекрыть воду перед унитазом. Резьба полдюйма.",
    )

    slots = response.debug["slots"]
    assert slots["application"] == "вода"
    assert slots["water_temperature"] == "холодная"
    assert slots["valve_kind"] == "угловой кран"
    assert slots["size_inch"] == "1/2"
    assert "для чего нужен кран" not in response.answer.lower()
    assert response.products == []
    assert "угловой запорный кран" in response.answer.lower()
    assert "резьба выходит из стены" in response.answer.lower()


def test_booster_target_pressure_and_flow_are_not_asked_twice() -> None:
    result = IntentRouterAgent(llm_client=_OfflineLLM()).route(
        "В центральном водопроводе сейчас 1,2 бар, хочу 3 бара при 30 л/мин"
    )

    assert result.category == "pumps"
    assert result.slots["inlet_pressure_bar"] == 1.2
    assert result.slots["required_pressure_bar"] == 3.0
    assert result.slots["required_flow_m3_h"] == 1.8


def test_building_height_is_not_circulation_pump_head() -> None:
    result = IntentRouterAgent(llm_client=_OfflineLLM()).route(
        "Дом два этажа, высота 7 м, подберите циркуляционный насос для закрытой системы"
    )

    assert result.category == "pumps"
    assert result.slots["pump_type"] == "циркуляционный"
    assert "head_m" not in result.slots
    assert "required_head_m" not in result.slots


def test_borehole_head_is_calculated_from_geometry_flow_pressure_and_pipe() -> None:
    slots = normalize_engineering_slots(
        {
            "pump_type": "скважинный",
            "water_source": "скважина",
            "dynamic_water_level_m": 18,
            "lift_height_m": 3,
            "horizontal_run_m": 35,
            "required_pressure_bar": 3,
            "required_flow_m3_h": 2,
            "discharge_diameter_mm": 32,
            "discharge_sdr": 11,
        }
    )

    assert slots["discharge_internal_diameter_mm"] == 26.18
    assert slots["hydraulic_loss_m"] > 0
    assert 50 < slots["required_head_m"] < 70
    assert slots["required_head_calculated"] is True
    assert "Darcy" in slots["head_calculation_method"]


def test_borehole_dialog_asks_for_pipe_data_not_a_ready_made_head() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "borehole-calculation-input",
        (
            "Скважина 55 м, динамический уровень 18 м, подъём 3 м, "
            "трасса 35 м, 3 бар, 2 м³/ч"
        ),
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "диаметр" in answer and "sdr" in answer
    assert "расчётный напор" not in answer or "укажите расчётный напор" not in answer


def test_drainage_route_requires_hose_diameter_before_head_calculation() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "drainage-hydraulics",
        "Грязная вода из подвала, подъём 4 м, шланг 25 м, 10 м³/ч, частицы 10 мм",
    )

    assert response.debug["slots"]["pump_type"] == "дренажный"
    assert response.debug["slots"]["water_quality"] == "грязная"
    assert "диаметр" in response.answer.lower()
    assert "чистая, грязная или фекальная" not in response.answer.lower()


def test_kns_does_not_show_catalog_until_duty_and_fixtures_are_known() -> None:
    kns = _product(
        "KNS-1",
        "Канализационная насосная установка",
        "Канализационные насосные установки",
        {"тип товара": "Канализационная насосная установка"},
    )
    bot = ChatOrchestrator(products=[kns], llm_client=_OfflineLLM())

    response = bot.handle_chat("kns-sizing", "КНС для санузла в подвале")

    assert response.products == []
    answer = response.answer.lower()
    assert "прибор" in answer
    assert "вертикальн" in answer
    assert "рабоч" in answer


def test_pump_marking_is_protected_from_llm_reinterpretation() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "pump-marking-term",
        "Что значит 25/6-180 на циркуляционном насосе?",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "6 м" in answer and "180 мм" in answer
    assert "не расход" in answer
    assert "рабочую точку" in answer


def test_branded_pump_marking_is_protected_even_when_pump_noun_is_omitted() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_TermPoisonLLM())

    response = bot.handle_chat(
        "pump-marking-brand-live-regression",
        (
            "Сгорел Wilo Star-RS 25/6-180. Хочу нормальную замену, "
            "объясните ещё, что означают цифры."
        ),
    )

    answer = response.answer.lower()
    assert "dn 25" in answer
    assert "6 м" in answer and "не расход" in answer
    assert "180 мм" in answer and "монтажная длина" in answer
    assert "25 — номинальный напор" not in answer


def test_far_hot_water_description_is_named_gvs_recirculation() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "gvs-recirculation-term",
        "Хочу, чтобы горячая вода из дальнего крана шла сразу. Как это называется?",
    )

    answer = response.answer.lower()
    assert "рециркуляц" in answer and "гвс" in answer
    assert "обратн" in answer


def test_plain_pex_request_rejects_metal_polymer_hybrid() -> None:
    pure = _product(
        "PEX-16",
        "Труба PE-Xa 16x2 EVOH",
        "Трубы",
        {"материал": "PE-Xa", "диаметр": "16 мм"},
    )
    hybrid = _product(
        "PEXAL-16",
        "Труба PE-Xa/Al/PE-RT 16x2",
        "Трубы",
        {"материал": "PE-Xa/Al/PE-RT", "диаметр": "16 мм"},
    )
    search = FeedSearchAgent([pure, hybrid])

    assert search._pipe_material_matches(pure, "pex") is True
    assert search._pipe_material_matches(hybrid, "pex") is False
    assert search._pipe_material_matches(hybrid, "pe-rt") is False


def test_pipe_alternatives_cannot_change_explicit_diameter_or_item_length() -> None:
    exact = _product(
        "PIPE-20-2000",
        "Труба PPR 20x2000",
        "Трубы",
        {"материал": "PPR", "диаметр": "20 мм", "длина": "2000 мм"},
    )
    wrong = _product(
        "PIPE-25-4000",
        "Труба PPR 25x4000",
        "Трубы",
        {"материал": "PPR", "диаметр": "25 мм", "длина": "4000 мм"},
    )
    search = FeedSearchAgent([exact, wrong])
    query = SearchQuery(
        original_text="PPR 20 мм, отрезок 2 м",
        category="pipes",
        slots={"pipe_material": "ppr", "diameter_mm": 20, "length_mm": 2000},
    )

    assert search._alternative_hard_slots_match(exact, query, query.slots) is True
    assert search._alternative_hard_slots_match(wrong, query, query.slots) is False


def test_pump_working_point_is_only_a_curve_check_candidate() -> None:
    card = ProductCard(
        sku="PUMP-QH",
        name="Насос тестовый",
        brand="TEST",
        price=100,
        stock_status="в наличии",
        stock_qty=1,
        url="https://example.test/pump-qh",
    )
    query = SearchQuery(
        original_text="Нужно 2 м³/ч при 35 м",
        category="pumps",
        slots={"required_flow_m3_h": 2.0, "required_head_m": 35.0},
    )

    answer = ResponseComposerAgent(llm_client=_OfflineLLM()).compose_choose_one(
        card,
        query,
    )

    assert "не могу корректно рекомендовать" in answer.lower()
    assert "q–h" in answer.lower()
    assert "ближайший кандидат" in answer.lower()


def test_package_answer_uses_customer_friendly_passport_attribution() -> None:
    product = _product(
        "DOC-PUMP",
        "Насос тестовый",
        "Насосы",
        {"тип товара": "Насос"},
    )
    text = (
        "Паспорт изделия. Комплект поставки. "
        "В комплект поставки входят: 1. Насос. 2. Прокладки."
    )
    product.docs_text = text
    product.documents = [
        ProductDocument(
            filename="DOC-PUMP-passport.pdf",
            document_kind="passport",
            text=text,
            page_count=8,
            section_pages={"комплект поставки": 6},
        )
    ]
    bot = ChatOrchestrator(products=[product], llm_client=_OfflineLLM())
    card = ProductCard(
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        price=product.price or 0,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
    )

    answer = bot._compose_passport_package_answer(card)
    llm_context = bot._build_context_block(SessionState(session_id="passport"), [card])

    assert "Согласно паспорту изделия" in answer
    assert "DOC-PUMP-passport.pdf" not in answer
    assert "стр. 6 PDF" not in answer
    assert "Прокладки" in answer
    assert "Привязанный паспорт изделия" in llm_context
    assert "DOC-PUMP-passport.pdf" not in llm_context


def test_dash_package_list_stops_before_numbered_construction_section() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    text = (
        "3. КОМПЛЕКТАЦИЯ. В стандартный комплект поставки насоса входят: "
        "Насос – 1 шт. Штуцер – 1 шт. Паспорт – 1 шт. Упаковка – 1 шт. "
        "Не допускается работа без воды. 4. ОБЩИЙ ВИД УСТРОЙСТВА. "
        "1. Насос. 2. Поплавковый выключатель. 5. ТЕХНИЧЕСКИЕ ХАРАКТЕРИСТИКИ."
    )

    assert bot._passport_package_items(text) == [
        "Насос — 1 шт",
        "Штуцер — 1 шт",
        "Паспорт — 1 шт",
        "Упаковка — 1 шт",
    ]


def test_package_absence_does_not_negate_a_card_confirmed_builtin() -> None:
    product = _product(
        "BUILTIN-PUMP",
        "Котёл со встроенным циркуляционным насосом",
        "Котлы",
        {"тип товара": "Котёл"},
    )
    product.documents = [
        ProductDocument(
            filename="package.pdf",
            document_kind="passport",
            text="В комплект поставки циркуляционный насос не входит.",
        )
    ]

    assert _builtin_part_state(product, "насос") is True


def test_real_builtin_source_conflict_fails_closed() -> None:
    product = _product(
        "CONFLICT-PUMP",
        "Котёл со встроенным циркуляционным насосом",
        "Котлы",
        {"тип товара": "Котёл"},
    )
    product.documents = [
        ProductDocument(
            filename="conflict.pdf",
            document_kind="passport",
            text="Циркуляционный насос в изделие не встроен.",
        )
    ]

    assert _builtin_part_state(product, "насос") is None
