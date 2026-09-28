"""
build_player_season.py — aggregate player-match rows into one row per player
per team per season.

Public API
----------
aggregate_player_season(df, min_minutes) -> pl.DataFrame
    Pure function: all aggregation logic.  No I/O.  Unit-testable directly.

build_player_season(input_path, output_path, min_minutes) -> pl.DataFrame
    Thin I/O wrapper: read parquet → aggregate → write parquet.

Design notes
------------
- Per-90 values are computed from season totals, never averaged from
  match-level per-90 values (ADR 002 §6a).
- Season totals, minutes, and per-90 values are stored side-by-side (ADR 002 §6b).
- Per-group season minutes are summed from the match-level minutes_{group}
  columns (exact, no approximation from per-match primary group).
- Season position_group = argmax of per-group season minute totals.
- Rows are never dropped; meets_min_minutes flags eligibility (ADR 002 §5).
- GK rows are kept but is_gk=True marks them ineligible for DNA/similarity/gems
  in V1 (ADR 002 §12).
- One row per (player, team, season); is_primary_stint marks the team with the
  most minutes for cross-team comparisons (ADR 002 §6).
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from eleveniq.config import MIN_MINUTES_THRESHOLD, MIN_AERIAL_CONTESTS
from eleveniq.metrics.positions import POSITION_GROUP_COLUMNS

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
INPUT_PATH = _PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
OUTPUT_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"

_GROUP_KEY = [
    "player_id", "player_name",
    "team_id", "team_name",
    "competition_id", "season_id",
    "source",
]


# Columns that get a per-90 companion. Mostly integer counts but also includes
# the Float64 metrics xg_assisted and xg_assisted_direct.
_COUNT_COLS: list[str] = [
    "goals", "shots", "shots_on_target",
    "passes_attempted", "passes_completed",
    "progressive_passes", "key_passes",
    "carries", "progressive_carries",
    "tackles_attempted", "tackles_won",
    "dribbles_attempted", "dribbles_completed",
    "interceptions", "clearances", "blocks",
    "fouls_committed", "fouls_won",
    "assists",
    "crosses_attempted",
    "dispossessed",
    "miscontrols",
    "pressures",
    "ball_recoveries",
    "dribbled_past",
    "aerials_won",
    "aerials_lost",
    "long_passes_attempted",
    "switches",
    "xg_assisted",
    "xg_assisted_direct",
    "final_third_entries_pass",
    "final_third_entries_carry",
    "box_entries_pass",
    "box_entries_carry",
]

# Columns that are summed directly but do NOT get per-90 values.
_DISPLAY_ONLY_COLS: list[str] = [
    "shots_missing_xg",
    "passes_unknown_outcome",
    "crosses_completed",
    "long_passes_completed",
    "passes_missing_length",
    "ball_recoveries_failed",
]


def _season_position_group(df: pl.DataFrame) -> pl.Series:
    """
    Return a Series with the season position group for each row, using the
    summed per-group minute columns.  Tie-break: alphabetically first group
    name (same rule as primary_position_group in positions.py).
    """
    # POSITION_GROUP_COLUMNS is alphabetically ordered; the first key is the
    # default and is overridden only by a strictly higher minute total —
    # so a tie resolves to the alphabetically first group.
    groups = list(POSITION_GROUP_COLUMNS.keys())
    result = pl.lit(groups[0])
    best = pl.col(POSITION_GROUP_COLUMNS[groups[0]])

    for group in groups[1:]:
        col = POSITION_GROUP_COLUMNS[group]
        result = pl.when(pl.col(col) > best).then(pl.lit(group)).otherwise(result)
        best = pl.when(pl.col(col) > best).then(pl.col(col)).otherwise(best)

    return df.select(result.alias("position_group"))["position_group"]


def aggregate_player_season(
    df: pl.DataFrame,
    min_minutes: int = MIN_MINUTES_THRESHOLD,
) -> pl.DataFrame:
    """
    Aggregate player-match rows into season totals, per-90 metrics, and
    eligibility flags.  Pure function with no I/O.

    Args:
        df: player_match_stats DataFrame (one row per player per match).
        min_minutes: minimum minutes threshold for meets_min_minutes flag.

    Returns:
        player_season_stats DataFrame (one row per player per team per season).
    """
    # ------------------------------------------------------------------
    # Step 1: Group-by aggregate
    # ------------------------------------------------------------------
    agg_exprs: list[pl.Expr] = [
        pl.len().cast(pl.Int32).alias("appearances"),
        pl.col("started").sum().cast(pl.Int32).alias("starts"),
        pl.col("started_inferred").sum().cast(pl.Int32).alias("started_inferred_count"),
        pl.col("minutes_played").sum().alias("minutes_played"),
        pl.col("xg").sum().alias("xg"),
    ]
    for col in _COUNT_COLS + _DISPLAY_ONLY_COLS:
        agg_exprs.append(pl.col(col).sum())
    for col in POSITION_GROUP_COLUMNS.values():
        agg_exprs.append(pl.col(col).sum())

    season = df.group_by(_GROUP_KEY).agg(agg_exprs)

    # ------------------------------------------------------------------
    # Step 2: Season position group from exact per-group minute totals
    # ------------------------------------------------------------------
    season = season.with_columns(
        _season_position_group(season),
    )

    # ------------------------------------------------------------------
    # Step 3: Per-90 columns (null when minutes_played == 0)
    # ------------------------------------------------------------------
    p90_exprs = [
        pl.when(pl.col("minutes_played") > 0)
        .then(pl.col(c).cast(pl.Float64) / pl.col("minutes_played") * 90)
        .otherwise(None)
        .alias(f"{c}_p90")
        for c in _COUNT_COLS
    ]
    # xg_p90 separately (xg is already Float64)
    p90_exprs.append(
        pl.when(pl.col("minutes_played") > 0)
        .then(pl.col("xg") / pl.col("minutes_played") * 90)
        .otherwise(None)
        .alias("xg_p90")
    )
    season = season.with_columns(p90_exprs)

    # ------------------------------------------------------------------
    # Step 4: Derived ratio columns (null when denominator == 0)
    # ------------------------------------------------------------------
    known_attempts = pl.col("passes_attempted") - pl.col("passes_unknown_outcome")
    season = season.with_columns([
        pl.when(known_attempts > 0)
        .then(pl.col("passes_completed").cast(pl.Float64) / known_attempts.cast(pl.Float64))
        .otherwise(None)
        .alias("pass_completion_pct"),

        pl.when(pl.col("shots") > 0)
        .then(pl.col("xg") / pl.col("shots").cast(pl.Float64))
        .otherwise(None)
        .alias("xg_per_shot"),

        pl.when(pl.col("dribbles_attempted") > 0)
        .then(pl.col("dribbles_completed").cast(pl.Float64) / pl.col("dribbles_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("dribble_success_pct"),

        pl.when(pl.col("tackles_attempted") > 0)
        .then(pl.col("tackles_won").cast(pl.Float64) / pl.col("tackles_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("tackle_success_pct"),

        pl.when(pl.col("crosses_attempted") > 0)
        .then(pl.col("crosses_completed").cast(pl.Float64) / pl.col("crosses_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("cross_completion_pct"),

        pl.when((pl.col("aerials_won") + pl.col("aerials_lost")) >= MIN_AERIAL_CONTESTS)
        .then(pl.col("aerials_won").cast(pl.Float64) / (pl.col("aerials_won") + pl.col("aerials_lost")).cast(pl.Float64))
        .otherwise(None)
        .alias("aerial_win_pct"),

        pl.when(pl.col("long_passes_attempted") > 0)
        .then(pl.col("long_passes_completed").cast(pl.Float64) / pl.col("long_passes_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("long_pass_completion_pct"),
    ])

    # ------------------------------------------------------------------
    # Step 5: Eligibility flags
    # ------------------------------------------------------------------
    season = season.with_columns([
        (pl.col("minutes_played") >= min_minutes).alias("meets_min_minutes"),
        (pl.col("position_group") == "GK").alias("is_gk"),
    ])

    # ------------------------------------------------------------------
    # Step 6: Multi-team flags and player_season_minutes_total
    # ------------------------------------------------------------------
    # "source" is included so that the same player_id appearing in two sources
    # for the same season is never treated as a multi-team stint.
    _player_season_key = ["player_id", "competition_id", "season_id", "source"]

    # Count how many team rows each player has in the same season.
    team_counts = (
        season.group_by(_player_season_key)
        .agg(pl.len().alias("_team_count"))
    )
    season = season.join(team_counts, on=_player_season_key, how="left")
    season = season.with_columns(
        (pl.col("_team_count") > 1).alias("multi_team_season")
    ).drop("_team_count")

    # player_season_minutes_total: total minutes across all team stints.
    total_minutes = (
        season.group_by(_player_season_key)
        .agg(pl.col("minutes_played").sum().alias("player_season_minutes_total"))
    )
    season = season.join(total_minutes, on=_player_season_key, how="left")

    # is_primary_stint: True for the row with the most minutes per player-season.
    # Tie-break: lower team_id wins.
    # Strategy: two deterministic aggregations — no reliance on row order.
    _max_minutes = (
        season.group_by(_player_season_key)
        .agg(pl.col("minutes_played").max().alias("_max_minutes"))
    )
    season = season.join(_max_minutes, on=_player_season_key, how="left")
    _primary_team = (
        season.filter(pl.col("minutes_played") == pl.col("_max_minutes"))
        .group_by(_player_season_key)
        .agg(pl.col("team_id").min().alias("_primary_team_id"))
    )
    season = season.join(_primary_team, on=_player_season_key, how="left")
    season = season.with_columns(
        ((pl.col("minutes_played") == pl.col("_max_minutes")) &
         (pl.col("team_id") == pl.col("_primary_team_id"))).alias("is_primary_stint")
    ).drop(["_max_minutes", "_primary_team_id"])

    return season


def build_player_season(
    input_path: Path | None = None,
    output_path: Path | None = None,
    min_minutes: int = MIN_MINUTES_THRESHOLD,
) -> pl.DataFrame:
    """
    Read player_match_stats.parquet, aggregate to season level, write output.

    Returns the season DataFrame.
    """
    in_path = input_path or INPUT_PATH
    out_path = output_path or OUTPUT_PATH

    match_df = pl.read_parquet(in_path)
    season_df = aggregate_player_season(match_df, min_minutes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    season_df.write_parquet(out_path)
    multi_team_count = int(season_df["multi_team_season"].sum())
    logger.info(
        "Wrote %d rows (%d multi-team stints) → %s",
        len(season_df),
        multi_team_count,
        out_path,
    )
    return season_df


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
        stream=sys.stdout,
    )

    season_df = build_player_season()

    match_df = pl.read_parquet(INPUT_PATH)

    multi_team = season_df.filter(pl.col("multi_team_season"))["player_name"].unique().len()
    print(f"\nRows: {len(season_df):,}  Players with multi-team season: {multi_team}")

    # Players meeting 600 and 900 min per position group
    print("\n--- Players meeting minimum-minutes thresholds per position group ---")
    for threshold in (600, 900):
        counts = (
            season_df.filter(pl.col("minutes_played") >= threshold)
            .group_by("position_group")
            .agg(pl.len().alias("players"))
            .sort("position_group")
        )
        print(f"\n  >= {threshold} min:")
        for row in counts.iter_rows(named=True):
            print(f"    {row['position_group']:12s}  {row['players']}")

    # Top 5 progressive passes per 90 (outfield, 900+ min, primary stint)
    print("\n--- Top 5 progressive passes p90 (outfield, 900+ min, primary stint) ---")
    top5 = (
        season_df.filter(
            ~pl.col("is_gk")
            & pl.col("meets_min_minutes")
            & pl.col("is_primary_stint")
        )
        .sort("progressive_passes_p90", descending=True)
        .head(5)
        .select(["player_name", "team_name", "position_group",
                 "minutes_played", "progressive_passes", "progressive_passes_p90"])
    )
    for row in top5.iter_rows(named=True):
        print(
            f"  {row['player_name']:<30s}  {row['team_name']:<25s}"
            f"  {row['position_group']:<12s}"
            f"  {row['minutes_played']:6.0f} min"
            f"  {row['progressive_passes']:4d} prog  "
            f"  {row['progressive_passes_p90']:.2f}/90"
        )

    # Reconciliation: season totals vs match totals
    print("\n--- Reconciliation: season totals vs match totals ---")
    for col in ("goals", "minutes_played", "passes_attempted", "shots"):
        match_total = match_df[col].sum()
        season_total = season_df[col].sum()
        ok = abs(match_total - season_total) < 0.01
        status = "OK" if ok else "MISMATCH"
        print(
            f"  {col:<20s}  match={match_total:10,.2f}  "
            f"season={season_total:10,.2f}  [{status}]"
        )
