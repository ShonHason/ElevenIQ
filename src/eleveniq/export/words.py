"""
The words table (plan 006 W1; CLAUDE.md Design: user-facing text comes only from here and
registry.REASON_TEXT / registry.NOTE_TEXT).

Every page text that states something about a metric, a status or a withheld score lives here,
so the comparison page, the web export and the spike checks read one source. Unknown codes
raise. Texts carry no causal claim; the "influenced" wordings were replaced (user decision C,
corrected by the stats-reviewer and approved by the user, 2026-10-09):
- long pass completion has no good / poor direction; its note gives the evidence;
- a withheld family score names its reason (the DNA's Passing reason: it rests on long pass
  completion alone); the page shows the display-only Passing score instead (user, 2026-10-10);
- a tag on a metric of a withheld family speaks about the family score, never the metric.

`short` texts are for table cells, `full` texts for their popovers (DESIGN decision 7); the
stats-reviewer approved both.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

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
# The page's Passing status where completion above expected failed the pre-registered possession
# check (plan 006): a display status, not a DNA one, so it stays out of the tables above (the
# spikes mirror those).
PASS_MIX_FAILED = "pass_mix_possession_failed"
PASS_MIX_FAILED_TEXT = NoteText(
    "associated with team possession",
    "completion vs expected still rises with the team's share of the ball for this position "
    "(checked before launch), so no Passing score is shown")

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
    if status == PASS_MIX_FAILED:
        return PASS_MIX_FAILED_TEXT
    try:
        full = WITHHELD_TEXT[status]
    except KeyError:
        raise KeyError(f"no text for dimension status {status!r}") from None
    return NoteText(WITHHELD_SHORT.get(status, full), full)


def withheld_tag(family: str, status: str) -> str:
    """The tag after a metric of a withheld family: "(Passing score withheld: …)"."""
    try:
        reason = PASS_MIX_FAILED_TEXT.short if status == PASS_MIX_FAILED else TAG_REASON[status]
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


# Style metrics have no good or bad end (W3 polish, user): a neutral phrase, not top / bottom N%.
STYLE_HIGHER, STYLE_LOWER = "higher than most", "lower than most"


def style_words(p: float) -> str:
    """The phrase for a style metric's percentile: around average in the 40–60 band (as
    pct_words), else higher / lower than most."""
    if not 0 <= p <= 100:
        raise ValueError(f"percentile out of range: {p}")
    if MID_LO <= p <= MID_HI:
        return "around average"
    return STYLE_HIGHER if p > MID_HI else STYLE_LOWER


POSSESSION_SHORT = "associated with team possession"


def reason_note(code: str) -> NoteText:
    """A display reason as a note: the team-possession reasons share the short text."""
    full = R.reason_text(code)
    short = POSSESSION_SHORT if code in (R.R_TEAM_STYLE, R.R_MAJORITY) else full
    return NoteText(short, full)


def merge_notes(items: list[NoteText]) -> list[NoteText]:
    """Each short text once, in first-seen order; the full texts of one short text join (each
    once) for the popover."""
    fulls: dict[str, list[str]] = {}
    for n in items:
        lst = fulls.setdefault(n.short, [])
        if n.full not in lst:
            lst.append(n.full)
    return [NoteText(short, "; ".join(f)) for short, f in fulls.items()]


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
# W3 polish (user, 2026-10-10): no internal jargon on the page ("DNA distance", milestones).
NEIGHBOURS_LABEL = ("Players whose profiles are most like this one, in the same position "
                    "(provisional)")
ATTRIBUTION = "Data: StatsBomb Open Data"
# Player page and chips (plan 006 W3; DESIGN.md §6 chip set; the Profile shape note is the user's
# W4 leaning, checked by the stats-reviewer in the final text pass).
PROFILE_SHAPE_TITLE = "Profile shape"
PROFILE_SHAPE_NOTE = ("Each family's score: the average of the player's league percentiles for "
                      "that family's scored metrics, against players in the same position and "
                      "league (50 is around average).")
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
BASIS_LEGEND = "The bold percentile is the one the player's profile uses for that metric."
# What stands out (W3 fix round, user 2026-10-10): the 6 league percentiles furthest from 50.
STAND_OUT_TITLE = "What stands out"
STAND_OUT_NOTE = ("The six metrics where the player is furthest from the league average for the "
                  "position, high or low. Lower-is-better metrics are counted from their better "
                  "end; metrics with a hollow bar have no better end. Close variants of a metric "
                  "already listed are left out.")
STAND_OUT_N = 6
# The compare-mode defensive rate (user-approved label, 2026-10-10): a plain rate, no
# percentile, never "adjusted" or "corrected".
RATE_LABEL = "per 100 opponent open-play passes (while on the pitch)"
# Compare mode (W4; user decisions 2026-10-09: same group only, no distance, top 6 by gap).
COMPARE_TOP_TITLE = "Where they differ most"
COMPARE_TOP_NOTE = ("The six metrics with the largest gap between the two players' league "
                    "percentiles. Long pass completion, signature traits and metrics associated "
                    "with team possession are left out, so the clearest team differences are not "
                    "headlined as player differences.")
COMPARE_ALL_TITLE = "All metrics"
COMPARE_SAME_GROUP = "Only players in the same position group can be compared."
RATE_METRICS = ("tackles_attempted", "interceptions", "ball_recoveries", "pressures", "blocks",
                "clearances")
LOWER_BETTER = "lower better"
BASIS_MARKER = "used for the profile"      # screen-reader text of the bold (basis) percentile
SIGNATURE_NOTE = ("A holder / non-holder flag, not a percentile: many players in the group record "
                  "none, so a rank would compare many tied values.")
PORTFOLIO = "A non-commercial portfolio project."


def family_label(family: str) -> str:
    return FAMILY_LABELS.get(family, family)


GROUP_ONE = {"CB": "centre-back", "FB-WB": "full-back or wing-back",
             "DM-CM": "central midfielder", "AM-Winger": "attacking midfielder or winger",
             "ST": "striker"}
# Restart deliveries include throw-ins (ADR 004 §10.6): no "set-piece" label.
_RESTARTS = "(corners, free kicks, throw-ins and other restarts)"
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


# ── Display round (user, 2026-10-10): percentile bands, units, definitions ─────────────
# A fixed band on the shown (direction-adjusted) league percentile replaces the number in every
# cell; the number moves to the popover. Lower bounds, compared with the shown 6-decimal value
# (the tolerance of the other display rules). Style metrics keep their neutral phrase.
# The band reads the shown whole number (band(); final text pass), so it agrees with the popover.
BANDS: tuple[tuple[float, str], ...] = (
    (95.0, "Top 5%"), (90.0, "Top 10%"), (75.0, "Top 25%"),
    (60.0, "Above average"), (40.0, "Around average"), (25.0, "Below average"),
    (10.0, "Bottom 25%"), (5.0, "Bottom 10%"), (0.0, "Bottom 5%"),
)
# The single highest / lowest row of its population (user, 2026-10-10: "Top 1%" dropped; league
# groups hold 35–84 players, so Top 1% meant the highest anyway and strikers could never get it).
HIGHEST, LOWEST = "Highest in league", "Lowest in league"
STYLE_BANDS = {"higher than most": "Higher than most", "around average": "Around average",
               "lower than most": "Lower than most"}


def shown_integer(p: float) -> int:
    """The whole number a percentile is shown as (display.format_pct): 0 and 100 stay, interior
    values round half up into 1–99."""
    if not 0 <= p <= 100:
        raise ValueError(f"percentile out of range: {p}")
    if p in (0, 100):
        return int(p)
    return min(99, max(1, math.floor(p + 0.5)))


def band(shown: float, style: bool, n: int | None = None, flipped: bool = False) -> str:
    """The band label of a shown percentile (style metrics: the neutral phrase), read from the
    whole number the popover shows (stats-reviewer, final text pass), except at the ends: with
    the population size n, the single highest row (Hazen (n − 0.5) / n, or 100 for a row
    ranked against a population it is not in) is HIGHEST and the single lowest LOWEST (user,
    2026-10-10), whatever the metric's reading. Highest / lowest name the raw value: for a
    lower-better metric (`flipped`, shown as 100 − p) the top shown row has the lowest value."""
    whole = shown_integer(shown)
    if n and 0 < shown < 100:          # ranked members only: an outsider row (0 / 100) is not
        eps = 1e-6                     # the league group's highest- or lowest-ranked player
        top, bottom = (LOWEST, HIGHEST) if flipped else (HIGHEST, LOWEST)
        if shown >= 100.0 * (n - 0.5) / n - eps:
            return top
        if shown <= 100.0 * 0.5 / n + eps:
            return bottom
    if style:
        return STYLE_BANDS[style_words(whole)]
    return next(label for lo, label in BANDS if whole >= lo)


def ordinal_percentile(pct_text: str) -> str:
    """"97th percentile", or "above the 99th percentile" / "below the 1st percentile" for a row
    ranked against a population it is not in (pct_text ">99" / "<1")."""
    if pct_text == ">99":
        return "above the 99th percentile"
    if pct_text == "<1":
        return "below the 1st percentile"
    n = int(pct_text)
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} percentile"


PCT_NOTE_LOCAL = "{ordinal} among {league} {group}"
PCT_NOTE_GLOBAL = "{ordinal} among {group} in all leagues"
LOWER_BETTER_NOTE = ("lower is better, so it is counted from the better end: 90 means the "
                     "player's rate is lower than about 90% of the group's")

# Units of a value, from the metric definition (MetricSpec.unit; the xG ratios are per shot).
UNIT_TEXT = {"per90": "per 90", "share": "", "ratio": ""}
PER_SHOT = frozenset({"np_xg_per_shot", "xg_per_shot"})
TOTAL_WORD = "total"
EXACT_HINT = "show the exact percentile"
SUM_COLUMNS = frozenset({"xg", "np_xg", "xg_assisted", "xg_assisted_direct", "op_xg_assisted",
                         "restart_xg_assisted"})        # sums, shown to 2 decimals     # screen-reader hint on a band's popover trigger
SUMMARY_LABELS = {"appearances": "appearances", "minutes_played": "minutes", "goals": "goals",
                  "assists": "assists", "xg": "xG", "shots": "shots"}
SUMMARY_SINGULAR = {"appearances": "appearance", "minutes_played": "minute", "goals": "goal",
                    "assists": "assist", "shots": "shot"}


def summary_label(key: str, value: float) -> str:
    """The label of a summary count, singular for exactly one (1 goal, 3 goals; xG unchanged)."""
    return SUMMARY_SINGULAR.get(key, SUMMARY_LABELS[key]) if value == 1 else SUMMARY_LABELS[key]


def unit_text(column: str, unit: str) -> str:
    """The unit shown next to a value ('%' is already part of a percentage value's text)."""
    if column in PER_SHOT:
        return "per shot"
    return UNIT_TEXT[unit]


# One plain sentence per metric, written from the implementation (src/eleveniq/metrics/ and
# pipeline/build_player_match._aggregate_events; thresholds in StatsBomb pitch units, the
# pitch being 120 × 80). Per-90 values are the season count / minutes played × 90.
# Restarts are corners, free kicks, goal kicks, kick-offs and throw-ins. Checked by the
# stats-reviewer against the code.
_RESTART = "corners, free kicks, goal kicks, kick-offs and throw-ins"
METRIC_DEFINITIONS: dict[str, str] = {
    "pass_completion_pct": "Completed passes as a share of all passes, leaving out passes with an unknown outcome and injury clearances.",
    "long_pass_completion_pct": "Completed long passes (30 units or more, restarts excluded) as a share of long passes, leaving out unknown outcomes and injury clearances.",
    "long_passes_attempted_p90": f"Passes of 30 pitch units or more, excluding {_RESTART}.",
    "team_pass_share": "The player's open-play passes as a share of all the team's open-play passes while the player was on the pitch.",
    "passes_attempted_p90": "Every pass the player attempted, completed or not, restarts included.",
    "passes_completed_p90": "Passes StatsBomb records as completed (no failed or unknown outcome).",
    "switches_p90": "Passes StatsBomb marks as a switch of play, restarts included.",
    "progressive_passes_p90": f"Completed passes, not from {_RESTART}, starting outside the team's own 40% of the pitch, that move the ball at least 10 units closer to the opponent's goal or into the penalty area from outside.",
    "final_third_entries_pass_p90": f"Completed passes, not from {_RESTART}, that start before the final third and end inside it.",
    "box_entries_pass_p90": f"Completed passes, not from {_RESTART}, that start outside the opponent's penalty area and end inside it.",
    "progressive_carries_p90": "Carries that move the ball at least 5 units closer to the opponent's goal and cover at least 5 units.",
    "final_third_entries_carry_p90": "Carries that start before the final third and end inside it.",
    "box_entries_carry_p90": "Carries that start outside the opponent's penalty area and end inside it.",
    "dribbles_attempted_p90": "Take-ons (StatsBomb dribbles) the player attempted.",
    "dribbles_completed_p90": "Take-ons (StatsBomb dribbles) recorded as complete.",
    "dribble_success_pct": "Completed take-ons as a share of take-ons attempted.",
    "carries_p90": "Every carry: the player moving with the ball under control.",
    "fouls_won_p90": "Fouls committed against the player.",
    "op_key_passes_p90": "Passes that led directly to a shot or assisted a goal, from open play (not from a restart).",
    "op_xg_assisted_p90": "The xG of the shots the player's open-play passes set up (not from a restart).",
    "key_passes_p90": "Passes that led directly to a shot or assisted a goal, restarts included.",
    "xg_assisted_p90": "The xG of the shots the player's passes set up, restarts included.",
    "xg_assisted_direct_p90": "The xG of the shots the player's passes set up, leaving out shots where the shooter attempted a take-on, or carried the ball more than 10 units in total, after receiving it, or where that could not be checked.",
    "crosses_attempted_p90": "Passes StatsBomb marks as crosses, restarts included.",
    "cross_completion_pct": "Completed crosses as a share of crosses, leaving out crosses with an unknown outcome.",
    "op_assists_p90": "Passes, from open play (not from a restart), that StatsBomb records as a goal assist.",
    "assists_p90": "Passes that StatsBomb records as a goal assist, restarts included.",
    "restart_key_passes_p90": "Passes from corners, free kicks, goal kicks, kick-offs and throw-ins that led directly to a shot or assisted a goal.",
    "restart_xg_assisted_p90": "The xG of the shots set up by the player's passes from corners, free kicks, goal kicks, kick-offs and throw-ins.",
    "restart_assists_p90": "Goal assists from corners, free kicks, goal kicks, kick-offs and throw-ins.",
    "np_shots_p90": "Shots, leaving out penalties.",
    "np_xg_p90": "The StatsBomb xG of the player's shots, leaving out penalties.",
    "np_shots_on_target_p90": "Shots that scored or that the goalkeeper saved on target (including saves pushed onto the post); shots that hit the woodwork unsaved are not counted. Penalties left out.",
    "shots_on_target_p90": "Shots that scored or that the goalkeeper saved on target (including saves pushed onto the post); shots that hit the woodwork unsaved are not counted. Penalties included.",
    "np_xg_per_shot": "The average StatsBomb xG of the player's non-penalty shots.",
    "shots_p90": "Every shot, penalties included.",
    "xg_p90": "The StatsBomb xG of the player's shots, penalties included.",
    "xg_per_shot": "The average StatsBomb xG of the player's shots, penalties included.",
    "goals_p90": "Goals the player scored, penalties included (penalty shoot-outs excluded).",
    "tackles_attempted_p90": "Tackles the player attempted (StatsBomb tackle duels), won or lost.",
    "tackles_won_p90": "Tackles recorded as won, including those where the ball went out of play.",
    "tackle_success_pct": "Tackles won as a share of tackles attempted.",
    "defensive_duel_success_pct": "Tackles won as a share of tackles attempted plus times the player was dribbled past.",
    "interceptions_p90": "Times the player intercepted the ball.",
    "ball_recoveries_p90": "Loose balls the player recovered, leaving out failed recoveries.",
    "pressures_p90": "Times the player pressed an opponent who had the ball.",
    "clearances_p90": "Clearances the player made.",
    "blocks_p90": "Shots or passes the player blocked, leaving out blocks by the attacking side and goalkeeper save-blocks.",
    "dribbled_past_p90": "Times an opponent's take-on went past the player.",
    "fouls_committed_p90": "Fouls the player committed.",
    "aerials_won_p90": "Aerial duels the player won.",
    "aerial_win_pct": "Aerial duels won as a share of aerial duels won and lost.",
    "aerials_lost_p90": "Aerial duels the player lost.",
    "dispossessed_p90": "Times the player lost the ball to an opponent's challenge.",
    "miscontrols_p90": "Times the player lost the ball through a bad touch.",
}


# ── Hollow bars and family definitions (user, 2026-10-10) ──────────────────────────────
# One legend line for the hollow (uncoloured) bar; its cells read the neutral words
# (higher than most / around average / lower than most), never top / bottom N%.
HOLLOW_LEGEND = ("Hollow bar: not read as good or poor (style, long pass completion, or "
                 "associated with team possession).")
# Compare mode: percentiles of two players from different leagues (user, 2026-10-10).
COMPARE_LEAGUE_NOTE = "Each percentile ranks the player within their own league and position."

# What each Profile shape family score measures, per position group: the plain meaning of its
# scored members (the family's DNA metrics with a good / poor direction; style metrics are not
# scored). `members` must equal the exporter's scored members for that group and family, or the
# export fails, so a text cannot outlive a registry change. Withheld families show their reason
# instead. Statistical text: in the final stats-reviewer pass.
@dataclass(frozen=True)
class FamilyDefinition:
    members: tuple[str, ...]
    text: str


_AERIAL = FamilyDefinition(
    ("aerials_won_p90",),
    "How many aerial duels the player wins per 90: more duels contested and more won both "
    "raise it; not the share won.")
_PROG_3 = FamilyDefinition(
    ("box_entries_pass_p90", "final_third_entries_pass_p90", "progressive_passes_p90"),
    "How often the player completes passes forward, into the final third and into the box, "
    "per 90: counts of completed passes, not the share completed.")
_PROG_2 = FamilyDefinition(
    ("box_entries_pass_p90", "progressive_passes_p90"),
    "How often the player completes passes forward and into the box, per 90: counts of "
    "completed passes, not the share completed.")
_CARRY_BOX = FamilyDefinition(
    ("box_entries_carry_p90", "progressive_carries_p90"),
    "How often the player carries the ball forward and into the box, per 90: volume, not "
    "dribbling success.")
_KEY_PASSES = FamilyDefinition(
    ("op_key_passes_p90",),
    "How many open-play passes per 90 lead to a shot: volume of chances made, not their quality.")

# Completion above expected (plan 006 pre-registration, user 2026-10-10; display only). The
# Passing score of a group whose spec passed the possession check; elsewhere Passing stays
# withheld with PASS_MIX_FAILED_TEXT. Added by the exporter (family_definitions), not listed per
# group below, because the check decides where it applies.
PASSING_CVE = FamilyDefinition(
    ("completion_vs_expected",),
    "The player's open-play pass completion compared with what their mix of short, medium and "
    "long passes would predict: accuracy for the length of pass, not difficulty, direction or "
    "pressure.")
CVE_LABEL = "Completion vs expected"
CVE_UNIT = "pts"
CVE_DEFINITION = ("Open-play pass completion minus the completion expected for the player's mix "
                  "of short (under 15 yards), medium (15 to 30) and long (30 and over) passes, "
                  "at the completion rates of ranked players in the same league and position, "
                  "in percentage points. Lengths are in StatsBomb pitch units (about yards). "
                  "Restarts, and passes with an unknown outcome or kicked out for an injury, are "
                  "left out.")
CVE_TOTAL = "{actual} completed vs {expected} expected ({n} passes)"
CVE_POPULATION = "n {n} {abbr} with 900+ minutes and ≥ {min} open-play passes"

FAMILY_DEFINITIONS: dict[str, dict[str, FamilyDefinition]] = {
    "CB": {
        "Aerial": _AERIAL,
        "Carrying": FamilyDefinition(
            ("final_third_entries_carry_p90", "progressive_carries_p90"),
            "How often the player carries the ball forward and into the final third, per 90: "
            "volume, not dribbling success."),
        "Progression": _PROG_3,
    },
    "FB-WB": {
        "Aerial": _AERIAL, "Carrying": _CARRY_BOX, "Creation": _KEY_PASSES,
        "Goal threat": FamilyDefinition(
            ("np_shots_p90",), "How many non-penalty shots the player takes per 90: volume, "
                               "not finishing."),
        "Progression": _PROG_2,
    },
    "DM-CM": {
        "Aerial": _AERIAL,
        "Carrying": FamilyDefinition(
            ("box_entries_carry_p90", "final_third_entries_carry_p90", "progressive_carries_p90"),
            "How often the player carries the ball forward, into the final third and into the "
            "box, per 90: volume, not dribbling success."),
        "Creation": FamilyDefinition(
            ("op_key_passes_p90", "op_xg_assisted_p90"),
            "How many open-play passes per 90 lead to a shot, and the total expected goals of "
            "those shots: how many chances, and how good they were in total."),
        "Goal threat": FamilyDefinition(
            ("np_shots_on_target_p90", "np_shots_p90", "np_xg_p90"),
            "How many non-penalty shots the player takes and puts on target per 90, and their "
            "expected goals: volume and chance quality, not finishing."),
        "Progression": _PROG_2,
    },
    "AM-Winger": {
        "Aerial": _AERIAL, "Carrying": _CARRY_BOX, "Creation": _KEY_PASSES,
        "Goal threat": FamilyDefinition(
            ("np_shots_on_target_p90", "np_shots_p90"),
            "How many non-penalty shots the player takes and puts on target per 90: volume, not "
            "finishing."),
        "Progression": _PROG_3,
    },
    "ST": {
        "Aerial": FamilyDefinition(
            ("aerial_win_pct", "aerials_won_p90"),
            "Mixes two things: how many aerial duels the player wins per 90, and the share of "
            "aerial duels won (counted only for players with enough duels). A striker who "
            "rarely wins headers scores low even with an average win rate."),
        "Carrying": _CARRY_BOX,
        "Goal threat": FamilyDefinition(
            ("np_shots_p90",), "How many non-penalty shots the player takes per 90: volume, "
                               "not finishing."),
        "Progression": _PROG_3,
    },
}


# ── Gem Finder (plan 006 simple mode, W5) ──────────────────────────────────────────────
# One position group's profiled players with their family scores; orders are computed by the
# exporter (ADR 005 §1), the page only filters by league, team and minutes tier.
GEMS_TITLE = "Gem Finder"
GEMS_LEAD = ("Players of one position with a profile, ordered by a family score, highest first. "
             "A family score is the average of the player's league percentiles for the scored "
             "metrics of that family.")
GEMS_SORT_NOTE = "Equal scores are listed by name."
GEMS_LIKE_TITLE = "Players like"
GEMS_LIKE_NOTE = ("The players whose profiles are most like this one, in the same position, from "
                  "other teams (provisional).")
# Minutes tiers for the filter: minutes played, at least (the profile gate is 900).
GEMS_MINUTES_TIERS: tuple[int, ...] = (1350, 1800, 2250, 2700)
GEMS_MINUTES_ANY = "Any (at least 900)"
# The family Gem Finder opens on, per group (user, 2026-10-10).
GEMS_DEFAULT_ORDER = {"ST": "Goal threat", "AM-Winger": "Creation", "DM-CM": "Progression",
                      "FB-WB": "Progression", "CB": "Aerial"}


# ── Methodology page (plan 006 simple mode, W5; final stats-reviewer pass) ──────────────
# (title, paragraphs). Every claim restates a rule in config, the registry or an ADR.
METHODOLOGY_TITLE = "Methodology"
METHODOLOGY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Data", (
        "Event data from StatsBomb Open Data for the 2015/16 season: La Liga, the Premier League "
        "and Serie A in full, Ligue 1 nearly in full (377 of 380 matches), and part of the 1. "
        "Bundesliga. The 1. Bundesliga is not fully covered, so its players have no percentiles "
        "or profiles.",
        "A player who moved clubs during the season has one row per club. Careers are never "
        "merged.",
    )),
    ("Per 90 and minimum minutes", (
        "Counts are shown per 90 minutes played, next to the season total. Only players with at "
        "least 900 minutes are ranked, counted as full-match equivalents (stoppage time does not "
        "add to them), so a player shown with slightly more than 900 minutes may still be "
        "unranked. A short run of games cannot make a player look elite. "
        "A rate such as pass completion is ranked only for players with enough attempts.",
    )),
    ("Position groups", (
        "Each player is placed in one of six position groups by where they played most: "
        "goalkeepers, centre-backs, full-backs and wing-backs, central midfielders (defensive "
        "midfielders included), attacking midfielders and wingers, and strikers. Players are only ever "
        "compared with their own group. Goalkeepers have no profile.",
    )),
    ("Percentiles and bands", (
        "A percentile ranks a player within their own league and position group: 90 means they "
        "are ahead of about 90% of that group. Leagues are not adjusted for strength.",
        "Tables show the percentile as a band: Top 5% (95 and above), Top 10% (90–94), Top 25% "
        "(75–89), Above average (60–74), Around average (40–59), Below average (25–39), Bottom "
        "25% (10–24), Bottom 10% (5–9) and Bottom 5% (below 5). The highest-ranked player in the "
        "league group reads \"Highest in league\" and the lowest-ranked \"Lowest in league\". A "
        "league group has about 35 to 85 players, so a top 1% band would hold one player at "
        "most, and often none. The exact number and the rank across all four full leagues are one tap away.",
        "For a lower-is-better metric, such as times dispossessed, the percentile is counted "
        "from the better end, so on a coloured bar a higher number is always better.",
    )),
    ("Hollow bars", (
        "Some metrics have no good or poor end. Style metrics describe how a player plays, not "
        "how well. Other metrics rise or fall with how much the team has the ball, so a high "
        "value may say more about the team than the player. Both show a hollow bar and read "
        "\"higher than most\", \"around average\" or \"lower than most\".",
    )),
    ("Profile shape and family scores", (
        "The metrics are grouped into families such as Progressive passing or Aerial. A family "
        "score is the average of the player's league percentiles for the family's scored "
        "metrics; style metrics are not scored. Tap a family name to see what it measures: "
        "most families count how often a player does something, not how well.",
        "A family is withheld for a position when its metrics are not shown to describe the "
        "player rather than the team. Defending is withheld for every position in this season. "
        "There is no overall rating.",
    )),
    ("Passing", (
        "\"Completion vs expected\" compares a player's open-play pass completion with what "
        "is expected for their mix of short (under 15 yards), medium (15–30) and long (30 and "
        "over) passes, using the completion rates of their league and position. +3.2 pts means a "
        "completion rate 3.2 percentage points above what that mix would predict.",
        "Before launch, we checked how closely it follows how much the team has the ball, "
        "against a limit set in advance. Strikers came in under the limit, though the link is "
        "not zero, so for them it is the Passing score. For the other outfield positions it did "
        "not, so Passing stays withheld there and the row is shown with a hollow bar, as "
        "context only. It is not used to find similar players.",
    )),
    ("Similar players", (
        "Similar players are found by comparing league percentiles on the profile's metrics "
        "within the same position group, with each family weighted equally. The closest "
        "players are listed; a player from the same team is marked. The method is provisional "
        "and will change.",
    )),
    ("Defensive rate", (
        "In compare mode, tackles, interceptions, ball recoveries, pressures, blocks and "
        "clearances also show a rate per 100 opponent open-play passes while the player was on "
        "the pitch. The rate shows how often the player acts relative to how much the opponent "
        "had the ball; it does not remove team effects.",
    )),
    ("Limits", (
        "One season only. No ages, because the open data has no birth dates. Team effects are "
        "not removed. Percentiles from different leagues rank players within their own league "
        "and are not adjusted for league strength.",
    )),
)


# ── Style tags (user-approved proposal, 2026-10-10) ────────────────────────────────────
# One tag per style metric; shown when the player's league percentile (the shown whole number)
# is at least STYLE_TAG_MIN in the position group; at most STYLE_TAG_MAX per player, highest
# percentile first (ties by this order). Tags describe style, never quality.
STYLE_TAGS: tuple[tuple[str, str], ...] = (
    ("long_passes_attempted_p90", "Long-range passer"),
    ("switches_p90", "Switches play"),
    ("team_pass_share", "Build-up hub"),
    ("dribbles_attempted_p90", "Takes players on"),
    ("carries_p90", "Ball carrier"),
    ("crosses_attempted_p90", "Frequent crosser"),
    ("fouls_won_p90", "Draws fouls"),
    ("pressures_p90", "High presser"),
)
STYLE_TAG_MIN = 90
STYLE_TAG_MAX = 3
STYLE_TAG_NOTE = "Style, not quality: in the top 10% of {group} in {league} for {metric}."
STYLE_TAG_EXTRA = {"pressures_p90": "Pressing counts also rise with how much the team defends."}
STYLE_TAGS_TITLE = "Style"
