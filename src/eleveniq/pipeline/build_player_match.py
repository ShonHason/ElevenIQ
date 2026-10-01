"""
build_player_match.py — aggregate StatsBomb events into one row per player per match.

Design notes
------------
- compute_minutes_per_group is the ONE shared function for both minutes_played and
  position group assignment.  minutes_played = sum(group_minutes.values()).
  compute_player_minutes is NOT called separately; the two can never diverge.
- outcome is passed explicitly to is_progressive_pass (required keyword arg).
- Raw counts (numerators) and minutes (denominator) are preserved alongside each
  other in the output, per ADR 002 §6b.  Per-90 values are not computed here.
- Players with minutes_played == 0 (unused substitutes) are excluded from output.
- One quality report is collected per season run and returned separately: the
  dismissal checks, the starter checks and the substitution/minutes checks. It
  also carries the fail-loud data checks (plan 004 H1): UNKNOWN-position
  minutes, unknown shot/tackle outcomes and duel types, Pass/Carry events with
  missing locations, shots missing xG, and dropped events. Any of these fails the
  build (DataQualityError) except the allow-listed bench-card drop
  (ALLOWED_DROPPED_EVENT_TYPES).
- Penalty-shootout events (period 5, metrics.minutes.is_playing_time) are
  removed before any metric is counted.
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import polars as pl

from eleveniq.metrics.minutes import compute_match_duration, is_playing_time, parse_mm_ss
from eleveniq.metrics.positions import (
    POSITION_GROUP_COLUMNS,
    UNKNOWN_POSITION_GROUP,
    assign_position_group,
    compute_minutes_per_group,
    compute_minutes_per_raw_position,
    primary_position_group,
)
from eleveniq.metrics.aerials import has_aerial_won, is_aerial_lost
from eleveniq.metrics.entries import (
    SET_PIECE_TYPES,
    is_final_third_entry_pass,
    is_final_third_entry_carry,
    is_box_entry_pass,
    is_box_entry_carry,
)
from eleveniq.metrics.progressive import is_progressive_pass, is_progressive_carry
from eleveniq.metrics.shots import KNOWN_SHOT_OUTCOMES, is_shot_on_target
from eleveniq.metrics.tackles import KNOWN_DUEL_TYPES, KNOWN_TACKLE_OUTCOMES, is_tackle_won
from eleveniq.metrics.xg_assisted import compute_xg_assisted_per_player
from eleveniq.config import (
    COMPETITION_SEASONS,
    LONG_PASS_THRESHOLD_UNITS,
    SOURCE_STATSBOMB,
    XG_ASSISTED_MAX_CARRY_UNITS,
    CompetitionSeason,
    select_competition_seasons,
)
from eleveniq.pipeline.quality_report import (
    DISMISSAL_MISSING_TIMESTAMP,
    DISMISSAL_TOLERANCE_MINUTES,
    check_dismissal_event_quality,
    find_dismissal,
    find_substitutions_off,
)
from eleveniq.statsbomb.loader import load_events, load_lineups, load_matches

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INTERIM_DIR = _PROJECT_ROOT / "data" / "interim"
# Combined output (input to build_player_season) and per-competition-season parts.
OUTPUT_PATH = _INTERIM_DIR / "player_match_stats.parquet"
PART_DIR = _INTERIM_DIR / "player_match"

# Quality-report issue names. Every "issue" value is one of these constants (or
# quality_report.DISMISSAL_MISSING_TIMESTAMP), never a literal, so a typo cannot
# silently disable a fail-loud check: it becomes a NameError instead.
STARTING_XI_MISSING = "starting_xi_missing"
STARTING_XI_MISMATCH = "starting_xi_mismatch"
STARTING_XI_DUPLICATE = "starting_xi_duplicate"
STARTED_INFERRED_FALLBACK = "started_inferred_fallback"
STARTER_COUNT_MISMATCH = "starter_count_mismatch"
UNKNOWN_SHOT_OUTCOME = "unknown_shot_outcome"
UNKNOWN_TACKLE_OUTCOME = "unknown_tackle_outcome"
UNKNOWN_DUEL_TYPE = "unknown_duel_type"
PASS_MISSING_LOCATION = "pass_missing_location"
CARRY_MISSING_LOCATION = "carry_missing_location"
MINUTES_UNKNOWN = "minutes_unknown"
DROPPED_EVENT = "dropped_event"
DROPPED_EVENT_ALLOWED = "dropped_event_allowed"
DISMISSAL_AFTER_MATCH_END = "dismissal_after_match_end"

# Quality-report issues that fail the build (starter cross-check).
STARTING_XI_ISSUES = frozenset(
    {STARTING_XI_MISSING, STARTING_XI_MISMATCH, STARTING_XI_DUPLICATE}
)


class StarterCrossCheckError(RuntimeError):
    """Derived starters disagree with the Starting XI event for some team-match."""


class MatchProcessingError(RuntimeError):
    """One or more matches of a competition-season raised during processing."""


class DataQualityError(RuntimeError):
    """A fail-loud data check found a value the pipeline would otherwise count silently."""


# Event-level checks recorded by _aggregate_events. Each key is an issue name in
# the quality report; the build fails when any of them occurs.
EVENT_QUALITY_ISSUES = frozenset({
    UNKNOWN_SHOT_OUTCOME,
    UNKNOWN_TACKLE_OUTCOME,
    UNKNOWN_DUEL_TYPE,
    PASS_MISSING_LOCATION,
    CARRY_MISSING_LOCATION,
})

# Pass outcome StatsBomb records when the result is unknown. Such passes stay in
# the attempted counts but leave the completion denominators (ADR 002 §14, §15, §19).
UNKNOWN_PASS_OUTCOME = "Unknown"

# Dropped events (a player.id with no output row) that do not fail the build:
# cards shown to a bench player who never entered the pitch (empty positions
# array). ADR 002 §3.
ALLOWED_DROPPED_EVENT_TYPES = frozenset({"Bad Behaviour"})
DROPPED_NOT_IN_LINEUP = "not_in_lineup"
DROPPED_ZERO_MINUTES = "zero_minutes"

# Substitution-off checks (ADR 002 §3). The player's own Substitution event caps
# his stints; these quality issues guard that rule.
MULTIPLE_SUBSTITUTIONS_OFF = "multiple_substitutions_off"
STINT_AFTER_SUBSTITUTION = "stint_after_substitution"
TEAM_MINUTES_EXCEED_CEILING = "team_minutes_exceed_ceiling"
PLAYER_MINUTES_EXCEED_DURATION = "player_minutes_exceed_duration"
# Informational (do not fail the build): a stint shortened by the substitution
# cap, a stint whose from_period is later than its to_period, and a team with
# more players on the pitch than allowed (11 minus its dismissals so far) at
# some instant.
SUBSTITUTION_CAPPED = "substitution_capped"
REVERSED_PERIOD_STINT = "reversed_period_stint"
ON_PITCH_COUNT_EXCEEDED = "on_pitch_count_exceeded"

# Per team-match, summed minutes may not exceed 11 × match_duration by more
# than float noise (ADR 002 §3: measured maximum after the cap is 0.0).
TEAM_MINUTES_TOLERANCE: float = 1e-6
# Per player-match, minutes may not exceed match_duration by more than this.
PLAYER_MINUTES_TOLERANCE: float = 1e-6
PLAYERS_ON_PITCH: int = 11

# Quality-report issues that fail the build (DataQualityError).
FAILING_QUALITY_ISSUES = EVENT_QUALITY_ISSUES | {
    MINUTES_UNKNOWN,
    DROPPED_EVENT,
    DISMISSAL_AFTER_MATCH_END,
    MULTIPLE_SUBSTITUTIONS_OFF,
    STINT_AFTER_SUBSTITUTION,
    TEAM_MINUTES_EXCEED_CEILING,
    PLAYER_MINUTES_EXCEED_DURATION,
}

_SCHEMA: dict[str, type] = {
    "match_id": pl.Int64,
    "player_id": pl.Int64,
    "player_name": pl.Utf8,
    "team_id": pl.Int64,
    "team_name": pl.Utf8,
    "competition_id": pl.Int64,
    "season_id": pl.Int64,
    "source": pl.Utf8,
    "minutes_played": pl.Float64,
    "match_duration": pl.Float64,
    "position_raw": pl.Utf8,
    "position_group": pl.Utf8,
    "goals": pl.Int32,
    "shots": pl.Int32,
    "shots_on_target": pl.Int32,
    "xg": pl.Float64,
    "shots_missing_xg": pl.Int32,
    "passes_attempted": pl.Int32,
    "passes_completed": pl.Int32,
    "passes_unknown_outcome": pl.Int32,
    "progressive_passes": pl.Int32,
    "key_passes": pl.Int32,
    "carries": pl.Int32,
    "progressive_carries": pl.Int32,
    "tackles_attempted": pl.Int32,
    "tackles_won": pl.Int32,
    "dribbles_attempted": pl.Int32,
    "dribbles_completed": pl.Int32,
    "interceptions": pl.Int32,
    "clearances": pl.Int32,
    "blocks": pl.Int32,
    "fouls_committed": pl.Int32,
    "fouls_won": pl.Int32,
    "assists": pl.Int32,
    "started": pl.Boolean,
    "started_inferred": pl.Boolean,
    "minutes_gk": pl.Float64,
    "minutes_cb": pl.Float64,
    "minutes_fb_wb": pl.Float64,
    "minutes_dm_cm": pl.Float64,
    "minutes_am_winger": pl.Float64,
    "minutes_st": pl.Float64,
    "minutes_unknown": pl.Float64,
    "crosses_attempted": pl.Int32,
    "crosses_completed": pl.Int32,
    "crosses_unknown_outcome": pl.Int32,
    "dispossessed": pl.Int32,
    "miscontrols": pl.Int32,
    "pressures": pl.Int32,
    "ball_recoveries": pl.Int32,
    "ball_recoveries_failed": pl.Int32,
    "dribbled_past": pl.Int32,
    "aerials_won": pl.Int32,
    "aerials_lost": pl.Int32,
    "long_passes_attempted": pl.Int32,
    "long_passes_completed": pl.Int32,
    "long_passes_unknown_outcome": pl.Int32,
    "passes_missing_length": pl.Int32,
    "switches": pl.Int32,
    "xg_assisted": pl.Float64,
    "xg_assisted_direct": pl.Float64,
    "final_third_entries_pass": pl.Int32,
    "final_third_entries_carry": pl.Int32,
    "box_entries_pass": pl.Int32,
    "box_entries_carry": pl.Int32,
}


def _group_minutes_to_columns(group_minutes: dict[str, float]) -> dict[str, float]:
    """
    Translate a {group: minutes} dict into {column_name: minutes} using
    POSITION_GROUP_COLUMNS.  Raises ValueError for any group key not in the
    canonical map so that a new group in positions.py is never silently dropped.
    """
    unknown = set(group_minutes) - set(POSITION_GROUP_COLUMNS)
    if unknown:
        raise ValueError(
            f"Unknown position group(s) from compute_minutes_per_group: {sorted(unknown)}"
        )
    return {col: group_minutes.get(group, 0.0) for group, col in POSITION_GROUP_COLUMNS.items()}


def _primary_raw_position(
    minutes_per_raw: dict[str, float],
) -> str:
    """Return the StatsBomb position name with the most playing time."""
    if not minutes_per_raw:
        return ""
    return min(minutes_per_raw, key=lambda n: (-minutes_per_raw[n], n))


def _aggregate_events(
    player_events: list[dict],
    issues: Counter[tuple[str, str | None]],
) -> dict:
    """
    Count raw metrics from one player's events in a single match.

    `issues` is incremented with (issue, offending value) for every
    EVENT_QUALITY_ISSUES occurrence: a shot outcome, tackle outcome or duel
    type outside the known lists, or a Pass/Carry missing location or
    end_location. It is required so that no caller can skip these checks.
    """
    goals = 0
    shots = 0
    shots_on_target = 0
    xg = 0.0
    shots_missing_xg = 0
    passes_attempted = 0
    passes_completed = 0
    passes_unknown_outcome = 0
    progressive_passes = 0
    key_passes = 0
    assists = 0
    crosses_attempted = 0
    crosses_completed = 0
    crosses_unknown_outcome = 0
    carries = 0
    progressive_carries = 0
    tackles_attempted = 0
    tackles_won_count = 0
    dribbles_attempted = 0
    dribbles_completed = 0
    interceptions = 0
    clearances = 0
    blocks = 0
    fouls_committed = 0
    fouls_won = 0
    dispossessed = 0
    miscontrols = 0
    pressures = 0
    ball_recoveries = 0
    ball_recoveries_failed = 0
    dribbled_past = 0
    aerials_won = 0
    aerials_lost = 0
    long_passes_attempted = 0
    long_passes_completed = 0
    long_passes_unknown_outcome = 0
    passes_missing_length = 0
    switches = 0
    final_third_entries_pass = 0
    final_third_entries_carry = 0
    box_entries_pass = 0
    box_entries_carry = 0

    for event in player_events:
        event_type = event.get("type", {}).get("name", "")

        if has_aerial_won(event):
            aerials_won += 1

        if event_type == "Pass":
            passes_attempted += 1
            pass_data = event.get("pass") or {}
            # Completed pass = no outcome field (None); any outcome string = incomplete.
            # Using (... or {}) guards against explicit JSON null on the outcome key.
            outcome_name: str | None = (pass_data.get("outcome") or {}).get("name")
            if outcome_name is None:
                passes_completed += 1
            elif outcome_name == UNKNOWN_PASS_OUTCOME:
                passes_unknown_outcome += 1

            loc = event.get("location")
            end_loc = pass_data.get("end_location")
            if not (loc and end_loc):
                issues[(PASS_MISSING_LOCATION, None)] += 1
            if loc and end_loc:
                if is_progressive_pass(
                    (loc[0], loc[1]),
                    (end_loc[0], end_loc[1]),
                    outcome=outcome_name,
                ):
                    progressive_passes += 1

            if pass_data.get("shot_assist") or pass_data.get("goal_assist"):
                key_passes += 1
            if pass_data.get("goal_assist"):
                assists += 1

            if pass_data.get("cross"):
                crosses_attempted += 1
                if outcome_name is None:
                    crosses_completed += 1
                elif outcome_name == UNKNOWN_PASS_OUTCOME:
                    crosses_unknown_outcome += 1

            pass_type_name: str | None = (pass_data.get("type") or {}).get("name")
            pass_length = pass_data.get("length")
            if pass_length is None:
                passes_missing_length += 1
            else:
                if pass_length >= LONG_PASS_THRESHOLD_UNITS and pass_type_name not in SET_PIECE_TYPES:
                    long_passes_attempted += 1
                    if outcome_name is None:
                        long_passes_completed += 1
                    elif outcome_name == UNKNOWN_PASS_OUTCOME:
                        long_passes_unknown_outcome += 1

            if outcome_name is None and loc and end_loc:
                start = (loc[0], loc[1])
                end = (end_loc[0], end_loc[1])
                if is_final_third_entry_pass(start, end, outcome=outcome_name, pass_type_name=pass_type_name):
                    final_third_entries_pass += 1
                if is_box_entry_pass(start, end, outcome=outcome_name, pass_type_name=pass_type_name):
                    box_entries_pass += 1

            if pass_data.get("switch"):
                switches += 1

        elif event_type == "Shot":
            shots += 1
            shot_data = event.get("shot") or {}
            shot_outcome = (shot_data.get("outcome") or {}).get("name")
            if shot_outcome not in KNOWN_SHOT_OUTCOMES:
                issues[(UNKNOWN_SHOT_OUTCOME, shot_outcome)] += 1
            if is_shot_on_target(shot_outcome):
                shots_on_target += 1
            if shot_outcome == "Goal":
                goals += 1
            xg_value = shot_data.get("statsbomb_xg")
            if xg_value is None:
                shots_missing_xg += 1
                player_id_ctx = (event.get("player") or {}).get("id")
                logger.warning("shot missing statsbomb_xg (player_id=%s)", player_id_ctx)
            else:
                xg += xg_value

        elif event_type == "Carry":
            carries += 1
            carry_data = event.get("carry") or {}
            loc = event.get("location")
            end_loc = carry_data.get("end_location")
            if not (loc and end_loc):
                issues[(CARRY_MISSING_LOCATION, None)] += 1
            if loc and end_loc:
                start = (loc[0], loc[1])
                end = (end_loc[0], end_loc[1])
                if is_progressive_carry(start, end):
                    progressive_carries += 1
                if is_final_third_entry_carry(start, end):
                    final_third_entries_carry += 1
                if is_box_entry_carry(start, end):
                    box_entries_carry += 1

        elif event_type == "Duel":
            duel_data = event.get("duel") or {}
            duel_type_name = (duel_data.get("type") or {}).get("name")
            if duel_type_name not in KNOWN_DUEL_TYPES:
                issues[(UNKNOWN_DUEL_TYPE, duel_type_name)] += 1
            if duel_type_name == "Tackle":
                tackles_attempted += 1
                duel_outcome = (duel_data.get("outcome") or {}).get("name")
                if duel_outcome not in KNOWN_TACKLE_OUTCOMES:
                    issues[(UNKNOWN_TACKLE_OUTCOME, duel_outcome)] += 1
                if is_tackle_won(duel_outcome):
                    tackles_won_count += 1
            elif is_aerial_lost(event):
                aerials_lost += 1

        elif event_type == "Dispossessed":
            dispossessed += 1

        elif event_type == "Miscontrol":
            miscontrols += 1

        elif event_type == "Pressure":
            pressures += 1

        elif event_type == "Dribbled Past":
            dribbled_past += 1

        elif event_type == "Ball Recovery":
            br = event.get("ball_recovery") or {}
            if br.get("recovery_failure"):
                ball_recoveries_failed += 1
            else:
                ball_recoveries += 1

        elif event_type == "Dribble":
            dribbles_attempted += 1
            dribble_data = event.get("dribble") or {}
            if (dribble_data.get("outcome") or {}).get("name") == "Complete":
                dribbles_completed += 1

        elif event_type == "Interception":
            interceptions += 1

        elif event_type == "Clearance":
            clearances += 1

        elif event_type == "Block":
            block_data = event.get("block") or {}
            if not block_data.get("offensive") and not block_data.get("save_block"):
                blocks += 1

        elif event_type == "Foul Committed":
            fouls_committed += 1

        elif event_type == "Foul Won":
            fouls_won += 1

    return {
        "goals": goals,
        "shots": shots,
        "shots_on_target": shots_on_target,
        "xg": xg,
        "shots_missing_xg": shots_missing_xg,
        "passes_attempted": passes_attempted,
        "passes_completed": passes_completed,
        "passes_unknown_outcome": passes_unknown_outcome,
        "progressive_passes": progressive_passes,
        "key_passes": key_passes,
        "carries": carries,
        "progressive_carries": progressive_carries,
        "tackles_attempted": tackles_attempted,
        "tackles_won": tackles_won_count,
        "dribbles_attempted": dribbles_attempted,
        "dribbles_completed": dribbles_completed,
        "interceptions": interceptions,
        "clearances": clearances,
        "blocks": blocks,
        "fouls_committed": fouls_committed,
        "fouls_won": fouls_won,
        "assists": assists,
        "crosses_attempted": crosses_attempted,
        "crosses_completed": crosses_completed,
        "crosses_unknown_outcome": crosses_unknown_outcome,
        "dispossessed": dispossessed,
        "miscontrols": miscontrols,
        "pressures": pressures,
        "ball_recoveries": ball_recoveries,
        "ball_recoveries_failed": ball_recoveries_failed,
        "dribbled_past": dribbled_past,
        "aerials_won": aerials_won,
        "aerials_lost": aerials_lost,
        "long_passes_attempted": long_passes_attempted,
        "long_passes_completed": long_passes_completed,
        "long_passes_unknown_outcome": long_passes_unknown_outcome,
        "passes_missing_length": passes_missing_length,
        "switches": switches,
        "final_third_entries_pass": final_third_entries_pass,
        "final_third_entries_carry": final_third_entries_carry,
        "box_entries_pass": box_entries_pass,
        "box_entries_carry": box_entries_carry,
    }


def _is_kickoff_spell(spell: dict) -> bool:
    """True for a period-1 spell that starts at 00:00 and has non-zero length."""
    return (
        spell["from"] == "00:00"
        and spell["from_period"] == 1
        and spell.get("to") != "00:00"
    )


def _starting_xi_by_team(events: list[dict]) -> dict[int, list[set[int]]]:
    """Map team_id -> one player-id set per Starting XI event for that team.

    A team normally has exactly one; the caller flags zero or more than one.
    """
    xi: dict[int, list[set[int]]] = {}
    for event in events:
        if event["type"]["name"] != "Starting XI":
            continue
        players = {entry["player"]["id"] for entry in event["tactics"]["lineup"]}
        xi.setdefault(event["team"]["id"], []).append(players)
    return xi


def _process_match(
    match_id: int,
    competition_id: int,
    season_id: int,
    *,
    data_root: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Process one match into player-match rows plus its quality report.

    Returns:
        (rows, quality_report)
        rows: list of player-match dicts ready for a Polars DataFrame.
        quality_report: every quality-report entry for this match (dismissal
            checks, starter checks, substitution/minutes checks, event checks
            and dropped events), each with an "issue" name except the plain
            dismissal records.
    """
    events = load_events(match_id, data_root)
    lineups = load_lineups(match_id, data_root)
    match_duration = compute_match_duration(events)
    # Penalty-shootout events (period 5) are not playing time and count toward
    # no metric. Dismissal lookups use this list too: a shootout card clamps
    # nothing, because every stint has ended by then (extra time, periods 3-4,
    # still counts). The Starting XI lookup keeps the full list.
    play_events = [e for e in events if is_playing_time(e)]
    # Period-aware end of the match, for the on-pitch count: (last playing
    # period, match_duration).
    match_end = (max(e["period"] for e in play_events), match_duration)

    # Compute xg_assisted per player (requires full match event list for cross-player lookup)
    xga_by_player = compute_xg_assisted_per_player(
        play_events, max_carry_units=XG_ASSISTED_MAX_CARRY_UNITS
    )

    starting_xi = _starting_xi_by_team(events)

    # Build per-player event index
    player_events: dict[int, list[dict]] = {}
    for event in play_events:
        pid = (event.get("player") or {}).get("id")
        if pid is not None:
            if pid not in player_events:
                player_events[pid] = []
            player_events[pid].append(event)

    rows: list[dict] = []
    quality_report: list[dict] = []
    event_issues: Counter[tuple[str, str | None]] = Counter()

    for team in lineups:
        team_id = team["team_id"]
        team_name = team["team_name"]
        # Collect (row, positions) tuples for the team before the started check
        # so the team-level fallback can inspect positions in a second pass.
        team_entries: list[tuple[dict, list]] = []
        # (player_id, positions, dismissal, substitution off) for the on-pitch count.
        on_pitch_input: list[OnPitchPlayer] = []

        for player in team["lineup"]:
            player_id = player["player_id"]
            player_name = player["player_name"]
            positions = player.get("positions") or []

            dismissal = find_dismissal(play_events, player_id)
            dismissal_minute = None if dismissal is None else dismissal[1]
            if dismissal_minute is not None and dismissal_minute > match_duration:
                # Never capped silently: fails the build (DataQualityError).
                quality_report.append({
                    "issue": DISMISSAL_AFTER_MATCH_END,
                    "match_id": match_id,
                    "player_id": player_id,
                    "dismissal_minute": dismissal_minute,
                    "match_duration": match_duration,
                })

            substitution = _substitution_off(
                match_id, player_id, positions, play_events, quality_report
            )
            substitution_minute = None if substitution is None else substitution[1]
            on_pitch_input.append((player_id, positions, dismissal, substitution))

            # ONE shared call: compute_minutes_per_group drives both minutes_played
            # and position group.  compute_player_minutes is NOT called separately.
            group_minutes = compute_minutes_per_group(
                positions,
                match_duration,
                dismissal_minute=dismissal_minute,
                substitution_minute=substitution_minute,
            )
            minutes_played = sum(group_minutes.values())
            if minutes_played > match_duration + PLAYER_MINUTES_TOLERANCE:
                quality_report.append({
                    "issue": PLAYER_MINUTES_EXCEED_DURATION,
                    "match_id": match_id,
                    "team_id": team_id,
                    "player_id": player_id,
                    "minutes_played": minutes_played,
                    "match_duration": match_duration,
                })

            if substitution_minute is not None:
                uncapped = sum(compute_minutes_per_group(
                    positions, match_duration,
                    dismissal_minute=dismissal_minute, substitution_minute=None,
                ).values())
                if uncapped > minutes_played:
                    quality_report.append({
                        "issue": SUBSTITUTION_CAPPED,
                        "match_id": match_id,
                        "team_id": team_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        "substitution_minute": substitution_minute,
                        "minutes_removed": uncapped - minutes_played,
                    })
            for stint in positions:
                if stint.get("to_period") is not None and stint["from_period"] > stint["to_period"]:
                    quality_report.append({
                        "issue": REVERSED_PERIOD_STINT,
                        "match_id": match_id,
                        "player_id": player_id,
                        "from": stint["from"],
                        "from_period": stint["from_period"],
                        "to": stint["to"],
                        "to_period": stint["to_period"],
                    })

            if minutes_played == 0.0:
                continue  # unused substitute

            position_group = primary_position_group(group_minutes)
            started = any(p.get("start_reason") == "Starting XI" for p in positions)

            raw_minutes = compute_minutes_per_raw_position(
                positions,
                match_duration,
                dismissal_minute=dismissal_minute,
                substitution_minute=substitution_minute,
            )
            position_raw = _primary_raw_position(raw_minutes)

            # Dismissal quality check: record every dismissed player and whether
            # the raw StatsBomb lineup data would have overcounted their minutes.
            if dismissal_minute is not None:
                raw_group_minutes_unfixed = compute_minutes_per_group(
                    positions, match_duration, dismissal_minute=None, substitution_minute=None
                )
                minutes_unfixed = sum(raw_group_minutes_unfixed.values())
                overcounted = minutes_unfixed > dismissal_minute + DISMISSAL_TOLERANCE_MINUTES
                # cause is only meaningful when overcounting actually occurred.
                # "to_null": at least one open-ended (to=None) stint was extended
                #   to match_duration instead of stopping at the card minute.
                # "explicit_to_after_card": all stints had explicit to-values but
                #   StatsBomb set them past the dismissal minute (known data error).
                if overcounted:
                    has_open_ended = any(not s.get("to") for s in positions)
                    cause: str | None = "to_null" if has_open_ended else "explicit_to_after_card"
                else:
                    cause = None
                quality_report.append({
                    "match_id": match_id,
                    "player_id": player_id,
                    "player_name": player_name,
                    "dismissal_minute": dismissal_minute,
                    "minutes_with_fix": minutes_played,
                    "minutes_without_fix": minutes_unfixed,
                    "minutes_overcounted_without_fix": overcounted,
                    "cause": cause,
                })
            else:
                # find_dismissal returns None either because the player
                # was not dismissed, or because the dismissal event has missing
                # timestamp fields. Surface the second case to the report so it
                # is not silently ignored.
                quality_issue = check_dismissal_event_quality(play_events, player_id)
                if quality_issue is not None:
                    quality_report.append({
                        "match_id": match_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        **quality_issue,
                    })

            unknown_minutes = group_minutes.get(UNKNOWN_POSITION_GROUP, 0.0)
            if unknown_minutes > 0:
                quality_report.append({
                    "issue": MINUTES_UNKNOWN,
                    "match_id": match_id,
                    "team_id": team_id,
                    "player_id": player_id,
                    "minutes_unknown": unknown_minutes,
                    "position_raw": sorted({
                        p["position"] for p in positions
                        if assign_position_group(p["position"]) == UNKNOWN_POSITION_GROUP
                    }),
                })

            xga, xga_direct = xga_by_player.get(player_id, (0.0, 0.0))
            team_entries.append((
                {
                    "match_id": match_id,
                    "player_id": player_id,
                    "player_name": player_name,
                    "team_id": team_id,
                    "team_name": team_name,
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "source": SOURCE_STATSBOMB,
                    "minutes_played": minutes_played,
                    "match_duration": match_duration,
                    "position_raw": position_raw,
                    "position_group": position_group,
                    "started": started,
                    "started_inferred": False,
                    **_group_minutes_to_columns(group_minutes),
                    **_aggregate_events(player_events.get(player_id, []), event_issues),
                    "xg_assisted": xga,
                    "xg_assisted_direct": xga_direct,
                },
                positions,
            ))

        # Team-level started count.  Apply fallback when primary count < 11:
        # a player with any period-1 stint beginning at "00:00" also counts as
        # started (covers StatsBomb data where real starters have a non-"Starting
        # XI" start_reason, e.g. "Tactical Shift" — confirmed in match 3825596).
        # Zero-length 00:00 spells (from == to == "00:00") do not count.
        starter_count = sum(1 for row, _ in team_entries if row["started"])
        if starter_count < 11:
            inferred_players: list[str] = []
            for row, positions in team_entries:
                if not row["started"] and any(
                    _is_kickoff_spell(p) for p in positions
                ):
                    row["started"] = True
                    row["started_inferred"] = True
                    inferred_players.append(row["player_name"])
            if inferred_players:
                quality_report.append({
                    "issue": STARTED_INFERRED_FALLBACK,
                    "match_id": match_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "players": inferred_players,
                })
            final_count = sum(1 for row, _ in team_entries if row["started"])
            if final_count != 11:
                quality_report.append({
                    "issue": STARTER_COUNT_MISMATCH,
                    "match_id": match_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "starter_count_after_fallback": final_count,
                })
        elif starter_count > 11:
            quality_report.append({
                "issue": STARTER_COUNT_MISMATCH,
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
                "starter_count_after_fallback": starter_count,
            })

        # Cross-check: the team must have exactly one Starting XI event and the
        # derived starters must equal its tactics.lineup. build_competition_season
        # fails on any entry recorded here.
        derived = {row["player_id"] for row, _ in team_entries if row["started"]}
        xi_events = starting_xi.get(team_id, [])
        if not xi_events:
            quality_report.append({
                "issue": STARTING_XI_MISSING,
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
            })
        elif len(xi_events) > 1:
            quality_report.append({
                "issue": STARTING_XI_DUPLICATE,
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
                "starting_xi_events": len(xi_events),
            })
        elif derived != (expected := xi_events[0]):
            quality_report.append({
                "issue": STARTING_XI_MISMATCH,
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
                "missing_from_derived": sorted(expected - derived),
                "extra_in_derived": sorted(derived - expected),
            })

        team_minutes = sum(row["minutes_played"] for row, _ in team_entries)
        ceiling = PLAYERS_ON_PITCH * match_duration
        if team_minutes > ceiling + TEAM_MINUTES_TOLERANCE:
            quality_report.append({
                "issue": TEAM_MINUTES_EXCEED_CEILING,
                "match_id": match_id,
                "team_id": team_id,
                "team_minutes": team_minutes,
                "ceiling": ceiling,
            })

        excess = _on_pitch_excess(on_pitch_input, match_end)
        if excess is not None:
            on_pitch, allowed, (period, minute) = excess
            quality_report.append({
                "issue": ON_PITCH_COUNT_EXCEEDED,
                "match_id": match_id,
                "team_id": team_id,
                "on_pitch": on_pitch,
                "allowed": allowed,
                "period": period,
                "minute": minute,
            })

        rows.extend(row for row, _ in team_entries)

    for (issue, value), count in sorted(event_issues.items(), key=str):
        quality_report.append({
            "issue": issue, "match_id": match_id, "value": value, "count": count,
        })
    quality_report.extend(_dropped_events(match_id, player_events, lineups, rows))

    return rows, quality_report


