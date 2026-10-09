"""
Hand-written spec tests for the metric registry (src/eleveniq/analytics/registry.py,
plan 005 M4c3), transcribed from ADR 004 §10 ("Metric registry decisions"), especially
§10.3, §10.6, §10.7 and §10.10. Since M4g2, the DNA lists and the boundary report's status
and note rows are transcribed from §13.6, which supersedes §10.6 and §10.10 for 2015/16.

These values are copied from the ADR text itself, not read off the registry's own
output: the point is to catch a registry/ADR mismatch, not to describe whatever the
code currently does. tests/test_registry.py covers the *rules* (predicates, overrides,
RegistryRuleError) independently of these concrete lists.

Only the public API is used: `from eleveniq.analytics import registry as R`.
"""
from __future__ import annotations

import math

import pytest

from eleveniq.analytics import registry as R

GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")


def col(name: str) -> str:
    """ADR §10.6 names are season columns; per-90s add the stored _p90 suffix, ratios
    (*_pct, np_xg_per_shot, xg_per_shot) and team_pass_share do not (task brief)."""
    if name.endswith("_pct") or name in ("np_xg_per_shot", "xg_per_shot", "team_pass_share"):
        return name
    return f"{name}_p90"


# ── §13.6 (supersedes §10.6): the exact DNA lists, with * (low_reliability) and (AL) (team_style_biased) ──

# Each entry: (bare metric name, starred, allow-listed).
DNA_SPEC: dict[str, list[tuple[str, bool, bool]]] = {
    "CB": [
        ("aerials_won", False, False),
        ("ball_recoveries", False, False),
        ("box_entries_pass", False, False),
        ("clearances", True, False),
        ("dribbled_past", True, False),
        ("dribbles_attempted", True, False),
        ("final_third_entries_carry", False, False),
        ("final_third_entries_pass", False, False),
        ("fouls_won", True, False),
        ("long_passes_attempted", False, False),
        ("pressures", False, False),
        ("progressive_carries", False, True),
        ("progressive_passes", False, True),
        ("tackles_attempted", False, False),
        ("long_pass_completion_pct", False, True),
    ],
    "FB-WB": [
        ("aerials_won", False, False),
        ("ball_recoveries", False, False),
        ("box_entries_carry", False, False),
        ("box_entries_pass", False, False),
        ("clearances", False, False),
        ("dribbled_past", True, False),
        ("dribbles_attempted", False, False),
        ("fouls_committed", True, False),
        ("fouls_won", False, False),
        ("long_passes_attempted", False, False),
        ("np_shots", False, False),
        ("op_key_passes", True, False),
        ("pressures", False, False),
        ("progressive_carries", False, True),
        ("progressive_passes", False, True),
        ("tackles_attempted", False, False),
        ("long_pass_completion_pct", True, True),
    ],
    "DM-CM": [
        ("aerials_lost", False, False),
        ("aerials_won", False, False),
        ("ball_recoveries", False, False),
        ("blocks", True, False),
        ("box_entries_carry", False, False),
        ("box_entries_pass", False, False),
        ("clearances", False, False),
        ("dribbled_past", True, False),
        ("dribbles_attempted", False, False),
        ("final_third_entries_carry", False, False),
        ("fouls_committed", False, False),
        ("fouls_won", False, False),
        ("interceptions", True, False),
        ("long_passes_attempted", False, False),
        ("np_shots", False, False),
        ("np_shots_on_target", True, False),
        ("np_xg", False, False),
        ("op_key_passes", False, False),
        ("op_xg_assisted", True, False),
        ("pressures", False, False),
        ("progressive_carries", False, True),
        ("progressive_passes", False, True),
        ("tackles_attempted", False, False),
        ("long_pass_completion_pct", False, True),
        ("team_pass_share", False, False),
    ],
    "AM-Winger": [
        ("aerials_lost", False, False),
        ("aerials_won", False, False),
        ("ball_recoveries", False, False),
        ("blocks", True, False),
        ("box_entries_carry", False, False),
        ("box_entries_pass", False, False),
        ("clearances", False, False),
        ("dribbled_past", False, False),
        ("dribbles_attempted", False, False),
        ("final_third_entries_pass", False, False),
        ("fouls_committed", False, False),
        ("fouls_won", False, False),
        ("interceptions", True, False),
        ("long_passes_attempted", False, False),
        ("np_shots", False, False),
        ("np_shots_on_target", False, False),
        ("op_key_passes", True, False),
        ("pressures", False, False),
        ("progressive_carries", False, False),
        ("progressive_passes", False, False),
        ("tackles_attempted", False, False),
        ("long_pass_completion_pct", False, False),
    ],
    "ST": [
        ("aerials_lost", False, False),
        ("aerials_won", False, False),
        ("ball_recoveries", False, False),
        ("blocks", True, False),
        ("box_entries_carry", False, False),
        ("box_entries_pass", False, False),
        ("clearances", False, False),
        ("dribbled_past", True, False),
        ("dribbles_attempted", False, False),
        ("final_third_entries_pass", False, False),
        ("fouls_committed", False, False),
        ("fouls_won", False, False),
        ("long_passes_attempted", False, False),
        ("np_shots", False, False),
        ("pressures", False, False),
        ("progressive_carries", False, False),
        ("progressive_passes", False, False),
        ("tackles_attempted", False, False),
        ("pass_completion_pct", False, False),
        ("aerial_win_pct", False, False),
    ],
}

