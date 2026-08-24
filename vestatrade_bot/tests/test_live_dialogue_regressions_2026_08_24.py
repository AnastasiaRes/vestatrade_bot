"""Регрессы на дефекты живого прогона ста диалогов 23.08.2026.

Каждый тест назван по коду дефекта из реестра разбора и проверяет **класс**
ошибки, а не формулировку ответа. Идентификаторы сценариев (A06, B08, D01, D21)
— из тест-набора живого прогона, файл
``reports/live_dialogues_2026-08-23/dialogues.json``.

Все сценарии воспроизводятся без обращения к сети: там, где нужен текст модели,
подставляется клиент-двойник, который ведёт себя как настоящий (пишет отданный
текст в запись хода).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.orchestrator import ChatOrchestrator
from app.openrouter_client import LLMResult, OpenRouterClient


# ---------------------------------------------------------------------------
# Двойники LLM
# ---------------------------------------------------------------------------


class _SilentLLM(OpenRouterClient):
    """Модель недоступна: ответы собирает детерминированный слой."""

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(content=None, llm_used=False, fallback_reason="offline")

    def complete_json(
        self,
        _agent: str,
        _messages: list[dict[str, str]],
        fallback: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], bool]:
        return fallback, False


class _TalkingLLM(_SilentLLM):
    """Модель отвечает прозой и, как настоящий клиент, пишет её в запись хода."""

    text = (
        "Здравствуйте! Рад помочь с подбором оборудования Vesta Trading: котлы, "
        "насосы, трубы, краны и радиаторная арматура. Опишите задачу своими "
        "словами, и я подберу подходящие позиции."
    )

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        self.record_completion(self.text)
        return LLMResult(content=self.text, llm_used=True)


@pytest.fixture
def bot() -> ChatOrchestrator:
    """Пустой каталог: эти классы ошибок от ассортимента не зависят."""
    return ChatOrchestrator(products=[], llm_client=_SilentLLM())


# ---------------------------------------------------------------------------
# Д5. Метка источника ответа обязана следовать за текстом, а не за флагами
# ---------------------------------------------------------------------------


def test_d5_answer_without_model_text_is_labelled_deterministic(
    bot: ChatOrchestrator,
) -> None:
    """Шаблонный ответ остаётся детерминированным.

    Обратная сторона фикса: метка не должна поехать на ходах, где модель
    вызывалась только для разбора реплики и текста ответа не писала.
    """
    response = bot.handle_chat("d5-det", "Здравствуйте, нужен кран на стояк ХВС 1/2")

    assert response.debug["final_answer_source"] == "deterministic"


def test_d5_model_text_in_answer_is_never_labelled_deterministic() -> None:
    """A23, B18: текст из системного промпта консультанта помечался шаблонным.

    Шесть ходов живого прогона начинались фразой «Веста Трейдинг, AI-консультант
    на связи» — она существует только в промпте — и были помечены
    ``deterministic``, потому что метка выводилась из флагов «вывод принят».
    Текст модели в ответе обязан быть виден в телеметрии.
    """
    bot = ChatOrchestrator(products=[], llm_client=_TalkingLLM())

    response = bot.handle_chat("d5-llm", "Привет, ты кто такой?")

    assert _TalkingLLM.text[:40] in response.answer
    assert response.debug["final_answer_source"] != "deterministic"


def test_d5_calling_the_model_is_not_the_same_as_printing_it() -> None:
    """Вызов модели сам по себе метку не меняет.

    Живой прогон показал обратную ошибку той же природы: агрегатный флаг
    «вывод принят» поднимал и инженерный интерпретатор, который вызывает модель
    только для разбора реплики. Метку определяет текст ответа, а не факт вызова.
    """
    bot = ChatOrchestrator(products=[], llm_client=_TalkingLLM())

    bot.handle_chat("d5-called-not-printed", "Привет, ты кто такой?")
    response = bot.handle_chat(
        "d5-called-not-printed",
        "Спасибо, всё ясно — оформлю сам. Пока!",
    )

    assert bot.llm_client.recorded_completions(), "модель в этом ходе вызывалась"
    assert _TalkingLLM.text[:40] not in response.answer
    assert response.debug["final_answer_source"] == "deterministic"


def test_d5_unclaimed_model_text_is_reported_as_unattributed() -> None:
    """Текст модели, который не признал ни один агент, получает свою метку.

    Это прямой контракт слоя сериализации: если совпадение с выводом модели
    есть, а флага «вывод принят» нет ни у одного агента, ход помечается
    ``llm_unattributed`` — так протечка достоверности видна в телеметрии,
    а не прячется среди шаблонных ответов.
    """
    bot = ChatOrchestrator(products=[], llm_client=_TalkingLLM())
    bot.llm_client.begin_turn_recording()
    bot.llm_client.record_completion(_TalkingLLM.text)

    assert bot._answer_carries_llm_text(_TalkingLLM.text) is True
    assert bot._answer_carries_llm_text("Уточните размер: 1/2 или 3/4.") is False


def test_d5_recording_does_not_leak_between_turns() -> None:
    """Запись открывается заново каждым ходом.

    Иначе текст, отданный моделью на первом ходе, помечал бы моделью все
    последующие шаблонные ответы той же сессии.
    """
    client = _TalkingLLM()
    client.begin_turn_recording()
    client.record_completion("какой-то ответ модели длиной больше порога сравнения")
    assert client.recorded_completions()

    client.begin_turn_recording()
    assert client.recorded_completions() == []


# ---------------------------------------------------------------------------
# Д2. Контакт, названный раньше просьбы о передаче
# ---------------------------------------------------------------------------


def test_d2_phone_given_before_the_request_is_not_asked_again(
    bot: ChatOrchestrator,
) -> None:
    """A06: телефон назван на ходу 2, «передай менеджеру» — на ходу 3.

    Контакт искали только в текущей реплике, поэтому бот просил его повторно
    у покупателя, который уже всё написал. Это самая частая концовка прогона.
    """
    bot.handle_chat("d2-phone", "Здравствуйте, где мой заказ №148237? Когда он придёт?")
    bot.handle_chat("d2-phone", "Номер заказа 148237. Звонил по телефону +7(999)123-45-67.")
    response = bot.handle_chat("d2-phone", "Передай менеджеру")

    assert "оставьте телефон" not in response.answer.lower()
    assert "***4567" in response.answer, "подхваченный контакт показывается маской"
    assert "подтвердите" in response.answer.lower(), "и требует согласия"


def test_d2_email_given_before_the_request_is_not_asked_again(
    bot: ChatOrchestrator,
) -> None:
    """D14: то же самое для почты."""
    bot.handle_chat("d2-mail", "Где мой заказ? Почему никто не отвечает?!")
    bot.handle_chat("d2-mail", "Номер заказа 789456, на почту zakaz@ex.com. Срочно надо!")
    response = bot.handle_chat("d2-mail", "Передай менеджеру — и всё.")

    assert "оставьте телефон" not in response.answer.lower()
    assert "@ex.com" in response.answer


def test_d2_remembered_contact_is_dropped_when_customer_refuses_handoff(
    bot: ChatOrchestrator,
) -> None:
    """Отказ от передачи стирает запомненный контакт.

    Обратная сторона фикса: запоминание не должно превращаться в хранение
    контакта, от передачи которого покупатель отказался.
    """
    bot.handle_chat("d2-optout", "Мой телефон +7(999)123-45-67, нужен насос")
    bot.handle_chat("d2-optout", "Передай менеджеру")
    bot.handle_chat("d2-optout", "Нет, не передавай менеджеру, я сам разберусь")

    assert bot.sessions.get("d2-optout").contact is None


def test_d2_company_tax_id_is_not_taken_for_a_phone_number(
    bot: ChatOrchestrator,
) -> None:
    """A08: ИНН уезжал в заявку как контакт покупателя.

    «ООО „Стройпоток“, ИНН 7714123456» давало ``контакт: ***3456``. Реквизит
    организации способом связи не является — ни для передачи, ни для хранения.
    """
    response = bot.handle_chat(
        "d2-inn",
        'Нужен счёт на ООО «Стройпоток», ИНН 7714123456. Коллекторы Valtec 1", 4 шт.',
    )
    bot.handle_chat("d2-inn", "Передай менеджеру")

    assert bot.sessions.get("d2-inn").contact is None
    assert "***3456" not in response.answer


def test_d2_tax_id_survives_redaction_in_the_manager_summary() -> None:
    """ИНН — содержание просьбы, а не контакт: вырезать его нельзя.

    Он уезжал в сводку как «[телефон удалён из описания]», и менеджер получал
    просьбу выставить счёт неизвестно кому.
    """
    from app.agents.handoff import HandoffAgent

    redacted = HandoffAgent.redact_contact(
        "ООО «Стройпоток», ИНН 7714123456, нужен счёт. Телефон +7(999)123-45-67"
    )

    assert "7714123456" in redacted
    assert "+7(999)123-45-67" not in redacted


# ---------------------------------------------------------------------------
# Д3. «Покажите варианты» — команда, а не фраза
# ---------------------------------------------------------------------------


def test_d3_show_options_command_actually_shows_options() -> None:
    """B08, D01: бот предлагал написать «покажите варианты» и не реагировал.

    Во всей кодовой базе фраза встречалась ровно один раз — в самом
    предложении. Обработчика не было ни в роутере, ни в оркестраторе.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("d3", "Нужен циркуляционный насос для отопления в частном доме")
    bot.handle_chat("d3", "Параметров не знаю")
    response = bot.handle_chat("d3", "Покажите варианты")

    assert response.products, "команда обязана показать позиции каталога"
    assert "не подтверж" in response.answer.lower(), "и честно назвать непроверенное"


