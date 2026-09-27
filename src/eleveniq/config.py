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

LONG_PASS_THRESHOLD_YDS: int = 30

MIN_AERIAL_CONTESTS: int = 10
