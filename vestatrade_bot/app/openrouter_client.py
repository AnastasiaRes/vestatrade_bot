from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from threading import Lock, local
from time import monotonic, sleep
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


@dataclass
class _CircuitState:
    open_until: float = 0.0
    half_open_probe_in_flight: bool = False
    last_error: str | None = None
    generation: int = 0


@dataclass(frozen=True)
class _CircuitPermit:
    generation: int
    is_half_open_probe: bool = False


class OpenRouterClient:
    openrouter_endpoint = "https://openrouter.ai/api/v1/chat/completions"
    _ollama_circuit_open_seconds = 20.0
    _connect_timeout_seconds = 3.0
    _write_timeout_seconds = 5.0
    _pool_timeout_seconds = 2.0
    _circuit_lock = Lock()
    _circuits: dict[str, _CircuitState] = {}

    def __init__(
        self,
        settings: Settings | None = None,
        budget_manager: BudgetManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.budget = budget_manager or BudgetManager(self.settings)
        self._telemetry = local()

    @property
    def last_json_output_accepted(self) -> bool | None:
        return getattr(self._telemetry, "json_output_accepted", None)

    @property
    def last_fallback_reason(self) -> str | None:
        return getattr(self._telemetry, "fallback_reason", None)

    def _fallback(self, reason: str) -> LLMResult:
        self._telemetry.fallback_reason = reason
        logger.info("LLM fallback: %s", reason)
        return LLMResult(content=None, llm_used=False, fallback_reason=reason)

    def _wait_before_retry(self, attempt: int) -> None:
        delay = max(0.0, float(self.settings.llm_retry_delay_seconds))
        if delay:
            sleep(delay * (attempt + 1))

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

    def _timeout(self) -> httpx.Timeout:
        read_timeout = self.settings.llm_timeout_seconds
        return httpx.Timeout(
            connect=min(read_timeout, self._connect_timeout_seconds),
            read=read_timeout,
            write=min(read_timeout, self._write_timeout_seconds),
            pool=min(read_timeout, self._pool_timeout_seconds),
        )

    def _acquire_circuit(
        self, endpoint: str
    ) -> tuple[bool, _CircuitPermit | None, str | None]:
        if self.settings.llm_provider != "ollama":
            return True, None, None

        now = monotonic()
        with self._circuit_lock:
            state = self._circuits.setdefault(endpoint, _CircuitState())
            if state.open_until <= 0.0:
                return True, _CircuitPermit(state.generation), None
            if now < state.open_until or state.half_open_probe_in_flight:
                return False, None, state.last_error
            state.half_open_probe_in_flight = True
            return True, _CircuitPermit(state.generation, is_half_open_probe=True), None

    def _open_circuit(
        self, endpoint: str, error: Exception, permit: _CircuitPermit | None
    ) -> None:
        if self.settings.llm_provider != "ollama":
            return
        with self._circuit_lock:
            state = self._circuits.setdefault(endpoint, _CircuitState())
            if permit is not None and permit.generation != state.generation:
                return
            state.generation += 1
            state.open_until = monotonic() + self._ollama_circuit_open_seconds
            state.half_open_probe_in_flight = False
            state.last_error = str(error)

    def _close_half_open_circuit(
        self, endpoint: str, permit: _CircuitPermit | None
    ) -> None:
        if (
            self.settings.llm_provider != "ollama"
            or permit is None
            or not permit.is_half_open_probe
        ):
            return
        with self._circuit_lock:
            state = self._circuits.setdefault(endpoint, _CircuitState())
            if (
                permit.generation != state.generation
                or not state.half_open_probe_in_flight
            ):
                return
            state.generation += 1
            state.open_until = 0.0
            state.half_open_probe_in_flight = False
            state.last_error = None

    def complete(
        self,
        agent: str,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> LLMResult:
        self._telemetry.fallback_reason = None
        if not self.settings.llm_enabled:
            return self._fallback(f"LLM provider '{self.settings.llm_provider}' is not configured")
        if not self.budget.can_call():
            return self._fallback("daily LLM budget is exhausted")

        endpoint = self._endpoint()
        headers = self._headers()
        if not endpoint or not headers:
            return self._fallback(f"LLM provider '{self.settings.llm_provider}' is not configured")

        circuit_allowed, circuit_permit, circuit_error = self._acquire_circuit(endpoint)
        if not circuit_allowed:
            detail = f" after: {circuit_error}" if circuit_error else ""
            return self._fallback(f"ollama request skipped: circuit is open{detail}")

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
                with httpx.Client(timeout=self._timeout()) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._close_half_open_circuit(endpoint, circuit_permit)
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
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.warning(
                    "%s LLM call failed for agent=%s attempt=%s: %s",
                    self.settings.llm_provider,
                    agent,
                    attempt + 1,
                    exc,
                )
                status_code = exc.response.status_code
                if status_code == 429 or status_code >= 500:
                    if attempt < self.settings.llm_max_retries:
                        self._wait_before_retry(attempt)
                        continue
                    if self.settings.llm_provider == "ollama":
                        self._open_circuit(endpoint, exc, circuit_permit)
                    break
                self._close_half_open_circuit(endpoint, circuit_permit)
                break
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning(
                    "%s LLM call failed for agent=%s attempt=%s: %s",
                    self.settings.llm_provider,
                    agent,
                    attempt + 1,
                    exc,
                )
                if attempt < self.settings.llm_max_retries:
                    self._wait_before_retry(attempt)
                    continue
                if self.settings.llm_provider == "ollama":
                    self._open_circuit(endpoint, exc, circuit_permit)
                break
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "%s LLM call failed for agent=%s attempt=%s: %s",
                    self.settings.llm_provider,
                    agent,
                    attempt + 1,
                    exc,
                )
                if attempt < self.settings.llm_max_retries:
                    self._wait_before_retry(attempt)
                    continue
                if self.settings.llm_provider == "ollama":
                    self._open_circuit(endpoint, exc, circuit_permit)
                break
            except (ValueError, KeyError) as exc:
                last_error = exc
                logger.warning(
                    "%s LLM call failed for agent=%s attempt=%s: %s",
                    self.settings.llm_provider,
                    agent,
                    attempt + 1,
                    exc,
                )
        if isinstance(last_error, (ValueError, KeyError)):
            self._close_half_open_circuit(endpoint, circuit_permit)
        return self._fallback(f"{self.settings.llm_provider} request failed: {last_error}")

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._telemetry.json_output_accepted = False
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
            parsed = json.loads(content)
            self._telemetry.json_output_accepted = True
            return parsed, True
        except json.JSONDecodeError:
            logger.warning("LLM JSON parse failed for %s: %s", agent, content[:500])
            return fallback, True
