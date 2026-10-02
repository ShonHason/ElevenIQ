#!/usr/bin/env python3
"""
Spike: minutes lost to first-half stoppage on the match-absolute axis (plan 004 H3b).

HISTORICAL: describes the pre-H4 pipeline (last-event clock, match-absolute minutes);
superseded by H4 (ADR 002 §31, before/after in §31.7 from
spikes/period_aware_before_after.py). It reads data/interim/player_match_stats.parquet,
which now holds §31 minutes, so rerunning it no longer reproduces the H3b figures; use
data/interim/h3b_snapshot/ for that.

Source of the figures in:
- docs/decisions/002-pipeline-decisions.md §3: the table of the 9
  reversed-period stints and the "Known limits" numbers;
- docs/milestones.md, Milestone 4, "Pre-condition from Plan 004 H3b".

Measurement only. The pipeline is unchanged, and nothing is written.

Run (needs data/raw/statsbomb/, data/interim/player_match_stats.parquet and
data/processed/player_season_stats.parquet):
    .venv/bin/python spikes/measure_first_half_stoppage.py

Independence
------------
Never imports eleveniq. The competition-seasons are the committed reference
files (tests/fixtures/reference_totals/*.json). The stint, exit-cap and card
rules are re-implemented from the ADR 002 §3 text. The only pipeline inputs are
the stored minutes_played and the season flags, which are what is measured.

Method
------
Stored minutes are match-absolute. Period 2 restarts at 45:00, so a player on
the pitch at the end of the first half is not credited its stoppage time
(ADR 002 §3). This spike recomputes every player-match on a period-aware clock:
- same lineup stints, and the same exit cap (the earlier of dismissal and the
  player's own substitution off), ordered by (period, minute);
- each period ends at its last event (the match_duration convention);
- a stint whose end does not follow its start (reversed-period) is empty;
- a player's overlapping stints count once.
lost = period-aware minutes - stored minutes_played.

Caveat: when the first half's last event is before 45:00 (30 matches in
2015/16), the period-aware clock credits *less* than the stored minutes. That
comes from the last-event convention, not from real time.

The season join keys on (player_id, team_id, competition_id, season_id,
source), the full season-row key, so it stays valid with several seasons or
sources.
"""
from __future__ import annotations

import json

import polars as pl

from reference_totals import DATA_ROOT, PROJECT_ROOT, REFERENCE_DIR, load_matches

MATCH_PARQUET = PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
SEASON_PARQUET = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
PERIOD_START = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}
SHOOTOUT_PERIOD = 5  # ADR 002 §3: not playing time
DISMISSAL_CARDS = {"Red Card", "Second Yellow"}  # ADR 002 §3
MIN_MINUTES = 900  # ADR 002 §5 default gate (config.MIN_MINUTES_THRESHOLD)
SEASON_KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
# Percentile-shift metrics (stats-reviewer, H3b): volume metrics across roles.
RANK_METRICS = ["pressures", "interceptions", "xg", "tackles_won", "progressive_passes"]

Instant = tuple[int, float]


def _load(kind: str, match_id: int) -> list[dict]:
    with open(DATA_ROOT / kind / f"{match_id}.json") as f:
        return json.load(f)


def _mmss(s: str) -> float:
    """'MM:SS', 'MM:SS.mmm' or 'H:MM:SS' → minutes (ADR 002 §3)."""
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + parts[2] / 60
    return parts[0] + parts[1] / 60


def _at(e: dict) -> Instant:
    return (e["period"], e["minute"] + e["second"] / 60)


def _card(e: dict) -> str | None:
    for key in ("bad_behaviour", "foul_committed"):
        name = ((e.get(key) or {}).get("card") or {}).get("name")
        if name:
            return name
    return None


def _exits(events: list[dict]) -> dict[int, Instant]:
    """Per player, the earlier of his first dismissal and first substitution off."""
    dismissal: dict[int, Instant] = {}
    sub_off: dict[int, Instant] = {}
    for e in sorted(events, key=lambda e: e["index"]):
        pid = (e.get("player") or {}).get("id")
        if pid is None:
            continue
        if _card(e) in DISMISSAL_CARDS:
            dismissal.setdefault(pid, _at(e))
        if e["type"]["name"] == "Substitution":
            sub_off.setdefault(pid, _at(e))
    return {pid: min(x for x in (dismissal.get(pid), sub_off.get(pid)) if x is not None)
            for pid in dismissal.keys() | sub_off.keys()}


def _period_ends(events: list[dict]) -> dict[int, float]:
    ends: dict[int, float] = {}
    for e in events:
        p, minute = _at(e)
        ends[p] = max(ends[p], minute) if p in ends else minute
    return ends


