"""
Spec tests for the H4a independent re-implementation of period-aware minutes
(spikes/verify_core_metrics.py, ADR 002 §31). The spike produces the
core.minutes_played / core.minutes_<group> reference totals that the H4b
pipeline must match, so a bug in it would silently become the "truth".

Every expected value below is derived by hand from the ADR 002 §31 text on
synthetic matches. No data/ files are read.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import verify_core_metrics as spike  # noqa: E402

_MID = 1


# ---------------------------------------------------------------- builders
def _t(minutes: float) -> str:
    whole = int(minutes)
    return f"{whole:02d}:{round((minutes - whole) * 60):02d}"


class _Events:
    """Event list in index order; Half End events are added per period."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, period: int, minute: float, kind: str, pid: int | None = None,
            **extra) -> "_Events":
        whole = int(minute)
        e = {"index": len(self.items) + 1, "period": period, "minute": whole,
             "second": round((minute - whole) * 60), "type": {"name": kind}, **extra}
        if pid is not None:
            e["player"] = {"id": pid}
        self.items.append(e)
        return self

    def half_end(self, period: int, minute: float, second_team_offset_s: float = 0.0):
        self.add(period, minute, "Half End")
        self.add(period, minute + second_team_offset_s / 60, "Half End")
        return self

    def card(self, period: int, minute: float, pid: int, name: str = "Red Card"):
        return self.add(period, minute, "Bad Behaviour", pid,
                        bad_behaviour={"card": {"name": name}})


def _stint(position: str, frm: float, fp: int, to: float | None = None,
           tp: int | None = None, reason: str = "Starting XI") -> dict:
    return {"position": position, "from": _t(frm), "from_period": fp,
            "to": None if to is None else _t(to), "to_period": tp,
            "start_reason": reason}


def _standard_events(p1_end: float = 47.0, p2_end: float = 93.0) -> _Events:
    ev = _Events()
    ev.add(1, 0, "Pass", 999)
    ev.half_end(1, p1_end)
    ev.add(2, 45, "Pass", 999)
    ev.half_end(2, p2_end)
    return ev


def _run(events: list[dict], players: dict[int, list[dict]],
         counts: Counter | None = None) -> dict[int, dict]:
    """One team per player, so per-player totals come straight out of the team row."""
    lineups = [{"team_id": pid, "lineup": [
        {"player_id": pid, "player_name": f"p{pid}", "positions": positions}]}
        for pid, positions in players.items()]
    teams, _ = spike.match_minutes(_MID, events, lineups,
                                   counts if counts is not None else Counter())
    return {t["team_id"]: t for t in teams}


def _ordered(ev: _Events) -> list[dict]:
    """Re-index so that index order follows (period, minute, second)."""
    items = sorted(ev.items, key=lambda e: (e["period"], e["minute"], e["second"], e["index"]))
    for i, e in enumerate(items, start=1):
        e["index"] = i
    return items


