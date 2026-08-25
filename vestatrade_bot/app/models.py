from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.dialogue_v2.contracts import DialogueStateV2


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class ProductDocument(BaseModel):
    """Structured, source-preserving evidence extracted from a product file.

    ``filename`` intentionally contains only the source basename.  This keeps
    cached product JSON portable between machines and avoids leaking a local
    absolute path into prompts or API responses.  ``text`` is the same bounded
    extraction that feeds the legacy ``Product.docs_text`` field.
    """

    filename: str
    document_kind: str = "technical_document"
    text: str
    page_count: int | None = None
    section_pages: dict[str, int] = Field(default_factory=dict)


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
    # Keep documents source-separated so answers can state which passport or
    # instruction supports a fact.  The default makes old cached Product JSON
    # (which only contains ``docs_text``) fully backwards compatible.
    documents: list[ProductDocument] = Field(default_factory=list)
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
    session_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    message: str = Field(min_length=1, max_length=8_000)
    client_turn_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


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


class IdempotentResponseRecord(BaseModel):
    """Bounded transport retry record; dialogue V2 never stores response prose."""

    client_turn_id: str
    response_payload: dict[str, Any]
    response_digest: str
    session_revision: int = Field(ge=0)


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
    # Canonical facts expected from the next user turn.  New selection
    # contracts populate this directly so dialogue state does not depend on
    # reverse-engineering a particular Russian rendering of the question.
    expected_slots: list[str] = Field(default_factory=list)
    blocking: bool = False


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
    # Monotonic order of catalogue displays inside one product branch.  The
    # order of ``product_skus`` is the order the customer actually saw; neither
    # later refinements nor an LLM interpretation may rewrite it.
    display_index: int | None = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ProductBranchState(BaseModel):
    """Chronological, category-scoped product referents for one dialogue."""

    selections: list[ProductSelectionSnapshot] = Field(default_factory=list)
    # ``selections`` is deliberately bounded below to keep a session compact.
    # Preserve the immutable first display separately so conversational
    # references such as "the very first one you showed" keep their identity
    # even after many later pages/refinements.
    first_display: ProductSelectionSnapshot | None = None
    next_display_index: int = 0


class LastSearchOutcome(BaseModel):
    """Grounded result of the latest catalogue search.

    An empty result is still a dialogue fact.  Without a typed record, a
    confirmation such as ``so the exact 45-degree fitting is absent?`` is
    routed as a brand-new request and may even switch product families.  The
    history length ties the fact to the immediately preceding assistant turn,
    which prevents an old negative result from leaking into a later topic.
    """

    status: Literal["no_exact_match"] = "no_exact_match"
    category: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    original_text: str = ""
    sku: str | None = None
    brand: str | None = None
    history_length: int = 0
    answer_text: str = ""
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ProductFocusState(BaseModel):
    """One unambiguous catalogue identity that follow-up turns may reference.

    This is deliberately independent from ``last_products``.  The latter is a
    presentation view and may legitimately be empty (for example when an exact
    out-of-stock SKU is hidden by an ``in stock only`` filter) or replaced by a
    page of analogues.  Losing the identity in either case makes a subsequent
    ``what is its price?`` or ``compare with the original`` impossible to
    ground safely.
    """

    sku: str
    category: str | None = None
    origin: str = "shown"
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ProductRelationContext(BaseModel):
    """Grounded source/target identities for an analogue result set."""

    relation: str = "analog"
    source_sku: str
    alternative_skus: list[str] = Field(default_factory=list)
    category: str | None = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SessionState(BaseModel):
    session_id: str
    session_revision: int = Field(default=0, ge=0)
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
    # Empty catalogue results must survive one immediate confirmation turn in
    # the same way that shown cards survive an attribute follow-up.
    last_search_outcome: LastSearchOutcome | None = None
    # Stable identity focus is not the same thing as the cards in the latest
    # response.  Keep it when an exact product is hidden by a commercial
    # filter, and keep the analogue relation when ``last_products`` becomes the
    # alternatives page.
    product_focus: ProductFocusState | None = None
    product_relation: ProductRelationContext | None = None
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
    # Selection intent survives clarification turns.  A compatibility
    # recommendation must keep asking for safety/fit inputs, while an explicit
    # browse opt-out may intentionally clear it and show catalogue examples.
    pending_selection_mode: str | None = None
    pending_complectation_parts: list[str] = Field(default_factory=list)
    question_repeats: int = 0
    # Подписи недавно заданных уточняющих вопросов. Держатся отдельно от
    # ``pending_question_state``: тот удаляется, когда ожидаемые слоты вопроса
    # определить не удалось, и именно такие вопросы зацикливались.
    recent_clarifications: list[str] = Field(default_factory=list)
    # Отпечатки последних ответов бота. Прежние защиты от повтора смотрели на
    # висящий вопрос, поэтому круг из двух-трёх чередующихся шаблонов кругом не
    # считался, а ответы длиннее 300 символов не проверялись вовсе. Кольцо
    # держится на уровне сессии и не зависит ни от длины, ни от ветки.
    recent_answer_hashes: list[str] = Field(default_factory=list)
    handoff_status: str = "none"
    pending_handoff: dict[str, Any] | None = None
    handoff_ticket_id: str | None = None
    handoff_fingerprint: str | None = None
    handoff_opt_out: bool = False
    # Контакт, названный покупателем в любой момент разговора. Держится
    # отдельно от ``pending_handoff``: заявка собирается тогда, когда о ней
    # попросили, а телефон или почту часто называют раньше — в ответ на вопрос
    # про заказ. Раньше контакт искали только в текущей реплике, и бот просил
    # его повторно у покупателя, который уже всё написал.
    contact: str | None = None
    contact_turn: int | None = None
    # Подтверждён ли перенос этого контакта в заявку. Подхваченный из прошлого
    # хода контакт показывается маской и требует согласия — так сохраняется
    # смысл прежнего ограничения: чужой адрес из другой темы не уедет молча.
    contact_confirmed: bool = False
    # Immutable Stage 2 shadow state.  It is serialized with the session for
    # both in-memory and Redis stores, but legacy routing and slots never read
    # it.  Old sessions omit the field and therefore load as ``None``.
    dialogue_state_v2: DialogueStateV2 | None = None
    # Stage 6 uses a separate epoch for answers that were actually selected.
    # Old shadow strategies must never imply that a customer saw a question.
    live_dialogue_state_v2: DialogueStateV2 | None = None
    v2_live_epoch_id: str | None = None
    v2_sticky_assignment_id: str | None = None
    v2_migration_cell_id: str | None = None
    v2_last_products: list[ProductCard] = Field(default_factory=list)
    idempotent_responses: list[IdempotentResponseRecord] = Field(default_factory=list)

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
            if "материал" in normalized and "прокладк" in normalized:
                # The question offers the laying method whenever the material
                # is undecided, so either answer closes it.
                return ["pipe_material", "installation_method"]
            if "водяной" in normalized and "электрическ" in normalized:
                return ["warm_floor_type"]
            if "утепл" in normalized or "пирог пола" in normalized:
                return ["floor_insulation_ready"]
            if "источник тепла" in normalized or "каким котл" in normalized:
                return ["warm_floor_heat_source"]
            if "автоматик" in normalized or "покомнатн" in normalized:
                return ["warm_floor_automation_needed"]

        if category == "pumps":
            if (
                "диаметр" in normalized
                and any(marker in normalized for marker in ["шланг", "напорн", "труб"])
            ):
                return ["discharge_diameter_mm"]
            if "размер частиц" in normalized:
                return ["solids_mm"]
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
