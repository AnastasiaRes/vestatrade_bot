from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogProductRole,
    FactProvenance,
    ProductKind,
)
from app.catalog_v2.normalization import normalize_catalog_product
from app.catalog_v2.registry import ProductContractRegistry
from app.compatibility_v2.contracts import (
    CompatibilityRelationKind,
    CompatibilityResultStatus,
    InterfaceFactResolutionStatus,
    InterfaceSourceKind,
)
from app.compatibility_v2.renderer import render_compatibility_result
from app.compatibility_v2.service import (
    InterfaceFactService,
    build_compatibility_request,
    build_compatibility_result,
    validate_compatibility_result,
)
from app.cutover_v2.compatibility import build_v2_compatibility_candidate
from app.cutover_v2.contracts import V2TurnCandidate
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.dialogue_v2.seller_policy import SellerPolicy
from app.models import Product, ProductCard, ProductDocument, SessionState
from app.product_fact_evidence import (
    PassportEvidenceResult,
    PassportEvidenceStatus,
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
    name: str,
    kind: ProductKind,
    facts: tuple[CatalogFact, ...],
) -> CatalogAnswerProduct:
    return CatalogAnswerProduct(
        sku=sku,
        name=name,
        product_kind=kind,
        role=CatalogProductRole.COMPONENT,
        price=100.0,
        currency="RUB",
        stock_status="в наличии",
        stock_qty=10,
        url=f"https://example.test/{sku}",
        image_url=None,
        facts=facts,
    )


