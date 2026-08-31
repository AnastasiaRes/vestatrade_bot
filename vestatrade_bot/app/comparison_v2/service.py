"""Build and validate deterministic comparisons from the V2 source snapshot."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.product_reference import (
    NamedProductResolutionStatus,
    resolve_strict_named_catalog_products,
)
from app.catalog_v2.registry import ProductContractRegistry
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import SessionState
from app.product_fact_evidence import ProductFactEvidenceService, ProductFactStatus
from app.v2_presentation import public_fact_label
from app.v2_visible_products import customer_visible_v2_scope, ordinal_indices

from .contracts import (
    ComparisonCriterion,
    ComparisonDimension,
    ComparisonProductReference,
    ComparisonReferenceKind,
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
_EXPLICIT_PASSPORT_PREDICATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "thermostatic_head_thread",
        re.compile(
            r"(?iu)(?:резьб\w*[^?.!]{0,32}(?:термоголов\w*|головк\w*)|"
            r"(?:термоголов\w*|головк\w*)[^?.!]{0,32}резьб\w*|посадочн\w*\s+резьб\w*)"
        ),
    ),
    (
        "expansion_tank_volume_l",
        re.compile(r"(?iu)расширительн\w*\s+бак\w*"),
    ),
    (
        "integrated_circulation_pump",
        re.compile(r"(?iu)(?:встроенн\w*|внутри)[^?.!]{0,32}насос\w*"),
    ),
)
_DECISION_REQUEST_MARKERS = (
    "что лучше",
    "какой лучше",
    "какая лучше",
    "какой выбрать",
    "какую выбрать",
    "что выбрать",
    "посоветуй",
    "посоветуйте",
    "рекомендуй",
    "рекомендуете",
)
_COMPARE_REFERENCE_INTENT_RE = re.compile(
    r"(?iu)(?:сравн\w*|сопостав\w*|отлич\w*|разниц\w*|какой[^?.!]{0,32}лучше)"
)
_NUMERIC_ORDINAL_TOKEN_RE = re.compile(r"(?<![\d/])([1-9]\d?)(?![\d/])")
_POSITION_WORD_RE = re.compile(r"(?iu)\b(?:вариант\w*|позиц\w*|товар\w*)\b")
_ORDINAL_UNIT_FOLLOWUP_RE = re.compile(
    r"(?iu)^\s*(?:м(?:м)?|метр\w*|шт\.?|штук\w*|руб\w*|₽|тыс\w*|литр\w*|квт|бар)\b"
)
_REFERENCE_NAME_STOPWORDS = frozenset(
    {"вариант", "позиция", "товар", "модель", "первый", "второй", "третий", "четвертый", "этот"}
)
_CARDINAL_POSITION_ORDINALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?iu)\b(?:один|одна|первую?\s+по\s+счету)\b"), 0),
    (re.compile(r"(?iu)\b(?:два|две)\b"), 1),
    (re.compile(r"(?iu)\bтри\b"), 2),
    (re.compile(r"(?iu)\bчетыре\b"), 3),
    (re.compile(r"(?iu)\bпять\b"), 4),
)


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(map(str, parts)).encode()).hexdigest()[:20]}"


def _normalized(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def _requested_predicates(message: str) -> tuple[str, ...]:
    lowered = _normalized(message)
    explicit_passport = {
        predicate
        for predicate, pattern in _EXPLICIT_PASSPORT_PREDICATES
        if pattern.search(lowered)
    }
    predicates = [
        predicate
        for predicate, patterns in _PREDICATE_PATTERNS
        if any(item in lowered for item in patterns)
        and not (
            predicate == "connection_pattern"
            and "thermostatic_head_thread" in explicit_passport
        )
    ]
    for predicate, _pattern in _EXPLICIT_PASSPORT_PREDICATES:
        if predicate in explicit_passport and predicate not in predicates:
            predicates.append(predicate)
    return tuple(predicates)


def _criterion(message: str) -> ComparisonCriterion | None:
    lowered = _normalized(message)
    if any(item in lowered for item in ("дешевле", "самый дешев", "минимальн")):
        return ComparisonCriterion.LOWEST_PRICE
    if any(item in lowered for item in ("что есть в наличии", "в наличии", "остаток")):
        return ComparisonCriterion.AVAILABILITY
    return None


def _needs_deciding_criterion(message: str) -> bool:
    """Whether the buyer asks us to choose, rather than list differences."""

    lowered = _normalized(message)
    return bool(
        any(marker in lowered for marker in _DECISION_REQUEST_MARKERS)
        or re.search(r"\b(?:что|какой|какая)\b[^?.!]{0,48}\bлучше\b", lowered)
    )


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


@dataclass(frozen=True)
class _ComparisonReferenceResolution:
    references: tuple[ComparisonProductReference, ...] = ()
    ordered_skus: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    explicit_request: bool = False


def _reference_text(value: object) -> str:
    return str(value or "").strip()


def _normalised_identifier(value: object) -> str:
    return re.sub(r"[^\w]", "", _reference_text(value).casefold().replace("ё", "е"))


def _semantic_reference_candidates(
    semantic_references: Iterable[object],
    original_utterance: str,
) -> tuple[tuple[str, str, str, str], ...]:
    """Accept only source-spanned, non-rejected semantic reference hints.

    The LLM may describe a natural phrase, but it cannot introduce a product.
    Later resolution is constrained exclusively to delivered cards.
    """

    message = _normalized(original_utterance)
    candidates: list[tuple[str, str, str, str]] = []
    for item in semantic_references:
        kind = _reference_text(getattr(item, "kind", ""))
        text = _reference_text(getattr(item, "text", ""))
        hint = _reference_text(getattr(item, "target_hint", ""))
        evidence = _reference_text(getattr(item, "evidence", ""))
        status = _reference_text(getattr(item, "validation_status", "accepted"))
        if (
            not kind
            or not text
            or status == "rejected"
            or not evidence
            or _normalized(evidence) not in message
            or _normalized(text) not in message
        ):
            continue
        candidates.append((kind, text, hint, evidence))
    return tuple(candidates)


def _numeric_comparison_ordinals(message: str) -> tuple[int, ...]:
    """Read bare 1/3 only in an unmistakable comparison expression.

    Bare numbers stay out of the shared ordinal parser: elsewhere they can be
    a quantity, price, diameter or pump designation.  Here a comparison action
    and a pair/range of small position numbers make the interpretation bounded.
    """

    if not _COMPARE_REFERENCE_INTENT_RE.search(message):
        return ()
    numeric: list[int] = []
    for match in _NUMERIC_ORDINAL_TOKEN_RE.finditer(message):
        if _ORDINAL_UNIT_FOLLOWUP_RE.match(message[match.end() :]):
            continue
        value = int(match.group(1))
        # A two-digit number is position-like only alongside an explicit list
        # noun.  This avoids treating a bare budget as an ordinal while still
        # yielding a useful clarification for «12-я позиция».
        if value > 9 and not _POSITION_WORD_RE.search(message):
            continue
        numeric.append(value - 1)
    if len(numeric) < 2 and not _POSITION_WORD_RE.search(message):
        return ()
    return tuple(dict.fromkeys(numeric))


def _semantic_ordinal_hint_indices(text: str) -> tuple[int, ...]:
    """Interpret only an LLM-labelled, explicit positional phrase.

    Cardinal words are intentionally not global ordinal anchors: they are too
    easily confused with quantities.  They become safe here only when the
    semantic candidate has labelled the exact current-turn span as an ordinal
    and the span itself says "вариант"/"позиция"/"товар".
    """

    indices = list(ordinal_indices(text))
    indices.extend(_numeric_comparison_ordinals(text))
    if not indices and _POSITION_WORD_RE.search(text):
        indices.extend(
            index
            for pattern, index in _CARDINAL_POSITION_ORDINALS
            if pattern.search(text)
        )
    return tuple(dict.fromkeys(indices))


def _visible_sku_matches(
    message: str,
    scope_skus: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_message = _normalised_identifier(message)
    matches: list[str] = []
    for sku in scope_skus:
        token = _normalised_identifier(sku)
        if len(token) >= 4 and token in normalized_message:
            matches.append(sku)
    return tuple(matches)


def _named_visible_sku(
    candidate: str,
    visible_cards: Iterable[object],
) -> str | None:
    """Resolve a semantic title hint only when it uniquely names one card."""

    words = {
        word
        for word in re.findall(r"[^\W_]+", _normalized(candidate), flags=re.UNICODE)
        if len(word) >= 3 and word not in _REFERENCE_NAME_STOPWORDS
    }
    if len(words) < 2:
        return None
    matches = []
    for card in visible_cards:
        name = _normalized(getattr(card, "name", ""))
        card_words = set(re.findall(r"[^\W_]+", name, flags=re.UNICODE))
        if words <= card_words:
            matches.append(str(getattr(card, "sku", "")))
    return matches[0] if len(matches) == 1 and matches[0] else None


def _resolve_comparison_reference_set(
    original_utterance: str,
    visible_scope,
    visible_cards: Iterable[object],
    *,
    semantic_references: Iterable[object] = (),
) -> _ComparisonReferenceResolution:
    """Resolve comparison subjects against one frozen visible scope only."""

    semantic = _semantic_reference_candidates(semantic_references, original_utterance)
    ordinal_values = list(ordinal_indices(original_utterance))
    ordinal_values.extend(_numeric_comparison_ordinals(original_utterance))
    semantic_explicit = False
    for kind, text, hint, _evidence in semantic:
        if kind == "ordinal":
            ordinal_values.extend(_semantic_ordinal_hint_indices(text))
            semantic_explicit = True
        elif kind in {"exact_sku", "partial_sku", "named_product", "deictic", "current_focus"}:
            semantic_explicit = True
    ordinal_values = list(dict.fromkeys(ordinal_values))

    references: list[ComparisonProductReference] = []
    reason_codes: list[str] = []
    explicit_request = bool(ordinal_values or semantic_explicit)
    for ordinal in ordinal_values:
        resolved = visible_scope.ordinal(ordinal)
        if resolved.resolved:
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.ORDINAL,
                    raw=resolved.raw,
                    canonical_sku=resolved.canonical_sku,
                    evidence=original_utterance,
                    reason_code=resolved.reason_code,
                )
            )
        else:
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.UNRESOLVED,
                    raw=resolved.raw,
                    evidence=original_utterance,
                    reason_code=resolved.reason_code,
                )
            )
            reason_codes.append(resolved.reason_code)

    for sku in _visible_sku_matches(original_utterance, visible_scope.ordered_skus):
        references.append(
            ComparisonProductReference(
                kind=ComparisonReferenceKind.EXPLICIT_VISIBLE_SKU,
                raw=sku,
                canonical_sku=sku,
                evidence=sku,
                reason_code="exact_or_unique_partial_sku_in_customer_visible_v2_scope",
            )
        )
        explicit_request = True

    for kind, text, hint, evidence in semantic:
        if kind in {"exact_sku", "partial_sku"}:
            # ``text`` is required to be a source span.  A model-produced
            # target hint may describe an item, but is never allowed to select
            # a product on its own.
            token = text
            matches = tuple(
                sku
                for sku in visible_scope.ordered_skus
                if _normalised_identifier(token) == _normalised_identifier(sku)
                or (
                    len(_normalised_identifier(token)) >= 4
                    and _normalised_identifier(sku).startswith(_normalised_identifier(token))
                )
            )
            if len(matches) == 1:
                references.append(
                    ComparisonProductReference(
                        kind=ComparisonReferenceKind.EXPLICIT_VISIBLE_SKU,
                        raw=token,
                        canonical_sku=matches[0],
                        evidence=evidence,
                        reason_code="semantic_sku_reference_validated_in_customer_visible_v2_scope",
                    )
                )
            else:
                references.append(
                    ComparisonProductReference(
                        kind=ComparisonReferenceKind.UNRESOLVED,
                        raw=token,
                        evidence=evidence,
                        reason_code="semantic_sku_reference_not_unique_or_outside_customer_visible_v2_scope",
                    )
                )
                reason_codes.append(references[-1].reason_code)
        elif kind == "named_product":
            sku = _named_visible_sku(text, visible_cards)
            if sku is not None and sku in visible_scope.ordered_skus:
                references.append(
                    ComparisonProductReference(
                        kind=ComparisonReferenceKind.NAMED_VISIBLE_PRODUCT,
                        raw=text,
                        canonical_sku=sku,
                        evidence=evidence,
                        reason_code="semantic_named_reference_validated_in_customer_visible_v2_scope",
                    )
                )
            else:
                references.append(
                    ComparisonProductReference(
                        kind=ComparisonReferenceKind.UNRESOLVED,
                        raw=text,
                        evidence=evidence,
                        reason_code="semantic_named_reference_not_unique_or_outside_customer_visible_v2_scope",
                    )
                )
                reason_codes.append(references[-1].reason_code)

    asks_deictic = any(kind in {"deictic", "current_focus"} for kind, *_rest in semantic)
    asks_deictic = asks_deictic or bool(re.search(r"(?iu)\b(?:этот|эта|этой|эту|его|ее)\b", original_utterance))
    if asks_deictic:
        explicit_request = True
        focus = visible_scope.current_focus()
        if focus.resolved:
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.CURRENT_FOCUS,
                    raw=focus.raw,
                    canonical_sku=focus.canonical_sku,
                    evidence="этот",
                    reason_code=focus.reason_code,
                )
            )
        else:
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.UNRESOLVED,
                    raw="этот",
                    evidence="этот",
                    reason_code=focus.reason_code,
                )
            )
            reason_codes.append(focus.reason_code)

    deduplicated: list[ComparisonProductReference] = []
    seen_skus: set[str] = set()
    for reference in references:
        if reference.canonical_sku:
            if reference.canonical_sku in seen_skus:
                continue
            seen_skus.add(reference.canonical_sku)
        deduplicated.append(reference)
    ordered_skus = tuple(
        item.canonical_sku for item in deduplicated if item.canonical_sku
    )
    return _ComparisonReferenceResolution(
        references=tuple(deduplicated),
        ordered_skus=ordered_skus,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        explicit_request=explicit_request,
    )


def _resolve_explicit_catalog_pair(
    original_utterance: str,
    source_snapshot: AnswerSourceSnapshot | None,
) -> _ComparisonReferenceResolution:
    """Resolve two or more fully named feed models without creating a scope.

    This is a comparison-only read seam.  It neither searches by similarity
    nor writes the named products into customer-visible Selection state.
    """

    if source_snapshot is None:
        return _ComparisonReferenceResolution()
    resolutions = resolve_strict_named_catalog_products(
        original_utterance,
        source_snapshot.products,
    )
    if not resolutions:
        return _ComparisonReferenceResolution()
    references: list[ComparisonProductReference] = []
    reasons: list[str] = []
    for item in resolutions:
        if (
            item.status == NamedProductResolutionStatus.EXACT
            and item.canonical_sku is not None
        ):
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.EXPLICIT_CATALOG_PRODUCT,
                    raw=item.raw,
                    canonical_sku=item.canonical_sku,
                    evidence=item.raw,
                    reason_code=item.reason_code,
                )
            )
        else:
            references.append(
                ComparisonProductReference(
                    kind=ComparisonReferenceKind.UNRESOLVED,
                    raw=item.raw,
                    evidence=item.raw,
                    reason_code=item.reason_code,
                )
            )
            reasons.append(item.reason_code)
    ordered = tuple(
        dict.fromkeys(
            item.canonical_sku
            for item in references
            if item.canonical_sku is not None
        )
    )
    return _ComparisonReferenceResolution(
        references=tuple(references),
        ordered_skus=ordered,
        reason_codes=tuple(dict.fromkeys(reasons)),
        explicit_request=True,
    )


def build_comparison_request(
    outcome: DialogueV2Outcome,
    session: SessionState,
    *,
    original_utterance: str,
    semantic_references: Iterable[object] = (),
    source_snapshot: AnswerSourceSnapshot | None = None,
) -> ComparisonRequest | None:
    """Project a typed COMPARE action into one source-gated read scope."""

    task = _comparison_task(outcome)
    if task is None:
        return None
    # A legacy list may be read by Shadow for diagnostics, but has no stored
    # revision / selection identity and therefore can never pass V2 delivery.
    visible_scope = customer_visible_v2_scope(session)
    reference_set = _resolve_explicit_catalog_pair(
        original_utterance,
        source_snapshot,
    )
    explicit_pair = bool(
        len(reference_set.ordered_skus) >= 2
        and not reference_set.reason_codes
        and all(
            item.kind == ComparisonReferenceKind.EXPLICIT_CATALOG_PRODUCT
            for item in reference_set.references
        )
    )
    if explicit_pair:
        ordered_skus = reference_set.ordered_skus
        origin = "explicit_catalog_pair"
        selection_id = None
        revision = source_snapshot.source_revision if source_snapshot else None
    elif visible_scope.is_valid:
        reference_set = _resolve_comparison_reference_set(
            original_utterance,
            visible_scope,
            session.v2_last_products,
            semantic_references=semantic_references,
        )
        # A fully unresolved explicit pair must never silently broaden to all
        # cards.  A generic «сравните их» is the only form that owns the whole
        # visible scope.
        ordered_skus = (
            reference_set.ordered_skus
            if reference_set.explicit_request
            else visible_scope.ordered_skus
        )
        origin: str = "v2_delivered"
        selection_id = visible_scope.selection_id
        revision = visible_scope.source_revision
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
        product_references=reference_set.references,
        reference_reason_codes=reference_set.reason_codes,
        requested_predicates=_requested_predicates(original_utterance),
        criterion=_criterion(original_utterance),
        needs_deciding_criterion=_needs_deciding_criterion(original_utterance),
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
    document: str | None = None,
    section: str | None = None,
    quote: str | None = None,
    verifier_status: str | None = None,
    document_scope: tuple[str, ...] = (),
) -> ComparisonSourceReference:
    return ComparisonSourceReference(
        source_ref_id=_stable_id(
            "comparison_source",
            product.sku,
            predicate,
            kind.value,
            field_name,
            revision,
            document,
        ),
        sku=product.sku,
        predicate=predicate,
        source_kind=kind,
        source_revision=revision,
        field_name=field_name,
        source_field=source_field,
        raw_value=raw_value,
        document=document,
        section=section,
        quote=quote,
        verifier_status=verifier_status,
        document_scope=document_scope,
    )


def _exact_fact(product: CatalogAnswerProduct, predicate: str):
    catalog_predicate = (
        ProductContractRegistry().canonical_fact_name(
            product.product_kind,
            predicate,
        )
        or predicate
    )
    accepted_names = {predicate, catalog_predicate}
    values = tuple(item for item in product.facts if item.name in accepted_names)
    issues = tuple(item for item in product.fact_issues if item.name in accepted_names)
    distinct = {(str(item.value), item.unit) for item in values}
    if issues or len(distinct) != 1:
        return None
    return values[0] if values else None


def _passport_quote_contains_value(
    quote: str,
    value: object,
    unit: str | None,
) -> bool:
    """A bounded consistency check for a verifier-approved passport quote."""

    normalized_quote = _normalized(quote).replace(" ", "")
    # For scalar values, prevent ``8`` from matching an unrelated ``18`` and
    # require the registered canonical unit to appear alongside the number.
    if isinstance(value, (int, float)) and unit:
        numeric_value = f"{float(value):g}"
        numeric = re.escape(numeric_value).replace(r"\.", r"[.,]")
        normalized_unit = _normalized(unit).replace("°", "")
        # A passport may spell a registered unit in full.  Keep this bounded
        # to the unit family rather than accepting an unbounded substring.
        unit_token = {
            "л": r"(?:л|литр\w*)",
            "мм": r"(?:мм|миллиметр\w*)",
            "м": r"(?:м|метр\w*)",
            "бар": r"(?:бар\w*)",
            "квт": r"(?:квт|киловатт\w*)",
        }.get(normalized_unit, re.escape(normalized_unit))
        return bool(
            re.search(
                rf"(?<!\d){numeric}(?!\d){unit_token}",
                normalized_quote.replace("°", ""),
            )
        )
    normalized_value = _normalized(value).replace(" ", "")
    variants = {
        normalized_value,
        normalized_value.replace("×", "x"),
        normalized_value.replace("m", "м").replace("×", "х"),
    }
    if any(variant and variant in normalized_quote for variant in variants):
        return True
    return False


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
            passport_fact = fact.provenance.source == "passport"
            source = _source_ref(
                product,
                predicate,
                (
                    ComparisonSourceKind.PASSPORT_DOCUMENT_EXACT
                    if passport_fact
                    else ComparisonSourceKind.CATALOG_ATTRIBUTE
                ),
                revision,
                field_name=predicate,
                source_field=fact.provenance.source_field,
                raw_value=str(fact.value),
                document=fact.provenance.source_document if passport_fact else None,
                section=fact.provenance.source_section if passport_fact else None,
                quote=fact.provenance.raw_value if passport_fact else None,
                verifier_status="document_table_exact" if passport_fact else "catalog_snapshot_exact",
                document_scope=product.document_scope if passport_fact else (),
            )
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


def _evidence_is_source_conflict(reason_code: str, verifier_status: str) -> bool:
    """Map an existing ProductFact conflict into Compare without guessing."""

    return "conflict" in reason_code or verifier_status == "source_conflict"


def _passport_fallback_for_requested_dimension(
    products: tuple[CatalogAnswerProduct, ...],
    dimension: ComparisonDimension,
    sources: tuple[ComparisonSourceReference, ...],
    *,
    predicate: str,
    revision: str,
    evidence_service: ProductFactEvidenceService,
) -> tuple[
    ComparisonDimension,
    tuple[ComparisonSourceReference, ...],
    tuple[str, ...],
]:
    """Fill only snapshot gaps with exact, checked passport evidence.

    Generic comparison must stay a cheap snapshot operation.  This fallback is
    deliberately reached only for an explicitly requested predicate which is
    absent for one or more already shown cards.  Each lookup is exact-SKU and
    goes through the existing ProductFact evidence, document-scope and
    verifier gates; it never searches the catalog or lets an LLM choose a
    product.
    """

    values_by_sku = {value.sku: value for value in dimension.values}
    refs = list(sources)
    unresolved = list(dimension.missing_skus)
    source_conflicts: list[str] = []
    for sku in tuple(dimension.missing_skus):
        product = next((item for item in products if item.sku == sku), None)
        if product is None:
            continue
        evidence = evidence_service.evaluate_exact_product(sku=sku, predicate=predicate)
        if _evidence_is_source_conflict(evidence.reason_code, evidence.verifier_status):
            source_conflicts.append(sku)
            continue
        if (
            evidence.status != ProductFactStatus.ANSWERED
            or evidence.value is None
            or evidence.source_kind
            not in {"passport_document_exact", "passport_and_catalog_card"}
            or not evidence.document
            or evidence.document not in evidence.document_scope
            or not evidence.quote
            or evidence.verifier_status not in {"accepted", "document_table_exact", "document_text_exact"}
        ):
            continue
        source_kind = (
            ComparisonSourceKind.PASSPORT_AND_CATALOG_CARD
            if evidence.source_kind == "passport_and_catalog_card"
            else ComparisonSourceKind.PASSPORT_DOCUMENT_EXACT
        )
        source = _source_ref(
            product,
            predicate,
            source_kind,
            revision,
            field_name=predicate,
            source_field=predicate,
            raw_value=str(evidence.value),
            document=evidence.document,
            section=evidence.section,
            quote=evidence.quote,
            verifier_status=evidence.verifier_status,
            document_scope=evidence.document_scope,
        )
        refs.append(source)
        values_by_sku[sku] = ComparisonValue(
            sku=sku,
            predicate=predicate,
            value=evidence.value,
            unit=evidence.unit,
            source_ref_ids=(source.source_ref_id,),
        )
        unresolved.remove(sku)

    values = tuple(
        values_by_sku[product.sku]
        for product in products
        if product.sku in values_by_sku
    )
    return (
        ComparisonDimension(
            predicate=dimension.predicate,
            label=dimension.label,
            values=values,
            missing_skus=tuple(unresolved),
            missing_reason_codes=(
                ("comparison_passport_evidence_not_confirmed",)
                if unresolved
                else ()
            ),
        ),
        tuple(refs),
        tuple(source_conflicts),
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
    product_fact_evidence: ProductFactEvidenceService | None = None,
) -> ComparisonResult:
    """Compare only the current delivered list, never a global catalogue set."""

    if request.scope_origin == "none":
        return ComparisonResult(status=ComparisonResultStatus.NEED_CLARIFICATION, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, source_revision=request.source_revision, reason_codes=("comparison_scope_missing",))
    if request.scope_origin not in {"v2_delivered", "explicit_catalog_pair"}:
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_scope_not_v2_versioned",))
    if request.scope_origin == "v2_delivered" and (
        not request.selection_id or not request.source_revision
    ):
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_selection_identity_missing",))
    if request.scope_origin == "explicit_catalog_pair" and (
        request.selection_id is not None
        or not request.source_revision
        or set(request.ordered_skus)
        != {
            item.canonical_sku
            for item in request.product_references
            if item.kind == ComparisonReferenceKind.EXPLICIT_CATALOG_PRODUCT
        }
    ):
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_explicit_catalog_pair_identity_failed",))
    if request.source_revision != source_snapshot.source_revision:
        return ComparisonResult(status=ComparisonResultStatus.REJECTED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, source_revision=request.source_revision, reason_codes=("comparison_source_revision_stale",))
    if len(request.ordered_skus) < 2:
        return ComparisonResult(
            status=ComparisonResultStatus.NEED_CLARIFICATION,
            task_id=request.task_id,
            goal_id=request.goal_id,
            selection_id=request.selection_id,
            compared_skus=request.ordered_skus,
            source_revision=request.source_revision,
            reason_codes=tuple(
                dict.fromkeys(
                    (
                        "comparison_requires_two_visible_cards",
                        *request.reference_reason_codes,
                    )
                )
            ),
        )

    cards_by_sku = {str(getattr(card, "sku", "")): card for card in visible_cards}
    products: list[CatalogAnswerProduct] = []
    for sku in request.ordered_skus:
        product = source_snapshot.product(sku)
        card = cards_by_sku.get(sku)
        if product is None or (
            request.scope_origin == "v2_delivered"
            and (card is None or not _is_same_visible_card(product, card))
        ):
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
    source_conflict_skus: list[str] = []
    for predicate in predicates:
        dimension, refs = _dimension_from_products(typed_products, predicate, source_snapshot.source_revision)
        if (
            predicate in request.requested_predicates
            and dimension.missing_skus
            and product_fact_evidence is not None
        ):
            dimension, refs, conflicts = _passport_fallback_for_requested_dimension(
                typed_products,
                dimension,
                refs,
                predicate=predicate,
                revision=source_snapshot.source_revision,
                evidence_service=product_fact_evidence,
            )
            source_conflict_skus.extend(conflicts)
        sources.extend(refs)
        if dimension.missing_skus:
            missing.append(predicate)
        if _has_proven_difference(dimension):
            dimensions.append(dimension)
        elif predicate in request.requested_predicates:
            # An explicitly requested coordinate remains useful even when the
            # two proved values are equal (same price/stock is itself an
            # answer).  The comparison gate below still requires at least one
            # genuine difference before the overall result can be COMPARED.
            dimensions.append(dimension)

    if source_conflict_skus:
        return ComparisonResult(
            status=ComparisonResultStatus.SOURCE_CONFLICT,
            task_id=request.task_id,
            goal_id=request.goal_id,
            selection_id=request.selection_id,
            compared_skus=request.ordered_skus,
            requested_predicates=request.requested_predicates,
            dimensions=tuple(dimensions),
            sources=tuple(sources),
            missing_data=tuple(dict.fromkeys(missing)),
            source_revision=source_snapshot.source_revision,
            reason_codes=tuple(
                ["comparison_passport_source_conflict"]
                + [f"comparison_passport_source_conflict:{sku}" for sku in source_conflict_skus]
            ),
        )

    # A customer who explicitly asked about a technical predicate must not get
    # a seemingly complete comparison based only on price or availability when
    # that predicate could not be proven for every selected product.
    explicit_missing = tuple(
        dict.fromkeys(
            item.predicate
            for item in dimensions
            if item.predicate in request.requested_predicates and item.missing_skus
        )
    )
    proved = tuple(item for item in dimensions if _has_proven_difference(item))
    proved_requested = tuple(
        item for item in proved if item.predicate in request.requested_predicates
    )
    if explicit_missing and not proved_requested:
        return ComparisonResult(
            status=ComparisonResultStatus.NOT_COMPARABLE,
            task_id=request.task_id,
            goal_id=request.goal_id,
            selection_id=request.selection_id,
            compared_skus=request.ordered_skus,
            requested_predicates=request.requested_predicates,
            dimensions=tuple(dimensions),
            sources=tuple(sources),
            missing_data=tuple(dict.fromkeys(missing)),
            source_revision=source_snapshot.source_revision,
            reason_codes=("comparison_explicit_predicate_insufficient_evidence",),
        )

    if not proved:
        return ComparisonResult(status=ComparisonResultStatus.NOT_COMPARABLE, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, requested_predicates=request.requested_predicates, dimensions=tuple(dimensions), sources=tuple(sources), missing_data=tuple(dict.fromkeys(missing)), source_revision=source_snapshot.source_revision, reason_codes=("comparison_no_proven_difference",))

    recommendation = None
    if request.criterion == ComparisonCriterion.LOWEST_PRICE:
        price = next((item for item in proved if item.predicate == "price"), None)
        if price is not None and not price.missing_skus:
            lowest = min(price.values, key=lambda item: float(item.value))
            if sum(float(item.value) == float(lowest.value) for item in price.values) == 1:
                recommendation = ComparisonRecommendation(sku=lowest.sku, criterion=ComparisonCriterion.LOWEST_PRICE, source_ref_ids=lowest.source_ref_ids, reason_code="lowest_confirmed_price")

    generic_request = (
        not request.requested_predicates
        and request.criterion is None
        and request.needs_deciding_criterion
    )
    question = None
    if generic_request:
        decision_dimensions = tuple(
            item for item in proved if item.predicate not in _NON_DECIDING_PREDICATES
        )
        if decision_dimensions:
            labels = ", ".join(item.label for item in decision_dimensions[:3])
            question = f"Какой критерий для вас решающий: {labels}?"
    return ComparisonResult(status=ComparisonResultStatus.COMPARED, task_id=request.task_id, goal_id=request.goal_id, selection_id=request.selection_id, compared_skus=request.ordered_skus, requested_predicates=request.requested_predicates, dimensions=tuple(dimensions), sources=tuple(sources), missing_data=tuple(dict.fromkeys(missing)), recommendation=recommendation, deciding_question=question, source_revision=source_snapshot.source_revision, reason_codes=(("comparison_from_explicit_catalog_pair",) if request.scope_origin == "explicit_catalog_pair" else ("comparison_from_customer_visible_v2_scope",)) + (("comparison_partial_requested_predicates",) if explicit_missing else ()))


def validate_comparison_result(
    request: ComparisonRequest,
    result: ComparisonResult,
    source_snapshot: AnswerSourceSnapshot,
) -> ComparisonResult:
    """Fail closed on scope, source, predicate, or recommendation drift."""

    reasons = list(result.reason_codes)
    passed = result.status in {
        ComparisonResultStatus.COMPARED,
        ComparisonResultStatus.NEED_CLARIFICATION,
        ComparisonResultStatus.NOT_COMPARABLE,
        ComparisonResultStatus.SOURCE_CONFLICT,
    }
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
                    elif reference.source_kind in {
                        ComparisonSourceKind.PASSPORT_DOCUMENT_EXACT,
                        ComparisonSourceKind.PASSPORT_AND_CATALOG_CARD,
                    }:
                        passport_scope_matches = bool(
                            reference.document
                            and reference.document in reference.document_scope
                            and reference.document in product.document_scope
                            and reference.quote
                            and reference.verifier_status
                            in {"accepted", "document_table_exact", "document_text_exact"}
                        )
                        snapshot_fact = _exact_fact(product, value.predicate)
                        # A passport fact pre-projected during normalization can
                        # be independently rechecked against the frozen snapshot.
                        # A dynamically retrieved exact document fact has already
                        # passed ProductFactEvidenceService; here the comparison
                        # gate verifies its product document scope, accepted
                        # verifier status and cited value shape.
                        source_value_matches = passport_scope_matches and (
                            (
                                snapshot_fact is not None
                                and snapshot_fact.provenance.source == "passport"
                                and snapshot_fact.value == value.value
                                and snapshot_fact.unit == value.unit
                                and snapshot_fact.provenance.source_document
                                == reference.document
                            )
                            or reference.verifier_status == "document_text_exact"
                            or _passport_quote_contains_value(
                                reference.quote or "",
                                value.value,
                                value.unit,
                            )
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
