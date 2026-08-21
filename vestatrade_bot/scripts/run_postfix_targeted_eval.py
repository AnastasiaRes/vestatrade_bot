#!/usr/bin/env python3
"""Targeted live-LLM regression for the post-fix VestaTrade assistant.

The script only talks to BOT_API_BASE_URL.  Product facts are verified against
the local XML file; product URLs present in responses are stored as text and
are never opened.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_bot_evaluation import (  # noqa: E402
    APIClient,
    Catalog,
    CatalogProduct,
    percentile,
    response_answer,
    response_debug,
    response_products,
)


METRIC_DEFAULTS = {
    "retrieval": 1,
    "factuality": 1,
    "constraints": 1,
    "context": 1,
    "clarification": "N/A",
    "hallucination": 1,
}


def thread_pair(product: CatalogProduct | None) -> str | None:
    if product is None:
        return None
    value = product.param("тип резьбы").casefold()
    name = product.name.casefold()
    if "(ff)" in value or "вн.-вн" in name or "вн-вн" in name:
        return "ff"
    if "(fm)" in value or "вн.-нар" in name or "вн-нар" in name:
        return "fm"
    if "(mm)" in value or "нар.-нар" in name or "нар-нар" in name:
        return "mm"
    return None


def size_inch(product: CatalogProduct | None) -> str:
    if product is None:
        return ""
    return product.param("диаметр подключения, дюйм", "присоединительная резьба, дюйм")


def is_single_34(product: CatalogProduct) -> bool:
    value = size_inch(product).strip()
    if value:
        return value == "3/4"
    name = product.name.casefold().replace(" ", "")
    return "3/4" in name and "1/2" not in name


def add_issue(
    turn: dict[str, Any],
    code: str,
    reason: str,
    *,
    metric: str,
    severity: str = "FAIL",
) -> None:
    turn["assessment"]["issues"].append(
        {"code": code, "reason": reason, "severity": severity}
    )
    if metric in turn["assessment"]["metrics"]:
        turn["assessment"]["metrics"][metric] = 0
    statuses = {item["severity"] for item in turn["assessment"]["issues"]}
    turn["assessment"]["status"] = (
        "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    )
    turn["assessment"]["metrics"]["overall"] = turn["assessment"]["status"]


def make_turn(number: int, message: str, technical: dict[str, Any]) -> dict[str, Any]:
    metrics = dict(METRIC_DEFAULTS)
    metrics["overall"] = "PASS"
    turn = {
        "turn": number,
        "user": message,
        "bot": response_answer(technical),
        "products": response_products(technical),
        "technical": technical,
        "assessment": {"status": "PASS", "metrics": metrics, "issues": []},
    }
    if technical.get("error") or technical.get("status_code") != 200:
        add_issue(
            turn,
            "API_ERROR",
            f"HTTP={technical.get('status_code')}, error={technical.get('error')}",
            metric="retrieval",
        )
    elif not turn["bot"]:
        add_issue(turn, "API_ERROR", "Empty answer", metric="factuality")
    return turn


def send(dialogue: dict[str, Any], client: APIClient, message: str) -> dict[str, Any]:
    technical = client.chat(dialogue["session_id"], message)
    turn = make_turn(len(dialogue["turns"]) + 1, message, technical)
    dialogue["turns"].append(turn)
    return turn


def card_skus(turn: dict[str, Any]) -> list[str]:
    return [str(item.get("sku") or "") for item in turn["products"] if item.get("sku")]


def check_all_cards(
    turn: dict[str, Any],
    catalog: Catalog,
    predicate: Callable[[CatalogProduct], bool],
    label: str,
) -> None:
    skus = card_skus(turn)
    if not skus:
        add_issue(turn, "RETRIEVAL_WRONG_PRODUCT", f"No product cards for {label}", metric="retrieval")
        return
    bad: list[str] = []
    unknown: list[str] = []
    for sku in skus:
        product = catalog.get(sku)
        if product is None:
            unknown.append(sku)
        elif not predicate(product):
            bad.append(sku)
    if unknown:
        add_issue(
            turn,
            "HALLUCINATED_PRODUCT",
            f"SKU absent from local XML: {unknown}",
            metric="hallucination",
        )
    if bad:
        add_issue(
            turn,
            "MISSED_CONSTRAINT",
            f"Cards violate {label}: {bad}",
            metric="constraints",
        )


def new_dialogue(case: str, repeat: int, title: str) -> dict[str, Any]:
    return {
        "scenario_id": f"{case}-R{repeat}",
        "case": case,
        "repeat": repeat,
        "title": title,
        "session_id": f"target-{case}-{repeat}-{uuid.uuid4().hex[:8]}",
        "turns": [],
    }


def case_ff(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-FF-NATURAL", repeat, "Natural phrase ВР-ВР must be hard constraint")
    first = send(d, client, "Нужен кран на воду полдюйма")
    first_text = first["bot"].casefold()
    if any(token in first_text for token in ("для чего нужен кран", "применение крана", "какой системы")):
        add_issue(
            first,
            "BAD_CLARIFICATION",
            "Assistant asks application although water was already stated",
            metric="clarification",
        )
    second = send(
        d,
        client,
        "Для холодной воды. Нужна резьба внутренняя с обеих сторон, то есть ВР-ВР.",
    )
    check_all_cards(second, catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
    third = send(d, client, "Оставьте только ВР-ВР и подтвердите резьбу каждого артикула.")
    check_all_cards(third, catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
    return d


def case_fm_term(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-FM-TERM", repeat, "FM must mean female-male, never flange")
    first = send(d, client, "Нужен шаровой кран 1/2 ВР-НР для воды")
    check_all_cards(first, catalog, lambda p: thread_pair(p) == "fm", "thread_pair=fm")
    second = send(d, client, "Подтвердите текущую резьбу ВР-НР и расшифруйте код FM.")
    text = second["bot"].casefold()
    if "фланц" in text:
        add_issue(
            second,
            "HALLUCINATED_ATTRIBUTE",
            "FM was falsely decoded as flange-related",
            metric="hallucination",
        )
        second["assessment"]["metrics"]["factuality"] = 0
    if not ("вр-нр" in text or ("внутрен" in text and "наруж" in text)):
        add_issue(
            second,
            "WRONG_ATTRIBUTE",
            "Answer does not correctly state internal-external thread",
            metric="factuality",
        )
    return d


def case_correction_fm(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-CORRECTION-FM", repeat, "FF to FM correction and grounded confirmation")
    first = send(d, client, "Нужен кран 1/2 ВР-ВР для воды")
    check_all_cards(first, catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
    second = send(d, client, "Нет, перепутал, нужен ВР-НР")
    check_all_cards(second, catalog, lambda p: thread_pair(p) == "fm", "thread_pair=fm")
    third = send(d, client, "Подтвердите текущую резьбу ВР-НР.")
    text = third["bot"].casefold()
    if "фланц" in text:
        add_issue(
            third,
            "HALLUCINATED_ATTRIBUTE",
            "FM/internal-external thread was falsely described as flange-related",
            metric="hallucination",
        )
        third["assessment"]["metrics"]["factuality"] = 0
    if not ("вр-нр" in text or ("внутрен" in text and "наруж" in text)):
        add_issue(
            third,
            "WRONG_ATTRIBUTE",
            "Confirmation does not correctly state internal-external thread",
            metric="factuality",
        )
    return d


def case_first_shown(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-FIRST-SHOWN", repeat, "Return to the actual first shown card")
    first = send(d, client, "Покажи кран 1/2 для воды")
    first_skus = card_skus(first)
    if not first_skus:
        add_issue(first, "RETRIEVAL_WRONG_PRODUCT", "No first card to remember", metric="retrieval")
    expected = first_skus[0] if first_skus else ""
    second = send(d, client, "А такой же 3/4?")
    check_all_cards(second, catalog, is_single_34, "single size 3/4")
    third = send(d, client, "А с бабочкой?")
    check_all_cards(
        third,
        catalog,
        lambda p: is_single_34(p)
        and "бабоч" in (p.param("тип ручки") + " " + p.name).casefold(),
        "3/4 with butterfly handle",
    )
    fourth = send(d, client, "Вернемся к первому показанному. Какой у него артикул?")
    returned = card_skus(fourth)
    if expected and expected not in returned and expected.casefold() not in fourth["bot"].casefold():
        add_issue(
            fourth,
            "CONTEXT_LOSS",
            f"Expected actual first card {expected}, got {returned}",
            metric="context",
        )
        fourth["assessment"]["metrics"]["retrieval"] = 0
    return d


def case_previous_sku(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-PREVIOUS-SKU", repeat, "Return to first of two explicitly shown SKUs")
    send(d, client, "Покажи 100013611")
    send(d, client, "Теперь покажи 100013619")
    third = send(d, client, "Вернемся к первому товару. Какой у него артикул?")
    if "100013611" not in card_skus(third) and "100013611" not in third["bot"]:
        add_issue(
            third,
            "CONTEXT_LOSS",
            f"Expected 100013611, got {card_skus(third)}",
            metric="context",
        )
        third["assessment"]["metrics"]["retrieval"] = 0
    fourth = send(d, client, "А цена у первого какая?")
    if "100013611" not in card_skus(fourth) and "100013611" not in fourth["bot"]:
        add_issue(
            fourth,
            "CONTEXT_LOSS",
            f"Price referent should remain 100013611, got {card_skus(fourth)}",
            metric="context",
        )
        fourth["assessment"]["metrics"]["retrieval"] = 0
    return d


def case_cheapest(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-CHEAPEST", repeat, "Cheapest product must be named by one exact SKU")
    first = send(d, client, "Покажи два шаровых крана 1/2 ВР-ВР для воды")
    send(d, client, "Чем отличаются эти варианты?")
    priced = [p for p in first["products"] if isinstance(p.get("price"), (int, float))]
    cheapest = min(priced, key=lambda p: p["price"])["sku"] if priced else ""
    third = send(d, client, "Какой дешевле? Назовите его один точный артикул.")
    named_shown = [
        str(item["sku"])
        for item in first["products"]
        if item.get("sku") and str(item["sku"]).casefold() in third["bot"].casefold()
    ]
    if not cheapest or named_shown != [cheapest]:
        add_issue(
            third,
            "WRONG_SKU",
            f"Expected cheapest SKU from shown API prices: {cheapest or '<none>'}",
            metric="factuality",
        )
        third["assessment"]["metrics"]["context"] = 0
    return d


def case_ppr45(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-PPR-45-NONE", repeat, "Impossible PPR 20x1/2 male 45 degree must fail closed")
    first = send(
        d,
        client,
        "Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов.",
    )
    if card_skus(first):
        add_issue(
            first,
            "RETRIEVAL_WRONG_PRODUCT",
            f"Exact combination is absent, but cards returned: {card_skus(first)}",
            metric="retrieval",
        )
        first["assessment"]["metrics"]["constraints"] = 0
    text = first["bot"].casefold()
    if not any(token in text for token in ("не наш", "нет точ", "отсутств", "не виж")):
        add_issue(
            first,
            "BAD_ALTERNATIVE",
            "Answer does not clearly disclose that exact combination is absent",
            metric="factuality",
        )
    second = send(d, client, "То есть точного PPR 20×1/2 НР на 45° в каталоге нет?")
    if card_skus(second):
        add_issue(
            second,
            "RETRIEVAL_WRONG_PRODUCT",
            f"Confirmation returned non-exact cards: {card_skus(second)}",
            metric="retrieval",
        )
        second["assessment"]["metrics"]["constraints"] = 0
    second_text = second["bot"].casefold()
    if not any(token in second_text for token in ("не наш", "нет точ", "отсутств", "не виж")):
        add_issue(
            second,
            "CONTEXT_LOSS",
            "Follow-up did not preserve/confirm the established no-exact-match result",
            metric="context",
        )
    return d


def is_ppr_90_male(product: CatalogProduct) -> bool:
    blob = (product.name + " " + " ".join(f"{k} {v}" for k, v in product.params.items())).casefold()
    ppr = "ppr" in blob or "полипропилен" in blob
    dim = "20" in product.param("диаметр", "размер") or "20" in product.name
    male = "наруж" in product.param("тип резьбы").casefold() or " нр" in blob
    angle = "90" in product.param("угол").casefold() or "90" in product.name
    return ppr and dim and male and angle


def case_ppr90(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue("T-PPR-90-EXACT", repeat, "Exact PPR 20x1/2 male 90 degree")
    first = send(d, client, "Нужен PPR угол 20×1/2 НР, 90 градусов.")
    check_all_cards(first, catalog, is_ppr_90_male, "PPR 20x1/2 male 90 degree")
    second = send(d, client, "Проверьте: система PPR, 20×1/2, наружная резьба, угол 90°. Артикул?")
    check_all_cards(second, catalog, is_ppr_90_male, "PPR 20x1/2 male 90 degree")
    return d


def case_sku_typo(client: APIClient, catalog: Catalog, repeat: int) -> dict[str, Any]:
    d = new_dialogue(
        "T-SKU-TYPO",
        repeat,
        "Ambiguous one-character SKU typo must ask for disambiguation",
    )
    first = send(d, client, "Найди артикул 15100Z")
    if card_skus(first):
        add_issue(
            first,
            "WRONG_SKU",
            "Ambiguous typo auto-selected a catalogue card",
            metric="retrieval",
        )
    normalized_answer = first["bot"].casefold()
    mentioned_neighbours = [
        sku for sku in ("151001", "151002", "151003", "151004", "151005")
        if sku in first["bot"]
    ]
    if len(mentioned_neighbours) < 2 or not any(
        marker in normalized_answer
        for marker in ("несколько", "уточн", "неоднознач", "вариант")
    ):
        add_issue(
            first,
            "BAD_CLARIFICATION",
            "Equally near SKUs 151001..151009 were not presented as ambiguous",
            metric="clarification",
        )
    second = send(d, client, "Исправляю: точный артикул 151002")
    if "151002" not in card_skus(second):
        add_issue(second, "WRONG_SKU", "Exact corrected SKU not returned", metric="retrieval")
    third = send(d, client, "Какая у него основная характеристика по карточке?")
    if "151002" not in card_skus(third) and "151002" not in third["bot"]:
        add_issue(third, "CONTEXT_LOSS", "Corrected SKU focus was lost", metric="context")
    return d


CASES: list[Callable[[APIClient, Catalog, int], dict[str, Any]]] = [
    case_ff,
    case_fm_term,
    case_first_shown,
    case_previous_sku,
    case_cheapest,
    case_ppr45,
    case_ppr90,
    case_sku_typo,
    case_correction_fm,
]


def reset_assessment(turn: dict[str, Any]) -> None:
    fresh = make_turn(turn["turn"], turn["user"], turn["technical"])
    turn["assessment"] = fresh["assessment"]


def rescore_dialogue(dialogue: dict[str, Any], catalog: Catalog) -> None:
    """Re-evaluate saved HTTP transcripts without sending new LLM requests."""
    for turn in dialogue["turns"]:
        reset_assessment(turn)
    turns = dialogue["turns"]
    case = dialogue["case"]
    if case == "T-FF-NATURAL":
        first_text = turns[0]["bot"].casefold()
        if any(token in first_text for token in ("для чего нужен кран", "применение крана", "какой системы")):
            add_issue(
                turns[0],
                "BAD_CLARIFICATION",
                "Assistant asks application although water was already stated",
                metric="clarification",
            )
        check_all_cards(turns[1], catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
        check_all_cards(turns[2], catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
    elif case == "T-FM-TERM":
        check_all_cards(turns[0], catalog, lambda p: thread_pair(p) == "fm", "thread_pair=fm")
        text = turns[1]["bot"].casefold()
        if "фланц" in text:
            add_issue(
                turns[1],
                "HALLUCINATED_ATTRIBUTE",
                "FM was falsely decoded as flange-related",
                metric="hallucination",
            )
            turns[1]["assessment"]["metrics"]["factuality"] = 0
        if not ("вр-нр" in text or ("внутрен" in text and "наруж" in text)):
            add_issue(
                turns[1],
                "WRONG_ATTRIBUTE",
                "Answer does not correctly state internal-external thread",
                metric="factuality",
            )
    elif case == "T-FIRST-SHOWN":
        first_skus = card_skus(turns[0])
        if not first_skus:
            add_issue(turns[0], "RETRIEVAL_WRONG_PRODUCT", "No first card to remember", metric="retrieval")
        expected = first_skus[0] if first_skus else ""
        check_all_cards(turns[1], catalog, is_single_34, "single size 3/4")
        check_all_cards(
            turns[2],
            catalog,
            lambda p: is_single_34(p)
            and "бабоч" in (p.param("тип ручки") + " " + p.name).casefold(),
            "3/4 with butterfly handle",
        )
        returned = card_skus(turns[3])
        if expected and expected not in returned and expected.casefold() not in turns[3]["bot"].casefold():
            add_issue(
                turns[3],
                "CONTEXT_LOSS",
                f"Expected actual first card {expected}, got {returned}",
                metric="context",
            )
            turns[3]["assessment"]["metrics"]["retrieval"] = 0
    elif case == "T-PREVIOUS-SKU":
        for turn, label in ((turns[2], "article"), (turns[3], "price")):
            if "100013611" not in card_skus(turn) and "100013611" not in turn["bot"]:
                add_issue(
                    turn,
                    "CONTEXT_LOSS",
                    f"Expected first explicit SKU 100013611 for {label}, got {card_skus(turn)}",
                    metric="context",
                )
                turn["assessment"]["metrics"]["retrieval"] = 0
    elif case == "T-CHEAPEST":
        priced = [p for p in turns[0]["products"] if isinstance(p.get("price"), (int, float))]
        cheapest = min(priced, key=lambda p: p["price"])["sku"] if priced else ""
        named = [
            str(item["sku"])
            for item in turns[0]["products"]
            if item.get("sku") and str(item["sku"]).casefold() in turns[2]["bot"].casefold()
        ]
        if not cheapest or named != [cheapest]:
            add_issue(
                turns[2],
                "WRONG_SKU",
                f"Expected one cheapest SKU {cheapest or '<none>'}; named shown SKUs={named}",
                metric="factuality",
            )
            turns[2]["assessment"]["metrics"]["context"] = 0
    elif case == "T-PPR-45-NONE":
        for turn in turns:
            if card_skus(turn):
                add_issue(
                    turn,
                    "RETRIEVAL_WRONG_PRODUCT",
                    f"Exact combination is absent, but cards returned: {card_skus(turn)}",
                    metric="retrieval",
                )
                turn["assessment"]["metrics"]["constraints"] = 0
        first_text = turns[0]["bot"].casefold()
        if not any(token in first_text for token in ("не наш", "нет точ", "отсутств", "не виж")):
            add_issue(
                turns[0],
                "BAD_ALTERNATIVE",
                "Answer does not disclose that exact combination is absent",
                metric="factuality",
            )
        second_text = turns[1]["bot"].casefold()
        if not any(
            token in second_text
            for token in ("не наш", "не найден", "нет точ", "отсутств", "не виж")
        ):
            add_issue(
                turns[1],
                "CONTEXT_LOSS",
                "Follow-up did not preserve/confirm the established no-exact-match result",
                metric="context",
            )
    elif case == "T-PPR-90-EXACT":
        for turn in turns:
            check_all_cards(turn, catalog, is_ppr_90_male, "PPR 20x1/2 male 90 degree")
    elif case == "T-SKU-TYPO":
        if "151002" not in turns[0]["bot"]:
            add_issue(
                turns[0],
                "WRONG_SKU",
                "Unique one-character-neighbour SKU 151002 was not suggested",
                metric="retrieval",
            )
        if "151002" not in card_skus(turns[1]):
            add_issue(turns[1], "WRONG_SKU", "Exact corrected SKU not returned", metric="retrieval")
        if "151002" not in card_skus(turns[2]) and "151002" not in turns[2]["bot"]:
            add_issue(turns[2], "CONTEXT_LOSS", "Corrected SKU focus was lost", metric="context")
    elif case == "T-CORRECTION-FM":
        check_all_cards(turns[0], catalog, lambda p: thread_pair(p) == "ff", "thread_pair=ff")
        check_all_cards(turns[1], catalog, lambda p: thread_pair(p) == "fm", "thread_pair=fm")
        text = turns[2]["bot"].casefold()
        if "фланц" in text:
            add_issue(
                turns[2],
                "HALLUCINATED_ATTRIBUTE",
                "FM/internal-external thread was falsely described as flange-related",
                metric="hallucination",
            )
            turns[2]["assessment"]["metrics"]["factuality"] = 0
        if not ("вр-нр" in text or ("внутрен" in text and "наруж" in text)):
            add_issue(
                turns[2],
                "WRONG_ATTRIBUTE",
                "Confirmation does not correctly state internal-external thread",
                metric="factuality",
            )
    finalize(dialogue)


def finalize(dialogue: dict[str, Any]) -> None:
    statuses = [turn["assessment"]["status"] for turn in dialogue["turns"]]
    dialogue["status"] = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"


def summarize(dialogues: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [turn for dialogue in dialogues for turn in dialogue["turns"]]
    latencies = [turn["technical"]["latency_sec"] for turn in turns if turn["technical"].get("latency_sec") is not None]
    issue_counts = Counter(
        issue["code"]
        for turn in turns
        for issue in turn["assessment"]["issues"]
        if issue["severity"] == "FAIL"
    )
    dialogue_counts = Counter(dialogue["status"] for dialogue in dialogues)
    turn_counts = Counter(turn["assessment"]["status"] for turn in turns)
    repeats: dict[str, dict[str, int]] = {}
    for case in sorted({dialogue["case"] for dialogue in dialogues}):
        repeats[case] = dict(Counter(d["status"] for d in dialogues if d["case"] == case))
    return {
        "dialogues": len(dialogues),
        "user_turns": len(turns),
        "dialogue_status": dict(dialogue_counts),
        "turn_status": dict(turn_counts),
        "dialogue_pass_rate": round(100 * dialogue_counts.get("PASS", 0) / len(dialogues), 2),
        "turn_pass_rate": round(100 * turn_counts.get("PASS", 0) / len(turns), 2),
        "top_errors": issue_counts.most_common(),
        "latency_sec": {
            "p50": round(percentile(latencies, 0.50) or 0, 4),
            "p95": round(percentile(latencies, 0.95) or 0, 4),
            "max": round(max(latencies) if latencies else 0, 4),
        },
        "technical_errors": sum(
            bool(turn["technical"].get("error")) or turn["technical"].get("status_code") != 200
            for turn in turns
        ),
        "llm_used_turns": sum(bool(response_debug(turn["technical"]).get("any_llm_used")) for turn in turns),
        "final_answer_sources": dict(Counter(response_debug(turn["technical"]).get("final_answer_source", "unknown") for turn in turns)),
        "repeated_runs": repeats,
    }


def write_outputs(output: Path, payload: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "test_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "test_transcripts.jsonl").open("w", encoding="utf-8") as handle:
        for dialogue in payload["dialogues"]:
            handle.write(json.dumps(dialogue, ensure_ascii=False) + "\n")
    summary = payload["summary"]
    lines = [
        "# Targeted post-fix OpenRouter evaluation",
        "",
        f"- Run UTC: `{payload['metadata']['run_utc']}`",
        f"- Endpoint: `{payload['metadata']['base_url']}`",
        f"- Model: `{payload['metadata']['model']}`",
        f"- Catalog: `{payload['metadata']['catalog']}` ({payload['metadata']['catalog_products']} offers)",
        f"- Dialogues / user turns: **{summary['dialogues']} / {summary['user_turns']}**",
        f"- Dialogue status: `{summary['dialogue_status']}`; pass rate **{summary['dialogue_pass_rate']}%**",
        f"- Turn status: `{summary['turn_status']}`; pass rate **{summary['turn_pass_rate']}%**",
        f"- Latency p50/p95/max: **{summary['latency_sec']['p50']} / {summary['latency_sec']['p95']} / {summary['latency_sec']['max']} s**",
        f"- Technical errors: **{summary['technical_errors']}**",
        f"- Turns with real LLM transport/use: **{summary['llm_used_turns']}**",
        f"- Final sources: `{summary['final_answer_sources']}`",
        "",
        "## Repeated runs",
        "",
    ]
    for case, counts in summary["repeated_runs"].items():
        lines.append(f"- `{case}`: `{counts}`")
    lines.extend(["", "## Errors", ""])
    for code, count in summary["top_errors"]:
        lines.append(f"- `{code}`: {count}")
    lines.extend(["", "## Failed dialogues", ""])
    for dialogue in payload["dialogues"]:
        if dialogue["status"] != "FAIL":
            continue
        lines.append(f"### {dialogue['scenario_id']}: {dialogue['title']}")
        lines.append("")
        for turn in dialogue["turns"]:
            lines.append(f"**USER:** {turn['user']}")
            lines.append("")
            lines.append(f"**BOT:** {turn['bot']}")
            lines.append("")
            if turn["assessment"]["issues"]:
                lines.append(f"Issues: `{json.dumps(turn['assessment']['issues'], ensure_ascii=False)}`")
                lines.append("")
    (output / "test_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("BOT_API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--xml", type=Path, default=ROOT / "data/products_all.xml")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=210.0)
    parser.add_argument("--pause", type=float, default=0.15)
    parser.add_argument("--rescore-input", type=Path)
    parser.add_argument("--only-case", choices=[case.__name__ for case in CASES])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = Catalog.from_xml(args.xml)
    if args.rescore_input:
        payload = json.loads(args.rescore_input.read_text(encoding="utf-8"))
        for dialogue in payload["dialogues"]:
            rescore_dialogue(dialogue, catalog)
        payload["metadata"]["rescored_utc"] = datetime.now(timezone.utc).isoformat()
        payload["metadata"]["rescore_source"] = str(args.rescore_input.resolve())
        payload["summary"] = summarize(payload["dialogues"])
        write_outputs(args.output_dir, payload)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)
        return 0
    client = APIClient(args.base_url, "/chat", "/health", args.timeout)
    health = client.health()
    if health.get("status_code") != 200:
        raise SystemExit(f"Local API health failed: {health}")
    dialogues: list[dict[str, Any]] = []
    selected_cases = [case for case in CASES if not args.only_case or case.__name__ == args.only_case]
    total = len(selected_cases) * args.repeats
    index = 0
    for case in selected_cases:
        for repeat in range(1, args.repeats + 1):
            index += 1
            print(f"[{index:02d}/{total:02d}] {case.__name__} repeat {repeat}", flush=True)
            dialogue = case(client, catalog, repeat)
            finalize(dialogue)
            dialogues.append(dialogue)
            print(f"  -> {dialogue['status']}", flush=True)
            if args.pause:
                time.sleep(args.pause)
    payload = {
        "metadata": {
            "run_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "model": "qwen/qwen3-vl-8b-instruct",
            "provider": "openrouter",
            "catalog": str(args.xml.resolve()),
            "catalog_products": len(catalog.products),
            "credentials": "***REDACTED***",
            "forbidden_domains_contacted": [],
        },
        "summary": {},
        "dialogues": dialogues,
    }
    payload["summary"] = summarize(dialogues)
    write_outputs(args.output_dir, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
