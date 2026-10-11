"""
Reference check of the web export (plan 006 W1d; ADR 005 §1, §7): re-derives the exported
values from the parquets and the raw files with the M4 spikes, never from eleveniq (tests/
test_spikes_independent.py), and compares them with an export directory.

  .venv/bin/python -m eleveniq.export.build_export --out export/v1
  .venv/bin/python spikes/w1_export_reference.py --check-output export/v1
  .venv/bin/python spikes/w1_export_reference.py --check-output tests/fixtures/web

What is re-derived, and from where (independent of src/ unless noted):
- identity: names, team, minutes, nominal minutes and the gate flag from the season parquet;
  the display name (nickname, stripped); the near-gate rule restated here (CB / FB-WB, 900 ≤
  nominal < 1,350); the fine role and DM-CM slot from the raw lineups (m4g_common,
  m4g_style_map.dmcm_labels);
- metric row facts: value, local / global percentile, n, status and basis from the
  percentiles parquet; the percentile text and phrase with this module's own format_pct and
  m4g_style_page.pct_words; a zero tie only on a zero value with a stored percentile;
- DNA: the vector (pct, weight, status) and the dimension rows from the DNA parquets;
- neighbours: the M4f distance (m4f_dna_reference.pair_distance) over every other player's
  row of the group, competition ranks (m4g_compare.ranks), the first 10 by (distance, key),
  the 2 most different and 3 most alike metrics with their phrases;
- style maps: the M4g1 fit (m4g_style_map.style_map), coordinates, lean words and the top-3
  pushes (m4g_style_page), fine roles, partial rows, the movers (m4g_movers_display: stints,
  months, positions, weights, combined dot, distances, ranks both ways, changed-most);
- search index: one entry per season row of the checked files, the accent-folded key.
Not re-derived here (registry-derived, checked by tests on src/): the group metadata files
(notes, reasons, gap eligibility, tags) and the colour bin of non-DNA metrics.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_weighting_checks as wc  # noqa: E402  (DNA rows, leagues)
import m4g_common as cm  # noqa: E402  (position minutes, fine roles, registry labels)
import m4g_compare as cmpm  # noqa: E402  (ranks, picks)
import m4g_movers_display as mv  # noqa: E402  (movers facts)
import m4g_style_map as smod  # noqa: E402  (the fit, axis names, DM-CM labels)
import m4g_style_page as page_mod  # noqa: E402  (pct_words, lean, contributions, end_name)

ref = wc.ref
KEY = ref.KEY
ROOT = ref.ROOT
SEASON = ROOT / "data" / "processed" / "player_season_stats.parquet"
PCT = ROOT / "data" / "processed" / "player_season_percentiles.parquet"
DIMS = ROOT / "data" / "processed" / "player_dna_dimensions.parquet"
# The export rounds floats to 6 decimals: half a unit (5e-7) is the largest honest
# difference, reached exactly on binary fractions such as 0.3828125 (= 49/128).
TOL = 5e-7 + 1e-12
NEAR_GATE = (("CB", "FB-WB"), 900.0 - 1e-6, 1350.0)    # user, 2026-10-09 (written out)
RESTARTS = "(corners, free kicks, throw-ins and other restarts)"
LABEL_OVERRIDES = {"restart_key_passes_p90": f"Restart key passes {RESTARTS}",
                   "restart_xg_assisted_p90": f"Restart xG assisted {RESTARTS}",
                   "restart_assists_p90": f"Restart assists {RESTARTS}"}
GROUP_TEXT = {**page_mod.GROUP_PLURAL, "GK": "goalkeepers"}
fail = cm.fail


class Mismatch:
    def __init__(self) -> None:
        self.n = 0
        self.errors: list[str] = []

    def eq(self, where: str, got, want) -> None:
        self.n += 1
        ok = (math.isclose(got, want, rel_tol=0, abs_tol=TOL)
              if isinstance(got, float) and isinstance(want, (int, float)) and not isinstance(want, bool)
              else got == want)
        if not ok:
            self.errors.append(f"{where}: export {got!r} ≠ reference {want!r}")


def registry_directions() -> dict[str, str]:
    """Metric column -> direction, parsed from the registry's SPECS as source text (never
    imported): _p90(base, name, family, direction), _ratio(...) is higher_better, MetricSpec(...)
    has a direction keyword."""
    import ast
    tree = ast.parse(cm.REGISTRY_SOURCE.read_text())
    specs = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                 and isinstance(n.target, ast.Name) and n.target.id == "SPECS")
    out: dict[str, str] = {}
    for call in specs.value.elts:                                      # type: ignore[union-attr]
        f = call.func.id
        kw = {k.arg: k.value for k in call.keywords}
        if f == "_p90":
            out[call.args[0].value + "_p90"] = call.args[3].value
        elif f == "_ratio":
            out[call.args[0].value] = "higher_better"
        elif f == "MetricSpec":
            out[kw["column"].value] = kw["direction"].value
        else:
            fail(f"registry SPECS: unknown builder {f}: update registry_directions")
    return out


DIRECTION = registry_directions()


def style_words(p: float) -> str:
    """W3 polish (user, 2026-10-10): a style metric's phrase, written out (no eleveniq)."""
    if 40.0 <= p <= 60.0:
        return "around average"
    return "higher than most" if p > 60.0 else "lower than most"


