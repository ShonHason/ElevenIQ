#!/usr/bin/env python3
"""
Spike: core DNA metric reference totals for one competition-season (plan 004 H2).

Computes league totals of the core columns from raw StatsBomb JSON and writes
them to the "core" section of
tests/fixtures/reference_totals/{competition_id}_{season_id}.json. Existing
sections are left untouched. Goals + own goals must reconcile with the
matches.json scores, and minutes must pass a structural check (below);
otherwise the spike fails and writes nothing. It also adds a "dismissals"
count to the "base" section (plan 004 H3 oracle): dismissal cards shown to
players who took the field (non-empty lineup positions), i.e. the dismissed
players that have a row in player_match_stats (ADR 002 §3).

Run:
    .venv/bin/python spikes/verify_core_metrics.py --competition-id 11 --season-id 27

Independence
------------
Never imports eleveniq.pipeline or eleveniq.metrics. Each definition is
re-implemented from the ADR 002 text; only threshold constants may come from
eleveniq.config (none are needed today: the progressive thresholds live in the
ADR text, §1-§2). Each column is one of two kinds:

- INDEPENDENT: a raw event count with no rule beyond the event type (or one
  StatsBomb flag). The only shared assumption is "every event with a player
  counts". Any silent drop in the pipeline shows up as a mismatch.
- SECOND IMPLEMENTATION: the same written definition coded again (ADR 002
  §1, §2, §3, §13, §30). Minutes follow §3 as amended 2026-09-29: every stint
  end is capped at the earlier of the player's dismissal and his own
  Substitution event. It catches coding errors and silent drops, not a
  wrong definition.

Scope rule shared by every column: period-5 (shootout) events are excluded
(ADR 002 §3). Events are counted league-wide, not per output row: a dropped
event (other than an allow-listed bench card, which is not a counted type)
would make the pipeline total fall short.

Minutes structural check (not a second implementation)
------------------------------------------------------
Per team-match, 11 players are on the pitch at any time except after a
dismissal. So, per team-match:
    summed minutes <= 11 * match_duration + PER_TEAM_TOLERANCE_MIN
and, per league:
    summed minutes ~= sum(11 * duration - dismissal losses)
where a dismissal loss is (duration - card minute) for an on-pitch player.
This oracle uses only event timestamps and cards, never lineup stints, so it
cannot share a bug with the stint arithmetic. It cannot see a player who left
injured with no substitutes left, or small timestamp gaps between a sub-off
and a sub-on, so the league figure has a tolerance (LEAGUE_TOLERANCE_SHARE).
"""
from __future__ import annotations

import json
import math
from collections import Counter

from reference_totals import (
    DATA_ROOT,
    REFERENCE_DIR,
    competition_label,
    load_matches,
    parse_args,
    reconcile_goals,
    write_reference,
)

# ADR 002 §1 (progressive pass) and §2 (progressive carry), StatsBomb units.
DEFENSIVE_40_X = 48.0
PROG_PASS_UNITS = 10.0
PROG_CARRY_UNITS = 5.0
PROG_CARRY_MIN_LENGTH = 5.0
GOAL_CENTRE = (120.0, 40.0)
BOX_X_MIN, BOX_Y_MIN, BOX_Y_MAX = 102.0, 18.0, 62.0

# ADR 002 §30: exact StatsBomb casing ("Saved to Post", lower-case "to").
ON_TARGET = {"Goal", "Saved", "Saved to Post"}
# tackles.py documented outcome mapping (the ADR does not list it).
TACKLE_WON = {"Won", "Success In Play", "Success Out"}
# ADR 002 §3: dismissal cards, from Bad Behaviour or Foul Committed.
DISMISSAL_CARDS = {"Red Card", "Second Yellow"}

PER_TEAM_TOLERANCE_MIN = 1e-6  # float noise only
LEAGUE_TOLERANCE_SHARE = 0.001  # 0.1% of the league's minutes

# Column → kind, reported with the totals.
KINDS = {
    "minutes_played": "second implementation (ADR 002 §3) + structural check",
    "shots": "independent", "shots_on_target": "second implementation (§30)",
    "xg": "independent", "passes_attempted": "independent",
    "passes_completed": "independent (no pass.outcome)",
    "progressive_passes": "second implementation (§1)",
    "progressive_carries": "second implementation (§2)",
    "key_passes": "independent (StatsBomb flags, §8)",
    "assists": "independent (StatsBomb flag, §8)",
    "tackles_attempted": "independent", "tackles_won": "second implementation (outcome list)",
    "dribbles_attempted": "independent", "dribbles_completed": "independent (outcome Complete)",
    "interceptions": "independent", "clearances": "independent",
    "blocks": "second implementation (§13)",
    "fouls_committed": "independent", "fouls_won": "independent", "carries": "independent",
}


