"""
Aerial duel helpers.

StatsBomb encodes the winner by placing aerial_won=True inside the winner's
own sub-event (Pass, Clearance, Shot, or Miscontrol).  The loser receives a
separate Duel event with duel.type.name == "Aerial Lost".  There is no
"Aerial Won" event type.

See ADR 002 §20 for symmetry contract and minimum-sample threshold.
"""
from __future__ import annotations

_AERIAL_WON_SUB_KEYS: dict[str, str] = {
    "Pass": "pass",
    "Clearance": "clearance",
    "Shot": "shot",
    "Miscontrol": "miscontrol",
}


def has_aerial_won(event: dict) -> bool:
    """Return True when this event's sub-payload carries aerial_won=True."""
    key = _AERIAL_WON_SUB_KEYS.get((event.get("type") or {}).get("name", ""))
    if not key:
        return False
    return bool((event.get(key) or {}).get("aerial_won"))


def is_aerial_lost(event: dict) -> bool:
    """Return True when this Duel event represents an aerial loss."""
    if (event.get("type") or {}).get("name") != "Duel":
        return False
    return ((event.get("duel") or {}).get("type") or {}).get("name") == "Aerial Lost"
