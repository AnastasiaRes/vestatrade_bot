"""Deterministic, goal-safe recognition of a return to an earlier topic.

The semantic LLM remains responsible for interpreting the rest of a free-form
turn.  An explicit phrase such as ``Вернёмся к насосу`` is different: selecting
which stored internal goal it means must be repeatable, observable and bounded
by the already confirmed customer state.  This module never searches the
catalogue and never creates, mutates or guesses a goal.
"""

from __future__ import annotations

import re

from app.agents.domain_ontology import PRODUCT_TYPE_ONTOLOGY

from .contracts import (
    DialogueStateV2,
    GoalReactivationResolution,
    ProductGoal,
    TaskAct,
    TaskStatus,
)


_RETURN_MARKER_RE = re.compile(
    r"(?iu)(?:\bверн(?:е|ё)мся\b|\bвернись\b|\bвернитесь\b|"
    r"\bдавай\s+назад\b|\bназад\s+к\b|\bобратно\s+к\b|"
    r"\bчто\s+там\s+по\b|\bпокажи\s+(?:еще|ещё)\s+раз\b)"
)
_WORD_RE = re.compile(r"(?iu)[0-9a-zа-яё]+")


def _topic_word(value: str) -> str:
    """A narrow Russian inflection key for exact ontology words.

    It deliberately does not perform fuzzy catalogue matching.  Removing a
    final vowel lets ``котлу`` and ``котёл`` share the compact key ``котл``;
    ambiguous matches remain ambiguous instead of picking a latest goal.
    """

    normalized = value.casefold().replace("ё", "е")
    if len(normalized) >= 4 and normalized[-1:] in "аеёиоуыэюяь":
        normalized = normalized[:-1]
    return normalized


def _phrase_words(value: str) -> tuple[str, ...]:
    return tuple(
        word
        for token in _WORD_RE.findall(value.casefold().replace("ё", "е"))
        if len(word := _topic_word(token)) >= 3
    )


def _goal_aliases(goal: ProductGoal) -> tuple[tuple[str, ...], ...]:
    canonical = (goal.canonical_type or "").casefold().replace("_", " ")
    aliases: list[tuple[str, ...]] = []
    for definition in PRODUCT_TYPE_ONTOLOGY:
        definition_canonical = str(definition.get("canonical_type") or "")
        if definition_canonical.casefold().replace("_", " ") != canonical:
            continue
        for item in (definition_canonical, *(definition.get("aliases") or ())):
            words = _phrase_words(str(item))
            if words:
                aliases.append(words)
                # A buyer commonly says only the product noun (``к насосу``)
                # rather than repeating the full ontology phrase.  This is
                # still safe because resolution is limited to suspended goals:
                # several pump goals remain an explicit ambiguity.
                if len(words[-1]) >= 4:
                    aliases.append((words[-1],))
    canonical_words = _phrase_words(canonical)
    if canonical_words:
        aliases.append(canonical_words)
    # Preserve deterministic ordering while discarding duplicates from aliases
    # such as ``котел``/``котёл`` after normalisation.
    return tuple(dict.fromkeys(aliases))


def _matches_topic(message_words: tuple[str, ...], goal: ProductGoal) -> int:
    """Return the strongest controlled ontology alias match for one goal."""

    message_set = set(message_words)
    best = 0
    for alias in _goal_aliases(goal):
        alias_set = set(alias)
        if alias_set and alias_set.issubset(message_set):
            best = max(best, len(alias_set))
    return best


def _reactivatable_goal_ids(state: DialogueStateV2) -> set[str]:
    """Only a paused selection goal is eligible for implicit resumption."""

    result = {
        scope.goal_id
        for scope in state.delivered_selection_scopes
    }
    result.update(
        task.target_goal_id
        for task in state.tasks
        if (
            task.target_goal_id is not None
            and task.status == TaskStatus.SUSPENDED
            and task.act in {TaskAct.FIND, TaskAct.SELECT}
        )
    )
    return result


def resolve_goal_reactivation(
    message: str,
    state: DialogueStateV2 | None,
) -> GoalReactivationResolution:
    """Resolve an explicit return without a last-goal heuristic.

    A generic return is accepted only if exactly one old selectable goal is
    paused.  With two plausible old goals the caller must ask which topic the
    customer means; returning to the most recent one would silently bind
    ordinals and new constraints to the wrong product family.
    """

    marker = _RETURN_MARKER_RE.search(message)
    if marker is None:
        return GoalReactivationResolution(status="not_requested")
    if state is None:
        return GoalReactivationResolution(
            status="not_found",
            evidence=marker.group(0),
            reason_codes=("return_without_typed_state",),
        )

    eligible_goal_ids = _reactivatable_goal_ids(state)
    candidates = [
        goal
        for goal in state.product_goals
        if goal.goal_id in eligible_goal_ids and goal.goal_id != state.active_goal_id
    ]
    if not candidates:
        return GoalReactivationResolution(
            status="not_found",
            evidence=marker.group(0),
            reason_codes=("return_target_not_found",),
        )

    words = _phrase_words(message)
    scored = [
        (goal, _matches_topic(words, goal))
        for goal in candidates
    ]
    named = [(goal, score) for goal, score in scored if score > 0]
    if named:
        best_score = max(score for _, score in named)
        best = [goal for goal, score in named if score == best_score]
        if len(best) == 1:
            return GoalReactivationResolution(
                status="resolved",
                target_goal_id=best[0].goal_id,
                evidence=marker.group(0),
                candidate_goal_ids=(best[0].goal_id,),
                reason_codes=("explicit_return_goal_resolved",),
            )
        return GoalReactivationResolution(
            status="ambiguous",
            evidence=marker.group(0),
            candidate_goal_ids=tuple(goal.goal_id for goal in best),
            reason_codes=("explicit_return_topic_ambiguous",),
        )

    # The user explicitly asked to return but did not name a matching topic.
    # The sole paused selectable goal is a deterministic and safe default.
    if len(candidates) == 1:
        return GoalReactivationResolution(
            status="resolved",
            target_goal_id=candidates[0].goal_id,
            evidence=marker.group(0),
            candidate_goal_ids=(candidates[0].goal_id,),
            reason_codes=("generic_return_single_goal_resolved",),
        )
    return GoalReactivationResolution(
        status="ambiguous",
        evidence=marker.group(0),
        candidate_goal_ids=tuple(goal.goal_id for goal in candidates),
        reason_codes=("return_target_requires_topic_clarification",),
    )