DNA_COUNTS = {"CB": 15, "FB-WB": 17, "DM-CM": 25, "AM-Winger": 22, "ST": 20}


class TestDnaLists:
    """ADR 004 §13.6 (supersedes §10.6): the exact DNA set per group, the * markers and the
    (AL) flag."""

    @pytest.mark.parametrize("group", GROUPS)
    def test_dna_count_matches_adr(self, group):
        assert len(DNA_SPEC[group]) == DNA_COUNTS[group]

    @pytest.mark.parametrize("group", GROUPS)
    def test_dna_set_matches_adr(self, group):
        expected = tuple(sorted(col(name) for name, _, _ in DNA_SPEC[group]))
        assert R.dna_metrics(group) == expected

    @pytest.mark.parametrize("group", GROUPS)
    def test_low_reliability_and_team_style_flags_match_adr(self, group):
        for name, starred, al in DNA_SPEC[group]:
            cell = R.cell(col(name), group)
            assert cell.status == "dna", f"{name} x {group} is not DNA"
            assert cell.low_reliability is starred, (
                f"{name} x {group}: expected low_reliability={starred}, got "
                f"{cell.low_reliability}")
            assert cell.team_style_biased is al, (
                f"{name} x {group}: expected team_style_biased={al}, got "
                f"{cell.team_style_biased}")


# ── §10.6: signature traits ──────────────────────────────────────────────────────────

SIGNATURE_MIN_COUNT = {"CB": 5, "FB-WB": 5, "DM-CM": 13, "AM-Winger": 19, "ST": 5}


class TestSignatureTraits:
    """ADR 004 §10.6: restart_key_passes is the only signature trait in every group;
    AM-Winger reaches it by override (U2), the rest by rule; CB box_entries_carry is
    display, not signature (R15 amendment of §1)."""

    @pytest.mark.parametrize("group", GROUPS)
    def test_restart_key_passes_is_the_only_signature_trait(self, group):
        assert R.signature_traits(group) == ("restart_key_passes_p90",)

    @pytest.mark.parametrize("group", GROUPS)
    def test_signature_min_count_matches_adr(self, group):
        cell = R.cell("restart_key_passes_p90", group)
        assert cell.signature_min_count == SIGNATURE_MIN_COUNT[group]

    def test_am_winger_restart_key_passes_reached_by_override(self):
        # §10.3 (user, 2026-10-05): an override cell's reasons are its status and the override's
        # ADR reference, not the replaced rule outcome (M4e shows reasons).
        cell = R.cell("restart_key_passes_p90", "AM-Winger")
        assert cell.reasons == ("signature", "override: ADR 004 §8, §10.2 U2")

    @pytest.mark.parametrize("group", [g for g in GROUPS if g != "AM-Winger"])
    def test_other_groups_reach_signature_by_rule_not_override(self, group):
        cell = R.cell("restart_key_passes_p90", group)
        assert not any(r.startswith("override") for r in cell.reasons)
        assert cell.reasons == ("zero_heavy",)

    def test_cb_box_entries_carry_is_display_not_signature(self):
        # §1, amended by R15 (§10.2): r_full 0.454 < 0.6, so zero-heavy -> display.
        cell = R.cell("box_entries_carry_p90", "CB")
        assert cell.status == "display"