def _substitution_off(
    match_id: int,
    player_id: int,
    positions: list[dict],
    play_events: list[dict],
    report: list[dict],
) -> tuple[int, float] | None:
    """
    (period, match-absolute minute) of the Substitution event that took this
    player off, or None.

    Appends a failing issue to `report` when the player was substituted off
    more than once, or has a stint that starts after his own substitution.
    The order is (period, minute), not the match-absolute minute alone:
    period 2 restarts at 45:00, so first-half stoppage (e.g. 46:01 in period 1)
    precedes a half-time substitution (45:00 in period 2). A stint starting at
    the substitution instant is allowed; the cap gives it 0 minutes. The
    comparison is at whole-second resolution: event timestamps carry whole
    seconds and parse_mm_ss drops a lineup time's fraction, so "37:02.400"
    against a substitution at 37:02 is that instant, not after it.
    `from_period` is required on every stint (KeyError otherwise, never
    defaulted, or the check would silently become period-naive).
    """
    subs = find_substitutions_off(play_events, player_id)
    if not subs:
        return None
    if len(subs) > 1:
        report.append({
            "issue": MULTIPLE_SUBSTITUTIONS_OFF,
            "match_id": match_id,
            "player_id": player_id,
            "substitutions": subs,
        })
    sub_period, sub_minute = subs[0]
    for stint in positions:
        if (stint["from_period"], parse_mm_ss(stint["from"])) > (sub_period, sub_minute):
            report.append({
                "issue": STINT_AFTER_SUBSTITUTION,
                "match_id": match_id,
                "player_id": player_id,
                "stint_from": stint["from"],
                "stint_from_period": stint["from_period"],
                "substitution_period": sub_period,
                "substitution_minute": sub_minute,
            })
    return subs[0]


