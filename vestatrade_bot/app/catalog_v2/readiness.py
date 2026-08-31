"""Pure contract-based task readiness assessment."""

from __future__ import annotations

import re

from app.dialogue_v2.contracts import (
    ConstraintFactV2,
    ConstraintStatus,
    ConstraintStrength,
    CustomerTask,
    DialogueStateV2,
    SelectionControlKind,
    TaskAct,
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
from .normalization import (
    format_numeric_choice_value,
    format_numeric_range_value,
    normalize_fact_value,
    normalize_unit_label,
    parse_numeric_choice_value,
    parse_numeric_range_value,
)


def _normalized_fact_token(value: object) -> str:
    return " ".join(
        str(value or "").casefold().replace("ё", "е").replace("-", "_").split()
    )


def canonical_fact_name(
    contract: ProductContract,
    name: str,
    unit: str | None = None,
) -> str | None:
    """Resolve a contract-scoped alias without crossing unit semantics.

    A semantic model may use ``pressure`` for metres of pump head.  That is a
    valid alias only when the model also supplies a length/head unit.  Pressure
    in bar or kPa is deliberately left unresolved because converting it would
    require a physical calculation that does not belong in the reducer or
    catalogue planner.
    """

    normalized = _normalized_fact_token(name)
    for definition in contract.fact_definitions:
        accepted = (definition.name, *definition.aliases)
        if normalized in {
            _normalized_fact_token(item)
            for item in accepted
        }:
            pressure_aliases = {
                "pressure",
                "required_pressure",
                "pressure_head",
                "system_head",
            }
            if definition.name == "max_head_m" and normalized in pressure_aliases:
                normalized_unit = _normalized_fact_token(unit).replace(" ", "")
                if normalized_unit not in {
                    "m",
                    "м",
                    "meter",
                    "meters",
                    "метр",
                    "метра",
                    "метров",
                    "cm",
                    "см",
                }:
                    return None
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
        if task.target_goal_id:
            if fact.goal_id not in {None, task.target_goal_id}:
                continue
            # A fact explicitly scoped to the same product goal remains true
            # when a compatible task for that goal is resumed or re-addressed.
            # Goal-less facts retain the narrower task boundary.
            if fact.goal_id is None and fact.task_id not in {None, task.task_id}:
                continue
        elif fact.task_id not in {None, task.task_id}:
            continue
        canonical = canonical_fact_name(contract, fact.name, fact.unit)
        if canonical is not None:
            selected[canonical] = fact
    return selected


_EXPLICIT_UNIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("l/min", r"(?:l/min|л/мин)(?![a-zа-я])"),
    ("m3/h", r"(?:m3/h|м3/ч)(?![a-zа-я])"),
    ("l/h", r"(?:l/h|л/ч)(?![a-zа-я])"),
    ("mm", r"(?:mm|мм)(?![a-zа-я])"),
    ("cm", r"(?:cm|см)(?![a-zа-я])"),
    ("kw", r"(?:kw|квт)(?![a-zа-я])"),
    ("w", r"(?<![a-zа-я])(?:w|вт)(?![a-zа-я])"),
    ("um", r"(?:um|мкм)(?![a-zа-я])"),
    ("%", r"%"),
    ("bar", r"(?:bar|бар)(?![a-zа-я])"),
    ("mpa", r"(?:mpa|мпа)(?![a-zа-я])"),
    ("kpa", r"(?:kpa|кпа)(?![a-zа-я])"),
    ("pa", r"(?<![a-zа-я])(?:pa|па)(?![a-zа-я])"),
    ("atm", r"(?:atm|атм)(?![a-zа-я])"),
    ("c", r"(?:°\s*c|°\s*с|℃)(?![a-zа-я])"),
    ("m", r"(?<![a-zа-я])(?:m|м)(?![a-zа-я0-9/])"),
)


