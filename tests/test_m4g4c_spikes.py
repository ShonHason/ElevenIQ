"""
M4g4c spikes (plan 005 M4g4c, ADR 004 §13.8): the pure pieces of spikes/m4g4_possession.py and
spikes/m4g4_possession_reference.py, written from the spec (the engine docstring, the M4g4c plan):
- the tie rule (β-adjusted values to 12 significant digits) makes exactly-equal values tie
  however they are computed, identically in the engine and the reference;
- G3 on a mover stint or half: < 10 team actions (share) or < 500 opponent passes (β) → NaN;
- average ranks and Spearman (reference) equal scipy / polars; the β̂ slope is the pooled
  within-league OLS slope;
- the reference's mismatch checks go red on a tampered value (one test per field family);
- the D5 status precedence (possession → mover verdict → seed sensitivity → below model range).
No data needed.
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g4_possession as eng  # noqa: E402
import m4g4_possession_reference as ref  # noqa: E402


# ── tie rule ──────────────────────────────────────────────────────────────────

def test_tie_rule_ties_equal_ratios_computed_in_different_orders():
    # count / opp equal in exact arithmetic (3 / 4000 = 6 / 8000), β = 1, two computation orders
    e_ref, mins = 417.7, 900.0
    a = 3 / mins * 90 * (e_ref / (4000 / mins * 90))
    b = 6 * e_ref / 8000
    assert a != b                      # the raw values differ in the last bit: the rule is needed
    assert eng.canon(a) == eng.canon(b) == ref.canon(a) == ref.canon(b)


def test_tie_rule_vectorised_equals_scalar_in_both():
    x = np.random.default_rng(3).uniform(0, 10, 200)
    assert np.array_equal(eng.canon(x), ref.canon(x))
    assert eng.canon(float(x[0])) == float(eng.canon(x)[0])


# ── G3 ────────────────────────────────────────────────────────────────────────

def _sums(n=30.0, mins=900.0, opp=4000.0, team=200.0):
    return {"ball_recoveries": n, "minutes_played": mins, eng.OPP: opp, "team_ball_recoveries_while_on": team}


@pytest.mark.parametrize("cand", ["beta_hat", "beta_1"])
def test_g3_opponent_passes(cand):
    prm = {"e_ref": 410.0, "beta_hat": 0.3}
    assert math.isnan(eng.mover_value(cand, prm, _sums(opp=499.0)))
    assert not math.isnan(eng.mover_value(cand, prm, _sums(opp=500.0)))
    assert math.isnan(ref.Movers.value(cand, prm, _sums(opp=499.0)))
    assert ref.Movers.value(cand, prm, _sums(opp=500.0)) == eng.mover_value(cand, prm, _sums(opp=500.0))


def test_g3_team_share():
    prm = {"e_ref": 410.0, "beta_hat": 0.3}
    assert math.isnan(eng.mover_value("team_share", prm, _sums(team=9.0)))
    assert eng.mover_value("team_share", prm, _sums(n=5.0, team=10.0)) == 0.5
    assert math.isnan(ref.Movers.value("team_share", prm, _sums(team=9.0)))


def test_beta_1_is_count_times_e_ref_over_opp():
    prm = {"e_ref": 400.0, "beta_hat": 0.0}
    assert eng.mover_value("beta_1", prm, _sums(n=30, mins=900, opp=4000)) == pytest.approx(30 * 400 / 4000)


# ── ranks, Spearman, β̂ ───────────────────────────────────────────────────────

def test_average_ranks_and_spearman_match_polars():
    rng = np.random.default_rng(1)
    a, b = rng.integers(0, 4, 60).astype(float), rng.integers(0, 3, 60).astype(float)
    want = pl.DataFrame({"a": a, "b": b}).select(pl.corr("a", "b", method="spearman")).item()
    assert ref.spearman(a, b) == pytest.approx(want, abs=1e-12)
    assert list(ref.avg_rank(np.array([2.0, 1.0, 2.0, 3.0]))) == [2.5, 1.0, 2.5, 4.0]


def test_slope_is_pooled_within_league_ols():
    # two leagues with different intercepts and the same slope 0.5: demeaning removes the offsets
    tab = {2: [(1, 0.0, 1.0), (2, 1.0, 1.5), (3, 2.0, 2.0)],
           7: [(4, 0.0, 5.0), (5, 2.0, 6.0)], 11: [(6, 1.0, 3.0), (7, 3.0, 4.0)], 12: [(8, 0.0, 0.0), (9, 4.0, 2.0)]}
    assert ref.slope(tab, ref.POOL) == pytest.approx(0.5)
    assert ref.slope(tab, [2, 7]) == pytest.approx(0.5)


def test_slope_fails_on_a_zero_team_mean():
    tab = {2: [(1, 0.0, None), (2, 1.0, 1.5)], 7: [], 11: [], 12: []}
    assert ref.slope(tab, [2]) is None


# ── reference mismatch checks (tampered copies) ───────────────────────────────

def _ref_cell() -> dict:
    """A reference cell_criteria result (synthetic, every candidate defined)."""
    per = {str(c): 0.01 * i for i, c in enumerate(ref.POOL)}
    crit = {"rho": {p: (dict(per), 0.015) for p in ref.PROXIES},
            "gap": {p: ({k: 5.0 for k in per}, 5.0) for p in ref.PROXIES}}
    cand = {"c": crit, "c1": True, "c2": True, "c3": True, "r_full": 0.7, "flip": False, "incons": False,
            "d9": {k: 50.0 for k in per}, "passes": True}
    return {"e_ref": 410.0, "beta_hat": 0.3, "lolo": {c: 0.3 for c in ref.POOL}, "ci": [0.1, 0.5],
            "zero_teams": [], "cands": {c: cand for c in ref.CANDS}, "selected": "beta_0"}


def _output_cell(r: dict) -> dict:
    """The engine's possession.json form of the same values (a deep copy)."""
    r = copy.deepcopy(r)
    cands = {}
    for c, rc in r["cands"].items():
        cands[c] = {"c1_rho": {p: {"by_league": rc["c"]["rho"][p][0], "mean": rc["c"]["rho"][p][1]} for p in ref.PROXIES},
                    "c2_gap": {p: {"by_league": rc["c"]["gap"][p][0], "gap": rc["c"]["gap"][p][1]} for p in ref.PROXIES},
                    "c3_r_full": {"r_full": rc["r_full"]}, "c4_d9_league_means": rc["d9"],
                    "c1_pass": rc["c1"], "c2_pass": rc["c2"], "c3_pass": rc["c3"], "sign_flip": rc["flip"],
                    "inconsistent_across_leagues": rc["incons"], "passes": rc["passes"]}
    return {"e_ref": r["e_ref"], "beta_hat": r["beta_hat"], "beta_hat_lolo": {str(c): v for c, v in r["lolo"].items()},
            "beta_hat_ci": {"ci": r["ci"]}, "selected": r["selected"],
            "flags": {"rises_with_own_possession": False, "abs_beta_hat_gt_1": False}, "candidates": cands}


