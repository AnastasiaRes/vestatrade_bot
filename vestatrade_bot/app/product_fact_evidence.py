"""Shared, fail-closed evidence path for a fact about a concrete product.

The module deliberately reuses the legacy passport index and
``PassportAnswerAgent``.  It adds the contracts which the V2 delivery path
needs: an explicit product reference, a canonical predicate and an evidence
gate that checks product, predicate, document scope and value before prose is
rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agents.passport_answer import PassportAnswerAgent
from app.agents.domain_ontology import fact_aliases
from app.catalog_v2.contracts import CatalogProductSnapshot
from app.component_evidence import builtin_part_evidence
from app.config import PROJECT_ROOT, Settings
from app.diagnostic_telemetry import record_passport_event
from app.models import Product, SessionState
from app.passport_retrieval import expand_query, load_or_build
from app.sku_resolution import (
    SkuResolutionStatus,
    extract_explicit_sku_tokens,
    resolve_catalog_sku,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ProductFactStatus(str, Enum):
    ANSWERED = "answered"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


class ProductReferenceKind(str, Enum):
    EXACT_SKU = "exact_sku"
    PARTIAL_SKU = "partial_sku"
    ORDINAL = "ordinal"
    NAMED_PRODUCT = "named_product"
    NAMED_SERIES = "named_series"
    CURRENT_FOCUS = "current_focus"
    SINGLE_PRESENTED = "single_presented"
    UNRESOLVED = "unresolved"


class ProductReference(FrozenModel):
    kind: ProductReferenceKind
    raw: str = ""
    canonical_sku: str | None = None
    candidate_skus: tuple[str, ...] = ()
    reason_code: str


class ProductFactRequest(FrozenModel):
    question: str
    predicate: str
    product_ref: ProductReference


class PassportEvidenceStatus(str, Enum):
    ANSWERED = "answered"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"


class PassportEvidenceResult(FrozenModel):
    status: PassportEvidenceStatus
    answer_text: str | None = None
    quote: str | None = None
    framing: str | None = None
    document: str | None = None
    section: str | None = None
    ordinal: int | None = Field(default=None, ge=0)
    verifier_status: str
    rejection_reason: str | None = None
    document_scope: tuple[str, ...] = ()


class ProductFactEvidence(FrozenModel):
    status: ProductFactStatus
    request: ProductFactRequest
    product_name: str | None = None
    value: str | int | float | bool | None = None
    unit: str | None = None
    source_kind: str | None = None
    document: str | None = None
    section: str | None = None
    quote: str | None = None
    verifier_status: str
    reason_code: str
    document_scope: tuple[str, ...] = ()


class _EmbedClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class PassportEvidenceService:
    """One shared adapter over the existing index, retrieval and verifier."""

    def __init__(
        self,
        settings: Settings,
        llm_client: _EmbedClient,
    ) -> None:
        self.settings = settings
        self.llm_client = llm_client

    def answer(
        self,
        question: str,
        *,
        document_scope: tuple[str, ...] | list[str],
        context: str | None = None,
        flow: str,
        predicate: str | None = None,
        canonical_sku: str | None = None,
    ) -> PassportEvidenceResult:
        documents = tuple(dict.fromkeys(str(item) for item in document_scope if item))
        if not documents:
            record_passport_event(
                event="passport_retrieval",
                status="skipped",
                reason="product_document_scope_missing",
                flow=flow,
                predicate=predicate,
                canonical_sku=canonical_sku,
                document_scope=[],
            )
            return PassportEvidenceResult(
                status=PassportEvidenceStatus.REJECTED,
                verifier_status="not_run",
                rejection_reason="product_document_scope_missing",
            )
        if not self.settings.embeddings_enabled:
            record_passport_event(
                event="passport_retrieval",
                status="skipped",
                reason="embeddings_not_configured",
                flow=flow,
                predicate=predicate,
                canonical_sku=canonical_sku,
                document_scope=list(documents),
            )
            return PassportEvidenceResult(
                status=PassportEvidenceStatus.REJECTED,
                verifier_status="not_run",
                rejection_reason="embeddings_not_configured",
                document_scope=documents,
            )
        try:
            index = load_or_build(
                self.settings.products_cache_path.with_name("passport_index.json"),
                [self.settings.product_docs_dir, PROJECT_ROOT / "data"],
                self.llm_client.embed,
                self.settings.embedding_model,
            )
            vectors = self.llm_client.embed([expand_query(question)])
            hits = index.search(
                question,
                documents=list(documents),
                query_vector=vectors[0] if vectors else None,
            )
            record_passport_event(
                event="passport_retrieval",
                status="hits" if hits else "no_hits",
                flow=flow,
                predicate=predicate,
                canonical_sku=canonical_sku,
                embedding_model=self.settings.embedding_model,
                index_model=index.model,
                index_chunk_count=len(index.chunks),
                index_has_vectors=index.has_vectors,
                document_scope=list(documents),
                hits=[
                    {
                        "document": hit.chunk.document,
                        "section": hit.chunk.section,
                        "ordinal": hit.chunk.ordinal,
                        "score": round(float(hit.score), 6),
                    }
                    for hit in hits
                ],
            )
            if not hits:
                return PassportEvidenceResult(
                    status=PassportEvidenceStatus.NOT_FOUND,
                    verifier_status="not_run",
                    rejection_reason="no_retrieval_hits",
                    document_scope=documents,
                )
            agent = PassportAnswerAgent(self.llm_client)  # type: ignore[arg-type]
            result = agent.answer(
                question,
                [hit.chunk for hit in hits],
                context=context,
            )
            verified = agent.last_verified_evidence
            accepted = bool(result and verified)
            record_passport_event(
                event="passport_verification",
                status="accepted" if accepted else "rejected",
                flow=flow,
                predicate=predicate,
                canonical_sku=canonical_sku,
                llm_used=agent.last_llm_used,
                rejection_reason=agent.last_rejection_reason,
                framing_drop_reason=agent.last_framing_drop_reason,
                corrected_excerpts=agent.corrected_excerpts,
                document_scope=list(documents),
                source=(
                    {
                        "document": verified.document,
                        "section": verified.section,
                        "ordinal": verified.ordinal,
                    }
                    if verified
                    else None
                ),
            )
            if not accepted or result is None or verified is None:
                return PassportEvidenceResult(
                    status=PassportEvidenceStatus.REJECTED,
                    verifier_status="rejected",
                    rejection_reason=(
                        agent.last_rejection_reason or "passport_verifier_rejected"
                    ),
                    document_scope=documents,
                )
            return PassportEvidenceResult(
                status=PassportEvidenceStatus.ANSWERED,
                answer_text=result[0],
                quote=verified.quote,
                framing=verified.framing,
                document=verified.document,
                section=verified.section,
                ordinal=verified.ordinal,
                verifier_status="accepted",
                document_scope=documents,
            )
        except Exception as exc:  # retrieval must never break the customer turn
            reason = f"{type(exc).__name__}:passport_evidence_failed"
            record_passport_event(
                event="passport_retrieval",
                status="error",
                reason=reason,
                flow=flow,
                predicate=predicate,
                canonical_sku=canonical_sku,
                document_scope=list(documents),
            )
            return PassportEvidenceResult(
                status=PassportEvidenceStatus.REJECTED,
                verifier_status="error",
                rejection_reason=reason,
                document_scope=documents,
            )


@dataclass(frozen=True)
class FactSpec:
    predicate: str
    label: str
    unit: str | None
    question_groups: tuple[tuple[str, ...], ...]
    attribute_keys: tuple[str, ...]
    quote_groups: tuple[tuple[str, ...], ...]


FACT_SPECS = (
    FactSpec(
        predicate="installation_length_mm",
        label="монтажная длина",
        unit="мм",
        question_groups=(("монтажн",), ("длин",)),
        attribute_keys=("монтажная длина, мм", "монтажная длина"),
        quote_groups=(("монтажн",), ("длин",)),
    ),
    FactSpec(
        predicate="maximum_operating_temperature_c",
        label="максимальная рабочая температура",
        unit="°C",
        question_groups=(("максим",), ("температур",)),
        attribute_keys=(
            "максимальная рабочая температура, °с",
            "максимальная температура рабочей среды, °с",
        ),
        quote_groups=(("температур",),),
    ),
    FactSpec(
        predicate="radiator_heating_pressure_bar",
        label="рабочее давление при радиаторном отоплении",
        unit="бар",
        question_groups=(("давлен",), ("радиатор",)),
        attribute_keys=("рабочее давление, радиаторное отопление, бар",),
        quote_groups=(("давлен",), ("радиатор", "отоплен")),
    ),
    FactSpec(
        predicate="thermostatic_head_thread",
        label="резьба под термоголовку",
        unit=None,
        question_groups=(("резьб",), ("термоголов", "головк")),
        attribute_keys=("резьба под термоголовку",),
        quote_groups=(("резьб",), ("термостат", "головк")),
    ),
    FactSpec(
        predicate="circuits",
        label="количество контуров",
        unit=None,
        question_groups=(("контур",),),
        attribute_keys=("количество контуров", "контуры"),
        quote_groups=(("контур",),),
    ),
    FactSpec(
        predicate="integrated_circulation_pump",
        label="встроенный циркуляционный насос",
        unit=None,
        question_groups=(("насос",), ("встроен", "в котле", "в этом")),
        attribute_keys=(),
        quote_groups=(("насос",),),
    ),
)

_SEMANTIC_PREDICATE_ALIASES = {
    "installation_length": "installation_length_mm",
    "installation_length_mm": "installation_length_mm",
    "mounting_length": "installation_length_mm",
    "mounting_length_mm": "installation_length_mm",
    "maximum_operating_temperature": "maximum_operating_temperature_c",
    "maximum_operating_temperature_c": "maximum_operating_temperature_c",
    "operating_temperature_c": "maximum_operating_temperature_c",
    "radiator_heating_pressure": "radiator_heating_pressure_bar",
    "radiator_heating_pressure_bar": "radiator_heating_pressure_bar",
    "operating_pressure_bar": "radiator_heating_pressure_bar",
    "control_thread": "thermostatic_head_thread",
    "thermostatic_head_thread": "thermostatic_head_thread",
    "integrated_circulation_pump": "integrated_circulation_pump",
    "built_in_pump": "integrated_circulation_pump",
    "builtin_pump": "integrated_circulation_pump",
}

_CARD_FACT_LABELS = {
    "handle_type": "тип ручки",
    "max_head_m": "максимальный напор",
    "max_flow_l_h": "максимальный расход",
    "connection_pattern": "тип резьбы",
    "connection_size": "размер соединения",
    "material": "материал",
    "reinforcement": "армирование",
    "boiler_type": "тип котла",
    "circuits": "количество контуров",
    "combustion_chamber": "камера сгорания",
    "center_distance_mm": "межосевое расстояние",
    "micron_rating_um": "тонкость фильтрации",
    "filter_method": "тип фильтрации",
}

_QUESTION_RE = re.compile(
    r"\?|^\s*(?:как|какой|какая|какое|какие|можно|нужно|почему|чем|что|сколько|где)\b",
    re.IGNORECASE,
)
_MODEL_MARKER_RE = re.compile(
    r"(?<![\w])(?:[a-zа-я]{1,12}\s*[-/]?\s*\d{1,4})(?![\w])",
    re.IGNORECASE,
)


def _normalise(text: object) -> str:
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


def _matches_groups(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(any(token in text for token in alternatives) for alternatives in groups)


def _display_value(value: object, predicate: str) -> str:
    raw = str(value).strip()
    if predicate == "thermostatic_head_thread":
        raw = raw.upper().replace("М", "M").replace("Х", "×").replace("X", "×")
        raw = re.sub(r"\s+", "", raw)
    if predicate == "installation_length_mm":
        raw = raw.replace("-", "–")
    return raw


def _quote_contains_value(quote: str, value: str) -> bool:
    normalized_quote = _normalise(quote).replace(" ", "")
    alternatives = {
        _normalise(value).replace(" ", ""),
        _normalise(value).replace(" ", "").replace("×", "x"),
        _normalise(value).replace(" ", "").replace("m", "м").replace("×", "х"),
    }
    if "–" in value or "-" in value:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", value)
        return bool(numbers and all(number.replace(",", ".") in normalized_quote.replace(",", ".") for number in numbers))
    return any(candidate and candidate in normalized_quote for candidate in alternatives)


class ProductFactEvidenceService:
    """Resolve one product reference and prove one requested predicate."""

    def __init__(
        self,
        settings: Settings,
        llm_client: _EmbedClient,
        products: list[Product],
        *,
        passport_service: PassportEvidenceService | None = None,
        catalog_snapshot: tuple[CatalogProductSnapshot, ...] = (),
    ) -> None:
        self.settings = settings
        self.llm_client = llm_client
        self.products = products
        self.passport_service = passport_service or PassportEvidenceService(
            settings,
            llm_client,
        )
        self.catalog_by_sku = {item.sku: item for item in catalog_snapshot}

    def set_products(self, products: list[Product]) -> None:
        self.products = products

    def set_catalog_snapshot(
        self,
        catalog_snapshot: tuple[CatalogProductSnapshot, ...],
    ) -> None:
        """Attach the existing normalized card snapshot; never build an index."""

        self.catalog_by_sku = {item.sku: item for item in catalog_snapshot}

    def evaluate(
        self,
        question: str,
        session: SessionState,
        *,
        semantic_fact_name: str | None = None,
    ) -> ProductFactEvidence | None:
        if not _QUESTION_RE.search(question.strip()):
            return None
        product_ref = self.resolve_reference(question, session)
        predicate = self._predicate(question, semantic_fact_name)
        if predicate is None:
            predicate = self._catalog_card_predicate(
                _normalise(question),
                semantic_fact_name=semantic_fact_name,
                product_ref=product_ref,
            )
        if predicate is None:
            if not self._looks_like_unmapped_product_fact(question, product_ref):
                return None
            predicate = "unsupported_product_fact"
        request = ProductFactRequest(
            question=question,
            predicate=predicate,
            product_ref=product_ref,
        )
        if product_ref.kind == ProductReferenceKind.UNRESOLVED:
            # Candidate suggestions are not a product scope.  In particular,
            # an ambiguous SKU prefix must not become a global/multi-product
            # passport search merely because the resolver can list matches.
            return ProductFactEvidence(
                status=ProductFactStatus.AMBIGUOUS,
                request=request,
                verifier_status="not_run",
                reason_code=product_ref.reason_code,
            )
        if not product_ref.candidate_skus:
            return ProductFactEvidence(
                status=ProductFactStatus.AMBIGUOUS,
                request=request,
                verifier_status="not_run",
                reason_code=product_ref.reason_code,
            )
        products = [
            product
            for sku in product_ref.candidate_skus
            if (product := self._product(sku)) is not None
        ]
        if not products:
            return ProductFactEvidence(
                status=ProductFactStatus.NOT_FOUND,
                request=request,
                verifier_status="not_run",
                reason_code="resolved_product_missing_from_catalogue",
            )
        product_name = products[0].name if len(products) == 1 else "PP-FIBER PN 20"
        documents = self._shared_document_scope(products)

        if predicate == "selection_power_rationale":
            return ProductFactEvidence(
                status=ProductFactStatus.REJECTED,
                request=request,
                product_name=product_name,
                verifier_status="not_run",
                reason_code="sizing_rationale_is_not_a_product_passport_fact",
                document_scope=documents,
            )
        if predicate == "compatibility_boundary":
            return ProductFactEvidence(
                status=ProductFactStatus.REJECTED,
                request=request,
                product_name=product_name,
                verifier_status="not_run",
                reason_code="compatibility_requires_two_verified_interfaces",
                document_scope=documents,
            )

        if predicate == "integrated_circulation_pump":
            return self._integrated_circulation_pump_evidence(
                products,
                request,
                product_name=product_name,
                document_scope=documents,
            )

        spec = next((item for item in FACT_SPECS if item.predicate == predicate), None)
        if spec is None:
            # A normalized card fact is already tied to the exact product,
            # source field and canonical predicate.  It is a separate safe
            # evidence route from passports: no global retrieval and no LLM
            # answer are needed for a value explicit in the current feed card.
            card_evidence = self._catalog_card_evidence(
                products,
                request,
                product_name=product_name,
                document_scope=documents,
            )
            if card_evidence is not None:
                return card_evidence
            return ProductFactEvidence(
                status=ProductFactStatus.NOT_FOUND,
                request=request,
                product_name=product_name,
                verifier_status="not_run",
                reason_code="unsupported_product_fact_predicate",
                document_scope=documents,
            )
        attribute_entries = [self._attribute_entry(product, spec) for product in products]
        values = [entry[1] if entry is not None else None for entry in attribute_entries]
        present_values = [value for value in values if value is not None]
        consensus = None
        if len(present_values) == len(products):
            normalized = {_normalise(value) for value in present_values}
            if len(normalized) == 1:
                consensus = _display_value(present_values[0], predicate)

        passport = self.passport_service.answer(
            question,
            document_scope=documents,
            context=self._context(products),
            flow="v2_product_fact",
            predicate=predicate,
            canonical_sku=product_ref.canonical_sku,
        )
        if (
            passport.status == PassportEvidenceStatus.ANSWERED
            and passport.quote
            and consensus is not None
            and _matches_groups(_normalise(passport.quote), spec.quote_groups)
            and _quote_contains_value(passport.quote, consensus)
        ):
            record_passport_event(
                event="product_fact_evidence_gate",
                status="accepted",
                flow="v2_product_fact",
                predicate=predicate,
                canonical_sku=product_ref.canonical_sku,
                candidate_skus=list(product_ref.candidate_skus),
                document_scope=list(documents),
                source_document=passport.document,
                verifier_status=passport.verifier_status,
                reason="product_predicate_scope_and_value_match",
            )
            return ProductFactEvidence(
                status=ProductFactStatus.ANSWERED,
                request=request,
                product_name=product_name,
                value=consensus,
                unit=spec.unit,
                source_kind="passport_and_catalog_card",
                document=passport.document,
                section=passport.section,
                quote=passport.quote,
                verifier_status="accepted",
                reason_code="product_predicate_scope_and_value_match",
                document_scope=documents,
            )

        if consensus is not None:
            card_document = (
                self.settings.feed_file_path.name
                if self.settings.feed_file_path is not None
                else self.settings.products_cache_path.name
            )
            card_entry = next(entry for entry in attribute_entries if entry is not None)
            card_section = (
                f"товар {product_ref.canonical_sku}, attributes_normalized"
                if product_ref.canonical_sku
                else "серия PP-FIBER PN 20, attributes_normalized"
            )
            record_passport_event(
                event="product_fact_evidence_gate",
                status="accepted",
                flow="v2_product_fact",
                predicate=predicate,
                canonical_sku=product_ref.canonical_sku,
                candidate_skus=list(product_ref.candidate_skus),
                document_scope=list(documents),
                verifier_status="catalog_card_exact",
                passport_verifier_status=passport.verifier_status,
                reason="catalog_attribute_consensus",
                source_document=card_document,
                source_section=card_section,
                evidence_fragment=f"{card_entry[0]}: {card_entry[1]}",
            )
            return ProductFactEvidence(
                status=ProductFactStatus.ANSWERED,
                request=request,
                product_name=product_name,
                value=consensus,
                unit=spec.unit,
                source_kind="catalog_card",
                document=card_document,
                section=card_section,
                quote=f"{card_entry[0]}: {card_entry[1]}",
                verifier_status="catalog_card_exact",
                reason_code="catalog_attribute_consensus",
                document_scope=documents,
            )

        reason = (
            "catalogue_series_values_conflict"
            if present_values
            else passport.rejection_reason or "requested_fact_not_confirmed"
        )
        record_passport_event(
            event="product_fact_evidence_gate",
            status="rejected",
            flow="v2_product_fact",
            predicate=predicate,
            canonical_sku=product_ref.canonical_sku,
            candidate_skus=list(product_ref.candidate_skus),
            document_scope=list(documents),
            verifier_status=passport.verifier_status,
            reason=reason,
        )
        return ProductFactEvidence(
            status=ProductFactStatus.REJECTED,
            request=request,
            product_name=product_name,
            verifier_status=passport.verifier_status,
            reason_code=reason,
            document_scope=documents,
        )

    @staticmethod
    def _integrated_circulation_pump_evidence(
        products: list[Product],
        request: ProductFactRequest,
        *,
        product_name: str,
        document_scope: tuple[str, ...],
    ) -> ProductFactEvidence:
        """Answer one boiler-component question from exact, attached sources.

        This does not use absence of a document phrase as an absence verdict.
        A mapped document is inspected only for the resolved product and wins
        as the displayed source when it agrees with the current catalogue card.
        """

        if len(products) != 1:
            return ProductFactEvidence(
                status=ProductFactStatus.AMBIGUOUS,
                request=request,
                product_name=product_name,
                verifier_status="not_run",
                reason_code="integrated_pump_requires_single_product",
                document_scope=document_scope,
            )
        evidence = builtin_part_evidence(products[0], "насос")
        if evidence.source_conflict:
            return ProductFactEvidence(
                status=ProductFactStatus.REJECTED,
                request=request,
                product_name=product_name,
                verifier_status="source_conflict",
                reason_code="integrated_pump_card_document_conflict",
                document_scope=document_scope,
            )
        if evidence.state is None:
            return ProductFactEvidence(
                status=ProductFactStatus.NOT_FOUND,
                request=request,
                product_name=product_name,
                verifier_status="not_run",
                reason_code="integrated_pump_not_explicitly_confirmed",
                document_scope=document_scope,
            )
        return ProductFactEvidence(
            status=ProductFactStatus.ANSWERED,
            request=request,
            product_name=product_name,
            value="есть" if evidence.state else "нет",
            source_kind=(
                "passport_document_exact"
                if evidence.source_kind == "passport"
                else "catalog_card_exact"
            ),
            document=evidence.document,
            section=evidence.section,
            quote=evidence.excerpt,
            verifier_status=(
                "document_text_exact"
                if evidence.source_kind == "passport"
                else "catalog_card_exact"
            ),
            reason_code="integrated_pump_explicit_component_evidence",
            document_scope=document_scope,
        )

    def resolve_reference(
        self,
        question: str,
        session: SessionState,
    ) -> ProductReference:
        text = _normalise(question)
        # Exact numeric articles are valid catalogue identities even when a
        # customer does not introduce them with the word "артикул".  The
        # shared extractor only returns candidates; ``resolve_catalog_sku``
        # remains the authority that proves an exact/unique identity.
        sku_tokens = list(extract_explicit_sku_tokens(question))
        resolved = []
        ambiguous = []
        for token in dict.fromkeys(sku_tokens):
            result = resolve_catalog_sku(token, self.products)
            if result.status in {
                SkuResolutionStatus.EXACT,
                SkuResolutionStatus.UNIQUE_PREFIX,
            }:
                resolved.append((token, result))
            elif result.status == SkuResolutionStatus.AMBIGUOUS_PREFIX:
                ambiguous.append((token, result))
        if len(resolved) == 1:
            token, result = resolved[0]
            return ProductReference(
                kind=(
                    ProductReferenceKind.EXACT_SKU
                    if result.status == SkuResolutionStatus.EXACT
                    else ProductReferenceKind.PARTIAL_SKU
                ),
                raw=token,
                canonical_sku=result.canonical_sku,
                candidate_skus=tuple(item.sku for item in result.candidates),
                reason_code=(
                    "explicit_exact_sku"
                    if result.status == SkuResolutionStatus.EXACT
                    else "explicit_unique_partial_sku"
                ),
            )
        if len(resolved) > 1:
            candidates = tuple(
                dict.fromkeys(item.sku for _, result in resolved for item in result.candidates)
            )
            return ProductReference(
                kind=ProductReferenceKind.UNRESOLVED,
                raw="; ".join(token for token, _ in resolved),
                candidate_skus=candidates,
                reason_code="multiple_explicit_product_references",
            )
        if ambiguous:
            token, result = ambiguous[0]
            return ProductReference(
                kind=ProductReferenceKind.UNRESOLVED,
                raw=token,
                candidate_skus=tuple(item.sku for item in result.candidates),
                reason_code="ambiguous_partial_sku",
            )

        named = self._strict_named_product_reference(question)
        if named is not None:
            return named

        ordinal = self._ordinal(text)
        cards = list(session.last_products or session.v2_last_products)
        if ordinal is not None:
            if 0 <= ordinal < len(cards):
                card = cards[ordinal]
                return ProductReference(
                    kind=ProductReferenceKind.ORDINAL,
                    raw=str(ordinal + 1),
                    canonical_sku=card.sku,
                    candidate_skus=(card.sku,),
                    reason_code="ordinal_in_customer_visible_cards",
                )
            return ProductReference(
                kind=ProductReferenceKind.UNRESOLVED,
                raw=str(ordinal + 1),
                reason_code="ordinal_outside_customer_visible_cards",
            )

        if "pp-fiber" in text or "pp fiber" in text:
            wants_pn20 = bool(re.search(r"\bpn\s*20\b", text))
            candidates = self._pp_fiber_candidates(wants_pn20=wants_pn20)
            if candidates:
                return ProductReference(
                    kind=ProductReferenceKind.NAMED_SERIES,
                    raw="PP-FIBER PN 20" if wants_pn20 else "PP-FIBER",
                    candidate_skus=candidates,
                    reason_code="named_catalogue_series",
                )

        # A named series answer has no single honest SKU/card to put into
        # ``last_products``.  Preserve only the immediately preceding explicit
        # user reference so a natural follow-up ("А какое давление ...?") does
        # not lose its product scope.  Looking farther back would make a stale
        # topic indistinguishable from the current focus.
        previous_user_message = next(
            (
                str(item.get("content") or item.get("message") or "")
                for item in reversed(session.history)
                if str(item.get("role") or "").casefold() == "user"
            ),
            "",
        )
        previous_text = _normalise(previous_user_message)
        if "pp-fiber" in previous_text or "pp fiber" in previous_text:
            wants_pn20 = bool(re.search(r"\bpn\s*20\b", previous_text))
            candidates = self._pp_fiber_candidates(wants_pn20=wants_pn20)
            if candidates:
                return ProductReference(
                    kind=ProductReferenceKind.CURRENT_FOCUS,
                    raw="PP-FIBER PN 20" if wants_pn20 else "PP-FIBER",
                    candidate_skus=candidates,
                    reason_code="immediate_named_series_focus",
                )

        if session.product_focus is not None:
            sku = session.product_focus.sku
            if self._product(sku) is not None:
                return ProductReference(
                    kind=ProductReferenceKind.CURRENT_FOCUS,
                    raw=sku,
                    canonical_sku=sku,
                    candidate_skus=(sku,),
                    reason_code="current_product_focus",
                )
        if len(cards) == 1 and self._has_deictic_reference(text):
            return ProductReference(
                kind=ProductReferenceKind.SINGLE_PRESENTED,
                raw=cards[0].sku,
                canonical_sku=cards[0].sku,
                candidate_skus=(cards[0].sku,),
                reason_code="single_customer_visible_card",
            )
        return ProductReference(
            kind=ProductReferenceKind.UNRESOLVED,
            reason_code="product_reference_not_grounded",
        )

    def _strict_named_product_reference(
        self,
        question: str,
    ) -> ProductReference | None:
        """Resolve a brand + model only when it selects one feed product.

        This is intentionally narrower than a catalogue search: a brand on
        its own ("Arderia") does not become a product reference, and a model
        token may not choose a product unless the same explicit brand is also
        present.  It lets a natural fact question reach the existing evidence
        service without inventing a new fuzzy search path.
        """

        normalized = _normalise(question)
        model_markers = tuple(
            _normalise(match.group(0)).replace(" ", "")
            for match in _MODEL_MARKER_RE.finditer(question)
        )
        if not model_markers:
            return None

        candidates: list[Product] = []
        for product in self.products:
            brand = _normalise(product.brand or "")
            if not brand or brand not in normalized:
                continue
            product_name = _normalise(product.name).replace(" ", "")
            if any(marker and marker in product_name for marker in model_markers):
                candidates.append(product)
        unique = tuple({item.sku: item for item in candidates}.values())
        if len(unique) != 1:
            return None
        product = unique[0]
        return ProductReference(
            kind=ProductReferenceKind.NAMED_PRODUCT,
            raw=question[:240],
            canonical_sku=product.sku,
            candidate_skus=(product.sku,),
            reason_code="strict_brand_model_catalogue_match",
        )

    @staticmethod
    def _predicate(question: str, semantic_fact_name: str | None) -> str | None:
        text = _normalise(question)
        if "почему" in text and "мощн" in text:
            return "selection_power_rationale"
        if any(marker in text for marker in ("подойдет", "подойдёт", "совместим")):
            return "compatibility_boundary"
        if "насос" in text and any(
            marker in text
            for marker in (
                "встроен",
                "внутри",
                "в котле",
                "в этом",
                "комплект",
            )
        ):
            return "integrated_circulation_pump"
        if (
            "по монтаж" in text
            or "между присоедин" in text
            or any(
                _normalise(alias) in text
                for alias in fact_aliases(
                    "circulation_pump",
                    "mounting_length_mm",
                )
            )
            or ("миллиметр" in text and "насос" in text and "длин" in text)
        ):
            return "installation_length_mm"
        semantic = _SEMANTIC_PREDICATE_ALIASES.get(
            _normalise(semantic_fact_name).replace(" ", "_")
        )
        if semantic:
            return semantic
        for spec in FACT_SPECS:
            if _matches_groups(text, spec.question_groups):
                return spec.predicate
        return None

    def _catalog_card_predicate(
        self,
        text: str,
        *,
        semantic_fact_name: str | None,
        product_ref: ProductReference,
    ) -> str | None:
        """Resolve only a registered predicate present on the scoped card(s)."""

        snapshots = tuple(
            item
            for sku in product_ref.candidate_skus
            if (item := self.catalog_by_sku.get(sku)) is not None
        )
        if not snapshots:
            return None
        shared = set.intersection(
            *(
                {
                    fact.name
                    for fact in snapshot.facts
                    if self._exact_catalog_fact(snapshot, fact.name) is not None
                }
                for snapshot in snapshots
            )
        )
        normalized_semantic = _normalise(semantic_fact_name).replace(" ", "_")
        if normalized_semantic in shared and normalized_semantic not in {"sku", "brand"}:
            return normalized_semantic

        candidates: set[str] = set()
        for snapshot in snapshots:
            for predicate in shared:
                if predicate in {"sku", "brand"}:
                    continue
                aliases = tuple(
                    _normalise(alias)
                    for alias in fact_aliases(snapshot.product_kind.value, predicate)
                    if _normalise(alias)
                )
                if any(alias in text for alias in aliases):
                    candidates.add(predicate)
        return next(iter(candidates)) if len(candidates) == 1 else None

    @staticmethod
    def _exact_catalog_fact(snapshot: CatalogProductSnapshot, predicate: str):
        if any(item.name == predicate for item in snapshot.fact_issues):
            return None
        facts = tuple(item for item in snapshot.facts if item.name == predicate)
        distinct = {(str(item.value), item.unit) for item in facts}
        return facts[0] if facts and len(distinct) == 1 else None

    def _catalog_card_evidence(
        self,
        products: list[Product],
        request: ProductFactRequest,
        *,
        product_name: str,
        document_scope: tuple[str, ...],
    ) -> ProductFactEvidence | None:
        entries = []
        for product in products:
            snapshot = self.catalog_by_sku.get(product.sku)
            if snapshot is None:
                return None
            fact = self._exact_catalog_fact(snapshot, request.predicate)
            if fact is None:
                return None
            entries.append((snapshot, fact))
        if not entries:
            return None
        distinct = {(str(fact.value), fact.unit) for _, fact in entries}
        if len(distinct) != 1:
            return None
        snapshot, fact = entries[0]
        document = (
            self.settings.feed_file_path.name
            if self.settings.feed_file_path is not None
            else self.settings.products_cache_path.name
        )
        section = f"товар {snapshot.sku}, {fact.provenance.source_field}"
        quote = f"{fact.provenance.source_field}: {fact.provenance.raw_value}"
        record_passport_event(
            event="product_fact_evidence_gate",
            status="accepted",
            flow="v2_product_fact",
            predicate=request.predicate,
            canonical_sku=request.product_ref.canonical_sku,
            candidate_skus=list(request.product_ref.candidate_skus),
            document_scope=list(document_scope),
            verifier_status="catalog_snapshot_exact",
            reason="catalog_snapshot_predicate_scope_and_value_match",
            source_document=document,
            source_section=section,
            evidence_fragment=quote,
        )
        return ProductFactEvidence(
            status=ProductFactStatus.ANSWERED,
            request=request,
            product_name=product_name,
            value=fact.value,
            unit=fact.unit,
            source_kind="catalog_card",
            document=document,
            section=section,
            quote=quote,
            verifier_status="catalog_snapshot_exact",
            reason_code="catalog_snapshot_predicate_scope_and_value_match",
            document_scope=document_scope,
        )

    @staticmethod
    def _ordinal(text: str) -> int | None:
        markers = (
            (("перв", "1-й", "1го", "номер 1"), 0),
            (("втор", "2-й", "2го", "номер 2"), 1),
            (("трет", "3-й", "3го", "номер 3"), 2),
            (("четверт", "4-й", "4го", "номер 4"), 3),
            (("пят", "5-й", "5го", "номер 5"), 4),
        )
        for aliases, index in markers:
            if any(alias in text for alias in aliases):
                return index
        return None

    @staticmethod
    def _has_deictic_reference(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                " у него",
                " у нее",
                " у неё",
                " этого ",
                " этой ",
                " этот ",
                " его ",
                " ее ",
                " её ",
            )
        )

    @staticmethod
    def _looks_like_unmapped_product_fact(
        question: str,
        product_ref: ProductReference,
    ) -> bool:
        text = _normalise(question)
        if any(
            marker in text
            for marker in (
                "сравн",
                "лучше",
                "дешев",
                "дороже",
                "выбрать",
                "подойдет",
                "подойдёт",
                "совместим",
                "почему",
            )
        ):
            return False
        has_product_reference = bool(
            product_ref.candidate_skus
            or product_ref.raw
            # Keep this guard aligned with the one shared SKU extractor used
            # by resolution above.  A syntactic token still needs catalogue
            # resolution before it can become a product reference; it merely
            # tells this bounded classifier that the user tried to name one.
            or extract_explicit_sku_tokens(question)
        )
        return has_product_reference and bool(
            re.search(
                r"(?:\bкак(?:ой|ая|ое|ие)\b|\bсколько\b|\bесть\s+ли\b|\bу\s+него\b|\bу\s+не[её]\b)",
                text,
            )
        )

    def _product(self, sku: str) -> Product | None:
        return next((item for item in self.products if item.sku == sku), None)

    def _pp_fiber_candidates(self, *, wants_pn20: bool) -> tuple[str, ...]:
        return tuple(
            product.sku
            for product in self.products
            if "pp-fiber" in _normalise(product.name)
            and (not wants_pn20 or "pn 20" in _normalise(product.name))
        )

    @staticmethod
    def _attribute_entry(product: Product, spec: FactSpec) -> tuple[str, str] | None:
        attributes = {
            _normalise(key): (str(key).strip(), str(value).strip())
            for key, value in product.attributes_normalized.items()
        }
        for key in spec.attribute_keys:
            if _normalise(key) in attributes:
                return attributes[_normalise(key)]
        return None

    @staticmethod
    def _shared_document_scope(products: list[Product]) -> tuple[str, ...]:
        document_sets = [
            {document.filename for document in product.documents if document.filename}
            for product in products
        ]
        if not document_sets:
            return ()
        shared = set.intersection(*document_sets)
        return tuple(sorted(shared))

    @staticmethod
    def _context(products: list[Product]) -> str:
        return "; ".join(f"{item.sku} — {item.name}" for item in products[:8])


def render_product_fact_evidence(evidence: ProductFactEvidence) -> str:
    """Deterministic renderer; it consumes only the checked typed result."""

    predicate = evidence.request.predicate
    spec = next((item for item in FACT_SPECS if item.predicate == predicate), None)
    reference = evidence.request.product_ref
    subject = evidence.product_name or reference.canonical_sku or "указанный товар"
    if reference.canonical_sku and reference.canonical_sku not in subject:
        subject = f"{subject} ({reference.canonical_sku})"

    if evidence.status == ProductFactStatus.ANSWERED and evidence.value is not None:
        label = spec.label if spec else _CARD_FACT_LABELS.get(
            predicate, predicate.replace("_", " ")
        )
        value = str(evidence.value)
        suffix = f" {evidence.unit}" if evidence.unit else ""
        answer = f"{subject}. {label.capitalize()} — {value}{suffix}."
        if (
            evidence.quote
            and evidence.document
            and evidence.source_kind == "passport_and_catalog_card"
        ):
            answer += f" По паспорту: «{evidence.quote}»"
            source = evidence.document
            if evidence.section:
                source += f", {evidence.section.rstrip('.')}"
            answer += f". Источник: {source}."
        elif (
            evidence.quote
            and evidence.document
            and evidence.source_kind == "passport_document_exact"
        ):
            answer += f" В привязанной документации: «{evidence.quote}»"
            source = evidence.document
            if evidence.section:
                source += f", {evidence.section.rstrip('.')}"
            answer += f". Источник: {source}."
        elif evidence.quote and evidence.document:
            answer += f" В карточке: «{evidence.quote}»."
            source = evidence.document
            if evidence.section:
                source += f", {evidence.section.rstrip('.')}"
            answer += f" Источник: {source}."
        else:
            answer += " Источник: проверенная карточка товара в текущем фиде."
        return answer

    if predicate == "selection_power_rationale":
        subject_phrase = (
            "Для указанного товара"
            if subject == "указанный товар"
            else f"Для товара {subject}"
        )
        return (
            f"{subject_phrase} паспорт может подтвердить характеристики самой модели, "
            "но не обоснование мощности для конкретного дома. Для такого вывода нужны "
            "расчётные теплопотери, климат, утепление и запас на ГВС. Поэтому не буду "
            "подменять расчёт посторонней характеристикой из паспорта."
        )
    if predicate == "compatibility_boundary" and reference.canonical_sku:
        return (
            f"Артикул {reference.raw} однозначно распознан как "
            f"{reference.canonical_sku} — {evidence.product_name}. Сам факт наличия "
            "товара не доказывает совместимость: для ответа нужно отдельно подтвердить "
            "интерфейсы обоих изделий. Без этого совместимость обещать не буду."
        )
    if evidence.status == ProductFactStatus.AMBIGUOUS:
        if reference.candidate_skus:
            variants = ", ".join(reference.candidate_skus[:6])
            return (
                f"Не могу однозначно определить товар для этой характеристики. "
                f"Подходящие артикулы: {variants}. Укажите точный артикул или номер "
                "ранее показанной карточки."
            )
        return (
            "Не могу однозначно определить, о каком товаре задан вопрос. Укажите "
            "артикул или номер ранее показанной карточки; без product scope искать "
            "ответ по всем паспортам небезопасно."
        )
    label = (
        spec.label
        if spec
        else (
            "запрошенная характеристика"
            if predicate == "unsupported_product_fact"
            else predicate.replace("_", " ")
        )
    )
    return (
        f"По товару {subject} не удалось подтвердить характеристику «{label}» "
        "в карточке и привязанной документации. Не буду подставлять типовое или "
        "похожее значение без источника именно для этого товара."
    )