# ── §13.6 (supersedes §10.10's status and note rows): the boundary report ────────────

# (rule, group, column): (value, threshold, status, team_style_biased, low_reliability, per_league)
BOUNDARY_SPEC: dict[tuple[str, str, str], tuple] = {
    ("r_full", "FB-WB", "interceptions_p90"): (0.585339, 0.6, "display", False, False, None),
    ("r_full", "FB-WB", "np_xg_p90"): (0.595898, 0.6, "display", False, False, None),
    ("r_full", "DM-CM", "blocks_p90"): (0.617896, 0.6, "dna", False, True, None),
    ("r_full", "ST", "blocks_p90"): (0.611414, 0.6, "dna", False, True, None),

    ("rho_trigger:poss_dur_loo", "FB-WB", "final_third_entries_pass_p90"): (
        0.409583, 0.4, "display", True, False, (0.346, 0.600, 0.313, 0.380)),
    ("rho_trigger:poss_pass_loo", "FB-WB", "final_third_entries_pass_p90"): (
        0.401950, 0.4, "display", True, False, (0.386, 0.581, 0.302, 0.339)),
    ("rho_trigger:poss_dur_loo", "FB-WB", "progressive_carries_p90"): (
        0.407480, 0.4, "dna", True, False, (0.324, 0.385, 0.508, 0.413)),
    ("rho_trigger:poss_dur_loo", "FB-WB", "long_pass_completion_pct"): (
        0.417085, 0.4, "dna", True, True, (0.587, 0.430, 0.296, 0.355)),
    ("rho_trigger:poss_pass_loo", "FB-WB", "long_pass_completion_pct"): (
        0.416738, 0.4, "dna", True, True, (0.568, 0.433, 0.295, 0.370)),
    # AM-Winger pass_completion_pct, duration proxy: this proxy alone does not trigger, but the
    # cell's outcome (shared with the pass share row) is display + team_style_biased.
    ("rho_trigger:poss_dur_loo", "AM-Winger", "pass_completion_pct"): (
        0.383490, 0.4, "display", True, False, (0.321, 0.295, 0.654, 0.264)),
    ("rho_trigger:poss_pass_loo", "AM-Winger", "pass_completion_pct"): (
        0.410877, 0.4, "display", True, False, (0.323, 0.412, 0.646, 0.263)),
    ("rho_trigger:poss_pass_loo", "AM-Winger", "np_xg_p90"): (
        0.410867, 0.4, "display", True, False, (0.323, 0.435, 0.401, 0.484)),
    ("rho_trigger:poss_pass_loo", "AM-Winger", "np_shots_on_target_p90"): (
        0.389822, 0.4, "dna", False, False, (0.412, 0.306, 0.364, 0.477)),
    ("rho_trigger:poss_pass_loo", "ST", "np_shots_on_target_p90"): (
        0.395111, 0.4, "display", True, False, (0.449, 0.467, 0.126, 0.538)),

    # M4g2 O3: V, the 3rd-largest same-sign |ρ| (ADR 004 §13.6).
    ("majority_trigger:poss_dur_loo", "FB-WB", "progressive_carries_p90"): (
        0.385216, 0.4, "dna", True, False, (0.324, 0.385, 0.508, 0.413)),
    ("majority_trigger:poss_dur_loo", "ST", "np_shots_on_target_p90"): (
        0.407722, 0.4, "display", True, False, (0.447, 0.408, 0.139, 0.512)),
    ("majority_trigger:poss_pass_loo", "CB", "long_pass_completion_pct"): (
        0.417317, 0.4, "dna", True, False, (0.364, 0.750, 0.504, 0.417)),
    ("majority_trigger:poss_pass_loo", "FB-WB", "progressive_carries_p90"): (
        0.419963, 0.4, "dna", True, False, (0.376, 0.460, 0.503, 0.420)),
    ("majority_trigger:poss_pass_loo", "DM-CM", "progressive_passes_p90"): (
        0.412802, 0.4, "dna", True, False, (0.413, 0.541, 0.396, 0.541)),
    ("majority_trigger:poss_pass_loo", "AM-Winger", "np_xg_p90"): (
        0.401357, 0.4, "display", True, False, (0.323, 0.435, 0.401, 0.484)),
    ("majority_trigger:poss_pass_loo", "ST", "np_shots_p90"): (
        0.381793, 0.4, "dna", False, False, (0.585, 0.523, -0.002, 0.382)),

    ("allow_bound", "CB", "progressive_carries_p90"): (
        0.548655, 0.55, "dna", True, False, (0.459, 0.620, 0.561, 0.556)),

    ("lower_reliability", "CB", "box_entries_pass_p90"): (0.707266, 0.7, "dna", False, False, None),
    ("lower_reliability", "CB", "dribbles_attempted_p90"): (0.696195, 0.7, "dna", False, True, None),
    ("lower_reliability", "CB", "fouls_won_p90"): (0.688642, 0.7, "dna", False, True, None),
    ("lower_reliability", "CB", "tackles_attempted_p90"): (0.714502, 0.7, "dna", False, False, None),
    ("lower_reliability", "FB-WB", "box_entries_pass_p90"): (0.705739, 0.7, "dna", False, False, None),
    ("lower_reliability", "FB-WB", "op_key_passes_p90"): (0.683752, 0.7, "dna", False, True, None),
    ("lower_reliability", "DM-CM", "dribbled_past_p90"): (0.690658, 0.7, "dna", False, True, None),
    ("lower_reliability", "DM-CM", "np_xg_p90"): (0.702235, 0.7, "dna", False, False, None),
    ("lower_reliability", "AM-Winger", "long_pass_completion_pct"): (
        0.704960, 0.7, "dna", False, False, None),
    ("lower_reliability", "AM-Winger", "np_shots_on_target_p90"): (
        0.710162, 0.7, "dna", False, False, None),
    ("lower_reliability", "ST", "box_entries_pass_p90"): (0.719059, 0.7, "dna", False, False, None),
}


