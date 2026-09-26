"""Tests for src/metrics/tackles.py"""
import pytest
from eleveniq.metrics.tackles import (
    is_tackle_won,
    TACKLE_WON_OUTCOMES,
    TACKLE_LOST_OUTCOMES,
    KNOWN_TACKLE_OUTCOMES,
    KNOWN_DUEL_TYPES,
)


class TestIsTackleWon:
    def test_won_is_true(self):
        assert is_tackle_won("Won") is True

    def test_success_in_play_is_true(self):
        assert is_tackle_won("Success In Play") is True

    def test_success_out_is_true(self):
        assert is_tackle_won("Success Out") is True

    def test_lost_in_play_is_false(self):
        assert is_tackle_won("Lost In Play") is False

    def test_lost_out_is_false(self):
        assert is_tackle_won("Lost Out") is False

    def test_none_is_false(self):
        # Aerial duels have no outcome key — should not crash or return True
        assert is_tackle_won(None) is False

    def test_unknown_outcome_is_false(self):
        assert is_tackle_won("Some New Outcome") is False

    def test_empty_string_is_false(self):
        assert is_tackle_won("") is False


class TestOutcomeSets:
    def test_won_and_lost_are_disjoint(self):
        assert TACKLE_WON_OUTCOMES.isdisjoint(TACKLE_LOST_OUTCOMES)

    def test_known_outcomes_is_union(self):
        assert KNOWN_TACKLE_OUTCOMES == TACKLE_WON_OUTCOMES | TACKLE_LOST_OUTCOMES

    def test_known_duel_types_includes_tackle_and_aerial(self):
        assert "Tackle" in KNOWN_DUEL_TYPES
        assert "Aerial Lost" in KNOWN_DUEL_TYPES

    def test_aerial_lost_not_a_tackle_outcome(self):
        # Aerial duels are in KNOWN_DUEL_TYPES but their outcomes are NOT in tackle sets
        # (they don't have a duel.outcome field in StatsBomb data)
        assert "Aerial Lost" not in KNOWN_TACKLE_OUTCOMES
