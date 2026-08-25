"""Strict, side-effect-free understanding of one customer turn.

This module is intentionally independent from routing, dialogue state updates,
catalogue search and response generation.  During the shadow rollout its output
is recorded for evaluation only and can never alter the customer-facing path.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from enum import Enum
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models import SessionState
from app.openrouter_client import OpenRouterClient
from app.pii import redact_pii_for_model

from .domain_ontology import semantic_ontology_payload


SEMANTIC_PROMPT_VERSION = "turn-understanding-v1.5"
SEMANTIC_INTERPRETER_PROMPT = """
Ты — семантический интерпретатор одного нового сообщения покупателя магазина
инженерной сантехники. Верни только JSON по переданной схеме.

Твоя задача — описать смысл НОВОЙ реплики, а не отвечать покупателю:
- выдели все действия покупателя, даже если их несколько;
- отличай явно запрошенный товар (target) от товара/системы, которые лишь
  задают контекст (context), уже установлены (existing) или нужны как аксессуар;
- различай новую цель, продолжение, уточнение, исправление, смену цели и возврат;
- сохраняй отрицания, предпочтения, неизвестные и отложенные параметры;
- бытовые названия можно канонизировать, но исходный фрагмент всегда сохрани
  в evidence;
- короткий ответ можно связать с pending_question из контекста, однако evidence
  всё равно должен быть дословным фрагментом НОВОЙ реплики;
- опечатки, разговорная речь и транслитерация не меняют эти правила.

Строгие ограничения:
- не составляй ответ покупателю и не добавляй поле reply;
- не выбирай SKU, товары или аналоги;
- не вычисляй и не конвертируй значения;
- не копируй параметры из истории в constraints;
- не додумывай отсутствующие значения;
- каждый product, constraint, reference и ambiguity должен иметь непустой
  evidence, дословно встречающийся в current_message;
- value у constraint — только явно сказанное значение. Если покупатель говорит,
  что не знает параметр, status=unknown и value=null;
- applies_to_product — индекс элемента products или null.

Как кодировать действия (не схлопывай несколько действий в одно):
- find — показать/найти варианты без просьбы решить, какой подходит;
- select — подобрать или рекомендовать подходящий вариант по условиям;
- compare — сопоставить варианты;
- explain — объяснить свойство или правило;
- calculate — посчитать результат по исходным данным;
- check_price, check_stock и get_link — отдельные действия, если покупатель
  одновременно просит цену, наличие или ссылку;
- остальные действия выбирай строго по их именам в JSON-схеме;
- просьба сначала подобрать товар, а затем выполнить коммерческую операцию —
  это два самостоятельных acts. Не теряй коммерческое действие из-за того,
  что в той же реплике есть товар или подбор;
- request_quote — оценка стоимости или коммерческое предложение, когда
  покупатель не просит платёжный документ; request_invoice — именно счёт как
  отдельный платёжный/бухгалтерский документ, не request_quote;
  reserve_product — любая просьба временно удержать или отложить товар за
  покупателем; place_order — оформление
  заказа; modify_order — изменение существующего заказа; cancel_order — его
  отмена; order_status — проверка состояния заказа; check_delivery — условия
  или стоимость доставки; return_product — возврат; warranty — гарантийное
  обращение; complaint — претензия; handoff — явная просьба передать обращение
  сотруднику.

Как кодировать управление commerce workflow:
- workflow_controls содержит только явно сказанное confirm, decline,
  withdraw_consent, opt_out или resume_after_opt_out;
- короткое «да» является confirm только когда оно отвечает на pending-вопрос о
  согласии; не выбирай workflow и не утверждай, что операция выполнена;
- явное подтверждение, отклонение или отзыв ранее подготовленной операции — это
  workflow_control, а не новая просьба выполнить ту же предметную операцию.
  Не повторяй соответствующий commerce act только из-за упоминания его объекта
  внутри подтверждения; новый act добавляй лишь для отдельной новой просьбы;