def test_d3_show_options_needs_a_category_to_search_in() -> None:
    """Без названной категории команда не срабатывает.

    Иначе «покажи варианты» первой репликой выдавало бы случайную выборку
    из четырнадцати тысяч позиций.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("d3-bare", "Покажи варианты")

    assert not response.products


# ---------------------------------------------------------------------------
# Д8. Газовый стоп-режим: отказ по опасному, ответ по товарному
# ---------------------------------------------------------------------------


_GAS_REFUSAL_MARK = "Инструкцию по подключению к газу я не дам"


def test_d8_gas_work_instruction_is_still_refused(bot: ChatOrchestrator) -> None:
    """C11: просьба о самостоятельном подключении газа обязана получать отказ.

    Это поведение правильное и остаётся: сужение окна не должно превратиться
    в разрешение опасной инструкции.
    """
    first = bot.handle_chat(
        "d8-refuse",
        "Можно ли самому подключить газовый котёл без вызова специалиста? Если да — как?",
    )
    followup = bot.handle_chat("d8-refuse", "А если я сам всё сделаю — не будет ли штрафа?")

    assert _GAS_REFUSAL_MARK in first.answer
    assert _GAS_REFUSAL_MARK in followup.answer


def test_d8_product_question_is_not_swallowed_by_the_gas_window() -> None:
    """D21: «где найти узел для котла» получал отказ вместо подбора.

    Окно продлевалось при каждом срабатывании, а маркеры действия широки
    («подключ», «резьб»), поэтому запрос товара попадал под отказ бессрочно.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat(
        "d8-product",
        "Можно ли самому подключить газовый котёл? Если да — как?",
    )
    response = bot.handle_chat(
        "d8-product",
        "Понял. Просто нужен узел с тремя штуцерами G1/2 для газового котла. Где его найти?",
    )

    assert _GAS_REFUSAL_MARK not in response.answer


