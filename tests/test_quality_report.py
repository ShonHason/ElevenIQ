"""Tests for eleveniq/pipeline/quality_report.py — red card / dismissal checks."""
import pytest
from eleveniq.pipeline.quality_report import (
    find_dismissal_minute,
    check_dismissal_event_quality,
    check_dismissed_player_minutes,
    DISMISSAL_CARDS,
    DISMISSAL_TOLERANCE_MINUTES,
)


def _bad_behaviour_event(player_id: int, card_name: str, minute: int, second: int = 0) -> dict:
    return {
        "type": {"name": "Bad Behaviour"},
        "player": {"id": player_id},
        "bad_behaviour": {"card": {"name": card_name}},
        "minute": minute,
        "second": second,
    }


def _other_event(player_id: int, event_type: str = "Pass") -> dict:
    return {
        "type": {"name": event_type},
        "player": {"id": player_id},
        "minute": 30,
        "second": 0,
    }


# ---------------------------------------------------------------------------
# find_dismissal_minute
# ---------------------------------------------------------------------------

class TestFindDismissalMinute:
    def test_red_card_found(self):
        events = [_bad_behaviour_event(player_id=42, card_name="Red Card", minute=15, second=30)]
        result = find_dismissal_minute(events, 42)
        assert result == pytest.approx(15 + 30 / 60)

    def test_second_yellow_found(self):
        events = [_bad_behaviour_event(player_id=7, card_name="Second Yellow", minute=72, second=0)]
        result = find_dismissal_minute(events, 7)
        assert result == pytest.approx(72.0)

    def test_yellow_card_not_a_dismissal(self):
        events = [_bad_behaviour_event(player_id=5, card_name="Yellow Card", minute=30)]
        assert find_dismissal_minute(events, 5) is None

    def test_different_player_not_matched(self):
        events = [_bad_behaviour_event(player_id=99, card_name="Red Card", minute=20)]
        assert find_dismissal_minute(events, 42) is None

    def test_no_bad_behaviour_events(self):
        events = [_other_event(player_id=1), _other_event(player_id=2)]
        assert find_dismissal_minute(events, 1) is None

    def test_empty_event_list(self):
        assert find_dismissal_minute([], 1) is None

    def test_returns_first_dismissal_only(self):
        # Should not happen in real data, but handle gracefully
        events = [
            _bad_behaviour_event(player_id=10, card_name="Red Card", minute=15),
            _bad_behaviour_event(player_id=10, card_name="Second Yellow", minute=20),
        ]
        result = find_dismissal_minute(events, 10)
        assert result == pytest.approx(15.0)

    def test_dismissal_cards_set(self):
        assert "Red Card" in DISMISSAL_CARDS
        assert "Second Yellow" in DISMISSAL_CARDS
        assert "Yellow Card" not in DISMISSAL_CARDS

    def test_missing_minute_returns_none(self):
        # A dismissal event with no `minute` field must return None, not default to 0.
        # Defaulting to 0 would silently create a fake dismissal at match start.
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 5},
            "bad_behaviour": {"card": {"name": "Red Card"}},
            # minute and second deliberately absent
        }]
        assert find_dismissal_minute(events, 5) is None

    def test_missing_second_returns_none(self):
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 5},
            "bad_behaviour": {"card": {"name": "Red Card"}},
            "minute": 33,
            # second deliberately absent
        }]
        assert find_dismissal_minute(events, 5) is None


# ---------------------------------------------------------------------------
# check_dismissal_event_quality
# ---------------------------------------------------------------------------

