"""Tests for src/metrics/minutes.py"""
import pytest
from eleveniq.metrics.minutes import (
    parse_mm_ss, compute_match_duration, compute_player_minutes, stint_end_cap,
)


class TestParseMmSs:
    def test_whole_minutes(self):
        assert parse_mm_ss("00:00") == pytest.approx(0.0)

    def test_standard_format(self):
        assert parse_mm_ss("73:10") == pytest.approx(73 + 10 / 60)

    def test_stoppage_time_format(self):
        assert parse_mm_ss("45:31") == pytest.approx(45 + 31 / 60)

    def test_milliseconds_stripped(self):
        assert parse_mm_ss("90:05.123") == pytest.approx(90 + 5 / 60)

    def test_hour_format(self):
        assert parse_mm_ss("1:30:00") == pytest.approx(90.0)

    def test_60_minute_mark(self):
        assert parse_mm_ss("60:00") == pytest.approx(60.0)

    def test_zero_with_milliseconds(self):
        # "00:00.000" must parse to 0.0 without crashing (milliseconds stripped).
        assert parse_mm_ss("00:00.000") == pytest.approx(0.0)

    def test_large_stoppage_value(self):
        # e.g. match with 7 minutes of stoppage: "97:12"
        assert parse_mm_ss("97:12") == pytest.approx(97 + 12 / 60)

    def test_45_00_exactly(self):
        # Half-time boundary
        assert parse_mm_ss("45:00") == pytest.approx(45.0)


class TestComputeMatchDuration:
    def _make_event(self, minute: int, second: int, period: int = 1) -> dict:
        return {"minute": minute, "second": second, "period": period}

    def test_basic_match(self):
        events = [
            self._make_event(0, 0, 1),
            self._make_event(45, 0, 1),
            self._make_event(45, 0, 2),
            self._make_event(92, 30, 2),
        ]
        assert compute_match_duration(events) == pytest.approx(92 + 30 / 60)

    def test_exact_90_minutes(self):
        events = [self._make_event(0, 0, 1), self._make_event(90, 0, 2)]
        assert compute_match_duration(events) == pytest.approx(90.0)

    def test_excludes_period_5_shootout(self):
        events = [
            self._make_event(90, 0, 2),
            self._make_event(120, 0, 4),
            self._make_event(0, 5, 5),
        ]
        assert compute_match_duration(events) == pytest.approx(120.0)

    def test_empty_events_raises(self):
        # No playing-time events: no 90-minute default (plan 004 H1.6).
        with pytest.raises(ValueError, match="period <= 4"):
            compute_match_duration([])

    def test_all_shootout_events_raises(self):
        events = [self._make_event(0, 0, 5), self._make_event(0, 30, 5)]
        with pytest.raises(ValueError, match="period <= 4"):
            compute_match_duration(events)

    def test_stoppage_captured(self):
        events = [self._make_event(95, 45, 2)]
        assert compute_match_duration(events) == pytest.approx(95 + 45 / 60)

    def test_extra_time_periods_3_and_4_included(self):
        # Periods 3 and 4 are extra time (≤ 4) — they MUST be included in duration.
        # Period 5 (penalty shootout) is the only excluded period.
        events = [
            self._make_event(90, 0, 2),   # end of normal time
            self._make_event(105, 0, 3),  # first half of extra time
            self._make_event(120, 30, 4), # second half of extra time
        ]
        assert compute_match_duration(events) == pytest.approx(120 + 30 / 60)

    def test_period_missing_raises(self):
        # The period key is required; a missing one is not defaulted to 1.
        event = {"minute": 88, "second": 15}  # no "period" key
        with pytest.raises(KeyError):
            compute_match_duration([event])


class TestComputePlayerMinutes:
    def _stint(self, from_str: str, to_str=None) -> dict:
        return {"from": from_str, "to": to_str}

    def test_full_match_starter_to_none(self):
        stints = [self._stint("00:00", None)]
        assert compute_player_minutes(stints, match_duration=92.5) == pytest.approx(92.5)

    def test_subbed_off_at_60(self):
        stints = [self._stint("00:00", "60:00")]
        assert compute_player_minutes(stints, match_duration=93.0) == pytest.approx(60.0)

    def test_sub_on_at_60_to_none(self):
        stints = [self._stint("60:00", None)]
        assert compute_player_minutes(stints, match_duration=93.0) == pytest.approx(33.0)

    def test_unused_sub_empty_positions(self):
        assert compute_player_minutes([], match_duration=90.0) == pytest.approx(0.0)

    def test_tactical_shift_multi_stint(self):
        stints = [self._stint("00:00", "55:00"), self._stint("55:00", None)]
        assert compute_player_minutes(stints, match_duration=92.0) == pytest.approx(92.0)

    def test_multi_stint_with_gap(self):
        stints = [self._stint("00:00", "30:00"), self._stint("60:00", "75:00")]
        assert compute_player_minutes(stints, match_duration=90.0) == pytest.approx(45.0)

    def test_zero_length_stint_clamped(self):
        stints = [self._stint("45:00", "45:00")]
        assert compute_player_minutes(stints, match_duration=90.0) == pytest.approx(0.0)

    def test_sub_on_at_73_10(self):
        stints = [self._stint("73:10", None)]
        expected = 90.0 + 5 / 60 - (73 + 10 / 60)
        assert compute_player_minutes(stints, match_duration=90.0 + 5 / 60) == pytest.approx(expected)


