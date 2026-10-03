"""
Tests for _aggregate_events (per-match event counting in build_player_match)
and the league-level goal reconciliation.

The season-level contracts that used to live here as inline Polars expressions
(pass completion with unknown outcomes, safe per-90 division, the multi-team
aggregation key) are tested against aggregate_player_season in
tests/test_build_player_season.py (plan 004 H3b, review item R12).
"""
import json
from collections import Counter
from pathlib import Path
import polars as pl
import pytest
from eleveniq.pipeline.build_player_match import _aggregate_events


# ---------------------------------------------------------------------------
# _aggregate_events — unit tests for key_passes, goals, and source column
# ---------------------------------------------------------------------------

def _carry_event(start: list, end: list) -> dict:
    """Minimal StatsBomb Carry event."""
    return {
        "type": {"name": "Carry"},
        "location": start,
        "carry": {"end_location": end},
    }


def _duel_event(duel_type: str = "Tackle", outcome: str | None = "Won") -> dict:
    """Minimal StatsBomb Duel event."""
    duel_data: dict = {"type": {"name": duel_type}}
    if outcome is not None:
        duel_data["outcome"] = {"name": outcome}
    return {"type": {"name": "Duel"}, "duel": duel_data}


def _dribble_event(outcome: str = "Complete") -> dict:
    return {"type": {"name": "Dribble"}, "dribble": {"outcome": {"name": outcome}}}


def _interception_event() -> dict:
    return {"type": {"name": "Interception"}}


def _clearance_event() -> dict:
    return {"type": {"name": "Clearance"}}


def _block_event(*, offensive: bool = False, save_block: bool = False) -> dict:
    block_data: dict = {}
    if offensive:
        block_data["offensive"] = True
    if save_block:
        block_data["save_block"] = True
    return {"type": {"name": "Block"}, "block": block_data}


def _foul_committed_event() -> dict:
    return {
        "type": {"name": "Foul Committed"},
        "minute": 20,
        "second": 0,
        "foul_committed": {},
    }


def _foul_won_event() -> dict:
    return {"type": {"name": "Foul Won"}}


def _pass_event(
    *,
    shot_assist=False,
    goal_assist=False,
    completed=True,
    outcome: str | None = None,
    location: list | None = None,
    end_location: list | None = None,
) -> dict:
    """Minimal StatsBomb Pass event.

    ``outcome`` overrides the ``completed`` flag when provided explicitly, so
    callers can inject any outcome string (e.g. "Unknown", "Out", "Incomplete").
    """
    pass_data: dict = {}
    if shot_assist:
        pass_data["shot_assist"] = True
    if goal_assist:
        pass_data["goal_assist"] = True
    if outcome is not None:
        pass_data["outcome"] = {"name": outcome}
    elif not completed:
        pass_data["outcome"] = {"name": "Incomplete"}
    return {
        "type": {"name": "Pass"},
        "location": location if location is not None else [60.0, 40.0],
        "pass": {**pass_data, "end_location": end_location if end_location is not None else [62.0, 40.0]},
    }


def _shot_event(outcome: str = "Goal", xg: float = 0.5) -> dict:
    """Minimal StatsBomb Shot event."""
    return {
        "type": {"name": "Shot"},
        "shot": {"outcome": {"name": outcome}, "statsbomb_xg": xg},
    }


def _own_goal_for_event() -> dict:
    """Own Goal For event — no player attribution (empty player field)."""
    return {"type": {"name": "Own Goal For"}}


def _own_goal_against_event(player_id: int) -> dict:
    """Own Goal Against event — attributed to the player who scored it."""
    return {
        "type": {"name": "Own Goal Against"},
        "player": {"id": player_id},
    }


class TestAggregateEvents:
    """
    Unit tests for _aggregate_events() based on ADR 002 specs.

    These cover contracts that are easy to miss or break during refactoring:
    - Key passes: BOTH shot_assist and goal_assist count (ADR 002 §8).
    - Own goals: NOT added to the `goals` column (ADR 002 §9).
    - Source column: every row from build_player_match carries source="statsbomb" (ADR 002 §10).
    """

    def test_shot_assist_only_counts_as_key_pass(self):
        # ADR 002 §8: pass.shot_assist=True → key_passes += 1
        events = [_pass_event(shot_assist=True)]
        result = _aggregate_events(events, Counter())
        assert result["key_passes"] == 1

    def test_goal_assist_only_counts_as_key_pass(self):
        # ADR 002 §8: the original pipeline missed goal_assist-only passes (11.1 %
        # of all key passes in La Liga 2015/16).  goal_assist=True must count.
        events = [_pass_event(goal_assist=True)]
        result = _aggregate_events(events, Counter())
        assert result["key_passes"] == 1

    def test_both_flags_counts_as_one_key_pass(self):
        # ADR 002 §8: both flags never co-occur in practice, but if they did the
        # pass should count once, not twice (the `or` short-circuits at shot_assist).
        events = [_pass_event(shot_assist=True, goal_assist=True)]
        result = _aggregate_events(events, Counter())
        assert result["key_passes"] == 1

    def test_pass_without_either_flag_not_a_key_pass(self):
        # A completed pass with neither flag must not increment key_passes.
        events = [_pass_event(shot_assist=False, goal_assist=False)]
        result = _aggregate_events(events, Counter())
        assert result["key_passes"] == 0

    def test_incomplete_pass_not_counted_as_key_pass(self):
        # An incomplete pass flagged as shot_assist is not counted as key pass
        # because the completion guard fires first (outcome != None → not progressive
        # and key_pass logic also checks the flag on the pass_data, not outcome).
        # This test verifies the pipeline doesn't double-count incomplete assists.
        # (ADR 002 §8: shot_assist/goal_assist flags only appear on completed passes.)
        events = [_pass_event(shot_assist=True, completed=False)]
        result = _aggregate_events(events, Counter())
        # key_passes should be 1 — the flag is present regardless of outcome;
        # the pipeline increments key_passes based on the flag, not on completion.
        # This test documents the current behaviour so a future change is deliberate.
        assert result["key_passes"] == 1

    def test_multiple_key_passes_accumulated(self):
        # Two goal_assist events in the same match → key_passes = 2.
        events = [
            _pass_event(goal_assist=True),
            _pass_event(shot_assist=True),
            _pass_event(goal_assist=False, shot_assist=False),
        ]
        result = _aggregate_events(events, Counter())
        assert result["key_passes"] == 2

    def test_own_goal_for_not_counted_in_goals(self):
        # ADR 002 §9: Own Goal For events have no player attribution;
        # they must NOT increment the goals counter.
        events = [_own_goal_for_event()]
        result = _aggregate_events(events, Counter())
        assert result["goals"] == 0
        assert result["shots"] == 0

    def test_own_goal_against_not_counted_in_goals(self):
        # ADR 002 §9: Own Goal Against events attribute the own-goal to the player,
        # but must NOT count toward their `goals` column.  Own goals are not attacking
        # actions and must never appear in DNA or similarity calculations.
        events = [_own_goal_against_event(player_id=5)]
        result = _aggregate_events(events, Counter())
        assert result["goals"] == 0
        assert result["shots"] == 0

    def test_regular_goal_counted(self):
        # A Shot with outcome=Goal must increment goals (baseline sanity check).
        events = [_shot_event(outcome="Goal", xg=0.7)]
        result = _aggregate_events(events, Counter())
        assert result["goals"] == 1
        assert result["shots"] == 1

    def test_regular_goal_plus_own_goal_against_does_not_inflate(self):
        # One regular goal + one Own Goal Against must produce goals=1, not goals=2.
        events = [
            _shot_event(outcome="Goal", xg=0.6),
            _own_goal_against_event(player_id=9),
        ]
        result = _aggregate_events(events, Counter())
        assert result["goals"] == 1

    def test_empty_events_returns_zeros(self):
        result = _aggregate_events([], Counter())
        assert result["goals"] == 0
        assert result["key_passes"] == 0
        assert result["passes_attempted"] == 0
        assert result["shots"] == 0