def test_d8_gas_window_is_not_extended_by_its_own_answers() -> None:
    """Срок окна ставится один раз.

    Раньше ``expires_at`` пересчитывался при каждом срабатывании, поэтому окно
    не истекало, пока покупатель произносил слово «газ».
    """
    bot = ChatOrchestrator(products=[], llm_client=_SilentLLM())

    bot.handle_chat("d8-window", "Хочу сам врезаться в газовую трубу, как это сделать?")
    first_deadline = bot.sessions.get("d8-window").slots.get("gas_work_safety_expires_at")
    bot.handle_chat("d8-window", "А если я сам всё сделаю, что будет?")
    second_deadline = bot.sessions.get("d8-window").slots.get("gas_work_safety_expires_at")

    assert first_deadline is not None
    assert first_deadline == second_deadline


def test_d3_show_options_does_not_hijack_a_request_with_its_own_parameters() -> None:
    """Команда не должна перехватывать содержательный запрос.

    «Старый насос Grundfos UPS 25-60, нужна более дешёвая альтернатива. Покажи
    варианты в наличии» несёт собственную модель и условие: подменять его
    выдачей по категории — потерять контекст, который покупатель уже дал.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("d3-rich", "циркуляционный насос, подешевле")
    response = bot.handle_chat(
        "d3-rich",
        "Старый насос Grundfos UPS 25-60, нужна более дешёвая альтернатива. "
        "Покажи варианты в наличии с ценой и ссылкой.",
    )

    assert "Показываю по тому, что уже известно" not in response.answer


# ---------------------------------------------------------------------------
# Д11. Круг из чередующихся шаблонов
# ---------------------------------------------------------------------------


def test_d11_repeated_answer_eventually_changes_strategy() -> None:
    """A01: бот отдавал один и тот же ответ, меняя только вступление.

    Прежние защиты сравнивали дословную формулировку висящего вопроса и не
    проверяли ответы длиннее 300 символов, поэтому круг из двух-трёх
    чередующихся шаблонов кругом не считался: 69 повторов в 56 диалогах.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    responses = []
    for message in [
        "Здравствуйте! Срочно надо радиаторы на две комнаты, не знаю, что брать.",
        "Не знаю, что выбрать. Подскажите, какой лучше взять для квартиры?",
        "Ну просто подскажите, какие радиаторы чаще берут в обычных квартирах.",
        "Мне не нужны технические детали — дайте 2-3 модели с ценой.",
        "Ну хоть что-нибудь покажите.",
        "Ну хоть что-нибудь покажите.",
    ]:
        responses.append(bot.handle_chat("d11", message))

    browse_response = responses[3]
    assert 2 <= len(browse_response.products) <= 3
    assert all("радиатор" in product.name.lower() for product in browse_response.products)
    assert all(product.price is not None for product in browse_response.products)
    assert browse_response.need_handoff is False
    assert "не буду подставлять случайный товар" not in browse_response.answer.lower()


