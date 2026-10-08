"""Mover verdicts in the registry (ADR 004 §13.7, plan 005 M4g3b; registry._movers, Derivation.mover).

Spec: PASS iff CI upper < 0.5, FAIL iff CI lower > 0.5, else inconclusive; n < 12 untestable;
"+inf" allowed only for f_hat and CI bounds; verdicts are report-only (no cell, status or note
depends on them) but do enter the registry version; the block must cover its own metric list
and every DNA metric.
"""
from __future__ import annotations

import copy
import json

import pytest

from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import RegistryRuleError, Rules, derive, load_measurements
from tools import registry_snapshot as S

_COL = "aerials_lost_p90"          # committed: n 24, testable


@pytest.fixture
def meas() -> dict:
    return copy.deepcopy(load_measurements())


def _set(meas, lo, hi, col=_COL, extra=None):
    m = meas["movers"]["metrics"][col]
    m["ci_lo"], m["ci_hi"] = lo, hi
    for e in m["extra_seeds"]:
        e["ci_lo"], e["ci_hi"] = (lo, hi) if extra is None else extra
    return derive(measurements=meas).mover(col)


class TestVerdictRule:
    @pytest.mark.parametrize("lo,hi,want", [
        (-0.3, 0.4999, "PASS"),
        (-0.3, 0.5, "inconclusive"),        # upper bound exactly at the threshold is not a PASS
        (0.5, 0.9, "inconclusive"),         # lower bound exactly at the threshold is not a FAIL
        (0.5001, 0.9, "FAIL"),
        (0.2, 0.9, "inconclusive"),
    ])
    def test_thresholds_are_strict(self, meas, lo, hi, want):
        assert _set(meas, lo, hi).verdict == want

    def test_infinite_bounds_are_accepted_and_never_pass(self, meas):
        v = _set(meas, 0.8, "+inf")
        assert v.verdict == "FAIL" and v.ci[1] == float("inf")
        assert _set(copy.deepcopy(meas), -0.1, "+inf").verdict == "inconclusive"

    def test_min_n_boundary(self, meas):
        for n, want in ((11, "untestable"), (12, "inconclusive")):
            mm = copy.deepcopy(meas)
            m = mm["movers"]["metrics"][_COL]
            m["n"] = n
            for e in m["extra_seeds"]:
                e["n"] = n
            m["ci_lo"], m["ci_hi"] = -0.1, 0.9
            if n < 12:
                for k in ("f_hat", "ci_lo", "ci_hi", "r_m", "power_at_f0", "power_se"):
                    m.pop(k)
                for e in m["extra_seeds"]:
                    e.pop("ci_lo"); e.pop("ci_hi")
            v = derive(measurements=mm).mover(_COL)
            assert v.verdict == want
            if n < 12:
                assert v.f_hat is None and v.ci is None and v.underpowered is None
                assert set(v.extra_seed_verdicts) == {"untestable"}


class TestFlags:
    def test_below_model_range_only_for_pass_with_negative_upper(self, meas):
        assert _set(meas, -0.9, -0.1).below_model_range is True
        assert _set(copy.deepcopy(meas), -0.9, 0.2).below_model_range is False
        assert _set(copy.deepcopy(meas), -0.9, 0.7).below_model_range is False

    def test_borderline_seed_when_an_extra_seed_differs(self, meas):
        assert _set(meas, -0.3, 0.4, extra=(-0.3, 0.6)).borderline_seed is True
        assert _set(copy.deepcopy(meas), -0.3, 0.4, extra=(-0.2, 0.3)).borderline_seed is False

    def test_underpowered_is_strict_below_the_threshold(self, meas):
        meas["movers"]["metrics"][_COL]["power_at_f0"] = 0.8
        assert derive(measurements=meas).mover(_COL).underpowered is False
        meas["movers"]["metrics"][_COL]["power_at_f0"] = 0.799
        assert derive(measurements=meas).mover(_COL).underpowered is True

    def test_flags_do_not_change_the_verdict(self, meas):
        v = _set(meas, -0.9, -0.1)
        assert v.verdict == "PASS" and v.underpowered is not None


