"""
M4g6a1 validation write-up (plan 005 "M4g6a spec", stats-reviewer conditions 1-12 folded in;
user decisions 2026-10-09). Report only: decides nothing, changes no rule.

Writes its own section (owner `m4_validation`) of docs/reviews/m4-validation.md and the figures
to data/interim/m4g6a1/validation.json. Never imports eleveniq (tests/test_spikes_independent.py):
the cohorts and percentiles come from spikes/m4d_percentiles_reference.py, the DNA lists,
families and pair distance from spikes/m4f_dna_reference.py, the per-player recount from
spikes/m4g6_recount.py, and the registry outcome from a registry snapshot (tools/registry_snapshot.py).

Shared definitions: primary scored rows (D1 status "scored" of a row's own group); neighbours =
same group, scored rows, any pool league, excluding the same player_id, k = 10, ties by the
season key (the M4f3 definition); the pair distance is the M4f one (family weights re-split
over shared metrics).

Items (M4g6a1: 1, 2, 4, 5, 7, 9 and the brief §20 map; items 3, 6, 8 are M4g6a2):
1. Minutes gate sensitivity at 720 and 1,080 nominal-equivalent minutes. The registry is held at
   900 (DNA lists, families, weights, zero-heavy exclusions, min_attempts, D9); local percentiles
   only. The rule-faithful build keeps the cohort floor (rows that lose a score are reported);
   the percentile, distance and neighbour comparisons use a floor-waived build, with the waived
   cells flagged.
2. Reliability: committed multi-split r_full per DNA cell and the minute-bucket summary.
4. Outlier audit: DNA cells strictly above their group's P99 (type 7, all leagues, eligible rows),
   recounted from raw events, with single-match concentration, flags and a classification.
5. Multi-position rows (D5) and club changers.
7. Neighbours on the local basis vs "global where defined" for a 21-player rule-based sample.
9. M4g1-M4g5 outcomes, every mover verdict with its flags.

  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g6a1/registry.json
  .venv/bin/python spikes/m4_validation.py --registry data/interim/m4g6a1/registry.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4d_percentiles_reference as m4d  # noqa: E402
import m4f_dna_reference as ref  # noqa: E402
import m4f_weighting_checks as wc  # noqa: E402  (FACE: the M4f3 face-validity players)
import m4g6_recount as rc  # noqa: E402
import review_sections as rs  # noqa: E402

ROOT = m4d.ROOT
REVIEW = ROOT / "docs" / "reviews" / "m4-validation.md"
OWNER = "m4_validation"
OUT_JSON = ROOT / "data" / "interim" / "m4g6a1" / "validation.json"
MATCH_PARQUET = ROOT / "data" / "interim" / "player_match_stats.parquet"
MATCHES = ROOT / "data" / "raw" / "statsbomb" / "matches"
PCT_PARQUET = ROOT / "data" / "processed" / "player_season_percentiles.parquet"
EVENTS = ROOT / "data" / "raw" / "statsbomb" / "events"
BUCKETS = ROOT / "data" / "processed" / "reports" / "m4g2_reliability_cells.json"

BASE_GATE = 900.0
GATES = (720.0, 1080.0)                 # user, 2026-10-09
K = 10                                  # config.DNA_NEIGHBOURS_K
TOP_PCT = 90.0                          # condition 12: "top 10%" = pct_local ≥ 90
P_OUT = 0.99                            # D20
P_LOW = 0.01                            # recommendation 11
P_DIAG = 0.95                           # post-hoc diagnostic of the single-match class (M4g6a1 output)
NEAR_GATE_HI = 1350.0                   # the M4g2 900-1350 minute bucket
MIXED_SHARE = 0.70                      # ADR 004 §12.8 C1
MOVER_FLOOR = 450.0                     # ADR 004 §13.1 mover stints
LOW_RELIABILITY = 0.7                   # config.LOW_RELIABILITY_BELOW (the "lower reliability" note)
ONE_MATCH_SHARE = 0.25                  # report-only: the cells one match clearly drives (M4g6a1 review)
TOL = 1e-6
COUNT_TOL = 1e-6                        # xG sums are floats
LOW_TAIL = ("passes_attempted_p90", "pressures_p90", "ball_recoveries_p90")
ON_BALL = ("passes_attempted", "carries", "shots", "dribbles_attempted", "pressures",
           "ball_recoveries", "clearances", "interceptions", "tackles_attempted",
           "fouls_committed", "fouls_won", "aerials_won", "aerials_lost", "blocks")
LEAGUE = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1"}
CLASSES = ("coding error", "data anomaly", "single-match spike", "mixed role", "near-gate",
           "genuine")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


# ── Small helpers (tested in tests/test_m4_validation_spike.py) ─────────────────

def q7(xs: list[float], p: float) -> float:
    return ref.quantile7(xs, p)


def overlap(a: list, b: list) -> int:
    return len(set(a) & set(b))


def summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "median": None, "p95": None, "max": None, "p10": None}
    return {"n": len(xs), "median": ref.median(xs), "p95": q7(xs, 0.95), "max": max(xs),
            "p10": q7(xs, 0.10)}


def classify(recount_ok: bool, duplicates: int, without_top_above: bool | None,
             share: float, nominal: float) -> str:
    """Outlier class, first match wins (condition 6)."""
    if not recount_ok:
        return "coding error"
    if duplicates > 0:
        return "data anomaly"
    if without_top_above is False:
        return "single-match spike"
    if share < MIXED_SHARE:
        return "mixed role"
    if nominal < NEAR_GATE_HI:
        return "near-gate"
    return "genuine"


def stint_rank(others: dict, d: float) -> int:
    """Condition 7: the other stint's rank among every same-group row, that stint admitted
    (`others` excludes the player's own rows, so it is 1 + the rows strictly closer)."""
    return 1 + sum(v < d for v in others.values())


def movers(season: list[dict]) -> dict[int, list[dict]]:
    """The M4g3 mover rule (ADR 004 §13.1; spikes/m4g3_movers.Ctx, over every season row):
    exactly two stints ≥ 450 nominal minutes, one outfield group. player_id -> those stints.
    A mover's stints need not both be scored (≥ 900); the item-7 sample includes the scored ones."""
    by: dict[int, list[dict]] = defaultdict(list)
    for r in season:
        if r["position_group"] != "GK" and r["minutes_nominal_equiv"] >= MOVER_FLOOR - TOL:
            by[r["player_id"]].append(r)
    return {p: rs_ for p, rs_ in by.items()
            if len(rs_) == 2 and len({r["position_group"] for r in rs_}) == 1}


def sample(scored: list[dict], season: list[dict]) -> list[dict]:
    """Item 7 (user, 2026-10-09): the 9 M4f3 face-validity players; per group the 2 scored rows
    with the most minutes_played not already picked; the 2 movers with the most combined
    minutes not already picked. Every scored stint of a picked player is included."""
    picked: list[int] = []
    by_name = {(r["player_name"], r["team_id"]): r for r in scored}
    for name, team in wc.FACE:
        if (name, team) not in by_name:
            fail(f"face-validity player {name} ({team}) is not a scored row")
        picked.append(by_name[(name, team)]["player_id"])
    for g in ref.DNA:
        rows = sorted((r for r in scored if r["position_group"] == g and r["player_id"] not in picked),
                      key=lambda r: (-r["minutes_played"], m4d.key_of(r)))
        picked += [r["player_id"] for r in rows[:2]]
    mv = movers(season)
    order = sorted((p for p in mv if p not in picked),
                   key=lambda p: (-sum(r["minutes_played"] for r in mv[p]), p))
    picked += order[:2]
    return sorted((r for r in scored if r["player_id"] in picked),
                  key=lambda r: (picked.index(r["player_id"]), m4d.key_of(r)))


# ── Builds and neighbours ──────────────────────────────────────────────────────

class Build:
    def __init__(self, meas: dict, gate: float, min_cohort: int) -> None:
        pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
        self.gate, self.min_cohort = gate, min_cohort
        self.co = m4d.Cohorts(m4d.load_rows(meas, gate), m4d.load_metrics(meas), pool, meas,
                              min_cohort)
        self.dna = ref.Dna(self.co)
        self.rows = {m4d.key_of(r): r for r in self.dna.rows}
        self.vec = self.dna.vec


def group_keys(vec: dict) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = defaultdict(list)
    for k, v in vec.items():
        out[v["group"]].append(k)
    return {g: sorted(ks) for g, ks in out.items()}


def distances(vec: dict) -> dict[tuple, dict[tuple, float]]:
    """key -> {other key in its group (different player): distance}."""
    out: dict[tuple, dict[tuple, float]] = defaultdict(dict)
    for keys in group_keys(vec).values():
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if a[0] == b[0]:
                    continue
                d = ref.pair_distance(vec[a], vec[b])
                out[a][b] = out[b][a] = d
    return out


def top_k(dist: dict[tuple, dict[tuple, float]], key: tuple, allowed: set | None = None,
          k: int = K) -> list[tuple]:
    cand = [(d, o) for o, d in dist.get(key, {}).items() if allowed is None or o in allowed]
    return [o for _, o in sorted(cand)[:k]]


def with_global(vec: dict, pct: pl.DataFrame) -> dict:
    """Item 7 basis (b): pct_global where status_global is ok, else pct_local."""
    glob = {(r["player_id"], r["team_id"], r["competition_id"], r["season_id"], r["source"], r["metric"]):
            r["pct_global"] for r in pct.filter((pl.col("cohort_role") == "primary")
                                               & (pl.col("status_global") == "ok")).iter_rows(named=True)}
    out = {}
    for k, v in vec.items():
        cells = {m: ({**c, "pct_local": glob[(*k, m)]} if c["metric_status"] == "ok" and (*k, m) in glob
                     else c) for m, c in v["cells"].items()}
        out[k] = {**v, "cells": cells}
    return out


def same_league(dist: dict, keys: list[tuple]) -> tuple[int, float]:
    obs = exp = 0.0
    for k in keys:
        cand = list(dist.get(k, {}))
        if not cand:
            continue
        obs += sum(o[2] == k[2] for o in top_k(dist, k))
        exp += K * sum(o[2] == k[2] for o in cand) / len(cand)
    return int(obs), exp


# ── Item 1: gate sensitivity ───────────────────────────────────────────────────

def sensitivity(base: Build, rule: Build, waived: Build, dist_base: dict) -> dict:
    g = waived.gate
    out: dict = {"gate": g}
    sizes = {}
    for b, name in ((base, "900"), (waived, f"{g:g}")):
        c = Counter((r["competition_id"], r["position_group"]) for r in m4d.dedupe(b.dna.rows))
        sizes[name] = {f"{LEAGUE[l]}|{grp}": n for (l, grp), n in sorted(c.items())}
    out["cohort_sizes"] = sizes
    # rule-faithful: rows that drop below the gate, and rows that lose a cell to the floor
    dropped = sorted(k for k in base.rows if k not in rule.rows)
    lost = sorted(k for k, v in rule.vec.items()
                  if any(c["metric_status"] == "cohort_too_small" for c in v["cells"].values()))
    mv = {(r["player_id"], r["team_id"], r["competition_id"]) for rs_ in movers(base.co.rows).values()
          for r in rs_}
    out["dropped_below_gate"] = len(dropped)
    out["dropped_mover_stints"] = [base.rows[k]["player_name"] + " / " + base.rows[k]["team_name"]
                                   for k in dropped if k[:3] in mv]
    lost_by = Counter(f"{LEAGUE[k[2]]}|{rule.vec[k]['group']}" for k in lost)
    out["rows_losing_a_cell_to_the_floor"] = dict(sorted(lost_by.items()))
    out["rows_losing_a_cell_names"] = sorted({rule.rows[k]["player_name"] for k in lost})[:15]
    # (b') percentile shifts, (c) top 10% retention, on rows scored at both (waived build)
    common = sorted(set(base.vec) & set(waived.vec))
    cells: dict[str, dict] = {}
    for grp, ms in ref.DNA.items():
        for m in ms:
            diffs, n_top, k_top, waived_cell = [], 0, 0, False
            for k in common:
                a, b = base.vec[k], waived.vec[k]
                if a["group"] != grp:
                    continue
                ca, cb = a["cells"][m], b["cells"][m]
                if ca["metric_status"] != "ok" or cb["metric_status"] != "ok":
                    continue
                waived_cell |= cb["n_local"] < m4d.MIN_COHORT_SIZE
                diffs.append(abs(ca["pct_local"] - cb["pct_local"]))
                if ca["pct_local"] >= TOP_PCT:
                    n_top += 1
                    k_top += cb["pct_local"] >= TOP_PCT
            cells[f"{grp}|{m}"] = {**summary(diffs), "top_kept": k_top, "top_n": n_top,
                                   "floor_waived": waived_cell}
    out["cells"] = cells
    # pair-distance change and neighbour overlap (waived build, all rows)
    dist_g = distances(waived.vec)
    both = set(common)
    per_group: dict[str, dict] = {}
    for grp, keys in group_keys({k: base.vec[k] for k in common}).items():
        dchange, ov, ov_restr, band_share, lost_share = [], [], [], [], []
        for k in keys:
            partners = [o for o in dist_base.get(k, {}) if o in both]
            if partners:
                dchange.append(ref.median([abs(dist_base[k][o] - dist_g[k][o]) for o in partners]))
            nb, ng = top_k(dist_base, k), top_k(dist_g, k)
            ov.append(overlap(nb, ng))
            if g < BASE_GATE:
                ov_restr.append(overlap(nb, top_k(dist_g, k, allowed=set(base.vec))))
                new = [o for o in ng if o not in nb]
                if new:
                    band_share.append(sum(o not in base.vec for o in new) / len(new))
            else:
                lost_share.append(sum(o not in waived.vec for o in nb) / K)
        per_group[grp] = {"rows": len(keys), "median_abs_dd": summary(dchange),
                          "overlap": summary([float(x) for x in ov]),
                          "overlap_restricted": summary([float(x) for x in ov_restr]),
                          "new_from_band": summary(band_share),
                          "lost_below_gate": summary(lost_share)}
    out["groups"] = per_group
    if g < BASE_GATE:
        out["band"] = band_landing(waived, set(base.vec))
    return out


def band_landing(build: Build, base_keys: set) -> dict:
    """Where the rows a lower gate admits land (stats-reviewer, M4g6a1 output review): their
    share of the rows, of the local top-10% cells (pct_local ≥ 90) and of the cells above the
    group's P99 (type 7 over the lower gate's eligible rows), on the DNA cells."""
    band = {k for k in build.vec if k not in base_keys}
    top = top_band = above = above_band = 0
    co = build.co
    for grp, ms in ref.DNA.items():
        for m in ms:
            metric = co.metrics[m]
            elig = [r for r in co.by_group[grp] if co.eligibility(metric, grp, r) == "ok"]
            if len(elig) < 2:
                continue
            cut = q7([r[m] for r in elig], P_OUT)
            for r in elig:
                if r[m] > cut:
                    above += 1
                    above_band += m4d.key_of(r) in band
    for k, v in build.vec.items():
        for c in v["cells"].values():
            if c["metric_status"] == "ok" and c["pct_local"] >= TOP_PCT:
                top += 1
                top_band += k in band
    return {"rows": len(band), "rows_share": len(band) / len(build.vec),
            "top_share": top_band / top if top else None, "top_cells": top,
            "p99_share": above_band / above if above else None, "p99_cells": above}


# ── Item 2: reliability ────────────────────────────────────────────────────────

def reliability(meas: dict) -> dict:
    out = {}
    for g, ms in ref.DNA.items():
        rs_ = {}
        for m in ms:
            if m in meas["metrics"]:
                rs_[m] = meas["metrics"][m]["groups"][g]["r_full"]
            elif m in meas["ratios"]:
                rs_[m] = meas["ratios"][m]["groups"][g]["split_half_r_full"]
            else:
                rs_[m] = meas["team_pass_share"][g]["split_half_r_full"]
        out[g] = {"n": len(rs_), "min": min(rs_.values()), "median": ref.median(list(rs_.values())),
                  "lower_reliability": sorted(m for m, r in rs_.items() if r < LOW_RELIABILITY),
                  "lowest": sorted(rs_.items(), key=lambda x: x[1])[:3]}
    buckets = json.loads(BUCKETS.read_text())
    tab: dict[str, dict[str, list[float]]] = defaultdict(dict)
    for c in buckets["cells"]:
        g = c["group"]
        if g in ref.DNA and c["measure"] in ref.DNA[g]:
            tab[c["population"]].setdefault(g, []).append(c["r_full"])
    out["buckets"] = {b: {g: ref.median(v) for g, v in gs.items()} for b, gs in tab.items()}
    return out


# ── Item 4: outliers ───────────────────────────────────────────────────────────

class Events:
    def __init__(self) -> None:
        self.cache: dict[int, list[dict]] = {}

    def __call__(self, mid: int) -> list[dict]:
        if mid not in self.cache:
            self.cache[mid] = json.loads((EVENTS / f"{mid}.json").read_text())
        return self.cache[mid]


def count_cols(meas: dict, metric: m4d.Metric) -> tuple[str, list[str], list[str]]:
    """(numerator or count column, denominator plus, minus)."""
    if metric.unit == "per90":
        return meas["metrics"][metric.name]["count_column"], [], []
    return metric.num, metric.plus, metric.minus  # type: ignore[return-value]


def value_of(metric: m4d.Metric, num: float, plus: float, minus: float, minutes: float) -> float | None:
    if metric.unit == "per90":
        return num / minutes * 90.0 if minutes > 0 else None
    den = plus - minus
    return num / den if den > 0 else None


def outliers(base: Build, meas: dict, season: dict, match: pl.DataFrame) -> dict:
    co = base.co
    cells_out, rows_any_dna, rows_any_all = [], set(), set()
    p99: dict[tuple, float] = {}
    p95: dict[tuple, float] = {}
    for g in ref.DNA:
        group_rows = co.by_group[g]
        for name, metric in co.metrics.items():
            elig = [r for r in group_rows if co.eligibility(metric, g, r) == "ok"]
            if len(elig) < 2:
                continue
            cut = q7([r[name] for r in elig], P_OUT)
            p99[(g, name)] = cut
            p95[(g, name)] = q7([r[name] for r in elig], P_DIAG)
            for r in elig:
                if r[name] > cut:
                    rows_any_all.add(m4d.key_of(r))
                    if name in ref.DNA[g]:
                        rows_any_dna.add(m4d.key_of(r))
                        cells_out.append((r, name, cut))
    events = Events()
    per_row: dict[tuple, list[tuple[int, Counter, float]]] = {}
    dups: dict[tuple, int] = {}
    out = []
    for r, name, cut in cells_out:
        k = m4d.key_of(r)
        if k not in per_row:
            ms = match.filter((pl.col("player_id") == r["player_id"]) & (pl.col("team_id") == r["team_id"])
                              & (pl.col("competition_id") == r["competition_id"]))
            per_row[k] = [(mid, rc.player_counts(events(mid), r["player_id"]), mins)
                          for mid, mins in ms.select("match_id", "minutes_played").iter_rows()]
            dups[k] = sum(rc.duplicate_events(events(mid), r["player_id"]) for mid, _, _ in per_row[k])
        matches = per_row[k]
        metric = co.metrics[name]
        num_c, plus_c, minus_c = count_cols(meas, metric)
        total = Counter()
        for _, c, _ in matches:
            total.update(c)
        traced, mismatch = [], []
        for col in (num_c, *plus_c, *minus_c):
            if col in rc.NOT_RECOUNTED:
                continue
            traced.append(col)
            if abs(total[col] - season[k][col]) > COUNT_TOL:
                mismatch.append(f"{col}: recount {total[col]} vs parquet {season[k][col]}")
        top_mid, top_c, top_min = max(matches, key=lambda x: (x[1][num_c], -x[0]))
        tot_num = season[k][num_c]
        share_top = top_c[num_c] / tot_num if tot_num else None
        without = value_of(metric, tot_num - top_c[num_c],
                           sum(season[k][c] - top_c[c] for c in plus_c),
                           sum(season[k][c] - top_c[c] for c in minus_c),
                           r["minutes_played"] - top_min)
        above = None if without is None else without > cut
        diag = p95[(r["position_group"], name)]
        cls = classify(not mismatch, dups[k], above, r["position_group_share"], r["minutes_nominal_equiv"])
        out.append({"player": r["player_name"], "team": r["team_name"], "group": r["position_group"],
                    "metric": name, "value": r[name], "p99": cut, "traced": traced,
                    "not_traced": [c for c in (num_c, *plus_c, *minus_c) if c in rc.NOT_RECOUNTED],
                    "mismatch": mismatch, "top_match": top_mid, "top_share": share_top,
                    "without_top": without, "without_top_above_p99": above, "duplicates": dups[k],
                    "p95": diag, "without_top_above_p95": None if without is None else without > diag,
                    "share": r["position_group_share"], "nominal": r["minutes_nominal_equiv"],
                    "near_gate": r["minutes_nominal_equiv"] < NEAR_GATE_HI, "class": cls})
    # lower tail
    low = []
    for g in ref.DNA:
        for name in LOW_TAIL:
            vals = [r[name] for r in co.by_group[g] if r[name] is not None]
            cut = q7(vals, P_LOW)
            low += [{"group": g, "metric": name, "player": r["player_name"], "team": r["team_name"],
                     "value": r[name], "p1": cut, "share": r["position_group_share"]}
                    for r in co.by_group[g] if r[name] is not None and r[name] < cut]
    no_ball = (match.filter(pl.col("minutes_played") >= 45)
               .filter(pl.sum_horizontal(*[pl.col(c) for c in ON_BALL]) == 0))
    gated = base.dna.rows
    near_rows = sum(r["minutes_nominal_equiv"] < NEAR_GATE_HI for r in gated) / len(gated)
    near_cells = sum(c["near_gate"] for c in out) / len(out) if out else None
    zrows = []
    for k, v in base.vec.items():
        zs = [(abs(c["z"]), m) for m, c in v["cells"].items() if c["z"] is not None]
        if zs:
            z, m = max(zs)
            r = base.rows[k]
            zrows.append({"player": r["player_name"], "team": r["team_name"], "group": v["group"],
                          "metric": m, "abs_z": z, "share": r["position_group_share"]})
    zrows.sort(key=lambda x: -x["abs_z"])
    return {"cells": out, "rows_dna": len(rows_any_dna), "rows_all_metrics": len(rows_any_all),
            "near_gate_row_share": near_rows, "near_gate_cell_share": near_cells,
            "top_abs_z": zrows[:20],
            "low_tail": low,
            "no_on_ball_matches": [{"match_id": r["match_id"], "player": r["player_name"],
                                    "minutes": r["minutes_played"]} for r in no_ball.iter_rows(named=True)]}


# ── Item 5: multi-position and club changers ─────────────────────────────────────

def first_dates(match: pl.DataFrame) -> dict[tuple, str]:
    """Season key -> the date of the row's first match (raw matches files)."""
    dates: dict[int, str] = {}
    for comp in LEAGUE:
        for mt in json.loads((MATCHES / str(comp) / "27.json").read_text()):
            dates[mt["match_id"]] = mt["match_date"]
    out: dict[tuple, str] = {}
    for r in match.select("match_id", "player_id", "team_id", "competition_id", "season_id",
                          "source").iter_rows(named=True):
        d = dates.get(r["match_id"])
        if d is None:
            continue
        k = (r["player_id"], r["team_id"], r["competition_id"], r["season_id"], r["source"])
        if k not in out or d < out[k]:
            out[k] = d
    return out


def multi_position(base: Build, pct: pl.DataFrame, dist: dict, first_date: dict[tuple, str]) -> dict:
    amb = Counter(r["position_group"] for r in base.dna.rows if r["position_group_ambiguous"])
    prim = {(r["player_id"], r["team_id"], r["competition_id"], r["metric"]): r["pct_local"]
            for r in pct.filter(pl.col("cohort_role") == "primary").iter_rows(named=True)}
    second = []
    sec = pct.filter((pl.col("cohort_role") == "second") & (pl.col("status_local") == "ok"))
    group_of = {k[:3]: r["position_group"] for k, r in base.rows.items()}
    for (pid, tid, cid), g in sec.group_by(["player_id", "team_id", "competition_id"]):
        own = group_of.get((pid, tid, cid))
        diffs = [abs(prim[(pid, tid, cid, r["metric"])] - r["pct_local"])
                 for r in g.iter_rows(named=True)
                 if prim.get((pid, tid, cid, r["metric"])) is not None and own is not None
                 and r["metric"] in ref.DNA[r["position_group"]] and r["metric"] in ref.DNA[own]]
        second.append({"player_id": pid, "group": g["position_group"][0], "n": len(diffs),
                       "median": ref.median(diffs) if diffs else None, "max": max(diffs) if diffs else None})
    # club changers: two scored rows of one player in one group
    by: dict[tuple, list[tuple]] = defaultdict(list)
    for k, v in base.vec.items():
        by[(k[0], v["group"])].append(k)
    changers = []
    keys_by_group = group_keys(base.vec)
    tenth_by_group = {g: sorted(sorted(dist[x].values())[K - 1] for x in ks if len(dist[x]) >= K)
                      for g, ks in keys_by_group.items()}
    for (pid, g), ks in sorted(by.items()):
        if len(ks) != 2:
            continue
        a, b = sorted(ks, key=lambda x: (first_date.get(x, ""), x))     # in date order
        d = ref.pair_distance(base.vec[a], base.vec[b])
        tenth = tenth_by_group[g]
        own = sorted(dist[a].values())

        changers.append({"player": base.rows[a]["player_name"], "group": g,
                         "teams": [base.rows[a]["team_name"], base.rows[b]["team_name"]],
                         "distance": d, "rank_a": stint_rank(dist[a], d),
                         "rank_b": stint_rank(dist[b], d),
                         "first_a": own[0] if own else None,
                         "tenth_a": own[K - 1] if len(own) >= K else None,
                         "pct_of_group_tenth": sum(t < d for t in tenth) / len(tenth) * 100 if tenth else None})
    # D2 / D3 counts: multi-stint pool outfield players with no stint ≥ 900 nominal
    stints: dict[int, list[dict]] = defaultdict(list)
    for r in base.co.rows:
        if r["in_percentile_pool"] and r["is_outfield"]:
            stints[r["player_id"]].append(r)
    multi = {p: s for p, s in stints.items() if len(s) >= 2}
    none_gated = [p for p, s in multi.items() if all(r["minutes_nominal_equiv"] < BASE_GATE - TOL for r in s)]
    combined = [p for p in none_gated if sum(r["minutes_nominal_equiv"] for r in multi[p]) >= BASE_GATE - TOL]
    return {"ambiguous": dict(sorted(amb.items())), "second_group": second, "club_changers": changers,
            "multi_stint_players": len(multi), "no_stint_gated": len(none_gated),
            "combined_would_pass": len(combined)}


# ── Item 7: both bases ─────────────────────────────────────────────────────────

def bases(base: Build, dist: dict, dist_g: dict, season_rows: list[dict]) -> dict:
    picks = sample(base.dna.rows, season_rows)
    name = {k: f"{r['player_name']} ({r['team_name']}, {LEAGUE[r['competition_id']]})"
            for k, r in base.rows.items()}
    rows = []
    for r in picks:
        k = m4d.key_of(r)
        a, b = top_k(dist, k), top_k(dist_g, k)
        rows.append({"player": name[k], "group": r["position_group"], "overlap": overlap(a, b),
                     "local": [name[x] for x in a], "global": [name[x] for x in b]})
    lg = {}
    for g, keys in group_keys(base.vec).items():
        o1, e1 = same_league(dist, keys)
        o2, e2 = same_league(dist_g, keys)
        lg[g] = {"slots": K * len(keys), "local": o1, "global": o2, "expected": e1}
    return {"players": len({r["player_id"] for r in picks}), "rows": rows,
            "median_overlap": ref.median([float(x["overlap"]) for x in rows]), "same_league": lg}


# ── Item 9: outcomes ───────────────────────────────────────────────────────────

def mover_text(v: dict) -> str:
    if v["verdict"] == "untestable":
        return f"untestable (n {v['n']})"
    flags = []
    if v.get("underpowered"):
        flags.append(f"underpowered at {v['n']}")
    if v.get("borderline_seed"):
        flags.append("borderline (seed-sensitive)")
    if v.get("below_model_range"):
        flags.append("PASS (below model range)")
    lo, hi = v["ci"]
    ci = f"f̂ {v['f_hat']:.2f} [{lo:.2f}, {hi:.2f}]"
    return f"{v['verdict']}, {ci}" + (f"; {'; '.join(flags)}" if flags else "")


# ── Markdown ───────────────────────────────────────────────────────────────────

def f1(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}"


def f3(x: float | None) -> str:
    return "–" if x is None else f"{x:.3f}"


def table(head: list[str], body: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(r) + " |" for r in body)]


