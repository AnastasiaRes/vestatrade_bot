from __future__ import annotations

import html
import logging
import re
from typing import Any

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - exercised only without optional dependency
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def partial_ratio(a: str, b: str) -> int:
            return int(SequenceMatcher(None, a, b).ratio() * 100)

        @staticmethod
        def ratio(a: str, b: str) -> int:
            return int(SequenceMatcher(None, a, b).ratio() * 100)

    fuzz = _FuzzFallback()

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

from .consultant import ConsultantAgent
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

_WORD_RE = re.compile(r"[a-zа-я0-9]+")

FEED_BRAND_ALIASES: dict[str, set[str]] = {
    "ariston": {"аристон"},
    "arderia": {"ардерия"},
    "e.c.a": {"eca", "еса", "ека"},
    "ostendorf": {"остендорф"},
    "rommer": {"роммер"},
    "thermex": {"термекс"},
    "unipump": {"юнипамп", "унипамп"},
    "valtec": {"валтек"},
    "wilo": {"вило"},
}

HUMAN_ROLE_MARKERS = [
    "менеджер",
    "оператор",
    "консультант",
    "сотрудник",
    "продавец",
    "продавцом",
    "продавца",
    "продав",
    "продов",
    "администратор",
    "админ",
    "специалист",
    "поддержка",
    "поддержку",
    "поддержк",
    "человек",
    "человеком",
    "человека",
    "живой",
    "живого",
    "реальный",
    "реального",
]

CONTACT_INTENT_MARKERS = [
    "как связаться",
    "связаться",
    "свяжите",
    "связь",
    "связ",
    "контакт",
    "контакты",
    "номер",
    "телефон",
    "позвонить",
    "звонить",
    "написать",
    "почта",
    "email",
    "имейл",
    "куда позвонить",
    "куда написать",
]

GENERIC_CONTACT_MARKERS = [
    "куда позвонить",
    "куда написать",
    "какой телефон",
    "номер телефона",
    "контакты",
]

TRANSFER_INTENT_MARKERS = [
    "передай",
    "переключ",
    "переключи",
    "соедин",
    "соедини",
    "позови",
    "пригласи",
    "вызови",
    "поговорить",
    "пообщаться",
    "дай",
    "дайте",
    "нужен",
    "нужна",
    "хочу",
    "можно",
]


