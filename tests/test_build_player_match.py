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
    _group_minutes_to_columns,
    _primary_raw_position,
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


def test_build_competition_season_schema_has_new_columns(tmp_path):
    """
    Schema test for columns added in this chunk:
      - per-group minute columns (minutes_gk, …, minutes_unknown)
      - crosses_attempted, crosses_completed
    These are part of _SCHEMA in build_player_match.py and must appear in
    the Parquet output so build_player_season can aggregate them.
    """
    out = tmp_path / "out.parquet"
    df, _, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    for col in (
        "minutes_gk", "minutes_cb", "minutes_fb_wb",
        "minutes_dm_cm", "minutes_am_winger", "minutes_st", "minutes_unknown",
        "crosses_attempted", "crosses_completed",
        "dispossessed", "miscontrols", "pressures", "ball_recoveries",
        "dribbled_past", "aerials_won", "aerials_lost",
        "long_passes_attempted", "long_passes_completed", "passes_missing_length",
        "switches",
    ):
        assert col in df.columns, (
            f"Expected new column {col!r} in build_competition_season output"
        )


def test_build_competition_season_crosses_non_negative(tmp_path):
    """
    crosses_attempted and crosses_completed must be >= 0 for every row
    in the fixture output — basic sanity guard.
    """
    out = tmp_path / "out.parquet"
    df, _, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert (df["crosses_attempted"] >= 0).all()
    assert (df["crosses_completed"] >= 0).all()


def test_build_competition_season_crosses_completed_le_attempted(tmp_path):
    """
    crosses_completed <= crosses_attempted for every row — a completed cross
    is a subset of all crosses attempted.
    """
    out = tmp_path / "out.parquet"
    df, _, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert (df["crosses_completed"] <= df["crosses_attempted"]).all(), (
        "crosses_completed must never exceed crosses_attempted"
    )


# ---------------------------------------------------------------------------
# Per-group minute columns: sum invariant
# ---------------------------------------------------------------------------

class TestPerGroupMinuteColumns:
    """
    Per-group minute columns (minutes_gk, minutes_cb, …) must sum to
    minutes_played for every row — same cross-consistency invariant as
    sum(compute_minutes_per_group(…).values()) == compute_player_minutes(…).
    """

    _GROUP_COLS = [
        "minutes_gk", "minutes_cb", "minutes_fb_wb",
        "minutes_dm_cm", "minutes_am_winger", "minutes_st", "minutes_unknown",
    ]

    def test_group_minutes_sum_to_minutes_played(self, process_match_result):
        by_id, _ = process_match_result
        for pid, row in by_id.items():
            group_total = sum(row[col] for col in self._GROUP_COLS)
            assert group_total == pytest.approx(row["minutes_played"]), (
                f"player_id={pid}: sum(minutes_*) {group_total} "
                f"!= minutes_played {row['minutes_played']}"
            )

    def test_primary_group_has_nonzero_minutes(self, process_match_result):
        by_id, _ = process_match_result
        col_map = {
            "GK": "minutes_gk", "CB": "minutes_cb", "FB-WB": "minutes_fb_wb",
            "DM-CM": "minutes_dm_cm", "AM-Winger": "minutes_am_winger",
            "ST": "minutes_st", "UNKNOWN": "minutes_unknown",
        }
        for pid, row in by_id.items():
            primary_col = col_map[row["position_group"]]
            assert row[primary_col] > 0, (
                f"player_id={pid}: position_group={row['position_group']} "
                f"but {primary_col}={row[primary_col]}"
            )

    def test_group_columns_non_negative(self, process_match_result):
        by_id, _ = process_match_result
        for pid, row in by_id.items():
            for col in self._GROUP_COLS:
                assert row[col] >= 0.0, f"player_id={pid}: {col} < 0"


# ---------------------------------------------------------------------------
# Per-group minute columns: specific fixture values
# ---------------------------------------------------------------------------

