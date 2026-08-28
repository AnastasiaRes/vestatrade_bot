"""Machine-readable, fail-open diagnostics for one complete chat turn."""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Iterator
from uuid import uuid4

from app.chat_logger import _file_lock, _interprocess_file_lock
from app.config import PROJECT_ROOT, Settings
from app.commerce_v2.registry import (
    canonical_commerce_field_name,
    sensitive_value_kind,
)
from app.models import ChatResponse, Product, SearchQuery, SessionState, model_to_dict
from app.pii import redact_pii_for_model


logger = logging.getLogger(__name__)
TRACE_SCHEMA_VERSION = "1.0"
_HEX_TO_ALPHA = str.maketrans("0123456789abcdef", "ghijklmnopqrstuv")
_ACTIVE_TRACE: ContextVar["TurnTrace | None"] = ContextVar(
    "diagnostic_turn_trace",
    default=None,
)


def safe_session_fingerprint(session_id: str) -> str:
    """Stable non-PII join key that cannot look like a phone number."""

    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest.translate(_HEX_TO_ALPHA)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        # LLM/debug payloads should use typed field names, but fail closed if a
        # malformed result ever promotes customer text to a mapping key.
        return {
            redact_pii_for_model(str(key)): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return redact_pii_for_model(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return _json_safe(enum_value)
    return redact_pii_for_model(str(value))


def _scrub_commerce_sensitive(value: Any, *, parent_key: str = "") -> Any:
    """Remove typed sensitive commerce values without inspecting message text."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        fact_name = canonical_commerce_field_name(str(value.get("name") or ""))
        fact_sensitive = sensitive_value_kind(fact_name) is not None
        for key, item in value.items():
            canonical_key = canonical_commerce_field_name(str(key))
            if sensitive_value_kind(canonical_key) is not None:
                result[str(key)] = "[sensitive-present]" if item not in (None, "", [], {}) else None
            elif fact_sensitive and key in {"value", "evidence"}:
                result[str(key)] = "[sensitive-present]" if item not in (None, "", [], {}) else None
            else:
                result[str(key)] = _scrub_commerce_sensitive(
                    item,
                    parent_key=str(key),
                )
        return result
    if isinstance(value, list):
        return [_scrub_commerce_sensitive(item, parent_key=parent_key) for item in value]
    return value


def _state_view(state: SessionState) -> dict[str, Any]:
    """Keep decision state while excluding history and stored contact data."""

    pending = state.pending_question_state
    return _scrub_commerce_sensitive(_json_safe(
        {
            "last_intent": state.last_intent,
            "category": state.category,
            "slots": state.slots,
            "project_context": state.project_context,
            "last_product_skus": [card.sku for card in state.last_products],
            "shown_product_skus": state.shown_product_skus,
            "pending": (
                {
                    "question_id": pending.question_id,
                    "expected_slots": pending.expected_slots,
                    "category": pending.category,
                    "intent_type": pending.intent_type,
                    "attempts": pending.attempts,
                }
                if pending is not None
                else None
            ),
            "pending_selection_mode": state.pending_selection_mode,
            "topic_changed": state.topic_changed,
            "handoff_status": state.handoff_status,
            "has_pending_handoff": bool(state.pending_handoff),
        }
    ))


@lru_cache(maxsize=1)
def runtime_revision() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    source_digest = hashlib.sha256()
    try:
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            source_digest.update(
                str(path.relative_to(PROJECT_ROOT)).encode("utf-8")
            )
            source_digest.update(b"\0")
            source_digest.update(path.read_bytes())
            source_digest.update(b"\0")
        result["source_tree_sha256"] = source_digest.hexdigest()
    except OSError:
        result["source_tree_sha256"] = None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "HEAD", "--", "app"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            timeout=8,
        ).stdout
        result.update(
            {
                "git_commit": head,
                "source_dirty": bool(diff),
                "source_diff_sha256": hashlib.sha256(diff).hexdigest(),
            }
        )
    except (OSError, subprocess.SubprocessError):
        result.update(
            {
                "git_commit": None,
                "source_dirty": None,
                "source_diff_sha256": None,
            }
        )
    return result


def catalogue_manifest(products: list[Product], source: str) -> dict[str, Any]:
    """Fingerprint the exact catalogue facts visible to the search layer."""

    digest = hashlib.sha256()
    for product in sorted(products, key=lambda item: item.sku):
        facts = model_to_dict(product)
        digest.update(
            json.dumps(
                facts,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return {
        "source": source,
        "product_count": len(products),
        "sha256": digest.hexdigest(),
    }


@dataclass
class TurnTrace:
    path: Path
    session_id: str
    message: str
    state_before: SessionState
    settings: Settings
    catalog: dict[str, Any]
    qa_mode: str | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: float = field(default_factory=monotonic)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    embedding_calls: list[dict[str, Any]] = field(default_factory=list)
    passport_events: list[dict[str, Any]] = field(default_factory=list)
    search_events: list[dict[str, Any]] = field(default_factory=list)
    semantic_shadow: dict[str, Any] | None = None
    dialogue_v2_shadow: dict[str, Any] | None = None
    cutover_v2: dict[str, Any] | None = None
    _written: bool = False

    def record_llm(self, event: dict[str, Any]) -> None:
        self.llm_calls.append(_json_safe(event))

    def record_embedding(self, event: dict[str, Any]) -> None:
        # Never persist the input texts or vectors.  Counts, dimensions and a
        # stable failure code are sufficient to distinguish retrieval outages
        # from semantic or catalogue errors without copying customer data into
        # an additional diagnostic surface.
        self.embedding_calls.append(_json_safe(event))

    def record_passport(self, event: dict[str, Any]) -> None:
        self.passport_events.append(_json_safe(event))

    def record_search(self, event: dict[str, Any]) -> None:
        self.search_events.append(_json_safe(event))

    def record_semantic(self, result: Any) -> None:
        self.semantic_shadow = _scrub_commerce_sensitive(_json_safe(result))

    def record_dialogue_v2(self, result: Any) -> None:
        self.dialogue_v2_shadow = _scrub_commerce_sensitive(_json_safe(result))

    def record_cutover_v2(self, result: Any) -> None:
        self.cutover_v2 = _scrub_commerce_sensitive(_json_safe(result))

    def finish(
        self,
        *,
        response: ChatResponse | None,
        state_after: SessionState,
        error: BaseException | None = None,
    ) -> None:
        if self._written:
            return
        self._written = True
        debug = dict(response.debug) if response is not None else {}
        turn_actions = list(debug.get("turn_actions") or [])
        selected_next_action = (
            {
                "source": "legacy_turn_plan",
                "primary": turn_actions[0],
                "additional": turn_actions[1:],
            }
            if turn_actions
            else {
                "source": "legacy_intent_proxy",
                "primary": debug.get("intent"),
                "additional": [],
            }
        )
        v2_plan = (self.dialogue_v2_shadow or {}).get("next_action_plan") or {}
        catalog_v2 = (self.dialogue_v2_shadow or {}).get("catalog_planning")
        answer_v2 = (self.dialogue_v2_shadow or {}).get("answer_planning")
        response_rendering_v2 = (self.dialogue_v2_shadow or {}).get(
            "response_rendering"
        )
        grounding_v2 = (self.dialogue_v2_shadow or {}).get(
            "grounding_validation"
        )
        answer_plan_payload = (answer_v2 or {}).get("answer_plan") or {}
        answer_claims = answer_plan_payload.get("claims") or []
        legacy_question_facts = (
            list(state_after.pending_question_state.expected_slots)
            if state_after.pending_question_state is not None
            else []
        )
        v2_primary = (v2_plan.get("primary") or {}).get("kind")
        legacy_primary = selected_next_action.get("primary")
        payload = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "duration_ms": int((monotonic() - self.started_at) * 1000),
            "session_fingerprint": safe_session_fingerprint(self.session_id),
            "current_message": (
                None
                if (
                    self.settings.commerce_workflows_v2_shadow_enabled
                    or self.settings.handoff_workflow_v2_shadow_enabled
                    or self.settings.commerce_outbox_v2_shadow_enabled
                    or self.settings.answer_plan_v2_shadow_enabled
                    or self.settings.response_renderer_v2_shadow_enabled
                    or self.settings.response_grounding_v2_shadow_enabled
                    or self.settings.progress_guard_v2_shadow_enabled
                    or self.settings.dialogue_v2_routing_enabled
                    or self.settings.dialogue_v2_shadow_compare_enabled
                    or self.settings.dialogue_v2_live_delivery_enabled
                    or self.settings.dialogue_v2_internal_canary_enabled
                    or self.settings.dialogue_v2_force_legacy
                )
                else redact_pii_for_model(self.message)
            ),
            "runtime": {
                **runtime_revision(),
                "llm_provider": self.settings.llm_provider,
                "llm_model": self.settings.llm_model,
                "llm_model_strong": self.settings.llm_model_strong,
                "embedding_model": self.settings.embedding_model,
                "embeddings_configured": self.settings.embeddings_enabled,
                "qa_mode": self.qa_mode,
                "semantic_shadow_model": (
                    self.settings.semantic_shadow_model
                    or self.settings.llm_model_strong
                ),
                "semantic_prompt_version": (
                    (self.semantic_shadow or {}).get("prompt_version")
                ),
                "semantic_prompt_hash": (
                    (self.semantic_shadow or {}).get("prompt_hash")
                ),
                "catalog": self.catalog,
            },
            "state_before": _state_view(self.state_before),
            "turn_understanding": self.semantic_shadow,
            "legacy_decision": {
                "intent": debug.get("intent"),
                "category": debug.get("category"),
                "slots": debug.get("slots"),
                "turn_acts": debug.get("turn_acts"),
                "turn_actions": turn_actions,
                "selection_mode": debug.get("selection_mode"),
                "agents_used": debug.get("agents_used"),
                "llm_acceptance": {
                    key: value
                    for key, value in debug.items()
                    if key in {
                        "llm_requested",
                        "llm_transport_succeeded",
                        "llm_output_accepted",
                        "llm_rejection_reason",
                        "final_answer_source",
                        "intent_llm_requested",
                        "intent_llm_output_accepted",
                        "intent_llm_rejection_reason",
                        "engineering_llm_requested",
                        "engineering_llm_output_accepted",
                        "engineering_llm_fallback_reason",
                        "response_llm_requested",
                        "response_llm_output_accepted",
                        "response_llm_rejection_reason",
                        "response_llm_fallback_reason",
                        "consultant_llm_requested",
                        "consultant_llm_output_accepted",
                        "consultant_llm_rejection_reason",
                        "consultant_llm_fallback_reason",
                    }
                },
            },
            "selected_next_action": selected_next_action,
            "v2_next_action": v2_plan or None,
            "catalog_planner_v2_shadow": catalog_v2,
            "commerce_workflows_v2_shadow": (
                (self.dialogue_v2_shadow or {}).get("commerce_planning")
            ),
            "answer_plan_v2_shadow": answer_v2,
            "response_renderer_v2_shadow": response_rendering_v2,
            "response_grounding_v2_shadow": grounding_v2,
            "task_progress_v2_shadow": (
                (self.dialogue_v2_shadow or {}).get("progress_assessments")
            ),
            "response_strategy_v2_shadow": (
                (self.dialogue_v2_shadow or {}).get("strategy_directives")
            ),
            "stage5_error": (
                (self.dialogue_v2_shadow or {}).get("stage5_error")
            ),
            "stage5_latency_ms": (
                (self.dialogue_v2_shadow or {}).get("latency_ms")
                if self.dialogue_v2_shadow is not None
                else None
            ),
            "cutover_v2": self.cutover_v2,
            "v2_candidate_skus": (
                (catalog_v2 or {}).get("candidate_skus")
                if catalog_v2 is not None
                else None
            ),
            "v2_legacy_catalog_divergence": (
                {
                    "v2_candidate_skus": (catalog_v2 or {}).get("candidate_skus", []),
                    "legacy_product_skus": [item.sku for item in response.products],
                    "same_sku_set": set((catalog_v2 or {}).get("candidate_skus", []))
                    == {item.sku for item in response.products},
                }
                if catalog_v2 is not None and response is not None
                else None
            ),
            "v2_legacy_decision_divergence": (
                {
                    "v2_primary": v2_primary,
                    "legacy_primary": legacy_primary,
                    "exact_name_match": v2_primary == legacy_primary,
                }
                if self.dialogue_v2_shadow is not None
                else None
            ),
            "answer_plan_v2_legacy_divergence": (
                {
                    "action": {
                        "v2": answer_plan_payload.get("primary_action"),
                        "legacy": legacy_primary,
                    },
                    "question_fact": {
                        "v2": (
                            (answer_plan_payload.get("question") or {}).get("fact_name")
                        ),
                        "legacy": legacy_question_facts,
                    },
                    "candidate_sku": {
                        "v2": [
                            item.get("sku")
                            for item in answer_plan_payload.get("products", [])
                        ],
                        "legacy": (
                            [item.sku for item in response.products]
                            if response is not None
                            else []
                        ),
                    },
                    "critical_facts": [
                        {
                            "kind": item.get("kind"),
                            "subject_ref": item.get("subject_ref"),
                            "predicate": item.get("predicate"),
                            "value": item.get("value"),
                            "unit": item.get("unit"),
                        }
                        for item in answer_claims
                        if item.get("allowed_in_response")
                    ],
                    "commerce_status": [
                        item.get("value")
                        for item in answer_claims
                        if item.get("kind") == "commerce_status"
                    ],
                    "capability_claim": [
                        item.get("predicate")
                        for item in answer_claims
                        if item.get("kind") == "capability_fact"
                    ],
                }
                if answer_plan_payload
                else None
            ),
            "dialogue_v2_shadow": self.dialogue_v2_shadow,
            "search_plan_events": self.search_events,
            "llm_calls": self.llm_calls,
            "embedding_calls": self.embedding_calls,
            "passport_events": self.passport_events,
            "legacy_answer_plan": (
                {
                    "final_answer_source": debug.get("final_answer_source"),
                    "answer": redact_pii_for_model(response.answer),
                    "product_skus": [item.sku for item in response.products],
                    "need_handoff": response.need_handoff,
                    "handoff_status": response.handoff_status,
                }
                if response is not None
                else None
            ),
            "state_after": _state_view(state_after),
            "error": (
                {
                    "type": type(error).__name__,
                    "message": "turn_failed_before_response",
                }
                if error is not None
                else None
            ),
        }
        self._append(_json_safe(payload))

    def _append(self, payload: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            with _file_lock(self.path):
                with _interprocess_file_lock(self.path):
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line)
                        fh.flush()
        except OSError as exc:
            logger.warning("Could not write diagnostic turn trace: %s", exc)


@contextmanager
def activate_turn_trace(trace: TurnTrace | None) -> Iterator[None]:
    token = _ACTIVE_TRACE.set(trace)
    try:
        yield
    finally:
        _ACTIVE_TRACE.reset(token)


def build_turn_trace(
    settings: Settings,
    *,
    session_id: str,
    message: str,
    state_before: SessionState,
    catalog: dict[str, Any],
    qa_mode: str | None = None,
) -> TurnTrace | None:
    if not (
        settings.diagnostic_telemetry_enabled
        or settings.semantic_shadow_enabled
        or settings.dialogue_state_v2_shadow_enabled
        or settings.seller_policy_v2_shadow_enabled
        or settings.product_contracts_v2_shadow_enabled
        or settings.catalog_planner_v2_shadow_enabled
        or settings.solution_plan_v2_shadow_enabled
        or settings.commerce_workflows_v2_shadow_enabled
        or settings.handoff_workflow_v2_shadow_enabled
        or settings.commerce_outbox_v2_shadow_enabled
        or settings.answer_plan_v2_shadow_enabled
        or settings.response_renderer_v2_shadow_enabled
        or settings.response_grounding_v2_shadow_enabled
        or settings.progress_guard_v2_shadow_enabled
        or settings.dialogue_v2_routing_enabled
        or settings.dialogue_v2_shadow_compare_enabled
        or settings.dialogue_v2_live_delivery_enabled
        or settings.dialogue_v2_internal_canary_enabled
        or settings.dialogue_v2_force_legacy
        or qa_mode is not None
    ):
        return None
    return TurnTrace(
        path=settings.diagnostic_trace_path,
        session_id=session_id,
        message=message,
        state_before=state_before,
        settings=settings,
        catalog=catalog,
        qa_mode=qa_mode,
    )


def record_llm_event(**event: Any) -> None:
    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_llm(event)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record LLM diagnostic error_type=%s",
                type(exc).__name__,
            )


def record_embedding_event(**event: Any) -> None:
    """Record one embedding transport boundary without text or vectors."""

    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_embedding(event)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record embedding diagnostic error_type=%s",
                type(exc).__name__,
            )


def record_passport_event(**event: Any) -> None:
    """Record passport retrieval and verification provenance."""

    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_passport(event)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record passport diagnostic error_type=%s",
                type(exc).__name__,
            )


def record_llm_json_validation(
    *,
    agent: str,
    accepted: bool,
    rejection_reason: str | None = None,
) -> None:
    record_llm_event(
        event="json_validation",
        agent=agent,
        output_accepted=accepted,
        rejection_reason=rejection_reason,
    )


def record_search_event(
    *,
    operation: str,
    query: SearchQuery | dict[str, Any] | str | None,
    result_skus: list[str],
    relaxations: list[str] | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    trace = _ACTIVE_TRACE.get()
    if trace is None:
        return
    if isinstance(query, SearchQuery):
        query_value: Any = model_to_dict(query)
    else:
        query_value = query
    try:
        trace.record_search(
            {
                "operation": operation,
                "query": query_value,
                "relaxations": relaxations or [],
                "result_skus": result_skus,
                "error": error,
                **({"details": details} if details else {}),
            }
        )
    except Exception as exc:  # pragma: no cover - injected telemetry failure
        logger.warning(
            "Could not record search diagnostic error_type=%s",
            type(exc).__name__,
        )


def record_semantic_shadow(result: Any) -> None:
    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_semantic(result)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record semantic diagnostic error_type=%s",
                type(exc).__name__,
            )


def record_dialogue_v2_shadow(result: Any) -> None:
    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_dialogue_v2(result)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record V2 diagnostic error_type=%s",
                type(exc).__name__,
            )


def record_cutover_v2(result: Any) -> None:
    trace = _ACTIVE_TRACE.get()
    if trace is not None:
        try:
            trace.record_cutover_v2(result)
        except Exception as exc:  # pragma: no cover - injected telemetry failure
            logger.warning(
                "Could not record cutover diagnostic error_type=%s",
                type(exc).__name__,
            )


def finish_turn_trace(
    trace: TurnTrace | None,
    *,
    response: ChatResponse | None,
    state_after: SessionState,
    error: BaseException | None = None,
) -> None:
    """Finalize diagnostics without ever changing the chat outcome."""

    if trace is None:
        return
    try:
        trace.finish(response=response, state_after=state_after, error=error)
    except Exception as exc:  # pragma: no cover - last-resort observability guard
        logger.warning(
            "Could not finalize diagnostic turn trace error_type=%s",
            type(exc).__name__,
        )
