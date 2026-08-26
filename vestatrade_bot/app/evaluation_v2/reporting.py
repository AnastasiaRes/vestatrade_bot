"""Deterministic, privacy-bounded artifacts for outcome evaluations.

The reporting boundary intentionally accepts only typed contracts and typed
evaluation records.  Raw dialogue transcripts and runtime session identifiers
are neither inputs nor output fields.  All renderers are pure; the single
writer performs local filesystem I/O into a caller-provided empty directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .contracts import (
    CriterionEvaluationMode,
    EvaluationStatus,
    OutcomeContract,
    OutcomeEvaluation,
    OutcomePriority,
    OutcomeVerdict,
    ViolationSeverity,
)
from .evidence import contract_sha256


REPORT_SCHEMA_VERSION = "1.0"
ARTIFACT_FILENAMES = (
    "outcome_contracts.jsonl",
    "outcome_evaluations.jsonl",
    "summary.json",
    "report.md",
    "junit.xml",
    "manifest.json",
)

_SAFE_MANIFEST_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_SAFE_CODE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,300}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_GIT_HASH = re.compile(r"^(?:[a-f0-9]{7,64}|unknown)$")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(r"[\w.+-]+@(?:[\w-]+\.)+[A-Za-zА-Яа-я]{2,}", re.IGNORECASE),
    # Require a leading plus or visible separators to avoid treating a purely
    # numeric catalogue SKU as a phone number.
    re.compile(r"(?:\+\d[\s().-]*|\d[\s().-]+)(?:\d[\s().-]*){8,}"),
)
_FORBIDDEN_PAYLOAD_KEYS = {
    "transcript",
    "raw_transcript",
    "dialogue_transcript",
    "session_id",
    "conversation_id",
    "thread_id",
    "user_text",
    "assistant_text",
    "messages",
    "api_key",
    "openrouter_api_key",
    "authorization",
    "cookie",
    "access_token",
    "refresh_token",
    "secret",
}


def _stable_json(value: Any, *, pretty: bool = False) -> str:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return json.dumps(value, **options)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _assert_public_payload(value: Any, *, path: str = "$") -> None:
    """Reject fields that could turn aggregate artifacts into a data leak."""

    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            if key.casefold() in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"forbidden reporting field at {path}.{key}")
            _assert_public_payload(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_public_payload(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise ValueError(f"secret-like value rejected at {path}")


def _assert_code_token(value: Any, *, path: str) -> None:
    if not isinstance(value, str) or not _SAFE_CODE_TOKEN.fullmatch(value):
        raise ValueError(f"unsafe machine-readable code at {path}")


def _assert_code_sequence(values: Iterable[Any], *, path: str) -> None:
    for index, value in enumerate(values):
        _assert_code_token(value, path=f"{path}[{index}]")


def _public_evaluation_payload(evaluation: OutcomeEvaluation) -> dict[str, Any]:
    """Project typed results onto an allowlisted, transcript-free schema.

    ``MachineViolation.evidence`` is intentionally extensible inside the
    evaluator, so a raw model dump is not a safe reporting boundary.  Public
    artifacts retain rule ids, turns and harmless counters, while dropping
    arbitrary values and hashing transcript-originated SKU strings.  Judge
    rationale is also removed even if an externally constructed assessment
    bypassed the normal judge sanitizer.
    """

    payload = evaluation.model_dump(mode="json")
    _assert_code_sequence(
        payload["gate_blocking_reason_codes"],
        path="$.gate_blocking_reason_codes",
    )
    _assert_code_sequence(payload["reason_codes"], path="$.reason_codes")

    machine = payload["machine"]
    for key in (
        "checked_rule_codes",
        "unchecked_hard_gate_codes",
        "limitation_reason_codes",
        "outcome_blocking_reason_codes",
    ):
        _assert_code_sequence(machine[key], path=f"$.machine.{key}")
    for index, violation in enumerate(machine["violations"]):
        base = f"$.machine.violations[{index}]"
        for key in ("violation_id", "code", "reason_code"):
            _assert_code_token(violation[key], path=f"{base}.{key}")
        product_sku = violation.pop("product_sku", None)
        if product_sku is not None:
            violation["product_sku_sha256"] = hashlib.sha256(
                str(product_sku).encode("utf-8", errors="surrogatepass")
            ).hexdigest()
        evidence = violation.pop("evidence", {})
        safe_counts = {
            key: value
            for key, value in evidence.items()
            if key in {"card_count", "repeat_count"}
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if safe_counts:
            violation["evidence_counts"] = safe_counts
        if evidence:
            violation["evidence_redacted"] = True

    judge = payload["judge"]
    _assert_code_sequence(
        judge["detected_red_flag_ids"],
        path="$.judge.detected_red_flag_ids",
    )
    _assert_code_sequence(judge["reason_codes"], path="$.judge.reason_codes")
    for index, assessment in enumerate(judge["criterion_assessments"]):
        _assert_code_token(
            assessment["criterion_id"],
            path=f"$.judge.criterion_assessments[{index}].criterion_id",
        )
        assessment.pop("rationale", None)
    return payload


def _index_contracts(
    contracts: Iterable[OutcomeContract],
) -> dict[str, OutcomeContract]:
    indexed: dict[str, OutcomeContract] = {}
    contract_ids: set[str] = set()
    for contract in contracts:
        if contract.scenario_id in indexed:
            raise ValueError(f"duplicate contract scenario_id: {contract.scenario_id}")
        if contract.contract_id in contract_ids:
            raise ValueError(f"duplicate contract_id: {contract.contract_id}")
        indexed[contract.scenario_id] = contract
        contract_ids.add(contract.contract_id)
    return indexed


def _index_evaluations(
    evaluations: Iterable[OutcomeEvaluation],
    contracts: Mapping[str, OutcomeContract],
) -> dict[str, OutcomeEvaluation]:
    indexed: dict[str, OutcomeEvaluation] = {}
    for evaluation in evaluations:
        if evaluation.scenario_id in indexed:
            raise ValueError(
                f"duplicate evaluation scenario_id: {evaluation.scenario_id}"
            )
        contract = contracts.get(evaluation.scenario_id)
        if contract is None:
            raise ValueError(
                f"evaluation has no matching contract: {evaluation.scenario_id}"
            )
        if evaluation.contract_id != contract.contract_id:
            raise ValueError(
                f"evaluation contract mismatch: {evaluation.scenario_id}"
            )
        if evaluation.release_eligible:
            if not contract.release_ready:
                raise ValueError(
                    f"release evaluation uses an unapproved contract: {evaluation.scenario_id}"
                )
            if evaluation.evidence_binding.contract_sha256 != contract_sha256(
                contract
            ):
                raise ValueError(
                    f"release evaluation contract digest mismatch: {evaluation.scenario_id}"
                )
            deterministic_failure = any(
                item.severity == ViolationSeverity.P0
                or item.verdict_cap == OutcomeVerdict.FAIL
                for item in evaluation.machine.violations
            )
            if not deterministic_failure:
                expected_criteria = {
                    item.criterion_id
                    for item in contract.criteria
                    if item.evaluation_mode
                    == CriterionEvaluationMode.INDEPENDENT_JUDGE
                }
                observed_criteria = {
                    item.criterion_id
                    for item in evaluation.judge.criterion_assessments
                }
                if observed_criteria != expected_criteria:
                    raise ValueError(
                        "release evaluation criterion coverage mismatch: "
                        f"{evaluation.scenario_id}"
                    )
                if any(
                    item.evaluation_mode == CriterionEvaluationMode.HUMAN
                    for item in contract.criteria
                ):
                    raise ValueError(
                        "release evaluation lacks required human evidence: "
                        f"{evaluation.scenario_id}"
                    )
        indexed[evaluation.scenario_id] = evaluation
    return indexed


def _requested_ids(
    contracts: Mapping[str, OutcomeContract],
    requested_scenario_ids: Iterable[str] | None,
) -> tuple[str, ...]:
    if requested_scenario_ids is None:
        return tuple(sorted(contracts))
    requested = tuple(str(item).strip() for item in requested_scenario_ids)
    if any(not item for item in requested):
        raise ValueError("requested scenario ids must be non-empty")
    if len(requested) != len(set(requested)):
        raise ValueError("requested scenario ids must be unique")
    return tuple(sorted(requested))


def _declared_total_ids(
    requested: tuple[str, ...],
    total_scenario_ids: Iterable[str] | None,
) -> tuple[str, ...] | None:
    """Validate the externally declared full-suite denominator.

    The report layer cannot infer the original suite from a selected collection
    of contracts.  Callers that know the full suite must declare it explicitly;
    legacy callers remain supported, but their aggregate scope is marked as
    undeclared rather than silently called a full run.
    """

    if total_scenario_ids is None:
        return None
    total = tuple(str(item).strip() for item in total_scenario_ids)
    if any(not item for item in total):
        raise ValueError("total scenario ids must be non-empty")
    if len(total) != len(set(total)):
        raise ValueError("total scenario ids must be unique")
    total = tuple(sorted(total))
    missing_from_total = set(requested) - set(total)
    if missing_from_total:
        raise ValueError(
            "selected scenario ids are absent from the declared full suite: "
            f"{sorted(missing_from_total)}"
        )
    return total


def _is_decisive_machine_failure(evaluation: OutcomeEvaluation) -> bool:
    """Return whether deterministic evidence alone fixes the result to FAIL."""

    return bool(
        evaluation.final_verdict == OutcomeVerdict.FAIL
        and any(
            violation.severity == ViolationSeverity.P0
            or violation.verdict_cap == OutcomeVerdict.FAIL
            for violation in evaluation.machine.violations
        )
    )


def _zeroed(keys: Iterable[str]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _sorted_counts(counter: Mapping[str, int]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def build_aggregate_summary(
    contracts: Iterable[OutcomeContract],
    evaluations: Iterable[OutcomeEvaluation],
    *,
    requested_scenario_ids: Iterable[str] | None = None,
    total_scenario_ids: Iterable[str] | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Build an aggregate without conflating verdicts and release evidence.

    Release-ready records, deterministic machine failures and provisional
    semantic verdicts are separate measures. Machine failures are a diagnostic
    subset and may themselves have release-ready evidence.
    """

    contract_map = _index_contracts(tuple(contracts))
    evaluation_map = _index_evaluations(tuple(evaluations), contract_map)
    requested = _requested_ids(contract_map, requested_scenario_ids)
    declared_total = _declared_total_ids(requested, total_scenario_ids)

    verdict_counts = Counter(
        _zeroed([item.value for item in OutcomeVerdict] + ["MISSING"])
    )
    judge_status_counts = Counter(
        _zeroed([item.value for item in EvaluationStatus] + ["missing"])
    )
    priority_rows: dict[str, Counter[str]] = defaultdict(Counter)
    machine_status_counts: Counter[str] = Counter()
    violation_occurrences: Counter[str] = Counter()
    violation_scenarios: dict[str, set[str]] = defaultdict(set)
    severity_occurrences = Counter(
        _zeroed(item.value for item in ViolationSeverity)
    )
    machine_limitations: Counter[str] = Counter()
    machine_limitation_scenarios: dict[str, set[str]] = defaultdict(set)
    judge_limitations: Counter[str] = Counter()
    judge_limitation_scenarios: dict[str, set[str]] = defaultdict(set)
    gate_blockers: Counter[str] = Counter()
    evaluation_reasons: Counter[str] = Counter()
    release_counts: Counter[str] = Counter(
        {"eligible": 0, "ineligible": 0, "missing_evaluation": 0}
    )
    normalization_counts: Counter[str] = Counter()
    recorded = 0
    release_ready = 0
    decisive_machine_failures = 0
    provisional_evaluated = 0
    unavailable_records = 0
    missing_contract = 0

    for scenario_id in requested:
        contract = contract_map.get(scenario_id)
        priority = contract.priority.value if contract else "MISSING_CONTRACT"
        if contract is not None:
            normalization_counts[contract.normalization_status.value] += 1
        priority_rows[priority]["requested"] += 1
        evaluation = evaluation_map.get(scenario_id)
        if contract is None:
            missing_contract += 1
        if evaluation is None:
            verdict_counts["MISSING"] += 1
            judge_status_counts["missing"] += 1
            release_counts["missing_evaluation"] += 1
            priority_rows[priority]["missing"] += 1
            continue

        recorded += 1
        priority_rows[priority]["recorded"] += 1
        verdict_counts[evaluation.final_verdict.value] += 1
        judge_status_counts[evaluation.judge.status.value] += 1
        machine_status_counts[evaluation.machine.status.value] += 1
        if (
            evaluation.release_eligible
            and evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE
        ):
            raise ValueError(
                "release-eligible evaluation cannot have unavailable verdict"
            )
        if evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE:
            unavailable_records += 1
            priority_rows[priority]["unavailable"] += 1
        elif evaluation.release_eligible:
            release_ready += 1
            priority_rows[priority]["release_ready"] += 1
        else:
            provisional_evaluated += 1
            priority_rows[priority]["provisional_evaluated"] += 1
        if _is_decisive_machine_failure(evaluation):
            decisive_machine_failures += 1
            priority_rows[priority]["decisive_machine_failures"] += 1
        priority_rows[priority][f"verdict_{evaluation.final_verdict.value}"] += 1

        release_counts[
            "eligible" if evaluation.release_eligible else "ineligible"
        ] += 1
        for violation in evaluation.machine.violations:
            violation_occurrences[violation.code] += 1
            violation_scenarios[violation.code].add(scenario_id)
            severity_occurrences[violation.severity.value] += 1
        for reason in evaluation.machine.limitation_reason_codes:
            machine_limitations[reason] += 1
            machine_limitation_scenarios[reason].add(scenario_id)
        if evaluation.judge.status != EvaluationStatus.EVALUATED:
            for reason in evaluation.judge.reason_codes:
                judge_limitations[reason] += 1
                judge_limitation_scenarios[reason].add(scenario_id)
        for reason in evaluation.gate_blocking_reason_codes:
            gate_blockers[reason] += 1
        for reason in evaluation.reason_codes:
            evaluation_reasons[reason] += 1

    requested_count = len(requested)
    missing_evaluation = requested_count - recorded
    unrequested_records = len(set(evaluation_map) - set(requested))
    unavailable_or_missing = unavailable_records + missing_evaluation
    if (
        release_ready + provisional_evaluated + unavailable_or_missing
        != requested_count
    ):
        raise AssertionError("outcome denominator buckets must cover selection")
    release_ready_ratio = (
        release_ready / requested_count if requested_count else 0.0
    )

    priority_counts: dict[str, dict[str, int]] = {}
    priority_order = [item.value for item in OutcomePriority] + [
        "MISSING_CONTRACT"
    ]
    for priority in priority_order:
        row = priority_rows.get(priority, Counter())
        if not row and priority == "MISSING_CONTRACT":
            continue
        normalized = {
            "requested": row["requested"],
            "recorded": row["recorded"],
            "release_ready": row["release_ready"],
            "decisive_machine_failures": row[
                "decisive_machine_failures"
            ],
            "provisional_evaluated": row["provisional_evaluated"],
            "missing": row["missing"],
            "unavailable": row["unavailable"],
        }
        normalized.update(
            {
                f"verdict_{verdict.value}": row[f"verdict_{verdict.value}"]
                for verdict in OutcomeVerdict
            }
        )
        priority_counts[priority] = normalized

    selection_partial = bool(
        declared_total is not None and set(requested) != set(declared_total)
    )
    evaluation_complete = bool(
        missing_evaluation == 0 and missing_contract == 0
    )
    summary: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_label": source_label,
        "scope": {
            "scope_declared": declared_total is not None,
            "total_contract_scenarios": (
                len(declared_total) if declared_total is not None else None
            ),
            "selected_scenario_ids": list(requested),
            "selection_partial": (
                selection_partial if declared_total is not None else None
            ),
            "evaluation_complete": evaluation_complete,
            "partial_run": (
                selection_partial or not evaluation_complete
                if declared_total is not None
                else None
            ),
        },
        "denominator": {
            "requested": requested_count,
            "evaluation_records": recorded,
            "release_ready": release_ready,
            "decisive_machine_failures": decisive_machine_failures,
            "provisional_evaluated": provisional_evaluated,
            "unavailable_evaluation_records": unavailable_records,
            "unavailable_or_missing": unavailable_or_missing,
            "missing_evaluation_records": missing_evaluation,
            "missing_contracts": missing_contract,
            "unrequested_evaluation_records": unrequested_records,
            "release_ready_ratio": round(release_ready_ratio, 6),
            "release_ready_display": f"{release_ready}/{requested_count}",
        },
        "verdict_counts": _sorted_counts(verdict_counts),
        "judge_status_counts": _sorted_counts(judge_status_counts),
        "priority_counts": priority_counts,
        "machine_assessment_status_counts": _sorted_counts(
            machine_status_counts
        ),
        "machine_violation_counts": {
            "occurrences_by_code": _sorted_counts(violation_occurrences),
            "affected_scenarios_by_code": {
                key: len(violation_scenarios[key])
                for key in sorted(violation_scenarios)
            },
            "occurrences_by_severity": _sorted_counts(severity_occurrences),
        },
        "limitation_counts": {
            "machine_occurrences_by_reason": _sorted_counts(machine_limitations),
            "machine_affected_scenarios_by_reason": {
                key: len(machine_limitation_scenarios[key])
                for key in sorted(machine_limitation_scenarios)
            },
            "judge_occurrences_by_reason": _sorted_counts(judge_limitations),
            "judge_affected_scenarios_by_reason": {
                key: len(judge_limitation_scenarios[key])
                for key in sorted(judge_limitation_scenarios)
            },
        },
        "gate_blocking_reason_counts": _sorted_counts(gate_blockers),
        "evaluation_reason_counts": _sorted_counts(evaluation_reasons),
        "release_eligibility_counts": _sorted_counts(release_counts),
        "contract_normalization_counts": _sorted_counts(normalization_counts),
    }
    _assert_public_payload(summary)
    return summary