class TestBoundaryReport:
    """ADR 004 §13.6 (supersedes §10.10's status and note rows): the boundary report holds
    exactly these rows (no zero-share, ratio-r, coverage or D12b value is within +-0.02 of its
    threshold, and no majority_sign row), each at the ADR's value/threshold/outcome."""

    def test_boundary_report_has_exactly_the_adr_rows(self):
        # The D9 rows (added 2026-10-05) are checked in TestD9BoundaryRows.
        actual_keys = {(b.rule, b.group, b.column) for b in R.REGISTRY.boundary if b.rule != "d9_band"}
        assert actual_keys == set(BOUNDARY_SPEC)

    @pytest.mark.parametrize("key", list(BOUNDARY_SPEC))
    def test_boundary_row_matches_adr(self, key):
        by_key = {(b.rule, b.group, b.column): b for b in R.REGISTRY.boundary}
        b = by_key[key]
        value, threshold, status, tsb, low_rel, per_league = BOUNDARY_SPEC[key]
        assert math.isclose(b.value, value, abs_tol=5e-7), (key, b.value, value)
        assert b.threshold == threshold
        assert b.status == status
        assert b.team_style_biased is tsb
        assert b.low_reliability is low_rel
        if per_league is None:
            assert b.per_league is None
        else:
            assert b.per_league is not None
            assert len(b.per_league) == len(per_league)
            for got, want in zip(b.per_league, per_league):
                assert math.isclose(got, want, abs_tol=5e-4), (key, b.per_league, per_league)


# ── §10.3: fixed display, U1, D12b team_pass_share ───────────────────────────────────

