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

import datetime as dt
import math

from eleveniq.analytics import dna as D
from eleveniq.analytics import movers, style_map
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import NoteText
from eleveniq.analytics.roles import FineRole

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

# The same rule for a table legend (rows are metrics, not axes).
GREY_RULE_TABLE = ("style metrics, long pass completion, and metrics associated with team "
                   "possession at or above the possession-check level are grey")

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


# ── Percentile phrases (plan 005 M4g1, user 2026-10-07) ────────────────────────────────
# p is a direction-free local percentile (Hazen). p ≥ 90 → "Very high …, top X%"; 60 < p < 90 →
# "High …, top X%"; 40 ≤ p ≤ 60 → "Average …, around average"; 10 < p < 40 → "Low …, bottom
# Y%"; p ≤ 10 → "Very low …, bottom Y%"; X = ⌈100 − p⌉, Y = ⌈p⌉, both at least 1.
MID_LO, MID_HI = 40.0, 60.0


def pct_words(p: float) -> tuple[str, str]:
    """(adjective, phrase) for a local percentile."""
    if not 0 <= p <= 100:
        raise ValueError(f"percentile out of range: {p}")
    if MID_LO <= p <= MID_HI:
        return "Average", "around average"
    if p > MID_HI:
        return ("Very high" if p >= 90 else "High"), f"top {max(1, math.ceil(100 - p - 1e-9))}%"
    return ("Very low" if p <= 10 else "Low"), f"bottom {max(1, math.ceil(p - 1e-9))}%"


def in_sentence(label: str) -> str:
    """A label inside a sentence: first letter lowered unless it starts an acronym (xG)."""
    return label if label[1:2].isupper() else label[:1].lower() + label[1:]


# ── Groups and metric labels ───────────────────────────────────────────────────────────
GROUP_PLURAL = {"CB": "centre-backs", "FB-WB": "full-backs and wing-backs",
                "DM-CM": "central midfielders", "AM-Winger": "attacking midfielders and wingers",
                "ST": "strikers"}
# Every group's plural, the goalkeepers included (identity text of any row).
GROUP_TEXT = {**GROUP_PLURAL, "GK": "goalkeepers"}
FAMILY_LABELS = {"Progression": "Progressive passing", "Carrying": "Carrying & dribbling"}
ZERO_TIE = "tied at zero, not a rank"
SIGNATURE_ROW = "signature trait — see below"
NEAR_GATE_CHIP = "Fewer minutes: less reliable"
HOLDER, NON_HOLDER = "holder", "non-holder"
NOT_ASSESSED = "not assessed: {reason}"
NEIGHBOURS_LABEL = ("Statistically similar: DNA distance within position group (provisional "
                    "until the M5 similarity engine)")
ATTRIBUTION = "Data: StatsBomb Open Data"
# Player page and chips (plan 006 W3; DESIGN.md §6 chip set; the Profile shape note is the user's
# W4 leaning, checked by the stats-reviewer in the final text pass).
PROFILE_SHAPE_TITLE = "Profile shape"
PROFILE_SHAPE_NOTE = ("How strongly the player stands out in each family, compared with players "
                      "in the same position and league.")
CHIPS = {"withheld": "Withheld", "same_team": "Same team", "provisional": "Provisional",
         "style": "Style", "basis": "basis"}
COLUMNS = {"value": "Value", "local": "League percentile", "global": "All-leagues percentile",
           "minutes": "min"}
# The 50 ring of the Profile shape: the dimension score is a mean of direction-adjusted local
# percentiles, so 50 sits in pct_words' "around average" band (a test ties the two).
AROUND_AVERAGE = "around average"
NEIGHBOURS_TITLE = "Similar players"
MOST_ALIKE, MOST_DIFFERENT = "Most alike", "Most different"
SIGNATURE_TITLE = "Signature traits"
SIGNATURE_NOTE = ("A holder / non-holder flag, not a percentile: most players in the group record "
                  "none, so a rank would mostly compare zeros.")
PORTFOLIO = "A non-commercial portfolio project."


def family_label(family: str) -> str:
    return FAMILY_LABELS.get(family, family)


GROUP_ONE = {"CB": "centre-back", "FB-WB": "full-back or wing-back",
             "DM-CM": "central midfielder", "AM-Winger": "attacking midfielder or winger",
             "ST": "striker"}
# Restart deliveries include throw-ins (ADR 004 §10.6): no "set-piece" label.
_RESTARTS = "(corners, free kicks, throw-ins)"
METRIC_LABELS: dict[str, str] = {
    "restart_key_passes_p90": f"Restart key passes {_RESTARTS}",
    "restart_xg_assisted_p90": f"Restart xG assisted {_RESTARTS}",
    "restart_assists_p90": f"Restart assists {_RESTARTS}",
}
# Group-specific wording (user, 2026-10-07): for strikers, dribbled past is about pressing.
GROUP_LABELS: dict[tuple[str, str], str] = {
    ("ST", "dribbled_past_p90"): "Dribbled past while pressing",
}


