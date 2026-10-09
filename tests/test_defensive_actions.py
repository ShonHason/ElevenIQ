"""
The shared Defending action rule (ADR 004 §13.8, plan 005 M4g4b; metrics/defensive_actions.py),
tested from its docstring: Ball Recovery without recovery_failure; Duel of type Tackle; Block
neither offensive nor save_block; Pressure, Clearance, Dribbled Past, Foul Committed,
Interception by type alone; anything else None. The player's own counts use the same rule.
"""
from __future__ import annotations

from collections import Counter

import pytest

from eleveniq.metrics.defensive_actions import DEFENSIVE_ACTIONS, defensive_action, team_column
from eleveniq.pipeline.build_player_match import _aggregate_events


def ev(kind: str, **payload) -> dict:
    return {"type": {"name": kind}, **payload}


@pytest.mark.parametrize("event,action", [
    (ev("Ball Recovery"), "ball_recoveries"),
    (ev("Ball Recovery", ball_recovery={"recovery_failure": False}), "ball_recoveries"),
    (ev("Ball Recovery", ball_recovery={"recovery_failure": True}), None),
    (ev("Duel", duel={"type": {"name": "Tackle"}}), "tackles_attempted"),
    (ev("Duel", duel={"type": {"name": "Aerial Lost"}}), None),
    (ev("Duel"), None),
    (ev("Block"), "blocks"),
    (ev("Block", block={"offensive": True}), None),
    (ev("Block", block={"save_block": True}), None),
    (ev("Pressure"), "pressures"),
    (ev("Clearance"), "clearances"),
    (ev("Dribbled Past"), "dribbled_past"),
    (ev("Foul Committed"), "fouls_committed"),
    (ev("Interception"), "interceptions"),
    (ev("Foul Won"), None),
    (ev("Pass"), None),
    ({}, None),
])
def test_defensive_action(event, action):
    assert defensive_action(event) == action


def test_the_eight_actions_and_their_team_columns():
    assert set(DEFENSIVE_ACTIONS) == {"ball_recoveries", "tackles_attempted", "pressures",
                                      "clearances", "dribbled_past", "fouls_committed",
                                      "blocks", "interceptions"}
    assert team_column("pressures") == "team_pressures_while_on"
    with pytest.raises(ValueError):
        team_column("tackles_won")


def test_player_counts_use_the_same_rule():
    events = [ev("Ball Recovery"), ev("Ball Recovery", ball_recovery={"recovery_failure": True}),
              ev("Duel", duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
              ev("Block", block={"save_block": True}), ev("Block"), ev("Pressure"),
              ev("Clearance"), ev("Dribbled Past"), ev("Foul Committed"), ev("Interception")]
    counts = _aggregate_events(events, Counter())
    expected = Counter(a for a in map(defensive_action, events) if a)
    assert {a: counts[a] for a in DEFENSIVE_ACTIONS} == {a: expected[a] for a in DEFENSIVE_ACTIONS}
    assert counts["ball_recoveries_failed"] == 1 and counts["tackles_won"] == 1
