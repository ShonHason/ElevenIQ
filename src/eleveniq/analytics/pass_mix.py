"""
Completion above expected (plan 006 pre-registration, user 2026-10-10; display only: no change
to DNA, similarity or the registry).

For one spec (1: length buckets; 2: length × direction):
- each league × position group pools completions / attempts per bucket over its cohort: gated
  rows of that primary group in a pool league with at least the group's pass_completion_pct
  min_attempts counted open-play passes;
- a row's expected completion is Σ_b (its share of counted passes in b) × that rate; the value
  is actual − expected, in percentage points. A bucket the league group never attempted has no
  rate: the row then has no value;
- the league percentile (Hazen, higher better) ranks the cohort; a cohort under
  MIN_COHORT_SIZE is not ranked;
- the check: the mean over the pool leagues of the within-league Spearman ρ between the value
  and the team's open-play pass share while on, on the cohort; the spec passes a group when
  |mean ρ| < PASS_MIX_MAX_RHO.

choose() takes spec 1, else spec 2 when spec 1 fails, else none (Passing stays withheld).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

import polars as pl

from eleveniq import config
from eleveniq.analytics.percentiles import hazen
from eleveniq.metrics.pass_mix import DIRECTIONS, LENGTHS, bucket_column

KEY = ("player_id", "team_id", "competition_id", "season_id", "source")
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
SPECS = (1, 2)
OK, NOT_IN_POOL, GOALKEEPER, BELOW_GATE, BELOW_MIN, COHORT_SMALL, NULL = (
    "ok", "not_in_pool", "goalkeeper", "below_gate", "below_min_attempts", "cohort_too_small",
    "null_value")


def buckets(spec: int) -> dict[str, tuple[str, ...]]:
    """Bucket name -> the length × direction cells it pools."""
    if spec == 1:
        return {ln: tuple(f"{ln}_{d}" for d in DIRECTIONS) for ln in LENGTHS}
    if spec == 2:
        return {f"{ln}_{d}": (f"{ln}_{d}",) for ln in LENGTHS for d in DIRECTIONS}
    raise ValueError(f"unknown spec {spec}")


@dataclass(frozen=True)
class RowResult:
    value: float | None          # percentage points
    actual: float | None         # percent
    expected: float | None       # percent
    counted: int                 # counted (known-outcome) open-play passes
    pct_local: float | None
    n_local: int | None
    status: str


@dataclass(frozen=True)
class GroupCheck:
    rhos: dict[int, float | None]     # competition_id -> within-league Spearman ρ
    mean_rho: float | None
    passes: bool


def _counts(r: Mapping, cells: Iterable[str]) -> tuple[int, int]:
    att = sum(r[bucket_column(*c.split("_"), "att")] for c in cells)
    cmp_ = sum(r[bucket_column(*c.split("_"), "cmp")] for c in cells)
    return att, cmp_


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx = pl.Series(x).rank("average")
    ry = pl.Series(y).rank("average")
    c = pl.DataFrame({"a": rx, "b": ry}).select(pl.corr("a", "b")).item()
    # A constant input has no rank correlation: polars returns NaN; undefined is None.
    return None if c is None or math.isnan(c) else float(c)


def possession_share(r: Mapping) -> float | None:
    """The team's open-play pass share while the player was on (the pre-registered proxy)."""
    t, o = r["team_open_play_passes_while_on"], r["opponent_open_play_passes_while_on"]
    return None if t is None or o is None or t + o == 0 else t / (t + o)


