from __future__ import annotations

import re

import pytest

from app.agents.commerce_topics import (
    compose_discount_supplement,
    compose_store_contact_answer,
)
from app.agents.orchestrator import ChatOrchestrator
from app.agents.intent_router import IntentRouterAgent
from app.agents.turn_planner import (
    ContactDirection,
    SelectionMode,
    TurnAct,
    TurnAction,
    TurnPlanner,
)
from app.business_config import Branch, BusinessFacts
from app.models import Product


def test_a01_frame_parses_long_browse_request_with_count_and_price() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Мне вообще не нужны технические детали — просто скажите, какие "
        "радиаторы чаще берут в обычных квартирах. Дайте 2–3 модели с ценой."
    )
    plan = planner.plan(frame)

    assert frame.acts == (TurnAct.BROWSE_OPTIONS, TurnAct.PRICE_LOOKUP)
    assert frame.selection_mode == SelectionMode.BROWSE
    assert frame.requested_count == 3
    assert plan.actions == (TurnAction.CATALOG_BROWSE, TurnAction.CATALOG_PRICE)
    assert plan.bypass_engineering_preflight is True
    assert plan.skip_commerce_short_circuit is False


def test_recommendation_does_not_bypass_engineering_requirements() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Помогите подобрать радиатор, который точно подойдет для моей квартиры."
    )
    plan = planner.plan(frame)

    assert frame.selection_mode == SelectionMode.RECOMMEND
    assert TurnAct.BROWSE_OPTIONS not in frame.acts
    assert plan.bypass_engineering_preflight is False


def test_recommendation_with_price_still_requires_engineering_inputs() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Подберите радиатор, который точно подойдет для моей квартиры, и назовите цену."
    )
    plan = planner.plan(frame)

    assert frame.selection_mode == SelectionMode.RECOMMEND
    assert TurnAct.PRICE_LOOKUP in frame.acts
    assert plan.bypass_engineering_preflight is False


def test_recommendation_with_count_is_not_downgraded_to_catalogue_browse() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Подберите 3 модели радиаторов, которые точно подойдут для моей квартиры."
    )
    plan = planner.plan(frame)

    assert frame.requested_count == 3
    assert frame.selection_mode == SelectionMode.RECOMMEND
    assert TurnAct.BROWSE_OPTIONS not in frame.acts
    assert TurnAction.CATALOG_BROWSE not in plan.actions
    assert plan.bypass_engineering_preflight is False


def test_central_heating_answer_is_not_mistaken_for_price() -> None:
    planner = TurnPlanner()

    frame = planner.frame("Центральная.")
    plan = planner.plan(frame)

    assert TurnAct.PRICE_LOOKUP not in frame.acts
    assert frame.selection_mode == SelectionMode.UNSPECIFIED
    assert TurnAction.CATALOG_PRICE not in plan.actions


def test_rich_product_refinement_is_not_reduced_to_generic_browse() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Старый насос Grundfos UPS 25-60, нужна более дешевая альтернатива. "
        "Покажи варианты в наличии с ценой и ссылкой."
    )
    plan = planner.plan(frame)

    assert TurnAct.BROWSE_OPTIONS not in frame.acts
    assert TurnAction.CATALOG_BROWSE not in plan.actions


def test_a03_plan_orders_catalog_price_before_discount_policy() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Нужно 200 метров трубы Rehau Rautitan Flex 20х2,8 — "
        "сколько за это и какая скидка?"
    )
    plan = planner.plan(frame)

    assert frame.acts == (TurnAct.PRICE_LOOKUP, TurnAct.DISCOUNT_POLICY)
    assert frame.selection_mode == SelectionMode.BROWSE
    assert plan.actions == (
        TurnAction.CATALOG_PRICE,
        TurnAction.ANSWER_DISCOUNT_POLICY,
    )
    assert plan.bypass_engineering_preflight is True
    assert plan.skip_commerce_short_circuit is True


def test_b20_store_contact_is_not_customer_contact_or_handoff() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Отправь мне телефон или email — я передам запрос менеджеру в Самаре "
        "и спрошу точный остаток по каждому артикулу."
    )
    plan = planner.plan(frame, pending_handoff=True)

    assert frame.acts == (TurnAct.REQUEST_STORE_CONTACT,)
    assert frame.contact_direction == ContactDirection.STORE_TO_CUSTOMER
    assert frame.customer_contact_present is False
    assert plan.actions == (TurnAction.ANSWER_STORE_CONTACT,)
    assert plan.ignore_pending_handoff_for_turn is True
    assert plan.bypass_engineering_preflight is True


