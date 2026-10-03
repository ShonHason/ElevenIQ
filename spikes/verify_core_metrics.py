#!/usr/bin/env python3
"""
Spike: core DNA metric reference totals for one competition-season (plan 004 H2, H4a;
plan 005 M4b1).

Computes league totals of the core columns from raw StatsBomb JSON and writes
them to the "core" section of
tests/fixtures/reference_totals/{competition_id}_{season_id}.json. Existing
sections are left untouched. Goals + own goals must reconcile with the
matches.json scores, and minutes must pass a structural check (below);
otherwise the spike fails and writes nothing. The "base" section's
"dismissals" count (plan 004 H3 oracle: dismissal cards shown to players who
took the field, ADR 002 §3) must come out unchanged.

Run:
    .venv/bin/python spikes/verify_core_metrics.py --competition-id 11 --season-id 27

Independence
------------
Never imports eleveniq. Each definition is re-implemented from the ADR 002
text. Each column is one of two kinds:

- INDEPENDENT: a raw event count with no rule beyond the event type (or one
  StatsBomb flag). The only shared assumption is "every event with a player
  counts". Any silent drop in the pipeline shows up as a mismatch.
- SECOND IMPLEMENTATION: the same written definition coded again (ADR 002
  §1, §2, §13, §30, §31). It catches coding errors and silent drops, not a
  wrong definition.

minutes_played and minutes_<group> follow ADR 002 §31 (period-aware minutes,
plan 004 H4): each period ends at its Half End event; exits (dismissal, own
substitution, unpaired Player Off) are ordered by (period, minute); treatment
windows are subtracted; a player's stints count as their union; the position
at time t is that of the stint with the latest start at or before t (at one
start instant, a stint started by a Tactical Shift, then the stint listed
first). The §31 fail-loud rules stop the spike.

M4b (ADR 004): progressive passes exclude restarts (D14: corners, free kicks, goal
kicks, kick-offs, throw-ins); np_xg / np_shots leave out penalties (D15);
passes_injury_clearance (D13); open_play_passes_attempted and
team_open_play_passes_while_on (D12). A team open-play pass is credited to each
player of the passing team whose credited time holds its instant (pieces
half-open, a piece ending at its period's end closed there, judged by the event's
own period) and always to its passer, even outside his own credited time (a
timeline inconsistency, reported as events_outside_credited_time); every pass must
be credited to 7-11 players (the Laws minimum on the pitch), otherwise the spike
fails. Only progressive_passes may change against an
existing core section; every other value must come out identical.

Scope rule shared by every column: period-5 (shootout) events are excluded
(ADR 002 §3). Events are counted league-wide, not per output row: a dropped
event (other than an allow-listed bench card, which is not a counted type)
would make the pipeline total fall short.

Minutes structural check (not a second implementation)
------------------------------------------------------
Per team, per period, at most 11 players are on the pitch, so the team's
minutes inside period p are <= 11 * length(p) (ADR 002 §31.5). Per league:
    summed minutes ~= sum(11 * match_duration) - exit losses - treatment windows
where an exit loss is, on the elapsed clock, (the player's own substitution,
or match_end) - exit, for a dismissal or unpaired Player Off before his own
substitution (§31.6). A treatment window is clipped at the player's exit, so
time after the exit is never deducted twice (a red card inside a window that
later closes). The expectation uses event timestamps only, never the
lineup stints. It cannot see a player who left injured with no substitutes
left and no Player Off event, or small gaps between a sub-off and a sub-on,
so the league figure has a tolerance (LEAGUE_TOLERANCE_SHARE). A second
identity: one goalkeeper per team at all times, so league GK minutes are
about 2 * sum(match_duration) (GK_TOLERANCE_SHARE).

Events outside credited time (report-only)
------------------------------------------
An event by a player (periods 1-4) whose instant lies outside his credited
time (the union of his pieces, closed at both ends), except the event types
that do not need him on the pitch (OFF_PITCH_EVENT_TYPES plus Player Off and
Player On). Counted per event: `events_outside_credited_time`. 2015/16: PL 3,
La Liga 1, Serie A 11 (Milinković-Savić 3879637, ADR 002 §31.6).

Event-position oracle (external to the §31 text)
------------------------------------------------
The spike re-implements the written rule, so it cannot catch a wrong rule.
In every match, each event's `position` group is compared with the player's
timeline group at the event's instant (events up to his exit). A player-match
below ORACLE_MIN_AGREEMENT fails. A second, minutes-based check rebuilds each
player's position timeline from the tactics events (Starting XI, Tactical
Shift, Substitution) and fails a player-match with more than
TACTICS_MAX_DISAGREE_MINUTES disagreeing minutes. Both share
ORACLE_EXCEPTIONS (reasons in ADR 002 §31.6). Neither is fully external:
event positions, tactics and lineups all come from StatsBomb's tactics feed,
so the checks prove consistency, not truth.
"""
from __future__ import annotations

import json
import math
from collections import Counter

from reference_totals import (
    DATA_ROOT,
    REFERENCE_DIR,
    competition_label,
    load_matches,
    parse_args,
    reconcile_goals,
    write_reference,
)

