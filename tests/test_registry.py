"""
Tests for the metric registry (src/eleveniq/analytics/registry.py; plan 005 M4c3,
ADR 004 §10.3).

- Every registry column, ratio term and count column is in the recorded schema.
- Every DNA and signature cell is re-checked by rule predicates written here from
  ADR 004 §10.3, independently of derive(), or carries a documented override.
- derive() on the committed JSON reproduces REGISTRY.
- Rule-breaking entries raise RegistryRuleError: each fixture changes one entry or one
  measurement of an otherwise valid input.
- The inclusive and strict comparisons of §10.3 hold at their exact thresholds.

The ADR 004 §10.6 / §10.10 lists themselves are in tests/test_registry_spec.py.
"""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pytest

from eleveniq import config
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import (
    AllowEntry,
    CollinearChoice,
    Override,
    RegistryRuleError,
    Rules,
    derive,
    load_measurements,
)

_ROOT = Path(__file__).resolve().parents[1]
_SEASON_PARQUET = _ROOT / "data" / "processed" / "player_season_stats.parquet"
PROXIES = ("poss_dur_loo", "poss_pass_loo")


@pytest.fixture(scope="module")
def committed() -> dict:
    return load_measurements()


@pytest.fixture
def meas(committed) -> dict:
    """A private copy to tamper with."""
    return copy.deepcopy(committed)


def _spec(column: str) -> R.MetricSpec:
    return next(s for s in R.SPECS if s.column == column)


def _replace_spec(column: str, **changes) -> tuple[R.MetricSpec, ...]:
    return tuple(dataclasses.replace(s, **changes) if s.column == column else s for s in R.SPECS)


# ── Schema ──────────────────────────────────────────────────────────────────────

class TestSchema:
    def test_every_column_and_term_is_in_the_recorded_schema(self, committed):
        schema = set(committed["provenance"]["season_columns"])
        for s in R.SPECS:
            used = {s.column, *([s.count_column] if s.count_column else []),
                    *(s.ratio.terms if s.ratio else ())}
            assert used <= schema, (s.column, used - schema)

    def test_every_measured_metric_and_ratio_is_registered(self, committed):
        registered = {s.column for s in R.SPECS}
        assert set(committed["metrics"]) <= registered
        assert set(committed["ratios"]) <= registered

    def test_no_spec_applies_to_gk(self):
        for s in R.SPECS:
            assert set(s.applies_to) <= {"CB", "FB-WB", "DM-CM", "AM-Winger", "ST"}, s.column

    def test_every_cell_is_an_outfield_group(self):
        assert {g for _, g in R.REGISTRY.cells} == set(R.GROUPS)
        assert len(R.REGISTRY.cells) == sum(len(s.applies_to) for s in R.SPECS)


# ── Independent re-check of every DNA and signature cell (ADR 004 §10.3) ────────

def _possession_ok(block: dict, column: str, group: str, committed: dict) -> tuple[bool, bool]:
    """(admitted, team_style_biased) from the §10.3 step-5 definitions, written out here."""
    pool = [str(c) for c in committed["provenance"]["pool_competition_ids"]]
    means = {}
    for proxy in PROXIES:
        per = [block[proxy]["by_league"].get(c) for c in pool]
        assert None not in per and block[proxy]["rho_within_mean"] is not None, "untestable cell in DNA"
        means[proxy] = block[proxy]["rho_within_mean"]
    triggered = False
    for proxy in PROXIES:
        per = [block[proxy]["by_league"][c] for c in pool]
        signed = [v for v in per if abs(v) >= 0.05]
        same_sign = bool(signed) and (all(v > 0 for v in signed) or all(v < 0 for v in signed))
        triggered |= abs(means[proxy]) >= 0.4 and same_sign
    if not triggered:
        return True, False
    allowed = {(a.column, a.group) for a in R.POSSESSION_ALLOW_LIST}
    return (column, group) in allowed and max(abs(v) for v in means.values()) <= 0.55, True


