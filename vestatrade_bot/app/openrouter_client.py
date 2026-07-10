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
    openrouter_endpoint = "https://openrouter.ai/api/v1/chat/completions"

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

    def _endpoint(self) -> str | None:
        if self.settings.llm_provider == "ollama":
            if not self.settings.ollama_base_url:
                return None
            base_url = self.settings.ollama_base_url.rstrip("/")
            if base_url.endswith("/v1"):
                return f"{base_url}/chat/completions"
            return f"{base_url}/v1/chat/completions"
        if self.settings.llm_provider == "openrouter":
            return self.openrouter_endpoint
        return None

    def _headers(self) -> dict[str, str] | None:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_provider == "openrouter":
            if not self.settings.openrouter_api_key:
                return None
            headers.update(
                {
                    "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "Vesta Trading Chat Bot",
                }
            )
        return headers

    def complete(
        self,
        agent: str,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> LLMResult:
        if not self.settings.llm_enabled:
            return self._fallback(f"LLM provider '{self.settings.llm_provider}' is not configured")
        if not self.budget.can_call():
            return self._fallback("daily LLM budget is exhausted")

        endpoint = self._endpoint()
        headers = self._headers()
        if not endpoint or not headers:
            return self._fallback(f"LLM provider '{self.settings.llm_provider}' is not configured")

        model_name = model or self.settings.llm_model
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        prompt_chars = len(json.dumps(messages, ensure_ascii=False))

        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
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
                    model=model_name,
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
                    "%s LLM call failed for agent=%s attempt=%s: %s",
                    self.settings.llm_provider,
                    agent,
                    attempt + 1,
                    exc,
                )
        return self._fallback(f"{self.settings.llm_provider} request failed: {last_error}")

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        result = self.complete(
            agent=agent, messages=messages, temperature=0.0, max_tokens=600, model=model
        )
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
