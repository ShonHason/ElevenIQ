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
from eleveniq.metrics.progressive import is_progressive_pass, is_progressive_carry
from eleveniq.metrics.shots import is_shot_on_target
from eleveniq.metrics.tackles import is_tackle_won
from eleveniq.pipeline.quality_report import (
    DISMISSAL_TOLERANCE_MINUTES,
    check_dismissal_event_quality,
    find_dismissal_minute,
)
from eleveniq.statsbomb.loader import load_events, load_lineups, load_matches

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INTERIM_DIR = _PROJECT_ROOT / "data" / "interim"
OUTPUT_PATH = _INTERIM_DIR / "player_match_stats.parquet"

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

    for event in player_events:
        event_type = event.get("type", {}).get("name", "")

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
                if is_progressive_carry((loc[0], loc[1]), (end_loc[0], end_loc[1])):
                    progressive_carries += 1

        elif event_type == "Duel":
            duel_data = event.get("duel") or {}
            if (duel_data.get("type") or {}).get("name") == "Tackle":
                tackles_attempted += 1
                duel_outcome = (duel_data.get("outcome") or {}).get("name")
                if is_tackle_won(duel_outcome):
                    tackles_won_count += 1

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
    }


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

            team_entries.append((
                {
                    "match_id": match_id,
                    "player_id": player_id,
                    "player_name": player_name,
                    "team_id": team_id,
                    "team_name": team_name,
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "source": "statsbomb",
                    "minutes_played": minutes_played,
                    "position_raw": position_raw,
                    "position_group": position_group,
                    "started": started,
                    "started_inferred": False,
                    **_group_minutes_to_columns(group_minutes),
                    **_aggregate_events(player_events.get(player_id, [])),
                },
                positions,
            ))

        # Team-level started count.  Apply fallback when primary count < 11:
        # a player with any period-1 stint beginning at "00:00" also counts as
        # started (covers StatsBomb data where real starters have a non-"Starting
        # XI" start_reason, e.g. "Tactical Shift" — confirmed in match 3825596).
        starter_count = sum(1 for row, _ in team_entries if row["started"])
        if starter_count < 11:
            inferred_players: list[str] = []
            for row, positions in team_entries:
                if not row["started"] and any(
                    p.get("from") == "00:00" and p.get("from_period", 1) == 1
                    for p in positions
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

        rows.extend(row for row, _ in team_entries)

    return rows, dismissal_report


def build_competition_season(
    competition_id: int,
    season_id: int,
    *,
    data_root: Path | None = None,
    output_path: Path | None = None,
) -> tuple[pl.DataFrame, list[dict], list[int]]:
    """
    Process all matches for one competition-season.

    Writes player_match_stats.parquet and returns (DataFrame, quality_report, failed_match_ids).
    Errors on individual matches are logged and skipped so one bad file does
    not abort the entire season.
    """
    out_path = output_path or OUTPUT_PATH
    matches = load_matches(competition_id, season_id, data_root)
    logger.info(
        "Building competition=%d season=%d  matches=%d",
        competition_id,
        season_id,
        len(matches),
    )

    all_rows: list[dict] = []
    all_dismissals: list[dict] = []
    errors: list[tuple[int, Exception]] = []

    for match in matches:
        match_id = match["match_id"]
        try:
            rows, dismissals = _process_match(
                match_id, competition_id, season_id, data_root=data_root
            )
            all_rows.extend(rows)
            all_dismissals.extend(dismissals)
        except Exception as exc:  # noqa: BLE001
            logger.error("match_id=%d failed", match_id, exc_info=True)
            errors.append((match_id, exc))

    df = pl.DataFrame(all_rows, schema=_SCHEMA)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    logger.info(
        "Wrote %d rows → %s  (errors on %d matches)",
        len(df),
        out_path,
        len(errors),
    )

    failed_ids = [mid for mid, _ in errors]
    return df, all_dismissals, failed_ids


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
        stream=sys.stdout,
    )
    # La Liga 2015/16: competition_id=11, season_id=27
    df, dismissals, failed_ids = build_competition_season(11, 27)
    n_dismissed = sum(1 for d in dismissals if d.get("issue") is None)
    n_missing_ts = sum(
        1 for d in dismissals if d.get("issue") == "dismissal_missing_timestamp"
    )
    n_fallbacks = sum(
        1 for d in dismissals if d.get("issue") == "started_inferred_fallback"
    )
    n_mismatches = sum(
        1 for d in dismissals if d.get("issue") == "starter_count_mismatch"
    )
    print(
        f"\nDone. Rows: {len(df):,}  Failed matches: {len(failed_ids)}\n"
        f"Quality report: {n_dismissed} dismissal(s), "
        f"{n_missing_ts} missing-timestamp dismissal(s), "
        f"{n_fallbacks} starter fallback(s), "
        f"{n_mismatches} starter mismatch(es)"
    )