# ---------------------------------------------------------------- tests
class TestSpikePeriodClock:
    """§31.1: each period ends at its Half End; elapsed time counts stoppage."""

    def test_first_half_stoppage_is_counted(self):
        """Match-absolute minutes lost the 2 min of first-half stoppage; §31 keeps them."""
        clock = spike.Clock(_MID, _standard_events().items)
        assert clock.duration == pytest.approx(47 + 48)
        assert clock.elapsed((2, 45.0)) == pytest.approx(47.0)
        assert clock.elapsed((2, 60.0)) == pytest.approx(62.0)
        # Clamp: an instant after a period's whistle sits at that period's end.
        assert clock.elapsed((1, 48.5)) == pytest.approx(47.0)

    def test_extra_time_periods_and_shootout_excluded(self):
        """Extra-time constants (only unit-tested per §31.1) and period 5 ignored."""
        ev = _standard_events(46.0, 92.0)
        ev.add(3, 90, "Pass", 999).half_end(3, 106.0)
        ev.add(4, 105, "Pass", 999).half_end(4, 121.5)
        ev.add(5, 120, "Shot", 999)  # shootout: no Half End needed
        events = _ordered(ev)
        clock = spike.Clock(_MID, [e for e in events if e["period"] <= 4])
        assert clock.duration == pytest.approx(46 + 47 + 16 + 16.5)
        assert clock.elapsed((3, 90.0)) == pytest.approx(93.0)
        assert clock.elapsed((4, 105.0)) == pytest.approx(109.0)
        teams = _run(events, {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(125.5)

    @pytest.mark.parametrize("case", ["no_half_end", "half_ends_disagree",
                                      "period_gap", "too_short"])
    def test_fail_loud_rules(self, case):
        ev = _Events()
        ev.add(1, 0, "Pass", 999).half_end(1, 46.0)
        if case == "no_half_end":
            ev.add(2, 45, "Pass", 999)
        elif case == "half_ends_disagree":
            ev.add(2, 45, "Pass", 999).half_end(2, 92.0, second_team_offset_s=2)
        elif case == "period_gap":
            ev.add(3, 90, "Pass", 999).half_end(3, 105.0)
        elif case == "too_short":
            ev.add(2, 45, "Pass", 999).half_end(2, 84.9)  # 5.1 min short of 90
        with pytest.raises(SystemExit):
            spike.Clock(_MID, ev.items)

    def test_slightly_short_period_is_kept_and_reported(self):
        """A period up to 5 min short is kept as recorded (report-only)."""
        ev = _Events()
        ev.add(1, 0, "Pass", 999).half_end(1, 44.0)
        ev.add(2, 45, "Pass", 999).half_end(2, 86.0)
        counts = Counter()
        teams = _run(ev.items, {1: [_stint("Center Back", 0, 1)]}, counts)
        assert teams[1]["summed"] == pytest.approx(44 + 41)
        assert counts["period_end_before_nominal"] == 2


class TestSpikeExitsAndWindows:
    """§31.2-31.3 on a 47 + 48 = 95 min match."""

    def test_substitution_splits_the_slot_on_the_elapsed_clock(self):
        ev = _standard_events()
        ev.add(2, 60, "Substitution", 1)
        teams = _run(_ordered(ev), {
            1: [_stint("Center Back", 0, 1, 60, 2)],
            2: [_stint("Center Back", 60, 2, reason="Substitution - On")],
        })
        assert teams[1]["summed"] == pytest.approx(62.0)
        assert teams[2]["summed"] == pytest.approx(33.0)

    def test_red_card_in_stoppage_caps_an_open_stint(self):
        ev = _standard_events()
        ev.card(1, 46.5, 1)
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(46.5)

    def test_second_yellow_from_foul_committed_is_an_exit(self):
        ev = _standard_events()
        ev.add(2, 70, "Foul Committed", 1, foul_committed={"card": {"name": "Second Yellow"}})
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(72.0)

    def test_interval_card_clamps_to_period_end_and_is_reported(self):
        ev = _standard_events()
        ev.card(1, 48.0, 1)  # after the first-half whistle at 47:00
        counts = Counter()
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]}, counts)
        assert teams[1]["summed"] == pytest.approx(47.0)
        assert counts["dismissal_after_period_end"] == 1

    def test_post_match_card_is_report_only_and_changes_no_minutes(self):
        ev = _standard_events()
        ev.card(2, 94.0, 1)
        counts = Counter()
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]}, counts)
        assert teams[1]["summed"] == pytest.approx(95.0)
        assert counts["dismissal_after_match_end"] == 1

    def test_unpaired_player_off_caps_minutes_whatever_its_label(self):
        """Boufal/Balmont pattern: no Player On afterwards, lineup stint left open."""
        ev = _standard_events()
        ev.add(2, 70, "Player Off", 1, player_off={"permanent": False})
        counts = Counter()
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]}, counts)
        assert teams[1]["summed"] == pytest.approx(72.0)
        assert counts["unpaired_player_off_capped"] == 1

    def test_event_after_unpaired_player_off_fails(self):
        ev = _standard_events()
        ev.add(2, 70, "Player Off", 1).add(2, 75, "Pass", 1)
        with pytest.raises(SystemExit):
            _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})

    def test_paired_off_on_inside_one_stint_subtracts_the_window(self):
        """Weber pattern: labelled permanent but returns, so it is a window, not an exit."""
        ev = _standard_events()
        ev.add(1, 20, "Player Off", 1, player_off={"permanent": True})
        ev.add(1, 22, "Player On", 1)
        counts = Counter()
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]}, counts)
        assert teams[1]["summed"] == pytest.approx(93.0)
        assert counts["treatment_window_inside_stint"] == 1

    def test_card_during_open_treatment_window_exits_at_the_off(self):
        """§31.3: no Player On before the card, so the Off is unpaired and earlier.
        The card is a Bad Behaviour event, which §31.2 exempts from the guard."""
        ev = _standard_events()
        ev.add(1, 30, "Player Off", 1)
        ev.card(1, 31, 1)
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(30.0)

    def test_yellow_bad_behaviour_card_after_unpaired_off_is_allowed(self):
        """§31.2: any-colour Bad Behaviour card is exempt; the exit stays the Off."""
        ev = _standard_events()
        ev.add(2, 70, "Player Off", 1)
        ev.card(2, 80, 1, name="Yellow Card")
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(47 + 25)

    def test_foul_committed_card_after_unpaired_off_fails(self):
        """§31.2: only Bad Behaviour is exempt. A Foul Committed event, even one
        carrying a card, after the unpaired Off fails (event_after_permanent_off)."""
        ev = _standard_events()
        ev.add(2, 70, "Player Off", 1)
        ev.add(2, 80, "Foul Committed", 1, foul_committed={"card": {"name": "Red Card"}})
        with pytest.raises(SystemExit, match="event_after_permanent_off"):
            _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})

    def test_two_player_off_in_a_row_fails(self):
        """§31.3: a second Player Off before a Player On fails the build."""
        ev = _standard_events()
        ev.add(1, 20, "Player Off", 1).add(1, 21, "Player Off", 1).add(1, 22, "Player On", 1)
        with pytest.raises(SystemExit, match="two Player Off"):
            _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})

    def test_window_spanning_two_stints_counts_once(self):
        """treatment_window_inside_stint counts distinct windows, not stint cuts."""
        ev = _standard_events()
        ev.add(1, 20, "Player Off", 1).add(1, 22, "Player On", 1)
        counts = Counter()
        teams = _run(_ordered(ev), {1: [
            _stint("Center Back", 0, 1, 21, 1),
            _stint("Left Back", 21, 1, reason="Tactical Shift"),
        ]}, counts)
        assert teams[1]["summed"] == pytest.approx(93.0)
        assert counts["treatment_window_inside_stint"] == 1

    @pytest.mark.parametrize("kind", ["red_card", "substitution"])
    def test_shootout_card_or_substitution_is_not_an_exit(self, kind):
        """§31.1: period 5 is not playing time, so a shootout red card or
        substitution caps nothing (the known shootout-card lesson)."""
        ev = _standard_events()
        if kind == "red_card":
            ev.add(5, 121, "Bad Behaviour", 1, bad_behaviour={"card": {"name": "Red Card"}})
        else:
            ev.add(5, 121, "Substitution", 1)
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(95.0)

    def test_match_without_playing_time_fails_clearly(self):
        """§31.1: no period 1-4 events is a named failure, not an IndexError."""
        ev = _Events().add(5, 121, "Shot", 999)
        with pytest.raises(SystemExit, match="no events in periods 1-4"):
            _run(ev.items, {1: [_stint("Center Back", 0, 1)]})


