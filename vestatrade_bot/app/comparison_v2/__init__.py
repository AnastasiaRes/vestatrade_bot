"""Grounded, deterministic comparison of customer-visible V2 cards."""

from .contracts import (
    ComparisonRequest,
    ComparisonResult,
    ComparisonResultStatus,
)
from .service import build_comparison_request, build_comparison_result

__all__ = (
    "ComparisonRequest",
    "ComparisonResult",
    "ComparisonResultStatus",
    "build_comparison_request",
    "build_comparison_result",
)
