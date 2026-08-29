"""Deterministic Compatibility request building, evidence and outcome gate.

The service deliberately composes existing V2 source snapshots and the accepted
ProductFact evidence adapter.  It never runs a catalogue search, invokes the
Legacy response composer, or lets an LLM choose a mechanical verdict.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.contracts import ProductKind
from app.component_evidence import builtin_part_evidence
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import Product, ProductFocusState, SessionState
from app.product_fact_evidence import (
    ProductFactEvidenceService,
    ProductFactStatus,
)
from app.sku_resolution import SkuResolutionStatus, extract_explicit_sku_tokens, resolve_catalog_sku

from .contracts import (
    CompatibilityProductReference,
    CompatibilityReferenceKind,
    CompatibilityRelationKind,
    CompatibilityRequest,
    CompatibilityResult,
    CompatibilityResultStatus,
    CompatibilityScopeOrigin,
    InterfaceFact,
    InterfaceSourceKind,
)


_ORDINALS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("перв", "1-й", "1я", "первую", "первый"), 0),
    (("втор", "2-й", "2я", "вторую", "второй"), 1),
    (("трет", "3-й", "3я", "третью", "третий"), 2),
    (("четверт", "4-й", "4я"), 3),
    (("пят", "5-й", "5я"), 4),
)
_DEICTIC_MARKERS = ("этот", "эта", "этой", "того", "тому", "нему", "ней")
_SEWER_KINDS = frozenset(
    {
        ProductKind.SEWER_PIPE,
        ProductKind.SEWER_ELBOW,
        ProductKind.TEE,
        ProductKind.COUPLING,
        ProductKind.REDUCING_COUPLING,
    }
)
_PUMP_KINDS = frozenset(
    {
        ProductKind.PUMP,
        ProductKind.CIRCULATION_PUMP,
        ProductKind.DHW_CIRCULATION_PUMP,
        ProductKind.BOOSTER_PUMP,
        ProductKind.BOREHOLE_PUMP,
        ProductKind.WELL_PUMP,
        ProductKind.DRAINAGE_PUMP,
        ProductKind.SEWAGE_PUMP,
        ProductKind.PUMP_STATION,
    }
)
_BOILER_KINDS = frozenset(
    {ProductKind.BOILER, ProductKind.GAS_BOILER, ProductKind.ELECTRIC_BOILER}
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _ordinal_indices(message: str) -> tuple[int, ...]:
    text = _normalise(message)
    found: list[int] = []
    for aliases, index in _ORDINALS:
        if any(alias in text for alias in aliases) and index not in found:
            found.append(index)
    return tuple(found)


def _reference(
    kind: CompatibilityReferenceKind,
    *,
    raw: str = "",
    sku: str | None = None,
    candidate_skus: tuple[str, ...] = (),
    reason: str,
) -> CompatibilityProductReference:
    return CompatibilityProductReference(
        kind=kind,
        raw=raw,
        canonical_sku=sku,
        candidate_skus=candidate_skus or ((sku,) if sku else ()),
        reason_code=reason,
    )


def _unresolved(reason: str, *, raw: str = "", candidates: tuple[str, ...] = ()) -> CompatibilityProductReference:
    return _reference(
        CompatibilityReferenceKind.UNRESOLVED,
        raw=raw,
        candidate_skus=candidates,
        reason=reason,
    )


def _explicit_sku_references(
    message: str,
    products: Iterable[object],
) -> tuple[CompatibilityProductReference, ...]:
    result: list[CompatibilityProductReference] = []
    for token in dict.fromkeys(extract_explicit_sku_tokens(message)):
        resolved = resolve_catalog_sku(token, products)
        if resolved.status in {SkuResolutionStatus.EXACT, SkuResolutionStatus.UNIQUE_PREFIX}:
            result.append(
                _reference(
                    (
                        CompatibilityReferenceKind.EXACT_SKU
                        if resolved.status == SkuResolutionStatus.EXACT
                        else CompatibilityReferenceKind.PARTIAL_SKU
                    ),
                    raw=token,
                    sku=resolved.canonical_sku,
                    candidate_skus=tuple(item.sku for item in resolved.candidates),
                    reason=(
                        "explicit_exact_sku"
                        if resolved.status == SkuResolutionStatus.EXACT
                        else "explicit_unique_partial_sku"
                    ),
                )
            )
        elif resolved.status == SkuResolutionStatus.AMBIGUOUS_PREFIX:
            result.append(
                _unresolved(
                    "ambiguous_partial_sku",
                    raw=token,
                    candidates=tuple(item.sku for item in resolved.candidates),
                )
            )
    return tuple(result)


def _strict_named_skus(message: str, snapshot: AnswerSourceSnapshot) -> tuple[str, ...]:
    """Resolve only a full catalogue title stated in the current utterance.

    This intentionally avoids fuzzy search.  A model/brand fragment remains
    ambiguous unless the existing SKU resolver handled it as an article.
    """

    text = _normalise(message)
    matches = [
        product.sku
        for product in snapshot.products
        if len(_normalise(product.name)) >= 12
        and _normalise(product.name) in text
    ]
    return tuple(dict.fromkeys(matches))


def _relation(
    left: CompatibilityProductReference,
    right: CompatibilityProductReference,
    snapshot: AnswerSourceSnapshot,
) -> CompatibilityRelationKind:
    if not left.canonical_sku or not right.canonical_sku:
        return CompatibilityRelationKind.UNKNOWN
    first, second = snapshot.product(left.canonical_sku), snapshot.product(right.canonical_sku)
    if first is None or second is None:
        return CompatibilityRelationKind.UNKNOWN
    kinds = {first.product_kind, second.product_kind}
    if kinds == {ProductKind.THERMOSTATIC_HEAD, ProductKind.RADIATOR_VALVE}:
        return CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE
    if first.product_kind in _SEWER_KINDS and second.product_kind in _SEWER_KINDS:
        return CompatibilityRelationKind.SEWER_CONNECTION
    if (
        first.product_kind in _PUMP_KINDS and second.product_kind in _BOILER_KINDS
    ) or (
        second.product_kind in _PUMP_KINDS and first.product_kind in _BOILER_KINDS
    ):
        return CompatibilityRelationKind.PUMP_TO_BOILER
    if all(
        _exact_card_fact(product, "connection_size")[0] is not None
        or _exact_card_fact(product, "connection_pattern")[0] is not None
        for product in (first, second)
    ):
        return CompatibilityRelationKind.THREADED_CONNECTION
    return CompatibilityRelationKind.UNKNOWN


def _compatibility_task(outcome: DialogueV2Outcome):
    plan = outcome.next_action_plan
    if plan is None:
        return None
    actions = tuple(item for item in (plan.primary, plan.secondary) if item is not None)
    action = next(
        (item for item in actions if item.kind == NextActionKind.CHECK_COMPATIBILITY),
        None,
    )
    if action is None:
        return None
    task = next((item for item in outcome.state_after.tasks if item.task_id == action.task_id), None)
    return task if task is not None and task.act == TaskAct.COMPATIBILITY else None


def build_compatibility_request(
    outcome: DialogueV2Outcome,
    session: SessionState,
    snapshot: AnswerSourceSnapshot,
    *,
    original_utterance: str,
) -> CompatibilityRequest | None:
    """Project a typed Compatibility task into two deterministic references."""

    task = _compatibility_task(outcome)
    if task is None:
        return None
    explicit = list(_explicit_sku_references(original_utterance, snapshot.products))
    unresolved = next((item for item in explicit if item.kind == CompatibilityReferenceKind.UNRESOLVED), None)
    if unresolved is not None:
        left, right = unresolved, _unresolved("compatibility_second_product_missing")
    else:
        resolved: list[CompatibilityProductReference] = []
        for item in explicit:
            if item.canonical_sku and item.canonical_sku not in {x.canonical_sku for x in resolved}:
                resolved.append(item)
        if len(resolved) < 2:
            for sku in _strict_named_skus(original_utterance, snapshot):
                if sku not in {item.canonical_sku for item in resolved}:
                    resolved.append(
                        _reference(
                            CompatibilityReferenceKind.NAMED_PRODUCT,
                            raw=snapshot.product(sku).name if snapshot.product(sku) else sku,
                            sku=sku,
                            reason="strict_catalogue_title_match",
                        )
                    )
        visible = tuple(card.sku for card in session.v2_last_products)
        visible_valid = bool(
            visible
            and session.v2_selection_id
            and session.v2_source_revision == snapshot.source_revision
        )
        for ordinal in _ordinal_indices(original_utterance):
            if len(resolved) >= 2:
                break
            if visible_valid and 0 <= ordinal < len(visible):
                sku = visible[ordinal]
                if sku not in {item.canonical_sku for item in resolved}:
                    resolved.append(
                        _reference(
                            CompatibilityReferenceKind.ORDINAL,
                            raw=str(ordinal + 1),
                            sku=sku,
                            reason="ordinal_in_customer_visible_v2_scope",
                        )
                    )
            elif visible_valid:
                resolved.append(_unresolved("ordinal_outside_customer_visible_v2_scope", raw=str(ordinal + 1)))
                break
        if len(resolved) < 2 and visible_valid:
            text = _normalise(original_utterance)
            if not resolved and len(visible) == 2:
                resolved.extend(
                    _reference(
                        CompatibilityReferenceKind.CURRENT_VISIBLE_SCOPE,
                        raw=sku,
                        sku=sku,
                        reason="two_customer_visible_v2_cards",
                    )
                    for sku in visible
                )
            elif len(resolved) == 1 and any(marker in text for marker in _DEICTIC_MARKERS):
                focus = session.product_focus.sku if session.product_focus else None
                candidates = tuple(sku for sku in visible if sku != resolved[0].canonical_sku)
                if focus in candidates:
                    candidates = (focus, *tuple(sku for sku in candidates if sku != focus))
                if len(candidates) == 1:
                    resolved.append(
                        _reference(
                            CompatibilityReferenceKind.CURRENT_FOCUS,
                            raw="этот",
                            sku=candidates[0],
                            reason="deictic_in_customer_visible_v2_scope",
                        )
                    )
        if len(resolved) >= 2:
            left, right = resolved[0], resolved[1]
        elif resolved:
            left, right = resolved[0], _unresolved("compatibility_second_product_missing")
        else:
            left, right = _unresolved("compatibility_product_references_missing"), _unresolved("compatibility_product_references_missing")

    both_visible = bool(
        session.v2_selection_id
        and session.v2_source_revision == snapshot.source_revision
        and left.canonical_sku in {item.sku for item in session.v2_last_products}
        and right.canonical_sku in {item.sku for item in session.v2_last_products}
    )
    scope_origin = (
        CompatibilityScopeOrigin.V2_DELIVERED
        if both_visible
        else (
            CompatibilityScopeOrigin.EXPLICIT_PRODUCTS
            if left.canonical_sku and right.canonical_sku
            else CompatibilityScopeOrigin.NONE
        )
    )
    return CompatibilityRequest(
        task_id=task.task_id,
        goal_id=task.target_goal_id,
        original_utterance=original_utterance,
        left=left,
        right=right,
        relation=_relation(left, right, snapshot),
        selection_id=(session.v2_selection_id if both_visible else None),
        ordered_skus=(tuple(card.sku for card in session.v2_last_products) if both_visible else ()),
        source_revision=snapshot.source_revision,
        scope_origin=scope_origin,
    )


def _exact_card_fact(
    product: CatalogAnswerProduct,
    predicate: str,
) -> tuple[object | None, bool]:
    facts = tuple(item for item in product.facts if item.name == predicate)
    issues = tuple(item for item in product.fact_issues if item.name == predicate)
    values = {(str(item.value), item.unit) for item in facts}
    if issues or len(values) > 1:
        return None, True
    return (facts[0] if len(facts) == 1 else None), False


class InterfaceFactService:
    """Read-only adapter for catalogue and accepted passport interface facts."""

    def __init__(
        self,
        snapshot: AnswerSourceSnapshot,
        *,
        product_fact_evidence: ProductFactEvidenceService | None = None,
        products: Iterable[Product] = (),
    ) -> None:
        self.snapshot = snapshot
        self.product_fact_evidence = product_fact_evidence
        self.products_by_sku = {product.sku: product for product in products}

    def fact(self, sku: str, predicate: str) -> tuple[InterfaceFact | None, bool]:
        """Return a proven fact and whether its source conflicts internally."""

        product = self.snapshot.product(sku)
        if product is None:
            return None, False
        if predicate == "integrated_circulation_pump":
            return self._integrated_circulation_pump_fact(sku, product)
        card_fact, conflict = _exact_card_fact(product, predicate)
        if conflict:
            return None, True
        if card_fact is not None:
            return (
                InterfaceFact(
                    sku=sku,
                    predicate=predicate,
                    value=card_fact.value,
                    unit=card_fact.unit,
                    source_kind=InterfaceSourceKind.CATALOG_ATTRIBUTE,
                    source_revision=self.snapshot.source_revision,
                    document="catalogue",
                    section=card_fact.provenance.source_field,
                    excerpt=f"{card_fact.provenance.source_field}: {card_fact.provenance.raw_value}",
                    verifier_status="catalog_card_exact",
                ),
                False,
            )
        if predicate in {"sewer_system_family", "diameter_mm"}:
            identity = self._sewer_identity_fact(product, predicate)
            if identity is not None:
                return identity, False
        if predicate == "control_thread" and self.product_fact_evidence is not None:
            return self._passport_control_thread_fact(sku)
        return None, False

    def _integrated_circulation_pump_fact(
        self,
        sku: str,
        snapshot_product: CatalogAnswerProduct,
    ) -> tuple[InterfaceFact | None, bool]:
        """Read one boiler's built-in pump state without treating absence as no.

        The source snapshot supplies a revision-bound card fact.  When the
        selected product also has a mapped passport/manual, the common Legacy
        reader checks it as a second, source-preserving proof.  An explicit
        disagreement is a source conflict, not a tie-break by text order.
        """

        card_fact, card_conflict = _exact_card_fact(
            snapshot_product, "integrated_circulation_pump"
        )
        if card_conflict:
            return None, True
        card_state = (
            bool(card_fact.value)
            if card_fact is not None and isinstance(card_fact.value, bool)
            else None
        )
        product = self.products_by_sku.get(sku)
        document_evidence = (
            builtin_part_evidence(product, "насос") if product is not None else None
        )
        if document_evidence is not None and document_evidence.source_conflict:
            return None, True
        document_state = document_evidence.state if document_evidence else None
        if (
            card_state is not None
            and document_state is not None
            and card_state != document_state
        ):
            return None, True
        state = document_state if document_state is not None else card_state
        if state is None:
            return None, False
        if document_evidence is not None and document_evidence.source_kind == "passport":
            return (
                InterfaceFact(
                    sku=sku,
                    predicate="integrated_circulation_pump",
                    value=state,
                    source_kind=InterfaceSourceKind.PASSPORT,
                    source_revision=self.snapshot.source_revision,
                    document=document_evidence.document or "attached_product_document",
                    section=document_evidence.section,
                    excerpt=document_evidence.excerpt or "",
                    # The exact attached-document reader is deterministic; it
                    # is stricter than a semantic quote and remains distinct
                    # from the embedding/verifier acceptance mode.
                    verifier_status="document_text_exact",
                ),
                False,
            )
        if card_fact is None:
            return None, False
        return (
            InterfaceFact(
                sku=sku,
                predicate="integrated_circulation_pump",
                value=state,
                unit=card_fact.unit,
                source_kind=InterfaceSourceKind.CATALOG_ATTRIBUTE,
                source_revision=self.snapshot.source_revision,
                document="catalogue",
                section=card_fact.provenance.source_field,
                excerpt=(
                    f"{card_fact.provenance.source_field}: "
                    f"{card_fact.provenance.raw_value}"
                ),
                verifier_status="catalog_card_exact",
            ),
            False,
        )

    def _sewer_identity_fact(
        self,
        product: CatalogAnswerProduct,
        predicate: str,
    ) -> InterfaceFact | None:
        name = product.name
        if predicate == "sewer_system_family":
            match = re.search(r"\b(ht|kg)\w*\b", name, re.IGNORECASE)
            if match is None:
                return None
            value = match.group(1).upper()
        else:
            match = re.search(r"\b(?:ht|kg)\w*\b[^0-9]{0,8}(\d{2,3})(?!\d)", name, re.IGNORECASE)
            if match is None:
                return None
            value = int(match.group(1))
        return InterfaceFact(
            sku=product.sku,
            predicate=predicate,
            value=value,
            unit="mm" if predicate == "diameter_mm" else None,
            source_kind=InterfaceSourceKind.CATALOG_IDENTITY,
            source_revision=self.snapshot.source_revision,
            document="catalogue",
            section="name",
            excerpt=name,
            verifier_status="catalogue_identity_exact",
        )

    def _passport_control_thread_fact(self, sku: str) -> tuple[InterfaceFact | None, bool]:
        assert self.product_fact_evidence is not None
        session = SessionState(
            session_id=f"compatibility:{sku}",
            product_focus=ProductFocusState(sku=sku, origin="compatibility_interface_lookup"),
        )
        evidence = self.product_fact_evidence.evaluate(
            "Какая резьба под термоголовку?",
            session,
            semantic_fact_name="control_thread",
        )
        if (
            evidence is None
            or evidence.status != ProductFactStatus.ANSWERED
            or evidence.request.product_ref.canonical_sku != sku
            or evidence.request.predicate != "thermostatic_head_thread"
            or evidence.value is None
            or not evidence.document
            or not evidence.quote
        ):
            return None, False
        return (
            InterfaceFact(
                sku=sku,
                predicate="control_thread",
                value=str(evidence.value),
                unit=evidence.unit,
                source_kind=InterfaceSourceKind.PASSPORT,
                source_revision=self.snapshot.source_revision,
                document=evidence.document,
                section=evidence.section,
                excerpt=evidence.quote,
                verifier_status=evidence.verifier_status,
            ),
            False,
        )


def _common_result(request: CompatibilityRequest) -> dict[str, object]:
    return {
        "task_id": request.task_id,
        "goal_id": request.goal_id,
        "relation": request.relation,
        "left": request.left,
        "right": request.right,
        "selection_id": request.selection_id,
        "source_revision": request.source_revision,
    }


def _all_facts(
    service: InterfaceFactService,
    request: CompatibilityRequest,
    predicates: tuple[str, ...],
) -> tuple[dict[tuple[str, str], InterfaceFact], bool]:
    facts: dict[tuple[str, str], InterfaceFact] = {}
    conflict = False
    for sku in (request.left.canonical_sku, request.right.canonical_sku):
        if sku is None:
            continue
        for predicate in predicates:
            value, has_conflict = service.fact(sku, predicate)
            conflict = conflict or has_conflict
            if value is not None:
                facts[(sku, predicate)] = value
    return facts, conflict


def _missing(
    request: CompatibilityRequest,
    facts: dict[tuple[str, str], InterfaceFact],
    predicates: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        f"{sku}:{predicate}"
        for sku in (request.left.canonical_sku, request.right.canonical_sku)
        if sku is not None
        for predicate in predicates
        if (sku, predicate) not in facts
    )


def _thread_pattern_mates(left: str, right: str) -> bool:
    expected = {
        "female_female": {"male_male"},
        "male_male": {"female_female"},
        "female_male": {"female_male", "male_female"},
        "male_female": {"female_male", "male_female"},
    }
    return right in expected.get(left, set())


def build_compatibility_result(
    request: CompatibilityRequest,
    snapshot: AnswerSourceSnapshot,
    *,
    interface_facts: InterfaceFactService | None = None,
) -> CompatibilityResult:
    """Apply one narrow deterministic compatibility rule, or fail closed."""

    common = _common_result(request)
    if request.source_revision != snapshot.source_revision:
        return CompatibilityResult(
            status=CompatibilityResultStatus.REJECTED,
            reason_codes=("compatibility_source_revision_stale",),
            **common,
        )
    if not request.left.canonical_sku or not request.right.canonical_sku:
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            missing_predicates=("resolved_left_product", "resolved_right_product"),
            reason_codes=(
                request.left.reason_code,
                request.right.reason_code,
            ),
            **common,
        )
    if request.left.canonical_sku == request.right.canonical_sku:
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            reason_codes=("compatibility_requires_two_distinct_products",),
            **common,
        )
    if any(snapshot.product(sku) is None for sku in (request.left.canonical_sku, request.right.canonical_sku)):
        return CompatibilityResult(
            status=CompatibilityResultStatus.REJECTED,
            reason_codes=("compatibility_sku_missing_from_source_snapshot",),
            **common,
        )

    service = interface_facts or InterfaceFactService(snapshot)
    if request.relation == CompatibilityRelationKind.PUMP_TO_BOILER:
        left_product = snapshot.product(request.left.canonical_sku)
        right_product = snapshot.product(request.right.canonical_sku)
        assert left_product is not None and right_product is not None
        boiler_sku = (
            request.left.canonical_sku
            if left_product.product_kind in _BOILER_KINDS
            else request.right.canonical_sku
        )
        integrated_pump, has_conflict = service.fact(
            boiler_sku, "integrated_circulation_pump"
        )
        if has_conflict:
            return CompatibilityResult(
                status=CompatibilityResultStatus.SOURCE_CONFLICT,
                interface_predicates=("integrated_circulation_pump",),
                facts=(() if integrated_pump is None else (integrated_pump,)),
                reason_codes=("boiler_integrated_pump_source_conflict",),
                **common,
            )
        if integrated_pump is None:
            return CompatibilityResult(
                status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
                interface_predicates=("integrated_circulation_pump",),
                missing_predicates=(f"{boiler_sku}:integrated_circulation_pump",),
                reason_codes=(
                    "boiler_integrated_pump_not_confirmed",
                    "pump_boiler_requires_hydraulic_calculation",
                ),
                **common,
            )
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            interface_predicates=("integrated_circulation_pump",),
            facts=(integrated_pump,),
            reason_codes=(
                (
                    "boiler_integrated_pump_confirmed"
                    if integrated_pump.value is True
                    else "boiler_integrated_pump_explicitly_absent"
                ),
                "pump_boiler_requires_hydraulic_calculation",
            ),
            **common,
        )
    if request.relation == CompatibilityRelationKind.UNKNOWN:
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            reason_codes=("compatibility_relation_not_supported",),
            **common,
        )

    predicates = {
        CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE: ("control_thread",),
        CompatibilityRelationKind.THREADED_CONNECTION: ("connection_size", "connection_pattern"),
        CompatibilityRelationKind.SEWER_CONNECTION: ("diameter_mm", "sewer_scope", "sewer_system_family"),
    }[request.relation]
    facts, conflict = _all_facts(service, request, predicates)
    if conflict:
        return CompatibilityResult(
            status=CompatibilityResultStatus.SOURCE_CONFLICT,
            interface_predicates=predicates,
            facts=tuple(facts.values()),
            reason_codes=("compatibility_interface_fact_source_conflict",),
            **common,
        )
    missing = _missing(request, facts, predicates)
    if missing:
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            interface_predicates=predicates,
            facts=tuple(facts.values()),
            missing_predicates=missing,
            reason_codes=("compatibility_interface_facts_missing",),
            **common,
        )

    left, right = request.left.canonical_sku, request.right.canonical_sku
    assert left is not None and right is not None
    values = {
        predicate: (facts[(left, predicate)].value, facts[(right, predicate)].value)
        for predicate in predicates
    }
    compatible = False
    mismatch_code = ""
    if request.relation == CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE:
        compatible = str(values["control_thread"][0]) == str(values["control_thread"][1])
        mismatch_code = "thermostatic_control_thread_mismatch"
    elif request.relation == CompatibilityRelationKind.THREADED_CONNECTION:
        if values["connection_size"][0] != values["connection_size"][1]:
            mismatch_code = "thread_connection_size_mismatch"
        elif not _thread_pattern_mates(
            str(values["connection_pattern"][0]), str(values["connection_pattern"][1])
        ):
            mismatch_code = "thread_connection_pattern_not_mating"
        else:
            compatible = True
    else:
        if values["diameter_mm"][0] != values["diameter_mm"][1]:
            mismatch_code = "sewer_nominal_diameter_mismatch"
        elif values["sewer_scope"][0] != values["sewer_scope"][1]:
            mismatch_code = "sewer_installation_scope_mismatch"
        elif values["sewer_system_family"][0] != values["sewer_system_family"][1]:
            mismatch_code = "sewer_system_family_mismatch"
        else:
            compatible = True
    return CompatibilityResult(
        status=(CompatibilityResultStatus.COMPATIBLE if compatible else CompatibilityResultStatus.INCOMPATIBLE),
        interface_predicates=predicates,
        facts=tuple(facts.values()),
        reason_codes=(
            "compatibility_proven_by_two_interface_sides"
            if compatible
            else mismatch_code
        ,),
        **common,
    )


def validate_compatibility_result(
    request: CompatibilityRequest,
    result: CompatibilityResult,
    snapshot: AnswerSourceSnapshot,
) -> CompatibilityResult:
    """Fail closed on scope, source, predicate and decision drift."""

    reasons = list(result.reason_codes)
    passed = result.status in {
        CompatibilityResultStatus.COMPATIBLE,
        CompatibilityResultStatus.INCOMPATIBLE,
        CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
        CompatibilityResultStatus.SOURCE_CONFLICT,
    }
    if (
        result.left != request.left
        or result.right != request.right
        or result.selection_id != request.selection_id
        or result.source_revision != request.source_revision
        or result.relation != request.relation
    ):
        passed = False
        reasons.append("compatibility_request_result_identity_mismatch")
    resolved = {request.left.canonical_sku, request.right.canonical_sku} - {None}
    if request.scope_origin == CompatibilityScopeOrigin.V2_DELIVERED:
        if not request.selection_id or tuple(request.ordered_skus) != tuple(dict.fromkeys(request.ordered_skus)):
            passed = False
            reasons.append("compatibility_customer_visible_scope_invalid")
        if not resolved.issubset(set(request.ordered_skus)):
            passed = False
            reasons.append("compatibility_sku_outside_customer_visible_scope")
    for fact in result.facts:
        product = snapshot.product(fact.sku)
        if (
            fact.sku not in resolved
            or fact.source_revision != snapshot.source_revision
            or product is None
        ):
            passed = False
            reasons.append("compatibility_fact_scope_or_revision_invalid")
            continue
        if fact.source_kind == InterfaceSourceKind.CATALOG_ATTRIBUTE:
            source, conflict = _exact_card_fact(product, fact.predicate)
            if (
                conflict
                or source is None
                or source.value != fact.value
                or source.unit != fact.unit
            ):
                passed = False
                reasons.append("compatibility_catalog_fact_does_not_match_snapshot")
        elif fact.source_kind == InterfaceSourceKind.CATALOG_IDENTITY:
            if fact.predicate not in {"sewer_system_family", "diameter_mm"}:
                passed = False
                reasons.append("compatibility_identity_predicate_not_allowed")
        elif fact.source_kind == InterfaceSourceKind.PASSPORT:
            if (
                fact.verifier_status not in {"accepted", "document_text_exact"}
                or not fact.document
                or not fact.excerpt
            ):
                passed = False
                reasons.append("compatibility_passport_evidence_not_verified")
    if result.status in {CompatibilityResultStatus.COMPATIBLE, CompatibilityResultStatus.INCOMPATIBLE}:
        if len(resolved) != 2 or not result.facts or not result.interface_predicates:
            passed = False
            reasons.append("compatibility_verdict_evidence_incomplete")
        if any(
            predicate not in {item.predicate for item in result.facts}
            for predicate in result.interface_predicates
        ):
            passed = False
            reasons.append("compatibility_predicate_evidence_missing")
    if result.status == CompatibilityResultStatus.REJECTED:
        passed = False
    return result.model_copy(
        update={
            "outcome_gate_passed": passed,
            "reason_codes": tuple(dict.fromkeys(reasons)),
        }
    )
