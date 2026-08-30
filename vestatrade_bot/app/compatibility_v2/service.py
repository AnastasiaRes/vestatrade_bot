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
from app.component_evidence import builtin_part_state_from_text
from app.dialogue_v2.contracts import NextActionKind, TaskAct
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import Product, SessionState
from app.product_fact_evidence import (
    ProductFactEvidenceService,
)
from app.sku_resolution import SkuResolutionStatus, extract_explicit_sku_tokens, resolve_catalog_sku
from app.v2_visible_products import (
    customer_visible_v2_scope,
    has_deictic_product_reference,
    ordinal_indices,
)

from .contracts import (
    CompatibilityProductReference,
    CompatibilityReferenceKind,
    CompatibilityRelationKind,
    CompatibilityRequest,
    CompatibilityResult,
    CompatibilityResultStatus,
    CompatibilityScopeOrigin,
    InterfaceEndpoint,
    InterfaceFact,
    InterfaceFactResolution,
    InterfaceFactResolutionStatus,
    InterfaceSourceKind,
)


_SEWER_EXPLICIT_KINDS = frozenset(
    {
        ProductKind.SEWER_PIPE,
        ProductKind.SEWER_ELBOW,
    }
)
_SEWER_MULTI_PORT_KINDS = frozenset(
    {
        ProductKind.TEE,
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
_METRIC_THREAD_RE = re.compile(
    r"(?iu)(?<![a-zа-яё])(?:m|м)\s*(\d{1,2})\s*[xх×]\s*(\d+(?:[.,]\d+)?)"
)
_THREAD_STANDARD_RE = re.compile(
    r"(?iu)(?<![a-zа-яё])(npt|rp|rc|g|r)\s*\d+(?:\s+\d+/\d+|/\d+)?"
)
_SEWER_SYSTEM_IDENTITY_RE = re.compile(r"(?iu)\b(?:ht|kg)\w*\b")
# General SKU extraction deliberately does not accept short numeric tokens:
# outside an identity operation they are too easily confused with a dimension
# or quantity.  Compatibility has a frozen catalogue scope, so an additional
# five-digit span may be considered *only* if the existing resolver confirms
# it as an exact/unique identity in that snapshot.
_COMPATIBILITY_NUMERIC_SKU_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


def _endpoint_for(predicate: str) -> InterfaceEndpoint | None:
    return {
        "control_thread": InterfaceEndpoint.THERMOSTATIC_CONTROL,
        "connection_size": InterfaceEndpoint.THREADED_CONNECTION,
        "connection_pattern": InterfaceEndpoint.THREADED_CONNECTION,
        "thread_standard": InterfaceEndpoint.THREADED_CONNECTION,
        "diameter_mm": InterfaceEndpoint.SEWER_JOINT,
        "sewer_scope": InterfaceEndpoint.SEWER_JOINT,
        "sewer_system_family": InterfaceEndpoint.SEWER_JOINT,
        "integrated_circulation_pump": InterfaceEndpoint.INTEGRATED_CIRCULATION_PUMP,
    }.get(predicate)


def _canonical_interface_value(predicate: str, value: object) -> object:
    if predicate == "control_thread":
        match = _METRIC_THREAD_RE.search(str(value))
        if match:
            pitch = match.group(2).replace(",", ".")
            return f"M{match.group(1)}x{pitch}"
    if predicate == "thread_standard":
        return str(value).casefold()
    return value


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


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
    candidate_tokens = tuple(
        sorted(
            dict.fromkeys(
                (*extract_explicit_sku_tokens(message), *(
                    match.group(0)
                    for match in _COMPATIBILITY_NUMERIC_SKU_RE.finditer(message)
                ))
            ),
            key=lambda token: message.find(token),
        )
    )
    for token in candidate_tokens:
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


def _is_sewer_product(product: CatalogAnswerProduct) -> bool:
    """Recognise a sewer item without treating every tee/coupling as sewer.

    ``TEE`` and ``REDUCING_COUPLING`` are shared catalogue kinds: a PPR
    reducer is not a sewer fitting.  A positive sewer relation therefore needs
    either an unambiguous sewer kind or a product-specific sewer marker from
    the frozen snapshot.  This is deliberately narrower than category search:
    it prevents a compatibility verdict from being inferred from a generic
    component name.
    """

    if product.product_kind in _SEWER_EXPLICIT_KINDS:
        return True
    scope, scope_conflict = _exact_card_fact(product, "sewer_scope")
    if scope is not None and not scope_conflict:
        return True
    return _SEWER_SYSTEM_IDENTITY_RE.search(product.name) is not None


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
    if _is_sewer_product(first) and _is_sewer_product(second):
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
        visible_scope = customer_visible_v2_scope(session)
        visible_valid = visible_scope.matches_revision(snapshot.source_revision)
        visible = visible_scope.ordered_skus if visible_valid else ()
        for ordinal in ordinal_indices(original_utterance):
            if len(resolved) >= 2:
                break
            ordinal_reference = visible_scope.ordinal(ordinal)
            if visible_valid and ordinal_reference.resolved:
                sku = ordinal_reference.canonical_sku
                assert sku is not None
                if sku not in {item.canonical_sku for item in resolved}:
                    resolved.append(
                        _reference(
                            CompatibilityReferenceKind.ORDINAL,
                            raw=str(ordinal + 1),
                            sku=sku,
                            reason=ordinal_reference.reason_code,
                        )
                    )
            elif visible_valid:
                resolved.append(
                    _unresolved(ordinal_reference.reason_code, raw=str(ordinal + 1))
                )
                break
        if len(resolved) < 2 and visible_valid:
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
            elif len(resolved) == 1 and has_deictic_product_reference(original_utterance):
                focus_reference = visible_scope.current_focus()
                if (
                    focus_reference.resolved
                    and focus_reference.canonical_sku != resolved[0].canonical_sku
                ):
                    # ``этот`` is an explicit reference to the current focus,
                    # not a request to guess among every remaining card.  It
                    # therefore stays resolvable for selections of 3–5 cards.
                    resolved.append(
                        _reference(
                            CompatibilityReferenceKind.CURRENT_FOCUS,
                            raw="этот",
                            sku=focus_reference.canonical_sku,
                            reason=focus_reference.reason_code,
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
    """Read-only adapter over all checked interface observations.

    It deliberately returns observations before choosing a display source.  A
    card value therefore cannot hide a contradictory, SKU-bound passport.
    """

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
        """Compatibility facade for existing deterministic relation rules."""

        resolution = self.observe(sku, predicate)
        return (
            resolution.selected_fact,
            resolution.status == InterfaceFactResolutionStatus.SOURCE_CONFLICT,
        )

    def observe(
        self,
        sku: str,
        predicate: str,
        *,
        endpoint: InterfaceEndpoint | None = None,
    ) -> InterfaceFactResolution:
        """Collect verified card/passport observations for one interface fact."""

        snapshot_product = self.snapshot.product(sku)
        resolved_endpoint = endpoint or _endpoint_for(predicate)
        if snapshot_product is None:
            return InterfaceFactResolution(
                sku=sku,
                predicate=predicate,
                endpoint=resolved_endpoint,
                status=InterfaceFactResolutionStatus.INSUFFICIENT_EVIDENCE,
                reason_codes=("interface_product_missing_from_source_snapshot",),
            )

        observations, card_ambiguous = self._catalog_observations(
            snapshot_product, predicate, resolved_endpoint
        )
        if predicate in {"sewer_system_family", "diameter_mm"}:
            identity = self._sewer_identity_fact(snapshot_product, predicate)
            if identity is not None:
                observations.append(identity.model_copy(update={"endpoint": resolved_endpoint}))
        if predicate == "thread_standard":
            standard = self._thread_standard_identity_fact(snapshot_product)
            if standard is not None:
                observations.append(standard.model_copy(update={"endpoint": resolved_endpoint}))
        if predicate == "control_thread":
            passport = self._passport_control_thread_observation(sku, resolved_endpoint)
            if passport is not None:
                observations.append(passport)
        if predicate == "integrated_circulation_pump":
            observations.extend(
                self._integrated_pump_passport_observations(sku, resolved_endpoint)
            )

        # Do not make source priority a verdict.  Multiple equal observations
        # are desirable corroboration; different canonical values are a hard
        # source conflict.
        values = {
            (str(_canonical_interface_value(predicate, item.value)), item.unit)
            for item in observations
        }
        if card_ambiguous or len(values) > 1:
            return InterfaceFactResolution(
                sku=sku,
                predicate=predicate,
                endpoint=resolved_endpoint,
                status=InterfaceFactResolutionStatus.SOURCE_CONFLICT,
                observations=tuple(observations),
                reason_codes=(
                    "catalogue_interface_fact_ambiguous"
                    if card_ambiguous
                    else "interface_fact_source_values_conflict",
                ),
            )
        if not observations:
            return InterfaceFactResolution(
                sku=sku,
                predicate=predicate,
                endpoint=resolved_endpoint,
                status=InterfaceFactResolutionStatus.INSUFFICIENT_EVIDENCE,
                reason_codes=("interface_fact_not_confirmed",),
            )
        # A checked passport is the most useful citation when it agrees with
        # the current source snapshot; the complete observations remain in the
        # result for the outcome gate and telemetry.
        selected = next(
            (item for item in observations if item.source_kind == InterfaceSourceKind.PASSPORT),
            observations[0],
        )
        return InterfaceFactResolution(
            sku=sku,
            predicate=predicate,
            endpoint=resolved_endpoint,
            status=InterfaceFactResolutionStatus.PROVEN,
            selected_fact=selected,
            observations=tuple(observations),
        )

    def _catalog_observations(
        self,
        product: CatalogAnswerProduct,
        predicate: str,
        endpoint: InterfaceEndpoint | None,
    ) -> tuple[list[InterfaceFact], bool]:
        facts = tuple(item for item in product.facts if item.name == predicate)
        issues = tuple(item for item in product.fact_issues if item.name == predicate)
        observations = [
            InterfaceFact(
                sku=product.sku,
                predicate=predicate,
                value=_canonical_interface_value(predicate, fact.value),
                unit=fact.unit,
                source_kind=InterfaceSourceKind.CATALOG_ATTRIBUTE,
                source_revision=self.snapshot.source_revision,
                document="catalogue",
                section=fact.provenance.source_field,
                excerpt=f"{fact.provenance.source_field}: {fact.provenance.raw_value}",
                verifier_status="catalog_card_exact",
                endpoint=endpoint,
                model_scope="source_snapshot",
            )
            for fact in facts
        ]
        distinct = {(str(item.value), item.unit) for item in observations}
        return observations, bool(issues or len(distinct) > 1)

    def _integrated_pump_passport_observations(
        self,
        sku: str,
        endpoint: InterfaceEndpoint | None,
    ) -> list[InterfaceFact]:
        product = self.products_by_sku.get(sku)
        if product is None:
            return []
        observations: list[InterfaceFact] = []
        for document in product.documents:
            if not self._document_is_model_bound(document.binding_scope):
                continue
            state = builtin_part_state_from_text(document.text, "насос")
            if state is None:
                continue
            observations.append(
                InterfaceFact(
                    sku=sku,
                    predicate="integrated_circulation_pump",
                    value=state,
                    source_kind=InterfaceSourceKind.PASSPORT,
                    source_revision=self.snapshot.source_revision,
                    document=document.filename,
                    section="комплект/конструкция",
                    excerpt=document.text[:500],
                    verifier_status="document_text_exact",
                    endpoint=endpoint,
                    model_scope=document.binding_scope,
                )
            )
        return observations

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
            endpoint=InterfaceEndpoint.SEWER_JOINT,
            model_scope="source_snapshot",
        )

    def _thread_standard_identity_fact(
        self,
        product: CatalogAnswerProduct,
    ) -> InterfaceFact | None:
        match = _THREAD_STANDARD_RE.search(product.name)
        if match is None:
            return None
        return InterfaceFact(
            sku=product.sku,
            predicate="thread_standard",
            value=match.group(1).casefold(),
            source_kind=InterfaceSourceKind.CATALOG_IDENTITY,
            source_revision=self.snapshot.source_revision,
            document="catalogue",
            section="name",
            excerpt=match.group(0),
            verifier_status="catalogue_identity_exact",
            endpoint=InterfaceEndpoint.THREADED_CONNECTION,
            model_scope="source_snapshot",
        )

    @staticmethod
    def _document_is_model_bound(binding_scope: str | None) -> bool:
        return binding_scope in {"exact_sku", "filename_match"}

    def _passport_control_thread_observation(
        self,
        sku: str,
        endpoint: InterfaceEndpoint | None,
    ) -> InterfaceFact | None:
        """Use the existing embedding/verifier path for an exact-bound model.

        A series-prefix document remains available to ordinary ProductFact,
        but this relation requires model-bound evidence before it can turn into
        a positive Compatibility verdict.
        """

        if self.product_fact_evidence is None:
            return None
        product = self.products_by_sku.get(sku)
        if product is None:
            return None
        documents = tuple(
            document
            for document in product.documents
            if self._document_is_model_bound(document.binding_scope)
        )
        if not documents:
            return None
        passport = self.product_fact_evidence.passport_service.answer(
            f"Какая присоединительная резьба термоголовки у {product.name} ({sku})?",
            document_scope=tuple(item.filename for item in documents),
            context=f"Точный товар: {sku} — {product.name}",
            flow="v2_compatibility_interface_fact",
            predicate="thermostatic_head_thread",
            canonical_sku=sku,
        )
        if (
            passport.status.value != "answered"
            or not passport.quote
            or not passport.document
            or passport.verifier_status != "accepted"
        ):
            return None
        match = _METRIC_THREAD_RE.search(passport.quote)
        if match is None:
            return None
        document = next(
            (item for item in documents if item.filename == passport.document),
            None,
        )
        if document is None:
            return None
        return InterfaceFact(
            sku=sku,
            predicate="control_thread",
            value=f"M{match.group(1)}x{match.group(2).replace(',', '.')}",
            source_kind=InterfaceSourceKind.PASSPORT,
            source_revision=self.snapshot.source_revision,
            document=passport.document,
            section=passport.section,
            excerpt=passport.quote,
            verifier_status=passport.verifier_status,
            endpoint=endpoint,
            model_scope=document.binding_scope,
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
) -> tuple[dict[tuple[str, str], InterfaceFact], bool, tuple[InterfaceFact, ...]]:
    facts: dict[tuple[str, str], InterfaceFact] = {}
    conflict = False
    observations: list[InterfaceFact] = []
    for sku in (request.left.canonical_sku, request.right.canonical_sku):
        if sku is None:
            continue
        for predicate in predicates:
            resolution = service.observe(sku, predicate)
            observations.extend(resolution.observations)
            conflict = conflict or (
                resolution.status == InterfaceFactResolutionStatus.SOURCE_CONFLICT
            )
            if resolution.selected_fact is not None:
                facts[(sku, predicate)] = resolution.selected_fact
    return facts, conflict, tuple(observations)


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


def _requires_port_resolution(
    product: CatalogAnswerProduct,
) -> bool:
    """A multi-port item cannot be validated from one flat connection fact."""

    port_count, conflict = _exact_card_fact(product, "port_count")
    if conflict or port_count is None:
        return False
    try:
        return int(port_count.value) > 2
    except (TypeError, ValueError):
        return False


def _requires_sewer_endpoint_resolution(product: CatalogAnswerProduct) -> bool:
    """A flat DN cannot identify a branch of a tee or a reducing fitting."""

    return (
        product.product_kind in _SEWER_MULTI_PORT_KINDS
        or _requires_port_resolution(product)
    )


def _relation_verdict(
    relation: CompatibilityRelationKind,
    facts: dict[tuple[str, str], InterfaceFact],
    left: str,
    right: str,
) -> tuple[CompatibilityResultStatus, str]:
    """Pure deterministic verdict used by both execution and the outcome gate."""

    predicates = {
        CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE: ("control_thread",),
        CompatibilityRelationKind.THREADED_CONNECTION: (
            "connection_size",
            "connection_pattern",
            "thread_standard",
        ),
        CompatibilityRelationKind.SEWER_CONNECTION: (
            "diameter_mm",
            "sewer_scope",
            "sewer_system_family",
        ),
    }[relation]
    values = {
        predicate: (facts[(left, predicate)].value, facts[(right, predicate)].value)
        for predicate in predicates
    }
    if relation == CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE:
        return (
            (
                CompatibilityResultStatus.COMPATIBLE
                if str(values["control_thread"][0]) == str(values["control_thread"][1])
                else CompatibilityResultStatus.INCOMPATIBLE
            ),
            (
                "compatibility_proven_by_two_interface_sides"
                if str(values["control_thread"][0]) == str(values["control_thread"][1])
                else "thermostatic_control_thread_mismatch"
            ),
        )
    if relation == CompatibilityRelationKind.THREADED_CONNECTION:
        if values["connection_size"][0] != values["connection_size"][1]:
            return CompatibilityResultStatus.INCOMPATIBLE, "thread_connection_size_mismatch"
        if str(values["thread_standard"][0]) != str(values["thread_standard"][1]):
            return CompatibilityResultStatus.INCOMPATIBLE, "thread_connection_standard_mismatch"
        if not _thread_pattern_mates(
            str(values["connection_pattern"][0]), str(values["connection_pattern"][1])
        ):
            return CompatibilityResultStatus.INCOMPATIBLE, "thread_connection_pattern_not_mating"
        return CompatibilityResultStatus.COMPATIBLE, "compatibility_proven_by_two_interface_sides"
    if values["diameter_mm"][0] != values["diameter_mm"][1]:
        return CompatibilityResultStatus.INCOMPATIBLE, "sewer_nominal_diameter_mismatch"
    if values["sewer_scope"][0] != values["sewer_scope"][1]:
        return CompatibilityResultStatus.INCOMPATIBLE, "sewer_installation_scope_mismatch"
    if values["sewer_system_family"][0] != values["sewer_system_family"][1]:
        return CompatibilityResultStatus.INCOMPATIBLE, "sewer_system_family_mismatch"
    return CompatibilityResultStatus.COMPATIBLE, "compatibility_proven_by_two_interface_sides"


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
        pump_resolution = service.observe(boiler_sku, "integrated_circulation_pump")
        integrated_pump = pump_resolution.selected_fact
        if pump_resolution.status == InterfaceFactResolutionStatus.SOURCE_CONFLICT:
            return CompatibilityResult(
                status=CompatibilityResultStatus.SOURCE_CONFLICT,
                interface_predicates=("integrated_circulation_pump",),
                facts=(() if integrated_pump is None else (integrated_pump,)),
                observations=pump_resolution.observations,
                reason_codes=("boiler_integrated_pump_source_conflict",),
                **common,
            )
        if integrated_pump is None:
            return CompatibilityResult(
                status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
                interface_predicates=("integrated_circulation_pump",),
                missing_predicates=(f"{boiler_sku}:integrated_circulation_pump",),
                observations=pump_resolution.observations,
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
            observations=pump_resolution.observations,
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
        CompatibilityRelationKind.THREADED_CONNECTION: (
            "connection_size",
            "connection_pattern",
            "thread_standard",
        ),
        CompatibilityRelationKind.SEWER_CONNECTION: ("diameter_mm", "sewer_scope", "sewer_system_family"),
    }[request.relation]
    if request.relation == CompatibilityRelationKind.THREADED_CONNECTION:
        left_product = snapshot.product(request.left.canonical_sku)
        right_product = snapshot.product(request.right.canonical_sku)
        assert left_product is not None and right_product is not None
        if _requires_port_resolution(left_product) or _requires_port_resolution(right_product):
            return CompatibilityResult(
                status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
                interface_predicates=predicates,
                missing_predicates=("resolved_connection_endpoint",),
                reason_codes=("threaded_multiport_endpoint_not_determined",),
                **common,
            )
    if request.relation == CompatibilityRelationKind.SEWER_CONNECTION:
        left_product = snapshot.product(request.left.canonical_sku)
        right_product = snapshot.product(request.right.canonical_sku)
        assert left_product is not None and right_product is not None
        if (
            _requires_sewer_endpoint_resolution(left_product)
            or _requires_sewer_endpoint_resolution(right_product)
        ):
            return CompatibilityResult(
                status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
                interface_predicates=predicates,
                missing_predicates=("resolved_sewer_joint_endpoint",),
                reason_codes=("sewer_multiport_endpoint_not_determined",),
                **common,
            )
    facts, conflict, observations = _all_facts(service, request, predicates)
    if conflict:
        return CompatibilityResult(
            status=CompatibilityResultStatus.SOURCE_CONFLICT,
            interface_predicates=predicates,
            facts=tuple(facts.values()),
            observations=observations,
            reason_codes=("compatibility_interface_fact_source_conflict",),
            **common,
        )
    missing = _missing(request, facts, predicates)
    if missing:
        return CompatibilityResult(
            status=CompatibilityResultStatus.INSUFFICIENT_EVIDENCE,
            interface_predicates=predicates,
            facts=tuple(facts.values()),
            observations=observations,
            missing_predicates=missing,
            reason_codes=("compatibility_interface_facts_missing",),
            **common,
        )

    left, right = request.left.canonical_sku, request.right.canonical_sku
    assert left is not None and right is not None
    status, reason = _relation_verdict(request.relation, facts, left, right)
    return CompatibilityResult(
        status=status,
        interface_predicates=predicates,
        facts=tuple(facts.values()),
        observations=observations,
        reason_codes=(reason,),
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
    def validate_fact_source(fact: InterfaceFact) -> str | None:
        product = snapshot.product(fact.sku)
        if (
            fact.sku not in resolved
            or fact.source_revision != snapshot.source_revision
            or product is None
        ):
            return "compatibility_fact_scope_or_revision_invalid"
        if fact.source_kind == InterfaceSourceKind.CATALOG_ATTRIBUTE:
            # A SOURCE_CONFLICT result intentionally carries more than one
            # card observation.  Validate each observation against the frozen
            # snapshot instead of rejecting it merely because the aggregate is
            # non-unique.
            matching = any(
                _canonical_interface_value(fact.predicate, item.value) == fact.value
                and item.unit == fact.unit
                for item in product.facts
                if item.name == fact.predicate
            )
            if not matching:
                return "compatibility_catalog_fact_does_not_match_snapshot"
        elif fact.source_kind == InterfaceSourceKind.CATALOG_IDENTITY:
            if fact.predicate not in {
                "sewer_system_family",
                "diameter_mm",
                "thread_standard",
            }:
                return "compatibility_identity_predicate_not_allowed"
        elif fact.source_kind == InterfaceSourceKind.PASSPORT:
            if (
                fact.verifier_status not in {"accepted", "document_text_exact"}
                or not fact.document
                or not fact.excerpt
                or fact.model_scope not in {"exact_sku", "filename_match"}
            ):
                return "compatibility_passport_evidence_not_verified"
        return None

    for fact in (*result.facts, *result.observations):
        error = validate_fact_source(fact)
        if error:
            passed = False
            reasons.append(error)
    if result.status in {CompatibilityResultStatus.COMPATIBLE, CompatibilityResultStatus.INCOMPATIBLE}:
        if len(resolved) != 2 or not result.facts or not result.interface_predicates:
            passed = False
            reasons.append("compatibility_verdict_evidence_incomplete")
        expected_keys = {
            (sku, predicate)
            for sku in resolved
            for predicate in result.interface_predicates
        }
        selected = {(item.sku, item.predicate): item for item in result.facts}
        if set(selected) != expected_keys:
            passed = False
            reasons.append("compatibility_predicate_evidence_missing")
        elif not all(item in result.observations for item in result.facts):
            passed = False
            reasons.append("compatibility_selected_fact_not_in_observations")
        elif result.relation in {
            CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE,
            CompatibilityRelationKind.THREADED_CONNECTION,
            CompatibilityRelationKind.SEWER_CONNECTION,
        }:
            expected_status, expected_reason = _relation_verdict(
                result.relation,
                selected,
                request.left.canonical_sku or "",
                request.right.canonical_sku or "",
            )
            if result.status != expected_status:
                passed = False
                reasons.append("compatibility_verdict_not_recomputed_from_evidence")
            if expected_reason not in result.reason_codes:
                passed = False
                reasons.append("compatibility_verdict_reason_drift")
    if result.status == CompatibilityResultStatus.REJECTED:
        passed = False
    return result.model_copy(
        update={
            "outcome_gate_passed": passed,
            "reason_codes": tuple(dict.fromkeys(reasons)),
        }
    )
