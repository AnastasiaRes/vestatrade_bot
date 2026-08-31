from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.contracts import CatalogFact, CatalogProductRole, FactProvenance, ProductKind
from app.comparison_v2.contracts import ComparisonResultStatus, ComparisonSourceKind
from app.comparison_v2.renderer import render_comparison_result
from app.comparison_v2.service import (
    _passport_quote_contains_value,
    build_comparison_request,
    build_comparison_result,
    validate_comparison_result,
)
from app.cutover_v2.comparison import build_v2_comparison_candidate
from app.cutover_v2.contracts import (
    CutoverDecision,
    ExecutionMode,
    ResponseOwner,
    V2TurnCandidate,
)
from app.agents.orchestrator import ChatOrchestrator
from app.config import get_settings
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    AnswerPlanSummary,
    NextAction,
    NextActionKind,
    NextActionPlan,
    PresentedCandidateSummary,
    ShadowDeliveryStatus,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.dialogue_v2.seller_policy import SellerPolicy
from app.models import ProductCard, ProductFocusState, SessionState
from app.semantic_v2.contracts import SemanticProductReference
from app.models import Product
from app.product_fact_evidence import (
    ProductFactEvidence,
    ProductFactRequest,
    ProductFactStatus,
    ProductReference,
    ProductReferenceKind,
)


def _fact(name: str, value: object, unit: str | None = None) -> CatalogFact:
    return CatalogFact(
        name=name,
        value=value,  # type: ignore[arg-type]
        unit=unit,
        provenance=FactProvenance(
            source="attribute",
            source_field=name,
            raw_value=str(value),
            parser="test",
        ),
    )


def _product(
    sku: str,
    *,
    price: float,
    length: int,
    stock: str = "в наличии",
    document_scope: tuple[str, ...] = (),
    facts: tuple[CatalogFact, ...] | None = None,
) -> CatalogAnswerProduct:
    return CatalogAnswerProduct(
        sku=sku,
        name=f"Насос {sku}",
        product_kind=ProductKind.CIRCULATION_PUMP,
        role=CatalogProductRole.BASE_PRODUCT,
        price=price,
        currency="RUB",
        stock_status=stock,
        stock_qty=10,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        document_scope=document_scope,
        facts=facts if facts is not None else (_fact("installation_length_mm", length, "мм"),),
    )


def _snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product("PUMP-180", price=5000, length=180),
            _product("PUMP-130", price=4500, length=130, stock="под заказ"),
        ),
    )


def _five_product_snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="source-v1",
        products=tuple(
            _product(
                f"PUMP-{index}",
                price=4000 + index * 100,
                length=120 + index * 10,
            )
            for index in range(1, 6)
        ),
    )


def _card(product: CatalogAnswerProduct) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        price=product.price or 0,
        currency=product.currency or "RUB",
        stock_status=product.stock_status or "",
        stock_qty=product.stock_qty,
        url=product.url or "",
        image_url=product.image_url,
    )


def _outcome(*, selection_id: str = "selection-v1") -> DialogueV2Outcome:
    summary = AnswerPlanSummary(
        plan_id="shown-plan",
        semantic_signature="shown-signature",
        task_ids=("selection-task",),
        primary_action=NextActionKind.SHOW_PRELIMINARY_OPTIONS,
        next_step_kind="show_preliminary_options",
        validation_status="accepted",
        delivery_status=ShadowDeliveryStatus.COMMITTED_TO_SESSION,
        selection_id=selection_id,
        catalog_revision="source-v1",
        presented_candidates=(
            PresentedCandidateSummary(
                sku="PUMP-180",
                name="Насос PUMP-180",
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                task_id="selection-task",
                goal_id="goal-pump",
                search_plan_id="search-v1",
                source_turn=1,
            ),
            PresentedCandidateSummary(
                sku="PUMP-130",
                name="Насос PUMP-130",
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                task_id="selection-task",
                goal_id="goal-pump",
                search_plan_id="search-v1",
                source_turn=1,
            ),
        ),
        source_turn=1,
    )
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="compare-task",
                act=TaskAct.COMPARE,
                target_goal_id="goal-pump",
                priority=0,
                source="semantic_interpreter",
                source_turn=2,
            ),
        ),
        answer_plan_summary=summary,
    )
    return DialogueV2Outcome(
        status="applied",
        state_before=state.model_copy(update={"turn_number": 1}),
        state_after=state,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.COMPARE,
                task_id="compare-task",
                reason_code="comparison_requested",
            ),
            task_ids=("compare-task",),
        ),
    )


