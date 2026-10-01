"""
StatsBomb position name → ElevenIQ position group mapping.

Groups used throughout the pipeline:
    GK          Goalkeeper (separate from all outfield comparisons)
    CB          Centre backs
    FB-WB       Full-backs and wing-backs
    DM-CM       Defensive and central midfielders
    AM-Winger   Attacking midfielders and wingers
    ST          Forwards / strikers
    UNKNOWN     Any position name not in the map (any UNKNOWN minutes fail the
                build in build_player_match)

GK is an explicit separate group.  Cohorts filter on the season-level
is_outfield column (position_group in OUTFIELD_GROUPS).

Positions not listed below will be mapped to UNKNOWN_POSITION_GROUP.
The map is kept here so position grouping is auditable and configurable
without touching aggregation code.

Season-level position group
----------------------------
At season level, each player is assigned the group in which they accumulated
the most minutes (most-minutes wins).  On an exact tie, the group that sorts
first alphabetically is chosen as a deterministic secondary tie-break
(note: AM-Winger sorts before CB, which sorts before DM-CM, etc. — so a
DM-CM / AM-Winger tie resolves to AM-Winger.  This is the secondary tie-break
only and rarely applies in practice).

Use compute_minutes_per_group() to build the per-match contribution, then
primary_position_group() to resolve the season-level assignment.
"""
from __future__ import annotations
from collections.abc import Iterator
from eleveniq.metrics.minutes import parse_mm_ss, stint_end_cap

POSITION_GROUP_MAP: dict[str, str] = {
    # Goalkeeper
    "Goalkeeper": "GK",
    # Centre backs
    "Center Back": "CB",
    "Left Center Back": "CB",
    "Right Center Back": "CB",
    # Full-backs / wing-backs
    "Left Back": "FB-WB",
    "Right Back": "FB-WB",
    "Left Wing Back": "FB-WB",
    "Right Wing Back": "FB-WB",
    # Defensive / central midfield
    "Defensive Midfield": "DM-CM",
    "Center Defensive Midfield": "DM-CM",
    "Left Defensive Midfield": "DM-CM",
    "Right Defensive Midfield": "DM-CM",
    "Center Midfield": "DM-CM",
    "Left Center Midfield": "DM-CM",
    "Right Center Midfield": "DM-CM",
    # Attacking midfield / wingers
    "Attacking Midfield": "AM-Winger",
    "Center Attacking Midfield": "AM-Winger",
    "Left Midfield": "AM-Winger",
    "Right Midfield": "AM-Winger",
    "Left Wing": "AM-Winger",
    "Right Wing": "AM-Winger",
    "Left Attacking Midfield": "AM-Winger",
    "Right Attacking Midfield": "AM-Winger",
    # Forwards
    "Center Forward": "ST",
    "Left Center Forward": "ST",
    "Right Center Forward": "ST",
    "Secondary Striker": "ST",
}

UNKNOWN_POSITION_GROUP: str = "UNKNOWN"
GK_GROUP: str = "GK"
OUTFIELD_GROUPS: frozenset[str] = frozenset({"CB", "FB-WB", "DM-CM", "AM-Winger", "ST"})

# Canonical mapping: position group → minute column name in player_match_stats.parquet.
# Alphabetical order so pipeline code can rely on insertion order for tie-breaks
# (AM-Winger sorts first, matching primary_position_group's lexicographic tie-break rule).
POSITION_GROUP_COLUMNS: dict[str, str] = {
    "AM-Winger": "minutes_am_winger",
    "CB":        "minutes_cb",
    "DM-CM":     "minutes_dm_cm",
    "FB-WB":     "minutes_fb_wb",
    "GK":        "minutes_gk",
    "ST":        "minutes_st",
    "UNKNOWN":   "minutes_unknown",
}