class TestSpikeStintsAndPositions:
    """§31.4-31.5 on a 47 + 48 = 95 min match."""

    def test_overlapping_stints_are_counted_once(self):
        counts = Counter()
        teams = _run(_standard_events().items, {1: [
            _stint("Center Back", 0, 1, 50, 2),
            _stint("Center Back", 48, 2, reason="Tactical Shift"),
        ]}, counts)
        assert teams[1]["summed"] == pytest.approx(95.0)
        assert counts["overlapping_stints"] == 1

    @pytest.mark.parametrize("shift_first", [True, False])
    def test_same_instant_tactical_shift_wins_regardless_of_order(self, shift_first):
        """Ravel Morrison pattern: the shift describes the shape after the sub."""
        slot = _stint("Left Back", 60, 2, reason="Substitution - On")
        shift = _stint("Center Forward", 60, 2, reason="Tactical Shift")
        teams = _run(_standard_events().items,
                     {1: [shift, slot] if shift_first else [slot, shift]})
        assert dict(teams[1]["by_group"]) == pytest.approx({"ST": 33.0})

    def test_same_instant_without_shift_first_listed_wins(self):
        teams = _run(_standard_events().items, {1: [
            _stint("Goalkeeper", 60, 2, 60, 2, reason="Substitution - On"),
            _stint("Right Midfield", 60, 2, reason="Substitution - On"),
        ]})
        assert dict(teams[1]["by_group"]) == pytest.approx({"GK": 33.0})

    def test_equal_elapsed_start_later_instant_wins(self):
        """(1, 47:30) is clamped to elapsed 47 = elapsed (2, 45:00); the later instant
        wins even though it is listed second (no data case; H4b must test it too)."""
        counts = Counter()
        teams = _run(_standard_events().items, {1: [
            _stint("Center Back", 0, 1, 47, 1),
            _stint("Right Midfield", 47.5, 1),
            _stint("Center Forward", 45, 2),
        ]}, counts)
        assert dict(teams[1]["by_group"]) == pytest.approx({"CB": 47.0, "ST": 48.0})
        assert counts["stint_after_period_end"] == 1

    def test_reversed_period_stint_carries_the_second_half_position(self):
        """Ramsey/Pléa pattern: from (2, 46:00) to (1, 46:30) is empty but its start
        still marks the second-half position change."""
        teams = _run(_standard_events().items, {1: [
            _stint("Center Back", 0, 1, 46.5, 1),
            _stint("Center Forward", 46, 2, 46.5, 1, reason="Tactical Shift"),
            _stint("Left Back", 46.5, 1, reason="Tactical Shift"),
        ]})
        assert teams[1]["summed"] == pytest.approx(95.0)
        assert dict(teams[1]["by_group"]) == pytest.approx(
            {"CB": 46.5, "FB-WB": 1.5, "ST": 47.0})

    @pytest.mark.parametrize("stint", [
        _stint("Center Back", 0, 1, 48, 1),   # to after the 47:00 whistle
        _stint("Center Back", 44, 2),         # from before period 2 starts
    ], ids=["end_after_period_end", "time_before_period_start"])
    def test_clamp_guards_fail(self, stint):
        with pytest.raises(SystemExit):
            _run(_standard_events().items, {1: [stint]})

    def test_twelve_players_in_a_period_exceed_the_team_ceiling(self):
        lineups = [{"team_id": 1, "lineup": [
            {"player_id": pid, "player_name": f"p{pid}",
             "positions": [_stint("Center Back", 0, 1)]} for pid in range(1, 13)]}]
        with pytest.raises(SystemExit):
            spike.match_minutes(_MID, _standard_events().items, lineups, Counter())