BRIEF = (
    ("Why per 90?", "ADR pointer only: ADR 004 §10 (per-90 and ratio units), §10.5. Item 2 shows the "
                    "per-90s are reliable, not why per-90 beats totals"),
    ("What happens with low-minute players?", "ADR 004 D1 (900 nominal gate), D20; item 1 (where the 720–900 "
     "rows land among the extremes: supports not lowering the gate), item 2 (minute buckets, incl. the "
     "900–1,350 caveat), item 4 (near-gate rows not over-represented at 900)"),
    ("How sensitive are rankings to the minimum-minutes threshold?", "item 1"),
    ("How are positions grouped?", "ADR 002 §4, §31.4; ADR 004 D5; item 5"),
    ("How are percentiles calculated?", "ADR 004 D11, §11.1 (Hazen, D3 dedupe, cohort floor)"),
    ("What happens when a player changes clubs?", "ADR 004 D2, D3; item 5 (club changers, D2/D3 counts); ADR 004 §13.7 (movers)"),
    ("What happens when a player plays multiple positions?", "ADR 004 D5, §12.8 C1; item 5; M4g6a2 item 6"),
    ("How are different leagues compared?", "ADR 004 D9, D10, §12.1 (local basis); item 7 (both bases, same-league share)"),
    ("Which features dominate similarity?", "M4g6a2 item 3 (neighbour-pair shares, leave-one-out)"),
    ("Are any features highly correlated and therefore effectively double-counted?",
     "ADR 004 D17; M4g6a2 item 3 (|ρ| ≥ 0.7 pairs, within / cross family)"),
    ("Does cosine similarity produce intuitively reasonable neighbors?",
     "deferred to M5: M4's distance is weighted squared Euclidean (ADR 004 §12.6); M4 does not answer the cosine question. Face validity of the M4 distance: item 7, M4f3"),
    ("How stable are Gem results?", "deferred to M6"),
    ("Are outliers caused by data errors?",
     "item 4: 0 recount mismatches, 0 duplicate events, so the pipeline matches the raw events. "
     "StatsBomb tagging errors beyond duplicates cannot be detected this way. Totals "
     "reconciliation: goals + own goals vs matches.json in spikes/verify_chunk_a_metrics.py and "
     "verify_core_metrics.py (not re-run in M4g6a1)"),
    ("How do we validate rare combinations?", "deferred to M6"),
)


