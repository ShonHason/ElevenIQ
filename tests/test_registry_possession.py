"""
Registry possession records and the Defending D5 status (ADR 004 §13.8; plan 005 M4g4d).

Each test edits one field of a copy of the committed measurements (schema 3) and re-derives.
"""
from __future__ import annotations

import copy
import math

import pytest

from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import RegistryRuleError, derive, load_measurements

_BR = "ball_recoveries_p90"


@pytest.fixture
def meas():
    return copy.deepcopy(load_measurements())


def _status(meas, group: str) -> R.FamilyStatus:
    return derive(measurements=meas).defending_status(group)


def _set_br_ci(meas, lo: float, hi: float, extra: list[tuple[float, float]] | None = None) -> None:
    m = meas["movers"]["metrics"][_BR]
    m["ci_lo"], m["ci_hi"] = lo, hi
    for e, (elo, ehi) in zip(m["extra_seeds"], extra or [(lo, hi)] * len(m["extra_seeds"])):
        e["ci_lo"], e["ci_hi"] = elo, ehi


# ── the committed 2015/16 outcome ─────────────────────────────────────────────

def test_2015_16_defending_statuses_and_notes():
    want = {"CB": R.POSSESSION_CRITERIA_FAILED, "FB-WB": R.POSSESSION_CRITERIA_FAILED,
            "DM-CM": R.MOVER_STABILITY_INCONCLUSIVE, "AM-Winger": R.POSSESSION_CRITERIA_FAILED,
            "ST": R.POSSESSION_CRITERIA_FAILED}
    for g, st in want.items():
        fs = R.REGISTRY.defending_status(g)
        assert (fs.status, fs.member) == (st, _BR)
    dm = R.REGISTRY.defending_status("DM-CM")
    assert dm.candidate == "beta_0" and dm.notes == ("underpowered at 24",)
    assert R.REGISTRY.defending_status("CB").candidate is None


def test_no_cell_enters_the_vector_adjusted_and_the_seven_selections_fail_the_trust_check():
    adjusted = {k: p for k, p in R.REGISTRY.possession.items()
                if p.selected not in (None, R.RAW_CANDIDATE)}
    assert len(R.REGISTRY.possession) == 34 and len(adjusted) == 7
    assert all(p.applied == R.RAW_CANDIDATE for p in R.REGISTRY.possession.values())
    assert all(p.trust is not None and not p.trust.passes for p in adjusted.values())
    assert all(p.trust is None for k, p in R.REGISTRY.possession.items() if k not in adjusted)


def test_candidate_movers_beta_0_reuses_m4g3b_and_the_adjusted_runs_are_report_only():
    cm = R.REGISTRY.candidate_movers
    assert set(cm) == {(_BR, c) for c in R.POSSESSION_CANDIDATES}
    assert cm[(_BR, "beta_0")].verdict == R.REGISTRY.mover(_BR)
    assert cm[(_BR, "beta_0")].used_for_restore_in == ("DM-CM",)
    for c in R.POSSESSION_CANDIDATES[1:]:
        assert cm[(_BR, c)].used_for_restore_in == ()
        assert cm[(_BR, c)].verdict.verdict == "inconclusive"


def test_cb_flags_rises_with_own_possession():
    p = R.REGISTRY.possession[(_BR, "CB")]
    assert p.beta_hat is not None and p.beta_hat < 0 and "rises with own possession" in p.flags


# ── D5 precedence on DM-CM (β = 0 selected; status from its mover verdict) ─────

def test_all_six_streams_pass_restores_defending(meas):
    _set_br_ci(meas, -0.2, 0.4)
    fs = _status(meas, "DM-CM")
    assert fs.status == R.SCORE_OK
    assert _status(meas, "CB").status == R.POSSESSION_CRITERIA_FAILED   # a failed group stays


def test_primary_pass_with_an_extra_stream_not_pass_is_seed_sensitive(meas):
    _set_br_ci(meas, -0.2, 0.4, [(-0.2, 0.6)] + [(-0.2, 0.4)] * 4)
    assert _status(meas, "DM-CM").status == R.MOVER_SEED_SENSITIVE


def test_fail_verdict(meas):
    _set_br_ci(meas, 0.6, 1.4)
    assert _status(meas, "DM-CM").status == R.MOVER_STABILITY_FAILED