# (player_id, positions, dismissal, substitution off), each as (period, minute) or None.
OnPitchPlayer = tuple[int, list[dict], tuple[int, float] | None, tuple[int, float] | None]

# Order of changes at one instant (only for a deterministic sort: the count is
# taken after every change at the instant, and whether a card lowers the
# allowed number is decided before the sweep).
_CARD, _EXIT, _ENTRY = 0, 1, 2


def _stint_end(stint: dict, match_end: tuple[int, float]) -> tuple[int, float]:
    """(to_period, to) of a stint, or match_end when it is open (no `to`).

    Raises ValueError for a stint with a `to` but no `to_period`: without the
    period the end cannot be ordered against period-aware instants.
    """
    if not stint.get("to"):
        return match_end
    if stint.get("to_period") is None:
        raise ValueError(
            f"stint {stint.get('from')!r}-{stint['to']!r} has a 'to' but no "
            "'to_period'; the on-pitch count needs both"
        )
    return (stint["to_period"], parse_mm_ss(stint["to"]))


def _card_lowers_allowed(
    positions: list[dict],
    dismissal: tuple[int, float],
    substitution: tuple[int, float] | None,
    match_end: tuple[int, float],
) -> bool:
    """
    Whether a dismissal leaves the team a player short (ADR 002 §3).

    True when the player has taken the field by the card (a non-empty stint
    starting at or before it) and was not substituted off before or at it.
    A player off for treatment ("Player Off" / "Player On") who is sent off
    in that window counts: he is dismissed and cannot be replaced. A card on
    the bench, before entering or after his own substitution, does not count.
    A substitution at the card's instant counts as first: a dismissed player
    cannot be substituted, so the Substitution event proves it came first.
    """
    if substitution is not None and substitution <= dismissal:
        return False
    for stint in positions:
        start = (stint["from_period"], parse_mm_ss(stint["from"]))
        if start <= dismissal and _stint_end(stint, match_end) > start:
            return True
    return False


