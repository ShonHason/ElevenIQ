"""
Unit tests for build_player_match — _process_match and build_competition_season.

Uses a minimal fixture in tests/fixtures/statsbomb/ that covers:
  - A starter who scores a goal and makes a progressive carry
  - A starter who is subbed off and makes a goal_assist pass
  - A starter who is sent off at 30:00 (open-ended to=null, so minutes overcounted
    without the fix)
  - A starter on the other team who has a defensive block, a save_block (excluded),
    and an Own Goal Against event (must not count in goals)
  - A substitute (started=False)
  - An unused substitute (excluded from output)
  - An offensive block (excluded from blocks count)
  - A player whose only position entry has start_reason='Tactical Shift' from 00:00
    (fallback rule: started=True, started_inferred=True)
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eleveniq.pipeline.build_player_match import (
    _process_match,
    build_competition_season,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "statsbomb"

# ---------------------------------------------------------------------------
# _process_match
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def process_match_result():
    rows, dismissal_report = _process_match(
        9999, competition_id=99, season_id=1, data_root=_FIXTURE_ROOT
    )
    by_id = {r["player_id"]: r for r in rows}
    return by_id, dismissal_report


class TestProcessMatchRows:
    def test_row_count_excludes_unused_sub(self, process_match_result):
        by_id, _ = process_match_result
        # 3 Team A + 3 Team B players with minutes; Frank (2003) unused
        assert len(by_id) == 6
        assert 2003 not in by_id

    def test_starter_full_match(self, process_match_result):
        by_id, _ = process_match_result
        alice = by_id[1001]
        assert alice["started"] is True
        assert alice["started_inferred"] is False
        assert alice["minutes_played"] == pytest.approx(90.0)
        assert alice["position_group"] == "CB"

    def test_starter_with_goal_and_xg(self, process_match_result):
        by_id, _ = process_match_result
        alice = by_id[1001]
        assert alice["goals"] == 1
        assert alice["shots"] == 1
        assert alice["shots_on_target"] == 1
        assert alice["xg"] == pytest.approx(0.5)

    def test_starter_progressive_carry(self, process_match_result):
        by_id, _ = process_match_result
        alice = by_id[1001]
        assert alice["progressive_carries"] == 1

    def test_offensive_block_excluded(self, process_match_result):
        by_id, _ = process_match_result
        alice = by_id[1001]
        assert alice["blocks"] == 0

    def test_goal_assist_pass_counts_as_key_pass_and_assist(self, process_match_result):
        by_id, _ = process_match_result
        bob = by_id[1002]
        assert bob["key_passes"] == 1
        assert bob["assists"] == 1

    def test_subbed_off_starter(self, process_match_result):
        by_id, _ = process_match_result
        bob = by_id[1002]
        assert bob["started"] is True
        assert bob["minutes_played"] == pytest.approx(60.0)

    def test_goal_assist_pass_is_progressive(self, process_match_result):
        # Bob's e1: (50,40)→(100,40), start_x=50≥48, dist_reduction=50yds≥10
        by_id, _ = process_match_result
        bob = by_id[1002]
        assert bob["progressive_passes"] == 1

    def test_dismissed_player_minutes_clamped(self, process_match_result):
        by_id, _ = process_match_result
        charlie = by_id[1003]
        assert charlie["started"] is True
        assert charlie["minutes_played"] == pytest.approx(30.0)

    def test_defensive_block_counted(self, process_match_result):
        by_id, _ = process_match_result
        dave = by_id[2001]
        assert dave["blocks"] == 1  # save_block excluded

    def test_own_goal_against_not_counted_in_goals(self, process_match_result):
        by_id, _ = process_match_result
        dave = by_id[2001]
        assert dave["goals"] == 0

    def test_substitute_started_false(self, process_match_result):
        by_id, _ = process_match_result
        eve = by_id[2002]
        assert eve["started"] is False
        assert eve["minutes_played"] == pytest.approx(30.0)

    def test_source_column_is_statsbomb(self, process_match_result):
        by_id, _ = process_match_result
        assert all(r["source"] == "statsbomb" for r in by_id.values())


class TestProcessMatchDismissalReport:
    def test_dismissed_player_in_report(self, process_match_result):
        _, report = process_match_result
        charlie_entries = [e for e in report if e.get("player_id") == 1003]
        assert len(charlie_entries) == 1

    def test_dismissal_report_overcounted_true(self, process_match_result):
        _, report = process_match_result
        entry = next(e for e in report if e.get("player_id") == 1003)
        assert entry["minutes_overcounted_without_fix"] is True

    def test_dismissal_cause_to_null(self, process_match_result):
        _, report = process_match_result
        entry = next(e for e in report if e.get("player_id") == 1003)
        assert entry["cause"] == "to_null"

    def test_dismissal_minute_correct(self, process_match_result):
        _, report = process_match_result
        entry = next(e for e in report if e.get("player_id") == 1003)
        assert entry["dismissal_minute"] == pytest.approx(30.0)

    def test_undismissed_players_not_in_report(self, process_match_result):
        _, report = process_match_result
        dismissed_ids = {e["player_id"] for e in report if "dismissal_minute" in e}
        assert dismissed_ids == {1003}


class TestStartedInferred:
    """started_inferred fallback: player with only start_reason='Tactical Shift'
    at 00:00 (fixture player Grace, id=2004) must be marked started via fallback."""

    def test_fallback_starter_started_true(self, process_match_result):
        by_id, _ = process_match_result
        grace = by_id[2004]
        assert grace["started"] is True

    def test_fallback_starter_inferred_true(self, process_match_result):
        by_id, _ = process_match_result
        grace = by_id[2004]
        assert grace["started_inferred"] is True

    def test_regular_starter_not_inferred(self, process_match_result):
        by_id, _ = process_match_result
        dave = by_id[2001]
        assert dave["started"] is True
        assert dave["started_inferred"] is False

    def test_substitute_not_inferred(self, process_match_result):
        by_id, _ = process_match_result
        eve = by_id[2002]
        assert eve["started"] is False
        assert eve["started_inferred"] is False

    def test_quality_report_has_fallback_entry_for_team_b(self, process_match_result):
        _, report = process_match_result
        entries = [
            e for e in report
            if e.get("issue") == "started_inferred_fallback" and e.get("team_id") == 102
        ]
        assert len(entries) == 1

    def test_quality_report_fallback_names_grace(self, process_match_result):
        _, report = process_match_result
        entry = next(
            e for e in report
            if e.get("issue") == "started_inferred_fallback" and e.get("team_id") == 102
        )
        assert "Grace" in entry["players"]

    def test_quality_report_mismatch_reported_for_fixture_teams(self, process_match_result):
        _, report = process_match_result
        mismatch_team_ids = {
            e["team_id"] for e in report if e.get("issue") == "starter_count_mismatch"
        }
        # Fixture teams each have fewer than 11 players → both trigger mismatch
        assert 101 in mismatch_team_ids
        assert 102 in mismatch_team_ids


# ---------------------------------------------------------------------------
# explicit_to_after_card dismissal cause — match 9998 fixture
# ---------------------------------------------------------------------------
#
# Match 9998 fixture: Hernandez (3001) receives a Bad Behaviour Red Card at
# 17:59 (minute=17, second=59 → 17.983… min) but StatsBomb left explicit to
# values that run past the card minute (45:00 and 84:41).  The pipeline must
# clamp both stints at the card minute and record cause='explicit_to_after_card'.
#
# ADR 002 §3: "explicit_to_after_card" is the cause when all stints have
# explicit `to` values but they run past the dismissal minute.

_FIXTURE_ROOT_9998 = Path(__file__).parent / "fixtures" / "statsbomb"


@pytest.fixture(scope="module")
def process_match_9998_result():
    rows, report = _process_match(
        9998, competition_id=99, season_id=2, data_root=_FIXTURE_ROOT_9998
    )
    by_id = {r["player_id"]: r for r in rows}
    return by_id, report


class TestExplicitToAfterCardCause:
    """ADR 002 §3 — dismissal cause = 'explicit_to_after_card' when all stints have
    explicit to-values past the card minute."""

    def test_dismissed_player_minutes_clamped_to_card_minute(self, process_match_9998_result):
        by_id, _ = process_match_9998_result
        hernandez = by_id[3001]
        # card at 17:59 → 17 + 59/60 = 17.983...
        assert hernandez["minutes_played"] == pytest.approx(17 + 59 / 60)

    def test_cause_is_explicit_to_after_card(self, process_match_9998_result):
        _, report = process_match_9998_result
        entry = next(e for e in report if e.get("player_id") == 3001)
        assert entry["cause"] == "explicit_to_after_card"

    def test_minutes_overcounted_without_fix_is_true(self, process_match_9998_result):
        _, report = process_match_9998_result
        entry = next(e for e in report if e.get("player_id") == 3001)
        assert entry["minutes_overcounted_without_fix"] is True

    def test_minutes_without_fix_exceeds_card_minute(self, process_match_9998_result):
        _, report = process_match_9998_result
        entry = next(e for e in report if e.get("player_id") == 3001)
        assert entry["minutes_without_fix"] > entry["dismissal_minute"] + 1.0

    def test_non_dismissed_player_not_in_dismissal_entries(self, process_match_9998_result):
        _, report = process_match_9998_result
        dismissal_ids = {e["player_id"] for e in report if "dismissal_minute" in e}
        assert 4001 not in dismissal_ids


# ---------------------------------------------------------------------------
# build_competition_season
# ---------------------------------------------------------------------------

def test_build_competition_season_writes_parquet(tmp_path):
    out = tmp_path / "out.parquet"
    df, dismissals, failed = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert out.exists()
    reloaded = pl.read_parquet(out)
    assert len(reloaded) == len(df)
    assert failed == []


def test_build_competition_season_row_count(tmp_path):
    out = tmp_path / "out.parquet"
    df, _, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert len(df) == 6  # Frank (unused sub) excluded; Grace (fallback starter) included


def test_build_competition_season_schema_has_started_and_assists(tmp_path):
    out = tmp_path / "out.parquet"
    df, _, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert "started" in df.columns
    assert "assists" in df.columns
    assert "started_inferred" in df.columns
    assert "passes_unknown_outcome" in df.columns
    assert "shots_missing_xg" in df.columns


def test_build_competition_season_returns_dismissal_report(tmp_path):
    out = tmp_path / "out.parquet"
    _, dismissals, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert any(d.get("player_id") == 1003 for d in dismissals)


# ---------------------------------------------------------------------------
# Integration tests (require pipeline output in data/interim/)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_every_team_has_exactly_11_starters():
    """After the started+fallback rule, every team in La Liga 2015/16 has exactly
    11 starters per match (total: 380 matches × 2 teams × 11 = 8,360)."""
    from pathlib import Path
    parquet = Path(__file__).resolve().parents[1] / "data/interim/player_match_stats.parquet"
    if not parquet.exists():
        pytest.skip("Pipeline output not available — run build_player_match first")
    df = pl.read_parquet(parquet)
    team_starters = (
        df.filter(pl.col("started"))
        .group_by(["match_id", "team_id"])
        .agg(pl.len().alias("starter_count"))
    )
    bad = team_starters.filter(pl.col("starter_count") != 11)
    assert len(bad) == 0, (
        f"{len(bad)} team(s) without exactly 11 starters:\n{bad.sort('match_id')}"
    )
