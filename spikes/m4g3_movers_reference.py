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

- the five extra seeds (plan 005 M4g3b: 'm4g3_draws#k' / 'm4g3_boot#k', k = 1–5), in the
  output's extra_seeds and in the measurement file's movers block;
- --write-expected (plan 005 M4g3b, before any registry code): derives each metric's verdict and
  flags with its own reading of the ADR 004 §13.7 rule from the checked movers block, asserts that
  the new measurement file changes no section other than movers, and writes
  spikes/expected_m4g3_flips.json (no cell changes; one mover verdict per metric).

  .venv/bin/python spikes/m4g3_movers_reference.py --check-output data/interim/m4g3_movers.json \
      --registry data/interim/m4g3b_snapshot/registry.json
  .venv/bin/python spikes/m4g3_movers_reference.py --check-measurements NEW_MEASUREMENTS.json \
      --registry data/interim/m4g3b_snapshot/registry.json --write-expected

Hardening (plan 005 M4g4-0, the M4g3b backlog gate): the bootstrap size is checked against B, not
taken from the file under check; with both files given, each one's power is checked (and the two
must agree); the block's cross_check_f_hat, the extra seeds' k (paired by position) and the
absence of cross-check fields without a reference cross value are checked; R_m ≤ 0 has power 0.
s2_rms_a (plan 005 M4g4a, D7): Ŝ² on scale (a), Σ Δa² / Σ base_a (base_a = a mover's mean over
draws of the squared local-percentile half difference), checked in the output and the block,
present iff the metric is testable.

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
EXTRA = 5                       # plan 005 M4g3b: extra seeds k = 1..5
EXPECTED = Path(__file__).resolve().parent / "expected_m4g3_flips.json"
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
    """P(PASS | f = 0): pool of 250 non-movers, halves = s + e, Var(s) 1, Var(e) (1 − R)/R.
    n < MIN_N or R ≤ 0 (no stable between-player signal, so f̂ = +inf) → 0.0; the rng is not used."""
    if n < MIN_N or not rel > 0:
        return 0.0
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


def num(x) -> float:
    """A measurement-file number: '+inf' / '-inf' strings back to floats."""
    return {"+inf": math.inf, "-inf": -math.inf}.get(x, x) if isinstance(x, str) else x


def cmp(errors: list[str], where: str, a, e) -> None:
    """Append a mismatch: numbers within TOL, anything else by equality."""
    if not close(float(a), float(e)) if isinstance(e, (int, float)) and not isinstance(e, bool) \
            else a != e:
        errors.append(f"{where}: output {a!r} != reference {e!r}")


def header_errors(label: str, src: dict) -> list[str]:
    """The bootstrap size is the written spec's B, never taken from the file under check."""
    return [] if src.get("b") == B else [f"{label}: b {src.get('b')!r} != B {B}"]


def extra_k_errors(label: str, extra: list[dict]) -> list[str]:
    """Extra seeds are paired with the reference by position: entry i must carry k = i + 1."""
    return [f"{label} extra seed {i}: k {e.get('k')!r} != {i + 1}"
            for i, e in enumerate(extra) if e.get("k") != i + 1]


