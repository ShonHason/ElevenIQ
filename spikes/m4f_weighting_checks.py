"""Spike: M4f3 weighting checks and face-validity neighbours (plan 005 M4f3, ADR 004 §12.5).

Report only: no rule or weight changes. Reads the M4f2 build output (player_dna.parquet) and
the season parquet (names, teams, main-group minute share) and writes
docs/reviews/m4f-weighting.md. It never imports eleveniq (tests/test_spikes_independent.py);
the DNA lists, families and pair distance come from the sibling reference spike
(spikes/m4f_dna_reference.py), whose --check-output proves them equal to the build.

Distance (ADR 004 §12.6, the checks' provisional distance): weighted squared Euclidean on
local percentile / 100 over the metrics both rows have. Weightings compared:
- family:  each family with a shared metric gets 1 / (number of such families), split
           equally among its shared members (the pre-registered weights, §10.8);
- metric:  every shared metric gets 1 / (number of shared metrics) (equal metric weights).
Both decompose exactly into per-family contributions that sum to the distance.

Neighbours (§12.6): same group, primary scored rows, any pool league, excluding the same
player_id; k = 10 (config.DNA_NEIGHBOURS_K, written out). Ties broken by the season key.

Checks, per group:
1. top-10 overlap, family vs equal metric weights, with the top-10-in-top-20 agreement, the
   rank 10 → 11 distance gap and a pure-noise overlap as references;
2. with / without np_shots_on_target_p90 (the groups where it is DNA), under both weightings;
3. each family's share of the squared distance (all pairs, and a row with its top-10
   neighbours: mean, P10, P90), next to the nominal weight and a pure-noise baseline;
4. whether the 32 partial rows are over-represented among the top-10 neighbours, with a
   control that drops the missing metric for every row;
plus distance references (random pair vs 1st / 10th neighbour), same-team and same-league
neighbours, the face-validity neighbour lists with the user's read, and every |z| > 5 cell
with the main-group minute share (position_group_share; stats-reviewer C1, ADR 004 §12.8).

Pure noise (stats-reviewer, M4f3 review): independent uniform percentiles in each group's
metric and family structure, the group's row count, NOISE_REPS seeded replicates. Picking
nearest neighbours by itself lowers the share of families with few metrics, so the noise
share, not 1 / |F|, is the baseline for the neighbour shares.

Usage: .venv/bin/python spikes/m4f_weighting_checks.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_dna_reference as ref  # noqa: E402  (sibling spike, no eleveniq)

ROOT = ref.ROOT
DNA_PARQUET = ROOT / "data" / "processed" / "player_dna.parquet"
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
OUT = ROOT / "docs" / "reviews" / "m4f-weighting.md"
KEY = ref.KEY

K = 10                                   # config.DNA_NEIGHBOURS_K
SOT = "np_shots_on_target_p90"
GROUPS_ALL = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
# The groups where np_shots_on_target_p90 is DNA (AM-Winger and ST at M4f; AM-Winger and DM-CM
# since M4g2, ADR 004 §13.6).
SOT_GROUPS = tuple(g for g in GROUPS_ALL if SOT in ref.DNA[g])
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
Z_REPORT = 5.0                           # stats-reviewer C1: |z| > 5
SHARE_MIXED = 0.70                       # C1: main-group minute share below this
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
LEAGUES = {2: "Premier League", 7: "Ligue 1", 11: "La Liga", 12: "Serie A",
           9: "1. Bundesliga"}           # config.COMPETITION_SEASONS labels
DIST_TOL = 1e-12                         # this spike vs ref.pair_distance
K_WIDE = 20                              # top-10 inside the other weighting's top-20
NOISE_REPS = 5                           # pure-noise replicates per group
NOISE_SEED = 20261007

# Face-validity players (user; plan 005 M4f3): (player_name, team_id).
FACE = (
    ("N'Golo Kanté", 22), ("Sergio Busquets i Burgos", 217),
    ("Francisco Román Alarcón Suárez", 220),            # Isco
    ("Mesut Özil", 1), ("Dimitri Payet", 40),
    ("Lionel Andrés Messi Cuccittini", 217), ("Neymar da Silva Santos Junior", 217),
    ("John Stones", 29), ("Wes Morgan", 22),
)
# The user's read of the lists below (2026-10-07), recorded verbatim in substance.
USER_READ = (
    "Busquets, Özil, Stones and Neymar lists are near-ideal.",
    "The Morgan and Stones lists do not overlap: style is separated correctly.",
    "~~Isco's list is all 8s: evidence for the DM-CM role split (plan 005 M4g).~~ "
    "**Superseded (user, 2026-10-09):** it does not hold on the current build, nor on M4g1's "
    "own labels.",
    "Kanté's list is ball-winning box-to-box midfielders.",
    "No list contains a clearly wrong-style player.",
)
# Outlier spot-check (stats-reviewer, M4f3 review): an independent recount from raw events.
SPOT_CHECK = (
    "**Spot-check: Piqué `progressive_carries_p90` 16.6 (La Liga CB median 5.06, z 5.70).** Recounted from "
    "the raw La Liga 2015/16 events with the ElevenIQ definition (distance to goal centre "
    "falls ≥ 5 units and carry length ≥ 5 units): 1,652 carries, 492 progressive, equal to the "
    "pipeline. Median gain toward goal 9.5 units. Barcelona's other CBs are close (Mascherano "
    "15.0, Bartra 16.3 in 531 min) and David Luiz (PSG) 14.9: team style, not a data error.")
SHORT = {"Sergio Busquets i Burgos": "Busquets", "Francisco Román Alarcón Suárez": "Isco",
         "Lionel Andrés Messi Cuccittini": "Messi", "Neymar da Silva Santos Junior": "Neymar"}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


# ── Load ─────────────────────────────────────────────────────────────────────

def load() -> tuple[dict[str, list[dict]], dict, pl.DataFrame]:
    dna = pl.read_parquet(DNA_PARQUET)
    meta = {}
    for c in ("source", "registry_version", "dna_percentile_basis", "season_parquet_sha256",
              "percentiles_parquet_sha256"):
        vals = dna[c].unique().to_list()
        if len(vals) != 1:
            fail(f"{c} is not unique in {DNA_PARQUET.name}: {vals}")
        meta[c] = vals[0]
    if meta["dna_percentile_basis"] != ref.DNA_PERCENTILE_BASIS:
        fail(f"basis {meta['dna_percentile_basis']!r}")
    if ref.m4d.sha256(SEASON_PARQUET) != meta["season_parquet_sha256"]:
        fail("the season parquet is not the one the DNA build read")
    season = pl.read_parquet(SEASON_PARQUET).select(
        *KEY, "player_name", "team_name", "position_group_share", "second_position_group")
    names = {tuple(r[k] for k in KEY): r for r in season.iter_rows(named=True)}

    rows: dict[tuple, dict] = {}
    for r in dna.iter_rows(named=True):
        k = tuple(r[c] for c in KEY)
        if k not in rows:
            n = names[k]
            rows[k] = {"key": k, "group": r["position_group"], "dna_status": r["dna_status"],
                       "name": n["player_name"], "team": n["team_name"],
                       "league": LEAGUES[r["competition_id"]],
                       "share": n["position_group_share"],
                       "second": n["second_position_group"], "cells": {}}
        rows[k]["cells"][r["metric"]] = {
            "family": r["family"], "pct_local": r["pct_local"], "metric_status": r["metric_status"],
            "value": r["value"], "z": r["z"], "z_median": r["z_median"]}
    by_group: dict[str, list[dict]] = defaultdict(list)
    for r in rows.values():
        if sorted(r["cells"]) != list(ref.DNA[r["group"]]):
            fail(f"{r['key']}: DNA metrics differ from the reference spike")
        if r["dna_status"] == "dna_incomplete":
            fail(f"{r['key']}: dna_incomplete rows are not expected in 2015/16")
        by_group[r["group"]].append(r)
    for g in by_group:
        by_group[g].sort(key=lambda r: r["key"])
    n_rows = sum(len(v) for v in by_group.values())
    n_partial = sum(r["dna_status"] == "partial" for v in by_group.values() for r in v)
    if (n_rows, n_partial) != (ref.EXPECTED["rows"], ref.EXPECTED["partial"]):
        fail(f"rows {n_rows}, partial {n_partial}")
    return dict(by_group), meta, dna


# ── Distance ─────────────────────────────────────────────────────────────────

def contributions(a: dict, b: dict, metrics: tuple[str, ...], weighting: str
                  ) -> dict[str, float]:
    """Family -> its contribution to the squared distance (they sum to the distance)."""
    both = [m for m in metrics if a["cells"][m]["metric_status"] == "ok"
            and b["cells"][m]["metric_status"] == "ok"]
    fams: dict[str, list[str]] = defaultdict(list)
    for m in both:
        fams[a["cells"][m]["family"]].append(m)
    out: dict[str, float] = {}
    for f, ms in fams.items():
        w = 1.0 / len(fams) / len(ms) if weighting == "family" else 1.0 / len(both)
        out[f] = sum(w * ((a["cells"][m]["pct_local"] - b["cells"][m]["pct_local"]) / 100.0) ** 2
                     for m in ms)
    return out


class Variant:
    """All pair distances of one group under one weighting and metric list."""

    def __init__(self, rows: list[dict], metrics: tuple[str, ...], weighting: str) -> None:
        self.rows = rows
        self.contrib: dict[tuple[int, int], dict[str, float]] = {}
        self.d: dict[tuple[int, int], float] = {}
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                c = contributions(rows[i], rows[j], metrics, weighting)
                self.contrib[i, j] = c
                self.d[i, j] = sum(c.values())
        self.ranked = [self._ranked(i) for i in range(len(rows))]
        self.top = [r[:K] for r in self.ranked]

    def dist(self, i: int, j: int) -> float:
        return self.d[min(i, j), max(i, j)]

    def _ranked(self, i: int) -> list[int]:
        """The K_WIDE nearest candidates, nearest first."""
        pid = self.rows[i]["key"][0]
        cand = [j for j in range(len(self.rows)) if j != i and self.rows[j]["key"][0] != pid]
        cand.sort(key=lambda j: (self.dist(i, j), self.rows[j]["key"]))
        return cand[:K_WIDE]


def noise_rows(group: str, n: int, rng: random.Random) -> list[dict]:
    """n complete rows of independent uniform percentiles in the group's DNA structure."""
    return [{"key": (i,), "cells": {m: {"family": ref.SPEC[m][0], "metric_status": "ok",
                                        "pct_local": rng.random() * 100.0}
                                    for m in ref.DNA[group]}}
            for i in range(n)]


