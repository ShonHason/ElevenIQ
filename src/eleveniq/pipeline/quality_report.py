"""
Data quality checks for the ElevenIQ pipeline.

Red card / dismissal check
---------------------------
For every red card and second-yellow event in a match, compare the dismissed
player's computed minutes_played against the card minute.  A significant
mismatch (computed >> card minute) indicates that StatsBomb left the player's
lineup positions[].to as null on dismissal, or that the explicit to values
were not corrected after the dismissal, causing compute_player_minutes()
to credit more time on pitch than the player actually played.

StatsBomb records dismissal cards in TWO distinct event types:

  1. ``Bad Behaviour``   — event.bad_behaviour.card.name
  2. ``Foul Committed``  — event.foul_committed.card.name

The two sources have zero overlap (each dismissal appears in exactly one
type).  ``find_dismissal_minute`` checks both sources in a single pass so
whichever fires first (chronologically) is returned.

When a mismatch is detected the quality report lists it and the build-match
stage must re-run with dismissal_minute supplied to compute_player_minutes().

Missing timestamp handling
--------------------------
If a dismissal event is missing the `minute` or `second` field,
find_dismissal_minute() returns None rather than defaulting to 0 (which would
silently create a fake dismissal at the start of the match).  Call
check_dismissal_event_quality() to detect and surface missing-timestamp issues
separately from the mismatch check.

The constants and helpers here are shared between the quality report and the
build-match pipeline (imported by build_player_match.py).
"""
from __future__ import annotations

DISMISSAL_CARDS: frozenset[str] = frozenset({"Red Card", "Second Yellow"})

# A dismissed player's computed minutes may exceed the card minute by this
# tolerance before we flag a mismatch (accounts for sub-minute precision loss).
DISMISSAL_TOLERANCE_MINUTES: float = 1.0


def find_dismissal_minute(events: list[dict], player_id: int) -> float | None:
    """
    Return the match-absolute minute of a red card or second yellow for
    `player_id`, or None if the player was not dismissed or the event has
    missing timestamp fields.

    Checks both ``Bad Behaviour`` events (bad_behaviour.card.name) and
    ``Foul Committed`` events (foul_committed.card.name).  The two sources
    never overlap in practice; the first dismissal found in chronological
    event order is returned.

    Returns None in two distinct cases:
    - No dismissal event exists for this player.
    - A dismissal event exists but lacks ``minute`` or ``second`` fields
      (bad data, not a clean absence).  These two cases are indistinguishable
      from this return value alone.  Call check_dismissal_event_quality() to
      tell them apart: a non-None result means the second case applies and the
      dismissal fix cannot be applied.
    """
    for event in events:
        if (event.get("player") or {}).get("id") != player_id:
            continue
        etype = event.get("type", {}).get("name")
        if etype == "Bad Behaviour":
            card_name = (event.get("bad_behaviour") or {}).get("card", {}).get("name", "")
        elif etype == "Foul Committed":
            card_name = (event.get("foul_committed") or {}).get("card", {}).get("name", "")
        else:
            continue
        if card_name not in DISMISSAL_CARDS:
            continue
        if "minute" not in event or "second" not in event:
            return None
        return event["minute"] + event["second"] / 60
    return None


def check_dismissal_event_quality(events: list[dict], player_id: int) -> dict | None:
    """
    Return a data-quality issue dict if a dismissal event is missing
    minute or second fields, or None if no issue found.

    Checks both ``Bad Behaviour`` and ``Foul Committed`` sources.

    This is the companion to find_dismissal_minute(): when that function
    returns None because of a missing timestamp (not because there was no
    dismissal), this function identifies and describes the problem.

    Return dict keys:
        issue          str   always "dismissal_missing_timestamp"
        player_id      int
        card_name      str
        missing_fields list[str]  subset of ["minute", "second"]
    """
    for event in events:
        if (event.get("player") or {}).get("id") != player_id:
            continue
        etype = event.get("type", {}).get("name")
        if etype == "Bad Behaviour":
            card_name = (event.get("bad_behaviour") or {}).get("card", {}).get("name", "")
        elif etype == "Foul Committed":
            card_name = (event.get("foul_committed") or {}).get("card", {}).get("name", "")
        else:
            continue
        if card_name not in DISMISSAL_CARDS:
            continue
        missing = [k for k in ("minute", "second") if k not in event]
        if missing:
            return {
                "issue": "dismissal_missing_timestamp",
                "player_id": player_id,
                "card_name": card_name,
                "missing_fields": missing,
            }
    return None


def check_dismissed_player_minutes(
    player_id: int,
    player_name: str,
    match_id: int,
    computed_minutes: float,
    events: list[dict],
) -> dict | None:
    """
    Return a mismatch report if a dismissed player's computed minutes
    significantly exceed their card minute, or None if no problem found.

    A non-None return means the pipeline credited too much playing time.
    The pipeline should re-compute their minutes using the returned
    `dismissal_minute` value.

    Returns None (no mismatch reported) if find_dismissal_minute() returns
    None — either no dismissal occurred or the event has missing timestamps
    (use check_dismissal_event_quality() for the latter case).

    Return dict keys:
        match_id          int
        player_id         int
        player_name       str
        computed_minutes  float
        dismissal_minute  float
        excess_minutes    float   (computed - dismissal, always > 0)
    """
    dismissal_minute = find_dismissal_minute(events, player_id)
    if dismissal_minute is None:
        return None
    if computed_minutes > dismissal_minute + DISMISSAL_TOLERANCE_MINUTES:
        return {
            "match_id": match_id,
            "player_id": player_id,
            "player_name": player_name,
            "computed_minutes": computed_minutes,
            "dismissal_minute": dismissal_minute,
            "excess_minutes": computed_minutes - dismissal_minute,
        }
    return None
