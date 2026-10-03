"""Tests for eleveniq/metrics/entries.py."""
import pytest
from eleveniq.metrics.entries import (
    FINAL_THIRD_X,
    is_final_third_entry_pass,
    is_final_third_entry_carry,
    is_box_entry_pass,
    is_box_entry_carry,
)
from eleveniq.metrics.pass_types import RESTART_TYPES, SET_PIECE_TYPES


# ---------------------------------------------------------------------------
# SET_PIECE_TYPES contract
# ---------------------------------------------------------------------------

class TestSetPieceTypes:
    def test_contains_corner(self):
        assert "Corner" in SET_PIECE_TYPES

    def test_contains_free_kick(self):
        assert "Free Kick" in SET_PIECE_TYPES

    def test_contains_goal_kick(self):
        assert "Goal Kick" in SET_PIECE_TYPES

    def test_contains_kick_off(self):
        assert "Kick Off" in SET_PIECE_TYPES

    def test_throw_in_not_included(self):
        assert "Throw-in" not in SET_PIECE_TYPES

    def test_recovery_not_included(self):
        assert "Recovery" not in SET_PIECE_TYPES

    def test_interception_not_included(self):
        assert "Interception" not in SET_PIECE_TYPES


class TestRestartTypes:
    """ADR 004 D14: the restarts excluded from pass entries are the set pieces
    plus throw-ins; Recovery and Interception passes are open play."""

    def test_restarts_are_set_pieces_plus_throw_in(self):
        assert RESTART_TYPES == SET_PIECE_TYPES | {"Throw-in"}

    @pytest.mark.parametrize("ptype", ["Recovery", "Interception", None])
    def test_open_play_types_are_not_restarts(self, ptype):
        assert ptype not in RESTART_TYPES


# ---------------------------------------------------------------------------
# is_final_third_entry_pass
# ---------------------------------------------------------------------------

