"""Narrow, source-preserving adapters for outcome evaluation inputs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models import Product

from .contracts import (
    CatalogTruthProduct,
    DialogueTranscript,
    ExecutionFailureActor,
    TranscriptProduct,
    TranscriptTurn,
)


_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_KNOWN_EXECUTION_STATUSES = frozenset(
    {
        "valid",
        "bot_error",
        "harness_error",
        "buyer_provider_error",
        "buyer_invalid_output",
        "buyer_protocol_error",
        "transport_error",
        "timeout",
    }
)


def _failure_actor(status: str, stage: Any) -> ExecutionFailureActor:
    stage_token = str(stage or "").strip().casefold()
    explicit = {
        "bot": ExecutionFailureActor.BOT,
        "transport": ExecutionFailureActor.TRANSPORT,
        "buyer": ExecutionFailureActor.BUYER,
        "harness": ExecutionFailureActor.HARNESS,
    }.get(stage_token)
    inferred = (
        ExecutionFailureActor.NONE
        if status == "valid"
        else ExecutionFailureActor.BOT
        if status == "bot_error"
        else ExecutionFailureActor.TRANSPORT
        if status in {"transport_error", "timeout"}
        else ExecutionFailureActor.BUYER
        if status.startswith("buyer_")
        else ExecutionFailureActor.HARNESS
        if status == "harness_error"
        else ExecutionFailureActor.UNKNOWN
    )
    if explicit is None:
        return inferred
    if inferred == ExecutionFailureActor.UNKNOWN:
        return explicit
    return explicit if explicit == inferred else ExecutionFailureActor.UNKNOWN


class TranscriptAdapterError(ValueError):
    """Raised when a saved evaluation record cannot be adapted safely."""


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranscriptAdapterError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TranscriptAdapterError(f"{field} must be a string")
    return value


def _first_present(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _bounded_code(value: Any, *, fallback: str | None = None) -> str | None:
    """Retain a code token, never arbitrary exception or failure prose."""

    if isinstance(value, Mapping):
        value = _first_present(value, "code", "type", "kind")
    if isinstance(value, str):
        candidate = value.strip()
        if _CODE_RE.fullmatch(candidate):
            return candidate.upper().replace("-", "_").replace(".", "_")
    return fallback


def _bounded_owner(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 80 or not _CODE_RE.fullmatch(candidate):
        return None
    return candidate


def _number(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TranscriptAdapterError(f"{field} must be an integer")
    if value < 1:
        raise TranscriptAdapterError(f"{field} must be at least 1")
    return value


def _optional_mapping_value(record: Mapping[str, Any], key: str) -> Any:
    value = record.get(key)
    return value if value is not None else None


def _adapt_product(value: Any, *, location: str) -> TranscriptProduct:
    if isinstance(value, str):
        sku = _required_text(value, field=f"{location}.sku")
        return TranscriptProduct(sku=sku)
    if not isinstance(value, Mapping):
        raise TranscriptAdapterError(
            f"{location} must be a SKU string or product-card object"
        )

    sku = _required_text(value.get("sku"), field=f"{location}.sku")
    payload = {
        "sku": sku,
        "name": _optional_mapping_value(value, "name"),
        "price": _optional_mapping_value(value, "price"),
        "currency": _optional_mapping_value(value, "currency"),
        "stock_status": _optional_mapping_value(value, "stock_status"),
        "stock_qty": _optional_mapping_value(value, "stock_qty"),
        "url": _optional_mapping_value(value, "url"),
        "product_kind": _optional_mapping_value(value, "product_kind"),
        "role": _optional_mapping_value(value, "role"),
        "presentation_status": _optional_mapping_value(
            value, "presentation_status"
        ),
    }
    try:
        return TranscriptProduct.model_validate(payload)
    except ValidationError as exc:
        raise TranscriptAdapterError(f"invalid {location}: {exc}") from exc


def _adapt_turn(value: Any, *, scenario_id: str, index: int) -> TranscriptTurn:
    location = f"scenario {scenario_id!r} turn[{index}]"
    if not isinstance(value, Mapping):
        raise TranscriptAdapterError(f"{location} must be an object")

    number = _number(
        _first_present(value, "turn_number", "n"),
        field=f"{location}.turn_number",
    )
    raw_products = value.get("products", ())
    if raw_products is None:
        raw_products = ()
    if not isinstance(raw_products, Sequence) or isinstance(
        raw_products, (str, bytes, bytearray)
    ):
        raise TranscriptAdapterError(f"{location}.products must be an array")

    products = tuple(
        _adapt_product(product, location=f"{location}.products[{product_index}]")
        for product_index, product in enumerate(raw_products)
    )
    raw_error = _first_present(value, "error_code", "error")
    error_code = (
        _bounded_code(raw_error, fallback="TURN_EXECUTION_ERROR")
        if raw_error
        else None
    )
    payload = {
        "turn_number": number,
        "user_text": _optional_text(
            _first_present(value, "user_text", "user", "message"),
            field=f"{location}.user_text",
        ),
        "assistant_text": _optional_text(
            _first_present(value, "assistant_text", "bot", "assistant", "answer"),
            field=f"{location}.assistant_text",
        ),
        "products": products,
        "error_code": error_code,
        "response_owner": _bounded_owner(
            _first_present(value, "response_owner", "source")
        ),
    }
    try:
        return TranscriptTurn.model_validate(payload)
    except ValidationError as exc:
        raise TranscriptAdapterError(f"invalid {location}: {exc}") from exc


def adapt_dialogue_record(
    record: Mapping[str, Any],
    *,
    source_label: str = "candidate",
) -> DialogueTranscript:
    """Adapt one saved harness record without retaining run/session metadata."""

    if not isinstance(record, Mapping):
        raise TranscriptAdapterError("dialogue record must be an object")
    scenario_id = _required_text(
        _first_present(record, "scenario_id", "id"), field="scenario_id"
    )
    raw_turns = record.get("turns")
    if not isinstance(raw_turns, Sequence) or isinstance(
        raw_turns, (str, bytes, bytearray)
    ):
        raise TranscriptAdapterError(f"scenario {scenario_id!r} turns must be an array")

    turns = tuple(
        _adapt_turn(turn, scenario_id=scenario_id, index=index)
        for index, turn in enumerate(raw_turns)
    )
    turn_numbers = [turn.turn_number for turn in turns]
    if len(turn_numbers) != len(set(turn_numbers)):
        raise TranscriptAdapterError(
            f"scenario {scenario_id!r} contains duplicate turn numbers"
        )
    if turn_numbers != sorted(turn_numbers):
        raise TranscriptAdapterError(
            f"scenario {scenario_id!r} turn numbers must be ordered"
        )

    raw_status = record.get("execution_status")
    status_token = str(raw_status).strip().lower() if raw_status is not None else ""
    execution_status = (
        status_token if status_token in _KNOWN_EXECUTION_STATUSES else "unknown"
    )
    execution_failure_actor = _failure_actor(
        execution_status,
        record.get("failure_stage"),
    )
    explicit_error = _bounded_code(record.get("execution_error_code"))
    execution_error_code = None
    if execution_status != "valid":
        execution_error_code = explicit_error or _bounded_code(
            status_token, fallback="EXECUTION_ERROR"
        )

    # ``session_id``, ``failure_reason`` and other harness prose are
    # intentionally not copied into the typed evidence contract.
    try:
        return DialogueTranscript(
            scenario_id=scenario_id,
            source_label=source_label,
            execution_status=execution_status,
            execution_failure_actor=execution_failure_actor,
            execution_error_code=execution_error_code,
            turns=turns,
        )
    except ValidationError as exc:
        raise TranscriptAdapterError(
            f"invalid transcript for scenario {scenario_id!r}: {exc}"
        ) from exc


def load_dialogue_transcripts_jsonl(
    path: Path,
    *,
    source_label: str = "candidate",
) -> tuple[DialogueTranscript, ...]:
    """Load one-record-per-scenario JSONL with strict duplicate detection."""

    if not isinstance(path, Path):
        raise TranscriptAdapterError("JSONL path must be a pathlib.Path")
    if not path.exists():
        raise TranscriptAdapterError(f"transcript JSONL does not exist: {path}")
    if not path.is_file():
        raise TranscriptAdapterError(f"transcript JSONL is not a file: {path}")

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise TranscriptAdapterError(f"cannot read transcript JSONL: {path}") from exc
    return load_dialogue_transcripts_jsonl_bytes(
        payload,
        source_label=source_label,
        source_name=str(path),
    )


def load_dialogue_transcripts_jsonl_bytes(
    payload: bytes,
    *,
    source_label: str = "candidate",
    source_name: str = "<transcript-bytes>",
) -> tuple[DialogueTranscript, ...]:
    """Parse the exact immutable bytes whose digest is recorded by a runner."""

    if not isinstance(payload, bytes):
        raise TranscriptAdapterError("transcript JSONL payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise TranscriptAdapterError(
            f"transcript JSONL is not valid UTF-8: {source_name}"
        ) from exc

    transcripts: list[DialogueTranscript] = []
    seen_scenarios: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise TranscriptAdapterError(
                f"invalid JSON in {source_name} at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, Mapping):
            raise TranscriptAdapterError(
                f"JSONL record in {source_name} at line {line_number} must be an object"
            )
        try:
            transcript = adapt_dialogue_record(record, source_label=source_label)
        except TranscriptAdapterError as exc:
            raise TranscriptAdapterError(
                f"invalid transcript in {source_name} at line {line_number}: {exc}"
            ) from exc
        if transcript.scenario_id in seen_scenarios:
            raise TranscriptAdapterError(
                f"duplicate scenario id {transcript.scenario_id!r} "
                f"in {source_name} at line {line_number}"
            )
        seen_scenarios.add(transcript.scenario_id)
        transcripts.append(transcript)

    if not transcripts:
        raise TranscriptAdapterError(
            f"transcript JSONL contains no records: {source_name}"
        )
    return tuple(transcripts)


def adapt_catalog_products(
    products: Iterable[Product],
) -> tuple[CatalogTruthProduct, ...]:
    """Project catalogue products onto the immutable evaluator truth schema."""

    adapted: list[CatalogTruthProduct] = []
    for index, product in enumerate(products):
        if not isinstance(product, Product):
            raise TranscriptAdapterError(
                f"catalog product[{index}] must be app.models.Product"
            )
        try:
            adapted.append(
                CatalogTruthProduct(
                    sku=product.sku,
                    name=product.name,
                    price=product.price,
                    currency=product.currency,
                    stock_status=product.stock_status,
                    stock_qty=product.stock_qty,
                    url=product.url,
                )
            )
        except ValidationError as exc:
            raise TranscriptAdapterError(
                f"invalid catalog product[{index}] {product.sku!r}: {exc}"
            ) from exc
    return tuple(adapted)


__all__ = [
    "TranscriptAdapterError",
    "adapt_catalog_products",
    "adapt_dialogue_record",
    "load_dialogue_transcripts_jsonl",
    "load_dialogue_transcripts_jsonl_bytes",
]
