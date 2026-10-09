"""
Fast unit tests for pipeline/build_player_dna.py's dna_rows / dna_tables glue, on a
synthetic registry and season (ADR 004 §12, §12.6, §12.8). test_dna.py covers
analytics/dna.py's pure functions directly with hand-built MetricInputs; this file covers
the pipeline wiring that feeds them: reusing the same local reference population the
percentiles were scored against, reconciling its size against the copied n_local, and
assembling the two output tables.

No data/ files are read; the season and percentiles are built in memory (pattern of
tests/test_cohorts.py).
"""
from __future__ import annotations

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics.registry import (
    POSSESSION_CRITERIA_FAILED, Cell, Derivation, FamilyStatus, MetricSpec, RatioSpec,
)
from eleveniq.pipeline.build_percentiles import percentile_table
from eleveniq.pipeline.build_player_dna import DnaInputError, dna_rows, dna_tables

GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
X = MetricSpec(column="x_p90", display_name="X", family="Defending", direction="higher_better",
               unit="per90", count_column="x")
R = MetricSpec(column="r_pct", display_name="R", family="Passing", direction="higher_better",
               unit="ratio", ratio=RatioSpec("rn", ("rd",)))
SPECS = (X, R)
SHA = "a" * 64
VERSION = "b" * 64
N = config.MIN_COHORT_SIZE   # exactly at the cohort-size gate: 30 members, no cohort_too_small

_MIN_COLS = ("minutes_gk", "minutes_cb", "minutes_fb_wb", "minutes_dm_cm", "minutes_am_winger",
             "minutes_st")
_GROUP_MIN = {"CB": "minutes_cb", "FB-WB": "minutes_fb_wb", "DM-CM": "minutes_dm_cm",
              "AM-Winger": "minutes_am_winger", "ST": "minutes_st"}


def _cell(col: str, grp: str, min_attempts: int | None = None) -> Cell:
    return Cell(col, grp, "dna", ("dna",), global_ok=True, basis="local", min_attempts=min_attempts)


def _derivation() -> Derivation:
    cells = {}
    for g in GROUPS:
        cells[("x_p90", g)] = _cell("x_p90", g)
        cells[("r_pct", g)] = _cell("r_pct", g, min_attempts=10)
    status = {("Defending", g): FamilyStatus("Defending", g, POSSESSION_CRITERIA_FAILED,
                                              "x_p90", None) for g in GROUPS}
    return Derivation(cells=cells, boundary=(), source="statsbomb", version=VERSION,
                      family_status=status)


def row(pid: int, x: float, *, team: int = 1, comp: int = 11, group: str = "CB",
       minutes: float = 1000.0, rn: int = 15, rd: int = 20) -> dict:
    r = {"player_id": pid, "team_id": team, "competition_id": comp, "season_id": 27,
         "source": "statsbomb", "player_name": f"p{pid}", "position_group": group,
         "second_position_group": None, "position_group_ambiguous": False,
         "in_percentile_pool": True, "is_outfield": True,
         "minutes_played": minutes, "minutes_nominal_equiv": minutes, "meets_min_minutes": True,
         "x_p90": x, "x": int(x), "rn": rn, "rd": rd, "r_pct": rn / rd if rd else None}
    for c in _MIN_COLS:
        r[c] = 0.0
    r[_GROUP_MIN[group]] = minutes
    return r


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None)


def _cohort() -> list[dict]:
    """N scored CB rows: x = 1..N, r_pct strictly increasing, all above min_attempts."""
    return [row(i + 1, float(i + 1), rn=10 + i, rd=20) for i in range(N)]


def _built():
    derivation = _derivation()
    season = frame(_cohort())
    pct = percentile_table(season, derivation, season_sha256=SHA, specs=SPECS)
    return derivation, season, pct


KEY = (1, 1, 11, 27, "statsbomb")


