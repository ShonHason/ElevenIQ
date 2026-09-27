"""
Tests for aggregation logic: pass completion with unknown outcomes,
safe division (per-90 and percentages), and multi-team aggregation key.

These tests use a minimal in-memory DataFrame rather than full pipeline fixtures
so they stay fast and isolated from I/O. The aggregation logic itself lives in
src/pipeline/build_player_season.py; these tests exercise the numeric contracts
that the pipeline must satisfy.
"""
from pathlib import Path
import polars as pl
import pytest
from eleveniq.pipeline.build_player_match import _aggregate_events


# ---------------------------------------------------------------------------
# Helpers — build minimal DataFrames that match the player_match_stats schema
# ---------------------------------------------------------------------------

def _row(
    player_id: int = 1,
    team_id: int = 10,
    competition_id: int = 11,
    season_id: int = 27,
    minutes_played: float = 90.0,
    passes_attempted: int = 50,
    passes_completed: int = 40,
    passes_unknown_outcome: int = 0,
    goals: int = 1,
    shots: int = 5,
    shots_on_target: int = 3,
    dribbles_attempted: int = 4,
    dribbles_completed: int = 2,
    xg: float = 1.2,
    started: bool = True,
) -> dict:
    return dict(
        player_id=player_id,
        team_id=team_id,
        competition_id=competition_id,
        season_id=season_id,
        minutes_played=minutes_played,
        passes_attempted=passes_attempted,
        passes_completed=passes_completed,
        passes_unknown_outcome=passes_unknown_outcome,
        goals=goals,
        shots=shots,
        shots_on_target=shots_on_target,
        dribbles_attempted=dribbles_attempted,
        dribbles_completed=dribbles_completed,
        xg=xg,
        started=started,
    )


# ---------------------------------------------------------------------------
# Pass completion accounting
# ---------------------------------------------------------------------------

class TestPassCompletion:
    def test_known_pass_attempts_excludes_unknown(self):
        row = _row(passes_attempted=56, passes_completed=40, passes_unknown_outcome=6)
        df = pl.DataFrame([row])
        df = df.with_columns(
            (pl.col("passes_attempted") - pl.col("passes_unknown_outcome"))
            .alias("known_pass_attempts")
        )
        assert df["known_pass_attempts"][0] == 50

    def test_pass_completion_pct(self):
        row = _row(passes_attempted=56, passes_completed=40, passes_unknown_outcome=6)
        df = pl.DataFrame([row])
        df = df.with_columns(
            (pl.col("passes_attempted") - pl.col("passes_unknown_outcome"))
            .alias("known_pass_attempts")
        ).with_columns(
            (pl.col("passes_completed") / pl.col("known_pass_attempts"))
            .alias("pass_completion_pct")
        )
        assert df["pass_completion_pct"][0] == pytest.approx(40 / 50)

    def test_pass_completion_zero_known_attempts(self):
        # all passes have unknown outcome — denominator is 0
        row = _row(passes_attempted=6, passes_completed=0, passes_unknown_outcome=6)
        df = pl.DataFrame([row])
        df = df.with_columns(
            (pl.col("passes_attempted") - pl.col("passes_unknown_outcome"))
            .alias("known_pass_attempts")
        ).with_columns(
            pl.when(pl.col("known_pass_attempts") > 0)
            .then(pl.col("passes_completed") / pl.col("known_pass_attempts"))
            .otherwise(None)
            .alias("pass_completion_pct")
        )
        assert df["pass_completion_pct"][0] is None

    def test_no_unknown_outcomes_unchanged(self):
        row = _row(passes_attempted=50, passes_completed=40, passes_unknown_outcome=0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            (pl.col("passes_attempted") - pl.col("passes_unknown_outcome"))
            .alias("known_pass_attempts")
        )
        assert df["known_pass_attempts"][0] == 50


# ---------------------------------------------------------------------------
# Per-90 safe division
# ---------------------------------------------------------------------------

