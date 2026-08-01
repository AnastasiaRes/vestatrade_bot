from __future__ import annotations

import re
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
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_in_stock(self) -> bool:
        if self.stock_qty is not None:
            return self.stock_qty > 0
        return (
            "налич" in self.stock_status.lower()
            and "нет" not in self.stock_status.lower()
        )


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
    image_url: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    products: list[ChatProductSummary] = Field(default_factory=list)
    need_handoff: bool = False
    handoff_status: str = "none"
    handoff_ticket_id: str | None = None
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


class PendingQuestionState(BaseModel):
    """Machine-readable dialogue question with legacy text compatibility.

    ``pending_question`` used to be the only source of truth.  That made a
    short answer such as ``25 metres`` depend on the exact wording generated
    for the previous question.  The structured state keeps the expected facts
    stable even when the visible question is rephrased.
    """

    question_id: str
    text: str
    expected_slots: list[str] = Field(default_factory=list)
    attempts: int = 0
    category: str | None = None
    intent_type: str | None = None


class SessionState(BaseModel):
    session_id: str
    last_intent: str | None = None
    category: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    # Long-lived, structured engineering memory. ``slots`` describe the active
    # product selection and are intentionally reset on an explicit topic
    # change; this map keeps the already discussed project/category facts so a
    # later "вернёмся к трубам" does not require starting from zero.
    project_context: dict[str, Any] = Field(default_factory=dict)
    last_products: list[ProductCard] = Field(default_factory=list)
    # All cards already emitted for the active catalogue result set. Unlike
    # ``last_products`` this survives pagination so "покажи ещё" cannot repeat
    # page one after page two.
    shown_product_skus: list[str] = Field(default_factory=list)
    shown_result_signature: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    topic_changed: bool = False
    pending_question: str | None = None
    pending_intent_type: str | None = None
    pending_category: str | None = None
    pending_slot_keys: list[str] = Field(default_factory=list)
    # New source of truth for engineering questions.  The legacy fields above
    # stay available because many specialised catalogue flows still read them.
    pending_question_state: PendingQuestionState | None = None
    pending_complectation_parts: list[str] = Field(default_factory=list)
    question_repeats: int = 0
    handoff_status: str = "none"
    pending_handoff: dict[str, Any] | None = None
    handoff_ticket_id: str | None = None
    handoff_fingerprint: str | None = None
    handoff_opt_out: bool = False

    @property
    def pending_question_id(self) -> str | None:
        """Compatibility shortcut for machine-readable dialogue checks."""

        return (
            self.pending_question_state.question_id
            if self.pending_question_state
            else None
        )

    @staticmethod
    def _default_question_id(
        category: str | None,
        expected_slots: list[str],
        slots: dict[str, Any],
    ) -> str:
        prefix = category or "dialog"
        source = str(slots.get("water_source") or "").lower()
        scope = str(
            slots.get("project_scope") or slots.get("scope_funnel") or ""
        ).lower()
        if prefix == "pumps" and ("колод" in source or source == "well"):
            prefix = "well"
        elif prefix == "pumps" and ("скваж" in source or source == "borehole"):
            prefix = "borehole"
        elif scope == "warm_floor":
            prefix = "warm_floor"

        aliases = {
            "horizontal_run_m": "horizontal_distance",
            "warm_floor_area_m2": "area",
            "area_m2": "area",
            "required_flow_m3_h": "flow",
            "required_flow_l_min": "flow",
            "lift_height_m": "lift_height",
        }
        suffix = (
            aliases.get(expected_slots[0], expected_slots[0])
            if expected_slots
            else "clarification"
        )
        return f"{prefix}.{suffix}"

    def set_pending_question_state(
        self,
        *,
        text: str,
        expected_slots: list[str] | None = None,
        question_id: str | None = None,
        category: str | None = None,
        intent_type: str | None = None,
        attempts: int | None = None,
    ) -> PendingQuestionState:
        """Set structured and legacy pending-question fields atomically."""

        expected = list(expected_slots or [])
        pending_category = category or self.pending_category or self.category
        resolved_id = question_id or self._default_question_id(
            pending_category,
            expected,
            self.slots,
        )
        previous = self.pending_question_state
        same_question = bool(previous and previous.question_id == resolved_id)
        resolved_attempts = (
            attempts
            if attempts is not None
            else previous.attempts if same_question and previous else 0
        )
        state = PendingQuestionState(
            question_id=resolved_id,
            text=text,
            expected_slots=expected,
            attempts=max(0, int(resolved_attempts)),
            category=pending_category,
            intent_type=intent_type or self.pending_intent_type,
        )
        self.pending_question_state = state
        self.pending_question = text
        self.pending_slot_keys = expected
        self.pending_category = pending_category
        self.pending_intent_type = state.intent_type
        self.question_repeats = state.attempts
        return state

    def clear_pending_question_state(self, *, clear_legacy: bool = True) -> None:
        """Clear a question and, by default, its legacy representation."""

        self.pending_question_state = None
        if clear_legacy:
            self.pending_question = None
            self.pending_intent_type = None
            self.pending_category = None
            self.pending_slot_keys = []
            self.question_repeats = 0

    def sync_pending_question_state(self) -> PendingQuestionState | None:
        """Import direct writes to the legacy pending fields.

        The orchestrator has numerous mature product-specific branches.  This
        bridge lets them keep assigning the legacy fields while every saved
        session receives a reliable structured representation.
        """

        if not self.pending_question:
            self.clear_pending_question_state()
            return None
        expected = list(self.pending_slot_keys or [])
        default_id = self._default_question_id(
            self.pending_category or self.category,
            expected,
            self.slots,
        )
        if not expected:
            signature = re.sub(
                r"[^a-zа-я0-9]+",
                "_",
                self.pending_question.lower().replace("ё", "е"),
            ).strip("_")
            default_id = f"{default_id}.{signature[:64] or 'unknown'}"
        previous = self.pending_question_state
        question_id = (
            previous.question_id
            if previous
            and previous.text == self.pending_question
            and previous.expected_slots == expected
            else default_id
        )
        return self.set_pending_question_state(
            text=self.pending_question,
            expected_slots=expected,
            question_id=question_id,
            category=self.pending_category or self.category,
            intent_type=self.pending_intent_type,
            attempts=self.question_repeats,
        )

    def sync_pending_into_project_context(self) -> None:
        """Persist the current question on the active project goal, if any."""

        context = dict(self.project_context or {})
        active_goal = context.get("active_goal")
        goals = dict(context.get("goals") or {})
        if not active_goal or active_goal not in goals:
            return
        goal = dict(goals[active_goal])
        goal["pending"] = (
            model_to_dict(self.pending_question_state)
            if self.pending_question_state
            else None
        )
        goals[active_goal] = goal
        context["goals"] = goals
        self.project_context = context


class HandoffSummary(BaseModel):
    wanted: str
    known_slots: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    products_considered: list[str] = Field(default_factory=list)
    contact: str | None = None