def test_untampered_cell_has_no_errors():
    r = _ref_cell()
    assert ref.cell_errors(_output_cell(r), r, "x") == []


@pytest.mark.parametrize("tamper", [
    lambda o: o.update(e_ref=411.0),
    lambda o: o.update(beta_hat=0.31),
    lambda o: o["beta_hat_lolo"].update({"2": 0.2}),
    lambda o: o["beta_hat_ci"].update(ci=[0.1, 0.6]),
    lambda o: o.update(selected="beta_hat"),
    lambda o: o["flags"].update(rises_with_own_possession=True),
    lambda o: o["candidates"]["beta_hat"]["c1_rho"]["poss_dur_loo"]["by_league"].update({"7": 0.5}),
    lambda o: o["candidates"]["beta_1"]["c2_gap"]["poss_pass_loo"].update(gap=9.0),
    lambda o: o["candidates"]["team_share"]["c3_r_full"].update(r_full=0.59),
    lambda o: o["candidates"]["beta_0"]["c4_d9_league_means"].update({"11": 49.0}),
    lambda o: o["candidates"]["beta_0"].update(sign_flip=True),
    lambda o: o["candidates"]["beta_0"].update(passes=False),
], ids=["e_ref", "beta_hat", "lolo", "ci", "selected", "flag", "rho", "gap", "r_full", "d9", "flip", "passes"])
def test_tampered_cell_goes_red(tamper):
    r = _ref_cell()
    o = _output_cell(r)
    tamper(o)
    assert ref.cell_errors(o, r, "x")