def _explicit_unit_from_text(value: object) -> str:
    text = str(value or "").casefold().replace("³", "3")
    return next(
        (
            canonical
            for canonical, pattern in _EXPLICIT_UNIT_PATTERNS
            if re.search(pattern, text, re.I)
        ),
        "",
    )


def _canonical_value(definition, fact: ConstraintFactV2):
    numeric_range = (
        parse_numeric_range_value(fact.value)
        if definition.value_type == FactValueType.NUMBER
        else None
    )
    numeric_choice = (
        parse_numeric_choice_value(fact.value)
        if definition.value_type == FactValueType.NUMBER
        else None
    )
    value = (
        format_numeric_range_value(*numeric_range)
        if numeric_range is not None
        else format_numeric_choice_value(numeric_choice)
        if numeric_choice is not None
        else normalize_fact_value(definition.name, fact.value)
    )
    if (
        definition.value_type == FactValueType.NUMBER
        and isinstance(value, str)
        and numeric_range is None
        and numeric_choice is None
    ):
        matches = re.findall(r"[-+]?\d+(?:[.,]\d+)?", value)
        if len(matches) == 1:
            parsed = float(matches[0].replace(",", "."))
            value = int(parsed) if parsed.is_integer() else parsed
    raw_unit = (
        fact.unit
        or _explicit_unit_from_text(fact.value)
        or _explicit_unit_from_text(fact.evidence)
    )
    unit = normalize_unit_label(str(raw_unit)) or ""
    if definition.unit_conversions and numeric_range is not None and unit:
        factor = definition.unit_conversions.get(unit)
        if factor is not None:
            converted = tuple(float(item) * factor for item in numeric_range)
            normalized = tuple(
                int(item) if item.is_integer() else item for item in converted
            )
            return format_numeric_range_value(*normalized)
    if definition.unit_conversions and numeric_choice is not None and unit:
        factor = definition.unit_conversions.get(unit)
        if factor is not None:
            converted = tuple(float(item) * factor for item in numeric_choice)
            normalized = tuple(
                int(item) if item.is_integer() else item for item in converted
            )
            return format_numeric_choice_value(normalized)
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
        "area_m2": "m2",
        "power_w": "W",
        "flow": "l/h",
        "percent": "%",
        "micron": "um",
        "temperature_c": "C",
        "pressure_bar": "bar",
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
    catalog_unverifiable: list[str] = []
    definition_by_name = {
        item.name: item for item in product_contract.fact_definitions
    }

    for name, fact in selected.items():
        definition = definition_by_name[name]
        fact_unit = normalize_unit_label(
            fact.unit
            or _explicit_unit_from_text(fact.value)
            or _explicit_unit_from_text(fact.evidence)
        )
        if (
            definition.value_type == FactValueType.NUMBER
            and definition.unit_conversions
            and fact_unit is not None
            and fact_unit not in definition.unit_conversions
        ):
            conflicts.append(f"{name}:unsupported_unit:{fact_unit}")
        explicit_preference = fact.polarity.value == "preferred"
        immutable_analog_fact = name in product_contract.analog_invariants
        effective_strength = (
            FactStrength.SOFT
            if explicit_preference and not immutable_analog_fact
            else FactStrength.HARD
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
        if fact.status == ConstraintStatus.KNOWN and not definition.catalog_verifiable:
            catalog_unverifiable.append(name)
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
    goal = next(
        (
            item
            for item in dialogue_state.product_goals
            if item.goal_id == customer_task.target_goal_id
        ),
        None,
    )
    irrigation_goal = (
        product_contract.contract_id == "pump.generic.v1"
        and _normalized_fact_token(getattr(goal, "canonical_type", ""))
        == "irrigation_pump"
    )
    # A generic pump request normally needs its application.  The anchored
    # irrigation goal has already established that application; searching or
    # asking for a circulation-pump dimension before the water source would
    # be unsafe.  Keep this as a narrow adapter over the same generic contract
    # rather than adding a second state machine or catalogue route.
    if irrigation_goal:
        required = [definition_by_name["water_source"]]
    known_names = {
        item.name for item in (*hard, *soft) if item.status == "known"
    }
    terminal_names = {
        item.name for item in (*hard, *soft)
    }
    required_alternatives = dict(product_contract.required_fact_alternatives)
    if "sku" in known_names:
        required = []
    for definition in required:
        alternatives = required_alternatives.get(definition.name, ())
        if (
            definition.name not in terminal_names
            and not any(name in terminal_names for name in alternatives)
        ):
            missing.append(definition.name)

    # A source-backed proxy can safely narrow cards without proving final
    # suitability.  It satisfies the required-alternative contract so that
    # the dialogue can progress, but must not promote the result to
    # ``EXACT_READY``.  This is intentionally declarative in the registry;
    # no product-family arithmetic belongs in readiness.
    preliminary_proxy_alternatives = tuple(
        alternative
        for definition in required
        if definition.name not in known_names
        for alternative in required_alternatives.get(definition.name, ())
        if alternative in known_names
        and definition_by_name.get(alternative) is not None
        and definition_by_name[alternative].preliminary_only_for_exact
    )

    unavailable = tuple(dict.fromkeys((*unknown, *refused, *deferred)))

    # A preliminary result is not a permission to show every item of the
    # resolved product kind.  Contracts declare one or more small ``any-of``
    # identity groups that must have a known member before cards can be shown.
    # This keeps a user who cannot answer a later confirmation question moving
    # forward, while still failing closed for a request such as just «нужна
    # труба» or an unknown system type.
    missing_preliminary_identity: list[str] = []
    unavailable_preliminary_groups: list[tuple[str, ...]] = []
    # A resolved SKU is a stronger identity anchor than any product-family
    # requirement.  Do not make an explicitly named item disappear merely
    # because an old task carried incomplete selection slots.
    anchor_groups = (
        ()
        if "sku" in known_names
        else (("water_source",) if irrigation_goal else product_contract.preliminary_identity_fact_groups)
    )
    for raw_group in anchor_groups:
        group = tuple(
            name for name in raw_group if name in definition_by_name
        )
        if not group or any(name in known_names for name in group):
            continue
        still_askable = tuple(name for name in group if name not in terminal_names)
        if still_askable:
            missing_preliminary_identity.append(still_askable[0])
        else:
            unavailable_preliminary_groups.append(group)

    continue_with_confirmed_facts = any(
        item.task_id == customer_task.task_id
        and item.kind == SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS
        for item in dialogue_state.selection_controls
    )
    # An explicit terminal answer ("не знаю", "уточню позже", "не хочу
    # сообщать") is a real decision by the customer.  Once identity anchors
    # are known, it may end the questionnaire and authorise a safe preliminary
    # result without requiring a second phrase such as "покажите по остальному".
    terminal_fact_allows_preliminary = bool(unavailable)
    # A circulation-pump contract may opt into showing a labelled preliminary
    # shortlist as soon as its own safety groups (here: a real duty point) are
    # known.  The registry keeps this deliberately family-specific; a bare
    # pump or another product kind continues through the normal questionnaire.
    auto_preliminary_requested = (
        product_contract.auto_preliminary_when_safety_facts_known
        and customer_task.act in {TaskAct.FIND, TaskAct.SELECT}
        # This is an opening-turn convenience, not an implicit permission to
        # skip a later installation question after the dialogue has already
        # entered the ordinary exact-selection funnel.
        and customer_task.origin_turn == dialogue_state.turn_number
        # Do not let the auto path turn its own missing safety data into the
        # next question.  Without both flow and head the ordinary exact path
        # retains control and asks exactly the established missing fact.
        and all(
            any(name in known_names for name in group)
            for group in product_contract.preliminary_required_fact_groups
        )
    )
    preliminary_requested = (
        continue_with_confirmed_facts
        or terminal_fact_allows_preliminary
        or auto_preliminary_requested
    )

    # Unlike identity anchors above, these safety groups must never block an
    # ordinary exact questionnaire.  They apply only when the buyer explicitly
    # asks to see a preliminary shortlist (or declines a normal fact).  This
    # keeps established exact pump selection backwards-compatible while
    # ensuring that a preliminary pump list has both flow and head.
    missing_preliminary_required: list[str] = []
    unavailable_preliminary_required_groups: list[tuple[str, ...]] = []
    if preliminary_requested and "sku" not in known_names:
        for raw_group in product_contract.preliminary_required_fact_groups:
            group = tuple(name for name in raw_group if name in definition_by_name)
            if not group or any(name in known_names for name in group):
                continue
            still_askable = tuple(name for name in group if name not in terminal_names)
            if still_askable:
                missing_preliminary_required.append(still_askable[0])
            else:
                unavailable_preliminary_required_groups.append(group)

    unresolved_for_preliminary = tuple(dict.fromkeys((*missing, *unavailable)))
    can_show_preliminary = (
        not missing_preliminary_identity
        and not unavailable_preliminary_groups
        and not missing_preliminary_required
        and not unavailable_preliminary_required_groups
        and all(
            definition_by_name[name].preliminary_allowed_without
            for name in unresolved_for_preliminary
            if name in definition_by_name
        )
    )
    if conflicts:
        status = ReadinessStatus.BLOCKED
        question = None
        reasons = ("conflicting_contract_facts",)
    elif preliminary_requested and missing_preliminary_required:
        status = ReadinessStatus.NEEDS_DECISION_FACT
        question = missing_preliminary_required[0]
        reasons = ("preliminary_safety_fact_missing",)
    elif preliminary_requested and unavailable_preliminary_required_groups:
        status = ReadinessStatus.BLOCKED
        question = None
        reasons = ("preliminary_safety_fact_unavailable",)
    elif missing_preliminary_identity:
        status = ReadinessStatus.NEEDS_DECISION_FACT
        question = missing_preliminary_identity[0]
        reasons = ("preliminary_identity_fact_missing",)
    elif unavailable_preliminary_groups:
        status = ReadinessStatus.BLOCKED
        question = None
        reasons = ("preliminary_identity_fact_unavailable",)
    elif missing:
        if can_show_preliminary and preliminary_requested:
            status = ReadinessStatus.PRELIMINARY_READY
            question = None
            reasons = (
                (
                    "terminal_fact_triggers_safe_preliminary_path"
                    if terminal_fact_allows_preliminary
                    else "safety_facts_trigger_safe_preliminary_path"
                    if auto_preliminary_requested
                    else "customer_requested_confirmed_facts_only"
                ),
                "preliminary_path_allowed",
            )
        else:
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
        status = (
            ReadinessStatus.PRELIMINARY_READY
            if can_show_preliminary
            else ReadinessStatus.BLOCKED
        )
        question = None
        reasons = (
            "unavailable_fact_not_reasked",
            (
                "preliminary_path_allowed"
                if can_show_preliminary
                else "honest_boundary_required"
            ),
        )
    elif preliminary_proxy_alternatives:
        status = ReadinessStatus.PRELIMINARY_READY
        question = None
        reasons = (
            "required_fact_satisfied_by_preliminary_source_backed_proxy",
            "preliminary_path_allowed",
        )
    elif catalog_unverifiable:
        status = ReadinessStatus.PRELIMINARY_READY
        question = None
        reasons = (
            "confirmed_fact_not_verifiable_from_catalogue",
            "preliminary_path_allowed",
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
        catalog_unverifiable_facts=tuple(catalog_unverifiable),
        missing_preliminary_identity_facts=tuple(missing_preliminary_identity),
        unavailable_preliminary_identity_groups=tuple(
            unavailable_preliminary_groups
        ),
        missing_preliminary_required_facts=tuple(missing_preliminary_required),
        unavailable_preliminary_required_groups=tuple(
            unavailable_preliminary_required_groups
        ),
        recommended_question_fact=question,
        learn_method_code=learn,
        reason_codes=reasons,
    )
