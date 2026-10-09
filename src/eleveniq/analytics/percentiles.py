"""
Percentiles (plan 005 M4d, ADR 004 D11, §11.1). Pure functions; the populations come
from analytics.cohorts.

percentile = (less + 0.5 · equal) / n × 100 against a reference population of size n.
For a member of the population this is Hazen, (rank − 0.5) / n with average ranks for
ties, so it lies strictly inside (0, 100). A row scored against a population that does
not contain it (a D3 outsider, a D5 second-group row) can be exactly 0 or 100.

A percentile is a rank of the raw value (high = more of the metric). The registry's
direction only colours it (ADR 004 §10.7).
"""
from __future__ import annotations

import polars as pl

from eleveniq import config


def hazen(population: pl.Series, values: pl.Series) -> pl.Series:
    """Each value's percentile against `population` (nulls dropped). All null when the
    population is empty; a null value stays null."""
    pop = population.drop_nulls().sort()
    n = pop.len()
    if n == 0:
        return pl.Series(values.name, [None] * values.len(), dtype=pl.Float64)
    lo = pop.search_sorted(values, side="left").cast(pl.Float64)
    hi = pop.search_sorted(values, side="right").cast(pl.Float64)
    pct = (lo + 0.5 * (hi - lo)) / n * 100.0
    return pl.select(pl.when(values.is_null()).then(None).otherwise(pct)).to_series().alias(values.name)


def score_cell(population: pl.Series, values: pl.Series,
               min_cohort_size: int = config.MIN_COHORT_SIZE) -> tuple[pl.Series | None, int]:
    """(percentiles, n) for `values` against `population`, n its non-null size. The
    percentiles are None when n < min_cohort_size (cohort_too_small, D11)."""
    n = population.drop_nulls().len()
    if n < min_cohort_size:
        return None, n
    return hazen(population, values), n
