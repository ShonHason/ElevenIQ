"""Tests for eleveniq/metrics/progressive.py (ElevenIQ definitions)."""
import math
import pytest
from eleveniq.metrics.progressive import (
    is_progressive_pass,
    is_progressive_carry,
    OPPONENT_GOAL,
    DEFENSIVE_40_THRESHOLD_X,
    OPP_PENALTY_AREA_X,
    OPP_PENALTY_AREA_Y_MIN,
    OPP_PENALTY_AREA_Y_MAX,
)


def _dist(x: float, y: float) -> float:
    return math.hypot(x - OPPONENT_GOAL[0], y - OPPONENT_GOAL[1])


# ---------------------------------------------------------------------------
# Progressive pass
# ---------------------------------------------------------------------------

class TestIsProgressivePass:
    # --- defensive-40% exclusion (start_x < 48) ----------------------------

    def test_defensive_40_excluded_start_near_own_goal(self):
        # start x=5 (own box) → excluded regardless of distance reduction
        assert is_progressive_pass((5.0, 40.0), (30.0, 40.0), outcome=None) is False

    def test_defensive_40_excluded_at_x47(self):
        # x=47 is inside the defensive 40% — excluded
        assert is_progressive_pass((47.0, 40.0), (70.0, 40.0), outcome=None) is False

    def test_defensive_40_boundary_x48_not_excluded(self):
        # x=48 is exactly the boundary — NOT in defensive 40%, obeys other conditions
        # (48,40) → (60,40): Δ = 72 - 60 = 12 yards ≥ 10 → True
        start = (48.0, 40.0)
        end = (60.0, 40.0)
        assert (_dist(*start) - _dist(*end)) >= 10.0
        assert is_progressive_pass(start, end, outcome=None) is True

    def test_defensive_40_excluded_diagonal_start(self):
        # (40, 0) used in old test; now correctly excluded because x=40 < 48
        assert is_progressive_pass((40.0, 0.0), (70.0, 20.0), outcome=None) is False

    # --- 10-yard distance-reduction rule (starts outside defensive 40%) -----

    def test_clearly_progressive_distance_rule(self):
        # (60,40) → (90,40): Δ=30 yards
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome=None) is True

    def test_exactly_10_yards_distance(self):
        # (50,40) → (60,40): Δ=10 yards exactly — boundary counts
        start = (50.0, 40.0)
        end = (60.0, 40.0)
        assert (_dist(*start) - _dist(*end)) == pytest.approx(10.0)
        assert is_progressive_pass(start, end, outcome=None) is True

    def test_just_under_10_yards_not_in_penalty_area(self):
        # (50,40) → (59,40): Δ≈9 yards, end not in penalty area → False
        start = (50.0, 40.0)
        end = (59.0, 40.0)
        assert (_dist(*start) - _dist(*end)) < 10.0
        assert end[0] < OPP_PENALTY_AREA_X
        assert is_progressive_pass(start, end, outcome=None) is False

    def test_backward_pass(self):
        assert is_progressive_pass((80.0, 40.0), (60.0, 40.0), outcome=None) is False

    def test_lateral_pass_no_distance_gain(self):
        # pure lateral: same distance from goal
        assert is_progressive_pass((60.0, 40.0), (60.0, 50.0), outcome=None) is False

    def test_custom_threshold(self):
        start = (50.0, 40.0)
        end = (60.0, 40.0)
        # Δ=10: passes threshold=10, fails threshold=15
        assert is_progressive_pass(start, end, outcome=None, threshold=10.0) is True
        assert is_progressive_pass(start, end, outcome=None, threshold=15.0) is False

    # --- penalty-area OR clause ---------------------------------------------

    def test_short_pass_ending_in_penalty_area_counts(self):
        # (100,40) → (103,40): Δ=20-17=3 yards (< 10), end at x=103 ≥ 102
        # start is outside box (x=100 < 102) → entered_box=True → True
        start = (100.0, 40.0)
        end = (103.0, 40.0)
        assert (_dist(*start) - _dist(*end)) < 10.0
        assert end[0] >= OPP_PENALTY_AREA_X
        assert is_progressive_pass(start, end, outcome=None) is True

    def test_pass_within_box_uses_distance_rule_only(self):
        # (110,40) → (115,40): start AND end inside box → OR clause disabled.
        # dist_reduction = 10-5 = 5 (< 10) → False
        assert is_progressive_pass((110.0, 40.0), (115.0, 40.0), outcome=None) is False

    def test_pass_entering_box_from_outside_counts(self):
        # (100,40) → (103,40): start x=100 outside box, end x=103 inside box
        # dist_reduction = 20-17 = 3 (< 10) but crosses into box → True
        assert is_progressive_pass((100.0, 40.0), (103.0, 40.0), outcome=None) is True

    def test_pass_ending_just_outside_penalty_area_x_axis(self):
        # (80,40) → (88,40): end x=88 < 102 (not in penalty area), Δ=8 (< 10) → False
        start = (80.0, 40.0)
        end = (88.0, 40.0)
        assert end[0] < OPP_PENALTY_AREA_X
        assert (_dist(*start) - _dist(*end)) < 10.0
        assert is_progressive_pass(start, end, outcome=None) is False

    def test_pass_ending_at_penalty_area_x_boundary(self):
        # end exactly at x=102: inside penalty area → True regardless of distance reduction
        start = (90.0, 40.0)
        end = (102.0, 40.0)
        assert end[0] == OPP_PENALTY_AREA_X
        assert is_progressive_pass(start, end, outcome=None) is True

    def test_pass_ending_outside_penalty_area_y_axis(self):
        # (90, 40) → (105, 17): x=105 ≥ 102 but y=17 < 18 (just outside box on y) → only distance rule
        start = (90.0, 40.0)
        end = (105.0, 17.0)
        assert end[0] >= OPP_PENALTY_AREA_X
        assert end[1] < OPP_PENALTY_AREA_Y_MIN
        # distance reduction: dist(90,40)=30, dist(105,17)≈sqrt(225+529)≈27.5, Δ≈2.5 < 10 → False
        assert is_progressive_pass(start, end, outcome=None) is False

    def test_penalty_area_corner_counts(self):
        # pass to corner of penalty area at (102, 18) — exactly on boundary → True
        start = (80.0, 40.0)
        end = (102.0, 18.0)
        assert end[0] == OPP_PENALTY_AREA_X
        assert end[1] == OPP_PENALTY_AREA_Y_MIN
        assert is_progressive_pass(start, end, outcome=None) is True

    def test_pass_from_defensive_40_to_penalty_area_excluded(self):
        # start x=40 < 48 (defensive 40%) — excluded even though it ends in penalty area
        assert is_progressive_pass((40.0, 40.0), (110.0, 40.0), outcome=None) is False

    # --- completion guard (outcome parameter) -------------------------------

    def test_incomplete_pass_never_counts(self):
        # Geometrically progressive but outcome is set → must return False
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome="Incomplete") is False

    def test_out_pass_never_counts(self):
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome="Out") is False

    def test_unknown_outcome_never_counts(self):
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome="Unknown") is False

    def test_pass_offside_never_counts(self):
        # Passes flagged offside are not completed
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome="Pass Offside") is False

    def test_none_outcome_means_completed(self):
        # outcome=None means completed pass — geometry applies
        assert is_progressive_pass((60.0, 40.0), (90.0, 40.0), outcome=None) is True

    def test_box_pass_with_incomplete_outcome_excluded(self):
        # Ends in penalty area but outcome set → not progressive
        assert is_progressive_pass((100.0, 40.0), (110.0, 40.0), outcome="Incomplete") is False

    def test_outcome_is_required_kwarg(self):
        # outcome has no default — calling without it is a TypeError
        with pytest.raises(TypeError):
            is_progressive_pass((60.0, 40.0), (90.0, 40.0))


