"""Safe, deterministic borehole-pump duty derivation for V2.

This module is deliberately an adapter over the established Legacy hydraulic
normalizer.  It accepts only already typed V2 facts, never message text or an
LLM proposal, and produces a *preliminary* customer requirement.  Catalogue
maximum ratings can subsequently rule out insufficient pumps, but they cannot
confirm a Q/H operating point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.agents.engineering_calculations import normalize_engineering_slots
from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintStatus,
    CustomerTask,
    DialogueStateV2,
)

from .contracts import ProductContract
from .normalization import normalize_unit_label


class BoreholeHydraulicStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    NEEDS_INPUT = "needs_input"
    DERIVED_PRELIMINARY = "derived_preliminary"
    EXPLICIT_REQUIREMENT = "explicit_requirement"
    INVALID = "invalid"


@dataclass(frozen=True)
class BoreholeHydraulicResult:
    """A typed calculation outcome, not a recommendation or a product match."""

    task_id: str
    goal_id: str | None
    status: BoreholeHydraulicStatus
    required_head_m: float | None = None
    missing_fact_names: tuple[str, ...] = ()
    source_fact_ids: tuple[str, ...] = ()
    evidence: str = ""
    reason_codes: tuple[str, ...] = ()


_INPUT_FACT_NAMES = (
    "dynamic_water_level_m",
    "static_water_level_m",
    "lift_height_m",
    "horizontal_run_m",
    "required_pressure_bar",
    "required_flow_l_h",
    "discharge_diameter_mm",
    "discharge_sdr",
)
_CALCULATED_HEAD_SOURCE = "borehole_hydraulic_calculation"
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _scoped_active_facts(
    state: DialogueStateV2,
    task: CustomerTask,
) -> dict[str, ConstraintFactV2]:
    """Return the latest typed fact of each borehole predicate in task scope."""

    selected: dict[str, ConstraintFactV2] = {}
    for fact in state.constraints:
        if not fact.active:
            continue
        if task.target_goal_id is not None:
            if fact.goal_id not in {None, task.target_goal_id}:
                continue
            if fact.goal_id is None and fact.task_id not in {None, task.task_id}:
                continue
        elif fact.task_id not in {None, task.task_id}:
            continue
        if fact.name in {*_INPUT_FACT_NAMES, "required_head_m"}:
            selected[fact.name] = fact
    return selected


def _number_in_contract_unit(
    fact: ConstraintFactV2,
    contract: ProductContract,
) -> float | None:
    """Read one explicit scalar using the contract's declared conversion map."""

    definition = next(
        (item for item in contract.fact_definitions if item.name == fact.name),
        None,
    )
    if definition is None or fact.status != ConstraintStatus.KNOWN:
        return None
    if isinstance(fact.value, bool) or fact.value is None:
        return None
    if isinstance(fact.value, (int, float)):
        numeric = float(fact.value)
    else:
        matches = _NUMBER_RE.findall(str(fact.value))
        if len(matches) != 1:
            return None
        numeric = float(matches[0].replace(",", "."))
    if numeric < 0:
        return None
    if fact.name == "discharge_sdr":
        return numeric if numeric > 0 else None
    unit = normalize_unit_label(fact.unit or "")
    if not unit:
        # The V2 semantic contract requires a unit for each physical input.
        # Guessing it here would convert an ambiguous phrase into a hydraulic
        # calculation, which is exactly what this adapter is meant to avoid.
        return None
    factor = definition.unit_conversions.get(unit)
    if factor is None:
        return None
    converted = numeric * factor
    return converted if converted >= 0 else None


