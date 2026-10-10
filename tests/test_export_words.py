"""
The words table (plan 006 W1; export/words.py and registry.NOTE_TEXT).

The "influenced" relabels (user decision C as corrected by the stats-reviewer, 2026-10-09; one
term site-wide, "associated with team possession"; short cell labels with the full text for the
popover, both approved by the stats-reviewer) are pinned as literals: the user's wording, never
read from the code under test.
"""
from __future__ import annotations

import pytest

from eleveniq.analytics import dna as D
from eleveniq.analytics import registry as R
from eleveniq.export import words as W

NOTES = {  # stored note → (short, full)
    "team-style influenced": (
        "associated with team possession",
        "associated with team possession at or above the possession-check level; kept in the "
        "profile by a recorded decision"),
    "team-style influenced (league means differ in every group, D9)": (
        "no cross-league rank in any group",
        "league averages differ too much in every position group to rank across leagues, so it "
        "is ranked within each league"),
}
LONG_PASS = ("not read as good or poor passing",
             "associated with team possession for centre-backs, full-backs and central "
             "midfielders, and not shown to follow the player across clubs (club-mover test "
             "inconclusive, underpowered); it is not read as good or poor individual passing")
PASSING = ("rests on long pass completion alone",
           "rests on long pass completion alone, which is not shown to follow the player across "
           "clubs; xPass deferred until after launch")
GREY = ("style axes, long pass completion, and axes associated with team possession at or above "
        "the possession-check level are grey")
CAUSAL = ("influenced", "depends", "driven by", "caused by", "because of", "due to",
          "results from", "determined by", "a product of", "reflects the team")


def test_relabelled_notes_are_the_approved_wording():
    for note, (short, full) in NOTES.items():
        assert R.note_text(note) == R.NoteText(short, full)


def test_long_pass_texts_and_direction_label():
    assert (W.LONG_PASS_NOTE.short, W.LONG_PASS_NOTE.full) == LONG_PASS
    assert W.NO_READING == "no good/poor reading"
    assert W.GREY_RULE == GREY


def test_passing_withheld_text_and_tag():
    t = W.withheld_text(D.PENDING_XPASS_DEFERRED)
    assert (t.short, t.full) == PASSING
    assert W.withheld_tag("Passing", D.PENDING_XPASS_DEFERRED) == (
        "(Passing score withheld: rests on long pass completion alone)")


def test_defending_tag_keeps_the_n2_wording():
    assert W.withheld_tag("Defending", R.POSSESSION_CRITERIA_FAILED) == (
        "(Defending score withheld: not shown to be free of team-possession effects)")


def test_every_stored_note_has_page_text():
    used = {n for c in R.REGISTRY.cells.values() for n in c.notes}
    used |= {n for s in R.SPECS for n in s.notes}
    for note in used:
        assert R.note_text(note).full


@pytest.mark.parametrize("bad", ["", "made up note"])
def test_unknown_note_raises(bad):
    with pytest.raises(KeyError, match="no page text"):
        R.note_text(bad)


def test_unknown_status_raises():
    with pytest.raises(KeyError, match="no text for dimension status"):
        W.withheld_text("made_up")
    with pytest.raises(KeyError, match="no tag reason"):
        W.withheld_tag("Passing", D.NO_DIRECTIONAL_METRIC)


def test_every_withheld_status_has_text_and_a_tag():
    for s in D.WITHHELD_STATUSES:
        assert W.withheld_text(s).full and W.withheld_tag("X", s)


def _all_texts() -> list[str]:
    out = [W.NO_READING, W.GREY_RULE, *W.LONG_PASS_NOTE.__dict__.values(),
           *W.WITHHELD_TEXT.values(), *W.WITHHELD_SHORT.values(), *W.TAG_REASON.values()]
    for t in R.NOTE_TEXT.values():
        out += [t.short, t.full]
    return out


@pytest.mark.parametrize("text", _all_texts())
def test_no_causal_claim(text):
    for word in CAUSAL:
        assert word not in text.lower(), (text, word)


def test_short_never_longer_than_full():
    for t in [*R.NOTE_TEXT.values(), W.LONG_PASS_NOTE,
              *(W.withheld_text(s) for s in W.WITHHELD_TEXT)]:
        assert len(t.short) <= len(t.full)


