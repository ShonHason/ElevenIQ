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

    def test_current_chunk_changes_no_recorded_chunk_a_or_b_value(self):
        """Plan 005 M4b3 (ADR 004 §9) only adds keys: no recorded chunk_a or chunk_b
        value may change. (M4b allowed the D14 long-pass and entry columns and D16's
        xg_assisted_direct.)"""
        assert chunk_a.CHUNK_A_CHANGED_BY_CURRENT_CHUNK == frozenset()
        assert chunk_b.CHUNK_B_CHANGED_BY_CURRENT_CHUNK == frozenset()


class TestOpenPlayRestartSplitSpikes:
    """ADR 004 §9: xg_assisted, key_passes and assists split by the key pass's own
    pass.type (RESTART_TYPES), never by play_pattern; open play + restart = total.
    A restart key pass inside an open-play-labelled possession fails the spike."""

    @pytest.fixture(autouse=True)
    def _no_processed_data(self, monkeypatch):
        monkeypatch.setattr(chunk_b, "_load_seasons", lambda *_: pl.DataFrame(schema={
            "player_id": pl.Int64, "player_name": pl.Utf8, "is_gk": pl.Boolean,
            "player_season_minutes_total": pl.Float64}))

    @staticmethod
    def _chance(idx: int, ptype: str | None, pattern: str, xg: float) -> list[dict]:
        pass_payload = {"assisted_shot_id": f"e{idx + 2}", "shot_assist": True}
        if ptype:
            pass_payload["type"] = {"name": ptype}
        return [_ev(idx, "Pass", 1, **{"pass": pass_payload}),
                _ev(idx + 1, "Ball Receipt*", 2),
                _ev(idx + 2, "Shot", 2, play_pattern={"name": pattern},
                    shot={"statsbomb_xg": xg, "type": {"name": "Open Play"}})]

    def test_xga_split_by_key_pass_type_not_play_pattern(self):
        events = [*self._chance(10, "Corner", "From Corner", 0.3),
                  *self._chance(20, None, "From Corner", 0.2),   # open play by definition
                  *self._chance(30, None, "Regular Play", 0.1)]
        r = chunk_b.section_xg_assisted({1: events}, 0, 0)
        assert r["restart_xg_assisted"] == pytest.approx(0.3)
        assert r["op_xg_assisted"] == pytest.approx(0.3)
        assert r["op_xg_assisted"] + r["restart_xg_assisted"] == pytest.approx(r["xg_assisted"])
        assert r["pattern_disagreement"]["type_open_pattern_restart"] == (1, pytest.approx(0.2))

    def test_restart_key_pass_in_open_play_possession_fails(self):
        with pytest.raises(SystemExit, match="consistency guard"):
            chunk_b.section_xg_assisted({1: self._chance(10, "Free Kick", "Regular Play", 0.3)}, 0, 0)

    def test_core_counts_split_and_reverse_direction(self):
        import verify_core_metrics as core
        from collections import Counter

        def kp(ptype, pattern, goal=False):
            p = {"shot_assist": True, "end_location": [100, 40]}
            if goal:
                p["goal_assist"] = True
            if ptype:
                p["type"] = {"name": ptype}
            return {"period": 1, "player": {"id": 1}, "type": {"name": "Pass"},
                    "location": [60, 40], "play_pattern": {"name": pattern}, "pass": p}
        t: Counter = Counter()
        core.count_events([kp("Corner", "From Corner", goal=True), kp(None, "From Throw In"),
                           kp(None, "Regular Play"), kp("Throw-in", "Regular Play")], t)
        assert (t["key_passes"], t["op_key_passes"], t["restart_key_passes"]) == (4, 2, 2)
        assert (t["assists"], t["op_assists"], t["restart_assists"]) == (1, 0, 1)
        assert t["key_passes_type_restart_pattern_open"] == 1
        assert t["key_passes_type_open_pattern_restart"] == 1


class TestBeforeAfterEmptyInput:
    """spikes/m4b_before_after.py exits with a clear error (code 2), not a crash, when
    any input frame is empty."""

    def test_empty_snapshot_exits_2_with_a_message(self, tmp_path, monkeypatch, capsys):
        import m4b_before_after as ba
        frame = pl.DataFrame({"player_id": [1]})
        empty = pl.DataFrame({"player_id": []}, schema={"player_id": pl.Int64})
        frames = {"player_match_stats.parquet": empty, "player_season_stats.parquet": frame}

        def fake_read(path):
            return frames.get(Path(path).name, frame)
        monkeypatch.setattr(ba.pl, "read_parquet", fake_read)
        expected = tmp_path / "expected.json"
        expected.write_text('{"meets_min_minutes": [], "unchanged_flags": []}')
        monkeypatch.setattr(sys, "argv", ["m4b_before_after.py", "--snapshot", str(tmp_path),
                                          "--expected", str(expected)])
        assert ba.main() == 2
        assert "empty input frame(s): snapshot match" in capsys.readouterr().out


