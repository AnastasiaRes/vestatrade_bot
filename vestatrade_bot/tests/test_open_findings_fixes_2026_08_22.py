"""Регрессии на открытые находки QA-прогона 2026-08-22.

F-07/F-14 — тупиковые повторы уточнений; F-08 — ближайшее после «точного нет»;
F-11 — выбор по цене внутри показанного набора; F-12 — названная покупателем
характеристика; F-13 — общая справка вместо ответа в товарном контексте.
"""

from __future__ import annotations

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.selection_contracts import slot_answer_hint
from app.models import IntentResult, Product, ProductCard, SearchQuery, SessionState


# --- F-13: товарный контекст не должен считаться непрофильной репликой -------


@pytest.mark.parametrize(
    "slots",
    [
        {"handle_type": "butterfly"},
        {"size_inch": "1/2"},
        {"thread_type": "ff"},
        {"radiator_panel_type": 22},
        {"product_kind": "ball_valve"},
    ],
)
def test_product_slots_prevent_capability_blurb(slots: dict) -> None:
    bot = ChatOrchestrator(products=[])
    intent = IntentResult(intent_type="unknown", category="other", slots=slots)
    assert bot._is_non_product_message(intent) is False


def test_pure_small_talk_still_non_product() -> None:
    bot = ChatOrchestrator(products=[])
    intent = IntentResult(intent_type="unknown", category="other", slots={})
    assert bot._is_non_product_message(intent) is True


# --- F-12: названная дословно характеристика ---------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Какая у него характеристика «Тип резьбы»?", ["тип резьбы"]),
        ('Какая характеристика "Тип ручки"?', ["тип ручки"]),
        ("характеристика Материал корпуса?", ["материал корпуса"]),
    ],
)
def test_explicit_attribute_label_is_extracted(message: str, expected: list) -> None:
    assert ChatOrchestrator._explicit_attribute_labels(message) == expected


def test_ordinary_question_has_no_explicit_attribute_label() -> None:
    assert ChatOrchestrator._explicit_attribute_labels("Какой у него диаметр?") == []


# --- F-11: выбор по цене внутри показанного набора ---------------------------


def _card(sku: str, price: float, qty: int | None) -> ProductCard:
    return ProductCard(
        sku=sku,
        name=f"Радиатор {sku}",
        brand="TEST",
        price=price,
        currency="RUB",
        stock_status="в наличии" if qty else "нет в наличии",
        stock_qty=qty,
        url=f"https://example.test/{sku.lower()}",
    )


def _session_with_cards() -> SessionState:
    session = SessionState(session_id="t")
    session.last_products = [
        _card("A", 9000, 0),
        _card("B", 7000, 0),
        _card("C", 12000, 3),
    ]
    return session