# ADR 002 §1 (progressive pass) and §2 (progressive carry), StatsBomb units.
DEFENSIVE_40_X = 48.0
PROG_PASS_UNITS = 10.0
PROG_CARRY_UNITS = 5.0
PROG_CARRY_MIN_LENGTH = 5.0
GOAL_CENTRE = (120.0, 40.0)
# ADR 004 D12: an open-play pass is credited to 7-11 players of its team. 7 is the
# Laws of the Game minimum on the pitch (2015/16 observed: 8-11), so a pass credited
# to fewer (e.g. only its passer) is a timeline or clock error, not football.
MIN_PLAYERS_PER_PASS = 7
MAX_PLAYERS_PER_PASS = 11
# ADR 004 D14: restarts, excluded from progressive passes and the open-play columns.
RESTART_TYPES = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off", "Throw-in"})
BOX_X_MIN, BOX_Y_MIN, BOX_Y_MAX = 102.0, 18.0, 62.0

# ADR 002 §30: exact StatsBomb casing ("Saved to Post", lower-case "to").
ON_TARGET = {"Goal", "Saved", "Saved to Post"}
# tackles.py documented outcome mapping (the ADR does not list it).
TACKLE_WON = {"Won", "Success In Play", "Success Out"}
# ADR 002 §3: dismissal cards, from Bad Behaviour or Foul Committed.
DISMISSAL_CARDS = {"Red Card", "Second Yellow"}

PER_TEAM_TOLERANCE_MIN = 1e-6  # float noise only
LEAGUE_TOLERANCE_SHARE = 1e-5  # 0.001% of the league's minutes (observed ≤ 0.0005%)
# GK identity (§31.6): one goalkeeper per team at all times, so league GK minutes
# ≈ 2 × Σ match_duration. Observed gaps: GK treatment windows and GK subs with a
# gap (16 team-matches, ≤ 3.25 min) and one lineup excess (3754157, 2.27 min).
GK_TOLERANCE_SHARE = 5e-4

# ADR 002 §31.1: the period clock.
PERIOD_START = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}
NOMINAL_END = {1: 45.0, 2: 90.0, 3: 105.0, 4: 120.0}
PERIOD_SHORT_FAIL_MINUTES = 5.0  # config default (§31.1)
HALF_END_AGREE_MINUTES = 1 / 60  # Half End events of one period: within 1 s
TIME_EPS = 1e-9

# ADR 002 §4: StatsBomb position → group (the taxonomy table).
POSITION_GROUP = {
    "Goalkeeper": "GK",
    **dict.fromkeys(["Center Back", "Left Center Back", "Right Center Back"], "CB"),
    **dict.fromkeys(["Left Back", "Right Back", "Left Wing Back", "Right Wing Back"], "FB-WB"),
    **dict.fromkeys([
        "Defensive Midfield", "Center Defensive Midfield", "Left Defensive Midfield",
        "Right Defensive Midfield", "Center Midfield", "Left Center Midfield",
        "Right Center Midfield"], "DM-CM"),
    **dict.fromkeys([
        "Attacking Midfield", "Center Attacking Midfield", "Left Midfield", "Right Midfield",
        "Left Wing", "Right Wing", "Left Attacking Midfield", "Right Attacking Midfield"],
        "AM-Winger"),
    **dict.fromkeys([
        "Center Forward", "Left Center Forward", "Right Center Forward", "Secondary Striker"],
        "ST"),
}
GROUPS = sorted(set(POSITION_GROUP.values()))
GROUP_COLUMN = {g: "minutes_" + g.lower().replace("-", "_") for g in GROUPS}

# Event-position oracle (§31.6): a player-match fails when fewer than this share
# of his positioned events agree with his timeline group at the event's instant.
ORACLE_MIN_AGREEMENT = 0.90
# Tactics-timeline check (§31.6): a player-match fails when more than this many
# of his minutes disagree with the group from the tactics events.
TACTICS_MAX_DISAGREE_MINUTES = 5.0
# Exceptions: (match_id, player_id) → reason, as listed in ADR 002 §31.6.
_DOUBLE_SHIFT = "two Tactical Shift events at one instant; lineup keeps the first"
_MISSING_SHIFT = "shift back to the sub-on slot position has no lineup stint"
ORACLE_EXCEPTIONS: dict[tuple[int, int], str] = {
    (3754226, 3532): _DOUBLE_SHIFT,  # Henderson
    (3754226, 3501): _DOUBLE_SHIFT,  # Coutinho
    (3754226, 3535): _DOUBLE_SHIFT,  # Firmino
    (3754226, 3380): _DOUBLE_SHIFT,  # Benteke
    (3754226, 4090): _DOUBLE_SHIFT,  # Lallana
    (3900509, 7347): _MISSING_SHIFT,  # Polomat
    (3829474, 4441): _MISSING_SHIFT,  # Ketkeophomphone
    (3879816, 15780): _MISSING_SHIFT,  # Totti
    (3878543, 25345): _MISSING_SHIFT,  # Hernanes
}

Instant = tuple[int, float]

# §31.2: event types by a player that may follow his unpaired Player Off. The
# player of a Substitution event is the one taken off, so it is his own.
OFF_PITCH_EVENT_TYPES = frozenset({"Bad Behaviour", "Substitution"})
# events_outside_credited_time: types that do not need the player on the pitch.
OUTSIDE_TIME_EXEMPT_TYPES = OFF_PITCH_EVENT_TYPES | {"Player Off", "Player On"}

