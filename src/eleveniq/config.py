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

# Competition-seasons ingested by the multi-league pipeline (Plan 003, ADR 003).
# Single source of truth for which (competition_id, season_id) pairs are built,
# their known league size, and their coverage class. coverage drives the
# in_percentile_pool flag: FULL -> True, PARTIAL -> False. See ADR 003 §2-§5.
@dataclass(frozen=True)
class CompetitionSeason:
    competition_id: int
    season_id: int
    label: str
    expected_teams: int
    coverage: Literal["FULL", "PARTIAL"]


# ADR 003 §2: a competition-season is FULL coverage when the available matches
# are at least this share of the n·(n−1) fixtures of a double round-robin.
FULL_COVERAGE_THRESHOLD: float = 0.95


COMPETITION_SEASONS: tuple[CompetitionSeason, ...] = (
    CompetitionSeason(11, 27, "La Liga 2015/16", 20, "FULL"),
    CompetitionSeason(2, 27, "Premier League 2015/16", 20, "FULL"),
    CompetitionSeason(12, 27, "Serie A 2015/16", 20, "FULL"),
    CompetitionSeason(7, 27, "Ligue 1 2015/16", 20, "FULL"),
    CompetitionSeason(9, 27, "1. Bundesliga 2015/16", 18, "PARTIAL"),
)
