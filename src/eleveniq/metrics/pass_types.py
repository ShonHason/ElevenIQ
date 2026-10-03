"""
StatsBomb pass types that are restarts (ADR 004 D14).

The single definition of the restart exclusion, imported by progressive.py,
entries.py and the pipeline. It lives in its own module because entries.py
imports from progressive.py, and both need it.

A restart is a pass that restarts play: a set piece (corner, free kick, goal
kick, kick-off) or a throw-in. Taking restarts is a role assignment, not open-play
distribution, so restarts are excluded from:
  - progressive_passes (amends ADR 002 §1),
  - final_third_entries_pass and box_entries_pass (amends §27),
  - long_passes_attempted / _completed and their unknown-outcome and
    Injury Clearance parts (amends §19),
  - open_play_passes_attempted and team_open_play_passes_while_on (ADR 004 D12).
Crosses and switches keep restarts (StatsBomb's own flags; ADR 004 D14).
"""
from __future__ import annotations

SET_PIECE_TYPES: frozenset[str] = frozenset(
    {"Corner", "Free Kick", "Goal Kick", "Kick Off"}
)
THROW_IN: str = "Throw-in"
RESTART_TYPES: frozenset[str] = SET_PIECE_TYPES | {THROW_IN}

