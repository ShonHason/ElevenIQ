"""
M4g6a2 weighting evidence for the M4 sign-off (plan 005 "M4g6a spec" item 3, stats-reviewer
conditions 4, 9, 10; user decisions 2026-10-09). Report only: decides nothing.

Writes its own section (owner `m4g6_weighting`) of docs/reviews/m4-validation.md and the figures
to data/interim/m4g6a2/weighting.json. Items 6 (|z| > 5 with the main-group share) and 8 (same-team
neighbours) come from the re-run of spikes/m4f_weighting_checks.py (docs/reviews/m4f-weighting.md).
Never imports eleveniq: the rows come from m4f_weighting_checks.load() (the DNA build), the DNA
lists and families from spikes/m4f_dna_reference.py, r_full from the committed measurements.

Distance: weighted squared Euclidean on local percentile / 100 over the metrics both rows have
(ADR 004 §12.6). Neighbours: same group, other players, k = 10, ties by the season key.

Weighting variants (fixed before running, user 2026-10-09):
- family (the build): each family with a shared metric 1 / |F|, split equally over its members;
- cap: as family, but each family with one member in the group's DNA list (nominal) gets
  exactly 0.5 / |F|, and the freed weight (0.5 / |F| per capped family) is split equally over
  the pair's uncapped families; weights still sum to 1. If every family of a pair is capped, the
  pair keeps family weights. (M4g6a2 fix round: the first version split freed weight into the
  other capped families too, so with 3 capped families in FB-WB each ended at 0.667 / |F|, not
  the pre-registered 0.5 / |F|; its test encoded that implementation.)
- merge: Goal threat and Creation are one family "Attacking output" in each group where either
  has one nominal member; then family weights. CB Aerial is never merged (cap only).

Evidence per group:
- per-metric share of the squared distance over neighbour pairs (a row and its top 10, build),
  and its absolute mean contribution, against a pure-noise baseline (independent uniform
  percentiles in the group's structure) and the nominal weight; the all-pairs share as a check
  only (approximately the weight);
- leave-one-family-out and leave-one-metric-out top-10 overlap with the build (the one-member
  families: leaving the metric out is leaving the family out; the 8 majority-admitted cells);
- per variant: effective metrics 1 / Σ w², top-10 overlap with the build, same-team and
  same-league neighbours (observed vs expected), the face-validity lists' overlap, and the
  expected noise share Σ w (1 − r_full) (nominal complete-row weights; r = the committed M4g2
  multi-split estimate, stored as `r_full` for per-90s and `split_half_r_full` for ratios and
  team_pass_share: one estimator under two key names);
- item-rest correlation of each directional member with the mean of the family's other
  directional members (a family with one directional member: not assessable);
- every DNA pair with |Spearman ρ| ≥ 0.7, tagged within-family or cross-family.

  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g6a2/registry.json
  .venv/bin/python spikes/m4g6_weighting.py --registry data/interim/m4g6a2/registry.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_dna_reference as ref  # noqa: E402
import m4f_weighting_checks as wc  # noqa: E402
import review_sections as rs  # noqa: E402

ROOT = ref.ROOT
REVIEW = ROOT / "docs" / "reviews" / "m4-validation.md"
OWNER = "m4g6_weighting"
OUT_JSON = ROOT / "data" / "interim" / "m4g6a2" / "weighting.json"
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"

K = wc.K
GROUPS = wc.GROUPS
VARIANTS = ("family", "cap", "merge")
CAP = 0.5                                # user, 2026-10-09
MERGED = "Attacking output"
MERGE_FAMILIES = ("Goal threat", "Creation")
RHO_REPORT = 0.7                         # condition 10 (D17 drops at 0.85)
H = ref.H
# Face-validity rows added for FB-WB and ST, which have no M4f3 face player (stats-reviewer,
# M4g6a2 output review): the 2 most-minutes primary scored rows of each group (the M4g6a1
# item-7 sample rule), checked against the data before use (user, 2026-10-09). Reported lists;
# no user read yet.
EXTRA_FACE = (("Craig Dawson", 27, "FB-WB"), ("Dennis Appiah", 140, "FB-WB"),
              ("Harry Kane", 38, "ST"), ("Rubén Castro Martín", 218, "ST"))
# M4f3 same-team neighbours (docs/reviews/m4f-weighting.md at 958f73e), for item 8.
M4F3_SAME_TEAM = {"CB": 47, "FB-WB": 47, "DM-CM": 28, "AM-Winger": 45, "ST": 10}
POISSON_NOTE = 7                          # ≈ √50: rough spread of a count of about 50
Z_REPORT = wc.Z_REPORT
SHARE_MIXED = wc.SHARE_MIXED


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


# ── Weights (tested in tests/test_m4g6_weighting_spike.py) ──────────────────────

def nominal_families(group: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for m in ref.DNA[group]:
        out[ref.SPEC[m][0]].append(m)
    return dict(out)


def merged_groups() -> set[str]:
    return {g for g in GROUPS
            if any(len(nominal_families(g).get(f, [])) == 1 for f in MERGE_FAMILIES)}


def family_of(group: str, metric: str, variant: str) -> str:
    f = ref.SPEC[metric][0]
    if variant == "merge" and group in merged_groups() and f in MERGE_FAMILIES:
        return MERGED
    return f


def weights(group: str, metrics: list[str], variant: str) -> dict[str, float]:
    """Metric weights over `metrics` (the metrics both rows have, after any leave-out)."""
    fams: dict[str, list[str]] = defaultdict(list)
    for m in metrics:
        fams[family_of(group, m, variant)].append(m)
    if not fams:
        return {}
    n = len(fams)
    fw = {f: 1.0 / n for f in fams}
    if variant == "cap" and n > 1:
        nominal = nominal_families(group)
        capped = [f for f in fams if len(nominal.get(f, [])) == 1]
        free = [f for f in fams if f not in capped]
        if capped and free:
            for c in capped:
                fw[c] = (1.0 - CAP) / n
            for f in free:
                fw[f] = 1.0 / n + len(capped) * CAP / n / len(free)
    return {m: fw[f] / len(ms) for f, ms in fams.items() for m in ms}


def effective_metrics(w: dict[str, float]) -> float:
    return 1.0 / sum(x * x for x in w.values())


def noise_share(w: dict[str, float], r: dict[str, float]) -> float:
    """Expected share of the distance that is noise: Σ w (1 − r_full)."""
    return sum(x * (1.0 - r[m]) for m, x in w.items())


def item_rest(cols: dict[str, list[float | None]]) -> dict[str, float | None]:
    """Spearman ρ of each member with the mean of the others, on rows where all are present.
    One member: None (not assessable)."""
    names = sorted(cols)
    if len(names) < 2:
        return {m: None for m in names}
    rows = [i for i in range(len(cols[names[0]])) if all(cols[m][i] is not None for m in names)]
    out = {}
    for m in names:
        rest = [sum(cols[o][i] for o in names if o != m) / (len(names) - 1) for i in rows]  # type: ignore[misc]
        df = pl.DataFrame({"a": [cols[m][i] for i in rows], "b": rest})
        out[m] = df.select(pl.corr("a", "b", method="spearman")).item()
    return out


# ── Distances and neighbours ────────────────────────────────────────────────────

def shared(a: dict, b: dict, metrics: tuple[str, ...]) -> list[str]:
    return [m for m in metrics if a["cells"][m]["metric_status"] == "ok"
            and b["cells"][m]["metric_status"] == "ok"]


class Pairs:
    """All pair distances of one group under one variant and metric list, plus top-10 lists."""

    def __init__(self, group: str, rows: list[dict], metrics: tuple[str, ...], variant: str) -> None:
        self.rows = rows
        self.d: dict[tuple[int, int], float] = {}
        self.parts: dict[tuple[int, int], dict[str, float]] = {}
        cache: dict[tuple[str, ...], dict[str, float]] = {}
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                sh = tuple(shared(rows[i], rows[j], metrics))
                if sh not in cache:
                    cache[sh] = weights(group, list(sh), variant)
                w = cache[sh]
                c = {m: x * ((rows[i]["cells"][m]["pct_local"] - rows[j]["cells"][m]["pct_local"]) / 100.0) ** 2
                     for m, x in w.items()}
                self.parts[i, j] = c
                self.d[i, j] = sum(c.values())
        self.top = []
        for i in range(len(rows)):
            pid = rows[i]["key"][0]
            cand = [j for j in range(len(rows)) if j != i and rows[j]["key"][0] != pid]
            cand.sort(key=lambda j: (self.dist(i, j), rows[j]["key"]))
            self.top.append(cand[:K])

    def dist(self, i: int, j: int) -> float:
        return self.d[min(i, j), max(i, j)]


def overlaps(a: Pairs, b: Pairs) -> list[int]:
    return [len(set(x) & set(y)) for x, y in zip(a.top, b.top)]


def ov_summary(ov: list[int]) -> dict:
    xs = sorted(float(x) for x in ov)
    return {"median": ref.quantile7(xs, 0.5), "p10": ref.quantile7(xs, 0.1), "mean": sum(xs) / len(xs)}


def same_pos(p: Pairs, pos: int) -> tuple[int, float]:
    obs = exp = 0.0
    rows = p.rows
    for i, top in enumerate(p.top):
        pid, own = rows[i]["key"][0], rows[i]["key"][pos]
        cand = [j for j in range(len(rows)) if j != i and rows[j]["key"][0] != pid]
        exp += K * sum(rows[j]["key"][pos] == own for j in cand) / len(cand)
        obs += sum(rows[j]["key"][pos] == own for j in top)
    return int(obs), exp


def metric_shares(p: Pairs, pairs: list[tuple[int, int]]) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    n = 0
    for i, j in pairs:
        c = p.parts[min(i, j), max(i, j)]
        d = sum(c.values())
        if d == 0:
            continue
        n += 1
        for m, x in c.items():
            tot[m] += x / d
    return {m: v / n for m, v in tot.items()}


def metric_abs(p: Pairs, pairs: list[tuple[int, int]]) -> dict[str, float]:
    """Mean absolute contribution of each metric over `pairs` (shares are compositional, so the
    absolute real / noise ratio is reported next to them)."""
    tot: dict[str, float] = defaultdict(float)
    for i, j in pairs:
        for m, x in p.parts[min(i, j), max(i, j)].items():
            tot[m] += x
    return {m: v / len(pairs) for m, v in tot.items()} if pairs else {}


def neighbour_pairs(p: Pairs) -> list[tuple[int, int]]:
    return [(i, j) for i, top in enumerate(p.top) for j in top]


# ── Main ────────────────────────────────────────────────────────────────────────

def r_full(meas: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for g in GROUPS:
        out[g] = {}
        for m in ref.DNA[g]:
            if m in meas["metrics"]:
                out[g][m] = meas["metrics"][m]["groups"][g]["r_full"]
            elif m in meas["ratios"]:
                out[g][m] = meas["ratios"][m]["groups"][g]["split_half_r_full"]
            else:
                out[g][m] = meas["team_pass_share"][g]["split_half_r_full"]
    return out


def correlations(rows: list[dict], group: str) -> tuple[list[dict], dict]:
    metrics = ref.DNA[group]
    df = pl.DataFrame({m: [r["cells"][m]["pct_local"] if r["cells"][m]["metric_status"] == "ok" else None
                           for r in rows] for m in metrics})
    pairs = []
    for i, a in enumerate(metrics):
        for b in metrics[i + 1:]:
            sub = df.select(a, b).drop_nulls()
            rho = sub.select(pl.corr(a, b, method="spearman")).item()
            if rho is not None and abs(rho) >= RHO_REPORT:
                pairs.append({"a": a, "b": b, "rho": rho,
                              "tag": "within-family" if ref.SPEC[a][0] == ref.SPEC[b][0] else "cross-family"})
    rest = {}
    for f, ms in nominal_families(group).items():
        directional = [m for m in ms if ref.SPEC[m][1] != ref.S]
        cols = {m: [None if v is None else (v if ref.SPEC[m][1] == H else 100.0 - v) for v in df[m].to_list()]
                for m in directional}
        rest[f] = item_rest(cols) if directional else {}
    return pairs, rest


def z_summary(by_group: dict[str, list[dict]]) -> dict:
    """Item 6 (C1): cells with |z| > 5, their rows, and the share of those rows below the
    main-group share bar, against the other rows."""
    rows = [r for g in GROUPS for r in by_group[g]]
    cells = [(r, m) for r in rows for m, c in r["cells"].items()
             if c["z"] is not None and abs(c["z"]) > Z_REPORT]
    zr = {r["key"] for r, _ in cells}
    mixed = lambda r: r["share"] < SHARE_MIXED  # noqa: E731
    return {"cells": len(cells), "rows": len(zr),
            "mixed_share_z_rows": sum(mixed(r) for r in rows if r["key"] in zr) / len(zr) if zr else None,
            "mixed_share_other": sum(mixed(r) for r in rows if r["key"] not in zr) / (len(rows) - len(zr))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True, help="tools/registry_snapshot.py output")
    ap.add_argument("--out", type=Path, default=REVIEW)
    args = ap.parse_args()
    reg = json.loads(args.registry.read_text())
    for g in GROUPS:
        if tuple(reg["groups"][g]["dna"]) != ref.DNA[g]:
            fail(f"{g}: the snapshot's DNA list differs from the reference spike's")
    majority = [(a["column"], a["group"]) for a in reg["majority_admitted"]]
    by_group, meta, _ = wc.load()
    if meta["registry_version"] != reg["registry_version"]:
        fail("the DNA build and the registry snapshot are different versions")
    meas = json.loads(MEASUREMENTS.read_text())
    rfull = r_full(meas)
    rng = random.Random(wc.NOISE_SEED)
    res: dict = {"registry_version": meta["registry_version"], "merged_groups": sorted(merged_groups()),
                 "majority_admitted": majority, "z": z_summary(by_group), "groups": {}}
    for g in GROUPS:
        print(f"{g} …", flush=True)
        rows, full = by_group[g], ref.DNA[g]
        base = Pairs(g, rows, full, "family")
        nominal = nominal_families(g)
        complete_w = {v: weights(g, list(full), v) for v in VARIANTS}
        out: dict = {"rows": len(rows), "nominal_families": {f: len(ms) for f, ms in nominal.items()}}
        # per-metric shares: neighbour pairs vs noise, all pairs as the check
        npairs = neighbour_pairs(base)
        out["share_neighbours"] = metric_shares(base, npairs)
        out["share_all_pairs"] = metric_shares(base, list(base.d))
        real_abs = metric_abs(base, npairs)
        noise: dict[str, list[float]] = defaultdict(list)
        noise_abs: dict[str, list[float]] = defaultdict(list)
        for _ in range(wc.NOISE_REPS):
            nr = wc.noise_rows(g, len(rows), rng)
            npv = Pairs(g, nr, full, "family")
            nb = neighbour_pairs(npv)
            for m, v in metric_shares(npv, nb).items():
                noise[m].append(v)
            for m, v in metric_abs(npv, nb).items():
                noise_abs[m].append(v)
        out["share_noise"] = {m: sum(v) / len(v) for m, v in noise.items()}
        out["abs_vs_noise"] = {m: real_abs[m] / (sum(v) / len(v)) for m, v in noise_abs.items()
                               if m in real_abs and sum(v) > 0}
        out["nominal_weight"] = complete_w["family"]
        # leave one family / metric out
        lofo = {}
        for f, ms in nominal.items():
            kept = tuple(m for m in full if m not in ms)
            lofo[f] = ov_summary(overlaps(base, Pairs(g, rows, kept, "family")))
        out["leave_family_out"] = lofo
        lomo = {}
        for m, gg in majority:
            if gg == g:
                kept = tuple(x for x in full if x != m)
                lomo[m] = ov_summary(overlaps(base, Pairs(g, rows, kept, "family")))
        out["leave_metric_out_majority_admitted"] = lomo
        out["one_member_families"] = sorted(f for f, ms in nominal.items() if len(ms) == 1)
        # variants
        var = {}
        for v in VARIANTS:
            p = base if v == "family" else Pairs(g, rows, full, v)
            team, team_e = same_pos(p, 1)
            league, league_e = same_pos(p, 2)
            var[v] = {"effective_metrics": effective_metrics(complete_w[v]),
                      "noise_share": noise_share(complete_w[v], rfull[g]),
                      "overlap_with_build": ov_summary(overlaps(base, p)),
                      "same_team": team, "same_team_expected": team_e,
                      "same_league": league, "same_league_expected": league_e,
                      "weights": complete_w[v], "pairs": p}
        face = {}
        idx = {(r["name"], r["key"][1]): i for i, r in enumerate(rows)}
        extra = [(n, t) for n, t, gg in EXTRA_FACE if gg == g]
        for name, team in extra:
            if (name, team) not in idx:
                fail(f"face-validity row {name} ({team}) is not a scored {g} row")
        for name, team in (*wc.FACE, *extra):
            if (name, team) in idx:
                i = idx[(name, team)]
                face[wc.SHORT.get(name, name)] = {
                    v: [rows[j]["name"] for j in var[v]["pairs"].top[i]] for v in VARIANTS}
        out["face"] = face
        for v in var.values():
            del v["pairs"]
        out["variants"] = var
        out["corr_pairs"], out["item_rest"] = correlations(rows, g)
        res["groups"][g] = out
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    rs.write_section(args.out, OWNER, render(res))
    print(f"wrote {OUT_JSON} and the {OWNER} section of {args.out}")


# ── Markdown ───────────────────────────────────────────────────────────────────

def f2(x: float | None) -> str:
    return "–" if x is None else f"{x:.2f}"


def table(head: list[str], body: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(r) + " |" for r in body)]


def render(res: dict) -> str:
    L = ["## M4g6a2 — Weighting evidence (family weighting, sign-off item 1)", "",
         f"Generated by `spikes/m4g6_weighting.py` (no eleveniq imports) from the DNA build, registry "
         f"`{res['registry_version'][:12]}…`. Report only (plan 005 \"M4g6a spec\" item 3, stats-reviewer "
         "conditions 4, 9, 10). Items 6 (|z| > 5 with the main-group share) and 8 (same-team "
         "neighbours) are in the regenerated `docs/reviews/m4f-weighting.md`. Split-half "
         "self-retrieval is deferred to the M5 plan (user, 2026-10-09), so the sign-off decision on "
         "weighting is provisional until M5.", "",
         "Variants, fixed before running (user, 2026-10-09):",
         "- **family**: the build;",
         "- **cap**: a one-member family (nominal) gets exactly 0.5/|F|; the freed weight is split "
         "equally over the pair's uncapped families. **Correction (M4g6a2 fix round, stats-reviewer):** "
         "the first run split freed weight into the other capped families too, so with three capped "
         "families in FB-WB each ended at 0.667/|F|, contradicting the pre-registered 0.5/|F|. Its "
         "test encoded that implementation rather than the rule, and was corrected to the rule. "
         "Only FB-WB (three capped families) changed: CB, AM-Winger and ST have one each;",
         f"- **merge**: Goal threat + Creation → \"{MERGED}\" where either has one member "
         f"(groups by the rule: {', '.join(res['merged_groups']) or 'none'}; ST has no Creation member, "
         "so its merge changes nothing and equals the build); CB Aerial never merged.", ""]
    z = res["z"]
    L += [f"**Item 6** (C1, stats-reviewer): {z['cells']} cells with |z| > {Z_REPORT:g} in {z['rows']} rows; "
          f"{100 * z['mixed_share_z_rows']:.1f}% of those rows have a main-group share below "
          f"{SHARE_MIXED:.2f}, against {100 * z['mixed_share_other']:.1f}% of the other rows (M4f3: 95 cells, "
          "64 rows, 34.4%). Full table: `docs/reviews/m4f-weighting.md` §6.", "",
          "**Item 8** (same-team neighbours, build, observed / expected; M4f3 → now): " + "; ".join(
              f"{g} {M4F3_SAME_TEAM[g]} → {o['variants']['family']['same_team']} / "
              f"{o['variants']['family']['same_team_expected']:.1f}" for g, o in res["groups"].items())
          + ". The DNA vector is unchanged since M4g2 apart from stamps (M4g4d frame comparison); the "
            "M4f3 → now differences come from the M4g2 DNA-list changes.", ""]
    L += ["### Variants side by side", "",
          "Effective metrics = 1 / Σ w² of a complete row. Noise share = Σ w (1 − r_full): the "
          "expected share of the all-pairs distance that is measurement noise. Lower means less noise; "
          "it is not a test of correct neighbours: it is minimised by putting all weight on the "
          "most reliable metric. Among top-10 pairs the true-score differences are smaller (mean "
          "neighbour distance ≈ 0.05–0.06 vs ≈ 0.15 for random pairs), so the noise fraction of a "
          "neighbour distance is roughly 2–3× this figure. Overlap = top-10 overlap with the build "
          "(median / P10). Team and league = same-team / same-league neighbours, observed / expected; "
          f"counts of about 50 vary by roughly ±{POISSON_NOTE} by chance and the lists overlap, so "
          "variant differences in these columns read as direction only.", ""]
    body = []
    for g, o in res["groups"].items():
        for v in VARIANTS:
            x = o["variants"][v]
            body.append([g, v, f"{x['effective_metrics']:.1f}", f"{x['noise_share']:.3f}",
                         f"{x['overlap_with_build']['median']:.0f} / {x['overlap_with_build']['p10']:.0f}",
                         f"{x['same_team']} / {x['same_team_expected']:.1f}",
                         f"{x['same_league']} / {x['same_league_expected']:.1f}"])
    L += table(["Group", "Variant", "Effective metrics", "Noise share", "Overlap", "Team", "League"], body) + [""]
    L += ["### Per-metric share of the distance over neighbour pairs (build)", "",
          "The all-pairs share approximately equals the weight (near-uniform percentiles; a heavy "
          "single weight and ties put one-member families slightly below it, e.g. CB Aerial), so the "
          "evidence is the share over a row and its top 10, against pure noise in the same structure. "
          "Shares are compositional (they sum to 1 in both), so the absolute mean contribution real / "
          "noise is shown too: below 1, neighbours agree on the metric more than random selection "
          "would; near 1, they are agreed on only about as well as noise selection.", ""]
    for g, o in res["groups"].items():
        body = [[m, ref.SPEC[m][0], f"{o['nominal_weight'][m]:.3f}", f"{o['share_all_pairs'].get(m, 0):.3f}",
                 f"{o['share_neighbours'].get(m, 0):.3f}", f"{o['share_noise'].get(m, 0):.3f}",
                 f"{o['abs_vs_noise'].get(m, 0):.2f}"]
                for m in ref.DNA[g]]
        L += [f"**{g}** (one-member families: {', '.join(o['one_member_families']) or 'none'})", ""]
        L += table(["Metric", "Family", "Weight", "All pairs", "Neighbour pairs", "Noise share",
                    "Abs. real / noise"], body) + [""]
    L += ["### Leave-one-out top-10 overlap with the build (median / P10)", "",
          "Leaving out a family shows how much it decides the neighbours. For a one-member family, "
          "leaving its metric out is leaving the family out.", ""]
    for g, o in res["groups"].items():
        L += [f"- **{g}**: " + "; ".join(
            f"{f}{' (1 member)' if f in o['one_member_families'] else ''} "
            f"{s['median']:.0f} / {s['p10']:.0f}" for f, s in sorted(o["leave_family_out"].items()))
              + (". Majority-admitted cells left out: " + "; ".join(
                  f"{m} {s['median']:.0f} / {s['p10']:.0f}" for m, s in o["leave_metric_out_majority_admitted"].items())
                 if o["leave_metric_out_majority_admitted"] else "") + "."]
    L += ["", "### Dimension coherence (item-rest Spearman, directional members)", "",
          "Defending has one directional member in every group (`ball_recoveries_p90`), so the "
          "coherence of the largest family is untested here.", ""]
    for g, o in res["groups"].items():
        parts = []
        for f, d in sorted(o["item_rest"].items()):
            if not d:
                parts.append(f"{f}: no directional member")
            elif all(v is None for v in d.values()):
                n_all = o["nominal_families"][f]
                parts.append(f"{f}: not assessable (one directional member"
                             + (f" of {n_all}; the style members are not in the score" if n_all > 1 else "")
                             + ")")
            else:
                parts.append(f"{f}: " + ", ".join(f"{m} {f2(v)}" for m, v in d.items()))
        L += [f"- **{g}**: " + "; ".join(parts) + "."]
    L += ["", f"### DNA pairs with |ρ| ≥ {RHO_REPORT} (Spearman, local percentiles)", "",
          "Only cross-family pairs are double-counted under family weights; within-family pairs share "
          "one family weight.", ""]
    body = [[g, p["a"], p["b"], f"{p['rho']:+.2f}", p["tag"]]
            for g, o in res["groups"].items() for p in o["corr_pairs"]]
    L += (table(["Group", "Metric", "Metric", "ρ", "Tag"], body) if body else ["None."]) + [""]
    L += ["### Face-validity lists per variant", "",
          "FB-WB and ST have no M4f3 face player; their rows (" + ", ".join(
              f"{n} ({g})" for n, _, g in EXTRA_FACE) + ") are the 2 most-minutes rows of each group "
          "(checked in the data), reported lists with no user read yet.", ""]
    for g, o in res["groups"].items():
        for name, lists in o["face"].items():
            fam = lists["family"]
            for v in ("cap", "merge"):
                new = [n for n in lists[v] if n not in fam]
                if new:
                    L += [f"- **{name}** ({g}), {v}: in {', '.join(new)}; out "
                          f"{', '.join(n for n in fam if n not in lists[v])}."]
                else:
                    L += [f"- **{name}** ({g}), {v}: same 10 as the build."]
    return "\n".join(L)


if __name__ == "__main__":
    main()
