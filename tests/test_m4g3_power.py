"""
M4g3 mover rule (plan 005 M4g3; ADR 004 §13.1, §13.7; spikes/m4g3_power.py), tested from the
written rule with hand-computed inputs:
- f̂ = (Ŝ² − 1)(1 − R_m) / R_m, Ŝ² = Σ Δ² / Σ baseline, baseline = mean over draws of (h1 − h2)²,
  R_m = one Pearson over the matched halves pooled over all draws;
- PASS iff the CI upper bound < 0.5; FAIL iff the lower bound > 0.5; otherwise inconclusive
  (a bound equal to 0.5 is inconclusive);
- fewer than 12 movers → untestable, not a pass;
- R_m ≤ 0 → f̂ = +inf, never a PASS;
- non-finite input raises (a failed upstream step must not read as "inconclusive");
- a mover's stints are taken in date order;
- power_at_f0: 0 for an untestable n or R_m ≤ 0; deterministic; higher at a higher R_m.

Hand-built halves used below (one pattern, every mover the same, so every bootstrap resample
gives the same f̂ and the CI collapses to the point):
  h1 = [1, −1, 1, −1, 3, −3], h2 = [1, −1, −1, 1, 3, −3]
  Σh1h2 = 18, Σh1² = Σh2² = 22, means 0 → R_m = 9/11, (1 − R_m)/R_m = 2/9;
  baseline = (0 + 0 + 4 + 4 + 0 + 0)/6 = 4/3.
  Δ² = 4 → Ŝ² = 3 → f̂ = 4/9 (PASS); Δ² = 6 → Ŝ² = 4.5 → f̂ = 7/9 (FAIL);
  Δ² = 13/3 → Ŝ² = 3.25 → f̂ = 0.5.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g3_power as pw  # noqa: E402

H1 = [1.0, -1.0, 1.0, -1.0, 3.0, -3.0]
H2 = [1.0, -1.0, -1.0, 1.0, 3.0, -3.0]


def _inputs(d2: list[float] | float, n: int = 24, h1=H1, h2=H2):
    d2 = np.full(n, d2, dtype=float) if np.isscalar(d2) else np.asarray(d2, dtype=float)
    return d2, np.tile(h1, (len(d2), 1)), np.tile(h2, (len(d2), 1))


def _rule(*args, **kw):
    return pw.mover_rule(*args, rng=np.random.default_rng(1), b=400, **kw)


# ── f̂ and the three outcomes ────────────────────────────────────────────────

def test_pass_hand_computed():
    r = _rule(*_inputs(4.0))
    assert r.r_m == pytest.approx(9 / 11)
    assert r.s2 == pytest.approx(3.0)
    assert r.f_hat == pytest.approx(4 / 9)
    assert r.ci_lo == pytest.approx(4 / 9) and r.ci_hi == pytest.approx(4 / 9)
    assert r.verdict == "PASS"
    assert r.point_pass


def test_fail_hand_computed():
    r = _rule(*_inputs(6.0))
    assert r.f_hat == pytest.approx(7 / 9)
    assert r.ci_lo == pytest.approx(7 / 9)
    assert r.verdict == "FAIL"
    assert not r.point_pass


def test_just_below_and_above_the_threshold():
    # f̂ = (Ŝ² − 1) · 2/9, Ŝ² = Δ² / (4/3): Δ² = 13/3 ∓ 0.003 → f̂ = 0.5 ∓ 0.0005.
    assert _rule(*_inputs(13 / 3 - 0.003)).verdict == "PASS"
    assert _rule(*_inputs(13 / 3 + 0.003)).verdict == "FAIL"


def test_spread_ci_straddling_the_threshold_is_inconclusive():
    # 12 movers with Δ² = 0 and 12 with Δ² = 26/3: pooled Ŝ² = 3.25, f̂ = 0.5; the bootstrap
    # spreads the share of large changes, so the CI covers 0.5 on both sides.
    r = _rule(*_inputs([0.0] * 12 + [26 / 3] * 12))
    assert r.f_hat == pytest.approx(0.5)
    assert r.ci_lo < 0.5 < r.ci_hi
    assert r.verdict == "inconclusive"


@pytest.mark.parametrize("lo, hi, want", [
    (0.1, 0.4999, "PASS"),
    (0.1, 0.5, "inconclusive"),        # upper bound equal to 0.5 is not a PASS
    (0.5, 0.9, "inconclusive"),        # lower bound equal to 0.5 is not a FAIL
    (0.5001, 0.9, "FAIL"),
    (0.3, 0.7, "inconclusive"),
    (math.inf, math.inf, "FAIL"),
    (0.2, math.inf, "inconclusive"),
])
def test_verdict_boundaries(lo, hi, want):
    assert pw.verdict_of(lo, hi) == want


# ── untestable ──────────────────────────────────────────────────────────────

def test_eleven_movers_untestable_and_rng_untouched():
    rng = np.random.default_rng(5)
    before = rng.bit_generator.state
    r = pw.mover_rule(*_inputs(4.0, n=11), rng=rng, b=400)
    assert r.verdict == "untestable"
    assert not r.point_pass
    assert math.isnan(r.f_hat)
    assert rng.bit_generator.state == before


def test_twelve_movers_testable():
    assert _rule(*_inputs(4.0, n=12)).verdict == "PASS"


# ── R_m ≤ 0 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h2", [
    [1.0, -1.0, -1.0, 1.0, -3.0, 3.0],     # Σh1h2 = 1 + 1 − 1 − 1 − 9 − 9 < 0
    [-1.0, 1.0, -1.0, 1.0, -3.0, 3.0],     # h2 = −h1: R_m = −1
])
def test_non_positive_r_m_gives_inf_never_pass(h2):
    # even with Δ² = 0 (Ŝ² = 0), which would otherwise give a negative f̂
    r = _rule(*_inputs(0.0, h2=h2))
    assert r.r_m <= 0
    assert r.f_hat == math.inf
    assert r.verdict != "PASS"
    assert not r.point_pass
    assert r.boot_inf == 400


def test_zero_r_m_is_inf():
    # Σh1h2 = 1 − 1 − 1 + 1 = 0 → R_m = 0
    r = _rule(*_inputs(4.0, h1=[1.0, 1.0, -1.0, -1.0], h2=[1.0, -1.0, 1.0, -1.0]))
    assert r.r_m == pytest.approx(0.0)
    assert r.f_hat == math.inf
    assert r.verdict != "PASS"


def test_some_resamples_with_non_positive_r_m():
    # 14 movers with pattern A (Σh1h2 = 18, Σh² = 22, baseline 4/3) and 10 with h2 = −h1
    # (Σh1h2 = −22, Σh² = 22, baseline 44/3): pooled R_m = (14·18 − 10·22)/(24·22) = 32/528 > 0.
    # A resample with ≤ 13 A movers has R_m ≤ 0 (f̂ = +inf); Δ² = each mover's own baseline
    # makes Ŝ² = 1 in every resample, so f̂ = 0 wherever R_m > 0.
    neg = [-v for v in H1]
    d2 = np.array([4 / 3] * 14 + [44 / 3] * 10)
    h1 = np.tile(H1, (24, 1))
    h2 = np.array([H2] * 14 + [neg] * 10)
    r = _rule(d2, h1, h2)
    assert r.r_m == pytest.approx(32 / 528)
    assert r.f_hat == pytest.approx(0.0)
    assert 0 < r.boot_inf < 400
    assert r.ci_lo == pytest.approx(0.0)
    assert r.ci_hi == math.inf
    assert r.verdict == "inconclusive"


# ── non-finite input ────────────────────────────────────────────────────────

@pytest.mark.parametrize("where, value", [
    ("d2", math.nan), ("d2", math.inf), ("h1", math.nan), ("h2", -math.inf),
])
def test_non_finite_input_raises(where, value):
    d2, h1, h2 = _inputs(4.0)
    {"d2": d2, "h1": h1[3], "h2": h2[7]}[where][0] = value
    with pytest.raises(ValueError, match="non-finite"):
        _rule(d2, h1, h2)


def test_non_finite_input_raises_even_when_untestable():
    d2, h1, h2 = _inputs(4.0, n=5)
    d2[0] = math.nan
    with pytest.raises(ValueError, match="non-finite"):
        _rule(d2, h1, h2)


def test_zero_baseline_raises():
    d2, h1, _ = _inputs(4.0)
    with pytest.raises(ValueError, match="baseline 0"):
        _rule(d2, h1, h1.copy())


def test_shape_mismatch_raises():
    d2, h1, h2 = _inputs(4.0)
    with pytest.raises(ValueError, match="shapes"):
        _rule(d2[:-1], h1, h2)


# ── quantile with +inf ──────────────────────────────────────────────────────

def test_quantile_matches_numpy_on_finite():
    x = np.random.default_rng(3).normal(size=2000)
    for q in (0.025, 0.975):
        assert pw._quantile(x, q) == pytest.approx(float(np.quantile(x, q)))


def test_quantile_keeps_inf():
    x = np.array([0.1] * 10 + [math.inf] * 990)
    assert pw._quantile(x, 0.975) == math.inf       # numpy gives NaN here
    assert pw._quantile(x, 0.0) == pytest.approx(0.1)


# ── date order ──────────────────────────────────────────────────────────────

def test_stints_in_date_order():
    late = {"team_id": 1, "first": dt.date(2016, 2, 1), "last": dt.date(2016, 5, 15)}
    early = {"team_id": 99, "first": dt.date(2015, 8, 8), "last": dt.date(2016, 1, 2)}
    assert pw.order_by_date([late, early]) == [early, late]


def test_same_first_date_ordered_by_last_then_team():
    a = {"team_id": 5, "first": dt.date(2015, 8, 8), "last": dt.date(2016, 1, 2)}
    b = {"team_id": 3, "first": dt.date(2015, 8, 8), "last": dt.date(2016, 5, 1)}
    c = {"team_id": 2, "first": dt.date(2015, 8, 8), "last": dt.date(2016, 5, 1)}
    assert pw.order_by_date([b, a, c]) == [a, c, b]


# ── power_at_f0 ─────────────────────────────────────────────────────────────

def test_power_untestable_or_no_signal_is_zero():
    assert pw.power_at_f0(11, 0.9) == 0.0
    assert pw.power_at_f0(24, 0.0) == 0.0
    assert pw.power_at_f0(24, -0.2) == 0.0


def test_power_numpy_scalars_same_stream():
    assert (pw.power_at_f0(np.int64(24), np.float64(0.6), datasets=100, b=200)
            == pw.power_at_f0(24, 0.6, datasets=100, b=200))


def test_low_power_label_carries_se():
    lab = pw.low_power(24, 0.6, datasets=100, b=200)
    p = pw.power_at_f0(24, 0.6, datasets=100, b=200)
    assert lab["power_at_f0"] == p
    assert lab["se"] == pytest.approx(math.sqrt(p * (1 - p) / 100))
    assert lab["underpowered"] == (p < 0.8)


def test_power_deterministic_and_rises_with_reliability():
    lo = pw.power_at_f0(24, 0.4, datasets=150, b=300)
    hi = pw.power_at_f0(24, 0.95, datasets=150, b=300)
    assert pw.power_at_f0(24, 0.4, datasets=150, b=300) == lo
    assert 0.0 <= lo < hi <= 1.0
    assert hi > pw.LOW_POWER
