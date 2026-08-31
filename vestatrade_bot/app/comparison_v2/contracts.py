"""Immutable contracts for the grounded V2 comparison seam.

The comparison path is deliberately separate from selection and product facts:
it may only read cards that have already been delivered to the customer.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


COMPARISON_SCHEMA_VERSION = "1.0"
Scalar = str | int | float | bool


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ComparisonResultStatus(str, Enum):
    COMPARED = "compared"
    NEED_CLARIFICATION = "need_clarification"
    NOT_COMPARABLE = "not_comparable"
    SOURCE_CONFLICT = "source_conflict"
    REJECTED = "rejected"


class ComparisonSourceKind(str, Enum):
    CATALOG_PRICE = "catalog_price"
    CATALOG_STOCK = "catalog_stock"
    CATALOG_ATTRIBUTE = "catalog_attribute"
    CATALOG_IDENTITY = "catalog_identity"
    PASSPORT_DOCUMENT_EXACT = "passport_document_exact"
    PASSPORT_AND_CATALOG_CARD = "passport_and_catalog_card"


class ComparisonCriterion(str, Enum):
    LOWEST_PRICE = "lowest_price"
    AVAILABILITY = "availability"


class ComparisonReferenceKind(str, Enum):
    """How one comparison subject was grounded inside a visible V2 scope."""

    ORDINAL = "ordinal"
    EXPLICIT_VISIBLE_SKU = "explicit_visible_sku"
    NAMED_VISIBLE_PRODUCT = "named_visible_product"
    CURRENT_FOCUS = "current_focus"
    UNRESOLVED = "unresolved"


class ComparisonProductReference(FrozenModel):
    """A reference candidate; it never authorizes a catalogue search."""

    kind: ComparisonReferenceKind
    raw: str = ""
    canonical_sku: str | None = None
    evidence: str = ""
    reason_code: str

    @model_validator(mode="after")
    def resolved_reference_has_a_sku(self) -> "ComparisonProductReference":
        if self.kind != ComparisonReferenceKind.UNRESOLVED and not self.canonical_sku:
            raise ValueError("resolved comparison reference requires a SKU")
        if self.kind == ComparisonReferenceKind.UNRESOLVED and self.canonical_sku:
            raise ValueError("unresolved comparison reference cannot carry a SKU")
        return self


class ComparisonSourceReference(FrozenModel):
    """One auditable source of a comparison value."""

    source_ref_id: str
    sku: str
    predicate: str
    source_kind: ComparisonSourceKind
    source_revision: str
    field_name: str | None = None
    source_field: str | None = None
    raw_value: str | None = None
    document: str | None = None
    section: str | None = None
    quote: str | None = None
    verifier_status: str | None = None
    document_scope: tuple[str, ...] = ()


class ComparisonValue(FrozenModel):
    sku: str
    predicate: str
    value: Scalar
    unit: str | None = None
    source_ref_ids: tuple[str, ...] = Field(min_length=1)


class ComparisonDimension(FrozenModel):
    predicate: str
    label: str
    values: tuple[ComparisonValue, ...] = ()
    missing_skus: tuple[str, ...] = ()
    missing_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def dimension_has_value_or_subject_missing_reason(self) -> "ComparisonDimension":
        if not self.values and not self.missing_skus:
            raise ValueError("comparison dimension requires values or missing scope")
        if self.missing_skus and not self.missing_reason_codes:
            raise ValueError("missing comparison values require reason codes")
        return self


class ComparisonRecommendation(FrozenModel):
    sku: str
    criterion: ComparisonCriterion
    source_ref_ids: tuple[str, ...] = Field(min_length=1)
    reason_code: str


class ComparisonRequest(FrozenModel):
    schema_version: Literal["1.0"] = COMPARISON_SCHEMA_VERSION
    task_id: str | None = None
    goal_id: str | None = None
    original_utterance: str = Field(min_length=1, max_length=8_000)
    selection_id: str | None = None
    ordered_skus: tuple[str, ...] = ()
    product_references: tuple[ComparisonProductReference, ...] = ()
    reference_reason_codes: tuple[str, ...] = ()
    requested_predicates: tuple[str, ...] = ()
    criterion: ComparisonCriterion | None = None
    # ``Сравните их`` asks for facts; ``что лучше?`` asks for a decision.
    # Keep the distinction explicit so the renderer cannot turn every factual
    # comparison into an unnecessary sales question.
    needs_deciding_criterion: bool = False
    source_revision: str | None = None
    scope_origin: Literal["v2_delivered", "legacy_unversioned", "none"]


class ComparisonResult(FrozenModel):
    schema_version: Literal["1.0"] = COMPARISON_SCHEMA_VERSION
    status: ComparisonResultStatus
    task_id: str | None = None
    goal_id: str | None = None
    selection_id: str | None = None
    compared_skus: tuple[str, ...] = ()
    requested_predicates: tuple[str, ...] = ()
    dimensions: tuple[ComparisonDimension, ...] = ()
    sources: tuple[ComparisonSourceReference, ...] = ()
    missing_data: tuple[str, ...] = ()
    recommendation: ComparisonRecommendation | None = None
    deciding_question: str | None = None
    source_revision: str | None = None
    outcome_gate_passed: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def result_status_is_coherent(self) -> "ComparisonResult":
        if self.status == ComparisonResultStatus.COMPARED:
            if len(self.compared_skus) < 2 or not self.dimensions:
                raise ValueError("compared result requires two SKUs and a dimension")
        if self.status == ComparisonResultStatus.SOURCE_CONFLICT and not self.reason_codes:
            raise ValueError("source conflict comparison requires a reason code")
        if self.recommendation is not None and self.status != ComparisonResultStatus.COMPARED:
            raise ValueError("recommendation requires a completed comparison")
        if self.outcome_gate_passed and self.status == ComparisonResultStatus.REJECTED:
            raise ValueError("rejected comparison cannot pass outcome gate")
        return self
