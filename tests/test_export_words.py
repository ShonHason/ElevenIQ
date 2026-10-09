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
    used |= {n for s in R.SPECS for n in (getattr(s, "notes", ()) or ())}
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
