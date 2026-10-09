"""Compare mode on the style maps (plan 005 M4g1b): pair facts, the panel and its check.

Display only: no change to the PCA, percentiles, DNA or src/. Never imports eleveniq
(tests/test_spikes_independent.py).

Facts, computed here and written into the page (the page's JS only indexes and formats them):
- distance: the M4f pair distance (m4f_dna_reference.pair_distance), weighted squared Euclidean
  on local percentile / 100 over every DNA metric of the group. A withheld family (Defending
  everywhere, Passing outside ST; ADR 004 §12.6, §12.9) has no dimension score, but its metrics
  stay in the vector, so they count in the distance as they do in M4f. Shown to 4 decimals;
- ranks: B's place in A's M4f neighbour order: every other primary row of the group (partial
  rows included, rows of A's own player left out, as in M4f3), by distance; N is the length of
  that list. Rank = 1 + the number of candidates strictly closer, so candidates at the same
  distance (within TIE_TOL; percentiles are discrete, and exact ties occur, e.g. three CB rows)
  share a rank instead of being ordered by float noise. Two rows of one player are not ranked
  against each other;
- metrics: the 5 with the largest and the 3 with the smallest gap in local percentile points,
  ties by metric name (the group's metric list is in name order);
- percentile phrase: m4g_style_page.pct_words, the hover cards' function (neutral band 40–60
  "around average"); the page stores each distinct phrase once and an index per cell;
- withheld families: a metric whose family score is withheld for the group is tagged
  "(<Family> score withheld: <reason>)" (it speaks about the family score, not the metric); when a Defending metric is listed, one sentence follows the
  lists (defending_note: built from the group's Defending withheld code, ADR 004 §14 N2 / N3).

Check (check_page), and what in it is independent of the page build:
- independent: the distances and the candidate sets are recomputed from player_dna.parquet with
  separate code (numpy on the weighted percentile vectors for complete pairs; pair_distance
  only for the partial rows), not from the page's data or from build();
- shared with the build: ranks, picks, phrases (cell_words, i.e. pct_words), statics and
  dist_code are applied to those recomputed inputs by the same helpers the build uses, so a bug
  in a helper would sit on both sides. The helpers are covered by unit tests with hand-computed
  values instead (tests/test_m4g_compare.py);
- the page's own JS renderer (cmpHTML) runs in node for every ordered pair, and each panel is
  compared (sha1) with the Python renderer here (render), which reads the page's data;
- numbers in a panel sit in <span class="n">; any other digit outside the header (traced by
  m4g_style_page.check_page) fails the run.
"""
from __future__ import annotations

import bisect
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_dna_reference as ref  # noqa: E402  (sibling spike: DNA lists, WITHHELD, pair_distance)
import m4g_common as cm  # noqa: E402
import m4g_style_page as page_mod  # noqa: E402  (GROUP_PLURAL; it does not import this module)

N_DIFF, N_ALIKE = 5, 3
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
TIE_TOL = 1e-12
# Withheld score reasons in words (export/words.WITHHELD_TEXT, written out: no eleveniq;
# tests/test_m4g_compare.py checks they are equal). The Defending D5 reasons (ADR 004 §13.8) are
# built from two parts so that the words and the note sentence cannot drift apart.
POSSESSION_WORDS = "not shown to be free of team-possession effects"        # ADR 004 §14 N2
MOVER_TEST = "player-vs-team test on club movers"
MOVER_OUTCOME = {"mover_stability_failed": "failed",
                 "mover_stability_inconclusive": "inconclusive",
                 "mover_seed_sensitive": "not stable across seeds",
                 "mover_below_model_range": "outside the model's range"}
UNTESTABLE_WORDS = "too few club movers to test player vs team"
WITHHELD_WORDS = {"pending_xpass_deferred": ("rests on long pass completion alone, which is not "
                                            "shown to follow the player across clubs; xPass "
                                            "deferred until after launch"),
                  "possession_criteria_failed": POSSESSION_WORDS,
                  **{c: f"{MOVER_TEST} {o}" for c, o in MOVER_OUTCOME.items()},
                  "mover_stability_untestable": UNTESTABLE_WORDS}