def test_d11_safety_answers_must_still_repeat_verbatim() -> None:
    """C12: повтор инструкции по безопасности — правильное поведение.

    Пока розетка не проверена, покупатель обязан получать то же предупреждение,
    сколько бы раз ни переспрашивал. Защита от кругов не должна его размывать.
    """
    bot = ChatOrchestrator(products=[], llm_client=_SilentLLM())

    answers = [
        bot.handle_chat("d11-safety", message).answer
        for message in [
            "Можно ли в обычную розетку через удлинитель подключить электрокотёл 9 кВт?",
            "А если просто вставлю удлинитель в розетку и включу котёл — что будет?",
            "А если всё-таки включу котёл через удлинитель в обычную розетку?",
        ]
    ]

    assert all("не подключайте мощный" in answer.lower() for answer in answers)


def test_d11_showing_products_is_not_treated_as_repetition() -> None:
    """Повторная выдача карточек — это движение, а не круг.

    Обратная сторона фикса: покупатель, дважды спросивший «что есть по 1/2»,
    обязан снова увидеть позиции, а не предложение передать задачу менеджеру.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    first = bot.handle_chat("d11-cards", "Нужен шаровой кран 1/2 ВР-НР на стояк ХВС")
    repeat = bot.handle_chat("d11-cards", "Нужен шаровой кран 1/2 ВР-НР на стояк ХВС")

    if first.products:
        assert repeat.products, "карточки не должны исчезать из-за защиты от повторов"


# ---------------------------------------------------------------------------
# Д4. Непроверенный текст модели не покидает агента
# ---------------------------------------------------------------------------


class _FabricatingLLM(_SilentLLM):
    """Модель выдумывает артикул и цену, которых нет в каталоге."""

    text = (
        "Веста Трейдинг, AI-консультант на связи. По вашему запросу подойдёт "
        "счётчик воды холодной механический 15 мм. Артикул: VW-015-M. "
        "Цена: 2 890 руб. Наличие: есть в наличии, 4 шт."
    )

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        self.record_completion(self.text)
        return LLMResult(content=self.text, llm_used=True)


def test_d4_ungrounded_answer_never_leaves_the_consultant() -> None:
    """A23, B18: выдуманные артикул и цена уходили покупателю.

    Проверка достоверности срабатывала, но ветка подмены выполнялась только
    при наличии карточек. Когда подставить было нечего, признанный
    недостоверным текст оставался в ответе — и помечался ``deterministic``.
    Дыру закрываем в источнике: недостоверный текст агент не отдаёт.
    """
    from app.agents.consultant import ConsultantAgent
    from app.models import SessionState

    agent = ConsultantAgent(llm_client=_FabricatingLLM())
    result = agent.respond(
        "Нужен механический счётчик горячей воды 15 мм без датчика",
        SessionState(session_id="d4"),
        [],
        [],
    )

    if result.llm_used:
        assert not result.grounded, "выдуманный артикул обязан быть отклонён"
        assert result.answer == "", "недостоверный текст не покидает агента"


def test_d4_fabricated_sku_does_not_reach_the_customer() -> None:
    """Тот же дефект на уровне диалога: артикула вне каталога в ответе нет."""
    bot = ChatOrchestrator(llm_client=_FabricatingLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat(
        "d4-dialog",
        "Нужны счётчики на квартиру — горячая и холодная вода, механические 15 мм.",
    )

    assert "VW-015-M" not in response.answer
    assert "2 890" not in response.answer


# ---------------------------------------------------------------------------
# Д12. Обрыв по лимиту токенов
# ---------------------------------------------------------------------------


class _TruncatingLLM(_SilentLLM):
    """Провайдер вернул ответ, оборванный по ``max_tokens``.

    Заглушка отдаёт то же, что настоящий клиент после проверки ``finish_reason``:
    подменять сам проверяемый метод нельзя, иначе тест проверял бы заглушку.
    """

    def complete(self, *_args: Any, **_kwargs: Any) -> LLMResult:
        return LLMResult(
            content=None,
            llm_used=False,
            fallback_reason="llm output truncated by max_tokens",
            finish_reason="length",
        )


def test_d12_truncated_completion_is_rejected_by_the_transport(monkeypatch) -> None:
    """C06, C18: ответы уходили оборванными на «Кон…» и «— труб PPR и армиров».

    ``finish_reason`` не проверялся нигде в модуле, а обрезанный текст проходит
    проверки достоверности: выдуманных фактов в нём нет — в нём нет конца.
    Проверяется настоящий транспорт с подменённым HTTP-слоем.
    """
    import httpx

    from app import openrouter_client as client_module
    from app.config import get_settings

    endpoint_response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://llm.test/v1/chat/completions"),
        json={
            "choices": [
                {
                    "message": {"content": "Здравствуйте! Помогу с подбором: кон"},
                    "finish_reason": "length",
                }
            ],
            "usage": {},
        },
    )

    class _FakeHTTP:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return endpoint_response

    monkeypatch.setattr(client_module.httpx, "Client", lambda **_kw: _FakeHTTP())

    settings = get_settings().model_copy(
        update={
            "llm_provider": "openrouter",
            "openrouter_api_key": "test-key",
            "openrouter_model": "test/model",
            "llm_max_retries": 0,
            "llm_retry_delay_seconds": 0.0,
        }
    )
    client = OpenRouterClient(settings=settings)
    client.begin_turn_recording()

    result = client.complete(agent="test", messages=[{"role": "user", "content": "hi"}])

    assert result.truncated is True
    assert result.llm_used is False, "обрезанный вывод не считается пригодным"
    assert result.content is None
    assert not client.recorded_completions(), "обрывок не попадает в запись хода"


def test_d12_truncated_answer_does_not_reach_the_customer() -> None:
    """На уровне диалога обрыв заменяется детерминированным ответом."""
    bot = ChatOrchestrator(products=[], llm_client=_TruncatingLLM())

    response = bot.handle_chat("d12", "Привет, ты кто такой?")

    assert not response.answer.rstrip().endswith("кон")


# ---------------------------------------------------------------------------
# Д1. Операционные факты о компании
# ---------------------------------------------------------------------------


def test_d1_business_config_is_present_and_loaded() -> None:
    """A06…D17: конфига операционных фактов не существовало вовсе.

    ``BusinessFacts.is_empty`` был истинным, поэтому защитный слой считал
    выдуманным любой контакт, срок и график — включая настоящие, — и весь
    коммерческий пласт упирался в «это подтвердит менеджер».
    """
    from app.business_config import load_business_facts

    facts = load_business_facts()

    assert not facts.is_empty
    assert facts.branches, "адреса пунктов выдачи обязаны быть в конфиге"
    assert facts.phones, "телефоны точек должны быть известны guard'у"


def test_d1_hours_question_asks_for_the_city_first() -> None:
    """A09, A22: пунктов шестнадцать, «наши часы работы» без города бессмысленны."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("d1-city", "Здравствуйте, работаете в воскресенье?")

    assert "назовите ваш" in response.answer.lower()


