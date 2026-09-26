"""Tests for src/metrics/positions.py"""
from __future__ import annotations
from pathlib import Path
import pytest
from eleveniq.metrics.positions import (
    assign_position_group,
    compute_minutes_per_group,
    primary_position_group,
    POSITION_GROUP_MAP,
    UNKNOWN_POSITION_GROUP,
    GK_GROUP,
    OUTFIELD_GROUPS,
)


# ---------------------------------------------------------------------------
# assign_position_group
# ---------------------------------------------------------------------------

class TestAssignPositionGroup:
    def test_goalkeeper(self):
        assert assign_position_group("Goalkeeper") == "GK"

    def test_center_back(self):
        assert assign_position_group("Center Back") == "CB"
        assert assign_position_group("Left Center Back") == "CB"
        assert assign_position_group("Right Center Back") == "CB"

    def test_fullback_wingback(self):
        assert assign_position_group("Left Back") == "FB-WB"
        assert assign_position_group("Right Back") == "FB-WB"
        assert assign_position_group("Left Wing Back") == "FB-WB"
        assert assign_position_group("Right Wing Back") == "FB-WB"

    def test_dm_cm(self):
        assert assign_position_group("Defensive Midfield") == "DM-CM"
        assert assign_position_group("Center Midfield") == "DM-CM"
        assert assign_position_group("Left Center Midfield") == "DM-CM"
        assert assign_position_group("Right Center Midfield") == "DM-CM"

    def test_am_winger(self):
        assert assign_position_group("Left Wing") == "AM-Winger"
        assert assign_position_group("Right Wing") == "AM-Winger"
        assert assign_position_group("Attacking Midfield") == "AM-Winger"
        assert assign_position_group("Center Attacking Midfield") == "AM-Winger"

    def test_left_right_attacking_midfield_added(self):
        # These were missing in the original map — stats reviewer flagged them
        assert assign_position_group("Left Attacking Midfield") == "AM-Winger"
        assert assign_position_group("Right Attacking Midfield") == "AM-Winger"

    def test_striker(self):
        assert assign_position_group("Center Forward") == "ST"
        assert assign_position_group("Secondary Striker") == "ST"

    def test_unknown_position(self):
        assert assign_position_group("Unknown Role XYZ") == UNKNOWN_POSITION_GROUP

    def test_empty_string_is_unknown(self):
        assert assign_position_group("") == UNKNOWN_POSITION_GROUP


class TestGroupConstants:
    def test_gk_group_constant(self):
        assert GK_GROUP == "GK"

    def test_gk_not_in_outfield_groups(self):
        assert "GK" not in OUTFIELD_GROUPS

    def test_all_outfield_groups_present(self):
        assert OUTFIELD_GROUPS == {"CB", "FB-WB", "DM-CM", "AM-Winger", "ST"}

    def test_all_map_values_are_known_groups(self):
        known = OUTFIELD_GROUPS | {GK_GROUP}
        for position, group in POSITION_GROUP_MAP.items():
            assert group in known, f"Position '{position}' maps to unknown group '{group}'"


# ---------------------------------------------------------------------------
# compute_minutes_per_group
# ---------------------------------------------------------------------------

class TestComputeMinutesPerGroup:
    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_single_stint_full_match(self):
        stints = [self._stint("Center Back", "00:00", None)]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"CB": pytest.approx(90.0)}

    def test_single_stint_sub(self):
        stints = [self._stint("Center Forward", "60:00", None)]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"ST": pytest.approx(30.0)}

    def test_tactical_shift_same_group(self):
        stints = [
            self._stint("Left Center Midfield", "00:00", "55:00"),
            self._stint("Left Defensive Midfield", "55:00", None),
        ]
        result = compute_minutes_per_group(stints, match_duration=92.0)
        assert result == {"DM-CM": pytest.approx(92.0)}

    def test_tactical_shift_different_groups(self):
        stints = [
            self._stint("Center Midfield", "00:00", "70:00"),
            self._stint("Center Attacking Midfield", "70:00", None),
        ]
        result = compute_minutes_per_group(stints, match_duration=92.0)
        assert result["DM-CM"] == pytest.approx(70.0)
        assert result["AM-Winger"] == pytest.approx(22.0)

    def test_unused_sub_returns_empty(self):
        assert compute_minutes_per_group([], match_duration=90.0) == {}

    def test_dismissal_caps_open_stint(self):
        stints = [self._stint("Center Back", "00:00", None)]
        result = compute_minutes_per_group(stints, match_duration=92.0, dismissal_minute=15.0)
        assert result == {"CB": pytest.approx(15.0)}

    def test_position_as_dict_handled(self):
        # Robustness: position stored as {"name": "..."} dict form
        stints = [{"position": {"name": "Right Wing"}, "from": "00:00", "to": None}]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"AM-Winger": pytest.approx(90.0)}

    def test_unknown_position_goes_to_unknown_key(self):
        stints = [self._stint("Libero", "00:00", "90:00")]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"UNKNOWN": pytest.approx(90.0)}

    def test_none_position_goes_to_unknown_not_crash(self):
        # StatsBomb JSON with "position": null — must not str(None) → "None"
        stints = [{"position": None, "from": "00:00", "to": "45:00"}]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"UNKNOWN": pytest.approx(45.0)}

    def test_zero_length_stint_omitted(self):
        stints = [self._stint("Left Back", "45:00", "45:00")]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {}


# ---------------------------------------------------------------------------
# primary_position_group
# ---------------------------------------------------------------------------

class TestPrimaryPositionGroup:
    def test_single_group(self):
        assert primary_position_group({"CB": 90.0}) == "CB"

    def test_most_minutes_wins(self):
        groups = {"DM-CM": 70.0, "AM-Winger": 20.0}
        assert primary_position_group(groups) == "DM-CM"

    def test_tie_broken_alphabetically_first(self):
        # DM-CM and AM-Winger have equal minutes; AM-Winger sorts before DM-CM
        groups = {"DM-CM": 45.0, "AM-Winger": 45.0}
        assert primary_position_group(groups) == "AM-Winger"

    def test_tie_cb_vs_fb_wb(self):
        # CB < FB-WB alphabetically → CB wins tie
        groups = {"CB": 45.0, "FB-WB": 45.0}
        assert primary_position_group(groups) == "CB"

    def test_empty_returns_unknown(self):
        assert primary_position_group({}) == UNKNOWN_POSITION_GROUP

    def test_three_groups_most_minutes_wins(self):
        groups = {"CB": 20.0, "DM-CM": 50.0, "AM-Winger": 30.0}
        assert primary_position_group(groups) == "DM-CM"

    def test_gk_can_be_primary(self):
        assert primary_position_group({"GK": 90.0}) == "GK"


# ---------------------------------------------------------------------------
# Integration test: no UNKNOWN positions in pipeline output
# ---------------------------------------------------------------------------

_PLAYER_MATCH_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
)


@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output — run 'python -m src.cli build-match' first",
)
def test_no_unknown_positions_in_pipeline_output():
    """After the pipeline runs, every position_raw in the output must map to a known group."""
    import polars as pl
    df = pl.read_parquet(_PLAYER_MATCH_PARQUET)
    unknowns = (
        df.filter(pl.col("position_group") == "UNKNOWN")["position_raw"]
        .unique()
        .to_list()
    )
    assert unknowns == [], (
        f"position_raw values mapping to UNKNOWN — add them to POSITION_GROUP_MAP: {unknowns}"
    )
