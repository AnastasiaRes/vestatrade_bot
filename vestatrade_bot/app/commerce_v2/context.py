"""Narrow adapters from legacy session/business data to PII-free V2 snapshots."""

from __future__ import annotations

from typing import Any

from .contracts import (
    CommerceContextSnapshot,
    CommerceFieldStatus,
    SensitiveValueKind,
    SensitiveValueRef,
)


def build_commerce_context_snapshot(
    session_state: Any,
    business_facts: Any | None = None,
) -> CommerceContextSnapshot:
    """Expose only capability/presence metadata; never copy sensitive values."""

    contact_ref = None
    if getattr(session_state, "contact", None):
        contact_ref = SensitiveValueRef(
            ref_id="legacy_session_customer_contact",
            kind=SensitiveValueKind.CONTACT,
            field_name="contact_ref",
            status=CommerceFieldStatus.KNOWN,
            source="legacy_session_contact_adapter",
            source_turn=max(0, int(getattr(session_state, "contact_turn", 0) or 0)),
        )
    present: list[str] = []
    drafted: tuple[str, ...] = ()
    if business_facts is not None:
        drafted = tuple(getattr(business_facts, "drafted_sections", ()) or ())
        for key in (
            "delivery",
            "payment",
            "returns",
            "warranty",
            "business_hours",
            "pickup_points",
            "branches",
            "response_time",
            "lead_times",
        ):
            if getattr(business_facts, key, None) and key not in drafted:
                present.append(key)
    return CommerceContextSnapshot(
        contact_ref=contact_ref,
        business_fact_keys=tuple(present),
        drafted_business_fact_keys=drafted,
        legacy_handoff_status=(
            str(getattr(session_state, "handoff_status", "") or "") or None
        ),
        legacy_has_pending_handoff=bool(
            getattr(session_state, "pending_handoff", None)
        ),
        legacy_handoff_ticket_present=bool(
            getattr(session_state, "handoff_ticket_id", None)
        ),
    )