# The tag's reason per withheld status (export/words.TAG_REASON): Passing's describes the family
# score (W1, user decision C as corrected by the stats-reviewer, 2026-10-09); every Defending
# status uses its withheld words exactly (ADR 004 §14 N2; user 2026-10-09).
TAG_WORDS = {"pending_xpass_deferred": "rests on long pass completion alone",
             **{c: w for c, w in WITHHELD_WORDS.items() if c != "pending_xpass_deferred"}}
# The Defending note's clause per withheld code (the same parts as WITHHELD_WORDS), with a verb
# per mover outcome.
MOVER_VERB = {"mover_stability_failed": "has failed", "mover_stability_inconclusive": "is inconclusive",
              "mover_seed_sensitive": "is not stable across seeds",
              "mover_below_model_range": "is outside the model's range"}
DEFENDING_CLAUSE = {"possession_criteria_failed": f"these metrics are {POSSESSION_WORDS}",
                    **{c: f"the {MOVER_TEST} {v}" for c, v in MOVER_VERB.items()},
                    "mover_stability_untestable": f"there are {UNTESTABLE_WORDS}"}
# The note's closing sentence (ADR 004 §14 item 5, N3 narrowed by the user 2026-10-09): the
# second-season clause applies where the mover test decides (it is underpowered at one season,
# DM-CM in 2015/16); the possession-failed groups only say it is not a finding about the player.
NOT_A_FINDING = "This is not a finding about the player."
EXPECTED_STATE = ("That is the expected state until a second season adds evidence, not a finding "
                  "about the player.")


def defending_note(group: str) -> str | None:
    """The sentence after a list with a Defending metric, from the group's Defending withheld
    code (ADR 004 §14 N2, N3 as narrowed to the mover-test groups; user 2026-10-09). None when
    Defending is not withheld."""
    code = ref.WITHHELD.get("Defending", {}).get(group)
    if code is None:
        return None
    under = " (underpowered)" if group in ref.UNDERPOWERED else ""
    closing = NOT_A_FINDING if code == "possession_criteria_failed" else EXPECTED_STATE
    return f"Defending scores are withheld: {DEFENDING_CLAUSE[code]}{under}. {closing}"
GROUP_ONE = {"CB": "centre-back", "FB-WB": "full-back or wing-back",
             "DM-CM": "central midfielder", "AM-Winger": "attacking midfielder or winger",
             "ST": "striker"}
HEAD_SPLIT = '<div class="ax">'

# The panel renderer. Mirrored line for line by render() below; check_page runs this code in
# node for every ordered pair and compares.
JS = r"""
function cmpEsc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
.replace(/"/g,'&quot;').replace(/'/g,'&#x27;')}
function cmpHTML(D,i,j){const C=D.cmp,n=D.names.length,M=C.lab.length;
const a=Math.min(i,j),b=Math.max(i,j),p=a*n-a*(a+1)/2+b-a-1;
const N=x=>'<span class="n">'+x+'</span>';
const nm=k=>cmpEsc(D.short[k]);
const poss=k=>cmpEsc(D.short[k]+(D.short[k].endsWith('s')?"'":"'s"));
const head=(k,t)=>'<div class="cmp-p"><span class="tag '+t.toLowerCase()+'">'+t+'</span>'
+D.tips[k].split('<div class="ax">')[0]+'</div>';
const ph=(k,m)=>N(C.pt[C.ph[k*M+m]]);
const rank=(x,y)=>'<li>'+nm(y)+' is '+poss(x)+' #'+N(C.r[x*n+y])+' most similar of '+N(C.N[x])+'.</li>';
const li=m=>'<li><b>'+C.lab[m]+'</b>'+C.tag[m]+' — '+nm(i)+': '+ph(i,m)+' · '+nm(j)+': '+ph(j,m)+'</li>';
const pk=[...C.pk.slice(8*p,8*p+8)].map(c=>parseInt(c,36));
return '<div class="cmp-h">'+head(i,'A')+head(j,'B')+'</div>'
+'<p><b>DNA distance '+N('0.'+String(C.d[p]).padStart(4,'0'))+'</b> '+C.st.dist+'</p>'
+(C.r[i*n+j]===0?'<p>'+C.st.same+'</p>':'<ul>'+rank(i,j)+rank(j,i)+'</ul>')
+C.st.warn
+'<h3>Where they differ most</h3><ul>'+pk.slice(0,5).map(li).join('')+'</ul>'
+'<h3>Where they are most alike</h3><ul>'+pk.slice(5).map(li).join('')+'</ul>'
+(pk.some(m=>C.def[m])?C.st.defn:'')+C.st.foot}
"""

