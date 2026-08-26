from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, local
from time import monotonic, sleep
from typing import Any, Iterator

import httpx

from app.budget import BudgetManager
from app.config import Settings, get_settings
from app.diagnostic_telemetry import (
    record_llm_event,
    record_llm_json_validation,
)
from app.pii import redact_pii_for_model


logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    content: str | None
    llm_used: bool
    fallback_reason: str | None = None
    usage: dict[str, Any] | None = None
    cost_usd: float = 0.0
    # Почему модель остановилась. ``length`` означает, что ответ обрезан по
    # лимиту токенов: такой текст проходит все проверки достоверности (в нём
    # нет выдуманных фактов — в нём вообще нет конца) и уходил покупателю
    # оборванным на середине слова.
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


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

    # ------------------------------------------------------------------
    # Запись сгенерированного моделью текста за один ход диалога.
    #
    # Метка источника ответа выводилась из флагов «вывод принят» у агентов,
    # поэтому текст модели, попавший в ответ мимо этих флагов, автоматически
    # считался детерминированным. В живом прогоне так было помечено шесть
    # ходов, где ответ начинался фразой из системного промпта консультанта.
    # Единственный надёжный признак — совпадение с тем, что модель реально
    # вернула, поэтому запись стоит здесь, в общей воронке всех вызовов.
    # ------------------------------------------------------------------

    def begin_turn_recording(self) -> None:
        self._telemetry.completions = []

    def record_completion(self, content: str | None) -> None:
        text = (content or "").strip()
        if not text:
            return
        recorded = getattr(self._telemetry, "completions", None)
        if recorded is None:
            # Ход не открывали — запись не ведём, чтобы не копить текст между
            # запросами одного потока.
            return
        recorded.append(text)

    def recorded_completions(self) -> list[str]:
        return list(getattr(self._telemetry, "completions", None) or ())

    def _fallback(self, reason: str) -> LLMResult:
        self._telemetry.fallback_reason = reason
        logger.info("LLM fallback: %s", reason)
        return LLMResult(content=None, llm_used=False, fallback_reason=reason)

    def _request_budget_fallback(self) -> LLMResult:
        seconds = float(self.settings.llm_request_timeout_seconds)
        return self._fallback(
            f"LLM request budget of {seconds:g}s is exhausted"
        )

    def _remaining_request_seconds(self) -> float | None:
        deadline = getattr(self._telemetry, "request_deadline", None)
        if deadline is None:
            return None
        return deadline - monotonic()

    def _request_budget_exhausted(self) -> bool:
        remaining = self._remaining_request_seconds()
        return remaining is not None and remaining <= 0

    @contextmanager
    def request_budget(self, seconds: float | None = None) -> Iterator[None]:
        """Share one wall-clock deadline across every LLM agent in a chat turn."""
        previous_deadline = getattr(self._telemetry, "request_deadline", None)
        timeout = max(
            0.0,
            float(
                self.settings.llm_request_timeout_seconds
                if seconds is None
                else seconds
            ),
        )
        deadline = monotonic() + timeout
        if previous_deadline is not None:
            deadline = min(deadline, previous_deadline)
        self._telemetry.request_deadline = deadline
        try:
            yield
        finally:
            if previous_deadline is None:
                try:
                    del self._telemetry.request_deadline
                except AttributeError:
                    pass
            else:
                self._telemetry.request_deadline = previous_deadline

    def _wait_before_retry(self, attempt: int) -> None:
        delay = max(0.0, float(self.settings.llm_retry_delay_seconds))
        delay *= attempt + 1
        remaining = self._remaining_request_seconds()
        if remaining is not None:
            delay = min(delay, max(0.0, remaining))
        if delay:
            sleep(delay)

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

    def _timeout(self, read_timeout: float | None = None) -> httpx.Timeout:
        if read_timeout is None:
            read_timeout = self.settings.llm_timeout_seconds
        read_timeout = max(0.001, float(read_timeout))
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
        json_mode: bool = False,
    ) -> LLMResult:
        started = monotonic()
        model_name = model or self.settings.llm_model

        def observed(result: LLMResult) -> LLMResult:
            record_llm_event(
                event="completion",
                agent=agent,
                provider=self.settings.llm_provider,
                model=model_name,
                requested=True,
                transport_succeeded=result.llm_used,
                finish_reason=result.finish_reason,
                fallback_reason=result.fallback_reason,
                usage=result.usage or {},
                cost_usd=result.cost_usd,
                latency_ms=int((monotonic() - started) * 1000),
                json_mode=json_mode,
            )
            return result

        self._telemetry.fallback_reason = None
        if self._request_budget_exhausted():
            return observed(self._request_budget_fallback())
        if not self.settings.llm_enabled:
            return observed(
                self._fallback(
                    f"LLM provider '{self.settings.llm_provider}' is not configured"
                )
            )
        if not self.budget.can_call():
            return observed(self._fallback("daily LLM budget is exhausted"))

        endpoint = self._endpoint()
        headers = self._headers()
        if not endpoint or not headers:
            return observed(
                self._fallback(
                    f"LLM provider '{self.settings.llm_provider}' is not configured"
                )
            )

        circuit_allowed, circuit_permit, circuit_error = self._acquire_circuit(endpoint)
        if not circuit_allowed:
            detail = f" after: {circuit_error}" if circuit_error else ""
            return observed(
                self._fallback(f"ollama request skipped: circuit is open{detail}")
            )

        messages = self.sanitize_messages(messages)
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.settings.llm_provider in {"ollama", "openrouter"}:
            # Both OpenRouter and Ollama's OpenAI-compatible endpoint support
            # JSON mode. Prompt-only JSON instructions can still produce prose,
            # Python sets or truncated objects, so structured routing belongs
            # to the transport contract rather than an agent convention.
            payload["response_format"] = {"type": "json_object"}
        if json_mode and self.settings.llm_provider == "openrouter":
            # OpenRouter must route only to backends that accept this parameter.
            # This field is provider-specific and is not sent to local Ollama.
            payload["provider"] = {"require_parameters": True}
        prompt_chars = len(json.dumps(messages, ensure_ascii=False))

        reservation_id: str | None = None
        reserve_call = getattr(self.budget, "reserve_call", None)
        if callable(reserve_call):
            reservation_id = reserve_call(
                agent=agent,
                model=model_name,
                prompt_chars=prompt_chars,
                max_tokens=max_tokens,
            )
            if reservation_id is None:
                return observed(
                    self._fallback("daily LLM budget has no unreserved headroom")
                )

        def release_reservation() -> None:
            nonlocal reservation_id
            if reservation_id is None:
                return
            release = getattr(self.budget, "release_reservation", None)
            if callable(release):
                release(reservation_id)
            reservation_id = None

        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            remaining = self._remaining_request_seconds()
            if remaining is not None and remaining <= 0:
                if self.settings.llm_provider == "ollama":
                    self._open_circuit(
                        endpoint,
                        TimeoutError("LLM request budget exhausted"),
                        circuit_permit,
                    )
                release_reservation()
                return observed(self._request_budget_fallback())
            # A local model should be allowed to use the entire remaining
            # turn budget.  Fast connection/write/pool limits still detect an
            # unreachable Ollama quickly.  Hosted providers retain their
            # configured per-attempt read timeout while respecting the shared
            # turn deadline.
            if remaining is not None and self.settings.llm_provider == "ollama":
                read_timeout = remaining
            elif remaining is not None:
                read_timeout = min(self.settings.llm_timeout_seconds, remaining)
            else:
                read_timeout = self.settings.llm_timeout_seconds
            try:
                with httpx.Client(timeout=self._timeout(read_timeout)) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self._close_half_open_circuit(endpoint, circuit_permit)
                choice = (data.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content")
                finish_reason = choice.get("finish_reason")
                usage = data.get("usage") or {}
                try:
                    cost = self.budget.record_call(
                        agent=agent,
                        model=model_name,
                        prompt_chars=prompt_chars,
                        completion_chars=len(content or ""),
                        usage=usage,
                        reservation_id=reservation_id,
                    )
                    reservation_id = None
                except Exception as exc:  # pragma: no cover - filesystem failure
                    logger.warning("Could not record paid LLM usage: %s", exc)
                    release_reservation()
                    cost = 0.0
                # The provider has already completed (and may have charged)
                # this request. Account for it before discarding content that
                # arrived after the customer-facing turn deadline.
                if self._request_budget_exhausted():
                    return observed(self._request_budget_fallback())
                if finish_reason == "length":
                    # Обрезанный ответ нельзя показывать покупателю: в живом
                    # прогоне так уходили «Кон…» и «— труб PPR и армиров».
                    # Ветка отката соберёт детерминированный текст.
                    logger.warning(
                        "LLM output truncated by max_tokens agent=%s model=%s",
                        agent,
                        model_name,
                    )
                    return observed(
                        LLMResult(
                            content=None,
                            llm_used=False,
                            fallback_reason="llm output truncated by max_tokens",
                            usage=usage,
                            cost_usd=cost,
                            finish_reason=finish_reason,
                        )
                    )
                self.record_completion(content)
                return observed(
                    LLMResult(
                        content=content,
                        llm_used=True,
                        usage=usage,
                        cost_usd=cost,
                        finish_reason=finish_reason,
                    )
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
                if self._request_budget_exhausted():
                    if self.settings.llm_provider == "ollama":
                        self._open_circuit(endpoint, exc, circuit_permit)
                    release_reservation()
                    return observed(self._request_budget_fallback())
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
        release_reservation()
        return observed(
            self._fallback(
                f"{self.settings.llm_provider} request failed: {last_error}"
            )
        )

    @staticmethod
    def sanitize_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Return a detached prompt with contact PII removed from every role."""

        return [
            {
                **message,
                "content": redact_pii_for_model(str(message.get("content") or "")),
            }
            for message in messages
        ]

    def complete_json(
        self,
        agent: str,
        messages: list[dict[str, str]],
        fallback: dict[str, Any],
        model: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._telemetry.json_output_accepted = False
        result = self.complete(
            agent=agent,
            messages=messages,
            temperature=0.0,
            # The engineering interpreter returns a typed object with evidence
            # and provenance. Live OpenRouter runs showed that 1000 tokens can
            # still truncate a valid Qwen object mid-string.  Keep enough room
            # for the complete contract; schema validation below remains the
            # authority and rejects prose or malformed output.
            max_tokens=1600,
            model=model,
            json_mode=True,
        )
        if not result.llm_used or not result.content:
            record_llm_json_validation(
                agent=agent,
                accepted=False,
                rejection_reason=result.fallback_reason or "empty LLM response",
            )
            return fallback, False
        content = result.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "", 1).replace("JSON\n", "", 1)
        try:
            parsed = json.loads(content)
            self._telemetry.json_output_accepted = True
            record_llm_json_validation(agent=agent, accepted=True)
            return parsed, True
        except json.JSONDecodeError as exc:
            # Provider output may echo customer data. Log a stable diagnostic
            # fingerprint, never the malformed payload itself.
            logger.warning(
                "LLM JSON parse failed for %s: chars=%s sha256=%s",
                agent,
                len(content),
                hashlib.sha256(
                    content.encode("utf-8", errors="surrogatepass")
                ).hexdigest(),
            )
            record_llm_json_validation(
                agent=agent,
                accepted=False,
                rejection_reason=f"JSONDecodeError: {exc}",
            )
            return fallback, True
