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
    DROPPED_EVENT_ALLOWED,
    ON_PITCH_COUNT_EXCEEDED,
    PLAYER_MINUTES_EXCEED_DURATION,
    REVERSED_PERIOD_STINT,
    STARTED_INFERRED_FALLBACK,
    STARTER_COUNT_MISMATCH,
    STARTING_XI_ISSUES,
    STARTING_XI_MISMATCH,
    STARTING_XI_MISSING,
    STINT_AFTER_SUBSTITUTION,
    SUBSTITUTION_CAPPED,
    MatchProcessingError,
    StarterCrossCheckError,
    _group_minutes_to_columns,
    _primary_raw_position,
    _process_match,
    build_all,
    build_competition_season,
    concat_parts,
    part_path,
)
from eleveniq.config import CompetitionSeason

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
            if e.get("issue") == STARTED_INFERRED_FALLBACK and e.get("team_id") == 102
        ]
        assert len(entries) == 1

    def test_quality_report_fallback_names_grace(self, process_match_result):
        _, report = process_match_result
        entry = next(
            e for e in report
            if e.get("issue") == STARTED_INFERRED_FALLBACK and e.get("team_id") == 102
        )
        assert "Grace" in entry["players"]

    def test_quality_report_mismatch_reported_for_fixture_teams(self, process_match_result):
        _, report = process_match_result
        mismatch_team_ids = {
            e["team_id"] for e in report if e.get("issue") == STARTER_COUNT_MISMATCH
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
    df, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert out.exists()
    reloaded = pl.read_parquet(out)
    assert len(reloaded) == len(df)


def test_build_competition_season_row_count(tmp_path):
    out = tmp_path / "out.parquet"
    df, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert len(df) == 6  # Frank (unused sub) excluded; Grace (fallback starter) included


def test_build_competition_season_schema_has_started_and_assists(tmp_path):
    out = tmp_path / "out.parquet"
    df, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert "started" in df.columns
    assert "assists" in df.columns
    assert "started_inferred" in df.columns
    assert "passes_unknown_outcome" in df.columns
    assert "shots_missing_xg" in df.columns


def test_build_competition_season_returns_dismissal_report(tmp_path):
    out = tmp_path / "out.parquet"
    _, dismissals = build_competition_season(
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
    df, _ = build_competition_season(
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
    df, _ = build_competition_season(
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
    df, _ = build_competition_season(
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

def test_build_competition_season_failed_match_fails_league(tmp_path, monkeypatch):
    """
    A match that raises inside _process_match is logged, the remaining matches
    are still attempted, and then the whole league fails: MatchProcessingError
    names the failed match id and no part is written, so a part never silently
    lacks matches (Plan 003 C2 review).
    """
    import eleveniq.pipeline.build_player_match as _bpm

    original_process = _bpm._process_match
    attempted: list[int] = []

    def _patched_process(match_id, competition_id, season_id, *, data_root=None):
        attempted.append(match_id)
        if match_id == 9998:
            raise ValueError("synthetic failure for match 9998")
        return original_process(match_id, competition_id, season_id, data_root=data_root)

    def _patched_load_matches(competition_id, season_id, data_root=None):
        return [{"match_id": 9998}, {"match_id": 9999}]

    monkeypatch.setattr(_bpm, "_process_match", _patched_process)
    monkeypatch.setattr(_bpm, "load_matches", _patched_load_matches)

    out = tmp_path / "out.parquet"
    with pytest.raises(MatchProcessingError, match=r"\[9998\]"):
        build_competition_season(99, 1, data_root=_FIXTURE_ROOT, output_path=out)
    assert attempted == [9998, 9999]
    assert not out.exists()


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


# ---------------------------------------------------------------------------
# Starter cross-check against the Starting XI event (Plan 003 C2)
# ---------------------------------------------------------------------------

def _write_match(root: Path, match_id: int, events: list[dict], lineups: list[dict]) -> None:
    import json
    for sub, payload in (("events", events), ("lineups", lineups)):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / f"{match_id}.json").write_text(json.dumps(payload))
    (root / "matches" / "99").mkdir(parents=True, exist_ok=True)
    (root / "matches" / "99" / "1.json").write_text(json.dumps([{"match_id": match_id}]))


def _xi_event(team_id: int, player_ids: list[int]) -> dict:
    return {
        "id": f"xi{team_id}", "type": {"name": "Starting XI"}, "team": {"id": team_id},
        "minute": 0, "second": 0, "period": 1,
        "tactics": {"lineup": [{"player": {"id": p}} for p in player_ids]},
    }


_HALF_END = {"id": "he", "type": {"name": "Half End"}, "minute": 90, "second": 0, "period": 2}


def _spell(frm: str, to: str | None, reason: str) -> dict:
    return {"position_id": 10, "position": "Center Defensive Midfield",
            "from": frm, "to": to, "from_period": 1, "to_period": None if to is None else 1,
            "start_reason": reason}


class TestZeroLengthKickoffSpellGuard:
    """A bench player with a zero-length 00:00 spell (from == to == "00:00") must
    NOT be promoted to starter by the 00:00 fallback, even when the team has
    fewer than 11 starters and the player later comes on."""

    @pytest.fixture()
    def result(self, tmp_path):
        lineups = [{
            "team_id": 501, "team_name": "Synthetic FC",
            "lineup": [
                # Real starter whose start_reason is not "Starting XI" -> inferred.
                {"player_id": 5001, "player_name": "Inferred Starter",
                 "positions": [_spell("00:00", None, "Tactical Shift")]},
                # Bench player: zero-length 00:00 spell, then subbed on at 60:00.
                {"player_id": 5002, "player_name": "Bench Ghost",
                 "positions": [_spell("00:00", "00:00", "Tactical Shift"),
                               _spell("60:00", None, "Substitution - On (Tactical)")]},
            ],
        }]
        _write_match(tmp_path, 7001, [_xi_event(501, [5001]), _HALF_END], lineups)
        rows, report = _process_match(7001, 99, 1, data_root=tmp_path)
        return {r["player_id"]: r for r in rows}, report

    def test_bench_player_with_zero_length_spell_not_started(self, result):
        by_id, _ = result
        assert by_id[5002]["started"] is False
        assert by_id[5002]["started_inferred"] is False

    def test_real_kickoff_spell_still_inferred(self, result):
        by_id, _ = result
        assert by_id[5001]["started"] is True
        assert by_id[5001]["started_inferred"] is True

    def test_cross_check_passes(self, result):
        _, report = result
        assert not [d for d in report if d.get("issue") in STARTING_XI_ISSUES]


class TestStartingXiCrossCheck:
    def _lineups(self) -> list[dict]:
        return [{
            "team_id": 601, "team_name": "Check FC",
            "lineup": [
                {"player_id": 6001, "player_name": "A",
                 "positions": [_spell("00:00", None, "Starting XI")]},
                {"player_id": 6002, "player_name": "B",
                 "positions": [_spell("60:00", None, "Substitution - On (Tactical)")]},
            ],
        }]

    def test_fixture_match_has_no_cross_check_issues(self, process_match_result):
        _, report = process_match_result
        assert not [d for d in report if d.get("issue") in STARTING_XI_ISSUES]

    def test_mismatch_recorded_with_player_ids(self, tmp_path):
        _write_match(tmp_path, 7002, [_xi_event(601, [6002]), _HALF_END], self._lineups())
        _, report = _process_match(7002, 99, 1, data_root=tmp_path)
        entry = next(d for d in report if d.get("issue") == STARTING_XI_MISMATCH)
        assert entry["missing_from_derived"] == [6002]
        assert entry["extra_in_derived"] == [6001]

    def test_missing_starting_xi_event_recorded(self, tmp_path):
        _write_match(tmp_path, 7003, [_HALF_END], self._lineups())
        _, report = _process_match(7003, 99, 1, data_root=tmp_path)
        assert any(d.get("issue") == STARTING_XI_MISSING for d in report)

    def test_mismatch_fails_build_and_writes_nothing(self, tmp_path):
        _write_match(tmp_path, 7002, [_xi_event(601, [6002]), _HALF_END], self._lineups())
        out = tmp_path / "out.parquet"
        with pytest.raises(StarterCrossCheckError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=out)
        assert not out.exists()

    def test_duplicate_starting_xi_event_recorded_not_merged(self, tmp_path):
        # Two identical XI events for one team: merged, they would equal the
        # derived starters and pass silently, so a duplicate must be flagged.
        events = [_xi_event(601, [6001]), {**_xi_event(601, [6001]), "id": "xi601b"},
                  _HALF_END]
        _write_match(tmp_path, 7005, events, self._lineups())
        _, report = _process_match(7005, 99, 1, data_root=tmp_path)
        issues = [d for d in report if d.get("issue") in STARTING_XI_ISSUES]
        assert [(d["issue"], d["team_id"], d["starting_xi_events"]) for d in issues] == [
            ("starting_xi_duplicate", 601, 2)
        ]

    def test_duplicate_starting_xi_fails_build_and_writes_nothing(self, tmp_path):
        events = [_xi_event(601, [6001]), {**_xi_event(601, [6001]), "id": "xi601b"},
                  _HALF_END]
        _write_match(tmp_path, 7005, events, self._lineups())
        out = tmp_path / "out.parquet"
        with pytest.raises(StarterCrossCheckError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=out)
        assert not out.exists()

    def test_matching_xi_builds(self, tmp_path):
        _write_match(tmp_path, 7004, [_xi_event(601, [6001]), _HALF_END], self._lineups())
        out = tmp_path / "out.parquet"
        build_competition_season(99, 1, data_root=tmp_path, output_path=out)
        assert out.exists()


_RAW_ROOT = Path(__file__).resolve().parents[1] / "data" / "raw" / "statsbomb"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("match_id", "comp_id", "team_name", "n_inferred"),
    [
        (3754047, 2, "Swansea City", 10),
        (3825596, 11, "Sporting Gijón", 3),
    ],
)
def test_real_fallback_matches_pass_cross_check(match_id, comp_id, team_name, n_inferred):
    if not (_RAW_ROOT / "events" / f"{match_id}.json").exists():
        pytest.skip("Raw StatsBomb data not available")
    rows, report = _process_match(match_id, comp_id, 27, data_root=_RAW_ROOT)
    assert not [d for d in report if d.get("issue") in STARTING_XI_ISSUES]
    team_rows = [r for r in rows if r["team_name"] == team_name]
    assert sum(r["started"] for r in team_rows) == 11
    assert sum(r["started_inferred"] for r in team_rows) == n_inferred


# ---------------------------------------------------------------------------
# Multi-league entry point: per-league parts, skip-if-exists, --force, concat
# ---------------------------------------------------------------------------

class TestBuildAll:
    _CS = (
        CompetitionSeason(99, 1, "Fixture A", 2, "FULL", "statsbomb"),
        CompetitionSeason(99, 2, "Fixture B", 2, "PARTIAL", "statsbomb"),
    )

    @pytest.fixture()
    def calls(self, monkeypatch):
        """Replace build_competition_season with a stub that writes a tiny part."""
        import eleveniq.pipeline.build_player_match as _bpm
        made: list[tuple[int, int]] = []

        def _stub(competition_id, season_id, *, data_root=None, output_path=None):
            made.append((competition_id, season_id))
            df = pl.DataFrame({"competition_id": [competition_id], "season_id": [season_id],
                               "started": [True]})
            output_path.parent.mkdir(parents=True, exist_ok=True)
            df.write_parquet(output_path)
            return df, []

        monkeypatch.setattr(_bpm, "build_competition_season", _stub)
        return made

    def test_part_path_layout(self, tmp_path):
        assert part_path(9, 27, tmp_path) == tmp_path / "9_27.parquet"

    def test_builds_one_part_per_competition_season_and_concats(self, tmp_path, calls):
        out = tmp_path / "combined.parquet"
        build_all(self._CS, part_dir=tmp_path / "parts", output_path=out)
        assert calls == [(99, 1), (99, 2)]
        assert (tmp_path / "parts" / "99_1.parquet").exists()
        assert (tmp_path / "parts" / "99_2.parquet").exists()
        assert pl.read_parquet(out)["season_id"].to_list() == [1, 2]

    def test_existing_part_skipped_without_force(self, tmp_path, calls):
        parts = tmp_path / "parts"
        build_all(self._CS, part_dir=parts, output_path=tmp_path / "c.parquet")
        calls.clear()
        summaries = build_all(self._CS, part_dir=parts, output_path=tmp_path / "c.parquet")
        assert calls == []
        assert all(s["skipped"] for s in summaries.values())

    def test_force_rebuilds_existing_part(self, tmp_path, calls):
        parts = tmp_path / "parts"
        build_all(self._CS, part_dir=parts, output_path=tmp_path / "c.parquet")
        calls.clear()
        build_all(self._CS, force=True, part_dir=parts, output_path=tmp_path / "c.parquet")
        assert calls == [(99, 1), (99, 2)]

    def test_skip_concat_writes_no_combined_file(self, tmp_path, calls):
        out = tmp_path / "combined.parquet"
        build_all(self._CS, concat=False, part_dir=tmp_path / "parts", output_path=out)
        assert not out.exists()

    def test_concat_with_missing_part_leaves_combined_untouched(self, tmp_path, caplog):
        parts = tmp_path / "parts"
        out = tmp_path / "c.parquet"
        pl.DataFrame({"sentinel": [1]}).write_parquet(out)
        before = out.read_bytes()
        parts.mkdir()
        pl.DataFrame({"competition_id": [99], "season_id": [1],
                      "started": [True]}).write_parquet(part_path(99, 1, parts))
        with caplog.at_level("WARNING"):
            result = concat_parts(self._CS, part_dir=parts, output_path=out)
        assert result is None
        assert out.read_bytes() == before
        assert "99_2.parquet" in caplog.text
        assert "99_1.parquet" not in caplog.text

    def test_subset_build_concats_full_config(self, tmp_path, calls, caplog):
        """A CLI-style single-league build (only=) must never shrink the combined
        file to that league: with another configured part missing, the combined
        file is left untouched; once every part exists, the concat covers all."""
        parts = tmp_path / "parts"
        out = tmp_path / "combined.parquet"
        pl.DataFrame({"sentinel": [1]}).write_parquet(out)
        before = out.read_bytes()

        with caplog.at_level("WARNING"):
            build_all(self._CS, only=self._CS[:1], part_dir=parts, output_path=out)
        assert calls == [(99, 1)]
        assert out.read_bytes() == before
        assert "99_2.parquet" in caplog.text

        build_all(self._CS, only=self._CS[1:], part_dir=parts, output_path=out)
        assert calls == [(99, 1), (99, 2)]
        assert sorted(pl.read_parquet(out)["season_id"].to_list()) == [1, 2]

    def test_only_must_be_configured(self, tmp_path, calls):
        stray = (CompetitionSeason(1, 1, "Stray", 2, "FULL", "statsbomb"),)
        with pytest.raises(ValueError, match="unconfigured"):
            build_all(self._CS, only=stray, part_dir=tmp_path, output_path=tmp_path / "c.parquet")
        assert calls == []

    @pytest.mark.parametrize(
        "error", [StarterCrossCheckError("synthetic"), MatchProcessingError("[123]")]
    )
    def test_failed_forced_rebuild_removes_stale_part_and_blocks_concat(
        self, tmp_path, calls, monkeypatch, error
    ):
        """A forced rebuild that fails must not leave the pre-change part behind:
        the part is gone, the other league still rebuilds, the combined file is
        untouched, and a later concat refuses because the part is missing."""
        import eleveniq.pipeline.build_player_match as _bpm
        parts = tmp_path / "parts"
        out = tmp_path / "combined.parquet"
        build_all(self._CS, part_dir=parts, output_path=out)  # stale parts + combined
        before = out.read_bytes()
        calls.clear()
        stub = _bpm.build_competition_season

        def _fail_first(competition_id, season_id, **kw):
            if season_id == 1:
                raise error
            return stub(competition_id, season_id, **kw)

        monkeypatch.setattr(_bpm, "build_competition_season", _fail_first)
        with pytest.raises(RuntimeError, match="Fixture A"):
            build_all(self._CS, force=True, part_dir=parts, output_path=out)
        assert not part_path(99, 1, parts).exists()
        assert calls == [(99, 2)]
        assert out.read_bytes() == before
        assert concat_parts(self._CS, part_dir=parts, output_path=out) is None
        assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# QA: kickoff-spell and Starting XI cross-check edge cases (Plan 003 C2)
# ---------------------------------------------------------------------------

class TestKickoffSpellAfterZeroLengthSpell:
    """A real starter whose start_reason is not "Starting XI" and whose FIRST
    spell is zero-length at 00:00 (instant shape change at kick-off), followed
    by a real 00:00 spell, must still be inferred as a starter. Guards against
    the zero-length guard being implemented as "look at the first spell only"."""

    def test_starter_with_leading_zero_length_spell_is_inferred(self, tmp_path):
        lineups = [{
            "team_id": 701, "team_name": "Shift FC",
            "lineup": [
                {"player_id": 7101, "player_name": "Shifted Starter",
                 "positions": [_spell("00:00", "00:00", "Tactical Shift"),
                               _spell("00:00", None, "Tactical Shift")]},
            ],
        }]
        _write_match(tmp_path, 7101, [_xi_event(701, [7101]), _HALF_END], lineups)
        rows, report = _process_match(7101, 99, 1, data_root=tmp_path)
        row = next(r for r in rows if r["player_id"] == 7101)
        assert row["started"] is True
        assert row["started_inferred"] is True
        assert not [d for d in report if d.get("issue") in STARTING_XI_ISSUES]


class TestStartingXiCrossCheckPerTeam:
    """The cross-check is per team-match: a missing Starting XI event for one
    team must be attributed to that team only, and must fail the build."""

    @staticmethod
    def _two_team_lineups() -> list[dict]:
        return [
            {"team_id": 801, "team_name": "Home FC", "lineup": [
                {"player_id": 8101, "player_name": "H",
                 "positions": [_spell("00:00", None, "Starting XI")]}]},
            {"team_id": 802, "team_name": "Away FC", "lineup": [
                {"player_id": 8201, "player_name": "A",
                 "positions": [_spell("00:00", None, "Starting XI")]}]},
        ]

    def test_missing_xi_attributed_to_the_right_team(self, tmp_path):
        _write_match(tmp_path, 7201, [_xi_event(801, [8101]), _HALF_END],
                     self._two_team_lineups())
        _, report = _process_match(7201, 99, 1, data_root=tmp_path)
        issues = [d for d in report if d.get("issue") in STARTING_XI_ISSUES]
        assert [(d["issue"], d["team_id"]) for d in issues] == [
            ("starting_xi_missing", 802)
        ]

    def test_missing_xi_fails_build_and_writes_nothing(self, tmp_path):
        _write_match(tmp_path, 7201, [_xi_event(801, [8101]), _HALF_END],
                     self._two_team_lineups())
        out = tmp_path / "out.parquet"
        with pytest.raises(StarterCrossCheckError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=out)
        assert not out.exists()


class TestBuildAllResumeAndConcat:
    """Resumable runs (Plan 003 §4): a rerun that skips an existing part and
    builds a missing one must still produce a combined file containing every
    configured league, and only configured leagues."""

    _CS = (
        CompetitionSeason(98, 1, "Existing", 2, "FULL", "statsbomb"),
        CompetitionSeason(98, 2, "New", 2, "FULL", "statsbomb"),
    )

    @staticmethod
    def _part(path: Path, comp: int, season: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"competition_id": [comp], "season_id": [season],
                      "started": [True]}).write_parquet(path)

    def test_combined_includes_skipped_and_newly_built_parts(self, tmp_path, monkeypatch):
        import eleveniq.pipeline.build_player_match as _bpm
        parts = tmp_path / "parts"
        self._part(part_path(98, 1, parts), 98, 1)
        built: list[tuple[int, int]] = []

        def _stub(competition_id, season_id, *, data_root=None, output_path=None):
            built.append((competition_id, season_id))
            self._part(output_path, competition_id, season_id)
            return pl.read_parquet(output_path), []

        monkeypatch.setattr(_bpm, "build_competition_season", _stub)
        out = tmp_path / "combined.parquet"
        summaries = build_all(self._CS, part_dir=parts, output_path=out)

        assert built == [(98, 2)]
        assert summaries[(98, 1)]["skipped"] is True
        assert summaries[(98, 2)]["skipped"] is False
        assert sorted(pl.read_parquet(out)["season_id"].to_list()) == [1, 2]

    def test_concat_ignores_unconfigured_stale_part(self, tmp_path):
        parts = tmp_path / "parts"
        for cs in self._CS:
            self._part(part_path(cs.competition_id, cs.season_id, parts),
                       cs.competition_id, cs.season_id)
        self._part(part_path(16, 27, parts), 16, 27)  # e.g. old Champions League part
        df = concat_parts(self._CS, part_dir=parts, output_path=tmp_path / "c.parquet")
        assert set(df["competition_id"].to_list()) == {98}
        assert len(df) == 2


class TestSummariseQualityReport:
    """Summary counts per category; the dismissal count must only include real
    dismissal entries (no "issue" key), not fallback or cross-check entries."""

    def test_counts_each_category_separately(self):
        from eleveniq.pipeline.build_player_match import summarise_quality_report
        report = [
            {"match_id": 1, "player_id": 10, "dismissal_minute": 30.0},
            {"match_id": 1, "player_id": 11, "dismissal_minute": 80.0},
            {"issue": "dismissal_missing_timestamp", "match_id": 2, "player_id": 12},
            {"issue": "started_inferred_fallback", "match_id": 3, "players": ["a", "b", "c"]},
            {"issue": "started_inferred_fallback", "match_id": 4, "players": ["d"]},
            {"issue": "starter_count_mismatch", "match_id": 5},
            {"issue": "starting_xi_mismatch", "match_id": 6},
            {"issue": "dropped_event_allowed", "match_id": 7, "player_id": 13},
            {"issue": "dropped_event_allowed", "match_id": 8, "player_id": 14},
            {"issue": "substitution_capped", "match_id": 9, "player_id": 15},
            {"issue": "reversed_period_stint", "match_id": 9, "player_id": 16},
            {"issue": "reversed_period_stint", "match_id": 9, "player_id": 17},
            {"issue": "on_pitch_count_exceeded", "match_id": 10, "team_id": 1},
            {"issue": "player_minutes_exceed_duration", "match_id": 11, "player_id": 18},
            {"issue": "player_minutes_exceed_duration", "match_id": 11, "player_id": 19},
        ]
        assert summarise_quality_report(report) == {
            "dismissals": 2,
            "missing_timestamp": 1,
            "fallback_team_matches": 2,
            "fallback_players": 4,
            "starter_count_mismatches": 1,
            "dropped_events_allowed": 2,
            "substitution_capped": 1,
            "reversed_period_stints": 2,
            "on_pitch_count_exceeded": 1,
            "player_minutes_exceed_duration": 2,
        }

    def test_empty_report_all_zero(self):
        from eleveniq.pipeline.build_player_match import summarise_quality_report
        assert set(summarise_quality_report([]).values()) == {0}


# ---------------------------------------------------------------------------
# Fail-loud data checks (plan 004 H1.1, H1.2, H1.4, H1.5, H1.7)
# ---------------------------------------------------------------------------

from collections import Counter

from eleveniq.pipeline.build_player_match import (
    DataQualityError,
    _aggregate_events,
    summarise_quality_report,
)

_TEAM = 801
_STARTER = 8001
_BENCH = 8002


def _ev(etype: str, pid: int | None = _STARTER, *, period: int = 1, minute: int = 10,
        **extra) -> dict:
    event = {"id": f"{etype}-{pid}-{minute}-{period}", "index": minute,
             "type": {"name": etype}, "team": {"id": _TEAM},
             "period": period, "minute": minute, "second": 0, **extra}
    if pid is not None:
        event["player"] = {"id": pid}
    return event


def _quality_lineups(starter_positions: list[dict] | None = None,
                     bench_positions: list[dict] | None = None) -> list[dict]:
    return [{
        "team_id": _TEAM, "team_name": "Quality FC",
        "lineup": [
            {"player_id": _STARTER, "player_name": "Starter",
             "positions": starter_positions or [_spell("00:00", None, "Starting XI")]},
            {"player_id": _BENCH, "player_name": "Bench",
             "positions": bench_positions or []},
        ],
    }]


def _shot(outcome: str, *, xg: float | None = 0.1, **kw) -> dict:
    shot = {"outcome": {"name": outcome}}
    if xg is not None:
        shot["statsbomb_xg"] = xg
    return _ev("Shot", shot=shot, location=[110, 40], **kw)


def _build(tmp_path, events, lineups=None):
    """Write one synthetic match and build it; return (out_path, result-or-exc)."""
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events, _HALF_END],
                 lineups or _quality_lineups())
    out = tmp_path / "out.parquet"
    return out, build_competition_season(99, 1, data_root=tmp_path, output_path=out)


