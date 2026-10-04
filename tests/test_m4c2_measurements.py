"""
Spec tests for the committed M4c2 registry measurements
(src/eleveniq/analytics/measurements/statsbomb_27.json), written by
spikes/m4_decision_measurements.py --write-json (plan 005 M4c2, ADR 004 §10).

M4c3's derive() turns this file into every metric x group status, and ADR 004
§10.6 / §10.10 record the lists and boundary values it must reproduce. So the
file must (a) hold measurements only, (b) be internally consistent with the
definitions in ADR 004 §10.1, §10.3 and §10.5, and (c) reconcile with the
figures the ADR quotes. The file is a committed package resource, not data/,
so the fast tests read it; the integration tests compare it with the current
pipeline output and with a fresh spike run.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_JSON = _ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
_SPIKE = _ROOT / "spikes" / "m4_decision_measurements.py"
_SEASON_PARQUET = _ROOT / "data" / "processed" / "player_season_stats.parquet"
_MATCH_PARQUET = _ROOT / "data" / "interim" / "player_match_stats.parquet"
_RAW_MATCHES = _ROOT / "data" / "raw" / "statsbomb" / "matches"

OUTFIELD = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
POOL = ("11", "2", "12", "7")
RATIOS = {"pass_completion_pct", "long_pass_completion_pct", "cross_completion_pct",
          "dribble_success_pct", "tackle_success_pct", "aerial_win_pct", "np_xg_per_shot"}


def _reject_constant(name: str):
    raise ValueError(f"non-standard JSON constant {name}")


@pytest.fixture(scope="module")
def m() -> dict:
    # NaN / Infinity would make every threshold comparison in derive() silently False.
    with _JSON.open() as f:
        return json.load(f, parse_constant=_reject_constant)


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + (k,), k, v
            yield from _walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (i,))


def _possession_blocks(m: dict):
    """Every (where, proxy, block) possession measurement in the file."""
    for metric, spec in m["metrics"].items():
        for grp, cell in spec["groups"].items():
            for proxy, block in cell["possession"].items():
                yield f"{metric}/{grp}", proxy, block
    for ratio, spec in m["ratios"].items():
        for grp, cell in spec["groups"].items():
            for proxy, block in cell.get("possession", {}).items():
                yield f"{ratio}/{grp}", proxy, block
    for grp, cell in (m["team_pass_share"] or {}).items():
        for proxy, block in cell["possession"].items():
            yield f"team_pass_share/{grp}", proxy, block


class TestMeasurementsScope:
    """ADR 004 §10.1: source statsbomb, season 27, pool 11/2/12/7 (Bundesliga outside), every
    per-90 and ratio over the five outfield groups (never GK, D21), measurements only."""

    def test_provenance_pool_excludes_bundesliga(self, m):
        p = m["provenance"]
        assert (p["source"], p["season_id"]) == ("statsbomb", 27)
        assert sorted(p["pool_competition_ids"]) == [2, 7, 11, 12]
        assert 9 not in p["pool_competition_ids"]

    def test_metric_set_is_every_season_per90_except_goals_and_assists(self, m):
        """43 per-90s = every *_p90 column of the season parquet except goals/assists (never DNA);
        np_shots_on_target_p90 joined in M4c2b."""
        cols = m["provenance"]["season_columns"]
        assert len(cols) == 144
        expected = {c for c in cols if c.endswith("_p90")} - {"goals_p90", "assists_p90"}
        assert set(m["metrics"]) == expected
        assert len(expected) == 43

    def test_ratio_set_and_columns_exist(self, m):
        """The 7 ratios, and every column a ratio or per-90 is built from is a season column."""
        cols = set(m["provenance"]["season_columns"])
        assert set(m["ratios"]) == RATIOS
        assert RATIOS <= cols
        for name, spec in m["ratios"].items():
            used = {spec["numerator"], *spec["denominator_plus"], *spec["denominator_minus"]}
            assert used <= cols, (name, used - cols)
        for name, spec in m["metrics"].items():
            assert spec["count_column"] in cols and name == spec["count_column"] + "_p90"

    def test_completion_denominators_follow_d13(self, m):
        """D13: pass and long-pass rates subtract unknown outcomes and Injury Clearances; crosses
        subtract unknown outcomes only (they keep Injury Clearances)."""
        r = m["ratios"]
        assert set(r["pass_completion_pct"]["denominator_minus"]) == {
            "passes_unknown_outcome", "passes_injury_clearance"}
        assert set(r["long_pass_completion_pct"]["denominator_minus"]) == {
            "long_passes_unknown_outcome", "long_passes_injury_clearance"}
        assert r["cross_completion_pct"]["denominator_minus"] == ["crosses_unknown_outcome"]

    def test_every_cell_covers_exactly_the_outfield_groups(self, m):
        for spec in [*m["metrics"].values(), *m["ratios"].values()]:
            assert set(spec["groups"]) == set(OUTFIELD)
        assert set(m["cohorts"]) == set(OUTFIELD)
        assert set(m["team_pass_share"]) == {"DM-CM", "CB"}   # D12b is pre-registered for these two only

    def test_no_rule_outcome_is_stored(self, m):
        """§10.1: the registry's thresholds, the sign test and the trait minimum are applied in
        eleveniq. The stored spike-applied outcomes are the ones §10.1 lists (global_ok,
        min_attempts / no_signal, coverage, ref_n_*, count_p90); no status or rule verdict is stored."""
        rule_keys = {"same_sign", "status", "dna", "enters_dna", "reliable", "below_min_cohort",
                     "triggered", "signature", "min_count"}
        found = [path for path, k, _ in _walk(m) if k in rule_keys]
        assert not found, found


class TestMeasurementsInternalConsistency:
    """Definitions from ADR 004 §10.3 / §10.5 hold on every cell, so derive() can rely on them."""

    def test_min_attempts_is_ceil_1_5_m_and_null_iff_no_signal(self, m):
        """min_attempts = ceil(1.5 M) (reliability 0.6); tau2 <= 0 -> M, min_attempts null and no
        fallback: nobody meets it, so coverage and every reference population are 0."""
        for name, spec in m["ratios"].items():
            for grp, c in spec["groups"].items():
                where = (name, grp)
                assert c["no_signal"] is (c["M"] is None), where
                if c["M"] is None:
                    assert c["min_attempts"] is None, where
                    assert c["coverage"] == 0 and c["ref_n_global"] == 0, where
                    assert set(c["ref_n_local"].values()) == {0}, where
                else:
                    assert c["M"] > 0, where
                    assert c["min_attempts"] == math.ceil(1.5 * c["M"]), where

    def test_no_signal_cells_are_the_adr_ones(self, m):
        """§10.5: 2015/16 no-signal cells are CB tackle_success_pct and CB cross_completion_pct."""
        cells = {(n, g) for n, s in m["ratios"].items() for g, c in s["groups"].items() if c["no_signal"]}
        assert cells == {("tackle_success_pct", "CB"), ("cross_completion_pct", "CB")}

    def test_np_xg_per_shot_m_is_variance_components(self, m):
        """§10.5: M = sigma2_w / tau2 (moment form, the one used); the variants are recorded only."""
        for grp, c in m["ratios"]["np_xg_per_shot"]["groups"].items():
            vc = c["variance_components"]
            assert vc["tau2"] > 0
            assert c["M"] == pytest.approx(vc["sigma2_w"] / vc["tau2"], rel=1e-12), grp
            assert c["M"] == vc["M"]
            assert vc["rows"] == c["n_estimate_rows"]
            b = vc["bootstrap"]
            assert b["M_p05"] <= b["M_p50"] and (b["M_p95"] is None or b["M_p50"] <= b["M_p95"])

    def test_reference_population_sizes_nest(self, m):
        """A reference population is a deduped subset of the gated rows: local <= global <= gated;
        a ratio's population (rows meeting min_attempts) is inside the group's cohort; local
        populations are per league x group, so a mover between pool leagues counts in two locals
        but once globally (sum of locals >= global). Only pool leagues have a local population."""
        for grp, co in m["cohorts"].items():
            assert set(co["ref_n_local"]) == set(POOL)
            assert co["ref_n_global"] <= co["n_gated"]
            assert sum(co["ref_n_local"].values()) >= co["ref_n_global"]
            assert max(co["ref_n_local"].values()) <= co["ref_n_global"]
        for name, spec in m["ratios"].items():
            for grp, c in spec["groups"].items():
                co = m["cohorts"][grp]
                where = (name, grp)
                assert c["n_gated"] == co["n_gated"], where
                assert set(c["ref_n_local"]) == set(POOL), where
                assert c["ref_n_global"] <= co["ref_n_global"], where
                for lg, n in c["ref_n_local"].items():
                    assert n <= co["ref_n_local"][lg] and n <= c["ref_n_global"], where
                assert sum(c["ref_n_local"].values()) >= c["ref_n_global"], where
                # coverage counts gated rows before dedupe; dedupe can only shrink.
                meeting = c["coverage"] * c["n_gated"]
                assert meeting == pytest.approx(round(meeting), abs=1e-9), where
                assert c["ref_n_global"] <= round(meeting), where
        for spec in m["metrics"].values():
            for grp, c in spec["groups"].items():
                assert c["n_gated"] == m["cohorts"][grp]["n_gated"]

    def test_count_p90_sanity_and_holders_are_monotone(self, m):
        """§10.2 consequences only: P90 = 0 iff at least 90% of gated rows have a zero count, and
        holders at a higher minimum are never more. The quantile itself is checked against the
        parquet in TestCountP90AgainstParquet (integration)."""
        for name, spec in m["metrics"].items():
            for grp, c in spec["groups"].items():
                where = (name, grp)
                p90 = c["count_p90"]
                # count columns are integers, except xG sums (np_xg, op_xg_assisted, ...)
                assert p90 >= 0, where
                assert (p90 == 0) is (c["zero_share"] >= 0.9), where
                h = {float(k): v for k, v in c["holders_at_count"].items()}
                assert set(h) == {5, 10, 15, p90}, where
                vals = [h[t] for t in sorted(h)]
                assert vals == sorted(vals, reverse=True), where
                if p90 > 0:
                    # at least ~10% of rows are at or above P90 (P90 is a value some rows reach)
                    assert h[p90] >= 1, where
                # rows with count >= 5 have a non-zero count
                assert h[5] <= round((1 - c["zero_share"]) * c["n_gated"]), where

    def test_global_ok_equals_the_d9_inclusive_band(self, m):
        """§10.3 D9: global_ok iff every pool league's mean global percentile is in [40, 60]."""
        n_ok = 0
        for name, spec in m["metrics"].items():
            for grp, c in spec["groups"].items():
                means = c["league_mean_global_pct"]
                assert set(means) == set(POOL), (name, grp)
                rule = all(40.0 <= v <= 60.0 for v in means.values())
                assert c["global_ok"] is rule, (name, grp, means)
                n_ok += rule
        assert n_ok == 166   # §10.1: 166 of 215 (M4c2b: np_shots_on_target is global_ok in all 5)

    def test_rho_within_mean_is_mean_of_the_pool_leagues(self, m):
        """§10.3: rho for a proxy is the mean of the four within-league Spearman rho, defined only
        when all four pool leagues have a rho; otherwise it is null (the cell is untestable).
        n_by_league records the rows used in each league."""
        n = 0
        for where, proxy, b in _possession_blocks(m):
            assert proxy in ("poss_dur_loo", "poss_pass_loo"), where
            by = b["by_league"]
            assert set(by) <= set(POOL), (where, proxy, by)
            assert set(by) <= set(b["n_by_league"]) <= set(POOL), (where, proxy)
            assert all(-1.0 <= v <= 1.0 for v in by.values()), (where, proxy)
            if set(by) == set(POOL):
                assert b["rho_within_mean"] == pytest.approx(sum(by.values()) / 4, abs=1e-12), (where, proxy)
            else:
                assert b["rho_within_mean"] is None, (where, proxy, by)
            n += 1
        assert n >= 2 * (43 * 5 + 2)

    def test_cells_missing_a_league_rho_cannot_reach_dna(self, m):
        """A league whose rho is undefined (constant values or < 2 rows) is dropped from by_league,
        so the mean is over fewer than four leagues, not the four of §10.3. That is only harmless
        while every such cell is fixed display (§10.3 step 1); a judged cell must have all four."""
        short = {(where.split("/")[0], where.split("/")[1]) for where, _, b in _possession_blocks(m)
                 if set(b["by_league"]) != set(POOL)}
        judged = {(name, grp) for name, grp in short if name not in _FIXED_DISPLAY}
        assert not judged, sorted(judged)

    def test_collinear_pairs_respect_floor_and_are_unique(self, m):
        seen = set()
        for p in m["collinear_pairs"]:
            assert p["a"] != p["b"] and p["group"] in OUTFIELD
            assert abs(p["rho"]) >= m["population"]["collinear_report_floor"] == 0.7
            key = (frozenset((p["a"], p["b"])), p["group"])
            assert key not in seen, p
            seen.add(key)


