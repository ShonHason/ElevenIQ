"""
Tests for analytics/percentiles.py (ADR 004 D11, §11.1): hand-computed Hazen percentiles
with average ties, outsiders, the cohort minimum and the (0, 100) bound for members.
"""
from __future__ import annotations

import random

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics.percentiles import hazen, score_cell


def _pct(pop: list[float | None], values: list[float | None]) -> list[float | None]:
    return hazen(pl.Series(pop, dtype=pl.Float64), pl.Series(values, dtype=pl.Float64)).to_list()


class TestHazen:
    def test_hand_computed_cohort_with_ties(self):
        # n = 4: 1 → (0 + 0.5·1)/4; 2 → (1 + 0.5·2)/4 for both tied rows; 3 → (3 + 0.5)/4.
        assert _pct([1, 2, 2, 3], [1, 2, 2, 3]) == [12.5, 50.0, 50.0, 87.5]

    def test_ties_take_the_average_rank_not_the_minimum(self):
        # A min-rank tie rule would give the tied 2s (1 + 0.5)/4 = 37.5 (mutation guard).
        assert _pct([1, 2, 2, 3], [2]) == [50.0]
        assert _pct([0, 0, 0, 0, 5], [0]) == [40.0]   # a zero mass: 0.5 · zero share

    def test_outsider_can_be_exactly_0_or_100(self):
        # §11.1: a row scored against a population without it.
        assert _pct([1, 2, 3, 4], [0.5, 9.0, 2.0]) == [0.0, 100.0, 37.5]

    def test_null_value_stays_null_and_population_nulls_are_dropped(self):
        assert _pct([1, None, 3], [None, 3]) == [None, 75.0]

    def test_empty_population_gives_nulls(self):
        assert _pct([], [1.0, 2.0]) == [None, None]

    def test_members_are_strictly_inside_0_100(self):
        rng = random.Random(27)
        for _ in range(200):
            n = rng.randint(1, 60)
            pop = [float(rng.randint(0, 5)) for _ in range(n)]   # many ties
            pcts = _pct(pop, pop)
            assert all(0.0 < p < 100.0 for p in pcts)

    def test_bounds_follow_population_size(self):
        assert _pct([7.0], [7.0]) == [50.0]
        pop = [float(i) for i in range(30)]
        pcts = _pct(pop, pop)
        assert min(pcts) == pytest.approx(0.5 / 30 * 100) and max(pcts) == pytest.approx(29.5 / 30 * 100)


class TestScoreCell:
    def test_cohort_of_29_is_null_and_30_is_scored(self):
        pop29 = pl.Series([float(i) for i in range(config.MIN_COHORT_SIZE - 1)])
        pct, n = score_cell(pop29, pl.Series([3.0]))
        assert pct is None and n == 29
        pop30 = pl.Series([float(i) for i in range(config.MIN_COHORT_SIZE)])
        pct, n = score_cell(pop30, pl.Series([3.0]))
        assert n == 30 and pct.to_list() == [pytest.approx(3.5 / 30 * 100)]

    def test_n_counts_non_null_population_rows(self):
        pop = pl.Series([1.0, None, 2.0])
        assert score_cell(pop, pl.Series([1.0]), min_cohort_size=2)[1] == 2
        assert score_cell(pop, pl.Series([1.0]), min_cohort_size=3)[0] is None
