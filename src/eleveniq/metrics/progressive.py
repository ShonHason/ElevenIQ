"""
Progressive pass and carry definitions.

StatsBomb pitch dimensions: 120 yards wide × 80 yards tall.
Opponent's goal centre: (120, 40).

Progressive pass (ElevenIQ definition)
---------------------------------------
A completed pass is progressive if ALL of the following hold:

  1. The pass outcome is absent (None) — i.e. the pass was completed.
     Any non-None outcome string ("Incomplete", "Out", "Unknown", etc.)
     means the pass was not completed; return False immediately.
     `outcome` is a required keyword argument so callers cannot forget it.

  2. The pass does NOT start in the attacking team's own defensive 40%
     (start_x < 48 yards — first 40% of the 120-yard pitch).

  3. The ball moves ≥ 10 yards closer to the opponent's goal centre
     (Euclidean), OR the pass ENTERS the opponent's penalty area
     (start is outside the box AND end is inside the box).

The penalty-area OR clause (condition 3) requires the pass to cross into
the box from outside.  Passes where both start and end are inside the box
are judged by the 10-yard distance rule only.  This prevents backward or
lateral passes within the penalty area from being automatically counted
as progressive.  Example: (110,40)→(108,40) is inside the box, has a
negative distance reduction, and is NOT progressive.  By contrast,
(100,40)→(103,40) enters the box from outside and IS progressive.

This is our own definition.  It is not identical to FBref's:
  - FBref uses 10 metres (~10.9 yds) as the distance threshold; we use 10 yds.
  - FBref's start-zone exclusion checks both start AND end; ours checks start only.
  - FBref excludes goal kicks; this function has no pass-type knowledge — the
    caller must filter by pass.type if needed.

Progressive carry (ElevenIQ definition)
-----------------------------------------
A carry is progressive if BOTH of the following hold:

  1. The Euclidean distance to the opponent's goal centre decreases by ≥ 5 yards.
  2. The total carry distance (straight line) is ≥ 5 yards.

Condition 2 prevents near-stationary ball-reception touches that happen to
shift slightly toward goal from being counted.

This is our own definition.  It differs from FBref's in that FBref uses
5 metres (~5.5 yards); we use 5 yards, making it slightly more permissive.
No start-zone exclusion applies to carries.
"""
from __future__ import annotations
import math

OPPONENT_GOAL: tuple[float, float] = (120.0, 40.0)

# Progressive pass thresholds
PROG_PASS_THRESHOLD_YDS: float = 10.0
DEFENSIVE_40_THRESHOLD_X: float = 48.0   # 40% of 120 yards from own goal

# Opponent penalty area boundaries (120×80 pitch)
OPP_PENALTY_AREA_X: float = 102.0       # 18 yards from opponent goal line
OPP_PENALTY_AREA_Y_MIN: float = 18.0    # 22 yards left of goal centre
OPP_PENALTY_AREA_Y_MAX: float = 62.0    # 22 yards right of goal centre

# Progressive carry thresholds
PROG_CARRY_THRESHOLD_YDS: float = 5.0
PROG_CARRY_MIN_DIST_YDS: float = 5.0


def _dist_to_goal(x: float, y: float) -> float:
    return math.hypot(x - OPPONENT_GOAL[0], y - OPPONENT_GOAL[1])


def _in_opponent_penalty_area(x: float, y: float) -> bool:
    return (
        x >= OPP_PENALTY_AREA_X
        and OPP_PENALTY_AREA_Y_MIN <= y <= OPP_PENALTY_AREA_Y_MAX
    )


def is_progressive_pass(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    outcome: str | None,
    threshold: float = PROG_PASS_THRESHOLD_YDS,
) -> bool:
    """
    Return True if a completed pass qualifies as progressive.

    Args:
        start:   (x, y) start location of the pass.
        end:     (x, y) end location of the pass.
        outcome: value of pass.outcome.name from the StatsBomb event, or None
                 if the field is absent.  In StatsBomb, a completed pass has
                 NO outcome field (None here); an incomplete pass has an outcome
                 string ("Incomplete", "Out", "Unknown", etc.).
                 This is a required keyword argument — omitting it is a TypeError.
        threshold: distance-reduction threshold in yards (default 10.0).

    See module docstring for the full progressive-pass definition.
    """
    if outcome is not None:
        return False
    if start[0] < DEFENSIVE_40_THRESHOLD_X:
        return False
    dist_reduction = _dist_to_goal(*start) - _dist_to_goal(*end)
    entered_box = (
        not _in_opponent_penalty_area(*start) and _in_opponent_penalty_area(*end)
    )
    return dist_reduction >= threshold or entered_box


def is_progressive_carry(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    threshold: float = PROG_CARRY_THRESHOLD_YDS,
    min_distance: float = PROG_CARRY_MIN_DIST_YDS,
) -> bool:
    """
    Return True if a carry qualifies as progressive.

    Both conditions must hold:
    - Distance to opponent's goal centre decreases by at least `threshold` yards.
    - The carry covers at least `min_distance` yards end-to-end.
    """
    dist_reduction = _dist_to_goal(*start) - _dist_to_goal(*end)
    carry_length = math.hypot(end[0] - start[0], end[1] - start[1])
    return dist_reduction >= threshold and carry_length >= min_distance
