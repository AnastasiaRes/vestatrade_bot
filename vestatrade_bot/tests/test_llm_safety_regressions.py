from __future__ import annotations

from threading import local

from app.agents.consultant import ConsultantAgent
from app.agents.intent_router import IntentRouterAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.response_composer import ResponseComposerAgent
from app.agents.utils import normalize_sku
from app.feed_loader import FeedLoader
from app.models import Product, SessionState
from app.openrouter_client import LLMResult, OpenRouterClient


class _NoLLMExpected:
    def complete(self, *args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("identity and field-service answers must be deterministic")


class _FixedLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=self.reply, llm_used=True)

    def complete_json(self, _agent, _messages, fallback):
        return fallback, False


class _JSONReplyClient(OpenRouterClient):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self._telemetry = local()

    def complete(self, *args, **kwargs) -> LLMResult:
        return LLMResult(content=self.reply, llm_used=True)


class _HistoryAwareIntentClient:
    last_json_output_accepted = True

    def __init__(self, forced_intent: str | None = None) -> None:
        self.calls = 0
        self.forced_intent = forced_intent

    def complete_json(self, _agent, messages, fallback):
        self.calls += 1
        prompt = messages[-1]["content"]
        boiler_type = "электрический" if "электрический" in prompt else "газовый"
        return (
            {
                "intent_type": self.forced_intent or "attribute_request",
                "category": "boilers" if self.forced_intent is None else "other",
                "slots": {"boiler_type": boiler_type},
                "flags": {},
                "confidence": 0.9,
            },
            True,
        )


class _HistorySkuIntentClient:
    last_json_output_accepted = True

    def complete_json(self, _agent, _messages, _fallback):
        return (
            {
                "intent_type": "attribute_request",
                "category": "pumps",
                "slots": {"sku": "PUMP-25-60"},
                "flags": {},
                "confidence": 0.9,
            },
            True,
        )


def test_identity_question_discloses_ai_without_calling_llm() -> None:
    composer = ResponseComposerAgent(llm_client=_NoLLMExpected())

    answer = composer.compose_small_talk("ты живой человек или бот?")

    assert "AI-консультант" in answer
    assert "живой" not in answer.lower()


def test_field_service_question_does_not_claim_physical_visit() -> None:
    composer = ResponseComposerAgent(llm_client=_NoLLMExpected())

    answer = composer.compose_small_talk("а ты можешь выехать ко мне и всё смонтировать?")

    assert "не могу приехать" in answer.lower()
    assert "менеджер" in answer.lower()


def test_selection_for_installation_is_not_mistaken_for_an_on_site_visit() -> None:
    composer = ResponseComposerAgent(llm_client=_NoLLMExpected())

    assert composer.compose_identity_or_service(
        "Вы продаёте комплект для монтажа котла?"
    ) is None
    assert composer.compose_identity_or_service(
        "Можете подобрать арматуру для установки радиатора?"
    ) is None


def test_orchestrator_enforces_identity_and_visit_boundaries_before_routing() -> None:
    bot = ChatOrchestrator(products=[], llm_client=_NoLLMExpected())

    identity = bot.handle_chat("identity-e2e", "привет! ты живой человек или бот?")
    visit = bot.handle_chat(
        "identity-e2e",
        "а ты можешь выехать ко мне и всё смонтировать?",
    )

    assert "ai-консультант" in identity.answer.lower()
    assert "не могу приехать" in visit.answer.lower()
    assert "менеджер" in visit.answer.lower()
    assert identity.debug["any_llm_used"] is False
    assert visit.debug["any_llm_used"] is False
    assert identity.debug["llm_requested"] is False
    assert identity.debug["final_answer_source"] == "deterministic"


def test_debug_distinguishes_transport_from_rejected_llm_output() -> None:
    bot = ChatOrchestrator(
        products=[],
        llm_client=_FixedLLM("У нас в ассортименте есть любые котлы в наличии."),
    )

    response = bot.handle_chat("metrics-rejected", "как дела?")

    assert response.debug["llm_requested"] is True
    assert response.debug["llm_transport_succeeded"] is True
    assert response.debug["llm_output_accepted"] is False
    assert response.debug["final_answer_source"] == "deterministic"
    assert response.debug["response_llm_rejection_reason"] == "ungrounded_assortment_claim"
    assert "любые котлы" not in response.answer.lower()


