from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.chat_logger import _file_lock, _interprocess_file_lock
from app.models import HandoffSummary, ProductCard, SessionState, model_to_dict

from .utils import normalize_text


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")

# Реквизиты — не контакт. Живой прогон: «ООО „Стройпоток“, ИНН 7714123456»
# попало в заявку как телефон покупателя (``контакт: ***3456``). Любая длинная
# последовательность цифр рядом с этими словами телефоном не считается.
_IDENTIFIER_CONTEXT_RE = re.compile(
    r"(?:инн|огрн(?:ип)?|кпп|окпо|бик|р\s*/?\s*с|к\s*/?\s*с|"
    r"расчетн\w*\s+счет\w*|расчётн\w*\s+счёт\w*|счет\w*\s+№|"
    r"номер\s+заказа|заказ\w*\s*№?)\s*[:№#-]?\s*$",
    re.IGNORECASE,
)

_HANDOFF_SLOT_ALLOWLIST = {
    "area_m2",
    "boiler_model",
    "boiler_requirement",
    "boiler_type",
    "boiler_volume_l",
    "brand",
    "connection_size",
    "contours",
    "diameter_mm",
    "excluded_builtin_parts",
    "excluded_features",
    "head_m",
    "max_price",
    "min_price",
    "mounting_length_mm",
    "old_model",
    "pipe_purpose",
    "power_kw",
    "pump_type",
    "pump_use",
    "required_builtin_parts",
    "required_features",
    "sewer_scope",
    "size_inch",
    "voltage_v",
    "warm_floor_area_m2",
    "warm_floor_contours",
    "water_source",
    "well_depth_m",
}

_SLOT_LABELS = {
    "area_m2": "площадь",
    "boiler_model": "модель котла",
    "boiler_requirement": "требование к котлу",
    "boiler_type": "тип котла",
    "boiler_volume_l": "объём бойлера",
    "brand": "бренд",
    "connection_size": "присоединение",
    "contours": "контурность",
    "diameter_mm": "диаметр",
    "excluded_builtin_parts": "без встроенных компонентов",
    "excluded_features": "без функций",
    "head_m": "напор",
    "max_price": "бюджет до",
    "min_price": "цена от",
    "mounting_length_mm": "монтажная длина",
    "old_model": "текущая модель",
    "pipe_purpose": "назначение трубы",
    "power_kw": "мощность",
    "pump_type": "тип насоса",
    "pump_use": "назначение насоса",
    "required_builtin_parts": "обязательные встроенные компоненты",
    "required_features": "обязательные функции",
    "sewer_scope": "тип канализации",
    "size_inch": "размер",
    "voltage_v": "напряжение",
    "warm_floor_area_m2": "площадь тёплого пола",
    "warm_floor_contours": "контуры тёплого пола",
    "water_source": "источник воды",
    "well_depth_m": "глубина",
}


@dataclass(frozen=True)
class HandoffRecordResult:
    success: bool
    ticket_id: str | None = None
    idempotency_key: str | None = None
    duplicate: bool = False


