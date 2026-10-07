"""Spike: M4g1 style map per group and the DM-CM role-split figures (plan 005 M4g1, ADR 004 §13.4).

Report only: it changes no rule, weight or neighbour. Never imports eleveniq
(tests/test_spikes_independent.py). The DNA rows, guards, families and the pair distance come
from the sibling spikes m4f_weighting_checks (load) and m4f_dna_reference (DNA lists, SPEC,
pair_distance), whose --check-output proves them equal to the build.

Style map (plan 005, "Style map per group"):
- input: complete primary scored rows of one group (partial rows are listed, not plotted;
  PCA needs every metric and ADR 004 §12.2 forbids imputation);
- each metric's local percentile / 100 times √(its family weight per metric), so the squared
  Euclidean distance between two rows equals the M4f weighted squared distance (asserted
  against pair_distance); no standardisation, which would undo the weights;
- PCA in scikit-learn with svd_solver="full" (deterministic); each component's sign is set so
  its largest absolute loading is positive, so a re-run cannot mirror the map.

DM-CM labels (§13.4), from per-position minutes on the ADR 002 §31 clock
(m4g_common.position_minutes); their DM-CM sum must equal the season parquet's minutes_dm_cm
(1e-6):
- 6 / 8: share6 = 6-slot minutes / (6 + 8 minutes); ≥ 0.7 → "6", ≤ 0.3 → "8", else "mixed";
- pivot: single (Center Defensive Midfield), double (Left / Right Defensive Midfield),
  cm (Left / Right / Center Center Midfield), each by ≥ 0.7 of the row's DM-CM minutes, else
  "mixed".

Figures (the question is answered from these, never from the picture):
- counts; mean and SD per label on PC1 and PC2; Cohen's d (pooled SD) between labels;
- the overlap coefficient of two labels' histograms, 20 equal-width bins over the pooled range
  of all plotted rows on that PC;
- neighbour label purity: for each row with label L, the share of its top-10 M4f neighbours
  (full vector, all primary rows of the group, partial rows included, as in M4f3) labelled L,
  averaged over the L rows; b = L's share of all rows (mixed included); lift = (purity − b)
  / (1 − b);
- pre-registered reading (stats-reviewer, M4g0): "6s and 8s form separate regions" iff
  lift ≥ 0.4 for both 6 and 8, and |d(6, 8)| ≥ 1.0 on PC1 or PC2. Applied to 6 / 8; reported
  for the pivot labels;
- within-team contrast: DM-CM teammate pairs (same team and competition, both plotted) whose
  share6 differs by ≥ 0.4: the share of pairs where the more-6 player sits on the 6 side of
  the PC (the side of the 6 mean against the 8 mean);
- Isco, Busquets, Kanté and Schneiderlin: labels and their neighbours' labels.

Page (display only; m4g_style_page): zoom / pan, search, fine-role colours and toggles
(m4g_common), an explainer, plain-language hover cards with registry display names; every
number in a card is re-derived from the parquets after writing (m4g_style_page.check_page).
Compare mode (M4g1b, m4g_compare): two players of the group, their M4f distance, ranks both ways
and the metrics where they differ most and least; every panel is checked against the parquet.
Axis names are the user's reading of the loadings (AXIS_NAMES), each checked against them in
the review; an axis whose proposed name the loadings do not support stays unnamed.

The review text goes into this spike's own section of docs/reviews/m4-validation.md
(review_sections.write_section); other sections are left untouched.

Usage: .venv/bin/python spikes/m4g_style_map.py [--out-dir DIR] [--review PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.decomposition import PCA

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_weighting_checks as wc  # noqa: E402  (sibling spike: load() and its guards)
import m4g_common as cm  # noqa: E402  (sibling spike: position minutes, fine roles, labels)
import m4g_compare as cmp_mod  # noqa: E402  (sibling spike: compare mode facts and check)
import m4g_style_page as page_mod  # noqa: E402  (sibling spike: the page and its check)
import review_sections  # noqa: E402

ref = wc.ref
ROOT = ref.ROOT
SEASON_PARQUET = wc.SEASON_PARQUET
OUT_DIR = ROOT / "data" / "processed" / "reports"
REVIEW = ROOT / "docs" / "reviews" / "m4-validation.md"
KEY = ref.KEY

K = 10                                   # config.DNA_NEIGHBOURS_K
LABEL_SHARE = cm.LABEL_SHARE             # §13.4
LIFT_BAR = 0.4                           # §13.4 (stats-reviewer, M4g0)
D_BAR = 1.0
BINS = 20
CONTRAST_GAP = 0.4                       # within-team pairs: share6 difference
MINUTES_TOL = 1e-6
TOP_LOADINGS = 6

SIX = frozenset({"Center Defensive Midfield", "Left Defensive Midfield", "Right Defensive Midfield"})
EIGHT = frozenset({"Center Midfield", "Left Center Midfield", "Right Center Midfield"})
PIVOT = {"single": frozenset({"Center Defensive Midfield"}),
         "double": frozenset({"Left Defensive Midfield", "Right Defensive Midfield"}),
         "cm": EIGHT}
LABELS_68 = ("6", "8", "mixed")
LABELS_PIVOT = ("single", "double", "cm", "mixed")

# Face-validity players (plan 005): (player_name, team_id) -> short label.
FACE = {("N'Golo Kanté", 22): "Kanté", ("Sergio Busquets i Burgos", 217): "Busquets",
        ("Francisco Román Alarcón Suárez", 220): "Isco", ("Mesut Özil", 1): "Özil",
        ("Dimitri Payet", 40): "Payet", ("Lionel Andrés Messi Cuccittini", 217): "Messi",
        ("Neymar da Silva Santos Junior", 217): "Neymar", ("John Stones", 29): "Stones",
        ("Wes Morgan", 22): "Morgan", ("Morgan Schneiderlin", 39): "Schneiderlin"}
DMCM_FACE = ("Isco", "Busquets", "Kanté", "Schneiderlin")
# Axis names: the user's reading of the loadings, never generated. (positive end, negative end)
# per PC; the sign convention is fixed (largest absolute loading positive).
# An axis whose proposed name the loadings do not support stays None (unnamed on the page)
# until the user names it.
AxisName = tuple[str, str] | None
AXIS_NAMES: dict[str, tuple[AxisName, AxisName]] = {
    "AM-Winger": (("Creator", "Physical"), ("Shooter", "Distributor")),   # user, 2026-10-07
    # Proposed by Claude, accepted by the user 2026-10-07, checked against the loadings:
    "CB": (("Ball-player", "Stopper"),
           ("More aerial duels", "Fewer aerial duels")),  # PC2: user, 2026-10-07 (after the flag)
    "FB-WB": (("Attacking", "Conservative"), ("Box presence", "Distributor")),
    "DM-CM": (("Creator", "Holder"), ("Box-arriving", "Deep playmaker")),
    "ST": (("Aerial target", "Technical forward"),
           ("Link-up focal point", "Safe passer")),     # PC2: user, 2026-10-07 (after the flag)
}
# Notes the legend shows next to an axis (user, 2026-10-07).
AXIS_NOTES: dict[tuple[str, int], str] = {
    ("CB", 1): ("This axis measures how many aerial duels a centre-back has, not how many he "
                "wins: aerials lost load on it more than aerials won (+0.65 vs +0.58)."),
}
# Every loading quoted in AXIS_VERDICTS / AXIS_NOTES, checked on each run (code-reviewer,
# M4g1): (group, axis, claim, metric, value). "value": the loading rounds to it; "min" / "max":
# it is also the axis's most negative / most positive loading; "no_end": no loading of sign
# `value` reaches END_MIN_LOADING (the end has nothing to name).
QUOTED_LOADINGS: tuple[tuple[str, int, str, str | None, float], ...] = (
    ("CB", 1, "value", "aerials_lost_p90", 0.65), ("CB", 1, "value", "aerials_won_p90", 0.58),
    ("CB", 1, "min", "long_pass_completion_pct", -0.11), ("CB", 1, "no_end", None, -1),
    ("FB-WB", 0, "value", "clearances_p90", -0.16), ("FB-WB", 0, "value", "aerials_won_p90", -0.12),
    ("FB-WB", 1, "value", "aerials_lost_p90", 0.41), ("FB-WB", 1, "value", "aerials_won_p90", 0.34),
    ("ST", 1, "min", "pass_completion_pct", -0.39), ("ST", 1, "max", "aerial_win_pct", 0.40),
    ("ST", 1, "value", "progressive_carries_p90", -0.08),
    ("ST", 1, "value", "dribbles_attempted_p90", -0.10),
)


def check_quoted(maps: dict[str, dict]) -> None:
    """Fail when a quoted loading no longer matches the fitted PCA."""
    for g, a, claim, metric, value in QUOTED_LOADINGS:
        comp = dict(zip(maps[g]["model"]["metrics"], maps[g]["model"]["components"][a]))
        if claim == "no_end":
            hit = [m for m, c in comp.items() if value * c >= page_mod.END_MIN_LOADING]
            if hit:
                fail(f"quoted: {g} PC{a + 1} {'+' if value > 0 else '−'} end now has {hit}")
            continue
        if round(comp[metric], 2) != value:
            fail(f"quoted: {g} PC{a + 1} {metric} is {comp[metric]:+.3f}, the text says {value:+.2f}")
        if claim == "min" and metric != min(comp, key=comp.get):
            fail(f"quoted: {g} PC{a + 1} {metric} is no longer the most negative loading")
        if claim == "max" and metric != max(comp, key=comp.get):
            fail(f"quoted: {g} PC{a + 1} {metric} is no longer the most positive loading")
    cb2 = dict(zip(maps["CB"]["model"]["metrics"], maps["CB"]["model"]["components"][1]))
    if not cb2["aerials_lost_p90"] > cb2["aerials_won_p90"]:     # AXIS_NOTES, the CB legend
        fail("quoted: CB PC2 aerials lost no longer loads above aerials won")


def legend_changes(maps: dict[str, dict], labels: dict[str, str], old: float = 0.10) -> list[str]:
    """Each axis end whose defining metrics differ between `old` and END_MIN_LOADING."""
    out = []
    for g, smap in maps.items():
        comp = smap["model"]
        for a in (0, 1):
            vals = dict(zip(comp["metrics"], comp["components"][a]))
            for sign in (1, -1):
                before = page_mod.end_metrics(comp, a, sign, old)
                after = page_mod.end_metrics(comp, a, sign)
                if before != after:
                    out.append(f"- {g} PC{a + 1} {'+' if sign > 0 else '−'} end: "
                               + "; ".join(f"{labels[m]} {vals[m]:+.3f}" for m in before)
                               + " → " + ("; ".join(labels[m] for m in after) or "nothing to name"))
    return out or ["- none"]


def pool_facts(roles: dict[tuple, dict], leagues: dict[int, str]) -> dict:
    """The role counts the legend quotes, computed from this run's lineup minutes."""
    wb = [k for k, r in roles.items() if r["label"] in ("LWB", "RWB")]
    wide = [r for r in roles.values() if r["label"] in ("LW", "RW")]
    mid = sum(r["pos_min"].get(p, 0.0) for r in wide for p in ("Left Midfield", "Right Midfield"))
    tot = sum(sum(v for p, v in r["pos_min"].items() if cm.ROLE_OF[p] in ("LW", "RW"))
              for r in wide)
    return {"am_pure": sum(r["label"] == "AM" for r in roles.values()), "wb_n": len(wb),
            "wb_leagues": sorted({leagues[k[2]] for k in wb}),
            "wide_mid_share": mid / tot if tot > 0 else None}   # None: no LW / RW rows


