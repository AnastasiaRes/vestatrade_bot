"""Regression tests for selection when the customer does not know a parameter.

The cases mirror product families present in the 100-item sample feed, while
the controller logic is catalogue-size independent.  They intentionally use
observations and paraphrases rather than the wording of the bot's question.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.agents.numeric_semantics import extract_spoken_area_m2
from app.agents.slot_filling import SlotFillingAgent
from app.models import IntentResult, Product, SessionState
from app.openrouter_client import LLMResult


class _OfflineLLM:
    last_json_output_accepted = False
    last_fallback_reason = "offline regression"

    def complete_json(
        self,
        _agent: str,
        _messages: list[dict[str, str]],
        fallback: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        return fallback, False

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=None,
            llm_used=False,
            fallback_reason="offline regression",
        )


@pytest.fixture(autouse=True)
def _skip_document_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.orchestrator.load_docs_for_products",
        lambda _products, _directories: 0,
    )


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    brand: str = "TEST",
    attributes: dict[str, str] | None = None,
    description: str | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand=brand,
        url=f"https://example.test/{sku.lower()}",
        price=1000,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized=attributes or {},
        description=description,
    )


def test_unknown_fitting_terms_are_replaced_by_observations_then_resolved() -> None:
    ppr = _product(
        "PPR20",
        "Угольник PPR 90 градусов 20 мм",
        "Фитинги PPR",
        attributes={"диаметр, мм": "20", "тип товара": "Угольник"},
    )
    sewer = _product(
        "DN50",
        "Отвод канализационный DN50 90 градусов",
        "Внутренняя канализация",
        attributes={"диаметр, мм": "50", "тип товара": "Отвод"},
    )
    bot = ChatOrchestrator(products=[ppr, sewer], llm_client=_OfflineLLM())
    session_id = "assisted-fitting"

    bot.handle_chat(session_id, "Нужно соединить трубы, какой фитинг взять?")
    unknown = bot.handle_chat(
        session_id,
        "Я не знаю ни систему, ни размер.",
    )

    assert "техническое название знать не обязательно" in unknown.answer.lower()
    assert "нагревом" in unknown.answer.lower()
    assert set(unknown.debug["slots"]["deferred_slot_keys"]) == {
        "fitting_system",
        "diameter_mm",
        "size_inch",
    }

    resolved = bot.handle_chat(
        session_id,
        "Белые пластиковые трубы соединяются нагревом, нужен уголок, "
        "на трубе написано 20 мм.",
    )

    assert [card.sku for card in resolved.products] == ["PPR20"]
    assert "deferred_slot_keys" not in resolved.debug["slots"]


def test_live_style_fitting_opening_is_selection_not_a_term_definition() -> None:
    ppr = _product(
        "PPR20",
        "Угольник PPR 90 градусов 20 мм",
        "Фитинги PPR",
        attributes={"диаметр, мм": "20", "тип товара": "Угольник"},
    )
    cap = _product(
        "PPR-CAP",
        "Заглушка PPR 20 мм",
        "Фитинги PPR",
        attributes={"диаметр, мм": "20", "тип товара": "Заглушка"},
    )
    tee = _product(
        "PPR-TEE",
        "Тройник PPR 20 мм",
        "Фитинги PPR",
        attributes={"диаметр, мм": "20", "тип товара": "Тройник"},
    )
    bot = ChatOrchestrator(products=[cap, ppr, tee], llm_client=_OfflineLLM())

    opening = bot.handle_chat(
        "live-fitting-wording",
        "Мне надо соединить две пластиковые трубы с поворотом, но я вообще "
        "не знаю, как эта система называется и какой фитинг просить.",
    )

    assert "точное значение этого термина" not in opening.answer.lower()
    assert "нагрев" in opening.answer.lower()
    assert opening.products == []

    resolved = bot.handle_chat(
        "live-fitting-wording",
        "Трубы белые, на них написано 20. Нужно сделать поворот — не прямо, "
        "а под углом. Соединяются нагревом, как по инструкции.",
    )
    assert [card.sku for card in resolved.products] == ["PPR20"]
    assert "система соединения" not in resolved.answer.lower()


def test_fitting_refinement_reapplies_angle_and_excludes_thread_transition() -> None:
    right_angle = _product(
        "PPR-90",
        "Угольник 90 PPR 20 мм",
        "Фитинги PPR",
        attributes={
            "диаметр, мм": "20",
            "тип товара": "Угольник",
            "угол, градусы": "90",
            "материал": "Полипропилен",
        },
    )
    diagonal = _product(
        "PPR-45",
        "Угольник 45 PPR 20 мм",
        "Фитинги PPR",
        attributes={
            "диаметр, мм": "20",
            "тип товара": "Угольник",
            "угол, градусы": "45",
            "материал": "Полипропилен",
        },
    )
    threaded = _product(
        "PPR-THREAD",
        'Угольник PPR с переходом на внутреннюю резьбу 20x1/2"',
        "Фитинги PPR",
        attributes={
            "диаметр, мм": "20",
            "тип товара": "Угольник",
            "угол, градусы": "90",
            "материал": "Полипропилен, Латунь",
        },
    )
    bot = ChatOrchestrator(
        products=[diagonal, threaded, right_angle],
        llm_client=_OfflineLLM(),
    )
    session_id = "fitting-hard-refinement"

    bot.handle_chat(
        session_id,
        "Нужно соединить две белые трубы 20 мм, соединяются нагревом, нужен поворот.",
    )
    refined = bot.handle_chat(
        session_id,
        "Нужен именно 90-градусный поворот, без перехода на резьбу. "
        "Как проверить по маркировке, что он подойдёт?",
    )

    assert [card.sku for card in refined.products] == ["PPR-90"]
    assert refined.debug["slots"]["angle_deg"] == 90
    assert refined.debug["slots"]["combined_metal"] is False
    assert refined.debug["slots"]["fitting_end_form"] == "socket_socket"
    assert "на обеих трубах" in refined.answer.lower()
    assert "цвет сам по себе" in refined.answer.lower()


def test_outside_measurement_does_not_turn_indoor_sewer_into_external_sewer() -> None:
    indoor = _product(
        "HT50",
        "Труба канализационная внутренняя DN50 2000 мм",
        "Внутренняя канализация",
        attributes={
            "диаметр, мм": "50",
            "длина, мм": "2000",
            "тип товара": "Труба",
        },
    )
    outdoor = _product(
        "KG110",
        "Труба канализационная наружная DN110 2000 мм",
        "Наружная канализация",
        attributes={
            "диаметр, мм": "110",
            "длина, мм": "2000",
            "тип товара": "Труба",
        },
    )
    bot = ChatOrchestrator(products=[indoor, outdoor], llm_client=_OfflineLLM())
    session_id = "assisted-sewer"

    bot.handle_chat(session_id, "Нужно заменить кусок канализации под раковиной.")
    unknown = bot.handle_chat(
        session_id,
        "Маркировка не читается, диаметр не знаю; нужен прямой участок.",
    )
    assert "измерить наружный размер" in unknown.answer.lower()

    resolved = bot.handle_chat(
        session_id,
        "Измерил снаружи 50 мм, длина отрезка 2000 мм.",
    )

    assert resolved.debug["slots"]["sewer_scope"] == "внутренняя"
    assert [card.sku for card in resolved.products] == ["HT50"]
    assert "deferred_slot_keys" not in resolved.debug["slots"]


def test_live_style_sewer_reply_reaches_catalogue_instead_of_colour_norm() -> None:
    indoor = _product(
        "HT50",
        "Труба канализационная внутренняя DN50 2000 мм",
        "Внутренняя канализация",
        attributes={
            "диаметр, мм": "50",
            "длина, мм": "2000",
            "тип товара": "Труба",
        },
    )
    bot = ChatOrchestrator(products=[indoor], llm_client=_OfflineLLM())

    opening = bot.handle_chat(
        "live-sewer-wording",
        "Под мойкой надо поменять прямой кусок серой канализации, а надпись "
        "на нём уже не читается. Я этих DN совсем не понимаю.",
    )
    assert opening.products == []
    assert "точное значение этого термина" not in opening.answer.lower()

    resolved = bot.handle_chat(
        "live-sewer-wording",
        "Труба внутри квартиры, серая, нужен прямой участок длиной 2 метра. "
        "Наружный диаметр примерно 50 мм — как измерить точно или что это значит?",
    )
    assert [card.sku for card in resolved.products] == ["HT50"]
    assert resolved.debug["slots"]["sewer_scope"] == "внутренняя"
    assert "наружный диаметр" in resolved.answer.lower()
    assert "не внутреннее отверстие" in resolved.answer.lower()

    colloquial_measurement = bot.handle_chat(
        "live-sewer-wording",
        "Какой из этих вариантов подойдёт, если наружка около 50 мм и труба серая?",
    )
    assert colloquial_measurement.products == resolved.products
    assert colloquial_measurement.debug["slots"]["sewer_scope"] == "внутренняя"
    assert "нет точного совпадения" not in colloquial_measurement.answer.lower()
    assert "по одному серому цвету" in colloquial_measurement.answer.lower()

    faded_marking = bot.handle_chat(
        "live-sewer-wording",
        "Надо ли измерять штангенциркулем или можно просто взять DN50, "
        "если на трубе было написано что-то вроде 50?",
    )
    assert faded_marking.products == resolved.products
    assert "что-то вроде 50" in faded_marking.answer.lower()
    assert "не подтверждает" in faded_marking.answer.lower()

    diameter_explanation = bot.handle_chat(
        "live-sewer-wording",
        "Наружный диаметр 50 мм — это точно то, что нужно, или смотреть по внутреннему?",
    )
    assert diameter_explanation.products == resolved.products
    assert "наружный диаметр ровного участка" in diameter_explanation.answer.lower()
    assert "штангенциркул" in diameter_explanation.answer.lower()

    without_caliper = bot.handle_chat(
        "live-sewer-wording",
        "А как проверить диаметр, если штангенциркуля нет? Есть простой способ "
        "обычной ниткой или рулеткой?",
    )
    assert without_caliper.products == resolved.products
    assert "не по раструбу" in without_caliper.answer.lower()
    assert "разделите на 3,14" in without_caliper.answer.lower()
    assert "157 мм" in without_caliper.answer.lower()

    ruler_only = bot.handle_chat(
        "live-sewer-wording",
        "Штангенциркуль у меня нет, только линейка. Можно ли примерно проверить DN50?",
    )
    assert ruler_only.products == resolved.products
    assert "бумажную полоску" in ruler_only.answer.lower()
    assert "разделите на 3,14" in ruler_only.answer.lower()

    appearance_only = bot.handle_chat(
        "live-sewer-wording",
        "Если штангенциркулем измерить не получается, можно выбрать трубу по "
        "цвету и внешнему виду или точный замер обязателен?",
    )
    assert appearance_only.products == resolved.products
    assert "по цвету" in appearance_only.answer.lower()
    assert "размер не назначают" in appearance_only.answer.lower()

    measurement_plan = bot.handle_chat(
        "live-sewer-wording",
        "Надо измерить штангенциркулем на старой трубе, на ровном участке, "
        "или посмотреть DN в паспорте и на упаковке.",
    )
    assert measurement_plan.products == resolved.products
    assert "это корректный порядок" in measurement_plan.answer.lower()
    assert "не на раструбе" in measurement_plan.answer.lower()

    size_abbreviation = bot.handle_chat(
        "live-sewer-wording",
        "Что значит д.50 в названии — это тоже диаметр 50 мм?",
    )
    assert size_abbreviation.products == resolved.products
    assert "сокращённая запись размера" in size_abbreviation.answer.lower()
    assert "а не артикул" in size_abbreviation.answer.lower()
    assert "не означает, что товар исчез" in size_abbreviation.answer.lower()

    explanation = bot.handle_chat(
        "live-sewer-wording",
        "Почему у вариантов разная цена, если всё равно 50 мм и 2 метра? "
        "И что значит «с раструбом»?",
    )
    assert explanation.products == resolved.products
    assert "расширенный конец" in explanation.answer.lower()
    assert "калькуляции цены" in explanation.answer.lower()
    assert "точное значение этого термина не подскажу" not in explanation.answer.lower()


def test_unexplained_feed_suffix_is_not_invented_or_parsed_as_a_diameter() -> None:
    ostendorf = _product(
        "112060",
        'Труба канализационная, HTEM, 50*2000"10',
        "Внутренняя канализация",
        brand="OSTENDORF",
        attributes={"тип товара": "Труба"},
    )
    sinikon = _product(
        "500053",
        "Труба с раструбом 50 х 2000 мм",
        "Внутренняя канализация",
        brand="СИНИКОН",
        attributes={"тип товара": "Труба"},
    )
    bot = ChatOrchestrator(products=[ostendorf, sinikon], llm_client=_OfflineLLM())
    session_id = "sewer-ambiguous-suffix"

    bot.handle_chat(
        session_id,
        "Под мойкой надо поменять прямой кусок серой канализации, маркировка стёрлась.",
    )
    shown = bot.handle_chat(
        session_id,
        "Труба внутри квартиры, наружный диаметр 50 мм, нужен прямой участок "
        "длиной 2 метра.",
    )
    assert {card.sku for card in shown.products} == {"112060", "500053"}
    explanation = bot.handle_chat(
        session_id,
        "Что значит '10' в обозначении '50*2000\"10'? Это упаковка, толщина "
        "или влияет на совместимость?",
    )

    assert {card.sku for card in explanation.products} == {"112060", "500053"}
    assert "присутствует только в названии" in explanation.answer.lower()
    assert "не буду придумывать" in explanation.answer.lower()
    assert "новый диаметр" in explanation.answer.lower()
    assert explanation.debug["category"] == "sewer"

    repeated = bot.handle_chat(
        session_id,
        "Как всё-таки понять, что означает 10 в конце и можно ли брать эту трубу?",
    )
    assert repeated.products == explanation.products
    assert "10 мм" not in repeated.answer.lower()
    assert "присутствует только в названии" in repeated.answer.lower()


def test_thermostatic_head_goal_survives_unknown_model_and_thread() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC M30x1,5",
        "Арматура для радиаторов",
        brand="VALTEC",
        description=(
            "Присоединительная резьба M30x1,5. Головка может использоваться "
            "совместно с любыми термостатическими клапанами марки VALTEC."
        ),
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())
    session_id = "assisted-head"

    bot.handle_chat(
        session_id,
        "Нужна термоголовка для старого радиаторного клапана.",
    )
    unknown = bot.handle_chat(
        session_id,
        "Модель клапана и резьбу не знаю.",
    )

    assert "резьбу измерять на глаз не нужно" in unknown.answer.lower()
    assert unknown.debug["slots"]["product_kind"] == "thermostatic_head"
    assert set(unknown.debug["slots"]["deferred_slot_keys"]) == {
        "metric_thread",
        "valve_model",
        "valve_brand",
    }

    observed = bot.handle_chat(
        session_id,
        "На корпусе клапана читается только VALTEC.",
    )

    assert [card.sku for card in observed.products] == ["VT.1500.0.0"]
    assert observed.debug["slots"]["valve_brand"] == "VALTEC"
    assert "резьба термоголовки" in observed.answer.lower()
    assert "не подтвержден" in observed.answer.lower()


def test_uncertain_metric_alternatives_clear_interpreter_shaped_filters() -> None:
    intent = IntentResult(
        intent_type="attribute_request",
        category="radiator_fittings",
        confidence=1.0,
        slots={
            "thermostatic_head": True,
            "product_kind": "thermostatic_head",
            "valve_brand": "VALTEC",
            "metric_thread": "M20",
            "connection_size": "M20",
            "connection_form": "threaded",
            "name_tokens": ["M20", "M30", "standard"],
        },
    )
    session = SessionState(
        session_id="uncertain-thread",
        category="radiator_fittings",
        slots={"metric_thread": "M20", "valve_brand": "VALTEC"},
    )
    result = SlotFillingAgent().fill(
        "На клапане VALTEC, но резьбу не знаю: M20 или M30.",
        intent,
        session,
    )

    assert result.slots["product_kind"] == "thermostatic_head"
    assert result.slots["valve_brand"] == "VALTEC"
    assert result.slots["deferred_slot_keys"] == ["metric_thread"]
    for key in (
        "metric_thread",
        "connection_size",
        "connection_form",
        "name_tokens",
    ):
        assert key not in result.slots

    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())
    bot.engineering_requirements.remember(
        "radiator_fittings",
        {"metric_thread": "M20", "valve_brand": "VALTEC"},
        session,
    )
    bot._merge_persistent_slots(
        session,
        result.slots,
        explicit_slots=intent.slots,
    )
    bot.engineering_requirements.remember(
        "radiator_fittings",
        result.slots,
        session,
    )
    assert "metric_thread" not in session.slots
    assert "metric_thread" not in session.project_context["categories"]["radiator_fittings"]
    query = bot._build_query(
        "На клапане VALTEC, но резьбу не знаю: M20 или M30.",
        intent,
        session,
    )
    for key in ("metric_thread", "connection_size", "connection_form", "name_tokens"):
        assert key not in query.slots


def test_missing_head_opening_is_not_mistaken_for_complectation() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC M30x1,5",
        "Арматура для радиаторов",
        brand="VALTEC",
        description=(
            "Присоединительная резьба M30x1,5. Головка может использоваться "
            "совместно с любыми термостатическими клапанами марки VALTEC."
        ),
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())

    opening = bot.handle_chat(
        "live-head-wording",
        "На батарее есть клапан, а головки на нём нет. Хочу поставить "
        "регулировку, но ни модель клапана, ни резьбу не знаю.",
    )
    assert opening.debug["category"] == "radiator_fittings"
    assert opening.debug["slots"]["product_kind"] == "thermostatic_head"
    assert "по какому котлу" not in opening.answer.lower()
    assert "марку" in opening.answer.lower()
    assert "загрузка фотографий" in opening.answer.lower()
    assert "не поддерживается" in opening.answer.lower()
    assert "пришлите фото" not in opening.answer.lower()

    resolved = bot.handle_chat(
        "live-head-wording",
        "На корпусе только VALTEC, ничего больше нет. Посадочное место — стандартная "
        "резьба на радиаторе, вроде обычного клапана. Ничего точнее измерить или "
        "описать не могу.",
    )
    assert [card.sku for card in resolved.products] == ["VT.1500.0.0"]
    assert "не подтвержден" in resolved.answer.lower()

    inspection = bot.handle_chat(
        "live-head-wording",
        "А можно по фото определить резьбу, не снимая клапан?",
    )
    assert inspection.products == resolved.products
    assert "загрузка фотографий" in inspection.answer.lower()
    assert "не поддерживается" in inspection.answer.lower()
    assert "не отворачивайте корпус" in inspection.answer.lower()
    assert "под давлением" in inspection.answer.lower()
    assert "перепишите" in inspection.answer.lower()

    compatibility = bot.handle_chat(
        "live-head-wording",
        "Где в карточке указано, что головка совместима с моим клапаном?",
    )
    assert compatibility.products == resolved.products
    assert "сторону самой головки" in compatibility.answer.lower()
    assert "прямое заявление о совместимости" in compatibility.answer.lower()
    assert "vt.1500.0.0" in compatibility.answer.lower()

    rationale = bot.handle_chat(
        "live-head-wording",
        "Почему эти варианты считаются подходящими, если я знаю только VALTEC "
        "и условно стандартную резьбу?",
    )
    assert rationale.products == resolved.products
    assert "прямое заявление о совместимости" in rationale.answer.lower()
    assert "только у vt.1500.0.0" in rationale.answer.lower()
    assert "обещать нельзя" in rationale.answer.lower()

    measurement = bot.handle_chat(
        "live-head-wording",
        "Как понять, там M20 или M30, и где это вообще смотреть без разборки?",
    )
    assert measurement.products == resolved.products
    assert "штангенциркул" in measurement.answer.lower()
    assert "около 30 мм" in measurement.answer.lower()
    assert "около 20 мм" in measurement.answer.lower()
    assert "шаг" in measurement.answer.lower()
    assert "не разгерметизируйте" in measurement.answer.lower()

    repeated_measurement = bot.handle_chat(
        "live-head-wording",
        "Я всё ещё не понял: где смотреть M20 или M30 и что именно измерять?",
    )
    assert repeated_measurement.products == resolved.products
    assert "новых данных после повторной проверки" not in repeated_measurement.answer.lower()
    assert "диаметр по вершинам" in repeated_measurement.answer.lower()


def test_brand_and_uncertain_thread_show_cards_and_inspection_in_one_turn() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC M30x1,5",
        "Арматура для радиаторов",
        brand="VALTEC",
        description=(
            "Присоединительная резьба M30x1,5. Головка может использоваться "
            "совместно с любыми термостатическими клапанами марки VALTEC."
        ),
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())
    session_id = "head-brand-and-howto"
    bot.handle_chat(
        session_id,
        "На батарее есть клапан, а головки на нём нет. Хочу поставить регулировку, "
        "но модель клапана и резьбу не знаю.",
    )
    response = bot.handle_chat(
        session_id,
        "На корпусе только VALTEC, резьба похожа на M30, но я не уверен. "
        "Помогите подобрать головку и понять, как проверить совместимость.",
    )

    assert [card.sku for card in response.products] == ["VT.1500.0.0"]
    assert "около 30 мм" in response.answer.lower()
    assert "одна эта запись ещё не доказывает" in response.answer.lower()


def test_radiator_unknown_pressure_yields_only_declared_area_adequate_cards() -> None:
    small = _product(
        "SMALL",
        "Радиатор биметаллический 4 секции",
        "Радиаторы отопления",
        attributes={
            "тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "7.7",
        },
    )
    adequate = _product(
        "ADEQUATE",
        "Радиатор биметаллический 10 секций",
        "Радиаторы отопления",
        attributes={
            "тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "19.6",
        },
    )
    bot = ChatOrchestrator(products=[small, adequate], llm_client=_OfflineLLM())
    session_id = "assisted-radiator"

    bot.handle_chat(session_id, "Подбери радиатор в комнату.")
    bot.handle_chat(session_id, "Комната 16 м2, отопление центральное.")
    response = bot.handle_chat(
        session_id,
        "Давления не знаю, подбери из того, что уже известно.",
    )

    assert [card.sku for card in response.products] == ["ADEQUATE"]
    assert "рабочее давление" in response.answer.lower()
    assert "не подтверждён" in response.answer.lower()
    assert "обещание совместимости" in response.answer.lower()


def test_spoken_area_and_first_turn_unknowns_start_preliminary_radiator_flow() -> None:
    assert extract_spoken_area_m2("комната шестнадцать квадратов") == 16.0
    adequate = _product(
        "ADEQUATE",
        "Радиатор биметаллический 10 секций",
        "Радиаторы отопления",
        attributes={
            "тип товара": "Радиатор отопления",
            "Площадь обогрева, м2": "19.6",
        },
    )
    bot = ChatOrchestrator(products=[adequate], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "live-radiator-wording",
        "Нужен радиатор в комнату шестнадцать квадратов, квартира с обычным "
        "центральным отоплением. Остальных паспортных данных у меня сейчас нет.",
    )

    assert response.debug["slots"]["area_m2"] == 16.0
    assert [card.sku for card in response.products] == ["ADEQUATE"]
    assert "рабочее давление" in response.answer.lower()
    assert "не подтвержд" in response.answer.lower()

    center_distance = bot.handle_chat(
        "live-radiator-wording",
        "Что значит межосевое расстояние и как его выбрать, если старый "
        "радиатор определить не получается?",
    )
    assert center_distance.products == response.products
    assert "между центрами" in center_distance.answer.lower()
    assert "не выбирают по площади" in center_distance.answer.lower()

    sizing = bot.handle_chat(
        "live-radiator-wording",
        "А как понять, какой из них подойдёт по тепловой мощности? У меня "
        "16 квадратов, но не знаю, сколько ватт нужно.",
    )
    assert sizing.products == response.products
    assert sizing.debug["category"] == "radiators"
    assert "не точный расчёт теплопотерь" in sizing.answer.lower()
    assert "газовый или электрический" not in sizing.answer.lower()

    area_followup = bot.handle_chat(
        "live-radiator-wording",
        "Площадь обогрева больше 16 — это нормально или выбирать ближайшее число?",
    )
    assert area_followup.products == response.products
    assert "выбирать просто ближайшее" in area_followup.answer.lower()

    repeated_calculation = bot.handle_chat(
        "live-radiator-wording",
        "А как посчитать, сколько ватт реально нужно? Нужно учитывать высоту?",
    )
    assert repeated_calculation.products == response.products
    assert repeated_calculation.answer != sizing.answer
    assert "по одной площади 16 м²" in repeated_calculation.answer.lower()

    unknown_both = bot.handle_chat(
        "live-radiator-wording",
        "Как понять, какой радиатор реально подойдёт, если я не знаю теплопотери "
        "и давление в системе?",
    )
    assert unknown_both.products == response.products
    assert "показанные позиции остаются предварительными" in unknown_both.answer.lower()
    assert "давление центральной системы" in unknown_both.answer.lower()

    no_measurements = bot.handle_chat(
        "live-radiator-wording",
        "А как вообще узнать, сколько тепла нужно для моей комнаты — "
        "без паспорта или замеров?",
    )
    assert no_measurements.products == response.products
    assert "по одной площади 16 м²" in no_measurements.answer.lower()
    assert "точную теплоотдачу" in no_measurements.answer.lower()

    premature_choice = bot.handle_chat(
        "live-radiator-wording",
        "Какой из этих радиаторов реально подойдёт для комнаты 16 м² "
        "без замеров и паспорта?",
    )
    assert premature_choice.products == response.products
    assert "ни один из показанных радиаторов" in premature_choice.answer.lower()
    assert "выбирать один товар наугад" in premature_choice.answer.lower()

    simplified_choice = bot.handle_chat(
        "live-radiator-wording",
        "Просто посоветуйте, какой из этих вариантов подойдёт для комнаты 16 м² "
        "без лишних технических деталей. Важно, чтобы было тепло и не ломалось.",
    )
    assert simplified_choice.products == response.products
    assert (
        "ни один из показанных радиаторов" in simplified_choice.answer.lower()
        or "самый безопасный" in simplified_choice.answer.lower()
    )
    assert (
        "окончательно подходящим" in simplified_choice.answer.lower()
        or "безопасность по площади" in simplified_choice.answer.lower()
    )
    assert "идеальн" not in simplified_choice.answer.lower()

    safety_choice = bot.handle_chat(
        "live-radiator-wording",
        "Какой из этих радиаторов самый безопасный для 16 м², если я пока "
        "не могу проверить давление и теплопотери?",
    )
    assert safety_choice.products == response.products
    assert "самый безопасный" in safety_choice.answer.lower()
    assert (
        "по полноте подтверждённых полей" in safety_choice.answer.lower()
        or "нет достаточного набора" in safety_choice.answer.lower()
    )
    assert "подтверждённый выбор" in safety_choice.answer.lower() or (
        "безопасность по площади" in safety_choice.answer.lower()
    )

    session = bot.sessions.get("live-radiator-wording")
    boundary_message = (
        "Просто посоветуйте, какой из этих вариантов подойдёт для комнаты 16 м²."
    )
    bot._request_agents.message = boundary_message
    bot._request_agents.turn_frame = None
    bot._request_agents.turn_plan = None
    guarded = bot._response(
        "live-radiator-wording",
        "Оба радиатора идеально подходят и точно надёжны для центрального отопления.",
        list(session.last_products),
        False,
        IntentResult(
            intent_type="attribute_request",
            category="radiators",
            confidence=1.0,
            slots=dict(session.slots),
        ),
        session,
        ["ResponseComposerAgent"],
    )
    assert "идеально подходят" not in guarded.answer.lower()
    assert "ни один из показанных радиаторов" in guarded.answer.lower()
    assert guarded.debug["final_answer_source"] == "deterministic"

    oversized_by_area = bot.handle_chat(
        "live-radiator-wording",
        "А если просто взять радиатор с тепловой мощностью чуть больше 16 м² — "
        "скажем, на 20–22 квадрата — он точно сработает?",
    )
    assert oversized_by_area.products == response.products
    assert "не означает ни гарантированный перегрев" in oversized_by_area.answer.lower()
    assert "ни гарантированную достаточность" in oversized_by_area.answer.lower()

    closest_known = bot.handle_chat(
        "live-radiator-wording",
        "Можно хотя бы посоветовать, какой из этих радиаторов будет ближе к 16 м² "
        "без перегрева, если данных УК пока нет?",
    )
    assert closest_known.products == response.products
    assert "если сравнивать только по заявленной площади" in closest_known.answer.lower()
    assert "условный лидер только по одному" in closest_known.answer.lower()
    assert "не обещание" in closest_known.answer.lower()

    heat_ranking = bot.handle_chat(
        "live-radiator-wording",
        "Какой из этих радиаторов нагреет 16 м² лучше всего, если пока "
        "сравнить только по теплу, без давления?",
    )
    assert heat_ranking.products == response.products
    assert "если сравнить только заявленную теплоотдачу" in heat_ranking.answer.lower() or (
        "нет числовой теплоотдачи" in heat_ranking.answer.lower()
    )
    assert "рабочее давление показывает" not in heat_ranking.answer.lower()

    area_vs_watts = bot.handle_chat(
        "live-radiator-wording",
        "Что значит площадь обогрева в карточке — это не то же самое, "
        "что тепловая мощность в ваттах?",
    )
    assert area_vs_watts.products == response.products
    assert "не одно и то же поле" in area_vs_watts.answer.lower()
    assert "фид не сообщает формулу" in area_vs_watts.answer.lower()

    popularity = bot.handle_chat(
        "live-radiator-wording",
        "Какой из этих радиаторов чаще устанавливают в подобных квартирах — "
        "по отзывам или популярности?",
    )
    assert popularity.products == response.products
    assert "нет проверенных отзывов" in popularity.answer.lower()
    assert "остаток на складе" in popularity.answer.lower()
    assert "не являются показателем популярности" in popularity.answer.lower()

    real_life = bot.handle_chat(
        "live-radiator-wording",
        "Как у других пользователей выглядит этот радиатор в реальной эксплуатации? "
        "Есть опыт с такими моделями?",
    )
    assert real_life.products == response.products
    assert real_life.answer != popularity.answer
    assert "в фиде нет отзывов" in real_life.answer.lower()
    assert "пользовательских фото" in real_life.answer.lower()

    trial_installation = bot.handle_chat(
        "live-radiator-wording",
        "Можно поставить самый мощный, потом проверить перегрев и при необходимости "
        "заменить на другой?",
    )
    assert trial_installation.products == response.products
    assert "не советую монтировать выбранный радиатор как пробу" in trial_installation.answer.lower()
    assert "до покупки и монтажа" in trial_installation.answer.lower()

    named_trial = bot.handle_chat(
        "live-radiator-wording",
        "А если просто поставить ADEQUATE и потом посмотреть, как будет "
        "нагреваться комната — может, хватит?",
    )
    assert [card.sku for card in named_trial.products] == ["ADEQUATE"]
    assert "не советую монтировать выбранный радиатор как пробу" in named_trial.answer.lower()
    assert "теплопотери заранее" in named_trial.answer.lower()
    assert "положительный остаток" not in named_trial.answer.lower()

    installation = bot.handle_chat(
        "live-radiator-wording",
        "Какой у этого радиатора размер, куда он крепится и как подключается, "
        "чтобы всё вписалось в стену?",
    )
    assert installation.products == response.products
    assert "размеры: в фиде не указаны" in installation.answer.lower()
    assert "подключение: в фиде не указано" in installation.answer.lower()
    assert "крепление/кронштейны: в фиде не указаны" in installation.answer.lower()
    assert "не буду автоматически выдавать за полные габариты" in installation.answer.lower()

    declared_area = bot.handle_chat(
        "live-radiator-wording",
        "Если взять радиатор с заявленной площадью 19,6 м², он точно не перегреет "
        "комнату или его всё равно может не хватить?",
    )
    assert declared_area.products == response.products
    assert "не означает ни гарантированный перегрев" in declared_area.answer.lower()
    assert "нельзя переносить в параметры комнаты" in declared_area.answer.lower()


def test_panel_type_22_is_explained_without_claiming_system_compatibility() -> None:
    panel = _product(
        "PANEL22",
        "Радиатор стальной панельный тип 22 500x1000",
        "Радиаторы отопления",
        attributes={
            "тип": "22",
            "теплоотдача, Вт": "2100",
            "межосевое расстояние, мм": "449",
            "площадь обогрева, м2": "21",
        },
    )
    bot = ChatOrchestrator(products=[panel], llm_client=_OfflineLLM())
    session_id = "radiator-panel-type"
    shown = bot.handle_chat(
        session_id,
        "Нужен радиатор в комнату 16 м2 с обычным центральным отоплением. "
        "Остальных паспортных данных сейчас нет, подбери пока из известного.",
    )
    assert [card.sku for card in shown.products] == ["PANEL22"]

    explanation = bot.handle_chat(
        session_id,
        "А что значит тип 22: это маркировка или размер, и гарантирует ли он тепло?",
    )

    assert explanation.products == shown.products
    assert "две водяные панели" in explanation.answer.lower()
    assert "а не размер" in explanation.answer.lower()
    assert "не доказывает" in explanation.answer.lower()


def test_radiator_working_pressure_is_explained_as_a_hard_constraint() -> None:
    panel = _product(
        "PRESSURE22",
        "Радиатор стальной панельный тип 22 500x1000",
        "Радиаторы отопления",
        description=(
            "Высокая теплопроводность и минимальный расход теплоносителя "
            "обеспечивают низкую тепловую инерционность."
        ),
        attributes={
            "тип": "22",
            "теплоотдача, Вт": "2100",
            "площадь обогрева, м2": "21",
            "рабочее давление, МПа": "2.0265",
        },
    )
    bot = ChatOrchestrator(products=[panel], llm_client=_OfflineLLM())
    session_id = "radiator-pressure-meaning"
    bot.handle_chat(session_id, "Подбери радиатор в комнату.")
    bot.handle_chat(session_id, "Комната 16 м2, отопление центральное.")
    shown = bot.handle_chat(
        session_id,
        "Давление пока неизвестно, подбери предварительно из того, что известно.",
    )
    assert [card.sku for card in shown.products] == ["PRESSURE22"]

    explanation = bot.handle_chat(
        session_id,
        "Что значит рабочее давление 2.0265 МПа? Нужно ли его знать или можно "
        "смотреть только на теплоотдачу?",
    )

    assert explanation.products == shown.products
    assert "2.03 мпа" in explanation.answer.lower()
    assert "20.3 бар" in explanation.answer.lower()
    assert "выбирать только по теплоотдаче нельзя" in explanation.answer.lower()
    assert "управляющей организации" in explanation.answer.lower()
    assert "не буду его придумывать" in explanation.answer.lower()

    no_uk_answer = bot.handle_chat(
        session_id,
        "Как проверить давление, если управляющая компания не отвечает? Можно "
        "узнать без звонков или бытовым манометром?",
    )
    assert no_uk_answer.products == shown.products
    assert "показание случайного бытового манометра" in no_uk_answer.answer.lower()
    assert "письменно у ук/тсж" in no_uk_answer.answer.lower()
    assert "снимать пробки" in no_uk_answer.answer.lower()
    assert "подбор остаётся предварительным" in no_uk_answer.answer.lower()

    bare_unit_answer = bot.handle_chat(
        session_id,
        "А как узнать, сколько бар у нас в доме? Управляющая компания не отвечает.",
    )
    assert bare_unit_answer.products == shown.products
    assert "показание случайного бытового манометра" in bare_unit_answer.answer.lower()

    flexible_wording = bot.handle_chat(
        session_id,
        "Где и как правильно проверить давление в системе, чтобы не сломать радиатор?",
    )
    assert flexible_wording.products == shown.products
    assert "самостоятельно безопасно определить" in flexible_wording.answer.lower()

    pressure_types = bot.handle_chat(
        session_id,
        "Где лучше спросить у УК давление и в чём разница между рабочим "
        "и опрессовочным?",
    )
    assert pressure_types.products == shown.products
    assert "кратковременного испытания" in pressure_types.answer.lower()
    assert "одно поле другим не заменяют" in pressure_types.answer.lower()

    marketing_terms = bot.handle_chat(
        session_id,
        "Что значит высокая теплопроводность и минимальный расход теплоносителя? "
        "Это важно для центрального отопления?",
    )
    assert marketing_terms.products == shown.products
    assert "качественное описание" in marketing_terms.answer.lower()
    assert "не отдельные измеренные значения" in marketing_terms.answer.lower()
    assert "не буду придумывать" in marketing_terms.answer.lower()

    request_template = bot.handle_chat(
        session_id,
        "Как составить запрос в УК или ТСЖ? Есть стандартная формулировка и "
        "можно ли отправить её через форму на сайте?",
    )
    assert request_template.products == shown.products
    assert "нормальное и максимальное рабочее давление" in request_template.answer.lower()
    assert "давление опрессовки" in request_template.answer.lower()
    assert "выдумывать сайт я не буду" in request_template.answer.lower()

    low_pressure = bot.handle_chat(
        session_id,
        "Если давление в доме ниже 2 бар, какой из показанных вариантов подойдёт?",
    )
    assert low_pressure.products == shown.products
    assert "2 бар — это 0.2 мпа" in low_pressure.answer.lower()
    assert "текущее давление само по себе не мешает" in low_pressure.answer.lower()
    assert "одного текущего показания недостаточно" in low_pressure.answer.lower()

    buy_before_check = bot.handle_chat(
        session_id,
        "Можно просто взять показанный радиатор, а давление сверить потом — "
        "не будет ли проблем?",
    )
    assert buy_before_check.products == shown.products
    assert "не советую покупать или монтировать" in buy_before_check.answer.lower()
    assert "предварительных кандидатов" in buy_before_check.answer.lower()


def test_radiator_pressure_uses_later_numeric_spec_after_qualitative_mention() -> None:
    radiator = _product(
        "PRESSURE-IN-DESCRIPTION",
        "Радиатор биметаллический 10 секций",
        "Радиаторы отопления",
        description=(
            "Благодаря высокому рабочему давлению радиатор подходит для разных систем. "
            "Рабочее давление: 30 атм; опрессовочное давление: 45 атм."
        ),
    )

    assert ChatOrchestrator._radiator_working_pressure_mpa(radiator) == pytest.approx(
        3.03975
    )
    assert ChatOrchestrator._radiator_test_pressure_mpa(radiator) == pytest.approx(
        4.559625
    )


def test_photo_invitation_is_replaced_at_the_response_boundary() -> None:
    answer = ChatOrchestrator._sanitize_customer_answer(
        "Пришлите мне фотографию маркировки, и я попробую определить модель."
    )

    assert "пришлите" not in answer.lower()
    assert "загрузка фотографий" in answer.lower()
    assert "не поддерживается" in answer.lower()
    assert "перепишите маркировку" in answer.lower()


def test_old_chimney_is_boiler_context_not_a_chimney_product_switch() -> None:
    boiler = _product(
        "G24",
        "Котёл газовый двухконтурный 24 кВт",
        "Котлы газовые",
        attributes={
            "тип товара": "Котёл газовый",
            "мощность, кВт": "24",
            "количество контуров": "2",
        },
    )
    chimney = _product(
        "CHIMNEY",
        "Дымоход коаксиальный 60/100",
        "Дымоходы",
        attributes={"тип товара": "Дымоход"},
    )
    bot = ChatOrchestrator(products=[boiler, chimney], llm_client=_OfflineLLM())
    session_id = "assisted-boiler"

    bot.handle_chat(
        session_id,
        "Подбери котёл в дом, мощности старого не знаю.",
    )
    response = bot.handle_chat(
        session_id,
        "Газ есть, дом 120 м2, нужна горячая вода, старый кирпичный дымоход, "
        "остальное не знаю.",
    )

    assert [card.sku for card in response.products] == ["G24"]
    assert "предварительный ориентир" in response.answer.lower()
    assert "теплопотер" in response.answer.lower()
    assert "старого дымохода" in response.answer.lower()
    assert "не подтверждает" in response.answer.lower()


def test_live_style_chimney_question_preserves_boiler_funnel_and_is_answered() -> None:
    boiler = _product(
        "G24",
        "Котёл газовый двухконтурный 24 кВт",
        "Котлы газовые",
        attributes={
            "тип товара": "Котёл газовый",
            "мощность, кВт": "24",
            "количество контуров": "2",
            "камера сгорания": "Закрытая",
        },
    )
    chimney = _product(
        "CHIMNEY",
        "Дымоход коаксиальный 60/100",
        "Дымоходы",
        attributes={"тип товара": "Дымоход"},
    )
    bot = ChatOrchestrator(products=[boiler, chimney], llm_client=_OfflineLLM())
    session_id = "live-boiler-wording"

    bot.handle_chat(
        session_id,
        "Старый котёл пора менять, а его мощность уже не прочитать. Дом "
        "частный, хочется сразу понять, что вообще можно рассматривать.",
    )
    middle = bot.handle_chat(
        session_id,
        "Газовый, на 120 м². Дом старый, дымоход кирпичный — не факт, что "
        "подойдёт к новому котлу. Нужно ли учитывать это при выборе?",
    )
    assert middle.products == []
    assert middle.debug["category"] == "boilers"
    assert middle.debug["slots"]["area_m2"] == 120.0
    assert "горяч" in middle.answer.lower()
    assert "паспорт" in middle.answer.lower()

    selected = bot.handle_chat(
        session_id,
        "Нужна и горячая вода, расчёта теплопотерь нет; подбери пока "
        "предварительно по известным данным.",
    )
    assert [card.sku for card in selected.products] == ["G24"]

    compatibility = bot.handle_chat(
        session_id,
        "А если дымоход кирпичный — можно ли его использовать с новым котлом "
        "или надо обязательно менять на коаксиальный?",
    )
    assert compatibility.products == [selected.products[0]]
    assert "по одному признаку" in compatibility.answer.lower()
    assert "паспорт" in compatibility.answer.lower()
    assert "нельзя решить" in compatibility.answer.lower()

    sizing = bot.handle_chat(
        session_id,
        "120 м² — это минимальная площадь котла или лучше взять с запасом?",
    )
    assert sizing.products == selected.products
    assert "не «минимальная площадь котла»" in sizing.answer.lower()
    assert "слишком мощный" in sizing.answer.lower()

    passport = bot.handle_chat(
        session_id,
        "Можно скинуть паспорт конкретной модели, чтобы проверить дымоход?",
    )
    assert passport.products == selected.products
    assert "проверенная ссылка из фида" in passport.answer.lower()
    assert "нет проверенной прямой ссылки" in passport.answer.lower()
    assert "расчёт теплопотерь" in passport.answer.lower()


def test_boiler_dhw_connection_wording_does_not_trigger_electrical_safety() -> None:
    boiler = _product(
        "G24",
        "Котёл газовый двухконтурный 24 кВт",
        "Котлы газовые",
        attributes={
            "тип товара": "Котёл газовый",
            "мощность, кВт": "24",
            "количество контуров": "2",
            "камера сгорания": "Закрытая",
        },
    )
    bot = ChatOrchestrator(products=[boiler], llm_client=_OfflineLLM())
    session_id = "boiler-hydraulic-connection"

    bot.handle_chat(
        session_id,
        "Старый котёл пора менять, мощность уже не прочитать. Дом частный.",
    )
    bot.handle_chat(
        session_id,
        "Газовый, дом 120 м², дымоход старый кирпичный. Нужно учитывать его при выборе?",
    )
    response = bot.handle_chat(
        session_id,
        "Нужен и для горячей воды. Тогда котёл должен быть с бойлером или "
        "подключаться к нему? И как понять, подойдёт ли старый дымоход — можно "
        "ли проверить самому или только специалисту?",
    )

    assert "электрический котёл" not in response.answer.lower()
    assert "обычной розетке" not in response.answer.lower()
    assert response.debug["category"] == "boilers"
    assert [card.sku for card in response.products] == ["G24"]
    assert "двухконтурный котёл готовит гвс" in response.answer.lower()
    assert "одноконтурный котёл сам воду для кранов не готовит" in response.answer.lower()
    assert "не требует отдельного накопительного бойлера" in response.answer.lower()


def test_explicit_chimney_purchase_request_remains_outside_catalogue_scope() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "explicit-chimney",
        "Подберите новый коаксиальный дымоход для котла.",
    )

    assert response.products == []
    assert "дымоход" in response.answer.lower()
    assert "в каталоге не нашёл" in response.answer.lower()
