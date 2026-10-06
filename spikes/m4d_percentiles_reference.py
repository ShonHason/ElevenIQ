"""Spike: independent percentile references for M4d (plan 005, ADR 004 §11).

Re-implements the M4d cohort and percentile rules from the ADR text and checks the
pipeline against them. It never imports eleveniq (tests/test_spikes_independent.py):
polars is used for I/O only, ranks are counted in plain Python, and the inputs the
registry also reads (min_attempts, no_signal, the D9 league means) come from the
committed measurements file, read as JSON.

Rules (ADR 004 D1, D3, D4, D5, D9, D11, D19, §10.1, §10.2, §10.5, §11):
- Row status, first match: not_in_pool → goalkeeper → below_gate → scored. Only scored
  rows (in_percentile_pool ∧ is_outfield ∧ meets_min_minutes) get a percentile.
- Eligible for metric m in group g: a scored row of g whose value is non-null and, for
  a ratio, whose denominator is ≥ the cell's min_attempts. A ratio cell without
  min_attempts has no eligible rows (no_signal; no_min_attempts when unmeasured).
- Reference population: the eligible rows of g (and of one league, for local), then
  the D3 dedupe: one row per (player_id, season_id, source), most minutes_played, then
  lowest competition_id, then lowest team_id. Filter first, then dedupe.
- Percentile: (less + 0.5 · equal) / n × 100 against the population (Hazen with average
  ties for a member). A D3 outsider is scored against a population without it, so it
  can be exactly 0 or 100. n < MIN_COHORT_SIZE → null (cohort_too_small).
- Global only: D9 undefined → null (d9_undefined), including when a pool league's
  population is below MIN_COHORT_SIZE; a league mean outside the band → null
  (league_inconsistent).
- D5: a gated ambiguous row with a second group is also scored against that group's
  populations (cohort_role "second"; never a member of them).

Modes:
  default             write tests/fixtures/m4d_percentiles/reference.json for the
                      hand-picked rows below (every metric, both scopes)
  --check-output PATH recompute every cell of the M4d output, the D9 league means
                      (against the measurements file) and the population sizes;
                      exit 1 on any difference

Usage: .venv/bin/python spikes/m4d_percentiles_reference.py [--check-output PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
FIXTURE = ROOT / "tests" / "fixtures" / "m4d_percentiles" / "reference.json"

# Written out instead of imported from eleveniq (fast tests assert they equal config).
MIN_COHORT_SIZE = 30                 # ADR 004 D11
GLOBAL_OK_BAND = (40.0, 60.0)        # ADR 004 D9, §10.3 (inclusive)
GATE = 900.0                         # ADR 004 D1
GATE_TOLERANCE = 1e-6
MIXED_POSITION_MINUTES = 45.0        # ADR 004 D6
MIN_AERIAL_CONTESTS = 10             # ADR 002 Chunk A: aerial_win_pct is stored null below it
OUTFIELD = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
GROUP_MINUTES = {"GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
                 "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger", "ST": "minutes_st"}
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")

# Every metric is assumed to apply to all five outfield groups (registry MetricSpec.applies_to
# defaults to them and no spec narrows it in 2015/16). If one is narrowed, this spike must learn it.
# Registry columns the measurements file does not hold (ADR 004 §10.1): goals and
# assists are never measured (D9 undefined), and xg_per_shot is an unmeasured ratio.
UNMEASURED_P90 = ("goals_p90", "assists_p90")
UNMEASURED_RATIOS = {"xg_per_shot": ("xg", ["shots"], [])}
SHARE = ("team_pass_share", "open_play_passes_attempted", ["team_open_play_passes_while_on"])

PCT_TOL = 1e-12
D9_TOL = 1e-8     # the measurements file stores 9 decimals

# Hand-picked rows: (player_name, team_id, why). Each claim in `why` is asserted below.
PICKS = (
    ("Diego Perotti", 233, "mover (Genoa/Roma, Serie A): local and global outsider; "
                           "progressive_carries_p90 local is exactly 100"),
    ("Jonjo Shelvey", 37, "mover (Swansea/Newcastle, PL): local and global outsider; "
                          "fouls_won_p90 local is exactly 0"),
    ("Wahbi Khazri", 41, "mover (Sunderland PL / Bordeaux L1): global outsider, local member"),
    ("Ashley Williams", 26, "tie: CB box_entries_carry_p90 = 0, shared by most of the cohort; "
                            "CB tackle_success_pct is no_signal"),
    ("Odion Jude Ighalo", 23, "ST below long_pass_completion_pct min_attempts (40 < 43): "
                              "null and absent from the reference"),
    ("Harry Kane", 38, "plain ST; long_pass_completion_pct local cohort_too_small, global "
                       "d9_undefined (every ST league population < 30), basis local"),
    ("Steven Naismith", 56, "below gate (915 elapsed, below 900 nominal; D1/D2)"),
    ("Kasper Schmeichel", 22, "goalkeeper"),
    ("Karim Bellarabi", 904, "Bundesliga: not_in_pool"),
    ("Hatem Ben Arfa", 136, "ambiguous AM-Winger/ST (D5): primary and second-group rows"),
    ("Wes Morgan", 22, "plain CB"),
    ("Craig Dawson", 27, "plain FB-WB"),
    ("Andrew Surman", 28, "plain DM-CM"),
    ("Moussa Sissoko", 37, "plain AM-Winger"),
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_of(r: dict) -> tuple:
    return tuple(r[k] for k in KEY)


# ── Metric definitions (from the measurements file plus the unmeasured columns) ──

class Metric:
    def __init__(self, name: str, unit: str, num: str | None = None,
                 plus: list[str] | None = None, minus: list[str] | None = None,
                 cells: dict | None = None) -> None:
        self.name, self.unit = name, unit
        self.num, self.plus, self.minus = num, plus or [], minus or []
        self.cells = cells or {}          # group -> measurement cell (or absent)

    def denominator(self, r: dict) -> float:
        return sum(r[c] for c in self.plus) - sum(r[c] for c in self.minus)


def load_metrics(meas: dict) -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    for col, m in meas["metrics"].items():
        out[col] = Metric(col, "per90", cells=m["groups"])
    for col in UNMEASURED_P90:
        out[col] = Metric(col, "per90")
    for col, m in meas["ratios"].items():
        out[col] = Metric(col, "ratio", m["numerator"], m["denominator_plus"],
                          m["denominator_minus"], cells=m["groups"])
    for col, (num, plus, minus) in UNMEASURED_RATIOS.items():
        out[col] = Metric(col, "ratio", num, plus, minus)
    name, num, plus = SHARE
    out[name] = Metric(name, "share", num, plus, cells=meas["team_pass_share"])
    return out


def league_sizes(m: "Metric", group: str, meas: dict) -> dict[str, int] | None:
    """Each pool league's reference population size for the cell: a ratio's own (min_attempts)
    populations, otherwise the gated group populations."""
    if m.unit == "ratio":
        cell = m.cells.get(group)
        return None if cell is None else cell["ref_n_local"]
    cohort = meas["cohorts"].get(group)     # GK has no cohort: never scored, basis local
    return None if cohort is None else cohort["ref_n_local"]


def global_ok(cell: dict | None, pool: list[str], sizes: dict[str, int] | None) -> bool | None:
    """D9 from the league means; None when undefined: no means, a pool league missing, or a
    pool league's population below MIN_COHORT_SIZE (§11.1: an untestable check is not a pass)."""
    if not cell or "league_mean_global_pct" not in cell:
        return None
    means = cell["league_mean_global_pct"]
    if any(means.get(c) is None for c in pool):
        return None
    if sizes is None or any(sizes.get(c, 0) < MIN_COHORT_SIZE for c in pool):
        return None
    lo, hi = GLOBAL_OK_BAND
    return all(lo <= means[c] <= hi for c in pool)


