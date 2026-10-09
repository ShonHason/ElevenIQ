"""
Tests for aggregate_player_season() and build_player_season().

Unit tests call aggregate_player_season() directly with in-memory DataFrames.
Integration tests require both parquets to exist after the pipeline has run.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from eleveniq.metrics.defensive_actions import DEFENSIVE_ACTIONS, team_column
from eleveniq.config import COMPETITION_SEASONS, CompetitionSeason
from eleveniq.pipeline.coverage import CoverageCheckError
import eleveniq.pipeline.build_player_season as bps
from eleveniq.pipeline.build_player_season import (
    aggregate_player_season,
    build_player_season,
    find_name_variants,
    _COUNT_COLS,
    _DISPLAY_ONLY_COLS,
)

_MATCH_PARQUET = Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
_SEASON_PARQUET = Path(__file__).resolve().parents[1] / "data" / "processed" / "player_season_stats.parquet"

# Per-league reference figures from the raw-JSON spikes (plan 003 §3).
_REF_DIR = Path(__file__).parent / "fixtures" / "reference_totals"
_REFERENCES = [json.loads(p.read_text()) for p in sorted(_REF_DIR.glob("*.json"))]
_REF_IDS = [f"{r['competition_id']}_{r['season_id']}" for r in _REFERENCES]


def _league(df: pl.DataFrame, ref: dict) -> pl.DataFrame:
    return df.filter(
        (pl.col("competition_id") == ref["competition_id"])
        & (pl.col("season_id") == ref["season_id"])
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_row(
    player_id: int = 1,
    player_name: str = "Player A",
    player_nickname: str | None = None,
    team_id: int = 10,
    team_name: str = "Team A",
    competition_id: int = 11,
    season_id: int = 27,
    source: str = "statsbomb",
    minutes_played: float = 90.0,
    position_group: str = "DM-CM",
    goals: int = 0,
    shots: int = 0,
    shots_on_target: int = 0,
    shots_missing_xg: int = 0,
    xg: float = 0.0,
    passes_attempted: int = 50,
    passes_completed: int = 40,
    passes_unknown_outcome: int = 0,
    progressive_passes: int = 5,
    key_passes: int = 1,
    carries: int = 10,
    progressive_carries: int = 3,
    tackles_attempted: int = 4,
    tackles_won: int = 2,
    dribbles_attempted: int = 2,
    dribbles_completed: int = 1,
    interceptions: int = 1,
    clearances: int = 0,
    blocks: int = 0,
    fouls_committed: int = 1,
    fouls_won: int = 1,
    assists: int = 0,
    crosses_attempted: int = 0,
    crosses_completed: int = 0,
    crosses_unknown_outcome: int = 0,
    dispossessed: int = 0,
    miscontrols: int = 0,
    pressures: int = 0,
    ball_recoveries: int = 0,
    ball_recoveries_failed: int = 0,
    dribbled_past: int = 0,
    aerials_won: int = 0,
    aerials_lost: int = 0,
    long_passes_attempted: int = 0,
    long_passes_completed: int = 0,
    long_passes_unknown_outcome: int = 0,
    passes_missing_length: int = 0,
    switches: int = 0,
    xg_assisted: float = 0.0,
    xg_assisted_direct: float = 0.0,
    final_third_entries_pass: int = 0,
    final_third_entries_carry: int = 0,
    box_entries_pass: int = 0,
    box_entries_carry: int = 0,
    started: bool = True,
    started_inferred: bool = False,
    minutes_gk: float = 0.0,
    minutes_cb: float = 0.0,
    minutes_fb_wb: float = 0.0,
    minutes_dm_cm: float = 0.0,
    minutes_am_winger: float = 0.0,
    minutes_st: float = 0.0,
    minutes_unknown: float = 0.0,
    # ADR 004 (plan 005 M4b) match columns. The defaults are neutral, so the
    # pre-M4b expectations hold: a nominal 90-minute match (nominal minutes ==
    # minutes_played), no penalties, no Injury Clearances, no restarts.
    match_duration: float = 90.0,
    match_nominal_duration: float = 90.0,
    np_shots: int | None = None,
    np_xg: float | None = None,
    np_shots_on_target: int | None = None,
    passes_injury_clearance: int = 0,
    long_passes_injury_clearance: int = 0,
    open_play_passes_attempted: int | None = None,
    team_open_play_passes_while_on: int = 0,
    # ADR 004 §13.8 (M4g4b): exposure and team-action counts, neutral 0 by default;
    # team_actions maps an action (DEFENSIVE_ACTIONS) to its team_<action>_while_on.
    opponent_open_play_passes_while_on: int = 0,
    team_actions: dict[str, int] | None = None,
    # ADR 004 §9: neutral defaults, everything open play (no restart key passes).
    op_xg_assisted: float | None = None,
    restart_xg_assisted: float = 0.0,
    op_key_passes: int | None = None,
    restart_key_passes: int = 0,
    op_assists: int | None = None,
    restart_assists: int = 0,
) -> dict:
    # Ensure per-group minutes are consistent with minutes_played when caller
    # does not specify them explicitly.
    group_sum = (minutes_gk + minutes_cb + minutes_fb_wb + minutes_dm_cm
                 + minutes_am_winger + minutes_st + minutes_unknown)
    if group_sum == 0.0 and minutes_played > 0.0:
        # Default: put all minutes in the position_group column.
        col_map = {
            "GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
            "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger",
            "ST": "minutes_st", "UNKNOWN": "minutes_unknown",
        }
        locals_copy = dict(locals())
        if position_group not in col_map:
            raise ValueError(
                f"Unknown position_group {position_group!r} — "
                f"valid values: {sorted(col_map)}"
            )
        locals_copy[col_map[position_group]] = minutes_played
        # Rebuild with updated per-group column
        minutes_gk = locals_copy["minutes_gk"]
        minutes_cb = locals_copy["minutes_cb"]
        minutes_fb_wb = locals_copy["minutes_fb_wb"]
        minutes_dm_cm = locals_copy["minutes_dm_cm"]
        minutes_am_winger = locals_copy["minutes_am_winger"]
        minutes_st = locals_copy["minutes_st"]
        minutes_unknown = locals_copy["minutes_unknown"]

    return dict(
        player_id=player_id, player_name=player_name, player_nickname=player_nickname,
        team_id=team_id, team_name=team_name,
        competition_id=competition_id, season_id=season_id, source=source,
        minutes_played=minutes_played, position_group=position_group,
        goals=goals, shots=shots, shots_on_target=shots_on_target,
        shots_missing_xg=shots_missing_xg, xg=xg,
        passes_attempted=passes_attempted, passes_completed=passes_completed,
        passes_unknown_outcome=passes_unknown_outcome,
        progressive_passes=progressive_passes, key_passes=key_passes,
        carries=carries, progressive_carries=progressive_carries,
        tackles_attempted=tackles_attempted, tackles_won=tackles_won,
        dribbles_attempted=dribbles_attempted, dribbles_completed=dribbles_completed,
        interceptions=interceptions, clearances=clearances, blocks=blocks,
        fouls_committed=fouls_committed, fouls_won=fouls_won, assists=assists,
        crosses_attempted=crosses_attempted, crosses_completed=crosses_completed,
        crosses_unknown_outcome=crosses_unknown_outcome,
        dispossessed=dispossessed, miscontrols=miscontrols, pressures=pressures,
        ball_recoveries=ball_recoveries, ball_recoveries_failed=ball_recoveries_failed,
        dribbled_past=dribbled_past,
        aerials_won=aerials_won, aerials_lost=aerials_lost,
        long_passes_attempted=long_passes_attempted,
        long_passes_completed=long_passes_completed,
        long_passes_unknown_outcome=long_passes_unknown_outcome,
        passes_missing_length=passes_missing_length, switches=switches,
        xg_assisted=xg_assisted, xg_assisted_direct=xg_assisted_direct,
        final_third_entries_pass=final_third_entries_pass,
        final_third_entries_carry=final_third_entries_carry,
        box_entries_pass=box_entries_pass,
        box_entries_carry=box_entries_carry,
        started=started, started_inferred=started_inferred,
        minutes_gk=minutes_gk, minutes_cb=minutes_cb,
        minutes_fb_wb=minutes_fb_wb, minutes_dm_cm=minutes_dm_cm,
        minutes_am_winger=minutes_am_winger, minutes_st=minutes_st,
        minutes_unknown=minutes_unknown,
        match_duration=match_duration, match_nominal_duration=match_nominal_duration,
        np_shots=shots if np_shots is None else np_shots,
        np_xg=xg if np_xg is None else np_xg,
        np_shots_on_target=shots_on_target if np_shots_on_target is None else np_shots_on_target,
        passes_injury_clearance=passes_injury_clearance,
        long_passes_injury_clearance=long_passes_injury_clearance,
        open_play_passes_attempted=(passes_attempted if open_play_passes_attempted is None
                                    else open_play_passes_attempted),
        team_open_play_passes_while_on=team_open_play_passes_while_on,
        opponent_open_play_passes_while_on=opponent_open_play_passes_while_on,
        **{team_column(a): (team_actions or {}).get(a, 0) for a in DEFENSIVE_ACTIONS},
        op_xg_assisted=xg_assisted if op_xg_assisted is None else op_xg_assisted,
        restart_xg_assisted=restart_xg_assisted,
        op_key_passes=key_passes if op_key_passes is None else op_key_passes,
        restart_key_passes=restart_key_passes,
        op_assists=assists if op_assists is None else op_assists,
        restart_assists=restart_assists,
    )


def _df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# TestPer90FromTotals — ADR 002 §6a regression
# ---------------------------------------------------------------------------

class TestPer90FromTotals:
    def test_3min_sub_season_goals_p90(self):
        """
        ADR 002 §6a: season per-90 must be computed from season totals, not by
        averaging match-level per-90 values.

        Player plays two matches: 3 min (1 goal) + 87 min (0 goals).
          Correct:  1 goal / 90 min * 90 = 1.0
          Wrong:    mean(1/3*90, 0/87*90) = mean(30.0, 0.0) = 15.0
        """
        rows = [
            _match_row(player_id=1, minutes_played=3.0, goals=1,
                       minutes_dm_cm=3.0),
            _match_row(player_id=1, minutes_played=87.0, goals=0,
                       minutes_dm_cm=87.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["goals_p90"] == pytest.approx(1.0), (
            f"Expected goals_p90=1.0 (season total method) but got {row['goals_p90']}"
        )

    def test_season_minutes_sums_correctly(self):
        rows = [
            _match_row(player_id=1, minutes_played=45.0, minutes_dm_cm=45.0),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(135.0)

    def test_goals_summed_not_averaged(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, goals=2, minutes_dm_cm=90.0),
            _match_row(player_id=1, minutes_played=90.0, goals=1, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["goals"] == 3
        assert row["goals_p90"] == pytest.approx(3 / 180 * 90)


# ---------------------------------------------------------------------------
# TestSafeDivision
# ---------------------------------------------------------------------------

class TestSafeDivision:
    def test_zero_minutes_p90_all_null(self):
        """A row with 0 minutes must produce null for every _p90 column."""
        # Build a match row without the auto-filling helper (0 minutes, 0 group min)
        base = _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)
        zero = {**base, "minutes_played": 0.0, "minutes_dm_cm": 0.0}
        result = aggregate_player_season(_df([zero]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        p90_cols = [c for c in result.columns if c.endswith("_p90")]
        for col in p90_cols:
            assert row[col] is None, f"Expected {col}=null for 0 minutes but got {row[col]}"

    def test_all_unknown_passes_completion_pct_null(self):
        """passes_attempted == passes_unknown_outcome → denominator 0 → null."""
        rows = [_match_row(
            player_id=1,
            passes_attempted=10, passes_completed=0, passes_unknown_outcome=10,
            minutes_dm_cm=90.0,
        )]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["pass_completion_pct"] is None

    def test_zero_shots_xg_per_shot_null(self):
        rows = [_match_row(player_id=1, shots=0, xg=0.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_per_shot"] is None

    def test_zero_dribbles_attempted_null(self):
        rows = [_match_row(player_id=1, dribbles_attempted=0, dribbles_completed=0,
                           minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["dribble_success_pct"] is None

    def test_zero_tackles_attempted_null(self):
        rows = [_match_row(player_id=1, tackles_attempted=0, tackles_won=0,
                           minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["tackle_success_pct"] is None

    def test_zero_defensive_duels_null(self):
        """ADR 004 §13.2: null when tackles_attempted + dribbled_past is 0."""
        rows = [_match_row(player_id=1, tackles_attempted=0, tackles_won=0, dribbled_past=0,
                           minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["defensive_duel_success_pct"] is None

    def test_defensive_duel_success_counts_lost_tackles_and_dribbled_past(self):
        """tackles_won / (tackles_attempted + dribbled_past), summed over the season."""
        rows = [_match_row(player_id=1, tackles_attempted=3, tackles_won=2,
                           dribbled_past=1, minutes_dm_cm=90.0),
                _match_row(player_id=1, tackles_attempted=0, tackles_won=0,
                           dribbled_past=2, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["defensive_duel_success_pct"] == pytest.approx(2 / 6)


# ---------------------------------------------------------------------------
# TestPositionGroup
# ---------------------------------------------------------------------------

class TestPositionGroup:
    def test_most_minutes_group_wins(self):
        """Player splits 60 min CB + 45 min FB-WB → season group = CB."""
        rows = [
            _match_row(player_id=1, position_group="CB",
                       minutes_played=60.0, minutes_cb=60.0),
            _match_row(player_id=1, position_group="FB-WB",
                       minutes_played=45.0, minutes_fb_wb=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "CB"
        assert row["minutes_cb"] == pytest.approx(60.0)
        assert row["minutes_fb_wb"] == pytest.approx(45.0)

    def test_group_minutes_sum_to_minutes_played(self):
        rows = [
            _match_row(player_id=1, position_group="CB",
                       minutes_played=60.0, minutes_cb=60.0),
            _match_row(player_id=1, position_group="FB-WB",
                       minutes_played=45.0, minutes_fb_wb=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        group_cols = ["minutes_gk", "minutes_cb", "minutes_fb_wb", "minutes_dm_cm",
                      "minutes_am_winger", "minutes_st", "minutes_unknown"]
        group_total = sum(row[c] for c in group_cols)
        assert group_total == pytest.approx(row["minutes_played"])

    def test_alphabetic_tiebreak(self):
        """Tie between AM-Winger and CB: AM-Winger wins (alphabetically first)."""
        rows = [
            _match_row(player_id=1, position_group="AM-Winger",
                       minutes_played=45.0, minutes_am_winger=45.0),
            _match_row(player_id=1, position_group="CB",
                       minutes_played=45.0, minutes_cb=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "AM-Winger"

    def test_single_group_all_matches(self):
        rows = [
            _match_row(player_id=1, position_group="ST",
                       minutes_played=90.0, minutes_st=90.0),
            _match_row(player_id=1, position_group="ST",
                       minutes_played=90.0, minutes_st=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "ST"
        assert row["minutes_st"] == pytest.approx(180.0)


# ---------------------------------------------------------------------------
# TestMeetsMinMinutes
# ---------------------------------------------------------------------------

class TestMeetsMinMinutes:
    def test_below_threshold_false(self):
        rows = [_match_row(player_id=1, minutes_played=89.0, minutes_dm_cm=89.0)]
        result = aggregate_player_season(_df(rows), min_minutes=90)
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is False

    def test_at_threshold_true(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows), min_minutes=90)
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is True

    def test_two_matches_below_threshold_false(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows), min_minutes=900)
        # 180 min < 900 → False
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is False

    def test_custom_min_minutes_600(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)
            for _ in range(7)  # 630 min total
        ]
        result = aggregate_player_season(_df(rows), min_minutes=600)
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is True

    def test_default_threshold_is_900(self):
        # 10 × 90 = 900 min → meets default threshold exactly
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)
                for _ in range(10)]
        result = aggregate_player_season(_df(rows))
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is True


# ---------------------------------------------------------------------------
# TestGkFlag
# ---------------------------------------------------------------------------

class TestGkFlag:
    def test_gk_is_gk_true(self):
        rows = [_match_row(player_id=1, position_group="GK",
                           minutes_played=90.0, minutes_gk=90.0)]
        result = aggregate_player_season(_df(rows))
        assert result.filter(pl.col("player_id") == 1)["is_gk"][0] is True

    def test_outfield_is_gk_false(self):
        for group in ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST"):
            col = f"minutes_{group.lower().replace('-', '_').replace('/', '_')}"
            col_map = {"CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
                       "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger",
                       "ST": "minutes_st"}
            rows = [_match_row(player_id=1, position_group=group,
                               minutes_played=90.0, **{col_map[group]: 90.0})]
            result = aggregate_player_season(_df(rows))
            assert result.filter(pl.col("player_id") == 1)["is_gk"][0] is False, (
                f"Expected is_gk=False for position_group={group}"
            )


# ---------------------------------------------------------------------------
# TestMultiTeamFlags
# ---------------------------------------------------------------------------

class TestMultiTeamFlags:
    def test_two_teams_multi_team_true(self):
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=600.0,
                       minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=20, minutes_played=400.0,
                       minutes_dm_cm=400.0),
        ]
        result = aggregate_player_season(_df(rows))
        for row in result.filter(pl.col("player_id") == 1).iter_rows(named=True):
            assert row["multi_team_season"] is True

    def test_two_teams_primary_stint_marks_most_minutes(self):
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=600.0,
                       minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=20, minutes_played=400.0,
                       minutes_dm_cm=400.0),
        ]
        result = aggregate_player_season(_df(rows))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert len(primary) == 1
        assert primary["team_id"][0] == 10
        assert primary["minutes_played"][0] == pytest.approx(600.0)

    def test_primary_stint_minutes_beats_lower_team_id(self):
        """
        Team with MORE minutes must be primary even when it has the HIGHER team_id.
        Tie-break (lower team_id) must NOT override a clear minutes advantage.
        """
        rows = [
            _match_row(player_id=1, team_id=100, minutes_played=200.0,
                       minutes_dm_cm=200.0),
            _match_row(player_id=1, team_id=250, minutes_played=1500.0,
                       minutes_dm_cm=1500.0),
        ]
        result = aggregate_player_season(_df(rows))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert len(primary) == 1
        assert primary["team_id"][0] == 250, (
            "team 250 has 1500 min vs team 100's 200 min — minutes must win, "
            "not the lower-team_id tie-break"
        )
        assert primary["minutes_played"][0] == pytest.approx(1500.0)

    def test_single_team_flags(self):
        rows = [_match_row(player_id=1, team_id=10, minutes_played=900.0,
                           minutes_dm_cm=900.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["multi_team_season"] is False
        assert row["is_primary_stint"] is True

    def test_tiebreak_lower_team_id_wins(self):
        rows = [
            _match_row(player_id=1, team_id=30, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=10, minutes_played=500.0,
                       minutes_dm_cm=500.0),
        ]
        result = aggregate_player_season(_df(rows))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert len(primary) == 1
        assert primary["team_id"][0] == 10

    def test_two_players_independent(self):
        """Multi-team flags for player 1 must not bleed into player 2."""
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=600.0,
                       minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=20, minutes_played=400.0,
                       minutes_dm_cm=400.0),
            _match_row(player_id=2, team_id=10, minutes_played=900.0,
                       minutes_dm_cm=900.0),
        ]
        result = aggregate_player_season(_df(rows))
        p2 = result.filter(pl.col("player_id") == 2).row(0, named=True)
        assert p2["multi_team_season"] is False
        assert p2["is_primary_stint"] is True

    def test_mid_season_transfer_totals_per_team(self):
        """A mid-season transfer gives one row per team, each with its own
        totals (moved from test_aggregation.py, plan 004 H3b R12)."""
        rows = [
            _match_row(player_id=1, team_id=10, goals=5, minutes_played=900.0,
                       minutes_dm_cm=900.0),
            _match_row(player_id=1, team_id=10, goals=3, minutes_played=810.0,
                       minutes_dm_cm=810.0),
            _match_row(player_id=1, team_id=20, goals=2, minutes_played=450.0,
                       minutes_dm_cm=450.0),
        ]
        result = aggregate_player_season(_df(rows)).sort("team_id")
        assert result["team_id"].to_list() == [10, 20]
        assert result["goals"].to_list() == [8, 2]
        assert result["minutes_played"].to_list() == pytest.approx([1710.0, 450.0])
        assert result["appearances"].to_list() == [2, 1]


# ---------------------------------------------------------------------------
# TestPlayerSeasonMinutesTotal
# ---------------------------------------------------------------------------

class TestPlayerSeasonMinutesTotal:
    def test_single_team_equals_minutes_played(self):
        rows = [_match_row(player_id=1, team_id=10, minutes_played=720.0,
                           minutes_dm_cm=720.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["player_season_minutes_total"] == pytest.approx(row["minutes_played"])

    def test_two_teams_total_equals_combined(self):
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=600.0,
                       minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=20, minutes_played=400.0,
                       minutes_dm_cm=400.0),
        ]
        result = aggregate_player_season(_df(rows))
        totals = result.filter(pl.col("player_id") == 1)["player_season_minutes_total"]
        assert totals[0] == pytest.approx(1000.0)
        assert totals[1] == pytest.approx(1000.0)  # same value on both rows


# ---------------------------------------------------------------------------
# TestStartsAndAppearances
# ---------------------------------------------------------------------------

class TestStartsAndAppearances:
    def test_appearances_equals_match_count(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0) for _ in range(7)]
        result = aggregate_player_season(_df(rows))
        assert result.filter(pl.col("player_id") == 1)["appearances"][0] == 7

    def test_starts_equals_sum_of_started(self):
        rows = [
            _match_row(player_id=1, started=True, minutes_dm_cm=90.0),
            _match_row(player_id=1, started=True, minutes_dm_cm=90.0),
            _match_row(player_id=1, started=False, minutes_dm_cm=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["starts"] == 2
        assert row["appearances"] == 3

    def test_started_inferred_count_summed(self):
        rows = [
            _match_row(player_id=1, started=True, started_inferred=True,
                       minutes_dm_cm=90.0),
            _match_row(player_id=1, started=True, started_inferred=False,
                       minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        assert result.filter(pl.col("player_id") == 1)["started_inferred_count"][0] == 1


# ---------------------------------------------------------------------------
# TestCrossMetrics
# ---------------------------------------------------------------------------

class TestCrossMetrics:
    def test_crosses_attempted_p90(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           crosses_attempted=3, crosses_completed=1)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["crosses_attempted"] == 3
        assert row["crosses_attempted_p90"] == pytest.approx(3.0)

    def test_cross_completion_pct(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           crosses_attempted=4, crosses_completed=1)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["cross_completion_pct"] == pytest.approx(0.25)

    def test_zero_crosses_attempted_completion_pct_null(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           crosses_attempted=0, crosses_completed=0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["cross_completion_pct"] is None

    def test_crosses_summed_across_matches(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=5, crosses_completed=2),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=3, crosses_completed=1),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["crosses_attempted"] == 8
        assert row["crosses_completed"] == 3
        assert row["cross_completion_pct"] == pytest.approx(3 / 8)

    def test_zero_minutes_crosses_p90_null(self):
        base = _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                          crosses_attempted=2, crosses_completed=1)
        zero = {**base, "minutes_played": 0.0, "minutes_dm_cm": 0.0}
        result = aggregate_player_season(_df([zero]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["crosses_attempted_p90"] is None


# ---------------------------------------------------------------------------
# TestChunkASeasonMetrics — aerials, long passes, and ratio columns (ADR §19–20)
# ---------------------------------------------------------------------------

class TestChunkASeasonMetrics:
    def test_aerial_win_pct_computed(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=7, aerials_lost=3)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerial_win_pct"] == pytest.approx(0.7)

    def test_aerial_win_pct_null_when_denom_below_threshold(self):
        # 5+4=9 < MIN_AERIAL_CONTESTS (10)
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=5, aerials_lost=4)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerial_win_pct"] is None

    def test_aerial_win_pct_null_when_no_aerials(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=0, aerials_lost=0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerial_win_pct"] is None

    def test_aerial_win_pct_exact_threshold(self):
        # 6+4=10 == MIN_AERIAL_CONTESTS — should be non-null
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=6, aerials_lost=4)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerial_win_pct"] == pytest.approx(0.6)

    def test_long_pass_completion_pct_computed(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           long_passes_attempted=5, long_passes_completed=3)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["long_pass_completion_pct"] == pytest.approx(0.6)

    def test_long_pass_completion_pct_null_when_no_long_passes(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           long_passes_attempted=0, long_passes_completed=0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["long_pass_completion_pct"] is None

    def test_chunk_a_columns_present(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        for col in (
            "dispossessed", "miscontrols", "pressures", "ball_recoveries",
            "dribbled_past", "aerials_won", "aerials_lost",
            "long_passes_attempted", "long_passes_completed",
            "passes_missing_length", "switches",
            "aerial_win_pct", "long_pass_completion_pct",
            "dispossessed_p90", "miscontrols_p90", "pressures_p90",
            "ball_recoveries_p90", "dribbled_past_p90",
            "aerials_won_p90", "aerials_lost_p90",
            "long_passes_attempted_p90", "switches_p90",
        ):
            assert col in result.columns, f"Missing column: {col!r}"

    def test_long_passes_completed_has_no_p90_column(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "long_passes_completed_p90" not in result.columns

    def test_passes_missing_length_has_no_p90_column(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "passes_missing_length_p90" not in result.columns

    def test_aerials_summed_across_matches(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       aerials_won=4, aerials_lost=3),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       aerials_won=3, aerials_lost=4),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerials_won"] == 7
        assert row["aerials_lost"] == 7
        assert row["aerial_win_pct"] == pytest.approx(0.5)

    def test_aerial_win_pct_zero_wins_above_threshold(self):
        # 0 won + 10 lost = 10 == MIN_AERIAL_CONTESTS → pct should be 0.0, not null.
        # This distinguishes "no aerial contests" (null) from "lost every duel" (0.0).
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=0, aerials_lost=10)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerial_win_pct"] == pytest.approx(0.0), (
            "A player with 0 wins / 10 lost must have aerial_win_pct=0.0 "
            "(not null) — denominator meets MIN_AERIAL_CONTESTS"
        )

    def test_pressures_p90_arithmetic(self):
        # 18 pressures over 90 minutes = 18.0 p90.
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           pressures=18)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["pressures_p90"] == pytest.approx(18.0)

    def test_aerials_won_p90_arithmetic(self):
        # 4 aerials_won over 45 minutes = 8.0 p90.
        rows = [_match_row(player_id=1, minutes_played=45.0, minutes_dm_cm=45.0,
                           aerials_won=4)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerials_won_p90"] == pytest.approx(8.0)

    def test_long_passes_attempted_p90_arithmetic(self):
        # 9 long passes attempted over 30 minutes = 27.0 p90.
        rows = [_match_row(player_id=1, minutes_played=30.0, minutes_dm_cm=30.0,
                           long_passes_attempted=9, long_passes_completed=5)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["long_passes_attempted_p90"] == pytest.approx(27.0)

    def test_long_pass_completion_pct_zero_completions_not_null(self):
        # 5 attempts, 0 completions → pct = 0.0, not null.
        # Tests the boundary between null (0 attempts) and 0.0 (attempts > 0, no completions).
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           long_passes_attempted=5, long_passes_completed=0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["long_pass_completion_pct"] == pytest.approx(0.0), (
            "5 long passes attempted with 0 completions must be 0.0, not null"
        )

    def test_chunk_a_p90_zero_minutes_all_null(self):
        # All new chunk-A _p90 columns must be null when minutes_played == 0.
        base = _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                          pressures=5, aerials_won=3, aerials_lost=2,
                          ball_recoveries=4, switches=2)
        zero = {**base, "minutes_played": 0.0, "minutes_dm_cm": 0.0}
        result = aggregate_player_season(_df([zero]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        for col in ("pressures_p90", "aerials_won_p90", "aerials_lost_p90",
                    "ball_recoveries_p90", "dribbled_past_p90",
                    "dispossessed_p90", "miscontrols_p90",
                    "long_passes_attempted_p90", "switches_p90"):
            assert row[col] is None, f"Expected {col}=null for 0 minutes but got {row[col]}"

    def test_aerials_won_p90_zero_not_null_distinguishes_from_aerial_win_pct(self):
        # aerials_won is in _COUNT_COLS → its per-90 is: 0 / 90 * 90 = 0.0 (not null).
        # aerial_win_pct is a ratio column → null when denominator < MIN_AERIAL_CONTESTS.
        # The two columns must behave differently when aerials_won=0 and aerials_lost=0.
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                           aerials_won=0, aerials_lost=0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["aerials_won_p90"] == pytest.approx(0.0), (
            "aerials_won=0 with 90 min must produce aerials_won_p90=0.0 (count-col rule), "
            "not null — null is reserved for the aerial_win_pct ratio column"
        )
        assert row["aerial_win_pct"] is None, (
            "aerial_win_pct must be null when denominator (0) < MIN_AERIAL_CONTESTS"
        )

    def test_ball_recoveries_failed_summed_across_matches(self):
        # ball_recoveries_failed is a display-only column (ADR §21): it must be
        # summed across matches like any count column, even though it has no _p90.
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       ball_recoveries=8, ball_recoveries_failed=2),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       ball_recoveries=5, ball_recoveries_failed=1),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["ball_recoveries"] == 13
        assert row["ball_recoveries_failed"] == 3, (
            "ball_recoveries_failed must be summed across matches "
            "(display-only denominator, ADR §21)"
        )
        # The combined total equals the total recovery attempts (success + failure).
        assert row["ball_recoveries"] + row["ball_recoveries_failed"] == 16


# ---------------------------------------------------------------------------
# TestBuildPlayerSeasonIOWrapper — build_player_season() reads / writes parquet
# ---------------------------------------------------------------------------

def _raw_fixture(tmp_path: Path) -> tuple[Path, tuple[CompetitionSeason, ...]]:
    """Synthetic raw StatsBomb dir for the I/O wrapper: one match (id 1) of a
    2-team league, i.e. 1 of 2 fixtures -> PARTIAL under ADR 003 §2."""
    root = tmp_path / "raw"
    (root / "matches" / "11").mkdir(parents=True)
    (root / "competitions.json").write_text(json.dumps(
        [{"competition_id": c, "season_id": 27, "season_name": "2015/2016"} for c in (11, 2)]
    ))
    (root / "matches" / "11" / "27.json").write_text(json.dumps([{
        "match_id": 1,
        "home_team": {"home_team_id": 1}, "away_team": {"away_team_id": 2},
    }]))
    return root, (CompetitionSeason(11, 27, "Fixture", 2, "PARTIAL", "statsbomb"),)


def _write_input(path: Path, *rows: dict) -> None:
    pl.DataFrame([{**r, "match_id": 1} for r in rows]).write_parquet(path)


class TestBuildPlayerSeasonIOWrapper:
    """Unit test for the thin I/O wrapper — no real pipeline data required."""

    def test_reads_input_and_writes_output(self, tmp_path):
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "player_season_stats.parquet"
        _write_input(input_path, _match_row(player_id=99, minutes_played=90.0,
                                            minutes_dm_cm=90.0))
        root, css = _raw_fixture(tmp_path)

        result = build_player_season(input_path=input_path, output_path=output_path,
                                     competition_seasons=css, data_root=root)

        assert output_path.exists(), "build_player_season must write the output parquet"
        on_disk = pl.read_parquet(output_path)
        assert len(on_disk) == len(result)
        assert on_disk["player_id"][0] == 99

    def test_output_columns_match_return_value(self, tmp_path):
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "player_season_stats.parquet"
        _write_input(input_path, _match_row(player_id=1, minutes_played=90.0,
                                            minutes_dm_cm=90.0))
        root, css = _raw_fixture(tmp_path)

        result = build_player_season(input_path=input_path, output_path=output_path,
                                     competition_seasons=css, data_root=root)
        on_disk = pl.read_parquet(output_path)

        assert set(on_disk.columns) == set(result.columns)

    def test_creates_output_directory_if_missing(self, tmp_path):
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "subdir" / "nested" / "out.parquet"
        _write_input(input_path, _match_row(player_id=1, minutes_played=90.0,
                                            minutes_dm_cm=90.0))
        root, css = _raw_fixture(tmp_path)

        build_player_season(input_path=input_path, output_path=output_path,
                            competition_seasons=css, data_root=root)

        assert output_path.exists()

    def test_coverage_disagreement_fails_before_writing(self, tmp_path):
        """ADR 003 §5: configured FULL but matches.json gives PARTIAL -> no output."""
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "out.parquet"
        _write_input(input_path, _match_row(player_id=1, minutes_played=90.0,
                                            minutes_dm_cm=90.0))
        root, _ = _raw_fixture(tmp_path)
        wrong = (CompetitionSeason(11, 27, "Fixture", 2, "FULL", "statsbomb"),)

        with pytest.raises(CoverageCheckError, match="configured FULL"):
            build_player_season(input_path=input_path, output_path=output_path,
                                competition_seasons=wrong, data_root=root)
        assert not output_path.exists()

    def test_missing_processed_match_fails_before_writing(self, tmp_path):
        """Processed match_ids must equal matches.json; a dropped match fails."""
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "out.parquet"
        _write_input(input_path, _match_row(player_id=1, minutes_played=90.0,
                                            minutes_dm_cm=90.0))
        root, css = _raw_fixture(tmp_path)
        (root / "matches" / "11" / "27.json").write_text(json.dumps([
            {"match_id": 1, "home_team": {"home_team_id": 1}, "away_team": {"away_team_id": 2}},
            {"match_id": 2, "home_team": {"home_team_id": 2}, "away_team": {"away_team_id": 1}},
        ]))
        full = (CompetitionSeason(11, 27, "Fixture", 2, "FULL", "statsbomb"),)

        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[2\], extra match_ids \[\]"):
            build_player_season(input_path=input_path, output_path=output_path,
                                competition_seasons=full, data_root=root)
        assert not output_path.exists()

    def test_team_in_two_competitions_fails_before_writing(self, tmp_path):
        """A team_id in two competitions of one season is a data error for leagues."""
        input_path = tmp_path / "player_match_stats.parquet"
        output_path = tmp_path / "out.parquet"
        root, _ = _raw_fixture(tmp_path)
        (root / "matches" / "2").mkdir()
        (root / "matches" / "2" / "27.json").write_text(json.dumps([{
            "match_id": 1, "home_team": {"home_team_id": 1}, "away_team": {"away_team_id": 2},
        }]))
        css = (
            CompetitionSeason(11, 27, "A", 2, "PARTIAL", "statsbomb"),
            CompetitionSeason(2, 27, "B", 2, "PARTIAL", "statsbomb"),
        )
        _write_input(
            input_path,
            _match_row(player_id=1, team_id=10, competition_id=11, minutes_dm_cm=90.0),
            _match_row(player_id=2, team_id=10, competition_id=2, minutes_dm_cm=90.0),
        )

        with pytest.raises(ValueError, match="more than one competition"):
            build_player_season(input_path=input_path, output_path=output_path,
                                competition_seasons=css, data_root=root)
        assert not output_path.exists()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not _MATCH_PARQUET.exists() or not _SEASON_PARQUET.exists(),
    reason="requires pipeline output — run build_player_match and build_player_season first",
)
class TestIntegration:
    @pytest.fixture(scope="class")
    @classmethod
    def frames(cls):
        match_df = pl.read_parquet(_MATCH_PARQUET)
        season_df = pl.read_parquet(_SEASON_PARQUET)
        return match_df, season_df

    def test_goals_reconcile(self, frames):
        match_df, season_df = frames
        assert season_df["goals"].sum() == match_df["goals"].sum()

    def test_minutes_reconcile(self, frames):
        match_df, season_df = frames
        assert season_df["minutes_played"].sum() == pytest.approx(
            match_df["minutes_played"].sum(), rel=1e-6
        )

    def test_passes_attempted_reconcile(self, frames):
        match_df, season_df = frames
        assert season_df["passes_attempted"].sum() == match_df["passes_attempted"].sum()

    def test_shots_reconcile(self, frames):
        match_df, season_df = frames
        assert season_df["shots"].sum() == match_df["shots"].sum()

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_starts_total(self, frames, ref):
        """Structural invariant (plan 003 §3): starts == matches × 2 teams × 11."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        assert m["match_id"].n_unique() == ref["base"]["matches"]
        assert season["starts"].sum() == ref["base"]["starts"]
        assert season["starts"].sum() == ref["base"]["matches"] * 2 * 11

    def test_unique_player_team_season_key(self, frames):
        """(player_id, team_id, competition_id, season_id) must be unique."""
        _, season_df = frames
        key_cols = ["player_id", "team_id", "competition_id", "season_id"]
        n_rows = len(season_df)
        n_unique = season_df.select(key_cols).unique().shape[0]
        assert n_rows == n_unique, (
            f"{n_rows - n_unique} duplicate (player, team, season) keys found"
        )

    def test_multi_team_primary_stint_exactly_one(self, frames):
        """Every multi-team player-season has exactly one is_primary_stint=True row."""
        _, season_df = frames
        multi = season_df.filter(pl.col("multi_team_season"))
        if len(multi) == 0:
            pytest.skip("no multi-team players in this dataset")
        key = ["player_id", "competition_id", "season_id"]
        primary_counts = (
            multi.filter(pl.col("is_primary_stint"))
            .group_by(key)
            .agg(pl.len().alias("n_primary"))
        )
        bad = primary_counts.filter(pl.col("n_primary") != 1)
        assert len(bad) == 0, f"Player-seasons with ≠1 primary stints: {bad}"

    def test_primary_stint_unique_per_competition_season(self, frames):
        """ADR 002 §29: exactly one is_primary_stint row per
        (player_id, competition_id, season_id, source). Not unique per
        (player_id, season_id, source): cross-league movers have one per league."""
        _, season_df = frames
        key = ["player_id", "competition_id", "season_id", "source"]
        counts = season_df.group_by(key).agg(pl.col("is_primary_stint").sum().alias("n"))
        assert counts.filter(pl.col("n") != 1).height == 0

    def test_in_percentile_pool_matches_config(self, frames):
        """ADR 003 §5: constant per competition-season, True iff configured FULL."""
        _, season_df = frames
        got = {
            (r["competition_id"], r["season_id"], r["source"]): r["pool"]
            for r in season_df.group_by(["competition_id", "season_id", "source"])
            .agg(pl.col("in_percentile_pool").unique().alias("pool"))
            .iter_rows(named=True)
        }
        expected = {
            (cs.competition_id, cs.season_id, cs.source): [cs.coverage == "FULL"]
            for cs in COMPETITION_SEASONS
        }
        assert got == expected

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_crosses_reconcile(self, frames, ref):
        """Season crosses equal match-level crosses and the spike reference figures."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        assert season["crosses_attempted"].sum() == m["crosses_attempted"].sum()
        assert season["crosses_attempted"].sum() == ref["base"]["crosses_attempted"]
        assert season["crosses_completed"].sum() == ref["base"]["crosses_completed"]

    # ADR 002 §15, §19, §30 (2026-09-29): per-league unknown-outcome crosses,
    # unknown-outcome long passes and post-fix shots_on_target.
    # crosses_unknown_outcome is not in the reference files (plan 004 H1 figures);
    # long_passes_unknown_outcome and shots_on_target are read from the references,
    # which the independent spikes write (plan 005 M4b2: the long-pass figures changed
    # with the ADR 004 D14 restart rule).
    _ADR_H1_CROSSES_UNKNOWN = {(11, 27): 139, (2, 27): 64, (12, 27): 182, (7, 27): 101, (9, 27): 3}

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_h1_counts_match_adr_and_ratios_recompute(self, frames, ref):
        """Unknown-outcome counts and shots_on_target equal the expected figures at
        match and season level (long passes and shots on target from the reference
        totals; crosses from the plan 004 H1 figures), and completion
        ratios recompute from stored counts with unknown outcomes excluded from the
        denominator. The ADR figures were measured by this same pipeline and the
        recompute uses the pipeline's formula; independent verification against raw
        JSON is plan 004 H2."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        crosses_unk = self._ADR_H1_CROSSES_UNKNOWN[(ref["competition_id"], ref["season_id"])]
        long_unk = ref["chunk_a"]["long_passes_unknown_outcome"]
        sot = ref["core"]["shots_on_target"]
        for df in (m, season):
            assert df["crosses_unknown_outcome"].sum() == crosses_unk
            assert df["long_passes_unknown_outcome"].sum() == long_unk
            assert df["shots_on_target"].sum() == sot
        # An unknown outcome is never also a completion.
        assert season.filter(
            pl.col("crosses_completed") + pl.col("crosses_unknown_outcome")
            > pl.col("crosses_attempted")).height == 0
        assert season.filter(
            pl.col("long_passes_completed") + pl.col("long_passes_unknown_outcome")
            > pl.col("long_passes_attempted")).height == 0
        known = season.filter(pl.col("crosses_attempted") - pl.col("crosses_unknown_outcome") > 0)
        expected = known["crosses_completed"] / (known["crosses_attempted"] - known["crosses_unknown_outcome"])
        assert (known["cross_completion_pct"] - expected).abs().max() < 1e-9
        assert season.filter(
            (pl.col("crosses_attempted") == pl.col("crosses_unknown_outcome"))
            & pl.col("cross_completion_pct").is_not_null()).height == 0

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_chunk_a_metrics_reconcile(self, frames, ref):
        """Season totals must match raw event counts from spikes/verify_chunk_a_metrics.py
        (tests/fixtures/reference_totals) and equal match-level totals."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        for col, expected in ref["chunk_a"].items():
            assert season[col].sum() == expected, f"{col}: {season[col].sum()} != {expected}"
            assert season[col].sum() == m[col].sum(), f"{col}: season sum != match sum"

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_chunk_b_metrics_reconcile(self, frames, ref):
        """Chunk B totals must match spikes/verify_chunk_b_metrics.py (ADR 002 §§25-27,
        ADR 004 D14/D16), which applies its own RESTART_TYPES exclusion (set pieces and
        throw-ins) without importing entries.py, and equal match-level totals."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        for col, expected in ref["chunk_b"].items():
            assert season[col].sum() == pytest.approx(expected, abs=0.001), col
            assert season[col].sum() == pytest.approx(m[col].sum(), abs=0.001), (
                f"{col}: season sum != match sum"
            )

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_core_metrics_reconcile(self, frames, ref):
        """Core DNA totals must match spikes/verify_core_metrics.py (plan 004 H2), which
        re-derives them from raw StatsBomb JSON without importing eleveniq, and equal
        match-level totals. Exact for counts; abs=0.001 for the float xG columns (the
        references are rounded to 4 decimals). The minutes columns are checked in
        test_core_minutes_reconcile."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        for col, expected in ref["core"].items():
            if col.startswith("minutes_"):
                continue
            if col in ("xg", "np_xg"):
                assert season[col].sum() == pytest.approx(expected, abs=0.001), col
                assert season[col].sum() == pytest.approx(m[col].sum(), abs=0.001), (
                    f"{col}: season sum != match sum"
                )
            else:
                assert season[col].sum() == expected, f"{col}: {season[col].sum()} != {expected}"
                assert season[col].sum() == m[col].sum(), f"{col}: season sum != match sum"

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_core_minutes_season_equals_match(self, frames, ref):
        """minutes_played and the six minutes_<group> columns: the season totals
        equal the match-level totals (the aggregation loses no minutes)."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        for col in ref["core"]:
            if col.startswith("minutes_"):
                assert season[col].sum() == pytest.approx(m[col].sum(), abs=0.001), (
                    f"{col}: season sum != match sum"
                )

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_core_minutes_reconcile(self, frames, ref):
        """minutes_played and minutes_<group> must match the period-aware spike totals
        (ADR 002 §31.6), abs=0.001 (the references are rounded to 4 decimals), and
        Σ match_duration (one value per match) must equal base.match_duration."""
        match_df, season_df = frames
        m, season = _league(match_df, ref), _league(season_df, ref)
        for col, expected in ref["core"].items():
            if col.startswith("minutes_"):
                assert season[col].sum() == pytest.approx(expected, abs=0.001), col
        per_match = m.group_by("match_id").agg(pl.col("match_duration").first())
        assert per_match["match_duration"].sum() == pytest.approx(
            ref["base"]["match_duration"], abs=0.001)

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_team_minutes_within_eleven_times_duration(self, frames, ref):
        """ADR 002 §3 structural check, on the output: per team-match, summed minutes
        never exceed 11 x the stored match_duration, and no player exceeds it."""
        match_df, _ = frames
        m = _league(match_df, ref)
        assert m.group_by("match_id").agg(pl.col("match_duration").n_unique())[
            "match_duration"].max() == 1, "match_duration differs within a match"
        assert m.filter(pl.col("minutes_played") > pl.col("match_duration") + 1e-6).height == 0
        per_team = m.group_by("match_id", "team_id").agg(
            pl.col("minutes_played").sum().alias("team"),
            pl.col("match_duration").first(),
        )
        over = per_team.filter(pl.col("team") > 11 * pl.col("match_duration") + 1e-6)
        assert over.height == 0, over

    @pytest.mark.parametrize("ref", _REFERENCES, ids=_REF_IDS)
    def test_chunk_b_xg_assisted_shot_side_reconcile(self, frames, ref):
        """Independent shot-side xGA (from raw StatsBomb JSON) must equal season xg_assisted total."""
        import json as _json
        _, season_df = frames
        season_df = _league(season_df, ref)
        _raw = Path(__file__).resolve().parents[1] / "data" / "raw" / "statsbomb"
        matches_path = _raw / "matches" / str(ref["competition_id"]) / f"{ref['season_id']}.json"
        if not matches_path.exists():
            pytest.skip("StatsBomb raw data not available for shot-side reconciliation")
        with open(matches_path) as f:
            matches = _json.load(f)
        shot_side_xga = 0.0
        missing_xg_count = 0
        for m in matches:
            event_path = _raw / "events" / f"{m['match_id']}.json"
            with open(event_path) as f:
                events = _json.load(f)
            for ev in events:
                if ev.get("type", {}).get("name") == "Shot":
                    shot = ev.get("shot", {})
                    if shot.get("key_pass_id") is not None:
                        xg = shot.get("statsbomb_xg")
                        if xg is None:
                            missing_xg_count += 1
                            continue
                        shot_side_xga += xg
        assert missing_xg_count == 0, (
            f"{missing_xg_count} assisted shot(s) with missing statsbomb_xg; "
            "pipeline excludes them — a non-zero count means the shot-side total "
            "and the pipeline total would diverge silently"
        )
        assert abs(shot_side_xga - season_df["xg_assisted"].sum()) < 0.001, (
            f"shot-side xGA {shot_side_xga:.4f} != season xg_assisted "
            f"{season_df['xg_assisted'].sum():.4f}"
        )

    @pytest.mark.parametrize("which", ["match", "season"])
    def test_open_play_restart_parts_add_up_on_every_row(self, frames, which):
        """ADR 004 §9 per row (the reference totals only check league sums, where
        errors of opposite sign could cancel): op + restart = total, counts
        exactly and xG within float rounding; no part is null or negative."""
        df = frames[0] if which == "match" else frames[1]
        for col in ("key_passes", "assists", "xg_assisted"):
            op, rs = df[f"op_{col}"], df[f"restart_{col}"]
            assert op.null_count() == 0 and rs.null_count() == 0, col
            assert (op >= 0).all() and (rs >= 0).all(), col
            diff = (op + rs - df[col]).abs()
            tol = 0 if col != "xg_assisted" else 1e-9
            bad = df.filter(diff > tol)
            assert bad.is_empty(), f"{col}: {len(bad)} rows where op + restart != total"

    def test_xg_split_agrees_with_key_pass_split_on_every_match_row(self, frames):
        """The count split (_aggregate_events) and the xG split (xg_assisted.py) are
        classified in two places: a match row with restart (or open-play) xG
        assisted must have at least one restart (or open-play) key pass."""
        match_df, _ = frames
        for part in ("op", "restart"):
            bad = match_df.filter((pl.col(f"{part}_xg_assisted") > 0)
                                  & (pl.col(f"{part}_key_passes") == 0))
            assert bad.is_empty(), (
                f"{len(bad)} match rows with {part}_xg_assisted > 0 but no {part} key pass")


