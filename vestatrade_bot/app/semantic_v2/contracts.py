"""Typed, source-preserving semantic delta contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenSemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SemanticActionCandidate(FrozenSemanticModel):
    action: str = Field(min_length=1, max_length=80)
    downstream_action: str | None = Field(default=None, max_length=80)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: str = Field(min_length=1, max_length=240)
    validation_status: Literal["accepted", "ambiguous", "rejected"] = "accepted"


class ResolvedEntityRef(FrozenSemanticModel):
    kind: Literal["product_kind", "exact_sku", "partial_sku", "named_product"]
    value: str = Field(min_length=1, max_length=240)


class SemanticEntityMention(FrozenSemanticModel):
    mention_id: str = Field(min_length=1, max_length=120)
    mention_index: int = Field(ge=0)
    source_span: str = Field(min_length=1, max_length=240)
    role: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    product_kind: str | None = Field(default=None, max_length=120)
    resolved: ResolvedEntityRef | None = None
    ambiguity_reason: str | None = Field(default=None, max_length=240)
    evidence: str = Field(min_length=1, max_length=240)


class SemanticFactUpdate(FrozenSemanticModel):
    subject_mention_index: int | None = Field(default=None, ge=0)
    task_id: str | None = Field(default=None, max_length=120)
    goal_id: str | None = Field(default=None, max_length=120)
    predicate: str = Field(min_length=1, max_length=120)
    operation: Literal["add", "correct", "retract"] = "add"
    raw_value: str | int | float | bool | None = None
    canonical_value: str | int | float | bool | None = None
    raw_unit: str | None = Field(default=None, max_length=40)
    canonical_unit: str | None = Field(default=None, max_length=40)
    status: str = Field(default="known", min_length=1, max_length=40)
    polarity: str = Field(default="required", min_length=1, max_length=40)
    evidence: str = Field(min_length=1, max_length=240)
    provenance: Literal["llm", "deterministic_anchor", "audit", "merged"] = "llm"
    source_turn: str = Field(min_length=1, max_length=160)
    validation_status: Literal["accepted", "ambiguous", "rejected"] = "accepted"


class SemanticProductReference(FrozenSemanticModel):
    kind: Literal[
        "exact_sku",
        "partial_sku",
        "named_product",
        "ordinal",
        "deictic",
        "current_focus",
        "previous_product",
        "previous_category",
        "pending_question",
        "other",
    ]
    text: str = Field(min_length=1, max_length=240)
    target_hint: str | None = Field(default=None, max_length=240)
    evidence: str = Field(min_length=1, max_length=240)
    validation_status: Literal["accepted", "ambiguous", "rejected"] = "accepted"


class SemanticRelation(FrozenSemanticModel):
    subject_mention_id: str = Field(min_length=1, max_length=120)
    predicate: str = Field(min_length=1, max_length=120)
    object_mention_id: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=240)


class SemanticTurnDeltaV1(FrozenSemanticModel):
    schema_version: Literal["semantic-turn-delta-v1"] = "semantic-turn-delta-v1"
    turn_id: str = Field(min_length=1, max_length=160)
    session_id: str | None = Field(default=None, max_length=160)
    registry_version: str = Field(min_length=1, max_length=128)
    status: Literal["accepted", "partial", "ambiguous", "rejected"]
    action_candidates: tuple[SemanticActionCandidate, ...] = ()
    entity_mentions: tuple[SemanticEntityMention, ...] = ()
    fact_updates: tuple[SemanticFactUpdate, ...] = ()
    product_references: tuple[SemanticProductReference, ...] = ()
    relations: tuple[SemanticRelation, ...] = ()
    unresolved_fragments: tuple[str, ...] = ()
    ambiguities: tuple[dict[str, Any], ...] = ()
    semantic_repairs: tuple[str, ...] = ()
    rejection_reason_codes: tuple[str, ...] = ()


class SemanticGateResult(FrozenSemanticModel):
    accepted: bool
    status: Literal["accepted", "partial", "ambiguous", "rejected"]
    reason_codes: tuple[str, ...] = ()
    anchor_count: int = Field(default=0, ge=0)
    accounted_anchor_count: int = Field(default=0, ge=0)
