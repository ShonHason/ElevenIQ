"""
Unit tests for eleveniq.pipeline.coverage (ADR 003 §2, §5). Synthetic
matches.json lists only; no data/ access.
"""
from __future__ import annotations

import json
from itertools import permutations

import polars as pl
import pytest

from eleveniq.config import CompetitionSeason
from eleveniq.pipeline.coverage import (
    CoverageCheckError,
    check_configured_coverage,
    check_processed_match_ids,
    check_season_ids_unique,
    classify_coverage,
)


def _matches(pairs: list[tuple[int, int]]) -> list[dict]:
    return [
        {"match_id": i, "home_team": {"home_team_id": h}, "away_team": {"away_team_id": a}}
        for i, (h, a) in enumerate(pairs, start=1)
    ]


def _round_robin(n: int) -> list[tuple[int, int]]:
    return list(permutations(range(1, n + 1), 2))


class TestClassifyCoverage:
    def test_complete_double_round_robin_is_full(self):
        assert classify_coverage(_matches(_round_robin(4)), expected_teams=4) == "FULL"

    def test_at_threshold_is_full(self):
        # 20 teams: 380 fixtures; 361 / 380 = 0.95 exactly
        pairs = _round_robin(20)[:361]
        assert classify_coverage(_matches(pairs), expected_teams=20, threshold=0.95) == "FULL"

    def test_just_below_threshold_is_partial(self):
        pairs = _round_robin(20)[:360]
        assert classify_coverage(_matches(pairs), expected_teams=20, threshold=0.95) == "PARTIAL"

    def test_ligue_1_style_three_missing_fixtures_is_full(self):
        assert classify_coverage(_matches(_round_robin(20)[3:]), expected_teams=20) == "FULL"

    def test_one_team_only_season_is_partial(self):
        """Bundesliga 2015/16 shape: all 18 teams appear, but only team 1's 34 matches."""
        pairs = [(1, t) for t in range(2, 19)] + [(t, 1) for t in range(2, 19)]
        assert classify_coverage(_matches(pairs), expected_teams=18) == "PARTIAL"

    def test_hidden_team_is_partial_even_with_high_ratio(self):
        """A file missing one team entirely must not pass as FULL: n comes from config."""
        pairs = _round_robin(3)  # teams 1-3 only, 6 matches
        # expected 3 -> FULL; if a 4th team is expected and absent -> PARTIAL
        assert classify_coverage(_matches(pairs), expected_teams=3) == "FULL"
        assert classify_coverage(_matches(pairs), expected_teams=4, threshold=0.5) == "PARTIAL"

    def test_more_teams_than_expected_raises(self):
        with pytest.raises(CoverageCheckError, match="expected_teams"):
            classify_coverage(_matches(_round_robin(4)), expected_teams=3)

    def test_duplicate_match_ids_counted_once(self):
        m = _matches(_round_robin(2))
        assert classify_coverage(m + [m[0]], expected_teams=2) == "FULL"
        assert classify_coverage([m[0], m[0]], expected_teams=2) == "PARTIAL"


def _write(root, comp, season, pairs):
    d = root / "matches" / str(comp)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{season}.json").write_text(json.dumps(_matches(pairs)))


class TestCheckConfiguredCoverage:
    def test_agreement_passes(self, tmp_path):
        _write(tmp_path, 1, 1, _round_robin(4))
        _write(tmp_path, 2, 1, _round_robin(4)[:2])
        check_configured_coverage(
            (CompetitionSeason(1, 1, "A", 4, "FULL", "statsbomb"),
             CompetitionSeason(2, 1, "B", 4, "PARTIAL", "statsbomb")),
            data_root=tmp_path,
        )

    @pytest.mark.parametrize("configured,pairs", [
        ("FULL", _round_robin(4)[:2]),
        ("PARTIAL", _round_robin(4)),
    ])
    def test_disagreement_raises_both_directions(self, tmp_path, configured, pairs):
        _write(tmp_path, 1, 1, pairs)
        with pytest.raises(CoverageCheckError, match=f"configured {configured}"):
            check_configured_coverage(
                (CompetitionSeason(1, 1, "A", 4, configured, "statsbomb"),), data_root=tmp_path
            )

    def test_non_statsbomb_entry_raises(self, tmp_path):
        with pytest.raises(CoverageCheckError, match="non-StatsBomb"):
            check_configured_coverage(
                (CompetitionSeason(1, 1, "A", 4, "FULL", "sportmonks"),), data_root=tmp_path
            )