def block_metric_errors(metric: str, bm: dict, n: int, res: dict | None, cross: float | None,
                        extras: list[dict]) -> list[str]:
    """One metric of the measurement file's movers block against the reference (power aside)."""
    errors: list[str] = []
    w = f"block {metric}"
    cmp(errors, f"{w} n", bm["n"], n)
    if res is not None:
        for key, want in (("f_hat", res["f_hat"]), ("ci_lo", res["ci"][0]), ("ci_hi", res["ci"][1]),
                          ("r_m", res["r_m"]), ("s2_rms", res["s2_rms"]), ("boot_inf", res["boot_inf"]),
                          ("s2_rms_a", res["s2_rms_a"])):
            if key not in bm:
                errors.append(f"{w}: {key} missing")
            else:
                cmp(errors, f"{w} {key}", num(bm[key]), want)
        if "power_at_f0" not in bm or "power_se" not in bm:
            errors.append(f"{w}: power_at_f0 / power_se missing")
    elif any(key in bm for key in ("f_hat", "power_at_f0", "s2_rms_a")):
        errors.append(f"{w}: untestable but carries f_hat / power / s2_rms_a")
    if cross is None:
        errors += [f"{w}: {key} present without a reference cross-check value"
                   for key in ("cross_check_r", "cross_check_f_hat") if key in bm]
    else:
        if "cross_check_r" not in bm:
            errors.append(f"{w}: cross_check_r missing")
        else:
            cmp(errors, f"{w} cross_check_r", bm["cross_check_r"], cross)
        if res is None:
            if "cross_check_f_hat" in bm:
                errors.append(f"{w}: cross_check_f_hat present for an untestable metric")
        elif "cross_check_f_hat" not in bm:
            errors.append(f"{w}: cross_check_f_hat missing")
        else:
            cmp(errors, f"{w} cross_check_f_hat", num(bm["cross_check_f_hat"]),
                (res["s2_rms"] - 1) * (1 - cross) / cross)
    if len(bm["extra_seeds"]) != EXTRA:
        errors.append(f"{w}: {len(bm['extra_seeds'])} extra seeds, expected {EXTRA}")
    errors += extra_k_errors(w, bm["extra_seeds"])
    for e, x in zip(bm["extra_seeds"], extras):
        cmp(errors, f"{w} seed {e['k']} n", e["n"], x["n"])
        if x["res"]:
            for key, want in (("ci_lo", x["res"]["ci"][0]), ("ci_hi", x["res"]["ci"][1])):
                if key not in e:
                    errors.append(f"{w} seed {e['k']}: {key} missing")
                else:
                    cmp(errors, f"{w} seed {e['k']} {key}", num(e[key]), want)
        elif "ci_lo" in e or "ci_hi" in e:
            errors.append(f"{w} seed {e['k']}: untestable but carries a CI")
    return errors


