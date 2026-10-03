"""
Spec tests for the plan 005 M4b1 changes to the chunk A and chunk B reference
spikes (spikes/verify_chunk_a_metrics.py, spikes/verify_chunk_b_metrics.py).
Their totals become tests/fixtures/reference_totals, the "truth" the M4b2
pipeline must match, so a definition bug here would be locked in.

Expected values come from ADR 004 D14 (restarts) and D16 (xG-assisted receipt
guard) on synthetic events. No data/ files are read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import verify_chunk_a_metrics as chunk_a  # noqa: E402
import verify_chunk_b_metrics as chunk_b  # noqa: E402
import reference_totals  # noqa: E402

_NO_GOALS = [{"match_id": 1, "home_score": 0, "away_score": 0}]


def _long_pass(ptype: str | None = None, outcome: str | None = None,
               length: float = 40.0, **flags) -> dict:
    p: dict = {"length": length, **flags}
    if ptype:
        p["type"] = {"name": ptype}
    if outcome:
        p["outcome"] = {"name": outcome}
    return {"type": {"name": "Pass"}, "player": {"id": 1}, "pass": p}


class TestChunkASpikeLongPassRestarts:
    """ADR 004 D14: long_passes_attempted/completed, long_passes_unknown_outcome and
    long_passes_injury_clearance share one predicate, length >= 30 and type not in
    RESTART_TYPES (throw-ins included). Crosses and switches keep restarts."""

    @staticmethod
    def _totals(events: list[dict]) -> tuple[dict, dict]:
        return chunk_a.section_reference_totals(_NO_GOALS, events)

    @pytest.mark.parametrize("outcome", [None, "Unknown", "Injury Clearance", "Incomplete"])
    @pytest.mark.parametrize("ptype", ["Throw-in", "Corner", "Free Kick", "Goal Kick", "Kick Off"])
    def test_long_restart_is_in_no_long_pass_count(self, ptype, outcome):
        """A long throw-in that ends as an Injury Clearance must not be subtracted
        from an attempt count it was never part of (404 such passes in 2015/16)."""
        _, a = self._totals([_long_pass(ptype, outcome)])
        assert (a["long_passes_attempted"], a["long_passes_completed"],
                a["long_passes_unknown_outcome"], a["long_passes_injury_clearance"]) == (0, 0, 0, 0)

    def test_open_play_long_pass_outcomes(self):
        """Completed, Unknown and Injury Clearance open-play long passes: 3 attempts,
        1 completion, 1 unknown, 1 Injury Clearance; a short Injury Clearance (29.9)
        is not a long pass."""
        events = [_long_pass(), _long_pass(outcome="Unknown"),
                  _long_pass(outcome="Injury Clearance"),
                  _long_pass(outcome="Injury Clearance", length=29.9)]
        _, a = self._totals(events)
        assert (a["long_passes_attempted"], a["long_passes_completed"],
                a["long_passes_unknown_outcome"], a["long_passes_injury_clearance"]) == (3, 1, 1, 1)

    def test_long_pass_exactly_at_threshold_is_long(self):
        _, a = self._totals([_long_pass(length=30.0)])
        assert a["long_passes_attempted"] == 1

    def test_crosses_and_switches_keep_throw_ins(self):
        """D14 'Kept': crosses_* and switches are StatsBomb flags and keep restarts."""
        base, a = self._totals([_long_pass("Throw-in", cross=True, switch=True)])
        assert base["crosses_attempted"] == 1 and base["crosses_completed"] == 1
        assert a["switches"] == 1


def _ev(idx: int, kind: str, pid: int, **extra) -> dict:
    return {"id": f"e{idx}", "index": idx, "type": {"name": kind}, "player": {"id": pid},
            "possession": 1, "possession_team": {"id": 1}, **extra}


class TestChunkBSpikeXgAssistedReceiptGuard:
    """ADR 004 D16: an assisted shot counts toward xg_assisted always, but toward
    xg_assisted_direct only when the shooter's last Ball Receipt* lies after the key
    pass and before the shot (and the window holds no dribble and <= 10 units of
    carry). A receipt before the key pass, or none, takes the no-receipt fallback:
    not direct."""

    @pytest.fixture(autouse=True)
    def _no_processed_data(self, monkeypatch):
        monkeypatch.setattr(chunk_b, "_load_seasons", lambda *_: pl.DataFrame(schema={
            "player_id": pl.Int64, "player_name": pl.Utf8, "is_gk": pl.Boolean,
            "player_season_minutes_total": pl.Float64}))

    @staticmethod
    def _run(shooter_events: list[dict]) -> dict:
        """Key pass by player 1 at index 10 to player 2's shot at index 20 (xG 0.3)."""
        events = [
            _ev(10, "Pass", 1, **{"pass": {"assisted_shot_id": "e20", "shot_assist": True}}),
            *shooter_events,
            _ev(20, "Shot", 2, shot={"statsbomb_xg": 0.3, "key_pass_id": "e10",
                                     "type": {"name": "Open Play"}}),
        ]
        return chunk_b.section_xg_assisted({1: sorted(events, key=lambda e: e["index"])}, 0, 0)

    def test_receipt_between_key_pass_and_shot_is_direct(self):
        r = self._run([_ev(11, "Ball Receipt*", 2)])
        assert r["xg_assisted"] == pytest.approx(0.3)
        assert r["xg_assisted_direct"] == pytest.approx(0.3)

    def test_receipt_only_before_the_key_pass_is_not_direct(self):
        """An earlier receipt in the same possession must not define the window."""
        r = self._run([_ev(5, "Ball Receipt*", 2)])
        assert r["xg_assisted"] == pytest.approx(0.3)
        assert r["xg_assisted_direct"] == 0.0

    def test_no_receipt_is_not_direct_but_still_assisted(self):
        r = self._run([])
        assert r["xg_assisted"] == pytest.approx(0.3)
        assert r["player_xg_assisted"] == {1: pytest.approx(0.3)}
        assert r["xg_assisted_direct"] == 0.0

    def test_last_receipt_after_the_key_pass_defines_the_window(self):
        """Receipts at 5 and 12; a dribble at 8 sits between the early receipt and
        the key pass. The window is (12, 20), so the shot is direct. (This also held
        before the D16 guard, since 12 was already the last receipt; the guard itself
        is tested by test_receipt_only_before_the_key_pass_is_not_direct.)"""
        r = self._run([_ev(5, "Ball Receipt*", 2), _ev(8, "Dribble", 2),
                       _ev(12, "Ball Receipt*", 2)])
        assert r["xg_assisted_direct"] == pytest.approx(0.3)


