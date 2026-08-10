from __future__ import annotations

from app.agents.orchestrator import ChatOrchestrator
from app.agents.utils import normalize_text
from app.models import Product, SearchQuery


def _product(
    sku: str,
    name: str,
    category_path: str,
    *,
    attributes: dict[str, str] | None = None,
    description: str = "",
    price: float = 1000,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category_path,
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized=attributes or {},
        description=description,
    )


def test_explicit_piece_length_in_metres_becomes_catalogue_millimetres() -> None:
    pipes = [
        _product(
            "OUT-50-1000",
            "Труба наружная ПВХ 50x1000",
            "Канализация наружная",
            attributes={"тип товара": "Труба", "диаметр": "50 мм", "длина": "1000 мм"},
        ),
        _product(
            "OUT-50-2000",
            "Труба наружная ПВХ 50x2000",
            "Канализация наружная",
            attributes={"тип товара": "Труба", "диаметр": "50 мм", "длина": "2000 мм"},
            price=1500,
        ),
    ]
    bot = ChatOrchestrator(products=pipes)
    session_id = "piece-length-metres"
    bot.handle_chat(session_id, "нужна наружная канализационная труба 50 мм")

    response = bot.handle_chat(session_id, "длина трубы 2 метра")

    assert response.debug["slots"]["length_mm"] == 2000
    assert "total_length_m" not in response.debug["slots"]
    assert [card.sku for card in response.products] == ["OUT-50-2000"]


def test_total_quantity_in_metres_stays_total_quantity() -> None:
    bot = ChatOrchestrator(products=[])
    session = bot.sessions.get("total-length-metres")

    intent = bot.intent_router.route("нужно 20 метров трубы", session)

    assert intent.slots["total_length_m"] == 20
    assert "length_mm" not in intent.slots


def test_warm_floor_calculation_wins_over_unrelated_card_attributes() -> None:
    pump = _product(
        "PUMP-CARD",
        "Насос циркуляционный",
        "Насосы циркуляционные",
        attributes={"напор": "6 м"},
    )
    bot = ChatOrchestrator(products=[pump])
    session = bot.sessions.get("warm-floor-fact-precedence")
    session.category = "pipes"
    session.slots.update(
        {
            "project_scope": "warm_floor",
            "has_warm_floor": True,
            "warm_floor_area_m2": 120,
            "warm_floor_pipe_min_m": 780,
            "warm_floor_pipe_max_m": 840,
            "warm_floor_contours": 10,
        }
    )
    card = bot.card_agent.build_card(
        pump,
        SearchQuery(original_text="", category="pumps"),
    )
    assert card is not None
    session.last_products = [card]

    response = bot.handle_chat(
        session.session_id,
        "сколько трубы и сколько контуров?",
    )
    answer = normalize_text(response.answer)

    assert "780–840" in response.answer
    assert "10" in answer and "контур" in answer
    assert "в карточке не указано" not in answer


def test_customer_requirement_recall_uses_saved_type_not_product_inference() -> None:
    bot = ChatOrchestrator(products=[])
    session = bot.sessions.get("saved-boiler-type")
    session.category = "boilers"
    session.slots.update(
        {
            "boiler_type": "газовый",
            "contours": "двухконтурный",
            "area_m2": 100,
        }
    )

    response = bot.handle_chat(session.session_id, "И какой тип я просил?")
    answer = normalize_text(response.answer)

    assert "газов" in answer
    assert "двухконтур" in answer
    assert "я консультант" not in answer


def test_shown_boiler_with_builtin_pump_does_not_start_pump_questionnaire() -> None:
    boiler = _product(
        "GAS-24",
        "Котёл газовый двухконтурный 24 кВт",
        "Котлы газовые",
        attributes={
            "тип котла": "Газовый",
            "насос": "Встроенный циркуляционный насос",
        },
        description="Котёл со встроенным циркуляционным насосом и расширительным баком.",
        price=50000,
    )
    bot = ChatOrchestrator(products=[boiler])
    session_id = "boiler-built-in-pump-necessity"
    bot.handle_chat(session_id, "Покажи артикул GAS-24")

    response = bot.handle_chat(session_id, "а насос к нему нужен?")
    answer = normalize_text(response.answer)
    session = bot.sessions.get(session_id)

    assert "уже встроен" in answer
    assert "отдельн" in answer
    assert "расход" in answer and "гидравлическ" in answer
    assert [card.sku for card in response.products] == ["GAS-24"]
    assert session.category == "boilers"
    assert session.pending_category != "pumps"
    assert "pump_selection_mode" not in session.slots


def test_open_package_comparison_and_followup_stay_scoped_to_all_cards() -> None:
    pumps = [
        _product(
            "PUMP-A",
            "Насос циркуляционный A 25/6-180 с гайками",
            "Насосы циркуляционные",
            attributes={"напор": "6 м", "монтажная длина": "180 мм"},
        ),
        _product(
            "PUMP-B",
            "Насос циркуляционный B 25/6-180 с гайками",
            "Насосы циркуляционные",
            attributes={"напор": "6 м", "монтажная длина": "180 мм"},
            price=1200,
        ),
    ]
    bot = ChatOrchestrator(products=pumps)
    session = bot.sessions.get("all-card-package-scope")
    session.category = "pumps"
    query = SearchQuery(original_text="", category="pumps")
    cards = [bot.card_agent.build_card(product, query) for product in pumps]
    assert all(card is not None for card in cards)
    session.last_products = [card for card in cards if card is not None]

    package = bot.handle_chat(session.session_id, "что входит в комплект поставки?")
    package_answer = normalize_text(package.answer)
    assert "по какой из показанных моделей" not in package_answer
    assert "pump-a" in package_answer and "pump-b" in package_answer
    assert "гайк" in package_answer

    nuts = bot.handle_chat(session.session_id, "а гайки в комплекте есть?")
    nuts_answer = normalize_text(nuts.answer)
    assert "по какой из показанных моделей" not in nuts_answer
    assert "pump-a" in nuts_answer and "pump-b" in nuts_answer
    assert nuts_answer.count("да, подтверждено") == 2