# Column → kind, reported with the totals.
KINDS = {
    "minutes_played": "second implementation (ADR 002 §31) + structural check",
    "shots": "independent", "shots_on_target": "second implementation (§30)",
    "xg": "independent", "passes_attempted": "independent",
    "passes_completed": "independent (no pass.outcome)",
    "progressive_passes": "second implementation (§1)",
    "progressive_carries": "second implementation (§2)",
    "key_passes": "independent (StatsBomb flags, §8)",
    "assists": "independent (StatsBomb flag, §8)",
    "tackles_attempted": "independent", "tackles_won": "second implementation (outcome list)",
    "dribbles_attempted": "independent", "dribbles_completed": "independent (outcome Complete)",
    "interceptions": "independent", "clearances": "independent",
    "blocks": "second implementation (§13)",
    "fouls_committed": "independent", "fouls_won": "independent", "carries": "independent",
    "np_xg": "independent (shot.type, ADR 004 D15)",
    "np_shots": "independent (shot.type, ADR 004 D15)",
    "passes_injury_clearance": "independent (pass.outcome, ADR 004 D13)",
    "open_play_passes_attempted": "independent (pass.type, ADR 004 D12)",
    "team_open_play_passes_while_on": "second implementation (ADR 004 D12) + 7-11 credit check",
    **{col: "second implementation (§31.4) + event-position oracle"
       for col in GROUP_COLUMN.values()},
}


def _mmss(s: str) -> float:
    """'MM:SS', 'MM:SS.mmm' or 'H:MM:SS' → minutes (ADR 002 §3)."""
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + parts[2] / 60
    return parts[0] + parts[1] / 60


def _dist_to_goal(p: list[float]) -> float:
    return math.hypot(GOAL_CENTRE[0] - p[0], GOAL_CENTRE[1] - p[1])


def _in_box(p: list[float]) -> bool:
    return p[0] >= BOX_X_MIN and BOX_Y_MIN <= p[1] <= BOX_Y_MAX


def _progressive_pass(start: list[float], end: list[float]) -> bool:
    """ADR 002 §1, for a completed pass."""
    if start[0] < DEFENSIVE_40_X:
        return False
    closer = _dist_to_goal(start) - _dist_to_goal(end)
    return closer >= PROG_PASS_UNITS or (not _in_box(start) and _in_box(end))


def _progressive_carry(start: list[float], end: list[float]) -> bool:
    """ADR 002 §2."""
    closer = _dist_to_goal(start) - _dist_to_goal(end)
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    return closer >= PROG_CARRY_UNITS and length >= PROG_CARRY_MIN_LENGTH


def _card(e: dict) -> str | None:
    for key in ("bad_behaviour", "foul_committed"):
        name = ((e.get(key) or {}).get("card") or {}).get("name")
        if name:
            return name
    return None


def count_events(events: list[dict], t: Counter) -> None:
    """Add one match's period 1-4 event counts to `t`."""
    for e in events:
        if e["period"] == 5 or not e.get("player"):
            continue
        kind = e["type"]["name"]
        if kind == "Pass":
            p = e.get("pass") or {}
            t["passes_attempted"] += 1
            outcome = (p.get("outcome") or {}).get("name")
            restart = (p.get("type") or {}).get("name") in RESTART_TYPES
            if not restart:
                t["open_play_passes_attempted"] += 1
            if outcome == "Injury Clearance":
                t["passes_injury_clearance"] += 1
            if outcome is None:
                t["passes_completed"] += 1
                if not restart and _progressive_pass(e["location"], p["end_location"]):
                    t["progressive_passes"] += 1
            if p.get("shot_assist") or p.get("goal_assist"):
                t["key_passes"] += 1
            if p.get("goal_assist"):
                t["assists"] += 1
        elif kind == "Shot":
            s = e.get("shot") or {}
            t["shots"] += 1
            if (s.get("outcome") or {}).get("name") in ON_TARGET:
                t["shots_on_target"] += 1
            t["xg"] += s["statsbomb_xg"]  # a missing xG raises: H1.4 says 0 occur
            if (s.get("type") or {}).get("name") != "Penalty":
                t["np_shots"] += 1
                t["np_xg"] += s["statsbomb_xg"]
        elif kind == "Carry":
            t["carries"] += 1
            if _progressive_carry(e["location"], e["carry"]["end_location"]):
                t["progressive_carries"] += 1
        elif kind == "Duel":
            d = e.get("duel") or {}
            if (d.get("type") or {}).get("name") == "Tackle":
                t["tackles_attempted"] += 1
                if (d.get("outcome") or {}).get("name") in TACKLE_WON:
                    t["tackles_won"] += 1
        elif kind == "Dribble":
            t["dribbles_attempted"] += 1
            if ((e.get("dribble") or {}).get("outcome") or {}).get("name") == "Complete":
                t["dribbles_completed"] += 1
        elif kind == "Block":
            b = e.get("block") or {}
            if not b.get("offensive") and not b.get("save_block"):
                t["blocks"] += 1
        elif kind in ("Interception", "Clearance", "Foul Committed", "Foul Won"):
            t[{"Interception": "interceptions", "Clearance": "clearances",
               "Foul Committed": "fouls_committed", "Foul Won": "fouls_won"}[kind]] += 1


def _at(e: dict) -> Instant:
    """An event's instant: (period, minute + second / 60) (§31.1)."""
    return (e["period"], e["minute"] + e["second"] / 60)


