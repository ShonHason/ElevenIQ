"""
build_player_match.py — aggregate StatsBomb events into one row per player per match.

Design notes
------------
- One minutes.player_timeline per player is the ONE source for minutes_played, the
  group minutes, position_raw and position_minutes (minutes per StatsBomb position, plan 006
  gate G3).  minutes_played = sum(minutes_per_group(...).values()).
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
- Minutes are period-aware (ADR 002 §31): one PeriodClock per match (each period
  ends at its Half End event), one player_timeline per player (exit cap,
  treatment windows, union of stints, latest-start position), and
  match_duration is the elapsed playing time.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from pathlib import Path

import polars as pl

from eleveniq.metrics.minutes import (
    NOMINAL_PERIOD_END,
    PERIOD_START,
    PLAYER_OFF,
    PLAYER_ON,
    TIME_EPS,
    Instant,
    PeriodClock,
    PlayerTimeline,
    event_instant,
    find_treatment_windows,
    is_playing_time,
    parse_mm_ss,
    player_timeline,
    stint_end,
    stint_end_cap,
)
from eleveniq.metrics.positions import (
    POSITION_GROUP_COLUMNS,
    UNKNOWN_POSITION_GROUP,
    assign_position_group,
    minutes_per_group,
    minutes_per_raw_position,
    primary_position_group,
)
from eleveniq.metrics.aerials import has_aerial_won, is_aerial_lost
from eleveniq.metrics.defensive_actions import DEFENSIVE_ACTIONS, defensive_action, team_column
from eleveniq.metrics.entries import (
    is_final_third_entry_pass,
    is_final_third_entry_carry,
    is_box_entry_pass,
    is_box_entry_carry,
)
from eleveniq.metrics.pass_types import is_restart
from eleveniq.metrics.progressive import is_progressive_pass, is_progressive_carry
from eleveniq.metrics.shots import KNOWN_SHOT_OUTCOMES, is_shot_on_target
from eleveniq.metrics.tackles import KNOWN_DUEL_TYPES, KNOWN_TACKLE_OUTCOMES, is_tackle_won
from eleveniq.metrics.xg_assisted import XGA_FALLBACKS, compute_xg_assisted_per_player
from eleveniq.config import (
    COMPETITION_SEASONS,
    LONG_PASS_THRESHOLD_UNITS,
    MAX_PLAYERS_PER_PASS,
    MIN_PLAYERS_PER_PASS,
    PERIOD_SHORT_FAIL_MINUTES,
    REPORT_ONLY_LIMIT_FLOOR,
    REPORT_ONLY_LIMITS_PER_100_MATCHES,
    SOURCE_STATSBOMB,
    STINT_LATE_START_FAIL_MINUTES,
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
# A deliberate kick out of play for an injured player: never completed, and left out
# of the completion denominators (ADR 004 D13).
INJURY_CLEARANCE_OUTCOME = "Injury Clearance"
# ADR 004 D15: shot.type.name of a penalty, left out of np_shots, np_shots_on_target
# and np_xg.
PENALTY_SHOT_TYPE = "Penalty"

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

# Period-aware minutes (ADR 002 §31). Failing: a period far short of its nominal
# end, a stint bound outside its period (the clock's clamp must not hide it), and
# an event by a player after his unpaired Player Off.
PERIOD_TOO_SHORT = "period_too_short"
STINT_END_AFTER_PERIOD_END = "stint_end_after_period_end"
STINT_TIME_BEFORE_PERIOD_START = "stint_time_before_period_start"
EVENT_AFTER_PERMANENT_OFF = "event_after_permanent_off"
# Report-only (ADR 002 §31).
PERIOD_END_BEFORE_NOMINAL = "period_end_before_nominal"
STINT_AFTER_PERIOD_END = "stint_after_period_end"
DISMISSAL_AFTER_PERIOD_END = "dismissal_after_period_end"
TREATMENT_WINDOW_INSIDE_STINT = "treatment_window_inside_stint"
OVERLAPPING_STINTS = "overlapping_stints"
UNPAIRED_PLAYER_OFF_CAPPED = "unpaired_player_off_capped"
EVENTS_OUTSIDE_CREDITED_TIME = "events_outside_credited_time"

# ADR 004 D12: an open-play pass credited to fewer than MIN_PLAYERS_PER_PASS or more
# than MAX_PLAYERS_PER_PASS players. Fails the build.
PASS_CREDIT_OUT_OF_RANGE = "pass_credit_out_of_range"
# ADR 004 D12: a pass whose event team.id differs from the passer's lineup team.
# Fails the build.
PASS_TEAM_MISMATCH = "pass_team_mismatch"
# ADR 004 §13.8 (M4g4b): an open-play pass credited to fewer than MIN_PLAYERS_PER_PASS or
# more than MAX_PLAYERS_PER_PASS players of the defending team. Fails the build.
OPPONENT_PASS_CREDIT_OUT_OF_RANGE = "opponent_pass_credit_out_of_range"
# The defending team's bounds: the same Laws minimum and maximum as a pass's own team (a
# separate name, so a one-team synthetic test fixture can relax it on its own).
MIN_DEFENDERS_PER_PASS = MIN_PLAYERS_PER_PASS
MAX_DEFENDERS_PER_PASS = MAX_PLAYERS_PER_PASS
# ADR 004 §13.8 (M4g4b): a Defending action credited to fewer than 7 or more than 11 players
# of its team (the actor counted once), or whose event team.id differs from the actor's
# lineup team. Both fail the build.
TEAM_ACTION_CREDIT_OUT_OF_RANGE = "team_action_credit_out_of_range"
TEAM_ACTION_TEAM_MISMATCH = "team_action_team_mismatch"
# ADR 004 D22: a stint starting more than STINT_LATE_START_FAIL_MINUTES after its
# period's Half End. Fails the build.
STINT_LATE_START_EXCEEDS_LIMIT = "stint_late_start_exceeds_limit"
# ADR 004 D22: a report-only issue over its per-competition-season limit.
REPORT_ONLY_LIMIT_EXCEEDED = "report_only_limit_exceeded"

# Report-only period-aware issues, counted by summarise_quality_report.
PERIOD_AWARE_REPORT_ONLY_ISSUES = (
    PERIOD_END_BEFORE_NOMINAL,
    STINT_AFTER_PERIOD_END,
    TREATMENT_WINDOW_INSIDE_STINT,
    OVERLAPPING_STINTS,
    UNPAIRED_PLAYER_OFF_CAPPED,
    DISMISSAL_AFTER_PERIOD_END,
    DISMISSAL_AFTER_MATCH_END,
    EVENTS_OUTSIDE_CREDITED_TIME,
)

# Counted per player-match, not per entry (ADR 004 D22).
_LIMIT_COUNTED_PER_PLAYER_MATCH = frozenset({EVENTS_OUTSIDE_CREDITED_TIME})

# Event types a player may have after his unpaired Player Off (§31.2): a card can
# be shown off the pitch, and an injured player is formally replaced after he
# left (he is the Substitution event's player).
OFF_PITCH_EVENT_TYPES = frozenset({"Bad Behaviour", "Substitution"})
# events_outside_credited_time ignores the types that do not need him on the pitch.
OUTSIDE_TIME_EXEMPT_TYPES = OFF_PITCH_EVENT_TYPES | {PLAYER_OFF, PLAYER_ON}

# Per team, per period, the minutes inside the period may not exceed
# 11 × its length by more than float noise (ADR 002 §31.5).
TEAM_MINUTES_TOLERANCE: float = 1e-6
# Per player-match, minutes may not exceed match_duration by more than this.
PLAYER_MINUTES_TOLERANCE: float = 1e-6
PLAYERS_ON_PITCH: int = 11

# Quality-report issues that fail the build (DataQualityError).
FAILING_QUALITY_ISSUES = EVENT_QUALITY_ISSUES | {
    MINUTES_UNKNOWN,
    DROPPED_EVENT,
    PERIOD_TOO_SHORT,
    STINT_END_AFTER_PERIOD_END,
    STINT_TIME_BEFORE_PERIOD_START,
    EVENT_AFTER_PERMANENT_OFF,
    MULTIPLE_SUBSTITUTIONS_OFF,
    STINT_AFTER_SUBSTITUTION,
    TEAM_MINUTES_EXCEED_CEILING,
    PLAYER_MINUTES_EXCEED_DURATION,
    PASS_CREDIT_OUT_OF_RANGE,
    PASS_TEAM_MISMATCH,
    OPPONENT_PASS_CREDIT_OUT_OF_RANGE,
    TEAM_ACTION_CREDIT_OUT_OF_RANGE,
    TEAM_ACTION_TEAM_MISMATCH,
    STINT_LATE_START_EXCEEDS_LIMIT,
}

_SCHEMA: dict[str, type] = {
    "match_id": pl.Int64,
    "player_id": pl.Int64,
    "player_name": pl.Utf8,
    # The lineup's player_nickname (nullable; plan 006 pre-W1 gate G1): display only, never a
    # key (14 nicknames are shared by several players in 2015/16).
    "player_nickname": pl.Utf8,
    "team_id": pl.Int64,
    "team_name": pl.Utf8,
    "competition_id": pl.Int64,
    "season_id": pl.Int64,
    "source": pl.Utf8,
    # The match file's match_date (plan 006 gate G3): display only (the mover stints' months).
    "match_date": pl.Date,
    "minutes_played": pl.Float64,
    "match_duration": pl.Float64,
    "match_nominal_duration": pl.Float64,
    "position_raw": pl.Utf8,
    "position_group": pl.Utf8,
    # Minutes per StatsBomb position from the same §31 timeline as the group minutes (plan 006
    # gate G3): positions with minutes > 0, sorted by name. Display only (fine roles).
    "position_minutes": pl.List(pl.Struct({"position": pl.Utf8, "minutes": pl.Float64})),
    "goals": pl.Int32,
    "shots": pl.Int32,
    "shots_on_target": pl.Int32,
    "xg": pl.Float64,
    "shots_missing_xg": pl.Int32,
    "np_shots": pl.Int32,
    "np_shots_on_target": pl.Int32,
    "np_xg": pl.Float64,
    "passes_attempted": pl.Int32,
    "open_play_passes_attempted": pl.Int32,
    "team_open_play_passes_while_on": pl.Int32,
    # ADR 004 §13.8 (M4g4b): exposure and team-action counts while on
    "opponent_open_play_passes_while_on": pl.Int32,
    **{team_column(a): pl.Int32 for a in DEFENSIVE_ACTIONS},
    "passes_injury_clearance": pl.Int32,
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
    "long_passes_injury_clearance": pl.Int32,
    "passes_missing_length": pl.Int32,
    "switches": pl.Int32,
    "xg_assisted": pl.Float64,
    "xg_assisted_direct": pl.Float64,
    "op_xg_assisted": pl.Float64,
    "restart_xg_assisted": pl.Float64,
    "op_key_passes": pl.Int32,
    "restart_key_passes": pl.Int32,
    "op_assists": pl.Int32,
    "restart_assists": pl.Int32,
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
            f"Unknown position group(s) from minutes_per_group: {sorted(unknown)}"
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
    np_shots = 0
    np_shots_on_target = 0
    np_xg = 0.0
    passes_attempted = 0
    open_play_passes_attempted = 0
    passes_injury_clearance = 0
    passes_completed = 0
    passes_unknown_outcome = 0
    progressive_passes = 0
    key_passes = 0
    assists = 0
    op_key_passes = 0
    restart_key_passes = 0
    op_assists = 0
    restart_assists = 0
    crosses_attempted = 0
    crosses_completed = 0
    crosses_unknown_outcome = 0
    carries = 0
    progressive_carries = 0
    tackles_won_count = 0
    dribbles_attempted = 0
    dribbles_completed = 0
    fouls_won = 0
    dispossessed = 0
    miscontrols = 0
    ball_recoveries_failed = 0
    aerials_won = 0
    aerials_lost = 0
    long_passes_attempted = 0
    long_passes_completed = 0
    long_passes_unknown_outcome = 0
    long_passes_injury_clearance = 0
    passes_missing_length = 0
    switches = 0
    final_third_entries_pass = 0
    final_third_entries_carry = 0
    box_entries_pass = 0
    box_entries_carry = 0

    # The 8 Defending actions: one shared rule (ADR 004 §13.8; the team counts use it too).
    actions: Counter[str] = Counter()

    for event in player_events:
        event_type = event.get("type", {}).get("name", "")
        action = defensive_action(event)
        if action is not None:
            actions[action] += 1

        if has_aerial_won(event):
            aerials_won += 1

        if event_type == "Pass":
            passes_attempted += 1
            pass_data = event.get("pass") or {}
            # Completed pass = no outcome field (None); any outcome string = incomplete.
            # Using (... or {}) guards against explicit JSON null on the outcome key.
            outcome_name: str | None = (pass_data.get("outcome") or {}).get("name")
            pass_type_name: str | None = (pass_data.get("type") or {}).get("name")
            if outcome_name is None:
                passes_completed += 1
            elif outcome_name == UNKNOWN_PASS_OUTCOME:
                passes_unknown_outcome += 1
            elif outcome_name == INJURY_CLEARANCE_OUTCOME:
                passes_injury_clearance += 1
            # ADR 004 D12: open play = every outcome, restarts excluded.
            restart = is_restart(pass_data)
            if not restart:
                open_play_passes_attempted += 1

            loc = event.get("location")
            end_loc = pass_data.get("end_location")
            if not (loc and end_loc):
                issues[(PASS_MISSING_LOCATION, None)] += 1
            if loc and end_loc:
                if is_progressive_pass(
                    (loc[0], loc[1]),
                    (end_loc[0], end_loc[1]),
                    outcome=outcome_name,
                    pass_type_name=pass_type_name,
                ):
                    progressive_passes += 1

            # ADR 004 §9: split by the key pass's own type, never by play_pattern.
            if pass_data.get("shot_assist") or pass_data.get("goal_assist"):
                key_passes += 1
                if restart:
                    restart_key_passes += 1
                else:
                    op_key_passes += 1
            if pass_data.get("goal_assist"):
                assists += 1
                if restart:
                    restart_assists += 1
                else:
                    op_assists += 1

            if pass_data.get("cross"):
                crosses_attempted += 1
                if outcome_name is None:
                    crosses_completed += 1
                elif outcome_name == UNKNOWN_PASS_OUTCOME:
                    crosses_unknown_outcome += 1

            pass_length = pass_data.get("length")
            if pass_length is None:
                passes_missing_length += 1
            else:
                # One predicate for the long pass and all its parts (ADR 004 D14):
                # restarts never count, so unknown and Injury Clearance long passes
                # are always subsets of long_passes_attempted.
                if pass_length >= LONG_PASS_THRESHOLD_UNITS and not restart:
                    long_passes_attempted += 1
                    if outcome_name is None:
                        long_passes_completed += 1
                    elif outcome_name == UNKNOWN_PASS_OUTCOME:
                        long_passes_unknown_outcome += 1
                    elif outcome_name == INJURY_CLEARANCE_OUTCOME:
                        long_passes_injury_clearance += 1

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
            # ADR 004 D15: a penalty (shot.type "Penalty") is left out of np_*.
            is_penalty = (shot_data.get("type") or {}).get("name") == PENALTY_SHOT_TYPE
            if is_shot_on_target(shot_outcome):
                shots_on_target += 1
                if not is_penalty:
                    np_shots_on_target += 1
            if shot_outcome == "Goal":
                goals += 1
            xg_value = shot_data.get("statsbomb_xg")
            if not is_penalty:
                np_shots += 1
            if xg_value is None:
                shots_missing_xg += 1
                player_id_ctx = (event.get("player") or {}).get("id")
                logger.warning("shot missing statsbomb_xg (player_id=%s)", player_id_ctx)
            else:
                xg += xg_value
                if not is_penalty:
                    np_xg += xg_value

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

        elif event_type == "Ball Recovery":
            br = event.get("ball_recovery") or {}
            if br.get("recovery_failure"):
                ball_recoveries_failed += 1

        elif event_type == "Dribble":
            dribbles_attempted += 1
            dribble_data = event.get("dribble") or {}
            if (dribble_data.get("outcome") or {}).get("name") == "Complete":
                dribbles_completed += 1

        elif event_type == "Foul Won":
            fouls_won += 1

    return {
        "goals": goals,
        "shots": shots,
        "shots_on_target": shots_on_target,
        "xg": xg,
        "shots_missing_xg": shots_missing_xg,
        "np_shots": np_shots,
        "np_shots_on_target": np_shots_on_target,
        "np_xg": np_xg,
        "passes_attempted": passes_attempted,
        "open_play_passes_attempted": open_play_passes_attempted,
        "passes_injury_clearance": passes_injury_clearance,
        "passes_completed": passes_completed,
        "passes_unknown_outcome": passes_unknown_outcome,
        "progressive_passes": progressive_passes,
        "key_passes": key_passes,
        "op_key_passes": op_key_passes,
        "restart_key_passes": restart_key_passes,
        "carries": carries,
        "progressive_carries": progressive_carries,
        "tackles_attempted": actions["tackles_attempted"],
        "tackles_won": tackles_won_count,
        "dribbles_attempted": dribbles_attempted,
        "dribbles_completed": dribbles_completed,
        "interceptions": actions["interceptions"],
        "clearances": actions["clearances"],
        "blocks": actions["blocks"],
        "fouls_committed": actions["fouls_committed"],
        "fouls_won": fouls_won,
        "assists": assists,
        "op_assists": op_assists,
        "restart_assists": restart_assists,
        "crosses_attempted": crosses_attempted,
        "crosses_completed": crosses_completed,
        "crosses_unknown_outcome": crosses_unknown_outcome,
        "dispossessed": dispossessed,
        "miscontrols": miscontrols,
        "pressures": actions["pressures"],
        "ball_recoveries": actions["ball_recoveries"],
        "ball_recoveries_failed": ball_recoveries_failed,
        "dribbled_past": actions["dribbled_past"],
        "aerials_won": aerials_won,
        "aerials_lost": aerials_lost,
        "long_passes_attempted": long_passes_attempted,
        "long_passes_completed": long_passes_completed,
        "long_passes_unknown_outcome": long_passes_unknown_outcome,
        "long_passes_injury_clearance": long_passes_injury_clearance,
        "passes_missing_length": passes_missing_length,
        "switches": switches,
        "final_third_entries_pass": final_third_entries_pass,
        "final_third_entries_carry": final_third_entries_carry,
        "box_entries_pass": box_entries_pass,
        "box_entries_carry": box_entries_carry,
    }


def team_open_play_pass_credits(
    play_events: list[dict],
    timelines: dict[int, PlayerTimeline],
    team_of: dict[int, int],
    clock: PeriodClock,
) -> tuple[Counter[int], list[dict], list[dict]]:
    """
    ADR 004 D12: for every open-play pass (a Pass whose type is not in
    RESTART_TYPES) by a lineup player, the players of the passing team it is
    credited to. The team is the event's `team.id`; only when the event has none is
    the passer's lineup team (`team_of`) used. When both exist and differ, the pass
    is reported as pass_team_mismatch (fails the build) and credited to nobody
    (2015/16: 0 of 1,512,747 passes differ). A pass by a player outside the lineups
    is a dropped event and fails the build elsewhere, so it is skipped here. The team and
    the mismatch come from _lineup_pass_team, shared with opponent_open_play_pass_credits,
    which relies on this function to report the mismatch. Every lineup player must have a
    timeline (_require_timelines).

    Returns (credits per player = team_open_play_passes_while_on, the passes
    credited to a count outside MIN_PLAYERS_PER_PASS..MAX_PLAYERS_PER_PASS (the count
    is _players_for_bounds: the passer once, not counted as an extra player at his own
    exit instant; user decision C, 2026-10-08), the
    team mismatches).

    A teammate is credited when PlayerTimeline.credits_pass_at holds for the pass's
    period and elapsed instant (that method is the one statement of the window
    rule). The passer is
    always credited, even when the pass lies outside his own credited time: that
    is a timeline inconsistency, not absence, and it is reported as
    events_outside_credited_time (ADR 002 §31.6; user decision 2026-10-03). So
    his share can never exceed 1. A pass stamped after its period's whistle is
    clamped to the whistle instant and counts for the players on at the whistle.
    """
    _require_timelines(team_of, timelines)
    team_players = _players_by_team(team_of)
    credits: Counter[int] = Counter()
    out_of_range: list[dict] = []
    mismatches: list[dict] = []
    for e in play_events:
        if not _is_open_play_pass(e):
            continue
        passer = e["player"]["id"]
        team_id, mismatch = _lineup_pass_team(e, team_of)
        if mismatch is not None:
            mismatches.append(mismatch)
        if team_id is None:
            continue
        period, at, end = _event_instant(e, clock)
        credited = {passer}
        for pid in team_players.get(team_id, []):
            if pid != passer and timelines[pid].credits_pass_at(period, at, end):
                credited.add(pid)
        for pid in credited:
            credits[pid] += 1
        players = _players_for_bounds(credited, passer, timelines, period, at, end)
        if not MIN_PLAYERS_PER_PASS <= players <= MAX_PLAYERS_PER_PASS:
            out_of_range.append({"event_id": e.get("id"), "team_id": team_id,
                                 "period": period, "minute": e.get("minute"),
                                 "second": e.get("second"), "players": players})
    return credits, out_of_range, mismatches


def _require_timelines(team_of: dict[int, int], timelines: dict[int, PlayerTimeline]) -> None:
    """Every lineup player has a timeline (the lineup loop assigns one to each, by
    construction). A missing one is reported by raising, never skipped: a skip would drop
    the player from every team credit silently (plan 005 M4g4b backlog #1)."""
    missing = sorted(pid for pid in team_of if pid not in timelines)
    if missing:
        raise ValueError(f"lineup players without a timeline: {missing}")


def _lineup_pass_team(e: dict, team_of: dict[int, int]) -> tuple[int | None, dict | None]:
    """(the passing team, the mismatch record) of a pass: the one team rule of the D12
    credits (team_open_play_pass_credits reports the mismatch and fails the build;
    opponent_open_play_pass_credits skips the pass). The team is the event's team.id, or the
    passer's lineup team when the event has none. A passer outside the lineups is a dropped
    event: (None, None). A team.id that differs from the lineup team: (None, mismatch)."""
    passer = e["player"]["id"]
    lineup_team = team_of.get(passer)
    if lineup_team is None:
        return None, None
    event_team = (e.get("team") or {}).get("id")
    if event_team is not None and event_team != lineup_team:
        return None, {"event_id": e.get("id"), "player_id": passer,
                      "event_team_id": event_team, "lineup_team_id": lineup_team}
    return lineup_team, None


def position_minutes_list(raw_minutes: dict[str, float]) -> list[dict]:
    """The position_minutes cell (plan 006 gate G3): {position, minutes} for each position
    with minutes > 0, sorted by position name, so the column is deterministic."""
    return [{"position": pos, "minutes": m} for pos, m in sorted(raw_minutes.items()) if m > 0]


def lineup_nickname(player: dict, match_id: int) -> str | None:
    """The lineup row's player_nickname (plan 006 pre-W1 gate G1), display only. An empty or
    whitespace-only nickname is null, so the display falls back to the name. A row without the
    field also reads as null: every 2015/16 lineup row has it, and detecting its absence is a
    "before a new source" backlog item (plan 006, G1 outcome)."""
    nick = player.get("player_nickname")
    return nick if nick is not None and nick.strip() else None


def _players_by_team(team_of: dict[int, int]) -> dict[int, list[int]]:
    """Lineup players per team, in team_of order: the one grouping every team credit uses."""
    team_players: dict[int, list[int]] = {}
    for pid, team_id in team_of.items():
        team_players.setdefault(team_id, []).append(pid)
    return team_players


def _players_for_bounds(credited: set[int], actor: int, timelines: dict[int, PlayerTimeline],
                        period: int, at: float, end: float) -> int:
    """
    The count the 7-11 check reads for a team event (user decision C, 2026-10-08; ADR 004
    §13.8): the credited set, the actor once, except that an actor at his own exit instant
    is not counted as an extra player. That is, when his own window does not hold the instant
    (credits_pass_at, half-open) but ADR 002 §31 covers() does (closed): a foul at the second
    of his own substitution (his replacement is on from that instant) or of his own
    dismissal. An event strictly after his exit with 11 others on still counts 12 and fails
    the build. Credits are unchanged: the actor is always credited. Callers run
    _require_timelines first, so the actor's timeline exists.
    """
    timeline = timelines[actor]
    at_own_exit = not timeline.credits_pass_at(period, at, end) and timeline.covers(at)
    return len(credited) - at_own_exit


def _is_open_play_pass(e: dict) -> bool:
    """ADR 004 D12: a Pass by a player whose type is not a restart (RESTART_TYPES). The one
    filter of team_open_play_passes_while_on and opponent_open_play_passes_while_on."""
    return e["type"]["name"] == "Pass" and bool(e.get("player")) and not is_restart(e.get("pass") or {})


def _event_instant(e: dict, clock: PeriodClock) -> tuple[int, float, float]:
    """(period, elapsed instant, the period's elapsed end): the D12 window inputs."""
    period = e["period"]
    end = clock.offset(period) + clock.length(period)
    return period, clock.elapsed(event_instant(e)), end


def opponent_open_play_pass_credits(
    play_events: list[dict],
    timelines: dict[int, PlayerTimeline],
    team_of: dict[int, int],
    clock: PeriodClock,
) -> tuple[Counter[int], list[dict]]:
    """
    ADR 004 §13.8 (M4g4b): for every open-play pass (the D12 filter, _is_open_play_pass) by a
    lineup player, the players of the *other* lineup team it is credited to:
    opponent_open_play_passes_while_on. A defender is credited when
    PlayerTimeline.credits_pass_at holds for the pass's period and instant (the D12 window).
    There is no passer rule. The passing team is the event's team.id (the passer's lineup
    team when the event has none; _lineup_pass_team, shared with
    team_open_play_pass_credits); a pass whose team.id differs from the passer's lineup team
    is skipped here (team_open_play_pass_credits reports it and fails the build), as is a
    pass by a player outside the lineups (a dropped event). Every lineup player must have a
    timeline (_require_timelines).

    Returns (credits per player, the passes credited to a defender count outside
    MIN_DEFENDERS_PER_PASS..MAX_DEFENDERS_PER_PASS, i.e. 7-11).
    """
    _require_timelines(team_of, timelines)
    team_players = _players_by_team(team_of)
    credits: Counter[int] = Counter()
    out_of_range: list[dict] = []
    for e in play_events:
        if not _is_open_play_pass(e):
            continue
        lineup_team, _ = _lineup_pass_team(e, team_of)
        if lineup_team is None:
            continue
        period, at, end = _event_instant(e, clock)
        defenders = [pid for team_id, pids in team_players.items() if team_id != lineup_team
                     for pid in pids if timelines[pid].credits_pass_at(period, at, end)]
        for pid in defenders:
            credits[pid] += 1
        if not MIN_DEFENDERS_PER_PASS <= len(defenders) <= MAX_DEFENDERS_PER_PASS:
            out_of_range.append({"event_id": e.get("id"), "team_id": lineup_team,
                                 "period": period, "minute": e.get("minute"),
                                 "second": e.get("second"), "players": len(defenders)})
    return credits, out_of_range


def team_action_credits(
    play_events: list[dict],
    timelines: dict[int, PlayerTimeline],
    team_of: dict[int, int],
    clock: PeriodClock,
) -> tuple[dict[str, Counter[int]], list[dict], list[dict]]:
    """
    ADR 004 §13.8 (M4g4b): team_<action>_while_on for the DEFENSIVE_ACTIONS. Every event that
    defensive_action() counts (the rule of the player's own counts) by a lineup player is
    credited to its actor and to each teammate for whom PlayerTimeline.credits_pass_at holds
    (the D12 window rule). The actor is always credited, even outside his own credited time
    (e.g. an action at the instant of his own dismissal), so a player's team count is never
    below his own count.

    The 7-11 check reads _players_for_bounds (user decision C, 2026-10-08): the credited set,
    the actor once, not counted as an extra player at his own exit instant. A count of 12 (an
    event strictly after the actor's exit with 11 others on) or below 7 is reported and fails
    the build. A team.id that differs from the actor's lineup team is
    reported, fails the build and is credited to nobody; an actor outside the lineups is a
    dropped event and is skipped here. Every lineup player must have a timeline
    (_require_timelines).

    Returns (credits per action and player, the out-of-range events, the team mismatches).
    """
    _require_timelines(team_of, timelines)
    team_players = _players_by_team(team_of)
    credits: dict[str, Counter[int]] = {a: Counter() for a in DEFENSIVE_ACTIONS}
    out_of_range: list[dict] = []
    mismatches: list[dict] = []
    for e in play_events:
        action = defensive_action(e)
        if action is None or not e.get("player"):
            continue
        actor = e["player"]["id"]
        lineup_team = team_of.get(actor)
        if lineup_team is None:
            continue
        event_team = (e.get("team") or {}).get("id")
        if event_team is not None and event_team != lineup_team:
            mismatches.append({"event_id": e.get("id"), "action": action, "player_id": actor,
                               "event_team_id": event_team, "lineup_team_id": lineup_team})
            continue
        period, at, end = _event_instant(e, clock)
        credited = {actor}
        for pid in team_players.get(lineup_team, []):
            if pid != actor and timelines[pid].credits_pass_at(period, at, end):
                credited.add(pid)
        for pid in credited:
            credits[action][pid] += 1
        players = _players_for_bounds(credited, actor, timelines, period, at, end)
        if not MIN_PLAYERS_PER_PASS <= players <= MAX_PLAYERS_PER_PASS:
            out_of_range.append({"event_id": e.get("id"), "action": action,
                                 "team_id": lineup_team, "period": period,
                                 "minute": e.get("minute"), "second": e.get("second"),
                                 "players": players})
    return credits, out_of_range, mismatches


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
    # ADR 002 §31.1: raises (the match fails) without a valid Half End per period.
    clock = PeriodClock.from_events(events)
    match_duration = clock.duration
    # ADR 004 D1: the nominal length of the periods played (90, or 120 with extra
    # time), for minutes_nominal_equiv in build_player_season.
    match_nominal_duration = NOMINAL_PERIOD_END[clock.periods[-1]]
    # Penalty-shootout events (period 5) are not playing time and count toward
    # no metric. Dismissal lookups use this list too: a shootout card clamps
    # nothing, because every stint has ended by then (extra time, periods 3-4,
    # still counts). The Starting XI lookup keeps the full list.
    play_events = [e for e in events if is_playing_time(e)]

    # Compute xg_assisted per player (requires full match event list for cross-player lookup)
    xga_fallbacks: Counter[str] = Counter()
    op_xga: dict[int, float] = {}
    restart_xga: dict[int, float] = {}
    xga_by_player = compute_xg_assisted_per_player(
        play_events, max_carry_units=XG_ASSISTED_MAX_CARRY_UNITS, fallbacks=xga_fallbacks,
        open_play_xga=op_xga, restart_xga=restart_xga,
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
    quality_report: list[dict] = _period_end_issues(match_id, clock)
    event_issues: Counter[tuple[str, str | None]] = Counter()
    # Every lineup player's timeline, for events_outside_credited_time.
    timelines: dict[int, PlayerTimeline] = {}

    for team in lineups:
        team_id = team["team_id"]
        team_name = team["team_name"]
        # Collect (row, positions) tuples for the team before the started check
        # so the team-level fallback can inspect positions in a second pass.
        team_entries: list[tuple[dict, list]] = []
        # (player_id, positions, dismissal, substitution off, unpaired Off) for the
        # on-pitch count.
        on_pitch_input: list[OnPitchPlayer] = []
        # Σ over the team's players of their minutes inside each period (§31.5).
        team_period_minutes: Counter[int] = Counter()

        for player in team["lineup"]:
            player_id = player["player_id"]
            player_name = player["player_name"]
            positions = player.get("positions") or []

            dismissal = find_dismissal(play_events, player_id)
            if dismissal is not None:
                quality_report.extend(_dismissal_timing_issues(match_id, player_id, dismissal, clock))

            substitution = _substitution_off(
                match_id, player_id, positions, play_events, quality_report
            )
            treatment = find_treatment_windows(play_events, player_id)
            unpaired_off = treatment.unpaired_off
            if treatment.unpaired_off_position is not None:
                quality_report.extend(_events_after_permanent_off(
                    match_id, player_id, play_events, treatment.unpaired_off_position))
            on_pitch_input.append((player_id, positions, dismissal, substitution, unpaired_off))
            quality_report.extend(_stint_bound_issues(match_id, player_id, positions, clock))

            # ONE timeline per player drives minutes_played, the group minutes and
            # position_raw (ADR 002 §31.4); they cannot diverge.
            windows = treatment.windows

            def timeline_with(exit_at: Instant | None) -> PlayerTimeline:
                return player_timeline(positions, clock, exit_at=exit_at, treatment_windows=windows)

            exit_at = stint_end_cap(
                dismissal=dismissal, substitution=substitution, unpaired_off=unpaired_off)
            timeline = timeline_with(exit_at)
            timelines[player_id] = timeline
            group_minutes = minutes_per_group(timeline)
            minutes_played = sum(group_minutes.values())
            for piece in timeline.pieces:
                team_period_minutes[piece.period] += piece.minutes
            if minutes_played > match_duration + PLAYER_MINUTES_TOLERANCE:
                quality_report.append({
                    "issue": PLAYER_MINUTES_EXCEED_DURATION,
                    "match_id": match_id,
                    "team_id": team_id,
                    "player_id": player_id,
                    "minutes_played": minutes_played,
                    "match_duration": match_duration,
                })
            if timeline.overlap_minutes > TIME_EPS:
                quality_report.append({
                    "issue": OVERLAPPING_STINTS,
                    "match_id": match_id,
                    "player_id": player_id,
                    "minutes_deduplicated": timeline.overlap_minutes,
                })
            if timeline.treatment_windows_inside_stints:
                quality_report.append({
                    "issue": TREATMENT_WINDOW_INSIDE_STINT,
                    "match_id": match_id,
                    "player_id": player_id,
                    "windows": timeline.treatment_windows_inside_stints,
                })

            if substitution is not None:
                uncapped = timeline_with(stint_end_cap(
                    dismissal=dismissal, substitution=None, unpaired_off=unpaired_off)).minutes
                if uncapped > minutes_played:
                    quality_report.append({
                        "issue": SUBSTITUTION_CAPPED,
                        "match_id": match_id,
                        "team_id": team_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        "substitution_minute": substitution[1],
                        "minutes_removed": uncapped - minutes_played,
                    })
            if unpaired_off is not None:
                uncapped = timeline_with(stint_end_cap(
                    dismissal=dismissal, substitution=substitution, unpaired_off=None)).minutes
                if uncapped - minutes_played > TIME_EPS:
                    quality_report.append({
                        "issue": UNPAIRED_PLAYER_OFF_CAPPED,
                        "match_id": match_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        "player_off_period": unpaired_off[0],
                        "player_off_minute": unpaired_off[1],
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

            raw_minutes = minutes_per_raw_position(timeline)
            position_raw = _primary_raw_position(raw_minutes)

            # Dismissal quality check: record every dismissed player and whether
            # the raw StatsBomb lineup data would have overcounted their minutes
            # (no exit cap at all). He can be credited at most the elapsed time
            # from his entry (his first credited instant) to the card, so a
            # substitute is held to that span, not to the card's elapsed time.
            if dismissal is not None:
                unfixed = timeline_with(None)
                minutes_unfixed = unfixed.minutes
                dismissal_elapsed = clock.elapsed(dismissal)
                entry_elapsed = unfixed.pieces[0].start  # minutes_played > 0: never empty
                overcounted = (minutes_unfixed
                               > dismissal_elapsed - entry_elapsed + DISMISSAL_TOLERANCE_MINUTES)
                # cause is only meaningful when overcounting actually occurred.
                # "to_null": at least one open-ended (to=None) stint was extended
                #   to the match end instead of stopping at the card.
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
                    "dismissal_period": dismissal[0],
                    "dismissal_minute": dismissal[1],
                    "dismissal_elapsed": dismissal_elapsed,
                    "entry_elapsed": entry_elapsed,
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
                    "player_nickname": lineup_nickname(player, match_id),
                    "team_id": team_id,
                    "team_name": team_name,
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "source": SOURCE_STATSBOMB,
                    "minutes_played": minutes_played,
                    "match_duration": match_duration,
                    "match_nominal_duration": match_nominal_duration,
                    "position_raw": position_raw,
                    "position_group": position_group,
                    "position_minutes": position_minutes_list(raw_minutes),
                    "started": started,
                    "started_inferred": False,
                    **_group_minutes_to_columns(group_minutes),
                    **_aggregate_events(player_events.get(player_id, []), event_issues),
                    "xg_assisted": xga,
                    "xg_assisted_direct": xga_direct,
                    "op_xg_assisted": op_xga.get(player_id, 0.0),
                    "restart_xg_assisted": restart_xga.get(player_id, 0.0),
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

        for period, team_minutes in sorted(team_period_minutes.items()):
            ceiling = PLAYERS_ON_PITCH * clock.length(period)
            if team_minutes > ceiling + TEAM_MINUTES_TOLERANCE:
                quality_report.append({
                    "issue": TEAM_MINUTES_EXCEED_CEILING,
                    "match_id": match_id,
                    "team_id": team_id,
                    "period": period,
                    "team_minutes": team_minutes,
                    "ceiling": ceiling,
                })

        excess = _on_pitch_excess(on_pitch_input, clock.end)
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

    # Report-only (ADR 004 D16): assisted shots whose direct window could not be
    # checked, and broken assist links.
    for issue in XGA_FALLBACKS:
        if xga_fallbacks[issue]:
            quality_report.append({"issue": issue, "match_id": match_id,
                                   "count": xga_fallbacks[issue]})
    for (issue, value), count in sorted(event_issues.items(), key=str):
        quality_report.append({
            "issue": issue, "match_id": match_id, "value": value, "count": count,
        })
    # ADR 004 D12: team open-play passes while on, from the same timelines.
    team_of = {p["player_id"]: team["team_id"] for team in lineups for p in team["lineup"]}
    credits, out_of_range, mismatches = team_open_play_pass_credits(
        play_events, timelines, team_of, clock)
    # ADR 004 §13.8 (M4g4b): the opponent's open-play passes and the team's actions while on.
    opp_credits, opp_out_of_range = opponent_open_play_pass_credits(
        play_events, timelines, team_of, clock)
    action_credits, action_out_of_range, action_mismatches = team_action_credits(
        play_events, timelines, team_of, clock)
    for row in rows:
        row["team_open_play_passes_while_on"] = credits.get(row["player_id"], 0)
        row["opponent_open_play_passes_while_on"] = opp_credits.get(row["player_id"], 0)
        for a in DEFENSIVE_ACTIONS:
            row[team_column(a)] = action_credits[a].get(row["player_id"], 0)
    quality_report.extend({"issue": PASS_CREDIT_OUT_OF_RANGE, "match_id": match_id, **d}
                          for d in out_of_range)
    quality_report.extend({"issue": PASS_TEAM_MISMATCH, "match_id": match_id, **d}
                          for d in mismatches)
    quality_report.extend({"issue": OPPONENT_PASS_CREDIT_OUT_OF_RANGE, "match_id": match_id, **d}
                          for d in opp_out_of_range)
    quality_report.extend({"issue": TEAM_ACTION_CREDIT_OUT_OF_RANGE, "match_id": match_id, **d}
                          for d in action_out_of_range)
    quality_report.extend({"issue": TEAM_ACTION_TEAM_MISMATCH, "match_id": match_id, **d}
                          for d in action_mismatches)
    quality_report.extend(_dropped_events(match_id, player_events, lineups, rows))
    quality_report.extend(_events_outside_credited_time(match_id, play_events, timelines, clock))

    return rows, quality_report


def _period_end_issues(match_id: int, clock: PeriodClock) -> list[dict]:
    """
    ADR 002 §31.1: a period that ends before its nominal end is reported
    (PERIOD_END_BEFORE_NOMINAL); one more than PERIOD_SHORT_FAIL_MINUTES short
    also fails the build (PERIOD_TOO_SHORT).
    """
    issues: list[dict] = []
    for period in clock.periods:
        shortfall = NOMINAL_PERIOD_END[period] - clock.ends[period]
        if shortfall <= 0:
            continue
        entry = {"match_id": match_id, "period": period, "shortfall": shortfall}
        issues.append({"issue": PERIOD_END_BEFORE_NOMINAL, **entry})
        if shortfall > PERIOD_SHORT_FAIL_MINUTES:
            issues.append({"issue": PERIOD_TOO_SHORT, **entry})
    return issues


def _dismissal_timing_issues(
    match_id: int, player_id: int, dismissal: Instant, clock: PeriodClock,
) -> list[dict]:
    """
    ADR 002 §31.2, report-only: a card after the match's end, or after a
    non-final period's end (in the interval). Both are clamped by the clock.
    """
    if dismissal > clock.end:
        issue = DISMISSAL_AFTER_MATCH_END
    elif dismissal[1] > clock.ends[dismissal[0]] + TIME_EPS:
        issue = DISMISSAL_AFTER_PERIOD_END
    else:
        return []
    return [{
        "issue": issue,
        "match_id": match_id,
        "player_id": player_id,
        "dismissal_period": dismissal[0],
        "dismissal_minute": dismissal[1],
        "period_end": clock.ends.get(dismissal[0]),
    }]


def _stint_bound_issues(
    match_id: int, player_id: int, positions: list[dict], clock: PeriodClock,
) -> list[dict]:
    """
    Clamp guards (ADR 002 §31.1): clock.elapsed() clamps a minute into its
    period, so a stint bound outside its period is checked here instead.
    - a `from` or `to` before its period's start fails the build;
    - a `to` after its period's Half End fails the build;
    - a `from` after its period's Half End (a player returning after the
      whistle) is report-only: the stint gets no minutes; more than
      STINT_LATE_START_FAIL_MINUTES after it fails the build (ADR 004 D22).
    A `to` without `to_period` is left to player_timeline, which raises.
    """
    issues: list[dict] = []

    def add(issue: str, field: str, period: int, minute: float) -> None:
        issues.append({"issue": issue, "match_id": match_id, "player_id": player_id,
                       "field": field, "period": period, "minute": minute})

    for stint in positions:
        bounds = [("from", stint["from_period"], parse_mm_ss(stint["from"]))]
        if stint.get("to") and stint.get("to_period") is not None:
            bounds.append(("to", stint["to_period"], parse_mm_ss(stint["to"])))
        for field, period, minute in bounds:
            if period not in clock.ends:
                raise ValueError(f"player {player_id}: stint {field} in period {period}, "
                                 f"not a playing period of this match {clock.periods}")
            if minute < PERIOD_START[period] - TIME_EPS:
                add(STINT_TIME_BEFORE_PERIOD_START, field, period, minute)
            elif minute > clock.ends[period] + TIME_EPS:
                add(STINT_AFTER_PERIOD_END if field == "from" else STINT_END_AFTER_PERIOD_END,
                    field, period, minute)
                if (field == "from"
                        and minute - clock.ends[period] > STINT_LATE_START_FAIL_MINUTES + TIME_EPS):
                    add(STINT_LATE_START_EXCEEDS_LIMIT, field, period, minute)
    return issues


def report_only_limit_failures(quality_report: list[dict], n_matches: int) -> list[dict]:
    """
    ADR 004 D22: one entry per report-only issue whose count in this
    competition-season exceeds max(limit per 100 matches x n_matches / 100,
    REPORT_ONLY_LIMIT_FLOOR). Counts are entries, except
    events_outside_credited_time, counted per (match_id, player_id), and the
    xG-assisted fallbacks (XGA_FALLBACKS), whose per-match entries carry a shot
    count that is summed. The issues themselves stay report-only; only the
    season-level count can fail.
    """
    failures = []
    for issue, per_100 in REPORT_ONLY_LIMITS_PER_100_MATCHES.items():
        entries = [d for d in quality_report if d.get("issue") == issue]
        if issue in _LIMIT_COUNTED_PER_PLAYER_MATCH:
            count = len({(d["match_id"], d.get("player_id")) for d in entries})
        elif issue in XGA_FALLBACKS:
            count = sum(d["count"] for d in entries)
        else:
            count = len(entries)
        limit = max(per_100 * n_matches / 100, REPORT_ONLY_LIMIT_FLOOR)
        if count > limit:
            failures.append({"issue": REPORT_ONLY_LIMIT_EXCEEDED, "report_issue": issue,
                             "count": count, "limit": limit, "matches": n_matches})
    return failures


def _events_after_permanent_off(
    match_id: int, player_id: int, play_events: list[dict], off_position: int,
) -> list[dict]:
    """
    ADR 002 §31.2: after an unpaired Player Off the player is gone, so any
    later event by him fails the build, except OFF_PITCH_EVENT_TYPES (a card
    shown off the pitch, his own Substitution).
    """
    return [{
        "issue": EVENT_AFTER_PERMANENT_OFF,
        "match_id": match_id,
        "player_id": player_id,
        "event_type": e["type"]["name"],
        "period": e["period"],
        "minute": e["minute"],
        "second": e["second"],
    } for e in play_events[off_position + 1:]
        if (e.get("player") or {}).get("id") == player_id
        and e["type"]["name"] not in OFF_PITCH_EVENT_TYPES]


def _events_outside_credited_time(
    match_id: int,
    play_events: list[dict],
    timelines: dict[int, PlayerTimeline],
    clock: PeriodClock,
) -> list[dict]:
    """
    Report-only (plan 004 H4b, ADR 002 §31.6): one entry per period 1-4 event
    by a player whose instant lies outside his credited time (closed
    intervals), except OUTSIDE_TIME_EXEMPT_TYPES. A player not in the lineup
    has no credited time (his events also fail as dropped events).
    """
    entries: list[dict] = []
    for e in play_events:
        pid = (e.get("player") or {}).get("id")
        if pid is None or e["type"]["name"] in OUTSIDE_TIME_EXEMPT_TYPES:
            continue
        timeline = timelines.get(pid)
        if timeline is None or not timeline.covers(clock.elapsed(event_instant(e))):
            entries.append({
                "issue": EVENTS_OUTSIDE_CREDITED_TIME,
                "match_id": match_id,
                "player_id": pid,
                "event_type": e["type"]["name"],
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
            })
    return entries


def _substitution_off(
    match_id: int,
    player_id: int,
    positions: list[dict],
    play_events: list[dict],
    report: list[dict],
) -> Instant | None:
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


# (player_id, positions, dismissal, substitution off, unpaired Player Off), each
# exit as (period, minute) or None.
OnPitchPlayer = tuple[int, list[dict], Instant | None, Instant | None, Instant | None]

# Order of changes at one instant (only for a deterministic sort: the count is
# taken after every change at the instant, and whether a card lowers the
# allowed number is decided before the sweep).
_CARD, _EXIT, _ENTRY = 0, 1, 2


def _card_lowers_allowed(
    positions: list[dict],
    dismissal: Instant,
    substitution: Instant | None,
    match_end: Instant,
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
        if start <= dismissal and stint_end(stint, match_end) > start:
            return True
    return False


def _on_pitch_excess(
    players: list[OnPitchPlayer],
    match_end: Instant,
) -> tuple[int, int, Instant] | None:
    """
    (on_pitch, allowed, (period, minute)) at the first instant where the
    team has the most players above the allowed number, or None when it
    never exceeds it.

    allowed starts at 11 and drops by one at each dismissal that leaves the
    team a player short (_card_lowers_allowed): the player had taken the field
    and was not substituted off before or at his card, so a player off for
    treatment counts but a card on the bench does not.

    `players` holds (player_id, positions, dismissal, substitution,
    unpaired_off): the player's red card or second yellow, his own
    substitution off and his unpaired Player Off, each as (period, minute) or
    None. His exit is the earliest of the three (minutes.stint_end_cap, the
    same rule as his minutes, ADR 002 §31.2). Instants are ordered by
    (period, minute), not by the match-absolute minute, so first-half
    stoppage (46:01 in period 1) precedes the second-half kick-off (45:00 in
    period 2). Treatment windows are not subtracted: a player off for
    treatment still counts on his team's side (§31.3). A stint ends at its (to_period, to), at
    match_end when open, and never after the player's exit, so a dismissed
    player stops counting at his card. A stint whose end does not follow its
    start (e.g. a reversed-period stint) contributes nothing. A player counts
    once even when his own stints overlap. The count is taken after every
    change at an instant, so a substitution or a dismissal is never a moment
    with one player too many.

    Raises ValueError for a stint with a `to` but no `to_period`.
    """
    changes: list[tuple[tuple[int, float], int, int]] = []
    for player_id, positions, dismissal, substitution, unpaired_off in players:
        exit_at = stint_end_cap(
            dismissal=dismissal, substitution=substitution, unpaired_off=unpaired_off)
        if dismissal is not None and _card_lowers_allowed(
            positions, dismissal, substitution, match_end
        ):
            changes.append((dismissal, _CARD, player_id))
        for stint in positions:
            start = (stint["from_period"], parse_mm_ss(stint["from"]))
            end = stint_end(stint, match_end)
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
        DataQualityError: if any FAILING_QUALITY_ISSUES entry is in the report,
            a report-only issue exceeds its ADR 004 D22 limit, or any shot lacks
            statsbomb_xg.
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
            # The match file's date (plan 006 gate G3); a missing date fails the match.
            match_date = dt.date.fromisoformat(match["match_date"])
            for row in rows:
                row["match_date"] = match_date
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
    ] + report_only_limit_failures(quality_report, len(matches))
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
        **{issue: sum(1 for d in report if d.get("issue") == issue)
           for issue in PERIOD_AWARE_REPORT_ONLY_ISSUES},
        **{issue: sum(d["count"] for d in report if d.get("issue") == issue)
           for issue in XGA_FALLBACKS},
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
            f"player_minutes_exceed_duration={summary['player_minutes_exceed_duration']} "
            + " ".join(f"{issue}={summary[issue]}" for issue in PERIOD_AWARE_REPORT_ONLY_ISSUES)
        )