def _snapshot() -> AnswerSourceSnapshot:
    return AnswerSourceSnapshot(
        source_revision="compatibility-source-v1",
        products=(
            _product(
                "VT.1500.0.0",
                "Термоголовка VALTEC VT.1500",
                ProductKind.THERMOSTATIC_HEAD,
                (_fact("control_thread", "M30x1.5"),),
            ),
            _product(
                "VT.048.N.04",
                "Клапан термостатический VALTEC VT.048.N.04",
                ProductKind.RADIATOR_VALVE,
                (_fact("control_thread", "M30x1.5"),),
            ),
            _product(
                "HEAD.20.00",
                "Термоголовка M20",
                ProductKind.THERMOSTATIC_HEAD,
                (_fact("control_thread", "M20x1.5"),),
            ),
            _product(
                "THREAD.11.00",
                "Кран G1/2 ВР-ВР",
                ProductKind.BALL_VALVE,
                (
                    _fact("connection_size", "1/2", "inch"),
                    _fact("connection_pattern", "female_female"),
                ),
            ),
            _product(
                "THREAD.22.00",
                "Ниппель G1/2 НР-НР",
                ProductKind.COUPLING,
                (
                    _fact("connection_size", "1/2", "inch"),
                    _fact("connection_pattern", "male_male"),
                ),
            ),
            _product(
                "THREAD.33.00",
                "Кран G1/2 ВР-ВР второй",
                ProductKind.BALL_VALVE,
                (
                    _fact("connection_size", "1/2", "inch"),
                    _fact("connection_pattern", "female_female"),
                ),
            ),
            _product(
                "HT-50-PIPE",
                "Труба канализационная HTEM 50*2000",
                ProductKind.SEWER_PIPE,
                (
                    _fact("diameter_mm", 50, "mm"),
                    _fact("sewer_scope", "internal"),
                ),
            ),
            _product(
                "HT-50-ELBOW",
                "Отвод канализационный HTB 50 87°",
                ProductKind.SEWER_ELBOW,
                (_fact("sewer_scope", "internal"),),
            ),
            _product(
                "KG-110-PIPE",
                "Труба канализационная KGEM 110*1000",
                ProductKind.SEWER_PIPE,
                (
                    _fact("diameter_mm", 110, "mm"),
                    _fact("sewer_scope", "external"),
                ),
            ),
            _product(
                "PUMP-25",
                "Насос 25-40",
                ProductKind.CIRCULATION_PUMP,
                (),
            ),
            _product(
                "BOILER-24",
                "Котёл 24 кВт",
                ProductKind.GAS_BOILER,
                (),
            ),
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


def _outcome() -> DialogueV2Outcome:
    task = CustomerTask(
        task_id="compatibility-task",
        act=TaskAct.COMPATIBILITY,
        target_goal_id="goal-interface",
        priority=0,
        source="semantic_interpreter",
        source_turn=2,
    )
    after = DialogueStateV2(turn_number=2, tasks=(task,))
    return DialogueV2Outcome(
        status="applied",
        state_before=after.model_copy(update={"turn_number": 1}),
        state_after=after,
        next_action_plan=NextActionPlan(
            primary=NextAction(
                kind=NextActionKind.CHECK_COMPATIBILITY,
                task_id=task.task_id,
                reason_code="explicit_compatibility_request",
            ),
            task_ids=(task.task_id,),
        ),
    )


def _result(message: str, session: SessionState | None = None):
    snapshot = _snapshot()
    request = build_compatibility_request(
        _outcome(), session or SessionState(session_id="compat"), snapshot,
        original_utterance=message,
    )
    assert request is not None
    result = validate_compatibility_result(
        request, build_compatibility_result(request, snapshot), snapshot
    )
    return request, result


class _PassportStub:
    def __init__(self, quote: str) -> None:
        self.quote = quote
        self.calls = 0

    def answer(self, *_args, **kwargs) -> PassportEvidenceResult:
        self.calls += 1
        documents = tuple(kwargs["document_scope"])
        return PassportEvidenceResult(
            status=PassportEvidenceStatus.ANSWERED,
            quote=self.quote,
            document=documents[0],
            section="технические характеристики",
            verifier_status="accepted",
            document_scope=documents,
        )


class _EvidenceStub:
    def __init__(self, passport_service: _PassportStub) -> None:
        self.passport_service = passport_service


def test_partial_sku_head_and_exact_valve_are_resolved_and_proven() -> None:
    request, result = _result("Подойдёт ли VT.1500 к VT.048.N.04?")

    assert request.left.canonical_sku == "VT.1500.0.0"
    assert request.left.reason_code == "explicit_unique_partial_sku"
    assert result.relation == CompatibilityRelationKind.THERMOSTATIC_HEAD_TO_VALVE
    assert result.status == CompatibilityResultStatus.COMPATIBLE
    assert result.outcome_gate_passed is True
    assert {item.predicate for item in result.facts} == {"control_thread"}
    assert "M30x1.5" in render_compatibility_result(result)


def test_thermostatic_thread_mismatch_is_incompatible_not_a_brand_guess() -> None:
    _, result = _result("Совместимы ли HEAD.20.00 и VT.048.N.04?")

    assert result.status == CompatibilityResultStatus.INCOMPATIBLE
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("thermostatic_control_thread_mismatch",)


def test_exact_bound_passport_is_aggregated_with_card_before_head_verdict() -> None:
    snapshot = _snapshot()
    source_head = Product(
        sku="VT.1500.0.0",
        name="Термоголовка VALTEC VT.1500",
        documents=[
            ProductDocument(
                filename="head-passport.pdf",
                document_kind="passport",
                text="Присоединительная резьба M30×1,5.",
                binding_scope="exact_sku",
                binding_value="VT.1500.0.0",
            )
        ],
    )
    passport = _PassportStub("Присоединительная резьба M30×1,5.")
    service = InterfaceFactService(
        snapshot,
        product_fact_evidence=_EvidenceStub(passport),  # type: ignore[arg-type]
        products=[source_head],
    )

    resolution = service.observe("VT.1500.0.0", "control_thread")

    assert resolution.status == InterfaceFactResolutionStatus.PROVEN
    assert resolution.selected_fact is not None
    assert resolution.selected_fact.source_kind == InterfaceSourceKind.PASSPORT
    assert resolution.selected_fact.value == "M30x1.5"
    assert {item.source_kind for item in resolution.observations} == {
        InterfaceSourceKind.CATALOG_ATTRIBUTE,
        InterfaceSourceKind.PASSPORT,
    }
    assert passport.calls == 1

    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="interface-observation"),
        snapshot,
        original_utterance="Подойдёт ли VT.1500 к VT.048.N.04?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request,
        build_compatibility_result(request, snapshot, interface_facts=service),
        snapshot,
    )

    assert result.status == CompatibilityResultStatus.COMPATIBLE
    assert result.outcome_gate_passed is True
    assert len(result.observations) == 3
    assert any(item.source_kind == InterfaceSourceKind.PASSPORT for item in result.observations)


