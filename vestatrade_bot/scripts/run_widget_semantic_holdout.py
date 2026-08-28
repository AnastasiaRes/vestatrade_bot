#!/usr/bin/env python3
"""Run holdout phrasings through the same structural V2 paraphrase gate."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from run_widget_paraphrase_gate import (
    _attach,
    _checks,
    _post,
    _render_report,
    _scenario,
    _write_json,
)


HOLDOUT_SCENARIOS = (
    _scenario(
        "holdout_ppr_compact",
        "ppr",
        (
            "Труба PPR 25-я, со стекловолоконным армированием, в отопительный контур на 90 °C",
            "Подбери доступные позиции",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "holdout_ppr_fragmented",
        "ppr",
        (
            "Нужна труба из полипропилена",
            "Наружный диаметр двадцать пять",
            "Армирование волокном стекла",
            "Для контура с радиаторами при 90 C",
            "Покажи наличие",
        ),
        selection_turn=5,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "holdout_ppr_everyday",
        "ppr",
        (
            "Хочу полипропилен 25 со стекловолокном для батарей, теплоноситель до 90 градусов",
            "Какие позиции подходят?",
        ),
        selection_turn=2,
        expected_skus=("VTp.700.FB20.25",),
    ),
    _scenario(
        "holdout_pump_compact",
        "pump",
        (
            "Циркуляционный насос для радиаторного отопления: подача 1,5 кубометра в час, напор 4 м",
            "Подбери доступные",
        ),
        selection_turn=2,
    ),
    _scenario(
        "holdout_pump_fragmented",
        "pump",
        (
            "Нужен циркуляционник для батарей",
            "По расходу полтора кубометра в час",
            "По напору четыре метра",
            "Что доступно?",
        ),
        selection_turn=4,
    ),
    _scenario(
        "holdout_pump_notation",
        "pump",
        (
            "Насос циркуляционный, контур радиаторов, Q 1.5 м3/ч и H 4 метра",
            "Выведи подходящие позиции",
        ),
        selection_turn=2,
    ),
    _scenario(
        "holdout_pump_reordered",
        "pump",
        (
            "Четыре метра напора и полтора куба расхода для радиаторов — нужен циркуляционный насос",
            "Что можно купить?",
        ),
        selection_turn=2,
    ),
    _scenario(
        "holdout_valve_words",
        "valves",
        (
            "Кран шаровой BASE на полдюйма, внутренняя резьба с обеих сторон",
            "Покажи подходящие",
        ),
        selection_turn=2,
    ),
    _scenario(
        "holdout_valve_notation",
        "valves",
        (
            "VALTEC BASE DN15, соединения ВР с двух сторон",
            "Какие есть в наличии?",
        ),
        selection_turn=2,
    ),
    _scenario(
        "holdout_valve_fragmented",
        "valves",
        (
            "Нужен шаровый кран BASE",
            "Размер G 1/2",
            "Оба присоединения с внутренней резьбой",
            "Покажи позиции",
        ),
        selection_turn=4,
    ),
    _scenario(
        "holdout_sewer_plain",
        "sewer",
        (
            "Нужна труба для стоков от здания до септика",
            "Диаметр 110 мм",
            "Покажи доступные",
        ),
        selection_turn=3,
    ),
    _scenario(
        "holdout_sewer_everyday",
        "sewer",
        (
            "Хочу вывести канализацию из дома наружу к септику",
            "Сто десятый диаметр",
            "Какие трубы есть?",
        ),
        selection_turn=3,
    ),
    _scenario(
        "holdout_sewer_fragmented",
        "sewer",
        (
            "Труба под бытовые стоки",
            "Прокладка снаружи дома",
            "Наружный диаметр 110",
            "Покажи варианты",
        ),
        selection_turn=4,
    ),
    _scenario(
        "holdout_ordinal_first",
        "ordinal",
        (
            "Циркуляционный насос для радиаторов: 1,5 м3/ч и 4 м напора",
            "Покажи доступные позиции",
            "У первой карточки сколько миллиметров между патрубками?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "holdout_ordinal_second",
        "ordinal",
        (
            "Циркуляционный насос для радиаторов: 1,5 м3/ч и 4 м напора",
            "Покажи доступные позиции",
            "Какой монтажный размер у второй позиции?",
        ),
        selection_turn=2,
        reference_index=1,
    ),
    _scenario(
        "holdout_ordinal_deictic",
        "ordinal",
        (
            "Циркуляционный насос для радиаторов: 1,5 м3/ч и 4 м напора",
            "Покажи доступные позиции",
            "А первый по длине монтажа какой?",
        ),
        selection_turn=2,
        reference_index=0,
    ),
    _scenario(
        "holdout_insufficient_pipe",
        "insufficient",
        ("Посоветуйте трубу",),
        selection_turn=1,
    ),
    _scenario(
        "holdout_insufficient_ppr",
        "insufficient",
        ("Ищу полипропиленовую трубу",),
        selection_turn=1,
    ),
    _scenario(
        "holdout_named_partial",
        "named",
        ("Найди в каталоге насос VRS.254",),
        selection_turn=1,
        expected_skus=("VRS.254.18.0",),
    ),
    _scenario(
        "holdout_named_exact",
        "named",
        ("Мне нужна карточка: Насос циркуляционный VALTEC RS 25/4-180 с гайками",),
        selection_turn=1,
        expected_skus=("VRS.254.18.0",),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--telemetry-path", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    token = os.environ.get("DIALOGUE_QA_TOKEN", "")
    if not token:
        raise SystemExit("DIALOGUE_QA_TOKEN is required")

    run_id = uuid.uuid4().hex[:10]
    results = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url,
        "holdout": True,
        "runs": [],
    }
    selected_scenarios = tuple(
        scenario
        for scenario in HOLDOUT_SCENARIOS
        if not args.only or scenario["id"] in set(args.only)
    )
    unknown = sorted(
        set(args.only) - {scenario["id"] for scenario in HOLDOUT_SCENARIOS}
    )
    if unknown:
        raise SystemExit(f"Unknown scenario ids: {', '.join(unknown)}")
    for index, scenario in enumerate(selected_scenarios, start=1):
        session_id = f"semantic-holdout-{run_id}-{scenario['id']}"
        run = {
            "scenario_id": scenario["id"],
            "family": scenario["family"],
            "selection_turn": scenario["selection_turn"],
            "expected_skus": scenario["expected_skus"],
            "reference_index": scenario["reference_index"],
            "expected_kind": (
                "pipe" if scenario["family"] == "insufficient" else None
            ),
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
        print(f"[{index:02d}/{len(selected_scenarios)}] {scenario['id']}", flush=True)

    _attach(results, args.telemetry_path)
    for run in results["runs"]:
        run["checks"] = _checks(run)
    _write_json(args.output_dir, results)
    report = _render_report(results).replace(
        "# V2 paraphrase and fragmented-fact gate",
        "# V2 semantic holdout gate",
        1,
    )
    (args.output_dir / "report.md").write_text(report, encoding="utf-8")
    failed = [
        run
        for run in results["runs"]
        if any(not check["passed"] for check in run["checks"])
    ]
    score = (len(selected_scenarios) - len(failed)) / len(selected_scenarios)
    print(f"FAILED SCENARIOS: {len(failed)}", flush=True)
    print(f"HOLDOUT SCORE: {score:.1%}", flush=True)
    print(f"REPORT: {args.output_dir / 'report.md'}", flush=True)
    return 0 if score >= 0.95 else 2


if __name__ == "__main__":
    raise SystemExit(main())
