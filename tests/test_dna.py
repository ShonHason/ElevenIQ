"""
Fast tests for analytics/dna.py and the build's input guard (plan 005 M4f2, ADR 004 §10.8,
§12). Written from the ADR text: robust z with the MAD → IQR → zero_spread fallback, row
weights re-split within a family, dna_status, directional dimension scores with Defending
withheld, and a symmetric pair distance over the metrics both rows have.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics import dna as D
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import GROUPS, REGISTRY, SPECS
from eleveniq.pipeline.build_player_dna import DnaInputError, check_percentiles

_ROOT = Path(__file__).resolve().parents[1]
_SPIKE = _ROOT / "spikes" / "m4f_dna_reference.py"
_BY_COL = {s.column: s for s in SPECS}


def _row_dna(group, specs, inputs, withheld=None):
    return D.row_dna(group, specs, inputs,
                     withheld=D.withheld_for(group) if withheld is None else withheld)


def _specs(group: str):
    return D.dna_specs(REGISTRY, SPECS, group)


def _inputs(group: str, pct: dict[str, float] | None = None, missing: tuple[str, ...] = (),
            spread: D.Spread = D.Spread(1.0, 2.0, "mad")) -> dict[str, D.MetricInput]:
    """Every DNA metric of `group` ok at percentile 50 (or `pct`), value 3; `missing` ones
    below_min_attempts."""
    pct = pct or {}
    out = {}
    for s in _specs(group):
        if s.column in missing:
            out[s.column] = D.MetricInput(0.1, None, None, "below_min_attempts")
        else:
            out[s.column] = D.MetricInput(3.0, pct.get(s.column, 50.0), 60, "ok", spread)
    return out


# ── spread and z ────────────────────────────────────────────────────────────────

class TestSpread:
    def test_mad_scale(self):
        # median 3; |dev| = 2, 1, 0, 1, 97 → MAD 1
        s = D.spread(pl.Series([1.0, 2.0, 3.0, 4.0, 100.0]))
        assert (s.median, s.scale, s.method) == (3.0, 1.4826, "mad")

    def test_even_population_median_averages_the_middle_pair(self):
        assert D.spread(pl.Series([1.0, 2.0, 3.0, 10.0])).median == 2.5

    def test_iqr_fallback_when_mad_is_zero(self):
        # median 0, MAD 0; type-7 quartiles: Q1 at h = 1.5 → 0, Q3 at h = 4.5 → 1.5
        s = D.spread(pl.Series([0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 5.0]))
        assert s.method == "iqr"
        assert s.scale == pytest.approx(1.5 / 1.349, abs=1e-15)

    def test_zero_spread_when_mad_and_iqr_are_zero(self):
        assert D.spread(pl.Series([3.0, 3.0, 3.0, 3.0, 9.0])) == D.Spread(3.0, None, None)

    def test_nulls_dropped_and_empty_raises(self):
        assert D.spread(pl.Series([None, 1.0, 2.0, 3.0])).median == 2.0
        with pytest.raises(ValueError):
            D.spread(pl.Series([None], dtype=pl.Float64))

    def test_z_is_robust_and_uncapped(self):
        inp = _inputs("ST")
        inp["np_shots_p90"] = D.MetricInput(41.0, 99.0, 60, "ok", D.Spread(1.0, 2.0, "mad"))
        c = _row_dna("ST", _specs("ST"), inp).cells["np_shots_p90"]
        assert c.z == 20.0 and c.z_status == "ok"          # (41 − 1) / 2, no cap
        assert (c.z_median, c.z_scale, c.z_scale_method) == (1.0, 2.0, "mad")

    def test_zero_spread_z_is_null_with_reason(self):
        inp = _inputs("ST")
        inp["blocks_p90"] = D.MetricInput(0.0, 50.0, 60, "ok", D.Spread(0.0, None, None))
        c = _row_dna("ST", _specs("ST"), inp).cells["blocks_p90"]
        assert c.z is None and c.z_status == "zero_spread" and c.weight is not None

    def test_ok_cell_without_spread_raises(self):
        inp = _inputs("ST")
        inp["blocks_p90"] = D.MetricInput(1.0, 50.0, 60, "ok", None)
        with pytest.raises(ValueError):
            _row_dna("ST", _specs("ST"), inp)

    def test_inputs_must_be_exactly_the_dna_metrics(self):
        inp = _inputs("ST")
        del inp["blocks_p90"]
        with pytest.raises(ValueError):
            _row_dna("ST", _specs("ST"), inp)


# ── weights and dna_status ──────────────────────────────────────────────────────

@pytest.mark.parametrize("group", GROUPS)
def test_complete_row_weights_sum_to_one_with_equal_family_totals(group):
    row = _row_dna(group, _specs(group), _inputs(group))
    assert row.dna_status == "complete"
    assert math.isclose(sum(c.weight for c in row.cells.values()), 1.0, abs_tol=1e-12)
    fams = {c.family for c in row.cells.values()}
    for f in fams:
        total = sum(c.weight for c in row.cells.values() if c.family == f)
        assert total == pytest.approx(1 / len(fams), abs=1e-15)


def test_partial_row_re_splits_the_family_weight():
    """AM-Winger without long_pass_completion_pct: long_passes_attempted carries all of
    Passing (1/7), not half of it (ADR 004 §12.2, §12.6)."""
    row = _row_dna("AM-Winger", _specs("AM-Winger"),
                    _inputs("AM-Winger", missing=("long_pass_completion_pct",)))
    assert row.dna_status == "partial"
    assert row.cells["long_pass_completion_pct"].weight is None
    assert row.cells["long_pass_completion_pct"].z_status == "below_min_attempts"
    assert row.cells["long_passes_attempted_p90"].weight == pytest.approx(1 / 7, abs=1e-15)
    assert math.isclose(sum(c.weight for c in row.cells.values() if c.weight), 1.0, abs_tol=1e-12)
    # AM-Winger Passing is withheld (§12.9); without the withhold it has no directional member.
    assert row.dimensions["Passing"] == D.Dimension(None, "pending_xpass_deferred", ())
    unheld = _row_dna("AM-Winger", _specs("AM-Winger"),
                      _inputs("AM-Winger", missing=("long_pass_completion_pct",)), withheld={})
    assert unheld.dimensions["Passing"] == D.Dimension(None, "no_directional_metric", ())


def test_two_missing_metrics_make_the_row_dna_incomplete_but_keep_its_data():
    """Synthetic (2015/16 has no such row): the row keeps its vector, weights and dimension
    scores; only the distance refuses it."""
    group = "FB-WB"
    row = _row_dna(group, _specs(group),
                    _inputs(group, missing=("long_pass_completion_pct", "progressive_passes_p90")))
    assert row.dna_status == "dna_incomplete"
    assert len(row.cells) == len(_specs(group))
    assert math.isclose(sum(c.weight for c in row.cells.values() if c.weight), 1.0, abs_tol=1e-12)
    assert row.dimensions["Goal threat"].members == ("np_shots_p90",)
    other = _row_dna(group, _specs(group), _inputs(group))
    with pytest.raises(D.DnaError, match="dna_incomplete"):
        D.pair_distance(row, other)
    with pytest.raises(D.DnaError, match="dna_incomplete"):
        D.pair_distance(other, row)


def test_one_missing_metric_that_empties_a_family_is_dna_incomplete():
    """AM-Winger Creation holds op_key_passes only: missing it empties the family."""
    row = _row_dna("AM-Winger", _specs("AM-Winger"),
                    _inputs("AM-Winger", missing=("op_key_passes_p90",)))
    assert row.dna_status == "dna_incomplete"
    assert "Creation" not in {c.family for c in row.cells.values() if c.weight}


# ── dimension scores ────────────────────────────────────────────────────────────

# ADR 004 §13.8 (M4g4d, 2015/16): the D5 Defending status per group.
D5_2015_16 = {"CB": "possession_criteria_failed", "FB-WB": "possession_criteria_failed",
              "DM-CM": "mover_stability_inconclusive", "AM-Winger": "possession_criteria_failed",
              "ST": "possession_criteria_failed"}


def test_defending_is_withheld_with_the_registry_d5_status_in_every_group():
    assert "Defending" not in config.DNA_WITHHELD_DIMENSIONS
    for g in GROUPS:
        assert REGISTRY.defending_status(g).status == D5_2015_16[g]
        d = _row_dna(g, _specs(g), _inputs(g)).dimensions["Defending"]
        assert d == D.Dimension(None, D5_2015_16[g], ())


def test_every_d5_status_is_a_withheld_status_and_ok_is_not():
    assert set(R.D5_STATUSES) <= D.WITHHELD_STATUSES
    assert len(set(R.D5_STATUSES)) == 6 and R.SCORE_OK not in D.WITHHELD_STATUSES


def test_a_restored_defending_is_not_withheld():
    """A registry whose Defending status is "ok" withholds only the table's families."""
    fs = {("Defending", g): R.FamilyStatus("Defending", g, R.SCORE_OK, "ball_recoveries_p90",
                                           "beta_0") for g in GROUPS}
    restored = replace(REGISTRY, family_status=fs)
    assert D.withheld_for("ST", derivation=restored) == {}
    assert D.withheld_for("CB", derivation=restored) == {"Passing": "pending_xpass_deferred"}


