"""Spike: independent DNA references for M4f (plan 005 M4f2, ADR 004 §10.8, §12).

Re-implements the M4f DNA rules from the ADR text and checks the pipeline against them.
It never imports eleveniq (tests/test_spikes_independent.py). Row status and the D3-deduped
local reference populations come from the M4d spike (spikes/m4d_percentiles_reference.py),
which its own --check-output proves equal to the M4d output; medians, quantiles, z, weights,
dimension scores and distances are computed here in plain Python. The DNA lists, families and
directions are written out below (a fast test asserts they equal the registry).

Rules (ADR 004 §10.8, §12.1, §12.2, §12.6):
- Rows: primary scored rows only (cohort_role "primary"; D5 second-group rows never enter).
- Vector: the group's DNA metrics, each with its league-local percentile (§12.1, basis local)
  and a robust z on the same local reference population:
  z = (x − median) / (1.4826 · MAD); when MAD = 0, scale = IQR / 1.349 (linear-interpolated
  quartiles, Hyndman–Fan type 7); when both are 0, z is null (zero_spread). Stored uncapped.
- A metric is missing for a row when its local cell is not ok (e.g. below_min_attempts). No
  imputation.
- Row weights: each family with a present member gets 1 / (number of such families), split
  equally among its present members; a missing metric has a null weight. Weights sum to 1.
- dna_status: complete (nothing missing) → partial (one metric missing, no family emptied) →
  dna_incomplete (more than one missing, or a family with no member left; no similarity).
- Dimension scores, per DNA family of the group: the mean local percentile of the family's
  present directional members (lower_better enters as 100 − p), naming those members in
  metric-name order.
  Withheld (null, before any other rule): Defending with its D5 status per group (ADR 004
  §13.8, M4g4d), read from spikes/expected_m4g4_flips.json (written by the independent M4g4c /
  M4g4c2 references), not from eleveniq; Passing in CB, FB-WB, DM-CM and AM-Winger, where
  its only directional DNA member is long_pass_completion_pct (pending_mover_stability, §12.9).
  A family with no present directional member is null (no_directional_metric).
- Pair distance (the checks' provisional distance; M5 picks the product metric): weighted
  squared Euclidean on percentile / 100 with pair weights — each family's weight re-split
  over the metrics both rows have, families with no shared metric dropped, the rest
  renormalised. So d(A, B) = d(B, A).

Outputs checked (the M4f2 build):
  player_dna.parquet             one row per (season row, DNA metric), sorted by (key, metric)
  player_dna_dimensions.parquet  one row per (season row, DNA family), sorted by (key, family)

Modes:
  default             write tests/fixtures/m4f_dna/reference.json for the hand-picked rows
                      below, assert their claims and the whole-season counts
  --check-output PATH recompute every cell of PATH (player_dna.parquet) and of the sibling
                      player_dna_dimensions.parquet; exit 1 on any difference

Usage: .venv/bin/python spikes/m4f_dna_reference.py [--check-output PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4d_percentiles_reference as m4d  # noqa: E402  (sibling spike, no eleveniq)

ROOT = m4d.ROOT
PERCENTILES_PARQUET = ROOT / "data" / "processed" / "player_season_percentiles.parquet"
FIXTURE = ROOT / "tests" / "fixtures" / "m4f_dna" / "reference.json"
DIMENSIONS_NAME = "player_dna_dimensions.parquet"
KEY = m4d.KEY

# Written out instead of imported from eleveniq (fast tests assert they equal config / registry).
DNA_PERCENTILE_BASIS = "local"       # ADR 004 §12.1
MAD_SCALE = 1.4826                   # ADR 004 §12.6
IQR_SCALE = 1.349
EXPECTED_M4G4 = ROOT / "spikes" / "expected_m4g4_flips.json"


def _withheld() -> dict[str, dict[str, str]]:
    """Family -> {group: score_status} whose dimension score is withheld: Defending's D5 status
    (§13.8; "ok" restores it) from the M4g4 expected file, Passing's pending status (§12.9)."""
    d5 = json.loads(EXPECTED_M4G4.read_text())["defending_score_status"]
    return {"Defending": {g: st for g, st in d5.items() if st != "ok"},
            "Passing": {g: "pending_mover_stability" for g in ("CB", "FB-WB", "DM-CM", "AM-Winger")}}