NODE = r"""
const fs=require('fs'),crypto=require('crypto'),vm=require('vm');
const t=fs.readFileSync(process.argv[2],'utf8');
const D=JSON.parse(t.split('<script type="application/json" id="pts">')[1].split('</script>')[0]);
const ctx={};vm.createContext(ctx);
vm.runInContext(t.split('<script id="cmpjs">')[1].split('</script>')[0],ctx);
const n=D.names.length;
if(process.argv[3]!==undefined){process.stdout.write(ctx.cmpHTML(D,+process.argv[3],+process.argv[4]))}
else{const out=[];for(let i=0;i<n;i++)for(let j=0;j<n;j++)if(i!==j)
out.push(crypto.createHash('sha1').update(ctx.cmpHTML(D,i,j),'utf8').digest('hex'));
process.stdout.write(out.join('\n'))}
"""


def esc(s: object) -> str:
    return html.escape(str(s))


def cell_words(group: str, pct: list[list[float]]) -> dict:
    """Phrase table (pct_words, each distinct phrase once), per-cell indices into it, and the
    per-metric withheld tag and Defending flag of the group."""
    metrics = ref.DNA[group]
    words = [page_mod.pct_words(v)[1] for row in pct for v in row]
    table = sorted(set(words))
    at = {w: i for i, w in enumerate(table)}
    held = {f: groups[group] for f, groups in ref.WITHHELD.items() if group in groups}
    fam = [ref.SPEC[m][0] for m in metrics]
    return {"pt": table, "ph": [at[w] for w in words],
            "tag": [f' <span class="muted">({esc(f)} score withheld: {esc(TAG_WORDS[held[f]])})</span>'
                    if f in held else "" for f in fam],
            "def": [f == "Defending" and f in held for f in fam]}


def pair_index(i: int, j: int, n: int) -> int:
    a, b = min(i, j), max(i, j)
    return a * n - a * (a + 1) // 2 + b - a - 1


def picks(pa: list[float], pb: list[float]) -> list[int]:
    """Metric indices: the N_DIFF largest gaps, then the N_ALIKE smallest (ties by index)."""
    gaps = [abs(x - y) for x, y in zip(pa, pb)]
    order = sorted(range(len(gaps)), key=lambda m: (-gaps[m], m))
    alike = sorted(range(len(gaps)), key=lambda m: (gaps[m], m))
    return order[:N_DIFF] + alike[:N_ALIKE]


def withheld_text(group: str) -> str:
    fams = [f"{f} ({WITHHELD_WORDS[groups[group]]}"
            f"{'; underpowered' if f == 'Defending' and group in ref.UNDERPOWERED else ''})"
            for f, groups in sorted(ref.WITHHELD.items()) if group in groups]
    if not fams:
        return "No family score is withheld for this group."
    return (f"Withheld family scores for {page_mod.GROUP_PLURAL[group]}: " + " and ".join(fams) + ". "
            "Withholding hides a family's score only: its metrics still count in the distance, "
            "the ranks and the map.")


