from __future__ import annotations

import logging
import re
from typing import Any

from app.config import Settings, get_settings
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

    def reload_products(self, refresh: bool = True) -> tuple[int, str]:
        products, source = self.feed_loader.load_products(refresh=refresh)
        self.search_agent.set_products(products)
        self.products_loaded_from = source
        return len(products), source

    def handle_chat(self, session_id: str, message: str) -> ChatResponse:
        session = self.sessions.get(session_id)
        session.topic_changed = False
        session.slots.pop("fallback_after_repeat", None)
        self.composer.reset_usage()
        self.composer.set_history(session.history)
        self.composer.set_state(session.category, session.slots)
        agents_used: list[str] = []

        intent = self.intent_router.route(message, session)
        agents_used.append("IntentRouterAgent")

        if self._is_pending_continuation(intent, session, message):
            self._restore_pending_intent(intent, session)

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

        gas_vs_electric = self._maybe_gas_vs_electric_consult(message, intent, session)
        if gas_vs_electric:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, gas_vs_electric)
            self.sessions.save(session)
            return self._response(session_id, gas_vs_electric, [], False, intent, session, agents_used)

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

        if self._stock_or_link_without_context(intent, session, message):
            question = self._stock_clarification_question(intent)
            answer = self.composer.compose_clarification(question, user_message=message)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "clarification", agents_used)
            session.pending_question = question
            session.pending_intent_type = intent.intent_type
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if slot_result.needs_clarification and slot_result.question:
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

        session.pending_question = None
        session.pending_intent_type = None
        session.question_repeats = 0

        query = self._build_query(message, intent, session)
        agents_used.append("FeedSearchAgent")
        products = self._safe_search(query)
        if not products:
            alternatives = self.search_agent.search_alternatives(query)
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
        if not target_product and len(session.last_products) == 1:
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

    def _maybe_gas_vs_electric_consult(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if "газов" not in text or "электрическ" not in text:
            return None
        if not any(marker in text for marker in ["или", "лучше", "выбрать", "разница", "отлича"]):
            return None
        if (
            intent.category != "boilers"
            and session.category != "boilers"
            and "котел" not in text
            and "котл" not in text
        ):
            return None
        session.category = "boilers"
        session.pending_question = "Газ подведён и какая площадь?"
        session.pending_intent_type = "broad_category"
        return (
            "Тут всё решает наличие газа. Если газ подведён — газовый котёл обычно ощутимо "
            "дешевле в эксплуатации, но нужны дымоход и согласование. Электрический проще и "
            "дешевле в установке, тише и без дымохода, но дороже по счетам за электричество, "
            "а для большой площади часто нужно 380 В. "
            "Подскажите: газ подведён и какая площадь? Подберу конкретные варианты из каталога."
        )

    def _append_companion_hint(self, answer: str, session: SessionState, category: str) -> str:
        hint = COMPANION_HINTS.get(category)
        if not hint:
            return answer
        flag = f"companion_hint_{category}"
        if session.slots.get(flag):
            return answer
        session.slots[flag] = True
        return f"{answer}\n\n{hint}"

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
        ]
        for term, roots, explanation in explanations:
            if any(root in text for root in roots):
                return self.composer.compose_term_explanation(term, explanation)
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

    def _requested_parts(self, message: str) -> list[str]:
        text = normalize_text(message)
        parts: list[str] = []
        if "насос" in text:
            parts.append("насос")
        if "бак" in text:
            parts.append("бак")
        if ("групп" in text and "безопас" in text) or "безопасн" in text:
            parts.append("группа безопасности")
        if "обвяз" in text:
            parts.append("обвязка")
        if "бойлер" in text:
            parts.append("бойлер")
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