@pytest.mark.parametrize(
    "message",
    [
        "Какой из них дешевле?",
        "Какой из них дешевле и есть ли он в наличии?",
        "Который из этих дешевле?",
        "Среди них есть подешевле?",
    ],
)
def test_cheapest_of_shown_is_answered_from_cards(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    result = bot._answer_shown_set_choice(message, _session_with_cards())

    assert result is not None
    answer, cards = result
    assert "B" in answer and "7000" in answer
    assert [card.sku for card in cards] == ["B"]


def test_stock_question_names_available_position() -> None:
    bot = ChatOrchestrator(products=[])
    answer, _ = bot._answer_shown_set_choice(
        "Какой из них дешевле и есть ли он в наличии?", _session_with_cards()
    )
    assert "C" in answer


def test_search_for_cheaper_is_not_hijacked() -> None:
    """«Есть дешевле?» — это поиск, а не выбор внутри показанного набора."""
    bot = ChatOrchestrator(products=[])
    assert bot._answer_shown_set_choice("Есть дешевле?", _session_with_cards()) is None


# --- F-08: ближайшее с названным отличием ------------------------------------


def _elbow(sku: str, angle: str, thread: str | None) -> Product:
    attributes = {"тип товара": "Угольник", "диаметр (мм)": "20", "угол (градусы)": angle}
    if thread:
        attributes["тип резьбы"] = thread
    return Product(
        sku=sku,
        name=f"Угольник {angle} PPR 20мм",
        category_path="Фитинги",
        brand="VALTEC",
        url=f"https://example.test/{sku.lower()}",
        price=50,
        stock_status="в наличии",
        stock_qty=10,
        attributes_normalized=attributes,
    )


def test_nearest_variants_relax_exactly_one_group() -> None:
    agent = FeedSearchAgent(products=[_elbow("ELB90", "90", "Наружная")])
    query = SearchQuery(
        original_text="PPR угол 20х1/2 наружная, угол 45",
        category="fittings",
        slots={"diameter_mm": 20, "angle_deg": 45, "thread_gender": "male"},
    )

    assert agent.search(query) == []
    groups = agent.search_nearest_variants(query)
    assert groups, "ближайшее должно находиться при ослаблении одного параметра"
    assert groups[0][0] == "угол"
    assert groups[0][1][0].sku == "ELB90"


@pytest.mark.parametrize(
    "message",
    ["Тогда что есть ближайшее по этим параметрам?", "Есть что-то похожее?", "чем заменить?"],
)
def test_nearest_request_is_recognised(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._asks_for_nearest_option(message) is True


def test_plain_request_is_not_a_nearest_request() -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._asks_for_nearest_option("Нужен кран 1/2 вн-вн") is False


# --- F-07/F-14: повтор вопроса должен что-то добавлять -----------------------


def test_known_slots_have_answer_hints() -> None:
    assert "M30x1,5" in slot_answer_hint(["metric_thread"])
    assert slot_answer_hint(["water_level_depth_m"])
    assert slot_answer_hint(["unknown_slot"]) == ""


# --- Ссылка из ответа обязана совпадать с карточкой --------------------------


def test_truncated_link_is_rejected() -> None:
    """LLM однажды обрезала адрес карточки — покупатель получил битую ссылку."""
    from app.agents.guardrails import GuardrailsAgent

    draft = "Кран. Ссылка: https://example.test/krany/kran-shar-base-babochka-12-vn-vn/"
    answer = "Кран. Ссылка: https://example.test/krany/kran-shar-base-babochka-12-vn"
    guard = GuardrailsAgent().validate_response_text(draft, answer, mode="products")

    assert guard.ok is False
    assert guard.safe_message == draft


def test_exact_link_passes() -> None:
    from app.agents.guardrails import GuardrailsAgent

    url = "https://example.test/krany/kran-shar-base-babochka-12-vn-vn/"
    draft = f"Кран. Артикул: VT.217.N.04. Цена: 452 RUB. Ссылка: {url}"
    guard = GuardrailsAgent().validate_response_text(draft, draft, mode="products")

    assert guard.ok is True


# --- F-07: ответ на вопрос о резьбе термоголовки должен распознаваться -------


@pytest.mark.parametrize(
    "message",
    ["M30x1,5", "м30х1,5", "резьба M30x1,5", "M30 x 1.5", "M30×1,5"],
)
def test_metric_thread_answer_is_recognised_without_category(message: str) -> None:
    """Ответ на прямой вопрос бота не должен зависеть от категории реплики."""
    from app.agents.engineering_notation import extract_engineering_notation

    slots = extract_engineering_notation(message, "other")
    assert slots.get("metric_thread") == "M30x1.5"


@pytest.mark.parametrize("panel_type", [10, 11, 12, 20, 21, 22, 30, 33])
def test_all_catalogue_panel_types_are_recognised(panel_type: int) -> None:
    """Тип 12 есть в каталоге (49 SKU), но раньше не распознавался."""
    from app.agents.engineering_notation import extract_engineering_notation

    slots = extract_engineering_notation(
        f"радиатор стальной панельный тип {panel_type}", "radiators"
    )
    assert slots.get("radiator_panel_type") == panel_type


# --- F-13: справка о возможностях не должна вытеснять товарный контекст ------


@pytest.mark.parametrize(
    "message",
    [
        "Признайте ошибку, если прошлый SKU был ВР-НР, и дайте корректный ВР-ВР.",
        "Этот точно подходит?",
        "Какой у него артикул?",
    ],
)
def test_product_followup_is_recognised(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._refers_to_shown_products(message) is True


@pytest.mark.parametrize("message", ["какие у тебя планы?", "спасибо", "как дела"])
def test_small_talk_does_not_revive_products(message: str) -> None:
    bot = ChatOrchestrator(products=[])
    assert bot._refers_to_shown_products(message) is False