def test_d1_city_answer_returns_to_the_same_topic() -> None:
    """Ответ на вопрос о городе продолжает ту же тему, а не уходит в подбор.

    «Я в Санкт-Петербурге» само по себе коммерческой темой не выглядит, и
    без этой связки покупатель получал «напишите, что нужно подобрать».
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("d1-follow", "Где можно самовывозом забрать? До скольки работаете?")
    response = bot.handle_chat("d1-follow", "Я в Санкт-Петербурге")

    assert "санкт-петербург" in response.answer.lower()
    assert "+7 (812)" in response.answer, "телефон точки обязан пережить guard"


def test_d1_delivery_outside_served_regions_is_answered_not_deflected() -> None:
    """A10: «доставка в Краснодар» умирала в круге запросов города.

    Доставка едет к покупателю, а не в наш пункт выдачи, поэтому город без
    филиала — не повод спрашивать, где он хочет забрать товар.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("d1-far", "Привет, доставка в Краснодар — есть?")

    assert "краснодар" in response.answer.lower()
    assert "транспортн" in response.answer.lower()


def test_d1_payment_question_gets_a_real_answer() -> None:
    """A11: покупатель трижды спрашивал способы оплаты и не узнал ни одного."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("d1-pay", "Какие способы оплаты есть для частных лиц?")

    lowered = response.answer.lower()
    assert "карт" in lowered and "налич" in lowered


def test_d1_drafted_sections_are_marked_for_owner_review() -> None:
    """Политики, составленные при разработке, помечены как черновик.

    Оплата, возврат, гарантия и сроки написаны не владельцем компании. Бот
    называет их покупателю, поэтому в конфиге должно быть видно, что именно
    ещё не вычитано.
    """
    from app.business_config import load_business_facts

    facts = load_business_facts()

    assert "payment" in facts.drafted_sections
    assert "returns" in facts.drafted_sections
    assert "warranty" in facts.drafted_sections


def test_d11_repeated_dead_end_result_changes_strategy() -> None:
    """A04, C01: «не вижу точного совпадения» повторялось ходами подряд.

    Тупиковый результат — не вопрос, поэтому под привязку к воронке он не
    попадал. Но повторить «ничего не нашёл» второй раз бессмысленно всегда:
    новой информации покупатель не получает ни в первый, ни во второй раз.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    answers = [
        bot.handle_chat("d11-dead", message).answer
        for message in [
            "Здравствуйте, у вас есть котёл Valtec Termax 4400 Duo, артикул VT-9981?",
            "Нет, я просто хочу посмотреть, есть ли такой котёл в наличии.",
            "Нет, я спрашиваю именно про этот котёл — есть он или нет?",
            "Так есть он у вас или нет?",
        ]
    ]

    signatures = [" ".join(answer.split())[-120:] for answer in answers]
    assert len(set(signatures)) > 1, "бот не должен повторять тупик дословно"


