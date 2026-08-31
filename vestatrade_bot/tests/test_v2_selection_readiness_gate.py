"""Protected-Preview gates for V2 Selection/readiness requirements.

The detailed contract tests exercise normalization, readiness and ranking in
isolation.  These cases deliberately go through ``/chat``'s V2 assembly and
delivery boundary, so a correct catalogue plan cannot accidentally be counted
as a buyer-visible result when Preview would fall back to Legacy.
"""

from __future__ import annotations

import json

from app.agents.orchestrator import ChatOrchestrator
from app.agents.semantic_interpreter import SemanticInterpretationResult, TurnUnderstanding
from app.config import get_settings
from app.models import DialogueQAMode, Product, ProductDocumentFlowHeadPoint


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    attributes: dict[str, str],
    price: float = 1_000,
    stock_status: str = "в наличии",
    stock_qty: int = 5,
    description: str = "",
    brand: str = "",
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        price=price,
        currency="RUB",
        stock_status=stock_status,
        stock_qty=stock_qty,
        url=f"https://example.test/{sku}",
        image_url=f"https://example.test/{sku}.jpg",
        attributes_normalized=attributes,
        description=description,
        brand=brand,
    )


def _known(
    name: str,
    value: str | int | float,
    evidence: str,
    *,
    unit: str | None = None,
    polarity: str = "required",
    applies_to_product: int | None = 0,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "status": "known",
        "polarity": polarity,
        "applies_to_product": applies_to_product,
        "evidence": evidence,
    }


def _frame(
    *,
    product: dict[str, object] | None = None,
    constraints: list[dict[str, object]] | None = None,
    show: bool = False,
    operation: str = "new",
    answers_pending_question: bool = False,
    acts: list[str] | None = None,
    references: list[dict[str, object]] | None = None,
    selection_preferences: list[dict[str, object]] | None = None,
) -> TurnUnderstanding:
    return TurnUnderstanding.model_validate(
        {
            "schema_version": "1.3",
            "language": "ru",
            "operation": operation,
            "acts": acts or ["find"],
            "products": ([product] if product is not None else []),
            "constraints": constraints or [],
            "references": references or [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": (
                [
                    {
                        "kind": "continue_with_confirmed_facts",
                        "evidence": "Покажите варианты",
                    }
                ]
                if show
                else []
            ),
            "selection_preferences": selection_preferences or [],
            "selection_strategy": {
                "kind": "continue_with_confirmed_facts" if show else "standard",
                "evidence": "Покажите варианты" if show else None,
            },
            "information_requests": [],
            "answers_pending_question": answers_pending_question,
            "confidence": 0.99,
        }
    )


def _semantic(understanding: TurnUnderstanding) -> SemanticInterpretationResult:
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        model="test/semantic",
        latency_ms=0,
        understanding=understanding,
    )


def _preview_settings(tmp_path):
    return get_settings().model_copy(
        update={
            "llm_provider": "disabled",
            "embeddings_enabled": False,
            "diagnostic_telemetry_enabled": True,
            "diagnostic_trace_path": tmp_path / "v2-selection-readiness.jsonl",
            "dialogue_v2_routing_enabled": False,
            "dialogue_v2_shadow_compare_enabled": False,
            "dialogue_v2_live_delivery_enabled": False,
            "dialogue_v2_internal_canary_enabled": False,
            "dialogue_v2_internal_canary_percent": 0,
            "dialogue_v2_qa_controls_enabled": True,
            "dialogue_v2_qa_control_token": "qa-secret",
            "commerce_external_execution_enabled": False,
        }
    )


def _preview_response(
    bot: ChatOrchestrator,
    *,
    session_id: str,
    turn_id: str,
    message: str,
):
    return bot.handle_chat(
        session_id,
        message,
        client_turn_id=turn_id,
        qa_mode=DialogueQAMode.V2_PREVIEW,
    )


def _assert_v2_owner(settings) -> dict[str, object]:
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 1
    assert traces[0]["cutover_v2"]["decision"]["owner_candidate"] == "v2"
    return traces[0]


