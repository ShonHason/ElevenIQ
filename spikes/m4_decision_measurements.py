"""Spike: measurements that settle the M4 decisions D1–D22 (plan 005, M4a).

Reads data/processed/player_season_stats.parquet, data/interim/player_match_stats.parquet,
matches.json (dates) and the raw events. Writes nothing: it prints one section per
D-item, and the figures go into ADR 004.

This is a measurement, not an independent check. It never imports eleveniq
(tests/test_spikes_independent.py): the pass geometry (ADR 002 §1, §27), the position
taxonomy (§4) and the restart set (ADR 004 D14) are written out below. The D22 rates
were measured once with the pipeline (plan 005 M4a) and are recorded in ADR 004; that
section was removed with the eleveniq import (plan 005 M4b2).

"Gated" below means the D1 default: in_percentile_pool ∧ is_outfield ∧ nominal-equivalent
minutes ≥ 900, where nominal = Σ_matches minutes × 90 / match_duration.

Usage: .venv/bin/python spikes/m4_decision_measurements.py
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

# Written out instead of imported from eleveniq (see the module docstring).
LONG_PASS_THRESHOLD_UNITS = 30.0  # ADR 002 §19
MIN_AERIAL_CONTESTS = 10  # ADR 002 Chunk A decisions (config default)
SET_PIECE_TYPES = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off"})
RESTART_TYPES = SET_PIECE_TYPES | {"Throw-in"}  # ADR 004 D14
DEFENSIVE_40_X, PROG_PASS_UNITS, FINAL_THIRD_X = 48.0, 10.0, 80.0  # ADR 002 §1, §27
GOAL_CENTRE = (120.0, 40.0)
POSITION_GROUP_MAP = {  # ADR 002 §4
    "Goalkeeper": "GK",
    **dict.fromkeys(["Center Back", "Left Center Back", "Right Center Back"], "CB"),
    **dict.fromkeys(["Left Back", "Right Back", "Left Wing Back", "Right Wing Back"], "FB-WB"),
    **dict.fromkeys(["Defensive Midfield", "Center Defensive Midfield", "Left Defensive Midfield",
                     "Right Defensive Midfield", "Center Midfield", "Left Center Midfield",
                     "Right Center Midfield"], "DM-CM"),
    **dict.fromkeys(["Attacking Midfield", "Center Attacking Midfield", "Left Midfield",
                     "Right Midfield", "Left Wing", "Right Wing", "Left Attacking Midfield",
                     "Right Attacking Midfield"], "AM-Winger"),
    **dict.fromkeys(["Center Forward", "Left Center Forward", "Right Center Forward",
                     "Secondary Striker"], "ST"),
}


def _in_box(p: tuple[float, float]) -> bool:
    return p[0] >= 102.0 and 18.0 <= p[1] <= 62.0


def _completed_progressive(s, t, outcome) -> bool:
    """ADR 002 §1 geometry, completed passes; the restart rule is applied by the caller."""
    if outcome is not None or s[0] < DEFENSIVE_40_X:
        return False
    closer = math.dist(s, GOAL_CENTRE) - math.dist(t, GOAL_CENTRE)
    return closer >= PROG_PASS_UNITS or (not _in_box(s) and _in_box(t))


def _completed_box_entry(s, t, outcome) -> bool:
    return outcome is None and not _in_box(s) and _in_box(t)


def _completed_final_third_entry(s, t, outcome) -> bool:
    return outcome is None and s[0] < FINAL_THIRD_X <= t[0]

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "statsbomb"
SEASON_PARQUET = ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = ROOT / "data" / "interim" / "player_match_stats.parquet"

LEAGUES = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1", 9: "Bundesliga"}
POOL_LEAGUES = (11, 2, 12, 7)
SEASON = 27
GATE = 900.0
OUTFIELD = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
GROUP_COLS = {
    "GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
    "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger", "ST": "minutes_st",
}
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]

# Plan 005 thresholds under test (proposals; the ADR fixes the final values).
ZERO_HEAVY_SHARE = 0.30
AMBIGUOUS_MARGIN = 0.05
GLOBAL_OK_BAND = 10.0          # D9: each league's mean global percentile within 50 ± 10
MEDIAN_RATIO_BAND = (0.8, 1.25)  # D9 original proposal, shown for comparison
MIN_COHORT_SIZE = 30
POSSESSION_RHO = 0.4
COLLINEAR_RHO = 0.85
RELIABILITY_DNA = 0.7
RELIABILITY_RATIO = 0.6
TRAIT_COUNTS = (5, 10, 15)

# Ratio metrics: (name, numerator column, denominator expression columns (+, -)).
RATIOS = {
    "pass_completion_pct": ("passes_completed", ["passes_attempted"], ["passes_unknown_outcome"]),
    "long_pass_completion_pct": ("long_passes_completed", ["long_passes_attempted"], ["long_passes_unknown_outcome"]),
    "cross_completion_pct": ("crosses_completed", ["crosses_attempted"], ["crosses_unknown_outcome"]),
    "dribble_success_pct": ("dribbles_completed", ["dribbles_attempted"], []),
    "tackle_success_pct": ("tackles_won", ["tackles_attempted"], []),
    "aerial_win_pct": ("aerials_won", ["aerials_won", "aerials_lost"], []),
}


def load_json(p: Path):
    with p.open() as f:
        return json.load(f)


def section(title: str) -> None:
    print(f"\n{'=' * 100}\n## {title}\n{'=' * 100}")


def show(df: pl.DataFrame, rows: int = 60) -> None:
    with pl.Config(tbl_rows=rows, tbl_cols=30, fmt_str_lengths=60, tbl_width_chars=220,
                   tbl_hide_dataframe_shape=True, float_precision=3):
        print(df)


def hazen(pop: pl.Series, x: pl.Series) -> pl.Series:
    """Hazen percentile of each x against the population pop: (less + 0.5·equal) / n × 100."""
    s = pop.drop_nulls().sort()
    n = s.len()
    if n == 0:
        return pl.Series([None] * x.len(), dtype=pl.Float64)
    lo = s.search_sorted(x, side="left").cast(pl.Float64)
    hi = s.search_sorted(x, side="right").cast(pl.Float64)
    return (lo + 0.5 * (hi - lo)) / n * 100.0


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((v - ma) ** 2 for v in a))
    sb = math.sqrt(sum((v - mb) ** 2 for v in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def spearman_brown(r: float | None) -> float | None:
    if r is None:
        return None
    return 2 * r / (1 + r) if r > -1 else None


# ── Load ──────────────────────────────────────────────────────────────────────
season = pl.read_parquet(SEASON_PARQUET)
match = pl.read_parquet(MATCH_PARQUET)
P90 = [c for c in season.columns if c.endswith("_p90") and c not in ("goals_p90", "assists_p90")]
COUNT_OF = {c: c[: -len("_p90")] for c in P90}

dates: dict[int, str] = {}
for comp in LEAGUES:
    for m in load_json(RAW / "matches" / str(comp) / f"{SEASON}.json"):
        dates[m["match_id"]] = f"{m['match_date']} {m.get('kick_off') or ''}"
match = match.with_columns(
    pl.col("match_id").replace_strict(dates, return_dtype=pl.Utf8).alias("match_date"),
    (pl.col("minutes_played") * 90.0 / pl.col("match_duration")).alias("minutes_nominal"),
)
nominal = match.group_by(KEY).agg(pl.col("minutes_nominal").sum().alias("minutes_nominal_equiv"))
season = season.join(nominal, on=KEY, how="left")

# Season position margin and share (D5).
outfield_min_cols = [GROUP_COLS[g] for g in GROUP_COLS]
season = season.with_columns(
    pl.concat_list(outfield_min_cols).list.sort(descending=True).alias("_sorted_mins")
).with_columns(
    ((pl.col("_sorted_mins").list.get(0) - pl.col("_sorted_mins").list.get(1)) / pl.col("minutes_played"))
    .alias("position_group_margin_pct"),
    (pl.col("_sorted_mins").list.get(0) / pl.col("minutes_played")).alias("position_group_share"),
).drop("_sorted_mins")
season = season.with_columns(
    pl.struct(outfield_min_cols).map_elements(
        lambda r: sorted(((v, g) for g, c in GROUP_COLS.items() for v in [r[c]]), reverse=True)[1][1],
        return_dtype=pl.Utf8,
    ).alias("second_group")
)

base = season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
gated = base.filter(pl.col("minutes_nominal_equiv") >= GATE)


def dedupe_global(df: pl.DataFrame) -> pl.DataFrame:
    """D3: one row per (player_id, season_id, source), most minutes, tie → lowest competition_id."""
    return (df.sort(["minutes_played", "competition_id"], descending=[True, False])
              .unique(subset=["player_id", "season_id", "source"], keep="first", maintain_order=True))


def local_pct(df: pl.DataFrame, metric: str, pop: pl.DataFrame | None = None) -> pl.Series:
    pop = df if pop is None else pop
    out = pl.Series([None] * df.height, dtype=pl.Float64)
    parts = []
    for (comp, grp), sub in df.with_row_index("_i").group_by(["competition_id", "position_group"]):
        p = pop.filter((pl.col("competition_id") == comp) & (pl.col("position_group") == grp))
        parts.append(sub.select("_i").with_columns(hazen(p[metric], sub[metric]).alias("v")))
    allp = pl.concat(parts).sort("_i")
    return out.scatter(allp["_i"], allp["v"])


def global_pct(df: pl.DataFrame, metric: str, pop: pl.DataFrame, group_col: str = "position_group") -> pl.Series:
    out = pl.Series([None] * df.height, dtype=pl.Float64)
    parts = []
    for (grp,), sub in df.with_row_index("_i").group_by([group_col]):
        p = pop.filter(pl.col("position_group") == grp)
        parts.append(sub.select("_i").with_columns(hazen(p[metric], sub[metric]).alias("v")))
    allp = pl.concat(parts).sort("_i")
    return out.scatter(allp["_i"], allp["v"])


def league_name(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.col("competition_id").replace_strict(LEAGUES, return_dtype=pl.Utf8).alias("league"))


# ── D1 ────────────────────────────────────────────────────────────────────────
section("D1 — minutes gate")
opts = base.with_columns(
    (pl.col("minutes_played") >= 900).alias("a_900_elapsed"),
    (pl.col("minutes_played") >= 945).alias("b_945_elapsed"),
    (pl.col("minutes_nominal_equiv") >= 900).alias("c_nominal_900"),
)
show(league_name(opts).group_by("league").agg(
    pl.col("a_900_elapsed").sum(), pl.col("b_945_elapsed").sum(), pl.col("c_nominal_900").sum()).sort("league"))
b_set = set(opts.filter("b_945_elapsed")["player_id"].to_list())
c_rows = opts.filter("c_nominal_900")
diff_bc = opts.filter(pl.col("b_945_elapsed") != pl.col("c_nominal_900")).height
print(f"rows where (b) and (c) disagree: {diff_bc}")
dropped = league_name(opts.filter(pl.col("a_900_elapsed") & ~pl.col("c_nominal_900")))
print(f"(a) → (c) drops {dropped.height} rows; by group:")
show(dropped.group_by("position_group").len().sort("position_group"))
full = league_name(match.filter(pl.col("competition_id").is_in(POOL_LEAGUES)).unique("match_id")).group_by("league").agg(
    pl.col("match_duration").mean().alias("mean_match_duration"))
show(full.sort("league"))

# Sensitivity: local percentiles at T vs at 900, on rows that pass every threshold.
thresholds = (600.0, 750.0, 900.0, 1200.0)
common = base.filter(pl.col("minutes_nominal_equiv") >= max(thresholds))
sens_rows = []
pct_at: dict[float, dict[str, pl.Series]] = {}
for t in thresholds:
    pop = base.filter(pl.col("minutes_nominal_equiv") >= t)
    pct_at[t] = {m: local_pct(common, m, pop) for m in P90}
for t in thresholds:
    if t == GATE:
        continue
    shifts: list[float] = []
    jacc: list[float] = []
    for m in P90:
        d = (pct_at[t][m] - pct_at[GATE][m]).abs()
        shifts.extend(d.drop_nulls().to_list())
        tmp = common.select("competition_id", "position_group").with_columns(
            a=pct_at[t][m], b=pct_at[GATE][m])
        for _, sub in tmp.group_by(["competition_id", "position_group"]):
            ta = set(sub.with_row_index().filter(pl.col("a") >= 90)["index"].to_list())
            tb = set(sub.with_row_index().filter(pl.col("b") >= 90)["index"].to_list())
            if ta | tb:
                jacc.append(len(ta & tb) / len(ta | tb))
    s = sorted(shifts)
    sens_rows.append({"threshold": t, "rows": common.height,
                      "p95_abs_shift": s[int(0.95 * (len(s) - 1))], "max_abs_shift": s[-1],
                      "mean_top_decile_jaccard": sum(jacc) / len(jacc), "min_top_decile_jaccard": min(jacc)})
show(pl.DataFrame(sens_rows))

# ── D2 / D3 ───────────────────────────────────────────────────────────────────
section("D2 — within-league split seasons (nominal gate)")
comp_tot = base.group_by(["player_id", "competition_id", "season_id", "source"]).agg(
    pl.col("minutes_nominal_equiv").sum().alias("total_nominal"),
    pl.col("minutes_nominal_equiv").max().alias("max_stint_nominal"),
    pl.col("player_name").first(), pl.len().alias("stints"))
d2 = league_name(comp_tot.filter((pl.col("total_nominal") >= GATE) & (pl.col("max_stint_nominal") < GATE)))
print(f"players with competition total ≥ {GATE:.0f} nominal but no stint ≥ {GATE:.0f}: {d2.height}")
show(d2.select("player_name", "league", "stints", "total_nominal", "max_stint_nominal").sort("total_nominal", descending=True))

section("D3 — cross-competition movers")
multi = gated.group_by(["player_id", "season_id", "source"]).agg(pl.len().alias("n"), pl.col("player_name").first(),
                                                                  pl.col("competition_id"), pl.col("minutes_nominal_equiv"))
print("players with ≥ 2 gated rows (double-counted without dedupe):")
show(multi.filter(pl.col("n") >= 2))
pool_tot = base.group_by(["player_id", "season_id", "source"]).agg(
    pl.col("minutes_nominal_equiv").sum().alias("total"), pl.col("minutes_nominal_equiv").max().alias("max_row"),
    pl.col("competition_id").n_unique().alias("comps"), pl.col("player_name").first())
print("combined-only (pool rows sum ≥ gate across competitions, no row ≥ gate):")
show(pool_tot.filter((pl.col("comps") >= 2) & (pl.col("total") >= GATE) & (pl.col("max_row") < GATE)))
gpop = dedupe_global(gated)
dup_keys = multi.filter(pl.col("n") >= 2)["player_id"].to_list()
movers = gated.filter(pl.col("player_id").is_in(dup_keys))
mx = 0.0
for m in P90:
    with_dup = global_pct(movers, m, gated)
    without = global_pct(movers, m, gpop)
    mx = max(mx, (with_dup - without).abs().max() or 0.0)
others = gated.filter(~pl.col("player_id").is_in(dup_keys))
mx_o = 0.0
for m in P90:
    mx_o = max(mx_o, (global_pct(others, m, gated) - global_pct(others, m, gpop)).abs().max() or 0.0)
print(f"global percentile change from the dedupe: movers max {mx:.2f} pts, all other rows max {mx_o:.2f} pts")

# ── D5 / D6 ───────────────────────────────────────────────────────────────────
section("D5 — near-tie season groups")
q = gated["position_group_margin_pct"]
print("margin quantiles P1/P5/P10/P25:", [round(q.quantile(p), 3) for p in (0.01, 0.05, 0.10, 0.25)])
print({f"margin < {t:.0%}": gated.filter(pl.col("position_group_margin_pct") < t).height for t in (0.02, 0.05, 0.10, 0.20)})
print({f"share < {t:.0%}": gated.filter(pl.col("position_group_share") < t).height for t in (0.5, 0.6)})
amb = gated.filter(pl.col("position_group_margin_pct") < AMBIGUOUS_MARGIN)
amb2 = amb.with_columns(pl.col("second_group").alias("_g2"))
gaps = []
for m in P90:
    own = global_pct(amb2, m, gpop)
    other = global_pct(amb2.with_columns(pl.col("_g2").alias("_grp")), m, gpop, group_col="_grp")
    gaps.append((own - other).abs().rename(m))
gap_df = pl.DataFrame(gaps)
amb_out = amb.select("player_name", "team_name", "position_group", "second_group", "position_group_margin_pct",
                     "position_group_share").with_columns(
    gap_df.select(pl.concat_list(pl.all()).list.median()).to_series().alias("median_gap_pts"),
    gap_df.select(pl.concat_list(pl.all()).list.max()).to_series().alias("max_gap_pts"))
print(f"rows flagged at margin < {AMBIGUOUS_MARGIN:.0%}: {amb.height}; percentile gap own vs second group (global):")
show(amb_out.sort("position_group_margin_pct"))

section("D6 — mixed GK / outfield season rows")
mixed = season.filter((pl.col("minutes_gk") >= 45) & ((pl.col("minutes_played") - pl.col("minutes_gk")) >= 45))
print(f"season rows with ≥ 45 GK and ≥ 45 outfield minutes: {mixed.height}")
if mixed.height:
    show(mixed.select("player_name", "team_name", "minutes_gk", "minutes_played", "position_group"))

# ── Events pass (D7, D12–D17) ─────────────────────────────────────────────────
section("Raw events pass (D7, D12–D17)")
D7_MATCHES = {3754226: ("Henderson", "Coutinho", "Firmino", "Benteke", "Lallana"),
              3900509: ("Polomat",), 3829474: ("Ketkeophomphone",), 3879816: ("Totti",), 3878543: ("Hernanes",)}
pm = Counter()                    # (match, player, field) -> value
team_poss: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: {"dur": 0.0, "passes": 0})
match_poss: dict[int, dict[str, float]] = defaultdict(lambda: {"dur": 0.0, "passes": 0})
d7_events: dict[tuple[int, int], Counter] = defaultdict(Counter)
no_receipt: list[dict] = []
xga_issue = Counter()
event_minutes = Counter()          # league -> events in periods 1–4

pool_match_ids = match.select("match_id", "competition_id").unique()
for match_id, comp in pool_match_ids.iter_rows():
    events = load_json(RAW / "events" / f"{match_id}.json")
    events = [e for e in events if e.get("period", 0) in (1, 2, 3, 4)]
    event_minutes[comp] += len(events)
    by_id = {e["id"]: e for e in events}
    poss_player: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for e in events:
        pid = (e.get("player") or {}).get("id")
        if pid is not None and e.get("possession") is not None:
            poss_player[(e["possession"], pid)].append(e)
        pteam = (e.get("possession_team") or {}).get("id")
        team = (e.get("team") or {}).get("id")
        dur = e.get("duration") or 0.0
        etype = e["type"]["name"]
        if pteam is not None:
            team_poss[(match_id, pteam)]["dur"] += dur
            match_poss[match_id]["dur"] += dur
        if etype == "Pass" and team is not None:
            team_poss[(match_id, team)]["passes"] += 1
            match_poss[match_id]["passes"] += 1
        if pid is None:
            continue
        if pteam is not None and pteam == team:
            pm[(match_id, pid, "own_dur")] += dur
        if match_id in D7_MATCHES and e.get("position"):
            d7_events[(match_id, pid)][POSITION_GROUP_MAP.get(e["position"]["name"], "UNKNOWN")] += 1
        if etype == "Pass":
            pm[(match_id, pid, "own_passes")] += 1
            p = e.get("pass") or {}
            outcome = (p.get("outcome") or {}).get("name")
            ptype = (p.get("type") or {}).get("name")
            loc, end = e.get("location"), p.get("end_location")
            length = p.get("length")
            if outcome == "Injury Clearance":
                pm[(match_id, pid, "ic")] += 1
                if length is not None and length >= LONG_PASS_THRESHOLD_UNITS and ptype not in RESTART_TYPES:
                    pm[(match_id, pid, "ic_long")] += 1
            if loc and end:
                s, t = (loc[0], loc[1]), (end[0], end[1])
                # Geometry only; throw-ins are counted on their own below (D14 history).
                prog = _completed_progressive(s, t, outcome)
                box = _completed_box_entry(s, t, outcome)
                fte = _completed_final_third_entry(s, t, outcome)
                if box and prog and ptype not in RESTART_TYPES:
                    pm[(match_id, pid, "box_and_prog")] += 1
                if ptype == "Throw-in":
                    pm[(match_id, pid, "ti_prog")] += prog
                    pm[(match_id, pid, "ti_box")] += box
                    pm[(match_id, pid, "ti_fte")] += fte
                    pm[(match_id, pid, "ti_cross")] += bool(p.get("cross"))
            aid = p.get("assisted_shot_id")
            if aid:
                shot = by_id.get(aid)
                if shot is None or shot["type"]["name"] != "Shot":
                    xga_issue["dangling_assisted_shot_id"] += 1
                    continue
                if (shot.get("shot") or {}).get("statsbomb_xg") is None:
                    continue
                if (shot.get("player") or {}).get("id") is None or shot.get("possession") is None:
                    xga_issue["missing_shooter"] += 1
                if (e.get("player") or {}).get("id") is None:
                    xga_issue["missing_assister"] += 1
        elif etype == "Shot":
            sh = e.get("shot") or {}
            if (sh.get("type") or {}).get("name") == "Penalty":
                pm[(match_id, pid, "pen_shots")] += 1
                pm[(match_id, pid, "pen_xg")] += sh.get("statsbomb_xg") or 0.0
    # D16: no-receipt assisted shots, now that poss_player is complete.
    for e in events:
        if e["type"]["name"] != "Pass":
            continue
        aid = (e.get("pass") or {}).get("assisted_shot_id")
        shot = by_id.get(aid) if aid else None
        if not shot or shot["type"]["name"] != "Shot" or (shot.get("shot") or {}).get("statsbomb_xg") is None:
            continue
        sid = (shot.get("player") or {}).get("id")
        if sid is None or shot.get("possession") is None:
            continue
        if not any(x["type"]["name"] == "Ball Receipt*" and x["index"] < shot["index"]
                   for x in poss_player.get((shot["possession"], sid), [])):
            sh = shot["shot"]
            no_receipt.append({
                "league": LEAGUES[comp], "match_id": match_id, "minute": f"{shot['minute']}:{shot['second']:02d}",
                "shooter": shot["player"]["name"], "passer": e["player"]["name"],
                "pass_type": ((e["pass"].get("type") or {}).get("name")) or "open play",
                "first_time": bool(sh.get("first_time")), "technique": (sh.get("technique") or {}).get("name"),
                "body_part": (sh.get("body_part") or {}).get("name"), "xg": round(sh["statsbomb_xg"], 3),
                "same_possession_as_pass": e.get("possession") == shot.get("possession"),
                "index_gap": shot["index"] - e["index"],
            })

fields = ("own_dur", "own_passes", "ic", "ic_long", "box_and_prog", "ti_prog", "ti_box", "ti_fte", "ti_cross",
          "pen_shots", "pen_xg")
rows: dict[tuple[int, int], dict] = defaultdict(lambda: dict.fromkeys(fields, 0.0))
for (mid, pid, f), v in pm.items():
    rows[(mid, pid)][f] = v
ev = pl.DataFrame([{"match_id": k[0], "player_id": k[1], **v} for k, v in rows.items()])
mp = match.join(ev, on=["match_id", "player_id"], how="left").with_columns(pl.col(fields).fill_null(0.0))
tp = pl.DataFrame([{"match_id": k[0], "team_id": k[1], "team_dur": v["dur"], "team_passes": v["passes"]}
                   for k, v in team_poss.items()])
mt = pl.DataFrame([{"match_id": k, "match_dur": v["dur"], "match_passes": v["passes"]} for k, v in match_poss.items()])
mp = mp.join(tp, on=["match_id", "team_id"], how="left").join(mt, on="match_id", how="left").with_columns(
    ((pl.col("team_dur") - pl.col("own_dur")) / (pl.col("match_dur") - pl.col("own_dur"))).alias("poss_dur_loo"),
    ((pl.col("team_passes") - pl.col("own_passes")) / (pl.col("match_passes") - pl.col("own_passes"))).alias("poss_pass_loo"),
)
agg = mp.group_by(KEY).agg(
    *[pl.col(f).sum() for f in fields],
    ((pl.col("poss_dur_loo") * pl.col("minutes_played")).sum() / pl.col("minutes_played").sum()).alias("poss_dur_loo"),
    ((pl.col("poss_pass_loo") * pl.col("minutes_played")).sum() / pl.col("minutes_played").sum()).alias("poss_pass_loo"),
)
gated = gated.join(agg, on=KEY, how="left")
print("events per elapsed minute (periods 1–4):")
dur_by_league = match.unique("match_id").group_by("competition_id").agg(pl.col("match_duration").sum())
for comp, tot in dur_by_league.iter_rows():
    print(f"  {LEAGUES[comp]:15s} {event_minutes[comp] / tot:.2f}")

# ── D7 ────────────────────────────────────────────────────────────────────────
section("D7 — Tactical Shift patterns B/C: season group with event-implied minutes")
d7_out = []
for mid, names in D7_MATCHES.items():
    for name in names:
        r = match.filter((pl.col("match_id") == mid) & pl.col("player_name").str.contains(name))
        if r.height != 1:
            d7_out.append({"player": name, "match_id": mid, "note": f"{r.height} rows matched"})
            continue
        r = r.row(0, named=True)
        evc = d7_events[(mid, r["player_id"])]
        tot = sum(v for g, v in evc.items() if g in GROUP_COLS)
        srow = season.filter((pl.col("player_id") == r["player_id"]) & (pl.col("team_id") == r["team_id"])
                             & (pl.col("competition_id") == r["competition_id"])).row(0, named=True)
        new = {g: srow[c] - r[c] + (r["minutes_played"] * evc.get(g, 0) / tot if tot else 0) for g, c in GROUP_COLS.items()}
        new_group = max(new.items(), key=lambda kv: kv[1])[0]
        d7_out.append({"player": r["player_name"], "match_id": mid, "match_group": r["position_group"],
                       "events_group": max(evc.items(), key=lambda kv: kv[1])[0] if evc else None,
                       "season_group": srow["position_group"], "season_group_events": new_group,
                       "gated": srow["minutes_nominal_equiv"] >= GATE and srow["in_percentile_pool"],
                       "cohort_change": new_group != srow["position_group"]})
show(pl.DataFrame(d7_out))

# ── D8 ────────────────────────────────────────────────────────────────────────
section("D8 — Player On not followed by the lineup")
print("Not re-measured: ADR 002 §31.6 events_outside_credited_time attributes its only multi-event case to "
      "Milinković-Savić 3879637 (11 events, 3.5 min); the other 4 are single mistimed receipts. Count = 1.")

# ── D9 ────────────────────────────────────────────────────────────────────────
section("D9 — global_ok: league mean of global percentile within 50 ± 10 (vs median-ratio band)")
d9 = []
for m in P90:
    gp = global_pct(gated, m, gpop)
    tmp = gated.select("competition_id", "position_group", m).with_columns(gp.alias("gp"))
    for (grp,), sub in tmp.group_by(["position_group"]):
        means = sub.group_by("competition_id").agg(pl.col("gp").mean(), pl.col(m).median().alias("med"))
        lo, hi = means["gp"].min(), means["gp"].max()
        med = means["med"]
        ratio = (med.max() / med.min()) if med.min() and med.min() > 0 else float("inf")
        worst = means.sort((pl.col("gp") - 50).abs(), descending=True).row(0, named=True)
        d9.append({"metric": m, "group": grp, "min_league_mean": lo, "max_league_mean": hi,
                   "worst_league": LEAGUES[worst["competition_id"]],
                   "global_ok": lo >= 50 - GLOBAL_OK_BAND and hi <= 50 + GLOBAL_OK_BAND,
                   "median_ratio": ratio,
                   "median_band_ok": MEDIAN_RATIO_BAND[0] <= 1 / ratio and ratio <= MEDIAN_RATIO_BAND[1]})
d9 = pl.DataFrame(d9)
print(f"metric × group cells: {d9.height}; global_ok: {d9['global_ok'].sum()}; "
      f"median-band ok: {d9['median_band_ok'].sum()}; disagree: {(d9['global_ok'] != d9['median_band_ok']).sum()}")
print("global_ok per metric (groups passing out of 5):")
show(d9.group_by("metric").agg(pl.col("global_ok").sum().alias("groups_ok"),
                                pl.col("max_league_mean").max(), pl.col("min_league_mean").min())
     .sort("groups_ok"), rows=40)
print("failing cells:")
show(d9.filter(~pl.col("global_ok")).sort("metric", "group").select(
    "metric", "group", "min_league_mean", "max_league_mean", "worst_league"), rows=200)

# ── D11 ───────────────────────────────────────────────────────────────────────
section("D11 — cohort sizes and zero shares")
sizes = league_name(gated).group_by("league", "position_group").len().pivot(on="position_group", index="league", values="len")
show(sizes)
print("global (deduped):", dict(gpop.group_by("position_group").len().iter_rows()))
zero = []
for m in P90:
    for (grp,), sub in gated.group_by(["position_group"]):
        zero.append({"metric": m, "group": grp, "zero_share": (sub[m] == 0).mean()})
zero = pl.DataFrame(zero)
heavy = zero.filter(pl.col("zero_share") > ZERO_HEAVY_SHARE).sort("metric", "group")
print(f"zero-heavy metric × group (> {ZERO_HEAVY_SHARE:.0%} zeros): {heavy.height}")
show(heavy, rows=80)

# ── D12 ───────────────────────────────────────────────────────────────────────
section("D12 — leave-one-out team possession vs every volume per-90")
d12 = []
for m in P90:
    for proxy in ("poss_dur_loo", "poss_pass_loo"):
        for (grp,), sub in gated.group_by(["position_group"]):
            pooled = sub.select(pl.corr(proxy, m, method="spearman")).item()
            per = [s.select(pl.corr(proxy, m, method="spearman")).item()
                   for _, s in sub.group_by("competition_id")]
            per = [x for x in per if x is not None and not math.isnan(x)]
            mean_within = sum(per) / len(per) if per else None
            same_sign = bool(per) and (all(x > 0 for x in per) or all(x < 0 for x in per))
            d12.append({"metric": m, "proxy": proxy, "group": grp, "rho_pooled": pooled, "rho_within_mean": mean_within,
                        "min_league": min(per) if per else None, "max_league": max(per) if per else None,
                        "trigger": mean_within is not None and abs(mean_within) >= POSSESSION_RHO and same_sign})
d12 = pl.DataFrame(d12)
print(f"triggered cells (|mean within-league ρ| ≥ {POSSESSION_RHO}, same sign in all 4 leagues):")
show(d12.filter("trigger").sort("proxy", "metric", "group"), rows=200)
print("max |ρ_within_mean| per metric, duration proxy:")
show(d12.filter(pl.col("proxy") == "poss_dur_loo").group_by("metric").agg(
    pl.col("rho_within_mean").abs().max().alias("max_abs_rho")).sort("max_abs_rho", descending=True), rows=40)

# ── D13 ───────────────────────────────────────────────────────────────────────
section("D13 — Injury Clearance passes")
print("total by league:", {LEAGUES[c]: int(v) for c, v in
                           mp.group_by("competition_id").agg(pl.col("ic").sum()).iter_rows()})
d13 = gated.with_columns(
    (pl.col("passes_completed") / (pl.col("passes_attempted") - pl.col("passes_unknown_outcome") - pl.col("ic")))
    .alias("pct_new"),
    (pl.col("long_passes_completed") / (pl.col("long_passes_attempted") - pl.col("long_passes_unknown_outcome")
                                         - pl.col("ic_long"))).alias("long_new"),
)
show(d13.group_by("position_group").agg(
    pl.col("ic").sum().alias("ic_passes"), pl.col("ic").mean().alias("ic_per_row"),
    (pl.col("pct_new") - pl.col("pass_completion_pct")).mean().alias("pass_pct_shift_mean"),
    (pl.col("pct_new") - pl.col("pass_completion_pct")).max().alias("pass_pct_shift_max"),
    (pl.col("long_new") - pl.col("long_pass_completion_pct")).mean().alias("long_pct_shift_mean"),
    (pl.col("long_new") - pl.col("long_pass_completion_pct")).max().alias("long_pct_shift_max"),
).sort("position_group"))
lc = d13.with_columns(a=local_pct(d13, "pass_completion_pct"), b=local_pct(d13, "pct_new"))
print(f"local pass_completion percentile shift: max {(lc['a'] - lc['b']).abs().max():.2f} pts, "
      f"rows ≥ 1 rank step-ish (≥ 3 pts): {((lc['a'] - lc['b']).abs() >= 3).sum()}")

# ── D14 ───────────────────────────────────────────────────────────────────────
section("D14 — throw-ins inside entry / progressive / cross counts (M4a history: meaningful on pre-M4b output only; after M4b the columns already exclude restarts)")
show(gated.group_by("position_group").agg(
    (pl.col("ti_box").sum() / pl.col("box_entries_pass").sum()).alias("box_entries_pass_share"),
    (pl.col("ti_fte").sum() / pl.col("final_third_entries_pass").sum()).alias("final_third_entries_pass_share"),
    (pl.col("ti_prog").sum() / pl.col("progressive_passes").sum()).alias("progressive_passes_share"),
    (pl.col("ti_cross").sum() / pl.col("crosses_attempted").sum()).alias("crosses_share"),
    pl.col("ti_box").sum().alias("ti_box"), pl.col("ti_prog").sum().alias("ti_prog"),
).sort("position_group"))

# ── D15 ───────────────────────────────────────────────────────────────────────
section("D15 — penalties in shots and xG")
d15 = gated.with_columns(((pl.col("xg") - pl.col("pen_xg")) / pl.col("minutes_played") * 90).alias("np_xg_p90"))
show(d15.group_by("position_group").agg(
    (pl.col("pen_xg").sum() / pl.col("xg").sum()).alias("pen_share_of_xg"),
    (pl.col("pen_shots").sum() / pl.col("shots").sum()).alias("pen_share_of_shots"),
    (pl.col("pen_xg") > 0).sum().alias("rows_with_pens")).sort("position_group"))
top_xg = d15.sort("xg_p90", descending=True).head(10)["player_name"].to_list()
top_np = d15.sort("np_xg_p90", descending=True).head(10)["player_name"].to_list()
print("top-10 xg_p90:   ", top_xg)
print("top-10 np_xg_p90:", top_np)
print("left the top 10:", [p for p in top_xg if p not in top_np], "| entered:", [p for p in top_np if p not in top_xg])

# ── D16 ───────────────────────────────────────────────────────────────────────
section("D16 — xG-assisted fallbacks")
print("counters:", dict(xga_issue))
print(f"no-receipt assisted shots (counted as direct): {len(no_receipt)}")
if no_receipt:
    show(pl.DataFrame(no_receipt).sort("league", "match_id"), rows=80)

# ── D17 ───────────────────────────────────────────────────────────────────────
section("D17 — collinearity (pre-M4b columns; re-run on M4b output)")
pairs = []
for (grp,), sub in gated.group_by(["position_group"]):
    for i, a in enumerate(P90):
        for b in P90[i + 1:]:
            r = sub.select(pl.corr(a, b, method="spearman")).item()
            if r is not None and not math.isnan(r) and abs(r) >= COLLINEAR_RHO:
                pairs.append({"group": grp, "a": a, "b": b, "rho": r})
show(pl.DataFrame(pairs).sort("a", "b", "group") if pairs else pl.DataFrame({"none": []}), rows=120)
print("box_entries_pass that are also progressive (share):")
show(gated.group_by("position_group").agg(
    (pl.col("box_and_prog").sum() / pl.col("box_entries_pass").sum()).alias("box_in_progressive")).sort("position_group"))

# ── D19 ───────────────────────────────────────────────────────────────────────
section("D19 — split-half reliability (odd/even matches by date, demeaned within league × group, Spearman-Brown)")
played = match.filter(pl.col("minutes_played") > 0).sort("match_date").with_columns(
    (pl.col("match_id").cum_count().over(KEY) % 2).alias("half"))
count_cols = sorted({COUNT_OF[m] for m in P90})
ratio_cols = sorted({c for num, pos, neg in RATIOS.values() for c in [num, *pos, *neg]})
halves = played.group_by([*KEY, "half"]).agg(pl.col("minutes_played").sum(),
                                            *[pl.col(c).sum() for c in set(count_cols) | set(ratio_cols)])
season_info = season.select(*KEY, "position_group", "in_percentile_pool", "is_outfield", "minutes_nominal_equiv",
                            "player_name")
halves = halves.join(season_info, on=KEY).filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))


def split_half(df: pl.DataFrame, value_expr: pl.Expr) -> dict[str, float | None]:
    """r_full per group: halves demeaned within league × group, pooled Pearson, Spearman-Brown."""
    v = df.with_columns(value_expr.alias("_v")).select(*KEY, "position_group", "half", "_v")
    w = v.pivot(on="half", index=[*KEY, "position_group"], values="_v").drop_nulls(["0", "1"])
    w = w.with_columns((pl.col("0") - pl.col("0").mean().over("competition_id", "position_group")).alias("a"),
                       (pl.col("1") - pl.col("1").mean().over("competition_id", "position_group")).alias("b"))
    out = {}
    for (grp,), sub in w.group_by(["position_group"]):
        out[grp] = spearman_brown(pearson(sub["a"].to_list(), sub["b"].to_list()))
    return out


bins = {"450-900": (450, 900), "900-1800": (900, 1800), "≥1800": (1800, 1e9), "gated(≥900)": (900, 1e9)}
rel_rows = []
for m in P90:
    c = COUNT_OF[m]
    for bname, (lo, hi) in bins.items():
        sub = halves.filter((pl.col("minutes_nominal_equiv") >= lo) & (pl.col("minutes_nominal_equiv") < hi))
        r = split_half(sub, pl.col(c) / pl.col("minutes_played") * 90)
        for grp, val in r.items():
            rel_rows.append({"metric": m, "bin": bname, "group": grp, "r_full": val})
rel = pl.DataFrame(rel_rows)
rel_g = rel.filter(pl.col("bin") == "gated(≥900)").pivot(on="group", index="metric", values="r_full").sort("metric")
print(f"r_full at the gate (target ≥ {RELIABILITY_DNA}):")
show(rel_g, rows=40)
print("r_full by minutes bin, mean over groups:")
show(rel.group_by("metric", "bin").agg(pl.col("r_full").mean()).pivot(on="bin", index="metric", values="r_full")
     .sort("metric"), rows=40)
below = rel.filter((pl.col("bin") == "gated(≥900)") & (pl.col("r_full") < RELIABILITY_DNA))
print(f"metric × group cells below {RELIABILITY_DNA} at the gate: {below.height}")

print("\nRatios: beta-binomial M per group (pool outfield rows, n ≥ 10), min_attempts = 1.5·M "
      f"(reliability {RELIABILITY_RATIO}); split-half cross-check at the gate")
ratio_rows = []
for name, (num, pos, neg) in RATIOS.items():
    den = sum(pl.col(c) for c in pos) - (sum(pl.col(c) for c in neg) if neg else 0)
    rows_r = base.with_columns(den.alias("_n"), pl.col(num).alias("_k")).filter(pl.col("_n") >= 10)
    sh = split_half(halves.with_columns(den.alias("_n")).filter(pl.col("_n") > 0)
                    .filter(pl.col("minutes_nominal_equiv") >= GATE), pl.col(num) / den)
    for (grp,), sub in rows_r.group_by(["position_group"]):
        k, n = sub["_k"].cast(pl.Float64), sub["_n"].cast(pl.Float64)
        pbar = k.sum() / n.sum()
        p = k / n
        var_obs = ((p - pbar) ** 2).mean()
        var_bin = (pbar * (1 - pbar) / n).mean()
        var_true = var_obs - var_bin
        M = pbar * (1 - pbar) / var_true if var_true > 0 else float("inf")
        min_att = math.ceil(1.5 * M) if math.isfinite(M) else None
        g_sub = gated.filter(pl.col("position_group") == grp).with_columns(den.alias("_n"))
        ratio_rows.append({"ratio": name, "group": grp, "p_bar": pbar, "M": M, "min_attempts": min_att,
                           "gated_rows_meeting": (g_sub.filter(pl.col("_n") >= (min_att or 1e12)).height / g_sub.height),
                           "split_half_r_full": sh.get(grp)})
show(pl.DataFrame(ratio_rows).sort("ratio", "group"), rows=60)
print(f"(current MIN_AERIAL_CONTESTS = {MIN_AERIAL_CONTESTS})")
xs = split_half(halves.filter((pl.col("shots") > 0) & (pl.col("minutes_nominal_equiv") >= GATE)),
                pl.col("xg") / pl.col("shots"))
print("xg_per_shot split-half r_full at the gate:", {g: round(v, 3) if v is not None else None for g, v in xs.items()})

# ── Signature traits ──────────────────────────────────────────────────────────
section("Signature traits — zero-heavy metric × group: holders at minimum counts, reliability")
st = []
for r in heavy.iter_rows(named=True):
    sub = gated.filter(pl.col("position_group") == r["group"])
    cnt = sub[COUNT_OF[r["metric"]]]
    relv = rel.filter((pl.col("metric") == r["metric"]) & (pl.col("group") == r["group"])
                      & (pl.col("bin") == "gated(≥900)"))["r_full"]
    st.append({"metric": r["metric"], "group": r["group"], "n_gated": sub.height, "zero_share": r["zero_share"],
               **{f"count≥{t}": int((cnt >= t).sum()) for t in TRAIT_COUNTS},
               "r_full": relv.item() if relv.len() else None,
               "reliable": bool(relv.len()) and relv.item() is not None and relv.item() >= RELIABILITY_DNA})
show(pl.DataFrame(st).sort("metric", "group"), rows=80)

# ── D20 ───────────────────────────────────────────────────────────────────────
section("D20 — extremes around the gate")
sub_gate = base.filter(pl.col("minutes_nominal_equiv") < GATE)
ext = []
for m in P90:
    ext.append({"metric": m, "gated_max": gated[m].max(), "gated_p99": gated[m].quantile(0.99),
                "sub_gate_max": sub_gate[m].max()})
show(pl.DataFrame(ext).sort("metric"), rows=40)
above = set()
for m in P90:
    for (grp,), sub in gated.group_by(["position_group"]):
        p99 = sub[m].quantile(0.99)
        above |= set(sub.filter(pl.col(m) > p99)["player_name"].to_list())
print(f"distinct gated rows above their group P99 on ≥ 1 metric (to trace in M4g): {len(above)}")

# ── D12b ──────────────────────────────────────────────────────────────────────
section("D12b — team_pass_share (open play) for DM-CM and CB: possession ρ and split-half "
        "(ADR 004 D12, pre-registered: enters DNA iff |ρ| < 0.3 and r_full ≥ 0.6)")
if "team_pass_share" not in season.columns:
    print("team_pass_share not in the season parquet (pre-M4b output): skipped")
else:
    d12b = []
    tps_halves = halves.join(
        played.group_by([*KEY, "half"]).agg(pl.col("open_play_passes_attempted").sum(),
                                            pl.col("team_open_play_passes_while_on").sum()),
        on=[*KEY, "half"], how="left").filter(pl.col("team_open_play_passes_while_on") > 0)
    rel_tps = split_half(tps_halves.filter(pl.col("minutes_nominal_equiv") >= GATE),
                         pl.col("open_play_passes_attempted") / pl.col("team_open_play_passes_while_on"))
    for grp in ("DM-CM", "CB"):
        sub = gated.filter(pl.col("position_group") == grp)
        per = {}
        for proxy in ("poss_dur_loo", "poss_pass_loo"):
            vals = [s.select(pl.corr(proxy, "team_pass_share", method="spearman")).item()
                    for _, s in sub.group_by("competition_id")]
            per[proxy] = sum(vals) / len(vals)
            per[proxy + "_leagues"] = [round(v, 3) for v in vals]
        r_full = rel_tps.get(grp)
        d12b.append({"group": grp, "n": sub.height,
                     "rho_dur_mean": per["poss_dur_loo"], "rho_pass_mean": per["poss_pass_loo"],
                     "rho_dur_by_league": str(per["poss_dur_loo_leagues"]),
                     "r_full": r_full,
                     "enters_dna": abs(per["poss_dur_loo"]) < 0.3 and abs(per["poss_pass_loo"]) < 0.3
                                   and r_full is not None and r_full >= 0.6})
    show(pl.DataFrame(d12b))
    print("compare: passes_attempted_p90 ρ (duration proxy) DM-CM 0.66, CB 0.74 (ADR 004 D12)")
