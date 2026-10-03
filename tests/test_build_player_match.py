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
import eleveniq.pipeline.build_player_match as _bpm
from eleveniq.metrics.minutes import PeriodClock, player_timeline

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "statsbomb"


@pytest.fixture
def synthetic_small_teams(monkeypatch):
    """
    Relax the ADR 004 D12 players-per-pass minimum for the minimal synthetic
    fixtures (teams of 1-4 players, below the Laws minimum of 7 that the build
    enforces). Applied explicitly, test by test, never autouse: every other test,
    including TestPassCreditBounds, runs with the real MIN_PLAYERS_PER_PASS = 7.
    Patched where it is read (build_player_match), not only in config.
    """
    monkeypatch.setattr(_bpm, "MIN_PLAYERS_PER_PASS", 1)

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
    @pytest.mark.usefixtures("synthetic_small_teams")
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

@pytest.mark.usefixtures("synthetic_small_teams")
def test_build_competition_season_writes_parquet(tmp_path):
    out = tmp_path / "out.parquet"
    df, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert out.exists()
    reloaded = pl.read_parquet(out)
    assert len(reloaded) == len(df)


@pytest.mark.usefixtures("synthetic_small_teams")
def test_build_competition_season_row_count(tmp_path):
    out = tmp_path / "out.parquet"
    df, _ = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert len(df) == 6  # Frank (unused sub) excluded; Grace (fallback starter) included


@pytest.mark.usefixtures("synthetic_small_teams")
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


@pytest.mark.usefixtures("synthetic_small_teams")
def test_build_competition_season_returns_dismissal_report(tmp_path):
    out = tmp_path / "out.parquet"
    _, dismissals = build_competition_season(
        99, 1, data_root=_FIXTURE_ROOT, output_path=out
    )
    assert any(d.get("player_id") == 1003 for d in dismissals)


@pytest.mark.usefixtures("synthetic_small_teams")
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


@pytest.mark.usefixtures("synthetic_small_teams")
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


@pytest.mark.usefixtures("synthetic_small_teams")
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

@pytest.mark.usefixtures("synthetic_small_teams")
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

_NOMINAL_PERIOD_END = {1: 45, 2: 90, 3: 105, 4: 120}


# The two teams' Half End events (ADR 002 §31.1: exactly two per period, one per team).
_HALF_END_TEAMS = (801, 802)


def _with_half_ends(events: list[dict]) -> list[dict]:
    """
    ADR 002 §31.1 needs exactly two Half End events, one per team, in every
    playing period 1..max. The synthetic matches below were written with at
    most one per period: add the pair at the nominal end of each period that
    has none, so a period-1 whistle at 45:00 leaves their expected minutes
    unchanged, and complete a period's single Half End with the other team's
    at the same instant. A period given two Half End events is left as written
    (the tests of the rule). Events with no period are left for the tests that
    expect them to fail.
    """
    def is_half_end(e: dict) -> bool:
        return (e.get("type") or {}).get("name") == "Half End"

    periods = [e["period"] for e in events if isinstance(e.get("period"), int) and e["period"] <= 4]
    by_period: dict[int, list[int]] = {}
    for i, e in enumerate(events):
        if is_half_end(e):
            by_period.setdefault(e.get("period"), []).append(i)
    out = list(events)
    for p, (i, *rest) in by_period.items():
        if rest:
            continue
        own = (out[i].get("team") or {}).get("id") or _HALF_END_TEAMS[0]
        other = next(t for t in _HALF_END_TEAMS if t != own)
        out[i] = {**out[i], "team": {"id": own}}
        out.append({**out[i], "id": f"{out[i].get('id', 'he')}-other", "team": {"id": other}})
    out += [{"id": f"he-auto-{p}-{t}", "type": {"name": "Half End"}, "team": {"id": t},
             "period": p, "minute": _NOMINAL_PERIOD_END[p], "second": 0}
            for p in range(1, max(periods, default=0) + 1) if p not in by_period
            for t in _HALF_END_TEAMS]
    return out


def _write_match(root: Path, match_id: int, events: list[dict], lineups: list[dict]) -> None:
    import json
    events = _with_half_ends(events)
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
    """A stint in match-absolute shorthand: a time before 45:00 is period 1, a
    later one period 2 (a `to` of exactly 45:00 ends period 1). A first-half
    stoppage stint sets its period explicitly."""
    def minute(t: str) -> float:
        mm, ss = t.split(".")[0].split(":")
        return int(mm) + int(ss) / 60
    return {"position_id": 10, "position": "Center Defensive Midfield",
            "from": frm, "to": to, "from_period": 1 if minute(frm) < 45 else 2,
            "to_period": None if to is None else (1 if minute(to) <= 45 else 2),
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
            {"issue": "period_end_before_nominal", "match_id": 12, "period": 1},
            {"issue": "stint_after_period_end", "match_id": 12, "player_id": 20},
            {"issue": "treatment_window_inside_stint", "match_id": 12, "player_id": 21},
            {"issue": "overlapping_stints", "match_id": 12, "player_id": 22},
            {"issue": "overlapping_stints", "match_id": 12, "player_id": 23},
            {"issue": "unpaired_player_off_capped", "match_id": 12, "player_id": 24},
            {"issue": "dismissal_after_period_end", "match_id": 12, "player_id": 25},
            {"issue": "dismissal_after_match_end", "match_id": 12, "player_id": 26},
            {"issue": "events_outside_credited_time", "match_id": 12, "player_id": 27},
            {"issue": "events_outside_credited_time", "match_id": 12, "player_id": 27},
            {"issue": "events_outside_credited_time", "match_id": 12, "player_id": 27},
            # ADR 004 D16: per-match counts, summed by their "count" field.
            {"issue": "xga_no_receipt", "match_id": 12, "count": 2},
            {"issue": "xga_no_receipt", "match_id": 13, "count": 1},
            {"issue": "xga_dangling_shot_id", "match_id": 13, "count": 1},
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
            "period_end_before_nominal": 1,
            "stint_after_period_end": 1,
            "treatment_window_inside_stint": 1,
            "overlapping_stints": 2,
            "unpaired_player_off_capped": 1,
            "dismissal_after_period_end": 1,
            "dismissal_after_match_end": 1,
            "events_outside_credited_time": 3,
            "xga_no_receipt": 3,
            "xga_missing_shooter": 0,
            "xga_dangling_shot_id": 1,
            "xga_missing_assister": 0,
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


def _default_half_end(events) -> list[dict]:
    """The (2, 90:00) whistle, unless the test gives its own period-2 Half End."""
    own = any((e.get("type") or {}).get("name") == "Half End" and e.get("period") == 2
              for e in events)
    return [] if own else [_HALF_END]


def _build(tmp_path, events, lineups=None):
    """Write one synthetic match and build it; return (out_path, result-or-exc)."""
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events,
                                  *_default_half_end(events)],
                 lineups or _quality_lineups())
    out = tmp_path / "out.parquet"
    return out, build_competition_season(99, 1, data_root=tmp_path, output_path=out)


