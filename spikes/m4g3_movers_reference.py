"""
Independent reference for the M4g3a mover run (plan 005 M4g3, ADR 004 §13.7).

Shares the written spec (spikes/m4g3_movers.py docstring, ADR 004 §13.7), not code, with
spikes/m4g3_movers.py and spikes/m4g3_power.py:
- mover and stint values from the season parquet's stored columns (*_p90, ratios,
  team_pass_share), not recomputed from counts; min_attempts from the counts;
- its own reference populations, Hazen, cut, half sums (a match loop), draws, R_m
  (np.corrcoef over the pooled halves), bootstrap (corrcoef per resample) and quantile;
- the same RNG streams (default_rng([20261008, crc32 of the metric key])), written out;
- low power: its own simulation (different stream, 1,000 datasets); a label must agree unless
  either estimate is within 4 combined SEs of 0.8, and the powers within 4 combined SEs.

  .venv/bin/python spikes/m4g3_movers_reference.py --check-output data/interim/m4g3_movers.json \
      --registry data/interim/m4g3_registry.json

Fails on any mismatch beyond TOL. No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "statsbomb"
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = ROOT / "data" / "interim" / "player_match_stats.parquet"
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"

SEASON, POOL = 27, (2, 7, 11, 12)
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
SEED, DRAWS, B, MIN_N, THRESHOLD, LOW = 20261008, 200, 2000, 12, 0.5, 0.8
FLOOR, GATE, EPS = 450.0, 900.0, 1e-6
TOL = 1e-9
POWER_DATASETS, POWER_SEED = 1000, 7
DEN = {  # ratio / share: (numerator, den+, den−); stored value column = metric name
    "pass_completion_pct": ("passes_completed", ("passes_attempted",),
                            ("passes_unknown_outcome", "passes_injury_clearance")),
    "long_pass_completion_pct": ("long_passes_completed", ("long_passes_attempted",),
                                 ("long_passes_unknown_outcome", "long_passes_injury_clearance")),
    "cross_completion_pct": ("crosses_completed", ("crosses_attempted",), ("crosses_unknown_outcome",)),
    "dribble_success_pct": ("dribbles_completed", ("dribbles_attempted",), ()),
    "tackle_success_pct": ("tackles_won", ("tackles_attempted",), ()),
    "aerial_win_pct": ("aerials_won", ("aerials_won", "aerials_lost"), ()),
    "defensive_duel_success_pct": ("tackles_won", ("tackles_attempted", "dribbled_past"), ()),
    "np_xg_per_shot": ("np_xg", ("np_shots",), ()),
    "team_pass_share": ("open_play_passes_attempted", ("team_open_play_passes_while_on",), ()),
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def stream(*key: str) -> np.random.Generator:
    return np.random.default_rng([SEED, zlib.crc32("|".join(repr(k) for k in key).encode())])


def terms(metric: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if metric in DEN:
        return DEN[metric]
    return metric.removesuffix("_p90"), ("minutes_played",), ()


def den_of(r: dict, metric: str) -> float:
    _, pos, neg = terms(metric)
    return sum(r[c] for c in pos) - sum(r[c] for c in neg)


def half_value(metric: str, sums: dict) -> tuple[float, float]:
    num, pos, neg = terms(metric)
    den = sum(sums[c] for c in pos) - sum(sums[c] for c in neg)
    if den <= 0:
        return math.nan, den
    return (sums[num] / den * 90.0 if metric.endswith("_p90") else sums[num] / den), den


def lin_quantile(x: np.ndarray, q: float) -> float:
    v = sorted(x.tolist())
    h = (len(v) - 1) * q
    lo = int(h)
    if lo + 1 >= len(v) or h == lo:
        return v[lo]
    if v[lo + 1] == math.inf:
        return math.inf
    return v[lo] + (h - lo) * (v[lo + 1] - v[lo])


def pct_hazen(pop: list[float], x: float) -> float:
    less = sum(1 for p in pop if p < x)
    eq = sum(1 for p in pop if p == x)
    return (less + 0.5 * eq) / len(pop) * 100.0


def f_of(d2s: float, bases: float, r: float) -> float:
    return (d2s / bases - 1.0) * (1.0 - r) / r if r > 0 else math.inf


def rule(d2: np.ndarray, h1: np.ndarray, h2: np.ndarray, rng: np.random.Generator, b: int) -> dict:
    base = np.mean((h1 - h2) ** 2, axis=1)
    r = float(np.corrcoef(h1.ravel(), h2.ravel())[0, 1])
    f = f_of(d2.sum(), base.sum(), r)
    n = len(d2)
    idx = rng.integers(0, n, (b, n))
    fb = np.empty(b)
    for k in range(b):
        sel = idx[k]
        rk = float(np.corrcoef(h1[sel].ravel(), h2[sel].ravel())[0, 1])
        fb[k] = f_of(d2[sel].sum(), base[sel].sum(), rk)
    lo, hi = lin_quantile(fb, 0.025), lin_quantile(fb, 0.975)
    verdict = "PASS" if hi < THRESHOLD else "FAIL" if lo > THRESHOLD else "inconclusive"
    return {"r_m": r, "f_hat": f, "s2_rms": d2.sum() / base.sum(), "ci": [lo, hi],
            "verdict": verdict, "point_pass": f <= THRESHOLD, "boot_inf": int(np.isinf(fb).sum())}


def power0(n: int, rel: float, rng: np.random.Generator) -> float:
    """P(PASS | f = 0): pool of 250 non-movers, halves = s + e, Var(s) 1, Var(e) (1 − R)/R."""
    rel = min(rel, 0.999)
    sd = math.sqrt((1 - rel) / rel)
    hits = 0
    for _ in range(POWER_DATASETS):
        s = rng.normal(size=250)
        a, c = s + rng.normal(0, sd, 250), s + rng.normal(0, sd, 250)
        d = rng.normal(0, sd, n) - rng.normal(0, sd, n)
        j = rng.integers(0, 250, (n, DRAWS))
        h1, h2 = a[j], c[j]
        base = np.mean((h1 - h2) ** 2, axis=1)
        sx, sy = h1.sum(1), h2.sum(1)
        sxx, syy, sxy = (h1 * h1).sum(1), (h2 * h2).sum(1), (h1 * h2).sum(1)
        idx = rng.integers(0, n, (B, n))
        cnt = n * DRAWS
        X, Y = sx[idx].sum(1), sy[idx].sum(1)
        r = (sxy[idx].sum(1) - X * Y / cnt) / np.sqrt((sxx[idx].sum(1) - X * X / cnt)
                                                      * (syy[idx].sum(1) - Y * Y / cnt))
        s2 = (d * d)[idx].sum(1) / base[idx].sum(1)
        fb = np.where(r > 0, (s2 - 1) * (1 - r) / np.where(r > 0, r, 1), np.inf)
        hits += lin_quantile(fb, 0.975) < THRESHOLD
    return hits / POWER_DATASETS


def close(a, b) -> bool:
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    if a in (math.inf, -math.inf) or b in (math.inf, -math.inf):
        return a == b
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check-output", type=Path, required=True)
    ap.add_argument("--registry", type=Path, required=True)
    args = ap.parse_args()
    got = json.loads(args.check_output.read_text())
    meas = json.loads(MEASUREMENTS.read_text())
    reg = json.loads(args.registry.read_text())
    b = got["b"]
    errors: list[str] = []

    def chk(where: str, a, e) -> None:
        if not close(float(a), float(e)) if isinstance(e, (int, float)) and not isinstance(e, bool) \
                else a != e:
            errors.append(f"{where}: output {a!r} != reference {e!r}")

    if any("dna" not in reg["groups"][g] for g in GROUPS):
        fail("registry snapshot: a group has no 'dna' list")
    metrics = sorted({m for g in GROUPS for m in reg["groups"][g]["dna"]}
                     | (set(DEN) - {"team_pass_share"}) | {"ball_recoveries_p90", "tackles_won_p90"})
    if sorted(got["metrics"]) != metrics:
        fail(f"metric set differs: {sorted(set(got['metrics']) ^ set(metrics))}")

    season = pl.read_parquet(SEASON_PARQUET).filter(pl.col("season_id") == SEASON)
    srows = season.to_dicts()                                # parquet row order
    dates, table = {}, defaultdict(lambda: [0, 0, 0])
    for comp in POOL:
        for m in json.loads((RAW / "matches" / str(comp) / f"{SEASON}.json").read_text()):
            dates[m["match_id"]] = m["match_date"]
            h, a = m["home_team"]["home_team_id"], m["away_team"]["away_team_id"]
            hs, as_ = m["home_score"], m["away_score"]
            for t, gf, ga in ((h, hs, as_), (a, as_, hs)):
                row = table[(comp, t)]
                row[0] += 3 if gf > ga else (1 if gf == ga else 0)
                row[1] += gf - ga
                row[2] += gf
    rank = {}
    for comp in POOL:
        order = sorted((k for k in table if k[0] == comp), key=lambda k: tuple(table[k]), reverse=True)
        rank.update({k: [i + 1, len(order)] for i, k in enumerate(order)})

    cols = {"minutes_played", "match_duration"}
    for m in metrics:
        num, pos, neg = terms(m)
        cols |= {num, *pos, *neg}
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in (pl.read_parquet(MATCH_PARQUET, columns=["match_id", "player_id", "team_id",
                                                       "competition_id", *sorted(cols)])
              .filter(pl.col("competition_id").is_in(POOL) & (pl.col("minutes_played") > 0))
              .to_dicts()):
        r["date"] = dates[r["match_id"]]
        r["nom"] = r["minutes_played"] * 90.0 / r["match_duration"]
        by_key[(r["player_id"], r["team_id"], r["competition_id"])].append(r)
    for v in by_key.values():
        v.sort(key=lambda r: (r["date"], r["match_id"]))

    # movers
    per_player: dict[int, list[dict]] = defaultdict(list)
    for r in srows:
        per_player[r["player_id"]].append(r)
    movers = []
    for pid in sorted(per_player):
        big = [r for r in per_player[pid]
               if r["minutes_nominal_equiv"] >= FLOOR - EPS and r["position_group"] != "GK"]
        if len(big) == 2 and big[0]["position_group"] == big[1]["position_group"]:
            for r in big:
                ms_ = by_key[(r["player_id"], r["team_id"], r["competition_id"])]
                r["first"], r["last"] = ms_[0]["date"], ms_[-1]["date"]
            movers.append(sorted(big, key=lambda r: (r["first"], r["last"], r["team_id"])))
    if len(got["movers"]) != len(movers):
        fail(f"movers {len(got['movers'])} != {len(movers)}")
    for i, (o, st) in enumerate(zip(got["movers"], movers)):
        s1, s2 = st
        chk(f"mover {i} name", o["player"], s1["player_name"])
        chk(f"mover {i} to", o["to"], s2["team_name"])
        chk(f"mover {i} m1", o["m1"], s1["minutes_nominal_equiv"])
        chk(f"mover {i} m2", o["m2"], s2["minutes_nominal_equiv"])
        chk(f"mover {i} from_rank", o["from_rank"], rank[(s1["competition_id"], s1["team_id"])])
        chk(f"mover {i} to_rank", o["to_rank"], rank[(s2["competition_id"], s2["team_id"])])

    gated = [r for r in srows if r["in_percentile_pool"] and r["is_outfield"]
             and r["minutes_nominal_equiv"] >= GATE - EPS]
    best: dict[tuple, dict] = {}
    for r in gated:
        k = (r["player_id"], r["competition_id"], r["position_group"])
        cur = best.get(k)
        if cur is None or (-r["minutes_played"], r["competition_id"], r["team_id"]) < \
                (-cur["minutes_played"], cur["competition_id"], cur["team_id"]):
            best[k] = r
    local = list(best.values())
    nrows = defaultdict(int)
    for r in srows:
        nrows[r["player_id"]] += 1
    nonmovers = [r for r in gated if nrows[r["player_id"]] == 1]

    def ma_of(metric: str, grp: str) -> float | None:
        if metric in DEN and metric != "team_pass_share":
            return meas["ratios"][metric]["groups"][grp]["min_attempts"]   # None: excluded
        return 0

    popc: dict = {}

    def pop(metric: str, grp: str) -> dict:
        if (metric, grp) not in popc:
            ma = ma_of(metric, grp)
            lg, res = {}, []
            for comp in POOL:
                vals = [r[metric] for r in local if r["competition_id"] == comp
                        and r["position_group"] == grp and r[metric] is not None
                        and not math.isnan(r[metric]) and den_of(r, metric) >= ma]
                mu = sum(vals) / len(vals)
                lg[comp] = (vals, mu)
                res += [v - mu for v in vals]
            mres = sum(res) / len(res)
            sd = math.sqrt(sum((x - mres) ** 2 for x in res) / (len(res) - 1))
            cell = [r for r in gated if r["position_group"] == grp]
            if metric in DEN and metric != "team_pass_share":
                med = float(np.median([den_of(r, metric) for r in cell]))
            else:
                med = float(np.median([r["minutes_nominal_equiv"] for r in cell]))
            popc[(metric, grp)] = {"lg": lg, "sd": sd, "med": med}
        return popc[(metric, grp)]

    def sc(metric: str, grp: str, comp: int, v: float) -> tuple[float, float]:
        p = pop(metric, grp)
        vals, mu = p["lg"][comp]
        return pct_hazen(vals, v), (v - mu) / p["sd"]

    # baseline candidates per mover: half sums by a match loop
    cands = []
    for st in movers:
        grp = st[0]["position_group"]
        m1, m2 = st[0]["minutes_nominal_equiv"], st[1]["minutes_nominal_equiv"]
        cl = []
        for r in nonmovers:
            if r["position_group"] != grp or r["minutes_nominal_equiv"] < m1 + m2 - EPS:
                continue
            ms_ = by_key[(r["player_id"], r["team_id"], r["competition_id"])]
            run, cum = 0.0, [0.0]
            for x in ms_:
                run += x["nom"]
                cum.append(run)
            k1 = min(range(1, len(ms_)), key=lambda k: (abs(cum[k] - m1), k))
            k2 = min(range(k1 + 1, len(ms_) + 1), key=lambda k: (abs(cum[k] - cum[k1] - m2), k))
            h = [{c: math.fsum(x[c] for x in part) for c in cols}
                 for part in (ms_[:k1], ms_[k1:k2])]
            cl.append((r["competition_id"], h))
        cands.append(cl)

    for metric in metrics:
        o = got["metrics"][metric]
        rng = stream("m4g3_draws", metric)
        d2, H1, H2, absa, absb, ba, bb, cross = [], [], [], [], [], [], [], []
        for st, cl in zip(movers, cands):
            grp = st[0]["position_group"]
            ma = ma_of(metric, grp)
            if ma is None:
                continue
            vals = [s[metric] for s in st]
            if any(v is None or math.isnan(v) or den_of(s, metric) < ma for s, v in zip(st, vals)):
                continue
            elig = []
            for comp, h in cl:
                hv = [half_value(metric, h[0]), half_value(metric, h[1])]
                if all(not math.isnan(v) and dn >= max(ma, 1e-12) for v, dn in hv):
                    elig.append((comp, hv[0][0], hv[1][0]))
            if not elig:
                continue
            p = [sc(metric, grp, s["competition_id"], v) for s, v in zip(st, vals)]
            pick = rng.integers(0, len(elig), DRAWS)
            ha = [[sc(metric, grp, elig[j][0], elig[j][1 + t]) for t in (0, 1)] for j in pick]
            d2.append((p[1][1] - p[0][1]) ** 2)
            absa.append(abs(p[1][0] - p[0][0]))
            absb.append(abs(p[1][1] - p[0][1]))
            H1.append([x[0][1] for x in ha])
            H2.append([x[1][1] for x in ha])
            ba.append(sum(abs(x[1][0] - x[0][0]) for x in ha) / DRAWS)
            bb.append(sum(abs(x[1][1] - x[0][1]) for x in ha) / DRAWS)
            if metric == "team_pass_share":
                cm = meas["team_pass_share"].get(grp, {})
            else:
                cm = meas["ratios" if metric in DEN else "metrics"].get(metric, {}).get("groups", {}).get(grp, {})
            rf = cm.get("split_half_r_full", cm.get("r_full"))
            if rf is not None:
                med = pop(metric, grp)["med"]
                ex = [den_of(s, metric) if metric in DEN and metric != "team_pass_share"
                      else s["minutes_nominal_equiv"] for s in st]
                rr = [(e / med) * rf / (1 + (e / med - 1) * rf) for e in ex]
                if min(rr) > 0:
                    cross.append(math.sqrt(rr[0] * rr[1]))
        n = len(d2)
        chk(f"{metric} n", o["n"], n)
        if n:
            chk(f"{metric} mover_mean_abs_a", o["mover_mean_abs_a"], float(np.mean(absa)))
            chk(f"{metric} mover_mean_abs_b", o["mover_mean_abs_b"], float(np.mean(absb)))
            chk(f"{metric} baseline_mean_abs_a", o["baseline_mean_abs_a"], float(np.mean(ba)))
            chk(f"{metric} baseline_mean_abs_b", o["baseline_mean_abs_b"], float(np.mean(bb)))
        if n < MIN_N:
            chk(f"{metric} verdict", o["verdict"], "untestable")
            continue
        res = rule(np.array(d2), np.array(H1), np.array(H2), stream("m4g3_boot", metric), b)
        for k in ("r_m", "f_hat", "s2_rms", "verdict", "point_pass", "boot_inf"):
            chk(f"{metric} {k}", o[k], res[k])
        chk(f"{metric} ci_lo", o["ci"][0], res["ci"][0])
        chk(f"{metric} ci_hi", o["ci"][1], res["ci"][1])
        if cross:
            cc = float(np.mean(cross))
            chk(f"{metric} cross r", o["cross_check"]["r_step_down"], cc)
            chk(f"{metric} cross f", o["cross_check"]["f_hat_with_step_down"],
                (res["s2_rms"] - 1) * (1 - cc) / cc)
        # low power: independent simulation, tolerance
        lp = o["low_power"]
        mine = power0(n, res["r_m"], np.random.default_rng([POWER_SEED, zlib.crc32(metric.encode())]))
        se = math.sqrt(lp["se"] ** 2 + mine * (1 - mine) / POWER_DATASETS)
        if abs(lp["power_at_f0"] - mine) > 4 * max(se, 1e-3):
            errors.append(f"{metric} power {lp['power_at_f0']:.3f} vs reference {mine:.3f}")
        elif (mine < LOW) != lp["underpowered"] and abs(mine - LOW) > 4 * max(se, 1e-3):
            errors.append(f"{metric} underpowered label differs (reference power {mine:.3f})")
        print(f"{metric:32s} ok  verdict={res['verdict']:12s} power {lp['power_at_f0']:.3f} / ref {mine:.3f}",
              flush=True)

    if len(got["kone"]) != 1 or got["kone"][0]["player"] != "Lamine Koné":
        fail(f"kone block: {[k['player'] for k in got['kone']]}")
    kone = got["kone"][0]
    for st in movers:
        if st[0]["player_name"] == kone["player"]:
            for o, s in zip(kone["stints"], st):
                v = s["long_pass_completion_pct"]
                a_, b_ = sc("long_pass_completion_pct", s["position_group"], s["competition_id"], v)
                chk("kone value", o["value"], v)
                chk("kone scale_a", o["scale_a"], a_)
                chk("kone scale_b", o["scale_b"], b_)
    if errors:
        print("\n".join(errors[:50]), file=sys.stderr)
        fail(f"{len(errors)} mismatches")
    print(f"OK: {len(metrics)} metrics, {len(movers)} movers, every field within {TOL:g}")


if __name__ == "__main__":
    main()
