"""Tests for eleveniq/metrics/xg_assisted.py."""
from collections import Counter

import pytest
from eleveniq.metrics.xg_assisted import (
    XGA_DANGLING_SHOT_ID,
    XGA_MISSING_ASSISTER,
    XGA_MISSING_SHOOTER,
    XGA_NO_RECEIPT,
    compute_xg_assisted_per_player,
)

MAX_CARRY = 10.0  # default threshold used across tests


# ---------------------------------------------------------------------------
# Minimal event-dict helpers
# ---------------------------------------------------------------------------

def _pass(
    player_id: int,
    shot_id: str,
    *,
    match_id: int = 1,
    idx: int = 1,
    possession: int = 1,
    possession_team_id: int = 100,
    goal_assist: bool = False,
    shot_assist: bool = True,
) -> dict:
    return {
        "id": f"pass-{player_id}-{idx}",
        "index": idx,
        "type": {"name": "Pass"},
        "player": {"id": player_id, "name": f"Player{player_id}"},
        "possession": possession,
        "possession_team": {"id": possession_team_id},
        "pass": {
            "assisted_shot_id": shot_id,
            "shot_assist": shot_assist and not goal_assist,
            "goal_assist": goal_assist,
        },
    }


def _shot(
    shot_id: str,
    shooter_id: int,
    xg: float,
    *,
    idx: int = 5,
    possession: int = 1,
    possession_team_id: int = 100,
    key_pass_id: str | None = None,
) -> dict:
    return {
        "id": shot_id,
        "index": idx,
        "type": {"name": "Shot"},
        "player": {"id": shooter_id, "name": f"Shooter{shooter_id}"},
        "possession": possession,
        "possession_team": {"id": possession_team_id},
        "shot": {
            "statsbomb_xg": xg,
            "key_pass_id": key_pass_id,
        },
    }


def _receipt(
    player_id: int,
    *,
    idx: int = 2,
    possession: int = 1,
    possession_team_id: int = 100,
) -> dict:
    return {
        "id": f"receipt-{player_id}-{idx}",
        "index": idx,
        "type": {"name": "Ball Receipt*"},
        "player": {"id": player_id},
        "possession": possession,
        "possession_team": {"id": possession_team_id},
    }


def _carry(
    player_id: int,
    start: list[float],
    end: list[float],
    *,
    idx: int = 3,
    possession: int = 1,
    possession_team_id: int = 100,
) -> dict:
    return {
        "id": f"carry-{player_id}-{idx}",
        "index": idx,
        "type": {"name": "Carry"},
        "player": {"id": player_id},
        "location": start,
        "carry": {"end_location": end},
        "possession": possession,
        "possession_team": {"id": possession_team_id},
    }


def _dribble(
    player_id: int,
    *,
    idx: int = 3,
    possession: int = 1,
    possession_team_id: int = 100,
) -> dict:
    return {
        "id": f"dribble-{player_id}-{idx}",
        "index": idx,
        "type": {"name": "Dribble"},
        "player": {"id": player_id},
        "possession": possession,
        "possession_team": {"id": possession_team_id},
    }


# ---------------------------------------------------------------------------
# Basic credit
# ---------------------------------------------------------------------------