# ---------------------------------------------------------------------------
# TestSeasonOutputSchema — new columns added in this chunk
# ---------------------------------------------------------------------------

class TestSeasonOutputSchema:
    """
    Verify that new columns introduced in this chunk appear in the output
    DataFrame and carry the expected semantics.
    """

    def test_per_group_minute_columns_present(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        for col in ("minutes_gk", "minutes_cb", "minutes_fb_wb",
                    "minutes_dm_cm", "minutes_am_winger", "minutes_st",
                    "minutes_unknown"):
            assert col in result.columns, f"Expected column {col!r} in season output"

    def test_per_group_minute_columns_sum_to_minutes_played(self):
        rows = [
            _match_row(player_id=1, position_group="CB",
                       minutes_played=60.0, minutes_cb=60.0),
            _match_row(player_id=1, position_group="DM-CM",
                       minutes_played=90.0, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        group_cols = ["minutes_gk", "minutes_cb", "minutes_fb_wb", "minutes_dm_cm",
                      "minutes_am_winger", "minutes_st", "minutes_unknown"]
        group_total = sum(row[c] for c in group_cols)
        assert group_total == pytest.approx(row["minutes_played"])

    def test_crosses_attempted_column_present(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "crosses_attempted" in result.columns

    def test_crosses_completed_column_present(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "crosses_completed" in result.columns

    def test_crosses_attempted_p90_column_present(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "crosses_attempted_p90" in result.columns

    def test_cross_completion_pct_column_present(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "cross_completion_pct" in result.columns


# ---------------------------------------------------------------------------
# TestSourceIsolation — ADR 002 §10 single-source rule
# ---------------------------------------------------------------------------

# The same competition-season configured under two sources, so cross-source
# rows pass the in_percentile_pool config join (ADR 003 §5).
_TWO_SOURCES = (
    CompetitionSeason(11, 27, "La Liga (statsbomb)", 20, "FULL", "statsbomb"),
    CompetitionSeason(11, 27, "La Liga (sportmonks)", 20, "FULL", "sportmonks"),
)


class TestSourceIsolation:
    """
    ADR 002 §10: 'source' is part of the group key (_GROUP_KEY).
    The same player_id with different source values must produce SEPARATE
    season rows and must NEVER be merged into a single aggregated row.
    """

    def test_same_player_different_sources_produce_separate_rows(self):
        rows = [
            _match_row(player_id=1, source="statsbomb", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=1),
            _match_row(player_id=1, source="sportmonks", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=2),
        ]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        player_rows = result.filter(pl.col("player_id") == 1)
        assert len(player_rows) == 2, (
            "Same player_id with different 'source' values must produce 2 rows, "
            "not be merged — single-source rule (ADR 002 §10)"
        )

    def test_source_column_preserved_in_output(self):
        rows = [_match_row(player_id=1, source="statsbomb", minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        assert "source" in result.columns
        assert result.filter(pl.col("player_id") == 1)["source"][0] == "statsbomb"

    def test_separate_sources_have_independent_totals(self):
        """Goals from different sources must never be summed together."""
        rows = [
            _match_row(player_id=1, source="statsbomb", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=1),
            _match_row(player_id=1, source="sportmonks", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=3),
        ]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        player_rows = result.filter(pl.col("player_id") == 1).sort("source")
        # Alphabetically: "sportmonks" < "statsbomb"
        sportmonks_row = player_rows.filter(pl.col("source") == "sportmonks").row(0, named=True)
        statsbomb_row  = player_rows.filter(pl.col("source") == "statsbomb").row(0, named=True)
        assert sportmonks_row["goals"] == 3
        assert statsbomb_row["goals"] == 1

    def test_same_source_same_player_merged_into_one_row(self):
        """Two matches from same source for same player must be merged (control case)."""
        rows = [
            _match_row(player_id=1, source="statsbomb", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=1),
            _match_row(player_id=1, source="statsbomb", minutes_played=90.0,
                       minutes_dm_cm=90.0, goals=2),
        ]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        player_rows = result.filter(pl.col("player_id") == 1)
        assert len(player_rows) == 1, (
            "Same player_id and source must produce exactly one season row"
        )
        assert player_rows["goals"][0] == 3

    def test_different_sources_not_multi_team_season(self):
        """
        The same player_id appearing in two sources for the same season must
        produce two rows each with multi_team_season=False, not True.
        multi_team_season tracks team transfers within one source, not
        cross-source appearances.
        """
        rows = [
            _match_row(player_id=1, team_id=10, source="statsbomb",
                       minutes_played=900.0, minutes_dm_cm=900.0),
            _match_row(player_id=1, team_id=10, source="sportmonks",
                       minutes_played=900.0, minutes_dm_cm=900.0),
        ]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        for row in result.filter(pl.col("player_id") == 1).iter_rows(named=True):
            assert row["multi_team_season"] is False, (
                f"source={row['source']!r}: cross-source rows must not be "
                "flagged as multi_team_season"
            )

    def test_player_season_minutes_total_computed_per_source(self):
        """
        player_season_minutes_total must be the within-source season total,
        not the sum across both sources.
        """
        rows = [
            _match_row(player_id=1, team_id=10, source="statsbomb",
                       minutes_played=600.0, minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=20, source="statsbomb",
                       minutes_played=400.0, minutes_dm_cm=400.0),
            _match_row(player_id=1, team_id=10, source="sportmonks",
                       minutes_played=800.0, minutes_dm_cm=800.0),
        ]
        result = aggregate_player_season(_df(rows), competition_seasons=_TWO_SOURCES)
        sb = result.filter(
            (pl.col("player_id") == 1) & (pl.col("source") == "statsbomb")
        )
        sm = result.filter(
            (pl.col("player_id") == 1) & (pl.col("source") == "sportmonks")
        )
        # statsbomb has two team stints: 600 + 400 = 1000 total
        for row in sb.iter_rows(named=True):
            assert row["player_season_minutes_total"] == pytest.approx(1000.0), (
                f"statsbomb team_id={row['team_id']}: expected total=1000, "
                f"got {row['player_season_minutes_total']}"
            )
        # sportmonks has one stint: 800 total
        assert sm["player_season_minutes_total"][0] == pytest.approx(800.0)


# ---------------------------------------------------------------------------
# TestSeasonPositionGroupEdgeCases
# ---------------------------------------------------------------------------

class TestSeasonPositionGroupEdgeCases:
    """
    Edge cases for _season_position_group that are not covered by TestPositionGroup.
    """

    def test_all_zero_group_minutes_resolves_to_alphabetically_first(self):
        """
        If somehow all per-group minute totals are zero (e.g., 0-minute row),
        the tie-break rule should still resolve deterministically to the
        alphabetically first group name: 'AM-Winger'.
        """
        # Build a row where minutes_played=0 and all group cols=0.
        base = _match_row(player_id=1, minutes_played=0.0)
        # Force all group columns to 0 regardless of helper logic.
        zero_row = {**base,
                    "minutes_gk": 0.0, "minutes_cb": 0.0, "minutes_fb_wb": 0.0,
                    "minutes_dm_cm": 0.0, "minutes_am_winger": 0.0,
                    "minutes_st": 0.0, "minutes_unknown": 0.0}
        result = aggregate_player_season(_df([zero_row]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        # All zeros → tie; alphabetically first = "AM-Winger"
        assert row["position_group"] == "AM-Winger", (
            "All-zero group minutes must resolve to alphabetically first group 'AM-Winger'"
        )

    def test_multi_match_position_switches_max_minutes_wins(self):
        """
        Player plays 3 matches: 2 as DM-CM (45 min each) and 1 as AM-Winger (91 min).
        Season group = AM-Winger (91 > 90 total DM-CM minutes).
        """
        rows = [
            _match_row(player_id=1, position_group="DM-CM",
                       minutes_played=45.0, minutes_dm_cm=45.0),
            _match_row(player_id=1, position_group="DM-CM",
                       minutes_played=45.0, minutes_dm_cm=45.0),
            _match_row(player_id=1, position_group="AM-Winger",
                       minutes_played=91.0, minutes_am_winger=91.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "AM-Winger"
        assert row["minutes_dm_cm"] == pytest.approx(90.0)
        assert row["minutes_am_winger"] == pytest.approx(91.0)

    def test_three_way_tie_resolves_alphabetically(self):
        """
        Three groups with equal minutes — result must be alphabetically first.
        CB, DM-CM, ST each with 30 min → 'CB' wins ('CB' < 'DM-CM' < 'ST').
        """
        rows = [
            _match_row(player_id=1, position_group="CB",
                       minutes_played=30.0, minutes_cb=30.0),
            _match_row(player_id=1, position_group="DM-CM",
                       minutes_played=30.0, minutes_dm_cm=30.0),
            _match_row(player_id=1, position_group="ST",
                       minutes_played=30.0, minutes_st=30.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "CB"


# ---------------------------------------------------------------------------
# TestDisplayOnlyColsNoPer90 — ADR 002 §15 / _DISPLAY_ONLY_COLS contract
# ---------------------------------------------------------------------------

class TestDisplayOnlyColsNoPer90:
    """
    ADR 002 §15: crosses_completed is in _DISPLAY_ONLY_COLS, NOT _COUNT_COLS.
    Therefore crosses_completed_p90 must NOT appear in the season output.

    The same rule applies to passes_unknown_outcome and shots_missing_xg —
    they are totals preserved for recalculation, not rate metrics.

    Asserting the absence of these columns prevents accidental promotion of a
    display-only column into a per-90 metric during refactoring.
    """

    def test_crosses_completed_has_no_p90_column(self):
        # ADR 002 §15: cross_completion_pct (ratio) is the meaningful rate metric;
        # crosses_completed_p90 would be redundant and invite misuse.
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0,
                           crosses_attempted=4, crosses_completed=1)]
        result = aggregate_player_season(_df(rows))
        assert "crosses_completed_p90" not in result.columns, (
            "crosses_completed is in _DISPLAY_ONLY_COLS (ADR 002 §15); "
            "it must not have a per-90 companion column"
        )

    def test_passes_unknown_outcome_has_no_p90_column(self):
        # passes_unknown_outcome is a data-quality counter, not a rate metric.
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0,
                           passes_unknown_outcome=2)]
        result = aggregate_player_season(_df(rows))
        assert "passes_unknown_outcome_p90" not in result.columns, (
            "passes_unknown_outcome is in _DISPLAY_ONLY_COLS; "
            "it must not have a per-90 companion column"
        )

    def test_shots_missing_xg_has_no_p90_column(self):
        # shots_missing_xg is a data-quality counter, not a rate metric.
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0,
                           shots_missing_xg=1)]
        result = aggregate_player_season(_df(rows))
        assert "shots_missing_xg_p90" not in result.columns, (
            "shots_missing_xg is in _DISPLAY_ONLY_COLS; "
            "it must not have a per-90 companion column"
        )

    def test_ball_recoveries_failed_has_no_p90_column(self):
        # ADR 002 §21: ball_recoveries_failed is a display-only denominator;
        # ball_recovery success rate is a derived ratio, not a raw per-90 metric.
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0,
                           ball_recoveries=5, ball_recoveries_failed=2)]
        result = aggregate_player_season(_df(rows))
        assert "ball_recoveries_failed" in result.columns, (
            "ball_recoveries_failed must appear in season output"
        )
        assert "ball_recoveries_failed_p90" not in result.columns, (
            "ball_recoveries_failed is in _DISPLAY_ONLY_COLS; "
            "it must not have a per-90 companion column"
        )
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["ball_recoveries_failed"] == 2

    def test_display_only_cols_list_does_not_overlap_count_cols(self):
        # _DISPLAY_ONLY_COLS and _COUNT_COLS must be disjoint — a column in both
        # would be computed twice (wrong) or produce duplicate columns.
        overlap = set(_COUNT_COLS) & set(_DISPLAY_ONLY_COLS)
        assert overlap == set(), (
            f"Columns appear in both _COUNT_COLS and _DISPLAY_ONLY_COLS: {overlap}"
        )


# ---------------------------------------------------------------------------
# TestXgPer90Column — xg_p90 is computed outside _COUNT_COLS
# ---------------------------------------------------------------------------

class TestXgPer90Column:
    """
    xg is a Float64 column (not Int32 like the count columns) and is not in
    _COUNT_COLS.  aggregate_player_season computes xg_p90 in a separate
    expression.  This class verifies the column is present and correct.
    """

    def test_xg_p90_column_present(self):
        rows = [_match_row(player_id=1, xg=0.5, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "xg_p90" in result.columns, (
            "xg_p90 must be present in season output (computed separately from _COUNT_COLS)"
        )

    def test_xg_p90_correct_value(self):
        # 1.8 xG in 45 min → xg_p90 = 1.8/45*90 = 3.6
        rows = [_match_row(player_id=1, xg=1.8, minutes_played=45.0,
                           minutes_dm_cm=45.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_p90"] == pytest.approx(3.6)

    def test_xg_p90_null_when_zero_minutes(self):
        # ADR 002 §5: per-90 must be null, not infinity, for 0-minute rows.
        base = _match_row(player_id=1, xg=0.3, minutes_played=90.0,
                          minutes_dm_cm=90.0)
        zero = {**base, "minutes_played": 0.0, "minutes_dm_cm": 0.0}
        result = aggregate_player_season(_df([zero]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_p90"] is None

    def test_xg_p90_accumulates_across_matches(self):
        # Two matches: 0.2 xG in 45 min + 0.4 xG in 45 min = 0.6 xG / 90 min → 0.6
        rows = [
            _match_row(player_id=1, xg=0.2, minutes_played=45.0,
                       minutes_dm_cm=45.0),
            _match_row(player_id=1, xg=0.4, minutes_played=45.0,
                       minutes_dm_cm=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_p90"] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# TestAllCountColsHavePer90 — ADR 002 §6b completeness check
# ---------------------------------------------------------------------------

class TestAllCountColsHavePer90:
    """
    ADR 002 §6b: season output must contain per-90 columns for every count
    metric.  This test enumerates _COUNT_COLS and asserts that {col}_p90 exists
    so that a new count column added without a matching per-90 expression is
    caught immediately.
    """

    def test_every_count_col_has_p90_sibling(self):
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        missing_p90 = [c for c in _COUNT_COLS if f"{c}_p90" not in result.columns]
        assert missing_p90 == [], (
            f"Count columns without a per-90 companion: {missing_p90} "
            f"(ADR 002 §6b requires per-90 for every count metric)"
        )


# ---------------------------------------------------------------------------
# TestPassCompletionPctFormula — ADR 002 §14 denominator formula
# ---------------------------------------------------------------------------

class TestPassCompletionPctFormula:
    """
    ADR 002 §14:
        pass_completion_pct = passes_completed / (passes_attempted − passes_unknown_outcome)

    Unknown-outcome passes are EXCLUDED from the denominator.  A naive
    implementation of passes_completed / passes_attempted would produce a
    different (lower) result when unknown-outcome passes are present.
    """

    def test_unknown_passes_excluded_from_denominator(self):
        # 50 attempted, 40 completed, 5 unknown.
        # Correct:  40 / (50 - 5) = 40/45 ≈ 0.889
        # Wrong:    40 / 50 = 0.800
        rows = [_match_row(
            player_id=1,
            passes_attempted=50, passes_completed=40, passes_unknown_outcome=5,
            minutes_dm_cm=90.0,
        )]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        expected = 40 / (50 - 5)
        assert row["pass_completion_pct"] == pytest.approx(expected), (
            f"Expected pass_completion_pct={expected:.4f} "
            f"(denominator excludes unknown passes per ADR 002 §14) "
            f"but got {row['pass_completion_pct']:.4f}"
        )

    def test_no_unknown_passes_full_denominator(self):
        # 50 attempted, 40 completed, 0 unknown → 40/50 = 0.80 (baseline).
        rows = [_match_row(
            player_id=1,
            passes_attempted=50, passes_completed=40, passes_unknown_outcome=0,
            minutes_dm_cm=90.0,
        )]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["pass_completion_pct"] == pytest.approx(40 / 50)

    def test_season_total_unknown_passes_excluded(self):
        # Player has two matches each with unknown-outcome passes.
        # Season: 100 attempted, 80 completed, 10 unknown.
        # pass_completion_pct = 80 / (100 - 10) = 80/90 ≈ 0.889
        rows = [
            _match_row(player_id=1,
                       passes_attempted=50, passes_completed=40,
                       passes_unknown_outcome=5, minutes_dm_cm=90.0),
            _match_row(player_id=1,
                       passes_attempted=50, passes_completed=40,
                       passes_unknown_outcome=5, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["passes_unknown_outcome"] == 10
        assert row["pass_completion_pct"] == pytest.approx(80 / 90)


# ---------------------------------------------------------------------------
# TestDisplayOnlyColsSummed — _DISPLAY_ONLY_COLS are aggregated (not dropped)
# ---------------------------------------------------------------------------

class TestDisplayOnlyColsSummed:
    """
    _DISPLAY_ONLY_COLS (shots_missing_xg, passes_unknown_outcome, crosses_completed)
    are summed across matches so the season total is available for recalculation.
    ADR 002 §6b: always preserve numerators and denominators alongside derived
    figures so metrics can be recalculated without re-downloading source data.
    """

    def test_shots_missing_xg_summed(self):
        rows = [
            _match_row(player_id=1, shots_missing_xg=2, minutes_dm_cm=90.0),
            _match_row(player_id=1, shots_missing_xg=1, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["shots_missing_xg"] == 3

    def test_passes_unknown_outcome_summed(self):
        rows = [
            _match_row(player_id=1, passes_unknown_outcome=3, minutes_dm_cm=90.0),
            _match_row(player_id=1, passes_unknown_outcome=4, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["passes_unknown_outcome"] == 7

    def test_crosses_completed_summed(self):
        # crosses_completed is in _DISPLAY_ONLY_COLS — season total preserved.
        rows = [
            _match_row(player_id=1, crosses_attempted=5, crosses_completed=2,
                       minutes_dm_cm=90.0),
            _match_row(player_id=1, crosses_attempted=3, crosses_completed=1,
                       minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        # crosses_completed is the season total (ADR 002 §6b: preserve numerator).
        assert row["crosses_completed"] == 3

    def test_display_only_cols_present_in_output(self):
        # All three display-only columns must appear in the output schema.
        rows = [_match_row(player_id=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        for col in _DISPLAY_ONLY_COLS:
            assert col in result.columns, (
                f"Display-only column {col!r} must be present in season output "
                f"(ADR 002 §6b: preserve numerators and denominators)"
            )


# ---------------------------------------------------------------------------
# TestMeetsMinMinutesPerStint — ADR 002 §17 per-stint evaluation contract
# ---------------------------------------------------------------------------

class TestMeetsMinMinutesPerStint:
    """
    ADR 002 §17: meets_min_minutes is computed from minutes_played (the per-stint
    total), NOT from player_season_minutes_total.  For a split-season player,
    each team stint is evaluated independently.

    This is the documented default behaviour until Milestone 4 decides how to
    handle split-season players in percentile rankings.

    Key scenario from ADR 002 §17:
      Club A: 500 min → meets_min_minutes = False (< 900)
      Club B: 450 min → meets_min_minutes = False (< 900)
      Combined: 950 min — but NEITHER row becomes True under the per-stint rule.
    """

    def test_split_season_both_stints_fail_when_each_below_threshold(self):
        # ADR 002 §17: per-stint minutes used, not combined total.
        # 500 + 450 = 950 (>= 900), but each stint is below 900 individually.
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=20, minutes_played=450.0,
                       minutes_dm_cm=450.0),
        ]
        result = aggregate_player_season(_df(rows), min_minutes=900)
        player_rows = result.filter(pl.col("player_id") == 1)
        assert len(player_rows) == 2
        for row in player_rows.iter_rows(named=True):
            assert row["meets_min_minutes"] is False, (
                f"team_id={row['team_id']}: per-stint {row['minutes_played']} min < 900 "
                f"must be meets_min_minutes=False even though combined total >= 900 "
                f"(ADR 002 §17 per-stint rule)"
            )

    def test_split_season_primary_stint_passes_when_above_threshold(self):
        # If one stint alone exceeds the threshold, that stint is True; the other
        # is still evaluated on its own minutes.
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=950.0,
                       minutes_dm_cm=950.0),
            _match_row(player_id=1, team_id=20, minutes_played=200.0,
                       minutes_dm_cm=200.0),
        ]
        result = aggregate_player_season(_df(rows), min_minutes=900)
        primary = result.filter(
            (pl.col("player_id") == 1) & (pl.col("team_id") == 10)
        ).row(0, named=True)
        secondary = result.filter(
            (pl.col("player_id") == 1) & (pl.col("team_id") == 20)
        ).row(0, named=True)
        assert primary["meets_min_minutes"] is True
        assert secondary["meets_min_minutes"] is False

    def test_single_team_at_threshold_passes(self):
        # Baseline: a player who has exactly the threshold in one team → True.
        rows = [_match_row(player_id=1, team_id=10, minutes_played=900.0,
                           minutes_dm_cm=900.0)]
        result = aggregate_player_season(_df(rows), min_minutes=900)
        assert result.filter(pl.col("player_id") == 1)["meets_min_minutes"][0] is True

    def test_player_season_minutes_total_does_not_affect_meets_min_minutes(self):
        """
        player_season_minutes_total is stored for display / future use, but
        meets_min_minutes must reflect only minutes_played (per-stint total).
        This test asserts the two columns can disagree.
        """
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=20, minutes_played=450.0,
                       minutes_dm_cm=450.0),
        ]
        result = aggregate_player_season(_df(rows), min_minutes=900)
        for row in result.filter(pl.col("player_id") == 1).iter_rows(named=True):
            # Combined total is 950 >= 900 but per-stint is < 900.
            assert row["player_season_minutes_total"] == pytest.approx(950.0)
            assert row["meets_min_minutes"] is False, (
                "meets_min_minutes must use per-stint minutes_played, not total"
            )


# ---------------------------------------------------------------------------
# TestGkTieBreakEdgeCase — milestones.md tech-debt note
# ---------------------------------------------------------------------------

class TestGkTieBreakEdgeCase:
    """
    Tech-debt note (milestones.md): _season_position_group uses an alphabetical
    tie-break via POSITION_GROUP_COLUMNS insertion order.  AM-Winger sorts
    before GK alphabetically, so an exact equal-minutes tie between GK and
    AM-Winger resolves to AM-Winger — meaning is_gk=False for that row.

    This is a KNOWN edge case, not a bug fix target until GK scouting is in
    scope.  The test documents this behaviour so any future fix is deliberate.
    """

    def test_gk_equal_minutes_to_am_winger_resolves_to_am_winger(self):
        """
        KNOWN EDGE CASE (milestones.md tech debt): GK with exactly the same
        minutes as AM-Winger resolves to AM-Winger (alphabetically first).
        This means is_gk=False for a goalkeeper in this exact scenario.
        The test asserts the CURRENT behaviour, not the desired behaviour —
        any change must be deliberate and recorded in ADR 002.
        """
        rows = [
            _match_row(player_id=1, position_group="GK",
                       minutes_played=45.0, minutes_gk=45.0),
            _match_row(player_id=1, position_group="AM-Winger",
                       minutes_played=45.0, minutes_am_winger=45.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        # AM-Winger < GK alphabetically → AM-Winger wins the tie.
        assert row["position_group"] == "AM-Winger", (
            "KNOWN EDGE CASE: equal GK and AM-Winger minutes must resolve to "
            "AM-Winger (alphabetical tie-break, milestones.md tech debt). "
            "If this test fails, the behaviour has changed — update ADR 002."
        )
        assert row["is_gk"] is False, (
            "KNOWN EDGE CASE: is_gk must be False when tie-break assigns AM-Winger "
            "(milestones.md tech debt — no fix until GK scouting is in scope)"
        )

    def test_gk_more_minutes_than_am_winger_is_gk_true(self):
        """Control case: GK with strictly more minutes than AM-Winger must win."""
        rows = [
            _match_row(player_id=1, position_group="GK",
                       minutes_played=60.0, minutes_gk=60.0),
            _match_row(player_id=1, position_group="AM-Winger",
                       minutes_played=30.0, minutes_am_winger=30.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "GK"
        assert row["is_gk"] is True


# ---------------------------------------------------------------------------
# TestColumnMembership — spec-derived column list contracts
# ---------------------------------------------------------------------------

class TestColumnMembership:
    """
    ADR 002 §15: crosses_completed is in _DISPLAY_ONLY_COLS (not _COUNT_COLS),
    so it is stored as a season total but has no per-90 sibling.
    crosses_attempted is in _COUNT_COLS and gets a per-90 sibling.

    These tests assert the membership directly from the module-level lists so
    that accidental moves between the lists (during refactoring) are caught
    before they corrupt the schema.
    """

    def test_crosses_completed_in_display_only_cols(self):
        # ADR 002 §15: cross_completion_pct is the meaningful rate; storing
        # crosses_completed_p90 would be redundant and invite misuse.
        assert "crosses_completed" in _DISPLAY_ONLY_COLS, (
            "crosses_completed must be in _DISPLAY_ONLY_COLS (ADR 002 §15)"
        )

    def test_crosses_attempted_in_count_cols(self):
        # ADR 002 §15: crosses_attempted gets a per-90 companion (crosses_attempted_p90).
        assert "crosses_attempted" in _COUNT_COLS, (
            "crosses_attempted must be in _COUNT_COLS so it gets a per-90 column"
        )

    def test_crosses_completed_not_in_count_cols(self):
        # If crosses_completed were in _COUNT_COLS it would also generate a
        # crosses_completed_p90 column, violating the ADR 002 §15 rationale.
        assert "crosses_completed" not in _COUNT_COLS, (
            "crosses_completed must NOT be in _COUNT_COLS (ADR 002 §15 rationale)"
        )

    def test_crosses_attempted_not_in_display_only_cols(self):
        # crosses_attempted needs a per-90 column; it must not be in display-only.
        assert "crosses_attempted" not in _DISPLAY_ONLY_COLS, (
            "crosses_attempted must NOT be in _DISPLAY_ONLY_COLS"
        )


# ---------------------------------------------------------------------------
# TestSeasonTotalsNotMatchAverages — ADR 002 §6a: per-90 and ratios from totals
# ---------------------------------------------------------------------------

class TestSeasonTotalsNotMatchAverages:
    """
    ADR 002 §6a: season-level per-90 and rate metrics must be computed from
    season totals (sum of numerators / sum of denominators), never from the
    average of per-match values.

    These tests construct two-match scenarios where the two approaches diverge
    and assert the correct (totals-based) value.
    """

    def test_crosses_attempted_p90_from_season_totals(self):
        """
        ADR 002 §6a applied to crosses_attempted_p90:
          Match 1:  3 min, 2 crosses_attempted  → p90 = 60.0 (wrong per-match average method)
          Match 2: 87 min, 0 crosses_attempted  → p90 =  0.0
          Correct: (0+2) / (3+87) * 90 = 2.0   (season-total method)
          Wrong:   mean(60.0, 0.0) = 30.0
        """
        rows = [
            _match_row(player_id=1, minutes_played=3.0, crosses_attempted=2,
                       minutes_dm_cm=3.0),
            _match_row(player_id=1, minutes_played=87.0, crosses_attempted=0,
                       minutes_dm_cm=87.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["crosses_attempted_p90"] == pytest.approx(2.0), (
            f"crosses_attempted_p90 must use season total method (ADR 002 §6a); "
            f"expected 2.0 but got {row['crosses_attempted_p90']}"
        )

    def test_cross_completion_pct_from_season_totals_not_per_match_ratios(self):
        """
        ADR 002 §6b: cross_completion_pct = season_crosses_completed / season_crosses_attempted.
        It must NOT be the average of per-match completion percentages.

        Match 1:  2 attempted, 2 completed → match pct = 1.0
        Match 2: 10 attempted, 1 completed → match pct = 0.1
          Correct: (2+1) / (2+10) = 3/12 = 0.25
          Wrong:   mean(1.0, 0.1) = 0.55
        """
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=2, crosses_completed=2),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=10, crosses_completed=1),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["cross_completion_pct"] == pytest.approx(3 / 12), (
            f"cross_completion_pct must use season totals (ADR 002 §6b); "
            f"expected {3/12:.4f} but got {row['cross_completion_pct']}"
        )

    def test_xg_per_shot_from_season_totals_not_per_match_ratios(self):
        """
        ADR 002 §6b: xg_per_shot = season_xg / season_shots.
        It must NOT be the average of per-match xg_per_shot values.

        Match 1:  1 shot, 0.7 xG  → match xg/shot = 0.70
        Match 2: 10 shots, 0.3 xG → match xg/shot = 0.03
          Correct: (0.7+0.3) / (1+10) = 1.0/11 ≈ 0.0909
          Wrong:   mean(0.70, 0.03) = 0.365
        """
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       shots=1, xg=0.7),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       shots=10, xg=0.3),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_per_shot"] == pytest.approx(1.0 / 11, rel=1e-5), (
            f"xg_per_shot must use season totals (ADR 002 §6b); "
            f"expected {1/11:.5f} but got {row['xg_per_shot']}"
        )


# ---------------------------------------------------------------------------
# TestThreeTeamPlayerSeason — player transfers to two new clubs in one season
# ---------------------------------------------------------------------------

class TestThreeTeamPlayerSeason:
    """
    A player transferred twice during the season ends up at three clubs.
    This is an extreme but valid multi-team scenario.

    Contracts (ADR 002 §6 / build_player_season docstring):
    - Three separate season rows (one per team) are produced.
    - multi_team_season is True on all three rows.
    - is_primary_stint is True on exactly one row (the one with the most minutes).
    - player_season_minutes_total equals the sum of all three stints on every row.
    - If two teams have equal minutes the lower team_id wins is_primary_stint.
    """

    def _three_team_rows(self):
        return [
            _match_row(player_id=1, team_id=10, minutes_played=300.0,
                       minutes_dm_cm=300.0),
            _match_row(player_id=1, team_id=20, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=30, minutes_played=200.0,
                       minutes_dm_cm=200.0),
        ]

    def test_three_rows_produced(self):
        result = aggregate_player_season(_df(self._three_team_rows()))
        assert len(result.filter(pl.col("player_id") == 1)) == 3

    def test_all_three_rows_multi_team_true(self):
        result = aggregate_player_season(_df(self._three_team_rows()))
        for row in result.filter(pl.col("player_id") == 1).iter_rows(named=True):
            assert row["multi_team_season"] is True, (
                f"team_id={row['team_id']}: multi_team_season must be True for a 3-team player"
            )

    def test_exactly_one_primary_stint(self):
        result = aggregate_player_season(_df(self._three_team_rows()))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert len(primary) == 1, (
            f"Expected exactly 1 is_primary_stint=True row for 3-team player; "
            f"got {len(primary)}"
        )

    def test_primary_stint_is_most_minutes_team(self):
        # team 20 has 500 min — the most — and must be the primary stint.
        result = aggregate_player_season(_df(self._three_team_rows()))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert primary["team_id"][0] == 20, (
            "The team with 500 min (team_id=20) must be is_primary_stint=True, "
            "not the lower-id team with fewer minutes"
        )

    def test_player_season_minutes_total_is_sum_of_all_stints(self):
        # 300 + 500 + 200 = 1000 on every row
        result = aggregate_player_season(_df(self._three_team_rows()))
        for row in result.filter(pl.col("player_id") == 1).iter_rows(named=True):
            assert row["player_season_minutes_total"] == pytest.approx(1000.0), (
                f"team_id={row['team_id']}: player_season_minutes_total must be "
                f"sum of all stints (1000) but got {row['player_season_minutes_total']}"
            )

    def test_three_team_tiebreak_lower_id_wins_when_equal_minutes(self):
        """
        When two of the three teams have equal minutes (the most), the lower
        team_id must be is_primary_stint=True.
        """
        rows = [
            _match_row(player_id=1, team_id=10, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=20, minutes_played=500.0,
                       minutes_dm_cm=500.0),
            _match_row(player_id=1, team_id=30, minutes_played=200.0,
                       minutes_dm_cm=200.0),
        ]
        result = aggregate_player_season(_df(rows))
        primary = result.filter(
            (pl.col("player_id") == 1) & pl.col("is_primary_stint")
        )
        assert len(primary) == 1
        assert primary["team_id"][0] == 10, (
            "Teams 10 and 20 both have 500 min; team_id=10 must win the tie-break "
            "(lower team_id is the secondary rule per build_player_season docstring)"
        )


# ---------------------------------------------------------------------------
# TestUnknownPositionGroupFlag — UNKNOWN position group is not GK
# ---------------------------------------------------------------------------

class TestUnknownPositionGroupFlag:
    """
    ADR 002 §12: is_gk=True only for rows where position_group='GK'.
    A player whose season position_group resolves to 'UNKNOWN' (e.g. their
    position name is not in POSITION_GROUP_MAP) must have is_gk=False.

    This guards against a future accidental broadening of the is_gk condition
    (e.g., changing `== "GK"` to `!= "Outfield"`) that would misclassify
    unknown-position players.
    """

    def test_unknown_position_group_is_gk_false(self):
        # Build a row where all minutes are in the UNKNOWN group.
        base = _match_row(player_id=1, minutes_played=90.0)
        unknown_row = {
            **base,
            "minutes_gk": 0.0,
            "minutes_cb": 0.0,
            "minutes_fb_wb": 0.0,
            "minutes_dm_cm": 0.0,
            "minutes_am_winger": 0.0,
            "minutes_st": 0.0,
            "minutes_unknown": 90.0,
        }
        result = aggregate_player_season(_df([unknown_row]))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["position_group"] == "UNKNOWN", (
            "Sanity: player with 90 min in minutes_unknown must resolve to 'UNKNOWN'"
        )
        assert row["is_gk"] is False, (
            "ADR 002 §12: is_gk must be False for position_group='UNKNOWN'; "
            "UNKNOWN players are not goalkeepers and must not enter GK cohorts"
        )

    def test_unknown_position_meets_min_minutes_not_affected(self):
        # An UNKNOWN-position player with enough minutes still passes meets_min_minutes.
        base = _match_row(player_id=1, minutes_played=900.0)
        unknown_row = {
            **base,
            "minutes_gk": 0.0,
            "minutes_cb": 0.0,
            "minutes_fb_wb": 0.0,
            "minutes_dm_cm": 0.0,
            "minutes_am_winger": 0.0,
            "minutes_st": 0.0,
            "minutes_unknown": 900.0,
        }
        result = aggregate_player_season(_df([unknown_row]), min_minutes=900)
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["meets_min_minutes"] is True


# ---------------------------------------------------------------------------
# TestChunkBSeasonColumns — Chunk B column presence and invariants
# ---------------------------------------------------------------------------

class TestChunkBSeasonColumns:
    """
    ADR 002 §25–27: xg_assisted, xg_assisted_direct, and the four entry
    columns must be present in the season output with per-90 companions,
    and must obey the xg_assisted_direct ≤ xg_assisted invariant.
    """

    def test_xg_assisted_p90_present(self):
        rows = [_match_row(player_id=1, xg_assisted=0.5, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "xg_assisted_p90" in result.columns, (
            "xg_assisted must have a per-90 companion column"
        )

    def test_xg_assisted_direct_p90_present(self):
        rows = [_match_row(player_id=1, xg_assisted_direct=0.3, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "xg_assisted_direct_p90" in result.columns, (
            "xg_assisted_direct must have a per-90 companion column"
        )

    def test_entry_columns_p90_present(self):
        rows = [_match_row(player_id=1, final_third_entries_pass=3,
                           box_entries_carry=1, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        for col in ("final_third_entries_pass", "final_third_entries_carry",
                    "box_entries_pass", "box_entries_carry"):
            assert f"{col}_p90" in result.columns, (
                f"{col} must have a per-90 companion column (ADR 002 §27)"
            )

    def test_xg_assisted_p90_correct_value(self):
        # 0.9 xGA in 90 min → xg_assisted_p90 = 0.9
        rows = [_match_row(player_id=1, xg_assisted=0.9, minutes_played=90.0,
                           minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_assisted_p90"] == pytest.approx(0.9)

    def test_xg_assisted_direct_never_exceeds_xg_assisted_at_season_level(self):
        """ADR §26: xg_assisted_direct ≤ xg_assisted.  Season sum must respect this."""
        rows = [
            _match_row(player_id=1, xg_assisted=0.5, xg_assisted_direct=0.4,
                       minutes_dm_cm=90.0),
            _match_row(player_id=1, xg_assisted=0.3, xg_assisted_direct=0.1,
                       minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_assisted"] == pytest.approx(0.8)
        assert row["xg_assisted_direct"] == pytest.approx(0.5)
        assert row["xg_assisted_direct"] <= row["xg_assisted"], (
            "xg_assisted_direct must never exceed xg_assisted at season level"
        )

    def test_xg_assisted_p90_null_when_zero_minutes(self):
        # ADR 002 §5: per-90 must be null for 0-minute rows.
        rows = [_match_row(player_id=1, xg_assisted=0.3, minutes_played=0.0)]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["xg_assisted_p90"] is None, (
            "xg_assisted_p90 must be null when minutes_played == 0"
        )

    def test_final_third_entries_pass_accumulates_across_matches(self):
        # Two matches with 5 and 3 entries → season total = 8.
        rows = [
            _match_row(player_id=1, final_third_entries_pass=5, minutes_dm_cm=90.0),
            _match_row(player_id=1, final_third_entries_pass=3, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["final_third_entries_pass"] == 8

    def test_box_entries_carry_accumulates_across_matches(self):
        rows = [
            _match_row(player_id=1, box_entries_carry=2, minutes_dm_cm=90.0),
            _match_row(player_id=1, box_entries_carry=4, minutes_dm_cm=90.0),
        ]
        result = aggregate_player_season(_df(rows))
        row = result.filter(pl.col("player_id") == 1).row(0, named=True)
        assert row["box_entries_carry"] == 6


# ---------------------------------------------------------------------------
# TestNameVariants — plan 003 C3: season rows keyed by ids, not names
# ---------------------------------------------------------------------------

class TestNameVariants:
    """
    StatsBomb spells some names differently from match to match
    (e.g. "N'Golo Kanté" / "N''Golo Kanté"). A spelling change must never
    split one player's (or team's) season into two rows.
    """

    def test_player_two_spellings_one_row_with_summed_totals(self):
        rows = [
            _match_row(player_id=1, player_name="N'Golo Kanté", goals=1, minutes_played=90.0),
            _match_row(player_id=1, player_name="N'Golo Kanté", goals=0, minutes_played=80.0),
            _match_row(player_id=1, player_name="N''Golo Kanté", goals=2, minutes_played=70.0),
        ]
        result = aggregate_player_season(_df(rows))
        assert result.height == 1
        row = result.row(0, named=True)
        assert row["goals"] == 3
        assert row["minutes_played"] == pytest.approx(240.0)
        assert row["appearances"] == 3
        assert row["player_name"] == "N'Golo Kanté"
        assert row["player_name_variants"] == ["N''Golo Kanté", "N'Golo Kanté"]

    def test_doubled_apostrophe_normalised_even_when_most_frequent(self):
        rows = [
            _match_row(player_id=1, player_name="N''Golo Kanté"),
            _match_row(player_id=1, player_name="N''Golo Kanté"),
            _match_row(player_id=1, player_name="N'Golo Kanté"),
        ]
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["player_name"] == "N'Golo Kanté"
        # Variants keep the raw spellings unchanged.
        assert row["player_name_variants"] == ["N''Golo Kanté", "N'Golo Kanté"]

    def test_double_quote_normalised_to_apostrophe(self):
        rows = [
            _match_row(player_id=1, player_name='David N"Gog'),
            _match_row(player_id=1, player_name='David N"Gog'),
            _match_row(player_id=1, player_name="David N'Gog"),
        ]
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["player_name"] == "David N'Gog"
        assert row["player_name_variants"] == ['David N"Gog', "David N'Gog"]

    def test_outer_whitespace_stripped_before_quote_fix(self):
        rows = [_match_row(player_id=1, player_name="Daniel Arnaud N''Di\t")]
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["player_name"] == "Daniel Arnaud N'Di"
        # Variants keep the raw spelling, tab included.
        assert row["player_name_variants"] == ["Daniel Arnaud N''Di\t"]

    def test_whitespace_variants_pool_their_counts(self):
        rows = (
            [_match_row(player_id=1, player_name=" Zed ")]
            + [_match_row(player_id=1, player_name="Zed\t")]
            + [_match_row(player_id=1, player_name="Abe")]
        )
        # " Zed " and "Zed\t" both normalise to "Zed" (2) and beat "Abe" (1).
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["player_name"] == "Zed"

    def test_team_name_normalised(self):
        rows = [
            _match_row(player_id=1, team_id=10, team_name="Côte d''Azur FC"),
            _match_row(player_id=1, team_id=10, team_name="Côte d''Azur FC"),
        ]
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["team_name"] == "Côte d'Azur FC"

    def test_normalised_spellings_pool_their_counts(self):
        # Raw, "O Kane" (x3) is the most frequent spelling; after normalisation
        # O'Kane (x2), O''Kane and O"Kane pool to 4 and win.
        rows = (
            [_match_row(player_id=1, player_name="O'Kane")] * 2
            + [_match_row(player_id=1, player_name="O''Kane")]
            + [_match_row(player_id=1, player_name='O"Kane')]
            + [_match_row(player_id=1, player_name="O Kane")] * 3
        )
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["player_name"] == "O'Kane"
        assert row["multi_team_season"] is False
        assert row["is_primary_stint"] is True

    def test_team_two_spellings_one_row_not_multi_team(self):
        rows = [
            _match_row(player_id=1, team_id=10, team_name="Leicester City", goals=1),
            _match_row(player_id=1, team_id=10, team_name="Leicester", goals=1),
            _match_row(player_id=1, team_id=10, team_name="Leicester City", goals=1),
        ]
        result = aggregate_player_season(_df(rows))
        assert result.height == 1
        row = result.row(0, named=True)
        assert row["goals"] == 3
        assert row["team_name"] == "Leicester City"
        assert row["multi_team_season"] is False

    def test_team_canonical_name_shared_by_all_its_players(self):
        rows = [
            _match_row(player_id=1, team_id=10, team_name="Team Alpha"),
            _match_row(player_id=2, team_id=10, team_name="Team Alpha"),
            _match_row(player_id=3, team_id=10, team_name="Team Alfa"),
        ]
        result = aggregate_player_season(_df(rows))
        assert result.height == 3
        assert result["team_name"].unique().to_list() == ["Team Alpha"]

    def test_tie_break_is_alphabetical_and_order_independent(self):
        rows = [
            _match_row(player_id=1, player_name="Fernando Luiz Roza"),
            _match_row(player_id=1, player_name="Fernando Luiz Rosa"),
        ]
        forward = aggregate_player_season(_df(rows))
        backward = aggregate_player_season(_df(rows[::-1]))
        assert forward["player_name"].to_list() == ["Fernando Luiz Rosa"]
        assert backward["player_name"].to_list() == ["Fernando Luiz Rosa"]

    def test_real_transfer_still_multi_team(self):
        rows = [
            _match_row(player_id=1, player_name="Player A", team_id=10, team_name="Team A"),
            _match_row(player_id=1, player_name="Player  A", team_id=20, team_name="Team B"),
        ]
        result = aggregate_player_season(_df(rows))
        assert result.height == 2
        assert result["multi_team_season"].to_list() == [True, True]
        # One canonical name per player_id across both stints (tie → alphabetical).
        assert result["player_name"].unique().to_list() == ["Player  A"]

    def test_find_name_variants_lists_only_multi_name_ids(self):
        rows = [
            _match_row(player_id=1, player_name="A", team_id=10, team_name="T"),
            _match_row(player_id=1, player_name="A2", team_id=10, team_name="T"),
            _match_row(player_id=2, player_name="B", team_id=20, team_name="U"),
            _match_row(player_id=2, player_name="B", team_id=20, team_name="U2"),
            _match_row(player_id=3, player_name="C", team_id=30, team_name="V"),
        ]
        variants = find_name_variants(_df(rows))
        assert variants.select(["entity", "id"]).rows() == [("player", 1), ("team", 20)]
        assert variants["names"].to_list() == [["A", "A2"], ["U", "U2"]]


# ---------------------------------------------------------------------------
# TestPoolAndMultiCompetitionFlags — ADR 003 §5, §7 (plan 003 C3)
# ---------------------------------------------------------------------------

_SA, _PL, _BL = 12, 2, 9
_FLAG_CONFIG = (
    CompetitionSeason(_SA, 27, "Serie A", 20, "FULL", "statsbomb"),
    CompetitionSeason(_PL, 27, "Premier League", 20, "FULL", "statsbomb"),
    CompetitionSeason(_BL, 27, "Bundesliga", 18, "PARTIAL", "statsbomb"),
)


def _agg(*rows: dict, config=_FLAG_CONFIG) -> pl.DataFrame:
    return aggregate_player_season(_df(list(rows)), competition_seasons=config)


class TestInPercentilePool:
    def test_full_true_partial_false(self):
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA, minutes_dm_cm=90.0),
            _match_row(player_id=2, team_id=2, competition_id=_BL, minutes_dm_cm=90.0),
        )
        flags = dict(result.select("player_id", "in_percentile_pool").iter_rows())
        assert flags == {1: True, 2: False}

    def test_independent_of_minutes_and_gk(self):
        """ADR 003 §5: the flag says nothing about the player's own minutes/position."""
        result = _agg(
            _match_row(player_id=1, competition_id=_SA, minutes_played=5.0, minutes_gk=5.0),
        )
        row = result.row(0, named=True)
        assert row["in_percentile_pool"] is True
        assert row["meets_min_minutes"] is False and row["is_gk"] is True

    def test_unconfigured_competition_season_raises(self):
        with pytest.raises(ValueError, match="not in COMPETITION_SEASONS"):
            _agg(_match_row(player_id=1, competition_id=99, minutes_dm_cm=90.0))

    def test_unconfigured_season_raises(self):
        with pytest.raises(ValueError, match="not in COMPETITION_SEASONS"):
            _agg(_match_row(player_id=1, competition_id=_SA, season_id=28, minutes_dm_cm=90.0))

    def test_key_differing_only_in_source_raises(self):
        """A non-StatsBomb row never inherits a StatsBomb coverage class."""
        with pytest.raises(ValueError, match="sportmonks"):
            _agg(_match_row(player_id=1, competition_id=_SA, source="sportmonks",
                            minutes_dm_cm=90.0))

    def test_duplicate_config_raises(self):
        dup = _FLAG_CONFIG + (_FLAG_CONFIG[0],)
        with pytest.raises(ValueError, match="Duplicate"):
            _agg(_match_row(player_id=1, competition_id=_SA, minutes_dm_cm=90.0), config=dup)

    def test_default_config_is_competition_seasons(self):
        """Default factory rows (La Liga 11/27, statsbomb) are FULL in config."""
        result = aggregate_player_season(_df([_match_row(player_id=1, minutes_dm_cm=90.0)]))
        assert result["in_percentile_pool"].to_list() == [True]


class TestMultiCompetitionSeason:
    def test_single_competition_false(self):
        result = _agg(_match_row(player_id=1, competition_id=_SA, minutes_dm_cm=90.0))
        assert result["multi_competition_season"].to_list() == [False]

    def test_serie_a_to_premier_league_mover(self):
        """January move between leagues: rows stay separate, within-competition
        columns stay within-competition, both rows carry the flag."""
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA,
                       minutes_played=600.0, minutes_dm_cm=600.0),
            _match_row(player_id=1, team_id=2, competition_id=_PL,
                       minutes_played=500.0, minutes_dm_cm=500.0),
        ).sort("competition_id")
        assert result.height == 2
        assert result["multi_competition_season"].to_list() == [True, True]
        assert result["multi_team_season"].to_list() == [False, False]
        assert result["is_primary_stint"].to_list() == [True, True]
        assert result["player_season_minutes_total"].to_list() == [500.0, 600.0]

    def test_mixed_within_league_and_cross_league_move(self):
        """A→B within Serie A, then B→C into the Premier League (3 rows)."""
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA,
                       minutes_played=300.0, minutes_dm_cm=300.0),
            _match_row(player_id=1, team_id=2, competition_id=_SA,
                       minutes_played=400.0, minutes_dm_cm=400.0),
            _match_row(player_id=1, team_id=3, competition_id=_PL,
                       minutes_played=200.0, minutes_dm_cm=200.0),
        )
        by_team = {r["team_id"]: r for r in result.iter_rows(named=True)}
        assert result["multi_competition_season"].all()
        assert by_team[1]["multi_team_season"] and by_team[2]["multi_team_season"]
        assert not by_team[3]["multi_team_season"]
        assert [by_team[t]["is_primary_stint"] for t in (1, 2, 3)] == [False, True, True]
        assert by_team[1]["player_season_minutes_total"] == 700.0
        assert by_team[3]["player_season_minutes_total"] == 200.0

    def test_partial_competition_counts(self):
        """The flag describes the player's season, not pool eligibility."""
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_PL, minutes_dm_cm=90.0),
            _match_row(player_id=1, team_id=2, competition_id=_BL, minutes_dm_cm=10.0),
        )
        assert result["multi_competition_season"].to_list() == [True, True]
        assert sorted(result["in_percentile_pool"].to_list()) == [False, True]

    def test_different_seasons_not_flagged(self):
        config = _FLAG_CONFIG + (CompetitionSeason(_PL, 28, "PL next", 20, "FULL", "statsbomb"),)
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA, season_id=27, minutes_dm_cm=90.0),
            _match_row(player_id=1, team_id=2, competition_id=_PL, season_id=28, minutes_dm_cm=90.0),
            config=config,
        )
        assert result["multi_competition_season"].to_list() == [False, False]

    def test_different_sources_not_flagged(self):
        config = _FLAG_CONFIG + (CompetitionSeason(_PL, 27, "PL sm", 20, "FULL", "sportmonks"),)
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA, minutes_dm_cm=90.0),
            _match_row(player_id=1, team_id=2, competition_id=_PL, source="sportmonks",
                       minutes_dm_cm=90.0),
            config=config,
        )
        assert result["multi_competition_season"].to_list() == [False, False]

    def test_other_players_unaffected(self):
        result = _agg(
            _match_row(player_id=1, team_id=1, competition_id=_SA, minutes_dm_cm=90.0),
            _match_row(player_id=1, team_id=2, competition_id=_PL, minutes_dm_cm=90.0),
            _match_row(player_id=2, team_id=1, competition_id=_SA, minutes_dm_cm=90.0),
        )
        flags = result.filter(pl.col("player_id") == 2)["multi_competition_season"].to_list()
        assert flags == [False]


# ---------------------------------------------------------------------------
# Unknown-outcome crosses / long passes (plan 004 H1.3, ADR 002 §15, §19)
# ---------------------------------------------------------------------------

class TestUnknownOutcomeDenominators:
    """An Unknown-outcome cross or long pass is neither completed nor failed: it
    stays in *_attempted (volume) but leaves the completion denominator."""

    def _row(self, **kw):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0, **kw)]
        return aggregate_player_season(_df(rows)).row(0, named=True)

    def test_unknown_cross_excluded_from_denominator(self):
        row = self._row(crosses_attempted=5, crosses_completed=2, crosses_unknown_outcome=1)
        assert row["cross_completion_pct"] == pytest.approx(2 / 4)

    def test_unknown_cross_still_counted_as_attempted(self):
        row = self._row(crosses_attempted=5, crosses_completed=2, crosses_unknown_outcome=1)
        assert row["crosses_attempted"] == 5
        assert row["crosses_attempted_p90"] == pytest.approx(5.0)
        assert row["crosses_unknown_outcome"] == 1

    def test_all_crosses_unknown_gives_null_not_nan(self):
        row = self._row(crosses_attempted=1, crosses_completed=0, crosses_unknown_outcome=1)
        assert row["cross_completion_pct"] is None

    def test_unknown_long_pass_excluded_from_denominator(self):
        row = self._row(long_passes_attempted=10, long_passes_completed=6,
                        long_passes_unknown_outcome=2)
        assert row["long_pass_completion_pct"] == pytest.approx(6 / 8)

    def test_unknown_long_pass_still_counted_as_attempted(self):
        row = self._row(long_passes_attempted=10, long_passes_completed=6,
                        long_passes_unknown_outcome=2)
        assert row["long_passes_attempted"] == 10
        assert row["long_passes_attempted_p90"] == pytest.approx(10.0)

    def test_all_long_passes_unknown_gives_null_not_nan(self):
        row = self._row(long_passes_attempted=2, long_passes_completed=0,
                        long_passes_unknown_outcome=2)
        assert row["long_pass_completion_pct"] is None

    def test_unknown_outcome_columns_summed_across_matches(self):
        rows = [
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=2, crosses_unknown_outcome=1,
                       long_passes_attempted=3, long_passes_unknown_outcome=1),
            _match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0,
                       crosses_attempted=2, crosses_unknown_outcome=1,
                       long_passes_attempted=3, long_passes_unknown_outcome=2),
        ]
        row = aggregate_player_season(_df(rows)).row(0, named=True)
        assert row["crosses_unknown_outcome"] == 2
        assert row["long_passes_unknown_outcome"] == 3

    def test_unknown_outcome_columns_have_no_p90(self):
        rows = [_match_row(player_id=1, minutes_played=90.0, minutes_dm_cm=90.0)]
        result = aggregate_player_season(_df(rows))
        assert "crosses_unknown_outcome_p90" not in result.columns
        assert "long_passes_unknown_outcome_p90" not in result.columns


# ---------------------------------------------------------------------------
# is_outfield (plan 004 H1.1): cohorts filter on it
# ---------------------------------------------------------------------------

class TestIsOutfield:
    _COL = {"CB": "minutes_cb", "FB-WB": "minutes_fb_wb", "DM-CM": "minutes_dm_cm",
            "AM-Winger": "minutes_am_winger", "ST": "minutes_st", "GK": "minutes_gk"}

    def _flag(self, group: str) -> bool:
        rows = [_match_row(player_id=1, position_group=group, minutes_played=90.0,
                           **{self._COL[group]: 90.0})]
        return aggregate_player_season(_df(rows))["is_outfield"][0]

    @pytest.mark.parametrize("group", ["CB", "FB-WB", "DM-CM", "AM-Winger", "ST"])
    def test_outfield_groups_true(self, group):
        assert self._flag(group) is True

    def test_gk_false(self):
        assert self._flag("GK") is False

    def test_unknown_group_false(self):
        rows = [_match_row(player_id=1, position_group="UNKNOWN", minutes_played=90.0,
                           minutes_unknown=90.0)]
        result = aggregate_player_season(_df(rows))
        assert result["position_group"][0] == "UNKNOWN"
        assert result["is_outfield"][0] is False


# ---------------------------------------------------------------------------
# Deterministic output order
# ---------------------------------------------------------------------------

class TestDeterministicRowOrder:
    """The season frame is sorted by its unique key, so two builds of the same
    input are identical row by row, whatever the input row order."""

    _KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]

    def _rows(self) -> list[dict]:
        rows = []
        for pid in range(1, 41):
            for team_id in (10, 20) if pid % 3 == 0 else (10,):
                for comp in (11, 2) if pid % 5 == 0 else (11,):
                    rows.append(_match_row(player_id=pid, team_id=team_id,
                                           team_name=f"Team {team_id}",
                                           competition_id=comp, minutes_dm_cm=90.0,
                                           shots=pid % 4))
        return rows

    def test_two_builds_identical_row_by_row(self):
        first = aggregate_player_season(_df(self._rows()))
        second = aggregate_player_season(_df(self._rows()))
        assert first.equals(second)

    def test_shuffled_input_gives_identical_frame(self):
        rows = self._rows()
        shuffled = _df(rows).sample(fraction=1.0, shuffle=True, seed=7)
        assert aggregate_player_season(_df(rows)).equals(aggregate_player_season(shuffled))

    def test_rows_sorted_by_unique_key(self):
        result = aggregate_player_season(_df(self._rows()))
        assert result.select(self._KEY).is_unique().all()
        assert result.select(self._KEY).equals(result.select(self._KEY).sort(self._KEY))


# ---------------------------------------------------------------------------
# ADR 004 (plan 005 M4b) season columns
# ---------------------------------------------------------------------------

def _season_one(rows: list[dict], min_minutes: int = 900) -> dict:
    result = aggregate_player_season(_df(rows), min_minutes=min_minutes)
    assert result.height == 1
    return result.row(0, named=True)


class TestNominalMinutesGate:
    """ADR 004 D1: meets_min_minutes reads minutes_nominal_equiv = Σ minutes_played ×
    match_nominal_duration / match_duration, with a 1e-6 tolerance; per-90s keep
    the elapsed minutes_played."""

    def test_ten_full_matches_of_non_round_length_meet_the_gate(self):
        """Each full match is worth 90 nominal minutes whatever its length; the float
        sum lands within 1e-6 of 900 and still passes."""
        durations = [94.37, 96.41, 93.0 + 1 / 3, 95.05, 97.6833, 92.95, 94.1, 98.35, 93.45, 96.03]
        row = _season_one([_match_row(minutes_played=d, match_duration=d) for d in durations])
        assert row["minutes_nominal_equiv"] == pytest.approx(900.0, abs=1e-9)
        assert row["meets_min_minutes"] is True
        assert row["minutes_played"] == pytest.approx(sum(durations))

    def test_just_below_the_gate_fails(self):
        row = _season_one([_match_row(minutes_played=95.0, match_duration=95.0)] * 9
                          + [_match_row(minutes_played=89.0, match_duration=95.0)])
        assert row["minutes_nominal_equiv"] < 900
        assert row["meets_min_minutes"] is False

    def test_elapsed_900_with_stoppage_is_below_the_gate(self):
        """900 elapsed minutes over 96-minute matches are 843.75 nominal minutes."""
        row = _season_one([_match_row(minutes_played=96.0, match_duration=96.0)] * 9
                          + [_match_row(minutes_played=36.0, match_duration=96.0)])
        assert row["minutes_played"] == pytest.approx(900.0)
        assert row["minutes_nominal_equiv"] == pytest.approx(843.75)
        assert row["meets_min_minutes"] is False

    def test_short_match_gives_more_nominal_than_elapsed_minutes(self):
        row = _season_one([_match_row(minutes_played=85.0, match_duration=85.0)], min_minutes=90)
        assert row["minutes_nominal_equiv"] == pytest.approx(90.0)
        assert row["meets_min_minutes"] is True

    def test_extra_time_match_is_worth_120_nominal_minutes(self):
        row = _season_one([_match_row(minutes_played=126.0, match_duration=126.0,
                                      match_nominal_duration=120.0)], min_minutes=120)
        assert row["minutes_nominal_equiv"] == pytest.approx(120.0)
        assert row["meets_min_minutes"] is True

    def test_per_90_uses_elapsed_minutes(self):
        row = _season_one([_match_row(minutes_played=96.0, match_duration=96.0, shots=4)],
                          min_minutes=0)
        assert row["shots_p90"] == pytest.approx(4 / 96 * 90)


class TestPositionMarginColumns:
    """ADR 004 D5: second group, share, margin and the ambiguous flag (< 5%)."""

    def test_near_tie_is_ambiguous(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="AM-Winger",
                                      minutes_am_winger=51.0, minutes_st=49.0)], min_minutes=0)
        assert row["position_group"] == "AM-Winger"
        assert row["second_position_group"] == "ST"
        assert row["position_group_share"] == pytest.approx(0.51)
        assert row["position_group_margin_pct"] == pytest.approx(0.02)
        assert row["position_group_ambiguous"] is True

    def test_clear_margin_is_not_ambiguous(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=80.0, minutes_fb_wb=20.0)], min_minutes=0)
        assert row["position_group_margin_pct"] == pytest.approx(0.6)
        assert row["position_group_ambiguous"] is False

    def test_single_group_has_no_second_group_and_margin_one(self):
        row = _season_one([_match_row(minutes_played=90.0, position_group="ST")], min_minutes=0)
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx(1.0)
        assert row["position_group_share"] == pytest.approx(1.0)
        assert row["position_group_ambiguous"] is False

    def test_three_way_near_tie_second_is_alphabetical_on_equal_minutes(self):
        """DM-CM 34, AM-Winger 33, ST 33: second goes to AM-Winger (alphabetical)."""
        row = _season_one([_match_row(minutes_played=100.0, position_group="DM-CM",
                                      minutes_dm_cm=34.0, minutes_am_winger=33.0,
                                      minutes_st=33.0)], min_minutes=0)
        assert row["position_group"] == "DM-CM"
        assert row["second_position_group"] == "AM-Winger"
        assert row["position_group_margin_pct"] == pytest.approx(0.01)
        assert row["position_group_ambiguous"] is True

    def test_gk_is_never_the_second_group_of_an_outfield_row(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=60.0, minutes_gk=30.0, minutes_st=10.0)],
                          min_minutes=0)
        assert row["second_position_group"] == "ST"
        assert row["position_group_margin_pct"] == pytest.approx(0.5)

    def test_outfield_group_can_be_second_for_a_gk_row(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="GK",
                                      minutes_gk=70.0, minutes_st=30.0)], min_minutes=0)
        assert row["second_position_group"] == "ST"


class TestSecondPositionMinShare:
    """ADR 004 §8 (plan 005 M4c1): the second_position_group label is null below
    SECOND_POSITION_MIN_SHARE (10%) of minutes_played; the margin, share and
    ambiguous flag still use the real second-most minutes."""

    def test_second_group_just_below_ten_percent_is_null_but_margin_uses_it(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=90.1, minutes_st=9.9)], min_minutes=0)
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx(0.802)
        assert row["position_group_share"] == pytest.approx(0.901)

    def test_second_group_at_exactly_ten_percent_is_kept(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=90.0, minutes_st=10.0)], min_minutes=0)
        assert row["second_position_group"] == "ST"
        assert row["position_group_margin_pct"] == pytest.approx(0.8)

    def test_goalkeeper_cameo_of_a_fraction_of_a_minute_has_no_second_group(self):
        """The Begović shape: a GK season with ~1 outfield minute in ~1,500."""
        row = _season_one([_match_row(minutes_played=1500.0, position_group="GK",
                                      minutes_gk=1498.97, minutes_dm_cm=1.03)], min_minutes=0)
        assert row["position_group"] == "GK"
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx((1498.97 - 1.03) / 1500)

    def test_threshold_does_not_touch_the_ambiguous_flag(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="AM-Winger",
                                      minutes_am_winger=51.0, minutes_st=49.0)], min_minutes=0)
        assert row["second_position_group"] == "ST"
        assert row["position_group_ambiguous"] is True

    def test_threshold_comes_from_config(self, monkeypatch):
        import eleveniq.pipeline.build_player_season as bps
        monkeypatch.setattr(bps, "SECOND_POSITION_MIN_SHARE", 0.25)
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=80.0, minutes_fb_wb=20.0)], min_minutes=0)
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx(0.6)


