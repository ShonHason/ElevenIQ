"""
Integration tests for pipeline/build_percentiles.py on the 2015/16 data (plan 005 M4d,
ADR 004 §11). They need data/processed/player_season_stats.parquet; the module builds the
table once into a temporary directory.

- The independent spike (spikes/m4d_percentiles_reference.py --check-output) passes: every
  cell, the D9 league means against the measurements, the population sizes.
- The hand-picked fixture rows match.
- Two builds write identical bytes.
- Every gated row is scored; every unscored row is all-null; every reference member meets
  the gate (D20) and comes from one source.
- Members of a population are strictly inside (0, 100); a row outside it (D3 outsider,
  D5 second group) shares a player_id with a member only as a D3 mover's other stint.
- Removing a row below min_attempts changes no other percentile (Ighalo).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

from eleveniq.analytics import cohorts as C
from eleveniq.analytics.registry import REGISTRY, REGISTRY_VERSION, SPECS
from eleveniq.pipeline.build_percentiles import build_percentiles, percentile_table

_ROOT = Path(__file__).resolve().parents[1]
_SEASON = _ROOT / "data" / "processed" / "player_season_stats.parquet"
_MEASUREMENTS = _ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
_SPIKE = _ROOT / "spikes" / "m4d_percentiles_reference.py"
_FIXTURE = _ROOT / "tests" / "fixtures" / "m4d_percentiles" / "reference.json"
_FIELDS = ("value", "pct_local", "n_local", "status_local", "pct_global", "n_global", "status_global")

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not _SEASON.exists(), reason="season parquet not built")]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, pl.DataFrame]:
    out = tmp_path_factory.mktemp("m4d") / "percentiles.parquet"
    return out, build_percentiles(_SEASON, out)


@pytest.fixture(scope="module")
def season() -> pl.DataFrame:
    return pl.read_parquet(_SEASON).with_columns(C.row_status().alias("_status"))


def test_spike_check_output_passes(built):
    proc = subprocess.run([sys.executable, str(_SPIKE), "--check-output", str(built[0])],
                          capture_output=True, text=True, cwd=_ROOT)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "OK: the M4d output equals the spike" in proc.stdout


def test_two_builds_write_identical_bytes(built, tmp_path):
    second = tmp_path / "again.parquet"
    build_percentiles(_SEASON, second)
    assert C.file_sha256(second) == C.file_sha256(built[0])


def test_registry_version_and_source_are_stamped(built):
    t = built[1]
    assert t["registry_version"].unique().to_list() == [REGISTRY_VERSION]
    assert t["measurement_source"].unique().to_list() == [REGISTRY.source]
    assert t["season_parquet_sha256"].unique().to_list() == [C.file_sha256(_SEASON)]


def test_hand_picked_fixture_rows_match(built):
    t = built[1]
    doc = json.loads(_FIXTURE.read_text())
    assert doc["season_parquet_sha256"] == C.file_sha256(_SEASON)
    # the fixture rows depend on the registry, so a measurement change must refresh them
    # (plan 005 M4g4b backlog item 6)
    assert doc["measurements_sha256"] == C.file_sha256(_MEASUREMENTS)
    for r in doc["rows"]:
        k = r["key"]
        sub = t.filter((pl.col("player_id") == k["player_id"]) & (pl.col("team_id") == k["team_id"])
                       & (pl.col("competition_id") == k["competition_id"])
                       & (pl.col("cohort_role") == r["cohort_role"]))
        assert sub.height == len(r["cells"]), r["player_name"]
        for o in sub.iter_rows(named=True):
            want = r["cells"][o["metric"]]
            assert o["position_group"] == r["position_group"]
            assert o["percentile_status"] == r["percentile_status"]
            for f in _FIELDS:
                a, b = o[f], want[f]
                if isinstance(a, float) and isinstance(b, float):
                    assert math.isclose(a, b, rel_tol=0, abs_tol=1e-12), (r["player_name"], o["metric"], f)
                else:
                    assert a == b, (r["player_name"], o["metric"], f, a, b)


def test_every_gated_row_is_scored_and_unscored_rows_are_null(built, season):
    t = built[1].filter(pl.col("cohort_role") == "primary")
    assert t.height == season.height * len(SPECS)
    gated = season.filter(pl.col("_status") == "scored").height
    assert t.filter(pl.col("percentile_status") == "scored").height == gated * len(SPECS)
    unscored = t.filter(pl.col("percentile_status") != "scored")
    assert unscored.select(pl.col("pct_local", "pct_global", "n_local", "n_global").null_count()) \
        .row(0) == (unscored.height,) * 4
    assert (unscored["status_local"] == unscored["percentile_status"]).all()
    # D20: every scored row meets the gate.
    scored_keys = t.filter(pl.col("percentile_status") == "scored").select(C.SEASON_KEY).unique()
    joined = scored_keys.join(season, on=list(C.SEASON_KEY))
    assert joined["meets_min_minutes"].all() and joined["in_percentile_pool"].all()


def _populations(season: pl.DataFrame):
    """Every (metric, group, league or None) reference population, from cohorts.py."""
    for spec in SPECS:
        for grp in spec.applies_to:
            cell = REGISTRY.cell(spec.column, grp)
            members = C.scored_members(season, grp)
            for comp in [None, *sorted(members["competition_id"].unique().to_list())]:
                yield spec, grp, comp, C.reference_population(members, spec, cell, comp)


def test_reference_members_meet_the_gate_and_share_one_source(season):
    for spec, grp, comp, pop in _populations(season):
        assert pop["meets_min_minutes"].all() and pop["is_outfield"].all(), (spec.column, grp, comp)
        assert pop["source"].unique().to_list() in ([], [REGISTRY.source])
        assert pop.select(C.PLAYER_KEY).is_unique().all()


def test_members_inside_0_100_and_outsiders_share_ids_only_as_movers(built, season):
    t = built[1]
    key = [*C.SEASON_KEY]
    seen = {"members": 0, "outsiders": 0, "second": 0}
    for spec, grp, comp, pop in _populations(season):
        scope = "global" if comp is None else "local"
        scored = t.filter((pl.col("metric") == spec.column) & (pl.col("position_group") == grp)
                          & (pl.col(f"status_{scope}") == "ok"))
        if comp is not None:
            scored = scored.filter(pl.col("competition_id") == comp)
        if not scored.height:
            continue
        flagged = scored.join(pop.select(key).with_columns(pl.lit(True).alias("_m")), on=key, how="left")
        members = flagged.filter(pl.col("_m") & (pl.col("cohort_role") == "primary"))
        assert members[f"pct_{scope}"].min() > 0 and members[f"pct_{scope}"].max() < 100, \
            (spec.column, grp, comp)
        outside = flagged.filter(pl.col("_m").is_null() | (pl.col("cohort_role") == "second"))
        shared = outside.join(pop.select("player_id", "team_id", "competition_id"),
                              on="player_id", suffix="_member")
        # A D3 outsider meets its own other (selected) stint; a D5 second-group row must not
        # meet its own player at all (2015/16: none) — a future case needs a decision.
        assert shared.filter(pl.col("cohort_role") == "second").height == 0, (spec.column, grp, comp)
        primary = shared.filter(pl.col("cohort_role") == "primary")
        assert ((primary["team_id"] != primary["team_id_member"])
                | (primary["competition_id"] != primary["competition_id_member"])).all()
        seen["members"] += members.height
        seen["outsiders"] += primary.height
        seen["second"] += outside.filter(pl.col("cohort_role") == "second").height
    # Not vacuous: the D3 movers (Shelvey, Perotti, ...) and the 13 D5 rows were checked.
    assert seen["members"] > 0 and seen["outsiders"] > 0 and seen["second"] > 0, seen


def test_removing_a_row_below_min_attempts_changes_no_other_percentile(built):
    # Ighalo (ST, 40 long passes < 43): run through cohorts.py, not the spike.
    raw = pl.read_parquet(_SEASON)
    ighalo = raw.filter((pl.col("player_name") == "Odion Jude Ighalo") & (pl.col("team_id") == 23))
    assert ighalo.height == 1
    spec = next(s for s in SPECS if s.column == "long_pass_completion_pct")
    t = built[1].filter(pl.col("metric") == spec.column)
    mine = t.join(ighalo.select(C.SEASON_KEY), on=list(C.SEASON_KEY))
    assert mine["status_global"].to_list() == ["below_min_attempts"]
    rest = raw.join(ighalo.select(C.SEASON_KEY), on=list(C.SEASON_KEY), how="anti")
    without = percentile_table(rest, REGISTRY, season_sha256="x", specs=(spec,))
    cols = [*C.SEASON_KEY, "cohort_role", *_FIELDS]
    others = t.join(ighalo.select(C.SEASON_KEY), on=list(C.SEASON_KEY), how="anti").select(cols)
    assert others.equals(without.select(cols))