def _build_fails(tmp_path, events, lineups=None) -> str:
    out = tmp_path / "out.parquet"
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events, _HALF_END],
                 lineups or _quality_lineups())
    with pytest.raises(DataQualityError) as exc:
        build_competition_season(99, 1, data_root=tmp_path, output_path=out)
    assert not out.exists()
    return str(exc.value)


class TestAggregateEventsQualityCounters:
    def _issues(self, events: list[dict]) -> Counter:
        issues: Counter = Counter()
        _aggregate_events(events, issues)
        return issues

    def test_unknown_shot_outcome_recorded_with_value(self):
        assert self._issues([_shot("Saved To Post")]) == Counter(
            {("unknown_shot_outcome", "Saved To Post"): 1})

    @pytest.mark.parametrize("outcome", ["Saved to Post", "Saved Off Target", "Goal", "Off T"])
    def test_known_shot_outcome_not_recorded(self, outcome):
        assert not self._issues([_shot(outcome)])

    def test_saved_to_post_counts_on_target(self):
        assert _aggregate_events([_shot("Saved to Post")], Counter())["shots_on_target"] == 1

    def test_saved_off_target_not_on_target(self):
        assert _aggregate_events([_shot("Saved Off Target")], Counter())["shots_on_target"] == 0

    def test_unknown_tackle_outcome_recorded(self):
        ev = _ev("Duel", duel={"type": {"name": "Tackle"}, "outcome": {"name": "Draw"}})
        assert self._issues([ev]) == Counter({("unknown_tackle_outcome", "Draw"): 1})

    def test_missing_tackle_outcome_recorded_as_none(self):
        ev = _ev("Duel", duel={"type": {"name": "Tackle"}})
        assert self._issues([ev]) == Counter({("unknown_tackle_outcome", None): 1})

    def test_unknown_duel_type_recorded(self):
        ev = _ev("Duel", duel={"type": {"name": "Aerial Won"}})
        assert self._issues([ev]) == Counter({("unknown_duel_type", "Aerial Won"): 1})

    def test_known_duels_not_recorded(self):
        events = [
            _ev("Duel", duel={"type": {"name": "Aerial Lost"}}),
            _ev("Duel", duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
        ]
        assert not self._issues(events)

    @pytest.mark.parametrize("missing", ["location", "end_location"])
    def test_pass_missing_location_recorded(self, missing):
        ev = _ev("Pass", location=[50, 40], **{"pass": {"end_location": [70, 40], "length": 20}})
        if missing == "location":
            del ev["location"]
        else:
            del ev["pass"]["end_location"]
        assert self._issues([ev]) == Counter({("pass_missing_location", None): 1})

    @pytest.mark.parametrize("missing", ["location", "end_location"])
    def test_carry_missing_location_recorded(self, missing):
        ev = _ev("Carry", location=[50, 40], carry={"end_location": [60, 40]})
        if missing == "location":
            del ev["location"]
        else:
            del ev["carry"]["end_location"]
        assert self._issues([ev]) == Counter({("carry_missing_location", None): 1})

    def test_issues_counter_is_required(self):
        # No default: a caller cannot skip the fail-loud event checks by accident.
        with pytest.raises(TypeError):
            _aggregate_events([_shot("Saved")])  # type: ignore[call-arg]


class TestAggregateEventsUnknownOutcomePasses:
    def _pass(self, outcome: str | None, *, cross: bool = False, length: float = 20.0,
              pass_type: str | None = None) -> dict:
        data: dict = {"end_location": [70, 40], "length": length}
        if outcome is not None:
            data["outcome"] = {"name": outcome}
        if cross:
            data["cross"] = True
        if pass_type is not None:
            data["type"] = {"name": pass_type}
        return _ev("Pass", location=[50, 40], **{"pass": data})

    def test_unknown_cross_counted_attempted_and_unknown(self):
        r = _aggregate_events([self._pass("Unknown", cross=True), self._pass(None, cross=True)], Counter())
        assert (r["crosses_attempted"], r["crosses_completed"], r["crosses_unknown_outcome"]) \
            == (2, 1, 1)

    def test_incomplete_cross_not_unknown(self):
        r = _aggregate_events([self._pass("Incomplete", cross=True)], Counter())
        assert r["crosses_unknown_outcome"] == 0

    def test_unknown_long_pass_counted_attempted_and_unknown(self):
        r = _aggregate_events([self._pass("Unknown", length=40.0), self._pass(None, length=40.0)], Counter())
        assert (r["long_passes_attempted"], r["long_passes_completed"],
                r["long_passes_unknown_outcome"]) == (2, 1, 1)

    def test_unknown_set_piece_long_pass_not_counted(self):
        r = _aggregate_events([self._pass("Unknown", length=40.0, pass_type="Goal Kick")], Counter())
        assert r["long_passes_unknown_outcome"] == 0

    def test_unknown_short_pass_not_a_long_pass(self):
        r = _aggregate_events([self._pass("Unknown", length=10.0)], Counter())
        assert r["long_passes_unknown_outcome"] == 0
        assert r["passes_unknown_outcome"] == 1


class TestBuildFailsOnQualityIssues:
    def test_clean_match_builds(self, tmp_path):
        out, (df, _) = _build(tmp_path, [_shot("Saved to Post")])
        assert out.exists()
        assert df["shots_on_target"].sum() == 1

    def test_new_unknown_outcome_columns_written(self, tmp_path):
        _, (df, _) = _build(tmp_path, [])
        assert {"crosses_unknown_outcome", "long_passes_unknown_outcome"} <= set(df.columns)

    def test_unknown_shot_outcome_fails(self, tmp_path):
        assert "unknown_shot_outcome" in _build_fails(tmp_path, [_shot("Saved To Post")])

    def test_unknown_tackle_outcome_fails(self, tmp_path):
        ev = _ev("Duel", duel={"type": {"name": "Tackle"}, "outcome": {"name": "Draw"}})
        assert "unknown_tackle_outcome" in _build_fails(tmp_path, [ev])

    def test_unknown_duel_type_fails(self, tmp_path):
        ev = _ev("Duel", duel={"type": {"name": "Aerial Won"}})
        assert "unknown_duel_type" in _build_fails(tmp_path, [ev])

    def test_pass_missing_location_fails(self, tmp_path):
        ev = _ev("Pass", **{"pass": {"end_location": [70, 40], "length": 20}})
        assert "pass_missing_location" in _build_fails(tmp_path, [ev])

    def test_carry_missing_location_fails(self, tmp_path):
        ev = _ev("Carry", location=[50, 40], carry={})
        assert "carry_missing_location" in _build_fails(tmp_path, [ev])

    def test_shot_missing_xg_fails(self, tmp_path):
        assert "shots_missing_xg" in _build_fails(tmp_path, [_shot("Saved", xg=None)])

    def test_unknown_position_minutes_fail(self, tmp_path):
        spell = {**_spell("00:00", None, "Starting XI"), "position": "Sweeper"}
        msg = _build_fails(tmp_path, [], _quality_lineups(starter_positions=[spell]))
        assert "minutes_unknown" in msg
        assert "Sweeper" in msg


class TestDroppedEvents:
    def _card(self, pid: int, card: str = "Yellow Card") -> dict:
        return _ev("Bad Behaviour", pid, minute=60, bad_behaviour={"card": {"name": card}})

    @pytest.mark.parametrize("card", ["Yellow Card", "Red Card", "Second Yellow"])
    def test_bench_card_allowed(self, tmp_path, card):
        out, (_, report) = _build(tmp_path, [self._card(_BENCH, card)])
        assert out.exists()
        dropped = [d for d in report if d.get("issue") == DROPPED_EVENT_ALLOWED]
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "zero_minutes"
        assert dropped[0]["card"] == card
        assert summarise_quality_report(report)["dropped_events_allowed"] == 1

    def test_non_card_event_by_bench_player_fails(self, tmp_path):
        ev = _ev("Pressure", _BENCH)
        assert "dropped_event" in _build_fails(tmp_path, [ev])

    def test_event_by_player_not_in_lineup_fails(self, tmp_path):
        msg = _build_fails(tmp_path, [self._card(9999)])
        assert "dropped_event" in msg
        assert "not_in_lineup" in msg

    def test_card_by_zero_minute_player_with_stints_fails(self, tmp_path):
        # Stints that compute to 0 minutes are a minutes bug, not a bench card.
        lineups = _quality_lineups(
            bench_positions=[_spell("60:00", "60:00", "Substitution - On (Tactical)")])
        msg = _build_fails(tmp_path, [self._card(_BENCH)], lineups)
        assert "dropped_event" in msg

    def test_player_with_minutes_has_no_dropped_events(self, tmp_path):
        _, (_, report) = _build(tmp_path, [self._card(_STARTER)])
        assert not [d for d in report if str(d.get("issue", "")).startswith("dropped_event")]


class TestPenaltyShootoutExcluded:
    """Period 5 (shootout) events count toward no metric (plan 004 H1.7)."""

    def test_shootout_goal_not_counted(self, tmp_path):
        events = [_shot("Goal", period=2, minute=50),
                  _shot("Goal", period=5, minute=121)]
        _, (df, _) = _build(tmp_path, events)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["shots"] == 1
        assert row["goals"] == 1
        assert row["xg"] == pytest.approx(0.1)

    def test_extra_time_red_card_still_cuts_minutes(self, tmp_path):
        # Only period 5 is ignored: a period-3 red card ends an open stint.
        events = [_ev("Bad Behaviour", period=3, minute=100,
                      bad_behaviour={"card": {"name": "Red Card"}}),
                  _ev("Pressure", _BENCH, period=4, minute=120)]
        lineups = _quality_lineups(
            bench_positions=[_spell("110:00", None, "Substitution - On (Tactical)")])
        _, (df, _) = _build(tmp_path, events, lineups)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(100.0)

    def test_dismissal_after_match_end_fails_build(self, tmp_path, monkeypatch):
        # A dismissal minute past the match duration is never capped silently.
        # Unreachable while the duration is the last period<=4 event (the card is
        # one), so the duration is forced below the card minute here.
        monkeypatch.setattr(
            "eleveniq.pipeline.build_player_match.compute_match_duration", lambda _: 50.0)
        card = _ev("Bad Behaviour", period=2, minute=60,
                   bad_behaviour={"card": {"name": "Red Card"}})
        assert "dismissal_after_match_end" in _build_fails(tmp_path, [card])

    def test_shootout_event_by_bench_player_is_not_dropped_event(self, tmp_path):
        # Filtered as a shootout event, so not reported as a dropped event.
        _, (_, report) = _build(tmp_path, [_shot("Goal", pid=_BENCH, period=5, minute=121)])
        assert not [d for d in report if str(d.get("issue", "")).startswith("dropped_event")]


def test_build_competition_season_twice_identical_row_by_row(tmp_path):
    first, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=tmp_path / "a.parquet")
    second, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=tmp_path / "b.parquet")
    assert first.equals(second)
    assert pl.read_parquet(tmp_path / "a.parquet").equals(pl.read_parquet(tmp_path / "b.parquet"))


