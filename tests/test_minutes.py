"""Tests for src/metrics/minutes.py"""
import pytest
from eleveniq.metrics.minutes import (
    PeriodClock, compute_match_duration, compute_period_ends, compute_player_minutes,
    find_treatment_windows, is_playing_time, parse_mm_ss, player_timeline, stint_end_cap,
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


def _ev(kind: str, period: int, minute: float, pid: int | None = None,
        team: int | None = None) -> dict:
    whole = int(minute)
    e = {"type": {"name": kind}, "period": period, "minute": whole,
         "second": round((minute - whole) * 60)}
    if pid is not None:
        e["player"] = {"id": pid}
    if team is not None:
        e["team"] = {"id": team}
    return e


def _half_end_pair(period: int, minute: float, other_minute: float | None = None) -> list[dict]:
    """The period's two Half End events, team 1 at `minute` and team 2 at
    `other_minute` (default: the same instant)."""
    return [_ev("Half End", period, minute, team=1),
            _ev("Half End", period, minute if other_minute is None else other_minute, team=2)]


def _half_ends(*ends: float) -> list[dict]:
    """Two Half End events (one per team) per period, period i ending at ends[i-1]."""
    return [e for p, end in enumerate(ends, start=1) for e in _half_end_pair(p, end)]


def _t(minutes: float) -> str:
    whole = int(minutes)
    return f"{whole:02d}:{round((minutes - whole) * 60):02d}"


def _stint(frm: float, fp: int, to: float | None = None, tp: int | None = None,
           position: str = "Center Back", reason: str = "Starting XI") -> dict:
    return {"position": position, "from": _t(frm), "from_period": fp,
            "to": None if to is None else _t(to), "to_period": tp, "start_reason": reason}


# A 47 + 48 = 95 min match: first-half whistle at 47:00, second at 93:00.
CLOCK = PeriodClock({1: 47.0, 2: 93.0})


def _minutes(positions, *, exit_at=None, windows=()) -> float:
    return compute_player_minutes(positions, CLOCK, exit_at=exit_at, treatment_windows=windows)


class TestComputePeriodEnds:
    """ADR 002 §31.1: each period ends at its Half End event."""

    def test_period_ends_at_half_end_not_last_event(self):
        events = [_ev("Pass", 1, 10), *_half_ends(46.5, 93.0), _ev("Ball Receipt*", 1, 48)]
        assert compute_period_ends(events) == {1: 46.5, 2: 93.0}

    def test_match_duration_is_the_sum_of_period_lengths(self):
        """3754339 shape: 54:15 + 49:07 - the second half starts at 45:00."""
        events = _half_ends(54.25, 94 + 7 / 60)
        assert compute_match_duration(events) == pytest.approx(54.25 + 49 + 7 / 60)

    def test_half_end_events_one_second_apart_agree(self):
        events = [*_half_end_pair(1, 46.0, 46 + 1 / 60), *_half_ends(0, 92.0)[2:]]
        assert compute_period_ends(events)[1] == pytest.approx(46 + 1 / 60)

    def test_extra_time_periods_and_shootout(self):
        events = [*_half_ends(46.0, 92.0, 106.0, 121.5), _ev("Shot", 5, 122, 7)]
        clock = PeriodClock.from_events(events)
        assert clock.duration == pytest.approx(46 + 47 + 16 + 16.5)
        assert clock.elapsed((3, 90.0)) == pytest.approx(93.0)
        assert clock.elapsed((4, 105.0)) == pytest.approx(109.0)

    @pytest.mark.parametrize("events, match", [
        ([_ev("Pass", 1, 10), *_half_end_pair(2, 90)], "period 1 has events but no Half End"),
        (_half_end_pair(1, 45.0, 45 + 2 / 60), "disagree"),
        ([*_half_ends(45.0), *_half_end_pair(3, 105.0)], "not 1..max"),
        ([_ev("Shot", 5, 121, 7)], "period <= 4"),
        ([], "period <= 4"),
        # §31.1: exactly two Half End events per period, one per team.
        (_half_end_pair(1, 45.0)[:1], "not one per team"),
        ([*_half_end_pair(1, 45.0), _ev("Half End", 1, 45.0, team=3)], "not one per team"),
        ([_ev("Half End", 1, 45.0, team=1), _ev("Half End", 1, 45.0, team=1)],
         "not one per team"),
        ([_ev("Half End", 1, 45.0, team=1), _ev("Half End", 1, 45.0)], "not one per team"),
        ([_ev("Half End", 1, 45.0), _ev("Half End", 1, 45.0)], "not one per team"),
    ], ids=["no_half_end", "half_ends_disagree", "period_gap", "shootout_only", "empty",
            "one_half_end", "three_half_ends", "same_team_twice", "one_team_missing",
            "no_team_ids"])
    def test_fail_loud(self, events, match):
        with pytest.raises(ValueError, match=match):
            compute_period_ends(events)

    def test_period_missing_raises(self):
        with pytest.raises(KeyError):
            compute_period_ends([{"minute": 88, "second": 15, "type": {"name": "Pass"}}])


class TestPeriodClock:
    def test_elapsed_counts_first_half_stoppage(self):
        assert CLOCK.elapsed((1, 46.0)) == pytest.approx(46.0)
        assert CLOCK.elapsed((2, 45.0)) == pytest.approx(47.0)
        assert CLOCK.elapsed((2, 60.0)) == pytest.approx(62.0)
        assert CLOCK.duration == pytest.approx(95.0)
        assert CLOCK.end == (2, 93.0)

    def test_elapsed_clamps_into_the_period(self):
        assert CLOCK.elapsed((1, 48.5)) == pytest.approx(47.0)   # after the whistle
        assert CLOCK.elapsed((2, 44.0)) == pytest.approx(47.0)   # before 45:00

    def test_short_period_is_kept_as_recorded(self):
        clock = PeriodClock({1: 44.0, 2: 90.0})
        assert clock.duration == pytest.approx(44 + 45)
        assert clock.elapsed((2, 45.0)) == pytest.approx(44.0)

    def test_period_not_in_the_match_raises(self):
        with pytest.raises(ValueError, match="not a playing period"):
            CLOCK.elapsed((3, 95.0))

    def test_periods_must_be_contiguous(self):
        with pytest.raises(ValueError):
            PeriodClock({1: 45.0, 3: 105.0})


class TestStintEndCap:
    """ADR 002 §31.2: the exit is the earliest instant, by (period, minute)."""

    @pytest.mark.parametrize("dismissal, substitution, off, expected", [
        (None, None, None, None),
        ((2, 70.0), None, None, (2, 70.0)),
        (None, (2, 60.0), (2, 80.0), (2, 60.0)),
        # first-half stoppage (1, 46:30) precedes (2, 45:30) although 46.5 > 45.5
        ((2, 45.5), None, (1, 46.5), (1, 46.5)),
        ((1, 0.0), (2, 30.0), None, (1, 0.0)),   # a 0:00 exit is an exit
    ])
    def test_earliest_exit(self, dismissal, substitution, off, expected):
        assert stint_end_cap(dismissal=dismissal, substitution=substitution,
                             unpaired_off=off) == expected

    def test_all_keywords_required(self):
        with pytest.raises(TypeError, match="unpaired_off"):
            stint_end_cap(dismissal=None, substitution=None)


class TestFindTreatmentWindows:
    def test_off_paired_with_next_on(self):
        events = [_ev("Player Off", 1, 20, 7), _ev("Pass", 1, 21, 8), _ev("Player On", 1, 22, 7),
                  _ev("Player Off", 2, 60, 7), _ev("Player On", 2, 61, 7)]
        found = find_treatment_windows(events, 7)
        assert found.windows == (((1, 20.0), (1, 22.0)), ((2, 60.0), (2, 61.0)))
        assert found.unpaired_off is None

    def test_unpaired_off_whatever_its_label(self):
        events = [_ev("Pass", 1, 5, 7), {**_ev("Player Off", 2, 70, 7),
                                         "player_off": {"permanent": False}}]
        found = find_treatment_windows(events, 7)
        assert found.windows == ()
        assert (found.unpaired_off, found.unpaired_off_position) == ((2, 70.0), 1)

    def test_player_on_without_off_is_ignored(self):
        assert find_treatment_windows([_ev("Player On", 1, 10, 7)], 7).windows == ()

    def test_two_offs_in_a_row_raise(self):
        events = [_ev("Player Off", 1, 20, 7), _ev("Player Off", 1, 21, 7),
                  _ev("Player On", 1, 22, 7)]
        with pytest.raises(ValueError, match="two Player Off"):
            find_treatment_windows(events, 7)


class TestComputePlayerMinutes:
    """ADR 002 §31.4 on the 47 + 48 = 95 min CLOCK."""

    def test_full_match_gains_first_half_stoppage(self):
        assert _minutes([_stint(0, 1)]) == pytest.approx(95.0)

    def test_second_half_sub_on(self):
        assert _minutes([_stint(60, 2)]) == pytest.approx(33.0)

    def test_subbed_off_in_second_half(self):
        assert _minutes([_stint(0, 1, 60, 2)]) == pytest.approx(62.0)

    def test_unused_sub(self):
        assert _minutes([]) == 0.0

    def test_stints_across_half_time(self):
        """First-half stoppage stint to (1, 46:30), then from (2, 45:00): 46.5 + 48."""
        assert _minutes([_stint(0, 1, 46.5, 1), _stint(45, 2)]) == pytest.approx(94.5)

    def test_zero_length_stint(self):
        assert _minutes([_stint(45, 2, 45, 2)]) == 0.0

    def test_dismissal_in_first_half_stoppage_caps_open_stint(self):
        assert _minutes([_stint(0, 1)], exit_at=(1, 46.5)) == pytest.approx(46.5)

    def test_exit_caps_explicit_to(self):
        """3829440-style explicit `to` past the card: clamped at the card."""
        assert _minutes([_stint(0, 1, 66.75, 2), _stint(66.75, 2)],
                        exit_at=(2, 56.0)) == pytest.approx(58.0)

    def test_explicit_to_before_exit_unchanged(self):
        assert _minutes([_stint(0, 1, 59.5, 2)], exit_at=(2, 60.0)) == pytest.approx(61.5)

    def test_exit_before_stint_start_gives_zero(self):
        assert _minutes([_stint(60, 2)], exit_at=(2, 55.0)) == 0.0

    def test_interval_card_clamps_to_period_end(self):
        """A card at (1, 48:00), after the 47:00 whistle, is clamped to 47."""
        assert _minutes([_stint(0, 1)], exit_at=(1, 48.0)) == pytest.approx(47.0)

    def test_treatment_window_inside_stint_subtracted(self):
        assert _minutes([_stint(0, 1)], windows=[((1, 20.0), (1, 22.0))]) == pytest.approx(93.0)

    def test_treatment_window_across_half_time(self):
        """(1, 46:00) -> (2, 46:00) removes 1 + 1 min on the elapsed clock."""
        assert _minutes([_stint(0, 1)], windows=[((1, 46.0), (2, 46.0))]) == pytest.approx(93.0)

    def test_reversed_treatment_window_raises(self):
        """A Player On earlier than its Player Off would subtract nothing and be
        misreported as an overlap: it fails loud instead."""
        with pytest.raises(ValueError, match="reversed in elapsed time"):
            _minutes([_stint(0, 1)], windows=[((1, 22.0), (1, 20.0))])
        with pytest.raises(ValueError, match="reversed in elapsed time"):
            _minutes([_stint(0, 1)], windows=[((2, 46.0), (1, 46.0))])

    def test_zero_length_window_after_clamping_is_allowed(self):
        """Off (1, 47:30) and On (1, 48:00), both after the 47:00 whistle, clamp
        to the same elapsed time: an empty window, not a reversed one."""
        assert _minutes([_stint(0, 1)], windows=[((1, 47.5), (1, 48.0))]) == pytest.approx(95.0)

    def test_treatment_windows_may_be_a_generator(self):
        windows = (w for w in [((1, 20.0), (1, 22.0))])
        assert _minutes([_stint(0, 1)], windows=windows) == pytest.approx(93.0)

    def test_reversed_period_stint_adds_nothing(self):
        assert _minutes([_stint(46, 2, 46.5, 1)]) == 0.0

    def test_overlapping_stints_counted_once(self):
        timeline = player_timeline([_stint(0, 1, 50, 2), _stint(48, 2)], CLOCK,
                                   exit_at=None, treatment_windows=())
        assert timeline.minutes == pytest.approx(95.0)
        assert timeline.overlap_minutes == pytest.approx(2.0)

    def test_to_without_to_period_raises(self):
        with pytest.raises(ValueError, match="to_period"):
            _minutes([{**_stint(0, 1, 60, 2), "to_period": None}])

    def test_keywords_required(self):
        with pytest.raises(TypeError, match="treatment_windows"):
            compute_player_minutes([_stint(0, 1)], CLOCK, exit_at=None)
        with pytest.raises(TypeError, match="exit_at"):
            compute_player_minutes([_stint(0, 1)], CLOCK, treatment_windows=())


class TestPlayerTimelinePosition:
    """ADR 002 §31.4: the position at t is that of the latest-starting stint."""

    @staticmethod
    def _by_position(positions) -> dict[str, float]:
        out: dict[str, float] = {}
        for piece in player_timeline(positions, CLOCK, exit_at=None, treatment_windows=()).pieces:
            out[piece.position] = out.get(piece.position, 0.0) + piece.minutes
        return out

    def test_latest_start_wins_after_that_stint_ended(self):
        """CB open from kick-off, a 2-min CF stint from (2, 48:00): CF from 50 on."""
        got = self._by_position([_stint(0, 1), _stint(48, 2, 50, 2, "Center Forward",
                                                      "Tactical Shift")])
        assert got == pytest.approx({"Center Back": 50.0, "Center Forward": 45.0})

    def test_equal_elapsed_start_later_instant_wins(self):
        """(1, 47:30) clamps to elapsed 47 = elapsed (2, 45:00); the later instant
        wins although it is listed second (no 2015/16 case)."""
        got = self._by_position([_stint(0, 1, 47, 1), _stint(47.5, 1, position="Right Midfield"),
                                 _stint(45, 2, position="Center Forward")])
        assert got == pytest.approx({"Center Back": 47.0, "Center Forward": 48.0})

    @pytest.mark.parametrize("shift_first", [True, False])
    def test_same_instant_tactical_shift_wins(self, shift_first):
        """Ravel Morrison 3878553: the shift's position wins whatever the order."""
        slot = _stint(60, 2, position="Left Back", reason="Substitution - On (Tactical)")
        shift = _stint(60, 2, 60, 2, position="Center Forward", reason="Tactical Shift")
        got = self._by_position([shift, slot] if shift_first else [slot, shift])
        assert got == pytest.approx({"Center Forward": 33.0})

    def test_chaigneau_same_instant_shift_puts_him_in_goal(self):
        """Chaigneau 3829440: on at (1, 2:16) in the Right Midfield slot, a Tactical
        Shift in the same second puts him in goal; he is a GK throughout."""
        got = self._by_position([
            _stint(2 + 16 / 60, 1, 2 + 16 / 60, 1, "Goalkeeper", "Tactical Shift"),
            _stint(2 + 16 / 60, 1, position="Right Midfield",
                   reason="Substitution - On (Injury)"),
        ])
        assert got == pytest.approx({"Goalkeeper": 95.0 - (2 + 16 / 60)})

    def test_same_instant_without_shift_first_listed_wins(self):
        got = self._by_position([
            _stint(60, 2, 60, 2, "Goalkeeper", "Substitution - On (Tactical)"),
            _stint(60, 2, position="Right Midfield", reason="Substitution - On (Tactical)"),
        ])
        assert got == pytest.approx({"Goalkeeper": 33.0})

    def test_reversed_stint_carries_the_second_half_position(self):
        """Ramsey/Pléa: from (2, 46:00) to (1, 46:30) is empty, but its start marks
        the second-half position."""
        got = self._by_position([
            _stint(0, 1, 46.5, 1),
            _stint(46, 2, 46.5, 1, "Center Forward", "Tactical Shift"),
            _stint(46.5, 1, position="Left Back", reason="Tactical Shift"),
        ])
        assert got == pytest.approx({"Center Back": 46.5, "Left Back": 1.5,
                                     "Center Forward": 47.0})

    def test_pieces_split_at_period_boundaries(self):
        pieces = player_timeline([_stint(0, 1)], CLOCK, exit_at=None, treatment_windows=()).pieces
        assert [(p.period, p.start, p.end) for p in pieces] == [(1, 0.0, 47.0), (2, 47.0, 95.0)]


class TestTreatmentAndExitSpecEdges:
    """ADR 002 §31.1-§31.3 cases the tests above do not reach."""

    def test_window_already_split_by_the_lineup_is_not_inside_a_stint(self):
        """Most of the 1,535 windows in 2015/16: the lineup ends at the Off and
        resumes at the On. Nothing is removed twice and the window is not
        reported as treatment_window_inside_stint."""
        timeline = player_timeline(
            [_stint(0, 1, 20, 1), _stint(22, 1, reason="Player On")], CLOCK,
            exit_at=None, treatment_windows=[((1, 20.0), (1, 22.0))])
        assert timeline.minutes == pytest.approx(93.0)
        assert timeline.treatment_windows_inside_stints == 0
        assert timeline.overlap_minutes == pytest.approx(0.0)

    def test_dismissal_inside_a_closed_window_caps_at_the_card_once(self):
        """§31.3/§31.6: Player Off (2, 60), red card (2, 62), Player On (2, 64).
        The exit is the card; the window removes Off..card, nothing more:
        credited time is kick-off to the Off, elapsed 62 on the 47+48 clock."""
        assert _minutes([_stint(0, 1)], exit_at=(2, 62.0),
                        windows=[((2, 60.0), (2, 64.0))]) == pytest.approx(62.0)

    def test_off_labelled_permanent_but_followed_by_on_is_paired(self):
        """Weber 3900512: the `permanent` label is ignored; only pairing counts."""
        events = [{**_ev("Player Off", 2, 60, 7), "player_off": {"permanent": True}},
                  _ev("Player On", 2, 63, 7)]
        found = find_treatment_windows(events, 7)
        assert found.windows == (((2, 60.0), (2, 63.0)),)
        assert found.unpaired_off is None

    def test_extra_time_minutes_and_shootout_half_end_ignored(self):
        """Periods 3-4 (only unit-tested per §31.1): Half Ends 46, 92, 106, 121:30;
        a period-5 Half End adds nothing. Elapsed: p3 starts at 93, p4 at 109,
        match_duration 125.5."""
        events = [*_half_ends(46.0, 92.0, 106.0, 121.5),
                  _ev("Half End", 5, 135.0), _ev("Half End", 5, 135.0)]
        clock = PeriodClock.from_events(events)
        assert clock.ends == {1: 46.0, 2: 92.0, 3: 106.0, 4: 121.5}
        assert clock.duration == pytest.approx(125.5)
        kw = dict(treatment_windows=())
        # Sub on at (3, 100:00), open stint: 125.5 - 103.
        assert compute_player_minutes([_stint(100, 3)], clock, exit_at=None, **kw) == pytest.approx(22.5)
        # Starter sent off in extra-time first-half stoppage (3, 105:30): 93 + 15.5.
        assert compute_player_minutes([_stint(0, 1)], clock, exit_at=(3, 105.5),
                                      **kw) == pytest.approx(108.5)
        # A stint to the end of period 3 and one from the period-4 kick-off: no gap.
        assert compute_player_minutes([_stint(0, 1, 106, 3), _stint(105, 4)], clock,
                                      exit_at=None, **kw) == pytest.approx(125.5)

# ---------------------------------------------------------------------------
# Plan 004 H3a: shootout period, shared stint bounds, required argument
# ---------------------------------------------------------------------------

class TestIsPlayingTime:
    @pytest.mark.parametrize("period", [1, 2, 3, 4])
    def test_periods_one_to_four_are_playing_time(self, period):
        assert is_playing_time({"period": period})

    def test_shootout_is_not_playing_time(self):
        assert not is_playing_time({"period": 5})

    def test_missing_period_raises(self):
        with pytest.raises(KeyError):
            is_playing_time({})


def test_parse_mm_ss_drops_fractional_seconds():
    # The stint-after-substitution check relies on this: a lineup "37:02.400"
    # is the same instant as a substitution event at 37:02.
    assert parse_mm_ss("37:02.400") == 37 + 2 / 60


