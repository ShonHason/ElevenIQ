"""
Player DNA: data/processed/player_season_stats.parquet + player_season_percentiles.parquet →
data/processed/player_dna.parquet and player_dna_dimensions.parquet (plan 005 M4f2,
ADR 004 §12, schema §12.7).

Rows: the primary scored rows (cohort_role "primary", percentile_status "scored"); D5
second-group rows never enter. pct_local and n_local are copied from the percentiles, never
re-ranked; the robust z is computed on the same local reference population
(cohorts.reference_population), whose size must equal the copied n_local.

- player_dna.parquet: one row per (season key, DNA metric), sorted by (key, metric):
  position_group, metric, family, direction, value, pct_local, n_local, metric_status,
  z, z_median, z_scale, z_scale_method, z_status, weight, dna_status.
- player_dna_dimensions.parquet: one row per (season key, DNA family of the group), sorted
  by (key, family): position_group, family, score, score_status, members, dna_status.
- Both carry the provenance: registry_version, measurement_source, dna_percentile_basis,
  season_parquet_sha256, percentiles_parquet_sha256, polars_version.

Usage: python -m eleveniq.pipeline.build_player_dna
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics.registry import REGISTRY, SPECS, Derivation, MetricSpec, load_measurements

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SEASON_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
PERCENTILES_PATH = _PROJECT_ROOT / "data" / "processed" / "player_season_percentiles.parquet"
OUT_DIR = _PROJECT_ROOT / "data" / "processed"
VECTOR_NAME = "player_dna.parquet"
DIMENSIONS_NAME = "player_dna_dimensions.parquet"
LOCAL = "local"

_KEY = {"player_id": pl.Int64, "team_id": pl.Int64, "competition_id": pl.Int64,
        "season_id": pl.Int64, "source": pl.String}
_PROVENANCE = {"registry_version": pl.String, "measurement_source": pl.String,
               "dna_percentile_basis": pl.String, "season_parquet_sha256": pl.String,
               "percentiles_parquet_sha256": pl.String, "polars_version": pl.String}
VECTOR_SCHEMA: dict[str, pl.DataType] = {
    **_KEY, "position_group": pl.String, "metric": pl.String, "family": pl.String,
    "direction": pl.String, "value": pl.Float64, "pct_local": pl.Float64, "n_local": pl.Int64,
    "metric_status": pl.String, "z": pl.Float64, "z_median": pl.Float64, "z_scale": pl.Float64,
    "z_scale_method": pl.String, "z_status": pl.String, "weight": pl.Float64,
    "dna_status": pl.String, **_PROVENANCE,
}
DIMENSIONS_SCHEMA: dict[str, pl.DataType] = {
    **_KEY, "position_group": pl.String, "family": pl.String, "score": pl.Float64,
    "score_status": pl.String, "members": pl.List(pl.String), "dna_status": pl.String,
    **_PROVENANCE,
}


class DnaInputError(ValueError):
    """The percentiles cannot be turned into DNA (stale, another source or basis)."""


def check_percentiles(pct: pl.DataFrame, derivation: Derivation, *, season_sha256: str,
                      basis: str = config.DNA_PERCENTILE_BASIS) -> None:
    """Raise DnaInputError unless the percentiles were built from this season parquet with
    this registry, within its source, and the configured basis is the one implemented."""
    if basis != LOCAL:
        raise DnaInputError(f"DNA_PERCENTILE_BASIS {basis!r}: only 'local' is implemented "
                            f"(ADR 004 §12.1)")
    for col, want in (("registry_version", derivation.version),
                      ("measurement_source", derivation.source),
                      ("season_parquet_sha256", season_sha256)):
        got = pct[col].unique().to_list()
        if got != [want]:
            raise DnaInputError(f"percentiles {col} {got} ≠ {want!r}: rebuild them with "
                                f"python -m eleveniq.pipeline.build_percentiles")


def _spreads(season: pl.DataFrame, derivation: Derivation, specs: Sequence[MetricSpec],
             groups: Sequence[str]) -> dict[tuple[str, str, int], tuple[D.Spread, int]]:
    """(metric, group, competition) -> (spread, population size) of every local population."""
    out: dict[tuple[str, str, int], tuple[D.Spread, int]] = {}
    for g in groups:
        members = C.scored_members(season, g)
        for s in D.dna_specs(derivation, specs, g):
            cell = derivation.cell(s.column, g)
            for comp in sorted(members["competition_id"].unique().to_list()):
                pop = C.reference_population(members, s, cell, comp)
                if pop.height:
                    out[(s.column, g, comp)] = (D.spread(pop[s.column]), pop.height)
    return out


def dna_rows(season: pl.DataFrame, pct: pl.DataFrame, derivation: Derivation,
             specs: Sequence[MetricSpec] = SPECS) -> dict[tuple, D.RowDna]:
    """Season key -> RowDna for every primary scored row."""
    prim = pct.filter((pl.col("cohort_role") == "primary")
                      & (pl.col("percentile_status") == C.SCORED))
    groups = sorted(prim["position_group"].unique().to_list())
    spreads = _spreads(season.with_columns(C.row_status().alias("_status")), derivation, specs,
                       groups)
    dna_cols = {g: D.dna_specs(derivation, specs, g) for g in groups}
    wanted = pl.concat([pl.DataFrame({"position_group": [g] * len(ss),
                                      "metric": [s.column for s in ss]})
                        for g, ss in dna_cols.items()])
    cells = prim.join(wanted, on=["position_group", "metric"], how="inner")
    out: dict[tuple, D.RowDna] = {}
    for (key, group), sub in _by_row(cells):
        inputs: dict[str, D.MetricInput] = {}
        for r in sub:
            sp = None
            if r["status_local"] == "ok":
                sp, n = spreads[(r["metric"], group, key[2])]
                if n != r["n_local"]:
                    raise DnaInputError(f"{key} {r['metric']}: population {n} ≠ the "
                                        f"percentiles' n_local {r['n_local']}")
            inputs[r["metric"]] = D.MetricInput(r["value"], r["pct_local"], r["n_local"],
                                                r["status_local"], sp)
        out[key] = D.row_dna(group, dna_cols[group], inputs,
                             withheld=D.withheld_for(group))
    return out


def _by_row(cells: pl.DataFrame):
    rows: dict[tuple, list[dict[str, Any]]] = {}
    groups: dict[tuple, str] = {}
    for r in cells.iter_rows(named=True):
        k = tuple(r[c] for c in C.SEASON_KEY)
        rows.setdefault(k, []).append(r)
        groups[k] = r["position_group"]
    for k in sorted(rows):
        yield (k, groups[k]), rows[k]


def dna_tables(rows: Mapping[tuple, D.RowDna], provenance: Mapping[str, str]
               ) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The two long tables, sorted and cast to their schemas."""
    vec: list[dict[str, Any]] = []
    dim: list[dict[str, Any]] = []
    for key in sorted(rows):
        row = rows[key]
        base = {**dict(zip(C.SEASON_KEY, key)), "position_group": row.group,
                "dna_status": row.dna_status, **provenance}
        for m, c in row.cells.items():
            vec.append({**base, "metric": m, "family": c.family, "direction": c.direction,
                        "value": c.value, "pct_local": c.pct_local, "n_local": c.n_local,
                        "metric_status": c.metric_status, "z": c.z, "z_median": c.z_median,
                        "z_scale": c.z_scale, "z_scale_method": c.z_scale_method,
                        "z_status": c.z_status, "weight": c.weight})
        for f, d in row.dimensions.items():
            dim.append({**base, "family": f, "score": d.score, "score_status": d.score_status,
                        "members": list(d.members)})
    v = pl.DataFrame(vec, schema=VECTOR_SCHEMA).sort([*C.SEASON_KEY, "metric"], maintain_order=True)
    d = pl.DataFrame(dim, schema=DIMENSIONS_SCHEMA).sort([*C.SEASON_KEY, "family"],
                                                         maintain_order=True)
    return v, d