def test_store_contact_question_does_not_require_the_word_phone() -> None:
    planner = TurnPlanner()

    frame = planner.frame("Как связаться с менеджером в Самаре?")

    assert frame.acts == (TurnAct.REQUEST_STORE_CONTACT,)
    assert frame.contact_direction == ContactDirection.STORE_TO_CUSTOMER


def test_manufacturer_contact_is_not_relabelled_as_store_contact() -> None:
    planner = TurnPlanner()

    frame = planner.frame("Как связаться с производителем Rehau?")
    plan = planner.plan(frame)

    assert TurnAct.REQUEST_STORE_CONTACT not in frame.acts
    assert frame.acts == (TurnAct.REQUEST_THIRD_PARTY_CONTACT,)
    assert frame.contact_direction == ContactDirection.THIRD_PARTY
    assert plan.actions == (TurnAction.ANSWER_THIRD_PARTY_CONTACT,)


@pytest.mark.parametrize(
    "message",
    [
        "Как связаться с производителем Rehau?",
        "Подскажите мне телефон производителя Rehau",
        "Куда позвонить производителю Rehau?",
    ],
)
def test_third_party_contact_questions_have_one_direction(message: str) -> None:
    frame = TurnPlanner().frame(message)

    assert frame.acts == (TurnAct.REQUEST_THIRD_PARTY_CONTACT,)
    assert frame.contact_direction == ContactDirection.THIRD_PARTY


def test_actual_customer_contact_keeps_handoff_direction() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Передай менеджеру, мой телефон указан в сообщении.",
        customer_contact_present=True,
    )
    plan = planner.plan(frame, pending_handoff=True)

    assert TurnAct.PROVIDE_CUSTOMER_CONTACT in frame.acts
    assert TurnAct.REQUEST_HANDOFF in frame.acts
    assert TurnAct.REQUEST_STORE_CONTACT not in frame.acts
    assert frame.contact_direction == ContactDirection.CUSTOMER_TO_STORE
    assert plan.actions == (TurnAction.CONTINUE_HANDOFF,)
    assert plan.ignore_pending_handoff_for_turn is False


def test_customer_contact_without_pending_or_explicit_handoff_stays_local() -> None:
    planner = TurnPlanner()

    frame = planner.frame(
        "Мой телефон уже здесь, покажи 3 модели с ценой.",
        customer_contact_present=True,
    )
    plan = planner.plan(frame, pending_handoff=False)

    assert TurnAct.PROVIDE_CUSTOMER_CONTACT in frame.acts
    assert TurnAct.REQUEST_HANDOFF not in frame.acts
    assert TurnAction.CONTINUE_HANDOFF not in plan.actions


def _business_facts() -> BusinessFacts:
    return BusinessFacts(
        branches=(
            Branch(
                region="samara",
                city="Самара",
                address="Самара, Тестовая улица, 1",
                phones=("+7 (846) 000-00-01",),
                hours="Пн-Пт: 9-18",
            ),
            Branch(
                region="moscow",
                city="Подольск",
                address="Подольск, Тестовая улица, 2",
                phones=("+7 (495) 000-00-02",),
                hours="Пн-Пт: 9-18",
            ),
        ),
        emails=("shop@example.test",),
        site_url="https://example.test",
    )


def test_store_contact_answer_uses_only_requested_city_facts() -> None:
    answer = compose_store_contact_answer(_business_facts(), city="Самара")

    assert "Самара" in answer
    assert "+7 (846) 000-00-01" in answer
    assert "shop@example.test" in answer
    assert "+7 (495) 000-00-02" not in answer
    assert "Подольск" not in answer


def test_store_contact_answer_asks_for_city_when_branch_is_unknown() -> None:
    answer = compose_store_contact_answer(_business_facts())

    assert "назовите ваш" in answer.lower()
    assert "Самара" in answer
    assert "Подольск" in answer


def test_discount_supplement_never_invents_a_percentage() -> None:
    answer = compose_discount_supplement()

    assert "скидк" in answer.lower()
    assert "менеджер" in answer.lower()
    assert not re.search(r"\b\d+(?:[,.]\d+)?\s*%", answer)


