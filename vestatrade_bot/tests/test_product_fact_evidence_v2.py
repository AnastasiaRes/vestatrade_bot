from __future__ import annotations

from app.answer_v2.contracts import AnswerSourceSnapshot, CatalogAnswerProduct
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogProductRole,
    CatalogProductSnapshot,
    FactProvenance,
    ProductKind,
)
from app.catalog_v2.normalization import normalize_catalog_product
from app.catalog_v2.registry import ProductContractRegistry
from app.config import get_settings
from app.cutover_v2.contracts import V2TurnCandidate
from app.cutover_v2.product_fact import build_v2_product_fact_candidate
from app.dialogue_v2.contracts import (
    CustomerTask,
    DialogueStateV2,
    NextAction,
    NextActionKind,
    NextActionPlan,
    RequestedInformationOutput,
    TaskAct,
)
from app.dialogue_v2.controller import DialogueV2Outcome
from app.models import Product, ProductCard, ProductDocument, ProductFocusState, SessionState
from app.product_fact_evidence import (
    PassportEvidenceResult,
    PassportEvidenceStatus,
    ProductFactEvidenceService,
    ProductFactStatus,
    ProductReferenceKind,
    render_product_fact_evidence,
)


class _NoopClient:
    def embed(self, _texts):
        raise AssertionError("stub passport provider owns retrieval in this unit test")


class _StubPassport:
    def __init__(
        self,
        *,
        quote: str = "Монтажная длина в мм (130; 180)",
        document: str = "VRS-0725.pdf",
    ) -> None:
        self.quote = quote
        self.document = document
        self.calls = []

    def answer(self, question: str, **kwargs) -> PassportEvidenceResult:
        self.calls.append((question, kwargs))
        return PassportEvidenceResult(
            status=PassportEvidenceStatus.ANSWERED,
            answer_text="legacy-compatible-rendering",
            quote=self.quote,
            framing="",
            document=self.document,
            section="таблица характеристик",
            ordinal=1,
            verifier_status="accepted",
            document_scope=tuple(kwargs["document_scope"]),
        )


def _product(
    sku: str,
    name: str,
    attributes: dict[str, str],
    document: str,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path="Инженерная сантехника",
        price=1000,
        stock_status="в наличии",
        stock_qty=10,
        url=f"https://example.test/{sku}",
        attributes_normalized=attributes,
        documents=[ProductDocument(filename=document, text="bounded text")],
    )


def _card(product: Product) -> ProductCard:
    return ProductCard(
        sku=product.sku,
        name=product.name,
        price=product.price or 0,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "https://example.test",
    )


def _service(products: list[Product], passport: _StubPassport):
    settings = get_settings().model_copy(update={"embeddings_enabled": True})
    return ProductFactEvidenceService(
        settings,
        _NoopClient(),
        products,
        passport_service=passport,  # type: ignore[arg-type]
    )


def test_ordinal_product_fact_is_bound_to_customer_visible_card() -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос VALTEC RS 25/4-180",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    passport = _StubPassport()
    service = _service([pump], passport)
    session = SessionState(session_id="pump", last_products=[_card(pump)])

    evidence = service.evaluate("Какая у первого монтажная длина?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.ORDINAL
    assert evidence.request.product_ref.canonical_sku == "VRS.254.18.0"
    assert evidence.request.predicate == "installation_length_mm"
    assert evidence.value == "180"
    assert evidence.unit == "мм"
    assert evidence.document == "VRS-0725.pdf"
    assert evidence.verifier_status == "accepted"
    assert len(passport.calls) == 1
    rendered = render_product_fact_evidence(evidence)
    assert "180 мм" in rendered
    assert "VRS.254.18.0" in rendered


def test_exact_product_evidence_facade_keeps_productfact_gates_for_compare() -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос VALTEC RS 25/4-180",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    passport = _StubPassport(
        quote="Монтажная длина для модели VRS.254.18.0 — 180 мм.",
        document="VRS-0725.pdf",
    )
    service = _service([pump], passport)

    evidence = service.evaluate_exact_product(
        sku="VRS.254.18.0",
        predicate="installation_length_mm",
    )

    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.EXACT_SKU
    assert evidence.request.product_ref.canonical_sku == pump.sku
    assert evidence.request.predicate == "installation_length_mm"
    assert evidence.value == "180"
    assert evidence.document == "VRS-0725.pdf"
    assert len(passport.calls) == 1