def _on_pitch_excess(
    players: list[OnPitchPlayer],
    match_end: tuple[int, float],
) -> tuple[int, int, tuple[int, float]] | None:
    """
    (on_pitch, allowed, (period, minute)) at the first instant where the
    team has the most players above the allowed number, or None when it
    never exceeds it.

    allowed starts at 11 and drops by one at each dismissal that leaves the
    team a player short (_card_lowers_allowed): the player had taken the field
    and was not substituted off before or at his card, so a player off for
    treatment counts but a card on the bench does not.

    `players` holds (player_id, positions, dismissal, substitution): the
    player's red card or second yellow and his own substitution off, each as
    (period, minute) or None. His exit is the earlier of the two.
    Instants are ordered by (period, minute), not by the match-absolute minute,
    so first-half stoppage (46:01 in period 1) precedes the second-half kick-off
    (45:00 in period 2). Note this differs from minutes.stint_end_cap, which
    picks the earlier exit by match-absolute minute; the two disagree only when
    one exit is in first-half stoppage and the other in period 2 (0 cases in
    the 2015/16 data; ADR 002 §3). A stint ends at its (to_period, to), at
    match_end when open, and never after the player's exit, so a dismissed
    player stops counting at his card. A stint whose end does not follow its
    start (e.g. a reversed-period stint) contributes nothing. A player counts
    once even when his own stints overlap. The count is taken after every
    change at an instant, so a substitution or a dismissal is never a moment
    with one player too many.

    Raises ValueError for a stint with a `to` but no `to_period`.
    """
    changes: list[tuple[tuple[int, float], int, int]] = []
    for player_id, positions, dismissal, substitution in players:
        exits = [e for e in (dismissal, substitution) if e is not None]
        exit_at = min(exits) if exits else None
        if dismissal is not None and _card_lowers_allowed(
            positions, dismissal, substitution, match_end
        ):
            changes.append((dismissal, _CARD, player_id))
        for stint in positions:
            start = (stint["from_period"], parse_mm_ss(stint["from"]))
            end = _stint_end(stint, match_end)
            if exit_at is not None:
                end = min(end, exit_at)
            if end > start:
                changes.append((start, _ENTRY, player_id))
                changes.append((end, _EXIT, player_id))
    changes.sort(key=lambda c: (c[0], c[1]))
    active: Counter[int] = Counter()
    on_pitch = 0
    allowed = PLAYERS_ON_PITCH
    worst: tuple[int, int, tuple[int, float]] | None = None
    for i, (at, kind, player_id) in enumerate(changes):
        if kind == _CARD:
            allowed -= 1
        else:
            was_on = active[player_id] > 0
            active[player_id] += 1 if kind == _ENTRY else -1
            on_pitch += (active[player_id] > 0) - was_on
        last_at_instant = i + 1 == len(changes) or changes[i + 1][0] != at
        if last_at_instant and on_pitch > allowed and (
            worst is None or on_pitch - allowed > worst[0] - worst[1]
        ):
            worst = (on_pitch, allowed, at)
    return worst