def _session(snapshot: AnswerSourceSnapshot) -> SessionState:
    return SessionState(
        session_id="compare",
        v2_last_products=[_card(item) for item in snapshot.products],
        last_products=[_card(item) for item in snapshot.products],
        v2_selection_id="selection-v1",
        v2_source_revision=snapshot.source_revision,
    )


def _base_candidate(outcome: DialogueV2Outcome) -> V2TurnCandidate:
    return V2TurnCandidate(
        turn_id="compare-turn",
        state_before=outcome.state_before,
        state_after=outcome.state_after,
        validation_status="not_run",
    )


class _PassportComparisonEvidence:
    """Typed stand-in for the existing ProductFact evidence seam."""

    def __init__(
        self,
        values: dict[str, tuple[object, str | None, str, str]],
        *,
        conflict_skus: tuple[str, ...] = (),
    ) -> None:
        self.values = values
        self.conflict_skus = set(conflict_skus)
        self.calls: list[tuple[str, str]] = []

    def evaluate_exact_product(self, *, sku: str, predicate: str) -> ProductFactEvidence:
        self.calls.append((sku, predicate))
        reference = ProductReference(
            kind=ProductReferenceKind.EXACT_SKU,
            raw=sku,
            canonical_sku=sku,
            candidate_skus=(sku,),
            reason_code="comparison_exact_visible_sku",
        )
        request = ProductFactRequest(
            question=f"Какая {predicate} у {sku}?",
            predicate=predicate,
            product_ref=reference,
        )
        if sku in self.conflict_skus:
            return ProductFactEvidence(
                status=ProductFactStatus.REJECTED,
                request=request,
                verifier_status="source_conflict",
                reason_code="catalogue_document_value_conflict",
            )
        if sku not in self.values:
            return ProductFactEvidence(
                status=ProductFactStatus.NOT_FOUND,
                request=request,
                verifier_status="not_run",
                reason_code="passport_evidence_not_found",
            )
        value, unit, document, quote = self.values[sku]
        return ProductFactEvidence(
            status=ProductFactStatus.ANSWERED,
            request=request,
            product_name=f"Насос {sku}",
            value=value,  # type: ignore[arg-type]
            unit=unit,
            source_kind="passport_document_exact",
            document=document,
            section="таблица характеристик",
            quote=quote,
            verifier_status="accepted",
            reason_code="passport_exact_control_thread_value_match",
            document_scope=(document,),
        )


def test_passport_quote_numeric_gate_does_not_read_eight_inside_eighteen() -> None:
    assert _passport_quote_contains_value(
        "Расширительный бак: 8 литров.", 8, "л"
    )
    assert not _passport_quote_contains_value(
        "Расширительный бак: 18 литров.", 8, "л"
    )


def test_compare_reads_only_visible_scope_and_proves_price_and_length() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)

    request = build_comparison_request(
        outcome, session, original_utterance="Сравните их по монтажной длине"
    )
    assert request is not None
    assert request.ordered_skus == ("PUMP-180", "PUMP-130")
    assert request.selection_id == "selection-v1"
    assert request.requested_predicates == ("installation_length_mm",)

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert {item.predicate for item in result.dimensions} >= {
        "price",
        "availability",
        "installation_length_mm",
    }
    length = next(item for item in result.dimensions if item.predicate == "installation_length_mm")
    assert [item.value for item in length.values] == [180, 130]
    assert all(item.source_ref_ids for item in length.values)
    assert all(item.sku in request.ordered_skus for item in result.sources)