- каждый control сохраняет дословное evidence из current_message.

Взаимоисключающие инварианты перед возвратом JSON:
- прямой запрос выставить/подготовить счёт или invoice как документ всегда
  требует act=request_invoice. Не заменяй его request_quote; оба act допустимы
  только если покупатель отдельно просит и счёт, и коммерческое предложение;
- confirm, decline, withdraw_consent, opt_out и resume_after_opt_out никогда не
  допустимы внутри acts — только внутри workflow_controls;
- handoff, request_invoice и остальные предметные действия никогда не
  допустимы внутри workflow_controls. Явная новая просьба передать обращение
  сотруднику — act=handoff;
- operation описывает только связь новой реплики с задачей: new, continue,
  refine, correct, switch, return, cancel или unknown. Предметные действия
  вроде modify_order, cancel_order и return_product никогда не допустимы в
  operation — они находятся в acts;
- если новая реплика только подтверждает или отклоняет ожидающую операцию,
  acts может быть пустым. До ещё не данного согласия отрицательный ответ —
  decline; withdraw_consent относится к отзыву уже ранее данного согласия.

Как кодировать ограничения:
- name — стабильное имя характеристики, а не вся бытовая фраза;
- известное число/строка/булево значение: status=known и value содержит его;
- требование отсутствия функции: polarity=excluded, status=known, value=true;
- качественный признак («настенный», «для горячей воды») — это известное
  строковое или булево value, а не null;
- value=null допустимо только при unknown/refused/deferred;
- preferred означает пожелание, excluded — запрет, required — обязательное
  условие. Polarity не заменяет value.

Допустимые категории:
pumps, pipes, boilers, water_heaters, hydraulic_accumulators, filters, controls,
valves, sewer, radiator_fittings, radiators, fittings, meters, sanitary_ware,
installation_systems, other.

Верни объект с schema_version="1.1", language, operation, acts, products,
constraints, references, ambiguities, workflow_controls,
answers_pending_question и confidence.
Не добавляй никаких других полей.
""".strip()

SEMANTIC_PROMPT_HASH = hashlib.sha256(
    SEMANTIC_INTERPRETER_PROMPT.encode("utf-8")
).hexdigest()

SEMANTIC_AUDIT_PROMPT = """
Ты — второй, независимый проход контроля семантического разбора сообщения
покупателя. Получишь current_message, контекст до хода, ontology, JSON-схему и
candidate от первого прохода. Верни исправленный полный TurnUnderstanding по
той же JSON-схеме, без пояснений и дополнительных полей.

Проверь смысл, а не отдельные ключевые слова:
1. Каждая самостоятельная просьба отражена отдельным acts: подбор подходящего
   товара — select, простой поиск/показ — find, цена/наличие/ссылка — отдельные
   check_price/check_stock/get_link. Отдельно проверь все commerce-просьбы:
   request_quote, request_invoice, reserve_product, place_order, modify_order,
   cancel_order, order_status, check_delivery, return_product, warranty,
   complaint и handoff. Товарный подбор и commerce-просьбу в одной реплике не
   схлопывай. Не смешивай request_invoice с request_quote: платёжный или
   бухгалтерский счёт — request_invoice, оценка/предложение без счёта —
   request_quote. Просьба временно удержать товар — reserve_product, даже если
   покупатель использует разговорный глагол.
2. Главный запрошенный товар имеет role=target. Уже установленный — existing;
   объект системы, который только задаёт условия, — context. Контекст не может
   заменить цель.
3. Все явные ограничения, предпочтения, запреты, неизвестные или отложенные
   параметры отражены в constraints с правильными status, polarity и value.
4. Исправление, смена и возврат отражены в operation; короткий ответ правильно
   связан с pending_question, если он есть.
5. Никаких вычислений, ответов, SKU и фактов из истории. Evidence каждого
   элемента — непустая дословная часть current_message.
