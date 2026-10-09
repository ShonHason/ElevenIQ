"""analytics/style_map.py (plan 006 W1): the weighted PCA and its display rules, as properties
on seeded synthetic rows of the real DM-CM DNA list (no quoted loadings for DM-CM)."""
from __future__ import annotations

import random

import pytest

from eleveniq.analytics import registry as R
from eleveniq.analytics import style_map as S
from eleveniq.analytics.dna import RowDna, VectorCell, dna_specs, pair_distance

GROUP = "DM-CM"
SPECS = dna_specs(R.REGISTRY, R.SPECS, GROUP)


def _rows(n: int = 40, seed: int = 7, partial: int = 0) -> dict[tuple, RowDna]:
    rng = random.Random(seed)
    out = {}
    for i in range(n + partial):
        cells = {s.column: VectorCell(s.family, s.direction, 1.0, rng.uniform(1, 99), 50, "ok",
                                      None, None, None, None, "ok", None) for s in SPECS}
        status = "complete"
        if i >= n:
            m = SPECS[0].column
            cells[m] = VectorCell(SPECS[0].family, SPECS[0].direction, None, None, None,
                                  "below_min_attempts", None, None, None, None,
                                  "below_min_attempts", None)
            status = "partial"
        out[(i, 1, 2, 27, "statsbomb")] = RowDna(GROUP, status, dict(sorted(cells.items())), {})
    return out


@pytest.fixture(scope="module")
def fitted():
    rows = _rows(partial=2)
    return rows, S.fit(GROUP, SPECS, rows)


def test_only_complete_rows_are_plotted(fitted):
    rows, sm = fitted
    assert len(sm.keys) == 40 and len(sm.partial) == 2


def test_input_distance_is_the_pair_distance(fitted):
    rows, sm = fitted
    a, b = sm.keys[0], sm.keys[1]
    xa = S.weighted_input(sm.model.metrics, sm.model.weights, rows[a])
    xb = S.weighted_input(sm.model.metrics, sm.model.weights, rows[b])
    assert sum((u - v) ** 2 for u, v in zip(xa, xb)) == pytest.approx(pair_distance(rows[a], rows[b]),
                                                                       abs=1e-12)


def test_pins_load_positive(fitted):
    _, sm = fitted
    for a, pin in enumerate(S.AXIS_PINS[GROUP]):
        assert sm.model.components[a][sm.model.metrics.index(pin)] > 0


def test_pushes_sum_to_the_score_and_projection_matches(fitted):
    rows, sm = fitted
    k = sm.keys[3]
    for a in (0, 1):
        assert sum(p for _, p in S.pushes(sm.model, rows[k], a)) == pytest.approx(sm.scores[k][a], abs=1e-12)
    x = S.weighted_input(sm.model.metrics, sm.model.weights, rows[k])
    assert S.project(sm.model, x) == pytest.approx(sm.scores[k], abs=1e-12)


def test_row_order_does_not_change_the_map():
    rows = _rows()
    rev = dict(reversed(list(rows.items())))
    a, b = S.fit(GROUP, SPECS, rows), S.fit(GROUP, SPECS, rev)
    for ca, cb in zip(a.model.components, b.model.components):
        assert ca == pytest.approx(cb, abs=1e-12)


def test_end_metrics_and_lean_band():
    m = S.Model("X", ("a", "b", "c", "d"), (0.25,) * 4, (0.0,) * 4,
                ((0.6, 0.15, 0.1499, -0.7), (0.0,) * 4), (0.5, 0.2), (2.0, 1.0))
    assert S.end_metrics(m, 0, 1) == ["a", "b"]               # 0.1499 does not reach 0.15
    assert S.end_metrics(m, 0, -1) == ["d"]
    assert [S.lean_band(s, 2.0) for s in (0.99, 1.0, 2.99, 3.0)] == ["near", "slight", "slight", "strong"]


def test_a_weak_pin_fails():
    import numpy as np
    with pytest.raises(S.StyleMapError, match="pin"):
        S.orient(np.array([[0.1, 0.9], [0.9, 0.1]]), ("p", "q"), ("p", "p"))


def test_a_changed_quoted_loading_fails():
    m = S.Model("CB", ("aerials_won_p90", "clearances_p90"), (0.5, 0.5), (0.0, 0.0),
                ((-0.40, -0.19), (0.82, 0.0)), (0.3, 0.2), (1.0, 1.0))
    with pytest.raises(S.StyleMapError, match="quoted"):
        S.check_quoted(m)


def _cb_model(pc2_extra: float = 0.0, pc1_first: float = -0.47):
    ms = ("aerials_won_p90", "clearances_p90", "final_third_entries_pass_p90",
          "progressive_passes_p90", "other")
    return S.Model("CB", ms, (0.2,) * 5, (0.0,) * 5,
                   ((pc1_first, -0.19, 0.37, 0.36, 0.0), (0.82, 0.0, 0.0, 0.0, pc2_extra)),
                   (0.3, 0.2), (1.0, 1.0))


def test_quoted_loading_claims_pass_and_catch_end_and_extreme_changes():
    S.check_quoted(_cb_model())
    with pytest.raises(S.StyleMapError, match="no longer the most positive"):
        S.check_quoted(_cb_model(pc2_extra=0.9))
    with pytest.raises(S.StyleMapError, match="end"):
        S.check_quoted(_cb_model(pc2_extra=-0.2))          # a negative end appears on PC2
    with pytest.raises(S.StyleMapError, match="quoted"):
        S.check_quoted(_cb_model(pc1_first=-0.50))


@pytest.mark.parametrize("sd", [0.0, -1.0])
def test_lean_band_refuses_an_axis_without_spread(sd):
    with pytest.raises(S.StyleMapError, match="spread"):
        S.lean_band(1.0, sd)


def test_a_value_claim_without_a_metric_is_an_error_not_an_assert(monkeypatch):
    m = S.Model("CB", ("aerials_won_p90", "clearances_p90"), (0.5, 0.5), (0.0, 0.0),
                ((-0.40, -0.19), (0.82, 0.0)), (0.3, 0.2), (1.0, 1.0))
    monkeypatch.setattr(S, "QUOTED_LOADINGS", (("CB", 1, "value", None, 0.5),))
    with pytest.raises(S.StyleMapError, match="without a metric"):
        S.check_quoted(m)