def test_preview_ppr_selection_uses_confirmed_facts_without_repeat_question(
    tmp_path,
    monkeypatch,
) -> None:
    """Heating context must stay a pipe requirement, not become a radiator."""

    product = _product(
        "PPR-GF-25",
        "Труба PP-R 25 мм армированная стекловолокном для отопления",
        "Трубы полипропиленовые",
        attributes={
            "Тип товара": "Труба",
            "Диаметр, мм": "25",
            "Армирование": "Стекловолокно",
            "Назначение": "Отопление",
            "Максимальная рабочая температура": "95 C",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[product])
    understanding = _frame(
        product={
            "text": "ППР",
            "canonical_type": "pipe",
            "category": "pipes",
            "role": "target",
            "evidence": "ППР",
        },
        constraints=[
            _known("pipe_service", "heating", "радиаторная магистраль"),
            _known("diameter_mm", 25, "25 мм", unit="mm"),
            _known("reinforcement", "glass_fiber", "стекловолокном"),
            _known("operating_temperature_c", 90, "90 °C", unit="c"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-ppr-selection",
        turn_id="v2-ppr-selection-1",
        message=(
            "Нужна ППР 25 армированная стекловолокном на радиаторную "
            "магистраль, подача 90 °С. Покажите варианты"
        ),
    )

    assert [item.sku for item in response.products] == ["PPR-GF-25"]
    assert "для чего" not in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["PPR-GF-25"]
    assert selection["outcome_gate_passed"] is True


def test_preview_price_preference_orders_only_technically_matching_cards(
    tmp_path,
    monkeypatch,
) -> None:
    """Price preference reaches protected Preview without weakening PPR facts."""

    common_attributes = {
        "Тип товара": "Труба",
        "Диаметр, мм": "25",
        "Армирование": "Стекловолокно",
        "Назначение": "Отопление",
        "Максимальная рабочая температура": "95 C",
    }
    expensive = _product(
        "PPR-GF-25-500",
        "Труба PP-R 25 мм армированная стекловолокном для отопления",
        "Трубы полипропиленовые",
        attributes=common_attributes,
        price=500,
    )
    cheap = _product(
        "PPR-GF-25-300",
        "Труба PP-R 25 мм армированная стекловолокном для отопления",
        "Трубы полипропиленовые",
        attributes=common_attributes,
        price=300,
    )
    wrong_service = _product(
        "PPR-GF-25-WATER-100",
        "Труба PP-R 25 мм армированная стекловолокном для воды",
        "Трубы полипропиленовые",
        attributes={**common_attributes, "Назначение": "Водоснабжение"},
        price=100,
        description="Труба для холодного водоснабжения",
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=[expensive, cheap, wrong_service],
    )
    understanding = _frame(
        product={
            "text": "ППР",
            "canonical_type": "pipe",
            "category": "pipes",
            "role": "target",
            "evidence": "ППР",
        },
        constraints=[
            _known("pipe_service", "heating", "для отопления"),
            _known("diameter_mm", 25, "25 мм", unit="mm"),
            _known("reinforcement", "glass_fiber", "стекловолокном"),
            _known("operating_temperature_c", 90, "90 °C", unit="c"),
        ],
        show=True,
        selection_preferences=[
            {"kind": "price_lowest", "value": None, "evidence": "подешевле"}
        ],
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-price-preference",
        turn_id="v2-price-preference-1",
        message="Нужна ППР 25 со стекловолокном для отопления, подешевле",
    )

    assert [item.sku for item in response.products] == [
        "PPR-GF-25-300",
        "PPR-GF-25-500",
    ]
    assert "отсортированы по цене" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["PPR-GF-25-300", "PPR-GF-25-500"]
    assert (
        "price_ordered_among_technically_presentable_candidates"
        in selection["ordering_reason_codes"]
    )


def test_preview_strict_brand_filter_never_substitutes_another_brand(
    tmp_path,
    monkeypatch,
) -> None:
    """«Только VALTEC» remains a technical-safe filter, not a tie-break."""

    attributes = {
        "Тип товара": "Труба",
        "Диаметр, мм": "25",
        "Армирование": "Стекловолокно",
        "Назначение": "Отопление",
        "Максимальная рабочая температура": "95 C",
    }
    valtec = _product(
        "PPR-VALTEC-25",
        "Труба PP-R 25 мм VALTEC для отопления",
        "Трубы полипропиленовые",
        attributes=attributes,
        price=700,
        brand="VALTEC",
    )
    other = _product(
        "PPR-OTHER-25",
        "Труба PP-R 25 мм другой марки для отопления",
        "Трубы полипропиленовые",
        attributes=attributes,
        price=300,
        brand="OTHER",
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[valtec, other])
    understanding = _frame(
        product={
            "text": "ППР",
            "canonical_type": "pipe",
            "category": "pipes",
            "role": "target",
            "evidence": "ППР",
        },
        constraints=[
            _known("pipe_service", "heating", "для отопления"),
            _known("diameter_mm", 25, "25 мм", unit="mm"),
            _known("reinforcement", "glass_fiber", "стекловолокном"),
            _known("brand", "VALTEC", "только VALTEC"),
        ],
        show=True,
        selection_preferences=[
            {"kind": "brand_required", "value": "VALTEC", "evidence": "только VALTEC"}
        ],
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-strict-brand",
        turn_id="v2-strict-brand-1",
        message="Нужна ППР 25 для отопления, только VALTEC",
    )

    assert [item.sku for item in response.products] == ["PPR-VALTEC-25"]
    trace = _assert_v2_owner(settings)
    assert trace["cutover_v2"]["selection_delivery"]["ordered_skus"] == [
        "PPR-VALTEC-25"
    ]


def test_preview_stock_filter_is_rendered_as_a_buyer_visible_condition(
    tmp_path,
    monkeypatch,
) -> None:
    """Availability filtering must not appear as a raw boolean fact."""

    product = _product(
        "PPR-IN-STOCK-25",
        "Труба PP-R 25 мм для отопления",
        "Трубы полипропиленовые",
        attributes={
            "Тип товара": "Труба",
            "Диаметр, мм": "25",
            "Армирование": "Стекловолокно",
            "Назначение": "Отопление",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[product])
    understanding = _frame(
        product={
            "text": "ППР",
            "canonical_type": "pipe",
            "category": "pipes",
            "role": "target",
            "evidence": "ППР",
        },
        constraints=[
            _known("pipe_service", "heating", "для отопления"),
            _known("diameter_mm", 25, "25 мм", unit="mm"),
            _known("reinforcement", "glass_fiber", "стекловолокном"),
            _known("stock_availability", True, "только в наличии"),
        ],
        show=True,
        selection_preferences=[
            {"kind": "stock_required", "value": True, "evidence": "только в наличии"}
        ],
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-stock-filter-copy",
        turn_id="v2-stock-filter-copy-1",
        message="Нужна ППР 25 для отопления, только в наличии",
    )

    assert [item.sku for item in response.products] == [product.sku]
    assert "только варианты с подтверждённым наличием" in response.answer.lower()
    assert "характеристика товара: да" not in response.answer.lower()


def test_preview_external_sewer_selection_never_substitutes_ppr(
    tmp_path,
    monkeypatch,
) -> None:
    """A safe V2 no-match is preferable to a card from another system."""

    sewer = _product(
        "SEWER-110",
        "Труба канализационная наружная 110 мм",
        "Канализационные системы",
        attributes={
            "Тип товара": "Труба канализационная",
            "Диаметр, мм": "110",
            "Назначение": "Наружная канализация",
        },
    )
    ppr = _product(
        "PPR-110",
        "Труба PP-R 110 мм",
        "Трубы полипропиленовые",
        attributes={"Тип товара": "Труба", "Диаметр, мм": "110"},
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[sewer, ppr])
    understanding = _frame(
        product={
            "text": "канализационная труба",
            "canonical_type": "sewer pipe",
            "category": "sewer",
            "role": "target",
            "evidence": "до септика",
        },
        constraints=[
            _known("sewer_scope", "external", "до септика"),
            _known("diameter_mm", 110, "110 мм", unit="mm"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-sewer-selection",
        turn_id="v2-sewer-selection-1",
        message="Нужна труба 110 от дома до септика. Покажите варианты",
    )

    assert [item.sku for item in response.products] == ["SEWER-110"]
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["SEWER-110"]
    assert "PPR-110" not in selection["ordered_skus"]


def test_preview_sewer_facts_accumulate_across_turns_before_selection(
    tmp_path,
    monkeypatch,
) -> None:
    """A short sewer dialogue retains scope and diameter until cards are safe.

    This ports the historical customer flow in which the buyer first says
    that the pipe is for a septic tank and only later provides DN. The first
    answer must ask one relevant question; the final delivery must use both
    facts and must never substitute a PPR pipe.
    """

    sewer = _product(
        "SEWER-110",
        "Труба канализационная наружная 110 мм",
        "Канализационные системы",
        attributes={
            "Тип товара": "Труба канализационная",
            "Диаметр, мм": "110",
            "Назначение": "Наружная канализация",
        },
    )
    ppr = _product(
        "PPR-110",
        "Труба PP-R 110 мм",
        "Трубы полипропиленовые",
        attributes={"Тип товара": "Труба", "Диаметр, мм": "110"},
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[sewer, ppr])

    opening = _frame(
        product={
            "text": "канализационная труба",
            "canonical_type": "sewer pipe",
            "category": "sewer",
            "role": "target",
            "evidence": "канализационная труба",
        },
    )
    scope = _frame(
        constraints=[
            _known(
                "sewer_scope",
                "external",
                "от дома до септика",
                applies_to_product=None,
            )
        ],
        operation="continue",
        answers_pending_question=True,
    )
    diameter = _frame(
        constraints=[
            _known(
                "diameter_mm",
                110,
                "110 мм",
                unit="mm",
                applies_to_product=None,
            )
        ],
        show=True,
        operation="continue",
        answers_pending_question=True,
    )
    frames = iter((opening, scope, diameter))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    session_id = "v2-sewer-accumulation"
    first = _preview_response(
        bot,
        session_id=session_id,
        turn_id="v2-sewer-accumulation-1",
        message="Нужна канализационная труба",
    )
    second = _preview_response(
        bot,
        session_id=session_id,
        turn_id="v2-sewer-accumulation-2",
        message="От дома до септика",
    )
    final = _preview_response(
        bot,
        session_id=session_id,
        turn_id="v2-sewer-accumulation-3",
        message="110 мм, покажите варианты",
    )

    assert first.products == []
    assert first.answer.count("?") == 1
    assert second.products == []
    assert second.answer.count("?") == 1
    assert [item.sku for item in final.products] == ["SEWER-110"]

    state = bot.sessions.snapshot(session_id).live_dialogue_state_v2
    assert state is not None
    active_facts = {
        item.name: item.value
        for item in state.constraints
        if item.active and item.goal_id == state.active_goal_id
    }
    assert active_facts.items() >= {
        "sewer_scope": "external",
        "diameter_mm": 110,
    }.items()

    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [
        trace["cutover_v2"]["decision"]["owner_candidate"] for trace in traces
    ] == ["v2", "v2", "v2"]
    selection = traces[-1]["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["SEWER-110"]
    assert "PPR-110" not in selection["ordered_skus"]


def test_preview_bare_pipe_asks_one_critical_question_without_random_cards(
    tmp_path,
    monkeypatch,
) -> None:
    """An insufficient request is a valid V2 outcome, not a failed search."""

    product = _product(
        "PIPE-25",
        "Труба PP-R 25 мм",
        "Трубы полипропиленовые",
        attributes={"Тип товара": "Труба", "Диаметр, мм": "25"},
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[product])
    understanding = _frame(
        product={
            "text": "труба",
            "canonical_type": "pipe",
            "category": "pipes",
            "role": "target",
            "evidence": "труба",
        },
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-bare-pipe",
        turn_id="v2-bare-pipe-1",
        message="Нужна труба",
    )

    assert response.products == []
    assert response.answer.count("?") == 1
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == []
    assert selection["outcome_gate_passed"] is True


def test_preview_explicit_stock_relaxation_replaces_only_its_goal_filter(
    tmp_path,
    monkeypatch,
) -> None:
    """``Наличие не важно`` widens one existing V2 selection honestly.

    A stock question and an in-stock-only purchase requirement are different
    actions. This gate covers the latter: the first delivery is limited to
    verified in-stock cards, then an explicit relaxation replaces that typed
    requirement on the same product goal and permits the matching out-of-stock
    card to be shown too.
    """

    unavailable = _product(
        "BASE-OUT",
        "Кран шаровой BASE 1/2 вн-вн, нет в наличии",
        "Водозапорная арматура",
        price=452,
        stock_status="нет в наличии",
        stock_qty=0,
        attributes={
            "Тип товара": "Кран шаровой",
            "Диаметр подключения, дюйм": "1/2",
            "Тип резьбы": "С внутренней резьбой (ff)",
        },
    )
    available = _product(
        "BASE-IN",
        "Кран шаровой BASE 1/2 вн-вн, в наличии",
        "Водозапорная арматура",
        price=503,
        stock_status="в наличии",
        stock_qty=4,
        attributes={
            "Тип товара": "Кран шаровой",
            "Диаметр подключения, дюйм": "1/2",
            "Тип резьбы": "С внутренней резьбой (ff)",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[unavailable, available])
    opening = _frame(
        product={
            "text": "кран BASE",
            "canonical_type": "ball valve",
            "category": "valves",
            "role": "target",
            "evidence": "кран BASE",
        },
        constraints=[
            _known("connection_size", "1/2", "1/2"),
            _known("connection_pattern", "female_female", "вн-вн"),
            _known("stock_availability", True, "только в наличии"),
        ],
        show=True,
    )
    relaxed = _frame(
        constraints=[
            _known(
                "stock_availability",
                True,
                "наличие не важно",
                polarity="excluded",
                applies_to_product=None,
            )
        ],
        show=True,
        operation="refine",
    )
    frames = iter((opening, relaxed))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    session_id = "v2-stock-relaxation"
    in_stock_only = _preview_response(
        bot,
        session_id=session_id,
        turn_id="v2-stock-relaxation-1",
        message="Нужны краны BASE 1/2 вн-вн, только в наличии. Покажите",
    )
    all_matching = _preview_response(
        bot,
        session_id=session_id,
        turn_id="v2-stock-relaxation-2",
        message="Наличие не важно, покажите все подходящие",
    )

    assert [item.sku for item in in_stock_only.products] == ["BASE-IN"]
    assert [item.sku for item in all_matching.products] == ["BASE-IN", "BASE-OUT"]
    state = bot.sessions.snapshot(session_id).live_dialogue_state_v2
    assert state is not None
    active_stock_facts = [
        item
        for item in state.constraints
        if item.active
        and item.name == "stock_availability"
        and item.goal_id == state.active_goal_id
    ]
    assert len(active_stock_facts) == 1
    assert active_stock_facts[0].polarity.value == "excluded"

    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [
        trace["cutover_v2"]["decision"]["owner_candidate"] for trace in traces
    ] == ["v2", "v2"]
    assert traces[0]["cutover_v2"]["selection_delivery"]["ordered_skus"] == [
        "BASE-IN"
    ]
    assert traces[1]["cutover_v2"]["selection_delivery"]["ordered_skus"] == [
        "BASE-IN",
        "BASE-OUT",
    ]


def test_preview_boiler_availability_analog_keeps_fuel_and_circuits_hard(
    tmp_path,
    monkeypatch,
) -> None:
    """A closest in-stock boiler is a labelled preliminary analogue only."""

    unavailable_exact = _product(
        "ELECTRIC-18-OUT",
        "Котёл электрический 18 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Электрический",
            "Мощность, кВт": "18",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "180",
        },
        stock_status="нет в наличии",
        stock_qty=0,
    )
    higher_in_stock = _product(
        "ELECTRIC-24-IN",
        "Котёл электрический 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Электрический",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    wrong_fuel = _product(
        "GAS-24-IN",
        "Котёл газовый 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Газовый",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=[unavailable_exact, higher_in_stock, wrong_fuel],
    )
    understanding = _frame(
        product={
            "text": "электрический котёл",
            "canonical_type": "electric_boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "электрический котёл",
        },
        constraints=[
            _known("boiler_type", "electric", "электрический"),
            _known("power_kw", 18, "18 кВт", unit="kW"),
            _known("area_m2", 150, "150 м²", unit="m2"),
            _known("circuits", 1, "только отопление"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-boiler-availability-analog",
        turn_id="v2-boiler-availability-analog-1",
        message="Нужен электрический одноконтурный котёл 18 кВт для дома 150 м²",
    )

    assert [item.sku for item in response.products] == ["ELECTRIC-24-IN"]
    assert "предваритель" in response.answer.lower()
    assert "24 кВт вместо запрошенных 18 кВт" in response.answer
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["availability_analog"] is True
    assert selection["ordered_skus"] == ["ELECTRIC-24-IN"]
    differences = selection["availability_analog_differences"]
    assert len(differences) == 1
    assert differences[0]["fact_name"] == "power_kw"
    assert differences[0]["requested_value"] == 18
    assert differences[0]["candidate_value"] == 24
    assert differences[0]["reason_code"] == (
        "availability_analog_higher_confirmed_power_in_stock"
    )


def test_preview_boiler_area_flow_keeps_facts_until_safe_preliminary_cards(
    tmp_path,
    monkeypatch,
) -> None:
    """Area is a preliminary proxy, never an implicit power calculation."""

    gas = _product(
        "GAS-24",
        "Котёл газовый 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Газовый",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    electric = _product(
        "ELECTRIC-24",
        "Котёл электрический 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Электрический",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[gas, electric])

    opening = _frame(
        product={
            "text": "котёл",
            "canonical_type": "boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "котёл",
        },
        constraints=[_known("area_m2", 150, "150 м²", unit="m2")],
    )
    fuel = _frame(
        constraints=[
            {
                **_known("boiler_type", "gas", "газовый"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    circuits = _frame(
        constraints=[
            {
                **_known("circuits", 1, "только отопление"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
        show=True,
    )
    frames = iter((opening, fuel, circuits))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    first = _preview_response(
        bot,
        session_id="v2-boiler-area-flow",
        turn_id="v2-boiler-area-flow-1",
        message="Нужен котёл для дома 150 м²",
    )
    second = _preview_response(
        bot,
        session_id="v2-boiler-area-flow",
        turn_id="v2-boiler-area-flow-2",
        message="Газовый",
    )
    third = _preview_response(
        bot,
        session_id="v2-boiler-area-flow",
        turn_id="v2-boiler-area-flow-3",
        message="Только отопление, покажите варианты",
    )

    assert first.products == []
    assert "газовый или электрический" in first.answer.lower()
    assert second.products == []
    assert "горяч" in second.answer.lower()
    assert [item.sku for item in third.products] == ["GAS-24"]
    assert "предваритель" in third.answer.lower()
    assert "электрическ" not in third.answer.lower()

    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 3
    assert all(
        trace["cutover_v2"]["decision"]["owner_candidate"] == "v2"
        for trace in traces
    )
    selection = traces[-1]["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["GAS-24"]
    assert selection["is_preliminary"] is True
    applied_facts = {
        item["name"]: item["value"] for item in selection["applied_facts"]
    }
    assert applied_facts.items() >= {
        "area_m2": 150,
        "boiler_type": "gas",
        "circuits": 1,
    }.items()


def test_preview_more_boilers_keeps_known_type_and_area_until_circuits_answered(
    tmp_path,
    monkeypatch,
) -> None:
    """A show-more command must continue the boiler task, never restart it.

    This ports the Legacy regression where ``Какие ещё котлы есть?`` used to
    erase the already answered fuel and area questions.  It is deliberately a
    Preview conversation: the assertion covers the customer-visible question,
    the typed state and the actual V2 owner together.
    """

    gas = _product(
        "GAS-24",
        "Котёл газовый 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Газовый",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[gas])
    opening = _frame(
        product={
            "text": "котёл",
            "canonical_type": "boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "Котлы",
        },
    )
    gas_turn = _frame(
        constraints=[
            {
                **_known("boiler_type", "gas", "Газовый"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    area_turn = _frame(
        constraints=[
            {
                **_known("area_m2", 240, "240 м²", unit="m2"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    # A request to show more cards is not an answer to the still-pending
    # one-/two-circuit question.  It must not authorise an unsafe generic
    # preliminary list.
    show_more = _frame(operation="continue", show=True)
    frames = iter((opening, gas_turn, area_turn, show_more))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    _preview_response(
        bot,
        session_id="v2-more-boilers",
        turn_id="v2-more-boilers-1",
        message="Котлы есть?",
    )
    _preview_response(
        bot,
        session_id="v2-more-boilers",
        turn_id="v2-more-boilers-2",
        message="Газовый",
    )
    _preview_response(
        bot,
        session_id="v2-more-boilers",
        turn_id="v2-more-boilers-3",
        message="240 м²",
    )
    response = _preview_response(
        bot,
        session_id="v2-more-boilers",
        turn_id="v2-more-boilers-4",
        message="Какие ещё котлы есть?",
    )

    assert response.products == []
    assert "горяч" in response.answer.lower()
    assert "газовый или электрический" not in response.answer.lower()
    state = bot.sessions.snapshot("v2-more-boilers").live_dialogue_state_v2
    assert state is not None
    active_facts = {
        item.name: item.value
        for item in state.constraints
        if item.active and item.goal_id == state.active_goal_id
    }
    assert active_facts.items() >= {"boiler_type": "gas", "area_m2": 240}.items()
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 4
    assert traces[-1]["cutover_v2"]["decision"]["owner_candidate"] == "v2"


def test_preview_repeated_electric_choice_does_not_forget_area_or_reask_fuel(
    tmp_path,
    monkeypatch,
) -> None:
    """A repeated answer is not a new boiler task and cannot reset context."""

    electric = _product(
        "ELECTRIC-24",
        "Котёл электрический 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Электрический",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[electric])
    opening = _frame(
        product={
            "text": "котёл",
            "canonical_type": "boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "котёл",
        },
        constraints=[_known("area_m2", 240, "240 м²", unit="m2")],
    )
    electric_turn = _frame(
        constraints=[
            {
                **_known("boiler_type", "electric", "Электрический"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    repeated_electric = _frame(
        constraints=[
            {
                **_known("boiler_type", "electric", "Электрический"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    frames = iter((opening, electric_turn, repeated_electric))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    _preview_response(
        bot,
        session_id="v2-electric-repeat",
        turn_id="v2-electric-repeat-1",
        message="Нужен котёл для дома 240 м²",
    )
    _preview_response(
        bot,
        session_id="v2-electric-repeat",
        turn_id="v2-electric-repeat-2",
        message="Электрический",
    )
    response = _preview_response(
        bot,
        session_id="v2-electric-repeat",
        turn_id="v2-electric-repeat-3",
        message="Электрический",
    )

    assert response.products == []
    assert "горяч" in response.answer.lower()
    assert "газовый или электрический" not in response.answer.lower()
    state = bot.sessions.snapshot("v2-electric-repeat").live_dialogue_state_v2
    assert state is not None
    active_facts = {
        item.name: item.value
        for item in state.constraints
        if item.active and item.goal_id == state.active_goal_id
    }
    assert active_facts.items() >= {"boiler_type": "electric", "area_m2": 240}.items()
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 3
    assert traces[-1]["cutover_v2"]["decision"]["owner_candidate"] == "v2"


def test_preview_radiator_room_area_is_only_a_source_backed_preliminary_proxy(
    tmp_path,
    monkeypatch,
) -> None:
    """A room area may narrow cards, but cannot become a heat-loss verdict.

    The smaller model has a manufacturer-declared coverage below the room
    area, so it must not be delivered.  The surviving card is still marked
    preliminary because neither physical radiator dimensions nor a design
    heat loss have been confirmed.
    """

    too_small = _product(
        "RADIATOR-12",
        "Радиатор биметаллический 6 секций",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "12",
        },
    )
    adequate = _product(
        "RADIATOR-20",
        "Радиатор биметаллический 10 секций",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "19.6",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[too_small, adequate])
    understanding = _frame(
        product={
            "text": "радиатор",
            "canonical_type": "radiator",
            "category": "radiators",
            "role": "target",
            "evidence": "радиатор",
        },
        constraints=[_known("area_m2", 16, "комната 16 м²", unit="m2")],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-radiator-area",
        turn_id="v2-radiator-area-1",
        message="Нужен радиатор в комнату 16 м². Покажите, что можно посмотреть",
    )

    assert [item.sku for item in response.products] == ["RADIATOR-20"]
    assert "предваритель" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == ["RADIATOR-20"]
    assert selection["is_preliminary"] is True
    applied_facts = {
        item["name"]: item["value"] for item in selection["applied_facts"]
    }
    assert applied_facts["area_m2"] == 16


def test_preview_circulation_pump_with_duty_point_shows_preliminary_cards_immediately(
    tmp_path,
    monkeypatch,
) -> None:
    """Flow and head are enough for a labelled pump shortlist before DN.

    DN and mounting length deliberately remain missing on the checked
    SelectionResult.  The UX improvement must not make the cards look like a
    confirmed installation match.
    """

    pump = _product(
        "PUMP-25-4",
        "Насос циркуляционный 25/4",
        "Насосное оборудование",
        attributes={"Тип товара": "Насос"},
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[pump])
    understanding = _frame(
        product={
            "text": "циркуляционный насос",
            "canonical_type": "circulation pump",
            "category": "pumps",
            "role": "target",
            "evidence": "циркуляционный насос",
        },
        constraints=[
            _known("duty_point_flow_l_h", 1.5, "расход 1,5 м3/ч", unit="m3/h"),
            _known("duty_point_head_m", 4, "напор 4 м", unit="m"),
        ],
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-pump-auto-preliminary",
        turn_id="v2-pump-auto-preliminary-1",
        message="Нужен циркуляционный насос: расход 1,5 м3/ч, напор 4 м",
    )

    assert [item.sku for item in response.products] == [pump.sku]
    assert "предваритель" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "shown"
    assert selection["is_preliminary"] is True
    assert set(selection["preliminary_fact_names"]) >= {
        "diameter_mm",
        "mounting_length_mm",
    }


def test_preview_irrigation_pump_asks_source_then_uses_borehole_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    """Irrigation never enters the circulation-pump or false-no-match path."""

    candidate = _product(
        "WELL-60",
        "Скважинный насос WELL-60",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "60",
            "Макс. производительность, л/ч": "3000",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[candidate])
    first = _frame(
        product={
            "text": "насос для полива",
            "canonical_type": "irrigation_pump",
            "category": "pumps",
            "role": "target",
            "evidence": "насос для полива",
        },
    )
    second = _frame(
        product={
            "text": "из скважины",
            "canonical_type": "borehole_pump",
            "category": "pumps",
            "role": "target",
            "evidence": "из скважины",
        },
        constraints=[
            _known("water_source", "borehole", "из скважины"),
            _known(
                "static_water_level_m",
                18,
                "до воды около 18 метров",
                unit="m",
            ),
        ],
        operation="correct",
        answers_pending_question=True,
    )
    understandings = iter((first, second))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(understandings)),
    )

    initial = _preview_response(
        bot,
        session_id="v2-irrigation-borehole",
        turn_id="v2-irrigation-borehole-1",
        message="Нужен насос для полива на даче",
    )
    assert initial.products == []
    assert "скважин" in initial.answer.lower()
    assert "циркуляцион" in initial.answer.lower()

    follow_up = _preview_response(
        bot,
        session_id="v2-irrigation-borehole",
        turn_id="v2-irrigation-borehole-2",
        message="Из скважины, до воды около 18 метров",
    )

    assert follow_up.products == []
    assert "нет товара" not in follow_up.answer.lower()
    assert "поднять воду" in follow_up.answer.lower()
    state = bot.sessions.snapshot("v2-irrigation-borehole").live_dialogue_state_v2
    assert state is not None
    active_goal = next(item for item in state.product_goals if item.goal_id == state.active_goal_id)
    assert active_goal.canonical_type == "borehole_pump"
    facts = {
        item.name: item.value
        for item in state.constraints
        if item.active and item.goal_id == active_goal.goal_id
    }
    assert facts["static_water_level_m"] == 18
    assert "required_head_m" not in facts
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 2
    assert all(trace["cutover_v2"]["decision"]["owner_candidate"] == "v2" for trace in traces)


def test_preview_borehole_pump_asks_for_pipe_before_calculating_head(
    tmp_path,
    monkeypatch,
) -> None:
    """V2 reuses the established calculation boundary instead of guessing head."""

    candidate = _product(
        "WELL-60",
        "Скважинный насос WELL-60",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "60",
            "Производительность, л/мин": "50",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[candidate])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "Скважинный насос",
        },
        constraints=[
            _known("dynamic_water_level_m", 20, "динамический уровень 20 м", unit="m"),
            _known("lift_height_m", 5, "высота подъёма 5 м", unit="m"),
            _known("horizontal_run_m", 30, "горизонтальная трасса 30 м", unit="m"),
            _known("required_pressure_bar", 3, "давление 3 бар", unit="bar"),
            _known("required_flow_l_h", 2, "расход 2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-missing-head",
        turn_id="v2-borehole-missing-head-1",
        message=(
            "Скважинный насос: динамический уровень 20 м, высота подъёма 5 м, "
            "горизонтальная трасса 30 м, давление 3 бар, расход 2 м3/ч. "
            "Покажите варианты"
        ),
    )

    assert response.products == []
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "need_clarification"
    assert selection["missing_critical_fact"] == "discharge_diameter_mm"
    assert selection["outcome_gate_passed"] is True
    assert "напорной трубы" in response.answer.lower()
    state = bot.sessions.snapshot("v2-borehole-missing-head").live_dialogue_state_v2
    assert state is not None
    assert not any(
        fact.active
        and fact.name == "required_head_m"
        and fact.source == "borehole_hydraulic_calculation"
        for fact in state.constraints
    )


def test_preview_borehole_pump_derives_preliminary_head_and_filters_ratings(
    tmp_path,
    monkeypatch,
) -> None:
    """A lower-capacity borehole pump cannot pass as a soft alternative."""

    low_flow = _product(
        "WELL-LOW-FLOW",
        "Скважинный насос WELL-LOW-FLOW",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "70",
            "Макс. производительность, л/ч": "1200",
        },
    )
    missing_rating = _product(
        "WELL-MISSING-FLOW",
        "Скважинный насос WELL-MISSING-FLOW",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
    )
    suitable = _product(
        "WELL-OK",
        "Скважинный насос WELL-OK",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "60",
            "Макс. производительность, л/ч": "3000",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(
        settings=settings,
        products=[low_flow, missing_rating, suitable],
    )
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "Скважинный насос",
        },
        constraints=[
            _known("dynamic_water_level_m", 18, "динамический уровень 18 м", unit="m"),
            _known("lift_height_m", 3, "высота подъёма 3 м", unit="m"),
            _known("horizontal_run_m", 35, "трасса 35 м", unit="m"),
            _known("required_pressure_bar", 3, "давление 3 бар", unit="bar"),
            _known("required_flow_l_h", 2, "расход 2 м3/ч", unit="m3/h"),
            _known("discharge_diameter_mm", 32, "труба 32 мм", unit="mm"),
            _known("discharge_sdr", 11, "SDR11"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-hard-filters",
        turn_id="v2-borehole-hard-filters-1",
        message=(
            "Скважинный насос: динамический уровень 18 м, подъём 3 м, трасса "
            "35 м, давление 3 бар, расход 2 м3/ч, труба 32 мм SDR11. "
            "Покажите варианты"
        ),
    )

    assert [item.sku for item in response.products] == ["WELL-OK"]
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    constraints = {item["name"]: item for item in selection["hard_constraints"]}
    assert constraints["required_flow_l_h"]["value"] == 2000
    assert constraints["required_flow_l_h"]["unit"] == "l/h"
    assert constraints["required_flow_l_h"]["polarity"] == "required"
    assert constraints["required_head_m"]["unit"] == "m"
    assert 50 < constraints["required_head_m"]["value"] < 70
    assert selection["ordered_skus"] == ["WELL-OK"]
    assert selection["is_preliminary"] is True
    assert selection["outcome_gate_passed"] is True
    assert (
        "catalogue_required_rating_missing"
        in selection["excluded_candidate_reason_codes"]["WELL-MISSING-FLOW"]
    )
    assert "кривой производителя" in response.answer.lower()
    state = bot.sessions.snapshot("v2-borehole-hard-filters").live_dialogue_state_v2
    assert state is not None
    derived = next(
        fact
        for fact in state.constraints
        if fact.active
        and fact.name == "required_head_m"
        and fact.source == "borehole_hydraulic_calculation"
    )
    assert 50 < float(derived.value) < 70


def test_preview_borehole_explicit_duty_stays_preliminary_not_engineering_match(
    tmp_path,
    monkeypatch,
) -> None:
    """A customer-supplied duty may filter ratings but cannot prove Q/H."""

    too_low = _product(
        "WELL-LOW",
        "Скважинный насос WELL-LOW",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "40",
            "Макс. производительность, л/ч": "3000",
        },
    )
    sufficient_rating = _product(
        "WELL-RATING",
        "Скважинный насос WELL-RATING",
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "60",
            "Макс. производительность, л/ч": "3000",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[too_low, sufficient_rating])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 45, "расчётный напор 45 м", unit="m"),
            _known("required_flow_l_h", 2, "расход 2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-explicit-duty",
        turn_id="v2-borehole-explicit-duty-1",
        message="Скважинный насос: расчётный напор 45 м, расход 2 м3/ч. Покажите варианты",
    )

    assert [item.sku for item in response.products] == ["WELL-RATING"]
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["is_preliminary"] is True
    assert "кривой производителя" in response.answer.lower()
    state = bot.sessions.snapshot("v2-borehole-explicit-duty").live_dialogue_state_v2
    assert state is not None
    assert not any(
        fact.active
        and fact.name == "required_head_m"
        and fact.source == "borehole_hydraulic_calculation"
        for fact in state.constraints
    )


def test_preview_borehole_pump_does_not_show_card_without_confirmed_flow_rating(
    tmp_path,
    monkeypatch,
) -> None:
    """A card with only maximum head cannot pass a head-and-flow shortlist."""

    head_only = _product(
        "11677",
        'Винтовой скважинный насос Unipump 3" ECO VINT 2',
        "Насосное оборудование",
        attributes={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[head_only])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 45, "расчётный напор 45 м", unit="m"),
            _known("required_flow_l_h", 2, "расход 2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-head-only-card",
        turn_id="v2-borehole-head-only-card-1",
        message="Скважинный насос: расчётный напор 45 м, расход 2 м3/ч. Покажите варианты",
    )

    assert response.products == []
    assert response.answer == (
        "В каталоге нет скважинного насоса с одновременно подтверждёнными "
        "максимальными напором и расходом под ваши исходные данные.\n"
        "Не показываю модель с неполными характеристиками как предварительно "
        "подходящую."
    )
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "no_match"
    assert (
        "catalogue_required_rating_missing"
        in selection["excluded_candidate_reason_codes"]["11677"]
    )


def test_preview_borehole_selection_uses_exact_passport_flow_projection(
    tmp_path,
    monkeypatch,
) -> None:
    """A verified passport row can fill a missing card rating, never the feed."""

    eco_vint = Product(
        sku="11677",
        name='Винтовой скважинный насос Unipump 3" ECO VINT 2 (550 Вт, кабель-20м)',
        brand="UNIPUMP",
        category_path="Насосное оборудование",
        price=9_528,
        currency="RUB",
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
        image_url="https://example.test/11677.jpg",
        attributes_normalized={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[eco_vint])
    source = bot.answer_source_snapshot_v2.product("11677")
    assert source is not None
    passport_flow = next(item for item in source.facts if item.name == "max_flow_l_h")
    assert passport_flow.value == 1500
    assert passport_flow.provenance.source == "passport"
    assert passport_flow.provenance.source_document == (
        "pasport-nasosy-skvazhinnye-unipump-ecovint.pdf"
    )

    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 40, "расчётный напор 40 м", unit="m"),
            _known("required_flow_l_h", 1.2, "расход 1,2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-passport-flow",
        turn_id="v2-borehole-passport-flow-1",
        message="Скважинный насос: напор 40 м, расход 1,2 м3/ч. Покажите варианты",
    )

    assert [item.sku for item in response.products] == ["11677"]
    assert "точная точка q/h" in response.answer.lower()
    assert "1200 л/ч" in response.answer
    assert "44 м" in response.answer
    assert "не гидравлический расчёт системы" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "shown"
    assert selection["ordered_skus"] == ["11677"]
    assert selection["passport_flow_head_evidence"] == [
        {
            "sku": "11677",
            "requested_flow_l_h": 1200.0,
            "required_head_m": 40.0,
            "passport_point": {
                "flow_l_h": 1200.0,
                "head_m": 44.0,
                "provenance": {
                    "source": "passport",
                    "source_field": "flow_head_curve",
                    "raw_value": "ECO VINT 2: Q=1200 л/ч; H=44 м",
                    "parser": "unipump_eco_vint_exact_qh_table_v1",
                    "source_document": "pasport-nasosy-skvazhinnye-unipump-ecovint.pdf",
                    "source_section": "3.4 Напорно-расходные характеристики, модель ECO VINT 2",
                },
            },
            "status": "clears_required_head",
        }
    ]


def test_preview_borehole_exact_passport_point_rejects_insufficient_head(
    tmp_path,
    monkeypatch,
) -> None:
    """A passport point can disprove a duty even when card maxima clear it."""

    eco_vint = Product(
        sku="11677",
        name='Винтовой скважинный насос Unipump 3" ECO VINT 2 (550 Вт, кабель-20м)',
        brand="UNIPUMP",
        category_path="Насосное оборудование",
        price=9_528,
        currency="RUB",
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
        image_url="https://example.test/11677.jpg",
        attributes_normalized={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[eco_vint])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 45, "расчётный напор 45 м", unit="m"),
            _known("required_flow_l_h", 1.2, "расход 1,2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-passport-point-below",
        turn_id="v2-borehole-passport-point-below-1",
        message="Скважинный насос: напор 45 м, расход 1,2 м3/ч. Покажите варианты",
    )

    assert response.products == []
    assert "1200 л/ч" in response.answer
    assert "44 м" in response.answer
    assert "нужно 45 м" in response.answer
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "no_match"
    assert selection["passport_flow_head_evidence"][0]["status"] == "below_required_head"
    assert (
        "passport_qh_exact_table_point_below_required_head"
        in selection["excluded_candidate_reason_codes"]["11677"]
    )


def test_preview_borehole_intermediate_flow_stays_preliminary_without_interpolation(
    tmp_path,
    monkeypatch,
) -> None:
    """A flow between table rows keeps the envelope-only preliminary path."""

    eco_vint = Product(
        sku="11677",
        name='Винтовой скважинный насос Unipump 3" ECO VINT 2 (550 Вт, кабель-20м)',
        brand="UNIPUMP",
        category_path="Насосное оборудование",
        price=9_528,
        currency="RUB",
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
        image_url="https://example.test/11677.jpg",
        attributes_normalized={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[eco_vint])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 40, "расчётный напор 40 м", unit="m"),
            _known("required_flow_l_h", 1.1, "расход 1,1 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-passport-no-interpolation",
        turn_id="v2-borehole-passport-no-interpolation-1",
        message="Скважинный насос: напор 40 м, расход 1,1 м3/ч. Покажите варианты",
    )

    assert [item.sku for item in response.products] == ["11677"]
    assert "точная точка q/h" not in response.answer.lower()
    assert "кривой производителя" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["passport_flow_head_evidence"] == []
    assert any(
        item["fact_name"] == "flow_head_curve"
        and item["candidate_sku"] == "11677"
        and item["reason_codes"] == ["passport_qh_exact_flow_not_listed"]
        for item in selection["constraint_dispositions"]
    )


def test_preview_borehole_passport_curve_conflict_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    """Contradictory document rows cannot silently become a shortlist proof."""

    eco_vint = Product(
        sku="11677",
        name='Винтовой скважинный насос Unipump 3" ECO VINT 2 (550 Вт, кабель-20м)',
        brand="UNIPUMP",
        category_path="Насосное оборудование",
        price=9_528,
        currency="RUB",
        stock_status="нет в наличии",
        stock_qty=0,
        url="https://example.test/11677",
        image_url="https://example.test/11677.jpg",
        attributes_normalized={
            "Тип товара": "Скважинный насос",
            "Максимальный напор, м": "90",
        },
        document_flow_head_points=[
            ProductDocumentFlowHeadPoint(
                flow_l_h=1200,
                head_m=43,
                document="conflicting-eco-vint-table.pdf",
                section="test conflicting ECO VINT 2 row",
                evidence="ECO VINT 2: Q=1200 л/ч; H=43 м",
                parser="test_conflicting_qh_table",
            )
        ],
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[eco_vint])
    understanding = _frame(
        product={
            "text": "скважинный насос",
            "canonical_type": "borehole pump",
            "category": "pumps",
            "role": "target",
            "evidence": "скважинный насос",
        },
        constraints=[
            _known("required_head_m", 40, "расчётный напор 40 м", unit="m"),
            _known("required_flow_l_h", 1.2, "расход 1,2 м3/ч", unit="m3/h"),
        ],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-borehole-passport-point-conflict",
        turn_id="v2-borehole-passport-point-conflict-1",
        message="Скважинный насос: напор 40 м, расход 1,2 м3/ч. Покажите варианты",
    )

    assert response.products == []
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "no_match"
    assert (
        "passport_qh_source_conflict"
        in selection["excluded_candidate_reason_codes"]["11677"]
    )


def test_preview_radiator_no_match_names_missing_declared_coverage_plainly(
    tmp_path,
    monkeypatch,
) -> None:
    """A smaller radiator is not silently offered as a room-area match."""

    too_small = _product(
        "RADIATOR-12",
        "Радиатор биметаллический 6 секций",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "12",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[too_small])
    understanding = _frame(
        product={
            "text": "радиатор",
            "canonical_type": "radiator",
            "category": "radiators",
            "role": "target",
            "evidence": "радиатор",
        },
        constraints=[_known("area_m2", 16, "комната 16 м²", unit="m2")],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-radiator-no-match",
        turn_id="v2-radiator-no-match-1",
        message="Нужен радиатор для комнаты 16 м². Покажите варианты",
    )

    assert response.products == []
    assert response.answer == (
        "В каталоге нет радиатора с заявленной площадью обогрева от 16 м².\n"
        "Радиаторы с меньшей заявленной площадью не показываю как подходящие."
    )
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["status"] == "no_match"
    assert selection["outcome_gate_passed"] is True
    assert selection["ordered_skus"] == []


def test_preview_radiators_are_sorted_by_closest_adequate_declared_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    """Closest sufficient coverage wins before the old lexical tie-breaker."""

    larger = _product(
        "A-RADIATOR-20",
        "Радиатор биметаллический 10 секций",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "20",
        },
    )
    closest = _product(
        "Z-RADIATOR-16",
        "Радиатор биметаллический 8 секций",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "16",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[larger, closest])
    understanding = _frame(
        product={
            "text": "радиатор",
            "canonical_type": "radiator",
            "category": "radiators",
            "role": "target",
            "evidence": "радиатор",
        },
        constraints=[_known("area_m2", 16, "комната 16 м²", unit="m2")],
        show=True,
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-radiator-nearest-area",
        turn_id="v2-radiator-nearest-area-1",
        message="Нужен радиатор для комнаты 16 м². Покажите варианты",
    )

    assert [item.sku for item in response.products] == [
        closest.sku,
        larger.sku,
    ]
    assert "соединению" not in response.answer.lower()
    assert "материал" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == [closest.sku, larger.sku]


def test_preview_bare_radiator_starts_with_physical_size_not_material(
    tmp_path,
    monkeypatch,
) -> None:
    """A bare radiator request needs an installation dimension before style.

    This is the V2 replacement for the old slot-based assertion that a plain
    radiator request must ask for size rather than silently search the entire
    catalogue.
    """

    radiator = _product(
        "RADIATOR-500",
        "Радиатор биметаллический 500 мм",
        "Радиаторы отопления",
        attributes={
            "Тип товара": "Радиатор отопления",
            "Межосевое расстояние, мм": "500",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[radiator])
    understanding = _frame(
        product={
            "text": "радиатор",
            "canonical_type": "radiator",
            "category": "radiators",
            "role": "target",
            "evidence": "радиатор",
        },
    )
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(understanding),
    )

    response = _preview_response(
        bot,
        session_id="v2-bare-radiator",
        turn_id="v2-bare-radiator-1",
        message="Нужен радиатор",
    )

    assert response.products == []
    assert "межосев" in response.answer.lower()
    trace = _assert_v2_owner(settings)
    selection = trace["cutover_v2"]["selection_delivery"]
    assert selection["ordered_skus"] == []
    assert selection["missing_critical_fact"] == "center_distance_mm"


def test_preview_boiler_selection_keeps_scope_for_following_direct_fact(
    tmp_path,
    monkeypatch,
) -> None:
    """A V2 boiler card is a customer-visible scope for ProductFact.

    This is the Selection side of the already accepted direct-fact seam.  It
    verifies that a preliminary area-based boiler list is not discarded before
    an ordinal fact request and that the answer remains V2-owned.
    """

    gas = _product(
        "GAS-24",
        "Котёл газовый 24 кВт одноконтурный",
        "Котельное оборудование",
        attributes={
            "Тип товара": "Котёл",
            "Тип котла": "Газовый",
            "Мощность, кВт": "24",
            "Количество контуров": "Одноконтурный",
            "Отапливаемая площадь, м²": "240",
        },
    )
    settings = _preview_settings(tmp_path)
    bot = ChatOrchestrator(settings=settings, products=[gas])
    opening = _frame(
        product={
            "text": "котёл",
            "canonical_type": "boiler",
            "category": "boilers",
            "role": "target",
            "evidence": "котёл",
        },
        constraints=[_known("area_m2", 150, "150 м²", unit="m2")],
    )
    fuel = _frame(
        constraints=[
            {
                **_known("boiler_type", "gas", "газовый"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
    )
    circuits = _frame(
        constraints=[
            {
                **_known("circuits", 1, "только отопление"),
                "applies_to_product": None,
            }
        ],
        operation="continue",
        answers_pending_question=True,
        show=True,
    )
    direct_fact = _frame(
        operation="continue",
        acts=["explain"],
        references=[
            {
                "kind": "ordinal",
                "text": "первого",
                "target_hint": "1",
                "evidence": "первого",
            }
        ],
    )
    frames = iter((opening, fuel, circuits, direct_fact))
    monkeypatch.setattr(
        bot.semantic_interpreter,
        "interpret",
        lambda _message, _before: _semantic(next(frames)),
    )

    _preview_response(
        bot,
        session_id="v2-boiler-to-fact",
        turn_id="v2-boiler-to-fact-1",
        message="Нужен котёл для дома 150 м²",
    )
    _preview_response(
        bot,
        session_id="v2-boiler-to-fact",
        turn_id="v2-boiler-to-fact-2",
        message="Газовый",
    )
    cards = _preview_response(
        bot,
        session_id="v2-boiler-to-fact",
        turn_id="v2-boiler-to-fact-3",
        message="Только отопление, покажите варианты",
    )
    response = _preview_response(
        bot,
        session_id="v2-boiler-to-fact",
        turn_id="v2-boiler-to-fact-4",
        message="Сколько контуров у первого котла?",
    )

    assert [item.sku for item in cards.products] == ["GAS-24"]
    assert [item.sku for item in response.products] == ["GAS-24"]
    assert "одноконтур" in response.answer.lower()
    traces = [
        json.loads(line)
        for line in settings.diagnostic_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(traces) == 4
    final = traces[-1]["cutover_v2"]
    assert final["decision"]["owner_candidate"] == "v2"
    assert final["product_fact_delivery"]["canonical_sku"] == "GAS-24"
    assert final["product_fact_delivery"]["predicate"] == "circuits"
    assert final["product_fact_delivery"]["customer_visible_scope_preserved"] is True
