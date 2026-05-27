from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.budget import BudgetManager
from app.config import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    content: str | None
    llm_used: bool
    fallback_reason: str | None = None
    usage: dict[str, Any] | None = None
    cost_usd: float = 0.0


class OpenRouterClient:
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        settings: Settings | None = None,
        budget_manager: BudgetManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.budget = budget_manager or BudgetManager(self.settings)

    def _fallback(self, reason: str) -> LLMResult:
        logger.info("LLM fallback: %s", reason)
        return LLMResult(content=None, llm_used=False, fallback_reason=reason)

    def complete(
        self,
        agent: str,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 500,
    ) -> LLMResult:
        if not self.settings.openrouter_api_key:
            return self._fallback("OPENROUTER_API_KEY is not set")
        if not self.budget.can_call():
            return self._fallback("daily LLM budget is exhausted")

        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Vesta Trading Chat Bot",
        }
        payload = {
            "model": self.settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        prompt_chars = len(json.dumps(messages, ensure_ascii=False))

        last_error: Exception | None = None
        for attempt in range(self.settings.openrouter_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.openrouter_timeout_seconds) as client:
                    response = client.post(self.endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content")
                )
                usage = data.get("usage") or {}
                cost = self.budget.record_call(
                    agent=agent,
                    model=self.settings.openrouter_model,
                    prompt_chars=prompt_chars,
                    completion_chars=len(content or ""),
                    usage=usage,
                )
                return LLMResult(
                    content=content,
                    llm_used=True,
                    usage=usage,
                    cost_usd=cost,
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                logger.warning(
                    "OpenRouter call failed for agent=%s attempt=%s: %s",
                    agent,
                    attempt + 1,
                    exc,
                )
        return self._fallback(f"OpenRouter request failed: {last_error}")

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        result = self.complete(agent=agent, messages=messages, temperature=0.0, max_tokens=600)
        if not result.llm_used or not result.content:
            return fallback, False
        content = result.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "", 1).replace("JSON\n", "", 1)
        try:
            return json.loads(content), True
        except json.JSONDecodeError:
            logger.warning("LLM JSON parse failed for %s: %s", agent, content[:500])
            return fallback, True