# ADR 004 §10.3 fixed display and D17 choices, written out from the ADR (not from code).
_FIXED_DISPLAY = {
    "xg_p90", "shots_p90", "xg_per_shot", "shots_on_target_p90", "xg_assisted_direct_p90",
    "dispossessed_p90", "miscontrols_p90", "switches_p90", "passes_attempted_p90",
    "passes_completed_p90", "carries_p90", "crosses_attempted_p90", "key_passes_p90",
    "xg_assisted_p90", "goals_p90", "assists_p90", "op_assists_p90", "restart_assists_p90",
    "restart_xg_assisted_p90", "dribble_success_pct", "tackle_success_pct", "cross_completion_pct",
}
_D17_DROPPED = {
    **{(g, "tackles_won_p90"): "tackles_attempted_p90" for g in OUTFIELD},
    **{(g, "dribbles_completed_p90"): "dribbles_attempted_p90" for g in OUTFIELD},
    **{(g, "final_third_entries_carry_p90"): "progressive_carries_p90" for g in ("AM-Winger", "FB-WB", "ST")},
    ("DM-CM", "final_third_entries_pass_p90"): "progressive_passes_p90",
}


# ADR 004 §10.3: ratio pairs at |rho| >= 0.85 (rows meeting min_attempts). Each has a member that
# is display in that group: dribble_success_pct is fixed display (FB-WB 7 rows, ST 4 rows: noise),
# np_xg_per_shot is display by U5 in every group.
_RATIO_PAIRS_OVER_085 = {
    ("ST", frozenset(("dribble_success_pct", "dispossessed_p90"))),
    ("ST", frozenset(("dribble_success_pct", "final_third_entries_carry_p90"))),
    ("ST", frozenset(("pass_completion_pct", "dribble_success_pct"))),
    ("FB-WB", frozenset(("dribble_success_pct", "tackles_attempted_p90"))),
    ("FB-WB", frozenset(("pass_completion_pct", "dribble_success_pct"))),
    ("FB-WB", frozenset(("np_xg_per_shot", "np_xg_p90"))),
}