# ---------------------------------------------------------------------------
# Fail-loud edge cases not covered above (QA, plan 004 H1.1/H1.2/H1.5/H1.6/H1.7)
# ---------------------------------------------------------------------------

class TestFailLoudEdgeCases:
    def test_shot_without_outcome_fails_build(self, tmp_path):
        # A missing outcome must not be silently counted as "off target".
        ev = _ev("Shot", location=[110, 40], shot={"statsbomb_xg": 0.1})
        assert "unknown_shot_outcome" in _build_fails(tmp_path, [ev])

    def test_small_unknown_stint_fails_even_when_primary_group_known(self, tmp_path):
        # Multi-position player: 60 min CDM then 30 min in an unmapped position.
        # Primary group is DM-CM, but any UNKNOWN minutes must still fail (H1.1).
        spells = [
            {**_spell("00:00", "60:00", "Starting XI"), "to_period": 2},
            {**_spell("60:00", None, "Tactical Shift"), "position": "Sweeper",
             "from_period": 2},
        ]
        msg = _build_fails(tmp_path, [], _quality_lineups(starter_positions=spells))
        assert "minutes_unknown" in msg
        assert "Sweeper" in msg

    @pytest.mark.parametrize("event", [
        # Bad Behaviour with no card is not a card: not allow-listed.
        {"type": "Bad Behaviour", "extra": {}},
        # A card on a Foul Committed event is not the allow-listed event type.
        {"type": "Foul Committed", "extra": {"foul_committed": {"card": {"name": "Red Card"}}}},
    ], ids=["bad_behaviour_no_card", "foul_committed_card"])
    def test_bench_non_allowlisted_event_fails(self, tmp_path, event):
        ev = _ev(event["type"], _BENCH, minute=60, **event["extra"])
        assert "dropped_event" in _build_fails(tmp_path, [ev])

    def test_event_missing_period_fails_league(self, tmp_path):
        # H1.6: period is required, never defaulted to 1; the match fails the league.
        ev = _ev("Pressure")
        del ev["period"]
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), ev, _HALF_END],
                     _quality_lineups())
        out = tmp_path / "out.parquet"
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=out)
        assert not out.exists()

    def test_shootout_red_card_does_not_cut_minutes(self, tmp_path):
        # H1.7: the dismissal lookup ignores period 5, so a shootout card clamps nothing.
        card = _ev("Bad Behaviour", period=5, minute=125,
                   bad_behaviour={"card": {"name": "Red Card"}})
        out, (df, _) = _build(tmp_path, [card])
        assert out.exists()
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(90.0)

    def test_unknown_long_cross_counted_in_all_unknown_counters(self, tmp_path):
        # One pass that is a cross, long and Unknown: excluded from all three
        # completion denominators, still counted as attempted in each.
        ev = _ev("Pass", location=[60, 5], **{"pass": {
            "end_location": [110, 40], "length": 45.0, "cross": True,
            "outcome": {"name": "Unknown"}}})
        r = _aggregate_events([ev], Counter())
        assert (r["passes_attempted"], r["passes_completed"], r["passes_unknown_outcome"]) == (1, 0, 1)
        assert (r["crosses_attempted"], r["crosses_completed"], r["crosses_unknown_outcome"]) == (1, 0, 1)
        assert (r["long_passes_attempted"], r["long_passes_completed"],
                r["long_passes_unknown_outcome"]) == (1, 0, 1)