class TestM4bRatios:
    """ADR 004 D12, D13, D15 season ratios, from season totals."""

    def test_np_xg_per_shot_and_null_without_np_shots(self):
        row = _season_one([_match_row(shots=3, xg=1.2, np_shots=2, np_xg=0.44)], min_minutes=0)
        assert row["np_xg_per_shot"] == pytest.approx(0.22)
        assert row["np_xg_p90"] == pytest.approx(0.44)
        row = _season_one([_match_row(shots=1, xg=0.76, np_shots=0, np_xg=0.0)], min_minutes=0)
        assert row["np_xg_per_shot"] is None

    def test_np_shots_on_target_summed_with_p90_and_penalties_kept_in_total(self):
        """M4c2b (D15): np_shots_on_target is summed and gets a p90;
        shots_on_target still counts the penalties on target."""
        row = _season_one([
            _match_row(shots=3, shots_on_target=2, np_shots=2, np_shots_on_target=1),
            _match_row(shots=2, shots_on_target=1),
        ], min_minutes=0)
        assert (row["shots_on_target"], row["np_shots_on_target"]) == (3, 2)
        assert row["np_shots_on_target_p90"] == pytest.approx(2 / 180 * 90)
        assert row["shots_on_target_p90"] == pytest.approx(3 / 180 * 90)

    def test_team_pass_share_from_season_totals(self):
        row = _season_one([
            _match_row(open_play_passes_attempted=40, team_open_play_passes_while_on=400),
            _match_row(open_play_passes_attempted=10, team_open_play_passes_while_on=100),
        ], min_minutes=0)
        assert row["team_pass_share"] == pytest.approx(50 / 500)

    def test_exposure_and_team_action_counts_are_summed_over_matches(self):
        row = _season_one([
            _match_row(opponent_open_play_passes_while_on=300, team_actions={"pressures": 5, "blocks": 1}),
            _match_row(opponent_open_play_passes_while_on=200, team_actions={"pressures": 7}),
        ], min_minutes=0)
        assert row["opponent_open_play_passes_while_on"] == 500
        assert row["team_pressures_while_on"] == 12
        assert row["team_blocks_while_on"] == 1
        assert row["team_interceptions_while_on"] == 0

    def test_team_pass_share_null_without_team_passes(self):
        row = _season_one([_match_row(team_open_play_passes_while_on=0)], min_minutes=0)
        assert row["team_pass_share"] is None

    def test_injury_clearances_leave_the_completion_denominators(self):
        row = _season_one([_match_row(passes_attempted=50, passes_completed=40,
                                      passes_unknown_outcome=2, passes_injury_clearance=3,
                                      long_passes_attempted=10, long_passes_completed=6,
                                      long_passes_unknown_outcome=1,
                                      long_passes_injury_clearance=1)], min_minutes=0)
        assert row["pass_completion_pct"] == pytest.approx(40 / 45)
        assert row["long_pass_completion_pct"] == pytest.approx(6 / 8)