def test_d2_consent_request_is_not_redrawn_verbatim(bot: ChatOrchestrator) -> None:
    """A06, D14: круг не исчез, а сдвинулся на ход вперёд.

    Как только контакт перестал теряться, ход 3 стал верным — бот показал
    данные и попросил согласие. А ход 4 повторял ход 3 дословно: покупатель
    уже прочитал список и ждал действия, а не второй его копии.
    """
    bot.handle_chat("d2-consent", "Где мой заказ №148237? Телефон +7(999)123-45-67.")
    first = bot.handle_chat("d2-consent", "Передай менеджеру")
    second = bot.handle_chat(
        "d2-consent",
        "Оставьте телефон или email, пожалуйста, я передам менеджеру.",
    )

    assert first.answer != second.answer
    assert "подтверждаю передачу" in second.answer.lower()
    assert len(second.answer) < len(first.answer), "второй раз — короче, а не копия"


# ---------------------------------------------------------------------------
# Д6. Отрицание переворачивалось в требование
# ---------------------------------------------------------------------------


def test_d6_refusing_hot_water_does_not_request_a_two_circuit_boiler() -> None:
    """C25, B18: «Только для отопления. ГВС не нужна» давало «двухконтурный».

    Слот выводился по вхождению подстроки «гвс», а проверка отрицания стояла
    ниже и правила другой слот. Два источника истины об одном факте
    расходились, и поиск не находил ничего.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("d6", "Нужен газовый котёл для дома 100 квадратов")
    response = bot.handle_chat("d6", "Да, только для отопления. ГВС не нужна.")

    slots = response.debug["slots"]
    assert slots.get("needs_hot_water") is False
    assert slots.get("contours") == "одноконтурный"


def test_d6_asking_for_hot_water_still_requests_two_circuits() -> None:
    """Обратная сторона: положительное упоминание ГВС должно работать."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("d6-pos", "Нужен газовый котёл для дома 120 квадратов")
    response = bot.handle_chat("d6-pos", "Да, нужна горячая вода прямо от котла.")

    assert response.debug["slots"].get("contours") == "двухконтурный"


def test_d6_refused_requirement_does_not_reach_the_manager_summary() -> None:
    """Тот же дефект уезжал в CRM: «ГВС не нужна» → «горячая вода/ГВС»."""
    from app.agents.handoff import HandoffAgent

    agent = HandoffAgent()

    assert "горячая вода/ГВС" not in agent._extract_key_requirements(
        "Только для отопления. ГВС не нужна."
    )
    assert "горячая вода/ГВС" in agent._extract_key_requirements(
        "Нужна горячая вода от котла"
    )