def test_debug_marks_accepted_response_llm_as_final_source() -> None:
    bot = ChatOrchestrator(
        products=[],
        llm_client=_FixedLLM("Дела хорошо, спасибо. Готов помочь с подбором."),
    )

    response = bot.handle_chat("metrics-accepted", "как дела?")

    assert response.debug["llm_transport_succeeded"] is True
    assert response.debug["llm_output_accepted"] is True
    assert response.debug["final_answer_source"] == "response_llm"


def test_small_talk_rejects_claim_that_vesta_is_customers_store() -> None:
    bot = ChatOrchestrator(
        products=[],
        llm_client=_FixedLLM(
            "Спасибо! Помогу выбрать оборудование для вашего интернет-магазина Vesta Trading."
        ),
    )

    response = bot.handle_chat("wrong-store-owner", "ты красивая")

    assert "вашего интернет-магазина" not in response.answer.lower()
    assert response.debug["llm_output_accepted"] is False
    assert response.debug["final_answer_source"] == "deterministic"


def test_json_transport_success_is_not_reported_as_accepted_on_parse_failure() -> None:
    fallback = {"intent_type": "unknown"}
    malformed = _JSONReplyClient("not-json")
    valid = _JSONReplyClient('{"intent_type": "small_talk"}')

    malformed_data, malformed_used = malformed.complete_json(
        "intent",
        [],
        fallback,
    )
    valid_data, valid_used = valid.complete_json("intent", [], fallback)

    assert malformed_used is True
    assert malformed_data == fallback
    assert malformed.last_json_output_accepted is False
    assert valid_used is True
    assert valid_data["intent_type"] == "small_talk"
    assert valid.last_json_output_accepted is True


def test_malformed_json_log_never_echoes_provider_payload(caplog) -> None:
    secret_payload = "not-json phone +7 999 123-45-67"
    client = _JSONReplyClient(secret_payload)

    client.complete_json("privacy-test", [], {"kind": "fallback"})

    assert secret_payload not in caplog.text
    assert "+7 999 123-45-67" not in caplog.text
    assert "sha256=" in caplog.text


def test_malformed_json_with_unpaired_surrogate_returns_safe_fallback(caplog) -> None:
    client = _JSONReplyClient("not-json\ud800private")
    fallback = {"kind": "fallback"}

    parsed, transport_used = client.complete_json("surrogate-test", [], fallback)

    assert parsed == fallback
    assert transport_used is True
    assert client.last_json_output_accepted is False
    assert "private" not in caplog.text
    assert "sha256=" in caplog.text


def test_intent_cache_is_history_and_session_scoped() -> None:
    client = _HistoryAwareIntentClient()
    router = IntentRouterAgent(client)
    gas = SessionState(
        session_id="gas-customer",
        category="boilers",
        last_intent="broad_category",
        history=[{"role": "user", "content": "Хочу газовый котёл"}],
    )
    electric = SessionState(
        session_id="electric-customer",
        category="boilers",
        last_intent="broad_category",
        history=[{"role": "user", "content": "Хочу электрический котёл"}],
    )

    gas_result = router.route("ну да", gas)
    electric_result = router.route("ну да", electric)

    assert gas_result.slots["boiler_type"] == "газовый"
    assert electric_result.slots["boiler_type"] == "электрический"
    assert client.calls == 2


def test_intent_cache_hit_does_not_report_a_new_llm_transport() -> None:
    client = _HistoryAwareIntentClient()
    router = IntentRouterAgent(client)
    session = SessionState(session_id="cache-metrics")

    first = router.route("ну да", session)
    second = router.route("ну да", session)

    assert first.raw["llm_transport_succeeded"] is True
    assert second.raw["llm_requested"] is False
    assert second.raw["llm_transport_succeeded"] is False
    assert second.raw["llm_output_accepted"] is False
    assert second.raw["intent_source"] == "cache"
    assert client.calls == 1