def _radiator(index: int) -> Product:
    return Product(
        sku=f"RAD-500-{index}",
        name=f"Радиатор биметаллический 500 мм {index + 5} секций",
        category_path="Радиаторы отопления",
        brand="TEST-RAD",
        url=f"https://example.test/radiator-{index}",
        price=10_000 + index * 1_000,
        stock_status="в наличии",
        stock_qty=10 + index,
        attributes_normalized={
            "межосевое расстояние, мм": "500",
            "количество секций": str(index + 5),
            "материал": "биметалл",
        },
    )


def test_a01_browse_escapes_clarification_and_returns_requested_cards() -> None:
    products = [
        _radiator(1),
        _radiator(2),
        _radiator(3),
        Product(
            sku="BOILER-SENTINEL",
            name="Котёл электрический Sentinel 9 кВт",
            category_path="Котлы электрические",
            url="https://example.test/boiler",
            price=99_000,
            stock_status="в наличии",
            stock_qty=1,
        ),
    ]
    bot = ChatOrchestrator(products=products)
    session_id = "planner-a01"

    first = bot.handle_chat(
        session_id,
        "Помогите подобрать радиатор, который точно подойдёт для моей квартиры.",
    )
    second = bot.handle_chat(
        session_id,
        "Мне вообще не нужны технические детали — скажите, какие радиаторы "
        "чаще берут. Дайте 2–3 модели с ценой.",
    )

    assert first.products == []
    assert 2 <= len(second.products) <= 3
    assert all(product.sku.startswith("RAD-") for product in second.products)
    assert all(product.price > 0 for product in second.products)
    assert second.need_handoff is False
    assert "Показываю по тому, что уже известно" in second.answer
    assert "не буду подставлять случайный товар" not in second.answer.lower()
    assert second.debug["turn_actions"][0] == TurnAction.CATALOG_BROWSE.value
    state = bot.sessions.get(session_id)
    assert state.pending_question_state is None
    assert state.pending_question is None


def test_a01_exact_live_wording_keeps_area_separate_then_allows_browse() -> None:
    products = [_radiator(1), _radiator(2), _radiator(3)]
    bot = ChatOrchestrator(products=products)
    session_id = "planner-a01-exact"

    opening = (
        "Привет, мне нужны радиаторы для квартиры в Москве, 70 кв.м., на "
        "2-м этаже. Уже есть батареи от старой системы — не знаю, что "
        "подойдёт лучше. Дай 2–3 модели с ценой."
    )
    routed = IntentRouterAgent().route(opening)
    first = bot.handle_chat(session_id, opening)
    second = bot.handle_chat(
        session_id,
        "Ну ты же продавец, а не бот. Не надо уточнять параметры — просто "
        "дай 2–3 модели с ценой, я не разбираюсь в теме.",
    )

    assert routed.slots["area_m2"] == 70.0
    assert "radiator_size_mm" not in routed.slots
    assert first.products == []
    assert "центральная или автономная" in first.answer.lower()
    assert 2 <= len(second.products) <= 3
    assert all(product.sku.startswith("RAD-") for product in second.products)
    assert second.need_handoff is False
    assert bot.sessions.get(session_id).pending_handoff is None


def test_a01_central_heating_followup_continues_compatibility_questions() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])
    session_id = "planner-a01-central"

    first = bot.handle_chat(
        session_id,
        "Подберите радиаторы, которые точно подойдут для квартиры 70 кв.м.",
    )
    second = bot.handle_chat(session_id, "Центральная.")

    assert first.products == []
    assert second.products == []
    assert "тип радиатора" in second.answer.lower()
    assert second.debug["slots"]["heating_system_type"] == "центральное"
    assert second.debug["turn_actions"] == []
    assert bot.sessions.get(session_id).pending_selection_mode == "recommend"


