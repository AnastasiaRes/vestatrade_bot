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
from app.models import IntentResult, Product, SearchQuery, SessionState
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


@pytest.mark.parametrize(
    ("category", "message", "slots"),
    [
        (
            "fittings",
            "Нужен PPR-переходник с трубы 50 мм, второй размер пока не назвал.",
            {
                "fitting_system": "ppr",
                "element_type": "переходник",
                "diameter_mm": 50,
            },
        ),
        (
            "sewer",
            "Нужен тройник 110 мм для серой канализации в квартире.",
            {
                "sewer_scope": "внутренняя",
                "element_type": "тройник",
                "diameter_mm": 110,
            },
        ),
    ],
)
def test_two_size_parts_require_the_second_size_by_product_contract(
    category: str,
    message: str,
    slots: dict[str, Any],
) -> None:
    filled = SlotFillingAgent().fill(
        message,
        IntentRouterAgent(_OfflineLLM()).route(message),
        SessionState(session_id=f"second-size-{category}", slots=slots),
    )

    assert filled.needs_clarification
    assert filled.blocking
    assert "secondary_diameter_mm" in filled.expected_slots
    assert "диаметр" in (filled.question or "").lower()
    assert "length_mm" not in filled.expected_slots


def test_equal_tee_size_is_copied_only_after_explicit_equal_observation() -> None:
    session = SessionState(
        session_id="equal-tee",
        category="sewer",
        slots={
            "sewer_scope": "внутренняя",
            "element_type": "тройник",
            "diameter_mm": 110,
        },
    )
    message = "Ответвление такого же диаметра, это равнопроходной тройник."
    intent = IntentResult(
        intent_type="attribute_request",
        category="sewer",
        confidence=1.0,
        slots={"element_type": "тройник"},
    )

    filled = SlotFillingAgent().fill(message, intent, session)

    assert filled.slots["diameter_mm"] == 110
    assert filled.slots["secondary_diameter_mm"] == 110
    assert not filled.needs_clarification


def test_fitting_size_pair_accepts_from_to_wording_and_direct_second_size_answer() -> None:
    router = IntentRouterAgent(_OfflineLLM())
    message = "Нужен PPR-переходник от 50 мм до 32 мм, оба конца под пайку."
    routed = router.route(message)

    assert routed.category == "fittings"
    assert routed.slots["diameter_mm"] == 50
    assert routed.slots["secondary_diameter_mm"] == 32

    session = SessionState(
        session_id="fitting-direct-second-size",
        category="fittings",
        slots={
            "fitting_system": "ppr",
            "element_type": "переходник",
            "diameter_mm": 50,
        },
    )
    filled = SlotFillingAgent().fill(
        "Второй диаметр — 32 мм.",
        IntentResult(
            intent_type="attribute_request",
            category="fittings",
            confidence=1.0,
            slots={"element_type": "переходник"},
        ),
        session,
    )

    assert filled.slots["diameter_mm"] == 50
    assert filled.slots["secondary_diameter_mm"] == 32
    assert not filled.needs_clarification


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


def test_circulation_pump_unknowns_become_deferred_preliminary_constraints() -> None:
    message = (
        "Монтажная длина 130 мм, присоединение DN25, система с радиаторами. "
        "Напор и расход не знаю — подбери по тому, что известно."
    )
    intent = IntentResult(
        intent_type="attribute_request",
        category="pumps",
        confidence=1.0,
        slots={
            "pump_type": "циркуляционный",
            "mounting_length_mm": 130,
            "connection_size": 25,
            "system_type": "радиаторы",
        },
    )

    filled = SlotFillingAgent().fill(
        message,
        intent,
        SessionState(session_id="pump-deferred-preliminary"),
    )

    assert not filled.needs_clarification
    assert filled.slots["preliminary_selection"] is True
    assert set(filled.slots["deferred_slot_keys"]) >= {
        "head_m",
        "required_flow_m3_h",
    }
    assert filled.slots.get("head_m") is None