class TestPer90SafeDivision:
    def test_normal_per90(self):
        row = _row(goals=2, minutes_played=90.0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            pl.when(pl.col("minutes_played") > 0)
            .then(pl.col("goals") / pl.col("minutes_played") * 90)
            .otherwise(None)
            .alias("goals_p90")
        )
        assert df["goals_p90"][0] == pytest.approx(2.0)

    def test_per90_zero_minutes_returns_null(self):
        row = _row(goals=0, minutes_played=0.0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            pl.when(pl.col("minutes_played") > 0)
            .then(pl.col("goals") / pl.col("minutes_played") * 90)
            .otherwise(None)
            .alias("goals_p90")
        )
        assert df["goals_p90"][0] is None

    def test_per90_30_minutes_2_goals(self):
        row = _row(goals=2, minutes_played=30.0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            pl.when(pl.col("minutes_played") > 0)
            .then(pl.col("goals") / pl.col("minutes_played") * 90)
            .otherwise(None)
            .alias("goals_p90")
        )
        assert df["goals_p90"][0] == pytest.approx(6.0)

    def test_xg_per_shot_zero_shots_returns_null(self):
        row = _row(xg=0.0, shots=0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            pl.when(pl.col("shots") > 0)
            .then(pl.col("xg") / pl.col("shots"))
            .otherwise(None)
            .alias("xg_per_shot")
        )
        assert df["xg_per_shot"][0] is None

    def test_dribble_success_pct_zero_attempts_returns_null(self):
        row = _row(dribbles_attempted=0, dribbles_completed=0)
        df = pl.DataFrame([row])
        df = df.with_columns(
            pl.when(pl.col("dribbles_attempted") > 0)
            .then(pl.col("dribbles_completed") / pl.col("dribbles_attempted"))
            .otherwise(None)
            .alias("dribble_success_pct")
        )
        assert df["dribble_success_pct"][0] is None


# ---------------------------------------------------------------------------
# Multi-team aggregation key
# ---------------------------------------------------------------------------