# The check of each name against the loadings (top 3 per end), as written in the review.
AXIS_VERDICTS: dict[tuple[str, int], str] = {
    ("CB", 0): "supported: passing / carrying progression vs aerials and clearances",
    ("CB", 1): ("**flagged:** \"Aerial\" is supported, but no metric loads on the other end "
                "(strongest −0.11, long pass completion), so \"Ground\" names nothing; the end "
                "means few aerial duels. **User decision (2026-10-07): \"Fewer aerial duels ↔ "
                "More aerial duels\"**; the axis measures aerial volume (lost loads above won), "
                "not dominance, which the page's legend says"),
    ("FB-WB", 0): ("supported: the − end is the absence of attacking output (clearances −0.16, "
                   "aerials −0.12, weak)"),
    ("FB-WB", 1): "supported, with aerial duels part of \"Box presence\" (+0.41, +0.34)",
    ("DM-CM", 0): "supported: creation and shots vs aerials and clearances",
    ("DM-CM", 1): "supported: xG, shots and aerials vs progressive passes, pass share, long passes",
    ("AM-Winger", 0): "supported (user's name): key passes, box entries vs aerials, clearances",
    ("AM-Winger", 1): "supported (user's name): shots vs long and final-third passes",
    ("ST", 0): "supported: aerials vs box entries, long passes, carries, pass completion",
    ("ST", 1): ("**flagged:** \"Link-up focal point\" is supported (aerial win rate, final-third "
                "and long passes), but the − end is pass completion alone (−0.39; carries −0.08, "
                "dribbles −0.10), so \"Runner\" names nothing in the loadings. Unnamed until the "
                "user decides. **User decision (2026-10-07): \"Safe passer ↔ Link-up focal "
                "point\"**; the − end is pass completion, the strongest negative loading (−0.39; "
                "aerial win rate +0.40 is the strongest overall)"),
}
# The user's decision on the role split (2026-10-07), recorded in the review.
SPLIT_DECISION = (
    "**Decision (user, 2026-10-07): DM-CM is not split.** The pre-registered reading found no "
    "separate regions: the 6s and 8s differ on average (and within teams), but they form a "
    "continuum, and the DNA neighbours already separate Isco from Busquets. Backlog for M5: a "
    "6-share context column (lineup slot minutes), shown next to DM-CM rows.")


