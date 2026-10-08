"""
M4g4c null calibration of criteria (1) and (2) (report only; stats-reviewer, M4g4c output review).

How often do the possession criteria fail when possession has no effect? Per Defending DNA cell,
on the raw (β = 0) values: within each league, team labels are permuted (default_rng(SEED)), and
every row takes the permuted team's mean proxy on both proxies (a tiny jitter, 1e-9, breaks the
within-team ties). This keeps the team clustering of the rows and removes any possession link.
Criterion (1) and (2) are then computed as in spikes/m4g4_possession.py (mean within-league
Spearman ρ; mean |Q5 − Q1| local-percentile gap), and a shuffle "fails" a criterion when either
proxy fails it (|ρ| ≥ 0.1, gap > 10), the rule the selection applies.

Writes data/interim/m4g4c/null_calibration.json and prints one line per cell. Changes nothing.
Never imports eleveniq (tests/test_spikes_independent.py).

  .venv/bin/python spikes/m4g4c_null_calibration.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g4_possession as eng  # noqa: E402

SEED, SHUFFLES, JITTER = 20261010, 400, 1e-9
OUT = eng.OUT_DIR / "null_calibration.json"


def shuffle_once(c: eng.Cell, pct: np.ndarray, rng: np.random.Generator) -> tuple[dict, dict]:
    """(mean ρ, mean gap) per proxy for one within-league permutation of team labels."""
    rho = {p: [] for p in eng.PROXIES}
    gap = {p: [] for p in eng.PROXIES}
    for lg in eng.POOL:
        s = np.flatnonzero(c.comp == lg)
        teams = np.unique(c.team[s])
        perm = dict(zip(teams.tolist(), rng.permutation(teams).tolist()))
        jit = rng.normal(0.0, JITTER, len(s))
        for p in eng.PROXIES:
            tmean = {t: float(c.proxy[p][s][c.team[s] == t].mean()) for t in teams.tolist()}
            prox = np.array([tmean[perm[t]] for t in c.team[s].tolist()]) + jit
            rk = pl.Series(prox).rank("average").to_numpy()
            q = np.ceil(5 * rk / len(s)).astype(int)
            gap[p].append(abs(float(pct[s][q == 5].mean()) - float(pct[s][q == 1].mean())))
            rho[p].append(pl.DataFrame({"a": prox, "b": c.p90[s]})
                          .select(pl.corr("a", "b", method="spearman")).item())
    return ({p: float(np.mean(v)) for p, v in rho.items()}, {p: float(np.mean(v)) for p, v in gap.items()})


def main() -> None:
    d = eng.Data()
    rng = np.random.default_rng(SEED)
    out = []
    for g in eng.GROUPS:
        for m in eng.DEFENDING[g]:
            c = eng.Cell(d, m, g)
            pct = c.local_pct(c.p90)
            rhos, gaps = [], []
            for _ in range(SHUFFLES):
                r, gp = shuffle_once(c, pct, rng)
                rhos.append(r)
                gaps.append(gp)
            g_dur = np.array([x["poss_dur_loo"] for x in gaps])
            fail2 = np.mean([any(x[p] > eng.GAP_MAX for p in eng.PROXIES) for x in gaps])
            fail1 = np.mean([any(abs(x[p]) >= eng.RHO_MAX for p in eng.PROXIES) for x in rhos])
            rec = {"group": g, "metric": m, "n": int(len(c.p90)),
                   "median_gap_dur": float(np.median(g_dur)),
                   "p_fail_c2_either_proxy": float(fail2), "p_fail_c1_either_proxy": float(fail1),
                   "sd_mean_rho_dur": float(np.std([x["poss_dur_loo"] for x in rhos], ddof=1))}
            out.append(rec)
            print(f"{g:9s} {m:22s} n={rec['n']:3d}  median gap {rec['median_gap_dur']:5.1f}  "
                  f"P(fail 2) {fail2:.2f}  P(fail 1) {fail1:.2f}", flush=True)
    OUT.write_text(json.dumps({"generated_by": "spikes/m4g4c_null_calibration.py", "seed": SEED,
                               "shuffles": SHUFFLES, "cells": out}, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