def derive_borehole_hydraulics(
    state: DialogueStateV2,
    task: CustomerTask,
    contract: ProductContract | None,
) -> BoreholeHydraulicResult:
    """Build a provisional head requirement from V2 facts when possible.

    The order of missing facts follows the verified Legacy questionnaire.  An
    explicit required head is retained as a customer requirement and is never
    silently replaced by a recalculation.
    """

    if contract is None or contract.contract_id != "pump.borehole.v1":
        return BoreholeHydraulicResult(
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            status=BoreholeHydraulicStatus.NOT_APPLICABLE,
        )

    facts = _scoped_active_facts(state, task)
    explicit_head = facts.get("required_head_m")
    if (
        explicit_head is not None
        and explicit_head.source != _CALCULATED_HEAD_SOURCE
        and _number_in_contract_unit(explicit_head, contract) is not None
    ):
        flow = facts.get("required_flow_l_h")
        if flow is None or _number_in_contract_unit(flow, contract) is None:
            return BoreholeHydraulicResult(
                task_id=task.task_id,
                goal_id=task.target_goal_id,
                status=BoreholeHydraulicStatus.NEEDS_INPUT,
                missing_fact_names=("required_flow_l_h",),
                source_fact_ids=(explicit_head.fact_id,),
                reason_codes=("borehole_required_flow_missing",),
            )
        return BoreholeHydraulicResult(
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            status=BoreholeHydraulicStatus.EXPLICIT_REQUIREMENT,
            source_fact_ids=(explicit_head.fact_id, flow.fact_id),
            reason_codes=("borehole_explicit_customer_head_retained",),
        )

    water_level = facts.get("dynamic_water_level_m") or facts.get(
        "static_water_level_m"
    )
    ordered_inputs = (
        ("dynamic_water_level_m", water_level),
        ("lift_height_m", facts.get("lift_height_m")),
        ("horizontal_run_m", facts.get("horizontal_run_m")),
        ("required_pressure_bar", facts.get("required_pressure_bar")),
        ("required_flow_l_h", facts.get("required_flow_l_h")),
        ("discharge_diameter_mm", facts.get("discharge_diameter_mm")),
    )
    missing = tuple(
        name
        for name, fact in ordered_inputs
        if fact is None or _number_in_contract_unit(fact, contract) is None
    )
    if missing:
        return BoreholeHydraulicResult(
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            status=BoreholeHydraulicStatus.NEEDS_INPUT,
            missing_fact_names=missing,
            source_fact_ids=tuple(
                fact.fact_id for _, fact in ordered_inputs if fact is not None
            ),
            reason_codes=("borehole_hydraulic_input_missing",),
        )

    # Inputs are canonicalised by the product contract above.  The Legacy
    # normalizer deliberately remains the single implementation of the actual
    # Darcy–Weisbach formula and its allowances.
    assert water_level is not None
    slots = {
        "pump_type": "скважинный",
        "water_source": "скважина",
        "dynamic_water_level_m": _number_in_contract_unit(water_level, contract),
        "lift_height_m": _number_in_contract_unit(
            facts["lift_height_m"], contract
        ),
        "horizontal_run_m": _number_in_contract_unit(
            facts["horizontal_run_m"], contract
        ),
        "required_pressure_bar": _number_in_contract_unit(
            facts["required_pressure_bar"], contract
        ),
        # The common normalizer expects m³/h. The V2 requirement uses l/h as
        # its canonical catalogue comparison unit.
        "required_flow_m3_h": _number_in_contract_unit(
            facts["required_flow_l_h"], contract
        )
        / 1000.0,
        "discharge_diameter_mm": _number_in_contract_unit(
            facts["discharge_diameter_mm"], contract
        ),
    }
    sdr = facts.get("discharge_sdr")
    if sdr is not None:
        normalized_sdr = _number_in_contract_unit(sdr, contract)
        if normalized_sdr is not None:
            slots["discharge_sdr"] = normalized_sdr

    normalized = normalize_engineering_slots(slots)
    calculated = normalized.get("required_head_m")
    if not isinstance(calculated, (int, float)) or isinstance(calculated, bool):
        return BoreholeHydraulicResult(
            task_id=task.task_id,
            goal_id=task.target_goal_id,
            status=BoreholeHydraulicStatus.INVALID,
            source_fact_ids=tuple(fact.fact_id for _, fact in ordered_inputs),
            reason_codes=("borehole_hydraulic_calculation_not_derived",),
        )

    basis = str(normalized.get("discharge_diameter_basis") or "").strip()
    evidence = "Предварительный расчёт напора: уровень воды, подъём, трасса, расход и давление"
    if basis:
        evidence = f"{evidence}; диаметр трубы: {basis}"
    return BoreholeHydraulicResult(
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        status=BoreholeHydraulicStatus.DERIVED_PRELIMINARY,
        required_head_m=round(float(calculated), 3),
        source_fact_ids=tuple(fact.fact_id for _, fact in ordered_inputs),
        evidence=evidence[:240],
        reason_codes=(
            "borehole_head_derived_by_shared_darcy_weisbach_adapter",
            "borehole_qh_curve_confirmation_required",
        ),
    )
