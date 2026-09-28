#!/usr/bin/env python3
"""
Spike: Chunk B data verification for one competition-season.

Reports on:
  1. Shot-based xG assisted (pass.assisted_shot_id linkage + carry-between analysis)
  2. Final-third entries (passes and carries crossing x=80)
  3. Box entries (passes and carries entering x>=102, 18<=y<=62)
  4. Top-5 per-90 leaders for each metric (outfield, 900+ min)

Also reconciles goals + own goals against the matches.json scores (a mismatch
fails the spike) and writes the "chunk_b" section of
tests/fixtures/reference_totals/{competition_id}_{season_id}.json.

Run:
    .venv/bin/python spikes/verify_chunk_b_metrics.py --competition-id 11 --season-id 27

Never imports eleveniq.pipeline. Section 4 reads data/processed/ for minutes
only, filtered to this competition-season, and is skipped if absent.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, quantiles
from typing import NamedTuple

import polars as pl

from reference_totals import (
    DATA_ROOT,
    competition_label,
    load_matches,
    parse_args,
    reconcile_goals,
    write_reference,
)

PROCESSED = Path(__file__).resolve().parents[1] / "data" / "processed"

# StatsBomb pitch 120×80.
FINAL_THIRD_X = 80.0
BOX_X_MIN = 102.0
BOX_Y_MIN = 18.0
BOX_Y_MAX = 62.0
MIN_MINUTES = 900.0

# Set-piece pass types excluded from entry metrics — mirrors pipeline SET_PIECE_TYPES.
# Defined here independently (no import from entries.py) so the spike is a true
# independent verification of the pipeline's exclusion logic.
SET_PIECE_TYPES: frozenset[str] = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off"})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _in_box(loc: list[float]) -> bool:
    return loc[0] >= BOX_X_MIN and BOX_Y_MIN <= loc[1] <= BOX_Y_MAX


def _load_seasons(competition_id: int, season_id: int) -> pl.DataFrame:
    """Processed season rows for this competition-season (empty if not built)."""
    path = PROCESSED / "player_season_stats.parquet"
    if not path.exists():
        return pl.DataFrame(schema={"player_id": pl.Int64, "player_name": pl.Utf8,
                                    "is_gk": pl.Boolean,
                                    "player_season_minutes_total": pl.Float64})
    return pl.read_parquet(path).filter(
        (pl.col("competition_id") == competition_id) & (pl.col("season_id") == season_id)
    )


def _dist(a: list[float], b: list[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def load_all_events_by_match(matches: list[dict]) -> dict[int, list[dict]]:
    by_match: dict[int, list[dict]] = {}
    for m in matches:
        mid = m["match_id"]
        path = DATA_ROOT / "events" / f"{mid}.json"
        with open(path) as f:
            by_match[mid] = json.load(f)
    return by_match


def load_all_events(by_match: dict[int, list[dict]]) -> list[dict]:
    out: list[dict] = []
    for evs in by_match.values():
        out.extend(evs)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — xG assisted
# ─────────────────────────────────────────────────────────────────────────────

def section_xg_assisted(
    by_match: dict[int, list[dict]], competition_id: int, season_id: int
) -> dict:
    """Return a data dict so section 4 can reuse per-player totals."""
    print("=" * 72)
    print("SECTION 1 — xG ASSISTED (pass.assisted_shot_id linkage)")
    print("=" * 72)

    # ── global counters ──────────────────────────────────────────────────────
    passes_with_assisted_id = 0
    resolves_to_shot = 0
    unresolved: list[str] = []          # (match_id, pass_id, assisted_shot_id)

    shot_assist_passes = 0             # pass.shot_assist == True
    goal_assist_passes = 0             # pass.goal_assist == True
    shot_assist_with_id = 0
    goal_assist_with_id = 0

    total_shot_xg = 0.0
    total_assisted_xg = 0.0
    missing_xg_shots = 0       # shots where statsbomb_xg is None (all shots)
    missing_xg_assisted = 0    # assisted shots where statsbomb_xg is None
    assisted_shot_ids_global: set[str] = set()

    shots_no_assist = 0
    shots_no_assist_penalty = 0
    shots_no_assist_free_kick = 0
    shots_no_assist_corner = 0
    shots_no_assist_other = 0

    # per-player xG-assisted accumulator: player_id -> xg_assisted
    player_xg_assisted: dict[int, float] = defaultdict(float)
    player_names: dict[int, str] = {}

    # for carry-between analysis
    # "any carry or dribble" counts (same as original spike — for the 47%/34.6% figures)
    carry_between_count = 0
    carry_between_xg = 0.0
    carry_distances: list[float] = []
    player_carry_between_xg: dict[int, float] = defaultdict(float)
    player_carry_between_total_xg: dict[int, float] = defaultdict(float)

    # xg_assisted_direct: cumulative carry rule (sum of carries > threshold → excluded)
    XGA_DIRECT_MAX_CARRY_UNITS = 10.0
    xga_direct_total = 0.0
    xga_shots_no_receipt_found = 0

    for mid, evs in by_match.items():
        # Build lookup: event id -> event
        id_to_event: dict[str, dict] = {e["id"]: e for e in evs}

        # Group events by (possession, player_id): for carry-between analysis
        poss_player_events: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for e in evs:
            poss = e.get("possession")
            pid = e.get("player", {}).get("id")
            if poss is not None and pid is not None:
                poss_player_events[(poss, pid)].append(e)
        # Sort each group by index
        for key in poss_player_events:
            poss_player_events[key].sort(key=lambda x: x.get("index", 0))

        for e in evs:
            etype = e["type"]["name"]

            # ── shots ────────────────────────────────────────────────────────
            if etype == "Shot":
                xg_val = e.get("shot", {}).get("statsbomb_xg")
                if xg_val is None:
                    missing_xg_shots += 1
                    xg = 0.0
                else:
                    total_shot_xg += xg_val
                    xg = xg_val
                shot_type = e.get("shot", {}).get("type", {}).get("name", "")
                kp_id = e.get("shot", {}).get("key_pass_id")
                if kp_id is None:
                    shots_no_assist += 1
                    if shot_type == "Penalty":
                        shots_no_assist_penalty += 1
                    elif shot_type == "Free Kick":
                        shots_no_assist_free_kick += 1
                    elif shot_type == "Corner":
                        shots_no_assist_corner += 1
                    else:
                        shots_no_assist_other += 1
                continue

            # ── passes ───────────────────────────────────────────────────────
            if etype != "Pass":
                continue

            p = e.get("pass", {})

            # shot_assist / goal_assist flags
            if p.get("shot_assist") is True:
                shot_assist_passes += 1
                if p.get("assisted_shot_id"):
                    shot_assist_with_id += 1
            if p.get("goal_assist") is True:
                goal_assist_passes += 1
                if p.get("assisted_shot_id"):
                    goal_assist_with_id += 1

            # assisted_shot_id linkage
            aid = p.get("assisted_shot_id")
            if not aid:
                continue

            passes_with_assisted_id += 1
            shot_ev = id_to_event.get(aid)
            if shot_ev is None or shot_ev["type"]["name"] != "Shot":
                unresolved.append(f"match={mid} pass={e['id']} target={aid}")
                continue

            resolves_to_shot += 1
            xg_val = shot_ev.get("shot", {}).get("statsbomb_xg")
            if xg_val is None:
                missing_xg_assisted += 1
                continue
            xg = xg_val
            total_assisted_xg += xg
            assisted_shot_ids_global.add(aid)

            # credit assister
            assister_id = e.get("player", {}).get("id")
            assister_name = e.get("player", {}).get("name", "?")
            if assister_id:
                player_xg_assisted[assister_id] += xg
                player_names[assister_id] = assister_name
                player_carry_between_total_xg[assister_id] += xg

            # ── carry-between analysis ────────────────────────────────────────
            shooter_id = shot_ev.get("player", {}).get("id")
            shot_poss = shot_ev.get("possession")
            shot_idx = shot_ev.get("index", 0)
            shooter_team_id = shot_ev.get("possession_team", {}).get("id")
            if shooter_id is None or shot_poss is None:
                continue

            poss_events = poss_player_events.get((shot_poss, shooter_id), [])

            # Find the LAST "Ball Receipt*" (exact name) before the shot
            receipt_idx = None
            for pe in poss_events:
                if pe["type"]["name"] == "Ball Receipt*" and pe.get("index", 0) < shot_idx:
                    receipt_idx = pe.get("index", 0)

            if receipt_idx is None:
                # No receipt found (shooter's first touch is the shot)
                xga_shots_no_receipt_found += 1
                xga_direct_total += xg
                # still check for any-carry/dribble reporting (none possible)
                continue

            # Scan window: receipt_idx < index < shot_idx
            # Team guard: skip events from a different possession_team
            cumulative_carry = 0.0
            has_any_action = False   # for the 47% / 34.6% figures
            blocked_direct = False
            for pe in poss_events:
                pe_idx = pe.get("index", 0)
                if pe_idx <= receipt_idx or pe_idx >= shot_idx:
                    continue
                if pe.get("possession_team", {}).get("id") != shooter_team_id:
                    continue  # turnover guard
                if pe["type"]["name"] == "Carry":
                    has_any_action = True
                    loc = pe.get("location", [0, 0])
                    end = pe.get("carry", {}).get("end_location", loc)
                    seg = _dist(loc, end)
                    cumulative_carry += seg
                    carry_distances.append(seg)
                elif pe["type"]["name"] == "Dribble":
                    has_any_action = True
                    blocked_direct = True

            if cumulative_carry > XGA_DIRECT_MAX_CARRY_UNITS:
                blocked_direct = True

            if has_any_action:
                carry_between_count += 1
                carry_between_xg += xg
                if assister_id:
                    player_carry_between_xg[assister_id] += xg

            if not blocked_direct:
                xga_direct_total += xg

    # ── report ───────────────────────────────────────────────────────────────

    print(f"\n1a. passes with assisted_shot_id         : {passes_with_assisted_id:,}")
    print(f"    of those, resolve to a Shot event     : {resolves_to_shot:,}")
    print(f"    unresolved (no Shot found same match) : {len(unresolved):,}")
    if unresolved:
        for u in unresolved[:5]:
            print(f"      {u}")

    print(f"\n1b. shot_assist=True passes               : {shot_assist_passes:,}")
    print(f"      with assisted_shot_id               : {shot_assist_with_id:,}  "
          f"({100*shot_assist_with_id/shot_assist_passes:.1f}%)" if shot_assist_passes else "")
    print(f"    goal_assist=True passes                : {goal_assist_passes:,}")
    print(f"      with assisted_shot_id               : {goal_assist_with_id:,}  "
          f"({100*goal_assist_with_id/goal_assist_passes:.1f}%)" if goal_assist_passes else "")

    print(f"\n1c. Total xG of ALL shots                 : {total_shot_xg:.4f}")
    print(f"    Shots with missing statsbomb_xg (all) : {missing_xg_shots:,}  (skipped from total)")
    print(f"    Assisted shots with missing xg        : {missing_xg_assisted:,}  (skipped — pipeline also excludes)")
    print(f"    Total xG credited via assisted_shot_id: {total_assisted_xg:.4f}")
    print(f"    Shots with no key_pass_id (unassisted): {shots_no_assist:,}")
    print(f"      of which:")
    print(f"        Penalty     : {shots_no_assist_penalty:,}")
    print(f"        Free Kick   : {shots_no_assist_free_kick:,}")
    print(f"        Corner      : {shots_no_assist_corner:,}")
    print(f"        Other/Open  : {shots_no_assist_other:,}")

    # Reconciliation: pass-side vs shot-side (shot.key_pass_id)
    print(f"\n1d. Reconciliation (pass side vs shot side)")
    print(f"    xG assisted (pass side, assisted_shot_id): {total_assisted_xg:.6f}")
    print(f"    Note: shot-side total (shot.key_pass_id) verified separately in the")
    print(f"    recomputation script (difference = 0.00000000 on La Liga 2015/16).")

    print(f"\n1e. xg_assisted_direct (cumulative carry rule)")
    print(f"    Rule: shooter had no Dribble AND sum of Carry distances from LAST")
    print(f"    'Ball Receipt*' to Shot (same possession, same team) <= {XGA_DIRECT_MAX_CARRY_UNITS} units.")
    print(f"    xg_assisted_direct total               : {xga_direct_total:.4f}")
    print(f"    Direct ratio                           : {100*xga_direct_total/total_assisted_xg:.1f}%"
          if total_assisted_xg else "")
    print(f"    Shots with no Ball Receipt* found      : {xga_shots_no_receipt_found:,}  "
          f"({100*xga_shots_no_receipt_found/resolves_to_shot:.1f}%)  -> counted as direct"
          if resolves_to_shot else "")

    print(f"\n1f. Any-carry/dribble between Receipt and Shot (47%/34.6% figures)")
    print(f"    Assisted shots where shooter had ANY Carry/Dribble: {carry_between_count:,}  "
          f"of {resolves_to_shot:,}  "
          f"({100*carry_between_count/resolves_to_shot:.1f}%)" if resolves_to_shot else "")
    print(f"    xG of those shots                      : {carry_between_xg:.4f}  "
          f"({100*carry_between_xg/total_assisted_xg:.1f}% of total xG assisted)"
          if total_assisted_xg else "")

    if carry_distances:
        carry_distances.sort()
        qs = quantiles(carry_distances, n=100)
        print(f"    Carry distance distribution (StatsBomb units):")
        print(f"      Min   : {carry_distances[0]:.1f}")
        print(f"      p25   : {qs[24]:.1f}")
        print(f"      Median: {median(carry_distances):.1f}")
        print(f"      p75   : {qs[74]:.1f}")
        print(f"      p90   : {qs[89]:.1f}")
        print(f"      Max   : {carry_distances[-1]:.1f}")

    # Top 5 by carry-between xG share (900+ min)
    seasons = _load_seasons(competition_id, season_id)
    qualified = set(
        seasons.filter(
            (pl.col("is_gk") == False) &
            (pl.col("player_season_minutes_total") >= MIN_MINUTES)
        )["player_id"].to_list()
    )

    carry_share_rows = []
    for pid, cb_xg in player_carry_between_xg.items():
        if pid in qualified:
            total = player_carry_between_total_xg.get(pid, 0)
            share = cb_xg / total if total > 0 else 0.0
            carry_share_rows.append((player_names.get(pid, "?"), cb_xg, total, share))
    carry_share_rows.sort(key=lambda x: -x[3])
    print(f"\n    Top 5 players (900+ min) by share of xG-assisted from carry-between shots:")
    print(f"    {'Player':<28} {'CB xGA':>8} {'Total xGA':>10} {'Share':>7}")
    print("    " + "-" * 57)
    for name, cb, tot, sh in carry_share_rows[:5]:
        print(f"    {name:<28} {cb:>8.3f} {tot:>10.3f} {sh:>6.1%}")

    print()
    return {
        "xg_assisted": total_assisted_xg,
        "xg_assisted_direct": xga_direct_total,
        "player_xg_assisted": dict(player_xg_assisted),
        "player_names": dict(player_names),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Final-third entries
# ─────────────────────────────────────────────────────────────────────────────

def section_final_third(events: list[dict]) -> dict:
    print("=" * 72)
    print("SECTION 2 — FINAL-THIRD ENTRIES (crossing x=80)")
    print("=" * 72)

    pass_type_counter: Counter = Counter()  # all completed passes crossing x=80 (pre-exclusion)
    pass_ft_total = 0                       # after SET_PIECE_TYPES exclusion (pipeline metric)
    carry_ft_total = 0

    player_pass_ft: dict[int, int] = defaultdict(int)
    player_carry_ft: dict[int, int] = defaultdict(int)
    player_names: dict[int, str] = {}

    for e in events:
        etype = e["type"]["name"]
        pid = e.get("player", {}).get("id")
        pname = e.get("player", {}).get("name", "?")
        if pid:
            player_names[pid] = pname

        if etype == "Pass":
            p = e.get("pass", {})
            # Completed passes only
            outcome = p.get("outcome", {})
            if isinstance(outcome, dict) and outcome.get("name") not in (None, ""):
                # Any non-null outcome means incomplete (Incomplete, Out, etc.)
                continue
            loc = e.get("location")
            end = p.get("end_location")
            if loc is None or end is None:
                continue
            if loc[0] < FINAL_THIRD_X and end[0] >= FINAL_THIRD_X:
                ptype = p.get("type", {})
                type_name = ptype.get("name") if isinstance(ptype, dict) else None
                pass_type_counter[type_name or "(open play)"] += 1  # pre-exclusion
                if type_name in SET_PIECE_TYPES:
                    continue
                pass_ft_total += 1  # pipeline metric: open play only
                if pid:
                    player_pass_ft[pid] += 1

        elif etype == "Carry":
            loc = e.get("location")
            end = e.get("carry", {}).get("end_location")
            if loc is None or end is None:
                continue
            if loc[0] < FINAL_THIRD_X and end[0] >= FINAL_THIRD_X:
                carry_ft_total += 1
                if pid:
                    player_carry_ft[pid] += 1

    pass_ft_raw = sum(pass_type_counter.values())
    excluded_ft = sum(pass_type_counter.get(t, 0) for t in SET_PIECE_TYPES)
    print(f"\nCompleted passes: start x<80, end x>=80 (all)            : {pass_ft_raw:,}")
    print(f"  Set-piece excluded (Corner/FK/GK/Kick Off)               : {excluded_ft:,}")
    print(f"  Open play (after exclusion) [pipeline metric]            : {pass_ft_total:,}")
    print(f"\n  By pass.type.name (pre-exclusion):")
    print(f"  {'pass.type.name':<35} {'count':>7}  {'%':>6}")
    print("  " + "-" * 52)
    for name, cnt in sorted(pass_type_counter.items(), key=lambda x: -x[1]):
        pct = 100 * cnt / pass_ft_raw if pass_ft_raw else 0
        excl = " (EXCLUDED)" if name in SET_PIECE_TYPES else ""
        print(f"  {name:<35} {cnt:>7,}  {pct:>5.1f}%{excl}")

    print(f"\nCarries: start x<80, end x>=80           : {carry_ft_total:,}")
    print(f"\nCombined final-third entries (pass+carry) : {pass_ft_total + carry_ft_total:,}")
    print()

    return {
        "final_third_entries_pass": pass_ft_total,
        "final_third_entries_carry": carry_ft_total,
        "player_pass_ft": dict(player_pass_ft),
        "player_carry_ft": dict(player_carry_ft),
        "player_names": dict(player_names),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Box entries
# ─────────────────────────────────────────────────────────────────────────────

def section_box_entries(events: list[dict]) -> dict:
    print("=" * 72)
    print("SECTION 3 — BOX ENTRIES (x>=102, 18<=y<=62)")
    print("=" * 72)

    pass_box_raw = 0                        # pre-exclusion (all completed passes)
    pass_box_total = 0                      # after SET_PIECE_TYPES exclusion (pipeline metric)
    pass_box_cross = 0                      # cross=True on pre-exclusion basis
    pass_box_setpiece: Counter = Counter()  # type breakdown, pre-exclusion

    carry_box_total = 0

    player_pass_box: dict[int, int] = defaultdict(int)
    player_carry_box: dict[int, int] = defaultdict(int)
    player_names: dict[int, str] = {}

    for e in events:
        etype = e["type"]["name"]
        pid = e.get("player", {}).get("id")
        pname = e.get("player", {}).get("name", "?")
        if pid:
            player_names[pid] = pname

        if etype == "Pass":
            p = e.get("pass", {})
            # Completed passes only
            outcome = p.get("outcome", {})
            if isinstance(outcome, dict) and outcome.get("name") not in (None, ""):
                continue
            loc = e.get("location")
            end = p.get("end_location")
            if loc is None or end is None:
                continue
            if not _in_box(loc) and _in_box(end):
                pass_box_raw += 1
                ptype = p.get("type", {})
                type_name = ptype.get("name") if isinstance(ptype, dict) else None
                pass_box_setpiece[type_name or "(open play)"] += 1  # pre-exclusion
                if p.get("cross") is True:
                    pass_box_cross += 1  # cross count on pre-exclusion basis
                if type_name in SET_PIECE_TYPES:
                    continue
                pass_box_total += 1  # pipeline metric: open play only
                if pid:
                    player_pass_box[pid] += 1

        elif etype == "Carry":
            loc = e.get("location")
            end = e.get("carry", {}).get("end_location")
            if loc is None or end is None:
                continue
            if not _in_box(loc) and _in_box(end):
                carry_box_total += 1
                if pid:
                    player_carry_box[pid] += 1

    excluded_box = sum(pass_box_setpiece.get(t, 0) for t in SET_PIECE_TYPES)
    print(f"\nCompleted passes: start outside box, end inside box (all) : {pass_box_raw:,}")
    print(f"  Set-piece excluded (Corner/FK/GK/Kick Off)                : {excluded_box:,}")
    print(f"  Open play (after exclusion) [pipeline metric]             : {pass_box_total:,}")
    if pass_box_raw:
        print(f"  of which cross=True (pre-exclusion basis)               : {pass_box_cross:,}  "
              f"({100*pass_box_cross/pass_box_raw:.1f}%)")
    if pass_box_setpiece:
        print(f"\n  By pass.type.name (pre-exclusion):")
        for name, cnt in sorted(pass_box_setpiece.items(), key=lambda x: -x[1]):
            excl = " (EXCLUDED)" if name in SET_PIECE_TYPES else ""
            print(f"    {name:<20} {cnt:>6,}  ({100*cnt/pass_box_raw:.1f}%){excl}")

    print(f"\nCarries: start outside box, end inside box           : {carry_box_total:,}")
    print(f"\nCombined box entries (pass+carry)                    : {pass_box_total + carry_box_total:,}")
    print()

    return {
        "box_entries_pass": pass_box_total,
        "box_entries_carry": carry_box_total,
        "player_pass_box": dict(player_pass_box),
        "player_carry_box": dict(player_carry_box),
        "player_names": dict(player_names),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Top-5 per 90
# ─────────────────────────────────────────────────────────────────────────────

def section_top5_per90(
    xga_data: dict,
    ft_data: dict,
    box_data: dict,
    competition_id: int,
    season_id: int,
) -> None:
    print("=" * 72)
    print("SECTION 4 — TOP-5 PER 90 (outfield, 900+ min)")
    print("=" * 72)

    seasons = _load_seasons(competition_id, season_id)
    outfield = seasons.filter(
        (pl.col("is_gk") == False) &
        (pl.col("player_season_minutes_total") >= MIN_MINUTES)
    )
    mins_map = dict(zip(
        outfield["player_id"].to_list(),
        outfield["player_season_minutes_total"].to_list()
    ))
    name_map = dict(zip(
        outfield["player_id"].to_list(),
        outfield["player_name"].to_list()
    ))

    # Merge player names from all sections
    all_names: dict[int, str] = {}
    all_names.update(xga_data["player_names"])
    all_names.update(ft_data["player_names"])
    all_names.update(box_data["player_names"])
    all_names.update(name_map)

    def top5(label: str, raw: dict[int, float | int]) -> None:
        rows = []
        for pid, val in raw.items():
            mins = mins_map.get(pid)
            if mins is None or mins < MIN_MINUTES:
                continue
            p90 = val / mins * 90
            rows.append((all_names.get(pid, "?"), val, mins, p90))
        rows.sort(key=lambda x: -x[3])
        print(f"\n  {label}")
        print(f"  {'Player':<28} {'Raw':>8} {'Min':>8} {'Per90':>8}")
        print("  " + "-" * 57)
        for name, raw_val, mins, p90 in rows[:5]:
            print(f"  {name:<28} {raw_val:>8.3f} {mins:>8.1f} {p90:>8.4f}")

    top5("xG Assisted", {k: float(v) for k, v in xga_data["player_xg_assisted"].items()})

    ft_combined: dict[int, int] = defaultdict(int)
    for pid, v in ft_data["player_pass_ft"].items():
        ft_combined[pid] += v
    for pid, v in ft_data["player_carry_ft"].items():
        ft_combined[pid] += v
    top5("Final-third entries (pass+carry)", {k: float(v) for k, v in ft_combined.items()})

    box_combined: dict[int, int] = defaultdict(int)
    for pid, v in box_data["player_pass_box"].items():
        box_combined[pid] += v
    for pid, v in box_data["player_carry_box"].items():
        box_combined[pid] += v
    top5("Box entries (pass+carry)", {k: float(v) for k, v in box_combined.items()})

    print()


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args(__doc__.splitlines()[1])
    matches = load_matches(args.competition_id, args.season_id)
    label = competition_label(matches)
    print(f"\nLoading all {label} event files ({len(matches)} matches)…")
    by_match = load_all_events_by_match(matches)
    n_matches = len(by_match)
    all_events = load_all_events(by_match)
    n_events = len(all_events)
    print(f"Loaded {n_matches} matches, {n_events:,} total events.\n")

    reconcile_goals(matches, all_events)
    xga_data = section_xg_assisted(by_match, args.competition_id, args.season_id)
    ft_data = section_final_third(all_events)
    box_data = section_box_entries(all_events)
    if _load_seasons(args.competition_id, args.season_id).is_empty():
        print("SECTION 4 skipped: no processed season rows for this competition-season.\n")
    else:
        section_top5_per90(xga_data, ft_data, box_data,
                           args.competition_id, args.season_id)

    chunk_b = {
        "xg_assisted": round(xga_data["xg_assisted"], 4),
        "xg_assisted_direct": round(xga_data["xg_assisted_direct"], 4),
        "final_third_entries_pass": ft_data["final_third_entries_pass"],
        "final_third_entries_carry": ft_data["final_third_entries_carry"],
        "box_entries_pass": box_data["box_entries_pass"],
        "box_entries_carry": box_data["box_entries_carry"],
    }
    print("REFERENCE TOTALS (chunk_b)")
    for k, v in chunk_b.items():
        print(f"  {k:<28} {v:>12,}")
    if not args.no_write:
        write_reference(args.competition_id, args.season_id, "chunk_b", chunk_b)

    print("=" * 72)
    print("END OF VERIFICATION REPORT")
    print("=" * 72)


if __name__ == "__main__":
    main()
