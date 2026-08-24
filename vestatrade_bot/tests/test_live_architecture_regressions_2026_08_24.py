from __future__ import annotations

from app.agents.guardrails import GuardrailsAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.numeric_semantics import extract_total_length_m
from app.agents.orchestrator import ChatOrchestrator
from app.business_config import BusinessFacts
from app.models import Product, SessionState


def _radiator(index: int, *, sections: int | None = None) -> Product:
    section_count = sections if sections is not None else index + 5
    return Product(
        sku=f"RAD-LIVE-{section_count}-{index}",
        name=f"Радиатор биметаллический 500 мм {section_count} секций",
        category_path="Радиаторы отопления / Биметаллические",
        brand="TEST-RAD",
        url=f"https://example.test/radiator-{section_count}-{index}",
        price=10_000 + index * 1_000,
        stock_status="в наличии",
        stock_qty=10 + index,
        attributes_normalized={
            "тип товара": "Радиатор отопления",
            "межосевое расстояние, мм": "500",
            "количество секций": str(section_count),
            "высота, мм": "570",
            "ширина секции, мм": "80",
            "теплоотдача, Вт": str(150 * section_count),
            "материал": "биметалл",
        },
    )


def test_unverified_business_email_is_removed_at_final_guard() -> None:
    cleaned, issues = GuardrailsAgent().strip_unverified_operational_claims(
        "Официальная почта магазина support@invented.example.",
        facts=BusinessFacts(site_url="https://example.test"),
    )

    assert any("email" in issue for issue in issues)
    assert "support@invented.example" not in cleaned
    assert "подтверждает менеджер" in cleaned.lower()


def test_verified_business_email_survives_final_guard() -> None:
    facts = BusinessFacts(emails=("shop@example.test",))

    cleaned, issues = GuardrailsAgent().strip_unverified_operational_claims(
        "Официальная почта магазина shop@example.test.",
        facts=facts,
    )

    assert issues == []
    assert "shop@example.test" in cleaned


def test_store_email_request_never_turns_into_callback_collection() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "architecture-store-email",
        "Дайте официальный email или мессенджер вашего магазина в Самаре. "
        "Телефон не подходит, свои данные оставлять не хочу.",
    )

    normalized = response.answer.lower()
    assert response.debug["contact_direction"] == "store_to_customer"
    assert "оставьте телефон" not in normalized
    assert "оставьте" not in normalized or "личн" not in normalized
    assert "email" in normalized or "мессендж" in normalized
    assert bot.sessions.get("architecture-store-email").pending_handoff is None


def test_store_address_followup_keeps_store_to_customer_direction() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-store-address-followup"

    first = bot.handle_chat(
        session_id,
        "Нужен официальный email или чат вашего магазина в Самаре. "
        "Свои контакты оставлять не буду.",
    )
    second = bot.handle_chat(
        session_id,
        "Тогда напишите адрес точки в Самаре или ссылку, где его посмотреть.",
    )

    assert first.debug["contact_direction"] == "store_to_customer"
    assert second.debug["contact_direction"] == "store_to_customer"
    assert "ново-вокзальная" in second.answer.lower() or "энтузиастов" in second.answer.lower()
    assert "оставьте телефон" not in second.answer.lower()
    assert bot.sessions.get(session_id).pending_handoff is None


def test_direct_manager_contact_is_not_replaced_with_pickup_points() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "architecture-manager-direct-contact",
        "Дайте прямой контакт менеджера, который может вручную проверить "
        "склад, а не адреса пунктов выдачи.",
    )

    normalized = response.answer.lower()
    assert response.debug["contact_direction"] == "store_to_customer"
    assert "проверенного прямого" in normalized
    assert "складские и коммерческие данные" in normalized
    assert "пункты выдачи есть" not in normalized


def test_preview_only_instruction_is_not_handoff_opt_out() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "architecture-preview-not-opt-out",
        "Передайте менеджеру вопрос по радиаторам. "
        "support@rehau.example — email производителя, не мой. "
        "Мой контакт buyer.qa@example.com. Сначала покажите итог и ничего "
        "не отправляйте, пока я явно не подтвержу.",
    )

    state = bot.sessions.get("architecture-preview-not-opt-out")
    assert response.need_handoff is True
    assert state.handoff_status == "awaiting_consent"
    assert state.contact == "buyer.qa@example.com"
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == "buyer.qa@example.com"
    assert "support@rehau.example" not in str(state.pending_handoff)
    assert "подтверд" in response.answer.lower()
    assert "продолжим подбор здесь" not in response.answer.lower()


