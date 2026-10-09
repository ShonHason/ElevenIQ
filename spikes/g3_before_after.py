"""
G3 STOP rule: per-position minutes and match dates through the pipeline (plan 006 gate G3).

Compares the snapshot taken before the rebuild (data/interim/g3_snapshot/) with the current
outputs, and checks the new data against independent sources. Stops (exit 1) unless:
- player_match_stats.parquet keeps every existing column byte-identical and gains exactly
  match_date and position_minutes; player_season_stats.parquet is unchanged;
- the measurements change in provenance only (match_parquet_sha256, equal to the current file);
- the registry snapshots agree on everything except registry_version, which must change;
- the percentiles, DNA and dimensions parquets are identical except their stamp columns;
- player_season_positions.parquet reproduces m4g_common.position_minutes (the spike's second
  §31 implementation, run with each position as its own group) within 1e-9 for every season
  row: the same keys, the same positions; and its minutes per row, mapped to groups with the
  spike's own POSITION_GROUP, equal the season parquet's minutes_<group> within 1e-9;
- match_date equals the raw match file for every match, and the match ids agree.
Report only (user, 2026-10-09; not fixed here): Player On events whose time is not credited on
the reference clock (the §31 re-entry backlog item, plan 005), with the rows of the new table
they touch and the minutes not credited.
Never imports eleveniq.

  .venv/bin/python tools/registry_snapshot.py --out data/interim/g3_after/registry.json
  .venv/bin/python spikes/g3_before_after.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_before_after as g1  # noqa: E402  (sibling spike: frame and registry comparisons)
import m4g_common as cm  # noqa: E402  (sibling spike: position_minutes)
import verify_core_metrics as vcm  # noqa: E402  (sibling spike: the §31 reference clock)
from reference_totals import DATA_ROOT, load_matches  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "data" / "interim" / "g3_snapshot"
AFTER_REGISTRY = ROOT / "data" / "interim" / "g3_after" / "registry.json"
MATCH = ROOT / "data" / "interim" / "player_match_stats.parquet"
SEASON = ROOT / "data" / "processed" / "player_season_stats.parquet"
POSITIONS = ROOT / "data" / "processed" / "player_season_positions.parquet"
NEW = ("match_date", "position_minutes")
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")
# The season parquet's competitions (config.COMPETITION_SEASONS, written out: no eleveniq);
# checked against the parquet below.
COMPS = ((2, 27), (7, 27), (9, 27), (11, 27), (12, 27))
TOL = 1e-9
GROUP_COL = {"GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
             "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger", "ST": "minutes_st"}


def match_errors(before: pl.DataFrame, after: pl.DataFrame) -> list[str]:
    errs = []
    if [c for c in after.columns if c not in NEW] != before.columns:
        errs.append("player_match_stats: existing columns lost, added or reordered")
    errs += [f"player_match_stats: {c} missing" for c in NEW if c not in after.columns]
    if before.height != after.height:
        return errs + [f"player_match_stats: {before.height} → {after.height} rows"]
    for c in before.columns:
        if c in after.columns and not (before[c].dtype == after[c].dtype
                                       and before[c].equals(after[c])):
            errs.append(f"player_match_stats: column {c} changed")
    return errs


def measurement_errors(before: dict, after: dict) -> list[str]:
    errs = [f"measurements: section {k!r} changed" for k in sorted(set(before) | set(after))
            if k != "provenance" and before.get(k) != after.get(k)]
    bp, ap = before["provenance"], after["provenance"]
    errs += [f"provenance: {k!r} changed" for k in sorted(set(bp) | set(ap))
             if k != "match_parquet_sha256" and bp.get(k) != ap.get(k)]
    if ap["match_parquet_sha256"] != g1.sha256(MATCH):
        errs.append("provenance: match_parquet_sha256 is not the current file's sha256")
    return errs


def positions_errors(season: pl.DataFrame, positions: pl.DataFrame) -> tuple[list[str], int]:
    """The new table against the spike's position_minutes and the season group minutes."""
    errs = []
    seen = {(r["competition_id"], r["season_id"]) for r in season.iter_rows(named=True)}
    if seen != set(COMPS):
        return [f"season competitions {sorted(seen)} != COMPS {sorted(COMPS)}"], 0
    if set(season["source"].unique()) != {"statsbomb"}:
        return ["season rows from a source other than statsbomb"], 0
    keys = {tuple(r[c] for c in KEY) for r in season.iter_rows(named=True)}
    ref = cm.position_minutes(keys, comps=COMPS)         # (pid, team, comp) -> position -> min
    got: dict[tuple, dict[str, float]] = {}
    for r in positions.iter_rows(named=True):
        got.setdefault(tuple(r[c] for c in KEY), {})[r["position"]] = r["minutes"]
    if set(got) != keys:
        errs.append(f"positions keys differ from the season rows: {len(set(got) ^ keys)} differ")
    worst = 0.0
    for k in sorted(keys):
        want = {p: v for p, v in ref.get(k[:3], Counter()).items() if v > 0}
        have = got.get(k, {})
        if set(want) != set(have):
            errs.append(f"{k}: positions {sorted(have)} != spike {sorted(want)}")
            continue
        for p, v in want.items():
            worst = max(worst, abs(have[p] - v))
            if abs(have[p] - v) > TOL:
                errs.append(f"{k} {p}: {have[p]!r} != spike {v!r}")
    # Group sums, with the spike's own position → group map.
    by_key = {tuple(r[c] for c in KEY): r for r in season.iter_rows(named=True)}
    for k, pos in got.items():
        sums: Counter = Counter()
        for p, v in pos.items():
            sums[vcm.POSITION_GROUP[p]] += v
        for g, col in GROUP_COL.items():
            if abs(sums.get(g, 0.0) - by_key[k][col]) > TOL:
                errs.append(f"{k}: {g} positions {sums.get(g, 0.0)!r} != {col} {by_key[k][col]!r}")
    if by_key and any(r["minutes_unknown"] for r in by_key.values()):
        errs.append("season rows with minutes_unknown > 0 (no position group to compare)")
    print(f"positions: {positions.height} rows over {len(got)} season rows; max |diff| vs the "
          f"spike {worst:.2e}")
    return errs, len(got)


