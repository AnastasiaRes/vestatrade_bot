#!/usr/bin/env python3
"""Run the same persona dialogues through protected widget /chat QA modes.

The script uses the public HTTP contract used by ``widget-loader.js``.  It
keeps a separate session per persona and mode, writes a checkpoint after every
turn, and enriches the final report from the diagnostic JSONL trace without
copying the QA token or provider credentials into artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "widget_full_run_2026-08-28"

DIALOGUES = [
    (
        "Новичок · канализация",
        "не знает терминов, описывает бытовыми словами",
        [
            "Здравствуйте! У меня в частном доме воняет из туалета, наверное труба плохая",
            "Наверное надо менять. А какую брать, серую или рыжую?",
            "Мне на улицу, от дома до септика. Покажите что есть",
            "Сравните их, что лучше?",
        ],
    ),
    (
        "Монтажник · трубы",
        "знает терминологию, формулирует точно",
        [
            "Нужна ППР 25 армированная стекловолокном на радиаторную магистраль, подача 90 °С",
            "Покажите варианты",
            "Сравните по классу эксплуатации",
        ],
    ),
    (
        "Прораб · краны",
        "думает партиями и деньгами",
        [
            "Нужны шаровые краны BASE 1/2 вн-вн, штук двадцать",
            "Чем 214-я серия отличается от 217-й?",
            "Сколько это выйдет за двадцать штук?",
        ],
    ),
    (
        "Новичок · радиаторы",
        "не понимает разницы материалов",
        [
            "Хочу батарею в комнату 18 квадратов, что посоветуете?",
            "А чем алюминиевый радиатор от биметаллического отличается?",
            "Покажите что есть",
        ],
    ),
    (
        "Проектировщик · насосы",
        "оперирует рабочей точкой",
        [
            "Циркуляционный насос: расчётный расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "Сравните их между собой",
            "Какая у первого монтажная длина?",
        ],
    ),
    (
        "Сомневающийся · котлы",
        "переспрашивает и требует обоснований",
        [
            "Нужен газовый котёл на дом 150 квадратов",
            "А почему именно такая мощность?",
            "А вы уверены? Мне сосед говорил что надо больше",
        ],
    ),
    (
        "Монтажник · радиаторная арматура",
        "проверяет совместимость",
        [
            "Нужен термостатический клапан прямой 1/2 и головка к нему",
            "Какая резьба под термоголовку у этого клапана?",
            "А головка VT.1500 подойдёт?",
        ],
    ),
    (
        "Новичок · фитинги",
        "не знает как соединять",
        [
            "Мне надо полипропиленовую трубу присоединить к железной, что купить?",
            "Труба 25 миллиметров, резьба дюймовая",
            "Покажите варианты",
        ],
    ),
    (
        "Снабженец · котельная",
        "мыслит комплектом",
        [
            "Собираю котельную на 200 квадратов, что нужно кроме котла?",
            "Покажите насос и краны для обвязки",
        ],
    ),
    (
        "Дотошный · трубы",
        "проверяет цифры и сравнивает",
        [
            "Какая максимальная рабочая температура у трубы PP-FIBER PN 20?",
            "А какое давление при радиаторном отоплении?",
            "Сравните её с PP-ALUX",
        ],
    ),
]


def _post_chat(
    base_url: str,
    token: str,
    *,
    session_id: str,
    client_turn_id: str,
    message: str,
    qa_mode: str,
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "session_id": session_id,
            "client_turn_id": client_turn_id,
            "message": message,
            "qa_mode": qa_mode,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Dialogue-QA-Token": token,
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "ok": True,
                "http_status": response.status,
                "latency_sec": round(time.monotonic() - started, 3),
                "response": payload,
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": exc.code,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": exc.read().decode("utf-8", "replace")[:1000],
        }
    except Exception as exc:  # noqa: BLE001 - QA harness must checkpoint errors
        return {
            "ok": False,
            "http_status": None,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _health(base_url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/health", timeout=timeout
        ) as response:
            return {
                "ok": response.status == 200,
                "http_status": response.status,
                "latency_sec": round(time.monotonic() - started, 3),
                "payload": json.loads(response.read().decode("utf-8")),
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _session_fingerprint(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest.translate(str.maketrans("0123456789abcdef", "ghijklmnopqrstuv"))


def _load_traces(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if not path.is_file():
        return grouped
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        runtime = trace.get("runtime") or {}
        key = (str(runtime.get("qa_mode") or ""), str(trace.get("session_fingerprint") or ""))
        grouped[key].append(trace)
    for traces in grouped.values():
        traces.sort(key=lambda item: str(item.get("timestamp") or ""))
    return grouped


def _trace_summary(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not trace:
        return {"trace_found": False}
    semantic = trace.get("turn_understanding") or {}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    candidate = cutover.get("candidate") or {}
    parity = cutover.get("parity") or {}
    v2_action = trace.get("v2_next_action") or {}
    primary = v2_action.get("primary") or {}
    llm_events = trace.get("llm_calls") or []
    completions = [event for event in llm_events if event.get("event") == "completion"]
    embeddings = trace.get("embedding_calls") or []
    passport = trace.get("passport_events") or []
    return {
        "trace_found": True,
        "duration_ms": trace.get("duration_ms"),
        "semantic_status": semantic.get("status"),
        "semantic_output_accepted": semantic.get("output_accepted"),
        "semantic_rejection_reason": semantic.get("rejection_reason"),
        "semantic_structural_repairs": semantic.get("structural_repairs") or [],
        "v2_status": (trace.get("dialogue_v2_shadow") or {}).get("status"),
        "v2_action": primary.get("kind"),
        "stage5_error": trace.get("stage5_error"),
        "owner": decision.get("owner_candidate"),
        "execution_mode": decision.get("execution_mode"),
        "decision_reason_codes": decision.get("reason_codes") or [],
        "candidate_eligible": candidate.get("eligible_for_delivery"),
        "candidate_rejection_reason_codes": candidate.get("rejection_reason_codes") or [],
        "parity_status": parity.get("status"),
        "parity_severity": parity.get("severity"),
        "llm_events": len(llm_events),
        "llm_calls": len(completions),
        "llm_cost_usd": sum(float(event.get("cost_usd") or 0) for event in completions),
        "llm_prompt_tokens": sum(
            int((event.get("usage") or {}).get("prompt_tokens") or 0)
            for event in completions
        ),
        "llm_completion_tokens": sum(
            int((event.get("usage") or {}).get("completion_tokens") or 0)
            for event in completions
        ),
        "embedding_calls": len(embeddings),
        "embedding_successes": sum(event.get("succeeded") is True for event in embeddings),
        "embedding_failures": sum(event.get("succeeded") is False for event in embeddings),
        "passport_events": passport,
        "search_events": trace.get("search_plan_events") or [],
    }


def _enrich(results: dict[str, Any], telemetry_path: Path) -> None:
    traces = _load_traces(telemetry_path)
    for mode in results["modes"]:
        for dialogue in mode["dialogues"]:
            key = (mode["mode"], _session_fingerprint(dialogue["session_id"]))
            matching = traces.get(key, [])
            for index, turn in enumerate(dialogue["turns"]):
                turn["trace"] = _trace_summary(
                    matching[index] if index < len(matching) else None
                )


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def _mode_summary(mode: dict[str, Any]) -> dict[str, Any]:
    turns = [turn for dialogue in mode["dialogues"] for turn in dialogue["turns"]]
    ok_turns = [turn for turn in turns if turn["result"].get("ok")]
    latencies = [float(turn["result"].get("latency_sec") or 0) for turn in ok_turns]
    answers = [str(turn["result"].get("response", {}).get("answer") or "") for turn in ok_turns]
    traces = [turn.get("trace") or {} for turn in turns]
    rejection_codes: Counter[str] = Counter()
    semantic_statuses: Counter[str] = Counter()
    owners: Counter[str] = Counter()
    parity: Counter[str] = Counter()
    repairs: Counter[str] = Counter()
    for trace in traces:
        if trace.get("semantic_status"):
            semantic_statuses[str(trace["semantic_status"])] += 1
        if trace.get("owner"):
            owners[str(trace["owner"])] += 1
        if trace.get("parity_status"):
            parity[str(trace["parity_status"])] += 1
        rejection_codes.update(trace.get("candidate_rejection_reason_codes") or [])
        repairs.update(trace.get("semantic_structural_repairs") or [])
    return {
        "turns": len(turns),
        "http_ok": len(ok_turns),
        "http_errors": len(turns) - len(ok_turns),
        "turns_with_products": sum(
            bool(turn["result"].get("response", {}).get("products")) for turn in ok_turns
        ),
        "passport_quote_answers": sum("По паспорту:" in answer for answer in answers),
        "comparison_answers": sum("Сравниваю" in answer for answer in answers),
        "avg_latency_sec": round(statistics.mean(latencies), 3) if latencies else 0.0,
        "p95_latency_sec": _percentile_95(latencies),
        "semantic_statuses": dict(sorted(semantic_statuses.items())),
        "semantic_repairs": dict(sorted(repairs.items())),
        "owners": dict(sorted(owners.items())),
        "candidate_eligible": sum(trace.get("candidate_eligible") is True for trace in traces),
        "candidate_rejection_codes": dict(sorted(rejection_codes.items())),
        "parity_statuses": dict(sorted(parity.items())),
        "llm_calls": sum(int(trace.get("llm_calls") or 0) for trace in traces),
        "llm_events": sum(int(trace.get("llm_events") or 0) for trace in traces),
        "llm_cost_usd": round(
            sum(float(trace.get("llm_cost_usd") or 0) for trace in traces), 8
        ),
        "llm_prompt_tokens": sum(
            int(trace.get("llm_prompt_tokens") or 0) for trace in traces
        ),
        "llm_completion_tokens": sum(
            int(trace.get("llm_completion_tokens") or 0) for trace in traces
        ),
        "embedding_calls": sum(int(trace.get("embedding_calls") or 0) for trace in traces),
        "embedding_successes": sum(int(trace.get("embedding_successes") or 0) for trace in traces),
        "embedding_failures": sum(int(trace.get("embedding_failures") or 0) for trace in traces),
        "passport_events": sum(len(trace.get("passport_events") or []) for trace in traces),
        "traces_found": sum(trace.get("trace_found") is True for trace in traces),
    }


def _render_markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Полный прогон виджет-бота: Legacy / Shadow / V2 Preview",
        "",
        f"Дата: {results['created_at']}",
        f"Маршрут: `{results['base_url']}/chat` (тот же контракт, что у widget-loader.js)",
        f"Сценариев: {len(DIALOGUES)}, ходов на режим: {sum(len(item[2]) for item in DIALOGUES)}",
        "",
        "## Конфигурация стенда",
        "",
        f"- health: `{json.dumps(results['health'], ensure_ascii=False)}`",
        f"- режимы: {', '.join(item['mode'] for item in results['modes'])}",
        "- QA-токен и ключ провайдера в отчёт не записываются.",
        "",
        "## Сводка",
        "",
        "| Режим | HTTP | С товарами | Цитаты паспорта | Сравнения | V2-владелец | Legacy-владелец | Semantic accepted | Embeddings ok/fail | avg / p95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in results["modes"]:
        summary = mode["summary"]
        lines.append(
            "| {mode} | {ok}/{turns} | {products} | {passport} | {compare} | "
            "{v2} | {legacy} | {semantic} | {emb_ok}/{emb_fail} | {avg:.1f}s / {p95:.1f}s |".format(
                mode=mode["mode"],
                ok=summary["http_ok"],
                turns=summary["turns"],
                products=summary["turns_with_products"],
                passport=summary["passport_quote_answers"],
                compare=summary["comparison_answers"],
                v2=summary["owners"].get("v2", 0),
                legacy=summary["owners"].get("legacy", 0),
                semantic=summary["semantic_statuses"].get("accepted", 0),
                emb_ok=summary["embedding_successes"],
                emb_fail=summary["embedding_failures"],
                avg=summary["avg_latency_sec"],
                p95=summary["p95_latency_sec"],
            )
        )
    for mode in results["modes"]:
        summary = mode["summary"]
        lines.extend(
            [
                "",
                f"## Диагностика: {mode['mode']}",
                "",
                f"- semantic statuses: `{json.dumps(summary['semantic_statuses'], ensure_ascii=False, sort_keys=True)}`",
                f"- semantic repairs: `{json.dumps(summary['semantic_repairs'], ensure_ascii=False, sort_keys=True)}`",
                f"- owners: `{json.dumps(summary['owners'], ensure_ascii=False, sort_keys=True)}`",
                f"- candidate rejection codes: `{json.dumps(summary['candidate_rejection_codes'], ensure_ascii=False, sort_keys=True)}`",
                f"- parity: `{json.dumps(summary['parity_statuses'], ensure_ascii=False, sort_keys=True)}`",
                f"- LLM completions: {summary['llm_calls']}; cost: ${summary['llm_cost_usd']:.6f}; prompt/completion tokens: {summary['llm_prompt_tokens']}/{summary['llm_completion_tokens']}",
                f"- diagnostic LLM events: {summary['llm_events']}; embedding calls: {summary['embedding_calls']}; passport events: {summary['passport_events']}",
            ]
        )
    for mode in results["modes"]:
        lines.extend(["", f"# Стенограмма: {mode['mode']}", ""])
        for dialogue in mode["dialogues"]:
            lines.extend(
                [
                    f"## {dialogue['title']}",
                    "",
                    f"_{dialogue['note']}_",
                    "",
                ]
            )
            for turn in dialogue["turns"]:
                lines.extend([f"**П:** {turn['message']}", ""])
                result = turn["result"]
                if not result.get("ok"):
                    lines.extend([f"**ОШИБКА:** {result.get('error')}", ""])
                    continue
                response = result.get("response") or {}
                lines.extend([f"**Б:** {str(response.get('answer') or '').strip()}", ""])
                products = response.get("products") or []
                if products:
                    items = "; ".join(
                        f"{item.get('sku')} {item.get('name')} — {item.get('price')} {item.get('currency')}"
                        for item in products[:5]
                    )
                    lines.extend([f"<sub>товары: {items}</sub>", ""])
                trace = turn.get("trace") or {}
                diagnostic = {
                    "latency_sec": result.get("latency_sec"),
                    "owner": trace.get("owner"),
                    "semantic": trace.get("semantic_status"),
                    "repairs": trace.get("semantic_structural_repairs"),
                    "v2_action": trace.get("v2_action"),
                    "eligible": trace.get("candidate_eligible"),
                    "rejections": trace.get("candidate_rejection_reason_codes"),
                    "passport_events": len(trace.get("passport_events") or []),
                }
                lines.extend(
                    [
                        f"<sub>{json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}</sub>",
                        "",
                    ]
                )
    return "\n".join(lines).rstrip() + "\n"


def _checkpoint(output_dir: Path, results: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "responses.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--telemetry-path", type=Path)
    parser.add_argument("--modes", default="legacy,shadow,v2_preview")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.8)
    args = parser.parse_args()

    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    allowed = {"legacy", "shadow", "v2_preview", "auto"}
    if any(mode not in allowed for mode in modes):
        raise SystemExit(f"Unsupported mode; allowed: {sorted(allowed)}")

    run_id = uuid.uuid4().hex[:10]
    results: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "health": _health(args.base_url, min(args.timeout, 30.0)),
        "modes": [],
    }
    if not results["health"].get("ok"):
        _checkpoint(args.output_dir, results)
        raise SystemExit(f"Stand is unavailable: {results['health']}")

    total_turns = len(modes) * sum(len(item[2]) for item in DIALOGUES)
    completed = 0
    for mode in modes:
        mode_result = {"mode": mode, "dialogues": []}
        results["modes"].append(mode_result)
        for dialogue_index, (title, note, messages) in enumerate(DIALOGUES, start=1):
            session_id = f"widget-{run_id}-{mode}-{dialogue_index:02d}"
            dialogue = {
                "title": title,
                "note": note,
                "session_id": session_id,
                "turns": [],
            }
            mode_result["dialogues"].append(dialogue)
            print(f"\n=== {mode} · {title}", flush=True)
            for turn_index, message in enumerate(messages, start=1):
                client_turn_id = f"{session_id}-t{turn_index:02d}"
                result = _post_chat(
                    args.base_url,
                    token,
                    session_id=session_id,
                    client_turn_id=client_turn_id,
                    message=message,
                    qa_mode=mode,
                    timeout=args.timeout,
                )
                dialogue["turns"].append(
                    {
                        "turn": turn_index,
                        "client_turn_id": client_turn_id,
                        "message": message,
                        "result": result,
                    }
                )
                completed += 1
                answer = str(result.get("response", {}).get("answer") or "")
                marker = "OK" if result.get("ok") else "ERROR"
                print(
                    f"[{completed:02d}/{total_turns}] {marker} {result.get('latency_sec', 0):6.1f}s "
                    f"{message[:48]} -> {' '.join(answer.split())[:100]}",
                    flush=True,
                )
                _checkpoint(args.output_dir, results)
                if args.pause > 0:
                    time.sleep(args.pause)

    if args.telemetry_path:
        _enrich(results, args.telemetry_path)
    for mode_result in results["modes"]:
        mode_result["summary"] = _mode_summary(mode_result)
    _checkpoint(args.output_dir, results)
    (args.output_dir / "report.md").write_text(
        _render_markdown(results),
        encoding="utf-8",
    )
    print(f"\nJSON: {args.output_dir / 'responses.json'}", flush=True)
    print(f"REPORT: {args.output_dir / 'report.md'}", flush=True)
    return 0 if all(
        mode["summary"]["http_errors"] == 0 for mode in results["modes"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
