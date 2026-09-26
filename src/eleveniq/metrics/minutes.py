"""
Minutes-played calculation from StatsBomb lineup positions.

Key confirmed facts (from StatsBomb open data schema probe, match 3788741):
- Lineup positions[].from / .to are in "MM:SS" format and are MATCH-ABSOLUTE,
  not period-relative. A substitute who came on at 73 minutes has from="73:10".
- to=None means the player played to the actual final whistle of the match.
- Unused substitutes have an empty positions array.
- event.minute is also match-absolute (same coordinate system).
- Period 5 (penalty shootout) is excluded from match duration.

Red card / dismissal handling
------------------------------
StatsBomb should set positions[].to to the dismissal minute for a sent-off
player. If that field is missing (to=None for a dismissed player), minutes
will be overcounted. The optional `dismissal_minute` parameter to
`compute_player_minutes` guards against this: if provided, any open-ended
stint (to=None) is capped at the dismissal minute instead of match_duration.

The quality report verifies, per match, that no dismissed player's computed
minutes significantly exceed their card minute (see
eleveniq.pipeline.quality_report).
"""
from __future__ import annotations


def parse_mm_ss(time_str: str) -> float:
    """
    Parse a StatsBomb position timestamp to decimal minutes.

    Accepts "MM:SS", "MM:SS.mmm", or "H:MM:SS" (the spec allows optional hours).
    Examples:
        "00:00"     → 0.0
        "45:00"     → 45.0
        "73:10"     → 73.1667...
        "90:00.000" → 90.0
    """
    main = time_str.split(".")[0]
    parts = main.split(":")
    if len(parts) == 3:
        return float(parts[0]) * 60 + float(parts[1]) + float(parts[2]) / 60
    return float(parts[0]) + float(parts[1]) / 60


def compute_match_duration(events: list[dict]) -> float:
    """
    Actual match end time in decimal minutes, including stoppage time.

    Uses the last event's match-absolute minute + second/60. Period 5 (penalty
    shootout) is excluded — it is not meaningful playing time.

    Note: this is the timestamp of the last *recorded* event, not the final
    whistle itself. It may undercount the true match duration by 15–60 seconds
    in matches where the last StatsBomb event precedes the whistle.
    """
    valid = [e for e in events if e.get("period", 1) <= 4]
    if not valid:
        return 90.0
    return max(e["minute"] + e["second"] / 60 for e in valid)


def compute_player_minutes(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None = None,
) -> float:
    """
    Sum of all playing stints for a player in one match, in decimal minutes.

    - Stints with to=None ran to the actual final whistle → use match_duration,
      or dismissal_minute if provided (red card / second yellow override).
    - An empty positions list means the player never took the field → 0 minutes.
    - Negative stint durations are clamped to 0 (data-quality guard).

    Args:
        positions:        player's positions array from the StatsBomb lineup.
        match_duration:   output of compute_match_duration() for this match.
        dismissal_minute: decimal-minute of a red card / second yellow for this
                          player, if one occurred. Used as the cap for any
                          open-ended (to=None) stint instead of match_duration.
    """
    if not positions:
        return 0.0
    effective_end = dismissal_minute if dismissal_minute is not None else match_duration
    total = 0.0
    for stint in positions:
        start = parse_mm_ss(stint["from"])
        end = parse_mm_ss(stint["to"]) if stint.get("to") else effective_end
        total += max(0.0, end - start)
    return total
