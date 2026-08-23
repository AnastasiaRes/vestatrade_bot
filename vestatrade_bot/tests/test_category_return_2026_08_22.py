"""Возврат к категории должен работать наравне с возвратом к товару.

«У крана, который показывал первым, какой артикул?» отрабатывало надёжно, а
«вернёмся к котлу» — нет: по одной формулировке возвращался товар предыдущей
категории, по другой бот повторял висящий уточняющий вопрос, по третьей
переискивал и подставлял другой SKU той же категории.
"""

from __future__ import annotations

import pytest

from app.agents.orchestrator import ChatOrchestrator


@pytest.mark.parametrize(
    "message",
    [
        "Вернёмся к котлу — какой ты предлагал и сколько он стоит?",
        "Вернись к крану, какой артикул?",
        "А что там по котлу было?",
        "Давай назад к насосу",
        "насос, который смотрели — какой артикул?",
        "Покажи ещё раз котёл",
        "верни котёл",
        "что по крану?",
    ],
)
def test_category_return_phrasings_are_recognised(message: str) -> None:
    assert ChatOrchestrator._looks_like_product_recall(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "а есть такой же с бабочкой?",
        "нужен кран 1/2 вн-вн для воды",
        "сравни эти два",
        "есть дешевле?",
        "покажи ещё варианты",
    ],
)
def test_ordinary_requests_are_not_returns(message: str) -> None:
    """Расширение словаря возврата не должно ловить обычные запросы."""
    assert ChatOrchestrator._looks_like_product_recall(message) is False


def test_return_by_category_restores_the_same_products(orchestrator) -> None:
    """Возврат обязан вернуть тот же набор, а не пересобранную выдачу."""
    shown = orchestrator.handle_chat("ret", "Найди артикул PUMP-25-40")
    original = [card.sku for card in shown.products]
    assert original, "первый ход должен показать насос"

    orchestrator.handle_chat("ret", "теперь покажи электрический котёл на 60 м2")
    returned = orchestrator.handle_chat("ret", "Вернёмся к насосу")

    assert [card.sku for card in returned.products] == original


def test_return_survives_a_pending_clarification(orchestrator) -> None:
    """Висящий вопрос другой ветки не должен глушить явный возврат."""
    shown = orchestrator.handle_chat("ret2", "Найди артикул PUMP-25-40")
    original = [card.sku for card in shown.products]
    assert original

    orchestrator.handle_chat("ret2", "а трубы какие посоветуешь?")
    returned = orchestrator.handle_chat("ret2", "Вернёмся к насосу")

    assert [card.sku for card in returned.products] == original
