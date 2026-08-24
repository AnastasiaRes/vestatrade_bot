"""Regressions from exploratory live buyers who do not name a product first."""

from __future__ import annotations

import pytest

from app.agents.engineering_interpreter import EngineeringInterpretation
from app.agents.problem_framing import frame_customer_problem
from app.agents.orchestrator import ChatOrchestrator
from app.models import IntentResult, Product, ProductCard


@pytest.mark.parametrize(
    ("message", "code", "category"),
    [
        ("После душа опять не хватило нагретой воды", "hot_water_shortage", "water_heaters"),
        ("В кружке осадок, а у воды странный привкус", "water_quality", "filters"),
        ("Из душевого слива неприятно пахнет", "sewer_odor", "sewer"),
        ("После дождей затапливает погреб", "standing_water", "pumps"),
        ("Котёл перестал работать, нужна замена", "boiler_failure", "boilers"),
        ("На ремонте хочется, чтобы пол не был холодным", "floor_comfort", "pipes"),
        (
            "На мансарде в душе струя совсем грустная, а внизу лучше",
            "weak_pressure",
            "pumps",
        ),
        (
            "Батареи ледяные, а старая коробка в котельной молчит",
            "boiler_failure",
            "boilers",
        ),
        (
            "В душе моются, а на кухне уже холодная течёт",
            "hot_water_shortage",
            "water_heaters",
        ),
        (
            "Вода отдаёт железом и оставляет белёсое на дне кружки",
            "water_quality",
            "filters",
        ),
        (
            "После командировки возле душа канализацией несёт",
            "sewer_odor",
            "sewer",
        ),
        (
            "Ремонт в квартире, хочется босиком ходить, а не по холоду",
            "floor_comfort",
            "pipes",
        ),
        (
            "Под кухней сыро возле красной ручки на трубе",
            "undersink_shutoff_leak",
            "valves",
        ),
    ],
)
def test_problem_frames_are_concept_based_paraphrases(
    message: str,
    code: str,
    category: str,
) -> None:
    frame = frame_customer_problem(message)

    assert frame is not None
    assert frame.code == code
    assert frame.category == category


def test_water_quality_problem_does_not_retrieve_random_mesh_filter(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "problem-water-quality",
        "В чайнике быстро осадок и вода неприятная на вкус. Что поставить?",
    )
    second = orchestrator.handle_chat(
        "problem-water-quality",
        "Не знаю названий фильтров, хочу просто убрать осадок и привкус.",
    )

    assert first.products == []
    assert second.products == []
    assert "анализ" in first.answer.lower()
    assert "сетчат" in second.answer.lower()
    assert "не убирает" in second.answer.lower()