# ---------------------------------------------------------------------------
# Substitution-off cap and team minutes ceiling (plan 004 H2, ADR 002 §3)
# ---------------------------------------------------------------------------

def _team_lineups(player_positions: list[list[dict]]) -> list[dict]:
    """One team; player i (id _TEAM_BASE + i) gets player_positions[i]."""
    return [{
        "team_id": _TEAM, "team_name": "Quality FC",
        "lineup": [
            {"player_id": _TEAM_BASE + i, "player_name": f"P{i}", "positions": positions}
            for i, positions in enumerate(player_positions)
        ],
    }]


_TEAM_BASE = 8200


def _write_team_match(tmp_path, player_positions, events) -> list[dict]:
    lineups = _team_lineups(player_positions)
    starters = [p["player_id"] for p in lineups[0]["lineup"]
                if any(s["start_reason"] == "Starting XI" for s in p["positions"])]
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, starters), *events, _HALF_END], lineups)
    return lineups


def _build_team(tmp_path, player_positions, events=()):
    """Build one match of distinct players; the Starting XI event lists every
    player with a "Starting XI" stint. Returns (df, report)."""
    _write_team_match(tmp_path, player_positions, list(events))
    return build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "out.parquet")


def _build_team_fails(tmp_path, player_positions, events=()) -> str:
    _write_team_match(tmp_path, player_positions, list(events))
    out = tmp_path / "out.parquet"
    with pytest.raises(DataQualityError) as exc:
        build_competition_season(99, 1, data_root=tmp_path, output_path=out)
    assert not out.exists()
    return str(exc.value)


