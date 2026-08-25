"""Pure contract-based task readiness assessment."""

from __future__ import annotations

import re

from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintStatus,
    ConstraintStrength,
    CustomerTask,
    DialogueStateV2,
)

from .contracts import (
    ContractResolution,
    ContractResolutionStatus,
    FactStrength,
    FactValueType,
    ProductKind,
    ProductContract,
    ReadinessFact,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from .normalization import normalize_fact_value


def canonical_fact_name(contract: ProductContract, name: str) -> str | None:
    normalized = " ".join(str(name or "").casefold().replace("ё", "е").replace("-", "_").split())
    for definition in contract.fact_definitions:
        accepted = (definition.name, *definition.aliases)
        if normalized in {
            " ".join(item.casefold().replace("ё", "е").replace("-", "_").split())
            for item in accepted
        }:
            return definition.name
    return None


def _applicable_facts(
    state: DialogueStateV2,
    task: CustomerTask,
    contract: ProductContract,
) -> dict[str, ConstraintFactV2]:
    selected: dict[str, ConstraintFactV2] = {}
    for fact in state.constraints:
        if not fact.active:
            continue
        if fact.task_id not in {None, task.task_id}:
            continue
        if task.target_goal_id and fact.goal_id not in {None, task.target_goal_id}:
            continue
        canonical = canonical_fact_name(contract, fact.name)
        if canonical is not None:
            selected[canonical] = fact
    return selected


def _canonical_value(definition, fact: ConstraintFactV2):
    value = normalize_fact_value(definition.name, fact.value)
    if definition.value_type == FactValueType.NUMBER and isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
        if match:
            parsed = float(match.group(0).replace(",", "."))
            value = int(parsed) if parsed.is_integer() else parsed
    raw_unit = fact.unit or ""
    if not raw_unit and isinstance(fact.value, str):
        normalized_value = fact.value.casefold().replace("³", "3")
        raw_unit = next(
            (
                unit for unit in ("l/min", "л/мин", "m3/h", "м3/ч", "l/h", "л/ч", "mm", "мм", "cm", "см", "kw", "квт", "w", "вт", "m", "м")
                if unit in normalized_value
            ),
            "",
        )
    unit = str(raw_unit).casefold().replace("³", "3").replace(" ", "")
    unit = {"мм": "mm", "см": "cm", "м": "m"}.get(unit, unit)
    if definition.unit_conversions and isinstance(value, (int, float)) and unit:
        factor = definition.unit_conversions.get(unit)
        if factor is not None:
            converted = float(value) * factor
            return int(converted) if converted.is_integer() else converted
    return value


def _canonical_unit(definition, fact: ConstraintFactV2) -> str | None:
    return {
        "length_mm": "mm",
        "length_m": "m",
        "head_m": "m",
        "angle_deg": "deg",
        "power_kw": "kW",
        "power_w": "W",
        "flow": "l/h",
    }.get(definition.unit_family, fact.unit)


def assess_task_readiness(
    dialogue_state: DialogueStateV2,
    customer_task: CustomerTask,
    product_contract: ProductContract | None,
    resolution: ContractResolution | None = None,
) -> TaskReadinessAssessment:
    """Assess facts without choosing a catalogue item or inspecting message text."""

    if product_contract is None:
        status = (
            ReadinessStatus.AMBIGUOUS
            if resolution and resolution.status == ContractResolutionStatus.AMBIGUOUS
            else ReadinessStatus.UNSUPPORTED
        )
        return TaskReadinessAssessment(
            task_id=customer_task.task_id,
            goal_id=resolution.goal_id if resolution else customer_task.target_goal_id,
            status=status,
            reason_codes=(
                *(resolution.reason_codes if resolution else ()),
                "readiness_has_no_product_contract",
            ),
        )

    selected = _applicable_facts(dialogue_state, customer_task, product_contract)
    hard: list[ReadinessFact] = []
    soft: list[ReadinessFact] = []
    missing: list[str] = []
    unknown: list[str] = []
    refused: list[str] = []
    deferred: list[str] = []
    conflicts: list[str] = []
    definition_by_name = {
        item.name: item for item in product_contract.fact_definitions
    }

    for name, fact in selected.items():
        definition = definition_by_name[name]
        effective_strength = (
            FactStrength.HARD
            if definition.strength == FactStrength.HARD
            or fact.strength == ConstraintStrength.HARD
            else FactStrength.SOFT
        )
        readiness_fact = ReadinessFact(
            name=name,
            status=fact.status.value,
            value=(
                _canonical_value(definition, fact)
                if fact.status == ConstraintStatus.KNOWN
                else None
            ),
            unit=_canonical_unit(definition, fact),
            strength=effective_strength,
            polarity=fact.polarity.value,
        )
        (hard if effective_strength == FactStrength.HARD else soft).append(readiness_fact)
        if fact.status == ConstraintStatus.UNKNOWN:
            unknown.append(name)
        elif fact.status == ConstraintStatus.REFUSED:
            refused.append(name)
        elif fact.status == ConstraintStatus.DEFERRED:
            deferred.append(name)

    if product_contract.product_kind in {
        ProductKind.CIRCULATION_PUMP,
        ProductKind.DHW_CIRCULATION_PUMP,
    }:
        diameter_source = selected.get("diameter_mm")
        designation = re.fullmatch(
            r"\s*(\d{1,3})\s*/\s*(\d{1,2})(?:\s*-\s*(\d{2,3}))?\s*",
            str(diameter_source.value if diameter_source else ""),
        )
        if designation and diameter_source.status == ConstraintStatus.KNOWN:
            derived = (
                ("max_head_m", int(designation.group(2)), "m"),
                *((
                    ("mounting_length_mm", int(designation.group(3)), "mm"),
                ) if designation.group(3) else ()),
            )
            for name, value, unit in derived:
                if name in selected:
                    continue
                definition = definition_by_name[name]
                target = hard if definition.strength == FactStrength.HARD else soft
                target.append(
                    ReadinessFact(
                        name=name,
                        status="known",
                        value=value,
                        unit=unit,
                        strength=definition.strength,
                        polarity=diameter_source.polarity.value,
                    )
                )

    required = [
        item for item in product_contract.fact_definitions
        if item.required_for_exact
    ]
    known_names = {
        item.name for item in (*hard, *soft) if item.status == "known"
    }
    terminal_names = {
        item.name for item in (*hard, *soft)
    }
    if "sku" in known_names:
        required = []
    for definition in required:
        if definition.name not in terminal_names:
            missing.append(definition.name)

    unavailable = tuple(dict.fromkeys((*unknown, *refused, *deferred)))
    if conflicts:
        status = ReadinessStatus.BLOCKED
        question = None
        reasons = ("conflicting_contract_facts",)
    elif missing:
        status = ReadinessStatus.NEEDS_DECISION_FACT
        question = next(
            (
                item.name for item in required
                if item.name in missing and item.decision_changing
            ),
            None,
        )
        reasons = ("decision_changing_fact_missing",)
    elif unavailable:
        can_preview = all(
            definition_by_name[name].preliminary_allowed_without
            for name in unavailable
            if name in definition_by_name
        )
        status = (
            ReadinessStatus.PRELIMINARY_READY
            if can_preview
            else ReadinessStatus.BLOCKED
        )
        question = None
        reasons = (
            "unavailable_fact_not_reasked",
            "preliminary_path_allowed" if can_preview else "honest_boundary_required",
        )
    else:
        status = ReadinessStatus.EXACT_READY
        question = None
        reasons = ("all_required_contract_facts_known",)

    learn = (
        definition_by_name[question].learn_method_code
        if question and question in definition_by_name
        else None
    )
    return TaskReadinessAssessment(
        task_id=customer_task.task_id,
        goal_id=customer_task.target_goal_id,
        contract_id=product_contract.contract_id,
        product_kind=product_contract.product_kind,
        status=status,
        confirmed_hard_facts=tuple(hard),
        confirmed_soft_facts=tuple(soft),
        missing_decision_facts=tuple(missing),
        unknown_facts=tuple(unknown),
        refused_facts=tuple(refused),
        deferred_facts=tuple(deferred),
        conflicting_facts=tuple(conflicts),
        recommended_question_fact=question,
        learn_method_code=learn,
        reason_codes=reasons,
    )
