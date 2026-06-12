from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models import HandoffSummary, ProductCard, SessionState


logger = logging.getLogger(__name__)


class HandoffAgent:
    def build_summary(
        self,
        user_message: str,
        session: SessionState,
        missing: list[str] | None = None,
        products: list[ProductCard] | None = None,
    ) -> HandoffSummary:
        considered = [card.sku for card in products or session.last_products]
        return HandoffSummary(
            wanted=user_message,
            known_slots=session.slots,
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
        status_part = (
            "Заявка зафиксирована, менеджер увидит историю запроса и свяжется с вами."
            if recorded
            else "Передайте, пожалуйста, ваш вопрос менеджеру напрямую — у меня не получилось сохранить заявку."
        )
        return (
            f"Передаю вопрос менеджеру.{context_part} {status_part} "
            "Пока я на связи — могу продолжить подбор по ассортименту."
        )

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