# ---------------------------------------------------------------------------
# QA (plan 005 M4b): per-stint nominal gate, margin edges
# ---------------------------------------------------------------------------

class TestNominalGatePerStint:
    """ADR 004 D1-D3: the gate reads minutes_nominal_equiv, each stint on its own,
    with no combined gate for a player on two teams in one season."""

    def test_two_team_season_is_never_gated_on_combined_minutes(self):
        """540 + 450 nominal minutes on two teams (990 combined): neither stint meets."""
        rows = ([_match_row(team_id=1, team_name="A")] * 6
                + [_match_row(team_id=2, team_name="B")] * 5)
        result = aggregate_player_season(_df(rows))
        assert result.height == 2
        assert result["minutes_nominal_equiv"].sort().to_list() == pytest.approx([450.0, 540.0])
        assert not result["meets_min_minutes"].any()

    def test_only_the_stint_over_the_gate_meets_it(self):
        rows = ([_match_row(team_id=1, team_name="A")] * 10
                + [_match_row(team_id=2, team_name="B")])
        result = aggregate_player_season(_df(rows)).sort("team_id")
        assert result["meets_min_minutes"].to_list() == [True, False]

    def test_nominal_minutes_summed_per_match_not_from_season_ratio(self):
        """Σ(min × nominal / duration) per match, not Σmin × Σnominal / Σduration.
        96/96 at 90 nominal = 90; a 30-minute extra-time cameo of a 126-minute match
        at 120 nominal = 30 × 120 / 126."""
        row = _season_one([_match_row(minutes_played=96.0, match_duration=96.0),
                           _match_row(minutes_played=30.0, match_duration=126.0,
                                      match_nominal_duration=120.0)], min_minutes=0)
        assert row["minutes_nominal_equiv"] == pytest.approx(90.0 + 30 * 120 / 126)


