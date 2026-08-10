from __future__ import annotations

from app.agents.response_composer import ResponseComposerAgent
from app.models import ProductCard, SearchQuery


def _boiler_card() -> ProductCard:
    return ProductCard(
        sku="BOILER-24",
        name="Котёл газовый одноконтурный 24 кВт",
        brand="TEST",
        price=35_000,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=2,
        url="https://example.test/boiler",
        characteristics={
            "тип котла": "Газовый",
            "количество контуров": "Одноконтурный",
            "мощность, кВт": "24",
        },
    )


def test_single_candidate_recommendation_is_honest_and_engineering_grounded() -> None:
    query = SearchQuery(
        original_text="какой бы вы выбрали и почему",
        category="boilers",
        slots={
            "choose_one": True,
            "boiler_type": "газовый",
            "contours": "одноконтурный",
            "area_m2": 120,
        },
    )

    answer = ResponseComposerAgent().compose_choose_one(
        _boiler_card(),
        query,
        candidate_count=1,
    )

    normalized = answer.lower()
    assert "один" in normalized and "вариант" in normalized
    assert "почему:" in normalized
    assert "тип котла: газовый" in normalized
    assert "количество контуров: одноконтурный" in normalized
    assert "если нужна горячая вода" in normalized


def test_price_is_not_used_as_the_reason_when_customer_did_not_ask_for_cheap() -> None:
    query = SearchQuery(
        original_text="какой лучше",
        category="boilers",
        slots={"choose_one": True, "boiler_type": "газовый"},
    )

    answer = ResponseComposerAgent().compose_choose_one(
        _boiler_card(), query, candidate_count=2
    )
    why = answer.lower().split("почему:", 1)[1].split("когда не подойдёт", 1)[0]

    assert "цена" not in why
    assert "сравнил 2 найденных варианта" in answer.lower()


def test_natural_choice_question_routes_to_grounded_recommendation(orchestrator) -> None:
    orchestrator.handle_chat("natural-choice", "насос 25/6 180")

    answer = orchestrator.handle_chat(
        "natural-choice",
        "Какой из предложенных вы бы выбрали и почему?",
    )

    assert len(answer.products) == 1
    assert "Рекомендую:" in answer.answer
    assert "Почему:" in answer.answer
    assert "Когда не подойдёт:" in answer.answer
    assert "Потому что параметры из ваших уточнений" not in answer.answer