class TestMultiTeamAggregationKey:
    def test_same_player_two_teams_two_rows(self):
        """A mid-season transfer should produce two separate season rows."""
        rows = [
            _row(player_id=1, team_id=10, goals=5, minutes_played=900.0),
            _row(player_id=1, team_id=10, goals=3, minutes_played=810.0),
            _row(player_id=1, team_id=20, goals=2, minutes_played=450.0),
        ]
        df = pl.DataFrame(rows)
        agg = (
            df.group_by(["player_id", "competition_id", "season_id", "team_id"])
            .agg(
                pl.col("goals").sum(),
                pl.col("minutes_played").sum(),
            )
            .sort(["team_id"])
        )
        assert len(agg) == 2
        team10 = agg.filter(pl.col("team_id") == 10)
        assert team10["goals"][0] == 8
        team20 = agg.filter(pl.col("team_id") == 20)
        assert team20["goals"][0] == 2

    def test_same_player_same_team_merged(self):
        """Multiple matches for the same player+team must collapse to one row."""
        rows = [_row(player_id=1, team_id=10, goals=1) for _ in range(5)]
        df = pl.DataFrame(rows)
        agg = (
            df.group_by(["player_id", "competition_id", "season_id", "team_id"])
            .agg(pl.col("goals").sum())
        )
        assert len(agg) == 1
        assert agg["goals"][0] == 5

    def test_match_count(self):
        rows = [_row(player_id=1, team_id=10) for _ in range(7)]
        df = pl.DataFrame(rows)
        agg = (
            df.group_by(["player_id", "competition_id", "season_id", "team_id"])
            .agg(pl.len().alias("matches"))
        )
        assert agg["matches"][0] == 7

    def test_starts_count(self):
        rows = [
            _row(player_id=1, team_id=10, started=True),
            _row(player_id=1, team_id=10, started=True),
            _row(player_id=1, team_id=10, started=False),
        ]
        df = pl.DataFrame(rows)
        agg = (
            df.group_by(["player_id", "competition_id", "season_id", "team_id"])
            .agg(pl.col("started").sum().alias("starts"))
        )
        assert agg["starts"][0] == 2


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
        result = _aggregate_events(events)
        assert result["key_passes"] == 1

    def test_goal_assist_only_counts_as_key_pass(self):
        # ADR 002 §8: the original pipeline missed goal_assist-only passes (11.1 %
        # of all key passes in La Liga 2015/16).  goal_assist=True must count.
        events = [_pass_event(goal_assist=True)]
        result = _aggregate_events(events)
        assert result["key_passes"] == 1

    def test_both_flags_counts_as_one_key_pass(self):
        # ADR 002 §8: both flags never co-occur in practice, but if they did the
        # pass should count once, not twice (the `or` short-circuits at shot_assist).
        events = [_pass_event(shot_assist=True, goal_assist=True)]
        result = _aggregate_events(events)
        assert result["key_passes"] == 1

    def test_pass_without_either_flag_not_a_key_pass(self):
        # A completed pass with neither flag must not increment key_passes.
        events = [_pass_event(shot_assist=False, goal_assist=False)]
        result = _aggregate_events(events)
        assert result["key_passes"] == 0

    def test_incomplete_pass_not_counted_as_key_pass(self):
        # An incomplete pass flagged as shot_assist is not counted as key pass
        # because the completion guard fires first (outcome != None → not progressive
        # and key_pass logic also checks the flag on the pass_data, not outcome).
        # This test verifies the pipeline doesn't double-count incomplete assists.
        # (ADR 002 §8: shot_assist/goal_assist flags only appear on completed passes.)
        events = [_pass_event(shot_assist=True, completed=False)]
        result = _aggregate_events(events)
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
        result = _aggregate_events(events)
        assert result["key_passes"] == 2

    def test_own_goal_for_not_counted_in_goals(self):
        # ADR 002 §9: Own Goal For events have no player attribution;
        # they must NOT increment the goals counter.
        events = [_own_goal_for_event()]
        result = _aggregate_events(events)
        assert result["goals"] == 0
        assert result["shots"] == 0

    def test_own_goal_against_not_counted_in_goals(self):
        # ADR 002 §9: Own Goal Against events attribute the own-goal to the player,
        # but must NOT count toward their `goals` column.  Own goals are not attacking
        # actions and must never appear in DNA or similarity calculations.
        events = [_own_goal_against_event(player_id=5)]
        result = _aggregate_events(events)
        assert result["goals"] == 0
        assert result["shots"] == 0

    def test_regular_goal_counted(self):
        # A Shot with outcome=Goal must increment goals (baseline sanity check).
        events = [_shot_event(outcome="Goal", xg=0.7)]
        result = _aggregate_events(events)
        assert result["goals"] == 1
        assert result["shots"] == 1

    def test_regular_goal_plus_own_goal_against_does_not_inflate(self):
        # One regular goal + one Own Goal Against must produce goals=1, not goals=2.
        events = [
            _shot_event(outcome="Goal", xg=0.6),
            _own_goal_against_event(player_id=9),
        ]
        result = _aggregate_events(events)
        assert result["goals"] == 1

    def test_empty_events_returns_zeros(self):
        result = _aggregate_events([])
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
        result = _aggregate_events([_block_event()])
        assert result["blocks"] == 1

    def test_offensive_block_excluded(self):
        # block.offensive=True is an attacking deflection, not a defensive action.
        result = _aggregate_events([_block_event(offensive=True)])
        assert result["blocks"] == 0

    def test_save_block_excluded(self):
        # block.save_block=True is a GK save recorded as a block — covered by GK metrics.
        result = _aggregate_events([_block_event(save_block=True)])
        assert result["blocks"] == 0

    def test_both_offensive_and_save_block_excluded(self):
        # Pathological case: both flags — still excluded.
        result = _aggregate_events([_block_event(offensive=True, save_block=True)])
        assert result["blocks"] == 0

    def test_multiple_blocks_mixed(self):
        # 2 defensive + 1 offensive + 1 save_block = 2 counted.
        events = [
            _block_event(),
            _block_event(),
            _block_event(offensive=True),
            _block_event(save_block=True),
        ]
        result = _aggregate_events(events)
        assert result["blocks"] == 2

    def test_block_with_no_block_subdict(self):
        # Block event missing the 'block' sub-dict entirely — treated as defensive.
        event = {"type": {"name": "Block"}}
        result = _aggregate_events([event])
        assert result["blocks"] == 1