def _build_fails(tmp_path, events, lineups=None) -> str:
    out = tmp_path / "out.parquet"
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events,
                                  *_default_half_end(events)],
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

    @pytest.mark.usefixtures("synthetic_small_teams")
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
        # The sub comes on at (4, 110:00). _spell's shorthand would put 110:00 in
        # period 2, 20 min after that whistle, which ADR 004 D22 now fails
        # (stint_late_start_exceeds_limit), so the period is set explicitly.
        lineups = _quality_lineups(
            bench_positions=[{**_spell("110:00", None, "Substitution - On (Tactical)"),
                              "from_period": 4}])
        _, (df, _) = _build(tmp_path, events, lineups)
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["minutes_played"] == pytest.approx(100.0)

    def test_dismissal_after_match_end_is_capped_and_reported(self, tmp_path):
        # ADR 002 §31.2 (report-only from H4): a red card after the final
        # whistle (2, 90:00) is a real event; it is clamped to match_end and
        # changes no minutes.
        card = _ev("Bad Behaviour", period=2, minute=92,
                   bad_behaviour={"card": {"name": "Red Card"}})
        _, (df, report) = _build(tmp_path, [card])
        assert df.filter(pl.col("player_id") == _STARTER)["minutes_played"][0] == pytest.approx(90.0)
        assert [d["issue"] for d in report if d.get("issue", "").startswith("dismissal_after")] == [
            "dismissal_after_match_end"]

    def test_dismissal_in_the_interval_caps_at_the_period_end(self, tmp_path):
        # ADR 002 §31.2: a card at (1, 46:00), after the (1, 45:00) whistle, is
        # clamped to the period end: 45 minutes, reported.
        card = _ev("Bad Behaviour", period=1, minute=46,
                   bad_behaviour={"card": {"name": "Red Card"}})
        _, (df, report) = _build(tmp_path, [card])
        assert df.filter(pl.col("player_id") == _STARTER)["minutes_played"][0] == pytest.approx(45.0)
        assert [d["issue"] for d in report if d.get("issue", "").startswith("dismissal_after")] == [
            "dismissal_after_period_end"]

    def test_shootout_event_by_bench_player_is_not_dropped_event(self, tmp_path):
        # Filtered as a shootout event, so not reported as a dropped event.
        _, (_, report) = _build(tmp_path, [_shot("Goal", pid=_BENCH, period=5, minute=121)])
        assert not [d for d in report if str(d.get("issue", "")).startswith("dropped_event")]


@pytest.mark.usefixtures("synthetic_small_teams")
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
    _write_match(tmp_path, 8100, [_xi_event(_TEAM, starters), *events,
                                  *_default_half_end(events)], lineups)
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
            {**_spell("46:01", None, "Player On"), "from_period": 1},
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
        # The first half ends at 47:00 (the to = (1, 45:51) is inside it).
        spells = [{**_spell("00:00", "45:00", "Starting XI")},
                  {**_spell("45:00", "45:51", "Tactical Shift"), "from_period": 2,
                   "to_period": 1}]
        events = [_ev("Half End", None, period=1, minute=47)]
        _, (_, report) = _build(tmp_path, events, _quality_lineups(starter_positions=spells))
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
        # 90:00, so (2, 92:30) precedes a substitution at (3, 91:00). His Player On
        # is after the (2, 90:00) whistle, so the stint is clamped to that end
        # (ADR 002 §31.1, stint_after_period_end) and runs from the period-3
        # restart to his substitution: 88 + 1 min. The return is 1:30 after the
        # whistle (was 2:30): ADR 004 D22 fails a start more than 2 min late.
        spells = [{**_spell("00:00", "88:00", "Starting XI"), "to_period": 2},
                  {**_spell("91:30", None, "Player On"), "from_period": 2}]
        events = [_sub_off(_STARTER, period=3, minute=91),
                  _ev("Half End", None, period=4, minute=120)]
        _, (df, report) = _build(tmp_path, events, _quality_lineups(starter_positions=spells))
        assert not [d for d in report if d.get("issue") == STINT_AFTER_SUBSTITUTION]
        assert [d["issue"] for d in report if d.get("issue") == "stint_after_period_end"] == [
            "stint_after_period_end"]
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(89.0)

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
    @pytest.mark.usefixtures("synthetic_small_teams")
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

    def test_overlapping_own_stints_are_counted_once_and_reported(self, tmp_path):
        # ADR 002 §31.4: minutes are the union of the stints, so two full-match
        # stints give 90, not 180; the deduplicated time is reported.
        spells = [_spell("00:00", None, "Starting XI"), _spell("00:00", None, "Tactical Shift")]
        _, (df, report) = _build(tmp_path, [], _quality_lineups(starter_positions=spells))
        assert df.filter(pl.col("player_id") == _STARTER)["minutes_played"][0] == pytest.approx(90.0)
        overlaps = [d for d in report if d.get("issue") == "overlapping_stints"]
        assert [d["minutes_deduplicated"] for d in overlaps] == [pytest.approx(90.0)]
        assert not [d for d in report if d.get("issue") == PLAYER_MINUTES_EXCEED_DURATION]

    def test_full_match_equals_duration_passes(self, tmp_path):
        _, (df, report) = _build(tmp_path, [])
        assert df.filter(pl.col("player_id") == _STARTER)["minutes_played"][0] == pytest.approx(90.0)
        assert not [d for d in report if d.get("issue") == PLAYER_MINUTES_EXCEED_DURATION]


class TestOnPitchCount:
    """More than 11 players of a team on the pitch at one instant is reported,
    not failed (H2 review follow-up 3)."""

    def test_overcount_masked_under_team_ceiling_is_reported_not_failed(self, tmp_path):
        # P0 leaves at (2, 60:00) with no substitute, so period 2's total
        # (15 + 450 + 5 = 470) is under 11 × 45, yet a 12th player is on during
        # (2, 50:00)-(2, 55:00). The per-period ceiling (ADR 002 §31.5) cannot see
        # it; the on-pitch count reports it.
        positions = [[_spell("00:00", "60:00", "Starting XI")]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("50:00", "55:00", "Substitution - On")]]
        df, report = _build_team(tmp_path, positions)
        assert df["minutes_played"].sum() == pytest.approx(965.0)
        entries = [d for d in report if d.get("issue") == ON_PITCH_COUNT_EXCEEDED]
        assert [(e["on_pitch"], e["allowed"], e["period"], e["minute"]) for e in entries] == [
            (12, 11, 2, 50.0)]

    def test_twelve_in_one_period_fail_the_per_period_ceiling(self, tmp_path):
        # ADR 002 §31.5: the same 12th player in period 1 makes period 1's total
        # 11 × 45 + 5 = 500 > 495, so the build fails, although the match total
        # (965) is under 11 × 90.
        positions = [[_spell("00:00", "60:00", "Starting XI")]]
        positions += [[_spell("00:00", None, "Starting XI")] for _ in range(10)]
        positions += [[_spell("10:00", "15:00", "Substitution - On")]]
        assert "team_minutes_exceed_ceiling" in _build_team_fails(tmp_path, positions)

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


def _pad(players: list) -> list:
    """The on-pitch tuples below predate the unpaired Player Off exit (ADR 002
    §31.2): a 4-tuple (player_id, positions, dismissal, substitution) has none."""
    return [(*p, None) if len(p) == 4 else p for p in players]


class TestOnPitchExcess:
    """_on_pitch_excess: period-aware, distinct players, exits applied, and the
    allowed number is 11 minus the dismissals that leave the team a player short.
    Player tuples are (player_id, positions, dismissal, substitution off[,
    unpaired Player Off])."""

    END = (2, 90.0)

    @staticmethod
    def _full(n: int, first_id: int = 1) -> list:
        return [(first_id + i, [_stint("00:00", 1, None, None)], None, None) for i in range(n)]

    @staticmethod
    def _excess(players):
        from eleveniq.pipeline.build_player_match import _on_pitch_excess
        return _on_pitch_excess(_pad(players), TestOnPitchExcess.END)

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
        return _on_pitch_excess(_pad(players), TestOnPitchExcessEdges.END)

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

    def test_exit_order_is_period_aware_like_stint_end_cap(self):
        # ADR 002 §31.2 (the §3 divergence is closed): P1 is substituted off in
        # first-half stoppage, (1, 46:30), then shown a red card on the bench at
        # (2, 45:30). Both the on-pitch count and stint_end_cap take the
        # substitution as the exit, so the card keeps 11 allowed.
        from eleveniq.metrics.minutes import stint_end_cap
        players = self._full(11)
        players[0] = (1, players[0][1], (2, 45.5), (1, 46.5))
        players.append((99, [_stint("46:30", 1, None, None)], None, None))
        assert self._excess(players) is None
        assert stint_end_cap(dismissal=(2, 45.5), substitution=(1, 46.5),
                             unpaired_off=None) == (1, 46.5)

    def test_unpaired_player_off_is_an_exit(self):
        # ADR 002 §31.2-31.3: P1's lineup stint runs to the whistle, but he left
        # at an unpaired Player Off (2, 60:00). P99 on at (2, 62:00) is not a
        # twelfth player; without the Off as an exit he would be.
        players = self._full(11)
        players[0] = (1, players[0][1], None, None, (2, 60.0))
        players.append((99, [_stint("62:00", 2, None, None)], None, None, None))
        assert self._excess(players) is None
        players[0] = (1, players[0][1], None, None, None)
        assert self._excess(players) == (12, 11, (2, 62.0))


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
        return _on_pitch_excess(_pad(players), (2, 90.0))

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
                                [_sub_off(_TEAM_BASE, period=2, minute=60)])
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
        return _on_pitch_excess(_pad(players), (2, 90.0))

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
        card = _ev("Foul Committed", _TEAM_BASE, period=2, minute=60,
                   foul_committed={"card": {"name": "Second Yellow"}})
        df, report = _build_team(tmp_path, positions,
                                 [_sub_off(_TEAM_BASE, period=2, minute=60), card])
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


