from __future__ import annotations

import logging
import re
from typing import Any

from app.config import PROJECT_ROOT, Settings, get_settings
from app.docs_loader import load_docs_for_products
from app.feed_loader import FeedLoader
from app.models import (
    ChatProductSummary,
    ChatResponse,
    IntentResult,
    Product,
    ProductCard,
    SearchQuery,
    SessionState,
)
from app.openrouter_client import OpenRouterClient
from app.session_store import InMemorySessionStore

from .feed_search import FeedSearchAgent
from .guardrails import GuardrailsAgent
from .handoff import HandoffAgent
from .intent_router import IntentRouterAgent
from .product_card import ProductCardAgent
from .ranking import RankingAgent
from .response_composer import ResponseComposerAgent
from .slot_filling import SlotFillingAgent
from .utils import collapse_sku_spaces, merge_slots, normalize_sku as normalize_sku_token, normalize_text


logger = logging.getLogger(__name__)


COMPANION_HINTS: dict[str, str] = {
    "boilers": (
        "Кстати, к котлу обычно берут ещё циркуляционный насос, группу безопасности и трубы "
        "для обвязки. Могу подобрать — напишите, например, «насос к нему»."
    ),
    "pumps": (
        "Кстати, к насосу часто ставят два шаровых крана с американкой — так его можно снять, "
        "не сливая систему. Если нужно, напишите «кран с американкой»."
    ),
    "pipes": (
        "Кстати, к трубам обычно нужны краны и переходники. Если нужно, напишите, "
        "например, «кран 1/2»."
    ),
    "sewer": (
        "Кстати, к канализационной трубе часто берут отводы и муфты того же диаметра. "
        "Если нужно, напишите, например, «отвод 50»."
    ),
    "radiator_fittings": (
        "Кстати, если нужна регулировка температуры, к радиаторному клапану берут "
        "термоголовку — могу подобрать."
    ),
}

GAS_VS_ELECTRIC_CONSULT = (
    "Главное отличие — топливо и эксплуатация.\n"
    "Газовый: дешевле в эксплуатации (газ выгоднее электричества), мощный, тянет большие "
    "площади. Минусы — нужен подведённый газ, дымоход и согласование, монтаж дороже.\n"
    "Электрический: проще и дешевле в установке, тихий, без дымохода и согласований. "
    "Минусы — дороже по счетам, а для большой площади часто нужно 380 В.\n"
    "Если коротко: есть газ — обычно берут газовый ради экономии; газа нет или площадь "
    "небольшая — электрический.\n"
    "Подскажите: газ подведён и какая площадь? Подберу конкретные варианты из каталога."
)

ONE_VS_TWO_CONTOUR_CONSULT = (
    "Одноконтурный котёл работает только на отопление — для горячей воды к нему нужен "
    "отдельный бойлер. Двухконтурный даёт и отопление, и горячую воду сразу, бойлер не "
    "нужен. Если горячую воду из крана хотите от котла — берите двухконтурный; если ГВС "
    "не нужна или есть отдельный бойлер — хватит одноконтурного.\n"
    "Нужна горячая вода от котла? И какая площадь? Подберу варианты."
)

PIPE_TYPES_CONSULT = (
    "Полипропиленовые трубы бывают трёх исполнений. Обычная PN20 — для холодной воды и "
    "несложных задач. Армированная стекловолокном (PP-FIBER) и алюминием (PP-ALUX) меньше "
    "расширяются от горячей воды и держат более высокое давление — их берут для отопления и "
    "горячего водоснабжения. Для тёплого пола и стояков чаще берут армированные.\n"
    "Скажите назначение (отопление, горячая или холодная вода) и диаметр — подберу."
)

# Слова, означающие, что клиент уже назвал конкретный тип товара — тогда воронку
# по системе не запускаем, ведём обычный подбор.
SPECIFIC_PRODUCT_WORDS = [
    "котел",
    "котёл",
    "котл",
    "бойлер",
    "насос",
    "помпа",
    "нсос",
    "труба",
    "трубы",
    "трубу",
    "кран",
    "вентиль",
    "американк",
    "радиатор",
    "батаре",
    "термоголов",
    "термостат",
    "фитинг",
    "отвод",
    "тройник",
    "муфта",
    "угольник",
    "клапан",
    "коллектор",
]

# Воронки по «системам»: клиент называет систему/проект целиком, а не товар.
# Объясняем, из чего состоит система, и предлагаем сузить до конкретной категории.
HEATING_FUNNEL = (
    "Система отопления — это не только котёл: ещё циркуляционный насос, трубы, "
    "радиаторы и запорно-регулирующая арматура. С чего начнём — котёл, насос, "
    "трубы или радиаторная арматура?"
)
WATER_SUPPLY_FUNNEL = (
    "Водоснабжение складывается из нескольких частей: насос (если вода из скважины "
    "или колодца), трубы, краны и фитинги. Что подобрать в первую очередь — "
    "насос, трубы или краны?"
)
WARM_FLOOR_FUNNEL = (
    "Тёплый пол — это трубы, циркуляционный насос и запорно-регулирующая арматура "
    "(плюс коллектор и автоматика, которых может не быть в каталоге). Что подобрать "
    "из наличия — трубы или насос?"
)
GENERAL_FUNNEL = (
    "Подскажите, что именно нужно — в каталоге Vesta Trading есть котлы, насосы, "
    "трубы, краны, канализация и радиаторная арматура. С чего начнём?"
)


class ChatOrchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        products: list[Product] | None = None,
        llm_client: OpenRouterClient | None = None,
        session_store: InMemorySessionStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.feed_loader = FeedLoader(self.settings)
        self.llm_client = llm_client or OpenRouterClient(self.settings)
        self.sessions = session_store or InMemorySessionStore()
        self.intent_router = IntentRouterAgent(self.llm_client)
        self.slot_filling = SlotFillingAgent()
        self.search_agent = FeedSearchAgent(products or [])
        self.ranking_agent = RankingAgent()
        self.card_agent = ProductCardAgent()
        self.guardrails = GuardrailsAgent()
        self.composer = ResponseComposerAgent(self.llm_client)
        self.handoff = HandoffAgent()
        self.products_loaded_from = "injected" if products is not None else "none"
        self.docs_attached = 0
        if products:
            self.docs_attached = load_docs_for_products(products, self._docs_dirs())

    def _docs_dirs(self) -> list[Any]:
        return [self.settings.product_docs_dir, PROJECT_ROOT / "data"]

    def reload_products(self, refresh: bool = True) -> tuple[int, str]:
        products, source = self.feed_loader.load_products(refresh=refresh)
        self.docs_attached = load_docs_for_products(products, self._docs_dirs())
        self.search_agent.set_products(products)
        self.products_loaded_from = source
        return len(products), source

    def handle_chat(self, session_id: str, message: str) -> ChatResponse:
        session = self.sessions.get(session_id)
        session.topic_changed = False
        session.slots.pop("fallback_after_repeat", None)
        self.composer.reset_usage()
        self.composer.set_history(session.history)
        last_summary: str | None = None
        docs_excerpt: str | None = None
        if session.last_products:
            last_card = session.last_products[0]
            last_summary = (
                f"{last_card.sku} — {last_card.name}, {last_card.price:g} {last_card.currency}, "
                f"наличие: {last_card.stock_status}"
            )
            last_product = self._find_product_by_sku(last_card.sku)
            if last_product and last_product.docs_text:
                docs_excerpt = last_product.docs_text[:700]
        self.composer.set_state(session.category, session.slots, last_summary, docs_excerpt)
        agents_used: list[str] = []

        intent = self.intent_router.route(message, session)
        agents_used.append("IntentRouterAgent")

        if self._is_pending_continuation(intent, session, message):
            self._restore_pending_intent(intent, session)

        # «этот насос», «тот что ты предложил» — это вопрос про показанное, а не смена
        # темы; иначе topic-change стёр бы контекст ещё до ответа агента.
        if session.last_products and self._references_shown_products(message):
            intent.is_topic_change = False

        # Вопрос про уже показанный товар («что входит в комплект», «проверь
        # документацию», «есть ли там насос») — это запрос к карточке, а не новый
        # подбор. Перенаправляем в комплектацию, иначе уходит в LLM/подбор насоса.
        if self._looks_like_card_question(message, session):
            intent.intent_type = "complectation"
            if session.category:
                intent.category = session.category
            intent.is_topic_change = False

        if intent.is_topic_change:
            session.slots = {}
            session.last_products = []
            session.topic_changed = True
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_complectation_parts = []
            session.question_repeats = 0

        if self._should_restart_category_context(message, intent, session):
            session.slots = {}
            session.last_products = []

        if self._wants_manager_handoff(message):
            summary = self.handoff.build_summary(message, session)
            recorded = self.handoff.record(summary, session.session_id, self.settings.handoff_log_path)
            answer = self.handoff.compose_user_confirmation(summary, recorded)
            agents_used.append("HandoffAgent")
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], True, intent, session, agents_used)

        meta_answer = self._maybe_meta_question(message)
        if meta_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, meta_answer)
            self.sessions.save(session)
            return self._response(session_id, meta_answer, session.last_products, False, intent, session, agents_used)

        if intent.intent_type == "link_request":
            selected_index = self._select_ordinal_index(message, session.last_products)
            answer = self.composer.compose_link_answer(session.last_products, selected_index)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "link", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        term_answer = self._maybe_term_explanation(message)
        if term_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(term_answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        engineering_risk = self._maybe_engineering_risk_answer(message)
        if engineering_risk:
            agents_used.append("GuardrailsAgent")
            agents_used.append("HandoffAgent")
            self._append_history(session, message, engineering_risk)
            self.sessions.save(session)
            return self._response(session_id, engineering_risk, [], True, intent, session, agents_used)

        consultation = self._maybe_consultation_answer(message, intent, session)
        if consultation:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, consultation)
            self.sessions.save(session)
            return self._response(session_id, consultation, [], False, intent, session, agents_used)

        # Открытый вопрос «что входит в полную комплектацию / ответь по паспорту» —
        # отдаём диалоговому агенту: у него в контексте текст паспорта, он ответит по
        # нему и без дословных повторов (детерминированный путь лишь зачитывал встроенные
        # узлы и отфутболивал «смотрите в паспорте»). Конкретные детали остаются в правилах.
        if session.last_products and self._is_open_complectation_question(message):
            context_response = self._answer_from_context(message, intent, session, agents_used)
            if context_response is not None:
                self.sessions.save(session)
                return context_response

        comparison_answer = self._maybe_comparison_answer(message, session)
        if comparison_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(comparison_answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id, answer, session.last_products, False, intent, session, agents_used
            )

        analogs_response = self._maybe_analogs_response(message, intent, session, agents_used)
        if analogs_response is not None:
            self.sessions.save(session)
            return analogs_response

        choose_answer = self._maybe_choose_one_answer(message, session)
        if choose_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(choose_answer, "link", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, session.last_products[:1], False, intent, session, agents_used)

        why_answer = self._maybe_why_explanation(message, session)
        if why_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(why_answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, session.last_products, False, intent, session, agents_used)

        if (
            (self._looks_like_confirmation(message) or self._looks_like_affirmation(message))
            and session.last_products
            and intent.category == "other"
        ):
            cards = session.last_products
            answer = self.composer.compose_confirm_last(cards)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "link", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, cards, False, intent, session, agents_used)

        if session.slots.get("pending_tradeoff"):
            insulation = self._extract_insulation_hint(message)
            if insulation:
                session.slots.pop("pending_tradeoff", None)
                session.pending_question = None
                session.pending_intent_type = None
                answer = self._compose_tradeoff_followup(insulation, message)
                agents_used.append("ResponseComposerAgent")
                agents_used.append("GuardrailsAgent")
                answer = self._guard_composed_answer(answer, "generic", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, [], False, intent, session, agents_used)

        # Свободный вопрос про уже показанные товары — отвечает LLM-агент по их карточкам.
        # Стоит после подтверждений/tradeoff, но до общих fallback'ов (small talk /
        # unknown / повторный поиск), которые иначе «съели» бы вопрос или зациклили список.
        if self._is_contextual_followup(message, intent, session):
            context_response = self._answer_from_context(message, intent, session, agents_used)
            if context_response is not None:
                self.sessions.save(session)
                return context_response

        # Клиент назвал систему/проект целиком («система отопления», «водоснабжение»,
        # «сантехнику в дом») без конкретного товара — не подбираем наугад, а объясняем
        # состав системы и сужаем до категории. Это и есть «воронка»: от общего к частному.
        # Стоит до small talk / unknown, чтобы системные фразы получали детерминированную
        # воронку, а не свободный ответ LLM с риском выдумать характеристики.
        scope_funnel = self._maybe_scope_funnel(message, intent, session)
        if scope_funnel:
            answer = scope_funnel
            agents_used.append("ResponseComposerAgent")
            session.pending_question = scope_funnel
            session.pending_intent_type = "broad_category"
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if intent.intent_type == "small_talk" and intent.category == "other":
            answer = self.composer.compose_small_talk(message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "small_talk", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if self._is_non_product_message(intent):
            sink_question = self._maybe_sink_question(message)
            if sink_question:
                session.pending_question = sink_question
                session.pending_intent_type = "broad_category"
                answer = self.composer.compose_clarification(sink_question, user_message=message)
                agents_used.append("ResponseComposerAgent")
                answer = self._guard_composed_answer(answer, "clarification", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, [], False, intent, session, agents_used)
            if (
                self._looks_like_confirmation(message) or self._looks_like_affirmation(message)
            ) and session.last_products:
                cards = session.last_products
                answer = self.composer.compose_confirm_last(cards)
                agents_used.append("ResponseComposerAgent")
                answer = self._guard_composed_answer(answer, "link", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, cards, False, intent, session, agents_used)
            if session.pending_question:
                answer = self.composer.compose_pending_repeat(session.pending_question)
                agents_used.append("ResponseComposerAgent")
                answer = self._guard_composed_answer(answer, "clarification", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, [], False, intent, session, agents_used)
            answer = self.composer.compose_unknown(user_message=message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "small_talk", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if intent.intent_type == "out_of_scope":
            answer = self.composer.compose_out_of_scope(user_message=message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "small_talk", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        boiler_warning = self._maybe_boiler_warning(message, intent, session)
        if boiler_warning:
            agents_used.append("ResponseComposerAgent")
            agents_used.append("GuardrailsAgent")
            answer = self._guard_composed_answer(boiler_warning, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        boiler_tradeoff = self._maybe_boiler_tradeoff(message, intent, session)
        if boiler_tradeoff:
            agents_used.append("ResponseComposerAgent")
            agents_used.append("GuardrailsAgent")
            answer = self._guard_composed_answer(boiler_tradeoff, "generic", agents_used)
            session.pending_question = "Какое утепление и нужна ли горячая вода?"
            session.pending_intent_type = "attribute_request"
            session.slots["pending_tradeoff"] = True
            session.category = "boilers"
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        slot_result = self.slot_filling.fill(message, intent, session)
        agents_used.append("SlotFillingAgent")
        session.slots = merge_slots(session.slots, slot_result.slots)
        session.category = intent.category if intent.category != "other" else session.category
        session.last_intent = intent.intent_type

        if intent.intent_type == "complectation" or session.pending_complectation_parts:
            response = self._handle_complectation(message, session, intent, agents_used)
            self.sessions.save(session)
            return response

        query = self._build_query(message, intent, session)
        direct_products: list[Product] = []
        if not session.pending_complectation_parts:
            direct_products = self.search_agent.search_by_name(message, query)

        if not direct_products and self._stock_or_link_without_context(intent, session, message):
            question = self._stock_clarification_question(intent)
            answer = self.composer.compose_clarification(question, user_message=message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "clarification", agents_used)
            session.pending_question = question
            session.pending_intent_type = intent.intent_type
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if slot_result.needs_clarification and slot_result.question and not direct_products:
            if session.pending_question == slot_result.question:
                session.question_repeats += 1
            else:
                session.question_repeats = 0
            if session.question_repeats < 2:
                answer = self.composer.compose_clarification(
                    slot_result.question,
                    small_talk=bool(intent.flags.get("small_talk")),
                    user_message=message,
                )
                agents_used.append("ResponseComposerAgent")
                answer = self._guard_composed_answer(answer, "clarification", agents_used)
                session.pending_question = slot_result.question
                session.pending_intent_type = intent.intent_type
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, [], False, intent, session, agents_used)
            # Один и тот же вопрос уже задавали дважды — меняем тактику:
            # показываем типовой вариант по текущим данным вместо зацикливания.
            session.slots["allow_basic_option"] = True
            session.slots["fallback_after_repeat"] = True
            query = self._build_query(message, intent, session)

        # Защита от «вываливания» случайных товаров: если категория не определена и
        # нет ни артикула, ни бренда, ни конкретного параметра — открытый поиск выдаёт
        # шум (угольники на «есть дом»). Вместо этого спрашиваем, что подбираем.
        if not direct_products and not self._is_searchable(query):
            session.slots["scope_funnel"] = "general"
            session.pending_question = GENERAL_FUNNEL
            session.pending_intent_type = "broad_category"
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, GENERAL_FUNNEL)
            self.sessions.save(session)
            return self._response(session_id, GENERAL_FUNNEL, [], False, intent, session, agents_used)

        session.pending_question = None
        session.pending_intent_type = None
        session.question_repeats = 0

        agents_used.append("FeedSearchAgent")
        products = direct_products or self._safe_search(query)
        if not products:
            alternatives = self.search_agent.search_alternatives(query)
            alternatives = self._drop_underpowered_boilers(alternatives, query)
            if alternatives:
                ranked_alternatives = alternatives
                if query.cheap:
                    agents_used.append("RankingAgent")
                    ranked_alternatives = self.ranking_agent.rank(alternatives, query)
                agents_used.append("ProductCardAgent")
                cards = self.card_agent.build_cards(
                    ranked_alternatives,
                    query,
                    limit=self._card_limit(query),
                )
                agents_used.append("GuardrailsAgent")
                guard = self.guardrails.validate_cards(cards, ranked_alternatives, query)
                if guard.ok and cards:
                    agents_used.append("ResponseComposerAgent")
                    if query.slots.get("choose_one"):
                        answer = self.composer.compose_choose_one(
                            cards[0],
                            query,
                            alternative=cards[1] if len(cards) > 1 else None,
                        )
                        cards = cards[:1]
                    else:
                        answer = self.composer.compose_products(
                            cards,
                            query,
                            note=self.composer.compose_alternative_note(query),
                        )
                    answer = self._guard_composed_answer(answer, "products", agents_used)
                    answer = self._append_companion_hint(answer, session, query.category)
                    session.last_products = cards
                    self._append_history(session, message, answer)
                    self.sessions.save(session)
                    return self._response(session_id, answer, cards, False, intent, session, agents_used)

            answer = self.composer.compose_no_match(query)
            answer = self._guard_composed_answer(answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            agents_used.append("ResponseComposerAgent")
            return self._response(session_id, answer, [], True, intent, session, agents_used)

        agents_used.append("RankingAgent")
        ranked = self.ranking_agent.rank(products, query)
        if query.cheap and session.last_products:
            min_previous_price = min(card.price for card in session.last_products)
            cheaper_ranked = [
                product
                for product in ranked
                if product.price is not None and product.price < min_previous_price
            ]
            if not cheaper_ranked:
                agents_used.append("ResponseComposerAgent")
                answer = self.composer.compose_no_cheaper(session.last_products)
                answer = self._guard_composed_answer(answer, "generic", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(session_id, answer, [], False, intent, session, agents_used)
            ranked = cheaper_ranked
        agents_used.append("ProductCardAgent")
        cards = self.card_agent.build_cards(ranked, query, limit=self._card_limit(query))

        agents_used.append("GuardrailsAgent")
        guard = self.guardrails.validate_cards(cards, ranked, query)
        if not guard.ok:
            summary = self.handoff.build_summary(message, session, missing=guard.issues, products=cards)
            answer = guard.safe_message or self.handoff.compose_answer(summary)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            agents_used.append("HandoffAgent")
            return self._response(session_id, answer, [], True, intent, session, agents_used)

        agents_used.append("ResponseComposerAgent")
        if query.slots.get("choose_one"):
            answer = self.composer.compose_choose_one(
                cards[0],
                query,
                alternative=cards[1] if len(cards) > 1 else None,
            )
            cards = cards[:1]
        else:
            answer = self.composer.compose_products(
                cards,
                query,
                note=self._compose_query_note(query),
            )
        answer = self._guard_composed_answer(answer, "products", agents_used)
        answer = self._append_companion_hint(answer, session, query.category)
        session.last_products = cards
        self._append_history(session, message, answer)
        self.sessions.save(session)
        return self._response(session_id, answer, cards, False, intent, session, agents_used)

    def _handle_complectation(
        self,
        message: str,
        session: SessionState,
        intent: IntentResult,
        agents_used: list[str],
    ) -> ChatResponse:
        requested_parts = self._requested_parts(message) or session.pending_complectation_parts
        if not requested_parts:
            requested_parts = ["комплектация"]

        sku_from_message = session.slots.get("sku")
        target_product: Product | None = None
        target_card: ProductCard | None = None
        if sku_from_message:
            target_product = self._find_product_by_sku(sku_from_message)
        if not target_product and session.last_products:
            # Если показано несколько — вопрос о комплекте относится к основной
            # (первой) позиции; не переспрашиваем «по какому товару».
            target_card = session.last_products[0]
            target_product = self._find_product_by_sku(target_card.sku)

        if not target_product:
            if session.pending_complectation_parts:
                summary = self.handoff.build_summary(
                    message,
                    session,
                    missing=["нет артикула/модели для проверки комплектации в фиде"],
                )
                answer = (
                    "Без артикула или модели котла не подтвержу обвязку/комплектацию по данным "
                    "фида. Не буду угадывать узлы системы — лучше передам менеджеру с краткой "
                    "сводкой.\n" + self.handoff.compose_answer(summary)
                )
                agents_used.append("HandoffAgent")
                session.pending_question = None
                session.pending_intent_type = None
                session.pending_complectation_parts = []
                self._append_history(session, message, answer)
                return self._response(session.session_id, answer, [], True, intent, session, agents_used)
            question = self._compose_complectation_question(message, requested_parts)
            session.pending_question = question
            session.pending_intent_type = "complectation"
            session.pending_complectation_parts = requested_parts
            answer = self.composer.compose_clarification(question, user_message=message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "clarification", agents_used)
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], False, intent, session, agents_used)

        if not target_card:
            target_card = self.card_agent.build_card(
                target_product,
                SearchQuery(
                    original_text=message,
                    category=session.category or "other",
                    slots=session.slots,
                ),
            )
        if not target_card:
            summary = self.handoff.build_summary(message, session, missing=["нет полной карточки товара"])
            answer = self.handoff.compose_answer(summary)
            agents_used.append("HandoffAgent")
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], True, intent, session, agents_used)

        # Открытый вопрос «что входит в комплект / проверь документацию» — читаем
        # карточку и перечисляем встроенные компоненты, а не отказываем.
        if requested_parts == ["комплектация"]:
            components = self.guardrails.list_builtin_components(target_product)
            agents_used.append("GuardrailsAgent")
            agents_used.append("ResponseComposerAgent")
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_complectation_parts = []
            answer = self.composer.compose_builtin_components(target_card, components)
            answer = self._guard_composed_answer(answer, "complectation", agents_used)
            session.last_products = [target_card]
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [target_card], False, intent, session, agents_used)

        guard = self.guardrails.validate_complectation_answer(target_product, requested_parts)
        agents_used.append("GuardrailsAgent")
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_complectation_parts = []
        if not guard.ok:
            answer = guard.safe_message or (
                "Не вижу подтверждения комплектации в данных фида. Лучше проверить карточку/документацию или передать вопрос менеджеру."
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], True, intent, session, agents_used)

        answer = self.composer.compose_complectation_confirmed(target_card, requested_parts)
        answer = self._guard_composed_answer(answer, "complectation", agents_used)
        session.last_products = [target_card]
        self._append_history(session, message, answer)
        return self._response(session.session_id, answer, [target_card], False, intent, session, agents_used)

    def _safe_search(self, query: SearchQuery) -> list[Product]:
        if not self.search_agent.products:
            try:
                self.reload_products(refresh=False)
            except Exception as exc:
                logger.exception("Cannot load products for search: %s", exc)
                return []
        return self.search_agent.search(query)

    def _build_query(self, message: str, intent: IntentResult, session: SessionState) -> SearchQuery:
        return SearchQuery(
            original_text=message,
            category=intent.category if intent.category != "other" else session.category or "other",
            slots=session.slots,
            sku=session.slots.get("sku"),
            brand=session.slots.get("brand"),
            cheap=bool(session.slots.get("cheap") or intent.flags.get("cheap")),
            in_stock_only=bool(session.slots.get("in_stock") or intent.flags.get("in_stock")),
        )

    def _is_searchable(self, query: SearchQuery) -> bool:
        """True when the query carries enough signal to run a meaningful search.

        A bare category=other query with no SKU, brand or constraining slot would
        otherwise fuzzy-match the whole feed and surface noise. We only search when
        there is at least one concrete anchor.
        """
        if query.sku or query.brand:
            return True
        if query.category and query.category != "other":
            return True
        meaningful_slots = {
            "diameter_mm",
            "size_inch",
            "head_m",
            "mounting_length_mm",
            "connection_size",
            "old_model",
            "power_kw",
            "area_m2",
            "boiler_type",
            "pump_type",
            "pump_use",
            "element_type",
            "sewer_scope",
            "pipe_purpose",
            "length_mm",
            "application",
            "cheap",
            "in_stock",
        }
        return any(
            key in meaningful_slots and value not in (None, "", [], {})
            for key, value in query.slots.items()
        )

    def _is_non_product_message(self, intent: IntentResult) -> bool:
        if intent.intent_type != "unknown" or intent.category != "other":
            return False
        actionable_slots = {"sku", "brand", "cheap", "in_stock", "diameter_mm", "area_m2", "power_kw"}
        if actionable_slots.intersection(intent.slots):
            return False
        if any(intent.flags.get(flag) for flag in ["cheap", "in_stock"]):
            return False
        return True

    def _select_ordinal_index(self, message: str, cards: list[ProductCard]) -> int | None:
        if not cards:
            return None
        text = normalize_text(message)
        ordinals = [
            (["первый", "первого", "1-й", "1й", " 1 ", "первое"], 0),
            (["второй", "второго", "2-й", "2й", " 2 ", "второе"], 1),
            (["третий", "третьего", "3-й", "3й", " 3 ", "третье"], 2),
        ]
        padded = f" {text} "
        for markers, index in ordinals:
            if any(marker in padded for marker in markers):
                if index < len(cards):
                    return index
        for card in cards:
            if normalize_sku_token(card.sku) and normalize_sku_token(card.sku) in normalize_sku_token(message):
                return cards.index(card)
        return None

    def _maybe_why_explanation(self, message: str, session: SessionState) -> str | None:
        text = normalize_text(message)
        if "почему" not in text and "зачем" not in text:
            return None
        if not session.last_products:
            return None
        slot_summary: list[str] = []
        for key, label in [
            ("pump_type", "тип насоса"),
            ("connection_size", "присоединение"),
            ("head_m", "напор"),
            ("mounting_length_mm", "монтажная длина"),
            ("old_model", "модель старого насоса"),
            ("boiler_type", "тип котла"),
            ("area_m2", "площадь"),
            ("size_inch", "размер"),
            ("diameter_mm", "диаметр"),
        ]:
            value = session.slots.get(key)
            if value not in (None, ""):
                slot_summary.append(f"{label}: {value}")
        details = ", ".join(slot_summary) or "ваши уточнения"
        skus = ", ".join(card.sku for card in session.last_products[:3])
        return (
            f"Потому что параметры из ваших уточнений совпадают с карточками товаров в фиде. "
            f"Использовал данные: {details}. Подходящие позиции из фида: {skus}."
        )

    def _maybe_choose_one_answer(self, message: str, session: SessionState) -> str | None:
        if not session.last_products or not self._wants_choose_one(message):
            return None
        return self.composer.compose_choose_one(
            session.last_products[0],
            SearchQuery(
                original_text=message,
                category=session.category or "other",
                slots=session.slots,
            ),
            alternative=session.last_products[1] if len(session.last_products) > 1 else None,
        )

    def _maybe_scope_funnel(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        """Narrow a system/project-level request to a concrete category.

        "Нужна система отопления", "водоснабжение", "сантехнику в дом" describe a
        whole system, not a single product. Instead of dumping random fittings or
        equating отопление with «котёл», we explain the system's components and ask
        which one to start with. Returns None as soon as the user names a concrete
        product type, so normal product flows keep working.
        """
        text = normalize_text(message)

        # Если клиент уже в конкретной категории и это не смена темы, то «для дачи»,
        # «водоснабжения», «в доме» — это уточнения текущего подбора, а не новый
        # системный запрос. Воронку не запускаем, ведём текущий сценарий.
        if session.category and session.category != "other" and not intent.is_topic_change:
            return None

        # Возражение «это не только котёл / не один котёл» — клиент уточняет, что
        # имел в виду систему, а не отдельный товар. Соглашаемся и снова сужаем.
        if "не только" in text or "не один" in text:
            if "отоплен" in text or session.slots.get("scope_funnel") == "heating":
                session.slots["scope_funnel"] = "heating"
                return "Согласен, отопление — это целая система. " + HEATING_FUNNEL
            if session.slots.get("scope_funnel") == "water" or "водоснаб" in text:
                session.slots["scope_funnel"] = "water"
                return "Верно, водоснабжение — это система. " + WATER_SUPPLY_FUNNEL

        # Клиент уже назвал конкретный товар — воронка не нужна, ведём подбор.
        if any(word in text for word in SPECIFIC_PRODUCT_WORDS):
            session.slots.pop("scope_funnel", None)
            return None

        # Тёплый пол — отдельная подсистема отопления со своим составом.
        if ("тепл" in text and "пол" in text) or "теплый пол" in text:
            session.slots["scope_funnel"] = "heating"
            return WARM_FLOOR_FUNNEL

        # Отопление как система (без конкретного узла).
        if "отоплен" in text:
            session.slots["scope_funnel"] = "heating"
            return HEATING_FUNNEL

        # Водоснабжение / водопровод как система.
        if "водоснаб" in text or "водопровод" in text:
            session.slots["scope_funnel"] = "water"
            return WATER_SUPPLY_FUNNEL

        # Общий проект / «сантехнику в дом» / «обустроить ванную» — самый широкий запрос.
        # Ультра-общие фразы без этих маркеров («есть дом») перехватит safety-net
        # перед поиском, поэтому держим список узким, чтобы не глушить small talk.
        general_markers = [
            "сантехник",
            "обустро",
            "в дом",
            "для дома",
            "на дач",
            "для дачи",
            "ванну",
            "ванной",
            "санузел",
            "кухн",
        ]
        if any(marker in text for marker in general_markers):
            session.slots["scope_funnel"] = "general"
            return GENERAL_FUNNEL

        return None

    def _maybe_consultation_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        """Explain a difference / give advice in context instead of repeating a question.

        Triggers on consulting phrasing ("в чём разница", "что лучше", "что
        посоветуешь", "чем отличается") and routes to the relevant conceptual
        explanation using the current category and the pending clarification —
        so "а в чём разница?" right after "газовый или электрический?" gets a
        real answer, not the same question again.
        """
        text = normalize_text(message)
        consult_markers = [
            "разниц",
            "отлич",
            "что лучше",
            "какой лучше",
            "что выбрать",
            "какой выбрать",
            "что посоветуеш",
            "посоветуй",
            "что взять",
            "что брать",
            "плюсы и минус",
            "за и против",
        ]
        if not any(marker in text for marker in consult_markers):
            return None

        pending = normalize_text(session.pending_question or "")
        has_gas = "газ" in text
        has_electric = "электрическ" in text or "электричеств" in text
        names_gas_electric = has_gas and has_electric
        in_boiler_context = (
            intent.category == "boilers"
            or session.category == "boilers"
            or "котел" in text
            or "котл" in text
            or ("газов" in pending and "электрическ" in pending)
            # Сравнение «газ или электричество» в магазине отопления — это про котёл.
            or names_gas_electric
        )
        pending_is_boiler_type = "газов" in pending and "электрическ" in pending

        # Одноконтурный vs двухконтурный (явно про контуры/ГВС).
        if in_boiler_context and ("контур" in text or "гвс" in text):
            session.category = "boilers"
            return ONE_VS_TWO_CONTOUR_CONSULT

        # Газовый vs электрический — по словам в сообщении или по заданному вопросу.
        if in_boiler_context and (names_gas_electric or pending_is_boiler_type):
            session.category = "boilers"
            if not session.slots.get("boiler_type"):
                session.pending_question = "Котёл нужен газовый или электрический?"
                session.pending_intent_type = "broad_category"
            return GAS_VS_ELECTRIC_CONSULT

        # Типы полипропиленовых труб (обычная vs армированная).
        in_pipe_context = (
            intent.category == "pipes" or session.category == "pipes" or "труба" in text or "трубы" in text
        )
        if in_pipe_context and any(
            marker in text for marker in ["армиров", "fiber", "alux", "pn20", "pn 20", "стеклов", "алюмин"]
        ):
            session.category = "pipes"
            return PIPE_TYPES_CONSULT

        return None

    def _drop_underpowered_boilers(
        self,
        products: list[Product],
        query: SearchQuery,
    ) -> list[Product]:
        """Keep boiler alternatives that are not too weak for the requested area.

        Otherwise a single underpowered model (e.g. 6 кВт for 100 м²) makes the
        guardrail reject the whole set and the customer sees nothing instead of the
        suitable option.
        """
        if query.category != "boilers" or not query.slots.get("area_m2"):
            return products
        required_kw = float(query.slots["area_m2"]) / 10.0
        kept: list[Product] = []
        for product in products:
            power = self.guardrails._extract_power_kw(product)
            if power is None or power >= required_kw * 0.75:
                kept.append(product)
        return kept or products

    def _append_companion_hint(self, answer: str, session: SessionState, category: str) -> str:
        hint = COMPANION_HINTS.get(category)
        if not hint:
            return answer
        flag = f"companion_hint_{category}"
        if session.slots.get(flag):
            return answer
        session.slots[flag] = True
        return f"{answer}\n\n{hint}"

    def _maybe_meta_question(self, message: str) -> str | None:
        """Questions about commercial terms not in the feed (discount/delivery/warranty/payment)."""
        text = normalize_text(message)
        topics = {
            "скидк": "скидки и акции",
            "акци": "скидки и акции",
            "достав": "доставку и сроки",
            "привез": "доставку и сроки",
            "когда буд": "доставку и сроки",
            "гаранти": "гарантию",
            "оплат": "оплату",
            "рассрочк": "оплату и рассрочку",
            "самовывоз": "самовывоз",
            "возврат": "возврат",
        }
        matched = next((label for key, label in topics.items() if key in text), None)
        if not matched:
            return None
        return (
            f"По вопросам про {matched} у меня нет данных в каталоге — это уточнит менеджер. "
            "Напишите «передай менеджеру», и я зафиксирую заявку. А с подбором товара, ценой и "
            "наличием по каталогу помогу прямо сейчас."
        )

    def _wants_manager_handoff(self, message: str) -> bool:
        text = normalize_text(message)
        negations = ["не надо менеджер", "без менеджера", "не нужен менеджер", "сам разберусь"]
        if any(neg in text for neg in negations):
            return False
        markers = [
            "менеджер",
            "оператор",
            "живой человек",
            "живым человеком",
            "с человеком",
            "позови человека",
            "сотрудник",
            "поддержк",
        ]
        return any(marker in text for marker in markers)

    def _is_contextual_followup(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        """A free-form question about products already shown — hand to the LLM agent.

        Catches conversational turns the rule pipeline can't script (stock/price of a
        specific item, "что лучше", "под какой котёл подходит", "а по паспорту") while
        leaving concrete searches, refinements and slot answers to the deterministic
        flow.
        """
        if not session.last_products or intent.is_topic_change:
            return False
        text = normalize_text(message)
        if intent.intent_type in {"exact_sku", "link_request", "complectation", "small_talk"}:
            return False
        # Новый товар другой категории — это новый подбор, не вопрос про показанное
        # (если только нет явной ссылки на ранее показанное).
        if intent.category not in {"other", session.category} and not self._references_shown_products(message):
            return False
        # Уточнения и команды, которые умеет детерминированный конвейер.
        refine_signals = [
            "дешевле",
            "подешевле",
            "аналог",
            "ссылк",
            "выбери один",
            "к нему",
            "американк",
            "сравни",
        ]
        if any(signal in text for signal in refine_signals):
            return False
        # Новый числовой параметр (мм/квт/площадь) — это рефайн поиска.
        if re.search(r"\d+\s*(?:мм|кв|м2|м²|квт|метр|контур)", text):
            return False
        # Маркеры именно товарного вопроса/ссылки на показанное — чтобы не перехватывать
        # отвлечённый small talk вроде «какие у тебя планы?».
        context_markers = [
            "наличи",
            "в наличии",
            "цена",
            "стоит",
            "сколько",
            "паспорт",
            "характеристик",
            "лучше",
            "посоветуй",
            "что взять",
            "что выбрать",
            "под какой",
            "к какому",
            "подходит",
            "для какого",
            "совмест",
            "разниц",
            "отлич",
            "этот",
            "эту",
            "эти",
            "тот",
            "они",
            "их ",
            "него",
            "нему",
            "нем ",
            "ней",
        ]
        return any(marker in text for marker in context_markers)

    def _passport_snippet(self, docs_text: str, limit: int = 900) -> str:
        """Excerpt of the passport, preferring the «комплект поставки» section."""
        low = docs_text.lower()
        for keyword in ["комплект поставки", "комплектность", "комплектац", "в комплект"]:
            idx = low.find(keyword)
            if idx >= 0:
                start = max(0, idx - 80)
                return docs_text[start : start + limit]
        return docs_text[:limit]

    def _is_open_complectation_question(self, message: str) -> bool:
        """Explicit «full package / answer from the passport» escalation (no specific part).

        Basic «что входит в комплект?» stays on the deterministic built-in list; this is
        for when the customer pushes for the full packaging or the passport.
        """
        text = normalize_text(message)
        open_markers = [
            "полную комплектац",
            "полная комплектац",
            "полной комплектац",
            "что входит в полн",
            "по паспорту",
            "в паспорте",
            "по тех паспорт",
            "по документац",
            "состав комплект поставки",
            "комплект поставки",
        ]
        return any(marker in text for marker in open_markers) and not self._requested_parts(message)

    def _references_shown_products(self, message: str) -> bool:
        # Однозначные ссылки на показанное. Дательные «к нему/к ней» намеренно НЕ
        # включаем — это companion-запрос («насос к нему»), а не вопрос про товар.
        text = normalize_text(message)
        return any(
            ref in text
            for ref in [
                "этот",
                "эту",
                "эти ",
                "тот ",
                "того ",
                "ты предложил",
                "ты показал",
                "что предложил",
                "что ты показал",
                "которые показал",
                "которые ты",
                "предложенн",
            ]
        )

    def _build_context_block(self, session: SessionState) -> str:
        lines: list[str] = []
        for index, card in enumerate(session.last_products[:3], start=1):
            product = self._find_product_by_sku(card.sku)
            stock = card.stock_status
            if card.stock_qty is not None:
                stock = f"{stock}, {card.stock_qty} шт."
            lines.append(f"{index}. {card.name} (артикул {card.sku})")
            lines.append(f"   Цена: {card.price:g} {card.currency}. Наличие: {stock}.")
            if card.characteristics:
                attrs = "; ".join(f"{k}: {v}" for k, v in card.characteristics.items())
                lines.append(f"   Характеристики: {attrs}")
            if product:
                components = self.guardrails.list_builtin_components(product)
                if components:
                    lines.append(f"   Встроенные узлы (по карточке): {', '.join(components)}")
                if product.docs_text:
                    lines.append(f"   Паспорт (выдержка): {self._passport_snippet(product.docs_text)}")
                elif product.description:
                    lines.append(f"   Описание: {product.description[:500]}")
            lines.append(f"   Ссылка: {card.url}")
        return "\n".join(lines)

    def _answer_from_context(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        context_block = self._build_context_block(session)
        if not context_block:
            return None
        fallback = (
            "По показанным товарам уточните, пожалуйста, что именно интересует — наличие, "
            "цену, характеристики или сравнение. Или назову артикул, чтобы посмотреть детальнее."
        )
        agents_used.append("ResponseComposerAgent")
        answer = self.composer.answer_in_context(message, context_block, fallback)
        agents_used.append("GuardrailsAgent")
        guard = self.guardrails.validate_context_answer(answer, context_block)
        if not guard.ok:
            logger.warning("Context answer rejected: %s", "; ".join(guard.issues))
            answer = fallback
        self._append_history(session, message, answer)
        return self._response(
            session.session_id, answer, session.last_products, False, intent, session, agents_used
        )

    def _maybe_analogs_response(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        text = normalize_text(message)
        if "аналог" not in text:
            return None
        if not session.last_products:
            return None
        blocking_slots = {"sku", "brand", "reference_brand", "old_model"}
        if blocking_slots.intersection(intent.slots):
            return None
        if intent.category not in {"other", session.category}:
            return None
        shown_skus = {normalize_sku_token(card.sku) for card in session.last_products}
        query = SearchQuery(
            original_text=message,
            category=session.category or "other",
            slots={key: value for key, value in session.slots.items() if key != "cheap"},
        )
        agents_used.append("FeedSearchAgent")
        alternatives = [
            product
            for product in self.search_agent.search_alternatives(query)
            if normalize_sku_token(product.sku) not in shown_skus
        ]
        if not alternatives:
            answer = (
                "Аналогов к показанным товарам в данных фида не вижу. "
                "Могу передать вопрос менеджеру — напишите «передай менеджеру»."
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], False, intent, session, agents_used)
        agents_used.append("ProductCardAgent")
        cards = self.card_agent.build_cards(alternatives, query, limit=3)
        agents_used.append("GuardrailsAgent")
        guard = self.guardrails.validate_cards(cards, alternatives, query)
        if not guard.ok or not cards:
            answer = (
                "Не могу безопасно показать аналоги по данным фида. "
                "Лучше передать вопрос менеджеру."
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], True, intent, session, agents_used)
        agents_used.append("ResponseComposerAgent")
        answer = self.composer.compose_products(
            cards,
            query,
            note="Аналоги к показанным ранее товарам — проверьте отличия в характеристиках:",
        )
        answer = self._guard_composed_answer(answer, "products", agents_used)
        session.last_products = cards
        self._append_history(session, message, answer)
        return self._response(session.session_id, answer, cards, False, intent, session, agents_used)

    def _maybe_comparison_answer(self, message: str, session: SessionState) -> str | None:
        if len(session.last_products) < 2:
            return None
        text = normalize_text(message)
        markers = ["отлича", "в чем разница", "какая разница", "разница между", "сравни"]
        if not any(marker in text for marker in markers):
            return None
        return self.composer.compose_comparison(session.last_products)

    def _wants_choose_one(self, message: str) -> bool:
        text = normalize_text(message)
        markers = [
            "выбери один",
            "выбери сама",
            "выбери сам",
            "что взять",
            "какой лучше",
            "какой выбрать",
            "посоветуй один",
            "оставь один",
            "один вариант",
        ]
        return any(marker in text for marker in markers)

    def _maybe_term_explanation(self, message: str) -> str | None:
        text = normalize_text(message)
        asks = any(marker in text for marker in ["что такое", "что значит", "не понимаю", "объясни"])
        if not asks:
            return None
        explanations: list[tuple[str, list[str], str]] = [
            (
                "монтажная длина",
                ["монтажн"],
                "это расстояние между гайками насоса, то есть сколько места он занимает в трубе. "
                "Частые варианты для циркуляционных насосов — 130 или 180 мм.",
            ),
            (
                "напор",
                ["напор"],
                "это способность насоса поднимать или проталкивать воду. В карточках часто указан в метрах: "
                "например 4 м или 6 м.",
            ),
            (
                "присоединение",
                ["присоедин"],
                "это размер подключения к трубе или резьбе. Для насосов часто встречается 25 или 32, "
                "для кранов — 1/2 или 3/4.",
            ),
            (
                "американка",
                ["американк"],
                "это разъёмное соединение с накидной гайкой. С ним кран или узел проще снять без разборки всей трубы.",
            ),
            (
                "термоголовка",
                ["термоголов"],
                "это регулятор на радиаторном клапане, который помогает поддерживать температуру в комнате.",
            ),
            (
                "одноконтурный котёл",
                ["одноконтурн"],
                "работает только на отопление. Для горячей воды к нему понадобится отдельный бойлер.",
            ),
            (
                "двухконтурный котёл",
                ["двухконтурн"],
                "даёт и отопление, и горячую воду — отдельный бойлер не нужен.",
            ),
            (
                "контур",
                ["контур"],
                "в котле один контур обычно работает на отопление, два контура — на отопление и горячую воду.",
            ),
            (
                "закрытая камера сгорания",
                ["закрытая камера", "закрытой камер", "камера сгорания", "коаксиал"],
                "котёл с закрытой камерой берёт воздух с улицы через коаксиальный дымоход "
                "(труба в трубе), а не из помещения — это безопаснее для жилых комнат.",
            ),
            (
                "армированная труба",
                ["армиров"],
                "это полипропиленовая труба, усиленная стекловолокном или алюминием. Она меньше "
                "расширяется от горячей воды, поэтому её берут для отопления и горячего водоснабжения.",
            ),
            (
                "pn",
                ["pn"],
                "это класс давления трубы. Для горячей воды и отопления часто смотрят PN20 или армированные трубы, "
                "но точный выбор зависит от задачи.",
            ),
            (
                "котёл",
                ["котел", "котл"],
                "это прибор, который греет воду для системы отопления, а двухконтурный — ещё и горячую "
                "воду для крана. Газовый дешевле в эксплуатации, но требует дымоход; электрический проще "
                "в установке. Мощность подбирают примерно 1 кВт на 10 м² площади.",
            ),
            (
                "насос",
                ["насос", "помпа"],
                "это устройство, которое прокачивает воду. Циркуляционный гоняет воду отопления по кругу, "
                "повысительный усиливает слабый напор, дренажный откачивает воду, скважинный поднимает её "
                "из скважины.",
            ),
            (
                "труба PPR",
                ["труба", "ppr", "полипропилен"],
                "PPR — это полипропиленовые трубы для водоснабжения и отопления, соединяются пайкой. "
                "Для горячей воды и отопления берут PN20 или армированные (стекловолокном/алюминием).",
            ),
            (
                "шаровой кран",
                ["кран", "вентиль"],
                "это запорная арматура: перекрывает поток одним поворотом ручки. Вариант с американкой "
                "позволяет снять узел без разборки трубы.",
            ),
            (
                "канализация",
                ["канализац"],
                "внутренняя (серые трубы, система HT) собирает стоки внутри дома, наружная (рыжая, KG) "
                "идёт под землёй от дома до септика или колодца.",
            ),
            (
                "радиаторная арматура",
                ["радиатор", "батаре"],
                "это клапаны и термоголовки для подключения батареи: термоголовка автоматически держит "
                "заданную температуру, запорный клапан просто перекрывает поток.",
            ),
        ]
        for term, roots, explanation in explanations:
            if any(root in text for root in roots):
                return self.composer.compose_term_explanation(term, explanation)
        if any(marker in text for marker in ["что такое", "что значит"]) or text.startswith("объясни"):
            return self.composer.compose_term_consult(message)
        return None

    def _maybe_engineering_risk_answer(self, message: str) -> str | None:
        text = normalize_text(message)
        risky_markers = [
            "гидравлический расчет",
            "гидравлический расчёт",
            "теплопотер",
            "рассчитай систему",
            "расчитать систему",
            "рассчитать систему",
            "проект отопления",
            "схему отопления",
            "схема обвязки",
        ]
        if not any(marker in text for marker in risky_markers):
            return None
        return (
            "Это уже инженерно рискованный вопрос: по фиду я не буду делать расчёт системы "
            "или схему обвязки. Могу помочь подобрать товары из ассортимента по известным "
            "параметрам, а для расчёта лучше передать задачу специалисту."
        )

    def _maybe_sink_question(self, message: str) -> str | None:
        text = normalize_text(message)
        if "раковин" not in text and "под раковину" not in text:
            return None
        return (
            "Под раковину обычно нужны: сифон (слив), гибкая подводка или угловой кран. "
            "Что именно нужно — слив/сифон или запорный кран?"
        )

    def _card_limit(self, query: SearchQuery) -> int:
        if query.slots.get("choose_one") or query.slots.get("allow_basic_option"):
            return 1
        return 3

    def _compose_query_note(self, query: SearchQuery) -> str | None:
        notes: list[str] = []
        if query.slots.get("fallback_after_repeat"):
            if query.category == "pumps":
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовой "
                    "вариант. Для типовой системы отопления чаще смотрят насосы 25/6 с "
                    "монтажной длиной 180 мм, но лучше сверить с вашей системой."
                )
            else:
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовые "
                    "варианты по текущим данным. Уточните недостающие параметры — подберу точнее."
                )
        elif query.slots.get("allow_basic_option"):
            notes.append(
                "Показываю базовый вариант из фида. Для точного подбора нужны монтажная длина, "
                "напор, присоединение или модель старого насоса."
            )
        old_pump_note = self.composer.compose_old_pump_note(query)
        if old_pump_note:
            notes.append(old_pump_note)
        if query.slots.get("choose_one"):
            notes.append("Выбираю один основной вариант из подходящих товаров.")
        return "\n".join(notes) if notes else None

    def _looks_like_affirmation(self, message: str) -> bool:
        text = normalize_text(message).strip()
        if not text:
            return False
        starters = [
            "да,",
            "да.",
            "да ",
            "ок,",
            "ок ",
            "хорошо",
            "согласен",
            "подходит",
            "ладно,",
            "не важн",
            "ничего страшного",
        ]
        if any(text.startswith(s) for s in starters):
            return True
        return text in {"да", "ок", "хорошо", "ладно"}

    def _looks_like_confirmation(self, message: str) -> bool:
        text = normalize_text(message)
        markers = [
            "это точно он",
            "точно он",
            "это он",
            "точно тот",
            "тот же товар",
            "ты уверен",
            "уверен",
            "точно ?",
            "точно?",
            "правильно",
        ]
        if any(marker in text for marker in markers):
            return True
        return text.strip() in {"точно", "уверен", "правильно?"}

    def _is_pending_continuation(
        self,
        intent: IntentResult,
        session: SessionState,
        message: str,
    ) -> bool:
        if not session.pending_question and not session.pending_complectation_parts:
            return False
        text = normalize_text(message)
        if intent.intent_type in {"small_talk", "unknown", "out_of_scope"} and intent.category == "other":
            return True
        if intent.intent_type == "exact_sku" and session.pending_complectation_parts:
            return True
        if session.pending_complectation_parts and intent.intent_type != "complectation":
            return True
        if "ау" == text or text.startswith("ау "):
            return True
        return False

    def _restore_pending_intent(
        self,
        intent: IntentResult,
        session: SessionState,
    ) -> None:
        if session.category and intent.category == "other":
            intent.category = session.category
        if session.pending_complectation_parts and intent.intent_type != "exact_sku":
            intent.intent_type = "complectation"
        elif session.pending_intent_type and intent.intent_type in {"small_talk", "unknown"}:
            intent.intent_type = session.pending_intent_type
        intent.is_topic_change = False

    def _stock_or_link_without_context(
        self,
        intent: IntentResult,
        session: SessionState,
        message: str,
    ) -> bool:
        if intent.intent_type != "stock_request":
            return False
        if intent.slots.get("sku"):
            return False
        if session.last_products:
            return False
        text = normalize_text(message)
        if text.startswith("что есть") or text.startswith("покаж"):
            return False
        if intent.category == "other" and not session.category:
            return True
        specific_keys = {
            "diameter_mm",
            "size_inch",
            "head_m",
            "mounting_length_mm",
            "connection_size",
            "old_model",
            "power_kw",
            "area_m2",
            "boiler_type",
            "pump_type",
            "element_type",
            "sewer_scope",
        }
        if specific_keys.intersection(intent.slots):
            return False
        return True

    def _stock_clarification_question(self, intent: IntentResult) -> str:
        if intent.category and intent.category != "other":
            return (
                "По какому товару проверить наличие? Напишите артикул, модель или ключевые "
                "параметры — иначе я не подтвержу, что в наличии именно нужный товар."
            )
        return (
            "По какому товару проверить наличие? Напишите артикул или модель — "
            "иначе я не смогу подтвердить, что в наличии именно нужный товар."
        )

    def _maybe_boiler_warning(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if "хват" not in text and "достаточн" not in text:
            return None
        slots = dict(session.slots)
        slots.update(intent.slots)
        power_kw = self._first_number(text, [r"(\d+(?:[,.]\d+)?)\s*квт"])
        area_m2 = self._first_number(
            text,
            [
                r"(\d{2,4})\s*(?:м2|м²|квадрат|кв)",
                r"(\d{2,4})\s*м(?:етр\w*)?(?:$|[^а-яa-z0-9])",
            ],
        )
        if power_kw is None:
            power_kw = self._float_slot(slots.get("power_kw"))
        if area_m2 is None:
            area_m2 = self._float_slot(slots.get("area_m2"))
        if not power_kw or not area_m2:
            return None
        required = area_m2 / 10.0
        if power_kw + 0.4 >= required:
            return None
        return (
            f"{power_kw:g} кВт на {area_m2:g} м² скорее не хватит: по эмпирическому правилу "
            f"нужно около {required:g} кВт (10 м² на 1 кВт), а с учётом утепления и горячей воды — "
            "обычно с запасом. Не буду подтверждать, что хватит. Если хотите, могу подобрать "
            "котёл с подходящей мощностью — уточните тип (газ/электр) и питание."
        )

    def _extract_insulation_hint(self, message: str) -> str | None:
        text = normalize_text(message)
        if any(marker in text for marker in ["утепл", "без супер", "обычное", "обычный", "слаб"]):
            return text
        return None

    def _compose_tradeoff_followup(self, insulation: str, message: str) -> str:
        return (
            "При обычном утеплении 15 кВт даст запас по мощности и комфортнее, "
            "12 кВт работает почти впритык и не оставляет запаса под ГВС. "
            "Не равнозначные варианты — для дом 100 м² я бы рекомендовал 15 кВт. "
            "Если нужны конкретные товары, уточните: газовый или электрический, питание 220/380."
        )

    def _maybe_boiler_tradeoff(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        kw_values = [float(m.replace(",", ".")) for m in re.findall(r"(\d+(?:[,.]\d+)?)\s*квт", text)]
        if len(kw_values) < 2 or "или" not in text:
            return None
        sorted_kw = sorted(set(kw_values))
        if len(sorted_kw) < 2:
            return None
        low, high = sorted_kw[0], sorted_kw[-1]
        return (
            f"{low:g} и {high:g} кВт — не равнозначные варианты. Ориентир 10 м² на 1 кВт, "
            f"но запас по мощности зависит от утепления, числа контуров и ГВС. {high:g} кВт даст запас "
            f"при плохом утеплении и при подключении бойлера, {low:g} кВт работает впритык. "
            "Уточните: какое утепление и нужна ли горячая вода — тогда подберу варианты из фида."
        )

    def _first_number(self, text: str, patterns: list[str]) -> float | None:
        import re as _re

        for pattern in patterns:
            match = _re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1).replace(",", "."))
                except ValueError:
                    continue
        return None

    def _float_slot(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _guard_composed_answer(
        self,
        answer: str,
        mode: str,
        agents_used: list[str],
    ) -> str:
        draft = self.composer.last_draft
        if not draft:
            return answer
        guard = self.guardrails.validate_response_text(draft, answer, mode=mode)
        if "GuardrailsAgent" not in agents_used:
            agents_used.append("GuardrailsAgent")
        if guard.ok:
            return answer
        logger.warning("Unsafe composed answer rejected: %s", "; ".join(guard.issues))
        return guard.safe_message or draft

    def _should_restart_category_context(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        if not session.slots or session.topic_changed:
            return False
        if intent.category == "other" or intent.category != session.category:
            return False
        if intent.intent_type not in {"broad_category", "attribute_request"}:
            return False

        text = normalize_text(message)
        if self._looks_like_parameter_followup(text):
            return False

        category_words = {
            "pumps": ["насос", "помпа"],
            "pipes": ["труба", "трубы"],
            "sewer": ["канализац"],
            "boilers": ["котел", "котёл", "котл"],
            "valves": ["кран", "шаровый", "вентиль"],
            "radiator_fittings": ["радиатор", "батаре", "термоголов", "клапан"],
        }
        if not any(word in text for word in category_words.get(intent.category, [])):
            return False

        specific_slots = set(intent.slots) - {"product_kind"}
        return not specific_slots

    def _looks_like_parameter_followup(self, text: str) -> bool:
        return any(
            marker in text
            for marker in [
                "мм",
                "напор",
                "метр",
                "м ",
                "дешев",
                "подешевле",
                "в наличии",
                "для воды",
                "воды",
                "водоснаб",
                "отоплен",
                "канализац",
                "дач",
                "полив",
                "скваж",
                "колод",
                "электр",
                "газ",
                "внутрен",
                "наруж",
                "углов",
                "прям",
                "американк",
            ]
        )

    def _looks_like_card_question(self, message: str, session: SessionState) -> bool:
        """True for questions about an already shown product's card / documentation."""
        if not session.last_products:
            return False
        text = normalize_text(message)
        markers = [
            "что входит",
            "что в комплект",
            "в комплект",
            "комплектац",
            "что идет в комплект",
            "проверь документ",
            "посмотри документ",
            "в документац",
            "по документац",
            "в паспорт",
            "по паспорт",
            "проверь карточк",
            "проверь описание",
            "проверь характеристик",
            "есть ли там",
            "есть ли в нем",
            "есть ли у",
            "входит ли",
            "идет ли",
            "в нем есть",
            "в него входит",
            "не входит",
            "входит насос",
        ]
        if any(marker in text for marker in markers):
            return True
        # «расширительный бак есть?», «насос идёт?» — деталь + глагол наличия.
        part_words = [
            "насос",
            "бак",
            "расширительн",
            "клапан",
            "бойлер",
            "манометр",
            "датчик",
            "группа безопас",
            "камера сгоран",
        ]
        presence_verbs = ["есть", "входит", "идет", "имеется", "включен", "встроен"]
        if (
            any(part in text for part in part_words)
            and any(verb in text for verb in presence_verbs)
            and not any(stop in text for stop in ["в наличии", "на складе", "сколько"])
        ):
            return True
        return False

    def _requested_parts(self, message: str) -> list[str]:
        text = normalize_text(message)
        parts: list[str] = []
        if "насос" in text:
            parts.append("насос")
        if "бак" in text or "расширительн" in text:
            parts.append("бак")
        if "клапан" in text:
            parts.append("клапан")
        if "манометр" in text:
            parts.append("манометр")
        if ("групп" in text and "безопас" in text) or "безопасн" in text:
            parts.append("группа безопасности")
        if "обвяз" in text:
            parts.append("обвязка")
        if "бойлер" in text:
            parts.append("бойлер")
        if "гайк" in text:
            parts.append("гайки")
        if "кронштейн" in text:
            parts.append("кронштейн")
        if "датчик" in text:
            parts.append("датчик")
        return parts

    def _compose_complectation_question(self, message: str, requested_parts: list[str]) -> str:
        text = normalize_text(message)
        if "обвяз" in text or "группа безопасности" in requested_parts or "обвязка" in requested_parts:
            return (
                "По какому котлу и какой системе обвязка/группа безопасности нужна? "
                "Уточните модель/артикул котла и тип системы (открытая или закрытая, радиаторы/тёплый пол) — "
                "без сверки с документацией не буду подтверждать конкретные узлы."
            )
        return (
            "По какому котлу или товару проверить комплектацию? Напишите модель/артикул и "
            "систему — без сверки с фидом не подтвержу узлы."
        )

    def _find_product_by_sku(self, sku: str) -> Product | None:
        needle = normalize_sku_token(sku)
        for product in self.search_agent.products:
            if normalize_sku_token(product.sku) == needle:
                return product
        return None

    def _append_history(self, session: SessionState, user_message: str, answer: str) -> None:
        session.history.append({"role": "user", "content": user_message})
        session.history.append({"role": "assistant", "content": answer})
        session.history = session.history[-20:]

    def _response(
        self,
        session_id: str,
        answer: str,
        cards: list[ProductCard],
        need_handoff: bool,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse:
        return ChatResponse(
            session_id=session_id,
            answer=answer,
            products=[
                ChatProductSummary(
                    sku=card.sku,
                    name=card.name,
                    price=card.price,
                    currency=card.currency,
                    stock_status=card.stock_status,
                    url=card.url,
                )
                for card in cards
            ],
            need_handoff=need_handoff,
            debug={
                "intent": intent.intent_type,
                "category": intent.category,
                "slots": session.slots,
                "agents_used": agents_used,
                "llm_used": intent.llm_used or self.composer.last_llm_used,
                "intent_llm_used": intent.llm_used,
                "response_llm_used": self.composer.last_llm_used,
                "response_llm_requested": self.composer.last_llm_requested,
                "response_llm_fallback_reason": self.composer.last_llm_fallback_reason,
                "any_llm_used": intent.llm_used or self.composer.last_llm_used,
                "topic_changed": session.topic_changed,
                "products_loaded_from": self.products_loaded_from,
            },
        )