def test_pump_browse_by_known_facts_keeps_constraints_and_warns_non_final() -> None:
    pump_130 = _product(
        "PUMP-25-6-130",
        "Насос циркуляционный 25/6-130",
        "Насосы циркуляционные",
        attributes={
            "монтажная длина, мм": "130",
            "максимальный напор, м": "6",
            "присоединительный размер": "25",
        },
    )
    pump_180 = _product(
        "PUMP-25-6-180",
        "Насос циркуляционный 25/6-180",
        "Насосы циркуляционные",
        attributes={
            "монтажная длина, мм": "180",
            "максимальный напор, м": "6",
            "присоединительный размер": "25",
        },
    )
    bot = ChatOrchestrator(
        products=[pump_180, pump_130],
        llm_client=_OfflineLLM(),
    )
    session_id = "pump-known-facts-browse"

    bot.handle_chat(
        session_id,
        "Нужен новый циркуляционный насос для системы отопления.",
    )
    response = bot.handle_chat(
        session_id,
        "Монтажная длина 130 мм, DN25, только радиаторы. Напор и расход "
        "не знаю — покажи варианты по тому, что уже известно.",
    )

    assert [card.sku for card in response.products] == ["PUMP-25-6-130"]
    assert response.debug["slots"]["mounting_length_mm"] == 130
    assert response.debug["slots"]["system_type"] == "радиаторы"
    assert set(response.debug["slots"]["deferred_slot_keys"]) >= {
        "head_m",
        "required_flow_m3_h",
    }
    assert "не обещание совместимости" in response.answer.lower()


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

    interface = bot.handle_chat(
        session_id,
        "Что такое штатные гайки и резьба трубопровода со стороны системы? "
        "Это смотреть в паспорте старого насоса или измерять руками?",
    )
    assert interface.debug["category"] == "pumps"
    assert [card.sku for card in interface.products] == ["PUMP-130"]
    assert "накидные гайки" in interface.answer.lower()
    assert "сброса давления" in interface.answer.lower()


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

    hidden_comparison = bot.handle_chat(
        session_id,
        "Объясни разницу между этими трубами: почему одна дороже и можно ли "
        "выбрать просто PN20 для скрытой прокладки в стене, если соединяют нагревом?",
    )
    hidden_repeat = bot.handle_chat(
        session_id,
        "Объясни разницу между этими трубами: почему одна дороже и можно ли "
        "выбрать просто PN20 для скрытой прокладки в стене, если соединяют нагревом?",
    )
    assert [card.sku for card in hidden_comparison.products] == ["PIPE-25"]
    assert [card.sku for card in hidden_repeat.products] == ["PIPE-25"]
    assert "не контур тёплого пола" in hidden_comparison.answer.lower()
    assert "практическая проверка" in hidden_repeat.answer.lower()
    assert hidden_comparison.answer != hidden_repeat.answer

    pn_pair = bot.handle_chat(
        session_id,
        "Что значит PN20 и PN25 и почему разница давления влияет на выбор, "
        "если давление дома неизвестно?",
    )
    pn_pair_repeat = bot.handle_chat(
        session_id,
        "Что значит PN20 и PN25 и почему разница давления влияет на выбор, "
        "если давление дома неизвестно?",
    )
    assert "pn25 всегда лучше" in pn_pair.answer.lower()
    assert "запросите у ук/тсж" in pn_pair_repeat.answer.lower()
    assert pn_pair.answer != pn_pair_repeat.answer
    assert [card.sku for card in pn_pair_repeat.products] == ["PIPE-25"]

    marking = bot.handle_chat(
        session_id,
        "Что означает PN20 и как отличить армирование стекловолокном от алюминия "
        "по внешнему виду или маркировке?",
    )
    assert "не взаимоисключающие" in marking.answer.lower()
    assert "одновременно" in marking.answer.lower()
    assert [card.sku for card in marking.products] == ["PIPE-25"]

    choice = bot.handle_chat(
        session_id,
        "Почему труба с алюминием может быть лучше? Нужно ли брать армированную, "
        "или обычная PN20 подойдёт?",
    )
    choice_checklist = bot.handle_chat(
        session_id,
        "Почему труба с алюминием может быть лучше? Нужно ли брать армированную, "
        "или обычная PN20 подойдёт?",
    )
    assert "не три последовательных класса" in choice.answer.lower()
    assert "четыре вещи" in choice_checklist.answer.lower()
    assert choice.answer != choice_checklist.answer
    assert [card.sku for card in choice_checklist.products] == ["PIPE-25"]

    ordinary_risk = bot.handle_chat(
        session_id,
        "Если просто взять обычную PN20, она не деформируется или не разрушится "
        "при нагреве?",
    )
    ordinary_risk_repeat = bot.handle_chat(
        session_id,
        "Если просто взять обычную PN20, она не деформируется или не разрушится "
        "при нагреве?",
    )
    assert "армированной pp-fiber" in ordinary_risk.answer.lower()
    assert "нельзя переносить" in ordinary_risk.answer.lower()
    assert "практический вывод" in ordinary_risk_repeat.answer.lower()
    assert ordinary_risk.answer != ordinary_risk_repeat.answer
    assert [card.sku for card in ordinary_risk_repeat.products] == ["PIPE-25"]

    buy_then_check = bot.handle_chat(
        session_id,
        "А если просто взять этот вариант, а давление и температуру проверить у "
        "мастера уже после покупки?",
    )
    assert "после покупки не стоит" in buy_then_check.answer.lower()
    assert "до покупки" in buy_then_check.answer.lower()
    assert "предварительного кандидата" in buy_then_check.answer.lower()
    assert [card.sku for card in buy_then_check.products] == ["PIPE-25"]

    applicability = bot.handle_chat(
        session_id,
        "Где в карточке видно, подходит ли эта труба для обычной ванной и крана?",
    )
    assert "разводка гвс внутри квартиры" in applicability.answer.lower()
    assert "горяч" in applicability.answer.lower()
    assert [card.sku for card in applicability.products] == ["PIPE-25"]

    candidate = bot.handle_chat(
        session_id,
        "Тогда я возьму эту PN20 со стекловолокном — она точно подойдёт "
        "для моей квартиры?",
    )
    assert "точно подойдёт" in candidate.answer.lower()
    assert "нельзя" in candidate.answer.lower()
    assert "предваритель" in candidate.answer.lower()

    verification = bot.handle_chat(
        session_id,
        "А как мне проверить реальные давление и температуру ГВС? Где их можно "
        "измерить или узнать?",
    )
    assert verification.answer != candidate.answer
    assert "ук/тсж" in verification.answer.lower()
    assert "pn20 нельзя читать" in verification.answer.lower()
    assert [card.sku for card in verification.products] == ["PIPE-25"]

    request_template = bot.handle_chat(
        session_id,
        "Где найти данные о максимальных температуре и давлении дома — в паспорте "
        "дома или у управляющей компании?",
    )
    assert "в письменном запросе" in request_template.answer.lower()
    assert "паспорт старой трубы" in request_template.answer.lower()
    assert request_template.answer != verification.answer
    assert [card.sku for card in request_template.products] == ["PIPE-25"]


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

    repeated = bot.handle_chat(
        "lost-head",
        "С клапана на батарее пропала головка. Могу сфотографировать, "
        "но маркировку и резьбу пока не понимаю.",
    )
    assert repeated.answer != response.answer
    assert "без проверки нельзя" in repeated.answer.lower()
    assert "не поддерживается" in repeated.answer.lower()


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

    counterpart = bot.handle_chat(
        session_id,
        "Что значит ответное соединение трубы и как его проверить, если труба "
        "просто выходит из стены?",
    )
    assert counterpart.debug["category"] == "radiator_fittings"
    assert counterpart.products == response.products
    assert "реальная деталь на трубной стороне" in counterpart.answer.lower()
    assert "не поддерживается" in counterpart.answer.lower()


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

    visual = bot.handle_chat(
        session_id,
        "Покажи, как выглядит такой тройник 87° в реальности — есть фото, "
        "видео или чертёж на странице?",
    )
    assert visual.debug["category"] == "sewer"
    assert [card.sku for card in visual.products] == ["TEE-110-50-87"]
    assert "странице его карточки" in visual.answer.lower()
    assert "110×50" in visual.answer


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

    purchase_check = bot.handle_chat(
        session_id,
        "Почему вн-нар важно для сварки и что дополнительно проверить перед покупкой?",
    )
    repeated_check = bot.handle_chat(
        session_id,
        "Почему вн-нар важно для сварки и что дополнительно проверить перед покупкой?",
    )
    assert "глубину вставки" in purchase_check.answer.lower()
    assert "итоговая проверка" in repeated_check.answer.lower()
    assert purchase_check.answer != repeated_check.answer
    assert [card.sku for card in repeated_check.products] == ["REDUCER"]

    geometry = bot.handle_chat(
        session_id,
        "Надо проверить, как устроена муфта. Есть схема или чертёж: где "
        "внутренний раструб, а где наружный патрубок?",
    )
    assert geometry.debug["category"] == "fittings"
    assert [card.sku for card in geometry.products] == ["REDUCER"]
    assert "не буду восстанавливать геометрию" in geometry.answer.lower()
    assert "чертёж" in geometry.answer.lower()


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

    companions = bot.handle_chat(
        session_id,
        "Что значит сверить с карточкой радиатора — какие именно узлы и клапаны нужны?",
    )
    assert [card.sku for card in companions.products] == ["RAD-6-500"]
    assert "на обратке" in companions.answer.lower()
    assert "термоголовку" in companions.answer.lower()
    assert companions.debug["category"] == "radiators"

    plain = bot.handle_chat(
        session_id,
        "Почему площадь обогрева около 10 м² не означает, что он перегреет "
        "комнату 10 м²? Объясни по-простому.",
    )
    assert "не означает" in plain.answer.lower()
    assert "предваритель" in plain.answer.lower()
    assert [card.sku for card in plain.products] == ["RAD-6-500"]

    heat_meaning = bot.handle_chat(
        session_id,
        "Что означает теплоотдача 1038 Вт и как это число влияет на выбор?",
    )
    heat_check = bot.handle_chat(
        session_id,
        "Как тогда проверить эту теплоотдачу для моей комнаты и системы?",
    )
    heat_repeat = bot.handle_chat(
        session_id,
        "Повторю: теплоотдача в ваттах точно подтверждает, что радиатора хватит?",
    )

    assert "не потребление электричества" in heat_meaning.answer.lower()
    assert "температурный график" in heat_check.answer.lower()
    assert "нельзя подтвердить" in heat_repeat.answer.lower()
    assert len({heat_meaning.answer, heat_check.answer, heat_repeat.answer}) == 3
    assert [card.sku for card in heat_repeat.products] == ["RAD-6-500"]


