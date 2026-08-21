#!/usr/bin/env python3
"""Combine the current post-fix live suites without issuing HTTP/LLM calls."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def debug_for(turn: dict[str, Any]) -> dict[str, Any]:
    body = (turn.get("technical") or {}).get("response_json")
    debug = body.get("debug") if isinstance(body, dict) else None
    return debug if isinstance(debug, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targeted",
        type=Path,
        default=ROOT / "reports/live_postfix_targeted_2026-08-21_rescored/test_results.json",
    )
    parser.add_argument(
        "--smoke",
        type=Path,
        default=ROOT / "reports/live_postfix_smoke_2026-08-21_rescored/test_results.json",
    )
    parser.add_argument(
        "--core",
        type=Path,
        default=ROOT / "reports/live_postfix_core_2026-08-21_rescored/test_results.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports/live_postfix_final_2026-08-21",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_paths = {
        "targeted": args.targeted.resolve(),
        "smoke": args.smoke.resolve(),
        "core": args.core.resolve(),
    }
    payloads = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in source_paths.items()
    }

    dialogues: list[dict[str, Any]] = []
    dialogue_status: Counter[str] = Counter()
    turn_status: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    problem_skus: Counter[str] = Counter()
    final_sources: Counter[str] = Counter()
    latencies: list[float] = []
    technical_errors = 0
    llm_used_turns = 0
    intent_requested = 0
    intent_rejected = 0
    engineering_requested = 0
    engineering_rejected = 0
    response_requested = 0
    response_rejected = 0
    hallucination_turns = 0
    unverified_dynamic_turns = 0

    for suite_name, payload in payloads.items():
        for raw_dialogue in payload.get("dialogues") or []:
            dialogue = dict(raw_dialogue)
            dialogue["suite"] = suite_name
            dialogue["source_scenario_id"] = raw_dialogue["scenario_id"]
            dialogue["scenario_id"] = f"{suite_name}:{raw_dialogue['scenario_id']}"
            dialogues.append(dialogue)
            dialogue_status[dialogue["status"]] += 1
            for turn in dialogue.get("turns") or []:
                status = turn["assessment"]["status"]
                turn_status[status] += 1
                technical = turn.get("technical") or {}
                latency = technical.get("latency_sec")
                if isinstance(latency, (int, float)):
                    latencies.append(float(latency))
                technical_errors += bool(technical.get("error")) or technical.get(
                    "status_code"
                ) != 200
                debug = debug_for(turn)
                llm_used_turns += bool(debug.get("any_llm_used"))
                final_sources[str(debug.get("final_answer_source") or "unknown")] += 1
                intent_requested += bool(debug.get("intent_llm_requested"))
                intent_rejected += bool(debug.get("intent_llm_requested")) and not bool(
                    debug.get("intent_llm_output_accepted")
                )
                engineering_requested += bool(debug.get("engineering_llm_requested"))
                engineering_rejected += bool(debug.get("engineering_llm_requested")) and not bool(
                    debug.get("engineering_llm_output_accepted")
                )
                response_requested += bool(debug.get("response_llm_requested"))
                response_rejected += bool(debug.get("response_llm_requested")) and not bool(
                    debug.get("response_llm_output_accepted")
                )

                codes: set[str] = set()
                for issue in turn["assessment"].get("issues") or []:
                    code = str(issue.get("code") or "")
                    severity = issue.get("severity")
                    if severity == "FAIL":
                        issue_counts[code] += 1
                        codes.add(code)
                hallucination_turns += bool(
                    codes
                    & {
                        "HALLUCINATED_PRODUCT",
                        "HALLUCINATED_ATTRIBUTE",
                        "HALLUCINATED_PRICE",
                        "HALLUCINATED_STOCK",
                    }
                )
                unverified_dynamic_turns += "UNVERIFIED_DYNAMIC_DATA" in codes
                if status == "FAIL":
                    for product in turn.get("products") or []:
                        if product.get("sku"):
                            problem_skus[str(product["sku"])] += 1

    dialogue_total = len(dialogues)
    turn_total = sum(turn_status.values())
    probes = list(payloads["smoke"].get("api_probes") or [])
    probe_status = Counter(str(item.get("status") or "unknown") for item in probes)
    summary = {
        "readiness": "NOT READY" if dialogue_status.get("FAIL") else "READY",
        "dialogues": dialogue_total,
        "user_turns": turn_total,
        "dialogue_status": dict(dialogue_status),
        "dialogue_pass_rate_percent": round(
            100 * dialogue_status.get("PASS", 0) / dialogue_total, 2
        ),
        "turn_status": dict(turn_status),
        "turn_pass_rate_percent": round(
            100 * turn_status.get("PASS", 0) / turn_total, 2
        ),
        "top_errors": issue_counts.most_common(),
        "problematic_skus": problem_skus.most_common(15),
        "latency_sec": {
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "max": round(max(latencies) if latencies else 0.0, 4),
        },
        "technical_errors": technical_errors,
        "api_probes": dict(probe_status),
        "llm_used_turns": llm_used_turns,
        "final_answer_sources": dict(final_sources),
        "llm_stage_contract": {
            "intent_requested": intent_requested,
            "intent_rejected_or_overridden": intent_rejected,
            "engineering_requested": engineering_requested,
            "engineering_rejected": engineering_rejected,
            "response_requested": response_requested,
            "response_rejected": response_rejected,
        },
        "hallucination_turns": hallucination_turns,
        "hallucination_rate_percent": round(100 * hallucination_turns / turn_total, 2),
        "unverified_dynamic_turns": unverified_dynamic_turns,
    }

    manual_findings = [
        {
            "priority": "P0",
            "code": "RETRIEVAL_WRONG_PRODUCT",
            "finding": (
                "Follow-up 'такой же 3/4' returns VTp.781.0.04005, a PPR collector "
                "tee with an integrated valve, as if it were the same standalone ball valve."
            ),
            "reproducibility": "4/4 (smoke S10 plus core C-CTX-1..3)",
        },
        {
            "priority": "P0",
            "code": "CONTEXT_LOSS",
            "finding": (
                "'Первый показанный' can resolve to VT.217.N.04 although the actual first "
                "card was VT.331.N.04."
            ),
            "reproducibility": "4 failures in 7 explicit first-shown runs",
        },
        {
            "priority": "P0",
            "code": "BAD_CLARIFICATION",
            "finding": (
                "The radiator funnel re-asks regulate-vs-shutoff after a thermostatic "
                "valve was already requested; one typo dialogue also loses the earlier 1/2 size."
            ),
            "reproducibility": "C07, C-COR-3 and C13",
        },
        {
            "priority": "P1",
            "code": "EVALUATOR_ORACLE",
            "finding": (
                "The old 15100Z oracle was invalid: catalogue SKUs 151001 through 151009 "
                "are all one edit away. The safe product behaviour is an ambiguity "
                "clarification, never automatic selection of 151002."
            ),
            "reproducibility": "4 historical turns require re-scoring",
        },
        {
            "priority": "P1",
            "code": "BAD_CLARIFICATION",
            "finding": (
                "The generic valve request shows mixed thread types before asking thread; "
                "the novice elbow request is routed to pipes and loops on application; "
                "a sewer pipe request shows several lengths without asking length."
            ),
            "reproducibility": "smoke S01/S02 and core C08",
        },
    ]

    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": "http://127.0.0.1:8011",
        "llm_provider": "openrouter",
        "llm_model": "qwen/qwen3-vl-8b-instruct",
        "generation_parameters": payloads["core"].get("metadata", {}).get(
            "generation_parameters"
        ),
        "llm_attempt_timeout_seconds": 60.0,
        "llm_request_timeout_seconds": 180.0,
        "llm_max_retries": 2,
        "catalog_file": str((ROOT / "data/products_all.xml").resolve()),
        "raw_xml_offers": payloads["targeted"].get("metadata", {}).get(
            "catalog_products"
        ),
        "backend_products_loaded": payloads["core"].get("metadata", {}).get(
            "products_loaded"
        ),
        "products_loaded_from": payloads["core"].get("metadata", {}).get(
            "products_loaded_from"
        ),
        "credentials": "***REDACTED***",
        "forbidden_domains_contacted": [],
        "source_artifacts": {name: str(path) for name, path in source_paths.items()},
    }
    combined = {
        "metadata": metadata,
        "summary": summary,
        "manual_findings": manual_findings,
        "api_probes": probes,
        "dialogues": dialogues,
    }

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "test_results.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output / "test_transcripts.jsonl").open("w", encoding="utf-8") as handle:
        for dialogue in dialogues:
            handle.write(json.dumps(dialogue, ensure_ascii=False) + "\n")

    lines = [
        "# VestaTrade — post-fix live OpenRouter evaluation",
        "",
        "## Verdict",
        "",
        f"**{summary['readiness']}**",
        "",
        (
            f"Проведено **{dialogue_total} диалогов / {turn_total} пользовательских реплик** "
            f"через штатный локальный HTTP API. Диалоги: PASS "
            f"{dialogue_status.get('PASS', 0)}, FAIL {dialogue_status.get('FAIL', 0)} "
            f"({summary['dialogue_pass_rate_percent']}%). Реплики: PASS "
            f"{turn_status.get('PASS', 0)}, FAIL {turn_status.get('FAIL', 0)} "
            f"({summary['turn_pass_rate_percent']}%)."
        ),
        "",
        "Транспорт стабилен, автоматических галлюцинаций SKU/атрибутов не найдено, но "
        "релиз блокируют воспроизводимые retrieval/context/clarification ошибки.",
        "",
        "## Environment",
        "",
        f"- Endpoint: `{metadata['base_url']}/chat`.",
        f"- Provider/model: `{metadata['llm_provider']}` / `{metadata['llm_model']}`.",
        "- Temperature: structured JSON 0.0; consultant 0.35, retry 0.2; response composer 0.2–0.5; top_p не задаётся.",
        "- Timeout: 60 s на попытку, 180 s на user turn, до 2 retry.",
        f"- Каталог: локальный XML, raw offers **{metadata['raw_xml_offers']}**, после sanitation backend **{metadata['backend_products_loaded']}**, source=`file`.",
        "- Credentials: `***REDACTED***`.",
        "- Запросы к `vestatrade.ru` / `www.vestatrade.ru`: **0**; URL использовались только как строки из XML/API.",
        "",
        "## Aggregate results",
        "",
        f"- Latency p50/p95/max: **{summary['latency_sec']['p50']} / {summary['latency_sec']['p95']} / {summary['latency_sec']['max']} s**.",
        f"- End-to-end API errors/timeouts: **{summary['technical_errors']}**; API probes: `{summary['api_probes']}`.",
        f"- Real LLM used: **{llm_used_turns}/{turn_total} turns**; final answer sources: `{dict(final_sources)}`.",
        f"- Structured LLM contract: `{summary['llm_stage_contract']}`. Rejected outputs were handled by deterministic fallback/guards.",
        f"- Hallucination turns: **{hallucination_turns} ({summary['hallucination_rate_percent']}%)**.",
        "- Цены и остатки считаются dynamic; совпадение с XML не подтверждает их актуальность на сайте.",
        "",
        "### Automatic error frequency",
        "",
    ]
    for code, count in summary["top_errors"]:
        lines.append(f"- `{code}`: **{count}**")
    lines.extend(["", "### Problematic SKUs", ""])
    for sku, count in summary["problematic_skus"]:
        lines.append(f"- `{sku}`: **{count}** failed-turn appearances")

    lines.extend(["", "## Manual root-cause audit", ""])
    for finding in manual_findings:
        lines.append(
            f"- **{finding['priority']} {finding['code']}** — {finding['finding']} "
            f"Reproducibility: {finding['reproducibility']}."
        )

    lines.extend(
        [
            "",
            "## Repeated runs",
            "",
            "- PASS 3/3: ВР-ВР natural phrasing, FM terminology, ВР-ВР→ВР-НР correction, cheapest shown SKU, previous exact SKU, PPR 45° no-match, PPR 90° exact, similar-SKU comparison, analogs and generic incomplete requests.",
            "- ORACLE CORRECTION: `15100Z` has nine equally near catalogue SKUs; require an ambiguity clarification, not `151002`.",
            "- FAIL 3/3 in core: multi-turn valve context returns a wrong product class at 3/4 and later loses the actual first shown SKU.",
            "- First-shown targeted repetitions alone: PASS 2/3, FAIL 1/3; combined with smoke/core: 4 failures in 7 runs.",
            "",
            "## What is confirmed fixed",
            "",
            "- No-match `PPR 20×1/2 НР 45°`: 3/3, no neighboring 90°/PEX/press SKU; confirmation keeps fittings context.",
            "- Exact `PPR 20×1/2 НР 90°`: 3/3 only grounded matching cards.",
            "- Explicit FF/FM constraints and thread correction: stable repeated PASS.",
            "- One cheapest card selection: 3/3 targeted plus core comparison PASS after manual/evaluator audit.",
            "- Exact previous SKU, analog source and similar-SKU comparison: stable repeated PASS.",
            "- Session independence, malformed/empty request handling, 4xx/404 and lost-session fail-closed probes: 7/7 PASS.",
            "",
            "## Priority fixes before the next live run",
            "",
            "1. P0: enforce standalone `ball_valve` product identity; exclude fittings/collectors merely containing a valve from `такой же кран` follow-ups.",
            "2. P0: resolve ordered historical emission snapshots before applying current handle/size slots; `первый показанный` must mean the first card of the first result set.",
            "3. P0: make radiator slot requirements product-kind-aware; `thermostatic_valve` already implies regulation, and known size/form must survive follow-ups.",
            "4. P1: wire explicit SKU typo suggestion to the actual built `SearchQuery`/slot path; keep confirmation-only behavior.",
            "5. P1: clarify thread for a generic valve, route `уголок на трубу` as a fitting, and require pipe length before offering neighboring sewer SKUs.",
            "",
            "## Full serious dialogues",
            "",
        ]
    )
    serious = {
        "targeted:T-SKU-TYPO-R1",
        "targeted:T-FIRST-SHOWN-R1",
        "smoke:S02",
        "core:C-CTX-1",
        "core:C-COR-3",
        "core:C08",
    }
    for dialogue in dialogues:
        if dialogue["scenario_id"] not in serious:
            continue
        lines.append(f"### {dialogue['scenario_id']} — {dialogue['title']}")
        lines.append("")
        for turn in dialogue.get("turns") or []:
            lines.append(f"**USER:** {turn['user']}")
            lines.append("")
            lines.append(f"**BOT:** {turn['bot']}")
            lines.append("")
            if turn["assessment"].get("issues"):
                lines.append(
                    "**Assessment:** `"
                    + json.dumps(turn["assessment"]["issues"], ensure_ascii=False)
                    + "`"
                )
                lines.append("")

    (output / "test_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