def test_compare_resolves_product_fact_alias_through_catalog_registry() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product(
                "PUMP-180",
                price=5000,
                length=180,
                facts=(_fact("mounting_length_mm", 180, "мм"),),
            ),
            _product(
                "PUMP-130",
                price=4500,
                length=130,
                facts=(_fact("mounting_length_mm", 130, "мм"),),
            ),
        ),
    )
    session = _session(snapshot)
    request = build_comparison_request(
        _outcome(),
        session,
        original_utterance="Сравните их по монтажной длине",
    )

    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(
            request,
            snapshot,
            visible_cards=session.v2_last_products,
        ),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    length = next(
        item for item in result.dimensions
        if item.predicate == "installation_length_mm"
    )
    assert [item.value for item in length.values] == [180, 130]
    assert all(item.source_ref_ids for item in length.values)


def test_compare_can_read_two_strictly_named_catalog_models_without_selection_scope() -> None:
    products = (
        CatalogAnswerProduct(
            sku="2201376",
            name="Котел газовый настенный Arderia SB28 28 кВт",
            product_kind=ProductKind.GAS_BOILER,
            role=CatalogProductRole.BASE_PRODUCT,
            price=91_000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            url="https://example.test/2201376",
            facts=(_fact("brand", "Arderia"), _fact("power_kw", 28, "кВт")),
        ),
        CatalogAnswerProduct(
            sku="2201377",
            name="Котел газовый настенный Arderia SB32 32 кВт",
            product_kind=ProductKind.GAS_BOILER,
            role=CatalogProductRole.BASE_PRODUCT,
            price=99_000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=1,
            url="https://example.test/2201377",
            facts=(_fact("brand", "Arderia"), _fact("power_kw", 32, "кВт")),
        ),
    )
    snapshot = AnswerSourceSnapshot(source_revision="source-v1", products=products)
    request = build_comparison_request(
        _outcome(),
        SessionState(session_id="named-pair"),
        original_utterance="Чем Arderia SB28 отличается от Arderia SB32?",
        source_snapshot=snapshot,
    )

    assert request is not None
    assert request.scope_origin == "explicit_catalog_pair"
    assert request.selection_id is None
    assert request.ordered_skus == ("2201376", "2201377")
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=()),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert "comparison_from_explicit_catalog_pair" in result.reason_codes
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in products},
    )
    assert "Сравнение названных моделей" in rendered
    assert "2201376" in rendered and "2201377" in rendered


def test_explicitly_requested_equal_price_and_stock_are_still_shown() -> None:
    products = (
        CatalogAnswerProduct(
            sku="2201376",
            name="Котел газовый Arderia SB28 28 кВт",
            product_kind=ProductKind.GAS_BOILER,
            role=CatalogProductRole.BASE_PRODUCT,
            price=38_535,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            url="https://example.test/2201376",
            facts=(_fact("brand", "Arderia"), _fact("power_kw", 28, "кВт")),
        ),
        CatalogAnswerProduct(
            sku="2201377",
            name="Котел газовый Arderia SB32 32 кВт",
            product_kind=ProductKind.GAS_BOILER,
            role=CatalogProductRole.BASE_PRODUCT,
            price=38_535,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            url="https://example.test/2201377",
            facts=(_fact("brand", "Arderia"), _fact("power_kw", 32, "кВт")),
        ),
    )
    snapshot = AnswerSourceSnapshot(source_revision="source-v1", products=products)
    request = build_comparison_request(
        _outcome(),
        SessionState(session_id="named-equal-offer-facts"),
        original_utterance=(
            "Сравните Arderia SB28 и Arderia SB32 по мощности, цене и наличию."
        ),
        source_snapshot=snapshot,
    )

    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=()),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert {item.predicate for item in result.dimensions} >= {
        "power_kw",
        "price",
        "availability",
    }
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in products},
    )
    assert "38535" in rendered
    assert "налич" in rendered.casefold()


def test_compare_returns_proved_requested_fields_and_marks_one_missing_field() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            _product(
                "PUMP-A",
                price=5000,
                length=180,
                facts=(
                    _fact("installation_length_mm", 180, "мм"),
                    _fact("max_head_m", 4, "м"),
                ),
            ),
            _product(
                "PUMP-B",
                price=4500,
                length=130,
                facts=(_fact("max_head_m", 6, "м"),),
            ),
        ),
    )
    session = _session(snapshot)
    request = build_comparison_request(
        _outcome(),
        session,
        original_utterance=(
            "Сравни их по цене, максимальному напору и монтажной длине."
        ),
    )

    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(
            request,
            snapshot,
            visible_cards=session.v2_last_products,
        ),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert "installation_length_mm" in result.missing_data
    assert "comparison_partial_requested_predicates" in result.reason_codes
    assert {item.predicate for item in result.dimensions} >= {
        "price",
        "max_head_m",
        "installation_length_mm",
    }


