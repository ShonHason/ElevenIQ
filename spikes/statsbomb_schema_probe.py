#!/usr/bin/env python3
"""
Spike: probe StatsBomb Open Data schema for ElevenIQ milestone 1 research.
Fetches competitions list, a sample lineup file, and a sample events file.
Outputs plain-text findings to stdout. No files written beyond this script.
"""

import json
import urllib.request
from collections import Counter

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

# ── helpers ──────────────────────────────────────────────────────────────────

def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


# ── 1. Competitions ───────────────────────────────────────────────────────────

def section_competitions():
    data = fetch_json(f"{BASE}/competitions.json")

    DOMESTIC_TOP_FLIGHT = {11, 2, 7, 9, 12}

    print("=" * 70)
    print("SECTION 1 — COMPETITIONS (domestic top-flight male leagues)")
    print("=" * 70)
    print(f"{'comp_id':>8}  {'competition_name':<22}  {'season_id':>9}  "
          f"{'season_name':<12}  {'match_available':<15}  {'match_avail_360'}")

    rows = [
        c for c in data
        if c["competition_id"] in DOMESTIC_TOP_FLIGHT
        and not c.get("competition_international", False)
        and not c.get("competition_youth", False)
        and c.get("competition_gender") == "male"
    ]
    rows.sort(key=lambda c: (c["competition_id"], c["season_id"]))

    for c in rows:
        avail     = "YES" if c.get("match_available")     else "NO"
        avail_360 = "YES" if c.get("match_available_360") else "NO"
        print(f"{c['competition_id']:>8}  {c['competition_name']:<22}  "
              f"{c['season_id']:>9}  {c['season_name']:<12}  "
              f"{avail:<15}  {avail_360}")

    print(f"\nTotal rows shown: {len(rows)}")
    print("Note: match_available = timestamp present in competitions.json;")
    print("      >=10-match filter NOT applied here (requires per-season match files).")


# ── 2. Lineup file – inspect raw position structure first ─────────────────────

def section_lineup():
    data = fetch_json(f"{BASE}/lineups/3788741.json")

    print("\n" + "=" * 70)
    print("SECTION 2 — LINEUP 3788741 (Italy vs Turkey, UEFA Euro 2020)")
    print("=" * 70)

    # First, dump the raw positions array from the first player to understand schema
    first_player = data[0]["lineup"][0]
    print("\n-- RAW positions array for first player (schema inspection) --")
    print(f"player_id  : {first_player['player_id']}")
    print(f"player_name: {first_player['player_name']}")
    print("positions  :", json.dumps(first_player.get("positions", []), indent=2))

    print("\n-- Showing 4 players (mix of starters + substituted) --\n")

    shown = 0
    # Collect all players from both teams
    all_players = []
    for team in data:
        for p in team["lineup"]:
            all_players.append(p)

    # First pass: find players with >1 position entry (substitutes)
    multi_pos = [p for p in all_players if len(p.get("positions", [])) > 1]
    single_pos = [p for p in all_players if len(p.get("positions", [])) == 1]

    # Show 3 starters + 1 substituted player
    targets = single_pos[:3] + (multi_pos[:1] if multi_pos else [])

    for player in targets:
        print(f"player_id   : {player['player_id']}")
        print(f"player_name : {player['player_name']}")
        print(f"positions   :")
        for pos in player.get("positions", []):
            # pos may be a dict with 'position' as a dict or string
            position_field = pos.get("position", {})
            if isinstance(position_field, dict):
                pos_id   = position_field.get("id", "?")
                pos_name = position_field.get("name", "?")
            else:
                pos_id   = "?"
                pos_name = str(position_field)
            print(f"  position.id   = {pos_id}  position.name = {pos_name}")
            print(f"  from          = {pos.get('from')}  to = {pos.get('to')}")
            print(f"  from_period   = {pos.get('from_period')}  to_period = {pos.get('to_period')}")
            print(f"  start_reason  = {pos.get('start_reason')}")
            print(f"  end_reason    = {pos.get('end_reason')}")
        print()


# ── 3. Events – Duel ─────────────────────────────────────────────────────────