def build_player_dna(season_path: Path = SEASON_PATH, percentiles_path: Path = PERCENTILES_PATH,
                     out_dir: Path = OUT_DIR, derivation: Derivation = REGISTRY,
                     measurements: Mapping[str, Any] | None = None
                     ) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Check the inputs, build both tables and write them into out_dir."""
    meas = load_measurements() if measurements is None else measurements
    season = pl.read_parquet(season_path)
    sha = C.file_sha256(season_path)
    C.check_inputs(season, source=derivation.source, season_sha256=sha,
                   expected_sha256=meas["provenance"]["season_parquet_sha256"])
    pct = pl.read_parquet(percentiles_path)
    check_percentiles(pct, derivation, season_sha256=sha)
    rows = dna_rows(season, pct, derivation)
    provenance = {"registry_version": derivation.version, "measurement_source": derivation.source,
                  "dna_percentile_basis": config.DNA_PERCENTILE_BASIS,
                  "season_parquet_sha256": sha,
                  "percentiles_parquet_sha256": C.file_sha256(percentiles_path),
                  "polars_version": pl.__version__}
    vec, dim = dna_tables(rows, provenance)
    out_dir.mkdir(parents=True, exist_ok=True)
    vec.write_parquet(out_dir / VECTOR_NAME, compression="zstd", statistics=True)
    dim.write_parquet(out_dir / DIMENSIONS_NAME, compression="zstd", statistics=True)
    status = {s: sum(r.dna_status == s for r in rows.values())
              for s in (D.COMPLETE, D.PARTIAL, D.DNA_INCOMPLETE)}
    logger.info("Wrote %d rows × DNA metrics (%d cells), %d dimension scores → %s",
                len(rows), vec.height, dim.height, out_dir)
    logger.info("dna_status: %s", status)
    return vec, dim


if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--season", type=Path, default=SEASON_PATH)
    parser.add_argument("--percentiles", type=Path, default=PERCENTILES_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s", stream=sys.stdout)
    build_player_dna(args.season, args.percentiles, args.out_dir)
