"""
M4g3b reference hardening (plan 005 M4g4-0, the backlog gate before the M4g4 movers-block write).
Each test tampers one input and expects the check to go red, from the written items:
1. the bootstrap size is checked against B = 2000, never taken from the file under check;
2. with both the output and the measurement block given, each one's power_at_f0 is checked
   (and the two must agree), so a wrong block power is not hidden by the output's value;
3. the block's cross_check_f_hat = (Ŝ² − 1)(1 − R) / R with R the reference cross value; extra
   seeds are paired by position (entry i carries k = i + 1); no cross-check field without a
   reference cross value;
4. the Koné illustration is report only: compute() runs without it, main() adds it;
5. a null count column fails the mover run instead of dropping a baseline candidate as NaN;
6. R_m ≤ 0 has power 0 (no math domain error), in the reference and in m4g3_power.low_power.
No data needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g3_movers as mv  # noqa: E402
import m4g3_movers_reference as ref  # noqa: E402
import m4g3_power as pw  # noqa: E402

RES = {"f_hat": 0.3, "ci": [-0.2, 1.0], "r_m": 0.59, "s2_rms": 1.44, "boot_inf": 0}
CROSS = 0.58
EXTRAS = [{"n": 24, "res": {"ci": [-0.1, 0.9]}} for _ in range(ref.EXTRA)]


def good_block_metric() -> dict:
    return {"n": 24, "f_hat": 0.3, "ci_lo": -0.2, "ci_hi": 1.0, "r_m": 0.59, "s2_rms": 1.44,
            "boot_inf": 0, "power_at_f0": 0.6, "power_se": 0.01, "cross_check_r": CROSS,
            "cross_check_f_hat": (1.44 - 1) * (1 - CROSS) / CROSS,
            "extra_seeds": [{"k": i + 1, "n": 24, "ci_lo": -0.1, "ci_hi": 0.9}
                            for i in range(ref.EXTRA)]}


def test_consistent_block_metric_passes():
    assert ref.block_metric_errors("m", good_block_metric(), 24, RES, CROSS, EXTRAS) == []


# 1. bootstrap size
def test_b_is_checked_against_the_spec():
    assert ref.header_errors("block", {"b": 2000}) == []
    assert ref.header_errors("block", {"b": 200})
    assert ref.header_errors("output", {})


# 2. both powers checked
def test_a_wrong_block_power_is_not_hidden_by_the_output():
    errs = ref.power_errors("m", [("block", 0.20, 0.01), ("output", 0.64, 0.01)], 0.64)
    assert any("block" in e for e in errs)


def test_agreeing_powers_pass():
    assert ref.power_errors("m", [("block", 0.64, 0.01), ("output", 0.64, 0.01)], 0.64) == []


def test_output_and_block_power_must_agree_exactly():
    errs = ref.power_errors("m", [("block", 0.640, 0.01), ("output", 0.645, 0.01)], 0.64)
    assert any("!=" in e for e in errs)


# 3. cross-check f̂, seed pairing, absent cross fields
def test_tampered_cross_check_f_hat_fails():
    bm = good_block_metric()
    bm["cross_check_f_hat"] += 0.01
    assert ref.block_metric_errors("m", bm, 24, RES, CROSS, EXTRAS)


def test_missing_cross_check_f_hat_fails():
    bm = good_block_metric()
    del bm["cross_check_f_hat"]
    assert ref.block_metric_errors("m", bm, 24, RES, CROSS, EXTRAS)


def test_extra_seeds_out_of_order_fail():
    bm = good_block_metric()
    bm["extra_seeds"][0]["k"], bm["extra_seeds"][1]["k"] = 2, 1
    assert ref.block_metric_errors("m", bm, 24, RES, CROSS, EXTRAS)
    assert ref.extra_k_errors("output m", [{"k": 2}, {"k": 1}])


@pytest.mark.parametrize("key", ["cross_check_r", "cross_check_f_hat"])
def test_cross_fields_without_a_reference_cross_value_fail(key):
    bm = good_block_metric()
    other = {"cross_check_r", "cross_check_f_hat"} - {key}
    for k in other:
        del bm[k]
    errs = ref.block_metric_errors("m", bm, 24, RES, None, EXTRAS)
    assert any(key in e for e in errs)


# 4. Koné is main()'s, not compute()'s
class _EmptyCtx:
    movers: list = []
    halves: list = []
    third: list = []
    results: list = []
    meas: dict = {"ratios": {}, "metrics": {}, "team_pass_share": {}}

    def min_att(self, metric, grp):  # pragma: no cover - no movers, never called
        raise AssertionError


def test_compute_runs_without_kone():
    out = mv.compute(b=10, power_datasets=1, extra_seeds=0, verbose=False, ctx=_EmptyCtx())
    assert "kone" not in out
    assert all(r["verdict"] == "untestable" for r in out["metrics"].values())


def test_kone_illustration_fails_without_kone():
    with pytest.raises(SystemExit):
        mv.kone_illustration(_EmptyCtx())


# 5. null count columns
def test_null_count_column_fails():
    frame = pl.DataFrame({"ball_recoveries": [1.0, None], "minutes_played": [90.0, 45.0]})
    with pytest.raises(SystemExit):
        mv.require_no_nulls(frame, {"ball_recoveries", "minutes_played"}, "player_match_stats")


def test_no_nulls_passes():
    frame = pl.DataFrame({"ball_recoveries": [1.0, 0.0], "minutes_played": [90.0, 45.0]})
    mv.require_no_nulls(frame, {"ball_recoveries", "minutes_played"}, "player_match_stats")


# 6. R_m ≤ 0
@pytest.mark.parametrize("r_m", [0.0, -0.2])
def test_reference_power_is_zero_at_nonpositive_r_m(r_m):
    rng = np.random.default_rng(1)
    state = rng.bit_generator.state
    assert ref.power0(24, r_m, rng) == 0.0
    assert rng.bit_generator.state == state


def test_reference_power_is_zero_below_min_movers():
    assert ref.power0(ref.MIN_N - 1, 0.7, np.random.default_rng(1)) == 0.0


@pytest.mark.parametrize("r_m", [0.0, -0.2])
def test_low_power_at_nonpositive_r_m(r_m):
    lp = pw.low_power(24, r_m)
    assert lp["power_at_f0"] == 0.0 and lp["se"] == 0.0 and lp["underpowered"] is True


# review fixes (code-reviewer, M4g4-0)
@pytest.mark.parametrize("key", ["ci_lo", "ci_hi"])
def test_missing_extra_seed_ci_is_a_clean_error(key):
    bm = good_block_metric()
    del bm["extra_seeds"][2][key]
    errs = ref.block_metric_errors("m", bm, 24, RES, CROSS, EXTRAS)
    assert any(f"{key} missing" in e for e in errs)


def test_with_kone_places_kone_after_third_stints():
    run = {"generated_by": "g", "seed": 1, "draws": 2, "b": 3, "movers": [], "third_stints": [],
           "metrics": {}, "extra_seeds": {}}
    out = mv.with_kone(run, [{"player": "Lamine Koné"}])
    assert list(out) == ["generated_by", "seed", "draws", "b", "movers", "third_stints", "kone",
                         "metrics", "extra_seeds"]
    with pytest.raises(SystemExit):
        mv.with_kone(out, [])


@pytest.mark.parametrize("power,mine", [(0.79, 0.83), (0.83, 0.79), (0.70, 0.90), (0.90, 0.70)])
def test_a_label_disagreement_is_caught_unless_mine_is_near_the_bar(power, mine):
    """Labels differ (one side of 0.8 each): either the power check fires, or mine is within
    the tolerance band of 0.8 (the M4g3b rule: no label comparison there)."""
    se = 0.005
    errs = ref.power_errors("m", [("block", power, se)], mine)
    band = 4 * max((se ** 2 + mine * (1 - mine) / ref.POWER_DATASETS) ** 0.5, 1e-3)
    assert errs or abs(mine - ref.LOW) <= band
    if abs(mine - ref.LOW) > band:
        assert errs


_DATA = (mv.SEASON_PARQUET, mv.MATCH_PARQUET, mv.MEASUREMENTS)


@pytest.mark.integration
@pytest.mark.skipif(not all(p.exists() for p in _DATA), reason="season / match parquet missing")
@pytest.mark.parametrize("target", ["season", "match"])
def test_ctx_fails_on_a_null_in_either_parquet(target, monkeypatch):
    path = mv.SEASON_PARQUET if target == "season" else mv.MATCH_PARQUET
    real = pl.read_parquet

    def tampered(source, *args, **kwargs):
        df = real(source, *args, **kwargs)
        if Path(source) == path:
            df = df.with_columns(pl.lit(None, dtype=df.schema["ball_recoveries"]).alias("ball_recoveries"))
        return df

    monkeypatch.setattr(mv.pl, "read_parquet", tampered)
    with pytest.raises(SystemExit):
        mv.Ctx()