def statics(group: str, n_metrics: int, map_share: int) -> dict[str, str]:
    n = lambda x: f'<span class="n">{x}</span>'  # noqa: E731
    plural = page_mod.GROUP_PLURAL[group]
    return {
        "dist": (f'<span class="muted">({n(0)} = identical profiles; the distance the neighbour '
                 f'lists use, over all {n(n_metrics)} DNA metrics with each family weighted '
                 'equally)</span>'),
        "same": ("Two rows of the same player (he changed club): not ranked against each other, "
                 "because neighbour lists leave out a player's own rows."),
        "warn": (f'<p class="warn">Distance on the map can mislead: the map shows about '
                 f'{n(f"{map_share}%")} of the style differences, so two players can look close '
                 'there and still differ, or look far apart and be neighbours. The distance and '
                 'ranks above use the full profile.</p>'),
        "defn": f'<p>{esc(note)}</p>' if (note := defending_note(group)) else "",
        "foot": (f'<p class="muted">Metrics are ordered by the gap between the two league '
                 f'percentiles (ties by metric name). Each percentile places a player among the '
                 f'{esc(plural)} of his own league. A rank counts every other '
                 f'{esc(GROUP_ONE[group])} row in the pool, those not plotted included (a player who changed club appears '
                 "once per club), leaving out the player's own rows; players at the same distance share a rank."
                 f'</p><p class="muted">{esc(withheld_text(group))}</p>'),
    }


# ── Facts ────────────────────────────────────────────────────────────────────

def build(group: str, plotted: list[dict], all_rows: list[dict], labels: dict[str, str],
          evr: list[float]) -> dict:
    """The page data for compare mode. `plotted` is in the page's display order."""
    metrics = ref.DNA[group]
    n = len(plotted)
    idx = {r["key"]: i for i, r in enumerate(plotted)}
    dist: dict[tuple[int, int], float] = {}
    rank = [0] * (n * n)
    big_n = [0] * n
    for i, a in enumerate(plotted):
        cand = []
        for b in all_rows:
            if b["key"][0] == a["key"][0]:
                continue
            j = idx.get(b["key"])
            if j is not None and (min(i, j), max(i, j)) in dist:
                d = dist[(min(i, j), max(i, j))]
            else:
                d = ref.pair_distance(a, b)
                if j is not None:
                    dist[(min(i, j), max(i, j))] = d
            cand.append((d, b["key"]))
        big_n[i] = len(cand)
        for k, rk in ranks(cand).items():
            if k in idx:
                rank[i * n + idx[k]] = rk
    d_codes, pk = [], []
    pct = [[r["cells"][m]["pct_local"] for m in metrics] for r in plotted]
    for i in range(n):
        for j in range(i + 1, n):
            d = dist.get((i, j))
            if d is None:                       # two rows of one player
                d = ref.pair_distance(plotted[i], plotted[j])
            d_codes.append(dist_code(d))
            pk.append("".join(B36[m] for m in picks(pct[i], pct[j])))
    return {"d": d_codes, "r": rank, "N": big_n, "pk": "".join(pk),
            **cell_words(group, pct),
            "lab": [esc(labels[m]) for m in metrics],
            "st": statics(group, len(metrics), round((evr[0] + evr[1]) * 100)),
            "one": GROUP_ONE[group]}


def ranks(cand: list[tuple[float, tuple]]) -> dict[tuple, int]:
    """Competition ranks: 1 + the number of candidates closer by more than TIE_TOL."""
    ds = sorted(d for d, _ in cand)
    return {k: bisect.bisect_left(ds, d - TIE_TOL) + 1 for d, k in cand}


def dist_code(d: float) -> int:
    """The 4 decimals of d, as an int (0.0412 → 412); the page writes "0." + 4 digits.

    d < 1 always: the pair weights sum to 1 and each term ((pA − pB) / 100)² is below 1 because
    Hazen percentiles lie strictly inside (0, 100). A d that would print as 1.0000 or more (from
    0.99995) cannot be shown in this format and is rejected rather than shown wrong."""
    s = f"{d:.4f}"
    if not s.startswith("0."):
        cm.fail(f"distance {d} outside [0, 1)")
    return int(s[2:])


