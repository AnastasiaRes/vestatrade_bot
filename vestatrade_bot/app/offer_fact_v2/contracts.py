"""Typed result contracts for a fact directly available on a shown offer."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


OFFER_FACT_SCHEMA_VERSION = "1.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class OfferFactKind(str, Enum):
    PRICE = "price"
    STOCK = "stock"
    LINK = "link"


class OfferFactStatus(str, Enum):
    ANSWERED = "answered"
    NEED_CLARIFICATION = "need_clarification"
    REJECTED = "rejected"


class OfferFactScopeOrigin(str, Enum):
    V2_DELIVERED = "v2_delivered"
    EXPLICIT_SKU = "explicit_sku"
    NONE = "none"


class OfferFactReferenceKind(str, Enum):
    EXACT_SKU = "exact_sku"
    PARTIAL_SKU = "partial_sku"
    ORDINAL = "ordinal"
    CURRENT_FOCUS = "current_focus"
    SINGLE_PRESENTED = "single_presented"
    UNRESOLVED = "unresolved"


class OfferFactProductReference(FrozenModel):
    kind: OfferFactReferenceKind
    raw: str = ""
    canonical_sku: str | None = None
    candidate_skus: tuple[str, ...] = ()
    reason_code: str


class OfferFactSourceReference(FrozenModel):
    source_ref_id: str
    sku: str
    field_name: str
    source_revision: str
    raw_value: str | None = None


class OfferFactRequest(FrozenModel):
    schema_version: Literal["1.0"] = OFFER_FACT_SCHEMA_VERSION
    task_id: str | None = None
    goal_id: str | None = None
    original_utterance: str = Field(min_length=1, max_length=8_000)
    fact_kind: OfferFactKind
    selection_id: str | None = None
    ordered_skus: tuple[str, ...] = ()
    source_revision: str | None = None
    scope_origin: OfferFactScopeOrigin
    product_ref: OfferFactProductReference


class OfferFactResult(FrozenModel):
    schema_version: Literal["1.0"] = OFFER_FACT_SCHEMA_VERSION
    status: OfferFactStatus
    task_id: str | None = None
    goal_id: str | None = None
    fact_kind: OfferFactKind
    selection_id: str | None = None
    source_revision: str | None = None
    scope_origin: OfferFactScopeOrigin
    product_ref: OfferFactProductReference
    sku: str | None = None
    product_name: str | None = None
    value: str | float | int | None = None
    currency: str | None = None
    source: OfferFactSourceReference | None = None
    clarification: str | None = None
    outcome_gate_passed: bool = False
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def coherent_result(self) -> "OfferFactResult":
        if self.status == OfferFactStatus.ANSWERED:
            if any(item is None for item in (self.sku, self.product_name, self.value, self.source)):
                raise ValueError("answered offer fact requires source-backed value")
        if self.status == OfferFactStatus.NEED_CLARIFICATION and not self.clarification:
            raise ValueError("offer clarification requires text")
        if self.outcome_gate_passed and self.status == OfferFactStatus.REJECTED:
            raise ValueError("rejected offer fact cannot pass outcome gate")
        return self
