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


class ProductSelectionSnapshot(BaseModel):
    """One grounded catalogue selection kept for later conversational recall.

    Product cards are the trusted facts shown to the customer.  ``constraints``
    contain only the selection hints from that turn and are used to resolve
    references such as ``the 180 mm pump`` or ``the first pipe``.  Keeping this
    separate from engineering ``slots`` prevents a valve turn from overwriting
    the pump the customer may return to later.
    """

    category: str
    # Store identities, not cards: prices, stock and attributes must be rebuilt
    # from the current feed whenever the customer returns to this selection.
    product_skus: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    user_message: str = ""
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ProductBranchState(BaseModel):
    """Chronological, category-scoped product referents for one dialogue."""

    selections: list[ProductSelectionSnapshot] = Field(default_factory=list)


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
    # Catalogue referents are branch-scoped. ``last_products`` remains the
    # active compatibility view, while this map survives topic switches and
    # lets the controller restore a named/qualified previous selection.
    product_branches: dict[str, ProductBranchState] = Field(default_factory=dict)
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

    @staticmethod
    def infer_pending_expected_slots(
        text: str,
        category: str | None = None,
        question_id: str | None = None,
    ) -> list[str]:
        """Recover machine-readable slots for old text-only questions.

        Some mature dialogue branches and already persisted sessions can have
        a visible question without ``expected_slots``.  Keeping such a
        question as generic pending state makes every short reply look like a
        continuation and can trap the dialogue in a loop.  Only unambiguous,
        well-known questions are recovered here; an unknown generic question
        is deliberately not kept as pending state.
        """

        normalized = re.sub(
            r"\s+",
            " ",
            str(text or "").lower().replace("ё", "е"),
        ).strip()
        normalized_id = str(question_id or "").lower()

        id_slots = {
            "warm_floor.area": ["warm_floor_area_m2"],
            "warm_floor.warm_floor_area_m2": ["warm_floor_area_m2"],
            "warm_floor.warm_floor_type": ["warm_floor_type"],
            "warm_floor.floor_insulation_ready": ["floor_insulation_ready"],
            "warm_floor.insulation": ["floor_insulation_ready"],
            "warm_floor.warm_floor_heat_source": ["warm_floor_heat_source"],
            "warm_floor.heat_source": ["warm_floor_heat_source"],
            "warm_floor.warm_floor_automation_needed": [
                "warm_floor_automation_needed"
            ],
            "warm_floor.automation": ["warm_floor_automation_needed"],
            "well.horizontal_distance": ["horizontal_run_m"],
            "well.lift_height": ["lift_height_m"],
            "well.flow": ["required_flow_m3_h"],
            "well.water_level_depth_m": ["water_level_depth_m"],
            "well.flow_unit_confirmation": ["flow_unit_confirmation"],
            "pumps.flow_unit_confirmation": ["flow_unit_confirmation"],
            "well.water_level_reference": ["water_level_reference"],
        }
        if normalized_id in id_slots:
            return list(id_slots[normalized_id])

        if category == "pipes" or normalized_id.startswith("warm_floor."):
            if "площад" in normalized and (
                "тепл" in normalized or "пол" in normalized
            ):
                return ["warm_floor_area_m2"]
            if "водяной" in normalized and "электрическ" in normalized:
                return ["warm_floor_type"]
            if "утепл" in normalized or "пирог пола" in normalized:
                return ["floor_insulation_ready"]
            if "источник тепла" in normalized or "каким котл" in normalized:
                return ["warm_floor_heat_source"]
            if "автоматик" in normalized or "покомнатн" in normalized:
                return ["warm_floor_automation_needed"]

        if category == "pumps":
            if "сверху" in normalized and "снизу" in normalized:
                return ["water_level_reference"]
            if (
                ("источник" in normalized and "вод" in normalized)
                or ("откуда" in normalized and "вод" in normalized)
                or (
                    "скваж" in normalized
                    and "колод" in normalized
                    and "водопровод" in normalized
                )
            ):
                return ["water_source"]
            if (
                "глубин" in normalized
                and "от верха" in normalized
                and "вод" in normalized
            ):
                return ["water_level_depth_m"]
            if any(
                marker in normalized
                for marker in [
                    "перепад высот",
                    "участок ровн",
                    "точка выше",
                    "на какую высоту",
                ]
            ):
                return ["lift_height_m"]
            if "давлен" in normalized and any(
                marker in normalized
                for marker in ["на вход", "сейчас", "исходн", "имеетс"]
            ):
                return ["inlet_pressure_bar"]
            if "давлен" in normalized and any(
                marker in normalized
                for marker in ["после насос", "получить", "нужно", "требуем"]
            ):
                return ["required_pressure_bar"]
            if (
                "литры в минуту" in normalized
                and "общ" in normalized
                and "объ" in normalized
            ):
                return ["flow_unit_confirmation"]
            if "расход" in normalized or "литр" in normalized:
                return ["required_flow_m3_h"]
            if "для какой задач" in normalized:
                return ["pump_use", "pump_type"]

        if category == "boilers":
            if (
                "два отдельных" in normalized
                and "встроенн" in normalized
                and "бойлер" in normalized
            ):
                return ["boiler_water_heater_relation"]
            if "площад" in normalized:
                return ["area_m2"]
            if "газов" in normalized and "электр" in normalized:
                return ["boiler_type"]
            if "только для отопления" in normalized and "горяч" in normalized:
                return ["contours", "needs_hot_water"]
            if "220" in normalized and "380" in normalized:
                return ["voltage_v"]

        if category == "water_heaters":
            if "объем" in normalized or "литр" in normalized:
                return ["volume_l"]
            if "накоп" in normalized or "проточ" in normalized:
                return ["heater_type"]

        if category == "controls":
            if (
                "термостат" in normalized
                and "сервопривод" in normalized
                and "контроллер" in normalized
            ):
                return ["control_kind"]
            if "24" in normalized or "230" in normalized or "питани" in normalized:
                return ["voltage_v"]

        if category == "valves":
            if "для чего" in normalized or (
                "вода" in normalized
                and "отоплен" in normalized
                and "радиатор" in normalized
            ):
                return ["application"]
            if "температур" in normalized or "давлен" in normalized:
                return ["operating_temperature_c", "operating_pressure_bar"]
            if "размер" in normalized:
                return ["size_inch", "diameter_mm", "connection_size"]

        if category == "radiators":
            expected: list[str] = []
            if "тип радиатор" in normalized:
                expected.append("radiator_type")
            if any(
                marker in normalized
                for marker in ["размер", "высот", "межосев", "длин", "секц"]
            ):
                expected.extend(
                    [
                        "radiator_size_mm",
                        "radiator_height_mm",
                        "length_mm",
                        "sections",
                    ]
                )
            if expected:
                return expected

        return []

    @staticmethod
    def _slot_has_answer(slots: dict[str, Any], key: str) -> bool:
        """Return whether a requested slot has a real user/domain answer."""

        if key == "flow_unit_confirmation":
            return str(slots.get("flow_unit_status") or "") in {
                "confirmed",
                "total_volume",
            }
        if key == "water_level_reference":
            return str(slots.get(key) or "") in {"from_top", "from_bottom"}
        if key not in slots:
            return False
        value = slots[key]
        # ``False`` and numeric zero are valid answers.  Only genuinely empty
        # values mean that the question is still unresolved.
        return value is not None and value not in ("", [], {})

    def pending_expected_slot_is_filled(self, expected_slots: list[str]) -> bool:
        """Whether any alternative expected by the pending question is set."""

        return any(self._slot_has_answer(self.slots, key) for key in expected_slots)

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
        if not expected:
            expected = self.infer_pending_expected_slots(
                text,
                pending_category,
                question_id,
            )
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
        previous = self.pending_question_state
        expected = list(self.pending_slot_keys or [])
        if (
            not expected
            and previous
            and previous.text == self.pending_question
            and previous.expected_slots
        ):
            expected = list(previous.expected_slots)
        if not expected:
            expected = self.infer_pending_expected_slots(
                self.pending_question,
                self.pending_category or self.category,
                previous.question_id if previous else None,
            )

        # A pending question has completed its lifecycle as soon as its
        # expected answer reaches the active branch.  Do this before assigning
        # an id so stale question text cannot overwrite a saved fact later.
        if expected and self.pending_expected_slot_is_filled(expected):
            self.clear_pending_question_state()
            return None

        # Unknown text-only questions cannot safely interpret a short answer.
        # Do not persist them as generic pending state and therefore do not let
        # them create an endless continuation loop.
        if not expected:
            self.clear_pending_question_state()
            return None

        default_id = self._default_question_id(
            self.pending_category or self.category,
            expected,
            self.slots,
        )
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