def test_a03_price_and_discount_are_composed_in_one_catalogue_answer() -> None:
    pipe = Product(
        sku="REHAU-FLEX-20",
        name="Труба Rehau Rautitan Flex 20х2,8",
        category_path="Трубы из сшитого полиэтилена",
        brand="REHAU",
        url="https://example.test/rehau-flex-20",
        price=550,
        stock_status="в наличии",
        stock_qty=8,
        attributes_normalized={
            "тип товара": "труба",
            "материал": "сшитый полиэтилен",
            "диаметр, мм": "20",
            "толщина стенки, мм": "2,8",
            "длина бухты": "100 м",
        },
    )
    bot = ChatOrchestrator(products=[pipe])

    response = bot.handle_chat(
        "planner-a03",
        "Нужно 200 метров трубы Rehau Rautitan Flex 20х2,8 — "
        "сколько за это и какая скидка?",
    )

    assert [product.sku for product in response.products] == [pipe.sku]
    assert "550" in response.answer
    assert "скидк" in response.answer.lower()
    assert "менеджер" in response.answer.lower()
    assert not re.search(r"\b\d+(?:[,.]\d+)?\s*%", response.answer)
    assert response.need_handoff is False
    assert response.debug["turn_actions"] == [
        TurnAction.CATALOG_PRICE.value,
        TurnAction.ANSWER_DISCOUNT_POLICY.value,
    ]
    state = bot.sessions.get("planner-a03")
    assert state.pending_handoff is None


def test_a03_generic_live_opening_keeps_discount_policy_and_asks_for_scope() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-a03-generic",
        "Привет, мне нужно рассчитать цену на сантехнику под конкретный объём "
        "— скажите, как это сделать и какие скидки доступны?",
    )

    normalized = response.answer.lower().replace("ё", "е")
    assert "скидк" in normalized
    assert "состав" in normalized
    assert "объем" in normalized
    assert "менеджер" in normalized
    assert not re.search(r"\b\d+(?:[,.]\d+)?\s*%", response.answer)
    assert "с чего начнем" not in normalized
    assert response.need_handoff is False
    assert bot.sessions.get("planner-a03-generic").pending_handoff is None


def test_compound_browse_and_handoff_executes_both_actions() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2), _radiator(3)])

    response = bot.handle_chat(
        "planner-compound-handoff",
        "Покажи 3 модели радиаторов и передай менеджеру.",
    )

    assert len(response.products) == 3
    assert response.need_handoff is True
    assert "оставьте телефон или email" in response.answer.lower()
    assert response.debug["turn_actions"] == [
        TurnAction.CATALOG_BROWSE.value,
        TurnAction.CONTINUE_HANDOFF.value,
    ]
    state = bot.sessions.get("planner-compound-handoff")
    assert state.handoff_status == "awaiting_contact"
    assert state.pending_handoff is not None
    assert state.pending_handoff["products_considered"] == [
        "RAD-500-1",
        "RAD-500-2",
        "RAD-500-3",
    ]
    assert "покажи 3 модели радиаторов" in state.pending_handoff["wanted"].lower()


def test_compound_price_and_handoff_never_loses_explicit_transfer() -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])

    response = bot.handle_chat(
        "planner-price-handoff",
        "Сколько стоит радиатор? Передай менеджеру.",
    )

    assert response.need_handoff is True
    assert "оставьте телефон или email" in response.answer.lower()
    assert response.debug["turn_actions"] == [
        TurnAction.CATALOG_PRICE.value,
        TurnAction.CONTINUE_HANDOFF.value,
    ]
    state = bot.sessions.get("planner-price-handoff")
    assert state.handoff_status == "awaiting_contact"
    assert state.pending_handoff is not None


def test_legacy_can_manager_wording_survives_compound_browse() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2), _radiator(3)])

    response = bot.handle_chat(
        "planner-legacy-compound-handoff",
        "Покажи 3 модели радиаторов. Можно менеджера?",
    )

    assert len(response.products) == 3
    assert response.need_handoff is True
    assert bot.sessions.get(
        "planner-legacy-compound-handoff"
    ).handoff_status == "awaiting_contact"


def test_contact_in_price_request_does_not_create_unsolicited_handoff() -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])

    response = bot.handle_chat(
        "planner-contact-with-price",
        "Мой телефон +7 999 111-22-33. Сколько стоит этот радиатор?",
    )

    assert response.need_handoff is False
    state = bot.sessions.get("planner-contact-with-price")
    assert state.contact is not None
    assert state.pending_handoff is None
    assert TurnAction.CONTINUE_HANDOFF.value not in response.debug["turn_actions"]


