"""
Period-aware minutes played from StatsBomb lineup positions (ADR 002 §31).

Clock (§31.1)
-------------
Lineup times and event minutes are match-absolute, and period 2 restarts at
45:00, so first-half stoppage overlaps the start of period 2 on that axis.
Every time is therefore an Instant = (period, minute), ordered by period
then minute, and converted to elapsed playing time by a PeriodClock:

- each playing period (1-4) ends at its `Half End` event, the referee's
  whistle, not at its last event;
- elapsed((p, m)) = sum of the earlier periods' lengths
  + clamp(m, PERIOD_START[p], end[p]) - PERIOD_START[p];
- match_duration = the sum of the period lengths (elapsed playing time).
Period 5 (penalty shootout) is not playing time.

Exits and treatment windows (§31.2, §31.3)
------------------------------------------
A player's exit is the earliest, by instant, of his first dismissal, his own
Substitution event and an unpaired Player Off (stint_end_cap). Every stint end
is capped at it. A treatment window (Player Off -> his next Player On, in
event order) is subtracted from his stints.

Union and position (§31.4)
--------------------------
player_timeline() turns the stints into non-overlapping pieces: minutes are
the union of the stints (a player cannot be on the pitch twice), and the
position at elapsed time t is that of the stint with the latest start at or
before t (on equal elapsed starts the later instant; at one start instant a
Tactical Shift stint, then the stint listed first). minutes_played and the
per-group / per-raw-position minutes (positions.py) all read the same
timeline, so they cannot drift.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from eleveniq.config import HALF_END_AGREE_MINUTES

# Period 5 is the penalty shootout: not playing time, so it counts toward no
# metric and is excluded from the match duration. Periods 1-4 are playing time.
SHOOTOUT_PERIOD: int = 5

# ADR 002 §31.1: start and nominal end of each playing period (match-absolute).
PERIOD_START: dict[int, float] = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}
NOMINAL_PERIOD_END: dict[int, float] = {1: 45.0, 2: 90.0, 3: 105.0, 4: 120.0}
# ADR 002 §31.1: every playing period has exactly this many Half End events,
# one per team.
HALF_END_EVENTS_PER_PERIOD: int = 2
# Float noise when comparing elapsed times.
TIME_EPS: float = 1e-9

HALF_END = "Half End"
PLAYER_OFF = "Player Off"
PLAYER_ON = "Player On"
TACTICAL_SHIFT = "Tactical Shift"

Instant = tuple[int, float]


def is_playing_time(event: dict) -> bool:
    """True for an event in periods 1-4; False for the shootout (period 5).

    The one shootout-period rule for the period clock and the event filter
    in build_player_match. `period` is required (KeyError, never defaulted).
    """
    return event["period"] < SHOOTOUT_PERIOD


def parse_mm_ss(time_str: str) -> float:
    """
    Parse a StatsBomb position timestamp to decimal minutes.

    Accepts "MM:SS", "MM:SS.mmm", or "H:MM:SS" (the spec allows optional hours).
    Examples:
        "00:00"     → 0.0
        "45:00"     → 45.0
        "73:10"     → 73.1667...
        "90:00.000" → 90.0
    """
    main = time_str.split(".")[0]
    parts = main.split(":")
    if len(parts) == 3:
        return float(parts[0]) * 60 + float(parts[1]) + float(parts[2]) / 60
    return float(parts[0]) + float(parts[1]) / 60


def event_instant(event: dict) -> Instant:
    """(period, minute + second / 60) of an event (ADR 002 §31.1)."""
    return (event["period"], event["minute"] + event["second"] / 60)


def compute_period_ends(events: list[dict]) -> dict[int, float]:
    """
    Match-absolute end of each playing period: the instant of its `Half End`
    event (the later of the two teams' events, which agree within 1 s).

    Raises ValueError (ADR 002 §31.1) when the match has no period 1-4 event,
    when the playing periods present are not exactly 1..max, when a playing
    period does not have exactly two Half End events from two different teams
    (`team.id`), or when they differ by more than HALF_END_AGREE_MINUTES
    (config.py). A period that ends before its nominal end is kept as recorded;
    the caller reports it (and fails a far-short one).
    """
    play = [e for e in events if is_playing_time(e)]
    if not play:
        raise ValueError("no events with period <= 4: the match has no playing time")
    periods = sorted({e["period"] for e in play})
    if periods != list(range(1, periods[-1] + 1)):
        raise ValueError(f"playing periods {periods} are not 1..max")
    ends: dict[int, float] = {}
    for p in periods:
        events_p = [e for e in play if e["period"] == p and e["type"]["name"] == HALF_END]
        if not events_p:
            raise ValueError(f"period {p} has events but no Half End event")
        teams = [(e.get("team") or {}).get("id") for e in events_p]
        if len(events_p) != HALF_END_EVENTS_PER_PERIOD or len(set(teams)) != len(teams) \
                or None in teams:
            raise ValueError(f"period {p} Half End events are not one per team "
                             f"(expected {HALF_END_EVENTS_PER_PERIOD}): team ids {teams}")
        half_ends = [event_instant(e)[1] for e in events_p]
        if max(half_ends) - min(half_ends) > HALF_END_AGREE_MINUTES + TIME_EPS:
            raise ValueError(f"period {p} Half End events disagree: {sorted(half_ends)}")
        ends[p] = max(half_ends)
    return ends


@dataclass(frozen=True)
class PeriodClock:
    """
    The ADR 002 §31.1 period clock. `ends` maps each playing period (exactly
    1..n) to the match-absolute minute of its Half End.
    """

    ends: dict[int, float]

    def __post_init__(self) -> None:
        periods = sorted(self.ends)
        if not periods or periods != list(range(1, periods[-1] + 1)) or periods[-1] > 4:
            raise ValueError(f"playing periods {periods} are not 1..n with n <= 4")

    @classmethod
    def from_events(cls, events: list[dict]) -> "PeriodClock":
        return cls(compute_period_ends(events))

    @property
    def periods(self) -> list[int]:
        return sorted(self.ends)

    def length(self, period: int) -> float:
        """Recorded length of a period: its Half End minus PERIOD_START."""
        return self.ends[period] - PERIOD_START[period]

    def offset(self, period: int) -> float:
        """Elapsed playing time at the start of `period`."""
        return sum(self.length(q) for q in self.periods if q < period)

    @property
    def duration(self) -> float:
        """match_duration: elapsed playing time, the sum of the period lengths."""
        return sum(self.length(p) for p in self.periods)

    @property
    def end(self) -> Instant:
        """match_end: (last period, its Half End)."""
        last = self.periods[-1]
        return (last, self.ends[last])

    def elapsed(self, at: Instant) -> float:
        """Elapsed playing time at an instant; the minute is clamped into its period."""
        period, minute = at
        if period not in self.ends:
            raise ValueError(f"instant {at} is in period {period}, not a playing period "
                             f"of this match {self.periods}")
        start = PERIOD_START[period]
        return self.offset(period) + min(max(minute, start), self.ends[period]) - start

    def period_at(self, t: float) -> int:
        """The period whose elapsed range holds t (a boundary belongs to the later period)."""
        for p in reversed(self.periods):
            if t >= self.offset(p) - TIME_EPS:
                return p
        raise ValueError(f"elapsed time {t} is before kick-off")


def compute_match_duration(events: list[dict]) -> float:
    """
    match_duration (ADR 002 §31.1): elapsed playing time, the sum of the
    period lengths on the Half End clock. Raises ValueError as
    compute_period_ends does.
    """
    return PeriodClock.from_events(events).duration


@dataclass(frozen=True)
class TreatmentWindows:
    """
    A player's Player Off / Player On pairs (ADR 002 §31.3) and his unpaired
    Player Off (§31.2), if any. `unpaired_off_position` is that event's position
    in the event list, for the event_after_permanent_off check.
    """

    windows: tuple[tuple[Instant, Instant], ...]
    unpaired_off: Instant | None
    unpaired_off_position: int | None


def find_treatment_windows(events: list[dict], player_id: int) -> TreatmentWindows:
    """
    Pair each Player Off by `player_id` with his next Player On, in event
    order (StatsBomb files are in `index` order). A Player Off with no later
    Player On is unpaired: an exit, whatever its `permanent` label says.

    Raises ValueError for two Player Off events in a row with no Player On
    between them: the pairing would be ambiguous (§31.3).
    """
    windows: list[tuple[Instant, Instant]] = []
    open_off: tuple[Instant, int] | None = None
    for position, event in enumerate(events):
        if (event.get("player") or {}).get("id") != player_id:
            continue
        kind = event["type"]["name"]
        if kind == PLAYER_OFF:
            if open_off is not None:
                raise ValueError(f"player {player_id} has two Player Off events in a row")
            open_off = (event_instant(event), position)
        elif kind == PLAYER_ON and open_off is not None:
            windows.append((open_off[0], event_instant(event)))
            open_off = None
    return TreatmentWindows(
        windows=tuple(windows),
        unpaired_off=None if open_off is None else open_off[0],
        unpaired_off_position=None if open_off is None else open_off[1],
    )


def stint_end_cap(
    *,
    dismissal: Instant | None,
    substitution: Instant | None,
    unpaired_off: Instant | None,
) -> Instant | None:
    """
    The player's exit (ADR 002 §31.2): the earliest, by (period, minute), of
    his dismissal, his own substitution off and his unpaired Player Off, or
    None when none happened. All three are required keywords, so a forgotten
    one cannot silently give uncapped minutes.
    """
    exits = [x for x in (dismissal, substitution, unpaired_off) if x is not None]
    return min(exits) if exits else None


def stint_end(stint: dict, match_end: Instant) -> Instant:
    """(to_period, to) of a stint, or match_end when it is open (no `to`).

    Raises ValueError for a stint with a `to` but no `to_period`: without the
    period the end cannot be ordered against period-aware instants.
    """
    if not stint.get("to"):
        return match_end
    if stint.get("to_period") is None:
        raise ValueError(
            f"stint {stint.get('from')!r}-{stint['to']!r} has a 'to' but no "
            "'to_period'; period-aware minutes need both"
        )
    return (stint["to_period"], parse_mm_ss(stint["to"]))


def stint_position_name(stint: dict) -> str:
    """The stint's StatsBomb position name: a plain string (as in StatsBomb open
    data) or a dict with 'name' (schema robustness); "" for a null position."""
    pos = stint.get("position")
    if pos is None:
        return ""
    if isinstance(pos, dict):
        return pos.get("name", "")
    return str(pos)


@dataclass(frozen=True)
class TimelinePiece:
    """A span of elapsed time inside one period with one position."""

    position: str
    period: int
    start: float
    end: float

    @property
    def minutes(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class PlayerTimeline:
    """
    A player's credited time in one match (ADR 002 §31.4).

    pieces: non-overlapping, in time order; their total is minutes_played.
    overlap_minutes: stint time counted once instead of twice (the union).
    treatment_windows_inside_stints: windows that removed time from a stint.
    """

    pieces: tuple[TimelinePiece, ...]
    overlap_minutes: float
    treatment_windows_inside_stints: int

    @property
    def minutes(self) -> float:
        return sum(p.minutes for p in self.pieces)

    def covers(self, t: float) -> bool:
        """True when elapsed time t is inside his credited time (closed intervals)."""
        return any(p.start - TIME_EPS <= t <= p.end + TIME_EPS for p in self.pieces)


def _subtract(
    start: float, end: float, cuts: Iterable[tuple[float, float]]
) -> list[tuple[float, float]]:
    """[start, end) minus every [c, d) in cuts; empty pieces dropped."""
    pieces = [(start, end)]
    for c, d in cuts:
        remaining = []
        for x, y in pieces:
            if d <= x or c >= y:
                remaining.append((x, y))
                continue
            if c > x:
                remaining.append((x, c))
            if d < y:
                remaining.append((d, y))
        pieces = remaining
    return [(x, y) for x, y in pieces if y > x]


def player_timeline(
    positions: list[dict],
    clock: PeriodClock,
    *,
    exit_at: Instant | None,
    treatment_windows: Iterable[tuple[Instant, Instant]],
) -> PlayerTimeline:
    """
    The player's credited time (ADR 002 §31.4).

    For each stint: start = (from_period, from); end = (to_period, to), or
    match_end when open; end = min(end, exit_at); both converted with
    clock.elapsed(); the treatment windows (converted the same way) are
    subtracted; an empty or negative piece contributes nothing (a
    reversed-period stint always ends up empty).

    The minutes are the union of these pieces. The position at elapsed time t
    is that of the stint with the greatest elapsed start <= t: on equal elapsed
    starts the later start instant; at one start instant a stint whose
    start_reason is a Tactical Shift, then the stint listed first. Every stint
    takes part in the position rule, empty and reversed ones included, and the
    latest-starting stint gives the position even after it has ended.

    exit_at and treatment_windows are required keywords (pass None / () when
    there is none). The clamp guards (a stint bound outside its period) are the
    caller's checks: clock.elapsed() clamps silently. Raises ValueError for a
    stint with a `to` but no `to_period`, a bound in a period the match does
    not have, or a reversed treatment window (its Player On earlier in elapsed
    time than its Player Off): subtracting it would remove nothing and leave an
    overlap that is then misreported as overlapping_stints.
    """
    windows = tuple(treatment_windows)
    cuts = [(clock.elapsed(a), clock.elapsed(b)) for a, b in windows]
    for (off, on), (c, d) in zip(windows, cuts):
        if d < c - TIME_EPS:
            raise ValueError(f"treatment window Player Off {off} -> Player On {on} "
                             "is reversed in elapsed time")
    intervals: list[tuple[float, float]] = []
    # (elapsed start, start instant, started by a Tactical Shift, -list index,
    # position): max() over the markers at or before t gives the position.
    markers: list[tuple[float, Instant, bool, int, str]] = []
    windows_used: set[int] = set()
    for i, stint in enumerate(positions):
        start: Instant = (stint["from_period"], parse_mm_ss(stint["from"]))
        end = stint_end(stint, clock.end)
        if exit_at is not None:
            end = min(end, exit_at)
        a, b = clock.elapsed(start), clock.elapsed(end)
        markers.append((a, start, stint.get("start_reason") == TACTICAL_SHIFT, -i,
                        stint_position_name(stint)))
        if b > a:
            windows_used.update(
                j for j, (c, d) in enumerate(cuts) if min(b, d) - max(a, c) > TIME_EPS)
            intervals.extend(_subtract(a, b, cuts))

    bounds = sorted({x for iv in intervals for x in iv}
                    | {m[0] for m in markers}
                    | {clock.offset(p) for p in clock.periods})
    pieces: list[TimelinePiece] = []
    for a, b in zip(bounds, bounds[1:]):
        mid = (a + b) / 2
        if not any(x <= mid < y for x, y in intervals):
            continue
        position = max(m for m in markers if m[0] <= mid + TIME_EPS)[4]
        pieces.append(TimelinePiece(position, clock.period_at(mid), a, b))
    union = sum(p.minutes for p in pieces)
    return PlayerTimeline(
        pieces=tuple(pieces),
        overlap_minutes=sum(y - x for x, y in intervals) - union,
        treatment_windows_inside_stints=len(windows_used),
    )


def compute_player_minutes(
    positions: list[dict],
    clock: PeriodClock,
    *,
    exit_at: Instant | None,
    treatment_windows: Iterable[tuple[Instant, Instant]],
) -> float:
    """
    minutes_played for one player-match: the length of his timeline
    (player_timeline). An empty positions list gives 0.

    Pipeline usage note: the pipeline derives minutes_played as
    sum(minutes_per_group(timeline).values()) from the same timeline;
    TestMinutesConsistency in tests/test_positions.py is the guard.
    """
    return player_timeline(
        positions, clock, exit_at=exit_at, treatment_windows=treatment_windows
    ).minutes