class TestBasicCredit:
    def test_simple_one_pass_one_shot(self):
        shot_id = "shot-1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][0] == pytest.approx(0.2)
        assert result[1][1] == pytest.approx(0.2)  # direct (short receipt→shot window)

    def test_goal_assist_only_pass_is_credited(self):
        """goal_assist=True (no shot_assist) must be credited via assisted_shot_id."""
        shot_id = "shot-ga"
        events = [
            _pass(1, shot_id, idx=1, goal_assist=True, shot_assist=False),
            _receipt(2, idx=2),
            _shot(shot_id, shooter_id=2, xg=0.5, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][0] == pytest.approx(0.5)

    def test_no_assist_pass_not_credited(self):
        """A pass without assisted_shot_id contributes nothing."""
        events = [
            {
                "id": "p-no-assist",
                "index": 1,
                "type": {"name": "Pass"},
                "player": {"id": 1},
                "possession": 1,
                "possession_team": {"id": 100},
                "pass": {},
            },
            _receipt(2, idx=2),
            _shot("shot-1", shooter_id=2, xg=0.3, idx=5),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert 1 not in result

    def test_two_passers_each_credited_their_own_shot(self):
        events = [
            _pass(1, "s1", idx=1, possession=1),
            _receipt(10, idx=2, possession=1),
            _shot("s1", shooter_id=10, xg=0.1, idx=5, possession=1,
                  key_pass_id="pass-1-1"),
            _pass(2, "s2", idx=10, possession=2),
            _receipt(20, idx=11, possession=2),
            _shot("s2", shooter_id=20, xg=0.3, idx=15, possession=2,
                  key_pass_id="pass-2-10"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][0] == pytest.approx(0.1)
        assert result[2][0] == pytest.approx(0.3)
        assert 20 not in result  # shooters get no credit

    def test_reconciliation_xga_sum_equals_shot_side(self):
        """Sum of xga over all players == sum of xg for shots with key_pass_id."""
        shot_id_a, shot_id_b = "sa", "sb"
        events = [
            _pass(1, shot_id_a, idx=1, possession=1),
            _receipt(10, idx=2, possession=1),
            _shot(shot_id_a, shooter_id=10, xg=0.15, idx=5, possession=1,
                  key_pass_id="pass-1-1"),
            _pass(2, shot_id_b, idx=10, possession=2),
            _receipt(20, idx=11, possession=2),
            _shot(shot_id_b, shooter_id=20, xg=0.25, idx=15, possession=2,
                  key_pass_id="pass-2-10"),
            # shot with no key_pass_id — should not appear in xga sum
            _shot("unassisted", shooter_id=30, xg=0.40, idx=20, possession=3),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        total_xga = sum(v[0] for v in result.values())
        shot_side = sum(
            e["shot"]["statsbomb_xg"]
            for e in events
            if e["type"]["name"] == "Shot" and e["shot"].get("key_pass_id")
        )
        assert total_xga == pytest.approx(shot_side)
        assert total_xga == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# xg_assisted_direct — carry rule
# ---------------------------------------------------------------------------

class TestXgAssistedDirect:
    def test_no_carry_between_receipt_and_shot_is_direct(self):
        """Receipt then immediate shot → direct."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.2)
        assert xga_d == pytest.approx(0.2)

    def test_short_carry_within_threshold_is_direct(self):
        """Carry of 5 units (≤ 10) → still direct."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [100.0, 40.0], [105.0, 40.0], idx=3),  # 5 units
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][1] == pytest.approx(0.2)

    def test_long_carry_exceeds_threshold_not_direct(self):
        """Carry of 15 units (> 10) → NOT direct; xga credited, xga_direct not."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [80.0, 40.0], [95.0, 40.0], idx=3),  # 15 units
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.2)
        assert xga_d == pytest.approx(0.0)

    def test_two_short_carries_summing_over_threshold_not_direct(self):
        """Two carries of 6 units each = 12 units total → NOT direct (cumulative rule)."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [90.0, 40.0], [96.0, 40.0], idx=3),   # 6 units
            _carry(2, [96.0, 40.0], [102.0, 40.0], idx=4),  # 6 units (total 12)
            _shot(shot_id, shooter_id=2, xg=0.3, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.3)
        assert xga_d == pytest.approx(0.0)

    def test_dribble_between_receipt_and_shot_not_direct(self):
        """Any Dribble → NOT direct regardless of carry distance."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _dribble(2, idx=3),
            _shot(shot_id, shooter_id=2, xg=0.25, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.25)
        assert xga_d == pytest.approx(0.0)

    def test_dribble_with_short_carry_still_not_direct(self):
        """Dribble + short carry → NOT direct (dribble disqualifies regardless)."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [100.0, 40.0], [103.0, 40.0], idx=3),  # 3 units
            _dribble(2, idx=4),
            _shot(shot_id, shooter_id=2, xg=0.15, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][1] == pytest.approx(0.0)

    def test_no_receipt_before_shot_is_not_direct(self):
        """ADR 004 D16: with no Ball Receipt* the window cannot be checked, so the
        shot counts toward xg_assisted but not xg_assisted_direct, and the fallback
        is counted."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            # No receipt event for shooter (id=2)
            _shot(shot_id, shooter_id=2, xg=0.35, idx=5, key_pass_id="pass-1-1"),
        ]
        fallbacks: Counter = Counter()
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=fallbacks)
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.35)
        assert xga_d == 0.0
        assert fallbacks == Counter({XGA_NO_RECEIPT: 1})

    def test_receipt_after_shot_does_not_define_the_window(self):
        """ADR 004 D16: a Ball Receipt* after the shot is not in the window, so the
        shot takes the no-receipt fallback: not direct."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            # receipt at index 10 (AFTER shot at index 5)
            _receipt(2, idx=10),
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        # No receipt before shot → not direct (D16)
        assert result[1][0] == pytest.approx(0.2)
        assert result[1][1] == 0.0

    def test_uses_last_receipt_before_shot(self):
        """With two receipts before the shot, only carries AFTER the LAST one matter."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            # First receipt at index 2 — any carry before this is outside the window
            _receipt(2, idx=2),
            # Long carry at index 3 (AFTER first receipt)
            _carry(2, [50.0, 40.0], [70.0, 40.0], idx=3),   # 20 units
            # Second (last) receipt at index 4
            _receipt(2, idx=4),
            # Short carry at index 5 (AFTER last receipt)
            _carry(2, [100.0, 40.0], [105.0, 40.0], idx=5),  # 5 units
            _shot(shot_id, shooter_id=2, xg=0.2, idx=10, key_pass_id="pass-1-1"),
        ]
        # Last receipt is at index 4; only carry at index 5 (5 units) is in window
        # 5 units ≤ 10 → direct
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][1] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Team-ownership guard
# ---------------------------------------------------------------------------

class TestTeamOwnershipGuard:
    def test_foreign_team_event_between_receipt_and_shot_ignored(self):
        """An event from the opposing team in the window is skipped; shot still direct."""
        shot_id = "s1"
        foreign_carry = {
            "id": "foreign-carry",
            "index": 3,
            "type": {"name": "Carry"},
            "player": {"id": 99},   # different player, different team
            "location": [80.0, 40.0],
            "carry": {"end_location": [100.0, 40.0]},  # 20 units
            "possession": 1,
            "possession_team": {"id": 999},  # DIFFERENT team
        }
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            foreign_carry,
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5,
                  possession_team_id=100, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        # Foreign-team carry is ignored; no other blocking action → direct
        assert result[1][1] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Edge cases — threshold boundary, null xg, multiple shots, invariants
# ---------------------------------------------------------------------------

class TestXgAssistedEdgeCases:
    def test_carry_exactly_at_threshold_is_direct(self):
        """ADR §26: carry sum > max_carry_units disqualifies. Equal to threshold is still direct."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [90.0, 40.0], [100.0, 40.0], idx=3),  # exactly 10 units
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        # 10.0 is not > 10.0, so still direct
        assert result[1][1] == pytest.approx(0.2), (
            "Carry sum equal to max_carry_units must still be direct "
            "(threshold condition is >, not >=)"
        )

    def test_carry_one_unit_over_threshold_not_direct(self):
        """One unit over the threshold must not be direct."""
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            _carry(2, [90.0, 40.0], [100.1, 40.0], idx=3),  # 10.1 units
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][1] == pytest.approx(0.0), (
            "Carry sum just over max_carry_units must not be direct"
        )

    def test_missing_xg_on_shot_excluded_from_result(self):
        """A shot with statsbomb_xg=None must be excluded — assister gets no credit."""
        shot_id = "s-null-xg"
        null_shot = {
            "id": shot_id,
            "index": 5,
            "type": {"name": "Shot"},
            "player": {"id": 2},
            "possession": 1,
            "possession_team": {"id": 100},
            "shot": {
                "statsbomb_xg": None,
                "key_pass_id": "pass-1-1",
            },
        }
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            null_shot,
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert 1 not in result, (
            "Assisted shot with missing statsbomb_xg must be excluded from result; "
            "assister (player 1) must not appear in the output dict"
        )

    def test_shot_missing_index_raises(self):
        """A shot event without an 'index' key must raise KeyError (malformed data).

        The KeyError fires at the sort step (line ~71:
        ``poss_player_events[key].sort(key=lambda x: x["index"])``),
        not at the later ``shot_ev["index"]`` lookup — the sort happens first
        because the shot event has a player_id and possession and therefore
        enters poss_player_events.
        """
        shot_id = "s-no-index"
        events = [
            _pass(1, shot_id, idx=1),
            {
                "id": shot_id,
                # No "index" key — malformed
                "type": {"name": "Shot"},
                "player": {"id": 2},
                "possession": 1,
                "possession_team": {"id": 100},
                "shot": {"statsbomb_xg": 0.2, "key_pass_id": "pass-1-1"},
            },
        ]
        with pytest.raises(KeyError):
            compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())

    def test_shot_missing_shooter_id_is_not_direct(self):
        """ADR 004 D16: a shot with no player.id has no window to check, so the
        assister gets xg_assisted but not xg_assisted_direct."""
        shot_id = "s-no-shooter"
        events = [
            _pass(1, shot_id, idx=1),
            {
                "id": shot_id,
                "index": 5,
                "type": {"name": "Shot"},
                "player": {},  # no id field
                "possession": 1,
                "possession_team": {"id": 100},
                "shot": {"statsbomb_xg": 0.3, "key_pass_id": "pass-1-1"},
            },
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.3)
        assert xga_d == 0.0, (
            "ADR 004 D16: no possession window without a shooter id, so not direct"
        )

    def test_xg_assisted_direct_never_exceeds_xg_assisted(self):
        """Invariant: xg_assisted_direct <= xg_assisted for every player in any scenario."""
        shot_id_a, shot_id_b = "sa", "sb"
        events = [
            _pass(1, shot_id_a, idx=1, possession=1),
            _receipt(10, idx=2, possession=1),
            # Short carry — direct
            _carry(10, [100.0, 40.0], [104.0, 40.0], idx=3, possession=1),
            _shot(shot_id_a, shooter_id=10, xg=0.2, idx=5, possession=1,
                  key_pass_id="pass-1-1"),
            # Same passer, second shot — shooter dribbles → not direct
            _pass(1, shot_id_b, idx=10, possession=2),
            _receipt(10, idx=11, possession=2),
            _dribble(10, idx=12, possession=2),
            _shot(shot_id_b, shooter_id=10, xg=0.3, idx=15, possession=2,
                  key_pass_id="pass-1-10"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.5)
        assert xga_d == pytest.approx(0.2)
        assert xga_d <= xga, "xg_assisted_direct must never exceed xg_assisted"

    def test_multiple_shots_by_same_passer_accumulate(self):
        """A passer who creates multiple assisted shots accumulates xGA correctly."""
        sid_a, sid_b, sid_c = "s-a", "s-b", "s-c"
        events = [
            _pass(1, sid_a, idx=1, possession=1),
            _receipt(10, idx=2, possession=1),
            _shot(sid_a, shooter_id=10, xg=0.1, idx=5, possession=1,
                  key_pass_id="pass-1-1"),
            _pass(1, sid_b, idx=10, possession=2),
            _receipt(10, idx=11, possession=2),
            _shot(sid_b, shooter_id=10, xg=0.2, idx=15, possession=2,
                  key_pass_id="pass-1-10"),
            _pass(1, sid_c, idx=20, possession=3),
            _receipt(10, idx=21, possession=3),
            _shot(sid_c, shooter_id=10, xg=0.3, idx=25, possession=3,
                  key_pass_id="pass-1-20"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result[1][0] == pytest.approx(0.6), (
            "xg_assisted must accumulate across all assisted shots for the same passer"
        )

    def test_assisted_shot_id_resolves_to_non_shot_event_ignored(self):
        """If assisted_shot_id points to a non-Shot event, it must be silently ignored."""
        non_shot_id = "some-carry"
        carry_event = {
            "id": non_shot_id,
            "index": 3,
            "type": {"name": "Carry"},
            "player": {"id": 99},
            "possession": 1,
            "possession_team": {"id": 100},
            "location": [80.0, 40.0],
            "carry": {"end_location": [90.0, 40.0]},
        }
        events = [
            {
                "id": "pass-with-wrong-id",
                "index": 1,
                "type": {"name": "Pass"},
                "player": {"id": 1},
                "possession": 1,
                "possession_team": {"id": 100},
                "pass": {"assisted_shot_id": non_shot_id},
            },
            carry_event,
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        # The pass resolved to a carry, not a shot → no credit
        assert 1 not in result, (
            "assisted_shot_id resolving to a non-Shot event must not credit the passer"
        )

    def test_empty_event_list_returns_empty_dict(self):
        """An empty event list must return an empty dict without error."""
        result = compute_xg_assisted_per_player([], max_carry_units=MAX_CARRY, fallbacks=Counter())
        assert result == {}, "Empty event list must produce an empty result dict"

    def test_shot_missing_possession_is_not_direct(self):
        """ADR 004 D16: shot with possession=None → shooter window unknown → not direct."""
        shot_id = "s-no-poss"
        events = [
            _pass(1, shot_id, idx=1),
            {
                "id": shot_id,
                "index": 5,
                "type": {"name": "Shot"},
                "player": {"id": 2},
                # possession key is absent → None
                "possession_team": {"id": 100},
                "shot": {"statsbomb_xg": 0.4, "key_pass_id": "pass-1-1"},
            },
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        xga, xga_d = result[1]
        assert xga == pytest.approx(0.4)
        assert xga_d == 0.0, (
            "ADR 004 D16: a shot with missing possession cannot have its carry "
            "window located, so it is not direct"
        )

    def test_passer_without_player_id_not_credited(self):
        """A pass event with no player.id must not crash and must not appear in result."""
        shot_id = "s1"
        events = [
            {
                "id": "pass-no-player",
                "index": 1,
                "type": {"name": "Pass"},
                "player": {},   # no id
                "possession": 1,
                "possession_team": {"id": 100},
                "pass": {"assisted_shot_id": shot_id},
            },
            _receipt(2, idx=2),
            _shot(shot_id, shooter_id=2, xg=0.3, idx=5, key_pass_id="pass-no-player"),
        ]
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())
        # No player id on the pass → assister_id is None → no entry in result
        assert not any(k is None for k in result), (
            "None must not appear as a key in the result dict"
        )

    def test_carry_missing_end_location_raises(self):
        """A Carry event in the receipt-to-shot window with no end_location raises KeyError.

        StatsBomb always provides carry.end_location; absence means malformed data.
        The implementation uses direct access (pe["carry"]["end_location"]) to surface
        this immediately rather than silently treating the carry as zero-distance.
        """
        shot_id = "s1"
        events = [
            _pass(1, shot_id, idx=1),
            _receipt(2, idx=2),
            {
                "id": "carry-no-end",
                "index": 3,
                "type": {"name": "Carry"},
                "player": {"id": 2},
                "location": [100.0, 40.0],
                "carry": {},   # end_location absent — malformed
                "possession": 1,
                "possession_team": {"id": 100},
            },
            _shot(shot_id, shooter_id=2, xg=0.2, idx=5, key_pass_id="pass-1-1"),
        ]
        with pytest.raises(KeyError):
            compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=Counter())


class TestXgAssistedFallbacksD16:
    """ADR 004 D16: the receipt must follow the key pass, and every fallback and
    broken link is counted (report-only issues in the pipeline)."""

    def test_receipt_before_the_key_pass_does_not_define_the_window(self):
        """The shooter's only receipt (index 0) precedes the key pass (index 1):
        not direct, counted as no-receipt."""
        events = [
            _receipt(2, idx=0),
            _pass(1, "s1", idx=1),
            _shot("s1", shooter_id=2, xg=0.3, idx=5, key_pass_id="pass-1-1"),
        ]
        fallbacks: Counter = Counter()
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=fallbacks)
        assert result[1] == (pytest.approx(0.3), 0.0)
        assert fallbacks == Counter({XGA_NO_RECEIPT: 1})

    def test_receipt_after_the_key_pass_is_direct_and_counts_nothing(self):
        events = [
            _pass(1, "s1", idx=1),
            _receipt(2, idx=2),
            _shot("s1", shooter_id=2, xg=0.3, idx=5, key_pass_id="pass-1-1"),
        ]
        fallbacks: Counter = Counter()
        result = compute_xg_assisted_per_player(events, max_carry_units=MAX_CARRY, fallbacks=fallbacks)
        assert result[1] == (pytest.approx(0.3), pytest.approx(0.3))
        assert not fallbacks

    def test_dangling_assisted_shot_id_is_counted(self):
        fallbacks: Counter = Counter()
        result = compute_xg_assisted_per_player(
            [_pass(1, "missing-shot", idx=1)], max_carry_units=MAX_CARRY, fallbacks=fallbacks)
        assert result == {}
        assert fallbacks == Counter({XGA_DANGLING_SHOT_ID: 1})

    def test_missing_shooter_and_missing_assister_are_counted(self):
        no_shooter = {"id": "s1", "index": 5, "type": {"name": "Shot"}, "player": {},
                      "possession": 1, "possession_team": {"id": 100},
                      "shot": {"statsbomb_xg": 0.3, "key_pass_id": "pass-1-1"}}
        no_assister = {**_pass(1, "s2", idx=6), "player": {}, "id": "pass-x"}
        fallbacks: Counter = Counter()
        compute_xg_assisted_per_player(
            [_pass(1, "s1", idx=1), no_shooter, no_assister,
             _shot("s2", shooter_id=2, xg=0.1, idx=8, key_pass_id="pass-x")],
            max_carry_units=MAX_CARRY, fallbacks=fallbacks)
        assert fallbacks == Counter({XGA_MISSING_SHOOTER: 1, XGA_MISSING_ASSISTER: 1})

    def test_fallbacks_is_a_required_keyword(self):
        with pytest.raises(TypeError):
            compute_xg_assisted_per_player([], max_carry_units=MAX_CARRY)