def test_the_table_cannot_carry_defending():
    with pytest.raises(ValueError, match="comes from the registry"):
        D.withheld_for("CB", table={"Defending": ("pending_xpass_deferred", GROUPS)},
                       derivation=REGISTRY)


def test_a_custom_table_without_its_derivation_is_refused():
    with pytest.raises(ValueError, match="explicit derivation"):
        D.withheld_for("CB", table={"Passing": ("pending_xpass_deferred", GROUPS)})


def test_passing_withheld_exactly_where_long_pass_completion_is_its_only_directional_member():
    """ADR 004 §12.9 (user, 2026-10-07): the configured groups are the groups whose only
    directional Passing DNA member is long_pass_completion_pct; ST keeps its score."""
    status, groups = config.DNA_WITHHELD_DIMENSIONS["Passing"]
    assert status == D.PENDING_XPASS_DEFERRED
    rule = {g for g in GROUPS if {m for m in REGISTRY.dna_metrics(g)
                                  if _BY_COL[m].family == "Passing"
                                  and _BY_COL[m].direction != "style"}
            == {"long_pass_completion_pct"}}
    assert set(groups) == rule == {"CB", "FB-WB", "DM-CM", "AM-Winger"}
    for g in GROUPS:
        d = _row_dna(g, _specs(g), _inputs(g)).dimensions["Passing"]
        if g in groups:
            assert d == D.Dimension(None, "pending_xpass_deferred", ())
        else:
            assert d.score_status == "ok" and d.members == ("pass_completion_pct",)


