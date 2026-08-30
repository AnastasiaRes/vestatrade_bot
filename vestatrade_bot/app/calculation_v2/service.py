"""Pure request building, arithmetic and validation for V2 Calculate."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import SessionState
from app.sku_resolution import (
    SkuResolutionStatus,
    resolve_catalog_sku_anchors,
)
from app.v2_visible_products import (
    customer_visible_v2_scope,
    has_deictic_product_reference,
    ordinal_indices,
)

from .contracts import (
    CalculationProductReference,
    CalculationReferenceKind,
    CalculationRequest,
    CalculationResult,
    CalculationResultStatus,
    CalculationScopeOrigin,
    CalculationSourceKind,
    CalculationSourceReference,
    CalculationUnit,
    StockAssessment,
)


_WORD_NUMBERS = {
    "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500,
}
_WORD_NUMBER_PATTERN = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
_NUMBER_PATTERN = rf"(?:\d+(?:[.,]\d+)?|(?:{_WORD_NUMBER_PATTERN})(?:\s+(?:{_WORD_NUMBER_PATTERN}))?)"
_QUANTITY_RE = re.compile(
    rf"\b(?P<number>{_NUMBER_PATTERN})\s*(?P<unit>"
    r"шт(?:\.|\b|ук\w*)|штук\w*|единиц\w*|"
    r"м(?:етр(?:а|ов)?)?\b|метр\w*)",
    re.IGNORECASE,
)


def _normalise(text: object) -> str:
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


def _stable_id(prefix: str, *parts: object) -> str:
    material = chr(31).join(map(str, parts))
    return f"{prefix}_{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def _spoken_number(text: str) -> Decimal | None:
    words = _normalise(text).split()
    if not words or any(word not in _WORD_NUMBERS for word in words):
        return None
    value = sum(_WORD_NUMBERS[word] for word in words)
    return Decimal(value) if value > 0 else None


def _quantity(message: str) -> tuple[Decimal | None, CalculationUnit | None, str | None]:
    """Extract one explicit purchasable quantity, never an arbitrary number."""

    matches = list(_QUANTITY_RE.finditer(message))
    if len(matches) != 1:
        return None, None, None
    match = matches[0]
    raw_number = match.group("number")
    value = _decimal(raw_number) if re.search(r"\d", raw_number) else _spoken_number(raw_number)
    if value is None or value <= 0:
        return None, None, None
    raw_unit = _normalise(match.group("unit"))
    unit = (
        CalculationUnit.METRE
        if raw_unit.startswith("м") or raw_unit.startswith("метр")
        else CalculationUnit.PIECE
    )
    if unit == CalculationUnit.PIECE and value != value.to_integral_value():
        return None, None, None
    return value, unit, match.group(0)


def _calculation_task(outcome: DialogueV2Outcome):
    plan = outcome.next_action_plan
    if plan is None:
        return None
    actions = tuple(item for item in (plan.primary, plan.secondary) if item is not None)
    action = next((item for item in actions if item.kind == NextActionKind.CALCULATE_PRELIMINARY), None)
    if action is None:
        return None
    task = next((item for item in outcome.state_after.tasks if item.task_id == action.task_id), None)
    return task if task is not None and task.act == TaskAct.CALCULATE else None


def _explicit_reference(message: str, snapshot: AnswerSourceSnapshot) -> CalculationProductReference | None:
    anchors = resolve_catalog_sku_anchors(message, snapshot.products)
    resolved = [
        (anchor.text, anchor.resolution)
        for anchor in anchors
        if anchor.resolution.status
        in {SkuResolutionStatus.EXACT, SkuResolutionStatus.UNIQUE_PREFIX}
    ]
    ambiguous = [
        (anchor.text, anchor.resolution)
        for anchor in anchors
        if anchor.resolution.status == SkuResolutionStatus.AMBIGUOUS_PREFIX
    ]
    if len(resolved) == 1:
        token, result = resolved[0]
        return CalculationProductReference(
            kind=(CalculationReferenceKind.EXACT_SKU if result.status == SkuResolutionStatus.EXACT else CalculationReferenceKind.PARTIAL_SKU),
            raw=token,
            canonical_sku=result.canonical_sku,
            candidate_skus=tuple(item.sku for item in result.candidates),
            reason_code=("explicit_exact_sku" if result.status == SkuResolutionStatus.EXACT else "explicit_unique_partial_sku"),
        )
    if len(resolved) > 1:
        return CalculationProductReference(
            kind=CalculationReferenceKind.UNRESOLVED,
            raw="; ".join(item[0] for item in resolved),
            candidate_skus=tuple(dict.fromkeys(sku.sku for _, result in resolved for sku in result.candidates)),
            reason_code="multiple_explicit_product_references",
        )
    if ambiguous:
        token, result = ambiguous[0]
        return CalculationProductReference(
            kind=CalculationReferenceKind.UNRESOLVED,
            raw=token,
            candidate_skus=tuple(item.sku for item in result.candidates),
            reason_code="ambiguous_partial_sku",
        )
    return None


def build_calculation_request(
    outcome: DialogueV2Outcome,
    session: SessionState,
    snapshot: AnswerSourceSnapshot,
    *,
    original_utterance: str,
) -> CalculationRequest | None:
    """Project only a typed CALCULATE action and a safe product scope."""

    task = _calculation_task(outcome)
    if task is None:
        return None
    quantity, unit, evidence = _quantity(original_utterance)
    explicit = _explicit_reference(original_utterance, snapshot)
    visible_scope = customer_visible_v2_scope(session)
    visible_skus = visible_scope.ordered_skus
    visible_scope_valid = visible_scope.is_valid
    if explicit is not None:
        reference = explicit
        scope_origin = (
            CalculationScopeOrigin.V2_DELIVERED
            if explicit.canonical_sku in visible_skus and visible_scope_valid
            else CalculationScopeOrigin.EXPLICIT_SKU
        )
    elif visible_scope_valid:
        ordinals = ordinal_indices(original_utterance)
        if len(ordinals) == 1:
            ordinal_reference = visible_scope.ordinal(ordinals[0])
            if ordinal_reference.resolved:
                sku = ordinal_reference.canonical_sku
                assert sku is not None
                reference = CalculationProductReference(
                    kind=CalculationReferenceKind.ORDINAL,
                    raw=ordinal_reference.raw,
                    canonical_sku=sku,
                    candidate_skus=(sku,),
                    reason_code=ordinal_reference.reason_code,
                )
            else:
                reference = CalculationProductReference(
                    kind=CalculationReferenceKind.UNRESOLVED,
                    raw=ordinal_reference.raw,
                    reason_code=ordinal_reference.reason_code,
                )
        elif len(ordinals) > 1:
            reference = CalculationProductReference(
                kind=CalculationReferenceKind.UNRESOLVED,
                reason_code="multiple_ordinal_product_references",
            )
        elif has_deictic_product_reference(original_utterance):
            focus_reference = visible_scope.current_focus()
            if focus_reference.resolved:
                sku = focus_reference.canonical_sku
                assert sku is not None
                reference = CalculationProductReference(
                    kind=CalculationReferenceKind.CURRENT_FOCUS,
                    raw=focus_reference.raw,
                    canonical_sku=sku,
                    candidate_skus=(sku,),
                    reason_code=focus_reference.reason_code,
                )
            else:
                reference = CalculationProductReference(
                    kind=CalculationReferenceKind.UNRESOLVED,
                    raw=focus_reference.raw,
                    reason_code=focus_reference.reason_code,
                )
        elif len(visible_skus) == 1:
            reference = CalculationProductReference(
                kind=CalculationReferenceKind.SINGLE_PRESENTED,
                raw=visible_skus[0],
                canonical_sku=visible_skus[0],
                candidate_skus=(visible_skus[0],),
                reason_code="single_customer_visible_v2_card",
            )
        else:
            reference = CalculationProductReference(
                kind=CalculationReferenceKind.UNRESOLVED,
                reason_code="calculation_product_reference_missing_in_multi_card_scope",
            )
        scope_origin = CalculationScopeOrigin.V2_DELIVERED
    else:
        reference = CalculationProductReference(
            kind=CalculationReferenceKind.UNRESOLVED,
            reason_code="calculation_scope_missing",
        )
        scope_origin = CalculationScopeOrigin.NONE
    return CalculationRequest(
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        original_utterance=original_utterance,
        selection_id=(
            visible_scope.selection_id
            if scope_origin == CalculationScopeOrigin.V2_DELIVERED
            else None
        ),
        ordered_skus=visible_skus if scope_origin == CalculationScopeOrigin.V2_DELIVERED else (),
        source_revision=(
            visible_scope.source_revision
            if scope_origin == CalculationScopeOrigin.V2_DELIVERED
            else snapshot.source_revision
        ),
        scope_origin=scope_origin,
        product_ref=reference,
        quantity=quantity,
        quantity_unit=unit,
        quantity_evidence=evidence,
    )


def _source(product: CatalogAnswerProduct, field_name: str, kind: CalculationSourceKind, revision: str, raw_value: object) -> CalculationSourceReference:
    return CalculationSourceReference(
        source_ref_id=_stable_id("calculation_source", product.sku, field_name, kind.value, revision),
        sku=product.sku,
        field_name=field_name,
        source_kind=kind,
        source_revision=revision,
        raw_value=str(raw_value),
    )


def _price_basis(product: CatalogAnswerProduct) -> CalculationUnit | None:
    facts = tuple(item for item in product.facts if item.name == "price_unit")
    if len(facts) != 1:
        return None
    value = _normalise(facts[0].value)
    return CalculationUnit.METRE if value in {"m", "метр", "погонный метр"} else None


def _stock_basis(product: CatalogAnswerProduct) -> CalculationUnit | None:
    """Return a stock unit only when the snapshot explicitly establishes it.

    Feed stock is a raw count. It can be shown to a buyer, but is not silently
    treated as pieces because a warehouse can count packs or another unit.
    """

    facts = tuple(item for item in product.facts if item.name == "stock_unit")
    if len(facts) != 1:
        return None
    return (
        CalculationUnit.PIECE
        if _normalise(facts[0].value) in {"pcs", "piece", "шт"}
        else None
    )


def build_calculation_result(
    request: CalculationRequest,
    snapshot: AnswerSourceSnapshot,
) -> CalculationResult:
    """Calculate only from a current immutable source snapshot."""

    common = dict(
        task_id=request.task_id,
        goal_id=request.goal_id,
        selection_id=request.selection_id,
        source_revision=request.source_revision,
        scope_origin=request.scope_origin,
        product_ref=request.product_ref,
    )
    if request.scope_origin == CalculationScopeOrigin.NONE:
        return CalculationResult(
            status=CalculationResultStatus.NEED_CLARIFICATION,
            clarification="Сначала покажите нужный товар или назовите его артикул — тогда посчитаю стоимость.",
            reason_codes=("calculation_scope_missing",),
            **common,
        )
    if request.source_revision != snapshot.source_revision:
        return CalculationResult(
            status=CalculationResultStatus.REJECTED,
            reason_codes=("calculation_source_revision_stale",),
            **common,
        )
    if request.quantity is None or request.quantity_unit is None:
        return CalculationResult(
            status=CalculationResultStatus.NEED_CLARIFICATION,
            clarification="Укажите количество и единицу: например, «20 шт.» или «15 м».",
            reason_codes=("calculation_quantity_missing_or_invalid",),
            **common,
        )
    sku = request.product_ref.canonical_sku
    if not sku:
        clarification = (
            "Укажите, для какой из показанных карточек посчитать количество: первой, второй или по артикулу."
            if request.scope_origin == CalculationScopeOrigin.V2_DELIVERED
            else "Укажите точный артикул товара, для которого нужно посчитать стоимость."
        )
        return CalculationResult(
            status=CalculationResultStatus.NEED_CLARIFICATION,
            clarification=clarification,
            reason_codes=(request.product_ref.reason_code,),
            **common,
        )
    if request.scope_origin == CalculationScopeOrigin.V2_DELIVERED and sku not in request.ordered_skus:
        return CalculationResult(
            status=CalculationResultStatus.REJECTED,
            reason_codes=("calculation_sku_outside_customer_visible_scope",),
            **common,
        )
    product = snapshot.product(sku)
    if product is None:
        return CalculationResult(
            status=CalculationResultStatus.REJECTED,
            reason_codes=("calculation_resolved_sku_missing_from_source_snapshot",),
            **common,
        )
    if product.price is None or not product.currency:
        return CalculationResult(
            status=CalculationResultStatus.NOT_CALCULABLE,
            sku=sku,
            product_name=product.name,
            quantity=request.quantity,
            quantity_unit=request.quantity_unit,
            clarification=f"Для «{product.name}» в каталоге не подтверждена цена, поэтому итоговую стоимость не вычисляю.",
            reason_codes=("calculation_catalog_price_missing",),
            **common,
        )
    price_basis = CalculationUnit.PIECE
    sources = [_source(product, "price", CalculationSourceKind.CATALOG_PRICE, snapshot.source_revision, product.price)]
    if request.quantity_unit == CalculationUnit.METRE:
        price_basis = _price_basis(product) or CalculationUnit.PIECE
        if price_basis != CalculationUnit.METRE:
            return CalculationResult(
                status=CalculationResultStatus.NOT_CALCULABLE,
                sku=sku,
                product_name=product.name,
                quantity=request.quantity,
                quantity_unit=request.quantity_unit,
                currency=product.currency,
                clarification=(
                    f"Для «{product.name}» не подтверждено, что цена указана за метр. "
                    "Итоговую стоимость метража без этого не вычисляю."
                ),
                sources=tuple(sources),
                reason_codes=("calculation_price_unit_for_metre_not_confirmed",),
                **common,
            )
        price_unit_fact = next(item for item in product.facts if item.name == "price_unit")
        sources.append(_source(product, "price_unit", CalculationSourceKind.CATALOG_ATTRIBUTE, snapshot.source_revision, price_unit_fact.value))
    total = Decimal(str(product.price)) * request.quantity
    stock_assessment = StockAssessment.UNKNOWN
    stock_delta = None
    if request.quantity_unit == CalculationUnit.PIECE and product.stock_qty is not None:
        sources.append(_source(product, "stock_qty", CalculationSourceKind.CATALOG_STOCK, snapshot.source_revision, product.stock_qty))
        if _stock_basis(product) == CalculationUnit.PIECE:
            stock_assessment = (
                StockAssessment.SUFFICIENT if Decimal(product.stock_qty) >= request.quantity else StockAssessment.INSUFFICIENT
            )
            stock_delta = Decimal(product.stock_qty) - request.quantity
            sources.append(_source(product, "stock_unit", CalculationSourceKind.CATALOG_ATTRIBUTE, snapshot.source_revision, "pcs"))
        else:
            stock_assessment = StockAssessment.UNIT_UNCONFIRMED
    elif product.stock_status:
        sources.append(_source(product, "stock_status", CalculationSourceKind.CATALOG_STOCK, snapshot.source_revision, product.stock_status))
    return CalculationResult(
        status=CalculationResultStatus.CALCULATED,
        sku=sku,
        product_name=product.name,
        quantity=request.quantity,
        quantity_unit=request.quantity_unit,
        unit_price=Decimal(str(product.price)),
        price_basis_unit=price_basis,
        currency=product.currency,
        total=total,
        stock_qty=product.stock_qty,
        stock_assessment=stock_assessment,
        stock_delta=stock_delta,
        sources=tuple(sources),
        reason_codes=("calculation_from_current_source_snapshot",),
        **common,
    )


def validate_calculation_result(
    request: CalculationRequest,
    result: CalculationResult,
    snapshot: AnswerSourceSnapshot,
) -> CalculationResult:
    """Fail closed on scope, arithmetic, source or price-basis drift."""

    reasons = list(result.reason_codes)
    passed = result.status in {
        CalculationResultStatus.CALCULATED,
        CalculationResultStatus.NEED_CLARIFICATION,
        CalculationResultStatus.NOT_CALCULABLE,
    }
    if result.task_id != request.task_id or result.goal_id != request.goal_id:
        passed = False
        reasons.append("calculation_task_identity_mismatch")
    if result.selection_id != request.selection_id or result.source_revision != request.source_revision:
        passed = False
        reasons.append("calculation_request_result_identity_mismatch")
    if request.source_revision != snapshot.source_revision:
        passed = False
        reasons.append("calculation_source_revision_stale")
    if result.status == CalculationResultStatus.CALCULATED:
        product = snapshot.product(result.sku or "")
        if product is None or result.sku != request.product_ref.canonical_sku:
            passed = False
            reasons.append("calculation_product_scope_mismatch")
        if request.scope_origin == CalculationScopeOrigin.V2_DELIVERED and result.sku not in request.ordered_skus:
            passed = False
            reasons.append("calculation_sku_outside_customer_visible_scope")
        if (
            result.quantity != request.quantity
            or result.quantity_unit != request.quantity_unit
            or result.unit_price is None
            or result.total is None
            or result.unit_price * result.quantity != result.total
        ):
            passed = False
            reasons.append("calculation_arithmetic_mismatch")
        if product is None or product.price is None or product.currency != result.currency or Decimal(str(product.price)) != result.unit_price:
            passed = False
            reasons.append("calculation_price_does_not_match_source_snapshot")
        source_by_id = {item.source_ref_id: item for item in result.sources}
        price_refs = [item for item in source_by_id.values() if item.source_kind == CalculationSourceKind.CATALOG_PRICE]
        if len(price_refs) != 1 or price_refs[0].sku != result.sku or price_refs[0].source_revision != snapshot.source_revision:
            passed = False
            reasons.append("calculation_price_source_reference_invalid")
        if result.quantity_unit == CalculationUnit.METRE:
            price_unit_refs = [item for item in source_by_id.values() if item.field_name == "price_unit"]
            actual_basis = _price_basis(product) if product is not None else None
            if result.price_basis_unit != CalculationUnit.METRE or actual_basis != CalculationUnit.METRE or len(price_unit_refs) != 1:
                passed = False
                reasons.append("calculation_price_unit_source_reference_invalid")
    if result.status == CalculationResultStatus.REJECTED:
        passed = False
    return result.model_copy(update={"outcome_gate_passed": passed, "reason_codes": tuple(dict.fromkeys(reasons))})