WITHHELD: dict[str, dict[str, str]] = _withheld()
# Groups whose Defending mover verdict is "underpowered at n" (a note on the status, D5).
UNDERPOWERED: frozenset[str] = frozenset(
    g for g, d in json.loads(EXPECTED_M4G4.read_text())["status_detail"].items() if d["underpowered"])

H, L, S = "higher_better", "lower_better", "style"
# ADR 004 §10.6 / §10.7 after §12.3 and §13.2 (M4g2: dribbled_past is style): metric ->
# (family, direction), for every DNA metric.
SPEC = {
    "aerial_win_pct": ("Aerial", H), "aerials_lost_p90": ("Aerial", S),
    "aerials_won_p90": ("Aerial", H),
    "ball_recoveries_p90": ("Defending", H), "blocks_p90": ("Defending", S),
    "clearances_p90": ("Defending", S), "dribbled_past_p90": ("Defending", S),
    "fouls_committed_p90": ("Defending", S), "interceptions_p90": ("Defending", S),
    "pressures_p90": ("Defending", S), "tackles_attempted_p90": ("Defending", S),
    "box_entries_pass_p90": ("Progression", H), "final_third_entries_pass_p90": ("Progression", H),
    "progressive_passes_p90": ("Progression", H),
    "box_entries_carry_p90": ("Carrying", H), "dribbles_attempted_p90": ("Carrying", S),
    "final_third_entries_carry_p90": ("Carrying", H), "fouls_won_p90": ("Carrying", S),
    "progressive_carries_p90": ("Carrying", H),
    "long_pass_completion_pct": ("Passing", H), "long_passes_attempted_p90": ("Passing", S),
    "pass_completion_pct": ("Passing", H), "team_pass_share": ("Passing", S),
    "np_shots_p90": ("Goal threat", H), "np_xg_p90": ("Goal threat", H),
    "np_shots_on_target_p90": ("Goal threat", H),
    "op_key_passes_p90": ("Creation", H), "op_xg_assisted_p90": ("Creation", H),
}
# M4g2 (plan 005, ADR 004 §13): the multi-split r_full and the majority trigger re-derived the lists.
_COMMON = ("aerials_won_p90", "ball_recoveries_p90", "box_entries_pass_p90",
           "clearances_p90", "dribbles_attempted_p90", "fouls_won_p90", "long_passes_attempted_p90",
           "pressures_p90", "progressive_carries_p90", "progressive_passes_p90",
           "tackles_attempted_p90")
DNA: dict[str, tuple[str, ...]] = {g: tuple(sorted(_COMMON + extra)) for g, extra in {
    "CB": ("dribbled_past_p90", "final_third_entries_carry_p90", "final_third_entries_pass_p90",
           "long_pass_completion_pct"),
    "FB-WB": ("box_entries_carry_p90", "dribbled_past_p90", "fouls_committed_p90",
              "long_pass_completion_pct", "np_shots_p90", "op_key_passes_p90"),
    "DM-CM": ("aerials_lost_p90", "blocks_p90", "box_entries_carry_p90", "dribbled_past_p90",
              "final_third_entries_carry_p90", "fouls_committed_p90", "interceptions_p90",
              "long_pass_completion_pct", "np_shots_on_target_p90", "np_shots_p90", "np_xg_p90",
              "op_key_passes_p90", "op_xg_assisted_p90", "team_pass_share"),
    "AM-Winger": ("aerials_lost_p90", "blocks_p90", "box_entries_carry_p90", "dribbled_past_p90",
                  "final_third_entries_pass_p90", "fouls_committed_p90", "interceptions_p90",
                  "long_pass_completion_pct", "np_shots_on_target_p90", "np_shots_p90",
                  "op_key_passes_p90"),
    "ST": ("aerial_win_pct", "aerials_lost_p90", "blocks_p90", "box_entries_carry_p90",
           "dribbled_past_p90", "final_third_entries_pass_p90", "fouls_committed_p90", "np_shots_p90",
           "pass_completion_pct"),
}.items()}

Z_TOL = 1e-9          # z, median and scale (plain Python vs polars order of operations)
W_TOL = 1e-12
EXPECTED = {"rows": 1233, "partial": 32, "dna_incomplete": 0, "no_directional_metric": 0,
            "pending_mover_stability": 1075,
            "zero_spread": 0, "iqr_scale": 0}