# ------------------------------------------------- reconciliation and oracles
def _match(events: list[dict], lineups: list[dict]):
    return spike.match_minutes(_MID, events, lineups, Counter())


def _team(team_id: int, players: dict[int, list[dict]]) -> dict:
    return {"team_id": team_id, "lineup": [
        {"player_id": pid, "player_name": f"p{pid}", "positions": positions}
        for pid, positions in players.items()]}


def _pos(name: str) -> dict:
    return {"position": {"name": name}}


class TestSpikeExitOrderAndExpectedMinutes:
    """§31.2 exit order and the §31.6 league expectation, on a 47 + 48 = 95 min match."""

    def test_exit_is_earliest_by_instant_not_by_match_absolute_minute(self):
        """A second yellow at (1, 46:30) precedes a substitution at (2, 45:30), even
        though 46.5 > 45.5 on the old match-absolute axis. Exit = card: 46.5 min
        (the sub would give 47.5)."""
        ev = _standard_events()
        ev.add(1, 46.5, "Foul Committed", 1, foul_committed={"card": {"name": "Second Yellow"}})
        ev.add(2, 45.5, "Substitution", 1)
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1)]})
        assert teams[1]["summed"] == pytest.approx(46.5)

    @pytest.mark.parametrize("build, stint_to, summed, deducted, dismissed", [
        # red card at (2, 60:00), no substitution: loss = 95 - 62
        (lambda ev: ev.card(2, 60, 1), (None, None), 62.0, 33.0, 1),
        # card on the bench after his own substitution: 11 stay on, no loss
        (lambda ev: ev.add(2, 60, "Substitution", 1).card(2, 70, 1), (60, 2), 62.0, 0.0, 1),
        # unpaired Player Off at (2, 70:00): loss = 95 - 72
        (lambda ev: ev.add(2, 70, "Player Off", 1), (None, None), 72.0, 23.0, 0),
        # card in the interval: clamped to the first-half whistle, loss = 95 - 47
        (lambda ev: ev.card(1, 48, 1), (None, None), 47.0, 48.0, 1),
        # treatment window across half time: (1, 46:00) -> (2, 46:00) is 1 + 1 min
        (lambda ev: ev.add(1, 46, "Player Off", 1).add(2, 46, "Player On", 1),
         (None, None), 93.0, 2.0, 0),
    ], ids=["red_card", "bench_card_after_sub", "unpaired_off", "interval_card",
            "window_across_half_time"])
    def test_expected_deducts_exit_losses_and_windows(self, build, stint_to, summed, deducted,
                                                      dismissed):
        """§31.6: expected = 11 x match_duration - exit losses - treatment windows,
        on the elapsed clock; only a dismissal or unpaired Off before his own
        substitution is a loss."""
        ev = _standard_events()
        build(ev)
        teams = _run(_ordered(ev), {1: [_stint("Center Back", 0, 1, *stint_to)]})
        t = teams[1]
        assert t["summed"] == pytest.approx(summed)
        assert 11 * 95.0 - t["expected"] == pytest.approx(deducted)
        assert t["dismissed"] == dismissed

    def test_unused_substitute_red_card_costs_nothing(self):
        """A bench card for a player with no lineup positions: no minutes, no
        dismissal counted (base.dismissals counts players who took the field), no
        exit loss, no crash on an empty stint list."""
        ev = _standard_events()
        ev.card(2, 80, 2)
        teams, _ = _match(_ordered(ev), [_team(1, {1: [_stint("Center Back", 0, 1)], 2: []})])
        assert teams[0]["summed"] == pytest.approx(95.0)
        assert teams[0]["dismissed"] == 0
        assert teams[0]["expected"] == pytest.approx(11 * 95.0)