def _sub_off(pid: int, *, period: int, minute: int, second: int = 0) -> dict:
    ev = _ev("Substitution", pid, period=period, minute=minute,
             substitution={"replacement": {"id": _BENCH}})
    ev["second"] = second
    return ev


class TestSubstitutionOffCap:
    def test_player_on_stint_after_treatment_capped_at_own_substitution(self, tmp_path):
        # Match 3825707 pattern: Player Off for treatment, Player On at the
        # instant he is replaced; the lineup still runs his stint to the whistle.
        spells = [
            {**_spell("00:00", "31:29", "Starting XI"), "end_reason": "Player Off"},
            {**_spell("37:02", None, "Player On"), "end_reason": "Final Whistle"},
        ]
        lineups = _quality_lineups(
            starter_positions=spells,
            bench_positions=[_spell("37:02", None, "Substitution - On (Injury)")])
        _, (df, report) = _build(
            tmp_path, [_sub_off(_STARTER, period=1, minute=37, second=2)], lineups)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(31 + 29 / 60)
        capped = [d for d in report if d.get("issue") == SUBSTITUTION_CAPPED]
        assert len(capped) == 1
        assert capped[0]["minutes_removed"] == pytest.approx(90 - (37 + 2 / 60))

    def test_explicit_to_past_own_substitution_is_capped(self, tmp_path):
        lineups = _quality_lineups(starter_positions=[_spell("00:00", "80:00", "Starting XI")])
        _, (df, _) = _build(tmp_path, [_sub_off(_STARTER, period=1, minute=30)], lineups)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(30.0)

    def test_first_half_stoppage_stint_before_half_time_substitution_is_allowed(self, tmp_path):
        # Bale, 3825739: back on at 46:01 in period 1, replaced at the period-2
        # restart (45:00). (1, 46:01) precedes (2, 45:00): no failure.
        spells = [
            {**_spell("00:00", "44:00", "Starting XI")},
            {**_spell("46:01", None, "Player On")},
        ]
        _, (df, report) = _build(
            tmp_path, [_sub_off(_STARTER, period=2, minute=45)],
            _quality_lineups(starter_positions=spells))
        assert not [d for d in report if d.get("issue") == STINT_AFTER_SUBSTITUTION]
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(44.0)

    def test_stint_starting_after_own_substitution_fails_build(self, tmp_path):
        spells = [
            {**_spell("00:00", "30:00", "Starting XI")},
            {**_spell("50:00", None, "Player On"), "from_period": 2},
        ]
        msg = _build_fails(tmp_path, [_sub_off(_STARTER, period=1, minute=30)],
                           _quality_lineups(starter_positions=spells))
        assert "stint_after_substitution" in msg

    def test_stint_starting_at_substitution_instant_is_allowed(self, tmp_path):
        spells = [{**_spell("00:00", "30:00", "Starting XI")},
                  {**_spell("30:00", None, "Player On")}]
        _, (df, _) = _build(tmp_path, [_sub_off(_STARTER, period=1, minute=30)],
                            _quality_lineups(starter_positions=spells))
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(30.0)

    def test_substituted_off_twice_fails_build(self, tmp_path):
        events = [_sub_off(_STARTER, period=1, minute=30),
                  _sub_off(_STARTER, period=2, minute=60)]
        assert "multiple_substitutions_off" in _build_fails(tmp_path, events)

    def test_team_minutes_above_eleven_times_duration_fails_build(self, tmp_path):
        # 12 distinct full-match players: 12 × 90 > 11 × 90, each within 90.
        msg = _build_team_fails(tmp_path, [[_spell("00:00", None, "Starting XI")]] * 12)
        assert "team_minutes_exceed_ceiling" in msg
        assert "player_minutes_exceed_duration" not in msg

    def test_team_minutes_at_ceiling_passes(self, tmp_path):
        df, report = _build_team(tmp_path, [[_spell("00:00", None, "Starting XI")]] * 11)
        assert df["minutes_played"].sum() == pytest.approx(11 * 90.0)
        assert not [d for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED]

    def test_reversed_period_stint_reported_not_failed(self, tmp_path):
        # 3879549 pattern: "45:00 p2 → 45:51 p1".
        spells = [{**_spell("00:00", "45:00", "Starting XI")},
                  {**_spell("45:00", "45:51", "Tactical Shift"), "from_period": 2}]
        _, (_, report) = _build(tmp_path, [], _quality_lineups(starter_positions=spells))
        assert [d for d in report if d.get("issue") == REVERSED_PERIOD_STINT]


