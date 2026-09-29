"""
Shot outcome classification.

On-target definition
--------------------
A shot is on target if its outcome is one of:
  "Goal"         – the ball entered the net
  "Saved"        – the goalkeeper stopped it
  "Saved to Post" – the goalkeeper parried it onto the post

Not on target:
  "Blocked"      – stopped by a defender before reaching the goalkeeper
  "Post"         – hit the woodwork without being saved (missed the goal)
  "Off T"        – off target (wide or high)
  "Wayward"      – clearly wide or high
  "No Touch"     – goalkeeper dived but did not touch (counted as off target)
  "Saved Off Target" – the goalkeeper saved a shot that was going wide or high
                   (StatsBomb: not on target)

Values are exact StatsBomb strings, casing included ("Saved to Post", lower-case
"to"; matched exactly, never case-insensitively). ADR 002 §30.

This definition matches the standard used by football data providers (Opta,
StatsBomb) and is consistent with the rule that the goalkeeper must touch the
ball for a shot to be on target.

Any shot.outcome.name value not in KNOWN_SHOT_OUTCOMES fails the build
(build_player_match: "unknown_shot_outcome" quality issue).
"""
from __future__ import annotations

SHOTS_ON_TARGET_OUTCOMES: frozenset[str] = frozenset({
    "Goal",
    "Saved",
    "Saved to Post",
})

KNOWN_SHOT_OUTCOMES: frozenset[str] = frozenset({
    "Goal",
    "Saved",
    "Saved to Post",
    "Blocked",
    "Post",
    "Off T",
    "Wayward",
    "No Touch",
    "Saved Off Target",
})


def is_shot_on_target(outcome_name: str | None) -> bool:
    """Return True if the shot outcome counts as on target."""
    return outcome_name in SHOTS_ON_TARGET_OUTCOMES
