"""Compatibility adapter from the versioned semantic delta to the V2 reducer."""

from __future__ import annotations

import re
from typing import Iterable

from app.agents.domain_ontology import (
    ACTION_ALIAS_ONTOLOGY,
    semantic_ontology_version,
)
from app.agents.semantic_interpreter import (
    ConstraintFact,
    GoalOperation,
    ProductMention,
    ProductRole,
    ReferenceKind,
    SelectionPreference,
    TurnReference,
    TurnUnderstanding,
)
from app.catalog_v2.normalization import normalize_fact_value, normalize_unit_label
from app.catalog_v2.registry import DEFAULT_CONTRACTS

from .contracts import (
    ResolvedEntityRef,
    SemanticActionCandidate,
    SemanticEntityMention,
    SemanticFactUpdate,
    SemanticGateResult,
    SemanticProductReference,
    SemanticSelectionPreference,
    SemanticTurnDeltaV1,
)


_ACT_TO_CAPABILITY = {
    "find": "show",
    "select": "select",
    "compare": "compare",
    "calculate": "calculate",
    "compatibility": "compatibility",
    "explain": "fact",
}


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _grounded_span(message: str, aliases: Iterable[str]) -> str | None:
    normalized = _normalise(message)
    for alias in sorted(aliases, key=len, reverse=True):
        candidate = _normalise(alias)
        if candidate and candidate in normalized:
            match = re.search(re.escape(alias), message, flags=re.IGNORECASE)
            return match.group(0) if match is not None else candidate
    return None


def _ordered_multi_goal_evidence(
    frame: TurnUnderstanding,
    message: str,
) -> str | None:
    """Recognize an explicit order only after LLM has typed two targets.

    This is deliberately not a second product classifier.  The LLM remains
    responsible for extracting the product targets; this narrow anchor merely
    preserves the customer's explicit ``сначала … потом`` ordering as the
    future-facing PROJECT action, while the existing reducer executes the
    first typed Selection task.
    """

    target_count = sum(
        1 for product in frame.products if product.role == ProductRole.TARGET
    )
    if target_count < 2:
        return None
    match = re.search(
        r"\bсначала\b[\s\S]{0,240}\b(?:потом|затем)\b",
        message,
        flags=re.IGNORECASE,
    )
    return match.group(0) if match is not None else None


def _registry_numeric_unit(
    predicate: str,
    unit: str | None,
) -> tuple[float, str] | None:
    """Find one fact conversion in the existing catalog contract registry."""

    normalized_unit = normalize_unit_label(unit)
    if normalized_unit is None:
        return None
    candidates = [
        definition
        for contract in DEFAULT_CONTRACTS
        for definition in contract.fact_definitions
        if definition.name == predicate and normalized_unit in definition.unit_conversions
    ]
    if not candidates:
        return None
    conversions = {
        (
            float(definition.unit_conversions[normalized_unit]),
            next(
                (
                    name
                    for name, multiplier in definition.unit_conversions.items()
                    if multiplier == 1.0
                ),
                None,
            ),
        )
        for definition in candidates
    }
    if len(conversions) != 1:
        return None
    multiplier, canonical_unit = next(iter(conversions))
    return (multiplier, canonical_unit) if canonical_unit is not None else None


