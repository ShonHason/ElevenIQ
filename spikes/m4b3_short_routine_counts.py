"""Spike: the ADR 004 §9 short-routine figures, measured from raw StatsBomb JSON.

A key pass (the pipeline's test: pass.shot_assist or pass.goal_assist, periods 1-4,
with a player) is a restart when its own pass.type.name is a restart (ADR 004 D14),
otherwise open play (op_). This spike counts how many op_ key passes lie in
possessions labelled From Corner / From Free Kick (short-corner and short-free-kick
routines, which the key-pass definition counts as open play), and also the passes
whose assisted_shot_id links a shot but which carry neither assist flag (the two
splits use different linkage; ADR 004 §9 pins the behaviour).

Fails (SystemExit) unless each league's key-pass total and restart count equal the
committed reference totals (tests/fixtures/reference_totals/*.json, core.key_passes /
core.restart_key_passes). Non-restart is total − restart by construction (each key
pass increments exactly one of the two), so it needs no separate check.

No eleveniq imports (tests/test_spikes_independent.py).

Usage: .venv/bin/python spikes/m4b3_short_routine_counts.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "statsbomb"
REF = ROOT / "tests" / "fixtures" / "reference_totals"
LEAGUES = {11: "La Liga", 2: "Premier League", 12: "Serie A", 7: "Ligue 1", 9: "Bundesliga"}
SEASON = 27
RESTART_TYPES = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off", "Throw-in"})  # ADR 004 D14
SHORT_ROUTINE_PATTERNS = ("From Corner", "From Free Kick")


def main() -> None:
    total = Counter()
    for comp, name in LEAGUES.items():
        c = Counter()
        for m in json.loads((RAW / "matches" / str(comp) / f"{SEASON}.json").read_text()):
            events = json.loads((RAW / "events" / f"{m['match_id']}.json").read_text())
            ids = {e["id"]: e for e in events}
            for e in events:
                if e["type"]["name"] != "Pass" or e["period"] > 4 or not e.get("player"):
                    continue
                p = e.get("pass") or {}
                flagged = bool(p.get("shot_assist") or p.get("goal_assist"))
                shot = ids.get(p.get("assisted_shot_id"))
                if (not flagged and shot is not None and shot["type"]["name"] == "Shot"):
                    c["linked_without_flag"] += 1
                if not flagged:
                    continue
                c["key_passes"] += 1
                if ((p.get("type") or {}).get("name")) in RESTART_TYPES:
                    c["restart"] += 1
                    continue
                c["non_restart"] += 1
                pattern = (e.get("play_pattern") or {}).get("name")
                if pattern in SHORT_ROUTINE_PATTERNS:
                    c[pattern] += 1
        ref = json.loads((REF / f"{comp}_{SEASON}.json").read_text())["core"]
        if (c["key_passes"], c["restart"]) != (ref["key_passes"], ref["restart_key_passes"]):
            raise SystemExit(f"FAIL {name}: ({c['key_passes']}, {c['restart']}) != reference "
                             f"({ref['key_passes']}, {ref['restart_key_passes']})")
        print(f"{name:<15} key passes {c['key_passes']:>6,}  restart {c['restart']:>5,}  "
              f"non-restart {c['non_restart']:>6,}  From Corner {c['From Corner']:>5,}  "
              f"From Free Kick {c['From Free Kick']:>5,}  linked without flag {c['linked_without_flag']}")
        total.update(c)

    routine = total["From Corner"] + total["From Free Kick"]
    print(f"\n5 leagues: key passes {total['key_passes']:,} = non-restart {total['non_restart']:,} "
          f"+ restart {total['restart']:,}")
    print(f"non-restart key passes in From Corner possessions {total['From Corner']:,}, "
          f"From Free Kick {total['From Free Kick']:,}: {routine:,} "
          f"({100 * routine / total['non_restart']:.1f}% of non-restart key passes)")
    print(f"passes with a linked shot but no assist flag: {total['linked_without_flag']}")


if __name__ == "__main__":
    main()