def test_withheld_precedes_no_directional_metric():
    """A withheld family keeps its withheld status even when its directional member is
    missing (2015/16: the 30 rows below the long-pass minimum)."""
    d = _row_dna("AM-Winger", _specs("AM-Winger"), _inputs(
        "AM-Winger", missing={"long_pass_completion_pct"})).dimensions["Passing"]
    assert d.score_status == "pending_xpass_deferred"


def test_withheld_for_and_unknown_status():
    assert D.withheld_for("ST") == {"Defending": "possession_criteria_failed"}
    assert D.withheld_for("CB") == {"Defending": "possession_criteria_failed",
                                    "Passing": "pending_xpass_deferred"}
    assert D.withheld_for("DM-CM")["Defending"] == "mover_stability_inconclusive"
    with pytest.raises(ValueError, match="unknown withheld status"):
        _row_dna("ST", _specs("ST"), _inputs("ST"), withheld={"Aerial": "made_up"})


def test_score_is_mean_of_directional_members_with_lower_better_inverted():
    """A lower_better member enters as 100 − p. Synthetic since M4g2: no DNA metric is
    lower_better (ADR 004 §13.2 made dribbled_past style), so dribbled_past is set to
    lower_better here; CB Defending = mean(ball_recoveries, 100 − dribbled_past)."""
    pct = {"ball_recoveries_p90": 80.0, "dribbled_past_p90": 30.0, "clearances_p90": 1.0,
           "pressures_p90": 1.0, "tackles_attempted_p90": 1.0}
    specs = [replace(s, direction="lower_better") if s.column == "dribbled_past_p90" else s
             for s in _specs("CB")]
    d = _row_dna("CB", specs, _inputs("CB", pct), withheld={}).dimensions["Defending"]
    assert d.score == pytest.approx((80.0 + 70.0) / 2, abs=1e-12)
    assert d.members == ("ball_recoveries_p90", "dribbled_past_p90")
    assert d.score_status == "ok"