@pytest.mark.parametrize("group", ["CB", "FB-WB", "DM-CM", "AM-Winger", "ST"])
def test_rendered_page_has_no_causal_word_and_shows_relabelled_notes(group):
    """Spec W1a: the page renders notes through note_text, so no stored 'influenced' string
    reaches a page (legend, radar text, metric notes), for every position group."""
    from tests.test_compare_page import _pair, _text
    from tools import compare_page as P
    page = _text(P.render(_pair(group)))
    assert "influenced" not in page.lower()
    assert GREY in page


# ── W1b: the ported page words (plan 005 M4g1 / M4g3c rules) ─────────────────────────

import datetime as dt  # noqa: E402

from eleveniq.analytics import movers as M  # noqa: E402
from eleveniq.analytics import style_map as S  # noqa: E402
from eleveniq.analytics.roles import FineRole  # noqa: E402


@pytest.mark.parametrize("p, want", [
    (90.0, ("Very high", "top 10%")), (89.9, ("High", "top 11%")), (60.0, ("Average", "around average")),
    (40.0, ("Average", "around average")), (39.9, ("Low", "bottom 40%")), (10.0, ("Very low", "bottom 10%")),
    (99.9, ("Very high", "top 1%")), (0.2, ("Very low", "bottom 1%")), (60.5, ("High", "top 40%"))])
def test_pct_words(p, want):
    assert W.pct_words(p) == want


def test_pct_words_rejects_out_of_range():
    with pytest.raises(ValueError):
        W.pct_words(100.5)


@pytest.mark.parametrize("a, b, want", [
    (dt.date(2015, 8, 9), dt.date(2015, 8, 30), "Aug"),
    (dt.date(2015, 8, 9), dt.date(2016, 1, 3), "Aug–Jan"),
    (dt.date(2015, 8, 9), dt.date(2016, 8, 3), "Aug 2015–Aug 2016")])
def test_month_range(a, b, want):
    assert W.month_range(a, b) == want


def test_role_and_position_words():
    one = FineRole("RW", None, (("RW", 0.8),))
    mixed = FineRole("mixed", ("DM", "CM"), (("DM", 0.5), ("CM", 0.5)))
    assert W.role_words(one) == "Right winger or right midfielder (RW)"
    assert W.role_words(mixed) == "Mixed: Deep midfielder / Central midfielder (DM / CM)"
    assert W.position_words(one) == "mostly right winger"
    assert W.position_words(mixed) == "deep midfielder / central midfielder"
    assert W.position_words(None) == "goalkeeper"


def test_metric_labels_with_group_wording():
    assert W.metric_label("dribbled_past_p90", "ST") == "Dribbled past while pressing"
    assert W.metric_label("dribbled_past_p90", "CB") == next(s.display_name for s in R.SPECS if s.column == "dribbled_past_p90")
    assert W.metric_label("restart_assists_p90") == "Restart assists (corners, free kicks, throw-ins)"


def test_lean_words():
    m = S.Model("DM-CM", ("a",), (1.0,), (0.0,), ((1.0,), (1.0,)), (0.5, 0.2), (2.0, 2.0))
    assert W.lean("DM-CM", m, 0.5, 0) == "Near the middle of Holder ↔ Creator"
    assert W.lean("DM-CM", m, 1.0, 0) == "Leans toward Creator (slight)"
    assert W.lean("DM-CM", m, -3.0, 1) == "Leans toward Deep playmaker (strong)"


def test_stint_notes_text():
    assert W.stint_note(None, None) == ""
    assert W.stint_note(M.NOTE_OTHER_GROUP, "ST") == "also on the ST map"
    assert W.stint_note(M.NOTE_UNDER_GATE, None) == "no profile (under 900 min)"
    with pytest.raises(KeyError):
        W.stint_note("made_up", None)


# The N2 / N3 sentences are the user's spec (ADR 004 §14): literals.
N2 = "not shown to be free of team-possession effects"


@pytest.mark.parametrize("group", ["CB", "FB-WB", "AM-Winger", "ST"])
def test_defending_note_possession_groups(group):
    assert W.defending_note(group) == (f"Defending scores are withheld: these metrics are {N2}. "
                                       "This is not a finding about the player.")


def test_defending_note_dm_cm():
    assert W.defending_note("DM-CM") == (
        "Defending scores are withheld: the player-vs-team test on club movers is inconclusive "
        "(underpowered). That is the expected state until a second season adds evidence, not a "
        "finding about the player.")


def test_rules_text_uses_neutral_pronouns():
    import re
    for t in (W.ROLE_RULE, W.SLOT_RULE, W.WEIGHT_NOTE, W.CLOCK_RULE, *W.AXIS_NOTES.values()):
        assert not re.search(r"\b(he|his|him|himself)\b", t, re.IGNORECASE), t