def _dropped_events(
    match_id: int,
    player_events: dict[int, list[dict]],
    lineups: list[dict],
    rows: list[dict],
) -> list[dict]:
    """
    One quality-report entry per event whose player has no output row.

    reason is DROPPED_NOT_IN_LINEUP or DROPPED_ZERO_MINUTES. The entry's issue is
    DROPPED_EVENT_ALLOWED only for a card event (ALLOWED_DROPPED_EVENT_TYPES)
    by a lineup player with an empty positions array (a bench player who never
    came on); every other drop is DROPPED_EVENT, which fails the build. A
    player with stints whose minutes compute to 0 is not allow-listed: that
    would be a minutes bug, not a bench card.
    """
    output_ids = {row["player_id"] for row in rows}
    lineup_positions = {
        p["player_id"]: p.get("positions") or []
        for team in lineups for p in team["lineup"]
    }
    entries: list[dict] = []
    for pid, pevents in player_events.items():
        if pid in output_ids:
            continue
        in_lineup = pid in lineup_positions
        bench = in_lineup and not lineup_positions[pid]
        for event in pevents:
            event_type = event["type"]["name"]
            card = ((event.get("bad_behaviour") or {}).get("card") or {}).get("name")
            allowed = bench and event_type in ALLOWED_DROPPED_EVENT_TYPES and card is not None
            entries.append({
                "issue": DROPPED_EVENT_ALLOWED if allowed else DROPPED_EVENT,
                "match_id": match_id,
                "player_id": pid,
                "event_type": event_type,
                "reason": DROPPED_ZERO_MINUTES if in_lineup else DROPPED_NOT_IN_LINEUP,
                "card": card,
            })
    return entries


