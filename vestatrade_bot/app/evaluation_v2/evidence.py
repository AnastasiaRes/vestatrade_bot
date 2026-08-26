"""Stable bindings between an outcome contract and its dialogue evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from .contracts import DialogueTranscript, EvidenceBinding, OutcomeContract


def _stable_model_sha256(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_evidence_binding(
    contract: OutcomeContract,
    transcript: DialogueTranscript,
) -> EvidenceBinding:
    if contract.scenario_id != transcript.scenario_id:
        raise ValueError("transcript scenario does not match outcome contract")
    return EvidenceBinding(
        contract_id=contract.contract_id,
        scenario_id=contract.scenario_id,
        contract_sha256=_stable_model_sha256(contract),
        transcript_sha256=_stable_model_sha256(transcript),
    )


def contract_sha256(contract: OutcomeContract) -> str:
    """Expose the canonical contract digest for artifact-boundary checks."""

    return _stable_model_sha256(contract)


__all__ = ["build_evidence_binding", "contract_sha256"]