# The display round's bands (user, 2026-10-10), written out (no eleveniq).
BANDS = ((95.0, "Top 5%"), (90.0, "Top 10%"), (75.0, "Top 25%"),
         (60.0, "Above average"), (40.0, "Around average"), (25.0, "Below average"),
         (10.0, "Bottom 25%"), (5.0, "Bottom 10%"), (0.0, "Bottom 5%"))


def whole(p: float) -> int:
    """The whole number a percentile is shown as (final text pass: the band reads it)."""
    return int(p) if p in (0, 100) else min(99, max(1, math.floor(p + 0.5)))


def band(shown: float, neutral: bool, n_pop: int | None = None, flipped: bool = False) -> str:
    """No Top 1% (user, 2026-10-10): the single highest / lowest of the league population reads
    Highest / Lowest in league, naming the raw value (flipped for lower-better)."""
    if n_pop and 0 < shown < 100:          # ranked members only (outsider rows: 0 / 100)
        top, bottom = ("Lowest in league", "Highest in league") if flipped else \
            ("Highest in league", "Lowest in league")
        if shown >= 100.0 * (n_pop - 0.5) / n_pop - 1e-6:
            return top
        if shown <= 100.0 * 0.5 / n_pop + 1e-6:
            return bottom
    n = whole(shown)
    if neutral:
        return style_words(n).capitalize()
    return next(label for lo, label in BANDS if n >= lo)


# Hollow bar = neutral words (user, 2026-10-10). Which cells are hollow is registry-derived
# (style, long pass completion, team_style_biased): read from the export's group files, the
# input this check takes as given (see "Not re-derived" above).
def hollow_of(out: Path) -> dict[tuple[str, str], bool]:
    return {(g.stem, m["metric"]): m["uncoloured"] for g in (out / "groups").glob("*.json")
            for m in json.loads(g.read_text())["metrics"]}


def words(p: float, neutral: bool) -> str:
    return style_words(p) if neutral else page_mod.pct_words(p)[1]


# Completion above expected (plan 006 pre-registration), written out (no eleveniq): buckets,
# cohort, rates, Hazen league percentile, the possession check, the spec choice.
PASS_MIX = ROOT / "data" / "processed" / "player_season_pass_mix.parquet"
CVE_MIN = {"CB": 57, "FB-WB": 84, "DM-CM": 68, "AM-Winger": 66, "ST": 93}   # pre-registered
CVE_GROUPS = tuple(CVE_MIN)
LENS, DIRS = ("short", "medium", "long"), ("forward", "sideways", "backward")