def _mmss(s: str) -> float:
    """'MM:SS', 'MM:SS.mmm' or 'H:MM:SS' → minutes (ADR 002 §3)."""
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + parts[2] / 60
    return parts[0] + parts[1] / 60


def _event_minute(e: dict) -> float:
    return e["minute"] + e["second"] / 60


def _dist_to_goal(p: list[float]) -> float:
    return math.hypot(GOAL_CENTRE[0] - p[0], GOAL_CENTRE[1] - p[1])


def _in_box(p: list[float]) -> bool:
    return p[0] >= BOX_X_MIN and BOX_Y_MIN <= p[1] <= BOX_Y_MAX


def _progressive_pass(start: list[float], end: list[float]) -> bool:
    """ADR 002 §1, for a completed pass."""
    if start[0] < DEFENSIVE_40_X:
        return False
    closer = _dist_to_goal(start) - _dist_to_goal(end)
    return closer >= PROG_PASS_UNITS or (not _in_box(start) and _in_box(end))


def _progressive_carry(start: list[float], end: list[float]) -> bool:
    """ADR 002 §2."""
    closer = _dist_to_goal(start) - _dist_to_goal(end)
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    return closer >= PROG_CARRY_UNITS and length >= PROG_CARRY_MIN_LENGTH


def _card(e: dict) -> str | None:
    for key in ("bad_behaviour", "foul_committed"):
        name = ((e.get(key) or {}).get("card") or {}).get("name")
        if name:
            return name
    return None


def count_events(events: list[dict], t: Counter) -> None:
    """Add one match's period 1-4 event counts to `t`."""
    for e in events:
        if e["period"] == 5 or not e.get("player"):
            continue
        kind = e["type"]["name"]
        if kind == "Pass":
            p = e.get("pass") or {}
            t["passes_attempted"] += 1
            completed = (p.get("outcome") or {}).get("name") is None
            if completed:
                t["passes_completed"] += 1
                if _progressive_pass(e["location"], p["end_location"]):
                    t["progressive_passes"] += 1
            if p.get("shot_assist") or p.get("goal_assist"):
                t["key_passes"] += 1
            if p.get("goal_assist"):
                t["assists"] += 1
        elif kind == "Shot":
            s = e.get("shot") or {}
            t["shots"] += 1
            if (s.get("outcome") or {}).get("name") in ON_TARGET:
                t["shots_on_target"] += 1
            t["xg"] += s["statsbomb_xg"]  # a missing xG raises: H1.4 says 0 occur
        elif kind == "Carry":
            t["carries"] += 1
            if _progressive_carry(e["location"], e["carry"]["end_location"]):
                t["progressive_carries"] += 1
        elif kind == "Duel":
            d = e.get("duel") or {}
            if (d.get("type") or {}).get("name") == "Tackle":
                t["tackles_attempted"] += 1
                if (d.get("outcome") or {}).get("name") in TACKLE_WON:
                    t["tackles_won"] += 1
        elif kind == "Dribble":
            t["dribbles_attempted"] += 1
            if ((e.get("dribble") or {}).get("outcome") or {}).get("name") == "Complete":
                t["dribbles_completed"] += 1
        elif kind == "Block":
            b = e.get("block") or {}
            if not b.get("offensive") and not b.get("save_block"):
                t["blocks"] += 1
        elif kind in ("Interception", "Clearance", "Foul Committed", "Foul Won"):
            t[{"Interception": "interceptions", "Clearance": "clearances",
               "Foul Committed": "fouls_committed", "Foul Won": "fouls_won"}[kind]] += 1


