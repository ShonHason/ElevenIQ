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

Use compute_minutes_per_group() (or minutes_per_group() on a timeline from
minutes.player_timeline) to build the per-match contribution, then
primary_position_group() to resolve the season-level assignment.
"""
from __future__ import annotations

from collections.abc import Iterable

from eleveniq.metrics.minutes import Instant, PeriodClock, PlayerTimeline, player_timeline

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


def minutes_per_group(timeline: PlayerTimeline) -> dict[str, float]:
    """
    position_group → minutes for one player-match, from his timeline
    (minutes.player_timeline, ADR 002 §31.4). Every piece of credited time has
    exactly one position, so the values sum to timeline.minutes. An unmapped
    or null position is UNKNOWN (which fails the build in build_player_match).
    """
    result: dict[str, float] = {}
    for piece in timeline.pieces:
        group = assign_position_group(piece.position)
        result[group] = result.get(group, 0.0) + piece.minutes
    return result


def minutes_per_raw_position(timeline: PlayerTimeline) -> dict[str, float]:
    """StatsBomb position name → minutes, from the same timeline as minutes_per_group."""
    result: dict[str, float] = {}
    for piece in timeline.pieces:
        result[piece.position] = result.get(piece.position, 0.0) + piece.minutes
    return result


def compute_minutes_per_group(
    positions: list[dict],
    clock: PeriodClock,
    *,
    exit_at: Instant | None,
    treatment_windows: Iterable[tuple[Instant, Instant]],
) -> dict[str, float]:
    """
    position_group → minutes for one match (ADR 002 §31.4): the clock, exit
    cap, treatment windows, union and position rule of minutes.player_timeline.

    exit_at (minutes.stint_end_cap) and treatment_windows are required
    keywords with no default, so a forgotten argument cannot silently give
    uncapped minutes. The `position` field of a stint may be a plain string
    (StatsBomb open data) or a dict with a 'name' key; a null position is
    UNKNOWN. Returns an empty dict for players who never took the field.
    """
    return minutes_per_group(player_timeline(
        positions, clock, exit_at=exit_at, treatment_windows=treatment_windows))


def compute_minutes_per_raw_position(
    positions: list[dict],
    clock: PeriodClock,
    *,
    exit_at: Instant | None,
    treatment_windows: Iterable[tuple[Instant, Instant]],
) -> dict[str, float]:
    """
    StatsBomb position name → minutes for one match. Uses the same timeline as
    compute_minutes_per_group, so the two always sum to the same total.
    """
    return minutes_per_raw_position(player_timeline(
        positions, clock, exit_at=exit_at, treatment_windows=treatment_windows))


def primary_position_group(minutes_per_group: dict[str, float]) -> str:
    """
    Return the position group in which the player spent the most minutes.

    Tie-break: alphabetically first group name (deterministic, rarely applies).
    Returns UNKNOWN_POSITION_GROUP for an empty input.
    """
    if not minutes_per_group:
        return UNKNOWN_POSITION_GROUP
    return min(minutes_per_group, key=lambda g: (-minutes_per_group[g], g))
