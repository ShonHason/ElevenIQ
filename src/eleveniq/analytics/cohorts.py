"""
Cohorts: who is scored and who is in each reference population (plan 005 M4d,
ADR 004 §11.1). This module is the only place that applies the cohort filters;
percentiles, DNA (M4f) and rarity (M6) go through it.

- Row status, first match: not_in_pool → goalkeeper → below_gate → scored (D4). Only
  scored rows (in_percentile_pool ∧ is_outfield ∧ meets_min_minutes) receive a
  percentile.
- Eligible for metric m in group g: a scored row of g whose value is non-null and, for a
  ratio, whose stored denominator is ≥ the cell's min_attempts (D19). A ratio cell
  without min_attempts has no eligible row (no_signal, or no_min_attempts when the
  ratio is unmeasured).
- Reference population for (m, g, scope): the eligible rows of g (local: and of one
  league), then the D3 dedupe within that population. Filter first, then dedupe (§10.2).
- A row is scored against a population; it need not be a member of it (a D3 outsider,
  or a D5 second-group row).

Every population is computed within MEASUREMENT_SOURCE only (CLAUDE.md); check_inputs()
refuses anything else.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from eleveniq import config
from eleveniq.analytics.registry import R_NO_SIGNAL, Cell, MetricSpec
from eleveniq.metrics.positions import GK_GROUP, OUTFIELD_GROUPS, POSITION_GROUP_COLUMNS

SEASON_KEY: tuple[str, ...] = ("player_id", "team_id", "competition_id", "season_id", "source")
# D3: a population holds at most one row per player-season-source.
PLAYER_KEY: tuple[str, ...] = ("player_id", "season_id", "source")

SCORED = "scored"
NOT_IN_POOL = "not_in_pool"
GOALKEEPER = "goalkeeper"
BELOW_GATE = "below_gate"
ELIGIBLE = "ok"
NO_SIGNAL = "no_signal"
NO_MIN_ATTEMPTS = "no_min_attempts"
BELOW_MIN_ATTEMPTS = "below_min_attempts"
NULL_VALUE = "null_value"

_OUTFIELD_MINUTES = tuple(POSITION_GROUP_COLUMNS[g] for g in sorted(OUTFIELD_GROUPS))


class CohortInputError(ValueError):
    """The season rows cannot be scored against the registry (ADR 004 §11.1 guards)."""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_inputs(season: pl.DataFrame, *, source: str, season_sha256: str,
                 expected_sha256: str) -> None:
    """Raise CohortInputError unless the rows can be scored against the registry:
    - every row's source is the registry's MEASUREMENT_SOURCE (one source per cohort);
    - the parquet is the one the registry's measurements were taken on;
    - no D6 row (GK and outfield minutes both ≥ MIXED_POSITION_MINUTES);
    - meets_min_minutes equals the D1 nominal gate, recomputed."""
    other = season.filter(pl.col("source") != source)["source"].unique().sort().to_list()
    if other or season["source"].null_count():
        raise CohortInputError(f"rows from sources other than {source!r}: {other or [None]}")
    if season_sha256 != expected_sha256:
        raise CohortInputError(
            f"season parquet sha256 {season_sha256} differs from the measurements' provenance "
            f"{expected_sha256}: re-run spikes/m4_decision_measurements.py --write-json")
    outfield = pl.sum_horizontal(*_OUTFIELD_MINUTES)
    mixed = season.filter((pl.col(POSITION_GROUP_COLUMNS[GK_GROUP]) >= config.MIXED_POSITION_MINUTES)
                          & (outfield >= config.MIXED_POSITION_MINUTES))
    if mixed.height:
        raise CohortInputError(f"D6: rows with GK and outfield minutes: "
                               f"{mixed.select(SEASON_KEY).rows()}")
    gate = pl.col("minutes_nominal_equiv") >= config.MIN_MINUTES_THRESHOLD - config.MIN_MINUTES_TOLERANCE
    stale = season.filter(gate.ne_missing(pl.col("meets_min_minutes")))
    if stale.height:
        raise CohortInputError(f"meets_min_minutes differs from the D1 gate on "
                               f"{stale.select(SEASON_KEY).rows()[:10]}")


def row_status() -> pl.Expr:
    """D4: the row's percentile_status. A null flag counts as False."""
    def flag(c: str) -> pl.Expr:
        return pl.col(c).fill_null(False)
    return (pl.when(~flag("in_percentile_pool")).then(pl.lit(NOT_IN_POOL))
            .when(~flag("is_outfield")).then(pl.lit(GOALKEEPER))
            .when(~flag("meets_min_minutes")).then(pl.lit(BELOW_GATE))
            .otherwise(pl.lit(SCORED)))


