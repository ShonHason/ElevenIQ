"""
build_player_season.py — aggregate player-match rows into one row per player
per team per season.

Public API
----------
aggregate_player_season(df, min_minutes, competition_seasons) -> pl.DataFrame
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
- Output rows are sorted by the unique key (player_id, team_id, competition_id,
  season_id, source), so the season file is deterministic row by row.
- GK rows are kept but is_gk=True marks them ineligible for DNA/similarity/gems
  in V1 (ADR 002 §12).
- One row per (player, team, competition, season). multi_team_season,
  is_primary_stint and player_season_minutes_total are within-competition:
  "player-season" there means player-competition-season (ADR 002 §6, §29).
  is_primary_stint is NOT unique per (player_id, season_id, source) for a
  player who played in two leagues in one season.
- in_percentile_pool comes from the configured coverage class of the row's
  (competition_id, season_id, source); multi_competition_season marks a player
  seen in more than one competition in the same (season_id, source). Rows stay
  separate (ADR 003 §5, §7).
- Rows are keyed by ids only. StatsBomb spells some names differently from
  match to match, so player_name / team_name are display attributes: outer
  whitespace and known quoting artifacts are normalised, then the most frequent spelling per id
  wins, ties broken by the alphabetically first spelling. Every raw player
  spelling is kept in player_name_variants (plan 003 C3, ADR 002).
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from eleveniq.config import (
    COMPETITION_SEASONS,
    AMBIGUOUS_POSITION_MARGIN,
    MIN_AERIAL_CONTESTS,
    MIN_MINUTES_THRESHOLD,
    MIN_MINUTES_TOLERANCE,
    CompetitionSeason,
    validate_competition_seasons,
)
from eleveniq.pipeline.coverage import (
    check_configured_coverage,
    check_processed_match_ids,
    check_season_ids_unique,
)
from eleveniq.statsbomb.loader import load_competitions
from eleveniq.metrics.positions import (
    GK_GROUP,
    OUTFIELD_GROUPS,
    POSITION_GROUP_COLUMNS,
    UNKNOWN_POSITION_GROUP,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
INPUT_PATH = _PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
OUTPUT_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"

_GROUP_KEY = [
    "player_id",
    "team_id",
    "competition_id", "season_id",
    "source",
]


# Columns that get a per-90 companion. Mostly integer counts but also includes
# the Float64 metrics xg_assisted, xg_assisted_direct, np_xg, op_xg_assisted and
# restart_xg_assisted.
_COUNT_COLS: list[str] = [
    "goals", "shots", "shots_on_target",
    "np_shots", "np_xg",  # ADR 004 D15
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
    # ADR 004 §9: open play vs restart (by the key pass's own type).
    "op_xg_assisted", "restart_xg_assisted",
    "op_key_passes", "restart_key_passes",
    "op_assists", "restart_assists",
]

# Columns that are summed directly but do NOT get per-90 values.
_DISPLAY_ONLY_COLS: list[str] = [
    "shots_missing_xg",
    "passes_unknown_outcome",
    "crosses_completed",
    "crosses_unknown_outcome",
    "long_passes_completed",
    "long_passes_unknown_outcome",
    "long_passes_injury_clearance",
    "passes_injury_clearance",  # ADR 004 D13
    "open_play_passes_attempted",  # ADR 004 D12
    "team_open_play_passes_while_on",  # ADR 004 D12
    "passes_missing_length",
    "ball_recoveries_failed",
]


def _position_margin_columns(season: pl.DataFrame) -> pl.DataFrame:
    """
    ADR 004 D5: second_position_group, position_group_share,
    position_group_margin_pct and position_group_ambiguous.

    The second group is the group with the most minutes after position_group,
    among GK and the outfield groups (UNKNOWN is not a group: its minutes fail the
    build). Ties go to the alphabetically first group, as for position_group. For
    an outfield row GK is never the second group. It is null when its minutes are
    0, and the margin is then the top group's share: 1.0, unless an outfield row
    also has GK minutes (GK is never its second group). share and margin are
    shares of minutes_played.
    """
    candidates = [g for g in POSITION_GROUP_COLUMNS if g != UNKNOWN_POSITION_GROUP]
    second = pl.lit(None, dtype=pl.Utf8)
    best = pl.lit(0.0)
    for group in candidates:  # alphabetical: only a strictly larger total overrides
        col = pl.col(POSITION_GROUP_COLUMNS[group])
        eligible = pl.col("position_group") != group
        if group == GK_GROUP:
            eligible = eligible & pl.col("position_group").is_in(sorted(OUTFIELD_GROUPS)).not_()
        better = eligible & (col > best)
        second = pl.when(better).then(pl.lit(group)).otherwise(second)
        best = pl.when(better).then(col).otherwise(best)
    top = pl.lit(0.0)
    for group in candidates:
        top = pl.when(pl.col("position_group") == group).then(
            pl.col(POSITION_GROUP_COLUMNS[group])).otherwise(top)
    minutes = pl.col("minutes_played")
    season = season.with_columns(
        second.alias("second_position_group"),
        pl.when(minutes > 0).then(top / minutes).otherwise(None).alias("position_group_share"),
        pl.when(minutes > 0).then((top - best) / minutes).otherwise(None)
        .alias("position_group_margin_pct"),
    )
    return season.with_columns(
        (pl.col("position_group_margin_pct") < AMBIGUOUS_POSITION_MARGIN)
        .fill_null(False).alias("position_group_ambiguous"))


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


# Known StatsBomb quoting artifacts, applied in order when choosing a display
# name: a doubled apostrophe (N''Golo) and a double quote inside a name
# (N"Gog) both stand for a single apostrophe.
_NAME_QUOTE_FIXES: list[tuple[str, str]] = [("''", "'"), ('"', "'")]


def _normalise_name(name: pl.Expr) -> pl.Expr:
    """
    Strip leading/trailing whitespace (incl. tabs), then apply
    _NAME_QUOTE_FIXES to a name expression (display use only).
    """
    name = name.str.strip_chars()
    for old, new in _NAME_QUOTE_FIXES:
        name = name.str.replace_all(old, new, literal=True)
    return name


def _canonical_names(df: pl.DataFrame, id_col: str, name_col: str) -> pl.DataFrame:
    """
    Return one row per (id_col, source): the canonical name_col and a sorted
    list of every raw spelling seen (``name_variants``).

    Canonical = spellings are first normalised (_normalise_name), then the
    normalised spelling on the most match rows wins; ties resolve to the
    alphabetically first spelling so the result never depends on row order.
    """
    key = [id_col, "source"]
    counts = (
        df.filter(pl.col(name_col).is_not_null())
        .group_by(key + [name_col])
        .agg(pl.len().alias("_n"))
    )
    canonical = (
        counts.with_columns(_normalise_name(pl.col(name_col)))
        .group_by(key + [name_col])
        .agg(pl.col("_n").sum())
        .sort(key + ["_n", name_col], descending=[False, False, True, False])
        .unique(subset=key, keep="first", maintain_order=True)
        .select(key + [name_col])
    )
    variants = counts.group_by(key).agg(
        pl.col(name_col).sort().alias("name_variants")
    )
    return canonical.join(variants, on=key, how="left")


def find_name_variants(df: pl.DataFrame) -> pl.DataFrame:
    """
    Data-quality check: every player_id / team_id spelled more than one way.

    Returns columns: entity ("player" | "team"), id, source, names (sorted
    list of spellings), with one row per id that has more than one name.
    """
    frames = []
    for entity, id_col, name_col in (
        ("player", "player_id", "player_name"),
        ("team", "team_id", "team_name"),
    ):
        names = _canonical_names(df, id_col, name_col)
        frames.append(
            names.filter(pl.col("name_variants").list.len() > 1).select(
                pl.lit(entity).alias("entity"),
                pl.col(id_col).cast(pl.Int64).alias("id"),
                pl.col("source"),
                pl.col("name_variants").alias("names"),
            )
        )
    return pl.concat(frames).sort(["entity", "source", "id"])


_COMP_SEASON_KEY = ["competition_id", "season_id", "source"]


def _pool_flags(competition_seasons: tuple[CompetitionSeason, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "competition_id": [cs.competition_id for cs in competition_seasons],
            "season_id": [cs.season_id for cs in competition_seasons],
            "source": [cs.source for cs in competition_seasons],
            "in_percentile_pool": [cs.coverage == "FULL" for cs in competition_seasons],
        },
        schema={
            "competition_id": pl.Int64, "season_id": pl.Int64,
            "source": pl.String, "in_percentile_pool": pl.Boolean,
        },
    )


def find_teams_in_multiple_competitions(season: pl.DataFrame) -> pl.DataFrame:
    """
    Data-quality check: team_ids that appear in more than one competition in the
    same (season_id, source). Every configured competition is a league (ADR 003
    §2), so a team should play in only one; rows returned here would make
    multi_competition_season fire for a player who never changed club. Once cup
    competitions are ingested this check must be scoped to leagues.
    Covers (player_id, team_id) too: a player's team rows share the team's
    competition.
    """
    return (
        season.group_by(["team_id", "season_id", "source"])
        .agg(pl.col("competition_id").unique().sort().alias("competition_ids"))
        .filter(pl.col("competition_ids").list.len() > 1)
        .sort(["source", "season_id", "team_id"])
    )


def aggregate_player_season(
    df: pl.DataFrame,
    min_minutes: int = MIN_MINUTES_THRESHOLD,
    competition_seasons: tuple[CompetitionSeason, ...] = COMPETITION_SEASONS,
) -> pl.DataFrame:
    """
    Aggregate player-match rows into season totals, per-90 metrics, and
    eligibility flags.  Pure function with no I/O.

    Args:
        df: player_match_stats DataFrame (one row per player per match).
        min_minutes: minimum nominal-equivalent minutes for meets_min_minutes
            (ADR 004 D1: minutes_nominal_equiv >= min_minutes - MIN_MINUTES_TOLERANCE).
        competition_seasons: configured competition-seasons; their coverage
            class sets in_percentile_pool. A (competition_id, season_id, source)
            in df but not configured raises ValueError.

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
        # ADR 004 D1: exposure on the nominal clock, for the minutes gate.
        (pl.col("minutes_played") * pl.col("match_nominal_duration") / pl.col("match_duration"))
        .sum().alias("minutes_nominal_equiv"),
        pl.col("xg").sum().alias("xg"),
    ]
    for col in _COUNT_COLS + _DISPLAY_ONLY_COLS:
        agg_exprs.append(pl.col(col).sum())
    for col in POSITION_GROUP_COLUMNS.values():
        agg_exprs.append(pl.col(col).sum())

    season = df.group_by(_GROUP_KEY).agg(agg_exprs)

    # Display names are attached after grouping so a spelling change never
    # splits a player's or team's season into two rows.
    player_names = _canonical_names(df, "player_id", "player_name").rename(
        {"name_variants": "player_name_variants"}
    )
    team_names = _canonical_names(df, "team_id", "team_name").drop("name_variants")
    season = (
        season.join(player_names, on=["player_id", "source"], how="left")
        .join(team_names, on=["team_id", "source"], how="left")
    )

    # ------------------------------------------------------------------
    # Step 2: Season position group from exact per-group minute totals
    # ------------------------------------------------------------------
    season = season.with_columns(
        _season_position_group(season),
    )
    season = _position_margin_columns(season)

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
    # Unknown outcomes and Injury Clearances are neither completed nor failed
    # attempts: both leave the completion denominators (ADR 002 §14, ADR 004 D13).
    known_attempts = (pl.col("passes_attempted") - pl.col("passes_unknown_outcome")
                      - pl.col("passes_injury_clearance"))
    known_crosses = pl.col("crosses_attempted") - pl.col("crosses_unknown_outcome")
    known_long_passes = (pl.col("long_passes_attempted") - pl.col("long_passes_unknown_outcome")
                         - pl.col("long_passes_injury_clearance"))
    season = season.with_columns([
        pl.when(known_attempts > 0)
        .then(pl.col("passes_completed").cast(pl.Float64) / known_attempts.cast(pl.Float64))
        .otherwise(None)
        .alias("pass_completion_pct"),

        pl.when(pl.col("shots") > 0)
        .then(pl.col("xg") / pl.col("shots").cast(pl.Float64))
        .otherwise(None)
        .alias("xg_per_shot"),

        # ADR 004 D15.
        pl.when(pl.col("np_shots") > 0)
        .then(pl.col("np_xg") / pl.col("np_shots").cast(pl.Float64))
        .otherwise(None)
        .alias("np_xg_per_shot"),

        # ADR 004 D12: share of the team's open-play passes while he was on.
        pl.when(pl.col("team_open_play_passes_while_on") > 0)
        .then(pl.col("open_play_passes_attempted").cast(pl.Float64)
              / pl.col("team_open_play_passes_while_on").cast(pl.Float64))
        .otherwise(None)
        .alias("team_pass_share"),

        pl.when(pl.col("dribbles_attempted") > 0)
        .then(pl.col("dribbles_completed").cast(pl.Float64) / pl.col("dribbles_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("dribble_success_pct"),

        pl.when(pl.col("tackles_attempted") > 0)
        .then(pl.col("tackles_won").cast(pl.Float64) / pl.col("tackles_attempted").cast(pl.Float64))
        .otherwise(None)
        .alias("tackle_success_pct"),

        # Unknown-outcome crosses / long passes are neither completed nor failed:
        # excluded from the denominator, like pass_completion_pct (ADR 002 §15, §19).
        pl.when(known_crosses > 0)
        .then(pl.col("crosses_completed").cast(pl.Float64) / known_crosses.cast(pl.Float64))
        .otherwise(None)
        .alias("cross_completion_pct"),

        pl.when((pl.col("aerials_won") + pl.col("aerials_lost")) >= MIN_AERIAL_CONTESTS)
        .then(pl.col("aerials_won").cast(pl.Float64) / (pl.col("aerials_won") + pl.col("aerials_lost")).cast(pl.Float64))
        .otherwise(None)
        .alias("aerial_win_pct"),

        pl.when(known_long_passes > 0)
        .then(pl.col("long_passes_completed").cast(pl.Float64) / known_long_passes.cast(pl.Float64))
        .otherwise(None)
        .alias("long_pass_completion_pct"),
    ])

    # ------------------------------------------------------------------
    # Step 5: Eligibility flags
    # ------------------------------------------------------------------
    season = season.with_columns([
        # ADR 004 D1: the gate reads nominal-equivalent minutes; per-90s keep
        # minutes_played (elapsed time).
        (pl.col("minutes_nominal_equiv") >= min_minutes - MIN_MINUTES_TOLERANCE)
        .alias("meets_min_minutes"),
        (pl.col("position_group") == "GK").alias("is_gk"),
        pl.col("position_group").is_in(sorted(OUTFIELD_GROUPS)).alias("is_outfield"),
    ])

    # ------------------------------------------------------------------
    # Step 6: Multi-team flags and player_season_minutes_total
    # ------------------------------------------------------------------
    # "source" is included so that the same player_id appearing in two sources
    # for the same season is never treated as a multi-team stint.
    _player_season_key = ["player_id", "competition_id", "season_id", "source"]

    # Count distinct teams per player-season (rows are keyed by team_id).
    team_counts = (
        season.group_by(_player_season_key)
        .agg(pl.col("team_id").n_unique().alias("_team_count"))
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

    # ------------------------------------------------------------------
    # Step 7: Pool membership and multi-competition flags (ADR 003 §5, §7)
    # ------------------------------------------------------------------
    # in_percentile_pool comes from config only. A left join plus a null check
    # fails loudly on an unconfigured competition-season instead of defaulting.
    # validate_competition_seasons makes the config keys unique, so the left
    # join cannot add rows.
    validate_competition_seasons(competition_seasons)
    season = season.join(_pool_flags(competition_seasons), on=_COMP_SEASON_KEY, how="left")
    unconfigured = season.filter(pl.col("in_percentile_pool").is_null())
    if unconfigured.height:
        keys = unconfigured.select(_COMP_SEASON_KEY).unique().sort(_COMP_SEASON_KEY).rows()
        raise ValueError(
            f"Competition-season(s) not in COMPETITION_SEASONS: {keys} "
            "(competition_id, season_id, source)"
        )

    # StatsBomb season_ids are shared across competitions (27 = 2015/2016
    # everywhere; checked at build time by check_season_ids_unique), so
    # (player_id, season_id, source) is the player's whole season. PARTIAL
    # competitions count: the flag describes the player's season, not pool
    # eligibility.
    _cross_comp_key = ["player_id", "season_id", "source"]
    comp_counts = season.group_by(_cross_comp_key).agg(
        pl.col("competition_id").n_unique().alias("_comp_count")
    )
    season = season.join(comp_counts, on=_cross_comp_key, how="left")
    season = season.with_columns(
        (pl.col("_comp_count") > 1).alias("multi_competition_season")
    ).drop("_comp_count")

    # group_by does not keep row order, so sort on the unique key: the same
    # input always gives the same frame, row by row.
    return season.sort(_GROUP_KEY)


def build_player_season(
    input_path: Path | None = None,
    output_path: Path | None = None,
    min_minutes: int = MIN_MINUTES_THRESHOLD,
    competition_seasons: tuple[CompetitionSeason, ...] = COMPETITION_SEASONS,
    data_root: Path | None = None,
) -> pl.DataFrame:
    """
    Read player_match_stats.parquet, aggregate to season level, write output.

    Fails before writing when the ADR 003 §5 coverage checks disagree with
    config, or when a team_id appears in more than one competition in a season.

    Returns the season DataFrame.
    """
    in_path = input_path or INPUT_PATH
    out_path = output_path or OUTPUT_PATH

    match_df = pl.read_parquet(in_path)

    # ADR 003 §5: config must agree with the downloaded data before the pool
    # flag is written. Each check raises CoverageCheckError on disagreement.
    check_season_ids_unique(load_competitions(data_root), competition_seasons)
    check_configured_coverage(competition_seasons, data_root)
    check_processed_match_ids(match_df, competition_seasons, data_root)

    variants = find_name_variants(match_df)
    for row in variants.iter_rows(named=True):
        logger.info(
            "Name variants: %s_id=%d (%s) → %s",
            row["entity"], row["id"], row["source"], row["names"],
        )
    logger.info(
        "%d player_ids and %d team_ids have more than one name",
        variants.filter(pl.col("entity") == "player").height,
        variants.filter(pl.col("entity") == "team").height,
    )

    season_df = aggregate_player_season(match_df, min_minutes, competition_seasons)

    shared_teams = find_teams_in_multiple_competitions(season_df)
    if shared_teams.height:
        raise ValueError(
            f"team_id(s) in more than one competition in a season: {shared_teams.rows()}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    season_df.write_parquet(out_path)
    multi_team_rows = int(season_df["multi_team_season"].sum())
    multi_team_players = season_df.filter(pl.col("multi_team_season"))["player_id"].n_unique()
    multi_comp_players = season_df.filter(pl.col("multi_competition_season"))["player_id"].n_unique()
    logger.info(
        "Wrote %d rows (%d multi-team stints, %d player_ids; %d multi-competition "
        "player_ids; %d in_percentile_pool rows) → %s",
        len(season_df),
        multi_team_rows,
        multi_team_players,
        multi_comp_players,
        int(season_df["in_percentile_pool"].sum()),
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

    multi_team = season_df.filter(pl.col("multi_team_season"))["player_id"].n_unique()
    print(f"\nRows: {len(season_df):,}  Player_ids with multi-team season: {multi_team}")

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

    # Top 5 progressive passes per 90 (outfield, 900+ min, primary stint, pool
    # rows). Cross-league movers can have one primary row per league; this
    # diagnostic does not deduplicate them (M4 decision, ADR 002 §29).
    print("\n--- Top 5 progressive passes p90 (outfield, 900+ min, primary stint, pool) ---")
    top5 = (
        season_df.filter(
            ~pl.col("is_gk")
            & pl.col("meets_min_minutes")
            & pl.col("is_primary_stint")
            & pl.col("in_percentile_pool")
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