def test_repeated_compound_handoff_rebuilds_preview_with_new_products() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2), _radiator(3)])
    session_id = "planner-updated-handoff"

    bot.handle_chat(session_id, "Передай менеджеру запрос по котлу.")
    before = dict(bot.sessions.get(session_id).pending_handoff or {})
    response = bot.handle_chat(
        session_id,
        "Покажи 3 модели радиаторов и передай менеджеру.",
    )
    after = bot.sessions.get(session_id).pending_handoff or {}

    assert response.need_handoff is True
    assert len(response.products) == 3
    assert after != before
    assert after["products_considered"] == [
        "RAD-500-1",
        "RAD-500-2",
        "RAD-500-3",
    ]
    assert "радиатор" in after["wanted"].lower()


def test_b20_store_contact_does_not_advance_pending_customer_handoff() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "planner-b20"
    bot.handle_chat(
        session_id,
        "Передай менеджеру запрос по точным остаткам кранов в Самаре.",
    )
    before = bot.sessions.get(session_id)
    assert before.handoff_status == "awaiting_contact"
    pending_before = dict(before.pending_handoff or {})

    response = bot.handle_chat(
        session_id,
        "Отправь мне телефон или email — я передам запрос менеджеру в Самаре "
        "и спрошу точный остаток по каждому артикулу.",
    )

    assert response.need_handoff is False
    assert "+7 (846)" in response.answer
    assert "для неё нужен телефон" not in response.answer.lower()
    assert "оставьте телефон" not in response.answer.lower()
    assert response.debug["contact_direction"] == "store_to_customer"
    after = bot.sessions.get(session_id)
    assert after.handoff_status == "awaiting_contact"
    assert after.pending_handoff == pending_before
    assert after.contact is None


def test_manufacturer_contact_request_uses_third_party_boundary() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-third-party-request",
        "Как связаться с производителем Rehau?",
    )

    normalized = response.answer.lower().replace("ё", "е")
    assert "производител" in normalized
    assert "официальн" in normalized
    assert "оставьте телефон" not in normalized
    assert response.need_handoff is False
    assert response.debug["contact_direction"] == "third_party"
    state = bot.sessions.get("planner-third-party-request")
    assert state.contact is None
    assert state.pending_handoff is None


def test_third_party_email_never_becomes_customer_contact_in_new_handoff() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-third-party-new-handoff",
        "Передай менеджеру вопрос по радиатору; email производителя "
        "support@rehau.example.",
    )

    state = bot.sessions.get("planner-third-party-new-handoff")
    assert response.need_handoff is True
    assert response.debug["contact_direction"] == "third_party"
    assert state.contact is None
    assert state.handoff_status == "awaiting_contact"
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] is None
    assert "оставьте телефон или email" in response.answer.lower()


def test_third_party_email_does_not_advance_pending_handoff() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "planner-third-party-pending"
    bot.handle_chat(session_id, "Передай менеджеру вопрос по радиатору.")
    before = dict(bot.sessions.get(session_id).pending_handoff or {})

    response = bot.handle_chat(
        session_id,
        "Email производителя support@rehau.example.",
    )

    state = bot.sessions.get(session_id)
    assert response.debug["contact_direction"] == "third_party"
    assert state.contact is None
    assert state.handoff_status == "awaiting_contact"
    assert state.pending_handoff == before
    assert state.pending_handoff["contact"] is None
    assert response.need_handoff is True
    assert "не считаю его вашим" in response.answer.lower()
    assert "именно ваш" in response.answer.lower()


def test_pending_handoff_contact_and_browse_returns_cards_and_one_preview() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])
    session_id = "planner-pending-contact-browse"
    bot.handle_chat(session_id, "Передай менеджеру запрос по радиаторам.")

    response = bot.handle_chat(
        session_id,
        "Мой email buyer@example.com. Покажи 1 модель радиатора.",
    )

    state = bot.sessions.get(session_id)
    assert len(response.products) == 1
    assert response.need_handoff is True
    assert response.answer.count("Заявку менеджеру пока не отправляю.") == 1
    assert "точной позиции" not in response.answer.lower()
    assert state.handoff_status == "awaiting_consent"
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == "buyer@example.com"
    assert state.pending_handoff["products_considered"] == [response.products[0].sku]


