"""
Project-wide configuration constants.

All configurable thresholds live here so they are never hard-coded in
pipeline or analytics modules.
"""

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
