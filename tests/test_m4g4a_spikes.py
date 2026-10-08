"""
M4g4a spikes (plan 005 M4g4 plan; ADR 004 §13.8), tested from the written rules:
- opponent exposure (spikes/m4g_opponent_exposure.py): the D12 window rule mirrored onto the
  opponent: a player is credited when the pass's elapsed instant lies in one of his segments
  [a, b) inside the pass's period, or is the period's end and a segment ends there; zero-length
  segments credit nothing; restarts and period 5 are not open play; own credits always include
  the passer; opponent credits never use a passer rule; a pass credited to fewer than 7 or more
  than 11 defenders is recorded;
- power (spikes/m4g4_power.py): P(PASS on all six streams) ≤ P(PASS on ≥ 4 of 6) ≤ ... and
  ≤ P(PASS primary); a cell is deterministic;
- STOP rule (spikes/m4g4a_movers_before_after.py): every existing mover field identical,
  s2_rms_a the only added key and only on testable metrics; registry unchanged but its version.
No data needed.
"""
from __future__ import annotations

import copy
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g4_power as pw4  # noqa: E402
import m4g4a_movers_before_after as ba  # noqa: E402
import m4g_opponent_exposure as ox  # noqa: E402


# ---------------------------------------------------------------- opponent exposure

class _Clock:
    """Two 45-minute periods, no stoppage: elapsed = minute (+ 0 offset for period 2 at 45)."""
    offset = {1: 0.0, 2: 45.0}

    def length(self, p: int) -> float:
        return 45.0

    def elapsed(self, at: tuple[int, float]) -> float:
        p, m = at
        start = {1: 0.0, 2: 45.0}[p]
        return self.offset[p] + min(max(m, start), start + 45.0) - start


def _pass(team: int, player: int, period: int, minute: int, second: int = 0, kind: str | None = None) -> dict:
    return {"id": f"{team}-{player}-{minute}-{second}", "type": {"name": "Pass"}, "period": period,
            "minute": minute, "second": second, "team": {"id": team}, "player": {"id": player},
            "pass": {"type": {"name": kind}} if kind else {}}


def _teams(home_segments: dict[int, list], away_segments: dict[int, list]) -> list[dict]:
    mk = lambda segs: [{"player_id": pid, "segments": [(a, b, "X") for a, b in s]}  # noqa: E731
                       for pid, s in segs.items()]
    return [{"team_id": 1, "players": mk(home_segments)}, {"team_id": 2, "players": mk(away_segments)}]


FULL = [(0.0, 90.0)]


def test_holds_is_half_open_and_closed_at_the_period_end():
    segs = [(10.0, 20.0, "X")]
    assert ox.holds(segs, 10.0, 0.0, 45.0)
    assert not ox.holds(segs, 20.0, 0.0, 45.0)
    assert ox.holds([(30.0, 45.0, "X")], 45.0, 0.0, 45.0)        # the period's end, segment ends there
    assert not ox.holds([(30.0, 44.0, "X")], 45.0, 0.0, 45.0)
    assert not ox.holds([(10.0, 10.0, "X")], 10.0, 0.0, 45.0)     # zero length credits nothing
    assert not ox.holds([(40.0, 50.0, "X")], 46.0, 0.0, 45.0)     # outside the pass's period


def test_open_play_filter():
    assert ox.is_open_play_pass(_pass(1, 10, 1, 5))
    assert ox.is_open_play_pass(_pass(1, 10, 1, 5, kind="Recovery"))
    for kind in ("Corner", "Free Kick", "Goal Kick", "Kick Off", "Throw-in"):
        assert not ox.is_open_play_pass(_pass(1, 10, 1, 5, kind=kind))
    e = _pass(1, 10, 1, 5)
    e["period"] = 5
    assert not ox.is_open_play_pass(e)
    e = _pass(1, 10, 1, 5)
    e["player"] = None
    assert not ox.is_open_play_pass(e)


