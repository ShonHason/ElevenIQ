"""Spike: the expected M4b flag flips, written BEFORE the M4b2 rebuild (plan 005).

Reads the pre-M4b snapshot (data/interim/m4a_snapshot/) and applies the ADR 004
D1 gate independently of the pipeline:

    minutes_nominal_equiv = Σ_matches minutes_played × nominal_length / match_duration
    meets_min_minutes     = minutes_nominal_equiv ≥ GATE − GATE_TOLERANCE

nominal_length is 90 for a match with periods 1–2 and 120 with extra time. The
match parquet does not store the periods, so the spike fails if any match is long
enough to have had extra time (match_duration > EXTRA_TIME_GUARD); 2015/16 has none.

Writes spikes/expected_m4b_flips.json: every row whose meets_min_minutes changes,
and the list of season flags expected NOT to change (M4b changes no minutes, so
position_group, is_primary_stint, ... stay as they are).
spikes/m4b_before_after.py compares the rebuild against this file (STOP rule).

No eleveniq imports (tests/test_spikes_independent.py).

Usage: .venv/bin/python spikes/m4b_expected_flips.py
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "interim" / "m4a_snapshot"
OUT = Path(__file__).resolve().parent / "expected_m4b_flips.json"

GATE = 900.0
GATE_TOLERANCE = 1e-6  # ADR 004 D1: float sums of a full match give 89.99999999999999
NOMINAL_LENGTH = 90.0
EXTRA_TIME_GUARD = 115.0  # minutes; a 90-minute match with this much stoppage does not exist
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
UNCHANGED_FLAGS = ["position_group", "is_primary_stint", "is_outfield", "is_gk",
                   "in_percentile_pool", "multi_team_season", "multi_competition_season"]


def main() -> None:
    match = pl.read_parquet(SNAPSHOT / "player_match_stats.parquet")
    season = pl.read_parquet(SNAPSHOT / "player_season_stats.parquet")
    longest = match["match_duration"].max()
    if longest > EXTRA_TIME_GUARD:
        raise SystemExit(f"FAIL: a match lasts {longest:.2f} min; extra time needs nominal 120")

    nominal = match.group_by(KEY).agg(
        (pl.col("minutes_played") * NOMINAL_LENGTH / pl.col("match_duration")).sum()
        .alias("minutes_nominal_equiv"))
    rows = season.join(nominal, on=KEY, how="left", validate="1:1")
    if rows["minutes_nominal_equiv"].null_count():
        raise SystemExit("FAIL: season rows without match rows in the snapshot")
    rows = rows.with_columns(
        (pl.col("minutes_nominal_equiv") >= GATE - GATE_TOLERANCE).alias("meets_min_after"))
    flips = rows.filter(pl.col("meets_min_minutes") != pl.col("meets_min_after")).sort(
        "competition_id", "player_name")

    out = {
        "description": "Expected M4b flag flips (plan 005 M4b2, ADR 004 D1), written from "
                       "data/interim/m4a_snapshot before the rebuild.",
        "gate": GATE,
        "gate_tolerance": GATE_TOLERANCE,
        "unchanged_flags": UNCHANGED_FLAGS,
        "meets_min_minutes": [
            {**{k: r[k] for k in KEY}, "player_name": r["player_name"],
             "team_name": r["team_name"], "position_group": r["position_group"],
             "in_percentile_pool": r["in_percentile_pool"], "is_outfield": r["is_outfield"],
             "minutes_played": round(r["minutes_played"], 4),
             "minutes_nominal_equiv": round(r["minutes_nominal_equiv"], 4),
             "before": r["meets_min_minutes"], "after": r["meets_min_after"]}
            for r in flips.iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    down = flips.filter(pl.col("meets_min_after").not_())
    print(f"meets_min_minutes flips: {flips.height} ({down.height} down, "
          f"{flips.height - down.height} up)")
    with pl.Config(tbl_rows=60, fmt_str_lengths=40, tbl_width_chars=200,
                   tbl_hide_dataframe_shape=True, float_precision=1):
        print(flips.select("competition_id", "player_name", "team_name", "position_group",
                           "in_percentile_pool", "is_outfield", "minutes_played",
                           "minutes_nominal_equiv"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