# ---------------------------------------------------------------------------
# Plan 004 H4b: period-aware minutes in the pipeline (ADR 002 §31)
# ---------------------------------------------------------------------------

def _half_end(period: int, minute: int, second: int = 0) -> dict:
    return {**_ev("Half End", None, period=period, minute=minute), "second": second}


class TestPeriodAwarePipeline:
    """_process_match wiring of ADR 002 §31: the Half End clock, exits, windows,
    the clamp guards and the report-only issues. One starter, 11 not needed:
    only per-player minutes and issues are checked."""

    @staticmethod
    def _starter(df):
        return df.filter(pl.col("player_id") == _STARTER).row(0, named=True)

    @staticmethod
    def _issues(report, name):
        return [d for d in report if d.get("issue") == name]

    @pytest.mark.usefixtures("synthetic_small_teams")
    def test_first_half_stoppage_is_credited_and_post_whistle_events_extend_nothing(self, tmp_path):
        # Half End (1, 47:00) and (2, 93:00); a Ball Receipt after the first-half
        # whistle stays a counted event but adds no time: 47 + 48 = 95.
        events = [_half_end(1, 47), _ev("Pass", period=1, minute=48,
                                        location=[50, 40], **{"pass": {"end_location": [60, 40]}}),
                  _half_end(2, 93)]
        _, (df, report) = _build(tmp_path, events)
        row = self._starter(df)
        assert row["minutes_played"] == pytest.approx(95.0)
        assert row["match_duration"] == pytest.approx(95.0)
        assert row["passes_attempted"] == 1

    def test_missing_half_end_fails_the_match(self, tmp_path):
        # A period-1 event but no period-1 Half End and the helper is bypassed.
        import json
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), _HALF_END], _quality_lineups())
        path = tmp_path / "events" / "8100.json"
        events = [e for e in json.loads(path.read_text())
                  if not (e["type"]["name"] == "Half End" and e["period"] == 1)]
        path.write_text(json.dumps(events))
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "o.parquet")

    def test_disagreeing_half_ends_fail_the_match(self, tmp_path, caplog):
        # One per team, 2 s apart: only the disagreement can fail it.
        events = [_half_end(1, 45), {**_half_end(1, 45, second=2), "team": {"id": 802}},
                  _HALF_END]
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events], _quality_lineups())
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "o.parquet")
        assert "disagree" in caplog.text

    def test_half_ends_not_one_per_team_fail_the_match(self, tmp_path, caplog):
        # §31.1: two period-1 Half End events, both from the same team.
        events = [_half_end(1, 45), _half_end(1, 45), _HALF_END]
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events], _quality_lineups())
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "o.parquet")
        assert "not one per team" in caplog.text

    def test_slightly_short_period_is_kept_and_reported(self, tmp_path):
        _, (df, report) = _build(tmp_path, [_half_end(1, 44)])
        assert self._starter(df)["minutes_played"] == pytest.approx(44 + 45)
        assert [(d["period"], d["shortfall"]) for d in
                self._issues(report, "period_end_before_nominal")] == [(1, pytest.approx(1.0))]

    def test_far_short_period_fails_the_build(self, tmp_path):
        msg = _build_fails(tmp_path, [_half_end(1, 39)])  # 6 min short of 45:00
        assert "period_too_short" in msg

    @pytest.mark.parametrize("stint, issue", [
        (_spell("00:00", "44:00", "Starting XI") | {"to": "46:00"}, "stint_end_after_period_end"),
        (_spell("00:00", None, "Starting XI") | {"from": "44:00", "from_period": 2},
         "stint_time_before_period_start"),
    ], ids=["to_after_half_end", "from_before_period_start"])
    def test_clamp_guards_fail_the_build(self, tmp_path, stint, issue):
        msg = _build_fails(tmp_path, [], _quality_lineups(starter_positions=[stint]))
        assert issue in msg

    def test_treatment_window_inside_a_stint_is_subtracted(self, tmp_path):
        # Shaqiri 3754339 pattern: Off (1, 20:00), On (1, 22:00), lineup unbroken.
        events = [_ev("Player Off", period=1, minute=20), _ev("Player On", period=1, minute=22)]
        _, (df, report) = _build(tmp_path, events)
        assert self._starter(df)["minutes_played"] == pytest.approx(88.0)
        assert [d["windows"] for d in self._issues(report, "treatment_window_inside_stint")] == [1]

    def test_unpaired_player_off_caps_and_is_reported(self, tmp_path):
        # Boufal 3901155 pattern: Player Off with no Player On, lineup runs on.
        _, (df, report) = _build(tmp_path, [_ev("Player Off", period=2, minute=70)])
        assert self._starter(df)["minutes_played"] == pytest.approx(70.0)
        assert [d["minutes_removed"] for d in self._issues(report, "unpaired_player_off_capped")] == [
            pytest.approx(20.0)]

    @pytest.mark.parametrize("kind, fails", [("Pass", True), ("Foul Committed", True),
                                             ("Bad Behaviour", False)])
    def test_event_after_unpaired_player_off(self, tmp_path, kind, fails, request):
        # ADR 002 §31.2: only a card (Bad Behaviour) or his own Substitution may
        # follow; a Foul Committed needs him on the pitch, even with a card.
        if (kind, fails) == ("Pass", True):
            # Only this case has an open-play pass on the 2-player synthetic team.
            request.getfixturevalue("synthetic_small_teams")
        later = _ev(kind, period=2, minute=80)
        if kind == "Bad Behaviour":
            later["bad_behaviour"] = {"card": {"name": "Yellow Card"}}
        if kind == "Pass":
            later |= {"location": [50, 40], "pass": {"end_location": [60, 40]}}
        events = [{**_ev("Player Off", period=2, minute=70), "index": 70}, {**later, "index": 80}]
        if fails:
            assert "event_after_permanent_off" in _build_fails(tmp_path, events)
        else:
            _, (df, _) = _build(tmp_path, events)
            assert self._starter(df)["minutes_played"] == pytest.approx(70.0)

    def test_reversed_treatment_window_fails_the_match(self, tmp_path, caplog):
        # Player Off then Player On in event order, but the On's timestamp is
        # earlier: a reversed window is a data error, not an overlap (§31.3).
        events = [{**_ev("Player Off", period=1, minute=22), "index": 22},
                  {**_ev("Player On", period=1, minute=20), "index": 23}]
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events, _HALF_END],
                     _quality_lineups())
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "o.parquet")
        assert "reversed in elapsed time" in caplog.text

    def test_two_player_offs_in_a_row_fail_the_match(self, tmp_path):
        events = [_ev("Player Off", period=1, minute=20), _ev("Player Off", period=1, minute=21),
                  _ev("Player On", period=1, minute=22)]
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER]), *events, _HALF_END],
                     _quality_lineups())
        with pytest.raises(MatchProcessingError):
            build_competition_season(99, 1, data_root=tmp_path, output_path=tmp_path / "o.parquet")

    @pytest.mark.usefixtures("synthetic_small_teams")
    def test_events_outside_credited_time_are_reported(self, tmp_path):
        # Milinković-Savić 3879637 pattern: Player Off (1, 30:00), Player On
        # (1, 32:00), but the lineup resumes only at (2, 45:00). His pass at
        # (1, 40:00) is outside his time; the Off/On events themselves are exempt.
        spells = [_spell("00:00", "30:00", "Starting XI"), _spell("45:00", None, "Player On")]
        events = [_ev("Player Off", period=1, minute=30), _ev("Player On", period=1, minute=32),
                  _ev("Pass", period=1, minute=40, location=[50, 40],
                      **{"pass": {"end_location": [60, 40]}})]
        _, (df, report) = _build(tmp_path, events, _quality_lineups(starter_positions=spells))
        assert self._starter(df)["minutes_played"] == pytest.approx(75.0)
        assert [(d["event_type"], d["period"], d["minute"]) for d in
                self._issues(report, "events_outside_credited_time")] == [("Pass", 1, 40)]

    def test_new_issue_names_fail_or_report_as_documented(self):
        from eleveniq.pipeline.build_player_match import (
            FAILING_QUALITY_ISSUES, PERIOD_AWARE_REPORT_ONLY_ISSUES,
        )
        assert {"period_too_short", "stint_end_after_period_end",
                "stint_time_before_period_start", "event_after_permanent_off",
                "team_minutes_exceed_ceiling"} <= FAILING_QUALITY_ISSUES
        assert not set(PERIOD_AWARE_REPORT_ONLY_ISSUES) & FAILING_QUALITY_ISSUES
        assert "dismissal_after_match_end" in PERIOD_AWARE_REPORT_ONLY_ISSUES


