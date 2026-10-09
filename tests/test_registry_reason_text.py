"""
Plan 006 pre-W1 gate G2: human-readable registry reason codes (texts approved by the user,
2026-10-09; decisions 5, 6, 10).
- every reason code the registry uses has a text (fixed_display keeps its spec's ADR text);
- an unknown code raises; the override form renders its ADR;
- no text contains a digit (no untraced numbers on the pages) or a causal claim;
- the approved wording is pinned as literals (the user's spec as reworded by the stats-reviewer
  under decision 10; not read from the code).
"""
from __future__ import annotations

import html
import re

import pytest

from eleveniq.analytics import registry as R

APPROVED = {
    "dna": "in the DNA profile",
    "signature": "signature trait",
    "d17_dropped": "left out: ranks players almost the same as another DNA metric",
    "zero_heavy": "many players record none, so it is not ranked as a profile metric",
    "unreliable": "not stable enough between two halves of a player's matches to profile a player",
    "low_coverage": "too few players reach its attempt minimum",
    "no_signal": "differences between players could not be told apart from chance",
    "possession_untestable": "its link to team possession could not be tested",
    # W1b: "at or above the possession-check level", one wording site-wide (user, 2026-10-09)
    "team_style_biased": "associated with team possession at or above the possession-check level",
    "majority_triggered": "associated with team possession in most leagues",
    "allow_listed": "kept in the profile despite an association with team possession (recorded decision)",
    "d12b": "team pass share is judged by its own stricter possession check",
    "d12b_not_preregistered": "team pass share is only used for central midfielders and centre-backs",
}
# Causal claims (decision 10): the texts state measured associations, never causes.
CAUSAL = ("depends", "dependent", "driven by", "caused by", "because of", "due to", "results from",
          "is explained by", "determined by", "a product of")


def test_the_texts_are_the_approved_wording():
    assert R.REASON_TEXT == APPROVED


def test_every_reason_code_in_use_has_a_text():
    used = {r for c in R.REGISTRY.cells.values() for r in c.reasons}
    for code in used - {R.R_FIXED}:
        assert R.reason_text(code)


@pytest.mark.parametrize("code", ["", "made_up", "fixed_display", "display"])
def test_unknown_codes_raise(code):
    with pytest.raises(KeyError, match="no human text"):
        R.reason_text(code)


def test_override_renders_its_adr():
    assert R.reason_text("override: ADR 004 §8, §10.2 U2") == \
        "set by a recorded decision (ADR 004 §8, §10.2 U2)"


@pytest.mark.parametrize("text", list(APPROVED.values()) + [R.OVERRIDE_TEXT])
def test_no_digit_and_no_causal_claim(text):
    body = text.replace("{adr}", "")
    assert not re.search(r"\d", body), text
    for word in CAUSAL:
        assert word not in body.lower(), (text, word)


# ── rendering on the comparison page (G2 decision 5) ────────────────────────────────

from eleveniq.analytics.registry import REGISTRY, SPECS  # noqa: E402
from tests.test_compare_page import _player  # noqa: E402
from tools import compare_page as P  # noqa: E402


def _notes(metric: str, group: str) -> str:
    return P.metric_notes([_player(group)], metric)


def test_every_non_dna_cell_renders_without_error_and_without_raw_codes():
    """Cells mixing fixed_display with another reason, overrides and multi-reason cells all
    go through metric_notes; no stored code may leak onto the page."""
    codes = set(R.REASON_TEXT)
    for (metric, group), cell in REGISTRY.cells.items():
        if cell.status == "dna" or group == "GK":
            continue
        out = _notes(metric, group)
        plain = re.sub(r"<[^>]+>", "", out)
        parts = {x.strip() for x in plain.replace(":", ",").split(",")}
        assert not parts & codes, (metric, group)
        assert "override:" not in out, (metric, group)


def test_multi_reason_cell_joins_texts_in_stored_order():
    cell = REGISTRY.cell("box_entries_carry_p90", "CB")
    assert cell.reasons == (R.R_ZERO_HEAVY, R.R_UNRELIABLE)
    out = _notes("box_entries_carry_p90", "CB")
    expected = f"{R.REASON_TEXT[R.R_ZERO_HEAVY]}, {R.REASON_TEXT[R.R_UNRELIABLE]}"
    assert expected in html.unescape(out)          # page text is HTML-escaped ("player's")
    assert 'data-reg="box_entries_carry_p90|CB|reasons"' in out


def test_fixed_display_cell_keeps_the_spec_reason_not_a_code_text():
    spec = next(s for s in SPECS if s.column == "cross_completion_pct")
    cell = REGISTRY.cell("cross_completion_pct", "CB")
    assert cell.reasons[0] == R.R_FIXED and spec.fixed_display is not None
    out = _notes("cross_completion_pct", "CB")
    assert "fixed_display.reason" in out
    assert R.REASON_TEXT[R.R_NO_SIGNAL] not in out