@pytest.mark.parametrize("s2, want", [(1.21, R.MOVER_BELOW_MODEL_RANGE), (0.81, R.SCORE_OK)])
def test_below_model_range_restores_only_when_scale_a_agrees(meas, s2, want):
    # D7: PASS with CI upper bound < 0 restores only when √s2_rms_a ≤ 1
    _set_br_ci(meas, -0.9, -0.1)
    meas["movers"]["metrics"][_BR]["s2_rms_a"] = s2
    assert math.sqrt(s2) != 1
    assert _status(meas, "DM-CM").status == want


def test_too_few_movers_is_untestable(meas):
    m = meas["movers"]["metrics"][_BR]
    m["n"] = 11
    for k in ("f_hat", "ci_lo", "ci_hi", "cross_check_f_hat", "cross_check_r"):
        m.pop(k, None)
    for e in m["extra_seeds"]:
        e["n"] = 11
        e.pop("ci_lo")
        e.pop("ci_hi")
    assert _status(meas, "DM-CM").status == R.MOVER_STABILITY_UNTESTABLE


def test_precedence_picks_the_member_furthest_along_ties_to_the_first_name():
    def adj(col: str, selected: str | None) -> R.PossessionAdjustment:
        return R.PossessionAdjustment(col, "CB", True, None, None, (), selected, None, "beta_0", ())

    def mover(col: str, verdict: str, extra: tuple[str, ...]) -> R.CandidateMover:
        return R.CandidateMover(col, "beta_0", R.MoverVerdict(
            col, verdict, 24, ci=(0.0, 0.4), power_at_f0=0.9, underpowered=False,
            borderline_seed=any(v != verdict for v in extra), below_model_range=False,
            extra_seed_verdicts=extra), ("CB",))

    poss = {("a_p90", "CB"): adj("a_p90", None), ("b_p90", "CB"): adj("b_p90", "beta_0"),
            ("c_p90", "CB"): adj("c_p90", "beta_0")}
    movers = {("b_p90", "beta_0"): mover("b_p90", "inconclusive", ("inconclusive",) * 5),
              ("c_p90", "beta_0"): mover("c_p90", "PASS", ("PASS", "inconclusive", "PASS",
                                                           "PASS", "PASS"))}
    fs = R._defending_status(poss, movers)[("Defending", "CB")]
    assert (fs.status, fs.member) == (R.MOVER_SEED_SENSITIVE, "c_p90")
    # a tie at the same stage goes to the first metric name
    movers[("c_p90", "beta_0")] = mover("c_p90", "FAIL", ("FAIL",) * 5)
    fs = R._defending_status(poss, movers)[("Defending", "CB")]
    assert (fs.status, fs.member) == (R.MOVER_STABILITY_INCONCLUSIVE, "b_p90")
    # any member that passes restores the family
    movers[("c_p90", "beta_0")] = mover("c_p90", "PASS", ("PASS",) * 5)
    assert R._defending_status(poss, movers)[("Defending", "CB")].status == R.SCORE_OK


# ── refusals ────────────────────────────────────────────────────────────────────

def test_missing_block_raises(meas):
    del meas["possession"]
    with pytest.raises(RegistryRuleError, match="possession block"):
        derive(measurements=meas)


@pytest.mark.parametrize("sec, key, value", [
    ("rules", "rho_max", 0.12), ("rules", "gap_max", 12.0), ("rules", "r_full_min", 0.5),
    ("trust_rules", "stab_min", 0.7), ("trust_rules", "d_min", 20),
])
def test_engine_rules_other_than_the_registrys_raise(meas, sec, key, value):
    meas["possession"][sec][key] = value
    with pytest.raises(RegistryRuleError, match=f"possession.{sec}.{key}"):
        derive(measurements=meas)


def test_an_engine_selection_the_rules_do_not_reproduce_raises(meas):
    meas["possession"]["cells"][_BR]["CB"]["spike_selected"] = "beta_hat"
    with pytest.raises(RegistryRuleError, match="the rules select None"):
        derive(measurements=meas)


def test_a_changed_criterion_value_moves_the_selection_and_raises(meas):
    # DM-CM ball_recoveries β = 0 fails criterion (2) if its gap exceeds 10 on one proxy
    meas["possession"]["cells"][_BR]["DM-CM"]["candidates"]["beta_0"]["c2_gap"]["poss_dur_loo"]["gap"] = 10.5
    with pytest.raises(RegistryRuleError, match="engine 'beta_0'"):
        derive(measurements=meas)