def test_compare_resolves_any_two_named_ordinals_inside_visible_scope() -> None:
    snapshot = _five_product_snapshot()
    outcome = _outcome()
    session = _session(snapshot)

    first_third = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравни первый и третий по цене.",
    )
    second_fourth = build_comparison_request(
        outcome,
        session,
        original_utterance="Сопоставь второй с четвёртым по напору.",
    )
    numeric_pair = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравни 1 и 4 варианты по цене.",
    )

    assert first_third is not None
    assert first_third.ordered_skus == ("PUMP-1", "PUMP-3")
    assert second_fourth is not None
    assert second_fourth.ordered_skus == ("PUMP-2", "PUMP-4")
    assert numeric_pair is not None
    assert numeric_pair.ordered_skus == ("PUMP-1", "PUMP-4")


def test_compare_resolves_one_ordinal_and_current_focus_without_widening_scope() -> None:
    snapshot = _five_product_snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    session.product_focus = ProductFocusState(sku="PUMP-4", category="pumps")

    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравни первый и этот по цене.",
    )

    assert request is not None
    assert request.ordered_skus == ("PUMP-1", "PUMP-4")
    assert [item.kind.value for item in request.product_references] == [
        "ordinal",
        "current_focus",
    ]


def test_semantic_reference_candidate_must_be_source_spanned_and_resolves_only_visible_pair() -> None:
    snapshot = _five_product_snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    references = (
        SemanticProductReference(
            kind="ordinal",
            text="позицию один",
            evidence="позицию один",
        ),
        SemanticProductReference(
            kind="ordinal",
            text="позицию четыре",
            evidence="позицию четыре",
        ),
    )

    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сопоставь позицию один и позицию четыре.",
        semantic_references=references,
    )

    assert request is not None
    assert request.ordered_skus == ("PUMP-1", "PUMP-4")
    visible_skus = {item.sku for item in session.v2_last_products}
    assert all(item.canonical_sku in visible_skus for item in request.product_references)


def test_compare_does_not_widen_an_out_of_scope_explicit_reference_to_all_cards() -> None:
    snapshot = _five_product_snapshot()
    outcome = _outcome()
    session = _session(snapshot)

    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравни первый и шестой варианты.",
    )

    assert request is not None
    assert request.ordered_skus == ("PUMP-1",)
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    assert result.status == ComparisonResultStatus.NEED_CLARIFICATION
    assert "ordinal_outside_customer_visible_v2_scope" in result.reason_codes
    assert "нет одной из названных позиций" in render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )


def test_cheapest_result_is_a_proved_price_conclusion_not_free_recommendation() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(
        outcome, session, original_utterance="Какой из показанных дешевле?"
    )
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.outcome_gate_passed is True
    assert result.recommendation is not None
    assert result.recommendation.sku == "PUMP-130"
    assert result.recommendation.reason_code == "lowest_confirmed_price"
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )
    assert "дешевле Насос PUMP-130 (PUMP-130) — 4500 ₽" in rendered
    assert "None" not in rendered
    assert "Какой критерий" not in rendered


def test_plain_compare_hides_identity_dimension_without_forcing_a_buying_criterion() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )

    assert result.outcome_gate_passed is True
    assert "• Sku:" not in rendered
    assert "None" not in rendered
    assert "Какой критерий для вас решающий" not in rendered


def test_decision_without_criterion_shows_differences_then_asks_one_question() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Что из показанных лучше?",
    )
    assert request is not None
    assert request.needs_deciding_criterion is True

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )

    assert result.outcome_gate_passed is True
    assert "Какой критерий для вас решающий: цена, наличие, монтажная длина?" in rendered