class TestSubstitutionOffPipelineEdges:
    """ADR 002 §3 edge cases not covered by TestSubstitutionOffCap: the replacement,
    a substitute who is himself replaced, shootout/extra-time periods, and a
    bench card after the substitution."""

    _THIRD = 8003

    @staticmethod
    def _row(df, pid):
        return df.filter(pl.col("player_id") == pid).row(0, named=True)

    def test_ordinary_substitution_replacement_keeps_minutes_and_nothing_reported(self, tmp_path):
        # The replacement is substitution.replacement, not the event's player: he must
        # not be capped at his own on-minute. A clean sub must not be reported as
        # substitution_capped (ADR: 33 capped of 8,922 subs, not every sub).
        lineups = _quality_lineups(
            starter_positions=[_spell("00:00", "60:00", "Starting XI")],
            bench_positions=[{**_spell("60:00", None, "Substitution - On"), "from_period": 2}])
        _, (df, report) = _build(tmp_path, [_sub_off(_STARTER, period=2, minute=60)], lineups)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(60.0)
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(30.0)
        assert not [d for d in report if d.get("issue") == SUBSTITUTION_CAPPED]

    def test_substitute_who_is_later_substituted_off(self, tmp_path):
        # Sub-of-a-sub: on at 30:00 (p1), replaced at 70:00 (p2); lineup leaves to=None.
        lineups = [{
            "team_id": _TEAM, "team_name": "Quality FC",
            "lineup": [
                {"player_id": _STARTER, "player_name": "Starter",
                 "positions": [_spell("00:00", "30:00", "Starting XI")]},
                {"player_id": _BENCH, "player_name": "Bench",
                 "positions": [_spell("30:00", None, "Substitution - On")]},
                {"player_id": self._THIRD, "player_name": "Third",
                 "positions": [{**_spell("70:00", None, "Substitution - On"),
                                "from_period": 2}]},
            ],
        }]
        events = [
            _sub_off(_STARTER, period=1, minute=30),
            _ev("Substitution", _BENCH, period=2, minute=70,
                substitution={"replacement": {"id": self._THIRD}}),
        ]
        _, (df, report) = _build(tmp_path, events, lineups)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(30.0)
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(40.0)
        assert self._row(df, self._THIRD)["minutes_played"] == pytest.approx(20.0)
        assert df["minutes_played"].sum() == pytest.approx(90.0)

    def test_second_half_stoppage_time_substitution(self, tmp_path):
        # Replaced at 93:20 (p2), lineup to=None, match runs to 95:00.
        events = [_sub_off(_STARTER, period=2, minute=93, second=20),
                  _ev("Half End", None, period=2, minute=95)]
        lineups = _quality_lineups(
            bench_positions=[{**_spell("93:20", None, "Substitution - On"), "from_period": 2}])
        _, (df, _) = _build(tmp_path, events, lineups)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(93 + 20 / 60)
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(95 - (93 + 20 / 60))

    def test_second_half_stoppage_stint_before_extra_time_substitution_is_allowed(self, tmp_path):
        # Same ordering rule as the Bale case, one half later: period 3 restarts at
        # 90:00, so (2, 92:30) precedes a substitution at (3, 91:00).
        spells = [{**_spell("00:00", "88:00", "Starting XI"), "to_period": 2},
                  {**_spell("92:30", None, "Player On"), "from_period": 2}]
        events = [_sub_off(_STARTER, period=3, minute=91),
                  _ev("Half End", None, period=4, minute=120)]
        _, (df, report) = _build(tmp_path, events, _quality_lineups(starter_positions=spells))
        assert not [d for d in report if d.get("issue") == STINT_AFTER_SUBSTITUTION]
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(88.0)

    def test_shootout_substitution_does_not_extend_minutes(self, tmp_path):
        # A Substitution in period 5 (shootout) is not playing time: it must neither
        # cap nor extend the stint past the 120:00 match duration.
        events = [_ev("Half End", None, period=4, minute=120),
                  _sub_off(_STARTER, period=5, minute=125)]
        _, (df, report) = _build(tmp_path, events)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(120.0)
        assert not [d for d in report if d.get("issue") == SUBSTITUTION_CAPPED]

    def test_card_on_bench_after_substitution_uses_earlier_substitution_cap(self, tmp_path):
        # Replaced at 60:00, then a red card on the bench at 80:00. The exit cap is
        # the earlier of the two; the open stint must stop at 60, not 80.
        events = [
            _sub_off(_STARTER, period=2, minute=60),
            _ev("Bad Behaviour", _STARTER, period=2, minute=80,
                bad_behaviour={"card": {"name": "Red Card"}}),
        ]
        _, (df, _) = _build(tmp_path, events)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Plan 004 H3a: H1/H2 review follow-ups and match_duration
# ---------------------------------------------------------------------------

def test_quality_issue_names_are_constants_not_literals():
    """Every "issue" value in the pipeline is a module constant, so a typo on
    the emitting side cannot silently disable a check in FAILING_QUALITY_ISSUES
    (H1 review follow-up 1)."""
    import ast
    import inspect
    from eleveniq.pipeline import build_player_match, quality_report

    literals = []
    for module in (build_player_match, quality_report):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "issue" and not (
                        isinstance(value, ast.Name)
                        or (isinstance(value, ast.IfExp)
                            and isinstance(value.body, ast.Name)
                            and isinstance(value.orelse, ast.Name))
                    ):
                        literals.append((module.__name__, ast.unparse(value)))
            # issues[("name", value)] in _aggregate_events
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Tuple):
                first = node.slice.elts[0] if node.slice.elts else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literals.append((module.__name__, ast.unparse(node)))
    assert literals == []


def test_failing_quality_issues_are_emitted_names():
    """Each failing issue name is the value of a module constant (not a stray
    string that nothing emits)."""
    from eleveniq.pipeline import build_player_match as bpm

    constants = {v for k, v in vars(bpm).items() if k.isupper() and isinstance(v, str)}
    assert bpm.FAILING_QUALITY_ISSUES <= constants
    assert bpm.STARTING_XI_ISSUES <= constants


class TestPeriodFiveAssists:
    def test_shootout_shot_assist_counts_toward_neither_key_passes_nor_xga(self, tmp_path):
        # A period-2 pass → shot pair is the control; the same pair in period 5
        # (shootout) must add nothing (H1 review follow-up 4).
        def pair(period: int, minute: int, goal: bool) -> list[dict]:
            shot_id = f"shot-{period}"
            pass_ev = _ev("Pass", period=period, minute=minute,
                          location=[60, 40],
                          **{"pass": {"end_location": [100, 40], "length": 40.0,
                                      "assisted_shot_id": shot_id, "shot_assist": not goal,
                                      "goal_assist": goal}})
            pass_ev["id"] = f"pass-{period}"
            shot = _shot("Goal" if goal else "Saved", pid=_BENCH, period=period, minute=minute)
            shot["id"] = shot_id
            return [pass_ev, shot]

        lineups = _quality_lineups(bench_positions=[_spell("10:00", None, "Substitution - On")])
        events = [*pair(2, 50, goal=False), _ev("Half End", None, period=4, minute=120),
                  *pair(5, 121, goal=True)]
        _, (df, _) = _build(tmp_path, events, lineups)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["key_passes"] == 1
        assert row["assists"] == 0
        assert row["xg_assisted"] == pytest.approx(0.1)


class TestPlayerMinutesGuard:
    """A player's minutes may not exceed the match duration (H2 review follow-up 2)."""

    def test_overlapping_own_stints_fail_build(self, tmp_path):
        # 2 × 90 = 180 minutes for one player, far under the 11 × 90 team ceiling.
        spells = [_spell("00:00", None, "Starting XI"), _spell("00:00", None, "Tactical Shift")]
        msg = _build_fails(tmp_path, [], _quality_lineups(starter_positions=spells))
        assert "player_minutes_exceed_duration" in msg
        assert "team_minutes_exceed_ceiling" not in msg

    def test_full_match_equals_duration_passes(self, tmp_path):
        _, (df, report) = _build(tmp_path, [])
        assert df.filter(pl.col("player_id") == _STARTER)["minutes_played"][0] == pytest.approx(90.0)
        assert not [d for d in report if d.get("issue") == PLAYER_MINUTES_EXCEED_DURATION]


class TestOnPitchCount:
    """More than 11 players of a team on the pitch at one instant is reported,
    not failed (H2 review follow-up 3)."""

    def test_overcount_masked_under_team_ceiling_is_reported_not_failed(self, tmp_path):
        # P0 leaves at 60:00 with no substitute, so the team total (965) is
        # under 11 × 90, yet a 12th player is on during 10:00-15:00.
        positions = [[_spell("00:00", "60:00", "Starting XI")]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("10:00", "15:00", "Substitution - On")]]
        df, report = _build_team(tmp_path, positions)
        assert df["minutes_played"].sum() == pytest.approx(965.0)
        entries = [d for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED]
        assert [(e["on_pitch"], e["allowed"], e["period"], e["minute"]) for e in entries] == [
            (12, 11, 1, 10.0)]

    def test_eleven_on_for_ten_man_team_is_reported(self, tmp_path):
        # P0 sent off at 30:00 (the cap ends his stint), but P11 is on from
        # 40:00 to 50:00 without replacing anyone: 11 on, 10 allowed.
        positions = [[_spell("00:00", None, "Starting XI")] for _ in range(11)]
        positions += [[_spell("40:00", "50:00", "Substitution - On")]]
        card = {**_ev("Bad Behaviour", _TEAM_BASE, minute=30,
                      bad_behaviour={"card": {"name": "Red Card"}})}
        _, report = _build_team(tmp_path, positions, [card])
        entries = [d for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED]
        assert [(e["on_pitch"], e["allowed"], e["period"], e["minute"]) for e in entries] == [
            (11, 10, 1, 40.0)]

    def test_issue_does_not_fail_build(self):
        from eleveniq.pipeline.build_player_match import (
            FAILING_QUALITY_ISSUES, ON_PITCH_COUNT_EXCEEDED,
        )
        assert ON_PITCH_COUNT_EXCEEDED not in FAILING_QUALITY_ISSUES


def _stint(frm: str, from_period: int, to: str | None, to_period: int | None) -> dict:
    return {"from": frm, "from_period": from_period, "to": to, "to_period": to_period}