class TestMeasurementsReconcileWithAdr004:
    """The figures ADR 004 §10 quotes (and M4c3's derive() must reproduce) match the file."""

    # ADR 004 §10.6 (M4c2b): np_shots_on_target_p90 per group, r_full, zero share and the larger
    # within-league mean possession rho of the two proxies, as quoted in the ADR.
    _NP_SOT = {"CB": (0.277656, 0.1373, 0.052089), "FB-WB": (0.580878, 0.1606, 0.211560),
               "DM-CM": (0.592665, 0.0442, 0.157286), "AM-Winger": (0.700334, 0.0036, 0.389822),
               "ST": (0.679293, 0.0, 0.395111)}

    @pytest.mark.parametrize("grp", sorted(_NP_SOT))
    def test_np_shots_on_target_figures_match_adr(self, m, grp):
        """§10.6: the inputs that make np_shots_on_target DNA in AM-Winger and ST only: no cell is
        zero-heavy, r_full < 0.6 in CB / FB-WB / DM-CM, and no cell triggers the possession rule
        (|mean rho| < 0.4 on both proxies). Every pair at |rho| >= 0.85 is with shots_on_target_p90
        (fixed display), and np_shots_p90 stays below 0.85."""
        c = m["metrics"]["np_shots_on_target_p90"]["groups"][grp]
        r, zero, rho = self._NP_SOT[grp]
        assert c["r_full"] == pytest.approx(r, abs=1e-6)
        assert c["zero_share"] == pytest.approx(zero, abs=1e-4)
        rhos = [c["possession"][k]["rho_within_mean"] for k in ("poss_dur_loo", "poss_pass_loo")]
        assert max(abs(x) for x in rhos) == pytest.approx(rho, abs=1e-6)
        assert c["global_ok"] is True
        strong = [p for p in m["collinear_pairs"] if p["group"] == grp and abs(p["rho"]) >= 0.85
                  and "np_shots_on_target_p90" in (p["a"], p["b"])]
        assert [{p["a"], p["b"]} - {"np_shots_on_target_p90"} for p in strong] == [{"shots_on_target_p90"}]

    def test_every_d17_pair_is_resolved(self, m):
        """§10.3: 67 per-90 pairs at |rho| >= 0.85 (M4c2b adds 5, np_shots_on_target ~ shots_on_target), each resolved by fixed display or a D17 choice,
        plus the ratio pairs §10.3 lists, each with a member that is display in that group. An
        unresolved pair would leave two near-duplicate dimensions in a DNA vector."""
        strong = [p for p in m["collinear_pairs"] if abs(p["rho"]) >= 0.85]
        per90 = [p for p in strong if p["a"].endswith("_p90") and p["b"].endswith("_p90")]
        assert len(per90) == 67
        ratio_pairs = {(p["group"], frozenset((p["a"], p["b"]))) for p in strong if p not in per90}
        assert ratio_pairs == _RATIO_PAIRS_OVER_085
        unresolved = []
        for p in per90:
            a, b, g = p["a"], p["b"], p["group"]
            if a in _FIXED_DISPLAY or b in _FIXED_DISPLAY:
                continue
            if _D17_DROPPED.get((g, a)) == b or _D17_DROPPED.get((g, b)) == a:
                continue
            unresolved.append(p)
        assert not unresolved, unresolved

    @pytest.mark.parametrize("grp,M,min_att,coverage,r,rows", [
        ("CB", 64.78, 98, 0.0, 0.175, 125),
        ("FB-WB", 13.09, 20, 0.173, 0.224, 131),
        ("DM-CM", 15.37, 24, 0.401, 0.448, 267),
        ("AM-Winger", 19.47, 30, 0.657, 0.542, 359),
        ("ST", 28.12, 43, 0.614, 0.464, 223),
    ])
    def test_np_xg_per_shot_table(self, m, grp, M, min_att, coverage, r, rows):
        c = m["ratios"]["np_xg_per_shot"]["groups"][grp]
        assert c["M"] == pytest.approx(M, abs=0.005)
        assert c["min_attempts"] == min_att
        assert c["coverage"] == pytest.approx(coverage, abs=0.0005)
        assert c["split_half_r_full"] == pytest.approx(r, abs=0.0005)
        assert c["n_estimate_rows"] == rows

    def test_reference_cells_below_min_cohort_size_are_the_adr_list(self, m):
        """§10.5: the ratio x group cells whose global or smallest local reference population is
        below MIN_COHORT_SIZE (30), with their sizes. M4d nulls exactly these (cohort_too_small)."""
        expected = {
            **{(r, g): (0, 0) for r in ("cross_completion_pct", "tackle_success_pct") for g in OUTFIELD},
            ("dribble_success_pct", "CB"): (0, 0), ("dribble_success_pct", "FB-WB"): (7, 1),
            ("dribble_success_pct", "DM-CM"): (9, 1), ("dribble_success_pct", "AM-Winger"): (41, 5),
            ("dribble_success_pct", "ST"): (4, 0),
            ("long_pass_completion_pct", "ST"): (64, 12),
            ("np_xg_per_shot", "CB"): (0, 0), ("np_xg_per_shot", "FB-WB"): (43, 4),
            ("np_xg_per_shot", "DM-CM"): (118, 23), ("np_xg_per_shot", "ST"): (96, 23),
        }
        actual = {}
        for name, spec in m["ratios"].items():
            for grp, c in spec["groups"].items():
                g, lo = c["ref_n_global"], min(c["ref_n_local"].values())
                if g < 30 or lo < 30:
                    actual[(name, grp)] = (g, lo)
        assert actual == expected

    @pytest.mark.parametrize("grp,p90,holders,r", [
        ("CB", 1, 4, 0.719), ("FB-WB", 4, 20, 0.747), ("DM-CM", 13, 33, 0.932),
        ("AM-Winger", 19, 31, 0.838), ("ST", 1, 5, 0.930),
    ])
    def test_restart_key_passes_signature_table(self, m, grp, p90, holders, r):
        """§10.6: minimum count = max(5, P90) and its holders among gated rows."""
        c = m["metrics"]["restart_key_passes_p90"]["groups"][grp]
        assert c["count_p90"] == p90
        assert c["holders_at_count"][str(max(5, p90))] == holders
        assert c["r_full"] == pytest.approx(r, abs=0.0005)

    def test_cohort_sizes_quoted_in_10_1(self, m):
        co = m["cohorts"]
        assert {g: co[g]["n_gated"] for g in OUTFIELD} == {
            "CB": 255, "FB-WB": 249, "DM-CM": 294, "AM-Winger": 277, "ST": 158}
        assert co["AM-Winger"]["ref_n_global"] == 275
        assert co["ST"]["ref_n_local"]["12"] == 44

    @pytest.mark.parametrize("kind,grp,name,value", [
        ("r", "CB", "interceptions_p90", 0.599849),
        ("r", "CB", "fouls_committed_p90", 0.589712),
        ("r", "CB", "aerials_lost_p90", 0.612088),
        ("r", "FB-WB", "fouls_committed_p90", 0.597776),
        ("r", "FB-WB", "aerials_lost_p90", 0.606793),
        ("r", "FB-WB", "dribbled_past_p90", 0.618266),
        ("r", "DM-CM", "blocks_p90", 0.608874),
        ("r", "ST", "dribbled_past_p90", 0.597873),
        ("r", "ST", "np_xg_p90", 0.599369),
        ("r", "ST", "tackles_attempted_p90", 0.612458),
        ("poss_dur_loo", "FB-WB", "final_third_entries_pass_p90", 0.409583),
        ("poss_pass_loo", "FB-WB", "final_third_entries_pass_p90", 0.401950),
        ("poss_dur_loo", "FB-WB", "progressive_carries_p90", 0.407480),
        ("poss_dur_loo", "FB-WB", "long_pass_completion_pct", 0.417085),
        ("poss_pass_loo", "FB-WB", "long_pass_completion_pct", 0.416738),
        ("poss_pass_loo", "AM-Winger", "np_xg_p90", 0.410867),
        ("poss_pass_loo", "AM-Winger", "op_xg_assisted_p90", 0.419109),
        ("poss_dur_loo", "AM-Winger", "pass_completion_pct", 0.383490),
        ("poss_pass_loo", "AM-Winger", "pass_completion_pct", 0.410877),
        ("allow", "CB", "progressive_carries_p90", 0.548655),
    ])
    def test_boundary_report_values(self, m, kind, grp, name, value):
        """§10.10: the fragile values, at 6 decimals. The allow-list value is the larger of the two
        proxies' |mean rho| (the §10.3 reading under which CB progressive_carries passes)."""
        spec = m["metrics"].get(name) or m["ratios"][name]
        cell = spec["groups"][grp]
        if kind == "r":
            got = cell["r_full"]
        elif kind == "allow":
            got = max(abs(cell["possession"][p]["rho_within_mean"]) for p in ("poss_dur_loo", "poss_pass_loo"))
        else:
            got = cell["possession"][kind]["rho_within_mean"]
        assert got == pytest.approx(value, abs=5e-7)

    def test_d9_fragile_examples(self, m):
        """§10.10: tackles_attempted AM-Winger fails D9 (Serie A 39.99) since the per-group dedupe;
        miscontrols FB-WB 40.0014 and fouls_committed DM-CM 40.0146 pass."""
        met = m["metrics"]
        c = met["tackles_attempted_p90"]["groups"]["AM-Winger"]
        assert c["global_ok"] is False and c["league_mean_global_pct"]["12"] == pytest.approx(39.99, abs=0.005)
        assert met["miscontrols_p90"]["groups"]["FB-WB"]["global_ok"] is True
        assert min(met["miscontrols_p90"]["groups"]["FB-WB"]["league_mean_global_pct"].values()) \
            == pytest.approx(40.0014, abs=5e-5)
        assert met["fouls_committed_p90"]["groups"]["DM-CM"]["global_ok"] is True
        assert min(met["fouls_committed_p90"]["groups"]["DM-CM"]["league_mean_global_pct"].values()) \
            == pytest.approx(40.0146, abs=5e-5)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@pytest.mark.integration
