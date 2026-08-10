from __future__ import annotations

import json
import logging
import re
from threading import RLock
from typing import Any

from app.models import IntentResult, SessionState
from app.openrouter_client import OpenRouterClient

from .engineering_notation import (
    category_hint as engineering_category_hint,
    extract_contextual_short_answer,
    extract_engineering_notation,
)
from .numeric_semantics import (
    extract_piece_length_mm,
    extract_temperature_c,
    extract_total_length_m,
    numeric_slot_has_compatible_context,
    numeric_span_has_incompatible_context,
    numeric_span_has_incompatible_unit,
)
from .utils import collapse_sku_spaces, normalize_sku, normalize_text


logger = logging.getLogger(__name__)


SKU_RE = re.compile(r"\b[а-яa-z]{1,8}[а-яa-z0-9]*[.\-][а-яa-z0-9.\-]{2,}\b", re.IGNORECASE)
NUMERIC_SKU_RE = re.compile(r"\b\d{5,}\b")
# Some vendors publish compact SKUs without separators (for example,
# ``CMSR02CA28``).  Requiring at least two letters and two digits keeps the
# matcher from treating ordinary model/brand words such as ``Arderia9`` as an
# exact article request.
ALPHANUM_SKU_RE = re.compile(
    r"\b(?=[a-z0-9]{8,}\b)(?=(?:[a-z0-9]*\d){2})(?=(?:[a-z0-9]*[a-z]){2})"
    r"[a-z0-9]+\b",
    re.IGNORECASE,
)
# Артикулы вида 68/2/8: минимум два слэша, чтобы не путать с размерами 1/2 и параметрами 25/6.
SLASH_SKU_RE = re.compile(r"\b\d{1,4}/\d{1,4}/\d{1,4}\b")
OLD_CIRCULATION_PUMP_RE = re.compile(
    r"\b(?:(grundfos|wilo|valtec|unipump|stout)\s*)?"
    r"((?:ups|up[cс]|up|alpha|star\s*rs|rs)\s*)"
    r"(\d{2})\s*[-/ ]\s*(\d{1,2}|[468]0)"
    r"(?:\s*[-/ ]\s*(130|180))?\b",
    re.IGNORECASE,
)
PUMP_PARAMS_RE = re.compile(
    r"(?<!\d)(25|32|40|50)\s*[-/]\s*(\d{1,2})(?:[\s,/-]+(130|180))?(?!\d)"
)
INCH_SIZE_RE = re.compile(
    r"(?<!\d)(1\s+1\s*/\s*4|1\s*/\s*2|3\s*/\s*4|3\s*/\s*8|1\s*/\s*4)(?!\d)"
)
INTEGER_INCH_RE = re.compile(r"(?<![\d/])([12])\s*(?:\"|дюйм(?:а|ов)?)(?!\w)")
TROUBLE_SHOOTING_PUMP_HINTS = [
    "хватит",
    "хватает",
    "достаточн",
    "не хватает",
]

BRANDS = [
    "VALTEC",
    "OSTENDORF",
    "ARDERIA",
    "E.C.A",
    "ECA",
    "GRUNDFOS",
    "WILO",
    "STOUT",
    "ROMMER",
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "hydraulic_accumulators": [
        "гидроаккумулятор",
        "гидробак",
        "мембранный бак",
        "мембранная емкост",
        "мембранная ёмкост",
        "ресивер для насос",
        "защита насоса от частых включений",
        "поддержание давления",
    ],
    "water_heaters": [
        "водонагрев",
        "водогрей",
        "бойлер",
        "бкн",
        "эвн",
        "газовая колонк",
        "газовый колонк",
    ],
    "filters": [
        "фильтр для воды",
        "фильтр вод",
        "картридж",
        "водоочист",
        "водоподготов",
        "обратный осмос",
    ],
    "controls": [
        "комнатный термостат",
        "терморегулятор",
        "сервопривод",
        "контроллер отоплен",
        "привод клапан",
    ],
    "sewer": ["канализац", "канализация", "htem", "ostendorf", "отвод", "тройник", "муфта", "ревизия"],
    "pumps": [
        "насос",
        "помпа",
        "циркуляц",
        "повысит",
        "дренаж",
        "откач",
        "скважин",
        "нсос",
    ],
    "boilers": [
        "котел",
        "котёл",
        "котл",
        "кател",
        "boiler",
        "газовый",
        "квт",
        "квадрат",
    ],
    "valves": ["кран", "шаровый", "вентиль", "клапан", "американк"],
    "radiator_fittings": ["термоголов", "термостатическ", "термост-ий", "клапан термост", "радиаторный клапан", "для рад", "для батаре", "д/рад"],
    "radiators": ["радиатор", "радиаторы", "батаре", "биметалл", "алюминиевый радиатор"],
    "fittings": ["фитинг", "угольник ppr", "муфта ppr", "тройник ppr", "переходник ppr"],
    # Stem form also covers ordinary inflections: ``трубу``, ``трубой``.
    "pipes": [
        "труб",
        "ppr",
        "ппр",
        "полипропилен",
        "pe-rt",
        "pert",
        "пе-рт",
        "pex",
        "pe-x",
        "сшитый полиэтилен",
        "сшитого полиэтилена",
        "металлопласт",
        "пнд",
        "пэ100",
        "pe100",
        "hdpe",
    ],
}

SYMPTOM_KEYWORDS = [
    "вода не ид",
    "вода шла",
    "не течет",
    "слабый напор",
    "низкий напор",
    "слабо течет",
    "плохой напор",
    "плохо ид",
    "плохо течет",
    "давления нет",
]

SMALL_TALK = [
    "как дела",
    "как тебя зовут",
    "кто ты",
    "ты кто",
    "как к тебе обращаться",
    "что ты умеешь",
    "помоги",
    "привет",
    "здравств",
    "добрый день",
    "ты красивая",
    "ты классный",
    "ты красивый",
    "спасибо",
    "к делу",
    "давай начнем",
    "давай начнём",
    "погнали",
    "поехали",
]

OUT_OF_SCOPE = [
    "погода",
    "курс валют",
    "политика",
    "анекдот",
    "напиши стих",
]

CHEAP_WORDS = ["подешев", "дешев", "недорог", "бюджет"]
STOCK_WORDS = [
    "в наличии",
    "из наличия",
    "из того что есть",
    "из того, что есть",
    "есть на складе",
    "наличие",
    "сколько есть",
    "есть 2",
    "можно забрать",
    "забрать сегодня",
    "забрать прямо сейчас",
    "самовывоз",
]
ALLOW_UNAVAILABLE_PATTERNS = [
    r"\b(?:покажи|покажите|подбери|подберите|давай)\b[^.!?]{0,35}"
    r"\b(?:даже\s+)?(?:если\s+)?(?:сейчас\s+)?нет\b",
    r"\b(?:можно|покажи|покажите)\b[^.!?]{0,30}\b(?:не\s+в\s+наличии|под\s+заказ)\b",
    r"\b(?:наличие|остаток|склад)\b[^.!?]{0,20}\bне\s+важн\w*\b",
    r"\bне\s+обязательно\b[^.!?]{0,20}\b(?:в\s+наличии|на\s+складе)\b",
    r"\b(?:можно|покажи|покажите|подбери|подберите|давай)\b[^.!?]{0,30}"
    r"\b(?:те|товар\w*|вариант\w*)\b,?\s+(?:котор\w*\s+)?"
    r"(?:сейчас\s+)?нет\b",
    r"\b(?:отсутствующ\w*|товар\w*\s+без\s+остатк\w*)\b[^.!?]{0,18}"
    r"\b(?:тоже|можно|покажи|покажите)\b",
]
CHOOSE_ONE_WORDS = [
    "выбери один",
    "назови один",
    "выбери сама",
    "выбери сам",
    "что взять",
    "какой лучше",
    "какой выбрать",
    "посоветуй один",
    "оставь один",
    "один вариант",
]
COMPLECTATION_WORDS = [
    "есть насос",
    "есть бак",
    "комплектац",
    "в комплект",
    "входит насос",
    "входит бак",
    "входит ли",
    "не входит",
    "что входит",
    "входит группа",
    "входит бойлер",
    "встроен",
    "обвяз",
    "групп безопас",
    "группу безопас",
    "группа безопас",
]
LINK_WORDS = [
    "дай ссылку",
    "дай ссылки",
    "ссылку",
    "ссылка",
    "ссылки",
    "ссылок",
]
TOPIC_CHANGE_WORDS = ["теперь", "а теперь", "еще нужен", "ещё нужен", "другой", "нужен"]

VALID_INTENTS = {
    "exact_sku",
    "brand_category",
    "broad_category",
    "cheap_request",
    "stock_request",
    "attribute_request",
    "complectation",
    "small_talk",
    "out_of_scope",
    "unknown",
}
VALID_CATEGORIES = {
    "pipes",
    "pumps",
    "boilers",
    "water_heaters",
    "hydraulic_accumulators",
    "filters",
    "controls",
    "valves",
    "sewer",
    "radiator_fittings",
    "radiators",
    "fittings",
    "other",
}