def test_sanity_override_marks_llm_intent_as_rejected() -> None:
    client = _HistoryAwareIntentClient(forced_intent="complectation")

    result = IntentRouterAgent(client).route(
        "абракадабра",
        SessionState(session_id="sanity"),
    )

    assert result.intent_type != "complectation"
    assert result.raw["llm_output_accepted"] is False
    assert result.raw["llm_rejection_reason"] == "intent_sanity_check_override"


def test_llm_cannot_copy_history_sku_into_current_followup() -> None:
    router = IntentRouterAgent(_HistorySkuIntentClient())
    session = SessionState(
        session_id="history-sku",
        category="pumps",
        history=[
            {"role": "user", "content": "Покажи PUMP-25-60"},
            {"role": "assistant", "content": "Нашёл PUMP-25-60"},
        ],
    )

    followup = router.route("ну да", session)
    exact = router.route("PUMP-25-60", session)

    assert "sku" not in followup.slots
    assert followup.raw["llm_output_accepted"] is False
    assert exact.intent_type == "exact_sku"
    assert exact.slots["sku"] == "pump-25-60"


def test_compact_sku_matcher_rejects_plain_model_words() -> None:
    router = IntentRouterAgent()

    assert router._is_valid_sku_candidate("CMSR02CA28") is True
    assert router._is_valid_sku_candidate("arderia12") is False
    assert router._is_valid_sku_candidate("ferroli24") is False
    assert router._is_valid_sku_candidate("котел24квт") is False


def test_elongated_no_gas_typo_overrides_gas_keyword() -> None:
    session = SessionState(
        session_id="no-gas-typo",
        category="boilers",
        slots={"boiler_type": "газовый", "area_m2": 100.0},
        pending_question="Котёл нужен газовый или электрический?",
        pending_intent_type="broad_category",
    )

    intent = IntentRouterAgent().route("ГАЗА НЕЕЕТ", session)

    assert intent.slots["boiler_type"] == "электрический"


def test_well_typo_keeps_source_and_does_not_ask_it_twice() -> None:
    well_pump = Product(
        sku="WELL-40",
        name="Насос скважинный 3-40",
        category_path="Насосы скважинные",
        url="https://example.test/well-40",
        price=8000,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип товара": "Насос"},
    )
    bot = ChatOrchestrator(products=[well_pump])

    first = bot.handle_chat(
        "well-typo",
        "ало че по насосам для скважны нужен срочн скок стоит",
    )
    second = bot.handle_chat(
        "well-typo",
        "глубина скважны метров 40, дом небольшой",
    )

    assert "источник — скважина" in first.answer.lower()
    assert "глубин" in first.answer.lower()
    assert second.debug["slots"]["water_source"] == "скважина"
    assert second.debug["slots"]["well_depth_m"] == 40.0
    assert "источник воды какой" not in second.answer.lower()


def test_foreign_boiler_description_cannot_override_structured_contours() -> None:
    raw_product = Product(
        sku="CMSR02CA28",
        name="Котел газовый настенный двухконтурный Fondital MAIORCA CTFS 28",
        category_path="Котельное оборудование",
        brand="FONDITAL",
        url="https://example.test/maiorca-ctfs-28",
        price=86250,
        stock_status="нет в наличии",
        stock_qty=0,
        attributes_normalized={
            "артикул": "CMSR02CA28",
            "количество контуров": "Двухконтурный",
            "тип товара": "Котёл",
            "тип котла": "Газовый",
        },
        description=(
            "Одноконтурный котёл другой модели, артикул CMSR02RF28, "
            "для подключения внешнего бойлера."
        ),
    )
    product = FeedLoader()._sanitize_products([raw_product])[0]
    bot = ChatOrchestrator(products=[product])

    first = bot.handle_chat("foreign-description", "CMSR02CA28")
    second = bot.handle_chat(
        "foreign-description",
        "этот котёл одноконтурный?",
    )

    assert product.description is None
    assert first.products and first.products[0].sku == "CMSR02CA28"
    assert second.answer.startswith("Нет:")
    assert "двухконтурный" in second.answer.lower()
    assert "одноконтурный котёл другой модели" not in second.answer.lower()


