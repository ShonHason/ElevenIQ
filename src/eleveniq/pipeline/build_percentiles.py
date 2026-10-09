"""
Season percentiles: data/processed/player_season_stats.parquet →
data/processed/player_season_percentiles.parquet (plan 005 M4d, ADR 004 §11).

One output row per season row × registry metric × cohort role, in long format:
- key: player_id, team_id, competition_id, season_id, source;
- position_group (the group scored in), cohort_role ("primary", or "second" for a
  gated ambiguous row's D5 second group), metric, value (the stored raw value);
- percentile_status (D4 row status), pct_local / n_local / status_local and
  pct_global / n_global / status_global;
- basis and registry_status (the registry cell; null for a group without one, GK);
- provenance: registry_version, measurement_source, min_cohort_size,
  min_minutes_threshold, season_parquet_sha256, polars_version (the file's bytes depend
  on the writer, so a sha256 change after a polars upgrade is explainable).

Cell status per scope, first match: the row status (unscored) → no_signal /
no_min_attempts → below_min_attempts → null_value → cohort_too_small → (global only)
d9_undefined / league_inconsistent → ok. Only ok carries a percentile; n is stored
whenever the row is eligible. The rows are sorted by (key, cohort_role, metric), so the
same inputs write the same bytes.

Usage: python -m eleveniq.pipeline.build_percentiles
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics.percentiles import score_cell
from eleveniq.analytics.registry import REGISTRY, SPECS, Derivation, MetricSpec, load_measurements
from eleveniq.metrics.positions import OUTFIELD_GROUPS

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
INPUT_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
OUTPUT_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_percentiles.parquet"

PRIMARY = "primary"
SECOND = "second"
COHORT_TOO_SMALL = "cohort_too_small"
D9_UNDEFINED = "d9_undefined"
LEAGUE_INCONSISTENT = "league_inconsistent"
OK = "ok"

SORT_KEY: tuple[str, ...] = (*C.SEASON_KEY, "cohort_role", "metric")
OUTPUT_SCHEMA: dict[str, pl.DataType] = {
    "player_id": pl.Int64, "team_id": pl.Int64, "competition_id": pl.Int64,
    "season_id": pl.Int64, "source": pl.String,
    "position_group": pl.String, "cohort_role": pl.String, "metric": pl.String,
    "value": pl.Float64, "percentile_status": pl.String,
    "pct_local": pl.Float64, "n_local": pl.Int64, "status_local": pl.String,
    "pct_global": pl.Float64, "n_global": pl.Int64, "status_global": pl.String,
    "basis": pl.String, "registry_status": pl.String,
    "registry_version": pl.String, "measurement_source": pl.String,
    "min_cohort_size": pl.Int64, "min_minutes_threshold": pl.Float64,
    "season_parquet_sha256": pl.String, "polars_version": pl.String,
}


def cohort_targets(season: pl.DataFrame) -> pl.DataFrame:
    """Every season row once as "primary" (its position_group), plus a "second" row for
    each scored ambiguous row with a second group (ADR 004 D5)."""
    season = season.with_columns(C.row_status().alias("_status"))
    primary = season.with_columns(pl.lit(PRIMARY).alias("cohort_role"),
                                  pl.col("position_group").alias("_group"))
    second = season.filter((pl.col("_status") == C.SCORED)
                           & pl.col("position_group_ambiguous").fill_null(False)
                           & pl.col("second_position_group").is_not_null())
    bad = second.filter(~pl.col("second_position_group").is_in(sorted(OUTFIELD_GROUPS)))
    if bad.height:
        raise C.CohortInputError(f"D5 second group is not outfield: "
                                 f"{bad.select(*C.SEASON_KEY, 'second_position_group').rows()}")
    second = second.with_columns(pl.lit(SECOND).alias("cohort_role"),
                                 pl.col("second_position_group").alias("_group"))
    return pl.concat([primary, second])


def _null_f64(n: int) -> pl.Series:
    return pl.Series([None] * n, dtype=pl.Float64)


def _score_group(targets: pl.DataFrame, members: pl.DataFrame, spec: MetricSpec, cell: Any,
                 min_cohort_size: int) -> pl.DataFrame:
    """The scored targets of one metric × group, with the local and global cells."""
    col = spec.column
    t = targets.with_columns(C.eligibility(spec, cell).alias("_elig"))

    gpop = C.reference_population(members, spec, cell)
    gpct, gn = score_cell(gpop[col], t[col], min_cohort_size)
    t = t.with_columns((gpct if gpct is not None else _null_f64(t.height)).alias("_pct_g"),
                       pl.lit(gn, dtype=pl.Int64).alias("_n_g"))

    parts = []
    for comp in sorted(t["competition_id"].unique().to_list()):
        sub = t.filter(pl.col("competition_id") == comp)
        lpop = C.reference_population(members, spec, cell, comp)
        lpct, ln = score_cell(lpop[col], sub[col], min_cohort_size)
        parts.append(sub.with_columns(
            (lpct if lpct is not None else _null_f64(sub.height)).alias("_pct_l"),
            pl.lit(ln, dtype=pl.Int64).alias("_n_l")))
    t = pl.concat(parts) if parts else t.with_columns(
        _null_f64(0).alias("_pct_l"), pl.lit(None, dtype=pl.Int64).alias("_n_l"))

    elig_ok = pl.col("_elig") == C.ELIGIBLE
    status_local = (pl.when(~elig_ok).then(pl.col("_elig"))
                    .when(pl.col("_n_l") < min_cohort_size).then(pl.lit(COHORT_TOO_SMALL))
                    .otherwise(pl.lit(OK)))
    global_d9 = (pl.lit(D9_UNDEFINED) if cell.global_ok is None
                 else pl.lit(OK) if cell.global_ok else pl.lit(LEAGUE_INCONSISTENT))
    status_global = (pl.when(~elig_ok).then(pl.col("_elig"))
                     .when(pl.col("_n_g") < min_cohort_size).then(pl.lit(COHORT_TOO_SMALL))
                     .otherwise(global_d9))
    return t.with_columns(status_local.alias("status_local"),
                          status_global.alias("status_global")).with_columns(
        pl.when(pl.col("status_local") == OK).then(pl.col("_pct_l")).alias("pct_local"),
        pl.when(elig_ok).then(pl.col("_n_l")).alias("n_local"),
        pl.when(pl.col("status_global") == OK).then(pl.col("_pct_g")).alias("pct_global"),
        pl.when(elig_ok).then(pl.col("_n_g")).alias("n_global"),
        pl.lit(cell.basis).alias("basis"),
        pl.lit(cell.status).alias("registry_status"),
    )


def _metric_rows(targets: pl.DataFrame, members_by_group: Mapping[str, pl.DataFrame],
                 spec: MetricSpec, derivation: Derivation, min_cohort_size: int) -> pl.DataFrame:
    col = spec.column
    scored = targets.filter(pl.col("_status") == C.SCORED)
    outside = scored.filter(~pl.col("_group").is_in(list(spec.applies_to)))
    if outside.height:
        raise C.CohortInputError(f"{col}: scored rows in groups outside applies_to "
                                 f"{sorted(set(outside['_group'].to_list()))} (ADR 004 D21)")
    parts = [_score_group(scored.filter(pl.col("_group") == g), members_by_group[g], spec,
                          derivation.cell(col, g), min_cohort_size)
             for g in spec.applies_to if scored.filter(pl.col("_group") == g).height]

    cells = {g: derivation.cell(col, g) for g in spec.applies_to}
    unscored = targets.filter(pl.col("_status") != C.SCORED).with_columns(
        pl.col("_status").alias("status_local"), pl.col("_status").alias("status_global"),
        pl.lit(None, dtype=pl.Float64).alias("pct_local"), pl.lit(None, dtype=pl.Int64).alias("n_local"),
        pl.lit(None, dtype=pl.Float64).alias("pct_global"), pl.lit(None, dtype=pl.Int64).alias("n_global"),
        pl.col("_group").replace_strict({g: c.basis for g, c in cells.items()}, default=None,
                                        return_dtype=pl.String).alias("basis"),
        pl.col("_group").replace_strict({g: c.status for g, c in cells.items()}, default=None,
                                        return_dtype=pl.String).alias("registry_status"),
    )
    out_cols = ["status_local", "status_global", "pct_local", "n_local", "pct_global", "n_global",
                "basis", "registry_status"]
    keep = [*C.SEASON_KEY, "_group", "cohort_role", "_status", col, *out_cols]
    return pl.concat([p.select(keep) for p in [*parts, unscored]]).with_columns(
        pl.lit(col).alias("metric"), pl.col(col).cast(pl.Float64).alias("value")).drop(col)


def percentile_table(season: pl.DataFrame, derivation: Derivation, *, season_sha256: str,
                     specs: Sequence[MetricSpec] = SPECS,
                     min_cohort_size: int = config.MIN_COHORT_SIZE) -> pl.DataFrame:
    """The long percentile table for `season` (already checked by cohorts.check_inputs)."""
    targets = cohort_targets(season)
    groups = sorted({g for s in specs for g in s.applies_to})
    members = {g: C.scored_members(targets.filter(pl.col("cohort_role") == PRIMARY), g)
               for g in groups}
    rows = pl.concat([_metric_rows(targets, members, s, derivation, min_cohort_size) for s in specs])
    out = rows.rename({"_group": "position_group", "_status": "percentile_status"}).with_columns(
        pl.lit(derivation.version).alias("registry_version"),
        pl.lit(derivation.source).alias("measurement_source"),
        pl.lit(min_cohort_size).alias("min_cohort_size"),
        pl.lit(float(config.MIN_MINUTES_THRESHOLD)).alias("min_minutes_threshold"),
        pl.lit(season_sha256).alias("season_parquet_sha256"),
        pl.lit(pl.__version__).alias("polars_version"),
    )
    out = out.select([pl.col(c).cast(t) for c, t in OUTPUT_SCHEMA.items()])
    return out.sort(list(SORT_KEY), maintain_order=True)


def build_percentiles(season_path: Path = INPUT_PATH, out_path: Path = OUTPUT_PATH,
                      derivation: Derivation = REGISTRY,
                      measurements: Mapping[str, Any] | None = None) -> pl.DataFrame:
    """Check the inputs against the registry, build the table and write it."""
    meas = load_measurements() if measurements is None else measurements
    season = pl.read_parquet(season_path)
    sha = C.file_sha256(season_path)
    C.check_inputs(season, source=derivation.source, season_sha256=sha,
                   expected_sha256=meas["provenance"]["season_parquet_sha256"])
    table = percentile_table(season, derivation, season_sha256=sha)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.write_parquet(out_path, compression="zstd", statistics=True)
    counts = table.group_by("status_global").len().sort("status_global")
    logger.info("Wrote %d rows (%d season rows × %d metrics, %d second-group rows) → %s",
                table.height, season.height, table["metric"].n_unique(),
                table.filter(pl.col("cohort_role") == SECOND).select(C.SEASON_KEY).unique().height,
                out_path)
    logger.info("status_global: %s", dict(counts.iter_rows()))
    return table


if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--season", type=Path, default=INPUT_PATH)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s", stream=sys.stdout)
    build_percentiles(args.season, args.out)
