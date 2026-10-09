"""
M4g3c movers-display helpers (plan 005 M4g3c; ADR 004 §13.7; spikes/m4g_movers_display.py),
tested from their written rules with hand-computed expected values:
- month range: "Aug–Jan", one month when a stint starts and ends in the same month of the same
  year, with the years when it spans 12 months or more;
- competitions guard: the written-out COMPS must equal the season parquet's pairs;
- position words: "mostly <role>" for a fine role, "<role> / <role>" for a mixed stint (its two
  largest roles in the fixed role order), "goalkeeper" without an outfield role;
- weights: nominal-equivalent minutes over their sum; shown as whole percentages;
- changed most: the 3 largest |Δ local percentile|, ties by metric name;
- stint note: no dot here → the reason (PARTIAL competition first, another group's map with a
  link, an incomplete profile, "no profile (under 900 min)" on the nominal clock); a complete
  row of this group that is not plotted, or a pool stint meeting the gate (900 − 1e-6) without a
  DNA row, fails;
- clock detail: a stint shown with 900+ played minutes but under the gate gets its played and
  nominal minutes and the clock rule in Details; no other stint does;
- combined dot: the weighted average of the stint inputs projects to the weighted average of
  the stint scores (the dot lies on the segment).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g_common as cm  # noqa: E402
import m4g_movers_display as mv  # noqa: E402


# ── month range ──────────────────────────────────────────────────────────────

def test_month_range_across_new_year():
    assert mv.month_range(dt.date(2015, 8, 9), dt.date(2016, 1, 30)) == "Aug–Jan"


def test_month_range_single_month():
    assert mv.month_range(dt.date(2015, 10, 3), dt.date(2015, 10, 31)) == "Oct"


def test_month_range_same_month_different_year_shows_the_years():
    assert mv.month_range(dt.date(2015, 8, 1), dt.date(2016, 8, 1)) == "Aug 2015–Aug 2016"


def test_month_range_eleven_months_needs_no_year():
    assert mv.month_range(dt.date(2015, 8, 1), dt.date(2016, 7, 31)) == "Aug–Jul"


# ── competitions guard ───────────────────────────────────────────────────────

def test_check_comps_accepts_the_written_out_list():
    mv.check_comps(set(mv.COMPS))


def test_check_comps_fails_on_a_new_competition():
    with pytest.raises(SystemExit):
        mv.check_comps(set(mv.COMPS) | {(16, 4)})


def test_check_comps_fails_on_a_missing_competition():
    with pytest.raises(SystemExit):
        mv.check_comps(set(mv.COMPS) - {(9, 27)})


# ── position words ───────────────────────────────────────────────────────────

def test_position_words_single_role():
    assert mv.position_words({"label": "RW", "combo": None}) == "mostly right winger"


def test_position_words_mixed_two_roles_in_role_order():
    assert mv.position_words({"label": "mixed", "combo": "LW/RW"}) == "left winger / right winger"


def test_position_words_goalkeeper():
    assert mv.position_words(None) == "goalkeeper"


def test_position_words_cover_every_role():
    assert set(mv.POSITION_WORDS) == set(cm.ROLES)


# ── weights ──────────────────────────────────────────────────────────────────

def test_weights_are_minute_shares():
    assert mv.weights([600.0, 300.0]) == pytest.approx([2 / 3, 1 / 3])


def test_weights_reject_zero_minutes():
    with pytest.raises(SystemExit):
        mv.weights([0.0, 0.0])


def test_weight_texts_whole_percent():
    assert mv.weight_texts([0.535, 0.465]) == ["54%", "46%"]


# ── changed most ─────────────────────────────────────────────────────────────

def test_changed_most_largest_abs_change_ties_by_name():
    a = {"m_a": 10.0, "m_b": 50.0, "m_c": 90.0, "m_d": 40.0, "m_e": 60.0}
    b = {"m_a": 40.0, "m_b": 20.0, "m_c": 85.0, "m_d": 80.0, "m_e": 60.0}
    # |Δ|: a 30, b 30, c 5, d 40, e 0 → d, then a and b tied (by name)
    assert mv.changed_most(a, b, tuple(sorted(a))) == ["m_d", "m_a", "m_b"]


# ── stint note ───────────────────────────────────────────────────────────────

def _st(key, comp=2, minutes=500.0, nominal=480.0):
    return {"key": key, "comp": comp, "minutes": minutes, "nominal": nominal}


PAGES = {"CB": "cb.html", "DM-CM": "dm.html"}


def test_note_plotted_here_is_empty():
    k = (1, 10, 2, 27, "statsbomb")
    assert mv.stint_note(_st(k), "CB", {k}, {k: "CB"}, {k: "complete"}, PAGES) == ("", None)


def test_note_partial_competition_first():
    k = (1, 10, 9, 27, "statsbomb")
    assert mv.stint_note(_st(k, comp=9), "CB", set(), {}, {}, PAGES) == (mv.NOTE_PARTIAL, None)


def test_note_other_group_links_its_map():
    k = (1, 10, 2, 27, "statsbomb")
    assert mv.stint_note(_st(k, nominal=1000), "CB", set(), {k: "DM-CM"}, {k: "complete"},
                         PAGES) == ("also on the DM-CM map", "dm.html")


def test_note_incomplete_profile():
    k = (1, 10, 2, 27, "statsbomb")
    assert mv.stint_note(_st(k, nominal=1000), "CB", set(), {k: "CB"}, {k: "partial"},
                         PAGES) == (mv.NOTE_INCOMPLETE, None)


def test_note_under_gate():
    k = (1, 10, 2, 27, "statsbomb")
    assert mv.stint_note(_st(k, minutes=899.9, nominal=850), "CB", set(), {}, {},
                         PAGES) == (mv.NOTE_UNDER, None)


def test_note_under_gate_with_900_played_minutes_keeps_the_plain_note():
    k = (1, 10, 2, 27, "statsbomb")
    assert mv.stint_note(_st(k, minutes=913.0, nominal=862.0), "CB", set(), {}, {},
                         PAGES) == (mv.NOTE_UNDER, None)


def test_note_at_gate_within_tolerance_fails():
    # ADR 004 D1: nominal ≥ 900 − 1e-6 meets the gate, so such a pool stint must have a DNA row.
    k = (1, 10, 2, 27, "statsbomb")
    with pytest.raises(SystemExit):
        mv.stint_note(_st(k, nominal=900.0 - 5e-7), "CB", set(), {}, {}, PAGES)


# ── clock detail (user, 2026-10-08: explanation in Details, not in the line) ─

def test_clock_detail_only_for_900_played_under_the_gate():
    a = dict(_st((1, 10, 2, 27, "statsbomb"), minutes=937.8, nominal=893.6), team="Troyes")
    b = dict(_st((1, 11, 2, 27, "statsbomb"), minutes=500.0, nominal=480.0), team="Lille")
    text = mv.clock_detail([a, b], [mv.NOTE_UNDER, mv.NOTE_UNDER])
    assert ">938<" in text and ">893.6<" in text and "Lille" not in text
    assert mv.CLOCK_RULE.replace("'", "&#x27;") in text


def test_clock_detail_empty_when_no_stint_needs_it():
    b = dict(_st((1, 11, 2, 27, "statsbomb"), minutes=500.0, nominal=480.0), team="Lille")
    assert mv.clock_detail([b], [mv.NOTE_UNDER]) == ""


def test_note_complete_row_not_plotted_fails():
    k = (1, 10, 2, 27, "statsbomb")
    with pytest.raises(SystemExit):
        mv.stint_note(_st(k, nominal=1000), "CB", set(), {k: "CB"}, {k: "complete"}, PAGES)


def test_note_gated_pool_stint_without_dna_row_fails():
    k = (1, 10, 2, 27, "statsbomb")
    with pytest.raises(SystemExit):
        mv.stint_note(_st(k, nominal=900.0), "CB", set(), {}, {}, PAGES)


# ── combined dot ─────────────────────────────────────────────────────────────

def test_combined_lies_on_the_segment():
    rng = np.random.default_rng(0)
    x = rng.random((4, 3))
    mean = x.mean(axis=0)
    comps = np.array([[0.6, 0.8, 0.0], [0.0, 0.0, 1.0]])
    sm = {"x": x, "scores": (x - mean) @ comps.T,
          "model": {"mean": mean.tolist(), "components": comps.tolist()}}
    stints = [{"key": "a", "nominal": 900.0, "name": "P"}, {"key": "b", "nominal": 300.0}]
    cb = mv.combined(sm, stints, {"a": 1, "b": 3})
    assert cb["weights"] == pytest.approx([0.75, 0.25])
    want = 0.75 * sm["scores"][1] + 0.25 * sm["scores"][3]
    assert cb["score"] == pytest.approx(want.tolist(), abs=1e-12)