FIXED_DISPLAY_COLUMNS = (
    "xg_p90", "shots_p90", "xg_per_shot", "shots_on_target_p90",
    "xg_assisted_direct_p90",
    "dispossessed_p90", "miscontrols_p90", "switches_p90",
    "passes_attempted_p90", "passes_completed_p90", "carries_p90",
    "crosses_attempted_p90",
    "key_passes_p90", "xg_assisted_p90",
    "goals_p90", "assists_p90", "op_assists_p90", "restart_assists_p90",
    "restart_xg_assisted_p90",
    "dribble_success_pct", "tackle_success_pct", "cross_completion_pct",
)


class TestFixedDisplay:
    """ADR 004 §10.3's fixed-display table: every listed column is display, reason
    fixed_display, in every group it applies to."""

    @pytest.mark.parametrize("column", FIXED_DISPLAY_COLUMNS)
    def test_fixed_display_in_every_group(self, column):
        for group in GROUPS:
            cell = R.cell(column, group)
            assert cell.status == "display", f"{column} x {group} is not display"
            assert "fixed_display" in cell.reasons, f"{column} x {group}: {cell.reasons}"

    def test_am_winger_op_xg_assisted_is_display(self):
        # U1 (§10.2) made it display for team style (ρ 0.419 on pass share). Since M4g2 (§13.6)
        # its multi-split r 0.555 is below 0.6, so it is display before the possession step.
        cell = R.cell("op_xg_assisted_p90", "AM-Winger")
        assert cell.status == "display"
        assert cell.reasons == ("unreliable",)
        assert cell.team_style_biased is False

    def test_team_pass_share_dna_only_in_dm_cm(self):
        # D12b / R10 (§10.3 step 6): DM-CM enters, CB and the rest are display.
        assert R.cell("team_pass_share", "DM-CM").status == "dna"
        for group in ("CB", "FB-WB", "AM-Winger", "ST"):
            assert R.cell("team_pass_share", group).status == "display"


# ── §10.7: families and directions ───────────────────────────────────────────────────

FAMILY_DIRECTION: dict[str, tuple[str, str]] = {
    "pass_completion_pct": ("Passing", "higher_better"),
    "long_pass_completion_pct": ("Passing", "higher_better"),
    "long_passes_attempted_p90": ("Passing", "style"),
    "team_pass_share": ("Passing", "style"),
    "passes_attempted_p90": ("Passing", "style"),
    "passes_completed_p90": ("Passing", "style"),
    "switches_p90": ("Passing", "style"),
    "progressive_passes_p90": ("Progression", "higher_better"),
    "final_third_entries_pass_p90": ("Progression", "higher_better"),
    "box_entries_pass_p90": ("Progression", "higher_better"),
    "progressive_carries_p90": ("Carrying", "higher_better"),
    "final_third_entries_carry_p90": ("Carrying", "higher_better"),
    "box_entries_carry_p90": ("Carrying", "higher_better"),
    "dribbles_attempted_p90": ("Carrying", "style"),
    "dribbles_completed_p90": ("Carrying", "higher_better"),
    "dribble_success_pct": ("Carrying", "higher_better"),
    "carries_p90": ("Carrying", "style"),
    "fouls_won_p90": ("Carrying", "style"),
    "op_key_passes_p90": ("Creation", "higher_better"),
    "op_xg_assisted_p90": ("Creation", "higher_better"),
    "key_passes_p90": ("Creation", "higher_better"),
    "xg_assisted_p90": ("Creation", "higher_better"),
    "xg_assisted_direct_p90": ("Creation", "higher_better"),
    "crosses_attempted_p90": ("Creation", "style"),
    "cross_completion_pct": ("Creation", "higher_better"),
    "op_assists_p90": ("Creation", "higher_better"),
    "assists_p90": ("Creation", "higher_better"),
    "restart_key_passes_p90": ("Creation", "higher_better"),
    "restart_xg_assisted_p90": ("Creation", "higher_better"),
    "restart_assists_p90": ("Creation", "higher_better"),
    "np_shots_p90": ("Goal threat", "higher_better"),
    "np_xg_p90": ("Goal threat", "higher_better"),
    "np_shots_on_target_p90": ("Goal threat", "higher_better"),
    "shots_on_target_p90": ("Goal threat", "higher_better"),
    "np_xg_per_shot": ("Goal threat", "higher_better"),
    "shots_p90": ("Goal threat", "higher_better"),
    "xg_p90": ("Goal threat", "higher_better"),
    "xg_per_shot": ("Goal threat", "higher_better"),
    "goals_p90": ("Goal threat", "higher_better"),
    "tackles_attempted_p90": ("Defending", "style"),
    "tackles_won_p90": ("Defending", "higher_better"),
    "tackle_success_pct": ("Defending", "higher_better"),
    "defensive_duel_success_pct": ("Defending", "higher_better"),  # ADR 004 §13.2 (M4g2)
    "interceptions_p90": ("Defending", "style"),  # ADR 004 §12 (user, 2026-10-06)
    "ball_recoveries_p90": ("Defending", "higher_better"),
    "pressures_p90": ("Defending", "style"),
    "clearances_p90": ("Defending", "style"),
    "blocks_p90": ("Defending", "style"),
    "dribbled_past_p90": ("Defending", "style"),  # ADR 004 §13.2 (M4g2)
    "fouls_committed_p90": ("Defending", "style"),
    "aerials_won_p90": ("Aerial", "higher_better"),
    "aerial_win_pct": ("Aerial", "higher_better"),
    "aerials_lost_p90": ("Aerial", "style"),
    "dispossessed_p90": ("Ball security", "lower_better"),
    "miscontrols_p90": ("Ball security", "lower_better"),
}