class TestBeforeAfterEdgeCases:
    """spikes/m4b_before_after.py: --snapshot is required; a row-count mismatch exits
    1 before any column compare; with --all-columns-identical, one changed value
    exits 1 (TestSplitInvariantProblems covers the null checks)."""

    @staticmethod
    def _season(n: int, minutes=None) -> pl.DataFrame:
        return pl.DataFrame({"player_id": list(range(n)), "team_id": [1] * n,
                             "competition_id": [2] * n, "season_id": [27] * n,
                             "source": ["statsbomb"] * n,
                             "minutes_played": minutes or [90.0] * n})

    @staticmethod
    def _run(monkeypatch, tmp_path, frames: dict, extra_args=()):
        import m4b_before_after as ba
        monkeypatch.setattr(ba.pl, "read_parquet", lambda path: frames[str(path)])
        expected = tmp_path / "expected.json"
        expected.write_text('{"meets_min_minutes": [], "unchanged_flags": []}')
        monkeypatch.setattr(sys, "argv", ["m4b_before_after.py", "--snapshot", str(tmp_path),
                                          "--expected", str(expected), *extra_args])
        return ba.main()

    def test_snapshot_is_required(self, monkeypatch):
        import m4b_before_after as ba
        monkeypatch.setattr(sys, "argv", ["m4b_before_after.py"])
        with pytest.raises(SystemExit) as exc:
            ba.main()
        assert exc.value.code == 2

    def test_row_count_mismatch_exits_before_column_compare(self, monkeypatch, tmp_path, capsys):
        import m4b_before_after as ba
        match_old = pl.DataFrame({"match_id": [1, 1], "player_id": [1, 2]})
        match_new = pl.DataFrame({"match_id": [1], "player_id": [1]})
        frames = {str(tmp_path / "player_match_stats.parquet"): match_old,
                  str(tmp_path / "player_season_stats.parquet"): self._season(2),
                  str(ba.NEW_MATCH): match_new, str(ba.NEW_SEASON): self._season(2)}
        code = self._run(monkeypatch, tmp_path, frames, ["--all-columns-identical"])
        out = capsys.readouterr().out
        assert code == 1
        assert "match rows differ: 2 → 1" in out and "columns not compared" in out

    def test_one_changed_value_exits_1(self, monkeypatch, tmp_path, capsys):
        """Mutation-style: the rebuild differs from the snapshot in one season value
        (minutes 90.0 → 91.0 for one row); the identical check must stop with exit 1."""
        import m4b_before_after as ba
        match = pl.DataFrame({"match_id": [1, 1], "player_id": [0, 1]})
        frames = {str(tmp_path / "player_match_stats.parquet"): match,
                  str(tmp_path / "player_season_stats.parquet"): self._season(2),
                  str(ba.NEW_MATCH): match,
                  str(ba.NEW_SEASON): self._season(2, minutes=[90.0, 91.0])}
        code = self._run(monkeypatch, tmp_path, frames, ["--all-columns-identical"])
        out = capsys.readouterr().out
        assert code == 1
        assert "season column minutes_played changed" in out
        assert "pre-existing columns changed" in out


class TestColumnDifferences:
    """spikes/m4b_before_after.column_differences: what counts as a change."""

    KEY = ["id"]

    def _diff(self, old: dict, new: dict):
        import m4b_before_after as ba
        return ba.column_differences(pl.DataFrame(old), pl.DataFrame(new), self.KEY)

    def test_identical_frames_in_another_row_order_have_no_difference(self):
        assert self._diff({"id": [1, 2], "x": [1.0, 2.0]}, {"id": [2, 1], "x": [2.0, 1.0]}) == []

    def test_float_within_tolerance_is_identical(self):
        assert self._diff({"id": [1], "x": [1.0]}, {"id": [1], "x": [1.0 + 1e-12]}) == []

    @pytest.mark.parametrize("old, new, fragment", [
        ({"id": [1], "x": [1.0]}, {"id": [1], "x": [1.1]}, "column x changed"),
        ({"id": [1], "x": [1]}, {"id": [1], "x": [2]}, "column x changed"),
        ({"id": [1], "x": [1.0]}, {"id": [1], "x": pl.Series([None], dtype=pl.Float64)},
         "nulls differ True"),
        ({"id": [1], "x": [1.0]}, {"id": [1], "x": [float("nan")]}, "NaNs differ True"),
        ({"id": [1], "x": [1]}, {"id": [1], "x": [1.0]}, "dtype"),
        ({"id": [1], "x": [1]}, {"id": [1]}, "missing after the rebuild"),
    ])
    def test_each_kind_of_change_is_reported(self, old, new, fragment):
        (msg,) = self._diff(old, new)
        assert fragment in msg