class TestIndependentPredicates:
    """Thresholds are written as literals from ADR 004 §10.3, not read from config."""

    def _override(self, column, group):
        return next((o for o in R.OVERRIDES if (o.column, o.group) == (column, group)), None)

    def test_every_dna_cell_meets_the_rules_or_is_overridden(self, committed):
        dropped = {(c.dropped, g) for c in R.COLLINEAR_CHOICES for g in c.groups}
        checked = 0
        for (col, grp), cell in R.REGISTRY.cells.items():
            if cell.status != "dna":
                continue
            o = self._override(col, grp)
            if o is not None:
                assert o.reason.strip() and o.adr.strip()
                continue
            spec = _spec(col)
            assert spec.fixed_display is None and (col, grp) not in dropped, (col, grp)
            assert col not in {"goals_p90", "assists_p90", "op_assists_p90", "restart_assists_p90"}
            assert not col.startswith("restart_")
            if spec.unit == "per90":
                m = committed["metrics"][col]["groups"][grp]
                assert m["zero_share"] <= 0.30 and m["r_full"] >= 0.6, (col, grp)
                admitted, tsb = _possession_ok(m["possession"], col, grp, committed)
                r = m["r_full"]
            elif spec.unit == "ratio":
                m = committed["ratios"][col]["groups"][grp]
                assert not m["no_signal"] and m["split_half_r_full"] >= 0.6 and m["coverage"] >= 0.90
                admitted, tsb = _possession_ok(m["possession"], col, grp, committed)
                r = m["split_half_r_full"]
            else:
                assert col == "team_pass_share" and grp in ("DM-CM", "CB")
                m = committed["team_pass_share"][grp]
                assert all(abs(m["possession"][p]["rho_within_mean"]) < 0.3 for p in PROXIES)
                assert m["split_half_r_full"] >= 0.6
                admitted, tsb, r = True, False, m["split_half_r_full"]
            assert admitted, (col, grp)
            assert cell.team_style_biased == tsb, (col, grp)
            assert cell.low_reliability == (r < 0.7), (col, grp)
            checked += 1
        assert checked == sum(len(R.dna_metrics(g)) for g in R.GROUPS) - sum(
            1 for o in R.OVERRIDES if o.status == "dna")

    def test_every_signature_cell_is_zero_heavy_and_reliable_or_overridden(self, committed):
        for (col, grp), cell in R.REGISTRY.cells.items():
            if cell.status != "signature":
                continue
            spec = _spec(col)
            m = committed["metrics"][col]["groups"][grp]
            assert spec.integer_count, (col, grp)
            assert cell.signature_min_count == max(5, m["count_p90"])
            if self._override(col, grp) is None:
                assert m["zero_share"] > 0.30 and m["r_full"] >= 0.6, (col, grp)

    def test_every_dna_cell_has_a_measured_d9(self):
        # M4c3: a DNA cell with global_ok None would force a local basis into a DNA vector.
        assert [k for k, c in R.REGISTRY.cells.items()
                if c.status == "dna" and c.global_ok is None] == []
        assert R.cell("team_pass_share", "DM-CM").basis == "global"

    @pytest.mark.parametrize("group", ["FB-WB", "AM-Winger", "ST"])
    def test_team_pass_share_outside_d12b_groups_is_not_preregistered(self, group):
        # D12b is pre-registered for DM-CM and CB only (§10.3 step 6); the other three
        # groups have no team_pass_share measurement at all, not merely a failing one.
        cell = R.cell("team_pass_share", group)
        assert cell.status == "display"
        assert cell.reasons == ("d12b_not_preregistered",)

    def test_global_ok_and_basis_follow_the_d9_band(self, committed):
        pool = [str(c) for c in committed["provenance"]["pool_competition_ids"]]
        cells = [(col, grp, m) for section in ("metrics", "ratios")
                 for col, spec in committed[section].items() for grp, m in spec["groups"].items()]
        cells += [("team_pass_share", grp, m) for grp, m in committed["team_pass_share"].items()]
        for col, grp, m in cells:
            cell = R.cell(col, grp)
            means = m.get("league_mean_global_pct", {})
            # ADR 004 §11.1: a ratio is ranked against its own min_attempts populations, every
            # other cell against the gated group populations.
            sizes = m["ref_n_local"] if col in committed["ratios"] else \
                committed["cohorts"][grp]["ref_n_local"]
            if any(c not in means for c in pool) or \
                    any(sizes.get(c, 0) < config.MIN_COHORT_SIZE for c in pool):
                # Not measured, a pool league without rows (ST dribble_success_pct has no
                # Ligue 1 row), or a league population below MIN_COHORT_SIZE (§11.1: an
                # untestable check is not a pass): D9 is undefined, so the basis is local.
                assert cell.global_ok is None and cell.basis == "local"
                continue
            ok = all(40 <= means[c] <= 60 for c in pool)
            assert cell.global_ok is ok and cell.basis == ("global" if ok else "local")


