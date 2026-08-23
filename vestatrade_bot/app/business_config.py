"""Проверяемые операционные факты компании: телефоны, часы работы, сроки.

Живой прогон показал, зачем это нужно. На просьбу «просто дайте телефон» бот
назвал `+7 (495) 123-45-67` и пообещал ответ менеджера за 15 минут; в другом
диалоге — «просчёт готовим в течение 24 часов». Ни один из этих фактов ниоткуда
не приходил: guardrails проверяли цены, остатки, артикулы и ссылки, а телефонов,
сроков и графика среди проверяемых сущностей не было вовсе.

Правило здесь то же, что уже действует для цен: модель может **пересказать**
факт из конфигурации, но не может его **создать**. Пока файл не заполнен,
любой такой факт в ответе считается выдуманным и вырезается — бот честно
говорит, что контакты и сроки подтвердит менеджер.

Формат ``data/business_config.json`` (путь настраивается через
``BUSINESS_CONFIG_PATH``); любой раздел можно опустить:

    {
      "phones": ["+7 495 000-00-00"],
      "emails": ["info@example.ru"],
      "business_hours": "пн-пт 9:00-18:00",
      "response_time": "в течение рабочего дня",
      "lead_times": {"просчёт спецификации": "1-2 рабочих дня"},
      "delivery": "по Москве в течение 1-3 рабочих дней",
      "pickup_points": ["Москва, ул. Примерная, 1"]
    }
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


logger = logging.getLogger(__name__)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


@dataclass(frozen=True)
class BusinessFacts:
    """Операционные факты, которые разрешено называть покупателю."""

    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    business_hours: str | None = None
    response_time: str | None = None
    lead_times: dict[str, str] = field(default_factory=dict)
    delivery: str | None = None
    pickup_points: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.phones,
                self.emails,
                self.business_hours,
                self.response_time,
                self.lead_times,
                self.delivery,
                self.pickup_points,
            ]
        )

    def knows_phone(self, value: str) -> bool:
        needle = _digits(value)
        if not needle:
            return False
        return any(_digits(phone) == needle for phone in self.phones)

    def states_duration(self, value: str) -> bool:
        """Есть ли такая формулировка срока среди подтверждённых."""
        needle = re.sub(r"\s+", " ", value).strip().casefold()
        if not needle:
            return False
        haystack = [self.response_time or "", self.delivery or "", *self.lead_times.values()]
        return any(needle in str(item).casefold() for item in haystack if item)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def load_business_facts(path: Path | None = None) -> BusinessFacts:
    source = path or (PROJECT_ROOT / "data" / "business_config.json")
    if not source.exists():
        return BusinessFacts()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        # A malformed config must not silently re-enable invented facts.
        logger.warning("Business config is unreadable (%s); treating as empty", type(exc).__name__)
        return BusinessFacts()
    if not isinstance(raw, dict):
        return BusinessFacts()
    lead_times = raw.get("lead_times")
    return BusinessFacts(
        phones=_as_tuple(raw.get("phones")),
        emails=_as_tuple(raw.get("emails")),
        business_hours=_as_text(raw.get("business_hours")),
        response_time=_as_text(raw.get("response_time")),
        lead_times={
            str(k): str(v) for k, v in lead_times.items()
        } if isinstance(lead_times, dict) else {},
        delivery=_as_text(raw.get("delivery")),
        pickup_points=_as_tuple(raw.get("pickup_points")),
    )


@lru_cache
def get_business_facts() -> BusinessFacts:
    return load_business_facts()
