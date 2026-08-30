"""Build and validate deterministic comparisons from the V2 source snapshot."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import SessionState
from app.v2_presentation import public_fact_label

from .contracts import (
    ComparisonCriterion,
    ComparisonDimension,
    ComparisonRecommendation,
    ComparisonRequest,
    ComparisonResult,
    ComparisonResultStatus,
    ComparisonSourceKind,
    ComparisonSourceReference,
    ComparisonValue,
)


# Identity fields are useful for source gates but are not meaningful comparison
# dimensions.  Brand may be shown as a factual difference, but it must not be
# presented as the customer's deciding technical criterion.
_NON_COMPARABLE_COMMON_FACTS = frozenset({"sku", "price_unit"})
_NON_DECIDING_PREDICATES = frozenset({"sku", "brand"})
_PREDICATE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("дешев", "цен", "стоим")),
    ("availability", ("налич", "остат", "склад")),
    ("installation_length_mm", ("монтажн", "между присоедин")),
    ("operating_temperature_c", ("температур",)),
    ("operating_pressure_bar", ("давлен",)),
    ("max_head_m", ("напор",)),
    ("max_flow_l_h", ("расход", "подач")),
    ("diameter_mm", ("диаметр", "размер")),
    ("reinforcement", ("армир", "стекловолок", "алюмин")),
    ("connection_pattern", ("резьб", "вн-вн", "вр/вр")),
    ("material", ("материал",)),
)


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(map(str, parts)).encode()).hexdigest()[:20]}"


def _normalized(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _requested_predicates(message: str) -> tuple[str, ...]:
    lowered = _normalized(message)
    return tuple(
        predicate
        for predicate, patterns in _PREDICATE_PATTERNS
        if any(item in lowered for item in patterns)
    )


def _criterion(message: str) -> ComparisonCriterion | None:
    lowered = _normalized(message)
    if any(item in lowered for item in ("дешевле", "самый дешев", "минимальн")):
        return ComparisonCriterion.LOWEST_PRICE
    if any(item in lowered for item in ("что есть в наличии", "в наличии", "остаток")):
        return ComparisonCriterion.AVAILABILITY
    return None


def _comparison_task(outcome: DialogueV2Outcome):
    plan = outcome.next_action_plan
    if plan is None:
        return None
    actions = tuple(item for item in (plan.primary, plan.secondary) if item is not None)
    compare_action = next((item for item in actions if item.kind == NextActionKind.COMPARE), None)
    if compare_action is None:
        return None
    task = next(
        (item for item in outcome.state_after.tasks if item.task_id == compare_action.task_id),
        None,
    )
    if task is None or task.act != TaskAct.COMPARE:
        return None
    return task


def build_comparison_request(
    outcome: DialogueV2Outcome,
    session: SessionState,
    *,
    original_utterance: str,
) -> ComparisonRequest | None:
    """Project only a typed COMPARE action and customer-visible scope."""

    task = _comparison_task(outcome)
    if task is None:
        return None
    # A legacy list may be read by Shadow for diagnostics, but has no stored
    # revision / selection identity and therefore can never pass V2 delivery.
    if session.v2_last_products:
        ordered_skus = tuple(item.sku for item in session.v2_last_products)
        origin: str = "v2_delivered"
        selection_id = session.v2_selection_id
        revision = session.v2_source_revision
    elif session.last_products:
        ordered_skus = tuple(item.sku for item in session.last_products)
        origin = "legacy_unversioned"
        selection_id = None
        revision = None
    else:
        ordered_skus = ()
        origin = "none"
        selection_id = None
        revision = None
    return ComparisonRequest(
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        original_utterance=original_utterance,
        selection_id=selection_id,
        ordered_skus=ordered_skus,
        requested_predicates=_requested_predicates(original_utterance),
        criterion=_criterion(original_utterance),
        source_revision=revision,
        scope_origin=origin,  # type: ignore[arg-type]
    )


def _source_ref(
    product: CatalogAnswerProduct,
    predicate: str,
    kind: ComparisonSourceKind,
    revision: str,
    *,
    field_name: str | None = None,
    source_field: str | None = None,
    raw_value: str | None = None,
) -> ComparisonSourceReference:
    return ComparisonSourceReference(
        source_ref_id=_stable_id(
            "comparison_source", product.sku, predicate, kind.value, field_name, revision
        ),
        sku=product.sku,
        predicate=predicate,
        source_kind=kind,
        source_revision=revision,
        field_name=field_name,
        source_field=source_field,
        raw_value=raw_value,
    )


def _exact_fact(product: CatalogAnswerProduct, predicate: str):
    values = tuple(item for item in product.facts if item.name == predicate)
    issues = tuple(item for item in product.fact_issues if item.name == predicate)
    distinct = {(str(item.value), item.unit) for item in values}
    if issues or len(distinct) != 1:
        return None
    return values[0] if values else None


def _dimension_from_products(
    products: tuple[CatalogAnswerProduct, ...],
    predicate: str,
    revision: str,
) -> tuple[ComparisonDimension, tuple[ComparisonSourceReference, ...]]:
    values: list[ComparisonValue] = []
    sources: list[ComparisonSourceReference] = []
    missing: list[str] = []
    if predicate == "price":
        for product in products:
            if product.price is None or not product.currency:
                missing.append(product.sku)
                continue
            source = _source_ref(product, predicate, ComparisonSourceKind.CATALOG_PRICE, revision, field_name="price", raw_value=str(product.price))
            sources.append(source)
            values.append(ComparisonValue(sku=product.sku, predicate=predicate, value=product.price, unit=product.currency, source_ref_ids=(source.source_ref_id,)))
    elif predicate == "availability":
        for product in products:
            if not product.stock_status:
                missing.append(product.sku)
                continue
            source = _source_ref(product, predicate, ComparisonSourceKind.CATALOG_STOCK, revision, field_name="stock_status", raw_value=product.stock_status)
            sources.append(source)
            # Stock status and stock quantity are different predicates.  Do
            # not attach ``шт.`` to a textual status such as "в наличии".
            values.append(ComparisonValue(sku=product.sku, predicate=predicate, value=product.stock_status, source_ref_ids=(source.source_ref_id,)))
    else:
        for product in products:
            fact = _exact_fact(product, predicate)
            if fact is None:
                missing.append(product.sku)
                continue
            source = _source_ref(product, predicate, ComparisonSourceKind.CATALOG_ATTRIBUTE, revision, field_name=predicate, source_field=fact.provenance.source_field, raw_value=fact.provenance.raw_value)
            sources.append(source)
            values.append(ComparisonValue(sku=product.sku, predicate=predicate, value=fact.value, unit=fact.unit, source_ref_ids=(source.source_ref_id,)))
    return (
        ComparisonDimension(
            predicate=predicate,
            label=public_fact_label(predicate),
            values=tuple(values),
            missing_skus=tuple(missing),
            missing_reason_codes=("catalogue_value_missing_or_ambiguous",) if missing else (),
        ),
        tuple(sources),
    )


def _has_proven_difference(dimension: ComparisonDimension) -> bool:
    if dimension.missing_skus:
        return False
    return len({(str(item.value), item.unit) for item in dimension.values}) > 1


def _common_fact_predicates(products: tuple[CatalogAnswerProduct, ...]) -> tuple[str, ...]:
    if not products:
        return ()
    common: set[str] | None = None
    for product in products:
        names = {
            fact.name
            for fact in product.facts
            if (
                fact.name not in _NON_COMPARABLE_COMMON_FACTS
                and _exact_fact(product, fact.name) is not None
            )
        }
        common = names if common is None else common.intersection(names)
    return tuple(sorted(common or ()))


def _is_same_visible_card(product: CatalogAnswerProduct, card: object) -> bool:
    return all(
        (
            product.sku == str(getattr(card, "sku", "")),
            product.name == str(getattr(card, "name", "")),
            product.price == getattr(card, "price", None),
            product.currency == getattr(card, "currency", None),
            product.stock_status == getattr(card, "stock_status", None),
            product.url == getattr(card, "url", None),
            product.image_url == getattr(card, "image_url", None),
        )
    )


def build_comparison_result(
    request: ComparisonRequest,
    source_snapshot: AnswerSourceSnapshot,
    *,
    visible_cards: Iterable[object],
) -> ComparisonResult:
    """Compare only the current delivered list, never a global catalogue set."""

    if request.scope_origin == "none":
        return ComparisonResult(status=ComparisonResultStatus.NEED_CLARIFICATION, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, source_revision=request.source_revision, reason_codes=("comparison_scope_missing",))
    if request.scope_origin != "v2_delivered":
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_scope_not_v2_versioned",))
    if not request.selection_id or not request.source_revision:
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_selection_identity_missing",))
    if request.source_revision != source_snapshot.source_revision:
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_source_revision_stale",))
    if len(request.ordered_skus) < 2:
        return ComparisonResult(status=ComparisonResultStatus.NEED_CLARIFICATION, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_requires_two_visible_cards",))

    cards_by_sku = {str(getattr(card, "sku", "")): card for card in visible_cards}
    products: list[CatalogAnswerProduct] = []
    for sku in request.ordered_skus:
        product = source_snapshot.product(sku)
        card = cards_by_sku.get(sku)
        if product is None or card is None or not _is_same_visible_card(product, card):
            return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_visible_card_source_gate_failed",))
        products.append(product)
    typed_products = tuple(products)
    if len({item.product_kind for item in typed_products}) != 1:
        return ComparisonResult(status=ComparisonResultStatus.NOT_COMPARABLE, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_mixed_product_kind_scope",))

    predicates: list[str] = list(request.requested_predicates)
    for predicate in ("price", "availability", *_common_fact_predicates(typed_products)):
        if predicate not in predicates:
            predicates.append(predicate)
    dimensions: list[ComparisonDimension] = []
    sources: list[ComparisonSourceReference] = []
    missing: list[str] = []
    for predicate in predicates:
        dimension, refs = _dimension_from_products(typed_products, predicate, source_snapshot.source_revision)
        sources.extend(refs)
        if dimension.missing_skus:
            missing.append(predicate)
        if _has_proven_difference(dimension):
            dimensions.append(dimension)
        elif predicate in request.requested_predicates and dimension.missing_skus:
            dimensions.append(dimension)

    proved = tuple(item for item in dimensions if _has_proven_difference(item))
    if not proved:
        return ComparisonResult(status=ComparisonResultStatus.NOT_COMPARABLE, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, requested_predicates=request.requested_predicates, dimensions=tuple(dimensions), sources=tuple(sources), missing_data=tuple(dict.fromkeys(missing)), source_revision=source_snapshot.source_revision, reason_codes=("comparison_no_proven_difference",))

    recommendation = None
    if request.criterion == ComparisonCriterion.LOWEST_PRICE:
        price = next((item for item in proved if item.predicate == "price"), None)
        if price is not None and not price.missing_skus:
            lowest = min(price.values, key=lambda item: float(item.value))
            if sum(float(item.value) == float(lowest.value) for item in price.values) == 1:
                recommendation = ComparisonRecommendation(sku=lowest.sku, criterion=ComparisonCriterion.LOWEST_PRICE, source_ref_ids=lowest.source_ref_ids, reason_code="lowest_confirmed_price")

    generic_request = not request.requested_predicates and request.criterion is None
    question = None
    if generic_request:
        decision_dimensions = tuple(
            item for item in proved if item.predicate not in _NON_DECIDING_PREDICATES
        )
        if decision_dimensions:
            labels = ", ".join(item.label for item in decision_dimensions[:3])
            question = f"Какой критерий для вас решающий: {labels}?"
    return ComparisonResult(status=ComparisonResultStatus.COMPARED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, requested_predicates=request.requested_predicates, dimensions=tuple(dimensions), sources=tuple(sources), missing_data=tuple(dict.fromkeys(missing)), recommendation=recommendation, deciding_question=question, source_revision=source_snapshot.source_revision, reason_codes=("comparison_from_customer_visible_v2_scope",))


def validate_comparison_result(
    request: ComparisonRequest,
    result: ComparisonResult,
    source_snapshot: AnswerSourceSnapshot,
) -> ComparisonResult:
    """Fail closed on scope, source, predicate, or recommendation drift."""

    reasons = list(result.reason_codes)
    passed = result.status in {ComparisonResultStatus.COMPARED, ComparisonResultStatus.NEED_CLARIFICATION, ComparisonResultStatus.NOT_COMPARABLE}
    if result.selection_id != request.selection_id or result.source_revision != request.source_revision:
        passed = False
        reasons.append("comparison_request_result_identity_mismatch")
    if result.status == ComparisonResultStatus.COMPARED:
        if result.compared_skus != request.ordered_skus or len(result.compared_skus) < 2:
            passed = False
            reasons.append("comparison_scope_sku_mismatch")
        source_ids = {item.source_ref_id: item for item in result.sources}
        proven_difference = False
        for dimension in result.dimensions:
            for value in dimension.values:
                if value.sku not in request.ordered_skus or value.predicate != dimension.predicate:
                    passed = False
                    reasons.append("comparison_value_scope_or_predicate_mismatch")
                    continue
                refs = [source_ids.get(ref_id) for ref_id in value.source_ref_ids]
                if not refs or any(ref is None or ref.sku != value.sku or ref.predicate != value.predicate or ref.source_revision != source_snapshot.source_revision for ref in refs):
                    passed = False
                    reasons.append("comparison_value_source_reference_invalid")
                    continue
                product = source_snapshot.product(value.sku)
                reference = refs[0]
                source_value_matches = False
                if product is not None and reference is not None:
                    if reference.source_kind == ComparisonSourceKind.CATALOG_PRICE:
                        source_value_matches = (
                            product.price == value.value
                            and product.currency == value.unit
                        )
                    elif reference.source_kind == ComparisonSourceKind.CATALOG_STOCK:
                        source_value_matches = product.stock_status == value.value
                    elif reference.source_kind == ComparisonSourceKind.CATALOG_ATTRIBUTE:
                        fact = _exact_fact(product, value.predicate)
                        source_value_matches = bool(
                            fact is not None
                            and fact.value == value.value
                            and fact.unit == value.unit
                        )
                if not source_value_matches:
                    passed = False
                    reasons.append("comparison_value_does_not_match_source_snapshot")
            if _has_proven_difference(dimension):
                proven_difference = True
        if not proven_difference:
            passed = False
            reasons.append("comparison_no_proven_difference")
        if result.recommendation is not None:
            if result.recommendation.criterion != request.criterion:
                passed = False
                reasons.append("comparison_undemonstrated_recommendation")
            if result.recommendation.sku not in request.ordered_skus:
                passed = False
                reasons.append("comparison_recommendation_outside_scope")
            if result.recommendation.criterion == ComparisonCriterion.LOWEST_PRICE:
                prices = {
                    item.sku: item.value
                    for dimension in result.dimensions
                    if dimension.predicate == "price" and not dimension.missing_skus
                    for item in dimension.values
                }
                if (
                    result.recommendation.sku not in prices
                    or any(float(prices[result.recommendation.sku]) > float(value) for value in prices.values())
                ):
                    passed = False
                    reasons.append("comparison_recommendation_not_lowest_proven_price")
    if result.status == ComparisonResultStatus.REJECTED:
        passed = False
    return result.model_copy(update={"outcome_gate_passed": passed, "reason_codes": tuple(dict.fromkeys(reasons))})