class TestIsFinalThirdEntryPass:
    # --- boundary -------------------------------------------------------

    def test_clearly_into_final_third(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name=None) is True

    def test_end_exactly_at_80_counts(self):
        assert is_final_third_entry_pass((79.0, 40.0), (80.0, 40.0),
                                         outcome=None, pass_type_name=None) is True

    def test_start_at_exactly_80_does_not_count(self):
        # start == FINAL_THIRD_X → already in final third, not an entry
        assert is_final_third_entry_pass((80.0, 40.0), (90.0, 40.0),
                                         outcome=None, pass_type_name=None) is False

    def test_both_in_final_third_not_an_entry(self):
        assert is_final_third_entry_pass((82.0, 40.0), (95.0, 40.0),
                                         outcome=None, pass_type_name=None) is False

    def test_backward_pass_not_an_entry(self):
        # passes backward out of the final third
        assert is_final_third_entry_pass((90.0, 40.0), (70.0, 40.0),
                                         outcome=None, pass_type_name=None) is False

    # --- set-piece exclusion --------------------------------------------

    def test_corner_excluded(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Corner") is False

    def test_free_kick_excluded(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Free Kick") is False

    def test_goal_kick_excluded(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Goal Kick") is False

    def test_kick_off_excluded(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Kick Off") is False

    def test_throw_in_excluded(self):
        """ADR 004 D14: a throw-in is a restart and never a final-third entry pass."""
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Throw-in") is False

    def test_recovery_included(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Recovery") is True

    def test_interception_included(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name="Interception") is True

    def test_none_type_open_play_included(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome=None, pass_type_name=None) is True

    # --- outcome contract -----------------------------------------------

    def test_incomplete_pass_not_an_entry(self):
        assert is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                         outcome="Incomplete", pass_type_name=None) is False

    def test_missing_outcome_raises_type_error(self):
        with pytest.raises(TypeError):
            is_final_third_entry_pass((60.0, 40.0), (85.0, 40.0),
                                      pass_type_name=None)


# ---------------------------------------------------------------------------
# is_final_third_entry_carry
# ---------------------------------------------------------------------------

class TestIsFinalThirdEntryCarry:
    def test_clearly_into_final_third(self):
        assert is_final_third_entry_carry((75.0, 40.0), (82.0, 40.0)) is True

    def test_end_exactly_at_80_counts(self):
        assert is_final_third_entry_carry((79.9, 40.0), (80.0, 40.0)) is True

    def test_start_at_80_does_not_count(self):
        assert is_final_third_entry_carry((80.0, 40.0), (90.0, 40.0)) is False

    def test_both_in_final_third_not_an_entry(self):
        assert is_final_third_entry_carry((81.0, 40.0), (95.0, 40.0)) is False

    def test_backward_carry_not_an_entry(self):
        assert is_final_third_entry_carry((85.0, 40.0), (75.0, 40.0)) is False

    def test_carry_at_various_y(self):
        # y-coordinate does not affect final-third entry
        assert is_final_third_entry_carry((70.0, 5.0), (81.0, 5.0)) is True
        assert is_final_third_entry_carry((70.0, 75.0), (81.0, 75.0)) is True


# ---------------------------------------------------------------------------
# is_box_entry_pass
# ---------------------------------------------------------------------------

class TestIsBoxEntryPass:
    # --- geometry -------------------------------------------------------

    def test_clearly_into_box(self):
        # (100, 40) → (105, 40): start outside, end inside
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name=None) is True

    def test_both_outside_not_an_entry(self):
        assert is_box_entry_pass((60.0, 40.0), (99.0, 40.0),
                                  outcome=None, pass_type_name=None) is False

    def test_start_inside_not_an_entry(self):
        # start already inside the box
        assert is_box_entry_pass((103.0, 40.0), (110.0, 40.0),
                                  outcome=None, pass_type_name=None) is False

    def test_end_outside_y_range_not_in_box(self):
        # end_y outside 18–62
        assert is_box_entry_pass((100.0, 40.0), (105.0, 70.0),
                                  outcome=None, pass_type_name=None) is False

    def test_end_at_x_boundary(self):
        # end x == 102 exactly → in box
        assert is_box_entry_pass((99.0, 40.0), (102.0, 40.0),
                                  outcome=None, pass_type_name=None) is True

    def test_end_at_y_boundary_min(self):
        # y == 18 → in box
        assert is_box_entry_pass((100.0, 40.0), (105.0, 18.0),
                                  outcome=None, pass_type_name=None) is True

    def test_end_at_y_boundary_max(self):
        # y == 62 → in box
        assert is_box_entry_pass((100.0, 40.0), (105.0, 62.0),
                                  outcome=None, pass_type_name=None) is True

    # --- set-piece exclusion --------------------------------------------

    def test_corner_excluded(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Corner") is False

    def test_free_kick_excluded(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Free Kick") is False

    def test_goal_kick_excluded(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Goal Kick") is False

    def test_kick_off_excluded(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Kick Off") is False

    def test_throw_in_excluded(self):
        """ADR 004 D14: a throw-in is a restart and never a box entry pass."""
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Throw-in") is False

    def test_recovery_included(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name="Recovery") is True

    def test_none_type_included(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome=None, pass_type_name=None) is True

    # --- outcome contract -----------------------------------------------

    def test_incomplete_pass_not_an_entry(self):
        assert is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                                  outcome="Incomplete", pass_type_name=None) is False

    def test_missing_outcome_raises_type_error(self):
        with pytest.raises(TypeError):
            is_box_entry_pass((100.0, 40.0), (105.0, 40.0),
                               pass_type_name=None)


# ---------------------------------------------------------------------------
# is_box_entry_carry
# ---------------------------------------------------------------------------

class TestIsBoxEntryCarry:
    def test_clearly_into_box(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 40.0)) is True

    def test_start_inside_not_an_entry(self):
        assert is_box_entry_carry((103.0, 40.0), (110.0, 40.0)) is False

    def test_both_outside_not_an_entry(self):
        assert is_box_entry_carry((90.0, 40.0), (100.0, 40.0)) is False

    def test_end_outside_y_range(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 70.0)) is False

    def test_end_at_x_boundary(self):
        assert is_box_entry_carry((100.0, 40.0), (102.0, 40.0)) is True

    def test_end_at_y_min_boundary(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 18.0)) is True

    def test_end_at_y_max_boundary(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 62.0)) is True

    def test_backward_carry_not_an_entry(self):
        assert is_box_entry_carry((110.0, 40.0), (100.0, 40.0)) is False


# ---------------------------------------------------------------------------
# Cross-function: start exactly on box boundary counts as already inside
# (shared boundary logic between is_box_entry_pass and is_box_entry_carry)
# ---------------------------------------------------------------------------

class TestBoxBoundaryStartOnEdge:
    """
    ADR 002 §27 uses in_opponent_penalty_area to determine whether the start
    position is inside the box.  x=102 is ON the boundary → inside → no entry.
    Both pass and carry must reject this case, not accept it.
    """

    def test_box_entry_pass_start_exactly_at_x102_not_an_entry(self):
        # start x == OPP_PENALTY_AREA_X (102) → in_opponent_penalty_area returns True
        # → not an entry (already inside the box)
        assert is_box_entry_pass((102.0, 40.0), (110.0, 40.0),
                                  outcome=None, pass_type_name=None) is False, (
            "Start at x=102 is ON the penalty-area boundary → already inside → not an entry"
        )

    def test_box_entry_carry_start_exactly_at_x102_not_an_entry(self):
        assert is_box_entry_carry((102.0, 40.0), (110.0, 40.0)) is False, (
            "Start at x=102 is ON the penalty-area boundary → already inside → not an entry"
        )

    def test_box_entry_pass_end_y_just_below_min_not_in_box(self):
        # end y = 17.0 < 18 → NOT in box → not an entry
        assert is_box_entry_pass((100.0, 40.0), (105.0, 17.0),
                                  outcome=None, pass_type_name=None) is False, (
            "end y=17 is below OPP_PENALTY_AREA_Y_MIN=18 → end not in box → no entry"
        )

    def test_box_entry_carry_end_y_just_below_min_not_in_box(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 17.0)) is False, (
            "end y=17 is below OPP_PENALTY_AREA_Y_MIN=18 → end not in box → no entry"
        )

    def test_box_entry_pass_end_y_just_above_max_not_in_box(self):
        # end y = 63.0 > 62 → NOT in box → not an entry
        assert is_box_entry_pass((100.0, 40.0), (105.0, 63.0),
                                  outcome=None, pass_type_name=None) is False, (
            "end y=63 is above OPP_PENALTY_AREA_Y_MAX=62 → end not in box → no entry"
        )

    def test_box_entry_carry_end_y_just_above_max_not_in_box(self):
        assert is_box_entry_carry((100.0, 40.0), (105.0, 63.0)) is False, (
            "end y=63 is above OPP_PENALTY_AREA_Y_MAX=62 → end not in box → no entry"
        )