def build_competition_season(
    competition_id: int,
    season_id: int,
    *,
    data_root: Path | None = None,
    output_path: Path | None = None,
) -> tuple[pl.DataFrame, list[dict]]:
    """
    Process all matches for one competition-season.

    Writes one parquet (default: PART_DIR/{competition_id}_{season_id}.parquet)
    and returns (DataFrame, quality_report). Every match is attempted and each
    failure is logged, but a part is only written when all matches succeed, so
    a part never silently lacks matches.

    Raises:
        MatchProcessingError: if any match raised; names the failed match ids.
        StarterCrossCheckError: if, for any team-match, the derived starters
            differ from the Starting XI event (or the event is missing or
            duplicated).
        DataQualityError: if any FAILING_QUALITY_ISSUES entry is in the report
            or any shot lacks statsbomb_xg.
        In every case the parquet is not written.
    """
    out_path = output_path or part_path(competition_id, season_id)
    matches = load_matches(competition_id, season_id, data_root)
    logger.info(
        "Building competition=%d season=%d  matches=%d",
        competition_id,
        season_id,
        len(matches),
    )

    all_rows: list[dict] = []
    quality_report: list[dict] = []
    failed_ids: list[int] = []

    for match in matches:
        match_id = match["match_id"]
        try:
            rows, match_report = _process_match(
                match_id, competition_id, season_id, data_root=data_root
            )
            all_rows.extend(rows)
            quality_report.extend(match_report)
        except Exception:  # noqa: BLE001
            logger.error("match_id=%d failed", match_id, exc_info=True)
            failed_ids.append(match_id)

    if failed_ids:
        raise MatchProcessingError(
            f"competition={competition_id} season={season_id}: "
            f"{len(failed_ids)} match(es) failed: {failed_ids}"
        )

    xi_issues = [
        d for d in quality_report if d.get("issue") in STARTING_XI_ISSUES
    ]
    if xi_issues:
        raise StarterCrossCheckError(
            f"competition={competition_id} season={season_id}: "
            f"{len(xi_issues)} team-match(es) where derived starters differ from "
            f"the Starting XI event. First: {xi_issues[0]}"
        )

    df = pl.DataFrame(all_rows, schema=_SCHEMA)

    quality_failures = [
        d for d in quality_report if d.get("issue") in FAILING_QUALITY_ISSUES
    ]
    missing_xg = df.filter(pl.col("shots_missing_xg") > 0)
    if quality_failures or missing_xg.height:
        counts = Counter(d["issue"] for d in quality_failures)
        if missing_xg.height:
            counts["shots_missing_xg"] = int(missing_xg["shots_missing_xg"].sum())
        raise DataQualityError(
            f"competition={competition_id} season={season_id}: "
            f"data-quality checks failed: {dict(sorted(counts.items()))}. "
            f"First entries: {quality_failures[:5]}; "
            f"shots missing xG in match_ids: {sorted(set(missing_xg['match_id'].to_list()))[:10]}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    logger.info("Wrote %d rows → %s", len(df), out_path)
    return df, quality_report


def part_path(competition_id: int, season_id: int, part_dir: Path | None = None) -> Path:
    """Per-competition-season parquet path: {part_dir}/{comp}_{season}.parquet."""
    return (part_dir or PART_DIR) / f"{competition_id}_{season_id}.parquet"


def summarise_quality_report(report: list[dict]) -> dict[str, int]:
    """Counts of each quality-report category for logging."""
    return {
        "dismissals": sum(1 for d in report if d.get("issue") is None),
        "missing_timestamp": sum(
            1 for d in report if d.get("issue") == DISMISSAL_MISSING_TIMESTAMP
        ),
        "fallback_team_matches": sum(
            1 for d in report if d.get("issue") == STARTED_INFERRED_FALLBACK
        ),
        "fallback_players": sum(
            len(d["players"]) for d in report
            if d.get("issue") == STARTED_INFERRED_FALLBACK
        ),
        "starter_count_mismatches": sum(
            1 for d in report if d.get("issue") == STARTER_COUNT_MISMATCH
        ),
        "dropped_events_allowed": sum(
            1 for d in report if d.get("issue") == DROPPED_EVENT_ALLOWED
        ),
        "substitution_capped": sum(
            1 for d in report if d.get("issue") == SUBSTITUTION_CAPPED
        ),
        "reversed_period_stints": sum(
            1 for d in report if d.get("issue") == REVERSED_PERIOD_STINT
        ),
        "on_pitch_count_exceeded": sum(
            1 for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED
        ),
        "player_minutes_exceed_duration": sum(
            1 for d in report if d.get("issue") == PLAYER_MINUTES_EXCEED_DURATION
        ),
    }


def concat_parts(
    competition_seasons: tuple[CompetitionSeason, ...] = COMPETITION_SEASONS,
    *,
    part_dir: Path | None = None,
    output_path: Path | None = None,
) -> pl.DataFrame | None:
    """
    Concatenate the per-competition-season parquets into the combined file read
    by build_player_season. Every configured part must exist, so the combined
    file never silently omits a league: if any is missing, the missing parts are
    logged, the combined file is left untouched and None is returned.
    """
    paths = [
        part_path(cs.competition_id, cs.season_id, part_dir) for cs in competition_seasons
    ]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        logger.warning(
            "Combined file not updated: %d configured part(s) missing: %s",
            len(missing),
            missing,
        )
        return None
    df = pl.concat([pl.read_parquet(p) for p in paths], how="vertical")
    out_path = output_path or OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    logger.info("Concatenated %d part(s), %d rows → %s", len(paths), len(df), out_path)
    return df


def build_all(
    competition_seasons: tuple[CompetitionSeason, ...] = COMPETITION_SEASONS,
    *,
    only: tuple[CompetitionSeason, ...] | None = None,
    force: bool = False,
    concat: bool = True,
    data_root: Path | None = None,
    part_dir: Path | None = None,
    output_path: Path | None = None,
) -> dict[tuple[int, int], dict]:
    """
    Build competition-seasons into their own parts, then concatenate.

    `only` restricts which leagues are built (e.g. one league from the CLI); the
    concat always covers every entry of `competition_seasons`, so a subset build
    never shrinks the combined file (concat_parts leaves it untouched while any
    configured part is missing).

    A part that already exists is skipped unless force=True, so a failed league
    can be rerun alone. With force=True the existing part is deleted before the
    rebuild, so a failed rebuild never leaves a stale part behind. A league that raises (e.g. StarterCrossCheckError,
    MatchProcessingError) is logged and the remaining leagues still run; the
    concat is then refused and a RuntimeError names the failed leagues.

    Returns {(competition_id, season_id): summary dict} for the leagues built
    in this call (skipped leagues have {"skipped": True}).
    """
    if only is not None and not set(only) <= set(competition_seasons):
        raise ValueError(f"only= contains unconfigured competition-season(s): {only}")
    summaries: dict[tuple[int, int], dict] = {}
    failed_leagues: list[str] = []
    for cs in competition_seasons if only is None else only:
        key = (cs.competition_id, cs.season_id)
        path = part_path(cs.competition_id, cs.season_id, part_dir)
        if path.exists() and not force:
            logger.info("Skipping %s: %s exists (use --force to rebuild)", cs.label, path)
            summaries[key] = {"label": cs.label, "skipped": True}
            continue
        path.unlink(missing_ok=True)
        try:
            df, report = build_competition_season(
                cs.competition_id, cs.season_id, data_root=data_root, output_path=path
            )
        except Exception:  # noqa: BLE001
            logger.error("%s failed", cs.label, exc_info=True)
            failed_leagues.append(cs.label)
            continue
        summaries[key] = {
            "label": cs.label,
            "skipped": False,
            "rows": len(df),
            "starts": int(df["started"].sum()),
            **summarise_quality_report(report),
        }

    if failed_leagues:
        raise RuntimeError(f"League(s) failed, concat not run: {failed_leagues}")
    if concat:
        concat_parts(competition_seasons, part_dir=part_dir, output_path=output_path)
    return summaries


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--competition-id", type=int, help="Build only this competition")
    parser.add_argument("--season-id", type=int, help="Build only this season")
    parser.add_argument("--force", action="store_true", help="Rebuild existing parts")
    parser.add_argument(
        "--skip-concat", action="store_true",
        help="Do not rebuild the combined player_match_stats.parquet",
    )
    args = parser.parse_args()
    try:
        only = select_competition_seasons(args.competition_id, args.season_id)
    except ValueError as exc:
        parser.error(str(exc))

    summaries = build_all(
        COMPETITION_SEASONS,
        only=only,
        force=args.force,
        concat=not args.skip_concat,
    )
    for summary in summaries.values():
        if summary["skipped"]:
            print(f"{summary['label']}: skipped (part exists)")
            continue
        print(
            f"{summary['label']}: rows={summary['rows']:,} starts={summary['starts']:,} "
            f"dismissals={summary['dismissals']} "
            f"missing_ts={summary['missing_timestamp']} "
            f"fallback_players={summary['fallback_players']} "
            f"(team-matches={summary['fallback_team_matches']}) "
            f"starter_count_mismatches={summary['starter_count_mismatches']} "
            f"dropped_events_allowed={summary['dropped_events_allowed']} "
            f"substitution_capped={summary['substitution_capped']} "
            f"reversed_period_stints={summary['reversed_period_stints']} "
            f"on_pitch_count_exceeded={summary['on_pitch_count_exceeded']} "
            f"player_minutes_exceed_duration={summary['player_minutes_exceed_duration']}"
        )
