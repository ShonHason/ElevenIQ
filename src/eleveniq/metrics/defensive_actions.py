"""
The Defending actions with a team-share candidate (ADR 004 §13.8, plan 005 M4g4b).

One rule decides which event counts as which action. The player's own counts
(_aggregate_events) and the team counts while he is on (team_<action>_while_on) both
call it, so the two cannot drift apart (the is_restart precedent, ADR 004 D14).

The 8 actions are the Defending DNA members whose team share is defined (per-90 counts,
not ratios); tackles_won is D17-dropped in every group and is not one of them.
"""
from __future__ import annotations

DEFENSIVE_ACTIONS: tuple[str, ...] = (
    "ball_recoveries",
    "tackles_attempted",
    "pressures",
    "clearances",
    "dribbled_past",
    "fouls_committed",
    "blocks",
    "interceptions",
)

# Event types that count as one action whatever their payload.
_BY_TYPE: dict[str, str] = {
    "Pressure": "pressures",
    "Clearance": "clearances",
    "Dribbled Past": "dribbled_past",
    "Foul Committed": "fouls_committed",
    "Interception": "interceptions",
}


def defensive_action(event: dict) -> str | None:
    """
    The action an event counts as, or None:
      - ball_recoveries: a Ball Recovery without ball_recovery.recovery_failure;
      - tackles_attempted: a Duel whose duel.type is Tackle;
      - blocks: a Block that is neither block.offensive nor block.save_block;
      - pressures, clearances, dribbled_past, fouls_committed, interceptions: the event
        type alone.
    The caller decides the scope (playing time, a player on the event).
    """
    event_type = (event.get("type") or {}).get("name", "")
    if event_type in _BY_TYPE:
        return _BY_TYPE[event_type]
    if event_type == "Ball Recovery":
        return None if (event.get("ball_recovery") or {}).get("recovery_failure") else "ball_recoveries"
    if event_type == "Duel":
        tackle = ((event.get("duel") or {}).get("type") or {}).get("name") == "Tackle"
        return "tackles_attempted" if tackle else None
    if event_type == "Block":
        block = event.get("block") or {}
        return None if block.get("offensive") or block.get("save_block") else "blocks"
    return None


def team_column(action: str) -> str:
    """The team count column of an action: team_<action>_while_on."""
    if action not in DEFENSIVE_ACTIONS:
        raise ValueError(f"not a defensive action: {action!r}")
    return f"team_{action}_while_on"
