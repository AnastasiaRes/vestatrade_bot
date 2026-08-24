"""Regressions extracted from the targeted full-catalogue live dialogue run."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.feed_loader import FeedLoader
from app.models import Product
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
def _skip_unrelated_document_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.orchestrator.load_docs_for_products",
        lambda _products, _directories: 0,
    )


def _product(
    sku: str,
    name: str,
    category: str,
    *,
    price: float = 100,
    qty: int = 5,
    attributes: dict[str, str] | None = None,
    description: str | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        category_path=category,
        brand="TEST",
        url=f"https://example.test/{sku.replace('/', '-')}",
        price=price,
        stock_status="в наличии" if qty > 0 else "нет в наличии",
        stock_qty=qty,
        attributes_normalized=attributes or {},
        description=description,
    )


def test_spoken_and_spaced_catalogue_skus_resolve_only_to_feed_identities() -> None:
    products = [
        _product("VT.1500.0.0", "Термоголовка VALTEC", "Арматура для радиаторов"),
        _product("VT.217.N.04", "Кран вн.-вн.", "Краны шаровые"),
        _product("VT.218.N.04", "Кран вн.-нар.", "Краны шаровые"),
        _product("VRS.256.13.0", "Насос VALTEC 25/6-130", "Насосы циркуляционные"),
        _product("VRS.256.18.0", "Насос VALTEC 25/6-180", "Насосы циркуляционные"),
        _product("112060", "Труба HT DN50", "Внутренняя канализация"),
    ]
    search = FeedSearchAgent(products)

    assert [p.sku for p in search.resolve_sku_mentions("вт 1500 0 0")] == [
        "VT.1500.0.0"
    ]
    assert [
        p.sku
        for p in search.resolve_sku_mentions(
            "Сравни VRS 256 13 0 и VRS 256 18 0"
        )
    ] == ["VRS.256.13.0", "VRS.256.18.0"]
    assert [p.sku for p in search.resolve_sku_mentions("артикул 112060")] == [
        "112060"
    ]
    assert [
        p.sku
        for p in search.resolve_sku_mentions(
            "Сравни VT 217 N 04 и 218 N 04"
        )
    ] == ["VT.217.N.04", "VT.218.N.04"]
    assert [
        p.sku
        for p in search.resolve_sku_mentions(
            "Чем отличаются VT 217 N 04 и 218 N 04?"
        )
    ] == ["VT.217.N.04", "VT.218.N.04"]
    assert search.resolve_sku_mentions("нужно 112060 рублей") == []


def test_exact_thermostatic_head_answers_stock_thread_and_valtec_compatibility() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка диап. регул-ки 6,5 - 28°C жидкостная",
        "Арматура для радиаторов",
        price=1044,
        qty=28,
        description=(
            "Жидкостная термоголовка регулирует от 6,5 до 28 °С. "
            "Присоединительная резьба — М30х1,5. "
            "Головка может использоваться совместно с клапанами VALTEC."
        ),
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "thermo-full-feed",
        "На коробке вт 1500 0 0. Есть такая и к клапану Valtec подойдёт?",
    )

    assert [product.sku for product in response.products] == ["VT.1500.0.0"]
    answer = response.answer.lower()
    assert "28 шт" in answer
    assert "м30х1,5" in answer
    assert "клапанами valtec" in answer


def test_pipe_description_supplies_price_unit_stick_length_and_arithmetic() -> None:
    pipe = _product(
        "VTp.700.FB20.20",
        "Труба PP-FIBER PN20 20 мм",
        "Трубы полипропиленовые",
        price=114,
        qty=1436,
        description=(
            "Труба поставляется отрезками длиной 4 м. "
            "В карточке приведена цена одного погонного метра."
        ),
    )
    bot = ChatOrchestrator(products=[pipe], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "pipe-packaging",
        "Артикул VTp 700 FB20 20: цена за метр или палку? Сколько будут стоить три палки?",
    )

    answer = response.answer.lower()
    assert "114" in answer and "погон" in answer
    assert "4 м" in answer

    repeated = bot.handle_chat(
        "pipe-packaging-followup",
        "Я всё ещё про VTp.700.FB20.20: два метра отдельно от палки купить можно?",
    )
    repeated_answer = repeated.answer.lower()
    assert "возможность резки" in repeated_answer
    assert "не указана" in repeated_answer
    assert "12 м" in answer and "1368" in answer


def test_pipe_packaging_followup_uses_shown_card_without_repeating_sku() -> None:
    pipe = _product(
        "VTp.700.FB20.20",
        "Труба PP-FIBER PN20 20 мм",
        "Трубы полипропиленовые",
        price=114,
        qty=1436,
        description=(
            "Труба поставляется отрезками длиной 4 м. "
            "В карточке приведена цена одного погонного метра."
        ),
    )
    bot = ChatOrchestrator(products=[pipe], llm_client=_OfflineLLM())
    bot.handle_chat(
        "pipe-packaging-followup",
        "Что по трубе VTp 700 FB20 20: цена за метр или за отрезок?",
    )

    response = bot.handle_chat(
        "pipe-packaging-followup",
        "Мне надо ровно 10 метров. Это сколько полных отрезков и какая цена?",
    )

    assert [product.sku for product in response.products] == ["VTp.700.FB20.20"]
    answer = response.answer.lower()
    assert "114" in answer and "погон" in answer
    assert "4 м" in answer

    cutting = bot.handle_chat(
        "pipe-packaging-followup",
        "Возьму 8 метров и попрошу отрезать ещё 2 метра. Так можно и сколько будет стоить?",
    )
    cutting_answer = cutting.answer.lower()
    assert "прямого «да» на резку нет" in cutting_answer
    assert "1368" in cutting_answer

    repeated_cutting = bot.handle_chat(
        "pipe-packaging-followup",
        "Можно всё-таки отдельно 2 метра от третьего отрезка?",
    )
    assert repeated_cutting.answer != cutting.answer
    assert "ни ответ «да»" in repeated_cutting.answer.lower()


def test_two_numeric_sewer_skus_keep_relation_compatibility_and_total() -> None:
    pipe = _product(
        "112060",
        "Труба канализационная HTEM DN50 2000 мм",
        "Внутренняя канализация HT",
        price=376,
        attributes={"диаметр, мм": "50"},
    )
    bend = _product(
        "112140",
        "Отвод канализационный HTB DN50 45°",
        "Внутренняя канализация HT",
        price=53,
        attributes={"диаметр, мм": "50"},
    )
    bot = ChatOrchestrator(products=[pipe, bend], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "sewer-relation",
        "Артикулы 112060 и 112140 совместимы? Сколько стоят вместе?",
    )

    assert {product.sku for product in response.products} == {"112060", "112140"}
    answer = response.answer.lower()
    assert "429" in answer
    assert "dn50" in answer and "совместим" in answer
    assert "уплотнен" in answer and "не подтвержда" in answer
    assert response.debug["category"] == "sewer"

    followup = bot.handle_chat(
        "sewer-relation",
        "Так уплотнительные кольца вообще нужны или можно собрать без них? И входят ли они?",
    )
    followup_answer = followup.answer.lower()
    assert "уплотнительное кольцо нужно" in followup_answer
    assert "без него нельзя" in followup_answer
    assert "не подтверждают" in followup_answer and "комплект" in followup_answer

    price_followup = bot.handle_chat(
        "sewer-relation",
        "А оно входит в 53 рубля за отвод или покупать отдельно?",
    )
    price_answer = price_followup.answer.lower()
    assert "честный ответ — неизвестно" in price_answer
    assert "ни вывод «точно входит»" in price_answer


def test_circulation_pump_comparison_keeps_category_for_dimension_choice() -> None:
    common = {
        "тип товара": "Насос",
        "тип насоса": "Циркуляционный",
        "максимальный напор, м": "6",
        "диаметр подключения": "1 1/2",
    }
    short = _product(
        "VRS.256.13.0",
        "Насос циркуляционный VALTEC RS 25/6-130",
        "Насосы циркуляционные",
        price=4311,
        qty=11,
        attributes={**common, "монтажная длина, мм": "130"},
    )
    long = _product(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180",
        "Насосы циркуляционные",
        price=4186,
        qty=17,
        attributes={**common, "монтажная длина, мм": "180"},
    )
    bot = ChatOrchestrator(products=[short, long], llm_client=_OfflineLLM())
    first = bot.handle_chat(
        "pump-dimension",
        "Сравни VRS 256 13 0 и VRS 256 18 0 по напору, подключению и длине.",
    )
    second = bot.handle_chat(
        "pump-dimension",
        "У меня монтажная длина 130 мм. Какой из них брать?",
    )

    assert {product.sku for product in first.products} == {
        "VRS.256.13.0",
        "VRS.256.18.0",
    }
    assert {product.sku for product in second.products} == {
        "VRS.256.13.0",
        "VRS.256.18.0",
    }
    assert "130 мм" in second.answer
    assert "совпадают напор" in second.answer.lower()
    assert second.debug["category"] == "pumps"


def test_pump_followup_explains_why_180_is_not_direct_replacement_for_130() -> None:
    common = {
        "тип товара": "Насос",
        "тип насоса": "Циркуляционный",
        "максимальный напор, м": "6",
        "диаметр подключения": "1 1/2",
    }
    short = _product(
        "VRS.256.13.0",
        "Насос циркуляционный VALTEC RS 25/6-130",
        "Насосы циркуляционные",
        attributes={**common, "монтажная длина, мм": "130"},
    )
    long = _product(
        "VRS.256.18.0",
        "Насос циркуляционный VALTEC RS 25/6-180",
        "Насосы циркуляционные",
        attributes={**common, "монтажная длина, мм": "180"},
    )
    bot = ChatOrchestrator(products=[short, long], llm_client=_OfflineLLM())
    bot.handle_chat(
        "pump-not-direct",
        "Сопоставь VRS 256 13 0 и VRS 256 18 0.",
    )

    response = bot.handle_chat(
        "pump-not-direct",
        "Старый был 130 мм. А если поставить вариант на 180 — он сядет или надо что-то менять?",
    )

    answer = response.answer.lower()
    assert "vrs.256.13.0" in answer
    assert "180 мм не является прямой заменой" in answer
    assert "разница 50 мм" in answer and "геометрии труб" in answer

    repeated = bot.handle_chat(
        "pump-not-direct",
        "Почему тогда VRS.256.18.0 не влезет вместо старого на 130 мм?",
    )
    assert "180 мм не является прямой заменой" in repeated.answer.lower()

    purpose = bot.handle_chat(
        "pump-not-direct",
        "А зачем вообще существует версия 180 мм, если у меня старый на 130 мм?",
    )
    purpose_answer = purpose.answer.lower()
    assert "изначально рассчитанных" in purpose_answer
    assert "не более дешёвая взаимозаменяемая" in purpose_answer

    alternatives = bot.handle_chat(
        "pump-not-direct",
        "А есть ещё варианты 130 мм, кроме VRS.256.13.0?",
    )
    alternatives_answer = alternatives.answer.lower()
    assert "кроме vrs.256.13.0" in alternatives_answer
    assert "другой циркуляционный насос" in alternatives_answer


def test_missing_exact_sku_is_not_replaced_by_an_unrelated_boiler() -> None:
    available_boiler = _product(
        "2201375",
        "Котёл газовый Arderia 24 кВт",
        "Котлы газовые",
        price=35869,
    )
    bot = ChatOrchestrator(products=[available_boiler], llm_client=_OfflineLLM())

    response = bot.handle_chat(
        "missing-exact-sku",
        "В старой выгрузке был котёл, артикул 3636151. Он есть в полном каталоге?",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "3636151" in answer and "точного артикула" in answer
    assert "данные каталога не сообщают" in answer and "почему" in answer
    assert "2201375" not in answer


def test_missing_boiler_followup_asks_observable_inputs_not_unknown_nameplate_data() -> None:
    available_boiler = _product(
        "2201375",
        "Котёл газовый Arderia 24 кВт",
        "Котлы газовые",
        price=35869,
    )
    bot = ChatOrchestrator(products=[available_boiler], llm_client=_OfflineLLM())
    bot.handle_chat(
        "missing-boiler-followup",
        "Ищу старый котёл 3636151, но такого артикула в каталоге нет?",
    )

    response = bot.handle_chat(
        "missing-boiler-followup",
        "А как подобрать замену, если характеристик старого я вообще не знаю?",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "случайн" in answer and "не покажу" in answer
    assert "газ или электричество" in answer
    assert "площадь" in answer and "горячая вода" in answer and "дымоход" in answer
    assert "мощность" in answer and "необязательно" in answer

    progressed = bot.handle_chat(
        "missing-boiler-followup",
        "Газ, дом 120 м², нужна горячая вода, дымоход кирпичный. Дай похожую модель и примерную мощность.",
    )
    progressed_answer = progressed.answer.lower()
    assert progressed.products == []
    assert "любое число было бы выдумкой" in progressed_answer
    assert "площадь 120" in progressed_answer and "горячая вода" in progressed_answer
    assert "категории котлов, а не дымоходов" in progressed_answer


def test_absent_boiler_accepts_observable_facts_on_the_immediate_next_turn() -> None:
    bot = ChatOrchestrator(
        products=[_product("2201375", "Котёл газовый Arderia", "Котлы газовые")],
        llm_client=_OfflineLLM(),
    )
    bot.handle_chat(
        "missing-boiler-immediate-facts",
        "Есть котёл 3636151?",
    )

    response = bot.handle_chat(
        "missing-boiler-immediate-facts",
        "Газ, дом 80 м², нужна горячая вода, есть стандартный дымоход.",
    )

    assert response.products == []
    answer = response.answer.lower()
    assert "площадь 80" in answer and "горячая вода" in answer
    assert "категории котлов, а не дымоходов" in answer


def test_valve_followup_uses_counterpart_thread_topology() -> None:
    ff = _product(
        "VT.217.N.04",
        "Кран шаровой вн.-вн. 1/2",
        "Краны шаровые",
        attributes={"тип резьбы": "Внутренняя/внутренняя"},
    )
    fm = _product(
        "VT.218.N.04",
        "Кран шаровой вн.-нар. 1/2",
        "Краны шаровые",
        attributes={"тип резьбы": "Внутренняя/наружная"},
    )
    bot = ChatOrchestrator(products=[ff, fm], llm_client=_OfflineLLM())
    bot.handle_chat(
        "valve-threads",
        "Сравни VT.217.N.04 и VT.218.N.04 по резьбе.",
    )

    response = bot.handle_chat(
        "valve-threads",
        "На обеих ответных подводках наружная резьба. Какой брать?",
    )

    assert "VT.217.N.04" in response.answer
    assert "два внутренних" in response.answer.lower()
    assert "без дополнительного перехода не подходит" in response.answer.lower()


def test_valve_followup_does_not_hide_three_quarter_size_mismatch() -> None:
    ff = _product(
        "VT.217.N.04",
        "Кран шаровой вн.-вн. 1/2",
        "Краны шаровые",
        attributes={"тип резьбы": "Внутренняя/внутренняя", "размер": "1/2"},
    )
    fm = _product(
        "VT.218.N.04",
        "Кран шаровой вн.-нар. 1/2",
        "Краны шаровые",
        attributes={"тип резьбы": "Внутренняя/наружная", "размер": "1/2"},
    )
    bot = ChatOrchestrator(products=[ff, fm], llm_client=_OfflineLLM())
    bot.handle_chat(
        "valve-size-mismatch",
        "Чем отличаются VT 217 N 04 и VT 218 N 04?",
    )

    response = bot.handle_chat(
        "valve-size-mismatch",
        "У меня с обеих сторон наружная G 3/4. Какой подойдёт без адаптеров?",
    )

    assert {product.sku for product in response.products} == {
        "VT.217.N.04",
        "VT.218.N.04",
    }
    answer = response.answer.lower()
    assert "без перехода нет" in answer
    assert "3/4" in answer and "1/2" in answer
    assert "вр 3/4" in answer and "нр 1/2" in answer


def test_valve_counterpart_topology_is_not_overwritten_by_stock_filter() -> None:
    ff = _product(
        "VT.217.N.04",
        "Кран шаровой вн.-вн. 1/2",
        "Краны шаровые",
        qty=76,
        attributes={"тип резьбы": "Внутренняя/внутренняя"},
    )
    fm = _product(
        "VT.218.N.04",
        "Кран шаровой вн.-нар. 1/2",
        "Краны шаровые",
        qty=0,
        attributes={"тип резьбы": "Внутренняя/наружная"},
    )
    bot = ChatOrchestrator(products=[ff, fm], llm_client=_OfflineLLM())
    bot.handle_chat(
        "valve-stock-topology",
        "Чем отличаются VT 217 N 04 и 218 N 04?",
    )

    response = bot.handle_chat(
        "valve-stock-topology",
        "На обеих подводках наружная резьба, но VT.218.N.04 нет в наличии. Какой нужен?",
    )

    answer = response.answer.lower()
    assert "нужны два внутренних порта" in answer
    assert "подходит vt.217.n.04" in answer
    assert "после финальной проверки" not in answer


def test_shown_thermostatic_head_quotes_quantity_total_without_promising_discount() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC",
        "Арматура для радиаторов",
        price=1044,
        qty=28,
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())
    bot.handle_chat(
        "thermo-quantity",
        "Покажи характеристики артикула VT 1500 0 0.",
    )

    response = bot.handle_chat(
        "thermo-quantity",
        "Если мне нужно 20 штук, сколько получится и дадите оптовую скидку?",
    )

    assert [product.sku for product in response.products] == ["VT.1500.0.0"]
    answer = response.answer.lower()
    assert "20880" in answer and "без скидки" in answer
    assert "не указаны" in answer and "без обещания скидки" in answer


def test_handoff_phrase_mentioned_in_a_question_does_not_start_handoff() -> None:
    head = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC",
        "Арматура для радиаторов",
        price=1044,
        qty=28,
    )
    bot = ChatOrchestrator(products=[head], llm_client=_OfflineLLM())
    bot.handle_chat(
        "handoff-meta-mention",
        "Сколько стоит VT 1500 0 0 и есть ли скидка?",
    )

    response = bot.handle_chat(
        "handoff-meta-mention",
        "Для 10 штук нужно писать «передай менеджеру» или можно назвать цену без скидки?",
    )

    assert response.need_handoff is False
    assert response.products and response.products[0].sku == "VT.1500.0.0"
    assert "10440" in response.answer

    conditional = bot.handle_chat(
        "handoff-meta-mention",
        "А если я напишу «передай менеджеру», он сам скидку посчитает?",
    )
    assert conditional.need_handoff is False
    assert bot.sessions.get("handoff-meta-mention").pending_handoff is None


def test_radiator_followup_keeps_both_cards_and_pressure_boundary() -> None:
    products = [
        _product(
            "RBM-0210-050006",
            "Радиатор биметаллический Rommer 6 секций",
            "Радиаторы отопления",
            price=3050,
            qty=0,
            attributes={
                "материал": "Биметалл",
                "теплоотдача, вт": "774",
                "площадь обогрева, м2": "7.74",
            },
            description="Рабочее давление (МПа) 1.8.",
        ),
        _product(
            "RAL-1210-050006",
            "Радиатор алюминиевый Rommer 6 секций",
            "Радиаторы отопления",
            price=3462,
            qty=0,
            attributes={
                "материал": "Алюминий",
                "теплоотдача, вт": "942",
                "площадь обогрева, м2": "9.42",
            },
            description="Рабочее давление (МПа) 1.6.",
        ),
    ]
    bot = ChatOrchestrator(products=products, llm_client=_OfflineLLM())
    first = bot.handle_chat(
        "radiator-pressure",
        "Что лучше для центрального отопления: RBM-0210-050006 или RAL-1210-050006?",
    )
    second = bot.handle_chat(
        "radiator-pressure",
        "У нас 16 атмосфер. Сравни теплоотдачу и материал. Можно заказать?",
    )

    assert "774" in first.answer and "942" in first.answer
    assert {product.sku for product in second.products} == {
        "RBM-0210-050006",
        "RAL-1210-050006",
    }
    answer = second.answer.lower()
    assert "1.62 мпа" in answer
    assert "ral-1210-050006" in answer and "ниже" in answer
    assert "rbm-0210-050006" in answer and "запас мал" in answer
    assert "остаток" in answer and "менеджер" in answer
    assert second.debug["category"] == "radiators"


def test_radiator_pressure_parser_converts_atmospheres_in_card_to_mpa() -> None:
    radiator = _product(
        "GERMANIUM-500-06",
        "Радиатор биметаллический Germanium 6 секций",
        "Радиаторы отопления",
        description="Рабочее давление 30 атм. Испытательное давление 45 атм.",
    )

    assert ChatOrchestrator._radiator_working_pressure_mpa(radiator) == pytest.approx(
        3.03975
    )
    assert ChatOrchestrator._radiator_test_pressure_mpa(radiator) == pytest.approx(
        4.559625
    )


def test_drainage_problem_frame_hard_excludes_head_below_vertical_lift() -> None:
    dn350 = _product(
        "68/2/8",
        "Дренажный насос Вихрь ДН-350",
        "Насосы дренажные",
        attributes={
            "высота напора, м": "5",
            "макс. производительность, л/ч": "8000",
        },
        description="Для грязной воды с твёрдыми частицами до 35 мм.",
    )
    dn750 = _product(
        "68/2/2",
        "Дренажный насос Вихрь ДН-750",
        "Насосы дренажные",
        attributes={
            "высота напора, м": "8",
            "макс. производительность, л/ч": "15300",
        },
        description="Для грязной воды с твёрдыми частицами до 35 мм.",
    )
    bot = ChatOrchestrator(products=[dn350, dn750], llm_client=_OfflineLLM())
    turns = [
        "В погребе мутная вода с песком, нужно выкачать.",
        "Подъём 6 метров, затем 12 метров шланга.",
        "Нужно 1,5 куба в час.",
        "Покажи подходящий вариант из каталога.",
    ]
    response = None
    for turn in turns:
        response = bot.handle_chat("drainage-full-feed", turn)

    assert response is not None
    assert [product.sku for product in response.products] == ["68/2/2"]
    answer = response.answer.lower()
    assert "68/2/8 исключена" in answer
    assert "q–h" in answer
    assert "не подача при подъёме 6 м" in answer

    documentation = bot.handle_chat(
        "drainage-full-feed",
        "Где на сайте или в карточке искать Q-H кривую: в паспорте или самому измерять?",
    )
    documentation_answer = documentation.answer.lower()
    assert "документ" in documentation_answer or "паспорт" in documentation_answer
    assert "самостоятельный замер" in documentation_answer
    assert "не заменяет паспортный подбор" in documentation_answer
    assert "68/2/2" in documentation_answer

    bot.handle_chat(
        "drainage-full-feed",
        "Если максимум 8 м, а подъём 6 м, потери в шланге не должны превысить 2 м?",
    )
    assert bot.sessions.get("drainage-full-feed").slots["horizontal_run_m"] == 12

    practical = bot.handle_chat(
        "drainage-full-feed",
        "Если Q-H-кривой нет, можно сначала купить и протестировать на практике?",
    )
    practical_answer = practical.answer.lower()
    assert "возврата уже использованного" in practical_answer
    assert "мерной ёмкостью и секундомером" in practical_answer
    assert "12-метровой трассе" in practical_answer

    acknowledgement = bot.handle_chat(
        "drainage-full-feed",
        "Хорошо, посмотрю документы в карточке и спрошу паспорт у продавца.",
    )
    assert "правильный следующий шаг" in acknowledgement.answer.lower()
    repeated_acknowledgement = bot.handle_chat(
        "drainage-full-feed",
        "Сейчас посмотрю документы, а если нет — спрошу у продавца.",
    )
    assert repeated_acknowledgement.answer != acknowledgement.answer

    compound_bot = ChatOrchestrator(products=[dn350, dn750], llm_client=_OfflineLLM())
    compound_bot.handle_chat(
        "drainage-volume-compound",
        "В погребе мутная вода с песком, нужно выкачать.",
    )
    compound_bot.handle_chat(
        "drainage-volume-compound",
        "Подъём 6 метров, затем 12 метров шланга.",
    )
    compound = compound_bot.handle_chat(
        "drainage-volume-compound",
        "Объём воды 6 м³, убрать за 2 часа. Покажи модель.",
    )
    assert [product.sku for product in compound.products] == ["68/2/2"]
    assert "3 м³/ч" in compound.answer

    half_depth_bot = ChatOrchestrator(products=[dn350, dn750], llm_client=_OfflineLLM())
    half_depth_bot.handle_chat(
        "drainage-half-metre",
        "В погребе мутная вода с песком, нужно выкачать.",
    )
    half_depth_bot.handle_chat(
        "drainage-half-metre",
        "Подъём 6 метров и 12 метров шланга.",
    )
    half_depth = half_depth_bot.handle_chat(
        "drainage-half-metre",
        "Зона 3 на 4 метра, глубина до полуметра, убрать за 2 часа. Какой насос подойдёт?",
    )
    assert [product.sku for product in half_depth.products] == ["68/2/2"]
    assert "3 м³/ч" in half_depth.answer


def test_feed_loader_decodes_nested_html_and_removes_pack_suffix() -> None:
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <yml_catalog><shop><offers><offer id='112060'>
      <name>Truba HTEM 50*2000&amp;amp;quot;10</name>
      <vendorCode>VTp.700.FB20.20</vendorCode>
      <categoryId>1</categoryId><price>376</price><currencyId>RUB</currencyId>
      <url>https://example.test/112060</url>
    </offer></offers></shop></yml_catalog>"""

    products = FeedLoader().parse_xml(xml)

    assert len(products) == 1
    assert products[0].name == "Truba HTEM 50*2000"
    assert products[0].sku == "VTp.700.FB20.20"
