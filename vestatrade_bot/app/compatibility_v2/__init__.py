"""Grounded, source-bound V2 compatibility checks."""

from .contracts import CompatibilityResult, CompatibilityResultStatus
from .service import InterfaceFactService, build_compatibility_request, build_compatibility_result

__all__ = (
    "CompatibilityResult",
    "CompatibilityResultStatus",
    "InterfaceFactService",
    "build_compatibility_request",
    "build_compatibility_result",
)