def test_style_members_do_not_enter_the_score():
    """With the registry's specs, CB Defending = ball_recoveries alone: dribbled_past (style
    since ADR 004 §13.2), clearances, pressures and tackles_attempted do not enter."""
    pct = {"ball_recoveries_p90": 80.0, "dribbled_past_p90": 30.0, "clearances_p90": 1.0,
           "pressures_p90": 1.0, "tackles_attempted_p90": 1.0}
    d = _row_dna("CB", _specs("CB"), _inputs("CB", pct), withheld={}).dimensions["Defending"]
    assert d.score == pytest.approx(80.0, abs=1e-12)
    assert d.members == ("ball_recoveries_p90",)


def test_withheld_is_a_required_keyword():
    with pytest.raises(TypeError):
        D.row_dna("ST", _specs("ST"), _inputs("ST"))  # type: ignore[call-arg]


def test_an_unknown_direction_raises_instead_of_being_inverted():
    specs = [replace(s, direction="sideways") if s.column == "aerials_won_p90" else s  # type: ignore[arg-type]
             for s in _specs("ST")]
    with pytest.raises(ValueError, match="unknown direction"):
        _row_dna("ST", specs, _inputs("ST"))


def test_members_are_named_in_metric_name_order():
    d = _row_dna("DM-CM", _specs("DM-CM"), _inputs("DM-CM")).dimensions["Carrying"]
    assert d.members == ("box_entries_carry_p90", "final_third_entries_carry_p90",
                         "progressive_carries_p90")


def test_scores_exist_only_for_the_groups_dna_families():
    fams = set(_row_dna("CB", _specs("CB"), _inputs("CB")).dimensions)
    assert fams == {"Aerial", "Carrying", "Defending", "Passing", "Progression"}


# ── pair distance ───────────────────────────────────────────────────────────────

def test_pair_weights_use_the_metrics_both_rows_have_and_are_symmetric():
    g = "AM-Winger"
    a = _row_dna(g, _specs(g), _inputs(g, {"long_passes_attempted_p90": 10.0},
                                        missing=("long_pass_completion_pct",)))
    b = _row_dna(g, _specs(g), _inputs(g, {"long_passes_attempted_p90": 90.0,
                                            "long_pass_completion_pct": 70.0}))
    wab, wba = D.pair_weights(a, b), D.pair_weights(b, a)
    assert wab == wba
    assert "long_pass_completion_pct" not in wab
    assert wab["long_passes_attempted_p90"] == pytest.approx(1 / 7, abs=1e-15)
    assert math.isclose(sum(wab.values()), 1.0, abs_tol=1e-12)
    # only long passes differ: d = 1/7 · 0.8²
    assert D.pair_distance(a, b) == D.pair_distance(b, a)
    assert D.pair_distance(a, b) == pytest.approx(0.64 / 7, abs=1e-15)