def _actions(frame: TurnUnderstanding, message: str) -> tuple[SemanticActionCandidate, ...]:
    actions: list[SemanticActionCandidate] = []
    seen: set[str] = set()
    explicit: list[tuple[str, str]] = []
    for definition in ACTION_ALIAS_ONTOLOGY:
        evidence = _grounded_span(message, definition.get("aliases") or ())
        if evidence is not None:
            explicit.append((str(definition["action"]), evidence))
    explicit_names = {item[0] for item in explicit}
    unsupported_explicit = explicit_names.intersection(
        {"rationale", "compatibility", "project"}
    )
    for act in frame.acts:
        downstream = act.value
        action = _ACT_TO_CAPABILITY.get(downstream, downstream)
        if unsupported_explicit and downstream in {"find", "select"}:
            continue
        if "project" in unsupported_explicit and downstream == "calculate":
            # A hydraulic/system-design request is not quantity × catalogue
            # price arithmetic.  Keep the explicit future-facing PROJECT
            # action in SemanticTurnDelta and let the bounded V2 capability
            # boundary own its presentation.
            continue
        if (
            "project" in unsupported_explicit
            and not unsupported_explicit.intersection({"rationale", "compatibility"})
            and downstream == "explain"
        ):
            continue
        evidence = next(
            (
                item.evidence
                for item in frame.information_requests
                if item.act.value == downstream
            ),
            message[:240] or downstream,
        )
        actions.append(
            SemanticActionCandidate(
                action=action,
                downstream_action=downstream,
                confidence=frame.confidence,
                evidence=evidence,
            )
        )
        seen.add(action)

    for action, evidence in explicit:
        if action == "calculate" and "project" in explicit_names:
            continue
        if action in seen:
            continue
        downstream = {
            "show": "find",
            "fact": "explain",
            "compare": "compare",
            "calculate": "calculate",
            # Rationale and project remain future-facing semantic actions;
            # Compatibility now has its own bounded two-sided evidence path.
            "rationale": "explain",
            "compatibility": "compatibility",
        }.get(action)
        actions.append(
            SemanticActionCandidate(
                action=action,
                downstream_action=downstream,
                confidence=1.0,
                evidence=evidence,
            )
        )
        seen.add(action)
    ordered_multi_goal = _ordered_multi_goal_evidence(frame, message)
    if ordered_multi_goal is not None and "project" not in seen:
        actions.append(
            SemanticActionCandidate(
                action="project",
                downstream_action=None,
                confidence=1.0,
                evidence=ordered_multi_goal,
            )
        )
    return tuple(actions)


def _canonical_fact_value(
    predicate: str,
    value: object,
    unit: str | None,
) -> object:
    if value is None:
        return None
    canonical_unit = normalize_unit_label(unit)
    text = _normalise(value)
    if predicate == "connection_size":
        if "полдюйм" in text or re.fullmatch(r"(?:g\s*)?1\s*/\s*2", text):
            return "1/2"
        if re.fullmatch(r"dn\s*15", text):
            return "1/2"
    if predicate == "connection_pattern" and (
        "обе резьбы внутренние" in text
        or "внутренняя резьба с обеих сторон" in text
        or text in {"female female", "female_female"}
    ):
        return "female_female"
    if predicate == "pipe_service" and any(
        marker in text
        for marker in ("батаре", "радиаторн", "отоплен")
    ):
        return "heating"

    numeric_predicates = {
        "area_m2",
        "diameter_mm",
        "length_mm",
        "mounting_length_mm",
        "installation_length_mm",
        "operating_temperature_c",
        "duty_point_head_m",
        "max_head_m",
        "duty_point_flow_l_h",
        "max_flow_l_h",
    }
    numeric_value: int | float | None = None
    if predicate in numeric_predicates and isinstance(value, str):
        literal = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
        if literal is not None:
            parsed = float(literal.group(0).replace(",", "."))
            numeric_value = int(parsed) if parsed.is_integer() else parsed
        elif "полтора" in text:
            numeric_value = 1.5
        elif "четыр" in text:
            numeric_value = 4
        elif "девяност" in text:
            numeric_value = 90
        elif "двадцать пят" in text or "двадцат" in text:
            numeric_value = 25
    candidate_value = numeric_value if numeric_value is not None else value
    registry_conversion = _registry_numeric_unit(predicate, canonical_unit)
    if (
        registry_conversion is not None
        and isinstance(candidate_value, (int, float))
        and not isinstance(candidate_value, bool)
    ):
        multiplier, _canonical_unit = registry_conversion
        converted = float(candidate_value) * multiplier
        return int(converted) if converted.is_integer() else converted
    return normalize_fact_value(predicate, candidate_value)