def _pass(pid: int, *, period: int, minute: int, second: int = 0) -> dict:
    return {**_ev("Pass", pid, period=period, minute=minute, location=[50, 40],
                  **{"pass": {"end_location": [60, 40]}}),
            "id": f"pass-{pid}-{period}-{minute}-{second}", "second": second}


class TestPeriodAwarePipelineEdges:
    """ADR 002 §31.2, §31.3 and §31.6 wiring not covered by TestPeriodAwarePipeline."""

    @staticmethod
    def _row(df, pid):
        return df.filter(pl.col("player_id") == pid).row(0, named=True)

    def test_dismissal_record_compares_against_elapsed_card_time(self, tmp_path):
        # First half ends (1, 47:00); red card at (2, 45:30) and the lineup stint
        # ends there too. The card's elapsed time is 47.5 = the uncapped
        # minutes, so nothing was overcounted. Comparing with the raw minute
        # (45.5 + 1 tolerance) would wrongly say "overcounted".
        card = {**_ev("Bad Behaviour", period=2, minute=45,
                      bad_behaviour={"card": {"name": "Red Card"}}), "second": 30}
        lineups = _quality_lineups(starter_positions=[_spell("00:00", "45:30", "Starting XI")])
        _, (df, report) = _build(tmp_path, [_half_end(1, 47), card], lineups)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(47.5)
        entry = next(d for d in report if "dismissal_elapsed" in d)
        assert entry["dismissal_elapsed"] == pytest.approx(47.5)
        assert entry["minutes_without_fix"] == pytest.approx(47.5)
        assert entry["minutes_overcounted_without_fix"] is False
        assert entry["cause"] is None

    def test_dismissal_record_holds_a_substitute_to_entry_until_card(self, tmp_path):
        # On at (2, 60:00), red card at (2, 70:00), lineup stint left open: the
        # raw data would credit him 30 min (to 90:00). That is under the card's
        # elapsed time (70 + 1), so only the entry-to-card span (10 + 1) shows
        # the overcount.
        card = _ev("Bad Behaviour", _BENCH, period=2, minute=70,
                   bad_behaviour={"card": {"name": "Red Card"}})
        lineups = _quality_lineups(
            starter_positions=[_spell("00:00", "60:00", "Starting XI")],
            bench_positions=[_spell("60:00", None, "Substitution")])
        _, (df, report) = _build(tmp_path, [card], lineups)
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(10.0)
        entry = next(d for d in report if d.get("player_id") == _BENCH and "dismissal_elapsed" in d)
        assert entry["entry_elapsed"] == pytest.approx(60.0)
        assert entry["minutes_without_fix"] == pytest.approx(30.0)
        assert entry["minutes_overcounted_without_fix"] is True
        assert entry["cause"] == "to_null"

    def test_unpaired_off_then_own_substitution_is_valid_and_exit_is_the_off(self, tmp_path):
        # §31.2: an injured player leaves (unpaired Player Off at 70:00) and is
        # formally replaced at 72:00. His own Substitution is exempt from
        # event_after_permanent_off, and his exit is the Off, the earlier instant.
        events = [_ev("Player Off", period=2, minute=70),
                  _sub_off(_STARTER, period=2, minute=72)]
        lineups = _quality_lineups(
            bench_positions=[_spell("72:00", None, "Substitution - On (Injury)")])
        _, (df, report) = _build(tmp_path, events, lineups)
        assert self._row(df, _STARTER)["minutes_played"] == pytest.approx(70.0)
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(18.0)
        assert [d["minutes_removed"] for d in report
                if d.get("issue") == "unpaired_player_off_capped"] == [pytest.approx(2.0)]
        assert not [d for d in report if d.get("issue") == "events_outside_credited_time"]

    @pytest.mark.usefixtures("synthetic_small_teams")
    def test_events_outside_credited_time_uses_the_period_clock(self, tmp_path):
        # Half End (1, 47:00). Inside credited time, so not reported:
        #  - the starter's pass in first-half stoppage (1, 46:00);
        #  - his pass after the whistle (1, 48:00): clamped to the period end;
        #  - the substitute's passes after his entry at (1, 46:00), including
        #    (2, 45:30), which is elapsed 47.5 although 45:30 < 46:00.
        # Outside: the substitute's pass at (1, 45:30), before he came on.
        bench = [_spell("46:00", None, "Substitution - On (Tactical)") | {"from_period": 1}]
        events = [_half_end(1, 47),
                  _pass(_STARTER, period=1, minute=46), _pass(_STARTER, period=1, minute=48),
                  _pass(_BENCH, period=1, minute=45, second=30),
                  _pass(_BENCH, period=1, minute=46, second=30),
                  _pass(_BENCH, period=2, minute=45, second=30)]
        _, (df, report) = _build(tmp_path, events, _quality_lineups(bench_positions=bench))
        assert self._row(df, _BENCH)["minutes_played"] == pytest.approx(92.0 - 46.0)
        outside = [(d["player_id"], d["period"], d["minute"], d["second"]) for d in report
                   if d.get("issue") == "events_outside_credited_time"]
        assert outside == [(_BENCH, 1, 45, 30)]


# ---------------------------------------------------------------------------
# Team open-play pass credit (plan 005 M4b, ADR 004 D12)
# ---------------------------------------------------------------------------

def _open_play_pass(pid: int, *, period: int = 1, minute: int = 10, ptype: str | None = None) -> dict:
    payload: dict = {"end_location": [60, 40]}
    if ptype:
        payload["type"] = {"name": ptype}
    return _ev("Pass", pid, period=period, minute=minute, location=[50, 40], **{"pass": payload})


def _full_match_players(n: int) -> list[list[dict]]:
    return [[_spell("00:00", None, "Starting XI")] for _ in range(n)]


