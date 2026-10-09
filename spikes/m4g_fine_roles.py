"""Spike: fine-role sample sizes in the percentile pool (user, 2026-10-07; report only).

Measures how many primary scored rows (the 1,233 DNA rows of the four 2015/16 pool leagues:
gated, outfield, cohort_role "primary") would carry each fine role, before any fine-role map or
cohort is designed. Cohorts, percentiles and DNA are unchanged. Never imports eleveniq.

Fine role per row: minutes in each lineup position on the ADR 002 §31 clock (the
verify_core_metrics spike's implementation, via m4g_common.position_minutes), summed into
the roles below; share = role minutes / all the row's minutes (every position counted; the sum
must equal minutes_played within 1e-6). label = the role with share ≥ 0.7, else "mixed"; a
mixed row's combo is its two largest roles.

Role mapping (StatsBomb lineup positions; a choice, stated in the report):
  CB  Center Back, Left / Right Center Back      LB  Left Back        RB  Right Back
  LWB Left Wing Back                             RWB Right Wing Back
  DM  Center / Left / Right Defensive Midfield, Defensive Midfield
  CM  Center / Left / Right Center Midfield
  AM  Center Attacking Midfield, Attacking Midfield, Left / Right Attacking Midfield
  LW  Left Wing, Left Midfield                   RW  Right Wing, Right Midfield
  ST  Center Forward, Left / Right Center Forward, Secondary Striker

Thresholds: MIN_COHORT_SIZE 30 (ADR 004 D11; local percentiles need it per league), 60 for a
stable map (user).

Usage: .venv/bin/python spikes/m4g_fine_roles.py [--out PATH]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g_common as cm  # noqa: E402  (sibling spike: per-position minutes, fine roles)
import m4f_weighting_checks as wc  # noqa: E402  (sibling spike: load() and its guards)

ref = wc.ref
ROOT = ref.ROOT
OUT = ROOT / "docs" / "reviews" / "m4g-fine-roles.md"
KEY = ref.KEY

LABEL_SHARE = cm.LABEL_SHARE
MIN_COHORT = 30                          # config.MIN_COHORT_SIZE (ADR 004 D11)
STABLE_MAP = 60                          # user, 2026-10-07
TOP_COMBOS = 6
ROLES = cm.ROLES
CENTRAL = ("DM", "CM", "AM")
PROPOSAL = """\
Proposal only, nothing implemented; a dated snapshot (2026-10-07) of the figures above. Its
quoted figures are checked on each run (PROPOSAL_FIGURES); a mismatch fails it.

- **No fine role gets its own percentile cohort.** Percentiles are local, so a cohort needs
  ≥ 30 in every league. Only CB meets it, and the CB role is 93% of the CB group already. LB
  (min 15), RB (16), DM (16, Premier League) and ST (28, Ligue 1) fail.
- **Own map (PCA of the unchanged group-percentile vectors, role rows only): none
  recommended now.** Pooled n ≥ 60 holds for CB 237, ST 140, DM 120, LB 92 and RB 83, but:
  - CB and ST are 93% and 89% of their groups, so their map is the group map;
  - LB vs RB is a mirror (side), not a style difference. A left-back-only map halves n and
    shows the same axes;
  - a DM-only map is the 6 side of the DM-CM map, where M4g1 found a continuum.
- **Colour / filter on the group map (recommended):**
  - FB-WB: LB / RB / LWB / RWB / mixed (LWB 5 and RWB 4 are too few to read alone);
  - DM-CM: DM / CM / mixed (CM 44);
  - AM-Winger: LW / RW / AM / mixed. AM is only 10 rows; central 10s are mostly mixed with a
    wing (AM/RW 23, AM/LW 19), and LW/RW is the largest combo (61);
  - CB and ST: colour the mixed rows only (CB/RB 12; RW/ST 9).
  - **Caveat:** LW / RW merge the wide forward slot (Left/Right Wing, 63–66% of minutes) with
    the wide midfield slot of a 4-4-2 (34–37%). Like 6 / 8, the slot records formation as well
    as role.
