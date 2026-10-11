"""
Pass-mix stage (plan 006 pre-registration, user 2026-10-10): raw StatsBomb events →
data/processed/player_season_pass_mix.parquet, one row per season row key, with each
length × direction bucket's counted (known-outcome) and completed open-play passes.

A separate, display-only artifact: the season parquet and everything measured from it stay
unchanged. STOP rule: every row's open-play pass count (all outcomes) must equal the season
parquet's open_play_passes_attempted; the stage refuses to write otherwise.

    .venv/bin/python -m eleveniq.pipeline.build_pass_mix
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

from eleveniq.config import COMPETITION_SEASONS
from eleveniq.metrics.pass_mix import (DIRECTIONS, LENGTHS, bucket_column, direction_bucket,
                                       length_bucket, outcome_counts)
from eleveniq.metrics.pass_types import is_restart
from eleveniq.statsbomb.loader import load_events, load_matches

log = logging.getLogger(__name__)
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")
PROCESSED_DIR = Path(__file__).resolve().parents[3] / "data" / "processed"
SEASON_PARQUET = PROCESSED_DIR / "player_season_stats.parquet"
OUT = PROCESSED_DIR / "player_season_pass_mix.parquet"
COLUMNS = [bucket_column(ln, d, k) for ln in LENGTHS for d in DIRECTIONS for k in ("att", "cmp")]


class PassMixError(RuntimeError):
    pass


def count_match(events: list[dict], competition_id: int, season_id: int, source: str,
                acc: dict[tuple, Counter]) -> None:
    """Adds one match's open-play passes to acc[key] (bucket counts, open_play_all,
    missing_length); a pass without an angle stops the build."""
    for e in events:
        if (e.get("type") or {}).get("name") != "Pass" or "player" not in e:
            continue
        p = e.get("pass") or {}
        if is_restart(p):
            continue
        key = (e["player"]["id"], e["team"]["id"], competition_id, season_id, source)
        c = acc[key]
        c["open_play_all"] += 1
        counted, completed = outcome_counts(p)
        if not counted:
            continue
        length, angle = p.get("length"), p.get("angle")
        if length is None:
            c["missing_length"] += 1
            continue
        if angle is None:
            # The pre-registration leaves out only passes without a length; a missing angle
            # (none in 2015/16) has no direction cell, so it stops the build for a decision.
            raise PassMixError(f"match pass without an angle (player {e['player']['id']}): "
                               "decide how spec 1 counts it before building")
        lb, db = length_bucket(length), direction_bucket(angle)
        c[bucket_column(lb, db, "att")] += 1
        if completed:
            c[bucket_column(lb, db, "cmp")] += 1


def build(data_root: Path | None = None) -> pl.DataFrame:
    acc: dict[tuple, Counter] = defaultdict(Counter)
    for cs in COMPETITION_SEASONS:
        for m in load_matches(cs.competition_id, cs.season_id, data_root):
            count_match(load_events(m["match_id"], data_root), cs.competition_id, cs.season_id,
                        cs.source, acc)
    rows = [{**dict(zip(KEY, k)), **{c: int(v[c]) for c in COLUMNS},
             "pm_open_play_all": int(v["open_play_all"]),
             "pm_missing_length": int(v["missing_length"])} for k, v in sorted(acc.items())]
    return pl.DataFrame(rows).with_columns(
        [pl.col(c).cast(pl.Int32) for c in (*COLUMNS, "pm_open_play_all", "pm_missing_length")])


def reconcile(mix: pl.DataFrame, season: pl.DataFrame) -> None:
    """STOP rule: open-play passes per season row equal the season parquet's count."""
    s = season.select([*KEY, "open_play_passes_attempted"])
    if s["open_play_passes_attempted"].null_count():
        raise PassMixError("the season parquet has null open_play_passes_attempted")
    extra = mix.join(s, on=list(KEY), how="anti")
    if extra.height:
        raise PassMixError(f"{extra.height} pass-mix rows have no season row, e.g. "
                           f"{extra.head(3).select(KEY).to_dicts()}")
    # A season row without passes has no pass-mix row: 0 passes, which must equal its count.
    j = s.join(mix.select([*KEY, "pm_open_play_all"]), on=list(KEY), how="left")
    j = j.with_columns(pl.col("pm_open_play_all").fill_null(0))
    bad = j.filter(pl.col("open_play_passes_attempted") != pl.col("pm_open_play_all"))
    if bad.height:
        raise PassMixError(f"{bad.height} rows: open-play passes ≠ the season parquet, e.g. "
                           f"{bad.head(3).to_dicts()}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argparse.ArgumentParser(description=__doc__.split("\n")[1]).parse_args()
    mix = build()
    reconcile(mix, pl.read_parquet(SEASON_PARQUET))
    mix.write_parquet(OUT)
    tot = mix.select(pl.sum("pm_open_play_all"), pl.sum("pm_missing_length")).row(0)
    log.info(f"wrote {mix.height} rows → {OUT}; {tot[0]:,} open-play passes, "
             f"{tot[1]} known-outcome passes without a length (left out); reconciled with the "
             "season parquet")


if __name__ == "__main__":
    main()
