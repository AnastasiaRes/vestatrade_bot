#!/usr/bin/env python3
"""Measure paraphrase and fragmented-fact coverage of the accepted V2 seams."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_widget_selection_gate import (
    _TRACE_SEQUENCE_PREFIX,
    _fingerprint,
    _post,
    _traces,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "widget_selection_paraphrase_v2_2026-08-28"

PUMP_SKUS = {
    "2459900",
    "53843",
    "9168934",
    "VRS.254.18.0",
    "VRS.256.13.0",
    "VRS.256.18.0",
    "VRS.258.18.0",
    "VRS.324.18.0",
}
EXTERNAL_SEWER_SKUS = {"220010", "1491056"}


def _scenario(
    scenario_id: str,
    family: str,
    turns: tuple[str, ...],
    *,
    selection_turn: int,
    expected_skus: tuple[str, ...] = (),
    reference_index: int | None = None,
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "family": family,
        "turns": turns,
        "selection_turn": selection_turn,
        "expected_skus": expected_skus,
        "reference_index": reference_index,
    }


SCENARIOS: tuple[dict[str, Any], ...] = (
    _scenario(
        "ppr_plain",
        "ppr",
        (
            "Полипропиленовая труба 25 со стекловолокном для отопления, температура подачи 90 градусов",
            "Покажи подходящие",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "ppr_slang",
        "ppr",
        (
            "Нужна ппэровская двадцать пятая со стеклом на батареи, подача девяносто",
            "Что есть?",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "ppr_typos",
        "ppr",
        (
            "Ищу полипропиленовю трубу 25, армированую стекловалакном, на радиаторное отопление 90С",
            "Покажы варианты",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "ppr_fragmented",
        "ppr",
        (
            "Нужна ППР труба",
            "Диаметр 25 миллиметров",
            "Армировка стекловолокно",
            "Она на отопление, подача 90 градусов",
            "Покажите варианты",
        ),
        selection_turn=5,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "ppr_reordered",
        "ppr",
        (
            "На радиаторную разводку при 90 градусах нужна PPR со стекловолокном, наружный диаметр 25",
            "Можно варианты?",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "ppr_short_fragments",
        "ppr",
        (
            "Полипропилен",
            "25 мм",
            "Со стеклом",
            "Для батарей, 90 градусов",
            "Покажи",
        ),
        selection_turn=5,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "pump_colloquial",
        "pump",
        (
            "Нужен циркуляционник на батареи: полтора куба в час при четырёх метрах напора",
            "Покажи, что подойдёт",
        ),
        selection_turn=2,
    ),
    _scenario(
        "pump_typos",
        "pump",
        (
            "Нужен циркуляционый нассос для радиаторов, расход 1,5 куба, напор 4 метра",
            "Покажы варианты",
        ),
        selection_turn=2,
    ),
    _scenario(
        "pump_fragmented",
        "pump",
        (
            "Ищу насос для радиаторного контура",
            "Расход 1,5 куба в час",
            "Требуемый напор четыре метра",
            "Покажи варианты",
        ),
        selection_turn=4,
    ),
    _scenario(
        "pump_reordered",
        "pump",
        (
            "Радиаторный контур: четыре метра напора при расходе полтора куба в час, нужен циркуляционный насос",
            "Что можно взять?",
        ),
        selection_turn=2,
    ),
    _scenario(
        "pump_everyday",
        "pump",
        (
            "На отопление нужен насос, чтобы качал 1.5 куба при напоре 4 м",
            "Покажите товары",
        ),
        selection_turn=2,
    ),
    _scenario(
        "pump_engineering_notation",
        "pump",
        (
            "Циркуляционный насос в радиаторную схему: Q=1.5 м³/ч, H=4 м",
            "Выдай варианты",
        ),
        selection_turn=2,
    ),
    _scenario(
        "valve_plain",
        "valves",
        ("Кран BASE полдюйма, обе резьбы внутренние", "Покажи варианты"),
        selection_turn=2,
    ),
    _scenario(
        "valve_vr",
        "valves",
        ("Шаровый BASE G1/2 ВР/ВР", "Что есть в каталоге?"),
        selection_turn=2,
    ),
    _scenario(
        "valve_typos",
        "valves",
        ("Нужен шаровый кран БЭЙС 1/2 вн вн", "Покажы"),
        selection_turn=2,
    ),
    _scenario(
        "valve_fragmented",
        "valves",
        (
            "Нужен кран BASE",
            "Полдюйма",
            "Обе резьбы внутренние",
            "Покажи варианты",
        ),
        selection_turn=4,
    ),
    _scenario(
        "valve_dn",
        "valves",
        (
            "VALTEC BASE DN15, внутренняя резьба с обеих сторон",
            "Покажите подходящие",
        ),
        selection_turn=2,
    ),
    _scenario(
        "sewer_plain",
        "sewer",
        ("Нужна рыжая канализационная труба от дома к септику", "Покажи варианты"),
        selection_turn=2,
    ),
    _scenario(
        "sewer_slang",
        "sewer",
        ("Каналия по улице до септика, диаметр 110", "Что можно купить?"),
        selection_turn=2,
    ),
    _scenario(
        "sewer_typos",
        "sewer",
        ("Нужна канализацыя наружняя к сиптику", "Покажы трубы"),
        selection_turn=2,
    ),
    _scenario(
        "sewer_fragmented",
        "sewer",
        (
            "В туалете пахнет канализацией, похоже проблема с трубой",
            "Трасса пойдёт наружу от дома",
            "До септика",
            "Покажи варианты",
        ),
        selection_turn=4,
    ),
    _scenario(
        "sewer_outlet",
        "sewer",
        ("Нужна труба для вывода стоков из дома наружу", "Покажи, что есть"),
        selection_turn=2,
    ),
    _scenario(
        "insufficient_pipe",
        "insufficient",
        ("Нужна труба",),
        selection_turn=1,
    ),
    _scenario(
        "insufficient_pipe_advice",
        "insufficient",
        ("Трубу посоветуйте",),
        selection_turn=1,
    ),
    _scenario(
        "insufficient_ppr",
        "insufficient",
        ("Нужна ППР",),
        selection_turn=1,
    ),
    _scenario(
        "insufficient_sewer",
        "insufficient",
        ("Хочу канализационную трубу",),
        selection_turn=1,
    ),
    _scenario(
        "ordinal_first_plain",
        "ordinal",
        (
            "Нужен циркуляционник на радиаторы: расход 1,5 куба, напор 4 метра",
            "Покажи варианты",
            "Какая монтажная длина у первой позиции?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_first_mm",
        "ordinal",
        (
            "Насос на радиаторную систему, Q 1,5 м3/ч, H 4 м",
            "Что есть?",
            "Сколько миллиметров у первого насоса по монтажу?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_first_between",
        "ordinal",
        (
            "Для батарей нужен циркуляционный насос: полтора куба и четыре метра напора",
            "Покажи товары",
            "Первый вариант какой длины между присоединениями?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_second",
        "ordinal",
        (
            "Циркуляционный насос, радиаторы, расход 1.5 куба, напор 4 метра",
            "Покажи варианты",
            "А у второго какая монтажная длина?",
        ),
        selection_turn=2,
        reference_index=1,
    ),
    _scenario(
        "named_exact",
        "named",
        ("Покажи насос циркуляционный VALTEC RS 25/4-180 с гайками",),
        selection_turn=1,
        expected_skus=("VRS.254.18.0",),
    ),
    _scenario(
        "named_partial_sku",
        "named",
        ("Покажи VRS.254",),
        selection_turn=1,
        expected_skus=("VRS.254.18.0",),
    ),
    _scenario(
        "explicit_sku_over_stale_goal",
        "named",
        (
            "Нужна ППР труба 25 для отопления",
            "Теперь покажи VRS.254.18.0",
        ),
        selection_turn=2,
        expected_skus=("VRS.254.18.0",),
    ),
    # Isolate paraphrasing of the follow-up product-fact question from
    # paraphrasing of the preceding selection request.  The setup below is the
    # already accepted canonical selection scenario.
    _scenario(
        "ordinal_isolated_first_plain",
        "ordinal",
        (
            "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "Какая монтажная длина у первой позиции?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_isolated_first_mm",
        "ordinal",
        (
            "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "Сколько миллиметров у первого насоса по монтажу?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_isolated_first_between",
        "ordinal",
        (
            "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "Первый вариант какой длины между присоединениями?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "ordinal_isolated_second",
        "ordinal",
        (
            "Циркуляционный насос: расход 1,5 м3/ч, напор 4 м, схема радиаторная",
            "Покажите варианты",
            "А у второго какая монтажная длина?",
        ),
        selection_turn=2,
        reference_index=1,
    ),
)


def _trace_excerpt(trace: dict[str, Any] | None) -> dict[str, Any]:
    if trace is None:
        return {"found": False}
    cutover = trace.get("cutover_v2") or {}
    decision = cutover.get("decision") or {}
    candidate = cutover.get("candidate") or {}
    understanding = trace.get("turn_understanding") or {}
    dialogue = trace.get("dialogue_v2_shadow") or {}
    state_after = dialogue.get("state_after") or {}
    passport = trace.get("passport_events") or []
    product_fact = next(
        (item for item in passport if item.get("event") == "product_fact_v2_candidate"),
        None,
    )
    return {
        "found": True,
        "owner": decision.get("owner_candidate"),
        "execution_mode": decision.get("execution_mode"),
        "semantic_status": understanding.get("status"),
        "semantic_rejection_reason": understanding.get("rejection_reason"),
        "semantic_repairs": understanding.get("structural_repairs") or [],
        "semantic_delta": understanding.get("semantic_delta"),
        "semantic_gate": understanding.get("semantic_gate"),
        "state_after": {
            "active_goal_id": state_after.get("active_goal_id"),
            "product_goals": state_after.get("product_goals") or [],
            "constraints": state_after.get("constraints") or [],
            "tasks": state_after.get("tasks") or [],
        },
        "candidate_eligible": candidate.get("eligible_for_delivery"),
        "candidate_rejection_reason_codes": candidate.get("rejection_reason_codes") or [],
        "selection": cutover.get("selection_delivery"),
        "product_fact": product_fact,
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


def _fact_values(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        str(item.get("name") or ""): item.get("value")
        for item in (selection.get("applied_facts") or [])
    }


def _active_state_fact_values(telemetry: dict[str, Any]) -> dict[str, Any]:
    state_after = telemetry.get("state_after") or {}
    return {
        str(item.get("name") or ""): item.get("value")
        for item in (state_after.get("constraints") or [])
        if item.get("active") and item.get("status") == "known"
    }


def _checks(run: dict[str, Any]) -> list[dict[str, Any]]:
    family = run["family"]
    turns = run["turns"]
    selected = turns[run["selection_turn"] - 1]
    result = selected["result"]
    response = result.get("response") or {}
    telemetry = selected.get("telemetry") or {}
    selection = telemetry.get("selection") or {}
    products = response.get("products") or []
    skus = [str(item.get("sku") or "") for item in products]
    answer = str(response.get("answer") or "").casefold()
    facts = _fact_values(selection)
    state_facts = _active_state_fact_values(telemetry)
    checks: list[tuple[str, bool]] = [
        ("all_http_200", all(item["result"].get("ok") for item in turns)),
        (
            "all_target_turns_owned_by_v2",
            all((item.get("telemetry") or {}).get("owner") == "v2" for item in turns),
        ),
        ("selection_trace_found", bool(telemetry.get("found"))),
        ("selection_owner_v2", telemetry.get("owner") == "v2"),
        ("selection_v2_primary", telemetry.get("execution_mode") == "v2_primary"),
        ("selection_outcome_gate", bool(selection.get("outcome_gate_passed"))),
        ("cards_equal_order", skus == (selection.get("ordered_skus") or [])),
        (
            "no_selection_placeholder",
            not any(marker in answer for marker in ("покажу варианты", "сравню варианты")),
        ),
    ]
    if family == "ppr":
        checks.extend(
            (
                ("ppr_exact_card", skus == ["VTp.700.FB20.25"]),
                ("ppr_kind", selection.get("product_kind") == "pipe"),
                ("ppr_diameter", facts.get("diameter_mm") == 25),
                ("ppr_reinforcement", facts.get("reinforcement") == "glass_fiber"),
                ("ppr_heating", facts.get("pipe_service") == "heating"),
                (
                    "ppr_temperature_preserved",
                    state_facts.get("operating_temperature_c") == 90,
                ),
                (
                    "ppr_service_not_reasked",
                    "уточните, пожалуйста, параметр «назначение трубы»" not in answer,
                ),
            )
        )
    elif family == "pump":
        checks.extend(
            (
                ("pump_cards", bool(skus)),
                ("pump_kind", selection.get("product_kind") == "circulation_pump"),
                ("pump_only", all(item in PUMP_SKUS for item in skus)),
                ("pump_flow", facts.get("duty_point_flow_l_h") in {1.5, 1500}),
                ("pump_head", facts.get("duty_point_head_m") == 4),
            )
        )
    elif family == "valves":
        checks.extend(
            (
                ("valve_cards", bool(skus)),
                ("valve_kind", selection.get("product_kind") == "ball_valve"),
                ("valve_internal_internal", all(".N." in item for item in skus)),
                ("valve_connection_fact", facts.get("connection_pattern") == "female_female"),
                (
                    "valve_connection_not_reasked",
                    "уточните, пожалуйста, параметр «тип резьбового соединения»" not in answer,
                ),
            )
        )
    elif family == "sewer":
        necessary_clarification = bool(
            selection.get("status") == "need_clarification"
            and selection.get("missing_critical_fact") == "diameter_mm"
            and not skus
        )
        checks.extend(
            (
                ("sewer_cards_or_necessary_clarification", bool(skus) or necessary_clarification),
                ("sewer_kind", selection.get("product_kind") == "sewer_pipe"),
                ("sewer_no_ppr", all(not item.casefold().startswith("vtp.") for item in skus)),
            )
        )
        if state_facts.get("sewer_scope") == "external":
            checks.extend(
                (
                    ("sewer_external_fact_applied", facts.get("sewer_scope") == "external"),
                    (
                        "sewer_external_cards_only",
                        bool(skus) and all(item in EXTERNAL_SEWER_SKUS for item in skus),
                    ),
                )
            )
        mentions_110 = any(
            "110" in item["message"].casefold()
            or "сто деся" in item["message"].casefold()
            for item in turns
        )
        if mentions_110:
            checks.extend(
                (
                    ("sewer_diameter_state_canonical", state_facts.get("diameter_mm") == 110),
                    ("sewer_diameter_applied", facts.get("diameter_mm") == 110),
                )
            )
    elif family == "insufficient":
        expected_kind = run.get("expected_kind") or {
            "insufficient_pipe": "pipe",
            "insufficient_pipe_advice": "pipe",
            "insufficient_ppr": "pipe",
            "insufficient_sewer": "sewer_pipe",
        }.get(run["scenario_id"])
        checks.extend(
            (
                ("insufficient_need_clarification", selection.get("status") == "need_clarification"),
                ("insufficient_one_fact", bool(selection.get("missing_critical_fact"))),
                ("insufficient_no_cards", not skus),
                ("insufficient_correct_kind", selection.get("product_kind") == expected_kind),
            )
        )
    elif family == "ordinal":
        final = turns[-1]
        final_response = final["result"].get("response") or {}
        final_telemetry = final.get("telemetry") or {}
        product_fact = final_telemetry.get("product_fact") or {}
        index = int(run["reference_index"])
        expected_sku = skus[index] if len(skus) > index else None
        checks.extend(
            (
                ("ordinal_cards", bool(skus)),
                ("ordinal_final_owner_v2", final_telemetry.get("owner") == "v2"),
                ("ordinal_reference_kind", product_fact.get("product_reference_kind") == "ordinal"),
                ("ordinal_matches_visible_card", product_fact.get("canonical_sku") == expected_sku),
                ("ordinal_predicate", product_fact.get("predicate") == "installation_length_mm"),
                ("ordinal_answered", product_fact.get("evidence_status") == "answered"),
                ("ordinal_has_mm", "мм" in str(final_response.get("answer") or "").casefold()),
            )
        )
    elif family == "named":
        checks.extend(
            (
                ("named_expected_sku", skus == list(run["expected_skus"])),
                ("named_shown", selection.get("status") == "shown"),
            )
        )
    return [{"name": name, "passed": passed} for name, passed in checks]


def _write_json(path: Path, results: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "responses.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _render_report(results: dict[str, Any]) -> str:
    runs = results["runs"]
    all_checks = [item for run in runs for item in run.get("checks", [])]
    failed_checks = [item for item in all_checks if not item["passed"]]
    passed_runs = [run for run in runs if all(item["passed"] for item in run["checks"])]
    family_totals: Counter[str] = Counter(run["family"] for run in runs)
    family_passed: Counter[str] = Counter(run["family"] for run in passed_runs)
    owners: Counter[str] = Counter()
    semantic: Counter[str] = Counter()
    repair_counts: Counter[str] = Counter()
    latencies: list[float] = []
    ordered: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for run in runs:
        for turn in run["turns"]:
            if turn["result"].get("ok"):
                latencies.append(float(turn["result"].get("latency_sec") or 0))
            trace = turn.get("telemetry") or {}
            if trace.get("owner"):
                owners[str(trace["owner"])] += 1
            if trace.get("semantic_status"):
                semantic[str(trace["semantic_status"])] += 1
            repair_counts.update(str(item) for item in (trace.get("semantic_repairs") or []))
        selected = run["turns"][run["selection_turn"] - 1]
        selection = (selected.get("telemetry") or {}).get("selection") or {}
        ordered[run["family"]].add(tuple(selection.get("ordered_skus") or []))
    p50 = statistics.median(latencies) if latencies else 0.0
    sorted_latencies = sorted(latencies)
    p95 = (
        sorted_latencies[max(0, int(len(sorted_latencies) * 0.95) - 1)]
        if sorted_latencies
        else 0.0
    )
    lines = [
        "# V2 paraphrase and fragmented-fact gate",
        "",
        f"Запуск: {results['created_at']}",
        f"Сценариев: {len(runs)}; ходов: {sum(len(run['turns']) for run in runs)}.",
        f"Полностью прошли: {len(passed_runs)}/{len(runs)} ({len(passed_runs) / len(runs) * 100:.1f}%).",
        f"Проверок: {len(all_checks)}; неуспешных: {len(failed_checks)}.",
        f"P50/P95 latency: {p50:.2f}/{p95:.2f} с.",
        f"Owners по всем ходам: `{dict(sorted(owners.items()))}`.",
        f"Semantic status: `{dict(sorted(semantic.items()))}`.",
        "",
        "## Покрытие по семействам",
        "",
        "| Семейство | Пройдено | Покрытие |",
        "|---|---:|---:|",
    ]
    for family in sorted(family_totals):
        passed = family_passed[family]
        total = family_totals[family]
        lines.append(f"| {family} | {passed}/{total} | {passed / total * 100:.1f}% |")
    lines.extend(("", "## Ordered SKU variants", ""))
    for family in sorted(ordered):
        variants = ordered[family]
        rendered = "; ".join(", ".join(items) or "без карточек" for items in sorted(variants))
        lines.append(f"- {family}: {len(variants)} — {rendered}")
    lines.extend(("", "## Частые semantic repairs", ""))
    for name, count in repair_counts.most_common(20):
        lines.append(f"- `{name}`: {count}")
    lines.extend(("", "## Не прошедшие сценарии", ""))
    failed_runs = [run for run in runs if any(not item["passed"] for item in run["checks"])]
    if not failed_runs:
        lines.append("- Нет.")
    for run in failed_runs:
        selected = run["turns"][run["selection_turn"] - 1]
        selection = (selected.get("telemetry") or {}).get("selection") or {}
        response = selected["result"].get("response") or {}
        failed = [item["name"] for item in run["checks"] if not item["passed"]]
        lines.extend(
            (
                f"### {run['scenario_id']} ({run['family']})",
                "",
                f"- failed: `{failed}`",
                f"- semantic: `{(selected.get('telemetry') or {}).get('semantic_status')}`",
                f"- owner: `{(selected.get('telemetry') or {}).get('owner')}`",
                f"- selection status/kind/missing: `{selection.get('status')}` / `{selection.get('product_kind')}` / `{selection.get('missing_critical_fact')}`",
                f"- ordered SKU: `{selection.get('ordered_skus') or []}`",
                f"- ответ: {str(response.get('answer') or '')[:700]}",
                "",
            )
        )
    lines.extend(
        (
            "## Методика",
            "",
            "Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--telemetry-path", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument(
        "--evaluate-existing",
        type=Path,
        help="Re-attach telemetry and evaluate an existing responses.json.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Run only the named scenario id; may be repeated.",
    )
    args = parser.parse_args()
    if args.evaluate_existing is not None:
        results = json.loads(args.evaluate_existing.read_text(encoding="utf-8"))
        _attach(results, args.telemetry_path)
        for run in results["runs"]:
            run["checks"] = _checks(run)
        _write_json(args.output_dir, results)
        report = _render_report(results)
        (args.output_dir / "report.md").write_text(report, encoding="utf-8")
        failed_runs = [
            run
            for run in results["runs"]
            if any(not check["passed"] for check in run["checks"])
        ]
        print(f"FAILED SCENARIOS: {len(failed_runs)}")
        print(f"REPORT: {args.output_dir / 'report.md'}")
        return 0 if not failed_runs else 2
    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")
    results: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url,
        "runs": [],
    }
    selected_scenarios = tuple(
        scenario
        for scenario in SCENARIOS
        if not args.only or scenario["id"] in set(args.only)
    )
    unknown = sorted(set(args.only) - {scenario["id"] for scenario in SCENARIOS})
    if unknown:
        raise SystemExit(f"Unknown scenario ids: {', '.join(unknown)}")
    run_id = uuid.uuid4().hex[:10]
    for index, scenario in enumerate(selected_scenarios, start=1):
        session_id = f"paraphrase-{run_id}-{scenario['id']}"
        run = {
            "scenario_id": scenario["id"],
            "family": scenario["family"],
            "selection_turn": scenario["selection_turn"],
            "expected_skus": scenario["expected_skus"],
            "reference_index": scenario["reference_index"],
            "session_id": session_id,
            "turns": [],
        }
        results["runs"].append(run)
        for turn_index, message in enumerate(scenario["turns"], start=1):
            result = _post(
                args.base_url,
                token,
                session_id=session_id,
                client_turn_id=f"{session_id}-t{turn_index:02d}",
                message=message,
                mode="v2_preview",
                timeout=args.timeout,
            )
            run["turns"].append(
                {"turn": turn_index, "message": message, "result": result}
            )
            _write_json(args.output_dir, results)
            if args.pause:
                time.sleep(args.pause)
        print(
            f"[{index:02d}/{len(selected_scenarios)}] "
            f"{scenario['family']}: {scenario['id']}",
            flush=True,
        )
    _attach(results, args.telemetry_path)
    for run in results["runs"]:
        run["checks"] = _checks(run)
    _write_json(args.output_dir, results)
    report = _render_report(results)
    (args.output_dir / "report.md").write_text(report, encoding="utf-8")
    failed_runs = [
        run
        for run in results["runs"]
        if any(not item["passed"] for item in run["checks"])
    ]
    print(f"FAILED SCENARIOS: {len(failed_runs)}", flush=True)
    print(f"REPORT: {args.output_dir / 'report.md'}", flush=True)
    return 2 if failed_runs else 0


if __name__ == "__main__":
    raise SystemExit(main())