# ── derive() reproduces REGISTRY ────────────────────────────────────────────────

class TestDeterminism:
    def test_derive_on_committed_json_equals_registry(self, meas):
        again = derive(measurements=meas)
        assert dict(again.cells) == dict(R.REGISTRY.cells)
        assert again.boundary == R.REGISTRY.boundary
        assert again.version == R.REGISTRY_VERSION

    def test_version_is_a_sha256_and_tracks_the_rules(self, meas):
        assert len(R.REGISTRY_VERSION) == 64 and int(R.REGISTRY_VERSION, 16) >= 0
        assert derive(measurements=meas, rules=Rules(boundary_margin=0.03)).version != R.REGISTRY_VERSION

    def test_version_tracks_the_measurement_provenance(self, meas):
        meas["provenance"]["season_parquet_sha256"] = "0" * 64
        assert derive(measurements=meas).version != R.REGISTRY_VERSION

    def test_measurement_source(self):
        assert R.MEASUREMENT_SOURCE == config.SOURCE_STATSBOMB

    def test_rule_defaults_come_from_config(self):
        r = Rules()
        assert (r.dna_reliability_min, r.low_reliability_below, r.zero_heavy_share,
                r.ratio_min_coverage, r.possession_rho_trigger, r.possession_sign_neutral,
                r.possession_allow_max_rho, r.team_pass_share_max_rho, r.collinear_rho,
                r.global_ok_band, r.signature_min_count_floor, r.boundary_margin,
                r.d9_boundary_margin_pts) == (
            0.6, 0.7, 0.30, 0.90, 0.4, 0.05, 0.55, 0.3, 0.85, (40.0, 60.0), 5, 0.02, 3.0)

    def test_lookup_of_gk_or_unknown_cell_raises(self):
        with pytest.raises(KeyError):
            R.dna_metrics("GK")
        with pytest.raises(KeyError):
            R.cell("goals_p90", "GK")


# ── Rule-breaking fixtures (plan M4c3): each raises RegistryRuleError ───────────

class _FakeFiles:
    def __init__(self, text: str) -> None:
        self.text = text

    def joinpath(self, _name: str) -> "_FakeFiles":
        return self

    def open(self, encoding: str = "utf-8"):
        import io
        return io.StringIO(self.text)


_CB_OP_XGA = "op_xg_assisted_p90"   # CB r_full 0.185: display by step 4