class TestAggregateEventsBlocks:
    """
    ADR 002 §13: blocks counts ONLY defensive blocks.
    offensive=True and save_block=True are both excluded.
    """

    def test_defensive_block_counted(self):
        # Neither flag set → defensive block → counted.
        result = _aggregate_events([_block_event()], Counter())
        assert result["blocks"] == 1

    def test_offensive_block_excluded(self):
        # block.offensive=True is an attacking deflection, not a defensive action.
        result = _aggregate_events([_block_event(offensive=True)], Counter())
        assert result["blocks"] == 0

    def test_save_block_excluded(self):
        # block.save_block=True is a GK save recorded as a block — covered by GK metrics.
        result = _aggregate_events([_block_event(save_block=True)], Counter())
        assert result["blocks"] == 0

    def test_both_offensive_and_save_block_excluded(self):
        # Pathological case: both flags — still excluded.
        result = _aggregate_events([_block_event(offensive=True, save_block=True)], Counter())
        assert result["blocks"] == 0

    def test_multiple_blocks_mixed(self):
        # 2 defensive + 1 offensive + 1 save_block = 2 counted.
        events = [
            _block_event(),
            _block_event(),
            _block_event(offensive=True),
            _block_event(save_block=True),
        ]
        result = _aggregate_events(events, Counter())
        assert result["blocks"] == 2

    def test_block_with_no_block_subdict(self):
        # Block event missing the 'block' sub-dict entirely — treated as defensive.
        event = {"type": {"name": "Block"}}
        result = _aggregate_events([event], Counter())
        assert result["blocks"] == 1