def test_recorded_handoff_explains_email_capability_without_falling_into_chat() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-recorded-handoff-capability"
    preview = bot.handle_chat(
        session_id,
        "Подготовьте менеджеру вопрос. Мой email buyer.qa@example.com. "
        "Сначала покажите итог.",
    )
    assert preview.handoff_status == "awaiting_consent"
    recorded = bot.handle_chat(session_id, "Подтверждаю передачу.")
    assert recorded.handoff_status == "locally_recorded"

    followup = bot.handle_chat(
        session_id,
        "Теперь перешлите этот черновик на мой email.",
    )

    normalized = followup.answer.lower()
    assert "не умею отправлять письма" in normalized
    assert "показан" in normalized and "до подтверждения" in normalized
    assert "crm" in normalized
    assert "чем могу помочь" not in normalized


def test_recorded_handoff_can_restate_exact_payload_then_ends_repeat() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-recorded-payload"
    bot.handle_chat(
        session_id,
        "Подготовьте вопрос менеджеру о радиаторах. Мой email "
        "payload.qa@example.com. Сначала покажите итог.",
    )
    bot.handle_chat(session_id, "Подтверждаю передачу.")

    first = bot.handle_chat(
        session_id,
        "Что именно нужно отправить менеджеру? Покажите состав черновика.",
    )
    second = bot.handle_chat(
        session_id,
        "Ещё раз: что именно должен получить менеджер из этого черновика?",
    )

    assert "точный состав" in first.answer.lower()
    assert "запрос:" in first.answer.lower()
    assert "p***@example.com" in first.answer
    assert second.answer != first.answer
    assert "новых данных" in second.answer.lower()


def test_prepare_request_wording_composes_catalog_and_handoff() -> None:
    bot = ChatOrchestrator(
        products=[_radiator(1), _radiator(2), _radiator(3), _radiator(4)]
    )

    response = bot.handle_chat(
        "architecture-compound-preview",
        "Покажите три биметаллических радиатора в наличии с артикулами и "
        "ценами и одновременно подготовьте запрос менеджеру. Мой email "
        "test-buyer@example.com. Сначала покажите preview, отправлять можно "
        "только после моего подтверждения.",
    )

    state = bot.sessions.get("architecture-compound-preview")
    assert len(response.products) == 3
    assert response.need_handoff is True
    assert state.handoff_status == "awaiting_consent"
    assert state.pending_handoff is not None
    assert state.pending_handoff["products_considered"] == [
        product.sku for product in response.products
    ]
    assert state.pending_handoff["contact"] == "test-buyer@example.com"
    assert "подтверд" in response.answer.lower()


def test_product_delivery_and_discount_are_all_covered() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])

    response = bot.handle_chat(
        "architecture-product-delivery-discount",
        "Покажите один биметаллический радиатор в наличии и сразу объясните, "
        "сколько он стоит, как считается доставка и бывает ли скидка.",
    )

    normalized = response.answer.lower()
    assert len(response.products) == 1
    assert "достав" in normalized
    assert "скидк" in normalized
    assert str(int(response.products[0].price)) in response.answer


def test_link_word_does_not_erase_requested_product_attributes() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2), _radiator(3)])
    session_id = "architecture-link-plus-attributes"
    first = bot.handle_chat(
        session_id,
        "Покажите три популярных биметаллических радиатора с ценами.",
    )
    assert len(first.products) == 3

    second = bot.handle_chat(
        session_id,
        "Дайте ссылки и укажите по каждому высоту, ширину, мощность и "
        "совместимость с антифризом. Если данных нет, скажите прямо.",
    )

    normalized = second.answer.lower()
    assert "ссылка" in normalized
    assert "высот" in normalized
    assert "ширин" in normalized
    assert "мощност" in normalized or "теплоотдач" in normalized
    assert "антифриз" in normalized or "теплоносител" in normalized
    assert "в карточке не указано" in normalized


