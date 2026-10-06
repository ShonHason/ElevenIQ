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

# ADR 004 D1: meets_min_minutes compares minutes_nominal_equiv (Σ minutes_played ×
# nominal match length / match_duration) with MIN_MINUTES_THRESHOLD minus this
# tolerance: the float sum of ten full matches often lands at 899.9999999999999.
MIN_MINUTES_TOLERANCE: float = 1e-6

# ADR 004 D5: a season row is position_group_ambiguous when the top two position
# groups' minutes differ by less than this share of minutes_played.
AMBIGUOUS_POSITION_MARGIN: float = 0.05

# ADR 004 §8 (user decision 2026-10-03, plan 005 M4c1): second_position_group is null
# when its group's minutes are below this share of minutes_played, so a fraction of a
# minute (e.g. a goalkeeper's last-minute outfield cameo) does not name a second role.
# The margin still uses the real second-most minutes. It must not exceed
# 1/6 - AMBIGUOUS_POSITION_MARGIN (≈ 0.117, six position groups): an ambiguous row's
# second group always holds more than that, so it keeps its label (enforced by
# TestSecondPositionShareBound).
SECOND_POSITION_MIN_SHARE: float = 0.10

LONG_PASS_THRESHOLD_UNITS: int = 30

# A playing period whose Half End is more than this many minutes before its
# nominal end (45:00, 90:00, 105:00, 120:00) fails the build (period_too_short):
# an abandoned or truncated match. A smaller shortfall is kept as recorded and
# reported. See ADR 002 §31.1.
PERIOD_SHORT_FAIL_MINUTES: float = 5.0

# The two Half End events of one playing period (one per team) may differ by at
# most this many minutes (1 s); a larger gap fails the build. See ADR 002 §31.1.
HALF_END_AGREE_MINUTES: float = 1 / 60

# ADR 004 D22: a stint that starts after its period's Half End is clamped to it and
# reported (stint_after_period_end); starting more than this many minutes after the
# whistle fails the build (stint_late_start_exceeds_limit). 2015/16: two cases, 1 s.
STINT_LATE_START_FAIL_MINUTES: float = 2.0

# ADR 004 D22: per competition-season, a report-only issue fails the build when its
# count exceeds max(limit per 100 matches x matches / 100, REPORT_ONLY_LIMIT_FLOOR).
# Each limit is 3 x the worst 2015/16 league rate (ADR 004 D22 table; numerators
# are entries, except events_outside_credited_time, counted per player-match, and
# the xga_* counters, counted in assisted shots, summed over the season's matches).
REPORT_ONLY_LIMIT_FLOOR: int = 3
REPORT_ONLY_LIMITS_PER_100_MATCHES: dict[str, float] = {
    "period_end_before_nominal": 3 * 17 / 380 * 100,      # Serie A 17 / 380
    "stint_after_period_end": 3 * 1 / 377 * 100,          # Ligue 1 1 / 377
    "treatment_window_inside_stint": 3 * 3 / 377 * 100,   # Ligue 1 3 / 377
    "overlapping_stints": 3 * 5 / 380 * 100,              # PL 5 / 380
    "unpaired_player_off_capped": 3 * 2 / 377 * 100,      # Ligue 1 2 / 377
    "dismissal_after_period_end": 0.0,                    # 0 in 2015/16; floor allows 3
    "dismissal_after_match_end": 0.0,                     # 0 in 2015/16; floor allows 3
    "events_outside_credited_time": 3 * 3 / 380 * 100,    # PL 3 player-matches / 380
    # ADR 004 D16 xG-assisted fallbacks (user, 2026-10-03), in assisted shots.
    "xga_no_receipt": 3 * 6 / 380 * 100,                  # La Liga 6 shots / 380
    "xga_missing_shooter": 0.0,                           # 0 in 2015/16; floor allows 3
    "xga_dangling_shot_id": 0.0,                          # 0 in 2015/16; floor allows 3
    "xga_missing_assister": 0.0,                          # 0 in 2015/16; floor allows 3
}

MIN_AERIAL_CONTESTS: int = 10

# ADR 004 D12: every open-play pass is credited to between these many players of
# the passing team (its passer plus the teammates on the pitch). 7 is the Laws of
# the Game minimum on the pitch, 11 the maximum; 2015/16 observed 8-11. A pass
# outside the range fails the build (pass_credit_out_of_range): a timeline or clock
# error, e.g. a pass credited to its passer only.
MIN_PLAYERS_PER_PASS: int = 7
MAX_PLAYERS_PER_PASS: int = 11

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