def test_d6_rejected_category_does_not_win_routing() -> None:
    """B13, B20: «мне нужны радиаторы, не трубы» уводило в трубы.

    Ветка маршрутизации ловила «труб» без проверки отрицания — в отличие от
    соседних веток того же метода.
    """
    from app.agents.intent_router import IntentRouterAgent
    from app.agents.utils import normalize_text

    router = IntentRouterAgent(llm_client=None)

    rejected, _ = router._detect_category(
        normalize_text("Нет, мне нужны радиаторы. Не трубы и краны.")
    )
    wanted, _ = router._detect_category(normalize_text("Нужна труба PPR 25 для стояка"))

    assert rejected != "pipes"
    assert wanted == "pipes"


# ---------------------------------------------------------------------------
# Д7. Бытовые числа в технических слотах
# ---------------------------------------------------------------------------


def test_d7_time_of_day_does_not_become_a_budget() -> None:
    """C03: «Нужно до 18:00» давало «бюджет до: 1800.0» в заявке менеджеру."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat(
        "d7-time",
        "Заеду на склад сегодня до 18:00 — отложите, пожалуйста, товар.",
    )

    slots = response.debug["slots"]
    assert slots.get("max_price") is None


def test_d7_floor_number_does_not_become_a_diameter() -> None:
    """B13: «живу на 22 этаже» давало «Диаметр 22 мм записал»."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat(
        "d7-floor",
        "Живу на 22 этаже в новостройке. Какие радиаторы подходят для такого давления?",
    )

    assert response.debug["slots"].get("diameter_mm") is None


def test_d7_real_budget_and_diameter_still_parse() -> None:
    """Обратная сторона: настоящие цена и диаметр обязаны разбираться."""
    from app.agents.numeric_semantics import number_has_domestic_role

    assert number_has_domestic_role("бюджет до 20 000", 20000) is None
    assert number_has_domestic_role("диаметр 22 мм", 22) is None
    assert number_has_domestic_role("на 22 этаже", 22) == "этаж"
    assert number_has_domestic_role("ИНН 7714123456", 7714123456) == "реквизит"


# ---------------------------------------------------------------------------
# Д9. Ложное «такой позиции в каталоге нет»
# ---------------------------------------------------------------------------


def test_d9_place_names_and_units_are_not_reported_as_missing_models() -> None:
    """D19, A13, B21, A21: оговорка срабатывала на топонимах и единицах.

    «Точной позиции „Северной, Осетии“ в каталоге не нахожу» подрывает доверие
    к остальному ответу сильнее, чем помогает честность про марку.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()
    search = bot.search_agent

    assert search.unknown_identity_tokens("Дом, в Северной Осетии, потолки 2,7 м") == []
    assert search.unknown_identity_tokens("Купил у вас в Москве, 8 месяцев назад") == []
    assert search.unknown_identity_tokens("артикул VP1620.3.200 — цена 78 RUB") == []
    assert search.unknown_identity_tokens("Резьба — ВР-НР. На кого перейти?") == []


def test_d9_cyrillic_brand_spelling_matches_the_catalogue() -> None:
    """A17: «Ардерия» объявлялась отсутствующей при наличии Arderia в каталоге."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    assert bot.search_agent.unknown_identity_tokens("У вас Ардерия SB24 — 24 кВт") == []


def test_d9_genuinely_absent_model_is_still_reported() -> None:
    """A02: ради этого случая оговорка и писалась — она обязана сохраниться."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    unknown = bot.search_agent.unknown_identity_tokens(
        "Есть ли в наличии полотенцесушитель Сунержа Модус 800х500?"
    )

    assert "Сунержа" in unknown


# ---------------------------------------------------------------------------
# Д10. Поиск по названию без проверки назначения
# ---------------------------------------------------------------------------


def test_d10_rejected_purpose_is_not_shown_again() -> None:
    """D09: на «не для кухни, а для радиатора» приходил тот же список.

    Поиск по названию не проверяет назначение, поэтому уточнение покупателя —
    единственный доступный фильтр. Повторить отвергнутый список — худший
    из возможных исходов.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat(
        "d10",
        "Вы снова показываете смесители для кухни, а мне нужен для радиатора.",
    )

    assert "д/кухни" not in response.answer


