"""Tests for src/metrics/positions.py"""
from __future__ import annotations
from pathlib import Path
import pytest
from eleveniq.metrics.minutes import compute_player_minutes
from eleveniq.metrics.positions import (
    assign_position_group,
    compute_minutes_per_group,
    compute_minutes_per_raw_position,
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

    def test_center_defensive_midfield(self):
        # Added in Milestone 3: Center Defensive Midfield was missing from the
        # map and caused UNKNOWN assignments in La Liga 2015/16.
        # ADR 002 §5 position table: DM-CM includes "Center Defensive Midfield".
        assert assign_position_group("Center Defensive Midfield") == "DM-CM"

    def test_left_right_defensive_midfield(self):
        assert assign_position_group("Left Defensive Midfield") == "DM-CM"
        assert assign_position_group("Right Defensive Midfield") == "DM-CM"

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

    def test_explicit_to_after_dismissal_clamped(self):
        # Gustavo-Cabral-like: first stint has explicit to=66:43 but card at 56:10.
        # Without clamping the full stint would count as 66.72 min.
        stints = [
            self._stint("Right Center Back", "00:00", "66:43"),
            self._stint("Right Center Midfield", "66:43", None),
        ]
        result = compute_minutes_per_group(stints, match_duration=95.0, dismissal_minute=56.17)
        assert sum(result.values()) == pytest.approx(56.17)

    def test_explicit_to_before_dismissal_unchanged(self):
        # Substituted off at 45:00, card at 60:00 — the 45-min stint must not be extended.
        stints = [self._stint("Center Back", "00:00", "45:00")]
        result = compute_minutes_per_group(stints, match_duration=90.0, dismissal_minute=60.0)
        assert result == {"CB": pytest.approx(45.0)}

    def test_explicit_to_no_dismissal_not_clamped_by_match_duration(self):
        # Without a dismissal, explicit to-values are trusted as-is even if they
        # are fractionally above match_duration (e.g. substitution logged 1s late).
        stints = [self._stint("Left Back", "00:00", "90:05")]
        result = compute_minutes_per_group(stints, match_duration=90.0)
        assert result == {"FB-WB": pytest.approx(90 + 5 / 60)}


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


# ---------------------------------------------------------------------------
# Cross-consistency: sum(compute_minutes_per_group) == compute_player_minutes
# ---------------------------------------------------------------------------

class TestMinutesConsistency:
    """
    Verifies that the shared _iter_stint_contributions generator keeps
    compute_minutes_per_group and compute_player_minutes in sync.
    sum(group minutes) must equal total player minutes for any input.
    """

    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_starter_full_match(self):
        positions = [self._stint("Center Back", "00:00", None)]
        duration = 92.0
        group_total = sum(compute_minutes_per_group(positions, duration).values())
        assert group_total == pytest.approx(compute_player_minutes(positions, duration))

    def test_sub_on_halfway(self):
        positions = [self._stint("Center Forward", "60:00", None)]
        duration = 93.0
        group_total = sum(compute_minutes_per_group(positions, duration).values())
        assert group_total == pytest.approx(compute_player_minutes(positions, duration))

    def test_tactical_shift_two_groups(self):
        positions = [
            self._stint("Center Midfield", "00:00", "55:00"),
            self._stint("Center Attacking Midfield", "55:00", None),
        ]
        duration = 92.0
        group_total = sum(compute_minutes_per_group(positions, duration).values())
        assert group_total == pytest.approx(compute_player_minutes(positions, duration))

    def test_with_dismissal(self):
        positions = [self._stint("Left Back", "00:00", None)]
        duration = 90.0
        d_min = 45.0
        group_total = sum(
            compute_minutes_per_group(positions, duration, dismissal_minute=d_min).values()
        )
        assert group_total == pytest.approx(
            compute_player_minutes(positions, duration, dismissal_minute=d_min)
        )

    def test_unused_sub_both_zero(self):
        group_total = sum(compute_minutes_per_group([], 90.0).values())
        assert group_total == pytest.approx(compute_player_minutes([], 90.0))

    def test_explicit_to_past_dismissal_consistency(self):
        """Consistency holds when explicit to-values are clamped by the dismissal."""
        positions = [
            self._stint("Right Center Back", "00:00", "66:43"),
            self._stint("Right Center Midfield", "66:43", "81:27"),
            self._stint("Left Center Midfield", "81:27", None),
        ]
        duration = 95.0
        d_min = 56.17
        group_total = sum(
            compute_minutes_per_group(positions, duration, dismissal_minute=d_min).values()
        )
        player_total = compute_player_minutes(positions, duration, dismissal_minute=d_min)
        assert group_total == pytest.approx(player_total)
        assert group_total == pytest.approx(d_min)

    def test_raw_position_sum_equals_group_sum(self):
        """compute_minutes_per_raw_position and compute_minutes_per_group share the same generator."""
        positions = [
            self._stint("Center Midfield", "00:00", "70:00"),
            self._stint("Left Wing", "70:00", None),
        ]
        duration = 93.0
        raw_total = sum(compute_minutes_per_raw_position(positions, duration).values())
        group_total = sum(compute_minutes_per_group(positions, duration).values())
        assert raw_total == pytest.approx(group_total)


# ---------------------------------------------------------------------------
# compute_minutes_per_raw_position — direct contract tests
# ---------------------------------------------------------------------------

class TestComputeMinutesPerRawPosition:
    """
    compute_minutes_per_raw_position preserves the StatsBomb position name
    as the key (not the group).  Its docstring guarantees sum of values
    equals sum(compute_minutes_per_group(...).values()) for the same inputs.
    """

    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_empty_positions_returns_empty_dict(self):
        # Docstring: "Returns an empty dict for players who never took the field."
        result = compute_minutes_per_raw_position([], match_duration=90.0)
        assert result == {}

    def test_keys_are_raw_position_names_not_groups(self):
        # Center Back → group CB, but the raw key must be "Center Back".
        stints = [self._stint("Center Back", "00:00", None)]
        result = compute_minutes_per_raw_position(stints, match_duration=90.0)
        assert "Center Back" in result
        assert "CB" not in result

    def test_two_positions_two_keys(self):
        # Tactical shift: two different StatsBomb positions must produce two keys.
        stints = [
            self._stint("Defensive Midfield", "00:00", "60:00"),
            self._stint("Center Midfield", "60:00", None),
        ]
        result = compute_minutes_per_raw_position(stints, match_duration=90.0)
        assert "Defensive Midfield" in result
        assert "Center Midfield" in result
        assert result["Defensive Midfield"] == pytest.approx(60.0)
        assert result["Center Midfield"] == pytest.approx(30.0)

    def test_two_stints_same_position_merged(self):
        # Same position in non-contiguous stints must be accumulated into one key.
        stints = [
            self._stint("Left Back", "00:00", "45:00"),
            self._stint("Right Back", "45:00", "75:00"),
            self._stint("Left Back", "75:00", None),
        ]
        result = compute_minutes_per_raw_position(stints, match_duration=90.0)
        assert result["Left Back"] == pytest.approx(45.0 + 15.0)
        assert result["Right Back"] == pytest.approx(30.0)

    def test_dismissal_clamping_applied(self):
        # Same dismissal clamping as compute_minutes_per_group must be applied.
        stints = [self._stint("Center Back", "00:00", None)]
        result = compute_minutes_per_raw_position(
            stints, match_duration=90.0, dismissal_minute=30.0
        )
        assert result == {"Center Back": pytest.approx(30.0)}


@pytest.mark.integration
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