def dedupe_population(df: pl.DataFrame) -> pl.DataFrame:
    """D3: one row per (player_id, season_id, source): most minutes_played, then lowest
    competition_id, then lowest team_id. `df` must hold one population."""
    return (df.sort(["minutes_played", "competition_id", "team_id"], descending=[True, False, False])
              .unique(subset=list(PLAYER_KEY), keep="first", maintain_order=True))


def denominator(spec: MetricSpec) -> pl.Expr:
    """The stored ratio's denominator: Σ denominator_plus − Σ denominator_minus."""
    assert spec.ratio is not None
    plus = pl.sum_horizontal(*spec.ratio.denominator_plus)
    if not spec.ratio.denominator_minus:
        return plus
    return plus - pl.sum_horizontal(*spec.ratio.denominator_minus)


def eligibility(spec: MetricSpec, cell: Cell) -> pl.Expr:
    """"ok", or why a scored row of the cell's group has no percentile for this metric."""
    value_ok = (pl.when(pl.col(spec.column).is_null()).then(pl.lit(NULL_VALUE))
                .otherwise(pl.lit(ELIGIBLE)))
    if spec.unit != "ratio":
        return value_ok
    if cell.min_attempts is None:
        return pl.lit(NO_SIGNAL if R_NO_SIGNAL in cell.reasons else NO_MIN_ATTEMPTS)
    return (pl.when(denominator(spec) < cell.min_attempts).then(pl.lit(BELOW_MIN_ATTEMPTS))
            .otherwise(value_ok))


def _check_population_rows(df: pl.DataFrame, spec: MetricSpec, group: str) -> None:
    """D21: only scored outfield rows of an applicable group may reach a population."""
    if group not in spec.applies_to or group not in OUTFIELD_GROUPS:
        raise CohortInputError(f"{spec.column} does not apply to {group!r} (ADR 004 D21)")
    bad = df.filter((pl.col("_status") != SCORED) | (pl.col("position_group") != group)
                    | ~pl.col("is_outfield").fill_null(False))
    if bad.height:
        raise CohortInputError(f"{spec.column} × {group}: non-scored, non-outfield or "
                               f"other-group rows offered as a population: "
                               f"{bad.select(SEASON_KEY).rows()[:5]}")


def reference_population(members: pl.DataFrame, spec: MetricSpec, cell: Cell,
                         competition_id: int | None = None) -> pl.DataFrame:
    """The reference population of (spec, cell.group, scope): the eligible rows among
    `members` (the scored rows of the group, with `_status`), of one league when
    competition_id is given, after the D3 dedupe."""
    _check_population_rows(members, spec, cell.group)
    rows = members if competition_id is None else \
        members.filter(pl.col("competition_id") == competition_id)
    eligible = rows.filter(eligibility(spec, cell) == ELIGIBLE)
    return dedupe_population(eligible)


def scored_members(season: pl.DataFrame, group: str) -> pl.DataFrame:
    """The scored rows whose position_group is `group`: the rows populations are built from."""
    if "_status" not in season.columns:
        season = season.with_columns(row_status().alias("_status"))
    return season.filter((pl.col("_status") == SCORED) & (pl.col("position_group") == group))