class TestPositionMarginEdges:
    """ADR 004 D5 edges: season totals (not per match), the strict < 5% boundary,
    and a zero-minute row (zero denominator)."""

    def test_margin_uses_season_group_totals(self):
        """CB 90 + ST 90 + CB 10 over three matches: CB 100, ST 90 of 190 minutes."""
        row = _season_one([
            _match_row(minutes_played=90.0, position_group="CB"),
            _match_row(minutes_played=90.0, position_group="ST"),
            _match_row(minutes_played=10.0, position_group="CB"),
        ], min_minutes=0)
        assert row["position_group"] == "CB"
        assert row["second_position_group"] == "ST"
        assert row["position_group_share"] == pytest.approx(100 / 190)
        assert row["position_group_margin_pct"] == pytest.approx(10 / 190)
        assert row["position_group_ambiguous"] is False  # 5.26% is not < 5%

    def test_margin_of_exactly_five_percent_is_not_ambiguous(self):
        row = _season_one([_match_row(minutes_played=100.0, position_group="FB-WB",
                                      minutes_fb_wb=52.5, minutes_cb=47.5)], min_minutes=0)
        assert row["position_group_margin_pct"] == pytest.approx(0.05)
        assert row["position_group_ambiguous"] is False

    def test_zero_minute_row_has_null_share_and_margin_and_is_not_ambiguous(self):
        """A substitute on at the final whistle: 0 minutes must not divide by zero."""
        row = _season_one([_match_row(minutes_played=0.0, started=False)], min_minutes=0)
        assert row["position_group_share"] is None
        assert row["position_group_margin_pct"] is None
        assert row["position_group_ambiguous"] is False
        assert row["second_position_group"] is None
        assert row["team_pass_share"] is None