def _clock(ends: dict[int, float]):
    """Map a (period, match-absolute minute) instant to elapsed real minutes."""
    offset, elapsed = {}, 0.0
    for p in sorted(ends):
        offset[p] = elapsed - PERIOD_START[p]
        elapsed += ends[p] - PERIOD_START[p]
    return lambda at: at[1] + offset[at[0]]


def _stint_bounds(positions: list[dict], match_end: Instant, exit_at: Instant | None):
    """
    (stint, start, end) per stint, period-aware.

    Deliberately a copy of the pipeline rule (build_player_match._stint_end and
    the exit cap in _on_pitch_excess), not an import: spikes never import
    eleveniq, so a bug in the pipeline cannot also be in the measurement. The
    copy must therefore carry the same guards, including the one below.
    """
    for s in positions:
        start = (s["from_period"], _mmss(s["from"]))
        if not s.get("to"):
            end = match_end
        elif s.get("to_period") is None:
            # Same guard as the pipeline: an end without a period cannot be
            # ordered against (period, minute) instants.
            raise ValueError(f"stint {s.get('from')!r}-{s['to']!r} has a 'to' but no 'to_period'")
        else:
            end = (s["to_period"], _mmss(s["to"]))
        if exit_at is not None:
            end = min(end, exit_at)
        yield s, start, end


def _union_length(intervals: list[tuple[float, float]]) -> float:
    total, cur = 0.0, None
    for a, b in sorted(intervals):
        if cur and a <= cur[1]:
            cur = (cur[0], max(cur[1], b))
        else:
            if cur:
                total += cur[1] - cur[0]
            cur = (a, b)
    return total + (cur[1] - cur[0] if cur else 0.0)


def _competition_seasons() -> list[tuple[int, int]]:
    refs = [json.loads(p.read_text()) for p in sorted(REFERENCE_DIR.glob("*.json"))]
    return [(r["competition_id"], r["season_id"]) for r in refs]


def measure() -> tuple[pl.DataFrame, list[dict]]:
    """One row per player_match_stats row, plus the reversed-period stints."""
    stored = {
        (r["match_id"], r["player_id"]): r
        for r in pl.read_parquet(MATCH_PARQUET).select(
            "match_id", "player_id", "season_id", "source", "minutes_played").iter_rows(named=True)
    }
    rows: list[dict] = []
    reversed_stints: list[dict] = []
    for comp_id, season_id in _competition_seasons():
        matches = load_matches(comp_id, season_id)
        league = f"{matches[0]['competition']['competition_name']} {matches[0]['season']['season_name']}"
        for match in matches:
            mid = match["match_id"]
            events = [e for e in _load("events", mid) if e["period"] < SHOOTOUT_PERIOD]
            ends = _period_ends(events)
            last = max(ends)
            real = _clock(ends)
            exits = _exits(events)
            for team in _load("lineups", mid):
                for player in team["lineup"]:
                    pid = player["player_id"]
                    row = stored.get((mid, pid))
                    if row is None:
                        continue
                    intervals = []
                    for s, start, end in _stint_bounds(player["positions"], (last, ends[last]),
                                                       exits.get(pid)):
                        if end > start:
                            intervals.append((real(start), real(end)))
                        if s.get("to_period") is not None and s["from_period"] > s["to_period"]:
                            reversed_stints.append({"match_id": mid, "player_id": pid,
                                                    "player": player["player_name"]})
                    first = min((s["from_period"], _mmss(s["from"])) for s in player["positions"])
                    kind = ("full_match" if first == (1, 0.0) and pid not in exits
                            else "second_half_sub" if first[0] >= 2 else "other")
                    rows.append({
                        "league": league, "competition_id": comp_id, "season_id": row["season_id"],
                        "source": row["source"], "match_id": mid, "player_id": pid,
                        "team_id": team["team_id"], "kind": kind,
                        "credited": row["minutes_played"], "real": _union_length(intervals),
                        "p1_stoppage": ends[1] - 45.0,
                    })
    df = pl.DataFrame(rows).with_columns((pl.col("real") - pl.col("credited")).alias("lost"))
    return df, reversed_stints


def _percentile_shift(gated: pl.DataFrame) -> pl.DataFrame:
    """Percentile-point shift per row when per-90 uses real instead of credited
    minutes; cohort = league × group ("local") or group only ("global")."""
    out = []
    for scope, by in (("local", ["competition_id", "position_group"]), ("global", ["position_group"])):
        for m in RANK_METRICS:
            pct = lambda minutes: (
                (pl.col(m) / minutes).rank("average").over(by) / pl.len().over(by) * 100)
            shift = gated.select((pct(pl.col("real_minutes")) - pct(pl.col("minutes_played")))
                                 .abs().alias("s"))["s"]
            out.append({"scope": scope, "metric": m, "max": shift.max(), "mean": shift.mean(),
                        "rows_ge_1pt": int((shift >= 1).sum())})
    return pl.DataFrame(out)