class TestPassCreditBounds:
    """ADR 004 D12: every open-play pass is credited to 7-11 players (the Laws
    minimum to the maximum on the pitch); outside that range the build fails with
    pass_credit_out_of_range. These tests run with the real bound (no
    synthetic_small_teams fixture)."""

    def test_config_bounds_are_the_laws_minimum_and_maximum(self):
        from eleveniq import config
        assert config.MIN_PLAYERS_PER_PASS == 7
        assert config.MAX_PLAYERS_PER_PASS == 11

    def test_pipeline_reads_the_config_bounds(self):
        assert (_bpm.MIN_PLAYERS_PER_PASS, _bpm.MAX_PLAYERS_PER_PASS) == (7, 11)

    def test_six_player_team_fails_the_build(self, tmp_path):
        msg = _build_team_fails(tmp_path, _full_match_players(6), [_open_play_pass(_TEAM_BASE)])
        assert "pass_credit_out_of_range" in msg
        assert "'players': 6" in msg

    @pytest.mark.parametrize("n", [7, 11])
    def test_seven_and_eleven_player_teams_pass(self, tmp_path, n):
        df, report = _build_team(tmp_path, _full_match_players(n), [_open_play_pass(_TEAM_BASE)])
        assert not [d for d in report if d.get("issue") == "pass_credit_out_of_range"]
        assert df["team_open_play_passes_while_on"].to_list() == [1] * n
        assert df.filter(pl.col("player_id") == _TEAM_BASE)["open_play_passes_attempted"][0] == 1

    def test_pass_team_mismatch_fails_the_build(self, tmp_path):
        """ADR 004 D12: a pass whose team.id is not its passer's lineup team fails
        the build (pass_team_mismatch). 11 players, so the 7-11 bound is not the cause."""
        bad = {**_open_play_pass(_TEAM_BASE), "team": {"id": _TEAM + 1}}
        msg = _build_team_fails(tmp_path, _full_match_players(11), [bad])
        assert "pass_team_mismatch" in msg

    def test_restart_by_a_small_team_is_not_checked(self, tmp_path):
        """Only open-play passes are credited, so a throw-in never trips the bound."""
        df, _ = _build_team(tmp_path, _full_match_players(6),
                            [_open_play_pass(_TEAM_BASE, ptype="Throw-in")])
        assert df["team_open_play_passes_while_on"].sum() == 0
        assert df["open_play_passes_attempted"].sum() == 0


class TestTeamOpenPlayPassCredits:
    """ADR 004 D12 credit rule on PlayerTimeline objects (team_open_play_pass_credits):
    half-open pieces, closed at the end of the pass's own period; the passer always
    counts; restarts are not credited. Clock: period 1 ends at 47:00, period 2 at
    93:00 (47 + 48 = 95 elapsed minutes)."""

    _CLOCK = PeriodClock({1: 47.0, 2: 93.0})

    def _timeline(self, *stints: tuple) -> object:
        positions = [{"position": "Center Back", "from": f"{int(a):02d}:{round((a % 1) * 60):02d}",
                      "from_period": fp,
                      "to": None if b is None else f"{int(b):02d}:{round((b % 1) * 60):02d}",
                      "to_period": tp, "start_reason": r}
                     for a, fp, b, tp, r in stints]
        return player_timeline(positions, self._CLOCK, exit_at=None, treatment_windows=())

    def _pass(self, pid, period, minute, second=0, ptype=None):
        e = {"id": f"p{pid}-{period}-{minute}-{second}", "type": {"name": "Pass"},
             "player": {"id": pid}, "period": period, "minute": minute, "second": second,
             "pass": {"type": {"name": ptype}} if ptype else {}}
        return e

    def _run(self, events, timelines):
        team_of = {pid: 1 for pid in timelines}
        credits, out_of_range, _ = _bpm.team_open_play_pass_credits(
            events, timelines, team_of, self._CLOCK)
        return credits, out_of_range

    def test_pass_at_the_final_whistle_counts_for_everyone_on(self):
        tl = {1: self._timeline((0, 1, None, None, "Starting XI")),
              2: self._timeline((0, 1, None, None, "Starting XI"))}
        credits, _ = self._run([self._pass(1, 2, 93)], tl)
        assert credits == Counter({1: 1, 2: 1})

    def test_pass_at_half_time_whistle_counts_for_first_half_players_only(self):
        """Player 2 replaces player 1 at (2, 45:00); player 4 plays throughout. The
        pass by player 3 at the period-1 Half End (1, 47:00) counts for 1, 3 and 4."""
        tl = {1: self._timeline((0, 1, 45, 2, "Starting XI")),
              2: self._timeline((45, 2, None, None, "Substitution")),
              3: self._timeline((0, 1, None, None, "Starting XI")),
              4: self._timeline((0, 1, None, None, "Starting XI"))}
        credits, _ = self._run([self._pass(3, 1, 47)], tl)
        assert credits == Counter({1: 1, 3: 1, 4: 1})

    def test_pass_at_a_substitution_instant_counts_for_the_incoming_player(self):
        """1 off and 2 on at (2, 60:00): 3's pass then counts for 2 and 3; 1's own
        pass at that instant still counts for him (the passer always counts)."""
        tl = {1: self._timeline((0, 1, 60, 2, "Starting XI")),
              2: self._timeline((60, 2, None, None, "Substitution")),
              3: self._timeline((0, 1, None, None, "Starting XI"))}
        credits, _ = self._run([self._pass(3, 2, 60), self._pass(1, 2, 60)], tl)
        assert credits == Counter({1: 1, 2: 2, 3: 2})

    def test_second_half_kick_off_counts_despite_float_rounding(self):
        """Period 1 ends at 48:38, so (2, 45:00) and the second-half piece start come
        from different float sums; the pass still counts for every teammate on."""
        clock = PeriodClock({1: 48 + 38 / 60, 2: 93.0})
        positions = [{"position": "Center Back", "from": "00:00", "from_period": 1,
                      "to": None, "to_period": None, "start_reason": "Starting XI"}]
        tl = {pid: player_timeline(positions, clock, exit_at=None, treatment_windows=())
              for pid in (1, 2, 3)}
        credits, _, _ = _bpm.team_open_play_pass_credits(
            [self._pass(1, 2, 45)], tl, {1: 1, 2: 1, 3: 1}, clock)
        assert credits == Counter({1: 1, 2: 1, 3: 1})

    @pytest.mark.parametrize("ptype", ["Throw-in", "Corner", "Free Kick", "Goal Kick", "Kick Off"])
    def test_restarts_are_not_credited(self, ptype):
        tl = {1: self._timeline((0, 1, None, None, "Starting XI"))}
        credits, bad = self._run([self._pass(1, 1, 10, ptype=ptype)], tl)
        assert not credits and not bad

    def test_event_team_id_is_the_team_key(self):
        """The pass carries team.id 1, its passer's lineup team: credited normally."""
        tl = {1: self._timeline((0, 1, None, None, "Starting XI")),
              2: self._timeline((0, 1, None, None, "Starting XI"))}
        e = {**self._pass(1, 1, 10), "team": {"id": 1}}
        credits, _, mismatches = _bpm.team_open_play_pass_credits(
            [e], tl, {1: 1, 2: 1}, self._CLOCK)
        assert credits == Counter({1: 1, 2: 1}) and mismatches == []

    def test_missing_event_team_falls_back_to_the_lineup_team(self):
        tl = {1: self._timeline((0, 1, None, None, "Starting XI")),
              2: self._timeline((0, 1, None, None, "Starting XI"))}
        credits, _, mismatches = _bpm.team_open_play_pass_credits(
            [self._pass(1, 1, 10)], tl, {1: 1, 2: 1}, self._CLOCK)
        assert credits == Counter({1: 1, 2: 1}) and mismatches == []

    def test_event_team_differing_from_lineup_team_is_a_mismatch(self):
        """team.id 2 but the passer is in team 1's lineup: reported, credited to nobody."""
        tl = {1: self._timeline((0, 1, None, None, "Starting XI")),
              2: self._timeline((0, 1, None, None, "Starting XI"))}
        e = {**self._pass(1, 1, 10), "team": {"id": 2}}
        credits, _, mismatches = _bpm.team_open_play_pass_credits(
            [e], tl, {1: 1, 2: 1}, self._CLOCK)
        assert not credits
        assert mismatches == [{"event_id": e["id"], "player_id": 1,
                               "event_team_id": 2, "lineup_team_id": 1}]
        assert "pass_team_mismatch" in _bpm.FAILING_QUALITY_ISSUES

    def test_out_of_range_pass_is_reported_with_its_count(self):
        """Under the real bound a 2-player team's pass is out of range."""
        tl = {1: self._timeline((0, 1, None, None, "Starting XI")),
              2: self._timeline((0, 1, None, None, "Starting XI"))}
        _, bad = self._run([self._pass(1, 1, 10)], tl)
        assert len(bad) == 1 and bad[0]["players"] == 2