class TestSpikePositionAndCeilingRules:
    """§31.4 position rules and the §31.5 per-period ceiling."""

    def test_latest_start_wins_even_after_that_stint_has_ended(self):
        """§31.4: CB from kick-off with no end, and a 2-min ST stint from (2, 48:00).
        After it ends, the earlier CB stint still covers t, but the position stays
        ST (latest start). Union 95 = CB 0-50 + ST 50-95."""
        teams = _run(_standard_events().items, {1: [
            _stint("Center Back", 0, 1),
            _stint("Center Forward", 48, 2, 50, 2, reason="Tactical Shift"),
        ]})
        assert teams[1]["summed"] == pytest.approx(95.0)
        assert dict(teams[1]["by_group"]) == pytest.approx({"CB": 50.0, "ST": 45.0})

    @pytest.mark.parametrize("order, group", [
        (["slot", "cf", "lb"], "ST"), (["slot", "lb", "cf"], "FB-WB"),
        (["cf", "slot", "lb"], "ST"),
    ])
    def test_same_instant_triple_first_listed_shift_wins(self, order, group):
        """The 2015/16 triple: among stints at one instant, a Tactical Shift beats a
        substitution slot, and between two shifts the first listed wins."""
        stints = {
            "slot": _stint("Right Midfield", 60, 2, reason="Substitution - On"),
            "cf": _stint("Center Forward", 60, 2, reason="Tactical Shift"),
            "lb": _stint("Left Back", 60, 2, reason="Tactical Shift"),
        }
        teams = _run(_standard_events().items, {1: [stints[k] for k in order]})
        assert dict(teams[1]["by_group"]) == pytest.approx({group: 33.0})

    def test_ceiling_is_per_period_not_per_match(self):
        """§31.5: 11 players in period 1 is fine, but 12 at once in period 2 fails,
        even though the match total (10 x 95 + 48 + 8 = 1006) is under 11 x 95."""
        players = {pid: [_stint("Center Back", 0, 1)] for pid in range(1, 11)}
        players[11] = [_stint("Center Back", 45, 2, reason="Substitution - On")]
        players[12] = [_stint("Center Back", 85, 2, reason="Substitution - On")]
        with pytest.raises(SystemExit, match="team_minutes_exceed_ceiling"):
            _match(_standard_events().items, [_team(1, players)])


