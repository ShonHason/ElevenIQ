"""Spike: the expected M4c1 second_position_group changes, written BEFORE the rebuild.

M4c1 (plan 005, ADR 004 §8, user decision U3 2026-10-03): second_position_group is
null when its group's minutes are below SECOND_POSITION_MIN_SHARE of minutes_played.
Nothing else changes: position_group_margin_pct keeps the real second-most minutes,
so the margin, share and ambiguity columns are identical.

Reads the snapshot (data/interim/m4c1_snapshot/) and applies the rule independently
of the pipeline: a row whose second_position_group is non-null and whose second
group's minutes / minutes_played < SECOND_POSITION_MIN_SHARE goes to null.

Writes spikes/expected_m4c1_second_group.json: every such row (before → null), and
the flags expected not to change. spikes/m4b_before_after.py compares the rebuild
against it (STOP rule):

    .venv/bin/python spikes/m4b_before_after.py --snapshot data/interim/m4c1_snapshot \
        --expected spikes/expected_m4c1_second_group.json --all-columns-identical

The list is read from the file's "second_position_group" key.

No eleveniq imports (tests/test_spikes_independent.py).

Usage: .venv/bin/python spikes/m4c1_expected_second_group.py
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "interim" / "m4c1_snapshot"
OUT = Path(__file__).resolve().parent / "expected_m4c1_second_group.json"

SECOND_POSITION_MIN_SHARE = 0.10  # user decision U3, 2026-10-03
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
GROUP_COLS = {"AM-Winger": "minutes_am_winger", "CB": "minutes_cb", "DM-CM": "minutes_dm_cm",
              "FB-WB": "minutes_fb_wb", "GK": "minutes_gk", "ST": "minutes_st"}
UNCHANGED_FLAGS = ["position_group", "is_primary_stint", "is_outfield", "is_gk",
                   "in_percentile_pool", "multi_team_season", "multi_competition_season",
                   "meets_min_minutes", "position_group_ambiguous"]


def main() -> None:
    season = pl.read_parquet(SNAPSHOT / "player_season_stats.parquet")
    second_minutes = pl.lit(None, dtype=pl.Float64)
    for group, col in GROUP_COLS.items():
        second_minutes = pl.when(pl.col("second_position_group") == group).then(pl.col(col)).otherwise(second_minutes)
    rows = (season.with_columns((second_minutes / pl.col("minutes_played")).alias("second_share"))
            .filter(pl.col("second_position_group").is_not_null()
                    & (pl.col("second_share") < SECOND_POSITION_MIN_SHARE))
            .sort(KEY))
    out = {
        "description": ("Expected M4c1 changes (plan 005, ADR 004 §8, user U3): second_position_group "
                        f"becomes null below {SECOND_POSITION_MIN_SHARE:.0%} of minutes_played. Written from "
                        "data/interim/m4c1_snapshot before the rebuild. No flag changes."),
        "second_position_min_share": SECOND_POSITION_MIN_SHARE,
        "unchanged_flags": UNCHANGED_FLAGS,
        "meets_min_minutes": [],
        "second_position_group": [
            {**{c: r[c] for c in KEY}, "player_name": r["player_name"], "before": r["second_position_group"],
             "after": None, "second_share": r["second_share"]}
            for r in rows.iter_rows(named=True)],
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    gated = rows.filter(pl.col("in_percentile_pool") & pl.col("meets_min_minutes"))
    print(f"rows going to null: {rows.height} (of {season['second_position_group'].is_not_null().sum()} "
          f"with a second group); gated pool: {gated.height} "
          f"(outfield {gated.filter(pl.col('is_outfield')).height}, GK {gated.filter(pl.col('is_gk')).height})")
    print(f"max second share among them: {rows['second_share'].max():.4f}; ambiguous among them: "
          f"{rows['position_group_ambiguous'].sum()}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