class Clock:
    """The §31.1 period clock: each period ends at its Half End event."""

    def __init__(self, mid: int, play: list[dict]) -> None:
        if not play:
            raise SystemExit(f"FAIL: match {mid}: no events in periods 1-4 (no playing time)")
        periods = sorted({e["period"] for e in play})
        if periods != list(range(1, periods[-1] + 1)):
            raise SystemExit(f"FAIL: match {mid}: playing periods {periods} are not 1..max")
        self.end: dict[int, float] = {}
        for p in periods:
            half_ends = [_at(e)[1] for e in play
                         if e["period"] == p and e["type"]["name"] == "Half End"]
            if not half_ends:
                raise SystemExit(f"FAIL: match {mid}: period {p} has no Half End")
            if max(half_ends) - min(half_ends) > HALF_END_AGREE_MINUTES + TIME_EPS:
                raise SystemExit(f"FAIL: match {mid}: period {p} Half End events disagree "
                                 f"{sorted(half_ends)}")
            self.end[p] = max(half_ends)
            shortfall = NOMINAL_END[p] - self.end[p]
            if shortfall > PERIOD_SHORT_FAIL_MINUTES:
                raise SystemExit(f"FAIL: match {mid}: period {p} is {shortfall:.2f} min short "
                                 "(period_too_short)")
        self.short = {p: NOMINAL_END[p] - self.end[p] for p in periods
                      if self.end[p] < NOMINAL_END[p]}
        self.offset: dict[int, float] = {}
        elapsed = 0.0
        for p in periods:
            self.offset[p] = elapsed
            elapsed += self.length(p)
        self.duration = elapsed
        self.match_end: Instant = (periods[-1], self.end[periods[-1]])

    def length(self, p: int) -> float:
        return self.end[p] - PERIOD_START[p]

    def elapsed(self, at: Instant) -> float:
        p, m = at
        return self.offset[p] + min(max(m, PERIOD_START[p]), self.end[p]) - PERIOD_START[p]

    def period_of(self, t: float) -> int:
        """The period whose elapsed range holds t (t strictly inside a segment)."""
        for p in sorted(self.offset, reverse=True):
            if t >= self.offset[p]:
                return p
        raise AssertionError(t)


def _exits(mid: int, play: list[dict]) -> tuple[dict, dict, dict, dict]:
    """
    Per player (§31.2, §31.3): first dismissal, first substitution off, unpaired
    Player Off, and the treatment windows, each as instants. Fails on any event
    after an unpaired Player Off except a Bad Behaviour card (any colour: a card
    can be shown to a player off the pitch) and his own Substitution event (an
    injured player is formally replaced after he left), and on two Player Off
    events in a row.
    """
    dismissal: dict[int, Instant] = {}
    sub_off: dict[int, Instant] = {}
    open_off: dict[int, tuple[Instant, int]] = {}
    windows: dict[int, list[tuple[Instant, Instant]]] = {}
    for e in sorted(play, key=lambda e: e["index"]):
        pid = (e.get("player") or {}).get("id")
        if pid is None:
            continue
        if _card(e) in DISMISSAL_CARDS:
            dismissal.setdefault(pid, _at(e))
        kind = e["type"]["name"]
        if kind == "Substitution":
            sub_off.setdefault(pid, _at(e))
        elif kind == "Player Off":
            if pid in open_off:
                raise SystemExit(f"FAIL: match {mid}: player {pid} has two Player Off in a row")
            open_off[pid] = (_at(e), e["index"])
        elif kind == "Player On" and pid in open_off:
            off_at, _ = open_off.pop(pid)
            windows.setdefault(pid, []).append((off_at, _at(e)))
    unpaired = {pid: at for pid, (at, _) in open_off.items()}
    for e in play:
        pid = (e.get("player") or {}).get("id")
        if (pid in open_off and e["index"] > open_off[pid][1]
                and e["type"]["name"] not in OFF_PITCH_EVENT_TYPES):
            raise SystemExit(f"FAIL: match {mid}: event {e['type']['name']} by player {pid} "
                             "after his unpaired Player Off (event_after_permanent_off)")
    return dismissal, sub_off, unpaired, windows