def match_minutes(events: list[dict], lineups: list[dict]) -> tuple[float, list[dict]]:
    """
    ADR 002 §3, re-implemented. Returns (duration, one dict per team with
    summed player minutes, the structural expectation and dismissals).
    """
    play = [e for e in events if e["period"] <= 4]
    duration = max(_event_minute(e) for e in play)

    dismissal: dict[int, float] = {}
    sub_off: dict[int, float] = {}
    for e in sorted(play, key=lambda e: (e["index"])):
        if _card(e) in DISMISSAL_CARDS and e.get("player"):
            dismissal.setdefault(e["player"]["id"], _event_minute(e))
        if e["type"]["name"] == "Substitution":
            sub_off.setdefault(e["player"]["id"], _event_minute(e))

    teams = []
    for team in lineups:
        summed = 0.0
        losses = 0.0
        n_dismissed = 0
        for pl in team["lineup"]:
            card = dismissal.get(pl["player_id"])
            exits = [m for m in (card, sub_off.get(pl["player_id"])) if m is not None]
            cap = min(exits) if exits else None
            minutes = 0.0
            for pos in pl["positions"]:
                start = _mmss(pos["from"])
                end = _mmss(pos["to"]) if pos.get("to") else (cap if cap is not None else duration)
                if cap is not None:
                    end = min(end, cap)
                minutes += max(0.0, end - start)
            summed += minutes
            if card is not None and pl["positions"]:
                n_dismissed += 1
                # A card after the player's own substitution leaves 11 on the pitch.
                sub = sub_off.get(pl["player_id"])
                if sub is None or card < sub:
                    losses += max(0.0, duration - card)
        teams.append({
            "team_id": team["team_id"], "summed": summed,
            "ceiling": 11 * duration, "expected": 11 * duration - losses,
            "dismissed": n_dismissed,
        })
    return duration, teams


def main() -> None:
    args = parse_args(__doc__.splitlines()[1])
    matches = load_matches(args.competition_id, args.season_id)
    label = competition_label(matches)
    print(f"{label}: {len(matches)} matches")

    totals: Counter = Counter()
    all_events: list[dict] = []
    team_rows: list[dict] = []
    for m in matches:
        mid = m["match_id"]
        events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
        lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
        count_events(events, totals)
        _, teams = match_minutes(events, lineups)
        for t in teams:
            t["match_id"] = mid
            totals["minutes_played"] += t["summed"]
        team_rows.extend(teams)
        all_events.extend(e for e in events if e["period"] != 5)

    reconcile_goals(matches, all_events)

    # Structural minutes check.
    over = [t for t in team_rows if t["summed"] > t["ceiling"] + PER_TEAM_TOLERANCE_MIN]
    expected = sum(t["expected"] for t in team_rows)
    gap = expected - totals["minutes_played"]
    print(
        f"\nMinutes: summed {totals['minutes_played']:,.3f}; "
        f"Σ(11×duration) {sum(t['ceiling'] for t in team_rows):,.3f}; "
        f"minus dismissal losses ({sum(t['dismissed'] for t in team_rows)} on-pitch cards) "
        f"= {expected:,.3f}; gap {gap:,.3f} ({gap / expected:.4%})"
    )
    worst = sorted(team_rows, key=lambda t: t["summed"] - t["expected"])
    for t in worst[:3] + worst[-3:]:
        print(f"  match {t['match_id']} team {t['team_id']}: summed {t['summed']:.2f} "
              f"expected {t['expected']:.2f} (Δ {t['summed'] - t['expected']:+.2f})")
    if over:
        raise SystemExit(f"FAIL: {len(over)} team-matches exceed 11 × duration: "
                         f"{[(t['match_id'], t['team_id']) for t in over[:10]]}")
    if abs(gap) > LEAGUE_TOLERANCE_SHARE * expected:
        raise SystemExit(f"FAIL: league minutes gap {gap:.3f} exceeds "
                         f"{LEAGUE_TOLERANCE_SHARE:.1%} of {expected:.3f}")

    core = {col: (round(totals[col], 4) if col in ("xg", "minutes_played") else totals[col])
            for col in KINDS}
    print()
    for col, value in core.items():
        print(f"  {col:<22} {value:>14,}   {KINDS[col]}")
    dismissals = sum(t["dismissed"] for t in team_rows)
    print(f"  {'dismissals (base)':<22} {dismissals:>14,}   independent (on-pitch cards)")
    if not args.no_write:
        write_reference(args.competition_id, args.season_id, "core", core)
        ref = json.loads((REFERENCE_DIR / f"{args.competition_id}_{args.season_id}.json").read_text())
        write_reference(args.competition_id, args.season_id, "base",
                        {**ref["base"], "dismissals": dismissals})


if __name__ == "__main__":
    main()
