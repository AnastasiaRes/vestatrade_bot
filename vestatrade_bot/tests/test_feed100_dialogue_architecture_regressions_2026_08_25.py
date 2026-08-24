"""Regressions found by live dialogue checks over the 100-item feed families.

The assertions describe category and compatibility contracts, not exact user
phrases.  They deliberately use paraphrases of the live turns so a passing
implementation cannot be a response template fitted to one transcript.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.slot_filling import SlotFillingAgent
from app.config import get_settings
from app.models import Product, SearchQuery, SessionState
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


class _TemptingConsultLLM(_OfflineLLM):
    """Simulate an available LLM that would happily answer too early."""

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=(
                "Дополнительно проверьте тип резьбы, монтажное положение и "
                "температурный диапазон."
            ),
            llm_used=True,
        )


@pytest.fixture(autouse=True)
def _skip_document_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.orchestrator.load_docs_for_products",
        lambda _products, _directories: 0,
    )


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    attributes: dict[str, str] | None = None,
    description: str | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="TEST",
        url=f"https://example.test/{sku}",
        price=1000,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized=attributes or {},
        description=description,
    )


def test_sewer_specific_element_outranks_generic_pipe_word_and_keeps_branch_size() -> None:
    router = IntentRouterAgent(_OfflineLLM())
    session = SessionState(session_id="sewer-tee")

    intent = router.route(
        "Нужен серый тройник для канализации: основная труба 110 мм, "
        "боковой отвод 50 мм, "
        "почти под прямым углом, для квартиры.",
        session,
    )
    filled = SlotFillingAgent().fill(
        "Нужен серый тройник для канализации: основная труба 110 мм, "
        "боковой отвод 50 мм, "
        "почти под прямым углом, для квартиры.",
        intent,
        session,
    )

    assert intent.category == "sewer"
    assert filled.slots["element_type"] == "тройник"
    assert filled.slots["diameter_mm"] == 110
    assert filled.slots["secondary_diameter_mm"] == 50
    assert filled.slots["angle_deg"] == 90
    assert "length_mm" not in filled.expected_slots


def test_ppr_reducer_wording_matches_catalogue_transition_coupling() -> None:
    reducer = _product(
        "PPR-50-32",
        "Муфта переходная PPR 50-32",
        "Фитинги PPR",
        attributes={"тип товара": "Муфта переходная", "диаметр, мм": "50x32"},
    )
    straight = _product(
        "PPR-50",
        "Муфта PPR 50 мм",
        "Фитинги PPR",
        attributes={"тип товара": "Муфта", "диаметр, мм": "50"},
    )
    transition_tee = _product(
        "PPR-TEE-50-32",
        "Тройник переходной PPR 50-32-50",
        "Фитинги PPR",
        attributes={"тип товара": "Тройник переходной", "диаметр, мм": "50x32x50"},
    )
    search = FeedSearchAgent([straight, transition_tee, reducer])
    query = SearchQuery(
        original_text="переходник с 50 на 32 без резьбы",
        category="fittings",
        slots={
            "fitting_system": "ppr",
            "element_type": "переходник",
            "diameter_mm": 50,
            "secondary_diameter_mm": 32,
            "combined_metal": False,
        },
    )

    assert [product.sku for product in search.search(query)] == ["PPR-50-32"]


def test_broken_circulation_pump_is_replacement_and_radiators_are_context() -> None:
    pump = _product(
        "PUMP-130",
        "Насос циркуляционный 25/6-130",
        "Насосы циркуляционные",
        attributes={"монтажная длина, мм": "130", "максимальный напор, м": "6"},
    )
    radiator = _product(
        "RAD-500",
        "Радиатор биметаллический 500 мм",
        "Радиаторы отопления",
    )
    bot = ChatOrchestrator(products=[radiator, pump], llm_client=_OfflineLLM())
    session_id = "pump-replacement"

    bot.handle_chat(
        session_id,
        "Сломался циркуляционный насос отопления, на корпусе 25/6.",
    )
    response = bot.handle_chat(
        session_id,
        "Монтажная длина между плоскостями 130 мм, система только с радиаторами.",
    )

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"]["pump_selection_mode"] == "замена"
    assert response.debug["slots"]["mounting_length_mm"] == 130
    assert response.debug["slots"]["system_type"] == "радиаторы"
    assert [card.sku for card in response.products] == ["PUMP-130"]


def test_pipe_comparison_does_not_swallow_selection_with_unknown_ratings() -> None:
    pipe = _product(
        "PIPE-25",
        "Труба PP-FIBER PN20 25 мм",
        "Трубы полипропиленовые",
        description="Для систем горячего водоснабжения. Диаметр 25 мм.",
    )
    bot = ChatOrchestrator(products=[pipe], llm_client=_OfflineLLM())
    session_id = "pipe-known-conditions"

    bot.handle_chat(
        session_id,
        "Нужна полипропиленовая труба 25 мм на горячую воду в квартире.",
    )
    response = bot.handle_chat(
        session_id,
        "Температуры и давления не знаю. Покажи предварительно варианты PN20, "
        "можно армированную стекловолокном, но не придумывай эти режимы.",
    )

    assert [card.sku for card in response.products] == ["PIPE-25"]
    assert set(response.debug["slots"]["deferred_slot_keys"]) >= {
        "operating_temperature_c",
        "operating_pressure_bar",
    }
    assert "предваритель" in response.answer.lower()

    marking = bot.handle_chat(
        session_id,
        "Что означает PN20 и как отличить армирование стекловолокном от алюминия "
        "по внешнему виду или маркировке?",
    )
    assert "не взаимоисключающие" in marking.answer.lower()
    assert "одновременно" in marking.answer.lower()
    assert [card.sku for card in marking.products] == ["PIPE-25"]

    applicability = bot.handle_chat(
        session_id,
        "Где в карточке видно, подходит ли эта труба для обычной ванной и крана?",
    )
    assert "разводка гвс внутри квартиры" in applicability.answer.lower()
    assert "горяч" in applicability.answer.lower()
    assert [card.sku for card in applicability.products] == ["PIPE-25"]


def test_hot_water_supply_word_does_not_mean_country_house() -> None:
    router = IntentRouterAgent(_OfflineLLM())
    intent = router.route(
        "Это внутри квартиры, где идёт подача горячей воды от крана.",
        SessionState(session_id="not-a-dacha"),
    )

    assert intent.slots.get("application") != "дача"


def test_thermostatic_valve_goal_survives_question_about_a_head() -> None:
    valve = _product(
        "RV-ANGLE",
        'Клапан термостатический угловой 1/2"',
        "Арматура для радиаторов",
        attributes={"подключение": "угловое", "размер": "1/2"},
    )
    head = _product(
        "HEAD-M30",
        "Термоголовка M30x1,5",
        "Арматура для радиаторов",
    )
    bot = ChatOrchestrator(products=[head, valve], llm_client=_OfflineLLM())
    session_id = "radiator-valve-goal"

    bot.handle_chat(
        session_id,
        "Нужен термостатический клапан для регулировки радиатора.",
    )
    bot.handle_chat(
        session_id,
        'Подключение 1/2 дюйма, угловое. А что такое термоголовка и зачем она?',
    )
    response = bot.handle_chat(
        session_id,
        'Понял, тогда покажи угловой клапан 1/2.',
    )

    assert response.debug["slots"]["product_kind"] == "thermostatic_valve"
    assert [card.sku for card in response.products] == ["RV-ANGLE"]


def test_missing_head_can_be_described_as_lost_and_photo_policy_is_direct() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "lost-head",
        "С клапана на батарее пропала головка. Могу сфотографировать, "
        "но маркировку и резьбу пока не понимаю.",
    )

    assert response.debug["category"] == "radiator_fittings"
    assert response.debug["slots"]["product_kind"] == "thermostatic_head"
    assert "загрузка фотографий" in response.answer.lower()
    assert "не поддерживается" in response.answer.lower()
    assert "пришлите фото" not in response.answer.lower()


def test_photo_upload_boundary_is_global_not_product_specific() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "global-photo-boundary",
        "Можно загрузить сюда фото непонятной детали, чтобы вы определили товар?",
    )

    answer = response.answer.lower()
    assert "загрузка фотографий" in answer
    assert "не поддерживается" in answer
    assert "маркировку" in answer
    assert "словами" in answer


def test_conditional_handoff_keeps_catalogue_search_first() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    message = (
        'Попробуй найти угловой клапан 1/2" с термоголовкой, а если ничего '
        "не найдётся — передай менеджеру."
    )

    frame = bot.turn_planner.frame(message)

    assert frame.catalog_request_present is True
    assert frame.handoff_if_catalog_empty is True


def test_manager_request_is_not_negated_by_the_word_mne() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    message = (
        "Нет, мне не нужен пункт выдачи — мне нужен менеджер для помощи с "
        "подбором клапана."
    )

    assert bot._is_handoff_opt_out(message) is False
    assert bot._wants_manager_handoff(message) is True


def test_radiator_valve_geometry_does_not_turn_into_pipe_selection() -> None:
    valve = _product(
        "RV-ANGLE-LIVE",
        'Клапан термостатический угловой 1/2"',
        "Арматура для радиаторов",
        attributes={"подключение": "угловое", "размер": "1/2"},
    )
    bot = ChatOrchestrator(products=[valve], llm_client=_OfflineLLM())
    session_id = "radiator-valve-geometry"

    bot.handle_chat(
        session_id,
        "Хочу поставить регулировку на батарею, но не понимаю, прямой там "
        "нужен клапан или угловой и что ещё смотреть.",
    )
    response = bot.handle_chat(
        session_id,
        "Размер 1/2, труба выходит из стены и должна повернуть на 90 градусов — "
        "нужен угловой. А как понять, подойдёт ли клапан с термоголовкой?",
    )

    assert response.debug["category"] == "radiator_fittings"
    assert response.debug["slots"]["product_kind"] == "thermostatic_valve"
    assert response.debug["slots"]["body_form"] == "угловой"
    assert [card.sku for card in response.products] == ["RV-ANGLE-LIVE"]


def test_answer_to_pending_catalogue_question_outranks_free_form_consultant() -> None:
    valve = _product(
        "RV-ANGLE-PRIORITY",
        'Клапан термостатический угловой 1/2"',
        "Арматура для радиаторов",
        attributes={
            "подключение": "угловое",
            "размер": "1/2",
            "резьба гайки головки": "M30x1,5",
        },
    )
    settings = get_settings().model_copy(
        update={"llm_provider": "openrouter", "openrouter_api_key": "test-key"}
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=[valve],
        llm_client=_TemptingConsultLLM(),
    )
    session_id = "pending-selection-before-consult"

    bot.handle_chat(
        session_id,
        "Хочу поставить регулировку на батарею, но не понимаю, прямой там "
        "нужен клапан или угловой и что ещё смотреть.",
    )
    pending_session = bot.sessions.get(session_id).model_copy(deep=True)
    followup_message = (
        "Размер 1/2, труба выходит из стены и идёт к радиатору под углом, "
        "так что нужен угловой клапан. А что ещё важно учитывать?"
    )
    followup_intent = bot.intent_router.route(followup_message, pending_session)
    bot._stabilize_active_goal(followup_message, followup_intent, pending_session)
    assert followup_intent.raw["answered_active_catalogue_question"] is True
    # Simulate the hosted engineering layer closing the pending state before
    # the consultant-priority check.
    pending_session.clear_pending_question_state()
    assert not bot._should_consult(
        followup_message,
        followup_intent,
        pending_session,
    )

    response = bot.handle_chat(
        session_id,
        "Размер 1/2, труба выходит из стены и идёт к радиатору под углом "
        "90 градусов — нужен угловой клапан. А что ещё важно проверить?",
    )

    assert response.debug["category"] == "radiator_fittings"
    assert response.debug["slots"]["product_kind"] == "thermostatic_valve"
    assert response.debug["final_answer_source"] != "consultant_llm"
    assert [card.sku for card in response.products] == ["RV-ANGLE-PRIORITY"]

    compatibility = bot.handle_chat(
        session_id,
        "Как узнать, подойдёт ли клапан с термоголовкой к моему радиатору? "
        "Если на головке M30x1,5, должен ли таким же быть интерфейс клапана, "
        "или смотреть нужно на самом корпусе?",
    )

    assert [card.sku for card in compatibility.products] == ["RV-ANGLE-PRIORITY"]
    assert "сторону самой головки" in compatibility.answer.lower()
    assert "не доказывает резьбу" in compatibility.answer.lower()

    pair_choice = bot.handle_chat(
        session_id,
        "Можно просто посоветовать, какой из этих клапанов точно подойдёт с "
        "новой термоголовкой, если старый корпус я не могу снять и измерить?",
    )

    assert pair_choice.products == compatibility.products
    assert pair_choice.answer != compatibility.answer
    assert "измерять резьбу старого клапана не нужно" in pair_choice.answer.lower()
    assert "m30x1,5 прямо подтверждена" in pair_choice.answer.lower()
    assert "только соединение «головка—клапан»" in pair_choice.answer.lower()


def test_shown_thermostatic_valves_explain_eurocone_and_separate_head() -> None:
    ordinary = _product(
        "RV-NR",
        'Клапан термостатический угловой 1/2" с доп. уплотнением',
        "Арматура для радиаторов",
    )
    eurocone = _product(
        "RV-NER",
        'Клапан термостатический угловой 1/2" Евроконус',
        "Арматура для радиаторов",
    )
    bot = ChatOrchestrator(products=[ordinary, eurocone], llm_client=_OfflineLLM())
    session_id = "valve-difference"
    bot.handle_chat(
        session_id,
        'Покажи термостатические угловые клапаны для радиатора 1/2".',
    )
    response = bot.handle_chat(
        session_id,
        "В чём разница между моделями и нужно ли ещё что-то для термоголовки?",
    )

    assert "евроконус" in response.answer.lower()
    assert "отдельной деталью" in response.answer.lower()
    assert {card.sku for card in response.products} == {"RV-NR", "RV-NER"}

    eurocone = bot.handle_chat(
        session_id,
        "Что такое Евроконус и надо ли его учитывать, если труба вроде обычная 1/2?",
    )
    assert eurocone.products == response.products
    assert eurocone.debug["category"] == "radiator_fittings"
    assert "не просто размер резьбы 1/2" in eurocone.answer.lower()
    assert "ответная часть" in eurocone.answer.lower()
    assert "нельзя считать подходящим" in eurocone.answer.lower()


def test_sewer_tee_identity_survives_branch_description_and_angle_followup() -> None:
    tee_87 = _product(
        "TEE-110-50-87",
        "Тройник канализационный 110x50 87° серый",
        "Внутренняя канализация",
        attributes={"тип товара": "Тройник", "диаметр": "110x50", "угол": "87"},
    )
    elbow = _product(
        "ELBOW-110-87",
        "Отвод канализационный 110 87° серый",
        "Внутренняя канализация",
        attributes={"тип товара": "Отвод", "диаметр": "110", "угол": "87"},
    )
    bot = ChatOrchestrator(products=[elbow, tee_87], llm_client=_OfflineLLM())
    session_id = "sewer-branch-language"

    bot.handle_chat(
        session_id,
        "Нужен серый тройник: большая канализационная труба и отвод поменьше, "
        "почти под прямым углом.",
    )
    shown = bot.handle_chat(
        session_id,
        "Большая труба 110 мм, отвод 50 мм, угол почти прямой.",
    )

    assert shown.debug["slots"]["element_type"] == "тройник"
    assert [card.sku for card in shown.products] == ["TEE-110-50-87"]

    explanation = bot.handle_chat(
        session_id,
        "Что значит 87 градусов? Угол почти 90 — можно ли взять такой?",
    )
    assert "87–90" in explanation.answer
    assert "45°" in explanation.answer
    assert [card.sku for card in explanation.products] == ["TEE-110-50-87"]


def test_rejected_transition_tee_does_not_replace_active_reducer() -> None:
    reducer = _product(
        "REDUCER",
        "Муфта переходная PPR 50-32",
        "Фитинги PPR",
        attributes={"тип товара": "Муфта переходная", "диаметр": "50x32"},
    )
    tee = _product(
        "TEE",
        "Тройник переходной PPR 50-32-50",
        "Фитинги PPR",
        attributes={"тип товара": "Тройник переходной", "диаметр": "50x32x50"},
    )
    bot = ChatOrchestrator(products=[tee, reducer], llm_client=_OfflineLLM())
    session_id = "reject-tee"
    bot.handle_chat(
        session_id,
        "Нужен PPR переходник с 50 на 32, оба конца под пайку, без резьбы.",
    )
    response = bot.handle_chat(
        session_id,
        "Тройник — это не то, ветка не нужна. Покажи только обычный переход.",
    )

    assert response.debug["slots"]["element_type"] == "переходник"
    assert [card.sku for card in response.products] == ["REDUCER"]

    notation = bot.handle_chat(
        session_id,
        "Что значит вн-нар и почему одна серия PPR, а другая PPRC? Нужно ли это мне?",
    )
    assert "не означает металлическую резьбу" in notation.answer.lower()
    assert "50×32" in notation.answer
    assert [card.sku for card in notation.products] == ["REDUCER"]


def test_boiler_sizing_followup_uses_session_area_and_electric_checks() -> None:
    boiler = _product(
        "E9",
        "Котел электрический одноконтурный 9 кВт 380 В",
        "Котлы электрические",
        attributes={
            "мощность, квт": "9",
            "напряжение, в": "380",
            "количество контуров": "1",
        },
    )
    bot = ChatOrchestrator(products=[boiler], llm_client=_OfflineLLM())
    session_id = "boiler-area"

    shown = bot.handle_chat(
        session_id,
        "Нужен электрический котёл только для отопления дома 80 м², сеть 380 В.",
    )
    assert [card.sku for card in shown.products] == ["E9"]

    response = bot.handle_chat(session_id, "Хватит ли 9 кВт и как проверить теплопотери?")

    assert "80 м²" in response.answer
    assert "120 м²" not in response.answer
    assert "электр" in response.answer.lower()
    assert "теплопотер" in response.answer.lower()

    power_supply = bot.handle_chat(
        session_id,
        "Можно проверить, подойдёт ли 9 кВт по мощности и питанию и включить без риска?",
    )
    assert "80 м²" in power_supply.answer
    assert "электрик" in power_supply.answer.lower()
    assert [card.sku for card in power_supply.products] == ["E9"]


def test_unknown_phrase_with_no_data_is_recognized_generically() -> None:
    filler = SlotFillingAgent()

    assert filler._does_not_know_params("По давлению данных нет, паспорт утерян")
    assert filler._does_not_know_params("Рабочее значение неизвестно")


def test_central_radiator_unknown_pressure_can_show_preliminary_cards() -> None:
    radiator = _product(
        "RAD-6-500",
        "Радиатор биметаллический 6 секций 500 мм",
        "Радиаторы отопления",
        attributes={
            "количество секций": "6",
            "межосевое расстояние, мм": "500",
            "рабочее давление, бар": "20",
        },
    )
    bot = ChatOrchestrator(products=[radiator], llm_client=_OfflineLLM())
    session_id = "radiator-unknown-pressure"

    bot.handle_chat(
        session_id,
        "Меняю батарею: старая на шесть секций, межосевое 500 мм, "
        "больше никаких данных о ней нет.",
    )
    response = bot.handle_chat(
        session_id,
        "Отопление центральное. По рабочему и опрессовочному давлению данных "
        "нет — покажи предварительные варианты по тому, что уже известно.",
    )

    assert [card.sku for card in response.products] == ["RAD-6-500"]
    assert "operating_pressure_bar" in response.debug["slots"][
        "deferred_slot_keys"
    ]
    assert "предваритель" in response.answer.lower()
    assert "не обещ" in response.answer.lower() or "не подтверж" in response.answer.lower()

    plain = bot.handle_chat(
        session_id,
        "Почему площадь обогрева около 10 м² не означает, что он перегреет "
        "комнату 10 м²? Объясни по-простому.",
    )
    assert "не означает" in plain.answer.lower()
    assert "предваритель" in plain.answer.lower()
    assert [card.sku for card in plain.products] == ["RAD-6-500"]


def test_thermostatic_thread_pitch_question_gets_a_specific_explanation() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    session_id = "head-pitch"
    bot.handle_chat(
        session_id,
        "С клапана потерялась термоголовка, резьбу и модель клапана не знаю.",
    )

    response = bot.handle_chat(
        session_id,
        "Что значит шаг резьбы x1.5 и можно ли понять его по диаметру?",
    )

    answer = response.answer.lower()
    assert "расстояние 1,5 мм" in answer
    assert "диаметр" in answer
    assert "подтверждает только m30" in answer or "только номинальный диаметр" in answer

    critical = bot.handle_chat(
        session_id,
        "Диаметр получится 30 мм, но шаг x1,5 проверить не смогу. Он не так "
        "критичен или без него нельзя подобрать?",
    )
    assert "расстояние 1,5 мм" in critical.answer.lower()
    assert "без проверки" in critical.answer.lower() or "совместимость" in critical.answer.lower()


def test_repeated_pump_curve_request_reports_unchanged_status_without_d11() -> None:
    pump = _product(
        "68/2/8",
        "Дренажный насос Вихрь ДН-350",
        "Насосы дренажные",
        attributes={
            "тип товара": "Дренажный насос",
            "высота напора, м": "8",
            "макс. производительность, л/ч": "8000",
        },
        description="Для грязной воды с твёрдыми частицами до 35 мм.",
    )
    bot = ChatOrchestrator(products=[pump], llm_client=_OfflineLLM())
    session_id = "pump-doc-repeat"
    for turn in (
        "В подвале грязноватая вода, нужен насос для откачки из приямка.",
        "Вертикальный подъём 4 м, по горизонтали шланг 10 м.",
        "Воды около одного кубометра, убрать желательно за час.",
    ):
        bot.handle_chat(session_id, turn)
    shown = bot.handle_chat(session_id, "Покажи подходящий вариант из каталога.")
    assert [card.sku for card in shown.products] == ["68/2/8"]

    question = "Где в карточке найти Q-H-кривую и подачу при рабочем напоре?"
    first = bot.handle_chat(session_id, question)
    second = bot.handle_chat(session_id, question)

    assert first.answer != second.answer
    assert "точной позиции" not in first.answer.lower()
    assert "точной позиции" not in second.answer.lower()
    assert "повторно проверил" in second.answer.lower()
    assert [card.sku for card in second.products] == ["68/2/8"]


def test_drainage_model_comparison_shows_named_candidates_after_duty_is_known() -> None:
    dn350 = _product(
        "68/2/8",
        "Дренажный насос Вихрь ДН-350",
        "Насосы дренажные",
        attributes={
            "высота напора, м": "5",
            "макс. производительность, л/ч": "8000",
        },
        description="Для грязной воды с твёрдыми частицами до 35 мм.",
    )
    dn750 = _product(
        "68/2/2",
        "Дренажный насос Вихрь ДН-750",
        "Насосы дренажные",
        attributes={
            "высота напора, м": "8",
            "макс. производительность, л/ч": "15300",
        },
        description="Для грязной воды с твёрдыми частицами до 35 мм.",
    )
    bot = ChatOrchestrator(products=[dn350, dn750], llm_client=_OfflineLLM())
    session_id = "drainage-live-comparison"
    bot.handle_chat(
        session_id,
        "Нужен насос для грязноватой воды из приямка, расчётов у меня нет.",
    )
    bot.handle_chat(session_id, "Подъём 4 м, горизонтальный шланг 10 м.")
    response = bot.handle_chat(
        session_id,
        "Приямок 2 на 1 метр и глубиной 0,5 м, убрать за 30 минут. "
        "Сравни ДН-350 и ДН-750: какой из них лучше подойдёт?",
    )

    assert {card.sku for card in response.products} == {"68/2/8", "68/2/2"}
    assert "2 м³/ч" in response.answer
    assert "предварительно" in response.answer.lower()
    assert "q–h" in response.answer.lower()
    assert "68/2/2" in response.answer

    order = bot.handle_chat(
        session_id,
        "По Q-H-кривой выбирать сразу или надо сначала купить шланг?",
    )
    assert "не покупают случайный шланг" in order.answer.lower()
    assert order.answer != response.answer


def test_boiler_estimate_paraphrases_remain_in_area_sizing_flow() -> None:
    boiler = _product(
        "E9-LIVE",
        "Котел электрический одноконтурный 9 кВт 380 В",
        "Котлы электрические",
        attributes={"мощность, квт": "9", "напряжение, в": "380"},
    )
    bot = ChatOrchestrator(products=[boiler], llm_client=_OfflineLLM())
    session_id = "boiler-estimate-paraphrases"
    bot.handle_chat(
        session_id,
        "Электрический котёл для отопления дома 80 м², есть 380 В.",
    )

    estimate = bot.handle_chat(
        session_id,
        "Как хотя бы примерно оценить мощность, чтобы не взять слишком мощный "
        "или слабоватый?",
    )
    specialist = bot.handle_chat(
        session_id,
        "А куда обращаться за точным расчётом теплопотерь?",
    )

    assert "80 м²" in estimate.answer
    assert "примерн" in estimate.answer.lower()
    assert [card.sku for card in estimate.products] == ["E9-LIVE"]
    assert "инженера-теплотехника" in specialist.answer.lower()
    assert [card.sku for card in specialist.products] == ["E9-LIVE"]


def test_boiler_power_list_is_compared_as_shown_set_not_last_number_filter() -> None:
    boilers = [
        _product(
            f"E{power:g}",
            f"Котел электрический {power:g} кВт 380 В",
            "Котлы электрические",
            attributes={"мощность, квт": f"{power:g}", "напряжение, в": "380"},
        )
        for power in (7.5, 9.0, 12.0)
    ]
    bot = ChatOrchestrator(products=boilers, llm_client=_OfflineLLM())
    session_id = "boiler-shown-power-set"
    shown = bot.handle_chat(
        session_id,
        "Электрический котёл для дома 80 м², доступно 380 В.",
    )
    assert len(shown.products) == 3

    comparison = bot.handle_chat(
        session_id,
        "Какой из этих трёх — 7,5, 9 или 12 кВт — будет работать стабильнее "
        "и безопаснее без лишнего риска?",
    )
    assert "предварительный кандидат — 9 квт" in comparison.answer.lower()
    assert {card.sku for card in comparison.products} == {"E7.5", "E9", "E12"}

    question = (
        "Как проверить, подойдёт ли 9 кВт без лишних рисков и не будет перегреваться?"
    )
    first = bot.handle_chat(session_id, question)
    second = bot.handle_chat(session_id, question)
    third = bot.handle_chat(session_id, question)
    assert len({first.answer, second.answer, third.answer}) == 3
