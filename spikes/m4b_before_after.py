"""Spike: M4b before/after with the STOP rule (plan 005 M4b2, ADR 004).

Compares the pre-M4b snapshot (data/interim/m4a_snapshot/) with the rebuilt
parquets, and the season flags with spikes/expected_m4b_flips.json, which
spikes/m4b_expected_flips.py wrote from the snapshot BEFORE the rebuild.

STOP rule: the script exits non-zero when
  - a meets_min_minutes flip is not in the expected list, or an expected flip did
    not happen;
  - any flag in the list's unchanged_flags changes on any row;
  - season rows appear or disappear, or minutes_played / any minutes_<group>
    changes (M4b changes no minutes);
  - an independent recomputation disagrees with the pipeline:
    minutes_nominal_equiv (Σ minutes × match_nominal_duration / match_duration),
    team_pass_share, np_xg_per_shot and the position margin columns.
A failure is to be brought to the user, not explained away.

No eleveniq imports (tests/test_spikes_independent.py).

Usage: .venv/bin/python spikes/m4b_before_after.py [--expected PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "interim" / "m4a_snapshot"
NEW_MATCH = ROOT / "data" / "interim" / "player_match_stats.parquet"
NEW_SEASON = ROOT / "data" / "processed" / "player_season_stats.parquet"
DEFAULT_EXPECTED = Path(__file__).resolve().parent / "expected_m4b_flips.json"

LEAGUES = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1", 9: "Bundesliga"}
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
MINUTE_COLS = ["minutes_played", "minutes_gk", "minutes_cb", "minutes_fb_wb", "minutes_dm_cm",
               "minutes_am_winger", "minutes_st", "minutes_unknown"]
CHANGED_COUNTS = ["progressive_passes", "final_third_entries_pass", "box_entries_pass",
                  "long_passes_attempted", "long_passes_completed", "long_passes_unknown_outcome",
                  "xg_assisted_direct"]
GROUPS = {"AM-Winger": "minutes_am_winger", "CB": "minutes_cb", "DM-CM": "minutes_dm_cm",
          "FB-WB": "minutes_fb_wb", "GK": "minutes_gk", "ST": "minutes_st"}  # alphabetical
OUTFIELD = {"CB", "FB-WB", "DM-CM", "AM-Winger", "ST"}
AMBIGUOUS_MARGIN = 0.05  # ADR 004 D5
TOL = 1e-9

problems: list[str] = []


def stop(msg: str) -> None:
    problems.append(msg)
    print(f"STOP: {msg}")


def second_group(row: dict) -> tuple[str | None, float]:
    """ADR 004 D5 second group, re-implemented: most minutes after the top group,
    alphabetical on ties, never GK for an outfield row, None at 0 minutes."""
    best_g, best_m = None, 0.0
    for g, col in GROUPS.items():
        if g == row["position_group"] or (g == "GK" and row["position_group"] in OUTFIELD):
            continue
        if row[col] > best_m:
            best_g, best_m = g, row[col]
    return best_g, best_m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    args = ap.parse_args()
    expected = json.loads(args.expected.read_text())

    old_m = pl.read_parquet(SNAPSHOT / "player_match_stats.parquet")
    old_s = pl.read_parquet(SNAPSHOT / "player_season_stats.parquet")
    new_m = pl.read_parquet(NEW_MATCH)
    new_s = pl.read_parquet(NEW_SEASON)

    # Rows and minutes: unchanged.
    for label, old, new, key in (("match", old_m, new_m, ["match_id", "player_id"]),
                                 ("season", old_s, new_s, KEY)):
        if old.height != new.height or old.select(key).sort(key).rows() != new.select(key).sort(key).rows():
            stop(f"{label} rows differ: {old.height} → {new.height}")
    joined = old_s.join(new_s, on=KEY, how="inner", suffix="_new")
    for col in MINUTE_COLS:
        diff = (joined[col] - joined[f"{col}_new"]).abs().max()
        if diff > TOL:
            stop(f"season {col} changed (max |Δ| {diff})")

    # Δ per league for the redefined counts.
    print("\nΔ per league (season totals, after − before):")
    rows = []
    for comp, name in LEAGUES.items():
        o = old_s.filter(pl.col("competition_id") == comp)
        n = new_s.filter(pl.col("competition_id") == comp)
        rows.append({"league": name, **{c: round(float(n[c].sum() - o[c].sum()), 4) for c in CHANGED_COUNTS},
                     "progressive_passes_%": round(100 * (n["progressive_passes"].sum() / o["progressive_passes"].sum() - 1), 2)})
    with pl.Config(tbl_cols=20, tbl_width_chars=250, tbl_hide_dataframe_shape=True):
        print(pl.DataFrame(rows))

    # Completion ratios (ADR 004 D13 + D14) on the pool, mean before/after.
    pool_old = old_s.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
    pool_new = new_s.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
    print("\nPool outfield means, before → after:")
    for c in ("pass_completion_pct", "long_pass_completion_pct", "long_passes_attempted_p90",
              "progressive_passes_p90", "box_entries_pass_p90", "final_third_entries_pass_p90"):
        print(f"  {c:<32} {pool_old[c].mean():.4f} → {pool_new[c].mean():.4f}")

    # Independent recomputation of the new season columns.
    nominal = new_m.group_by(KEY).agg(
        (pl.col("minutes_played") * pl.col("match_nominal_duration") / pl.col("match_duration"))
        .sum().alias("_nominal"))
    chk = new_s.join(nominal, on=KEY, how="left")
    d = (chk["minutes_nominal_equiv"] - chk["_nominal"]).abs().max()
    if d > 1e-6:
        stop(f"minutes_nominal_equiv differs from the recomputation (max |Δ| {d})")
    share = chk.with_columns(
        pl.when(pl.col("team_open_play_passes_while_on") > 0)
        .then(pl.col("open_play_passes_attempted") / pl.col("team_open_play_passes_while_on"))
        .alias("_share"),
        pl.when(pl.col("np_shots") > 0).then(pl.col("np_xg") / pl.col("np_shots")).alias("_npxs"))
    for col, mine in (("team_pass_share", "_share"), ("np_xg_per_shot", "_npxs")):
        both_null = share[col].is_null() == share[mine].is_null()
        if not both_null.all() or ((share[col] - share[mine]).abs().max() or 0) > TOL:
            stop(f"{col} differs from the recomputation")
    if share.filter(pl.col("team_pass_share") > 1).height:
        stop("team_pass_share > 1")
    bad_margin = 0
    for r in new_s.iter_rows(named=True):
        if r["minutes_played"] <= 0:
            if r["position_group_margin_pct"] is not None or r["position_group_ambiguous"]:
                bad_margin += 1
            continue
        g2, m2 = second_group(r)
        top = r[GROUPS[r["position_group"]]] if r["position_group"] in GROUPS else 0.0
        margin = (top - m2) / r["minutes_played"]
        if (g2 != r["second_position_group"] or abs(margin - r["position_group_margin_pct"]) > TOL
                or (margin < AMBIGUOUS_MARGIN) != r["position_group_ambiguous"]
                or abs(top / r["minutes_played"] - r["position_group_share"]) > TOL):
            bad_margin += 1
    if bad_margin:
        stop(f"{bad_margin} rows: position margin columns differ from the recomputation")

    # Flags (STOP rule).
    exp_rows = {(e["player_id"], e["team_id"], e["competition_id"], e["season_id"], e["source"]):
                (e["before"], e["after"]) for e in expected["meets_min_minutes"]}
    flips = joined.filter(pl.col("meets_min_minutes") != pl.col("meets_min_minutes_new"))
    seen = set()
    for r in flips.iter_rows(named=True):
        k = tuple(r[c] for c in KEY)
        seen.add(k)
        if exp_rows.get(k) != (r["meets_min_minutes"], r["meets_min_minutes_new"]):
            stop(f"unexpected meets_min_minutes flip: {r['player_name']} ({r['team_name']}, "
                 f"comp {r['competition_id']}) {r['meets_min_minutes']} → {r['meets_min_minutes_new']}, "
                 f"nominal {r['minutes_nominal_equiv']:.2f}")
    for k in set(exp_rows) - seen:
        stop(f"expected meets_min_minutes flip did not happen: {k}")
    for flag in expected["unchanged_flags"]:
        changed = joined.filter(pl.col(flag) != pl.col(f"{flag}_new"))
        for r in changed.iter_rows(named=True):
            stop(f"unexpected {flag} flip: {r['player_name']} ({r['team_name']}) "
                 f"{r[flag]} → {r[f'{flag}_new']}")
    print(f"\nmeets_min_minutes flips: {flips.height} (expected {len(exp_rows)}); "
          f"down {flips.filter(pl.col('meets_min_minutes_new').not_()).height}")

    gated = new_s.filter(pl.col("in_percentile_pool") & pl.col("is_outfield") & pl.col("meets_min_minutes"))
    print(f"gated rows: {gated.height}; ambiguous among them: {gated['position_group_ambiguous'].sum()}; "
          f"second group null: {gated['second_position_group'].null_count()}")

    if problems:
        print(f"\nSTOP: {len(problems)} problem(s). Bring them to the user; do not explain them away.")
        return 1
    print("\nOK: no unexpected flips; recomputations agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