def _subtract(a: float, b: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[a, b) minus every [c, d) in cuts."""
    pieces = [(a, b)]
    for c, d in cuts:
        nxt = []
        for x, y in pieces:
            if d <= x or c >= y:
                nxt.append((x, y))
                continue
            if c > x:
                nxt.append((x, c))
            if d < y:
                nxt.append((d, y))
        pieces = nxt
    return [(x, y) for x, y in pieces if y > x]


Marker = tuple[float, Instant, bool, int, str]


def player_minutes(
    mid: int, positions: list[dict], clock: Clock, exit_at: Instant | None,
    windows: list[tuple[Instant, Instant]], counts: Counter,
) -> tuple[dict[str, float], dict[int, float], float, list[Marker], list[tuple[float, float, str]]]:
    """
    §31.4 for one player: (minutes per group, minutes per period, overlap,
    position markers, timeline segments (start, end, group)). Raises
    SystemExit on a clamp-guard violation (§31.1).
    """
    cuts = [(clock.elapsed(a), clock.elapsed(b)) for a, b in windows]
    pieces: list[tuple[float, float]] = []
    # (elapsed(start), start instant, started by a Tactical Shift, -index, group):
    # max() gives the §31.4 position.
    markers: list[Marker] = []
    cut_windows: set[int] = set()  # windows that remove time from a stint
    for i, s in enumerate(positions):
        fp, f = s["from_period"], _mmss(s["from"])
        if f < PERIOD_START[fp] - TIME_EPS:
            raise SystemExit(f"FAIL: match {mid}: stint from {s['from']} before period {fp} "
                             "start (stint_time_before_period_start)")
        if f > clock.end[fp] + TIME_EPS:
            counts["stint_after_period_end"] += 1
        start: Instant = (fp, f)
        if s.get("to"):
            if s.get("to_period") is None:
                raise SystemExit(f"FAIL: match {mid}: stint {s['from']}-{s['to']} has no to_period")
            tp, t = s["to_period"], _mmss(s["to"])
            if t < PERIOD_START[tp] - TIME_EPS:
                raise SystemExit(f"FAIL: match {mid}: stint to {s['to']} before period {tp} "
                                 "start (stint_time_before_period_start)")
            if t > clock.end[tp] + TIME_EPS:
                raise SystemExit(f"FAIL: match {mid}: stint to {s['to']} after period {tp} "
                                 "end (stint_end_after_period_end)")
            end: Instant = (tp, t)
        else:
            end = clock.match_end
        if exit_at is not None:
            end = min(end, exit_at)
        group = POSITION_GROUP.get(s["position"])
        if group is None:
            raise SystemExit(f"FAIL: match {mid}: unmapped position {s['position']!r}")
        a, b = clock.elapsed(start), clock.elapsed(end)
        shift = (s.get("start_reason") or "").startswith("Tactical Shift")
        markers.append((a, start, shift, -i, group))
        if b > a:
            cut_windows.update(j for j, (c, d) in enumerate(cuts) if min(b, d) - max(a, c) > TIME_EPS)
            pieces.extend(_subtract(a, b, cuts))
    counts["treatment_window_inside_stint"] += len(cut_windows)

    bounds = sorted({x for piece in pieces for x in piece}
                    | {m[0] for m in markers} | set(clock.offset.values()))
    by_group: dict[str, float] = {}
    by_period: dict[int, float] = {}
    segments: list[tuple[float, float, str]] = []
    for a, b in zip(bounds, bounds[1:]):
        mid_t = (a + b) / 2
        if not any(x <= mid_t < y for x, y in pieces):
            continue
        group = position_at(markers, mid_t)
        segments.append((a, b, group))
        by_group[group] = by_group.get(group, 0.0) + (b - a)
        p = clock.period_of(mid_t)
        by_period[p] = by_period.get(p, 0.0) + (b - a)
    overlap = sum(y - x for x, y in pieces) - sum(by_group.values())
    return by_group, by_period, overlap, markers, segments


def position_at(markers: list[Marker], t: float) -> str:
    """
    §31.4: the group of the stint with the greatest elapsed start ≤ t; on equal
    elapsed starts the later start instant; on equal start instants a stint
    started by a Tactical Shift, then the stint listed first in the lineup.
    """
    return max(m for m in markers if m[0] <= t + TIME_EPS)[4]


def match_minutes(
    mid: int, events: list[dict], lineups: list[dict], counts: Counter,
) -> tuple[list[dict], Clock]:
    """
    ADR 002 §31, re-implemented. One dict per team: summed minutes, minutes per
    group, minutes per period with the per-period ceiling, the structural
    expectation, the dismissal count and the per-player rows for the oracle.
    """
    play = [e for e in events if e["period"] <= 4]
    clock = Clock(mid, play)
    counts["period_end_before_nominal"] += len(clock.short)
    dismissal, sub_off, unpaired, windows = _exits(mid, play)
    for pid, card in dismissal.items():
        if card > clock.match_end:
            counts["dismissal_after_match_end"] += 1
        elif card[1] > clock.end[card[0]] + TIME_EPS:
            counts["dismissal_after_period_end"] += 1

    teams = []
    for team in lineups:
        summed, losses, window_time, n_dismissed = 0.0, 0.0, 0.0, 0
        by_group: Counter = Counter()
        by_period: Counter = Counter()
        players = []
        for pl in team["lineup"]:
            pid = pl["player_id"]
            card, sub, off = dismissal.get(pid), sub_off.get(pid), unpaired.get(pid)
            exits = [x for x in (card, sub, off) if x is not None]
            exit_at = min(exits) if exits else None
            groups, periods, overlap, markers, segments = player_minutes(
                mid, pl["positions"], clock, exit_at, windows.get(pid, []), counts)
            minutes = sum(groups.values())
            if minutes > clock.duration + PER_TEAM_TOLERANCE_MIN:
                raise SystemExit(f"FAIL: match {mid}: player {pid} has {minutes:.3f} min "
                                 f"> duration {clock.duration:.3f}")
            if overlap > TIME_EPS:
                counts["overlapping_stints"] += 1
                counts["overlap_minutes"] += overlap
            if off is not None:
                without_off = [x for x in (card, sub) if x is not None]
                uncapped, *_ = player_minutes(
                    mid, pl["positions"], clock, min(without_off) if without_off else None,
                    windows.get(pid, []), Counter())
                if sum(uncapped.values()) - minutes > TIME_EPS:
                    counts["unpaired_player_off_capped"] += 1
                    counts["unpaired_off_minutes"] += sum(uncapped.values()) - minutes
            summed += minutes
            by_group.update(groups)
            by_period.update(periods)
            if minutes > 0:
                players.append({"player_id": pid, "player": pl["player_name"],
                                "markers": markers, "segments": segments, "exit": exit_at})
            if card is not None and pl["positions"]:
                n_dismissed += 1
            # Exit loss (§31.6): a dismissal or unpaired Off before his own substitution.
            non_sub = [x for x in (card, off) if x is not None]
            if pl["positions"] and non_sub and (sub is None or min(non_sub) < sub):
                until = clock.elapsed(sub) if sub is not None else clock.duration
                losses += max(0.0, until - clock.elapsed(min(non_sub)))
            for a, b in windows.get(pid, []):
                # Clipped at the exit: time after it is already an exit loss.
                if exit_at is not None:
                    b = min(b, exit_at)
                window_time += max(0.0, clock.elapsed(b) - clock.elapsed(a))
        for p, used in by_period.items():
            if used > 11 * clock.length(p) + PER_TEAM_TOLERANCE_MIN:
                raise SystemExit(f"FAIL: match {mid} team {team['team_id']}: period {p} "
                                 f"minutes {used:.3f} > 11 × {clock.length(p):.3f} "
                                 "(team_minutes_exceed_ceiling)")
        teams.append({
            "team_id": team["team_id"], "summed": summed, "by_group": by_group,
            "ceiling": 11 * clock.duration, "duration": clock.duration,
            "expected": 11 * clock.duration - losses - window_time,
            "dismissed": n_dismissed, "players": players,
        })
    return teams, clock


def team_open_play_pass_credits(
    mid: int, events: list[dict], teams: list[dict], clock: Clock, credits: Counter,
) -> int:
    """
    ADR 004 D12: Σ over the match's open-play passes (periods 1-4) of the number of
    players credited with each, i.e. the league total of team_open_play_passes_while_on.
    A player of the passing team (`team.id`) is credited when the pass's elapsed
    instant t lies in one of his timeline segments [a, b) inside the pass's own
    period, or t is that period's end and a segment ends there (all comparisons
    within TIME_EPS); the passer is always credited, even outside his own
    credited time (a timeline inconsistency, reported as
    events_outside_credited_time). Fails unless every pass is
    credited to MIN_PLAYERS_PER_PASS-MAX_PLAYERS_PER_PASS players. `credits` gets the
    distribution of players per pass.
    """
    segments = {t["team_id"]: {p["player_id"]: p["segments"] for p in t["players"]}
                for t in teams}
    total = 0
    for e in events:
        if (e["period"] > 4 or e["type"]["name"] != "Pass" or not e.get("player")
                or ((e.get("pass") or {}).get("type") or {}).get("name") in RESTART_TYPES):
            continue
        p = e["period"]
        start = clock.offset[p]
        end = start + clock.length(p)
        at = clock.elapsed(_at(e))
        passer = e["player"]["id"]
        n = 0
        for pid, segs in segments[e["team"]["id"]].items():
            if pid == passer:
                continue
            # Compared within TIME_EPS: elapsed() and the segment bounds are built by
            # different float sums (e.g. 48.633333333333326 vs 48.63333333333333 at a
            # second-half kick-off). Zero-length segments credit nothing.
            if any(b - a > TIME_EPS
                   and ((a - TIME_EPS <= at < b - TIME_EPS
                         and start - TIME_EPS <= at < end - TIME_EPS)
                        or (abs(at - end) <= TIME_EPS and abs(b - end) <= TIME_EPS))
                   for a, b, _ in segs):
                n += 1
        n += 1  # the passer
        if not MIN_PLAYERS_PER_PASS <= n <= MAX_PLAYERS_PER_PASS:
            raise SystemExit(f"FAIL: match {mid}: pass {e['id']} credited to {n} players")
        credits[n] += 1
        total += n
    return total


def _group(mid: int, name: str | None, where: str) -> str:
    """§4 group of a position name; an unmapped or missing name fails (as in §31.4)."""
    group = POSITION_GROUP.get(name)
    if group is None:
        raise SystemExit(f"FAIL: match {mid}: unmapped position {name!r} in {where}")
    return group


def events_outside_credited_time(events: list[dict], teams: list[dict], clock: Clock) -> list[dict]:
    """
    Report-only: every period 1-4 event by a player, other than
    OUTSIDE_TIME_EXEMPT_TYPES, whose elapsed instant is outside his credited
    time (closed intervals). A player with no credited time (or not in the
    lineup) has every such event outside.
    """
    segments = {p["player_id"]: p["segments"] for t in teams for p in t["players"]}
    rows = []
    for e in events:
        pid = (e.get("player") or {}).get("id")
        if (e["period"] > 4 or pid is None
                or e["type"]["name"] in OUTSIDE_TIME_EXEMPT_TYPES):
            continue
        t = clock.elapsed(_at(e))
        if not any(a - TIME_EPS <= t <= b + TIME_EPS for a, b, _ in segments.get(pid, [])):
            rows.append({"player_id": pid, "player": e["player"].get("name"),
                         "type": e["type"]["name"], "at": _at(e)})
    return rows


def event_position_oracle(mid: int, events: list[dict], teams: list[dict], clock: Clock) -> list[dict]:
    """
    §31.6: per player-match, the share of his positioned events (from his first
    stint start to his exit) whose group equals his timeline group at the
    event's instant.
    """
    rows = []
    for team in teams:
        for p in team["players"]:
            n = agree = 0
            for e in events:
                if (e["period"] > 4 or (e.get("player") or {}).get("id") != p["player_id"]
                        or not e.get("position")):
                    continue
                at = _at(e)
                t = clock.elapsed(at)
                # Skip events after his exit or before his first stint starts.
                if (p["exit"] is not None and at > p["exit"]) or t < min(p["markers"])[0] - TIME_EPS:
                    continue
                n += 1
                agree += position_at(p["markers"], t) == _group(
                    mid, e["position"].get("name"), f"event {e['index']}")
            if n:
                rows.append({"match_id": mid, "player_id": p["player_id"], "player": p["player"],
                             "events": n, "agree": agree})
    return rows


def tactics_check(mid: int, events: list[dict], teams: list[dict], clock: Clock) -> list[dict]:
    """
    §31.6 minutes-based check: per player, a position timeline built from the
    events alone (Starting XI and Tactical Shift `tactics.lineup`, and the
    replaced player's position for a substitute), in event-index order.
    Returns, per player-match, the minutes of his lineup timeline whose group
    differs from it.
    """
    timeline: dict[int, list[tuple[float, int, str]]] = {}
    for e in sorted((e for e in events if e["period"] <= 4), key=lambda e: e["index"]):
        kind = e["type"]["name"]
        if kind in ("Starting XI", "Tactical Shift"):
            for x in e["tactics"]["lineup"]:
                timeline.setdefault(x["player"]["id"], []).append(
                    (clock.elapsed(_at(e)), e["index"],
                     _group(mid, x["position"].get("name"), f"tactics of event {e['index']}")))
        elif kind == "Substitution":
            timeline.setdefault(e["substitution"]["replacement"]["id"], []).append(
                (clock.elapsed(_at(e)), e["index"],
                 _group(mid, (e.get("position") or {}).get("name"),
                        f"substitution event {e['index']}")))
    rows = []
    for team in teams:
        for p in team["players"]:
            marks = timeline.get(p["player_id"], [])
            off = 0.0
            for a, b, group in p["segments"]:
                cuts = sorted({a, b} | {m[0] for m in marks if a < m[0] < b})
                for x, y in zip(cuts, cuts[1:]):
                    known = [m for m in marks if m[0] <= (x + y) / 2]
                    if not known or max(known)[2] != group:
                        off += y - x
            rows.append({"match_id": mid, "player_id": p["player_id"], "player": p["player"],
                         "disagree": off, "minutes": sum(b - a for a, b, _ in p["segments"])})
    return rows


def main() -> None:
    args = parse_args(__doc__.splitlines()[1])
    matches = load_matches(args.competition_id, args.season_id)
    label = competition_label(matches)
    print(f"{label}: {len(matches)} matches")

    totals: Counter = Counter()
    counts: Counter = Counter()
    all_events: list[dict] = []
    team_rows: list[dict] = []
    oracle_rows: list[dict] = []
    tactics_rows: list[dict] = []
    outside_rows: list[dict] = []
    match_duration = 0.0
    pass_credits: Counter = Counter()
    for m in matches:
        mid = m["match_id"]
        events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
        lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
        count_events(events, totals)
        teams, clock = match_minutes(mid, events, lineups, counts)
        match_duration += clock.duration
        totals["team_open_play_passes_while_on"] += team_open_play_pass_credits(
            mid, events, teams, clock, pass_credits)
        outside_rows.extend({"match_id": mid, **r}
                            for r in events_outside_credited_time(events, teams, clock))
        for t in teams:
            t["match_id"] = mid
            totals["minutes_played"] += t["summed"]
            for g, col in GROUP_COLUMN.items():
                totals[col] += t["by_group"].get(g, 0.0)
        team_rows.extend(teams)
        oracle_rows.extend(event_position_oracle(mid, events, teams, clock))
        tactics_rows.extend(tactics_check(mid, events, teams, clock))
        all_events.extend(e for e in events if e["period"] != 5)

    reconcile_goals(matches, all_events)

    print("\nReport-only counts (ADR 002 §31):")
    for key in ("period_end_before_nominal", "stint_after_period_end",
                "treatment_window_inside_stint", "overlapping_stints", "unpaired_player_off_capped",
                "dismissal_after_period_end", "dismissal_after_match_end"):
        print(f"  {key:<32} {counts[key]}")
    print(f"  {'events_outside_credited_time':<32} {len(outside_rows)}")
    for r in outside_rows:
        print(f"    match {r['match_id']} {r['player']} {r['type']} at {r['at'][0]}, "
              f"{r['at'][1]:.2f}")
    print(f"  {'overlap minutes deduplicated':<32} {counts['overlap_minutes']:.2f}")
    print(f"  {'unpaired-Off minutes removed':<32} {counts['unpaired_off_minutes']:.2f}")

    n_passes = sum(pass_credits.values())
    print(f"\nOpen-play passes credited (ADR 004 D12): {n_passes:,} passes; players per pass "
          + ", ".join(f"{k}: {v:,} ({v / n_passes:.2%})" for k, v in sorted(pass_credits.items())))

    # Structural minutes check (the per-period ceiling already ran per team).
    groups_sum = sum(totals[c] for c in GROUP_COLUMN.values())
    if abs(groups_sum - totals["minutes_played"]) > 1e-6:
        raise SystemExit(f"FAIL: Σ group minutes {groups_sum} != minutes {totals['minutes_played']}")
    expected = sum(t["expected"] for t in team_rows)
    gap = expected - totals["minutes_played"]
    print(
        f"\nMinutes: summed {totals['minutes_played']:,.3f}; "
        f"Σ(11×duration) {sum(t['ceiling'] for t in team_rows):,.3f}; "
        f"minus exit losses and treatment windows = {expected:,.3f}; "
        f"gap {gap:,.3f} ({gap / expected:.4%})"
    )
    worst = sorted(team_rows, key=lambda t: t["summed"] - t["expected"])
    for t in worst[:3] + worst[-3:]:
        print(f"  match {t['match_id']} team {t['team_id']}: summed {t['summed']:.2f} "
              f"expected {t['expected']:.2f} (Δ {t['summed'] - t['expected']:+.2f})")
    if abs(gap) > LEAGUE_TOLERANCE_SHARE * expected:
        raise SystemExit(f"FAIL: league minutes gap {gap:.3f} exceeds "
                         f"{LEAGUE_TOLERANCE_SHARE:.3%} of {expected:.3f}")

    # GK identity (§31.6): one goalkeeper per team on the pitch at all times.
    gk_expected = sum(t["duration"] for t in team_rows)
    gk_gap = gk_expected - totals[GROUP_COLUMN["GK"]]
    gk_over = [t for t in team_rows
               if t["by_group"].get("GK", 0.0) > t["duration"] + PER_TEAM_TOLERANCE_MIN]
    print(f"GK identity: GK minutes {totals[GROUP_COLUMN['GK']]:,.3f} vs Σ team durations "
          f"{gk_expected:,.3f}; gap {gk_gap:,.3f} ({gk_gap / gk_expected:.4%}); "
          f"team-matches above their duration: {len(gk_over)}")
    for t in gk_over:
        print(f"  match {t['match_id']} team {t['team_id']}: GK {t['by_group']['GK']:.2f} "
              f"> {t['duration']:.2f}")
    if abs(gk_gap) > GK_TOLERANCE_SHARE * gk_expected:
        raise SystemExit(f"FAIL: GK minutes gap {gk_gap:.3f} exceeds "
                         f"{GK_TOLERANCE_SHARE:.2%} of {gk_expected:.3f}")

    # Event-position oracle (§31.6).
    n_events = sum(r["events"] for r in oracle_rows)
    n_agree = sum(r["agree"] for r in oracle_rows)
    low = [r for r in oracle_rows if r["agree"] < ORACLE_MIN_AGREEMENT * r["events"]]
    print(f"\nEvent-position oracle: {len(oracle_rows)} player-matches, {n_events:,} events, "
          f"agreement {n_agree / n_events:.4%}; below {ORACLE_MIN_AGREEMENT:.0%}: {len(low)}")
    for r in low:
        key = (r["match_id"], r["player_id"])
        flag = f"EXCEPTION ({ORACLE_EXCEPTIONS[key]})" if key in ORACLE_EXCEPTIONS else "FAIL"
        print(f"  {flag}: match {r['match_id']} {r['player']} {r['agree']}/{r['events']}")
    unexplained = [r for r in low if (r["match_id"], r["player_id"]) not in ORACLE_EXCEPTIONS]

    off = sum(r["disagree"] for r in tactics_rows)
    big = [r for r in tactics_rows if r["disagree"] > TACTICS_MAX_DISAGREE_MINUTES]
    print(f"Tactics-timeline check: {off:,.2f} of {sum(r['minutes'] for r in tactics_rows):,.1f} "
          f"min disagree; player-matches over {TACTICS_MAX_DISAGREE_MINUTES:g} min: {len(big)}")
    for r in big:
        key = (r["match_id"], r["player_id"])
        flag = f"EXCEPTION ({ORACLE_EXCEPTIONS[key]})" if key in ORACLE_EXCEPTIONS else "FAIL"
        print(f"  {flag}: match {r['match_id']} {r['player']} {r['disagree']:.2f} min")
    unexplained += [r for r in big if (r["match_id"], r["player_id"]) not in ORACLE_EXCEPTIONS]
    if unexplained:
        raise SystemExit(f"FAIL: {len(unexplained)} player-matches fail the position checks")

    core = {col: (round(totals[col], 4)
                  if col in ("xg", "np_xg", "minutes_played") or col in GROUP_COLUMN.values()
                  else totals[col])
            for col in KINDS}
    print()
    for col, value in core.items():
        print(f"  {col:<22} {value:>14,}   {KINDS[col]}")
    dismissals = sum(t["dismissed"] for t in team_rows)
    print(f"  {'dismissals (base)':<22} {dismissals:>14,}   independent (on-pitch cards)")
    match_duration = round(match_duration, 4)
    print(f"  {'match_duration (base)':<22} {match_duration:>14,}   Σ length(p) per match (§31.1)")

    update_reference(args.competition_id, args.season_id, core, dismissals, match_duration,
                     write=not args.no_write)


# Plan 005 M4b: the only recorded core value allowed to change (ADR 004 D14).
CHANGED_BY_M4B = frozenset({"progressive_passes"})


def update_reference(
    competition_id: int, season_id: int, core: dict, dismissals: int, match_duration: float,
    *, write: bool,
) -> None:
    """
    Write the core section, base.dismissals and base.match_duration (Σ of the
    §31.1 match_duration over the league's matches, for the exact GK identity
    in tests/test_reference_totals.py). When the reference file already
    has a core section, every recorded value except those in CHANGED_BY_M4B, and
    the dismissal count, must come out exactly as recorded (M4b changes only the
    progressive-pass definition; new columns are added). A league with no
    reference file, or none of these sections yet, is written as new.
    """
    path = REFERENCE_DIR / f"{competition_id}_{season_id}.json"
    ref = json.loads(path.read_text()) if path.exists() else {}
    old_core, old_base = ref.get("core", {}), ref.get("base", {})
    changed = {col: (old, core.get(col)) for col, old in old_core.items()
               if col not in CHANGED_BY_M4B and core.get(col) != old}
    old_dismissals = old_base.get("dismissals")
    old_duration = old_base.get("match_duration")
    if (changed or (old_dismissals is not None and old_dismissals != dismissals)
            or (old_duration is not None and old_duration != match_duration)):
        raise SystemExit(f"FAIL: values outside {sorted(CHANGED_BY_M4B)} changed: {changed}; "
                         f"dismissals {old_dismissals} → {dismissals}; match_duration "
                         f"{old_duration} → {match_duration}")
    print()
    for col in core:
        old = old_core.get(col)
        if old is None or col in CHANGED_BY_M4B:
            delta = "new" if old is None else f"Δ {core[col] - old:+,}"
            print(f"  {col:<30} {old} → {core[col]:,} ({delta})")
    if write:
        write_reference(competition_id, season_id, "core", core)
        write_reference(competition_id, season_id, "base",
                        {**old_base, "dismissals": dismissals, "match_duration": match_duration})


if __name__ == "__main__":
    main()
