from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

try:  # pragma: no cover - available on the Linux/macOS deployment targets
    import fcntl
except ImportError:  # pragma: no cover - defensive Windows fallback
    fcntl = None

from app.config import Settings


logger = logging.getLogger(__name__)


class BudgetManager:
    _RESERVATION_TTL_SECONDS = 600.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.usage_budget_path
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock = RLock()

    def _today(self) -> str:
        return datetime.now().date().isoformat()

    def _empty_record(self) -> dict[str, Any]:
        return {
            "date": self._today(),
            "spent_usd": 0.0,
            "calls": [],
            "reservations": [],
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_record()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read budget file %s: %s", self.path, exc)
            return self._empty_record()
        if data.get("date") != self._today():
            return self._empty_record()
        data.setdefault("spent_usd", 0.0)
        data.setdefault("calls", [])
        data.setdefault("reservations", [])
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @contextmanager
    def _locked_record(self) -> Iterator[dict[str, Any]]:
        """Lock the budget ledger across threads *and* worker processes."""

        with self._lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    data = self._read()
                    self._prune_reservations(data)
                    yield data
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _prune_reservations(self, data: dict[str, Any]) -> None:
        cutoff = time.time() - self._RESERVATION_TTL_SECONDS
        data["reservations"] = [
            reservation
            for reservation in data.get("reservations", [])
            if float(reservation.get("created_at", 0.0)) >= cutoff
        ]

    @staticmethod
    def _reserved_total(data: dict[str, Any]) -> float:
        return sum(
            max(0.0, float(item.get("amount_usd", 0.0)))
            for item in data.get("reservations", [])
        )

    def can_call(self) -> bool:
        with self._locked_record() as data:
            return (
                float(data.get("spent_usd", 0.0)) + self._reserved_total(data)
                < self.settings.daily_budget_usd
            )

    def spent_today(self) -> float:
        with self._locked_record() as data:
            return float(data.get("spent_usd", 0.0))

    def reserve_call(
        self,
        *,
        agent: str,
        model: str,
        prompt_chars: int,
        max_tokens: int,
    ) -> str | None:
        """Atomically reserve headroom before an external paid request.

        Provider-reported cost is known only after completion.  Configured
        token prices provide the best estimate; when they are intentionally
        zero, reserve a conservative one-percent slice (at least one cent).
        Near the limit this means one request may proceed, never two requests
        that independently observed the same remaining balance.
        """

        prompt_tokens = max(1, prompt_chars // 4)
        estimated = (
            prompt_tokens / 1_000_000
        ) * self.settings.input_price_per_1m_tokens_usd + (
            max(1, int(max_tokens)) / 1_000_000
        ) * self.settings.output_price_per_1m_tokens_usd
        conservative_floor = min(
            0.10,
            max(0.01, float(self.settings.daily_budget_usd) * 0.01),
        )
        amount = max(estimated, conservative_floor)

        with self._locked_record() as data:
            spent = float(data.get("spent_usd", 0.0))
            remaining = (
                float(self.settings.daily_budget_usd)
                - spent
                - self._reserved_total(data)
            )
            if remaining + 1e-12 < amount:
                return None
            reservation_id = uuid.uuid4().hex
            data.setdefault("reservations", []).append(
                {
                    "id": reservation_id,
                    "created_at": time.time(),
                    "amount_usd": round(amount, 8),
                    "agent": agent,
                    "model": model,
                }
            )
            self._write(data)
            return reservation_id

    def release_reservation(self, reservation_id: str | None) -> None:
        if not reservation_id:
            return
        with self._locked_record() as data:
            before = len(data.get("reservations", []))
            data["reservations"] = [
                item
                for item in data.get("reservations", [])
                if item.get("id") != reservation_id
            ]
            if len(data["reservations"]) != before:
                self._write(data)

    def estimate_cost_usd(
        self,
        prompt_chars: int = 0,
        completion_chars: int = 0,
        usage: dict[str, Any] | None = None,
    ) -> tuple[float, int, int]:
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        else:
            prompt_tokens = max(1, prompt_chars // 4)
            completion_tokens = max(1, completion_chars // 4)

        # Hosted providers may return the amount actually charged in the
        # response.  OpenRouter exposes it as ``usage.cost``.  It is more
        # authoritative than a locally configured price table because routing,
        # caching and provider tiers can change the effective price.  The old
        # token-price calculation remains the fallback for Ollama-compatible
        # endpoints and providers that omit cost accounting.
        if usage is not None and "cost" in usage:
            try:
                provider_cost = float(usage["cost"])
            except (TypeError, ValueError):
                provider_cost = -1.0
            if provider_cost >= 0:
                return provider_cost, prompt_tokens, completion_tokens

        input_cost = (
            prompt_tokens / 1_000_000
        ) * self.settings.input_price_per_1m_tokens_usd
        output_cost = (
            completion_tokens / 1_000_000
        ) * self.settings.output_price_per_1m_tokens_usd
        return input_cost + output_cost, prompt_tokens, completion_tokens

    def record_call(
        self,
        agent: str,
        model: str,
        prompt_chars: int,
        completion_chars: int,
        usage: dict[str, Any] | None,
        reservation_id: str | None = None,
    ) -> float:
        cost, prompt_tokens, completion_tokens = self.estimate_cost_usd(
            prompt_chars=prompt_chars,
            completion_chars=completion_chars,
            usage=usage,
        )
        with self._locked_record() as data:
            if reservation_id:
                data["reservations"] = [
                    item
                    for item in data.get("reservations", [])
                    if item.get("id") != reservation_id
                ]
            data["spent_usd"] = round(float(data.get("spent_usd", 0.0)) + cost, 8)
            data.setdefault("calls", []).append(
                {
                    "ts": datetime.now().isoformat(),
                    "agent": agent,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": round(cost, 8),
                }
            )
            self._write(data)
        logger.info(
            "LLM call cost estimate: agent=%s model=%s cost_usd=%.8f spent_today=%.8f limit=%.2f",
            agent,
            model,
            cost,
            self.spent_today(),
            self.settings.daily_budget_usd,
        )
        return cost