class TestChunkBSpikeEntryRestarts:
    """ADR 004 D14: the pass entry metrics exclude every restart, throw-ins included."""

    @staticmethod
    def _pass(ptype: str | None, start: list[float], end: list[float]) -> dict:
        p: dict = {"end_location": end}
        if ptype:
            p["type"] = {"name": ptype}
        return {"type": {"name": "Pass"}, "player": {"id": 1, "name": "p1"},
                "location": start, "pass": p}

    @pytest.mark.parametrize("ptype, expected", [(None, 1), ("Throw-in", 0), ("Free Kick", 0),
                                                 ("Corner", 0), ("Goal Kick", 0), ("Kick Off", 0)])
    def test_final_third_and_box_entries(self, ptype, expected):
        events = [self._pass(ptype, [70.0, 40.0], [85.0, 40.0]),   # final-third entry
                  self._pass(ptype, [95.0, 5.0], [110.0, 40.0])]   # box entry
        assert chunk_b.section_final_third(events)["final_third_entries_pass"] == expected
        assert chunk_b.section_box_entries(events)["box_entries_pass"] == expected


class TestReferenceGuard:
    """Plan 005 M4b: a reference spike may change only the recorded values its
    definition change names; anything else moving fails before a write."""

    @pytest.fixture
    def ref_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reference_totals, "REFERENCE_DIR", tmp_path)
        (tmp_path / "99_1.json").write_text(
            '{"competition_id": 99, "season_id": 1, "chunk_a": {"a": 1, "b": 2}}')
        return tmp_path

    def test_unchanged_and_new_keys_pass(self, ref_dir):
        reference_totals.guard_unchanged(99, 1, "chunk_a", {"a": 1, "b": 2, "c": 3}, frozenset())

    def test_allowed_key_may_change(self, ref_dir):
        reference_totals.guard_unchanged(99, 1, "chunk_a", {"a": 5, "b": 2}, frozenset({"a"}))

    def test_other_key_changing_fails(self, ref_dir):
        with pytest.raises(SystemExit, match="chunk_a values outside"):
            reference_totals.guard_unchanged(99, 1, "chunk_a", {"a": 5, "b": 3}, frozenset({"a"}))

    def test_recorded_key_missing_fails(self, ref_dir):
        with pytest.raises(SystemExit, match="changed"):
            reference_totals.guard_unchanged(99, 1, "chunk_a", {"a": 1}, frozenset())

    def test_new_league_or_section_passes(self, ref_dir):
        reference_totals.guard_unchanged(98, 1, "chunk_a", {"a": 1}, frozenset())
        reference_totals.guard_unchanged(99, 1, "chunk_b", {"x": 1}, frozenset())

    def test_spikes_name_exactly_the_m4b_columns(self):
        """ADR 004 D14 (long passes, pass entries) and D16 (xg_assisted_direct)."""
        assert chunk_a.CHUNK_A_CHANGED_BY_M4B == {"long_passes_attempted", "long_passes_completed"}
        assert chunk_b.CHUNK_B_CHANGED_BY_M4B == {
            "final_third_entries_pass", "box_entries_pass", "xg_assisted_direct"}