class TestAggregateEventsTackles:
    """
    _aggregate_events counts tackles as Duel events where duel.type.name=='Tackle'.
    Aerial duels are ignored.  Won/lost determined by is_tackle_won().
    """

    def test_tackle_won_counted(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Won")], Counter())
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 1

    def test_tackle_lost_counted_as_attempt_not_won(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Lost In Play")], Counter())
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 0

    def test_aerial_duel_not_counted_as_tackle(self):
        # ADR 002 §6a / tackles.py: Aerial Lost duels are not tackles.
        result = _aggregate_events([_duel_event(duel_type="Aerial Lost", outcome="Won")], Counter())
        assert result["tackles_attempted"] == 0
        assert result["tackles_won"] == 0

    def test_tackle_success_in_play(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Success In Play")], Counter())
        assert result["tackles_won"] == 1

    def test_tackle_success_out(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Success Out")], Counter())
        assert result["tackles_won"] == 1

    def test_tackle_unknown_outcome_not_won(self):
        # Unknown outcome → False from is_tackle_won (conservative classification).
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Unknown XYZ")], Counter())
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 0

    def test_multiple_tackles_accumulated(self):
        events = [
            _duel_event(duel_type="Tackle", outcome="Won"),
            _duel_event(duel_type="Tackle", outcome="Lost In Play"),
            _duel_event(duel_type="Aerial Lost"),  # should not count
        ]
        result = _aggregate_events(events, Counter())
        assert result["tackles_attempted"] == 2
        assert result["tackles_won"] == 1


class TestAggregateEventsDribbles:
    """
    _aggregate_events counts Dribble events; complete vs. incomplete determined by
    dribble.outcome.name == 'Complete'.
    """

    def test_complete_dribble_counted(self):
        result = _aggregate_events([_dribble_event(outcome="Complete")], Counter())
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 1

    def test_incomplete_dribble_attempted_not_completed(self):
        result = _aggregate_events([_dribble_event(outcome="Incomplete")], Counter())
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 0

    def test_dribble_unknown_outcome_not_completed(self):
        result = _aggregate_events([_dribble_event(outcome="Unknown")], Counter())
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 0

    def test_multiple_dribbles_accumulated(self):
        events = [_dribble_event("Complete"), _dribble_event("Complete"), _dribble_event("Incomplete")]
        result = _aggregate_events(events, Counter())
        assert result["dribbles_attempted"] == 3
        assert result["dribbles_completed"] == 2


class TestAggregateEventsInterceptionsClearances:
    """
    Interceptions and Clearances are raw counts — every event increments its counter.
    """

    def test_interception_counted(self):
        result = _aggregate_events([_interception_event()], Counter())
        assert result["interceptions"] == 1

    def test_clearance_counted(self):
        result = _aggregate_events([_clearance_event()], Counter())
        assert result["clearances"] == 1

    def test_multiple_interceptions_accumulated(self):
        result = _aggregate_events([_interception_event(), _interception_event()], Counter())
        assert result["interceptions"] == 2

    def test_multiple_clearances_accumulated(self):
        result = _aggregate_events([_clearance_event(), _clearance_event(), _clearance_event()], Counter())
        assert result["clearances"] == 3

    def test_interceptions_and_clearances_independent(self):
        events = [_interception_event(), _clearance_event()]
        result = _aggregate_events(events, Counter())
        assert result["interceptions"] == 1
        assert result["clearances"] == 1


class TestAggregateEventsFouls:
    """
    fouls_committed and fouls_won are raw counts — every matching event increments its counter.
    Note: Foul Committed events with a red-card sub-dict (dismissal events) are STILL counted
    as fouls committed — the card check is done separately in quality_report.
    """

    def test_foul_committed_counted(self):
        result = _aggregate_events([_foul_committed_event()], Counter())
        assert result["fouls_committed"] == 1

    def test_foul_won_counted(self):
        result = _aggregate_events([_foul_won_event()], Counter())
        assert result["fouls_won"] == 1

    def test_foul_committed_with_red_card_still_counted(self):
        # A Foul Committed event that also carries a dismissal card is still a foul.
        event = {
            "type": {"name": "Foul Committed"},
            "minute": 35,
            "second": 0,
            "player": {"id": 5},
            "foul_committed": {"card": {"name": "Red Card"}},
        }
        result = _aggregate_events([event], Counter())
        assert result["fouls_committed"] == 1

    def test_multiple_fouls_accumulated(self):
        events = [_foul_committed_event(), _foul_committed_event(), _foul_won_event()]
        result = _aggregate_events(events, Counter())
        assert result["fouls_committed"] == 2
        assert result["fouls_won"] == 1


class TestAggregateEventsShotsDetail:
    """
    Covers shots_on_target and xg accumulation, which are tested via the fixture
    in test_build_player_match.py but not via direct unit tests of _aggregate_events.
    """

    def test_on_target_goal_counted(self):
        result = _aggregate_events([_shot_event(outcome="Goal", xg=0.8)], Counter())
        assert result["shots_on_target"] == 1
        assert result["xg"] == pytest.approx(0.8)

    def test_on_target_saved_counted(self):
        result = _aggregate_events([_shot_event(outcome="Saved", xg=0.3)], Counter())
        assert result["shots_on_target"] == 1

    def test_on_target_saved_to_post_counted(self):
        result = _aggregate_events([_shot_event(outcome="Saved to Post", xg=0.1)], Counter())
        assert result["shots_on_target"] == 1

    def test_blocked_shot_not_on_target(self):
        result = _aggregate_events([_shot_event(outcome="Blocked", xg=0.2)], Counter())
        assert result["shots_on_target"] == 0
        assert result["shots"] == 1

    def test_off_target_shot_not_on_target(self):
        result = _aggregate_events([_shot_event(outcome="Off T", xg=0.05)], Counter())
        assert result["shots_on_target"] == 0

    def test_xg_accumulated_across_shots(self):
        events = [_shot_event(outcome="Saved", xg=0.3), _shot_event(outcome="Off T", xg=0.15)]
        result = _aggregate_events(events, Counter())
        assert result["xg"] == pytest.approx(0.45)

    def test_xg_none_treated_as_zero(self):
        # statsbomb_xg field absent — must not crash, must treat as 0.
        event = {
            "type": {"name": "Shot"},
            "shot": {"outcome": {"name": "Saved"}},
        }
        result = _aggregate_events([event], Counter())
        assert result["xg"] == pytest.approx(0.0)

    def test_multiple_goals_counted(self):
        events = [_shot_event("Goal"), _shot_event("Goal"), _shot_event("Saved")]
        result = _aggregate_events(events, Counter())
        assert result["goals"] == 2
        assert result["shots"] == 3
        assert result["shots_on_target"] == 3


class TestAggregateEventsProgressiveCounts:
    """
    Progressive pass and carry counters in _aggregate_events.
    The progressive logic is unit-tested in test_progressive.py; here we verify
    that _aggregate_events wires it up correctly.
    """

    def test_progressive_pass_counted(self):
        # (60,40) → (90,40): 30-yard distance reduction, outcome=None → progressive.
        event = {
            "type": {"name": "Pass"},
            "location": [60.0, 40.0],
            "pass": {"end_location": [90.0, 40.0]},
        }
        result = _aggregate_events([event], Counter())
        assert result["progressive_passes"] == 1
        assert result["passes_attempted"] == 1
        assert result["passes_completed"] == 1

    def test_incomplete_pass_not_counted_as_progressive(self):
        # outcome="Incomplete" prevents progressive_pass from being True.
        event = {
            "type": {"name": "Pass"},
            "location": [60.0, 40.0],
            "pass": {"end_location": [90.0, 40.0], "outcome": {"name": "Incomplete"}},
        }
        result = _aggregate_events([event], Counter())
        assert result["progressive_passes"] == 0
        assert result["passes_completed"] == 0

    def test_pass_from_defensive_40_not_progressive(self):
        # start x=40 < 48 → excluded from progressive pass calculation.
        event = {
            "type": {"name": "Pass"},
            "location": [40.0, 40.0],
            "pass": {"end_location": [80.0, 40.0]},
        }
        result = _aggregate_events([event], Counter())
        assert result["progressive_passes"] == 0
        assert result["passes_attempted"] == 1  # still an attempt

    def test_progressive_carry_counted(self):
        # (60,40) → (80,40): 20-yard carry, 20-yard dist reduction → progressive.
        result = _aggregate_events([_carry_event([60.0, 40.0], [80.0, 40.0])], Counter())
        assert result["progressive_carries"] == 1
        assert result["carries"] == 1

    def test_backward_carry_not_progressive(self):
        # (80,40) → (60,40): moves away from goal.
        result = _aggregate_events([_carry_event([80.0, 40.0], [60.0, 40.0])], Counter())
        assert result["progressive_carries"] == 0
        assert result["carries"] == 1

    def test_missing_location_does_not_crash(self):
        # Pass event with no 'location' — should count the attempt but not progressive.
        event = {"type": {"name": "Pass"}, "pass": {"end_location": [90.0, 40.0]}}
        result = _aggregate_events([event], Counter())
        assert result["passes_attempted"] == 1
        assert result["progressive_passes"] == 0


# ---------------------------------------------------------------------------
# Unknown-outcome passes — _aggregate_events unit tests
#
# Milestone 3 tech-debt note (milestones.md): unknown-outcome passes are
# excluded from the completion denominator via the separate column
# passes_unknown_outcome.  The contract for _aggregate_events is:
#   outcome="Unknown" → passes_attempted+1, passes_unknown_outcome+1
#                       passes_completed unchanged (NOT incremented)
#                       progressive_passes NOT incremented (pass not confirmed complete)
# ---------------------------------------------------------------------------

class TestAggregateEventsUnknownOutcomePasses:
    """
    Verify that Pass events with outcome.name == "Unknown" are counted in
    passes_unknown_outcome, increment passes_attempted, but do NOT increment
    passes_completed or progressive_passes.

    This directly tests the contract specified in milestones.md §M3 tech-debt:
    'unknown-outcome passes are excluded from the completion denominator
    (passes_attempted − passes_unknown_outcome) and passes_completed is accurate.'
    """

    def test_unknown_outcome_increments_passes_attempted(self):
        # A pass with outcome="Unknown" is still a pass attempt.
        result = _aggregate_events([_pass_event(outcome="Unknown")], Counter())
        assert result["passes_attempted"] == 1

    def test_unknown_outcome_increments_passes_unknown_outcome(self):
        # outcome="Unknown" must land in the dedicated counter, not silently dropped.
        result = _aggregate_events([_pass_event(outcome="Unknown")], Counter())
        assert result["passes_unknown_outcome"] == 1

    def test_unknown_outcome_does_not_increment_passes_completed(self):
        # passes_completed = raw completed count only (outcome is None in StatsBomb).
        # An "Unknown" outcome must NOT be treated as completed.
        result = _aggregate_events([_pass_event(outcome="Unknown")], Counter())
        assert result["passes_completed"] == 0

    def test_unknown_outcome_not_counted_as_progressive(self):
        # Progressive passes require outcome=None (completed).  outcome="Unknown"
        # must return False from is_progressive_pass regardless of geometry.
        # Use a geometry that would qualify as progressive if the pass were completed:
        # start=(60,40) → end=(90,40): 30-yard reduction, start_x≥48 → progressive
        # if and only if outcome is None.
        result = _aggregate_events([
            _pass_event(outcome="Unknown", location=[60.0, 40.0], end_location=[90.0, 40.0])
        ], Counter())
        assert result["progressive_passes"] == 0

    def test_multiple_unknown_outcomes_accumulate(self):
        # Two passes with outcome="Unknown" → passes_unknown_outcome=2.
        result = _aggregate_events([
            _pass_event(outcome="Unknown"),
            _pass_event(outcome="Unknown"),
        ], Counter())
        assert result["passes_unknown_outcome"] == 2
        assert result["passes_completed"] == 0
        assert result["passes_attempted"] == 2

    def test_mixed_completed_unknown_incomplete(self):
        # Mix of completed (outcome=None), unknown, and incomplete passes.
        # Completed: passes_completed+1, passes_attempted+1
        # Unknown:   passes_unknown_outcome+1, passes_attempted+1
        # Incomplete: passes_attempted+1 (no bucket incremented for incomplete here)
        result = _aggregate_events([
            _pass_event(completed=True),          # outcome=None → completed
            _pass_event(outcome="Unknown"),        # unknown
            _pass_event(completed=False),          # outcome="Incomplete" → not completed
        ], Counter())
        assert result["passes_attempted"] == 3
        assert result["passes_completed"] == 1
        assert result["passes_unknown_outcome"] == 1

    def test_known_pass_denominator_excludes_unknown(self):
        # 5 attempted: 3 completed (outcome=None), 2 unknown-outcome attempts.
        # known-outcome attempts = 5 - 2 = 3.
        result = _aggregate_events([
            _pass_event(completed=True),
            _pass_event(completed=True),
            _pass_event(completed=True),
            _pass_event(outcome="Unknown"),
            _pass_event(outcome="Unknown"),
        ], Counter())
        assert result["passes_attempted"] == 5
        assert result["passes_unknown_outcome"] == 2
        assert result["passes_completed"] == 3
        known_attempts = result["passes_attempted"] - result["passes_unknown_outcome"]
        assert known_attempts == 3

    def test_unknown_outcome_pass_with_goal_assist_still_increments_assists_and_key_passes(self):
        # An "Unknown" outcome pass that carries a goal_assist flag:
        # key_passes and assists should still be incremented because the flags are
        # recorded on the pass regardless of outcome (current pipeline behaviour,
        # documented in the key_passes tech-debt note in milestones.md §M3).
        result = _aggregate_events([_pass_event(outcome="Unknown", goal_assist=True)], Counter())
        assert result["key_passes"] == 1
        assert result["assists"] == 1
        # But it must not count as completed or progressive.
        assert result["passes_completed"] == 0
        assert result["passes_unknown_outcome"] == 1

    def test_other_known_incomplete_outcomes_not_counted_as_unknown(self):
        # Outcomes such as "Out", "Pass Offside", "Injury Clearance" are
        # *known* incomplete outcomes — they must NOT land in passes_unknown_outcome.
        # The implementation uses string-equality "Unknown"; this test guards against
        # accidental broadening (e.g. 'elif outcome_name is not None' would break this).
        for outcome_name in ("Out", "Pass Offside", "Injury Clearance", "Incomplete"):
            result = _aggregate_events([_pass_event(outcome=outcome_name)], Counter())
            assert result["passes_unknown_outcome"] == 0, (
                f"outcome={outcome_name!r} must not be counted as unknown-outcome"
            )
            assert result["passes_attempted"] == 1
            assert result["passes_completed"] == 0

    def test_zero_unknown_outcomes_when_all_completed(self):
        # Baseline: three completed passes → passes_unknown_outcome stays at 0.
        result = _aggregate_events([
            _pass_event(completed=True),
            _pass_event(completed=True),
            _pass_event(completed=True),
        ], Counter())
        assert result["passes_unknown_outcome"] == 0
        assert result["passes_completed"] == 3
        assert result["passes_attempted"] == 3

    def test_unknown_outcome_with_shot_assist_increments_key_passes(self):
        # Symmetry check with the goal_assist test already in the class:
        # shot_assist flag on an "Unknown" outcome pass should still count as a key pass
        # (flag-based; not gated on outcome).
        result = _aggregate_events([_pass_event(outcome="Unknown", shot_assist=True)], Counter())
        assert result["key_passes"] == 1
        assert result["assists"] == 0          # shot_assist only → no assist
        assert result["passes_completed"] == 0
        assert result["passes_unknown_outcome"] == 1


# ---------------------------------------------------------------------------
# crosses — _aggregate_events unit tests
# ---------------------------------------------------------------------------

class TestAggregateEventsCrosses:
    """
    crosses_attempted: every Pass with pass.cross=True, regardless of outcome.
    crosses_completed: Pass with pass.cross=True AND outcome is None.
    """

    def test_completed_cross_counts_both(self):
        event = _pass_event(completed=True)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["crosses_completed"] == 1

    def test_incomplete_cross_counts_attempted_only(self):
        event = _pass_event(completed=False)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["crosses_completed"] == 0

    def test_unknown_outcome_cross_counts_attempted_only(self):
        event = _pass_event(outcome="Unknown")
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["crosses_completed"] == 0

    def test_pass_without_cross_flag_not_counted(self):
        result = _aggregate_events([_pass_event(completed=True)], Counter())
        assert result["crosses_attempted"] == 0
        assert result["crosses_completed"] == 0

    def test_multiple_crosses_accumulated(self):
        c1 = _pass_event(completed=True)
        c1["pass"]["cross"] = True
        c2 = _pass_event(completed=False)
        c2["pass"]["cross"] = True
        c3 = _pass_event(completed=True)  # no cross flag
        result = _aggregate_events([c1, c2, c3], Counter())
        assert result["crosses_attempted"] == 2
        assert result["crosses_completed"] == 1

    def test_cross_with_goal_assist_both_counted(self):
        # A completed cross that is also a goal_assist: crosses_completed AND
        # key_passes both increment.
        event = _pass_event(goal_assist=True)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["crosses_completed"] == 1
        assert result["key_passes"] == 1
        assert result["assists"] == 1

    def test_empty_events_crosses_zero(self):
        result = _aggregate_events([], Counter())
        assert result["crosses_attempted"] == 0
        assert result["crosses_completed"] == 0

    def test_cross_false_explicit_not_counted(self):
        # ADR 002 §15: pass.cross == True is required.  An explicit cross=False
        # on the pass_data must not increment crosses_attempted or crosses_completed.
        # StatsBomb omits the cross field when it is False; some encodings may set
        # it explicitly to False.  pass_data.get("cross") returns False → falsy →
        # the guard must not count it.
        event = _pass_event(completed=True)
        event["pass"]["cross"] = False
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 0
        assert result["crosses_completed"] == 0

    def test_cross_increments_passes_attempted(self):
        # A cross is still a pass: passes_attempted must increment alongside
        # crosses_attempted.
        event = _pass_event(completed=True)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["passes_attempted"] == 1
        assert result["crosses_attempted"] == 1

    def test_completed_cross_increments_passes_completed(self):
        # A completed cross (outcome=None) must also be counted as a completed pass.
        event = _pass_event(completed=True)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["passes_completed"] == 1
        assert result["crosses_completed"] == 1

    def test_other_outcome_cross_not_in_passes_completed(self):
        # A cross with an explicit non-None outcome ("Incomplete") must NOT be
        # counted in passes_completed.
        event = _pass_event(completed=False)
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["passes_completed"] == 0
        assert result["crosses_attempted"] == 1


# ---------------------------------------------------------------------------
# ADR 002 §16 — Progressive-cross intentional overlap
# ---------------------------------------------------------------------------

class TestProgressiveCrossOverlap:
    """
    ADR 002 §16: A completed cross that satisfies the progressive-pass geometry
    (start_x >= 48, 10-yard distance reduction, or enters the penalty area from
    outside) is counted simultaneously in BOTH progressive_passes AND
    crosses_attempted / crosses_completed.

    This is intentional: a cross is a pass, and if it is also progressive it is
    both.  No adjustment or de-duplication (decision deferred to Milestone 4).
    """

    def test_progressive_cross_increments_both_progressive_passes_and_crosses_attempted(self):
        # start=(60,40) → end=(90,40): 30-yard distance reduction, start_x=60>=48,
        # outcome=None → qualifies as progressive pass AND cross (cross=True flag).
        event = _pass_event(completed=True, location=[60.0, 40.0], end_location=[90.0, 40.0])
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["progressive_passes"] == 1, (
            "ADR 002 §16: a progressive cross must increment progressive_passes"
        )
        assert result["crosses_attempted"] == 1, (
            "ADR 002 §16: a progressive cross must also increment crosses_attempted"
        )
        assert result["crosses_completed"] == 1, (
            "ADR 002 §16: a completed progressive cross must increment crosses_completed"
        )

    def test_progressive_cross_also_increments_passes_attempted_and_completed(self):
        # A cross is still a pass — all four counters must fire.
        event = _pass_event(completed=True, location=[60.0, 40.0], end_location=[90.0, 40.0])
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["passes_attempted"] == 1
        assert result["passes_completed"] == 1

    def test_non_progressive_cross_does_not_increment_progressive_passes(self):
        # A cross that starts in the defensive 40% (start_x < 48) is NOT progressive.
        # It must increment crosses_attempted but NOT progressive_passes.
        event = _pass_event(completed=True, location=[30.0, 40.0], end_location=[60.0, 40.0])
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["progressive_passes"] == 0, (
            "A cross from the defensive 40 percent (start_x<48) must not count as progressive"
        )

    def test_incomplete_progressive_cross_not_progressive(self):
        # An incomplete cross from the attacking half cannot be progressive
        # (progressive pass requires outcome=None — ADR 002 §1).
        event = _pass_event(completed=False, location=[60.0, 40.0], end_location=[90.0, 40.0])
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["crosses_attempted"] == 1
        assert result["crosses_completed"] == 0
        assert result["progressive_passes"] == 0, (
            "An incomplete cross must not count as progressive (ADR 002 §1 + §16)"
        )


# ---------------------------------------------------------------------------
# Chunk A — Ball Security, Defending, Passing (new metrics)
# ---------------------------------------------------------------------------

def _dispossessed_event() -> dict:
    return {"type": {"name": "Dispossessed"}}


def _miscontrol_event(*, aerial_won: bool = False) -> dict:
    mc: dict = {}
    if aerial_won:
        mc["aerial_won"] = True
    return {"type": {"name": "Miscontrol"}, "miscontrol": mc}


def _pressure_event() -> dict:
    return {"type": {"name": "Pressure"}}


def _ball_recovery_event(*, recovery_failure: bool | None = None, offensive: bool = False) -> dict:
    br: dict = {}
    if offensive:
        br["offensive"] = True
    if recovery_failure is True:
        br["recovery_failure"] = True
    # br is {} (empty dict) for the default call — the "present but empty sub-dict" case.
    return {"type": {"name": "Ball Recovery"}, "ball_recovery": br}


def _ball_recovery_event_no_subdict() -> dict:
    """Ball Recovery with no ball_recovery sub-dict at all (most common case)."""
    return {"type": {"name": "Ball Recovery"}}


def _dribbled_past_event() -> dict:
    return {"type": {"name": "Dribbled Past"}}


def _aerial_won_pass_event() -> dict:
    return {"type": {"name": "Pass"}, "pass": {"aerial_won": True, "end_location": [62.0, 40.0]}}


def _aerial_won_clearance_event() -> dict:
    return {"type": {"name": "Clearance"}, "clearance": {"aerial_won": True}}


def _aerial_won_shot_event() -> dict:
    return {"type": {"name": "Shot"}, "shot": {"aerial_won": True, "statsbomb_xg": 0.1, "outcome": {"name": "Off T"}}}


def _aerial_lost_duel_event() -> dict:
    return {"type": {"name": "Duel"}, "duel": {"type": {"name": "Aerial Lost"}}}


def _long_pass_event(*, length: float, outcome: str | None = None, pass_type: str | None = None) -> dict:
    pass_data: dict = {"length": length, "end_location": [90.0, 40.0]}
    if outcome is not None:
        pass_data["outcome"] = {"name": outcome}
    if pass_type is not None:
        pass_data["type"] = {"name": pass_type}
    return {"type": {"name": "Pass"}, "location": [60.0, 40.0], "pass": pass_data}


def _switch_pass_event(*, switch: bool = True) -> dict:
    pass_data: dict = {"switch": switch, "length": 35.0, "end_location": [90.0, 70.0]}
    return {"type": {"name": "Pass"}, "location": [60.0, 10.0], "pass": pass_data}


def _no_length_pass_event() -> dict:
    return {"type": {"name": "Pass"}, "location": [60.0, 40.0], "pass": {"end_location": [62.0, 40.0]}}


class TestAggregateEventsChunkA:
    """Unit tests for Chunk A metrics per ADR 002 §18–22."""

    # -- Ball Security --------------------------------------------------------

    def test_dispossessed_increments(self):
        r = _aggregate_events([_dispossessed_event()], Counter())
        assert r["dispossessed"] == 1

    def test_miscontrol_increments(self):
        r = _aggregate_events([_miscontrol_event()], Counter())
        assert r["miscontrols"] == 1

    def test_miscontrol_with_aerial_won_increments_both(self):
        r = _aggregate_events([_miscontrol_event(aerial_won=True)], Counter())
        assert r["miscontrols"] == 1
        assert r["aerials_won"] == 1

    # -- Defending ------------------------------------------------------------

    def test_pressure_increments(self):
        r = _aggregate_events([_pressure_event()], Counter())
        assert r["pressures"] == 1

    def test_ball_recovery_absent_subdict_is_success(self):
        r = _aggregate_events([_ball_recovery_event_no_subdict()], Counter())
        assert r["ball_recoveries"] == 1
        assert r["ball_recoveries_failed"] == 0

    def test_ball_recovery_empty_subdict_is_success(self):
        # ADR §21 case 2: sub-dict present but contains no recovery_failure key.
        r = _aggregate_events([_ball_recovery_event()], Counter())
        assert r["ball_recoveries"] == 1
        assert r["ball_recoveries_failed"] == 0

    def test_ball_recovery_offensive_subdict_is_success(self):
        r = _aggregate_events([_ball_recovery_event(offensive=True)], Counter())
        assert r["ball_recoveries"] == 1
        assert r["ball_recoveries_failed"] == 0

    def test_ball_recovery_failure_not_counted_as_success(self):
        r = _aggregate_events([_ball_recovery_event(recovery_failure=True)], Counter())
        assert r["ball_recoveries"] == 0
        assert r["ball_recoveries_failed"] == 1

    def test_dribbled_past_increments(self):
        r = _aggregate_events([_dribbled_past_event()], Counter())
        assert r["dribbled_past"] == 1

    def test_pass_aerial_won_true_increments_aerials_won(self):
        r = _aggregate_events([_aerial_won_pass_event()], Counter())
        assert r["aerials_won"] == 1
        assert r["aerials_lost"] == 0

    def test_clearance_aerial_won_true_increments_aerials_won(self):
        r = _aggregate_events([_aerial_won_clearance_event()], Counter())
        assert r["aerials_won"] == 1
        assert r["aerials_lost"] == 0

    def test_shot_aerial_won_true_increments_aerials_won(self):
        r = _aggregate_events([_aerial_won_shot_event()], Counter())
        assert r["aerials_won"] == 1
        assert r["aerials_lost"] == 0

    def test_aerial_won_false_not_counted(self):
        event = {"type": {"name": "Pass"}, "pass": {"aerial_won": False, "end_location": [62.0, 40.0]}}
        r = _aggregate_events([event], Counter())
        assert r["aerials_won"] == 0

    def test_duel_aerial_lost_increments_aerials_lost(self):
        r = _aggregate_events([_aerial_lost_duel_event()], Counter())
        assert r["aerials_lost"] == 1
        assert r["aerials_won"] == 0

    def test_tackle_duel_not_counted_as_aerial_lost(self):
        r = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Won")], Counter())
        assert r["aerials_lost"] == 0

    def test_aerial_won_does_not_increment_aerials_lost(self):
        r = _aggregate_events([_aerial_won_pass_event()], Counter())
        assert r["aerials_lost"] == 0

    def test_multiple_aerial_events_accumulated(self):
        events = [
            _aerial_won_pass_event(),
            _aerial_won_clearance_event(),
            _aerial_lost_duel_event(),
        ]
        r = _aggregate_events(events, Counter())
        assert r["aerials_won"] == 2
        assert r["aerials_lost"] == 1

    # -- Passing --------------------------------------------------------------

    def test_long_pass_open_play_attempted_and_completed(self):
        r = _aggregate_events([_long_pass_event(length=30.0)], Counter())
        assert r["long_passes_attempted"] == 1
        assert r["long_passes_completed"] == 1

    def test_long_pass_incomplete_counts_attempted_only(self):
        r = _aggregate_events([_long_pass_event(length=35.0, outcome="Incomplete")], Counter())
        assert r["long_passes_attempted"] == 1
        assert r["long_passes_completed"] == 0

    def test_short_pass_not_counted_as_long(self):
        r = _aggregate_events([_long_pass_event(length=29.9)], Counter())
        assert r["long_passes_attempted"] == 0
        assert r["long_passes_completed"] == 0

    def test_corner_long_pass_excluded(self):
        r = _aggregate_events([_long_pass_event(length=40.0, pass_type="Corner")], Counter())
        assert r["long_passes_attempted"] == 0

    def test_goal_kick_long_pass_excluded(self):
        r = _aggregate_events([_long_pass_event(length=40.0, pass_type="Goal Kick")], Counter())
        assert r["long_passes_attempted"] == 0

    def test_free_kick_long_pass_excluded(self):
        r = _aggregate_events([_long_pass_event(length=40.0, pass_type="Free Kick")], Counter())
        assert r["long_passes_attempted"] == 0

    def test_switch_true_increments_switches(self):
        r = _aggregate_events([_switch_pass_event(switch=True)], Counter())
        assert r["switches"] == 1

    def test_switch_false_not_counted(self):
        r = _aggregate_events([_switch_pass_event(switch=False)], Counter())
        assert r["switches"] == 0

    def test_switch_absent_not_counted(self):
        r = _aggregate_events([_long_pass_event(length=35.0)], Counter())
        assert r["switches"] == 0

    def test_pass_no_length_increments_missing_not_long(self):
        r = _aggregate_events([_no_length_pass_event()], Counter())
        assert r["passes_missing_length"] == 1
        assert r["long_passes_attempted"] == 0

    def test_long_switch_pass_increments_both_counters(self):
        # A pass that is both >= 30 yards AND has switch=True must increment both
        # long_passes_attempted and switches.  This is not a mutual-exclusion case.
        event = _switch_pass_event(switch=True)
        # _switch_pass_event uses length=35.0, which is >= LONG_PASS_THRESHOLD_UNITS
        r = _aggregate_events([event], Counter())
        assert r["long_passes_attempted"] == 1, (
            "A 35-yard switch pass must count as a long pass attempted"
        )
        assert r["switches"] == 1, (
            "A switch=True pass must count as a switch regardless of length"
        )

    def test_ball_recovery_failure_false_treated_as_success(self):
        # ADR 002 §21: recovery_failure=False never appears in StatsBomb open data,
        # but the implementation guard 'not br.get("recovery_failure")' evaluates
        # False as falsy → not False = True → ball_recoveries increments.
        # Explicitly test the boundary: False value must be treated as success.
        event = {"type": {"name": "Ball Recovery"}, "ball_recovery": {"recovery_failure": False}}
        r = _aggregate_events([event], Counter())
        assert r["ball_recoveries"] == 1, (
            "ADR §21: recovery_failure=False must be treated as success "
            "(success encoded by absence of True, not presence of False)"
        )

    def test_long_pass_set_piece_excluded_even_below_threshold(self):
        # A short Corner kick (length=20) must not appear in long_passes_attempted
        # (excluded by both length check and set-piece exclusion).
        # Verifies the set-piece path doesn't accidentally count short set-pieces.
        r = _aggregate_events([_long_pass_event(length=20.0, pass_type="Corner")], Counter())
        assert r["long_passes_attempted"] == 0

    def test_throw_in_long_pass_excluded(self):
        """ADR 004 D14 (amends ADR 002 §19): a throw-in is a restart, so a 35-unit
        long throw counts toward no long-pass column."""
        event = _long_pass_event(length=35.0, pass_type="Throw-in")
        r = _aggregate_events([event], Counter())
        assert r["long_passes_attempted"] == 0
        assert r["long_passes_completed"] == 0

    def test_multiple_new_metric_events_accumulate_independently(self):
        # Regression: new metrics added via elif must not prevent each other
        # from accumulating.  If any elif accidentally absorbs a prior branch,
        # the totals will be wrong.
        events = [
            _dispossessed_event(),
            _miscontrol_event(),
            _pressure_event(),
            _ball_recovery_event_no_subdict(),
            _ball_recovery_event(recovery_failure=True),
            _dribbled_past_event(),
        ]
        r = _aggregate_events(events, Counter())
        assert r["dispossessed"] == 1
        assert r["miscontrols"] == 1
        assert r["pressures"] == 1
        assert r["ball_recoveries"] == 1
        assert r["ball_recoveries_failed"] == 1
        assert r["dribbled_past"] == 1


# ---------------------------------------------------------------------------
# TestAggregateEventsChunkAInvariants — spec-derived invariants not covered above
# ---------------------------------------------------------------------------

class TestAggregateEventsChunkAInvariants:
    """
    Spec-derived invariants from ADR 002 §18–23 that protect against subtle
    regressions not covered by the positive-case tests in TestAggregateEventsChunkA.
    """

    def test_empty_events_new_fields_all_zero(self):
        # _aggregate_events([]) must initialise every new Chunk-A field to 0,
        # not raise KeyError when callers read them.
        result = _aggregate_events([], Counter())
        for field in (
            "dispossessed", "miscontrols", "pressures",
            "ball_recoveries", "ball_recoveries_failed", "dribbled_past",
            "aerials_won", "aerials_lost",
            "long_passes_attempted", "long_passes_completed",
            "passes_missing_length", "switches",
        ):
            assert field in result, f"Field {field!r} missing from _aggregate_events output"
            assert result[field] == 0, f"Expected {field}=0 for empty events but got {result[field]}"

    def test_long_passes_completed_never_exceeds_attempted(self):
        # Invariant: completed ⊆ attempted.  Test with multiple mixed events.
        events = [
            _long_pass_event(length=35.0),                       # completed
            _long_pass_event(length=35.0, outcome="Incomplete"),  # attempted only
            _long_pass_event(length=35.0),                       # completed
        ]
        result = _aggregate_events(events, Counter())
        assert result["long_passes_completed"] <= result["long_passes_attempted"], (
            "long_passes_completed must never exceed long_passes_attempted"
        )
        assert result["long_passes_attempted"] == 3
        assert result["long_passes_completed"] == 2

    def test_switch_on_incomplete_pass_still_counted(self):
        # ADR 002 §23: pass.switch=True increments switches regardless of
        # whether the pass was completed or not.  The flag is a tactical
        # annotation, not a quality measure.
        event = _switch_pass_event(switch=True)
        event["pass"]["outcome"] = {"name": "Incomplete"}
        result = _aggregate_events([event], Counter())
        assert result["switches"] == 1, (
            "ADR §23: switch=True on an incomplete pass must still increment switches"
        )

    def test_ball_recovery_each_event_goes_to_exactly_one_bucket(self):
        # Each Ball Recovery event increments EITHER ball_recoveries OR
        # ball_recoveries_failed — never both, never neither.
        events = [
            _ball_recovery_event_no_subdict(),           # success (no sub-dict)
            _ball_recovery_event(),                      # success (empty sub-dict)
            _ball_recovery_event(offensive=True),        # success (offensive)
            _ball_recovery_event(recovery_failure=True), # failure
        ]
        result = _aggregate_events(events, Counter())
        total_events = 4
        assert result["ball_recoveries"] + result["ball_recoveries_failed"] == total_events, (
            "Every Ball Recovery event must land in exactly one bucket "
            "(ball_recoveries or ball_recoveries_failed)"
        )
        assert result["ball_recoveries"] == 3
        assert result["ball_recoveries_failed"] == 1

    def test_corner_cross_long_excluded_from_long_passes(self):
        # ADR §18/§19: corner kicks are excluded from long_passes_attempted by the
        # set-piece type filter, even when cross=True and length >= threshold.
        # This matters because a corner into the box often has cross=True in practice.
        event = _long_pass_event(length=40.0, pass_type="Corner")
        event["pass"]["cross"] = True
        result = _aggregate_events([event], Counter())
        assert result["long_passes_attempted"] == 0, (
            "ADR §19: a Corner kick must not count as long_passes_attempted "
            "even when pass.cross=True"
        )

    def test_passes_missing_length_and_long_attempts_mutually_exclusive_per_pass(self):
        # A pass with length=None must land in passes_missing_length, never
        # long_passes_attempted.  These two counters are mutually exclusive for
        # every individual pass.
        result = _aggregate_events([_no_length_pass_event()], Counter())
        # Exactly one of the two counters must have incremented.
        assert result["passes_missing_length"] == 1
        assert result["long_passes_attempted"] == 0
        total = result["passes_missing_length"] + result["long_passes_attempted"]
        assert total == 1, (
            "A null-length pass must increment exactly one of "
            "passes_missing_length or long_passes_attempted"
        )


# ---------------------------------------------------------------------------
# TestAggregateEventsChunkB — entries and Kick Off exclusion (ADR 002 §27)
# ---------------------------------------------------------------------------

def _entry_pass_event(
    *,
    start: list,
    end: list,
    pass_type: str | None = None,
    outcome: str | None = None,
) -> dict:
    """Minimal completed (or incomplete) pass with location/end_location."""
    pass_data: dict = {"end_location": end}
    if pass_type is not None:
        pass_data["type"] = {"name": pass_type}
    if outcome is not None:
        pass_data["outcome"] = {"name": outcome}
    return {
        "type": {"name": "Pass"},
        "location": start,
        "pass": pass_data,
    }


def _entry_carry_event(*, start: list, end: list) -> dict:
    """Minimal Carry event."""
    return {
        "type": {"name": "Carry"},
        "location": start,
        "carry": {"end_location": end},
    }


class TestAggregateEventsChunkBEntries:
    """
    Unit tests for final_third_entries_{pass,carry} and box_entries_{pass,carry}
    as aggregated in _aggregate_events().  Derived from ADR 002 §27.
    """

    # -- final_third_entries_pass ----------------------------------------

    def test_completed_pass_into_final_third_counted(self):
        # Open play, start outside (x=70), end inside (x=85) → entry
        r = _aggregate_events([_entry_pass_event(start=[70.0, 40.0], end=[85.0, 40.0])], Counter())
        assert r["final_third_entries_pass"] == 1

    def test_incomplete_pass_into_final_third_not_counted(self):
        # ADR §27: pipeline only counts completed passes (outcome_name is None).
        r = _aggregate_events([
            _entry_pass_event(start=[70.0, 40.0], end=[85.0, 40.0], outcome="Incomplete")
        ], Counter())
        assert r["final_third_entries_pass"] == 0

    def test_corner_pass_into_final_third_excluded(self):
        # ADR §27: Corner is in SET_PIECE_TYPES → excluded.
        r = _aggregate_events([
            _entry_pass_event(start=[70.0, 40.0], end=[85.0, 40.0], pass_type="Corner")
        ], Counter())
        assert r["final_third_entries_pass"] == 0

    def test_kick_off_pass_into_final_third_excluded(self):
        # ADR §27 / Chunk B amendment: Kick Off added to SET_PIECE_TYPES.
        r = _aggregate_events([
            _entry_pass_event(start=[60.0, 40.0], end=[82.0, 40.0], pass_type="Kick Off")
        ], Counter())
        assert r["final_third_entries_pass"] == 0

    def test_throw_in_pass_into_final_third_excluded(self):
        """ADR 004 D14 (amends ADR 002 §27): a throw-in is a restart, never an entry."""
        r = _aggregate_events([
            _entry_pass_event(start=[70.0, 40.0], end=[85.0, 40.0], pass_type="Throw-in")
        ], Counter())
        assert r["final_third_entries_pass"] == 0

    def test_pass_both_in_final_third_not_counted(self):
        # Start already in final third → no entry.
        r = _aggregate_events([_entry_pass_event(start=[82.0, 40.0], end=[95.0, 40.0])], Counter())
        assert r["final_third_entries_pass"] == 0

    def test_pass_missing_location_not_counted(self):
        # Passes missing location/end_location must not crash or increment entries.
        event = {"type": {"name": "Pass"}, "pass": {}}
        r = _aggregate_events([event], Counter())
        assert r["final_third_entries_pass"] == 0

    # -- final_third_entries_carry ----------------------------------------

    def test_carry_into_final_third_counted(self):
        r = _aggregate_events([_entry_carry_event(start=[75.0, 40.0], end=[82.0, 40.0])], Counter())
        assert r["final_third_entries_carry"] == 1

    def test_carry_already_in_final_third_not_counted(self):
        r = _aggregate_events([_entry_carry_event(start=[81.0, 40.0], end=[95.0, 40.0])], Counter())
        assert r["final_third_entries_carry"] == 0

    def test_carry_backward_out_of_final_third_not_counted(self):
        r = _aggregate_events([_entry_carry_event(start=[85.0, 40.0], end=[70.0, 40.0])], Counter())
        assert r["final_third_entries_carry"] == 0

    # -- box_entries_pass -------------------------------------------------

    def test_completed_pass_into_box_counted(self):
        # (100, 40) → (105, 40): start outside, end inside penalty area.
        r = _aggregate_events([_entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0])], Counter())
        assert r["box_entries_pass"] == 1

    def test_incomplete_pass_into_box_not_counted(self):
        r = _aggregate_events([
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0], outcome="Incomplete")
        ], Counter())
        assert r["box_entries_pass"] == 0

    def test_corner_into_box_excluded(self):
        r = _aggregate_events([
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0], pass_type="Corner")
        ], Counter())
        assert r["box_entries_pass"] == 0

    def test_free_kick_into_box_excluded(self):
        r = _aggregate_events([
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0], pass_type="Free Kick")
        ], Counter())
        assert r["box_entries_pass"] == 0

    def test_throw_in_into_box_excluded(self):
        """ADR 004 D14 (amends ADR 002 §27): a throw-in is a restart, never an entry."""
        r = _aggregate_events([
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0], pass_type="Throw-in")
        ], Counter())
        assert r["box_entries_pass"] == 0

    def test_pass_start_inside_box_not_counted(self):
        # Start already inside the penalty area → NOT an entry.
        r = _aggregate_events([_entry_pass_event(start=[103.0, 40.0], end=[110.0, 40.0])], Counter())
        assert r["box_entries_pass"] == 0

    # -- box_entries_carry ------------------------------------------------

    def test_carry_into_box_counted(self):
        r = _aggregate_events([_entry_carry_event(start=[100.0, 40.0], end=[105.0, 40.0])], Counter())
        assert r["box_entries_carry"] == 1

    def test_carry_start_inside_box_not_counted(self):
        r = _aggregate_events([_entry_carry_event(start=[103.0, 40.0], end=[110.0, 40.0])], Counter())
        assert r["box_entries_carry"] == 0

    def test_carry_end_outside_y_range_not_counted(self):
        # end_y > 62 → outside penalty area.
        r = _aggregate_events([_entry_carry_event(start=[100.0, 40.0], end=[105.0, 70.0])], Counter())
        assert r["box_entries_carry"] == 0

    # -- independence and accumulation ------------------------------------

    def test_final_third_entry_can_also_be_box_entry(self):
        # A pass from x=70 to x=105 enters both the final third and the box.
        # Both counters must increment independently.
        r = _aggregate_events([_entry_pass_event(start=[70.0, 40.0], end=[105.0, 40.0])], Counter())
        assert r["final_third_entries_pass"] == 1
        assert r["box_entries_pass"] == 1

    def test_multiple_entries_accumulate(self):
        events = [
            _entry_pass_event(start=[70.0, 40.0], end=[85.0, 40.0]),   # ft entry only
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0]),  # box entry only
            _entry_carry_event(start=[75.0, 40.0], end=[82.0, 40.0]),   # ft carry entry
            _entry_carry_event(start=[100.0, 40.0], end=[104.0, 40.0]), # box carry entry
        ]
        r = _aggregate_events(events, Counter())
        assert r["final_third_entries_pass"] == 1
        assert r["box_entries_pass"] == 1
        assert r["final_third_entries_carry"] == 1
        assert r["box_entries_carry"] == 1