class TestFamilyAndDirection:
    """ADR 004 §10.7 (and §13.2 for dribbled_past and defensive_duel_success_pct): every
    metric's family and direction, and every spec's applies_to excludes GK (D21)."""

    def test_every_adr_metric_has_the_adr_family_and_direction(self):
        specs_by_col = {s.column: s for s in R.SPECS}
        for column, (family, direction) in FAMILY_DIRECTION.items():
            spec = specs_by_col.get(column)
            assert spec is not None, f"{column} is not a registered spec"
            assert spec.family == family, f"{column}: expected family {family}, got {spec.family}"
            assert spec.direction == direction, (
                f"{column}: expected direction {direction}, got {spec.direction}")

    def test_adr_table_covers_every_spec(self):
        assert {s.column for s in R.SPECS} == set(FAMILY_DIRECTION)

    def test_no_spec_applies_to_gk(self):
        for spec in R.SPECS:
            assert "GK" not in spec.applies_to, f"{spec.column} applies_to includes GK"

    def test_interceptions_style_note_reaches_the_cell_in_every_group(self):
        # ADR 004 §12.3: the note documenting why interceptions is style, not higher_better,
        # must reach Cell.notes in every group, including CB and ST where the cell is
        # display (not DNA) for unrelated reasons (r_full below the DNA bar) — notes are
        # attached from the spec before the status is known.
        for group in GROUPS:
            cell = R.cell("interceptions_p90", group)
            assert "team-style influenced (league means differ in every group, D9)" in cell.notes, (
                group, cell.notes)


# ── §10.2: integer_count ──────────────────────────────────────────────────────────────

XG_COUNT_P90_COLUMNS = (
    "xg_p90", "np_xg_p90", "xg_assisted_p90", "xg_assisted_direct_p90",
    "op_xg_assisted_p90", "restart_xg_assisted_p90",
)


class TestIntegerCount:
    """ADR 004 §10.2: integer_count is False exactly for the xG per-90 count columns
    (no signature count minimum, because the 'count' is an xG sum); every other
    per-90 is an integer count."""

    def test_xg_sums_are_not_integer_counts(self):
        for column in XG_COUNT_P90_COLUMNS:
            spec = next(s for s in R.SPECS if s.column == column)
            assert spec.integer_count is False, column

    def test_every_other_per90_is_an_integer_count(self):
        for spec in R.SPECS:
            if spec.unit != "per90" or spec.column in XG_COUNT_P90_COLUMNS:
                continue
            assert spec.integer_count is True, spec.column


# ── §10.1 / §10.3: source and the single override ────────────────────────────────────

