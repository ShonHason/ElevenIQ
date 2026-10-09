"""analytics/roles.py (plan 006 W1): fine roles and DM-CM slots, from the written rules."""
from __future__ import annotations

import pytest

from eleveniq.analytics import roles as RO


def test_a_role_with_at_least_the_share_holds_the_row():
    r = RO.fine_role({"Left Back": 70.0, "Left Wing Back": 30.0})
    assert (r.label, r.combo) == ("LB", None)
    assert r.top == (("LB", 0.7), ("LWB", pytest.approx(0.3)))


def test_mixed_names_the_two_largest_roles_in_role_order():
    r = RO.fine_role({"Right Wing": 40.0, "Left Wing": 40.0, "Center Forward": 20.0})
    assert (r.label, r.combo) == ("mixed", ("LW", "RW"))     # tie → ROLES order


def test_positions_of_one_role_add_up_and_goalkeeper_minutes_are_dropped():
    r = RO.fine_role({"Center Back": 30.0, "Left Center Back": 50.0, "Goalkeeper": 900.0,
                      "Right Back": 20.0})
    assert (r.label, r.top[0]) == ("CB", ("CB", 0.8))


def test_unmapped_position_and_no_minutes_raise():
    with pytest.raises(ValueError, match="unmapped"):
        RO.fine_role({"Sweeper": 90.0})
    with pytest.raises(ValueError, match="no outfield"):
        RO.fine_role({"Goalkeeper": 90.0})


@pytest.mark.parametrize("six, eight, label", [(70.0, 30.0, "6"), (30.0, 70.0, "8"),
                                               (50.0, 50.0, "mixed")])
def test_dmcm_six_eight_label(six, eight, label):
    s = RO.dmcm_slot({"Center Defensive Midfield": six, "Center Midfield": eight,
                      "Left Back": 500.0})                    # other groups ignored
    assert s.label == label and s.share6 == pytest.approx(six / 100)


def test_dmcm_pivot():
    assert RO.dmcm_slot({"Left Defensive Midfield": 40.0, "Right Defensive Midfield": 40.0,
                         "Center Midfield": 20.0}).pivot == "double"
    assert RO.dmcm_slot({"Center Defensive Midfield": 60.0, "Center Midfield": 40.0}).pivot == "mixed"


def test_dmcm_rejects_an_undefined_slot():
    with pytest.raises(ValueError, match="outside the 6 / 8"):
        RO.dmcm_slot({"Defensive Midfield": 90.0})


def test_dmcm_single_pivot_and_all_eight_minutes():
    s = RO.dmcm_slot({"Center Defensive Midfield": 80.0, "Left Center Midfield": 20.0})
    assert (s.label, s.pivot) == ("6", "single")
    e = RO.dmcm_slot({"Left Center Midfield": 45.5, "Right Center Midfield": 45.5})
    assert (e.label, e.pivot, e.share6) == ("8", "cm", 0.0)


def test_dmcm_without_dmcm_minutes_raises():
    with pytest.raises(ValueError, match="no DM-CM"):
        RO.dmcm_slot({"Left Back": 90.0})


def test_role_share_is_not_lost_to_float_sums():
    # 0.7 of 3 * 0.1-style fractional minutes must still reach the share
    r = RO.fine_role({"Left Back": 0.1 + 0.2 + 0.4, "Left Wing Back": 0.3})
    assert r.label == "LB"
