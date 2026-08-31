"""Pure goal-scoped helpers for V2 catalogue ordering preferences.

The reducer owns persistence.  This module only selects the latest applicable
preference and a source-revision-compatible delivered scope; it never searches
the catalogue or interprets customer prose.
"""

from __future__ import annotations

from .contracts import (
    DeliveredSelectionScope,
    DialogueStateV2,
    SelectionPreferenceKind,
    SelectionPreferenceSignal,
)


_PRICE_KINDS = {
    SelectionPreferenceKind.PRICE_LOWEST,
    SelectionPreferenceKind.PRICE_BELOW_REFERENCE,
}
_BRAND_KINDS = {
    SelectionPreferenceKind.BRAND_REQUIRED,
    SelectionPreferenceKind.BRAND_PREFERRED,
    SelectionPreferenceKind.BRAND_ANY,
}


def active_selection_preferences(
    state: DialogueStateV2,
    *,
    task_id: str,
    goal_id: str | None,
) -> tuple[SelectionPreferenceSignal, ...]:
    """Return the latest preference of each independent kind for one task.

    A task can retain an older brand preference while a later price request
    changes its ordering.  Price modes are mutually exclusive: the most recent
    ``price_lowest`` or ``price_below_reference`` wins for that task only.
    """

    scoped = [
        item
        for item in state.selection_preferences
        if item.task_id == task_id
        and (goal_id is None or item.goal_id == goal_id)
    ]
    latest: dict[object, SelectionPreferenceSignal] = {}
    for item in scoped:
        key: object = (
            "price"
            if item.kind in _PRICE_KINDS
            else ("brand" if item.kind in _BRAND_KINDS else item.kind)
        )
        previous = latest.get(key)
        if previous is None or (item.source_turn, item.preference_id) > (
            previous.source_turn,
            previous.preference_id,
        ):
            latest[key] = item
    return tuple(
        sorted(
            latest.values(),
            key=lambda item: (item.source_turn, item.preference_id),
        )
    )


def price_preference(
    state: DialogueStateV2,
    *,
    task_id: str,
    goal_id: str | None,
) -> SelectionPreferenceSignal | None:
    return next(
        (
            item
            for item in reversed(
                active_selection_preferences(
                    state,
                    task_id=task_id,
                    goal_id=goal_id,
                )
            )
            if item.kind in _PRICE_KINDS
        ),
        None,
    )


def latest_delivered_scope(
    state: DialogueStateV2,
    *,
    task_id: str,
    goal_id: str | None,
    catalog_revision: str,
) -> DeliveredSelectionScope | None:
    """Return only a delivered selection from the same task/goal/revision."""

    candidates = [
        item
        for item in state.delivered_selection_scopes
        if item.catalog_revision == catalog_revision
        and (
            (goal_id is not None and item.goal_id == goal_id)
            or (goal_id is None and item.task_id == task_id)
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.source_turn, item.delivery_id, item.scope_id),
    )
