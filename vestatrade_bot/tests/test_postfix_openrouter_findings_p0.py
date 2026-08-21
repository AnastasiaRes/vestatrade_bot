from __future__ import annotations

from typing import Any

from app.agents.orchestrator import ChatOrchestrator
from app.agents.product_card import ProductCardAgent
from app.models import Product, SearchQuery
from app.openrouter_client import LLMResult


class _PoisonTerminologyLLM:
    last_json_output_accepted = False
    last_fallback_reason = None

    def complete_json(
        self,
        _agent: str,
        _messages: list[dict[str, str]],
        fallback: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        return fallback, False

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content="FM означает фланцевую внутреннюю-наружную резьбу.",
            llm_used=True,
        )


class _OfflineLLM(_PoisonTerminologyLLM):
    last_fallback_reason = "offline regression"

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=None,
            llm_used=False,
            fallback_reason="offline regression",
        )


def _pump(sku: str, price: float) -> Product:
    return Product(
        sku=sku,
        name=f"Насос циркуляционный {sku} 25/6 180",
        category_path="Насосы циркуляционные",
        url=f"https://example.test/{sku.lower()}",
        price=price,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "Тип товара": "Циркуляционный насос",
            "Присоединение": "25",
            "Напор": "6 м",
            "Монтажная длина": "180 мм",
        },
    )


def _valve(sku: str, thread: str) -> Product:
    return Product(
        sku=sku,
        name=f'Кран шаровой 1/2" {"вн.-нар." if thread == "fm" else "вн.-вн."}',
        category_path="Краны шаровые",
        url=f"https://example.test/{sku.lower()}",
        price=500,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={
            "Тип товара": "Кран шаровой",
            "Диаметр подключения, дюйм": "1/2",
            "Тип резьбы": (
                "С внутренней наружной резьбой (fm)"
                if thread == "fm"
                else "С внутренней резьбой (ff)"
            ),
            "Рабочая среда": "Для воды",
        },
    )


def test_which_is_cheaper_returns_one_cheapest_shown_sku() -> None:
    bot = ChatOrchestrator(
        products=[
            _pump("P-6100", 6100),
            _pump("P-4777", 4777),
            _pump("P-5200", 5200),
        ],
        llm_client=_OfflineLLM(),
    )
    first = bot.handle_chat(
        "postfix-cheapest",
        "Покажи циркуляционные насосы 25/6 180",
    )
    assert len(first.products) > 1

    response = bot.handle_chat(
        "postfix-cheapest",
        "Какой дешевле? Назовите его один точный артикул.",
    )

    assert [product.sku for product in response.products] == ["P-4777"]
    assert "P-4777" in response.answer
    assert "P-6100" not in response.answer
    assert "P-5200" not in response.answer
    assert "более дешёвых подходящих" not in response.answer.lower()


def test_explicit_sku_typo_suggests_unique_neighbour_without_auto_selecting() -> None:
    bot = ChatOrchestrator(
        products=[
            Product(
                sku="151002",
                name="Водонагреватель THERMEX MK 50 V",
                category_path="Водонагреватели",
                url="https://example.test/151002",
                price=13160,
                stock_status="в наличии",
                stock_qty=8,
                attributes_normalized={
                    "Тип товара": "Водонагреватель",
                    "Объем бака, л": "50",
                },
            )
        ],
        llm_client=_OfflineLLM(),
    )

    response = bot.handle_chat("postfix-sku-typo", "Найди артикул 15100Z")

    assert response.products == []
    assert "15100Z" in response.answer
    assert "151002" in response.answer
    assert any(marker in response.answer.lower() for marker in ["возможно", "имели в виду", "подтверд"])


def test_thread_confirmation_is_deterministic_and_never_expands_fm_as_flange() -> None:
    product = _valve("VT.FM.04", "fm")
    bot = ChatOrchestrator(products=[product], llm_client=_PoisonTerminologyLLM())
    card = ProductCardAgent().build_card(
        product,
        SearchQuery(
            original_text="кран 1/2 ВР-НР",
            category="valves",
            slots={"size_inch": "1/2", "thread_type": "fm"},
        ),
    )
    assert card is not None
    session = bot.sessions.get("postfix-fm-grounding")
    session.category = "valves"
    session.slots = {
        "product_kind": "ball_valve",
        "size_inch": "1/2",
        "thread_type": "fm",
        "application": "вода",
    }
    session.last_products = [card]
    bot.sessions.save(session)

    response = bot.handle_chat(
        "postfix-fm-grounding",
        "Подтвердите текущую резьбу ВР-НР.",
    )

    normalized = response.answer.lower()
    assert "фланц" not in normalized
    assert "вр-нр" in normalized
    assert "внутрен" in normalized and "наруж" in normalized
    assert response.debug["final_answer_source"] == "deterministic"
