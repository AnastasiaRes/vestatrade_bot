"""Regressions for bugs found by the 2026-07-21 persona dialog QA run.

See reports/persona_dialog_qa_report_2026-07-21.md for the original transcripts.
"""

from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product, SearchQuery


def test_pipe_question_mentioning_radiators_as_destination_stays_pipes(orchestrator) -> None:
    # "трубы ... подключение радиаторов" names the pipe as the subject; the
    # radiator is only the destination. The hard-coded radiators rule used to
    # hijack this into the radiators category and ask about sections/height.
    response = orchestrator.handle_chat(
        "pipe-to-radiator",
        "а трубы PPR 25мм для отопления какие есть у вас, назначение - подключение радиаторов",
    )

    assert response.debug["category"] == "pipes"
    assert "секци" not in response.answer.lower()


def test_negated_category_correction_is_respected(orchestrator) -> None:
    # "не насосы" explicitly rejects pumps; the classifier used to ignore the
    # negation and, on a keyword tie, default to "pumps" because it is
    # declared before "pipes" in CATEGORY_KEYWORDS.
    orchestrator.handle_chat("negation-fix", "дай трубы канализационные")
    response = orchestrator.handle_chat("negation-fix", "не то, я говорил трубы а не насосы")

    assert response.debug["category"] != "pumps"


def test_postpositive_negation_keeps_concrete_pipe_request(orchestrator) -> None:
    message = "котёл мне не нужен вообще, подбери трубу для отопления 20 мм"
    intent = orchestrator.intent_router.route(
        message,
        orchestrator.sessions.get("postpositive-negation"),
    )
    assert orchestrator._should_consult(
        message,
        intent,
        orchestrator.sessions.get("postpositive-negation"),
    ) is False

    response = orchestrator.handle_chat(
        "postpositive-negation",
        message,
    )

    assert response.debug["category"] == "pipes"
    assert response.products == []
    assert "участ" in response.answer.lower()
    assert "температур" in response.answer.lower()
    assert "давлен" in response.answer.lower()
    assert "площад" not in response.answer.lower()


def test_boiler_combined_heat_and_water_without_literal_hot_word_marks_two_contour(
    orchestrator,
) -> None:
    # "на отопление и на воду сразу" describes a two-circuit boiler (heat +
    # water together) without the literal word "горячая". The old elif chain
    # matched bare "отоплен" first and recorded одноконтурный — the opposite
    # of what the customer asked for.
    orchestrator.handle_chat("contours-fix", "хочу купить котел, но вообще не разбираюсь в этом")
    orchestrator.handle_chat("contours-fix", "дом у меня примерно 100 метров")
    response = orchestrator.handle_chat("contours-fix", "ну наверное на отопление и на воду сразу")

    assert response.debug["slots"].get("contours") == "двухконтурный"


def test_repeat_fallback_generic_pump_does_not_claim_circulation_specifics(orchestrator) -> None:
    # When pump_type was never established, the typical-variant fallback text
    # used to unconditionally talk about "циркуляционный насос" (mounting
    # length 130/180mm) even when the shown product was a different pump type
    # entirely — text and product disagreed.
    orchestrator.handle_chat("fallback-fix", "нужен насос")
    orchestrator.handle_chat("fallback-fix", "не знаю какой")
    response = orchestrator.handle_chat("fallback-fix", "любой")

    # The assistant must keep clarifying the use case instead of showing an
    # unrelated catalog fallback.
    assert response.products == []
    assert "монтажную длину 130/180" not in response.answer.lower()
    assert "для какой задачи" in response.answer.lower()
    assert "отопление" in response.answer.lower()
    assert "откачка воды" in response.answer.lower()


def test_repeat_fallback_circulation_pump_keeps_circulation_note(orchestrator) -> None:
    # When pump_type IS known to be circulation, the specific note should
    # still show (this guards against overcorrecting the fix above).
    orchestrator.handle_chat("fallback-fix-2", "нужен циркуляционный насос")
    orchestrator.handle_chat("fallback-fix-2", "не знаю какой")
    response = orchestrator.handle_chat("fallback-fix-2", "любой")

    assert "циркуляцион" in response.answer.lower()


def test_pump_own_complectation_question_is_not_treated_as_builtin_check(orchestrator) -> None:
    # "что входит в комплект поставки этого насоса?" names the pump itself.
    # _requested_parts() used to read the word "насос" as "check whether a
    # pump is built into [the shown product]" -- meaningless when the shown
    # product already IS the pump -- and produced a confusing decline instead
    # of the package-contents answer.
    orchestrator.handle_chat("selfref-fix", "нужен циркуляционный насос 25/6 180")
    response = orchestrator.handle_chat(
        "selfref-fix", "что входит в комплект поставки этого насоса?"
    )

    assert response.debug["intent"] == "complectation"
    assert "его наличие или включение в поставку" not in response.answer.lower()


