"""
M4g3a mover stability: the real run (plan 005 M4g3, ADR 004 §13.1, §13.7).

Reads the season and player-match parquets, matches.json (dates, results), the committed
measurements (min_attempts, r_full) and a registry snapshot (tools/registry_snapshot.py; which
metrics are DNA where). Writes data/interim/m4g3_movers.json and prints the results. Report only:
it changes no status. Never imports eleveniq (tests/test_spikes_independent.py).

Spec (plan 005 M4g3 and ADR 004 §13.7, as amended 2026-10-08):
- Movers: outfield player-seasons with exactly two stints of ≥ 450 nominal minutes in the same
  position_group, any pool-league pair; stints in date order (first match played). A smaller
  third stint is ignored and listed; a stint outside the pool leagues fails the run.
- Metrics: every metric that is DNA in some outfield group, every ratio, and the Defending
  members ball_recoveries_p90, tackles_won_p90. All movers are pooled for every metric.
- Values: per-90 = count / minutes_played × 90; ratio = num / (Σ den+ − Σ den−) (as the season
  parquet); team_pass_share = open-play passes / team open-play passes while on. A ratio stint
  or baseline half below the group's min_attempts is excluded (the mover for that metric; the
  non-mover from that mover's eligible set).
- Reference population (per league × group): pool, outfield, gated (≥ 900 − 1e-6 nominal),
  one row per player (D3: most minutes, then lowest competition_id, team_id); ratios keep rows
  at ≥ min_attempts.
  - scale (a): Hazen local percentile against it (report only);
  - scale (b): (value − league × group mean) / the group's SD of league-demeaned values (ddof 1).
- Baseline: non-movers are single-row players (one row in the season) in the pool, gated, same
  group. For mover i (m1, m2 nominal minutes), a non-mover with ≥ m1 + m2 is eligible; its
  matches (minutes > 0, by date then match_id) are cut at the whole match nearest m1 nominal
  minutes, then at the match nearest a further m2. 200 draws with replacement per mover and
  metric, from that mover's eligible set (non-movers in season-parquet row order). Half sums
  use math.fsum (exactly rounded), so values and Hazen ties do not depend on summation order.
- RNG: one stream per metric for the draws and one for the bootstrap, seeded from
  SEED = 20261008 and the metric name (m4g3_power.cell_rng), so adding a metric leaves the
  others unchanged. This refines plan 005's single default_rng(20261008).
- Decision: m4g3_power.mover_rule on scale (b) (Ŝ² RMS form, R_m pooled over all draws and
  recomputed per resample, two-sided 95% percentile CI; PASS / FAIL / inconclusive; fewer than
  12 movers → untestable).
- Low power (written before f̂): m4g3_power.low_power at the metric's own n and R_m.
- Cross-check (report only): per mover √(r(m1)·r(m2)), r(x) = r_full stepped down by
  Spearman-Brown with k = x / the cell's median (minutes for per-90s and team_pass_share,
  attempts for ratios), the cell = the group's gated pool rows; averaged over the metric's
  movers. f̂ with the cross-check in place of R_m is reported next to it. A gap > 0.10 is flagged.

Usage:
  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g3_registry.json
  .venv/bin/python spikes/m4g3_movers.py --registry data/interim/m4g3_registry.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g3_power as pw  # noqa: E402  (the rule, low power, cell_rng, order_by_date)
from reference_totals import PROJECT_ROOT, load_matches  # noqa: E402

SEASON_PARQUET = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
MEASUREMENTS = PROJECT_ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
OUT = PROJECT_ROOT / "data" / "interim" / "m4g3_movers.json"

SEASON_ID = 27
POOL = (2, 7, 11, 12)
OUTFIELD = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
MOVER_FLOOR, GATE, TOL = 450.0, 900.0, 1e-6
DRAWS = pw.DRAWS
GAP_FLAG = 0.10
EXTRA = ("ball_recoveries_p90", "tackles_won_p90")
RATIOS = {   # m4_decision_measurements.RATIOS_ALL, written out
    "pass_completion_pct": ("passes_completed", ["passes_attempted"],
                            ["passes_unknown_outcome", "passes_injury_clearance"]),
    "long_pass_completion_pct": ("long_passes_completed", ["long_passes_attempted"],
                                 ["long_passes_unknown_outcome", "long_passes_injury_clearance"]),
    "cross_completion_pct": ("crosses_completed", ["crosses_attempted"], ["crosses_unknown_outcome"]),
    "dribble_success_pct": ("dribbles_completed", ["dribbles_attempted"], []),
    "tackle_success_pct": ("tackles_won", ["tackles_attempted"], []),
    "aerial_win_pct": ("aerials_won", ["aerials_won", "aerials_lost"], []),
    "defensive_duel_success_pct": ("tackles_won", ["tackles_attempted", "dribbled_past"], []),
    "np_xg_per_shot": ("np_xg", ["np_shots"], []),
}
SHARE = {"team_pass_share": ("open_play_passes_attempted", ["team_open_play_passes_while_on"], [])}
KEY = ("player_id", "team_id", "competition_id")
KONE = "Lamine Koné"             # the Koné illustration (plan 005 M4g item list)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


# ---------------------------------------------------------------- metric definitions

def spec(metric: str) -> tuple[str, str, list[str], list[str]]:
    """(kind, numerator, den+, den−); per-90s use minutes_played as the denominator."""
    if metric in RATIOS:
        return ("ratio", *RATIOS[metric])
    if metric in SHARE:
        return ("share", *SHARE[metric])
    if metric.endswith("_p90"):
        return ("per90", metric[: -len("_p90")], ["minutes_played"], [])
    fail(f"unknown metric {metric}")
    raise AssertionError


def value(kind: str, num: float, den: float) -> float:
    if den <= 0:
        return math.nan
    return num / den * 90.0 if kind == "per90" else num / den


def sb_step(r: float, k: float) -> float:
    """Spearman-Brown: the reliability at k times the length (k < 1 steps down)."""
    return k * r / (1 + (k - 1) * r)


def hazen(sorted_pop: np.ndarray, x: float) -> float:
    lo = np.searchsorted(sorted_pop, x, side="left")
    hi = np.searchsorted(sorted_pop, x, side="right")
    return float((lo + 0.5 * (hi - lo)) / len(sorted_pop) * 100.0)


def cut(cum_nominal: np.ndarray, m1: float, m2: float) -> tuple[int, int]:
    """Match counts (k1, k2): the first half is matches [0, k1), the second [k1, k2).
    cum_nominal[k] = nominal minutes of the first k matches (cum_nominal[0] = 0). k1 ≥ 1 is the
    whole match nearest m1; k2 > k1 the one nearest cum[k1] + m2 (ties → the earlier match)."""
    n = len(cum_nominal) - 1
    k1 = 1 + int(np.argmin(np.abs(cum_nominal[1:n] - m1)))          # leave ≥ 1 match
    k2 = k1 + 1 + int(np.argmin(np.abs(cum_nominal[k1 + 1:] - cum_nominal[k1] - m2)))
    return k1, k2


# ---------------------------------------------------------------- data

def load(registry_path: Path):
    season = pl.read_parquet(SEASON_PARQUET).filter(pl.col("season_id") == SEASON_ID)
    meas = json.loads(MEASUREMENTS.read_text())
    reg = json.loads(registry_path.read_text())
    dna_groups: dict[str, list[str]] = defaultdict(list)
    for g in OUTFIELD:
        if "dna" not in reg["groups"][g]:
            fail(f"registry snapshot: group {g} has no 'dna' list")
        for m in reg["groups"][g]["dna"]:
            dna_groups[m].append(g)
    metrics = sorted(set(dna_groups) | set(RATIOS) | set(EXTRA))

    dates, results = {}, []
    for comp in POOL:
        for mt in load_matches(comp, SEASON_ID):
            dates[mt["match_id"]] = mt["match_date"]
            results.append((comp, mt["home_team"]["home_team_id"], mt["away_team"]["away_team_id"],
                            mt["home_score"], mt["away_score"]))
    cols = {"minutes_played", "match_duration"}
    for m in metrics:
        _, num, pos, neg = spec(m)
        cols |= {num, *pos, *neg}
    match = (pl.read_parquet(MATCH_PARQUET, columns=["match_id", *KEY, *sorted(cols)])
             .filter(pl.col("competition_id").is_in(POOL) & (pl.col("minutes_played") > 0))
             .with_columns(pl.col("match_id").replace_strict(dates, return_dtype=pl.Utf8)
                           .alias("match_date"),
                           (pl.col("minutes_played") * 90.0 / pl.col("match_duration"))
                           .alias("nominal"))
             .sort("player_id", "team_id", "match_date", "match_id"))
    return season, match, meas, metrics, dict(dna_groups), results


def league_ranks(results) -> dict[tuple[int, int], tuple[int, int]]:
    """(competition_id, team_id) → (final rank, teams in the league) by points, goal difference,
    goals for (a simple table from matches.json; report only)."""
    tab: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0, 0])
    for comp, h, a, hs, as_ in results:
        for t, gf, ga in ((h, hs, as_), (a, as_, hs)):
            row = tab[(comp, t)]
            row[0] += 3 if gf > ga else 1 if gf == ga else 0
            row[1] += gf - ga
            row[2] += gf
    out = {}
    for comp in POOL:
        teams = sorted((k for k in tab if k[0] == comp), key=lambda k: tab[k], reverse=True)
        for i, k in enumerate(teams):
            out[k] = (i + 1, len(teams))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True,
                    help="registry snapshot JSON (tools/registry_snapshot.py)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--b", type=int, default=pw.B)
    ap.add_argument("--power-datasets", type=int, default=pw.DATASETS)
    args = ap.parse_args()

    season, match, meas, metrics, dna_groups, results = load(args.registry)
    ranks = league_ranks(results)

    # movers ---------------------------------------------------------------------------
    st = season.filter((pl.col("minutes_nominal_equiv") >= MOVER_FLOOR - TOL)
                       & (pl.col("position_group") != "GK"))
    per = st.group_by("player_id").agg(pl.len().alias("n"),
                                        pl.col("position_group").n_unique().alias("ng"))
    mover_ids = per.filter((pl.col("n") == 2) & (pl.col("ng") == 1))["player_id"].to_list()
    rows_all = season.filter(pl.col("player_id").is_in(mover_ids))
    stint_dates = {k: (g["match_date"].min(), g["match_date"].max())
                   for k, g in match.filter(pl.col("player_id").is_in(mover_ids))
                   .group_by(list(KEY))}
    stint_dates = {tuple(k): v for k, v in stint_dates.items()}
    movers, third = [], []
    for pid in sorted(mover_ids):
        rows = rows_all.filter(pl.col("player_id") == pid).to_dicts()
        big = [r for r in rows if r["minutes_nominal_equiv"] >= MOVER_FLOOR - TOL]
        third += [r for r in rows if r["minutes_nominal_equiv"] < MOVER_FLOOR - TOL]
        for r in big:
            if r["competition_id"] not in POOL:
                fail(f"mover {r['player_name']} has a stint outside the pool leagues")
            r["first"], r["last"] = stint_dates[(r["player_id"], r["team_id"], r["competition_id"])]
        movers.append(pw.order_by_date(big))
    print(f"movers: {len(movers)} (third stints below {MOVER_FLOOR:.0f}: {len(third)})")

    # reference populations and non-movers ----------------------------------------------
    gated = season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield")
                          & (pl.col("minutes_nominal_equiv") >= GATE - TOL))
    local = (gated.sort(["minutes_played", "competition_id", "team_id"], descending=[True, False, False])
             .unique(subset=["player_id", "competition_id", "position_group"], keep="first",
                     maintain_order=True))
    singles = season.group_by("player_id").len().filter(pl.col("len") == 1)["player_id"]
    nonmovers = gated.filter(pl.col("player_id").is_in(singles.implode())).to_dicts()
    nm_matches = {k: g for k, g in match.group_by(list(KEY))}
    nm_matches = {tuple(k): g for k, g in nm_matches.items()}

    def min_att(metric: str, grp: str) -> int | None:
        """The group's min_attempts for a ratio (None: not defined for this group, so its
        movers are excluded for the metric); 0 for per-90s and team_pass_share."""
        return meas["ratios"][metric]["groups"][grp]["min_attempts"] if metric in RATIOS else 0

    def row_value(r: dict, metric: str) -> tuple[float, float]:
        kind, num, pos, neg = spec(metric)
        den = sum(r[c] for c in pos) - sum(r[c] for c in neg)
        return value(kind, r[num], den), den

    pop_cache: dict[tuple[str, str], dict] = {}

    def population(metric: str, grp: str) -> dict:
        """per league: sorted values, mean; the group's demeaned SD; the cell's median exposure."""
        if (metric, grp) in pop_cache:
            return pop_cache[(metric, grp)]
        ma = min_att(metric, grp)
        if ma is None:
            fail(f"population for {metric} × {grp} requested without a min_attempts")
        by_league, resid = {}, []
        for comp in POOL:
            vals = []
            for r in local.filter((pl.col("competition_id") == comp)
                                  & (pl.col("position_group") == grp)).to_dicts():
                v, den = row_value(r, metric)
                if not math.isnan(v) and den >= ma:
                    vals.append(v)
            a = np.sort(np.array(vals))
            if not len(a):
                fail(f"empty reference population: {metric} × {grp} × competition {comp}")
            by_league[comp] = {"sorted": a, "mean": float(a.mean())}
            resid += list(a - a.mean())
        cell = [row_value(r, metric)[1] for r in gated.filter(pl.col("position_group") == grp).to_dicts()]
        kind = spec(metric)[0]
        cell_minutes = gated.filter(pl.col("position_group") == grp)["minutes_nominal_equiv"]
        out = {"league": by_league, "sd": float(np.std(resid, ddof=1)),
               "median_exposure": float(np.median(cell)) if kind == "ratio"
               else float(cell_minutes.median()), "cell_n": len(cell)}
        pop_cache[(metric, grp)] = out
        return out

    def scales(metric: str, grp: str, comp: int, v: float) -> tuple[float, float]:
        p = population(metric, grp)
        lg = p["league"][comp]
        return hazen(lg["sorted"], v), (v - lg["mean"]) / p["sd"]

    # non-mover halves per mover (shared by all metrics) ---------------------------------
    # half sums of every needed column, per (mover, eligible non-mover)
    need = sorted({c for m in metrics for c in (spec(m)[1], *spec(m)[2], *spec(m)[3])})
    halves: list[list[dict]] = []
    for stints in movers:
        grp = stints[0]["position_group"]
        m1, m2 = stints[0]["minutes_nominal_equiv"], stints[1]["minutes_nominal_equiv"]
        cand = []
        for r in nonmovers:
            if r["position_group"] != grp or r["minutes_nominal_equiv"] < m1 + m2 - TOL:
                continue
            g = nm_matches[(r["player_id"], r["team_id"], r["competition_id"])]
            cum = np.concatenate([[0.0], np.cumsum(g["nominal"].to_numpy())])
            k1, k2 = cut(cum, m1, m2)
            arr = g.select(need).to_numpy().astype(float)
            # math.fsum: exactly rounded, so the half values (and Hazen ties) do not depend
            # on the summation order
            cand.append({"player_id": r["player_id"], "comp": r["competition_id"],
                         "h1": {c: math.fsum(arr[:k1, i]) for i, c in enumerate(need)},
                         "h2": {c: math.fsum(arr[k1:k2, i]) for i, c in enumerate(need)},
                         "nominal": (float(cum[k1]), float(cum[k2] - cum[k1]))})
        halves.append(cand)

    # per metric ------------------------------------------------------------------------
    out_metrics = {}
    for metric in metrics:
        kind = spec(metric)[0]
        d2, h1s, h2s, absa, absb, base_a, base_b, cross, ids, excl = ([] for _ in range(10))
        rng_draw = pw.cell_rng("m4g3_draws", metric)
        for stints, cand in zip(movers, halves):
            grp = stints[0]["position_group"]
            ma = min_att(metric, grp)
            if ma is None:
                excl.append(f"{stints[0]['player_name']} (no min_attempts for {grp})")
                continue
            v = [row_value(s, metric) for s in stints]
            if any(math.isnan(x) or den < ma for x, den in v):
                excl.append(stints[0]["player_name"])
                continue
            elig = []
            for c in cand:
                hv = [row_value(c[h], metric) for h in ("h1", "h2")]
                if all(not math.isnan(x) and den >= max(ma, 1e-12) for x, den in hv):
                    elig.append((c["comp"], hv[0][0], hv[1][0]))
            if not elig:
                excl.append(stints[0]["player_name"] + " (no eligible baseline)")
                continue
            pa = [scales(metric, grp, s["competition_id"], x) for s, (x, _) in zip(stints, v)]
            pick = rng_draw.integers(0, len(elig), DRAWS)
            hb = np.array([[scales(metric, grp, elig[j][0], elig[j][1 + h]) for h in (0, 1)]
                           for j in pick])                       # (draws, half, scale)
            d2.append((pa[1][1] - pa[0][1]) ** 2)
            absa.append(abs(pa[1][0] - pa[0][0]))
            absb.append(abs(pa[1][1] - pa[0][1]))
            h1s.append(hb[:, 0, 1])
            h2s.append(hb[:, 1, 1])
            base_a.append(float(np.mean(np.abs(hb[:, 1, 0] - hb[:, 0, 0]))))
            base_b.append(float(np.mean(np.abs(hb[:, 1, 1] - hb[:, 0, 1]))))
            ids.append(stints[0]["player_name"])
            # cross-check
            src = meas["ratios" if kind == "ratio" else "metrics"].get(metric) if kind != "share" \
                else {"groups": meas["team_pass_share"]}
            cellm = (src or {}).get("groups", {}).get(grp, {})
            rf = cellm.get("split_half_r_full", cellm.get("r_full"))
            if rf is not None:
                med = population(metric, grp)["median_exposure"]
                ex = [den if kind == "ratio" else s["minutes_nominal_equiv"]
                      for s, (_, den) in zip(stints, v)]
                rr = [sb_step(rf, e / med) for e in ex]
                if min(rr) > 0:
                    cross.append(math.sqrt(rr[0] * rr[1]))
        n = len(d2)
        rec = {"kind": kind, "dna_in": dna_groups.get(metric, []), "n": n, "excluded": excl}
        if n:
            d2a, h1a, h2a = np.array(d2), np.array(h1s), np.array(h2s)
            rec |= {"mover_mean_abs_a": float(np.mean(absa)), "mover_mean_abs_b": float(np.mean(absb)),
                    "baseline_mean_abs_a": float(np.mean(base_a)),
                    "baseline_mean_abs_b": float(np.mean(base_b)),
                    "s_mean_a": float(np.mean(absa) / np.mean(base_a)),
                    "s_mean_b": float(np.mean(absb) / np.mean(base_b))}
        if n >= pw.MIN_MOVERS:
            # R_m and the low-power label first (ADR 004 §13.7: before f̂)
            _, _, r_m = pw._f_hat(np.float64(1.0), np.float64(1.0),
                                  pw._moments(h1a, h2a).sum(0))
            rec["r_m"] = float(r_m)
            rec["low_power"] = pw.low_power(n, float(r_m), args.power_datasets, args.b)
            res = pw.mover_rule(d2a, h1a, h2a, pw.cell_rng("m4g3_boot", metric), b=args.b)
            rec |= {"f_hat": res.f_hat, "s2_rms": res.s2, "s_rms": math.sqrt(res.s2),
                    "ci": [res.ci_lo, res.ci_hi], "verdict": res.verdict,
                    "point_pass": res.point_pass, "boot_inf": res.boot_inf}
        else:
            rec["verdict"] = "untestable"
        if cross:
            cc = float(np.mean(cross))
            rec["cross_check"] = {"r_step_down": cc, "n": len(cross)}
            if "r_m" in rec:
                rec["cross_check"]["gap"] = rec["r_m"] - cc
                rec["cross_check"]["gap_flag"] = abs(rec["r_m"] - cc) > GAP_FLAG
                rec["cross_check"]["f_hat_with_step_down"] = (rec["s2_rms"] - 1) * (1 - cc) / cc
        rec["movers"] = ids
        out_metrics[metric] = rec
        lp = rec.get("low_power", {})
        print(f"{metric:32s} n={n:2d} {rec['verdict']:12s} "
              + (f"f̂={rec['f_hat']:.3f} CI=[{rec['ci'][0]:.3f}, {rec['ci'][1]:.3f}] "
                 f"R_m={rec['r_m']:.3f} power={lp['power_at_f0']:.2f}{'*' if lp['underpowered'] else ''} "
                 f"S_rms={rec['s_rms']:.2f} S_a={rec['s_mean_a']:.2f}"
                 if "f_hat" in rec else ""), flush=True)

    # Koné and struggling clubs -------------------------------------------------------------
    stint_rows = []
    for stints in movers:
        s1, s2 = stints
        r1 = ranks.get((s1["competition_id"], s1["team_id"]))
        r2 = ranks.get((s2["competition_id"], s2["team_id"]))
        stint_rows.append({"player": s1["player_name"], "group": s1["position_group"],
                           "from": s1["team_name"], "from_rank": r1, "to": s2["team_name"],
                           "to_rank": r2, "m1": s1["minutes_nominal_equiv"],
                           "m2": s2["minutes_nominal_equiv"],
                           "cross_league": s1["competition_id"] != s2["competition_id"],
                           "first": [str(s1["first"]), str(s2["first"])]})
    kone = [s for s in movers if s[0]["player_name"] == KONE]
    if len(kone) != 1:
        fail(f"{KONE}: {len(kone)} movers with that name, expected 1")
    kone_out = []
    for stints in kone:
        grp = stints[0]["position_group"]
        vals = [row_value(s, "long_pass_completion_pct") for s in stints]
        kone_out.append({"player": stints[0]["player_name"], "group": grp,
                         "stints": [{"team": s["team_name"], "value": v, "attempts": den,
                                     "scale_a": scales("long_pass_completion_pct", grp, s["competition_id"], v)[0],
                                     "scale_b": scales("long_pass_completion_pct", grp, s["competition_id"], v)[1]}
                                    for s, (v, den) in zip(stints, vals)]})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "generated_by": "spikes/m4g3_movers.py", "seed": pw.SEED, "draws": DRAWS, "b": args.b,
        "registry_version": json.loads(args.registry.read_text()).get("registry_version"),
        "movers": stint_rows, "third_stints": [r["player_name"] for r in third],
        "kone": kone_out, "metrics": out_metrics}, indent=1, default=str))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
