#!/usr/bin/env python3
"""Exercise the bounded V2 product-fact seam through the real widget /chat API.

The harness intentionally keeps catalogue selection in Legacy for protected
Preview scenarios that require previously shown cards.  That is the migration
cell under test: existing widget selection followed by a V2-owned direct fact.
Credentials and the QA token are never written to the report artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "widget_product_fact_v2_2026-08-28"


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "pump_ordinal",
        "title": "Насос · монтажная длина первой карточки",
        "turns": (
            (
                "Циркуляционный насос: расчётный расход 1,5 м3/ч, "
                "напор 4 м, схема радиаторная"
            ),
            "Какая у первого монтажная длина?",
        ),
        "preview_setup_turns": 1,
    },
    {
        "id": "pp_fiber",
        "title": "PP-FIBER PN 20 · температура и давление",
        "turns": (
            "Какая максимальная рабочая температура у трубы PP-FIBER PN 20?",
            "А какое давление при радиаторном отоплении?",
        ),
        "preview_setup_turns": 0,
    },
    {
        "id": "boiler_power",
        "title": "Котёл · обоснование мощности",
        "turns": (
            "Нужен газовый котёл на дом 150 квадратов",
            "А почему именно такая мощность?",
        ),
        "preview_setup_turns": 1,
    },
    {
        "id": "partial_sku",
        "title": "Термоголовка · частичный SKU",
        "turns": (
            "А головка VT.1500 подойдёт к термостатическому клапану?",
        ),
        "preview_setup_turns": 0,
    },
    {
        "id": "unknown_fact",
        "title": "Неизвестная характеристика · fail closed",
        "turns": ("Какой у VRS.254.18.0 цвет корпуса?",),
        "preview_setup_turns": 0,
    },
    {
        "id": "ambiguous_product",
        "title": "Неоднозначное местоимение · без глобального поиска",
        "turns": ("Какая у него монтажная длина?",),
        "preview_setup_turns": 0,
    },
)


def _fingerprint(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest.translate(str.maketrans("0123456789abcdef", "ghijklmnopqrstuv"))


def _post(
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
            return {
                "ok": True,
                "http_status": response.status,
                "latency_sec": round(time.monotonic() - started, 3),
                "response": json.loads(response.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": exc.code,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": exc.read().decode("utf-8", "replace")[:1000],
        }
    except Exception as exc:  # noqa: BLE001 - gate must checkpoint failures
        return {
            "ok": False,
            "http_status": None,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _load_traces(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not path.is_file():
        return grouped
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        grouped.setdefault(str(trace.get("session_fingerprint") or ""), []).append(trace)
    for traces in grouped.values():
        traces.sort(key=lambda item: str(item.get("timestamp") or ""))
    return grouped


def _trace_excerpt(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not trace:
        return {"found": False}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    passport = trace.get("passport_events") or []
    embeddings = trace.get("embedding_calls") or []
    candidate_event = next(
        (item for item in reversed(passport) if item.get("event") == "product_fact_v2_candidate"),
        None,
    )
    retrieval = next(
        (
            item
            for item in passport
            if item.get("event") == "passport_retrieval"
            and item.get("flow") == "v2_product_fact"
        ),
        None,
    )
    gate = next(
        (item for item in passport if item.get("event") == "product_fact_evidence_gate"),
        None,
    )
    return {
        "found": True,
        "qa_mode": (trace.get("runtime") or {}).get("qa_mode"),
        "owner": decision.get("owner_candidate"),
        "execution_mode": decision.get("execution_mode"),
        "decision_reason_codes": decision.get("reason_codes") or [],
        "semantic_status": (trace.get("turn_understanding") or {}).get("status"),
        "candidate_event": candidate_event,
        "retrieval": retrieval,
        "evidence_gate": gate,
        "embedding_succeeded": any(item.get("succeeded") for item in embeddings),
    }


def _attach_traces(results: dict[str, Any], telemetry_path: Path) -> None:
    grouped = _load_traces(telemetry_path)
    for run in results["runs"]:
        traces = grouped.get(_fingerprint(run["session_id"]), [])
        used: set[int] = set()
        for turn in run["turns"]:
            chosen = None
            for index, trace in enumerate(traces):
                if index in used:
                    continue
                if str(trace.get("current_message") or "") != turn["message"]:
                    continue
                if str((trace.get("runtime") or {}).get("qa_mode") or "") != turn["qa_mode"]:
                    continue
                chosen = trace
                used.add(index)
                break
            turn["telemetry"] = _trace_excerpt(chosen)


def _checks(run: dict[str, Any]) -> list[dict[str, Any]]:
    scenario = run["scenario_id"]
    turns = run["turns"]
    final = turns[-1]
    answer = str((final["result"].get("response") or {}).get("answer") or "")
    low = answer.casefold()
    telemetry = final.get("telemetry") or {}
    checks: list[tuple[str, bool]] = [
        ("http_200", bool(final["result"].get("ok"))),
    ]
    mode = run["requested_mode"]
    if mode == "legacy":
        return [{"name": name, "passed": passed} for name, passed in checks]
    candidate_event = telemetry.get("candidate_event") or {}
    if mode == "shadow":
        checks.extend(
            (
                ("visible_owner_remains_legacy", telemetry.get("owner") == "legacy"),
                ("shadow_v2_candidate", candidate_event.get("status") == "accepted"),
            )
        )
    if mode == "v2_preview":
        checks.append(("owner_v2", telemetry.get("owner") == "v2"))
        checks.append(
            (
                "v2_candidate_event",
                bool((telemetry.get("candidate_event") or {}).get("status") == "accepted"),
            )
        )
    if scenario == "pump_ordinal":
        if mode == "v2_preview":
            checks.extend(
                (
                    ("value_180_mm", "180 мм" in low),
                    ("sku_vrs_254", "vrs.254.18.0" in low),
                )
            )
        if mode in {"shadow", "v2_preview"}:
            retrieval = telemetry.get("retrieval") or {}
            checks.extend(
                (
                    ("predicate_installation_length", retrieval.get("predicate") == "installation_length_mm"),
                    ("correct_passport_scope", "VRS-0725.pdf" in (retrieval.get("document_scope") or [])),
                    ("embedding_called", bool(telemetry.get("embedding_succeeded"))),
                )
            )
    elif scenario == "pp_fiber":
        first_answer = str(
            (turns[0]["result"].get("response") or {}).get("answer") or ""
        ).casefold()
        if mode == "v2_preview":
            checks.extend(
                (
                    ("temperature_90_c", "90 °c" in first_answer),
                    ("pressure_6_bar", "6 бар" in low),
                )
            )
        if mode in {"shadow", "v2_preview"}:
            first_telemetry = turns[0].get("telemetry") or {}
            checks.append(
                (
                    "pressure_predicate",
                    (telemetry.get("retrieval") or {}).get("predicate")
                    == "radiator_heating_pressure_bar",
                )
            )
            checks.append(
                (
                    "temperature_predicate",
                    ((first_telemetry.get("candidate_event") or {}).get("predicate"))
                    == "maximum_operating_temperature_c",
                )
            )
    elif scenario == "boiler_power":
        if mode == "v2_preview":
            checks.extend(
                (
                    ("no_gas_pressure_quote", "давлен" not in low),
                    ("explains_heat_loss_boundary", "теплопотер" in low),
                )
            )
        checks.append(
            ("power_rationale_predicate", candidate_event.get("predicate") == "selection_power_rationale")
        )
    elif scenario == "partial_sku":
        if mode == "v2_preview":
            checks.extend(
                (
                    ("canonical_partial_sku", "vt.1500.0.0" in low),
                    ("not_reported_missing", "артикула нет" not in low),
                    ("compatibility_not_claimed", "совместимость обещать не буду" in low),
                )
            )
        checks.append(
            ("candidate_canonical_partial_sku", candidate_event.get("canonical_sku") == "VT.1500.0.0")
        )
    elif scenario == "unknown_fact":
        if mode == "v2_preview":
            checks.extend(
                (
                    ("typed_refusal", "не удалось подтвердить" in low),
                    ("no_invented_colour", "цвет корпуса —" not in low),
                )
            )
        checks.append(
            ("unsupported_predicate_typed", candidate_event.get("predicate") == "unsupported_product_fact")
        )
    elif scenario == "ambiguous_product":
        if mode == "v2_preview":
            checks.append(("asks_for_product_scope", "не могу однозначно определить" in low))
        checks.append(("ambiguous_evidence_status", candidate_event.get("evidence_status") == "ambiguous"))
        if mode == "v2_preview":
            checks.append(("no_global_v2_retrieval", telemetry.get("retrieval") is None))
    return [{"name": name, "passed": passed} for name, passed in checks]


def _render(results: dict[str, Any]) -> str:
    lines = [
        "# Целевой gate: V2 direct product fact",
        "",
        f"Запуск: {results['created_at']}",
        f"Повторов: {results['repetitions']}",
        "",
        "Preview-сценарии с карточками проверяют ограниченный интеграционный шов: "
        "Legacy показывает товар, прямой вопрос отвечает V2.",
        "",
    ]
    for mode in ("legacy", "shadow", "v2_preview"):
        mode_runs = [item for item in results["runs"] if item["requested_mode"] == mode]
        passed = sum(all(check["passed"] for check in item["checks"]) for item in mode_runs)
        lines.extend((f"## {mode}", "", f"Успешно: {passed}/{len(mode_runs)}", ""))
        for run in mode_runs:
            failures = [check["name"] for check in run["checks"] if not check["passed"]]
            status = "PASS" if not failures else "FAIL: " + ", ".join(failures)
            answer = str(
                (run["turns"][-1]["result"].get("response") or {}).get("answer") or ""
            ).replace("\n", " ")
            lines.extend(
                (
                    f"- {run['scenario_id']} · повтор {run['repetition']}: {status}",
                    f"  - ответ: {answer[:420]}",
                )
            )
        lines.append("")
    total_checks = [
        check
        for run in results["runs"]
        for check in run["checks"]
    ]
    failed = [check for check in total_checks if not check["passed"]]
    lines.extend(
        (
            "## Итог",
            "",
            f"Проверок: {len(total_checks)}; успешно: {len(total_checks) - len(failed)}; "
            f"ошибок: {len(failed)}.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--telemetry-path", type=Path, required=True)
    parser.add_argument("--modes", default="legacy,shadow,v2_preview")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--evaluate-existing", type=Path)
    args = parser.parse_args()

    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token and args.evaluate_existing is None:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    if any(item not in {"legacy", "shadow", "v2_preview"} for item in modes):
        raise SystemExit("modes must be legacy, shadow and/or v2_preview")

    if args.evaluate_existing is not None:
        results = json.loads(args.evaluate_existing.read_text(encoding="utf-8"))
        _attach_traces(results, args.telemetry_path)
        for run in results["runs"]:
            run["checks"] = _checks(run)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / "targeted_responses.json"
        report_path = args.output_dir / "targeted_report.md"
        json_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_path.write_text(_render(results), encoding="utf-8")
        failed = [
            check
            for run in results["runs"]
            for check in run["checks"]
            if not check["passed"]
        ]
        print(f"JSON: {json_path}")
        print(f"REPORT: {report_path}")
        print(f"FAILED CHECKS: {len(failed)}")
        return 0 if not failed else 2

    results: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url.rstrip("/"),
        "repetitions": args.repetitions,
        "runs": [],
    }
    run_id = uuid.uuid4().hex[:10]
    total = len(modes) * len(SCENARIOS) * args.repetitions
    completed = 0
    for mode in modes:
        for scenario in SCENARIOS:
            for repetition in range(1, args.repetitions + 1):
                session_id = (
                    f"product-fact-{run_id}-{mode}-{scenario['id']}-r{repetition}"
                )
                run = {
                    "requested_mode": mode,
                    "scenario_id": scenario["id"],
                    "title": scenario["title"],
                    "repetition": repetition,
                    "session_id": session_id,
                    "turns": [],
                }
                results["runs"].append(run)
                for turn_index, message in enumerate(scenario["turns"], start=1):
                    qa_mode = mode
                    if (
                        mode == "v2_preview"
                        and turn_index <= int(scenario["preview_setup_turns"])
                    ):
                        qa_mode = "legacy"
                    result = _post(
                        args.base_url,
                        token,
                        session_id=session_id,
                        client_turn_id=f"{session_id}-t{turn_index:02d}",
                        message=message,
                        qa_mode=qa_mode,
                        timeout=args.timeout,
                    )
                    run["turns"].append(
                        {
                            "turn": turn_index,
                            "message": message,
                            "qa_mode": qa_mode,
                            "result": result,
                        }
                    )
                    if args.pause:
                        time.sleep(args.pause)
                completed += 1
                answer = str(
                    (run["turns"][-1]["result"].get("response") or {}).get("answer") or ""
                ).replace("\n", " ")
                print(
                    f"[{completed:02d}/{total}] {mode:10s} {scenario['id']:18s} "
                    f"r{repetition}: {answer[:100]}",
                    flush=True,
                )

    _attach_traces(results, args.telemetry_path)
    for run in results["runs"]:
        run["checks"] = _checks(run)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "targeted_responses.json"
    report_path = args.output_dir / "targeted_report.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_render(results), encoding="utf-8")
    failed = [
        check
        for run in results["runs"]
        for check in run["checks"]
        if not check["passed"]
    ]
    print(f"JSON: {json_path}", flush=True)
    print(f"REPORT: {report_path}", flush=True)
    print(f"FAILED CHECKS: {len(failed)}", flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