def _canonical_fact_unit(predicate: str, unit: str | None) -> str | None:
    canonical = normalize_unit_label(unit)
    registry_conversion = _registry_numeric_unit(predicate, canonical)
    if registry_conversion is not None:
        return registry_conversion[1]
    if predicate == "area_m2" and canonical in {"m2", "м2", "m²", "м²"}:
        return "m2"
    return canonical


def build_semantic_turn_delta(
    frame: TurnUnderstanding,
    *,
    message: str,
    turn_id: str,
    session_id: str | None = None,
    semantic_repairs: tuple[str, ...] = (),
) -> tuple[SemanticTurnDeltaV1, SemanticGateResult]:
    """Build and validate the typed seam without executing any capability."""

    entities = tuple(
        SemanticEntityMention(
            mention_id=f"{turn_id}:entity:{index}",
            mention_index=index,
            source_span=item.evidence,
            role=item.role.value,
            category=item.category.value,
            product_kind=item.canonical_type,
            resolved=(
                ResolvedEntityRef(kind="product_kind", value=item.canonical_type)
                if item.canonical_type
                else None
            ),
            ambiguity_reason=(None if item.canonical_type else "unresolved_product_kind"),
            evidence=item.evidence,
        )
        for index, item in enumerate(frame.products)
    )
    operation = (
        "correct"
        if frame.operation == GoalOperation.CORRECT
        else "retract"
        if frame.operation == GoalOperation.CANCEL
        else "add"
    )
    deterministic_codes = {
        "ppr_product_goal_recovered",
        "pipe_service_recovered_from_radiator_main",
        "glass_fiber_reinforcement_recovered",
        "connection_pattern_recovered_from_explicit_pair",
        "spoken_numeric_anchor_recovered",
        "pump_product_goal_recovered",
        "irrigation_pump_goal_recovered",
        "irrigation_source_pump_goal_recovered",
        "irrigation_borehole_water_level_recovered",
        "valve_product_goal_recovered",
        "external_sewer_goal_recovered",
        "sewer_length_anchor_recovered",
        "radiator_valve_connection_size_recovered",
        "radiator_valve_shape_recovered",
        "radiator_valve_kit_target_recovered",
        "boiler_circuits_unknown_recovered",
        "spoken_boiler_power_anchor_recovered",
        "pending_spoken_metric_answer_recovered",
    }
    provenance = (
        "deterministic_anchor"
        if deterministic_codes.intersection(semantic_repairs)
        else "audit"
    )
    facts = tuple(
        SemanticFactUpdate(
            subject_mention_index=item.applies_to_product,
            predicate=item.name,
            operation=operation,
            raw_value=item.value,
            canonical_value=_canonical_fact_value(item.name, item.value, item.unit),
            raw_unit=item.unit,
            canonical_unit=_canonical_fact_unit(item.name, item.unit),
            status=item.status.value,
            polarity=item.polarity.value,
            evidence=item.evidence,
            provenance=provenance,
            source_turn=turn_id,
        )
        for item in frame.constraints
    )
    reference_kind_map = {
        ReferenceKind.ORDINAL: "ordinal",
        ReferenceKind.DEICTIC: "deictic",
        ReferenceKind.PREVIOUS_PRODUCT: "previous_product",
        ReferenceKind.PREVIOUS_CATEGORY: "previous_category",
        ReferenceKind.PENDING_QUESTION: "pending_question",
        ReferenceKind.OTHER: "other",
    }
    references = tuple(
        SemanticProductReference(
            kind=reference_kind_map[item.kind],
            text=item.text,
            target_hint=item.target_hint,
            evidence=item.evidence,
        )
        for item in frame.references
    )
    preferences = tuple(
        SemanticSelectionPreference(
            kind=item.kind.value,
            value=item.value,
            evidence=item.evidence,
        )
        for item in frame.selection_preferences
    )

    normalized_message = _normalise(message)
    reason_codes: list[str] = []
    for evidence in [
        *(item.evidence for item in entities),
        *(item.evidence for item in facts),
        *(item.evidence for item in references),
        *(item.evidence for item in preferences),
    ]:
        if _normalise(evidence) not in normalized_message:
            reason_codes.append("evidence_not_in_current_turn")
            break
    if any(
        item.subject_mention_index is not None
        and item.subject_mention_index >= len(entities)
        for item in facts
    ):
        reason_codes.append("fact_entity_binding_invalid")
    if any(item.canonical_value is None and item.status == "known" for item in facts):
        reason_codes.append("known_fact_without_canonical_value")

    accepted = not reason_codes
    status = (
        "rejected"
        if not accepted
        else "partial"
        if frame.ambiguities
        else "accepted"
    )
    delta = SemanticTurnDeltaV1(
        turn_id=turn_id,
        session_id=session_id,
        registry_version=semantic_ontology_version(),
        status=status,
        action_candidates=_actions(frame, message),
        entity_mentions=entities,
        fact_updates=facts,
        product_references=references,
        selection_preferences=preferences,
        ambiguities=tuple(item.model_dump(mode="json") for item in frame.ambiguities),
        semantic_repairs=semantic_repairs,
        rejection_reason_codes=tuple(dict.fromkeys(reason_codes)),
    )
    gate = SemanticGateResult(
        accepted=accepted,
        status=status,
        reason_codes=delta.rejection_reason_codes,
        anchor_count=len(entities) + len(facts) + len(references) + len(preferences),
        accounted_anchor_count=(
            len(entities) + len(facts) + len(references) + len(preferences)
            if accepted
            else 0
        ),
    )
    return delta, gate