def cve_reference(season: dict) -> dict:
    mix = {tuple(r[c] for c in KEY): r for r in pl.read_parquet(PASS_MIX).iter_rows(named=True)}

    def cells(spec: int) -> dict[str, list[str]]:
        if spec == 1:
            return {ln: [f"{ln}_{d}" for d in DIRS] for ln in LENS}
        return {f"{ln}_{d}": [f"{ln}_{d}"] for ln in LENS for d in DIRS}

    def run(spec: int) -> tuple[dict, dict]:
        bk = cells(spec)
        cnt = {}
        for k in season:
            m = mix.get(k)
            cnt[k] = {b: ((sum(m[f"pm_{c}_att"] for c in cs), sum(m[f"pm_{c}_cmp"] for c in cs))
                          if m else (0, 0)) for b, cs in bk.items()}
        tot = {k: sum(a for a, _ in v.values()) for k, v in cnt.items()}

        def cohort(k):
            r = season[k]
            g = r["position_group"]
            return (g in CVE_MIN and r["in_percentile_pool"] and r["meets_min_minutes"]
                    and tot[k] >= CVE_MIN[g])
        pool: dict = {}
        for k in season:
            if cohort(k):
                lg = (season[k]["competition_id"], season[k]["position_group"])
                acc = pool.setdefault(lg, {b: [0, 0] for b in bk})
                for b, (a, c) in cnt[k].items():
                    acc[b][0] += a
                    acc[b][1] += c
        val = {}
        for k, r in season.items():
            lg = (r["competition_id"], r["position_group"])
            if lg not in pool or not r["in_percentile_pool"] or tot[k] == 0:
                continue
            rate = {b: (c / a if a else None) for b, (a, c) in pool[lg].items()}
            if any(a and rate[b] is None for b, (a, _) in cnt[k].items()):
                continue
            act = 100.0 * sum(c for _, c in cnt[k].values()) / tot[k]
            exp = 100.0 * sum(a / tot[k] * rate[b] for b, (a, _) in cnt[k].items() if a)
            val[k] = act - exp
        # Every row with a value shows it; only the cohort is ranked (pre-registration).
        rows: dict = {k: {"value": v, "pct": None} for k, v in val.items()}
        rhos: dict = {g: [] for g in CVE_GROUPS}
        for lg in pool:
            ks = [k for k in season if cohort(k) and k in val
                  and (season[k]["competition_id"], season[k]["position_group"]) == lg]
            n = len(ks)
            vs = sorted(val[k] for k in ks)
            for k in ks:
                lo = sum(1 for v in vs if v < val[k])
                eq = sum(1 for v in vs if v == val[k])
                rows[k] = {"value": val[k], "pct": round((lo + 0.5 * eq) / n * 100, 6)
                           if n >= 30 else None}
            pairs = []
            for k in ks:
                r = season[k]
                t, o = r["team_open_play_passes_while_on"], r["opponent_open_play_passes_while_on"]
                if t is not None and o is not None and t + o:
                    pairs.append((val[k], t / (t + o)))
            rhos[lg[1]].append(spearman(pairs))
        mean = {g: (sum(x) / len(x) if x and None not in x else None) for g, x in rhos.items()}
        return rows, mean

    rows1, mean1 = run(1)
    rows2, mean2 = run(2)
    spec = {}
    for g in CVE_GROUPS:
        if mean1[g] is not None and abs(mean1[g]) < 0.4:
            spec[g] = 1
        elif mean2[g] is not None and abs(mean2[g]) < 0.4:
            spec[g] = 2
        else:
            spec[g] = None
    return {"rows": {1: rows1, 2: rows2}, "spec": spec, "mean": {1: mean1, 2: mean2}}


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None

    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for t in range(i, j + 1):
                r[order[t]] = (i + j) / 2 + 1
            i = j + 1
        return r
    a, b = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((y - mb) ** 2 for y in b) ** 0.5
    return cov / (va * vb) if va and vb else None


