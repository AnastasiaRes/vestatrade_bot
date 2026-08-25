"""Capability port defaults; Stage 4 never wires execution into the controller."""

from __future__ import annotations

from .contracts import (
    CommerceCapabilitySnapshot,
    CommerceCommand,
    CommerceExecutionResult,
    CommerceExecutionStatus,
)
from .registry import build_capability_snapshot


class UnavailableCommerceGateway:
    """Honest default when no transactional integration is configured."""

    def describe_capabilities(self) -> CommerceCapabilitySnapshot:
        return build_capability_snapshot()

    def execute(self, command: CommerceCommand) -> CommerceExecutionResult:
        return CommerceExecutionResult(
            command_id=command.command_id,
            capability_id=command.capability_id,
            status=CommerceExecutionStatus.FAILED,
            reason_code="commerce_external_execution_unavailable",
        )