@pytest.mark.parametrize(
    "message",
    [
        "Не надо уточнять город доставки радиатора, просто скажите стоимость.",
        "Не хочу уточнять адрес доставки.",
        "Без уточнений скажите сроки доставки.",
    ],
)
def test_delivery_questions_do_not_trigger_catalogue_browse(message: str) -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])

    response = bot.handle_chat(f"planner-delivery-{abs(hash(message))}", message)

    assert response.products == []
    assert "достав" in response.answer.lower()
    assert TurnAction.CATALOG_BROWSE.value not in response.debug["turn_actions"]


def test_delivery_and_discount_are_answered_in_the_same_turn() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-delivery-discount",
        "Сколько стоит доставка и какая скидка?",
    )

    normalized = response.answer.lower()
    assert "достав" in normalized
    assert "скидк" in normalized
    assert response.products == []


def test_generic_price_discount_does_not_reuse_stale_product_category() -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])
    session_id = "planner-stale-category-commerce"
    state = bot.sessions.get(session_id)
    state.category = "radiators"
    bot.sessions.save(state)

    response = bot.handle_chat(
        session_id,
        "Привет, мне нужно рассчитать цену на сантехнику под конкретный объём "
        "— скажите, как это сделать и какие скидки доступны?",
    )

    normalized = response.answer.lower().replace("ё", "е")
    assert response.products == []
    assert "скидк" in normalized
    assert "состав" in normalized
    assert "объем" in normalized
    assert "rad-" not in normalized
    assert "11000" not in normalized


def test_budget_continues_active_radiator_recommendation() -> None:
    bot = ChatOrchestrator(products=[_radiator(0), _radiator(1)])
    session_id = "planner-recommend-budget"
    first = bot.handle_chat(
        session_id,
        "Помогите подобрать радиатор, который точно подойдет для квартиры.",
    )

    second = bot.handle_chat(
        session_id,
        "Центральное отопление, биметаллический, цена до 10000.",
    )

    assert first.products == []
    assert second.products == []
    assert second.debug["slots"]["heating_system_type"] == "центральное"
    assert second.debug["slots"]["radiator_type"] == "биметаллический"
    assert second.debug["slots"]["max_price"] == 10000.0
    assert second.debug["selection_mode"] == SelectionMode.RECOMMEND.value
    assert bot.sessions.get(session_id).pending_selection_mode == "recommend"


def test_safety_side_question_does_not_cancel_active_recommendation() -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])
    session_id = "planner-recommend-safety"
    bot.handle_chat(
        session_id,
        "Помогите подобрать радиатор, который точно подойдет для квартиры.",
    )

    response = bot.handle_chat(
        session_id,
        "Сколько стоит самому подключить газовый котел?",
    )

    normalized = response.answer.lower().replace("ё", "е")
    assert "газ" in normalized
    assert "специалист" in normalized or "служб" in normalized
    assert bot.sessions.get(session_id).pending_selection_mode == "recommend"


def test_exact_sku_price_and_discount_keeps_grounded_card() -> None:
    product = _radiator(1)
    bot = ChatOrchestrator(products=[product])

    response = bot.handle_chat(
        "planner-exact-sku-discount",
        f"Сколько стоит {product.sku} и какая скидка?",
    )

    assert [card.sku for card in response.products] == [product.sku]
    assert str(int(product.price)) in response.answer
    assert "скидк" in response.answer.lower()


def test_owned_email_wins_over_manufacturer_email_in_the_same_turn() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-mixed-contacts",
        "Передай менеджеру вопрос. Email производителя support@rehau.example, "
        "мой email buyer@example.com.",
    )

    state = bot.sessions.get("planner-mixed-contacts")
    assert response.need_handoff is True
    assert state.contact == "buyer@example.com"
    assert state.handoff_status == "awaiting_consent"
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == "buyer@example.com"
    assert "support@rehau.example" not in str(state.pending_handoff)


def test_compound_handoff_rebuild_preserves_engineering_missing_items() -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])
    session_id = "planner-preserve-handoff-missing"
    bot.handle_chat(session_id, "Передай менеджеру запрос по системе отопления.")
    state = bot.sessions.get(session_id)
    assert state.pending_handoff is not None
    state.pending_handoff["missing"] = [
        "инженерная схема и проверка совместимости узлов"
    ]
    bot.sessions.save(state)

    response = bot.handle_chat(
        session_id,
        "Покажи 1 модель радиатора и передай менеджеру.",
    )

    after = bot.sessions.get(session_id)
    assert len(response.products) == 1
    assert after.pending_handoff is not None
    assert after.pending_handoff["missing"] == [
        "инженерная схема и проверка совместимости узлов"
    ]


