"""
StatsBomb position name → ElevenIQ position group mapping.

Groups used throughout the pipeline:
    GK          Goalkeeper (separate from all outfield comparisons)
    CB          Centre backs
    FB-WB       Full-backs and wing-backs
    DM-CM       Defensive and central midfielders
    AM-Winger   Attacking midfielders and wingers
    ST          Forwards / strikers
    UNKNOWN     Any position name not in the map (surfaces in quality report)

GK is an explicit separate group.  Percentile calculations for outfield players
must exclude GK rows.  Use OUTFIELD_GROUPS to filter.

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
from eleveniq.metrics.minutes import parse_mm_ss

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


def assign_position_group(position_name: str) -> str:
    """Return the ElevenIQ position group for a StatsBomb position name."""
    return POSITION_GROUP_MAP.get(position_name, UNKNOWN_POSITION_GROUP)


def compute_minutes_per_group(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None = None,
) -> dict[str, float]:
    """
    Return a mapping of position_group → minutes played in that group for
    one match.

    Handles multi-stint appearances (tactical shifts) and dismissals.
    The `position` field in each stint may be a plain string (confirmed in
    StatsBomb open data) or a dict with a 'name' key (schema robustness).
    A null/None position is treated as UNKNOWN and included in the result.

    Returns an empty dict for players who never took the field.
    """
    if not positions:
        return {}
    effective_end = dismissal_minute if dismissal_minute is not None else match_duration
    result: dict[str, float] = {}
    for stint in positions:
        start = parse_mm_ss(stint["from"])
        end = parse_mm_ss(stint["to"]) if stint.get("to") else effective_end
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
        group = assign_position_group(position_name)
        result[group] = result.get(group, 0.0) + duration
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