def compute(season: pl.DataFrame, mix: pl.DataFrame, spec: int,
            min_attempts: Mapping[str, int]) -> tuple[dict[tuple, RowResult], dict[str, GroupCheck]]:
    """Every season row's result for one spec, and the check per group."""
    bk = buckets(spec)
    mixed = {tuple(r[c] for c in KEY): r for r in mix.iter_rows(named=True)}
    rows = {tuple(r[c] for c in KEY): r for r in season.iter_rows(named=True)}
    counts: dict[tuple, dict[str, tuple[int, int]]] = {}
    for k in rows:
        m = mixed.get(k)
        counts[k] = {b: (_counts(m, cells) if m else (0, 0)) for b, cells in bk.items()}

    def counted(k: tuple) -> int:
        return sum(a for a, _ in counts[k].values())

    def in_cohort(k: tuple) -> bool:
        r = rows[k]
        g = r["position_group"]
        return (g in GROUPS and r["in_percentile_pool"] and r["meets_min_minutes"]
                and counted(k) >= min_attempts[g])

    # Bucket rates per league × group, pooled over the cohort.
    pooled: dict[tuple, dict[str, list[int]]] = {}
    for k in rows:
        if in_cohort(k):
            r = rows[k]
            acc = pooled.setdefault((r["competition_id"], r["position_group"]),
                                    {b: [0, 0] for b in bk})
            for b, (a, c) in counts[k].items():
                acc[b][0] += a
                acc[b][1] += c
    rate = {lg: {b: (c / a if a else None) for b, (a, c) in acc.items()}
            for lg, acc in pooled.items()}

    values: dict[tuple, tuple[float | None, float | None, float | None]] = {}
    for k, r in rows.items():
        g, n = r["position_group"], counted(k)
        lg = (r["competition_id"], g)
        if g not in GROUPS or not r["in_percentile_pool"] or n == 0 or lg not in rate:
            values[k] = (None, None, None)
            continue
        if any(a and rate[lg][b] is None for b, (a, _) in counts[k].items()):
            values[k] = (None, None, None)
            continue
        actual = 100.0 * sum(c for _, c in counts[k].values()) / n
        expected = 100.0 * sum(a / n * rate[lg][b] for b, (a, _) in counts[k].items() if a)
        values[k] = (actual - expected, actual, expected)

    # League percentiles over the cohort.
    pct: dict[tuple, tuple[float | None, int]] = {}
    for lg in pooled:
        members = [k for k in rows if in_cohort(k)
                   and (rows[k]["competition_id"], rows[k]["position_group"]) == lg
                   and values[k][0] is not None]
        pop = pl.Series([values[k][0] for k in members], dtype=pl.Float64)
        if len(members) < config.MIN_COHORT_SIZE:
            for k in members:
                pct[k] = (None, len(members))
            continue
        ranked = hazen(pop, pop)
        for k, p in zip(members, ranked):
            pct[k] = (p, len(members))

    out: dict[tuple, RowResult] = {}
    for k, r in rows.items():
        v, act, exp = values[k]
        p, n = pct.get(k, (None, None))
        g = r["position_group"]
        if g == "GK":
            st = GOALKEEPER
        elif not r["in_percentile_pool"]:
            st = NOT_IN_POOL
        elif not r["meets_min_minutes"]:
            st = BELOW_GATE
        elif counted(k) < min_attempts[g]:
            st = BELOW_MIN
        elif v is None:
            st = NULL
        elif p is None:
            st = COHORT_SMALL
        else:
            st = OK
        out[k] = RowResult(v, act, exp, counted(k), p, n, st)

    checks: dict[str, GroupCheck] = {}
    for g in GROUPS:
        rhos: dict[int, float | None] = {}
        for comp in sorted({c for c, gg in pooled if gg == g}):
            # The ranked population: cohort rows with a percentile (cohort ≥ MIN_COHORT_SIZE).
            ks = [k for k in rows if in_cohort(k) and rows[k]["competition_id"] == comp
                  and rows[k]["position_group"] == g and out[k].pct_local is not None
                  and possession_share(rows[k]) is not None]
            rhos[comp] = _spearman([out[k].value for k in ks],          # type: ignore[misc]
                                   [possession_share(rows[k]) for k in ks])  # type: ignore[misc]
        defined = [x for x in rhos.values() if x is not None]
        mean = sum(defined) / len(defined) if defined and len(defined) == len(rhos) else None
        checks[g] = GroupCheck(rhos, mean, mean is not None and abs(mean) < config.PASS_MIX_MAX_RHO)
    return out, checks


@dataclass(frozen=True)
class Choice:
    spec: int | None                       # the spec the group's Passing score uses, or None
    checks: dict[int, GroupCheck]          # spec -> check (spec 2 only when spec 1 failed)


def choose(season: pl.DataFrame, mix: pl.DataFrame, min_attempts: Mapping[str, int]
           ) -> tuple[dict[str, Choice], dict[int, dict[tuple, RowResult]]]:
    """Spec 1 per group; spec 2 only where spec 1 fails; none where both fail."""
    res1, chk1 = compute(season, mix, 1, min_attempts)
    results = {1: res1}
    choice: dict[str, Choice] = {}
    need2 = [g for g in GROUPS if not chk1[g].passes]
    chk2: dict[str, GroupCheck] = {}
    if need2:
        res2, chk2 = compute(season, mix, 2, min_attempts)
        results[2] = res2
    for g in GROUPS:
        if chk1[g].passes:
            choice[g] = Choice(1, {1: chk1[g]})
        else:
            choice[g] = Choice(2 if chk2[g].passes else None, {1: chk1[g], 2: chk2[g]})
    return choice, results