def test_passport_card_thread_disagreement_is_source_conflict_not_card_priority() -> None:
    snapshot = _snapshot()
    source_head = Product(
        sku="VT.1500.0.0",
        name="Термоголовка VALTEC VT.1500",
        documents=[
            ProductDocument(
                filename="head-passport.pdf",
                document_kind="passport",
                text="Присоединительная резьба M28×1,5.",
                binding_scope="exact_sku",
                binding_value="VT.1500.0.0",
            )
        ],
    )
    service = InterfaceFactService(
        snapshot,
        product_fact_evidence=_EvidenceStub(
            _PassportStub("Присоединительная резьба M28×1,5.")
        ),  # type: ignore[arg-type]
        products=[source_head],
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="interface-conflict"),
        snapshot,
        original_utterance="Подойдёт ли VT.1500 к VT.048.N.04?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request,
        build_compatibility_result(request, snapshot, interface_facts=service),
        snapshot,
    )

    assert result.status == CompatibilityResultStatus.SOURCE_CONFLICT
    assert result.outcome_gate_passed is True
    assert {item.value for item in result.observations if item.sku == "VT.1500.0.0"} == {
        "M28x1.5",
        "M30x1.5",
    }


def test_series_bound_passport_cannot_upgrade_model_specific_interface_verdict() -> None:
    snapshot = _snapshot()
    source_head = Product(
        sku="VT.1500.0.0",
        name="Термоголовка VALTEC VT.1500",
        documents=[
            ProductDocument(
                filename="head-series.pdf",
                document_kind="passport",
                text="Присоединительная резьба M30×1,5.",
                binding_scope="sku_prefix",
                binding_value="VT.1500",
            )
        ],
    )
    passport = _PassportStub("Присоединительная резьба M30×1,5.")
    service = InterfaceFactService(
        snapshot,
        product_fact_evidence=_EvidenceStub(passport),  # type: ignore[arg-type]
        products=[source_head],
    )

    resolution = service.observe("VT.1500.0.0", "control_thread")

    assert resolution.status == InterfaceFactResolutionStatus.PROVEN
    assert len(resolution.observations) == 1
    assert resolution.selected_fact is not None
    assert resolution.selected_fact.source_kind == InterfaceSourceKind.CATALOG_ATTRIBUTE
    assert passport.calls == 0


def test_threaded_connection_requires_complementary_size_and_gender() -> None:
    _, compatible = _result("Можно соединить THREAD.11.00 и THREAD.22.00?")
    _, incompatible = _result("Можно соединить THREAD.11.00 и THREAD.33.00?")

    assert compatible.status == CompatibilityResultStatus.COMPATIBLE
    assert incompatible.status == CompatibilityResultStatus.INCOMPATIBLE
    assert incompatible.reason_codes == ("thread_connection_pattern_not_mating",)


