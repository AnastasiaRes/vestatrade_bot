"""Regressions for natural-language pipe requests seen in live dialogue.

These tests describe the intended deterministic behaviour.  They deliberately
use an unavailable LLM so a provider outage cannot turn ordinary pipe wording
into another product category or discard explicit engineering constraints.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.models import Product, SearchQuery
from app.openrouter_client import LLMResult


class _OfflineLLM:
    """Return every agent's deterministic fallback without network access."""

    last_json_output_accepted = False
    last_fallback_reason = "offline test"

    def complete_json(
        self,
        _agent: str,
        _messages: list[dict[str, str]],
        fallback: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        self.last_json_output_accepted = False
        self.last_fallback_reason = "offline test"
        return fallback, False

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=None,
            llm_used=False,
            fallback_reason="offline test",
        )


@pytest.fixture
def router() -> IntentRouterAgent:
    return IntentRouterAgent(llm_client=_OfflineLLM())


@pytest.mark.parametrize(
    ("message", "expected_slots"),
    [
        pytest.param(
            "PEX 16×2 EVOH для ВТП",
            {
                "pipe_material": "pex",
                "diameter_mm": 16,
                "wall_thickness_mm": 2.0,
                "oxygen_barrier": True,
                "pipe_service": "петля тёплого пола",
            },
            id="pex-without-pipe-noun",
        ),
        pytest.param(
            "PE-RT 16×2 для ВТП",
            {
                "pipe_material": "pe-rt",
                "diameter_mm": 16,
                "wall_thickness_mm": 2.0,
                "pipe_service": "петля тёплого пола",
            },
            id="pe-rt-without-pipe-noun",
        ),
        pytest.param(
            "металлопласт 16 мм на радиаторную разводку",
            {
                "pipe_material": "металлопластик",
                "diameter_mm": 16,
                "pipe_service": "радиаторная разводка",
            },
            id="metal-plastic-without-pipe-noun",
        ),
        pytest.param(
            "ПНД ПЭ100 SDR11 от колодца до дома, 32 мм",
            {
                "pipe_material": "пэ100",
                "diameter_mm": 32,
                "sdr": 11.0,
                "pipe_service": "подземный ввод от источника",
                "pipe_purpose": "водоснабжение",
                "water_temperature": "холодная",
            },
            id="hdpe-without-pipe-noun",
        ),
    ],
)
def test_material_name_starts_pipe_context_without_the_word_pipe(
    router: IntentRouterAgent,
    message: str,
    expected_slots: dict[str, object],
) -> None:
    result = router.route(message)

    assert result.category == "pipes"
    for key, expected in expected_slots.items():
        assert result.slots[key] == expected


def test_colloquial_hdpe_size_wins_over_route_length(
    router: IntentRouterAgent,
) -> None:
    result = router.route(
        "ПНД ПЭ100 SDR11 от колодца до дома, 32-я, трасса метров 40"
    )

    assert result.category == "pipes"
    assert result.slots["diameter_mm"] == 32
    assert result.slots["total_length_m"] == 40.0
    assert result.slots["pipe_purpose"] == "водоснабжение"
    assert result.slots["water_temperature"] == "холодная"


def test_explicit_warm_floor_pipe_and_quantity_do_not_restart_area_design() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "warm-floor-concrete-pipe",
        "Нужно PE-RT 16x2 для водяного тёплого пола, примерно 600 метров.",
    )

    assert response.debug["slots"]["pipe_material"] == "pe-rt"
    assert response.debug["slots"]["diameter_mm"] == 16
    assert response.debug["slots"]["total_length_m"] == 600.0
    assert "площад" not in response.answer.lower()


def test_local_feed_hdpe_abbreviations_confirm_cold_water_service() -> None:
    product = Product(
        sku="HDPE-32",
        name="Труба напорн. для хол/водосн. ПЭ100 SDR 11, 32х3,0",
        category_path="Трубы ПНД",
        brand="TEST",
        price=100,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип товара": "Труба"},
    )
    slots = {
        "pipe_material": "пэ100",
        "pipe_service": "подземный ввод от источника",
        "pipe_purpose": "водоснабжение",
        "water_temperature": "холодная",
        "sdr": 11.0,
        "diameter_mm": 32,
    }

    assert FeedSearchAgent([product])._slots_match(product, slots, "pipes") is True


