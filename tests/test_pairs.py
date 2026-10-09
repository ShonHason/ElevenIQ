"""analytics/pairs.py (plan 006 W1): neighbour facts, from the written rules (M4g1b, M4f3)."""
from __future__ import annotations

import polars as pl
import pytest

from eleveniq.analytics import pairs as P
from eleveniq.analytics.dna import RowDna, VectorCell

METRICS = ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8")


def _row(p: float | dict, status: str = "complete", missing: str | None = None) -> RowDna:
    pct = p if isinstance(p, dict) else {m: p for m in METRICS}
    cells = {m: VectorCell("F", "higher_better", 1.0, None if m == missing else pct[m], 50,
                           "below_min_attempts" if m == missing else "ok",
                           None, None, None, None, "ok", None) for m in METRICS}
    return RowDna("ST", status, cells, {})


def _k(pid: int, tid: int = 10) -> tuple:
    return (pid, tid, 2, 27, "statsbomb")


def test_ranks_are_competition_ranks_with_the_tie_tolerance():
    got = P.ranks([(0.01, "a"), (0.01 + 1e-13, "b"), (0.02, "c"), (0.005, "d")])
    assert got == {"d": 1, "a": 2, "b": 2, "c": 4}


def test_picks_largest_gaps_then_smallest_ties_by_name():
    a = _row({m: 50.0 for m in METRICS})
    b = _row({"m1": 90.0, "m2": 10.0, "m3": 80.0, "m4": 70.0, "m5": 60.0, "m6": 55.0,
              "m7": 50.0, "m8": 50.0})
    assert P.picks(a, b) == ("m1", "m2", "m3", "m4", "m5", "m7", "m8", "m6")


def test_picks_skip_a_missing_metric():
    a = _row(50.0, missing="m1")
    b = _row({**{m: 50.0 for m in METRICS}, "m1": 99.0})
    assert "m1" not in P.picks(a, b)


def test_neighbours_leave_out_own_rows_count_partial_rows_and_share_ranks():
    # Every metric at one percentile, so d = (Δ / 100)². A and A2 are one player at two clubs.
    rows = {_k(1, 10): _row(50.0), _k(1, 11): _row(55.0), _k(2): _row(60.0), _k(3): _row(40.0),
            _k(4): _row(52.0, status="partial", missing="m8")}
    out = P.group_neighbours(rows, 3)
    a = out[_k(1, 10)]
    assert a.n_candidates == 3                                   # own row left out
    assert [n.key for n in a.neighbours] == [_k(4), _k(2), _k(3)]
    assert [n.rank for n in a.neighbours] == [1, 2, 2]           # 60 and 40 tie at .01
    assert a.neighbours[1].distance == pytest.approx(0.01)


def test_dna_incomplete_rows_have_no_neighbours_and_are_no_candidates():
    rows = {_k(1): _row(50.0), _k(2): _row(60.0), _k(3): _row(55.0, status="dna_incomplete")}
    out = P.group_neighbours(rows, 10)
    assert set(out) == {_k(1), _k(2)}
    assert [n.key for n in out[_k(1)].neighbours] == [_k(2)]


def test_rows_from_dna_rebuilds_rows_and_rejects_two_groups():
    base = {"team_id": 10, "competition_id": 2, "season_id": 27, "source": "statsbomb",
            "family": "F", "direction": "higher_better", "value": 1.0, "n_local": 50,
            "metric_status": "ok", "z": None, "z_median": None, "z_scale": None,
            "z_scale_method": None, "z_status": "ok", "weight": 0.5, "dna_status": "complete"}
    df = pl.DataFrame([{**base, "player_id": 1, "position_group": "ST", "metric": m,
                        "pct_local": 40.0} for m in ("b", "a")])
    rows = P.rows_from_dna(df)
    assert list(rows["ST"][_k(1)].cells) == ["a", "b"]
    bad = pl.concat([df, pl.DataFrame([{**base, "player_id": 1, "position_group": "CB",
                                        "metric": "c", "pct_local": 1.0}])])
    with pytest.raises(ValueError, match="more than one position group"):
        P.rows_from_dna(bad)