def test_two_ordinals_compare_only_the_two_cards_named_by_the_buyer() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            *_snapshot().products,
            _product("PUMP-250", price=7000, length=250),
        ),
    )
    outcome = _outcome()
    session = _session(snapshot)
    session.v2_last_products = [_card(item) for item in snapshot.products]
    session.last_products = [_card(item) for item in snapshot.products]

    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Чем первый отличается от второго?",
    )
    assert request is not None
    assert request.ordered_skus == ("PUMP-180", "PUMP-130")
    assert request.needs_deciding_criterion is False

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    rendered = render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )

    assert result.outcome_gate_passed is True
    assert "PUMP-250" not in rendered
    assert "Какой критерий для вас решающий" not in rendered


def test_plural_pair_reference_compares_only_the_first_two_visible_cards() -> None:
    snapshot = AnswerSourceSnapshot(
        source_revision="source-v1",
        products=(
            *_snapshot().products,
            _product("PUMP-250", price=7000, length=250),
        ),
    )
    outcome = _outcome()
    session = _session(snapshot)

    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Чем отличаются первые два?",
    )

    assert request is not None
    assert request.ordered_skus == ("PUMP-180", "PUMP-130")
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    assert result.status == ComparisonResultStatus.COMPARED
    assert result.compared_skus == ("PUMP-180", "PUMP-130")


def test_explicit_passport_predicate_fills_only_snapshot_gap_for_visible_skus() -> None:
    left = _product(
        "HEAD-30",
        price=1000,
        length=180,
        document_scope=("head-30.pdf",),
        facts=(),
    )
    right = _product(
        "HEAD-28",
        price=1100,
        length=180,
        document_scope=("head-28.pdf",),
        facts=(),
    )
    snapshot = AnswerSourceSnapshot(source_revision="source-v1", products=(left, right))
    outcome = _outcome()
    session = _session(snapshot)
    evidence = _PassportComparisonEvidence(
        {
            "HEAD-30": (
                "M30×1,5",
                None,
                "head-30.pdf",
                "Посадочная резьба термоголовки M30×1,5.",
            ),
            "HEAD-28": (
                "M28×1,5",
                None,
                "head-28.pdf",
                "Посадочная резьба термоголовки M28×1,5.",
            ),
        }
    )
    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравните их по резьбе под термоголовку.",
    )

    assert request is not None
    assert request.requested_predicates == ("thermostatic_head_thread",)
    result = validate_comparison_result(
        request,
        build_comparison_result(
            request,
            snapshot,
            visible_cards=session.v2_last_products,
            product_fact_evidence=evidence,  # type: ignore[arg-type]
        ),
        snapshot,
    )

    dimension = next(
        item for item in result.dimensions if item.predicate == "thermostatic_head_thread"
    )
    assert result.status == ComparisonResultStatus.COMPARED
    assert result.outcome_gate_passed is True
    assert [item.value for item in dimension.values] == ["M30×1,5", "M28×1,5"]
    assert evidence.calls == [
        ("HEAD-30", "thermostatic_head_thread"),
        ("HEAD-28", "thermostatic_head_thread"),
    ]
    passport_sources = [
        source
        for source in result.sources
        if source.source_kind == ComparisonSourceKind.PASSPORT_DOCUMENT_EXACT
    ]
    assert {source.document for source in passport_sources} == {
        "head-30.pdf",
        "head-28.pdf",
    }
    assert all(source.quote for source in passport_sources)


def test_generic_compare_never_calls_passport_evidence() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    evidence = _PassportComparisonEvidence({})
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")

    assert request is not None
    result = build_comparison_result(
        request,
        snapshot,
        visible_cards=session.v2_last_products,
        product_fact_evidence=evidence,  # type: ignore[arg-type]
    )

    assert result.status == ComparisonResultStatus.COMPARED
    assert evidence.calls == []