def test_dna_rows_uses_the_same_local_population_the_percentiles_were_scored_against():
    derivation, season, pct = _built()
    rows = dna_rows(season, pct, derivation, specs=SPECS)
    cell = rows[KEY].cells["x_p90"]
    want = D.spread(pl.Series([float(i) for i in range(1, N + 1)]))
    assert (cell.z_median, cell.z_scale, cell.z_scale_method) == (want.median, want.scale, want.method)
    # player 1 holds the minimum x and the minimum r_pct: bottom (Hazen) percentile on both.
    assert cell.pct_local == pytest.approx(0.5 / N * 100)
    assert cell.z == pytest.approx((1.0 - want.median) / want.scale)
    pct_row = pct.filter((pl.col("player_id") == 1) & (pl.col("metric") == "x_p90")
                         & (pl.col("cohort_role") == "primary")).row(0, named=True)
    assert cell.pct_local == pct_row["pct_local"]
    assert cell.n_local == pct_row["n_local"] == N


def test_dna_rows_raises_when_the_population_size_disagrees_with_the_copied_n_local():
    """A reconciliation guard (§12.8): the z population recomputed from the season must
    match the n_local copied from the percentiles, or the two inputs are out of step."""
    derivation, season, pct = _built()
    tampered = pct.with_columns(
        pl.when((pl.col("player_id") == 1) & (pl.col("metric") == "x_p90"))
          .then(pl.col("n_local") + 1).otherwise(pl.col("n_local")).alias("n_local"))
    with pytest.raises(DnaInputError, match="population"):
        dna_rows(season, tampered, derivation, specs=SPECS)


def test_dna_tables_withholds_defending_and_scores_a_single_member_passing(monkeypatch):
    """Passing is scored on its one member when not withheld (CB Passing is withheld by
    config, §12.9, so the table is narrowed to Defending here; the CB withhold is tested
    below)."""
    monkeypatch.setitem(config.DNA_WITHHELD_DIMENSIONS, "Passing", ("pending_xpass_deferred", ()))
    derivation, season, pct = _built()
    rows = dna_rows(season, pct, derivation, specs=SPECS)
    provenance = {"registry_version": derivation.version, "measurement_source": derivation.source,
                  "dna_percentile_basis": "local", "season_parquet_sha256": "s" * 64,
                  "percentiles_parquet_sha256": "p" * 64, "polars_version": "x.y.z"}
    vec, dim = dna_tables(rows, provenance)

    assert vec.height == N * len(SPECS)
    assert dim.height == N * 2                       # Defending and Passing, one each per row

    defending = dim.filter(pl.col("family") == "Defending")
    assert defending["score_status"].unique().to_list() == [POSSESSION_CRITERIA_FAILED]
    assert defending["score"].null_count() == N
    assert (defending["members"].list.len() == 0).all()

    passing = dim.filter((pl.col("family") == "Passing") & (pl.col("player_id") == 1))
    r_cell = vec.filter((pl.col("player_id") == 1) & (pl.col("metric") == "r_pct")).row(0, named=True)
    assert passing.height == 1
    assert passing["score"][0] == pytest.approx(r_cell["pct_local"])
    assert passing["members"][0].to_list() == ["r_pct"]

    for col, want in provenance.items():
        assert vec[col].unique().to_list() == [want]
        assert dim[col].unique().to_list() == [want]

    got = vec.select(*C.SEASON_KEY, "metric").rows()
    assert got == sorted(got)


def test_dna_tables_withhold_cb_passing_by_config():
    derivation, season, pct = _built()
    rows = dna_rows(season, pct, derivation, specs=SPECS)
    provenance = {"registry_version": derivation.version, "measurement_source": derivation.source,
                  "dna_percentile_basis": "local", "season_parquet_sha256": "s" * 64,
                  "percentiles_parquet_sha256": "p" * 64, "polars_version": "x.y.z"}
    _, dim = dna_tables(rows, provenance)
    passing = dim.filter(pl.col("family") == "Passing")
    assert passing.height == N
    assert passing["score_status"].unique().to_list() == ["pending_xpass_deferred"]
    assert passing["score"].null_count() == N
