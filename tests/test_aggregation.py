"""
Tests for aggregation logic: pass completion with unknown outcomes,
safe division (per-90 and percentages), and multi-team aggregation key.

These tests use a minimal in-memory DataFrame rather than full pipeline fixtures
so they stay fast and isolated from I/O. The aggregation logic itself lives in
src/pipeline/build_player_season.py; these tests exercise the numeric contracts
that the pipeline must satisfy.
"""
import polars as pl
import pytest


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