def power_errors(metric: str, claimed: list[tuple[str, float, float]], mine: float) -> list[str]:
    """Each claimed (label, power, se) against the reference simulation `mine` (POWER_DATASETS):
    within t = 4 combined SEs. That also settles the < LOW label: if |power − mine| ≤ t and
    |mine − LOW| > t, power lies on mine's side of LOW, so the labels can differ only when mine
    is within t of LOW (the M4g3b rule; a separate label check could never fire).
    Two claims (output and block) must also agree with each other within TOL."""
    errors: list[str] = []
    if len(claimed) == 2 and not close(float(claimed[0][1]), float(claimed[1][1])):
        errors.append(f"{metric} power: {claimed[0][0]} {claimed[0][1]!r} != "
                      f"{claimed[1][0]} {claimed[1][1]!r}")
    for label, power, se_out in claimed:
        se = math.sqrt(se_out ** 2 + mine * (1 - mine) / POWER_DATASETS)
        if abs(power - mine) > 4 * max(se, 1e-3):
            errors.append(f"{metric} {label} power {power:.3f} vs reference {mine:.3f}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check-output", type=Path, help="spikes/m4g3_movers.py output JSON")
    ap.add_argument("--check-measurements", type=Path,
                    help="a measurement file whose movers block to check (plan 005 M4g3b)")
    ap.add_argument("--registry", type=Path, required=True,
                    help="registry snapshot taken BEFORE the measurement change")
    ap.add_argument("--write-expected", action="store_true",
                    help="write spikes/expected_m4g3_flips.json (needs --check-measurements)")
    args = ap.parse_args()
    if not (args.check_output or args.check_measurements):
        fail("nothing to check: give --check-output and/or --check-measurements")
    if args.write_expected and not args.check_measurements:
        fail("--write-expected needs --check-measurements")
    got = json.loads(args.check_output.read_text()) if args.check_output else None
    block_file = json.loads(args.check_measurements.read_text()) if args.check_measurements else None
    block = block_file["movers"] if block_file else None
    # min_attempts and r_full from the file under check (its other sections must equal the
    # committed ones for the expected flips: checked below), else the committed file
    meas = block_file if block_file else json.loads(MEASUREMENTS.read_text())
    reg = json.loads(args.registry.read_text())
    head = header_errors("output", got) if got else []
    head += header_errors("movers block", block) if block else []
    if head:
        fail("; ".join(head))
    b = B
    errors: list[str] = []

    def chk(where: str, a, e) -> None:
        cmp(errors, where, a, e)

    if any("dna" not in reg["groups"][g] for g in GROUPS):
        fail("registry snapshot: a group has no 'dna' list")
    metrics = sorted({m for g in GROUPS for m in reg["groups"][g]["dna"]}
                     | (set(DEN) - {"team_pass_share"}) | {"ball_recoveries_p90", "tackles_won_p90"})
    if got and sorted(got["metrics"]) != metrics:
        fail(f"metric set differs: {sorted(set(got['metrics']) ^ set(metrics))}")
    if block and sorted(block["metric_list"]) != metrics:
        fail(f"movers block metric list differs: {sorted(set(block['metric_list']) ^ set(metrics))}")
    if block and (block["seed"] != SEED or block["draws"] != DRAWS or block.get("min_movers") != MIN_N
                  or block["stream_keys"]["primary"] != ["m4g3_draws", "m4g3_boot"]
                  or block["stream_keys"]["extra"] != [[f"m4g3_draws#{k}", f"m4g3_boot#{k}"]
                                                       for k in range(1, EXTRA + 1)]):
        fail("movers block seed / draws / min_movers / stream keys differ from the written spec")

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
    if block and block["movers"] != len(movers):
        fail(f"movers block: {block['movers']} movers != {len(movers)}")
    if got and len(got["movers"]) != len(movers):
        fail(f"movers {len(got['movers'])} != {len(movers)}")
    for i, (o, st) in enumerate(zip(got["movers"] if got else [], movers)):
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

    def ref_metric(metric: str, k: int) -> dict:
        """One metric on stream k (0: primary; k ≥ 1: 'm4g3_draws#k' / 'm4g3_boot#k')."""
        sfx = "" if k == 0 else f"#{k}"
        rng = stream("m4g3_draws" + sfx, metric)
        d2, H1, H2, absa, absb, ba, bb, cross = [], [], [], [], [], [], [], []
        d2a, b2a = [], []
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
            d2a.append((p[1][0] - p[0][0]) ** 2)
            b2a.append(sum((x[1][0] - x[0][0]) ** 2 for x in ha) / DRAWS)
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
        out = {"n": len(d2), "absa": absa, "absb": absb, "ba": ba, "bb": bb, "res": None,
               "cross": float(np.mean(cross)) if cross else None}
        if len(d2) >= MIN_N:
            out["res"] = rule(np.array(d2), np.array(H1), np.array(H2), stream("m4g3_boot" + sfx, metric), b)
            out["res"]["s2_rms_a"] = sum(d2a) / sum(b2a)
        return out

    def verdict_of(n: int, lo: float | None, hi: float | None) -> str:
        if n < MIN_N:
            return "untestable"
        return "PASS" if hi < THRESHOLD else "FAIL" if lo > THRESHOLD else "inconclusive"

    expected: dict[str, dict] = {}
    for metric in metrics:
        r0 = ref_metric(metric, 0)
        n, res = r0["n"], r0["res"]
        extras = [ref_metric(metric, k) for k in range(1, EXTRA + 1)]
        if got:
            o = got["metrics"][metric]
            chk(f"{metric} n", o["n"], n)
            if n:
                chk(f"{metric} mover_mean_abs_a", o["mover_mean_abs_a"], float(np.mean(r0["absa"])))
                chk(f"{metric} mover_mean_abs_b", o["mover_mean_abs_b"], float(np.mean(r0["absb"])))
                chk(f"{metric} baseline_mean_abs_a", o["baseline_mean_abs_a"], float(np.mean(r0["ba"])))
                chk(f"{metric} baseline_mean_abs_b", o["baseline_mean_abs_b"], float(np.mean(r0["bb"])))
            if res is None:
                chk(f"{metric} verdict", o["verdict"], "untestable")
            else:
                for key in ("r_m", "f_hat", "s2_rms", "verdict", "point_pass", "boot_inf", "s2_rms_a"):
                    chk(f"{metric} {key}", o[key], res[key])
                chk(f"{metric} ci_lo", o["ci"][0], res["ci"][0])
                chk(f"{metric} ci_hi", o["ci"][1], res["ci"][1])
                if r0["cross"] is not None:
                    chk(f"{metric} cross r", o["cross_check"]["r_step_down"], r0["cross"])
                    chk(f"{metric} cross f", o["cross_check"]["f_hat_with_step_down"],
                        (res["s2_rms"] - 1) * (1 - r0["cross"]) / r0["cross"])
            errors.extend(extra_k_errors(f"output {metric}", got.get("extra_seeds", {}).get(metric, [])))
            for e, x in zip(got.get("extra_seeds", {}).get(metric, []), extras):
                chk(f"{metric} seed {e['k']} n", e["n"], x["n"])
                chk(f"{metric} seed {e['k']} verdict", e["verdict"],
                    x["res"]["verdict"] if x["res"] else "untestable")
                if x["res"]:
                    chk(f"{metric} seed {e['k']} ci_lo", e["ci"][0], x["res"]["ci"][0])
                    chk(f"{metric} seed {e['k']} ci_hi", e["ci"][1], x["res"]["ci"][1])
        power = None
        claimed: list[tuple[str, float, float]] = []
        if block:
            bm = block["metrics"][metric]
            errors.extend(block_metric_errors(metric, bm, n, res, r0["cross"], extras))
            if res is not None and "power_at_f0" in bm and "power_se" in bm:
                claimed.append(("block", bm["power_at_f0"], bm["power_se"]))
        if got and res is not None:
            lp = got["metrics"][metric]["low_power"]
            claimed.append(("output", lp["power_at_f0"], lp["se"]))
        # low power: independent simulation, tolerance; every claimed power is checked
        if res is not None and claimed:
            mine = power0(n, res["r_m"], np.random.default_rng([POWER_SEED, zlib.crc32(metric.encode())]))
            errors.extend(power_errors(metric, claimed, mine))
            power = claimed[0][1]
        # expected verdict and flags (ADR 004 §13.7 rule, derived here; plan 005 M4g3b)
        v0 = verdict_of(n, *(res["ci"] if res else (None, None)))
        vk = [verdict_of(x["n"], *(x["res"]["ci"] if x["res"] else (None, None))) for x in extras]
        expected[metric] = {
            "verdict": v0, "n": n,
            "underpowered": None if res is None or power is None else power < LOW,
            "borderline_seed": None if res is None else any(v != v0 for v in vk),
            "below_model_range": None if res is None else (v0 == "PASS" and res["ci"][1] < 0),
            "extra_seed_verdicts": vk,
        }
        print(f"{metric:32s} ok  {v0:12s} seeds {vk}", flush=True)

    kone = got["kone"][0] if got else None
    if got and (len(got["kone"]) != 1 or kone["player"] != "Lamine Koné"):
        fail(f"kone block: {[k['player'] for k in got['kone']]}")
    for st in movers if got else []:
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
    print(f"OK: {len(metrics)} metrics, {len(movers)} movers, {EXTRA} extra seeds, every field within {TOL:g}")
    if args.write_expected:
        committed = json.loads(MEASUREMENTS.read_text())
        others = sorted(k for k in set(committed) | set(block_file)
                        if k not in ("movers", "schema_version", "generated_by")
                        and committed.get(k) != block_file.get(k))
        if others:
            fail(f"the new measurement file changes sections other than movers: {others}")
        tally: dict[str, int] = defaultdict(int)
        for e in expected.values():
            tally[e["verdict"]] += 1
        EXPECTED.write_text(json.dumps({
            "description": "M4g3b expected registry changes (plan 005 M4g3b), written by "
                           "spikes/m4g3_movers_reference.py --write-expected BEFORE any registry code: "
                           "no cell changes; one mover verdict per metric with its flags.",
            "snapshot_registry_version": reg.get("registry_version"),
            "status_changes": [], "note_changes": [], "reason_changes": [],
            "rules": {"threshold": THRESHOLD, "min_movers": MIN_N, "low_power": LOW,
                      "extra_seeds": EXTRA},
            "summary": {**dict(sorted(tally.items())),
                        "underpowered": sum(bool(e["underpowered"]) for e in expected.values()),
                        "borderline_seed": sorted(m for m, e in expected.items() if e["borderline_seed"]),
                        "below_model_range": sorted(m for m, e in expected.items() if e["below_model_range"])},
            "mover_verdicts": expected}, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {EXPECTED}")


if __name__ == "__main__":
    main()
