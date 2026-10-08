"""
M4g4c2 trust check spikes (ADR 004 §13.8 "M4g4c2 trust check"), from the written spec:
- a bootstrap resample keeps each team's rows together, gives every draw slot its own team key
  and player key (no dedupe across slots), and draws teams within each league;
- leave-one-league-out runs the selection on 3 leagues;
- the β̂ refit leaves out teams with Σ count below the bar (both engine and reference agree);
- the permutation p is (1 + #{T_null ≥ T_obs}) / (1 + N) and the null permutes whole teams;
- the engine and the reference select the same candidate on a synthetic frame;
- the reference's mismatch check goes red on a tampered field.
No data needed.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g3_power as pw  # noqa: E402
import m4g4c2_trust_check as eng  # noqa: E402
import m4g4c2_trust_reference as ref  # noqa: E402

POOL = (2, 7, 11, 12)


def synthetic(seed: int = 0, teams: int = 6, per_team: int = 3):
    rng = np.random.default_rng(seed)
    rows = []
    pid = 1
    for lg in POOL:
        for t in range(teams):
            poss = rng.uniform(0.4, 0.6)
            for _ in range(per_team):
                mins = float(rng.uniform(900, 3000))
                opp = mins / 90 * rng.uniform(350, 500)
                rows.append({"comp": lg, "team": lg * 100 + t, "pkey": pid, "min": mins,
                             "n": float(rng.poisson(mins / 90 * 2) + 1), "opp": opp,
                             "tm": float(rng.integers(200, 400)), "dur": poss + rng.normal(0, 0.01),
                             "pas": poss + rng.normal(0, 0.01)})
                pid += 1
    f_eng = eng.Frame(np.array([r["comp"] for r in rows]), np.array([r["team"] for r in rows]),
                      np.array([r["pkey"] for r in rows]), np.array([r["min"] for r in rows]),
                      np.array([r["n"] for r in rows]), np.array([r["opp"] for r in rows]),
                      np.array([r["tm"] for r in rows]),
                      {"poss_dur_loo": np.array([r["dur"] for r in rows]),
                       "poss_pass_loo": np.array([r["pas"] for r in rows])})
    f_ref = {"comp": f_eng.comp, "team": f_eng.team, "pkey": f_eng.pkey, "min": f_eng.minutes, "n": f_eng.count,
             "opp": f_eng.opp, "tm": f_eng.team_count, "poss_dur_loo": f_eng.proxy["poss_dur_loo"],
             "poss_pass_loo": f_eng.proxy["poss_pass_loo"]}
    return f_eng, f_ref


ALL_C3 = {c: True for c in ("beta_0", "beta_hat", "team_share", "beta_1")}


def possession_frame(seed: int = 0):
    """A frame whose raw per-90 scales with opponent passes (E ∝ 1.5 − possession), so β = 0 fails
    criterion (1) and an E-scaling candidate is selected (β̂ for seeds 0–2, 4, 5; β = 1 for 3)."""
    rng = np.random.default_rng(seed)
    cols = {k: [] for k in ("comp", "team", "pkey", "min", "n", "opp", "tm", "dur")}
    pid = 1
    for lg in POOL:
        for t in range(16):
            poss = rng.uniform(0.35, 0.65)
            for _ in range(4):
                m, e = float(rng.uniform(1500, 3000)), 400 * (1.5 - poss)
                skill = rng.lognormal(0, 0.35)
                for k, v in (("comp", lg), ("team", lg * 100 + t), ("pkey", pid), ("min", m),
                             ("n", float(round(m / 90 * 3 * skill * e / 400))), ("opp", m / 90 * e),
                             ("tm", float(rng.integers(300, 500))), ("dur", poss + rng.normal(0, 0.005))):
                    cols[k].append(v)
                pid += 1
    a = {k: np.array(v) for k, v in cols.items()}
    prox = {"poss_dur_loo": a["dur"], "poss_pass_loo": a["dur"] + 0.001}
    f_eng = eng.Frame(a["comp"], a["team"], a["pkey"], a["min"], a["n"], a["opp"], a["tm"], prox)
    f_ref = {"comp": a["comp"], "team": a["team"], "pkey": a["pkey"], "min": a["min"], "n": a["n"],
             "opp": a["opp"], "tm": a["tm"], **prox}
    return f_eng, f_ref


def test_resample_keeps_teams_together_with_distinct_keys():
    f, _ = synthetic()
    g = eng.resample(f, np.random.default_rng(1))
    assert len(g.comp) == len(f.comp)                      # equal team sizes → same row count
    for lg in POOL:
        s = g.comp == lg
        slots = np.unique(g.team[s])
        assert len(slots) == 6 and all(t // 1000 == lg for t in slots.tolist())
        for t in slots.tolist():
            assert (g.team == t).sum() == 3                  # a slot holds one whole team
    assert len(np.unique(g.pkey)) == len(g.pkey)            # no dedupe across slots


def test_resample_matches_reference_draws():
    f, fr = synthetic()
    g = eng.resample(f, pw.cell_rng("m4g4c2_boot", "x", "y"))
    gr = ref.resample(fr, ref.r4.stream("m4g4c2_boot", "x", "y"))
    assert np.array_equal(g.team, gr["team"]) and np.array_equal(g.pkey, gr["pkey"])
    assert np.array_equal(g.minutes, gr["min"])


@pytest.mark.parametrize("min_count", [0.0, 10.0, 40.0])
def test_fit_agrees_and_excludes_low_count_teams(min_count):
    f, fr = synthetic(seed=3)
    a, b = eng.fit(f, POOL, min_count), ref.slope(fr, POOL, min_count)
    assert (a is None and b is None) or a == pytest.approx(b, abs=1e-12)


def test_fit_with_a_bar_above_every_team_is_undefined():
    f, fr = synthetic()
    assert eng.fit(f, POOL, 1e9) is None and ref.slope(fr, POOL, 1e9) is None


@pytest.mark.parametrize("seed, want", [(0, "beta_hat"), (2, "beta_hat"), (3, "beta_1")])
def test_engine_and_reference_select_the_same(seed, want):
    f, fr = possession_frame(seed)
    assert eng.select(f, POOL, ALL_C3) == ref.select(fr, POOL, ALL_C3) == want
    assert eng.select(f, POOL, ALL_C3, 1e9) == ref.select(fr, POOL, ALL_C3, 1e9) == eng.UNDEF
    kept = [2, 7, 11]
    sub_e = f.take(np.flatnonzero(f.comp != 12))
    sub_r = ref.sub(fr, np.flatnonzero(fr["comp"] != 12))
    assert eng.select(sub_e, kept, ALL_C3) == ref.select(sub_r, kept, ALL_C3)


def test_lolo_reports_four_three_league_runs():
    f, _ = synthetic()
    out = eng.lolo(f, ALL_C3, "beta_0")
    assert sorted(out["selected_without"]) == ["11", "12", "2", "7"]
    assert out["agree"] == sum(v == "beta_0" for v in out["selected_without"].values())
    assert out["pass"] == (out["agree"] >= 3)


def test_criterion_3_failure_blocks_a_candidate():
    f, fr = possession_frame(0)
    assert eng.select(f, POOL, ALL_C3) == "beta_hat"
    blocked = dict(ALL_C3, beta_hat=False)
    assert eng.select(f, POOL, blocked) == ref.select(fr, POOL, blocked) != "beta_hat"


def test_permutation_engine_equals_reference_on_the_same_stream(monkeypatch):
    """(c) on a synthetic cell: the engine's and the reference's T and p agree (same stream), and
    the team-mean proxy is constant within a team (the null moves whole teams)."""
    f, fr = possession_frame(0)
    lolo = {lg: eng.fit(f, [x for x in POOL if x != lg]) for lg in POOL}
    monkeypatch.setattr(eng, "N_PERM", 19)
    monkeypatch.setattr(ref, "N_PERM", 19)
    monkeypatch.setattr(ref, "B_BOOT", 2)
    failed = {"rho": True, "gap": True}
    out = eng.permutation(f, failed, "m", "g", "beta_hat", lolo)
    cell = {"metric": "m", "group": "g", "selected": "beta_hat",
            "candidates": {"beta_0": {"c3_pass": True, "c1_pass": False, "c2_pass": False},
                           "beta_hat": {"c3_pass": True}, "team_share": {"c3_pass": True},
                           "beta_1": {"c3_pass": True}},
            "beta_hat_lolo": {str(k): v for k, v in lolo.items()}}
    r = ref.check_cell(fr, cell)["c"]
    for k in ("t_rho_obs", "t_gap_obs", "p_rho", "p_gap", "pass"):
        assert ref.close(out[k], r[k]), k
    tm = eng.team_mean_proxy(f)
    for lg in POOL:
        teams, means = tm[lg]
        for t in teams.tolist():
            rows = (f.comp == lg) & (f.team == t)
            assert means["poss_dur_loo"][t] == pytest.approx(f.proxy["poss_dur_loo"][rows].mean())


def test_permutation_p_lies_on_the_grid():
    f, _ = synthetic()
    tm = eng.team_mean_proxy(f)
    for lg in POOL:
        teams, means = tm[lg]
        assert len(teams) == 6 and set(means) == {"poss_dur_loo", "poss_pass_loo"}
    old = eng.N_PERM
    try:
        eng.N_PERM = 9
        out = eng.permutation(f, {"rho": True, "gap": True}, "m", "g", "beta_1", {})
    finally:
        eng.N_PERM = old
    for k in ("p_rho", "p_gap"):
        assert out[k] in {(1 + j) / 10 for j in range(10)}
    assert out["judged"] == ["rho", "gap"]


def _cell():
    return {"a": {"p_same": 0.1, "p_same_family": 0.2, "p_raw_or_none": 0.7, "undefined": 0, "pass": False,
                  "tally": {"beta_hat": 100, "none": 900}},
            "b": {"selected_without": {"2": None, "7": "beta_hat", "11": None, "12": None}, "agree": 1, "pass": False},
            "c": {"t_rho_obs": 0.17, "t_gap_obs": 8.6, "p_rho": 0.0005, "p_gap": 0.0005, "judged": ["rho", "gap"],
                  "pass": True},
            "d": {"10": {"beta_hat_all4": 0.96, "selected": "beta_hat", "teams_excluded": 0},
                  "20": {"beta_hat_all4": 0.96, "selected": "beta_hat", "teams_excluded": 0}, "pass": True},
            "pass": False}


def test_untampered_trust_cell_has_no_errors():
    assert ref.errors_of(_cell(), _cell(), "x") == []


@pytest.mark.parametrize("tamper", [
    lambda o: o["a"].update(p_same=0.2),
    lambda o: o["a"]["tally"].update(none=899),
    lambda o: o["b"]["selected_without"].update({"2": "beta_hat"}),
    lambda o: o["c"].update(p_gap=0.06),
    lambda o: o["d"]["10"].update(selected=None),
    lambda o: o["d"]["20"].update(teams_excluded=2),
    lambda o: o.update(**{"pass": True}),
], ids=["p_same", "tally", "lolo", "p_gap", "d", "d_excluded", "pass"])
def test_tampered_trust_cell_goes_red(tamper):
    o = copy.deepcopy(_cell())
    tamper(o)
    assert ref.errors_of(o, _cell(), "x")