def at6(p: float | None) -> float | None:
    """A percentile at the contract's precision (6 decimals): the export's texts, phrases and
    bins read the number the file carries."""
    return None if p is None else round(p, 6)


def format_pct(p: float) -> str:
    if p == 0:
        return "<1"
    if p == 100:
        return ">99"
    return str(min(99, max(1, math.floor(p + 0.5))))


def half_up(v: float) -> str:
    return str(math.floor(v + 0.5)) if not float(v).is_integer() else str(int(v))


def fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch)).casefold().strip()


def mins_of(ctx: dict, k: tuple) -> Counter:
    """A row's lineup minutes per position (missing data fails, never an empty default)."""
    if k[:3] not in ctx["mins"]:
        fail(f"{k}: no lineup position minutes")
    return ctx["mins"][k[:3]]


def label(metric: str, group: str, labels: dict[str, str]) -> str:
    if (group, metric) in smod.GROUP_LABELS:
        return smod.GROUP_LABELS[(group, metric)]
    return LABEL_OVERRIDES.get(metric, labels[metric])


def key_of(text: str) -> tuple:
    pid, tid, cid, sid = (int(x) for x in text.split("-"))
    return (pid, tid, cid, sid, "statsbomb")


# ── Players ──────────────────────────────────────────────────────────────────────