# ── D5 status precedence ──────────────────────────────────────────────────────

def _v(verdict="PASS", extra=("PASS",) * 5, ci_hi=0.3, s_a=0.9):
    return {"verdict": verdict, "extra_seed_verdicts": list(extra), "ci": [-0.5, ci_hi], "sqrt_s2_rms_a": s_a}


@pytest.mark.parametrize("sel, v, want", [
    (None, None, "possession_criteria_failed"),
    ("beta_0", _v("inconclusive"), "mover_stability_inconclusive"),
    ("beta_hat", _v("FAIL"), "mover_stability_failed"),
    ("beta_1", _v("untestable"), "mover_stability_untestable"),
    ("team_share", _v(extra=("PASS", "PASS", "inconclusive", "PASS", "PASS")), "mover_seed_sensitive"),
    ("beta_hat", _v(ci_hi=-0.1, s_a=1.2), "mover_below_model_range"),
    ("beta_hat", _v(ci_hi=-0.1, s_a=0.95), "ok"),
    ("beta_hat", _v(), "ok"),
    # precedence: seed sensitivity is reported before below model range
    ("beta_hat", _v(extra=("FAIL",) * 5, ci_hi=-0.1, s_a=1.2), "mover_seed_sensitive"),
])
def test_status_precedence(sel, v, want):
    assert ref.status_of(sel, v) == want


def test_engine_fit_equals_reference_slope_and_fails_on_a_zero_team():
    # rows: (league, team, minutes, count, opp); two teams per league, team logs with slope 0.5
    rows = []
    for lg, base in zip(eng.POOL, (1.0, 2.0, 0.5, 3.0)):
        for t, (x, y) in enumerate(((0.0, 0.0), (1.0, 0.5), (2.0, 1.0))):
            rows.append((lg, lg * 100 + t, 900.0, 900.0 / 90 * np.exp(base + y), 900.0 / 90 * np.exp(5 + x)))
    cell = object.__new__(eng.Cell)
    cell.comp, cell.team, cell.minutes, cell.count, cell.opp = (np.array([r[i] for r in rows]) for i in range(5))
    beta, zero = cell.fit(eng.POOL)
    ref_rows = [{"competition_id": r[0], "team_id": r[1], "minutes_played": r[2], "ball_recoveries": r[3],
                 eng.OPP: r[4]} for r in rows]
    assert beta == pytest.approx(0.5) and zero == []
    assert beta == pytest.approx(ref.slope(ref.team_logs(ref_rows, "ball_recoveries_p90"), ref.POOL))
    cell.count = cell.count.copy()
    cell.count[0] = 0.0
    beta0, zero0 = cell.fit(eng.POOL)
    assert beta0 is None and zero0 == [int(cell.team[0])]


def test_reference_mover_value_is_nan_at_zero_minutes_like_the_engine():
    prm = {"e_ref": 410.0, "beta_hat": 0.3}
    s = _sums(mins=0.0)
    assert math.isnan(eng.mover_value("beta_hat", prm, s))
    assert math.isnan(ref.Movers.value("beta_hat", prm, s))