class TestSpikePositionOracles:
    """§31.6 event-position oracle and tactics-timeline check (external checks of the
    lineup timeline; previously untested)."""

    def _substitution_match(self):
        """Player 1 (CB) is replaced by player 2 (CB slot) at (2, 60:00)."""
        ev = _standard_events()
        ev.add(1, 10, "Pass", 1, **_pos("Center Back"))
        ev.add(1, 30, "Pass", 2, **_pos("Left Back"))   # player 2 before his stint
        ev.add(2, 60, "Substitution", 1, substitution={"replacement": {"id": 2}},
               **_pos("Center Back"))
        ev.add(2, 70, "Pass", 2, **_pos("Center Back"))
        ev.add(2, 75, "Pass", 2, **_pos("Center Forward"))  # disagrees
        ev.add(2, 80, "Pass", 1, **_pos("Center Forward"))  # after player 1's exit
        ev.add(5, 121, "Shot", 2, **_pos("Goalkeeper"))      # shootout
        events = _ordered(ev)
        teams, clock = _match(events, [_team(1, {
            1: [_stint("Center Back", 0, 1, 60, 2)],
            2: [_stint("Center Back", 60, 2, reason="Substitution - On")],
        })])
        return events, teams, clock

    def test_event_oracle_counts_only_events_inside_the_players_time(self):
        events, teams, clock = self._substitution_match()
        rows = {r["player_id"]: r for r in spike.event_position_oracle(_MID, events, teams, clock)}
        # player 1: the pass at 10' and his Substitution event; not the pass after exit
        assert (rows[1]["events"], rows[1]["agree"]) == (2, 2)
        # player 2: 70' and 75' only; not before his stint, not the shootout
        assert (rows[2]["events"], rows[2]["agree"]) == (2, 1)

    def test_tactics_check_measures_minutes_against_tactical_shift_events(self):
        """Starting XI: player 1 CB, player 3 LB. A Tactical Shift at (2, 60:00) moves
        player 1 to CF, but his lineup stint stays CB: 95 - 62 = 33 min disagree.
        A substitute takes the replaced player's position from the Substitution
        event, so player 2 (LB slot) disagrees 0."""
        lineup = [{"player": {"id": 1}, **_pos("Center Back")},
                  {"player": {"id": 3}, **_pos("Left Back")}]
        shifted = [{"player": {"id": 1}, **_pos("Center Forward")},
                   {"player": {"id": 2}, **_pos("Left Back")}]
        ev = _standard_events()
        ev.add(1, 0, "Starting XI", tactics={"lineup": lineup})
        ev.add(2, 60, "Substitution", 3, substitution={"replacement": {"id": 2}},
               **_pos("Left Back"))
        ev.add(2, 60, "Tactical Shift", tactics={"lineup": shifted})
        events = _ordered(ev)
        teams, clock = _match(events, [_team(1, {
            1: [_stint("Center Back", 0, 1)],
            3: [_stint("Left Back", 0, 1, 60, 2)],
            2: [_stint("Left Back", 60, 2, reason="Substitution - On")],
        })])
        rows = {r["player_id"]: r for r in spike.tactics_check(_MID, events, teams, clock)}
        assert rows[1]["disagree"] == pytest.approx(33.0)
        assert rows[3]["disagree"] == pytest.approx(0.0)
        assert rows[2]["disagree"] == pytest.approx(0.0)


class TestSpikeSubstitutionAfterUnpairedOff:
    """§31.2: the player's own Substitution may follow his unpaired Player Off
    (an injured player formally replaced); any other event still fails."""

    def _events(self) -> _Events:
        ev = _standard_events()
        ev.add(2, 60, "Player Off", 1)
        ev.add(2, 65, "Substitution", 1, substitution={"replacement": {"id": 2}})
        return ev

    def _lineup(self) -> list[dict]:
        return [_team(1, {
            1: [_stint("Center Back", 0, 1)],
            2: [_stint("Center Back", 65, 2, reason="Substitution - On (Injury)")],
        })]

    def test_own_substitution_after_unpaired_off_is_allowed(self):
        """Exit = the Off (62 elapsed); the team is a player short from the Off to
        the Substitution, so the expectation deducts 67 - 62 = 5 min (§31.6)."""
        (team,), _ = _match(_ordered(self._events()), self._lineup())
        assert team["summed"] == pytest.approx(62.0 + (95.0 - 67.0))
        assert team["expected"] == pytest.approx(11 * 95.0 - 5.0)

    def test_other_event_after_the_substitution_still_fails(self):
        ev = self._events()
        ev.add(2, 70, "Pass", 1)
        with pytest.raises(SystemExit, match="event_after_permanent_off"):
            _match(_ordered(ev), self._lineup())