- **Combined "midfield" map: feasible by count, not by method, and of little gain.**
  - **Count:** 294 DM-CM + 26 central AM-Winger rows = 320 (73–89 per league).
  - **Method:** each group's percentiles rank against its own population, and the DNA lists
    share 20 metrics. A combined map would need the 320 rows re-ranked in one cohort, which
    is a cohort change (ADR), against "cohorts unchanged".
  - **Gain:** the added rows are 8% of the map.
  - **Recommendation:** not now. If wanted later, propose a "central midfield" cohort in its
    own ADR.
"""
# Every figure the proposal quotes, checked against this run (code-reviewer, M4g1).
PROPOSAL_FIGURES = {
    "n CB": 237, "n ST": 140, "n DM": 120, "n LB": 92, "n RB": 83, "n CM": 44, "n AM": 10,
    "n LWB": 5, "n RWB": 4, "min league LB": 15, "min league RB": 16, "min league DM": 16,
    "min league ST": 28, "CB share of group %": 93, "ST share of group %": 89,
    "combo AM/RW": 23, "combo AM/LW": 19, "combo LW/RW": 61, "combo CB/RB": 12,
    "combo RW/ST": 9, "central AM-Winger": 26, "DM-CM rows": 294,
    "midfield combined": 320, "midfield min league": 73, "midfield max league": 89,
    "midfield added %": 8, "shared DNA metrics": 20, "wide slot min %": 63,
    "wide slot max %": 66,
}
LEAGUE_ORDER = ("La Liga", "Ligue 1", "Premier League", "Serie A")


def label_rows(rows: list[dict]) -> list[dict]:
    mins = cm.position_minutes({r["key"] for r in rows})
    season = pl.read_parquet(wc.SEASON_PARQUET).select(*KEY, "minutes_played")
    played = {tuple(r[c] for c in KEY): r["minutes_played"] for r in season.iter_rows(named=True)}
    out = []
    for r in rows:
        role = cm.fine_role(mins.get(r["key"][:3], Counter()), played[r["key"]], r["key"])
        out.append({**r, **role, "top_share": role["top"][0][1],
                    "central": sum(role["share"].get(c, 0.0) for c in CENTRAL)})
    print(f"position minutes reconciled for {len(out)} rows "
          f"(max |diff| {max(r['diff'] for r in out):.2e})")
    return out


def table(head: list[str], body: list[list[str]], right: int = 1) -> list[str]:
    al = ["---"] * right + ["---:"] * (len(head) - right)
    return ["| " + " | ".join(head) + " |", "| " + " | ".join(al) + " |",
            *("| " + " | ".join(r) + " |" for r in body)]


def check_proposal(rows: list[dict], n_shared: int) -> None:
    """Fail when a figure quoted in PROPOSAL no longer matches this run."""
    lab = Counter(r["label"] for r in rows)
    by_role: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_role[r["label"]][r["league"]] += 1
    grp = Counter(r["group"] for r in rows)
    # The proposal quotes each combo within the group it is about.
    combos = Counter((r["group"], r["combo"]) for r in rows if r["label"] == "mixed")
    dm = sum(r["group"] == "DM-CM" for r in rows)
    central = [r for r in rows if r["group"] == "AM-Winger" and r["central"] >= LABEL_SHARE]
    mid_lg = Counter(r["league"] for r in rows if r["group"] == "DM-CM") + Counter(
        r["league"] for r in central)
    wide = {}
    for role, parts in (("LW", ("Left Wing",)), ("RW", ("Right Wing",))):
        rr = [r for r in rows if r["label"] == role]
        tot = sum(sum(v for p, v in r["pos_min"].items() if cm.ROLE_OF[p] == role) for r in rr)
        wide[role] = round(100 * sum(r["pos_min"].get(parts[0], 0.0) for r in rr) / tot)
    now = {
        **{f"n {k}": lab[k] for k in ("CB", "ST", "DM", "LB", "RB", "CM", "AM", "LWB", "RWB")},
        **{f"min league {k}": min(by_role[k].get(x, 0) for x in LEAGUE_ORDER)
           for k in ("LB", "RB", "DM", "ST")},
        "CB share of group %": round(100 * lab["CB"] / grp["CB"]),
        "ST share of group %": round(100 * lab["ST"] / grp["ST"]),
        **{f"combo {c}": combos[(g, c)] for g, c in (
            ("AM-Winger", "AM/RW"), ("AM-Winger", "AM/LW"), ("AM-Winger", "LW/RW"),
            ("CB", "CB/RB"), ("ST", "RW/ST"))},
        "central AM-Winger": len(central), "DM-CM rows": dm,
        "midfield combined": dm + len(central),
        "midfield min league": min(mid_lg[x] for x in LEAGUE_ORDER),
        "midfield max league": max(mid_lg[x] for x in LEAGUE_ORDER),
        "midfield added %": round(100 * len(central) / (dm + len(central))),
        "shared DNA metrics": n_shared,
        "wide slot min %": min(wide.values()), "wide slot max %": max(wide.values()),
    }
    bad = {k: (v, now[k]) for k, v in PROPOSAL_FIGURES.items() if now[k] != v}
    if bad:
        cm.fail(f"PROPOSAL quotes figures this run no longer gives (quoted, now): {bad}")


def report(rows: list[dict], meta: dict) -> str:
    groups = wc.GROUPS
    L = ["# M4g — Fine-role sample sizes (report only)", "",
         "User request 2026-10-07: measure before building anything. Generated by "
         "`spikes/m4g_fine_roles.py` (no eleveniq imports). Cohorts, percentiles and DNA are "
         f"unchanged. Build: `registry_version` {meta['registry_version'][:12]}…, source "
         f"`{meta['source']}`.", "",
         "## Method", "",
         f"- **Rows:** the {len(rows):,} primary scored rows (DNA rows) of the four 2015/16 pool "
         "leagues: gated (≥ 900 nominal minutes), outfield, `cohort_role` primary. One row per "
         "player-team-league-season; the group is the row's `position_group`.",
         "- **Minutes per lineup position** on the ADR 002 §31 clock (the `verify_core_metrics` "
         "spike's implementation); their sum equals `minutes_played` for every row "
         "(tolerance 1e-6).",
         f"- **Label:** the fine role holding ≥ {LABEL_SHARE} of the row's minutes, else "
         "`mixed`; a mixed row's combo is its two largest roles.",
         "- **Role mapping (a choice):** LW = Left Wing + Left Midfield, RW = Right Wing + Right "
         "Midfield; AM = Center / Left / Right Attacking Midfield; DM = the three Defensive "
         "Midfield slots; CM = the three Center Midfield slots; ST includes Secondary Striker.",
         f"- **Bars:** {MIN_COHORT} = `MIN_COHORT_SIZE` (D11). Percentiles are local (per "
         f"league, §12.1), so a role's own percentile cohort needs ≥ {MIN_COHORT} in *every* "
         f"league; pooled n is what a map (PCA) uses. {STABLE_MAP} = the user's bar for a "
         "stable map.", ""]

    # Per group
    L += ["## Per position group", ""]
    body = []
    for g in groups:
        gr = [r for r in rows if r["group"] == g]
        lab = Counter(r["label"] for r in gr)
        roles = ", ".join(f"{k} {v}" for k, v in sorted(lab.items(), key=lambda kv: -kv[1])
                          if k != "mixed")
        body.append([g, str(len(gr)), str(len(gr) - lab["mixed"]), str(lab["mixed"]),
                     f"{lab['mixed'] / len(gr):.0%}", roles])
    L += table(["Group", "Rows", "Labelled", "Mixed", "Mixed %", "Labels"], body)
    L += ["", "### Most common mixed combos", ""]
    body = []
    for g in groups:
        mixed = [r for r in rows if r["group"] == g and r["label"] == "mixed"]
        combos = Counter(r["combo"] for r in mixed)
        cover = sorted(r["top2_share"] for r in mixed)
        med = cover[len(cover) // 2] if cover else None
        body.append([g, str(len(mixed)), ", ".join(f"{c} {n}" for c, n in
                                                   combos.most_common(TOP_COMBOS)),
                     "–" if med is None else f"{med:.2f}"])
    L += table(["Group", "Mixed", f"Top {TOP_COMBOS} combos (rows)",
                "Median share in the top 2 roles"], body, right=1)

    # Per role
    L += ["", "## Per fine role", ""]
    body = []
    by_role: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["label"] != "mixed":
            by_role[r["label"]].append(r)
    for role in ROLES:
        rr = by_role[role]
        lg = Counter(r["league"] for r in rr)
        min_lg = min(lg.get(x, 0) for x in LEAGUE_ORDER)
        grp = Counter(r["group"] for r in rr)
        body.append([role, str(len(rr)), *(str(lg.get(x, 0)) for x in LEAGUE_ORDER), str(min_lg),
                     "yes" if len(rr) >= MIN_COHORT else "no",
                     "yes" if min_lg >= MIN_COHORT else "no",
                     "yes" if len(rr) >= STABLE_MAP else "no",
                     ", ".join(f"{k} {v}" for k, v in grp.most_common())])
    L += table(["Role", "n labelled", *LEAGUE_ORDER, "Min league", f"n ≥ {MIN_COHORT}",
                f"≥ {MIN_COHORT} every league", f"n ≥ {STABLE_MAP}", "Main group of the rows"],
               body)

    # Wide roles: wing vs wide midfield; AM sub-slots
    L += ["", "### Inside the merged roles", ""]
    body = []
    for role, parts in (("LW", ("Left Wing", "Left Midfield")),
                        ("RW", ("Right Wing", "Right Midfield")),
                        ("AM", ("Center Attacking Midfield", "Left Attacking Midfield",
                                "Right Attacking Midfield", "Attacking Midfield"))):
        rr = by_role[role]
        tot = sum(sum(r["pos_min"].get(p, 0.0) for p in parts) for r in rr)
        body.append([role, str(len(rr)), "; ".join(
            f"{p} {sum(r['pos_min'].get(p, 0.0) for r in rr) / tot:.0%}" for p in parts
            if tot)])
    L += table(["Role", "Rows", "Share of the role's minutes by slot"], body)
    L += ["", "Left / Right Midfield is the wide slot of a 4-4-2 / 4-4-1-1; Left / Right Wing "
          "the wide forward of a 4-3-3 / 4-2-3-1. Like 6 / 8, the slot records formation as well "
          "as role.", ""]

    # Combined midfield
    dm = [r for r in rows if r["group"] == "DM-CM"]
    amw = [r for r in rows if r["group"] == "AM-Winger"]
    am_central = [r for r in amw if r["central"] >= LABEL_SHARE]
    lg = Counter(r["league"] for r in dm + am_central)
    shared = sorted(set(ref.DNA["DM-CM"]) & set(ref.DNA["AM-Winger"]))
    only_dm = sorted(set(ref.DNA["DM-CM"]) - set(ref.DNA["AM-Winger"]))
    only_am = sorted(set(ref.DNA["AM-Winger"]) - set(ref.DNA["DM-CM"]))
    L += ["## A combined \"midfield\" map: the inputs", "",
          f"- DM-CM rows: {len(dm)}. AM-Winger rows with ≥ {LABEL_SHARE} of their minutes in "
          f"central slots (DM + CM + AM): **{len(am_central)}** of {len(amw)} (AM-labelled "
          f"{sum(r['label'] == 'AM' for r in amw)}).",
          f"- Combined: {len(dm) + len(am_central)} rows; per league "
          + ", ".join(f"{x} {lg.get(x, 0)}" for x in LEAGUE_ORDER) + ".",
          f"- DNA lists: {len(shared)} metrics shared; DM-CM only: "
          + ", ".join(m.removesuffix("_p90") for m in only_dm) + "; AM-Winger only: "
          + ", ".join(m.removesuffix("_p90") for m in only_am) + ".",
          "- The two groups' percentiles rank against different reference populations (each "
          "row against its own group in its league), so a DM-CM 90th percentile and an "
          "AM-Winger 90th percentile on the same metric are not the same value.", ""]
    check_proposal(rows, len(shared))
    L += ["## Proposal", "", PROPOSAL]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    by_group, meta, _ = wc.load()
    rows = label_rows([r for g in wc.GROUPS for r in by_group[g]])
    text = report(rows, meta)
    args.out.write_text(text)
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