# ---------------------------------------------------------------------------
# Report-only limits and the late-start limit (plan 005 M4b, ADR 004 D22)
# ---------------------------------------------------------------------------

class TestLateStartLimit:
    """ADR 004 D22: a stint starting after its period's Half End is report-only
    (stint_after_period_end); more than STINT_LATE_START_FAIL_MINUTES (2.0) late
    also fails the build (stint_late_start_exceeds_limit)."""

    _CLOCK = PeriodClock({1: 45.0, 2: 90.0})

    def _issues(self, frm: str):
        stint = {"position": "Center Back", "from": frm, "from_period": 1, "to": None,
                 "to_period": None, "start_reason": "Substitution"}
        return [d["issue"] for d in _bpm._stint_bound_issues(1, 2, [stint], self._CLOCK)]

    def test_config_limit_is_two_minutes(self):
        from eleveniq import config
        assert config.STINT_LATE_START_FAIL_MINUTES == 2.0

    def test_one_second_late_is_report_only(self):
        assert self._issues("45:01") == ["stint_after_period_end"]

    def test_exactly_two_minutes_late_is_report_only(self):
        assert self._issues("47:00") == ["stint_after_period_end"]

    def test_build_fails_on_a_return_two_minutes_thirty_late(self, tmp_path):
        """End to end: the bench player's stint starts at (1, 47:30), 2:30 after the
        (1, 45:00) whistle: the build fails."""
        lineups = _quality_lineups(bench_positions=[_spell("47:30", None, "Player On")
                                                    | {"from_period": 1}])
        assert "stint_late_start_exceeds_limit" in _build_fails(tmp_path, [], lineups)

    def test_build_passes_on_a_return_exactly_two_minutes_late(self, tmp_path):
        lineups = _quality_lineups(bench_positions=[_spell("47:00", None, "Player On")
                                                    | {"from_period": 1}])
        _, (_, report) = _build(tmp_path, [], lineups)
        assert [d["issue"] for d in report if d.get("issue", "").startswith("stint_")] == [
            "stint_after_period_end"]

    def test_two_minutes_thirty_late_fails(self):
        """A return 2:30 after the whistle exceeds the 2-minute limit."""
        assert self._issues("47:30") == ["stint_after_period_end", "stint_late_start_exceeds_limit"]

    def test_more_than_two_minutes_late_fails(self):
        assert self._issues("47:01") == ["stint_after_period_end", "stint_late_start_exceeds_limit"]
        assert "stint_late_start_exceeds_limit" in _bpm.FAILING_QUALITY_ISSUES


class TestReportOnlyLimits:
    """ADR 004 D22: per competition-season, a report-only issue fails the build when
    its count exceeds max(3 x worst 2015/16 rate per 100 matches x matches / 100, 3)."""

    def test_limits_cover_exactly_the_report_only_issues(self):
        from eleveniq import config
        assert set(config.REPORT_ONLY_LIMITS_PER_100_MATCHES) == (
            set(_bpm.PERIOD_AWARE_REPORT_ONLY_ISSUES) | set(_bpm.XGA_FALLBACKS))
        assert config.REPORT_ONLY_LIMIT_FLOOR == 3

    def test_period_end_limit_for_a_380_match_season_is_51(self):
        """Serie A 2015/16: 17 of 380 → 3 x 17 = 51 allowed per 380 matches."""
        report = [{"issue": "period_end_before_nominal", "match_id": i} for i in range(51)]
        assert _bpm.report_only_limit_failures(report, 380) == []
        report.append({"issue": "period_end_before_nominal", "match_id": 99})
        (failure,) = _bpm.report_only_limit_failures(report, 380)
        assert failure["report_issue"] == "period_end_before_nominal"
        assert failure["count"] == 52 and failure["limit"] == pytest.approx(51.0)

    def test_floor_of_three_applies_to_a_zero_rate_issue(self):
        report = [{"issue": "dismissal_after_match_end", "match_id": i} for i in range(3)]
        assert _bpm.report_only_limit_failures(report, 380) == []
        report.append({"issue": "dismissal_after_match_end", "match_id": 3})
        assert len(_bpm.report_only_limit_failures(report, 380)) == 1

    def test_events_outside_credited_time_count_player_matches_not_events(self):
        """11 events by one player in one match count once (Milinković-Savić)."""
        one = [{"issue": "events_outside_credited_time", "match_id": 1, "player_id": 7}] * 11
        assert _bpm.report_only_limit_failures(one, 380) == []
        many = [{"issue": "events_outside_credited_time", "match_id": m, "player_id": 7}
                for m in range(10)]
        (failure,) = _bpm.report_only_limit_failures(many, 380)
        assert failure["count"] == 10

    def test_exceeding_a_limit_fails_the_build(self, tmp_path, monkeypatch):
        """End to end: with the limit forced to 0 and the floor to 0, one
        period_end_before_nominal entry (a 44:00 first half) fails the build."""
        monkeypatch.setattr(_bpm, "REPORT_ONLY_LIMIT_FLOOR", 0)
        monkeypatch.setitem(_bpm.REPORT_ONLY_LIMITS_PER_100_MATCHES, "period_end_before_nominal", 0.0)
        events = [_ev("Half End", None, period=1, minute=44)]
        msg = _build_fails(tmp_path, events)
        assert "report_only_limit_exceeded" in msg


# ---------------------------------------------------------------------------
# QA (plan 005 M4b): per-event M4b counters, credit edges, nominal duration
# ---------------------------------------------------------------------------

def _m4b_pass(outcome: str | None = None, *, ptype: str | None = None, length: float = 20.0,
              start=(50, 40), end=(70, 40), explicit_null_type: bool = False) -> dict:
    data: dict = {"end_location": list(end), "length": length}
    if outcome is not None:
        data["outcome"] = {"name": outcome}
    if ptype is not None:
        data["type"] = {"name": ptype}
    elif explicit_null_type:
        data["type"] = None
    return _ev("Pass", location=list(start), **{"pass": data})