class TestRuleBreakingEntries:
    def test_override_without_adr_reference(self, meas):
        bad = (*R.OVERRIDES, Override(_CB_OP_XGA, "CB", "dna", "a strong CB creator", ""))
        with pytest.raises(RegistryRuleError, match="no ADR reference"):
            derive(measurements=meas, overrides=bad)

    def test_override_with_adr_but_no_reason(self, meas):
        bad = (*R.OVERRIDES, Override(_CB_OP_XGA, "CB", "dna", "  ", "ADR 004 §10"))
        with pytest.raises(RegistryRuleError, match="no reason"):
            derive(measurements=meas, overrides=bad)

    def test_no_op_override(self, meas):
        # CB tackles_attempted is DNA by the rules already.
        bad = (*R.OVERRIDES, Override("tackles_attempted_p90", "CB", "dna", "keep it", "ADR 004 §10"))
        with pytest.raises(RegistryRuleError, match="no-op"):
            derive(measurements=meas, overrides=bad)

    @pytest.mark.parametrize("column", ["goals_p90", "assists_p90", "op_assists_p90",
                                        "restart_assists_p90"])
    def test_goals_or_assists_into_dna(self, meas, column):
        bad = (*R.OVERRIDES, Override(column, "ST", "dna", "finishing is the ST's job", "ADR 004"))
        with pytest.raises(RegistryRuleError, match="never DNA"):
            derive(measurements=meas, overrides=bad)

    def test_allow_list_bound_below_the_measured_rho(self, meas):
        # CB progressive_carries: measured max |mean ρ| 0.548655 against a declared bound 0.50.
        bad = tuple(AllowEntry(a.column, a.group, 0.50, a.adr)
                    if (a.column, a.group) == ("progressive_carries_p90", "CB") else a
                    for a in R.POSSESSION_ALLOW_LIST)
        with pytest.raises(RegistryRuleError, match=r"0\.548655 > its bound 0\.5"):
            derive(measurements=meas, allow=bad)

    def test_gk_in_applies_to(self, meas):
        bad = _replace_spec("tackles_attempted_p90", applies_to=(*R.GROUPS, "GK"))
        with pytest.raises(RegistryRuleError, match="never GK"):
            derive(specs=bad, measurements=meas)

    def test_allow_list_bound_looser_than_d12(self, meas):
        bad = (*R.POSSESSION_ALLOW_LIST, AllowEntry("np_xg_p90", "AM-Winger", 0.6, "ADR 004"))
        with pytest.raises(RegistryRuleError, match="above the D12 maximum"):
            derive(measurements=meas, allow=bad)

    def test_allow_list_entry_without_adr(self, meas):
        bad = (*R.POSSESSION_ALLOW_LIST, AllowEntry("np_xg_p90", "AM-Winger", 0.55, ""))
        with pytest.raises(RegistryRuleError, match="no ADR reference"):
            derive(measurements=meas, allow=bad)

    def test_column_missing_from_schema(self, meas):
        bad = (*R.SPECS, R.MetricSpec("cards_p90", "Cards", "Defending", "lower_better", "per90",
                                      count_column="cards", integer_count=True))
        with pytest.raises(RegistryRuleError, match="not in the recorded season schema"):
            derive(specs=bad, measurements=meas)

    def test_ratio_term_missing_from_schema(self, meas):
        bad = _replace_spec("aerial_win_pct", ratio=R.RatioSpec("aerials_won", ("aerial_duels",)))
        with pytest.raises(RegistryRuleError, match="not in the recorded season schema"):
            derive(specs=bad, measurements=meas)

    def test_measured_metric_without_a_spec(self, meas):
        bad = tuple(s for s in R.SPECS if s.column != "blocks_p90")
        with pytest.raises(RegistryRuleError, match="blocks_p90 has no per90 registry spec"):
            derive(specs=bad, measurements=meas)

    def test_restart_column_into_dna_without_its_override(self, meas):
        # §8: without the U2 override AM-Winger restart_key_passes would be DNA by the rules.
        with pytest.raises(RegistryRuleError, match="restart columns never are"):
            derive(measurements=meas, overrides=())

    def test_xg_count_cell_cannot_become_a_signature_trait(self, meas):
        # restart_xg_assisted is zero-heavy with r >= 0.6 in four groups; only its fixed
        # display keeps it out. Without it, it would be an xG-sum signature trait (§10.2).
        bad = _replace_spec("restart_xg_assisted_p90", fixed_display=None)
        with pytest.raises(RegistryRuleError, match="integer count"):
            derive(specs=bad, measurements=meas)

    def test_d17_kept_metric_must_be_dna(self, meas):
        # CB dribbles_attempted r_full 0.698 → force it below 0.6: the drop would remove both.
        meas["metrics"]["dribbles_attempted_p90"]["groups"]["CB"]["r_full"] = 0.5
        with pytest.raises(RegistryRuleError, match="the drop would remove both"):
            derive(measurements=meas)

    def test_collinear_pair_both_dna_without_a_choice(self, meas):
        bad = tuple(c for c in R.COLLINEAR_CHOICES if c.dropped != "tackles_won_p90")
        with pytest.raises(RegistryRuleError, match="both DNA without a D17 choice"):
            derive(measurements=meas, collinear=bad)

    def test_d17_choice_without_a_measured_pair(self, meas):
        bad = (*R.COLLINEAR_CHOICES,
               CollinearChoice("np_shots_p90", "np_xg_p90", ("FB-WB",), "ADR 004 D17"))
        with pytest.raises(RegistryRuleError, match="no measured pair"):
            derive(measurements=meas, collinear=bad)

    def test_team_pass_share_measurements_are_required(self, meas):
        meas["team_pass_share"] = None
        with pytest.raises(RegistryRuleError, match="team_pass_share"):
            derive(measurements=meas)

    def test_team_pass_share_needs_both_preregistered_groups(self, meas):
        del meas["team_pass_share"]["CB"]
        with pytest.raises(RegistryRuleError, match="team_pass_share"):
            derive(measurements=meas)

    def test_measurement_missing_for_a_derived_cell(self, meas):
        del meas["metrics"]["blocks_p90"]["groups"]["ST"]
        with pytest.raises(RegistryRuleError, match=r"blocks_p90: no measurements for \['ST'\]"):
            derive(measurements=meas)

    def test_count_column_mismatch(self, meas):
        meas["metrics"]["blocks_p90"]["count_column"] = "shots"
        with pytest.raises(RegistryRuleError, match="count column"):
            derive(measurements=meas)

    def test_fixed_display_without_adr(self, meas):
        bad = _replace_spec("switches_p90", fixed_display=R.FixedDisplay("D18", " "))
        with pytest.raises(RegistryRuleError, match="fixed display needs"):
            derive(specs=bad, measurements=meas)

    def test_source_must_be_named(self, meas):
        meas["provenance"]["source"] = ""
        with pytest.raises(RegistryRuleError, match="one source"):
            derive(measurements=meas)

    def test_nan_in_the_measurement_file(self, monkeypatch):
        # NaN would make every threshold comparison silently False.
        monkeypatch.setattr(R.resources, "files", lambda _pkg: _FakeFiles('{"r_full": NaN}'))
        with pytest.raises(RegistryRuleError, match="NaN"):
            load_measurements()


