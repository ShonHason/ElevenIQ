"""
Integration tests for pipeline/build_player_dna.py on the 2015/16 data (plan 005 M4f2,
ADR 004 §12). They need data/processed/player_season_stats.parquet and
player_season_percentiles.parquet; the module builds once into a temporary directory.

- The independent spike (spikes/m4f_dna_reference.py --check-output) passes.
- Two builds write identical bytes (both files).
- 1,233 primary scored rows: 32 partial, 0 dna_incomplete (§12.2); row weights sum to 1.
- d(A, B) = d(B, A) over every same-group pair, partial rows included.
- The hand-picked fixture rows match; provenance is stamped; Defending is withheld.
"""
from __future__ import annotations

import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import polars as pl
import pytest

from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics.registry import REGISTRY, REGISTRY_VERSION
from eleveniq.pipeline.build_player_dna import (DIMENSIONS_NAME, VECTOR_NAME, build_player_dna,
                                                dna_rows)

_ROOT = Path(__file__).resolve().parents[1]
_SEASON = _ROOT / "data" / "processed" / "player_season_stats.parquet"
_MEASUREMENTS = _ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
_PCT = _ROOT / "data" / "processed" / "player_season_percentiles.parquet"
_SPIKE = _ROOT / "spikes" / "m4f_dna_reference.py"
_FIXTURE = _ROOT / "tests" / "fixtures" / "m4f_dna" / "reference.json"

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not (_SEASON.exists() and _PCT.exists()),
                                 reason="season or percentiles parquet not built")]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, pl.DataFrame, pl.DataFrame]:
    out = tmp_path_factory.mktemp("m4f")
    vec, dim = build_player_dna(_SEASON, _PCT, out)
    return out, vec, dim


@pytest.fixture(scope="module")
def rows() -> dict:
    return dna_rows(pl.read_parquet(_SEASON), pl.read_parquet(_PCT), REGISTRY)


def test_spike_check_output_passes(built):
    proc = subprocess.run([sys.executable, str(_SPIKE), "--check-output", str(built[0] / VECTOR_NAME)],
                          capture_output=True, text=True, cwd=_ROOT)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "OK: the M4f output equals the spike" in proc.stdout


def test_two_builds_write_identical_bytes(built, tmp_path):
    build_player_dna(_SEASON, _PCT, tmp_path)
    for name in (VECTOR_NAME, DIMENSIONS_NAME):
        assert C.file_sha256(tmp_path / name) == C.file_sha256(built[0] / name), name


def test_row_counts_and_dna_status(built):
    vec = built[1]
    status = vec.select(*C.SEASON_KEY, "dna_status").unique()
    assert status.height == 1233
    counts = dict(status.group_by("dna_status").len().iter_rows())
    assert counts == {"complete": 1201, "partial": 32}


def test_rows_are_the_primary_scored_rows(built):
    pct = pl.read_parquet(_PCT)
    want = pct.filter((pl.col("cohort_role") == "primary") & (pl.col("percentile_status") == "scored")
                      ).select(C.SEASON_KEY).unique().sort(C.SEASON_KEY)
    got = built[1].select(C.SEASON_KEY).unique().sort(C.SEASON_KEY)
    assert got.equals(want)


def test_row_weights_sum_to_one(built):
    sums = built[1].group_by(C.SEASON_KEY).agg(pl.col("weight").sum())
    assert (sums["weight"] - 1.0).abs().max() <= 1e-12
    missing = built[1].filter(pl.col("metric_status") != "ok")
    assert missing.height == 32 and missing["weight"].null_count() == 32


def test_distance_is_symmetric_over_every_same_group_pair(rows):
    by_group: dict[str, list[D.RowDna]] = {}
    for r in rows.values():
        by_group.setdefault(r.group, []).append(r)
    pairs = partial_pairs = 0
    for g, rs in by_group.items():
        for a, b in combinations(rs, 2):
            assert D.pair_distance(a, b) == D.pair_distance(b, a)
            pairs += 1
            partial_pairs += "partial" in (a.dna_status, b.dna_status)
    assert pairs == 156961 and partial_pairs > 0


