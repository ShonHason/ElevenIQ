"""Tests for src/metrics/shots.py"""
import pytest
from eleveniq.metrics.shots import (
    is_shot_on_target,
    SHOTS_ON_TARGET_OUTCOMES,
    KNOWN_SHOT_OUTCOMES,
)


class TestIsOnTarget:
    def test_goal_is_on_target(self):
        assert is_shot_on_target("Goal") is True

    def test_saved_is_on_target(self):
        assert is_shot_on_target("Saved") is True

    def test_saved_to_post_is_on_target(self):
        assert is_shot_on_target("Saved To Post") is True

    def test_blocked_is_not_on_target(self):
        # Blocked by a defender — never reached the goalkeeper
        assert is_shot_on_target("Blocked") is False

    def test_post_is_not_on_target(self):
        assert is_shot_on_target("Post") is False

    def test_off_target_is_not_on_target(self):
        assert is_shot_on_target("Off T") is False

    def test_wayward_is_not_on_target(self):
        assert is_shot_on_target("Wayward") is False

    def test_no_touch_is_not_on_target(self):
        assert is_shot_on_target("No Touch") is False

    def test_none_is_not_on_target(self):
        assert is_shot_on_target(None) is False

    def test_unknown_string_is_not_on_target(self):
        assert is_shot_on_target("Some Future Outcome") is False


class TestOutcomeSets:
    def test_on_target_outcomes_are_subset_of_known(self):
        assert SHOTS_ON_TARGET_OUTCOMES.issubset(KNOWN_SHOT_OUTCOMES)

    def test_on_target_set_size(self):
        assert len(SHOTS_ON_TARGET_OUTCOMES) == 3

    def test_known_outcomes_covers_all_expected(self):
        expected = {"Goal", "Saved", "Saved To Post", "Blocked", "Post", "Off T", "Wayward", "No Touch"}
        assert expected == KNOWN_SHOT_OUTCOMES
