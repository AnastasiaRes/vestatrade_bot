"""Immutable contracts for the grounded V2 compatibility seam.

Compatibility is deliberately a relation between two resolved, source-bound
catalogue identities.  It is not a similarity search, engineering calculation
or brand-level recommendation.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


COMPATIBILITY_SCHEMA_VERSION = "1.0"
Scalar = str | int | float | bool


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CompatibilityResultStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCE_CONFLICT = "source_conflict"
    REJECTED = "rejected"


class CompatibilityRelationKind(str, Enum):
    THERMOSTATIC_HEAD_TO_VALVE = "thermostatic_head_to_valve"
    THREADED_CONNECTION = "threaded_connection"
    SEWER_CONNECTION = "sewer_connection"
    PUMP_TO_BOILER = "pump_to_boiler"
    UNKNOWN = "unknown"


class CompatibilityReferenceKind(str, Enum):
    EXACT_SKU = "exact_sku"
    PARTIAL_SKU = "partial_sku"
    NAMED_PRODUCT = "named_product"
    ORDINAL = "ordinal"
    CURRENT_FOCUS = "current_focus"
    CURRENT_VISIBLE_SCOPE = "current_visible_scope"
    UNRESOLVED = "unresolved"


class CompatibilityScopeOrigin(str, Enum):
    V2_DELIVERED = "v2_delivered"
    EXPLICIT_PRODUCTS = "explicit_products"
    NONE = "none"


class InterfaceSourceKind(str, Enum):
    CATALOG_ATTRIBUTE = "catalog_attribute"
    CATALOG_IDENTITY = "catalog_identity"
    PASSPORT = "passport"


class CompatibilityProductReference(FrozenModel):
    kind: CompatibilityReferenceKind
    raw: str = ""
    canonical_sku: str | None = None
    candidate_skus: tuple[str, ...] = ()
    reason_code: str


class InterfaceFact(FrozenModel):
    """One predicate value proven for exactly one side of a relation."""

    sku: str
    predicate: str
    value: Scalar
    unit: str | None = None
    source_kind: InterfaceSourceKind
    source_revision: str
    document: str
    section: str | None = None
    excerpt: str
    verifier_status: str


class CompatibilityRequest(FrozenModel):
    schema_version: Literal["1.0"] = COMPATIBILITY_SCHEMA_VERSION
    task_id: str | None = None
    goal_id: str | None = None
    original_utterance: str = Field(min_length=1, max_length=8_000)
    left: CompatibilityProductReference
    right: CompatibilityProductReference
    relation: CompatibilityRelationKind = CompatibilityRelationKind.UNKNOWN
    selection_id: str | None = None
    ordered_skus: tuple[str, ...] = ()
    source_revision: str | None = None
    scope_origin: CompatibilityScopeOrigin


class CompatibilityResult(FrozenModel):
    schema_version: Literal["1.0"] = COMPATIBILITY_SCHEMA_VERSION
    status: CompatibilityResultStatus
    task_id: str | None = None
    goal_id: str | None = None
    relation: CompatibilityRelationKind
    left: CompatibilityProductReference
    right: CompatibilityProductReference
    selection_id: str | None = None
    source_revision: str | None = None
    interface_predicates: tuple[str, ...] = ()
    facts: tuple[InterfaceFact, ...] = ()
    missing_predicates: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    outcome_gate_passed: bool = False

    @model_validator(mode="after")
    def result_is_coherent(self) -> "CompatibilityResult":
        resolved = (self.left.canonical_sku, self.right.canonical_sku)
        if self.status in {
            CompatibilityResultStatus.COMPATIBLE,
            CompatibilityResultStatus.INCOMPATIBLE,
        }:
            if any(item is None for item in resolved):
                raise ValueError("compatibility verdict requires two resolved products")
            if not self.interface_predicates or not self.facts:
                raise ValueError("compatibility verdict requires interface evidence")
        if self.status == CompatibilityResultStatus.SOURCE_CONFLICT and not self.reason_codes:
            raise ValueError("source conflict requires a reason code")
        if self.outcome_gate_passed and self.status == CompatibilityResultStatus.REJECTED:
            raise ValueError("rejected compatibility cannot pass outcome gate")
        return self
