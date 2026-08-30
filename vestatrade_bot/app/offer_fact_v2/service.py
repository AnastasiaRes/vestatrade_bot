"""Read-only, source-bound answers for price, stock and link questions."""

from __future__ import annotations

import hashlib
import re

from app.answer_v2.contracts import AnswerSourceSnapshot
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import SessionState
from app.sku_resolution import SkuResolutionStatus, resolve_catalog_sku_anchors
from app.v2_visible_products import (
    customer_visible_v2_scope,
    has_deictic_product_reference,
    ordinal_indices,
)

from .contracts import (
    OfferFactKind,
    OfferFactProductReference,
    OfferFactReferenceKind,
    OfferFactRequest,
    OfferFactResult,
    OfferFactScopeOrigin,
    OfferFactSourceReference,
    OfferFactStatus,
)


_PRICE_RE = re.compile(r"(?iu)(?:сколько\s+стоит|какая\s+цена|цена|стоимост)")
_STOCK_RE = re.compile(r"(?iu)(?:есть\s+ли\s+(?:он\s+)?в\s+наличии|в\s+наличии|остаток)")
_LINK_RE = re.compile(r"(?iu)(?:ссылк\w*|где\s+открыть\s+товар)")
_QUANTITY_RE = re.compile(r"(?iu)(?:\b\d+(?:[.,]\d+)?\s*(?:шт\.?|штук|метр\w*)\b|\bпосчита\w*)")


def _task_for_offer(outcome: DialogueV2Outcome) -> tuple[str | None, str | None]:
    action = outcome.next_action_plan.primary if outcome.next_action_plan else None
    if action is None:
        return None, outcome.state_after.active_goal_id
    task = next(
        (item for item in outcome.state_after.tasks if item.task_id == action.task_id),
        None,
    )
    if task is not None and task.act in {TaskAct.CHECK_PRICE, TaskAct.CHECK_STOCK, TaskAct.GET_LINK}:
        return task.task_id, task.target_goal_id
    if action.kind == NextActionKind.ANSWER_DIRECT_QUESTION:
        return action.task_id, outcome.state_after.active_goal_id
    return None, outcome.state_after.active_goal_id


def _kind(text: str) -> OfferFactKind | None:
    # Quantified requests are owned by Calculation even if they happen to use
    # the word "стоит".  This service only reads one existing offer fact.
    if _QUANTITY_RE.search(text):
        return None
    if _PRICE_RE.search(text):
        return OfferFactKind.PRICE
    if _STOCK_RE.search(text):
        return OfferFactKind.STOCK
    if _LINK_RE.search(text):
        return OfferFactKind.LINK
    return None


