"""Deterministic capability ownership above the existing cutover policy.

The resolver does not render an answer, mutate dialogue state or run either
bot.  It only records whether the current turn belongs to a delivery-ready V2
contract, to one narrowly allowlisted Legacy capability, or remains
unresolved.  The ordinary cutover remains the sole response-owner arbiter.
"""

from __future__ import annotations

import re

from app.agents.commerce_topics import (
    is_commerce_topic_continuation,
    match_commerce_topic,
)
from app.agents.engineering_norms import match_engineering_norm
from app.agents.item_list import split_item_list
from app.agents.problem_framing import frame_customer_problem
from app.dialogue_v2.contracts import NextActionKind

from .contracts import (
    CapabilityCoverageDecision,
    CapabilityCoverageStatus,
    CapabilityMaturity,
    CapabilityOwner,
    CapabilityRule,
    CapabilityTurnContext,
    MigrationRegistry,
    V2TurnCandidate,
)
from .engineering_boundary import hydraulic_system_calculation_evidence


_VERIFIED_COMMERCE_ACTIONS = frozenset(
    {
        NextActionKind.ANSWER_VERIFIED_COMMERCE_QUESTION,
    }
)

_SMALL_TALK_ONLY_RE = re.compile(
    r"(?iu)^\s*(?:привет(?:ик)?|здравствуйте|доброе\s+утро|"
    r"добрый\s+(?:день|вечер)|спасибо|благодарю|кто\s+ты|"
    r"как\s+тебя\s+зовут)[\s!?.]*$"
)

_ENGINEERING_METRE_VALUE_RE = re.compile(
    r"(?iu)(?<!\d)\d+(?:[.,]\d+)?\s*(?:м|метр\w*)\b"
)
_PUMP_GOAL_TYPES = frozenset(
    {"pump", "circulation_pump", "borehole_pump", "surface_pump"}
)


def _rule(
    registry: MigrationRegistry,
    capability_id: str,
    owner: CapabilityOwner,
) -> CapabilityRule | None:
    return next(
        (
            item
            for item in registry.capabilities
            if item.capability_id == capability_id
            and item.owner == owner
            and item.maturity == CapabilityMaturity.READY
        ),
        None,
    )


def _candidate_is_delivery_ready(candidate: V2TurnCandidate | None) -> bool:
    return bool(
        candidate is not None
        and (
            candidate.semantic_accepted
            or candidate.capability_boundary_result is not None
        )
        and candidate.contracts_resolved
        and candidate.eligible_for_delivery
        and candidate.validation_status == "accepted"
        and candidate.response is not None
        and candidate.response_digest
        and candidate.answer_plan_id
        and candidate.rendered_answer_id
        and candidate.source_revision
        and candidate.catalog_revision
        and not candidate.external_side_effect_started
    )


def _decision(
    registry: MigrationRegistry,
    status: CapabilityCoverageStatus,
    *,
    owner: CapabilityOwner | None = None,
    rule: CapabilityRule | None = None,
    candidate: V2TurnCandidate | None = None,
    evidence_codes: tuple[str, ...] = (),
    reason_codes: tuple[str, ...] = (),
    enforced: bool = False,
) -> CapabilityCoverageDecision:
    return CapabilityCoverageDecision(
        registry_version=registry.capability_registry_version,
        status=status,
        owner=owner,
        capability_ids=((rule.capability_id,) if rule is not None else ()),
        task_acts=(candidate.task_acts if candidate is not None else ()),
        product_kinds=(candidate.product_kinds if candidate is not None else ()),
        next_action=(candidate.next_action if candidate is not None else None),
        evidence_codes=evidence_codes,
        reason_codes=tuple(
            dict.fromkeys(
                (
                    *(rule.reason_codes if rule is not None else ()),
                    *reason_codes,
                )
            )
        ),
        enforced=enforced,
    )