COMPANION_HINTS: dict[str, str] = {
    "boilers": (
        "Кстати, у настенных котлов циркуляционный насос часто уже встроен, поэтому отдельный "
        "насос нужен не всегда. Его добавляют для тёплого пола, бойлера, нескольких контуров "
        "или длинной системы; также обычно проверяют группу безопасности и трубы для обвязки."
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
    "radiators": (
        "К радиатору также нужны клапаны и узлы подключения; их размер сверяют "
        "с карточкой радиатора."
    ),
    "fittings": (
        "Фитинги должны совпадать с типом системы и обоими размерами перехода."
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
    "Полипропиленовые трубы бывают обычными и армированными стекловолокном (PP-FIBER) "
    "или алюминием (PP-ALUX). Армирование прежде всего уменьшает температурное удлинение; "
    "допустимые температуру и давление нужно сверять по паспорту конкретной трубы. Для "
    "водяного тёплого пола жёсткую PPR используют только на подводящих магистралях, но не "
    "как трубу петли: для контура нужна предназначенная для него гибкая PEX, PE-RT или "
    "металлопластиковая труба.\n"
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
    "Для водяного тёплого пола обычно нужны контурная труба PEX/PE-RT или металлопластик, "
    "коллектор, смесительный узел или "
    "насосная группа, запорная арматура, фитинги, теплоизоляция, демпферная лента, "
    "крепёж и автоматика. Обычную PPR не буду выдавать за трубу петли пола; в каталоге "
    "могу подобрать только совместимые позиции, а также насос, краны и арматуру. "
    "Давайте соберём комплект по шагам: какая площадь "
    "тёплого пола?"
)
GENERAL_FUNNEL = (
    "Подскажите, что именно нужно — в каталоге Vesta Trading есть котлы, насосы, "
    "трубы, краны, канализация и радиаторная арматура. С чего начнём?"
)
BATHROOM_FUNNEL = (
    "Для ванной обычно нужны несколько блоков: водоснабжение, канализация, "
    "смесители/краны, трубы и фитинги, запорная арматура, при необходимости насосы, "
    "тёплый пол и радиатор или полотенцесушитель. В нашем каталоге могу подобрать "
    "трубы, краны, насосы, канализацию и арматуру. Начнём с водоснабжения, "
    "канализации или тёплого пола?"
)
WARM_FLOOR_ALL_FOLLOWUP = (
    "Окей, собираем комплект для тёплого пола. Чтобы не гадать: какая площадь "
    "тёплого пола в м² и это водяной тёплый пол от котла или электрический? "
    "Если пока не знаете — могу дать типовой список комплекта и начать с "
    "универсальных позиций из каталога."
)
HEATING_ALL_FOLLOWUP = (
    "Окей, собираем отопление как систему. Для старта нужны 2 вещи: площадь "
    "помещения/дома и источник тепла — газ, электричество или уже выбранный котёл. "
    "После этого подберём из каталога котёл, насосы, трубы и арматуру по шагам."
)
WATER_SUPPLY_ALL_FOLLOWUP = (
    "Окей, собираем водоснабжение. Сначала уточните источник воды: центральный "
    "водопровод, скважина или колодец, и где нужна вода — дом, ванная, кухня, "
    "полив. После этого подберём насос, трубы, краны и фитинги из каталога."
)
BATHROOM_ALL_FOLLOWUP = (
    "Окей, собираем ванную/санузел. Базово там два обязательных блока: "
    "водоснабжение и канализация; дальше краны, трубы, фитинги, запорная арматура, "
    "при необходимости насос или тёплый пол. Начнём с размеров/точек воды или с "
    "канализации?"
)
GENERAL_ALL_FOLLOWUP = (
    "Окей, пойдём как по проекту, но без угадывания товаров. Сначала выберите "
    "систему: отопление, водоснабжение, канализация, ванная/санузел или котельная. "
    "Дальше задам 1–2 параметра и покажу подходящие позиции из ассортимента."
)
SCOPE_FOLLOWUP_ANSWERS = {
    "heating": HEATING_ALL_FOLLOWUP,
    "water": WATER_SUPPLY_ALL_FOLLOWUP,
    "warm_floor": WARM_FLOOR_ALL_FOLLOWUP,
    "bathroom": BATHROOM_ALL_FOLLOWUP,
    "general": GENERAL_ALL_FOLLOWUP,
}

PROJECT_SCOPE_LABELS: dict[str, str] = {
    "warm_floor": "тёплого пола",
    "bathroom": "ванной/санузла",
    "heating": "отопления",
    "water": "водоснабжения",
    "sewer": "канализации",
    "general": "инженерной сантехники",
}

PROJECT_CATEGORY_LABELS: dict[str, str] = {
    "boilers": "Котёл",
    "pumps": "Насос",
    "pipes": "Трубы",
    "valves": "Запорная арматура",
    "sewer": "Канализация",
    "radiator_fittings": "Радиаторная арматура",
    "radiators": "Радиаторы",
    "fittings": "Фитинги",
}

PROJECT_CATEGORY_REASONS: dict[str, str] = {
    "boilers": "закрывает источник тепла; мощность и тип котла нужно сверять с площадью, топливом и ГВС",
    "pumps": "нужен для циркуляции или отдельного контура, если штатного насоса котла/узла недостаточно",
    "pipes": "это базовая магистраль системы; диаметр и материал уточняются по задаче",
    "valves": "нужна для отсечения и обслуживания узлов без слива всей системы",
    "sewer": "закрывает отвод стоков; диаметр и длина зависят от участка",
    "radiator_fittings": "нужна для подключения и регулировки приборов отопления",
    "radiators": "отдают тепло в помещение; тип и размер нужно сверить с расчётом",
    "fittings": "соединяют трубы; важны система, тип фитинга и оба размера",
}

PROJECT_SCOPE_CATEGORIES: dict[str, list[str]] = {
    "warm_floor": ["pipes", "pumps", "valves"],
    "bathroom": ["pipes", "valves", "sewer"],
    "heating": ["boilers", "pumps", "pipes", "valves", "radiator_fittings"],
    "water": ["pipes", "valves", "pumps"],
    "sewer": ["sewer"],
    "general": ["boilers", "pumps", "pipes", "valves", "sewer"],
}

PROJECT_CART_CATEGORY_ORDER = [
    "boilers",
    "pumps",
    "pipes",
    "valves",
    "sewer",
    "radiator_fittings",
]


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
        self.consultant = ConsultantAgent(
            self.llm_client, model=self.settings.llm_model_strong
        )
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
        self.consultant.last_llm_used = False
        self.consultant.last_fallback_reason = None
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
        self._enrich_brand_from_feed(message, intent)
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

        if intent.is_topic_change and self._is_project_component_turn(intent, session):
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

        if intent.category == "pumps" and self._pump_requested_for_boiler_context(message, session):
            intent.slots.setdefault("pump_type", "циркуляционный")
            intent.slots.setdefault("pump_use", "отопление")
            intent.slots.setdefault("pump_context", "котел")
            intent.slots.setdefault("allow_basic_option", True)

        manager_contact_answer = self._maybe_manager_contact_question(message)
        if manager_contact_answer:
            agents_used.append("HandoffAgent")
            self._append_history(session, message, manager_contact_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                manager_contact_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        handoff_process_answer = self._maybe_handoff_process_question(message)
        if handoff_process_answer:
            agents_used.append("HandoffAgent")
            self._append_history(session, message, handoff_process_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                handoff_process_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

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

        warm_floor_pipe_answer = self._maybe_warm_floor_pipe_answer(message)
        if warm_floor_pipe_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, warm_floor_pipe_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                warm_floor_pipe_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

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

        hot_water_answer = self._maybe_one_contour_hot_water_answer(message, intent, session)
        if hot_water_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, hot_water_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                hot_water_answer,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        boiler_type_correction = self._maybe_boiler_type_correction(message, intent, session)
        if boiler_type_correction:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            answer, cards = boiler_type_correction
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                cards,
                False,
                intent,
                session,
                agents_used,
            )

        shown_boiler_type = self._maybe_shown_boiler_type_answer(message, intent, session)
        if shown_boiler_type:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            answer, cards = shown_boiler_type
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                cards,
                False,
                intent,
                session,
                agents_used,
            )

        consultation = self._maybe_consultation_answer(message, intent, session)
        if consultation:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, consultation)
            self.sessions.save(session)
            return self._response(session_id, consultation, [], False, intent, session, agents_used)

        pump_domain_answer = self._maybe_pump_domain_answer(message, intent, session)
        if pump_domain_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(pump_domain_answer, "generic", agents_used)
            products = session.last_products if session.last_products else []
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, products, False, intent, session, agents_used)

        misunderstanding_answer = self._maybe_misunderstanding_answer(message, session)
        if misunderstanding_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, misunderstanding_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                misunderstanding_answer,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        # Открытый вопрос «что входит в полную комплектацию / ответь по паспорту»
        # отвечаем прямо из привязанного документа. Модель не должна пересказывать
        # паспорт: в живых диалогах она могла отрицать раздел, который был в контексте.
        if session.last_products and self._is_open_complectation_question(message):
            target_card, ambiguous = self._resolve_shown_product_card(message, session)
            if ambiguous:
                answer = self._complectation_target_question(session.last_products)
                session.pending_question = answer
                session.pending_intent_type = "complectation"
                session.pending_complectation_parts = ["комплектация"]
                agents_used.append("ResponseComposerAgent")
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(
                    session_id, answer, session.last_products, False, intent, session, agents_used
                )
            if target_card:
                session.last_products = [target_card]
                answer = self._compose_passport_package_answer(target_card)
                agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(
                    session_id, answer, [target_card], False, intent, session, agents_used
                )

        if intent.intent_type == "complectation" and session.last_products:
            response = self._handle_complectation(message, session, intent, agents_used)
            self.sessions.save(session)
            return response

        comparison_answer = self._maybe_comparison_answer(message, session)
        if comparison_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(comparison_answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id, answer, session.last_products, False, intent, session, agents_used
            )

        yes_no_complectation = self._maybe_yes_no_complectation_followup(message, session)
        if yes_no_complectation:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(
                yes_no_complectation, "complectation", agents_used
            )
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
            chosen_cards = session.last_products[:1]
            session.last_products = chosen_cards
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, chosen_cards, False, intent, session, agents_used)

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

        repeated_filter_answer = self._maybe_redundant_filter_confirmation(message, intent, session)
        if repeated_filter_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, repeated_filter_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                repeated_filter_answer,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        required_clarification = self._maybe_required_boiler_clarification(message, session)
        if required_clarification:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, required_clarification)
            self.sessions.save(session)
            return self._response(
                session_id,
                required_clarification,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        # Свободный вопрос про уже показанные товары — отвечает LLM-агент по их карточкам.
        # Стоит после подтверждений/tradeoff, но до общих fallback'ов (small talk /
        # unknown / повторный поиск), которые иначе «съели» бы вопрос или зациклили список.
        if self._is_contextual_followup(message, intent, session):
            compatibility_answer = self._maybe_pump_compatibility_answer(message, session)
            if compatibility_answer:
                agents_used.append("ResponseComposerAgent")
                answer = self._guard_composed_answer(compatibility_answer, "generic", agents_used)
                self._append_history(session, message, answer)
                self.sessions.save(session)
                return self._response(
                    session_id, answer, session.last_products, False, intent, session, agents_used
                )
            context_response = self._answer_from_context(message, intent, session, agents_used)
            if context_response is not None:
                self.sessions.save(session)
                return context_response

        project_response = self._maybe_project_cart_response(
            session_id, message, intent, session, agents_used
        )
        if project_response is not None:
            self.sessions.save(session)
            return project_response

        # Консультативный/проектный разговор («дом построить», «240 м², газ и
        # электричество», «есть другие котлы?», «что ещё нужно?», «в котле встроенный
        # насос?») ведёт ConsultantAgent: LLM рассуждает по предметной области и
        # опирается на реальные товары из фида. Если LLM недоступна (нет ключа/бюджета),
        # метод возвращает None и мы продолжаем детерминированным пайплайном.
        consult_response = self._maybe_consult(session_id, message, intent, session, agents_used)
        if consult_response is not None:
            self.sessions.save(session)
            return consult_response

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

        scope_followup = self._maybe_scope_followup_answer(message, session)
        if scope_followup:
            answer = scope_followup
            agents_used.append("ResponseComposerAgent")
            session.pending_question = answer
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
        if (
            query.category == "boilers"
            and query.slots.get("area_m2")
            and not query.sku
            and not query.brand
        ):
            # A broad sizing request such as "котёл на 240 м" is not an
            # exact model-name lookup. Fuzzy name matching confuses 240 м² with
            # model/power token 24 and can drop the best 24 kW option.
            direct_products = []

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
                cards = self._limit_oversized_boiler_cards(
                    cards,
                    self._float_slot(query.slots.get("area_m2")),
                    query.original_text,
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
                    self._remember_project_cart(session, cards, replace_category=query.category)
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
        cards = self._limit_oversized_boiler_cards(
            cards,
            self._float_slot(query.slots.get("area_m2")),
            query.original_text,
        )

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
        self._remember_project_cart(session, cards, replace_category=query.category)
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

        sku_from_message = intent.slots.get("sku") or session.slots.get("sku")
        target_product: Product | None = None
        target_card: ProductCard | None = None
        if sku_from_message:
            target_product = self._find_product_by_sku(sku_from_message)
        if not target_product and session.last_products:
            target_card, ambiguous = self._resolve_shown_product_card(message, session)
            if ambiguous:
                question = self._complectation_target_question(session.last_products)
                session.pending_question = question
                session.pending_intent_type = "complectation"
                session.pending_complectation_parts = requested_parts
                agents_used.append("ResponseComposerAgent")
                self._append_history(session, message, question)
                return self._response(
                    session.session_id,
                    question,
                    session.last_products,
                    False,
                    intent,
                    session,
                    agents_used,
                )
            if target_card:
                target_product = self._find_product_by_sku(target_card.sku)

        if not target_product:
            if session.pending_complectation_parts:
                summary = self.handoff.build_summary(
                    message,
                    session,
                    missing=["нет артикула/модели для проверки комплектации"],
                )
                answer = (
                    "Без артикула или модели котла не подтвержу обвязку или комплектацию. "
                    "Не буду угадывать узлы системы — лучше передам менеджеру краткую "
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
                "Не вижу подтверждения комплектации в карточке товара. Лучше проверить документацию или передать вопрос менеджеру."
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], True, intent, session, agents_used)

        answer = self.composer.compose_complectation_confirmed(target_card, requested_parts)
        answer = self._guard_composed_answer(answer, "complectation", agents_used)
        session.last_products = [target_card]
        session.slots["last_complectation_parts"] = requested_parts
        session.slots["last_complectation_sku"] = target_card.sku
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
        slots = self._normalized_query_slots(session.slots)
        return SearchQuery(
            original_text=message,
            category=intent.category if intent.category != "other" else session.category or "other",
            slots=slots,
            sku=slots.get("sku"),
            brand=slots.get("brand"),
            cheap=bool(slots.get("cheap") or intent.flags.get("cheap")),
            in_stock_only=bool(slots.get("in_stock") or intent.flags.get("in_stock")),
        )

    def _enrich_brand_from_feed(self, message: str, intent: IntentResult) -> None:
        """Resolve every current feed brand, including common Cyrillic spellings.

        The intent router has a short static vocabulary, while the feed can gain
        new vendors. An explicit brand must therefore be grounded against the
        loaded catalog before building the search query.
        """
        if intent.slots.get("brand") or not self.search_agent.products:
            return
        text = normalize_text(message)
        brands_by_normalized: dict[str, str] = {}
        for product in self.search_agent.products:
            actual = str(product.brand or "").strip()
            normalized = normalize_text(actual)
            if normalized:
                brands_by_normalized.setdefault(normalized, actual)

        matched: list[str] = []
        for normalized, actual in brands_by_normalized.items():
            aliases = {normalized, *FEED_BRAND_ALIASES.get(normalized, set())}
            if any(
                re.search(
                    rf"(?<![a-zа-я0-9]){re.escape(alias)}(?![a-zа-я0-9])",
                    text,
                )
                for alias in aliases
                if alias
            ):
                matched.append(actual)
        if len(matched) != 1:
            return
        intent.slots["brand"] = matched[0]
        if intent.category != "other" and intent.intent_type in {
            "unknown",
            "broad_category",
            "attribute_request",
        }:
            intent.intent_type = "brand_category"

    def _normalized_query_slots(self, raw_slots: dict[str, Any]) -> dict[str, Any]:
        slots = dict(raw_slots)
        boiler_type = normalize_text(str(slots.get("boiler_type") or ""))
        if boiler_type:
            if boiler_type in {"electric", "electrical", "electric boiler"} or "электр" in boiler_type:
                slots["boiler_type"] = "электрический"
            elif boiler_type in {"gas", "gas boiler"} or "газ" in boiler_type:
                slots["boiler_type"] = "газовый"
        contours = normalize_text(str(slots.get("contours") or ""))
        if contours:
            if "двух" in contours or contours == "2" or "two" in contours or "double" in contours or "dual" in contours:
                slots["contours"] = "двухконтурный"
            elif "одно" in contours or contours == "1" or "one" in contours or "single" in contours:
                slots["contours"] = "одноконтурный"
        return slots

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
            "total_length_m",
            "secondary_diameter_mm",
            "radiator_size_mm",
            "sections",
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

    def _resolve_shown_product_card(
        self,
        message: str,
        session: SessionState,
    ) -> tuple[ProductCard | None, bool]:
        """Resolve a reference to one shown card; report genuine ambiguity.

        Product-list follow-ups such as ``что входит в комплект?`` must not silently
        switch to the first of three models.  Ordinals, SKU and model codes (SB24,
        E9, etc.) make the reference explicit; otherwise we ask once.
        """
        cards = session.last_products
        if not cards:
            return None, False
        ordinal = self._select_ordinal_index(message, cards)
        if ordinal is not None:
            return cards[ordinal], False
        if len(cards) == 1:
            return cards[0], False

        text = normalize_text(message)
        matched: list[ProductCard] = []
        for card in cards:
            model_tokens = {
                token
                for token in re.findall(r"[a-zа-я0-9./\-]+", normalize_text(card.name))
                if len(token) >= 2 and re.search(r"[a-zа-я]", token) and re.search(r"\d", token)
            }
            if any(token in text for token in model_tokens):
                matched.append(card)
        if len(matched) == 1:
            return matched[0], False
        return None, True

    def _complectation_target_question(self, cards: list[ProductCard]) -> str:
        lines = [
            "Уточните, по какой из показанных моделей проверить комплектацию — у них она может отличаться:"
        ]
        for index, card in enumerate(cards[:3], start=1):
            lines.append(f"{index}. {card.sku} — {html.unescape(card.name)}")
        lines.append("Напишите номер, модель или артикул.")
        return "\n".join(lines)

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
            f"Потому что параметры из ваших уточнений совпадают с карточками товаров. "
            f"Учёл: {details}. Подходящие позиции: {skus}."
        )

    def _maybe_pump_compatibility_answer(self, message: str, session: SessionState) -> str | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        markers = [
            "под какой котел",
            "под какой котёл",
            "к какому котлу",
            "с каким котлом",
            "подходит к котлу",
            "совместим с котлом",
            "совместима с котлом",
        ]
        if not any(marker in text for marker in markers):
            return None
        pump_card: ProductCard | None = None
        for card in session.last_products:
            product = self._find_product_by_sku(card.sku)
            if product and self.search_agent.canonical_category(product) == "pumps":
                pump_card = card
                break
        if not pump_card:
            return None

        details = []
        for key, value in pump_card.characteristics.items():
            key_norm = normalize_text(key)
            if any(marker in key_norm for marker in ["напор", "монтаж", "присоедин", "мощность"]):
                details.append(f"{key}: {value}")
        detail_text = "; ".join(details) if details else "в карточке нет достаточных параметров для проверки совместимости"
        return (
            f"В карточке насоса {pump_card.sku} не указана привязка к конкретным моделям котлов, "
            "поэтому не буду подтверждать совместимость с определённым котлом. "
            f"По карточке насоса есть такие данные: {detail_text}. "
            "Циркуляционный насос подбирают не «под котёл» напрямую, а под систему: напор, расход, "
            "монтажную длину, присоединение и схему контуров. Если в настенном котле уже есть "
            "встроенный насос, отдельный насос обычно нужен только на тёплый пол, бойлер, "
            "длинную ветку или отдельные контуры. Для точного подтверждения лучше сверить схему "
            "или передать вопрос менеджеру."
        )

    def _maybe_pump_domain_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if self._is_pump_domain_correction(text):
            session.category = "pumps"
            session.pending_question = None
            session.pending_intent_type = None
            session.slots["pump_use"] = "полив"
            session.slots["pump_type"] = "дренажный"
            session.last_products = []
            return (
                "Да, верно. Циркуляционный насос — это про отопление и движение теплоносителя "
                "по контуру. Для полива смотрят насос по источнику воды: из бочки, ёмкости "
                "или для откачки чаще подходит дренажный; из скважины — скважинный; из колодца "
                "или для стабильной подачи в дом — поверхностный насос или насосная станция. "
                "Если подбираем именно для полива, уточните источник воды и примерную высоту "
                "подъёма/длину шланга."
            )

        if not session.last_products or not self._asks_pump_application_fit(text):
            return None

        pump_card = self._first_shown_pump_card(session)
        if not pump_card:
            return None

        kind = self._pump_kind_from_card(pump_card)
        if "полив" in text:
            detail = self._pump_card_details(pump_card)
            if kind == "дренажный":
                return (
                    f"{pump_card.name} можно рассматривать для полива из бочки, ёмкости, "
                    "дренажного приямка или другой не слишком глубокой воды, если хватает напора "
                    f"и производительности. {detail} Для скважины такой насос обычно не берут — "
                    "там нужен скважинный насос; для отопления нужен циркуляционный."
                )
            if kind == "скважинный":
                return (
                    f"{pump_card.name} подходит для подачи воды из скважины, в том числе дальше "
                    f"на полив, если хватает напора и расхода. {detail} Если вода берётся из "
                    "бочки или нужно просто откачать воду, чаще смотрят дренажный насос."
                )
            if kind == "циркуляционный":
                return (
                    f"{pump_card.name} для полива не подходит: циркуляционный насос работает в "
                    "замкнутом контуре отопления, а не как насос для забора воды из ёмкости, "
                    "скважины или колодца. Для полива лучше смотреть дренажный, скважинный, "
                    "поверхностный насос или насосную станцию по источнику воды."
                )
            return (
                f"Для полива по {pump_card.name} нужно смотреть источник воды, напор и расход. "
                "Из ёмкости или для откачки обычно берут дренажный насос, из скважины — "
                "скважинный, из колодца/для дома — поверхностный насос или насосную станцию."
            )

        if any(marker in text for marker in ["отоплен", "тепл", "тёпл"]):
            if kind == "циркуляционный":
                return (
                    f"{pump_card.name} относится к циркуляционным насосам и применяется в "
                    "отоплении: его подбирают по напору, расходу, монтажной длине и "
                    "присоединению."
                )
            return (
                f"{pump_card.name} не стоит рассматривать как насос для отопления. Для системы "
                "отопления нужен циркуляционный насос, а дренажные/скважинные насосы решают "
                "другие задачи."
            )

        if "скваж" in text:
            if kind == "скважинный":
                return (
                    f"{pump_card.name} как раз относится к скважинным насосам. Для точного "
                    "подбора нужны глубина скважины, зеркало воды, высота подъёма и нужный расход."
                )
            return (
                f"{pump_card.name} не является скважинным насосом. Для скважины нужен "
                "скважинный насос, который подбирают по глубине, напору и расходу."
            )
        return None

    def _maybe_misunderstanding_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if not any(
            marker in text
            for marker in [
                "не то ответил",
                "не так ответил",
                "неверно ответил",
                "неправильно ответил",
                "ты ошибся",
                "вы ошиблись",
            ]
        ):
            return None
        if session.category == "pumps":
            session.last_products = []
            return (
                "Понял, предыдущий ответ был не по вашей задаче. "
                "Разделим типы: циркуляционный насос — для отопления; "
                "для полива из ёмкости может подойти дренажный, а для скважины "
                "нужен скважинный. Напишите источник воды — продолжу подбор без "
                "привязки к предыдущему товару."
            )
        return (
            "Понял, предыдущий ответ был не по вашей задаче. Напишите, какую именно "
            "позицию или параметр нужно проверить, и я пересоберу подбор."
        )

    def _maybe_boiler_type_correction(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        is_correction = (
            ("не электр" in text and "газ" in text)
            or ("не газ" in text and "электр" in text)
            or ("же газ" in text and "кот" in text)
            or ("же электр" in text and "кот" in text)
        )
        if not is_correction:
            return None

        brand_aliases = {
            "ariston": {"ariston", "аристон"},
            "arderia": {"arderia", "ардерия"},
            "eca": {"eca", "e.c.a", "еса", "ека"},
        }

        def brand_is_mentioned(brand_value: str) -> bool:
            brand_norm = normalize_text(brand_value or "").replace(".", "")
            aliases = brand_aliases.get(brand_norm, {brand_norm})
            return any(alias and alias in text for alias in aliases)

        matched: list[ProductCard] = []
        for card in session.last_products:
            model_tokens = {
                token
                for token in re.findall(r"[a-zа-я0-9./\-]+", normalize_text(card.name))
                if len(token) >= 2 and re.search(r"[a-zа-я]", token) and re.search(r"\d", token)
            }
            if brand_is_mentioned(card.brand) or any(token in text for token in model_tokens):
                matched.append(card)
        was_shown = True
        if len(matched) == 1:
            card = matched[0]
        elif len(session.last_products) == 1:
            card = session.last_products[0]
        else:
            # The user may name a feed product that was present in an earlier answer
            # but not in the current top three. Resolve only one unambiguous catalog
            # match; never attach the correction to the first shown card.
            catalog_matches: list[Product] = []
            for candidate in self.search_agent.products:
                if self.search_agent.canonical_category(candidate) != "boilers":
                    continue
                model_tokens = {
                    token
                    for token in re.findall(
                        r"[a-zа-я0-9./\-]+", normalize_text(candidate.name)
                    )
                    if len(token) >= 2
                    and re.search(r"[a-zа-я]", token)
                    and re.search(r"\d", token)
                }
                if brand_is_mentioned(candidate.brand) or any(
                    token in text for token in model_tokens
                ):
                    catalog_matches.append(candidate)
            if len(catalog_matches) != 1:
                return None
            product = catalog_matches[0]
            built_card = self.card_agent.build_card(
                product,
                SearchQuery(
                    original_text=message,
                    category="boilers",
                    slots={"boiler_type": intent.slots.get("boiler_type")},
                ),
            )
            if not built_card:
                return None
            card = built_card
            was_shown = False

        product = self._find_product_by_sku(card.sku)
        if not product:
            return None
        actual_type = self._boiler_type_from_product(product)
        if not actual_type:
            return None
        opposite = "электрический" if actual_type == "газовый" else "газовый"

        claimed_type = normalize_text(str(intent.slots.get("boiler_type") or ""))
        if claimed_type and claimed_type != actual_type:
            # The feed wins over a weak classifier or an ambiguous correction.
            intent.slots["boiler_type"] = actual_type
        session.slots["boiler_type"] = actual_type
        session.last_products = [card]
        previous_selection_note = ""
        if not was_shown:
            previous_selection_note = (
                " В последней подборке были другие модели; эту позицию нашёл отдельно в ассортименте."
            )
        return (
            f"Да, вы правы: {card.name} — {actual_type} котёл, а не {opposite}. "
            "Предыдущая формулировка была ошибочной; тип сверил с карточкой товара."
            f"{previous_selection_note}",
            [card],
        )

    def _maybe_shown_boiler_type_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        refers_to_shown = bool(
            re.search(r"\b(он|этот|эта|это|модель)\b", text)
            or any(
                (
                    bool(brand := normalize_text(card.brand or ""))
                    and brand in text
                )
                or any(
                    token in text
                    for token in re.findall(
                        r"[a-zа-я]+\d+[a-zа-я0-9-]*", normalize_text(card.name)
                    )
                )
                for card in session.last_products
            )
        )
        asks_known_type = "электр" in text or "газ" in text
        asks_open_type = any(
            marker in text
            for marker in ["какой он", "какого он типа", "какой тип котла", "какой это котел", "какой это котёл"]
        )
        if not refers_to_shown or not (asks_known_type or asks_open_type):
            return None

        card, ambiguous = self._resolve_shown_product_card(message, session)
        if ambiguous or not card:
            typed_cards: list[tuple[ProductCard, str]] = []
            for candidate in session.last_products:
                product = self._find_product_by_sku(candidate.sku)
                actual = self._boiler_type_from_product(product) if product else None
                if actual:
                    typed_cards.append((candidate, actual))
            actual_types = {actual for _, actual in typed_cards}
            if typed_cards and len(actual_types) == 1:
                actual = typed_cards[0][1]
                intent.slots["boiler_type"] = actual
                session.slots["boiler_type"] = actual
                return (
                    f"Все показанные котлы — {actual}. Тип сверил с карточками товаров.",
                    session.last_products,
                )
            return None

        product = self._find_product_by_sku(card.sku)
        actual_type = self._boiler_type_from_product(product) if product else None
        if not actual_type:
            return None
        intent.slots["boiler_type"] = actual_type
        session.slots["boiler_type"] = actual_type
        if "электр" in text:
            prefix = "Да" if actual_type == "электрический" else "Нет"
        elif "газ" in text:
            prefix = "Да" if actual_type == "газовый" else "Нет"
        else:
            prefix = "По карточке товара"
        return (
            f"{prefix}: {card.name} — {actual_type} котёл. "
            "Тип взят из карточки товара, а не из предположения в вопросе.",
            [card],
        )

    def _boiler_type_from_product(self, product: Product | None) -> str | None:
        if not product:
            return None
        product_text = self.search_agent._product_text(product)
        if "газов" in product_text:
            return "газовый"
        if "электр" in product_text:
            return "электрический"
        return None

    def _is_pump_domain_correction(self, text: str) -> bool:
        return (
            "циркуляц" in text
            and "отоплен" in text
            and "полив" in text
            and any(marker in text for marker in ["дренаж", "скваж", "поверхност", "насосная станц"])
        )

    def _asks_pump_application_fit(self, text: str) -> bool:
        application_markers = ["полив", "отоплен", "тепл", "тёпл", "скваж", "колод", "водоснаб"]
        fit_markers = ["подойдет", "подходит", "пойдет", "годится", "можно", "нормально"]
        product_refs = ["он", "она", "этот", "эта", "его", "ее", "её", "такой", "такую"]
        return (
            any(marker in text for marker in application_markers)
            and any(marker in text for marker in fit_markers)
            and (any(ref in text for ref in product_refs) or "насос" in text)
        )

    def _first_shown_pump_card(self, session: SessionState) -> ProductCard | None:
        for card in session.last_products:
            product = self._find_product_by_sku(card.sku)
            if product and self.search_agent.canonical_category(product) == "pumps":
                return card
            if "насос" in normalize_text(card.name):
                return card
        return None

    def _pump_kind_from_card(self, card: ProductCard) -> str:
        card_text = normalize_text(
            " ".join(
                [
                    card.name,
                    *[f"{key} {value}" for key, value in card.characteristics.items()],
                ]
            )
        )
        if "дренаж" in card_text:
            return "дренажный"
        if "скваж" in card_text:
            return "скважинный"
        if "циркуляц" in card_text:
            return "циркуляционный"
        if "насосная станц" in card_text:
            return "насосная станция"
        if "поверхност" in card_text:
            return "поверхностный"
        return "насос"

    def _pump_card_details(self, card: ProductCard) -> str:
        details = []
        for key, value in card.characteristics.items():
            key_norm = normalize_text(key)
            if any(marker in key_norm for marker in ["напор", "производ", "мощность", "глубин"]):
                details.append(f"{key}: {value}")
        if not details:
            return "В карточке мало параметров, поэтому точность лучше проверить по напору и расходу."
        return "По карточке: " + "; ".join(details[:4]) + "."

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

    def _maybe_redundant_filter_confirmation(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        """A repeated short filter confirms the current list instead of re-deciding it."""
        if not session.last_products or intent.category != "boilers" or session.category != "boilers":
            return None
        text = normalize_text(message).strip(" .,!?:;")
        if any(
            marker in text
            for marker in [
                "что",
                "какой",
                "почему",
                "лучше",
                "сравн",
                "сколько",
                "стоит",
                "цена",
                "самый",
                "перв",
                "втор",
                "трет",
                "?",
            ]
        ):
            return None

        checks: list[tuple[str, str]] = []
        if intent.slots.get("contours") and any(
            marker in text for marker in ["одноконтур", "двухконтур"]
        ):
            checks.append(("контурность", normalize_text(str(intent.slots["contours"]))))
        if intent.slots.get("boiler_type") and any(
            marker in text for marker in ["газов", "электрическ"]
        ):
            checks.append(("тип котла", normalize_text(str(intent.slots["boiler_type"]))))
        if not checks:
            return None

        product_texts: list[str] = []
        for card in session.last_products:
            product = self._find_product_by_sku(card.sku)
            if not product:
                return None
            product_texts.append(self.search_agent._product_text(product))
        for label, value in checks:
            if value and all(value in product_text for product_text in product_texts):
                return (
                    f"Да, параметр уже учтён: {label} — {value}. "
                    "Показанную подборку не меняю; можете попросить сравнить варианты или выбрать один."
                )
        return None

    def _maybe_required_boiler_clarification(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Keep price/stock questions from bypassing an unfinished boiler funnel."""
        if session.category != "boilers" or session.last_products or not session.pending_question:
            return None
        text = normalize_text(message)
        if not any(
            marker in text
            for marker in ["сколько", "стоит", "стоют", "цена", "цену", "наличи", "на складе"]
        ):
            return None
        pending = normalize_text(session.pending_question)
        if "площад" in pending and not session.slots.get("area_m2"):
            return (
                "Чтобы показать цены релевантных котлов, сначала нужна площадь: "
                "на сколько м² подбираете котёл?"
            )
        if "контур" in pending and not session.slots.get("contours"):
            return (
                "Чтобы показать цены подходящих моделей, сначала уточните: одноконтурный "
                "котёл (только отопление) или двухконтурный (отопление и горячая вода)?"
            )
        if "газов" in pending and "электр" in pending and not session.slots.get("boiler_type"):
            return (
                "Чтобы показать цены подходящих моделей, сначала уточните тип котла: "
                "газовый или электрический?"
            )
        return None

    # --- Консультант (RAG) ---------------------------------------------------

    CONSULT_MARKERS = [
        "построить",
        "строю",
        "коттедж",
        "посовет",
        "что ещё",
        "что еще",
        "в итоге",
        "комплект",
        "под ключ",
        "закрыть",
        "что нужно",
        "что ещё нужно",
        "есть другие",
        "есть ещё",
        "есть еще",
        "встроен",
        "рециркуляц",
        "обвяз",
        "котельн",
        "инженерн",
        # запрос на ведение/подбор «под ключ»
        "помоги",
        "помогите",
        "подбери",
        "подберите",
        "сориентир",
        "ориентир",
        "составь",
        "составить список",
        "список того",
        "выбрать все",
        "выбрать всё",
        "выбери все",
        "выбери всё",
        "давай все",
        "давай всё",
        "нужно все",
        "нужно всё",
        "как скажешь",
        "на твое усмотрение",
        "на твоё усмотрение",
        "сам реши",
        "что зачем",
    ]

    # Системные слова — заявка на ведение подбора, а не конкретный товар.
    CONSULT_SYSTEM_WORDS = [
        "отоплен",
        "водоснаб",
        "водопровод",
        "канализац",
        "теплый пол",
        "тёплый пол",
        "тепл",
    ]

    def _maybe_consult(
        self,
        session_id: str,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ):
        # Без настроенной LLM консультант недоступен — идём детерминированным путём
        # (так офлайн-тесты и rule-based fallback остаются прежними).
        if not self.settings.llm_enabled:
            return None
        if not self._should_consult(message, intent, session):
            return None

        self._update_project_state(message, intent, session)
        categories, retrieval_slots = self._consult_plan(message, intent, session)
        retrieved: list[Product] = []
        if categories:
            retrieved = self.search_agent.retrieve_for_consult(
                categories, retrieval_slots, per_category=4
            )
        # Уже показанные товары держим в каталоге, чтобы follow-up'ы («а в котле
        # встроенный насос?») не противоречили ранее предложенному.
        seen = {normalize_sku_token(p.sku) for p in retrieved}
        for card in session.last_products[:3]:
            product = self._find_product_by_sku(card.sku)
            if product and normalize_sku_token(product.sku) not in seen:
                retrieved.insert(0, product)
                seen.add(normalize_sku_token(product.sku))

        agents_used.append("ConsultantAgent")
        result = self.consultant.respond(message, session, retrieved, session.history)
        if not result.llm_used:
            # LLM не ответила — откатываемся к обычному пайплайну.
            agents_used.pop()
            return None

        answer = result.answer
        cards = result.cards
        # Если ответ не прошёл проверку достоверности — не показываем возможный бред
        # модели, а собираем чистый список реальных товаров из фида.
        if not result.grounded:
            # Берём корректно отсортированную выдержку из фида, а не то, что мог
            # неудачно выбрать слабый LLM.
            fallback_cards = self.card_agent.build_cards(
                retrieved, self._build_query(message, intent, session), limit=5
            ) or cards
            if fallback_cards:
                answer = self._plain_catalog_answer(fallback_cards)
                cards = fallback_cards

        cards = self._limit_oversized_boiler_cards(
            cards,
            self._float_slot(session.slots.get("area_m2")),
            message,
        )
        sizing_warning = self._consult_boiler_sizing_warning(session, cards)
        if sizing_warning:
            # Do not leave a contradictory free-form claim such as «идеально
            # подходит» after the warning.  In this edge case the catalog list plus
            # deterministic sizing caveat is safer than a polished recommendation.
            answer = (
                sizing_warning
                + "\n"
                + self._plain_catalog_answer(cards, include_followup=False)
                + "\nЧтобы оценить модель точнее, уточните: это дом или квартира, "
                "какой регион, высота потолков и насколько хорошо утеплено здание?"
            )

        if cards:
            session.last_products = cards
        # Запоминаем основную категорию, чтобы follow-up'ы держали контекст.
        primary = self._primary_session_category(categories)
        if primary:
            session.category = primary
        session.last_intent = "consult"
        session.pending_question = None
        session.pending_intent_type = None

        self._append_history(session, message, answer)
        need_handoff = not result.grounded and not cards
        return self._response(
            session_id, answer, cards, need_handoff, intent, session, agents_used
        )

    def _consult_boiler_sizing_warning(
        self,
        session: SessionState,
        cards: list[ProductCard],
    ) -> str | None:
        area = self._float_slot(session.slots.get("area_m2"))
        if not area or not cards:
            return None
        boiler_entries: list[tuple[float, Product, ProductCard]] = []
        for card in cards:
            product = self._find_product_by_sku(card.sku)
            if not product or self.search_agent.canonical_category(product) != "boilers":
                continue
            power = self.guardrails._extract_power_kw(product)
            if power is not None:
                boiler_entries.append((power, product, card))
        if not boiler_entries:
            return None
        base_kw = area / 10.0
        upper_kw = base_kw * 1.3
        rated_power, product, card = min(boiler_entries, key=lambda item: item[0])
        if rated_power <= upper_kw * 1.25:
            return None
        label = self._short_product_label(product, card)
        passport_range = self._boiler_passport_output_range(product)
        introduction = (
            f"Для {area:g} м² предварительный ориентир по мощности — примерно "
            f"{base_kw:g}–{upper_kw:g} кВт. Точный подбор зависит от региона, утепления, "
            "высоты потолков и теплопотерь. "
            f"Из найденных моделей самая маломощная — {label} на {rated_power:g} кВт."
        )
        if not passport_range:
            return (
                introduction
                + " Её максимальная мощность выше предварительного ориентира, поэтому без "
                "минимальной мощности и расчёта теплопотерь не называю эту модель оптимальной."
            )
        minimum, maximum, source = passport_range
        min_text = f"{minimum:g}".replace(".", ",")
        max_text = f"{maximum:g}".replace(".", ",")
        source_note = f" ({source})" if source else ""
        return (
            introduction
            + f" По техническому паспорту её теплопроизводительность в режиме отопления "
            f"регулируется примерно от {min_text} до {max_text} кВт{source_note}, поэтому "
            f"котёл не работает постоянно на всех {rated_power:g} кВт. При этом в тёплую "
            f"погоду потребность здания может опускаться ниже {min_text} кВт — тогда возможны "
            "более частые включения и выключения."
        )

    def _boiler_passport_output_range(
        self,
        product: Product,
    ) -> tuple[float, float, str | None] | None:
        minimum: float | None = None
        maximum: float | None = None
        source: str | None = None
        for key, value in product.attributes_normalized.items():
            key_norm = normalize_text(key)
            number_match = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if "теплопроизводительность отопления" in key_norm and number_match:
                number = float(number_match.group(0).replace(",", "."))
                if "мин" in key_norm:
                    minimum = number
                elif "макс" in key_norm:
                    maximum = number
            elif "источник диапазона мощности" in key_norm:
                source = str(value)
        if minimum is None or maximum is None or minimum > maximum:
            return None
        return minimum, maximum, source

    def _short_product_label(self, product: Product, card: ProductCard) -> str:
        model_match = re.search(
            r"\b(?:SB|D|B)\s?\d{2}\b",
            product.name,
            re.IGNORECASE,
        )
        if product.brand and model_match:
            model = re.sub(r"\s+", "", model_match.group(0)).upper()
            return f"{product.brand} {model}"
        return card.name

    def _plain_catalog_answer(
        self,
        cards: list[ProductCard],
        *,
        include_followup: bool = True,
    ) -> str:
        import html

        lines = ["Рассматриваемая модель:" if len(cards) == 1 else "Найденные варианты:"]
        for card in cards:
            stock = card.stock_status
            if card.stock_qty is not None and card.stock_qty > 0:
                stock = f"в наличии {card.stock_qty} шт"
            lines.append(
                f"• {html.unescape(card.name)} — арт. {card.sku}, {card.price:g} {card.currency}, "
                f"{stock}. {card.url}"
            )
        if include_followup:
            if len(cards) == 1:
                lines.append(
                    "Могу проверить, насколько эта модель подходит именно для вашей задачи, "
                    "или подобрать необходимую обвязку."
                )
            else:
                lines.append(
                    "Могу сравнить эти модели по важным для вашей задачи характеристикам "
                    "или подобрать необходимую обвязку."
                )
        return "\n".join(lines)

    def _should_consult(self, message: str, intent: IntentResult, session: SessionState) -> bool:
        text = normalize_text(message)

        # Точный артикул и запрос ссылки — детерминированные пути, мимо консультанта.
        if intent.intent_type in {"exact_sku", "link_request"}:
            return False
        if intent.slots.get("sku") or session.slots.get("sku"):
            return False

        if (
            session.pending_question
            and intent.category == "boilers"
            and {"area_m2", "power_kw", "boiler_type", "contours"}.intersection(intent.slots)
        ):
            return False

        if self._is_explicit_boiler_product_request(text, intent):
            return False

        concrete_non_boiler = {
            "pumps",
            "pipes",
            "fittings",
            "valves",
            "sewer",
            "radiators",
            "radiator_fittings",
        }
        broad_markers = [
            "что ещё",
            "что еще",
            "в итоге",
            "комплект",
            "под ключ",
            "что нужно",
            "собери",
            "подбери",
            "подберите",
            "корзин",
        ]
        concrete_slot_keys = {
            "diameter_mm",
            "sewer_scope",
            "element_type",
            "length_mm",
            "pipe_purpose",
            "pump_type",
            "pump_use",
            "head_m",
            "mounting_length_mm",
            "size_inch",
            "application",
        }
        if intent.category in concrete_non_boiler and concrete_slot_keys.intersection(intent.slots):
            if not any(marker in text for marker in broad_markers):
                return False
        if intent.category in concrete_non_boiler and any(
            word in text for word in SPECIFIC_PRODUCT_WORDS
        ):
            if not any(marker in text for marker in broad_markers):
                return False

        # Явные консультативные/проектные маркеры перебивают даже ошибочную
        # классификацию интента («дом построить» иногда уходит в out_of_scope).
        if any(marker in text for marker in self.CONSULT_MARKERS):
            return True

        # Идёт проектный разговор — продолжаем у консультанта.
        if any(session.slots.get(key) for key in ("project", "heat_sources")):
            return True
        if session.last_intent == "consult" and intent.category != "other":
            return True

        # Момент рекомендации котла: дана площадь/мощность или источник тепла.
        if intent.category == "boilers" and (
            intent.slots.get("area_m2")
            or intent.slots.get("power_kw")
            or "газ" in text
            or "электр" in text
        ):
            return True

        # Системная заявка («мне нужно отопление», «хочу тёплый пол») без точного
        # товара — это просьба повести подбор, ведёт консультант, а не воронка.
        if any(word in text for word in self.CONSULT_SYSTEM_WORDS):
            return True

        # Короткое согласие/«давай»/«начнём» в активном проектном контексте —
        # продолжаем у консультанта, чтобы не зациклить воронку.
        if session.last_intent in {"consult", "broad_category"} and any(
            token in text for token in ["давай", "начн", "ок", "да", "хорошо", "поехали", "погнали"]
        ):
            return True

        # «А насосы есть?», «канализация тоже есть?» — каталожный вопрос через «есть».
        category_words = [
            "котел",
            "котёл",
            "насос",
            "труб",
            "кран",
            "канализац",
            "радиатор",
            "фитинг",
            "бойлер",
        ]
        if "есть" in text and any(word in text for word in category_words):
            return True

        # Чистый small talk без проектного контекста — не к консультанту.
        return False

    def _is_explicit_boiler_product_request(self, text: str, intent: IntentResult) -> bool:
        if intent.category != "boilers":
            return False
        if not any(word in text for word in ["котел", "котёл", "котл", "кател"]):
            return False
        return bool(
            intent.slots.get("boiler_type")
            or intent.slots.get("contours")
            or intent.slots.get("area_m2")
            or intent.slots.get("power_kw")
        )

    # Формулировки, означающие отсутствие газа.
    NO_GAS_MARKERS = ["газа нет", "газа нету", "без газа", "нет газа", "не газ", "газ отсутств"]

    def _update_project_state(self, message: str, intent: IntentResult, session: SessionState) -> None:
        text = normalize_text(message)
        if intent.slots.get("area_m2"):
            session.slots["area_m2"] = intent.slots["area_m2"]
        if intent.slots.get("boiler_type"):
            session.slots["boiler_type"] = intent.slots["boiler_type"]
        if intent.slots.get("contours"):
            session.slots["contours"] = intent.slots["contours"]

        # Источники тепла с учётом отрицания: «газа нет» → has_gas=False, а не +газ.
        no_gas = any(marker in text for marker in self.NO_GAS_MARKERS)
        if no_gas:
            session.slots["has_gas"] = False
        elif "газ" in text:
            session.slots["has_gas"] = True
        if "электр" in text or no_gas:
            # «газа нет» обычно подразумевает электрическую котельную.
            session.slots["has_electricity"] = True

        sources: list[str] = []
        if session.slots.get("has_gas") is True:
            sources.append("газ")
        if session.slots.get("has_electricity") is True:
            sources.append("электричество")
        if session.slots.get("has_gas") is False:
            sources.append("газа нет")
        if sources:
            session.slots["heat_sources"] = ", ".join(dict.fromkeys(sources))
        if session.slots.get("has_gas") is True and session.slots.get("has_electricity") is True:
            mentions_both_sources = "газ" in text and "электр" in text
            if mentions_both_sources:
                session.slots.pop("boiler_type", None)
                session.slots["boiler_types"] = ["газовый", "электрический"]

        if any(word in text for word in ["дом", "коттедж", "построить", "строю"]):
            session.slots.setdefault("project", "частный дом")
        if "водян" in text and "пол" in text:
            session.slots["warm_floor_type"] = "водяной"
        elif "от котл" in text and session.slots.get("project_scope") == "warm_floor":
            session.slots["warm_floor_type"] = "водяной"
        elif "электр" in text and "пол" in text:
            session.slots["warm_floor_type"] = "электрический"
        elif text.strip(" .,!?:;") in {"водяной", "водяной от котла", "электрический"}:
            if session.slots.get("project_scope") == "warm_floor":
                session.slots["warm_floor_type"] = (
                    "электрический" if "электр" in text else "водяной"
                )
        if "скваж" in text:
            session.slots["water_source"] = "скважина"
        elif "колод" in text:
            session.slots["water_source"] = "колодец"
        elif "центральн" in text:
            session.slots["water_source"] = "центральный водопровод"

    def _consult_plan(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[list[str], dict]:
        text = normalize_text(message)
        slots = dict(session.slots)
        # boiler_type берём из intent_router — там отрицание («газа нет») уже учтено.
        # НЕ выводим тип из подстроки «газ» (иначе «газа нет» → газовый, баг из логов).
        if intent.slots.get("boiler_type"):
            slots["boiler_type"] = intent.slots["boiler_type"]
        if intent.slots.get("contours"):
            slots["contours"] = intent.slots["contours"]
        # Если по проекту газа нет — это электрическая котельная, чем бы ни был тип.
        if slots.get("has_gas") is False and not slots.get("boiler_type"):
            slots["boiler_type"] = "электрический"
        if slots.get("has_gas") is True and slots.get("has_electricity") is True:
            current_mentions_only_electric = "электр" in text and "газ" not in text
            current_mentions_only_gas = "газ" in text and "электр" not in text
            if current_mentions_only_electric:
                slots["boiler_type"] = "электрический"
                slots.pop("boiler_types", None)
            elif current_mentions_only_gas:
                slots["boiler_type"] = "газовый"
                slots.pop("boiler_types", None)
            else:
                slots.pop("boiler_type", None)
                slots["boiler_types"] = ["газовый", "электрический"]

        named: list[str] = []
        if "котел" in text or "котёл" in text or "котельн" in text:
            named.append("boilers")
        if "насос" in text:
            named.append("pumps")
        if "труб" in text and "канализац" not in text:
            named.append("pipes")
        if "кран" in text or "вентил" in text:
            named.append("valves")
        if "канализац" in text:
            named.append("sewer")
        if "радиатор" in text:
            named.append("radiators")
        if "фитинг" in text:
            named.append("fittings")

        broad = any(
            marker in text
            for marker in [
                "что ещё",
                "что еще",
                "в итоге",
                "комплект",
                "что нужно",
                "под ключ",
                "закрыть",
                "выбрать все",
                "выбрать всё",
                "давай все",
                "давай всё",
                "нужно все",
                "нужно всё",
                "все и",
                "всё и",
                "список",
            ]
        )
        # Тёплый пол — это трубы + насос (+ арматура).
        warm_floor = (
            ("теплый пол" in text or "тёплый пол" in text or ("тепл" in text and "пол" in text))
            and not self._negates_warm_floor(text)
        )
        if warm_floor and not named:
            return ["pipes", "pumps", "valves"], slots
        if broad:
            return ["boilers", "pumps", "pipes", "valves", "sewer"], slots
        # Вопрос про встроенный насос котла — нужен и котёл, и насосы в контексте.
        if "встроен" in text and "pumps" not in named:
            named = ["boilers", "pumps"]
        if named:
            return named, slots
        # Проект известен (площадь/источник), товар не назван — стартуем с котла.
        if session.slots.get("area_m2") or session.slots.get("heat_sources"):
            return ["boilers"], slots
        return [], slots

    def _primary_session_category(self, categories: list[str]) -> str | None:
        mapping = {
            "boilers": "boilers",
            "pumps": "pumps",
            "pipes": "pipes",
            "valves": "valves",
            "sewer": "sewer",
            "radiators": "radiators",
            "fittings": "fittings",
        }
        for category in categories:
            if category in mapping:
                return mapping[category]
        return None

    # --- Проектная подборка / корзина -----------------------------------------

    def _maybe_project_cart_response(
        self,
        session_id: str,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        text = normalize_text(message)
        if (
            session.category
            and session.category != "other"
            and not session.slots.get("project_scope")
            and not self._wants_project_selection(text)
            and not self._wants_project_cart_summary(text)
        ):
            return None
        explicit_scope = self._explicit_project_scope_from_text(text)
        if explicit_scope:
            self._reset_project_context_if_scope_changed(explicit_scope, session)
        self._update_project_state(message, intent, session)
        area = self._project_area_from_text(text)
        if area is not None:
            session.slots["area_m2"] = area

        if self._wants_project_cart_summary(text) and not explicit_scope:
            cards = self._project_cart_cards(session)
            if not cards and session.last_products:
                cards = session.last_products
                self._remember_project_cart(session, cards)
            if cards:
                answer = self._compose_project_cart_summary(session, cards)
                agents_used.append("ResponseComposerAgent")
                session.last_products = cards
                session.last_intent = "project_cart"
                session.pending_question = None
                session.pending_intent_type = None
                self._append_history(session, message, answer)
                return self._response(session_id, answer, cards, False, intent, session, agents_used)

        scope = explicit_scope or self._project_scope_from_message(text, session)
        if not scope:
            return None
        if session.slots.get("project_cart") and self._wants_more_project_components(text):
            cards = self._project_cart_cards(session) or session.last_products
            answer = self._compose_project_next_steps(scope, cards)
            agents_used.append("ResponseComposerAgent")
            session.last_products = cards
            session.last_intent = "project_cart"
            self._append_history(session, message, answer)
            return self._response(session_id, answer, cards, False, intent, session, agents_used)
        if not self._should_handle_project_cart(text, intent, session):
            intro = self._project_intro_for_scope(scope, text)
            if intro and not any(word in text for word in SPECIFIC_PRODUCT_WORDS):
                session.slots["scope_funnel"] = scope
                session.pending_question = intro
                session.pending_intent_type = "broad_category"
                agents_used.append("ResponseComposerAgent")
                self._append_history(session, message, intro)
                return self._response(session_id, intro, [], False, intent, session, agents_used)
            return None

        session.slots["project_scope"] = scope
        session.slots["scope_funnel"] = scope

        clarification = self._project_clarification(scope, session, text)
        if clarification:
            agents_used.append("ResponseComposerAgent")
            session.pending_question = clarification
            session.pending_intent_type = "broad_category"
            session.last_intent = "project_cart"
            self._append_history(session, message, clarification)
            return self._response(session_id, clarification, [], False, intent, session, agents_used)

        cards_by_category = self._project_cards_by_category(scope, message, session)
        cards = [
            card
            for category in PROJECT_CART_CATEGORY_ORDER
            for card in cards_by_category.get(category, [])
        ]
        if not cards:
            answer = (
                "В текущем ассортименте не вижу товаров, из которых можно безопасно собрать подборку "
                "по артикулам. Не буду придумывать позиции. Уточните систему или напишите "
                "«передай менеджеру» — зафиксирую задачу."
            )
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, answer)
            return self._response(session_id, answer, [], True, intent, session, agents_used)

        self._remember_project_cart(
            session,
            cards,
            replace_categories=list(cards_by_category),
        )
        session.last_products = cards
        session.last_intent = "project_cart"
        session.category = self._primary_session_category(list(cards_by_category)) or session.category
        session.pending_question = None
        session.pending_intent_type = None
        answer = self._compose_project_selection_answer(scope, cards_by_category, session)
        agents_used.append("FeedSearchAgent")
        agents_used.append("ProductCardAgent")
        agents_used.append("ResponseComposerAgent")
        self._append_history(session, message, answer)
        return self._response(session_id, answer, cards, False, intent, session, agents_used)

    def _explicit_project_scope_from_text(self, text: str) -> str | None:
        if "тепл" in text and "пол" in text and not self._negates_warm_floor(text):
            return "warm_floor"
        if ("вод" in text or "водоснаб" in text) and "канализац" in text:
            return "bathroom"
        if any(marker in text for marker in ["ванн", "санузел", "санузла"]):
            return "bathroom"
        if "котельн" in text or "отоплен" in text:
            return "heating"
        if "водоснаб" in text or "водопровод" in text:
            return "water"
        if "канализац" in text:
            return "sewer"
        if any(marker in text for marker in ["сантехник", "инженерн", "для дома", "в дом"]):
            return "general"
        return None

    def _negates_warm_floor(self, text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:без|не|кроме|исключи|убери|только\s+не)\s+[^.?!,;]{0,24}(?:тепл|тёпл)[^,.?!;]{0,12}пол",
                text,
            )
        )

    def _project_area_from_text(self, text: str) -> float | None:
        match = re.search(r"(\d{2,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)", text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _project_intro_for_scope(self, scope: str, text: str = "") -> str | None:
        if scope == "bathroom" and self._negates_warm_floor(text):
            return (
                "Ок, без тёплого пола. Для ванной/санузла оставляем водоснабжение и "
                "канализацию: трубы, краны/запорную арматуру и канализационные элементы. "
                "Начнём с размеров труб/точек воды или с канализации?"
            )
        return {
            "warm_floor": WARM_FLOOR_FUNNEL,
            "heating": HEATING_FUNNEL,
            "water": WATER_SUPPLY_FUNNEL,
            "bathroom": BATHROOM_FUNNEL,
            "general": GENERAL_FUNNEL,
        }.get(scope)

    def _reset_project_context_if_scope_changed(
        self,
        new_scope: str,
        session: SessionState,
    ) -> None:
        old_scope = session.slots.get("project_scope") or session.slots.get("scope_funnel")
        if not old_scope or old_scope == new_scope:
            return
        for key in [
            "project_cart",
            "project_scope",
            "scope_funnel",
            "area_m2",
            "power_kw",
            "heat_sources",
            "has_gas",
            "has_electricity",
            "boiler_type",
            "boiler_types",
            "contours",
            "pump_type",
            "pump_use",
            "project_note",
            "warm_floor_type",
            "pipe_purpose",
            "water_source",
            "element_type",
            "sewer_scope",
            "length_mm",
            "total_length_m",
            "diameter_mm",
            "secondary_diameter_mm",
            "radiator_size_mm",
            "sections",
        ]:
            session.slots.pop(key, None)
        session.last_products = []
        session.category = None
        session.pending_question = None
        session.pending_intent_type = None

    def _project_scope_from_message(self, text: str, session: SessionState) -> str | None:
        explicit = self._explicit_project_scope_from_text(text)
        if explicit:
            return explicit
        if session.slots.get("project_scope") and (
            self._is_project_followup(text) or self._is_project_source_followup(text)
        ):
            return str(session.slots["project_scope"])
        if session.slots.get("scope_funnel") and (
            self._is_project_followup(text) or self._is_project_source_followup(text)
        ):
            return str(session.slots["scope_funnel"])
        return None

    def _should_handle_project_cart(
        self,
        text: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        if self._wants_project_cart_summary(text):
            return True
        if self._wants_project_selection(text):
            return True
        if self._is_project_parameter_followup(text, intent, session):
            return True
        if session.slots.get("project_cart") and any(
            marker in text
            for marker in ["что еще", "что ещё", "еще нужно", "ещё нужно", "дальше", "продолж"]
        ):
            return True
        return False

    def _wants_project_selection(self, text: str) -> bool:
        markers = [
            "что нужно",
            "что для этого нужно",
            "что еще нужно",
            "что ещё нужно",
            "собери",
            "собрать",
            "подбери",
            "подберите",
            "подборк",
            "комплект",
            "корзин",
            "по артикул",
            "артикул",
            "под ключ",
            "все для",
            "всё для",
            "все что нужно",
            "всё что нужно",
            "мы же собирали",
        ]
        if any(marker in text for marker in markers):
            return True
        return text.strip(" .,!?:;") in {"все", "всё", "комплектом", "полностью"}

    def _wants_project_cart_summary(self, text: str) -> bool:
        summary_markers = [
            "собери артикул",
            "собрать артикул",
            "список артикул",
            "артикулы списком",
            "артикулы с тем",
            "как корзин",
            "в корзин",
            "корзин",
            "итог",
            "что обсудили",
        ]
        return any(marker in text for marker in summary_markers)

    def _wants_more_project_components(self, text: str) -> bool:
        return any(
            marker in text
            for marker in [
                "что еще нужно",
                "что ещё нужно",
                "а еще что",
                "а ещё что",
                "что дальше",
                "еще нужно",
                "ещё нужно",
            ]
        )

    def _is_project_followup(self, text: str) -> bool:
        if self._wants_project_selection(text) or self._wants_project_cart_summary(text):
            return True
        if any(marker in text for marker in ["водяной", "электрический пол", "от котла"]):
            return True
        return bool(re.search(r"\d{2,4}\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)", text))

    def _is_project_source_followup(self, text: str) -> bool:
        return any(marker in text for marker in ["скваж", "колод", "центральн", "водопровод"])

    def _is_project_parameter_followup(
        self,
        text: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        if not (session.slots.get("project_scope") or session.slots.get("scope_funnel")):
            return False
        if intent.slots.get("area_m2"):
            return True
        if self._is_project_source_followup(text):
            return True
        if session.slots.get("project_scope") == "warm_floor" and any(
            marker in text for marker in ["водян", "электр", "от котл"]
        ):
            return True
        return bool(re.search(r"\d{2,4}\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)", text))

    def _is_project_component_turn(self, intent: IntentResult, session: SessionState) -> bool:
        if not (session.slots.get("project_cart") or session.slots.get("project_scope")):
            return False
        return intent.category in {
            "boilers",
            "pumps",
            "pipes",
            "valves",
            "sewer",
            "radiator_fittings",
            "radiators",
            "fittings",
        }

    def _project_clarification(
        self,
        scope: str,
        session: SessionState,
        text: str,
    ) -> str | None:
        if scope == "warm_floor" and not session.slots.get("area_m2"):
            if "что нужно" in text:
                return WARM_FLOOR_FUNNEL
            return WARM_FLOOR_ALL_FOLLOWUP
        if scope == "warm_floor" and not session.slots.get("warm_floor_type"):
            return (
                "Площадь учёл. Уточните ещё один обязательный параметр: тёплый пол водяной "
                "от котла или электрический? Не буду подставлять насос и трубы без выбора типа."
            )
        if scope == "warm_floor" and session.slots.get("warm_floor_type") == "электрический":
            return (
                "В текущем ассортименте не вижу электрических нагревательных матов или кабеля и "
                "терморегуляторов. Не буду подставлять вместо них трубы и насос водяного пола; "
                "наличие электрического комплекта нужно уточнить у менеджера."
            )
        if scope == "heating" and not (
            session.slots.get("area_m2")
            or session.slots.get("heat_sources")
            or session.slots.get("boiler_type")
        ):
            return HEATING_ALL_FOLLOWUP
        if scope == "water" and not (
            session.slots.get("water_source")
            or session.slots.get("pump_use")
            or session.slots.get("project_cart")
        ):
            return WATER_SUPPLY_ALL_FOLLOWUP
        if scope == "general" and not (
            session.slots.get("area_m2")
            or session.slots.get("heat_sources")
            or session.slots.get("project_cart")
        ):
            return GENERAL_ALL_FOLLOWUP
        return None

    def _project_cards_by_category(
        self,
        scope: str,
        message: str,
        session: SessionState,
    ) -> dict[str, list[ProductCard]]:
        categories = list(PROJECT_SCOPE_CATEGORIES.get(scope, PROJECT_SCOPE_CATEGORIES["general"]))
        if scope == "water" and session.slots.get("water_source") == "центральный водопровод":
            categories = [category for category in categories if category != "pumps"]
        slots = self._project_retrieval_slots(scope, session)
        result: dict[str, list[ProductCard]] = {}
        for category in categories:
            per_category = 2 if category == "boilers" and slots.get("boiler_types") else 1
            category_slots = dict(slots)
            if category == "sewer" and scope in {"bathroom", "sewer"}:
                category_slots.setdefault("element_type", "труба")
            products = self.search_agent.retrieve_for_consult(
                [category],
                category_slots,
                per_category=per_category,
            )
            cards = self.card_agent.build_cards(
                products,
                SearchQuery(original_text=message, category=category, slots=category_slots),
                limit=per_category,
            )
            if cards:
                result[category] = cards
        return result

    def _project_retrieval_slots(self, scope: str, session: SessionState) -> dict:
        slots = dict(session.slots)
        if scope in {"warm_floor", "heating"}:
            slots.setdefault("pump_type", "циркуляционный")
            slots.setdefault("pump_use", "отопление")
        if scope == "warm_floor":
            slots.setdefault("pipe_purpose", "отопление")
            slots.setdefault("project_note", "водяной тёплый пол")
        if scope == "water":
            slots.setdefault("pipe_purpose", "водоснабжение")
            source = normalize_text(str(slots.get("water_source") or ""))
            if "скваж" in source:
                slots.setdefault("pump_type", "скважинный")
                slots.setdefault("pump_use", "водоснабжение")
            elif "колод" in source:
                slots.setdefault("pump_type", "насосная станция")
                slots.setdefault("pump_use", "водоснабжение")
        if scope == "bathroom":
            slots.setdefault("pipe_purpose", "водоснабжение")
        return slots

    def _remember_project_cart(
        self,
        session: SessionState,
        cards: list[ProductCard],
        replace_category: str | None = None,
        replace_categories: list[str] | None = None,
    ) -> None:
        if not cards:
            return
        if not session.slots.get("project_scope") and not session.slots.get("project_cart"):
            return
        raw_cart = session.slots.get("project_cart") or {}
        cart: dict[str, list[str]] = {
            str(category): [str(sku) for sku in skus]
            for category, skus in raw_cart.items()
            if isinstance(skus, list)
        }
        categories_to_replace = set(replace_categories or [])
        if replace_category and replace_category != "other":
            categories_to_replace.add(replace_category)
        for category in categories_to_replace:
            cart[category] = []
        for card in cards:
            product = self._find_product_by_sku(card.sku)
            category = self.search_agent.canonical_category(product) if product else replace_category
            if not category or category == "other":
                continue
            # Фитинги добавляем только когда их явно подобрали отдельным запросом; в
            # широкую корзину они не попадают автоматически, чтобы не повторять баг с угольниками.
            if category == "fittings" and "fittings" not in categories_to_replace:
                continue
            bucket = cart.setdefault(category, [])
            if card.sku not in bucket:
                bucket.append(card.sku)
        session.slots["project_cart"] = cart

    def _project_cart_cards(self, session: SessionState) -> list[ProductCard]:
        cart = session.slots.get("project_cart") or {}
        if not isinstance(cart, dict):
            return []
        cards: list[ProductCard] = []
        seen: set[str] = set()
        for category in PROJECT_CART_CATEGORY_ORDER:
            skus = cart.get(category, [])
            if not isinstance(skus, list):
                continue
            for sku in skus:
                product = self._find_product_by_sku(str(sku))
                if not product or sku in seen:
                    continue
                card = self.card_agent.build_card(
                    product,
                    SearchQuery(
                        original_text="корзина проекта",
                        category=category,
                        slots=session.slots,
                    ),
                )
                if card:
                    cards.append(card)
                    seen.add(str(sku))
        return cards

    def _compose_project_selection_answer(
        self,
        scope: str,
        cards_by_category: dict[str, list[ProductCard]],
        session: SessionState,
    ) -> str:
        scope_label = PROJECT_SCOPE_LABELS.get(scope, "подбора")
        lines = [
            f"Хорошо, собираю стартовую подборку для {scope_label}.",
            "Это не окончательная инженерная спецификация: количества труб, контуров и расходников нужно считать по схеме. Цены и наличие ниже сверены с карточками товаров.",
        ]
        area = session.slots.get("area_m2")
        if area:
            lines.append(f"Площадь {float(area):g} м² учёл как исходный параметр, но метраж трубы без схемы не рассчитываю.")

        for category in PROJECT_CART_CATEGORY_ORDER:
            cards = cards_by_category.get(category)
            if not cards:
                continue
            label = PROJECT_CATEGORY_LABELS.get(category, category)
            reason = self._project_category_reason(category, scope, session)
            for card in cards:
                lines.append(
                    f"{label}: {html.unescape(card.name)} — арт. {card.sku}, "
                    f"{card.price:g} {card.currency}, {self._card_stock_text(card)}. Почему: {reason}."
                )

        note = self._project_missing_note(scope)
        if note:
            lines.append(note)
        lines.append(
            "Дальше можно редактировать подборку: напишите, например, «замените насос», "
            "«труба другого диаметра», «без канализации» или «соберите артикулы корзиной»."
        )
        return "\n".join(lines)

    def _compose_project_cart_summary(
        self,
        session: SessionState,
        cards: list[ProductCard],
    ) -> str:
        scope = str(session.slots.get("project_scope") or session.slots.get("scope_funnel") or "general")
        scope_label = PROJECT_SCOPE_LABELS.get(scope, "подбора")
        lines = [f"Собрал обсуждённые позиции как корзину для {scope_label}:"]
        total = 0.0
        for card in cards:
            total += card.price
            lines.append(
                f"- {html.unescape(card.name)} — арт. {card.sku}, "
                f"{card.price:g} {card.currency}, {self._card_stock_text(card)}."
            )
        if cards:
            currency = cards[0].currency
            lines.append(f"Ориентир по сумме, если считать по 1 шт. каждого артикула: {total:g} {currency}.")
        lines.append(
            "Количество по трубам, канализации и расходникам нужно считать отдельно по метражу/схеме; "
            "я не буду выдумывать количество без этих данных."
        )
        return "\n".join(lines)

    def _compose_project_next_steps(
        self,
        scope: str,
        cards: list[ProductCard],
    ) -> str:
        if cards:
            skus = ", ".join(card.sku for card in cards)
            lines = [f"В текущей подборке уже есть: {skus}. Повторять тот же список не буду."]
        else:
            lines = ["Товарных позиций в текущей подборке пока нет."]
        note = self._project_missing_note(scope)
        if note:
            lines.append(note)
        else:
            lines.append(
                "Следующий шаг — уточнить размеры и схему системы, чтобы подобрать недостающие "
                "узлы без случайных товаров и выдуманных количеств."
            )
        return "\n".join(lines)

    def _project_missing_note(self, scope: str) -> str | None:
        if scope == "warm_floor":
            return (
                "Для петли тёплого пола нужна предназначенная для неё гибкая труба PEX, PE-RT "
                "или металлопластик; обычную PPR не подставляю вместо неё. Также нужны "
                "коллектор, смесительный узел, теплоизоляция, демпферная лента, крепёж и автоматика. "
                "Если совместимых позиций в ассортименте нет, я не ставлю им выдуманные артикулы."
            )
        if scope == "bathroom":
            return (
                "Для ванной отдельно могут понадобиться смесители, сифоны, подводка и крепёж; "
                "если их нет в ассортименте, я честно не добавляю их в корзину."
            )
        if scope == "heating":
            return (
                "По отоплению ещё могут понадобиться радиаторы, коллекторы, группа безопасности, "
                "расширительный бак и бойлер ГВС — эти узлы нужно сверять по схеме и наличию."
            )
        return None

    def _project_category_reason(
        self,
        category: str,
        scope: str,
        session: SessionState,
    ) -> str:
        if category == "pumps" and scope == "water":
            source = normalize_text(str(session.slots.get("water_source") or ""))
            if "скваж" in source:
                return "подходит как насос для подачи воды из скважины; параметры по глубине и расходу нужно сверить отдельно"
            if "колод" in source:
                return "подходит как стартовый вариант насосного узла для водоснабжения; параметры по глубине и расходу нужно сверить отдельно"
            return "насос для водоснабжения нужен не всегда; его ставят при слабом давлении или автономном источнике воды"
        return PROJECT_CATEGORY_REASONS.get(
            category,
            "подходит как часть выбранной системы",
        )

    def _card_stock_text(self, card: ProductCard) -> str:
        if card.stock_qty is not None and card.stock_qty > 0:
            return f"в наличии {card.stock_qty} шт"
        return card.stock_status

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
            session.slots["scope_funnel"] = "warm_floor"
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
            "кухн",
        ]
        bathroom_markers = ["ванну", "ванной", "ванная", "санузел", "санузла"]
        if any(marker in text for marker in bathroom_markers):
            session.slots["scope_funnel"] = "bathroom"
            return BATHROOM_FUNNEL
        if any(marker in text for marker in general_markers):
            session.slots["scope_funnel"] = "general"
            return GENERAL_FUNNEL

        return None

    def _maybe_scope_followup_answer(self, message: str, session: SessionState) -> str | None:
        scope = session.slots.get("scope_funnel")
        if not scope:
            return None
        text = normalize_text(message).strip(" .,!?:;")
        if not self._wants_full_scope_followup(text):
            return None
        return SCOPE_FOLLOWUP_ANSWERS.get(str(scope))

    def _wants_full_scope_followup(self, text: str) -> bool:
        direct = {
            "все",
            "всё",
            "давай все",
            "давай всё",
            "комплектом",
            "полный комплект",
            "полностью",
            "под ключ",
        }
        if text in direct:
            return True
        markers = [
            "все для",
            "всё для",
            "все что нужно",
            "всё что нужно",
            "собери все",
            "собери всё",
            "нужно все",
            "нужно всё",
        ]
        return any(marker in text for marker in markers)

    def _maybe_yes_no_complectation_followup(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message).strip(" ?!.,")
        if text not in {"да или нет", "да нет", "так да или нет"}:
            return None
        parts = session.slots.get("last_complectation_parts") or []
        sku = session.slots.get("last_complectation_sku")
        if not parts or not sku or not session.last_products:
            return None
        part_text = ", ".join(parts)
        return (
            f"Да: по данным карточки {sku} подтверждено — {part_text}. "
            "Для стандартной схемы это означает, что отдельно такой узел обычно не подбираем, "
            "но точную комплектацию поставки всё равно лучше сверить по паспорту или у менеджера."
        )

    def _maybe_one_contour_hot_water_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        asks_hot_water = "гвс" in text or ("горяч" in text and "вод" in text)
        mentions_one_contour = "одноконтур" in text or session.slots.get("contours") == "одноконтурный"
        boiler_context = intent.category == "boilers" or session.category == "boilers"
        if not (asks_hot_water and mentions_one_contour and boiler_context):
            return None
        return (
            "Одноконтурный котёл сам по себе работает на отопление и не готовит горячую воду "
            "для кранов напрямую. Для ГВС к нему обычно добавляют бойлер косвенного нагрева "
            "или отдельную схему приготовления горячей воды. Поэтому показанные одноконтурные "
            "котлы можно рассматривать как источник отопления, но я не буду выдавать их за "
            "двухконтурные модели. Если нужна горячая вода от котла без отдельного бойлера, "
            "нужен именно двухконтурный котёл; в текущем ассортименте точного двухконтурного варианта "
            "я не вижу."
        )

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
            area = intent.slots.get("area_m2") or session.slots.get("area_m2")
            if area:
                base = GAS_VS_ELECTRIC_CONSULT.rsplit("\n", 1)[0]
                return (
                    f"{base}\nПлощадь {float(area):g} м² уже учёл. Подскажите, подведён ли газ "
                    "и какая электрическая мощность выделена на дом — это определит практичный вариант."
                )
            return GAS_VS_ELECTRIC_CONSULT

        # Типы полипропиленовых труб (обычная vs армированная; для горячей vs холодной).
        in_pipe_context = (
            intent.category == "pipes" or session.category == "pipes"
            or "труба" in text or "трубы" in text or "труб" in text
        )
        if in_pipe_context and any(
            marker in text
            for marker in ["армиров", "fiber", "alux", "pn20", "pn 20", "стеклов", "алюмин", "горяч", "холодн"]
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

    def _maybe_manager_contact_question(self, message: str) -> str | None:
        text = normalize_text(message)
        has_contact_intent = self._has_marker(text, CONTACT_INTENT_MARKERS, fuzzy_threshold=84)
        has_generic_contact = self._has_marker(text, GENERIC_CONTACT_MARKERS, fuzzy_threshold=84)
        if not has_generic_contact and (not has_contact_intent or not self._mentions_human_role(text)):
            return None
        return (
            "Чтобы менеджер смог связаться с вами, оставьте телефон, email или другой удобный контакт "
            "и кратко напишите вопрос. Я сохраню обращение вместе с историей диалога. "
            "Если хотите продолжить здесь, я могу сразу помочь с подбором по каталогу."
        )

    def _maybe_handoff_process_question(self, message: str) -> str | None:
        text = normalize_text(message)
        mentions_handoff = self._has_marker(
            text,
            ["передал", "передали", "переда", "заявк", "связаться", "свяжется", "свяжутся"],
            fuzzy_threshold=78,
        )
        challenges_contact = self._has_marker(
            text,
            [
                "никакую информацию",
                "никакой информации",
                "без информации",
                "не давал",
                "не дала",
                "нет контакт",
                "как ты",
                "куда",
                "что именно",
            ],
            fuzzy_threshold=82,
        )
        if not mentions_handoff or not challenges_contact:
            return None
        return (
            "Вы правы: без телефона, email или другого контакта я не должен обещать, что менеджер "
            "свяжется с вами. Я могу сохранить историю обращения для менеджера, но для обратной связи "
            "нужен контакт. Оставьте его здесь вместе с вопросом, либо продолжим подбор прямо в чате."
        )

    def _mentions_human_role(self, text: str) -> bool:
        return self._has_marker(text, HUMAN_ROLE_MARKERS, fuzzy_threshold=76)

    def _has_marker(self, text: str, markers: list[str], fuzzy_threshold: int = 82) -> bool:
        if any(marker in text for marker in markers):
            return True
        tokens = _WORD_RE.findall(text)
        if not tokens:
            return False
        for marker in markers:
            normalized_marker = normalize_text(marker)
            marker_tokens = normalized_marker.split()
            if len(normalized_marker) < 4:
                continue
            if len(marker_tokens) == 1:
                if self._has_fuzzy_token(tokens, normalized_marker, fuzzy_threshold):
                    return True
                continue
            if self._has_fuzzy_phrase(tokens, normalized_marker, len(marker_tokens), fuzzy_threshold):
                return True
        return False

    def _has_fuzzy_token(self, tokens: list[str], marker: str, threshold: int) -> bool:
        for token in tokens:
            if len(token) < 4:
                continue
            if fuzz.ratio(token, marker) >= threshold:
                return True
        return False

    def _has_fuzzy_phrase(
        self,
        tokens: list[str],
        marker: str,
        marker_width: int,
        threshold: int,
    ) -> bool:
        for width in {max(1, marker_width - 1), marker_width, marker_width + 1}:
            if width > len(tokens):
                continue
            for index in range(0, len(tokens) - width + 1):
                window = " ".join(tokens[index : index + width])
                if fuzz.ratio(window, marker) >= threshold:
                    return True
        return False

    def _wants_manager_handoff(self, message: str) -> bool:
        text = normalize_text(message)
        negations = [
            "не надо менеджер",
            "без менеджера",
            "не нужен менеджер",
            "не надо человек",
            "без человека",
            "сам разберусь",
        ]
        if any(neg in text for neg in negations):
            return False
        return self._mentions_human_role(text) and self._has_marker(
            text,
            TRANSFER_INTENT_MARKERS,
            fuzzy_threshold=75,
        )

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
        candidates: list[tuple[int, int]] = []
        for keyword in ["комплект поставки", "комплектность", "комплектац", "в комплект"]:
            for match in re.finditer(re.escape(keyword), low):
                idx = match.start()
                after = low[idx : idx + limit]
                score = 0
                if "в комплект поставки входят" in after:
                    score += 10
                if "поставляются в комплекте" in after:
                    score += 5
                if any(anchor in after for anchor in ["входят:", "1.", "2."]):
                    score += 3
                score += min(idx // 2000, 4)
                candidates.append((score, idx))
        if candidates:
            idx = max(candidates)[1]
            start = max(0, idx - 100)
            return docs_text[start : start + limit]
        return docs_text[:limit]

    def _passport_package_items(self, docs_text: str) -> list[str]:
        """Extract confirmed package items from the best passport section."""
        snippet = self._passport_snippet(docs_text, limit=1800).replace("ѐ", "ё")
        marker = re.search(
            r"в комплект поставки\s+(?:входят|входит)\s*:?\s*",
            snippet,
            flags=re.IGNORECASE,
        )
        if not marker:
            return []
        body = snippet[marker.end() :]
        numbered = list(re.finditer(r"(?<!\d)(\d{1,2})\.\s+", body))
        items: list[str] = []
        expected = 1
        for index, match in enumerate(numbered):
            number = int(match.group(1))
            if number != expected:
                if items:
                    break
                continue
            end = numbered[index + 1].start() if index + 1 < len(numbered) else len(body)
            item = body[match.end() : end]
            item = re.split(
                r"\s+(?:ВНИМАНИЕ!|Рис\.|\d{1,2}\.\s+(?:Серийный|Инструкция|Монтаж))",
                item,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            item = " ".join(item.split()).strip(" .;:")
            if item:
                items.append(item)
                expected += 1
            if expected > 12:
                break
        if items:
            return items

        # Некоторые паспорта перечисляют короткий состав поставки одной фразой.
        plain = re.split(
            r"\s+(?:ВНИМАНИЕ!|Рис\.|\d{1,2}\.\s+(?:Серийный|Инструкция|Монтаж))",
            body,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        plain = " ".join(plain.split()).strip(" .;:")
        return [plain] if plain else []

    def _compose_passport_package_answer(self, card: ProductCard) -> str:
        product = self._find_product_by_sku(card.sku)
        if not product or not product.docs_text:
            components = self.guardrails.list_builtin_components(product) if product else []
            known = ""
            if components:
                known = (
                    " Отдельно карточка подтверждает встроенные узлы: "
                    + ", ".join(components)
                    + ". Это не перечень содержимого коробки."
                )
            return (
                f"Для {card.sku} не найден паспорт с подтверждённым составом "
                "поставки. Не буду смешивать встроенные узлы котла с содержимым коробки; "
                f"полную комплектацию нужно уточнить у менеджера.{known} Карточка: {card.url}"
            )
        items = self._passport_package_items(product.docs_text)
        if not items:
            return (
                f"В привязанном паспорте для {card.sku} не нахожу явного перечня комплекта "
                "поставки. Не буду дополнять его по общим знаниям; уточните состав у менеджера. "
                f"Карточка: {card.url}"
            )
        lines = [
            f"По привязанному паспорту для {card.sku} в комплект поставки входят:"
        ]
        lines.extend(f"- {item}." for item in items)
        lines.append(
            "Это именно комплект поставки; встроенные узлы котла перечисляются отдельно. "
            f"Карточка товара: {card.url}"
        )
        return "\n".join(lines)

    def _is_open_complectation_question(self, message: str) -> bool:
        """Package/passport question with no request for one specific built-in part."""
        text = normalize_text(message)
        open_markers = [
            "полную комплектац",
            "полная комплектац",
            "полной комплектац",
            "что входит в комплект",
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

    def _pump_requested_for_boiler_context(self, message: str, session: SessionState) -> bool:
        text = normalize_text(message)
        if "насос" not in text:
            return False
        if not any(marker in text for marker in ["к нему", "к котл", "для котл", "на котл"]):
            return False
        if session.category == "boilers" or session.slots.get("boiler_type") or session.slots.get("heat_sources"):
            return True
        for card in session.last_products:
            product = self._find_product_by_sku(card.sku)
            if product and self.search_agent.canonical_category(product) == "boilers":
                return True
        return False

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
        asks_more = any(
            marker in text
            for marker in [
                "какие еще",
                "какие ещё",
                "покажи еще",
                "покажи ещё",
                "другие котл",
                "еще котл",
                "ещё котл",
            ]
        )
        if "аналог" not in text and not asks_more:
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
        wants_cheaper = bool(intent.flags.get("cheap") or intent.slots.get("cheap") or "дешев" in text)
        if wants_cheaper:
            query.cheap = True
        agents_used.append("FeedSearchAgent")
        alternatives = [
            product
            for product in self.search_agent.search_alternatives(query)
            if normalize_sku_token(product.sku) not in shown_skus
        ]
        if wants_cheaper:
            min_shown_price = min((card.price for card in session.last_products), default=None)
            if min_shown_price is not None:
                alternatives = [
                    product
                    for product in alternatives
                    if product.price is not None and product.price < min_shown_price
                ]
        if not alternatives:
            answer = (
                self.composer.compose_no_cheaper(session.last_products)
                if wants_cheaper
                else (
                    "Аналогов к показанным товарам в текущем ассортименте не вижу. "
                    "Могу передать вопрос менеджеру — напишите «передай менеджеру»."
                )
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], False, intent, session, agents_used)
        agents_used.append("ProductCardAgent")
        cards = self.card_agent.build_cards(alternatives, query, limit=3)
        agents_used.append("GuardrailsAgent")
        guard = self.guardrails.validate_cards(cards, alternatives, query)
        if not guard.ok or not cards:
            answer = (
                "Не могу безопасно показать аналоги по текущим данным. "
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
            "что лучше",
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
                "(труба в трубе), а не из помещения. Это снижает зависимость горения от "
                "воздуха в комнате, но не отменяет требования к помещению, вентиляции, "
                "дымоходу и монтажу специалистом.",
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
            "Это уже инженерно рискованный вопрос: без проекта я не буду делать расчёт системы "
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

    def _limit_oversized_boiler_cards(
        self,
        cards: list[ProductCard],
        area_m2: float | None,
        message: str,
    ) -> list[ProductCard]:
        if not area_m2 or len(cards) <= 1:
            return cards
        text = normalize_text(message)
        if any(marker in text for marker in ["еще", "ещё", "друг", "сравн", "вариант"]):
            return cards
        rated_cards: list[tuple[float, ProductCard]] = []
        for card in cards:
            product = self._find_product_by_sku(card.sku)
            if not product or self.search_agent.canonical_category(product) != "boilers":
                return cards
            power = self.guardrails._extract_power_kw(product)
            if power is not None:
                rated_cards.append((power, card))
        if len(rated_cards) != len(cards):
            return cards
        upper_kw = area_m2 / 10.0 * 1.3
        nearest_power, nearest_card = min(rated_cards, key=lambda item: item[0])
        if nearest_power <= upper_kw * 1.25:
            return cards
        return [nearest_card]

    def _compose_query_note(self, query: SearchQuery) -> str | None:
        notes: list[str] = []
        if query.category == "pipes" and query.slots.get("total_length_m"):
            notes.append(
                f"Общий метраж {float(query.slots['total_length_m']):g} м учёл как требуемое количество, "
                "а не как диаметр. В карточке не указаны длина одного отрезка и единица цены, "
                "поэтому стоимость всего метража не умножаю без уточнения."
            )
        if query.slots.get("fallback_after_repeat"):
            if query.category == "pumps":
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовой "
                    "вариант из ассортимента. Для циркуляционного насоса важно сверить напор, "
                    "монтажную длину 130/180 мм и присоединение; без этих данных это не "
                    "окончательный подбор."
                )
            else:
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовые "
                    "варианты по текущим данным. Уточните недостающие параметры — подберу точнее."
                )
        elif query.slots.get("allow_basic_option"):
            notes.append(
                "Показываю базовый вариант из ассортимента. Для точного подбора нужны монтажная длина, "
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

    def _maybe_warm_floor_pipe_answer(self, message: str) -> str | None:
        text = normalize_text(message)
        mentions_warm_floor = "пол" in text and any(marker in text for marker in ["тепл", "тёпл"])
        mentions_ppr = any(marker in text for marker in ["ppr", "ппр", "полипропилен"])
        if not (mentions_warm_floor and mentions_ppr):
            return None
        return (
            "Жёсткую PPR-трубу не используют как петлю водяного тёплого пола: у контура много "
            "плавных поворотов и он должен укладываться без соединений в стяжке. PPR может быть "
            "на подводящей магистрали до коллектора. Для самой петли нужна предназначенная для "
            "тёплого пола гибкая труба PEX, PE-RT или металлопластик; конкретный тип и диаметр "
            "сверяют по проекту и паспорту системы."
        )

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
            "Уточните: какое утепление и нужна ли горячая вода — тогда подберу варианты из ассортимента."
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
        if any(
            marker in text
            for marker in [
                "какие еще",
                "какие ещё",
                "покажи еще",
                "покажи ещё",
                "другие котл",
                "еще котл",
                "ещё котл",
            ]
        ):
            return False
        if self._looks_like_parameter_followup(text):
            return False

        category_words = {
            "pumps": ["насос", "помпа"],
            "pipes": ["труба", "трубы"],
            "sewer": ["канализац"],
            "boilers": ["котел", "котёл", "котл"],
            "valves": ["кран", "шаровый", "вентиль"],
            "radiator_fittings": ["радиатор", "батаре", "термоголов", "клапан"],
            "radiators": ["радиатор", "батаре", "биметалл"],
            "fittings": ["фитинг", "муфт", "угольник", "тройник"],
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
                "дюйм",
                "dn",
                "секц",
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
        if self._is_new_pump_selection_question(text):
            return False
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

    def _is_new_pump_selection_question(self, text: str) -> bool:
        pump_markers = ["насос", "дренаж", "циркуляц", "скваж", "повысит", "помпа"]
        if not any(marker in text for marker in pump_markers):
            return False
        if any(
            marker in text
            for marker in [
                "там",
                "в нем",
                "в нём",
                "в него",
                "у него",
                "туда",
                "в комплект",
                "входит",
                "встро",
            ]
        ):
            return False
        application_markers = [
            "для скваж",
            "скваж",
            "дренаж",
            "циркуляц",
            "для полив",
            "полив",
            "для отоплен",
            "отоплен",
            "водоснаб",
            "повысит",
            "колод",
        ]
        return any(marker in text for marker in application_markers)

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
            "систему — без сверки с карточкой товара не подтвержу узлы."
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
                    image_url=card.image_url,
                )
                for card in cards
            ],
            need_handoff=need_handoff,
            debug={
                "intent": intent.intent_type,
                "category": intent.category,
                "slots": session.slots,
                "agents_used": agents_used,
                "llm_used": intent.llm_used
                or self.composer.last_llm_used
                or self.consultant.last_llm_used,
                "intent_llm_used": intent.llm_used,
                "response_llm_used": self.composer.last_llm_used,
                "response_llm_requested": self.composer.last_llm_requested,
                "response_llm_fallback_reason": self.composer.last_llm_fallback_reason,
                "consultant_llm_used": self.consultant.last_llm_used,
                "consultant_llm_fallback_reason": self.consultant.last_fallback_reason,
                "any_llm_used": intent.llm_used
                or self.composer.last_llm_used
                or self.consultant.last_llm_used,
                "topic_changed": session.topic_changed,
                "products_loaded_from": self.products_loaded_from,
            },
        )