def test_pending_pump_package_question_survives_sku_selection(sample_products) -> None:
    products = [product.model_copy(deep=True) for product in sample_products]
    products.append(
        Product(
            sku="PUMP-25-60-B",
            name="Насос циркуляционный 25-60 180 мм, модель B",
            category_path="Насосы циркуляционные",
            url="https://example.test/pump2560b",
            price=6500,
            stock_status="в наличии",
            stock_qty=4,
            attributes_normalized={
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "6 м",
            },
        )
    )
    bot = ChatOrchestrator(products=products)
    bot.handle_chat("pump-package-selection", "циркуляционный насос 25/6 180")
    question = bot.handle_chat(
        "pump-package-selection", "что входит в комплект поставки этого насоса?"
    )
    assert "по какой из показанных моделей" in question.answer.lower()

    response = bot.handle_chat("pump-package-selection", "PUMP-25-60")

    assert response.debug["intent"] == "complectation"
    assert "PUMP-25-60" in response.answer
    assert "в котёл встроены" not in response.answer.lower()
    assert "паспорт" in response.answer.lower() or "комплект" in response.answer.lower()


def test_warm_floor_paraphrase_starts_warm_floor_discovery(orchestrator) -> None:
    response = orchestrator.handle_chat(
        "warm-floor-paraphrase",
        "ищу трубы в пол, хочу чтобы зимой было тепло",
    )

    assert response.debug["slots"].get("scope_funnel") == "warm_floor"
    assert "площад" in response.answer.lower()
    assert "канализац" not in response.answer.lower()


def test_stock_of_cheapest_shown_product_uses_comparison_context(orchestrator) -> None:
    orchestrator.handle_chat("cheapest-stock", "что есть в наличии из котлов?")
    orchestrator.handle_chat("cheapest-stock", "чем они отличаются?")
    response = orchestrator.handle_chat(
        "cheapest-stock", "а сколько в наличии у самого дешевого?"
    )

    assert len(response.products) == 1
    assert response.products[0].sku == "ECA-6"
    assert "5 шт" in response.answer
    assert "более дешёвых" not in response.answer.lower()


def test_project_boiler_price_followup_uses_shown_boilers_not_new_weak_models() -> None:
    products = [
        Product(
            sku="GAS-24",
            name="Котел газовый 24 кВт",
            category_path="Котлы газовые",
            url="https://example.test/gas24",
            price=36000,
            stock_status="в наличии",
            stock_qty=4,
            attributes_normalized={"тип товара": "Котёл", "мощность, кВт": "24"},
        ),
        Product(
            sku="E-9",
            name="Котел электрический 9 кВт",
            category_path="Котлы электрические",
            url="https://example.test/e9",
            price=35000,
            stock_status="в наличии",
            stock_qty=5,
            attributes_normalized={"тип товара": "Котёл", "мощность, кВт": "9"},
        ),
    ]
    bot = ChatOrchestrator(products=products)
    session = bot.sessions.get("project-price")
    gas_card = bot.card_agent.build_card(
        products[0],
        SearchQuery(original_text="котел", category="boilers"),
    )
    assert gas_card is not None
    session.last_products = [gas_card]
    session.category = "boilers"
    session.slots = {"project_scope": "heating", "area_m2": 140.0}
    bot.sessions.save(session)

    response = bot.handle_chat(
        "project-price", "а по цене что посоветуете, сколько примерно выйдет котёл?"
    )

    assert {product.sku for product in response.products} == {"GAS-24"}
    assert "GAS-24" in response.answer
    assert "E-9" not in response.answer


def test_project_cart_uses_shutoff_valve_not_water_meter_check_valve() -> None:
    products = [
        Product(
            sku="PEX-16",
            name="Труба PE-Xa 16x2 для теплого пола",
            category_path="Трубы для теплого пола",
            url="https://example.test/pex",
            price=80,
            stock_status="в наличии",
            stock_qty=500,
            attributes_normalized={"тип товара": "Труба", "назначение": "Отопление"},
            description="Гибкая труба для контуров водяного теплого пола и систем отопления.",
        ),
        Product(
            sku="PPR-20",
            name="Труба PPR 20 мм для отопления",
            category_path="Трубы полипропиленовые",
            url="https://example.test/ppr",
            price=60,
            stock_status="в наличии",
            stock_qty=100,
            attributes_normalized={"тип товара": "Труба"},
        ),
        Product(
            sku="PUMP-WF",
            name="Насос циркуляционный 25-40 180 мм",
            category_path="Насосы циркуляционные",
            url="https://example.test/pump",
            price=4000,
            stock_status="в наличии",
            stock_qty=4,
            attributes_normalized={"тип товара": "Насос"},
        ),
        Product(
            sku="CHECK-METER",
            name="Обратный клапан для водосчетчика 1/2",
            category_path="Клапаны",
            url="https://example.test/check",
            price=100,
            stock_status="в наличии",
            stock_qty=30,
            attributes_normalized={"тип товара": "Клапан"},
        ),
        Product(
            sku="BALL-VALVE",
            name="Кран шаровой запорный 1/2",
            category_path="Краны шаровые",
            url="https://example.test/ball",
            price=300,
            stock_status="в наличии",
            stock_qty=20,
            attributes_normalized={"тип товара": "Кран"},
        ),
    ]
    bot = ChatOrchestrator(products=products)

    response = bot.handle_chat(
        "warm-floor-safe-cart",
        "собери полный комплект для водяного теплого пола 50 м2",
    )

    skus = {product.sku for product in response.products}
    assert "PEX-16" in skus
    assert "PPR-20" not in skus
    assert "BALL-VALVE" in skus
    assert "CHECK-METER" not in skus