# Hand-picked rows: (player_name, team_id, why). Each claim in `why` is asserted below.
PICKS = (
    ("N'Golo Kanté", 22, "face-validity DM-CM (Leicester); complete vector"),
    ("Sergio Busquets i Burgos", 217, "face-validity DM-CM (Barcelona); Defending score withheld "
                                      "although his low defensive percentiles stay in the vector"),
    ("Francisco Román Alarcón Suárez", 220, "Isco: the largest |z| of the season (DM-CM "
                                          "box_entries_carry_p90, ≈ 13.7), stored uncapped"),
    ("Theo Walcott", 1, "partial AM-Winger: long_pass_completion_pct below min_attempts, so "
                        "Passing keeps long_passes_attempted_p90 at 1/7; its score is withheld "
                        "(pending_mover_stability, as for every AM-Winger)"),
    ("Antonio Di Natale", 230, "partial ST: aerial_win_pct below min_attempts; Aerial keeps "
                               "aerials_won/lost and its score uses aerials_won_p90 only"),
    ("Jonjo Shelvey", 37, "D3 outsider (Swansea/Newcastle): scored against a local population "
                          "without him, so fouls_won_p90 local is exactly 0"),
    ("Wes Morgan", 22, "plain CB"),
    ("Lionel Andrés Messi Cuccittini", 217, "face-validity AM-Winger"),
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


# ── Robust z ─────────────────────────────────────────────────────────────────

def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def quantile7(xs: list[float], p: float) -> float:
    """Hyndman–Fan type 7 (linear interpolation between order statistics)."""
    s = sorted(xs)
    h = (len(s) - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


def spread(values: list[float]) -> tuple[float, float | None, str | None]:
    """(median, scale, method) of a population; scale None when MAD and IQR are both 0."""
    med = median(values)
    mad = median([abs(v - med) for v in values])
    if mad > 0:
        return med, MAD_SCALE * mad, "mad"
    iqr = quantile7(values, 0.75) - quantile7(values, 0.25)
    if iqr > 0:
        return med, iqr / IQR_SCALE, "iqr"
    return med, None, None


# ── DNA per row ──────────────────────────────────────────────────────────────

class Dna:
    def __init__(self, co: m4d.Cohorts) -> None:
        self.co = co
        self.rows = [r for r in co.scored]          # primary scored rows (roles()[0])
        self._spread: dict[tuple, tuple] = {}
        self.vec = {m4d.key_of(r): self.vector(r) for r in self.rows}

    def population_spread(self, metric: str, group: str, league: int) -> tuple:
        k = (metric, group, league)
        if k not in self._spread:
            pop = self.co.population(self.co.metrics[metric], group, league)
            self._spread[k] = spread([p[metric] for p in pop])
        return self._spread[k]

    def vector(self, r: dict) -> dict:
        """metric -> output fields, plus the row-level dna_status."""
        g = r["position_group"]
        cells: dict[str, dict] = {}
        for m in DNA[g]:
            c = self.co.cell(r, self.co.metrics[m], g)
            fam, direction = SPEC[m]
            out = {"family": fam, "direction": direction, "value": c["value"],
                   "pct_local": c["pct_local"], "n_local": c["n_local"],
                   "metric_status": c["status_local"], "z": None, "z_median": None,
                   "z_scale": None, "z_scale_method": None, "z_status": c["status_local"],
                   "weight": None}
            if c["status_local"] == "ok":
                med, scale, method = self.population_spread(m, g, r["competition_id"])
                out.update(z_median=med, z_scale=scale, z_scale_method=method)
                if scale is None:
                    out["z_status"] = "zero_spread"
                else:
                    out["z"] = (r[m] - med) / scale
            cells[m] = out
        present = {m for m, c in cells.items() if c["metric_status"] == "ok"}
        fams = family_members(g, present)
        for f, ms in fams.items():
            for m in ms:
                cells[m]["weight"] = 1.0 / len(fams) / len(ms)
        missing = len(cells) - len(present)
        emptied = set(SPEC[m][0] for m in DNA[g]) - set(fams)
        status = "complete" if missing == 0 else \
            "dna_incomplete" if missing > 1 or emptied else "partial"
        return {"cells": cells, "dna_status": status, "group": g,
                "dimensions": dimensions(g, cells)}


def family_members(group: str, present: set[str]) -> dict[str, list[str]]:
    """Family -> its present DNA members (families with none are left out)."""
    out: dict[str, list[str]] = defaultdict(list)
    for m in DNA[group]:
        if m in present:
            out[SPEC[m][0]].append(m)
    return dict(out)


def dimensions(group: str, cells: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for f in sorted({SPEC[m][0] for m in DNA[group]}):
        if group in WITHHELD.get(f, {}):
            out[f] = {"score": None, "score_status": WITHHELD[f][group], "members": []}
            continue
        # DNA[group] is sorted, so `members` is in metric-name order (the output contract).
        used = [m for m in DNA[group] if SPEC[m][0] == f and SPEC[m][1] != S
                and cells[m]["metric_status"] == "ok"]
        if not used:
            out[f] = {"score": None, "score_status": "no_directional_metric", "members": []}
            continue
        vals = [cells[m]["pct_local"] if SPEC[m][1] == H else 100.0 - cells[m]["pct_local"]
                for m in used]
        out[f] = {"score": sum(vals) / len(vals), "score_status": "ok", "members": used}
    return out


def pair_weights(group: str, a: dict, b: dict) -> dict[str, float]:
    """Pair weights: each family re-split over the metrics both rows have (§12.6)."""
    both = {m for m in DNA[group] if a["cells"][m]["metric_status"] == "ok"
            and b["cells"][m]["metric_status"] == "ok"}
    fams = family_members(group, both)
    return {m: 1.0 / len(fams) / len(ms) for ms in fams.values() for m in ms}


def pair_distance(a: dict, b: dict) -> float:
    if a["group"] != b["group"]:
        raise ValueError("distance across position groups")
    w = pair_weights(a["group"], a, b)
    return sum(wm * ((a["cells"][m]["pct_local"] - b["cells"][m]["pct_local"]) / 100.0) ** 2
               for m, wm in w.items())


# ── Whole-season checks ──────────────────────────────────────────────────────

def season_checks(dna: Dna) -> dict:
    vecs = list(dna.vec.values())
    counts = {
        "rows": len(vecs),
        "partial": sum(v["dna_status"] == "partial" for v in vecs),
        "dna_incomplete": sum(v["dna_status"] == "dna_incomplete" for v in vecs),
        "no_directional_metric": sum(d["score_status"] == "no_directional_metric"
                                     for v in vecs for d in v["dimensions"].values()),
        "pending_mover_stability": sum(d["score_status"] == "pending_mover_stability"
                                       for v in vecs for d in v["dimensions"].values()),
        "zero_spread": sum(c["z_status"] == "zero_spread" for v in vecs for c in v["cells"].values()),
        "iqr_scale": sum(c["z_scale_method"] == "iqr" for v in vecs for c in v["cells"].values()),
    }
    for k, want in EXPECTED.items():
        if counts[k] != want:
            fail(f"{k}: {counts[k]} ≠ the ADR's {want}")
    for k, v in dna.vec.items():
        total = sum(c["weight"] for c in v["cells"].values() if c["weight"] is not None)
        if abs(total - 1.0) > W_TOL:
            fail(f"row weights of {k} sum to {total!r}")
        for f, groups in WITHHELD.items():
            if v["group"] in groups and v["dimensions"][f]["score"] is not None:
                fail(f"{f} score on {k}")
    # Symmetry and pair weights over every same-group pair.
    by_group: dict[str, list[dict]] = defaultdict(list)
    for v in vecs:
        by_group[v["group"]].append(v)
    pairs = 0
    for g, vs in by_group.items():
        for i, a in enumerate(vs):
            for b in vs[i + 1:]:
                if pair_distance(a, b) != pair_distance(b, a):
                    fail(f"d(A, B) ≠ d(B, A) in {g}")
                if abs(sum(pair_weights(g, a, b).values()) - 1.0) > W_TOL:
                    fail(f"pair weights do not sum to 1 in {g}")
                pairs += 1
    counts["pairs_checked"] = pairs
    zmax = max(((abs(c["z"]), k, m) for k, v in dna.vec.items() for m, c in v["cells"].items()
                if c["z"] is not None), key=lambda t: t[0])
    counts["max_abs_z"] = {"value": zmax[0], "key": list(zmax[1]), "metric": zmax[2]}
    counts["scale_methods"] = sorted({c["z_scale_method"] for v in vecs
                                      for c in v["cells"].values() if c["z_scale_method"]})
    return counts


# ── Default mode: the hand-picked fixture ────────────────────────────────────

def assert_claims(dna: Dna, picked: dict[str, dict], counts: dict) -> None:
    def v(name: str) -> dict:
        return dna.vec[m4d.key_of(picked[name])]

    for name in ("N'Golo Kanté", "Sergio Busquets i Burgos", "Wes Morgan",
                 "Lionel Andrés Messi Cuccittini"):
        if v(name)["dna_status"] != "complete":
            fail(f"{name} should be complete")
    bus = v("Sergio Busquets i Burgos")
    if bus["cells"]["interceptions_p90"]["pct_local"] is None or \
            bus["dimensions"]["Defending"]["score_status"] != "mover_stability_inconclusive":
        fail("Busquets: interceptions should stay in the vector with Defending withheld")
    isco = picked["Francisco Román Alarcón Suárez"]
    if counts["max_abs_z"]["key"] != list(m4d.key_of(isco)) or \
            counts["max_abs_z"]["metric"] != "box_entries_carry_p90" or \
            not 13.6 <= counts["max_abs_z"]["value"] <= 13.8:
        fail(f"the largest |z| is not Isco's box_entries_carry_p90 ≈ 13.7: {counts['max_abs_z']}")
    wal = v("Theo Walcott")
    if wal["dna_status"] != "partial" or \
            wal["cells"]["long_pass_completion_pct"]["metric_status"] != "below_min_attempts" or \
            abs(wal["cells"]["long_passes_attempted_p90"]["weight"] - 1 / 7) > W_TOL or \
            wal["dimensions"]["Passing"]["score_status"] != "pending_mover_stability":
        fail("Walcott: partial, long passes at 1/7, Passing withheld (pending_mover_stability)")
    din = v("Antonio Di Natale")
    if din["dna_status"] != "partial" or \
            din["cells"]["aerial_win_pct"]["metric_status"] != "below_min_attempts" or \
            din["dimensions"]["Aerial"]["members"] != ["aerials_won_p90"]:
        fail("Di Natale: partial on aerial_win_pct, Aerial score from aerials_won_p90 only")
    she = picked["Jonjo Shelvey"]
    if dna.co.is_member(she, dna.co.metrics["fouls_won_p90"], "DM-CM", she["competition_id"]) or \
            v("Jonjo Shelvey")["cells"]["fouls_won_p90"]["pct_local"] != 0.0:
        fail("Shelvey: local outsider with fouls_won_p90 local 0")


def write_fixture(dna: Dna, meas: dict) -> None:
    counts = season_checks(dna)
    picks = []
    for name, team, why in PICKS:
        hits = [r for r in dna.rows if r["player_name"] == name and r["team_id"] == team]
        if len(hits) != 1:
            fail(f"{name} (team {team}): {len(hits)} scored rows")
        picks.append((hits[0], why))
    assert_claims(dna, {r["player_name"]: r for r, _ in picks}, counts)
    out_rows = []
    for r, why in picks:
        v = dna.vec[m4d.key_of(r)]
        out_rows.append({"key": {k: r[k] for k in KEY}, "player_name": r["player_name"],
                         "why": why, "position_group": v["group"], "dna_status": v["dna_status"],
                         "cells": v["cells"], "dimensions": v["dimensions"]})
    doc = {
        "generated_by": "spikes/m4f_dna_reference.py",
        "season_parquet_sha256": meas["provenance"]["season_parquet_sha256"],
        "measurements_sha256": m4d.sha256(m4d.MEASUREMENTS),
        "dna_percentile_basis": DNA_PERCENTILE_BASIS,
        "season_counts": counts,
        "rows": out_rows,
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURE.open("w") as f:
        json.dump(doc, f, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}: {len(out_rows)} rows")
    print(json.dumps(counts, ensure_ascii=False))


# ── --check-output mode ──────────────────────────────────────────────────────

def same(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= tol
    return a == b


def check_output(dna: Dna, meas: dict, path: Path) -> None:
    vec = pl.read_parquet(path)
    dim = pl.read_parquet(path.parent / DIMENSIONS_NAME)
    pct = pl.read_parquet(PERCENTILES_PARQUET)
    errors: list[str] = []
    prov = meas["provenance"]
    want_prov = {"measurement_source": prov["source"],
                 "season_parquet_sha256": prov["season_parquet_sha256"],
                 "percentiles_parquet_sha256": m4d.sha256(PERCENTILES_PARQUET),
                 "dna_percentile_basis": DNA_PERCENTILE_BASIS,
                 # the spike cannot derive the version; the DNA must match the percentiles' one
                 "registry_version": pct["registry_version"].unique().to_list()[0],
                 "polars_version": pl.__version__}
    vcols = {*KEY, "metric", "position_group", "family", "direction", "value", "pct_local",
             "n_local", "metric_status", "z", "z_median", "z_scale", "z_scale_method", "z_status",
             "weight", "dna_status", *want_prov}
    dcols = {*KEY, "family", "position_group", "score", "score_status", "members", "dna_status",
             *want_prov}
    for name, df, cols in (("vector", vec, vcols), ("dimensions", dim, dcols)):
        missing = sorted(cols - set(df.columns))
        if missing:
            fail(f"{name} output is missing columns {missing}")
    for name, df in (("vector", vec), ("dimensions", dim)):
        for col, want in want_prov.items():
            vals = df[col].unique().to_list()
            if vals != [want]:
                errors.append(f"{name} provenance {col}: {vals} ≠ {want!r}")
    for name, df, order in (("vector", vec, [*KEY, "metric"]),
                            ("dimensions", dim, [*KEY, "family"])):
        if not df.select(order).equals(df.select(order).sort(order)):
            errors.append(f"{name} is not sorted by {order}")

    vfields = ("position_group", "family", "direction", "value", "pct_local", "n_local",
               "metric_status", "z", "z_median", "z_scale", "z_scale_method", "z_status",
               "weight", "dna_status")
    got = {(tuple(r[k] for k in KEY), r["metric"]): r for r in vec.iter_rows(named=True)}
    n = 0
    for k, v in dna.vec.items():
        for m, c in v["cells"].items():
            n += 1
            o = got.get((k, m))
            if o is None:
                errors.append(f"vector missing {k} {m}")
                continue
            want = {**c, "position_group": v["group"], "dna_status": v["dna_status"]}
            bad = [f for f in vfields
                   if not same(o[f], want[f], Z_TOL if f.startswith("z") else m4d.PCT_TOL)]
            if bad:
                errors.append(f"vector {k} {m}: {[(f, o[f], want[f]) for f in bad]}")
    if vec.height != n:
        errors.append(f"vector has {vec.height} rows, expected {n}")
    print(f"(a) vector cells recomputed: {n}")

    dfields = ("position_group", "score", "score_status", "members", "dna_status")
    got = {(tuple(r[k] for k in KEY), r["family"]): r for r in dim.iter_rows(named=True)}
    n = 0
    for k, v in dna.vec.items():
        for f, d in v["dimensions"].items():
            n += 1
            o = got.get((k, f))
            if o is None:
                errors.append(f"dimensions missing {k} {f}")
                continue
            want = {**d, "position_group": v["group"], "dna_status": v["dna_status"]}
            bad = [x for x in dfields if not same(o[x], want[x], m4d.PCT_TOL)]
            if bad:
                errors.append(f"dimensions {k} {f}: {[(x, o[x], want[x]) for x in bad]}")
    if dim.height != n:
        errors.append(f"dimensions has {dim.height} rows, expected {n}")
    print(f"(b) dimension scores recomputed: {n}")

    # (c) the build copies the M4d output's local percentile and n (it never re-ranks), so
    # they are equal exactly; (a) compares them to this spike's own ranks within PCT_TOL.
    joined = vec.join(pct.filter(pl.col("cohort_role") == "primary"),
                      on=[*KEY, "metric"], how="left", suffix="_m4d")
    diff = joined.filter(pl.col("pct_local").ne_missing(pl.col("pct_local_m4d"))
                         | pl.col("n_local").ne_missing(pl.col("n_local_m4d")))
    if diff.height:
        errors.append(f"{diff.height} vector cells differ from player_season_percentiles")
    print(f"(c) local percentiles equal to the M4d output: {joined.height - diff.height}")

    if errors:
        for e in errors[:50]:
            print(f"  {e}")
        fail(f"{len(errors)} difference(s) between the M4f output and the spike")
    print("OK: the M4f output equals the spike")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check-output", type=Path, metavar="PATH",
                        help="check player_dna.parquet (and its sibling dimensions file)")
    args = parser.parse_args()
    with m4d.MEASUREMENTS.open(encoding="utf-8") as f:
        meas = json.load(f)
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    co = m4d.Cohorts(m4d.load_rows(meas), m4d.load_metrics(meas), pool, meas)
    dna = Dna(co)
    if args.check_output:
        season_checks(dna)
        check_output(dna, meas, args.check_output)
    else:
        write_fixture(dna, meas)


if __name__ == "__main__":
    sys.exit(main())
