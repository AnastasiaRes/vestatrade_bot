"""Защитные проверки двух LLM-агентов: объяснение терминов и сравнение.

Оба агента отдают покупателю текст, собранный моделью, поэтому ценность здесь
не в том, что «модель ответила», а в том, что непроверяемый ответ до
покупателя не доходит. Тесты фиксируют именно границу: что принимается, что
отклоняется и почему.
"""

from __future__ import annotations

from typing import Any

from app.agents.product_comparator import ProductComparatorAgent
from app.agents.term_explainer import TermExplainerAgent
from app.models import ProductCard


class _StubLLM:
    """Отдаёт заранее заданный JSON вместо похода в сеть."""

    def __init__(self, payload: dict[str, Any] | None, used: bool = True) -> None:
        self.payload = payload
        self.used = used

    def complete_json(self, _agent, _messages, _fallback):
        return (self.payload or {}), self.used


def _pipe_card() -> ProductCard:
    return ProductCard(
        sku="VTp.700.FB20.25",
        name="Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)",
        brand="VALTEC",
        price=168.0,
        stock_status="в наличии",
        stock_qty=1000,
        url="https://example.invalid/fb20",
        characteristics={"полное наименование": "Труба PP-FIBER PN 20"},
    )


def _alux_card(stock_status: str = "в наличии") -> ProductCard:
    return ProductCard(
        sku="VTp.700.AL25.25",
        name="Труба PP-ALUX, арм. алюминием, PN 25, 25 MM (белый)",
        brand="VALTEC",
        price=261.0,
        stock_status=stock_status,
        stock_qty=1091,
        url="https://example.invalid/al25",
        characteristics={},
    )


def _difference(value_a: str, value_b: str) -> dict[str, Any]:
    return {
        "parameter": "класс давления",
        "values": [
            {"sku": "VTp.700.FB20.25", "value": value_a},
            {"sku": "VTp.700.AL25.25", "value": value_b},
        ],
        "why_it_matters": "Запас по давлению расходуется с ростом температуры.",
    }


def _comparison(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "comparable": True,
        "differences": [_difference("PN 20", "PN 25")],
        "missing_for_decision": [],
        "recommendation": None,
        "deciding_question": "Какая температура подачи в системе?",
    }
    payload.update(overrides)
    return payload


def test_comparator_keeps_values_that_expand_card_abbreviations() -> None:
    # В карточке «арм. стекл.», у модели «арм. стекловолокном». Это
    # расшифровка сокращения, а не выдумка: различие обязано пройти.
    payload = _comparison(
        differences=[
            {
                "parameter": "тип армирования",
                "values": [
                    {"sku": "VTp.700.FB20.25", "value": "арм. стекловолокном"},
                    {"sku": "VTp.700.AL25.25", "value": "арм. алюминием"},
                ],
                "why_it_matters": "Алюминиевый слой работает кислородным барьером.",
            }
        ]
    )
    agent = ProductComparatorAgent(_StubLLM(payload))

    answer = agent.compare([_pipe_card(), _alux_card()])

    assert answer is not None
    assert "арм. стекловолокном" in answer
    assert agent.last_dropped_differences == 0


def test_comparator_drops_value_that_swaps_a_numeric_parameter() -> None:
    # «PN 25» для трубы «PN 20, 25 MM»: токены «pn» и «25» в карточке есть,
    # но рядом их нет. Это чужой класс давления, и он не должен дойти.
    payload = _comparison(differences=[_difference("PN 25", "PN 25")])
    agent = ProductComparatorAgent(_StubLLM(payload))

    assert agent.compare([_pipe_card(), _alux_card()]) is None
    assert agent.last_rejection_reason == "no_grounded_differences"