6. Явное согласие, отказ, отзыв согласия, opt-out или возобновление ранее
   подготовленного workflow отражено в workflow_controls. Само упоминание
   предмета подтверждаемой операции не создаёт повторный commerce act. Новый
   act нужен только для самостоятельной новой просьбы.
7. Выполни финальную взаимоисключающую проверку: запрос счёта как документа —
   request_invoice, не request_quote; control-kind никогда не находится в
   acts; предметный act, включая handoff, никогда не находится в
   workflow_controls; предметный act никогда не находится в operation; чистый
   ответ на consent может иметь acts=[]. До выдачи согласия «нет» означает
   decline, а не withdraw_consent.

Если candidate уже точен и полон, верни его без смысловых изменений.
""".strip()
SEMANTIC_AUDIT_PROMPT_HASH = hashlib.sha256(
    SEMANTIC_AUDIT_PROMPT.encode("utf-8")
).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalOperation(str, Enum):
    NEW = "new"
    CONTINUE = "continue"
    REFINE = "refine"
    CORRECT = "correct"
    SWITCH = "switch"
    RETURN = "return"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


class CustomerAct(str, Enum):
    FIND = "find"
    SELECT = "select"
    COMPARE = "compare"
    EXPLAIN = "explain"
    CALCULATE = "calculate"
    CHECK_PRICE = "check_price"
    CHECK_STOCK = "check_stock"
    GET_LINK = "get_link"
    REQUEST_QUOTE = "request_quote"
    REQUEST_INVOICE = "request_invoice"
    RESERVE_PRODUCT = "reserve_product"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    ORDER_STATUS = "order_status"
    CHECK_DELIVERY = "check_delivery"
    RETURN_PRODUCT = "return_product"
    WARRANTY = "warranty"
    COMPLAINT = "complaint"
    CONTACT_STORE = "contact_store"
    HANDOFF = "handoff"
    GREETING = "greeting"
    GRATITUDE = "gratitude"
    OTHER = "other"


class ProductRole(str, Enum):
    TARGET = "target"
    CONTEXT = "context"
    EXISTING = "existing"
    ACCESSORY = "accessory"
    ALTERNATIVE = "alternative"
    UNKNOWN = "unknown"


class ProductCategory(str, Enum):
    PUMPS = "pumps"
    PIPES = "pipes"
    BOILERS = "boilers"
    WATER_HEATERS = "water_heaters"
    HYDRAULIC_ACCUMULATORS = "hydraulic_accumulators"
    FILTERS = "filters"
    CONTROLS = "controls"
    VALVES = "valves"
    SEWER = "sewer"
    RADIATOR_FITTINGS = "radiator_fittings"
    RADIATORS = "radiators"
    FITTINGS = "fittings"
    METERS = "meters"
    SANITARY_WARE = "sanitary_ware"
    INSTALLATION_SYSTEMS = "installation_systems"
    OTHER = "other"


class ConstraintStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    REFUSED = "refused"
    DEFERRED = "deferred"


class ConstraintPolarity(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    EXCLUDED = "excluded"


class ReferenceKind(str, Enum):
    PREVIOUS_PRODUCT = "previous_product"
    PREVIOUS_CATEGORY = "previous_category"
    ORDINAL = "ordinal"
    DEICTIC = "deictic"
    PENDING_QUESTION = "pending_question"
    OTHER = "other"


class WorkflowControlKind(str, Enum):
    CONFIRM = "confirm"
    DECLINE = "decline"
    WITHDRAW_CONSENT = "withdraw_consent"
    OPT_OUT = "opt_out"
    RESUME_AFTER_OPT_OUT = "resume_after_opt_out"


class ProductMention(StrictModel):
    text: str = Field(min_length=1, max_length=240)
    canonical_type: str | None = Field(default=None, max_length=120)
    category: ProductCategory = ProductCategory.OTHER
    role: ProductRole = Field(
        description=(
            "target for the primary requested product; existing only when it is "
            "already installed/owned; context when it merely constrains a target."
        )
    )
    evidence: str = Field(min_length=1, max_length=240)


class ConstraintFact(StrictModel):
    name: str = Field(
        min_length=1,
        max_length=120,
        description="Stable attribute name, not the complete source phrase.",
    )
    value: str | int | float | bool | None = Field(
        default=None,
        description=(
            "Explicit numeric, text or boolean value; null only when status is "
            "unknown, refused or deferred."
        ),
    )
    unit: str | None = Field(default=None, max_length=40)
    status: ConstraintStatus = ConstraintStatus.KNOWN
    polarity: ConstraintPolarity = ConstraintPolarity.REQUIRED
    applies_to_product: int | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def unknown_values_are_empty(self) -> "ConstraintFact":
        if self.status in {
            ConstraintStatus.UNKNOWN,
            ConstraintStatus.REFUSED,
            ConstraintStatus.DEFERRED,
        } and self.value is not None:
            raise ValueError("unknown/refused/deferred constraint must have null value")
        if self.status == ConstraintStatus.KNOWN and self.value is None:
            raise ValueError("known constraint must have a value")
        return self


class TurnReference(StrictModel):
    kind: ReferenceKind
    text: str = Field(min_length=1, max_length=240)
    target_hint: str | None = Field(default=None, max_length=160)
    evidence: str = Field(min_length=1, max_length=240)


class TurnAmbiguity(StrictModel):
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    evidence: str = Field(min_length=1, max_length=240)


class WorkflowControl(StrictModel):
    kind: WorkflowControlKind
    evidence: str = Field(min_length=1, max_length=240)


class TurnUnderstanding(StrictModel):
    """Grounded semantics of the current message; never an execution plan."""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    language: str = Field(default="ru", min_length=2, max_length=16)
    operation: GoalOperation = Field(
        default=GoalOperation.UNKNOWN,
        description=(
            "Dialogue relation only (new/continue/refine/correct/switch/return/"
            "cancel/unknown), never a domain action such as modify_order."
        ),
    )
    acts: list[CustomerAct] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Every independent customer action; do not collapse selection, "
            "price, stock, link or comparison requests into one action. "
            "request_invoice is a requested invoice/payment document and is "
            "not interchangeable with request_quote. Workflow control kinds "
            "never belong in acts."
        ),
    )
    products: list[ProductMention] = Field(default_factory=list, max_length=12)
    constraints: list[ConstraintFact] = Field(default_factory=list, max_length=40)
    references: list[TurnReference] = Field(default_factory=list, max_length=12)
    ambiguities: list[TurnAmbiguity] = Field(default_factory=list, max_length=12)
    workflow_controls: list[WorkflowControl] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Explicit control of an already prepared workflow. A control-only "
            "turn may have no acts; decline precedes consent, while "
            "withdraw_consent revokes consent already granted."
        ),
    )
    answers_pending_question: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def product_indexes_exist(self) -> "TurnUnderstanding":
        for constraint in self.constraints:
            if (
                constraint.applies_to_product is not None
                and constraint.applies_to_product >= len(self.products)
            ):
                raise ValueError("constraint points to a missing product mention")
        return self


class SemanticInterpretationResult(StrictModel):
    status: Literal["accepted", "rejected", "skipped"]
    requested: bool = False
    transport_succeeded: bool = False
    output_accepted: bool = False
    model: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    prompt_version: str = SEMANTIC_PROMPT_VERSION
    prompt_hash: str = SEMANTIC_PROMPT_HASH
    audit_prompt_hash: str = SEMANTIC_AUDIT_PROMPT_HASH
    audit_requested: bool = False
    audit_output_accepted: bool = False
    audit_rejection_reason: str | None = None
    structural_repairs: tuple[str, ...] = ()
    understanding: TurnUnderstanding | None = None
    rejection_reason: str | None = None
    fallback_reason: str | None = None


def _normalize_evidence(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def validate_current_turn_evidence(
    understanding: TurnUnderstanding,
    current_message: str,
) -> None:
    """Reject facts that cannot be traced to the current customer message."""

    normalized_message = _normalize_evidence(current_message)
    evidence_values = [
        *(item.evidence for item in understanding.products),
        *(item.evidence for item in understanding.constraints),
        *(item.evidence for item in understanding.references),
        *(item.evidence for item in understanding.ambiguities),
        *(item.evidence for item in understanding.workflow_controls),
    ]
    for evidence in evidence_values:
        normalized = _normalize_evidence(evidence)
        if not normalized or normalized not in normalized_message:
            raise ValueError(f"evidence is absent from current_message: {evidence!r}")


def repair_structural_enum_placement(
    raw: Any,
    current_message: str,
) -> tuple[Any, tuple[str, ...]]:
    """Repair only misplaced known enums; never infer a speech act from text."""

    if not isinstance(raw, dict):
        return raw, ()
    repaired = deepcopy(raw)
    acts = list(repaired.get("acts") or [])
    controls = list(repaired.get("workflow_controls") or [])
    operation = repaired.get("operation")
    act_values = {item.value for item in CustomerAct}
    control_values = {item.value for item in WorkflowControlKind}
    operation_values = {item.value for item in GoalOperation}
    evidence = str(current_message or "")[:240]
    changes: list[str] = []

    if operation in control_values:
        controls.append({"kind": operation, "evidence": evidence})
        repaired["operation"] = GoalOperation.CONTINUE.value
        changes.append("operation_control_moved_to_workflow_controls")
    elif operation in act_values and operation not in operation_values:
        acts.append(operation)
        repaired["operation"] = GoalOperation.UNKNOWN.value
        changes.append("operation_act_moved_to_acts")

    normalized_acts: list[Any] = []
    for item in acts:
        value = getattr(item, "value", item)
        if value in control_values:
            controls.append({"kind": value, "evidence": evidence})
            changes.append("act_control_moved_to_workflow_controls")
        else:
            normalized_acts.append(item)

    normalized_controls: list[Any] = []
    for item in controls:
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind in act_values:
            normalized_acts.append(kind)
            changes.append("workflow_control_act_moved_to_acts")
        else:
            normalized_controls.append(item)

    repaired["acts"] = list(dict.fromkeys(normalized_acts))
    unique_controls: list[Any] = []
    seen_controls: set[tuple[object, object]] = set()
    for item in normalized_controls:
        key = (
            item.get("kind") if isinstance(item, dict) else None,
            item.get("evidence") if isinstance(item, dict) else None,
        )
        if key in seen_controls:
            continue
        seen_controls.add(key)
        unique_controls.append(item)
    repaired["workflow_controls"] = unique_controls
    return repaired, tuple(dict.fromkeys(changes))


def semantic_context(state: SessionState) -> dict[str, Any]:
    """Return bounded, PII-free context needed to interpret a short answer."""

    pending = state.pending_question_state
    recent_dialogue = []
    for item in state.history[-4:]:
        recent_dialogue.append(
            {
                "role": str(item.get("role") or ""),
                "content": redact_pii_for_model(str(item.get("content") or ""))[:600],
            }
        )
    return {
        "active_category": state.category,
        "last_intent": state.last_intent,
        "pending_question": (
            {
                "question_id": pending.question_id,
                "text": redact_pii_for_model(pending.text)[:400],
                "expected_slots": list(pending.expected_slots),
                "category": pending.category,
            }
            if pending is not None
            else None
        ),
        "active_product_skus": [card.sku for card in state.last_products[:5]],
        "recent_dialogue": recent_dialogue,
    }


class SemanticInterpreter:
    """LLM semantic parser used only as an observable shadow component."""

    def __init__(
        self,
        llm_client: OpenRouterClient,
        *,
        model: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model

    def interpret(
        self,
        current_message: str,
        state_before: SessionState,
    ) -> SemanticInterpretationResult:
        started = monotonic()
        model = self.model or self.llm_client.settings.llm_model
        safe_message = redact_pii_for_model(current_message)
        payload = {
            "current_message": safe_message,
            "context_before_turn": semantic_context(state_before),
            "ontology": semantic_ontology_payload(),
            "output_schema": TurnUnderstanding.model_json_schema(),
        }
        fallback: dict[str, Any] = {
            "schema_version": "1.1",
            "language": "ru",
            "operation": "unknown",
            "acts": [],
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.0,
        }
        requested = bool(self.llm_client.settings.llm_enabled)
        try:
            raw, transport_succeeded = self.llm_client.complete_json(
                "SemanticInterpreter.shadow",
                [
                    {"role": "system", "content": SEMANTIC_INTERPRETER_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                fallback=fallback,
                model=self.model,
            )
            if not transport_succeeded:
                return SemanticInterpretationResult(
                    status="skipped" if not requested else "rejected",
                    requested=requested,
                    transport_succeeded=False,
                    output_accepted=False,
                    model=model,
                    latency_ms=int((monotonic() - started) * 1000),
                    fallback_reason=getattr(
                        self.llm_client, "last_fallback_reason", None
                    ),
                )
            audit_payload = {
                **payload,
                # The audit receives even a schema-invalid first attempt.  Its
                # purpose includes repairing swapped enum fields or omitted
                # required values before the strict local validator decides.
                "candidate": raw,
            }
            audited_raw, audit_transport = self.llm_client.complete_json(
                "SemanticInterpreter.shadow.audit",
                [
                    {"role": "system", "content": SEMANTIC_AUDIT_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(audit_payload, ensure_ascii=False),
                    },
                ],
                fallback=raw,
                model=self.model,
            )
            audit_accepted = False
            audit_rejection: str | None = None
            understanding: TurnUnderstanding | None = None
            validation_errors: list[str] = []
            structural_repairs: tuple[str, ...] = ()
            if audit_transport:
                try:
                    repaired_audit, audit_repairs = repair_structural_enum_placement(
                        audited_raw,
                        safe_message,
                    )
                    audited = TurnUnderstanding.model_validate(repaired_audit)
                    validate_current_turn_evidence(audited, safe_message)
                    understanding = audited
                    audit_accepted = True
                    structural_repairs = audit_repairs
                except (ValidationError, ValueError, TypeError) as exc:
                    audit_rejection = str(exc)[:1200]
                    validation_errors.append(f"audit: {exc}")
            if understanding is None:
                try:
                    repaired_first, first_repairs = repair_structural_enum_placement(
                        raw,
                        safe_message,
                    )
                    first_pass = TurnUnderstanding.model_validate(repaired_first)
                    validate_current_turn_evidence(first_pass, safe_message)
                    understanding = first_pass
                    structural_repairs = first_repairs
                except (ValidationError, ValueError, TypeError) as exc:
                    validation_errors.append(f"first_pass: {exc}")
            if understanding is None:
                raise ValueError("; ".join(validation_errors)[:2000])
            return SemanticInterpretationResult(
                status="accepted",
                requested=True,
                transport_succeeded=True,
                output_accepted=True,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                understanding=understanding,
                audit_requested=True,
                audit_output_accepted=audit_accepted,
                structural_repairs=structural_repairs,
                audit_rejection_reason=(
                    audit_rejection
                    or (
                        None
                        if audit_transport
                        else getattr(self.llm_client, "last_fallback_reason", None)
                    )
                ),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return SemanticInterpretationResult(
                status="rejected",
                requested=requested,
                transport_succeeded=True,
                output_accepted=False,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                rejection_reason=str(exc)[:1200],
            )
        except Exception as exc:  # shadow failures must never escape
            return SemanticInterpretationResult(
                status="rejected",
                requested=requested,
                transport_succeeded=False,
                output_accepted=False,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                rejection_reason=f"{type(exc).__name__}: {exc}"[:1200],
            )
