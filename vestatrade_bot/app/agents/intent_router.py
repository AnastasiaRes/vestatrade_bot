from __future__ import annotations

import json
import logging
import re
from threading import RLock
from typing import Any

from app.models import IntentResult, SessionState
from app.openrouter_client import OpenRouterClient

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
        "электрический",
        "квт",
        "квадрат",
    ],
    "valves": ["кран", "шаровый", "вентиль", "американк"],
    "radiator_fittings": ["термоголов", "термостатическ", "термост-ий", "клапан термост", "радиаторный клапан", "для рад", "для батаре", "д/рад"],
    "radiators": ["радиатор", "радиаторы", "батаре", "биметалл", "алюминиевый радиатор"],
    "fittings": ["фитинг", "угольник ppr", "муфта ppr", "тройник ppr", "переходник ppr"],
    # Stem form also covers ordinary inflections: ``трубу``, ``трубой``.
    "pipes": ["труб", "ppr", "полипропилен"],
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
    "есть на складе",
    "наличие",
    "сколько есть",
    "есть 2",
    "можно забрать",
    "забрать сегодня",
    "забрать прямо сейчас",
    "самовывоз",
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
LINK_WORDS = ["дай ссылку", "ссылку", "ссылка"]
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
    "valves",
    "sewer",
    "radiator_fittings",
    "radiators",
    "fittings",
    "other",
}


class IntentRouterAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        self._cache: dict[str, IntentResult] = {}
        self._cache_lock = RLock()

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

    def _rule_based(self, message: str, session: SessionState | None) -> IntentResult:
        text = normalize_text(message)
        sku_text = collapse_sku_spaces(text)
        flags = {
            "cheap": any(word in text for word in CHEAP_WORDS),
            "in_stock": any(word in text for word in STOCK_WORDS),
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
        if sku_match and self._is_valid_sku_candidate(sku_match.group(0)):
            slots["sku"] = sku_match.group(0)

        for brand in BRANDS:
            if normalize_text(brand) in text:
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
        if category == "other" and session and session.category and self._looks_like_attribute_followup(text):
            category = session.category
            category_score = 0.65
        symptom_match = any(symptom in text for symptom in SYMPTOM_KEYWORDS) or (
            "вода" in text and ("шла" in text or "иде" in text or "течет" in text)
        )
        if symptom_match:
            flags["symptom"] = True
            if category == "other":
                category = "pumps"
                category_score = max(category_score, 0.7)
        if category == "other" and PUMP_PARAMS_RE.search(text):
            category = "pumps"
            category_score = max(category_score, 0.7)
        self._extract_slots(text, category, slots)

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
            slots["allow_basic_option"] = True

        intent_type = "unknown"
        confidence = category_score
        if (
            slots.get("sku")
            and any(word in text for word in COMPLECTATION_WORDS)
            and not self._is_bare_pump_assortment_question(text)
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
        elif any(word in text for word in COMPLECTATION_WORDS) and not self._is_bare_pump_assortment_question(text):
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
    def _thread_type_from_text(text: str) -> str | None:
        """Canonical thread pairing asked for: ff (ВР/ВР), fm (ВР/НР), mm (НР/НР).

        ВР/ВН = внутренняя (female), НР/НАР = наружная (male). Customers write
        this a dozen ways, and until now it was dropped entirely — so «кран
        1/2" ВР/ВР» ranked a ВН/НР valve first just because it was cheaper.
        """
        female = r"(?:вр|вн)\.?"
        male = r"(?:нр|нар)\.?"
        if re.search(rf"\b{female}\s*[-/х]\s*{female}", text) or "ff" in text.split():
            return "ff"
        if re.search(rf"\b{female}\s*[-/х]\s*{male}", text) or re.search(
            rf"\b{male}\s*[-/х]\s*{female}", text
        ):
            return "fm"
        if re.search(rf"\b{male}\s*[-/х]\s*{male}", text) or "mm" in text.split():
            return "mm"
        return None

    @staticmethod
    def _name_tokens_from_text(text: str) -> list[str]:
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
            if token not in stop and token not in {brand.lower() for brand in BRANDS}
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

        if "циркуляц" in text:
            slots["pump_type"] = "циркуляционный"
            if category == "pumps":
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
        has_gas = "газ" in text
        rejects_electric = bool(re.search(r"\bне\s+электр", text))
        rejects_gas = bool(
            re.search(r"\bне\s+газ", text)
            or re.search(r"\bгаз[ауы]?\s+н[еэ]+т\w*\b", text)
            or re.search(r"\bн[еэ]+т\w*\s+газ[ауы]?\b", text)
            or "газа нет" in text
            or "без газ" in text
            or "нет газ" in text
        )
        if rejects_electric and has_gas:
            slots["boiler_type"] = "газовый"
        elif rejects_gas:
            slots["boiler_type"] = "электрический"
        elif has_gas and not has_electric:
            slots["boiler_type"] = "газовый"
        elif has_electric and not has_gas:
            slots["boiler_type"] = "электрический"

        if "двухконтурн" in text:
            slots["contours"] = "двухконтурный"
            slots["allow_alternatives"] = False
        elif "одноконтурн" in text:
            slots["contours"] = "одноконтурный"
            slots["allow_alternatives"] = False

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

        if "внутрен" in text:
            slots["sewer_scope"] = "внутренняя"
        elif "наруж" in text:
            slots["sewer_scope"] = "наружная"

        if category in {"pipes", "sewer"} and "канализац" in text:
            slots["pipe_purpose"] = "канализация"
        elif category in {"pipes", "sewer"} and "отоплен" in text:
            slots["pipe_purpose"] = "отопление"
        elif category in {"pipes", "sewer"} and (
            "водоснаб" in text or "для воды" in text or "горяч" in text or "холодн" in text
        ):
            slots["pipe_purpose"] = "водоснабжение"

        if category == "pipes":
            if "горяч" in text:
                slots["water_temperature"] = "горячая"
            elif "холод" in text:
                slots["water_temperature"] = "холодная"
            if "бел" in text:
                slots["pipe_color"] = "белая"

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
            r"(?:глубин\w*[^\d]{0,20}|скваж\w*[^\d]{0,30})(\d{1,3})"
            r"(?:\s*(?:м\b|метр\w*))?",
            text,
        )
        if well_depth_match and slots.get("water_source") == "скважина":
            slots["well_depth_m"] = float(well_depth_match.group(1))
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
            slots["allow_basic_option"] = True
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
            or "давлен" in text
        ):
            slots["pump_use"] = "повышение давления"
            slots["symptom"] = "слабый напор"
            if "дом" in text:
                slots["application"] = "дом"
        elif category == "pumps" and ("скваж" in text or "колод" in text):
            slots["pump_use"] = "водоснабжение"
        elif category == "pumps" and (
            "вода не ид" in text
            or "вода шла" in text
            or "вода" in text and ("ид" in text or "шла" in text)
        ):
            slots["pump_use"] = "водоснабжение"
            slots["symptom"] = "проблема с подачей воды"
        elif category == "pumps" and "водоснаб" in text:
            slots["pump_use"] = "водоснабжение"
        elif category == "pumps" and "полив" in text:
            slots["pump_use"] = "полив"
        elif category == "pumps" and ("откач" in text or "дренаж" in text):
            slots["pump_use"] = "откачка воды"

        for marker, element in [
            ("труба", "труба"),
            ("отвод", "отвод"),
            ("тройник", "тройник"),
            ("муфт", "муфта"),
        ]:
            if marker in text:
                slots["element_type"] = element
                break

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
        elif "регулир" in text or "температур" in text:
            slots["thermostatic_head"] = True
            slots["radiator_action"] = "регулировать температуру"

        area_match = re.search(
            r"(\d{2,4})\s*(?:м2|м²|квадрат|кв\.?\s*м|кв\b)",
            text,
        )
        if area_match:
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

        if category in {"pipes", "sewer"}:
            total_length_match = re.search(
                r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:м\b|метр(?:а|ов)?)(?!\s*(?:2|²|м))",
                text,
            )
            if total_length_match:
                slots["total_length_m"] = float(
                    total_length_match.group(1).replace(",", ".")
                )

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
            mounting_match = re.search(r"(\d{2,3})\s*мм", text)
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

        if category in {"pipes", "sewer", "fittings", "valves", "radiator_fittings"} or any(
            marker in text for marker in ["диаметр", "ø"]
        ):
            for diameter_match in re.finditer(
                r"(?:^|\s|dn\s*|d\s*|ø\s*)(\d{2,3})(?:\s*мм|\s|$)", text
            ):
                tail = text[diameter_match.end(1) : diameter_match.end(1) + 12]
                value = int(diameter_match.group(1))
                # Число с единицей не-размера (угол, температура, объём, секции)
                # не должно превращаться в диаметр — идём к следующему кандидату.
                if re.match(
                    r"\s*(?:м\b|метр|м2|м²|кв|квадрат|градус|°|литр|л\b|секц)",
                    tail,
                ):
                    continue
                if 10 <= value <= 250:
                    slots["diameter_mm"] = value
                    break

        if category in {"valves", "radiator_fittings", "radiators", "fittings"}:
            inch_match = INCH_SIZE_RE.search(text) or INTEGER_INCH_RE.search(text)
            if inch_match:
                slots["size_inch"] = re.sub(r"\s+", "", inch_match.group(1))
            thread_type = self._thread_type_from_text(text)
            if thread_type:
                slots["thread_type"] = thread_type

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
            sections_match = re.search(r"(\d{1,2})\s*секц", text)
            if sections_match:
                slots["sections"] = int(sections_match.group(1))
            center_match = re.search(
                r"(?:межосев\w*|высот\w*)\D{0,12}(\d{2,4})\s*мм",
                text,
            )
            if center_match:
                slots["radiator_size_mm"] = int(center_match.group(1))
            elif "length_mm" not in slots:
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
        if any(word in text for word in STOCK_WORDS):
            slots["in_stock"] = True

        if category == "pumps" and "насос" in text and "pump_type" not in slots:
            slots["product_kind"] = "насос"

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
                tail = text[match.end() :].lstrip()
                if re.match(
                    r"(?:м(?:2|²)?\b|мм\b|квт\b|вт\b|вольт\w*\b|в\b|л\b|бар\b)",
                    tail,
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
            "380",
            "220",
            "стар",
        ]
        if any(marker in text for marker in markers):
            return True
        if PUMP_PARAMS_RE.search(text):
            return True
        if re.fullmatch(r"\s*\d{2,4}\s*", text):
            return True
        return False

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
            and any(
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

        if result.category in {
            "pumps",
            "valves",
            "radiator_fittings",
            "radiators",
            "fittings",
            "sewer",
            "pipes",
            "boilers",
        }:
            self._extract_slots(text, result.category, slots)

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
            "category": "pipes|fittings|pumps|boilers|valves|sewer|radiators|radiator_fittings|other",
            "slots": {},
            "flags": {},
            "confidence": 0.0,
        }
        context_part = ""
        if session and session.history:
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
