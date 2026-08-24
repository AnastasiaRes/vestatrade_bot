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
class Branch:
    """Одна точка выдачи: адрес, телефоны и режим работы.

    Точек больше десяти и они в разных городах, поэтому «наш телефон» и «наши
    часы работы» без города — бессмысленный ответ. Бот сначала спрашивает
    город, потом называет конкретную точку.
    """

    region: str
    city: str
    address: str
    phones: tuple[str, ...] = ()
    hours: str | None = None

    def describe(self) -> str:
        parts = [self.address]
        if self.phones:
            parts.append("тел. " + ", ".join(self.phones))
        if self.hours:
            parts.append(self.hours)
        return " — ".join(parts)


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
    branches: tuple[Branch, ...] = ()
    # Правила доставки по регионам: ключ — ``region`` филиала.
    delivery_by_region: dict[str, str] = field(default_factory=dict)
    payment: str | None = None
    returns: str | None = None
    warranty: str | None = None
    # Адрес сайта и дата сверки фактов. Конфиг — снимок: часы работы, тарифы
    # и состав точек меняются, и называть их без указания источника значит
    # обещать актуальность, которой у файла нет.
    site_url: str | None = None
    facts_verified_on: str | None = None
    # Разделы, которые владелец ещё не вычитал. Бот их называет, но здесь
    # видно, что это черновик, а не подтверждённая политика компании.
    drafted_sections: tuple[str, ...] = ()

    def cities(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(branch.city for branch in self.branches))

    def branches_in(self, city: str) -> tuple[Branch, ...]:
        needle = str(city or "").strip().casefold()
        if not needle:
            return ()
        return tuple(
            branch
            for branch in self.branches
            if needle in branch.city.casefold() or needle in branch.address.casefold()
        )

    def volatile_caveat(self) -> str | None:
        """Оговорка к фактам, которые устаревают: часы, адреса, тарифы.

        Без неё бот выдаёт снимок конфигурации за текущее состояние компании.
        Ссылка на сайт даёт покупателю проверяемый источник, а не просьбу
        поверить на слово.
        """
        if not self.site_url:
            return None
        site = self.site_url.replace("https://", "").replace("http://", "").rstrip("/")
        return (
            "Адреса, режим работы и тарифы могли измениться — "
            f"актуальные смотрите на {site} или уточните у менеджера."
        )

    def draft_caveat(self, section: str) -> str | None:
        """Оговорка к разделу, который владелец ещё не подтвердил.

        Условия оплаты, возврата и гарантии покупатель воспринимает как
        обязательство магазина. Пока раздел числится черновиком, бот обязан
        сказать, что итог подтверждает менеджер, а не подавать его как
        окончательную политику. Как только раздел уходит из
        ``drafted_sections``, оговорка исчезает сама.
        """
        if section not in self.drafted_sections:
            return None
        # Текст политики уже говорит, что итог подтверждает менеджер. Дублировать
        # это здесь — лишняя фраза; полезное, чего в нём нет, — проверяемый
        # источник полных условий.
        site = (self.site_url or "").replace("https://", "").replace("http://", "").rstrip("/")
        if not site:
            return None
        return f"Полные условия — на {site}."

    def delivery_for(self, region: str | None) -> str | None:
        if region and region in self.delivery_by_region:
            return self.delivery_by_region[region]
        return self.delivery

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
                self.branches,
                self.delivery_by_region,
                self.payment,
                self.returns,
                self.warranty,
            ]
        )

    def knows_phone(self, value: str) -> bool:
        needle = _digits(value)
        if not needle:
            return False
        return any(_digits(phone) == needle for phone in self.phones)

    def knows_email(self, value: str) -> bool:
        """Whether an email is an explicitly configured company channel."""

        needle = str(value or "").strip().casefold().rstrip(".,;:")
        if not needle:
            return False
        return any(
            needle == str(email or "").strip().casefold().rstrip(".,;:")
            for email in self.emails
        )

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
    branches = tuple(
        Branch(
            region=str(item.get("region") or "").strip(),
            city=str(item.get("city") or "").strip(),
            address=str(item.get("address") or "").strip(),
            phones=_as_tuple(item.get("phones")),
            hours=_as_text(item.get("hours")),
        )
        for item in (raw.get("branches") or [])
        if isinstance(item, dict) and str(item.get("address") or "").strip()
    )
    delivery_raw = raw.get("delivery")
    delivery_by_region: dict[str, str] = {}
    delivery_text: str | None = None
    if isinstance(delivery_raw, dict):
        delivery_by_region = {str(k): str(v) for k, v in delivery_raw.items()}
    else:
        delivery_text = _as_text(delivery_raw)
    # Телефоны и адреса филиалов — те же проверяемые факты: guard'у нужен
    # плоский список, чтобы не вырезать из ответа настоящий номер точки.
    branch_phones = tuple(
        dict.fromkeys(phone for branch in branches for phone in branch.phones)
    )
    return BusinessFacts(
        phones=_as_tuple(raw.get("phones")) + branch_phones,
        emails=_as_tuple(raw.get("emails")),
        business_hours=_as_text(raw.get("business_hours")),
        response_time=_as_text(raw.get("response_time")),
        lead_times={
            str(k): str(v) for k, v in lead_times.items()
        } if isinstance(lead_times, dict) else {},
        delivery=delivery_text,
        pickup_points=_as_tuple(raw.get("pickup_points"))
        or tuple(branch.address for branch in branches),
        branches=branches,
        delivery_by_region=delivery_by_region,
        payment=_as_text(raw.get("payment")),
        returns=_as_text(raw.get("returns")),
        warranty=_as_text(raw.get("warranty")),
        drafted_sections=_as_tuple(raw.get("drafted_sections")),
        site_url=_as_text(raw.get("site_url")),
        facts_verified_on=_as_text(raw.get("facts_verified_on")),
    )


@lru_cache
def get_business_facts() -> BusinessFacts:
    return load_business_facts()
