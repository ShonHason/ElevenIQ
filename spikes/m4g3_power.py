"""
M4g3a power simulation of the effective mover rule (plan 005 M4g3, ADR 004 §13.7).

Run before the real mover run; the figures go into ADR 004 §13.7 before any mover change is
computed. Never imports eleveniq (tests/test_spikes_independent.py).

The rule (mover_rule) is the one the real run uses (spikes/m4g3_movers.py imports it):
- per mover i: d2_i = Δ_i² (scale b), and its R matched non-mover draws (halves h1, h2);
  baseline_i = mean over draws of (h1 − h2)²;
- Ŝ² = mean_i d2_i / mean_i baseline_i (the RMS form of S);
- R_m = Pearson of the matched halves pooled over all n × R draws (stats-reviewer, M4g3a
  pre-code review, condition 2.1: pooled, not a mean of per-draw n-pair correlations);
- f̂ = (Ŝ² − 1)(1 − R_m) / R_m;
- paired bootstrap over movers (B resamples; each resample takes the movers' Δ² and their own
  draws, and recomputes R_m from those draws, condition 2.2); percentile CI, two-sided 95%
  (numpy linear quantiles at 0.025 / 0.975, condition 2.3);
- verdict: PASS iff the 97.5th percentile < 0.5; FAIL iff the 2.5th percentile > 0.5;
  otherwise "inconclusive"; fewer than 12 movers → "untestable". The point rule (f̂ ≤ 0.5) is
  reported for comparison only.
- power_at_f0(n, R_m): P(PASS | f = 0) at a metric's own n and R_m, the low-power label's input
  (ADR 004 §13.7: computed before f̂).

Each simulation cell has its own RNG stream, seeded from SEED and the cell's parameters (not its
position in the grid), so editing the grid leaves the other cells' figures unchanged.

Simulation model (value = player + team + noise, SD units, stable variance 1):
- non-mover: one stable value s (player + team) shared by both halves; Δ_b = e1 − e2;
- mover: player p shared, teams t1, t2 independent with Var(t) = f, Var(p) = 1 − f;
  Δ_m = t1 − t2 + e1 − e2. Then (S² − 1)(1 − R) / R = f for the population quantities; the
  estimator (a ratio of sums with a skewed numerator) is only approximately unbiased: the point
  rule passes 0.54–0.56 at f = 0.5.
- homogeneous (primary): every half has the same noise, Var(e) = (1 − R)/R, so the half
  reliability is R; one pool of N_POOL non-movers, each with one fixed pair of halves that
  every mover drawing it shares;
- t(5) (sensitivity): noise from a unit-variance t(5);
- real minutes (sensitivity, condition 2.7): the 24 real movers' (m1, m2) and group, each
  drawing from its own eligible pool (same-group single-stint non-movers in the pool leagues
  with ≥ m1 + m2 nominal minutes, at their real minutes); per-match noise, halves of the first
  round(m1/90) and the next round(m2/90) matches; the per-match variance is set so the pooled
  R_m equals the target. The calibration uses the geometric mean of the two halves'
  reliabilities while the baseline uses 1/a + 1/b, so f̂ is slightly biased low when a ≠ b
  (small: the figures stay within 2 pp of the homogeneous ones).
- stints are in date order (first match played), as in the real run.

Known limit (condition 2.9): t1 and t2 are independent draws. Real moves are not random (many
second stints are at struggling clubs); the model cannot represent that.

Usage:
  .venv/bin/python spikes/m4g3_power.py                  # full grid → data/interim/m4g3_power.json
  .venv/bin/python spikes/m4g3_power.py --datasets 200   # quick run
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reference_totals import PROJECT_ROOT  # noqa: E402

SEED = 20261008
THRESHOLD = 0.5
DRAWS = 200                      # R, matched non-mover draws per mover (plan 005 M4g3)
B = 2000                         # paired bootstrap resamples (plan 005 M4g3)
DATASETS = 2000                  # simulated datasets per cell (MC SE ≤ 1.1 pp)
N_POOL = 250                     # homogeneous model: non-mover pool size
CI_LO, CI_HI = 0.025, 0.975
F_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
MIN_MOVERS = 12                  # fewer → "untestable", not a pass (ADR 004 §13.1)
LOW_POWER = 0.8                  # P(PASS | f = 0) < 0.8 → "underpowered at n", label only (§13.7)
POOL_LEAGUES = (2, 7, 11, 12)    # m4g_common.POOL (ADR 003)
SEASON_ID = 27
MOVER_FLOOR = 450.0
GATE = 900.0
GATE_TOL = 1e-6
OUT = PROJECT_ROOT / "data" / "interim" / "m4g3_power.json"


# ---------------------------------------------------------------- the rule (shared with the real run)

def _moments(h1: np.ndarray, h2: np.ndarray) -> np.ndarray:
    """Per mover: [Σh1, Σh2, Σh1², Σh2², Σh1h2, count] over its draws. h1, h2: (n, R)."""
    return np.stack([h1.sum(1), h2.sum(1), (h1 * h1).sum(1), (h2 * h2).sum(1),
                     (h1 * h2).sum(1), np.full(h1.shape[0], h1.shape[1], dtype=float)], axis=1)


def _f_hat(d2_sum: np.ndarray, base_sum: np.ndarray, mom: np.ndarray) -> tuple[np.ndarray, ...]:
    """f̂, Ŝ², R_m from summed quantities (any leading shape; mom's last axis is the 6 moments)."""
    sx, sy, sxx, syy, sxy, c = (mom[..., k] for k in range(6))
    cov = sxy - sx * sy / c
    vx, vy = sxx - sx * sx / c, syy - sy * sy / c
    with np.errstate(divide="ignore", invalid="ignore"):
        r = cov / np.sqrt(vx * vy)
        s2 = d2_sum / base_sum
        f = np.where(r > 0, (s2 - 1.0) * (1.0 - r) / r, np.inf)
    return f, s2, r


def _quantile(x: np.ndarray, q: float) -> float:
    """numpy's linear quantile, with +inf kept as +inf (numpy gives inf − inf = NaN when both
    bracketing values are +inf). x holds finite values and +inf only."""
    v = np.sort(x)
    pos = q * (len(v) - 1)
    i = math.floor(pos)
    frac = pos - i
    lo_v = v[i]
    hi_v = v[min(i + 1, len(v) - 1)]
    if math.isinf(lo_v) or (frac > 0 and math.isinf(hi_v)):
        return math.inf
    return float(lo_v + (hi_v - lo_v) * frac)


def verdict_of(lo: float, hi: float, threshold: float = THRESHOLD) -> str:
    """PASS iff the CI upper bound < threshold; FAIL iff the lower bound > threshold; else
    "inconclusive" (ADR 004 §13.7). A bound equal to the threshold is inconclusive."""
    if hi < threshold:
        return "PASS"
    if lo > threshold:
        return "FAIL"
    return "inconclusive"


@dataclass(frozen=True)
class RuleResult:
    n: int
    f_hat: float                 # NaN when untestable; +inf when R_m ≤ 0
    s2: float
    r_m: float
    ci_lo: float
    ci_hi: float
    verdict: str                 # "PASS" | "FAIL" | "inconclusive" | "untestable"
    point_pass: bool             # f̂ ≤ 0.5, comparison only
    boot_inf: int                # bootstrap resamples with R_m ≤ 0 (f̂ = +inf)


def mover_rule(d2: np.ndarray, h1: np.ndarray, h2: np.ndarray, rng: np.random.Generator,
               b: int = B, threshold: float = THRESHOLD) -> RuleResult:
    """The M4g3 effective rule. d2: (n,) movers' Δ²; h1, h2: (n, R) each mover's matched halves.

    The caller demeans the halves (league × group) and applies min_attempts first.
    - n < MIN_MOVERS → "untestable" (ADR 004 §13.1), not a pass; the rng is not used.
    - Non-finite d2, h1 or h2, mismatched shapes, or a mover whose baseline is 0 → ValueError
      (a failed upstream step must not surface as "inconclusive").
    - R_m ≤ 0 (no stable between-player signal) → f̂ = +inf, in the point estimate and in each
      resample; +inf is never a PASS, and a CI lower bound of +inf is a FAIL.
    The bootstrap draws rng.integers(0, n, (b, n)) once, so the RNG stream is fixed by (n, b).
    """
    d2 = np.asarray(d2, dtype=float)
    h1 = np.asarray(h1, dtype=float)
    h2 = np.asarray(h2, dtype=float)
    if d2.ndim != 1 or h1.ndim != 2 or h1.shape != h2.shape or h1.shape[0] != d2.shape[0]:
        raise ValueError(f"shapes d2 {d2.shape}, h1 {h1.shape}, h2 {h2.shape}")
    if not (np.isfinite(d2).all() and np.isfinite(h1).all() and np.isfinite(h2).all()):
        raise ValueError("non-finite mover change or baseline half")
    n = d2.shape[0]
    if n < MIN_MOVERS:
        nan = math.nan
        return RuleResult(n, nan, nan, nan, nan, nan, "untestable", False, 0)
    base = ((h1 - h2) ** 2).mean(1)
    if (base <= 0).any():
        raise ValueError("a mover's matched halves are identical in every draw (baseline 0)")
    mom = _moments(h1, h2)
    f, s2, r = _f_hat(d2.sum(), base.sum(), mom.sum(0))
    idx = rng.integers(0, n, (b, n))
    fb, _, _ = _f_hat(d2[idx].sum(1), base[idx].sum(1), mom[idx].sum(1))
    if np.isnan(fb).any() or np.isnan(f):
        raise ValueError("f̂ is NaN")
    lo, hi = _quantile(fb, CI_LO), _quantile(fb, CI_HI)
    return RuleResult(n, float(f), float(s2), float(r), lo, hi, verdict_of(lo, hi, threshold),
                      bool(f <= threshold), int(np.isinf(fb).sum()))


# ---------------------------------------------------------------- simulation

def _noise(rng: np.random.Generator, shape, sd: float, dist: str) -> np.ndarray:
    if dist == "normal":
        return rng.normal(0.0, sd, shape)
    if dist == "t5":
        return rng.standard_t(5, shape) * sd / math.sqrt(5 / 3)
    raise ValueError(dist)


def sim_homogeneous(rng: np.random.Generator, n: int, rel: float, f: float,
                    dist: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    e_sd = math.sqrt((1 - rel) / rel)
    s = rng.normal(0.0, 1.0, N_POOL)
    h1p = s + _noise(rng, N_POOL, e_sd, dist)
    h2p = s + _noise(rng, N_POOL, e_sd, dist)
    t = rng.normal(0.0, math.sqrt(f), (2, n))
    d = t[0] - t[1] + _noise(rng, n, e_sd, dist) - _noise(rng, n, e_sd, dist)
    j = rng.integers(0, N_POOL, (n, DRAWS))
    return d * d, h1p[j], h2p[j]


@dataclass(frozen=True)
class RealDesign:
    groups: list[str]            # per mover
    a: np.ndarray                # matches in stint 1 (round(m1/90))
    b: np.ndarray                # matches in stint 2
    pool_minutes: dict[str, np.ndarray]   # group → non-mover nominal minutes
    pool_of: list[np.ndarray]    # per mover: indices into its group's pool (eligible)
    m1: np.ndarray
    m2: np.ndarray


def order_by_date(stints: list[dict]) -> list[dict]:
    """A mover's stints in date order: by first match played, then last match, then team_id."""
    return sorted(stints, key=lambda s: (s["first"], s["last"], s["team_id"]))


def load_real_design() -> RealDesign:
    import polars as pl
    import m4g_movers_display as mvd   # sibling spike: stint dates from the player-match rows
    path = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
    d = pl.read_parquet(path).filter(pl.col("season_id") == SEASON_ID)
    st = d.filter((pl.col("minutes_nominal_equiv") >= MOVER_FLOOR - GATE_TOL)
                  & (pl.col("position_group") != "GK"))
    g = (st.group_by("player_id")
         .agg(pl.len().alias("n"), pl.col("position_group").n_unique().alias("ng"),
              pl.col("position_group").first().alias("grp"),
              pl.col("team_id"), pl.col("competition_id"),
              pl.col("minutes_nominal_equiv").alias("mins")))
    mv = g.filter((pl.col("n") == 2) & (pl.col("ng") == 1)).sort("player_id")
    keys = {(p, t, c) for p, ts, cs in zip(mv["player_id"], mv["team_id"], mv["competition_id"])
            for t, c in zip(ts, cs)}
    dates = mvd.stint_dates_from_matches(mvd.MATCH_PARQUET, keys)
    single = d.group_by("player_id").len().filter(pl.col("len") == 1)["player_id"]
    nm = d.filter(pl.col("player_id").is_in(single.implode())
                  & pl.col("competition_id").is_in(POOL_LEAGUES)
                  & (pl.col("minutes_nominal_equiv") >= GATE - GATE_TOL)
                  & (pl.col("position_group") != "GK"))
    pool_minutes = {grp: np.sort(nm.filter(pl.col("position_group") == grp)
                                 ["minutes_nominal_equiv"].to_numpy())
                    for grp in sorted(set(mv["grp"]))}
    groups, m1, m2, pool_of = [], [], [], []
    for pid, grp, ts, cs, mins in zip(mv["player_id"], mv["grp"], mv["team_id"],
                                      mv["competition_id"], mv["mins"]):
        stints = order_by_date([{"team_id": t, "first": dates[(pid, t, c)][0],
                                 "last": dates[(pid, t, c)][1], "mins": float(m)}
                                for t, c, m in zip(ts, cs, mins)])
        x, y = stints[0]["mins"], stints[1]["mins"]
        groups.append(grp)
        m1.append(x)
        m2.append(y)
        pool_of.append(np.flatnonzero(pool_minutes[grp] >= x + y - GATE_TOL))
    m1a, m2a = np.array(m1), np.array(m2)
    a = np.maximum(1, np.rint(m1a / 90)).astype(int)
    b = np.maximum(1, np.rint(m2a / 90)).astype(int)
    return RealDesign(groups, a, b, pool_minutes, pool_of, m1a, m2a)


def _calibrate(a: np.ndarray, b: np.ndarray, rel: float) -> float:
    """Per-match noise variance σ² so the pooled R_m = 1/√((1+σ²·mean 1/a)(1+σ²·mean 1/b)) = rel."""
    ia, ib = float(np.mean(1 / a)), float(np.mean(1 / b))
    lo, hi = 0.0, 1e4
    for _ in range(200):
        mid = (lo + hi) / 2
        if 1 / math.sqrt((1 + mid * ia) * (1 + mid * ib)) > rel:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sim_real(rng: np.random.Generator, des: RealDesign, rel: float,
             f: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sig = math.sqrt(_calibrate(des.a, des.b, rel))
    n = len(des.groups)
    h1 = np.empty((n, DRAWS))
    h2 = np.empty((n, DRAWS))
    cum: dict[str, np.ndarray] = {}
    stable: dict[str, np.ndarray] = {}
    for grp, mins in des.pool_minutes.items():
        k = np.maximum(1, np.rint(mins / 90)).astype(int)
        e = rng.normal(0.0, sig, (len(mins), int(k.max()) + 2))
        cum[grp] = np.concatenate([np.zeros((len(mins), 1)), np.cumsum(e, 1)], 1)
        stable[grp] = rng.normal(0.0, 1.0, len(mins))
    for i, grp in enumerate(des.groups):
        j = des.pool_of[i][rng.integers(0, len(des.pool_of[i]), DRAWS)]
        a, b = des.a[i], des.b[i]
        c = cum[grp][j]
        h1[i] = stable[grp][j] + c[:, a] / a
        h2[i] = stable[grp][j] + (c[:, a + b] - c[:, a]) / b
    t = rng.normal(0.0, math.sqrt(f), (2, n))
    d = (t[0] - t[1] + rng.normal(0.0, sig, n) / np.sqrt(des.a)
         - rng.normal(0.0, sig, n) / np.sqrt(des.b))
    return d * d, h1, h2


def cell_rng(*params: object) -> np.random.Generator:
    """The RNG of one simulation cell, seeded from SEED and the cell's parameters."""
    key = "|".join(repr(p) for p in params).encode()
    return np.random.default_rng([SEED, zlib.crc32(key)])


def power_at_f0(n: int, r_m: float, datasets: int = DATASETS, b: int = B) -> float:
    """P(PASS | f = 0) of the effective rule at n movers and reliability R_m (homogeneous model).
    The input of the low-power label (ADR 004 §13.7). n < MIN_MOVERS or R_m ≤ 0 → 0.0."""
    n, r_m = int(n), float(r_m)        # numpy scalars would change the seed key (repr)
    if n < MIN_MOVERS or not r_m > 0:
        return 0.0
    rel = min(r_m, 0.999)
    return run_cell(cell_rng("power_at_f0", n, round(rel, 6), datasets, b), datasets, b,
                    lambda rng: sim_homogeneous(rng, n, rel, 0.0, "normal"))["p_pass_effective"]


def low_power(n: int, r_m: float, datasets: int = DATASETS, b: int = B) -> dict:
    """The low-power label (ADR 004 §13.7): power_at_f0 with its Monte Carlo SE, so a label near
    LOW_POWER can be read against its simulation noise."""
    p = power_at_f0(n, r_m, datasets, b)
    return {"power_at_f0": p, "se": math.sqrt(p * (1 - p) / datasets),
            "underpowered": p < LOW_POWER}


def run_cell(rng: np.random.Generator, datasets: int, b: int, simulate) -> dict:
    eff = point = fail = 0
    r_ms = []
    for _ in range(datasets):
        d2, h1, h2 = simulate(rng)
        res = mover_rule(d2, h1, h2, rng, b=b)
        eff += res.verdict == "PASS"
        fail += res.verdict == "FAIL"
        point += res.point_pass
        r_ms.append(res.r_m)
    se = lambda p: math.sqrt(p * (1 - p) / datasets)  # noqa: E731
    p_eff, p_pt, p_fail = eff / datasets, point / datasets, fail / datasets
    return {"p_pass_effective": p_eff, "se_effective": se(p_eff),
            "p_pass_point": p_pt, "se_point": se(p_pt),
            "p_fail": p_fail, "p_inconclusive": 1 - p_eff - p_fail,
            "mean_r_m": float(np.mean(r_ms)), "datasets": datasets, "b": b}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--datasets", type=int, default=DATASETS)
    ap.add_argument("--b", type=int, default=B)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    cells = []
    for n in (24, 15):
        for rel in (0.7, 0.5):
            for f in F_GRID:
                cells.append(("primary", "normal", n, rel, f))
    for n in (24, 15):
        for f in F_GRID:
            cells.append(("sens_r085", "normal", n, 0.85, f))
            cells.append(("sens_t5", "t5", n, 0.7, f))
    des = load_real_design()
    for rel in (0.7, 0.5):
        for f in F_GRID:
            cells.append(("sens_real_minutes", "normal", len(des.groups), rel, f))

    rows = []
    t0 = time.time()
    for block, dist, n, rel, f in cells:
        if block == "sens_real_minutes":
            sim = lambda rng, rel=rel, f=f: sim_real(rng, des, rel, f)  # noqa: E731
        else:
            sim = lambda rng, n=n, rel=rel, f=f, dist=dist: sim_homogeneous(rng, n, rel, f, dist)  # noqa: E731
        res = run_cell(cell_rng(block, dist, n, rel, f, args.datasets, args.b),
                       args.datasets, args.b, sim)
        rows.append({"block": block, "noise": dist, "n": n, "r_m": rel, "f": f, **res})
        print(f"{block:18s} {dist:6s} n={n:2d} R={rel:.2f} f={f:.2f}  "
              f"eff={res['p_pass_effective']:.3f}±{res['se_effective']:.3f}  "
              f"point={res['p_pass_point']:.3f}  fail={res['p_fail']:.3f}  "
              f"inc={res['p_inconclusive']:.3f}  R̂={res['mean_r_m']:.3f}  "
              f"[{time.time() - t0:.0f}s]", flush=True)

    low = {f"n={r['n']} R={r['r_m']}": {"power_at_f0": r["p_pass_effective"],
                                         "se": r["se_effective"],
                                         "underpowered": r["p_pass_effective"] < LOW_POWER}
           for r in rows if r["block"] == "primary" and r["f"] == 0.0}
    real = {"movers": len(des.groups), "m1": des.m1.tolist(), "m2": des.m2.tolist(),
            "groups": des.groups, "pool_sizes": [int(len(p)) for p in des.pool_of]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "generated_by": "spikes/m4g3_power.py", "seed": SEED, "draws": DRAWS,
        "n_pool_homogeneous": N_POOL, "ci": [CI_LO, CI_HI], "threshold": THRESHOLD,
        "low_power_threshold": LOW_POWER, "low_power_at_f0": low,
        "real_design": real, "cells": rows}, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
