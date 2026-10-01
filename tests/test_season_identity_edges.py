"""
Edge-case tests for plan 003 C3: id-keyed season rows, canonical display
names (ADR 002 §28), within/cross-competition flags (ADR 002 §29, ADR 003 §7)
and the coverage build checks (ADR 003 §5).

Unit tests use in-memory frames / tmp_path only. The integration class reads
pipeline output and is skipped when it is missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from eleveniq.config import CompetitionSeason
from eleveniq.pipeline.build_player_season import (
    aggregate_player_season,
    find_name_variants,
    find_teams_in_multiple_competitions,
)
from eleveniq.pipeline.coverage import CoverageCheckError, check_processed_match_ids
from tests.test_build_player_season import _match_row

_SEASON_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "player_season_stats.parquet"
)

_A, _B = 12, 2
_CONFIG = (
    CompetitionSeason(_A, 27, "A 15/16", 20, "FULL", "statsbomb"),
    CompetitionSeason(_B, 27, "B 15/16", 20, "FULL", "statsbomb"),
    CompetitionSeason(_A, 42, "A 16/17", 20, "FULL", "statsbomb"),
    CompetitionSeason(_A, 27, "A 15/16 other source", 20, "FULL", "othersource"),
)


def _agg(*rows: dict) -> pl.DataFrame:
    return aggregate_player_season(pl.DataFrame(list(rows)), competition_seasons=_CONFIG)


class TestCanonicalNameScope:
    """ADR 002 §28: one canonical name per (id, source), chosen across all
    seasons and competitions, so a player has one name everywhere."""

    def test_player_name_shared_across_competitions_and_seasons(self):
        # Majority spelling (after normalisation) lives in competition A 15/16;
        # the other rows (B 15/16, A 16/17) must display the same name.
        result = _agg(
            _match_row(player_id=1, player_name="N''Golo Kanté", team_id=1, competition_id=_A),
            _match_row(player_id=1, player_name="N'Golo Kanté", team_id=1, competition_id=_A),
            _match_row(player_id=1, player_name="Ngolo Kante", team_id=2, competition_id=_B),
            _match_row(player_id=1, player_name="Ngolo Kante", team_id=1, competition_id=_A,
                       season_id=42),
        )
        assert result.height == 3
        assert result["player_name"].unique().to_list() == ["N'Golo Kanté"]
        # Variants are the full raw list, identical on every row.
        for v in result["player_name_variants"].to_list():
            assert v == ["N''Golo Kanté", "N'Golo Kanté", "Ngolo Kante"]

    def test_team_name_shared_across_seasons(self):
        result = _agg(
            _match_row(player_id=1, team_id=140, team_name="Stade Malherbe Caen",
                       competition_id=_A),
            _match_row(player_id=1, team_id=140, team_name="Stade Malherbe Caen",
                       competition_id=_A),
            _match_row(player_id=2, team_id=140, team_name="Caen", competition_id=_A,
                       season_id=42),
        )
        assert result["team_name"].unique().to_list() == ["Stade Malherbe Caen"]

    def test_names_are_per_source(self):
        """Canonical name key includes source: another source's spelling must
        not win (or even appear in variants) for a StatsBomb row."""
        result = _agg(
            _match_row(player_id=1, player_name="Alpha", source="statsbomb", competition_id=_A),
            _match_row(player_id=1, player_name="Zulu", source="othersource", competition_id=_A),
            _match_row(player_id=1, player_name="Zulu", source="othersource", competition_id=_A),
        )
        by_source = {r["source"]: r for r in result.iter_rows(named=True)}
        assert by_source["statsbomb"]["player_name"] == "Alpha"
        assert by_source["statsbomb"]["player_name_variants"] == ["Alpha"]
        assert by_source["othersource"]["player_name"] == "Zulu"
        # Different sources are never the same player-season.
        assert not by_source["statsbomb"]["multi_competition_season"]


class TestNullNames:
    """Missing names must not drop rows, win the canonical choice, or split rows."""

    def test_null_spelling_ignored_when_a_real_one_exists(self):
        result = _agg(
            _match_row(player_id=1, player_name=None, goals=1, competition_id=_A),
            _match_row(player_id=1, player_name=None, goals=1, competition_id=_A),
            _match_row(player_id=1, player_name="Real Name", goals=1, competition_id=_A),
        )
        assert result.height == 1
        row = result.row(0, named=True)
        assert row["goals"] == 3
        assert row["appearances"] == 3
        assert row["player_name"] == "Real Name"
        assert row["player_name_variants"] == ["Real Name"]

    def test_player_with_only_null_names_is_kept(self):
        """ADR 002 §5: rows are never dropped."""
        result = _agg(
            _match_row(player_id=1, player_name=None, competition_id=_A),
            _match_row(player_id=2, player_name="Other", competition_id=_A),
        )
        assert sorted(result["player_id"].to_list()) == [1, 2]


class TestFindNameVariantsEdges:
    def test_raw_whitespace_difference_is_reported(self):
        """The check lists every id with more than one *raw* name, even if the
        spellings normalise to the same display name (e.g. the trailing tab)."""
        df = pl.DataFrame([
            _match_row(player_id=1, player_name="Daniel Arnaud N''Di\t"),
            _match_row(player_id=1, player_name="Daniel Arnaud N''Di"),
        ])
        variants = find_name_variants(df)
        assert variants.select(["entity", "id"]).rows() == [("player", 1)]

    def test_no_variants_returns_empty_frame(self):
        df = pl.DataFrame([_match_row(player_id=1), _match_row(player_id=2)])
        variants = find_name_variants(df)
        assert variants.height == 0
        assert {"entity", "id", "source", "names"} <= set(variants.columns)


class TestFindTeamsInMultipleCompetitions:
    """ADR 003 §7: a team_id may appear in only one competition per
    (season_id, source)."""

    def _season(self, rows):
        return pl.DataFrame(rows, schema={
            "team_id": pl.Int64, "competition_id": pl.Int64,
            "season_id": pl.Int64, "source": pl.String,
        }, orient="row")

    def test_same_season_two_competitions_flagged(self):
        got = find_teams_in_multiple_competitions(self._season([
            (10, 1, 27, "statsbomb"), (10, 2, 27, "statsbomb"), (11, 1, 27, "statsbomb"),
        ]))
        assert got["team_id"].to_list() == [10]
        assert got["competition_ids"].to_list() == [[1, 2]]

    def test_relegation_across_seasons_not_flagged(self):
        """A team in different leagues in different seasons is normal."""
        got = find_teams_in_multiple_competitions(self._season([
            (10, 1, 27, "statsbomb"), (10, 2, 42, "statsbomb"),
        ]))
        assert got.height == 0

    def test_same_ids_in_different_sources_not_flagged(self):
        got = find_teams_in_multiple_competitions(self._season([
            (10, 1, 27, "statsbomb"), (10, 2, 27, "othersource"),
        ]))
        assert got.height == 0


class TestProcessedMatchCountEdges:
    def _write(self, root, n_matches):
        d = root / "matches" / "1"
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps([
            {"match_id": i, "home_team": {"home_team_id": 1},
             "away_team": {"away_team_id": 2}}
            for i in range(1, n_matches + 1)
        ]))

    def _df(self, match_ids, sources=None):
        n = len(match_ids)
        return pl.DataFrame({
            "match_id": match_ids, "competition_id": [1] * n, "season_id": [1] * n,
            "source": sources or ["statsbomb"] * n,
        })

    _CS = (CompetitionSeason(1, 1, "X", 2, "FULL", "statsbomb"),)

    def test_more_processed_than_available_raises(self, tmp_path):
        """Sets must be equal, not merely a superset: extra match_ids mean rows
        were mislabelled into this competition-season."""
        self._write(tmp_path, 2)
        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[\], extra match_ids \[3\]"):
            check_processed_match_ids(self._df([1, 2, 3]), self._CS, tmp_path)

    def test_other_source_rows_do_not_mask_a_missing_statsbomb_match(self, tmp_path):
        """Source isolation: a non-StatsBomb row with the same ids must not
        make up for a StatsBomb match that was dropped."""
        self._write(tmp_path, 2)
        df = self._df([1, 2], sources=["statsbomb", "othersource"])
        with pytest.raises(CoverageCheckError, match=r"missing match_ids \[2\], extra match_ids \[\]"):
            check_processed_match_ids(df, self._CS, tmp_path)


@pytest.mark.integration
@pytest.mark.skipif(not _SEASON_PARQUET.exists(), reason="requires player_season_stats.parquet")
class TestSeasonIdentityIntegration:
    @pytest.fixture(scope="class")
    @classmethod
    def season_df(cls):
        return pl.read_parquet(_SEASON_PARQUET)

    def test_one_display_name_per_player_and_team(self, season_df):
        """ADR 002 §28: one canonical name per (id, source) across all rows."""
        for id_col, name_col in (("player_id", "player_name"), ("team_id", "team_name")):
            bad = (
                season_df.group_by([id_col, "source"])
                .agg(pl.col(name_col).n_unique().alias("n"))
                .filter(pl.col("n") != 1)
            )
            assert bad.height == 0, f"{id_col} with several display names: {bad}"

    def test_display_names_have_no_known_artifacts(self, season_df):
        for col in ("player_name", "team_name"):
            names = season_df[col]
            assert names.null_count() == 0, f"null {col}"
            assert not names.str.contains("''", literal=True).any()
            assert not names.str.contains('"', literal=True).any()
            assert (names == names.str.strip_chars()).all()

    def test_multi_competition_flag_matches_definition(self, season_df):
        """ADR 003 §7: True iff >1 competition_id per (player_id, season_id, source)."""
        key = ["player_id", "season_id", "source"]
        expected = season_df.with_columns(
            (pl.col("competition_id").n_unique().over(key) > 1).alias("_exp")
        )
        assert (expected["_exp"] == expected["multi_competition_season"]).all()

    def test_multi_team_flag_matches_definition(self, season_df):
        """ADR 002 §28/§29: True iff >1 team_id per player-competition-season."""
        key = ["player_id", "competition_id", "season_id", "source"]
        expected = season_df.with_columns(
            (pl.col("team_id").n_unique().over(key) > 1).alias("_exp")
        )
        assert (expected["_exp"] == expected["multi_team_season"]).all()

    def test_flag_and_pool_counts_match_adrs(self, season_df):
        """Independent of the flag definitions above: the 2015/16 counts recorded
        in ADR 002 §28 (68 multi-team player_ids), ADR 003 §7 (47 multi-competition
        player_ids, 94 rows) and ADR 003 §5 (2,279 pool rows)."""
        df = season_df.filter((pl.col("season_id") == 27) & (pl.col("source") == "statsbomb"))
        multi_team = df.filter(pl.col("multi_team_season"))
        multi_comp = df.filter(pl.col("multi_competition_season"))
        assert multi_team["player_id"].n_unique() == 68
        assert multi_comp["player_id"].n_unique() == 47
        assert multi_comp.height == 94
        assert df["in_percentile_pool"].sum() == 2279

    def test_no_team_in_two_competitions(self, season_df):
        assert find_teams_in_multiple_competitions(season_df).height == 0

    def test_row_key_unique_including_source(self, season_df):
        key = ["player_id", "team_id", "competition_id", "season_id", "source"]
        assert season_df.select(key).is_duplicated().sum() == 0
