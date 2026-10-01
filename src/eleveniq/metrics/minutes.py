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

from collections.abc import Iterator

# Period 5 is the penalty shootout: not playing time, so it counts toward no
# metric and is excluded from the match duration. Periods 1-4 are playing time.
SHOOTOUT_PERIOD: int = 5


def is_playing_time(event: dict) -> bool:
    """True for an event in periods 1-4; False for the shootout (period 5).

    The one shootout-period rule for the match duration and the event filter
    in build_player_match. `period` is required (KeyError, never defaulted).
    """
    return event["period"] < SHOOTOUT_PERIOD


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
    valid = [e for e in events if is_playing_time(e)]
    if not valid:
        raise ValueError("no events with period <= 4: cannot compute match duration")
    return max(e["minute"] + e["second"] / 60 for e in valid)


def stint_end_cap(
    dismissal_minute: float | None, substitution_minute: float | None
) -> float | None:
    """
    The minute after which a player cannot be on the pitch: the earlier of his
    dismissal and his own substitution off, or None when neither happened.

    "Earlier" is by match-absolute minute. The on-pitch count in
    build_player_match orders exits by (period, minute) instead; the two
    disagree only when one exit is in first-half stoppage (e.g. 46:30, period
    1) and the other in period 2 before 46:30. That does not occur in the
    2015/16 data and is pinned by tests, not aligned (ADR 002 §3).
    """
    caps = [m for m in (dismissal_minute, substitution_minute) if m is not None]
    return min(caps) if caps else None


def iter_stint_bounds(
    positions: list[dict],
    match_duration: float,
    cap: float | None,
) -> Iterator[tuple[dict, float, float]]:
    """
    Yield (stint, start, end) in decimal minutes for every stint, before the
    negative-duration clamp. The one stint-end rule shared by
    compute_player_minutes and the per-group/raw-position functions in
    positions.py, so the two cannot drift:

    - to=None ends at the exit cap (stint_end_cap) or, without one, at
      match_duration (the final whistle).
    - An explicit `to` is clamped at the exit cap, but used as-is otherwise:
      match_duration is the last event timestamp and may lag the real whistle,
      so it is not a clamp. A `to` past match_duration is not silently
      accepted either: if it gives the player more than match_duration minutes,
      the pipeline's player_minutes_exceed_duration check fails the build
      (tolerance 1e-6; the 2015/16 data has 0 such rows). On a new source the
      response is to investigate the stint, not to widen the tolerance
      (ADR 002 §3).
    """
    effective_end = cap if cap is not None else match_duration
    for stint in positions:
        start = parse_mm_ss(stint["from"])
        if stint.get("to"):
            raw_end = parse_mm_ss(stint["to"])
            end = min(raw_end, cap) if cap is not None else raw_end
        else:
            end = effective_end
        yield stint, start, end


def compute_player_minutes(
    positions: list[dict],
    match_duration: float,
    *,
    dismissal_minute: float | None,
    substitution_minute: float | None,
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
    iter_stint_bounds generator; TestMinutesConsistency in
    tests/test_positions.py is the automated guard.

    Args:
        positions:        player's positions array from the StatsBomb lineup.
        match_duration:   output of compute_match_duration() for this match.
        dismissal_minute: decimal-minute of a red card / second yellow for this
                          player, or None.
        substitution_minute: decimal-minute of the Substitution event that took
                          this player off, or None.
        Both are required keywords with no default: a forgotten argument would
        silently give uncapped minutes.
    """
    if not positions:
        return 0.0
    cap = stint_end_cap(dismissal_minute, substitution_minute)
    return sum(
        max(0.0, end - start)
        for _, start, end in iter_stint_bounds(positions, match_duration, cap)
    )
