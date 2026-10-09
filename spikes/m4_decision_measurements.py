"""Spike: measurements that settle the M4 decisions D1–D22 (plan 005, M4a).

Reads data/processed/player_season_stats.parquet, data/interim/player_match_stats.parquet,
matches.json (dates) and the raw events. It prints one section per D-item, and the
figures go into ADR 004. With --write-json PATH it also writes the measurements the
M4c metric registry derives its statuses from (plan 005 M4c2; committed as
src/eleveniq/analytics/measurements/statsbomb_27.json). The file holds measurements
only: every threshold is applied in eleveniq, not here.

This is a measurement, not an independent check. It never imports eleveniq
(tests/test_spikes_independent.py): the pass geometry (ADR 002 §1, §27), the position
taxonomy (§4) and the restart set (ADR 004 D14) are written out below. The D22 rates
were measured once with the pipeline (plan 005 M4a) and are recorded in ADR 004; that
section was removed with the eleveniq import (plan 005 M4b2).

"Gated" below means the D1 default: in_percentile_pool ∧ is_outfield ∧ nominal-equivalent
minutes ≥ 900 − 1e-6, where nominal = Σ_matches minutes × 90 / match_duration. The spike
recomputes it and fails unless it equals the parquet's meets_min_minutes on every row.

Usage: .venv/bin/python spikes/m4_decision_measurements.py [--write-json PATH [--possession DIR] [--trust PATH]]
(schema 3, plan 005 M4g4d: --write-json embeds the M4g4c / M4g4c2 engine outputs; see m4g4d_possession_block)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import polars as pl

import m4g2_multisplit as ms  # sibling spike: the multi-split engine (plan 005 M4g2)
import m4g3_movers as mv     # sibling spike: the mover engine (plan 005 M4g3b)
import m4g4d_possession_block as pb  # sibling spike: the possession block (plan 005 M4g4d)

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
BUCKETS_JSON = ROOT / "data" / "processed" / "reports" / "m4g2_reliability_cells.json"  # every population, report

LEAGUES = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1", 9: "Bundesliga"}
POOL_LEAGUES = (11, 2, 12, 7)
SEASON = 27
SOURCE = "statsbomb"
GATE = 900.0
GATE_TOLERANCE = 1e-6          # MIN_MINUTES_TOLERANCE (ADR 004 D1), written out
OUTFIELD = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
GROUP_COLS = {
    "GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
    "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger", "ST": "minutes_st",
}
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]

# Plan 005 thresholds under test (proposals; the ADR fixes the final values).
ZERO_HEAVY_SHARE = 0.30
AMBIGUOUS_MARGIN = 0.05
GLOBAL_OK_BAND = (40.0, 60.0)  # D9: each league's mean global percentile in the band (config.GLOBAL_OK_BAND, written out)
MEDIAN_RATIO_BAND = (0.8, 1.25)  # D9 original proposal, shown for comparison
MIN_COHORT_SIZE = 30
POSSESSION_RHO = 0.4
SIGN_NEUTRAL_RHO = 0.05        # a league with |ρ| below this has no sign in the D12 sign test (user, 2026-10-04)
COLLINEAR_RHO = 0.85
RELIABILITY_DNA = 0.6          # D19 (user, 2026-10-02); 0.6 ≤ r < 0.7 carries a "lower reliability" note
LOW_RELIABILITY_BELOW = 0.7
RELIABILITY_RATIO = 0.6        # min_attempts = ⌈1.5·M⌉ gives n / (n + M) = 0.6
RATIO_MIN_N = 10               # rows with fewer attempts are left out of the M estimate
TRAIT_COUNTS = (5, 10, 15)
COLLINEAR_REPORT_FLOOR = 0.7   # --write-json keeps every pair with |ρ| ≥ this (the D17 rule is 0.85)
BOOTSTRAP_B = 2000             # np_xg_per_shot M interval (stats-reviewer condition 2, M4c2)
BOOTSTRAP_SEED = 27
JSON_FLOAT_DECIMALS = 9        # --write-json rounds every float (ADR 004 §10.1: runs agree within 1e-9)

# Ratio metrics: (name, numerator column, denominator expression columns (+, -)).
RATIOS = {
    # Unknown outcomes and Injury Clearances leave the pass denominators (ADR 002 §14, ADR 004 D13);
    # crosses keep their Injury Clearances (D13).
    "pass_completion_pct": ("passes_completed", ["passes_attempted"],
                            ["passes_unknown_outcome", "passes_injury_clearance"]),
    "long_pass_completion_pct": ("long_passes_completed", ["long_passes_attempted"],
                                 ["long_passes_unknown_outcome", "long_passes_injury_clearance"]),
    "cross_completion_pct": ("crosses_completed", ["crosses_attempted"], ["crosses_unknown_outcome"]),
    "dribble_success_pct": ("dribbles_completed", ["dribbles_attempted"], []),
    "tackle_success_pct": ("tackles_won", ["tackles_attempted"], []),
    "aerial_win_pct": ("aerials_won", ["aerials_won", "aerials_lost"], []),
    # ADR 004 §13.2 (M4g2): a lost tackle and a Dribbled Past are both failures.
    "defensive_duel_success_pct": ("tackles_won", ["tackles_attempted", "dribbled_past"], []),
}
RATIOS_ALL = {**RATIOS, "np_xg_per_shot": ("np_xg", ["np_shots"], [])}   # variance-components M (M4c2)
# Multi-split r_full (plan 005 M4g2). The K target applies to cells whose status depends on r:
# not fixed display, not D17-dropped (written out from the registry; tests/test_registry.py keeps
# them equal). Minute buckets partition the gated rows on nominal minutes (report only).
FIXED_DISPLAY_COLUMNS = frozenset({
    "assists_p90", "carries_p90", "cross_completion_pct", "crosses_attempted_p90", "dispossessed_p90",
    "dribble_success_pct", "goals_p90", "key_passes_p90", "miscontrols_p90", "op_assists_p90",
    "passes_attempted_p90", "passes_completed_p90", "restart_assists_p90", "restart_xg_assisted_p90",
    "shots_on_target_p90", "shots_p90", "switches_p90", "tackle_success_pct", "xg_assisted_direct_p90",
    "xg_assisted_p90", "xg_p90", "xg_per_shot"})
D17_DROPPED = frozenset({   # a literal, so tests read it with ast.literal_eval
    ("tackles_won_p90", "CB"), ("tackles_won_p90", "FB-WB"), ("tackles_won_p90", "DM-CM"),
    ("tackles_won_p90", "AM-Winger"), ("tackles_won_p90", "ST"),
    ("dribbles_completed_p90", "CB"), ("dribbles_completed_p90", "FB-WB"),
    ("dribbles_completed_p90", "DM-CM"), ("dribbles_completed_p90", "AM-Winger"),
    ("dribbles_completed_p90", "ST"),
    ("final_third_entries_carry_p90", "FB-WB"), ("final_third_entries_carry_p90", "AM-Winger"),
    ("final_third_entries_carry_p90", "ST"),
    ("final_third_entries_pass_p90", "DM-CM")})
TEAM_PASS_SHARE_GROUPS = ("DM-CM", "CB")
MINUTE_BUCKETS = {"900-1350": (None, 1350.0), "1350-2000": (1350.0, 2000.0), ">=2000": (2000.0, None)}
SIGNATURE_COUNT_QUANTILE = 0.9  # --write-json: each cell's P90 of the season count (inverse ECDF), for the trait minimum


def ratio_den(pos: list[str], neg: list[str]) -> pl.Expr:
    return sum(pl.col(c) for c in pos) - (sum(pl.col(c) for c in neg) if neg else 0)


def ratio_value(num: str, pos: list[str], neg: list[str]) -> pl.Expr:
    """The spike's own ratio; null when the denominator is 0 (as in the pipeline)."""
    den = ratio_den(pos, neg)
    return pl.when(den > 0).then(pl.col(num) / den).otherwise(None)


parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--write-json", type=Path, metavar="PATH",
                    help="also write the registry measurements (plan 005 M4c2) to PATH")
parser.add_argument("--possession", type=Path, metavar="DIR",
                    default=ROOT / "data" / "interim" / "m4g4c",
                    help="with --write-json: the M4g4c engine outputs (possession.json, movers.json)")
parser.add_argument("--trust", type=Path, metavar="PATH",
                    default=ROOT / "data" / "interim" / "m4g4c2" / "trust.json",
                    help="with --write-json: the M4g4c2 trust check output")
ARGS = parser.parse_args()


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
# The parquet's own minutes_nominal_equiv is set aside: every gate below uses the spike's value.
season = season.rename({"minutes_nominal_equiv": "pipeline_minutes_nominal_equiv"}).join(nominal, on=KEY, how="left")
season = season.with_columns(
    (pl.col("minutes_nominal_equiv") >= GATE - GATE_TOLERANCE).fill_null(False).alias("gate"))
gate_mismatch = season.filter(pl.col("gate").ne_missing(pl.col("meets_min_minutes")))  # a null mismatches
if gate_mismatch.height:
    print(f"FAIL: recomputed gate ≠ meets_min_minutes on {gate_mismatch.height} rows:")
    print(gate_mismatch.select(*KEY, "player_name", "minutes_nominal_equiv", "pipeline_minutes_nominal_equiv",
                               "gate", "meets_min_minutes"))
    sys.exit(1)
print(f"gate check: recomputed nominal ≥ {GATE:.0f} − {GATE_TOLERANCE:g} equals meets_min_minutes on all "
      f"{season.height} rows ({season['gate'].sum()} true)")

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
gated = base.filter("gate")


def dedupe_population(df: pl.DataFrame, by: tuple[str, ...] = ("position_group",)) -> pl.DataFrame:
    """D3: each reference population holds one row per (player_id, season_id, source): most minutes,
    tie → lowest competition_id, then lowest team_id. A population is a position group (global) or a
    league × position group (local: by=("competition_id", "position_group")), so the dedupe runs after
    the group split (user, 2026-10-04, ADR 004 §10.1): a mover whose two rows are in different groups
    (2015/16: Doucouré, Rennes AM-Winger / Granada DM-CM) keeps both, one in each population."""
    return (df.sort(["minutes_played", "competition_id", "team_id"], descending=[True, False, False])
              .unique(subset=["player_id", "season_id", "source", *by], keep="first", maintain_order=True))


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
gpop = dedupe_population(gated)
lpop = dedupe_population(gated, by=("competition_id", "position_group"))   # local populations (D3 amended)
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
np_shot_xg: list[dict] = []        # one row per non-penalty shot (np_xg_per_shot M, M4c2)
np_shot_null_xg: list[dict] = []

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
            else:
                shot_row = {"match_id": match_id, "player_id": pid, "shot_team_id": team,
                            "xg": sh.get("statsbomb_xg")}
                (np_shot_null_xg if shot_row["xg"] is None else np_shot_xg).append(shot_row)
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
                       "gated": srow["gate"] and srow["in_percentile_pool"],
                       "cohort_change": new_group != srow["position_group"]})
show(pl.DataFrame(d7_out))

# ── D8 ────────────────────────────────────────────────────────────────────────
section("D8 — Player On not followed by the lineup")
print("Not re-measured: ADR 002 §31.6 events_outside_credited_time attributes its only multi-event case to "
      "Milinković-Savić 3879637 (11 events, 3.5 min); the other 4 are single mistimed receipts. Count = 1.")

# ── D9 ────────────────────────────────────────────────────────────────────────
section("D9 — global_ok: every league mean of global percentile in [40, 60] (vs median-ratio band)")
d9 = []
d9_league_means: dict[tuple[str, str], dict[str, float]] = {}   # (metric, group) -> {competition_id: mean}