def assign_position_group(position_name: str) -> str:
    """Return the ElevenIQ position group for a StatsBomb position name."""
    return POSITION_GROUP_MAP.get(position_name, UNKNOWN_POSITION_GROUP)


def _iter_stint_contributions(
    positions: list[dict],
    match_duration: float,
    dismissal_minute: float | None,
    substitution_minute: float | None = None,
) -> Iterator[tuple[str, float]]:
    """
    Yield (raw_position_name, duration_minutes) for every non-zero stint.

    This is the single shared function underlying both compute_minutes_per_group
    and compute_minutes_per_raw_position, ensuring they can never diverge.

    Exit clamping (ADR 002 §3)
    --------------------------
    The exit cap is the earlier of dismissal_minute and substitution_minute
    (stint_end_cap). When there is one, ALL stint end-times are clamped to
    min(end, cap) — including stints with explicit `to` values. StatsBomb
    sometimes keeps a stint running past the card minute or past the player's
    own Substitution event (lineup not corrected). Without this clamp those
    stints would overcount minutes.

    Without an exit cap, explicit `to` values are used as-is.
    match_duration is derived from the last event timestamp and can be a few
    seconds earlier than the real final whistle, so clamping against it would
    risk truncating a legitimate substitution timestamp.
    """
    cap = stint_end_cap(dismissal_minute, substitution_minute)
    effective_end = cap if cap is not None else match_duration
    for stint in positions:
        start = parse_mm_ss(stint["from"])
        if stint.get("to"):
            raw_end = parse_mm_ss(stint["to"])
            # Clamp only when there is an exit cap (see docstring).
            end = min(raw_end, cap) if cap is not None else raw_end
        else:
            end = effective_end
        duration = max(0.0, end - start)
        if duration == 0.0:
            continue
        pos = stint.get("position")
        if pos is None:
            position_name = ""
        elif isinstance(pos, dict):
            position_name = pos.get("name", "")
        else:
            position_name = str(pos)
        yield position_name, duration


def compute_minutes_per_group(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None = None,
    substitution_minute: float | None = None,
) -> dict[str, float]:
    """
    Return a mapping of position_group → minutes played in that group for
    one match.

    Handles multi-stint appearances (tactical shifts), dismissals and the
    player's own substitution off (the exit cap, ADR 002 §3).
    The `position` field in each stint may be a plain string (confirmed in
    StatsBomb open data) or a dict with a 'name' key (schema robustness).
    A null/None position is treated as UNKNOWN and included in the result.

    Returns an empty dict for players who never took the field.
    """
    if not positions:
        return {}
    result: dict[str, float] = {}
    for pos_name, duration in _iter_stint_contributions(
        positions, match_duration, dismissal_minute, substitution_minute
    ):
        group = assign_position_group(pos_name)
        result[group] = result.get(group, 0.0) + duration
    return result


def compute_minutes_per_raw_position(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None = None,
    substitution_minute: float | None = None,
) -> dict[str, float]:
    """
    Return a mapping of StatsBomb position name → minutes for one match.

    Uses the same shared stint logic as compute_minutes_per_group, so
    sum(compute_minutes_per_raw_position(...).values()) always equals
    sum(compute_minutes_per_group(...).values()) for the same inputs.
    """
    if not positions:
        return {}
    result: dict[str, float] = {}
    for pos_name, duration in _iter_stint_contributions(
        positions, match_duration, dismissal_minute, substitution_minute
    ):
        result[pos_name] = result.get(pos_name, 0.0) + duration
    return result


def primary_position_group(minutes_per_group: dict[str, float]) -> str:
    """
    Return the position group in which the player spent the most minutes.

    Tie-break: alphabetically first group name (deterministic, rarely applies).
    Returns UNKNOWN_POSITION_GROUP for an empty input.
    """
    if not minutes_per_group:
        return UNKNOWN_POSITION_GROUP
    return min(minutes_per_group, key=lambda g: (-minutes_per_group[g], g))
