"""
Independent reference for M4g4c (plan 005 M4g4 plan + M4g4c, ADR 004 §13.8).

Shares the written spec (ADR 004 §13.8, plan 005 "M4g4 plan", the M4g4c plan and the docstring
of spikes/m4g4_possession.py), not code, with spikes/m4g4_possession.py, m4g3_movers.py,
m4g3_power.py and m4g2_multisplit.py:
- its own possession proxies (a per-match event loop into dicts, the LOO ratio and the
  minutes-weighted mean in Python), own gated rows, D3 dedupe, average ranks, Spearman (Pearson
  of average ranks), quintiles, β̂ (least squares on stacked league-demeaned team logs) and β̂
  bootstrap (the same stream, written out);
- the stored *_p90 columns as the raw values (not count / minutes);
- the tie rule of the spec: β-adjusted values rounded to 12 significant digits;
- criterion (3) from the M4g2 reference's own split loop (m4g2_reliability_reference.labels,
  r_by_split, sb), its own half sums;
- movers: its own mover / non-mover construction and cut (a match loop), adjusted values and
  scales; the rule, Hazen, quantile and power simulation of m4g3_movers_reference (itself
  independent of the engine).

Modes:
  --check-output possession.json       every criterion value, β̂ (all-4, LOLO, CI), flag,
                                       selection, G2 minimum and G3 exclusion count within TOL.
  --check-movers movers.json           every mover field (primary and 5 extra streams), power
                                       within 4 combined SEs.
  --write-expected                     (needs both) spikes/expected_m4g4_flips.json from the
                                       reference's own selection and verdicts, BEFORE any
                                       registry code.

No eleveniq imports (tests/test_spikes_independent.py).

  .venv/bin/python spikes/m4g4_possession_reference.py --registry data/interim/m4g4c_snapshot/registry.json \
      --check-output data/interim/m4g4c/possession.json --check-movers data/interim/m4g4c/movers.json \
      [--write-expected]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g2_reliability_reference as r2  # noqa: E402  (independent split loop)
import m4g3_movers_reference as r3  # noqa: E402  (independent rule, Hazen, power)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "statsbomb"
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = ROOT / "data" / "interim" / "player_match_stats.parquet"
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
EXPECTED = Path(__file__).resolve().parent / "expected_m4g4_flips.json"

SEASON, POOL = 27, (2, 7, 11, 12)
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")
GATE, EPS = 900.0, 1e-6
PROXIES = ("poss_dur_loo", "poss_pass_loo")
CANDS = ("beta_0", "beta_hat", "team_share", "beta_1")
MOVER_CANDS = ("beta_hat", "team_share", "beta_1")
G2_TEAM, G2_OPP, G3_TEAM, G3_OPP = 20, 1000, 10, 500
RHO_MAX, DEAD, INCONS, GAP_MAX, RF_MIN = 0.1, 0.05, 0.3, 10.0, 0.6
BETA_B, SEED, MIN_N, THRESHOLD, LOW, EXTRA = 2000, 20261008, 12, 0.5, 0.8, 5
SPLIT_SEED, CHUNK = 20261007, 100
METRIC = "ball_recoveries_p90"
OPP = "opponent_open_play_passes_while_on"
TOL = 1e-9
WITHHELD = {"untestable": "mover_stability_untestable", "FAIL": "mover_stability_failed",
            "inconclusive": "mover_stability_inconclusive"}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b)
                and all(close(x, y) for x, y in zip(a, b)))
    if isinstance(a, dict) or isinstance(b, dict):
        return a == b
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return a == b
    a, b = float(a), float(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))


def stream(*key: str) -> np.random.Generator:
    return np.random.default_rng([SEED, zlib.crc32("|".join(repr(k) for k in key).encode())])


def avg_rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(avg_rank(a), avg_rank(b))[0, 1])


def canon(v):
    """Tie rule (spec): β-adjusted values to 12 significant digits."""
    if isinstance(v, np.ndarray):
        return np.array([float("%.12g" % x) for x in v])
    return float("%.12g" % v)


def actn(metric: str) -> str:
    return metric.removesuffix("_p90")


# ── data ──────────────────────────────────────────────────────────────────────

def proxies(keys: dict[tuple, dict], match_rows: list[dict]) -> None:
    """Adds poss_dur_loo / poss_pass_loo to each gated row dict (in place)."""
    by_match: dict[int, list[dict]] = defaultdict(list)
    for r in match_rows:
        by_match[r["match_id"]].append(r)
    acc: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])   # Σw·dur, Σw·pass, Σw
    for mid in sorted(by_match):
        team_d, team_p, own_d, own_p = defaultdict(float), defaultdict(int), defaultdict(float), defaultdict(int)
        all_d, all_p = 0.0, 0
        for e in json.loads((RAW / "events" / f"{mid}.json").read_text()):
            if e.get("period") not in (1, 2, 3, 4):
                continue
            pt = e["possession_team"]["id"] if e.get("possession_team") else None
            t = e["team"]["id"] if e.get("team") else None
            p = e["player"]["id"] if e.get("player") else None
            d = e.get("duration") or 0.0          # StatsBomb omits duration on instant events (the M4c definition)
            if pt is not None:
                team_d[pt] += d
                all_d += d
            ispass = e["type"]["name"] == "Pass"
            if ispass and t is not None:
                team_p[t] += 1
                all_p += 1
            if p is not None:
                if pt is not None and pt == t:
                    own_d[p] += d
                if ispass:
                    own_p[p] += 1
        for r in by_match[mid]:
            k = tuple(r[c] for c in KEY)
            p, t, w = r["player_id"], r["team_id"], r["minutes_played"]
            a = acc[k]
            a[0] += w * (team_d[t] - own_d[p]) / (all_d - own_d[p])
            a[1] += w * (team_p[t] - own_p[p]) / (all_p - own_p[p])
            a[2] += w
    for k, row in keys.items():
        a = acc[k]
        row["poss_dur_loo"], row["poss_pass_loo"] = a[0] / a[2], a[1] / a[2]


class Ref:
    def __init__(self, reg: dict) -> None:
        self.meas = json.loads(MEASUREMENTS.read_text())
        fam = {(c["column"], c["group"]): c["family"] for c in reg["cells"]}
        self.defending = {g: sorted(m for m in reg["groups"][g]["dna"] if fam[(m, g)] == "Defending")
                          for g in GROUPS}
        season = pl.read_parquet(SEASON_PARQUET).filter(pl.col("season_id") == SEASON)
        self.srows = season.to_dicts()
        gated = [r for r in self.srows if r["in_percentile_pool"] and r["is_outfield"]
                 and r["minutes_nominal_equiv"] >= GATE - EPS]
        self.gated = gated
        keys = {tuple(r[c] for c in KEY): r for r in gated}
        mrows = (pl.read_parquet(MATCH_PARQUET)
                 .filter(pl.col("competition_id").is_in(POOL) & (pl.col("season_id") == SEASON)))
        self.match = mrows
        sel = [r for r in mrows.select(*KEY, "match_id", "minutes_played").to_dicts()
               if tuple(r[c] for c in KEY) in keys]
        proxies(keys, sel)

    def rows(self, g: str) -> list[dict]:
        return sorted((r for r in self.gated if r["position_group"] == g),
                      key=lambda r: tuple(r[c] for c in KEY))


def dedupe(rows: list[dict], by) -> list[bool]:
    best: dict = {}
    for i, r in enumerate(rows):
        k = tuple(r[c] for c in by)
        if k not in best or (-r["minutes_played"], r["competition_id"], r["team_id"]) < \
                (-rows[best[k]]["minutes_played"], rows[best[k]]["competition_id"], rows[best[k]]["team_id"]):
            best[k] = i
    keep = set(best.values())
    return [i in keep for i in range(len(rows))]


# ── one cell ──────────────────────────────────────────────────────────────────

def team_logs(rows: list[dict], metric: str) -> dict[int, list[tuple[int, float, float | None]]]:
    acc: dict = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        a = acc[(r["competition_id"], r["team_id"])]
        a[0] += r["minutes_played"]
        a[1] += r[actn(metric)]
        a[2] += r[OPP]
    out: dict[int, list] = {c: [] for c in POOL}
    for (c, t) in sorted(acc):
        m, n, o = acc[(c, t)]
        out[c].append((t, math.log(o * 90 / m), math.log(n * 90 / m) if n > 0 else None))
    return out


def slope(tab: dict, leagues) -> float | None:
    if any(y is None for c in leagues for _, _, y in tab[c]):
        return None
    X, Y = [], []
    for c in leagues:
        x = np.array([v[1] for v in tab[c]])
        y = np.array([v[2] for v in tab[c]])
        X.append(x - x.mean())
        Y.append(y - y.mean())
    X, Y = np.concatenate(X), np.concatenate(Y)
    return float(np.linalg.lstsq(X[:, None], Y, rcond=None)[0][0])


def boot_ci(tab: dict, metric: str, g: str) -> list[float] | None:
    if any(y is None for c in POOL for _, _, y in tab[c]):
        return None
    rng = stream("m4g4_beta_boot", metric, g)
    out = []
    for _ in range(BETA_B):
        X, Y = [], []
        for c in POOL:
            v = tab[c]
            pick = rng.integers(0, len(v), len(v))
            x = np.array([v[j][1] for j in pick])
            y = np.array([v[j][2] for j in pick])
            X.append(x - x.mean())
            Y.append(y - y.mean())
        X, Y = np.concatenate(X), np.concatenate(Y)
        sxx = float(X @ X)
        if sxx > 0:
            out.append(float(X @ Y) / sxx)
    a = np.array(out)
    return [r3.lin_quantile(a, 0.025), r3.lin_quantile(a, 0.975)]


def cell_criteria(ref: Ref, metric: str, g: str, rf: dict) -> dict:
    rows = ref.rows(g)
    raw = np.array([r[metric] for r in rows])
    E = np.array([r[OPP] / r["minutes_played"] * 90 for r in rows])
    e_ref = float(np.median(E))
    comp = np.array([r["competition_id"] for r in rows])
    loc = dedupe(rows, ("player_id", "competition_id"))
    glob = dedupe(rows, ("player_id",))
    tab = team_logs(rows, metric)
    b_all = slope(tab, POOL)
    lolo = {c: slope(tab, [x for x in POOL if x != c]) for c in POOL}

    def hz(pop, xs):
        return np.array([r3.pct_hazen(pop, x) for x in xs])

    def crit(vals: np.ndarray) -> dict:
        out = {"rho": {}, "gap": {}}
        pct = np.empty(len(vals))
        for c in POOL:
            s = comp == c
            pct[s] = hz([v for v, k, cc in zip(vals, loc, comp) if k and cc == c], vals[s])
        for p in PROXIES:
            prox = np.array([r[p] for r in rows])
            per, gaps = {}, {}
            for c in POOL:
                s = np.flatnonzero(comp == c)
                per[str(c)] = spearman(prox[s], vals[s])
                q = np.ceil(5 * avg_rank(prox[s]) / len(s))
                gaps[str(c)] = abs(pct[s][q == 5].mean() - pct[s][q == 1].mean())
            out["rho"][p] = (per, sum(per.values()) / 4)
            out["gap"][p] = (gaps, sum(gaps.values()) / 4)
        return out

    def d9(vals):
        pop = [v for v, k in zip(vals, glob) if k]
        gp = hz(pop, vals)
        return {str(c): float(gp[comp == c].mean()) for c in POOL}

    res = {"e_ref": e_ref, "beta_hat": b_all, "lolo": lolo,
           "ci": boot_ci(tab, metric, g) if b_all is not None else None,
           "zero_teams": [t for c in POOL for t, _, y in tab[c] if y is None], "cands": {}}
    share = np.array([r[actn(metric)] / r[f"team_{actn(metric)}_while_on"] for r in rows])
    ref_mean = None
    for cand in CANDS:
        if cand == "beta_hat" and (b_all is None or any(v is None for v in lolo.values())):
            res["cands"][cand] = {"fit_failed": True}
            continue
        if cand == "beta_0":
            vc = va = raw
        elif cand == "beta_1":
            vc = va = canon(raw * e_ref / E)
        elif cand == "team_share":
            vc = va = share
        else:
            vc = canon(raw * (e_ref / E) ** np.array([lolo[c] for c in comp]))
            va = canon(raw * (e_ref / E) ** b_all)
        c = crit(vc)
        means = {p: c["rho"][p][1] for p in PROXIES}
        if cand == "beta_0":
            ref_mean = means
        flip = any(abs(ref_mean[p]) >= DEAD and abs(means[p]) >= DEAD
                   and (means[p] > 0) != (ref_mean[p] > 0) for p in PROXIES)
        r3full = rf[(metric, g, cand)]
        c1 = all(abs(means[p]) < RHO_MAX for p in PROXIES)
        c2 = all(c["gap"][p][1] <= GAP_MAX for p in PROXIES)
        res["cands"][cand] = {
            "c": c, "c1": c1, "c2": c2, "c3": r3full >= RF_MIN, "r_full": r3full, "flip": flip,
            "incons": any(abs(v) >= INCONS for p in PROXIES for v in c["rho"][p][0].values()),
            "d9": d9(va), "passes": c1 and c2 and r3full >= RF_MIN and not flip}
    res["selected"] = next((cd for cd in CANDS if res["cands"][cd].get("passes")), None)
    return res


# ── criterion (3) ─────────────────────────────────────────────────────────────

def multisplit(ref: Ref, params: dict, k: int) -> dict:
    """(metric, group, candidate) → mean r_full over k splits (the M4g2 stream)."""
    stints, rows = r2.load()
    stints = stints.filter(pl.col("season_id") == SEASON)
    n = stints["n"].to_list()
    starts = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)
    grp = stints["position_group"].to_numpy().astype(str)
    gated = stints["gated"].to_numpy()
    league = stints["competition_id"].to_numpy().astype(np.int64)
    cols = {"minutes_played", OPP}
    for g in GROUPS:
        for m in ref.defending[g]:
            cols |= {actn(m), f"team_{actn(m)}_while_on"}
    vals = {c: rows[c].cast(pl.Float64).to_numpy() for c in cols}
    rng = np.random.default_rng(SPLIT_SEED)
    acc: dict = defaultdict(list)
    for k0 in range(0, k, CHUNK):
        L = r2.labels(n, k0, min(k0 + CHUNK, k), rng)
        cnt0 = np.add.reduceat(L.astype(np.int64), starts, axis=1)
        cnt = (cnt0, np.array(n)[None, :] - cnt0)
        sums: dict = {}

        def s(c: str, h: int) -> np.ndarray:
            if (c, h) not in sums:
                w = L if h == 0 else ~L
                sums[(c, h)] = np.add.reduceat(np.where(w, vals[c][None, :], 0.0), starts, axis=1)
            return sums[(c, h)]

        for g in GROUPS:
            idx = np.flatnonzero(gated & (grp == g))
            for m in ref.defending[g]:
                for cand in CANDS:
                    prm = params[(m, g)]
                    if cand == "beta_hat" and prm["beta_hat"] is None:
                        continue
                    halves = []
                    for h in (0, 1):
                        num, mins = s(actn(m), h)[:, idx], s("minutes_played", h)[:, idx]
                        opp, tm = s(OPP, h)[:, idx], s(f"team_{actn(m)}_while_on", h)[:, idx]
                        with np.errstate(divide="ignore", invalid="ignore"):
                            if cand == "team_share":
                                v = np.where(tm >= G3_TEAM, num / tm, np.nan)
                            else:
                                v = np.where(cnt[h][:, idx] > 0, num / mins * 90, np.nan)
                                if cand != "beta_0":
                                    b = 1.0 if cand == "beta_1" else prm["beta_hat"]
                                    v = np.where(opp >= G3_OPP, v * (prm["e_ref"] / (opp / mins * 90)) ** b, np.nan)
                        halves.append(v)
                    r, _, _ = r2.r_by_split(halves[0], halves[1], league[idx])
                    acc[(m, g, cand)].append(r2.sb(r))
        print(f"  reference splits {min(k0 + CHUNK, k)}/{k}", flush=True)
    out = {}
    for key, parts in acc.items():
        a = np.concatenate(parts)
        if np.isnan(a).any():
            fail(f"r undefined in some split for {key}")
        out[key] = float(a.mean())
    return out


# ── movers ────────────────────────────────────────────────────────────────────

class Movers:
    def __init__(self, ref: Ref) -> None:
        dates = {}
        for comp in POOL:
            for m in json.loads((RAW / "matches" / str(comp) / f"{SEASON}.json").read_text()):
                dates[m["match_id"]] = m["match_date"]
        cols = ("minutes_played", "match_duration", "ball_recoveries", OPP, "team_ball_recoveries_while_on")
        by_key: dict = defaultdict(list)
        for r in (ref.match.filter(pl.col("minutes_played") > 0)
                  .select("match_id", "player_id", "team_id", "competition_id", *cols).to_dicts()):
            r["date"] = dates[r["match_id"]]
            r["nom"] = r["minutes_played"] * 90.0 / r["match_duration"]
            by_key[(r["player_id"], r["team_id"], r["competition_id"])].append(r)
        for v in by_key.values():
            v.sort(key=lambda r: (r["date"], r["match_id"]))
        per: dict = defaultdict(list)
        for r in ref.srows:
            per[r["player_id"]].append(r)
        self.movers = []
        for pid in sorted(per):
            big = [r for r in per[pid] if r["minutes_nominal_equiv"] >= 450.0 - EPS and r["position_group"] != "GK"]
            if len(big) == 2 and big[0]["position_group"] == big[1]["position_group"]:
                for r in big:
                    ms_ = by_key[(r["player_id"], r["team_id"], r["competition_id"])]
                    r["first"], r["last"] = ms_[0]["date"], ms_[-1]["date"]
                self.movers.append(sorted(big, key=lambda r: (r["first"], r["last"], r["team_id"])))
        nrows: dict = defaultdict(int)
        for r in ref.srows:
            nrows[r["player_id"]] += 1
        self.local = [r for r, k in zip(ref.gated, dedupe(ref.gated, ("player_id", "competition_id", "position_group"))) if k]
        nonmovers = [r for r in ref.gated if nrows[r["player_id"]] == 1]
        self.cands = []
        for st in self.movers:
            g = st[0]["position_group"]
            m1, m2 = st[0]["minutes_nominal_equiv"], st[1]["minutes_nominal_equiv"]
            cl = []
            for r in nonmovers:
                if r["position_group"] != g or r["minutes_nominal_equiv"] < m1 + m2 - EPS:
                    continue
                ms_ = by_key[(r["player_id"], r["team_id"], r["competition_id"])]
                cum = [0.0]
                for x in ms_:
                    cum.append(cum[-1] + x["nom"])
                k1 = min(range(1, len(ms_)), key=lambda k: (abs(cum[k] - m1), k))
                k2 = min(range(k1 + 1, len(ms_) + 1), key=lambda k: (abs(cum[k] - cum[k1] - m2), k))
                h = [{c: math.fsum(x[c] for x in part) for c in cols} for part in (ms_[:k1], ms_[k1:k2])]
                cl.append((r["competition_id"], h))
            self.cands.append(cl)

    @staticmethod
    def value(cand: str, prm: dict, s: dict) -> float:
        if cand == "team_share":
            t = s["team_ball_recoveries_while_on"]
            return s["ball_recoveries"] / t if t >= G3_TEAM else math.nan
        if s[OPP] < G3_OPP or s["minutes_played"] <= 0:
            return math.nan
        b = 1.0 if cand == "beta_1" else prm["beta_hat"]
        p90 = s["ball_recoveries"] * 90.0 / s["minutes_played"]
        return canon(p90 * (prm["e_ref"] * s["minutes_played"] / (s[OPP] * 90.0)) ** b)

    def exclusions(self, cand: str, params: dict) -> dict:
        stint, nobase, dropped, total, mins = [], [], 0, 0, []
        for st, cl in zip(self.movers, self.cands):
            prm = params[st[0]["position_group"]]
            sv = [self.value(cand, prm, s) for s in st]
            ok = [all(not math.isnan(self.value(cand, prm, h)) for h in hh) for _, hh in cl]
            dropped += len(cl) - sum(ok)
            total += len(cl)
            if any(math.isnan(v) for v in sv):
                stint.append(st[0]["player_name"])
                continue
            mins.append(sum(ok))
            if not any(ok):
                nobase.append(st[0]["player_name"])
        n = len(self.movers) - len(stint) - len(nobase)
        return {"movers": len(self.movers), "stint_g3_excluded": stint, "no_eligible_baseline": nobase,
                "n": n, "below_min_movers": n < MIN_N, "baseline_halves_dropped": dropped,
                "baseline_candidates": total, "min_eligible_per_mover": min(mins, default=0)}

    def run(self, cand: str, params: dict, k: int) -> dict:
        sfx = "" if k == 0 else f"#{k}"
        rng = stream("m4g4_draws" + sfx, METRIC, cand)
        pops = {}
        for g in GROUPS:
            lg, res = {}, []
            for c in POOL:
                v = [self.value(cand, params[g], r) for r in self.local
                     if r["competition_id"] == c and r["position_group"] == g]
                mu = sum(v) / len(v)
                lg[c] = (v, mu)
                res += [x - mu for x in v]
            mr = sum(res) / len(res)
            pops[g] = (lg, math.sqrt(sum((x - mr) ** 2 for x in res) / (len(res) - 1)))

        def sc(g, c, v):
            vals, mu = pops[g][0][c]
            return r3.pct_hazen(vals, v), (v - mu) / pops[g][1]

        d2, H1, H2, absa, absb, ba, bb, d2a, b2a = ([] for _ in range(9))
        for st, cl in zip(self.movers, self.cands):
            g = st[0]["position_group"]
            sv = [self.value(cand, params[g], s) for s in st]
            if any(math.isnan(v) for v in sv):
                continue
            elig = []
            for c, hh in cl:
                hv = [self.value(cand, params[g], h) for h in hh]
                if not any(math.isnan(v) for v in hv):
                    elig.append((c, hv[0], hv[1]))
            if not elig:
                continue
            p = [sc(g, s["competition_id"], v) for s, v in zip(st, sv)]
            pick = rng.integers(0, len(elig), 200)
            ha = [[sc(g, elig[j][0], elig[j][1 + t]) for t in (0, 1)] for j in pick]
            d2.append((p[1][1] - p[0][1]) ** 2)
            absa.append(abs(p[1][0] - p[0][0]))
            absb.append(abs(p[1][1] - p[0][1]))
            H1.append([x[0][1] for x in ha])
            H2.append([x[1][1] for x in ha])
            ba.append(sum(abs(x[1][0] - x[0][0]) for x in ha) / 200)
            bb.append(sum(abs(x[1][1] - x[0][1]) for x in ha) / 200)
            d2a.append((p[1][0] - p[0][0]) ** 2)
            b2a.append(sum((x[1][0] - x[0][0]) ** 2 for x in ha) / 200)
        out = {"n": len(d2), "res": None}
        if d2:
            out |= {"mover_mean_abs_a": float(np.mean(absa)), "mover_mean_abs_b": float(np.mean(absb)),
                    "baseline_mean_abs_a": float(np.mean(ba)), "baseline_mean_abs_b": float(np.mean(bb))}
        if len(d2) >= MIN_N:
            out["res"] = r3.rule(np.array(d2), np.array(H1), np.array(H2), stream("m4g4_boot" + sfx, METRIC, cand), 2000)
            out["res"]["s2_rms_a"] = sum(d2a) / sum(b2a)
        return out


def verdict_of(n: int, lo, hi) -> str:
    if n < MIN_N:
        return "untestable"
    return "PASS" if hi < THRESHOLD else "FAIL" if lo > THRESHOLD else "inconclusive"


def cell_errors(o: dict, r: dict, w: str) -> list[str]:
    """One output cell (possession.json) against the reference's cell_criteria result."""
    errors: list[str] = []

    def chk(where, a, e):
        if not close(a, e):
            errors.append(f"{where}: output {a!r} != reference {e!r}")

    chk(f"{w} e_ref", o["e_ref"], r["e_ref"])
    chk(f"{w} beta_hat", o["beta_hat"], r["beta_hat"])
    for c in POOL:
        chk(f"{w} lolo {c}", o["beta_hat_lolo"][str(c)], r["lolo"][c])
    if r["ci"] is None:
        chk(f"{w} ci", o["beta_hat_ci"]["ci"], None)
    else:
        chk(f"{w} ci lo", o["beta_hat_ci"]["ci"][0], r["ci"][0])
        chk(f"{w} ci hi", o["beta_hat_ci"]["ci"][1], r["ci"][1])
    chk(f"{w} rises flag", o["flags"]["rises_with_own_possession"], r["beta_hat"] is not None and r["beta_hat"] < 0)
    chk(f"{w} |β̂|>1 flag", o["flags"]["abs_beta_hat_gt_1"], r["beta_hat"] is not None and abs(r["beta_hat"]) > 1)
    chk(f"{w} selected", o["selected"], r["selected"])
    for cand in CANDS:
        oc, rc = o["candidates"][cand], r["cands"][cand]
        if rc.get("fit_failed"):
            chk(f"{w} {cand} fit_failed", oc.get("fit_failed", False), True)
            continue
        for p in PROXIES:
            for c in POOL:
                chk(f"{w} {cand} ρ {p} {c}", oc["c1_rho"][p]["by_league"][str(c)], rc["c"]["rho"][p][0][str(c)])
                chk(f"{w} {cand} gap {p} {c}", oc["c2_gap"][p]["by_league"][str(c)], rc["c"]["gap"][p][0][str(c)])
            chk(f"{w} {cand} ρ mean {p}", oc["c1_rho"][p]["mean"], rc["c"]["rho"][p][1])
            chk(f"{w} {cand} gap {p}", oc["c2_gap"][p]["gap"], rc["c"]["gap"][p][1])
        chk(f"{w} {cand} r_full", oc["c3_r_full"]["r_full"], rc["r_full"])
        for c in POOL:
            chk(f"{w} {cand} d9 {c}", oc["c4_d9_league_means"][str(c)], rc["d9"][str(c)])
        for key, rk in (("c1_pass", "c1"), ("c2_pass", "c2"), ("c3_pass", "c3"), ("sign_flip", "flip"),
                        ("inconsistent_across_leagues", "incons"), ("passes", "passes")):
            chk(f"{w} {cand} {key}", oc[key], rc[rk])
    return errors