def test_consultant_rejects_combustion_chamber_for_electric_boiler() -> None:
    product = Product(
        sku="ARD-E12",
        name="Котел электрический Arderia E12, 12 кВт",
        category_path="Котлы электрические",
        brand="Arderia",
        url="https://example.test/e12",
        price=36534,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"тип котла": "Электрический", "мощность, кВт": "12"},
    )

    issues = ConsultantAgent()._grounding_violations(
        "Котел Arderia E12, арт. ARD-E12. Он также имеет закрытую камеру сгорания.",
        {normalize_sku(product.sku): product},
    )

    assert any("камера сгорания" in issue for issue in issues)


def test_consultant_accepts_explicit_absence_of_chamber_for_electric_boiler() -> None:
    product = Product(
        sku="ARD-E12",
        name="Котел электрический Arderia E12, 12 кВт",
        category_path="Котлы электрические",
        brand="Arderia",
        url="https://example.test/e12",
        price=36534,
        stock_status="в наличии",
        attributes_normalized={"тип котла": "Электрический", "мощность, кВт": "12"},
    )

    issues = ConsultantAgent()._grounding_violations(
        "Котел Arderia E12, арт. ARD-E12. У электрического котла нет камеры сгорания.",
        {normalize_sku(product.sku): product},
    )

    assert not any("камера сгорания" in issue for issue in issues)


def test_consultant_rejects_underpowered_boiler_called_sufficient() -> None:
    product = Product(
        sku="ARD-E9",
        name="Котел электрический Arderia E9, 9 кВт",
        category_path="Котлы электрические",
        brand="Arderia",
        url="https://example.test/e9",
        price=35365,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"тип котла": "Электрический", "мощность, кВт": "9"},
    )
    session = SessionState(session_id="underpowered", slots={"area_m2": 100.0})

    issues = ConsultantAgent()._grounding_violations(
        "Котел Arderia E9, арт. ARD-E9. Этот котёл будет достаточно мощным для 100 м², "
        "и у него есть запас.",
        {normalize_sku(product.sku): product},
        session=session,
    )

    assert any("недостаточна" in issue for issue in issues)


def test_consultant_rejects_power_borrowed_from_another_boiler() -> None:
    e9 = Product(
        sku="2202210",
        name="Котел электрический Arderia E9, 9 кВт",
        category_path="Котлы электрические",
        brand="Arderia",
        url="https://example.test/e9",
        price=35365,
        stock_status="в наличии",
        stock_qty=5,
        attributes_normalized={"мощность, кВт": "9", "отапливаемая площадь, м²": "90"},
    )
    e12 = Product(
        sku="2202211",
        name="Котел электрический Arderia E12, 12 кВт",
        category_path="Котлы электрические",
        brand="Arderia",
        url="https://example.test/e12",
        price=36534,
        stock_status="в наличии",
        stock_qty=2,
        attributes_normalized={"мощность, кВт": "12", "отапливаемая площадь, м²": "120"},
    )
    by_sku = {normalize_sku(product.sku): product for product in [e9, e12]}

    issues = ConsultantAgent()._grounding_violations(
        "1. Arderia E9 (24 кВт), артикул 2202210.\n"
        "2. Arderia E12 (24 кВт), артикул 2202211.",
        by_sku,
    )

    assert any("9 кВт" not in issue and "2202210" in issue for issue in issues)
    assert any("2202211" in issue for issue in issues)


