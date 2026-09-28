"""
coverage.py — build-time checks that tie the configured competition-seasons
to the downloaded StatsBomb files (ADR 003 §2, §5).

Config is the single source of truth for pool membership; these checks only
fail the build when config and data disagree. They never change a class.

Public API
----------
classify_coverage(matches, expected_teams, threshold) -> "FULL" | "PARTIAL"
    Pure ADR 003 §2 rule applied to one matches.json list.
check_configured_coverage(competition_seasons, data_root)
    Recompute every class from matches.json; raise on disagreement.
check_processed_match_ids(match_df, competition_seasons, data_root)
    The set of match_ids per competition-season in player_match_stats must
    equal the set in matches.json.
check_season_ids_unique(competitions, competition_seasons)
    Across the configured competition-seasons, StatsBomb season_id <->
    season_name must be one-to-one, because multi_competition_season keys
    the season on season_id.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Literal

import polars as pl

from eleveniq.config import (
    COMPETITION_SEASONS,
    FULL_COVERAGE_THRESHOLD,
    SOURCE_STATSBOMB,
    CompetitionSeason,
)
from eleveniq.statsbomb.loader import load_matches


class CoverageCheckError(ValueError):
    """Configured competition-seasons disagree with the downloaded data."""


def _teams(matches: list[dict]) -> set[int]:
    return {m["home_team"]["home_team_id"] for m in matches} | {
        m["away_team"]["away_team_id"] for m in matches
    }


def classify_coverage(
    matches: list[dict],
    expected_teams: int,
    threshold: float = FULL_COVERAGE_THRESHOLD,
) -> Literal["FULL", "PARTIAL"]:
    """
    ADR 003 §2: FULL when available / (n·(n−1)) >= threshold and every one of
    the n = expected_teams teams appears in at least one match; else PARTIAL.

    n is the known league size from config, not the teams seen in the file,
    so a file that hides teams entirely cannot pass as FULL. More teams in the
    file than expected is a config error and raises.
    """
    n_seen = len(_teams(matches))
    if n_seen > expected_teams:
        raise CoverageCheckError(
            f"{n_seen} teams in matches.json but expected_teams={expected_teams}"
        )
    available = len({m["match_id"] for m in matches})
    ratio = available / (expected_teams * (expected_teams - 1))
    if ratio >= threshold and n_seen == expected_teams:
        return "FULL"
    return "PARTIAL"


def _statsbomb_only(competition_seasons: Iterable[CompetitionSeason]) -> None:
    other = [cs.label for cs in competition_seasons if cs.source != SOURCE_STATSBOMB]
    if other:
        raise CoverageCheckError(f"No matches.json loader for non-StatsBomb entries: {other}")


def check_configured_coverage(
    competition_seasons: Iterable[CompetitionSeason] = COMPETITION_SEASONS,
    data_root: Path | None = None,
    threshold: float = FULL_COVERAGE_THRESHOLD,
) -> None:
    """Raise CoverageCheckError listing every entry whose configured coverage
    disagrees with classify_coverage on its matches.json."""
    competition_seasons = tuple(competition_seasons)
    _statsbomb_only(competition_seasons)
    errors = []
    for cs in competition_seasons:
        matches = load_matches(cs.competition_id, cs.season_id, data_root)
        derived = classify_coverage(matches, cs.expected_teams, threshold)
        if derived != cs.coverage:
            errors.append(f"{cs.label}: configured {cs.coverage}, matches.json gives {derived}")
    if errors:
        raise CoverageCheckError("; ".join(errors))


def check_processed_match_ids(
    match_df: pl.DataFrame,
    competition_seasons: Iterable[CompetitionSeason] = COMPETITION_SEASONS,
    data_root: Path | None = None,
) -> None:
    """
    Raise CoverageCheckError when the set of match_ids of a configured
    competition-season in player_match_stats differs from the set in its
    matches.json, so a pool is never built on silently missing or mislabelled
    matches. The error lists missing ids (in matches.json, not processed) and
    extra ids (processed, not in matches.json).
    """
    competition_seasons = tuple(competition_seasons)
    _statsbomb_only(competition_seasons)
    processed = {
        (r["competition_id"], r["season_id"]): set(r["ids"])
        for r in match_df.filter(pl.col("source") == SOURCE_STATSBOMB)
        .group_by(["competition_id", "season_id"])
        .agg(pl.col("match_id").unique().alias("ids"))
        .iter_rows(named=True)
    }
    errors = []
    for cs in competition_seasons:
        available = {m["match_id"] for m in load_matches(cs.competition_id, cs.season_id, data_root)}
        got = processed.get((cs.competition_id, cs.season_id), set())
        missing, extra = sorted(available - got), sorted(got - available)
        if missing or extra:
            errors.append(f"{cs.label}: missing match_ids {missing}, extra match_ids {extra}")
    if errors:
        raise CoverageCheckError("; ".join(errors))


def check_season_ids_unique(
    competitions: list[dict],
    competition_seasons: Iterable[CompetitionSeason] = COMPETITION_SEASONS,
) -> None:
    """
    Raise CoverageCheckError unless, across the configured competition-seasons,
    each StatsBomb season_id maps to exactly one season_name and vice versa
    (competitions.json). Keying the season on season_id across competitions
    relies on this. Unconfigured competitions are ignored, so an unrelated
    StatsBomb entry cannot block the build; a configured competition-season
    missing from competitions.json raises.
    """
    competition_seasons = tuple(competition_seasons)
    _statsbomb_only(competition_seasons)
    configured = {(cs.competition_id, cs.season_id) for cs in competition_seasons}
    listed = [c for c in competitions if (c["competition_id"], c["season_id"]) in configured]
    absent = sorted(configured - {(c["competition_id"], c["season_id"]) for c in listed})
    if absent:
        raise CoverageCheckError(f"Configured competition-season(s) not in competitions.json: {absent}")
    names_by_id: dict[int, set[str]] = defaultdict(set)
    ids_by_name: dict[str, set[int]] = defaultdict(set)
    for c in listed:
        names_by_id[c["season_id"]].add(c["season_name"])
        ids_by_name[c["season_name"]].add(c["season_id"])
    bad = {k: v for k, v in names_by_id.items() if len(v) > 1} | {
        k: v for k, v in ids_by_name.items() if len(v) > 1
    }
    if bad:
        raise CoverageCheckError(f"season_id <-> season_name not one-to-one: {bad}")