def test_industrial_steam_valve_keeps_hard_product_type() -> None:
    valve = Product(
        sku="STEAM-VALVE-DN50",
        name="Вентиль стальной запорный Ду50",
        category_path="Запорная арматура / Вентили",
        brand="TEST-INDUSTRIAL",
        url="https://example.test/steam-valve-dn50",
        price=42_000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={
            "тип товара": "вентиль запорный",
            "рабочая среда": "пар",
            "условный проход, мм": "50",
            "максимальная рабочая температура, °C": "200",
            "максимальное рабочее давление, бар": "16",
        },
    )
    pipe = Product(
        sku="PIPE-DN50",
        name="Труба полипропиленовая PN20 50 мм",
        category_path="Трубы полипропиленовые",
        brand="TEST-PIPE",
        url="https://example.test/pipe-dn50",
        price=700,
        stock_status="в наличии",
        stock_qty=20,
        attributes_normalized={"тип товара": "труба"},
    )
    bot = ChatOrchestrator(products=[pipe, valve])

    response = bot.handle_chat(
        "architecture-industrial-valve",
        "Подберите промышленную запорную арматуру Ду50 на пар 180 градусов "
        "и 10 бар. Нужен вентиль, не труба.",
    )

    assert response.debug["category"] == "valves"
    assert [product.sku for product in response.products] == [valve.sku]
    assert pipe.sku not in response.answer


def test_industrial_valve_without_match_never_falls_back_to_pipe() -> None:
    pipe = Product(
        sku="PIPE-ONLY-DN50",
        name="Труба PN20 50 мм",
        category_path="Трубы",
        url="https://example.test/pipe-only-dn50",
        price=700,
        stock_status="в наличии",
        stock_qty=20,
    )
    bot = ChatOrchestrator(products=[pipe])

    response = bot.handle_chat(
        "architecture-industrial-valve-no-match",
        "Ищу вентиль Ду50 для насыщенного пара 180 °C при 10 bar. "
        "Трубы и бытовые краны не предлагать.",
    )

    assert response.debug["category"] == "valves"
    assert response.products == []
    assert pipe.sku not in response.answer
    normalized = response.answer.lower()
    assert "вентил" in normalized or "арматур" in normalized
    assert "размер в дюймах" not in normalized


def test_repeated_industrial_no_match_progresses_instead_of_looping() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-industrial-no-match-repeat"

    first = bot.handle_chat(
        session_id,
        "Нужен промышленный вентиль DN50 на пар 175 °C и 9 бар.",
    )
    second = bot.handle_chat(
        session_id,
        "Так он есть в каталоге или нет? Проверьте ещё раз те же условия.",
    )

    assert first.products == second.products == []
    assert second.answer != first.answer
    assert "повторно проверил" in second.answer.lower()
    assert "подтвердить не могу" in second.answer.lower()


def test_delivery_city_is_extracted_and_remembered_outside_branch_cities() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-delivery-city"

    first = bot.handle_chat(
        session_id,
        "Сколько примерно стоит доставка радиаторов в Краснодар и от чего "
        "зависит сумма? А скидки на большой заказ есть?",
    )
    second = bot.handle_chat(
        session_id,
        "Краснодар, 15-й этаж. Так как считается доставка и скидка?",
    )

    for response in (first, second):
        normalized = response.answer.lower()
        assert "краснодар" in normalized
        assert "скажите город" not in normalized
        assert "скидк" in normalized


def test_industrial_armature_routes_to_valves_before_pipe_landmark() -> None:
    routed = IntentRouterAgent().route(
        "Нужна запорная арматура на паровую трубу: Ду50, 180 °C, 10 бар."
    )

    assert routed.category == "valves"
    assert routed.slots["nominal_diameter_dn"] == 50
    assert routed.slots["application"] == "пар"


def test_spoken_battery_noun_and_count_execute_catalogue_browse() -> None:
    bot = ChatOrchestrator(
        products=[_radiator(1), _radiator(2), _radiator(3), _radiator(4)]
    )

    response = bot.handle_chat(
        "architecture-spoken-battery",
        "Дайте три биметаллические батареи из наличия с ценами.",
    )

    assert len(response.products) == 3
    assert response.debug["selection_mode"] == "browse"


def test_handoff_artifact_and_recipient_can_appear_in_either_order() -> None:
    for index, wording in enumerate(
        [
            "Соберите обращение продавцу по радиаторам.",
            "Подготовьте менеджеру вопрос по радиаторам.",
        ]
    ):
        bot = ChatOrchestrator(products=[])
        response = bot.handle_chat(f"architecture-handoff-order-{index}", wording)

        assert response.need_handoff is True
        assert response.handoff_status == "awaiting_contact"
        assert "оставьте телефон или email" in response.answer.lower()