# ── Rendering (Python mirror of JS) ──────────────────────────────────────────

def render(data: dict, i: int, j: int) -> tuple[str, str]:
    """(headers, rest) of the panel for rows i, j, as cmpHTML builds it."""
    c = data["cmp"]
    n, m_count = len(data["names"]), len(c["lab"])
    p = pair_index(i, j, n)
    num = lambda x: f'<span class="n">{x}</span>'  # noqa: E731
    nm = lambda k: esc(data["short"][k])  # noqa: E731
    poss = lambda k: esc(data["short"][k] + ("'" if data["short"][k].endswith("s") else "'s"))  # noqa: E731

    def head(k: int, t: str) -> str:
        return (f'<div class="cmp-p"><span class="tag {t.lower()}">{t}</span>'
                + data["tips"][k].split(HEAD_SPLIT)[0] + "</div>")

    def ph(k: int, m: int) -> str:
        return num(c["pt"][c["ph"][k * m_count + m]])

    def rank(x: int, y: int) -> str:
        return (f"<li>{nm(y)} is {poss(x)} #{num(c['r'][x * n + y])} most similar of "
                f"{num(c['N'][x])}.</li>")

    def li(m: int) -> str:
        return f"<li><b>{c['lab'][m]}</b>{c['tag'][m]} — {nm(i)}: {ph(i, m)} · {nm(j)}: {ph(j, m)}</li>"

    pk = [int(ch, 36) for ch in c["pk"][8 * p:8 * p + 8]]
    heads = '<div class="cmp-h">' + head(i, "A") + head(j, "B") + "</div>"
    rest = (f'<p><b>DNA distance {num("0." + str(c["d"][p]).rjust(4, "0"))}</b> {c["st"]["dist"]}</p>'
            + (f'<p>{c["st"]["same"]}</p>' if c["r"][i * n + j] == 0
               else f"<ul>{rank(i, j)}{rank(j, i)}</ul>")
            + c["st"]["warn"]
            + "<h3>Where they differ most</h3><ul>" + "".join(li(m) for m in pk[:N_DIFF]) + "</ul>"
            + "<h3>Where they are most alike</h3><ul>" + "".join(li(m) for m in pk[N_DIFF:])
            + "</ul>" + (c["st"]["defn"] if any(c["def"][m] for m in pk) else "")
            + c["st"]["foot"])
    return heads, rest


def as_text(panel: str) -> str:
    """The panel as plain text (for the run log)."""
    t = re.sub(r"</(div|p|li|h3)>|<ul>", "\n", panel)
    t = re.sub(r"<li>", "  - ", t)
    t = re.sub(r'<span class="tag [ab]">([AB])</span>', r"[\1] ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n\s*\n+", "\n", html.unescape(t)).strip()


# ── Check ────────────────────────────────────────────────────────────────────

def _node(path: Path, *args: str) -> str:
    try:
        out = subprocess.run(["node", "-", str(path), *args], input=NODE, capture_output=True,
                             text=True, check=False)
    except FileNotFoundError:
        cm.fail("node is needed to check the compare panel (the page's own JS is run)")
        raise
    if out.returncode != 0:
        cm.fail(f"node: {out.stderr[:500]}")
    return out.stdout


