"""
Consistency checks on the committed reference figures
(tests/fixtures/reference_totals/*.json, Plan 003 §3) against config and
ADR 003. Reads only committed test fixtures, never data/, so these run in the
fast unit suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eleveniq.config import COMPETITION_SEASONS, FULL_COVERAGE_THRESHOLD

_REF_DIR = Path(__file__).parent / "fixtures" / "reference_totals"


def _load(cs) -> dict:
    return json.loads((_REF_DIR / f"{cs.competition_id}_{cs.season_id}.json").read_text())


_IDS = [cs.label for cs in COMPETITION_SEASONS]


class TestReferenceTotalsFixtures:
    def test_one_reference_file_per_configured_competition_season(self):
        expected = {f"{cs.competition_id}_{cs.season_id}.json" for cs in COMPETITION_SEASONS}
        present = {p.name for p in _REF_DIR.glob("*.json")}
        assert present == expected

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_file_keys_match_its_name(self, cs):
        ref = _load(cs)
        assert (ref["competition_id"], ref["season_id"]) == (cs.competition_id, cs.season_id)

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_starts_equal_matches_times_22(self, cs):
        """Plan 003 §3 structural invariant; holds for the partial Bundesliga too."""
        base = _load(cs)["base"]
        assert base["starts"] == base["matches"] * 2 * 11

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_configured_coverage_agrees_with_adr_003_rule(self, cs):
        """ADR 003 §5: config is the source of truth but must agree with the §2
        rule (available / (n*(n-1)) >= FULL_COVERAGE_THRESHOLD). Only the
        fixture-coverage half of the rule can be checked from the reference file."""
        n = cs.expected_teams
        coverage = _load(cs)["base"]["matches"] / (n * (n - 1))
        derived = "FULL" if coverage >= FULL_COVERAGE_THRESHOLD else "PARTIAL"
        assert derived == cs.coverage, f"{cs.label}: coverage {coverage:.3f}"

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_completed_never_exceeds_attempted(self, cs):
        ref = _load(cs)
        base, a, b = ref["base"], ref["chunk_a"], ref["chunk_b"]
        assert base["crosses_completed"] <= base["crosses_attempted"]
        assert a["long_passes_completed"] <= a["long_passes_attempted"]
        assert 0 <= b["xg_assisted_direct"] <= b["xg_assisted"]

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_aerials_won_equal_aerials_lost(self, cs):
        """Every aerial duel has one winner and one loser, so league totals
        must balance (holds in all five 2015/16 leagues)."""
        a = _load(cs)["chunk_a"]
        assert a["aerials_won"] == a["aerials_lost"]

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_core_section_present(self, cs):
        """Plan 004 H2/H4a, plan 005 M4b1: every reference file has the core section
        from spikes/verify_core_metrics.py, including the per-group minutes (ADR 002
        §31.6) and the ADR 004 columns, and base carries the dismissal count and
        Σ match_duration."""
        ref = _load(cs)
        assert set(ref["core"]) == {
            "minutes_played", "shots", "shots_on_target", "xg", "passes_attempted",
            "passes_completed", "progressive_passes", "progressive_carries", "key_passes",
            "assists", "tackles_attempted", "tackles_won", "dribbles_attempted",
            "dribbles_completed", "interceptions", "clearances", "blocks",
            "fouls_committed", "fouls_won", "carries",
            "np_xg", "np_shots", "passes_injury_clearance", "open_play_passes_attempted",
            "team_open_play_passes_while_on",
            "minutes_am_winger", "minutes_cb", "minutes_dm_cm", "minutes_fb_wb",
            "minutes_gk", "minutes_st",
        }
        assert isinstance(ref["base"]["dismissals"], int)
        assert isinstance(ref["base"]["match_duration"], float)

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_core_group_minutes_sum_to_minutes_played(self, cs):
        """ADR 002 §31.4: every minute has exactly one group, so the six group
        totals add up to minutes_played (each is rounded to 4 decimals)."""
        core = _load(cs)["core"]
        groups = ["minutes_am_winger", "minutes_cb", "minutes_dm_cm", "minutes_fb_wb",
                  "minutes_gk", "minutes_st"]
        assert sum(core[g] for g in groups) == pytest.approx(core["minutes_played"], abs=1e-3)

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_core_minutes_obey_the_goalkeeper_identity(self, cs):
        """ADR 002 §31.6 on the committed totals. base.match_duration is Σ of the
        §31.1 match_duration (elapsed playing time) over the league's matches.
        - GK identity: one goalkeeper per team at all times, so GK minutes equal
          2 x Σ match_duration within 0.05% (observed 0.000-0.018% short);
        - at most 11 players per team: minutes_played <= 22 x Σ match_duration;
        - every period runs to its whistle, so the average match is longer
          than a nominal 90."""
        ref = _load(cs)
        core, base = ref["core"], ref["base"]
        duration = base["match_duration"]
        assert core["minutes_gk"] == pytest.approx(2 * duration, rel=5e-4)
        assert core["minutes_played"] <= 2 * 11 * duration
        assert duration / base["matches"] > 90.0

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_core_subsets_never_exceed_their_totals(self, cs):
        core = _load(cs)["core"]
        assert core["shots_on_target"] <= core["shots"]
        assert core["passes_completed"] <= core["passes_attempted"]
        assert core["progressive_passes"] <= core["passes_completed"]
        assert core["assists"] <= core["key_passes"] <= core["passes_attempted"]
        assert core["tackles_won"] <= core["tackles_attempted"]
        assert core["dribbles_completed"] <= core["dribbles_attempted"]
        assert core["progressive_carries"] <= core["carries"]

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_m4b_columns_are_subsets_of_their_totals(self, cs):
        """ADR 004 D12-D15 on the committed totals: penalties are a subset of shots
        and xG; Injury Clearances are incomplete passes; open-play passes are a subset
        of all passes; long-pass unknown and Injury Clearance outcomes share the
        long-pass predicate, so both are incomplete long passes."""
        ref = _load(cs)
        core, a = ref["core"], ref["chunk_a"]
        assert core["np_shots"] <= core["shots"]
        assert 0 <= core["np_xg"] <= core["xg"]
        assert core["passes_injury_clearance"] <= core["passes_attempted"] - core["passes_completed"]
        assert core["open_play_passes_attempted"] <= core["passes_attempted"]
        assert (a["long_passes_unknown_outcome"] + a["long_passes_injury_clearance"]
                <= a["long_passes_attempted"] - a["long_passes_completed"])

    @pytest.mark.parametrize("cs", COMPETITION_SEASONS, ids=_IDS)
    def test_team_open_play_passes_credit_seven_to_eleven_players(self, cs):
        """ADR 004 D12: each team open-play pass is credited to 7-11 players (the
        Laws minimum to the maximum on the pitch; the spike fails otherwise), so the
        league total of team_open_play_passes_while_on lies between 7x and 11x the
        open-play passes (2015/16: about 10.97x)."""
        core = _load(cs)["core"]
        n = core["open_play_passes_attempted"]
        assert 7 * n <= core["team_open_play_passes_while_on"] <= 11 * n
        assert core["team_open_play_passes_while_on"] / n > 10.5