def test_d10_ordinary_request_is_not_filtered_away() -> None:
    """Обратная сторона: обычный запрос обязан показывать позиции."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("d10-ok", "Нужен смеситель для кухни")

    assert response.products


def test_d10_layperson_misnomer_reaches_the_right_catalogue_group() -> None:
    """D09: «смеситель для батареи» — это радиаторный клапан, и он в каталоге есть.

    Запрос уходил в семейство смесителей, где подбор идёт по названию, вместо
    радиаторной арматуры, где он идёт с проверкой параметров.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat(
        "d10-misnomer",
        "Мне сантехник посоветовал смеситель для батареи, помогите разобраться.",
    )

    assert response.debug["category"] == "radiator_fittings"


# ---------------------------------------------------------------------------
# Актуальность фактов: конфиг — снимок, а не состояние компании
# ---------------------------------------------------------------------------


def test_facts_answer_points_to_the_site_as_the_current_source() -> None:
    """Часы, адреса и тарифы устаревают — ответ обязан назвать источник.

    Без этого бот выдаёт снимок конфигурации за текущее состояние компании:
    точки открываются и закрываются, пороги бесплатной доставки меняются.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("facts-site", "Где можно самовывозом забрать?")
    response = bot.handle_chat("facts-site", "Я в Санкт-Петербурге")

    lowered = response.answer.lower()
    assert "vestatrade.ru" in lowered
    assert "могли измениться" in lowered


def test_freshness_caveat_is_stated_once_per_conversation() -> None:
    """Оговорка не должна стать новым повтором.

    В каждом ответе она превратилась бы ровно в то буксование, которое мы
    убирали волной 1.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("facts-once", "Где можно самовывозом забрать?")
    first = bot.handle_chat("facts-once", "Я в Санкт-Петербурге")
    second = bot.handle_chat("facts-once", "А в субботу до скольки?")

    assert "могли измениться" in first.answer
    assert "могли измениться" not in second.answer


def test_follow_up_about_hours_is_answered_not_escalated() -> None:
    """«А в субботу до скольки?» — уточнение, а не повтор темы.

    Счётчик повторов гасил ответ из фактов, и покупатель вместо режима работы
    получал предложение передать вопрос менеджеру.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("facts-follow", "До скольки работаете?")
    bot.handle_chat("facts-follow", "Самара")
    response = bot.handle_chat("facts-follow", "А в субботу до скольки?")

    assert "Самара" in response.answer
    assert "передай менеджеру" not in response.answer.lower()


def test_drafted_policy_carries_a_pointer_to_full_terms() -> None:
    """Условия, составленные при разработке, не подаются как окончательные.

    Пока раздел числится черновиком, покупатель получает ссылку на полные
    условия. Как только владелец убирает раздел из ``drafted_sections``,
    оговорка исчезает сама — без правки кода.
    """
    from app.business_config import load_business_facts

    facts = load_business_facts()

    assert facts.draft_caveat("payment"), "черновой раздел обязан нести источник"
    assert facts.draft_caveat("branches") is None, "подтверждённый — не обязан"


def test_policy_answer_does_not_repeat_itself() -> None:
    """Оговорка не должна дублировать то, что уже сказано в тексте политики."""
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    response = bot.handle_chat("facts-dup", "Какие способы оплаты есть?")

    assert response.answer.lower().count("подтверждает менеджер") <= 1


def test_gas_markers_require_word_boundaries() -> None:
    """Живой прогон 24.08: «магазин» совпадал с «газ», «Самара» — с «сам».

    Маркеры сравнивались подстрокой, поэтому вопрос про магазин рядом со
    словом «инструкция» получал отказ по газоопасным работам. Дефект был и в
    сборке 23.08; всплыл, когда бот начал спрашивать город и покупатели стали
    отвечать «Самара».
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    innocent = bot.handle_chat(
        "gas-word", "Ну вот, Самара. Где там ваш магазин? Адрес и часы работы."
    )
    dangerous = bot.handle_chat(
        "gas-word-2", "Можно ли самому подключить газовый котёл? Если да — как?"
    )

    assert _GAS_REFUSAL_MARK not in innocent.answer
    assert _GAS_REFUSAL_MARK in dangerous.answer


def test_city_question_keeps_its_thread_when_customer_pushes() -> None:
    """Живой прогон 24.08 (A09): покупатель дожимал, не назвав город.

    Такая реплика не попадала ни в одну коммерческую тему и уходила в
    small talk — бот терял нить собственного вопроса.
    """
    bot = ChatOrchestrator(llm_client=_SilentLLM())
    bot._ensure_products_loaded()

    bot.handle_chat("city-thread", "Где ваш магазин и когда работаете?")
    response = bot.handle_chat("city-thread", "Ну вот, а где конкретно ближайший ко мне?")

    assert "город" in response.answer.lower()