class TestSourceAndOverrides:
    def test_measurement_source_is_statsbomb(self):
        assert R.MEASUREMENT_SOURCE == "statsbomb"

    def test_exactly_one_override(self):
        assert len(R.OVERRIDES) == 1
        o = R.OVERRIDES[0]
        assert (o.column, o.group) == ("restart_key_passes_p90", "AM-Winger")


# ── §10.10: D9 rows (user, 2026-10-05) ───────────────────────────────────────────────

class TestD9BoundaryRows:
    """§10.10: DNA cells whose D9 margin (the deciding league mean's signed distance to the
    [40, 60] band edge) is within 3 pts, the sampling noise of a league mean of percentiles
    (≈ 29/√80 ≈ 3.2 pts). 51 of 101 DNA cells: 33 global_ok, 18 not."""

    def _rows(self):
        return [b for b in R.REGISTRY.boundary if b.rule == "d9_band"]

    def test_counts(self):
        rows = self._rows()
        assert len(rows) == 51
        assert sum(R.cell(b.column, b.group).global_ok is True for b in rows) == 33
        assert sum(R.cell(b.column, b.group).global_ok is False for b in rows) == 18
        assert all(b.status == "dna" for b in rows)

    def test_dm_cm_team_pass_share(self):
        b = next(b for b in self._rows() if (b.group, b.column) == ("DM-CM", "team_pass_share"))
        assert math.isclose(b.value, 42.503657, abs_tol=5e-7)   # Serie A
        assert b.threshold == 40.0
        assert tuple(round(v, 2) for v in b.per_league) == (50.43, 56.11, 42.50, 52.75)
        assert math.isclose(R.cell("team_pass_share", "DM-CM").d9_margin, 2.503657, abs_tol=5e-7)

    def test_a_cell_failing_far_in_one_league_is_not_listed(self):
        # CB clearances: PL 73.5 decides D9 (margin −13.5), although La Liga is 41.5.
        assert ("CB", "clearances_p90") not in {(b.group, b.column) for b in self._rows()}


# ── §13.6: majority-triggered cells the allow-list admits (M4g2 O1) ──────────────────

# (group, column): ((dur leagues ≥ 0.4, dur max |ρ|, dur fires), (pass ..., ..., ...))
MAJORITY_ADMITTED_SPEC = {
    ("CB", "long_pass_completion_pct"): ((3, 0.715, True), (3, 0.750, True)),
    ("CB", "progressive_carries_p90"): ((3, 0.589, True), (4, 0.620, True)),
    ("CB", "progressive_passes_p90"): ((3, 0.509, True), (4, 0.558, True)),
    ("FB-WB", "progressive_carries_p90"): ((2, 0.508, False), (3, 0.503, True)),
    ("FB-WB", "progressive_passes_p90"): ((4, 0.646, True), (4, 0.642, True)),
    ("DM-CM", "long_pass_completion_pct"): ((3, 0.574, True), (3, 0.607, True)),
    ("DM-CM", "progressive_carries_p90"): ((4, 0.632, True), (4, 0.637, True)),
    ("DM-CM", "progressive_passes_p90"): ((3, 0.544, True), (3, 0.541, True)),
}


class TestMajorityAdmitted:
    """ADR 004 §13.6 (stats-reviewer condition, M4g2 O1): derive() returns exactly these 8
    cells on statsbomb_27, each with its per-proxy league count, max league |ρ| and outcome."""

    def test_set_is_the_adr_list(self):
        got = {(a.group, a.column) for a in R.REGISTRY.majority_admitted}
        assert got == set(MAJORITY_ADMITTED_SPEC)

    @pytest.mark.parametrize("key", list(MAJORITY_ADMITTED_SPEC))
    def test_detail_matches_adr(self, key):
        a = {(a.group, a.column): a for a in R.REGISTRY.majority_admitted}[key]
        cell = R.cell(key[1], key[0])
        assert cell.status == "dna" and cell.team_style_biased
        assert cell.reasons == ("majority_triggered", "allow_listed", "dna")
        for proxy, (n, top, fires) in zip(a.proxies, MAJORITY_ADMITTED_SPEC[key]):
            assert proxy.leagues_at_trigger == n
            assert math.isclose(proxy.max_single_league, top, abs_tol=5e-4)
            assert proxy.fires is fires