def check_page(path: Path, group: str, dna_parquet: Path, key_cols: tuple[str, ...],
               weights: dict[str, float], labels: dict[str, str], evr: list[float]) -> int:
    """Re-derive every compare fact from the parquet, then compare every panel the page's JS
    renders with render(). Returns the number of ordered pairs checked."""
    text = path.read_text()
    data = json.loads(text.split('<script type="application/json" id="pts">', 1)[1]
                      .split("</script>", 1)[0])
    c = data["cmp"]
    metrics = ref.DNA[group]
    if data["model"]["metrics"] != list(metrics):
        cm.fail(f"{path.name}: model metrics differ from the DNA list")
    # Every primary row of the group, from the parquet.
    rows: dict[tuple, dict] = {}
    for r in (pl.read_parquet(dna_parquet).filter(pl.col("position_group") == group)
              .iter_rows(named=True)):
        k = tuple(r[col] for col in key_cols)
        row = rows.setdefault(k, {"key": k, "group": group, "dna_status": r["dna_status"],
                                  "cells": {}})
        row["cells"][r["metric"]] = {"pct_local": r["pct_local"],
                                     "metric_status": r["metric_status"]}
    by_ref = {"|".join(str(v) for v in k): k for k in rows}
    keys = [by_ref[k] for k in data["keys"]]
    n = len(keys)
    if any(rows[k]["dna_status"] != "complete" for k in keys):
        cm.fail(f"{path.name}: a plotted row is not complete")
    pos = {k: i for i, k in enumerate(keys)}
    p = np.array([[rows[k]["cells"][m]["pct_local"] for m in metrics] for k in keys]) / 100.0
    w = np.array([weights[m] for m in metrics])
    d_plot = (((p[:, None, :] - p[None, :, :]) ** 2) * w).sum(axis=2)   # complete pairs
    others = [k for k in sorted(rows) if k not in pos]
    if any(rows[k]["dna_status"] == "complete" for k in others):
        cm.fail(f"{path.name}: a complete row is not plotted")
    want_n, want_r = [0] * n, [0] * (n * n)
    for i, a in enumerate(keys):
        cand = [(float(d_plot[i, j]), b) for j, b in enumerate(keys) if b[0] != a[0]]
        cand += [(ref.pair_distance(rows[a], rows[b]), b) for b in others if b[0] != a[0]]
        want_n[i] = len(cand)
        for b, rk in ranks(cand).items():
            if b in pos:
                want_r[i * n + pos[b]] = rk
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i != j and (want_r[i * n + j] == 0) != (a[0] == b[0]):
                cm.fail(f"{path.name}: rank 0 for {a}, {b} but not one player (or vice versa)")
    want_d, want_pk = [], []
    pct = (p * 100.0).tolist()
    for i in range(n):
        for j in range(i + 1, n):
            want_d.append(dist_code(float(d_plot[i, j])))
            want_pk.append("".join(B36[m] for m in picks(pct[i], pct[j])))
    want = {"d": want_d, "r": want_r, "N": want_n, "pk": "".join(want_pk),
            **cell_words(group, pct),
            "lab": [esc(labels[m]) for m in metrics],
            "st": statics(group, len(metrics), round((evr[0] + evr[1]) * 100))}
    for field, v in want.items():
        if c[field] != v:
            cm.fail(f"{path.name}: compare data {field!r} differs from the parquet")
    # Every panel: the page's JS (node) against the Python mirror; digits only in .n spans.
    static = sorted(tuple(labels.values()), key=len, reverse=True)
    hashes = _node(path).split("\n")
    if len(hashes) != n * (n - 1):
        cm.fail(f"{path.name}: node rendered {len(hashes)} panels, expected {n * (n - 1)}")
    h = 0
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            heads, rest = render(data, i, j)
            loose = html.unescape(re.sub(r"<[^>]+>", "",
                                         re.sub(r'<span class="n">[^<]*</span>', "", rest)))
            for s_ in static:
                loose = loose.replace(s_, "")
            if re.search(r"\d", loose):
                cm.fail(f"{path.name}: digits outside traced spans in panel {i}, {j}: {loose[:300]}")
            if hashlib.sha1((heads + rest).encode()).hexdigest() != hashes[h]:
                got = _node(path, str(i), str(j))
                cm.fail(f"{path.name}: panel {i}, {j} differs.\nJS:     {got[:600]}\n"
                        f"Python: {(heads + rest)[:600]}")
            h += 1
    return h