_SPEC_BY_COLUMN = {s.column: s for s in R.SPECS}


def metric_label(column: str, group: str | None = None) -> str:
    """The display name of a metric (the registry's, with the overrides above)."""
    if group is not None and (group, column) in GROUP_LABELS:
        return GROUP_LABELS[(group, column)]
    if column in METRIC_LABELS:
        return METRIC_LABELS[column]
    return _SPEC_BY_COLUMN[column].display_name


# ── Roles (user, 2026-10-07; analytics/roles.py) ───────────────────────────────────────
ROLE_WORDS = {
    "CB": "Centre-back", "LB": "Left-back", "RB": "Right-back", "LWB": "Left wing-back",
    "RWB": "Right wing-back", "DM": "Deep midfielder", "CM": "Central midfielder",
    "AM": "Attacking midfielder", "LW": "Left winger or left midfielder",
    "RW": "Right winger or right midfielder", "ST": "Striker",
}
POSITION_WORDS = {"CB": "centre-back", "LB": "left-back", "RB": "right-back",
                  "LWB": "left wing-back", "RWB": "right wing-back", "DM": "deep midfielder",
                  "CM": "central midfielder", "AM": "attacking midfielder", "LW": "left winger",
                  "RW": "right winger", "ST": "striker"}
SLOT_WORDS = {"6": "6 (holding slot)", "8": "8 (central-midfield slot)", "mixed": "mixed"}
PIVOT_WORDS = {"single": "single pivot (one holding midfielder)",
               "double": "double pivot (two holding midfielders side by side)",
               "cm": "central-midfield slot", "mixed": "mixed"}
ROLE_RULE = ("A role is the lineup position where a player spent at least 70% of their "
             "minutes; otherwise the player is shown as mixed, with their two main roles.")
SLOT_RULE = ("Within central midfield, a 6 spent at least 70% of those minutes in a "
             "defensive-midfield slot, an 8 at least 70% in a central-midfield slot.")


def role_words(role: FineRole) -> str:
    """'Deep midfielder (DM)', or 'Mixed: Deep midfielder / Central midfielder (DM / CM)'."""
    if role.combo is None:
        return f"{ROLE_WORDS[role.label]} ({role.label})"
    a, b = role.combo
    return f"Mixed: {ROLE_WORDS[a]} / {ROLE_WORDS[b]} ({a} / {b})"


def position_words(role: FineRole | None) -> str:
    """'mostly right winger', 'left winger / right winger', or 'goalkeeper'."""
    if role is None:
        return "goalkeeper"
    if role.combo is None:
        return "mostly " + POSITION_WORDS[role.label]
    a, b = role.combo
    return f"{POSITION_WORDS[a]} / {POSITION_WORDS[b]}"


# ── Style-map axes (the user's reading of the loadings; never generated) ───────────────
AxisName = tuple[str, str] | None          # (positive end, negative end)
AXIS_NAMES: dict[str, tuple[AxisName, AxisName]] = {
    "AM-Winger": (("Creator", "Physical"), ("Shooter", "Distributor")),   # user, 2026-10-07
    "CB": (("Ball-player", "Stopper"), ("More aerials won", "Fewer aerials won")),
    "FB-WB": (("Attacking", "Conservative"), ("More aerials won", "Fewer aerials won")),
    "DM-CM": (("Creator", "Holder"), ("Box-arriving", "Deep playmaker")),
    "ST": (("Aerial target", "Technical forward"), ("Box striker", "Safe passer")),
}
_AERIALS_WON_NOTE = ("Almost entirely aerial duels won per 90 (a count, not a win rate). "
                     "Aerials lost left the {g} DNA in M4g2 (lower reliability), so the Aerial "
                     "family's weight now sits on this one metric.")
# Legend notes (user, 2026-10-07); the loadings they quote are checked on every fit
# (style_map.QUOTED_LOADINGS).
AXIS_NOTES: dict[tuple[str, int], str] = {
    ("CB", 1): _AERIALS_WON_NOTE.format(g="CB"),
    ("FB-WB", 1): _AERIALS_WON_NOTE.format(g="FB-WB"),
    ("ST", 0): ("\"Aerial target\" mostly measures how often the striker contests aerial duels "
                "(aerials won +0.30, lost +0.28), with aerial win rate weaker (+0.20). It is not "
                "headed shots or headed goals."),
}
MAP_CAPTION = "Position shows style, not quality"


def axis_title(name: tuple[str, str]) -> str:
    """An axis name in reading order (PC1 left → right, PC2 bottom → top): negative end first."""
    return f"{name[1]} ↔ {name[0]}"