def test_threaded_connection_without_explicit_standard_is_not_positive() -> None:
    original = _snapshot()
    snapshot = original.model_copy(
        update={
            "products": tuple(
                item.model_copy(update={"name": item.name.replace("G1/2 ", "")})
                if item.sku in {"THREAD.11.00", "THREAD.22.00"}
                else item
                for item in original.products
            )
        }
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="thread-standard-missing"),
        snapshot,
        original_utterance="Можно соединить THREAD.11.00 и THREAD.22.00?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request, build_compatibility_result(request, snapshot), snapshot
    )

    assert result.status == CompatibilityResultStatus.INSUFFICIENT_EVIDENCE
    assert result.outcome_gate_passed is True
    assert set(result.missing_predicates) == {
        "THREAD.11.00:thread_standard",
        "THREAD.22.00:thread_standard",
    }
    assert render_compatibility_result(result).count("стандарт резьбы") == 1


def test_five_digit_article_is_resolved_only_inside_source_bound_compatibility() -> None:
    original = _snapshot()
    pump = _product(
        "53843",
        "Насос циркуляционный 25-40",
        ProductKind.CIRCULATION_PUMP,
        (),
    )
    boiler = _product(
        "8216262000",
        "Котёл электрический",
        ProductKind.ELECTRIC_BOILER,
        (),
    )
    snapshot = original.model_copy(update={"products": (*original.products, pump, boiler)})
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="five-digit-compatibility"),
        snapshot,
        original_utterance="Подойдет ли насос 53843 к котлу 8216262000?",
    )

    assert request is not None
    assert request.left.canonical_sku == "53843"
    assert request.right.canonical_sku == "8216262000"
    assert request.left.reason_code == "explicit_exact_sku"
    assert request.right.reason_code == "explicit_exact_sku"


def test_multiport_threaded_item_requires_a_resolved_endpoint() -> None:
    original = _snapshot()
    left = original.product("THREAD.11.00")
    assert left is not None
    snapshot = original.model_copy(
        update={
            "products": tuple(
                item.model_copy(update={"facts": (*item.facts, _fact("port_count", 3))})
                if item.sku == "THREAD.11.00"
                else item
                for item in original.products
            )
        }
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="threaded-multiport"),
        snapshot,
        original_utterance="Можно соединить THREAD.11.00 и THREAD.22.00?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request, build_compatibility_result(request, snapshot), snapshot
    )

    assert result.status == CompatibilityResultStatus.INSUFFICIENT_EVIDENCE
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("threaded_multiport_endpoint_not_determined",)


def test_outcome_gate_recomputes_threaded_verdict_from_selected_evidence() -> None:
    request, result = _result("Можно соединить THREAD.11.00 и THREAD.22.00?")
    assert result.status == CompatibilityResultStatus.COMPATIBLE
    forged = result.model_copy(
        update={
            "status": CompatibilityResultStatus.INCOMPATIBLE,
            "reason_codes": ("thread_connection_pattern_not_mating",),
        }
    )

    checked = validate_compatibility_result(request, forged, _snapshot())

    assert checked.outcome_gate_passed is False
    assert "compatibility_verdict_not_recomputed_from_evidence" in checked.reason_codes


def test_sewer_identity_fallback_proves_dn_from_explicit_ht_marking() -> None:
    _, result = _result("Совместимы ли HT-50-PIPE и HT-50-ELBOW?")

    assert result.relation == CompatibilityRelationKind.SEWER_CONNECTION
    assert result.status == CompatibilityResultStatus.COMPATIBLE
    assert result.outcome_gate_passed is True
    diameter = [item for item in result.facts if item.predicate == "diameter_mm"]
    assert {item.value for item in diameter} == {50}
    assert any(item.source_kind.value == "catalog_identity" for item in diameter)


def test_sewer_scope_or_diameter_mismatch_is_not_hidden_as_a_fallback() -> None:
    _, result = _result("Совместимы ли HT-50-PIPE и KG-110-PIPE?")

    assert result.status == CompatibilityResultStatus.INCOMPATIBLE
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("sewer_nominal_diameter_mismatch",)