def test_explicit_passport_compare_does_not_mask_missing_evidence_with_price() -> None:
    left = _product(
        "HEAD-30",
        price=1000,
        length=180,
        document_scope=("head-30.pdf",),
        facts=(),
    )
    right = _product(
        "HEAD-28",
        price=1100,
        length=180,
        document_scope=("head-28.pdf",),
        facts=(),
    )
    snapshot = AnswerSourceSnapshot(source_revision="source-v1", products=(left, right))
    outcome = _outcome()
    session = _session(snapshot)
    evidence = _PassportComparisonEvidence(
        {
            "HEAD-30": (
                "M30×1,5",
                None,
                "head-30.pdf",
                "Посадочная резьба термоголовки M30×1,5.",
            ),
        }
    )
    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравните их по резьбе под термоголовку.",
    )

    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(
            request,
            snapshot,
            visible_cards=session.v2_last_products,
            product_fact_evidence=evidence,  # type: ignore[arg-type]
        ),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.NOT_COMPARABLE
    assert result.outcome_gate_passed is True
    assert result.missing_data == ("thermostatic_head_thread",)
    assert "comparison_explicit_predicate_insufficient_evidence" in result.reason_codes
    assert "нет подтверждённых данных" in render_comparison_result(
        result,
        names={item.sku: item.name for item in snapshot.products},
    )


def test_passport_source_conflict_is_delivered_as_a_safe_comparison_status() -> None:
    left = _product(
        "HEAD-30",
        price=1000,
        length=180,
        document_scope=("head-30.pdf",),
        facts=(),
    )
    right = _product(
        "HEAD-28",
        price=1100,
        length=180,
        document_scope=("head-28.pdf",),
        facts=(),
    )
    snapshot = AnswerSourceSnapshot(source_revision="source-v1", products=(left, right))
    outcome = _outcome()
    session = _session(snapshot)
    evidence = _PassportComparisonEvidence(
        {
            "HEAD-30": ("M30×1,5", None, "head-30.pdf", "M30×1,5"),
            "HEAD-28": ("M28×1,5", None, "head-28.pdf", "M28×1,5"),
        },
        conflict_skus=("HEAD-28",),
    )
    request = build_comparison_request(
        outcome,
        session,
        original_utterance="Сравните их по резьбе под термоголовку.",
    )

    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(
            request,
            snapshot,
            visible_cards=session.v2_last_products,
            product_fact_evidence=evidence,  # type: ignore[arg-type]
        ),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id="comparison-conflict",
        turn_id="comparison-conflict-turn",
    )

    assert result.status == ComparisonResultStatus.SOURCE_CONFLICT
    assert result.outcome_gate_passed is True
    assert candidate is not None
    assert candidate.response is not None
    assert "несовместимые данные" in candidate.response.answer


def test_single_visible_card_gets_one_subject_clarification() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    session.v2_last_products = session.v2_last_products[:1]
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None

    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )

    assert result.status == ComparisonResultStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("comparison_requires_two_visible_cards",)


def test_no_visible_card_returns_v2_clarification_without_global_catalogue_scope() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = SessionState(session_id="empty")
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=()),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id="empty",
        turn_id="compare-empty",
    )

    assert result.status == ComparisonResultStatus.NEED_CLARIFICATION
    assert result.outcome_gate_passed is True
    assert candidate is not None
    assert candidate.response is not None
    assert candidate.response.products == []
    assert "минимум две" in candidate.response.answer


def test_explicit_compare_beats_a_bare_explain_candidate_from_the_same_turn() -> None:
    state = DialogueStateV2(
        turn_number=2,
        tasks=(
            CustomerTask(
                task_id="compare-task",
                act=TaskAct.COMPARE,
                target_goal_id="goal-pump",
                priority=0,
                source="semantic_interpreter",
                source_turn=2,
            ),
            CustomerTask(
                task_id="bare-explain",
                act=TaskAct.EXPLAIN,
                target_goal_id="goal-pump",
                priority=1,
                source="semantic_interpreter",
                source_turn=2,
            ),
            CustomerTask(
                task_id="selection-task",
                act=TaskAct.SELECT,
                target_goal_id="goal-pump",
                priority=2,
                source="semantic_interpreter",
                source_turn=1,
            ),
        ),
    )

    plan = SellerPolicy().decide(state)

    assert plan.primary.kind == NextActionKind.COMPARE
    assert plan.primary.task_id == "compare-task"