def date_errors(match: pl.DataFrame) -> list[str]:
    raw: dict[int, dt.date] = {}
    for comp, season in COMPS:
        for m in load_matches(comp, season):
            raw[m["match_id"]] = dt.date.fromisoformat(m["match_date"])
    pairs = match.select("match_id", "match_date").unique()
    errs = []
    if set(pairs["match_id"]) != set(raw):
        errs.append(f"match ids differ from the raw match files: "
                    f"{len(set(pairs['match_id']) ^ set(raw))}")
    if pairs.height != pairs["match_id"].n_unique():
        errs.append("a match has more than one match_date")
    bad = [r for r in pairs.iter_rows(named=True) if raw.get(r["match_id"]) != r["match_date"]]
    errs += [f"match {r['match_id']}: {r['match_date']} != raw {raw.get(r['match_id'])}"
             for r in bad[:10]]
    print(f"match_date: {pairs.height} matches checked against the raw match files")
    return errs


def reentry_report(match: pl.DataFrame) -> None:
    """Player On events whose instant is not inside the player's credited time on the reference
    clock, and the minutes until his credited time resumes (or his period ends)."""
    team_of = {(r["match_id"], r["player_id"]): (r["team_id"], r["competition_id"])
               for r in match.select("match_id", "player_id", "team_id", "competition_id")
               .iter_rows(named=True)}
    hits = []
    n_on = after_exit = 0
    for comp, season in COMPS:
        for m in load_matches(comp, season):
            mid = m["match_id"]
            with open(DATA_ROOT / "events" / f"{mid}.json") as f:
                events = json.load(f)
            with open(DATA_ROOT / "lineups" / f"{mid}.json") as f:
                lineups = json.load(f)
            teams, clock = vcm.match_minutes(mid, events, lineups, Counter())
            segs = {p["player_id"]: p["segments"] for t in teams for p in t["players"]}
            exit_at = {p["player_id"]: p["exit"] for t in teams for p in t["players"]}
            for e in events:
                if e["period"] > 4 or e["type"]["name"] != "Player On":
                    continue
                n_on += 1
                pid = e["player"]["id"]
                p = e["period"]
                t = clock.elapsed(vcm._at(e))
                end = clock.offset[p] + clock.length(p)
                s = segs.get(pid, [])
                if t >= end - vcm.TIME_EPS or any(a <= t + vcm.TIME_EPS and t < b for a, b, _ in s):
                    continue
                ex = exit_at.get(pid)
                if ex is not None and vcm._at(e) >= ex:
                    after_exit += 1          # after his substitution or dismissal: not credited by §31
                    continue
                resume = min([a for a, _, _ in s if a > t] + [end])
                lost = min(resume, end) - t
                if lost > vcm.TIME_EPS:
                    hits.append({"match_id": mid, "player_id": pid, "period": p,
                                 "at": f"{e['minute']}:{e['second']:02d}", "lost": lost,
                                 "row": (pid, *team_of.get((mid, pid), (None, None)))})
    rows = {h["row"] for h in hits}
    total = sum(h["lost"] for h in hits)
    print(f"§31 re-entry (report only): {len(hits)} of {n_on} Player On events not credited, "
          f"{len({(h['match_id'], h['player_id']) for h in hits})} player-match rows, "
          f"{len(rows)} season rows of player_season_positions; {total:.2f} min not credited "
          f"(max {max((h['lost'] for h in hits), default=0.0):.2f}); {after_exit} more come after "
          "the player's own substitution or dismissal (not re-entries)")
    for h in sorted(hits, key=lambda h: -h["lost"])[:10]:
        print(f"  match {h['match_id']} player {h['player_id']} P{h['period']} {h['at']}: "
              f"{h['lost']:.2f} min")


def main() -> None:
    errs: list[str] = []
    match = pl.read_parquet(MATCH)
    errs += match_errors(pl.read_parquet(BEFORE / "player_match_stats.parquet"), match)
    if g1.sha256(BEFORE / "player_season_stats.parquet") != g1.sha256(SEASON):
        errs.append("player_season_stats.parquet changed")
    for name, path in g1.STAMPED.items():
        errs += g1.stamped_errors(name, pl.read_parquet(BEFORE / name), pl.read_parquet(path))
    errs += measurement_errors(json.loads((BEFORE / "measurements_old.json").read_text()),
                               json.loads(g1.MEASUREMENTS.read_text()))
    rb = json.loads((BEFORE / "registry.json").read_text())
    ra = json.loads(AFTER_REGISTRY.read_text())
    errs += g1.registry_errors(rb, ra)
    errs += date_errors(match)
    pos_errs, n_rows = positions_errors(pl.read_parquet(SEASON), pl.read_parquet(POSITIONS))
    errs += pos_errs
    reentry_report(match)
    if errs:
        print("\n".join(errs[:50]), file=sys.stderr)
        print(f"FAIL: {len(errs)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: player_match_stats gains only {', '.join(NEW)}; season parquet unchanged; "
          f"measurements provenance only; 0 registry changes; percentiles / DNA / dimensions "
          f"stamps only; positions match the spike for {n_rows} season rows; "
          f"REGISTRY_VERSION {rb['registry_version'][:12]}… → {ra['registry_version'][:12]}…")


if __name__ == "__main__":
    main()