def check_players(out: Path, mm: Mismatch, ctx: dict) -> int:
    files = sorted((out / "players").glob("*.json"))
    order = list(ctx["labels"])                 # registry SPECS order
    for f in files:
        d = json.loads(f.read_text())
        k = key_of(f.stem)
        s = ctx["season"][k]
        i = d["identity"]
        w = f"{f.stem}"
        mm.eq(f"{w} name", i["name"], s["player_name"])
        nick = (s["player_nickname"] or "").strip()
        mm.eq(f"{w} display_name", i["display_name"], nick or s["player_name"])
        mm.eq(f"{w} team", i["team"], s["team_name"])
        mm.eq(f"{w} group", i["group"], s["position_group"])
        mm.eq(f"{w} group_text", i["group_text"], GROUP_TEXT[s["position_group"]])
        mm.eq(f"{w} minutes_text", i["minutes_text"], half_up(s["minutes_played"]))
        mm.eq(f"{w} nominal", i["nominal_minutes"], s["minutes_nominal_equiv"])
        mm.eq(f"{w} nominal_text", i["nominal_minutes_text"], half_up(s["minutes_nominal_equiv"]))
        mm.eq(f"{w} meets_gate", i["meets_gate"], bool(s["meets_min_minutes"]))
        groups, lo, hi = NEAR_GATE
        mm.eq(f"{w} near_gate", i["near_gate"], bool(s["meets_min_minutes"]) and
              s["position_group"] in groups and lo <= s["minutes_nominal_equiv"] < hi)
        if s["position_group"] != "GK":
            role = cm.fine_role(mins_of(ctx, k), s["minutes_played"], k)
            mm.eq(f"{w} role_text", i["role_text"], cm.role_words(role))
        if s["position_group"] == "DM-CM" and k in ctx["dmcm"]:
            lb = ctx["dmcm"][k]
            mm.eq(f"{w} slot", (i["dmcm_slot"]["label"], i["dmcm_slot"]["pivot"]),
                  (lb["label"], lb["pivot"]))
            mm.eq(f"{w} share6", i["dmcm_slot"]["share6"], lb["share6"])
        for role, cells in (("primary", d["metrics"]),
                            ("second", (d["second_group"] or {}).get("metrics", []))):
            if cells and len(cells) != len(order):
                fail(f"{w}: {len(cells)} {role} metric cells, the registry has {len(order)}")
            for m, c in zip(order, cells):
                r = ctx["pct"].get((k, role, m))
                if r is None:
                    fail(f"{w}: no {role} percentile row for {m}")
                mm.eq(f"{w} {role} {m} value", c["value"], r["value"])
                mm.eq(f"{w} {role} {m} basis", c["basis"], r["basis"])
                for scope in ("local", "global"):
                    e, p = c[scope], r[f"pct_{scope}"]
                    mm.eq(f"{w} {role} {m} {scope} n", e["n"], r[f"n_{scope}"])
                    mm.eq(f"{w} {role} {m} {scope} status", e["status"], r[f"status_{scope}"])
                    if r["registry_status"] == "signature":     # never a percentile (§11.5)
                        mm.eq(f"{w} {role} {m} {scope} signature", (e["pct"], e["bin"],
                              e["status_text"] is not None), (None, None, True))
                        continue
                    if c["zero_tie"]:
                        mm.eq(f"{w} {role} {m} zero tie", (r["value"], p is not None or
                                                          r[f"pct_{'global' if scope == 'local' else 'local'}"] is not None),
                              (0.0, True))
                        continue
                    if p is None:
                        mm.eq(f"{w} {role} {m} {scope} pct", e["pct"], None)
                        continue
                    if DIRECTION[m] == "lower_better":    # W0 decision 1: shown as 100 − p
                        p = 100.0 - p
                    mm.eq(f"{w} {role} {m} {scope} pct", e["pct"], p)
                    mm.eq(f"{w} {role} {m} {scope} pct_text", e["pct_text"], format_pct(at6(p)))
                    neutral = ctx["hollow"][(i["group"] if role == "primary" else
                                             d["second_group"]["group"], m)]
                    mm.eq(f"{w} {role} {m} {scope} phrase", e["phrase"], words(at6(p), neutral))
                    mm.eq(f"{w} {role} {m} {scope} band", e["band"],
                          band(at6(p), neutral, r["n_local"] if scope == "local" else None,
                               DIRECTION[m] == "lower_better"))
                    mm.eq(f"{w} {role} {m} {scope} text or number",
                          (e["pct_text"] is None) != (e["status_text"] is None), True)
        cv = ctx["cve"]
        if s["position_group"] in CVE_MIN:
            res = cv["rows"][cv["spec"][s["position_group"]] or 1].get(k)
            c = d["completion_vs_expected"]
            mm.eq(f"{w} cve value", c["value"], None if res is None else round(res["value"], 6))
            mm.eq(f"{w} cve pct", c["local"]["pct"], None if res is None else res["pct"])
        dna = ctx["dna_rows"].get(k)
        mm.eq(f"{w} has dna", d["dna"] is not None, dna is not None)
        if dna is not None and d["dna"] is not None:
            for v in d["dna"]["vector"]:
                cell = dna["cells"][v["metric"]]
                mm.eq(f"{w} dna {v['metric']} pct", v["pct"], cell["pct_local"])
                mm.eq(f"{w} dna {v['metric']} status", v["status"], cell["metric_status"])
            mm.eq(f"{w} dna metrics", sorted(v["metric"] for v in d["dna"]["vector"]),
                  list(ref.DNA[dna["group"]]))
            for x in d["dna"]["dimensions"]:
                if x["family"] == "Passing" and i["group"] != "GK":
                    cv = ctx["cve"]
                    g = i["group"]
                    res = cv["rows"][cv["spec"][g] or 1].get(k)
                    if cv["spec"][g] is not None:
                        want = (res["pct"], ["completion_vs_expected"])
                        mm.eq(f"{w} dim Passing (cve)", (x["score"], x["members"]), want)
                    else:
                        mm.eq(f"{w} dim Passing withheld", (x["score"], x["status"]),
                              (None, "pass_mix_possession_failed"))
                    continue
                r = ctx["dims"][(k, x["family"])]
                mm.eq(f"{w} dim {x['family']} score", x["score"], r["score"])
                mm.eq(f"{w} dim {x['family']} status", x["status"], r["score_status"])
                mm.eq(f"{w} dim {x['family']} members", x["members"], list(r["members"]))
            check_neighbours(w, k, dna, d["neighbours"], mm, ctx)
    return len(files)