def test_radiator_pressure_help_stays_deterministic_then_allows_preliminary_cards() -> None:
    radiator = _product(
        "RAD-LIVE-6-500",
        "Радиатор биметаллический 6 секций 500 мм",
        "Радиаторы отопления",
        attributes={
            "количество секций": "6",
            "межосевое расстояние, мм": "500",
            "площадь обогрева, м2": "10",
            "рабочее давление, бар": "20",
        },
    )
    settings = get_settings().model_copy(
        update={"llm_provider": "openrouter", "openrouter_api_key": "test-key"}
    )
    bot = ChatOrchestrator(
        settings=settings,
        products=[radiator],
        llm_client=_TemptingConsultLLM(),
    )
    session_id = "radiator-pressure-observation"

    bot.handle_chat(
        session_id,
        "Нужно заменить батарею, старая на шесть секций, но больше ничего не знаю.",
    )
    help_answer = bot.handle_chat(
        session_id,
        "Центральное отопление, давление и гидроудары не знаю — где посмотреть?",
    )

    assert help_answer.debug["category"] == "radiators"
    assert help_answer.debug["final_answer_source"] != "consultant_llm"
    assert "ук/тсж" in help_answer.answer.lower()
    assert "паспорт старого радиатора" in help_answer.answer.lower()
    assert "operating_pressure_bar" in help_answer.debug["slots"][
        "deferred_slot_keys"
    ]

    bot.handle_chat(
        session_id,
        "Межосевое 500 мм, шесть секций. Где взять давление, если данных нет?",
    )
    shown = bot.handle_chat(
        session_id,
        "Давление не знаю — покажи предварительный вариант для комнаты 10 м² "
        "по тому, что уже известно.",
    )

    assert shown.debug["category"] == "radiators"
    assert [card.sku for card in shown.products] == ["RAD-LIVE-6-500"]
    assert "предваритель" in shown.answer.lower()
    assert "не обещание совместимости" in shown.answer.lower()


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