def test_sewer_odor_starts_with_diagnosis_not_pipe_dimensions(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "problem-sewer-odor",
        "После выходных из душа тянет неприятным запахом, что делать?",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "гидрозатвор" in answer
    assert "налейте воду" in answer
    assert "диаметр" not in answer


def test_uncertain_water_floor_mention_does_not_become_a_selection(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-choice",
        "Сняли старое покрытие, не хотим снова холодный пол. С чего начать?",
    )
    response = orchestrator.handle_chat(
        "problem-floor-choice",
        "Квартира, около 18 квадратов. А водяной вариант вообще можно?",
    )

    assert response.products == []
    assert response.debug["slots"].get("warm_floor_type") is None
    assert "водяной" in response.answer.lower()
    assert "электр" in response.answer.lower()


def test_drainage_goal_cannot_be_retyped_by_llm_without_user_evidence(orchestrator) -> None:
    session = orchestrator.sessions.get("problem-drainage-goal")
    session.category = "pumps"
    session.pending_category = "pumps"
    session.slots = {
        "pump_use": "откачка воды",
        "pump_type": "дренажный",
    }
    intent = IntentResult(
        intent_type="attribute_request",
        category="other",
        confidence=0.4,
        slots={},
    )
    interpretation = EngineeringInterpretation(
        handled=True,
        output_accepted=True,
        category="sewer",
        slots={
            "pump_use": "водоснабжение",
            "pump_type": "скважинный",
            "water_source": "скважина",
        },
    )

    orchestrator._overlay_engineering_interpretation(
        "Не знаю размер частиц, вода просто мутная",
        intent,
        interpretation,
        session,
    )

    assert intent.category == "other"
    assert "pump_use" not in intent.slots
    assert "pump_type" not in intent.slots
    assert "water_source" not in intent.slots
    assert set(intent.raw["rejected_llm_goal_overrides"]) == {
        "pump_use",
        "pump_type",
        "water_source",
    }


def test_boiler_failure_asks_household_facts_instead_of_defining_boiler(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "problem-boiler-failure",
        "Наш котёл перестал работать, дома холодно и не понимаю, что брать взамен.",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "площад" in answer
    assert "газ" in answer and "электр" in answer
    assert "теплогенератор" not in answer


def test_unknown_valve_size_gets_an_observable_identification_path(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "problem-valve-size",
        "Под раковиной капает штука с ручкой, нужен новый кран, но размера не знаю.",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "маркиров" in answer
    assert "1/2" in answer and "3/4" in answer
    assert "не разбирая" in answer


def test_basement_water_followup_keeps_drainage_goal(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-drainage-context",
        "После ливней в погребе стоит вода, хочу перестать вычерпывать её вручную.",
    )
    response = orchestrator.handle_chat(
        "problem-drainage-context",
        "Вода мутная, иногда с песком, размер частиц я не знаю.",
    )

    assert response.debug["category"] == "pumps"
    assert response.debug["slots"].get("pump_type") == "дренажный"
    assert "фильтр" not in response.answer.lower()


def test_stock_status_question_uses_grounded_card_state(orchestrator) -> None:
    session = orchestrator.sessions.get("problem-stock-status")
    session.category = "boilers"
    session.last_products = [
        ProductCard(
            sku="TEST-BOILER",
            name="Тестовый котёл",
            price=100.0,
            stock_status="нет в наличии",
            stock_qty=0,
            url="https://example.test/product",
        )
    ]

    response = orchestrator.handle_chat(
        "problem-stock-status",
        "Что значит «нет в наличии», его всё-таки можно заказать?",
    )

    answer = response.answer.lower()
    assert "нет подтверждённого положительного остатка" in answer
    assert "срок следующего поступления" in answer
    assert response.products[0].sku == "TEST-BOILER"


@pytest.mark.parametrize(
    ("opening", "followup", "expected"),
    [
        (
            "На мансарде в душе струя грустная, а внизу лучше.",
            "Центральный водопровод, наверху во всех кранах слабо течёт.",
            "манометром",
        ),
        (
            "В душе моются, а на кухне уже холодная течёт.",
            "Вода из бойлера, потом из крана течёт холодно.",
            "табличке",
        ),
    ],
)
def test_service_flow_inside_problem_context_is_not_a_flood(
    orchestrator,
    opening: str,
    followup: str,
    expected: str,
) -> None:
    orchestrator.handle_chat("problem-service-flow-" + expected, opening)
    response = orchestrator.handle_chat(
        "problem-service-flow-" + expected,
        followup,
    )

    assert response.debug["intent"] != "emergency"
    assert "аварийн" not in response.answer.lower()
    assert expected in response.answer.lower()


def test_acknowledged_sewer_check_closes_instead_of_repeating(orchestrator) -> None:
    first = orchestrator.handle_chat(
        "problem-odor-ack",
        "После командировки возле душа канализацией несёт.",
    )
    second = orchestrator.handle_chat(
        "problem-odor-ack",
        "Начну с ванны, потом долью воду в трап и проверю сливы по одному.",
    )

    assert second.answer != first.answer
    assert "правильный безопасный порядок" in second.answer.lower()
    assert "пересыхание гидрозатвора" in second.answer.lower()


def test_heat_loss_term_has_observable_homeowner_path(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "problem-heat-loss",
        "Что значит теплопотери здания и как их узнать, если я не инженер?",
    )

    answer = response.answer.lower()
    assert "стены" in answer and "окна" in answer
    assert "собрать" in answer and "исходные данные" in answer
    assert "не подскажу без проверки" not in answer


def test_water_analysis_followup_explains_sample_collection(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-water-sample",
        "Вода отдаёт железом и оставляет белёсое на дне кружки.",
    )
    orchestrator.handle_chat(
        "problem-water-sample",
        "Центральный водопровод, питьевая точка на кухне. Где сделать анализ?",
    )
    response = orchestrator.handle_chat(
        "problem-water-sample",
        "Где взять пробу и как правильно сдать её в лабораторию?",
    )

    answer = response.answer.lower()
    assert "инструкцию и тару" in answer
    assert "случайную бутылку" in answer
    assert "не подскажу без проверки" not in answer


def test_water_utility_protocol_question_gets_document_path(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-water-protocol",
        "Вода отдаёт железом и оставляет белёсое на дне кружки.",
    )
    response = orchestrator.handle_chat(
        "problem-water-protocol",
        "Где взять протокол водоканала и в каком виде он будет?",
    )

    answer = response.answer.lower()
    assert "официальный сайт" in answer
    assert "датой и местом отбора" in answer
    assert "pdf" in answer


def test_old_valve_marking_question_gets_exact_location(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-old-valve-mark",
        "Под кухней сыро возле красной ручки на трубе.",
    )
    response = orchestrator.handle_chat(
        "problem-old-valve-mark",
        "Где именно искать цифры на старом кране?",
    )

    answer = response.answer.lower()
    assert "не на красной ручке" in answer
    assert "плоской грани под ключ" in answer
    assert "не назначайте 1/2 или 3/4 по линейке" in answer


def test_drainage_measurements_are_explained_in_observable_terms(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-drain-observable",
        "После ливня в погребе болото с мутью и песком.",
    )
    response = orchestrator.handle_chat(
        "problem-drain-observable",
        "Подъём 2 метра, шланг 5 метров, вода с песком и мелким мусором.",
    )

    answer = response.answer.lower()
    assert "дренажный для грязной воды" in answer
    assert "длина × ширина" in answer
    assert "скважин" not in answer

    calculated = orchestrator.handle_chat(
        "problem-drain-observable",
        "Зона 2 на 1 метр, глубина до 30 см, хочу выкачать за 2 часа. Какая производительность?",
    )
    calculated_answer = calculated.answer.lower()
    assert "0.6 м³" in calculated_answer
    assert "0.3 м³/ч" in calculated_answer
    assert "не мощность двигателя" in calculated_answer

    qh = orchestrator.handle_chat(
        "problem-drain-observable",
        "Что такое Q–H-кривая и какой диаметр шланга выбрать?",
    )
    qh_answer = qh.answer.lower()
    assert "по горизонтали идёт подача" in qh_answer
    assert "самовольно назначать 25 мм" in qh_answer


def test_floor_action_acknowledgement_does_not_repeat_safety_paragraph(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-ack",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    central = orchestrator.handle_chat(
        "problem-floor-ack",
        "Квартира, центральное отопление, площадь 60 квадратов.",
    )
    electric = orchestrator.handle_chat(
        "problem-floor-ack",
        "Тогда можно проверить электрический мат как альтернативу?",
    )
    acknowledgement = orchestrator.handle_chat(
        "problem-floor-ack",
        "Надо проверить с электриком выделенную мощность и покрытие.",
    )

    assert "общедомов" in central.answer.lower()
    assert "подтверждённых матов" in electric.answer.lower()
    assert acknowledgement.answer != electric.answer
    assert "достаточный следующий шаг" in acknowledgement.answer.lower()


def test_central_floor_hydraulics_question_requires_project_not_diy_trial(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-hydraulics",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-hydraulics",
        "Квартира, центральное отопление, 40 квадратов.",
    )
    response = orchestrator.handle_chat(
        "problem-floor-hydraulics",
        "Как проверить гидравлику, если попробовать подключить водяной пол?",
    )

    answer = response.answer.lower()
    assert "домашним замером нельзя" in answer
    assert "не врезайтесь для эксперимента" in answer
    assert "управляющей организации" in answer


def test_electric_floor_question_uses_free_area_not_hydronic_cart(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-electric-calc",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-electric-calc",
        "Квартира, центральное отопление, 60 квадратов.",
    )
    response = orchestrator.handle_chat(
        "problem-floor-electric-calc",
        "Как рассчитать мощность электрического мата и какие компоненты нужны?",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "свободную обогреваемую площадь" in answer
    assert "общая мощность" in answer
    assert "водяные трубы" in answer


def test_existing_hot_water_tank_context_cannot_switch_to_drainage(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-hot-tank-context",
        "В душе моются, а на кухне уже холодная течёт.",
    )
    measured = orchestrator.handle_chat(
        "problem-hot-tank-context",
        "Бойлер 200 л, 3 кВт, 60 градусов, через 5–7 минут снова теплеет.",
    )
    flow = orchestrator.handle_chat(
        "problem-hot-tank-context",
        "Можно проверить, не перекрывает ли что-то в подвале поток воды?",
    )

    assert "полный бак" in measured.answer.lower()
    assert "сравните напор холодной и горячей" in flow.answer.lower()
    assert flow.debug["category"] == "water_heaters"
    assert "вертикальный подъём" not in flow.answer.lower()


def test_failed_boiler_diagnosis_request_outranks_stock_cards(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-old-boiler-diagnosis",
        "Батареи ледяные, а старая коробка в котельной молчит.",
    )
    orchestrator.handle_chat(
        "problem-old-boiler-diagnosis",
        "Газ, дом 90 квадратов, нужна горячая вода.",
    )
    response = orchestrator.handle_chat(
        "problem-old-boiler-diagnosis",
        "Давайте сначала проверим, почему старый котёл молчит и не запускается.",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "штатный дисплей" in answer
    assert "не снимайте крышку" in answer
    assert "только в наличии" not in answer


def test_one_circuit_plus_tank_comparison_answers_practical_tradeoff(orchestrator) -> None:
    session = orchestrator.sessions.get("problem-boiler-dhw-architecture")
    session.category = "boilers"
    response = orchestrator.handle_chat(
        "problem-boiler-dhw-architecture",
        "Одноконтурный котёл с бойлером сложнее, чем двухконтурный?",
    )

    answer = response.answer.lower()
    assert "сложнее по монтажу" in answer
    assert "запас горячей воды" in answer
    assert "готовит воду проточно" in answer


def test_drainage_implicit_one_hour_is_calculated_and_reused(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-drain-implicit-hour",
        "После ливня в погребе мутная вода с песком.",
    )
    orchestrator.handle_chat(
        "problem-drain-implicit-hour",
        "Подъём 2 метра, шланг около 5 метров.",
    )
    calculated = orchestrator.handle_chat(
        "problem-drain-implicit-hour",
        "Зона 3 на 2 метра, глубина воды до 30 см, справиться нужно за час.",
    )
    followup = orchestrator.handle_chat(
        "problem-drain-implicit-hour",
        "Какой класс грязи и какую мощность брать?",
    )

    assert "1.8 м³" in calculated.answer.lower()
    assert "1.8 м³/ч" in calculated.answer.lower()
    assert "не менее 1.8 м³/ч" in followup.answer.lower()
    assert "мощность в ваттах по отдельности" in followup.answer.lower()


def test_electric_floor_uses_customer_area_and_asks_for_decisive_inputs(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-customer-area",
        "Делаем ремонт в квартире, хочется ходить босиком, но систему не выбрали.",
    )
    orchestrator.handle_chat(
        "problem-floor-customer-area",
        "Квартира 40 кв.м., отопление центральное.",
    )
    response = orchestrator.handle_chat(
        "problem-floor-customer-area",
        "Какой электрический мат взять на 40 квадратов, чтобы не перегрелся?",
    )

    answer = response.answer.lower()
    assert "указанные 40 м²" in answer
    assert "60 м²" not in answer
    assert "терморегулятор" in answer and "датчик пола" in answer
    assert "назовите покрытие" in answer
    assert "площадь свободных зон" in answer


def test_water_quality_explains_how_to_find_accredited_lab(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-water-lab-search",
        "В кружке белёсый осадок, а вода отдаёт железом.",
    )
    response = orchestrator.handle_chat(
        "problem-water-lab-search",
        "Где искать такую лабораторию и как понять, что она подходит?",
    )

    answer = response.answer.lower()
    assert "реестр аккредитованных лиц" in answer
    assert "области аккредитации" in answer
    assert "принимают ли пробы от частных лиц" in answer


def test_undersink_valve_photo_followup_outranks_generic_sink_menu(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-valve-photo",
        "Под кухней сыро возле красной ручки на трубе.",
    )
    response = orchestrator.handle_chat(
        "problem-valve-photo",
        "Где именно сфотографировать под мойкой и можно ли измерить руками?",
    )

    answer = response.answer.lower()
    assert response.debug["category"] == "valves"
    assert "три снимка" in answer
    assert "не разбирайте его под давлением" in answer
    assert "слив/сифон, подводка или кран" not in answer


def test_handoff_command_outranks_stale_sink_clarification(orchestrator) -> None:
    session = orchestrator.sessions.get("problem-valve-handoff")
    session.category = "sewer"
    session.slots = {"sink_flow": "awaiting_kind"}
    session.pending_question = "Нужен слив/сифон или запорный кран?"

    response = orchestrator.handle_chat(
        "problem-valve-handoff",
        "Передай менеджеру",
    )

    answer = response.answer.lower()
    assert response.debug["intent"] == "handoff_request"
    assert "заявку менеджеру пока не отправляю" in answer
    assert "телефон или email" in answer
    assert "слив/сифон" not in answer


def test_water_quality_frame_retires_after_explicit_solution_choice(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-water-solution-transition",
        "В кружке белёсый осадок, а вода отдаёт железом.",
    )
    chosen = orchestrator.handle_chat(
        "problem-water-solution-transition",
        "Хорошо, тогда посмотрю каталог полной системы обратного осмоса.",
    )
    stock = orchestrator.handle_chat(
        "problem-water-solution-transition",
        "Где посмотреть, какие из этих фильтров реально есть в наличии?",
    )

    assert chosen.debug["slots"].get("_problem_frame") is None
    assert "лабораторного анализа" not in stock.answer.lower()
    assert stock.debug["intent"] == "stock_request"


def test_repeated_hose_question_gets_direct_non_duplicate_answer(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-drain-hose-repeat",
        "После ливня в погребе стоит вода с песком.",
    )
    first = orchestrator.handle_chat(
        "problem-drain-hose-repeat",
        "Можно взять любой шланг или надо проверять диаметр шланга?",
    )
    second = orchestrator.handle_chat(
        "problem-drain-hose-repeat",
        "Можно взять любой шланг или надо проверять диаметр шланга?",
    )

    assert first.answer != second.answer
    assert "любой шланг" in first.answer.lower()
    assert "выход насоса" in second.answer.lower()
    assert "паспорт" in second.answer.lower()


def test_repeated_floor_hydraulics_question_gets_direct_non_duplicate_answer(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-hydraulics-repeat",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    first = orchestrator.handle_chat(
        "problem-floor-hydraulics-repeat",
        "Можно просто подключить водяной пол и проверить гидравлику?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-hydraulics-repeat",
        "А простого способа проверить гидравлику дома точно нет?",
    )

    assert first.answer != second.answer
    assert "домашнего теста нет" in second.answer.lower()
    assert "не подключайтесь" in second.answer.lower()


def test_water_protocol_and_offline_lab_search_are_answered_together(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-water-compound-path",
        "В кружке белёсый осадок, а вода отдаёт железом.",
    )
    response = orchestrator.handle_chat(
        "problem-water-compound-path",
        "Где взять протокол водоканала и как реально найти лабораторию в городе?",
    )

    answer = response.answer.lower()
    assert "квитанц" in answer
    assert "управляющая компания" in answer
    assert "центр гигиены" in answer
    assert "роспотребнадзор" in answer
    assert "актуальную цену" in answer


def test_floor_future_action_acknowledgement_closes_hydraulic_branch(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-future-action",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-future-action",
        "Можно просто подключить водяной пол и проверить гидравлику?",
    )
    response = orchestrator.handle_chat(
        "problem-floor-future-action",
        "Хорошо, напишу в управляющую компанию, а пока подумаю про электрический пол.",
    )

    answer = response.answer.lower()
    assert "достаточный и безопасный следующий шаг" in answer
    assert response.debug["slots"].get("_problem_frame") is None


def test_electric_power_question_is_not_mistaken_for_hydraulic_check(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-electric-precedence",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-electric-precedence",
        "Квартира 40 кв.м., отопление центральное.",
    )
    response = orchestrator.handle_chat(
        "problem-floor-electric-precedence",
        "Как проверить, сколько мощности нужно для электрического пола?",
    )

    answer = response.answer.lower()
    assert "общая мощность" in answer
    assert "свободную обогреваемую площадь" in answer
    assert "домашнего теста" not in answer


def test_electric_floor_universal_kit_question_gets_direct_answer(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-no-universal",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    first = orchestrator.handle_chat(
        "problem-floor-no-universal",
        "Можно взять универсальный электрический мат и не измерять свободные зоны?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-no-universal",
        "Точно нужно измерять свободные зоны или есть универсальный кабель?",
    )

    assert "универсального" in first.answer.lower()
    assert "измеряют обязательно" in first.answer.lower()
    assert first.answer != second.answer
    assert "измерять нужно" in second.answer.lower()


def test_colloquial_lab_contact_question_enters_lab_search_path(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-water-lab-colloquial",
        "В кружке белёсый осадок, а вода отдаёт железом.",
    )
    response = orchestrator.handle_chat(
        "problem-water-lab-colloquial",
        "Где я могу найти такую лабораторию и как к ней обратиться?",
    )

    answer = response.answer.lower()
    assert "росаккредитации" in answer
    assert "роспотребнадзора" in answer
    assert "лабораторного анализа" not in answer


def test_floor_area_is_remembered_without_hardcoded_value(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-area-memory",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-area-memory",
        "Квартира около 60 кв.м., отопление центральное.",
    )
    response = orchestrator.handle_chat(
        "problem-floor-area-memory",
        "Можно взять универсальный электрический мат под такую площадь?",
    )

    answer = response.answer.lower()
    assert "общей площади 60 м²" in answer
    assert "40 м²" not in answer


def test_electric_floor_answers_measure_before_search_order(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-order",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    first = orchestrator.handle_chat(
        "problem-floor-order",
        "Как начать: сначала измерить свободные зоны или сразу искать комплекты электрического мата?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-order",
        "Точно сначала измерять или сразу искать мат?",
    )

    assert first.answer.lower().startswith("сначала измерьте")
    assert "только потом" in second.answer.lower()
    assert first.answer != second.answer


def test_analysis_choice_without_lab_word_enters_access_instructions(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-water-analysis-choice",
        "В кружке белёсый осадок, а вода отдаёт железом.",
    )
    orchestrator.handle_chat(
        "problem-water-analysis-choice",
        "Можно поставить фильтр сразу или лучше сначала сделать анализ?",
    )
    response = orchestrator.handle_chat(
        "problem-water-analysis-choice",
        "Хорошо, сначала сделаю анализ воды — куда и как обращаться?",
    )

    answer = response.answer.lower()
    assert "роспотребнадзора" in answer
    assert "центр гигиены" in answer
    assert "универсального картриджа" not in answer


def test_valve_size_alternatives_in_question_are_not_a_confirmed_size(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-valve-size-alternatives",
        "Под кухней сыро возле красной ручки на трубе.",
    )
    response = orchestrator.handle_chat(
        "problem-valve-size-alternatives",
        "Где на старом кране искать цифры 1/2 или 3/4, если видна только красная ручка?",
    )

    answer = response.answer.lower()
    assert response.products == []
    assert "не на красной ручке" in answer
    assert "плоской грани под ключ" in answer
    assert "точное значение этого термина" not in answer


def test_floor_question_about_writing_management_company_gets_yes_and_steps(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-management-request",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-management-request",
        "Можно просто подключить водяной пол и проверить гидравлику?",
    )
    response = orchestrator.handle_chat(
        "problem-floor-management-request",
        "Можно написать в управляющую компанию и спросить, не нарушит ли подключение водяного пола гидравлику, без врезки в стояк?",
    )

    answer = response.answer.lower()
    assert answer.startswith("да, это правильный первый шаг")
    assert "письменный запрос" in answer
    assert "не начинайте монтаж" in answer
    assert response.debug["slots"].get("_problem_frame") == "floor_comfort"

    acknowledgement = orchestrator.handle_chat(
        "problem-floor-management-request",
        "Напишу в управляющую компанию и спрошу, какие документы и расчёт нужны.",
    )
    assert "достаточный и безопасный следующий шаг" in acknowledgement.answer.lower()
    assert acknowledgement.debug["slots"].get("_problem_frame") is None


def test_filter_feature_and_price_comparison_is_grounded_in_shown_cards() -> None:
    products = [
        Product(
            sku="20037",
            name="Фильтр Гейзер Аллегро М (RO, бак 12 л, минерализатор)",
            category_path="Фильтры",
            price=10990,
            stock_status="нет в наличии",
            stock_qty=0,
            url="https://example.test/20037",
        ),
        Product(
            sku="20038",
            name="Фильтр Гейзер Аллегро П (RO, бак 12 л)",
            category_path="Фильтры",
            price=17300,
            stock_status="нет в наличии",
            stock_qty=0,
            url="https://example.test/20038",
            description=(
                "Работает при давлении не менее 2 атм. Устройство повышения "
                "давления, входящее в комплект, повысит уровень давления до 3-6 атм."
            ),
        ),
    ]
    bot = ChatOrchestrator(products=products)
    session = bot.sessions.get("problem-filter-card-comparison")
    session.category = "filters"
    session.last_products = [
        ProductCard(
            sku=product.sku,
            name=product.name,
            price=float(product.price or 0),
            stock_status=product.stock_status,
            stock_qty=product.stock_qty,
            url=product.url or "https://example.test",
        )
        for product in products
    ]

    response = bot.handle_chat(
        "problem-filter-card-comparison",
        "Что за минерализатор в модели М и почему П стоит втрое дороже?",
    )

    answer = response.answer.lower()
    assert "дополнительная ступень" in answer
    assert "6310 rub" in answer
    assert "около 57%" in answer
    assert "а не втрое" in answer
    assert "устройство повышения давления" in answer
    assert "вся наценка" in answer


def test_electric_floor_choice_is_explained_in_novice_terms(orchestrator) -> None:
    orchestrator.handle_chat(
        "problem-floor-simple-choice",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    first = orchestrator.handle_chat(
        "problem-floor-simple-choice",
        "Как понять простыми словами, какой электрический мат или кабель подойдёт для конкретной комнаты?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-simple-choice",
        "А можно выбрать мат или кабель, совсем не глядя в технические характеристики?",
    )

    first_answer = first.answer.lower()
    second_answer = second.answer.lower()
    assert "площадь комплекта" in first_answer
    assert "лишний нагревательный кабель нельзя отрезать" in first_answer
    assert "мат обычно удобнее" in first_answer
    assert "отдельный кабель гибче" in first_answer
    assert first.answer != second.answer
    assert "четыре совпадения" in second_answer


def test_floor_permission_documents_stay_in_problem_frame_not_pipe_catalog(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-permission-docs",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    first = orchestrator.handle_chat(
        "problem-floor-permission-docs",
        "Как проверить, разрешён ли водяной пол без обращения к проектировщику?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-permission-docs",
        "Какие документы нужно запросить в управляющей организации?",
    )

    assert first.products == []
    assert "управляющую организацию или тсж" in first.answer.lower()
    assert second.products == []
    assert "в письменном запросе" in second.answer.lower()
    assert "технические условия" in second.answer.lower()
    assert second.debug["category"] == "pipes"


def test_floor_building_plan_question_is_not_retyped_as_heating_catalog(
    orchestrator,
) -> None:
    orchestrator.handle_chat(
        "problem-floor-building-plan",
        "Ремонт в квартире, хочется босиком ходить, а не по холоду.",
    )
    orchestrator.handle_chat(
        "problem-floor-building-plan",
        "Как проверить, разрешён ли водяной пол без обращения к проектировщику?",
    )
    first = orchestrator.handle_chat(
        "problem-floor-building-plan",
        "Можно посмотреть в техпаспорте или планах дома, где указано, что разрешено с отоплением?",
    )
    second = orchestrator.handle_chat(
        "problem-floor-building-plan",
        "В техпаспорте есть ли информация именно про разрешение водяного пола?",
    )

    assert first.products == []
    assert "сам по себе обычно не является разрешением" in first.answer.lower()
    assert "технической документации дома" in first.answer.lower()
    assert second.products == []
    assert "не документ с ответом" in second.answer.lower()
    assert first.answer != second.answer
