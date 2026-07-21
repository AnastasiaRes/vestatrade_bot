"""Run 100 ordered turns in 10 concurrent, isolated live-LLM sessions."""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any


API_URL = os.getenv("QA_API_URL", "http://127.0.0.1:8000").rstrip("/")
REPORT_PATH = Path(
    os.getenv(
        "QA_CONCURRENCY_REPORT",
        "reports/concurrent_llm_qa_report_2026-07-20.md",
    )
)
MAX_WORKERS = int(os.getenv("QA_CONCURRENCY_WORKERS", "10"))

SKUS = [
    "VT.217.N.04",
    "VRS.256.18.0",
    "2202210",
    "3300867",
    "VRS.129G.15.0",
    "VT.226.N.05",
    "APZ9-550M",
    "VT.KIT.3.0",
    "VTc.701.NE.05",
    "VT.AC674.V.0",
]

ROUNDS = [
    ("setup", lambda sku: sku, False, False, False),
    (
        "characteristics",
        lambda _sku: "Какие основные характеристики у показанного товара? Назови его артикул.",
        False,
        False,
        False,
    ),
    (
        "advice",
        lambda _sku: (
            "Какие два практических момента важно проверить перед покупкой именно этого "
            "товара? Назови артикул и не добавляй фактов, которых нет в карточке."
        ),
        True,
        False,
        False,
    ),
    (
        "link",
        lambda _sku: "Повтори ссылку на этот же товар и его артикул.",
        False,
        True,
        False,
    ),
    (
        "price",
        lambda _sku: "Сколько он стоит? Назови также артикул.",
        False,
        False,
        False,
    ),
    (
        "stock",
        lambda _sku: "Есть ли он в наличии? Назови артикул.",
        False,
        False,
        False,
    ),
    (
        "description",
        lambda _sku: "Кратко опиши показанный товар и назови артикул.",
        False,
        False,
        False,
    ),
    (
        "price_stock",
        lambda _sku: "Какая у него цена и статус наличия? Назови артикул.",
        False,
        False,
        False,
    ),
    (
        "recommendation",
        lambda _sku: (
            "Дай осторожную рекомендацию по этой карточке: какие ограничения нужно учесть. "
            "Назови артикул и опирайся только на данные карточки."
        ),
        True,
        False,
        False,
    ),
    (
        "summary_link",
        lambda _sku: "Дай итог: название, артикул и ссылку именно на этот товар.",
        False,
        True,
        True,
    ),
]


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(value).casefold())