@pytest.mark.skipif(not (_SEASON_PARQUET.exists() and _MATCH_PARQUET.exists()),
                    reason="pipeline output missing (run both pipeline stages)")
class TestMeasurementsMatchPipelineOutput:
    """The committed file describes the current parquets. A pipeline change rebuilt with --force
    without regenerating the JSON would leave derive() deciding on stale measurements."""

    def test_parquet_hashes_match_provenance(self):
        with _JSON.open() as f:
            p = json.load(f)["provenance"]
        assert _sha256(_SEASON_PARQUET) == p["season_parquet_sha256"]
        assert _sha256(_MATCH_PARQUET) == p["match_parquet_sha256"]

    def test_season_columns_match_parquet_schema(self):
        import polars as pl
        with _JSON.open() as f:
            p = json.load(f)["provenance"]
        assert pl.read_parquet_schema(_SEASON_PARQUET).names() == p["season_columns"]


@pytest.mark.integration
@pytest.mark.skipif(not _SEASON_PARQUET.exists(), reason="pipeline output missing (run both pipeline stages)")
class TestCountP90AgainstParquet:
    """§10.2: the signature minimum is max(5, P90), with P90 the inverse ECDF of the season count
    among gated rows (in_percentile_pool, is_outfield, meets_min_minutes): the smallest observed c
    with share(count <= c) >= 0.9. Recomputed here from the parquet by a linear scan, independently
    of the spike's index formula."""

    def test_count_p90_and_holders(self, m):
        import polars as pl
        season = pl.read_parquet(_SEASON_PARQUET).filter(
            pl.col("in_percentile_pool") & pl.col("is_outfield") & pl.col("meets_min_minutes"))
        checked = 0
        for name, spec in m["metrics"].items():
            count_col = spec["count_column"]
            assert count_col == name.removesuffix("_p90")
            for grp, c in spec["groups"].items():
                vals = season.filter(pl.col("position_group") == grp)[count_col].to_list()
                n = len(vals)
                assert n == c["n_gated"], (name, grp)
                p90 = next(v for v in sorted(set(vals)) if sum(x <= v for x in vals) >= 0.9 * n)
                assert c["count_p90"] == pytest.approx(p90, abs=1e-12), (name, grp)
                assert c["holders_at_count"][str(c["count_p90"])] == sum(x >= p90 for x in vals), (name, grp)
                checked += 1
        assert checked == 43 * 5


def _assert_close(a, b, path="$"):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, set(a) ^ set(b) if isinstance(b, dict) else b)
        for k in a:
            _assert_close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_close(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None, (path, a, b)
        assert a == pytest.approx(b, abs=1e-9), (path, a, b)
    else:
        assert a == b, (path, a, b)


@pytest.mark.integration
@pytest.mark.skipif(not (_SEASON_PARQUET.exists() and _MATCH_PARQUET.exists() and _RAW_MATCHES.exists()),
                    reason="raw data or pipeline output missing")
def test_spike_rerun_reproduces_committed_json(tmp_path):
    """§10.1: two runs agree within 1e-9 on every value (fixed bootstrap seed). A fresh run must
    reproduce the committed file, so the file is what the current spike code produces."""
    out = tmp_path / "statsbomb_27.json"
    proc = subprocess.run([sys.executable, str(_SPIKE), "--write-json", str(out)],
                          cwd=_ROOT, capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    with _JSON.open() as f:
        committed = json.load(f)
    with out.open() as f:
        fresh = json.load(f)
    _assert_close(committed, fresh)