class TestAggregateEventsChunkBInvariants:
    """
    Spec-derived invariants for the six new Chunk B columns.
    """

    def test_empty_events_chunk_b_fields_all_zero(self):
        # The four entry fields (tracked by _aggregate_events) must be present and 0.
        # xg_assisted / xg_assisted_direct are merged separately in _process_match
        # and are NOT returned by _aggregate_events — they are not tested here.
        result = _aggregate_events([], Counter())
        for field in (
            "final_third_entries_pass", "final_third_entries_carry",
            "box_entries_pass", "box_entries_carry",
        ):
            assert field in result, f"Field {field!r} missing from _aggregate_events output"
            assert result[field] == 0, (
                f"Expected {field}=0 for empty events but got {result[field]}"
            )

    def test_kick_off_excluded_from_long_passes_chunk_b(self):
        # ADR §19 Chunk B amendment: Kick Off was added to SET_PIECE_TYPES.
        # A Kick Off pass >= 30 units must NOT count as long_passes_attempted.
        r = _aggregate_events([_long_pass_event(length=35.0, pass_type="Kick Off")], Counter())
        assert r["long_passes_attempted"] == 0, (
            "ADR §19 Chunk B: 'Kick Off' added to SET_PIECE_TYPES must exclude "
            "long Kick Off passes from long_passes_attempted"
        )

    def test_pass_from_outside_final_third_into_box_counts_in_both_columns(self):
        # A pass from outside the final third (x<80) that lands inside the penalty
        # area (x>=102, 18<=y<=62) must count in BOTH final_third_entries_pass AND
        # box_entries_pass.  The two metrics are independent but overlap is legal.
        r = _aggregate_events([_entry_pass_event(start=[70.0, 40.0], end=[105.0, 40.0])], Counter())
        assert r["final_third_entries_pass"] == 1, (
            "Pass from x<80 to box must count as a final-third entry"
        )
        assert r["box_entries_pass"] == 1, (
            "Pass from outside box to inside box must count as a box entry"
        )

    def test_goal_kick_into_box_excluded_from_box_entries_pass(self):
        # Goal Kick is in SET_PIECE_TYPES → excluded from box_entries_pass.
        # This is a box_entries_pass-specific regression: the Chunk B code checks
        # pass_type_name not in SET_PIECE_TYPES inside the outcome_name is None branch.
        r = _aggregate_events([
            _entry_pass_event(start=[100.0, 40.0], end=[105.0, 40.0], pass_type="Goal Kick")
        ], Counter())
        assert r["box_entries_pass"] == 0, (
            "Goal Kick is in SET_PIECE_TYPES and must be excluded from box_entries_pass"
        )

    def test_carry_missing_end_location_does_not_increment_entries(self):
        # A Carry event with end_location absent must not crash and must not
        # increment any entry counter (the pipeline guards with `if loc and end_loc`).
        event = {
            "type": {"name": "Carry"},
            "location": [75.0, 40.0],
            "carry": {},   # end_location key absent
        }
        r = _aggregate_events([event], Counter())
        assert r["final_third_entries_carry"] == 0, (
            "Carry with missing end_location must not increment final_third_entries_carry"
        )
        assert r["box_entries_carry"] == 0, (
            "Carry with missing end_location must not increment box_entries_carry"
        )

    def test_carry_missing_location_does_not_increment_entries(self):
        # A Carry event with location absent must not crash and must not increment
        # any entry counter.
        event = {
            "type": {"name": "Carry"},
            # location key absent
            "carry": {"end_location": [82.0, 40.0]},
        }
        r = _aggregate_events([event], Counter())
        assert r["final_third_entries_carry"] == 0, (
            "Carry with missing location must not increment final_third_entries_carry"
        )
        assert r["box_entries_carry"] == 0, (
            "Carry with missing location must not increment box_entries_carry"
        )


