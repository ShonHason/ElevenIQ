"""
Unit tests for eleveniq.metrics.aerials — has_aerial_won() and is_aerial_lost().

Written from ADR 002 §20 (spec), not from reading the implementation.

Spec:
- aerial_won flag appears on the winner's own sub-event inside Pass, Clearance,
  Shot, or Miscontrol.  No other event type carries this flag.
- aerial_lost is signalled by a separate Duel event with duel.type.name == "Aerial Lost".
- has_aerial_won returns False for any event type outside the four recognised types,
  even if an aerial_won key exists in the sub-payload.
- is_aerial_lost returns False for any event that is not a Duel, and for Duel events
  whose type.name is not exactly "Aerial Lost".
"""
from __future__ import annotations

import pytest

from eleveniq.metrics.aerials import has_aerial_won, is_aerial_lost


# ---------------------------------------------------------------------------
# has_aerial_won
# ---------------------------------------------------------------------------

class TestHasAerialWon:
    """ADR 002 §20: aerial_won flag lives on winner's sub-event (Pass/Clearance/Shot/Miscontrol)."""

    # -- Positive cases (each of the four recognised event types) -------------

    def test_pass_with_aerial_won_true(self):
        event = {"type": {"name": "Pass"}, "pass": {"aerial_won": True}}
        assert has_aerial_won(event) is True

    def test_clearance_with_aerial_won_true(self):
        event = {"type": {"name": "Clearance"}, "clearance": {"aerial_won": True}}
        assert has_aerial_won(event) is True

    def test_shot_with_aerial_won_true(self):
        event = {"type": {"name": "Shot"}, "shot": {"aerial_won": True}}
        assert has_aerial_won(event) is True

    def test_miscontrol_with_aerial_won_true(self):
        event = {"type": {"name": "Miscontrol"}, "miscontrol": {"aerial_won": True}}
        assert has_aerial_won(event) is True

    # -- aerial_won=False must not count ------------------------------------

    def test_pass_aerial_won_false(self):
        event = {"type": {"name": "Pass"}, "pass": {"aerial_won": False}}
        assert has_aerial_won(event) is False

    def test_clearance_aerial_won_false(self):
        event = {"type": {"name": "Clearance"}, "clearance": {"aerial_won": False}}
        assert has_aerial_won(event) is False

    # -- Absent aerial_won key (most events) --------------------------------

    def test_pass_without_aerial_won_key(self):
        event = {"type": {"name": "Pass"}, "pass": {"length": 20.0}}
        assert has_aerial_won(event) is False

    def test_clearance_without_aerial_won_key(self):
        event = {"type": {"name": "Clearance"}, "clearance": {}}
        assert has_aerial_won(event) is False

    # -- Event types NOT in the recognised set must never count ---------------
    # ADR 002 §20: only Pass, Clearance, Shot, Miscontrol carry the flag.
    # A Carry or Dribble event with aerial_won=True in its sub-dict must
    # be silently ignored — it represents noise / future schema change.

    def test_carry_event_with_aerial_won_true_not_counted(self):
        """Carry is not a recognised aerial-won event type (ADR §20)."""
        event = {"type": {"name": "Carry"}, "carry": {"aerial_won": True}}
        assert has_aerial_won(event) is False

    def test_dribble_event_with_aerial_won_true_not_counted(self):
        event = {"type": {"name": "Dribble"}, "dribble": {"aerial_won": True}}
        assert has_aerial_won(event) is False

    def test_duel_aerial_lost_event_not_counted_as_aerial_won(self):
        """A Duel/Aerial Lost event carries the loss, not the win."""
        event = {"type": {"name": "Duel"}, "duel": {"type": {"name": "Aerial Lost"}}}
        assert has_aerial_won(event) is False

    # -- Malformed / missing fields (data-quality robustness) ---------------

    def test_empty_event(self):
        assert has_aerial_won({}) is False

    def test_event_with_no_type_key(self):
        assert has_aerial_won({"pass": {"aerial_won": True}}) is False

    def test_event_with_null_sub_payload(self):
        """event.pass = None — guard (event.get("pass") or {}) must not raise."""
        event = {"type": {"name": "Pass"}, "pass": None}
        assert has_aerial_won(event) is False

    def test_event_with_null_type_field(self):
        """event.type = None — must return False without raising."""
        event = {"type": None, "pass": {"aerial_won": True}}
        assert has_aerial_won(event) is False


# ---------------------------------------------------------------------------
# is_aerial_lost
# ---------------------------------------------------------------------------

class TestIsAerialLost:
    """ADR 002 §20: aerial_lost is a Duel event with duel.type.name == 'Aerial Lost'."""

    def test_duel_aerial_lost_returns_true(self):
        event = {"type": {"name": "Duel"}, "duel": {"type": {"name": "Aerial Lost"}}}
        assert is_aerial_lost(event) is True

    def test_duel_tackle_returns_false(self):
        """Tackle duel must not be treated as aerial lost."""
        event = {"type": {"name": "Duel"}, "duel": {"type": {"name": "Tackle"}}}
        assert is_aerial_lost(event) is False

    def test_duel_ground_duel_returns_false(self):
        event = {"type": {"name": "Duel"}, "duel": {"type": {"name": "Ground Duel"}}}
        assert is_aerial_lost(event) is False

    def test_non_duel_event_returns_false(self):
        """A Pass event is never an aerial lost, even if the pass sub-dict says so."""
        event = {"type": {"name": "Pass"}, "pass": {"aerial_won": True}}
        assert is_aerial_lost(event) is False

    def test_clearance_event_returns_false(self):
        event = {"type": {"name": "Clearance"}, "clearance": {"aerial_won": True}}
        assert is_aerial_lost(event) is False

    def test_empty_event_returns_false(self):
        assert is_aerial_lost({}) is False

    def test_duel_with_no_duel_sub_dict_returns_false(self):
        """Duel event missing the 'duel' sub-dict must not raise."""
        event = {"type": {"name": "Duel"}}
        assert is_aerial_lost(event) is False

    def test_duel_with_null_duel_sub_dict_returns_false(self):
        event = {"type": {"name": "Duel"}, "duel": None}
        assert is_aerial_lost(event) is False

    def test_duel_with_missing_type_name_returns_false(self):
        """duel.type is absent — no crash, returns False."""
        event = {"type": {"name": "Duel"}, "duel": {"outcome": {"name": "Lost"}}}
        assert is_aerial_lost(event) is False
