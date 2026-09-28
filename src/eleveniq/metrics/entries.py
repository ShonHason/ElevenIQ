"""
Final-third and box entry metric helpers.

StatsBomb pitch: 120 × 80 normalised units.
Final third: x >= 80 (last third of the 120-unit pitch).
Penalty area: x >= 102, 18 <= y <= 62.

All four helpers operate on a single event's locations; they are called per-event
inside the pipeline aggregation loop.  Set-piece filtering for passes uses the
shared SET_PIECE_TYPES constant defined here; this constant also replaces the
former _SET_PIECE_TYPES in build_player_match.py so there is a single source of
truth for all pass-type exclusions (entries, long passes).
"""
from __future__ import annotations

from eleveniq.metrics.progressive import in_opponent_penalty_area

# Passes of these types are excluded from final-third and box-entry pass counts,
# and from long-pass counts in the pipeline.  Throw-ins, Recovery, and Interception
# types are included (territorial progression).  See ADR 002 §final_third_entries.
SET_PIECE_TYPES: frozenset[str] = frozenset(
    {"Corner", "Free Kick", "Goal Kick", "Kick Off"}
)

FINAL_THIRD_X: float = 80.0


def is_final_third_entry_pass(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    outcome: str | None,
    pass_type_name: str | None,
) -> bool:
    """
    Return True for a completed pass that enters the final third.

    Conditions:
    - outcome is None (completed pass — no outcome name in StatsBomb data)
    - start x < FINAL_THIRD_X
    - end x >= FINAL_THIRD_X
    - pass_type_name not in SET_PIECE_TYPES

    The ``outcome`` parameter is required so callers cannot forget the completion
    check — the same contract as ``is_progressive_pass``.
    """
    if outcome is not None:
        return False
    return (
        start[0] < FINAL_THIRD_X
        and end[0] >= FINAL_THIRD_X
        and pass_type_name not in SET_PIECE_TYPES
    )


def is_final_third_entry_carry(
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """Return True for a carry that crosses from x < 80 into x >= 80."""
    return start[0] < FINAL_THIRD_X and end[0] >= FINAL_THIRD_X


def is_box_entry_pass(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    outcome: str | None,
    pass_type_name: str | None,
) -> bool:
    """
    Return True for a completed pass that enters the penalty area from outside.

    Conditions:
    - outcome is None (completed pass — no outcome name in StatsBomb data)
    - start NOT in penalty area
    - end IN penalty area
    - pass_type_name not in SET_PIECE_TYPES

    The ``outcome`` parameter is required so callers cannot forget the completion
    check — the same contract as ``is_progressive_pass``.
    """
    if outcome is not None:
        return False
    return (
        not in_opponent_penalty_area(*start)
        and in_opponent_penalty_area(*end)
        and pass_type_name not in SET_PIECE_TYPES
    )


def is_box_entry_carry(
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """Return True for a carry that enters the penalty area from outside."""
    return not in_opponent_penalty_area(*start) and in_opponent_penalty_area(*end)