def test_pump_to_boiler_is_explicitly_insufficient_not_an_engineering_guess() -> None:
    _, result = _result("Подойдёт ли PUMP-25 к BOILER-24?")

    assert result.status == CompatibilityResultStatus.INSUFFICIENT_EVIDENCE
    assert result.outcome_gate_passed is True
    assert "pump_boiler_requires_hydraulic_calculation" in result.reason_codes
    assert "boiler_integrated_pump_not_confirmed" in result.reason_codes


def test_boiler_normalization_keeps_only_an_explicit_integrated_pump_fact() -> None:
    boiler = Product(
        sku="BOILER-NORMALIZED",
        name="Котел электрический 9 кВт",
        category_path="Котлы",
        attributes_normalized={"Тип товара": "Котел"},
        description="Встроенный циркуляционный насос с тремя скоростями.",
    )
    normalized = normalize_catalog_product(boiler, ProductContractRegistry())

    fact = next(
        item
        for item in normalized.facts
        if item.name == "integrated_circulation_pump"
    )
    assert fact.value is True
    assert fact.provenance.source == "description"


def test_pump_boiler_reads_attached_passport_before_requesting_hydraulic_precheck() -> None:
    original = _snapshot()
    boiler = original.product("BOILER-24")
    assert boiler is not None
    boiler = boiler.model_copy(
        update={"facts": (_fact("integrated_circulation_pump", True),)}
    )
    snapshot = original.model_copy(
        update={
            "products": tuple(
                boiler if item.sku == boiler.sku else item
                for item in original.products
            )
        }
    )
    source_boiler = Product(
        sku="BOILER-24",
        name="Котёл 24 кВт",
        category_path="Котлы",
        documents=[
                ProductDocument(
                    filename="boiler-passport.pdf",
                    document_kind="passport",
                    text="Котёл оборудован встроенным циркуляционным насосом.",
                    binding_scope="exact_sku",
            )
        ],
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="pump-boiler-passport"),
        snapshot,
        original_utterance="Подойдёт ли PUMP-25 к BOILER-24?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request,
        build_compatibility_result(
            request,
            snapshot,
            interface_facts=InterfaceFactService(
                snapshot,
                products=[source_boiler],
            ),
        ),
        snapshot,
    )

    assert result.status == CompatibilityResultStatus.INSUFFICIENT_EVIDENCE
    assert result.outcome_gate_passed is True
    assert "boiler_integrated_pump_confirmed" in result.reason_codes
    fact = next(item for item in result.facts if item.predicate == "integrated_circulation_pump")
    assert fact.value is True
    assert fact.document == "boiler-passport.pdf"
    assert fact.verifier_status == "document_text_exact"
    assert "встроенный циркуляционный насос" in render_compatibility_result(result).casefold()


def test_pump_boiler_card_and_passport_conflict_fails_closed() -> None:
    original = _snapshot()
    boiler = original.product("BOILER-24")
    assert boiler is not None
    boiler = boiler.model_copy(
        update={"facts": (_fact("integrated_circulation_pump", True),)}
    )
    snapshot = original.model_copy(
        update={
            "products": tuple(
                boiler if item.sku == boiler.sku else item
                for item in original.products
            )
        }
    )
    source_boiler = Product(
        sku="BOILER-24",
        name="Котёл 24 кВт",
        category_path="Котлы",
        description="Котёл со встроенным циркуляционным насосом.",
        documents=[
                ProductDocument(
                    filename="boiler-passport.pdf",
                    document_kind="passport",
                    text="Циркуляционный насос в изделие не встроен.",
                    binding_scope="exact_sku",
            )
        ],
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="pump-boiler-conflict"),
        snapshot,
        original_utterance="Подойдёт ли PUMP-25 к BOILER-24?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request,
        build_compatibility_result(
            request,
            snapshot,
            interface_facts=InterfaceFactService(snapshot, products=[source_boiler]),
        ),
        snapshot,
    )

    assert result.status == CompatibilityResultStatus.SOURCE_CONFLICT
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("boiler_integrated_pump_source_conflict",)