class TestOnPitchExcess:
    """_on_pitch_excess: period-aware, distinct players, exits applied, and the
    allowed number is 11 minus the dismissals that leave the team a player short.
    Player tuples are (player_id, positions, dismissal, substitution off)."""

    END = (2, 90.0)

    @staticmethod
    def _full(n: int, first_id: int = 1) -> list:
        return [(first_id + i, [_stint("00:00", 1, None, None)], None, None) for i in range(n)]

    @staticmethod
    def _excess(players):
        from eleveniq.pipeline.build_player_match import _on_pitch_excess
        return _on_pitch_excess(players, TestOnPitchExcess.END)

    def test_eleven_full_match_players(self):
        assert self._excess(self._full(11)) is None

    def test_twelve_overlapping_players_detected(self):
        players = self._full(11) + [(99, [_stint("20:00", 1, "25:00", 1)], None, None)]
        assert self._excess(players) == (12, 11, (1, 20.0))

    def test_dismissed_player_stops_counting_at_card(self):
        # P1 sent off at (1, 30:00): the ceiling drops to 10 and P1 leaves.
        players = self._full(11)
        players[0] = (1, players[0][1], (1, 30.0), None)
        assert self._excess(players) is None

    def test_eleven_on_after_a_dismissal_is_reported(self):
        # A 10-man team with 11 on the pitch: the plain 11 ceiling misses it.
        players = self._full(11)
        players[0] = (1, players[0][1], (1, 30.0), None)
        players.append((99, [_stint("40:00", 1, None, None)], None, None))
        assert self._excess(players) == (11, 10, (1, 40.0))

    def test_dismissal_instant_is_not_an_excess(self):
        # At the card instant the ceiling drops and the player leaves together.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "30:00", 1)], (1, 30.0), None)
        assert self._excess(players) is None

    def test_card_after_own_substitution_does_not_lower_ceiling(self):
        # 3900519 pattern (Bordeaux): P1 substituted off at (1, 29:10), shown a
        # red card on the bench at (2, 94:09); P2 leaves for treatment at
        # (2, 92:58) and returns at (2, 94:31). Eleven on is allowed throughout.
        players = self._full(11, first_id=2)
        players[0] = (2, [_stint("00:00", 1, "92:58", 2), _stint("94:31", 2, None, None)],
                      None, None)
        sub, card = (1, 29 + 10 / 60), (2, 94 + 9 / 60)
        players[1] = (3, [_stint("00:00", 1, "29:10", 1)], card, sub)
        players.append((99, [_stint("29:10", 1, None, None)], None, None))
        assert self._excess(players) is None

    def test_reversed_stint_after_dismissal_is_not_an_excess(self):
        # 3879549 pattern (AC Milan): P1 sent off at (1, 41:48); P2 replaced by
        # P99 at (1, 45:51); P99's first stint is "45:00 p2 → 45:51 p1". In
        # period-aware time it is empty, so ten are on throughout.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "45:00", 2), _stint("45:00", 2, None, None)],
                      (1, 41.8), None)
        players[1] = (2, [_stint("00:00", 1, "45:51", 1)], None, (1, 45.85))
        players.append((99, [_stint("45:00", 2, "45:51", 1), _stint("45:51", 1, "46:06", 1),
                             _stint("46:06", 1, None, None)], None, None))
        assert self._excess(players) is None

    def test_first_half_stoppage_does_not_overlap_second_half(self):
        # P1 plays to 46:00 of period 1; P99 comes on at 45:00 of period 2.
        # Match-absolute minutes overlap (45-46); period-aware time does not.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "46:00", 1)], None, None)
        players.append((99, [_stint("45:00", 2, None, None)], None, None))
        assert self._excess(players) is None

    def test_player_with_overlapping_own_stints_counts_once(self):
        # 3879549 Bonaventura pattern: (1,0)→(2,45:00), reversed (2,45:00)→(1,46:06),
        # (1,46:06)→end. The first and last overlap in period-aware time.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "45:00", 2), _stint("45:00", 2, "46:06", 1),
                          _stint("46:06", 1, None, None)], None, None)
        assert self._excess(players) is None

    def test_substitution_instant_is_not_twelve(self):
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "60:00", 2)], None, (2, 60.0))
        players.append((99, [_stint("60:00", 2, None, None)], None, None))
        assert self._excess(players) is None


class TestOnPitchExcessEdges:
    """H3a review follow-up 1 (plan 004): when a card lowers the allowed number,
    a malformed stint end, and the period-aware exit order."""

    END = (2, 90.0)

    @staticmethod
    def _excess(players):
        from eleveniq.pipeline.build_player_match import _on_pitch_excess
        return _on_pitch_excess(players, TestOnPitchExcessEdges.END)

    @staticmethod
    def _full(n: int) -> list:
        return [(1 + i, [_stint("00:00", 1, None, None)], None, None) for i in range(n)]

    def test_dismissal_while_off_for_treatment_lowers_allowed(self):
        # P1 leaves for treatment at (1, 30:00), is sent off at (1, 32:00) while
        # off, and is never substituted. He cannot be replaced, so P99 coming
        # on at (1, 35:00) puts 11 on a 10-man team.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "30:00", 1)], (1, 32.0), None)
        players.append((99, [_stint("35:00", 1, None, None)], None, None))
        assert self._excess(players) == (11, 10, (1, 35.0))

    def test_substitution_and_card_at_same_instant_keep_allowed(self):
        # P1 is substituted off and booked a second time at (2, 60:00). The
        # Substitution event proves it came first (a dismissed player cannot be
        # substituted), so the team keeps 11 and P99's entry is not an excess.
        players = self._full(11)
        players[0] = (1, players[0][1], (2, 60.0), (2, 60.0))
        players.append((99, [_stint("60:00", 2, None, None)], None, None))
        assert self._excess(players) is None

    def test_card_before_taking_the_field_keeps_allowed(self):
        # P12 is sent off at (1, 20:00) before his only stint starts; he had
        # not taken the field, so the allowed number stays 11.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "50:00", 2)], None, (2, 50.0))
        players.append((12, [_stint("50:00", 2, None, None)], (1, 20.0), None))
        assert self._excess(players) is None

    def test_stint_with_to_but_null_to_period_raises(self):
        players = self._full(10) + [(11, [_stint("00:00", 1, "60:00", None)], None, None)]
        with pytest.raises(ValueError, match="to_period"):
            self._excess(players)

    def test_stint_end_without_substitution_event_then_card_lowers_allowed(self):
        # Pinned assumption (ADR 002 §3): the Substitution event is complete.
        # P1's stint ends at (2, 60:00) with no Substitution event and no
        # "Player Off"; P99 is on from (2, 60:00). P1 is then shown a red card at
        # (2, 70:00). With no Substitution event the rule cannot tell this from
        # "off for treatment", so allowed drops to 10 and 11 on is reported.
        # If a source drops Substitution events, this is a false positive.
        players = self._full(11)
        players[0] = (1, [{**_stint("00:00", 1, "60:00", 2), "end_reason": "Tactical Shift"}],
                      (2, 70.0), None)
        players.append((99, [_stint("60:00", 2, None, None)], None, None))
        assert self._excess(players) == (11, 10, (2, 70.0))

    def test_exit_order_is_period_aware_unlike_stint_end_cap(self):
        # Pinned divergence (ADR 002 §3): P1 is substituted off in first-half
        # stoppage, (1, 46:30), then shown a red card on the bench at (2, 45:30).
        # The on-pitch count takes the substitution as the exit, so the card
        # keeps 11 allowed; stint_end_cap takes the smaller match-absolute
        # minute, 45.5. 0 cases in the 2015/16 data.
        from eleveniq.metrics.minutes import stint_end_cap
        players = self._full(11)
        players[0] = (1, players[0][1], (2, 45.5), (1, 46.5))
        players.append((99, [_stint("46:30", 1, None, None)], None, None))
        assert self._excess(players) is None
        assert stint_end_cap(dismissal_minute=45.5, substitution_minute=46.5) == 45.5


@pytest.mark.integration
@pytest.mark.parametrize("match_id", [3879549, 3900519])
def test_real_dismissal_matches_have_no_on_pitch_excess(match_id):
    """ADR 002 §3: AC Milan 3879549 (reversed stint after a red card) and
    Bordeaux 3900519 (red card on the bench after a substitution) stay within
    11 minus dismissals."""
    if not (_RAW_ROOT / "events" / f"{match_id}.json").exists():
        pytest.skip("StatsBomb raw data not available")
    comp_id = {3879549: 12, 3900519: 7}[match_id]
    _, report = _process_match(match_id, comp_id, 27, data_root=_RAW_ROOT)
    assert not [d for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED]


class TestSubstitutionOffEdges:
    def test_stint_without_from_period_raises(self):
        # _substitution_off requires from_period on every stint (never defaulted).
        from eleveniq.pipeline.build_player_match import _substitution_off
        events = [_sub_off(_STARTER, period=2, minute=60)]
        with pytest.raises(KeyError, match="from_period"):
            _substitution_off(1, _STARTER, [{"from": "00:00", "to": None}], events, [])

    def test_fractional_second_lineup_time_is_the_substitution_instant(self, tmp_path):
        # H2 review follow-up 5: "37:02.400" against a substitution at 37:02 is
        # that instant (allowed, 0 minutes), not a stint after the substitution.
        spells = [
            {**_spell("00:00", "31:29", "Starting XI"), "end_reason": "Player Off"},
            {**_spell("37:02.400", None, "Player On"), "end_reason": "Final Whistle"},
        ]
        _, (df, report) = _build(
            tmp_path, [_sub_off(_STARTER, period=1, minute=37, second=2)],
            _quality_lineups(starter_positions=spells))
        assert not [d for d in report if d.get("issue") == STINT_AFTER_SUBSTITUTION]
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(31 + 29 / 60)