def test_provenance_is_stamped(built):
    for df in built[1:]:
        assert df["registry_version"].unique().to_list() == [REGISTRY_VERSION]
        assert df["measurement_source"].unique().to_list() == [REGISTRY.source]
        assert df["dna_percentile_basis"].unique().to_list() == ["local"]
        assert df["season_parquet_sha256"].unique().to_list() == [C.file_sha256(_SEASON)]
        assert df["percentiles_parquet_sha256"].unique().to_list() == [C.file_sha256(_PCT)]


def test_defending_withheld_and_passing_null_counts(built):
    """§13.8 D5: Defending withheld in every group with its registry status (2015/16: DM-CM
    mover_stability_inconclusive, the others possession_criteria_failed); §12.9: Passing
    withheld in every non-ST row (1,075), which absorbs the 30 rows that had no directional
    Passing member."""
    dim = built[2]
    defending = dim.filter(pl.col("family") == "Defending")
    assert defending["score"].null_count() == defending.height
    by_group = dict(defending.group_by("position_group").agg(pl.col("score_status").unique())
                    .iter_rows())
    assert by_group == {"CB": ["possession_criteria_failed"], "FB-WB": ["possession_criteria_failed"],
                        "DM-CM": ["mover_stability_inconclusive"],
                        "AM-Winger": ["possession_criteria_failed"],
                        "ST": ["possession_criteria_failed"]}
    passing = dim.filter(pl.col("family") == "Passing")
    held = passing.filter(pl.col("score_status") == "pending_mover_stability")
    assert held.height == 1075 and "ST" not in held["position_group"].to_list()
    assert held["score"].null_count() == held.height
    st = passing.filter(pl.col("position_group") == "ST")
    assert st["score_status"].unique().to_list() == ["ok"]
    assert dim.filter(pl.col("score_status") == "no_directional_metric").is_empty()


def test_hand_picked_fixture_rows_match(built):
    vec, dim = built[1], built[2]
    doc = json.loads(_FIXTURE.read_text())
    assert doc["season_parquet_sha256"] == C.file_sha256(_SEASON)
    # the fixture rows depend on the registry, so a measurement change must refresh them
    # (plan 005 M4g4b backlog item 6)
    assert doc["measurements_sha256"] == C.file_sha256(_MEASUREMENTS)
    for r in doc["rows"]:
        k = r["key"]
        sel = (pl.col("player_id") == k["player_id"]) & (pl.col("team_id") == k["team_id"]) \
            & (pl.col("competition_id") == k["competition_id"])
        got = {o["metric"]: o for o in vec.filter(sel).iter_rows(named=True)}
        assert set(got) == set(r["cells"]), r["player_name"]
        for m, want in r["cells"].items():
            for f in ("pct_local", "z", "weight"):
                assert got[m][f] == pytest.approx(want[f], abs=1e-9) if want[f] is not None \
                    else got[m][f] is None, (r["player_name"], m, f)
            assert got[m]["metric_status"] == want["metric_status"]
            assert got[m]["dna_status"] == r["dna_status"]
        dims = {o["family"]: o for o in dim.filter(sel).iter_rows(named=True)}
        for f, want in r["dimensions"].items():
            assert dims[f]["score_status"] == want["score_status"], (r["player_name"], f)
            assert dims[f]["members"] == want["members"]
            if want["score"] is not None:
                assert dims[f]["score"] == pytest.approx(want["score"], abs=1e-9)


def test_isco_holds_the_largest_abs_z_uncapped(built):
    top = built[1].filter(pl.col("z").is_not_null()).sort(pl.col("z").abs(), descending=True).row(0, named=True)
    assert (top["player_id"], top["team_id"], top["metric"]) == (4926, 220, "box_entries_carry_p90")
    assert top["z"] == pytest.approx(13.658, abs=1e-3)
