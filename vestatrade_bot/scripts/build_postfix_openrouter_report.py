#!/usr/bin/env python3
"""Merge post-fix OpenRouter evaluation artifacts into one release report."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports"
SOURCES = {
    "smoke": REPORT_ROOT / "postfix_openrouter_smoke_2026-08-21/test_results.json",
    "core": REPORT_ROOT / "postfix_openrouter_core_2026-08-21/test_results.json",
    "targeted": REPORT_ROOT / "postfix_openrouter_targeted_2026-08-21/test_results.json",
    "correction_retest": REPORT_ROOT / "postfix_openrouter_targeted_correction_2026-08-21/test_results.json",
}
OUTPUT = REPORT_ROOT / "postfix_openrouter_final_2026-08-21"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> None:
    suites = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in SOURCES.items()}
    dialogues: list[dict[str, Any]] = []
    api_probes: list[dict[str, Any]] = []
    latencies: list[float] = []
    dialogue_status: Counter[str] = Counter()
    turn_status: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    problem_skus: Counter[str] = Counter()
    final_sources: Counter[str] = Counter()
    llm_used_turns = 0
    technical_errors = 0

    for suite_name, payload in suites.items():
        if suite_name == "core":
            api_probes = payload.get("api_probes", [])
        for source_dialogue in payload["dialogues"]:
            dialogue = dict(source_dialogue)
            dialogue["suite"] = suite_name
            dialogue["scenario_id"] = f"{suite_name}:{source_dialogue['scenario_id']}"
            dialogues.append(dialogue)
            dialogue_status[dialogue["status"]] += 1
            for turn in dialogue["turns"]:
                status = turn["assessment"]["status"]
                turn_status[status] += 1
                latency = turn["technical"].get("latency_sec")
                if isinstance(latency, (int, float)):
                    latencies.append(float(latency))
                debug = ((turn["technical"].get("response_json") or {}).get("debug") or {})
                llm_used_turns += bool(debug.get("any_llm_used"))
                final_sources[debug.get("final_answer_source", "unknown")] += 1
                technical_errors += bool(turn["technical"].get("error")) or turn["technical"].get("status_code") != 200
                for issue in turn["assessment"].get("issues", []):
                    if issue.get("severity") == "FAIL":
                        issues[issue["code"]] += 1
                if status == "FAIL":
                    for card in turn.get("products", []):
                        if card.get("sku"):
                            problem_skus[str(card["sku"])] += 1

    dialogue_total = sum(dialogue_status.values())
    turn_total = sum(turn_status.values())
    summary = {
        "readiness": "NOT READY",
        "dialogues": dialogue_total,
        "user_turns": turn_total,
        "dialogue_status": dict(dialogue_status),
        "dialogue_pass_rate": round(100 * dialogue_status["PASS"] / dialogue_total, 2),
        "turn_status": dict(turn_status),
        "turn_pass_rate": round(100 * turn_status["PASS"] / turn_total, 2),
        "top_errors": issues.most_common(),
        "problem_skus": problem_skus.most_common(15),
        "latency_sec": {
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "max": round(max(latencies), 4),
        },
        "technical_errors": technical_errors,
        "api_probes_passed": sum(probe.get("status") == "PASS" for probe in api_probes),
        "api_probes_total": len(api_probes),
        "llm_used_turns": llm_used_turns,
        "final_answer_sources": dict(final_sources),
        "automatic_product_hallucinations": 0,
        "manual_hallucinated_or_wrong_attribute_turns": 1,
        "manual_hallucination_like_rate_pct": round(100 / turn_total, 2),
    }
    metadata = {
        "date": "2026-08-21",
        "base_url": "http://127.0.0.1:8765",
        "provider": "openrouter",
        "model": "qwen/qwen3-vl-8b-instruct",
        "strong_model": "qwen/qwen3-vl-8b-instruct",
        "generation_parameters": {
            "top_p": "not_set_by_application",
            "structured_json_agents_temperature": 0.0,
            "consultant_temperature": 0.35,
            "consultant_retry_temperature": 0.2,
            "response_composer_temperature_range": "0.2-0.5",
        },
        "llm_attempt_timeout_seconds": 60.0,
        "llm_request_budget_seconds": 180.0,
        "llm_max_retries": 2,
        "catalog": str((ROOT / "data/products_all.xml").resolve()),
        "catalog_offers": 14035,
        "products_loaded_from": "file",
        "credentials": "***REDACTED***",
        "vestatrade_ru_requests": 0,
        "source_artifacts": {name: str(path.resolve()) for name, path in SOURCES.items()},
    }
    manual_audit = [
        {
            "kind": "evaluator_false_positive",
            "scenarios": ["core:C-CTX-1", "core:C-CTX-2", "core:C-CTX-3"],
            "finding": "Final return to the first shown product was correct (VT.331.N.04), but the evaluator applied later 3/4/butterfly constraints. The same dialogues still fail manually because the intermediate 3/4 response contains mixed 1/2x3/4 products.",
        },
        {
            "kind": "evaluator_false_negative",
            "scenarios": ["smoke:S08"],
            "finding": "One accepted LLM response described FM as flange-related. This is a grounded terminology error missed by the automatic hallucination metric; exact correction retest later passed 3/3.",
        },
        {
            "kind": "evaluator_false_negative",
            "scenarios": ["targeted:T-CHEAPEST-R1", "targeted:T-CHEAPEST-R2", "targeted:T-CHEAPEST-R3"],
            "finding": "The assistant named all three shown SKUs after being asked for one cheapest SKU; targeted transcripts were rescored as FAIL.",
        },
        {
            "kind": "dynamic_data",
            "finding": "Prices and stock are retained as UNVERIFIED_DYNAMIC_DATA unless only compared within one live API response. No site request was made.",
        },
    ]
    combined = {
        "metadata": metadata,
        "summary": summary,
        "manual_audit": manual_audit,
        "api_probes": api_probes,
        "dialogues": dialogues,
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "test_results.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUTPUT / "test_transcripts.jsonl").open("w", encoding="utf-8") as handle:
        for dialogue in dialogues:
            handle.write(json.dumps(dialogue, ensure_ascii=False) + "\n")

    serious_ids = {
        "smoke:S08",
        "targeted:T-FIRST-SHOWN-R1",
        "targeted:T-PREVIOUS-SKU-R1",
        "targeted:T-CHEAPEST-R1",
        "targeted:T-PPR-45-NONE-R1",
        "targeted:T-SKU-TYPO-R1",
    }
    lines = [
        "# VestaTrade — post-fix OpenRouter dialogue evaluation",
        "",
        "## Verdict",
        "",
        "**NOT READY**",
        "",
        (
            f"Проверено **{summary['dialogues']} диалогов / {summary['user_turns']} пользовательских реплик**. "
            f"Диалоги: PASS {dialogue_status['PASS']}, FAIL {dialogue_status['FAIL']} "
            f"(pass rate {summary['dialogue_pass_rate']}%). Реплики: PASS {turn_status['PASS']}, "
            f"FAIL {turn_status['FAIL']} (pass rate {summary['turn_pass_rate']}%)."
        ),
        "",
        "Транспорт и базовый API стабильны, а новые hard constraints заметно улучшили PPR и резьбу. "
        "Релиз блокируют воспроизводимые ошибки ссылок на ранее показанный товар, смешение многопортовых "
        "размеров, выбор самого дешёвого и обработка опечатанного артикула.",
        "",
        "## Environment",
        "",
        f"- Local API: `{metadata['base_url']}`; штатный `/chat` flow.",
        f"- OpenRouter model / strong model: `{metadata['model']}` / `{metadata['strong_model']}`.",
        "- Temperature: JSON agents 0.0; consultant 0.35, retry 0.2; response composer 0.2–0.5; `top_p` не задаётся.",
        "- Timeouts: 60 s на попытку, 180 s на пользовательскую реплику, 2 retry.",
        f"- Local XML: **{metadata['catalog_offers']} offers**; `products_loaded_from=file`.",
        "- Credentials: `***REDACTED***`.",
        "- Запросов к `vestatrade.ru` / `www.vestatrade.ru`: **0**. URL из XML/API сохранялись только как строки.",
        "",
        "## Aggregate results",
        "",
        f"- Latency p50/p95/max: **{summary['latency_sec']['p50']} / {summary['latency_sec']['p95']} / {summary['latency_sec']['max']} s**.",
        f"- Technical/API errors: **{summary['technical_errors']}**; API probes: **{summary['api_probes_passed']}/{summary['api_probes_total']} PASS**.",
        f"- LLM used in routing/composition: **{summary['llm_used_turns']}/{summary['user_turns']} turns**.",
        f"- Final answers: `{summary['final_answer_sources']}`. Most defects are deterministic, not caused by model prose.",
        f"- Automatic product hallucinations: **0**. Manual audit found **1** hallucinated/wrong terminology turn "
        f"(**{summary['manual_hallucination_like_rate_pct']}%**): `FM` was once called flange-related.",
        "- Dynamic prices/stock: `UNVERIFIED_DYNAMIC_DATA`; they were not counted as catalog truth errors.",
        "",
        "### Error frequency",
        "",
    ]
    for code, count in summary["top_errors"]:
        lines.append(f"- `{code}`: **{count}**")
    lines.extend(["", "### Most problematic returned SKUs", ""])
    for sku, count in summary["problem_skus"]:
        lines.append(f"- `{sku}`: **{count}** failed-turn appearances")
    lines.extend(
        [
            "",
            "## Repeated-run results",
            "",
            "- PASS 3/3: core similar-SKU, incomplete request, analog, correction; targeted FM terminology; exact PPR 20×1/2 НР 90°; exact FF→FM correction.",
            "- Retrieval PASS 3/3 but dialogue FAIL 3/3: natural `внутренняя с обеих сторон` correctly returns FF cards, but `на воду` causes a redundant application question.",
            "- First no-match answer PASS 3/3 but dialogue FAIL 3/3: PPR 45° correctly returns no exact item, then the confirmation turn switches from fittings to pipes.",
            "- FAIL 3/3: mixed 3/4 follow-up, return to first shown, return to first explicit SKU, cheapest-one-SKU, one-character SKU typo.",
            "- Intermittent terminology defect: `FM` flange error observed once in smoke, then not reproduced in 3 exact correction repeats.",
            "",
            "## What the fixes improved",
            "",
            "- `PPR 20×1/2 НР 45°`: no invented 90°/PEX/press product in all three first responses.",
            "- `PPR 20×1/2 НР 90°`: only XML-grounded PPR male 20×1/2 90° cards in 3/3 runs.",
            "- Explicit `ВР-ВР` and `ВР-НР`: selected cards comply in repeated runs.",
            "- Requirement correction `ВР-ВР → ВР-НР`: 3/3 full dialogues PASS.",
            "- API isolation, session independence, malformed JSON, 4xx/404 and lost-session probes: all PASS.",
            "",
            "## Root-cause analysis and architecture",
            "",
            "A full RAG/LLM rewrite is not justified. Keep the current pipeline, but strengthen three shared deterministic layers: normalized product facts, typed dialogue referents/outcomes, and state-aware dialogue acts. Prompt changes or a larger model will not fix the dominant defects: 214/219 final answers were deterministic.",
            "",
            "1. **Port-aware product facts (P0).** `_inch_size_matches()` currently succeeds when a requested fraction occurs anywhere in the set of all product inches. Therefore 1/2×3/4 appliance valves pass a generic 3/4 constraint. At feed ingestion, build `connection_ports[]` with standard, size, gender and role plus a canonical `primary_size`. One shared fail-closed matcher must be used by retrieval, ranking and guardrails. A single-size query must reject mixed-port products unless the requested port is explicit.",
            "2. **Referent ledger (P0).** `ProductBranchState` is the right direction, but recall still depends on narrow regexes and defaults to the latest snapshot. Store immutable ordered emission snapshots (`turn_id`, ordered SKUs, query constraints, relation) and resolve `первый показанный`, nounless `первому показанному`, `первый товар`, `предыдущий`, `исходный` before merging current slots. `last_products` must remain only a view.",
            "3. **State-aware cheap/comparison act (P0).** `какой дешевле` over shown cards is a deterministic comparison, while `есть/покажи дешевле` is a new catalog search. Resolve the former before `cheap_request`; return exactly one min-price SKU. The current no-cheaper composer incorrectly lists all SKUs as one “last option”.",
            "4. **Persist no-match outcome (P0).** Save `LastSearchOutcome(category=fittings, constraints, status=no_exact_match)`. A confirmation such as `то есть точного ... нет?` must answer from that state and must not open a new `pipes` goal without an explicit topic noun/change.",
            "5. **Safe fuzzy SKU resolution (P1).** Exact matching must remain exact. Only after an explicit `артикул` marker, query a normalized SKU index and return `unique`, `ambiguous`, or `none`. A unique neighbour may be suggested but never auto-selected; tied neighbours such as `151001`…`151009` for `15100Z` require clarification.",
            "6. **Russian domain morphology (P1).** Centralize canonical lexemes (`вода/воды/воду → water`) before slot filling. Today the literal list recognizes `вода` and `воды`, but misses `воду`, producing redundant clarification.",
            "7. **Grounded terminology renderer (P1).** Render `ff/fm/mm`, sizes and connection types from the ontology/template, not free-form LLM prose. If LLM paraphrasing is retained, run a semantic claim guard against the selected product facts.",
            "8. **Evaluator contract (P1).** Score the actual response snapshot and port topology. On explicit recall, later constraints must not be applied to the restored historical card. Add checks for semantic code expansion (`FM`), one-of-N comparisons and no-match follow-ups.",
            "",
            "## Priority acceptance gates",
            "",
            "Before release, require all of the following in at least 3 repeated OpenRouter runs:",
            "",
            "- 0 mixed-port products for a single 3/4 request;",
            "- 3/3 correct for nounless and noun-qualified first/previous product references;",
            "- 3/3 one exact cheapest SKU;",
            "- 3/3 no-match confirmation stays in the same category/constraints;",
            "- 3/3 unique one-character SKU typo suggests the correct candidate;",
            "- no grounded terminology contradiction;",
            "- existing PPR 45° fail-closed and PPR 90° exact suites remain 3/3.",
            "",
            "## Manual evaluator audit",
            "",
            "- Core `C-CTX-1..3`: the final `VT.331.N.04` recall was correct; the evaluator wrongly applied later 3/4/butterfly constraints. The dialogue still fails because turn 2 contains mixed 1/2×3/4 cards.",
            "- Smoke `S08`: the automatic hallucination metric missed one false expansion of `FM` as flange-related.",
            "- Targeted cheapest and PPR-no-match transcripts were rescored from saved HTTP data; no extra LLM calls were used for rescoring.",
            "",
            "## Full representative serious dialogues",
            "",
        ]
    )
    for dialogue in dialogues:
        if dialogue["scenario_id"] not in serious_ids:
            continue
        lines.append(f"### {dialogue['scenario_id']} — {dialogue['title']}")
        lines.append("")
        for turn in dialogue["turns"]:
            lines.append(f"**USER:** {turn['user']}")
            lines.append("")
            lines.append(f"**BOT:** {turn['bot']}")
            lines.append("")
            if turn["assessment"].get("issues"):
                lines.append("**Assessment:** " + json.dumps(turn["assessment"]["issues"], ensure_ascii=False))
                lines.append("")
    (OUTPUT / "test_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