class TestSplitInvariantProblems:
    """spikes/m4b_before_after.split_invariant_problems (ADR 004 §9 invariant)."""

    @staticmethod
    def _frame(**over):
        base = {"key_passes": [3], "op_key_passes": [2], "restart_key_passes": [1],
                "assists": [1], "op_assists": [1], "restart_assists": [0],
                "xg_assisted": [0.5], "op_xg_assisted": [0.3], "restart_xg_assisted": [0.2]}
        return pl.DataFrame({**base, **over})

    def test_consistent_frame_has_no_problems(self):
        import m4b_before_after as ba
        assert ba.split_invariant_problems(self._frame()) == []

    def test_count_mismatch_is_reported(self):
        import m4b_before_after as ba
        (msg,) = ba.split_invariant_problems(self._frame(restart_key_passes=[2]))
        assert "op + restart key_passes" in msg

    def test_all_null_xg_column_is_reported_not_raised(self):
        import m4b_before_after as ba
        frame = self._frame(op_xg_assisted=pl.Series([None], dtype=pl.Float64))
        (msg,) = ba.split_invariant_problems(frame)
        assert "null values in compared columns" in msg and "op_xg_assisted" in msg

    @pytest.mark.parametrize("col", ["op_key_passes", "restart_assists", "key_passes"])
    def test_null_count_column_is_reported(self, col):
        """A comparison with a null is itself null and would be filtered away."""
        import m4b_before_after as ba
        frame = self._frame(**{col: pl.Series([None], dtype=pl.Int64)})
        (msg,) = ba.split_invariant_problems(frame)
        assert col in msg

    def test_holds_is_printed_only_when_every_check_passed(self, capsys):
        import m4b_before_after as ba
        ba.split_invariant_problems(self._frame(restart_key_passes=[2]))
        assert "holds" not in capsys.readouterr().out
        ba.split_invariant_problems(self._frame())
        assert "holds" in capsys.readouterr().out

    def test_pre_m4b3_frame_is_skipped(self):
        import m4b_before_after as ba
        assert ba.split_invariant_problems(pl.DataFrame({"key_passes": [1]})) == []


class TestNpShotsOnTargetProblems:
    """spikes/m4b_before_after.np_shots_on_target_problems (plan 005 M4c2b, ADR 004
    D15): np + penalties on target = shots_on_target per match row; season sum,
    np <= np_shots, p90 from minutes_played."""

    KEY = {"team_id": [10, 10], "competition_id": [11, 11], "season_id": [27, 27],
           "source": ["statsbomb", "statsbomb"]}

    def _frames(self, match_over: dict | None = None, season_over: dict | None = None):
        match = pl.DataFrame({"match_id": [1, 2], "player_id": [7, 7], **self.KEY,
                              "shots_on_target": [2, 1], "np_shots_on_target": [1, 1],
                              **(match_over or {})})
        season = pl.DataFrame({"player_id": [7], **{k: v[:1] for k, v in self.KEY.items()},
                               "minutes_played": [180.0], "shots_on_target": [3], "np_shots": [4],
                               "np_shots_on_target": [2], "np_shots_on_target_p90": [1.0],
                               **(season_over or {})})
        return match, season

    def test_consistent_frames_have_no_problems(self):
        import m4b_before_after as ba
        assert ba.np_shots_on_target_problems(*self._frames(), {(1, 7): 1}) == []

    def test_missing_penalty_breaks_the_identity(self):
        """Mutation-style: the pipeline counted a penalty on target as np."""
        import m4b_before_after as ba
        (msg,) = ba.np_shots_on_target_problems(*self._frames(), {})
        assert "1 match rows where np_shots_on_target + penalties on target" in msg

    def test_penalty_of_a_player_without_a_row_is_reported(self):
        import m4b_before_after as ba
        msgs = ba.np_shots_on_target_problems(*self._frames(), {(1, 7): 1, (3, 9): 1})
        assert any("no match row" in m for m in msgs)

    @pytest.mark.parametrize("over, fragment", [
        ({"np_shots_on_target": [3], "np_shots_on_target_p90": [1.5]}, "match sum"),
        ({"np_shots": [1]}, "> np_shots"),
        ({"np_shots_on_target_p90": [1.1]}, "p90 differs"),
        ({"np_shots_on_target_p90": pl.Series([None], dtype=pl.Float64)}, "p90 differs"),
    ])
    def test_season_inconsistencies_are_reported(self, over, fragment):
        import m4b_before_after as ba
        msgs = ba.np_shots_on_target_problems(*self._frames(season_over=over), {(1, 7): 1})
        assert any(fragment in m for m in msgs), msgs

    def test_null_is_reported_not_dropped(self):
        import m4b_before_after as ba
        match_over = {"np_shots_on_target": pl.Series([None, 1], dtype=pl.Int64)}
        (msg,) = ba.np_shots_on_target_problems(*self._frames(match_over=match_over), {(1, 7): 1})
        assert "null values" in msg

    def test_pre_m4c2b_frame_is_skipped(self):
        import m4b_before_after as ba
        match, season = self._frames()
        assert ba.np_shots_on_target_problems(match.drop("np_shots_on_target"), season, {}) == []
