"""Typed, read-only resolution of customer-visible V2 product references.

The V2 capabilities have different output contracts, but ``первый``, ``этот``
and the ordered customer-visible scope must mean the same thing everywhere.
This module is deliberately small: it neither searches the catalogue nor
changes session state.  A successful Selection remains the only writer of the
scope; callers only read the frozen order, selection identity and focus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import SessionState


_ORDINALS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("перв", "1-й", "1я", "1го", "первую", "первый", "первого"), 0),
    (("втор", "2-й", "2я", "2го", "вторую", "второй", "второго"), 1),
    (("трет", "3-й", "3я", "3го", "третью", "третий", "третьего"), 2),
    (("четверт", "4-й", "4я", "4го", "четвёрт", "четвертый"), 3),
    (("пят", "5-й", "5я", "5го", "пятую", "пятый", "пятого"), 4),
)
_DEICTIC_RE = re.compile(
    r"(?iu)\b(?:этот|эта|это|эти|этой|этого|этому|нем[уё]|не[йм]|его|ее)\b"
)


def normalise_product_reference_text(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class CustomerVisibleProductScope:
    """The only V2 scope valid for ordinal/deictic product references."""

    ordered_skus: tuple[str, ...] = ()
    selection_id: str | None = None
    source_revision: str | None = None
    focus_sku: str | None = None
    reason_code: str = "customer_visible_v2_scope_missing"

    @property
    def is_valid(self) -> bool:
        return bool(
            self.ordered_skus
            and self.selection_id
            and self.source_revision
            and len(self.ordered_skus) == len(set(self.ordered_skus))
        )

    def matches_revision(self, source_revision: str | None) -> bool:
        return self.is_valid and self.source_revision == source_revision

    def ordinal(self, index: int) -> "VisibleProductReference":
        if not self.is_valid:
            return VisibleProductReference(
                raw=str(index + 1),
                ordinal=index,
                reason_code=self.reason_code,
            )
        if 0 <= index < len(self.ordered_skus):
            return VisibleProductReference(
                raw=str(index + 1),
                canonical_sku=self.ordered_skus[index],
                ordinal=index,
                reason_code="ordinal_in_customer_visible_v2_scope",
            )
        return VisibleProductReference(
            raw=str(index + 1),
            ordinal=index,
            reason_code="ordinal_outside_customer_visible_v2_scope",
        )

    def current_focus(self) -> "VisibleProductReference":
        if not self.is_valid:
            return VisibleProductReference(reason_code=self.reason_code)
        if self.focus_sku in self.ordered_skus:
            return VisibleProductReference(
                raw="этот",
                canonical_sku=self.focus_sku,
                reason_code="deictic_focus_in_customer_visible_v2_scope",
            )
        return VisibleProductReference(
            raw="этот",
            reason_code="deictic_focus_missing_or_outside_customer_visible_v2_scope",
        )

    def single_product(self) -> "VisibleProductReference":
        if not self.is_valid:
            return VisibleProductReference(reason_code=self.reason_code)
        if len(self.ordered_skus) == 1:
            return VisibleProductReference(
                raw=self.ordered_skus[0],
                canonical_sku=self.ordered_skus[0],
                reason_code="single_customer_visible_v2_card",
            )
        return VisibleProductReference(
            reason_code="multiple_customer_visible_v2_cards_require_reference"
        )


@dataclass(frozen=True)
class VisibleProductReference:
    """A resolved customer-visible reference, never a catalogue match."""

    raw: str = ""
    canonical_sku: str | None = None
    ordinal: int | None = None
    reason_code: str = "product_reference_not_grounded"

    @property
    def resolved(self) -> bool:
        return self.canonical_sku is not None


def customer_visible_v2_scope(session: SessionState) -> CustomerVisibleProductScope:
    """Read the versioned V2 selection without falling back to Legacy cards."""

    ordered_skus = tuple(card.sku for card in session.v2_last_products)
    if not ordered_skus:
        return CustomerVisibleProductScope()
    if not session.v2_selection_id:
        return CustomerVisibleProductScope(
            ordered_skus=ordered_skus,
            source_revision=session.v2_source_revision,
            focus_sku=session.product_focus.sku if session.product_focus else None,
            reason_code="customer_visible_v2_selection_id_missing",
        )
    if not session.v2_source_revision:
        return CustomerVisibleProductScope(
            ordered_skus=ordered_skus,
            selection_id=session.v2_selection_id,
            focus_sku=session.product_focus.sku if session.product_focus else None,
            reason_code="customer_visible_v2_source_revision_missing",
        )
    if len(ordered_skus) != len(set(ordered_skus)):
        return CustomerVisibleProductScope(
            ordered_skus=ordered_skus,
            selection_id=session.v2_selection_id,
            source_revision=session.v2_source_revision,
            focus_sku=session.product_focus.sku if session.product_focus else None,
            reason_code="customer_visible_v2_scope_order_not_unique",
        )
    return CustomerVisibleProductScope(
        ordered_skus=ordered_skus,
        selection_id=session.v2_selection_id,
        source_revision=session.v2_source_revision,
        focus_sku=session.product_focus.sku if session.product_focus else None,
        reason_code="customer_visible_v2_scope_valid",
    )


def ordinal_indices(message: str) -> tuple[int, ...]:
    """Return distinct ordinal references in the order the customer used them."""

    text = normalise_product_reference_text(message)
    found: list[tuple[int, int]] = []
    for aliases, index in _ORDINALS:
        positions = [text.find(alias) for alias in aliases if text.find(alias) >= 0]
        if positions:
            found.append((min(positions), index))
    ordered: list[int] = []
    for _, index in sorted(found):
        if index not in ordered:
            ordered.append(index)
    return tuple(ordered)


def has_deictic_product_reference(message: str) -> bool:
    return _DEICTIC_RE.search(normalise_product_reference_text(message)) is not None