class TestReportOnly:
    def test_committed_registry_reproduces_with_movers(self, meas):
        d = derive(measurements=meas)
        assert d.movers == R.REGISTRY.movers and d.version == R.REGISTRY.version

    def test_changing_a_verdict_moves_the_version_but_no_cell(self, meas):
        base = derive(measurements=meas)
        _set(meas, 0.8, "+inf")
        again = derive(measurements=meas)
        assert again.version != base.version
        assert dict(again.cells) == dict(base.cells)

    def test_changing_a_mover_threshold_moves_the_version(self, meas):
        assert derive(measurements=meas, rules=Rules(mover_f_threshold=0.6)).version != R.REGISTRY.version

    def test_mover_lookup_of_unknown_metric_raises(self):
        with pytest.raises(KeyError):
            R.REGISTRY.mover("not_a_metric")

    def test_every_dna_metric_has_a_verdict(self):
        for g in R.GROUPS:
            for c in R.REGISTRY.dna_metrics(g):
                assert R.REGISTRY.mover(c).verdict in {"PASS", "FAIL", "inconclusive", "untestable"}


class TestBlockValidation:
    def test_missing_block(self, meas):
        del meas["movers"]
        with pytest.raises(RegistryRuleError, match="movers block"):
            derive(measurements=meas)

    def test_metric_list_and_metrics_differ(self, meas):
        meas["movers"]["metric_list"].append("ghost_p90")
        with pytest.raises(RegistryRuleError):
            derive(measurements=meas)

    def test_dna_metric_without_verdict(self, meas):
        col = R.REGISTRY.dna_metrics("ST")[0]
        meas["movers"]["metric_list"].remove(col)
        del meas["movers"]["metrics"][col]
        with pytest.raises(RegistryRuleError, match="DNA metrics without a verdict"):
            derive(measurements=meas)

    def test_metric_without_spec(self, meas):
        meas["movers"]["metric_list"].append("ghost_p90")
        meas["movers"]["metrics"]["ghost_p90"] = copy.deepcopy(meas["movers"]["metrics"][_COL])
        with pytest.raises(RegistryRuleError, match="no registry spec"):
            derive(measurements=meas)

    @pytest.mark.parametrize("key,bad", [("f_hat", "-inf"), ("r_m", "+inf"), ("power_at_f0", None),
                                         ("power_se", "+inf"), ("ci_lo", "nan"), ("ci_hi", True)])
    def test_non_finite_or_non_numeric_numbers(self, meas, key, bad):
        meas["movers"]["metrics"][_COL][key] = bad
        with pytest.raises(RegistryRuleError):
            derive(measurements=meas)

    def test_extra_seed_n_must_equal_primary_n(self, meas):
        meas["movers"]["metrics"][_COL]["extra_seeds"][0]["n"] = 23
        with pytest.raises(RegistryRuleError, match="extra seeds"):
            derive(measurements=meas)

    def test_untestable_must_not_carry_f_hat(self, meas):
        m = meas["movers"]["metrics"][_COL]
        m["n"] = 5
        for e in m["extra_seeds"]:
            e["n"] = 5
        with pytest.raises(RegistryRuleError, match="untestable"):
            derive(measurements=meas)