class TestMatchDurationColumn:
    def test_every_row_carries_its_match_duration(self, tmp_path):
        events = [_ev("Half End", None, period=2, minute=93, second=30)]
        _, (df, _) = _build(tmp_path, events)
        assert df.schema["match_duration"] == pl.Float64
        assert df["match_duration"].to_list() == [pytest.approx(93.5)] * df.height


# ---------------------------------------------------------------------------
# QA (plan 004 H3a): on-pitch count, worst-instant choice and pipeline wiring
# ---------------------------------------------------------------------------

class TestOnPitchExcessWorstInstant:
    """Docstring: the instant returned is the first one where the team has the
    MOST players above the allowed number."""

    @staticmethod
    def _excess(players):
        from eleveniq.pipeline.build_player_match import _on_pitch_excess
        return _on_pitch_excess(players, (2, 90.0))

    @staticmethod
    def _full(n: int) -> list:
        return [(1 + i, [_stint("00:00", 1, None, None)], None, None) for i in range(n)]

    def test_larger_later_excess_wins_over_smaller_earlier_one(self):
        # +1 at (1, 10:00)-(1, 15:00); +2 at (2, 60:00)-(2, 65:00).
        players = self._full(11) + [
            (90, [_stint("10:00", 1, "15:00", 1)], None, None),
            (91, [_stint("60:00", 2, "65:00", 2)], None, None),
            (92, [_stint("60:00", 2, "65:00", 2)], None, None),
        ]
        assert self._excess(players) == (13, 11, (2, 60.0))

    def test_equal_excesses_report_the_first_instant(self):
        players = self._full(11) + [
            (90, [_stint("10:00", 1, "15:00", 1)], None, None),
            (91, [_stint("60:00", 2, "65:00", 2)], None, None),
        ]
        assert self._excess(players) == (12, 11, (1, 10.0))

    def test_excess_below_reduced_ceiling_ranks_by_margin_not_count(self):
        # 12 on vs 11 allowed (margin 1) early; 11 on vs 9 allowed (margin 2)
        # after two dismissals. The larger margin is "most above allowed".
        players = self._full(11)
        players[0] = (1, players[0][1], (2, 50.0), None)
        players[1] = (2, players[1][1], (2, 51.0), None)
        players += [
            (90, [_stint("10:00", 1, "15:00", 1)], None, None),
            (91, [_stint("70:00", 2, None, None)], None, None),
            (92, [_stint("70:00", 2, None, None)], None, None),
        ]
        assert self._excess(players) == (11, 9, (2, 70.0))


class TestOnPitchCountPipelineWiring:
    """_process_match must feed _on_pitch_excess the right exits and dismissals."""

    @staticmethod
    def _on_pitch(report):
        return [(e["on_pitch"], e["allowed"]) for e in report
                if e.get("issue") == ON_PITCH_COUNT_EXCEEDED]

    def test_second_yellow_from_foul_committed_lowers_allowed(self, tmp_path):
        # Second yellows also come as Foul Committed cards; a 10-man team that
        # then has 11 on must still be reported.
        positions = [[_spell("00:00", None, "Starting XI")] for _ in range(11)]
        positions += [[_spell("40:00", "50:00", "Substitution - On")]]
        card = _ev("Foul Committed", _TEAM_BASE, minute=30,
                   foul_committed={"card": {"name": "Second Yellow"}})
        _, report = _build_team(tmp_path, positions, [card])
        assert self._on_pitch(report) == [(11, 10)]

    def test_red_card_to_unused_substitute_keeps_eleven_allowed(self, tmp_path):
        # A bench player who never came on is shown a red: allowed stays 11, so
        # eleven players on the pitch all match is not an excess.
        positions = [[_spell("00:00", None, "Starting XI")] for _ in range(11)] + [[]]
        card = _ev("Bad Behaviour", _TEAM_BASE + 11, period=2, minute=70,
                   bad_behaviour={"card": {"name": "Red Card"}})
        df, report = _build_team(tmp_path, positions, [card])
        assert self._on_pitch(report) == []
        assert df["minutes_played"].sum() == pytest.approx(11 * 90.0)

    def test_explicit_to_past_own_substitution_is_capped_in_count(self, tmp_path):
        # Lineup says P0 played to 70:00 but his Substitution event is at 60:00
        # (the ADR 002 §3 cap); his replacement comes on at 60:00. With the exit
        # applied there are never 12 on the pitch.
        positions = [[_spell("00:00", "70:00", "Starting XI")]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("60:00", None, "Substitution - On")]]
        _, report = _build_team(tmp_path, positions,
                                [_sub_off(_TEAM_BASE, period=1, minute=60)])
        assert self._on_pitch(report) == []


# ---------------------------------------------------------------------------
# QA (plan 004 H3b): when a card lowers the allowed number (ADR 002 §3,
# _card_lowers_allowed docstring)
# ---------------------------------------------------------------------------

class TestCardLowersAllowedSpec:
    """Spec: a card lowers the allowed number when the player has taken the
    field by the card (a NON-EMPTY stint starting AT OR BEFORE it, in
    (period, minute) order) and was not substituted off BEFORE OR AT it."""

    @staticmethod
    def _excess(players):
        from eleveniq.pipeline.build_player_match import _on_pitch_excess
        return _on_pitch_excess(players, (2, 90.0))

    @staticmethod
    def _full(n: int) -> list:
        return [(1 + i, [_stint("00:00", 1, None, None)], None, None) for i in range(n)]

    def test_substitution_after_card_does_not_cancel_dismissal(self):
        # Data-quality pattern: a Substitution recorded AFTER the red card. Only
        # a substitution before or at the card cancels it; P99 coming on at
        # (2, 70:00) puts 11 on a 10-man team.
        players = self._full(11)
        players[0] = (1, players[0][1], (2, 60.0), (2, 70.0))
        players.append((99, [_stint("70:00", 2, None, None)], None, None))
        assert self._excess(players) == (11, 10, (2, 70.0))

    def test_card_at_entry_instant_lowers_allowed(self):
        # P12 replaces P1 at (2, 60:00) and is sent off at that same instant: his
        # stint starts AT the card, so he has taken the field and the team is 10.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "60:00", 2)], None, (2, 60.0))
        players.append((12, [_stint("60:00", 2, None, None)], (2, 60.0), None))
        players.append((13, [_stint("70:00", 2, None, None)], None, None))
        assert self._excess(players) == (11, 10, (2, 70.0))

    def test_zero_length_stint_before_card_is_not_taking_the_field(self):
        # P12's only stint is empty (from == to), so he never took the field and
        # his card at (1, 20:00) must keep 11 allowed.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "60:00", 2)], None, (2, 60.0))
        players.append((12, [_stint("10:00", 1, "10:00", 1)], (1, 20.0), None))
        players.append((13, [_stint("60:00", 2, None, None)], None, None))
        assert self._excess(players) is None

    def test_taken_the_field_is_period_aware(self):
        # Card at (1, 46:30) in first-half stoppage; P12's stint starts at
        # (2, 45:00), which is AFTER the card in (period, minute) order although
        # 45.0 < 46.5 match-absolute. He had not taken the field: 11 allowed.
        players = self._full(11)
        players[0] = (1, [_stint("00:00", 1, "45:00", 2)], None, (2, 45.0))
        players.append((12, [_stint("45:00", 2, None, None)], (1, 46.5), None))
        players.append((13, [_stint("50:00", 2, None, None)], None, None))
        assert self._excess(players) is None


class TestOnPitchCardRulePipeline:
    """_process_match wiring for the H3b card rule: dismissal and substitution
    must reach _on_pitch_excess separately, not merged into one exit."""

    @staticmethod
    def _on_pitch(report):
        return [(e["on_pitch"], e["allowed"]) for e in report
                if e.get("issue") == ON_PITCH_COUNT_EXCEEDED]

    def test_substitution_and_second_yellow_same_second_is_not_an_excess(self, tmp_path):
        # P0 is replaced at 60:00 and booked a second time at 60:00; P11 comes on.
        # The substitution came first, so the team keeps 11 and nothing is reported.
        positions = [[_spell("00:00", "60:00", "Starting XI")]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("60:00", None, "Substitution - On")]]
        card = _ev("Foul Committed", _TEAM_BASE, minute=60,
                   foul_committed={"card": {"name": "Second Yellow"}})
        df, report = _build_team(tmp_path, positions,
                                 [_sub_off(_TEAM_BASE, period=1, minute=60), card])
        assert self._on_pitch(report) == []
        p0 = df.filter(pl.col("player_id") == _TEAM_BASE)["minutes_played"][0]
        assert p0 == pytest.approx(60.0)

    def test_red_card_while_off_for_treatment_lowers_allowed(self, tmp_path):
        # P0 goes off for treatment at 30:00, is sent off at 32:00 without
        # returning, and P11 comes on at 35:00: 11 on a 10-man team.
        positions = [[{**_spell("00:00", "30:00", "Starting XI"), "end_reason": "Player Off"}]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("35:00", None, "Substitution - On")]]
        card = _ev("Bad Behaviour", _TEAM_BASE, minute=32,
                   bad_behaviour={"card": {"name": "Red Card"}})
        df, report = _build_team(tmp_path, positions, [card])
        assert self._on_pitch(report) == [(11, 10)]
        p0 = df.filter(pl.col("player_id") == _TEAM_BASE)["minutes_played"][0]
        assert p0 == pytest.approx(30.0)
