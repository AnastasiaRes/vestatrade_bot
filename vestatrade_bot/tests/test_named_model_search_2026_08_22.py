"""Явно названная модель должна побеждать воронку подбора.

«Покажи насос Wilo Star RS 25/6» и «Нужен котёл Arderia D24» возвращали пусто:
воронка требовала площадь дома и напор раньше, чем происходил поиск, хотя
модель уже определяет товар. Товары при этом находились по артикулу — то есть
дело было не в каталоге.
"""

from __future__ import annotations

import pytest

from app.agents.utils import fold_model_key


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # В фиде у насоса «UPС 25-40 180» буква «С» кириллическая.
        ("UPС 25-40 180", "UPC 25/40 180"),
        ("Wilo Star RS 25/6", "wilo star rs 25-6"),
        ("Arderia D24", "arderia  d-24"),
    ],
)
def test_model_key_folds_alphabets_and_separators(first: str, second: str) -> None:
    assert fold_model_key(first) == fold_model_key(second)


def test_model_key_keeps_different_models_apart() -> None:
    assert fold_model_key("Arderia D24") != fold_model_key("Arderia D28")
    assert fold_model_key("UPC 25-40") != fold_model_key("UPC 25-60")


def _pump(sku: str, name: str, head: str, mounting: str) -> "Product":
    from app.models import Product

    return Product(
        sku=sku,
        name=name,
        category_path="Насосное оборудование",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=5000.0,
        stock_status="в наличии",
        stock_qty=3,
        attributes_normalized={
            "тип товара": "Насос",
            "тип насоса": "Циркуляционный",
            "максимальный напор, м": head,
            "монтажная длина, мм": mounting,
        },
    )


def test_named_model_is_found_despite_mixed_alphabet_in_the_feed() -> None:
    """Латинский запрос должен находить позицию с кириллической «С» в названии."""
    from app.agents.feed_search import FeedSearchAgent

    agent = FeedSearchAgent(
        products=[_pump("53843", "Насос циркуляц. (отопл.) UPС 25-40 180", "4.5", "180")]
    )
    found = agent.find_named_models(old_model="UPC 25-40 180", category="pumps")

    assert [product.sku for product in found] == ["53843"]


def test_model_phrase_is_read_from_the_message_itself() -> None:
    """Нормализация могла привести «UPС» к «UPS»; исходный текст это переживает."""
    from app.agents.feed_search import FeedSearchAgent

    agent = FeedSearchAgent(
        products=[_pump("53843", "Насос циркуляц. (отопл.) UPС 25-40 180", "4.5", "180")]
    )
    found = agent.find_named_models(
        old_model="UPS 25-40 180",
        message="нужен насос UPС 25-40 180",
        category="pumps",
    )

    assert [product.sku for product in found] == ["53843"]


def test_ordinary_request_without_a_model_finds_nothing() -> None:
    """«Котёл на 100 м2» — не маркировка: ветка модели не должна срабатывать."""
    from app.agents.feed_search import FeedSearchAgent

    agent = FeedSearchAgent(products=[_pump("P1", "Насос циркуляционный 25/6 180", "6", "180")])
    assert agent.find_named_models(message="электрический котёл на 100 м2") == []


def test_named_model_still_honours_stated_constraints(orchestrator) -> None:
    """Названная серия не отменяет резьбу: «BASE 1/2 ВР/ВР» — только ВР/ВР.

    Фраза «BASE 1/2» похожа на маркировку, и без проверки ограничений ветка
    модели приносила исполнение ВР/НР.
    """
    response = orchestrator.handle_chat(
        "named-model-constraints",
        'Нужен шаровой кран BASE 1/2" ВР/ВР для воды',
    )
    for product in response.products:
        assert "вн.-нар" not in product.name.lower()