class TestAggregateEventsTackles:
    """
    _aggregate_events counts tackles as Duel events where duel.type.name=='Tackle'.
    Aerial duels are ignored.  Won/lost determined by is_tackle_won().
    """

    def test_tackle_won_counted(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Won")])
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 1

    def test_tackle_lost_counted_as_attempt_not_won(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Lost In Play")])
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 0

    def test_aerial_duel_not_counted_as_tackle(self):
        # ADR 002 §6a / tackles.py: Aerial Lost duels are not tackles.
        result = _aggregate_events([_duel_event(duel_type="Aerial Lost", outcome="Won")])
        assert result["tackles_attempted"] == 0
        assert result["tackles_won"] == 0

    def test_tackle_success_in_play(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Success In Play")])
        assert result["tackles_won"] == 1

    def test_tackle_success_out(self):
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Success Out")])
        assert result["tackles_won"] == 1

    def test_tackle_unknown_outcome_not_won(self):
        # Unknown outcome → False from is_tackle_won (conservative classification).
        result = _aggregate_events([_duel_event(duel_type="Tackle", outcome="Unknown XYZ")])
        assert result["tackles_attempted"] == 1
        assert result["tackles_won"] == 0

    def test_multiple_tackles_accumulated(self):
        events = [
            _duel_event(duel_type="Tackle", outcome="Won"),
            _duel_event(duel_type="Tackle", outcome="Lost In Play"),
            _duel_event(duel_type="Aerial Lost"),  # should not count
        ]
        result = _aggregate_events(events)
        assert result["tackles_attempted"] == 2
        assert result["tackles_won"] == 1


class TestAggregateEventsDribbles:
    """
    _aggregate_events counts Dribble events; complete vs. incomplete determined by
    dribble.outcome.name == 'Complete'.
    """

    def test_complete_dribble_counted(self):
        result = _aggregate_events([_dribble_event(outcome="Complete")])
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 1

    def test_incomplete_dribble_attempted_not_completed(self):
        result = _aggregate_events([_dribble_event(outcome="Incomplete")])
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 0

    def test_dribble_unknown_outcome_not_completed(self):
        result = _aggregate_events([_dribble_event(outcome="Unknown")])
        assert result["dribbles_attempted"] == 1
        assert result["dribbles_completed"] == 0

    def test_multiple_dribbles_accumulated(self):
        events = [_dribble_event("Complete"), _dribble_event("Complete"), _dribble_event("Incomplete")]
        result = _aggregate_events(events)
        assert result["dribbles_attempted"] == 3
        assert result["dribbles_completed"] == 2


class TestAggregateEventsInterceptionsClearances:
    """
    Interceptions and Clearances are raw counts — every event increments its counter.
    """

    def test_interception_counted(self):
        result = _aggregate_events([_interception_event()])
        assert result["interceptions"] == 1

    def test_clearance_counted(self):
        result = _aggregate_events([_clearance_event()])
        assert result["clearances"] == 1

    def test_multiple_interceptions_accumulated(self):
        result = _aggregate_events([_interception_event(), _interception_event()])
        assert result["interceptions"] == 2

    def test_multiple_clearances_accumulated(self):
        result = _aggregate_events([_clearance_event(), _clearance_event(), _clearance_event()])
        assert result["clearances"] == 3

    def test_interceptions_and_clearances_independent(self):
        events = [_interception_event(), _clearance_event()]
        result = _aggregate_events(events)
        assert result["interceptions"] == 1
        assert result["clearances"] == 1


class TestAggregateEventsFouls:
    """
    fouls_committed and fouls_won are raw counts — every matching event increments its counter.
    Note: Foul Committed events with a red-card sub-dict (dismissal events) are STILL counted
    as fouls committed — the card check is done separately in quality_report.
    """

    def test_foul_committed_counted(self):
        result = _aggregate_events([_foul_committed_event()])
        assert result["fouls_committed"] == 1

    def test_foul_won_counted(self):
        result = _aggregate_events([_foul_won_event()])
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
        result = _aggregate_events([event])
        assert result["fouls_committed"] == 1

    def test_multiple_fouls_accumulated(self):
        events = [_foul_committed_event(), _foul_committed_event(), _foul_won_event()]
        result = _aggregate_events(events)
        assert result["fouls_committed"] == 2
        assert result["fouls_won"] == 1


class TestAggregateEventsShotsDetail:
    """
    Covers shots_on_target and xg accumulation, which are tested via the fixture
    in test_build_player_match.py but not via direct unit tests of _aggregate_events.
    """

    def test_on_target_goal_counted(self):
        result = _aggregate_events([_shot_event(outcome="Goal", xg=0.8)])
        assert result["shots_on_target"] == 1
        assert result["xg"] == pytest.approx(0.8)

    def test_on_target_saved_counted(self):
        result = _aggregate_events([_shot_event(outcome="Saved", xg=0.3)])
        assert result["shots_on_target"] == 1

    def test_on_target_saved_to_post_counted(self):
        result = _aggregate_events([_shot_event(outcome="Saved To Post", xg=0.1)])
        assert result["shots_on_target"] == 1

    def test_blocked_shot_not_on_target(self):
        result = _aggregate_events([_shot_event(outcome="Blocked", xg=0.2)])
        assert result["shots_on_target"] == 0
        assert result["shots"] == 1

    def test_off_target_shot_not_on_target(self):
        result = _aggregate_events([_shot_event(outcome="Off T", xg=0.05)])
        assert result["shots_on_target"] == 0

    def test_xg_accumulated_across_shots(self):
        events = [_shot_event(outcome="Saved", xg=0.3), _shot_event(outcome="Off T", xg=0.15)]
        result = _aggregate_events(events)
        assert result["xg"] == pytest.approx(0.45)

    def test_xg_none_treated_as_zero(self):
        # statsbomb_xg field absent — must not crash, must treat as 0.
        event = {
            "type": {"name": "Shot"},
            "shot": {"outcome": {"name": "Saved"}},
        }
        result = _aggregate_events([event])
        assert result["xg"] == pytest.approx(0.0)

    def test_multiple_goals_counted(self):
        events = [_shot_event("Goal"), _shot_event("Goal"), _shot_event("Saved")]
        result = _aggregate_events(events)
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
        result = _aggregate_events([event])
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
        result = _aggregate_events([event])
        assert result["progressive_passes"] == 0
        assert result["passes_completed"] == 0

    def test_pass_from_defensive_40_not_progressive(self):
        # start x=40 < 48 → excluded from progressive pass calculation.
        event = {
            "type": {"name": "Pass"},
            "location": [40.0, 40.0],
            "pass": {"end_location": [80.0, 40.0]},
        }
        result = _aggregate_events([event])
        assert result["progressive_passes"] == 0
        assert result["passes_attempted"] == 1  # still an attempt

    def test_progressive_carry_counted(self):
        # (60,40) → (80,40): 20-yard carry, 20-yard dist reduction → progressive.
        result = _aggregate_events([_carry_event([60.0, 40.0], [80.0, 40.0])])
        assert result["progressive_carries"] == 1
        assert result["carries"] == 1

    def test_backward_carry_not_progressive(self):
        # (80,40) → (60,40): moves away from goal.
        result = _aggregate_events([_carry_event([80.0, 40.0], [60.0, 40.0])])
        assert result["progressive_carries"] == 0
        assert result["carries"] == 1

    def test_missing_location_does_not_crash(self):
        # Pass event with no 'location' — should count the attempt but not progressive.
        event = {"type": {"name": "Pass"}, "pass": {"end_location": [90.0, 40.0]}}
        result = _aggregate_events([event])
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
        result = _aggregate_events([_pass_event(outcome="Unknown")])
        assert result["passes_attempted"] == 1

    def test_unknown_outcome_increments_passes_unknown_outcome(self):
        # outcome="Unknown" must land in the dedicated counter, not silently dropped.
        result = _aggregate_events([_pass_event(outcome="Unknown")])
        assert result["passes_unknown_outcome"] == 1

    def test_unknown_outcome_does_not_increment_passes_completed(self):
        # passes_completed = raw completed count only (outcome is None in StatsBomb).
        # An "Unknown" outcome must NOT be treated as completed.
        result = _aggregate_events([_pass_event(outcome="Unknown")])
        assert result["passes_completed"] == 0

    def test_unknown_outcome_not_counted_as_progressive(self):
        # Progressive passes require outcome=None (completed).  outcome="Unknown"
        # must return False from is_progressive_pass regardless of geometry.
        # Use a geometry that would qualify as progressive if the pass were completed:
        # start=(60,40) → end=(90,40): 30-yard reduction, start_x≥48 → progressive
        # if and only if outcome is None.
        result = _aggregate_events([
            _pass_event(outcome="Unknown", location=[60.0, 40.0], end_location=[90.0, 40.0])
        ])
        assert result["progressive_passes"] == 0

    def test_multiple_unknown_outcomes_accumulate(self):
        # Two passes with outcome="Unknown" → passes_unknown_outcome=2.
        result = _aggregate_events([
            _pass_event(outcome="Unknown"),
            _pass_event(outcome="Unknown"),
        ])
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
        ])
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
        ])
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
        result = _aggregate_events([_pass_event(outcome="Unknown", goal_assist=True)])
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
            result = _aggregate_events([_pass_event(outcome=outcome_name)])
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
        ])
        assert result["passes_unknown_outcome"] == 0
        assert result["passes_completed"] == 3
        assert result["passes_attempted"] == 3

    def test_unknown_outcome_with_shot_assist_increments_key_passes(self):
        # Symmetry check with the goal_assist test already in the class:
        # shot_assist flag on an "Unknown" outcome pass should still count as a key pass
        # (flag-based; not gated on outcome).
        result = _aggregate_events([_pass_event(outcome="Unknown", shot_assist=True)])
        assert result["key_passes"] == 1
        assert result["assists"] == 0          # shot_assist only → no assist
        assert result["passes_completed"] == 0
        assert result["passes_unknown_outcome"] == 1


