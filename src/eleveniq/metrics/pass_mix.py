"""
Pass-mix buckets for completion above expected (plan 006 pre-registration, user 2026-10-10).

Pure functions on one StatsBomb pass payload. Spec 1 buckets a pass by length (StatsBomb units
are yards): short < 15, medium 15 to < 30, long ≥ 30 (the long-pass threshold). Spec 2 adds the
direction from pass.angle (radians, 0 = towards the opponent goal): forward |a| ≤ π/4,
backward |a| ≥ 3π/4, sideways otherwise.

A pass counts towards completion when its outcome is known: no outcome = completed; Unknown and
Injury Clearance are left out (as pass_completion_pct); any other outcome is incomplete.
"""
from __future__ import annotations

import math

from eleveniq.config import LONG_PASS_THRESHOLD_UNITS

SHORT_MAX_UNITS: float = 15.0
LENGTHS: tuple[str, ...] = ("short", "medium", "long")
DIRECTIONS: tuple[str, ...] = ("forward", "sideways", "backward")
UNKNOWN_OUTCOME = "Unknown"
INJURY_CLEARANCE = "Injury Clearance"


def length_bucket(length: float) -> str:
    """short < 15, medium 15 to < LONG_PASS_THRESHOLD_UNITS, long ≥ it."""
    if length < 0:
        raise ValueError(f"negative pass length {length}")
    if length < SHORT_MAX_UNITS:
        return "short"
    return "medium" if length < LONG_PASS_THRESHOLD_UNITS else "long"


def direction_bucket(angle: float) -> str:
    """forward |a| ≤ π/4, backward |a| ≥ 3π/4, sideways otherwise (radians, 0 = attacking)."""
    a = abs(angle)
    if a > math.pi + 1e-6:          # StatsBomb rounds angles to 7 decimals (3.1415927)
        raise ValueError(f"pass angle out of range {angle}")
    if a <= math.pi / 4:
        return "forward"
    return "backward" if a >= 3 * math.pi / 4 else "sideways"


def outcome_counts(pass_data: dict) -> tuple[bool, bool]:
    """(counted, completed): counted when the outcome is known (not Unknown or Injury
    Clearance); completed when there is no outcome."""
    outcome = (pass_data.get("outcome") or {}).get("name")
    if outcome in (UNKNOWN_OUTCOME, INJURY_CLEARANCE):
        return False, False
    return True, outcome is None


def bucket_column(length: str, direction: str, kind: str) -> str:
    """The parquet column of one length × direction bucket: e.g. pm_short_forward_att."""
    return f"pm_{length}_{direction}_{kind}"
