"""
Project-wide configuration constants.

All configurable thresholds live here so they are never hard-coded in
pipeline or analytics modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Minimum minutes a player must have played in a season to be eligible for
# percentile calculations, similarity, and Gem Finder.  Players below this
# threshold are kept in player_season_stats.parquet but flagged with
# meets_min_minutes=False.  See ADR 002 §5.
MIN_MINUTES_THRESHOLD: int = 900

LONG_PASS_THRESHOLD_UNITS: int = 30

MIN_AERIAL_CONTESTS: int = 10

# Maximum cumulative carry distance (StatsBomb units) allowed between a shooter's
# last Ball Receipt* and their shot for the assist to count as "direct".
# Any Dribble in the same window also disqualifies. See ADR 002 §xg_assisted_direct.
XG_ASSISTED_MAX_CARRY_UNITS: float = 10.0

# Provenance tag written to the `source` column of every StatsBomb row and used
# by every check that filters on it (CLAUDE.md: one source per cohort).
SOURCE_STATSBOMB: str = "statsbomb"

# Competition-seasons ingested by the multi-league pipeline (Plan 003, ADR 003).
# Single source of truth for which (competition_id, season_id, source) keys are
# built, their known league size, and their coverage class. coverage drives the
# in_percentile_pool flag: FULL -> True, PARTIAL -> False. See ADR 003 §2-§5.
# source is required (no default) so an entry can never silently inherit
# StatsBomb provenance.
@dataclass(frozen=True)
class CompetitionSeason:
    competition_id: int
    season_id: int
    label: str
    expected_teams: int
    coverage: Literal["FULL", "PARTIAL"]
    source: str


# ADR 003 §2: a competition-season is FULL coverage when the available matches
# are at least this share of the n·(n−1) fixtures of a double round-robin.
FULL_COVERAGE_THRESHOLD: float = 0.95


COMPETITION_SEASONS: tuple[CompetitionSeason, ...] = (
    CompetitionSeason(11, 27, "La Liga 2015/16", 20, "FULL", SOURCE_STATSBOMB),
    CompetitionSeason(2, 27, "Premier League 2015/16", 20, "FULL", SOURCE_STATSBOMB),
    CompetitionSeason(12, 27, "Serie A 2015/16", 20, "FULL", SOURCE_STATSBOMB),
    CompetitionSeason(7, 27, "Ligue 1 2015/16", 20, "FULL", SOURCE_STATSBOMB),
    CompetitionSeason(9, 27, "1. Bundesliga 2015/16", 18, "PARTIAL", SOURCE_STATSBOMB),
)


def validate_competition_seasons(competition_seasons: tuple[CompetitionSeason, ...]) -> None:
    """
    Raise ValueError if two entries share (competition_id, season_id, source),
    which would fan out the in_percentile_pool join (ADR 003 §5), or if an
    entry has expected_teams < 2 (a league needs n·(n−1) > 0 fixtures, ADR 003 §2),
    or if coverage is not exactly "FULL" or "PARTIAL" (Literal is not enforced at
    runtime, and a typo would silently drop a league from the pools).
    """
    bad_coverage = [
        (cs.label, cs.coverage) for cs in competition_seasons
        if cs.coverage not in ("FULL", "PARTIAL")
    ]
    if bad_coverage:
        raise ValueError(f'coverage must be "FULL" or "PARTIAL": {bad_coverage}')
    too_small = [cs.label for cs in competition_seasons if cs.expected_teams < 2]
    if too_small:
        raise ValueError(f"expected_teams must be >= 2: {too_small}")
    keys = [(cs.competition_id, cs.season_id, cs.source) for cs in competition_seasons]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"Duplicate COMPETITION_SEASONS keys: {dupes}")


validate_competition_seasons(COMPETITION_SEASONS)


def select_competition_seasons(
    competition_id: int | None,
    season_id: int | None,
    competition_seasons: tuple[CompetitionSeason, ...] = COMPETITION_SEASONS,
) -> tuple[CompetitionSeason, ...]:
    """
    Entries matching the given ids (None matches any). Used by the pipeline and
    downloader CLIs. Raises ValueError when nothing matches, so an unconfigured
    pair is never silently built or downloaded.
    """
    selected = tuple(
        cs for cs in competition_seasons
        if (competition_id is None or cs.competition_id == competition_id)
        and (season_id is None or cs.season_id == season_id)
    )
    if not selected:
        raise ValueError(
            f"No configured competition-season matches "
            f"competition_id={competition_id} season_id={season_id}"
        )
    return selected
