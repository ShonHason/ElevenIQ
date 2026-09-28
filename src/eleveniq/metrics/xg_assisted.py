"""
xG-assisted metric computation.

xg_assisted:
    For each player, the sum of shot.statsbomb_xg for all shots whose
    pass.assisted_shot_id resolves to a pass by that player in the same match.
    Equivalent to traversing from the shot side: sum of shot.xg for all shots
    whose shot.key_pass_id is a pass by this player.

xg_assisted_direct:
    Same as xg_assisted, but a shot is excluded if the shooter had:
      - any Dribble event, OR
      - cumulative Carry distance > max_carry_units
    between their LAST "Ball Receipt*" and the Shot, within the same possession
    (same possession_team).

    No "Ball Receipt*" found → the shot counts as direct (first touch was the shot).

See ADR 002 §25 and §26 for the full definition and La Liga 2015/16 reference figures.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

logger = logging.getLogger(__name__)


def _seg_dist(loc: list[float], end: list[float]) -> float:
    return math.hypot(end[0] - loc[0], end[1] - loc[1])


def compute_xg_assisted_per_player(
    events: list[dict],
    *,
    max_carry_units: float,
) -> dict[int, tuple[float, float]]:
    """
    Return {player_id: (xg_assisted, xg_assisted_direct)} for all passers.

    Args:
        events:          All events for a single match, in any order.
        max_carry_units: Cumulative carry-distance threshold for the "direct"
                         classification. Carry sum > this value → not direct.
                         Any Dribble → not direct regardless of carry distance.

    Data-quality logging (all at INFO or WARNING level):
        - Assisted shots with missing statsbomb_xg: WARNING, excluded from total.
        - Assisted shots with missing shooter id or possession: WARNING, credited
          as direct (conservative; no window to check).
        - Assisted shots with no "Ball Receipt*" before the shot: INFO, counted
          as direct. Expected to be rare: 6 in La Liga 2015/16 and 0 in the
          other 2015/16 leagues, so a large count points at a linkage problem.

    Raises:
        KeyError: if any event is missing "id" (used to build id_to_event lookup),
                  "type" or "type.name" (used to classify event kind), "index"
                  (used to sort per-player possession buckets), or — for Carry events
                  in the receipt-to-shot window — "location" or "carry.end_location".
                  StatsBomb events always carry these fields; absence indicates
                  malformed input data. This is intentional: callers should not pass
                  truncated or partially constructed event dicts.
    """
    id_to_event: dict[str, dict] = {e["id"]: e for e in events}

    # Group shooter's events by (possession, player_id), sorted by index.
    # Uses e["index"] directly — raises KeyError on malformed (index-less) events.
    poss_player_events: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for e in events:
        poss = e.get("possession")
        pid = e.get("player", {}).get("id")
        if poss is not None and pid is not None:
            poss_player_events[(poss, pid)].append(e)
    for key in poss_player_events:
        poss_player_events[key].sort(key=lambda x: x["index"])

    xga: dict[int, float] = defaultdict(float)
    xga_direct: dict[int, float] = defaultdict(float)
    no_receipt_count = 0
    missing_xg_count = 0
    missing_shooter_count = 0

    for e in events:
        if e["type"]["name"] != "Pass":
            continue
        p = e.get("pass", {})
        aid = p.get("assisted_shot_id")
        if not aid:
            continue

        shot_ev = id_to_event.get(aid)
        if shot_ev is None or shot_ev["type"]["name"] != "Shot":
            continue

        raw_xg = shot_ev.get("shot", {}).get("statsbomb_xg")
        if raw_xg is None:
            missing_xg_count += 1
            continue  # no xG value → excluded from both xg_assisted and xg_assisted_direct

        xg: float = raw_xg
        assister_id: int | None = e.get("player", {}).get("id")
        if assister_id is None:
            continue

        xga[assister_id] += xg

        # ── direct check ─────────────────────────────────────────────────────
        shooter_id: int | None = shot_ev.get("player", {}).get("id")
        shot_poss: int | None = shot_ev.get("possession")
        shot_idx: int = shot_ev["index"]  # raises KeyError on malformed data
        shooter_team_id = shot_ev.get("possession_team", {}).get("id")

        if shooter_id is None or shot_poss is None:
            # Cannot locate the shooter's possession window; credit as direct
            # (conservative: we cannot rule out that it was a first-touch shot).
            missing_shooter_count += 1
            xga_direct[assister_id] += xg
            continue

        poss_evs = poss_player_events.get((shot_poss, shooter_id), [])

        # Find the LAST "Ball Receipt*" (exact StatsBomb name) before the shot
        receipt_idx: int | None = None
        for pe in poss_evs:
            if pe["type"]["name"] == "Ball Receipt*" and pe["index"] < shot_idx:
                receipt_idx = pe["index"]

        if receipt_idx is None:
            # No receipt found: first touch is the shot → direct
            no_receipt_count += 1
            xga_direct[assister_id] += xg
            continue

        # Scan between receipt and shot (exclusive on both ends)
        blocked = False
        cumulative_carry = 0.0
        for pe in poss_evs:
            pe_idx = pe["index"]  # raises KeyError on malformed data
            if pe_idx <= receipt_idx or pe_idx >= shot_idx:
                continue
            # Turnover guard: skip events from a different team
            if pe.get("possession_team", {}).get("id") != shooter_team_id:
                continue
            if pe["type"]["name"] == "Dribble":
                blocked = True
                break
            if pe["type"]["name"] == "Carry":
                loc = pe["location"]
                end = pe["carry"]["end_location"]
                cumulative_carry += _seg_dist(loc, end)

        if not blocked and cumulative_carry > max_carry_units:
            blocked = True

        if not blocked:
            xga_direct[assister_id] += xg

    if missing_xg_count:
        logger.warning(
            "xg_assisted: %d assisted shot(s) had no statsbomb_xg value "
            "(excluded from xg_assisted total).",
            missing_xg_count,
        )
    if missing_shooter_count:
        logger.warning(
            "xg_assisted: %d assisted shot(s) had no shooter id or possession "
            "(credited as direct; conservative fallback).",
            missing_shooter_count,
        )
    if no_receipt_count:
        logger.info(
            "xg_assisted: %d assisted shot(s) had no Ball Receipt* before the shot "
            "(counted as direct; expected to be rare).",
            no_receipt_count,
        )

    # Merge: every player with xga gets an entry; default direct to 0
    result: dict[int, tuple[float, float]] = {}
    for pid, val in xga.items():
        result[pid] = (val, xga_direct.get(pid, 0.0))
    return result