# ── Metric registry rules (ADR 004 §10.3; plan 005 M4c) ─────────────────────────
# Applied by eleveniq.analytics.registry.derive() to the committed measurements
# (analytics/measurements/statsbomb_27.json). The spike that writes that file applies
# none of them, so changing one here re-derives every status without regenerating it.

# D19 (user): a DNA metric needs split-half reliability r_full >= this (inclusive).
DNA_RELIABILITY_MIN: float = 0.6
# A DNA cell with r below this (strict) carries a "lower reliability" note (§10.3 step 7).
LOW_RELIABILITY_BELOW: float = 0.7
# §1, R15: a per-90 whose gated zero share is above this (strict) is a signature trait
# when r_full >= DNA_RELIABILITY_MIN, otherwise display.
ZERO_HEAVY_SHARE: float = 0.30
# U5: a ratio enters DNA only when at least this share of gated rows meet min_attempts.
RATIO_MIN_COVERAGE: float = 0.90
# D12 (§10.3 step 5): a cell is possession-triggered when, for at least one proxy, the
# mean of the four within-league Spearman rho is >= this in absolute value (inclusive)
# and the sign test passes. Leagues with |rho| below POSSESSION_SIGN_NEUTRAL have no sign.
POSSESSION_RHO_TRIGGER: float = 0.4
POSSESSION_SIGN_NEUTRAL: float = 0.05
# D12: an allow-list entry admits a triggered cell when the larger proxy |mean rho| is
# <= this (inclusive). No entry may declare a looser bound.
POSSESSION_ALLOW_MAX_RHO: float = 0.55
# D12b: team_pass_share enters DNA (DM-CM, CB only) iff |rho| < this on both proxies.
TEAM_PASS_SHARE_MAX_RHO: float = 0.3
# D17: a pair of metrics at |rho| >= this in a group keeps one (an explicit choice).
COLLINEAR_RHO: float = 0.85
# D9: global_ok iff every pool league's mean global percentile is in this band (inclusive).
GLOBAL_OK_BAND: tuple[float, float] = (40.0, 60.0)
# ADR 004 D11: a reference population smaller than this gives null percentiles
# (cohort_too_small). §11.1 (user, 2026-10-06): D9 is also undefined when any pool league's
# population for the cell is below it — a league mean over so few rows cannot test the band.
MIN_COHORT_SIZE: int = 30
# ADR 004 D6: a season row with at least this many GK minutes and at least this many
# outfield minutes stops the cohort build until it is explained (2015/16: none).
MIXED_POSITION_MINUTES: float = 45.0
# §10.2: a signature trait's minimum season count is max(this, the group's P90 of the
# count among gated rows). Integer count columns only; xG sums have no count minimum.
SIGNATURE_TRAIT_MIN_COUNT_FLOOR: int = 5
# §10.10: derive() reports every judged value within this distance of its threshold.
BOUNDARY_MARGIN: float = 0.02
# §10.10 (user, 2026-10-05): derive() also reports DNA cells whose D9 margin (the
# nearest pool league mean's distance to the GLOBAL_OK_BAND edge) is within this many
# percentile points. A league mean of percentiles over ~80 players has a standard
# error of ≈ 29/√80 ≈ 3.2 pts, so a margin under 3 pts is within sampling noise.
D9_BOUNDARY_MARGIN_PTS: float = 3.0

# ADR 004 §12 (user, 2026-10-06): the DNA vector uses one percentile basis, league-local,
# for every row. 29 of 101 DNA cells have no global percentile (D9) and 51 are within
# noise of flipping, so a global or per-cell basis would not be stable.
DNA_PERCENTILE_BASIS: str = "local"
# ADR 004 §12: neighbours per row in the M4f weighting checks (not a product setting).
DNA_NEIGHBOURS_K: int = 10
# Dimension scores withheld (null, with the score_status given) per family and group. The
# metrics stay in the vector, so similarity is unaffected.
# - Defending, every group (ADR 004 §12.6, user 2026-10-06): until M4g settles the
#   possession question.
# - Passing where its only directional DNA member is long_pass_completion_pct (ADR 004
#   §12.9, user 2026-10-07): that metric reflects team style (§11.5), so a one-metric
#   Passing score would present a low value as poor individual passing. M4g decides
#   whether to restore it from the pre-registered xPass candidate (plan 005 M4g). ST
#   keeps Passing (pass_completion_pct).
#   tests/test_dna.py::test_passing_withheld_exactly_where_long_pass_completion_is_its_only_
#   directional_member re-derives these groups from the registry: re-check it if they change.
DNA_WITHHELD_DIMENSIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Defending": ("pending_possession_adjustment", ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")),
    "Passing": ("pending_mover_stability", ("CB", "FB-WB", "DM-CM", "AM-Winger")),
}