class TestSecondPositionMinShareEdges:
    """ADR 004 §8 (M4c1) edges not covered by TestSecondPositionMinShare: the 10%
    test reads season totals per stint, and skips GK minutes on an outfield row."""

    def test_one_full_match_elsewhere_is_below_ten_percent_of_the_season(self):
        """11 full matches at CB + 1 full match at ST: ST is 100% of its match but
        90/1080 = 8.3% of the season, so no label; the margin still subtracts it."""
        row = _season_one([_match_row(minutes_played=90.0, position_group="CB")] * 11
                          + [_match_row(minutes_played=90.0, position_group="ST")],
                          min_minutes=0)
        assert row["position_group"] == "CB"
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx((990 - 90) / 1080)

    def test_small_cameos_that_sum_above_ten_percent_are_labelled(self):
        """Ten matches with 9 ST minutes each: season ST = 90 of 900, exactly the
        10% boundary on season totals, so the label is kept."""
        row = _season_one([_match_row(minutes_played=90.0, position_group="CB",
                                      minutes_cb=81.0, minutes_st=9.0)] * 10,
                          min_minutes=0)
        assert row["second_position_group"] == "ST"
        assert row["position_group_share"] == pytest.approx(0.9)

    def test_outfield_row_with_large_gk_minutes_does_not_use_them_for_the_label(self):
        """CB 80, GK 15, ST 5: GK is ineligible, the real second is ST at 5% -> null.
        A threshold read from GK minutes (15%) would wrongly keep a label."""
        row = _season_one([_match_row(minutes_played=100.0, position_group="CB",
                                      minutes_cb=80.0, minutes_gk=15.0, minutes_st=5.0)],
                          min_minutes=0)
        assert row["second_position_group"] is None
        assert row["position_group_margin_pct"] == pytest.approx(0.75)
        assert row["position_group_ambiguous"] is False

    def test_threshold_is_per_team_stint_for_a_player_on_two_teams(self):
        """Same player and season, two clubs: ST is 5% at team 10 (null) and 50% at
        team 20 (kept). Pooling the stints would give one shared answer."""
        result = aggregate_player_season(_df([
            _match_row(team_id=10, team_name="Team A", minutes_played=100.0,
                       position_group="CB", minutes_cb=95.0, minutes_st=5.0),
            _match_row(team_id=20, team_name="Team B", minutes_played=100.0,
                       position_group="ST", minutes_cb=45.0, minutes_st=55.0),
        ]), min_minutes=0)
        by_team = {r["team_id"]: r for r in result.iter_rows(named=True)}
        assert by_team[10]["second_position_group"] is None
        assert by_team[20]["second_position_group"] == "CB"
        assert by_team[20]["position_group_ambiguous"] is False  # margin 0.10