# ---------------------------------------------------------------------------
# Progressive carry (definition unchanged — verified by tests below)
# ---------------------------------------------------------------------------

class TestIsProgressiveCarry:
    def test_clearly_progressive(self):
        assert is_progressive_carry((60.0, 40.0), (80.0, 40.0)) is True

    def test_exactly_5_yard_threshold(self):
        start = (50.0, 40.0)
        end = (55.0, 40.0)
        assert (_dist(*start) - _dist(*end)) == pytest.approx(5.0)
        assert math.hypot(5.0, 0.0) == pytest.approx(5.0)
        assert is_progressive_carry(start, end) is True

    def test_good_dist_reduction_but_carry_too_short(self):
        # (119,40) → (120,40): carry=1yd — minimum distance not met
        assert is_progressive_carry((119.0, 40.0), (120.0, 40.0)) is False

    def test_long_carry_but_lateral(self):
        # (60,40) → (60,20): 20-yard carry but moves AWAY from goal
        # dist(60,40)=60, dist(60,20)=sqrt(3600+400)≈63.25 → reduction ≈ -3.25 < 5 → False
        assert is_progressive_carry((60.0, 40.0), (60.0, 20.0)) is False

    def test_backward_carry(self):
        assert is_progressive_carry((80.0, 40.0), (60.0, 40.0)) is False

    def test_carry_from_own_half_is_not_excluded(self):
        # Carries have no start-zone exclusion — deep carry in own half counts
        assert is_progressive_carry((30.0, 40.0), (50.0, 40.0)) is True

    def test_custom_threshold(self):
        start = (60.0, 40.0)
        end = (66.0, 40.0)
        assert is_progressive_carry(start, end, threshold=5.0) is True
        assert is_progressive_carry(start, end, threshold=10.0) is False

    def test_custom_min_distance(self):
        start = (60.0, 40.0)
        end = (66.0, 40.0)
        assert is_progressive_carry(start, end, min_distance=5.0) is True
        assert is_progressive_carry(start, end, min_distance=7.0) is False