fail = cm.fail


# ── DM-CM slot labels (from the shared per-position minutes) ─────────────────

def dmcm_labels(rows: list[dict], mins: dict[tuple, Counter]) -> dict[tuple, dict]:
    season = pl.read_parquet(SEASON_PARQUET).select(*KEY, "minutes_dm_cm")
    dm = {tuple(r[c] for c in KEY): r["minutes_dm_cm"] for r in season.iter_rows(named=True)}
    out = {}
    worst = 0.0
    for r in rows:
        k = r["key"]
        pos = Counter({p: v for p, v in mins.get(k[:3], Counter()).items()
                       if cm.group_of(p) == "DM-CM"})
        unknown = set(pos) - SIX - EIGHT
        if unknown:
            fail(f"{k}: DM-CM position(s) outside the 6 / 8 definitions: {sorted(unknown)}")
        total = sum(pos.values())
        worst = max(worst, abs(total - dm[k]))
        if abs(total - dm[k]) > MINUTES_TOL:
            fail(f"{k}: 6 + 8 minutes {total:.6f} != minutes_dm_cm {dm[k]:.6f}")
        six = sum(v for p, v in pos.items() if p in SIX)
        share6 = six / total
        lab = "6" if share6 >= LABEL_SHARE else "8" if share6 <= 1 - LABEL_SHARE else "mixed"
        piv = next((name for name, s in PIVOT.items()
                    if sum(v for p, v in pos.items() if p in s) / total >= LABEL_SHARE), "mixed")
        out[k] = {"share6": share6, "label": lab, "pivot": piv, "minutes": total,
                  "by_pos": dict(sorted(pos.items()))}
    print(f"DM-CM minutes reconciled for {len(out)} rows (max |diff| {worst:.2e})")
    return out