def test_comparator_rejects_everything_when_an_unknown_sku_appears() -> None:
    payload = _comparison(
        differences=[
            {
                "parameter": "класс давления",
                "values": [
                    {"sku": "VTp.700.FB20.25", "value": "PN 20"},
                    {"sku": "VT.000.NOPE.99", "value": "PN 25"},
                ],
                "why_it_matters": "Запас по давлению.",
            }
        ]
    )
    agent = ProductComparatorAgent(_StubLLM(payload))

    assert agent.compare([_pipe_card(), _alux_card()]) is None
    assert agent.last_rejection_reason == "unknown_sku"


def test_comparator_does_not_recommend_a_position_without_stock() -> None:
    payload = _comparison(
        recommendation={"sku": "VTp.700.AL25.25", "reason": "выше класс давления"}
    )
    agent = ProductComparatorAgent(_StubLLM(payload))

    answer = agent.compare([_pipe_card(), _alux_card(stock_status="нет в наличии")])

    assert answer is not None
    assert "выше класс давления" not in answer


def test_comparator_needs_two_cards() -> None:
    agent = ProductComparatorAgent(_StubLLM(_comparison()))

    assert agent.compare([_pipe_card()]) is None
    assert agent.last_rejection_reason == "less_than_two_cards"


def _explanation(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "is_domain_term": True,
        "term": "DN",
        "definition": "DN — условный проход, округлённый типоразмер.",
        "why_it_matters": "По DN сходятся кран, фитинг и труба.",
        "pitfall": None,
        "ambiguous_meanings": [],
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


def test_term_explainer_answers_a_term_missing_from_the_glossary() -> None:
    agent = TermExplainerAgent(_StubLLM(_explanation()))

    answer = agent.explain("Что такое DN?")

    assert answer is not None
    assert "условный проход" in answer


def test_term_explainer_allows_the_word_pipe_despite_the_price_filter() -> None:
    # Регресс: подстрока «руб» сидит внутри слова «труба», и фильтр
    # коммерческих обещаний отклонял любое объяснение про трубы.
    agent = TermExplainerAgent(
        _StubLLM(
            _explanation(
                definition="SDR — отношение наружного диаметра трубы к толщине стенки.",
                why_it_matters="Чем меньше SDR, тем толще стенка трубы.",
            )
        )
    )

    assert agent.explain("Что такое SDR?") is not None


def test_term_explainer_rejects_a_price_claim() -> None:
    agent = TermExplainerAgent(
        _StubLLM(_explanation(pitfall="Такой кран стоит около 500 руб."))
    )

    assert agent.explain("Что такое шаровой кран?") is None
    assert agent.last_rejection_reason is not None
    assert agent.last_rejection_reason.startswith("commerce_claim")


def test_term_explainer_rejects_a_named_article() -> None:
    agent = TermExplainerAgent(
        _StubLLM(_explanation(why_it_matters="Например, VT.214.N.04 подойдёт."))
    )

    assert agent.explain("Что такое шаровой кран?") is None
    assert agent.last_rejection_reason == "sku_mentioned"


def test_term_explainer_stays_silent_on_low_confidence() -> None:
    # Именно ради этого случая в коде живёт честный отказ глоссария:
    # неуверенная расшифровка термина хуже, чем её отсутствие.
    agent = TermExplainerAgent(_StubLLM(_explanation(confidence="low")))

    assert agent.explain("Что такое квазифланец?") is None
    assert agent.last_rejection_reason == "low_confidence"


def test_term_explainer_stays_silent_outside_the_domain() -> None:
    agent = TermExplainerAgent(
        _StubLLM(
            {
                "is_domain_term": False,
                "term": "погода",
                "definition": None,
                "why_it_matters": None,
                "pitfall": None,
                "ambiguous_meanings": [],
                "confidence": "high",
            }
        )
    )

    assert agent.explain("Какая завтра погода?") is None
    assert agent.last_rejection_reason == "not_a_domain_term"


def test_term_explainer_survives_an_unavailable_model() -> None:
    agent = TermExplainerAgent(_StubLLM(None, used=False))

    assert agent.explain("Что такое DN?") is None
    assert agent.last_rejection_reason == "llm_unavailable"
