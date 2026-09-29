"""Downloader CLI argument selection (plan 004 H1.8). Nothing is downloaded."""
from __future__ import annotations

import pytest

from eleveniq.config import COMPETITION_SEASONS, CompetitionSeason, select_competition_seasons
from eleveniq.statsbomb import downloader


@pytest.fixture()
def calls(monkeypatch) -> list[tuple[int, int]]:
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(
        downloader, "download_competition_season",
        lambda competition_id, season_id, **_: recorded.append((competition_id, season_id)),
    )
    return recorded


def test_no_arguments_downloads_every_configured_entry(calls):
    downloader.main([])
    assert calls == [(cs.competition_id, cs.season_id) for cs in COMPETITION_SEASONS]


def test_both_ids_download_one_league(calls):
    downloader.main(["--competition-id", "11", "--season-id", "27"])
    assert calls == [(11, 27)]


def test_unconfigured_pair_exits_with_error_and_downloads_nothing(calls, capsys):
    with pytest.raises(SystemExit) as exc:
        downloader.main(["--competition-id", "11", "--season-id", "999"])
    assert exc.value.code != 0
    assert "No configured competition-season" in capsys.readouterr().err
    assert calls == []


class TestSelectCompetitionSeasons:
    _CONFIG = (
        CompetitionSeason(1, 10, "A", 20, "FULL", "statsbomb"),
        CompetitionSeason(1, 11, "B", 20, "FULL", "statsbomb"),
        CompetitionSeason(2, 10, "C", 20, "PARTIAL", "statsbomb"),
    )

    def test_none_selects_all(self):
        assert select_competition_seasons(None, None, self._CONFIG) == self._CONFIG

    def test_competition_only(self):
        assert [cs.label for cs in select_competition_seasons(1, None, self._CONFIG)] == ["A", "B"]

    def test_season_only(self):
        assert [cs.label for cs in select_competition_seasons(None, 10, self._CONFIG)] == ["A", "C"]

    def test_no_match_raises(self):
        with pytest.raises(ValueError, match="No configured competition-season"):
            select_competition_seasons(3, None, self._CONFIG)
