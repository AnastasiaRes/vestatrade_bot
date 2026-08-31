#!/usr/bin/env python3
"""Exercise the native V2 single-category selection seam through real /chat."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "widget_selection_v2_2026-08-28" / "targeted"
_TRACE_SEQUENCE_PREFIX = "\x00turn-index:"

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "pump",
        "turns": (
            "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "Какая у первого монтажная длина?",
        ),
        "selection_turn": 2,
    },
    {
        "id": "ppr",
        "turns": (
            "Нужна ППР 25 армированная стекловолокном на радиаторную магистраль, подача 90 °С",
            "Покажите варианты",
        ),
        "selection_turn": 2,
    },
    {
        "id": "valves",
        "turns": (
            "Нужны шаровые краны BASE 1/2 вн-вн, двадцать штук",
            "Покажите варианты",
        ),
        "selection_turn": 2,
    },
    {
        "id": "sewer",
        "turns": (
            "Здравствуйте! У меня в частном доме воняет из туалета, наверное труба плохая",
            "Мне на улицу, от дома до септика",
            "Покажите варианты",
        ),
        "selection_turn": 3,
    },
    {
        "id": "insufficient",
        "turns": ("Нужна труба",),
        "selection_turn": 1,
    },
    {
        "id": "named_product",
        "turns": (
            "Покажите Насос циркуляционный VALTEC RS 25/4-180 с гайками",
        ),
        "selection_turn": 1,
    },
    {
        "id": "ppr_progressive_unknown",
        "turns": (
            "Нужна ППР 25 на отопление.",
            "Температуру сейчас не знаю.",
            "Подача 90 °C, давление 6 бар. Покажите подходящие варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
    },
    {
        "id": "pump_progressive_unknown",
        "turns": (
            "Нужен циркуляционный насос на отопление, напор 4 м.",
            "Монтажную длину пока не знаю.",
            "Расход 1,5 м3/ч, монтажная длина 180 мм. Покажите варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
    },
    {
        "id": "valves_progressive_unknown",
        "turns": (
            "Нужен шаровой кран BASE 1/2.",
            "Тип резьбы пока не знаю.",
            "Обе резьбы внутренние. Покажите варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
    },
    {
        "id": "sewer_progressive_unknown",
        "turns": (
            "Нужна канализационная труба от дома до септика.",
            "Диаметр пока не знаю.",
            "Диаметр 110 мм. Покажите варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
    },
    {
        "id": "radiator_progressive_unknown",
        "turns": (
            "Нужен радиатор с межосевым расстоянием 500 мм.",
            "Материал пока не знаю.",
            "Нужен биметаллический. Покажите варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
    },
    {
        "id": "boiler_progressive_unknown",
        "turns": (
            "Нужен газовый котёл 24 кВт.",
            "Количество контуров пока не знаю.",
            "Нужен двухконтурный с закрытой камерой. Покажите варианты.",
        ),
        "preliminary_turn": 2,
        "selection_turn": 3,
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
    mode: str,
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "session_id": session_id,
            "client_turn_id": client_turn_id,
            "message": message,
            "qa_mode": mode,
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
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "http_status": None,
            "latency_sec": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _traces(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    sequence_by_session: dict[str, int] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        fingerprint = str(trace.get("session_fingerprint") or "")
        message = str(trace.get("current_message") or "")
        if message:
            result[(fingerprint, message)] = trace
        turn_index = sequence_by_session.get(fingerprint, 0)
        result[(fingerprint, f"{_TRACE_SEQUENCE_PREFIX}{turn_index}")] = trace
        sequence_by_session[fingerprint] = turn_index + 1
    return result


def _trace_excerpt(trace: dict[str, Any] | None) -> dict[str, Any]:
    if trace is None:
        return {"found": False}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    selection = cutover.get("selection_delivery")
    passport = trace.get("passport_events") or []
    fact = next(
        (item for item in passport if item.get("event") == "product_fact_v2_candidate"),
        None,
    )
    return {
        "found": True,
        "owner": decision.get("owner_candidate"),
        "execution_mode": decision.get("execution_mode"),
        "candidate_eligible": ((cutover.get("candidate") or {}).get("eligible_for_delivery")),
        "selection": selection,
        "product_fact": fact,
        "embedding_succeeded": any(
            item.get("succeeded") for item in (trace.get("embedding_calls") or [])
        ),
    }


def _attach(results: dict[str, Any], telemetry: Path) -> None:
    traces = _traces(telemetry)
    for run in results["runs"]:
        fingerprint = _fingerprint(run["session_id"])
        for turn_index, turn in enumerate(run["turns"]):
            turn["telemetry"] = _trace_excerpt(
                traces.get((fingerprint, turn["message"]))
                or traces.get(
                    (fingerprint, f"{_TRACE_SEQUENCE_PREFIX}{turn_index}")
                )
            )


def _checks(run: dict[str, Any]) -> list[dict[str, Any]]:
    scenario = run["scenario_id"]
    mode = run["mode"]
    turns = run["turns"]
    selection_turn = turns[run["selection_turn"] - 1]
    selection_response = selection_turn["result"].get("response") or {}
    products = selection_response.get("products") or []
    telemetry = selection_turn.get("telemetry") or {}
    selection = telemetry.get("selection") or {}
    checks: list[tuple[str, bool]] = [
        ("all_http_200", all(item["result"].get("ok") for item in turns)),
    ]
    if mode == "legacy":
        return [{"name": name, "passed": passed} for name, passed in checks]
    if mode == "shadow":
        checks.extend(
            (
                ("visible_owner_legacy", telemetry.get("owner") == "legacy"),
                (
                    "shadow_did_not_update_visible_state",
                    not bool(selection.get("customer_visible_state_updated")),
                ),
            )
        )
        return [{"name": name, "passed": passed} for name, passed in checks]

    checks.extend(
        (
            ("selection_owner_v2", telemetry.get("owner") == "v2"),
            ("outcome_gate_passed", bool(selection.get("outcome_gate_passed"))),
            ("not_legacy_fallback", telemetry.get("execution_mode") == "v2_primary"),
        )
    )
    answer = str(selection_response.get("answer") or "").casefold()
    skus = [str(item.get("sku") or "") for item in products]
    if scenario == "pump":
        final = turns[-1]
        final_answer = str((final["result"].get("response") or {}).get("answer") or "")
        fact = (final.get("telemetry") or {}).get("product_fact") or {}
        checks.extend(
            (
                ("pump_cards_delivered", bool(products)),
                ("ordinal_owner_v2", (final.get("telemetry") or {}).get("owner") == "v2"),
                ("ordinal_has_proven_value", "мм" in final_answer),
                ("ordinal_matches_first_sku", fact.get("canonical_sku") == skus[0] if skus else False),
                ("embedding_called", bool((final.get("telemetry") or {}).get("embedding_succeeded"))),
            )
        )
    elif scenario == "ppr":
        checks.extend(
            (
                ("only_expected_ppr", skus == ["VTp.700.FB20.25"]),
                ("purpose_not_reasked", "уточните, пожалуйста, параметр «назначение трубы»" not in answer),
            )
        )
    elif scenario == "valves":
        checks.extend(
            (
                ("valve_cards_delivered", bool(products)),
                ("only_internal_internal", all(".n." in sku.casefold() for sku in skus)),
                ("connection_not_reasked", "уточните, пожалуйста, параметр «тип резьбового соединения»" not in answer),
            )
        )
    elif scenario == "sewer":
        checks.extend(
            (
                ("sewer_cards_delivered", bool(products)),
                ("typed_sewer_contract", selection.get("product_kind") == "sewer_pipe"),
                ("no_ppr_cards", all(not sku.casefold().startswith("vtp.") for sku in skus)),
            )
        )
    elif scenario == "insufficient":
        checks.extend(
            (
                ("one_critical_question", selection.get("status") == "need_clarification"),
                ("missing_pipe_service", selection.get("missing_critical_fact") == "pipe_service"),
                ("no_random_cards", not products),
            )
        )
    elif scenario == "named_product":
        checks.extend(
            (
                ("one_exact_named_card", skus == ["VRS.254.18.0"]),
                ("named_status_shown", selection.get("status") == "shown"),
            )
        )
    elif scenario == "ppr_progressive_unknown":
        preliminary_turn = turns[1]
        preliminary_response = preliminary_turn["result"].get("response") or {}
        preliminary_cards = preliminary_response.get("products") or []
        preliminary = (preliminary_turn.get("telemetry") or {}).get("selection") or {}
        applied_facts = {
            str(item.get("name")): item
            for item in (selection.get("applied_facts") or [])
        }
        checks.extend(
            (
                ("preliminary_owner_v2", (preliminary_turn.get("telemetry") or {}).get("owner") == "v2"),
                ("preliminary_cards_delivered", bool(preliminary_cards)),
                ("preliminary_outcome_gate_passed", bool(preliminary.get("outcome_gate_passed"))),
                ("no_repeated_temperature_question", "параметр «рабочая температура»" not in str(preliminary_response.get("answer") or "").casefold()),
                ("refined_cards_delivered", bool(products)),
                ("temperature_saved_for_refined_search", applied_facts.get("operating_temperature_c", {}).get("value") == 90),
                ("pressure_saved_for_refined_search", applied_facts.get("operating_pressure_bar", {}).get("value") == 6),
            )
        )
    elif scenario == "pump_progressive_unknown":
        preliminary_turn = turns[1]
        preliminary_response = preliminary_turn["result"].get("response") or {}
        preliminary_cards = preliminary_response.get("products") or []
        preliminary = (preliminary_turn.get("telemetry") or {}).get("selection") or {}
        applied_facts = {
            str(item.get("name")): item
            for item in (selection.get("applied_facts") or [])
        }
        checks.extend(
            (
                ("preliminary_owner_v2", (preliminary_turn.get("telemetry") or {}).get("owner") == "v2"),
                ("flow_is_requested_before_cards", preliminary.get("status") == "need_clarification"),
                ("no_phantom_preliminary_cards", not preliminary_cards),
                ("no_phantom_preliminary_scope", not bool(preliminary.get("customer_visible_state_updated"))),
                ("refined_cards_delivered", bool(products)),
                ("refined_state_updated", bool(selection.get("customer_visible_state_updated"))),
                ("typed_circulation_pump", selection.get("product_kind") == "circulation_pump"),
                ("head_saved_for_refined_search", applied_facts.get("duty_point_head_m", {}).get("value") == 4),
                ("flow_saved_for_refined_search", applied_facts.get("duty_point_flow_l_h", {}).get("value") == 1500),
                ("mounting_length_saved_for_refined_search", applied_facts.get("mounting_length_mm", {}).get("value") == 180),
            )
        )
    elif scenario in {
        "valves_progressive_unknown",
        "sewer_progressive_unknown",
        "radiator_progressive_unknown",
        "boiler_progressive_unknown",
    }:
        preliminary_turn = turns[1]
        preliminary_response = preliminary_turn["result"].get("response") or {}
        preliminary_cards = preliminary_response.get("products") or []
        preliminary = (preliminary_turn.get("telemetry") or {}).get("selection") or {}
        applied_facts = {
            str(item.get("name")): item
            for item in (selection.get("applied_facts") or [])
        }
        checks.extend(
            (
                ("preliminary_owner_v2", (preliminary_turn.get("telemetry") or {}).get("owner") == "v2"),
                ("preliminary_cards_delivered", bool(preliminary_cards)),
                ("preliminary_outcome_gate_passed", bool(preliminary.get("outcome_gate_passed"))),
                ("preliminary_state_updated", bool(preliminary.get("customer_visible_state_updated"))),
                ("refined_cards_delivered", bool(products)),
                ("refined_state_updated", bool(selection.get("customer_visible_state_updated"))),
            )
        )
        if scenario == "valves_progressive_unknown":
            checks.extend(
                (
                    ("typed_ball_valve", selection.get("product_kind") == "ball_valve"),
                    ("only_internal_internal", all(".n." in sku.casefold() for sku in skus)),
                    ("pattern_saved_for_refined_search", applied_facts.get("connection_pattern", {}).get("value") == "female_female"),
                )
            )
        elif scenario == "sewer_progressive_unknown":
            checks.extend(
                (
                    ("typed_external_sewer", selection.get("product_kind") == "sewer_pipe"),
                    ("no_ppr_cards", all(not sku.casefold().startswith("vtp.") for sku in skus)),
                    ("scope_saved_for_refined_search", applied_facts.get("sewer_scope", {}).get("value") == "external"),
                    ("diameter_saved_for_refined_search", applied_facts.get("diameter_mm", {}).get("value") == 110),
                )
            )
        elif scenario == "radiator_progressive_unknown":
            checks.extend(
                (
                    ("typed_radiator", selection.get("product_kind") == "radiator"),
                    ("only_requested_bimetal", skus == ["RBM-0210-050006"]),
                    ("center_distance_saved", applied_facts.get("center_distance_mm", {}).get("value") == 500),
                    ("material_saved", applied_facts.get("material", {}).get("value") == "биметалл"),
                )
            )
        elif scenario == "boiler_progressive_unknown":
            checks.extend(
                (
                    ("typed_gas_boiler", selection.get("product_kind") == "gas_boiler"),
                    ("only_requested_boiler", skus == ["3636151"]),
                    ("power_saved", applied_facts.get("power_kw", {}).get("value") == 24),
                    ("circuits_saved", applied_facts.get("circuits", {}).get("value") == 2),
                    ("closed_chamber_saved", applied_facts.get("combustion_chamber", {}).get("value") == "closed"),
                )
            )
    checks.extend(
        (
            ("cards_equal_gate_order", skus == (selection.get("ordered_skus") or [])),
            (
                "selection_result_is_structurally_deliverable",
                (
                    selection.get("status") == "shown"
                    and bool(products)
                    and bool(selection.get("outcome_gate_passed"))
                )
                or (
                    selection.get("status") in {"need_clarification", "no_match"}
                    and not products
                    and bool(selection.get("outcome_gate_passed"))
                ),
            ),
        )
    )
    return [{"name": name, "passed": passed} for name, passed in checks]


def _checkpoint(path: Path, results: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "responses.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _report(results: dict[str, Any]) -> str:
    checks = [item for run in results["runs"] for item in run.get("checks", [])]
    failed = [item for item in checks if not item["passed"]]
    latencies = [
        turn["result"]["latency_sec"]
        for run in results["runs"]
        for turn in run["turns"]
        if turn["result"].get("ok")
    ]
    ordered: dict[str, set[tuple[str, ...]]] = {}
    for run in results["runs"]:
        turn = run["turns"][run["selection_turn"] - 1]
        selection = (turn.get("telemetry") or {}).get("selection") or {}
        ordered.setdefault(run["scenario_id"], set()).add(
            tuple(selection.get("ordered_skus") or ())
        )
    lines = [
        "# Targeted V2 selection gate",
        "",
        f"Прогонов: {len(results['runs'])}; проверок: {len(checks)}; ошибок: {len(failed)}.",
        f"P50 latency: {statistics.median(latencies):.2f} с." if latencies else "P50 latency: n/a.",
        f"P95 latency: {sorted(latencies)[max(0, int(len(latencies) * .95) - 1)]:.2f} с." if latencies else "P95 latency: n/a.",
        "",
        "## Стабильность ordered SKU",
        "",
    ]
    lines.extend(
        f"- {scenario}: {len(variants)} вариант(а) порядка — "
        + "; ".join(", ".join(items) or "без карточек" for items in sorted(variants))
        for scenario, variants in sorted(ordered.items())
    )
    lines.extend(("", "## Неуспешные проверки", ""))
    if failed:
        lines.extend(f"- {item['name']}" for item in failed)
    else:
        lines.append("- Нет.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--telemetry-path", type=Path, required=True)
    parser.add_argument("--modes", default="v2_preview")
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenario_ids",
        help="Run only one or more named scenarios; repeat the flag if needed.",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.5)
    args = parser.parse_args()
    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    modes = tuple(item.strip() for item in args.modes.split(",") if item.strip())
    if any(item not in {"legacy", "shadow", "v2_preview"} for item in modes):
        raise SystemExit("unsupported mode")
    results: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url,
        "runs": [],
    }
    run_id = uuid.uuid4().hex[:10]
    scenarios = tuple(
        item
        for item in SCENARIOS
        if not args.scenario_ids or item["id"] in set(args.scenario_ids)
    )
    if not scenarios:
        raise SystemExit("no requested scenario")
    total = len(modes) * len(scenarios) * args.repetitions
    completed = 0
    for mode in modes:
        for scenario in scenarios:
            for repetition in range(1, args.repetitions + 1):
                session_id = f"selection-{run_id}-{mode}-{scenario['id']}-r{repetition}"
                run = {
                    "mode": mode,
                    "scenario_id": scenario["id"],
                    "selection_turn": scenario["selection_turn"],
                    "repetition": repetition,
                    "session_id": session_id,
                    "turns": [],
                }
                results["runs"].append(run)
                for index, message in enumerate(scenario["turns"], start=1):
                    result = _post(
                        args.base_url,
                        token,
                        session_id=session_id,
                        client_turn_id=f"{session_id}-t{index:02d}",
                        message=message,
                        mode=mode,
                        timeout=args.timeout,
                    )
                    run["turns"].append(
                        {"turn": index, "message": message, "result": result}
                    )
                    _checkpoint(args.output_dir, results)
                    if args.pause:
                        time.sleep(args.pause)
                completed += 1
                print(
                    f"[{completed:02d}/{total}] {mode} {scenario['id']} r{repetition}",
                    flush=True,
                )
    _attach(results, args.telemetry_path)
    for run in results["runs"]:
        run["checks"] = _checks(run)
    _checkpoint(args.output_dir, results)
    (args.output_dir / "report.md").write_text(_report(results), encoding="utf-8")
    failed = [
        item
        for run in results["runs"]
        for item in run["checks"]
        if not item["passed"]
    ]
    print(f"FAILED CHECKS: {len(failed)}", flush=True)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