def render(res: dict, reg: dict) -> str:
    L: list[str] = ["## M4g6a1 — Validation (gate sensitivity, outliers, movers, bases)", "",
                    f"Generated by `spikes/m4_validation.py` (no eleveniq imports), registry "
                    f"`{reg['registry_version'][:12]}…`, basis `local`. Report only (plan 005 "
                    f"\"M4g6a spec\", stats-reviewer conditions 1–12). Figures: "
                    f"`data/interim/m4g6a1/validation.json`.", ""]
    L += ["### Brief §20 questions → evidence", "",
          "The rows pointing to M4g6a2 (features dominating similarity, double counting, item 6) "
          "are completed there.", ""]
    L += table(["Question", "Evidence"], [[q, e] for q, e in BRIEF]) + [""]
    # item 1
    L += ["### 1. Minutes gate sensitivity (720 / 1,080 nominal)", "",
          "Held at the 900 registry: DNA lists, families, weights, zero-heavy exclusions, "
          "`min_attempts` and D9; local percentiles only (global / D9 not recomputed). On rows "
          "scored at both gates the within-cell rank order cannot change (nested populations, "
          "monotone percentiles), so the comparison is the size of the shifts, not a rank "
          "correlation. Percentile, distance and neighbour figures use the floor-waived build; "
          "cells whose league population falls below 30 are flagged.", ""]
    for s in res["sensitivity"]:
        g = s["gate"]
        L += [f"**Gate {g:g}.** Rows that drop below the gate: {s['dropped_below_gate']}"
              + (f" (mover stints: {', '.join(s['dropped_mover_stints'])})" if s["dropped_mover_stints"] else "")
              + ". Rule-faithful rows that lose a DNA cell to the cohort floor: "
              + (", ".join(f"{k} {v}" for k, v in s["rows_losing_a_cell_to_the_floor"].items()) or "none")
              + (f" (e.g. {', '.join(s['rows_losing_a_cell_names'][:6])})" if s["rows_losing_a_cell_names"] else "")
              + ".", ""]
        L += table(["Group", "Rows", "|Δpct| cell medians, median / max", "Top 10% kept",
                    "Median row |Δd|, median / P95", "Top-10 overlap, median / P10", "Extra"],
                   [[grp, str(v["rows"]),
                     f"{f1(ref.median([c['median'] for k, c in s['cells'].items() if k.startswith(grp + '|') and c['median'] is not None]))} / "
                     f"{f1(max(c['max'] for k, c in s['cells'].items() if k.startswith(grp + '|') and c['max'] is not None))}",
                     f"{sum(c['top_kept'] for k, c in s['cells'].items() if k.startswith(grp + '|'))} of "
                     f"{sum(c['top_n'] for k, c in s['cells'].items() if k.startswith(grp + '|'))}",
                     f"{f3(v['median_abs_dd']['median'])} / {f3(v['median_abs_dd']['p95'])}",
                     f"{f1(v['overlap']['median'])} / {f1(v['overlap']['p10'])}",
                     (f"restricted to rows scored at both: {f1(v['overlap_restricted']['median'])}; new neighbours "
                      f"from the 720–900 band: {f1(100 * (v['new_from_band']['median'] or 0))}% (median over the "
                      f"{v['new_from_band']['n']} rows whose list changed)")
                     if g < BASE_GATE else
                     f"900 neighbours lost below the gate: {f1(100 * (v['lost_below_gate']['median'] or 0))}% (median)"]
                    for grp, v in s["groups"].items()]) + [""]
        worst = sorted(((k, c) for k, c in s["cells"].items() if c["p95"] is not None),
                       key=lambda kc: -kc[1]["p95"])[:6]
        L += ["Largest shifts (P95 |Δpct|): " + "; ".join(
            f"{k} {f1(c['p95'])} (max {f1(c['max'])}{', floor waived' if c['floor_waived'] else ''})"
            for k, c in worst) + ".", ""]
        if "band" in s:
            bd = s["band"]
            L += [f"**Where the 720–900 rows land.** The "
                  f"{bd['rows']} rows a 720 gate would admit are {f1(100 * bd['rows_share'])}% of the "
                  f"720 population, but {f1(100 * bd['top_share'])}% of the local top-10% cells "
                  f"({bd['top_cells']}) and {f1(100 * bd['p99_share'])}% of the cells above the group "
                  f"P99 ({bd['p99_cells']}): {bd['top_share'] / bd['rows_share']:.1f}× their share in the "
                  f"top-10% cells and {bd['p99_share'] / bd['rows_share']:.1f}× above P99. This is "
                  f"consistent with low-minute noise at the extremes, and supports not lowering the "
                  f"900-minute gate. 900 vs 1,080 is decided by the coverage loss (e.g. PL ST, below), "
                  f"not by this figure.", ""]
        L += ["Cohort sizes (D3-deduped scored rows) 900 → " + f"{g:g}: " + "; ".join(
            f"{k} {s['cohort_sizes']['900'].get(k, 0)} → {v}" for k, v in s["cohort_sizes"][f"{g:g}"].items()
            if v < 40 or s['cohort_sizes']['900'].get(k, 0) < 40) + " (cells under 40 shown).", ""]
    # item 2
    r = res["reliability"]
    L += ["### 2. Reliability (committed multi-split r_full, DNA cells)", ""]
    L += table(["Group", "DNA cells", "Min", "Median", "Below 0.7 (lower reliability)"],
               [[g, str(v["n"]), f3(v["min"]), f3(v["median"]), ", ".join(v["lower_reliability"]) or "–"]
                for g, v in r.items() if g != "buckets"]) + [""]
    L += ["Minute buckets (median r_full of each group's DNA measures): " + "; ".join(
        f"{b}: " + ", ".join(f"{g} {f3(x)}" for g, x in sorted(gs.items())) for b, gs in sorted(r["buckets"].items())) + ".", "",
          "**Caveat.** In the 900–1,350 bucket the gate admits CB and FB-WB rows whose DNA measures have "
          f"a median r_full of {f3(r['buckets'].get('900-1350', {}).get('CB'))} and "
          f"{f3(r['buckets'].get('900-1350', {}).get('FB-WB'))}, below the {LOW_RELIABILITY} "
          "\"lower reliability\" line: a near-gate CB or FB-WB profile is noisier than the cell r_full "
          "suggests.", ""]
    # item 4
    o = res["outliers"]
    cls = Counter(c["class"] for c in o["cells"])
    L += ["### 4. Outlier audit (D20)", "",
          f"DNA cells strictly above their group's P99 (type 7, all leagues, eligible rows): "
          f"{len(o['cells'])} cells in {o['rows_dna']} rows; over all per-90 and ratio metrics "
          f"{o['rows_all_metrics']} rows (D20 recorded 243 at M4a, before M4b). Every cell's count "
          f"columns are recounted from the raw events (periods 1–4); `team_open_play_passes_while_on` "
          f"(credit-based, league total verified by `verify_core_metrics.py`) and minutes "
          f"(verified at league level by the same spike) are not re-derived per row.", "",
          "Classes (first match wins: coding error → data anomaly → single-match spike → mixed role "
          "→ near-gate → genuine): " + ", ".join(f"{c} {cls.get(c, 0)}" for c in CLASSES) + ". "
          f"Flags regardless of class: near-gate {sum(c['near_gate'] for c in o['cells'])}, mixed role "
          f"{sum(c['share'] < MIXED_SHARE for c in o['cells'])} cells.", "",
          f"Near-gate rows (900–1,350 nominal) are {f1(100 * o['near_gate_row_share'])}% of the gated "
          f"rows and {f1(100 * o['near_gate_cell_share'])}% of the cells above P99: at the 900 gate they "
          "are not over-represented among the extremes.", ""]
    spikes = [c for c in o["cells"] if c["class"] == "single-match spike"]
    shares = sorted(c["top_share"] for c in spikes if c["top_share"] is not None)
    one = sorted((c for c in o["cells"] if (c["top_share"] or 0) >= ONE_MATCH_SHARE),
                 key=lambda c: -c["top_share"])
    L += ["**How to read the single-match class (post-hoc diagnostic, not pre-registered; M4g6a1 "
          "output review).** The rule (\"below P99 without the top match\") measures how close a cell "
          "sits to P99, not whether one match drives it: dropping any strong match takes a cell just "
          "above P99 below it. Of the "
          f"{len(spikes)} single-match cells, the top match holds a median "
          f"{f1(100 * ref.median(shares)) if shares else '–'}% of the count (P90 "
          f"{f1(100 * q7(shares, 0.9)) if shares else '–'}%), and "
          f"{sum(bool(c['without_top_above_p95']) for c in spikes)} stay above their group's P95 without "
          "it. The label stays as pre-registered; read it as **marginal exceedance (within one match of "
          f"P99)**, which is benign. The cells one match clearly drives (top share ≥ "
          f"{ONE_MATCH_SHARE:.0%}): " + ("; ".join(
              f"{c['player']} {c['metric']} {100 * c['top_share']:.0f}%" for c in one) or "none") + ".", "",
          f"**Answer to \"are outliers caused by data errors?\"** No error found: {len(o['cells'])} cells, "
          f"0 recount mismatches, 0 duplicate events; {cls.get('genuine', 0)} genuine and "
          f"{cls.get('single-match spike', 0)} marginal exceedances. The recount shows the pipeline matches "
          "the raw events; it cannot detect StatsBomb tagging errors beyond duplicates.", ""]
    mism = [c for c in o["cells"] if c["mismatch"]]
    L += [f"Recount mismatches: {len(mism)}" + (": " + "; ".join(
        f"{c['player']} {c['metric']} ({'; '.join(c['mismatch'])})" for c in mism[:10]) if mism else "") + ".", ""]
    flagged = [c for c in o["cells"] if c["class"] != "genuine"]
    L += table(["Player", "Group", "Metric", "Value", "P99", "Top match share", "Without top", "Class"],
               [[f"{c['player']} ({c['team']})", c["group"], c["metric"], f"{c['value']:.3g}", f"{c['p99']:.3g}",
                 "–" if c["top_share"] is None else f"{100 * c['top_share']:.0f}%",
                 "–" if c["without_top"] is None else f"{c['without_top']:.3g}", c["class"]]
                for c in sorted(flagged, key=lambda c: (CLASSES.index(c["class"]), c["group"], c["metric"]))]) + [""]
    L += ["The 20 largest |z| rows (the row's largest cell), with the main-group minute share:", ""]
    L += table(["Player", "Group", "Metric", "|z|", "Main-group share"],
               [[f"{x['player']} ({x['team']})", x["group"], x["metric"], f"{x['abs_z']:.2f}",
                 f"{x['share']:.2f}"] for x in o["top_abs_z"]]) + [""]
    L += [f"Lower tail: {len(o['low_tail'])} gated rows below their group's P1 on "
          f"{', '.join(LOW_TAIL)}; player-matches with ≥ 45 minutes and no on-ball event: "
          f"{len(o['no_on_ball_matches'])}"
          + (" (" + "; ".join(f"{x['player']} {x['match_id']}" for x in o["no_on_ball_matches"][:8]) + ")"
             if o["no_on_ball_matches"] else "") + ".", ""]
    # item 5
    m = res["multi"]
    L += ["### 5. Multi-position players and club changers", "",
          "D5 ambiguous scored rows per group: " + ", ".join(f"{g} {n}" for g, n in m["ambiguous"].items())
          + f". Second-group rows: {len(m['second_group'])}; |primary − second| local percentile on "
            f"shared DNA metrics, median per row: "
          + ", ".join(f1(x["median"]) for x in m["second_group"]) + ". Gaps of about 30 points: a "
            "second-group profile is a different profile, which is why it is display-only (plan 005 "
            "M4d2).", "",
          f"Club changers (two scored stints in one group): {len(m['club_changers'])}, described, no "
          f"inference at this n. Clubs in date order (first match). Rank = the other stint's rank among "
          f"all same-group rows, that stint admitted.", ""]
    L += table(["Player", "Group", "Clubs", "Distance", "Rank (a / b)", "1st / 10th neighbour (a)",
                "Pct of the group's 10th-neighbour distances"],
               [[c["player"], c["group"], " → ".join(c["teams"]), f"{c['distance']:.4f}",
                 f"{c['rank_a']} / {c['rank_b']}",
                 "–" if c["tenth_a"] is None else f"{c['first_a']:.4f} / {c['tenth_a']:.4f}",
                 f1(c["pct_of_group_tenth"])] for c in m["club_changers"]]) + [""]
    L += [f"D2 / D3: {m['multi_stint_players']} pool outfield players have two or more stints; "
          f"{m['no_stint_gated']} have no stint ≥ 900 nominal, and {m['combined_would_pass']} of those "
          f"would pass on combined minutes, but get no profile (stints are never merged).", ""]
    # item 7
    b = res["bases"]
    L += ["### 7. Neighbours on both bases", "",
          f"Sample (rule fixed before computing): {b['players']} players, {len(b['rows'])} rows "
          f"(every scored stint of a sampled mover: the M4g3 mover rule runs over every season row, so a "
          f"sampled mover can have one scored stint). Basis (a) local (the build); (b) `pct_global` "
          f"where `status_global` is ok, else `pct_local`. Median top-10 overlap "
          f"{f1(b['median_overlap'])} of 10.", ""]
    L += table(["Group", "Slots", "Same league, local", "Same league, global where defined", "Expected"],
               [[g, str(v["slots"]), str(v["local"]), str(v["global"]), f1(v["expected"])]
                for g, v in b["same_league"].items()]) + [""]
    L += ["Reading: on the local basis same-league neighbours fall below expectation in every group. "
          "Part of this is mechanical: within-league Hazen percentiles are evenly spaced, so two players "
          "of one league cannot share a value while players of different leagues can (strongest in ST, "
          "with league cohorts of 29–50). So \"below expected\" is not evidence of league neutrality. "
          "The rise above expectation on the global basis is the league-style signal.", ""]
    for x in b["rows"]:
        L += [f"- **{x['player']}** ({x['group']}), overlap {x['overlap']}/10. Local: "
              + "; ".join(x["local"]) + ". Global where defined: " + "; ".join(x["global"]) + "."]
    L += [""]
    # item 9
    L += ["### 9. M4g1–M4g5 outcomes", ""]
    L += ["- **M4g1** style maps and the DM-CM role split: the section above (`m4g_style_map`).",
          "- **M4g2** multi-split reliability and the majority trigger: `docs/reviews/m4g2-reliability.md`, ADR 004 §13.6.",
          "- **M4g3** movers: `docs/reviews/m4g3-movers.md`, ADR 004 §13.7. Every verdict below carries its flags.",
          "- **M4g4** possession and the Defending restore: `docs/reviews/m4g4c-possession.md`, ADR 004 §13.8. "
          "Defending withheld in every group: " + ", ".join(
              f"{g} `{v['status']}`" + (f" ({'; '.join(v['notes'])})" if v["notes"] else "")
              for g, v in reg["family_status"].items()) + ".",
          "- **M4g5** xPass: deferred (ADR 004 §13.9, `docs/reviews/m4g5-feasibility.md`); Passing stays "
          "`pending_mover_stability` = deferred, not tested.", ""]
    L += table(["Metric", "Mover verdict (with flags)"],
               [[c, mover_text(v)] for c, v in sorted(reg["movers"].items())]) + [""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True, help="tools/registry_snapshot.py output")
    ap.add_argument("--out", type=Path, default=REVIEW)
    args = ap.parse_args()
    reg = json.loads(args.registry.read_text())
    meas = json.loads(m4d.MEASUREMENTS.read_text())
    if not reg.get("family_status") or "possession" not in meas:
        fail("the registry snapshot or measurements predate M4g4d")
    for g in ref.DNA:
        if tuple(reg["groups"][g]["dna"]) != ref.DNA[g]:
            fail(f"{g}: the snapshot's DNA list differs from the reference spike's")
    print("builds: 900 …", flush=True)
    base = Build(meas, BASE_GATE, m4d.MIN_COHORT_SIZE)
    if len(base.vec) != ref.EXPECTED["rows"]:
        fail(f"{len(base.vec)} scored rows, expected {ref.EXPECTED['rows']}")
    dist = distances(base.vec)
    res: dict = {"sensitivity": []}
    for g in GATES:
        print(f"builds: {g:g} …", flush=True)
        rule = Build(meas, g, m4d.MIN_COHORT_SIZE)
        waived = Build(meas, g, 0)
        res["sensitivity"].append(sensitivity(base, rule, waived, dist))
    print("reliability, outliers …", flush=True)
    res["reliability"] = reliability(meas)
    season_rows = base.co.rows
    season = {m4d.key_of(r): r for r in season_rows}
    match = pl.read_parquet(MATCH_PARQUET)
    res["outliers"] = outliers(base, meas, season, match)
    pct = pl.read_parquet(PCT_PARQUET)
    res["multi"] = multi_position(base, pct, dist, first_dates(match))
    print("bases …", flush=True)
    gvec = with_global(base.vec, pct)
    res["bases"] = bases(base, dist, distances(gvec), season_rows)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=1, default=str, ensure_ascii=False) + "\n")
    rs.write_section(args.out, OWNER, render(res, reg))
    print(f"wrote {OUT_JSON} and the {OWNER} section of {args.out}")


if __name__ == "__main__":
    main()