def test_exact_sku_price_delivery_and_discount_are_all_composed() -> None:
    product = _radiator(1)
    bot = ChatOrchestrator(products=[product])

    response = bot.handle_chat(
        "planner-sku-delivery-discount",
        f"Сколько стоит {product.sku} с доставкой и какая скидка?",
    )

    normalized = response.answer.lower()
    assert [card.sku for card in response.products] == [product.sku]
    assert str(int(product.price)) in response.answer
    assert "достав" in normalized
    assert "скидк" in normalized
    assert response.debug["turn_actions"] == [
        TurnAction.CATALOG_PRICE.value,
        TurnAction.ANSWER_COMMERCE_POLICY.value,
        TurnAction.ANSWER_DISCOUNT_POLICY.value,
    ]


def test_browse_and_delivery_are_answered_in_the_same_turn() -> None:
    bot = ChatOrchestrator(products=[_radiator(1), _radiator(2)])

    response = bot.handle_chat(
        "planner-browse-delivery",
        "Покажи 1 модель радиатора и скажи условия доставки.",
    )

    assert len(response.products) == 1
    assert "достав" in response.answer.lower()
    assert response.debug["turn_actions"] == [
        TurnAction.CATALOG_BROWSE.value,
        TurnAction.ANSWER_COMMERCE_POLICY.value,
    ]


def test_safety_answer_does_not_stage_unseen_handoff_contact() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "planner-safety-contact-consent"
    bot.handle_chat(session_id, "Передай менеджеру вопрос по котлу.")

    safety = bot.handle_chat(
        session_id,
        "Сколько стоит самому подключить газовый котел? "
        "Мой телефон +7 999 111-22-33.",
    )
    after_safety = bot.sessions.get(session_id)
    confirmation = bot.handle_chat(session_id, "Подтверждаю передачу")

    assert "газ" in safety.answer.lower()
    assert after_safety.contact is None
    assert after_safety.pending_handoff is not None
    assert after_safety.pending_handoff["contact"] is None
    assert confirmation.handoff_status == "awaiting_contact"
    assert confirmation.handoff_ticket_id is None


def test_contact_and_confirmation_same_turn_still_requires_preview() -> None:
    bot = ChatOrchestrator(products=[])
    session_id = "planner-contact-and-consent"
    bot.handle_chat(session_id, "Передай менеджеру вопрос по радиатору.")

    response = bot.handle_chat(
        session_id,
        "Мой email buyer@example.com, подтверждаю передачу.",
    )

    assert response.handoff_status == "awaiting_consent"
    assert response.handoff_ticket_id is None
    assert "будут переданы только" in response.answer.lower()
    assert "подтверждаю передачу" in response.answer.lower()


def test_ten_digit_article_is_not_a_callback_phone() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-numeric-article",
        "Передай менеджеру вопрос по артикулу 1234567890.",
    )

    state = bot.sessions.get("planner-numeric-article")
    assert response.handoff_status == "awaiting_contact"
    assert state.contact is None
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] is None
    assert "1234567890" in state.pending_handoff["wanted"]


@pytest.mark.parametrize(
    "message",
    [
        "Сколько стоит доставка радиатора и какая скидка?",
        "Сколько стоит доставка для радиатора? И есть скидка?",
        "Радиатор: сколько стоит доставка и какая скидка?",
        "Какая стоимость доставки трубы 20x2.8 и какая скидка?",
    ],
)
def test_delivery_cost_with_product_words_is_not_product_price(
    message: str,
) -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])

    response = bot.handle_chat(f"planner-delivery-cost-{abs(hash(message))}", message)

    normalized = response.answer.lower()
    assert response.products == []
    assert "достав" in normalized
    assert "скидк" in normalized
    assert TurnAction.CATALOG_PRICE.value not in response.debug["turn_actions"]


