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
from app.models import DialogueQAMode, Product


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    attributes: dict[str, str],
    price: float = 1_000,
    stock_status: str = "в наличии",
    stock_qty: int = 5,
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
    )


def _known(
    name: str,
    value: str | int | float,
    evidence: str,
    *,
    unit: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "status": "known",
        "polarity": "required",
        "applies_to_product": 0,
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
