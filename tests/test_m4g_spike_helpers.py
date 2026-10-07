"""
M4g1 spike helpers (plan 005 M4g1; ADR 004 §13.4; docs/reviews/m4g-fine-roles.md), tested from
their written rules:
- fine role: the role with ≥ 0.7 of a row's minutes, else "mixed" with its two largest roles
  (ties by the fixed role order); GK minutes dropped; the rest must equal minutes_played;
- registry labels: read from registry.py as source text; unsupported forms fail;
- percentile wording: ≥ 90 "top X%" (X = ⌈100 − p⌉, at least 1); ≥ 50 above average;
  > 10 below average; ≤ 10 "bottom X%" (X = max(1, ⌈p⌉));
- the M4g1 reading: Cohen's d with the pooled SD; the overlap coefficient on shared bins;
  lift = (purity − b) / (1 − b); the pre-registered reading (lift ≥ 0.4 for both labels and
  |d| ≥ 1 on PC1 or PC2); the within-team contrast.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import m4g_common as cm  # noqa: E402
import m4g_style_map as sm  # noqa: E402
import m4g_style_page as page  # noqa: E402


# ── Fine role ────────────────────────────────────────────────────────────────

def _role(pos: dict[str, float], played: float | None = None) -> dict:
    played = sum(v for p, v in pos.items() if p != "Goalkeeper") if played is None else played
    return cm.fine_role(Counter(pos), played, ("key",))


def test_exactly_seventy_percent_is_labelled():
    r = _role({"Left Back": 700.0, "Right Back": 300.0})
    assert r["label"] == "LB" and r["combo"] is None


def test_just_under_seventy_percent_is_mixed_with_its_two_main_roles():
    r = _role({"Left Back": 699.0, "Right Back": 300.0, "Left Wing Back": 1.0})
    assert r["label"] == "mixed" and r["combo"] == "LB/RB"


def test_positions_of_one_role_add_up():
    # Center Defensive Midfield + Left Defensive Midfield are both DM: 80% DM.
    r = _role({"Center Defensive Midfield": 500.0, "Left Defensive Midfield": 300.0,
               "Center Midfield": 200.0})
    assert r["label"] == "DM" and r["share"]["DM"] == pytest.approx(0.8)


def test_combo_is_in_role_order_and_ties_break_by_role_order():
    r = _role({"Right Wing": 500.0, "Left Wing": 500.0})
    assert r["label"] == "mixed" and r["combo"] == "LW/RW"
    assert [t[0] for t in r["top"]] == ["LW", "RW"]


def test_goalkeeper_minutes_are_dropped_and_exempt_from_reconciliation():
    r = _role({"Goalkeeper": 50.0, "Center Back": 900.0}, played=950.0)
    assert r["label"] == "CB" and r["share"] == {"CB": 1.0}


def test_minutes_that_do_not_reconcile_fail():
    with pytest.raises(SystemExit):
        _role({"Center Back": 900.0}, played=901.0)


def test_no_outfield_minutes_fail():
    with pytest.raises(SystemExit):
        _role({"Goalkeeper": 90.0}, played=90.0)


def test_an_unmapped_position_fails():
    with pytest.raises(SystemExit):
        _role({"Sweeper": 900.0})


def test_role_words():
    assert cm.role_words({"label": "DM"}) == "Deep midfielder (DM)"
    assert cm.role_words({"label": "mixed", "combo": "DM/CM"}) == (
        "Mixed: Deep midfielder / Central midfielder (DM / CM)")


# ── Registry labels ──────────────────────────────────────────────────────────

def test_registry_labels_cover_every_dna_metric():
    labels = cm.registry_labels()
    dna = {m for ms in sm.ref.DNA.values() for m in ms}
    assert dna <= set(labels)
    assert labels["progressive_passes_p90"] == "Progressive passes"
    assert labels["op_key_passes_p90"] == "Key passes (non-restart delivery)"   # f-string form


_HEAD = 'CONST = "x"\n'


@pytest.mark.parametrize("body", [
    'SPECS: tuple = (_p90("a", label_fn(), "F", "style"),)\n',        # a call as the label
    'SPECS: tuple = (_p90("a", f"{1 + 1}", "F", "style"),)\n',         # an expression in an f-string
    'SPECS: tuple = (other("a", "A"),)\n',                             # unknown constructor
    'SPECS: tuple = (_p90("a", "A", "F", "style"), _p90("a", "B", "F", "style"))\n',  # twice
    'SPECS = [_p90("a", "A", "F", "style")]\n',                        # not the annotated tuple
])
def test_unsupported_registry_forms_fail(tmp_path, body):
    p = tmp_path / "registry.py"
    p.write_text(_HEAD + body)
    with pytest.raises(SystemExit):
        cm.registry_labels(p)


def test_registry_fstring_over_a_module_constant(tmp_path):
    p = tmp_path / "registry.py"
    p.write_text(_HEAD + 'SPECS: tuple = (_ratio("r", f"Rate ({CONST})", "F", None),\n'
                 '    MetricSpec(column="c", display_name="C"),)\n')
    assert cm.registry_labels(p) == {"r": "Rate (x)", "c": "C"}


# ── Percentile wording ───────────────────────────────────────────────────────

@pytest.mark.parametrize("p, want", [
    (99.9, ("Very high", "top 1%")), (96.3, ("Very high", "top 4%")),
    (90.0, ("Very high", "top 10%")), (89.99, ("High", "above average")),
    (50.0, ("High", "above average")), (49.99, ("Low", "below average")),
    (10.01, ("Low", "below average")), (10.0, ("Very low", "bottom 10%")),
    (3.2, ("Very low", "bottom 4%")), (0.4, ("Very low", "bottom 1%")),
])
def test_pct_words(p, want):
    assert page.pct_words(p) == want


def test_axis_title_reads_like_the_map():
    # Stored (positive, negative); shown negative ↔ positive: left → right, bottom → top.
    assert page.axis_title(("Creator", "Physical")) == "Physical ↔ Creator"


# ── The M4g1 reading ─────────────────────────────────────────────────────────

def test_cohen_d_uses_the_pooled_sd():
    a, b = [1.0, 2.0, 3.0], [3.0, 4.0, 5.0, 6.0]
    pooled = math.sqrt((2 * 1.0 + 3 * (5 / 3)) / 5)            # SDs² 1 and 5/3
    assert sm.cohen_d(a, b) == pytest.approx((2.0 - 4.5) / pooled)
    assert sm.cohen_d(b, a) == pytest.approx(-sm.cohen_d(a, b))


def test_overlap_coefficient():
    assert sm.overlap([0.1, 0.2], [0.1, 0.2], 0.0, 1.0) == pytest.approx(1.0)
    assert sm.overlap([0.01, 0.02], [0.98, 0.99], 0.0, 1.0) == 0.0
    # The top of the range falls in the last bin, not past it.
    assert sm.overlap([1.0], [0.99], 0.0, 1.0) == pytest.approx(1.0)


def _fixture():
    """Six rows: three 6s, two 8s, one mixed; scores separate the labels on PC1."""
    keys = [(i, 10 if i < 3 else 20, 11, 27, "statsbomb") for i in range(6)]
    rows = [{"key": k} for k in keys]
    labels = ["6", "6", "6", "8", "8", "mixed"]
    share = [0.9, 0.8, 0.95, 0.1, 0.25, 0.6]
    lab = {k: {"label": lbl, "share6": s} for k, lbl, s in zip(keys, labels, share)}
    scores = np.array([[-2.0, 0.0], [-1.0, 1.0], [-1.5, -1.0], [2.0, 0.0], [1.0, 0.5],
                       [0.0, 0.0]])
    return rows, lab, {"rows": rows, "scores": scores}


def test_lift_from_purity_and_base_rate():
    rows, lab, smap = _fixture()
    # Each 6's neighbours: two 6s and one 8 → purity 2/3 for every 6 (K is 10 in the spike;
    # purity is a share, so a 3-neighbour fixture with K patched is equivalent).
    nb = {r["key"]: [] for r in rows}
    six = [r for r in rows if lab[r["key"]]["label"] == "6"]
    eight = [r for r in rows if lab[r["key"]]["label"] == "8"]
    for r in six:
        nb[r["key"]] = [x for x in six if x is not r] + [eight[0]]
    for r in eight:
        nb[r["key"]] = [six[0], six[1], rows[5]]
    nb[rows[5]["key"]] = [six[0], eight[0], six[1]]
    old = sm.K
    sm.K = 3
    try:
        fig = sm.label_figures(smap, rows, lab, "label", ("6", "8", "mixed"), nb)
    finally:
        sm.K = old
    six_f = fig["labels"]["6"]
    assert six_f["b"] == pytest.approx(0.5) and six_f["purity"] == pytest.approx(2 / 3)
    assert six_f["lift"] == pytest.approx((2 / 3 - 0.5) / 0.5)
    assert fig["labels"]["8"]["purity"] == 0.0
    assert fig["labels"]["8"]["lift"] == pytest.approx((0 - 1 / 3) / (2 / 3))
    assert fig["pairs"][("6", "8")]["d"][0] < 0


def test_reading_needs_both_lifts_and_one_large_d():
    def fig(l6, l8, d):
        return {"labels": {"6": {"lift": l6}, "8": {"lift": l8}}, "pairs": {("6", "8"): {"d": d}}}
    assert sm.reading(fig(0.4, 0.4, [1.0, 0.0]), "6", "8")["separate"]
    assert sm.reading(fig(0.4, 0.4, [0.0, -1.0]), "6", "8")["separate"]
    assert not sm.reading(fig(0.39, 0.9, [3.0, 3.0]), "6", "8")["separate"]
    assert not sm.reading(fig(0.9, 0.9, [0.99, -0.99]), "6", "8")["separate"]
    assert not sm.reading(fig(None, 0.9, [3.0, 3.0]), "6", "8")["separate"]


def test_within_team_contrast_counts_teammate_pairs_on_the_6_side():
    rows, lab, smap = _fixture()
    wt = sm.within_team(smap, lab)
    # Team 10: rows 0-2, share gaps ≤ 0.15: no pair. Team 20: rows 3-5, gaps (3, 4) 0.15,
    # (3, 5) 0.5, (4, 5) 0.35: only (3, 5) reaches 0.4.
    assert wt["pairs"] == 1
    # 6 side on PC1 is negative (6 mean −1.5 vs 8 mean 1.5); row 5 is more 6
    # (0.6) than row 3 (0.1) and sits at 0.0 < 2.0, so it is on the 6 side.
    assert wt["agree"][0] == 1
