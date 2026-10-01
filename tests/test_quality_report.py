"""Tests for eleveniq/pipeline/quality_report.py — red card / dismissal checks."""
from pathlib import Path
import pytest
from eleveniq.config import COMPETITION_SEASONS
from eleveniq.pipeline.quality_report import (
    find_dismissal_minute,
    find_substitutions_off,
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


def _foul_committed_event(player_id: int, card_name: str, minute: int, second: int = 0) -> dict:
    return {
        "type": {"name": "Foul Committed"},
        "player": {"id": player_id},
        "foul_committed": {"card": {"name": card_name}},
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

    # --- Foul Committed source ---

    def test_foul_red_card_detected(self):
        events = [_foul_committed_event(player_id=9, card_name="Red Card", minute=39, second=32)]
        result = find_dismissal_minute(events, 9)
        assert result == pytest.approx(39 + 32 / 60)

    def test_foul_second_yellow_detected(self):
        events = [_foul_committed_event(player_id=7, card_name="Second Yellow", minute=57, second=18)]
        result = find_dismissal_minute(events, 7)
        assert result == pytest.approx(57 + 18 / 60)

    def test_foul_yellow_card_not_a_dismissal(self):
        events = [_foul_committed_event(player_id=3, card_name="Yellow Card", minute=25)]
        assert find_dismissal_minute(events, 3) is None

    def test_foul_different_player_not_matched(self):
        events = [_foul_committed_event(player_id=99, card_name="Red Card", minute=20)]
        assert find_dismissal_minute(events, 42) is None

    def test_foul_missing_minute_returns_none(self):
        events = [{
            "type": {"name": "Foul Committed"},
            "player": {"id": 5},
            "foul_committed": {"card": {"name": "Red Card"}},
            # minute deliberately absent
        }]
        assert find_dismissal_minute(events, 5) is None

    def test_foul_missing_second_returns_none(self):
        events = [{
            "type": {"name": "Foul Committed"},
            "player": {"id": 5},
            "foul_committed": {"card": {"name": "Second Yellow"}},
            "minute": 57,
            # second deliberately absent
        }]
        assert find_dismissal_minute(events, 5) is None

    def test_foul_card_before_bad_behaviour_returned_first(self):
        # Foul Committed at 30 min (first in event list), Bad Behaviour at 50 min
        events = [
            _foul_committed_event(player_id=10, card_name="Red Card", minute=30),
            _bad_behaviour_event(player_id=10, card_name="Second Yellow", minute=50),
        ]
        assert find_dismissal_minute(events, 10) == pytest.approx(30.0)

    def test_bad_behaviour_before_foul_returned_first(self):
        events = [
            _bad_behaviour_event(player_id=10, card_name="Red Card", minute=30),
            _foul_committed_event(player_id=10, card_name="Second Yellow", minute=50),
        ]
        assert find_dismissal_minute(events, 10) == pytest.approx(30.0)

    def test_foul_no_card_field_not_a_dismissal(self):
        # Foul Committed event with no card key at all
        events = [{"type": {"name": "Foul Committed"}, "player": {"id": 5}, "minute": 30, "second": 0,
                   "foul_committed": {}}]
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

    def test_foul_committed_missing_minute_detected(self):
        events = [{
            "type": {"name": "Foul Committed"},
            "player": {"id": 7},
            "foul_committed": {"card": {"name": "Red Card"}},
            # minute and second absent
        }]
        result = check_dismissal_event_quality(events, 7)
        assert result is not None
        assert result["issue"] == "dismissal_missing_timestamp"
        assert result["card_name"] == "Red Card"
        assert "minute" in result["missing_fields"]

    def test_foul_committed_missing_second_only_detected(self):
        # Only `second` absent (minute present) — must still report the issue.
        # ADR 002 §3: both minute and second are required to form the decimal time.
        events = [{
            "type": {"name": "Foul Committed"},
            "player": {"id": 11},
            "foul_committed": {"card": {"name": "Second Yellow"}},
            "minute": 63,
            # second deliberately absent
        }]
        result = check_dismissal_event_quality(events, 11)
        assert result is not None
        assert result["issue"] == "dismissal_missing_timestamp"
        assert result["card_name"] == "Second Yellow"
        assert "second" in result["missing_fields"]
        assert "minute" not in result["missing_fields"]

    def test_foul_committed_complete_event_returns_none(self):
        # A well-formed Foul Committed dismissal event must NOT trigger a quality issue.
        events = [_foul_committed_event(player_id=14, card_name="Red Card", minute=22, second=10)]
        assert check_dismissal_event_quality(events, 14) is None

    def test_bad_behaviour_no_card_field_returns_none(self):
        # Bad Behaviour event with no card key — not a dismissal, no quality issue.
        events = [{"type": {"name": "Bad Behaviour"}, "player": {"id": 3}, "minute": 20, "second": 0,
                   "bad_behaviour": {}}]
        assert check_dismissal_event_quality(events, 3) is None


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

    def test_foul_committed_source_triggers_mismatch(self):
        # ADR 002 §3: Foul Committed is the dominant dismissal source (83 % of La Liga
        # 2015/16 dismissals).  check_dismissed_player_minutes must detect the mismatch
        # when the card comes from a Foul Committed event, not just Bad Behaviour.
        events = [_foul_committed_event(player_id=22, card_name="Red Card", minute=35)]
        result = check_dismissed_player_minutes(
            player_id=22,
            player_name="Foul Player",
            match_id=777,
            computed_minutes=92.0,
            events=events,
        )
        assert result is not None
        assert result["dismissal_minute"] == pytest.approx(35.0)
        assert result["excess_minutes"] == pytest.approx(92.0 - 35.0)

    def test_foul_committed_within_tolerance_not_a_mismatch(self):
        # A Foul Committed dismissal where the pipeline already credited the right
        # minutes must not be reported.
        events = [_foul_committed_event(player_id=33, card_name="Second Yellow", minute=78)]
        result = check_dismissed_player_minutes(
            player_id=33,
            player_name="Clean Data Player",
            match_id=888,
            computed_minutes=78.0,
            events=events,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Integration test: no dismissed player has minutes above card minute + tolerance
# ---------------------------------------------------------------------------

_PLAYER_MATCH_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output",
)
@pytest.mark.parametrize(
    "cs", COMPETITION_SEASONS, ids=[cs.label for cs in COMPETITION_SEASONS]
)
def test_no_dismissed_player_minutes_above_card_minute(cs):
    """
    For every dismissed player in the parquet, minutes_played must not exceed
    their card minute by more than DISMISSAL_TOLERANCE_MINUTES.

    Loads raw event files to reconstruct dismissal minutes, then cross-checks
    against the pipeline's computed minutes_played column.
    """
    import polars as pl
    from eleveniq.statsbomb.loader import load_events, load_matches
    from eleveniq.pipeline.quality_report import DISMISSAL_TOLERANCE_MINUTES

    df = pl.read_parquet(_PLAYER_MATCH_PARQUET).filter(
        (pl.col("competition_id") == cs.competition_id)
        & (pl.col("season_id") == cs.season_id)
    )
    matches = load_matches(cs.competition_id, cs.season_id)

    violations = []
    for m in matches:
        mid = m["match_id"]
        events = load_events(mid)
        for row in df.filter(pl.col("match_id") == mid).iter_rows(named=True):
            dm = find_dismissal_minute(events, row["player_id"])
            if dm is None:
                continue
            if row["minutes_played"] > dm + DISMISSAL_TOLERANCE_MINUTES:
                violations.append(
                    f"match={mid} player={row['player_name']!r} "
                    f"minutes={row['minutes_played']:.2f} card_min={dm:.2f} "
                    f"excess={row['minutes_played'] - dm:.2f}"
                )

    assert violations == [], (
        f"{len(violations)} dismissed player(s) with overcounted minutes:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# find_substitutions_off (ADR 002 §3)
# ---------------------------------------------------------------------------

def _substitution_event(player_id: int, replacement_id: int, *, period: int,
                        minute: int, second: int = 0) -> dict:
    return {
        "type": {"name": "Substitution"},
        "player": {"id": player_id},
        "substitution": {"replacement": {"id": replacement_id}},
        "period": period,
        "minute": minute,
        "second": second,
    }


class TestFindSubstitutionsOff:
    def test_only_the_player_leaving_matches_not_the_replacement(self):
        events = [_substitution_event(10, 20, period=2, minute=60, second=30)]
        assert find_substitutions_off(events, 10) == [(2, pytest.approx(60.5))]
        assert find_substitutions_off(events, 20) == []

    def test_other_event_types_and_playerless_events_ignored(self):
        events = [
            {"type": {"name": "Half End"}, "period": 1, "minute": 45, "second": 0},
            {**_other_event(10, "Player Off"), "period": 1},
            {**_other_event(10, "Pass"), "period": 1},
        ]
        assert find_substitutions_off(events, 10) == []

    def test_all_substitutions_returned_in_event_order(self):
        # More than one entry is the caller's data-quality failure; the finder must
        # return all of them, not just the first.
        events = [_substitution_event(10, 20, period=1, minute=30),
                  _substitution_event(10, 21, period=2, minute=70)]
        assert find_substitutions_off(events, 10) == [(1, 30.0), (2, 70.0)]