class IntentRouterAgent:
    def __init__(
        self,
        llm_client: OpenRouterClient | None = None,
        catalog_brands: list[str] | None = None,
    ) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        self._cache: dict[str, IntentResult] = {}
        self._cache_lock = RLock()
        self._catalog_brands: list[str] = []
        self.set_catalog_brands(catalog_brands or [])

    @staticmethod
    def _household_count(token: str) -> float | None:
        normalized = normalize_text(token).strip()
        if re.fullmatch(r"\d{1,3}(?:[,.]\d+)?", normalized):
            return float(normalized.replace(",", "."))
        stems = {
            "од": 1.0,
            "дв": 2.0,
            "тр": 3.0,
            "четыр": 4.0,
            "пят": 5.0,
            "шест": 6.0,
            "сем": 7.0,
            "восем": 8.0,
            "девят": 9.0,
            "десят": 10.0,
        }
        for stem, value in stems.items():
            if normalized.startswith(stem):
                return value
        return None

    def set_catalog_brands(self, brands: list[str]) -> None:
        by_normalized: dict[str, str] = {}
        for brand in [*BRANDS, *brands]:
            normalized = normalize_text(brand)
            if len(normalized) < 2:
                continue
            by_normalized.setdefault(normalized, str(brand).strip())
        self._catalog_brands = sorted(
            by_normalized.values(),
            key=lambda value: len(normalize_text(value)),
            reverse=True,
        )
        with self._cache_lock:
            self._cache.clear()

    def route(self, message: str, session: SessionState | None = None) -> IntentResult:
        normalized_message = normalize_text(message)
        if session:
            # The LLM sees recent history and the rule router consumes pending
            # state/slots.  All of that must be part of the key; a high-level
            # category-only key reused a gas follow-up in an electric session.
            context_key = json.dumps(
                {
                    "session_id": session.session_id,
                    "category": session.category,
                    "last_intent": session.last_intent,
                    "pending_intent_type": session.pending_intent_type,
                    "pending_question": session.pending_question,
                    "pending_complectation_parts": session.pending_complectation_parts,
                    "slots": session.slots,
                    "project_context": session.project_context,
                    "last_product_skus": [card.sku for card in session.last_products],
                    "history": session.history[-6:],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        else:
            context_key = "none:-:-"
        cache_key = f"{context_key}:{normalized_message}"
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            result = IntentResult(**self._as_dict(cached))
            result.llm_used = False
            result.raw = dict(result.raw or {})
            result.raw.update(
                {
                    "llm_requested": False,
                    "llm_transport_succeeded": False,
                    "llm_output_accepted": False,
                    "intent_source": "cache",
                }
            )
            result.is_topic_change = self._is_topic_change(result.category, message, session)
            return result

        result = self._rule_based(message, session)
        llm_requested = False
        if result.confidence < 0.55:
            llm_requested = True
            llm_result = self._llm_fallback(message, result, session)
            llm_result = self._sanity_check_llm_intent(llm_result, result, message)
            result = llm_result
        self._normalize_result(result, message, session)
        result.raw = dict(result.raw or {})
        result.raw["llm_requested"] = llm_requested
        result.raw["llm_transport_succeeded"] = bool(result.llm_used)
        # Store a detached value: the orchestrator enriches ``intent.slots``
        # later in the request, and retaining the same object leaked those
        # session-specific mutations into another user's cached intent.
        with self._cache_lock:
            self._cache[cache_key] = IntentResult(**self._as_dict(result))
        return result

    @staticmethod
    def _allows_unavailable_stock(text: str) -> bool:
        """Return whether this turn explicitly removes a stock-only filter."""
        return any(re.search(pattern, text) for pattern in ALLOW_UNAVAILABLE_PATTERNS)

    def _rule_based(self, message: str, session: SessionState | None) -> IntentResult:
        text = normalize_text(message)
        sku_text = collapse_sku_spaces(text)
        allows_unavailable = self._allows_unavailable_stock(text)
        flags = {
            "cheap": any(word in text for word in CHEAP_WORDS),
            "in_stock": any(word in text for word in STOCK_WORDS)
            and not allows_unavailable,
            "small_talk": any(word in text for word in SMALL_TALK),
            "choose_one": any(word in text for word in CHOOSE_ONE_WORDS),
        }
        slots: dict[str, Any] = {}
        if flags["choose_one"]:
            slots["choose_one"] = True
            slots["result_limit"] = 1

        sku_match = (
            SKU_RE.search(sku_text)
            or NUMERIC_SKU_RE.search(sku_text)
            or SLASH_SKU_RE.search(sku_text)
            or ALPHANUM_SKU_RE.search(sku_text)
        )
        if (
            sku_match
            and self._is_valid_sku_candidate(sku_match.group(0))
            and not self._sku_candidate_is_measurement(
                text,
                sku_match.group(0),
            )
        ):
            slots["sku"] = sku_match.group(0)

        for brand in self._catalog_brands:
            brand_text = normalize_text(brand)
            if re.search(
                rf"(?<![a-zа-я0-9]){re.escape(brand_text)}(?![a-zа-я0-9])",
                text,
            ):
                slots["brand"] = brand
                break
        if (
            slots.get("brand")
            and any(marker in text for marker in ["как", "аналог", "замен", "дешев", "подешев"])
            and "только" not in text
            and "без аналог" not in text
        ):
            slots["reference_brand"] = slots.pop("brand")

        category, category_score = self._detect_category(text)
        if (
            category == "boilers"
            and self._has_non_negated_match(
                text,
                r"\b(?:кот[её]л|котл\w*|кател\w*)\b",
            )
            and self._has_non_negated_match(text, r"\bбойлер\w*\b")
            and not re.search(
                r"\b(?:кот[её]л|котл\w*)\b[^.!?]{0,30}"
                r"\bс\s+(?:встроенн\w*\s+)?бойлер\w*\b",
                text,
            )
        ):
            slots["boiler_water_heater_pair"] = True
        if (
            category == "other"
            and session
            and session.category == "boilers"
            and self._looks_like_boiler_type_followup(text, session)
        ):
            category = "boilers"
            category_score = 0.85
        if (
            category == "other"
            and session
            and session.category == "radiators"
            and any(
                marker in text
                for marker in ["биметалл", "алюмин", "панельн", "стальн"]
            )
        ):
            category = "radiators"
            category_score = 0.85
        if category == "other" and session and session.category and self._looks_like_attribute_followup(text):
            category = session.category
            category_score = 0.65
        if (
            session
            and session.pending_category
            and not re.search(
                r"\b(?:кот[её]л|насос|труб|радиатор|кран|бойлер|водонагрев|"
                r"гидроаккумулятор|гидробак|канализац|фитинг)\w*\b",
                text,
            )
        ):
            # Extract the answer *as the category we asked about*. Restoring
            # only after extraction loses compact replies such as ``24 л``,
            # ``м/п`` and ``присоединение 25``.
            category = session.pending_category
            category_score = max(category_score, 0.75)
        if (
            session
            and session.category == "water_heaters"
            and not re.search(r"\b(?:кот[её]л|котл\w*|кател\w*)\b", text)
            and self._looks_like_water_heater_followup(text, session)
        ):
            # Short answers to our own questions (``электрический``, ``80``,
            # ``вертикальный``) must not be reclassified as boilers or lose the
            # active product family.
            category = "water_heaters"
            category_score = max(category_score, 0.85)
        # Ответ на наш собственный вопрос наследует его рамку. Иначе слово внутри
        # ответа переклассифицирует ход, и извлекатели нужной категории вообще не
        # запускаются: «радиаторная разводка, 70 градусов, 2 бара» уезжало в
        # radiators из-за слова «радиаторн», блок pipes не выполнялся, и все три
        # названных клиентом параметра терялись. Выше тот же принцип уже зашит
        # под водонагреватели — здесь он обобщён на любую категорию.
        pending_state = getattr(session, "pending_question_state", None) if session else None
        pending_category = str(
            getattr(pending_state, "category", None)
            or (getattr(session, "pending_category", None) if session else None)
            or ""
        )
        if (
            pending_category
            and pending_category in VALID_CATEGORIES
            and pending_category != category
            and getattr(pending_state, "expected_slots", None)
            and not self._looks_like_new_request(text)
            # Если клиент прямо назвал товар другой категории («насос 25/6 180»),
            # это смена предмета, а не ответ на наш вопрос.
            and not self._names_category_noun(text, category)
        ):
            category = pending_category
            category_score = max(category_score, 0.8)

        symptom_match = any(symptom in text for symptom in SYMPTOM_KEYWORDS) or (
            "вода" in text and ("шла" in text or "иде" in text or "течет" in text)
        )
        if symptom_match:
            flags["symptom"] = True
            if category == "other":
                category = "pumps"
                category_score = max(category_score, 0.7)
            elif category == "valves" and not (
                INCH_SIZE_RE.search(text)
                or INTEGER_INCH_RE.search(text)
                or any(marker in text for marker in ["шаров", "вентил", "американк"])
            ):
                # «Вода еле течёт из крана» — кран здесь точка водоразбора, а не
                # товар: жалоба про напор, и отвечать надо про повышение давления,
                # а не спрашивать размер крана. Явный запрос крана (назван размер
                # или тип) остаётся в valves.
                category = "pumps"
                category_score = max(category_score, 0.7)
        if category == "other" and PUMP_PARAMS_RE.search(text):
            category = "pumps"
            category_score = max(category_score, 0.7)
        self._extract_slots(text, category, slots)

        if category == "boilers" and session and session.category == "boilers":
            # In an active boiler branch the adjective immediately following a
            # replacement marker identifies the requested appliance. A later
            # capability clause (``но газ у дома тоже есть``) describes the
            # site and must not turn the boiler back into a gas model.
            contextual_type = re.search(
                r"\b(?:теперь|нет\s*,?|давай|хочу|выбираю|пусть\s+будет)\s+"
                r"(электрическ\w*|газов\w*)\b",
                text,
            )
            if contextual_type:
                slots["boiler_type"] = (
                    "электрический"
                    if contextual_type.group(1).startswith("электр")
                    else "газовый"
                )

        if (
            "насос" in text
            and session
            and session.category == "boilers"
            and any(marker in text for marker in ["к нему", "для него", "к этому", "под него", "к ней"])
        ):
            category = "pumps"
            category_score = max(category_score, 0.8)
            slots["pump_type"] = "циркуляционный"
            slots["pump_use"] = "отопление"
            slots["pump_context"] = "котел"

        complectation_request = bool(
            any(word in text for word in COMPLECTATION_WORDS)
            and not self._is_bare_pump_assortment_question(text)
            and not self._is_builtin_selection_constraint(text, category)
        )
        intent_type = "unknown"
        confidence = category_score
        if (
            slots.get("sku")
            and complectation_request
        ):
            # «В котле VT.033 есть встроенный насос?» names a SKU but asks about
            # complectation, not a plain lookup — exact_sku would search that SKU
            # inside whatever category won the tie-break (e.g. pumps) and miss
            # the boiler entirely. Complectation resolves the SKU directly,
            # regardless of category, so it must win here.
            intent_type = "complectation"
            confidence = max(confidence, 0.9)
        elif slots.get("sku"):
            intent_type = "exact_sku"
            confidence = max(confidence, 0.95)
        elif any(word in text for word in LINK_WORDS):
            intent_type = "link_request"
            confidence = 0.9
        elif flags["in_stock"]:
            intent_type = "stock_request"
            confidence = max(confidence, 0.8)
        elif allows_unavailable and session and session.category:
            # This is an explicit correction of the current catalogue filter.
            # Keep it out of the low-confidence LLM fallback so a probabilistic
            # classification cannot discard the deterministic ``False`` value.
            category = session.category
            intent_type = "attribute_request"
            confidence = max(confidence, 0.9)
        elif complectation_request:
            intent_type = "complectation"
            if session and session.category:
                category = session.category
            confidence = max(confidence, 0.85)
        elif flags["cheap"] or slots.get("max_price") is not None:
            intent_type = "cheap_request"
            confidence = max(confidence, 0.75)
        elif flags.get("symptom"):
            intent_type = "broad_category"
            confidence = max(confidence, 0.75)
        elif category != "other" and self._looks_like_attribute_followup(text):
            intent_type = "attribute_request"
            confidence = max(confidence, 0.7)
        elif slots.get("brand") and category != "other":
            intent_type = "brand_category"
            confidence = max(confidence, 0.8)
        elif category != "other":
            intent_type = "broad_category"
            confidence = max(confidence, 0.7)
        elif flags["small_talk"]:
            intent_type = "small_talk"
            confidence = 0.9
        elif any(word in text for word in OUT_OF_SCOPE):
            intent_type = "out_of_scope"
            confidence = 0.8

        return IntentResult(
            intent_type=intent_type,
            category=category,
            confidence=confidence,
            slots=slots,
            flags=flags,
            is_topic_change=self._is_topic_change(category, message, session),
            llm_used=False,
        )

    def _detect_category(self, text: str) -> tuple[str, float]:
        if OLD_CIRCULATION_PUMP_RE.search(text):
            return "pumps", 0.9
        if (
            any(marker in text for marker in ["центральн", "водопровод"])
            and (
                "давлен" in text
                or re.search(r"\b\d+(?:[,.]\d+)?\s*(?:бар|bar)\b", text)
            )
        ):
            return "pumps", 0.94
        if (
            any(marker in text for marker in ["под раковин", "под мойк"])
            and "сер" in text
            and "пластик" in text
            and (
                re.search(r"\b\d{2,3}\s*(?:мм)?\b", text)
                or any(marker in text for marker in ["длин", "полметра", "треснул"])
            )
        ):
            # A novice often recognises an indoor sewer pipe only as the grey
            # plastic piece under a sink.  Dimensions make this more specific
            # than the generic undersink siphon/valve menu.
            return "sewer", 0.94
        if (
            "унитаз" in text
            and "перекры" in text
            and any(marker in text for marker in ["вод", "ручк", "перед"])
        ):
            # A novice may describe an angle shut-off valve only by its job:
            # «штука с ручкой перед унитазом».  This is specific enough to
            # enter the water-valve funnel without relying on the LLM.
            return "valves", 0.94
        mentions_warm_floor = bool(
            (
                ("тепл" in text or "тёпл" in text or "водян" in text)
                and re.search(r"\bпол(?:а|у|ом|е|ы)?\b", text)
            )
            or re.search(r"(?<![a-zа-я])(?:втп|тп)(?![a-zа-я])", text)
        )
        negates_warm_floor = bool(
            re.search(
                r"\b(?:без|не|кроме|исключи|убери|только\s+не)\s+"
                r"[^.?!,;]{0,24}(?:тепл|тёпл)[^,.?!;]{0,12}пол",
                text,
            )
            or re.search(
                r"\b(?:тепл|тёпл)\w*\s+пол\w*[^.?!,;]{0,20}"
                r"(?:не\s+будет|не\s+нужен|не\s+нужно|исключен\w*)",
                text,
            )
        )
        if mentions_warm_floor and not negates_warm_floor:
            # A warm floor is a project scope, not a radiator fitting.  An
            # explicitly named component still wins; otherwise start with the
            # loop-pipe branch and let the project state machine collect area
            # and heat-source facts.
            if re.search(r"\b(?:насос|помп)\w*\b", text):
                return "pumps", 0.96
            if re.search(
                r"\b(?:термостат|терморегулятор|сервопривод|контроллер)\w*\b",
                text,
            ):
                return "controls", 0.96
            boiler_mentioned = bool(
                re.search(r"\b(?:кот[её]л|котл\w*|кател\w*)\b", text)
            )
            boiler_is_context = bool(
                re.search(
                    r"\bот\s+(?:(?:газов|электрическ)\w*\s+)?"
                    r"кот(?:[её]л|л)\w*\b",
                    text,
                )
                or re.search(
                    r"\bкот(?:[её]л|л)\w*[^.!?]{0,16}(?:уже\s+есть|имеется|установлен)",
                    text,
                )
            )
            explicit_boiler_task = any(
                marker in text
                for marker in ["обвяз", "бойлер", "радиатор", "гвс"]
            ) or bool(
                re.search(
                    r"\b(?:подбери|подобрать|нужен|купить|заменить)\w*"
                    r"[^.!?]{0,18}кот(?:[её]л|л)\w*",
                    text,
                )
            )
            boiler_is_primary = boiler_mentioned and (
                explicit_boiler_task or not boiler_is_context
            )
            broader_heating_project = bool(
                re.search(r"\b(?:дом|коттедж)\w*\b", text)
                and any(
                    marker in text
                    for marker in ["газ", "электр", "радиатор", "гвс", "отоплен"]
                )
            )
            if boiler_is_primary or broader_heating_project:
                return "boilers", 0.96
            return "pipes", 0.95
        if re.search(
            r"\b(?:муфт|тройник|угольник|переходник|фитинг)\w*\b",
            text,
        ) and not any(
            marker in text
            for marker in ["канализац", "канаш", "ht ", "htb", "kg "]
        ) and not re.search(
            r"\bне\s+(?:(?:нужн\w*|ищ\w*)\s+)?"
            r"(?:муфт|тройник|угольник|переходник|фитинг)\w*\b",
            text,
        ):
            # A material name describes the fitting system, not the product
            # category: «муфта PPR 40 на 25» is still a fitting.
            return "fittings", 0.97
        # Material/system notation identifies a pipe even when a novice omits
        # the noun: «ПНД ПЭ100 от колодца», «металлопласт на радиаторы».
        # This must run before the generic well/pump context below.
        explicit_pipe_material = bool(
            re.search(
                r"\b(?:pprc|ppr|pp-r|ппр|pe-rt|pert|пе-рт|pex|pe-x[abc]?|"
                r"пнд|hdpe|пэ\s*-?\s*100|pe\s*-?\s*100)\b|"
                r"полипропилен|сшит\w*\s+полиэтилен|металлопласт",
                text,
            )
        )
        if explicit_pipe_material and not re.search(r"\b(?:насос|помп)\w*\b", text):
            return "pipes", 0.96
        if (
            re.search(r"\b(?:развест\w*|разводк\w*|пролож\w*)\b", text)
            and any(marker in text for marker in ["вод", "гвс", "хвс", "отоплен"])
        ):
            return "pipes", 0.94
        if (
            re.search(r"\b(?:пая\w*|свари\w*)\b", text)
            and any(marker in text for marker in ["утюг", "паяльник", "пластик"])
        ):
            return "pipes", 0.92
        if (
            "зеркал" in text
            and re.search(r"\bкол(?:ьц|ец)\w*", text)
            and any(marker in text for marker in ["вод", "расход", "колод"])
        ):
            return "pumps", 0.9
        if "колод" in text or (
            "столб" in text and "вод" in text
        ) or (
            re.search(r"\bкол(?:ьц|ец)\w*", text)
            and "вод" in text
            and "от дна" in text
        ):
            return "pumps", 0.88
        notation_category, notation_score = engineering_category_hint(text)
        if notation_category:
            return notation_category, notation_score
        # A pressure vessel is not a pump merely because its purpose mentions
        # protecting a pump.  Recognise both the trade term and a customer's
        # functional description before generic pump keywords are scored.
        if (
            re.search(r"\b(?:гидроаккумулятор\w*|гидробак\w*)\b", text)
            or re.search(
                r"\bг\s*\.?\s*а\s*\.?\s*\d{1,4}\s*(?:л\b|литр)",
                text,
            )
            or re.search(r"\bрасширительн\w*\s+бак\w*\b", text)
            or (
                re.search(r"\b(?:бак|емкост|ёмкост|ресивер)\w*\b", text)
                and (
                    "защит" in text and "насос" in text and "част" in text and "включ" in text
                    or "поддерж" in text and "давлен" in text
                    or "водоснаб" in text and "мембран" in text
                )
            )
            or (
                "мембран" in text
                and "бак" in text
                and not re.search(r"\b(?:отоплен|теплоносител)\w*\b", text)
            )
        ):
            return "hydraulic_accumulators", 0.98

        # An explicitly requested pipe remains a pipe when the phrase also
        # names its source/route ("ПНД труба от скважины до дома").
        if re.search(r"\bтруб\w*\b", text) and not re.search(
            r"\b(?:насос|помп)\w*\b", text
        ):
            if "канализац" not in text:
                return "pipes", 0.94
        # Product nouns win over generic energy words.  Previously the single
        # adjective ``электрический`` was a boiler keyword, so an electric
        # water-heater request was routed into the boiler funnel before the LLM
        # could see it.  Keep ``котёл с бойлером`` in the boiler branch (the
        # customer is selecting a boiler/complectation), while a standalone
        # boiler or an explicit water-heater kind starts the water-heater
        # branch.
        has_positive_boiler_noun = self._has_non_negated_match(
            text,
            r"\b(?:кот[её]л|котл\w*|кател\w*)\b",
        )
        boiler_with_tank = bool(
            has_positive_boiler_noun
            and (
                re.search(
                    r"\b(?:кот[её]л|котл\w*)\b[^.!?]{0,45}"
                    r"(?:\bс\s+(?:встроенн\w*\s+)?бойлер\w*|"
                    r"\bбойлер\w*\s+(?:встроен|внутри|в комплект))",
                    text,
                )
            )
        )
        water_heater_accessory = self._is_water_heater_accessory_request(text)
        has_positive_water_heater_noun = self._has_non_negated_match(
            text,
            r"\b(?:водонагрев\w*|водогре\w*|бойлер\w*)\b",
        )
        has_positive_gas_column = self._has_non_negated_match(
            text,
            r"\bгазов\w*\s+колонк\w*\b",
        )
        explicit_water_heater = not water_heater_accessory and bool(
            has_positive_water_heater_noun or has_positive_gas_column
        )
        if boiler_with_tank:
            return "boilers", 0.95
        # A single-category search cannot represent two separate products.
        # Preserve the heating-system branch for "котёл и бойлер" instead of
        # silently discarding the explicitly requested котёл.
        if has_positive_boiler_noun and has_positive_water_heater_noun:
            return "boilers", 0.9
        if explicit_water_heater and not has_positive_boiler_noun:
            return "water_heaters", 0.95
        complex_heating_project = bool(
            re.search(r"\b(?:дом|коттедж)\w*\b", text)
            and re.search(r"\d{2,4}\s*(?:м2|м²|квадрат|кв\.?\s*м)", text)
            and any(marker in text for marker in ["газ", "электр", "отоплен", "гвс"])
            and any(marker in text for marker in ["радиатор", "тепл", "тёпл", "гвс"])
        )
        if complex_heating_project:
            return "boilers", 0.96
        if (
            any(marker in text for marker in ["радиатор", "батаре"])
            and any(marker in text for marker in ["крут", "регулир", "держал"])
            and "температур" in text
        ):
            # Everyday description of a thermostatic head.  It refers to the
            # radiator valve accessory, not to selection of the radiator body.
            return "radiator_fittings", 0.97
        if (
            any(marker in text for marker in ["радиатор", "батаре", "биметалл"])
            and not any(
                marker in text
                for marker in [
                    "клапан",
                    "термоголов",
                    "арматур",
                    "комплект",
                    "кран",
                    "для батаре",
                    "для радиатор",
                    "штук",
                    "крут",
                    "перекры",
                    "регулир",
                    # "трубы ... подключение радиаторов" называет трубу, а не
                    # радиатор — радиатор здесь только цель подключения.
                    "труб",
                    "подключение радиатор",
                    "подключение батаре",
                ]
            )
        ):
            return "radiators", 0.9
        if "ppr" in text and any(
            marker in text for marker in ["угольник", "муфт", "тройник", "переходник", "фитинг"]
        ):
            return "fittings", 0.9
        best_category = "other"
        best_score = 0.0
        for category, keywords in CATEGORY_KEYWORDS.items():
            if category == "water_heaters" and water_heater_accessory:
                continue
            hits = sum(
                1
                for keyword in keywords
                if normalize_text(keyword) in text and not self._is_negated(text, normalize_text(keyword))
            )
            if hits:
                score = min(0.95, 0.55 + hits * 0.15)
                if score > best_score:
                    best_category = category
                    best_score = score
        if best_category == "sewer" and "труба" in text:
            best_score = max(best_score, 0.9)
        return best_category, best_score

    @staticmethod
    def _has_non_negated_match(text: str, pattern: str) -> bool:
        """Return true when at least one matching product mention is positive."""
        for match in re.finditer(pattern, text):
            before = text[max(0, match.start() - 70) : match.start()]
            after = text[match.end() : match.end() + 55]
            negated_before = re.search(
                r"\b(?:без|кроме|не(?!\s+только)"
                r"(?:\s+(?:нужен|нужна|нужны|надо|хочу|интересует|"
                r"предлагай|показывай))?|не\s+хочу)"
                r"\s+(?:\w+\s+){0,2}$",
                before,
            )
            negated_after = re.match(
                r"(?:\s+\w+){0,3}\s+не\s+"
                r"(?:нужен|нужна|нужны|надо|интересует|"
                r"предлагай|показывай|хочу)\b",
                after,
            )
            if not negated_before and not negated_after:
                return True
        return False

    @staticmethod
    def _is_water_heater_accessory_request(text: str) -> bool:
        """Distinguish an appliance from a requested spare part for it."""
        accessory = (
            r"(?:тэн|анод|термостат|датчик|клапан|кран|фланец|"
            r"прокладк\w*|креплен\w*)"
        )
        appliance = r"(?:водонагрев\w*|бойлер\w*)"
        return bool(
            # "ТЭН для бойлера" / "анод к водонагревателю".
            re.search(
                rf"\b{accessory}\b[^.!?]{{0,35}}\b(?:для|к)\s+{appliance}\b",
                text,
            )
            # Natural reverse order: "для бойлера нужен ТЭН".
            or re.search(
                rf"\b(?:для|к)\s+{appliance}\b[^.!?]{{0,45}}\b{accessory}\b",
                text,
            )
            # The accessory itself is the grammatical object of the request.
            or re.search(
                rf"\b(?:нужен|нужна|нужно|нужны|ищу|подбери|подберите|"
                rf"покажи|покажите|купить)\s+(?:\w+\s+){{0,2}}{accessory}\b",
                text,
            )
        )

    @staticmethod
    def _looks_like_new_request(text: str) -> bool:
        """Клиент начинает новый запрос, а не отвечает на заданный вопрос.

        Ответ обычно не содержит просьбы: «радиаторная разводка, 70 градусов» —
        это ответ, а «подберите канализацию 110» — новый запрос, и он вправе
        сменить категорию.
        """
        return any(
            marker in text
            for marker in [
                "нужен",
                "нужна",
                "нужны",
                "подбери",
                "подберите",
                "дайте",
                "покажи",
                "хочу",
                "ищу",
                "интересует",
                "а есть",
                "теперь",
                # Явное исправление — это тоже новый запрос, а не ответ.
                "не то",
                "я спрашива",
                "я про",
                "имею в виду",
                "имел в виду",
                "речь про",
                "речь о",
                "ошибся",
                "ошиблась",
            ]
        ) or bool(re.match(r"\s*нет\b", text))

    # Именительные формы названий товара по категориям. Границы слова
    # обязательны: «радиаторная разводка» — это участок трубы, а не радиатор,
    # поэтому подстрочный поиск здесь не годится.
    _CATEGORY_NOUNS: dict[str, str] = {
        "pumps": r"\bнасос(?:ы|а|ов|у|ом)?\b|\bпомп(?:а|ы|у)\b",
        "boilers": r"\bкот(?:ел|ёл|лы|ла|лов)\b",
        "pipes": r"\bтруб(?:а|ы|у|ой|ам)?\b",
        "radiators": r"\bрадиатор(?:ы|а|ов|у|ом)?\b|\bбатаре(?:я|и|ю)\b",
        "valves": r"\bкран(?:ы|а|ов|у|ом)?\b|\bвентил(?:ь|я|и)\b",
        "sewer": r"\bканализаци(?:я|и|ю)\b",
        "radiator_fittings": r"\bтермоголовк(?:а|и|у)\b",
    }

    def _names_category_noun(self, text: str, category: str) -> bool:
        pattern = self._CATEGORY_NOUNS.get(category)
        return bool(pattern and re.search(pattern, text))

    @staticmethod
    def _spoken_inch_size(text: str) -> str | None:
        """Размер, названный словами: «полдюйма», «дюймовка», «три четверти».

        Покупатели редко пишут «1/2"» — чаще «полдюйма» или «дюймовка». Раньше
        такой размер терялся, и бот переспрашивал «1/2, 3/4 или в мм?», хотя
        клиент уже ответил.
        """
        spoken = (
            (r"пол\s*[-]?\s*дюйм", "1/2"),
            (r"\bполудюйм", "1/2"),
            (r"тр(?:и|ех|ех)\s*четверт|\bтрехчетвертн", "3/4"),
            (r"\bдюймовк|\bдюймов(?:ый|ая|ую)\b|\bна\s+дюйм\b|\bодин\s+дюйм\b", "1"),
        )
        for pattern, size in spoken:
            if re.search(pattern, text):
                return size
        return None

    @staticmethod
    def _thread_type_from_text(text: str) -> str | None:
        """Canonical thread pairing asked for: ff (ВР/ВР), fm (ВР/НР), mm (НР/НР).

        ВР/ВН = внутренняя (female), НР/НАР = наружная (male). Customers write
        this a dozen ways, and until now it was dropped entirely — so «кран
        1/2" ВР/ВР» ranked a ВН/НР valve first just because it was cheaper.
        """
        # Монтажники называют внутреннюю резьбу «мамой», наружную «папой».
        # Без этого «кран полдюйма мама-папа» терял тип резьбы, и бот
        # переспрашивал то, что клиент уже назвал.
        female = r"(?:вр|вн|мам\w*)\.?"
        male = r"(?:нр|нар|пап\w*)\.?"
        if (
            re.search(rf"\b{female}\s*[-/х]\s*{female}", text)
            or "ff" in text.split()
            or re.search(r"\bвв\b", text)
            or re.search(r"\bвнутренн\w*\s*[-/х]\s*внутренн\w*", text)
        ):
            return "ff"
        if re.search(rf"\b{female}\s*[-/х]\s*{male}", text) or re.search(
            rf"\b{male}\s*[-/х]\s*{female}", text
        ) or any(code in text.split() for code in ["fm", "mf"]) or re.search(
            r"\b(?:внутренн\w*\s*[-/х]\s*наружн\w*|"
            r"наружн\w*\s*[-/х]\s*внутренн\w*)",
            text,
        ):
            return "fm"
        if (
            re.search(rf"\b{male}\s*[-/х]\s*{male}", text)
            or "mm" in text.split()
            or re.search(r"\bнн\b", text)
            or re.search(r"\bнаружн\w*\s*[-/х]\s*наружн\w*", text)
        ):
            return "mm"
        return None

    def _name_tokens_from_text(self, text: str) -> list[str]:
        """Latin model/series words from the query, e.g. BASE, MINI, GOST.

        Units and measurement words are excluded; brands are matched separately
        and would only duplicate the signal.
        """
        stop = {
            "ppr",
            "pex",
            "pvc",
            "pn",
            "dn",
            "sdr",
            "mm",
            "ff",
            "fm",
            "gost",
            "din",
            "max",
            "min",
        }
        tokens = [
            token
            for token in re.findall(r"\b[a-z][a-z0-9]{2,}\b", text)
            if token not in stop
            and token
            not in {
                normalize_text(brand)
                for brand in self._catalog_brands
            }
        ]
        return list(dict.fromkeys(tokens))[:3]

    @staticmethod
    def _is_negated(text: str, keyword: str) -> bool:
        """True when ``keyword`` is explicitly rejected, e.g. "не насосы",
        "а не насос", "не нужен насос". Prevents a corrected topic ("трубы, а
        не насосы") from still being scored as the rejected category.

        ``\\b`` before "не" is required: without it the pattern also matches
        the "не" tail of ordinary words like "мне" (as in "мне нужен котёл"),
        which zeroed out unrelated category hits entirely.
        """
        word = rf"{re.escape(keyword)}\w*"
        before = re.search(
            rf"\b(?:без|кроме|не(?!\s+только)(?:\s+(?:нужен|нужна|нужны|надо|хочу|"
            rf"интересует|предлагай|показывай))?|не\s+хочу)\s+(?:\w+\s+){{0,2}}{word}",
            text,
        )
        # Natural corrections are often post-positive: "котёл мне не нужен".
        # Only a rejection verb after "не" counts, so "котёл мне нужен не
        # электрический" does not accidentally reject the boiler category.
        after = re.search(
            rf"\b{word}(?:\s+\w+){{0,3}}\s+не\s+"
            rf"(?:нужен|нужна|нужны|надо|интересует|предлагай|показывай|хочу)\b",
            text,
        )
        return bool(before or after)

    def _extract_slots(self, text: str, category: str, slots: dict[str, Any]) -> None:
        max_price = self._extract_price_bound(text, upper=True)
        if max_price is not None:
            slots["max_price"] = max_price
            slots["allow_alternatives"] = False
        min_price = self._extract_price_bound(text, upper=False)
        if min_price is not None:
            slots["min_price"] = min_price
            slots["allow_alternatives"] = False

        wifi_term = r"(?:wi[- ]?fi|вай[- ]?фа(?:й|я|ем))"
        excludes_wifi = bool(
            re.search(
                rf"\bбез\s+(?:(?:встроенного\s+)?модуля\s+|поддержки\s+)?{wifi_term}\b",
                text,
            )
            or re.search(
                rf"\b{wifi_term}\b(?:\s+\w+){{0,2}}\s+не\s+(?:нужен|нужна|нужно|требуется)\b",
                text,
            )
        )
        requires_wifi = bool(
            re.search(
                rf"\bс\s+(?:поддержкой\s+|(?:встроенным\s+)?модулем\s+)?{wifi_term}\b",
                text,
            )
            or re.search(rf"\b(?:нужен|нужна|нужно)\s+{wifi_term}\b", text)
        )
        if excludes_wifi:
            slots["excluded_features"] = ["wifi"]
            slots["allow_alternatives"] = False
        elif requires_wifi:
            slots["required_features"] = ["wifi"]
            slots["allow_alternatives"] = False

        if category == "fittings" and "ppr" in text:
            slots["fitting_system"] = "ppr"

        if category == "boilers" and self._is_builtin_selection_constraint(
            text,
            category,
        ):
            part_patterns = {
                "насос": r"(?:встроенн\w*\s+)?(?:циркуляционн\w*\s+)?насос",
                "бак": r"(?:встроенн\w*\s+)?(?:расширительн\w*\s+)?бак",
                "группа безопасности": r"(?:встроенн\w*\s+)?групп\w*\s+безопасн",
                "3-ходовой клапан": (
                    r"(?:встроенн\w*\s+)?(?:трех|3)[- ]?ходов\w*\s+клапан"
                ),
            }
            excluded_parts = [
                part
                for part, pattern in part_patterns.items()
                if (
                    re.search(rf"\bбез(?:\s+\w+){{0,3}}\s+{pattern}", text)
                    or re.search(
                        rf"\bне\s+(?:нужен\w*|требуется|должен\s+быть)"
                        rf"(?:\s+\w+){{0,3}}\s+{pattern}",
                        text,
                    )
                    or re.search(rf"\bне\s+с(?:о)?\s+{pattern}", text)
                    or re.search(
                        rf"{pattern}(?:\s+\w+){{0,4}}\s+"
                        r"(?:не\s+(?:нужен\w*|требуется|должен\s+быть)|"
                        r"исключить|не\s+включать)",
                        text,
                    )
                )
            ]
            required_parts: list[str] = []
            positive_patterns = {
                "насос": (
                    r"(?:со?|с)\s+(?:встроенн\w*\s+)?"
                    r"(?:циркуляционн\w*\s+)?насос"
                    r"|встроенн\w*(?:\s+\w+){0,3}\s+насос"
                ),
                "бак": (
                    r"(?:со?|с)\s+(?:встроенн\w*\s+)?"
                    r"(?:расширительн\w*\s+)?бак"
                    r"|встроенн\w*(?:\s+\w+){0,3}\s+бак"
                ),
                "группа безопасности": (
                    r"(?:со?|с)\s+(?:встроенн\w*\s+)?групп\w*\s+безопасн"
                    r"|встроенн\w*(?:\s+\w+){0,3}\s+групп\w*\s+безопасн"
                ),
                "3-ходовой клапан": (
                    r"(?:со?|с)\s+(?:встроенн\w*\s+)?"
                    r"(?:трех|3)[- ]?ходов\w*\s+клапан"
                    r"|встроенн\w*(?:\s+\w+){0,3}\s+"
                    r"(?:трех|3)[- ]?ходов\w*\s+клапан"
                ),
            }
            for part, pattern in positive_patterns.items():
                if part not in excluded_parts and re.search(pattern, text):
                    required_parts.append(part)
            if required_parts:
                slots["required_builtin_parts"] = required_parts
                slots["allow_alternatives"] = False
            if excluded_parts:
                slots["excluded_builtin_parts"] = excluded_parts
                slots["allow_alternatives"] = False

        if category == "pumps":
            if "циркуляц" in text:
                slots["pump_type"] = "циркуляционный"
                # An explicit circulation-pump request starts an отопление branch.
                # Do not inherit a previous irrigation purpose from the session.
                slots["pump_use"] = "отопление"
            elif "повысит" in text:
                slots["pump_type"] = "повысительный"
            elif "дренаж" in text or "откач" in text:
                slots["pump_type"] = "дренажный"
            elif "скважин" in text:
                slots["pump_type"] = "скважинный"

        has_electric = "электр" in text
        has_gas = bool(re.search(r"\bгаз(?:ов\w*|а|у|ом)?\b", text))
        rejects_electric = bool(re.search(r"\bне\s+электр", text))
        rejects_gas = bool(
            re.search(r"\bне\s+газ", text)
            or re.search(r"\bгаз[ауы]?\s+н[еэ]+т\w*\b", text)
            or re.search(r"\bн[еэ]+т\w*\s+газ[ауы]?\b", text)
            or "газа нет" in text
            or "без газ" in text
            or "нет газ" in text
        )
        if category == "boilers":
            explicit_electric_boiler = bool(
                re.search(r"\bэлектр\w*\s+кот(?:ел|ёл|л\w*)\b", text)
                or re.search(r"\bкот(?:ел|ёл|л\w*)\s+электр\w*\b", text)
            )
            explicit_gas_boiler = bool(
                re.search(r"\bгазов\w*\s+кот(?:ел|ёл|л\w*)\b", text)
                or re.search(r"\bкот(?:ел|ёл|л\w*)\s+газов\w*\b", text)
            )
            if explicit_electric_boiler and not explicit_gas_boiler:
                slots["boiler_type"] = "электрический"
            elif explicit_gas_boiler and not explicit_electric_boiler:
                slots["boiler_type"] = "газовый"
            elif rejects_electric and has_gas:
                slots["boiler_type"] = "газовый"
            elif rejects_gas:
                slots["boiler_type"] = "электрический"
            elif has_gas and not has_electric:
                slots["boiler_type"] = "газовый"
            elif has_electric and not has_gas:
                slots["boiler_type"] = "электрический"
            if re.search(
                r"\b(?:газ(?:а|у|ом)?\b[^.!?]{0,30}\b(?:тоже\s+)?есть|"
                r"есть\s+газ)\b",
                text,
            ):
                slots["has_gas"] = True
            if re.search(
                r"\b(?:электричеств\w*|электроснабжен\w*)\b[^.!?]{0,18}"
                r"\b(?:есть|подведен\w*|подключен\w*)\b",
                text,
            ):
                slots["has_electricity"] = True
        elif category == "water_heaters":
            self._extract_water_heater_slots(
                text,
                slots,
                has_electric=has_electric,
                has_gas=has_gas,
                rejects_electric=rejects_electric,
                rejects_gas=rejects_gas,
            )

        if re.search(
            r"\b(?:двух\s*контурн|2\s*(?:-?\s*х)?\s*-?\s*контурн|2x\s*контурн)\w*",
            text,
        ):
            slots["contours"] = "двухконтурный"
            slots["allow_alternatives"] = False
        elif re.search(
            r"\b(?:одно\s*контурн|1\s*(?:-?\s*х)?\s*-?\s*контурн)\w*",
            text,
        ):
            slots["contours"] = "одноконтурный"
            slots["allow_alternatives"] = False

        if category == "boilers":
            closed_chamber = bool(
                re.search(r"\bз\s*\.?\s*к\s*\.?\s*с\.?\b", text)
                or re.search(r"\bзакрыт\w*\s+камер\w*(?:\s+сгоран\w*)?", text)
                or "турбирован" in text
            )
            open_chamber = bool(
                re.search(r"\bо\s*\.?\s*к\s*\.?\s*с\.?\b", text)
                or re.search(r"\bоткрыт\w*\s+камер\w*(?:\s+сгоран\w*)?", text)
                or "атмосферн" in text
            )
            if closed_chamber and not open_chamber:
                slots["combustion_chamber"] = "закрытая"
                slots["allow_alternatives"] = False
            elif open_chamber and not closed_chamber:
                slots["combustion_chamber"] = "открытая"
                slots["allow_alternatives"] = False
            if "дымоход" in text:
                slots["needs_chimney"] = True
            coaxial = re.search(
                r"\bкоаксиальн\w*(?:\s+дымоход\w*)?(?:\s+|\s*[-xх×/]\s*)"
                r"(\d{2,3})\s*[/xх×]\s*(\d{2,3})",
                text,
            )
            if "коаксиальн" in text:
                slots["chimney_type"] = "коаксиальный"
                slots["needs_chimney"] = True
            if coaxial:
                slots["chimney_size"] = (
                    f"{int(coaxial.group(1))}/{int(coaxial.group(2))}"
                )

        # "На отопление и на воду сразу" names both needs without the literal
        # word "горячая" — that combination still means двухконтурный, not
        # just отопление.
        wants_heat_and_water = bool(
            re.search(r"отоплен\w*[^.!?]{0,25}\bи\s+(?:на\s+)?(?:горяч\w*\s+)?вод", text)
            or re.search(r"\bвод\w*[^.!?]{0,25}\bи\s+(?:на\s+)?отоплен", text)
        )
        if category == "boilers" and "не знаю" in text and "кот" in text:
            slots["needs_voltage_clarification"] = True
        elif category == "boilers" and (
            "гвс" in text or ("горяч" in text and "вод" in text) or wants_heat_and_water
        ):
            slots["contours"] = "двухконтурный"
        elif category == "boilers" and "отоплен" in text:
            # The customer answers in terms of the need, not boiler jargon:
            # "только отопление" means a single-circuit boiler.
            slots["contours"] = "одноконтурный"

        rejects_hot_water = bool(
            re.search(r"\bбез\s+(?:горяч\w*\s+вод\w*|гвс)\b", text)
            or re.search(
                r"\b(?:горяч\w*\s+вод\w*|гвс)\b[^.!?]{0,18}"
                r"не\s+(?:нужн\w*|будет|требуется)",
                text,
            )
        )
        mentions_hot_water = bool(
            "гвс" in text or ("горяч" in text and "вод" in text)
        )
        if rejects_hot_water:
            slots["needs_hot_water"] = False
        elif mentions_hot_water:
            slots["needs_hot_water"] = True

        mentions_warm_floor = bool(
            ("тепл" in text or "тёпл" in text)
            and re.search(r"\bпол(?:а|у|ом|е)?\b", text)
        )
        rejects_warm_floor = bool(
            re.search(
                r"\b(?:без|не\s+будет|не\s+нужен|не\s+нужно|исключи\w*|"
                r"только\s+не)\s+[^.?!,;]{0,24}(?:тепл|тёпл)\w*"
                r"[^,.?!;]{0,12}пол",
                text,
            )
            or re.search(
                r"\b(?:тепл|тёпл)\w*\s+пол\w*[^.?!,;]{0,20}"
                r"(?:не\s+будет|не\s+нужен|не\s+нужно)",
                text,
            )
        )
        if rejects_warm_floor:
            slots["has_warm_floor"] = False
            if "радиатор" in text:
                slots["system_type"] = "радиаторы"
        elif mentions_warm_floor:
            slots["has_warm_floor"] = True
            if category == "pipes":
                slots["project_scope"] = "warm_floor"
            if "водян" in text or "от котл" in text:
                slots["warm_floor_type"] = "водяной"
            elif "электр" in text and "кот" not in text:
                slots["warm_floor_type"] = "электрический"
            if "от котл" in text:
                slots["warm_floor_heat_source"] = "котёл"
            if "газ" in text and "кот" in text:
                slots["warm_floor_heat_source"] = "газовый котёл"
            elif "электр" in text and "кот" in text:
                slots["warm_floor_heat_source"] = "электрический котёл"

        insulation_ready = bool(
            re.search(
                r"\b(?:утеплител|теплоизоляц)\w*[^.!?]{0,25}"
                r"(?:есть|готов|уложен|сделан|уже)\w*\b",
                text,
            )
            or re.search(
                r"\b(?:есть|готов|уложен|сделан)\w*[^.!?]{0,20}"
                r"(?:утеплител|теплоизоляц)\w*\b",
                text,
            )
        )
        insulation_missing = bool(
            re.search(
                r"\b(?:утеплител|теплоизоляц)\w*[^.!?]{0,20}"
                r"(?:нет|не\s+(?:готов|уложен|сделан))\b",
                text,
            )
            or re.search(r"\bбез\s+(?:утеплител|теплоизоляц)\w*\b", text)
        )
        if insulation_ready:
            slots["floor_insulation_ready"] = True
        elif insulation_missing:
            slots["floor_insulation_ready"] = False

        automation_mentioned = bool(
            re.search(
                r"\b(?:автоматик|сервопривод|комнатн\w*\s+термостат)\w*\b",
                text,
            )
        )
        if automation_mentioned:
            automation_rejected = bool(
                re.search(
                    r"\bбез\s+(?:автоматик|сервопривод|термостат)\w*\b",
                    text,
                )
                or re.search(
                    r"\b(?:автоматик|сервопривод|термостат)\w*[^.!?]{0,18}"
                    r"не\s+(?:нужн|треб)\w*",
                    text,
                )
            )
            slots["warm_floor_automation_needed"] = not automation_rejected

        if category == "sewer":
            if "внутрен" in text:
                slots["sewer_scope"] = "внутренняя"
            elif "наруж" in text:
                slots["sewer_scope"] = "наружная"
            elif "сер" in text and any(marker in text for marker in ["пластик", "канализац"]):
                slots["sewer_scope"] = "внутренняя"
            elif any(marker in text for marker in ["рыж", "оранж"]):
                slots["sewer_scope"] = "наружная"

        if category in {"pipes", "sewer"} and "канализац" in text:
            slots["pipe_purpose"] = "канализация"
        elif category in {"pipes", "sewer"} and "отоплен" in text:
            slots["pipe_purpose"] = "отопление"
        elif category in {"pipes", "sewer"} and (
            "водоснаб" in text
            or "для воды" in text
            or "горяч" in text
            or "холодн" in text
            or "гвс" in text
            or "хвс" in text
        ):
            slots["pipe_purpose"] = "водоснабжение"

        if category == "pipes":
            if "горяч" in text or "гвс" in text:
                slots["water_temperature"] = "горячая"
            elif "холод" in text or "хвс" in text:
                slots["water_temperature"] = "холодная"
            if "бел" in text:
                slots["pipe_color"] = "белая"
            multilayer_metal_polymer = bool(
                re.search(
                    r"(?:pe-?x[a-c]?|pex)\s*[-/]\s*al\s*[-/]\s*(?:pe-?x[a-c]?|pe-?rt|pe)|"
                    r"металлопласт",
                    text,
                )
            )
            if multilayer_metal_polymer or "м/п" in text:
                slots["pipe_material"] = "металлопластик"
            elif any(marker in text for marker in ["ppr", "pp-r", "ппр", "полипроп"]):
                slots["pipe_material"] = "ppr"
            elif re.search(r"\b(?:пая\w*|свари\w*)\b", text) and any(
                marker in text for marker in ["утюг", "паяльник", "пластик"]
            ):
                slots["pipe_material"] = "ppr"
            elif any(marker in text for marker in ["pe-rt", "pert", "пе-рт"]):
                slots["pipe_material"] = "pe-rt"
            elif any(marker in text for marker in ["pex", "pe-x", "сшит"]):
                slots["pipe_material"] = "pex"
            elif any(marker in text for marker in ["пнд", "hdpe", "pe100", "pe-100", "пэ100", "пэ-100"]):
                slots["pipe_material"] = "пэ100"
            elif "нержав" in text or "нерж" in text:
                slots["pipe_material"] = "нержавеющая сталь"

            if (
                ("тепл" in text or "тёпл" in text or "водян" in text)
                and re.search(r"\bпол(?:а|у|ом|е)?\b", text)
            ) or re.search(r"(?<![a-zа-я])(?:втп|тп)(?![a-zа-я])", text):
                slots["pipe_service"] = "петля тёплого пола"
            elif any(
                marker in text
                for marker in [
                    "подключение радиатор",
                    "подключить радиатор",
                    "радиаторн",
                    "к батаре",
                    "до батаре",
                ]
            ):
                slots["pipe_service"] = "радиаторная разводка"
            elif any(marker in text for marker in ["магистрал", "стояк отоплен"]):
                slots["pipe_service"] = "магистраль отопления"
            elif any(marker in text for marker in ["обвязк", "к котл", "котельн"]):
                slots["pipe_service"] = "обвязка котла"
            elif (
                ("скваж" in text or "колод" in text)
                and any(marker in text for marker in ["до дом", "в дом", "от дом"])
            ):
                slots["pipe_service"] = "подземный ввод от источника"
            elif "рециркуляц" in text and ("гвс" in text or "горяч" in text):
                slots["pipe_service"] = "рециркуляция гвс"
            elif any(
                marker in text
                for marker in [
                    "внутри дом",
                    "по дому",
                    "по квартир",
                    "разводк вод",
                    "от стояк",
                    "к кранам",
                    "к точкам",
                ]
            ):
                slots["pipe_service"] = "разводка внутри дома"

            if slots.get("pipe_service") == "подземный ввод от источника":
                # A PE100/HDPE line from a well or borehole into a house is the
                # cold-water service.  Asking whether it is heating or sewerage
                # ignores two strong contextual facts the customer already gave.
                slots.setdefault("pipe_purpose", "водоснабжение")
                slots.setdefault("water_temperature", "холодная")

            if any(marker in text for marker in ["под земл", "в грунт", "подзем"]):
                slots["installation_method"] = "подземная"
            elif any(marker in text for marker in ["скрыт", "в стяжк", "в стен"]):
                slots["installation_method"] = "скрытая"
            elif any(marker in text for marker in ["открыт", "снаружи стен"]):
                slots["installation_method"] = "открытая"
        elif category in {"valves", "radiator_fittings"}:
            if "горяч" in text or "гвс" in text:
                slots["water_temperature"] = "горячая"
            elif "холод" in text or "хвс" in text:
                slots["water_temperature"] = "холодная"

        if "батаре" in text:
            slots["application"] = "радиатор"
        if "дач" in text:
            slots["application"] = "дача"
        if "скваж" in text:
            # Covers common typos such as «скважны» as well as inflected forms.
            slots["water_source"] = "скважина"
        elif "колод" in text:
            slots["water_source"] = "колодец"
        elif "центральн" in text and any(
            marker in text for marker in ["вод", "водопровод"]
        ):
            slots["water_source"] = "центральный водопровод"

        well_depth_match = re.search(
            r"(?:глубин\w*[^\d]{0,20}|"
            r"скважин(?:а|ы|е|у|ой)?\b[^\d]{0,12})(\d{1,3})"
            r"(?:\s*(?:м\b|метр\w*))?",
            text,
        )
        if (
            category == "pumps"
            and well_depth_match
            and slots.get("water_source") == "скважина"
        ):
            slots["well_depth_m"] = float(well_depth_match.group(1))

        if category in {"pipes", "sewer"}:
            sdr_match = re.search(r"\bsdr\s*-?\s*(\d{1,2}(?:[,.]\d+)?)\b", text)
            if sdr_match:
                slots["sdr"] = float(sdr_match.group(1).replace(",", "."))
        boiler_pump_relation = any(
            marker in text
            for marker in [
                "насос для котл",
                "насос к котл",
                "к котл",
                "для котл",
                "система отоплен",
                "контур отоплен",
                "циркуляц",
            ]
        )
        explicit_other_pump_use = any(
            marker in text
            for marker in [
                "водоснаб",
                "скваж",
                "колод",
                "полив",
                "давлен",
                "напор",
                "дренаж",
                "откач",
            ]
        )
        if (
            category == "pumps"
            and "насос" in text
            and "котл" in text
            and boiler_pump_relation
            and not explicit_other_pump_use
        ):
            slots["pump_type"] = "циркуляционный"
            slots["pump_use"] = "отопление"
            slots["pump_context"] = "котел"
        if category == "pumps" and (
            "на замен" in text
            or ("стар" in text and "насос" in text)
            or "заменить насос" in text
        ):
            slots["pump_replacement"] = True
        if category == "pumps" and (
            "слабый напор" in text
            or "низкий напор" in text
            or "плохой напор" in text
            or "еле теч" in text
            or "слабо теч" in text
            or "плохо теч" in text
            or "давлен" in text
        ):
            slots["pump_use"] = "повышение давления"
            slots["symptom"] = "слабый напор"
            if "дом" in text:
                slots["application"] = "дом"
        elif category == "pumps" and (
            "откач" in text
            or "дренаж" in text
            or (
                any(marker in text for marker in ["грязн", "пес", "мусор", "ил "])
                and any(marker in text for marker in ["подвал", "приям", "затоп"])
            )
        ):
            slots["pump_use"] = "откачка воды"
        elif category == "pumps" and "полив" in text:
            # The stated purpose is more specific than the source.  A request
            # such as ``для полива из колодца`` must remain an irrigation goal
            # instead of being relabelled as domestic water supply merely
            # because the same sentence contains ``колодец``.
            slots["pump_use"] = "полив"
        elif category == "pumps" and ("скваж" in text or "колод" in text):
            slots["pump_use"] = "водоснабжение"
        elif category == "pumps" and (
            "вода не ид" in text
            or "вода шла" in text
            or "вода" in text and ("ид" in text or "шла" in text)
        ):
            slots["pump_use"] = "водоснабжение"
            slots["symptom"] = "проблема с подачей воды"
        elif category == "pumps" and (
            "водоснаб" in text
            or "для воды" in text
            or ("вод" in text and "дом" in text)
        ):
            slots["pump_use"] = "водоснабжение"
            if "дом" in text:
                slots["application"] = "дом"
        if (
            category == "pumps"
            and slots.get("pump_use") == "повышение давления"
            and not slots.get("pump_type")
        ):
            slots["pump_type"] = "повысительный"

        for marker, element in [
            ("труб", "труба"),
            ("отвод", "отвод"),
            ("тройник", "тройник"),
            ("муфт", "муфта"),
        ]:
            if marker in text and not self._is_negated(text, marker):
                slots["element_type"] = element
                break
        if category == "sewer" and not slots.get("element_type"):
            if any(
                marker in text
                for marker in [
                    "повернуть",
                    "поворот",
                    "изменить направление",
                    "сделать угол",
                ]
            ):
                slots["element_type"] = "отвод"
            elif "отрез" in text or (
                "сер" in text
                and "пластик" in text
                and re.search(r"\b\d{2,3}\s*мм\b", text)
            ):
                slots["element_type"] = "труба"

        if "углов" in text:
            slots["body_form"] = "угловой"
            slots["connection_form"] = "угловое"
        elif "прям" in text:
            slots["body_form"] = "прямой"
            slots["connection_form"] = "прямое"
        if "американк" in text or "полусгон" in text:
            slots["union"] = True
        if "термоголов" in text:
            slots["thermostatic_head"] = True
        elif "перекры" in text or "закрывать" in text or "отсек" in text:
            slots["thermostatic_head"] = False
            slots["radiator_action"] = "перекрывать поток"
        elif category == "radiator_fittings" and (
            "регулир" in text or "температур" in text
        ):
            slots["thermostatic_head"] = True
            slots["radiator_action"] = "регулировать температуру"

        area_match = re.search(
            r"(\d{2,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)",
            text,
        )
        warm_floor_area_match = re.search(
            r"(?:тепл\w*|тёпл\w*)\s+пол\w*.{0,30}?"
            r"(\d{1,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)|"
            r"(\d{1,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)"
            r".{0,30}?(?:тепл\w*|тёпл\w*)\s+пол",
            text,
        )
        house_area_match = re.search(
            r"(?:дом|коттедж|площад\w*\s+дом\w*|общ\w*\s+площад\w*)"
            r"\D{0,20}(\d{2,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b|метр)|"
            r"(\d{2,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b|метр)"
            r"\D{0,12}(?:дом|коттедж|общ\w*\s+площад)",
            text,
        )
        if warm_floor_area_match and not rejects_warm_floor:
            raw_area = warm_floor_area_match.group(1) or warm_floor_area_match.group(2)
            slots["warm_floor_area_m2"] = float(raw_area)
            if not house_area_match:
                # A local model may duplicate this literal as generic
                # ``area_m2``.  The wording anchors it to the warm-floor
                # subsystem, so it cannot replace an already known house area.
                slots.pop("area_m2", None)
        if house_area_match and not re.search(r"\bдо\s+дом\w*\b", text):
            raw_area = house_area_match.group(1) or house_area_match.group(2)
            slots["area_m2"] = float(raw_area)
        elif area_match and not warm_floor_area_match:
            if mentions_warm_floor and not rejects_warm_floor and category == "pipes":
                slots["warm_floor_area_m2"] = float(area_match.group(1))
            else:
                slots["area_m2"] = float(area_match.group(1))
        elif category == "boilers":
            area_meters_match = re.search(r"(\d{2,4})\s*(?:м\b|метр)", text)
            if area_meters_match:
                slots["area_m2"] = float(area_meters_match.group(1))
            else:
                # In a boiler request, "котёл на 100" conventionally means an
                # approximate heated area. Keep small model/power numbers such as
                # 24 out of this shorthand and ignore prices/budgets.
                short_area_match = re.search(
                    r"(?:\bна|\bдля)\s*(\d{2,4})(?!\s*(?:квт|тыс|руб|₽|вольт|в\b))",
                    text,
                )
                if short_area_match and int(short_area_match.group(1)) >= 30:
                    slots["area_m2"] = float(short_area_match.group(1))

        floors_match = re.search(r"(\d{1,2})\s*(?:этаж|этажа|этажей)\b", text)
        if floors_match:
            slots["floors"] = int(floors_match.group(1))
        else:
            word_floor_match = re.search(
                r"\b(один|одно|два|две|три|четыре|пять)\s+"
                r"(?:этаж|этажа|этажей)\b",
                text,
            )
            if word_floor_match:
                slots["floors"] = {
                    "один": 1,
                    "одно": 1,
                    "два": 2,
                    "две": 2,
                    "три": 3,
                    "четыре": 4,
                    "пять": 5,
                }[word_floor_match.group(1)]

        if category == "hydraulic_accumulators":
            if (
                re.search(r"\b(?:гидроаккумулятор|гидробак)\w*\b", text)
                or re.search(
                    r"\bг\s*\.?\s*а\s*\.?\s*\d{1,4}\s*(?:л\b|литр)",
                    text,
                )
                or (
                    "насос" in text
                    and any(marker in text for marker in ["част", "включ", "запас вод"])
                )
                or any(marker in text for marker in ["водоснаб", "гвс", "хвс"])
            ):
                slots["tank_application"] = "водоснабжение"
            elif any(marker in text for marker in ["отоплен", "теплоносител"]):
                slots["tank_application"] = "отопление"
            volume_match = re.search(
                r"(?<!\d)(\d{1,4}(?:[,.]\d+)?)\s*(?:л\b|литр\w*)",
                text,
            )
            if volume_match:
                slots["volume_l"] = float(volume_match.group(1).replace(",", "."))
            if re.search(
                r"\bг\s*\.?\s*а\s*\.?\s*\d{1,4}\s*(?:л\b|литр)",
                text,
            ):
                slots.pop("sku", None)
            if re.search(r"\b(?:вертикальн|стояч)\w*", text):
                slots["orientation"] = "вертикальный"
            elif re.search(r"\b(?:горизонтальн|лежач)\w*", text):
                slots["orientation"] = "горизонтальный"

        dn_match = re.search(r"\b(?:ду|dn)\s*-?\s*(\d{1,3})\b", text)
        if dn_match and category in {
            "pipes",
            "sewer",
            "fittings",
            "valves",
            "radiator_fittings",
        }:
            slots["diameter_mm"] = int(dn_match.group(1))
            slots["nominal_diameter_dn"] = int(dn_match.group(1))
        pn_match = re.search(r"\b(?:ру|pn)\s*-?\s*(\d{1,3})\b", text)
        if pn_match and category in {
            "pipes",
            "sewer",
            "fittings",
            "valves",
            "radiator_fittings",
        }:
            slots["pressure_class_bar"] = float(pn_match.group(1))

        temperature_c = extract_temperature_c(text)
        if temperature_c is not None and category in {
            "pipes",
            "valves",
            "radiator_fittings",
        }:
            if -80 <= temperature_c <= 300:
                slots["operating_temperature_c"] = temperature_c

        pressure_match = re.search(
            r"(?:рабоч\w*\s+)?давлен\w*[^\d]{0,18}"
            r"(\d+(?:[,.]\d+)?)\s*(?:бар(?:а|ов)?|bar)\b",
            text,
        )
        if not pressure_match:
            pressure_match = re.search(
                r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:бар(?:а|ов)?|bar)\b",
                text,
            )
        if category == "pumps":
            pressure_transition_match = re.search(
                r"давлен\w*[^\d]{0,18}(\d+(?:[,.]\d+)?)\s*"
                r"(?:бар(?:а|ов)?\s*)?(?:до|[-=]>|→|—|–)\s*"
                r"(\d+(?:[,.]\d+)?)\s*(?:бар(?:а|ов)?|bar)\b",
                text,
            )
            inlet_pressure_match = re.search(
                r"(?:давлен\w*[^\d]{0,15})?"
                r"(?:на вход\w*|входн\w*\s+давлен\w*|исходн\w*\s+давлен\w*|"
                r"сейчас|теперь|имеетс\w*|есть)"
                r"[^\d]{0,12}(\d+(?:[,.]\d+)?)\s*(?:бар(?:а|ов)?|bar)\b",
                text,
            )
            required_pressure_match = re.search(
                r"(?:нужн\w*|требуем\w*|целев\w*|хочу|получить|"
                r"после насос\w*|на выход\w*)"
                r"[^\d]{0,18}(\d+(?:[,.]\d+)?)\s*(?:бар(?:а|ов)?|bar)\b",
                text,
            )
            if pressure_transition_match:
                slots["inlet_pressure_bar"] = float(
                    pressure_transition_match.group(1).replace(",", ".")
                )
                slots["required_pressure_bar"] = float(
                    pressure_transition_match.group(2).replace(",", ".")
                )
            elif inlet_pressure_match:
                slots["inlet_pressure_bar"] = float(
                    inlet_pressure_match.group(1).replace(",", ".")
                )
            if required_pressure_match and not pressure_transition_match:
                slots["required_pressure_bar"] = float(
                    required_pressure_match.group(1).replace(",", ".")
                )
            elif (
                pressure_match
                and not inlet_pressure_match
                and not pressure_transition_match
            ):
                slots["required_pressure_bar"] = float(
                    pressure_match.group(1).replace(",", ".")
                )
        elif pressure_match and category in {
            "pipes",
            "valves",
            "radiator_fittings",
        }:
            pressure_value = float(pressure_match.group(1).replace(",", "."))
            slots["operating_pressure_bar"] = pressure_value

        if category in {"pipes", "sewer"}:
            piece_length_mm = extract_piece_length_mm(text)
            if piece_length_mm is not None:
                slots["length_mm"] = piece_length_mm
            total_length_m = extract_total_length_m(text)
            if total_length_m is not None:
                slots["total_length_m"] = total_length_m

        if category == "boilers":
            voltage_match = re.search(r"\b(220|380)\b", text)
            if voltage_match:
                slots["voltage_v"] = int(voltage_match.group(1))

        kw_match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", text)
        if kw_match:
            slots["power_kw"] = float(kw_match.group(1).replace(",", "."))

        if category == "pumps":
            self._extract_old_pump_model(text, slots)
            self._extract_standalone_pump_params(text, slots)
            count_token = (
                r"\d{1,3}(?:[,.]\d+)?|од\w*|дв\w*|тр\w*|четыр\w*|"
                r"пят\w*|шест\w*|сем\w*|восем\w*|девят\w*|десят\w*"
            )
            before_water_level = text.split("зеркал", 1)[0]
            ring_match = re.search(
                rf"(?<!\w)({count_token})\s+(?:ж/?б\s+)?кол(?:ьц|ец)\w*",
                before_water_level,
            )
            ring_prefix = (
                before_water_level[max(0, ring_match.start() - 24) : ring_match.start()]
                if ring_match
                else ""
            )
            ring_suffix = (
                before_water_level[ring_match.end() : ring_match.end() + 24]
                if ring_match
                else ""
            )
            ring_belongs_to_water = bool(
                re.search(r"(?:вод\w*|столб\w*\s+вод\w*)(?:\s+на)?\s*$", ring_prefix)
                or re.match(r"\s+(?:вод\w*\s+)?(?:от|со)\s+дна", ring_suffix)
            )
            if ring_match and not ring_belongs_to_water:
                rings = self._household_count(ring_match.group(1))
                if rings:
                    slots["water_source"] = "колодец"
                    slots.setdefault("pump_use", "водоснабжение")
                    slots["well_ring_count"] = rings
                    slots["ring_height_assumed"] = True
            water_level_match = re.search(
                rf"зеркал\w*(?:\s+вод\w*)?[^\dа-я]{{0,12}}"
                rf"(?:на\s+)?({count_token})\s+кол(?:ьц|ец)\w*",
                text,
            )
            if water_level_match:
                rings = self._household_count(water_level_match.group(1))
                if rings:
                    slots["water_source"] = "колодец"
                    slots.setdefault("pump_use", "водоснабжение")
                    slots["water_level_ring_count"] = rings
                    if re.search(
                        r"(?:от\s+(?:верха|края|поверхност\w*)|"
                        r"сверху|глубин\w*\s+до\s+вод)",
                        text,
                    ):
                        slots["water_level_reference"] = "from_top"
                    elif re.search(
                        r"(?:от\s+дна|со\s+дна|вод\w*\s+от\s+дна)",
                        text,
                    ):
                        slots["water_level_reference"] = "from_bottom"
                    else:
                        # «Зеркало на двух кольцах» does not say whether the
                        # count starts at the top or at the bottom.  Keep the
                        # raw count and make the dialogue resolve it once.
                        slots["water_level_reference"] = "ambiguous"
                    slots["ring_height_assumed"] = True
            water_column_match = re.search(
                rf"(?:столб\w*\s+вод\w*[^\dа-я]{{0,12}}"
                rf"({count_token})\s+кол(?:ьц|ец)\w*|"
                rf"вод\w*\s+({count_token})\s+кол(?:ьц|ец)\w*\s+от\s+дна|"
                rf"({count_token})\s+кол(?:ьц|ец)\w*\s+"
                rf"(?:вод\w*\s+)?от\s+дна)",
                text,
            )
            if water_column_match:
                raw_rings = next(
                    group for group in water_column_match.groups() if group is not None
                )
                rings = self._household_count(raw_rings)
                if rings:
                    slots["water_column_ring_count"] = rings
                    slots["water_level_reference"] = "from_bottom"
                    slots["ring_height_assumed"] = True
            ambiguous_water_rings = re.search(
                rf"\bвод\w*(?:\s+на)?\s+({count_token})\s+"
                rf"кол(?:ьц|ец)\w*",
                text,
            )
            if (
                ambiguous_water_rings
                and not water_level_match
                and not water_column_match
            ):
                rings = self._household_count(ambiguous_water_rings.group(1))
                if rings:
                    slots["water_source"] = "колодец"
                    slots.setdefault("pump_use", "водоснабжение")
                    slots["water_level_ring_count"] = rings
                    slots["water_level_reference"] = "ambiguous"
                    slots["ring_height_assumed"] = True
            ordinal_water_level = re.search(
                r"\bвод\w*(?:\s+начина\w*)?\s+"
                r"(?:(?:примерн\w*|ориентировочн\w*|где[- ]?то)\s+)?"
                r"(?:на|с)\s+"
                r"(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|"
                r"шест\w*|седьм\w*|восьм\w*|девят\w*|десят\w*)\b",
                text,
            )
            if (
                ordinal_water_level
                and "water_level_ring_count" not in slots
                and slots.get("well_ring_count")
            ):
                rings = self._household_count(ordinal_water_level.group(1))
                if rings:
                    # Colloquial «вода начинается на третьем» omits both the
                    # word «кольцо» and the reference edge.  Preserve the fact,
                    # but let the dialogue confirm whether counting is from the
                    # top instead of silently converting it into metres.
                    slots["water_level_ring_count"] = rings
                    slots["water_level_reference"] = "ambiguous"
                    slots["ring_height_assumed"] = True
            ring_height_match = re.search(
                r"кол(?:ьц|ец)\w*[^.!?\d]{0,16}(\d+(?:[,.]\d+)?)\s*"
                r"(?:м\b|метр\w*)|"
                r"(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)"
                r"[^.!?]{0,16}(?:высот\w*\s+)?кол(?:ьц|ец)\w*",
                text,
            )
            if ring_height_match:
                raw_height = ring_height_match.group(1) or ring_height_match.group(2)
                slots["ring_height_m"] = float(raw_height.replace(",", "."))
                slots["ring_height_assumed"] = False

            explicit_well_depth = re.search(
                r"(?:глубин\w*\s+колодц\w*|колодец\w*\s+глубин\w*)"
                r"[^\d]{0,18}(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)",
                text,
            )
            if explicit_well_depth:
                slots["explicit_well_depth_m"] = float(
                    explicit_well_depth.group(1).replace(",", ".")
                )

            explicit_water_column = re.search(
                r"(?:столб\w*\s+вод\w*|вод\w*\s+от\s+дна\s+до\s+"
                r"(?:поверхност\w*\s+вод\w*|зеркал\w*))"
                r"[^\d]{0,30}(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)",
                text,
            )
            if explicit_water_column:
                slots["explicit_water_column_depth_m"] = float(
                    explicit_water_column.group(1).replace(",", ".")
                )
                slots["water_level_reference"] = "from_bottom"

            explicit_water_level = re.search(
                r"(?:от\s+(?:верха|края)(?:\s+колодц\w*)?\s+до\s+"
                r"(?:поверхност\w*\s+вод\w*|вод\w*|зеркал\w*)|"
                r"глубин\w*\s+до\s+(?:поверхност\w*\s+)?вод\w*)"
                r"[^\d]{0,24}(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)",
                text,
            )
            if not explicit_water_level:
                explicit_water_level = re.search(
                    r"(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)\s+"
                    r"от\s+(?:верха|края)(?:\s+колодц\w*)?"
                    r"\s+до\s+(?:поверхност\w*\s+вод\w*|вод\w*|зеркал\w*)",
                    text,
                )
            if explicit_water_level:
                slots["explicit_water_level_depth_m"] = float(
                    explicit_water_level.group(1).replace(",", ".")
                )
                slots["water_level_reference"] = "from_top"
            mounting_match = re.search(
                r"(?:монтажн\w*\s+длин\w*[^\d]{0,12})?(130|180)\s*мм",
                text,
            )
            if mounting_match:
                slots["mounting_length_mm"] = int(mounting_match.group(1))
            head_match = re.search(r"напор\D{0,10}(\d+(?:[,.]\d+)?)\s*(?:м|метр)", text)
            if head_match:
                slots["head_m"] = float(head_match.group(1).replace(",", "."))
            if "отоплен" in text and "pump_type" not in slots:
                slots["pump_type"] = "циркуляционный"
            if (
                normalize_text(str(slots.get("pump_type") or "")) == "циркуляционный"
                and "head_m" not in slots
                and not re.search(
                    r"\b(?:высот\w*|этаж\w*|подъем\w*|подъём\w*)\b",
                    text,
                )
            ):
                bare_heads = [
                    float(match.group(1).replace(",", "."))
                    for match in re.finditer(
                        r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:м\b|метр(?:а|ов)?)(?!\s*(?:2|²|м))",
                        text,
                    )
                    if float(match.group(1).replace(",", ".")) <= 20
                ]
                if len(bare_heads) == 1:
                    slots["head_m"] = bare_heads[0]
            bare_number_match = re.search(r"(?<!\d)(130|180)(?!\d)", text)
            if bare_number_match and "mounting_length_mm" not in slots:
                slots["mounting_length_mm"] = int(bare_number_match.group(1))

            flow_match = re.search(
                r"(?:расход\w*|производительност\w*|подач\w*)[^\d]{0,18}"
                r"(\d+(?:[,.]\d+)?)\s*"
                r"(м3/ч|м³/ч|куб(?:а|ов)?(?:\s+в\s+час)?|л/мин|"
                r"литр\w*\s+в\s+минут\w*|л/ч|литр\w*\s+в\s+час)",
                text,
            )
            if not flow_match:
                flow_match = re.search(
                    r"(?<!\d)(\d+(?:[,.]\d+)?)\s*"
                    r"(л/мин|литр\w*\s+в\s+минут\w*|л/ч|"
                    r"литр\w*\s+в\s+час|м3/ч|м³/ч|"
                    r"куб(?:а|ов)?(?:\s+в\s+час)?)(?!\w)",
                    text,
                )
            if flow_match:
                flow_value = float(flow_match.group(1).replace(",", "."))
                unit = normalize_text(flow_match.group(2))
                if "л/мин" in unit or ("литр" in unit and "минут" in unit):
                    slots["required_flow_l_min"] = flow_value
                    slots["flow_unit_assumed"] = False
                    slots["flow_unit_status"] = "confirmed_per_minute"
                    flow_value = flow_value * 60.0 / 1000.0
                elif "л/ч" in unit:
                    flow_value = flow_value / 1000.0
                slots["required_flow_m3_h"] = round(flow_value, 4)
            else:
                symbolic_flow = re.search(
                    r"(?<![a-zа-я])q\s*(?:=\s*)?(\d+(?:[,.]\d+)?)\s*"
                    r"(?:м3/ч|м³/ч|куб(?:а|ов)?(?:\s+в\s+час)?|л/мин|л/ч|л/с)",
                    text,
                )
                if symbolic_flow:
                    slots["required_flow_m3_h"] = float(
                        symbolic_flow.group(1).replace(",", ".")
                    )
                household_flow = re.search(
                    r"(?:расход\w*[^\d]{0,18}(?:литр\w*|л\b)[^\d]{0,8}"
                    r"(\d+(?:[,.]\d+)?)|"
                    r"расход\w*[^\d]{0,18}(\d+(?:[,.]\d+)?)\s*литр\w*)",
                    text,
                )
                if household_flow and "required_flow_m3_h" not in slots:
                    raw_value = household_flow.group(1) or household_flow.group(2)
                    flow_l_min = float(raw_value.replace(",", "."))
                    slots["required_flow_l_min"] = flow_l_min
                    slots["required_flow_m3_h"] = round(flow_l_min * 60 / 1000, 4)
                    slots["flow_unit_assumed"] = True
                    slots["flow_unit_status"] = "assumed"

            symbolic_head = re.search(
                r"(?<![a-zа-я])h\s*(?:=\s*)?(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)?",
                text,
            )
            if symbolic_head:
                slots["required_head_m"] = float(
                    symbolic_head.group(1).replace(",", ".")
                )
                slots["required_head_calculated"] = False
                slots.pop("head_m", None)

            connection_match = re.search(
                r"(?:присоедин\w*|условн\w*\s+проход\w*)"
                r"[^\d]{0,12}(?:dn\s*)?(25|32|40|50)\b",
                text,
            )
            if connection_match:
                slots["connection_size"] = int(connection_match.group(1))

            required_head_match = re.search(
                r"(?:расчетн\w*|расчётн\w*|рабоч\w*|требуем\w*)\s+"
                r"напор\w*[^\d]{0,12}(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
                text,
            )
            if required_head_match:
                slots["required_head_m"] = float(
                    required_head_match.group(1).replace(",", ".")
                )
                slots["required_head_calculated"] = False
                # ``head_m`` means an exact pump marking (25/6); a calculated
                # duty head is a minimum capability and must not be compared as
                # exact equality with the product's maximum head.
                slots.pop("head_m", None)

            for key, pattern in [
                (
                    "static_water_level_m",
                    r"статическ\w*\s+уровен\w*[^\d]{0,15}"
                    r"(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
                ),
                (
                    "dynamic_water_level_m",
                    r"динамическ\w*\s+уровен\w*[^\d]{0,15}"
                    r"(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
                ),
                (
                    "lift_height_m",
                    r"(?:высот\w*\s+подъем\w*|высот\w*\s+подъём\w*|"
                    r"поднять|подъем|подъём|дом\w*\s+(?:наход\w*\s+)?выше|"
                    r"дом\w*\s+выше)[^\d]{0,18}"
                    r"(\d+(?:[,.]\d+)?)\s*(?:м|метр)",
                ),
            ]:
                match = re.search(pattern, text)
                if match:
                    slots[key] = float(match.group(1).replace(",", "."))

            # Prefer the value *after* a destination anchor.  Combining both
            # word orders in one regex made ``13 м, до полива 40 м`` match the
            # earlier ``13 м до полива`` fragment and bind the water depth as
            # the horizontal run.
            household_distance_after = re.search(
                r"(?:от\s+(?:колодц\w*|скважин\w*)\s+)?"
                r"до\s+(?:дом\w*|точк\w*\s+полив\w*|полив\w*)"
                r"[^\d]{0,15}(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)",
                text,
            )
            household_distance_before = re.search(
                r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)"
                r"[^,;.!?]{0,18}до\s+(?:дом\w*|полив\w*)",
                text,
            )
            if household_distance_after:
                slots["horizontal_run_m"] = float(
                    household_distance_after.group(1).replace(",", ".")
                )
            elif household_distance_before and "horizontal_run_m" not in slots:
                slots["horizontal_run_m"] = float(
                    household_distance_before.group(1).replace(",", ".")
                )
            elif "horizontal_run_m" not in slots:
                generic_horizontal = re.search(
                    r"(?:горизонтальн\w*\s+(?:трасс|участ|длин)|"
                    r"длин\w*\s+трасс\w*|"
                    r"расстоян\w*\s+по\s+горизонтал\w*|"
                    r"трасс\w*|шланг\w*)[^\d]{0,80}"
                    r"(\d+(?:[,.]\d+)?)\s*(?:м\b|метр\w*)",
                    text,
                )
                if generic_horizontal:
                    slots["horizontal_run_m"] = float(
                        generic_horizontal.group(1).replace(",", ".")
                    )

            if "фекал" in text:
                slots["water_quality"] = "фекальная"
            elif (
                "грязн" in text
                or "пес" in text
                or "мусор" in text
                or re.search(r"\bил(?:а|ом|ист\w*)?\b", text)
            ):
                slots["water_quality"] = "грязная"
            elif "чист" in text:
                slots["water_quality"] = "чистая"
            if (
                slots.get("water_quality") == "грязная"
                and not slots.get("pump_type")
                and not slots.get("water_source")
                and (
                    re.search(r"\bнасос\w*\s+для\b", text)
                    or any(marker in text for marker in ["откач", "подвал", "приям"])
                    or any(
                        slots.get(key) is not None
                        for key in [
                            "lift_height_m",
                            "horizontal_run_m",
                            "required_flow_m3_h",
                            "solids_mm",
                        ]
                    )
                )
            ):
                # Functional novice wording («насос для грязной воды с песком»)
                # is enough to enter the drainage funnel.  Borehole/well
                # requests stay separate because solids tolerance there must
                # be checked against the exact submersible model.
                slots["pump_type"] = "дренажный"
                slots["pump_use"] = "откачка воды"
            solids_match = re.search(
                r"(?:частиц\w*|включен\w*)[^\d]{0,16}(\d+(?:[,.]\d+)?)\s*мм",
                text,
            )
            if solids_match:
                slots["solids_mm"] = float(
                    solids_match.group(1).replace(",", ".")
                )
            discharge_diameter = re.search(
                r"(?:внутренн\w*\s+)?диаметр\w*\s+"
                r"(?:шланг\w*|труб\w*)?[^\d]{0,12}"
                r"(\d{2,3}(?:[,.]\d+)?)\s*мм|"
                r"(?:шланг\w*|труб\w*)\s+(\d{2,3}(?:[,.]\d+)?)\s*мм|"
                r"(?:пнд|hdpe|пэ\s*-?\s*100|pe\s*-?\s*100)\s+"
                r"(\d{2,3}(?:[,.]\d+)?)\s*мм",
                text,
            )
            if discharge_diameter:
                raw_diameter = next(
                    group for group in discharge_diameter.groups() if group is not None
                )
                slots["discharge_diameter_mm"] = float(
                    raw_diameter.replace(",", ".")
                )
            discharge_sdr = re.search(r"\bsdr\s*-?\s*(\d{1,2}(?:[,.]\d+)?)\b", text)
            if discharge_sdr:
                slots["discharge_sdr"] = float(
                    discharge_sdr.group(1).replace(",", ".")
                )
            if normalize_text(str(slots.get("pump_type") or "")) == "канализационная насосная установка":
                fixtures = [
                    label
                    for marker, label in [
                        ("унитаз", "унитаз"),
                        ("раковин", "раковина"),
                        ("мойк", "мойка"),
                        ("душ", "душ"),
                        ("ванн", "ванна"),
                        ("стиральн", "стиральная машина"),
                        ("посудомо", "посудомоечная машина"),
                    ]
                    if marker in text
                ]
                if fixtures:
                    slots["connected_fixtures"] = fixtures

            if any(marker in text for marker in ["старый насос", "замена", "на замен"]):
                slots["pump_selection_mode"] = "замена"
                slots["pump_selection_mode_explicit"] = True
            elif any(
                marker in text
                for marker in [
                    "новая система",
                    "с нуля",
                    "новый подбор",
                    "новый насос",
                    "новый циркуляцион",
                ]
            ):
                slots["pump_selection_mode"] = "новый подбор"
                slots["pump_selection_mode_explicit"] = True

            warm_floor_mentioned = bool(
                ("тепл" in text or "тёпл" in text)
                and re.search(r"\bпол(?:а|у|ом|е)?\b", text)
            )
            warm_floor_negated = bool(
                re.search(
                    r"\b(?:без|не\s+будет|не\s+нужен|не\s+нужно|исключи\w*|"
                    r"только\s+не)\s+[^.?!,;]{0,24}(?:тепл|тёпл)\w*"
                    r"[^,.?!;]{0,12}пол",
                    text,
                )
                or re.search(
                    r"\b(?:тепл|тёпл)\w*\s+пол\w*[^.?!,;]{0,20}"
                    r"(?:не\s+будет|не\s+нужен|не\s+нужно)",
                    text,
                )
            )
            if (
                "радиатор" in text
                and warm_floor_mentioned
                and not warm_floor_negated
            ):
                slots["system_type"] = "радиаторы и тёплый пол"
            elif "радиатор" in text:
                slots["system_type"] = "радиаторы"
            elif warm_floor_mentioned and not warm_floor_negated:
                slots["system_type"] = "тёплый пол"

        if category in {"pipes", "sewer", "fittings", "valves", "radiator_fittings"} or any(
            marker in text for marker in ["диаметр", "ø"]
        ):
            for diameter_match in re.finditer(
                r"(?:^|\s|dn\s*|d\s*|ø\s*)(\d{2,3})(?:\s*мм|\s|[,;.!?]|$)", text
            ):
                value = int(diameter_match.group(1))
                # A weak standalone-size guess must yield to any explicit unit.
                # This also avoids treating pressure/voltage as a diameter while
                # no longer confusing the Russian preposition «с» with Celsius.
                if numeric_span_has_incompatible_context(
                    text,
                    diameter_match.start(1),
                    diameter_match.end(1),
                    expected_families={"diameter", "length_mm"},
                ):
                    continue
                if 10 <= value <= 250:
                    slots["diameter_mm"] = value
                    break

        if category in {"pipes", "sewer"}:
            colloquial_diameter = re.search(
                r"(?<!\d)(\d{2,3})\s*[- ]?(?:я|й)(?![a-zа-я0-9])",
                text,
            )
            if colloquial_diameter:
                value = int(colloquial_diameter.group(1))
                if 10 <= value <= 250:
                    # Plumber shorthand «32-я» is the pipe size.  It must win
                    # over a later route length such as «трасса 40 метров».
                    slots["diameter_mm"] = value

        if category in {"valves", "radiator_fittings", "radiators", "fittings", "pipes"}:
            inch_match = INCH_SIZE_RE.search(text) or INTEGER_INCH_RE.search(text)
            if inch_match:
                slots["size_inch"] = re.sub(r"\s+", "", inch_match.group(1))
            else:
                spoken = self._spoken_inch_size(text)
                if spoken:
                    slots["size_inch"] = spoken
            thread_type = self._thread_type_from_text(text)
            if thread_type:
                slots["thread_type"] = thread_type
            if any(marker in text for marker in ["американк", "полусгон", "накидн"]):
                slots["union"] = True

        # Серия/модельное имя («BASE», «MINI») живёт в названии товара, а в
        # атрибутах фида её почти нет, поэтому запоминаем токен и учитываем его
        # при ранжировании как совпадение с названием.
        name_tokens = self._name_tokens_from_text(text)
        if name_tokens:
            slots["name_tokens"] = name_tokens

        if category in {"pipes", "sewer", "radiators"}:
            explicit_length = re.search(
                r"(?:длина|длиной|длину)\D{0,12}(\d{2,5})\s*мм",
                text,
            )
            dimension_pair = re.search(r"(\d{2,3})\s*[xх×*]\s*(\d{3,5})", text)
            length_candidates = (
                [
                    int(match.group(1))
                    for match in re.finditer(r"(\d{3,5})\s*мм", text)
                    if int(match.group(1)) >= 300
                ]
                if category in {"pipes", "sewer"}
                else []
            )
            if explicit_length:
                slots["length_mm"] = int(explicit_length.group(1))
            elif dimension_pair:
                slots["length_mm"] = int(dimension_pair.group(2))
                if category in {"pipes", "sewer"}:
                    slots["diameter_mm"] = int(dimension_pair.group(1))
            elif length_candidates:
                slots["length_mm"] = length_candidates[-1]

        if category == "fittings":
            fitting_pair = re.search(
                r"(?<!\d)(\d{2,3})\s*(?:[xх×-]|на)\s*(\d{2,3})(?!\d)", text
            )
            if fitting_pair:
                slots["diameter_mm"] = int(fitting_pair.group(1))
                slots["secondary_diameter_mm"] = int(fitting_pair.group(2))

        if category == "sewer" and slots.get("element_type") != "труба":
            branch_pair = re.search(
                r"(?<!\d)(\d{2,3})\s*(?:[xх×*]|на)\s*(\d{2,3})(?!\d)", text
            )
            if branch_pair:
                slots["diameter_mm"] = int(branch_pair.group(1))
                slots["secondary_diameter_mm"] = int(branch_pair.group(2))

        if category == "radiators":
            if "биметалл" in text:
                slots["radiator_type"] = "биметаллический"
            elif "алюмин" in text:
                slots["radiator_type"] = "алюминиевый"
            elif "панельн" in text:
                slots["radiator_type"] = "панельный"
            elif "стальн" in text:
                slots["radiator_type"] = "стальной"
            sections_match = re.search(r"(\d{1,2})\s*секц", text)
            if sections_match:
                slots["sections"] = int(sections_match.group(1))
            center_match = re.search(
                r"(?:межосев\w*|м\s*[/.-]?\s*о)\D{0,12}"
                r"(\d{2,4})(?:\s*мм)?",
                text,
            )
            if center_match:
                slots["radiator_size_mm"] = int(center_match.group(1))
            height_match = re.search(
                r"высот\w*\D{0,12}(\d{2,4})(?:\s*мм)?",
                text,
            )
            if height_match:
                slots["radiator_height_mm"] = int(height_match.group(1))
            elif not center_match and "length_mm" not in slots:
                standalone_mm = [
                    int(match.group(1))
                    for match in re.finditer(r"(?<!\d)(\d{2,4})\s*мм", text)
                    if int(match.group(1)) >= 300
                ]
                if standalone_mm:
                    slots["radiator_size_mm"] = standalone_mm[0]

        if any(word in text for word in CHEAP_WORDS):
            slots["cheap"] = True
        if "самый дешев" in text or "самого дешев" in text or "дешевле всех" in text:
            slots["sort_mode"] = "price_asc"
        if any(
            marker in text
            for marker in [
                "покажи дешевле",
                "есть дешевле",
                "аналог подешевле",
                "вариант подешевле",
                "дешевле предыдущ",
            ]
        ):
            slots["relative_cheaper"] = True
        if self._allows_unavailable_stock(text):
            # An explicit relaxation is a real correction, not absence of a
            # filter.  Persisting False lets it replace an earlier
            # ``только в наличии`` constraint in the active product branch.
            slots["in_stock"] = False
        elif any(word in text for word in STOCK_WORDS):
            slots["in_stock"] = True

        if category == "water_heaters" and (
            re.search(
                r"\bможно\s+(?:(?:показать|подобрать)\s+)?"
                r"(?:аналог\w*|альтернатив\w*)\b",
                text,
            )
            or re.search(
                r"\b(?:аналог\w*|альтернатив\w*)\s+"
                r"(?:можно|допустим\w*|разрешен\w*|разрешён\w*|подойд\w*)\b",
                text,
            )
            or re.search(
                r"\bразрешаю\s+(?:(?:показывать|подбирать)\s+)?"
                r"(?:аналог\w*|альтернатив\w*)\b",
                text,
            )
        ):
            # Consent only enables the alternative-search branch. Water-heater
            # volume/type/source, stock and budget remain hard predicates there;
            # changing one of them still requires a separate explicit choice.
            slots["allow_alternatives"] = True

        if category == "pumps" and "насос" in text and "pump_type" not in slots:
            slots["product_kind"] = "насос"

        # Category-specific notation is applied last so an explicit engineering
        # form (DN/PN, Q/H, 10BB, type 22) can correct a weaker generic number
        # guess without becoming a global text replacement.
        notation_slots = extract_engineering_notation(text, category)
        if (
            notation_slots.get("pressure_class_bar") is not None
            and re.search(r"\b(?:номинальн|условн)\w*\s+давлен", text)
            and not re.search(r"\bрабоч\w*\s+давлен", text)
        ):
            slots.pop("operating_pressure_bar", None)
        slots.update(notation_slots)

    def _extract_water_heater_slots(
        self,
        text: str,
        slots: dict[str, Any],
        *,
        has_electric: bool,
        has_gas: bool,
        rejects_electric: bool,
        rejects_gas: bool,
    ) -> None:
        """Extract water-heater constraints without leaking boiler slots.

        ``бойлер`` in ordinary customer speech is ambiguous, so the bare word
        establishes only the category.  A storage/flow/indirect qualifier is
        required before ``heater_type`` is set.
        """
        mentions_indirect = bool(
            re.search(r"\bкосвенн\w*(?:\s+нагрев\w*)?\b", text)
            or re.search(r"\bб\s*\.?\s*к\s*\.?\s*н\.?\b", text)
        )
        mentions_flow = bool(
            re.search(r"\bпроточн\w*\b", text)
            or re.search(r"\bгазов\w*\s+колонк\w*\b", text)
        )
        mentions_storage = bool(
            re.search(r"\bнакопительн\w*\b", text)
            or re.search(r"\bэ\s*\.?\s*в\s*\.?\s*н\.?\b", text)
        )
        rejects_flow = bool(re.search(r"\bне\s+проточн\w*\b", text))
        rejects_storage = bool(re.search(r"\bне\s+накопительн\w*\b", text))

        if mentions_indirect and not re.search(r"\bне\s+косвенн\w*\b", text):
            slots["heater_type"] = "косвенного нагрева"
            slots["energy_source"] = "косвенный"
        elif mentions_storage and not rejects_storage:
            slots["heater_type"] = "накопительный"
        elif mentions_flow and not rejects_flow:
            slots["heater_type"] = "проточный"

        mentions_combined = bool(re.search(r"\bкомбинированн\w*\b", text))
        if mentions_combined:
            slots["energy_source"] = "комбинированный"
            # In the water-heater feed "комбинированный" describes a tank
            # appliance heated from more than one source, not a third
            # storage/flow geometry. Keep the two dimensions separate.
            if "heater_type" not in slots:
                slots["heater_type"] = "накопительный"
        elif rejects_electric and has_gas:
            slots["energy_source"] = "газовый"
        elif (
            rejects_gas
            and slots.get("heater_type") != "косвенного нагрева"
        ):
            slots["energy_source"] = "электрический"
        elif has_gas and not has_electric and not rejects_gas:
            slots["energy_source"] = "газовый"
        elif has_electric and not has_gas and not rejects_electric:
            slots["energy_source"] = "электрический"
        elif re.search(r"\bэ\s*\.?\s*в\s*\.?\s*н\.?\b", text):
            slots["energy_source"] = "электрический"

        volume = self._extract_water_heater_volume_l(text)
        if volume is not None:
            slots["volume_l"] = volume

        has_wall = self._has_non_negated_match(text, r"\bнастенн\w*\b")
        has_floor = self._has_non_negated_match(text, r"\bнапольн\w*\b")
        has_under_sink = self._has_non_negated_match(
            text,
            r"\bпод\s+мойк\w*\b",
        )
        has_over_sink = self._has_non_negated_match(
            text,
            r"\bнад\s+мойк\w*\b",
        )
        if has_wall:
            slots["mounting"] = "настенный"
        elif has_floor:
            slots["mounting"] = "напольный"
        elif has_under_sink:
            slots["mounting"] = "под мойкой"
        elif has_over_sink:
            slots["mounting"] = "над мойкой"

        has_vertical = self._has_non_negated_match(
            text,
            r"\bвертикальн\w*\b",
        )
        has_horizontal = self._has_non_negated_match(
            text,
            r"\bгоризонтальн\w*\b",
        )
        if has_vertical and has_horizontal:
            slots["orientation"] = "универсальный"
        elif has_vertical:
            slots["orientation"] = "вертикальный"
        elif has_horizontal:
            slots["orientation"] = "горизонтальный"
        elif re.search(r"\bуниверсальн\w*\b", text):
            slots["orientation"] = "универсальный"

    @staticmethod
    def _extract_water_heater_volume_l(
        text: str,
        *,
        allow_unitless: bool = False,
    ) -> int | float | None:
        """Extract volume while preferring the replacement in corrections."""
        number = r"(\d{1,4}(?:[,.]\d+)?)"
        unit = r"(?:л\b|литр(?:а|ов)?\b)"
        correction = re.search(
            rf"\bне\s+{number}\s*(?:{unit})?"
            rf"\s*(?:,?\s*(?:а|но)\s+){number}\s*(?:{unit})?",
            text,
        )
        if correction and (
            allow_unitless
            or re.search(r"\b(?:л|литр(?:а|ов)?)\b", correction.group(0))
        ):
            value = float(correction.group(2).replace(",", "."))
            if 1 <= value <= 5000:
                return int(value) if value.is_integer() else value

        explicit_matches = list(
            re.finditer(
                rf"(?<!\d){number}\s*{unit}",
                text,
            )
        )
        for match in reversed(explicit_matches):
            before = text[max(0, match.start() - 12) : match.start()]
            if re.search(r"\bне\s*$", before):
                continue
            value = float(match.group(1).replace(",", "."))
            if 1 <= value <= 5000:
                return int(value) if value.is_integer() else value

        if not allow_unitless:
            return None
        unitless_values = re.findall(r"(?<!\d)\d{1,4}(?:[,.]\d+)?(?!\d)", text)
        if len(unitless_values) != 1:
            return None
        value = float(unitless_values[0].replace(",", "."))
        if not 1 <= value <= 5000:
            return None
        return int(value) if value.is_integer() else value

    @staticmethod
    def _extract_price_bound(text: str, *, upper: bool) -> float | None:
        """Extract an explicit monetary bound without confusing area/power with price."""
        unit_pattern = (
            r"(тыс(?:яч\w*)?|т\s*\.?\s*р\.?|к\b|руб\w*|р\b)"
        )
        value_pattern = r"(\d(?:[\d ]*\d)?(?:[,.]\d+)?)"
        if upper:
            patterns = [
                r"(?:не\s+дороже(?:\s+чем)?|максимум(?:\s+по\s+цене)?|"
                r"бюджет(?:ом)?(?:\s+до)?|в\s+пределах)\s*"
                + value_pattern
                + r"\s*"
                + unit_pattern
                + r"?",
                r"(?:по\s+цене\s+)?до\s*"
                + value_pattern
                + r"\s*"
                + unit_pattern,
            ]
        else:
            patterns = [
                r"(?:не\s+дешевле(?:\s+чем)?|минимум(?:\s+по\s+цене)?|"
                r"по\s+цене\s+от)\s*"
                + value_pattern
                + r"\s*"
                + unit_pattern
                + r"?",
            ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            raw_value = re.sub(r"\s+", "", match.group(1)).replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                continue
            slot_key = "max_price" if upper else "min_price"
            if not numeric_slot_has_compatible_context(
                slot_key,
                value,
                message=text,
            ):
                continue
            unit = normalize_text(match.group(2) or "") if match.lastindex and match.lastindex >= 2 else ""
            if unit.startswith("тыс") or unit == "к" or re.match(r"т\s*\.?\s*р", unit):
                value *= 1000
            if value > 0:
                return value
        if upper:
            # A bare four-plus-digit «до …» is normally a budget after
            # normalization removes the ₽ sign. Reject engineering units
            # explicitly and require a digit boundary to prevent 12000→1200
            # regex backtracking.
            match = re.search(
                r"\bдо\s*(\d(?: ?\d){3,7})(?!\d)",
                text,
            )
            if match:
                if numeric_span_has_incompatible_unit(
                    text,
                    match.end(1),
                    expected_families={"money"},
                ):
                    return None
                value = float(match.group(1).replace(" ", ""))
                if value > 0:
                    return value
        return None

    def _extract_standalone_pump_params(self, text: str, slots: dict[str, Any]) -> None:
        """Recognise plain `25/6 130` / `25-6 180` parameter shorthand without brand/series."""
        if slots.get("old_model"):
            return
        match = PUMP_PARAMS_RE.search(text)
        if not match:
            return
        connection_raw, head_raw, length = match.groups()
        connection = int(connection_raw)
        head = int(head_raw)
        if head > 12:
            return
        slots.setdefault("connection_size", connection)
        slots.setdefault("head_m", float(head))
        if length:
            slots.setdefault("mounting_length_mm", int(length))
        slots.setdefault("pump_type", "циркуляционный")

    def _extract_old_pump_model(self, text: str, slots: dict[str, Any]) -> None:
        match = OLD_CIRCULATION_PUMP_RE.search(text)
        if not match:
            return

        brand, series, connection, head_code, length = match.groups()
        model_parts = []
        if brand:
            model_parts.append(brand.upper())
            slots["old_model_brand"] = brand.upper()
        model_parts.append(series.upper().replace("UPС", "UPS").strip())
        model_parts.append(f"{connection}-{head_code}")
        if length:
            model_parts.append(length)

        slots["old_model"] = " ".join(model_parts)
        slots["pump_type"] = "циркуляционный"
        slots["connection_size"] = int(connection)
        slots["head_m"] = self._pump_head_from_model_code(head_code)
        if length:
            slots["mounting_length_mm"] = int(length)

        if (
            slots.get("brand")
            and slots.get("brand") == slots.get("old_model_brand")
            and any(marker in text for marker in ["стар", "альтернатив", "замен", "аналог", *CHEAP_WORDS])
        ):
            slots.pop("brand", None)

    def _pump_head_from_model_code(self, value: str) -> float:
        number = int(value)
        if number >= 20 and number % 10 == 0:
            return float(number // 10)
        return float(number)

    def _is_valid_sku_candidate(self, value: str) -> bool:
        normalized = normalize_text(value)
        if normalized in {"что-нибудь", "что нибудь", "какой-нибудь", "какой нибудь"}:
            return False
        if normalized.isalnum() and not normalized.isdigit():
            # Compact SKUs need multiple letter/digit runs (CMSR|02|CA|28).
            # Ordinary product phrases/models such as arderia|12 or ferroli|24
            # have only two runs and must remain natural-language searches.
            if not normalized.isascii():
                return False
            runs = re.findall(r"[a-z]+|\d+", normalized)
            if len(runs) < 3:
                return False
        return any(char.isdigit() for char in normalized)

    @staticmethod
    def _sku_candidate_is_measurement(text: str, value: str) -> bool:
        """Do not reinterpret a budget, quantity or engineering value as SKU."""
        candidate = normalize_text(value)
        if not candidate.isdigit():
            return False
        escaped = re.escape(candidate)
        return bool(
            re.search(
                rf"(?<!\d){escaped}\s*"
                r"(?:руб\w*|тыс\w*|к\b|шт\w*|мм\b|см\b|м\b|м2\b|"
                r"м²\b|квт\b|вт\b|вольт\w*|бар\b|л\b)",
                text,
            )
            or re.search(
                rf"\b(?:бюджет(?:ом)?|до|не\s+дороже|цена\s+до|за)\s+"
                rf"{escaped}\b",
                text,
            )
        )

    def _looks_like_attribute_followup(self, text: str) -> bool:
        markers = [
            "мм",
            "напор",
            "метр",
            "м ",
            "дюйм",
            "dn",
            "секц",
            "покажи дешевле",
            "дешевле",
            "подешевле",
            "не дороже",
            "бюджет",
            "по цене",
            "руб",
            "wi-fi",
            "wifi",
            "вай-фай",
            "в наличии",
            "для воды",
            "воды",
            "водоснаб",
            "отоплен",
            "канализац",
            "внутрен",
            "наруж",
            "дач",
            "полив",
            "скваж",
            "колод",
            "старый насос",
            "старая модель",
            "модель старого",
            "модель",
            "альтернатив",
            "аналог",
            "замен",
            "grundfos",
            "wilo",
            "ups",
            "upс",
            "перекрыв",
            "регулир",
            "температур",
            "слабый напор",
            "низкий напор",
            "давлен",
            "1/2",
            "3/4",
            "горяч",
            "холодн",
            "квт",
            "квадрат",
            "контурн",
            "гвс",
            "газ",
            "литр",
            "накопительн",
            "проточн",
            "косвенн",
            "настенн",
            "напольн",
            "вертикальн",
            "горизонтальн",
            "универсальн",
            "отдельн",
            "встроенн",
            "380",
            "220",
            "стар",
            "м/п",
            "металлопласт",
            "ппр",
            "pp-r",
            "ppr",
            "pex",
            "pe-x",
            "pe-rt",
            "внутри дом",
            "бар",
            "°c",
            "°с",
            "присоедин",
        ]
        if any(marker in text for marker in markers):
            return True
        if PUMP_PARAMS_RE.search(text):
            return True
        if re.fullmatch(r"\s*\d{2,4}\s*", text):
            return True
        return False

    def _looks_like_water_heater_followup(
        self,
        text: str,
        session: SessionState,
    ) -> bool:
        pending = normalize_text(session.pending_question or "")
        if any(
            marker in text
            for marker in [
                "водонагрев",
                "бойлер",
                "накопительн",
                "проточн",
                "косвенн",
                "электр",
                "газ",
                "литр",
                "настенн",
                "напольн",
                "вертикальн",
                "горизонтальн",
                "универсальн",
                "под мойк",
                "над мойк",
            ]
        ):
            return True
        if re.search(r"(?<!\d)\d{1,4}\s*л\b", text):
            return True
        if re.fullmatch(r"\d{1,4}(?:[,.]\d+)?", text) and any(
            marker in pending for marker in ["объем", "объём", "литр"]
        ):
            return True
        if (
            session.slots.get("volume_l") is not None
            or any(marker in pending for marker in ["объем", "объём", "литр"])
        ) and re.fullmatch(
            r"не\s+\d{1,4}(?:[,.]\d+)?\s*(?:,?\s*(?:а|но)\s+)"
            r"\d{1,4}(?:[,.]\d+)?",
            text,
        ):
            return True
        return False

    def _looks_like_boiler_type_followup(
        self,
        text: str,
        session: SessionState,
    ) -> bool:
        if not re.search(r"\b(?:электр\w*|газов\w*)\b", text):
            return False
        if re.search(
            r"\b(?:водонагрев\w*|водогре\w*|бойлер\w*|накопительн\w*|"
            r"проточн\w*|насос\w*|труб\w*|радиатор\w*)\b"
            r"|\b(?:тепл\w*|электрическ\w*)\s+пол\b",
            text,
        ):
            return False
        pending = normalize_text(session.pending_question or "")
        compact_answer = len(text.split()) <= 8
        return compact_answer or "газовый или электрический" in pending

    @staticmethod
    def _is_builtin_selection_constraint(text: str, category: str) -> bool:
        """Separate a requested built-in feature from a card-fact question."""
        if category != "boilers":
            return False
        mentions_component_constraint = bool(
            "встро" in text
            or re.search(
                r"\bбез(?:\s+\w+){0,3}\s+(?:насос|бак|групп\w*\s+безопасн|"
                r"(?:трех|3)[- ]?ходов\w*\s+клапан)",
                text,
            )
            or re.search(
                r"\bс\s+(?:насос|бак|групп\w*\s+безопасн|"
                r"(?:трех|3)[- ]?ходов\w*\s+клапан)",
                text,
            )
        )
        if not mentions_component_constraint:
            return False
        if any(
            marker in text
            for marker in [
                "есть ли",
                "входит ли",
                "что входит",
                "проверь",
                "по паспорту",
                "в этом котле",
                "у этого котла",
                "у него",
                "у показанного",
                "какие из",
                "у каких",
            ]
        ):
            return False
        return any(
            marker in text
            for marker in [
                "нужен",
                "нужна",
                "нужно",
                "подбери",
                "подберите",
                "покажи",
                "ищу",
                "вариант",
                "услови",
                "требуется",
                "хочу",
                "только",
                "электр",
                "газов",
                "220",
                "380",
                "до ",
                "бюджет",
                "один вариант",
            ]
        )

    def _is_bare_pump_assortment_question(self, text: str) -> bool:
        if "насос" not in text:
            return False
        assortment_markers = ["есть", "прода", "покажи", "подбери", "нужен", "нужны"]
        if not any(marker in text for marker in assortment_markers):
            return False
        card_context_markers = [
            "котл",
            "в комплект",
            "входит",
            "встро",
            "обвяз",
            "бак",
            "группа безопас",
            "там",
            "в нем",
            "в него",
            "у него",
            "туда",
        ]
        return not any(marker in text for marker in card_context_markers)

    def _normalize_result(
        self,
        result: IntentResult,
        message: str,
        session: SessionState | None,
    ) -> None:
        text = normalize_text(message)
        slots = result.slots
        explicit_category, explicit_score = self._detect_category(text)
        if explicit_category == "water_heaters" and explicit_score >= 0.9:
            result.category = "water_heaters"
            if result.intent_type in {"unknown", "small_talk"}:
                result.intent_type = "broad_category"
            result.confidence = max(result.confidence, explicit_score)
        if result.category == "other" and session and session.category and self._looks_like_attribute_followup(text):
            result.category = session.category
            if result.intent_type == "unknown":
                result.intent_type = "attribute_request"

        if (
            session
            and session.category == "sewer"
            and result.category == "other"
            and ("внутрен" in text or "наруж" in text)
        ):
            result.category = "sewer"
            if result.intent_type == "unknown":
                result.intent_type = "attribute_request"

        if (
            session
            and session.category == "sewer"
            and result.category == "pipes"
            and (
                any(
                    marker in text
                    for marker in [
                        "внутрен",
                        "наруж",
                        "отвод",
                        "тройник",
                        "муфта",
                        "канализац",
                    ]
                )
                or (
                    session.slots.get("sewer_scope")
                    and session.slots.get("element_type") == "труба"
                    and any(marker in text for marker in ["длин", "диаметр", "мм", "метр"])
                    and not any(
                        marker in text
                        for marker in ["отоплен", "водоснаб", "горяч", "холодн", "тепл", "тёпл"]
                    )
                )
            )
        ):
            result.category = "sewer"

        if not self._is_valid_sku_candidate(str(slots.get("sku", ""))):
            slots.pop("sku", None)
            if result.intent_type == "exact_sku":
                result.intent_type = "unknown"

        if "pressure" in slots and "head_m" not in slots:
            slots["head_m"] = self._to_float_slot(slots["pressure"])
        if "напор" in slots and "head_m" not in slots:
            slots["head_m"] = self._to_float_slot(slots["напор"])
        if "diameter" in slots and result.category == "pumps" and "mounting_length_mm" not in slots:
            slots["mounting_length_mm"] = self._to_int_slot(slots["diameter"])
        if "mounting_length" in slots and "mounting_length_mm" not in slots:
            slots["mounting_length_mm"] = self._to_int_slot(slots["mounting_length"])

        # A short answer such as "240" or "240 м" is an area only while the bot
        # is explicitly waiting for boiler area.  Without that pending question the
        # same number may be a model, an article or another parameter.
        pending = normalize_text(session.pending_question or "") if session else ""
        bare_area_match = re.fullmatch(
            r"(\d{2,4})(?:\s*(?:м|м2|м²|метр(?:а|ов)?))?",
            text,
        )
        if (
            session
            and session.category == "boilers"
            and "площад" in pending
            and bare_area_match
        ):
            area = float(bare_area_match.group(1))
            if 10 <= area <= 5000:
                result.category = "boilers"
                result.intent_type = "attribute_request"
                result.confidence = max(result.confidence, 0.9)
                slots["area_m2"] = area
                result.is_topic_change = False

        pending_volume = normalize_text(session.pending_question or "") if session else ""
        expects_volume = any(
            marker in pending_volume for marker in ["объем", "объём", "литр"]
        )
        corrects_known_volume = bool(
            session
            and session.slots.get("volume_l") is not None
            and re.search(
                r"\bне\s+\d{1,4}(?:[,.]\d+)?\s*"
                r"(?:,?\s*(?:а|но)\s+)\d{1,4}(?:[,.]\d+)?",
                text,
            )
        )
        contextual_volume = self._extract_water_heater_volume_l(
            text,
            allow_unitless=expects_volume or corrects_known_volume,
        )
        if (
            session
            and session.category == "water_heaters"
            and result.category in {"other", "water_heaters"}
            and contextual_volume is not None
            and (expects_volume or corrects_known_volume)
        ):
            result.category = "water_heaters"
            result.intent_type = "attribute_request"
            result.confidence = max(result.confidence, 0.9)
            slots["volume_l"] = contextual_volume
            result.is_topic_change = False

        pending_pair_relation = bool(
            session
            and session.category == "boilers"
            and "отдельн" in pending
            and "встро" in pending
        )
        if pending_pair_relation:
            if "отдельн" in text:
                result.category = "boilers"
                result.intent_type = "attribute_request"
                result.is_topic_change = False
                slots["boiler_water_heater_relation"] = "отдельные приборы"
            elif "встро" in text:
                result.category = "boilers"
                result.intent_type = "attribute_request"
                result.is_topic_change = False
                slots["boiler_water_heater_relation"] = "встроенный бойлер"

        if result.category in {
            "pumps",
            "valves",
            "radiator_fittings",
            "radiators",
            "fittings",
            "sewer",
            "pipes",
            "boilers",
            "water_heaters",
            "hydraulic_accumulators",
            "filters",
            "controls",
        }:
            self._extract_slots(text, result.category, slots)

        if (
            session
            and result.category == "sewer"
            and normalize_text(
                str(
                    slots.get("element_type")
                    or session.slots.get("element_type")
                    or ""
                )
            )
            == "отвод"
            and session.slots.get("diameter_mm") is not None
        ):
            bare_numbers = [
                int(value)
                for value in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text)
            ]
            standard_angles = {15, 22, 30, 45, 67, 87, 88, 90}
            explicit_diameter = bool(
                re.search(r"(?:диаметр\w*|\bdn|\bd\s*|ø)\D{0,8}\d|\d+\s*мм\b", text)
            )
            if (
                len(bare_numbers) == 1
                and bare_numbers[0] in standard_angles
                and not explicit_diameter
            ):
                # ``отвод 110`` -> ``внутренняя, 90`` means angle 90° while
                # the already confirmed DN110 remains in force. Reconcile it
                # before any accepted LLM interpretation is persisted.
                slots["angle_deg"] = bare_numbers[0]
                slots["diameter_mm"] = session.slots["diameter_mm"]

        if session and session.pending_category and not result.is_topic_change:
            contextual = extract_contextual_short_answer(
                text,
                result.category,
                session.pending_question,
                session.pending_slot_keys,
            )
            if "inlet_pressure_bar" in contextual:
                # Generic pump parsing treats an unqualified ``N бар`` as a
                # target.  The structured question is stronger evidence and
                # makes the two roles mutually exclusive for this turn.
                slots.pop("required_pressure_bar", None)
            slots.update(contextual)

        if session and result.category == "pumps":
            previous_pump_use = normalize_text(
                str(session.slots.get("pump_use") or "")
            )
            if (
                previous_pump_use == "полив"
                and any(
                    marker in text
                    for marker in ["колод", "скваж", "боч", "емкост"]
                )
                and not any(
                    marker in text
                    for marker in [
                        "отоплен",
                        "циркуляц",
                        "повысит",
                        "давлен",
                        "дренаж",
                        "откач",
                        "водоснаб",
                        "для дома",
                    ]
                )
            ):
                # A short source answer belongs to the existing irrigation
                # task; naming a well must not silently turn it into domestic
                # water supply.
                slots["pump_use"] = "полив"

            estimates_standard_hose = bool(
                "колод" in normalize_text(
                    str(
                        slots.get("water_source")
                        or session.slots.get("water_source")
                        or ""
                    )
                )
                and "шлан" in text
                and any(
                    marker in text
                    for marker in [
                        "посчитай сам",
                        "рассчитай сам",
                        "посчитайте сами",
                        "рассчитайте сами",
                        "стандартн",
                    ]
                )
            )
            if estimates_standard_hose:
                # A duration alone does not define volume, so use a clearly
                # stated preliminary duty point for one ordinary garden hose.
                # Twenty litres per minute is a conservative catalogue-sizing
                # assumption; 2 bar preserves useful pressure at the nozzle.
                slots["required_flow_l_min"] = 20.0
                slots["required_flow_m3_h"] = 1.2
                slots["flow_unit_assumed"] = False
                slots["flow_unit_status"] = "estimated_standard_hose"
                slots.setdefault("required_pressure_bar", 2.0)
                slots.setdefault("lift_height_m", 0.0)
                assumptions = list(
                    session.slots.get("engineering_assumptions") or []
                )
                for assumption in [
                    "один стандартный садовый шланг: расход 20 л/мин",
                    "давление у шланга: 2 бар",
                    "дополнительный перепад участка: 0 м",
                ]:
                    if assumption not in assumptions:
                        assumptions.append(assumption)
                slots["engineering_assumptions"] = assumptions
                result.category = "pumps"
                result.intent_type = "attribute_request"
                result.is_topic_change = False

            reference = normalize_text(
                str(session.slots.get("water_level_reference") or "")
            )
            if reference == "ambiguous":
                if re.search(
                    r"(?:от\s+(?:верха|края|поверхност\w*)|"
                    r"сверху\s+до\s+вод|глубин\w*\s+до\s+вод)",
                    text,
                ):
                    slots["water_level_reference"] = "from_top"
                    result.intent_type = "attribute_request"
                    result.is_topic_change = False
                elif re.search(
                    r"(?:от\s+дна|со\s+дна|столб\w*\s+вод)",
                    text,
                ):
                    slots["water_level_reference"] = "from_bottom"
                    result.intent_type = "attribute_request"
                    result.is_topic_change = False

            confirms_per_minute = bool(
                re.search(r"\b(?:да\s*,?\s*)?(?:именно\s+)?(?:в\s+)?минут\w*\b", text)
                and not re.search(r"\bне\s+(?:в\s+)?минут", text)
            )
            confirms_total_volume = bool(
                re.search(
                    r"\b(?:нет\s*,?\s*)?(?:это\s+)?(?:общ\w*\s+объем|"
                    r"общ\w*\s+объём|всего|суммарн\w*\s+объем|"
                    r"суммарн\w*\s+объём)\b",
                    text,
                )
            )
            if session.slots.get("flow_unit_assumed") and confirms_total_volume:
                slots["flow_unit_status"] = "total_volume"
                result.intent_type = "attribute_request"
                result.is_topic_change = False
            elif session.slots.get("flow_unit_assumed") and confirms_per_minute:
                litres = self._to_float_slot(session.slots.get("required_flow_l_min"))
                if litres is not None and litres > 0:
                    slots["required_flow_l_min"] = litres
                    slots["required_flow_m3_h"] = round(litres * 60.0 / 1000.0, 4)
                    slots["flow_unit_assumed"] = False
                    slots["flow_unit_status"] = "confirmed_per_minute"
                    result.intent_type = "attribute_request"
                    result.is_topic_change = False

        if session and (
            session.slots.get("project_scope") == "warm_floor"
            or session.slots.get("scope_funnel") == "warm_floor"
        ):
            if (
                result.category in {"pipes", "other"}
                and slots.get("area_m2") is not None
                and not re.search(
                    r"\b(?:кот[её]л|насос|радиатор|водонагрев|бойлер)\w*\b",
                    text,
                )
            ):
                slots["warm_floor_area_m2"] = slots["area_m2"]
                slots.pop("area_m2", None)
                slots["project_scope"] = "warm_floor"
                result.category = "pipes"
                result.intent_type = "attribute_request"
                result.is_topic_change = False
            correction = re.search(
                r"\bне\s+\d{1,4}(?:[,.]\d+)?\s*"
                r"(?:м2|м²|кв(?:\.?\s*м)?|квадрат\w*|метр\w*)?\s*"
                r"(?:,?\s*(?:а|но)\s+)(\d{1,4}(?:[,.]\d+)?)\s*"
                r"(?:м2|м²|кв(?:\.?\s*м)?|квадрат\w*|метр\w*)?\b",
                text,
            )
            if correction:
                area = float(correction.group(1).replace(",", "."))
                if 1 <= area <= 10000:
                    result.category = "pipes"
                    result.intent_type = "attribute_request"
                    result.is_topic_change = False
                    slots["project_scope"] = "warm_floor"
                    slots["warm_floor_area_m2"] = area

        if (
            session
            and result.category == "pumps"
            and normalize_text(str(slots.get("pump_type") or session.slots.get("pump_type") or ""))
            == "циркуляционный"
            and "head_m" not in slots
        ):
            bare_head = re.fullmatch(
                r"(\d+(?:[,.]\d+)?)\s*(?:м|метр(?:а|ов)?)", text
            )
            if bare_head and float(bare_head.group(1).replace(",", ".")) <= 20:
                slots["head_m"] = float(bare_head.group(1).replace(",", "."))

        slots.pop("pressure", None)
        slots.pop("напор", None)
        if result.category == "pumps":
            slots.pop("diameter", None)
        self._normalize_slot_values(slots)

    def _normalize_slot_values(self, slots: dict[str, Any]) -> None:
        boiler_type = normalize_text(str(slots.get("boiler_type") or ""))
        if boiler_type:
            if boiler_type in {"electric", "electrical", "electric boiler"} or "электр" in boiler_type:
                slots["boiler_type"] = "электрический"
            elif boiler_type in {"gas", "gas boiler"} or "газ" in boiler_type:
                slots["boiler_type"] = "газовый"

        contours = normalize_text(str(slots.get("contours") or ""))
        if contours:
            if (
                "двух" in contours
                or "2" == contours
                or "two" in contours
                or "double" in contours
                or "dual" in contours
            ):
                slots["contours"] = "двухконтурный"
            elif (
                "одно" in contours
                or "1" == contours
                or "one" in contours
                or "single" in contours
            ):
                slots["contours"] = "одноконтурный"

        # Accept a few common LLM field aliases, but keep one canonical slot in
        # the session so subsequent turns do not ask for an already supplied
        # water-heater type.
        if not slots.get("heater_type"):
            for alias in ["water_heater_type", "heating_type"]:
                if slots.get(alias):
                    slots["heater_type"] = slots[alias]
                    break
        slots.pop("water_heater_type", None)
        slots.pop("heating_type", None)
        heater_type = normalize_text(str(slots.get("heater_type") or ""))
        if heater_type:
            if "косвен" in heater_type or "indirect" in heater_type:
                slots["heater_type"] = "косвенного нагрева"
                slots.setdefault("energy_source", "косвенный")
            elif "проточ" in heater_type or "instant" in heater_type or "tankless" in heater_type:
                slots["heater_type"] = "проточный"
            elif "накоп" in heater_type or "storage" in heater_type or "tank" == heater_type:
                slots["heater_type"] = "накопительный"

        source = normalize_text(str(slots.get("energy_source") or ""))
        if source:
            if "электр" in source or source in {"electric", "electrical"}:
                slots["energy_source"] = "электрический"
            elif "газ" in source or source == "gas":
                slots["energy_source"] = "газовый"
            elif "комбин" in source or source in {"combined", "hybrid"}:
                slots["energy_source"] = "комбинированный"
            elif "косвен" in source or source == "indirect":
                slots["energy_source"] = "косвенный"

        if slots.get("volume_l") is not None:
            volume = self._to_float_slot(slots["volume_l"])
            if volume is None or not 1 <= volume <= 5000:
                slots.pop("volume_l", None)
            else:
                slots["volume_l"] = int(volume) if volume.is_integer() else volume

        mounting = normalize_text(str(slots.get("mounting") or ""))
        if mounting:
            if "настенн" in mounting or mounting == "wall":
                slots["mounting"] = "настенный"
            elif "напольн" in mounting or mounting == "floor":
                slots["mounting"] = "напольный"
            elif mounting == "under_sink" or "под мойк" in mounting:
                slots["mounting"] = "под мойкой"
            elif mounting == "over_sink" or "над мойк" in mounting:
                slots["mounting"] = "над мойкой"

        orientation = normalize_text(str(slots.get("orientation") or ""))
        if orientation:
            if "вертик" in orientation or orientation == "vertical":
                slots["orientation"] = "вертикальный"
            elif "горизонт" in orientation or orientation == "horizontal":
                slots["orientation"] = "горизонтальный"
            elif "универс" in orientation or orientation == "universal":
                slots["orientation"] = "универсальный"

    def _to_float_slot(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:[,.]\d+)?", str(value))
        if not match:
            return None
        return float(match.group(0).replace(",", "."))

    def _to_int_slot(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        match = re.search(r"\d+", str(value))
        if not match:
            return None
        return int(match.group(0))

    def _is_topic_change(
        self,
        category: str,
        message: str,
        session: SessionState | None,
    ) -> bool:
        if not session or not session.category or category in {"other", session.category}:
            return False
        text = normalize_text(message)
        explicit_change = any(word in text for word in TOPIC_CHANGE_WORDS)
        if session.pending_category == "pumps" and not explicit_change:
            return False
        if {category, session.category}.issubset({"pipes", "sewer", "fittings"}) and not explicit_change:
            return False
        return explicit_change or category != session.category

    def _sanity_check_llm_intent(
        self,
        llm_result: IntentResult,
        rule_result: IntentResult,
        message: str,
    ) -> IntentResult:
        """Reject LLM classifications that contradict obvious rule-based evidence.

        qwen3-vl-8b sometimes returns intents like `complectation` for vague phrases
        ("ну ты понял?"). We refuse such labels unless the original text has at
        least one anchor word the rule-based extractor would also accept.
        """
        text = normalize_text(message)
        sku_text = collapse_sku_spaces(text)
        original_semantics = (
            llm_result.intent_type,
            llm_result.category,
            json.dumps(llm_result.slots, ensure_ascii=False, sort_keys=True, default=str),
        )

        # The model occasionally echoes the schema enum string verbatim
        # ("exact_sku|brand_category|…") or invents a label/category. Snap any
        # out-of-vocabulary value back to the rule-based guess so the
        # orchestrator never receives a garbage intent_type/category.
        if llm_result.intent_type not in VALID_INTENTS:
            llm_result.intent_type = rule_result.intent_type
        if llm_result.category not in VALID_CATEGORIES:
            llm_result.category = rule_result.category

        # A syntactically valid LLM payload is still not measurement evidence.
        # Apply the same dimension guard as the engineering interpreter before
        # its slots can reach the rule/LLM merge (degrees are not money; a
        # fraction component is not a standalone temperature or diameter).
        for key, value in list(llm_result.slots.items()):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if not numeric_slot_has_compatible_context(
                key,
                float(value),
                message=message,
            ):
                llm_result.slots.pop(key, None)

        explicit_category, explicit_score = self._detect_category(text)
        if explicit_category == "water_heaters" and explicit_score >= 0.9:
            llm_result.category = "water_heaters"
            if llm_result.intent_type in {"unknown", "small_talk"}:
                llm_result.intent_type = "broad_category"
        conflicts_with_water_heater = bool(
            self._is_water_heater_accessory_request(text)
            or (
                re.search(
                    r"\b(?:накопительн|проточн)\w*\s+"
                    r"(?:фильтр|бак|емкост|резервуар)\w*\b",
                    text,
                )
                and not re.search(
                    r"\b(?:водонагрев\w*|водогре\w*|бойлер\w*|"
                    r"газов\w*\s+колонк\w*)\b",
                    text,
                )
            )
        )
        if (
            conflicts_with_water_heater
            and llm_result.category == "water_heaters"
            and rule_result.category != "water_heaters"
        ):
            llm_result.category = rule_result.category
            llm_result.intent_type = rule_result.intent_type
            for key in [
                "volume_l",
                "heater_type",
                "energy_source",
                "mounting",
                "orientation",
            ]:
                llm_result.slots.pop(key, None)

        # An energy adjective is a constraint, not a product family.  Outside
        # an existing boiler dialogue the model must not turn ``электрический``
        # (or ``нужен электрический``) into a boiler search.
        generic_electric_only = bool(
            "электр" in text
            and not re.search(
                r"\b(?:кот[её]л|котл\w*|кател\w*|boiler|водонагрев\w*|"
                r"водогре\w*|бойлер\w*|накопительн\w*|проточн\w*)\b",
                text,
            )
        )
        if (
            generic_electric_only
            and rule_result.category == "other"
            and llm_result.category == "boilers"
        ):
            llm_result.category = "other"
            llm_result.intent_type = rule_result.intent_type
            llm_result.slots.pop("boiler_type", None)

        if llm_result.intent_type == "complectation" and not any(
            word in text for word in COMPLECTATION_WORDS
        ):
            llm_result.intent_type = rule_result.intent_type
            llm_result.category = rule_result.category

        if llm_result.intent_type == "exact_sku":
            sku_match = (
                SKU_RE.search(sku_text)
                or NUMERIC_SKU_RE.search(sku_text)
                or SLASH_SKU_RE.search(sku_text)
                or ALPHANUM_SKU_RE.search(sku_text)
            )
            if not sku_match:
                llm_result.intent_type = rule_result.intent_type
                llm_result.slots.pop("sku", None)

        # Recent history is deliberately present in the classification prompt, but
        # it is context rather than evidence that the customer entered an article in
        # this turn.  Some models copied the previous product's SKU into ``slots`` on
        # replies such as "ну да".  Only accept an LLM-provided SKU when that exact
        # candidate is visible in the current message; ordinary contextual follow-ups
        # continue to work through ``session.last_products`` instead.
        llm_sku = llm_result.slots.get("sku")
        if llm_sku:
            current_skus = {
                normalize_sku(match.group(0))
                for pattern in (SKU_RE, NUMERIC_SKU_RE, SLASH_SKU_RE, ALPHANUM_SKU_RE)
                for match in pattern.finditer(sku_text)
                if self._is_valid_sku_candidate(match.group(0))
                and not self._sku_candidate_is_measurement(
                    text,
                    match.group(0),
                )
            }
            if normalize_sku(str(llm_sku)) not in current_skus:
                llm_result.slots.pop("sku", None)
                if llm_result.intent_type == "exact_sku":
                    llm_result.intent_type = rule_result.intent_type
                    llm_result.category = rule_result.category

        if llm_result.intent_type == "stock_request" and not any(
            word in text for word in STOCK_WORDS
        ):
            llm_result.intent_type = rule_result.intent_type

        if llm_result.intent_type == "link_request" and not any(
            word in text for word in LINK_WORDS
        ):
            llm_result.intent_type = rule_result.intent_type

        if llm_result.intent_type == "cheap_request" and not any(
            word in text for word in CHEAP_WORDS
        ):
            llm_result.intent_type = rule_result.intent_type

        if llm_result.intent_type == "out_of_scope" and not any(
            word in text for word in OUT_OF_SCOPE
        ) and rule_result.category != "other":
            llm_result.intent_type = rule_result.intent_type

        final_semantics = (
            llm_result.intent_type,
            llm_result.category,
            json.dumps(llm_result.slots, ensure_ascii=False, sort_keys=True, default=str),
        )
        if final_semantics != original_semantics:
            llm_result.raw = dict(llm_result.raw or {})
            llm_result.raw["llm_output_accepted"] = False
            llm_result.raw["llm_rejection_reason"] = "intent_sanity_check_override"
        return llm_result

    def _llm_fallback(
        self,
        message: str,
        fallback_result: IntentResult,
        session: SessionState | None = None,
    ) -> IntentResult:
        fallback = self._as_dict(fallback_result)
        schema_hint = {
            "intent_type": "exact_sku|brand_category|broad_category|cheap_request|stock_request|attribute_request|complectation|small_talk|out_of_scope|unknown",
            "category": "pipes|fittings|pumps|boilers|water_heaters|hydraulic_accumulators|filters|controls|valves|sewer|radiators|radiator_fittings|other",
            "slots": {
                "volume_l": "number|null",
                "heater_type": "storage|flow|indirect|null",
                "energy_source": "electric|gas|indirect|combined|null",
                "mounting": "wall|floor|under_sink|over_sink|null",
                "orientation": "vertical|horizontal|universal|null",
            },
            "flags": {},
            "confidence": 0.0,
        }
        context_part = ""
        if session and (session.history or session.project_context):
            context_lines = []
            for entry in session.history[-6:]:
                content = (entry.get("content") or "").strip()
                if not content:
                    continue
                if len(content) > 300:
                    content = content[:300] + "…"
                speaker = "Клиент" if entry.get("role") == "user" else "Бот"
                context_lines.append(f"{speaker}: {content}")
            if context_lines:
                context_part = "Недавний диалог:\n" + "\n".join(context_lines) + "\n"
            if session.project_context:
                context_part += (
                    "Структурированный контекст проекта: "
                    + json.dumps(
                        session.project_context,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                )
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты классификатор запросов интернет-магазина инженерной сантехники "
                    "(отопление, водоснабжение, канализация). "
                    "Классифицируй новое сообщение клиента с учётом недавнего диалога: "
                    "короткие уточнения (например «а подешевле?», «да», «второй», «давай») "
                    "продолжают предыдущую тему и наследуют её категорию. "
                    "Просьбы «помоги выбрать», «подбери всё», «составь список», «что нужно», "
                    "«нужно отопление/водоснабжение», «строю дом» — это broad_category "
                    "(консультация по системе), а не small_talk. "
                    "Учитывай отрицание: «газа нет», «без газа» означает отсутствие газа "
                    "(boiler_type должен быть электрический, не газовый). "
                    "Водонагреватель, отдельный бойлер, газовая колонка и бойлер "
                    "косвенного нагрева относятся к category=water_heaters. Слова "
                    "«накопительный» и «проточный» означают эту категорию только "
                    "когда относятся именно к водонагревателю: проточный фильтр или "
                    "накопительный бак — не водонагреватели. ТЭН, анод, клапан и "
                    "другие запчасти для бойлера — это не сам водонагреватель. "
                    "Фраза «котёл с бойлером» относится к category=boilers. "
                    "Гидроаккумулятор, гидробак и мембранный бак водоснабжения, "
                    "в том числе описанный как бак для поддержания давления и защиты "
                    "насоса от частых включений, относятся к "
                    "category=hydraulic_accumulators, а не pumps. "
                    "Слово «электрический» без названия товара само по себе не означает котёл. "
                    "Верни только JSON без Markdown. Не выдумывай факты."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Схема: {json.dumps(schema_hint, ensure_ascii=False)}\n"
                    f"{context_part}"
                    f"Новое сообщение клиента: {message}"
                ),
            },
        ]
        data, used = self.llm_client.complete_json("IntentRouterAgent", messages, fallback)
        json_accepted = bool(
            getattr(self.llm_client, "last_json_output_accepted", used)
        )
        try:
            result = IntentResult(**data)
            result.llm_used = used
            result.raw = dict(result.raw or {})
            result.raw["llm_output_accepted"] = json_accepted
            return result
        except Exception as exc:
            logger.warning("Invalid LLM intent payload: %s", exc)
            fallback_result.llm_used = used
            fallback_result.raw = dict(fallback_result.raw or {})
            fallback_result.raw["llm_output_accepted"] = False
            return fallback_result

    def _as_dict(self, result: IntentResult) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result.dict()
