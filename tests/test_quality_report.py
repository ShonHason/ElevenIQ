"""Tests for eleveniq/pipeline/quality_report.py — red card / dismissal checks."""
from pathlib import Path
import pytest
from eleveniq.config import COMPETITION_SEASONS
from eleveniq.pipeline.quality_report import (
    find_dismissal,
    find_dismissal_minute,
    find_substitutions_off,
    check_dismissal_event_quality,
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
    Secondary check of the exit cap (plan 004 H4 §7, ADR 002 §31.2): for every
    dismissed player in the parquet, minutes_played must not exceed the elapsed
    playing time from his entry (his earliest lineup stint start) to the card
    by more than DISMISSAL_TOLERANCE_MINUTES. Measuring from the entry, not from
    kick-off, also catches an uncapped substitute: on at 60, sent off at 70 and
    credited to 90 has 30 min, under the card's 70 but over its 10-min span.

    The elapsed times are computed here from the raw Half End events (each
    period ends at its Half End; period p starts at 0, 45, 90, 105) and the raw
    lineups, not with the pipeline's PeriodClock: on the period-aware clock a
    starter sent off in period 2 is legitimately above the match-absolute card
    minute by the first-half stoppage.
    """
    import polars as pl
    from eleveniq.statsbomb.loader import load_events, load_lineups, load_matches
    from eleveniq.pipeline.quality_report import DISMISSAL_TOLERANCE_MINUTES

    period_start = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}

    def elapsed(events: list[dict], period: int, minute: float) -> float:
        ends = {}
        for e in events:
            if e["type"]["name"] == "Half End" and e["period"] <= 4:
                ends[e["period"]] = max(ends.get(e["period"], 0.0),
                                        e["minute"] + e["second"] / 60)
        before = sum(ends[p] - period_start[p] for p in ends if p < period)
        return before + min(max(minute, period_start[period]), ends[period]) - period_start[period]

    def mm_ss(t: str) -> float:
        mm, ss = t.split(".")[0].split(":")[-2:]
        return int(mm) + int(ss) / 60

    df = pl.read_parquet(_PLAYER_MATCH_PARQUET).filter(
        (pl.col("competition_id") == cs.competition_id)
        & (pl.col("season_id") == cs.season_id)
    )
    matches = load_matches(cs.competition_id, cs.season_id)

    violations = []
    checked = 0
    for m in matches:
        mid = m["match_id"]
        events = [e for e in load_events(mid) if e["period"] <= 4]
        stints = {p["player_id"]: p.get("positions") or []
                  for team in load_lineups(mid) for p in team["lineup"]}
        for row in df.filter(pl.col("match_id") == mid).iter_rows(named=True):
            card = find_dismissal(events, row["player_id"])
            if card is None:
                continue
            checked += 1
            entry = min(elapsed(events, s["from_period"], mm_ss(s["from"]))
                        for s in stints[row["player_id"]])
            limit = elapsed(events, *card) - entry
            if row["minutes_played"] > limit + DISMISSAL_TOLERANCE_MINUTES:
                violations.append(
                    f"match={mid} player={row['player_name']!r} "
                    f"minutes={row['minutes_played']:.2f} entry_to_card={limit:.2f} "
                    f"excess={row['minutes_played'] - limit:.2f}"
                )

    assert checked > 0
    assert violations == [], (
        f"{len(violations)} dismissed player(s) with overcounted minutes:\n"
        + "\n".join(violations)
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output",
)
@pytest.mark.parametrize(
    "cs", COMPETITION_SEASONS, ids=[cs.label for cs in COMPETITION_SEASONS]
)
def test_dismissed_players_in_parquet_match_spike_count(cs):
    """
    Plan 004 R12: the number of dismissed players with a player_match row
    equals base.dismissals in the reference file. Only the expected count is
    independent: spikes/verify_core_metrics.py counts it from raw cards
    (players who took the field). The observed side uses the pipeline's own
    find_dismissal_minute, so a dismissal with missing timestamps (None) shows
    up here as a count mismatch, not as a named error.
    La Liga 2015/16: 109 cards, 3 on the bench, so 106 (ADR 002 §3).
    test_no_dismissed_player_minutes_above_card_minute is the secondary check.
    """
    import json
    import polars as pl
    from eleveniq.metrics.minutes import is_playing_time
    from eleveniq.statsbomb.loader import load_events, load_matches

    ref_path = (
        Path(__file__).parent / "fixtures" / "reference_totals"
        / f"{cs.competition_id}_{cs.season_id}.json"
    )
    expected = json.loads(ref_path.read_text())["base"]["dismissals"]
    df = pl.read_parquet(_PLAYER_MATCH_PARQUET).filter(
        (pl.col("competition_id") == cs.competition_id)
        & (pl.col("season_id") == cs.season_id)
    )
    in_parquet = {}
    for row in df.select("match_id", "player_id").iter_rows():
        in_parquet.setdefault(row[0], set()).add(row[1])

    dismissed = 0
    for m in load_matches(cs.competition_id, cs.season_id):
        events = [e for e in load_events(m["match_id"]) if is_playing_time(e)]
        dismissed += sum(
            1 for pid in in_parquet.get(m["match_id"], ())
            if find_dismissal_minute(events, pid) is not None
        )
    assert dismissed == expected


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


class TestFindSubstitutionsOffNullType:
    def test_null_type_raises_clear_error(self):
        # Could be a Substitution: never skipped silently, never an AttributeError.
        events = [{"id": "x1", "type": None, "player": {"id": 10},
                   "period": 1, "minute": 30, "second": 0}]
        with pytest.raises(ValueError, match="x1"):
            find_substitutions_off(events, 10)


class TestFindDismissal:
    def test_returns_period_and_minute(self):
        events = [{"type": {"name": "Bad Behaviour"}, "player": {"id": 7},
                   "bad_behaviour": {"card": {"name": "Red Card"}},
                   "period": 2, "minute": 70, "second": 30}]
        assert find_dismissal(events, 7) == (2, pytest.approx(70.5))
        assert find_dismissal_minute(events, 7) == pytest.approx(70.5)

    def test_none_when_not_dismissed(self):
        assert find_dismissal([], 7) is None


def test_find_substitutions_off_missing_type_key_raises():
    # Docstring: a missing type (not only a null one) is never skipped silently.
    events = [{"id": "x2", "player": {"id": 10}, "period": 1, "minute": 30, "second": 0}]
    with pytest.raises(ValueError, match="x2"):
        find_substitutions_off(events, 10)