def resolve_capability_coverage(
    message: str,
    candidate: V2TurnCandidate | None,
    registry: MigrationRegistry,
    *,
    turn_context: CapabilityTurnContext | None = None,
) -> CapabilityCoverageDecision:
    """Resolve one turn against the versioned built-in capability registry.

    Priority is deliberate:

    * unsafe system calculations stay on the V2 boundary;
    * Legacy list/problem/norm capabilities pre-empt a generic catalogue
      candidate because those candidates are a known false-positive shape;
    * a source-gated V2 commerce result beats the broad Legacy topic matcher;
    * every other fully gated V2 candidate remains V2-owned;
    * unknown turns are observed but not mislabeled as safe Legacy coverage.
    """

    hydraulic_evidence = hydraulic_system_calculation_evidence(message)
    if hydraulic_evidence is not None:
        v2_boundary = _rule(
            registry,
            "v2.engineering_boundary",
            CapabilityOwner.V2,
        )
        if (
            v2_boundary is not None
            and _candidate_is_delivery_ready(candidate)
            and candidate is not None
            and candidate.next_action == NextActionKind.STATE_CAPABILITY_BOUNDARY
        ):
            return _decision(
                registry,
                CapabilityCoverageStatus.V2_READY,
                owner=CapabilityOwner.V2,
                rule=v2_boundary,
                candidate=candidate,
                evidence_codes=("hydraulic_system_calculation",),
                reason_codes=("grounded_v2_engineering_boundary_ready",),
                enforced=True,
            )
        boundary = _rule(
            registry,
            "boundary.hydraulic_system_calculation",
            CapabilityOwner.BOUNDARY,
        )
        if boundary is not None:
            return _decision(
                registry,
                CapabilityCoverageStatus.BOUNDARY_REQUIRED,
                owner=CapabilityOwner.BOUNDARY,
                rule=boundary,
                candidate=candidate,
                evidence_codes=("hydraulic_system_calculation",),
                reason_codes=("legacy_execution_forbidden",),
                # Observation-only until the ordinary V2 pipeline has built
                # the typed boundary response.  A boundary requirement must
                # never become permission to execute the unsafe Legacy
                # hydraulic calculator, but the ownership policy also cannot
                # deliver a response that does not exist.
                enforced=False,
            )

    item_list = split_item_list(message)
    active_engineering_answer = bool(
        turn_context is not None
        and turn_context.pending_question_fact
        and turn_context.active_goal_canonical_type in _PUMP_GOAL_TYPES
        and len(_ENGINEERING_METRE_VALUE_RE.findall(message)) >= 2
        and not item_list
    )
    item_rule = _rule(registry, "legacy.item_list", CapabilityOwner.LEGACY)
    if item_rule is not None and item_list:
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=item_rule,
            candidate=candidate,
            evidence_codes=(f"item_count:{len(item_list)}",),
            reason_codes=("capability_owner_legacy",),
            enforced=True,
        )

    if active_engineering_answer and _candidate_is_delivery_ready(candidate):
        catalogue_v2 = _rule(
            registry,
            "v2.catalogue_turn",
            CapabilityOwner.V2,
        )
        if catalogue_v2 is not None:
            return _decision(
                registry,
                CapabilityCoverageStatus.V2_READY,
                owner=CapabilityOwner.V2,
                rule=catalogue_v2,
                candidate=candidate,
                evidence_codes=(
                    f"pending_fact:{turn_context.pending_question_fact}",
                    "engineering_measurement_answer",
                ),
                reason_codes=(
                    "legacy_item_list_blocked_by_active_engineering_answer",
                    "capability_owner_v2",
                ),
                enforced=True,
            )

    problem = frame_customer_problem(message)
    problem_rule = _rule(
        registry,
        "legacy.problem_frame",
        CapabilityOwner.LEGACY,
    )
    if problem_rule is not None and problem is not None:
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=problem_rule,
            candidate=candidate,
            evidence_codes=(f"problem_frame:{problem.code}",),
            reason_codes=("capability_owner_legacy",),
            enforced=True,
        )

    norm = match_engineering_norm(message)
    norm_rule = _rule(
        registry,
        "legacy.engineering_norm",
        CapabilityOwner.LEGACY,
    )
    if norm_rule is not None and norm is not None:
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=norm_rule,
            candidate=candidate,
            evidence_codes=(f"engineering_norm:{norm.key}",),
            reason_codes=("capability_owner_legacy",),
            enforced=True,
        )

    if (
        _candidate_is_delivery_ready(candidate)
        and candidate is not None
        and candidate.next_action in _VERIFIED_COMMERCE_ACTIONS
    ):
        commerce_v2 = _rule(
            registry,
            "v2.verified_commerce",
            CapabilityOwner.V2,
        )
        if commerce_v2 is not None:
            return _decision(
                registry,
                CapabilityCoverageStatus.V2_READY,
                owner=CapabilityOwner.V2,
                rule=commerce_v2,
                candidate=candidate,
                evidence_codes=("verified_commerce_candidate",),
                reason_codes=("capability_owner_v2",),
                enforced=True,
            )

    if (
        _candidate_is_delivery_ready(candidate)
        and candidate is not None
        and candidate.next_action == NextActionKind.STATE_CAPABILITY_BOUNDARY
        and candidate.capability_boundary_result is not None
    ):
        uncovered_boundary = _rule(
            registry,
            "v2.uncovered_boundary",
            CapabilityOwner.V2,
        )
        if uncovered_boundary is not None:
            return _decision(
                registry,
                CapabilityCoverageStatus.V2_READY,
                owner=CapabilityOwner.V2,
                rule=uncovered_boundary,
                candidate=candidate,
                evidence_codes=("uncovered_capability_boundary",),
                reason_codes=("capability_owner_v2_boundary",),
                enforced=True,
            )

    commerce = match_commerce_topic(message)
    commerce_rule = _rule(
        registry,
        "legacy.commerce_topic",
        CapabilityOwner.LEGACY,
    )
    if commerce_rule is not None and commerce is not None:
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=commerce_rule,
            candidate=candidate,
            evidence_codes=(f"commerce_topic:{commerce.key}",),
            reason_codes=("capability_owner_legacy",),
            enforced=True,
        )

    if (
        commerce_rule is not None
        and turn_context is not None
        and is_commerce_topic_continuation(
            message,
            turn_context.legacy_commerce_topic,
        )
    ):
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=commerce_rule,
            candidate=candidate,
            evidence_codes=(
                f"commerce_continuation:{turn_context.legacy_commerce_topic}",
            ),
            reason_codes=("capability_owner_legacy_continuation",),
            enforced=True,
        )

    small_talk_rule = _rule(
        registry,
        "legacy.small_talk",
        CapabilityOwner.LEGACY,
    )
    if small_talk_rule is not None and _SMALL_TALK_ONLY_RE.fullmatch(message):
        return _decision(
            registry,
            CapabilityCoverageStatus.LEGACY_READY,
            owner=CapabilityOwner.LEGACY,
            rule=small_talk_rule,
            candidate=candidate,
            evidence_codes=("small_talk_only",),
            reason_codes=("capability_owner_legacy",),
            enforced=True,
        )

    catalogue_v2 = _rule(
        registry,
        "v2.catalogue_turn",
        CapabilityOwner.V2,
    )
    if catalogue_v2 is not None and _candidate_is_delivery_ready(candidate):
        return _decision(
            registry,
            CapabilityCoverageStatus.V2_READY,
            owner=CapabilityOwner.V2,
            rule=catalogue_v2,
            candidate=candidate,
            evidence_codes=("delivery_ready_v2_candidate",),
            reason_codes=("capability_owner_v2",),
            enforced=True,
        )

    return _decision(
        registry,
        CapabilityCoverageStatus.UNRESOLVED,
        candidate=candidate,
        reason_codes=("capability_coverage_unresolved_observation_only",),
        enforced=False,
    )
