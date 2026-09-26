"""Tests for src/metrics/minutes.py"""
import pytest
from eleveniq.metrics.minutes import parse_mm_ss, compute_match_duration, compute_player_minutes


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

    def test_empty_events_returns_90(self):
        assert compute_match_duration([]) == pytest.approx(90.0)

    def test_all_shootout_events_returns_90(self):
        events = [self._make_event(0, 0, 5), self._make_event(0, 30, 5)]
        assert compute_match_duration(events) == pytest.approx(90.0)

    def test_stoppage_captured(self):
        events = [self._make_event(95, 45, 2)]
        assert compute_match_duration(events) == pytest.approx(95 + 45 / 60)


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
