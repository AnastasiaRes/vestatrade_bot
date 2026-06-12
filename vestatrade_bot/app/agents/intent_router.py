from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import IntentResult, SessionState
from app.openrouter_client import OpenRouterClient

from .utils import collapse_sku_spaces, normalize_text


logger = logging.getLogger(__name__)


SKU_RE = re.compile(r"\b[а-яa-z]{1,8}[а-яa-z0-9]*[.\-][а-яa-z0-9.\-]{2,}\b", re.IGNORECASE)
NUMERIC_SKU_RE = re.compile(r"\b\d{5,}\b")
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
INCH_SIZE_RE = re.compile(r"(?<!\d)(1\s*/\s*2|3\s*/\s*4|3\s*/\s*8|1\s*/\s*4)(?!\d)")
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
    "pumps": ["насос", "помпа", "циркуляц", "повысит", "дренаж", "скважин", "нсос"],
    "boilers": ["котел", "котёл", "котл", "boiler", "газовый", "электрический", "квт", "квадрат"],
    "valves": ["кран", "шаровый", "вентиль", "американк"],
    "radiator_fittings": ["радиатор", "батаре", "термоголов", "термостатическ", "клапан"],
    "pipes": ["труба", "трубы", "ppr", "полипропилен"],
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
    "спасибо",
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
    "встроен",
    "обвяз",
    "групп безопас",
    "группу безопас",
    "группа безопас",
    "безопасн",
]
LINK_WORDS = ["дай ссылку", "ссылку", "ссылка"]
TOPIC_CHANGE_WORDS = ["теперь", "а теперь", "еще нужен", "ещё нужен", "другой", "нужен"]


class IntentRouterAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        self._cache: dict[str, IntentResult] = {}

    def route(self, message: str, session: SessionState | None = None) -> IntentResult:
        normalized_message = normalize_text(message)
        if session:
            context_key = ":".join(
                [
                    session.category or "none",
                    session.last_intent or "-",
                    session.pending_intent_type or "-",
                ]
            )
        else:
            context_key = "none:-:-"
        cache_key = f"{context_key}:{normalized_message}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            result = IntentResult(**self._as_dict(cached))
            result.is_topic_change = self._is_topic_change(result.category, message, session)
            return result

        result = self._rule_based(message, session)
        if result.confidence < 0.55:
            llm_result = self._llm_fallback(message, result, session)
            llm_result = self._sanity_check_llm_intent(llm_result, result, message)
            result = llm_result
        self._normalize_result(result, message, session)
        self._cache[cache_key] = result
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

        sku_match = SKU_RE.search(sku_text) or NUMERIC_SKU_RE.search(sku_text)
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
        if slots.get("sku"):
            intent_type = "exact_sku"
            confidence = max(confidence, 0.95)
        elif any(word in text for word in LINK_WORDS):
            intent_type = "link_request"
            confidence = 0.9
        elif flags["in_stock"]:
            intent_type = "stock_request"
            confidence = max(confidence, 0.8)
        elif any(word in text for word in COMPLECTATION_WORDS):
            intent_type = "complectation"
            if session and session.category:
                category = session.category
            confidence = max(confidence, 0.85)
        elif flags["cheap"]:
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
        best_category = "other"
        best_score = 0.0
        for category, keywords in CATEGORY_KEYWORDS.items():
            hits = sum(1 for keyword in keywords if normalize_text(keyword) in text)
            if hits:
                score = min(0.95, 0.55 + hits * 0.15)
                if score > best_score:
                    best_category = category
                    best_score = score
        if best_category == "sewer" and "труба" in text:
            best_score = max(best_score, 0.9)
        return best_category, best_score

    def _extract_slots(self, text: str, category: str, slots: dict[str, Any]) -> None:
        if "циркуляц" in text:
            slots["pump_type"] = "циркуляционный"
        elif "повысит" in text:
            slots["pump_type"] = "повысительный"
        elif "дренаж" in text:
            slots["pump_type"] = "дренажный"
        elif "скважин" in text:
            slots["pump_type"] = "скважинный"

        if (
            "электр" in text
            or "газа нет" in text
            or "без газ" in text
            or "не газ" in text
            or "нет газ" in text
        ):
            slots["boiler_type"] = "электрический"
        elif "газ" in text:
            slots["boiler_type"] = "газовый"

        if "двухконтурн" in text:
            slots["contours"] = "двухконтурный"
        elif "одноконтурн" in text:
            slots["contours"] = "одноконтурный"
        elif category == "boilers" and ("гвс" in text or ("горяч" in text and "вод" in text)):
            slots["contours"] = "двухконтурный"

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
        if category == "pumps" and "насос" in text and "котл" in text:
            slots["pump_type"] = "циркуляционный"
            slots["pump_use"] = "отопление"
            slots["pump_context"] = "котел"
            slots["allow_basic_option"] = True
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

        for element in ["труба", "отвод", "тройник", "муфта"]:
            if element in text:
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
            area_meters_match = re.search(r"(\d{2,4})\s*метр", text)
            if area_meters_match:
                slots["area_m2"] = float(area_meters_match.group(1))

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
            bare_number_match = re.search(r"(?<!\d)(130|180)(?!\d)", text)
            if bare_number_match and "mounting_length_mm" not in slots:
                slots["mounting_length_mm"] = int(bare_number_match.group(1))

        if category in {"pipes", "sewer", "valves", "radiator_fittings"} or any(
            marker in text for marker in ["диаметр", "ø"]
        ):
            diameter_match = re.search(r"(?:^|\s|d|ø)(\d{2,3})(?:\s*мм|\s|$)", text)
            if diameter_match:
                tail = text[diameter_match.end(1) : diameter_match.end(1) + 12]
                value = int(diameter_match.group(1))
                if (
                    not re.match(r"\s*(?:м2|м²|кв|квадрат)", tail)
                    and 10 <= value <= 250
                ):
                    slots["diameter_mm"] = value

        if category in {"valves", "radiator_fittings"}:
            inch_match = INCH_SIZE_RE.search(text)
            if inch_match:
                slots["size_inch"] = re.sub(r"\s+", "", inch_match.group(1))

        length_match = re.search(r"(\d{3,5})\s*мм", text)
        if length_match and category in {"pipes", "sewer"}:
            slots["length_mm"] = int(length_match.group(1))

        if any(word in text for word in CHEAP_WORDS):
            slots["cheap"] = True
        if any(word in text for word in STOCK_WORDS):
            slots["in_stock"] = True

        if category == "pumps" and "насос" in text and "pump_type" not in slots:
            slots["product_kind"] = "насос"

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
        return any(char.isdigit() for char in normalized)

    def _looks_like_attribute_followup(self, text: str) -> bool:
        markers = [
            "мм",
            "напор",
            "метр",
            "м ",
            "покажи дешевле",
            "дешевле",
            "подешевле",
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

        if result.category in {"pumps", "valves", "radiator_fittings", "sewer", "pipes", "boilers"}:
            self._extract_slots(text, result.category, slots)

        slots.pop("pressure", None)
        slots.pop("напор", None)
        if result.category == "pumps":
            slots.pop("diameter", None)

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
        if {category, session.category}.issubset({"pipes", "sewer"}) and not explicit_change:
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

        if llm_result.intent_type == "complectation" and not any(
            word in text for word in COMPLECTATION_WORDS
        ):
            llm_result.intent_type = rule_result.intent_type
            llm_result.category = rule_result.category

        if llm_result.intent_type == "exact_sku":
            sku_match = SKU_RE.search(sku_text) or NUMERIC_SKU_RE.search(sku_text)
            if not sku_match:
                llm_result.intent_type = rule_result.intent_type
                llm_result.slots.pop("sku", None)

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
            "category": "pipes|pumps|boilers|valves|sewer|radiator_fittings|other",
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
                    "Ты классификатор запросов интернет-магазина инженерной сантехники. "
                    "Классифицируй новое сообщение клиента с учётом недавнего диалога: "
                    "короткие уточнения (например «а подешевле?», «да», «второй») "
                    "продолжают предыдущую тему и наследуют её категорию. "
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
        try:
            result = IntentResult(**data)
            result.llm_used = used
            return result
        except Exception as exc:
            logger.warning("Invalid LLM intent payload: %s", exc)
            fallback_result.llm_used = used
            return fallback_result

    def _as_dict(self, result: IntentResult) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result.dict()