# ── Rows ─────────────────────────────────────────────────────────────────────

def load_rows(meas: dict) -> list[dict]:
    prov = meas["provenance"]
    if sha256(SEASON_PARQUET) != prov["season_parquet_sha256"]:
        fail("season parquet sha256 differs from the measurements provenance")
    rows = pl.read_parquet(SEASON_PARQUET).to_dicts()
    for r in rows:
        if r["source"] != prov["source"]:
            fail(f"row {key_of(r)} has source {r['source']!r}, measurements are {prov['source']!r}")
        gate = r["minutes_nominal_equiv"] >= GATE - GATE_TOLERANCE
        if gate != r["meets_min_minutes"]:
            fail(f"meets_min_minutes differs from the recomputed gate on {key_of(r)}")
        outfield = sum(r[GROUP_MINUTES[g]] for g in OUTFIELD)
        if r["minutes_gk"] >= MIXED_POSITION_MINUTES and outfield >= MIXED_POSITION_MINUTES:
            fail(f"D6: mixed GK/outfield row {key_of(r)}")
        if r["in_percentile_pool"] is not True:
            r["_status"] = "not_in_pool"
        elif r["is_outfield"] is not True:
            r["_status"] = "goalkeeper"
        elif r["meets_min_minutes"] is not True:
            r["_status"] = "below_gate"
        else:
            r["_status"] = "scored"
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    """D3: one row per (player_id, season_id, source) in this population."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["player_id"], r["season_id"], r["source"])
        rank = (-r["minutes_played"], r["competition_id"], r["team_id"])
        if k not in best or rank < (-best[k]["minutes_played"], best[k]["competition_id"],
                                    best[k]["team_id"]):
            best[k] = r
    return list(best.values())


def percentile(pop: list[float], x: float) -> float:
    less = sum(1 for v in pop if v < x)
    equal = sum(1 for v in pop if v == x)
    return (less + 0.5 * equal) / len(pop) * 100.0


class Cohorts:
    """Every reference population, built once from the scored rows."""

    def __init__(self, rows: list[dict], metrics: dict[str, Metric], pool: list[str],
                 meas: dict) -> None:
        self.rows, self.metrics, self.pool, self.meas = rows, metrics, pool, meas
        # The stored ratio is the one whose min_attempts is checked: num / den, null at den 0.
        for m in metrics.values():
            if m.num is None:
                continue
            for r in rows:
                den = m.denominator(r)
                floor = MIN_AERIAL_CONTESTS if m.name == "aerial_win_pct" else 1
                want = r[m.num] / den if den >= floor and den > 0 else None
                if not ((want is None and r[m.name] is None) or
                        (want is not None and r[m.name] is not None and abs(r[m.name] - want) <= 1e-12)):
                    fail(f"{m.name} on {key_of(r)}: stored {r[m.name]!r} ≠ {m.num} / denominator {want!r}")
        self.scored = [r for r in rows if r["_status"] == "scored"]
        self.by_group: dict[str, list[dict]] = defaultdict(list)
        for r in self.scored:
            self.by_group[r["position_group"]].append(r)
        self._pops: dict[tuple, list[dict]] = {}

    def min_attempts(self, m: Metric, group: str) -> int | None:
        cell = m.cells.get(group)
        return None if cell is None else cell["min_attempts"]

    def eligibility(self, m: Metric, group: str, r: dict) -> str:
        """'ok' or the reason the row is not eligible in (m, group)."""
        if m.unit == "ratio":
            cell = m.cells.get(group)
            if cell is None:
                return "no_min_attempts"
            if cell["min_attempts"] is None:
                return "no_signal"
            if m.denominator(r) < cell["min_attempts"]:
                return "below_min_attempts"
        if r[m.name] is None:
            return "null_value"
        return "ok"

    def population(self, m: Metric, group: str, league: int | None) -> list[dict]:
        k = (m.name, group, league)
        if k not in self._pops:
            rows = [r for r in self.by_group[group]
                    if (league is None or r["competition_id"] == league)
                    and self.eligibility(m, group, r) == "ok"]
            self._pops[k] = dedupe(rows)
        return self._pops[k]

    def cell(self, r: dict, m: Metric, group: str) -> dict:
        """The output fields of one (row, metric, group) cell."""
        out = {"value": r[m.name]}
        if r["_status"] != "scored":
            return {**out, **{f"{f}_{s}": None for f in ("pct", "n") for s in ("local", "global")},
                    "status_local": r["_status"], "status_global": r["_status"]}
        why = self.eligibility(m, group, r)
        for scope, league in (("local", r["competition_id"]), ("global", None)):
            pct, n, status = None, None, why
            if why == "ok":
                pop = self.population(m, group, league)
                n = len(pop)
                if n < MIN_COHORT_SIZE:
                    status = "cohort_too_small"
                elif scope == "global" and self.global_ok(m, group) is None:
                    status = "d9_undefined"
                elif scope == "global" and not self.global_ok(m, group):
                    status = "league_inconsistent"
                else:
                    pct = percentile([p[m.name] for p in pop], r[m.name])
            out.update({f"pct_{scope}": pct, f"n_{scope}": n, f"status_{scope}": status})
        return out

    def global_ok(self, m: Metric, group: str) -> bool | None:
        return global_ok(m.cells.get(group), self.pool, league_sizes(m, group, self.meas))

    def roles(self, r: dict) -> list[tuple[str, str]]:
        """(cohort_role, group) pairs for a season row (D5 second group for ambiguous rows)."""
        out = [("primary", r["position_group"])]
        if r["_status"] == "scored" and r["position_group_ambiguous"] and r["second_position_group"]:
            if r["second_position_group"] not in OUTFIELD:
                fail(f"second group {r['second_position_group']} of {key_of(r)} is not outfield")
            out.append(("second", r["second_position_group"]))
        return out

    def is_member(self, r: dict, m: Metric, group: str, league: int | None) -> bool:
        return any(p is r for p in self.population(m, group, league))


# ── Default mode: the hand-picked fixture ────────────────────────────────────

def pick_rows(rows: list[dict]) -> list[tuple[dict, str]]:
    out = []
    for name, team, why in PICKS:
        hits = [r for r in rows if r["player_name"] == name and r["team_id"] == team]
        if len(hits) != 1:
            fail(f"{name} (team {team}): {len(hits)} rows")
        out.append((hits[0], why))
    return out


def assert_claims(co: Cohorts, picked: dict[str, dict]) -> None:
    """Each `why` in PICKS holds on this data."""
    M = co.metrics
    per, she, kha = picked["Diego Perotti"], picked["Jonjo Shelvey"], picked["Wahbi Khazri"]
    for r in (per, she):
        for league in (r["competition_id"], None):
            if co.is_member(r, M["fouls_won_p90"], r["position_group"], league):
                fail(f"{r['player_name']} should be a D3 outsider ({league})")
    if co.cell(per, M["progressive_carries_p90"], "AM-Winger")["pct_local"] != 100.0:
        fail("Perotti progressive_carries_p90 local is not 100")
    if co.cell(she, M["fouls_won_p90"], "DM-CM")["pct_local"] != 0.0:
        fail("Shelvey fouls_won_p90 local is not 0")
    if co.is_member(kha, M["np_shots_p90"], "AM-Winger", None) or \
            not co.is_member(kha, M["np_shots_p90"], "AM-Winger", kha["competition_id"]):
        fail("Khazri should be a global outsider and a local member")

    wil = picked["Ashley Williams"]
    pop = co.population(M["box_entries_carry_p90"], "CB", None)
    zeros = sum(1 for p in pop if p["box_entries_carry_p90"] == 0)
    if wil["box_entries_carry_p90"] != 0 or zeros < 2 or zeros * 2 < len(pop):
        fail("Williams box_entries_carry_p90 is not a majority tie at 0")
    expected = (0 + 0.5 * zeros) / len(pop) * 100.0
    if abs(co.cell(wil, M["box_entries_carry_p90"], "CB")["pct_global"] - expected) > PCT_TOL:
        fail("Williams tie percentile is not (0.5 · zeros) / n")
    if co.cell(wil, M["tackle_success_pct"], "CB")["status_global"] != "no_signal":
        fail("CB tackle_success_pct should be no_signal")

    lp = M["long_pass_completion_pct"]
    igh, kane = picked["Odion Jude Ighalo"], picked["Harry Kane"]
    c = co.cell(igh, lp, "ST")
    if c["status_local"] != "below_min_attempts" or c["pct_global"] is not None:
        fail("Ighalo long_pass_completion_pct should be below_min_attempts")
    for league in (igh["competition_id"], None):
        if co.is_member(igh, lp, "ST", league):
            fail("Ighalo is in a long-pass reference population")
    # Removing Ighalo changes no other row's long-pass percentile.
    before = {key_of(r): co.cell(r, lp, "ST") for r in co.by_group["ST"]}
    rest = [r for r in co.rows if r is not igh]
    co2 = Cohorts(rest, co.metrics, co.pool, co.meas)
    for r in co2.by_group["ST"]:
        if co2.cell(r, lp, "ST") != before[key_of(r)]:
            fail(f"removing Ighalo changed {r['player_name']}'s long-pass percentile")
    k = co.cell(kane, lp, "ST")
    if k["status_local"] != "cohort_too_small" or k["status_global"] != "d9_undefined":
        fail("Kane long pass should be cohort_too_small locally and d9_undefined globally "
             "(every ST league population is below MIN_COHORT_SIZE)")

    for name, status in (("Steven Naismith", "below_gate"), ("Kasper Schmeichel", "goalkeeper"),
                         ("Karim Bellarabi", "not_in_pool")):
        if picked[name]["_status"] != status:
            fail(f"{name} should be {status}")
    if [g for _, g in co.roles(picked["Hatem Ben Arfa"])] != ["AM-Winger", "ST"]:
        fail("Ben Arfa should be scored in AM-Winger and ST")


def write_fixture(co: Cohorts, meas: dict) -> None:
    picks = pick_rows(co.rows)
    assert_claims(co, {r["player_name"]: r for r, _ in picks})
    out_rows = []
    for r, why in picks:
        for role, group in co.roles(r):
            out_rows.append({
                "key": {k: r[k] for k in KEY}, "player_name": r["player_name"], "why": why,
                "cohort_role": role, "position_group": group, "percentile_status": r["_status"],
                "cells": {name: co.cell(r, m, group) for name, m in sorted(co.metrics.items())},
            })
    doc = {
        "generated_by": "spikes/m4d_percentiles_reference.py",
        "season_parquet_sha256": meas["provenance"]["season_parquet_sha256"],
        "measurements_sha256": sha256(MEASUREMENTS),
        "min_cohort_size": MIN_COHORT_SIZE,
        "rows": out_rows,
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURE.open("w") as f:
        json.dump(doc, f, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}: {len(out_rows)} rows × {len(co.metrics)} metrics")
    for row in out_rows:
        print(f"  {row['player_name']:<22} {row['cohort_role']:<7} {row['position_group']:<9} "
              f"{row['percentile_status']:<11} {row['why']}")


# ── --check-output mode ──────────────────────────────────────────────────────

def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return abs(a - b) <= PCT_TOL
    return a == b


def check_output(co: Cohorts, meas: dict, path: Path) -> None:
    out = pl.read_parquet(path)
    errors: list[str] = []
    prov = meas["provenance"]
    for col, want in (("measurement_source", prov["source"]),
                      ("season_parquet_sha256", prov["season_parquet_sha256"]),
                      ("min_cohort_size", MIN_COHORT_SIZE), ("min_minutes_threshold", GATE)):
        vals = out[col].unique().to_list()
        if vals != [want]:
            errors.append(f"provenance {col}: {vals} ≠ {want!r}")
    # The spike cannot derive the registry version (it may not import eleveniq); it checks the
    # shape here, and an integration test checks it equals eleveniq's REGISTRY_VERSION.
    if out["polars_version"].unique().to_list() != [pl.__version__]:
        errors.append(f"provenance polars_version: {out['polars_version'].unique().to_list()} "
                      f"≠ the running polars {pl.__version__}")
    versions = out["registry_version"].unique().to_list()
    if len(versions) != 1 or not isinstance(versions[0], str) or len(versions[0]) != 64 \
            or any(ch not in "0123456789abcdef" for ch in versions[0]):
        errors.append(f"provenance registry_version: {versions} is not one sha256 hex digest")

    order = [*KEY, "cohort_role", "metric"]
    if not out.select(order).equals(out.select(order).sort(order)):
        errors.append(f"output is not sorted by {order}")

    # (a) every cell, recomputed.
    fields = ("value", "pct_local", "n_local", "status_local", "pct_global", "n_global", "status_global")
    got = {(tuple(r[k] for k in KEY), r["cohort_role"], r["metric"]): r for r in out.iter_rows(named=True)}
    expected_n = 0
    for r in co.rows:
        for role, group in co.roles(r):
            for name, m in co.metrics.items():
                expected_n += 1
                o = got.get((key_of(r), role, name))
                if o is None:
                    errors.append(f"missing {key_of(r)} {role} {name}")
                    continue
                want = co.cell(r, m, group)
                bad = [f for f in fields if not same(o[f], want[f])]
                if o["position_group"] != group or o["percentile_status"] != r["_status"]:
                    bad.append("group/status")
                # GK has no registry cell (D21): no basis.
                exp_basis = None if group not in OUTFIELD else \
                    "global" if co.global_ok(m, group) else "local"
                if o["basis"] != exp_basis:
                    bad.append("basis")
                if bad:
                    errors.append(f"{r['player_name']} {key_of(r)} {role} {name}: {bad} "
                                  f"got {[o[f] for f in fields]} want {[want[f] for f in fields]}")
    if out.height != expected_n:
        errors.append(f"output has {out.height} rows, expected {expected_n}")
    print(f"(a) cells recomputed: {expected_n}")

    # (b) D9 league means from the output, against the measurements file.
    prim = out.filter(pl.col("cohort_role") == "primary")
    matched = inconsistent = small = 0
    for name, m in co.metrics.items():
        for group in OUTFIELD:
            cell = m.cells.get(group)
            if not cell or "league_mean_global_pct" not in cell:
                continue
            sub = prim.filter((pl.col("metric") == name) & (pl.col("position_group") == group)
                              & (pl.col("percentile_status") == "scored"))
            statuses = set(sub["status_global"].to_list()) - {"below_min_attempts", "null_value"}
            ok = co.global_ok(m, group)
            ref_n = cell.get("ref_n_global", meas["cohorts"][group]["ref_n_global"])
            if ref_n < MIN_COHORT_SIZE:
                small += 1
                if statuses != {"cohort_too_small"}:
                    errors.append(f"D9 {name} × {group}: JSON ref_n {ref_n} < {MIN_COHORT_SIZE} "
                                  f"but statuses {statuses}")
                continue
            if ok is None:
                if statuses != {"d9_undefined"}:
                    errors.append(f"D9 {name} × {group}: undefined but statuses {statuses}")
                continue
            if not ok:
                inconsistent += 1
                if statuses != {"league_inconsistent"}:
                    errors.append(f"D9 {name} × {group}: fails the band but statuses {statuses}")
                continue
            if statuses != {"ok"}:
                errors.append(f"D9 {name} × {group}: global_ok but statuses {statuses}")
                continue
            matched += 1
            for comp, want in cell["league_mean_global_pct"].items():
                vals = sub.filter((pl.col("competition_id") == int(comp))
                                  & (pl.col("status_global") == "ok"))["pct_global"].to_list()
                mean = sum(vals) / len(vals)
                if abs(mean - want) > D9_TOL:
                    errors.append(f"D9 {name} × {group} league {comp}: {mean!r} ≠ {want!r}")
    print(f"(b) D9 league means matched: {matched} cells; league_inconsistent: {inconsistent}; "
          f"cohort_too_small: {small}")

    # (c) population sizes, against the measurements file.
    checked = 0
    for name, m in co.metrics.items():
        for group in OUTFIELD:
            cell = m.cells.get(group)
            if m.unit == "ratio":
                if cell is None or cell["min_attempts"] is None:
                    continue
                want_g, want_l = cell["ref_n_global"], cell["ref_n_local"]
            else:
                want_g = meas["cohorts"][group]["ref_n_global"]
                want_l = meas["cohorts"][group]["ref_n_local"]
            sub = prim.filter((pl.col("metric") == name) & (pl.col("position_group") == group))
            g_ns = set(sub["n_global"].drop_nulls().to_list())
            if want_g >= 1 and g_ns != {want_g}:
                errors.append(f"n_global {name} × {group}: {g_ns} ≠ {want_g}")
            for comp, want in want_l.items():
                l_ns = set(sub.filter(pl.col("competition_id") == int(comp))["n_local"].drop_nulls().to_list())
                if want >= 1 and l_ns != {want}:
                    errors.append(f"n_local {name} × {group} × {comp}: {l_ns} ≠ {want}")
            checked += 1
    print(f"(c) population sizes checked: {checked} metric × group cells")

    if errors:
        for e in errors[:50]:
            print(f"  {e}")
        fail(f"{len(errors)} difference(s) between the M4d output and the spike")
    print("OK: the M4d output equals the spike")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check-output", type=Path, metavar="PATH",
                        help="check the M4d percentiles parquet instead of writing the fixture")
    args = parser.parse_args()
    with MEASUREMENTS.open(encoding="utf-8") as f:
        meas = json.load(f)
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    co = Cohorts(load_rows(meas), load_metrics(meas), pool, meas)
    if args.check_output:
        check_output(co, meas, args.check_output)
    else:
        write_fixture(co, meas)


if __name__ == "__main__":
    sys.exit(main())