def test_consultant_rejects_collective_suitability_for_underpowered_models() -> None:
    products = [
        Product(
            sku="E9",
            name="Котел Arderia E9, 9 кВт",
            url="https://example.test/e9",
            price=35000,
            stock_status="в наличии",
            attributes_normalized={"мощность, кВт": "9"},
        ),
        Product(
            sku="E12",
            name="Котел Arderia E12, 12 кВт",
            url="https://example.test/e12",
            price=36000,
            stock_status="в наличии",
            attributes_normalized={"мощность, кВт": "12"},
        ),
    ]
    session = SessionState(session_id="collective", slots={"area_m2": 140.0})

    issues = ConsultantAgent()._grounding_violations(
        "Arderia E9, артикул E9, и Arderia E12, артикул E12. "
        "Эти модели имеют достаточную мощность для вашего дома.",
        {normalize_sku(product.sku): product for product in products},
        session=session,
    )

    assert any("коллективная рекомендация" in issue for issue in issues)


def test_consultant_rejects_underpowered_boiler_called_acceptable_or_recommended() -> None:
    product = Product(
        sku="E9",
        name="Котел Arderia E9, 9 кВт",
        url="https://example.test/e9",
        price=35000,
        stock_status="в наличии",
        attributes_normalized={"мощность, кВт": "9"},
    )
    session = SessionState(session_id="acceptable", slots={"area_m2": 140.0})

    issues = ConsultantAgent()._grounding_violations(
        "Котел Arderia E9, артикул E9 — вполне приемлемый вариант; рекомендую его.",
        {normalize_sku(product.sku): product},
        session=session,
    )

    assert any("недостаточна" in issue for issue in issues)


def test_consultant_article_parser_does_not_match_inside_card_word() -> None:
    product = Product(
        sku="VT.217.N.04",
        name="Кран шаровой VALTEC",
        url="https://example.test/valve",
        price=100,
        stock_status="в наличии",
    )
    by_sku = {normalize_sku(product.sku): product}

    correct = ConsultantAgent()._grounding_violations(
        "По карточке товара важны назначение и размер. Артикул: VT.217.N.04.",
        by_sku,
    )
    prefix = ConsultantAgent()._grounding_violations(
        "Проверьте артикул VT.217.N.",
        by_sku,
    )

    assert not any("артикул" in issue for issue in correct)
    assert any("артикул" in issue for issue in prefix)


def test_consultant_rejects_recommended_product_outside_retrieval_without_type_prefix() -> None:
    product = Product(
        sku="2201375",
        name="Котёл газовый Arderia SB24 24 кВт",
        brand="Arderia",
        url="https://example.test/sb24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
    )

    issues = ConsultantAgent()._grounding_violations(
        "Рекомендую Ferroli Bluehelix 24C за 35000 RUB.",
        {normalize_sku(product.sku): product},
    )

    assert any("не из каталога" in issue for issue in issues)


def test_consultant_binds_prices_and_stock_to_each_specific_product() -> None:
    first = Product(
        sku="2201375",
        name="Котёл газовый Arderia SB24 24 кВт",
        brand="Arderia",
        url="https://example.test/sb24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
    )
    second = Product(
        sku="ARD-E12",
        name="Котёл электрический Arderia E12 12 кВт",
        brand="Arderia",
        url="https://example.test/e12",
        price=36534,
        stock_status="в наличии",
        stock_qty=7,
    )
    by_sku = {
        normalize_sku(first.sku): first,
        normalize_sku(second.sku): second,
    }
    consultant = ConsultantAgent()

    correct = consultant._grounding_violations(
        "Arderia SB24, арт. 2201375 — 35000 RUB, 2 шт.\n"
        "Arderia E12, арт. ARD-E12 — 36534 RUB, 7 шт.",
        by_sku,
    )
    swapped = consultant._grounding_violations(
        "Arderia SB24, арт. 2201375 — 36534 RUB, 7 шт.\n"
        "Arderia E12, арт. ARD-E12 — 35000 RUB, 2 шт.",
        by_sku,
    )

    assert correct == []
    assert any("цена" in issue and "2201375" in issue for issue in swapped)
    assert any("остаток" in issue and "2201375" in issue for issue in swapped)
    assert any("цена" in issue and "ARD-E12" in issue for issue in swapped)
    assert any("остаток" in issue and "ARD-E12" in issue for issue in swapped)