def picks(a: dict, b: dict, metrics: tuple[str, ...]) -> list[str]:
    both = [m for m in metrics if a["cells"][m]["pct_local"] is not None
            and b["cells"][m]["pct_local"] is not None]
    # Ties within 1e-12 by metric name (float noise must not decide; pairs.TIE_TOL).
    gap = {m: round(abs(a["cells"][m]["pct_local"] - b["cells"][m]["pct_local"]) / 1e-12)
           for m in both}
    return (sorted(both, key=lambda m: (-gap[m], m))[:cmpm.N_DIFF]
            + sorted(both, key=lambda m: (gap[m], m))[:cmpm.N_ALIKE])


def check_neighbours(w: str, k: tuple, a: dict, nb: dict, mm: Mismatch, ctx: dict) -> None:
    g = a["group"]
    rows = ctx["by_group"][g]
    cand = [(ref.pair_distance(a, b), b["key"]) for b in rows if b["key"][0] != k[0]]
    rk = cmpm.ranks(cand)
    top = sorted(cand)[:10]
    mm.eq(f"{w} n_candidates", nb["n_candidates"], len(cand))
    mm.eq(f"{w} neighbour keys", [key_of(n["key"]) for n in nb["items"]], [b for _, b in top])
    by_key = {r["key"]: r for r in rows}
    for n, (dist, b) in zip(nb["items"], top):
        mm.eq(f"{w} → {n['key']} distance", n["distance"], dist)
        mm.eq(f"{w} → {n['key']} distance_text", n["distance_text"], f"{dist:.4f}")
        mm.eq(f"{w} → {n['key']} rank", n["rank"], rk[b])
        pk = picks(a, by_key[b], ref.DNA[g])
        for field, ms in (("most_different", pk[:2]), ("most_alike", pk[cmpm.N_DIFF:])):
            mm.eq(f"{w} → {n['key']} {field}", [x["metric"] for x in n[field]], ms)
            for x in n[field]:
                m = x["metric"]
                neutral = ctx["hollow"][(g, m)]

                def shown(row: dict) -> float:
                    p = row["cells"][m]["pct_local"]
                    return at6(100.0 - p if DIRECTION[m] == "lower_better" else p)
                mm.eq(f"{w} → {n['key']} {m} phrases", (x["a_phrase"], x["b_phrase"]),
                      (words(shown(a), neutral), words(shown(by_key[b]), neutral)))


# ── Style maps ───────────────────────────────────────────────────────────────────