def render_markdown_report(summary: Mapping[str, Any]) -> str:
    """Render a compact aggregate report without scenario dialogue content."""

    _assert_public_payload(summary)
    denominator = summary["denominator"]
    scope = summary.get("scope") or {}
    if scope.get("scope_declared"):
        scope_line = (
            "Run scope: "
            f"**{len(scope['selected_scenario_ids'])}/"
            f"{scope['total_contract_scenarios']}** scenarios selected "
            f"({'partial' if scope['selection_partial'] else 'full'} selection); "
            f"**{denominator['evaluation_records']}/"
            f"{denominator['requested']}** evaluation records "
            f"({'complete' if scope['evaluation_complete'] else 'incomplete'})."
        )
    else:
        scope_line = (
            "Run scope: **undeclared**; full-suite release coverage cannot be "
            "inferred from this report."
        )
    lines = [
        "# Outcome evaluation report",
        "",
        f"Source: `{summary.get('source_label') or 'unspecified'}`",
        "",
        scope_line,
        "",
        (
            "Release-ready evidence: "
            f"**{denominator['release_ready']}/{denominator['requested']}** "
            "selected scenarios."
        ),
        (
            "Decisive machine failures: "
            f"**{denominator['decisive_machine_failures']}**; "
            "this is a diagnostic subset, not successful release coverage."
        ),
        (
            "Provisional evaluated results: "
            f"**{denominator['provisional_evaluated']}**."
        ),
        (
            "Unavailable or missing results: "
            f"**{denominator['unavailable_or_missing']}**."
        ),
        "",
        "## Coverage",
        "",
        "| Measure | Count |",
        "|---|---:|",
    ]
    coverage_labels = (
        ("Selected", "requested"),
        ("Evaluation records", "evaluation_records"),
        ("Release-ready evidence", "release_ready"),
        ("Decisive machine failures", "decisive_machine_failures"),
        ("Provisional evaluated", "provisional_evaluated"),
        ("Unavailable or missing", "unavailable_or_missing"),
        ("Missing contracts", "missing_contracts"),
    )
    lines.extend(
        f"| {label} | {denominator[key]} |" for label, key in coverage_labels
    )

    def add_count_table(title: str, counts: Mapping[str, int]) -> None:
        lines.extend(["", f"## {title}", "", "| Value | Count |", "|---|---:|"])
        if counts:
            lines.extend(f"| `{key}` | {counts[key]} |" for key in sorted(counts))
        else:
            lines.append("| _none_ | 0 |")

    add_count_table("Verdicts", summary["verdict_counts"])
    add_count_table("Judge status", summary["judge_status_counts"])
    add_count_table(
        "Machine violations",
        summary["machine_violation_counts"]["occurrences_by_code"],
    )
    add_count_table(
        "Machine limitations",
        summary["limitation_counts"]["machine_occurrences_by_reason"],
    )
    add_count_table(
        "Judge limitations",
        summary["limitation_counts"]["judge_occurrences_by_reason"],
    )
    add_count_table(
        "Release eligibility",
        summary["release_eligibility_counts"],
    )
    add_count_table(
        "Contract normalization",
        summary["contract_normalization_counts"],
    )

    lines.extend(
        [
            "",
            "## Priority coverage",
            "",
            (
                "| Priority | Selected | Release-ready | Machine failures | "
                "Provisional | Unavailable | Missing |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for priority, row in summary["priority_counts"].items():
        lines.append(
            f"| `{priority}` | {row['requested']} | {row['release_ready']} | "
            f"{row['decisive_machine_failures']} | "
            f"{row['provisional_evaluated']} | {row['unavailable']} | "
            f"{row['missing']} |"
        )
    return "\n".join(lines) + "\n"


def build_junit_xml(
    contracts: Iterable[OutcomeContract],
    evaluations: Iterable[OutcomeEvaluation],
    *,
    requested_scenario_ids: Iterable[str] | None = None,
    total_scenario_ids: Iterable[str] | None = None,
    suite_name: str = "outcome-evaluation-v2",
) -> str:
    """Render JUnit where every requested scenario is a concrete testcase.

    PARTIAL and FAIL are failures; UNAVAILABLE or an absent record is an error.
    No outcome is represented as ``skipped``.
    """

    contract_map = _index_contracts(tuple(contracts))
    evaluation_map = _index_evaluations(tuple(evaluations), contract_map)
    requested = _requested_ids(contract_map, requested_scenario_ids)
    declared_total = _declared_total_ids(requested, total_scenario_ids)
    suite_scenarios = declared_total or requested
    requested_set = set(requested)
    failures = 0
    errors = 0
    suite = ET.Element(
        "testsuite",
        {
            "name": suite_name,
            "tests": str(len(suite_scenarios)),
            "failures": "0",
            "errors": "0",
            "skipped": "0",
            "time": "0",
        },
    )
    for scenario_id in suite_scenarios:
        contract = contract_map.get(scenario_id)
        evaluation = evaluation_map.get(scenario_id)
        priority = contract.priority.value if contract else "MISSING_CONTRACT"
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"outcome_evaluation.{priority}",
                "name": scenario_id,
                "time": "0",
            },
        )
        if scenario_id not in requested_set:
            errors += 1
            node = ET.SubElement(
                case,
                "error",
                {
                    "type": "NOT_SELECTED",
                    "message": "scenario omitted from partial evaluation run",
                },
            )
            node.text = (
                "The declared full-suite scenario was not selected for this run."
            )
            continue
        if evaluation is None:
            errors += 1
            node = ET.SubElement(
                case,
                "error",
                {
                    "type": "UNAVAILABLE",
                    "message": (
                        "missing contract and evaluation"
                        if contract is None
                        else "missing evaluation"
                    ),
                },
            )
            node.text = "No typed outcome evaluation record was produced."
            continue
        if evaluation.final_verdict == OutcomeVerdict.UNAVAILABLE:
            errors += 1
            reasons = sorted(
                set(evaluation.reason_codes)
                | set(evaluation.judge.reason_codes)
                | set(evaluation.machine.limitation_reason_codes)
            )
            error_payload = {"reason_codes": reasons}
            _assert_public_payload(error_payload)
            node = ET.SubElement(
                case,
                "error",
                {"type": "UNAVAILABLE", "message": "outcome unavailable"},
            )
            node.text = _stable_json(error_payload)
        elif evaluation.final_verdict in {
            OutcomeVerdict.PARTIAL,
            OutcomeVerdict.FAIL,
        }:
            failures += 1
            node = ET.SubElement(
                case,
                "failure",
                {
                    "type": evaluation.final_verdict.value,
                    "message": f"outcome {evaluation.final_verdict.value.lower()}",
                },
            )
            failure_payload = {
                "gate_blocking_reason_codes": sorted(
                    evaluation.gate_blocking_reason_codes
                ),
                "machine_violation_codes": sorted(
                    {item.code for item in evaluation.machine.violations}
                ),
                "reason_codes": sorted(evaluation.reason_codes),
            }
            _assert_public_payload(failure_payload)
            node.text = _stable_json(failure_payload)
        elif not evaluation.release_eligible:
            # A provisional semantic PASS is useful analysis, but it must not
            # silently turn a source-imported/unverified contract into a green
            # release signal.
            errors += 1
            node = ET.SubElement(
                case,
                "error",
                {
                    "type": "PROVISIONAL",
                    "message": "pass is not release-eligible evidence",
                },
            )
            node.text = _stable_json(
                {
                    "evidence_grade": evaluation.evidence_grade.value,
                    "reason_codes": sorted(evaluation.reason_codes),
                }
            )
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    return ET.tostring(
        suite,
        encoding="unicode",
        xml_declaration=True,
        short_empty_elements=True,
    ) + "\n"


def _jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    serialized: list[str] = []
    for record in records:
        _assert_public_payload(record)
        serialized.append(_stable_json(record))
    return "\n".join(serialized) + ("\n" if serialized else "")


def _validate_manifest_inputs(
    *,
    source_label: str,
    input_hashes: Mapping[str, str],
    prompt_version: str,
    prompt_hash: str,
    judge_model: str | None,
    bot_model: str | None,
    bot_strong_model: str | None,
    judge_lineage_registry_version: str | None,
    git_hash: str,
    git_status_sha256: str | None,
) -> None:
    if not _SAFE_MANIFEST_LABEL.fullmatch(source_label):
        raise ValueError("source_label must be a safe logical label")
    if not prompt_version or len(prompt_version) > 160:
        raise ValueError("prompt_version must be between 1 and 160 characters")
    if not _SHA256.fullmatch(prompt_hash):
        raise ValueError("prompt_hash must be a lowercase sha256 digest")
    if not _GIT_HASH.fullmatch(git_hash):
        raise ValueError("git_hash must be a git object prefix or 'unknown'")
    if git_status_sha256 is not None and not _SHA256.fullmatch(git_status_sha256):
        raise ValueError("git_status_sha256 must be a lowercase sha256 digest")
    if not input_hashes:
        raise ValueError("at least one immutable input hash is required")
    for label, digest in input_hashes.items():
        if not _SAFE_MANIFEST_LABEL.fullmatch(str(label)):
            raise ValueError("input hash labels must be safe logical names")
        if not _SHA256.fullmatch(str(digest)):
            raise ValueError(f"invalid sha256 digest for input {label}")
    for model in (judge_model, bot_model, bot_strong_model):
        if model is not None and (not model.strip() or len(model) > 200):
            raise ValueError("model names must be non-empty and bounded")
    if judge_lineage_registry_version is not None and not _SAFE_MANIFEST_LABEL.fullmatch(
        judge_lineage_registry_version
    ):
        raise ValueError("judge lineage registry version must be a safe label")
    _assert_public_payload(
        {
            "source_label": source_label,
            "prompt_version": prompt_version,
            "judge_model": judge_model,
            "bot_model": bot_model,
            "bot_strong_model": bot_strong_model,
            "judge_lineage_registry_version": judge_lineage_registry_version,
        }
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise ValueError(f"temporary output path already exists: {temporary.name}")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _publish_artifact_directory(
    destination: Path,
    contents: Mapping[str, bytes],
) -> None:
    """Publish a complete artifact set with one final directory rename.

    The destination must not exist. Every payload is first written into a
    private sibling directory, with the manifest written last. A failed write
    removes that unpublished directory and can therefore never expose a partial
    destination containing an apparently complete manifest.
    """

    if destination.exists() or destination.is_symlink():
        raise ValueError("output directory must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.publishing-",
            dir=destination.parent,
        )
    )
    try:
        for filename in sorted(set(contents) - {"manifest.json"}):
            _atomic_write(temporary / filename, contents[filename])
        _atomic_write(temporary / "manifest.json", contents["manifest.json"])
        if destination.exists() or destination.is_symlink():
            raise ValueError("output directory appeared during publication")
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def write_evaluation_artifacts(
    output_dir: str | Path,
    *,
    contracts: Iterable[OutcomeContract],
    evaluations: Iterable[OutcomeEvaluation],
    source_label: str,
    input_hashes: Mapping[str, str],
    prompt_version: str,
    prompt_hash: str,
    judge_model: str | None,
    bot_model: str | None,
    git_hash: str,
    bot_strong_model: str | None = None,
    judge_lineage_registry_version: str | None = None,
    git_dirty: bool | None = None,
    git_status_sha256: str | None = None,
    requested_scenario_ids: Iterable[str] | None = None,
    total_scenario_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Atomically publish the artifact set to a new directory.

    The returned manifest is the exact public object persisted as
    ``manifest.json``.  The manifest hashes every other artifact; it does not
    attempt the impossible self-hash of its own serialized representation.
    """

    _validate_manifest_inputs(
        source_label=source_label,
        input_hashes=input_hashes,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        judge_model=judge_model,
        bot_model=bot_model,
        bot_strong_model=bot_strong_model,
        judge_lineage_registry_version=judge_lineage_registry_version,
        git_hash=git_hash,
        git_status_sha256=git_status_sha256,
    )
    contract_map = _index_contracts(tuple(contracts))
    evaluation_map = _index_evaluations(tuple(evaluations), contract_map)
    requested = _requested_ids(contract_map, requested_scenario_ids)
    declared_total = _declared_total_ids(requested, total_scenario_ids)
    selected_contracts = tuple(
        contract_map[item] for item in requested if item in contract_map
    )
    selected_evaluations = tuple(
        evaluation_map[item] for item in requested if item in evaluation_map
    )
    if any(item.source_label != source_label for item in selected_evaluations):
        raise ValueError("evaluation source labels must match the report source")

    contract_payloads = [
        item.model_dump(mode="json") for item in selected_contracts
    ]
    evaluation_payloads = [
        _public_evaluation_payload(item) for item in selected_evaluations
    ]
    summary = build_aggregate_summary(
        selected_contracts,
        selected_evaluations,
        requested_scenario_ids=requested,
        total_scenario_ids=declared_total,
        source_label=source_label,
    )
    contents: dict[str, bytes] = {
        "outcome_contracts.jsonl": _jsonl(contract_payloads).encode("utf-8"),
        "outcome_evaluations.jsonl": _jsonl(evaluation_payloads).encode(
            "utf-8"
        ),
        "summary.json": (_stable_json(summary, pretty=True) + "\n").encode(
            "utf-8"
        ),
        "report.md": render_markdown_report(summary).encode("utf-8"),
        "junit.xml": build_junit_xml(
            selected_contracts,
            selected_evaluations,
            requested_scenario_ids=requested,
            total_scenario_ids=declared_total,
        ).encode("utf-8"),
    }
    denominator = summary["denominator"]
    scope = summary["scope"]
    non_unavailable = (
        denominator["release_ready"] + denominator["provisional_evaluated"]
    )
    manifest: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_label": source_label,
        "input_hashes": {
            key: input_hashes[key] for key in sorted(input_hashes)
        },
        "outcome_judge_prompt": {
            "version": prompt_version,
            "sha256": prompt_hash,
        },
        "models": {
            "bot": bot_model,
            "bot_strong": bot_strong_model,
            "judge": judge_model,
            "independence_policy_version": judge_lineage_registry_version,
        },
        "git_hash": git_hash,
        "git": {
            "commit": git_hash,
            "dirty": git_dirty,
            "status_sha256": git_status_sha256,
        },
        # ``requested_scenarios`` and ``evaluated_scenarios`` remain as
        # compatibility fields for the first Stage 7 runner. The accompanying
        # semantics prevents the latter from being mistaken for release-ready
        # coverage; new consumers must use the explicitly named counters.
        "requested_scenarios": len(requested),
        "evaluated_scenarios": non_unavailable,
        "evaluated_scenarios_semantics": (
            "deprecated_non_unavailable_including_provisional_"
            "not_release_coverage"
        ),
        "non_unavailable_scenarios": non_unavailable,
        "selected_scenarios": len(requested),
        "total_contract_scenarios": scope["total_contract_scenarios"],
        "selected_scenario_ids": scope["selected_scenario_ids"],
        "scope_declared": scope["scope_declared"],
        "partial_run": scope["partial_run"],
        "selection_partial": scope["selection_partial"],
        "evaluation_complete": scope["evaluation_complete"],
        "release_ready_scenarios": denominator["release_ready"],
        "decisive_machine_failure_scenarios": denominator[
            "decisive_machine_failures"
        ],
        "provisional_evaluated_scenarios": denominator[
            "provisional_evaluated"
        ],
        "unavailable_or_missing_scenarios": denominator[
            "unavailable_or_missing"
        ],
        "artifacts": {
            name: {"sha256": _sha256_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(contents.items())
        },
    }
    _assert_public_payload(manifest)
    contents["manifest.json"] = (
        _stable_json(manifest, pretty=True) + "\n"
    ).encode("utf-8")
    if set(contents) != set(ARTIFACT_FILENAMES):
        raise AssertionError("report artifact set is incomplete")

    # All payload construction and privacy validation happens before touching
    # the destination. Publication itself is one final directory rename.
    destination = Path(output_dir).expanduser()
    _publish_artifact_directory(destination, contents)
    return manifest


__all__ = [
    "ARTIFACT_FILENAMES",
    "REPORT_SCHEMA_VERSION",
    "build_aggregate_summary",
    "build_junit_xml",
    "render_markdown_report",
    "write_evaluation_artifacts",
]