@pytest.mark.parametrize(
    "followup",
    [
        "Сколько он стоит с доставкой и какая скидка?",
        "Какая у него цена, доставка и скидка?",
    ],
)
def test_shown_product_price_delivery_discount_uses_grounded_memory(
    followup: str,
) -> None:
    bot = ChatOrchestrator(products=[_radiator(1)])
    session_id = f"planner-memory-commerce-{abs(hash(followup))}"
    first = bot.handle_chat(session_id, "Покажи 1 модель радиатора.")

    response = bot.handle_chat(session_id, followup)

    assert len(first.products) == 1
    assert [card.sku for card in response.products] == [first.products[0].sku]
    assert str(int(first.products[0].price)) in response.answer
    assert "достав" in response.answer.lower()
    assert "скидк" in response.answer.lower()


def test_handoff_and_delivery_execute_both_policy_and_preview() -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        "planner-handoff-delivery",
        "Передай менеджеру запрос и скажи условия доставки.",
    )

    normalized = response.answer.lower()
    assert response.need_handoff is True
    assert "достав" in normalized
    assert "оставьте телефон или email" in normalized
    assert response.debug["turn_actions"] == [
        TurnAction.ANSWER_COMMERCE_POLICY.value,
        TurnAction.CONTINUE_HANDOFF.value,
    ]


@pytest.mark.parametrize(
    "ownership",
    [
        "для связи +7 999 123-45-67",
        "мой рабочий телефон +7 999 123-45-67",
    ],
)
def test_explicit_customer_phone_wins_over_manufacturer_email(
    ownership: str,
) -> None:
    bot = ChatOrchestrator(products=[])

    response = bot.handle_chat(
        f"planner-owned-phone-{abs(hash(ownership))}",
        "Передай менеджеру вопрос. Email производителя "
        f"support@rehau.example, {ownership}.",
    )

    state = bot.sessions.get(response.session_id)
    assert response.handoff_status == "awaiting_consent"
    assert state.contact is not None
    assert "123-45-67" in state.contact
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == state.contact


@pytest.mark.parametrize(
    "email",
    [
        "иван@пример.рф",
        "buyer@xn--e1afmkfd.xn--p1ai",
    ],
)
def test_idn_customer_email_is_extracted_as_one_contact(email: str) -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"planner-idn-email-{abs(hash(email))}"

    response = bot.handle_chat(
        session_id,
        f"Передай менеджеру вопрос. Мой email {email}.",
    )

    state = bot.sessions.get(session_id)
    assert response.handoff_status == "awaiting_consent"
    assert state.contact == email
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] == email


@pytest.mark.parametrize(
    "phone",
    [
        "Мой телефон 123-45-67",
        "Мой телефон +7 999/123-45-67",
    ],
)
def test_explicit_local_phone_is_accepted_for_preview(phone: str) -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"planner-local-phone-{abs(hash(phone))}"

    response = bot.handle_chat(
        session_id,
        f"Передай менеджеру вопрос. {phone}.",
    )

    state = bot.sessions.get(session_id)
    assert response.handoff_status == "awaiting_consent"
    assert state.contact is not None
    assert "123-45-67" in state.contact


@pytest.mark.parametrize("label", ["артикулам", "SKU"])
def test_numeric_identifier_list_never_becomes_customer_contact(label: str) -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"planner-identifier-list-{label}"

    response = bot.handle_chat(
        session_id,
        f"Передай менеджеру вопрос по {label} 1234567890 и 0987654321.",
    )

    state = bot.sessions.get(session_id)
    assert response.handoff_status == "awaiting_contact"
    assert state.contact is None
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] is None
    assert "1234567890" in state.pending_handoff["wanted"]
    assert "0987654321" in state.pending_handoff["wanted"]


@pytest.mark.parametrize(
    "third_party_contact",
    [
        "Для связи с производителем email support@rehau.example.",
        "Контакт производителя для связи: support@rehau.example.",
        "Телефон поставщика для связи: +7 999 123-45-67.",
    ],
)
def test_third_party_for_contact_phrase_does_not_invert_ownership(
    third_party_contact: str,
) -> None:
    bot = ChatOrchestrator(products=[])
    session_id = f"planner-third-party-for-contact-{abs(hash(third_party_contact))}"

    response = bot.handle_chat(
        session_id,
        f"Передай менеджеру вопрос. {third_party_contact}",
    )

    state = bot.sessions.get(session_id)
    assert response.handoff_status == "awaiting_contact"
    assert response.debug["contact_direction"] == "third_party"
    assert state.contact is None
    assert state.pending_handoff is not None
    assert state.pending_handoff["contact"] is None