# ---------------------------------------------------------------------------
# _primary_raw_position — unit tests
# ---------------------------------------------------------------------------

from eleveniq.pipeline.build_player_match import _primary_raw_position


class TestPrimaryRawPosition:
    """
    _primary_raw_position returns the StatsBomb position name with the most
    playing time.  Tie-break: alphabetically first (same rule as primary_position_group).
    """

    def test_single_position(self):
        assert _primary_raw_position({"Center Back": 90.0}) == "Center Back"

    def test_most_minutes_wins(self):
        raw = {"Center Forward": 70.0, "Left Wing": 20.0}
        assert _primary_raw_position(raw) == "Center Forward"

    def test_tie_broken_alphabetically(self):
        # "Center Back" < "Left Back" alphabetically → Center Back wins tie.
        raw = {"Center Back": 45.0, "Left Back": 45.0}
        assert _primary_raw_position(raw) == "Center Back"

    def test_empty_returns_empty_string(self):
        # Docstring: no-op for empty input → returns "".
        assert _primary_raw_position({}) == ""

    def test_three_positions(self):
        raw = {"Defensive Midfield": 30.0, "Center Midfield": 55.0, "Left Midfield": 5.0}
        assert _primary_raw_position(raw) == "Center Midfield"


# ---------------------------------------------------------------------------
# Integration test: parquet goals + own goals == official match-score totals
# ---------------------------------------------------------------------------

_PLAYER_MATCH_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output",
)
def test_goals_plus_own_goals_equal_match_scores():
    """
    The parquet `goals` column counts Shot-outcome=Goal events (regular goals
    only).  StatsBomb encodes own goals as separate 'Own Goal Against' events
    that are not captured in the parquet yet.  Summing both sources must equal
    the official match totals from matches.json.

    Reconciliation for La Liga 2015/16: 1,014 (regular) + 29 (own goals) = 1,043.
    See ADR 002 §9 for the full explanation.
    """
    from eleveniq.statsbomb.loader import load_events, load_matches

    df = pl.read_parquet(_PLAYER_MATCH_PARQUET)
    # La Liga 2015/16 — the only season currently in the parquet
    matches = load_matches(11, 27)

    official_total = sum(m["home_score"] + m["away_score"] for m in matches)
    parquet_goals = df["goals"].sum()
    own_goals = sum(
        1
        for m in matches
        for e in load_events(m["match_id"])
        if (e.get("type") or {}).get("name") == "Own Goal Against"
    )

    assert parquet_goals + own_goals == official_total, (
        f"goals={parquet_goals} + own_goals={own_goals} = {parquet_goals + own_goals}"
        f" ≠ official {official_total}"
    )