# ── Override mechanics: the final restart/goals-and-assists guard, and state reset ──

class TestOverrideMechanics:
    def test_override_cannot_smuggle_a_non_goals_restart_column_into_dna(self, meas):
        # restart_xg_assisted_p90 is not in GOALS_AND_ASSISTS, so the entry-time check
        # in _validate_entries (goals/assists only) does not catch this; only the
        # final per-cell guard (ADR 004 §8, "restart columns are never DNA") does.
        bad = (*R.OVERRIDES, Override("restart_xg_assisted_p90", "CB", "dna",
                                       "a strong set-piece creator", "ADR 004 §8"))
        with pytest.raises(RegistryRuleError, match="restart columns never are"):
            derive(measurements=meas, overrides=bad)

    def test_override_away_from_dna_resets_low_reliability(self, meas):
        # CB aerials_lost_p90 is DNA with low_reliability True by rule (r_full 0.612).
        before = R.cell("aerials_lost_p90", "CB")
        assert before.status == "dna" and before.low_reliability is True
        bad = (*R.OVERRIDES, Override("aerials_lost_p90", "CB", "display",
                                       "test: override away from dna", "ADR 004 §10"))
        after = derive(measurements=meas, overrides=bad).cell("aerials_lost_p90", "CB")
        assert after.status == "display" and after.low_reliability is False


