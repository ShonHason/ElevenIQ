"""
Minutes-played calculation from StatsBomb lineup positions.

Key confirmed facts (from StatsBomb open data schema probe, match 3788741):
- Lineup positions[].from / .to are in "MM:SS" format and are MATCH-ABSOLUTE,
  not period-relative. A substitute who came on at 73 minutes has from="73:10".
- to=None means the player played to the actual final whistle of the match.
- Unused substitutes have an empty positions array.
- event.minute is also match-absolute (same coordinate system).
- Period 5 (penalty shootout) is excluded from match duration.

Exit cap: dismissal and own substitution
----------------------------------------
StatsBomb lineups sometimes keep a stint running after the player left the
pitch: past a red card / second yellow, or past his own Substitution event
(e.g. a "Player On" stint after treatment that runs to "Final Whistle" although
he was replaced at that instant). When a player has a dismissal minute and/or
a substitution-off minute, every stint end (explicit `to` included) is capped
at the earlier of the two (stint_end_cap). Without either, explicit `to` values
are used as-is. ADR 002 §3.

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

    Raises:
        KeyError:   if an event has no "period" key.
        ValueError: if no event has period <= 4 (a match with no playing-time
                    events cannot be given a duration; no 90-minute default).
    """
    valid = [e for e in events if e["period"] <= 4]
    if not valid:
        raise ValueError("no events with period <= 4: cannot compute match duration")
    return max(e["minute"] + e["second"] / 60 for e in valid)


def stint_end_cap(
    dismissal_minute: float | None, substitution_minute: float | None
) -> float | None:
    """
    The minute after which a player cannot be on the pitch: the earlier of his
    dismissal and his own substitution off, or None when neither happened.
    """
    caps = [m for m in (dismissal_minute, substitution_minute) if m is not None]
    return min(caps) if caps else None


def compute_player_minutes(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None = None,
    substitution_minute: float | None = None,
) -> float:
    """
    Sum of all playing stints for a player in one match, in decimal minutes.

    - Stints with to=None ran to the actual final whistle → use match_duration,
      or the exit cap (stint_end_cap) if the player was dismissed or substituted.
    - With an exit cap, every stint end (explicit `to` included) is clamped at it.
    - An empty positions list means the player never took the field → 0 minutes.
    - Negative stint durations are clamped to 0 (data-quality guard).

    Pipeline usage note: the pipeline derives minutes_played as
    sum(compute_minutes_per_group(...).values()), NOT by calling this function
    directly. The two are kept numerically consistent by the shared
    _iter_stint_contributions generator; TestMinutesConsistency in
    tests/test_positions.py is the automated guard.

    Args:
        positions:        player's positions array from the StatsBomb lineup.
        match_duration:   output of compute_match_duration() for this match.
        dismissal_minute: decimal-minute of a red card / second yellow for this
                          player, if one occurred.
        substitution_minute: decimal-minute of the Substitution event that took
                          this player off, if one occurred.
    """
    if not positions:
        return 0.0
    cap = stint_end_cap(dismissal_minute, substitution_minute)
    effective_end = cap if cap is not None else match_duration
    total = 0.0
    for stint in positions:
        start = parse_mm_ss(stint["from"])
        if stint.get("to"):
            raw_end = parse_mm_ss(stint["to"])
            # Mirror the clamping in _iter_stint_contributions: clamp explicit
            # to-values at the exit cap, but leave them as-is otherwise
            # (match_duration may lag the real whistle by a few seconds).
            end = min(raw_end, cap) if cap is not None else raw_end
        else:
            end = effective_end
        total += max(0.0, end - start)
    return total