def test_heating_project_rejects_cold_water_only_pipe() -> None:
    products = [
        Product(
            sku="HEAT-PIPE",
            name="Труба PPR 25 мм для отопления",
            category_path="Трубы полипропиленовые",
            url="https://example.test/heat-pipe",
            price=120,
            stock_status="в наличии",
            stock_qty=50,
            attributes_normalized={"тип товара": "Труба", "назначение": "Отопление"},
        ),
        Product(
            sku="COLD-PIPE",
            name="Труба ПЭ100 25 мм для холодного водоснабжения",
            category_path="Трубы ПНД",
            url="https://example.test/cold-pipe",
            price=50,
            stock_status="в наличии",
            stock_qty=100,
            attributes_normalized={
                "тип товара": "Труба",
                "назначение": "Холодное водоснабжение",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    session = bot.sessions.get("heating-pipe-role")
    session.slots = {"project_scope": "heating", "area_m2": 100.0}

    cards = bot._project_cards_by_category(
        "heating",
        "собери отопление",
        session,
    )

    assert [card.sku for card in cards.get("pipes", [])] == ["HEAT-PIPE"]


def test_complectation_question_with_sku_routes_to_complectation_not_exact_sku() -> None:
    # A complectation question that also names a SKU ("в котле ARD-E9 есть
    # встроенный насос?") used to be classified as a plain exact_sku lookup
    # (checked first in the elif chain), which then searched that SKU inside
    # whatever category won the earlier pumps/boilers keyword tie instead of
    # answering the actual complectation question about the named product.
    products = [
        Product(
            sku="ARD-E9",
            name="Электрический котёл Arderia E9, 9 кВт",
            category_path="Котлы электрические",
            brand="ARDERIA",
            url="https://example.test/arde9",
            price=38000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={"артикул": "ARD-E9", "мощность": "9 кВт"},
            description="Электрический котёл со встроенным циркуляционным насосом.",
        )
    ]
    bot = ChatOrchestrator(products=products)
    response = bot.handle_chat("sku-complect-fix", "в котле ARD-E9 есть встроенный насос?")

    assert response.debug["intent"] == "complectation"
    assert "ARD-E9" in response.answer
    assert "насос" in response.answer.lower()


def test_project_cart_pump_followup_answers_pump_not_whole_cart_again() -> None:
    # "теперь подберите насос к нему" after the boiler cart is already
    # confirmed used to re-run the entire multi-category bundling and repeat
    # the exact same boiler+pump+pipe+valve+sewer package verbatim, instead of
    # answering specifically about the pump already in the cart.
    products = [
        Product(
            sku="GAS-2C-120",
            name="Котел газовый настенный двухконтурный 24 кВт",
            category_path="Котлы газовые настенные",
            brand="ARDERIA",
            url="https://example.test/gas2c120",
            price=45000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=3,
            attributes_normalized={
                "артикул": "GAS-2C-120",
                "мощность": "24 кВт",
                "тип котла": "Газовый",
                "количество контуров": "Двухконтурный",
            },
        ),
        Product(
            sku="PUMP-25-40",
            name="Насос циркуляционный 25-40 180 мм",
            category_path="Насосы циркуляционные",
            brand="VESTA",
            url="https://example.test/pump2540",
            price=4300,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=3,
            attributes_normalized={
                "артикул": "PUMP-25-40",
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "4 м",
            },
        ),
    ]
    bot = ChatOrchestrator(products=products)
    # The cart must be established through a genuine whole-system request —
    # "подберите котёл для дома 120 м²" deliberately no longer builds one.
    bot.handle_chat("cart-followup-fix", "нужно отопление под ключ")
    first = bot.handle_chat("cart-followup-fix", "120 м2, газ есть")
    assert first.debug["slots"]["project_cart"]["pumps"] == ["PUMP-25-40"]

    response = bot.handle_chat("cart-followup-fix", "теперь подберите насос к нему")

    assert response.answer != first.answer
    assert "уже выбран" in response.answer.lower()
    assert "PUMP-25-40" in response.answer