def report(df: pl.DataFrame, reversed_stints: list[dict]) -> None:
    pl.Config.set_tbl_rows(50)
    pl.Config.set_tbl_width_chars(140)

    print("== The reversed-period stints (ADR 002 §3 table) ==")
    stints = pl.DataFrame(reversed_stints).join(df, on=["match_id", "player_id"], how="left")
    print(stints.select("league", "match_id", "player", "credited", "real", "lost"))
    print(f"total lost: {stints['lost'].sum():.2f}")

    print("\n== Per league ==")
    per_match = df.group_by("league", "match_id").agg(pl.col("p1_stoppage").first())
    print(df.group_by("league").agg(
        pl.col("lost").sum().alias("minutes_lost"),
        (pl.col("lost").sum() / pl.col("credited").sum() * 100).alias("pct_of_minutes"),
    ).join(per_match.group_by("league").agg(pl.col("p1_stoppage").median().alias("median_stoppage")),
           on="league").sort("league"))
    print(f"total lost: {df['lost'].sum():,.1f} "
          f"({df['lost'].sum() / df['credited'].sum() * 100:.2f}% of minutes)")

    print("\n== Per player-match, by kind ==")
    print(df.group_by("kind").agg(
        pl.len(), pl.col("lost").mean().alias("mean"), pl.col("lost").median().alias("median"),
        pl.col("lost").max().alias("max"), pl.col("lost").abs().max().alias("max_abs"),
    ).sort("kind"))
    fm = df.filter(pl.col("kind") == "full_match")
    print("full-match share with lost == first-half stoppage (1e-6):",
          f"{((fm['lost'] - fm['p1_stoppage']).abs() < 1e-6).mean():.5f}")
    print(fm.group_by("league").agg(pl.col("lost").mean().alias("full_match_mean")).sort("league"))

    print("\n== Caveat: first half's last event before 45:00; largest stoppages ==")
    early = per_match.filter(pl.col("p1_stoppage") < 0)
    negative = df.filter(pl.col("lost") < -1e-6)
    print(f"matches: {early.height} (min {early['p1_stoppage'].min():.2f} min); "
          f"player-matches with lost < 0: {negative.height}, sum {negative['lost'].sum():.1f}")
    print(per_match.sort("p1_stoppage", descending=True).head(5))

    print("\n== Season level: per-90 overstatement, percentile shift, 900-minute gate ==")
    real_season = df.group_by(SEASON_KEY).agg(pl.col("real").sum().alias("real_minutes"))
    season = pl.read_parquet(SEASON_PARQUET).join(real_season, on=SEASON_KEY, how="left")
    assert season["real_minutes"].null_count() == 0
    pool = season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
    gated = pool.filter(pl.col("meets_min_minutes")).with_columns(
        ((pl.col("real_minutes") / pl.col("minutes_played") - 1) * 100).alias("overstated_pct"))
    stats = [pl.col("overstated_pct").min().alias("min"),
             pl.col("overstated_pct").median().alias("median"),
             pl.col("overstated_pct").max().alias("max"), pl.len()]
    print("gated pool rows (in_percentile_pool ∧ is_outfield ∧ meets_min_minutes):")
    print(gated.select(stats))
    print(gated.group_by("competition_id").agg(stats).sort("competition_id"))
    print(gated.sort("overstated_pct", descending=True)
          .select("player_name", "team_name", "minutes_played", "overstated_pct").head(3))
    print("percentile-point shift on the gated rows:")
    print(_percentile_shift(gated))
    up = pool.filter(~pl.col("meets_min_minutes") & (pl.col("real_minutes") >= MIN_MINUTES))
    down = pool.filter(pl.col("meets_min_minutes") & (pl.col("real_minutes") < MIN_MINUTES))
    print(f"pool rows that would cross {MIN_MINUTES} upward: {up.height}; downward: {down.height}")
    print(up.select("player_name", "team_name", "competition_id", "minutes_played",
                    "real_minutes").sort("real_minutes"))
    any_up = season.filter(~pl.col("meets_min_minutes") & (pl.col("real_minutes") >= MIN_MINUTES))
    print(f"all rows that would cross upward (GK included): {any_up.height}")


if __name__ == "__main__":
    report(*measure())