def test_a_selection_without_a_trust_record_raises(meas):
    del meas["possession"]["trust"]["clearances_p90"]["DM-CM"]
    with pytest.raises(RegistryRuleError, match="has no trust record"):
        derive(measurements=meas)


def test_a_trusted_adjusted_selection_raises_not_implemented(meas):
    t = meas["possession"]["trust"]["clearances_p90"]["DM-CM"]      # β̂; (c), (d) pass
    t["a"]["p_same"] = 0.9
    t["b"]["selected_without"] = {k: "beta_hat" for k in t["b"]["selected_without"]}
    with pytest.raises(RegistryRuleError, match="adjusted columns are not implemented"):
        derive(measurements=meas)


def test_possession_cells_must_be_the_defending_dna_cells(meas):
    meas["possession"]["cells"][_BR]["GK"] = meas["possession"]["cells"][_BR]["CB"]
    with pytest.raises(RegistryRuleError, match="possession cells differ"):
        derive(measurements=meas)


def test_the_version_covers_the_possession_block(meas):
    meas["possession"]["cells"][_BR]["CB"]["e_ref"] += 1.0
    assert derive(measurements=meas).version != R.REGISTRY_VERSION


# ── fix round (code-reviewer, M4g4d) ────────────────────────────────────────────

def test_a_restored_member_beats_a_withheld_one_whatever_the_name_order():
    """D5: Defending is restored if any directional member passes, even when the withheld
    member sorts first (a_p90 possession_criteria_failed, z_p90 ok)."""
    def adj(col: str, selected: str | None) -> R.PossessionAdjustment:
        return R.PossessionAdjustment(col, "DM-CM", True, None, None, (), selected, None,
                                      "beta_0", ())

    ok = R.MoverVerdict("z_p90", "PASS", 24, ci=(0.0, 0.4), power_at_f0=0.9, underpowered=False,
                        borderline_seed=False, below_model_range=False,
                        extra_seed_verdicts=("PASS",) * 5, s2_rms_a=0.9)
    poss = {("a_p90", "DM-CM"): adj("a_p90", None), ("z_p90", "DM-CM"): adj("z_p90", "beta_0")}
    movers = {("z_p90", "beta_0"): R.CandidateMover("z_p90", "beta_0", ok, ("DM-CM",))}
    fs = R._defending_status(poss, movers)[("Defending", "DM-CM")]
    assert (fs.status, fs.member, fs.candidate) == (R.SCORE_OK, "z_p90", "beta_0")


def test_a_testable_mover_entry_without_s2_rms_a_raises(meas):
    del meas["movers"]["metrics"][_BR]["s2_rms_a"]
    with pytest.raises(RegistryRuleError, match="s2_rms_a"):
        derive(measurements=meas)


def test_below_model_range_without_s2_rms_a_raises_never_withholds():
    v = R.MoverVerdict(_BR, "PASS", 24, ci=(-0.9, -0.1), power_at_f0=0.9, underpowered=False,
                       borderline_seed=False, below_model_range=True,
                       extra_seed_verdicts=("PASS",) * 5, s2_rms_a=None)
    with pytest.raises(RegistryRuleError, match="without s2_rms_a"):
        R._member_status(v)


@pytest.mark.parametrize("path, match", [
    (("cells", _BR, "CB", "spike_selected"), "ball_recoveries_p90 × CB: missing 'spike_selected'"),
    (("cells", _BR, "CB", "candidates", "beta_hat", "c3_r_full"),
     "beta_hat: missing 'c3_r_full'"),
    (("trust", "clearances_p90", "DM-CM", "c", "p_gap"), "trust: missing 'c.p_gap'"),
])
def test_a_malformed_record_names_the_cell_and_the_missing_key(meas, path, match):
    rec = meas["possession"]
    for k in path[:-1]:
        rec = rec[k]
    del rec[path[-1]]
    with pytest.raises(RegistryRuleError, match=match):
        derive(measurements=meas)


def test_a_second_directional_member_without_its_mover_runs_is_refused(meas):
    meas["possession"]["movers"]["metric"] = "pressures_p90"
    meas["movers"]["metrics"].setdefault("pressures_p90", meas["movers"]["metrics"][_BR])
    with pytest.raises(RegistryRuleError, match="candidate mover runs are missing"):
        derive(measurements=meas)