@pytest.mark.integration
@pytest.mark.skipif(not _SEASON_PARQUET.exists(),
                    reason="requires data/processed/player_season_stats.parquet")
class TestSecondPositionMinShareOutput:
    """ADR 004 §8 (M4c1) invariants on the built season parquet."""

    @pytest.fixture(scope="class")
    @classmethod
    def season(cls) -> pl.DataFrame:
        return pl.read_parquet(_SEASON_PARQUET)

    def test_every_ambiguous_row_keeps_its_second_group(self, season):
        """ADR 004 §8: no ambiguous row loses the label. Guaranteed by the structural
        bound (see TestSecondPositionShareBound); in 2015/16 the lowest second-group
        share of an ambiguous row is 0.448 in the gated pool and 0.380 over all rows."""
        bad = season.filter(pl.col("position_group_ambiguous")
                            & pl.col("second_position_group").is_null())
        assert bad.height == 0, bad.select("player_name", "team_id", "season_id").head(10)

    def test_every_label_matches_an_independent_recomputation(self, season):
        """Full per-row comparison, both directions: the label is recomputed from
        the per-group minute columns (ADR 004 D5: most minutes after the top group,
        alphabetical on ties, never GK for an outfield row, none at 0 minutes) and
        kept only when its share of minutes_played is >= SECOND_POSITION_MIN_SHARE
        (ADR 004 §8). A label that should be blank and a blank that should be a
        label both fail."""
        from eleveniq.config import SECOND_POSITION_MIN_SHARE
        from eleveniq.metrics.positions import (
            GK_GROUP, OUTFIELD_GROUPS, POSITION_GROUP_COLUMNS, UNKNOWN_POSITION_GROUP)
        groups = sorted(g for g in POSITION_GROUP_COLUMNS if g != UNKNOWN_POSITION_GROUP)
        wrong = []
        for row in season.iter_rows(named=True):
            best_group, best_min = None, 0.0
            for group in groups:
                if group == row["position_group"]:
                    continue
                if group == GK_GROUP and row["position_group"] in OUTFIELD_GROUPS:
                    continue
                if row[POSITION_GROUP_COLUMNS[group]] > best_min:
                    best_group, best_min = group, row[POSITION_GROUP_COLUMNS[group]]
            minutes = row["minutes_played"]
            expected = (best_group if minutes > 0 and best_min / minutes >= SECOND_POSITION_MIN_SHARE
                        else None)
            if expected != row["second_position_group"]:
                wrong.append((row["player_name"], row["team_id"], expected,
                              row["second_position_group"]))
        assert season.height > 0
        assert not wrong, f"{len(wrong)} rows differ, e.g. {wrong[:5]}"


