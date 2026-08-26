#!/usr/bin/env python3
"""Opt-in Stage 6A release lane; never enables public V2 delivery.

The lane deliberately runs the existing adaptive buyer/full-100 harness in
shadow comparison mode.  It writes raw transcripts only to the caller-chosen
local output directory; the compact gate summary contains aggregate metrics,
typed cutover reason codes and reproducibility hashes, but no dialogue text or
credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTSET = PROJECT_ROOT / "data/live_dialogue_feed_testset_2026-08-25.json"
FEED100 = PROJECT_ROOT / "data/feed_showcase_100_2026-06-14.xml"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")


def _require_opt_in() -> None:
    if os.getenv("RUN_LIVE_LLM_TESTS") != "1" or os.getenv(
        "RUN_STAGE6_RELEASE_EVALS"
    ) != "1":
        raise SystemExit(
            "Stage 6 release lane is disabled. Set RUN_LIVE_LLM_TESTS=1 and "
            "RUN_STAGE6_RELEASE_EVALS=1 explicitly."
        )
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for the release lane")


def _shadow_environment(output_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "RUN_LIVE_LLM_TESTS": "1",
            "RUN_STAGE6_RELEASE_EVALS": "1",
            "DIAGNOSTIC_TELEMETRY_ENABLED": "true",
            "DIALOGUE_V2_ROUTING_ENABLED": "true",
            "DIALOGUE_V2_SHADOW_COMPARE_ENABLED": "true",
            "DIALOGUE_V2_LIVE_DELIVERY_ENABLED": "false",
            "DIALOGUE_V2_INTERNAL_CANARY_ENABLED": "false",
            "DIALOGUE_V2_LOCAL_PREVIEW_ENABLED": "false",
            "DIALOGUE_V2_INTERNAL_CANARY_PERCENT": "0",
            "DIALOGUE_V2_FORCE_LEGACY": "false",
            "COMMERCE_EXTERNAL_EXECUTION_ENABLED": "false",
            "DIAGNOSTIC_TRACE_PATH": str(output_dir / "shadow_turns.jsonl"),
        }
    )
    return env


def _run(command: list[str], *, env: dict[str, str]) -> int:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    return completed.returncode


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _telemetry_summary(path: Path) -> dict[str, Any]:
    decisions: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    parity: Counter[str] = Counter()
    accepted_candidates = 0
    trace_count = 0
    if not path.is_file():
        return {
            "traces": 0,
            "accepted_delivery_candidates": 0,
            "decision_owners": {},
            "candidate_rejection_reasons": {},
            "parity_statuses": {},
        }
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            trace = json.loads(line)
        except json.JSONDecodeError:
            continue
        trace_count += 1
        cutover = trace.get("cutover_v2") or {}
        decision = cutover.get("decision") or {}
        owner = decision.get("owner_candidate")
        if owner:
            decisions[str(owner)] += 1
        candidate = cutover.get("candidate") or {}
        if candidate.get("eligible_for_delivery") is True:
            accepted_candidates += 1
        for reason in candidate.get("rejection_reason_codes") or ():
            rejection_reasons[str(reason)] += 1
        parity_status = (cutover.get("parity") or {}).get("status")
        if parity_status:
            parity[str(parity_status)] += 1
    return {
        "traces": trace_count,
        "accepted_delivery_candidates": accepted_candidates,
        "decision_owners": _counter_dict(decisions),
        "candidate_rejection_reasons": _counter_dict(rejection_reasons),
        "parity_statuses": _counter_dict(parity),
    }


def _full100_summary(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "dialogues_attempted": report.get("dialogues_attempted"),
        "dialogues_valid": report.get("dialogues_valid"),
        "dialogues_invalid": report.get("dialogues_invalid"),
        "turns_attempted": report.get("turns_attempted"),
        "outcomes": report.get("outcomes"),
        "execution_outcomes": report.get("execution_outcomes"),
        "defect_hits": report.get("defect_hits"),
        "latency_sec": report.get("latency_sec"),
        "elapsed_sec": report.get("elapsed_sec"),
    }


def _junit_summary(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    root = ElementTree.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(item.attrib.get("tests", 0)) for item in suites)
    failures = sum(int(item.attrib.get("failures", 0)) for item in suites)
    errors = sum(int(item.attrib.get("errors", 0)) for item in suites)
    skipped = sum(int(item.attrib.get("skipped", 0)) for item in suites)
    xfailed = 0
    for case in root.iter("testcase"):
        skipped_node = case.find("skipped")
        if skipped_node is not None and skipped_node.attrib.get("type") == "pytest.xfail":
            xfailed += 1
    return {
        "tests": tests,
        "passed": max(0, tests - failures - errors - skipped),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "gate_denominator": tests,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--testset", type=Path, default=DEFAULT_TESTSET)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument(
        "--targeted-only",
        action="store_true",
        help="run the 16 targeted release dialogues without full-100",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    _require_opt_in()
    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit("output directory must be absent or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _shadow_environment(output_dir)

    targeted_rc = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_cutover_v2_live_llm.py",
            "-rxX",
            "--junitxml",
            str(output_dir / "targeted_junit.xml"),
        ],
        env=env,
    )
    full100_rc: int | None = None
    full100_dir = output_dir / "full100_shadow"
    if not args.targeted_only:
        full100_rc = _run(
            [
                sys.executable,
                "scripts/run_live_dialogues.py",
                "--mode",
                "live",
                "--testset",
                str(args.testset.resolve()),
                "--workers",
                str(max(1, args.workers)),
                "--max-turns",
                str(max(4, args.max_turns)),
                "--output-dir",
                str(full100_dir),
            ],
            env=env,
        )

    from app.config import get_settings
    from app.cutover_v2.manifest import build_release_manifest
    from app.cutover_v2.registry import (
        build_migration_readiness_matrix,
        load_registry,
    )

    settings = get_settings()
    registry = load_registry(settings.dialogue_v2_migration_registry_path)
    readiness_matrix = build_migration_readiness_matrix(
        registry.registry(),
        catalog_revision=None,
    )
    summary = {
        "schema_version": "1.0",
        "public_v2_delivery_enabled": False,
        "external_commerce_execution_enabled": False,
        "targeted_live_return_code": targeted_rc,
        "full100_return_code": full100_rc,
        "targeted_xfail_is_success": False,
        "targeted_live": _junit_summary(output_dir / "targeted_junit.xml"),
        "rollout_scope": "blocked_until_gate_review",
        "migration_readiness": [
            item.model_dump(mode="json") for item in readiness_matrix
        ],
        "telemetry": _telemetry_summary(output_dir / "shadow_turns.jsonl"),
        "full100": _full100_summary(full100_dir / "report.json"),
        "release_manifest": build_release_manifest(
            PROJECT_ROOT,
            catalog_path=settings.products_cache_path,
            feed100_path=FEED100,
            registry_revision=registry.revision,
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model_strong,
            catalog_source=str(settings.products_cache_path),
            testset_path=args.testset.resolve(),
            generation_settings={
                "request_timeout_seconds": settings.llm_request_timeout_seconds,
                "attempt_timeout_seconds": settings.llm_timeout_seconds,
                "max_retries": settings.llm_max_retries,
                "retry_delay_seconds": settings.llm_retry_delay_seconds,
            },
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            feature_flags={
                "DIALOGUE_V2_ROUTING_ENABLED": True,
                "DIALOGUE_V2_SHADOW_COMPARE_ENABLED": True,
                "DIALOGUE_V2_LIVE_DELIVERY_ENABLED": False,
                "DIALOGUE_V2_INTERNAL_CANARY_ENABLED": False,
                "DIALOGUE_V2_LOCAL_PREVIEW_ENABLED": False,
                "DIALOGUE_V2_INTERNAL_CANARY_PERCENT": 0,
                "DIALOGUE_V2_LEGACY_DRY_RUN_COMPARE_ENABLED": False,
                "DIALOGUE_V2_FORCE_LEGACY": False,
            },
        ),
    }
    (output_dir / "stage6_release_gate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if targeted_rc == 0 and full100_rc in {None, 0} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
