"""
Independent reference for the M4g2 multi-split reliability and the expected registry flips
(plan 005 M4g2, ADR 004 §13.1).

It shares the written spec (plan 005 M4g2, "Split assignment"), not code, with
spikes/m4g2_multisplit.py and spikes/m4_decision_measurements.py:
- its own stint table, gate and match order from the parquets and matches.json;
- its own draw loop;
- half sums by reduceat along the split axis, and r vectorised over splits within a cell
  (the engine vectorises over metrics within a split);
- judged cells read from the registry snapshot (reasons fixed_display / d17_dropped /
  d12b_not_preregistered), not from the spike's written-out lists.

Modes:
  --check-output MEAS.json   compare every stored multi-split field (gated per-90s, ratios,
                             team_pass_share) and every cell of the report file
                             data/processed/reports/m4g2_reliability_cells.json; verify the K
                             rule on prefixes of the same stream; reproduce each stored *_odd_even
                             with an odd/even assignment; check defensive_duel_success_pct (M,
                             min_attempts, coverage, its terms' totals and the stored season
                             column). Fails on any mismatch beyond TOL.
  --write-expected           also write spikes/expected_m4g2_flips.json: the registry rules of
                             ADR 004 §10.3 plus the majority trigger, applied here to the new
                             measurements and the registry snapshot, BEFORE the registry code
                             changes. Self-check first: the same rules on the old measurements
                             must reproduce every snapshot status.

No eleveniq imports (tests/test_spikes_independent.py).

Usage:
  .venv/bin/python spikes/m4g2_reliability_reference.py \
      --check-output data/interim/m4g2_snapshot/measurements_new.json \
      --snapshot data/interim/m4g2_snapshot [--write-expected]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "statsbomb"
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = ROOT / "data" / "interim" / "player_match_stats.parquet"
CELLS_REPORT = ROOT / "data" / "processed" / "reports" / "m4g2_reliability_cells.json"
EXPECTED = Path(__file__).resolve().parent / "expected_m4g2_flips.json"

KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
SEASON = 27
LEAGUES = (11, 2, 12, 7, 9)
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
ALL = "ALL"
GATE, GATE_TOL = 900.0, 1e-6
BUCKETS = {"900-1350": (None, 1350.0), "1350-2000": (1350.0, 2000.0), ">=2000": (2000.0, None)}
SEED, K_START, K_CAP, SE_TARGET = 20261007, 200, 3200, 0.002
CHUNK = 100
TOL = 2e-9                      # the JSON rounds to 9 decimals

# ADR 004 §10.3 rules and constants, written out (config.py; tests keep the registry's equal).
DNA_MIN, LOW_BELOW, ZERO_HEAVY, RATIO_COVERAGE = 0.6, 0.7, 0.30, 0.90
RHO_TRIGGER, SIGN_NEUTRAL, ALLOW_MAX, TPS_MAX_RHO, COLLINEAR = 0.4, 0.05, 0.55, 0.3, 0.85
MAJORITY_LEAGUES, BOUNDARY = 3, 0.02
PROXIES = ("poss_dur_loo", "poss_pass_loo")
ALLOW = frozenset({*((c, g) for c in ("progressive_passes_p90", "progressive_carries_p90") for g in GROUPS),
                   *(("long_pass_completion_pct", g) for g in ("CB", "FB-WB", "DM-CM"))})
NOT_JUDGED = ("fixed_display", "d17_dropped", "d12b_not_preregistered")
NEW_RATIO = ("defensive_duel_success_pct", "tackles_won", ("tackles_attempted", "dribbled_past"))
RATIO_MIN_N = 10


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


# ── Data ───────────────────────────────────────────────────────────────────────

def load() -> tuple[pl.DataFrame, pl.DataFrame]:
    """(stints, played rows), rows contiguous per stint in (match_date, match_id) order."""
    season = pl.read_parquet(SEASON_PARQUET)
    match = pl.read_parquet(MATCH_PARQUET)
    dates = {}
    for comp in LEAGUES:
        for m in json.loads((RAW / "matches" / str(comp) / f"{SEASON}.json").read_text()):
            dates[m["match_id"]] = f"{m['match_date']} {m.get('kick_off') or ''}"
    match = match.with_columns(pl.col("match_id").replace_strict(dates, return_dtype=pl.Utf8).alias("match_date"))
    nominal = match.group_by(KEY).agg((pl.col("minutes_played") * 90.0 / pl.col("match_duration")).sum().alias("nom"))
    stints = (season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
              .join(nominal, on=KEY, how="left").sort(KEY))
    stints = stints.with_columns((pl.col("nom") >= GATE - GATE_TOL).alias("gated"))
    if stints.filter(pl.col("gated") != pl.col("meets_min_minutes")).height:
        fail("recomputed gate differs from meets_min_minutes")
    rows = (match.filter(pl.col("minutes_played") > 0)
            .join(stints.select(KEY), on=KEY, how="semi").sort([*KEY, "match_date", "match_id"]))
    n = rows.group_by(KEY, maintain_order=True).len("n")
    stints = stints.join(n, on=KEY, how="left")
    if stints["n"].null_count():
        fail("a pool outfield stint has no played match")
    return stints, rows


def measures(meas: dict) -> list[tuple[str, str, str, tuple[str, ...], tuple[str, ...]]]:
    """(name, kind, numerator, plus, minus) for every stored quantity, from the JSON's own terms."""
    out = [(m, "per90", v["count_column"], ("minutes_played",), ()) for m, v in sorted(meas["metrics"].items())]
    out += [(r, "ratio", v["numerator"], tuple(v["denominator_plus"]), tuple(v["denominator_minus"]))
            for r, v in sorted(meas["ratios"].items())]
    out.append(("team_pass_share", "share", "open_play_passes_attempted", ("team_open_play_passes_while_on",), ()))
    return out