class TestReviewFixes:
    """M4g3b fix delta (code-reviewer): readable errors, min_movers, the committed untestable
    record, borderline with a primary FAIL and with an infinite bound."""

    def test_committed_untestable_record_derives_untestable(self):
        # aerial_win_pct as movers_block() wrote it (n 4): no f̂, CI or power keys
        rec = load_measurements()["movers"]["metrics"]["aerial_win_pct"]
        assert rec["n"] == 4 and "f_hat" not in rec
        v = R.REGISTRY.mover("aerial_win_pct")
        assert v.verdict == "untestable" and v.n == 4
        assert v.f_hat is None and v.ci is None and v.power_at_f0 is None
        assert v.underpowered is None and v.borderline_seed is None and v.below_model_range is None

    def test_primary_fail_with_an_extra_seed_pass_is_borderline(self, meas):
        m = meas["movers"]["metrics"][_COL]
        m["ci_lo"], m["ci_hi"] = 0.6, 1.2                          # FAIL
        for e in m["extra_seeds"]:
            e["ci_lo"], e["ci_hi"] = 0.6, 1.2
        m["extra_seeds"][2]["ci_lo"], m["extra_seeds"][2]["ci_hi"] = -0.2, 0.3   # PASS on k = 3
        v = derive(measurements=meas).mover(_COL)
        assert v.verdict == "FAIL" and v.borderline_seed is True
        assert v.extra_seed_verdicts[2] == "PASS"

    def test_borderline_with_an_infinite_bound(self, meas):
        assert _set(meas, -0.1, "+inf", extra=(-0.2, 0.3)).borderline_seed is True
        assert _set(copy.deepcopy(meas), -0.1, "+inf", extra=(-0.2, "+inf")).borderline_seed is False

    @pytest.mark.parametrize("key", ["power_se", "ci_lo", "r_m", "extra_seeds"])
    def test_missing_key_names_metric_and_key(self, meas, key):
        meas["movers"]["metrics"][_COL].pop(key)
        with pytest.raises(RegistryRuleError, match=rf"{_COL}.*'{key}'"):
            derive(measurements=meas)

    def test_missing_extra_seed_key_names_metric(self, meas):
        meas["movers"]["metrics"][_COL]["extra_seeds"][0].pop("ci_hi")
        with pytest.raises(RegistryRuleError, match=rf"{_COL}.extra_seeds.*'ci_hi'"):
            derive(measurements=meas)

    def test_block_min_movers_must_equal_rules(self, meas):
        with pytest.raises(RegistryRuleError, match="min_movers 12.*mover_min_n is 10"):
            derive(measurements=meas, rules=Rules(mover_min_n=10))
        meas["movers"].pop("min_movers")
        with pytest.raises(RegistryRuleError, match="block: missing 'min_movers'"):
            derive(measurements=meas)


class TestSnapshotMovers:
    def test_snapshot_movers_cover_registry_and_are_strict_json_with_inf(self, meas):
        _set(meas, 0.8, "+inf")
        d = derive(measurements=meas)
        snap = S.snapshot(d)
        assert set(snap["movers"]) == set(d.movers)
        assert snap["movers"][_COL]["ci"] == [0.8, "+inf"]
        json.dumps(snap, allow_nan=False)      # raises on a bare inf/nan


class TestCommittedS2RmsA:
    """ADR 004 §13.8 D7: s2_rms_a (RMS S^2 on local percentiles) is stored for testable metrics only."""

    def test_present_iff_testable_and_finite_positive(self):
        block = load_measurements()["movers"]
        for name, rec in block["metrics"].items():
            testable = rec["n"] >= block["min_movers"]
            assert ("s2_rms_a" in rec) == testable, name
            assert ("f_hat" in rec) == testable, name
            if testable:
                assert 0 < rec["s2_rms_a"] < 100, name

    def test_documented_below_model_range_values(self):
        # plan 005 M4g4a outcome: sqrt(s2_rms_a) 0.818 and 0.964, both <= 1
        mm = load_measurements()["movers"]["metrics"]
        assert mm["fouls_won_p90"]["s2_rms_a"] ** 0.5 == pytest.approx(0.818, abs=1e-3)
        assert mm["op_xg_assisted_p90"]["s2_rms_a"] ** 0.5 == pytest.approx(0.964, abs=1e-3)
