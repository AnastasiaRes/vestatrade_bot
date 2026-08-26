from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation_v2.adapters import (
    TranscriptAdapterError,
    adapt_catalog_products,
    adapt_dialogue_record,
    load_dialogue_transcripts_jsonl,
)
from app.feed_loader import FeedLoader
from app.evaluation_v2.contracts import ExecutionFailureActor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE6_TRANSCRIPTS = Path(
    "/private/tmp/vestatrade-stage6-full100-final/full100_shadow/transcripts.jsonl"
)


def test_adapter_accepts_sku_strings_and_complete_card_objects() -> None:
    transcript = adapt_dialogue_record(
        {
            "id": "A01",
            "session_id": "must-not-survive",
            "failure_reason": "arbitrary prose must not survive",
            "execution_status": "valid",
            "turns": [
                {
                    "n": 1,
                    "user": "Нужен товар",
                    "bot": "Показываю варианты",
                    "products": [
                        "SKU-STRING",
                        {
                            "sku": "SKU-CARD",
                            "name": "Товар",
                            "price": 100,
                            "currency": "RUB",
                            "stock_status": "in_stock",
                            "stock_qty": 2,
                            "url": "https://catalog.test/card",
                        },
                    ],
                    "source": "legacy",
                }
            ],
        },
        source_label="stage6",
    )

    assert [item.sku for item in transcript.turns[0].products] == [
        "SKU-STRING",
        "SKU-CARD",
    ]
    assert transcript.turns[0].products[0].price is None
    assert transcript.turns[0].products[1].price == 100
    serialized = json.dumps(transcript.model_dump(mode="json"), ensure_ascii=False)
    assert "must-not-survive" not in serialized
    assert "arbitrary prose" not in serialized


def test_adapter_rejects_duplicate_or_out_of_order_turns() -> None:
    base = {
        "id": "A01",
        "turns": [
            {"n": 1, "user": "one", "bot": "one", "products": []},
            {"n": 1, "user": "two", "bot": "two", "products": []},
        ],
    }
    with pytest.raises(TranscriptAdapterError, match="duplicate turn"):
        adapt_dialogue_record(base)
    base["turns"][1]["n"] = 0
    with pytest.raises(TranscriptAdapterError, match="at least 1"):
        adapt_dialogue_record(base)


def test_execution_failure_keeps_only_bounded_code_not_failure_prose() -> None:
    transcript = adapt_dialogue_record(
        {
            "id": "A01",
            "execution_status": "bot_error",
            "failure_reason": "customer phone +7 999 123-45-67",
            "turns": [],
        }
    )
    serialized = json.dumps(transcript.model_dump(mode="json"), ensure_ascii=False)

    assert transcript.execution_error_code == "BOT_ERROR"
    assert transcript.execution_failure_actor == ExecutionFailureActor.BOT
    assert "+7 999" not in serialized


def test_buyer_failure_actor_is_preserved_and_missing_status_is_unknown() -> None:
    buyer = adapt_dialogue_record(
        {
            "id": "A06",
            "execution_status": "buyer_protocol_error",
            "failure_stage": "buyer",
            "turns": [
                {
                    "n": 1,
                    "user": "Нужен товар",
                    "bot": "Уточните",
                    "products": [],
                }
            ],
        }
    )
    unknown = adapt_dialogue_record(
        {
            "id": "A07",
            "turns": [
                {
                    "n": 1,
                    "user": "Нужен товар",
                    "bot": "Ответ",
                    "products": [],
                }
            ],
        }
    )

    assert buyer.execution_failure_actor == ExecutionFailureActor.BUYER
    assert unknown.execution_status == "unknown"
    assert unknown.execution_failure_actor == ExecutionFailureActor.UNKNOWN


@pytest.mark.skipif(
    not STAGE6_TRANSCRIPTS.exists(),
    reason="saved Stage 6 full100 artifact is not present",
)
def test_current_stage6_full100_jsonl_adapts_without_special_cases() -> None:
    transcripts = load_dialogue_transcripts_jsonl(
        STAGE6_TRANSCRIPTS,
        source_label="stage6-shadow",
    )

    assert len(transcripts) == 100
    assert len({item.scenario_id for item in transcripts}) == 100


def test_feed100_catalog_adapter_preserves_authoritative_card_facts() -> None:
    feed = PROJECT_ROOT / "data" / "feed_showcase_100_2026-06-14.xml"
    products = FeedLoader().parse_xml(feed.read_bytes())
    truth = adapt_catalog_products(products)

    assert len(truth) == 100
    assert {item.sku for item in truth} == {item.sku for item in products}
    expected = products[0]
    actual = next(item for item in truth if item.sku == expected.sku)
    assert actual.price == expected.price
    assert actual.stock_status == expected.stock_status
    assert actual.url == expected.url