def test_parenthesised_pert_dimension_exposes_wall_thickness() -> None:
    product = Product(
        sku="PERT-16-200",
        name=(
            "Трубы из полиэтилена повышенной термостойкости PE-RT "
            "(тип 2) 16(2,0) бухта по 200м"
        ),
        category_path="Трубы для тёплого пола",
        brand="TEST",
        price=100,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={"тип товара": "Труба"},
    )
    slots = {
        "pipe_material": "pe-rt",
        "pipe_service": "петля тёплого пола",
        "pipe_purpose": "отопление",
        "diameter_mm": 16,
        "wall_thickness_mm": 2.0,
    }

    assert FeedSearchAgent([product])._slots_match(product, slots, "pipes") is True


def test_known_pert_coil_length_produces_quantity_without_price_assumption() -> None:
    product = Product(
        sku="PERT-16-200",
        name="Труба PE-RT 16(2,0), бухта по 200м",
        category_path="Трубы для тёплого пола",
        brand="TEST",
        price=39,
        stock_status="нет в наличии",
        stock_qty=0,
        attributes_normalized={"тип товара": "Труба"},
    )
    query = SearchQuery(
        original_text="PE-RT 16x2, нужно 600 метров",
        category="pipes",
        slots={
            "pipe_service": "петля тёплого пола",
            "project_scope": "warm_floor",
            "total_length_m": 600.0,
        },
    )
    bot = ChatOrchestrator(products=[product], llm_client=_OfflineLLM())

    note = bot._compose_query_note(query, [product], []) or ""

    assert "бухта 200 м" in note
    assert "3 бухты" in note
    assert "цельной" in note and "без скрытых соединений" in note
    assert "единица цены" in note.lower()
    assert "не указаны длина" not in note


def test_piece_length_and_total_metres_survive_the_same_message(
    router: IntentRouterAgent,
) -> None:
    result = router.route(
        "Нужна PPR труба 20 мм: длина одного отрезка "
        "2 метра, всего нужно 20 метров"
    )

    assert result.category == "pipes"
    assert result.slots["length_mm"] == 2000
    assert result.slots["total_length_m"] == 20.0


def test_colloquial_cut_length_is_an_item_length_not_total_quantity(
    router: IntentRouterAgent,
) -> None:
    result = router.route("Канашка наружная 110, нужен отрезок 2 метра")

    assert result.category == "sewer"
    assert result.slots["diameter_mm"] == 110
    assert result.slots["length_mm"] == 2000
    assert "total_length_m" not in result.slots


def test_boiler_is_heat_source_not_a_replacement_for_warm_floor_service() -> None:
    product = Product(
        sku="PERT-16",
        name="Труба PE-RT 16x2 для тёплого пола",
        category_path="Трубы для тёплого пола",
        brand="TEST",
        url="https://example.test/pert-16",
        price=100,
        stock_status="в наличии",
        stock_qty=1,
        attributes_normalized={
            "тип товара": "Труба",
            "материал": "PE-RT",
            "диаметр (мм)": "16",
            "толщина стенки (мм)": "2",
        },
    )
    bot = ChatOrchestrator(products=[product], llm_client=_OfflineLLM())
    session_id = "warm-floor-source-is-not-service"

    first = bot.handle_chat(session_id, "Нужна труба PE-RT 16×2 для ВТП")
    assert first.debug["slots"]["pipe_service"] == "петля тёплого пола"

    followup = bot.handle_chat(session_id, "45 м², водяной от котла")

    assert followup.debug["category"] == "pipes"
    assert followup.debug["slots"]["pipe_service"] == "петля тёплого пола"


def test_without_evoh_is_preserved_as_a_negative_constraint(
    router: IntentRouterAgent,
) -> None:
    result = router.route("труба PEX без EVOH 16×2")

    assert result.category == "pipes"
    assert result.slots["oxygen_barrier"] is False


def test_negated_pn_does_not_override_the_requested_pn(
    router: IntentRouterAgent,
) -> None:
    result = router.route("труба PPR не PN20, нужна PN25")

    assert result.category == "pipes"
    assert result.slots["pressure_class_bar"] == 25.0