def check_style_maps(out: Path, mm: Mismatch, ctx: dict) -> int:
    n = 0
    for f in sorted((out / "style-maps").glob("*.json")):
        d = json.loads(f.read_text())
        g = d["group"]
        sm = smod.style_map(g, ctx["by_group"][g])
        names = smod.AXIS_NAMES[g]
        labels = smod.labels_for(g, ctx["labels"])
        scores = {r["key"]: s for r, s in zip(sm["rows"], sm["scores"])}
        idx = {r["key"]: i for i, r in enumerate(sm["rows"])}
        mm.eq(f"{g} points", [key_of(p["key"]) for p in d["points"]], sorted(scores))
        mm.eq(f"{g} partial", [key_of(p["key"]) for p in d["partial"]],
              sorted(r["key"] for r in sm["partial"]))
        for a in (0, 1):
            mm.eq(f"{g} axis {a} variance", d["axes"][a]["variance_share"], sm["evr"][a])
        for p in d["points"]:
            k = key_of(p["key"])
            r = sm["rows"][idx[k]]
            pc = {m: r["cells"][m]["pct_local"] for m in sm["metrics"]}
            mm.eq(f"{g} {p['key']} x", p["x"], float(scores[k][0]))
            mm.eq(f"{g} {p['key']} y", p["y"], float(scores[k][1]))
            s = ctx["season"][k]
            mm.eq(f"{g} {p['key']} role", p["role_text"], cm.role_words(
                cm.fine_role(mins_of(ctx, k), s["minutes_played"], k)))
            for a in (0, 1):
                mm.eq(f"{g} {p['key']} lean {a}", p["lean"][a], page_mod.lean(
                    float(scores[k][a]), sm["sd"][a], names, sm["model"], labels, a))
                want = page_mod.contributions(sm["model"], pc, a)[:3]
                mm.eq(f"{g} {p['key']} pushes {a}", [x["metric"] for x in p["pushes"][a]],
                      [m for m, _ in want])
                for x, (m, c) in zip(p["pushes"][a], want):
                    adj, words = page_mod.pct_words(at6(pc[m]))
                    mm.eq(f"{g} {p['key']} push {m}", x["push"], c)
                    mm.eq(f"{g} {p['key']} push {m} words", (x["adjective"], x["phrase"], x["toward"]),
                          (adj, words, page_mod.end_name(names, sm["model"], labels, a,
                                                         1 if c > 0 else -1)))
            n += 1
        check_movers(g, d, sm, idx, mm, ctx)
    return n


def check_movers(g: str, d: dict, sm: dict, idx: dict, mm: Mismatch, ctx: dict) -> None:
    plotted = set(idx)
    want_movers = []
    for pk in sorted(ctx["multi"]):
        stints = ctx["multi"][pk]
        here = [st for st in stints if st["key"] in plotted]
        if not here:
            continue
        for st in here:
            lines = d["played_for"].get(f"{st['key'][0]}-{st['key'][1]}-{st['key'][2]}-{st['key'][3]}")
            if lines is None:
                mm.eq(f"{g} played_for {st['key']}", None, "present")
                continue
            mm.eq(f"{g} played_for {st['key']} stints", [key_of(x["key"]) for x in lines],
                  [x["key"] for x in stints])
            for x, s2 in zip(lines, stints):
                mm.eq(f"{g} played_for {x['key']} months", x["months"],
                      mv.month_range(s2["first"], s2["last"]))
                mm.eq(f"{g} played_for {x['key']} position", x["position_text"],
                      mv.position_words(s2["role"]))
                # Identity numbers round half-up (the contract's rule, as the comparison
                # page); the M4g3c page used ",.0f" (half to even), which differs on exact .5.
                mm.eq(f"{g} played_for {x['key']} minutes", x["minutes_text"],
                      half_up(s2["minutes"]))
        if len(here) >= 2:
            cb = mv.combined(sm, here, idx)
            pairs = []
            for p in range(len(here) - 1):
                a, b = sm["rows"][idx[here[p]["key"]]], sm["rows"][idx[here[p + 1]["key"]]]
                (rab, na), (rba, nb) = mv.rank_both(a, b, ctx["by_group"][g])
                pa = {m: a["cells"][m]["pct_local"] for m in sm["metrics"]}
                pb = {m: b["cells"][m]["pct_local"] for m in sm["metrics"]}
                pairs.append((ref.pair_distance(a, b), rab, na, rba, nb,
                              mv.changed_most(pa, pb, sm["metrics"])))
            want_movers.append(([st["key"] for st in here], cb, pairs))
    mm.eq(f"{g} movers", [[key_of(s) for s in m["stints"]] for m in d["movers"]],
          [k for k, _, _ in want_movers])
    if len(d["movers"]) != len(want_movers):
        fail(f"{g}: {len(d['movers'])} movers exported, {len(want_movers)} expected")
    for m, (_, cb, pairs) in zip(d["movers"], want_movers):
        if len(m["pairs"]) != len(pairs):
            fail(f"{g}: mover {m['player_id']} has {len(m['pairs'])} pairs, {len(pairs)} expected")
        mm.eq(f"{g} mover {m['player_id']} weights", m["weights"], [round(w, 6) for w in cb["weights"]])
        mm.eq(f"{g} mover {m['player_id']} x", m["x"], cb["score"][0])
        mm.eq(f"{g} mover {m['player_id']} y", m["y"], cb["score"][1])
        for e, (dist, rab, na, rba, nb, ch) in zip(m["pairs"], pairs):
            mm.eq(f"{g} mover {m['player_id']} distance", e["distance"], dist)
            mm.eq(f"{g} mover {m['player_id']} ranks", (e["rank_ab"], e["n_a"], e["rank_ba"], e["n_b"]),
                  (rab, na, rba, nb))
            mm.eq(f"{g} mover {m['player_id']} changed", [x["metric"] for x in e["changed"]], list(ch))