class TestPerGroupMinuteColumnValues:
    """
    Verify that per-group minute columns are populated with correct values
    for known players in the match 9999 fixture.

    Expected position assignments from fixture lineups/9999.json:
      Alice  (1001): Center Back     → CB       → minutes_cb = 90.0
      Bob    (1002): Center Midfield → DM-CM    → minutes_dm_cm = 60.0
      Charlie(1003): Center Forward  → ST       → minutes_st = 30.0  (red card at 30:00)
      Dave   (2001): Center Back     → CB       → minutes_cb = 90.0
      Eve    (2002): Center Midfield → DM-CM    → minutes_dm_cm = 30.0
      Grace  (2004): Left Back       → FB-WB    → minutes_fb_wb = 90.0
    """

    _ALL_GROUP_COLS = [
        "minutes_gk", "minutes_cb", "minutes_fb_wb",
        "minutes_dm_cm", "minutes_am_winger", "minutes_st", "minutes_unknown",
    ]

    def _non_primary_cols(self, primary_col: str) -> list[str]:
        return [c for c in self._ALL_GROUP_COLS if c != primary_col]

    def test_alice_cb_minutes_correct(self, process_match_result):
        by_id, _ = process_match_result
        alice = by_id[1001]
        assert alice["minutes_cb"] == pytest.approx(90.0)
        for col in self._non_primary_cols("minutes_cb"):
            assert alice[col] == pytest.approx(0.0), (
                f"Alice (CB): expected {col}=0.0 but got {alice[col]}"
            )

    def test_bob_dm_cm_minutes_correct(self, process_match_result):
        # Bob was subbed off at 60:00 → 60 minutes of DM-CM time
        by_id, _ = process_match_result
        bob = by_id[1002]
        assert bob["minutes_dm_cm"] == pytest.approx(60.0)
        for col in self._non_primary_cols("minutes_dm_cm"):
            assert bob[col] == pytest.approx(0.0), (
                f"Bob (DM-CM): expected {col}=0.0 but got {bob[col]}"
            )

    def test_charlie_st_minutes_clamped(self, process_match_result):
        # Charlie was sent off at 30:00 → clamped to 30 minutes of ST time
        by_id, _ = process_match_result
        charlie = by_id[1003]
        assert charlie["minutes_st"] == pytest.approx(30.0)
        for col in self._non_primary_cols("minutes_st"):
            assert charlie[col] == pytest.approx(0.0), (
                f"Charlie (ST): expected {col}=0.0 but got {charlie[col]}"
            )

    def test_grace_fb_wb_minutes_correct(self, process_match_result):
        # Grace (Left Back → FB-WB) played 90 minutes
        by_id, _ = process_match_result
        grace = by_id[2004]
        assert grace["minutes_fb_wb"] == pytest.approx(90.0)
        for col in self._non_primary_cols("minutes_fb_wb"):
            assert grace[col] == pytest.approx(0.0), (
                f"Grace (FB-WB): expected {col}=0.0 but got {grace[col]}"
            )

    def test_eve_sub_dm_cm_minutes_correct(self, process_match_result):
        # Eve came on at 60:00 → 30 minutes (90 - 60) of DM-CM time
        by_id, _ = process_match_result
        eve = by_id[2002]
        assert eve["minutes_dm_cm"] == pytest.approx(30.0)
        for col in self._non_primary_cols("minutes_dm_cm"):
            assert eve[col] == pytest.approx(0.0), (
                f"Eve (DM-CM sub): expected {col}=0.0 but got {eve[col]}"
            )

    def test_minutes_gk_zero_for_all_outfield_players(self, process_match_result):
        # Fixture has no goalkeeper → minutes_gk must be 0 for all players.
        by_id, _ = process_match_result
        for pid, row in by_id.items():
            assert row["minutes_gk"] == pytest.approx(0.0), (
                f"player_id={pid}: minutes_gk should be 0 (no GK in fixture) "
                f"but got {row['minutes_gk']}"
            )


# ---------------------------------------------------------------------------
# _group_minutes_to_columns: unknown group guard
# ---------------------------------------------------------------------------

def test_group_minutes_to_columns_unknown_group_raises():
    """A group key not in POSITION_GROUP_COLUMNS must raise ValueError immediately."""
    with pytest.raises(ValueError, match="Unknown position group"):
        _group_minutes_to_columns({"SomeNewGroup": 45.0})


def test_group_minutes_to_columns_known_groups_ok():
    """All canonical group keys must pass without error."""
    result = _group_minutes_to_columns({"GK": 90.0, "CB": 0.0})
    assert result["minutes_gk"] == pytest.approx(90.0)
    assert result["minutes_cb"] == pytest.approx(0.0)
    assert result["minutes_st"] == pytest.approx(0.0)


def test_group_minutes_to_columns_empty_ok():
    """Empty input is valid — every column defaults to 0.0."""
    result = _group_minutes_to_columns({})
    assert all(v == pytest.approx(0.0) for v in result.values())


def test_group_minutes_to_columns_multiple_unknown_groups_raises():
    """
    Multiple unknown keys must raise ValueError and the message must name them.
    Docstring: "Raises ValueError for any group key not in the canonical map so
    that a new group in positions.py is never silently dropped."
    """
    with pytest.raises(ValueError, match="Unknown position group"):
        _group_minutes_to_columns({"Alpha": 10.0, "Beta": 20.0})


