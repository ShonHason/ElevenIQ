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
    STARTING_XI_ISSUES,
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
        entry = next(d for d in report if d.get("issue") == "starting_xi_mismatch")
        assert entry["missing_from_derived"] == [6002]
        assert entry["extra_in_derived"] == [6001]

    def test_missing_starting_xi_event_recorded(self, tmp_path):
        _write_match(tmp_path, 7003, [_HALF_END], self._lineups())
        _, report = _process_match(7003, 99, 1, data_root=tmp_path)
        assert any(d.get("issue") == "starting_xi_missing" for d in report)

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
        ]
        assert summarise_quality_report(report) == {
            "dismissals": 2,
            "missing_timestamp": 1,
            "fallback_team_matches": 2,
            "fallback_players": 4,
            "starter_count_mismatches": 1,
        }

    def test_empty_report_all_zero(self):
        from eleveniq.pipeline.build_player_match import summarise_quality_report
        assert set(summarise_quality_report([]).values()) == {0}
