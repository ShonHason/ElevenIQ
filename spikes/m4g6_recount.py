"""
M4g6a1 per-player recount from raw events, for the outlier trace (plan 005 M4g6a spec, item 4;
stats-reviewer condition 5). Never imports eleveniq (tests/test_spikes_independent.py).

Counts one player's events of one match (periods 1-4) into the season count columns of the
DNA metrics, with the independent definitions written out in the reference spikes:
- spikes/verify_core_metrics.py `count_events` (passes, completions, Injury Clearance,
  open-play passes, progressive passes and carries, key passes by type, shots, np shots / xG /
  on target, tackles, dribbles, blocks, interceptions, clearances, fouls);
- spikes/verify_chunk_a_metrics.py `section_reference_totals` (pressures, dribbled past, ball
  recoveries without recovery_failure, aerials won on the winner's Pass / Clearance / Shot /
  Miscontrol, aerials lost as Duel "Aerial Lost", long passes: length ≥ 30, not a restart, with
  their completed / Unknown / Injury Clearance subsets);
- spikes/verify_chunk_b_metrics.py `section_final_third` / `section_box_entries` (completed
  open-play passes and carries from outside to inside the final third, x ≥ 80, or the box,
  x ≥ 102 and 18 ≤ y ≤ 62).
Written out here (no reference spike defines them per player): `passes_unknown_outcome` (a pass
with outcome "Unknown") and `op_xg_assisted` (ADR 004 §9 / D16: Σ statsbomb_xg of the shots
whose key_pass_id is an open-play pass, i.e. not a restart type, by the player).

Not recounted (justified in the review): `team_open_play_passes_while_on` (credit-based; its
league total is verified by verify_core_metrics) and minutes (verified at league level by the same
spike).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_chunk_a_metrics as va  # noqa: E402  (AERIAL_WON_KEYS, SIMPLE_COUNTS, LONG_PASS_THRESHOLD)
import verify_core_metrics as vc  # noqa: E402  (count_events, RESTART_TYPES)

FINAL_THIRD_X = 80.0                   # verify_chunk_b_metrics.FINAL_THIRD_X
BOX_X_MIN, BOX_Y_MIN, BOX_Y_MAX = 102.0, 18.0, 62.0
NOT_RECOUNTED = frozenset({"team_open_play_passes_while_on"})


def _in_box(loc: list[float]) -> bool:
    return loc[0] >= BOX_X_MIN and BOX_Y_MIN <= loc[1] <= BOX_Y_MAX


def _entries(start: list[float], end: list[float]) -> tuple[bool, bool]:
    """(final-third entry, box entry) for a move from start to end."""
    return (start[0] < FINAL_THIRD_X <= end[0], not _in_box(start) and _in_box(end))


def player_counts(events: list[dict], player_id: int) -> Counter:
    """One match's season-count columns for `player_id` (periods 1-4)."""
    play = [e for e in events if e["period"] <= 4]
    mine = [e for e in play if (e.get("player") or {}).get("id") == player_id]
    t: Counter = Counter()
    vc.count_events(mine, t)
    for e in mine:
        kind = e["type"]["name"]
        if kind in va.SIMPLE_COUNTS:
            t[va.SIMPLE_COUNTS[kind]] += 1
        key = va.AERIAL_WON_KEYS.get(kind)
        if key and (e.get(key) or {}).get("aerial_won"):
            t["aerials_won"] += 1
        if kind == "Duel" and ((e.get("duel") or {}).get("type") or {}).get("name") == "Aerial Lost":
            t["aerials_lost"] += 1
        elif kind == "Ball Recovery":
            if not (e.get("ball_recovery") or {}).get("recovery_failure"):
                t["ball_recoveries"] += 1
        elif kind == "Pass":
            p = e.get("pass") or {}
            outcome = (p.get("outcome") or {}).get("name")
            restart = (p.get("type") or {}).get("name") in vc.RESTART_TYPES
            t["passes_unknown_outcome"] += outcome == "Unknown"
            length = p.get("length")
            if length is not None and length >= va.LONG_PASS_THRESHOLD and not restart:
                t["long_passes_attempted"] += 1
                t["long_passes_completed"] += outcome is None
                t["long_passes_unknown_outcome"] += outcome == "Unknown"
                t["long_passes_injury_clearance"] += outcome == "Injury Clearance"
            if outcome is None and not restart and e.get("location") and p.get("end_location"):
                ft, box = _entries(e["location"], p["end_location"])
                t["final_third_entries_pass"] += ft
                t["box_entries_pass"] += box
        elif kind == "Carry":
            end = (e.get("carry") or {}).get("end_location")
            if e.get("location") and end:
                ft, box = _entries(e["location"], end)
                t["final_third_entries_carry"] += ft
                t["box_entries_carry"] += box
    passes = {e["id"]: e for e in play if e["type"]["name"] == "Pass"}
    for e in play:
        if e["type"]["name"] != "Shot":
            continue
        kp = passes.get((e.get("shot") or {}).get("key_pass_id"))
        if kp is None or (kp.get("player") or {}).get("id") != player_id:
            continue
        if ((kp.get("pass") or {}).get("type") or {}).get("name") not in vc.RESTART_TYPES:
            t["op_xg_assisted"] += e["shot"]["statsbomb_xg"]
    return t


def duplicate_events(events: list[dict], player_id: int) -> int:
    """Events of the player sharing (period, timestamp, type) with an earlier one: a raw-data
    anomaly scan (stats-reviewer condition 6). Duplicated ids are counted too."""
    seen: set[tuple] = set()
    ids: set[str] = set()
    dup = 0
    for e in events:
        if (e.get("player") or {}).get("id") != player_id or e["period"] > 4:
            continue
        k = (e["period"], e.get("timestamp"), e["type"]["name"])
        if k in seen or e.get("id") in ids:
            dup += 1
        seen.add(k)
        ids.add(e.get("id"))
    return dup
