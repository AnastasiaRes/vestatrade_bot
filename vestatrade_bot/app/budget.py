from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import Settings


logger = logging.getLogger(__name__)


class BudgetManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.usage_budget_path
        self._lock = RLock()

    def _today(self) -> str:
        return datetime.now().date().isoformat()

    def _empty_record(self) -> dict[str, Any]:
        return {"date": self._today(), "spent_usd": 0.0, "calls": []}

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
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def can_call(self) -> bool:
        with self._lock:
            data = self._read()
            return float(data.get("spent_usd", 0.0)) < self.settings.daily_budget_usd

    def spent_today(self) -> float:
        with self._lock:
            return float(self._read().get("spent_usd", 0.0))

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
    ) -> float:
        cost, prompt_tokens, completion_tokens = self.estimate_cost_usd(
            prompt_chars=prompt_chars,
            completion_chars=completion_chars,
            usage=usage,
        )
        with self._lock:
            data = self._read()
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