def _post(session_id: str, message: str) -> tuple[dict[str, Any], float]:
    body = json.dumps(
        {"session_id": session_id, "message": message},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{API_URL}/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, time.perf_counter() - started


def _parallel_round(items: list[tuple[str, str]]) -> list[tuple[dict[str, Any], float]]:
    results: list[tuple[dict[str, Any], float] | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_post, session_id, message): index
            for index, (session_id, message) in enumerate(items)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # surfaced as a test issue below
                results[index] = ({"_error": repr(exc)}, 0.0)
    return [result for result in results if result is not None]


def run() -> dict[str, Any]:
    stamp = str(int(time.time()))
    sessions = [f"live-multi-{stamp}-{index}" for index in range(len(SKUS))]
    issues: list[str] = []
    latencies: list[float] = []
    metrics: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    phase_rows: list[dict[str, Any]] = []

    for phase, message_factory, expects_llm, expects_link, expects_name in ROUNDS:
        messages = [message_factory(sku) for sku in SKUS]
        responses = _parallel_round(list(zip(sessions, messages)))
        for index, (response, elapsed) in enumerate(responses):
            expected = SKUS[index]
            session_id = sessions[index]
            latencies.append(elapsed)
            row_issues: list[str] = []

            if response.get("_error"):
                row_issues.append(response["_error"])
            else:
                if response.get("session_id") != session_id:
                    row_issues.append("неверный session_id")
                answer = str(response.get("answer") or "")
                if not answer.strip():
                    row_issues.append("пустой ответ")

                expected_norm = _normalize(expected)
                got = {
                    _normalize(product.get("sku"))
                    for product in response.get("products") or []
                }
                if expected_norm not in got:
                    row_issues.append(f"потерян свой SKU {expected}; got={sorted(got)}")
                foreign_products = got - {expected_norm}
                if foreign_products:
                    row_issues.append(f"чужие карточки {sorted(foreign_products)}")

                answer_norm = _normalize(answer)
                if phase != "setup" and expected_norm not in answer_norm:
                    row_issues.append(f"ответ не называет свой SKU {expected}")
                foreign_mentions = [
                    sku
                    for sku in SKUS
                    if sku != expected and _normalize(sku) in answer_norm
                ]
                if foreign_mentions:
                    row_issues.append(f"чужие SKU в тексте {foreign_mentions}")
                if expects_link and "http" not in answer.casefold():
                    row_issues.append("не возвращена ссылка")
                if expects_name:
                    expected_name = next(
                        (
                            str(product.get("name") or "")
                            for product in response.get("products") or []
                            if _normalize(product.get("sku")) == expected_norm
                        ),
                        "",
                    )
                    if expected_name and _normalize(expected_name) not in answer_norm:
                        row_issues.append("не возвращено точное название товара")

                debug = response.get("debug") or {}
                source = str(debug.get("final_answer_source"))
                sources[source] += 1
                for key in [
                    "llm_requested",
                    "llm_transport_succeeded",
                    "llm_output_accepted",
                ]:
                    if debug.get(key):
                        metrics[key] += 1
                if debug.get("response_llm_output_accepted"):
                    metrics["response_llm_output_accepted"] += 1
                if debug.get("response_llm_requested"):
                    metrics["response_llm_requested"] += 1
                if debug.get("response_llm_used"):
                    metrics["response_llm_transport_succeeded"] += 1
                if expects_llm:
                    if not debug.get("response_llm_requested"):
                        row_issues.append("ResponseComposer не запросил LLM")
                    elif not debug.get("response_llm_used"):
                        row_issues.append("LLM transport не сработал")
                    if not debug.get("response_llm_output_accepted"):
                        metrics["safe_llm_rejections"] += 1
                        rejection_reasons[
                            str(debug.get("response_llm_rejection_reason") or "без причины")
                        ] += 1

            for issue in row_issues:
                issues.append(f"{phase}/{session_id}: {issue}")
            phase_rows.append(
                {
                    "phase": phase,
                    "session": session_id,
                    "sku": expected,
                    "elapsed_sec": round(elapsed, 3),
                    "issues": row_issues,
                    "answer": str(response.get("answer") or ""),
                    "products": [
                        {
                            "sku": product.get("sku"),
                            "name": product.get("name"),
                        }
                        for product in response.get("products") or []
                    ],
                    "debug": {
                        key: (response.get("debug") or {}).get(key)
                        for key in [
                            "intent",
                            "category",
                            "final_answer_source",
                            "response_llm_requested",
                            "response_llm_used",
                            "response_llm_output_accepted",
                            "response_llm_rejection_reason",
                        ]
                    },
                }
            )

    ordered = sorted(latencies)
    summary = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "api_url": API_URL,
        "requests": len(latencies),
        "sessions": len(sessions),
        "concurrency_workers": MAX_WORKERS,
        "issues": len(issues),
        "cross_session_leaks": sum(
            "чуж" in issue or "неверный session_id" in issue for issue in issues
        ),
        "latency_p50_sec": round(statistics.median(ordered), 3),
        "latency_p95_sec": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
        "latency_max_sec": round(max(ordered), 3),
        "metrics": dict(metrics),
        "final_answer_sources": dict(sources),
        "safe_llm_rejection_reasons": dict(rejection_reasons),
        "issues_list": issues,
    }
    _write_report(summary, phase_rows)
    return summary


def _write_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Конкурентный live-LLM QA после исправлений",
        "",
        f"API: `{summary['api_url']}`.",
        f"Запросов: **{summary['requests']}**, независимых сессий: **{summary['sessions']}**.",
        f"Параллельных workers: **{summary['concurrency_workers']}**.",
        f"Ошибок инвариантов: **{summary['issues']}**.",
        f"Утечек между сессиями: **{summary['cross_session_leaks']}**.",
        f"Latency p50/p95/max: **{summary['latency_p50_sec']} / {summary['latency_p95_sec']} / {summary['latency_max_sec']} с**.",
        f"LLM-метрики: `{json.dumps(summary['metrics'], ensure_ascii=False)}`.",
        f"Источники финальных ответов: `{json.dumps(summary['final_answer_sources'], ensure_ascii=False)}`.",
        f"Причины безопасных отклонений LLM: `{json.dumps(summary['safe_llm_rejection_reasons'], ensure_ascii=False)}`.",
        "",
        "## Проблемы",
        "",
    ]
    if summary["issues_list"]:
        lines.extend(f"- {issue}" for issue in summary["issues_list"])
    else:
        lines.append("Автоматические инварианты прошли без ошибок.")
    lines.extend(
        [
            "",
            "## Ходы",
            "",
            "| Фаза | Сессия | Ожидаемый SKU | Сек. | Результат |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in rows:
        result = "; ".join(row["issues"]) if row["issues"] else "PASS"
        lines.append(
            f"| {row['phase']} | `{row['session']}` | `{row['sku']}` | "
            f"{row['elapsed_sec']} | {result} |"
        )
        if row["issues"]:
            lines.append("")
            lines.append(f"Ответ: `{row['answer'][:500]}`")
            lines.append(f"Карточки: `{json.dumps(row['products'], ensure_ascii=False)}`")
            lines.append(f"Debug: `{json.dumps(row['debug'], ensure_ascii=False)}`")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"report={REPORT_PATH}")
    raise SystemExit(1 if result["issues"] else 0)