def test_group_minutes_to_columns_mixed_known_unknown_raises():
    """A mix of known and unknown keys must still raise (unknown keys are the trigger)."""
    with pytest.raises(ValueError):
        _group_minutes_to_columns({"GK": 90.0, "NewGroup": 45.0})


def test_group_minutes_to_columns_output_has_exactly_seven_keys():
    """
    The output dict must have exactly 7 keys — one per canonical column in
    POSITION_GROUP_COLUMNS.  A schema change that adds a new column must update
    the function, not silently produce a dict with a different key count.
    """
    from eleveniq.metrics.positions import POSITION_GROUP_COLUMNS
    result = _group_minutes_to_columns({"CB": 45.0})
    assert set(result.keys()) == set(POSITION_GROUP_COLUMNS.values()), (
        "Output keys must exactly match the column names in POSITION_GROUP_COLUMNS"
    )


def test_group_minutes_to_columns_all_seven_canonical_groups_pass():
    """All 7 canonical groups supplied simultaneously must not raise."""
    from eleveniq.metrics.positions import POSITION_GROUP_COLUMNS
    all_groups = {group: float(i * 10) for i, group in enumerate(POSITION_GROUP_COLUMNS)}
    result = _group_minutes_to_columns(all_groups)
    # Total must match the sum of the input values.
    assert sum(result.values()) == pytest.approx(sum(all_groups.values()))


# ---------------------------------------------------------------------------
# _primary_raw_position — unit tests
# ---------------------------------------------------------------------------

class TestPrimaryRawPosition:
    """
    _primary_raw_position(minutes_per_raw) -> str
    Docstring: "Return the StatsBomb position name with the most playing time."
    Tie-break: alphabetically first name (min key=lambda n: (-minutes, n)).
    """

    def test_single_position_returns_it(self):
        assert _primary_raw_position({"Center Back": 90.0}) == "Center Back"

    def test_most_minutes_wins(self):
        result = _primary_raw_position({"Left Wing": 20.0, "Center Midfield": 70.0})
        assert result == "Center Midfield"

    def test_tie_resolved_alphabetically_first(self):
        # "Attacking Midfield" < "Center Forward" alphabetically
        result = _primary_raw_position({"Attacking Midfield": 45.0, "Center Forward": 45.0})
        assert result == "Attacking Midfield"

    def test_empty_dict_returns_empty_string(self):
        # Docstring does not specify empty-dict behaviour but the implementation
        # returns "" so this guards against changing that.
        assert _primary_raw_position({}) == ""

    def test_three_positions_most_minutes_wins(self):
        positions = {
            "Left Back": 30.0,
            "Center Back": 55.0,
            "Right Back": 10.0,
        }
        assert _primary_raw_position(positions) == "Center Back"


# ---------------------------------------------------------------------------
# build_competition_season error handler
# ---------------------------------------------------------------------------

def test_build_competition_season_bad_match_isolated_in_failed_ids(tmp_path, monkeypatch):
    """
    Milestones tech-debt (L550–552): the except-Exception block must append the
    failed match_id to failed_ids and continue processing remaining matches.

    Strategy:
    - Patch load_matches to return both match 9999 (good) and match 9998 (bad).
    - Patch _process_match to raise for match 9998 only.
    The call must return a non-empty DataFrame (from 9999) and failed_ids=[9998].
    """
    import eleveniq.pipeline.build_player_match as _bpm

    original_process = _bpm._process_match

    def _patched_process(match_id, competition_id, season_id, *, data_root=None):
        if match_id == 9998:
            raise ValueError("synthetic failure for match 9998")
        return original_process(match_id, competition_id, season_id, data_root=data_root)

    def _patched_load_matches(competition_id, season_id, data_root=None):
        # Return both the good fixture match and the synthetic bad match.
        return [{"match_id": 9999}, {"match_id": 9998}]

    monkeypatch.setattr(_bpm, "_process_match", _patched_process)
    monkeypatch.setattr(_bpm, "load_matches", _patched_load_matches)

    out = tmp_path / "out.parquet"
    df, _, failed = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )

    assert 9998 in failed, (
        "match_id 9998 raised an exception; it must appear in failed_ids"
    )
    assert 9999 not in failed, (
        "match_id 9999 succeeded; it must NOT appear in failed_ids"
    )
    assert len(df) > 0, (
        "The successful match (9999) must still produce rows "
        "despite the failure on match 9998"
    )


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
