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
- One dismissal quality report is collected per season run and returned separately.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from eleveniq.metrics.minutes import compute_match_duration
from eleveniq.metrics.positions import (
    POSITION_GROUP_COLUMNS,
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
from eleveniq.metrics.shots import is_shot_on_target
from eleveniq.metrics.tackles import is_tackle_won
from eleveniq.metrics.xg_assisted import compute_xg_assisted_per_player
from eleveniq.config import (
    COMPETITION_SEASONS,
    LONG_PASS_THRESHOLD_UNITS,
    SOURCE_STATSBOMB,
    XG_ASSISTED_MAX_CARRY_UNITS,
    CompetitionSeason,
)
from eleveniq.pipeline.quality_report import (
    DISMISSAL_TOLERANCE_MINUTES,
    check_dismissal_event_quality,
    find_dismissal_minute,
)
from eleveniq.statsbomb.loader import load_events, load_lineups, load_matches

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INTERIM_DIR = _PROJECT_ROOT / "data" / "interim"
# Combined output (input to build_player_season) and per-competition-season parts.
OUTPUT_PATH = _INTERIM_DIR / "player_match_stats.parquet"
PART_DIR = _INTERIM_DIR / "player_match"

# Quality-report issues that fail the build (starter cross-check).
STARTING_XI_ISSUES = frozenset(
    {"starting_xi_missing", "starting_xi_mismatch", "starting_xi_duplicate"}
)


class StarterCrossCheckError(RuntimeError):
    """Derived starters disagree with the Starting XI event for some team-match."""


class MatchProcessingError(RuntimeError):
    """One or more matches of a competition-season raised during processing."""

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


def _aggregate_events(player_events: list[dict]) -> dict:
    """Count raw metrics from one player's events in a single match."""
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
            elif outcome_name == "Unknown":
                passes_unknown_outcome += 1

            loc = event.get("location")
            end_loc = pass_data.get("end_location")
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

            pass_type_name: str | None = (pass_data.get("type") or {}).get("name")
            pass_length = pass_data.get("length")
            if pass_length is None:
                passes_missing_length += 1
            else:
                if pass_length >= LONG_PASS_THRESHOLD_UNITS and pass_type_name not in SET_PIECE_TYPES:
                    long_passes_attempted += 1
                    if outcome_name is None:
                        long_passes_completed += 1

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
            if duel_type_name == "Tackle":
                tackles_attempted += 1
                duel_outcome = (duel_data.get("outcome") or {}).get("name")
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
    Process one match into player-match rows plus a dismissal quality report.

    Returns:
        (rows, dismissal_report)
        rows: list of player-match dicts ready for a Polars DataFrame.
        dismissal_report: list of dismissal quality-check dicts for this match.
    """
    events = load_events(match_id, data_root)
    lineups = load_lineups(match_id, data_root)
    match_duration = compute_match_duration(events)

    # Compute xg_assisted per player (requires full match event list for cross-player lookup)
    xga_by_player = compute_xg_assisted_per_player(
        events, max_carry_units=XG_ASSISTED_MAX_CARRY_UNITS
    )

    starting_xi = _starting_xi_by_team(events)

    # Build per-player event index
    player_events: dict[int, list[dict]] = {}
    for event in events:
        pid = (event.get("player") or {}).get("id")
        if pid is not None:
            if pid not in player_events:
                player_events[pid] = []
            player_events[pid].append(event)

    rows: list[dict] = []
    dismissal_report: list[dict] = []

    for team in lineups:
        team_id = team["team_id"]
        team_name = team["team_name"]
        # Collect (row, positions) tuples for the team before the started check
        # so the team-level fallback can inspect positions in a second pass.
        team_entries: list[tuple[dict, list]] = []

        for player in team["lineup"]:
            player_id = player["player_id"]
            player_name = player["player_name"]
            positions = player.get("positions") or []

            dismissal_minute = find_dismissal_minute(events, player_id)

            # ONE shared call: compute_minutes_per_group drives both minutes_played
            # and position group.  compute_player_minutes is NOT called separately.
            group_minutes = compute_minutes_per_group(
                positions,
                match_duration,
                dismissal_minute=dismissal_minute,
            )
            minutes_played = sum(group_minutes.values())

            if minutes_played == 0.0:
                continue  # unused substitute

            position_group = primary_position_group(group_minutes)
            started = any(p.get("start_reason") == "Starting XI" for p in positions)

            raw_minutes = compute_minutes_per_raw_position(
                positions,
                match_duration,
                dismissal_minute=dismissal_minute,
            )
            position_raw = _primary_raw_position(raw_minutes)

            # Dismissal quality check: record every dismissed player and whether
            # the raw StatsBomb lineup data would have overcounted their minutes.
            if dismissal_minute is not None:
                raw_group_minutes_unfixed = compute_minutes_per_group(
                    positions, match_duration
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
                dismissal_report.append({
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
                # find_dismissal_minute returns None either because the player
                # was not dismissed, or because the dismissal event has missing
                # timestamp fields. Surface the second case to the report so it
                # is not silently ignored.
                quality_issue = check_dismissal_event_quality(events, player_id)
                if quality_issue is not None:
                    dismissal_report.append({
                        "match_id": match_id,
                        "player_id": player_id,
                        "player_name": player_name,
                        **quality_issue,
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
                    "position_raw": position_raw,
                    "position_group": position_group,
                    "started": started,
                    "started_inferred": False,
                    **_group_minutes_to_columns(group_minutes),
                    **_aggregate_events(player_events.get(player_id, [])),
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
                dismissal_report.append({
                    "issue": "started_inferred_fallback",
                    "match_id": match_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "players": inferred_players,
                })
            final_count = sum(1 for row, _ in team_entries if row["started"])
            if final_count != 11:
                dismissal_report.append({
                    "issue": "starter_count_mismatch",
                    "match_id": match_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "starter_count_after_fallback": final_count,
                })
        elif starter_count > 11:
            dismissal_report.append({
                "issue": "starter_count_mismatch",
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
            dismissal_report.append({
                "issue": "starting_xi_missing",
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
            })
        elif len(xi_events) > 1:
            dismissal_report.append({
                "issue": "starting_xi_duplicate",
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
                "starting_xi_events": len(xi_events),
            })
        elif derived != (expected := xi_events[0]):
            dismissal_report.append({
                "issue": "starting_xi_mismatch",
                "match_id": match_id,
                "team_id": team_id,
                "team_name": team_name,
                "missing_from_derived": sorted(expected - derived),
                "extra_in_derived": sorted(derived - expected),
            })

        rows.extend(row for row, _ in team_entries)

    return rows, dismissal_report


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
        In both cases the parquet is not written.
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
    all_dismissals: list[dict] = []
    failed_ids: list[int] = []

    for match in matches:
        match_id = match["match_id"]
        try:
            rows, dismissals = _process_match(
                match_id, competition_id, season_id, data_root=data_root
            )
            all_rows.extend(rows)
            all_dismissals.extend(dismissals)
        except Exception:  # noqa: BLE001
            logger.error("match_id=%d failed", match_id, exc_info=True)
            failed_ids.append(match_id)

    if failed_ids:
        raise MatchProcessingError(
            f"competition={competition_id} season={season_id}: "
            f"{len(failed_ids)} match(es) failed: {failed_ids}"
        )

    xi_issues = [
        d for d in all_dismissals if d.get("issue") in STARTING_XI_ISSUES
    ]
    if xi_issues:
        raise StarterCrossCheckError(
            f"competition={competition_id} season={season_id}: "
            f"{len(xi_issues)} team-match(es) where derived starters differ from "
            f"the Starting XI event. First: {xi_issues[0]}"
        )

    df = pl.DataFrame(all_rows, schema=_SCHEMA)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    logger.info("Wrote %d rows → %s", len(df), out_path)
    return df, all_dismissals


def part_path(competition_id: int, season_id: int, part_dir: Path | None = None) -> Path:
    """Per-competition-season parquet path: {part_dir}/{comp}_{season}.parquet."""
    return (part_dir or PART_DIR) / f"{competition_id}_{season_id}.parquet"


def summarise_quality_report(report: list[dict]) -> dict[str, int]:
    """Counts of each quality-report category for logging."""
    return {
        "dismissals": sum(1 for d in report if d.get("issue") is None),
        "missing_timestamp": sum(
            1 for d in report if d.get("issue") == "dismissal_missing_timestamp"
        ),
        "fallback_team_matches": sum(
            1 for d in report if d.get("issue") == "started_inferred_fallback"
        ),
        "fallback_players": sum(
            len(d["players"]) for d in report
            if d.get("issue") == "started_inferred_fallback"
        ),
        "starter_count_mismatches": sum(
            1 for d in report if d.get("issue") == "starter_count_mismatch"
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


def _select(
    competition_id: int | None, season_id: int | None
) -> tuple[CompetitionSeason, ...]:
    selected = tuple(
        cs for cs in COMPETITION_SEASONS
        if (competition_id is None or cs.competition_id == competition_id)
        and (season_id is None or cs.season_id == season_id)
    )
    if not selected:
        raise SystemExit(
            f"No configured competition-season matches "
            f"competition_id={competition_id} season_id={season_id}"
        )
    return selected


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

    summaries = build_all(
        COMPETITION_SEASONS,
        only=_select(args.competition_id, args.season_id),
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
            f"starter_count_mismatches={summary['starter_count_mismatches']}"
        )