# ── Search index ─────────────────────────────────────────────────────────────────

def check_search(out: Path, mm: Mismatch, ctx: dict) -> int:
    d = json.loads((out / "search-index.json").read_text())
    players = {p.stem for p in (out / "players").glob("*.json")}
    mm.eq("search keys", sorted(e["key"] for e in d["entries"]), sorted(players))
    for e in d["entries"]:
        s = ctx["season"][key_of(e["key"])]
        disp = (s["player_nickname"] or "").strip() or s["player_name"]
        mm.eq(f"search {e['key']} name", (e["display_name"], e["name"]), (disp, s["player_name"]))
        mm.eq(f"search {e['key']} key", e["search_key"],
              " ".join(dict.fromkeys(fold(x) for x in (disp, s["player_name"]))))
    return len(d["entries"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check-output", type=Path, required=True, metavar="DIR")
    args = ap.parse_args()
    out = args.check_output
    by_group, meta, _ = wc.load()
    season = {tuple(r[c] for c in KEY): r for r in pl.read_parquet(SEASON).iter_rows(named=True)}
    pct = {(tuple(r[c] for c in KEY), r["cohort_role"], r["metric"]): r
           for r in pl.read_parquet(PCT).iter_rows(named=True)}
    dims = {(tuple(r[c] for c in KEY), r["family"]): r for r in pl.read_parquet(DIMS).iter_rows(named=True)}
    keys = set(season)
    mins = cm.position_minutes({k for k in keys if season[k]["position_group"] != "GK"}, mv.COMPS)
    ctx = {"by_group": by_group, "season": season, "pct": pct, "dims": dims, "mins": mins,
           "labels": cm.registry_labels(),
           "dna_rows": {r["key"]: r for rs in by_group.values() for r in rs},
           "dmcm": smod.dmcm_labels(by_group["DM-CM"], mins),
           "multi": mv.build_facts(SEASON, mv.MATCH_PARQUET),
           "hollow": hollow_of(out),
           "cve": cve_reference(season)}
    mm = Mismatch()
    n_p = check_players(out, mm, ctx)
    n_s = check_style_maps(out, mm, ctx)
    n_i = check_search(out, mm, ctx)
    if mm.errors:
        print("\n".join(mm.errors[:40]), file=sys.stderr)
        fail(f"{len(mm.errors)} of {mm.n} values differ from the reference")
    cv = ctx["cve"]
    print("completion above expected, mean within-league ρ (spec 1 / spec 2) and spec used: " +
          "; ".join(f"{g} {cv['mean'][1][g]:.3f} / {cv['mean'][2][g]:.3f} → {cv['spec'][g]}"
                    for g in CVE_GROUPS))
    print(f"OK: the export at {out} equals the reference: {mm.n:,} values over {n_p} player "
          f"files, {n_s} map points and {n_i} search entries (registry {meta['registry_version'][:12]}…)")


if __name__ == "__main__":
    main()