def test_versioned_v2_selection_wins_over_legacy_context_card_for_ordinals() -> None:
    first = _product(
        "VRS.254.18.0",
        "Насос VALTEC RS 25/4-180",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    second = _product(
        "2459900",
        "Насос Wilo Star RS 25/6-130(180)-RK",
        {"монтажная длина, мм": "130-180"},
        "Wilo.pdf",
    )
    passport = _StubPassport(
        quote="Монтажная длина для исполнений: 130 и 180 мм.",
        document="Wilo.pdf",
    )
    service = _service([first, second], passport)
    session = SessionState(
        session_id="v2-authoritative-ordinals",
        # A contextual card from another response must not truncate the V2
        # Selection that owns the ordinal order.
        last_products=[_card(first)],
        v2_last_products=[_card(first), _card(second)],
        v2_selection_id="selection-two-pumps",
        v2_source_revision="catalog-revision",
    )

    evidence = service.evaluate("Какая у второго монтажная длина?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.ORDINAL
    assert evidence.request.product_ref.canonical_sku == second.sku
    assert evidence.value == "130–180"


def test_explicit_card_title_fact_answers_without_passport_search() -> None:
    valve = _product(
        "VT.217.N.04",
        'Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.',
        {"Тип товара": "Кран шаровой"},
        "VT.217.pdf",
    )
    snapshot = CatalogProductSnapshot(
        sku=valve.sku,
        name=valve.name,
        category=valve.category_path,
        product_kind=ProductKind.BALL_VALVE,
        role=CatalogProductRole.COMPONENT,
        facts=(
            CatalogFact(
                name="handle_type",
                value="рукоятка бабочка",
                provenance=FactProvenance(
                    source="name",
                    source_field="name",
                    raw_value="рукоятка бабочка",
                    parser="explicit_valve_handle_title",
                ),
            ),
        ),
    )
    passport = _StubPassport()
    settings = get_settings().model_copy(update={"embeddings_enabled": True})
    service = ProductFactEvidenceService(
        settings,
        _NoopClient(),
        [valve],
        passport_service=passport,  # type: ignore[arg-type]
        catalog_snapshot=(snapshot,),
    )
    session = SessionState(session_id="valve", last_products=[_card(valve)])

    evidence = service.evaluate("Какая ручка у первого?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.predicate == "handle_type"
    assert evidence.request.product_ref.kind == ProductReferenceKind.ORDINAL
    assert evidence.value == "рукоятка бабочка"
    assert evidence.source_kind == "catalog_card"
    assert evidence.verifier_status == "catalog_snapshot_exact"
    assert passport.calls == []
    assert "Тип ручки — рукоятка бабочка" in render_product_fact_evidence(evidence)


def test_type_of_thread_on_first_v2_card_prefers_pattern_over_generic_thread_size() -> None:
    """The common customer phrase must not become an unknown ProductFact."""

    valve = _product(
        "VT.214.N.04",
        'Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн.',
        {"Тип товара": "Кран шаровой"},
        "VT.214-215-217-218-219-1224.pdf",
    )
    snapshot = CatalogProductSnapshot(
        sku=valve.sku,
        name=valve.name,
        category=valve.category_path,
        product_kind=ProductKind.BALL_VALVE,
        role=CatalogProductRole.COMPONENT,
        facts=(
            CatalogFact(
                name="connection_size",
                value="1/2",
                unit="inch",
                provenance=FactProvenance(
                    source="name",
                    source_field="name",
                    raw_value='1/2"',
                    parser="inch_size",
                ),
            ),
            CatalogFact(
                name="connection_pattern",
                value="female_female",
                provenance=FactProvenance(
                    source="name",
                    source_field="name",
                    raw_value="вн.-вн.",
                    parser="connection_pattern",
                ),
            ),
        ),
    )
    passport = _StubPassport()
    settings = get_settings().model_copy(update={"embeddings_enabled": True})
    service = ProductFactEvidenceService(
        settings,
        _NoopClient(),
        [valve],
        passport_service=passport,  # type: ignore[arg-type]
        catalog_snapshot=(snapshot,),
    )
    session = SessionState(
        session_id="v2-thread-pattern",
        # The public contextual card is intentionally shorter; ordinals must
        # still resolve from the complete versioned V2 Selection.
        last_products=[_card(valve)],
        v2_last_products=[_card(valve)],
        v2_selection_id="selection-valve",
        v2_source_revision="catalog-revision",
    )

    evidence = service.evaluate("Какая у первого тип резьбы?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.predicate == "connection_pattern"
    assert evidence.request.product_ref.kind == ProductReferenceKind.ORDINAL
    assert evidence.request.product_ref.canonical_sku == valve.sku
    assert evidence.value == "female_female"
    assert evidence.source_kind == "catalog_card"
    assert evidence.verifier_status == "catalog_snapshot_exact"
    assert passport.calls == []
    assert "внутренняя/внутренняя резьба" in render_product_fact_evidence(evidence)


def test_wilo_range_is_not_silently_narrowed_to_180() -> None:
    pump = _product(
        "2459900",
        "Wilo Star RS 25/6-130(180)-RK",
        {"монтажная длина, мм": "130-180"},
        "Wilo.pdf",
    )
    passport = _StubPassport(
        quote="Монтажная длина для исполнений: 130 и 180 мм.",
        document="Wilo.pdf",
    )
    service = _service([pump], passport)
    session = SessionState(session_id="wilo", last_products=[_card(pump)])

    evidence = service.evaluate("Какая у первого монтажная длина?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.value == "130–180"
    rendered = render_product_fact_evidence(evidence)
    assert "130–180 мм" in rendered
    assert "— 180 мм" not in rendered


def test_named_pipe_series_answers_only_on_catalogue_consensus() -> None:
    products = [
        _product(
            f"VTp.700.FB20.{diameter}",
            f"Труба PP-FIBER PN 20 {diameter} MM",
            {
                "максимальная рабочая температура, °с": "90",
                "рабочее давление, радиаторное отопление, бар": "6",
            },
            "VTp.700.FB20-0425.pdf",
        )
        for diameter in (20, 25)
    ]
    passport = _StubPassport(
        quote="Максимальная рабочая температура труб составляет 90 °C.",
        document="VTp.700.FB20-0425.pdf",
    )
    service = _service(products, passport)
    session = SessionState(session_id="pipe")

    evidence = service.evaluate(
        "Какая максимальная рабочая температура у трубы PP-FIBER PN 20?",
        session,
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.NAMED_SERIES
    assert evidence.request.product_ref.canonical_sku is None
    assert evidence.value == "90"
    assert evidence.unit == "°C"
    assert set(evidence.request.product_ref.candidate_skus) == {
        "VTp.700.FB20.20",
        "VTp.700.FB20.25",
    }


def test_temperature_threshold_question_reads_maximum_then_compares() -> None:
    pipe = _product(
        "VTp.700.FB20.25",
        "Труба PP-FIBER PN 20 25 MM",
        {"максимальная рабочая температура, °с": "90"},
        "VTp.700.FB20-0425.pdf",
    )
    passport = _StubPassport(
        quote="Максимальная рабочая температура труб составляет 90 °C.",
        document="VTp.700.FB20-0425.pdf",
    )
    service = _service([pipe], passport)
    session = SessionState(session_id="pipe-threshold", last_products=[_card(pipe)])

    evidence = service.evaluate(
        "Эта труба точно выдерживает 90 °C?",
        session,
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.predicate == "maximum_operating_temperature_c"
    assert evidence.request.threshold_value == 90
    assert evidence.request.threshold_operator == "at_least"
    rendered = render_product_fact_evidence(evidence)
    assert rendered.startswith("Да.")
    assert "90 °C" in rendered


def test_immediate_named_series_focus_carries_the_next_pressure_predicate() -> None:
    products = [
        _product(
            f"VTp.700.FB20.{diameter}",
            f"Труба PP-FIBER PN 20 {diameter} MM",
            {
                "максимальная рабочая температура, °с": "90",
                "рабочее давление, радиаторное отопление, бар": "6",
            },
            "VTp.700.FB20-0425.pdf",
        )
        for diameter in (20, 25)
    ]
    passport = _StubPassport(
        quote="Рабочее давление при радиаторном отоплении составляет 6 бар.",
        document="VTp.700.FB20-0425.pdf",
    )
    service = _service(products, passport)
    session = SessionState(
        session_id="pipe-follow-up",
        history=[
            {
                "role": "user",
                "content": (
                    "Какая максимальная рабочая температура у трубы "
                    "PP-FIBER PN 20?"
                ),
            },
            {"role": "assistant", "content": "90 °C"},
        ],
    )

    evidence = service.evaluate(
        "А какое давление при радиаторном отоплении?",
        session,
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.predicate == "radiator_heating_pressure_bar"
    assert evidence.request.product_ref.kind == ProductReferenceKind.CURRENT_FOCUS
    assert evidence.value == "6"
    assert evidence.unit == "бар"
    assert len(passport.calls) == 1


def test_power_rationale_never_searches_unscoped_passports() -> None:
    boiler = _product(
        "BOILER-24",
        "Котёл 24 кВт",
        {"мощность, квт": "24"},
        "boiler.pdf",
    )
    passport = _StubPassport(quote="Давление газа 2,75 кПа", document="boiler.pdf")
    service = _service([boiler], passport)
    session = SessionState(
        session_id="boiler",
        product_focus=ProductFocusState(sku=boiler.sku, category="boilers"),
    )

    evidence = service.evaluate("А почему именно такая мощность?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.REJECTED
    assert evidence.reason_code == "sizing_rationale_is_not_a_product_passport_fact"
    assert passport.calls == []
    rendered = render_product_fact_evidence(evidence)
    assert "давлен" not in rendered.casefold()
    assert "теплопотер" in rendered.casefold()


def test_ambiguous_pronoun_does_not_start_global_passport_search() -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос VALTEC",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    passport = _StubPassport()
    service = _service([pump], passport)

    evidence = service.evaluate(
        "Какая у него монтажная длина?",
        SessionState(session_id="ambiguous"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.AMBIGUOUS
    assert evidence.request.product_ref.candidate_skus == ()
    assert passport.calls == []
    assert "всем паспортам небезопасно" in render_product_fact_evidence(evidence)


def test_unknown_product_attribute_is_a_typed_refusal_without_a_value() -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос VALTEC",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    passport = _StubPassport()
    service = _service([pump], passport)

    evidence = service.evaluate(
        "Какой у VRS.254.18.0 цвет корпуса?",
        SessionState(session_id="unknown-attribute"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.NOT_FOUND
    assert evidence.request.predicate == "unsupported_product_fact"
    assert evidence.value is None
    assert evidence.request.product_ref.canonical_sku == "VRS.254.18.0"
    assert passport.calls == []
    rendered = render_product_fact_evidence(evidence)
    assert "не удалось подтвердить" in rendered
    assert "180" not in rendered


def test_partial_sku_has_priority_over_stale_product_kind_context() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC",
        {},
        "VT.1500-0624.pdf",
    )
    passport = _StubPassport(document="VT.1500-0624.pdf")
    service = _service([head], passport)
    session = SessionState(
        session_id="head",
        category="radiator_fittings",
        slots={"product_kind": "thermostatic_valve"},
    )

    evidence = service.evaluate("А головка VT.1500 подойдёт?", session)

    assert evidence is not None
    assert evidence.status == ProductFactStatus.REJECTED
    assert evidence.request.product_ref.kind == ProductReferenceKind.PARTIAL_SKU
    assert evidence.request.product_ref.canonical_sku == "VT.1500.0.0"
    assert passport.calls == []
    rendered = render_product_fact_evidence(evidence)
    assert "VT.1500.0.0" in rendered
    assert "артикула нет" not in rendered.casefold()
    assert "совместимость" in rendered.casefold()


def test_verified_fact_overrides_planner_wait_action_for_explicit_predicate() -> None:
    pump = _product(
        "VRS.254.18.0",
        "Насос VALTEC RS 25/4-180",
        {"монтажная длина, мм": "180"},
        "VRS-0725.pdf",
    )
    service = _service([pump], _StubPassport())
    session = SessionState(session_id="candidate", last_products=[_card(pump)])
    evidence = service.evaluate("Какая у первого монтажная длина?", session)
    assert evidence is not None

    state = DialogueStateV2(
        turn_number=0,
        tasks=(
            CustomerTask(
                task_id="task-fact",
                act=TaskAct.EXPLAIN,
                priority=100,
                source="semantic",
                source_turn=1,
            ),
        ),
    )
    action_plan = NextActionPlan(
        primary=NextAction(
            kind=NextActionKind.WAIT_FOR_SEMANTIC_UNDERSTANDING,
            task_id="task-fact",
            information_request_id="request-fact",
            reason_code="direct_question_has_priority",
        ),
        task_ids=("task-fact",),
    )
    outcome = DialogueV2Outcome(
        status="skipped",
        state_before=state,
        state_after=state,
        next_action_plan=action_plan,
        skip_reason="contentful current_message reduced to empty semantic frame",
    )
    source = AnswerSourceSnapshot(
        source_revision="catalog-revision",
        products=(
            CatalogAnswerProduct(
                sku=pump.sku,
                name=pump.name,
                product_kind=ProductKind.CIRCULATION_PUMP,
                role=CatalogProductRole.BASE_PRODUCT,
                price=pump.price,
                currency=pump.currency,
                stock_status=pump.stock_status,
                stock_qty=pump.stock_qty,
                url=pump.url,
            ),
        ),
    )
    base = V2TurnCandidate(
        turn_id="turn-fact",
        state_before=DialogueStateV2(),
        state_after=state,
        source_revision=source.source_revision,
        catalog_revision=source.source_revision,
        validation_status="accepted",
        task_acts=(TaskAct.EXPLAIN,),
        product_kinds=(ProductKind.CIRCULATION_PUMP,),
        semantic_accepted=False,
        contracts_resolved=False,
        eligible_for_delivery=False,
        rejection_reason_codes=("verified_direct_answer_source_missing",),
    )

    candidate = build_v2_product_fact_candidate(
        outcome,
        base,
        evidence,
        source,
        session_id="candidate",
        turn_id="turn-fact",
    )

    assert candidate is not None
    assert candidate.eligible_for_delivery is True
    assert candidate.next_action == NextActionKind.ANSWER_DIRECT_QUESTION
    assert "180 мм" in candidate.response.answer
    assert candidate.semantic_accepted is True
    assert candidate.state_after.turn_number == 1
    assert candidate.state_after.answer_plan_summary is not None
    assert candidate.contracts_resolved is True
    assert candidate.validation_status == "accepted"
    assert candidate.next_action == NextActionKind.ANSWER_DIRECT_QUESTION
    assert candidate.response is not None
    assert "180 мм" in candidate.response.answer
    assert [item.sku for item in candidate.response.products] == [pump.sku]
    assert candidate.state_after.answer_plan_summary is not None
    assert (
        candidate.state_after.answer_plan_summary.information_fulfilled_outputs
        == (RequestedInformationOutput.EXPLANATION,)
    )


def test_numeric_sku_without_marker_resolves_to_the_exact_catalogue_product() -> None:
    boiler = _product(
        "2202210",
        "Котел электрический Arderia E9, 9 кВт",
        {"количество контуров": "1"},
        "arderias-e9.pdf",
    )
    boiler.brand = "Arderia"
    service = _service([boiler], _StubPassport(quote="паспорт не потребовался"))

    evidence = service.evaluate(
        "У котла Arderia E9 2202210 сколько контуров?",
        SessionState(session_id="numeric-article"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.EXACT_SKU
    assert evidence.request.product_ref.canonical_sku == "2202210"
    assert evidence.request.predicate == "circuits"
    assert evidence.value == "1"


def test_context_bound_five_digit_and_slash_skus_use_the_same_product_fact_resolver() -> None:
    numeric = _product(
        "11677",
        "Термостатический клапан",
        {"количество контуров": "1"},
        "numeric.pdf",
    )
    slash = _product(
        "68/2/8",
        "Тестовый товар со slash-SKU",
        {"количество контуров": "2"},
        "slash.pdf",
    )
    service = _service([numeric, slash], _StubPassport(quote="паспорт не потребовался"))

    numeric_evidence = service.evaluate(
        "У товара 11677 сколько контуров?",
        SessionState(session_id="five-digit-product-fact"),
    )
    slash_evidence = service.evaluate(
        "У SKU 68/2/8 сколько контуров?",
        SessionState(session_id="slash-product-fact"),
    )

    assert numeric_evidence is not None
    assert numeric_evidence.request.product_ref.canonical_sku == "11677"
    assert slash_evidence is not None
    assert slash_evidence.request.product_ref.canonical_sku == "68/2/8"


def test_builtin_pump_question_uses_only_the_resolved_boiler_document() -> None:
    boiler = _product(
        "2202210",
        "Котел электрический Arderia E9, 9 кВт",
        {},
        "arderias-e9.pdf",
    )
    boiler.documents = [
        ProductDocument(
            filename="arderias-e9.pdf",
            document_kind="passport",
            text="В конструкции котла предусмотрен встроенный циркуляционный насос.",
        )
    ]
    passport = _StubPassport(quote="этот вызов не нужен")
    service = _service([boiler], passport)

    evidence = service.evaluate(
        "Есть ли в котле 2202210 встроенный насос?",
        SessionState(session_id="builtin-pump-fact"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.predicate == "integrated_circulation_pump"
    assert evidence.request.product_ref.canonical_sku == "2202210"
    assert evidence.value == "есть"
    assert evidence.source_kind == "passport_document_exact"
    assert evidence.document == "arderias-e9.pdf"
    assert passport.calls == []
    assert "в привязанной документации" in render_product_fact_evidence(evidence).casefold()


def test_builtin_pump_question_never_treats_missing_words_as_absence() -> None:
    boiler = _product(
        "BOILER-UNKNOWN",
        "Котел без детального описания",
        {},
        "unknown.pdf",
    )
    service = _service([boiler], _StubPassport())

    evidence = service.evaluate(
        "Есть ли в этом котле встроенный насос?",
        SessionState(
            session_id="builtin-pump-unknown",
            product_focus=ProductFocusState(sku=boiler.sku),
        ),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.NOT_FOUND
    assert evidence.value is None
    assert evidence.reason_code == "integrated_pump_not_explicitly_confirmed"


def test_attached_document_content_is_part_of_the_v2_source_revision() -> None:
    boiler = _product(
        "BOILER-SOURCE",
        "Котел электрический 9 кВт",
        {"Тип товара": "Котел"},
        "boiler.pdf",
    )
    boiler.documents = [
        ProductDocument(filename="boiler.pdf", text="Встроенный циркуляционный насос.")
    ]
    changed = boiler.model_copy(
        update={
            "documents": [
                ProductDocument(
                    filename="boiler.pdf",
                    text="Циркуляционный насос в изделие не встроен.",
                )
            ]
        }
    )
    registry = ProductContractRegistry()
    first = build_answer_source_snapshot(
        [boiler], [normalize_catalog_product(boiler, registry)]
    )
    second = build_answer_source_snapshot(
        [changed], [normalize_catalog_product(changed, registry)]
    )

    assert first.source_revision != second.source_revision


def test_document_binding_scope_is_part_of_the_v2_source_revision() -> None:
    boiler = _product(
        "BOILER-BINDING",
        "Котел электрический 9 кВт",
        {"Тип товара": "Котел"},
        "boiler.pdf",
    )
    boiler.documents = [
        ProductDocument(
            filename="boiler.pdf",
            text="Встроенный циркуляционный насос.",
            binding_scope="exact_sku",
            binding_value="BOILER-BINDING",
        )
    ]
    changed = boiler.model_copy(
        update={
            "documents": [
                ProductDocument(
                    filename="boiler.pdf",
                    text="Встроенный циркуляционный насос.",
                    binding_scope="sku_prefix",
                    binding_value="BOILER",
                )
            ]
        }
    )
    registry = ProductContractRegistry()
    first = build_answer_source_snapshot(
        [boiler], [normalize_catalog_product(boiler, registry)]
    )
    second = build_answer_source_snapshot(
        [changed], [normalize_catalog_product(changed, registry)]
    )

    assert first.source_revision != second.source_revision


def test_strict_brand_model_reference_reaches_circuits_fact_without_sku() -> None:
    boiler = _product(
        "2202210",
        "Котел электрический Arderia E9, 9 кВт",
        {"количество контуров": "1"},
        "arderias-e9.pdf",
    )
    boiler.brand = "Arderia"
    service = _service([boiler], _StubPassport(quote="паспорт не потребовался"))

    evidence = service.evaluate(
        "У котла Arderia E9 сколько контуров?",
        SessionState(session_id="named-model"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.kind == ProductReferenceKind.NAMED_PRODUCT
    assert evidence.request.product_ref.canonical_sku == "2202210"
    assert evidence.request.predicate == "circuits"


def test_exact_passport_thread_can_answer_without_a_duplicate_card_attribute() -> None:
    """VT.5000's exact, verified document is sufficient for M30×1,5.

    The card does not duplicate this interface fact.  The V2 evidence gate may
    still answer only because the requested product has one exact document,
    the verifier accepted the quote and the value is explicitly present there.
    """

    head = _product(
        "VT.5000.0.0",
        "Термостатическая головка VALTEC VT.5000",
        {},
        "0962d51dab5c3219f584820a92d556aa.pdf",
    )
    passport = _StubPassport(
        quote="Присоединительная резьба термостатической головки: M30×1,5.",
        document="0962d51dab5c3219f584820a92d556aa.pdf",
    )
    service = _service([head], passport)
    session = SessionState(session_id="vt5000", last_products=[_card(head)])

    evidence = service.evaluate(
        "Какая у VT.5000 посадочная резьба?",
        session,
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.canonical_sku == "VT.5000.0.0"
    assert evidence.request.predicate == "thermostatic_head_thread"
    assert evidence.value == "M30×1,5"
    assert evidence.source_kind == "passport_document_exact"
    assert evidence.document == "0962d51dab5c3219f584820a92d556aa.pdf"
    assert evidence.verifier_status == "accepted"


def test_exact_boiler_passport_can_answer_expansion_tank_volume() -> None:
    """A verified exact-model quote may expose the built-in tank volume."""

    boiler = _product(
        "8216262000",
        "Котел электрический E.C.A. Arceus ST 6",
        {},
        "63109b6ad4cd19.27758769.pdf",
    )
    boiler.documents = [
        boiler.documents[0].model_copy(
            update={
                "binding_scope": "exact_sku",
                "binding_value": boiler.sku,
            }
        )
    ]
    passport = _StubPassport(
        quote="Расширительный бак (8 литров)",
        document="63109b6ad4cd19.27758769.pdf",
    )
    service = _service([boiler], passport)

    evidence = service.evaluate(
        "Какой объём расширительного бака у котла 8216262000?",
        SessionState(session_id="eca-expansion-tank"),
    )

    assert evidence is not None
    assert evidence.status == ProductFactStatus.ANSWERED
    assert evidence.request.product_ref.canonical_sku == "8216262000"
    assert evidence.request.predicate == "expansion_tank_volume_l"
    assert evidence.value == 8
    assert evidence.unit == "л"
    assert evidence.source_kind == "passport_document_exact"
    assert evidence.document == "63109b6ad4cd19.27758769.pdf"
    assert evidence.verifier_status == "accepted"
    assert "8 л" in render_product_fact_evidence(evidence)


def test_compound_boiler_question_evaluates_pump_and_tank_independently() -> None:
    boiler = _product(
        "8216262000",
        "Котел электрический E.C.A. Arceus ST 6",
        {},
        "63109b6ad4cd19.27758769.pdf",
    )
    boiler.brand = "E.C.A"
    boiler.documents = [
        boiler.documents[0].model_copy(
            update={
                "binding_scope": "exact_sku",
                "binding_value": boiler.sku,
                "text": (
                    "Встроенный циркуляционный насос. "
                    "Расширительный бак (8 литров)."
                ),
            }
        )
    ]
    passport = _StubPassport(
        quote="Расширительный бак (8 литров)",
        document="63109b6ad4cd19.27758769.pdf",
    )
    service = _service([boiler], passport)

    evidence = service.evaluate_many(
        "У котла E.C.A. Arceus ST 6 внутри есть циркуляционный насос и "
        "расширительный бак?",
        SessionState(session_id="eca-compound-parts"),
    )

    assert [item.request.predicate for item in evidence] == [
        "integrated_circulation_pump",
        "expansion_tank_volume_l",
    ]
    assert [item.status for item in evidence] == [
        ProductFactStatus.ANSWERED,
        ProductFactStatus.ANSWERED,
    ]
    assert evidence[0].value == "есть"
    assert evidence[1].value == 8
    assert {
        item.request.product_ref.canonical_sku for item in evidence
    } == {"8216262000"}