class TestAggregateEventsM4bCounters:
    """_aggregate_events for ADR 004 D12 (open play), D13 (Injury Clearance) and
    D15 (non-penalty shots/xG). The spike counterparts are tested elsewhere; these
    pin the pipeline's own counting."""

    def test_penalty_is_out_of_np_shots_and_np_xg(self):
        """D15: a penalty stays in shots/xg/goals but leaves np_shots and np_xg."""
        pen = _ev("Shot", shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.76,
                                "type": {"name": "Penalty"}}, location=[108, 40])
        op = _ev("Shot", shot={"outcome": {"name": "Off T"}, "statsbomb_xg": 0.1,
                               "type": {"name": "Open Play"}}, location=[110, 40])
        r = _aggregate_events([pen, op], Counter())
        assert (r["shots"], r["np_shots"], r["goals"]) == (2, 1, 1)
        assert r["xg"] == pytest.approx(0.86)
        assert r["np_xg"] == pytest.approx(0.1)

    def test_long_injury_clearance_is_counted_never_completed_nor_progressive(self):
        """D13: an Injury Clearance is its own outcome: not completed, not unknown,
        never progressive even when it gains ground; an open-play long one is a
        long-pass Injury Clearance and an open-play pass (D12 counts every outcome)."""
        ic = _m4b_pass("Injury Clearance", length=45.0, start=(60, 40), end=(105, 40))
        r = _aggregate_events([ic], Counter())
        assert (r["passes_attempted"], r["passes_completed"], r["passes_unknown_outcome"],
                r["passes_injury_clearance"]) == (1, 0, 0, 1)
        assert (r["long_passes_attempted"], r["long_passes_completed"],
                r["long_passes_unknown_outcome"], r["long_passes_injury_clearance"]) == (1, 0, 0, 1)
        assert r["progressive_passes"] == 0
        assert r["final_third_entries_pass"] == 0
        assert r["open_play_passes_attempted"] == 1

    @pytest.mark.parametrize("ptype", ["Throw-in", "Goal Kick", "Free Kick"])
    def test_long_restart_injury_clearance_is_not_a_long_pass_part(self, ptype):
        """D13/D14: a long restart ending as an Injury Clearance is a pass Injury
        Clearance but no long pass, so long_passes_injury_clearance stays a subset of
        long_passes_attempted (otherwise the long-pass denominator goes negative)."""
        r = _aggregate_events([_m4b_pass("Injury Clearance", ptype=ptype, length=40.0)], Counter())
        assert r["passes_injury_clearance"] == 1
        assert (r["long_passes_attempted"], r["long_passes_injury_clearance"]) == (0, 0)
        assert r["open_play_passes_attempted"] == 0

    def test_open_play_counts_every_outcome_and_a_null_type(self):
        """D12: open play = every outcome, restarts excluded. An explicit JSON null
        pass.type is open play, not a crash or a restart."""
        events = [_m4b_pass(None), _m4b_pass("Incomplete"), _m4b_pass("Unknown"),
                  _m4b_pass("Injury Clearance"), _m4b_pass("Out"),
                  _m4b_pass(None, explicit_null_type=True),
                  _m4b_pass(None, ptype="Recovery"), _m4b_pass(None, ptype="Interception"),
                  _m4b_pass(None, ptype="Corner"), _m4b_pass("Out", ptype="Throw-in"),
                  _m4b_pass(None, ptype="Kick Off")]
        r = _aggregate_events(events, Counter())
        assert r["passes_attempted"] == 11
        assert r["open_play_passes_attempted"] == 8


class TestTeamOpenPlayPassCreditEdges:
    """ADR 004 D12 credit rule: only the passing team's players on the pitch at the
    pass instant are credited. Edge cases from the project's bug list: opponents,
    a red card, an injury treatment window, a passer outside the lineups.
    Clock: 45:00 / 90:00."""

    _CLOCK = PeriodClock({1: 45.0, 2: 90.0})
    _FULL = [{"position": "Center Back", "from": "00:00", "from_period": 1, "to": None,
              "to_period": None, "start_reason": "Starting XI"}]

    def _tl(self, *, exit_at=None, windows=()):
        return player_timeline(self._FULL, self._CLOCK, exit_at=exit_at, treatment_windows=windows)

    def _pass(self, pid, period, minute, team=None):
        e = {"id": f"p{pid}-{period}-{minute}", "type": {"name": "Pass"},
             "player": {"id": pid}, "period": period, "minute": minute, "second": 0,
             "pass": {}}
        if team is not None:
            e["team"] = {"id": team}
        return e

    def _run(self, events, timelines, team_of):
        return _bpm.team_open_play_pass_credits(events, timelines, team_of, self._CLOCK)

    def test_opponents_on_the_pitch_are_never_credited(self):
        tl = {pid: self._tl() for pid in (1, 2, 3, 4)}
        team_of = {1: 10, 2: 10, 3: 20, 4: 20}
        credits, _, mismatches = self._run(
            [self._pass(1, 1, 10, team=10), self._pass(3, 1, 20, team=20),
             self._pass(4, 2, 70, team=20)], tl, team_of)
        assert credits == Counter({1: 1, 2: 1, 3: 2, 4: 2})
        assert mismatches == []

    def test_sent_off_teammate_is_not_credited_after_the_red_card(self):
        """Player 2 is sent off at (1, 30:00): credited for the 20' pass only. A
        later pass is credited to one player fewer (10 men)."""
        tl = {1: self._tl(), 2: self._tl(exit_at=(1, 30.0)), 3: self._tl()}
        credits, _, _ = self._run([self._pass(1, 1, 20), self._pass(1, 2, 60)], tl,
                                  {1: 1, 2: 1, 3: 1})
        assert credits == Counter({1: 2, 2: 1, 3: 2})

    def test_teammate_off_for_treatment_is_not_credited_during_it(self):
        """Player 2 leaves for treatment (1, 20:00) and returns (1, 25:00): a pass at
        22' is not credited to him; one at 25:00 (his return instant, half-open
        piece start) and one at 30' are."""
        tl = {1: self._tl(), 2: self._tl(windows=[((1, 20.0), (1, 25.0))])}
        credits, _, _ = self._run(
            [self._pass(1, 1, 22), self._pass(1, 1, 25), self._pass(1, 1, 30)], tl, {1: 1, 2: 1})
        assert credits == Counter({1: 3, 2: 2})

    def test_teammate_leaving_at_the_pass_instant_is_not_credited(self):
        """Half-open pieces: a teammate whose piece ends at (1, 30:00), mid-period,
        is not on the pitch for a pass at exactly 30:00."""
        tl = {1: self._tl(), 2: self._tl(exit_at=(1, 30.0))}
        credits, _, _ = self._run([self._pass(1, 1, 30)], tl, {1: 1, 2: 1})
        assert credits == Counter({1: 1})

    def test_pass_by_a_player_outside_the_lineups_is_skipped(self):
        """Such a pass is a dropped event (fails elsewhere); here it credits nobody
        and is not reported as out of range."""
        tl = {1: self._tl(), 2: self._tl()}
        credits, bad, mismatches = self._run([self._pass(99, 1, 10, team=1)], tl, {1: 1, 2: 1})
        assert not credits and bad == [] and mismatches == []


class TestOpenPlayCreditPipeline:
    """End to end (ADR 004 D12): team_open_play_passes_while_on on output rows for a
    substitute and a sent-off player, under the real 7-11 bound."""

    def test_substitute_and_sent_off_player_credited_only_while_on(self, tmp_path):
        """11 starters. P0 is replaced at 60:00 by P11; P1 sees red at (2, 50:00).
        Passes by P2 at 30' and 70', by P11 at 75'. Expected credits: P0 1 (30'),
        P1 1 (30'), P11 2 (70', 75'), the other nine 3."""
        positions = _full_match_players(11)
        positions[0] = [_spell("00:00", "60:00", "Starting XI")]
        positions.append([_spell("60:00", None, "Substitution - On (Tactical)")])
        sub = _ev("Substitution", _TEAM_BASE, period=2, minute=60,
                  substitution={"replacement": {"id": _TEAM_BASE + 11}})
        red = _ev("Bad Behaviour", _TEAM_BASE + 1, period=2, minute=50,
                  bad_behaviour={"card": {"name": "Red Card"}})
        events = [sub, red,
                  _open_play_pass(_TEAM_BASE + 2, period=1, minute=30),
                  _open_play_pass(_TEAM_BASE + 2, period=2, minute=70),
                  _open_play_pass(_TEAM_BASE + 11, period=2, minute=75)]
        df, report = _build_team(tmp_path, positions, events)
        credits = dict(zip(df["player_id"].to_list(),
                           df["team_open_play_passes_while_on"].to_list()))
        expected = {_TEAM_BASE + i: 3 for i in range(2, 11)}
        expected.update({_TEAM_BASE: 1, _TEAM_BASE + 1: 1, _TEAM_BASE + 11: 2})
        assert credits == expected
        # The passer is always credited: a share can never exceed 1.
        assert (df["open_play_passes_attempted"] <= df["team_open_play_passes_while_on"]).all()
        assert not [d for d in report if d.get("issue") == "pass_credit_out_of_range"]


