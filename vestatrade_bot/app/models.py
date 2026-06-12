from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class Product(BaseModel):
    sku: str
    name: str
    category_path: str = ""
    brand: str | None = None
    url: str | None = None
    image_url: str | None = None
    price: float | None = None
    currency: str = "RUB"
    stock_status: str = "unknown"
    stock_qty: int | None = None
    attributes_normalized: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    docs_text: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_in_stock(self) -> bool:
        if self.stock_qty is not None:
            return self.stock_qty > 0
        return "налич" in self.stock_status.lower() and "нет" not in self.stock_status.lower()


class ProductCard(BaseModel):
    sku: str
    name: str
    brand: str | None = None
    price: float
    currency: str = "RUB"
    stock_status: str
    stock_qty: int | None = None
    url: str
    image_url: str | None = None
    characteristics: dict[str, str] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatProductSummary(BaseModel):
    sku: str
    name: str
    price: float
    currency: str
    stock_status: str
    url: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    products: list[ChatProductSummary] = Field(default_factory=list)
    need_handoff: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)


class IntentResult(BaseModel):
    intent_type: str = "unknown"
    category: str = "other"
    confidence: float = 0.0
    slots: dict[str, Any] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)
    is_topic_change: bool = False
    llm_used: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchQuery(BaseModel):
    original_text: str
    category: str = "other"
    slots: dict[str, Any] = Field(default_factory=dict)
    sku: str | None = None
    brand: str | None = None
    cheap: bool = False
    in_stock_only: bool = False
    limit: int = 30


class SlotFillingResult(BaseModel):
    slots: dict[str, Any] = Field(default_factory=dict)
    needs_clarification: bool = False
    question: str | None = None


class GuardrailsResult(BaseModel):
    ok: bool = True
    issues: list[str] = Field(default_factory=list)
    need_handoff: bool = False
    safe_message: str | None = None


class SessionState(BaseModel):
    session_id: str
    last_intent: str | None = None
    category: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    last_products: list[ProductCard] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    topic_changed: bool = False
    pending_question: str | None = None
    pending_intent_type: str | None = None
    pending_complectation_parts: list[str] = Field(default_factory=list)
    question_repeats: int = 0


class HandoffSummary(BaseModel):
    wanted: str
    known_slots: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    products_considered: list[str] = Field(default_factory=list)