# ── Style map (PCA) ──────────────────────────────────────────────────────────

def metric_weights(group: str) -> dict[str, float]:
    """Per-metric family weight for a complete row (ADR 004 §12.6)."""
    fams = ref.family_members(group, set(ref.DNA[group]))
    return {m: 1.0 / len(fams) / len(ms) for ms in fams.values() for m in ms}


def style_map(group: str, rows: list[dict]) -> dict:
    metrics = ref.DNA[group]
    w = metric_weights(group)
    complete = [r for r in rows if r["dna_status"] == "complete"]
    x = np.array([[r["cells"][m]["pct_local"] / 100.0 * math.sqrt(w[m]) for m in metrics]
                  for r in complete])
    # The map's input distance is the M4f distance (every pair of complete rows).
    for i in range(len(complete)):
        d2 = ((x[i + 1:] - x[i]) ** 2).sum(axis=1)
        for j, d in enumerate(d2, start=i + 1):
            if abs(d - ref.pair_distance(complete[i], complete[j])) > 1e-12:
                fail(f"{group}: input distance != pair_distance for {complete[i]['key']}, "
                     f"{complete[j]['key']}")
    pca = PCA(svd_solver="full").fit(x)
    comps = pca.components_.copy()
    for c in comps:
        if c[np.argmax(np.abs(c))] < 0:
            c *= -1
    scores = (x - pca.mean_) @ comps[:2].T
    loadings = []
    for c in comps[:2]:
        order = sorted(range(len(metrics)), key=lambda j: (-abs(c[j]), metrics[j]))
        loadings.append([(metrics[j], ref.SPEC[metrics[j]][0], float(c[j]))
                         for j in order[:TOP_LOADINGS]])
    return {"group": group, "rows": complete, "scores": scores,
            "evr": [float(v) for v in pca.explained_variance_ratio_[:2]],
            "loadings": loadings, "partial": [r for r in rows if r["dna_status"] == "partial"],
            "n_metrics": len(metrics), "metrics": metrics, "x": x,
            "model": {"metrics": list(metrics), "weights": [w[m] for m in metrics],
                      "mean": [float(v) for v in pca.mean_],
                      "components": [[float(v) for v in c] for c in comps[:2]]},
            "sd": [float(np.std(scores[:, a], ddof=1)) for a in (0, 1)]}


# ── DM-CM figures ────────────────────────────────────────────────────────────

def neighbours(rows: list[dict]) -> dict[tuple, list[dict]]:
    out = {}
    for a in rows:
        cand = [(ref.pair_distance(a, b), b["key"], b) for b in rows if b["key"][0] != a["key"][0]]
        cand.sort(key=lambda t: (t[0], t[1]))
        out[a["key"]] = [b for _, _, b in cand[:K]]
    return out


def mean_sd(v: list[float]) -> tuple[float, float]:
    m = sum(v) / len(v)
    return m, math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0