def cross_check(rows: list[dict], fam: Variant) -> int:
    """Every family-weight pair distance equals the reference spike's pair_distance."""
    n = 0
    for (i, j), d in fam.d.items():
        r = ref.pair_distance(rows[i], rows[j])
        if abs(r - d) > DIST_TOL or abs(ref.pair_distance(rows[j], rows[i]) - r) > 0:
            fail(f"distance {rows[i]['key']} {rows[j]['key']}: {d} vs reference {r}")
        n += 1
    return n


# ── Statistics ───────────────────────────────────────────────────────────────

def q(xs: list[float], p: float) -> float:
    return ref.quantile7(sorted(xs), p)


def overlap(a: Variant, b: Variant) -> list[int]:
    return [len(set(x) & set(y)) for x, y in zip(a.top, b.top)]


def in_wide(a: Variant, b: Variant) -> float:
    """Mean of (|A top-10 ∩ B top-20| + |B top-10 ∩ A top-20|) / 2 per row."""
    return sum((len(set(ta) & set(rb)) + len(set(tb) & set(ra))) / 2
               for ta, ra, tb, rb in zip(a.top, a.ranked, b.top, b.ranked)) / len(a.top)


def gap_10_11(v: Variant) -> float:
    """Median relative distance gap between a row's 10th and 11th neighbour."""
    return q([(v.dist(i, r[K]) - v.dist(i, r[K - 1])) / v.dist(i, r[K - 1])
              for i, r in enumerate(v.ranked)], 0.5)