def test_awaiting_contact_repeat_ladder_is_independent_from_other_repeats() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "architecture-awaiting-contact-ladder"
    first = bot.handle_chat(
        session_id,
        "Подготовьте менеджеру вопрос по точной цене партии труб.",
    )
    state = bot.sessions.get(session_id)
    assert state.handoff_status == "awaiting_contact"
    # A previous catalogue loop must not skip or collapse the handoff-specific
    # ladder; the counters represent different workflow states.
    state.slots["_answer_repeat_strikes"] = 4
    bot.sessions.save(state)

    second = bot.handle_chat(session_id, "Передайте этот вопрос менеджеру.")
    third = bot.handle_chat(session_id, "Передайте этот вопрос менеджеру.")
    fourth = bot.handle_chat(session_id, "Передайте этот вопрос менеджеру.")

    assert len({first.answer, second.answer, third.answer, fourth.answer}) == 4
    assert "контакта покупателя" in third.answer.lower()
    assert "ставлю на паузу" in fourth.answer.lower()


def test_postfixed_third_party_role_does_not_hide_owned_email() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "architecture-contact-postfix",
        "Подготовьте менеджеру вопрос. factory.docs@example.org напечатан в "
        "инструкции и принадлежит заводу, это не я; отвечать мне можно на "
        "procurement.wave@example.com. Покажите итог до отправки.",
    )

    state = bot.sessions.get("architecture-contact-postfix")
    assert response.handoff_status == "awaiting_consent"
    assert state.contact == "procurement.wave@example.com"
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == "procurement.wave@example.com"
    assert "factory.docs@example.org" not in str(state.pending_handoff)


def test_cross_script_brand_and_full_catalogue_name_resolve_exact_model() -> None:
    pipe = Product(
        sku="REHAU-CYR-FLEX-20",
        name="Труба универсальная 20х2,8 мм РЕХАУ FLEX, бухта 100м",
        category_path="Трубы",
        brand="РЕХАУ",
        url="https://example.test/rehau-flex",
        price=273,
        stock_status="в наличии",
        stock_qty=36,
        attributes_normalized={
            "полное наименование": (
                "Труба PEX универсальная 20х2,8 мм Rehau RAUTITAN Flex, "
                "бухта 100м"
            )
        },
    )
    bot = ChatOrchestrator(products=[pipe])

    response = bot.handle_chat(
        "architecture-cross-script-model",
        "Нужна труба REHAU RAUTITAN flex 20×2,8, общий метраж двести. "
        "Сколько стоит и какие "
        "условия для такого объёма?",
    )

    assert [product.sku for product in response.products] == [pipe.sku]
    assert "273" in response.answer
    assert "RAUTITAN Flex" in response.answer
    assert "Общий метраж 200 м" in response.answer
    assert "2 бухт" in response.answer
    assert "не умножаю" in response.answer
    assert "объём" in response.answer.lower() or "скидк" in response.answer.lower()

    followup = bot.handle_chat(
        "architecture-cross-script-model",
        "Нет, именно RAUTITAN Flex: назовите цену за двести метров, а не за "
        "одну бухту.",
    )
    assert "Запрошенное обозначение «rautitan flex»" in followup.answer
    assert "Общий метраж 200 м" in followup.answer
    assert "не умножаю" in followup.answer


def test_spoken_total_length_is_parsed_only_with_quantity_context() -> None:
    assert extract_total_length_m("общий метраж двести") == 200
    assert extract_total_length_m("нужно двести метров трубы") == 200
    assert extract_total_length_m("бюджет двести") is None


def test_named_shown_card_is_resolved_and_water_only_excludes_antifreeze() -> None:
    products = [
        Product(
            sku="GEKON-6",
            name="Радиатор биметаллический Gekon BM 500 6 секций",
            category_path="Радиаторы / Биметаллические",
            price=4990,
            stock_status="в наличии",
            stock_qty=5,
            url="https://example.test/gekon-6",
            attributes_normalized={"теплоотдача, Вт": "1038", "высота, мм": "570"},
        ),
        Product(
            sku="RIFAR-BASE-8",
            name="Радиатор биметаллический Rifar Base BVL 500 8 секций",
            category_path="Радиаторы / Биметаллические",
            price=11438,
            stock_status="в наличии",
            stock_qty=2,
            url="https://example.test/rifar-base-8",
            attributes_normalized={"теплоотдача, Вт": "1576", "высота, мм": "570"},
            description=(
                "В качестве теплоносителя допускается использование только "
                "специально подготовленной воды."
            ),
        ),
        Product(
            sku="RIFAR-MONOLIT-10",
            name="Радиатор биметаллический Rifar Monolit 500 10 секций",
            category_path="Радиаторы / Биметаллические",
            price=13830,
            stock_status="в наличии",
            stock_qty=1,
            url="https://example.test/rifar-monolit-10",
            attributes_normalized={"теплоотдача, Вт": "1960", "высота, мм": "577"},
        ),
    ]
    bot = ChatOrchestrator(products=products)
    session_id = "architecture-card-name-coolant"
    shown = bot.handle_chat(
        session_id,
        "Покажите три биметаллических радиатора в наличии.",
    )
    assert len(shown.products) == 3

    comparison = bot.handle_chat(
        session_id,
        "Сравните их по высоте, теплоотдаче и допустимости антифриза.",
    )
    assert all(sku in comparison.answer for sku in ["GEKON-6", "RIFAR-BASE-8", "RIFAR-MONOLIT-10"])
    assert "в карточке не указано" in comparison.answer.lower()

    focused = bot.handle_chat(
        session_id,
        "Так Rifar Base BVL можно использовать с антифризом?",
    )
    assert [card.sku for card in focused.products] == ["RIFAR-BASE-8"]
    assert "считать незамерзающий теплоноситель разрешённым нельзя" in focused.answer.lower()


