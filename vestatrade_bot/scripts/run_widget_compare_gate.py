#!/usr/bin/env python3
"""One targeted real-widget gate for the grounded V2 Compare seam.

This is intentionally not a persona matrix: it exercises only Compare and
the required cards -> compare -> ordinal fact continuation once per mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "widget_compare_v2_2026-08-29"

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "sewer_compare",
        "turns": (
            "В частном доме пахнет из туалета, нужна труба на улицу.",
            "От дома до септика, наружная канализация.",
            "Покажите варианты.",
            "Сравните их.",
        ),
        "selection_turn": 3,
        "compare_turn": 4,
        "needs_two_cards": True,
    },
    {
        "id": "pump_compare_then_fact",
        "turns": (
            "Нужен циркуляционный насос для радиаторного отопления, расход 1,5 м3/ч, напор 6 метров.",
            "Покажите варианты.",
            "Сравните их.",
            "Какая у первого монтажная длина?",
        ),
        "selection_turn": 2,
        "compare_turn": 3,
        "fact_turn": 4,
        "needs_two_cards": True,
    },
    {
        "id": "base_compare",
        "turns": (
            "Нужны шаровые краны BASE 1/2 вн-вн.",
            "Покажите варианты.",
            "Сравните их.",
        ),
        "selection_turn": 2,
        "compare_turn": 3,
        "needs_two_cards": True,
    },
    {
        "id": "single_ppr_compare",
        "turns": (
            "Нужна ППР 25 армированная стекловолокном на радиаторную магистраль, подача 90 °С.",
            "Покажите варианты.",
            "Сравните их.",
        ),
        "selection_turn": 2,
        "compare_turn": 3,
        "needs_two_cards": False,
    },
    {
        "id": "cheapest_visible",
        "turns": (
            "Нужен циркуляционный насос для радиаторного отопления, расход 1,5 м3/ч, напор 6 метров.",
            "Покажите варианты.",
            "Какой из показанных дешевле?",
        ),
        "selection_turn": 2,
        "compare_turn": 3,
        "needs_two_cards": True,
        "cheapest": True,
    },
    {
        "id": "compare_without_scope",
        "turns": ("Сравните их.",),
        "compare_turn": 1,
        "needs_two_cards": False,
        "no_scope": True,
    },
)


def _fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest().translate(
        str.maketrans("0123456789abcdef", "ghijklmnopqrstuv")
    )


def _post(base_url: str, token: str, *, session_id: str, turn_id: str, message: str, mode: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(
        {"session_id": session_id, "client_turn_id": turn_id, "message": message, "qa_mode": mode}
    ).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Dialogue-QA-Token": token},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"ok": True, "http_status": response.status, "latency_sec": round(time.monotonic() - started, 3), "response": json.loads(response.read())}
    except Exception as exc:  # gate records a transport failure without secrets
        return {"ok": False, "latency_sec": round(time.monotonic() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


def _traces(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not path.is_file():
        return grouped
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        grouped.setdefault(str(trace.get("session_fingerprint") or ""), []).append(trace)
    return grouped


def _telemetry(trace: dict[str, Any] | None) -> dict[str, Any]:
    if trace is None:
        return {"found": False}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    return {
        "found": True,
        "owner": decision.get("owner_candidate"),
        "mode": decision.get("execution_mode"),
        "candidate": cutover.get("candidate") or {},
        "selection": cutover.get("selection_delivery"),
        "comparison": cutover.get("comparison_delivery"),
        "embedding_called": any(item.get("succeeded") for item in (trace.get("embedding_calls") or [])),
    }


def _check(run: dict[str, Any]) -> list[dict[str, Any]]:
    turns = run["turns"]
    scenario = run["scenario"]
    mode = run["mode"]
    checks: list[tuple[str, bool]] = [("http_200", all(item["result"].get("ok") for item in turns))]
    compare = turns[scenario["compare_turn"] - 1]
    telemetry = compare.get("telemetry") or {}
    comparison = telemetry.get("comparison") or {}
    if mode == "legacy":
        return [{"name": name, "passed": passed} for name, passed in checks]
    if mode == "shadow":
        checks.extend((
            ("visible_owner_legacy", telemetry.get("owner") == "legacy"),
            ("shadow_did_not_update_scope", not bool(comparison.get("customer_visible_scope_preserved"))),
        ))
        return [{"name": name, "passed": passed} for name, passed in checks]
    checks.extend((
        ("compare_owner_v2", telemetry.get("owner") == "v2"),
        ("comparison_gate_passed", bool(comparison.get("outcome_gate_passed"))),
        (
            "comparison_result_is_structurally_deliverable",
            comparison.get("status") in {"compared", "need_clarification", "not_comparable"}
            and bool(comparison.get("outcome_gate_passed")),
        ),
    ))
    if scenario.get("no_scope"):
        checks.append(("one_scope_question", comparison.get("status") == "need_clarification"))
    else:
        selection = turns[scenario["selection_turn"] - 1]
        shown = (selection["result"].get("response") or {}).get("products") or []
        compared = comparison.get("compared_skus") or []
        if scenario.get("needs_two_cards"):
            checks.append(("at_least_two_visible_cards", len(shown) >= 2))
        checks.append(("comparison_scope_matches_visible_order", compared == [item.get("sku") for item in shown] if len(shown) >= 2 else comparison.get("status") == "need_clarification"))
        if scenario.get("cheapest") and len(shown) >= 2:
            expected = min(shown, key=lambda item: float(item["price"]))["sku"]
            recommendation = comparison.get("recommendation") or {}
            checks.append(("proved_cheapest", recommendation.get("sku") == expected))
    if "fact_turn" in scenario:
        fact = turns[scenario["fact_turn"] - 1]
        fact_answer = str((fact["result"].get("response") or {}).get("answer") or "").casefold()
        checks.extend((
            ("ordinal_fact_owner_v2", (fact.get("telemetry") or {}).get("owner") == "v2"),
            ("ordinal_fact_has_mm", "мм" in fact_answer),
            ("ordinal_fact_embedding_called", bool((fact.get("telemetry") or {}).get("embedding_called"))),
        ))
    return [{"name": name, "passed": passed} for name, passed in checks]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--telemetry-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--modes", default="legacy,shadow,v2_preview")
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.4)
    parser.add_argument(
        "--reconcile-input",
        type=Path,
        help="Attach telemetry and recalculate an already completed run; sends no /chat requests.",
    )
    args = parser.parse_args()
    modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
    if any(item not in {"legacy", "shadow", "v2_preview"} for item in modes):
        raise SystemExit("unsupported mode")
    if args.reconcile_input is not None:
        results = json.loads(args.reconcile_input.read_text(encoding="utf-8"))
    else:
        token = os.environ.get("DIALOGUE_QA_TOKEN", "")
        if not token:
            raise SystemExit("DIALOGUE_QA_TOKEN is required")
        results = {"created_at": datetime.now(timezone.utc).astimezone().isoformat(), "runs": []}
        run_id = uuid.uuid4().hex[:10]
        scenarios = tuple(
            scenario
            for scenario in SCENARIOS
            if not args.scenario_ids or scenario["id"] in set(args.scenario_ids)
        )
        if not scenarios:
            raise SystemExit("no requested scenario")
        for mode in modes:
            for scenario in scenarios:
                session_id = f"compare-{run_id}-{mode}-{scenario['id']}"
                run: dict[str, Any] = {"mode": mode, "scenario": scenario, "session_id": session_id, "turns": []}
                results["runs"].append(run)
                for index, message in enumerate(scenario["turns"], start=1):
                    run["turns"].append({"turn": index, "message": message, "result": _post(args.base_url, token, session_id=session_id, turn_id=f"{session_id}-t{index}", message=message, mode=mode, timeout=args.timeout)})
                    if args.pause:
                        time.sleep(args.pause)
    traces = _traces(args.telemetry_path)
    for run in results["runs"]:
        for index, turn in enumerate(run["turns"]):
            trace_list = traces.get(_fingerprint(run["session_id"]), [])
            turn["telemetry"] = _telemetry(trace_list[index] if index < len(trace_list) else None)
        run["checks"] = _check(run)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "responses.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    checks = [item for run in results["runs"] for item in run["checks"]]
    failures = [item for item in checks if not item["passed"]]
    lines = ["# Targeted grounded V2 Compare gate", "", f"Runs: {len(results['runs'])}; checks: {len(checks)}; failures: {len(failures)}.", "", "## Failed checks", ""]
    lines.extend(f"- {item['name']}" for item in failures) if failures else lines.append("- None.")
    (args.output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"FAILED CHECKS: {len(failures)}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
