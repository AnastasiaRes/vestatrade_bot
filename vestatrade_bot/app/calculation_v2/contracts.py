"""Immutable contracts for grounded catalogue price calculations in V2.

This seam deliberately calculates only a quantity of an already identified
catalogue offer.  It is not an engineering estimate, quote, basket, discount
or delivery calculator.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CALCULATION_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CalculationResultStatus(str, Enum):
    CALCULATED = "calculated"
    NEED_CLARIFICATION = "need_clarification"
    NOT_CALCULABLE = "not_calculable"
    REJECTED = "rejected"


class CalculationScopeOrigin(str, Enum):
    V2_DELIVERED = "v2_delivered"
    EXPLICIT_SKU = "explicit_sku"
    NONE = "none"


class CalculationReferenceKind(str, Enum):
    EXACT_SKU = "exact_sku"
    PARTIAL_SKU = "partial_sku"
    ORDINAL = "ordinal"
    CURRENT_FOCUS = "current_focus"
    SINGLE_PRESENTED = "single_presented"
    UNRESOLVED = "unresolved"


class CalculationUnit(str, Enum):
    PIECE = "pcs"
    METRE = "m"


class CalculationSourceKind(str, Enum):
    CATALOG_PRICE = "catalog_price"
    CATALOG_STOCK = "catalog_stock"
    CATALOG_ATTRIBUTE = "catalog_attribute"
    CATALOG_IDENTITY = "catalog_identity"


class StockAssessment(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNIT_UNCONFIRMED = "unit_unconfirmed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CalculationProductReference(FrozenModel):
    kind: CalculationReferenceKind
    raw: str = ""
    canonical_sku: str | None = None
    candidate_skus: tuple[str, ...] = ()
    reason_code: str


class CalculationSourceReference(FrozenModel):
    source_ref_id: str
    sku: str
    field_name: str
    source_kind: CalculationSourceKind
    source_revision: str
    raw_value: str | None = None


class CalculationRequest(FrozenModel):
    schema_version: Literal["1.0"] = CALCULATION_SCHEMA_VERSION
    task_id: str | None = None
    goal_id: str | None = None
    original_utterance: str = Field(min_length=1, max_length=8_000)
    selection_id: str | None = None
    ordered_skus: tuple[str, ...] = ()
    source_revision: str | None = None
    scope_origin: CalculationScopeOrigin
    product_ref: CalculationProductReference
    quantity: Decimal | None = Field(default=None, gt=0)
    quantity_unit: CalculationUnit | None = None
    quantity_evidence: str | None = None

    @model_validator(mode="after")
    def quantity_is_complete_or_absent(self) -> "CalculationRequest":
        if (self.quantity is None) != (self.quantity_unit is None):
            raise ValueError("calculation quantity and unit must be supplied together")
        if self.quantity is not None and not self.quantity_evidence:
            raise ValueError("calculation quantity requires source evidence")
        return self


class CalculationResult(FrozenModel):
    schema_version: Literal["1.0"] = CALCULATION_SCHEMA_VERSION
    status: CalculationResultStatus
    task_id: str | None = None
    goal_id: str | None = None
    selection_id: str | None = None
    source_revision: str | None = None
    scope_origin: CalculationScopeOrigin
    product_ref: CalculationProductReference
    sku: str | None = None
    product_name: str | None = None
    quantity: Decimal | None = None
    quantity_unit: CalculationUnit | None = None
    unit_price: Decimal | None = None
    price_basis_unit: CalculationUnit | None = None
    currency: str | None = None
    total: Decimal | None = None
    stock_qty: int | None = None
    stock_assessment: StockAssessment = StockAssessment.NOT_APPLICABLE
    stock_delta: Decimal | None = None
    sources: tuple[CalculationSourceReference, ...] = ()
    clarification: str | None = None
    outcome_gate_passed: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def result_is_coherent(self) -> "CalculationResult":
        if self.status == CalculationResultStatus.CALCULATED:
            if any(
                item is None
                for item in (
                    self.sku,
                    self.product_name,
                    self.quantity,
                    self.quantity_unit,
                    self.unit_price,
                    self.price_basis_unit,
                    self.currency,
                    self.total,
                )
            ):
                raise ValueError("calculated result requires complete price evidence")
            if not self.sources:
                raise ValueError("calculated result requires sources")
        if self.status == CalculationResultStatus.NEED_CLARIFICATION and not self.clarification:
            raise ValueError("clarification result requires one clarification")
        if self.outcome_gate_passed and self.status == CalculationResultStatus.REJECTED:
            raise ValueError("rejected calculation cannot pass outcome gate")
        return self