def test_stale_or_unversioned_scope_is_rejected_before_comparison() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    session.v2_source_revision = "old-source"
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    stale = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    assert stale.status == ComparisonResultStatus.REJECTED
    assert stale.outcome_gate_passed is False
    assert "comparison_source_revision_stale" in stale.reason_codes

    legacy = SessionState(session_id="legacy", last_products=session.last_products)
    legacy_request = build_comparison_request(outcome, legacy, original_utterance="Сравните их")
    assert legacy_request is not None
    legacy_result = validate_comparison_result(
        legacy_request,
        build_comparison_result(legacy_request, snapshot, visible_cards=legacy.last_products),
        snapshot,
    )
    assert legacy_result.status == ComparisonResultStatus.REJECTED
    assert "comparison_scope_not_v2_versioned" in legacy_result.reason_codes


def test_comparison_gate_rejects_a_value_or_winner_that_does_not_match_snapshot() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Какой из показанных дешевле?")
    assert request is not None
    accepted = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    price = next(item for item in accepted.dimensions if item.predicate == "price")
    changed_price = price.model_copy(
        update={"values": (price.values[0].model_copy(update={"value": 1}), *price.values[1:])}
    )
    tampered = accepted.model_copy(update={"dimensions": (changed_price, *accepted.dimensions[1:])})
    rejected = validate_comparison_result(request, tampered, snapshot)

    assert rejected.outcome_gate_passed is False
    assert "comparison_value_does_not_match_source_snapshot" in rejected.reason_codes


def test_comparison_candidate_preserves_selection_scope_and_never_reissues_cards() -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    session = _session(snapshot)
    request = build_comparison_request(outcome, session, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=session.v2_last_products),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id="compare",
        turn_id="compare-turn",
    )

    assert candidate is not None
    assert candidate.eligible_for_delivery is True
    assert candidate.response is not None
    assert candidate.response.products == []
    assert "Сравнение показанных вариантов" in candidate.response.answer
    assert candidate.state_after.answer_plan_summary is not None
    assert candidate.state_after.answer_plan_summary.selection_id == "selection-v1"
    assert [item.sku for item in candidate.state_after.answer_plan_summary.presented_candidates] == [
        "PUMP-180",
        "PUMP-130",
    ]


def test_committed_comparison_keeps_the_visible_order_and_selection_identity(tmp_path) -> None:
    snapshot = _snapshot()
    outcome = _outcome()
    before = _session(snapshot)
    request = build_comparison_request(outcome, before, original_utterance="Сравните их")
    assert request is not None
    result = validate_comparison_result(
        request,
        build_comparison_result(request, snapshot, visible_cards=before.v2_last_products),
        snapshot,
    )
    candidate = build_v2_comparison_candidate(
        outcome,
        _base_candidate(outcome),
        result,
        snapshot,
        session_id=before.session_id,
        turn_id="compare-turn",
    )
    assert candidate is not None
    products = [
        Product(
            sku=item.sku,
            name=item.name,
            category_path="Насосы",
            price=item.price,
            currency=item.currency or "RUB",
            stock_status=item.stock_status or "",
            stock_qty=item.stock_qty,
            url=item.url,
            image_url=item.image_url,
        )
        for item in snapshot.products
    ]
    settings = get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "comparison-commit.jsonl",
        }
    )
    bot = ChatOrchestrator(settings=settings, products=products)
    bot.answer_source_snapshot_v2 = snapshot
    decision = CutoverDecision(
        owner_candidate=ResponseOwner.V2,
        execution_mode=ExecutionMode.V2_PRIMARY,
        eligible=True,
        catalog_revision=snapshot.source_revision,
    )

    response, commit = bot._commit_v2_response(
        before,
        "Сравните их",
        "comparison-client-turn",
        "compare-turn",
        decision,
        candidate,
    )
    stored = bot.sessions.snapshot(before.session_id)

    assert commit.committed is True
    assert response.products == []
    assert [item.sku for item in stored.v2_last_products] == ["PUMP-180", "PUMP-130"]
    assert [item.sku for item in stored.last_products] == ["PUMP-180", "PUMP-130"]
    assert stored.v2_selection_id == "selection-v1"
    assert stored.v2_source_revision == "source-v1"