class TestCheckProcessedMatchIds:
    def _df(self, match_ids, comp=1):
        return pl.DataFrame({
            "match_id": match_ids, "competition_id": [comp] * len(match_ids),
            "season_id": [1] * len(match_ids), "source": ["statsbomb"] * len(match_ids),
        })

    def test_equal_counts_pass(self, tmp_path):
        _write(tmp_path, 1, 1, _round_robin(2))
        cs = (CompetitionSeason(1, 1, "A", 2, "FULL", "statsbomb"),)
        check_processed_match_ids(self._df([1, 1, 2, 2]), cs, tmp_path)

    def test_missing_match_raises(self, tmp_path):
        _write(tmp_path, 1, 1, _round_robin(2))
        cs = (CompetitionSeason(1, 1, "A", 2, "FULL", "statsbomb"),)
        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[2\], extra match_ids \[\]"):
            check_processed_match_ids(self._df([1]), cs, tmp_path)

    def test_competition_absent_from_parquet_raises(self, tmp_path):
        _write(tmp_path, 1, 1, _round_robin(2))
        cs = (CompetitionSeason(1, 1, "A", 2, "FULL", "statsbomb"),)
        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[1, 2\]"):
            check_processed_match_ids(self._df([1, 2], comp=9), cs, tmp_path)

    def test_same_count_different_ids_raises(self, tmp_path):
        """One dropped match plus one mislabelled extra match keeps the count
        equal; the set comparison must still catch both."""
        _write(tmp_path, 1, 1, _round_robin(2))
        cs = (CompetitionSeason(1, 1, "A", 2, "FULL", "statsbomb"),)
        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[2\], extra match_ids \[3\]"):
            check_processed_match_ids(self._df([1, 3]), cs, tmp_path)


_CS_27 = (
    CompetitionSeason(11, 27, "A", 20, "FULL", "statsbomb"),
    CompetitionSeason(2, 27, "B", 20, "FULL", "statsbomb"),
)


class TestCheckSeasonIdsUnique:
    def test_one_to_one_passes(self):
        check_season_ids_unique([
            {"competition_id": 11, "season_id": 27, "season_name": "2015/2016"},
            {"competition_id": 2, "season_id": 27, "season_name": "2015/2016"},
            {"competition_id": 11, "season_id": 4, "season_name": "2018/2019"},
        ], _CS_27)

    def test_one_id_two_names_raises(self):
        with pytest.raises(CoverageCheckError, match="not one-to-one"):
            check_season_ids_unique([
                {"competition_id": 11, "season_id": 27, "season_name": "2015/2016"},
                {"competition_id": 2, "season_id": 27, "season_name": "2016"},
            ], _CS_27)

    def test_one_name_two_ids_raises(self):
        cs = (
            CompetitionSeason(11, 27, "A", 20, "FULL", "statsbomb"),
            CompetitionSeason(2, 28, "B", 20, "FULL", "statsbomb"),
        )
        with pytest.raises(CoverageCheckError, match="not one-to-one"):
            check_season_ids_unique([
                {"competition_id": 11, "season_id": 27, "season_name": "2015/2016"},
                {"competition_id": 2, "season_id": 28, "season_name": "2015/2016"},
            ], cs)

    def test_unconfigured_competitions_ignored(self):
        """An unrelated StatsBomb entry breaking the mapping must not block the build."""
        check_season_ids_unique([
            {"competition_id": 11, "season_id": 27, "season_name": "2015/2016"},
            {"competition_id": 2, "season_id": 27, "season_name": "2015/2016"},
            {"competition_id": 72, "season_id": 27, "season_name": "2019"},
            {"competition_id": 72, "season_id": 30, "season_name": "2015/2016"},
        ], _CS_27)

    def test_configured_pair_missing_from_competitions_raises(self):
        with pytest.raises(CoverageCheckError, match=r"not in competitions.json: \[\(2, 27\)\]"):
            check_season_ids_unique([
                {"competition_id": 11, "season_id": 27, "season_name": "2015/2016"},
            ], _CS_27)