class TestCheckDismissalEventQuality:
    def test_missing_minute_detected(self):
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 5},
            "bad_behaviour": {"card": {"name": "Red Card"}},
        }]
        result = check_dismissal_event_quality(events, 5)
        assert result is not None
        assert result["issue"] == "dismissal_missing_timestamp"
        assert result["player_id"] == 5
        assert result["card_name"] == "Red Card"
        assert "minute" in result["missing_fields"]

    def test_missing_second_detected(self):
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 7},
            "bad_behaviour": {"card": {"name": "Second Yellow"}},
            "minute": 33,
        }]
        result = check_dismissal_event_quality(events, 7)
        assert result is not None
        assert "second" in result["missing_fields"]
        assert "minute" not in result["missing_fields"]

    def test_complete_event_returns_none(self):
        events = [_bad_behaviour_event(player_id=5, card_name="Red Card", minute=15)]
        assert check_dismissal_event_quality(events, 5) is None

    def test_no_dismissal_event_returns_none(self):
        events = [_other_event(player_id=5)]
        assert check_dismissal_event_quality(events, 5) is None

    def test_different_player_returns_none(self):
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 99},
            "bad_behaviour": {"card": {"name": "Red Card"}},
        }]
        assert check_dismissal_event_quality(events, 5) is None


# ---------------------------------------------------------------------------
# check_dismissed_player_minutes
# ---------------------------------------------------------------------------

class TestCheckDismissedPlayerMinutes:
    def test_mismatch_detected_red_card(self):
        events = [_bad_behaviour_event(player_id=5, card_name="Red Card", minute=15)]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=92.0,
            events=events,
        )
        assert result is not None
        assert result["player_id"] == 5
        assert result["match_id"] == 12345
        assert result["dismissal_minute"] == pytest.approx(15.0)
        assert result["computed_minutes"] == pytest.approx(92.0)
        assert result["excess_minutes"] == pytest.approx(77.0)

    def test_no_mismatch_when_statsbomb_set_to_correctly(self):
        # StatsBomb set `to` correctly: computed_minutes matches dismissal_minute
        events = [_bad_behaviour_event(player_id=5, card_name="Red Card", minute=15)]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=15.0,
            events=events,
        )
        assert result is None

    def test_within_tolerance_is_not_a_mismatch(self):
        events = [_bad_behaviour_event(player_id=5, card_name="Red Card", minute=15)]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=15.0 + DISMISSAL_TOLERANCE_MINUTES,
            events=events,
        )
        assert result is None

    def test_just_over_tolerance_is_a_mismatch(self):
        events = [_bad_behaviour_event(player_id=5, card_name="Red Card", minute=15)]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=15.0 + DISMISSAL_TOLERANCE_MINUTES + 0.01,
            events=events,
        )
        assert result is not None

    def test_no_card_event_returns_none(self):
        events = [_other_event(player_id=5)]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=90.0,
            events=events,
        )
        assert result is None

    def test_second_yellow_mismatch_detected(self):
        events = [_bad_behaviour_event(player_id=8, card_name="Second Yellow", minute=72)]
        result = check_dismissed_player_minutes(
            player_id=8,
            player_name="Test Player",
            match_id=99,
            computed_minutes=92.0,
            events=events,
        )
        assert result is not None
        assert result["dismissal_minute"] == pytest.approx(72.0)

    def test_mismatch_report_has_all_keys(self):
        events = [_bad_behaviour_event(player_id=1, card_name="Red Card", minute=10)]
        result = check_dismissed_player_minutes(
            player_id=1,
            player_name="Player One",
            match_id=555,
            computed_minutes=90.0,
            events=events,
        )
        assert result is not None
        assert set(result.keys()) == {
            "match_id", "player_id", "player_name",
            "computed_minutes", "dismissal_minute", "excess_minutes",
        }

    def test_missing_timestamp_event_returns_none(self):
        # find_dismissal_minute returns None for missing timestamp, so
        # check_dismissed_player_minutes must also return None (no false mismatch).
        events = [{
            "type": {"name": "Bad Behaviour"},
            "player": {"id": 5},
            "bad_behaviour": {"card": {"name": "Red Card"}},
            # minute and second absent
        }]
        result = check_dismissed_player_minutes(
            player_id=5,
            player_name="Test Player",
            match_id=12345,
            computed_minutes=92.0,
            events=events,
        )
        assert result is None