def test_v2_visible_scope_allows_ordinals_and_candidate_preserves_it() -> None:
    snapshot = _snapshot()
    visible = (snapshot.product("VT.1500.0.0"), snapshot.product("VT.048.N.04"))
    session = SessionState(
        session_id="visible",
        v2_last_products=[_card(item) for item in visible if item is not None],
        last_products=[_card(item) for item in visible if item is not None],
        v2_selection_id="selection-v1",
        v2_source_revision=snapshot.source_revision,
    )
    request = build_compatibility_request(
        _outcome(), session, snapshot, original_utterance="Подойдут ли первый и второй друг к другу?"
    )
    assert request is not None
    result = validate_compatibility_result(
        request, build_compatibility_result(request, snapshot), snapshot
    )
    base = V2TurnCandidate(
        turn_id="compatibility-turn",
        state_before=_outcome().state_before,
        state_after=_outcome().state_after,
        validation_status="not_run",
    )
    candidate = build_v2_compatibility_candidate(
        _outcome(), base, result, snapshot, session_id=session.session_id, turn_id="compatibility-turn"
    )

    assert request.selection_id == "selection-v1"
    assert result.outcome_gate_passed is True
    assert candidate is not None and candidate.response is not None
    assert candidate.response.products == []
    assert candidate.compatibility_result == result
    assert candidate.state_after.answer_plan_summary.selection_id == "selection-v1"


def test_no_scope_never_launches_a_global_search() -> None:
    request, result = _result("Подойдут ли они друг к другу?")

    assert request.left.canonical_sku is None
    assert request.right.canonical_sku is None
    assert result.status == CompatibilityResultStatus.INSUFFICIENT_EVIDENCE
    assert result.outcome_gate_passed is True
    assert "назовите две позиции" in render_compatibility_result(result).casefold()


def test_conflicting_interface_values_return_source_conflict_without_a_verdict() -> None:
    original = _snapshot()
    head = original.product("VT.1500.0.0")
    assert head is not None
    conflicting_head = head.model_copy(
        update={"facts": (*head.facts, _fact("control_thread", "M28x1.5"))}
    )
    snapshot = original.model_copy(
        update={
            "products": tuple(
                conflicting_head if product.sku == conflicting_head.sku else product
                for product in original.products
            )
        }
    )
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="source-conflict"),
        snapshot,
        original_utterance="Подойдёт ли VT.1500 к VT.048.N.04?",
    )
    assert request is not None
    result = validate_compatibility_result(
        request, build_compatibility_result(request, snapshot), snapshot
    )

    assert result.status == CompatibilityResultStatus.SOURCE_CONFLICT
    assert result.outcome_gate_passed is True
    assert result.reason_codes == ("compatibility_interface_fact_source_conflict",)


def test_stale_source_snapshot_is_rejected_and_cannot_be_delivered() -> None:
    snapshot = _snapshot()
    request = build_compatibility_request(
        _outcome(),
        SessionState(session_id="stale"),
        snapshot,
        original_utterance="Подойдёт ли VT.1500 к VT.048.N.04?",
    )
    assert request is not None
    stale_request = request.model_copy(update={"source_revision": "obsolete-source"})
    result = validate_compatibility_result(
        stale_request,
        build_compatibility_result(stale_request, snapshot),
        snapshot,
    )

    assert result.status == CompatibilityResultStatus.REJECTED
    assert result.outcome_gate_passed is False


def test_seller_policy_prioritizes_typed_compatibility_over_selection() -> None:
    state = _outcome().state_after
    plan = SellerPolicy().decide(state)

    assert plan.primary.kind == NextActionKind.CHECK_COMPATIBILITY
    assert plan.primary.task_id == "compatibility-task"