def test_credits_own_and_opponent():
    home = {i: FULL for i in range(10, 21)}                       # 11 on for 90
    away = {i: FULL for i in range(30, 40)} | {40: [(0.0, 30.0)]}  # 40 off at 30
    events = [_pass(1, 10, 1, 10), _pass(1, 10, 1, 35), _pass(2, 30, 2, 60),
              _pass(1, 11, 1, 20, kind="Corner")]
    per_pass: Counter = Counter()
    own, opp, passes, bad = ox.match_credits(1, events, _teams(home, away), _Clock(), per_pass)
    assert passes == Counter({1: 2, 2: 1})                        # the corner is not open play
    assert opp[40] == 1 and opp[30] == 2                          # 40 defends the 10th-minute pass only
    assert own[10] == 2 and own[11] == 2 and own[30] == 1         # every on-pitch teammate, passer included
    assert opp[10] == 1                                           # home players defend the away pass
    assert per_pass == Counter({11: 2, 10: 1}) and bad == []


def test_passer_outside_his_own_time_is_still_credited_own_not_opponent():
    home = {10: [(0.0, 5.0)]} | {i: FULL for i in range(11, 21)}
    away = {i: FULL for i in range(30, 41)}
    own, opp, _, _ = ox.match_credits(1, [_pass(1, 10, 1, 30)], _teams(home, away), _Clock(), Counter())
    assert own[10] == 1 and opp[10] == 0


def test_fewer_than_seven_defenders_is_recorded():
    home = {i: FULL for i in range(10, 21)}
    away = {i: FULL for i in range(30, 36)}                       # 6 on the pitch
    per_pass: Counter = Counter()
    _, _, _, bad = ox.match_credits(1, [_pass(1, 10, 1, 30)], _teams(home, away), _Clock(), per_pass)
    assert len(bad) == 1 and bad[0]["defenders"] == 6 and per_pass == Counter({6: 1})


# ---------------------------------------------------------------- power

def test_six_stream_pass_implies_primary_and_majority():
    c = pw4.cell(24, 0.6, 0.0, datasets=8)
    assert c["p_pass_all6"] <= c["p_pass_4of6"] and c["p_pass_all6"] <= c["p_pass_primary"]
    assert c == pw4.cell(24, 0.6, 0.0, datasets=8)                # deterministic per cell


# ---------------------------------------------------------------- STOP rule

def _block() -> dict:
    return {"cohorts": {"x": 1}, "movers": {"b": 2000, "metrics": {
        "a_p90": {"n": 24, "f_hat": 0.3, "ci_lo": -0.1, "ci_hi": 0.9, "extra_seeds": []},
        "u_pct": {"n": 4, "extra_seeds": []}}}}


def _after() -> dict:
    a = copy.deepcopy(_block())
    a["movers"]["metrics"]["a_p90"]["s2_rms_a"] = 1.1
    return a


def test_stop_rule_accepts_only_the_added_key():
    assert ba.measurement_errors(_block(), _after()) == []


def test_stop_rule_rejects_a_changed_field_a_missing_key_or_a_key_on_an_untestable_metric():
    a = _after()
    a["movers"]["metrics"]["a_p90"]["f_hat"] = 0.31
    assert ba.measurement_errors(_block(), a)
    a = _block()                                                  # s2_rms_a missing
    assert ba.measurement_errors(_block(), a)
    a = _after()
    a["movers"]["metrics"]["u_pct"]["s2_rms_a"] = 1.0
    assert ba.measurement_errors(_block(), a)
    a = _after()
    a["movers"]["metrics"]["a_p90"]["other"] = 1
    assert ba.measurement_errors(_block(), a)
    a = _after()
    a["cohorts"]["x"] = 2
    assert ba.measurement_errors(_block(), a)


def test_stop_rule_registry_only_the_version_may_change():
    before = {"cells": [1], "movers": {"m": "PASS"}, "registry_version": "a"}
    assert ba.registry_errors(before, {**before, "registry_version": "b"}) == []
    assert ba.registry_errors(before, before)                     # the version must move
    assert ba.registry_errors(before, {**before, "movers": {"m": "inconclusive"}, "registry_version": "b"})