class TestDismissalMinute:
    def _stint(self, from_str: str, to_str=None) -> dict:
        return {"from": from_str, "to": to_str}

    def test_dismissal_at_15_caps_open_stint(self):
        # StatsBomb left to=None; dismissal_minute=15 should cap the stint
        stints = [self._stint("00:00", None)]
        minutes = compute_player_minutes(stints, match_duration=92.0, dismissal_minute=15.0)
        assert minutes == pytest.approx(15.0)

    def test_dismissal_explicit_to_overrides_nothing(self):
        # StatsBomb correctly set to="15:00"; dismissal_minute param has no effect
        stints = [self._stint("00:00", "15:00")]
        minutes = compute_player_minutes(stints, match_duration=92.0, dismissal_minute=15.0)
        assert minutes == pytest.approx(15.0)

    def test_no_dismissal_minute_uses_match_duration(self):
        stints = [self._stint("00:00", None)]
        minutes = compute_player_minutes(stints, match_duration=92.0)
        assert minutes == pytest.approx(92.0)

    def test_dismissal_minute_none_behaves_as_normal(self):
        stints = [self._stint("00:00", None)]
        minutes = compute_player_minutes(stints, match_duration=90.0, dismissal_minute=None)
        assert minutes == pytest.approx(90.0)

    def test_dismissal_second_half_sub(self):
        # Sub came on at 60:00, dismissed at 75:00 (to=None from StatsBomb)
        stints = [self._stint("60:00", None)]
        minutes = compute_player_minutes(stints, match_duration=92.0, dismissal_minute=75.0)
        assert minutes == pytest.approx(15.0)

    def test_dismissal_minute_with_two_stints(self):
        # Tactical shift player: 0→55, 55→None; dismissed at 70
        stints = [self._stint("00:00", "55:00"), self._stint("55:00", None)]
        minutes = compute_player_minutes(stints, match_duration=92.0, dismissal_minute=70.0)
        assert minutes == pytest.approx(70.0)  # 55 (first stint) + 15 (second, capped at 70)

    def test_dismissal_before_sub_on_gives_zero(self):
        # Impossible in practice, but the clamp should handle it gracefully
        stints = [self._stint("60:00", None)]
        minutes = compute_player_minutes(stints, match_duration=92.0, dismissal_minute=55.0)
        # effective_end=55, start=60 → duration=max(0, 55-60)=0
        assert minutes == pytest.approx(0.0)

    def test_explicit_to_after_dismissal_clamped(self):
        # StatsBomb left explicit to="66:43" even though card was at 56:10.
        # The first stint must be clamped at the card minute; the second at 0.
        stints = [self._stint("00:00", "66:43"), self._stint("66:43", None)]
        minutes = compute_player_minutes(stints, match_duration=95.0, dismissal_minute=56.17)
        assert minutes == pytest.approx(56.17)

    def test_explicit_to_before_dismissal_unchanged(self):
        # Substituted off at 45:00, card fired at 60:00 — 45 min should stand.
        stints = [self._stint("00:00", "45:00")]
        minutes = compute_player_minutes(stints, match_duration=90.0, dismissal_minute=60.0)
        assert minutes == pytest.approx(45.0)

    def test_explicit_to_no_dismissal_not_clamped(self):
        # Without dismissal_minute, explicit to-values are used as-is.
        stints = [self._stint("00:00", "90:05")]
        minutes = compute_player_minutes(stints, match_duration=90.0)
        assert minutes == pytest.approx(90 + 5 / 60)


class TestSubstitutionMinuteCap:
    """Exit cap from the player's own substitution (ADR 002 §3)."""

    @pytest.mark.parametrize("dismissal,substitution,expected", [
        (None, None, None),
        (40.0, None, 40.0),
        (None, 40.0, 40.0),
        (40.0, 60.0, 40.0),
        (60.0, 40.0, 40.0),
        (0.0, 30.0, 0.0),   # a 0.0 cap is a cap, not "missing"
    ])
    def test_stint_end_cap_is_earlier_exit(self, dismissal, substitution, expected):
        assert stint_end_cap(dismissal, substitution) == expected

    def test_explicit_to_before_substitution_unchanged(self):
        # The cap only shortens: an explicit `to` earlier than the sub stands.
        stints = [{"from": "00:00", "to": "59:50"}]
        assert compute_player_minutes(
            stints, match_duration=92.0, substitution_minute=60.0
        ) == pytest.approx(59 + 50 / 60)

    def test_explicit_to_past_match_duration_kept_when_sub_is_later(self):
        # match_duration can lag the real whistle; a sub cap later than `to` must
        # not clamp `to` down to match_duration.
        stints = [{"from": "00:00", "to": "90:05"}]
        assert compute_player_minutes(
            stints, match_duration=90.0, substitution_minute=90 + 10 / 60
        ) == pytest.approx(90 + 5 / 60)