def status_of(selected: str | None, v: dict | None) -> str:
    """ADR 004 §13.8 D5: the first failed test along the restore rule (possession → primary
    verdict → seed sensitivity → below model range), else ok."""
    if selected is None:
        return "possession_criteria_failed"
    if v["verdict"] != "PASS":
        return WITHHELD[v["verdict"]]
    if any(x != "PASS" for x in v["extra_seed_verdicts"]):
        return "mover_seed_sensitive"
    if v["ci"][1] < 0 and v["sqrt_s2_rms_a"] > 1:
        return "mover_below_model_range"
    return "ok"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True, help="registry snapshot BEFORE M4g4d")
    ap.add_argument("--check-output", type=Path, required=True)
    ap.add_argument("--check-movers", type=Path)
    ap.add_argument("--write-expected", action="store_true")
    args = ap.parse_args()
    if args.write_expected and not args.check_movers:
        fail("--write-expected needs --check-movers")
    reg = json.loads(args.registry.read_text())
    got = json.loads(args.check_output.read_text())
    errors: list[str] = []

    def chk(where, a, e):
        if not close(a, e):
            errors.append(f"{where}: output {a!r} != reference {e!r}")

    k = ref_k = json.loads(MEASUREMENTS.read_text())["reliability"]["k"]
    chk("k", got["k"], ref_k)
    rules = {"rho_max": RHO_MAX, "sign_dead_zone": DEAD, "inconsistent": INCONS, "gap_max": GAP_MAX,
             "r_full_min": RF_MIN, "g2": [G2_TEAM, G2_OPP], "g3": [G3_TEAM, G3_OPP], "beta_b": BETA_B}
    if got["rules"] != rules:
        fail(f"rules {got['rules']} != the written spec {rules}")
    ref = Ref(reg)
    # G2
    bad = []
    mins = {OPP: min(r[OPP] for r in ref.gated)}
    for r in ref.gated:
        if r[OPP] < G2_OPP:
            bad.append(r["player_name"])
        for m in ref.defending[r["position_group"]]:
            if r[f"team_{actn(m)}_while_on"] < G2_TEAM:
                bad.append((r["player_name"], m))
    if bad:
        fail(f"G2: {bad[:20]}")
    for g in GROUPS:
        for m in ref.defending[g]:
            mins[f"{g} team_{actn(m)}_while_on"] = min(r[f"team_{actn(m)}_while_on"] for r in ref.rows(g))
    if got["g2_minimums"] != mins:
        errors.append(f"g2_minimums {got['g2_minimums']} != {mins}")

    params = {}
    for g in GROUPS:
        for m in ref.defending[g]:
            rows = ref.rows(g)
            E = [r[OPP] / r["minutes_played"] * 90 for r in rows]
            params[(m, g)] = {"e_ref": float(np.median(E)), "beta_hat": slope(team_logs(rows, m), POOL)}
    print(f"reference criterion (3): {k} splits …", flush=True)
    rf = multisplit(ref, params, k)

    cells = {(c["metric"], c["group"]): c for c in got["cells"]}
    want = {(m, g) for g in GROUPS for m in ref.defending[g]}
    if set(cells) != want:
        fail(f"cell set differs: {sorted(set(cells) ^ want)}")
    mine: dict = {}
    for (m, g) in sorted(want, key=lambda x: (GROUPS.index(x[1]), x[0])):
        r = cell_criteria(ref, m, g, rf)
        mine[(m, g)] = r
        errors += cell_errors(cells[(m, g)], r, f"{g} {m}")
        # β = 0 equals the committed measurements
        st = ref.meas["metrics"][m]["groups"][g]
        for p in PROXIES:
            for c in POOL:
                if abs(r["cands"]["beta_0"]["c"]["rho"][p][0][str(c)] - st["possession"][p]["by_league"][str(c)]) > 5e-9:
                    errors.append(f"{g} {m} β = 0 ρ ≠ committed ({p}, {c})")
        if abs(r["cands"]["beta_0"]["r_full"] - st["r_full"]) > 5e-9:
            errors.append(f"{g} {m} β = 0 r_full ≠ committed")

    mv_params = {cand: {g: {"e_ref": mine[(METRIC, g)]["e_ref"], "beta_hat": mine[(METRIC, g)]["beta_hat"]}
                        for g in GROUPS} for cand in MOVER_CANDS}
    movers = Movers(ref)
    for cand in MOVER_CANDS:
        e = movers.exclusions(cand, mv_params[cand])
        for key, v in e.items():
            chk(f"g3 {cand} {key}", got["g3_mover_exclusions"][cand][key], v)
    if errors:
        print("\n".join(errors[:60]), file=sys.stderr)
        fail(f"{len(errors)} mismatches (criteria)")
    print(f"OK criteria: {len(want)} cells × {len(CANDS)} candidates, every field within {TOL:g}")
    if not args.check_movers:
        return

    mo = json.loads(args.check_movers.read_text())
    if mo["b"] != 2000 or mo["draws"] != 200 or mo["seed"] != SEED or mo["stream_keys"] != {
            "primary": ["m4g4_draws", "m4g4_boot"],
            "extra": [[f"m4g4_draws#{k}", f"m4g4_boot#{k}"] for k in range(1, EXTRA + 1)]}:
        fail("movers header differs from the written spec")
    chk("movers count", mo["movers"], len(movers.movers))
    sel = {g: mine[(METRIC, g)]["selected"] for g in GROUPS}
    chk("selected", mo["selected"], sel)
    verdicts = {}
    for cand in MOVER_CANDS:
        oc = mo["candidates"][cand]
        r0 = movers.run(cand, mv_params[cand], 0)
        w = f"mover {cand}"
        chk(f"{w} n", oc["n"], r0["n"])
        for key in ("mover_mean_abs_a", "mover_mean_abs_b", "baseline_mean_abs_a", "baseline_mean_abs_b"):
            if key in r0:
                chk(f"{w} {key}", oc[key], r0[key])
        res = r0["res"]
        if res is None:
            chk(f"{w} verdict", oc["verdict"], "untestable")
        else:
            for key in ("r_m", "f_hat", "s2_rms", "s2_rms_a", "verdict", "point_pass", "boot_inf"):
                chk(f"{w} {key}", oc[key], res[key])
            chk(f"{w} ci lo", oc["ci"][0], res["ci"][0])
            chk(f"{w} ci hi", oc["ci"][1], res["ci"][1])
            mine_p = r3.power0(r0["n"], res["r_m"], np.random.default_rng([r3.POWER_SEED, zlib.crc32(f"{METRIC}|{cand}".encode())]))
            errors += r3.power_errors(f"{cand}", [("output", oc["low_power"]["power_at_f0"], oc["low_power"]["se"])], mine_p)
        extras = []
        for k in range(1, EXTRA + 1):
            rk = movers.run(cand, mv_params[cand], k)
            e = oc["extra_seeds"][k - 1]
            chk(f"{w} seed {k} k", e["k"], k)
            chk(f"{w} seed {k} n", e["n"], rk["n"])
            vk = verdict_of(rk["n"], *(rk["res"]["ci"] if rk["res"] else (None, None)))
            chk(f"{w} seed {k} verdict", e["verdict"], vk)
            if rk["res"]:
                chk(f"{w} seed {k} ci lo", e["ci"][0], rk["res"]["ci"][0])
                chk(f"{w} seed {k} ci hi", e["ci"][1], rk["res"]["ci"][1])
            extras.append(vk)
        chk(f"{w} used_for_restore_in", oc["used_for_restore_in"], sorted(g for g in GROUPS if sel[g] == cand))
        v0 = verdict_of(r0["n"], *(res["ci"] if res else (None, None)))
        verdicts[cand] = {"verdict": v0, "n": r0["n"], "extra_seed_verdicts": extras,
                          "ci": res["ci"] if res else None, "f_hat": res["f_hat"] if res else None,
                          "r_m": res["r_m"] if res else None,
                          "sqrt_s2_rms_a": math.sqrt(res["s2_rms_a"]) if res else None,
                          "power_at_f0": oc["low_power"]["power_at_f0"] if res else None,
                          "used_for_restore_in": sorted(g for g in GROUPS if sel[g] == cand)}
        print(f"{cand:11s} ok  {v0:12s} seeds {extras}", flush=True)
    # β = 0: the M4g3b entry, its verdicts derived here from the committed numbers
    b0 = ref.meas["movers"]["metrics"][METRIC]
    v0 = verdict_of(b0["n"], r3.num(b0["ci_lo"]), r3.num(b0["ci_hi"]))
    reuse = mo["beta_0_reused"]
    for key in ("n", "f_hat", "ci_lo", "ci_hi", "r_m", "s2_rms_a", "power_at_f0"):
        chk(f"beta_0 reuse {key}", reuse[key], b0[key])
    verdicts["beta_0"] = {"verdict": v0, "n": b0["n"],
                          "extra_seed_verdicts": [verdict_of(e["n"], r3.num(e["ci_lo"]), r3.num(e["ci_hi"]))
                                                  for e in b0["extra_seeds"]],
                          "ci": [r3.num(b0["ci_lo"]), r3.num(b0["ci_hi"])], "f_hat": r3.num(b0["f_hat"]),
                          "r_m": b0["r_m"], "sqrt_s2_rms_a": math.sqrt(b0["s2_rms_a"]),
                          "power_at_f0": b0["power_at_f0"], "reused_from": "M4g3b",
                          "used_for_restore_in": sorted(g for g in GROUPS if sel[g] == "beta_0")}
    if errors:
        print("\n".join(errors[:60]), file=sys.stderr)
        fail(f"{len(errors)} mismatches (movers)")
    print("OK movers: 3 candidates × 6 streams within TOL; power within 4 combined SEs")
    if not args.write_expected:
        return

    # ── expected flips (ADR 004 §13.8 D4, D5; plan 005 M4g4 plan) ──
    status, detail, vector, selection = {}, {}, {}, {}
    for g in GROUPS:
        selection[g] = {m: mine[(m, g)]["selected"] for m in ref.defending[g]}
        vector[g] = sorted([{"metric": m, "candidate": c} for m, c in selection[g].items()
                            if c not in (None, "beta_0")], key=lambda d: d["metric"])
        s = selection[g][METRIC]
        v = None if s is None else verdicts[s]
        st = status_of(s, v)
        status[g] = st
        detail[g] = {"selected": s, "mover_verdict": None if v is None else v["verdict"],
                     "underpowered": None if v is None or v["power_at_f0"] is None else v["power_at_f0"] < LOW}
    dm = [d["metric"] for d in vector["DM-CM"]]
    EXPECTED.write_text(json.dumps({
        "description": "M4g4 expected registry and DNA changes (ADR 004 §13.8, plan 005 M4g4), written by "
                       "spikes/m4g4_possession_reference.py --write-expected BEFORE any registry code: "
                       "no existing cell changes; the five Defending score_status values (D5); the "
                       "adjusted vector columns per group (D4 = C); the ball_recoveries_p90 mover verdicts "
                       "per candidate (a candidate no group selected is report only, not used for any restore).",
        "snapshot_registry_version": reg["registry_version"],
        "status_changes": [], "note_changes": [], "reason_changes": [],
        "rules": {**rules, "threshold": THRESHOLD, "min_movers": MIN_N, "low_power": LOW, "extra_seeds": EXTRA},
        "defending_score_status": status, "status_detail": detail,
        "selection": selection, "vector_adjusted_columns": vector,
        "mover_verdicts": {c: verdicts[c] for c in CANDS},
        "compare_pairs": {"dm_cm_adjusted": dm,
                          "tested": any(m in dm for m in ("interceptions_p90", "ball_recoveries_p90"))},
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {EXPECTED}")


if __name__ == "__main__":
    main()