def adapt_delta_to_turn_understanding(
    delta: SemanticTurnDeltaV1,
    original: TurnUnderstanding,
) -> TurnUnderstanding | None:
    """Adapt the accepted delta to the existing reducer contract.

    Non-semantic workflow controls stay on the original compatibility frame;
    products, facts, references and supported actions come from the delta.
    Unsupported future actions remain in the delta but are deliberately not
    forged into a current CustomerAct.
    """

    if delta.status == "rejected":
        return None
    acts = [
        item.downstream_action
        for item in delta.action_candidates
        if item.downstream_action is not None
    ]
    products = [
        ProductMention(
            text=item.source_span,
            canonical_type=item.product_kind,
            category=item.category,
            role=ProductRole(item.role),
            evidence=item.evidence,
        )
        for item in sorted(delta.entity_mentions, key=lambda candidate: candidate.mention_index)
    ]
    constraints = [
        ConstraintFact(
            name=item.predicate,
            value=item.canonical_value,
            unit=item.canonical_unit,
            status=item.status,
            polarity=item.polarity,
            applies_to_product=item.subject_mention_index,
            evidence=item.evidence,
        )
        for item in delta.fact_updates
    ]
    reverse_reference_kinds = {
        "ordinal": ReferenceKind.ORDINAL,
        "deictic": ReferenceKind.DEICTIC,
        "previous_product": ReferenceKind.PREVIOUS_PRODUCT,
        "previous_category": ReferenceKind.PREVIOUS_CATEGORY,
        "pending_question": ReferenceKind.PENDING_QUESTION,
        "other": ReferenceKind.OTHER,
    }
    references = [
        TurnReference(
            kind=reverse_reference_kinds.get(item.kind, ReferenceKind.OTHER),
            text=item.text,
            target_hint=item.target_hint,
            evidence=item.evidence,
        )
        for item in delta.product_references
    ]
    preferences = [
        SelectionPreference(
            kind=item.kind,
            value=item.value,
            evidence=item.evidence,
        )
        for item in delta.selection_preferences
    ]
    payload = original.model_dump(mode="json")
    payload.update(
        {
            "acts": list(dict.fromkeys(acts)),
            "products": [item.model_dump(mode="json") for item in products],
            "constraints": [item.model_dump(mode="json") for item in constraints],
            "references": [item.model_dump(mode="json") for item in references],
            "selection_preferences": [
                item.model_dump(mode="json") for item in preferences
            ],
        }
    )
    return TurnUnderstanding.model_validate(payload)
