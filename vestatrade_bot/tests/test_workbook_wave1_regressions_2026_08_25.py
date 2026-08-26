"""Regression coverage for systemic defects found by the adaptive live run."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.engineering_norms import match_engineering_norm
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.slot_filling import SlotFillingAgent
from app.agents.utils import normalize_text
from app.models import Product, SearchQuery, SessionState
from app.pii import redact_pii_for_model


def product(
    sku: str,
    name: str,
    category: str,
    attributes: dict[str, str],
    *,
    stock: int = 5,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="TEST",
        url=f"https://example.test/{sku}",
        price=1000,
        stock_status="в наличии" if stock else "нет в наличии",
        stock_qty=stock,
        attributes_normalized=attributes,
    )


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Нужен насос для канализационной установки", "pumps"),
        ("Подберите насос в систему с радиаторами", "pumps"),
        ("Нужна запорная арматура на стальную трубу", "valves"),
        ("Ищу кран Маевского для батареи", "radiator_fittings"),
    ],
)
def test_explicit_product_head_owns_category(message: str, category: str) -> None:
    intent = IntentRouterAgent().route(message)
    assert intent.category == category


def test_sewer_installation_context_sets_pump_subtype_without_becoming_pipe() -> None:
    intent = IntentRouterAgent().route(
        "Нужен насос для канализационной установки в подвале"
    )
    assert intent.category == "pumps"
    assert intent.slots["pump_type"] == "канализационная насосная установка"


def test_pump_goal_is_not_replaced_by_floor_problem_frame() -> None:
    pump = product(
        "P-25-6-180",
        "Насос циркуляционный 25/6 180 мм",
        "Насосы циркуляционные",
        {
            "тип товара": "циркуляционный насос",
            "присоединение": "25",
            "максимальный напор": "6 м",
            "монтажная длина": "180 мм",
        },
    )
    bot = ChatOrchestrator(products=[pump])
    response = bot.handle_chat(
        "pump-floor",
        "Нужен циркуляционный насос: расход 1,8 м3/ч, напор 4,5 м, гликоль 30%",
    )
    response = bot.handle_chat(
        "pump-floor",
        "Монтажная длина 180, присоединение 25; обслуживает радиаторы и тёплый пол",
    )

    assert response.debug["category"] == "pumps"
    assert "электрический тёплый пол" not in normalize_text(response.answer)


def test_unknown_well_measurement_is_deferred_instead_of_reasked() -> None:
    pump = product(
        "WELL-1",
        "Насос поверхностный для колодца",
        "Насосы поверхностные",
        {"тип товара": "поверхностный насос", "максимальный напор": "35 м"},
    )
    bot = ChatOrchestrator(products=[pump])
    bot.handle_chat("well-unknown", "Нужен насос для полива из колодца")
    response = bot.handle_chat("well-unknown", "Глубину до воды я не знаю")

    assert "глубину от верха колодца" not in normalize_text(response.answer)
    assert "water_level_depth_m" in response.debug["slots"].get(
        "deferred_slot_keys", []
    )


def test_grounded_domain_questions_do_not_fall_into_unrelated_catalogues() -> None:
    bot = ChatOrchestrator(products=[])

    compatibility = bot.handle_chat(
        "compat",
        "Фитинг Rehau подойдёт на трубу Valtec того же диаметра?",
    )
    noise = bot.handle_chat(
        "noise",
        "В квартире слышно, как соседи сливают воду по канализационному стояку. Что купить?",
    )
    correction = bot.handle_chat(
        "radiator-claim",
        "Алюминиевый радиатор ведь всегда безопасен для центрального отопления?",
    )

    assert "не подтверждает совместимость" in normalize_text(compatibility.answer)
    assert compatibility.debug["category"] == "fittings"
    assert "акустическ" in normalize_text(noise.answer)
    assert "pex" in normalize_text(noise.answer)
    assert noise.products == []
    assert "утверждать" in normalize_text(correction.answer)
    assert "нельзя" in normalize_text(correction.answer)


def test_manual_air_vent_is_grounded_as_radiator_fitting() -> None:
    vent = product(
        "R.400",
        'Воздухоотводчик д/рад. ручной 1/2"',
        "Комплектующие для радиаторов",
        {"тип товара": "ручной воздухоотводчик", "резьба": "1/2"},
        stock=39,
    )
    bot = ChatOrchestrator(products=[vent])
    response = bot.handle_chat(
        "maevsky",
        "Бабушке нужна штука, чтобы спускать воздух из радиатора — кран Маевского",
    )

    assert response.debug["category"] == "radiator_fittings"
    assert [card.sku for card in response.products] == ["R.400"]
    assert "фотограф" in normalize_text(response.answer)


def test_radiator_capability_browse_filters_on_declared_pressure() -> None:
    weak = product(
        "RAD-10",
        "Радиатор панельный 500x800",
        "Радиаторы",
        {"максимальное рабочее давление": "10 бар"},
    )
    strong = product(
        "RAD-16",
        "Радиатор стальной 500x800",
        "Радиаторы",
        {"максимальное рабочее давление": "16 бар"},
    )
    agent = FeedSearchAgent([weak, strong])
    result = agent.search(
        SearchQuery(
            original_text="какие радиаторы выдерживают не меньше 16 бар",
            category="radiators",
            slots={"operating_pressure_bar": 16.0, "capability_browse": True},
        )
    )

    assert [item.sku for item in result] == ["RAD-16"]


def test_solid_fuel_correction_is_not_mapped_to_electric() -> None:
    intent = IntentRouterAgent().route(
        "Газ не нужен: дом 300 м2, подберите твердотопливный котёл на дровах"
    )
    assert intent.category == "boilers"
    assert intent.slots["boiler_type"] == "твердотопливный"


def test_numeric_sku_tail_is_not_redacted_as_a_phone() -> None:
    text = "Муфта, арт. VTp.704.0.040025, цена 25 RUB"
    assert redact_pii_for_model(text) == text
    assert "[phone redacted]" in redact_pii_for_model(
        "Мой телефон +7 999 123-45-67"
    )


def test_no_gas_is_an_exclusion_not_an_invented_electric_boiler() -> None:
    message = "Нужен котёл на дом 300 м², газа нет"
    intent = IntentRouterAgent().route(message)

    assert intent.category == "boilers"
    assert intent.slots["gas_available"] is False
    assert "boiler_type" not in intent.slots

    result = SlotFillingAgent().fill(
        message,
        intent,
        SessionState(session_id="no-gas-source-choice"),
    )
    question = normalize_text(result.question or "")
    assert result.needs_clarification is True
    assert "газа нет" in question
    assert "электричество" in question
    assert "твердое топливо" in question or "твёрдое топливо" in question
    assert "boiler_type" not in result.slots


def test_pump_duty_with_coolant_stays_a_pump_request() -> None:
    intent = IntentRouterAgent().route(
        "Подберите насос на 1,8 м³/ч и 4,5 м, теплоноситель — пропиленгликоль 30%"
    )

    assert intent.category == "pumps"
    assert intent.slots["required_flow_m3_h"] == 1.8
    assert intent.slots["required_head_m"] == 4.5


@pytest.mark.parametrize(
    ("opening", "followup", "norm_key"),
    [
        (
            "Какой уклон канализационной трубы 110 мм нужен на 18 м?",
            "А если септик выше, какой получится перепад?",
            "sewer_slope",
        ),
        (
            "Сталь 3/4 меняю на PPR: 20 или 25 мм?",
            "Почему PPR 20 не сохраняет то же проходное сечение?",
            "steel_to_ppr",
        ),
    ],
)
def test_engineering_norm_keeps_original_dimensions_on_topical_followup(
    opening: str,
    followup: str,
    norm_key: str,
) -> None:
    first = match_engineering_norm(opening)
    assert first is not None and first.key == norm_key

    continued = match_engineering_norm(
        followup,
        previous_norm=first.key,
        previous_message=opening,
    )
    assert continued is not None and continued.key == norm_key
    assert normalize_text(continued.text) != normalize_text(first.text)


def test_pending_thread_choice_is_applied_to_catalog_search() -> None:
    mixed = product(
        "VALVE-FM",
        'Кран шаровой 1/2" вн.-нар.',
        "Краны шаровые",
        {
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "1/2",
            "тип резьбы": "С внутренней наружной резьбой (fm)",
            "рабочая среда": "вода",
            "назначение": "холодное водоснабжение",
        },
    )
    female = product(
        "VALVE-FF",
        'Кран шаровой 1/2" вн.-вн.',
        "Краны шаровые",
        {
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "1/2",
            "тип резьбы": "С внутренней резьбой (ff)",
            "рабочая среда": "вода",
            "назначение": "холодное водоснабжение",
        },
    )
    bot = ChatOrchestrator(products=[mixed, female])
    first = bot.handle_chat("thread-choice", "Нужен шаровой кран на стояк ХВС 1/2")
    assert "вр-вр" in normalize_text(first.answer)

    response = bot.handle_chat("thread-choice", "ВР-НР. Дайте подходящие артикулы")

    assert response.debug["slots"]["thread_type"] == "fm"
    assert [card.sku for card in response.products] == ["VALVE-FM"]
    assert "мама-папа" not in normalize_text(response.answer)


def test_pending_thread_choice_with_doubt_still_continues_selection() -> None:
    mixed = product(
        "VALVE-FM-DOUBT",
        'Кран шаровой 1/2" вн.-нар.',
        "Краны шаровые",
        {
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "1/2",
            "тип резьбы": "С внутренней наружной резьбой (fm)",
            "рабочая среда": "вода",
            "назначение": "холодное водоснабжение",
        },
    )
    female = product(
        "VALVE-FF-DOUBT",
        'Кран шаровой 1/2" вн.-вн.',
        "Краны шаровые",
        {
            "тип товара": "Кран шаровой",
            "диаметр подключения, дюйм": "1/2",
            "тип резьбы": "С внутренней резьбой (ff)",
            "рабочая среда": "вода",
            "назначение": "холодное водоснабжение",
        },
    )
    bot = ChatOrchestrator(products=[mixed, female])
    session_id = f"thread-doubt-{uuid4()}"
    bot.handle_chat(session_id, "Нужен шаровой кран на стояк ХВС 1/2")
    response = bot.handle_chat(session_id, "ВР-НР. Или это не важно?")

    assert response.debug["slots"]["thread_type"] == "fm"
    assert [card.sku for card in response.products] == ["VALVE-FM-DOUBT"]
    assert "опишите задачу" not in normalize_text(response.answer)


def test_perekhod_word_does_not_trigger_manager_handoff() -> None:
    bot = ChatOrchestrator(products=[])
    session = SessionState(session_id="not-a-handoff")

    assert (
        bot._maybe_handoff_process_question(  # noqa: SLF001 - regression seam
            "Нужен отвод PPR 25 без перехода"
        )
        is None
    )


def test_generic_house_inlet_is_not_misread_as_hot_water_shortage() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"house-inlet-{uuid4()}"
    bot.handle_chat(session_id, "Здравствуйте, нужна труба")
    response = bot.handle_chat(
        session_id,
        "Для воды, которая течёт в дом; не знаю, холодная или горячая",
    )

    answer = normalize_text(response.answer)
    assert response.debug["category"] == "pipes"
    assert "ввод холодного водоснабжения" in answer
    assert "нехватк" not in answer


def test_rehau_valtec_compatibility_task_persists_and_rejects_ppr_contradiction() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"mixed-systems-{uuid4()}"
    first = bot.handle_chat(
        session_id,
        "Труба Rehau Rautitan 20х2,8, а фитинг Valtec аксиальный. Подойдёт?",
    )
    followup = bot.handle_chat(
        session_id,
        "Труба — PPR, 20х2,8. Какие ещё параметры нужны?",
    )

    assert "не подтверждает совместимость" in normalize_text(first.answer)
    assert followup.debug["category"] == "fittings"
    assert "разные трубные системы" in normalize_text(followup.answer)
    assert "раструбной сваркой" in normalize_text(followup.answer)


def test_sewer_noise_guidance_checks_catalogue_and_answers_chance_directly() -> None:
    acoustic = product(
        "SINIKON-40",
        "Отвод PP 40×45 Синикон Комфорт Плюс",
        "Канализация бесшумная Синикон",
        {"тип товара": "отвод", "диаметр": "40 мм"},
    )
    bot = ChatOrchestrator(products=[acoustic])
    session_id = f"sewer-noise-{uuid4()}"
    first = bot.handle_chat(
        session_id,
        "Слышно, как сосед сливает воду по стояку. Что купить?",
    )
    followup = bot.handle_chat(
        session_id,
        "Есть ли хоть какой-то шанс частично уменьшить шум?",
    )

    assert "в каталоге есть" in normalize_text(first.answer)
    assert "синикон комфорт плюс" in normalize_text(first.answer)
    assert normalize_text(followup.answer).startswith("да, шанс")
    assert followup.products == []


def test_coolant_task_survives_named_boiler_and_never_returns_a_boiler_card() -> None:
    coolant = product(
        "WARME-30",
        "Теплоноситель WARME Eco Pro -30 20 кг",
        "Теплоносители",
        {"температура кристаллизации": "-30 °C"},
        stock=0,
    )
    boiler = product(
        "BAXI-OTHER",
        "Котёл Baxi LUNA 3 Comfort 1.310 Fi",
        "Котлы газовые",
        {"тип товара": "котёл"},
    )
    bot = ChatOrchestrator(products=[coolant, boiler])
    session_id = f"coolant-memory-{uuid4()}"
    bot.handle_chat(
        session_id,
        "Хочу залить антифриз в двухконтурный котёл Baxi. Какой взять?",
    )
    response = bot.handle_chat(
        session_id,
        "Модель Baxi LUNA 240, нужно -30 °C. Есть подходящий в наличии?",
    )

    answer = normalize_text(response.answer)
    assert response.debug["category"] == "other"
    assert "BAXI-OTHER" not in [card.sku for card in response.products]
    assert "прямой ответ" in answer
    assert "внешние склады" in answer


def test_simple_radiator_fitting_explanation_then_air_request_stays_on_task() -> None:
    vent = product(
        "VENT-SIMPLE",
        'Кран Маевского 1/2"',
        "Комплектующие для радиаторов",
        {"тип товара": "ручной воздухоотводчик", "резьба": "1/2"},
    )
    bot = ChatOrchestrator(products=[vent])
    session_id = f"simple-fitting-{uuid4()}"
    first = bot.handle_chat(
        session_id,
        "Нужен какой-то смеситель для батареи",
    )
    explanation = bot.handle_chat(
        session_id,
        "Я не знаю, что такое прямое или угловое. Объясните простыми словами",
    )
    response = bot.handle_chat(
        session_id,
        "Я просто хочу, чтобы вода шла и не было воздуха",
    )

    assert "прям" in normalize_text(first.answer)
    assert "труба подходит к радиатору прямо или с поворотом" in normalize_text(
        explanation.answer
    )
    assert response.debug["category"] == "radiator_fittings"
    assert [card.sku for card in response.products] == ["VENT-SIMPLE"]


def test_text_description_after_photo_boundary_continues_selection() -> None:
    valve = product(
        "MIX-3WAY",
        'Клапан трёхходовой термостатический смесительный 3/4"',
        "Смесительные клапаны",
        {
            "тип товара": "Клапан трёхходовой термостатический",
            "диаметр подключения, дюйм": "3/4",
        },
    )
    bot = ChatOrchestrator(products=[valve])
    first = bot.handle_chat(
        "photo-description",
        "Можно отправить фото детали из узла отопления?",
    )
    assert "не поддерж" in normalize_text(first.answer)

    response = bot.handle_chat(
        "photo-description",
        (
            "Опишу словами: это трёхходовой клапан с термостатическим регулятором. "
            "Покажите похожие позиции."
        ),
    )

    assert response.debug["category"] == "valves"
    assert [card.sku for card in response.products] == ["MIX-3WAY"]
    assert "описания уже достаточно" in normalize_text(response.answer)
