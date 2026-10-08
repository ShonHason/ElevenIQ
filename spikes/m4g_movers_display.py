"""Movers on the style maps (plan 005 M4g3c, ADR 004 §13.7): display only.

No change to the PCA fit, percentiles, DNA, neighbours, cohorts or src/. Never imports eleveniq
(tests/test_spikes_independent.py).

What the page gets (facts computed here; the page's JS only shows, hides and positions them):
- "Played for", on the card of every plotted row whose player has 2+ stints in the season (any
  competition in the season parquet): each stint in date order (first match played) as
  team · league · minutes · month range · main positions in plain words (the fine-role rule of
  m4g_common: ≥ 0.7 of the stint's minutes → "mostly <role>", else "<role> / <role>", its two
  largest roles), and
  a note where the stint has no dot on this map:
  - another group's DNA row → "also on the <group> map" (a link to that page);
  - a partial DNA row of this group → "profile incomplete (not plotted)";
  - no DNA row → "no profile (under 900 min)": the gate counts nominal-equivalent minutes
    (ADR 004 D1), so a stint shown with 900+ played minutes reads "(under 900 min after
    stoppage time is scaled out)";
  - a PARTIAL competition (ADR 003) → "partial coverage — minutes undercounted" (user,
    2026-10-08).
- a combined dot per player-season with 2+ plotted (complete) rows in this group: the
  minutes-weighted average of the stints' map inputs (weights: nominal-equivalent minutes, the
  gate's clock), projected with the page's PCA. The projection is linear, so the dot is the
  same weighted average of the stint dots and lies on the segment between them. It is never
  part of the PCA fit (style_map fits complete rows only, before this module runs), neighbour
  lists, percentile populations or ranks. Its card names it "Season profile, weighted by
  minutes (display only)", shows "Played for" and where it sits on each axis, and carries no
  percentile wording (an average of local percentiles is not a percentile; user, 2026-10-08).
- the expanded panel (a click on the combined dot): minutes and weight per club, the M4f pair
  distance between consecutive stints (m4f_dna_reference.pair_distance, 4 decimals), and the
  3 metrics with the largest local-percentile change between them (ties by metric name), each
  phrased with m4g_style_page.pct_words against that stint's own league, with the compare
  panel's withheld-family tags and Defending note (m4g_compare).

Check (check_page), after the page is written:
- independently of the build: the stint dates come from the raw lineup files (a player with a
  lineup position in a match played in it), not from player_match_stats; minutes, team and
  stint sets from the season parquet; percentiles and statuses from player_dna.parquet; each
  combined score from the parquet cells and the model written into the page, and it must
  equal the weighted average of its stints' plotted scores (1e-9) and pixels (0.02 px);
- shared with the build (not independent): the position words, from the fine roles of this
  run's lineup minutes (as the existing cards' data-role values); a stint without a role fails;
- every number in a "Played for" block, combined card and panel sits in a data-mv span and is
  re-derived; a digit outside one fails (registry labels and the fixed wording excepted);
- every plotted row of a multi-stint player carries its block, and no row of a single-stint
  player does; every eligible player-season has exactly one combined dot.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4f_dna_reference as ref  # noqa: E402  (sibling spike: DNA lists, SPEC, pair_distance)
import m4g_common as cm  # noqa: E402
import m4g_compare as cmp_mod  # noqa: E402  (withheld tags, Defending note, dist_code)
import m4g_style_page as page_mod  # noqa: E402  (pct_words, lean, span, kref)
from reference_totals import DATA_ROOT, load_matches  # noqa: E402

MATCH_PARQUET = ref.ROOT / "data" / "interim" / "player_match_stats.parquet"
GATE = 900.0                     # config.MIN_MINUTES_THRESHOLD, nominal-equivalent (ADR 004 D1)
GATE_TOL = 1e-6                  # ADR 004 D1: minutes_nominal_equiv ≥ GATE − 1e-6
COMPS = (*cm.POOL, (9, 27))      # the season parquet's competitions: pool + Bundesliga
PARTIAL_COMPS = frozenset({9})   # config.COMPETITION_SEASONS coverage "PARTIAL" (ADR 003)
N_CHANGED = 3
SCORE_TOL, PX_TOL = 1e-9, 0.02
LABEL = "Season profile, weighted by minutes (display only)"
# Short role nouns for "Played for" (the card header keeps cm.ROLE_WORDS).
POSITION_WORDS = {"CB": "centre-back", "LB": "left-back", "RB": "right-back",
                  "LWB": "left wing-back", "RWB": "right wing-back", "DM": "deep midfielder",
                  "CM": "central midfielder", "AM": "attacking midfielder", "LW": "left winger",
                  "RW": "right winger", "ST": "striker"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NOTE_PARTIAL = "partial coverage — minutes undercounted"
NOTE_UNDER = "no profile (under 900 min)"
CLOCK_RULE = ("The 900-minute gate counts full-match equivalents: each match's minutes are "
              "scaled to a 90-minute match (120 with extra time), so stoppage time does not add "
              "to them.")
NOTE_INCOMPLETE = "profile incomplete (not plotted)"
WEIGHT_NOTE = ("Weights use nominal-equivalent minutes, the clock of the 900-minute gate. The "
               "season dot is display only: it is not part of the map's fit, the neighbour "
               "lists, the percentiles or the ranks.")
# Fixed wording with digits (checked: nothing else may carry one outside a data-mv span).
STATIC_TEXT = (CLOCK_RULE, NOTE_UNDER, WEIGHT_NOTE, "PC1", "PC2")


def esc(s: object) -> str:
    return html.escape(str(s))


def mv_span(ref_: str, text: str) -> str:
    return page_mod.span("data-mv", ref_, text)


# ── Wording ──────────────────────────────────────────────────────────────────

def month_range(first: dt.date, last: dt.date) -> str:
    """'Aug–Jan'; one month when the stint starts and ends in the same month of the same year;
    with the years ('Aug 2015–Aug 2016') when it spans 12 months or more, where the months alone
    would be ambiguous."""
    if (first.year, first.month) == (last.year, last.month):
        return MONTHS[first.month - 1]
    if (last.year - first.year) * 12 + last.month - first.month >= 12:
        return f"{MONTHS[first.month - 1]} {first.year}–{MONTHS[last.month - 1]} {last.year}"
    return f"{MONTHS[first.month - 1]}–{MONTHS[last.month - 1]}"


def position_words(role: dict | None) -> str:
    """'mostly right winger' (one role with ≥ 0.7 of the stint's minutes), else its two largest
    roles in the fixed role order, 'left winger / right winger'."""
    if role is None:
        return "goalkeeper"
    if role["label"] != "mixed":
        return "mostly " + POSITION_WORDS[role["label"]]
    a, b = role["combo"].split("/")
    return f"{POSITION_WORDS[a]} / {POSITION_WORDS[b]}"


def weights(nominal: list[float]) -> list[float]:
    tot = sum(nominal)
    if tot <= 0:
        cm.fail("combined dot: no minutes")
    return [m / tot for m in nominal]


def weight_texts(ws: list[float]) -> list[str]:
    """Whole percentages; they need not sum to 100 after rounding (each is re-derived alone)."""
    return [f"{w:.0%}" for w in ws]


def changed_most(pa: dict[str, float], pb: dict[str, float], metrics: tuple[str, ...]) -> list[str]:
    """The N_CHANGED metrics with the largest |Δ local percentile|, ties by metric name."""
    return sorted(metrics, key=lambda m: (-abs(pa[m] - pb[m]), m))[:N_CHANGED]


# ── Facts ────────────────────────────────────────────────────────────────────

def _match_dates() -> dict[int, dt.date]:
    out = {}
    for comp, season in COMPS:
        for m in load_matches(comp, season):
            out[m["match_id"]] = dt.date.fromisoformat(m["match_date"])
    return out


def stint_dates_from_matches(match_parquet: Path, keys: set[tuple]) -> dict[tuple, tuple]:
    """(player_id, team_id, competition_id) -> (first, last) match date with minutes > 0, from the
    pipeline's player-match rows (the build's source)."""
    dates = _match_dates()
    pm = (pl.read_parquet(match_parquet, columns=["match_id", "player_id", "team_id",
                                                  "competition_id", "minutes_played"])
          .filter(pl.col("minutes_played") > 0))
    out: dict[tuple, list] = {}
    for r in pm.iter_rows(named=True):
        k = (r["player_id"], r["team_id"], r["competition_id"])
        if k in keys:
            d = dates[r["match_id"]]
            lo, hi = out.get(k, (d, d))
            out[k] = (min(lo, d), max(hi, d))
    return out


def stint_dates_from_lineups(keys: set[tuple]) -> dict[tuple, tuple]:
    """The same dates from the raw lineup files (the check's source): a player with a lineup
    position in a match played in it."""
    out: dict[tuple, tuple] = {}
    for comp, season in COMPS:
        for m in load_matches(comp, season):
            d = dt.date.fromisoformat(m["match_date"])
            with open(DATA_ROOT / "lineups" / f"{m['match_id']}.json") as f:
                lineups = json.load(f)
            for team in lineups:
                for p in team["lineup"]:
                    k = (p["player_id"], team["team_id"], comp)
                    if k in keys and p["positions"]:
                        lo, hi = out.get(k, (d, d))
                        out[k] = (min(lo, d), max(hi, d))
    return out


def check_comps(seen: set[tuple[int, int]]) -> None:
    """COMPS and PARTIAL_COMPS are written out (spikes never import eleveniq); fail when the
    season parquet's (competition_id, season_id) pairs differ, or a PARTIAL competition is in
    the pool, so a config change cannot drift silently."""
    if seen != set(COMPS):
        cm.fail(f"season parquet competitions {sorted(seen)} != COMPS {sorted(COMPS)}; "
                "update COMPS / PARTIAL_COMPS from config.COMPETITION_SEASONS")
    if PARTIAL_COMPS & {c for c, _ in cm.POOL} or not PARTIAL_COMPS <= {c for c, _ in COMPS}:
        cm.fail(f"PARTIAL_COMPS {sorted(PARTIAL_COMPS)} inconsistent with the pool / COMPS")


def season_stints(season_parquet: Path) -> dict[tuple, list[dict]]:
    """(player_id, season_id, source) -> its stints, for player-seasons with 2+ rows."""
    s = pl.read_parquet(season_parquet).select(
        *ref.KEY, "player_name", "team_name", "minutes_played", "minutes_nominal_equiv",
        "position_group")
    check_comps({(r["competition_id"], r["season_id"])
                 for r in s.select("competition_id", "season_id").unique().iter_rows(named=True)})
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in s.iter_rows(named=True):
        k = tuple(r[c] for c in ref.KEY)
        by[(k[0], k[3], k[4])].append({
            "key": k, "name": r["player_name"], "team": r["team_name"], "comp": k[2],
            "minutes": r["minutes_played"], "nominal": r["minutes_nominal_equiv"],
            "group": r["position_group"]})
    return {p: v for p, v in by.items() if len(v) >= 2}


def build_facts(season_parquet: Path, match_parquet: Path) -> dict[tuple, list[dict]]:
    """Multi-stint player-seasons with dates and fine roles, stints in date order."""
    multi = season_stints(season_parquet)
    keys = {st["key"][:3] for v in multi.values() for st in v}
    dates = stint_dates_from_matches(match_parquet, keys)
    mins = cm.position_minutes({st["key"] for v in multi.values() for st in v}, COMPS)
    for v in multi.values():
        for st in v:
            k3 = st["key"][:3]
            if k3 not in dates:
                cm.fail(f"{st['key']}: no match with minutes")
            st["first"], st["last"] = dates[k3]
            st["role"] = (None if st["group"] == "GK"
                          else cm.fine_role(mins.get(k3, Counter()), st["minutes"], st["key"]))
        v.sort(key=lambda st: (st["first"], st["last"], st["key"]))
    return multi


def stint_note(st: dict, group: str, plotted: set[tuple], dna_group: dict[tuple, str],
               dna_status: dict[tuple, str], page_of: dict[str, str]) -> tuple[str, str | None]:
    """(note, link) for one stint on the map of `group`; ("", None) when it has a dot here."""
    k = st["key"]
    if st["comp"] in PARTIAL_COMPS:
        return NOTE_PARTIAL, None
    if k in plotted:
        return "", None
    if k in dna_group:
        g = dna_group[k]
        if g != group:
            return f"also on the {g} map", page_of[g]
        if dna_status[k] != "complete":
            return NOTE_INCOMPLETE, None
        cm.fail(f"{k}: complete row of {group} not plotted")
    if st["nominal"] >= GATE - GATE_TOL:
        cm.fail(f"{k}: {st['nominal']:.1f} nominal minutes in a pool league but no DNA row")
    return NOTE_UNDER, None


def clock_detail(stints: list[dict], notes: list[str]) -> str:
    """The Details text for stints shown with 900+ played minutes but under the gate's clock
    (user, 2026-10-08: the line says "no profile (under 900 min)"; the clock is explained here)."""
    out = []
    for st, note in zip(stints, notes):
        if note == NOTE_UNDER and st["minutes"] >= GATE:
            k = page_mod.kref(st["key"])
            mins, nom = f"{st['minutes']:,.0f}", f"{st['nominal']:,.1f}"
            out.append(f'<div>{mv_span(k + "|team", st["team"])}: {mv_span(k + "|min", mins)} '
                       f'min played, {mv_span(k + "|nominal", nom)} min on the gate\'s clock.</div>')
    if not out:
        return ""
    return "".join(out) + f'<div class="g">{esc(CLOCK_RULE)}</div>'


def played_for(stints: list[dict], group: str, plotted: set[tuple], dna_group: dict,
               dna_status: dict, page_of: dict[str, str], leagues: dict[int, str],
               colours: dict[tuple, int]) -> tuple[str, str]:
    """(the "Played for" block, its Details text). `colours` maps a stint key to its team colour
    slot (combined movers' plotted stints only)."""
    out = ['<div class="pf"><span class="g">Played for</span><ol>']
    notes = []
    for n, st in enumerate(stints):
        k = page_mod.kref(st["key"])
        note, link = stint_note(st, group, plotted, dna_group, dna_status, page_of)
        notes.append(note)
        sw = (f'<span class="sw t{colours[st["key"]]}"></span>' if st["key"] in colours else "")
        mins = f"{st['minutes']:,.0f}"
        line = (f'{"→ " if n else ""}{sw}{mv_span(k + "|team", st["team"])} · '
                f'{mv_span(k + "|league", leagues[st["comp"]])} · '
                f'{mv_span(k + "|min", mins)} min · '
                f'{mv_span(k + "|months", month_range(st["first"], st["last"]))} · '
                f'{mv_span(k + "|pos", position_words(st["role"]))}')
        if note:
            text = mv_span(k + "|note", note)
            if link:
                text = f'<a href="{esc(link)}">{text}</a>'
            line += f' <span class="muted">— {text}</span>'
        out.append(f"<li>{line}</li>")
    out.append("</ol></div>")
    return "".join(out), clock_detail(stints, notes)


def details(text: str) -> str:
    return f"<details><summary>Details</summary>{text}</details>" if text else ""


def rank_both(a: dict, b: dict, group_rows: list[dict]) -> tuple[tuple[int, int], tuple[int, int]]:
    """((rank of b in a's list, N for a), (rank of a in b's list, N for b)): where the other stint
    would sit among the candidates of the M4g1b compare panel (every other primary row of the
    group, partial rows included, the player's own rows left out); ties share a rank
    (m4g_compare.ranks)."""
    out = []
    for x, y in ((a, b), (b, a)):
        cand = [(ref.pair_distance(x, c), c["key"]) for c in group_rows if c["key"][0] != x["key"][0]]
        rk = cmp_mod.ranks(cand + [(ref.pair_distance(x, y), y["key"])])[y["key"]]
        out.append((rk, len(cand)))
    return out[0], out[1]


def combined(sm: dict, stints: list[dict], idx: dict[tuple, int]) -> dict:
    """The combined dot of one player-season: weights, input vector and scores."""
    ws = weights([st["nominal"] for st in stints])
    rows = [idx[st["key"]] for st in stints]
    x = sum(w * sm["x"][i] for w, i in zip(ws, rows))
    mean = sm["model"]["mean"]
    score = [sum(c * (xv - mu) for c, xv, mu in zip(comp, x, mean))
             for comp in sm["model"]["components"]]
    seg = [sum(w * float(sm["scores"][i, a]) for w, i in zip(ws, rows)) for a in (0, 1)]
    if any(abs(s - t) > SCORE_TOL for s, t in zip(score, seg)):
        cm.fail(f"combined dot {stints[0]['name']}: not on the segment of its stints")
    return {"weights": ws, "rows": rows, "score": score}


def mv_ref(stints: list[dict]) -> str:
    k = stints[0]["key"]
    return f"mv|{k[0]}|{k[3]}|{k[4]}"


def combined_card(ctx: dict, stints: list[dict], cb: dict, pf_html: str, detail: str) -> str:
    sm, names, labels = ctx["sm"], ctx["names"], ctx["labels"]
    r = mv_ref(stints)
    out = [f'<div><b>{esc(stints[0]["name"])}</b></div>', f'<div class="muted">{esc(LABEL)}</div>',
           pf_html]
    for a in (0, 1):
        where = page_mod.lean(cb["score"][a], sm["sd"][a], names, sm["model"], labels, a)
        sc = f"{cb['score'][a]:+.3f}"
        out.append(f'<div class="ax">{mv_span(f"{r}|pc{a + 1}|lean", where)} '
                   f'<span class="g">PC{a + 1} score '
                   f'{mv_span(f"{r}|pc{a + 1}", sc)}</span></div>')
    out.append('<div class="g">Click the dot to see each club.</div>')
    out.append(details(detail))
    return "".join(out)


def panel(ctx: dict, stints: list[dict], cb: dict, pf_html: str, detail: str,
          colours: dict) -> str:
    sm, labels = ctx["sm"], ctx["labels"]
    g = sm["group"]
    metrics = ref.DNA[g]
    r = mv_ref(stints)
    rows = [sm["rows"][i] for i in cb["rows"]]
    held = {f: s for f, (s, groups) in ref.WITHHELD.items() if g in groups}
    out = [f'<h3>{esc(stints[0]["name"])} — {esc(LABEL)}</h3>',
           '<div class="scroll"><table><thead><tr><th>Club</th><th>League</th>'
           '<th>Minutes</th><th>Weight</th></tr></thead><tbody>']
    for st, wt in zip(stints, weight_texts(cb["weights"])):
        k = page_mod.kref(st["key"])
        mins = f"{st['minutes']:,.0f}"
        out.append(f'<tr><td><span class="sw t{colours[st["key"]]}"></span>'
                   f'{mv_span(k + "|team", st["team"])}</td>'
                   f'<td>{mv_span(k + "|league", ctx["leagues"][st["comp"]])}</td>'
                   f'<td class="n">{mv_span(k + "|min", mins)}</td>'
                   f'<td class="n">{mv_span(k + "|weight", wt)}</td></tr>')
    out.append("</tbody></table></div>")
    cross = len({st["comp"] for st in stints}) > 1
    any_def = False
    for p in range(len(rows) - 1):
        a, b = rows[p], rows[p + 1]
        sa, sb = stints[p], stints[p + 1]
        d = ref.pair_distance(a, b)
        code = cmp_mod.dist_code(d)
        pa = {m: a["cells"][m]["pct_local"] for m in metrics}
        pb = {m: b["cells"][m]["pct_local"] for m in metrics}
        pick = changed_most(pa, pb, metrics)
        ka, kb = page_mod.kref(sa["key"]), page_mod.kref(sb["key"])
        head = (f' ({esc(sa["team"])} → {esc(sb["team"])})' if len(rows) > 2 else "")
        out.append(f'<p><b>DNA distance between the two clubs{head} '
                   f'{mv_span(f"{r}|dist|{p}", "0." + str(code).rjust(4, "0"))}</b> '
                   '<span class="muted">(the distance the neighbour lists use; '
                   'the same player at both clubs)</span></p>')
        (rab, na), (rba, nb) = rank_both(a, b, ctx["group_rows"])
        name = esc(stints[0]["name"])
        ta, tb = mv_span(ka + "|team", sa["team"]), mv_span(kb + "|team", sb["team"])
        plural = page_mod.GROUP_PLURAL[g]
        out.append(f'<ul><li>{name} at {tb} would be #{mv_span(f"{r}|rank|{p}|ab", str(rab))} '
                   f'most similar to {name} at {ta}, of {mv_span(f"{r}|n|{p}|a", str(na))} '
                   f'{esc(plural)}.</li>'
                   f'<li>{name} at {ta} would be #{mv_span(f"{r}|rank|{p}|ba", str(rba))} '
                   f'most similar to {name} at {tb}, of {mv_span(f"{r}|n|{p}|b", str(nb))} '
                   f'{esc(plural)}.</li></ul>'
                   f'<p class="muted">As in compare mode: the count is every other '
                   f'{esc(cmp_mod.GROUP_ONE[g])} row in the pool, those not plotted included (a '
                   "player who changed club appears once per club), leaving out his own rows; "
                   "players at the same distance share a rank.</p>")
        out.append(f'<h3>What changed most{head}</h3><ul data-mvpick="{r}|{p}|{",".join(pick)}">')
        for m in pick:
            fam = ref.SPEC[m][0]
            tag = (f' <span class="muted">(team-influenced: {esc(cmp_mod.TAG_WORDS[held[fam]])})'
                   f'</span>' if fam in held else "")
            any_def |= fam == "Defending" and fam in held
            out.append(f'<li><b>{esc(labels[m])}</b>{tag} — {esc(sa["team"])}: '
                       f'{mv_span(f"{ka}|{m}|words", page_mod.pct_words(pa[m])[1])} · '
                       f'{esc(sb["team"])}: '
                       f'{mv_span(f"{kb}|{m}|words", page_mod.pct_words(pb[m])[1])}</li>')
        out.append("</ul>")
    plural = page_mod.GROUP_PLURAL[g]
    out.append(f'<p class="muted">Each percentile places him among the {esc(plural)} of that '
               f'club\'s league{" (each against its own league)" if cross else ""}; metrics are '
               'ordered by the change in league percentile (ties by metric name).</p>')
    if any_def:
        out.append(f"<p>{esc(cmp_mod.DEFENDING_NOTE)}</p>")
    out.append(pf_html)
    out.append(details(detail))
    out.append(f'<p class="muted">{esc(WEIGHT_NOTE)}</p>')
    return "".join(out)


def for_map(ctx: dict, multi: dict[tuple, list[dict]], dna_group: dict[tuple, str],
            dna_status: dict[tuple, str], page_of: dict[str, str]) -> dict:
    """Everything one map page needs: "Played for" per plotted key, and the combined dots."""
    sm = ctx["sm"]
    g = sm["group"]
    idx = {r["key"]: i for i, r in enumerate(sm["rows"])}
    plotted = set(idx)
    played: dict[tuple, str] = {}
    played_detail: dict[tuple, str] = {}
    movers = []
    for pk in sorted(multi):
        stints = multi[pk]
        here = [st for st in stints if st["key"] in plotted]
        if not here:
            continue
        colours = ({st["key"]: n + 1 for n, st in enumerate(here)} if len(here) >= 2 else {})
        pf, detail = played_for(stints, g, plotted, dna_group, dna_status, page_of,
                                ctx["leagues"], colours)
        for st in here:
            played[st["key"]] = pf
            if detail:
                played_detail[st["key"]] = detail
        if len(here) >= 2:
            if len(here) > 3:
                cm.fail(f"{pk}: {len(here)} plotted stints; only 3 team colours")
            cb = combined(sm, here, idx)
            movers.append({"stints": here, "cb": cb, "name": here[0]["name"],
                           "tip": combined_card(ctx, here, cb, pf, detail),
                           "panel": panel(ctx, here, cb, pf, detail, colours),
                           "teams": [st["team"] for st in here]})
    return {"played": played, "detail": played_detail, "movers": movers}


# ── Check ────────────────────────────────────────────────────────────────────

class _MvSpans(page_mod._Spans):
    ATTRS = ("data-mv",)

    def __init__(self) -> None:
        super().__init__()
        self.picks: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "ul" and "data-mvpick" in a:
            self.picks.append(a["data-mvpick"])
        super().handle_starttag(tag, attrs)


def check_page(path: Path, group: str, dna_parquet: Path, season_parquet: Path,
               leagues: dict[int, str], labels: dict[str, str], names, roles: dict[tuple, dict],
               page_of: dict[str, str], lineup_dates: dict[tuple, tuple]) -> int:
    """Re-derive every data-mv number and the combined dots from the sources. Returns the number
    of spans checked."""
    text = path.read_text()
    data = json.loads(text.split('<script type="application/json" id="pts">', 1)[1]
                      .split("</script>", 1)[0])
    model, keys = data["model"], data["keys"]
    metrics = ref.DNA[group]
    dna = pl.read_parquet(dna_parquet)
    cells: dict[str, dict[str, float]] = defaultdict(dict)
    dna_group, dna_status = {}, {}
    grows: dict[str, dict] = {}            # every primary row of the group, partial included
    for r in dna.iter_rows(named=True):
        k = tuple(r[c] for c in ref.KEY)
        dna_group[k], dna_status[k] = r["position_group"], r["dna_status"]
        if r["position_group"] == group:
            cells[page_mod.kref(k)][r["metric"]] = r["pct_local"]
            row = grows.setdefault(page_mod.kref(k), {"key": k, "group": group,
                                                       "dna_status": r["dna_status"], "cells": {}})
            row["cells"][r["metric"]] = {"pct_local": r["pct_local"],
                                         "metric_status": r["metric_status"]}

    def dist(a: str, b: str) -> float:
        """Weighted squared distance: summed directly for two complete rows (independent of
        pair_distance); pair_distance for a partial row, as the compare check does."""
        if grows[a]["dna_status"] == grows[b]["dna_status"] == "complete":
            return sum(wt * ((cells[a][m] - cells[b][m]) / 100.0) ** 2
                       for m, wt in zip(model["metrics"], model["weights"]))
        return ref.pair_distance(grows[a], grows[b])

    def rank_of(x: str, y: str) -> tuple[int, int]:
        cand = [(dist(x, c), c) for c in grows if grows[c]["key"][0] != grows[x]["key"][0]]
        return cmp_mod.ranks(cand + [(dist(x, y), y)])[y], len(cand)
    season = pl.read_parquet(season_parquet).select(
        *ref.KEY, "player_name", "team_name", "minutes_played", "minutes_nominal_equiv")
    stints_of: dict[tuple, list[dict]] = defaultdict(list)
    by_ref = {}
    for r in season.iter_rows(named=True):
        k = tuple(r[c] for c in ref.KEY)
        st = {"key": k, "team": r["team_name"], "comp": k[2], "minutes": r["minutes_played"],
              "nominal": r["minutes_nominal_equiv"], "name": r["player_name"]}
        stints_of[(k[0], k[3], k[4])].append(st)
        by_ref[page_mod.kref(k)] = st
    for v in stints_of.values():
        for st in v:
            if len(v) >= 2:
                st["first"], st["last"] = lineup_dates[st["key"][:3]]
        if len(v) >= 2:
            v.sort(key=lambda st: (st["first"], st["last"], st["key"]))
    pos = {k: i for i, k in enumerate(keys)}
    plotted = {by_ref[k]["key"] for k in keys}

    def score_of(c: dict[str, float]) -> list[float]:
        x = [c[m] / 100.0 * math.sqrt(w) for m, w in zip(model["metrics"], model["weights"])]
        return [sum(cc * (xv - mu) for cc, xv, mu in zip(comp, x, model["mean"]))
                for comp in model["components"]]

    # Movers expected on this page, and their combined facts from the parquets.
    want_mv: dict[str, dict] = {}
    for pk, v in stints_of.items():
        here = [st for st in v if st["key"] in plotted]
        if len(v) >= 2 and len(here) >= 2:
            ws = weights([st["nominal"] for st in here])
            kr = [page_mod.kref(st["key"]) for st in here]
            avg = {m: sum(w * cells[k][m] for w, k in zip(ws, kr)) for m in metrics}
            score = score_of(avg)
            seg = [sum(w * data["scores"][pos[k]][a] for w, k in zip(ws, kr)) for a in (0, 1)]
            if any(abs(s - t) > SCORE_TOL for s, t in zip(score, seg)):
                cm.fail(f"{path.name}: combined {here[0]['name']} off its segment")
            want_mv[mv_ref(here)] = {"here": here, "ws": ws, "score": score, "kr": kr}
    if len(data["mv"]) != len(want_mv):
        cm.fail(f"{path.name}: {len(data['mv'])} combined dots, expected {len(want_mv)}")
    sd = []
    for a in (0, 1):
        v = [s[a] for s in data["scores"]]
        mu = sum(v) / len(v)
        sd.append(math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1)))
    seen = set()
    for m in data["mv"]:
        st_keys = [keys[i] for i in m["st"]]
        r = "mv|" + "|".join(st_keys[0].split("|")[i] for i in (0, 3, 4))
        w = want_mv.get(r)
        if w is None or w["kr"] != st_keys:
            cm.fail(f"{path.name}: combined dot {m['name']} has the wrong stints")
        seen.add(r)
        px = [sum(wt * data["xy"][pos[k]][a] for wt, k in zip(w["ws"], w["kr"])) for a in (0, 1)]
        if any(abs(p - q) > PX_TOL for p, q in zip(px, m["xy"])):
            cm.fail(f"{path.name}: combined dot {m['name']} drawn at {m['xy']}, expected {px}")
    if seen != set(want_mv):
        cm.fail(f"{path.name}: combined dots missing for {sorted(set(want_mv) - seen)}")

    static = sorted(STATIC_TEXT + tuple(labels.values()), key=len, reverse=True)

    def want(r_: str) -> str:
        parts = r_.split("|")
        if parts[0] == "mv":
            w = want_mv["|".join(parts[:4])]
            if parts[4] in ("dist", "rank", "n"):
                p = int(parts[5])
                a, b = w["kr"][p], w["kr"][p + 1]
                if parts[4] == "dist":
                    return "0." + str(cmp_mod.dist_code(dist(a, b))).rjust(4, "0")
                if parts[4] == "rank":
                    return str(rank_of(a, b)[0] if parts[6] == "ab" else rank_of(b, a)[0])
                return str(rank_of(a, b)[1] if parts[6] == "a" else rank_of(b, a)[1])
            a = int(parts[4][2:]) - 1
            score = w["score"][a]
            if len(parts) == 5:
                return f"{score:+.3f}"
            return page_mod.lean(score, sd[a], names, model, labels, a)
        k, field = "|".join(parts[:5]), parts[5]
        st = by_ref[k]
        if field == "team":
            return st["team"]
        if field == "league":
            return leagues[st["comp"]]
        if field == "min":
            return f"{st['minutes']:,.0f}"
        if field == "nominal":
            return f"{st['nominal']:,.1f}"
        if field == "months":
            return month_range(st["first"], st["last"])
        if field == "pos":
            if st["key"] not in roles:
                cm.fail(f"{path.name}: no fine role for {st['key']}")
            return position_words(roles[st["key"]])
        if field == "weight":
            for w in want_mv.values():
                if k in w["kr"]:
                    return weight_texts(w["ws"])[w["kr"].index(k)]
            cm.fail(f"{path.name}: weight for {k}, which is in no combined dot")
        if field == "note":
            note, _ = stint_note(st, group, plotted, dna_group, dna_status, page_of)
            return note
        return page_mod.pct_words(cells[k][field])[1]       # <key>|<metric>|words

    n = 0
    multi_refs = {page_mod.kref(st["key"]) for v in stints_of.values() if len(v) >= 2 for st in v}
    blocks = [(t, keys[i]) for i, t in enumerate(data["tips"])]
    blocks += [(m["tip"], None) for m in data["mv"]] + [(m["panel"], None) for m in data["mv"]]

    def clock_refs(player_of: str) -> set[str]:
        """Stints of this player shown with 900+ played minutes but under the gate's clock."""
        k = by_ref[player_of]["key"]
        return {page_mod.kref(st["key"]) for st in stints_of[(k[0], k[3], k[4])]
                if st["minutes"] >= GATE
                and stint_note(st, group, plotted, dna_group, dna_status, page_of)[0] == NOTE_UNDER}

    owners_mv = [keys[m["st"][0]] for m in data["mv"]] * 2
    for bi, (blob, owner) in enumerate(blocks):
        if owner is not None and (('class="pf"' in blob) != (owner in multi_refs)):
            cm.fail(f"{path.name}: {owner}: Played for block present iff 2+ stints")
        if owner is not None and 'class="pf"' not in blob:
            continue
        p = _MvSpans()
        p.feed(blob)
        for r_, shown in ((r_, s) for _, r_, s in p.spans):
            if shown != (w := want(r_)):
                cm.fail(f"{path.name}: data-mv {r_} shows {shown!r}, source gives {w!r}")
            n += 1
        who = owner if owner is not None else owners_mv[bi - len(data["tips"])]
        got = {"|".join(r_.split("|")[:5]) for _, r_, _ in p.spans if r_.endswith("|nominal")}
        if got != clock_refs(who):
            cm.fail(f"{path.name}: {who}: gate-clock details {sorted(got)}, expected "
                    f"{sorted(clock_refs(who))}")
        for pk_ in p.picks:
            r_, p_i, ms = pk_.rsplit("|", 2)
            w = want_mv[r_]
            a, b = w["kr"][int(p_i)], w["kr"][int(p_i) + 1]
            if ms.split(",") != changed_most(cells[a], cells[b], metrics):
                cm.fail(f"{path.name}: changed-most metrics for {r_} differ from the parquet")
        if owner is None:
            loose = "".join(p.loose)
            for s_ in static:
                loose = loose.replace(s_, "")
            if re.search(r"\d", loose):
                cm.fail(f"{path.name}: digits outside data-mv spans: {loose[:300]}")
    return n


def show(path: Path, group: str, players: tuple[str, ...]) -> None:
    """Print the combined card and the expanded panel of the named players on this page."""
    data = json.loads(path.read_text().split('<script type="application/json" id="pts">', 1)[1]
                      .split("</script>", 1)[0])
    for m in data["mv"]:
        if m["name"] in players:
            print(f"\n── {group}: {m['name']} — combined card ──\n{cmp_mod.as_text(m['tip'])}")
            tab = re.sub(r"</t[dh]>", " | ", m["panel"]).replace("</tr>", "</p>")
            print(f"\n── {group}: {m['name']} — expanded panel ──\n{cmp_mod.as_text(tab)}\n")