# ── Thresholds: inclusive and strict comparisons (ADR 004 §10.3 exact definitions) ──

def _cell(meas, column, group, **kw):
    return derive(measurements=meas, **kw).cell(column, group)


class TestThresholdEdges:
    # CB aerials_lost: zero share 0, r_full 0.612, no trigger → DNA with a low-reliability note.
    COL, GRP = "aerials_lost_p90", "CB"

    def _m(self, meas):
        return meas["metrics"][self.COL]["groups"][self.GRP]

    def test_r_exactly_at_the_dna_bar_is_dna(self, meas):
        self._m(meas)["r_full"] = 0.6
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "dna" and c.low_reliability

    def test_r_just_below_the_dna_bar_is_display(self, meas):
        self._m(meas)["r_full"] = 0.6 - 1e-12
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "display" and c.reasons == ("unreliable",)

    def test_r_exactly_at_the_lower_reliability_bar_has_no_note(self, meas):
        self._m(meas)["r_full"] = 0.7
        assert not _cell(meas, self.COL, self.GRP).low_reliability

    def test_zero_share_exactly_at_the_bar_is_not_zero_heavy(self, meas):
        self._m(meas)["zero_share"] = 0.30
        assert _cell(meas, self.COL, self.GRP).status == "dna"

    def test_zero_share_above_the_bar_is_signature_when_reliable(self, meas):
        self._m(meas)["zero_share"] = 0.30 + 1e-12
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "signature" and c.signature_min_count == max(5, self._m(meas)["count_p90"])

    def test_zero_heavy_and_unreliable_is_display(self, meas):
        self._m(meas)["zero_share"] = 0.5
        self._m(meas)["r_full"] = 0.59
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "display" and c.reasons == ("zero_heavy", "unreliable")

    def _set_rho(self, meas, proxy, values, mean=None):
        p = self._m(meas)["possession"][proxy]
        p["by_league"] = dict(zip(("11", "2", "12", "7"), values))
        p["rho_within_mean"] = sum(values) / 4 if mean is None else mean

    def test_rho_exactly_at_the_trigger_triggers(self, meas):
        self._set_rho(meas, "poss_dur_loo", [0.4, 0.4, 0.4, 0.4], mean=0.4)
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "display" and c.team_style_biased and c.reasons == ("team_style_biased",)

    def test_negative_rho_triggers_on_its_magnitude(self, meas):
        self._set_rho(meas, "poss_pass_loo", [-0.5, -0.45, -0.4, -0.3])
        assert _cell(meas, self.COL, self.GRP).status == "display"

    def test_sign_test_fails_on_opposite_signs(self, meas):
        # Mean 0.45 but one league clearly negative: no trigger.
        self._set_rho(meas, "poss_dur_loo", [0.9, 0.8, 0.2, -0.1])
        assert _cell(meas, self.COL, self.GRP).status == "dna"

    def test_sign_test_ignores_near_zero_leagues(self, meas):
        # |−0.04| < 0.05 has no sign, so the other three agree: trigger.
        self._set_rho(meas, "poss_dur_loo", [0.8, 0.5, 0.44, -0.04])
        assert _cell(meas, self.COL, self.GRP).status == "display"

    def test_untestable_possession_is_display(self, meas):
        p = self._m(meas)["possession"]["poss_dur_loo"]
        del p["by_league"]["7"]
        p["rho_within_mean"] = None
        c = _cell(meas, self.COL, self.GRP)
        assert c.status == "display" and c.reasons == ("possession_untestable",)

    def test_allow_list_admits_exactly_at_its_bound(self, meas):
        self._set_rho(meas, "poss_dur_loo", [0.55, 0.55, 0.55, 0.55], mean=0.55)
        allow = (*R.POSSESSION_ALLOW_LIST, AllowEntry(self.COL, self.GRP, 0.55, "test"))
        c = _cell(meas, self.COL, self.GRP, allow=allow)
        assert c.status == "dna" and c.team_style_biased and "allow_listed" in c.reasons
        assert "team-style influenced" in c.notes

    def test_ratio_coverage_exactly_at_the_bar_is_dna(self, meas):
        m = meas["ratios"]["aerial_win_pct"]["groups"]["ST"]
        m["coverage"] = 0.90
        assert _cell(meas, "aerial_win_pct", "ST").status == "dna"
        m["coverage"] = 0.90 - 1e-12
        c = _cell(meas, "aerial_win_pct", "ST")
        assert c.status == "display" and c.reasons == ("low_coverage",)

    def test_ratio_no_signal_is_display(self, meas):
        m = meas["ratios"]["aerial_win_pct"]["groups"]["ST"]
        m.update(no_signal=True, min_attempts=None, M=None)
        c = _cell(meas, "aerial_win_pct", "ST")
        assert c.status == "display" and c.reasons == ("no_signal",) and c.min_attempts is None

    def test_d12b_rho_is_strict(self, meas):
        p = meas["team_pass_share"]["DM-CM"]["possession"]["poss_pass_loo"]
        p["rho_within_mean"] = -0.3
        assert _cell(meas, "team_pass_share", "DM-CM").status == "display"
        p["rho_within_mean"] = -0.3 + 1e-12
        assert _cell(meas, "team_pass_share", "DM-CM").status == "dna"

    def test_global_ok_band_is_inclusive(self, meas):
        means = self._m(meas)["league_mean_global_pct"]
        means.update({"11": 40.0, "2": 60.0, "12": 50.0, "7": 50.0})
        assert _cell(meas, self.COL, self.GRP).basis == "global"
        means["7"] = 39.999
        c = _cell(meas, self.COL, self.GRP)
        assert c.global_ok is False and c.basis == "local"

    def test_d9_is_undefined_when_a_league_population_is_below_min_cohort(self, meas):
        # §11.1 (user, 2026-10-06): a league mean over fewer than MIN_COHORT_SIZE rows cannot
        # test the band. Per-90s use the gated group populations; 29 → undefined, 30 → tested.
        self._m(meas)["league_mean_global_pct"].update({"11": 50.0, "2": 50.0, "12": 50.0, "7": 50.0})
        sizes = meas["cohorts"][self.GRP]["ref_n_local"]
        sizes["7"] = config.MIN_COHORT_SIZE - 1
        c = _cell(meas, self.COL, self.GRP)
        assert c.global_ok is None and c.d9_margin is None and c.basis == "local"
        sizes["7"] = config.MIN_COHORT_SIZE
        assert _cell(meas, self.COL, self.GRP).basis == "global"

    def test_ratio_d9_uses_its_own_min_attempts_populations(self, meas):
        # ST long_pass_completion_pct passes the band (min league mean 46.05) but its league
        # populations are 12–19, so D9 is undefined; at 30 in every league it would be global.
        m = meas["ratios"]["long_pass_completion_pct"]["groups"]["ST"]
        assert min(m["ref_n_local"].values()) < config.MIN_COHORT_SIZE
        assert _cell(meas, "long_pass_completion_pct", "ST").global_ok is None
        m["ref_n_local"] = {c: config.MIN_COHORT_SIZE for c in m["ref_n_local"]}
        assert _cell(meas, "long_pass_completion_pct", "ST").basis == "global"

    def test_d9_margin_is_the_deciding_leagues_signed_distance(self, committed):
        # Written out from §10.10: inside the band, the nearest league mean's distance to an
        # edge; outside, minus the furthest-out league's distance.
        pool = [str(c) for c in committed["provenance"]["pool_competition_ids"]]
        for (col, grp), cell in R.REGISTRY.cells.items():
            if cell.global_ok is None:
                assert cell.d9_margin is None, (col, grp)
                continue
            spec = _spec(col)
            m = (committed["team_pass_share"][grp] if spec.unit == "share" else
                 committed["metrics" if spec.unit == "per90" else "ratios"][col]["groups"][grp])
            means = [m["league_mean_global_pct"][c] for c in pool]
            expected = min(min(v - 40.0, 60.0 - v) for v in means)
            assert cell.d9_margin == pytest.approx(expected, abs=1e-12), (col, grp)
            assert (cell.d9_margin >= 0) is cell.global_ok

    def test_d9_rows_are_the_dna_cells_within_three_points(self):
        expected = {(c.column, c.group) for c in R.REGISTRY.cells.values()
                    if c.status == "dna" and c.d9_margin is not None and abs(c.d9_margin) <= 3.0}
        assert {(b.column, b.group) for b in R.REGISTRY.boundary if b.rule == "d9_band"} == expected

    def test_d9_margin_exactly_at_three_points_is_reported(self, meas):
        means = self._m(meas)["league_mean_global_pct"]
        means.update({"11": 43.0, "2": 50.0, "12": 50.0, "7": 50.0})
        rows = [b for b in derive(measurements=meas).boundary
                if (b.rule, b.group, b.column) == ("d9_band", self.GRP, self.COL)]
        assert len(rows) == 1 and rows[0].value == 43.0 and rows[0].threshold == 40.0
        means["11"] = 43.0 + 1e-9
        assert not [b for b in derive(measurements=meas).boundary
                    if (b.rule, b.group, b.column) == ("d9_band", self.GRP, self.COL)]

    def test_stored_global_ok_flag_is_not_trusted(self, meas):
        self._m(meas)["global_ok"] = not R.cell(self.COL, self.GRP).global_ok
        assert _cell(meas, self.COL, self.GRP).global_ok == R.cell(self.COL, self.GRP).global_ok

    def test_signature_minimum_floor(self, meas):
        m = meas["metrics"]["restart_key_passes_p90"]["groups"]["CB"]
        m["count_p90"] = 2
        assert _cell(meas, "restart_key_passes_p90", "CB").signature_min_count == 5
        m["count_p90"] = 21
        assert _cell(meas, "restart_key_passes_p90", "CB").signature_min_count == 21