def _reference(
    text: str,
    snapshot: AnswerSourceSnapshot,
    session: SessionState,
) -> tuple[OfferFactProductReference, OfferFactScopeOrigin, tuple[str, ...], str | None, str | None]:
    scope = customer_visible_v2_scope(session)
    anchors = resolve_catalog_sku_anchors(text, snapshot.products)
    proven = [
        item for item in anchors
        if item.resolution.status in {SkuResolutionStatus.EXACT, SkuResolutionStatus.UNIQUE_PREFIX}
        and len(item.resolution.candidates) == 1
    ]
    if len(proven) == 1:
        sku = proven[0].canonical_sku
        assert sku is not None
        return (
            OfferFactProductReference(
                kind=(
                    OfferFactReferenceKind.EXACT_SKU
                    if proven[0].resolution.status == SkuResolutionStatus.EXACT
                    else OfferFactReferenceKind.PARTIAL_SKU
                ),
                raw=proven[0].text,
                canonical_sku=sku,
                candidate_skus=(sku,),
                reason_code="catalog_bound_sku_offer_reference",
            ),
            (
                OfferFactScopeOrigin.V2_DELIVERED
                if scope.is_valid and sku in scope.ordered_skus
                else OfferFactScopeOrigin.EXPLICIT_SKU
            ),
            scope.ordered_skus if scope.is_valid else (),
            scope.selection_id if scope.is_valid else None,
            scope.source_revision if scope.is_valid else None,
        )
    if len(proven) > 1:
        return (
            OfferFactProductReference(
                kind=OfferFactReferenceKind.UNRESOLVED,
                raw="; ".join(item.text for item in proven),
                candidate_skus=tuple(
                    item.canonical_sku for item in proven if item.canonical_sku
                ),
                reason_code="multiple_explicit_offer_references",
            ),
            OfferFactScopeOrigin.NONE,
            (),
            None,
            None,
        )
    if not scope.is_valid:
        return (
            OfferFactProductReference(
                kind=OfferFactReferenceKind.UNRESOLVED,
                reason_code=scope.reason_code,
            ),
            OfferFactScopeOrigin.NONE,
            (),
            None,
            None,
        )
    ordinals = ordinal_indices(text)
    if len(ordinals) == 1:
        resolved = scope.ordinal(ordinals[0])
        return (
            OfferFactProductReference(
                kind=(OfferFactReferenceKind.ORDINAL if resolved.resolved else OfferFactReferenceKind.UNRESOLVED),
                raw=resolved.raw,
                canonical_sku=resolved.canonical_sku,
                candidate_skus=((resolved.canonical_sku,) if resolved.canonical_sku else ()),
                reason_code=resolved.reason_code,
            ),
            OfferFactScopeOrigin.V2_DELIVERED,
            scope.ordered_skus,
            scope.selection_id,
            scope.source_revision,
        )
    if len(ordinals) > 1:
        return (
            OfferFactProductReference(
                kind=OfferFactReferenceKind.UNRESOLVED,
                raw="; ".join(str(item + 1) for item in ordinals),
                reason_code="multiple_ordinal_offer_references",
            ),
            OfferFactScopeOrigin.V2_DELIVERED,
            scope.ordered_skus,
            scope.selection_id,
            scope.source_revision,
        )
    if has_deictic_product_reference(text):
        resolved = scope.current_focus()
        return (
            OfferFactProductReference(
                kind=(OfferFactReferenceKind.CURRENT_FOCUS if resolved.resolved else OfferFactReferenceKind.UNRESOLVED),
                raw=resolved.raw,
                canonical_sku=resolved.canonical_sku,
                candidate_skus=((resolved.canonical_sku,) if resolved.canonical_sku else ()),
                reason_code=resolved.reason_code,
            ),
            OfferFactScopeOrigin.V2_DELIVERED,
            scope.ordered_skus,
            scope.selection_id,
            scope.source_revision,
        )
    if len(scope.ordered_skus) == 1:
        sku = scope.ordered_skus[0]
        return (
            OfferFactProductReference(
                kind=OfferFactReferenceKind.SINGLE_PRESENTED,
                raw=sku,
                canonical_sku=sku,
                candidate_skus=(sku,),
                reason_code="single_customer_visible_v2_card",
            ),
            OfferFactScopeOrigin.V2_DELIVERED,
            scope.ordered_skus,
            scope.selection_id,
            scope.source_revision,
        )
    return (
        OfferFactProductReference(
            kind=OfferFactReferenceKind.UNRESOLVED,
            reason_code="multiple_customer_visible_cards_require_reference",
        ),
        OfferFactScopeOrigin.V2_DELIVERED,
        scope.ordered_skus,
        scope.selection_id,
        scope.source_revision,
    )


def build_offer_fact_request(
    outcome: DialogueV2Outcome,
    session: SessionState,
    snapshot: AnswerSourceSnapshot,
    *,
    original_utterance: str,
) -> OfferFactRequest | None:
    kind = _kind(original_utterance)
    if kind is None:
        return None
    reference, origin, ordered, selection_id, revision = _reference(
        original_utterance,
        snapshot,
        session,
    )
    task_id, goal_id = _task_for_offer(outcome)
    return OfferFactRequest(
        task_id=task_id,
        goal_id=goal_id,
        original_utterance=original_utterance,
        fact_kind=kind,
        selection_id=selection_id,
        ordered_skus=ordered,
        source_revision=revision,
        scope_origin=origin,
        product_ref=reference,
    )