def test_month_range_years_start_at_twelve_months_and_pct_words_just_outside_middle():
    assert W.month_range(dt.date(2015, 8, 9), dt.date(2016, 7, 30)) == "Aug–Jul"
    assert W.pct_words(60.0001)[0] == "High" and W.pct_words(39.9999)[0] == "Low"
    assert W.pct_words(10.0001) == ("Low", "bottom 11%")


def test_end_name_describes_an_unnamed_axis_by_metrics(monkeypatch):
    m = S.Model("DM-CM", ("np_xg_p90", "np_shots_p90"), (0.5, 0.5), (0.0, 0.0), ((0.6, -0.1), (0.0, 0.0)),
                (0.5, 0.2), (1.0, 1.0))
    monkeypatch.setitem(W.AXIS_NAMES, "DM-CM", (None, None))
    assert W.end_name("DM-CM", m, 0, 1).startswith("the PC1 + end (")
    assert W.end_name("DM-CM", m, 0, -1).startswith("the PC1 − end (low ")   # no loading reaches 0.15


# ── W1a backlog (code-reviewer): claims tied to their measurements; spike parity ──────

def test_long_pass_note_claims_hold_in_the_registry():
    """LONG_PASS_NOTE.full names the groups where long pass completion is associated with team
    possession at or above the possession-check level (the registry's team_style_biased flag)
    and says the club-mover test is inconclusive and underpowered: a measurement change that
    breaks either claim fails here."""
    groups = {g for (m, g), c in R.REGISTRY.cells.items()
              if m == "long_pass_completion_pct" and c.team_style_biased}
    assert groups == {"CB", "FB-WB", "DM-CM"}
    assert "centre-backs, full-backs and central midfielders" in W.LONG_PASS_NOTE.full
    v = R.REGISTRY.movers["long_pass_completion_pct"]
    assert (v.verdict, v.underpowered) == ("inconclusive", True)
    assert "inconclusive, underpowered" in W.LONG_PASS_NOTE.full


def test_spike_tag_words_equal_the_words_table(monkeypatch):
    import importlib
    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "spikes"))
    cmpm = sys.modules.get("m4g_compare") or importlib.import_module("m4g_compare")
    assert cmpm.TAG_WORDS == W.TAG_REASON


def test_table_grey_rule_is_the_approved_wording():
    assert W.GREY_RULE_TABLE == ("style metrics, long pass completion, and metrics associated with "
                                 "team possession at or above the possession-check level are grey")


# ── Pre-registered (plan 006 W0, user condition 11): the near-gate chip ─────────────────

@pytest.mark.parametrize("group", ["CB", "FB-WB", "DM-CM", "AM-Winger", "ST", "GK"])
@pytest.mark.parametrize("minutes", [899.0, 899.9999995, 900.0, 1100.0, 1349.99, 1350.0, 2500.0])
def test_near_gate_chip_only_cb_fbwb_900_1350(group, minutes):
    """The "Fewer minutes: less reliable" flag appears only for CB and FB-WB profiles with
    900 ≤ nominal minutes < 1,350 (the gate's 1e-6 tolerance included), where it was measured
    (ADR 004 §14 sign-off note 3). Literal bounds: the user's spec."""
    from eleveniq.export import display as X
    want = group in ("CB", "FB-WB") and 900.0 - 1e-6 <= minutes < 1350.0
    assert X.near_gate(group, minutes) is want


def test_w3_player_page_words_are_the_approved_wording():
    """Plan 006 W3 (user's W4 leaning for the note; final stats-reviewer text pass at the end)."""
    assert W.PROFILE_SHAPE_TITLE == "Profile shape"
    assert W.PROFILE_SHAPE_NOTE == ("How strongly the player stands out in each family, compared "
                                    "with players in the same position and league.")
    assert W.CHIPS["withheld"] == "Withheld" and W.CHIPS["same_team"] == "Same team"
    assert W.COLUMNS == {"value": "Value", "local": "League percentile",
                         "global": "All-leagues percentile", "minutes": "min"}
    for t in (W.PROFILE_SHAPE_NOTE, W.SIGNATURE_NOTE):
        for word in CAUSAL:
            assert word not in t.lower()
        assert "quality" not in t.lower() and "style" not in t.lower()     # user, W4 leaning 2


def test_around_average_is_the_percentile_band_at_50():
    assert W.AROUND_AVERAGE == "around average" == W.pct_words(50.0)[1]
