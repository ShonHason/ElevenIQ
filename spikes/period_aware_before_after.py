#!/usr/bin/env python3
"""
Spike: before/after of period-aware minutes (plan 004 H4b, ADR 002 §31).

Compares the H3b output (data/interim/h3b_snapshot/, match-absolute minutes)
with the rebuilt H4 output (data/interim/player_match_stats.parquet,
data/processed/player_season_stats.parquet). Never imports eleveniq: the
decomposition re-runs spikes/verify_core_metrics.py (the independent §31
re-implementation) on the raw StatsBomb JSON, toggling one rule at a time.

Run (after the snapshot and both pipeline stages):
    .venv/bin/python spikes/period_aware_before_after.py

Reports, per league unless noted:
1. minutes before/after, Δ and %; the league reconciliation gap
   Σ(11 × match_duration) − Σ minutes, before vs after;
2. Δ per player-match kind (full match, second-half sub, other);
3. the extreme matches (3754339, 3829452);
4. every player-match whose minutes decrease, decomposed by cause;
5. match-level position_group flips and minutes_<group> shifts over 5 min;
6. season flag flips by name: meets_min_minutes (the 900-minute gate),
   is_primary_stint, position_group, is_outfield; unchanged-flag checks;
7. Erdinç and Klose (the combined-gate note);
8. the per-90 ratio on the gated pool;
9. the percentile shift on every per-90 DNA metric (goals and assists
   excluded, ADR 002 §6c; no card columns exist), local (league × group) and
   global (group), split into (a) the same cohort with new minutes and
   (b) the change of cohort membership (also shown for the rows whose own
   season group did not change: the effect of other rows joining or leaving).

Decomposition of a player-match's Δ (old stored minutes A → new N), each step
computed with verify_core_metrics.player_minutes on the §31 clock:
- clock:  Σ of his stint pieces on the Half End clock with the H3b exit cap
          (earlier match-absolute minute of dismissal and substitution), no
          windows, no unpaired-Off exit, before the union − A. It holds the
          first-half stoppage gain, early half ends, the last event after the
          whistle, and the reversed stints;
- overlap dedup: − the overlap removed by the union;
- cap realignment: the exit taken by instant instead of by match-absolute minute;
- unpaired Off: the unpaired Player Off as an exit;
- window: the treatment windows subtracted.
The five add up to N − A exactly (checked).

STOP rule (plan 004 H4b): the script prints "STOP" and exits 1 for a gate flip
downward, a change in in_percentile_pool / multi_team_season /
multi_competition_season / pool rows or is_outfield, a decomposition that does
not add up, or a position-group flip that differs from EXPECTED_FLIPS. It
changes nothing.

Expected position-group flips (signed off 2026-10-02, ADR 002 §31.7):
spikes/expected_position_flips.json lists every match-level position_group
flip by cause, every season position_group flip and every is_primary_stint
flip. Each match-level flip is classified here:
- tie_rule: the player has two stints starting at one instant with
  different groups (the §31.4 same-instant rule);
- overlap: his stints overlap on the §31 clock (latest start, e.g. Ramsey);
- stoppage_near_tie: no group loses more than 0.01 min and the gain is at
  most the match's first-half stoppage (credited to his half-time group);
- unexplained: none of these.
A flip missing from the file, an expected flip that no longer occurs, a
changed cause or any "unexplained" flip is a STOP. --write-expected rewrites
the file from the current run; use it only after the user signs off a change.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

from reference_totals import DATA_ROOT, PROJECT_ROOT, load_matches
import verify_core_metrics as core

SNAPSHOT = PROJECT_ROOT / "data" / "interim" / "h3b_snapshot"
OLD_MATCH = SNAPSHOT / "player_match_stats.parquet"
OLD_SEASON = SNAPSHOT / "player_season_stats.parquet"
NEW_MATCH = PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
NEW_SEASON = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"

LEAGUES = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1", 9: "Bundesliga"}
SEASON_ID = 27
MIN_MINUTES = 900  # config.MIN_MINUTES_THRESHOLD (not imported)
SEASON_KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
MATCH_KEY = ["match_id", "player_id"]
GROUP_COLS = ["minutes_am_winger", "minutes_cb", "minutes_dm_cm", "minutes_fb_wb",
              "minutes_gk", "minutes_st"]
EXTREMES = (3754339, 3829452)
# ADR 002 §6c/§6d: not DNA metrics (cards have no _p90 column).
NON_DNA_P90 = {"goals_p90", "assists_p90"}
CAUSES = ["clock", "overlap_dedup", "cap_realignment", "unpaired_off", "window"]
TOL = 1e-6
EXPECTED_FLIPS = Path(__file__).resolve().parent / "expected_position_flips.json"
GROUP_LOSS_TOL = 0.01  # stoppage_near_tie: no group may lose more than this


def classify_match_flips(flips: pl.DataFrame, om: pl.DataFrame, nm: pl.DataFrame) -> list[dict]:
    """Cause of each match-level position_group flip (see the module docstring)."""
    old_g = {(r["match_id"], r["player_id"]): r for r in om.select(*MATCH_KEY, *GROUP_COLS)
             .iter_rows(named=True)}
    new_g = {(r["match_id"], r["player_id"]): r for r in nm.select(*MATCH_KEY, *GROUP_COLS)
             .iter_rows(named=True)}
    out = []
    cache: dict[int, tuple] = {}
    for r in flips.sort(MATCH_KEY).iter_rows(named=True):
        mid, pid = r["match_id"], r["player_id"]
        if mid not in cache:
            events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
            lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
            play = [e for e in events if e["period"] <= 4]
            cache[mid] = (lineups, core.Clock(mid, play), core._exits(mid, play))
        lineups, clock, (dismissal, sub_off, unpaired, windows) = cache[mid]
        pos = next(p["positions"] for t in lineups for p in t["lineup"] if p["player_id"] == pid)
        starts: dict[tuple, set] = defaultdict(set)
        for st in pos:
            starts[(st["from_period"], core._mmss(st["from"]))].add(core.POSITION_GROUP[st["position"]])
        exit_at = _new_cap(dismissal.get(pid), sub_off.get(pid), unpaired.get(pid))
        _, _, overlap, _, _ = core.player_minutes(mid, pos, clock, exit_at, windows.get(pid, []),
                                                  Counter())
        diffs = [new_g[(mid, pid)][c] - old_g[(mid, pid)][c] for c in GROUP_COLS]
        if any(len(g) > 1 for g in starts.values()):
            cause = "tie_rule"
        elif overlap > TOL:
            cause = "overlap"
        elif min(diffs) > -GROUP_LOSS_TOL and sum(diffs) <= clock.end[1] - 45.0 + TOL:
            cause = "stoppage_near_tie"
        else:
            cause = "unexplained"
        out.append({"match_id": mid, "player_id": pid, "player": r["player_name"],
                    "before": r["before"], "after": r["after"], "cause": cause})
    return out


def _season_flip_rows(f: pl.DataFrame) -> list[dict]:
    return [{"player_id": r["player_id"], "team_id": r["team_id"],
             "competition_id": r["competition_id"], "player": r["player_name"],
             "team": r["team_name"], "before": r["before"], "after": r["after"]}
            for r in f.sort(SEASON_KEY).iter_rows(named=True)]


def check_expected_flips(actual: dict, stop: list[str], *, write: bool) -> None:
    """Compare the flips with EXPECTED_FLIPS; any difference is a STOP."""
    if write:
        EXPECTED_FLIPS.write_text(json.dumps(actual, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {EXPECTED_FLIPS}")
        return
    expected = json.loads(EXPECTED_FLIPS.read_text())
    unexplained = [f for f in actual["match"] if f["cause"] == "unexplained"]
    if unexplained:
        stop.append(f"{len(unexplained)} unexplained match-level flips: {unexplained[:5]}")
    for kind in ("match", "season_group", "primary_stint"):
        exp = {json.dumps(x, sort_keys=True) for x in expected[kind]}
        got = {json.dumps(x, sort_keys=True) for x in actual[kind]}
        print(f"{kind} flips: {len(got)} (expected {len(exp)}); "
              f"new {len(got - exp)}, missing {len(exp - got)}")
        if got != exp:
            stop.append(f"{kind} flips differ from {EXPECTED_FLIPS.name}: "
                        f"new {sorted(got - exp)[:5]}, missing {sorted(exp - got)[:5]}")
    print("match-level flips by cause:", dict(Counter(f["cause"] for f in actual["match"])))


def _old_cap(card: core.Instant | None, sub: core.Instant | None) -> core.Instant | None:
    """H3b stint_end_cap: the earlier exit by match-absolute minute (ADR 002 §3)."""
    exits = [x for x in (card, sub) if x is not None]
    return min(exits, key=lambda x: x[1]) if exits else None


def _new_cap(*exits: core.Instant | None) -> core.Instant | None:
    present = [x for x in exits if x is not None]
    return min(present) if present else None


def decompose() -> pl.DataFrame:
    """One row per player-match with credited time on either side: kind and Δ by cause."""
    old = {(r["match_id"], r["player_id"]): r["minutes_played"]
           for r in pl.read_parquet(OLD_MATCH).select(*MATCH_KEY, "minutes_played")
           .iter_rows(named=True)}
    rows = []
    for comp in LEAGUES:
        for m in load_matches(comp, SEASON_ID):
            mid = m["match_id"]
            events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
            lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
            play = [e for e in events if e["period"] <= 4]
            clock = core.Clock(mid, play)
            dismissal, sub_off, unpaired, windows = core._exits(mid, play)
            for team in lineups:
                for pl_ in team["lineup"]:
                    pid, pos = pl_["player_id"], pl_["positions"]
                    if not pos:
                        continue
                    card, sub, off = dismissal.get(pid), sub_off.get(pid), unpaired.get(pid)
                    win = windows.get(pid, [])

                    def minutes(exit_at, wins):
                        groups, _, overlap, _, _ = core.player_minutes(
                            mid, pos, clock, exit_at, wins, Counter())
                        return sum(groups.values()), overlap

                    x, ox = minutes(_old_cap(card, sub), [])
                    m1, _ = minutes(_new_cap(card, sub), [])
                    m2, _ = minutes(_new_cap(card, sub, off), [])
                    n, _ = minutes(_new_cap(card, sub, off), win)
                    a = old.get((mid, pid), 0.0)
                    if a == 0.0 and n == 0.0:
                        continue
                    first = min((s["from_period"], core._mmss(s["from"])) for s in pos)
                    exited = any(v is not None for v in (card, sub, off))
                    kind = ("full_match" if first == (1, 0.0) and not exited
                            else "second_half_sub" if first[0] >= 2 else "other")
                    rows.append({
                        "competition_id": comp, "match_id": mid, "player_id": pid,
                        "player": pl_["player_name"], "kind": kind, "old": a, "new": n,
                        "clock": x + ox - a, "overlap_dedup": -ox,
                        "cap_realignment": m1 - x, "unpaired_off": m2 - m1, "window": n - m2,
                        "p1_short": max(0.0, core.NOMINAL_END[1] - clock.end[1]),
                        "p2_short": max(0.0, core.NOMINAL_END[2] - clock.end.get(2, 90.0)),
                    })
    return pl.DataFrame(rows).with_columns((pl.col("new") - pl.col("old")).alias("delta"))


def percentile_shift(old_season: pl.DataFrame, new_season: pl.DataFrame) -> pl.DataFrame:
    """C4: percentile-point shift on every DNA per-90, (a) same cohort, (b) membership."""
    metrics = [c for c in new_season.columns if c.endswith("_p90") and c not in NON_DNA_P90]
    gate = pl.col("in_percentile_pool") & pl.col("is_outfield") & pl.col("meets_min_minutes")
    old_g = old_season.filter(gate).select(*SEASON_KEY, "position_group", *metrics)
    new_g = new_season.filter(gate).select(*SEASON_KEY, "position_group", *metrics)
    # (a) the OLD cohort (old membership and groups) ranked with the NEW per-90 values.
    new_vals = new_season.select(*SEASON_KEY, *[pl.col(m).alias(f"{m}__new") for m in metrics])
    a_frame = old_g.join(new_vals, on=SEASON_KEY, how="left")
    out = []
    for scope, by in (("local", ["competition_id", "position_group"]),
                      ("global", ["position_group"])):
        def pct(col: str):
            return pl.col(col).rank("average").over(by) / pl.len().over(by) * 100

        for m in metrics:
            a = a_frame.select(*SEASON_KEY, pl.col("position_group").alias("group_before"),
                               pct(m).alias("old_pct"), pct(f"{m}__new").alias("a_pct"))
            b = new_g.select(*SEASON_KEY, pl.col("position_group").alias("group_after"),
                             pct(m).alias("new_pct"))
            both = a.join(b, on=SEASON_KEY, how="inner")
            shift_a = (both["a_pct"] - both["old_pct"]).abs()
            shift_b = (both["new_pct"] - both["a_pct"]).abs()
            total = (both["new_pct"] - both["old_pct"]).abs()
            # (b) for rows that stayed in their group: only other rows joined or left.
            same = both["group_before"] == both["group_after"]
            shift_b_same = shift_b.filter(same)
            out.append({
                "scope": scope, "metric": m,
                "a_mean": shift_a.mean(), "a_max": shift_a.max(), "a_ge1": int((shift_a >= 1).sum()),
                "b_mean": shift_b.mean(), "b_max": shift_b.max(), "b_ge1": int((shift_b >= 1).sum()),
                "b_same_group_max": shift_b_same.max(),
                "b_same_group_ge1": int((shift_b_same >= 1).sum()),
                "total_mean": total.mean(), "total_max": total.max(),
                "total_ge1": int((total >= 1).sum()),
            })
    return pl.DataFrame(out)


def _flips(old: pl.DataFrame, new: pl.DataFrame, col: str, key: list[str],
           show: list[str]) -> pl.DataFrame:
    j = old.select(*key, pl.col(col).alias("before"), *[c for c in show if c not in key]).join(
        new.select(*key, pl.col(col).alias("after")), on=key, how="full", coalesce=True)
    return j.filter(pl.col("before").ne_missing(pl.col("after")))


def report_stop(stop: list[str]) -> None:
    """Print the STOP rule result; exit 1 on any STOP so a rerun cannot miss it."""
    print("\n== STOP rule ==")
    if not stop:
        print("no STOP condition")
        return
    print("\n".join(f"STOP: {s}" for s in stop))
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--write-expected", action="store_true",
                        help="rewrite expected_position_flips.json (only after a user sign-off)")
    args = parser.parse_args()
    pl.Config.set_tbl_rows(200)
    pl.Config.set_tbl_width_chars(170)
    pl.Config.set_fmt_str_lengths(40)
    stop: list[str] = []
    om, nm = pl.read_parquet(OLD_MATCH), pl.read_parquet(NEW_MATCH)
    os_, ns = pl.read_parquet(OLD_SEASON), pl.read_parquet(NEW_SEASON)
    league = pl.col("competition_id").replace_strict(LEAGUES, return_dtype=pl.Utf8).alias("league")

    print("== 1. Minutes per league ==")
    per = (om.group_by("competition_id").agg(pl.col("minutes_played").sum().alias("before"))
           .join(nm.group_by("competition_id").agg(pl.col("minutes_played").sum().alias("after")),
                 on="competition_id")
           .with_columns((pl.col("after") - pl.col("before")).alias("delta"))
           .with_columns((pl.col("delta") / pl.col("before") * 100).alias("pct"))
           .with_columns(league).sort("competition_id"))
    print(per)
    tot_b, tot_a = per["before"].sum(), per["after"].sum()
    print(f"total: {tot_b:,.1f} → {tot_a:,.1f} (Δ {tot_a - tot_b:+,.1f}, "
          f"{(tot_a - tot_b) / tot_b:+.3%})")

    def gap(df: pl.DataFrame) -> pl.DataFrame:
        team = df.group_by("competition_id", "match_id", "team_id").agg(
            pl.col("minutes_played").sum(), pl.col("match_duration").first())
        return team.group_by("competition_id").agg(
            (11 * pl.col("match_duration") - pl.col("minutes_played")).sum().alias("gap"),
            (11 * pl.col("match_duration")).sum().alias("ceiling"))
    g = gap(om).rename({"gap": "gap_before", "ceiling": "ceiling_before"}).join(
        gap(nm).rename({"gap": "gap_after", "ceiling": "ceiling_after"}), on="competition_id")
    print("League reconciliation gap Σ(11 × match_duration) − Σ minutes "
          "(exit losses, treatment windows, sub gaps):")
    print(g.with_columns(league, (pl.col("gap_before") / pl.col("ceiling_before") * 100)
                         .alias("pct_before"),
                         (pl.col("gap_after") / pl.col("ceiling_after") * 100).alias("pct_after"))
          .sort("competition_id"))

    print("\nComputing the decomposition from raw JSON (verify_core_metrics) ...")
    d = decompose()
    joined = d.join(nm.select(*MATCH_KEY, pl.col("minutes_played").alias("parquet")),
                    on=MATCH_KEY, how="left").with_columns(pl.col("parquet").fill_null(0.0))
    mismatch = joined.filter((pl.col("parquet") - pl.col("new")).abs() > TOL)
    resid = d.filter((pl.sum_horizontal(CAUSES) - pl.col("delta")).abs() > TOL)
    print(f"spike vs parquet minutes, player-matches over {TOL}: {mismatch.height}; "
          f"decomposition residual rows: {resid.height}")
    if mismatch.height or resid.height:
        stop.append("decomposition does not reproduce the parquet minutes")

    print("\n== 2. Δ per player-match kind ==")
    print(d.group_by("competition_id", "kind").agg(
        pl.len(), pl.col("delta").mean().alias("mean"), pl.col("delta").median().alias("median"),
        pl.col("delta").min().alias("min"), pl.col("delta").max().alias("max"),
        pl.col("delta").sum().alias("sum")).sort("competition_id", "kind"))
    print(d.group_by("kind").agg(pl.len(), pl.col("delta").mean().alias("mean"),
                                 pl.col("delta").sum().alias("sum")).sort("kind"))

    print("\n== 3. Extreme matches ==")
    for mid in EXTREMES:
        o = om.filter(pl.col("match_id") == mid)
        n = nm.filter(pl.col("match_id") == mid)
        print(f"match {mid}: match_duration {o['match_duration'][0]:.2f} → "
              f"{n['match_duration'][0]:.2f}; minutes {o['minutes_played'].sum():.2f} → "
              f"{n['minutes_played'].sum():.2f}; full-match Δ "
              f"{d.filter((pl.col('match_id') == mid) & (pl.col('kind') == 'full_match'))['delta'].mean():.2f}")

    print("\n== 4. Player-matches whose minutes decrease, by cause ==")
    dec = d.filter(pl.col("delta") < -TOL)
    print(f"rows: {dec.height}; minutes: {dec['delta'].sum():.2f}")
    print(dec.select([pl.col(c).filter(pl.col(c) < -TOL).len().alias(f"{c}_rows")
                      for c in CAUSES]))
    print(dec.select([pl.col(c).filter(pl.col(c) < -TOL).sum().alias(f"{c}_min")
                      for c in CAUSES]))
    clock_dec = dec.filter(pl.col("clock") < -TOL).with_columns(
        pl.when(pl.col("p1_short") > 0).then(pl.lit("early first-half end"))
        .when(pl.col("p2_short") > 0).then(pl.lit("early second-half end"))
        .otherwise(pl.lit("other clock (last event after whistle, reversed stint)"))
        .alias("clock_cause"))
    print("clock decreases by sub-cause:")
    print(clock_dec.group_by("clock_cause").agg(pl.len(), pl.col("clock").sum()))
    print("decreases not explained by an early half end (every row listed):")
    print(dec.filter(~((pl.col("clock") < -TOL) & ((pl.col("p1_short") > 0) | (pl.col("p2_short") > 0)))
                     | (pl.sum_horizontal([pl.col(c) for c in CAUSES if c != "clock"]) < -TOL))
          .select("competition_id", "match_id", "player", "kind", "old", "new", "delta", *CAUSES)
          .sort("delta"))

    print("\n== 5. Match-level position_group flips; minutes_<group> shifts over 5 min ==")
    flips = _flips(om, nm, "position_group", MATCH_KEY, ["player_name", "competition_id"])
    print(f"position_group flips: {flips.height}")
    print(flips.sort("match_id"))
    match_flips = classify_match_flips(flips, om, nm)
    shifts = om.select(*MATCH_KEY, "player_name", *GROUP_COLS).join(
        nm.select(*MATCH_KEY, *[pl.col(c).alias(f"{c}_new") for c in GROUP_COLS]),
        on=MATCH_KEY, how="inner")
    big = shifts.with_columns(pl.max_horizontal(
        [(pl.col(f"{c}_new") - pl.col(c)).abs() for c in GROUP_COLS]).alias("max_shift")
    ).filter(pl.col("max_shift") > 5)
    print(f"player-matches with a minutes_<group> shift over 5 min: {big.height}")
    print(big.select(*MATCH_KEY, "player_name", "max_shift", *[
        (pl.col(f"{c}_new") - pl.col(c)).round(2).alias(c.removeprefix("minutes_"))
        for c in GROUP_COLS]).sort("max_shift", descending=True))

    print("\n== 6. Season flag flips ==")
    show = ["player_name", "team_name", "competition_id", "minutes_played"]
    new_min = ns.select(*SEASON_KEY, pl.col("minutes_played").alias("minutes_after"))
    gate = _flips(os_, ns, "meets_min_minutes", SEASON_KEY, show).join(
        new_min, on=SEASON_KEY, how="left").join(
        ns.select(*SEASON_KEY, "in_percentile_pool", "is_outfield"), on=SEASON_KEY, how="left")
    up, down = gate.filter(pl.col("after")), gate.filter(~pl.col("after"))
    print(f"meets_min_minutes: {up.height} up, {down.height} down")
    print(gate.select("player_name", "team_name", "competition_id", "in_percentile_pool",
                      "is_outfield", pl.col("minutes_played").alias("minutes_before"),
                      "minutes_after", "before", "after").sort("competition_id", "minutes_after"))
    pool_up = up.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
    print(f"pool outfield rows crossing 900 upward: {pool_up.height} "
          f"({dict(Counter(LEAGUES[c] for c in pool_up['competition_id']))})")
    if down.height:
        stop.append(f"{down.height} rows fell below the 900-minute gate")
    season_flips = {}
    for col in ("is_primary_stint", "position_group", "is_outfield"):
        f = _flips(os_, ns, col, SEASON_KEY, show).join(new_min, on=SEASON_KEY, how="left")
        season_flips[col] = f
        print(f"{col} flips: {f.height}")
        if f.height:
            print(f.sort("competition_id", "player_name"))
    if season_flips["is_outfield"].height:
        stop.append("is_outfield changed")
    print("\nExpected position-group flips (STOP check):")
    check_expected_flips({
        "match": match_flips,
        "season_group": _season_flip_rows(season_flips["position_group"]),
        "primary_stint": _season_flip_rows(season_flips["is_primary_stint"]),
    }, stop, write=args.write_expected)
    for col in ("in_percentile_pool", "multi_team_season", "multi_competition_season"):
        f = _flips(os_, ns, col, SEASON_KEY, show)
        print(f"{col}: {ns[col].sum()} (before {os_[col].sum()}), flips {f.height}")
        if f.height:
            stop.append(f"{col} changed")
    pool_b, pool_a = os_["in_percentile_pool"].sum(), ns["in_percentile_pool"].sum()
    print(f"pool rows: {pool_b} → {pool_a}; season rows: {os_.height} → {ns.height}")
    if pool_b != pool_a or os_.height != ns.height:
        stop.append("pool or season row count changed")

    print("\n== 7. Erdinç and Klose (combined-gate note) ==")
    print(ns.filter(pl.col("player_name").str.contains("Erdinç|Klose"))
          .join(os_.select(*SEASON_KEY, pl.col("minutes_played").alias("minutes_before")),
                on=SEASON_KEY, how="left")
          .select("player_name", "team_name", "competition_id", "in_percentile_pool",
                  "minutes_before", "minutes_played", "meets_min_minutes"))

    print("\n== 8. Per-90 ratio on the gated pool (old per-90 / new per-90 − 1) ==")
    gated = ns.filter(pl.col("in_percentile_pool") & pl.col("is_outfield")
                      & pl.col("meets_min_minutes")).join(
        os_.select(*SEASON_KEY, pl.col("minutes_played").alias("minutes_before")),
        on=SEASON_KEY, how="inner").with_columns(
        ((pl.col("minutes_played") / pl.col("minutes_before") - 1) * 100).alias("overstated_pct"))
    stats = [pl.len(), pl.col("overstated_pct").min().alias("min"),
             pl.col("overstated_pct").median().alias("median"),
             pl.col("overstated_pct").max().alias("max")]
    print(gated.select(stats))
    print(gated.group_by("competition_id").agg(stats).with_columns(league).sort("competition_id"))

    print("\n== 9. Percentile shift, every DNA per-90 (gated pool) ==")
    shift = percentile_shift(os_, ns)
    for scope in ("local", "global"):
        print(f"-- {scope} --")
        print(shift.filter(pl.col("scope") == scope).drop("scope").sort("total_max", descending=True))
    print(shift.group_by("scope").agg(
        pl.col("total_max").max(), pl.col("total_mean").mean(), pl.col("a_max").max(),
        pl.col("a_mean").mean(), pl.col("b_max").max(), pl.col("b_same_group_max").max(),
        pl.col("b_same_group_ge1").max()))

    report_stop(stop)


if __name__ == "__main__":
    main()