class TestSecondPositionShareBound:
    """ADR 004 §8: an ambiguous row (margin < AMBIGUOUS_POSITION_MARGIN) always keeps
    its second-group label. With n position groups whose minutes sum to
    minutes_played (UNKNOWN minutes fail the build), the top group holds at least
    1/n, so an ambiguous row's second group holds more than 1/n - margin. The
    label threshold must not exceed that bound, or ambiguous rows could lose the
    label their display-only second-cohort percentile depends on."""

    def test_min_share_does_not_exceed_the_ambiguity_bound(self):
        from eleveniq.config import AMBIGUOUS_POSITION_MARGIN, SECOND_POSITION_MIN_SHARE
        from eleveniq.metrics.positions import POSITION_GROUP_COLUMNS, UNKNOWN_POSITION_GROUP
        n_groups = len([g for g in POSITION_GROUP_COLUMNS if g != UNKNOWN_POSITION_GROUP])
        bound = 1 / n_groups - AMBIGUOUS_POSITION_MARGIN
        assert n_groups == 6
        assert bound == pytest.approx(0.1167, abs=1e-4)
        assert SECOND_POSITION_MIN_SHARE <= bound, (
            f"SECOND_POSITION_MIN_SHARE {SECOND_POSITION_MIN_SHARE} exceeds the bound "
            f"{bound:.4f}: an ambiguous row could lose its second-group label")


class TestOpenPlayRestartSeasonColumns:
    """ADR 004 §9: the six split columns are summed and get p90s; open play +
    restart = total on the season row (counts exact, xG within 1e-9)."""

    def test_season_sums_p90s_and_invariant(self):
        row = _season_one([
            _match_row(xg_assisted=0.5, op_xg_assisted=0.3, restart_xg_assisted=0.2,
                       key_passes=4, op_key_passes=3, restart_key_passes=1,
                       assists=1, op_assists=0, restart_assists=1),
            _match_row(xg_assisted=0.17, op_xg_assisted=0.17, key_passes=2, assists=0),
        ], min_minutes=0)
        assert row["op_key_passes"] + row["restart_key_passes"] == row["key_passes"] == 6
        assert row["op_assists"] + row["restart_assists"] == row["assists"] == 1
        assert row["op_xg_assisted"] + row["restart_xg_assisted"] == pytest.approx(
            row["xg_assisted"], abs=1e-9)
        assert row["restart_key_passes_p90"] == pytest.approx(1 / 180 * 90)
        assert row["op_xg_assisted_p90"] == pytest.approx(0.47 / 180 * 90)


class TestPlayerNicknames:
    """Plan 006 pre-W1 gate G1: one nickname (or null) per player and source; two different
    nicknames for one player fail loudly; the season row carries it."""

    def test_one_nickname_per_player_and_null_when_none(self):
        df = pl.DataFrame([_match_row(player_id=1, player_nickname="Isco"),
                           _match_row(player_id=1, player_nickname="Isco"),
                           _match_row(player_id=2, player_nickname=None)])
        got = dict(bps.player_nicknames(df).select("player_id", "player_nickname").iter_rows())
        assert got == {1: "Isco", 2: None}

    def test_a_null_in_some_matches_does_not_hide_the_nickname(self):
        df = pl.DataFrame([_match_row(player_id=1, player_nickname=None),
                           _match_row(player_id=1, player_nickname="Isco")])
        assert bps.player_nicknames(df)["player_nickname"].to_list() == ["Isco"]

    def test_two_nicknames_for_one_player_raise(self):
        df = pl.DataFrame([_match_row(player_id=1, player_nickname="A"),
                           _match_row(player_id=1, player_nickname="B")])
        with pytest.raises(ValueError, match="more than one nickname"):
            bps.player_nicknames(df)


@pytest.mark.integration
def test_nickname_is_not_a_key_and_player_id_is():
    """Decision 9 (user, 2026-10-09): pages, URLs and data keys use player_id, never a name or
    nickname. The data shows why: one nickname per player_id, but several players share one."""
    path = Path(__file__).resolve().parents[1] / "data" / "processed" / "player_season_stats.parquet"
    if not path.exists():
        pytest.skip("season parquet missing")
    s = pl.read_parquet(path)
    if "player_nickname" not in s.columns:
        pytest.skip("season parquet built before G1")
    per_id = s.group_by("player_id").agg(pl.col("player_nickname").drop_nulls().n_unique().alias("n"))
    assert per_id["n"].max() <= 1
    shared = (s.drop_nulls("player_nickname").group_by("player_nickname")
              .agg(pl.col("player_id").n_unique().alias("ids")).filter(pl.col("ids") > 1))
    assert shared.height > 0           # a nickname cannot identify a player
    assert s.select(["player_id", "team_id", "competition_id", "season_id", "source"]).is_duplicated().sum() == 0


class TestSeasonRowCarriesNickname:
    """Plan 006 G1 decisions 1 and 3: the season row gains player_nickname without changing the
    grain; a player on two teams keeps one nickname on both rows; null stays null."""

    def test_nickname_on_every_season_row_incl_two_teams(self):
        df = pl.DataFrame([
            _match_row(player_id=1, team_id=10, player_nickname="Isco"),
            _match_row(player_id=1, team_id=20, team_name="Team B", player_nickname=None),
            _match_row(player_id=2, team_id=10, player_nickname=None),
        ])
        out = aggregate_player_season(df)
        assert out.height == 3
        got = {(r["player_id"], r["team_id"]): r["player_nickname"] for r in out.iter_rows(named=True)}
        assert got == {(1, 10): "Isco", (1, 20): "Isco", (2, 10): None}


def test_player_nicknames_on_a_parquet_built_before_g1_asks_for_a_rebuild():
    df = pl.DataFrame([_match_row(player_id=1)]).drop("player_nickname")
    with pytest.raises(ValueError, match="rebuild it with build_player_match --force"):
        bps.player_nicknames(df)