def cohen_d(a: list[float], b: list[float]) -> float:
    (ma, sa), (mb, sb) = mean_sd(a), mean_sd(b)
    pooled = math.sqrt(((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2) / (len(a) + len(b) - 2))
    return (ma - mb) / pooled


def overlap(a: list[float], b: list[float], lo: float, hi: float) -> float:
    def hist(v: list[float]) -> list[float]:
        h = [0] * BINS
        for x in v:
            h[min(BINS - 1, int((x - lo) / (hi - lo) * BINS))] += 1
        return [c / len(v) for c in h]
    return sum(min(p, q) for p, q in zip(hist(a), hist(b)))


def label_figures(sm: dict, all_rows: list[dict], lab: dict[tuple, dict], field: str,
                  names: tuple[str, ...], nbrs: dict[tuple, list[dict]]) -> dict:
    pcs = {lbl: ([], []) for lbl in names}
    for r, s in zip(sm["rows"], sm["scores"]):
        pcs[lab[r["key"]][field]][0].append(float(s[0]))
        pcs[lab[r["key"]][field]][1].append(float(s[1]))
    lo_hi = [(float(sm["scores"][:, i].min()), float(sm["scores"][:, i].max())) for i in (0, 1)]
    n_all = len(all_rows)
    base = Counter(lab[r["key"]][field] for r in all_rows)
    per_label = {}
    for lbl in names:
        own = [r for r in all_rows if lab[r["key"]][field] == lbl]
        purity = (sum(sum(lab[b["key"]][field] == lbl for b in nbrs[r["key"]]) / K for r in own)
                  / len(own)) if own else None
        b = base[lbl] / n_all
        per_label[lbl] = {
            "n_all": base[lbl], "n_plotted": len(pcs[lbl][0]), "b": b, "purity": purity,
            "lift": None if purity is None else (purity - b) / (1 - b),
            "pc": [mean_sd(pcs[lbl][i]) if len(pcs[lbl][i]) > 1 else None for i in (0, 1)]}
    pairs = {}
    core = [n for n in names if n != "mixed"]
    for i, a in enumerate(core):
        for bb in core[i + 1:]:
            if len(pcs[a][0]) > 1 and len(pcs[bb][0]) > 1:
                pairs[(a, bb)] = {
                    "d": [cohen_d(pcs[a][k], pcs[bb][k]) for k in (0, 1)],
                    "ovl": [overlap(pcs[a][k], pcs[bb][k], *lo_hi[k]) for k in (0, 1)]}
    return {"labels": per_label, "pairs": pairs}


def reading(fig: dict, a: str, b: str) -> dict:
    lifts = [fig["labels"][x]["lift"] for x in (a, b)]
    d = fig["pairs"][(a, b)]["d"]
    lift_ok = all(v is not None and v >= LIFT_BAR for v in lifts)
    d_ok = any(abs(v) >= D_BAR for v in d)
    return {"lift_ok": lift_ok, "d_ok": d_ok, "separate": lift_ok and d_ok}


def within_team(sm: dict, lab: dict[tuple, dict]) -> dict:
    six = [i for i, r in enumerate(sm["rows"]) if lab[r["key"]]["label"] == "6"]
    eight = [i for i, r in enumerate(sm["rows"]) if lab[r["key"]]["label"] == "8"]
    side = [1.0 if sm["scores"][six, k].mean() >= sm["scores"][eight, k].mean() else -1.0
            for k in (0, 1)]
    by_team: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(sm["rows"]):
        by_team[(r["key"][1], r["key"][2])].append(i)
    agree, n = [0, 0], 0
    for idx in by_team.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                si, sj = lab[sm["rows"][i]["key"]]["share6"], lab[sm["rows"][j]["key"]]["share6"]
                if abs(si - sj) < CONTRAST_GAP:
                    continue
                hi, lo = (i, j) if si > sj else (j, i)
                n += 1
                for k in (0, 1):
                    agree[k] += side[k] * (sm["scores"][hi, k] - sm["scores"][lo, k]) > 0
    return {"pairs": n, "agree": agree, "teams": sum(len(v) >= 2 for v in by_team.values())}


# ── Page (rendering in m4g_style_page) ───────────────────────────────────────

def dmcm_figs_html(fig: dict) -> str:
    rows = []
    for lbl, v in fig["labels"].items():
        pc = "".join(f'<td class="n">{p[0]:+.3f} ± {p[1]:.3f}</td>' if p else "<td>–</td>"
                     for p in v["pc"])
        rows.append(f'<tr><td>{page_mod.esc(lbl)}</td><td class="n">{v["n_all"]}</td>'
                    f'<td class="n">{v["n_plotted"]}</td>{pc}<td class="n">{v["b"]:.2f}</td>'
                    f'<td class="n">{fmt(v["purity"])}</td><td class="n">{fmt(v["lift"])}</td></tr>')
    d = fig["pairs"][("6", "8")]
    return ('<h2>Role-split evaluation (M4g1): 6 vs 8</h2><p class="muted">The lineup-slot label '
            '(6 = defensive-midfield slot, 8 = central-midfield slot, at 0.7 of DM-CM minutes). '
            'Decision: no split (user, 2026-10-07).</p><div class="scroll"><table><thead><tr>'
            '<th>Label</th><th>Rows</th><th>Plotted</th><th>PC1 mean ± SD</th><th>PC2 mean ± SD</th>'
            '<th>Base rate b</th><th>Neighbour purity</th><th>Lift</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>'
            f'<p>Cohen\'s d (6 − 8): PC1 {d["d"][0]:+.2f}, PC2 {d["d"][1]:+.2f}. Overlap '
            f'coefficient: PC1 {d["ovl"][0]:.2f}, PC2 {d["ovl"][1]:.2f}. Full figures: '
            'docs/reviews/m4-validation.md.</p>')


# ── Review write-up ──────────────────────────────────────────────────────────

def fmt(v: float | None, spec: str = ".2f") -> str:
    return "–" if v is None else format(v, spec)


def review(maps: dict[str, dict], lab: dict[tuple, dict], f68: dict, fpv: dict, read68: dict,
           readpv: dict[tuple, dict], wt: dict, nbrs: dict, dm_all: list[dict], meta: dict,
           labels: dict[str, str]) -> str:
    L = ["## M4g1 — Style map and the DM-CM role split", "",
         "Generated by `spikes/m4g_style_map.py` (no eleveniq imports) from the M4f build: source "
         f"`{meta['source']}`, `registry_version` {meta['registry_version'][:12]}…, basis "
         f"`{meta['dna_percentile_basis']}`. Pages: `data/processed/reports/style_map_<group>.html`.", "",
         "### Method", "",
         "- **Input:** complete primary scored rows per group; each local percentile / 100 times "
         "√(its per-metric family weight). Every complete pair's input distance equals "
         "`pair_distance` within 1e-12 (asserted). No standardisation.",
         "- **PCA:** scikit-learn, `svd_solver=\"full\"`, one fit per group; each component's "
         "largest absolute loading is positive.",
         "- **Labels:** per-position DM-CM minutes on the ADR 002 §31 clock (the "
         "`verify_core_metrics` spike's implementation); their sum equals `minutes_dm_cm` for "
         f"every DM-CM row (tolerance {MINUTES_TOL:g}). 6 / 8 and pivot labels at "
         f"{LABEL_SHARE} (ADR 004 §13.4).",
         f"- **Neighbours:** top {K} M4f neighbours on the full vector (partial rows included), "
         "excluding the same player; ties by the season key.", "",
         "### Variance explained and top loadings", "",
         "| Group | Rows plotted | Partial (listed) | PC1 | PC2 | PC1 + PC2 | PC1 top loadings | "
         "PC2 top loadings |", "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for g, sm in maps.items():
        tops = ["; ".join(f"{m.removesuffix('_p90')} {c:+.2f}" for m, _, c in ld[:4])
                for ld in sm["loadings"]]
        L.append(f"| {g} | {len(sm['rows'])} | {len(sm['partial'])} | {sm['evr'][0]*100:.1f}% | "
                 f"{sm['evr'][1]*100:.1f}% | {(sm['evr'][0]+sm['evr'][1])*100:.1f}% | {tops[0]} | "
                 f"{tops[1]} |")
    L += ["", "### Axis names, checked against the loadings", "",
          "Names read as the map does: PC1 left → right, PC2 bottom → top (user, 2026-10-07). "
          "Never generated. Each proposed name was checked against the axis's three strongest "
          "loadings at each end (registry display names), not only the players at the "
          f"extremes. An end with no loading of at least {page_mod.END_MIN_LOADING} (below the "
          "1/√k = 0.20–0.25 of an even spread over the k = 16–24 metrics) has nothing to name. Every loading quoted "
          "in a verdict or in the legend is checked on each run (QUOTED_LOADINGS); a mismatch "
          "fails it.", "",
          "| Group | Axis | Name | + end (top 3) | − end (top 3) | Check |",
          "| --- | --- | --- | --- | --- | --- |"]
    for g, sm in maps.items():
        for a in (0, 1):
            nm = AXIS_NAMES[g][a]
            cs = list(zip(sm["model"]["components"][a], sm["model"]["metrics"]))
            pos = sorted(cs, key=lambda t: (-t[0], t[1]))[:3]
            neg = sorted(cs, key=lambda t: (t[0], t[1]))[:3]
            fmt_end = lambda e: "; ".join(f"{labels[m]} {c:+.2f}" for c, m in e)  # noqa: E731
            L.append(f"| {g} | PC{a + 1} | {page_mod.axis_title(nm) if nm else '(unnamed)'} | "
                     f"{fmt_end(pos)} | {fmt_end(neg)} | {AXIS_VERDICTS[(g, a)]} |")
    L += ["", "**Display rule for naming an axis end (not pre-registered).** An end is "
          f"described by its loadings of at least {page_mod.END_MIN_LOADING} (unrounded). The "
          "value was chosen after seeing the loadings, to keep the CB lower end from being "
          "named by long pass completion (−0.11) against the user's decision. Rationale: a "
          "loading spread evenly over k metrics is 1/√k, 0.20–0.25 for these groups (k = 16–24 "
          "DNA metrics), so a smaller one does not define an end. It changes only page text, never a score. "
          "Legend changes against the earlier 0.10 (computed):", ""]
    L += legend_changes(maps, labels)
    L += ["",
          "**Page (display only; user, 2026-10-07):** zoom, pan and pinch with a reset; name "
          "labels when zoomed in; search that zooms to the player; points coloured by fine role "
          "(FB-WB, DM-CM, AM-Winger; mixed rows only in CB and ST) with a toggle per role; an "
          "explainer panel; hover cards in plain words with registry display names (read from "
          "`registry.py` as source text, not imported). Each card says where the player leans "
          "on each axis (|z| < 0.5 near the middle, < 1.5 slight, else strong) and gives the "
          "three largest pushes as sentences (push = loading × (weighted value − group mean); "
          "all pushes sum to the score). Every number in a card names its source and is "
          "re-derived from the parquets after the page is written; a mismatch fails the run.",
          "",
          "**Compare mode (M4g1b, display only; user, 2026-10-07):** click two players of the "
          "group: their M4f pair distance (withheld families' metrics included, as in M4f), "
          "each one's rank in the other's neighbour order (partial rows counted, own rows left "
          "out, equal distances within 1e-12 share a rank), the 5 metrics with the largest and "
          "the 3 with the smallest local-percentile gap, and the group's withheld family scores "
          "in words. Facts are computed in Python (`spikes/m4g_compare.py`); the check "
          "re-derives them from `player_dna.parquet` and runs the page's own renderer in node "
          "for every ordered pair against the Python rendering.",
          "", "### DM-CM: 6 / 8 label", ""]
    L += label_table(f68)
    d = f68["pairs"][("6", "8")]
    L += ["", f"- Cohen's d (6 − 8): PC1 {d['d'][0]:+.2f}, PC2 {d['d'][1]:+.2f}.",
          f"- Overlap coefficient ({BINS} bins): PC1 {d['ovl'][0]:.2f}, PC2 {d['ovl'][1]:.2f}.",
          "",
          "**Pre-registered reading** (ADR 004 §13.4): separate regions iff lift ≥ "
          f"{LIFT_BAR} for both 6 and 8, and |d| ≥ {D_BAR} on PC1 or PC2.", "",
          f"- lift ≥ {LIFT_BAR} for both: **{'yes' if read68['lift_ok'] else 'no'}**;",
          f"- |d| ≥ {D_BAR} on PC1 or PC2: **{'yes' if read68['d_ok'] else 'no'}**;",
          f"- **6s and 8s form separate regions: {'YES' if read68['separate'] else 'NO'}.**", "",
          "### DM-CM: pivot label (reported, not the decision rule)", ""]
    L += label_table(fpv)
    L += ["", "| Pair | d PC1 | d PC2 | Overlap PC1 | Overlap PC2 | Both lifts ≥ 0.4 | Any |d| ≥ 1 |",
          "| --- | ---: | ---: | ---: | ---: | --- | --- |"]
    for (a, b), v in fpv["pairs"].items():
        r = readpv[(a, b)]
        L.append(f"| {a} − {b} | {v['d'][0]:+.2f} | {v['d'][1]:+.2f} | {v['ovl'][0]:.2f} | "
                 f"{v['ovl'][1]:.2f} | {'yes' if r['lift_ok'] else 'no'} | "
                 f"{'yes' if r['d_ok'] else 'no'} |")
    L += ["", "### Within-team contrast", "",
          f"DM-CM teammate pairs (same team and league, both plotted) whose 6-share differs by ≥ "
          f"{CONTRAST_GAP}: **{wt['pairs']}** pairs. The more-6 player sits on the 6 side of the "
          f"6 − 8 mean difference in **{wt['agree'][0]} of {wt['pairs']}** pairs on PC1 and "
          f"**{wt['agree'][1]} of {wt['pairs']}** on PC2 (chance: half). Formation is mostly the "
          "same within a team-season, so this contrast largely holds it fixed.", "",
          "### Face-validity rows", "",
          "| Player | 6-share | 6 / 8 | Pivot | Neighbours (6 / 8 / mixed) | Neighbour labels |",
          "| --- | ---: | --- | --- | --- | --- |"]
    by_short = {FACE.get((r["name"], r["key"][1])): r for r in dm_all}
    for short in DMCM_FACE:
        r = by_short.get(short)
        if r is None:
            fail(f"face-validity row {short} not in DM-CM")
        lb = lab[r["key"]]
        nb = nbrs[r["key"]]
        cnt = Counter(lab[b["key"]]["label"] for b in nb)
        names = ", ".join(f"{b['name']} ({lab[b['key']]['label']})" for b in nb)
        L.append(f"| {short} | {lb['share6']:.2f} | {lb['label']} | {lb['pivot']} | "
                 f"{cnt['6']} / {cnt['8']} / {cnt['mixed']} | {names} |")
    L += ["", "### Decision", "", SPLIT_DECISION, ""]
    return "\n".join(L) + "\n"


def label_table(fig: dict) -> list[str]:
    L = ["| Label | Rows | Plotted | PC1 mean ± SD | PC2 mean ± SD | b | Purity | Lift |",
         "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for lbl, v in fig["labels"].items():
        pc = [f"{p[0]:+.3f} ± {p[1]:.3f}" if p else "–" for p in v["pc"]]
        L.append(f"| {lbl} | {v['n_all']} | {v['n_plotted']} | {pc[0]} | {pc[1]} | {v['b']:.2f} | "
                 f"{fmt(v['purity'])} | {fmt(v['lift'])} |")
    return L


# Compare-mode panels printed on each run (user, 2026-10-07): (A, B) by FACE short label or
# player_name, DM-CM.
SHOW_PAIRS = (("Busquets", "Kanté"), ("Michael Carrick", "Busquets"))


def show_pairs(path: Path, sm: dict, order: list[int]) -> None:
    data = json.loads(path.read_text().split('<script type="application/json" id="pts">', 1)[1]
                      .split("</script>", 1)[0])
    names = [FACE.get((sm["rows"][i]["name"], sm["rows"][i]["key"][1])) or sm["rows"][i]["name"]
             for i in order]
    for a, b in SHOW_PAIRS:
        if names.count(a) != 1 or names.count(b) != 1:
            fail(f"compare pair {a} / {b}: not exactly one plotted DM-CM row each")
        i, j = names.index(a), names.index(b)
        heads, rest = cmp_mod.render(data, i, j)
        print(f"\n── Compare panel: {a} (A) vs {b} (B) ──\n{cmp_mod.as_text(heads + rest)}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--review", type=Path, default=REVIEW)
    args = ap.parse_args()
    labels = cm.registry_labels()
    by_group, meta, _ = wc.load()
    missing = sorted({m for g in wc.GROUPS for m in ref.DNA[g]} - set(labels))
    if missing:
        fail(f"no registry display name for {missing}")
    maps = {g: style_map(g, by_group[g]) for g in wc.GROUPS}
    check_quoted(maps)
    all_rows = [r for g in wc.GROUPS for r in by_group[g]]
    mins = cm.position_minutes({r["key"] for r in all_rows})
    season = pl.read_parquet(SEASON_PARQUET).select(*KEY, "player_name", "team_name",
                                                     "minutes_played")
    ident = {tuple(r[c] for c in KEY): r for r in season.iter_rows(named=True)}
    roles = {r["key"]: cm.fine_role(mins.get(r["key"][:3], Counter()),
                                    ident[r["key"]]["minutes_played"], r["key"])
             for r in all_rows}
    facts = pool_facts(roles, wc.LEAGUES)
    dm_all = by_group["DM-CM"]
    lab = dmcm_labels(dm_all, mins)
    nbrs = neighbours(dm_all)
    f68 = label_figures(maps["DM-CM"], dm_all, lab, "label", LABELS_68, nbrs)
    fpv = label_figures(maps["DM-CM"], dm_all, lab, "pivot", LABELS_PIVOT, nbrs)
    read68 = reading(f68, "6", "8")
    readpv = {p: reading(fpv, *p) for p in fpv["pairs"]}
    wt = within_team(maps["DM-CM"], lab)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    page_of = {g: f"style_map_{g.lower().replace('-', '_')}.html" for g in maps}
    for g, sm in maps.items():
        is_dm = g == "DM-CM"
        order = sorted(range(len(sm["rows"])), key=lambda i: sm["rows"][i]["key"])
        cmp_data = cmp_mod.build(g, [sm["rows"][i] for i in order], by_group[g], labels, sm["evr"])
        others = [[r["name"], g2, page_of[g2]] for g2 in maps if g2 != g for r in by_group[g2]]
        ctx = {"sm": sm, "names": AXIS_NAMES[g], "labels": labels, "ident": ident,
               "roles": roles, "lab": lab if is_dm else None, "face": FACE,
               "leagues": wc.LEAGUES, "meta": meta, "facts": facts,
               "notes": {a: AXIS_NOTES[(g, a)] for a in (0, 1) if (g, a) in AXIS_NOTES},
               "cmp": cmp_data, "cmp_js": cmp_mod.JS, "others": others}
        path = args.out_dir / page_of[g]
        path.write_text(page_mod.page(ctx, dmcm_figs_html(f68) if is_dm else ""))
        n = page_mod.check_page(path, g, metric_weights(g), wc.DNA_PARQUET, SEASON_PARQUET, KEY,
                                wc.LEAGUES, labels, AXIS_NAMES[g], roles,
                                lab if is_dm else None)
        n_cmp = cmp_mod.check_page(path, g, wc.DNA_PARQUET, KEY, metric_weights(g), labels,
                                   sm["evr"])
        print(f"{g}: PC1 {sm['evr'][0]:.3f} PC2 {sm['evr'][1]:.3f}, {n} card values traced, "
              f"{n_cmp} compare panels checked → {path} ({path.stat().st_size:,} bytes)")
        if is_dm:
            show_pairs(path, sm, order)
    body = review(maps, lab, f68, fpv, read68, readpv, wt, nbrs, dm_all, meta, labels)
    review_sections.write_section(args.review, "m4g_style_map", body)
    print(f"6 / 8 separate regions: {read68} → {args.review} (section m4g_style_map)")


if __name__ == "__main__":
    main()
