"""
Multi-split split-half reliability: the measurement engine (plan 005 M4g2, ADR 004 §13.1).

Used by spikes/m4_decision_measurements.py. The independent check is
spikes/m4g2_reliability_reference.py, which shares only the written spec (plan 005 M4g2,
"Split assignment"), not this code: this engine is vectorised over metrics within a split,
the reference over splits within a cell.

Spec (plan 005 M4g2; user 2026-10-07; stats-reviewer conditions):
- A stint is a pool outfield season key row; its matches are its rows with minutes > 0,
  ordered by (match_date, match_id). Stints are visited in ascending key order.
- One generator, default_rng(SEED). For split k = 0 … K−1, for each stint:
  perm = rng.permutation(n), then c = rng.integers(0, 2) only when n is odd. Half 0 takes
  positions perm[:h0], h0 = n // 2 + c (odd n) or n / 2 (even n). Every stint draws.
- One assignment per split serves every metric, ratio, team_pass_share and bucket.
- ALL_GROUPS pools every group (report only), still demeaned within league × group.
- Per split and cell: half values demeaned within league × group over exactly the rows that enter
  r_k (both halves defined), pooled Pearson r_k, then Spearman-Brown r_full,k = 2r/(1+r).
  Stored: mean r_full,k, SD, MC SE = SD / √K, mean r_k.
- K starts at K_START and doubles (continuing the same stream, which equals a rerun from
  the seed) until every judged gated cell's MC SE ≤ MC_SE_TARGET, capped at K_CAP.
- r_k undefined (n < 3 or zero variance) in every split → null; in some but not all → fail.

No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

SEED = 20261007
ALL_GROUPS = "ALL"
K_START = 200
K_CAP = 3200
MC_SE_TARGET = 0.002


@dataclass(frozen=True)
class Measure:
    """One split-half quantity: numerator column / denominator (Σ plus − Σ minus).

    per90: value = num / minutes × 90, defined when the half has matches.
    ratio / share: value = num / den, defined when den > 0 in the half.
    """
    name: str
    kind: str                      # "per90" | "ratio" | "share"
    num: str
    plus: tuple[str, ...]
    minus: tuple[str, ...] = ()


@dataclass
class Data:
    """Match rows grouped by stint (rows of a stint contiguous, in the spec's order)."""
    columns: list[str]
    values: np.ndarray             # rows × columns, float64
    starts: np.ndarray             # first row of each stint
    n: np.ndarray                  # matches per stint
    league: np.ndarray             # competition_id per stint
    group: np.ndarray              # position_group per stint (str)
    # demeaning key per stint: league × group (within one group it is the league)
    populations: dict[str, np.ndarray]   # name → bool per stint

    @property
    def cell(self) -> np.ndarray:
        groups = {g: i for i, g in enumerate(sorted(set(self.group.tolist())))}
        return self.league.astype(np.int64) * 100 + np.array([groups[g] for g in self.group.tolist()])


@dataclass
class Result:
    k: int
    reached_cap: bool
    # (population, group, measure) → per-split arrays
    r_full: dict[tuple[str, str, str], list[float]] = field(default_factory=dict)
    r_raw: dict[tuple[str, str, str], list[float]] = field(default_factory=dict)
    n_rows: dict[tuple[str, str, str], list[int]] = field(default_factory=dict)
    present_all: dict[tuple[str, str, str], np.ndarray] = field(default_factory=dict)
    present_any: dict[tuple[str, str, str], np.ndarray] = field(default_factory=dict)

    def summary(self, cell: tuple[str, str, str], *, allow_partial: bool = False) -> dict:
        """mean, SD, MC SE of r_full,k; mean r_k; n range; share of rows present in every split.
        r undefined in some splits but not all fails, unless allow_partial (then the statistics are
        over the defined splits and n_undefined says how many were not)."""
        rf = np.array(self.r_full[cell], dtype=float)
        rr = np.array(self.r_raw[cell], dtype=float)
        n = np.array(self.n_rows[cell])
        undefined = np.isnan(rf)
        if undefined.any() and not undefined.all() and not allow_partial:
            raise SystemExit(f"FAIL: r undefined in {int(undefined.sum())} of {len(rf)} splits for {cell}")
        if undefined.all():
            mean = sd = se = r_mean = None
        else:
            d = rf[~undefined]
            mean = float(d.mean())
            sd = float(d.std(ddof=1)) if len(d) > 1 else 0.0
            se = sd / math.sqrt(len(d))
            r_mean = float(rr[~undefined].mean())
        anyp = int(self.present_any[cell].sum())
        return {"r_full": mean, "sd": sd, "mc_se": se, "r_k_mean": r_mean,
                "n_undefined": int(undefined.sum()) if not undefined.all() else len(rf),
                "n_min": int(n.min()), "n_median": float(np.median(n)), "n_max": int(n.max()),
                "share_present_every_split": (int(self.present_all[cell].sum()) / anyp) if anyp else None}


def spearman_brown(r: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(r > -1, 2 * r / (1 + r), np.nan)


def half_assignment(n: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One split: a bool per match row (stint-contiguous), True = half 0. Draw order per spec."""
    out = np.empty(int(n.sum()), dtype=bool)
    pos = 0
    for ni in n.tolist():
        perm = rng.permutation(ni)
        h0 = ni // 2
        if ni % 2:
            h0 += int(rng.integers(0, 2))
        seg = np.zeros(ni, dtype=bool)
        seg[perm[:h0]] = True
        out[pos:pos + ni] = seg
        pos += ni
    return out


def _half_values(data: Data, measures: Sequence[Measure], half0: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray]:
    """A, B: stints × measures, NaN where the half's value is undefined."""
    col = {c: i for i, c in enumerate(data.columns)}
    m0 = half0.astype(float)[:, None]
    s0 = np.add.reduceat(data.values * m0, data.starts, axis=0)
    s1 = np.add.reduceat(data.values * (1.0 - m0), data.starts, axis=0)
    c0 = np.add.reduceat(half0.astype(np.int64), data.starts)
    c1 = data.n - c0
    A = np.full((len(data.n), len(measures)), np.nan)
    B = np.full_like(A, np.nan)
    for j, m in enumerate(measures):
        for s, cnt, out in ((s0, c0, A), (s1, c1, B)):
            num = s[:, col[m.num]]
            den = sum(s[:, col[c]] for c in m.plus) - (sum(s[:, col[c]] for c in m.minus) if m.minus else 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                if m.kind == "per90":
                    out[:, j] = np.where(cnt > 0, num / den * 90.0, np.nan)
                else:
                    out[:, j] = np.where(den > 0, num / den, np.nan)
    return A, B


def _pooled_r(A: np.ndarray, B: np.ndarray, key: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per column: demean within key (league × group) over rows with both halves, pooled Pearson.
    Returns (r, n, valid)."""
    valid = ~np.isnan(A) & ~np.isnan(B)
    a = np.where(valid, A, 0.0)
    b = np.where(valid, B, 0.0)
    da = np.zeros_like(a)
    db = np.zeros_like(b)
    for kv in np.unique(key):
        sel = key == kv
        v = valid[sel]
        cnt = v.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ma = np.where(cnt > 0, a[sel].sum(axis=0) / cnt, 0.0)
            mb = np.where(cnt > 0, b[sel].sum(axis=0) / cnt, 0.0)
        da[sel] = np.where(v, a[sel] - ma, 0.0)
        db[sel] = np.where(v, b[sel] - mb, 0.0)
    n = valid.sum(axis=0)
    saa, sbb, sab = (da * da).sum(axis=0), (db * db).sum(axis=0), (da * db).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where((n >= 3) & (saa > 0) & (sbb > 0), sab / np.sqrt(saa * sbb), np.nan)
    return r, n, valid


def run(data: Data, measures: Sequence[Measure], groups: Sequence[str],
        judged: Callable[[str, str], bool], *, seed: int = SEED, k_start: int = K_START,
        k_cap: int = K_CAP, target: float = MC_SE_TARGET, judged_population: str = "gate") -> Result:
    """All splits; K doubles until every judged (group, measure) cell of judged_population has
    MC SE ≤ target, or K reaches k_cap."""
    rng = np.random.default_rng(seed)
    names = [m.name for m in measures]
    res = Result(k=0, reached_cap=False)
    key = data.cell
    idx = {(p, g): np.flatnonzero(mask & ((data.group == g) if g != ALL_GROUPS else True))
           for p, mask in data.populations.items() for g in groups}
    for (p, g), rows in idx.items():
        for nm in names:
            res.r_full[(p, g, nm)] = []
            res.r_raw[(p, g, nm)] = []
            res.n_rows[(p, g, nm)] = []
            res.present_all[(p, g, nm)] = np.ones(len(rows), dtype=bool)
            res.present_any[(p, g, nm)] = np.zeros(len(rows), dtype=bool)
    k_goal = k_start
    while True:
        while res.k < k_goal:
            A, B = _half_values(data, measures, half_assignment(data.n, rng))
            for (p, g), rows in idx.items():
                r, n, valid = _pooled_r(A[rows], B[rows], key[rows])
                rf = spearman_brown(r)
                for j, nm in enumerate(names):
                    cell = (p, g, nm)
                    res.r_full[cell].append(float(rf[j]))
                    res.r_raw[cell].append(float(r[j]))
                    res.n_rows[cell].append(int(n[j]))
                    res.present_all[cell] &= valid[:, j]
                    res.present_any[cell] |= valid[:, j]
            res.k += 1
        worst = 0.0
        for g in groups:
            for nm in names:
                if g == ALL_GROUPS or not judged(g, nm):
                    continue
                rf = np.array(res.r_full[(judged_population, g, nm)])
                if np.isnan(rf).all():
                    continue
                worst = max(worst, float(np.nanstd(rf, ddof=1)) / math.sqrt(len(rf)))
        if worst <= target:
            return res
        if k_goal >= k_cap:
            res.reached_cap = True
            return res
        k_goal = min(k_goal * 2, k_cap)