def test_distance_to_self_is_zero_and_across_groups_raises():
    a = _row_dna("ST", _specs("ST"), _inputs("ST"))
    assert D.pair_distance(a, a) == 0.0
    with pytest.raises(D.DnaError, match="groups"):
        D.pair_distance(a, _row_dna("CB", _specs("CB"), _inputs("CB")))


# ── build input guard ───────────────────────────────────────────────────────────

def _pct(**over) -> pl.DataFrame:
    base = {"registry_version": REGISTRY.version, "measurement_source": REGISTRY.source,
            "season_parquet_sha256": "abc"}
    return pl.DataFrame([{**base, **over}] * 2)


def test_check_percentiles_accepts_matching_provenance():
    check_percentiles(_pct(), REGISTRY, season_sha256="abc")


@pytest.mark.parametrize("over", [{"registry_version": "stale"}, {"measurement_source": "other"},
                                  {"season_parquet_sha256": "def"}])
def test_check_percentiles_refuses_stale_or_foreign_percentiles(over):
    with pytest.raises(DnaInputError):
        check_percentiles(_pct(**over), REGISTRY, season_sha256="abc")


def test_check_percentiles_refuses_a_global_basis():
    with pytest.raises(DnaInputError, match="local"):
        check_percentiles(_pct(), REGISTRY, season_sha256="abc", basis="global")


# ── the spike's written-out copies ──────────────────────────────────────────────

def _spike_module():
    sys.path.insert(0, str(_SPIKE.parent))
    try:
        spec = importlib.util.spec_from_file_location("m4f_dna_reference", _SPIKE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_SPIKE.parent))


def test_spike_dna_lists_families_and_directions_equal_the_registry():
    spike = _spike_module()
    for g in GROUPS:
        assert spike.DNA[g] == REGISTRY.dna_metrics(g), g
        for m in REGISTRY.dna_metrics(g):
            assert spike.SPEC[m] == (_BY_COL[m].family, _BY_COL[m].direction), m
    assert set(spike.SPEC) == {m for g in GROUPS for m in REGISTRY.dna_metrics(g)}


@pytest.mark.parametrize("name, want", [
    ("MAD_SCALE", D.MAD_SCALE), ("IQR_SCALE", D.IQR_SCALE),
    ("DNA_PERCENTILE_BASIS", config.DNA_PERCENTILE_BASIS),
])
def test_spike_constants_equal_the_package(name, want):
    tree = ast.parse(_SPIKE.read_text())
    values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert values == [want]


def test_spike_withheld_equals_the_package():
    """The reference reads Defending's D5 statuses from spikes/expected_m4g4_flips.json and
    writes Passing out: both equal the package's (registry D5 status, config table)."""
    spike = _spike_module()
    status, groups = config.DNA_WITHHELD_DIMENSIONS["Passing"]
    assert spike.WITHHELD["Passing"] == {g: status for g in groups}
    assert spike.WITHHELD["Defending"] == {
        g: REGISTRY.defending_status(g).status for g in GROUPS
        if REGISTRY.defending_status(g).status != R.SCORE_OK}


def test_old_pending_mover_stability_status_is_no_longer_a_withheld_status():
    """ADR 004 §14 item 4 rename: the old name must not linger as an accepted status."""
    assert "pending_mover_stability" not in D.WITHHELD_STATUSES
    assert D.PENDING_XPASS_DEFERRED in D.WITHHELD_STATUSES
    with pytest.raises(ValueError, match="unknown withheld status"):
        _row_dna("CB", _specs("CB"), _inputs("CB"), withheld={"Passing": "pending_mover_stability"})