class TestMatchNominalDuration:
    """ADR 004 D1: match_nominal_duration is 90, or 120 when the match has extra
    time, whatever the elapsed match_duration (stoppage time)."""

    def test_regular_match_with_stoppage_is_nominal_90(self, tmp_path):
        _, (df, _) = _build(tmp_path, [_ev("Half End", None, period=2, minute=94)])
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["match_duration"] == pytest.approx(94.0)
        assert row["match_nominal_duration"] == 90.0

    def test_extra_time_match_is_nominal_120(self, tmp_path):
        _, (df, _) = _build(tmp_path, [_ev("Half End", None, period=4, minute=121)])
        row = df.filter(pl.col("player_id") == _STARTER).row(0, named=True)
        assert row["match_duration"] == pytest.approx(121.0)
        assert row["match_nominal_duration"] == 120.0


class TestPassOutsideCreditedTime:
    """ADR 004 D12 (user decision 2026-10-03): a pass inside match time is credited
    to its passer plus every teammate on the pitch, even when the passer has no
    credited time at that instant (a timeline inconsistency, not absence); the event
    is reported as events_outside_credited_time (ADR 002 §31.6). A pass stamped
    after the whistle is clamped to the whistle instant. Real 7-11 bound."""

    def test_milinkovic_savic_pattern_passer_still_credited(self):
        """Lazio 3879637 pattern: the passer's lineup time stops at (1, 44:27) and
        resumes only at (2, 45:00), but he passes at (1, 45:25), before the (1, 48:47)
        whistle. The 10 teammates on the pitch plus the passer: 11 credits."""
        clock = PeriodClock({1: 48 + 47 / 60, 2: 93.0})

        def tl(*stints):
            return player_timeline(
                [{"position": "Center Back", "from": a, "from_period": fp, "to": b,
                  "to_period": tp, "start_reason": "Starting XI"} for a, fp, b, tp in stints],
                clock, exit_at=None, treatment_windows=())
        timelines = {i: tl(("00:00", 1, None, None)) for i in range(1, 11)}
        timelines[0] = tl(("00:00", 1, "44:27", 1), ("45:00", 2, None, None))
        assert not timelines[0].covers(clock.elapsed((1, 45 + 25 / 60)))
        e = {"id": "p", "type": {"name": "Pass"}, "player": {"id": 0}, "team": {"id": 1},
             "period": 1, "minute": 45, "second": 25, "pass": {}}
        credits, out_of_range, mismatches = _bpm.team_open_play_pass_credits(
            [e], timelines, {i: 1 for i in range(11)}, clock)
        assert credits == Counter({i: 1 for i in range(11)})
        assert sum(credits.values()) == 11
        assert not out_of_range and not mismatches

    def test_pass_outside_the_passers_time_is_credited_and_reported(self, tmp_path):
        """End to end, same pattern: the period-1 whistle is at (1, 48:00); player 0's
        stint stops at (1, 44:00) and resumes at (2, 45:00); he passes at (1, 46:00).
        All 11 are credited, nothing fails, and the pass is reported."""
        players = _full_match_players(11)
        players[0] = [{**_spell("00:00", "44:00", "Starting XI"), "to_period": 1},
                      {**_spell("45:00", None, "Tactical Shift"), "from_period": 2}]
        events = [_ev("Half End", None, period=1, minute=48),
                  _open_play_pass(_TEAM_BASE, period=1, minute=46)]
        df, report = _build_team(tmp_path, players, events)
        assert not [d for d in report if d.get("issue") == "pass_credit_out_of_range"]
        outside = [d for d in report if d.get("issue") == "events_outside_credited_time"]
        assert [d["player_id"] for d in outside] == [_TEAM_BASE]
        assert df["team_open_play_passes_while_on"].to_list() == [1] * 11

    def test_post_whistle_pass_is_credited_to_the_players_on_at_the_whistle(self, tmp_path):
        """A pass stamped (1, 46:00), after the (1, 45:00) whistle, is clamped to the
        whistle instant: it counts for the 11 on the pitch then, and is not outside
        anyone's credited time."""
        events = [_open_play_pass(_TEAM_BASE, period=1, minute=46)]
        df, report = _build_team(tmp_path, _full_match_players(11), events)
        assert not [d for d in report if d.get("issue") in (
            "pass_credit_out_of_range", "events_outside_credited_time")]
        assert df["team_open_play_passes_while_on"].to_list() == [1] * 11


class TestXgAssistedFallbacksReported:
    """ADR 004 D16: _process_match writes each xG-assisted fallback as a per-match
    report-only entry with its count (the counters replaced the old log lines, so
    a broken wiring would make them silent)."""

    def test_no_receipt_shot_is_reported_by_process_match(self, tmp_path):
        key_pass = {**_ev("Pass", _STARTER, minute=20), "index": 20, "possession": 5,
                    "location": [80, 40],
                    "pass": {"end_location": [110, 40], "assisted_shot_id": "shot-1",
                             "shot_assist": True}}
        shot = {**_ev("Shot", _BENCH, minute=21), "id": "shot-1", "index": 21, "possession": 5,
                "possession_team": {"id": _TEAM}, "location": [110, 40],
                "shot": {"statsbomb_xg": 0.3, "outcome": {"name": "Saved"},
                         "type": {"name": "Open Play"}, "key_pass_id": key_pass["id"]}}
        lineups = _quality_lineups(bench_positions=[_spell("00:00", None, "Starting XI")])
        _write_match(tmp_path, 8100, [_xi_event(_TEAM, [_STARTER, _BENCH]), key_pass, shot,
                                      _HALF_END], lineups)
        rows, report = _process_match(8100, 99, 1, data_root=tmp_path)
        assert [d for d in report if d.get("issue") == "xga_no_receipt"] == [
            {"issue": "xga_no_receipt", "match_id": 8100, "count": 1}]
        passer = next(r for r in rows if r["player_id"] == _STARTER)
        assert passer["xg_assisted"] == pytest.approx(0.3)
        assert passer["xg_assisted_direct"] == 0.0


class TestXgAssistedFallbackLimits:
    """ADR 004 D22 extended to the D16 fallback counters (user, 2026-10-03): 3 x the
    worst 2015/16 rate (La Liga 6 no-receipt shots / 380), floor 3; counted in
    shots, summed over the season's per-match entries."""

    def test_no_receipt_limit_for_a_380_match_season_is_18(self):
        report = [{"issue": "xga_no_receipt", "match_id": m, "count": 6} for m in range(3)]
        assert _bpm.report_only_limit_failures(report, 380) == []
        report.append({"issue": "xga_no_receipt", "match_id": 9, "count": 1})
        (failure,) = _bpm.report_only_limit_failures(report, 380)
        assert failure["report_issue"] == "xga_no_receipt"
        assert failure["count"] == 19 and failure["limit"] == pytest.approx(18.0)

    @pytest.mark.parametrize("issue", ["xga_missing_shooter", "xga_dangling_shot_id",
                                       "xga_missing_assister"])
    def test_zero_rate_fallbacks_allow_the_floor_of_three_shots(self, issue):
        assert _bpm.report_only_limit_failures(
            [{"issue": issue, "match_id": 1, "count": 3}], 380) == []
        assert len(_bpm.report_only_limit_failures(
            [{"issue": issue, "match_id": 1, "count": 2}, {"issue": issue, "match_id": 2, "count": 2}],
            380)) == 1
