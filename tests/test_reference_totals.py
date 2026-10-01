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
        """Plan 004 H2: every reference file has the core section from
        spikes/verify_core_metrics.py, and base carries the dismissal count."""
        ref = _load(cs)
        assert set(ref["core"]) == {
            "minutes_played", "shots", "shots_on_target", "xg", "passes_attempted",
            "passes_completed", "progressive_passes", "progressive_carries", "key_passes",
            "assists", "tackles_attempted", "tackles_won", "dribbles_attempted",
            "dribbles_completed", "interceptions", "clearances", "blocks",
            "fouls_committed", "fouls_won", "carries",
        }
        assert isinstance(ref["base"]["dismissals"], int)

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
