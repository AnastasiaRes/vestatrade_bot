from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from app.models import HandoffSummary, ProductCard, SessionState


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")


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
        # Сохраняем исходные пользовательские формулировки, а не только последнюю
        # команду «передай менеджеру». Это не даёт потерять «с бойлером», отрицания
        # и другие обязательные условия во время уточняющих ходов.
        unique_messages: list[str] = []
        for item in user_messages[-8:]:
            if item and item not in unique_messages:
                unique_messages.append(item)
        wanted = " | ".join(unique_messages) or user_message
        known_slots = dict(session.slots)
        requirements = self._extract_key_requirements(" ".join(unique_messages))
        if requirements:
            known_slots["key_requirements"] = "; ".join(requirements)
        return HandoffSummary(
            wanted=wanted,
            known_slots=known_slots,
            missing=missing or [],
            products_considered=considered,
        )

    def record(self, summary: HandoffSummary, session_id: str, path: Path) -> bool:
        """Append the handoff request to a JSONL log the manager team can process."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "wanted": summary.wanted,
                "known_slots": {
                    key: value
                    for key, value in summary.known_slots.items()
                    if not isinstance(value, bool)
                },
                "missing": summary.missing,
                "products_considered": summary.products_considered,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            return True
        except OSError as exc:
            logger.warning("Cannot record handoff request: %s", exc)
            return False

    def compose_user_confirmation(self, summary: HandoffSummary, recorded: bool) -> str:
        details: list[str] = []
        if summary.wanted:
            wanted = " ".join(summary.wanted.split())
            if len(wanted) > 260:
                wanted = wanted[:257].rstrip() + "…"
            details.append(f"запрос: {wanted}")
        if summary.products_considered:
            details.append(f"рассматривали: {', '.join(summary.products_considered[:3])}")
        known = ", ".join(
            f"{key}: {value}"
            for key, value in summary.known_slots.items()
            if not isinstance(value, bool)
        )
        if known:
            details.append(f"параметры: {known}")
        context_part = f" Сохранил контекст диалога ({'; '.join(details)})." if details else ""
        if recorded and self._has_contact_info(summary):
            status_part = "Заявка зафиксирована: менеджер увидит историю запроса и контакт для связи."
        elif recorded:
            status_part = (
                "Я сохранил обращение и историю диалога для менеджера. "
                "Чтобы менеджер мог связаться с вами, оставьте телефон, email или удобный способ связи."
            )
        else:
            status_part = (
                "Передайте, пожалуйста, ваш вопрос менеджеру напрямую — "
                "у меня не получилось сохранить заявку."
            )
        return (
            f"Передаю вопрос менеджеру.{context_part} {status_part} "
            "Пока я на связи — могу продолжить подбор по ассортименту."
        )

    def _has_contact_info(self, summary: HandoffSummary) -> bool:
        values = [summary.wanted, *(str(value) for value in summary.known_slots.values())]
        text = " ".join(values)
        return bool(_EMAIL_RE.search(text) or _PHONE_RE.search(text))

    def _extract_key_requirements(self, text: str) -> list[str]:
        normalized = text.lower().replace("ё", "е")
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
            if re.search(pattern, normalized) and label not in requirements:
                requirements.append(label)
        return requirements

    def compose_answer(self, summary: HandoffSummary) -> str:
        missing = ", ".join(summary.missing) if summary.missing else "нужна проверка менеджера"
        known = ", ".join(f"{key}: {value}" for key, value in summary.known_slots.items()) or "нет"
        products = ", ".join(summary.products_considered) or "не рассматривались"
        return (
            "Лучше передать вопрос менеджеру.\n"
            f"Кратко: пользователь хочет: {summary.wanted}. "
            f"Известно: {known}. Не хватает: {missing}. "
            f"Рассматривались товары: {products}."
        )