class HandoffAgent:
    def build_summary(
        self,
        user_message: str,
        session: SessionState,
        missing: list[str] | None = None,
        products: list[ProductCard] | None = None,
    ) -> HandoffSummary:
        considered = [card.sku for card in products or session.last_products]
        user_messages = [
            item.get("content", "").strip()
            for item in session.history
            if item.get("role") == "user" and item.get("content", "").strip()
        ]
        if not user_messages or user_messages[-1] != user_message.strip():
            user_messages.append(user_message.strip())
        unique_messages: list[str] = []
        for item in user_messages[-8:]:
            sanitized = self.redact_contact(item).strip()
            if (
                sanitized
                and sanitized not in unique_messages
                and not self._is_handoff_control_message(sanitized)
                and not self._is_contact_only(item)
            ):
                unique_messages.append(sanitized)
        # Передаём краткое содержательное описание, а не сырой полный диалог.
        wanted = " | ".join(unique_messages[-4:])
        if not wanted:
            wanted = f"Подбор по категории: {session.category or 'товары Vesta Trading'}"
        wanted = wanted[:800].rstrip()
        known_slots = {
            key: value
            for key, value in session.slots.items()
            if key in _HANDOFF_SLOT_ALLOWLIST and value not in (None, "", [], {})
        }
        requirements = self._extract_key_requirements(" ".join(unique_messages))
        if requirements:
            known_slots["key_requirements"] = "; ".join(requirements)
        # Контакт берётся из текущей реплики, а если его там нет — из того, что
        # покупатель уже называл в этом разговоре. Прежнее ограничение «только
        # текущая реплика» защищало от переноса чужого адреса из другой темы,
        # но отсекало и обычный случай: телефон называют в ответ на вопрос про
        # заказ, а «передай менеджеру» пишут ходом позже. Защита сохранена
        # иначе — подхваченный контакт показывается маской и требует согласия.
        contact = self.extract_contact(user_message) or (session.contact or None)
        return HandoffSummary(
            wanted=wanted,
            known_slots=known_slots,
            missing=missing or [],
            products_considered=considered,
            contact=contact,
        )

    def record(
        self,
        summary: HandoffSummary,
        session_id: str,
        path: Path,
    ) -> HandoffRecordResult:
        """Register one consented request and return a verifiable ticket id."""
        business_payload = {
            "wanted": summary.wanted,
            "known_slots": summary.known_slots,
            "missing": summary.missing,
            "products_considered": summary.products_considered,
            "contact": summary.contact,
        }
        fingerprint_source = json.dumps(
            business_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        idempotency_key = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        ticket_id = f"VT-{idempotency_key[:20].upper()}"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(path):
                with _interprocess_file_lock(path):
                    if path.exists():
                        for line in path.read_text(encoding="utf-8").splitlines():
                            try:
                                existing = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if existing.get("idempotency_key") == idempotency_key:
                                return HandoffRecordResult(
                                    success=True,
                                    ticket_id=str(existing.get("ticket_id") or ticket_id),
                                    idempotency_key=idempotency_key,
                                    duplicate=True,
                                )
                    entry = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "ticket_id": ticket_id,
                        "idempotency_key": idempotency_key,
                        "status": "locally_recorded",
                        "session_id": session_id,
                        **business_payload,
                    }
                    with path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            return HandoffRecordResult(
                success=True,
                ticket_id=ticket_id,
                idempotency_key=idempotency_key,
            )
        except OSError as exc:
            logger.warning("Cannot record handoff request: %s", exc)
            return HandoffRecordResult(
                success=False,
                idempotency_key=idempotency_key,
            )

    def compose_user_confirmation(
        self,
        summary: HandoffSummary,
        result: HandoffRecordResult,
    ) -> str:
        if not result.success or not result.ticket_id:
            return (
                "Заявку отправить не удалось, поэтому я не буду утверждать, что она передана. "
                "Можно продолжить подбор здесь или обратиться к менеджеру напрямую."
            )
        return (
            f"Локальный черновик обращения сохранён. Номер: {result.ticket_id}. "
            "Это не подтверждает получение менеджером: внешняя CRM в этом ответе "
            "не подтверждена. Сохранены только согласованные краткое описание и контакт."
        )

    def compose_consent_request(
        self,
        summary: HandoffSummary,
        *,
        needs_contact: bool,
    ) -> str:
        details: list[str] = []
        if summary.wanted:
            wanted = " ".join(summary.wanted.split())
            details.append(f"запрос: {wanted}")
        if summary.products_considered:
            details.append(f"рассматривали: {', '.join(summary.products_considered)}")
        known = ", ".join(
            f"{_SLOT_LABELS.get(key, key)}: {value}"
            for key, value in summary.known_slots.items()
            if not isinstance(value, bool)
        )
        if known:
            details.append(f"параметры: {known}")
        if summary.missing:
            details.append(f"требует проверки: {', '.join(summary.missing)}")
        preview = "; ".join(details) or "краткое описание вашего вопроса"
        if needs_contact:
            return (
                "Заявку менеджеру пока не отправляю. Подготовил данные для передачи: "
                f"{preview}. Оставьте телефон или email; после этого я покажу итог "
                "и попрошу подтвердить передачу."
            )
        return (
            "Заявку менеджеру пока не отправляю. Будут переданы только: "
            f"{preview}; контакт: {self.mask_contact(summary.contact)}. "
            "Подтвердите согласие фразой «подтверждаю передачу»."
        )

    def _has_contact_info(self, summary: HandoffSummary) -> bool:
        return bool(summary.contact)

    def extract_contact(self, text: str) -> str | None:
        email = _EMAIL_RE.search(text)
        if email:
            return email.group(0)
        for phone in _PHONE_RE.finditer(text):
            # Слева от числа стоит «ИНН», «ОГРН», «номер заказа»? Это реквизит,
            # а не способ связи, и в заявку он как контакт попасть не должен.
            prefix = text[max(0, phone.start() - 40) : phone.start()]
            if _IDENTIFIER_CONTEXT_RE.search(prefix.rstrip()):
                continue
            return phone.group(0).strip()
        return None

    @staticmethod
    def redact_contact(text: str) -> str:
        """Вырезать из описания способы связи, но не реквизиты запроса.

        ИНН, ОГРН и номер заказа — содержание просьбы, а не контакт: без них
        менеджер не поймёт, кому выставлять счёт. В живом прогоне ИНН уезжал
        в сводку как «[телефон удалён из описания]», и заявка теряла смысл.
        """
        text = _EMAIL_RE.sub("[email удалён из описания]", text)

        def _mask_phone(match: re.Match[str]) -> str:
            prefix = text[max(0, match.start() - 40) : match.start()]
            if _IDENTIFIER_CONTEXT_RE.search(prefix.rstrip()):
                return match.group(0)
            # Шаблон номера захватывает и разделители после последней цифры.
            # Возвращаем их на место, иначе соседние слова слипаются:
            # «ИНН [телефон удалён из описания]Коллекторы Valtec».
            trailing = match.group(0)[len(match.group(0).rstrip(" ().-")) :]
            return "[телефон удалён из описания]" + trailing

        return _PHONE_RE.sub(_mask_phone, text)

    @staticmethod
    def mask_contact(contact: str | None) -> str:
        if not contact:
            return "не указан"
        if "@" in contact:
            local, domain = contact.split("@", 1)
            visible = local[:1] + "***" if local else "***"
            return f"{visible}@{domain}"
        digits = re.sub(r"\D", "", contact)
        return f"***{digits[-4:]}" if digits else "***"

    def summary_to_dict(self, summary: HandoffSummary) -> dict:
        return model_to_dict(summary)

    @staticmethod
    def _is_handoff_control_message(text: str) -> bool:
        normalized = normalize_text(text)
        return any(
            marker in normalized
            for marker in [
                "передай менеджер",
                "передать менеджер",
                "позови менеджер",
                "соедини с менеджер",
                "подтверждаю передач",
                "не передава",
                "не сохраня",
            ]
        )

    def _is_contact_only(self, text: str) -> bool:
        contact = self.extract_contact(text)
        if not contact:
            return False
        remainder = normalize_text(text.replace(contact, ""))
        return len(remainder.split()) <= 4

    def _extract_key_requirements(self, text: str) -> list[str]:
        normalized = text.lower().replace("ё", "е")
        # Отрицание переворачивало требование и уезжало в CRM: «ГВС не нужна»
        # превращалось в «горячая вода/ГВС» среди параметров заявки, и менеджер
        # получал ровно противоположное тому, что сказал покупатель.
        negated: set[str] = set()
        for pattern, label in [
            (r"\bбез\s+(?:горяч\w*\s+вод\w*|гвс)\b", "горячая вода/ГВС"),
            (
                r"\b(?:горяч\w*\s+вод\w*|гвс)\b[^.!?]{0,18}не\s+(?:нужн\w*|будет|требуется)",
                "горячая вода/ГВС",
            ),
            (r"\bтолько\s+(?:для\s+)?отоплен\w*", "горячая вода/ГВС"),
            (r"\bбез\s+бойлер\w*\b", "с бойлером"),
            (r"\b(?:тепл|тепл)\w*\s+пол\w*[^.!?]{0,18}не\s+(?:нужн\w*|будет)", "тёплый пол"),
        ]:
            if re.search(pattern, normalized):
                negated.add(label)
        requirements: list[str] = []
        patterns = [
            (r"\bс\s+(?:встроенн\w*\s+)?бойлер\w*\b", "с бойлером"),
            (r"\bнуж\w*\s+(?:еще\s+|ещё\s+)?бойлер\w*\b", "с бойлером"),
            (r"\bбез\s+бойлер\w*\b", "без бойлера"),
            (r"\b(?:газа\s+нет|нет\s+газа|без\s+газа)\b", "газа нет"),
            (r"\bодноконтур\w*\b", "одноконтурный"),
            (r"\bдвухконтур\w*\b", "двухконтурный"),
            (r"\b(?:тепл\w*\s+пол|теплого\s+пола)\b", "тёплый пол"),
            (r"\bгоряч\w*\s+вод\w*\b|\bгвс\b", "горячая вода/ГВС"),
        ]
        for pattern, label in patterns:
            if label in negated:
                continue
            if re.search(pattern, normalized) and label not in requirements:
                requirements.append(label)
        return requirements

    def compose_answer(self, summary: HandoffSummary) -> str:
        missing = ", ".join(summary.missing) if summary.missing else "нужна проверка менеджера"
        known = ", ".join(
            f"{_SLOT_LABELS.get(key, key)}: {value}"
            for key, value in summary.known_slots.items()
        ) or "нет"
        products = ", ".join(summary.products_considered) or "не рассматривались"
        return (
            "Лучше передать вопрос менеджеру.\n"
            f"Кратко: пользователь хочет: {summary.wanted}. "
            f"Известно: {known}. Не хватает: {missing}. "
            f"Рассматривались товары: {products}."
        )