def section_duels():
    data = fetch_json(f"{BASE}/events/3788741.json")

    print("=" * 70)
    print("SECTION 3 — DUEL EVENTS (match 3788741)")
    print("=" * 70)

    # First, print raw duel object from first duel event for schema inspection
    first_duel_ev = next((ev for ev in data if ev.get("type", {}).get("name") == "Duel"), None)
    if first_duel_ev:
        print("\n-- RAW first Duel event (schema inspection) --")
        print(json.dumps({k: v for k, v in first_duel_ev.items()
                          if k in ("type", "player", "minute", "second", "duel")}, indent=2))

    seen_combos = {}
    type_names    = set()
    outcome_names = set()

    for ev in data:
        if ev.get("type", {}).get("name") != "Duel":
            continue
        duel = ev.get("duel", {})
        dtype   = (duel.get("type") or {}).get("name", "MISSING") if isinstance(duel.get("type"), dict) else str(duel.get("type", "MISSING"))
        outcome_obj = duel.get("outcome")
        if isinstance(outcome_obj, dict):
            outcome = outcome_obj.get("name", "MISSING")
        elif outcome_obj is None:
            outcome = "NULL"
        else:
            outcome = str(outcome_obj)
        type_names.add(dtype)
        outcome_names.add(outcome)
        key = (dtype, outcome)
        if key not in seen_combos:
            seen_combos[key] = {
                "duel_type"   : dtype,
                "duel_outcome": outcome,
                "player"      : ev.get("player", {}).get("name", "?"),
                "minute"      : ev.get("minute"),
                "duel_keys"   : list(duel.keys()),
            }

    print("\n\nAll unique (duel.type.name, duel.outcome.name) combinations:\n")
    for (dt, do), ex in sorted(seen_combos.items()):
        print(f"  duel.type={dt!r:<40}  duel.outcome={do!r}")
        print(f"    example: player={ex['player']}  minute={ex['minute']}")
        print(f"    duel keys: {ex['duel_keys']}")
        print()

    print("Distinct duel.type.name   :", sorted(type_names))
    print("Distinct duel.outcome.name:", sorted(outcome_names))


# ── 4. Shot outcomes ──────────────────────────────────────────────────────────

def section_shots():
    data = fetch_json(f"{BASE}/events/3788741.json")

    print("\n" + "=" * 70)
    print("SECTION 4 — SHOT OUTCOME VALUES (match 3788741)")
    print("=" * 70)

    # Print raw first Shot event for schema inspection
    first_shot = next((ev for ev in data if ev.get("type", {}).get("name") == "Shot"), None)
    if first_shot:
        print("\n-- RAW first Shot event (schema inspection, shot sub-object only) --")
        print(json.dumps(first_shot.get("shot", {}), indent=2))

    counter = Counter()
    for ev in data:
        if ev.get("type", {}).get("name") != "Shot":
            continue
        outcome_obj = ev.get("shot", {}).get("outcome")
        if isinstance(outcome_obj, dict):
            outcome = outcome_obj.get("name", "MISSING")
        elif outcome_obj is None:
            outcome = "NULL/MISSING"
        else:
            outcome = str(outcome_obj)
        counter[outcome] += 1

    print(f"\n{'shot.outcome.name':<30}  count")
    for outcome, cnt in sorted(counter.items(), key=lambda x: -x[1]):
        print(f"  {outcome:<28}  {cnt}")
    print(f"  Total shots: {sum(counter.values())}")


# ── 5. Pass outcomes ─────────────────────────────────────────────────────────

def section_passes():
    data = fetch_json(f"{BASE}/events/3788741.json")

    print("\n" + "=" * 70)
    print("SECTION 5 — PASS OUTCOME VALUES (match 3788741)")
    print("=" * 70)
    print("(null/absent outcome = completed pass)")

    counter = Counter()
    for ev in data:
        if ev.get("type", {}).get("name") != "Pass":
            continue
        pass_obj = ev.get("pass", {})
        outcome_obj = pass_obj.get("outcome") if pass_obj else None
        if isinstance(outcome_obj, dict):
            label = outcome_obj.get("name", "MISSING")
        elif outcome_obj is None:
            label = "NULL (completed)"
        else:
            label = str(outcome_obj)
        counter[label] += 1

    print(f"\n{'pass.outcome.name':<30}  count")
    for outcome, cnt in sorted(counter.items(), key=lambda x: -x[1]):
        print(f"  {outcome:<28}  {cnt}")
    print(f"  Total passes: {sum(counter.values())}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    section_competitions()
    section_lineup()
    section_duels()
    section_shots()
    section_passes()
