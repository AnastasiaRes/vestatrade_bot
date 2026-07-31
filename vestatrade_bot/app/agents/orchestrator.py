from __future__ import annotations

import html
import logging
import re
from threading import RLock, local
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
    HandoffSummary,
    IntentResult,
    Product,
    ProductCard,
    SearchQuery,
    SessionState,
)
from app.openrouter_client import OpenRouterClient
from app.session_store import InMemorySessionStore

from .consultant import ConsultantAgent
from .engineering_requirements import EngineeringRequirementsAgent
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

TRANSIENT_QUERY_SLOTS = {
    "choose_one",
    "result_limit",
    "sort_mode",
    "relative_cheaper",
}


COMPANION_HINTS: dict[str, str] = {
    "boilers": (
        "Кстати, у настенных котлов циркуляционный насос часто уже встроен, поэтому отдельный "
        "насос нужен не всегда. Его добавляют для тёплого пола, бойлера, нескольких контуров "
        "или длинной системы; также обычно проверяют группу безопасности и трубы для обвязки."
    ),
    "pumps": (
        "Кстати, в контуре отопления по обе стороны насоса часто ставят два запорных крана, "
        "чтобы узел можно было снять без слива всей системы. Размер крана нельзя выбирать "
        "только по маркировке насоса: нужно сверить паспорт насоса, штатные гайки и резьбу "
        "трубопровода со стороны системы. Напишите этот размер — тогда проверю краны в наличии."
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
    "Главное отличие — источник энергии и требования к подключению.\n"
    "Газовый котёл требует подведённого газа, корректного дымоудаления, проекта и соблюдения "
    "местных требований. Электрическому не нужен дымоход, но нужно проверить выделенную "
    "электрическую мощность, питание 220/380 В и местные требования к подключению.\n"
    "Что окажется выгоднее по монтажу и эксплуатации, зависит от тарифов, стоимости подключения, "
    "теплопотерь и режима работы — без этих данных не обещаю экономию ни одного варианта.\n"
    "Подскажите: газ подведён, какое питание доступно и какая площадь? Тогда сравню подходящие "
    "варианты из каталога."
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
    "водонагрев",
    "накопительн",
    "проточн",
    "газовая колонк",
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
    "унитаз",
    "инсталляц",
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
    "water_heaters": "Водонагреватель",
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
    "water_heaters": "готовит горячую воду; объём, способ нагрева и тип установки нужно сверять с задачей",
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

WATER_EMERGENCY_FIRST_RESPONSE = (
    "Сначала остановите аварийную ситуацию — сейчас товары не подбираем.\n"
    "1. Немедленно перекройте вводной кран воды или стояк. Если это невозможно, "
    "сразу звоните в аварийно-диспетчерскую службу управляющей компании/ТСЖ.\n"
    "2. Если вода дошла до розеток, проводки или электроприборов, не касайтесь их. "
    "Отключите электричество только через сухой и безопасно доступный щиток; иначе "
    "не подходите к опасной зоне и сообщите об этом аварийной службе.\n"
    "3. Предупредите соседей снизу и, если это безопасно, собирайте воду, пока едет "
    "аварийная служба. При угрозе людям звоните 112.\n"
    "Когда поток воды будет остановлен, напишите об этом — затем уточним место, "
    "материал и размер повреждённого участка."
)

WATER_EMERGENCY_CONTAINED_RESPONSE = (
    "Хорошо, что воду перекрыли. Товар пока не советую: сначала нужно точно определить "
    "повреждение. Напишите, где именно течь — труба, гибкая подводка, сифон или соединение "
    "под мойкой; из какого материала деталь; какой наружный диаметр трубы или размер резьбы; "
    "что именно лопнуло/разошлось. Если безопасно, приложите фото повреждённого узла и "
    "маркировки — без этих данных нельзя надёжно подобрать замену."
)

HEATING_EMERGENCY_FIRST_RESPONSE = (
    "Сначала остановите аварийную ситуацию — сейчас товары не подбираем.\n"
    "1. Не касайтесь горячего теплоносителя, радиатора и мокрых поверхностей: есть риск ожога. "
    "Уведите людей и животных из опасной зоны.\n"
    "2. Если безопасно доступны штатные краны радиатора/контура, перекройте их. Не разбирайте "
    "горячий узел. Сразу звоните в аварийно-диспетчерскую службу управляющей компании/ТСЖ.\n"
    "3. Если теплоноситель попал на розетки или проводку, не касайтесь их. Электричество "
    "отключайте только через сухой безопасно доступный щиток; иначе ждите аварийную службу. "
    "При угрозе людям звоните 112.\n"
    "Когда течь будет остановлена и узел остынет, напишите — затем уточним модель радиатора, "
    "место повреждения и размеры подключения."
)

HEATING_EMERGENCY_CONTAINED_RESPONSE = (
    "Хорошо, что поток остановлен. Не разбирайте узел, пока радиатор и теплоноситель полностью "
    "не остынут. Для безопасного подбора напишите модель/тип радиатора, где именно течь "
    "(секция, пробка, кран, соединение или труба), размер подключения и приложите фото маркировки. "
    "Если перекрытие ненадёжно или течь возобновляется, нужна аварийная служба."
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
        self.intent_router = IntentRouterAgent(
            self.llm_client,
            catalog_brands=[
                product.brand
                for product in (products or [])
                if product.brand
            ],
        )
        self.slot_filling = SlotFillingAgent()
        self.engineering_requirements = EngineeringRequirementsAgent(
            self.slot_filling
        )
        self.search_agent = FeedSearchAgent(products or [])
        self.ranking_agent = RankingAgent()
        self.card_agent = ProductCardAgent()
        self.guardrails = GuardrailsAgent()
        # Composer and consultant expose request-scoped diagnostic state
        # (history, draft, last products/LLM result).  A single shared instance
        # leaked that state when FastAPI handled different users concurrently.
        self._request_agents = local()
        self._catalog_load_lock = RLock()
        self._session_locks_guard = RLock()
        self._session_locks: dict[str, RLock] = {}
        self.handoff = HandoffAgent()
        self.products_loaded_from = "injected" if products is not None else "none"
        self.docs_attached = 0
        if products:
            self.docs_attached = load_docs_for_products(products, self._docs_dirs())

    def _docs_dirs(self) -> list[Any]:
        return [self.settings.product_docs_dir, PROJECT_ROOT / "data"]

    def reload_products(self, refresh: bool = True) -> tuple[int, str]:
        with self._catalog_load_lock:
            products, source = self.feed_loader.load_products(refresh=refresh)
            self.docs_attached = load_docs_for_products(products, self._docs_dirs())
            self.search_agent.set_products(products)
            self.intent_router.set_catalog_brands(
                [product.brand for product in products if product.brand]
            )
            self.products_loaded_from = source
            return len(products), source

    def _ensure_products_loaded(self) -> bool:
        """Load the cached catalogue once before catalogue-aware intent handling."""
        if self.search_agent.products:
            return True
        with self._catalog_load_lock:
            if self.search_agent.products:
                return True
            try:
                self.reload_products(refresh=False)
            except Exception as exc:
                logger.exception("Cannot load products for intent handling: %s", exc)
        return bool(self.search_agent.products)

    @property
    def composer(self) -> ResponseComposerAgent:
        agent = getattr(self._request_agents, "composer", None)
        if agent is None:
            agent = ResponseComposerAgent(self.llm_client)
            self._request_agents.composer = agent
        return agent

    @property
    def consultant(self) -> ConsultantAgent:
        agent = getattr(self._request_agents, "consultant", None)
        if agent is None:
            agent = ConsultantAgent(
                self.llm_client,
                model=self.settings.llm_model_strong,
            )
            self._request_agents.consultant = agent
        return agent

    def handle_chat(self, session_id: str, message: str) -> ChatResponse:
        # Turns inside one dialogue are transactional, while independent users
        # may proceed in parallel.  This preserves session history without the
        # latency penalty of a process-wide chat lock.
        with self._session_lock(session_id):
            self._request_agents.composer = ResponseComposerAgent(self.llm_client)
            self._request_agents.consultant = ConsultantAgent(
                self.llm_client,
                model=self.settings.llm_model_strong,
            )
            return self._handle_chat(session_id, message)

    def _session_lock(self, session_id: str) -> RLock:
        with self._session_locks_guard:
            return self._session_locks.setdefault(session_id, RLock())

    def _handle_chat(self, session_id: str, message: str) -> ChatResponse:
        session = self.sessions.get(session_id)
        session.topic_changed = False
        session.slots.pop("fallback_after_repeat", None)
        self.composer.reset_usage()
        self.consultant.last_llm_used = False
        self.consultant.last_llm_requested = False
        self.consultant.last_llm_output_accepted = False
        self.consultant.last_llm_rejection_reason = None
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

        water_heater_safety = self._maybe_water_heater_operational_safety_answer(
            message,
            session,
        )
        if water_heater_safety:
            intent = IntentResult(
                intent_type="water_heater_safety",
                category="water_heaters",
                confidence=1.0,
            )
            agents_used.append("GuardrailsAgent")
            self._append_history(session, message, water_heater_safety)
            self.sessions.save(session)
            return self._response(
                session_id,
                water_heater_safety,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        emergency_answer = self._maybe_water_emergency_answer(message, session)
        if emergency_answer:
            intent = IntentResult(
                intent_type="emergency",
                category="pipes",
                confidence=1.0,
            )
            agents_used.append("GuardrailsAgent")
            self._append_history(session, message, emergency_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                emergency_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        gas_safety = self._maybe_gas_safety_answer(message, session)
        if gas_safety:
            intent = IntentResult(
                intent_type="gas_safety",
                category=session.category or "boilers",
                confidence=1.0,
            )
            agents_used.append("GuardrailsAgent")
            self._append_history(session, message, gas_safety)
            self.sessions.save(session)
            return self._response(
                session_id,
                gas_safety,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        electrical_safety = self._maybe_electrical_safety_answer(message, session)
        if electrical_safety:
            intent = IntentResult(
                intent_type="electrical_safety",
                category=session.category or "boilers",
                confidence=1.0,
            )
            agents_used.append("GuardrailsAgent")
            self._append_history(session, message, electrical_safety)
            self.sessions.save(session)
            return self._response(
                session_id,
                electrical_safety,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        sink_flow_answer = self._maybe_sink_flow_answer(message, session)
        if sink_flow_answer:
            intent = IntentResult(
                intent_type="broad_category",
                category="sewer",
                confidence=1.0,
            )
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, sink_flow_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                sink_flow_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        # Identity and physical-service boundaries must not depend on the
        # probabilistic intent router: live QA routed an onsite-visit question
        # as ``unknown`` and skipped the deterministic safety reply.
        boundary_answer = self.composer.compose_identity_or_service(message)
        if boundary_answer:
            intent = IntentResult(
                intent_type="assistant_boundary",
                category="other",
                confidence=1.0,
            )
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(
                boundary_answer,
                "small_talk",
                agents_used,
            )
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        # Contact details, consent and refusal are deterministic control turns.
        # Handle them before the intent router so PII and consent phrases are
        # neither sent to an external model nor incorporated into its cache key.
        if session.pending_handoff:
            handoff_intent = IntentResult(
                intent_type="handoff_control",
                category=session.category or "other",
                confidence=1.0,
            )
            if self._is_handoff_opt_out(message) or self._is_handoff_refusal(message):
                response = self._handle_handoff_opt_out(
                    message,
                    handoff_intent,
                    session,
                    agents_used,
                )
                self.sessions.save(session)
                return response
            if (
                self.handoff.extract_contact(message)
                or self._is_handoff_confirmation(message)
                or self._wants_manager_handoff(message)
            ):
                response = self._maybe_continue_handoff(
                    message,
                    handoff_intent,
                    session,
                    agents_used,
                )
                if response is not None:
                    self.sessions.save(session)
                    return response

        pre_handoff_command = self._wants_manager_handoff(message)
        if pre_handoff_command or session.slots.get("financial_context"):
            boundary_intent = IntentResult(
                intent_type="handoff_control",
                category="other",
                confidence=1.0,
            )
            financial_answer = self._maybe_financial_stocks_answer(
                message,
                boundary_intent,
                session,
            )
            if financial_answer:
                agents_used.append("ResponseComposerAgent")
                self._append_history(session, message, financial_answer)
                self.sessions.save(session)
                return self._response(
                    session_id,
                    financial_answer,
                    [],
                    False,
                    boundary_intent,
                    session,
                    agents_used,
                )

        if self._is_handoff_opt_out(message):
            boundary_intent = IntentResult(
                intent_type="handoff_control",
                category=session.category or "other",
                confidence=1.0,
            )
            response = self._handle_handoff_opt_out(
                message,
                boundary_intent,
                session,
                agents_used,
            )
            self.sessions.save(session)
            return response

        if pre_handoff_command:
            handoff_intent = IntentResult(
                intent_type="handoff_request",
                category=session.category or "other",
                confidence=1.0,
            )
            summary = self.handoff.build_summary(message, session)
            session.handoff_opt_out = False
            session.pending_handoff = self.handoff.summary_to_dict(summary)
            needs_contact = not bool(summary.contact)
            session.handoff_status = (
                "awaiting_contact" if needs_contact else "awaiting_consent"
            )
            answer = self.handoff.compose_consent_request(
                summary,
                needs_contact=needs_contact,
            )
            agents_used.append("HandoffAgent")
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                [],
                True,
                handoff_intent,
                session,
                agents_used,
            )

        intent = self.intent_router.route(message, session)
        # Exact identities must see the catalogue on the very first request.
        # Keep the preload scoped to identity-shaped turns: small talk and
        # non-catalogue control turns must stay cheap and concurrent.
        if self._needs_catalog_identity_resolution(message, intent):
            self._ensure_products_loaded()
        self._ground_catalog_sku_intent(message, intent)
        self._enrich_brand_from_feed(message, intent)
        agents_used.append("IntentRouterAgent")

        toilet_project_answer = self._maybe_toilet_installation_project(
            message,
            session,
        )
        if toilet_project_answer:
            # This project is outside the currently typed basket categories.
            # Clear any stale boiler/pump goal before the consultant can expand
            # «нужно всё» into an unrelated generic engineering basket.
            intent = IntentResult(
                intent_type="broad_category",
                category="other",
                confidence=1.0,
                is_topic_change=True,
            )
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            self._append_history(session, message, toilet_project_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                toilet_project_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        self._stabilize_active_goal(message, intent, session)
        self._ground_builtin_boiler_refinement(message, intent, session)
        self._reconcile_builtin_constraints(intent, session)

        if self._is_pending_continuation(intent, session, message):
            self._restore_pending_intent(intent, session)

        voltage_match = re.search(r"\b(220|380)\b", normalize_text(message))
        boiler_voltage_context = bool(
            intent.category == "boilers"
            or session.category == "boilers"
            or session.slots.get("needs_voltage_clarification")
            or "220 или 380" in normalize_text(session.pending_question or "")
        )
        if voltage_match and boiler_voltage_context:
            # A bare voltage reply is a concrete catalogue filter, not a new
            # consulting topic.  Persist it before the early Consultant branch.
            voltage = int(voltage_match.group(1))
            intent.intent_type = "attribute_request"
            intent.category = "boilers"
            intent.slots["voltage_v"] = voltage
            intent.is_topic_change = False
            session.slots["voltage_v"] = voltage

        # «этот насос», «тот что ты предложил» — это вопрос про показанное, а не смена
        # темы; иначе topic-change стёр бы контекст ещё до ответа агента.
        if session.last_products and self._references_shown_products(message):
            intent.is_topic_change = False
        if session.last_products and self._looks_like_pump_boiler_compatibility(message):
            # The boiler SKU is the comparison target, not a command to discard
            # the pump that "он" refers to.
            intent.is_topic_change = False
        if session.last_products and self._looks_like_pump_union_valves_request(message):
            # This is a deliberate category transition which still needs the
            # shown pump long enough to read its passport connection facts.
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
        if intent.is_topic_change and session.slots.get("complex_engineering_request"):
            intent.is_topic_change = False

        if intent.is_topic_change:
            session.slots = {}
            session.last_products = []
            session.shown_product_skus = []
            session.topic_changed = True
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_category = None
            session.pending_slot_keys = []
            session.pending_complectation_parts = []
            session.question_repeats = 0
            if session.handoff_status in {"awaiting_contact", "awaiting_consent", "failed"}:
                session.pending_handoff = None
                session.handoff_status = "none"

        if self._should_restart_category_context(message, intent, session):
            session.slots = {}
            session.last_products = []
            session.shown_product_skus = []

        if intent.category == "pumps" and self._pump_requested_for_boiler_context(message, session):
            intent.slots.setdefault("pump_type", "циркуляционный")
            intent.slots.setdefault("pump_use", "отопление")
            intent.slots.setdefault("pump_context", "котел")

        direct_comparison = self._maybe_direct_sku_comparison_response(
            session_id,
            message,
            intent,
            session,
            agents_used,
        )
        if direct_comparison is not None:
            self.sessions.save(session)
            return direct_comparison

        financial_answer = self._maybe_financial_stocks_answer(message, intent, session)
        if financial_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, financial_answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                financial_answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        pending_handoff = self._maybe_continue_handoff(message, intent, session, agents_used)
        if pending_handoff is not None:
            self.sessions.save(session)
            return pending_handoff

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

        meta_answer = self._maybe_meta_question(message)
        if meta_answer:
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, meta_answer)
            self.sessions.save(session)
            return self._response(session_id, meta_answer, session.last_products, False, intent, session, agents_used)

        if intent.intent_type == "link_request":
            selected_index = self._select_ordinal_index(message, session.last_products)
            link_text = normalize_text(message)
            include_name = "назван" in link_text or "итог" in link_text
            answer = self.composer.compose_link_answer(
                session.last_products,
                selected_index,
                include_name=include_name,
            )
            if selected_index is not None and 0 <= selected_index < len(session.last_products):
                response_cards = [session.last_products[selected_index]]
            else:
                response_cards = session.last_products
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "link", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                response_cards,
                False,
                intent,
                session,
                agents_used,
            )

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

        boiler_warning = self._maybe_boiler_warning(message, intent, session)
        if boiler_warning:
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(boiler_warning, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        boiler_power_followup = self._maybe_boiler_power_followup(message, session)
        if boiler_power_followup:
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(boiler_power_followup, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        complex_handoff = self._maybe_complex_engineering_handoff(
            session_id,
            message,
            intent,
            session,
            agents_used,
        )
        if complex_handoff is not None:
            self.sessions.save(session)
            return complex_handoff

        if self._looks_like_unresolved_complectation_question(message, session):
            intent.intent_type = "complectation"
            intent.is_topic_change = False
            response = self._handle_complectation(message, session, intent, agents_used)
            self.sessions.save(session)
            return response

        context_parts = self._part_question_about_shown_products(message, session)
        if context_parts:
            intent.intent_type = "complectation"
            intent.is_topic_change = False
            session.pending_complectation_parts = context_parts
            response = self._handle_complectation(message, session, intent, agents_used)
            self.sessions.save(session)
            return response

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

        shown_boiler_contours = self._maybe_shown_boiler_contours_answer(
            message,
            intent,
            session,
        )
        if shown_boiler_contours:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            answer, cards = shown_boiler_contours
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

        shown_product_purpose = self._maybe_shown_product_purpose_answer(message, session)
        if shown_product_purpose:
            answer, cards = shown_product_purpose
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
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

        pump_electrical = self._maybe_shown_pump_electrical_answer(message, session)
        if pump_electrical:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, pump_electrical)
            self.sessions.save(session)
            return self._response(
                session_id,
                pump_electrical,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        # A combined request such as «подбери краны с американкой и назови
        # присоединительный размер» is primarily an accessory workflow. Run it
        # before the standalone connection-fact answer so the size fact cannot
        # prematurely end the turn and silently skip the requested selection.
        pump_union_valves = self._maybe_pump_union_valves_answer(message, session)
        if pump_union_valves:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, pump_union_valves)
            self.sessions.save(session)
            return self._response(
                session_id,
                pump_union_valves,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        pump_connection = self._maybe_shown_pump_connection_answer(message, session)
        if pump_connection:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, pump_connection)
            self.sessions.save(session)
            return self._response(
                session_id,
                pump_connection,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        pump_boiler_compatibility = self._maybe_pump_compatibility_answer(
            message,
            session,
        )
        if pump_boiler_compatibility:
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, pump_boiler_compatibility)
            self.sessions.save(session)
            return self._response(
                session_id,
                pump_boiler_compatibility,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )

        completed_union_valves = self._maybe_complete_pump_union_valves_answer(
            message,
            intent,
            session,
        )
        if completed_union_valves:
            answer, cards = completed_union_valves
            agents_used.extend(
                [
                    "FeedSearchAgent",
                    "ProductCardAgent",
                    "GuardrailsAgent",
                    "ResponseComposerAgent",
                ]
            )
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
            # A product-card/passport question supersedes any unfinished
            # category-selection clarification (for example a pending pump
            # purpose). Keep only complectation state from this point on.
            session.pending_category = None
            session.pending_slot_keys = []
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

        focused_stock = self._maybe_focused_stock_answer(message, intent, session)
        if focused_stock:
            answer, cards = focused_stock
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(answer, "products", agents_used)
            # A strict stock follow-up may intentionally hide an unavailable
            # card. Keep the exact shown product as the internal reference so a
            # subsequent «покажи аналоги» can use its verified characteristics.
            if cards:
                session.last_products = cards
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id, answer, cards, False, intent, session, agents_used
            )

        choose_result = self._maybe_choose_one_answer(message, session, intent)
        if choose_result:
            choose_answer, chosen_card = choose_result
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(choose_answer, "link", agents_used)
            chosen_cards = [chosen_card]
            session.last_products = chosen_cards
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id,
                answer,
                chosen_cards,
                False,
                intent,
                session,
                agents_used,
            )

        focused_price = self._maybe_shown_category_price_answer(message, intent, session)
        if focused_price:
            answer, cards = focused_price
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(answer, "products", agents_used)
            session.last_products = cards
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(
                session_id, answer, cards, False, intent, session, agents_used
            )

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

        why_answer = self._maybe_why_explanation(message, session)
        if why_answer:
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(why_answer, "generic", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, session.last_products, False, intent, session, agents_used)

        if self._looks_like_confirmation(message) and session.last_products:
            cards = session.last_products
            answer = self.composer.compose_confirm_last(cards)
            agents_used.append("ResponseComposerAgent")
            answer = self._guard_composed_answer(answer, "link", agents_used)
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, cards, False, intent, session, agents_used)

        if (
            self._looks_like_affirmation(message)
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

        required_clarification = self._maybe_required_boiler_clarification(
            message,
            intent,
            session,
        )
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

        # Establish engineering assumptions for a direct power comparison
        # before the free-form consultant can recommend a catalogue item.
        boiler_tradeoff = self._maybe_boiler_tradeoff(message, intent, session)
        if boiler_tradeoff:
            agents_used.extend(["ResponseComposerAgent", "GuardrailsAgent"])
            answer = self._guard_composed_answer(boiler_tradeoff, "generic", agents_used)
            session.pending_question = "Какое утепление и нужна ли горячая вода?"
            session.pending_intent_type = "attribute_request"
            session.slots["pending_tradeoff"] = True
            session.category = "boilers"
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        project_response = self._maybe_project_cart_response(
            session_id, message, intent, session, agents_used
        )
        if project_response is not None:
            self.sessions.save(session)
            return project_response

        # Engineering selection readiness is deterministic and runs before the
        # free-form consultant.  Otherwise a polished LLM answer can bypass the
        # missing hydraulic/temperature inputs and recommend a plausible but
        # wrong pipe or pump.
        requirements_result: Any | None = None
        if self._should_preflight_engineering_requirements(message, intent, session):
            requirements_result = self.engineering_requirements.assess(
                message,
                intent,
                session,
            )
            self._merge_persistent_slots(
                session,
                requirements_result.slots,
                explicit_slots=intent.slots,
            )
            if intent.category != "other":
                session.category = intent.category
            if requirements_result.needs_clarification and requirements_result.question:
                question = requirements_result.question
                if intent.flags.get("small_talk"):
                    normalized_message = normalize_text(message)
                    prefix = (
                        "Дела хорошо, спасибо. "
                        if "как дела" in normalized_message
                        else "Здравствуйте. "
                    )
                    question = prefix + question
                agents_used.extend(
                    ["EngineeringRequirementsAgent", "ResponseComposerAgent"]
                )
                if session.pending_question == question:
                    session.question_repeats += 1
                else:
                    session.question_repeats = 0
                session.pending_question = question
                session.pending_intent_type = intent.intent_type
                session.pending_category = (
                    intent.category if intent.category != "other" else session.category
                )
                session.pending_slot_keys = self._pending_slot_keys_for_question(
                    question,
                    session.pending_category,
                )
                self._append_history(session, message, question)
                self.sessions.save(session)
                return self._response(
                    session_id,
                    question,
                    [],
                    False,
                    intent,
                    session,
                    agents_used,
                )

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

        slot_result = requirements_result or self.engineering_requirements.assess(
            message,
            intent,
            session,
        )
        agents_used.append("EngineeringRequirementsAgent")
        self._merge_persistent_slots(
            session,
            slot_result.slots,
            explicit_slots=intent.slots,
        )
        session.category = intent.category if intent.category != "other" else session.category
        session.last_intent = intent.intent_type

        if intent.intent_type == "complectation" or session.pending_complectation_parts:
            response = self._handle_complectation(message, session, intent, agents_used)
            self.sessions.save(session)
            return response

        query = self._build_query(message, intent, session)
        direct_products: list[Product] = []
        if not session.pending_complectation_parts and not query.sku:
            direct_products = self.search_agent.search_by_name(message, query)
        if (
            query.category == "boilers"
            and (
                query.slots.get("area_m2")
                or query.slots.get("power_kw") is not None
            )
            and not query.sku
        ):
            # A rating/sizing request such as "котёл 6 кВт" or "котёл на
            # 240 м²" asks for the whole matching catalogue group, not one
            # model found by fuzzy name coverage.  The latter used to turn the
            # number into a model token and hide exact in-stock peers.
            direct_products = []

        if not direct_products and self._stock_or_link_without_context(intent, session, message):
            question = self._stock_clarification_question(intent)
            agents_used.append("ResponseComposerAgent")
            if intent.category == "pumps":
                # Здесь обязательны назначение/тип насоса: свободная перефразировка
                # иногда сокращала вопрос до одного артикула и уводила подбор в шум.
                answer = question
            else:
                answer = self.composer.compose_clarification(question, user_message=message)
                answer = self._guard_composed_answer(answer, "clarification", agents_used)
            session.pending_question = question
            session.pending_intent_type = intent.intent_type
            session.pending_category = (
                intent.category if intent.category != "other" else session.category
            )
            session.pending_slot_keys = self._pending_slot_keys_for_question(
                question,
                session.pending_category,
            )
            self._append_history(session, message, answer)
            self.sessions.save(session)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        hard_refinement_on_shown = bool(
            session.last_products
            and any(
                key in intent.slots
                for key in [
                    "max_price",
                    "min_price",
                    "required_features",
                    "excluded_features",
                    "in_stock",
                ]
            )
        )
        if (
            slot_result.needs_clarification
            and slot_result.question
            and not direct_products
            and not hard_refinement_on_shown
        ):
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
                session.pending_category = (
                    intent.category if intent.category != "other" else session.category
                )
                session.pending_slot_keys = self._pending_slot_keys_for_question(
                    slot_result.question,
                    session.pending_category,
                )
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
        session.pending_category = None
        session.pending_slot_keys = []
        session.question_repeats = 0

        agents_used.append("FeedSearchAgent")
        products = self._drop_underpowered_boilers(
            direct_products or self._safe_search(query),
            query,
        )
        if (
            query.sku
            and query.in_stock_only
            and products
            and not products[0].is_in_stock
        ):
            product = products[0]
            quantity = (
                f"{product.stock_qty} шт."
                if product.stock_qty is not None
                else product.stock_status
            )
            answer = (
                f"Точный артикул {product.sku} найден, но сейчас он не в наличии "
                f"({quantity}). По вашему фильтру «только в наличии» карточку товара "
                "не показываю. Если разрешите аналоги, подберу доступные позиции отдельно."
            )
            self._append_history(session, message, answer)
            self.sessions.save(session)
            agents_used.append("ResponseComposerAgent")
            return self._response(
                session_id,
                answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )
        if not products:
            alternatives = (
                self.search_agent.search_alternatives(query)
                if query.slots.get("allow_alternatives", True)
                else []
            )
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
                    session.shown_product_skus = [card.sku for card in cards]
                    self._remember_result_category(session, cards)
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
        if (
            query.cheap
            and session.last_products
            and query.slots.get("relative_cheaper")
            and not query.slots.get("choose_one")
        ):
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
        card_query = self._card_query_for_products(query, ranked)
        cards = self.card_agent.build_cards(
            ranked,
            card_query,
            limit=self._card_limit(query),
        )
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
            )
            cards = cards[:1]
        else:
            answer = self.composer.compose_products(
                cards,
                query,
                note=self._compose_query_note(query, ranked),
            )
        answer = self._guard_composed_answer(answer, "products", agents_used)
        answer = self._append_companion_hint(answer, session, query.category)
        self._remember_project_cart(session, cards, replace_category=query.category)
        session.last_products = cards
        session.shown_product_skus = [card.sku for card in cards]
        self._remember_result_category(session, cards)
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
        # Complectation has its own continuation state. A stale selection goal
        # must not be restored after the passport answer is complete.
        session.pending_category = None
        session.pending_slot_keys = []
        requested_parts = self._requested_parts(message) or session.pending_complectation_parts
        if not requested_parts:
            requested_parts = ["комплектация"]

        message_text = normalize_text(message)
        safety_context_sku = (
            session.slots.get("electrical_safety_sku")
            if "котл" in message_text
            else None
        )
        sku_from_message = (
            intent.slots.get("sku")
            or session.slots.get("sku")
            or safety_context_sku
        )
        target_product: Product | None = None
        target_card: ProductCard | None = None
        if sku_from_message:
            target_product = self._find_product_by_sku(sku_from_message)
        if not target_product and session.last_products:
            # «Какие из предложенных имеют встроенный насос?» — вопрос про ВСЕ
            # показанные товары. Просить выбрать одну модель здесь неуместно:
            # клиент как раз и хочет сравнить их по этому узлу.
            if len(session.last_products) > 1 and self._asks_about_all_shown(message):
                answer = self._compose_builtin_part_overview(
                    session.last_products, requested_parts
                )
                agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
                session.pending_question = None
                session.pending_intent_type = None
                session.pending_complectation_parts = []
                self._append_history(session, message, answer)
                return self._response(
                    session.session_id,
                    answer,
                    session.last_products,
                    False,
                    intent,
                    session,
                    agents_used,
                )
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
                    "Не буду угадывать узлы системы. Можно подготовить менеджеру краткую "
                    "сводку, но без контакта и вашего подтверждения ничего не отправляю.\n"
                    + self.handoff.compose_answer(summary)
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
            # Модель не должна сокращать обязательные модель/артикул и тип системы
            # до общего вопроса о площади или категории товара.
            answer = question
            agents_used.append("ResponseComposerAgent")
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], False, intent, session, agents_used)

        resolved_category = self.search_agent.canonical_category(target_product)
        if resolved_category and resolved_category != "other":
            session.category = resolved_category

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

        # «Что входит в комплект поставки этого насоса?» — слово «насос» здесь
        # называет сам товар, а не спрашивает, встроен ли насос куда-то ещё.
        # Без этого фильтра вопрос уходит в проверку «насос внутри насоса» и
        # выдаёт бессмысленный отказ вместо содержимого комплекта поставки.
        requested_parts = self._drop_self_referential_parts(requested_parts, target_product)
        if not requested_parts:
            requested_parts = ["комплектация"]

        # Открытый вопрос «что входит в комплект / проверь документацию» — читаем
        # карточку и перечисляем встроенные компоненты, а не отказываем.
        if requested_parts == ["комплектация"]:
            agents_used.append("GuardrailsAgent")
            agents_used.append("ResponseComposerAgent")
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_complectation_parts = []
            # An open package question must describe the shipment package, not
            # infer "built-in" parts from the product's own name.  That inference
            # produced nonsense such as "a circulation pump is built into the
            # boiler" when the selected product itself was a pump.
            answer = self._compose_passport_package_answer(target_card)
            answer = self._guard_composed_answer(answer, "complectation", agents_used)
            session.last_products = [target_card]
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [target_card], False, intent, session, agents_used)

        part_states = self.guardrails.builtin_part_states(
            target_product,
            requested_parts,
        )
        explicitly_not_included = [
            part for part, state in part_states.items() if state is False
        ]
        if explicitly_not_included:
            confirmed = [part for part, state in part_states.items() if state is True]
            unknown = [
                part
                for part in requested_parts
                if part_states.get(part) is None
            ]
            lines = [
                f"Нет: для {target_card.sku} карточка или привязанный паспорт прямо "
                "указывает, что не встроены либо приобретаются отдельно: "
                f"{', '.join(explicitly_not_included)}."
            ]
            if confirmed:
                lines.append(
                    "При этом встроенными подтверждены: "
                    f"{', '.join(confirmed)}."
                )
            if unknown:
                lines.append(
                    "По остальным пунктам подтверждения нет: "
                    f"{', '.join(unknown)}; их нужно уточнить."
                )
            lines.append(f"Карточка товара: {target_card.url}")
            answer = " ".join(lines)
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_complectation_parts = []
            session.last_products = [target_card]
            session.slots["last_complectation_parts"] = requested_parts
            session.slots["last_complectation_sku"] = target_card.sku
            self._append_history(session, message, answer)
            return self._response(
                session.session_id,
                answer,
                [target_card],
                bool(unknown),
                intent,
                session,
                agents_used,
            )

        guard = self.guardrails.validate_complectation_answer(target_product, requested_parts)
        agents_used.append("GuardrailsAgent")
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_complectation_parts = []
        if not guard.ok:
            answer = guard.safe_message or (
                "Не вижу подтверждения комплектации в карточке товара. Лучше проверить документацию или передать вопрос менеджеру."
            )
            checked_parts = ", ".join(requested_parts)
            answer += (
                f" Проверяемый пункт для {target_card.sku}: {checked_parts}; "
                "его наличие или включение в поставку карточкой не подтверждено."
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
        if not self._ensure_products_loaded():
            return []
        return self.search_agent.search(query)

    def _build_query(self, message: str, intent: IntentResult, session: SessionState) -> SearchQuery:
        # Current-turn slots include transient presentation instructions, while
        # the durable session intentionally does not.
        slots = self._normalized_query_slots(
            merge_slots(session.slots, intent.slots)
        )
        return SearchQuery(
            original_text=message,
            category=intent.category if intent.category != "other" else session.category or "other",
            slots=slots,
            sku=slots.get("sku"),
            brand=slots.get("brand"),
            cheap=bool(slots.get("cheap") or intent.flags.get("cheap")),
            in_stock_only=bool(slots.get("in_stock") or intent.flags.get("in_stock")),
        )

    def _merge_persistent_slots(
        self,
        session: SessionState,
        new_slots: dict[str, Any],
        *,
        explicit_slots: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge one turn, replacing mutually exclusive hard constraints."""
        base = dict(session.slots)
        incoming = dict(new_slots)
        explicit = explicit_slots if explicit_slots is not None else new_slots
        if "excluded_features" in explicit:
            incoming.pop("required_features", None)
            excluded = set(
                str(value) for value in explicit.get("excluded_features") or []
            )
            required = [
                value
                for value in base.get("required_features", [])
                if str(value) not in excluded
            ]
            if required:
                base["required_features"] = required
            else:
                base.pop("required_features", None)
        if "required_features" in explicit:
            incoming.pop("excluded_features", None)
            required = set(
                str(value) for value in explicit.get("required_features") or []
            )
            excluded = [
                value
                for value in base.get("excluded_features", [])
                if str(value) not in required
            ]
            if excluded:
                base["excluded_features"] = excluded
            else:
                base.pop("excluded_features", None)
        merged_slots = merge_slots(base, incoming)
        # Presentation commands apply to one answer only. Persisting
        # ``result_limit=1`` after «назови один» silently restricted every
        # later catalogue request in the same session.
        session.slots = {
            key: value
            for key, value in merged_slots.items()
            if key not in TRANSIENT_QUERY_SLOTS
        }
        return merged_slots

    def _card_query_for_products(
        self,
        query: SearchQuery,
        products: list[Product],
    ) -> SearchQuery:
        """Use a product's canonical category for exact-SKU card attributes."""
        if query.category != "other" or not products:
            return query
        category = self.search_agent.canonical_category(products[0])
        if not category or category == "other":
            return query
        return query.model_copy(update={"category": category})

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
        heater_type = normalize_text(str(slots.get("heater_type") or ""))
        if heater_type:
            if "проточ" in heater_type or heater_type in {"instant", "tankless"}:
                slots["heater_type"] = "проточный"
            elif "косвен" in heater_type or heater_type == "indirect":
                slots["heater_type"] = "косвенного нагрева"
            elif "накоп" in heater_type or heater_type in {"storage", "tank"}:
                slots["heater_type"] = "накопительный"
        energy_source = normalize_text(str(slots.get("energy_source") or ""))
        if energy_source:
            if "комбин" in energy_source or energy_source == "combined":
                slots["energy_source"] = "комбинированный"
            elif "косвен" in energy_source or energy_source == "indirect":
                slots["energy_source"] = "косвенный"
            elif "газ" in energy_source or energy_source == "gas":
                slots["energy_source"] = "газовый"
            elif "электр" in energy_source or energy_source in {"electric", "electrical"}:
                slots["energy_source"] = "электрический"
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
            "volume_l",
            "heater_type",
            "energy_source",
            "mounting",
            "orientation",
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

    def _should_preflight_engineering_requirements(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        category = intent.category if intent.category != "other" else session.category
        if category not in self.engineering_requirements.CATEGORIES:
            return False
        if intent.intent_type in {
            "exact_sku",
            "link_request",
            "complectation",
            "small_talk",
            "out_of_scope",
        }:
            return False
        if session.pending_complectation_parts:
            return False
        text = normalize_text(message)
        if session.last_products and {
            "max_price",
            "min_price",
            "required_features",
            "excluded_features",
            "required_builtin_parts",
            "excluded_builtin_parts",
            "in_stock",
        }.intersection(intent.slots):
            # This is a correction/refinement of an already grounded candidate
            # set. The ordinary hard-constraint path must re-filter it before a
            # new discovery questionnaire can start.
            return False
        if any(
            text == normalize_text(html.unescape(product.name))
            for product in self.search_agent.products
        ):
            # A full catalogue name is an identity lookup, even when it contains
            # spaces and therefore is not shaped like an SKU.
            return False
        if (
            category == "pipes"
            and re.search(r"\bpn\s*\d{1,2}\b", text)
            and re.search(
                r"\b(?:ppr|ппр|полипроп|pex|pe-x|pe-rt|pert|металлопласт|пнд|пэ100)\b",
                text,
            )
            and re.search(r"(?<!\d)\d{2,3}\s*(?:мм)?\b", text)
        ):
            # This is a concrete catalogue specification, not a request for the
            # bot to design a pipe. The normal direct-name lookup still checks
            # that the exact product exists.
            return False
        return True

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

    def _looks_like_pump_boiler_compatibility(self, message: str) -> bool:
        text = normalize_text(message)
        mentioned_products = self.search_agent.resolve_sku_mentions(message)
        has_boiler_target = bool(
            any(marker in text for marker in ["котел", "котл"])
            or any(
                self.search_agent.canonical_category(product) == "boilers"
                for product in mentioned_products
            )
        )
        return bool(
            has_boiler_target
            and (
                "совмест" in text
                or "подойд" in text
                or re.search(r"подход\w*.*котл", text)
                or any(
                    marker in text
                    for marker in [
                        "под какой котел",
                        "под какой котёл",
                        "к какому котлу",
                        "с каким котлом",
                    ]
                )
            )
        )

    def _maybe_pump_compatibility_answer(self, message: str, session: SessionState) -> str | None:
        if not self._looks_like_pump_boiler_compatibility(message):
            return None
        mentioned_products = self.search_agent.resolve_sku_mentions(message)
        explicit_pump = next(
            (
                product
                for product in mentioned_products
                if self.search_agent.canonical_category(product) == "pumps"
            ),
            None,
        )
        pump_card: ProductCard | None = None
        pump_product: Product | None = explicit_pump
        if explicit_pump:
            pump_card = next(
                (
                    card
                    for card in session.last_products
                    if normalize_sku_token(card.sku)
                    == normalize_sku_token(explicit_pump.sku)
                ),
                None,
            )
            if pump_card is None:
                pump_card = self.card_agent.build_card(
                    explicit_pump,
                    SearchQuery(
                        original_text=message,
                        category="pumps",
                        slots={"sku": explicit_pump.sku},
                    ),
                )
        else:
            for card in session.last_products:
                product = self._find_product_by_sku(card.sku)
                if product and self.search_agent.canonical_category(product) == "pumps":
                    pump_card = card
                    pump_product = product
                    break
        if not pump_card or not pump_product:
            return None
        # The explicit source SKU wins over list position and remains the object
        # referred to by later pronouns.
        session.last_products = [pump_card]
        session.category = "pumps"

        details: list[str] = []
        for key, value in pump_card.characteristics.items():
            key_norm = normalize_text(key)
            if any(marker in key_norm for marker in ["напор", "монтаж", "присоедин", "мощность"]):
                details.append(f"{key}: {value}")
        dn, thread, _ = self._pump_connection_facts(pump_product)
        if dn and not any("диаметр условного прохода" in normalize_text(item) for item in details):
            details.append(f"DN {dn}")
        if thread and not any("присоедин" in normalize_text(item) for item in details):
            details.append(f"присоединительная резьба {thread}″")
        detail_text = "; ".join(details) if details else "в карточке нет достаточных параметров для проверки совместимости"

        boiler = next(
            (
                product
                for product in mentioned_products
                if self.search_agent.canonical_category(product) == "boilers"
            ),
            None,
        )
        if boiler:
            boiler_components = self.guardrails.list_builtin_components(boiler)
            built_in_pump = any("насос" in component for component in boiler_components)
            boiler_connection = self._boiler_heating_connection(boiler)
            boiler_facts: list[str] = []
            if built_in_pump:
                boiler_facts.append("карточка подтверждает встроенный циркуляционный насос")
            if boiler_connection:
                boiler_facts.append(
                    f"подключение контура отопления указано как G {boiler_connection}"
                )
            boiler_detail = (
                "; ".join(boiler_facts)
                if boiler_facts
                else "в карточке нет полной гидравлической схемы подключения"
            )
            return (
                f"Прямую совместимость {pump_card.sku} с котлом {boiler.sku} по одним "
                "карточкам не подтверждаю — для этого нужна схема и гидравлический расчёт. "
                f"У насоса {pump_card.sku}: {detail_text}. У котла {boiler.sku}: "
                f"{boiler_detail}. "
                + (
                    "Поскольку в котле уже есть штатный насос, отдельный VRS не следует "
                    "считать обязательной заменой: его рассматривают только для отдельного "
                    "контура, длинной ветки или другого проектного назначения. "
                    if built_in_pump
                    else ""
                )
                + "Нужно сверить расход, сопротивление контура, место установки и переходы; "
                "без схемы не буду подтверждать прямое подключение этого насоса."
            )

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

    @staticmethod
    def _pump_connection_facts(
        product: Product,
    ) -> tuple[str | None, str | None, str]:
        nominal_dn: str | None = None
        thread: str | None = None
        for key, value in product.attributes_normalized.items():
            key_text = normalize_text(key)
            value_text = normalize_text(value)
            if (
                ("диаметр" in key_text and "проход" in key_text)
                or key_text.strip() == "dn"
            ):
                match = re.search(r"\b(15|20|25|32|40|50)\b", value_text)
                if match:
                    nominal_dn = match.group(1)
            if "присоедин" in key_text or "резьб" in key_text:
                match = re.search(r"\b(1\s+1/2|1/2|3/4|1|2)\b", value_text)
                if match:
                    thread = re.sub(r"\s+", " ", match.group(1)).strip()
        if not nominal_dn:
            match = re.search(
                r"(?<!\d)(15|20|25|32|40|50)\s*[-/]\s*\d{1,2}(?!\d)",
                normalize_text(product.name),
            )
            if match:
                nominal_dn = match.group(1)
        source = "passport" if product.docs_text and (nominal_dn or thread) else "card"
        return nominal_dn, thread, source

    @staticmethod
    def _boiler_heating_connection(product: Product) -> str | None:
        sources = [
            " ".join(
                f"{key} {value}"
                for key, value in product.attributes_normalized.items()
                if any(
                    marker in normalize_text(key)
                    for marker in ["отоплен", "патруб", "подключ"]
                )
            ),
            product.description or "",
            product.docs_text or "",
        ]
        for source in sources:
            text = normalize_text(source)
            match = re.search(
                r"(?:отоплен\w*|патруб\w*)[^.!?]{0,80}?"
                r"(?:g\s*)?(1\s+1/2|1/2|3/4|1|2)\b",
                text,
            )
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()
        return None

    def _maybe_shown_pump_connection_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        if not any(marker in text for marker in ["присоедин", "резьб", "подключ"]):
            return None
        if any(
            marker in text
            for marker in [
                "электр",
                "электросет",
                "розет",
                "кабел",
                "питан",
                "220",
                "380",
                "узо",
                "зазем",
            ]
        ):
            return None
        pump_card = self._first_shown_pump_card(session)
        if not pump_card:
            return None
        product = self._find_product_by_sku(pump_card.sku)
        if not product:
            return None
        nominal_dn, thread, source = self._pump_connection_facts(product)
        if nominal_dn and thread:
            source_text = (
                "По привязанному паспорту"
                if source == "passport"
                else "По карточке и маркировке"
            )
            return (
                f"{source_text} у {pump_card.sku} условный проход DN {nominal_dn}, "
                f"присоединительная резьба насоса — {thread}″. "
                "Размер крана или перехода со стороны системы нужно сверять отдельно: "
                "DN насоса и резьба его корпуса не всегда равны размеру трубопровода."
            )
        if nominal_dn:
            return (
                f"В маркировке и карточке {pump_card.sku} подтверждено DN {nominal_dn}, "
                "но точный размер присоединительной резьбы в доступных данных не указан. "
                "Не буду переводить DN в дюймы без паспорта конкретной модели."
            )
        return (
            f"Для {pump_card.sku} в карточке и привязанном паспорте не нахожу однозначного "
            "размера присоединения. Нужна маркировка резьбы или фото подключения."
        )

    def _maybe_shown_pump_electrical_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        if not any(
            marker in text
            for marker in [
                "электр",
                "электросет",
                "розет",
                "кабел",
                "питан",
                "220",
                "380",
                "узо",
                "зазем",
            ]
        ):
            return None
        if not any(
            marker in text
            for marker in ["подключ", "питан", "розет", "кабел", "электросет"]
        ):
            return None
        pump_card = self._first_shown_pump_card(session)
        if not pump_card:
            return None
        product = self._find_product_by_sku(pump_card.sku)
        if not product:
            return None

        source_text = normalize_text(
            " ".join(
                [
                    product.docs_text or "",
                    " ".join(
                        f"{key} {value}"
                        for key, value in product.attributes_normalized.items()
                    ),
                ]
            )
        )
        voltage_match = re.search(
            r"\b(220|230|380)\s*(?:в|v|ас|ac)?\b",
            source_text,
        )
        voltage_fact = (
            f"Для {pump_card.sku} в документации указано питание {voltage_match.group(1)} В."
            if voltage_match
            else (
                f"Для {pump_card.sku} в доступной карточке не нахожу полного "
                "описания электрического подключения."
            )
        )
        socket_warning = (
            " Подключение к обычной розетке по одному значению напряжения не подтверждаю:"
            if "розет" in text
            else " Схему подключения по одному значению напряжения не подтверждаю:"
        )
        return (
            voltage_fact
            + socket_warning
            + " нужно сверить паспорт именно этой модификации, класс защиты, заземление, "
            "защитный аппарат и способ подключения. Монтаж должен выполнять "
            "квалифицированный электрик; параметры водяного контура к этому вопросу "
            "не относятся."
        )

    def _maybe_pump_union_valves_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if not self._looks_like_pump_union_valves_request(message):
            return None
        explicit_system_size = bool(
            re.search(
                r"(?<!\d)(?:g\s*)?(?:1\s+1/2|1/2|3/4|1|2)\s*"
                r"(?:дюйм|inch|[\"″])",
                text,
            )
        )

        pump_product = next(
            (
                product
                for product in self.search_agent.resolve_sku_mentions(message)
                if self.search_agent.canonical_category(product) == "pumps"
            ),
            None,
        )
        pump_card = self._first_shown_pump_card(session)
        if not pump_product and pump_card:
            pump_product = self._find_product_by_sku(pump_card.sku)
        if not pump_product:
            return None
        nominal_dn, thread, source = self._pump_connection_facts(pump_product)
        facts: list[str] = []
        if nominal_dn:
            facts.append(f"DN {nominal_dn}")
        if thread:
            facts.append(f"резьба корпуса {thread}″")
        facts_text = ", ".join(facts) if facts else "размер соединения не подтверждён"

        session.category = "valves"
        session.slots.update(
            {
                "pump_accessory_sku": pump_product.sku,
                "union": True,
                "requested_quantity": 2,
                "application": "отопление",
                "in_stock": True,
            }
        )
        if explicit_system_size:
            size_match = re.search(
                r"(?<!\d)(?:g\s*)?(1\s+1/2|1/2|3/4|1|2)\s*"
                r"(?:дюйм|inch|[\"″])",
                text,
            )
            if size_match:
                session.slots["size_inch"] = re.sub(
                    r"\s+",
                    " ",
                    size_match.group(1),
                ).strip()
            session.pending_category = None
            session.pending_intent_type = None
            session.pending_slot_keys = []
            session.pending_question = None
            # The completion handler runs immediately after this one.
            return None
        session.pending_category = "valves"
        session.pending_intent_type = "attribute_request"
        session.pending_slot_keys = ["size_inch"]
        session.pending_question = (
            "Какой размер резьбы/трубы со стороны системы должен быть у каждого крана?"
        )
        return (
            f"Для насоса {pump_product.sku} "
            f"{'паспорт подтверждает' if source == 'passport' else 'карточка указывает'}: "
            f"{facts_text}. "
            "Но по этим данным нельзя автоматически выбрать размер двух шаровых кранов "
            "с американкой: нужно знать соединение со стороны трубопровода после штатных "
            "присоединительных гаек. Напишите этот размер в дюймах (например, 3/4 или 1) — "
            "тогда проверю один подходящий артикул в наличии и посчитаю 2 шт.; случайные "
            "краны показывать не буду."
        )

    @staticmethod
    def _looks_like_pump_union_valves_request(message: str) -> bool:
        text = normalize_text(message)
        return bool(
            "американк" in text
            and any(marker in text for marker in ["кран", "краны", "вентил"])
            and (
                "насос" in text
                or any(
                    marker in text
                    for marker in [
                        "к нему",
                        "к этому",
                        "для него",
                        "для этого",
                        "ты сам предложил",
                        "ты предложил",
                    ]
                )
            )
        )

    def _maybe_complete_pump_union_valves_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        pump_sku = session.slots.get("pump_accessory_sku")
        if not pump_sku or session.category != "valves":
            return None
        slots = merge_slots(session.slots, intent.slots)
        size_inch = slots.get("size_inch")
        if not size_inch:
            return None
        quantity = int(slots.get("requested_quantity") or 2)
        query_slots = {
            "size_inch": size_inch,
            "union": True,
            "application": slots.get("application") or "отопление",
            "in_stock": True,
            "choose_one": True,
            "result_limit": 1,
        }
        query = SearchQuery(
            original_text=message,
            category="valves",
            slots=query_slots,
            in_stock_only=True,
            limit=10,
        )
        products = [
            product
            for product in self.search_agent.search(query)
            if product.stock_qty is not None and product.stock_qty >= quantity
            and self._valve_size_is_unambiguous(product, str(size_inch))
        ]
        if not products:
            answer = (
                f"Не вижу крана с американкой размера {size_inch} для отопления "
                f"с подтверждённым остатком не меньше {quantity} шт. "
                "Другой размер вместо указанного показывать не буду."
            )
            return answer, []

        product = products[0]
        card = self.card_agent.build_card(product, query)
        if not card:
            return None
        guard = self.guardrails.validate_cards([card], [product], query)
        if not guard.ok:
            return (
                "Не могу подтвердить карточку крана по цене, наличию и размеру; "
                "случайную замену показывать не буду.",
                [],
            )

        total = card.price * quantity
        remaining = (card.stock_qty or 0) - quantity
        answer = (
            f"Для трубопроводной стороны {size_inch} выбрал один подтверждённый вариант "
            f"с американкой для насоса {pump_sku}:\n"
            f"- {card.sku} — {card.name}.\n"
            f"- Количество: {quantity} шт.\n"
            f"- Цена за единицу: {card.price:g} {card.currency}.\n"
            f"- Итого: {total:g} {card.currency}.\n"
            f"- Остаток сейчас: {card.stock_qty} шт.; после {quantity} шт. останется {remaining} шт.\n"
            f"- Ссылка: {card.url}\n"
            "Карточка отфильтрована по названному вами размеру, типу «с американкой» "
            "и назначению для отопления. Какую именно сторону соединения описывает "
            "размер, а также переходы и фактическую резьбу нужно сверить по схеме "
            "и маркировке перед монтажом."
        )
        session.category = "valves"
        session.last_products = [card]
        session.slots.update(
            {
                "last_accessory_pump_sku": pump_sku,
                "size_inch": size_inch,
                "union": True,
                "application": "отопление",
            }
        )
        session.slots.pop("pump_accessory_sku", None)
        session.slots.pop("requested_quantity", None)
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_category = None
        session.pending_slot_keys = []
        return answer, [card]

    @staticmethod
    def _valve_size_is_unambiguous(product: Product, requested: str) -> bool:
        """Reject reducers when the feed does not identify the requested side."""
        evidence = " ".join(
            [
                product.name,
                " ".join(
                    f"{key} {value}"
                    for key, value in product.attributes_normalized.items()
                    if any(
                        marker in normalize_text(str(key))
                        for marker in ["диаметр", "размер", "присоедин", "резьб"]
                    )
                ),
            ]
        )
        normalized_requested = re.sub(r"\s+", "", normalize_text(requested))
        sizes = {
            re.sub(r"\s+", "", match.group(1))
            for match in re.finditer(
                r"(?<!\d)(1\s+1/2|1/2|3/4|1|2)\s*(?:дюйм|inch|[\"″])?",
                normalize_text(evidence),
            )
        }
        return normalized_requested in sizes and len(sizes) == 1

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

        if not session.last_products:
            return None

        # A question such as ``для чего этот насос и что в него входит`` has
        # two parts: product purpose and shipment package.  Treating the word
        # ``насос`` as a requested built-in component used to invoke a boiler
        # template and produced "в котёл встроен циркуляционный насос" for a
        # drainage pump.  Resolve the shown product first and answer both parts
        # only from its feed card/passport.
        if self._asks_shown_pump_purpose(text):
            pump_card, ambiguous = self._resolve_shown_product_card(message, session)
            if ambiguous:
                return self._complectation_target_question(session.last_products)
            if not pump_card:
                pump_card = self._first_shown_pump_card(session)
            product = self._find_product_by_sku(pump_card.sku) if pump_card else None
            if not pump_card or not product or self.search_agent.canonical_category(product) != "pumps":
                return None
            session.last_products = [pump_card]
            answer = self._compose_pump_purpose_answer(pump_card, product)
            if self._asks_package_contents(text):
                answer += "\n\n" + self._compose_passport_package_answer(pump_card)
            return answer

        if not self._asks_pump_application_fit(text):
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

    def _maybe_shown_product_purpose_answer(
        self,
        message: str,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        """Answer a shown product's purpose for every catalogue category.

        This is deliberately product-neutral.  Purpose/package questions must
        not be sent through a boiler or pump-specific response template merely
        because the product name occurs in the question.
        """
        if not session.last_products:
            return None
        text = normalize_text(message)
        if not self._asks_shown_product_purpose(text):
            return None

        target_card, ambiguous = self._resolve_shown_product_card(message, session)
        if ambiguous:
            return self._shown_product_target_question(session.last_products), session.last_products
        if not target_card:
            return None
        product = self._find_product_by_sku(target_card.sku)
        if not product:
            return None

        session.last_products = [target_card]
        answer = self._compose_product_purpose_answer(target_card, product)
        if self._asks_package_contents(text):
            answer += "\n\n" + self._compose_passport_package_answer(target_card)
        return answer, [target_card]

    def _asks_shown_product_purpose(self, text: str) -> bool:
        markers = [
            "для чего этот",
            "для чего эта",
            "для чего это",
            "для чего он",
            "для чего она",
            "для чего насос",
            "для чего котел",
            "для чего котёл",
            "для чего труба",
            "для чего кран",
            "зачем нужен этот",
            "зачем нужна эта",
            "какое назначение",
            "каково назначение",
            "назначение этого",
            "назначение этой",
            "что делает этот",
            "что делает эта",
            "где применяют этот",
            "где применяют эту",
            "где применяется этот",
            "где применяется эта",
        ]
        return any(marker in text for marker in markers)

    def _shown_product_target_question(self, cards: list[ProductCard]) -> str:
        lines = ["Уточните, о какой из показанных моделей речь:"]
        for index, card in enumerate(cards[:3], start=1):
            lines.append(f"{index}. {card.sku} — {html.unescape(card.name)}")
        lines.append("Напишите номер, модель или артикул.")
        return "\n".join(lines)

    def _compose_product_purpose_answer(self, card: ProductCard, product: Product) -> str:
        attributes = product.attributes_normalized or {}
        for key, value in attributes.items():
            key_text = normalize_text(key)
            if any(
                marker in key_text
                for marker in ["назначение", "область применения", "применение"]
            ) and str(value).strip():
                return f"По карточке товара {card.sku}, назначение: {value}."

        description = html.unescape(product.description or "").strip()
        if description:
            sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", description)
                if sentence.strip()
            ]
            grounded = " ".join(sentences[:2]).strip()
            if grounded:
                if len(grounded) > 520:
                    grounded = grounded[:517].rsplit(" ", 1)[0] + "..."
                return f"По описанию товара {card.sku}: {grounded}"

        product_type = next(
            (
                str(value).strip()
                for key, value in attributes.items()
                if normalize_text(key) in {"тип товара", "тип изделия"} and str(value).strip()
            ),
            "",
        )
        type_note = f" В карточке он обозначен как «{product_type}»." if product_type else ""
        return (
            f"Для {card.sku} в фиде нет отдельного подтверждённого описания назначения."
            f"{type_note} Не буду додумывать применение; его нужно уточнить по паспорту или "
            f"у менеджера. Карточка: {card.url}"
        )

    def _asks_shown_pump_purpose(self, text: str) -> bool:
        purpose_markers = [
            "для чего",
            "зачем нужен",
            "зачем этот",
            "что делает",
            "назначение",
            "для каких задач",
            "где примен",
        ]
        product_refs = ["насос", "этот", "эта модель", "он ", "его "]
        return any(marker in text for marker in purpose_markers) and any(
            marker in text for marker in product_refs
        )

    def _asks_package_contents(self, text: str) -> bool:
        return any(
            marker in text
            for marker in [
                "что входит",
                "что в него входит",
                "что в комплект",
                "комплектац",
                "комплект поставки",
            ]
        )

    def _compose_pump_purpose_answer(self, card: ProductCard, product: Product) -> str:
        description = html.unescape(product.description or "").strip()
        if description:
            sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", description)
                if sentence.strip()
            ]
            grounded = " ".join(sentences[:2]).strip()
            if grounded:
                if len(grounded) > 520:
                    grounded = grounded[:517].rsplit(" ", 1)[0] + "..."
                return f"По описанию товара {card.sku}: {grounded}"

        kind = self._pump_kind_from_card(card)
        purpose_by_kind = {
            "дренажный": "предназначен для откачки воды",
            "скважинный": "предназначен для подъёма и подачи воды из скважины",
            "циркуляционный": "предназначен для циркуляции теплоносителя в системе отопления",
            "поверхностный": "предназначен для подачи воды из доступного источника",
            "насосная станция": "предназначена для автономной подачи воды и поддержания давления",
        }
        purpose = purpose_by_kind.get(kind)
        if purpose:
            return (
                f"{card.name} {purpose}. {self._pump_card_details(card)} "
                f"Точные ограничения нужно сверить по карточке: {card.url}"
            )
        return (
            f"В карточке {card.sku} не вижу достаточно данных, чтобы надёжно описать назначение. "
            f"Не буду додумывать; уточните его у менеджера. Карточка: {card.url}"
        )

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
        # Пользователь отвечает на наш же вопрос «по какой модели проверить
        # комплектацию?», и в ответе он цитирует название товара. Разбирать эту
        # реплику как новый вопрос про тип котла нельзя: вопрос про насос
        # терялся, а в ответ приходило «Да, это электрический котёл».
        if session.pending_intent_type == "complectation":
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
        # «электрический»/«газовый» внутри процитированного названия товара — это
        # не вопрос о типе. Считаем вопросом только то слово, которого нет в
        # названиях показанных карточек.
        shown_names = " ".join(normalize_text(card.name) for card in session.last_products)
        asks_known_type = any(
            marker in text and marker not in shown_names for marker in ("электр", "газ")
        )
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

    def _maybe_shown_boiler_contours_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        if not session.last_products:
            return None
        text = normalize_text(message)
        if "контур" not in text:
            return None
        if not (
            re.search(r"\b(он|этот|эта|это|модель|котел)\b", text)
            or self._references_shown_products(message)
        ):
            return None

        card, ambiguous = self._resolve_shown_product_card(message, session)
        if ambiguous or not card:
            resolved: list[tuple[ProductCard, str]] = []
            for candidate in session.last_products:
                product = self._find_product_by_sku(candidate.sku)
                contours = self._boiler_contours_from_product(product)
                if contours:
                    resolved.append((candidate, contours))
            contour_values = {value for _, value in resolved}
            if not resolved or len(contour_values) != 1:
                return None
            actual = resolved[0][1]
            cards = session.last_products
            label = "Все показанные котлы"
        else:
            product = self._find_product_by_sku(card.sku)
            actual = self._boiler_contours_from_product(product)
            if not actual:
                return None
            cards = [card]
            label = f"{card.sku} — {card.name}"

        intent.slots["contours"] = actual
        session.slots["contours"] = actual
        if "одноконтур" in text:
            prefix = "Да" if actual == "одноконтурный" else "Нет"
        elif "двухконтур" in text:
            prefix = "Да" if actual == "двухконтурный" else "Нет"
        else:
            prefix = "По карточке товара"
        return (
            f"{prefix}: {label} — {actual}. Контурность сверена по названию и "
            "структурированным характеристикам карточки.",
            cards,
        )

    def _boiler_contours_from_product(self, product: Product | None) -> str | None:
        if not product:
            return None
        structured_values = [
            str(value)
            for key, value in (product.attributes_normalized or {}).items()
            if "контур" in normalize_text(key)
        ]
        trusted = normalize_text(" ".join([product.name, *structured_values]))
        if "двухконтур" in trusted:
            return "двухконтурный"
        if "одноконтур" in trusted:
            return "одноконтурный"
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

    def _maybe_choose_one_answer(
        self,
        message: str,
        session: SessionState,
        intent: IntentResult,
    ) -> tuple[str, ProductCard] | None:
        if not session.last_products or not self._wants_choose_one(message):
            return None
        refinement_keys = {
            "max_price",
            "min_price",
            "required_features",
            "excluded_features",
            "required_builtin_parts",
            "excluded_builtin_parts",
            "diameter_mm",
            "size_inch",
            "head_m",
            "mounting_length_mm",
            "connection_size",
            "power_kw",
            "area_m2",
            "boiler_type",
            "contours",
            "voltage_v",
            "pump_type",
            "pump_use",
        }
        if any(
            key in intent.slots
            and intent.slots.get(key) != session.slots.get(key)
            for key in refinement_keys
        ):
            # Apply a newly stated constraint to the full candidate set first;
            # choosing directly from stale cards would ignore the refinement.
            return None
        text = normalize_text(message)
        if any(
            marker in text
            for marker in ["самый дешев", "самого дешев", "дешевле всех"]
        ):
            # Search the whole constrained catalogue; the cheapest suitable
            # item may not be present in the last three displayed cards.
            return None
        wants_in_stock = bool(
            intent.flags.get("in_stock")
            or intent.slots.get("in_stock")
            or "в наличии" in text
        )
        candidates = session.last_products
        if wants_in_stock:
            candidates = [
                card for card in candidates if self._card_is_in_stock(card)
            ]
            if not candidates:
                # Let the normal catalogue path search beyond the stale cards.
                return None
        card = candidates[0]
        query = SearchQuery(
            original_text=message,
            category=session.category or "other",
            slots={**session.slots, "choose_one": True, "result_limit": 1},
            cheap="дешев" in text,
        )
        return (
            self.composer.compose_choose_one(
                card,
                query,
            ),
            card,
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
        refinement_keys = {
            "max_price",
            "min_price",
            "required_features",
            "excluded_features",
            "required_builtin_parts",
            "excluded_builtin_parts",
            "area_m2",
            "power_kw",
            "voltage_v",
            "contours",
            "in_stock",
        }
        if any(
            key in intent.slots
            and intent.slots.get(key) != session.slots.get(key)
            for key in refinement_keys
        ):
            return None
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
        intent: IntentResult,
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
        effective_slots = merge_slots(session.slots, intent.slots)
        if "площад" in pending and not effective_slots.get("area_m2"):
            return (
                "Чтобы показать цены релевантных котлов, сначала нужна площадь: "
                "на сколько м² подбираете котёл?"
            )
        if "контур" in pending and not effective_slots.get("contours"):
            return (
                "Чтобы показать цены подходящих моделей, сначала уточните: одноконтурный "
                "котёл (только отопление) или двухконтурный (отопление и горячая вода)?"
            )
        if (
            "газов" in pending
            and "электр" in pending
            and not effective_slots.get("boiler_type")
        ):
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

        self._merge_persistent_slots(session, intent.slots)
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
            category = (
                self.search_agent.canonical_category(product)
                if product
                else "other"
            )
            if (
                product
                and (not categories or category in categories)
                and self.search_agent.matches_constraints(
                    product,
                    category,
                    retrieval_slots,
                )
                and normalize_sku_token(product.sku) not in seen
            ):
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

        # Apply the same deterministic constraints to LLM-selected cards as to
        # ordinary catalogue search. If any card is removed, also replace the
        # prose so it cannot keep recommending the rejected item.
        checked_cards: list[ProductCard] = []
        for card in cards:
            product = self._find_product_by_sku(card.sku)
            if not product:
                continue
            category = self.search_agent.canonical_category(product)
            if not self.search_agent.matches_constraints(
                product,
                category,
                retrieval_slots,
            ):
                continue
            card_query = SearchQuery(
                original_text=message,
                category=category,
                slots=retrieval_slots,
                cheap=bool(
                    retrieval_slots.get("cheap")
                    or intent.flags.get("cheap")
                ),
                in_stock_only=bool(
                    retrieval_slots.get("in_stock")
                    or intent.flags.get("in_stock")
                ),
            )
            guard = self.guardrails.validate_cards([card], [product], card_query)
            if guard.ok:
                checked_cards.append(card)
        if len(checked_cards) != len(cards):
            cards = checked_cards
            answer = (
                self._plain_catalog_answer(cards)
                if cards
                else (
                    "По указанным ограничениям не нашёл подтверждённого товара. "
                    "Измените бюджет или обязательные характеристики."
                )
            )
            agents_used.append("GuardrailsAgent")
            self.consultant.last_llm_output_accepted = False
            self.consultant.last_llm_rejection_reason = (
                "replaced_by_hard_constraint_guard"
            )

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
            self.consultant.last_llm_output_accepted = False
            self.consultant.last_llm_rejection_reason = "replaced_by_boiler_sizing_guard"

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
        if intent.category == "boilers" and intent.slots.get("voltage_v"):
            return False

        if (
            session.pending_question
            and intent.category == "boilers"
            and {"area_m2", "power_kw", "boiler_type", "contours"}.intersection(intent.slots)
        ):
            return False

        if intent.slots.get("boiler_water_heater_pair"):
            return True

        if self._is_explicit_boiler_product_request(text, intent):
            return False

        concrete_non_boiler = {
            "water_heaters",
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
            "volume_l",
            "heater_type",
            "energy_source",
            "mounting",
            "orientation",
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
            "водонагрев",
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

    def _message_rejects_gas(self, text: str) -> bool:
        return bool(
            any(marker in text for marker in self.NO_GAS_MARKERS)
            or re.search(r"\bгаз[ауы]?\s+н[еэ]+т\w*\b", text)
            or re.search(r"\bн[еэ]+т\w*\s+газ[ауы]?\b", text)
        )

    def _update_project_state(self, message: str, intent: IntentResult, session: SessionState) -> None:
        text = normalize_text(message)
        if intent.slots.get("area_m2"):
            mentions_warm_floor = bool(
                re.search(r"\bпол(?:а|у|ом|е)?\b", text)
                and any(marker in text for marker in ["тепл", "тёпл"])
            )
            explicitly_names_house_area = bool(
                re.search(
                    r"(?:дом|площад\w*\s+дом\w*|общ\w*\s+площад\w*)\D{0,20}\d{2,4}"
                    r"|\d{2,4}\D{0,12}(?:дом|общ\w*\s+площад)",
                    text,
                )
            )
            # Generic area extraction sees ``тёплый пол 60 м²`` as the house
            # area too.  Preserve the already supplied 180 m² house value and
            # let the complex-flow parser store 60 separately below.
            warm_floor_subarea_only = (
                mentions_warm_floor
                and session.slots.get("complex_engineering_request")
                and not explicitly_names_house_area
            )
            if not warm_floor_subarea_only:
                session.slots["area_m2"] = intent.slots["area_m2"]
        if intent.slots.get("boiler_type"):
            session.slots["boiler_type"] = intent.slots["boiler_type"]
        if intent.slots.get("contours"):
            session.slots["contours"] = intent.slots["contours"]

        # Energy words inside a water-heater request describe that appliance,
        # not the heat source of an отопление project.  Keeping them in
        # ``heat_sources`` made a later «покажи ещё» jump from water heaters to
        # boilers in the consultant branch.
        heat_project_context = intent.category != "water_heaters" and bool(
            intent.category == "boilers"
            or session.category == "boilers"
            or session.slots.get("project")
            or session.slots.get("project_scope") in {"heating", "general", "warm_floor"}
            or any(
                marker in text
                for marker in [
                    "котел",
                    "котёл",
                    "котельн",
                    "отоплен",
                    "источник тепла",
                    "обогрев",
                    "строю дом",
                    "построить дом",
                ]
            )
        )
        if heat_project_context:
            # Источники тепла с учётом отрицания:
            # «газа нет» → has_gas=False, а не +газ.
            no_gas = self._message_rejects_gas(text)
            if no_gas:
                session.slots["has_gas"] = False
                # An explicit negative constraint supersedes a gas selection kept
                # from an earlier turn or inferred from the word ``газ`` itself.
                session.slots["boiler_type"] = "электрический"
                session.slots.pop("boiler_types", None)
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
            if (
                session.slots.get("has_gas") is True
                and session.slots.get("has_electricity") is True
            ):
                mentions_both_sources = "газ" in text and "электр" in text
                if mentions_both_sources:
                    session.slots.pop("boiler_type", None)
                    session.slots["boiler_types"] = ["газовый", "электрический"]

        if any(word in text for word in ["дом", "коттедж", "построить", "строю"]):
            session.slots.setdefault("project", "частный дом")
        if re.search(r"\bс\s+(?:встроенн\w*\s+)?бойлер\w*\b", text):
            # Обязательное требование должно пережить уточняющие вопросы и попасть
            # в handoff, даже если последняя реплика пользователя — только команда
            # «передай менеджеру».
            session.slots["boiler_requirement"] = "с бойлером"
        elif re.search(r"\bбез\s+бойлер\w*\b", text):
            session.slots["boiler_requirement"] = "без бойлера"
        mentions_floor = bool(re.search(r"\bпол(?:а|у|ом|е)?\b", text))
        if "водян" in text and mentions_floor:
            session.slots["warm_floor_type"] = "водяной"
        elif "от котл" in text and session.slots.get("project_scope") == "warm_floor":
            session.slots["warm_floor_type"] = "водяной"
        elif "электр" in text and mentions_floor:
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
        slots = merge_slots(session.slots, intent.slots)
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
        if (
            "водонагрев" in text
            or (
                "бойлер" in text
                and not re.search(r"\bкот[её]л\w*\s+с\s+(?:встроенн\w*\s+)?бойлер\w*", text)
            )
        ):
            named.append("water_heaters")
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
            # An explicitly named product/category limits retrieval.  A request
            # such as «нужно всё для водонагревателя» must not silently become a
            # generic basket containing boilers and unrelated systems.
            return list(dict.fromkeys(named)) or [
                "boilers",
                "pumps",
                "pipes",
                "valves",
                "sewer",
            ], slots
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
            "water_heaters": "water_heaters",
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

    def _maybe_complex_engineering_handoff(
        self,
        session_id: str,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        text = normalize_text(message)
        mentions_warm_floor = "пол" in text and any(marker in text for marker in ["тепл", "тёпл"])
        starts_complex_request = (
            "обвяз" in text
            and any(marker in text for marker in ["котел", "котёл", "котл"])
            and "бойлер" in text
            and mentions_warm_floor
        )
        in_complex_flow = bool(session.slots.get("complex_engineering_request"))
        if not starts_complex_request and not in_complex_flow:
            return None

        if starts_complex_request:
            session.slots["complex_engineering_request"] = (
                "обвязка котла, бойлера и водяного тёплого пола"
            )
            session.slots["boiler_requirement"] = "с бойлером"
            session.slots["warm_floor_requirement"] = "тёплый пол"
            session.category = "boilers"
            session.last_products = []

        self._update_project_state(message, intent, session)
        area = self._first_number(
            text,
            [
                r"(\d{2,4})\s*(?:м2|м²|квадрат|кв)",
                r"(\d{2,4})\s*м(?:етр\w*)?(?:$|[^а-яa-z0-9])",
            ],
        )
        if area is not None and (not mentions_warm_floor or "дом" in text or "площад" in text):
            session.slots["area_m2"] = area
        if "бойлер" in text:
            session.slots["boiler_requirement"] = "с бойлером"

        boiler_volume = self._first_number(
            text,
            [
                r"бойлер.{0,35}?(\d{2,4})\s*(?:л|литр)",
                r"(\d{2,4})\s*(?:л|литр\w*).{0,35}?бойлер",
            ],
        )
        if boiler_volume is not None:
            session.slots["boiler_volume_l"] = boiler_volume
        boiler_model_match = re.search(
            r"бойлер.{0,25}?(?:модел|артикул)\s*[:№-]?\s*([a-zа-я0-9._/-]+)",
            text,
        )
        if boiler_model_match:
            session.slots["boiler_model"] = boiler_model_match.group(1)

        warm_floor_area = self._first_number(
            text,
            [
                r"(?:тепл\w*|тёпл\w*)\s+пол\w*.{0,30}?(\d{1,4})\s*(?:м2|м²|квадрат)",
                r"(\d{1,4})\s*(?:м2|м²|квадрат).{0,30}?(?:тепл\w*|тёпл\w*)\s+пол",
            ],
        )
        if warm_floor_area is not None:
            session.slots["warm_floor_area_m2"] = warm_floor_area
        floor_contours = self._first_number(
            text,
            [
                r"(?:тепл\w*|тёпл\w*)\s+пол\w*.{0,35}?(\d{1,2})\s*контур",
                r"(\d{1,2})\s*контур\w*.{0,35}?(?:тепл\w*|тёпл\w*)\s+пол",
                r"\b(\d{1,2})\s*контур",
            ],
        )
        if floor_contours is not None:
            session.slots["warm_floor_contours"] = int(floor_contours)

        boiler_status_known = any(
            marker in text
            for marker in [
                "котел не выбран",
                "котёл не выбран",
                "котла нет",
                "газовый кот",
                "электрический кот",
                "модель кот",
                "артикул кот",
            ]
        ) or bool(session.slots.get("boiler_type"))
        if boiler_status_known:
            session.slots["boiler_status_known"] = True

        missing_details: list[str] = []
        if not session.slots.get("area_m2"):
            missing_details.append("площадь дома")
        if not session.slots.get("boiler_status_known"):
            missing_details.append("выбранный котёл (тип, модель/артикул) или отметка, что он не выбран")
        if not (
            session.slots.get("boiler_volume_l")
            or session.slots.get("boiler_model")
        ):
            missing_details.append("объём или модель бойлера")
        if not session.slots.get("warm_floor_area_m2"):
            missing_details.append("площадь тёплого пола")
        if not session.slots.get("warm_floor_contours"):
            missing_details.append("число контуров тёплого пола")

        if missing_details:
            missing_text = "; ".join(missing_details)
            next_step = (
                "После ответа продолжим здесь; менеджеру ничего не передаю."
                if session.handoff_opt_out
                else (
                    "После ответа сохраню все три подсистемы в краткой сводке и попрошу "
                    "контакт и подтверждение передачи менеджеру."
                )
            )
            clarification_goal = (
                "Для безопасного продолжения"
                if session.handoff_opt_out
                else "Чтобы передать специалисту не пустую заявку"
            )
            answer = (
                "Обвязка котла, бойлера и тёплого пола — комплексная инженерная схема; "
                f"случайную корзину по ней собирать небезопасно. {clarification_goal}, "
                f"осталось уточнить: {missing_text}. {next_step}"
            )
            session.pending_question = answer
            session.pending_intent_type = "engineering_handoff"
            session.last_intent = "engineering_handoff"
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, answer)
            return self._response(session_id, answer, [], False, intent, session, agents_used)

        if session.handoff_opt_out:
            answer = (
                "Исходные параметры собраны, но по вашему запрету менеджеру ничего не передаю "
                "и заявку не создаю. Могу продолжить консультацию здесь; инженерную схему "
                "без специалиста не утверждаю."
            )
            session.pending_question = None
            session.pending_intent_type = None
            session.pending_category = None
            session.pending_slot_keys = []
            session.last_intent = "engineering_handoff"
            agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
            self._append_history(session, message, answer)
            return self._response(
                session_id,
                answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        summary = self.handoff.build_summary(
            message,
            session,
            missing=["инженерная схема и проверка совместимости узлов"],
        )
        session.pending_handoff = self.handoff.summary_to_dict(summary)
        needs_contact = not bool(summary.contact)
        session.handoff_status = "awaiting_contact" if needs_contact else "awaiting_consent"
        answer = (
            "Спасибо, исходные данные для инженерной заявки собраны. "
            + self.handoff.compose_consent_request(
                summary,
                needs_contact=needs_contact,
            )
        )
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_category = None
        session.pending_slot_keys = []
        session.last_intent = "engineering_handoff"
        agents_used.extend(["GuardrailsAgent", "HandoffAgent"])
        self._append_history(session, message, answer)
        return self._response(session_id, answer, [], True, intent, session, agents_used)

    def _maybe_project_cart_response(
        self,
        session_id: str,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        text = normalize_text(message)
        concrete_product_slots = {
            "diameter_mm",
            "size_inch",
            "head_m",
            "mounting_length_mm",
            "connection_size",
            "length_mm",
            "element_type",
        }
        explicit_whole_project = any(
            marker in text
            for marker in [
                "собери всё",
                "собери все",
                "весь комплект",
                "полный комплект",
                "под ключ",
                "корзин",
                "система отопления",
                "всё для",
                "все для",
            ]
        )
        if (
            not session.slots.get("project_scope")
            and intent.category in {"pipes", "pumps", "valves", "sewer", "radiator_fittings", "radiators", "fittings"}
            and concrete_product_slots.intersection(intent.slots)
            and not explicit_whole_project
        ):
            # "Подбери трубу 25 мм для отопления" is a concrete product
            # request.  The purpose word "отопление" must not expand it into a
            # boiler+pump+pipe project funnel.
            return None
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
            in_stock_only = bool(
                intent.flags.get("in_stock") or intent.slots.get("in_stock")
            )
            cards = self._project_cart_cards(session)
            if not cards and session.last_products:
                cards = session.last_products
                self._remember_project_cart(session, cards)
            if in_stock_only:
                session.slots["in_stock"] = True
                cards = [card for card in cards if self._card_is_in_stock(card)]
                cart = session.slots.get("project_cart")
                if isinstance(cart, dict):
                    allowed = {card.sku for card in cards}
                    session.slots["project_cart"] = {
                        category: [
                            sku for sku in skus if str(sku) in allowed
                        ]
                        for category, skus in cart.items()
                        if isinstance(skus, list)
                    }
            if cards:
                answer = self._compose_project_cart_summary(session, cards)
                if in_stock_only:
                    answer += (
                        "\nПозиции с нулевым или неподтверждённым остатком "
                        "в эту корзину не включены."
                    )
                agents_used.append("ResponseComposerAgent")
                session.last_products = cards
                session.last_intent = "project_cart"
                session.pending_question = None
                session.pending_intent_type = None
                self._append_history(session, message, answer)
                return self._response(session_id, answer, cards, False, intent, session, agents_used)
            if in_stock_only:
                answer = (
                    "В сохранённой подборке не осталось ни одной позиции с "
                    "подтверждённым положительным остатком. Карточки с нулевым "
                    "остатком показывать не буду."
                )
                session.last_products = []
                agents_used.extend(["GuardrailsAgent", "ResponseComposerAgent"])
                self._append_history(session, message, answer)
                return self._response(
                    session_id,
                    answer,
                    [],
                    False,
                    intent,
                    session,
                    agents_used,
                )

        # «Подберите насос к нему» про уже собранную корзину — точечный вопрос об
        # одном узле. Проверяем до разрешения scope: сама фраза не является
        # проектным follow-up (и не должна им быть), поэтому scope из сообщения
        # не выводится, и раньше эта ветка была недостижима.
        component_category = self._wants_specific_cart_component(text, intent)
        if component_category and session.slots.get("project_cart"):
            existing_skus = (session.slots.get("project_cart") or {}).get(component_category) or []
            all_cards = self._project_cart_cards(session)
            focus_cards = [card for card in all_cards if card.sku in existing_skus]
            if focus_cards:
                cart_scope = str(
                    session.slots.get("project_scope")
                    or session.slots.get("scope_funnel")
                    or "general"
                )
                answer = self._compose_project_component_focus(
                    component_category, cart_scope, focus_cards, session
                )
                agents_used.append("ResponseComposerAgent")
                session.last_products = focus_cards
                session.last_intent = "project_cart"
                self._append_history(session, message, answer)
                return self._response(session_id, answer, focus_cards, False, intent, session, agents_used)

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
            concrete_warm_floor_pipe = bool(
                scope == "warm_floor"
                and (
                    intent.slots.get("diameter_mm")
                    or any(
                        marker in text
                        for marker in [
                            " pex",
                            "pe-x",
                            "pe rt",
                            "pe-rt",
                            "металлопласт",
                            " ppr",
                            "полипропилен",
                        ]
                    )
                )
            )
            if intro and (
                not any(word in text for word in SPECIFIC_PRODUCT_WORDS)
                or (scope == "warm_floor" and not concrete_warm_floor_pipe)
            ):
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
        # «для дома»/«в дом» здесь намеренно НЕ маркеры проекта: «котёл для дома
        # 100 м²» — обычный запрос одного товара, а не заявка на инженерию всего
        # дома. Общий scope включают только явные признаки комплексной задачи.
        # «под ключ» здесь тоже не маркер: это степень работ, применимая к любому
        # scope, и она должна продолжать уже выбранный («отопление» → «под ключ»).
        if any(marker in text for marker in ["сантехник", "инженерн", "весь дом"]):
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
        # «Подберите котёл на 100 м²» — обычная вежливая просьба про ОДИН товар,
        # а не заявка на мультикатегорийный комплект. Считаем эти глаголы
        # проектными только когда конкретный товар не назван («подберите всё
        # для отопления»). Иначе флагманский запрос про котёл уходил в сборку
        # корзины с канализацией и трубой для тёплого пола.
        polite_request_verbs = ["подбери", "подберите", "подборк"]
        if any(verb in text for verb in polite_request_verbs):
            return not any(word in text for word in SPECIFIC_PRODUCT_WORDS)
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

    def _wants_specific_cart_component(self, text: str, intent: IntentResult) -> str | None:
        """"Подберите насос к нему" after the cart is already collected is a
        follow-up about ONE cart category — "к нему/для него" ties it to the
        item already chosen, not a request for a fresh full selection. Without
        this check such a follow-up just reran the whole bundling and echoed
        the exact same answer as the first, unrelated question.
        """
        if intent.category not in PROJECT_CART_CATEGORY_ORDER:
            return None
        tie_markers = [
            "к нему",
            "к ней",
            "для него",
            "для неё",
            "для нее",
            "под него",
            "под неё",
            "под нее",
            "на него",
            "на неё",
            "на нее",
        ]
        if not any(marker in text for marker in tie_markers):
            return None
        return intent.category

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
        # Настоящий ответ на проектный вопрос выглядит как «50 м2» или «водяной
        # от котла»: он может упоминать котёл как источник тепла, но ничего не
        # просит. Если пользователь именно ЗАПРАШИВАЕТ товар («нужен котёл на
        # 40 м2»), это новый однокатегорийный запрос, а не продолжение проекта —
        # иначе однажды включённый project_scope залипал и любое следующее
        # сообщение с площадью снова уходило в сборку комплекта.
        if self._is_new_product_request(text):
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

    @staticmethod
    def _is_new_product_request(text: str) -> bool:
        """True when the message asks for a concrete product, not just mentions one.

        «нужен котёл на 40 м2» requests a boiler; «водяной от котла» only names
        the boiler as the heat source while answering a project question. Only
        the gendered forms «нужен/нужна/нужны» are used — neuter «нужно» belongs
        to project phrasings like «что нужно?».
        """
        request_markers = [
            "нужен",
            "нужна",
            "нужны",
            "подбери",
            "подберите",
            "хочу",
            "дайте",
            "покажи",
            "ищу",
            "интересует",
        ]
        if not any(marker in text for marker in request_markers):
            return False
        return any(word in text for word in SPECIFIC_PRODUCT_WORDS)

    def _is_project_component_turn(self, intent: IntentResult, session: SessionState) -> bool:
        if not (session.slots.get("project_cart") or session.slots.get("project_scope")):
            return False
        return intent.category in {
            "boilers",
            "water_heaters",
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
            # retrieve_for_consult сортирует всю категорию до среза. Берём весь
            # отсортированный набор и затем применяем более строгую проверку роли:
            # дешёвый аксессуар не должен занять единственное место насоса/трубы/крана.
            products = self.search_agent.retrieve_for_consult(
                [category],
                category_slots,
                per_category=max(1, len(self.search_agent.products)),
            )
            products = [
                product
                for product in products
                if self._project_product_is_suitable(product, category, scope, session)
            ]
            in_stock = [product for product in products if product.is_in_stock]
            products = (
                in_stock
                if category_slots.get("in_stock")
                else (in_stock or products)
            )[:per_category]
            cards = self.card_agent.build_cards(
                products,
                SearchQuery(original_text=message, category=category, slots=category_slots),
                limit=per_category,
            )
            if cards:
                result[category] = cards
        return self._drop_components_already_included(result, session)

    def _card_confirms_builtin_part(self, card: ProductCard, part: str) -> bool:
        product = self._find_product_by_sku(card.sku)
        if not product:
            return False
        return any(
            part in component
            for component in self.guardrails.list_builtin_components(product)
        )

    def _drop_components_already_included(
        self,
        cards_by_category: dict[str, list[ProductCard]],
        session: SessionState,
    ) -> dict[str, list[ProductCard]]:
        """Не продавать узел, который уже встроен в другую позицию подборки.

        Комплект собирается по категориям независимо, поэтому котёл со штатным
        циркуляционным насосом попадал в подборку рядом с отдельным насосом —
        клиент купил бы то, что у него уже есть.

        Хост не зашит в котёл: проверяется любая другая категория подборки.
        Обязательное условие ``host != candidate`` — иначе сработала бы
        самореференция (у 222 насосов в фиде «встроенный насос» это они сами).
        Позиция снимается только если узел подтверждён карточкой/паспортом
        (GuardrailsAgent намеренно консервативен) у ВСЕХ товаров категории-хоста:
        если второй предложенный вариант штатного узла не имеет, узел ещё нужен.

        В карте намеренно только насос: это единственная связь, подтверждённая
        данными фида (103 котла). Отображать, например, «3-ходовой клапан»
        котла на категорию valves нельзя — в подборке это запорные краны, и
        встроенный смесительный клапан их не заменяет.
        """
        session.slots.pop("cart_builtin_skipped", None)
        candidate_parts = {"pumps": "насос"}
        skipped: list[str] = []
        result = dict(cards_by_category)
        for candidate, part in candidate_parts.items():
            if not result.get(candidate):
                continue
            for host, host_cards in cards_by_category.items():
                if host == candidate or not host_cards:
                    continue
                if all(self._card_confirms_builtin_part(card, part) for card in host_cards):
                    result.pop(candidate, None)
                    skipped.append(part)
                    break
        if skipped:
            session.slots["cart_builtin_skipped"] = skipped
        return result

    def _project_role_is_confirmed(self, product: Product, category: str) -> bool:
        """Require the product itself, not an accessory mentioning it, to fill a role."""
        name = normalize_text(product.name)
        type_values = " ".join(
            normalize_text(str(value))
            for key, value in product.attributes_normalized.items()
            if "тип товар" in normalize_text(str(key))
        )
        identity = f"{name} {type_values}".strip()

        common_accessory_markers = [
            "декоратив",
            "чашка",
            "колпачок",
            "кожух",
            "трос",
            "кабель",
            "кронштейн",
            "креплен",
            "зажим",
            "коуш",
            "ручка для",
            "ремкомплект",
            "запчаст",
        ]
        if any(marker in name for marker in common_accessory_markers):
            return False

        if category == "boilers":
            return bool(re.search(r"\bкот[её]л\w*\b", name))
        if category == "pumps":
            if re.search(r"\b(?:насосная\s+станция|станция\s+насосная)\b", identity):
                return True
            return bool(
                re.search(r"^(?:насос\b|[^,;]{0,35}\bнасос\s+(?:циркуляц|скваж|дренаж|поверхност))", identity)
                or re.search(r"\bнасос\b", type_values)
                or re.search(r"^(?:циркуляционный|скважинный|дренажный|поверхностный)\s+насос\b", type_values)
            )
        if category == "pipes":
            return bool(
                re.search(r"^труб[аы]\b", identity)
                or re.search(r"\bтип\s*труб", type_values)
                or re.search(r"^труб[аы]\b", type_values)
            )
        if category == "valves":
            return any(
                re.search(rf"\b{marker}\w*\b", identity)
                for marker in ["кран", "вентил", "клапан", "задвиж", "затвор"]
            )
        if category == "sewer":
            return any(
                re.search(rf"\b{marker}\w*\b", identity)
                for marker in ["труб", "отвод", "тройник", "муфт", "переход", "ревизи"]
            )
        if category == "radiator_fittings":
            return any(
                marker in identity
                for marker in [
                    "клапан",
                    "вентиль",
                    "термоголов",
                    "узел подключ",
                    "гарнитур",
                ]
            )
        if category == "radiators":
            return "радиатор" in identity or "конвектор" in identity
        if category == "fittings":
            return any(
                marker in identity
                for marker in ["угольник", "муфта", "тройник", "переходник", "фитинг"]
            )
        return False

    def _project_product_is_suitable(
        self,
        product: Product,
        category: str,
        scope: str,
        session: SessionState,
    ) -> bool:
        """Confirm both the catalogue category and the component's project role."""
        if not self._project_role_is_confirmed(product, category):
            return False
        identity = normalize_text(
            " ".join(
                [
                    product.name,
                    product.category_path,
                    product.description or "",
                    *[str(value) for value in product.attributes_normalized.values()],
                ]
            )
        )
        if category == "valves":
            # The project role is isolation/service.  Check, safety and control
            # valves are real valves but cannot silently fill that role.
            if any(
                marker in identity
                for marker in [
                    "обратн",
                    "для водосчет",
                    "для водосчёт",
                    "предохран",
                    "термостат",
                    "балансир",
                    "редуктор давления",
                ]
            ):
                return False
            return any(marker in identity for marker in ["шаров", "запорн", "вентиль"])
        if category == "pipes":
            mentions_warm_floor = "пол" in identity and any(
                marker in identity for marker in ["тепл", "тёпл"]
            )
            warm_floor_identity = mentions_warm_floor or any(
                marker in identity
                for marker in [
                    "для теплого пола",
                    "для тёплого пола",
                    "pex",
                    "pe-x",
                    "pe xa",
                    "pe rt",
                    "pe-rt",
                    "pert",
                    "металлопласт",
                ]
            )
            is_ppr = "ppr" in identity or "полипропилен" in identity
            if scope == "warm_floor":
                return warm_floor_identity and not is_ppr
            if scope == "heating" and mentions_warm_floor:
                return False
        if category == "pumps" and scope == "water":
            source = normalize_text(str(session.slots.get("water_source") or ""))
            if "скваж" in source:
                return "скваж" in identity or "погружн" in identity
            if "колод" in source:
                return any(marker in identity for marker in ["колод", "погружн", "насосная станция"])
        return True

    def _project_retrieval_slots(self, scope: str, session: SessionState) -> dict:
        slots = dict(session.slots)
        if scope in {"warm_floor", "heating"}:
            slots.setdefault("pump_type", "циркуляционный")
            slots.setdefault("pump_use", "отопление")
        if scope == "heating":
            slots.setdefault("pipe_purpose", "отопление")
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
            if not product or not self._project_role_is_confirmed(product, category):
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
                if not self._project_role_is_confirmed(product, category):
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

        # Позиция, снятая из-за штатного узла котла, не «не найдена» — у неё
        # своя причина ниже, иначе объяснение получится ложным.
        skipped_builtin = session.slots.get("cart_builtin_skipped") or []
        missing_categories = [
            PROJECT_CATEGORY_LABELS.get(category, category).lower()
            for category in PROJECT_SCOPE_CATEGORIES.get(scope, [])
            if not cards_by_category.get(category)
            and not (category == "pumps" and skipped_builtin)
        ]
        if missing_categories:
            lines.append(
                "Не добавил артикулы для категорий: "
                + ", ".join(missing_categories)
                + ". В текущем ассортименте не нашёл позицию, чья роль однозначно "
                "подтверждена названием или типом товара; аксессуар вместо основного узла "
                "подставлять не буду."
            )
        if skipped_builtin:
            lines.append(
                "Отдельный циркуляционный насос не добавляю: по описанию карточки он уже "
                "встроен в выбранный котёл. Если нужен насос на отдельный контур "
                "(например, тёплый пол) или для замены штатного — скажите, подберу."
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

    def _compose_project_component_focus(
        self,
        category: str,
        scope: str,
        cards: list[ProductCard],
        session: SessionState,
    ) -> str:
        label = PROJECT_CATEGORY_LABELS.get(category, category)
        reason = self._project_category_reason(category, scope, session)
        lines = [f"{label} для вашей подборки уже выбран:"]
        for card in cards:
            lines.append(
                f"{html.unescape(card.name)} — арт. {card.sku}, "
                f"{card.price:g} {card.currency}, {self._card_stock_text(card)}. Почему: {reason}."
            )
        lines.append(
            f"Это тот же товар, что и в подборке выше. Нужен другой вариант — напишите "
            f"«замените {label.lower()}»."
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

    @staticmethod
    def _card_is_in_stock(card: ProductCard) -> bool:
        if card.stock_qty is not None:
            return card.stock_qty > 0
        status = normalize_text(card.stock_status)
        return "налич" in status and "нет" not in status

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

    def _maybe_toilet_installation_project(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Fail closed for an untyped sanitary-fixture basket.

        The feed contains both toilets and small accessories, but they do not yet
        have a typed project-cart category.  A broad installation request must
        therefore collect the interface facts first instead of inheriting an old
        category or asking the consultant to retrieve a generic heating basket.
        """
        text = normalize_text(message)
        names_toilet_fixture = bool(re.search(r"\bунитаз\w*\b", text))
        installs_toilet_fixture = bool(
            re.search(
                r"\b(?:установк\w*|монтаж\w*|подключени\w*)\s+"
                r"(?:нов\w+\s+)?туалет\w*\b",
                text,
            )
        )
        project_request = any(
            marker in text
            for marker in [
                "установ",
                "монтаж",
                "подключ",
                "все для",
                "всё для",
                "все что нужно",
                "всё что нужно",
                "комплект",
                "под ключ",
            ]
        )
        if not (
            installs_toilet_fixture
            or names_toilet_fixture and project_request
        ):
            return None

        session.slots = {
            "project_scope": "toilet_installation",
            "project_fixture": "унитаз",
        }
        session.category = None
        session.last_products = []
        session.last_intent = "broad_category"
        session.topic_changed = True
        session.pending_category = None
        session.pending_slot_keys = []
        session.pending_complectation_parts = []
        session.question_repeats = 0
        session.pending_handoff = None
        if session.handoff_status in {"awaiting_contact", "awaiting_consent", "failed"}:
            session.handoff_status = "none"

        question = (
            "Чтобы собрать корректный комплект для установки унитаза, сначала уточните: "
            "нужен сам унитаз или только подключение уже выбранного; он напольный или "
            "подвесной; какой у него выпуск и как расположена канализационная труба; "
            "как подведена вода? Пока эти данные неизвестны, не буду показывать случайные "
            "товары. После уточнения отдельно проверю по карточкам унитаз, крепёж, "
            "манжету/переход к канализации, подводку и запорный кран."
        )
        session.pending_question = question
        session.pending_intent_type = "broad_category"
        return question

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
            if power is None or power >= required_kw * 0.9:
                kept.append(product)
        return kept

    def _append_companion_hint(self, answer: str, session: SessionState, category: str) -> str:
        if category == "boilers" and (
            session.slots.get("required_builtin_parts")
            or session.slots.get("excluded_builtin_parts")
        ):
            return answer
        # Isolation valves are a useful companion hint for a circulation pump
        # installed in a closed heating circuit, but not for drainage/borehole
        # pumps.  The old category-wide hint made drainage selections sound as
        # if they were heating-system components.
        if category == "pumps" and normalize_text(
            str(session.slots.get("pump_type") or "")
        ) != "циркуляционный":
            return answer
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
            "Если хотите подготовить обращение, напишите «передай менеджеру»: я сначала покажу "
            "краткое содержание, запрошу контакт и подтверждение. А с подбором товара, ценой и "
            "наличием по каталогу помогу прямо сейчас."
        )

    def _maybe_financial_stocks_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> str | None:
        """Distinguish explicit investments from ordinary store promotions."""
        text = normalize_text(message)
        finance_markers = [
            "купить акции",
            "продать акции",
            "акции компан",
            "ценные бумаги",
            "инвестиц",
            "инвестировать",
            "бирж",
            "брокер",
            "дивиденд",
            "портфел",
            "доходност",
            "гарантированно заработать",
        ]
        product_promo_context = any(
            marker in text
            for marker in [
                "скидк",
                "распродаж",
                "промокод",
                "акционный товар",
                "акции на товар",
                "акции на кот",
                "акции на насос",
                "акции на труб",
                "магазин",
            ]
        )
        stock_market_context = "акци" in text and any(
            marker in text
            for marker in [
                "газпром",
                "сбер",
                "лукойл",
                "яндекс",
                "тесла",
                "tesla",
                "apple",
                "котиров",
                "тикер",
                "фондов",
                "рост акци",
                "паден",
                "влож",
            ]
        )
        explicit_finance = any(marker in text for marker in finance_markers) or (
            stock_market_context and not product_promo_context
        )
        if explicit_finance:
            session.slots["financial_context"] = True
            return (
                "Это финансовый и инвестиционный вопрос, он вне моей компетенции: "
                "я не консультирую по акциям и ценным бумагам. Помогу только с товарами "
                "и условиями магазина Vesta Trading."
            )
        # В контексте магазина голое «какие есть акции?» однозначно означает
        # скидки/промо, как и ожидает пользователь.
        if "акци" in text:
            session.slots.pop("financial_context", None)
            return None
        if session.slots.get("financial_context") and self._wants_manager_handoff(message):
            return (
                "Менеджеру магазина финансовый запрос не передаю: подбор ценных бумаг "
                "не относится к Vesta Trading. Могу помочь с товарами магазина."
            )
        if intent.category != "other":
            session.slots.pop("financial_context", None)
        return None

    def _maybe_manager_contact_question(self, message: str) -> str | None:
        text = normalize_text(message)
        has_contact_intent = self._has_marker(text, CONTACT_INTENT_MARKERS, fuzzy_threshold=84)
        has_generic_contact = self._has_marker(text, GENERIC_CONTACT_MARKERS, fuzzy_threshold=84)
        if not has_generic_contact and (not has_contact_intent or not self._mentions_human_role(text)):
            return None
        return (
            "Чтобы подготовить обращение менеджеру, кратко напишите вопрос и оставьте телефон "
            "или email. Перед отправкой я покажу, какие данные будут переданы, и попрошу "
            "подтверждение. Без него заявку не создаю."
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
            "Вы правы: без контакта и явного подтверждения я не должен говорить, что заявка "
            "передана. Для обратной связи нужен контакт; успешную передачу подтверждаю только "
            "номером заявки. Сейчас можно "
            "оставить телефон/email и подтвердить краткое обращение либо продолжить подбор здесь."
        )

    def _mentions_human_role(self, text: str) -> bool:
        # «Нужна консультация» names a service, not necessarily a request to
        # transfer the chat to a human. Avoid the fuzzy consultant/consultation
        # collision while still accepting an explicitly named role.
        if "консультац" in text and not any(
            marker in text for marker in HUMAN_ROLE_MARKERS
        ):
            return False
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
            "не передава",
            "не передайте",
            "не сохраня",
            "не создава",
            "не отправля",
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

    @staticmethod
    def _is_handoff_opt_out(message: str) -> bool:
        text = normalize_text(message)
        return any(
            marker in text
            for marker in [
                "не передава",
                "не передайте",
                "не отправля",
                "не сохраня",
                "не создава",
                "без менеджера",
                "менеджер не нужен",
                "не нужен менеджер",
            ]
        )

    @staticmethod
    def _is_handoff_refusal(message: str) -> bool:
        text = normalize_text(message).strip(" .,!?:;")
        return (
            text in {"нет", "не согласен", "не согласна", "отказываюсь", "передумал", "передумала"}
            or any(
                marker in text
                for marker in [
                    "не согласен на передач",
                    "не согласна на передач",
                    "не подтверждаю",
                    "отказываюсь от передач",
                    "не даю соглас",
                ]
            )
        )

    @staticmethod
    def _is_handoff_confirmation(message: str) -> bool:
        text = normalize_text(message).strip(" .,!?:;")
        if (
            text.startswith("нет")
            or "не соглас" in text
            or "не подтвержда" in text
            or "отказыва" in text
        ):
            return False
        return text in {
            "подтверждаю",
            "подтверждаю передачу",
            "согласен",
            "согласна",
            "да подтверждаю",
            "да передавайте",
            "да передай",
        } or any(
            marker in text
            for marker in [
                "согласен на передач",
                "согласна на передач",
                "подтверждаю передач",
            ]
        )

    def _handle_handoff_opt_out(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse:
        if "HandoffAgent" not in agents_used:
            agents_used.append("HandoffAgent")
        if session.handoff_status == "locally_recorded" and session.handoff_ticket_id:
            answer = (
                f"Локальный черновик уже сохранён, номер: "
                f"{session.handoff_ticket_id}. Он не подтверждает передачу менеджеру. "
                "Я не могу автоматически удалить эту запись; повторный черновик "
                "не формирую."
            )
            session.pending_handoff = None
            self._append_history(session, message, answer)
            return self._response(
                session.session_id,
                answer,
                [],
                True,
                intent,
                session,
                agents_used,
            )

        session.handoff_status = "opted_out"
        session.handoff_opt_out = True
        session.pending_handoff = None
        session.handoff_ticket_id = None
        session.handoff_fingerprint = None
        answer = (
            "Понял: менеджеру ничего не передаю и заявку не создаю. "
            "Продолжим подбор здесь."
        )
        self._append_history(session, message, answer)
        return self._response(
            session.session_id,
            answer,
            [],
            False,
            intent,
            session,
            agents_used,
        )

    def _maybe_continue_handoff(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        if not session.pending_handoff:
            return None
        status = session.handoff_status
        contact = self.handoff.extract_contact(message)
        confirmation = self._is_handoff_confirmation(message)
        handoff_command = self._wants_manager_handoff(message)
        if not (contact or confirmation or handoff_command):
            return None

        try:
            summary = HandoffSummary(**session.pending_handoff)
        except (TypeError, ValueError):
            session.pending_handoff = None
            session.handoff_status = "failed"
            return None

        if status == "locally_recorded" and session.handoff_ticket_id:
            answer = (
                f"Этот локальный черновик уже сохранён, номер: "
                f"{session.handoff_ticket_id}. Повторную запись не создаю."
            )
            if "HandoffAgent" not in agents_used:
                agents_used.append("HandoffAgent")
            self._append_history(session, message, answer)
            return self._response(
                session.session_id,
                answer,
                [],
                True,
                intent,
                session,
                agents_used,
            )

        if contact:
            summary.contact = contact
            session.pending_handoff = self.handoff.summary_to_dict(summary)
            session.handoff_status = "awaiting_consent"
            if not confirmation:
                answer = self.handoff.compose_consent_request(
                    summary,
                    needs_contact=False,
                )
                if "HandoffAgent" not in agents_used:
                    agents_used.append("HandoffAgent")
                self._append_history(session, message, answer)
                return self._response(
                    session.session_id,
                    answer,
                    [],
                    True,
                    intent,
                    session,
                    agents_used,
                )

        if status == "awaiting_contact" and not summary.contact:
            answer = (
                "Заявку пока не отправляю: для неё нужен телефон или email. "
                "После получения контакта покажу итог и попрошу подтверждение."
            )
            if "HandoffAgent" not in agents_used:
                agents_used.append("HandoffAgent")
            self._append_history(session, message, answer)
            return self._response(
                session.session_id,
                answer,
                [],
                True,
                intent,
                session,
                agents_used,
            )

        if not confirmation:
            return None

        result = self.handoff.record(
            summary,
            session.session_id,
            self.settings.handoff_log_path,
        )
        session.handoff_status = "locally_recorded" if result.success else "failed"
        session.handoff_ticket_id = result.ticket_id
        session.handoff_fingerprint = result.idempotency_key
        answer = self.handoff.compose_user_confirmation(summary, result)
        if "HandoffAgent" not in agents_used:
            agents_used.append("HandoffAgent")
        self._append_history(session, message, answer)
        return self._response(
            session.session_id,
            answer,
            [],
            True,
            intent,
            session,
            agents_used,
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
        references = self._references_shown_products(message)
        if intent.intent_type in {"exact_sku", "link_request", "complectation"}:
            return False
        # The probabilistic router can label an explicit card follow-up as
        # small talk.  The user's reference to the shown card is stronger
        # evidence than that label and must keep the product context attached.
        if intent.intent_type == "small_talk" and not references:
            return False
        # Новый товар другой категории — это новый подбор, не вопрос про показанное
        # (если только нет явной ссылки на ранее показанное).
        if intent.category not in {"other", session.category} and not references:
            return False
        if self._wants_choose_one(message):
            return False
        if "только в налич" in text:
            # This is a persistent catalogue filter, not a question about the
            # stock of the card already on screen.
            return False
        # A stock marker may accompany a new product refinement. Do not answer
        # «только в наличии» about the old card when this turn also changes a
        # typed water-heater constraint.
        if {
            "volume_l",
            "heater_type",
            "energy_source",
            "mounting",
            "orientation",
        }.intersection(intent.slots):
            return False
        # Уточнения и команды, которые умеет детерминированный конвейер.
        refine_signals = [
            "дешевле",
            "подешевле",
            "не дороже",
            "бюджет",
            "без wi-fi",
            "без wifi",
            "без вай-фай",
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
        if re.search(r"\d[\d ]*\s*(?:руб|тыс|₽)", text):
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
        return references or any(
            marker in text for marker in context_markers
        )

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
            r"(?:в\s+)?комплект поставки"
            r"(?:\s+(?:входят|входит))?\s*:?\s*",
            snippet,
            flags=re.IGNORECASE,
        )
        if not marker:
            return []
        prose_package_statement = bool(
            re.search(r"\b(?:входят|входит)\b", marker.group(0), re.IGNORECASE)
        )
        body = snippet[marker.end() :]
        body = re.sub(
            r"^\s*№?\s*Наименование\s+Ед\.\s*изм\.\s*Количество\s*",
            "",
            body,
            count=1,
            flags=re.IGNORECASE,
        )
        numbered = list(re.finditer(r"(?<![\d.])(\d{1,2})\.\s+", body))
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

        # Flattened PDF tables commonly use ``1 Item шт. 2`` rather than
        # numbered prose ``1. Item``.  Parse only inside the package section and
        # require an explicit unit + quantity, so technical-table row numbers
        # elsewhere in the passport cannot become fictitious box contents.
        table_body = re.split(
            r"\s+\d{1,2}\.\s+[A-ZА-ЯЁ]",
            body,
            maxsplit=1,
        )[0]
        table_rows = list(
            re.finditer(
                r"(?<!\d)(\d{1,2})\s+(.+?)\s+"
                r"(комплект|шт\.?)\s+(\d+)"
                r"(?=\s+\d{1,2}\s+|$)",
                table_body,
                flags=re.IGNORECASE,
            )
        )
        table_items: list[str] = []
        expected = 1
        for row in table_rows:
            number = int(row.group(1))
            if number != expected:
                if table_items:
                    break
                continue
            name = " ".join(row.group(2).split()).strip(" .;:")
            unit = row.group(3).rstrip(".")
            quantity = int(row.group(4))
            if name:
                table_items.append(f"{name} — {quantity} {unit}")
                expected += 1
        if table_items:
            return table_items

        # Некоторые паспорта перечисляют короткий состав поставки одной фразой.
        if not prose_package_statement:
            return []
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
                f"Для {card.sku} не вижу привязанного паспорта с подтверждённым составом "
                "поставки. Не буду смешивать встроенные узлы изделия с содержимым коробки; "
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
            "Это именно комплект поставки; встроенные узлы изделия перечисляются отдельно. "
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
        explicit_reference = any(
            ref in text
            for ref in [
                "этот",
                "этого",
                "этой",
                "эту",
                "эти ",
                "это же",
                "тот ",
                "тот же",
                "того ",
                "показанн",
                "ты предложил",
                "ты показал",
                "что предложил",
                "что ты показал",
                "которые показал",
                "которые ты",
                "предложенн",
            ]
        )
        if explicit_reference:
            return True
        # Short pronoun follow-ups are common after an exact SKU lookup.  Limit
        # them to unambiguous card-fact phrases so unrelated uses of «он/она» do
        # not accidentally pin an old catalogue context.
        return any(
            phrase in text
            for phrase in [
                "он стоит",
                "она стоит",
                "оно стоит",
                "у него цена",
                "у нее цена",
                "у неё цена",
                "его цена",
                "ее цена",
                "её цена",
                "его артикул",
                "ее артикул",
                "её артикул",
                "он в наличии",
                "она в наличии",
                "есть ли он",
                "есть ли она",
                "сколько он",
                "сколько она",
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

    def _remember_result_category(
        self,
        session: SessionState,
        cards: list[ProductCard],
    ) -> None:
        """Keep exact-SKU follow-ups in the product's canonical category.

        Exact article queries are intentionally routed with ``category=other``.  If
        we leave the session category empty, a later question such as "характеристики
        показанного товара" can be mistaken for a brand-new pump/boiler selection and
        the slot funnel asks irrelevant questions.  The product card is authoritative,
        so remember its feed category after every successful search.
        """
        if not cards:
            return
        product = self._find_product_by_sku(cards[0].sku)
        if not product:
            return
        category = self.search_agent.canonical_category(product)
        if category and category != "other":
            session.category = category

    def _contextual_fallback(
        self,
        message: str,
        cards: list[ProductCard],
    ) -> str:
        """Grounded deterministic answer when a context LLM omits requested facts."""
        if not cards:
            return (
                "Не вижу последнего показанного товара. Напишите артикул или уточните, "
                "что нужно подобрать."
            )
        text = normalize_text(message)
        lines: list[str] = []
        for card in cards[:3]:
            lines.append(f"{card.name}. Артикул: {card.sku}.")
            details: list[str] = []
            if any(marker in text for marker in ["характерист", "опис", "отлич", "главн"]):
                details.extend(
                    f"{key}: {value}"
                    for key, value in list(card.characteristics.items())[:6]
                )
            if any(marker in text for marker in ["цен", "стоит", "сколько"]):
                details.append(f"цена: {card.price:g} {card.currency}")
            if "налич" in text:
                stock = card.stock_status
                if card.stock_qty is not None:
                    stock = f"{stock}, {card.stock_qty} шт."
                details.append(f"наличие: {stock}")
            if details:
                lines.append("Основные данные: " + "; ".join(details) + ".")
            if "ссыл" in text:
                lines.append(f"Ссылка: {card.url}")
        return "\n".join(lines)

    def _is_basic_card_fact_question(self, message: str) -> bool:
        """Facts copied from one card are safer and clearer without free-form LLM prose."""
        text = normalize_text(message)
        if any(
            marker in text
            for marker in ["посовет", "что лучше", "что взять", "что выбрать", "почему"]
        ):
            return False
        return any(
            marker in text
            for marker in [
                "характерист",
                "кратко опиш",
                "описание",
                "название",
                "главн",
                "отлич",
                "цена",
                "стоит",
                "сколько",
                "налич",
                "присоедин",
                "резьб",
            ]
        )

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
        fallback = self._contextual_fallback(message, session.last_products)
        agents_used.append("ResponseComposerAgent")
        if self._is_basic_card_fact_question(message):
            # A free-form rewrite of basic card facts repeatedly introduced semantic
            # inventions (components, materials and temperature limits).  Keep LLM
            # for advice/comparison, but copy identity and catalogue facts exactly.
            agents_used.append("GuardrailsAgent")
            self._append_history(session, message, fallback)
            return self._response(
                session.session_id,
                fallback,
                session.last_products,
                False,
                intent,
                session,
                agents_used,
            )
        answer = self.composer.answer_in_context(message, context_block, fallback)
        agents_used.append("GuardrailsAgent")
        required_issues: list[str] = []
        message_text = normalize_text(message)
        if "артикул" in message_text or "sku" in message_text:
            normalized_answer = normalize_sku_token(answer)
            if not any(
                normalize_sku_token(card.sku) in normalized_answer
                for card in session.last_products
            ):
                required_issues.append("LLM context answer omitted requested SKU")
        guard = self.guardrails.validate_context_answer(answer, context_block)
        issues = [*required_issues, *guard.issues]
        if issues:
            logger.warning("Context answer rejected: %s", "; ".join(issues))
            self.composer.last_llm_output_accepted = False
            self.composer.last_llm_rejection_reason = "; ".join(issues)
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
        if self._wants_choose_one(message):
            return None
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
        wants_cheaper = bool(
            intent.flags.get("cheap")
            or intent.slots.get("cheap")
            or "дешев" in text
        )
        if "аналог" not in text and not asks_more and not wants_cheaper:
            return None
        if not session.last_products:
            return None
        blocking_slots = {"sku", "brand", "reference_brand", "old_model"}
        if blocking_slots.intersection(intent.slots):
            return None
        if intent.category not in {"other", session.category}:
            return None
        shown_skus = {
            normalize_sku_token(sku)
            for sku in (
                session.shown_product_skus
                or [card.sku for card in session.last_products]
            )
        }
        transient_slots = {
            "cheap",
            "choose_one",
            "result_limit",
            "sort_mode",
            "relative_cheaper",
        }
        current_slots = self._merge_persistent_slots(session, intent.slots)
        reference_slots = self._shown_water_heater_reference_slots(session)
        for key, value in reference_slots.items():
            # Current-turn constraints are authoritative. Missing compatibility
            # dimensions, however, must come from the exact shown feed row—not
            # from semantic similarity to the words «покажи аналоги».
            current_slots.setdefault(key, value)
            session.slots.setdefault(key, value)
        query = SearchQuery(
            original_text=message,
            category=session.category or "other",
            slots={
                key: value
                for key, value in current_slots.items()
                if key not in transient_slots
            },
            in_stock_only=bool(
                current_slots.get("in_stock")
                or intent.flags.get("in_stock")
            ),
        )
        if wants_cheaper:
            query.cheap = True
        agents_used.append("FeedSearchAgent")
        if query.slots.get("allow_alternatives") is False:
            # The user explicitly asked for more options, but previously stated
            # hard constraints still apply. Search peers that satisfy them;
            # do not use the relaxation path that can drop contour/type filters.
            alternative_pool = self.search_agent.search(query)
        else:
            alternative_pool = self.search_agent.search_alternatives(query)
        alternatives = [
            product
            for product in alternative_pool
            if normalize_sku_token(product.sku) not in shown_skus
        ]
        alternatives = self._drop_underpowered_boilers(alternatives, query)
        if wants_cheaper:
            min_shown_price = min((card.price for card in session.last_products), default=None)
            if min_shown_price is not None:
                alternatives = [
                    product
                    for product in alternatives
                    if product.price is not None and product.price < min_shown_price
                ]
        if not alternatives:
            if (
                query.category == "water_heaters"
                and query.slots.get("allow_alternatives") is True
            ):
                agents_used.append("ResponseComposerAgent")
                answer = self.composer.compose_no_match(query)
                answer = self._guard_composed_answer(answer, "generic", agents_used)
            elif wants_cheaper:
                agents_used.append("ResponseComposerAgent")
                answer = self.composer.compose_no_cheaper(session.last_products)
                answer = self._guard_composed_answer(answer, "generic", agents_used)
            else:
                answer = (
                    "Аналогов к показанным товарам в текущем ассортименте не вижу. "
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
                "Не могу безопасно показать аналоги по текущим данным. "
                "Лучше передать вопрос менеджеру."
            )
            self._append_history(session, message, answer)
            return self._response(session.session_id, answer, [], True, intent, session, agents_used)
        agents_used.append("ResponseComposerAgent")
        note = "Аналоги к показанным ранее товарам — проверьте отличия в характеристиках:"
        if query.category == "boilers" and query.slots.get("power_kw") is not None:
            requested_kw = float(query.slots["power_kw"])
            exact_in_stock_remaining = [
                product
                for product in alternatives
                if product.is_in_stock
                and (power := self.ranking_agent._extract_power_kw(product)) is not None
                and abs(power - requested_kw) <= 0.05
            ]
            exact_shown = sum(
                1
                for card in cards
                if (
                    (product := self._find_product_by_sku(card.sku)) is not None
                    and product.is_in_stock
                    and (
                        power := self.ranking_agent._extract_power_kw(product)
                    ) is not None
                    and abs(power - requested_kw) <= 0.05
                )
            )
            still_available = max(len(exact_in_stock_remaining) - exact_shown, 0)
            note = (
                f"Сначала показываю следующие котлы ровно {requested_kw:g} кВт в наличии. "
                "Если после них на странице идут позиции без остатка или другой мощности, "
                "это уже следующие по приоритету варианты."
            )
            if still_available:
                note += (
                    f" В наличии есть ещё {still_available} шт. с теми же параметрами — "
                    "напишите «покажи ещё»."
                )
        answer = self.composer.compose_products(
            cards,
            query,
            note=note,
        )
        answer = self._guard_composed_answer(answer, "products", agents_used)
        session.last_products = cards
        session.shown_product_skus = [
            *session.shown_product_skus,
            *[
                card.sku
                for card in cards
                if normalize_sku_token(card.sku) not in shown_skus
            ],
        ]
        self._append_history(session, message, answer)
        return self._response(session.session_id, answer, cards, False, intent, session, agents_used)

    def _shown_water_heater_reference_slots(
        self,
        session: SessionState,
    ) -> dict[str, object]:
        """Hydrate hard analogue constraints from one exact shown card."""
        if session.category != "water_heaters" or len(session.last_products) != 1:
            return {}
        product = self._find_product_by_sku(session.last_products[0].sku)
        if product is None:
            return {}
        return self.search_agent.water_heater_reference_slots(product)

    def _maybe_comparison_answer(self, message: str, session: SessionState) -> str | None:
        if len(session.last_products) < 2:
            return None
        text = normalize_text(message)
        markers = ["отлича", "в чем разница", "какая разница", "разница между", "сравни"]
        if not any(marker in text for marker in markers):
            return None
        return self.composer.compose_comparison(session.last_products)

    def _ground_catalog_sku_intent(
        self,
        message: str,
        intent: IntentResult,
    ) -> None:
        """Let exact feed identities override generic SKU-shape heuristics."""
        products = self.search_agent.resolve_sku_mentions(message)
        if not products:
            return
        text = normalize_text(message)
        comparison = self._looks_like_comparison_request(text)
        if len(products) >= 2 and comparison:
            categories = {
                self.search_agent.canonical_category(product)
                for product in products
            }
            intent.intent_type = "exact_sku_comparison"
            intent.category = categories.pop() if len(categories) == 1 else "other"
            intent.slots.pop("sku", None)
            intent.confidence = 1.0
            return

        exact_identity_only = bool(
            len(products) == 1
            and normalize_sku_token(text) == normalize_sku_token(products[0].sku)
        )
        target_lookup = (
            intent.intent_type == "complectation"
            or any(
                marker in text
                for marker in [
                    "артикул",
                    "sku",
                    "точн",
                    "найди",
                    "найти",
                    "покажи",
                    "карточк",
                    "цена",
                    "стоит",
                    "налич",
                ]
            )
            or len(text.split()) <= 2
            or exact_identity_only
        )
        if not target_lookup or self._looks_like_pump_boiler_compatibility(message):
            return
        product = products[0]
        intent.slots["sku"] = product.sku
        if intent.intent_type != "complectation":
            intent.intent_type = "exact_sku"
        intent.category = self.search_agent.canonical_category(product)
        intent.confidence = 1.0

    @staticmethod
    def _looks_like_comparison_request(text: str) -> bool:
        return any(
            marker in text
            for marker in [
                "сравни",
                "сравнение",
                "отлича",
                "в чем разница",
                "какая разница",
                "разница между",
            ]
        )

    def _needs_catalog_identity_resolution(
        self,
        message: str,
        intent: IntentResult,
    ) -> bool:
        """Whether a cold catalogue must load before exact-identity grounding."""
        text = normalize_text(message)
        if intent.intent_type in {"exact_sku", "exact_sku_comparison"}:
            return True
        if self._looks_like_comparison_request(text):
            return True
        if any(
            marker in text
            for marker in [
                "артикул",
                "sku",
                "код товара",
                "проигнор",
                "не сравнил",
                "не сравнила",
                "второй товар",
                "второй артикул",
            ]
        ):
            return True
        # Composite vendor codes often contain one mixed ASCII token
        # (``25/6G`` in ``PS 25/6G 180``), even when the generic router reads
        # the same digits as pump dimensions.
        composite_token = bool(
            re.search(
                r"(?<![a-zа-я0-9])"
                r"(?=[a-z0-9./+\-]*[a-z])"
                r"(?=[a-z0-9./+\-]*\d)"
                r"[a-z0-9]+(?:[./+\-][a-z0-9]+)+"
                r"(?![a-zа-я0-9])",
                text,
            )
        )
        if composite_token:
            return True
        compact_code = re.search(
            r"(?<![a-zа-я0-9])(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)"
            r"[a-z0-9]{3,}(?![a-zа-я0-9])",
            text,
        )
        return bool(
            compact_code
            and (
                len(text.split()) <= 2
                or any(
                    marker in text
                    for marker in [
                        "покажи",
                        "найди",
                        "карточк",
                        "цена",
                        "стоит",
                        "налич",
                    ]
                )
            )
        )

    def _maybe_direct_sku_comparison_response(
        self,
        session_id: str,
        message: str,
        intent: IntentResult,
        session: SessionState,
        agents_used: list[str],
    ) -> ChatResponse | None:
        """Compare explicitly named catalogue rows before semantic retrieval."""
        text = normalize_text(message)
        mentioned = self.search_agent.resolve_sku_mentions(message)
        correction = any(
            marker in text
            for marker in [
                "проигнор",
                "не сравнил",
                "не сравнила",
                "оба товар",
                "оба артикул",
                "второй товар",
                "второй артикул",
            ]
        )
        if not self._looks_like_comparison_request(text) and not correction:
            return None

        products: list[Product] = []
        seen: set[str] = set()
        if correction:
            for card in session.last_products:
                product = self._find_product_by_sku(card.sku)
                if product:
                    key = normalize_sku_token(product.sku)
                    if key not in seen:
                        products.append(product)
                        seen.add(key)
        for product in mentioned:
            key = normalize_sku_token(product.sku)
            if key not in seen:
                products.append(product)
                seen.add(key)
        if len(products) < 2:
            return None

        in_stock_only = bool(
            intent.flags.get("in_stock") or intent.slots.get("in_stock")
        )
        unavailable = [product for product in products if not product.is_in_stock]
        if in_stock_only and unavailable:
            articles = ", ".join(product.sku for product in unavailable)
            answer = (
                "Не могу показать эти позиции как товары «только в наличии»: "
                f"у артикулов {articles} сейчас нулевой остаток. "
                "Могу сравнить их справочно без фильтра наличия или подобрать доступные аналоги."
            )
            self._append_history(session, message, answer)
            return self._response(
                session_id,
                answer,
                [],
                False,
                intent,
                session,
                agents_used,
            )

        cards: list[ProductCard] = []
        for product in products[:3]:
            category = self.search_agent.canonical_category(product)
            query = SearchQuery(
                original_text=message,
                category=category,
                slots={},
            )
            card = self.card_agent.build_card(product, query)
            if not card:
                continue
            guard = self.guardrails.validate_cards([card], [product], query)
            if guard.ok:
                cards.append(card)
        if len(cards) < 2:
            return None

        answer = self.composer.compose_comparison(cards)
        answer = self._guard_composed_answer(answer, "products", agents_used)
        agents_used.extend(["FeedSearchAgent", "ProductCardAgent", "GuardrailsAgent"])
        if "ResponseComposerAgent" not in agents_used:
            agents_used.append("ResponseComposerAgent")
        session.last_products = cards
        categories = {
            self.search_agent.canonical_category(product)
            for product in products[: len(cards)]
        }
        if len(categories) == 1:
            session.category = categories.pop()
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_category = None
        session.pending_slot_keys = []
        self._append_history(session, message, answer)
        return self._response(
            session_id,
            answer,
            cards,
            False,
            intent,
            session,
            agents_used,
        )

    def _wants_choose_one(self, message: str) -> bool:
        text = normalize_text(message)
        markers = [
            "выбери один",
            "назови один",
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

    def _maybe_water_heater_operational_safety_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Stop unsafe water-heater operation before routing and catalogue search."""
        text = normalize_text(message)
        water_heater_context = bool(
            re.search(r"\b(?:водонагрев\w*|электробойлер\w*|бойлер\w*)\b", text)
        )
        if not water_heater_context:
            return None

        relief_valve = bool(
            re.search(
                r"\b(?:предохранительн\w*|сбросн\w*)"
                r"(?:\s+\w+){0,2}\s+клапан\w*\b",
                text,
            )
            or re.search(
                r"\bклапан\w*(?:\s+\w+){0,2}\s+"
                r"(?:предохранительн\w*|сбросн\w*)\b",
                text,
            )
        )
        blocking_action = bool(
            re.search(
                r"\b(?:заглуш\w*|перекры\w*|закры\w*|затк\w*|"
                r"зажат\w*|зажм\w*|пережат\w*|пережм\w*)\b",
                text,
            )
        )
        # Blocking the valve itself is already unsafe; customers do not always
        # name its drain/outlet explicitly.
        unsafe_relief_block = relief_valve and blocking_action

        start_action = bool(
            re.search(
                r"\b(?:включ\w*|запуст\w*|старт\w*|"
                r"подат\w*\s+питан\w*|начат\w*\s+нагрев\w*)\b",
                text,
            )
        )
        empty_state = bool(
            re.search(r"\bпуст\w*\b", text)
            or "без воды" in text
            or re.search(
                r"\b(?:не|еще\s+не|ещё\s+не)\s+"
                r"(?:заполн\w*|наполн\w*|набран\w*)\s*(?:вод\w*)?\b",
                text,
            )
            or re.search(
                r"\bвод\w*(?:\s+\w+){0,2}\s+"
                r"(?:не\s+подал\w*|не\s+набра\w*|нет)\b",
                text,
            )
        )
        unsafe_dry_start = start_action and empty_state

        if not (unsafe_relief_block or unsafe_dry_start):
            return None

        # This is an explicit topic switch.  Do not let words such as
        # «клапан», «слив» or an earlier dialogue leave radiator/sewer slots
        # attached to the water-heater safety answer.
        session.category = "water_heaters"
        session.slots = {}
        session.last_products = []
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_category = None
        session.pending_slot_keys = []
        session.pending_complectation_parts = []
        session.pending_handoff = None
        session.question_repeats = 0
        if session.handoff_status in {"awaiting_contact", "awaiting_consent", "failed"}:
            session.handoff_status = "none"

        if unsafe_relief_block:
            return (
                "Нет: не заглушайте и не перекрывайте слив, выход или отвод "
                "предохранительного клапана водонагревателя. Клапан должен свободно "
                "сбрасывать избыточное давление; блокировка может привести к повреждению "
                "бака, разрыву и ожогам горячей водой. Если клапан постоянно или сильно "
                "течёт, прекратите эксплуатацию, безопасно отключите питание и поручите "
                "проверку клапана, давления и монтажа квалифицированному специалисту."
            )
        return (
            "Нет: не включайте и не запускайте водонагреватель без воды. Сухой "
            "запуск может быстро повредить нагревательный элемент и создать опасность "
            "перегрева. До подачи питания заполните накопительный бак по инструкции "
            "или обеспечьте штатный проток для проточной модели и убедитесь, что вода "
            "идёт без воздуха. Если оборудование уже включали пустым, отключите питание "
            "и перед повторным запуском поручите его проверку специалисту."
        )

    def _maybe_gas_safety_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Stop unsafe gas-installation advice before intent and catalogue search."""
        text = normalize_text(message)
        safety_expires_at = session.slots.get("gas_safety_expires_at")
        safety_active = bool(session.slots.get("gas_safety_active"))
        if (
            safety_active
            and isinstance(safety_expires_at, int)
            and len(session.history) > safety_expires_at
        ):
            safety_active = False
            for key in [
                "gas_safety_active",
                "gas_safety_expires_at",
                "gas_safety_bathroom",
                "gas_safety_no_window",
                "gas_safety_ventilation_blocked",
                "gas_safety_category",
            ]:
                session.slots.pop(key, None)

        explicit_product = self._resolve_explicit_safety_product(text)
        explicit_product_category = (
            self.search_agent.canonical_category(explicit_product)
            if explicit_product is not None
            else None
        )
        explicit_product_is_gas = bool(
            explicit_product is not None
            and "газов" in self.search_agent._structured_text(explicit_product)
        )
        explicit_gas_boiler = bool(
            re.search(r"\bгазов\w*\s+кот[её]л\w*\b", text)
            or re.search(r"\bкот[её]л\w*[^.!?]{0,30}\bгазов\w*\b", text)
            or (
                explicit_product_category == "boilers"
                and explicit_product_is_gas
            )
        )
        explicit_gas_water_heater = bool(
            re.search(r"\bгазов\w*\s+колонк\w*\b", text)
            or re.search(r"\bколонк\w*[^.!?]{0,30}\bгазов\w*\b", text)
            or re.search(r"\bгазов\w*[^.!?]{0,30}\bводонагревател\w*\b", text)
            or re.search(r"\bводонагревател\w*[^.!?]{0,30}\bгазов\w*\b", text)
            or (
                explicit_product_category == "water_heaters"
                and explicit_product_is_gas
            )
        )
        stored_safety_category = normalize_text(
            str(session.slots.get("gas_safety_category") or "")
        )
        if explicit_gas_water_heater:
            safety_category = "water_heaters"
        elif explicit_gas_boiler:
            safety_category = "boilers"
        elif safety_active and stored_safety_category in {"boilers", "water_heaters"}:
            safety_category = stored_safety_category
        else:
            safety_category = "boilers"

        gas_leak = any(
            marker in text
            for marker in [
                "запах газа",
                "пахнет газ",
                "утечка газа",
                "утечку газа",
                "шипит газ",
            ]
        )
        leak_explicitly_denied = bool(
            re.search(
                r"\b(?:запах(?:а)?\s+газа|утечк\w*\s+газа)\s+"
                r"(?:нет|отсутств\w*|исключен\w*|не\s+обнаружен\w*)\b",
                text,
            )
            or re.search(
                r"\bнет\s+(?:запах\w*\s+газа|утечк\w*\s+газа)\b",
                text,
            )
        )
        if any(
            marker in text
            for marker in [
                "или нет",
                "не уверен",
                "не уверена",
                "не понимаю",
                "все-таки есть",
                "всё-таки есть",
            ]
        ):
            leak_explicitly_denied = False
        if gas_leak and not leak_explicitly_denied:
            session.category = safety_category
            session.slots["gas_safety_active"] = True
            session.slots["gas_safety_expires_at"] = len(session.history) + 6
            session.slots["gas_safety_category"] = safety_category
            session.last_products = []
            session.pending_question = None
            session.pending_category = None
            session.pending_handoff = None
            if session.handoff_status in {"awaiting_contact", "awaiting_consent", "failed"}:
                session.handoff_status = "none"
            return (
                "Это возможная утечка газа. Не включайте и не выключайте свет и электроприборы, "
                "не используйте огонь. Если это можно сделать без риска, перекройте газ, откройте "
                "окна, выйдите из помещения и звоните в аварийную газовую службу 104 или 112 "
                "снаружи. К подбору товара возвращайтесь только после проверки специалистами."
            )

        explicit_non_gas_topic = bool(
            (
                "электр" in text
                and any(
                    marker in text
                    for marker in ["котел", "котл", "водонагрев", "бойлер", "колонк"]
                )
                and "газов" not in text
            )
            or any(
                marker in text
                for marker in [
                    "нужен насос",
                    "нужна помпа",
                    "подбери насос",
                    "насос для",
                    "труба для",
                    "подбери трубу",
                ]
            )
        )
        if safety_active and explicit_non_gas_topic:
            for key in [
                "gas_safety_active",
                "gas_safety_expires_at",
                "gas_safety_bathroom",
                "gas_safety_no_window",
                "gas_safety_ventilation_blocked",
                "gas_safety_category",
            ]:
                session.slots.pop(key, None)
            safety_active = False
        referential_safety_followup = bool(
            safety_active
            and not explicit_non_gas_topic
            and (
                any(
                    marker in text
                    for marker in [
                        "вентиляц",
                        "вытяж",
                        "вентканал",
                        "приток",
                        "окн",
                        "ванн",
                        "котл",
                        "водонагрев",
                        "колонк",
                    ]
                )
                or any(
                    marker in text
                    for marker in [
                        "все равно",
                        "всё равно",
                        "так сделать",
                        "так можно",
                        "все-таки",
                        "всё-таки",
                        "почему нельзя",
                    ]
                )
            )
        )
        gas_context = bool(
            explicit_gas_boiler
            or explicit_gas_water_heater
            or referential_safety_followup
        )
        bathroom = any(marker in text for marker in ["ванн", "сануз"])
        no_window = any(
            marker in text
            for marker in [
                "без окна",
                "без окон",
                "нет окна",
                "нет окон",
                "окна нет",
                "окно не делать",
                "не делать окно",
            ]
        )
        no_window = bool(
            no_window
            or re.search(r"\bокн\w*[^.!?]{0,20}\bотсутств\w*", text)
            or re.search(r"\bотсутств\w*[^.!?]{0,20}\bокн\w*", text)
        )
        air_path = (
            r"(?:вентиляц\w*|вытяжк\w*|вентканал\w*|"
            r"приток\w*(?:\s+воздуха)?)"
        )
        ventilation_blocked = bool(
            re.search(
                rf"{air_path}(?:\s+\w+){{0,3}}\s+"
                r"(?:заглуш\w*|перекры\w*|закры\w*|отключ\w*|убран\w*|"
                r"не\s+работа\w*|нет|отсутств\w*)\b",
                text,
            )
            or re.search(
                r"(?:заглуш\w*|перекры\w*|закры\w*|отключ\w*|убрат\w*)"
                rf"(?:\s+\w+){{0,3}}\s+{air_path}",
                text,
            )
            or re.search(rf"\b(?:без|нет)\s+{air_path}", text)
            or re.search(rf"{air_path}\s+(?:нет|отсутств\w*)\b", text)
        )
        ventilation_explicitly_safe = bool(
            re.search(
                rf"{air_path}[^.!?]{{0,45}}\bне\s+"
                r"(?:заглушен\w*|перекрыт\w*|закрыт\w*|отключен\w*)",
                text,
            )
            or re.search(
                rf"{air_path}[^.!?]{{0,35}}(?<!не\s)\bработа\w*",
                text,
            )
            or re.search(
                rf"{air_path}[^.!?]{{0,35}}\b(?:восстановлен\w*|открыт\w*)",
                text,
            )
        )
        if ventilation_explicitly_safe:
            ventilation_blocked = False
        window_explicitly_restored = bool(
            re.search(
                r"\bокн\w*[^.!?]{0,25}"
                r"(?:есть|установлен\w*|сделан\w*|появил\w*|добавлен\w*)",
                text,
            )
        )
        stored_bathroom = bool(session.slots.get("gas_safety_bathroom"))
        stored_no_window = bool(session.slots.get("gas_safety_no_window"))
        stored_ventilation_blocked = bool(
            session.slots.get("gas_safety_ventilation_blocked")
        )
        if safety_active and ventilation_explicitly_safe:
            stored_ventilation_blocked = False
            session.slots["gas_safety_ventilation_blocked"] = False
        if safety_active and window_explicitly_restored:
            stored_no_window = False
            session.slots["gas_safety_no_window"] = False
        effective_bathroom = bathroom or (safety_active and stored_bathroom)
        effective_no_window = no_window or (safety_active and stored_no_window)
        effective_ventilation_blocked = ventilation_blocked or (
            safety_active and stored_ventilation_blocked
        )
        safety_resolved = bool(
            safety_active
            and not effective_ventilation_blocked
            and not (effective_bathroom and effective_no_window)
        )
        if safety_resolved:
            for key in [
                "gas_safety_active",
                "gas_safety_expires_at",
                "gas_safety_bathroom",
                "gas_safety_no_window",
                "gas_safety_ventilation_blocked",
                "gas_safety_category",
            ]:
                session.slots.pop(key, None)
            safety_active = False
            referential_safety_followup = False
            gas_context = explicit_gas_boiler or explicit_gas_water_heater
            if any(marker in text for marker in ["можно", "став", "установ", "запуст"]):
                equipment = (
                    "газового водонагревателя"
                    if safety_category == "water_heaters"
                    else "газового котла"
                )
                return (
                    "Восстановление вентиляции устраняет один из опасных факторов, но само "
                    f"по себе не подтверждает допустимость установки {equipment}. Помещение, "
                    "приток воздуха, дымоудаление и проект подключения должна проверить "
                    "специализированная газовая организация до монтажа и запуска."
                )

        unsafe_room = (
            effective_ventilation_blocked
            or (effective_bathroom and effective_no_window)
        )
        if not (gas_context and unsafe_room):
            return None

        session.category = safety_category
        safety_slots: dict[str, Any] = {
            "gas_safety_active": True,
            "gas_safety_expires_at": len(session.history) + 6,
            "gas_safety_bathroom": effective_bathroom,
            "gas_safety_no_window": effective_no_window,
            "gas_safety_ventilation_blocked": effective_ventilation_blocked,
            "gas_safety_category": safety_category,
        }
        if safety_category == "water_heaters":
            safety_slots["energy_source"] = "газовый"
            if "колонк" in text:
                safety_slots["heater_type"] = "проточный"
        else:
            safety_slots["boiler_type"] = "газовый"
        session.slots = safety_slots
        session.last_products = []
        session.pending_question = None
        session.pending_intent_type = None
        session.pending_category = None
        session.pending_slot_keys = []
        session.pending_handoff = None
        if session.handoff_status in {"awaiting_contact", "awaiting_consent", "failed"}:
            session.handoff_status = "none"
        if (
            ventilation_explicitly_safe
            and effective_bathroom
            and effective_no_window
        ):
            equipment = (
                "газовом водонагревателе"
                if safety_category == "water_heaters"
                else "газовом котле"
            )
            return (
                "То, что вентиляция восстановлена, важно, но исходная проблема "
                f"полностью не снята: речь всё ещё о {equipment} в ванной без окна. "
                "Не устанавливайте и не запускайте его до проверки помещения, притока "
                "воздуха, дымоудаления и проекта специализированной газовой организацией."
            )
        equipment = (
            "газовый водонагреватель (газовую колонку)"
            if safety_category == "water_heaters"
            else "газовый котёл"
        )
        return (
            "Нет: заглушать или перекрывать вентиляцию в помещении с газовым "
            f"оборудованием нельзя. Не устанавливайте и не запускайте {equipment} "
            "в ванной без согласованного решения и проверки специализированной газовой "
            "организацией. Отсутствие окна, объём помещения, постоянный приток воздуха, "
            "вентиляцию и дымоудаление должен проверить специалист; закрытая камера "
            "сгорания не отменяет этих требований. Сначала восстановите вентиляцию и "
            "получите подтверждение допустимости установки для конкретного помещения."
        )

    def _maybe_electrical_safety_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Stop unsafe electrical-installation advice before catalogue routing."""
        text = normalize_text(message)
        safety_expires_at = session.slots.get("electrical_safety_expires_at")
        safety_active = bool(session.slots.get("electrical_safety_active"))
        if (
            safety_active
            and isinstance(safety_expires_at, int)
            and len(session.history) > safety_expires_at
        ):
            safety_active = False
            session.slots.pop("electrical_safety_active", None)
            session.slots.pop("electrical_safety_sku", None)
            session.slots.pop("electrical_safety_expires_at", None)
            session.slots.pop("electrical_safety_category", None)
        product = self._resolve_electrical_safety_product(text, session)
        product_category = (
            self.search_agent.canonical_category(product)
            if product is not None
            else None
        )
        explicit_water_heater = bool(
            any(marker in text for marker in ["водонагрев", "электробойлер"])
            or (
                "бойлер" in text
                and not any(marker in text for marker in ["котел", "котёл", "котл"])
            )
        )
        explicit_boiler = any(
            marker in text for marker in ["котел", "котёл", "электрокот"]
        )
        if (
            explicit_water_heater
            and not explicit_boiler
            and product_category != "water_heaters"
        ):
            # A fallback to the only/previous boiler must not override the
            # appliance explicitly named in the current safety question.
            product = None
            product_category = None
        elif explicit_boiler and product_category != "boilers":
            product = None
            product_category = None
        stored_safety_category = normalize_text(
            str(session.slots.get("electrical_safety_category") or "")
        )
        if product_category in {"boilers", "water_heaters"}:
            safety_category = product_category
        elif explicit_water_heater and not explicit_boiler:
            safety_category = "water_heaters"
        elif explicit_boiler:
            safety_category = "boilers"
        elif safety_active and stored_safety_category in {"boilers", "water_heaters"}:
            safety_category = stored_safety_category
        elif session.category in {"boilers", "water_heaters"}:
            safety_category = session.category
        else:
            safety_category = None
        equipment_context = bool(
            safety_category in {"boilers", "water_heaters"}
            or "квт" in text
            or safety_active
        )
        electrical_action = any(
            marker in text
            for marker in [
                "подключ",
                "розет",
                "удлинител",
                "переходник",
                "кабел",
                "провод",
                "автомат",
                "узо",
                "фаз",
                "линия",
                "сечен",
                "питан",
            ]
        )
        installation_question = any(
            marker in text
            for marker in [
                "можно ли",
                "как подключ",
                "подключить",
                "подключу",
                "подойдет розет",
                "подойдёт розет",
                "обычн",
                "через переходник",
                "через удлинител",
                "отдельн лини",
                "какое сечен",
            ]
        )
        if not (
            equipment_context
            and electrical_action
            and (installation_question or safety_active)
        ):
            return None

        voltage, three_phase, requires_specialist = (
            self._electrical_supply_facts(product)
            if product
            else (None, False, False)
        )

        session.category = safety_category or "boilers"
        session.slots["electrical_safety_active"] = True
        session.slots["electrical_safety_expires_at"] = len(session.history) + 6
        session.slots["electrical_safety_category"] = session.category
        if product:
            session.slots["electrical_safety_sku"] = product.sku

        if voltage == 380 or three_phase:
            model = f" {product.sku} — {product.name}" if product else ""
            qualification = (
                " Карточка также требует квалифицированного подключения."
                if requires_specialist
                else ""
            )
            return (
                f"Нет, не подключайте{model} к обычной розетке 220 В. "
                "По карточке требуется трёхфазное питание 380 В; обычная розетка, "
                "удлинитель или переходник для такого подключения не подходят."
                f"{qualification} Не включайте оборудование до проверки "
                "квалифицированным электриком."
            )
        if session.category == "water_heaters":
            return (
                "Не подключайте электрический водонагреватель к обычной розетке только "
                "по названию товара или значению 220 В. Нужно сверить паспорт: мощность, "
                "допустимость штепсельного подключения, заземление, УЗО, выделенную линию "
                "и защиту. Схему питания, кабель и автоматику должен проверить "
                "квалифицированный электрик. До проверки оборудование не включайте."
            )
        return (
            "Не подключайте мощный электрический котёл к обычной розетке только на основании "
            "того, что указано 220 В. Нужно сверить паспорт, выделенную мощность, схему питания "
            "и защиту; кабель и автоматику должен проверить квалифицированный электрик. "
            "До проверки оборудование не включайте."
        )

    def _resolve_explicit_safety_product(
        self,
        text: str,
    ) -> Product | None:
        """Resolve only a product identity explicitly present in this turn."""
        mentioned = self.search_agent.resolve_sku_mentions(text)
        if len(mentioned) == 1:
            return mentioned[0]
        if len(mentioned) > 1:
            return None

        normalized_text = normalize_text(text)
        exact_name_matches = [
            product
            for product in self.search_agent.products
            if len(normalize_text(product.name).split()) >= 3
            and re.search(
                rf"(?<!\w){re.escape(normalize_text(product.name))}(?!\w)",
                normalized_text,
            )
        ]
        if not exact_name_matches:
            return None
        exact_name_matches.sort(
            key=lambda product: len(normalize_text(product.name)),
            reverse=True,
        )
        return exact_name_matches[0]

    def _resolve_electrical_safety_product(
        self,
        text: str,
        session: SessionState,
    ) -> Product | None:
        explicit_product = self._resolve_explicit_safety_product(text)
        if explicit_product is not None:
            return explicit_product
        # Resolve an explicitly named model before falling back to the previous
        # safety target. This prevents «а Arderia E9?» from inheriting E12 facts.
        explicit_matches: list[Product] = []
        text_tokens = set(_WORD_RE.findall(text))
        for product in self.search_agent.products:
            product_text = normalize_text(product.name)
            model_tokens = {
                token
                for token in _WORD_RE.findall(product_text)
                if re.fullmatch(r"[a-zа-я]+-?\d+[a-z0-9.-]*", token)
            }
            if model_tokens.intersection(text_tokens):
                explicit_matches.append(product)
        if len(explicit_matches) == 1:
            return explicit_matches[0]
        if session.slots.get("electrical_safety_sku"):
            product = self._find_product_by_sku(
                str(session.slots["electrical_safety_sku"])
            )
            if product:
                return product
        if session.last_products:
            product = self._find_product_by_sku(session.last_products[0].sku)
            if product:
                return product
        boiler_products = [
            product
            for product in self.search_agent.products
            if self.search_agent.canonical_category(product) == "boilers"
        ]
        if len(boiler_products) == 1:
            return boiler_products[0]
        return None

    @staticmethod
    def _electrical_supply_facts(
        product: Product,
    ) -> tuple[int | None, bool, bool]:
        """Read model-specific supply facts without first-match leakage.

        Structured attributes win. Description and passport are only used when
        each source contains one unambiguous voltage value.
        """
        attribute_text = normalize_text(
            " ".join(
                f"{key} {value}"
                for key, value in product.attributes_normalized.items()
                if any(
                    marker in normalize_text(key)
                    for marker in ["напряж", "питан", "фаз", "подключ"]
                )
            )
        )
        sources = [
            attribute_text,
            normalize_text(product.description or ""),
            normalize_text(product.docs_text or ""),
        ]
        voltage: int | None = None
        phase_text = ""
        for source in sources:
            values = {
                int(value)
                for value in re.findall(r"\b(220|230|380|400)\s*в?\b", source)
            }
            normalized_values = {
                220 if value in {220, 230} else 380
                for value in values
            }
            if len(normalized_values) == 1:
                voltage = normalized_values.pop()
                phase_text = source
                break
        all_model_text = normalize_text(
            " ".join([attribute_text, product.description or "", product.docs_text or ""])
        )
        three_phase = bool(
            re.search(r"(?:3|трех)[- ]?фаз", phase_text or all_model_text)
        )
        requires_specialist = any(
            marker in all_model_text
            for marker in ["квалифицирован", "специалист", "электрик"]
        )
        return voltage, three_phase, requires_specialist

    def _maybe_sink_question(self, message: str) -> str | None:
        text = normalize_text(message)
        if "раковин" not in text and "под раковину" not in text:
            return None
        return (
            "Под раковину обычно нужны: сифон (слив), гибкая подводка или угловой кран. "
            "Что именно нужно — слив/сифон или запорный кран?"
        )

    def _maybe_sink_flow_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        text = normalize_text(message)
        if (
            re.search(r"\b(?:водонагрев\w*|бойлер\w*)\b", text)
            or re.search(r"\bгазов\w*\s+колонк\w*\b", text)
        ):
            # «Водонагреватель под мойку» names a complete appliance.  The
            # location phrase must not be mistaken for an undersink
            # siphon/valve request before intent routing starts.
            return None
        state = session.slots.get("sink_flow")
        mentions_sink = any(marker in text for marker in ["под раковин", "под мойк"])
        drain_markers = ["слив", "сифон", "водослив"]
        valve_markers = ["кран", "вентиль", "подводк", "смесител"]
        vague_markers = ["фигн", "штук", "эта", "это", "что то", "что-то"]

        if mentions_sink and any(marker in text for marker in drain_markers):
            session.slots = {
                "sink_flow": "awaiting_drain_dimensions",
                "sink_component": "слив/сифон",
            }
            session.last_products = []
            session.category = "sewer"
            session.pending_question = (
                "Какой размер выпуска раковины/мойки и подключения к канализации?"
            )
            session.pending_intent_type = "broad_category"
            return (
                "Понял, нужен слив/сифон под раковину. Уточните размер выпуска "
                "раковины/мойки и диаметр подключения к канализации, а также одна или две "
                "чаши у мойки. Без этих размеров не буду подставлять случайный сифон."
            )

        if mentions_sink and not any(marker in text for marker in valve_markers):
            if any(marker in text for marker in vague_markers) or not any(
                marker in text for marker in drain_markers
            ):
                session.slots = {"sink_flow": "awaiting_kind"}
                session.last_products = []
                session.category = None
                session.pending_question = "Нужен слив/сифон или запорный кран?"
                session.pending_intent_type = "broad_category"
                return (
                    "Под раковиной могут быть разные узлы: сифон/слив отводит воду в "
                    "канализацию, гибкая подводка подаёт воду, а запорный кран её перекрывает. "
                    "Что именно нужно — слив/сифон, подводка или кран?"
                )

        if state == "awaiting_kind":
            if any(marker in text for marker in drain_markers):
                session.slots["sink_flow"] = "awaiting_drain_dimensions"
                session.slots["sink_component"] = "слив/сифон"
                session.category = "sewer"
                session.pending_question = (
                    "Какой размер выпуска раковины/мойки и подключения к канализации?"
                )
                return (
                    "Понял, нужен слив/сифон. Уточните размер выпуска раковины/мойки "
                    "и диаметр подключения к канализации, а также одна или две чаши у мойки. "
                    "По этим данным можно проверить подходящую позицию без угадывания."
                )
            if not any(marker in text for marker in valve_markers):
                return (
                    "Уточните назначение детали под раковиной: слив/сифон, гибкая подводка "
                    "или запорный кран?"
                )

        if state == "awaiting_drain_dimensions":
            has_dimensions = bool(re.search(r"\b\d{2,3}\s*(?:мм)?\b", text))
            if not has_dimensions:
                return (
                    "Для слива/сифона нужны размер выпуска раковины/мойки и диаметр "
                    "подключения к канализации. Напишите размеры или маркировку, если она есть."
                )
            session.slots.pop("sink_flow", None)
            session.pending_question = None
            session.pending_intent_type = None
        return None

    def _maybe_water_emergency_answer(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Keep an active leak out of catalog search until the danger is contained."""
        text = normalize_text(message)
        emergency_state = session.slots.get("water_emergency")
        water_shut = any(
            marker in text
            for marker in [
                "воду перекрыл",
                "воду перекрыла",
                "воду перекрыли",
                "перекрыл воду",
                "перекрыла воду",
                "стояк перекрыл",
                "стояк перекрыли",
                "подачу воды перекрыл",
                "подача воды остановлена",
                "вода больше не течет",
                "вода больше не течёт",
                "радиатор перекрыл",
                "батарею перекрыл",
                "краны радиатора перекрыл",
                "отопление перекрыл",
                "теплоноситель больше не течет",
                "теплоноситель больше не течёт",
            ]
        )
        flood_markers = ["затоп", "заливает", "топит сосед", "потоп"]
        rupture_markers = ["прорвало", "прорыв", "лопнула", "разорвало"]
        water_fixture_markers = [
            "труб",
            "вода",
            "стояк",
            "под мойк",
            "под раковин",
            "кран",
            "шланг",
            "подводк",
            "сифон",
            "радиатор",
            "батаре",
            "отоплен",
            "теплонос",
            "кипяток",
        ]
        looks_like_leak = any(marker in text for marker in flood_markers) or (
            any(marker in text for marker in rupture_markers)
            and any(marker in text for marker in water_fixture_markers)
        ) or (
            any(marker in text for marker in ["течет", "течёт", "льется", "льётся"])
            and any(marker in text for marker in water_fixture_markers)
        )

        if looks_like_leak:
            heating_leak = any(
                marker in text
                for marker in ["радиатор", "батаре", "отоплен", "теплонос", "кипяток"]
            )
            # Авария — новая приоритетная тема. Убираем старые проектные параметры,
            # чтобы следующий короткий ответ не вернулся к отоплению или корзине.
            state = "contained" if water_shut else "active"
            session.slots = {
                "water_emergency": state,
                "emergency_kind": "heating" if heating_leak else "water",
            }
            session.last_products = []
            session.category = "pipes"
            session.pending_question = (
                "Где повреждение, какой материал и диаметр трубы/размер резьбы?"
                if water_shut
                else "Сообщите, когда вода будет перекрыта."
            )
            session.pending_intent_type = "emergency"
            session.pending_complectation_parts = []
            session.question_repeats = 0
            return (
                (
                    HEATING_EMERGENCY_CONTAINED_RESPONSE
                    if heating_leak
                    else WATER_EMERGENCY_CONTAINED_RESPONSE
                )
                if water_shut
                else (
                    HEATING_EMERGENCY_FIRST_RESPONSE
                    if heating_leak
                    else WATER_EMERGENCY_FIRST_RESPONSE
                )
            )

        if emergency_state == "active":
            if water_shut:
                session.slots["water_emergency"] = "contained"
                session.pending_question = (
                    "Где повреждение, какой материал и диаметр трубы/размер резьбы?"
                )
                session.pending_intent_type = "emergency"
                return (
                    HEATING_EMERGENCY_CONTAINED_RESPONSE
                    if session.slots.get("emergency_kind") == "heating"
                    else WATER_EMERGENCY_CONTAINED_RESPONSE
                )
            return (
                HEATING_EMERGENCY_FIRST_RESPONSE
                if session.slots.get("emergency_kind") == "heating"
                else WATER_EMERGENCY_FIRST_RESPONSE
            )

        if emergency_state == "contained" and (
            water_shut
            or not any(
                marker in text
                for marker in [
                    "диаметр",
                    "резьб",
                    "мм",
                    "дюйм",
                    "металлопласт",
                    "полипроп",
                    "ppr",
                    "шланг",
                    "подводк",
                    "сифон",
                    "соединен",
                    "соединён",
                ]
            )
        ):
            return (
                HEATING_EMERGENCY_CONTAINED_RESPONSE
                if session.slots.get("emergency_kind") == "heating"
                else WATER_EMERGENCY_CONTAINED_RESPONSE
            )

        if emergency_state == "contained":
            # Появились данные о повреждении — обычный безопасный подбор может
            # продолжиться, но без старого контекста отопления.
            session.slots.pop("water_emergency", None)
            session.pending_question = None
            session.pending_intent_type = None
        return None

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

    def _compose_query_note(
        self,
        query: SearchQuery,
        products: list[Product] | None = None,
    ) -> str | None:
        notes: list[str] = []
        if query.category == "boilers" and query.slots.get("power_kw") is not None:
            requested_kw = float(query.slots["power_kw"])
            exact_in_stock = [
                product
                for product in (products or [])
                if product.is_in_stock
                and (power := self.ranking_agent._extract_power_kw(product)) is not None
                and abs(power - requested_kw) <= 0.05
            ]
            if exact_in_stock:
                page_limit = self._card_limit(query)
                note = f"Сначала показываю котлы ровно {requested_kw:g} кВт в наличии."
                if len(exact_in_stock) > page_limit:
                    remaining = len(exact_in_stock) - page_limit
                    note += (
                        f" Сейчас показываю первые {page_limit}; в наличии есть ещё "
                        f"{remaining} шт. с теми же параметрами — напишите «покажи ещё»."
                    )
                else:
                    note += (
                        " После них идут точные совпадения без остатка, а затем "
                        "ближайшие по мощности варианты — это уже альтернативы "
                        "указанному параметру."
                    )
                notes.append(note)
            else:
                notes.append(
                    f"Котлов ровно {requested_kw:g} кВт в наличии не нашёл. Сначала "
                    "показываю точные совпадения без остатка, затем ближайшие по мощности "
                    "варианты с явным отличием в характеристиках."
                )
        if query.category == "pipes" and (
            query.slots.get("operating_temperature_c") is not None
            or query.slots.get("operating_pressure_bar") is not None
        ):
            checks = []
            if query.slots.get("operating_temperature_c") is not None:
                checks.append(
                    f"до {float(query.slots['operating_temperature_c']):g} °C"
                )
            if query.slots.get("operating_pressure_bar") is not None:
                checks.append(
                    f"при {float(query.slots['operating_pressure_bar']):g} бар"
                )
            unconfirmed = [
                product
                for product in (products or [])
                if self.search_agent.pipe_ratings_status(
                    product,
                    query.slots.get("operating_temperature_c"),
                    query.slots.get("operating_pressure_bar"),
                )
                is None
            ]
            if unconfirmed:
                brands = ", ".join(
                    dict.fromkeys(
                        product.brand or product.sku
                        for product in unconfirmed
                    )
                )
                notes.append(
                    "Сначала показываю VALTEC, если он соответствует назначению, "
                    "материалу и размеру. Для части карточек "
                    f"({brands}) в фиде нет числового подтверждения режима "
                    + " ".join(checks)
                    + " — это кандидаты, а не подтверждённый расчётом выбор; "
                    "перед монтажом обязательно сверьте паспорт/диаграмму трубы."
                )
            else:
                notes.append(
                    "Оставил только трубы, у которых карточка подтверждает работу "
                    + " ".join(checks)
                    + ". Перед монтажом всё равно сверьте сочетание температуры и "
                    "давления по диаграмме/паспорту конкретной трубы."
                )
        if query.category == "pipes" and query.slots.get("total_length_m"):
            notes.append(
                f"Общий метраж {float(query.slots['total_length_m']):g} м учёл как требуемое количество, "
                "а не как диаметр. В карточке не указаны длина одного отрезка и единица цены, "
                "поэтому стоимость всего метража не умножаю без уточнения."
            )
        if query.category == "pumps" and (
            query.slots.get("required_flow_m3_h") is not None
            or query.slots.get("required_head_m") is not None
        ):
            duty = []
            if query.slots.get("required_flow_m3_h") is not None:
                duty.append(
                    f"расход {float(query.slots['required_flow_m3_h']):g} м³/ч"
                )
            if query.slots.get("required_head_m") is not None:
                duty.append(
                    f"напор {float(query.slots['required_head_m']):g} м"
                )
            notes.append(
                "Предварительный фильтр по рабочей точке: "
                + ", ".join(duty)
                + ". Максимальный напор и максимальная подача не достигаются "
                "одновременно; окончательно модель нужно проверить по насосной кривой."
            )
        if query.slots.get("fallback_after_repeat"):
            if query.category == "pumps" and normalize_text(str(query.slots.get("pump_type") or "")) == "циркуляционный":
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовой "
                    "вариант из ассортимента. Для циркуляционного насоса важно сверить напор, "
                    "монтажную длину 130/180 мм и присоединение; без этих данных это не "
                    "окончательный подбор."
                )
            elif query.category == "pumps":
                notes.append(
                    "Чтобы не гонять вас по кругу одним и тем же вопросом, показываю типовой "
                    "вариант из ассортимента. Тип насоса не уточнён, поэтому это не "
                    "окончательный подбор — сверьте назначение и характеристики в карточке."
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

    def _stabilize_active_goal(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> None:
        """Keep answers attached to the pending product goal.

        Words such as «котёл» and «радиаторы» often describe the heating system
        while the customer is answering a pump question.  They are context, not
        an implicit request to abandon the pump selection.
        """
        text = normalize_text(message)
        if (
            session.pending_category == "boilers"
            and "газовый или электрический" in normalize_text(
                session.pending_question or ""
            )
        ):
            boiler_choice = self._explicit_boiler_type_choice(text)
            uncertain_choice = bool(
                any(marker in text for marker in ["не знаю", "не уверен", "не решила"])
                and "газ" in text
                and "электр" in text
            )
            if boiler_choice:
                intent.category = "boilers"
                intent.intent_type = "attribute_request"
                intent.slots["boiler_type"] = boiler_choice
                intent.is_topic_change = False
            elif uncertain_choice:
                intent.category = "boilers"
                intent.intent_type = "broad_category"
                intent.slots.pop("boiler_type", None)
                session.slots.pop("boiler_type", None)
                intent.is_topic_change = False
            if intent.category == "boilers":
                for pump_only in [
                    "product_kind",
                    "pump_type",
                    "pump_use",
                    "pump_context",
                ]:
                    intent.slots.pop(pump_only, None)

        explicit_pump = self._is_explicit_pump_selection_or_correction(text)
        if explicit_pump:
            previous_category = session.category
            intent.category = "pumps"
            intent.intent_type = (
                "attribute_request"
                if any(marker in text for marker in ["циркуляц", "отоплен"])
                else "broad_category"
            )
            intent.is_topic_change = bool(
                previous_category and previous_category != "pumps"
            )
            if "циркуляц" in text or "отоплен" in text:
                intent.slots["pump_type"] = "циркуляционный"
                intent.slots["pump_use"] = "отопление"
            elif "дренаж" in text or "откач" in text:
                intent.slots.pop("pump_type", None)
                intent.slots["pump_use"] = "откачка воды"
            elif "давлен" in text or "напор" in text:
                intent.slots.pop("pump_type", None)
                intent.slots["pump_use"] = "повышение давления"
            elif any(marker in text for marker in ["водоснаб", "скваж", "колод"]):
                intent.slots.pop("pump_type", None)
                intent.slots["pump_use"] = "водоснабжение"
            elif "полив" in text:
                intent.slots.pop("pump_type", None)
                intent.slots["pump_use"] = "полив"
            for boiler_only in [
                "boiler_type",
                "contours",
                "needs_voltage_clarification",
                "voltage_v",
            ]:
                intent.slots.pop(boiler_only, None)
            return
        pending_category = session.pending_category
        if pending_category == "pipes":
            pipe_answer_markers = [
                "радиатор",
                "магистрал",
                "обвяз",
                "тепл",
                "тёпл",
                "горяч",
                "холод",
                "гвс",
                "ppr",
                "ппр",
                "pex",
                "pe-rt",
                "металлопласт",
                "пнд",
                "пэ100",
                "давлен",
                "бар",
                "градус",
                "°",
                "скрыт",
                "открыт",
                "под земл",
                "скваж",
                "колод",
            ]
            explicit_other_product = any(
                re.search(
                    rf"\b(?:нужен|нужна|нужны|подбери|ищу)\b[^.!?]{{0,20}}\b{noun}",
                    text,
                )
                for noun in [
                    "насос",
                    "кот[её]л",
                    "бойлер",
                    "радиатор(?:ы)?",
                    "кран",
                ]
            )
            if (
                any(marker in text for marker in pipe_answer_markers)
                and not explicit_other_product
            ):
                intent.category = "pipes"
                intent.intent_type = "attribute_request"
                intent.is_topic_change = False
                return
        if pending_category != "pumps":
            return
        if self._is_explicit_non_pump_topic_change(text):
            if "труб" in text:
                intent.slots.setdefault("element_type", "труба")
            intent.is_topic_change = True
            return
        answers_pump_question = any(
            marker in text
            for marker in [
                "отоплен",
                "радиатор",
                "тепл",
                "скваж",
                "колод",
                "полив",
                "дренаж",
                "откач",
                "водоснаб",
                "давлен",
                "труба",
            ]
        )
        if not answers_pump_question:
            return
        intent.category = "pumps"
        intent.intent_type = "attribute_request"
        intent.is_topic_change = False
        if any(marker in text for marker in ["отоплен", "радиатор", "тепл"]):
            intent.slots["pump_type"] = "циркуляционный"
            intent.slots["pump_use"] = "отопление"
        elif any(marker in text for marker in ["дренаж", "откач"]):
            intent.slots["pump_type"] = "дренажный"
            intent.slots["pump_use"] = "откачка воды"
        elif any(marker in text for marker in ["скваж", "колод", "водоснаб"]):
            intent.slots["pump_use"] = "водоснабжение"
        elif "полив" in text:
            intent.slots["pump_use"] = "полив"
        for boiler_only in [
            "boiler_type",
            "contours",
            "needs_voltage_clarification",
            "voltage_v",
        ]:
            intent.slots.pop(boiler_only, None)

    @staticmethod
    def _explicit_boiler_type_choice(text: str) -> str | None:
        """Resolve an answer to the gas/electric question with scoped negation."""
        normalized = normalize_text(text)
        if (
            any(marker in normalized for marker in ["не знаю", "не уверен", "не решила"])
            and "газ" in normalized
            and "электр" in normalized
        ):
            return None

        wants_gas = bool(
            re.search(
                r"\b(?:хочу|нужен|нужна|выбираю|давай\w*)"
                r"(?:\s+\w+){0,2}\s+газов\w*",
                normalized,
            )
        )
        wants_electric = bool(
            re.search(
                r"\b(?:хочу|нужен|нужна|выбираю|давай\w*)"
                r"(?:\s+\w+){0,2}\s+электр\w*",
                normalized,
            )
        )
        if wants_gas != wants_electric:
            return "газовый" if wants_gas else "электрический"

        rejects_electric = bool(
            re.search(r"\bне\s+электр\w*", normalized)
            or re.search(r"\bэлектр\w*(?:\s+\w+){0,2}\s+не\s+нуж\w*", normalized)
            or re.search(
                r"\bне\s+нуж\w*(?:\s+\w+){0,2}\s+электр\w*",
                normalized,
            )
        )
        rejects_gas = bool(
            re.search(r"\bне\s+газов\w*", normalized)
            or re.search(r"\bгазов\w*(?:\s+\w+){0,2}\s+не\s+нуж\w*", normalized)
            or re.search(r"\bне\s+нуж\w*(?:\s+\w+){0,2}\s+газов\w*", normalized)
            or "газа нет" in normalized
            or "без газа" in normalized
        )
        has_gas = "газов" in normalized or rejects_gas
        has_electric = "электр" in normalized
        if rejects_electric and not rejects_gas:
            return "газовый" if has_gas else None
        if rejects_gas and not rejects_electric:
            return "электрический"
        if has_gas and not has_electric:
            return "газовый"
        if has_electric and not has_gas:
            return "электрический"
        return None

    @staticmethod
    def _reconcile_builtin_constraints(
        intent: IntentResult,
        session: SessionState,
    ) -> None:
        """Make a current positive/negative refinement override stale context."""
        required = {
            normalize_text(str(part))
            for part in intent.slots.get("required_builtin_parts") or []
            if part
        }
        excluded = {
            normalize_text(str(part))
            for part in intent.slots.get("excluded_builtin_parts") or []
            if part
        }
        if excluded:
            intent.slots["required_builtin_parts"] = [
                part for part in required if part not in excluded
            ]
            previous_required = {
                normalize_text(str(part))
                for part in session.slots.get("required_builtin_parts") or []
                if part
            }
            remaining = sorted(previous_required - excluded)
            if remaining:
                session.slots["required_builtin_parts"] = remaining
            else:
                session.slots.pop("required_builtin_parts", None)
        if required:
            intent.slots["excluded_builtin_parts"] = [
                part for part in excluded if part not in required
            ]
            previous_excluded = {
                normalize_text(str(part))
                for part in session.slots.get("excluded_builtin_parts") or []
                if part
            }
            remaining = sorted(previous_excluded - required)
            if remaining:
                session.slots["excluded_builtin_parts"] = remaining
            else:
                session.slots.pop("excluded_builtin_parts", None)

    def _ground_builtin_boiler_refinement(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> None:
        """Keep a built-in-component refinement attached to a boiler search."""
        if (
            session.category != "boilers"
            and session.pending_category != "boilers"
        ):
            return
        text = normalize_text(message)
        if not IntentRouterAgent._is_builtin_selection_constraint(text, "boilers"):
            return
        grounded_slots: dict[str, Any] = {}
        self.intent_router._extract_slots(text, "boilers", grounded_slots)
        if not any(
            grounded_slots.get(key)
            for key in ["required_builtin_parts", "excluded_builtin_parts"]
        ):
            return
        intent.category = "boilers"
        intent.intent_type = "attribute_request"
        intent.is_topic_change = False
        intent.slots.update(grounded_slots)
        for pump_only in [
            "product_kind",
            "pump_type",
            "pump_use",
            "pump_context",
        ]:
            intent.slots.pop(pump_only, None)

    @staticmethod
    def _is_explicit_pump_selection_or_correction(text: str) -> bool:
        if not any(marker in text for marker in ["насос", "циркуляц", "помпа"]):
            return False
        if any(
            marker in text
            for marker in [
                "не котел а насос",
                "не котел, а насос",
                "спрашивал про насос",
                "спрашивала про насос",
                "нужен насос",
                "нужен циркуляционный",
                "нужна помпа",
                "подбери насос",
                "подберите насос",
                "ищу насос",
            ]
        ):
            return True
        return bool(
            "циркуляционный насос" in text
            and not any(
                marker in text
                for marker in [
                    "есть ли",
                    "входит",
                    "встроен",
                    "в комплект",
                    "у этого котла",
                    "в этом котле",
                ]
            )
        )

    @staticmethod
    def _is_explicit_non_pump_topic_change(text: str) -> bool:
        target = any(
            marker in text
            for marker in [
                "котел",
                "бойлер",
                "водонагрев",
                "труб",
                "радиатор",
                "кран",
                "канализац",
                "фитинг",
                "арматур",
            ]
        )
        explicit_switch = any(
            marker in text
            for marker in [
                "теперь",
                "перейдем",
                "больше не нужен",
                "нужен ",
                "нужна ",
                "нужно ",
                "подбери",
                "подберите",
                "ищу ",
                "спрашиваю про",
                "спрашивал про",
                "спрашивала про",
                "не насос",
                "вернемся",
                "вернёмся",
                "как обсуждали",
                "к предыдущ",
            ]
        )
        return bool(target and explicit_switch)

    @staticmethod
    def _pending_slot_keys_for_question(
        question: str,
        category: str | None,
    ) -> list[str]:
        text = normalize_text(question)
        keys: list[str] = []
        if category == "pumps":
            if any(marker in text for marker in ["для какой задач", "назначен", "отоплен"]):
                keys.extend(["pump_use", "pump_type"])
            if "монтажн" in text:
                keys.append("mounting_length_mm")
            if "напор" in text:
                keys.extend(["head_m", "required_head_m"])
            if "расход" in text or "производительност" in text:
                keys.append("required_flow_m3_h")
            if "присоедин" in text:
                keys.append("connection_size")
            if "динамическ" in text:
                keys.append("dynamic_water_level_m")
            if "статическ" in text:
                keys.append("static_water_level_m")
            if "давлен" in text:
                keys.extend(["inlet_pressure_bar", "required_pressure_bar"])
            if "горизонтальн" in text or "трасс" in text:
                keys.append("horizontal_run_m")
        if category == "pipes":
            if "для чего" in text or "назначен" in text:
                keys.append("pipe_purpose")
            if any(marker in text for marker in ["участ", "петл", "магистрал", "обвяз"]):
                keys.append("pipe_service")
            if "холодн" in text and "горяч" in text:
                keys.append("water_temperature")
            if "температур" in text:
                keys.append("operating_temperature_c")
            if "давлен" in text:
                keys.append("operating_pressure_bar")
            if "диаметр" in text:
                keys.append("diameter_mm")
            if "материал" in text or "ppr" in text or "pex" in text:
                keys.append("pipe_material")
        if category == "boilers":
            if "газов" in text and "электр" in text:
                keys.append("boiler_type")
            if "площад" in text:
                keys.append("area_m2")
            if "220" in text and "380" in text:
                keys.append("voltage_v")
        if category == "water_heaters":
            if "объ" in text or "литр" in text:
                keys.append("volume_l")
            if "накоп" in text or "проточ" in text:
                keys.append("heater_type")
            if any(marker in text for marker in ["электр", "газ", "косвен", "источник"]):
                keys.append("energy_source")
            if "монтаж" in text or "настенн" in text or "напольн" in text:
                keys.append("mounting")
            if "вертик" in text or "горизонт" in text:
                keys.append("orientation")
        return list(dict.fromkeys(keys))

    def _is_pending_continuation(
        self,
        intent: IntentResult,
        session: SessionState,
        message: str,
    ) -> bool:
        if not session.pending_question and not session.pending_complectation_parts:
            return False
        text = normalize_text(message)
        if session.pending_category == "pumps" and not intent.is_topic_change:
            return True
        if (
            session.pending_category == "pipes"
            and intent.category in {"pipes", "other"}
            and not intent.is_topic_change
        ):
            return True
        if (
            session.pending_category == "boilers"
            and not intent.is_topic_change
            and any(marker in text for marker in ["электр", "газ", "220", "380"])
        ):
            return True
        if (
            session.pending_category == "water_heaters"
            and not intent.is_topic_change
            and any(
                marker in text
                for marker in [
                    "электр",
                    "газ",
                    "косвен",
                    "накоп",
                    "проточ",
                    "литр",
                    "настенн",
                    "напольн",
                    "вертик",
                    "горизонт",
                ]
            )
        ):
            return True
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
        if session.pending_category == "pumps" and not intent.is_topic_change:
            intent.category = session.pending_category
        elif (
            session.pending_category == "pipes"
            and intent.category in {"pipes", "other"}
            and not intent.is_topic_change
        ):
            intent.category = session.pending_category
        elif session.pending_category == "boilers" and not intent.is_topic_change:
            intent.category = session.pending_category
        elif session.pending_category == "water_heaters" and not intent.is_topic_change:
            intent.category = session.pending_category
        elif session.category and intent.category == "other":
            intent.category = session.category
        if session.pending_complectation_parts:
            intent.intent_type = "complectation"
        elif session.pending_intent_type and intent.intent_type in {"small_talk", "unknown"}:
            intent.intent_type = session.pending_intent_type
        intent.is_topic_change = False

    def _maybe_focused_stock_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        """Answer quantity questions about one item from the shown comparison."""
        if intent.intent_type != "stock_request" or not session.last_products:
            return None
        if session.slots.get("pump_accessory_sku") and session.category == "valves":
            return None
        text = normalize_text(message)
        # This shortcut is only for a *pure* follow-up about a card that has
        # already been shown.  A stock phrase may be appended to new or
        # corrected product requirements (for example: "не проточный,
        # накопительный 80 л, только в наличии").  In that case the normal
        # search pipeline must apply the new hard constraints instead of
        # reporting the stock of the previous card.
        stock_only_slots = {"in_stock", "cheap", "sort_mode"}
        if set(intent.slots) - stock_only_slots:
            return None
        if self._wants_choose_one(message) and "в наличии" in text:
            # This is a selection constraint, not a question about the stock of
            # the currently displayed (possibly unavailable) card.
            return None
        selected: ProductCard | None = None
        if any(marker in text for marker in ["самого дешев", "самый дешев", "дешевле всех"]):
            selected = min(session.last_products, key=lambda card: card.price)
        elif any(marker in text for marker in ["самого дорог", "самый дорог", "дороже всех"]):
            selected = max(session.last_products, key=lambda card: card.price)
        else:
            index = self._select_ordinal_index(message, session.last_products)
            if index is not None:
                selected = session.last_products[index]
            elif len(session.last_products) == 1 and any(
                marker in text
                for marker in [
                    "у него",
                    "у неё",
                    "у нее",
                    "этого",
                    "этой",
                    "сколько осталось",
                    "только в налич",
                    "а в налич",
                    "он в налич",
                    "она в налич",
                    "есть ли",
                ]
            ):
                selected = session.last_products[0]
        if selected is None:
            return None
        strict_in_stock = "только в налич" in text
        if strict_in_stock:
            # This turn is handled before the normal slot merge, therefore the
            # filter must be made durable explicitly.
            session.slots["in_stock"] = True
        if selected.stock_qty is not None:
            stock = (
                f"в наличии {selected.stock_qty} шт."
                if selected.stock_qty > 0
                else "сейчас нет в наличии (0 шт.)"
            )
        else:
            stock = f"статус наличия: {selected.stock_status}"
        answer = (
            f"Самый дешёвый из показанных — {selected.name}, артикул {selected.sku}, "
            f"цена {selected.price:g} {selected.currency}; {stock}"
            if "дешев" in text
            else f"По товару {selected.sku} ({selected.name}): {stock}"
        )
        if strict_in_stock and not self._card_is_in_stock(selected):
            answer += (
                " По фильтру «только в наличии» карточку не показываю. "
                "Если хотите, покажу только совместимые аналоги с подтверждённым остатком."
            )
            return answer, []
        return answer, [selected]

    def _maybe_shown_category_price_answer(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> tuple[str, list[ProductCard]] | None:
        """Price a previously selected project component without fresh LLM retrieval."""
        if not session.last_products or intent.category == "other":
            return None
        if self._wants_choose_one(message):
            return None
        text = normalize_text(message)
        if not any(
            marker in text
            for marker in ["цен", "стоит", "стоимост", "сколько выйдет", "по деньгам"]
        ):
            return None
        cards: list[ProductCard] = []
        for card in session.last_products:
            product = self._find_product_by_sku(card.sku)
            if product and self.search_agent.canonical_category(product) == intent.category:
                cards.append(card)
        if not cards:
            return None
        cards.sort(key=lambda card: card.price)
        label = PROJECT_CATEGORY_LABELS.get(intent.category, "Товар").lower()
        lines = [f"По уже показанной подборке цены на {label}:"]
        for card in cards[:3]:
            lines.append(
                f"- {card.name}, арт. {card.sku}: {card.price:g} {card.currency}; "
                f"{self._card_stock_text(card)}."
            )
        if any(marker in text for marker in ["посовет", "рекоменд", "что взять"]):
            cheapest = cards[0]
            lines.append(
                f"Если выбирать из этих вариантов прежде всего по цене, начните с "
                f"{cheapest.sku}; окончательную пригодность нужно сверить по параметрам системы."
            )
        lines.append("Монтаж, доставка и дополнительные комплектующие в эту цену не входят.")
        return "\n".join(lines), cards[:3]

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
            "volume_l",
            "heater_type",
            "energy_source",
            "mounting",
            "orientation",
            "pump_type",
            "element_type",
            "sewer_scope",
        }
        if specific_keys.intersection(intent.slots):
            return False
        return True

    def _stock_clarification_question(self, intent: IntentResult) -> str:
        if intent.category == "pumps":
            return (
                "Какой насос нужен и для какой задачи? Укажите тип (циркуляционный, "
                "скважинный, дренажный, поверхностный/станция) или ключевые параметры: "
                "напор, присоединение и монтажную длину либо источник воды. После этого "
                "проверю наличие подходящих моделей."
            )
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
        session.slots["power_kw"] = power_kw
        session.slots["area_m2"] = area_m2
        session.category = "boilers"
        required = area_m2 / 10.0
        if power_kw + 0.4 >= required:
            return None
        return (
            f"{power_kw:g} кВт на {area_m2:g} м² скорее не хватит: по эмпирическому правилу "
            f"нужно около {required:g} кВт (10 м² на 1 кВт), а с учётом утепления и горячей воды — "
            "обычно с запасом. Не буду подтверждать, что хватит. Если хотите, могу подобрать "
            "котёл с подходящей мощностью — уточните тип (газ/электр) и питание."
        )

    def _maybe_boiler_power_followup(
        self,
        message: str,
        session: SessionState,
    ) -> str | None:
        """Explain a challenge to the immediately preceding sizing warning.

        Short replies such as ``точно? раньше ты советовал 12`` contain no
        category word, but they still refer to the stored 6 kW / 100 m² sizing
        context.  Losing that context produced a generic greeting and looked
        like a contradiction to the customer.
        """
        if session.category != "boilers":
            return None
        power_kw = self._float_slot(session.slots.get("power_kw"))
        area_m2 = self._float_slot(session.slots.get("area_m2"))
        if not power_kw or not area_m2:
            return None
        text = normalize_text(message)
        if not any(marker in text for marker in ["точн", "раньше", "советов", "почему"]):
            return None
        required = area_m2 / 10.0
        if power_kw + 0.4 >= required:
            return None
        reserve = max(required * 1.2, required)
        return (
            f"Да, позиция та же: {power_kw:g} кВт на {area_m2:g} м² недостаточно. "
            f"{required:g} кВт — только предварительный ориентир по правилу 1 кВт на 10 м², "
            f"а вариант около {reserve:g} кВт мог быть предложен как запас на теплопотери и ГВС. "
            "Это не означает, что больший котёл автоматически лучше: окончательную мощность "
            "проверяют расчётом теплопотерь и по минимальной модуляции модели."
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
        if "плох" in normalize_text(insulation):
            conclusion = (
                "При плохом утеплении 15 кВт может быть оправданнее, но это нужно подтвердить "
                "расчётом теплопотерь и проверить минимальную мощность котла."
            )
        else:
            conclusion = (
                "При обычном утеплении для 100 м² разумнее начать проверку с 12 кВт; "
                "15 кВт рассматривайте при повышенных теплопотерях или заметной нагрузке ГВС."
            )
        return (
            f"{conclusion} Оба варианта выше базового ориентира около 10 кВт, поэтому "
            "15 кВт нельзя автоматически считать лучше: запас нужно соотнести с минимальной "
            "мощностью, тактованием и ГВС. "
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
        area = self._float_slot(intent.slots.get("area_m2")) or self._float_slot(
            session.slots.get("area_m2")
        )
        if area is None:
            area = self._first_number(text, [r"(\d{2,4})\s*(?:м2|м²|квадрат|метр)"])
        base_note = (
            f" Для {area:g} м² базовый ориентир — около {area / 10:g} кВт."
            if area
            else ""
        )
        return (
            f"{low:g} и {high:g} кВт — не равнозначные варианты.{base_note} "
            f"Оба могут иметь запас, а {high:g} кВт не автоматически лучше: выбор зависит от "
            "теплопотерь, минимальной мощности, числа контуров и нагрузки ГВС. "
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
        self.composer.last_llm_output_accepted = False
        self.composer.last_llm_rejection_reason = "; ".join(guard.issues)
        return guard.safe_message or draft

    def _should_restart_category_context(
        self,
        message: str,
        intent: IntentResult,
        session: SessionState,
    ) -> bool:
        # A purpose question about a shown card is not a fresh category search,
        # even if the router sees the product noun ("кран", "труба") and emits
        # broad_category.  Preserve the card so the grounded follow-up handler
        # can answer from its feed attributes.
        if session.last_products and self._asks_shown_product_purpose(
            normalize_text(message)
        ):
            return False
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
            "water_heaters": ["водонагрев", "бойлер", "накопительн", "проточн"],
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
                "литр",
                "накоп",
                "проточ",
                "косвен",
                "настенн",
                "напольн",
                "вертик",
                "горизонт",
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
                "цен",
                "стоит",
                "стоимост",
                "сколько выйдет",
                "по деньгам",
                "посовет",
            ]
        )

    def _looks_like_card_question(self, message: str, session: SessionState) -> bool:
        """True for questions about an already shown product's card / documentation."""
        if not session.last_products:
            return False
        text = normalize_text(message)
        if IntentRouterAgent._is_builtin_selection_constraint(
            text,
            session.category or "boilers",
        ):
            # This refines the selection rather than asking for a fact about
            # one of the cards that happen to be on screen.
            return False
        if self._is_explicit_pump_selection_or_correction(text):
            return False
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
            "уже внутри",
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
        presence_verbs = [
            "есть",
            "входит",
            "идет",
            "идёт",
            "имеется",
            "включен",
            "включён",
            "встроен",
            "встроён",
            "внутри",
        ]
        if (
            any(part in text for part in part_words)
            and any(verb in text for verb in presence_verbs)
            and not any(stop in text for stop in ["в наличии", "на складе", "сколько"])
        ):
            return True
        return False

    def _looks_like_unresolved_complectation_question(
        self,
        message: str,
        session: SessionState,
    ) -> bool:
        """Detect a part-presence question even if the LLM routes it as a project."""
        if session.last_products:
            return False
        text = normalize_text(message)
        if self._is_explicit_pump_selection_or_correction(text):
            return False
        if IntentRouterAgent._is_builtin_selection_constraint(text, "boilers"):
            return False
        if not self._requested_parts(message):
            return False
        presence_markers = [
            "есть",
            "входит",
            "идет",
            "идёт",
            "имеется",
            "включен",
            "включён",
            "встроен",
            "комплектац",
            "в комплект",
            "обвяз",
        ]
        product_reference = any(
            marker in text
            for marker in ["этого", "этот", "котел", "котёл", "товар", "модель"]
        ) or bool(re.search(r"\b(?:его|ее|её)\b", text))
        return product_reference and any(marker in text for marker in presence_markers)

    def _part_question_about_shown_products(
        self,
        message: str,
        session: SessionState,
    ) -> list[str]:
        """Resolve «а тут в каких он добавлен?» against our own previous reply.

        After a companion hint («у настенных котлов насос часто уже встроен»)
        the customer refers to that узел by a pronoun. Nothing linked the
        pronoun to the part, so a substantive question about the shown products
        fell through to the small-talk reply («Я на связи…»). We resolve the
        part from the last assistant message and answer it as a complectation
        question, strictly from the cards.

        Returns the parts to check, or [] when this is not such a question.
        """
        if not session.last_products:
            return []
        text = normalize_text(message)
        # Наличие на складе и цена — не про комплектацию.
        if any(marker in text for marker in ["налич", "цена", "цену", "стоит", "сколько"]):
            return []
        presence_markers = [
            "есть",
            "входит",
            "идет",
            "идёт",
            "имеется",
            "включен",
            "включён",
            "встроен",
            "добавлен",
            "комплектац",
            "в комплект",
        ]
        if not any(marker in text for marker in presence_markers):
            return []
        # Если узел назван прямо («что в него входит», «есть ли насос») — этим уже
        # занимаются существующие обработчики, и они отвечают полнее (например,
        # заодно объясняют назначение товара). Здесь закрываем только пробел:
        # узел назван местоимением и берётся из предыдущей реплики.
        if self._requested_parts(message):
            return []
        if not re.search(r"\b(?:он|его|она|ее|её|оно|они|их)\b", text):
            return []
        for entry in reversed(session.history):
            if entry.get("role") != "assistant":
                continue
            # Только непосредственно предыдущая реплика: местоимение ссылается
            # на неё, а не на произвольный узел из середины диалога.
            return self._requested_parts(entry.get("content", ""))[:1]
        return []

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
        if re.search(r"(?:трех|трёх|3)[- ]?ходов\w*\s+клапан", text):
            parts.append("3-ходовой клапан")
        elif "клапан" in text:
            parts.append("клапан")
        if "манометр" in text:
            parts.append("манометр")
        if "групп" in text and "безопас" in text:
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

    def _drop_self_referential_parts(
        self, requested_parts: list[str], product: Product
    ) -> list[str]:
        """Remove component keywords that just name the target product itself.

        «Что входит в комплект поставки этого насоса?» matches keyword «насос»
        the same way as «есть ли в котле насос?» does, but here «насос» names
        the product being asked about, not a component to confirm inside it —
        checking «is a pump built into this pump» is meaningless and produced
        a confusing decline. Only strip a keyword when it is part of the
        product's own name/category, so genuine component checks («а бак
        входит?» about a boiler) are unaffected.
        """
        haystack = normalize_text(f"{product.name} {product.category_path}")
        return [part for part in requested_parts if normalize_text(part) not in haystack]

    @staticmethod
    def _asks_about_all_shown(message: str) -> bool:
        """«какие из них», «у всех ли», «в каких есть» — вопрос обо всех карточках."""
        text = normalize_text(message)
        return any(
            marker in text
            for marker in [
                "какие",
                "в каких",
                "у каких",
                "у всех",
                "во всех",
                "все ли",
                "каждый",
                "у обоих",
                "из предложенных",
                "из показанных",
            ]
        )

    def _compose_builtin_part_overview(
        self,
        cards: list[ProductCard],
        requested_parts: list[str],
    ) -> str:
        """Per-card verdict for a part, strictly from what the cards confirm."""
        parts = [part for part in requested_parts if part != "комплектация"] or ["комплектация"]
        part_label = ", ".join(parts)
        lines = [f"Проверил по карточкам показанных моделей ({part_label}):"]
        for card in cards:
            product = self._find_product_by_sku(card.sku)
            components = self.guardrails.list_builtin_components(product) if product else []
            confirmed = [
                part
                for part in parts
                if any(part in component for component in components)
            ]
            if confirmed:
                lines.append(f"- {card.sku} — {card.name}: да, подтверждено — {', '.join(confirmed)}.")
            else:
                lines.append(
                    f"- {card.sku} — {card.name}: в карточке подтверждения нет; "
                    "не утверждаю ни наличие, ни отсутствие."
                )
        lines.append(
            "Это данные карточек и привязанных паспортов. Где подтверждения нет, "
            "точную комплектацию уточнит менеджер."
        )
        return "\n".join(lines)

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
        in_stock_only = bool(
            intent.flags.get("in_stock") or intent.slots.get("in_stock")
        )
        if in_stock_only and cards:
            available_cards = [
                card for card in cards if self._card_is_in_stock(card)
            ]
            if len(available_cards) != len(cards):
                removed_count = len(cards) - len(available_cards)
                cards = available_cards
                session.last_products = available_cards
                if available_cards:
                    lines = [
                        "После финальной проверки оставил только позиции с "
                        "подтверждённым положительным остатком:"
                    ]
                    for card in available_cards:
                        lines.append(
                            f"- {card.sku} — {html.unescape(card.name)}; "
                            f"{card.price:g} {card.currency}; "
                            f"{self._card_stock_text(card)}."
                        )
                    lines.append(
                        f"Исключено карточек без подтверждённого остатка: {removed_count}."
                    )
                    answer = "\n".join(lines)
                else:
                    answer = (
                        "По финальной проверке ни у одной найденной позиции нет "
                        "подтверждённого положительного остатка. По фильтру "
                        "«только в наличии» карточки не показываю."
                    )
                if (
                    session.history
                    and session.history[-1].get("role") == "assistant"
                ):
                    session.history[-1]["content"] = answer
                if "GuardrailsAgent" not in agents_used:
                    agents_used.append("GuardrailsAgent")

        intent_requested = bool((intent.raw or {}).get("llm_requested"))
        intent_output_accepted = bool((intent.raw or {}).get("llm_output_accepted"))
        response_requested = bool(getattr(self.composer, "last_llm_requested", False))
        response_transport = bool(getattr(self.composer, "last_llm_used", False))
        response_accepted = bool(
            getattr(self.composer, "last_llm_output_accepted", False)
        )
        consultant_requested = bool(
            getattr(self.consultant, "last_llm_requested", False)
        )
        consultant_transport = bool(getattr(self.consultant, "last_llm_used", False))
        consultant_accepted = bool(
            getattr(self.consultant, "last_llm_output_accepted", False)
        )
        transport_succeeded = bool(
            intent.llm_used or response_transport or consultant_transport
        )
        output_accepted = bool(response_accepted or consultant_accepted)
        if consultant_accepted and "ConsultantAgent" in agents_used:
            final_answer_source = "consultant_llm"
        elif response_accepted and "ResponseComposerAgent" in agents_used:
            final_answer_source = "response_llm"
        else:
            final_answer_source = "deterministic"
        rejection_reasons = [
            reason
            for reason in [
                (intent.raw or {}).get("llm_rejection_reason"),
                getattr(self.composer, "last_llm_rejection_reason", None),
                getattr(self.consultant, "last_llm_rejection_reason", None),
            ]
            if reason
        ]
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
            handoff_status=session.handoff_status,
            handoff_ticket_id=session.handoff_ticket_id,
            debug={
                "intent": intent.intent_type,
                "category": intent.category,
                "slots": session.slots,
                "project_context": session.project_context,
                "agents_used": agents_used,
                "llm_used": transport_succeeded,
                "llm_requested": intent_requested
                or response_requested
                or consultant_requested,
                "llm_transport_succeeded": transport_succeeded,
                "llm_output_accepted": output_accepted,
                "final_answer_source": final_answer_source,
                "llm_rejection_reason": "; ".join(rejection_reasons) or None,
                "intent_llm_used": intent.llm_used,
                "intent_llm_requested": intent_requested,
                "intent_llm_output_accepted": intent_output_accepted,
                "intent_llm_rejection_reason": (intent.raw or {}).get(
                    "llm_rejection_reason"
                ),
                "response_llm_used": response_transport,
                "response_llm_requested": response_requested,
                "response_llm_output_accepted": response_accepted,
                "response_llm_rejection_reason": getattr(
                    self.composer,
                    "last_llm_rejection_reason",
                    None,
                ),
                "response_llm_fallback_reason": self.composer.last_llm_fallback_reason,
                "consultant_llm_used": consultant_transport,
                "consultant_llm_requested": consultant_requested,
                "consultant_llm_output_accepted": consultant_accepted,
                "consultant_llm_rejection_reason": getattr(
                    self.consultant,
                    "last_llm_rejection_reason",
                    None,
                ),
                "consultant_llm_fallback_reason": self.consultant.last_fallback_reason,
                "any_llm_used": transport_succeeded,
                "topic_changed": session.topic_changed,
                "handoff_status": session.handoff_status,
                "handoff_ticket_id": session.handoff_ticket_id,
                "products_loaded_from": self.products_loaded_from,
            },
        )