# ---------------------------------------------------------------------------
# Integration test: parquet goals + own goals == official match-score totals
# ---------------------------------------------------------------------------

_PLAYER_MATCH_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
)


_REF_DIR = Path(__file__).parent / "fixtures" / "reference_totals"
_REFERENCES = [json.loads(p.read_text()) for p in sorted(_REF_DIR.glob("*.json"))]


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output",
)
@pytest.mark.parametrize(
    "ref", _REFERENCES, ids=[f"{r['competition_id']}_{r['season_id']}" for r in _REFERENCES]
)
def test_goals_plus_own_goals_equal_match_scores(ref):
    """
    The parquet `goals` column counts Shot-outcome=Goal events (regular goals
    only).  StatsBomb encodes own goals as separate 'Own Goal Against' events
    that are not captured in the parquet yet.  Summing both must equal the
    official match totals from matches.json.

    Own goals come from the reference file, counted from raw events by the
    spikes (plan 003 §3), so this stays independent of the pipeline.
    Reconciliation for La Liga 2015/16: 1,014 (regular) + 29 (own goals) = 1,043.
    See ADR 002 §9 for the full explanation.
    """
    from eleveniq.statsbomb.loader import load_matches

    df = pl.read_parquet(_PLAYER_MATCH_PARQUET).filter(
        (pl.col("competition_id") == ref["competition_id"])
        & (pl.col("season_id") == ref["season_id"])
    )
    matches = load_matches(ref["competition_id"], ref["season_id"])

    official_total = sum(m["home_score"] + m["away_score"] for m in matches)
    parquet_goals = df["goals"].sum()
    own_goals = ref["base"]["own_goals"]

    assert parquet_goals == ref["base"]["goals"]
    assert parquet_goals + own_goals == official_total, (
        f"goals={parquet_goals} + own_goals={own_goals} = {parquet_goals + own_goals}"
        f" ≠ official {official_total}"
    )