# ── is_signature_holder (ADR 004 §1) ─────────────────────────────────────────────
# The boundary, "below_gate"/None and null-count-raises cases are covered by
# tests/test_compare_page.py::test_is_signature_holder_edges; these fill the gaps.

class TestIsSignatureHolder:
    def test_zero_season_count_on_a_scored_row_is_non_holder_not_an_error(self):
        """Zero is a real count, not a missing value: must not be mistaken for None."""
        c = R.cell("restart_key_passes_p90", "CB")
        assert c.signature_min_count is not None and c.signature_min_count > 0
        assert R.is_signature_holder(c, 0, "scored") is False

    def test_display_status_cell_raises_same_as_dna(self):
        """The guard is "not signature", not "is dna": a display-status cell must raise too."""
        c = R.cell("pass_completion_pct", "CB")
        assert c.status == "display"
        with pytest.raises(ValueError, match="not a signature trait"):
            R.is_signature_holder(c, 50, "scored")


# ── Integration: the specs against the real parquet ─────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not _SEASON_PARQUET.exists(), reason="season parquet not built")
def test_integer_count_flag_matches_the_parquet_dtypes():
    import polars as pl
    schema = pl.read_parquet_schema(_SEASON_PARQUET)
    for s in R.SPECS:
        if s.unit == "per90":
            assert schema[s.count_column].is_integer() == s.integer_count, s.count_column


@pytest.mark.parametrize("spike, name", [
    ("m4_decision_measurements.py", "GLOBAL_OK_BAND"),
    ("m4_decision_measurements.py", "MIN_COHORT_SIZE"),
    ("m4d_percentiles_reference.py", "GLOBAL_OK_BAND"),
    ("m4d_percentiles_reference.py", "MIN_COHORT_SIZE"),
])
def test_spike_constants_equal_config(spike, name):
    """One source for the D9 band and the cohort minimum (user, 2026-10-05/06): the spikes
    cannot import eleveniq, so they write out their own copies; this keeps them equal."""
    import ast
    tree = ast.parse((_ROOT / "spikes" / spike).read_text())
    values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert values == [getattr(config, name)]