def test_named_zero_stock_reason_and_available_analogs_are_answered_together() -> None:
    available_one = _radiator(1, sections=12)
    available_two = _radiator(2, sections=12)
    unavailable = Product(
        sku="ROMMER-ZERO-12",
        name="Радиатор биметаллический Rommer Optima 500 12 секций",
        category_path="Радиаторы / Биметаллические",
        price=6099,
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/rommer-zero",
        attributes_normalized={
            "межосевое расстояние, мм": "500",
            "количество секций": "12",
        },
    )
    bot = ChatOrchestrator(products=[available_one, available_two, unavailable])
    session_id = "architecture-zero-stock-reason"
    shown = bot.handle_chat(
        session_id,
        "Покажите три биметаллических радиатора на 12 секций, включая "
        "отсутствующие.",
    )
    assert len(shown.products) == 3

    explained = bot.handle_chat(
        session_id,
        "Почему Rommer Optima нет в наличии и какие из показанных есть как аналоги?",
    )

    assert "подтверждён остаток 0 шт" in explained.answer.lower()
    assert "фид не указывает причину" in explained.answer.lower()
    assert {card.sku for card in explained.products} == {
        available_one.sku,
        available_two.sku,
    }


def test_choose_any_from_shown_set_returns_one_card() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2), _radiator(3)])
    session_id = "architecture-choose-any"
    shown = bot.handle_chat(session_id, "Покажите три батареи в наличии.")
    assert len(shown.products) == 3

    chosen = bot.handle_chat(session_id, "Выбирай любой из них, но один.")

    assert len(chosen.products) == 1
    assert chosen.products[0].sku in {card.sku for card in shown.products}


def test_two_pipe_word_does_not_become_radiator_element_type() -> None:
    routed = IntentRouterAgent().route(
        "Комната 18 м², система двухтрубная, давление 1,5 бар, "
        "12 секций, межосевое 500 мм.",
        SessionState(session_id="architecture-two-pipe", category="radiators"),
    )

    assert routed.category == "radiators"
    assert "element_type" not in routed.slots
    assert routed.slots["sections"] == 12


def test_reversed_heating_system_word_order_is_understood() -> None:
    for wording, expected in [
        ("Система центральная", "центральное"),
        ("У меня автономная система", "автономное"),
    ]:
        routed = IntentRouterAgent().route(
            wording,
            SessionState(
                session_id=f"architecture-system-{expected}",
                category="radiators",
            ),
        )
        assert routed.slots["heating_system_type"] == expected


def test_novice_disclaimer_does_not_turn_product_selection_into_glossary() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])

    response = bot.handle_chat(
        "architecture-novice-selection",
        "Я в сантехнике ничего не понимаю. Помогите выбрать биметаллическую "
        "батарею для комнаты.",
    )

    normalized = response.answer.lower()
    assert response.debug["category"] == "radiators"
    assert "точное значение этого термина" not in normalized
    assert "уточн" in normalized or "систем" in normalized


def test_recommendation_and_commerce_policies_do_not_short_circuit_each_other() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])

    response = bot.handle_chat(
        "architecture-recommend-commerce",
        "Посоветуйте одну биметаллическую батарею и сразу объясните доставку "
        "до Краснодара и скидку за несколько штук.",
    )

    normalized = response.answer.lower()
    assert response.debug["category"] == "radiators"
    assert "достав" in normalized and "краснодар" in normalized
    assert "скидк" in normalized
    assert "совместим" in normalized
