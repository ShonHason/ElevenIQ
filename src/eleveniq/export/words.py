"""
The words table (plan 006 W1; CLAUDE.md Design: user-facing text comes only from here and
registry.REASON_TEXT / registry.NOTE_TEXT).

Every page text that states something about a metric, a status or a withheld score lives here,
so the comparison page, the web export and the spike checks read one source. Unknown codes
raise. Texts carry no causal claim; the "influenced" wordings were replaced (user decision C,
corrected by the stats-reviewer and approved by the user, 2026-10-09):
- long pass completion has no good / poor direction; its note gives the evidence;
- a withheld family score names its reason; Passing's reason is that it rests on long pass
  completion alone (ST keeps its Passing score: it has a second member);
- a tag on a metric of a withheld family speaks about the family score, never the metric.

`short` texts are for table cells, `full` texts for their popovers (DESIGN decision 7); the
stats-reviewer approved both.
"""
from __future__ import annotations

from eleveniq.analytics import dna as D
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import NoteText

# The direction label of long pass completion (ADR 004 §11.5): no good / poor reading.
NO_READING = "no good/poor reading"

LONG_PASS_NOTE = NoteText(
    "not read as good or poor passing",
    "associated with team possession for centre-backs, full-backs and central midfielders, and "
    "not shown to follow the player across clubs (club-mover test inconclusive, underpowered); "
    "it is not read as good or poor individual passing")

# Which axes and metrics are grey (tools/compare_page.uncoloured: style, long pass completion,
# or the registry's team_style_biased flag).
GREY_RULE = ("style axes, long pass completion, and axes associated with team possession at or "
             "above the possession-check level are grey")

PASSING_REASON = "rests on long pass completion alone"

# Plain literals (the spike tests read this table as source text; spikes never import eleveniq).
# A withheld or unscored dimension score (dna.WITHHELD_STATUSES and no_directional_metric) as
# text, read "withheld (<text>)". The Defending D5 texts keep ADR 004 §14 N2 wording.
WITHHELD_TEXT: dict[str, str] = {
    D.PENDING_XPASS_DEFERRED: ("rests on long pass completion alone, which is not shown to "
                               "follow the player across clubs; xPass deferred until after launch"),
    R.POSSESSION_CRITERIA_FAILED: "not shown to be free of team-possession effects",
    R.MOVER_STABILITY_FAILED: "player-vs-team test on club movers failed",
    R.MOVER_STABILITY_INCONCLUSIVE: "player-vs-team test on club movers inconclusive",
    R.MOVER_STABILITY_UNTESTABLE: "too few club movers to test player vs team",
    R.MOVER_SEED_SENSITIVE: "player-vs-team test on club movers not stable across seeds",
    R.MOVER_BELOW_MODEL_RANGE: "player-vs-team test on club movers outside the model's range",
    D.NO_DIRECTIONAL_METRIC: "no directional metric present in this row",
}
# Cell labels for the long ones (the full text goes in the popover).
WITHHELD_SHORT: dict[str, str] = {D.PENDING_XPASS_DEFERRED: PASSING_REASON}

# The reason in a metric's tag when its family score is withheld: "(<Family> score withheld:
# <reason>)". It describes the family score, not the metric on the line.
TAG_REASON: dict[str, str] = {
    D.PENDING_XPASS_DEFERRED: PASSING_REASON,
    **{s: t for s, t in WITHHELD_TEXT.items()
       if s not in (D.PENDING_XPASS_DEFERRED, D.NO_DIRECTIONAL_METRIC)},
}


def withheld_text(status: str) -> NoteText:
    """(short, full) text of a withheld or unscored dimension score. Raises KeyError on an
    unknown status."""
    try:
        full = WITHHELD_TEXT[status]
    except KeyError:
        raise KeyError(f"no text for dimension status {status!r}") from None
    return NoteText(WITHHELD_SHORT.get(status, full), full)


def withheld_tag(family: str, status: str) -> str:
    """The tag after a metric of a withheld family: "(Passing score withheld: …)"."""
    try:
        reason = TAG_REASON[status]
    except KeyError:
        raise KeyError(f"no tag reason for withheld status {status!r}") from None
    return f"({family} score withheld: {reason})"