def league_mean_global_pct(sub: pl.DataFrame, col: str, pop: pl.DataFrame) -> dict[str, float]:
    """D9: each pool league's mean global percentile of `col` (sub and pop hold one group)."""
    gp = global_pct(sub, col, pop)
    means = sub.select("competition_id").with_columns(gp.alias("gp")).group_by("competition_id").agg(pl.col("gp").mean())
    return {str(c): v for c, v in sorted(means.iter_rows())}


for m in P90:
    gp = global_pct(gated, m, gpop)
    tmp = gated.select("competition_id", "position_group", m).with_columns(gp.alias("gp"))
    for (grp,), sub in tmp.group_by(["position_group"]):
        means = sub.group_by("competition_id").agg(pl.col("gp").mean(), pl.col(m).median().alias("med"))
        d9_league_means[(m, grp)] = {str(c): v for c, v in sorted(means.select("competition_id", "gp").iter_rows())}
        lo, hi = means["gp"].min(), means["gp"].max()
        med = means["med"]
        ratio = (med.max() / med.min()) if med.min() and med.min() > 0 else float("inf")
        worst = means.sort((pl.col("gp") - 50).abs(), descending=True).row(0, named=True)
        d9.append({"metric": m, "group": grp, "min_league_mean": lo, "max_league_mean": hi,
                   "worst_league": LEAGUES[worst["competition_id"]],
                   "global_ok": lo >= GLOBAL_OK_BAND[0] and hi <= GLOBAL_OK_BAND[1],
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
def possession_rho(sub: pl.DataFrame, col: str, proxy: str) -> dict:
    """D12: Spearman ρ of `col` with a possession proxy within each pool league, and their mean.

    The mean is defined only when ρ is defined in all four pool leagues (user, 2026-10-04, ADR 004
    §10.3); otherwise it is null and the cell cannot be tested. n_by_league holds the rows used."""
    per, n_by = {}, {}
    for (comp,), s in sorted(sub.select("competition_id", proxy, col).drop_nulls().group_by(["competition_id"]),
                             key=lambda kv: kv[0]):
        n_by[str(comp)] = s.height
        x = s.select(pl.corr(proxy, col, method="spearman")).item()
        if x is not None and not math.isnan(x):
            per[str(comp)] = x
    vals = list(per.values())
    complete = sorted(per) == sorted(str(c) for c in POOL_LEAGUES)
    signed = [x for x in vals if abs(x) >= SIGN_NEUTRAL_RHO]
    return {"rho_within_mean": sum(vals) / len(vals) if complete else None, "by_league": per,
            "n_by_league": n_by,
            # printed trigger only; --write-json drops it and the registry applies the sign test itself
            "same_sign": complete and bool(signed) and (all(x > 0 for x in signed) or all(x < 0 for x in signed)),
            "rho_pooled": sub.select(pl.corr(proxy, col, method="spearman")).item()}


d12 = []
d12_detail: dict[tuple[str, str, str], dict] = {}   # (metric, proxy, group) -> possession_rho()
for m in P90:
    for proxy in ("poss_dur_loo", "poss_pass_loo"):
        for (grp,), sub in gated.group_by(["position_group"]):
            pr = d12_detail[(m, proxy, grp)] = possession_rho(sub, m, proxy)
            per, mean_within = list(pr["by_league"].values()), pr["rho_within_mean"]
            d12.append({"metric": m, "proxy": proxy, "group": grp, "rho_pooled": pr["rho_pooled"],
                        "rho_within_mean": mean_within,
                        "min_league": min(per) if per else None, "max_league": max(per) if per else None,
                        "trigger": mean_within is not None and abs(mean_within) >= POSSESSION_RHO and pr["same_sign"]})
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
pairs = []   # per-90 pairs here; ratio and team_pass_share pairs are added after the ratio section (D19)
for (grp,), sub in gated.group_by(["position_group"]):
    for i, a in enumerate(P90):
        for b in P90[i + 1:]:
            r = sub.select(pl.corr(a, b, method="spearman")).item()
            if r is not None and not math.isnan(r) and abs(r) >= COLLINEAR_REPORT_FLOOR:
                pairs.append({"group": grp, "a": a, "b": b, "rho": r})
pairs.sort(key=lambda d: (d["a"], d["b"], d["group"]))
over = [d for d in pairs if abs(d["rho"]) >= COLLINEAR_RHO]
show(pl.DataFrame(over) if over else pl.DataFrame({"none": []}), rows=120)
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
                            "gate", "player_name")
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


# Odd/even r_full at the gate: the comparator for the multi-split before/after (plan 005 M4g2).
rel_rows = []
for m in P90:
    r = split_half(halves.filter("gate"), pl.col(COUNT_OF[m]) / pl.col("minutes_played") * 90)
    for grp, val in r.items():
        rel_rows.append({"metric": m, "group": grp, "r_full_odd_even": val})
rel = pl.DataFrame(rel_rows)

# ── Multi-split r_full (plan 005 M4g2, ADR 004 §13.1) ─────────────────────────
section("D19 — multi-split r_full (plan 005 M4g2: random equal halves per stint, Spearman-Brown per "
        "split, mean over K splits)")
ms_rows = (played.join(season_info.filter(pl.col("in_percentile_pool") & pl.col("is_outfield")), on=KEY)
           .sort([*KEY, "match_date", "match_id"]))
ms_stints = (base.select(*KEY, "position_group", "gate", "minutes_nominal_equiv").sort(KEY)
             .join(ms_rows.group_by(KEY, maintain_order=True).len("n_matches"), on=KEY, how="left"))
if ms_stints["n_matches"].null_count() or ms_stints["n_matches"].min() < 1:
    sys.exit("FAIL: a pool outfield stint has no played match (the split spec needs n ≥ 1)")
ms_cols = sorted({"minutes_played", *count_cols, *ratio_cols, "np_xg", "np_shots",
                  "open_play_passes_attempted", "team_open_play_passes_while_on"})
starts = np.concatenate([[0], np.cumsum(ms_stints["n_matches"].to_numpy())[:-1]])
nom = ms_stints["minutes_nominal_equiv"].to_numpy()
gate_mask = ms_stints["gate"].to_numpy()
populations = {"gate": gate_mask}
for bname, (lo, hi) in MINUTE_BUCKETS.items():
    populations[bname] = gate_mask & ((nom >= lo) if lo is not None else True) & ((nom < hi) if hi is not None else True)
ms_data = ms.Data(columns=ms_cols, values=ms_rows.select(ms_cols).cast(pl.Float64).to_numpy(),
                  starts=starts.astype(np.int64), n=ms_stints["n_matches"].to_numpy().astype(np.int64),
                  league=ms_stints["competition_id"].to_numpy(),
                  group=ms_stints["position_group"].to_numpy().astype(str), populations=populations)
ms_measures = ([ms.Measure(m, "per90", COUNT_OF[m], ("minutes_played",)) for m in P90]
               + [ms.Measure(r, "ratio", num, tuple(pos), tuple(neg)) for r, (num, pos, neg) in RATIOS_ALL.items()]
               + [ms.Measure("team_pass_share", "share", "open_play_passes_attempted",
                             ("team_open_play_passes_while_on",))])


def ms_judged(grp: str, name: str) -> bool:
    if name == "team_pass_share":
        return grp in TEAM_PASS_SHARE_GROUPS
    return name not in FIXED_DISPLAY_COLUMNS and (name, grp) not in D17_DROPPED


ms_res = ms.run(ms_data, ms_measures, [*OUTFIELD, ms.ALL_GROUPS], ms_judged)
print(f"K = {ms_res.k} (start {ms.K_START}, cap {ms.K_CAP}, MC SE target {ms.MC_SE_TARGET}; "
      f"reached cap: {ms_res.reached_cap}); seed {ms.SEED}")
# r undefined in some splits fails only for judged gated cells (plan 005 M4g2 amendment, 2026-10-07):
# report-only buckets and non-judged cells (CB restart_assists_p90, fixed display) keep the statistics
# over their defined splits, with n_undefined.
ms_summary = {cell: ms_res.summary(cell, allow_partial=not (cell[0] == "gate" and cell[1] != ms.ALL_GROUPS
                                                            and ms_judged(cell[1], cell[2])))
              for cell in ms_res.r_full}
odd_even_of = {(r["metric"], r["group"]): r["r_full_odd_even"] for r in rel.iter_rows(named=True)}


def ms_fields(prefix: str, name: str, grp: str, odd_even: float | None) -> dict:
    """The stored multi-split fields of a gated cell: <prefix> is the mean r_full (what the registry
    reads), with the odd/even comparator, SD, MC SE, mean r_k and the n range (plan 005 M4g2)."""
    sm = ms_summary[("gate", grp, name)]
    return {prefix: sm["r_full"], f"{prefix}_odd_even": odd_even, f"{prefix}_sd": sm["sd"],
            f"{prefix}_mc_se": sm["mc_se"], f"{prefix}_r_k_mean": sm["r_k_mean"],
            f"{prefix}_n_undefined": sm["n_undefined"],
            f"{prefix}_n_min": sm["n_min"], f"{prefix}_n_median": sm["n_median"], f"{prefix}_n_max": sm["n_max"],
            f"{prefix}_share_present_every_split": sm["share_present_every_split"]}

cmp_rows = []
for m in P90:
    for grp in OUTFIELD:
        sm = ms_summary[("gate", grp, m)]
        oe = odd_even_of.get((m, grp))
        z = ((oe - sm["r_full"]) / sm["sd"] if oe is not None and sm["r_full"] is not None and sm["sd"]
             else None)
        cmp_rows.append({"metric": m, "group": grp, "odd_even": oe, "multi_split": sm["r_full"],
                         "sd": sm["sd"], "mc_se": sm["mc_se"], "z_odd_even": z,
                         "judged": ms_judged(grp, m)})
cmp = pl.DataFrame(cmp_rows)
print(f"per-90 r_full at the gate, odd/even vs multi-split (z = (odd/even − mean) / SD; target ≥ {RELIABILITY_DNA}):")
show(cmp.sort("group", "metric"), rows=400)
ms_over = [(c, v["mc_se"]) for c, v in ms_summary.items()
        if c[0] == "gate" and c[1] != ms.ALL_GROUPS and ms_judged(c[1], c[2])
        and v["mc_se"] is not None and v["mc_se"] > ms.MC_SE_TARGET]
print(f"judged gated cells above the MC SE target at K = {ms_res.k}: {len(ms_over)}")
for c, v in sorted(ms_over):
    print(f"  {c[1]:10s} {c[2]:32s} MC SE {v:.4f}  r_full {ms_summary[c]['r_full']:.4f}")
print("minute buckets (report only; no status change follows): multi-split r_full per bucket, n = stints with "
      "both halves (median over splits)")
bucket_rows = []
for nm in [m.name for m in ms_measures]:
    for grp in [*OUTFIELD, ms.ALL_GROUPS]:
        row = {"metric": nm, "group": grp}
        for bname in MINUTE_BUCKETS:
            sm = ms_summary[(bname, grp, nm)]
            row[bname] = sm["r_full"]
            row[f"n_{bname}"] = sm["n_median"]
        bucket_rows.append(row)
show(pl.DataFrame(bucket_rows).filter(pl.col("metric").is_in(P90)).sort("metric", "group"), rows=400)

# ── np_xg_per_shot: per-shot xG for the variance-components M (M4c2) ────────────
# Stats-reviewer conditions (M4c2): raw scale, pooled within-row variance (MSW) in the same
# moment form as the beta-binomial; the ANOVA, per-row-variance and log-scale variants and
# M at n ≥ 5 are printed and recorded but not used; a bootstrap interval records how
# stable M is. Guards: no null xG, every shot maps to a match row of the shooter's team, and
# per row the shot count and xG sum reconcile with the season parquet's np_shots / np_xg.
print("\nnp_xg_per_shot: per-shot non-penalty xG from the raw events (periods 1–4)")
if np_shot_null_xg:
    print(f"FAIL: {len(np_shot_null_xg)} non-penalty shots have no statsbomb_xg:", np_shot_null_xg[:10])
    sys.exit(1)
shots_np = pl.DataFrame(np_shot_xg).join(match.select("match_id", *KEY), on=["match_id", "player_id"], how="left")
unmapped = shots_np.filter(pl.col("team_id").is_null())
wrong_team = shots_np.filter(pl.col("team_id").is_not_null() & (pl.col("shot_team_id") != pl.col("team_id")))
if unmapped.height or wrong_team.height:
    print(f"FAIL: {unmapped.height} shots map to no match row, {wrong_team.height} to a row of another team")
    print(unmapped.head(10), wrong_team.head(10))
    sys.exit(1)
per_row_shots = shots_np.group_by(KEY).agg(
    pl.len().alias("n_shots"), pl.col("xg").sum().alias("sum_xg"), pl.col("xg").mean().alias("mean_xg"),
    ((pl.col("xg") - pl.col("xg").mean()) ** 2).sum().alias("ss_xg"),
    pl.col("xg").log().mean().alias("mean_log_xg"),
    ((pl.col("xg").log() - pl.col("xg").log().mean()) ** 2).sum().alias("ss_log_xg"),
    pl.col("xg").min().alias("min_xg"),
)
if shots_np["xg"].min() <= 0:
    print("FAIL: a non-penalty shot has xG ≤ 0, so the log-scale variant is undefined")
    sys.exit(1)
recon = season.select(*KEY, "np_shots", "np_xg").join(per_row_shots, on=KEY, how="full", coalesce=True).with_columns(
    pl.col("n_shots").fill_null(0), pl.col("sum_xg").fill_null(0.0))
bad = recon.filter((pl.col("np_shots").is_null()) | (pl.col("n_shots") != pl.col("np_shots"))
                   | ((pl.col("sum_xg") - pl.col("np_xg")).abs() > 1e-9))
if bad.height:
    print(f"FAIL: {bad.height} season rows do not reconcile with the per-shot events (np_shots exact, np_xg 1e-9):")
    print(bad.head(20))
    sys.exit(1)
print(f"  {shots_np.height} non-penalty shots; null xG 0; unmapped 0; team mismatches 0; "
      f"{recon.filter(pl.col('n_shots') > 0).height} season rows reconcile with np_shots (exact) and np_xg "
      f"(max |Δ| {(recon['sum_xg'] - recon['np_xg']).abs().max():.2g}); min shot xG {shots_np['xg'].min():.4g}")
shot_rows = (per_row_shots.join(base.select(*KEY, "position_group"), on=KEY, how="inner").sort(KEY))


def variance_components(n: list[int], mean: list[float], ss: list[float]) -> dict:
    """One-way random effects over rows: per-shot noise σ²_w (pooled, MSW), between-row τ², M = σ²_w / τ²."""
    k, N = len(n), sum(n)
    msw = sum(ss) / sum(ni - 1 for ni in n)
    xbar = sum(ni * mi for ni, mi in zip(n, mean)) / N
    var_obs = sum((mi - xbar) ** 2 for mi in mean) / k
    tau2 = var_obs - sum(msw / ni for ni in n) / k
    msb = sum(ni * (mi - xbar) ** 2 for ni, mi in zip(n, mean)) / (k - 1)
    n0 = (N - sum(ni * ni for ni in n) / N) / (k - 1)
    tau2_anova = (msb - msw) / n0
    tau2_row = var_obs - sum(si / (ni - 1) / ni for ni, si in zip(n, ss)) / k
    return {"rows": k, "shots": N, "mean": xbar, "sigma2_w": msw, "tau2": tau2,
            "M": msw / tau2 if tau2 > 0 else None,
            "M_anova": msw / tau2_anova if tau2_anova > 0 else None,
            "M_row_variance": msw / tau2_row if tau2_row > 0 else None}


def quantile_inf(vals: list[float], q: float) -> float | None:
    """Nearest-rank quantile; None stands for +∞ (no signal)."""
    v = sorted(math.inf if x is None else x for x in vals)
    x = v[round(q * (len(v) - 1))]
    return None if math.isinf(x) else x


np_vc: dict[str, dict] = {}
for grp in OUTFIELD:
    g = shot_rows.filter(pl.col("position_group") == grp)
    g10 = g.filter(pl.col("n_shots") >= RATIO_MIN_N)
    n, mu, ss = g10["n_shots"].to_list(), g10["mean_xg"].to_list(), g10["ss_xg"].to_list()
    out = variance_components(n, mu, ss)
    out["M_log_scale"] = variance_components(n, g10["mean_log_xg"].to_list(), g10["ss_log_xg"].to_list())["M"]
    g5 = g.filter(pl.col("n_shots") >= 5)
    out["M_min_n5"] = variance_components(g5["n_shots"].to_list(), g5["mean_xg"].to_list(), g5["ss_xg"].to_list())["M"]
    rng = random.Random(f"{BOOTSTRAP_SEED}-{grp}")
    boot = []
    for _ in range(BOOTSTRAP_B):
        idx = [rng.randrange(len(n)) for _ in range(len(n))]
        boot.append(variance_components([n[i] for i in idx], [mu[i] for i in idx], [ss[i] for i in idx])["M"])
    out["bootstrap"] = {"B": BOOTSTRAP_B, "seed": f"{BOOTSTRAP_SEED}-{grp}",
                        "M_p05": quantile_inf(boot, 0.05), "M_p50": quantile_inf(boot, 0.50),
                        "M_p95": quantile_inf(boot, 0.95),
                        "no_signal_share": sum(b is None for b in boot) / BOOTSTRAP_B}
    np_vc[grp] = out

print(f"\nRatios: M per group (pool outfield rows, n ≥ {RATIO_MIN_N}; beta-binomial, or variance components for "
      f"np_xg_per_shot), min_attempts = ⌈1.5·M⌉ (reliability {RELIABILITY_RATIO}); coverage = gated rows meeting it; "
      "split-half cross-check at the gate. Reference populations (ADR 004 D19 clarification, M4c2): gated rows "
      f"meeting min_attempts, one row per player per population (D3); a cell below MIN_COHORT_SIZE {MIN_COHORT_SIZE} "
      "is flagged.")
ratio_rows = []
ratio_json: dict[str, dict] = {}
ratio_min_att: dict[tuple[str, str], int | None] = {}
for name, (num, pos, neg) in RATIOS_ALL.items():
    den = sum(pl.col(c) for c in pos) - (sum(pl.col(c) for c in neg) if neg else 0)
    rows_r = base.with_columns(den.alias("_n"), pl.col(num).alias("_k")).filter(pl.col("_n") >= RATIO_MIN_N)
    sh = split_half(halves.with_columns(den.alias("_n")).filter(pl.col("_n") > 0)
                    .filter("gate"), pl.col(num) / den)
    ratio_json[name] = {"numerator": num, "denominator_plus": pos, "denominator_minus": neg, "groups": {}}
    for grp in OUTFIELD:
        if name == "np_xg_per_shot":
            vc = np_vc[grp]
            pbar, M = vc["mean"], vc["M"]
        else:
            sub = rows_r.filter(pl.col("position_group") == grp)
            k, n = sub["_k"].cast(pl.Float64), sub["_n"].cast(pl.Float64)
            pbar = k.sum() / n.sum()
            p = k / n
            var_obs = ((p - pbar) ** 2).mean()
            var_bin = (pbar * (1 - pbar) / n).mean()
            var_true = var_obs - var_bin
            M = pbar * (1 - pbar) / var_true if var_true > 0 else None
        min_att = math.ceil(1.5 * M) if M is not None else None
        g_sub = gated.filter(pl.col("position_group") == grp).with_columns(den.alias("_n"),
                                                                            (pl.col(num) / den).alias("_v"))
        meets = (pl.col("_n") >= min_att) if min_att is not None else pl.lit(False)
        meeting = g_sub.filter(meets)
        ratio_min_att[(name, grp)] = min_att
        # Filter by min_attempts first, then dedupe (user, 2026-10-04): a mover whose most-minutes row
        # misses min_attempts is represented by his other row in the group if that one meets it.
        ref_global = dedupe_population(meeting)
        ref_l = dedupe_population(meeting, by=("competition_id", "position_group"))
        ref_local = {str(c): ref_l.filter(pl.col("competition_id") == c).height for c in POOL_LEAGUES}
        cell = {"n_gated": g_sub.height, "n_estimate_rows": rows_r.filter(pl.col("position_group") == grp).height,
                "p_bar": pbar, "M": M, "no_signal": M is None, "min_attempts": min_att,
                "coverage": meeting.height / g_sub.height,
                **ms_fields("split_half_r_full", name, grp, sh.get(grp)),
                "ref_n_global": ref_global.height, "ref_n_local": ref_local}
        if meeting.height:
            cell["league_mean_global_pct"] = league_mean_global_pct(meeting, "_v", ref_global)
            cell["possession"] = {proxy: possession_rho(ref_global, "_v", proxy)
                                  for proxy in ("poss_dur_loo", "poss_pass_loo")}
        if name == "np_xg_per_shot":
            cell["variance_components"] = np_vc[grp]
        ratio_json[name]["groups"][grp] = cell
        ratio_rows.append({"ratio": name, "group": grp, "p_bar": pbar, "M": M, "min_attempts": min_att,
                           "gated_rows_meeting": cell["coverage"], "split_half_r_full": cell["split_half_r_full"],
                           "ref_n_global": ref_global.height, "ref_n_local_min": min(ref_local.values()),
                           "below_min_cohort": ref_global.height < MIN_COHORT_SIZE
                                               or min(ref_local.values()) < MIN_COHORT_SIZE})
show(pl.DataFrame(ratio_rows).sort("ratio", "group"), rows=60)

# D17 over ratios and team_pass_share (stats-reviewer, user 2026-10-04): each ratio only on gated rows
# meeting its min_attempts in that group (null otherwise); pairs use rows where both are non-null.
EXTRA_COLS = [*RATIOS_ALL, "team_pass_share"]
for grp in OUTFIELD:
    sub = gated.filter(pl.col("position_group") == grp).select(
        *P90, "team_pass_share",
        *[pl.when(ratio_den(pos, neg) >= (ratio_min_att[(name, grp)] or math.inf))
            .then(ratio_value(num, pos, neg)).alias(name) for name, (num, pos, neg) in RATIOS_ALL.items()])
    for a in EXTRA_COLS:
        for b in [*P90, *EXTRA_COLS[EXTRA_COLS.index(a) + 1:]]:
            ab = sub.select(a, b).drop_nulls()
            r = ab.select(pl.corr(a, b, method="spearman")).item() if ab.height >= 3 else None
            if r is not None and not math.isnan(r) and abs(r) >= COLLINEAR_REPORT_FLOOR:
                pairs.append({"group": grp, "a": a, "b": b, "rho": r})
pairs.sort(key=lambda d: (d["a"], d["b"], d["group"]))
extra_over = [d for d in pairs if abs(d["rho"]) >= COLLINEAR_RHO and (d["a"] in EXTRA_COLS or d["b"] in EXTRA_COLS)]
print(f"D17 over ratios (rows meeting min_attempts) and team_pass_share, |ρ| ≥ {COLLINEAR_RHO}:")
show(pl.DataFrame(extra_over) if extra_over else pl.DataFrame({"none": []}), rows=60)
print(f"(current MIN_AERIAL_CONTESTS = {MIN_AERIAL_CONTESTS})")
print("np_xg_per_shot variance components (raw scale, pooled σ²_w; M_anova / M_row_variance / M_log_scale / "
      "M_min_n5 recorded, not used):")
show(pl.DataFrame([{"group": g, **{k: v for k, v in d.items() if k != "bootstrap"},
                    **{f"boot_{k}": v for k, v in d["bootstrap"].items() if k not in ("B", "seed")}}
                   for g, d in np_vc.items()]))

# ── Signature traits ──────────────────────────────────────────────────────────
section("Signature traits — zero-heavy metric × group: holders at minimum counts, reliability")
st = []
for r in heavy.iter_rows(named=True):
    sub = gated.filter(pl.col("position_group") == r["group"])
    cnt = sub[COUNT_OF[r["metric"]]]
    relv = pl.Series([ms_summary[("gate", r["group"], r["metric"])]["r_full"]])
    st.append({"metric": r["metric"], "group": r["group"], "n_gated": sub.height, "zero_share": r["zero_share"],
               **{f"count≥{t}": int((cnt >= t).sum()) for t in TRAIT_COUNTS},
               "r_full": relv.item() if relv.len() else None,
               "reliable": bool(relv.len()) and relv.item() is not None and relv.item() >= RELIABILITY_DNA})
show(pl.DataFrame(st).sort("metric", "group"), rows=80)

# ── D20 ───────────────────────────────────────────────────────────────────────
section("D20 — extremes around the gate")
sub_gate = base.filter(~pl.col("gate"))
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
    # The registry requires the D12b measurements (plan 005 M4c3): a file without them
    # would silently leave team_pass_share out of DM-CM DNA.
    if ARGS.write_json:
        sys.exit("team_pass_share not in the season parquet (pre-M4b output): --write-json needs it (D12b)")
    print("team_pass_share not in the season parquet (pre-M4b output): skipped")
else:
    d12b = []
    tps_halves = halves.join(
        played.group_by([*KEY, "half"]).agg(pl.col("open_play_passes_attempted").sum(),
                                            pl.col("team_open_play_passes_while_on").sum()),
        on=[*KEY, "half"], how="left").filter(pl.col("team_open_play_passes_while_on") > 0)
    rel_tps = split_half(tps_halves.filter("gate"),
                         pl.col("open_play_passes_attempted") / pl.col("team_open_play_passes_while_on"))
    d12b_json: dict[str, dict] = {}
    for grp in ("DM-CM", "CB"):   # pre-registered for these two groups only (ADR 004 D12)
        sub = gated.filter(pl.col("position_group") == grp)
        per = {}
        for proxy in ("poss_dur_loo", "poss_pass_loo"):
            per[proxy + "_detail"] = possession_rho(sub, "team_pass_share", proxy)
            per[proxy] = per[proxy + "_detail"]["rho_within_mean"]
        r_full = ms_summary[("gate", grp, "team_pass_share")]["r_full"]
        # D9 as for the per-90s: gated rows of the group scored against its deduped global
        # population (plan 005 M4c3: the registry needs global_ok for the DM-CM DNA cell).
        tps_means = league_mean_global_pct(sub, "team_pass_share", gpop.filter(pl.col("position_group") == grp))
        d12b_json[grp] = {"n_gated": sub.height,
                          **ms_fields("split_half_r_full", "team_pass_share", grp, rel_tps.get(grp)),
                          "league_mean_global_pct": tps_means,
                          "possession": {proxy: per[proxy + "_detail"] for proxy in ("poss_dur_loo", "poss_pass_loo")}}
        d12b.append({"group": grp, "n": sub.height,
                     "rho_dur_mean": per["poss_dur_loo"], "rho_pass_mean": per["poss_pass_loo"],
                     "rho_dur_by_league": str([round(v, 3) for v in per["poss_dur_loo_detail"]["by_league"].values()]),
                     "r_full": r_full,
                     "d9_league_means": str({c: round(v, 2) for c, v in tps_means.items()}),
                     "enters_dna": abs(per["poss_dur_loo"]) < 0.3 and abs(per["poss_pass_loo"]) < 0.3
                                   and r_full is not None and r_full >= 0.6})
    show(pl.DataFrame(d12b))
    print("compare: passes_attempted_p90 ρ (duration proxy) DM-CM 0.66, CB 0.74 (ADR 004 D12)")

# ── --write-json: the registry measurements (plan 005 M4c2) ───────────────────
if ARGS.write_json:
    def sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    def round_floats(obj):
        """Every stored float to JSON_FLOAT_DECIMALS, so two runs write identical bytes: float sums
        differ between runs in the last digits (≤ 1e-12), and REGISTRY_VERSION hashes the values.
        + 0.0 turns -0.0 into 0.0, so noise around zero cannot flip a sign."""
        if isinstance(obj, float):
            return round(obj, JSON_FLOAT_DECIMALS) + 0.0
        if isinstance(obj, dict):
            return {k: round_floats(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [round_floats(v) for v in obj]
        return obj

    def count_key(t) -> str:
        """A holders_at_count key. An xG count's P90 is a float key, which round_floats does not
        reach: round it the same way so the key cannot change between runs (the count of holders
        still uses the exact threshold)."""
        return str(round(t, JSON_FLOAT_DECIMALS) + 0.0) if isinstance(t, float) else str(t)

    def strip_sign(d: dict) -> dict:
        """Measurements only: the sign test is a rule, applied in eleveniq from by_league."""
        return {k: v for k, v in d.items() if k != "same_sign"}

    for r_spec in ratio_json.values():
        for cell in r_spec["groups"].values():
            if "possession" in cell:
                cell["possession"] = {k: strip_sign(v) for k, v in cell["possession"].items()}
    for cell in d12b_json.values():
        cell["possession"] = {k: strip_sign(v) for k, v in cell["possession"].items()}
    zero_of = {(r["metric"], r["group"]): r["zero_share"] for r in zero.iter_rows(named=True)}
    d9_ok = {(r["metric"], r["group"]): r["global_ok"] for r in d9.iter_rows(named=True)}
    metrics_json: dict[str, dict] = {}
    for m in P90:
        metrics_json[m] = {"count_column": COUNT_OF[m], "groups": {}}
        for grp in OUTFIELD:
            sub = gated.filter(pl.col("position_group") == grp)
            cnt = sub[COUNT_OF[m]]
            ordered = sorted(cnt.to_list())   # inverse ECDF: smallest c with share(count ≤ c) ≥ 0.9
            count_p90 = ordered[math.ceil(SIGNATURE_COUNT_QUANTILE * len(ordered)) - 1]
            metrics_json[m]["groups"][grp] = {
                "n_gated": sub.height,
                "zero_share": zero_of[(m, grp)],
                **ms_fields("r_full", m, grp, odd_even_of.get((m, grp))),
                "count_p90": count_p90,
                "holders_at_count": {count_key(t): int((cnt >= t).sum()) for t in sorted({*TRAIT_COUNTS, count_p90})},
                "global_ok": d9_ok[(m, grp)],
                "league_mean_global_pct": d9_league_means[(m, grp)],
                "possession": {proxy: strip_sign(d12_detail[(m, proxy, grp)]) for proxy in ("poss_dur_loo", "poss_pass_loo")},
            }
    cohorts = {}
    for grp in OUTFIELD:
        g = gated.filter(pl.col("position_group") == grp)
        lg = lpop.filter(pl.col("position_group") == grp)
        cohorts[grp] = {"n_gated": g.height, "ref_n_global": gpop.filter(pl.col("position_group") == grp).height,
                        "ref_n_local": {str(c): lg.filter(pl.col("competition_id") == c).height
                                        for c in POOL_LEAGUES}}
    out = {
        "schema_version": 3,           # 2: the movers block (M4g3b); 3: the possession block (M4g4d)
        "generated_by": "spikes/m4_decision_measurements.py --write-json",
        "provenance": {
            "source": SOURCE, "season_id": SEASON,
            "pool_competition_ids": list(POOL_LEAGUES), "competition_ids": sorted(LEAGUES),
            "season_parquet": str(SEASON_PARQUET.relative_to(ROOT)), "season_parquet_sha256": sha256(SEASON_PARQUET),
            "match_parquet": str(MATCH_PARQUET.relative_to(ROOT)), "match_parquet_sha256": sha256(MATCH_PARQUET),
            "season_columns": pl.read_parquet_schema(SEASON_PARQUET).names(),
        },
        "population": {
            "gated": "in_percentile_pool ∧ is_outfield ∧ meets_min_minutes (recomputed: minutes_nominal_equiv ≥ "
                     f"{GATE:g} − {GATE_TOLERANCE:g}, asserted equal on every row)",
            "reference": "gated rows, one row per (player_id, season_id, source) per population (D3: most minutes, "
                         "then lowest competition_id, then lowest team_id); for a ratio, only rows meeting "
                         "min_attempts (ADR 004 D19 clarification, M4c2)",
            "r_full": "multi-split split-half (plan 005 M4g2): per split, each stint's matches go at random to "
                      "two equal halves; half values demeaned within league × group, pooled Pearson, "
                      "Spearman-Brown; r_full = the mean over K splits of the corrected values. *_odd_even = "
                      "the earlier odd/even-by-date value, kept for the before/after",
            "possession": "Spearman ρ with leave-one-out team possession (duration, pass share) within each pool league",
            "ratio_M": f"pool outfield rows with denominator ≥ {RATIO_MIN_N}; beta-binomial (np_xg_per_shot: "
                       "variance components); min_attempts = ⌈1.5·M⌉; null when no signal",
            "collinear_report_floor": COLLINEAR_REPORT_FLOOR,
        },
        "cohorts": cohorts,
        "metrics": metrics_json,
        "ratios": ratio_json,
        "team_pass_share": d12b_json,
        "collinear_pairs": pairs,
        "reliability": {"seed": ms.SEED, "k": ms_res.k, "k_start": ms.K_START, "k_cap": ms.K_CAP,
                        "mc_se_target": ms.MC_SE_TARGET, "reached_cap": ms_res.reached_cap,
                        "judged_cells_above_target": sorted([c[1], c[2]] for c, _ in ms_over)},
    }
    # Movers (plan 005 M4g3b): the engine reads min_attempts and r_full from the values written
    # above (rounded as stored), not from the committed file this run replaces.
    print("\nmovers (plan 005 M4g3b): primary stream + extra seeds …", flush=True)
    out["movers"] = mv.movers_block(mv.compute(meas=round_floats(out), verbose=False))
    # Possession (plan 005 M4g4d): embedded from the M4g4c / M4g4c2 engine outputs, pinned to
    # the β = 0 values and the β = 0 mover entry written above (rounded as stored).
    out["possession"] = pb.possession_block(round_floats(out), ARGS.possession / "possession.json",
                                            ARGS.possession / "movers.json", ARGS.trust)
    ARGS.write_json.parent.mkdir(parents=True, exist_ok=True)
    with ARGS.write_json.open("w") as f:
        json.dump(round_floats(out), f, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False)
        f.write("\n")
    print(f"\nwrote {ARGS.write_json}")
    # Minute buckets are report only (plan 005 M4g2 #9): a separate file, never read by the registry.
    buckets_out = {"generated_by": "spikes/m4_decision_measurements.py --write-json", "k": ms_res.k,
                   "seed": ms.SEED, "buckets": {b: {"lo": lo, "hi": hi} for b, (lo, hi) in MINUTE_BUCKETS.items()},
                   "cells": [{"population": p_, "group": g_, "measure": n_, **ms_summary[(p_, g_, n_)]}
                             for (p_, g_, n_) in sorted(ms_summary)]}
    BUCKETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with BUCKETS_JSON.open("w") as f:
        json.dump(round_floats(buckets_out), f, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {BUCKETS_JSON}")