def build_offer_fact_result(
    request: OfferFactRequest,
    snapshot: AnswerSourceSnapshot,
) -> OfferFactResult:
    reference = request.product_ref
    if request.source_revision and request.source_revision != snapshot.source_revision:
        return OfferFactResult(
            status=OfferFactStatus.REJECTED,
            task_id=request.task_id,
            goal_id=request.goal_id,
            fact_kind=request.fact_kind,
            selection_id=request.selection_id,
            source_revision=request.source_revision,
            scope_origin=request.scope_origin,
            product_ref=reference,
            reason_codes=("offer_scope_source_revision_stale",),
        )
    if reference.canonical_sku is None:
        return OfferFactResult(
            status=OfferFactStatus.NEED_CLARIFICATION,
            task_id=request.task_id,
            goal_id=request.goal_id,
            fact_kind=request.fact_kind,
            selection_id=request.selection_id,
            source_revision=snapshot.source_revision,
            scope_origin=request.scope_origin,
            product_ref=reference,
            clarification="Уточните, пожалуйста, номер варианта или артикул товара.",
            outcome_gate_passed=True,
            reason_codes=(reference.reason_code,),
        )
    product = snapshot.product(reference.canonical_sku)
    if product is None:
        return OfferFactResult(
            status=OfferFactStatus.REJECTED,
            task_id=request.task_id,
            goal_id=request.goal_id,
            fact_kind=request.fact_kind,
            selection_id=request.selection_id,
            source_revision=snapshot.source_revision,
            scope_origin=request.scope_origin,
            product_ref=reference,
            reason_codes=("offer_product_missing_from_source_snapshot",),
        )
    if request.fact_kind == OfferFactKind.PRICE:
        value, field, currency = product.price, "price", product.currency
    elif request.fact_kind == OfferFactKind.STOCK:
        value, field, currency = product.stock_status, "stock_status", None
    else:
        value, field, currency = product.url, "url", None
    if value is None or (request.fact_kind == OfferFactKind.PRICE and not currency):
        return OfferFactResult(
            status=OfferFactStatus.REJECTED,
            task_id=request.task_id,
            goal_id=request.goal_id,
            fact_kind=request.fact_kind,
            selection_id=request.selection_id,
            source_revision=snapshot.source_revision,
            scope_origin=request.scope_origin,
            product_ref=reference,
            sku=product.sku,
            product_name=product.name,
            reason_codes=("offer_source_field_missing",),
        )
    source = OfferFactSourceReference(
        source_ref_id="offer_" + hashlib.sha256(
            f"{product.sku}:{field}:{snapshot.source_revision}".encode()
        ).hexdigest()[:20],
        sku=product.sku,
        field_name=field,
        source_revision=snapshot.source_revision,
        raw_value=str(value),
    )
    return OfferFactResult(
        status=OfferFactStatus.ANSWERED,
        task_id=request.task_id,
        goal_id=request.goal_id,
        fact_kind=request.fact_kind,
        selection_id=request.selection_id,
        source_revision=snapshot.source_revision,
        scope_origin=request.scope_origin,
        product_ref=reference,
        sku=product.sku,
        product_name=product.name,
        value=value,
        currency=currency,
        source=source,
        outcome_gate_passed=True,
        reason_codes=("offer_fact_from_current_source_snapshot",),
    )


def render_offer_fact_result(result: OfferFactResult) -> str:
    if result.status == OfferFactStatus.NEED_CLARIFICATION:
        return result.clarification or "Уточните товар, пожалуйста."
    if result.status != OfferFactStatus.ANSWERED:
        return "Не могу подтвердить цену, наличие или ссылку по этому товару из текущего каталога."
    assert result.product_name is not None
    if result.fact_kind == OfferFactKind.PRICE:
        assert result.currency is not None
        value = float(result.value) if isinstance(result.value, (int, float)) else result.value
        price = f"{value:g}" if isinstance(value, float) else str(value)
        currency = "₽" if result.currency.upper() in {"RUB", "RUR"} else result.currency
        return f"{result.product_name}: {price} {currency}."
    if result.fact_kind == OfferFactKind.STOCK:
        return f"{result.product_name}: {result.value}."
    return f"Ссылка на {result.product_name}: {result.value}"