# ── Splits ─────────────────────────────────────────────────────────────────────

def labels(n: list[int], k_from: int, k_to: int, rng: np.random.Generator) -> np.ndarray:
    """(k_to − k_from) × rows, True = half 0; the generator continues the one stream."""
    total = sum(n)
    L = np.zeros((k_to - k_from, total), dtype=bool)
    for k in range(k_to - k_from):
        off = 0
        for ni in n:
            perm = rng.permutation(ni)
            extra = int(rng.integers(0, 2)) if ni % 2 == 1 else 0
            L[k, off + perm[: ni // 2 + extra]] = True
            off += ni
    return L


def odd_even_labels(n: list[int]) -> np.ndarray:
    """The earlier method: by date, 1st match → half 1, 2nd → half 0, … (cum_count % 2)."""
    return np.concatenate([np.arange(ni) % 2 == 1 for ni in n])[None, :]


def r_by_split(A: np.ndarray, B: np.ndarray, key: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A, B: splits × rows (NaN undefined). Demean within key per split, pooled Pearson per split."""
    ok = ~(np.isnan(A) | np.isnan(B))
    a = np.where(ok, A, 0.0)
    b = np.where(ok, B, 0.0)
    for kv in np.unique(key):
        cols = key == kv
        c = ok[:, cols].sum(axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            a[:, cols] = np.where(ok[:, cols], a[:, cols] - np.where(c > 0, a[:, cols].sum(1, keepdims=True) / c, 0), 0)
            b[:, cols] = np.where(ok[:, cols], b[:, cols] - np.where(c > 0, b[:, cols].sum(1, keepdims=True) / c, 0), 0)
    nn = ok.sum(axis=1)
    sab, saa, sbb = (a * b).sum(1), (a * a).sum(1), (b * b).sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where((nn >= 3) & (saa > 0) & (sbb > 0), sab / np.sqrt(saa * sbb), np.nan)
    return r, nn, ok


def sb(r: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(r > -1, 2 * r / (1 + r), np.nan)


class Splitter:
    """All cells for a block of splits given by its label matrix."""

    def __init__(self, stints: pl.DataFrame, rows: pl.DataFrame, ms: list) -> None:
        self.ms = ms
        self.n = stints["n"].to_list()
        self.starts = np.concatenate([[0], np.cumsum(self.n)[:-1]]).astype(np.int64)
        cols = sorted({c for _, _, num, plus, minus in ms for c in (num, *plus, *minus)})
        self.vals = {c: rows[c].cast(pl.Float64).to_numpy() for c in cols}
        grp = stints["position_group"].to_numpy().astype(str)
        gcode = {g: i for i, g in enumerate(sorted(set(grp.tolist())))}
        self.key = stints["competition_id"].to_numpy().astype(np.int64) * 100 + np.array([gcode[g] for g in grp])
        nom, gated = stints["nom"].to_numpy(), stints["gated"].to_numpy()
        pops = {"gate": gated}
        for b, (lo, hi) in BUCKETS.items():
            pops[b] = gated & ((nom >= lo) if lo is not None else True) & ((nom < hi) if hi is not None else True)
        self.cells = {(p, g): np.flatnonzero(mask & ((grp == g) if g != ALL else True))
                      for p, mask in pops.items() for g in (*GROUPS, ALL)}

    def block(self, L: np.ndarray) -> dict:
        """(pop, group, measure) → (r_k, r_full_k, n_k, present_all, present_any) for these splits."""
        sums: dict[tuple[str, int], np.ndarray] = {}
        cnt0 = np.add.reduceat(L.astype(np.int64), self.starts, axis=1)
        cnt = (cnt0, np.array(self.n)[None, :] - cnt0)

        def s(col: str, h: int) -> np.ndarray:
            if (col, h) not in sums:
                w = L if h == 0 else ~L
                sums[(col, h)] = np.add.reduceat(np.where(w, self.vals[col][None, :], 0.0), self.starts, axis=1)
            return sums[(col, h)]

        out = {}
        for name, kind, num, plus, minus in self.ms:
            halves = []
            for h in (0, 1):
                den = sum(s(c, h) for c in plus) - (sum(s(c, h) for c in minus) if minus else 0)
                with np.errstate(invalid="ignore", divide="ignore"):
                    if kind == "per90":
                        halves.append(np.where(cnt[h] > 0, s(num, h) / den * 90.0, np.nan))
                    else:
                        halves.append(np.where(den > 0, s(num, h) / den, np.nan))
            for (p, g), idx in self.cells.items():
                r, nn, ok = r_by_split(halves[0][:, idx], halves[1][:, idx], self.key[idx])
                out[(p, g, name)] = (r, sb(r), nn, ok.all(axis=0), ok.any(axis=0))
        return out


def summarise(rk: np.ndarray, rf: np.ndarray, nn: np.ndarray, p_all: np.ndarray, p_any: np.ndarray) -> dict:
    und = np.isnan(rf)
    if und.all():
        mean = sd = se = rmean = None
    else:
        d = rf[~und]
        mean, sd = float(d.mean()), (float(d.std(ddof=1)) if len(d) > 1 else 0.0)
        se, rmean = sd / math.sqrt(len(d)), float(rk[~und].mean())
    return {"r_full": mean, "sd": sd, "mc_se": se, "r_k_mean": rmean,
            "n_undefined": int(und.sum()), "n_min": int(nn.min()), "n_median": float(np.median(nn)),
            "n_max": int(nn.max()), "share_present_every_split": (int(p_all.sum()) / int(p_any.sum()))
            if p_any.sum() else None}


def run_splits(sp: Splitter, k: int) -> dict:
    rng = np.random.default_rng(SEED)
    acc: dict = {}
    for k0 in range(0, k, CHUNK):
        part = sp.block(labels(sp.n, k0, min(k0 + CHUNK, k), rng))
        for cell, (rk, rf, nn, pa, py) in part.items():
            if cell not in acc:
                acc[cell] = [[rk], [rf], [nn], pa.copy(), py.copy()]
            else:
                a = acc[cell]
                a[0].append(rk); a[1].append(rf); a[2].append(nn); a[3] &= pa; a[4] |= py
        print(f"  splits {min(k0 + CHUNK, k)}/{k}", flush=True)
    return {c: (np.concatenate(a[0]), np.concatenate(a[1]), np.concatenate(a[2]), a[3], a[4]) for c, a in acc.items()}


# ── Checks ─────────────────────────────────────────────────────────────────────

def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= TOL


def judged_cells(snapshot: dict) -> set[tuple[str, str]]:
    out = {(c["column"], c["group"]) for c in snapshot["cells"] if not any(r in NOT_JUDGED for r in c["reasons"])}
    return out | {(NEW_RATIO[0], g) for g in GROUPS}


def check(meas: dict, snapshot: dict, stints: pl.DataFrame, rows: pl.DataFrame) -> dict:
    ms = measures(meas)
    sp = Splitter(stints, rows, ms)
    k = meas["reliability"]["k"]
    print(f"reference splits: K = {k}")
    res = run_splits(sp, k)
    errors: list[str] = []

    # K rule on prefixes of the same stream.
    judged = judged_cells(snapshot)
    k_rule, capped = None, False
    kk = K_START
    while True:
        worst = 0.0
        for (col, g) in judged:
            rf = res[("gate", g, col)][1][:kk]
            if np.isnan(rf).all():
                continue
            worst = max(worst, float(np.nanstd(rf, ddof=1)) / math.sqrt(len(rf)))
        if worst <= SE_TARGET:
            k_rule = kk
            break
        if kk >= K_CAP:
            k_rule, capped = kk, True
            break
        kk = min(kk * 2, K_CAP)
    if k_rule != k or capped != meas["reliability"]["reached_cap"]:
        errors.append(f"K rule: reference K {k_rule} (cap {capped}) vs stored {k} "
                      f"(cap {meas['reliability']['reached_cap']})")

    summ = {c: summarise(*v) for c, v in res.items()}
    fields = ("r_full", "sd", "mc_se", "r_k_mean", "n_undefined", "n_min", "n_median", "n_max",
              "share_present_every_split")
    checked = 0

    def cmp(where: str, stored: dict, prefix: str, ref: dict) -> None:
        nonlocal checked
        for f in fields:
            key = prefix if f == "r_full" else f"{prefix}_{'r_k_mean' if f == 'r_k_mean' else f}"
            if not close(stored.get(key), ref[f]):
                errors.append(f"{where} {key}: stored {stored.get(key)} vs reference {ref[f]}")
            checked += 1

    for m, v in meas["metrics"].items():
        for g, c in v["groups"].items():
            cmp(f"metrics {m} × {g}", c, "r_full", summ[("gate", g, m)])
    for r, v in meas["ratios"].items():
        for g, c in v["groups"].items():
            cmp(f"ratios {r} × {g}", c, "split_half_r_full", summ[("gate", g, r)])
    for g, c in meas["team_pass_share"].items():
        cmp(f"team_pass_share × {g}", c, "split_half_r_full", summ[("gate", g, "team_pass_share")])
    report = json.loads(CELLS_REPORT.read_text())
    if report["k"] != k or report["seed"] != SEED:
        errors.append("cells report: K or seed differ from the measurements")
    seen = set()
    for c in report["cells"]:
        cell = (c["population"], c["group"], c["measure"])
        seen.add(cell)
        ref = summ.get(cell)
        if ref is None:
            errors.append(f"cells report: {cell} not computed by the reference")
            continue
        for f in fields:
            if not close(c.get(f), ref[f]):
                errors.append(f"cells report {cell} {f}: {c.get(f)} vs {ref[f]}")
            checked += 1
    if seen != set(summ):
        errors.append(f"cells report misses {len(set(summ) - seen)} reference cells")

    # Odd/even: the stored comparator reproduced with the earlier assignment (ties to history).
    oe = sp.block(odd_even_labels(sp.n))
    for m, v in meas["metrics"].items():
        for g, c in v["groups"].items():
            ref = float(oe[("gate", g, m)][1][0])
            ref = None if math.isnan(ref) else ref
            if not close(c["r_full_odd_even"], ref):
                errors.append(f"odd/even {m} × {g}: stored {c['r_full_odd_even']} vs {ref}")
            checked += 1

    # defensive_duel_success_pct: terms' totals, the season column, and M / min_attempts / coverage.
    name, num, plus = NEW_RATIO
    season = stints
    den = sum(pl.col(c) for c in plus)
    for c in (num, *plus):
        tot_m, tot_s = rows[c].sum(), season[c].sum()
        print(f"  total {c:18s} match rows {tot_m}  season rows {tot_s}")
        if tot_m != tot_s:
            errors.append(f"{c}: match total {tot_m} ≠ season total {tot_s}")
    recomputed = season.select(pl.when(den > 0).then(pl.col(num) / den).otherwise(None).alias("v"))["v"]
    stored = season[name]
    if not ((recomputed.is_null() == stored.is_null()).all()
            and ((recomputed - stored).abs().max() or 0.0) <= 1e-12):
        errors.append(f"{name}: stored season column ≠ {num} / ({' + '.join(plus)})")
    print(f"  {name}: {stored.is_not_null().sum()} non-null pool outfield rows of {season.height}")
    for g in GROUPS:
        sub = season.filter((pl.col("position_group") == g) & (den >= RATIO_MIN_N))
        kk_, nn_ = sub[num].cast(pl.Float64).to_numpy(), sub.select(den.cast(pl.Float64))[:, 0].to_numpy()
        pbar = kk_.sum() / nn_.sum()
        var_true = np.mean((kk_ / nn_ - pbar) ** 2) - np.mean(pbar * (1 - pbar) / nn_)
        M = pbar * (1 - pbar) / var_true if var_true > 0 else None
        min_att = math.ceil(1.5 * M) if M is not None else None
        gated = season.filter((pl.col("position_group") == g) & pl.col("gated"))
        cov = (gated.filter(gated.select(den)[:, 0] >= min_att).height / gated.height) if min_att else 0.0
        c = meas["ratios"][name]["groups"][g]
        if not (close(c["M"], M) and c["min_attempts"] == min_att and close(c["coverage"], cov)):
            errors.append(f"{name} × {g}: stored M {c['M']} / {c['min_attempts']} / {c['coverage']} vs "
                          f"{M} / {min_att} / {cov}")
    if errors:
        print(f"FAIL: {len(errors)} mismatches (of {checked} compared values):")
        for e in errors[:60]:
            print("  " + e)
        sys.exit(1)
    print(f"OK: {checked} values agree within {TOL:g}; K = {k} (cap reached: {capped}); odd/even reproduced")
    return summ


# ── Expected flips (ADR 004 §10.3 + majority trigger, written out) ───────────────

def sign_ok(per: list[float]) -> bool:
    s = [v for v in per if abs(v) >= SIGN_NEUTRAL]
    return bool(s) and (all(v > 0 for v in s) or all(v < 0 for v in s))


def majority(per: list[float]) -> bool:
    q = [v for v in per if abs(v) >= RHO_TRIGGER]
    return len(q) >= MAJORITY_LEAGUES and (all(v > 0 for v in q) or all(v < 0 for v in q))


def majority_v(per: list[float]) -> tuple[float | None, float | None]:
    """O3: V = max over signs of the 3rd-largest |ρ| among leagues of that sign (|ρ| ≥ 0.05), and the
    largest opposite-sign |ρ| for the sign that gives V."""
    best = (None, None)
    for sgn in (1, -1):
        same = sorted((abs(v) for v in per if abs(v) >= SIGN_NEUTRAL and v * sgn > 0), reverse=True)
        if len(same) >= MAJORITY_LEAGUES and (best[0] is None or same[2] > best[0]):
            opp = [abs(v) for v in per if abs(v) >= SIGN_NEUTRAL and v * sgn < 0]
            best = (same[2], max(opp) if opp else None)
    return best


def possession(block: dict | None, pool: list[str], col: str, grp: str, majority_on: bool):
    """(status or None to continue, team_style_biased, reason, triggered_by)."""
    if block is None:
        return "display", False, "possession_untestable", ()
    trig, means, by = [], {}, []
    for p in PROXIES:
        per = [block[p]["by_league"].get(c) for c in pool]
        if block[p]["rho_within_mean"] is None or any(v is None for v in per):
            return "display", False, "possession_untestable", ()
        means[p] = block[p]["rho_within_mean"]
        if abs(means[p]) >= RHO_TRIGGER and sign_ok(per):
            by.append(f"mean:{p}")
        if majority_on and majority(per):
            by.append(f"majority:{p}")
    if not by:
        return None, False, None, ()
    if (col, grp) in ALLOW and max(abs(v) for v in means.values()) <= ALLOW_MAX:
        return None, True, "allow_listed", tuple(by)
    return "display", True, "team_style_biased", tuple(by)


def judge(meas: dict, spec: dict, majority_on: bool) -> dict:
    """status, low_reliability, team_style_biased and the possession trigger for a cell that is not
    fixed display / D17 / override / not-preregistered (those keep their snapshot status)."""
    col, grp, unit = spec["column"], spec["group"], spec["unit"]
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    if unit == "share":
        m = meas["team_pass_share"][grp]
        r = m["split_half_r_full"]
        low = all(abs(m["possession"][p]["rho_within_mean"]) < TPS_MAX_RHO for p in PROXIES)
        st = "dna" if low and r is not None and r >= DNA_MIN else "display"
        return {"status": st, "low_reliability": st == "dna" and r < LOW_BELOW, "team_style_biased": False,
                "r": r, "by": ()}
    if unit == "per90":
        m = meas["metrics"][col]["groups"][grp]
        r = m["r_full"]
        if m["zero_share"] > ZERO_HEAVY:
            st = "signature" if r is not None and r >= DNA_MIN else "display"
            return {"status": st, "low_reliability": False, "team_style_biased": False, "r": r, "by": ()}
        ok = r is not None and r >= DNA_MIN
    else:
        m = meas["ratios"][col]["groups"][grp]
        r = m["split_half_r_full"]
        if m["no_signal"]:
            return {"status": "display", "low_reliability": False, "team_style_biased": False, "r": r, "by": ()}
        ok = r is not None and r >= DNA_MIN and m["coverage"] >= RATIO_COVERAGE
    if not ok:
        return {"status": "display", "low_reliability": False, "team_style_biased": False, "r": r, "by": ()}
    st, tsb, _, by = possession(m.get("possession"), pool, col, grp, majority_on)
    st = st or "dna"
    return {"status": st, "low_reliability": st == "dna" and r < LOW_BELOW, "team_style_biased": tsb,
            "r": r, "by": by, "step5": True}


FROZEN = ("fixed_display", "d17_dropped", "d12b_not_preregistered")


def expected(old_meas: dict, new_meas: dict, snapshot: dict) -> dict:
    pool = [str(c) for c in new_meas["provenance"]["pool_competition_ids"]]
    # Self-check: the written-out rules reproduce the snapshot on the old measurements.
    mismatch = []
    for c in snapshot["cells"]:
        if any(r in FROZEN for r in c["reasons"]) or any(r.startswith("override") for r in c["reasons"]):
            continue
        j = judge(old_meas, c, majority_on=False)
        if (j["status"], j["low_reliability"], j["team_style_biased"]) != \
                (c["status"], c["low_reliability"], c["team_style_biased"]):
            mismatch.append((c["column"], c["group"], c["status"], j["status"]))
    if mismatch:
        fail(f"the reference rules do not reproduce the snapshot: {mismatch[:10]}")
    print("self-check: the written-out rules reproduce every snapshot status on the old measurements")

    changes, admitted, boundary = [], [], []
    by_group: dict[str, dict[str, list[str]]] = {g: {"enter_dna": [], "leave_dna": []} for g in GROUPS}
    cells = list(snapshot["cells"]) + [{"column": NEW_RATIO[0], "group": g, "unit": "ratio", "status": None,
                                        "reasons": [], "low_reliability": False, "team_style_biased": False}
                                       for g in GROUPS]
    for c in cells:
        col, grp = c["column"], c["group"]
        frozen = any(r in FROZEN for r in c["reasons"]) or any(r.startswith("override") for r in c["reasons"])
        if frozen:
            continue
        j = judge(new_meas, c, majority_on=True)
        if any(b.startswith("majority") for b in j["by"]) and j["team_style_biased"] and j["status"] == "dna":
            sec = "metrics" if c["unit"] == "per90" else "ratios"
            blk = new_meas[sec][col]["groups"][grp]["possession"]
            admitted.append({"column": col, "group": grp, "triggered_by": list(j["by"]),
                             "per_proxy": {p: {"by_league": [blk[p]["by_league"][x] for x in pool],
                                               "leagues_at_0.4": sum(abs(blk[p]["by_league"][x]) >= RHO_TRIGGER
                                                                     for x in pool),
                                               "max_single_league": max(abs(blk[p]["by_league"][x]) for x in pool)}
                                           for p in PROXIES}})
        if j.get("step5"):   # the registry evaluates the trigger (and its boundary) at step 5 only
            sec = "metrics" if c["unit"] == "per90" else "ratios"
            blk = new_meas[sec][col]["groups"][grp].get("possession")
            if blk:
                for p in PROXIES:
                    per = [blk[p]["by_league"].get(x) for x in pool]
                    if any(v is None for v in per):
                        continue
                    v, opp = majority_v(per)
                    if v is not None and abs(v - RHO_TRIGGER) <= BOUNDARY:
                        boundary.append({"rule": f"majority_trigger:{p}", "column": col, "group": grp, "value": v})
                    if v is not None and v >= RHO_TRIGGER - BOUNDARY and opp is not None \
                            and abs(opp - RHO_TRIGGER) <= BOUNDARY:
                        boundary.append({"rule": f"majority_sign:{p}", "column": col, "group": grp,
                                         "value": opp, "v": v})
        old = (c["status"], c["low_reliability"], c["team_style_biased"])
        new = (j["status"], j["low_reliability"], j["team_style_biased"])
        if old != new:
            why = ("new cell" if c["status"] is None else
                   "majority trigger" if any(b.startswith("majority") for b in j["by"]) and j["status"] == "display"
                   and not any(b.startswith("mean") for b in j["by"]) else
                   f"r {old_r(old_meas, c)} → {j['r']}")
            changes.append({"column": col, "group": grp, "old": {"status": old[0], "low_reliability": old[1],
                                                                   "team_style_biased": old[2]},
                            "new": {"status": new[0], "low_reliability": new[1], "team_style_biased": new[2]},
                            "why": why})
            if old[0] != "dna" and new[0] == "dna":
                by_group[grp]["enter_dna"].append(col)
            if old[0] == "dna" and new[0] != "dna":
                by_group[grp]["leave_dna"].append(col)
    # D17: a new DNA pair at |ρ| ≥ 0.85 without a choice would make derive() raise (STOP).
    new_dna = {(c["column"], c["group"]) for c in snapshot["cells"] if c["status"] == "dna"}
    for ch in changes:
        if ch["new"]["status"] == "dna":
            new_dna.add((ch["column"], ch["group"]))
        elif ch["old"]["status"] == "dna":
            new_dna.discard((ch["column"], ch["group"]))
    d17 = [p for p in new_meas["collinear_pairs"] if abs(p["rho"]) >= COLLINEAR
           and (p["a"], p["group"]) in new_dna and (p["b"], p["group"]) in new_dna]
    known_pairs = {(frozenset((p["a"], p["b"])), p["group"]) for p in old_meas["collinear_pairs"]
                   if abs(p["rho"]) >= COLLINEAR}
    d17_new = [p for p in d17 if (frozenset((p["a"], p["b"])), p["group"]) not in known_pairs]
    return {"description": "Expected M4g2 registry changes (plan 005 M4g2), written by "
                           "spikes/m4g2_reliability_reference.py --write-expected BEFORE the registry code "
                           "changes: ADR 004 §10.3 rules + the majority trigger (O1: the allow-list admits), "
                           "applied to the new measurements; frozen cells (fixed display, D17, override, "
                           "not pre-registered) keep their snapshot status.",
            "snapshot_registry_version": snapshot["registry_version"],
            "measurements_k": new_meas["reliability"]["k"],
            "changes": sorted(changes, key=lambda d: (GROUPS.index(d["group"]), d["column"])),
            "by_group": by_group,
            "majority_admitted_by_allow_list": sorted(admitted, key=lambda d: (GROUPS.index(d["group"]), d["column"])),
            "boundary_majority": sorted(boundary, key=lambda d: (d["rule"], GROUPS.index(d["group"]), d["column"])),
            "direction_changes": [{"column": "dribbled_past_p90", "old": "lower_better", "new": "style"}],
            "d17_new_conflicts": d17_new}


def old_r(old_meas: dict, c: dict):
    if c["unit"] == "per90":
        return old_meas["metrics"][c["column"]]["groups"][c["group"]]["r_full"]
    if c["unit"] == "ratio":
        return old_meas["ratios"][c["column"]]["groups"][c["group"]]["split_half_r_full"]
    return old_meas["team_pass_share"][c["group"]]["split_half_r_full"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check-output", type=Path, required=True, metavar="MEAS.json")
    ap.add_argument("--snapshot", type=Path, required=True,
                    help="dir with registry.json (tools/registry_snapshot.py) and measurements.json (old)")
    ap.add_argument("--write-expected", action="store_true")
    args = ap.parse_args()
    meas = json.loads(args.check_output.read_text())
    snapshot = json.loads((args.snapshot / "registry.json").read_text())
    stints, rows = load()
    check(meas, snapshot, stints, rows)
    if args.write_expected:
        old_meas = json.loads((args.snapshot / "measurements.json").read_text())
        exp = expected(old_meas, meas, snapshot)
        with EXPECTED.open("w") as f:
            json.dump(exp, f, indent=1, ensure_ascii=False, allow_nan=False)
            f.write("\n")
        print(f"wrote {EXPECTED}: {len(exp['changes'])} changes, "
              f"{len(exp['majority_admitted_by_allow_list'])} majority-admitted, "
              f"{len(exp['boundary_majority'])} majority boundary rows, D17 conflicts {len(exp['d17_new_conflicts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