def overlap_cells(ov: list[int]) -> list[str]:
    n = len(ov)
    return [f"{sum(ov) / n / K:.3f}", f"{q([float(x) for x in ov], 0.5):.0f}",
            f"{q([float(x) for x in ov], 0.1):.1f}", f"{min(ov)}",
            f"{sum(x >= 7 for x in ov) / n:.1%}", f"{sum(x <= 4 for x in ov) / n:.1%}"]


def families(group: str) -> list[str]:
    return sorted({ref.SPEC[m][0] for m in ref.DNA[group]})


def shares(v: Variant, pairs: list[tuple[int, int]], fams: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {f: [] for f in fams}
    for i, j in pairs:
        c = v.contrib[min(i, j), max(i, j)]
        d = sum(c.values())
        if d == 0:
            continue
        for f in fams:
            out[f].append(c.get(f, 0.0) / d)
    return out


def neighbour_pairs(v: Variant) -> list[tuple[int, int]]:
    return [(i, j) for i, top in enumerate(v.top) for j in top]


def partial_stats(v: Variant) -> dict:
    """Observed vs expected appearances of partial rows among the top-10 lists. Expected:
    each row's 10 slots filled in proportion to the partial share of its candidate set."""
    rows = v.rows
    part = {i for i, r in enumerate(rows) if r["dna_status"] == "partial"}
    obs = exp = 0.0
    indeg = [0] * len(rows)
    for i, top in enumerate(v.top):
        pid = rows[i]["key"][0]
        cand = [j for j in range(len(rows)) if j != i and rows[j]["key"][0] != pid]
        exp += K * sum(j in part for j in cand) / len(cand)
        obs += sum(j in part for j in top)
        for j in top:
            indeg[j] += 1
    pin = [indeg[i] for i in part]
    cin = [indeg[i] for i in range(len(rows)) if i not in part]
    return {"n_partial": len(part), "obs": int(obs), "exp": exp,
            "partial_indeg": sum(pin) / len(pin) if pin else None,
            "complete_indeg": sum(cin) / len(cin), "max_partial_indeg": max(pin) if pin else None,
            "zero_partial": sum(x == 0 for x in pin)}


def team_stats(v: Variant, pos: int) -> tuple[int, float]:
    """Observed and expected top-10 neighbours sharing key[pos] (1 team, 2 competition).
    Expected: each row's 10 slots filled in proportion to its candidate set."""
    rows = v.rows
    obs = exp = 0.0
    for i, top in enumerate(v.top):
        pid, own = rows[i]["key"][0], rows[i]["key"][pos]
        cand = [j for j in range(len(rows)) if j != i and rows[j]["key"][0] != pid]
        exp += K * sum(rows[j]["key"][pos] == own for j in cand) / len(cand)
        obs += sum(rows[j]["key"][pos] == own for j in top)
    return int(obs), exp


def missing_metrics(rows: list[dict]) -> tuple[str, ...]:
    """The DNA metrics some row of the group misses."""
    return tuple(sorted({m for r in rows for m, c in r["cells"].items()
                         if c["metric_status"] != "ok"}))


# ── Report ───────────────────────────────────────────────────────────────────

def table(head: list[str], body: list[list[str]], align: str | None = None) -> list[str]:
    al = align or "l" + "r" * (len(head) - 1)
    sep = ["---:" if a == "r" else "---" for a in al]
    return ["| " + " | ".join(head) + " |", "| " + " | ".join(sep) + " |",
            *["| " + " | ".join(r) + " |" for r in body], ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    by_group, meta, dna = load()
    V: dict[str, dict[str, Variant]] = {}
    noise: dict[str, list[dict[str, Variant]]] = {}
    dropped: dict[str, tuple[str, ...]] = {}
    rng = random.Random(NOISE_SEED)
    checked = 0
    for g in GROUPS:
        rows = by_group[g]
        full = ref.DNA[g]
        V[g] = {"family": Variant(rows, full, "family"), "metric": Variant(rows, full, "metric")}
        checked += cross_check(rows, V[g]["family"])
        if g in SOT_GROUPS:
            no = tuple(m for m in full if m != SOT)
            V[g]["family_nosot"] = Variant(rows, no, "family")
            V[g]["metric_nosot"] = Variant(rows, no, "metric")
        dropped[g] = missing_metrics(rows)
        if dropped[g]:
            kept = tuple(m for m in full if m not in dropped[g])
            V[g]["family_dropped"] = Variant(rows, kept, "family")
            V[g]["metric_dropped"] = Variant(rows, kept, "metric")
        noise[g] = []
        for _ in range(NOISE_REPS):
            nr = noise_rows(g, len(rows), rng)
            noise[g].append({"family": Variant(nr, full, "family"),
                             "metric": Variant(nr, full, "metric")})
        print(f"{g}: {len(rows)} rows", file=sys.stderr)

    L: list[str] = []
    w = L.append
    w("# M4f3 — Weighting checks and face-validity neighbours")
    w("")
    w("Plan 005 M4f3, ADR 004 §12.5. **Report only: no rule or weight changes.** Any weighting "
      "change needs a new ADR entry argued from this report; M4g owns validation.")
    w("")
    w("Generated by `spikes/m4f_weighting_checks.py` (no eleveniq imports) from the current DNA "
      "build (first written for M4f2; regenerated in M4g6a2, plan 005, after the M4g2 list changes):")
    w("")
    w(f"- source `{meta['source']}`, `registry_version` {meta['registry_version'][:12]}…, "
      f"basis `{meta['dna_percentile_basis']}`;")
    w(f"- season parquet {meta['season_parquet_sha256'][:12]}…, percentiles parquet "
      f"{meta['percentiles_parquet_sha256'][:12]}….")
    w("")
    w("## Method")
    w("")
    w("- **Rows:** the 1,233 primary scored rows of the four 2015/16 pool leagues (1,201 "
      "complete, 32 partial, 0 `dna_incomplete`).")
    w("- **Distance** (ADR 004 §12.6, provisional): weighted squared Euclidean on local "
      "percentile / 100 over the metrics both rows have. "
      "**Family weights** (pre-registered): each family 1 / |F|, split equally among its shared "
      "members. **Equal metric weights**: each shared metric 1 / (number of shared metrics). "
      "Both decompose exactly into per-family contributions; a family with no shared metric "
      "in a pair contributes 0 to that pair.")
    w(f"- Every family-weight distance equals the reference spike's `pair_distance` within "
      f"{DIST_TOL:g} ({checked:,} same-group pairs, both orders).")
    w(f"- **Neighbours:** same group, any pool league, excluding the same `player_id`; k = {K}; "
      "ties broken by the season key.")
    w("- **Overlap** = |top-10 A ∩ top-10 B| per row. Columns: mean overlap / 10, median, P10, "
      "min, share of rows with ≥ 7 shared, share with ≤ 4 shared.")
    w(f"- **Pure noise** (stats-reviewer, M4f3 review): independent uniform percentiles in each "
      f"group's metric and family structure, with the group's row count; {NOISE_REPS} seeded "
      "replicates, pooled. It is the reference for what the neighbour rule produces with no "
      "structure in the data.")
    w("- Quantiles are linear-interpolated (type 7). \"RMS gap\" = √d × 100, the weighted RMS "
      "local-percentile gap in points.")
    w("")

    # Weights per group
    w("## Metric weights per group (family weights, complete rows)")
    w("")
    body = []
    for g in GROUPS:
        fams = families(g)
        for f in fams:
            ms = [m for m in ref.DNA[g] if ref.SPEC[m][0] == f]
            body.append([g, f, str(len(ms)), f"1/{len(fams)}",
                         f"1/{len(fams) * len(ms)}", f"1/{len(ref.DNA[g])}",
                         ", ".join(m.removesuffix("_p90") for m in ms)])
    L += table(["Group", "Family", "n", "Family weight", "Per metric (family)",
                "Per metric (equal)", "Members"], body, "llrrrrl")

    # Distance references
    w("## Distance references (family weights)")
    w("")
    w("What \"near\" means: the median distance of a random same-group pair, of each row's "
      "nearest and 10th-nearest neighbour, and under pure noise. Independent uniform "
      "percentiles give an expected distance of 1/6 ≈ 0.167 (RMS gap ≈ 40.8 pts).")
    w("")
    body = []
    rms = lambda d: f"{math.sqrt(d) * 100:.1f}"  # noqa: E731
    for g in GROUPS:
        fv = V[g]["family"]
        allp = q(list(fv.d.values()), 0.5)
        d1 = q([fv.dist(i, r[0]) for i, r in enumerate(fv.ranked)], 0.5)
        d10 = q([fv.dist(i, r[K - 1]) for i, r in enumerate(fv.ranked)], 0.5)
        n_all = q([d for nv in noise[g] for d in nv["family"].d.values()], 0.5)
        n10 = q([nv["family"].dist(i, r[K - 1]) for nv in noise[g]
                 for i, r in enumerate(nv["family"].ranked)], 0.5)
        body.append([g, f"{allp:.4f} ({rms(allp)})", f"{d1:.4f} ({rms(d1)})",
                     f"{d10:.4f} ({rms(d10)})", f"{math.sqrt(d10 / allp):.2f}",
                     f"{n_all:.4f} ({rms(n_all)})", f"{n10:.4f} ({rms(n10)})"])
    L += table(["Group", "Random pair (RMS)", "1st neighbour", "10th neighbour",
                "10th / random (RMS)", "Noise: random pair", "Noise: 10th neighbour"], body)

    # Check 1
    w("## 1. Family weights vs equal metric weights: top-10 overlap")
    w("")
    w(f"**In top-20** = the mean of |A top-10 ∩ B top-20| and |B top-10 ∩ A top-20| per row "
      f"(out of 10). **Gap 10→11** = the median relative distance gap between a row's 10th and "
      f"11th neighbour (family weights): small gaps make the top-10 cut-off fragile, so part "
      f"of the non-overlap is reshuffling near rank 10. **Noise** = the mean overlap / 10 "
      f"between the two weightings on pure noise.")
    w("")
    body = []
    for g in GROUPS:
        fv, mv = V[g]["family"], V[g]["metric"]
        nz = [x for nv in noise[g] for x in overlap(nv["family"], nv["metric"])]
        body.append([g, str(len(by_group[g])), *overlap_cells(overlap(fv, mv)),
                     f"{in_wide(fv, mv):.2f}", f"{gap_10_11(fv):.1%}",
                     f"{sum(nz) / len(nz) / K:.3f}"])
    L += table(["Group", "Rows", "Mean / 10", "Median", "P10", "Min", "≥ 7", "≤ 4",
                "In top-20", "Gap 10→11", "Noise"], body)

    # Check 2
    w(f"## 2. With vs without `np_shots_on_target_p90` ({', '.join(SOT_GROUPS)})")
    w("")
    rho = {p["group"]: p["rho"] for p in json.loads(MEASUREMENTS.read_text())["collinear_pairs"]
           if {p["a"], p["b"]} == {SOT, "np_shots_p90"}}
    w("Without it, the other Goal threat members carry the family weight. "
      "`np_shots_on_target_p90` correlates "
      + ", ".join(f"{rho[g]:.2f} ({g})" for g in SOT_GROUPS)
      + " with `np_shots_p90` (Spearman, the committed measurements; ADR 004 §10.9, §13.6).")
    w("")
    body = []
    for g in SOT_GROUPS:
        for label, a, b in (("family: with vs without", "family", "family_nosot"),
                            ("equal: with vs without", "metric", "metric_nosot"),
                            ("without: family vs equal", "family_nosot", "metric_nosot"),
                            ("with: family vs equal", "family", "metric")):
            body.append([g, label, *overlap_cells(overlap(V[g][a], V[g][b])),
                         f"{in_wide(V[g][a], V[g][b]):.2f}"])
    L += table(["Group", "Comparison", "Mean / 10", "Median", "P10", "Min", "≥ 7", "≤ 4",
                "In top-20"], body, "llrrrrrrr")

    # Check 3
    w("## 3. Each family's share of the squared distance")
    w("")
    w("Per pair, share = family contribution / distance.")
    w("")
    w("- **All pairs** is the fair measure of each family's contribution; compare it with the "
      "nominal weight.")
    w("- **Neighbour pairs** (each row and its 10 neighbours, directed) are biased by the "
      "selection itself: choosing the nearest rows matches the families with few metrics most "
      "closely (their per-metric weight is largest), so their share falls and the share of "
      "many-metric families rises, even on pure noise. Compare neighbour shares with the "
      "**noise** column, not with the nominal weight.")
    w("- Under equal metric weights the nominal share of a family is n_F / N.")
    w("")
    body = []
    for g in GROUPS:
        fams = families(g)
        fv, mv = V[g]["family"], V[g]["metric"]
        allp = shares(fv, list(fv.d), fams)
        nb = shares(fv, neighbour_pairs(fv), fams)
        eq = shares(mv, neighbour_pairs(mv), fams)
        nzf: dict[str, list[float]] = {f: [] for f in fams}
        nzm: dict[str, list[float]] = {f: [] for f in fams}
        for nv in noise[g]:
            for f, xs in shares(nv["family"], neighbour_pairs(nv["family"]), fams).items():
                nzf[f] += xs
            for f, xs in shares(nv["metric"], neighbour_pairs(nv["metric"]), fams).items():
                nzm[f] += xs
        mean = lambda xs: f"{sum(xs) / len(xs):.3f}"  # noqa: E731
        for f in fams:
            n_f = sum(ref.SPEC[m][0] == f for m in ref.DNA[g])
            body.append([g, f, str(n_f), f"{1 / len(fams):.3f}", mean(allp[f]), mean(nb[f]),
                         f"{q(nb[f], 0.1):.3f}", f"{q(nb[f], 0.9):.3f}", mean(nzf[f]),
                         f"{n_f / len(ref.DNA[g]):.3f}", mean(eq[f]), mean(nzm[f])])
    L += table(["Group", "Family", "n", "Nominal", "All pairs", "Neighbours mean", "P10", "P90",
                "Noise neighbours", "Equal: nominal", "Equal: neighbours", "Equal: noise"],
               body, "llrrrrrrrrrr")

    # Check 4
    w("## 4. Partial rows as neighbours")
    w("")
    w("A partial row misses one DNA ratio (`below_min_attempts`, ADR 004 §12.2); a pair "
      "involving it uses the metrics both rows have, with the family re-split. Expected = each "
      "row's 10 slots filled in proportion to the partial share of its candidate set. In-degree "
      "= the number of top-10 lists a row appears in. **Control** = obs / exp with the missing "
      "metric dropped for every row of the group, so the missing-data rule plays no part: what "
      "remains is where the partial players sit (they fell below the minimum attempts, so they "
      "are not typical rows).")
    w("")
    body = []
    for g in GROUPS:
        for wt, label in (("family", "family"), ("metric", "equal")):
            s = partial_stats(V[g][wt])
            if s["n_partial"] == 0:
                continue
            c = partial_stats(V[g][f"{wt}_dropped"])
            body.append([g, label, ", ".join(m.removesuffix("_p90") for m in dropped[g]),
                         str(s["n_partial"]), str(s["obs"]), f"{s['exp']:.1f}",
                         f"{s['obs'] / s['exp']:.2f}", f"{c['obs'] / c['exp']:.2f}",
                         f"{s['partial_indeg']:.1f}", f"{s['complete_indeg']:.1f}",
                         str(s["max_partial_indeg"]), str(s["zero_partial"])])
    L += table(["Group", "Weights", "Missing metric", "Partial rows", "Observed", "Expected",
                "Obs / exp", "Control", "Partial in-degree", "Complete in-degree",
                "Max partial", "Partial with 0"], body, "lllrrrrrrrrr")
    w("Groups without partial rows (CB in 2015/16) are left out. **DM-CM and ST have 2 partial "
      "rows each** (expected ≈ 20 appearances): their ratios rest on a handful of appearances "
      "and are noise.")
    w("")

    # Teammates
    w("## Same-team and same-league neighbours (family weights)")
    w("")
    w("Not one of the pre-registered checks (stats-reviewer, M4f3 review). Observed top-10 "
      "neighbours from the same team / league, against the count expected if neighbours were "
      "drawn uniformly from each row's candidate set. Local percentiles should remove league "
      "effects; team style is not removed (plan 005: possession and team style belong to M4g).")
    w("")
    body = []
    for g in GROUPS:
        to, te = team_stats(V[g]["family"], 1)
        lo, le = team_stats(V[g]["family"], 2)
        body.append([g, str(to), f"{te:.1f}", f"{to / te:.2f}", str(lo), f"{le:.1f}",
                     f"{lo / le:.2f}"])
    L += table(["Group", "Same team", "Expected", "Ratio", "Same league", "Expected", "Ratio"],
               body)

    # Face validity
    w("## 5. Face-validity neighbours (family weights)")
    w("")
    w("Top-10 neighbours under the pre-registered family weights. **Share** = the neighbour's "
      "main-group minute share (`position_group_share`). **Eq** = also in the equal-metric-"
      "weight top-10. **Largest-gap family** = the family with the largest share of that "
      "pair's distance: where the two players differ most, not what they were matched on.")
    w("")
    w("**User's read (2026-10-07), recorded on the M4f2 build; re-read on the current build "
      "(user, 2026-10-09; ADR 004 §14).** The lists below are from the current build (unchanged "
      "since the M4g2 DNA-list changes). The read stands, except the Isco line, which is "
      "superseded. Top-10 overlap of the current lists with the lists that were read (stats-"
      "reviewer, M4 sign-off): 10/10 Kanté, Özil, Payet, Messi, Neymar; 9/10 Busquets, Isco; "
      "8/10 Stones; 7/10 Morgan.")
    w("")
    for line in USER_READ:
        w(f"- {line}")
    w("")
    index = {(r["name"], r["key"][1]): (g, i) for g in GROUPS for i, r in enumerate(by_group[g])}
    for name, team in FACE:
        if (name, team) not in index:
            fail(f"face-validity row not found: {name} ({team})")
        g, i = index[name, team]
        r = by_group[g][i]
        fv, mv = V[g]["family"], V[g]["metric"]
        w(f"### {SHORT.get(name, name)} — {g}, {r['team']}, {r['league']}")
        w("")
        w(f"Main-group share {r['share']:.3f}"
          + (f", second group {r['second']}" if r["second"] else "")
          + f"; `dna_status` {r['dna_status']}; equal-weight overlap "
            f"{len(set(fv.top[i]) & set(mv.top[i]))}/{K}.")
        w("")
        body = []
        for rank, j in enumerate(fv.top[i], 1):
            n = by_group[g][j]
            d = fv.dist(i, j)
            c = fv.contrib[min(i, j), max(i, j)]
            top_f = max(c, key=lambda f: c[f])
            body.append([str(rank), SHORT.get(n["name"], n["name"]), n["team"], n["league"],
                         f"{d:.4f}", f"{math.sqrt(d) * 100:.1f}", f"{n['share']:.2f}",
                         "✓" if j in mv.top[i] else "", f"{top_f} {c[top_f] / d:.0%}"])
        L += table(["#", "Player", "Team", "League", "Distance", "RMS gap", "Share", "Eq",
                    "Largest-gap family"], body, "rlllrrrcl")

    # |z| > 5
    w(f"## 6. Cells with |z| > {Z_REPORT:g} and the main-group minute share")
    w("")
    w("Stats-reviewer C1 (ADR 004 §12.8). z is stored uncapped; M5 decides its use and cap. "
      f"Rows with share < {SHARE_MIXED:.2f} are marked **mixed**.")
    w("")
    cells = []
    for g in GROUPS:
        for r in by_group[g]:
            for m, c in r["cells"].items():
                if c["z"] is not None and abs(c["z"]) > Z_REPORT:
                    cells.append((r, m, c))
    cells.sort(key=lambda t: (-abs(t[2]["z"]), t[0]["key"], t[1]))
    zrows = {t[0]["key"] for t in cells}
    all_rows = [r for g in GROUPS for r in by_group[g]]
    mixed = lambda r: r["share"] < SHARE_MIXED  # noqa: E731
    w(f"- {len(cells)} cells in {len(zrows)} rows. Of those rows, "
      f"{sum(mixed(r) for r in all_rows if r['key'] in zrows) / len(zrows):.1%} have a share "
      f"below {SHARE_MIXED:.2f}, against "
      f"{sum(mixed(r) for r in all_rows if r['key'] not in zrows) / (len(all_rows) - len(zrows)):.1%}"
      " of the other primary scored rows.")
    w("")
    body = []
    for r, m, c in cells:
        body.append([SHORT.get(r["name"], r["name"]), r["team"], r["league"], r["group"],
                     m.removesuffix("_p90"), f"{c['value']:.3f}", f"{c['z_median']:.3f}",
                     f"{c['z']:.2f}", f"{c['pct_local']:.1f}",
                     f"{r['share']:.3f}" + (" **mixed**" if mixed(r) else ""),
                     r["second"] or ""])
    L += table(["Player", "Team", "League", "Group", "Metric", "Value", "Median", "z",
                "pct local", "Share", "Second group"], body, "lllllrrrrrl")
    w(SPOT_CHECK)
    w("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
