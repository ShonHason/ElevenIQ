#!/usr/bin/env python3
"""
Spike: Chunk A data verification for one competition-season.

Reports on aerial duels, corners, pass types, long passes, ball recoveries,
and dribbled-past events to confirm metric definitions before implementation.

It also computes the reference totals (match count, starts, goals, own goals,
crosses and Chunk A totals) from raw JSON, reconciles goals + own goals
against the matches.json scores (a mismatch fails the spike), and writes the
"base" and "chunk_a" sections of
tests/fixtures/reference_totals/{competition_id}_{season_id}.json.

Run:
    .venv/bin/python spikes/verify_chunk_a_metrics.py --competition-id 11 --season-id 27

Never imports eleveniq.pipeline: the totals are an independent reference.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median, quantiles

from reference_totals import (
    DATA_ROOT,
    competition_label,
    load_matches,
    parse_args,
    reconcile_goals,
    write_reference,
)

# StatsBomb pitch: 120 yd × 80 yd.
# Penalty area (attacking end): x >= 102, 18 <= y <= 62
PENALTY_BOX_X_MIN = 102.0
PENALTY_BOX_Y_MIN = 18.0
PENALTY_BOX_Y_MAX = 62.0


def _in_penalty_area(loc: list[float]) -> bool:
    x, y = loc[0], loc[1]
    return x >= PENALTY_BOX_X_MIN and PENALTY_BOX_Y_MIN <= y <= PENALTY_BOX_Y_MAX


def load_all_events(matches: list[dict]) -> list[dict]:
    events: list[dict] = []
    for m in matches:
        mid = m["match_id"]
        path = DATA_ROOT / "events" / f"{mid}.json"
        with open(path) as f:
            events.extend(json.load(f))
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def section_aerial_duels(events: list[dict]) -> None:
    """
    StatsBomb encodes the winner of an aerial duel by placing aerial_won=true
    inside the sub-event payload of the WINNER's own event (Pass, Clearance,
    Shot, Miscontrol, …).  The loser gets a Duel event with duel.type.name ==
    "Aerial Lost".  There is no separate "Aerial Won" event type.

    We therefore count:
      - aerial_won wins: events where any sub-event dict has aerial_won=True
      - aerial_lost:     Duel events with duel.type.name == "Aerial Lost"
    """
    print("=" * 68)
    print("SECTION 1 — AERIAL DUELS")
    print("=" * 68)

    wins_by_type: Counter = Counter()
    aerial_lost_total = 0

    SUB_EVENT_KEYS = {
        "Pass": "pass",
        "Clearance": "clearance",
        "Shot": "shot",
        "Miscontrol": "miscontrol",
        "Ball Receipt*": "ball_receipt",
        "Dribble": "dribble",
        "Goal Keeper": "goalkeeper",
        "Block": "block",
        "Interception": "interception",
    }

    for e in events:
        etype = e["type"]["name"]

        # Count Aerial Lost duels
        if etype == "Duel":
            duel = e.get("duel", {})
            if duel.get("type", {}).get("name") == "Aerial Lost":
                aerial_lost_total += 1
            continue

        # Count aerial_won=True in sub-event payload
        key = SUB_EVENT_KEYS.get(etype)
        if key:
            sub = e.get(key, {})
            if isinstance(sub, dict) and sub.get("aerial_won") is True:
                wins_by_type[etype] += 1

    total_won = sum(wins_by_type.values())

    print(f"\naerialwon=True by event type:")
    for etype, n in sorted(wins_by_type.items(), key=lambda x: -x[1]):
        print(f"  {etype:<20} {n:>6,}")
    print(f"  {'TOTAL aerial_won':<20} {total_won:>6,}")

    print(f"\naerialLost (Duel.duel.type.name=='Aerial Lost'): {aerial_lost_total:,}")

    ratio = aerial_lost_total / total_won if total_won else float("inf")
    print(
        f"\nRatio aerial_lost / aerial_won = {ratio:.3f}  "
        f"(expected ≈ 1.0 if every duel has one winner and one loser)"
    )
    print(
        "\nInterpretation: aerial_won counts the WINNER's event. "
        "aerial_lost counts the LOSER's Duel event. "
        "A ratio near 1.0 validates one-winner-one-loser symmetry.\n"
    )


def section_corners(events: list[dict], n_matches: int) -> None:
    """
    Corners are passes with pass.type.name == "Corner".
    We report: total corners, how many have pass.cross==True, and how many
    end inside the attacking penalty area (x>=102, 18<=y<=62).
    """
    print("=" * 68)
    print("SECTION 2 — CORNERS")
    print("=" * 68)

    total = 0
    cross_true = 0
    cross_false = 0
    cross_none = 0
    ends_in_box = 0
    end_outside_box = 0
    end_missing = 0

    for e in events:
        if e["type"]["name"] != "Pass":
            continue
        p = e.get("pass", {})
        if p.get("type", {}).get("name") != "Corner":
            continue

        total += 1

        c = p.get("cross")
        if c is True:
            cross_true += 1
        elif c is False:
            cross_false += 1
        else:
            cross_none += 1

        end_loc = p.get("end_location")
        if end_loc is None:
            end_missing += 1
        elif _in_penalty_area(end_loc):
            ends_in_box += 1
        else:
            end_outside_box += 1

    print(f"\nTotal corners (pass.type.name == 'Corner'): {total:,}")
    print(f"  Per match: {total / n_matches:.1f}  (La Liga 2015/16 reference: ~9–10.5)")
    print(f"\npass.cross values:")
    print(f"  cross=True  : {cross_true:,}")
    print(f"  cross=False : {cross_false:,}")
    print(f"  cross=None  : {cross_none:,}  (field absent in payload)")
    print(f"\nEnd location relative to attacking penalty area (x>=102, 18<=y<=62):")
    print(f"  Ends inside box : {ends_in_box:,}  ({100*ends_in_box/total:.1f}%)")
    print(f"  Ends outside box: {end_outside_box:,}  ({100*end_outside_box/total:.1f}%)")
    if end_missing:
        print(f"  End loc missing : {end_missing:,}")
    print()


def section_pass_types(events: list[dict]) -> None:
    """
    List every pass.type.name value with its count.
    A None/absent type.name means an open-play pass.
    """
    print("=" * 68)
    print("SECTION 3 — PASS TYPES (pass.type.name distribution)")
    print("=" * 68)

    type_counter: Counter = Counter()
    total_passes = 0

    for e in events:
        if e["type"]["name"] != "Pass":
            continue
        total_passes += 1
        ptype = e.get("pass", {}).get("type", {})
        name = ptype.get("name") if isinstance(ptype, dict) else None
        type_counter[name or "(open play / no type)"] += 1

    print(f"\nTotal pass events: {total_passes:,}")
    print(f"\n{'pass.type.name':<35}  {'count':>8}  {'%':>6}")
    print("-" * 54)
    for name, count in sorted(type_counter.items(), key=lambda x: -x[1]):
        pct = 100 * count / total_passes
        print(f"  {name:<33}  {count:>8,}  {pct:>5.1f}%")
    print()


def section_long_passes(events: list[dict]) -> None:
    """
    Show the pass.length distribution (StatsBomb yards) and propose a
    long-pass threshold.
    """
    print("=" * 68)
    print("SECTION 4 — LONG PASSES (pass.length distribution)")
    print("=" * 68)

    lengths: list[float] = []
    for e in events:
        if e["type"]["name"] != "Pass":
            continue
        l = e.get("pass", {}).get("length")
        if l is not None:
            lengths.append(float(l))

    lengths.sort()
    n = len(lengths)
    qs = quantiles(lengths, n=100)  # percentiles p1..p99

    def pct(p: int) -> float:
        return qs[p - 1]

    print(f"\nTotal passes with length: {n:,}")
    print(f"\nDistribution (StatsBomb yards):")
    print(f"  Min     : {lengths[0]:.1f}")
    print(f"  p10     : {pct(10):.1f}")
    print(f"  p25     : {pct(25):.1f}")
    print(f"  Median  : {median(lengths):.1f}")
    print(f"  p75     : {pct(75):.1f}")
    print(f"  p85     : {pct(85):.1f}")
    print(f"  p90     : {pct(90):.1f}")
    print(f"  p95     : {pct(95):.1f}")
    print(f"  p99     : {pct(99):.1f}")
    print(f"  Max     : {lengths[-1]:.1f}")

    # Count by threshold candidates
    for threshold in [25, 30, 32]:
        n_long = sum(1 for l in lengths if l >= threshold)
        print(f"\n  Passes >= {threshold} yd: {n_long:,}  ({100*n_long/n:.1f}% of all passes)")

    print(
        "\nChosen threshold: 30 yards (≈ p78), per ADR 002 §19."
        "\nReason: conventional round number near the 78th percentile of open-play"
        "\n  pass lengths; the data-observed La Liga 2015/16 share (17.8% of all"
        "\n  passes) is the documented justification.  No external definition is cited."
        "\n  (32 yd and 25 yd were also evaluated; see ADR 002 §19 for the decision.)"
    )
    print()


def section_ball_recoveries(events: list[dict]) -> None:
    """
    Count total ball recovery events and those with recovery_failure=True.
    """
    print("=" * 68)
    print("SECTION 5 — BALL RECOVERIES")
    print("=" * 68)

    total = 0
    failure_true = 0
    failure_false = 0
    failure_none = 0

    for e in events:
        if e["type"]["name"] != "Ball Recovery":
            continue
        total += 1
        br = e.get("ball_recovery", {})
        rf = br.get("recovery_failure")
        if rf is True:
            failure_true += 1
        elif rf is False:
            failure_false += 1
        else:
            failure_none += 1

    print(f"\nTotal Ball Recovery events: {total:,}")
    print(f"\nrecovery_failure values:")
    print(f"  True  (recovery failed)  : {failure_true:,}  ({100*failure_true/total:.1f}%)")
    print(f"  False (recovery retained): {failure_false:,}  ({100*failure_false/total:.1f}%)")
    print(f"  None/absent              : {failure_none:,}  ({100*failure_none/total:.1f}%)")
    print(
        "\nNote: recovery_failure=True means the player attempted a recovery but"
        "\n  did not retain possession. Successes (False/absent) count toward"
        "\n  ball-recovery metrics; failures should be excluded or tracked separately."
    )
    print()


def section_dribbled_past(events: list[dict]) -> None:
    """
    Count Dribbled Past events and confirm they are attributed to the
    defender who was beaten (not the dribbler).
    """
    print("=" * 68)
    print("SECTION 6 — DRIBBLED PAST")
    print("=" * 68)

    total = 0
    positions: Counter = Counter()
    sample_players: list[str] = []

    # collect related dribble events to confirm attribution
    dribble_event_ids: set[str] = set()
    dribbled_past_ids: dict[str, dict] = {}   # event_id -> event

    for e in events:
        if e["type"]["name"] == "Dribble":
            dribble_event_ids.add(e["id"])
        if e["type"]["name"] == "Dribbled Past":
            total += 1
            pos = e.get("position", {}).get("name", "Unknown")
            positions[pos] += 1
            dribbled_past_ids[e["id"]] = e
            if len(sample_players) < 5:
                sample_players.append(
                    f"{e.get('player', {}).get('name','?')} "
                    f"({pos}) — related: {e.get('related_events', [])}"
                )

    print(f"\nTotal 'Dribbled Past' events: {total:,}")
    print(f"\nTop 10 positions attributed with 'Dribbled Past':")
    for pos, cnt in positions.most_common(10):
        print(f"  {pos:<30} {cnt:>6,}")

    print(f"\nSample events (first 5):")
    for s in sample_players:
        print(f"  {s}")

    print(
        "\nConfirmation: 'Dribbled Past' events carry the team, player, and"
        "\n  position of the DEFENDER who was beaten — not the dribbler."
        "\n  The dribbler's Dribble event (with outcome.name=='Complete') is"
        "\n  linked via related_events. Check the sample above for this competition."
    )
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Reference totals (Plan 003 §3) — definitions per ADR 002, independent of the
# pipeline code. Counted over events that carry a player.
# ─────────────────────────────────────────────────────────────────────────────

LONG_PASS_THRESHOLD = 30.0
# Set-piece pass types excluded from long passes (ADR 002 §19).
SET_PIECE_TYPES = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off"})
# Sub-payloads that carry aerial_won on the winner's event (ADR 002 §20).
AERIAL_WON_KEYS = {"Pass": "pass", "Clearance": "clearance", "Shot": "shot",
                   "Miscontrol": "miscontrol"}
SIMPLE_COUNTS = {"Dispossessed": "dispossessed", "Miscontrol": "miscontrols",
                 "Pressure": "pressures", "Dribbled Past": "dribbled_past"}


def section_reference_totals(matches: list[dict], events: list[dict]) -> tuple[dict, dict]:
    """Return (base, chunk_a) totals for the reference file."""
    print("=" * 68)
    print("REFERENCE TOTALS")
    print("=" * 68)

    goals, own_goals = reconcile_goals(matches, events)
    starts = sum(
        len(e["tactics"]["lineup"]) for e in events if e["type"]["name"] == "Starting XI"
    )

    a = {k: 0 for k in (
        "dispossessed", "miscontrols", "pressures", "ball_recoveries",
        "ball_recoveries_failed", "dribbled_past", "aerials_won", "aerials_lost",
        "long_passes_attempted", "long_passes_completed", "passes_missing_length",
        "switches",
    )}
    crosses_attempted = crosses_completed = 0

    for e in events:
        if not e.get("player"):
            continue
        etype = e["type"]["name"]
        if etype in SIMPLE_COUNTS:
            a[SIMPLE_COUNTS[etype]] += 1
        key = AERIAL_WON_KEYS.get(etype)
        if key and (e.get(key) or {}).get("aerial_won"):
            a["aerials_won"] += 1
        if etype == "Duel" and ((e.get("duel") or {}).get("type") or {}).get("name") == "Aerial Lost":
            a["aerials_lost"] += 1
        elif etype == "Ball Recovery":
            if (e.get("ball_recovery") or {}).get("recovery_failure"):
                a["ball_recoveries_failed"] += 1
            else:
                a["ball_recoveries"] += 1
        elif etype == "Pass":
            pd = e.get("pass") or {}
            completed = pd.get("outcome") is None
            if pd.get("cross"):
                crosses_attempted += 1
                crosses_completed += completed
            if pd.get("switch"):
                a["switches"] += 1
            length = pd.get("length")
            if length is None:
                a["passes_missing_length"] += 1
            elif length >= LONG_PASS_THRESHOLD and (pd.get("type") or {}).get("name") not in SET_PIECE_TYPES:
                a["long_passes_attempted"] += 1
                a["long_passes_completed"] += completed

    base = {
        "matches": len(matches),
        "starts": starts,
        "goals": goals,
        "own_goals": own_goals,
        "crosses_attempted": crosses_attempted,
        "crosses_completed": crosses_completed,
    }
    for label, section in (("base", base), ("chunk_a", a)):
        for k, v in section.items():
            print(f"  {label:<8} {k:<24} {v:>10,}")
    print()
    return base, a


def main() -> None:
    args = parse_args(__doc__.splitlines()[1])
    matches = load_matches(args.competition_id, args.season_id)
    label = competition_label(matches)
    print(f"\nLoading all {label} event files ({len(matches)} matches)…")
    events = load_all_events(matches)
    print(f"Loaded {len(events):,} total events.\n")

    section_aerial_duels(events)
    section_corners(events, len(matches))
    section_pass_types(events)
    section_long_passes(events)
    section_ball_recoveries(events)
    section_dribbled_past(events)
    base, chunk_a = section_reference_totals(matches, events)

    if not args.no_write:
        write_reference(args.competition_id, args.season_id, "base", base)
        write_reference(args.competition_id, args.season_id, "chunk_a", chunk_a)

    print("=" * 68)
    print("END OF VERIFICATION REPORT")
    print("=" * 68)


if __name__ == "__main__":
    main()
