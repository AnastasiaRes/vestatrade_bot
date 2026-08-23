"""Границы зоны консультации: не подменять семейство товара.

На «полотенцесушитель водяной 500х600» бот выдавал стальные панельные радиаторы
500x600 — размер совпадал, изделие было другим. Такие семейства (смесители,
полотенцесушители, дымоходы, крепёж, инструмент, санфаянс) не имеют у бота ни
категории, ни правил подбора, поэтому о границе нужно говорить прямо, а позиции
показывать как результат поиска по названию.
"""

from __future__ import annotations

import pytest

from app.agents.catalog_scope import match_unsupported_family
from app.agents.utils import normalize_text


@pytest.mark.parametrize(
    ("message", "family"),
    [
        ("Полотенцесушитель водяной 500х600", "полотенцесушители"),
        ("Нужен смеситель для ванны с душем", "смесители"),
        ("нужен коаксиальный дымоход для газового котла", "дымоходы и коаксиальные комплекты"),
        ("нужна теплоизоляция для труб 20 мм", "теплоизоляция"),
        ("утеплитель для труб 20", "теплоизоляция"),
        ("хомут для трубы 32", "крепёж"),
        ("сварочный аппарат для ппр труб", "инструмент"),
        ("нужна раковина в ванную", "санфаянс"),
    ],
)
def test_out_of_scope_families_are_recognised(message: str, family: str) -> None:
    matched = match_unsupported_family(normalize_text(message))
    assert matched is not None and matched.title == family


@pytest.mark.parametrize(
    "message",
    [
        # Головное существительное поддержанного семейства перевешивает.
        "кран для полотенцесушителя",
        "нужен кран 1/2 вн-вн для воды",
        "радиатор 22 тип 500 на 1000",
        "нужен котёл газовый 24 квт",
        # Слово в уточнении называет место, а не товар.
        "Под раковиной треснула серая пластиковая штука 50 мм",
        # Факт о состоянии объекта в воронке тёплого пола, а не покупка.
        "Пока только голая плита, утеплителя ещё нет.",
        "пол не утеплён",
    ],
)
def test_supported_requests_are_not_pushed_out_of_scope(message: str) -> None:
    assert match_unsupported_family(normalize_text(message)) is None


def test_towel_dryer_request_never_returns_radiators(orchestrator) -> None:
    """Главная защита: не подменять семейство соседним с тем же размером."""
    response = orchestrator.handle_chat("scope-towel", "Полотенцесушитель водяной 500х600")

    # Ограничение проговаривается, но не как отказ обслуживать.
    assert "не проверяю" in response.answer.lower() or "не нашёл" in response.answer.lower()
    for product in response.products:
        assert "радиатор" not in product.name.lower()


def test_out_of_scope_answer_names_the_family_and_the_supported_scope(orchestrator) -> None:
    response = orchestrator.handle_chat("scope-mixer", "Нужен смеситель для ванны")

    answer = response.answer.lower()
    assert "смесители" in answer
    # Ответ ведёт диалог дальше, а не заканчивается отказом.
    assert "менеджер" in answer or "подберу" in answer


def _sanitary(sku: str, name: str, kind: str, qty: int) -> "Product":
    from app.models import Product

    return Product(
        sku=sku,
        name=name,
        category_path="Санфаянс",
        brand="TEST",
        url=f"https://example.test/{sku.lower()}",
        price=1000.0,
        stock_status="в наличии" if qty else "нет в наличии",
        stock_qty=qty,
        attributes_normalized={"тип товара": kind},
    )


def test_family_search_prefers_the_product_over_its_accessories() -> None:
    """На «унитаз» нужен унитаз, а не сиденье и не обрамление для него."""
    from app.agents.feed_search import FeedSearchAgent

    agent = FeedSearchAgent(
        products=[
            _sanitary("A98", "Обрамление для унитаза малое", "Обрамление", 5),
            _sanitary("003PP", "Сиденье для унитаза Iddis", "Сиденье для унитаза", 9),
            _sanitary("07244", "Унитаз-компакт Home De Lux W101", "Унитаз", 1),
        ]
    )
    found = agent.search_unsupported_family(
        r"унитаз\w*", "нужен унитаз", required_word="унитаз"
    )

    assert found[0].sku == "07244"


def test_out_of_scope_answer_leads_with_the_product_not_the_refusal(orchestrator) -> None:
    """Первое, что читает покупатель, — что товар есть, а не отказ."""
    response = orchestrator.handle_chat("scope-tone", "Полотенцесушитель водяной 500х600")

    first_line = response.answer.splitlines()[0].lower()
    assert "не консультирую" not in first_line
    assert "не подбираю" not in first_line