class TestSpikeUpdateReference:
    """The spike writes core and base.dismissals, also for a league with no file."""

    @pytest.fixture
    def ref_dir(self, tmp_path, monkeypatch):
        import reference_totals
        monkeypatch.setattr(reference_totals, "REFERENCE_DIR", tmp_path)
        monkeypatch.setattr(spike, "REFERENCE_DIR", tmp_path)
        return tmp_path

    @staticmethod
    def _core(minutes: float = 990.0, shots: int = 10) -> dict:
        groups = {col: 0.0 for col in spike.GROUP_COLUMN.values()}
        groups["minutes_cb"] = minutes
        return {"minutes_played": minutes, "shots": shots, **groups}

    def test_new_league_without_a_file_is_written(self, ref_dir):
        spike.update_reference(99, 1, self._core(), 3, write=True)
        ref = json.loads((ref_dir / "99_1.json").read_text())
        assert ref["core"] == self._core()
        assert ref["base"] == {"dismissals": 3}

    def test_existing_base_keys_are_kept_and_minutes_may_change(self, ref_dir):
        (ref_dir / "99_1.json").write_text(json.dumps({
            "competition_id": 99, "season_id": 1,
            "base": {"matches": 5, "dismissals": 3}, "core": self._core(900.0)}))
        spike.update_reference(99, 1, self._core(990.0), 3, write=True)
        ref = json.loads((ref_dir / "99_1.json").read_text())
        assert ref["base"] == {"matches": 5, "dismissals": 3}
        assert ref["core"]["minutes_played"] == 990.0

    @pytest.mark.parametrize("change", ["shots", "dismissals"])
    def test_non_minutes_change_fails_and_writes_nothing(self, ref_dir, change):
        before = {"competition_id": 99, "season_id": 1,
                  "base": {"dismissals": 3}, "core": self._core()}
        (ref_dir / "99_1.json").write_text(json.dumps(before))
        core = self._core(shots=11) if change == "shots" else self._core()
        dismissals = 4 if change == "dismissals" else 3
        with pytest.raises(SystemExit, match="non-minutes values changed"):
            spike.update_reference(99, 1, core, dismissals, write=True)
        assert json.loads((ref_dir / "99_1.json").read_text()) == before

    def test_no_write_leaves_a_new_league_unwritten(self, ref_dir):
        spike.update_reference(99, 1, self._core(), 3, write=False)
        assert not (ref_dir / "99_1.json").exists()


class TestSpikeUnknownPositionsFail:
    """§31.6: an unmapped position fails both position checks, as in §31.4."""

    def _teams(self, events):
        return _match(events, [_team(1, {1: [_stint("Center Back", 0, 1)]})])

    def test_event_oracle_fails_on_unmapped_event_position(self):
        ev = _standard_events()
        ev.add(1, 10, "Pass", 1, **_pos("Sweeper Keeper"))
        events = _ordered(ev)
        teams, clock = self._teams(events)
        with pytest.raises(SystemExit, match="unmapped position 'Sweeper Keeper'"):
            spike.event_position_oracle(_MID, events, teams, clock)

    def test_tactics_check_fails_on_unmapped_tactics_position(self):
        ev = _standard_events()
        ev.add(1, 0, "Starting XI",
               tactics={"lineup": [{"player": {"id": 1}, **_pos("Libero")}]})
        events = _ordered(ev)
        teams, clock = self._teams(events)
        with pytest.raises(SystemExit, match="unmapped position 'Libero'"):
            spike.tactics_check(_MID, events, teams, clock)

    def test_tactics_check_fails_on_substitution_without_position(self):
        ev = _standard_events()
        ev.add(2, 60, "Substitution", 3, substitution={"replacement": {"id": 2}})
        events = _ordered(ev)
        teams, clock = self._teams(events)
        with pytest.raises(SystemExit, match="unmapped position None"):
            spike.tactics_check(_MID, events, teams, clock)