def end_name(group: str, model: style_map.Model, axis: int, sign: int) -> str:
    """The user's name for an axis end, or a description by its metrics when unnamed."""
    names = AXIS_NAMES[group]
    if names[axis] is not None:
        return names[axis][0 if sign > 0 else 1]  # type: ignore[index]
    end = "+" if sign > 0 else "−"
    ms = style_map.end_metrics(model, axis, sign)
    if ms:
        return f"the PC{axis + 1} {end} end (" + ", ".join(
            metric_label(m, group) for m in ms[:2]) + ")"
    other = style_map.end_metrics(model, axis, -sign)
    return (f"the PC{axis + 1} {end} end (low "
            + ", ".join(metric_label(m, group) for m in other[:2]) + ")")


def lean(group: str, model: style_map.Model, score: float, axis: int) -> str:
    """Where a player sits on one axis, in words."""
    band = style_map.lean_band(score, model.sd[axis])
    names = AXIS_NAMES[group]
    if band == "near":
        name = names[axis]
        return (f"Near the middle of {axis_title(name)}" if name is not None
                else f"Near the middle of PC{axis + 1}")
    return f"Leans toward {end_name(group, model, axis, 1 if score > 0 else -1)} ({band})"


# ── Movers (plan 005 M4g3c) ────────────────────────────────────────────────────────────
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
STINT_NOTE: dict[str, str] = {
    movers.NOTE_PARTIAL: "partial coverage — minutes undercounted",
    movers.NOTE_INCOMPLETE: "profile incomplete (not plotted)",
    movers.NOTE_UNDER_GATE: "no profile (under 900 min)",
    movers.NOTE_GOALKEEPER: "goalkeeper: no outfield profile",
    movers.NOTE_OTHER_GROUP: "also on the {group} map",
}
SEASON_DOT_LABEL = "Season profile, weighted by minutes (display only)"
CLOCK_RULE = ("The 900-minute gate counts full-match equivalents: each match's minutes are "
              "scaled to a 90-minute match (120 with extra time), so stoppage time does not add "
              "to them.")
WEIGHT_NOTE = ("Weights use nominal-equivalent minutes, the clock of the 900-minute gate. The "
               "season dot is display only: it is not part of the map's fit, the neighbour "
               "lists, the percentiles or the ranks.")


def month_range(first: dt.date, last: dt.date) -> str:
    """'Aug–Jan'; one month when first and last share a month; with the years when the stint
    spans 12 months or more."""
    if (first.year, first.month) == (last.year, last.month):
        return MONTHS[first.month - 1]
    if (last.year - first.year) * 12 + last.month - first.month >= 12:
        return f"{MONTHS[first.month - 1]} {first.year}–{MONTHS[last.month - 1]} {last.year}"
    return f"{MONTHS[first.month - 1]}–{MONTHS[last.month - 1]}"


def stint_note(code: str | None, group: str | None) -> str:
    """The text of a movers.stint_note code ("" for a stint with a dot)."""
    if code is None:
        return ""
    try:
        text = STINT_NOTE[code]
    except KeyError:
        raise KeyError(f"no text for stint note {code!r}") from None
    if code == movers.NOTE_OTHER_GROUP:
        if group is None:
            raise ValueError("other_group note without a group")
        return text.format(group=group)
    return text


# ── The Defending note (ADR 004 §14 N2, N3 as narrowed to the mover-test groups) ─────────
MOVER_TEST = "player-vs-team test on club movers"
MOVER_VERB = {R.MOVER_STABILITY_FAILED: "has failed",
              R.MOVER_STABILITY_INCONCLUSIVE: "is inconclusive",
              R.MOVER_SEED_SENSITIVE: "is not stable across seeds",
              R.MOVER_BELOW_MODEL_RANGE: "is outside the model's range"}
DEFENDING_CLAUSE = {
    R.POSSESSION_CRITERIA_FAILED: f"these metrics are {WITHHELD_TEXT[R.POSSESSION_CRITERIA_FAILED]}",
    **{c: f"the {MOVER_TEST} {v}" for c, v in MOVER_VERB.items()},
    R.MOVER_STABILITY_UNTESTABLE: f"there are {WITHHELD_TEXT[R.MOVER_STABILITY_UNTESTABLE]}",
}
NOT_A_FINDING = "This is not a finding about the player."
EXPECTED_STATE = ("That is the expected state until a second season adds evidence, not a finding "
                  "about the player.")


def defending_note(group: str, derivation: R.Derivation | None = None) -> str | None:
    """The sentence shown with a withheld Defending score (N2; N3's second-season clause only
    for the mover-test groups). None when Defending is scored."""
    fs = (derivation or R.REGISTRY).defending_status(group)
    if fs.status == R.SCORE_OK:
        return None
    under = " (underpowered)" if any(n.startswith("underpowered") for n in fs.notes) else ""
    closing = NOT_A_FINDING if fs.status == R.POSSESSION_CRITERIA_FAILED else EXPECTED_STATE
    return f"Defending scores are withheld: {DEFENDING_CLAUSE[fs.status]}{under}. {closing}"
