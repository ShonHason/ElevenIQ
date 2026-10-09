"""
Display rules shared by the comparison page and the web export (plan 006 W1c; moved from
tools/compare_page.py, plan 005 M4e / M4f4, ADR 004 §11.5). They format exported values and
name statuses; they compute no statistic.

- percentile text: a stored 100 shows ">99" and 0 "<1" (rows ranked against a population they
  are not in); others are rounded half-up and clamped to 1–99;
- value text: shares and _pct metrics as a percent with one decimal, ratios with 3 decimals,
  others with 2; a null value is "—";
- identity numbers (minutes, counts) are rounded half-up and never negative;
- every percentile status and dimension status has text; an unknown one raises;
- uncoloured: style metrics, long pass completion and team_style_biased cells carry no
  good / poor colour (ADR 004 §10.7, §11.5);
- percentile bin (DESIGN.md §4): five bins of the direction-adjusted percentile (lower-better
  as 100 − p), aligned with words.pct_words: p ≤ 10 → 0, < 40 → 1, ≤ 60 → 2, < 90 → 3, else 4;
  "uncoloured" for an uncoloured cell and None without a percentile;
- near-gate flag (config.NEAR_GATE_*): CB and FB-WB rows with gate ≤ nominal minutes < 1,350.
"""
from __future__ import annotations

import math
from typing import Any

from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import MetricSpec
from eleveniq.config import (
    MIN_MINUTES_THRESHOLD,
    MIN_MINUTES_TOLERANCE,
    NEAR_GATE_GROUPS,
    NEAR_GATE_MAX_MINUTES,
)
from eleveniq.export import words as W


class DisplayError(ValueError):
    """The inputs cannot be shown honestly (an out-of-range value, an unknown status, ...)."""


SPECS_BY_COLUMN: dict[str, MetricSpec] = {s.column: s for s in R.SPECS}
# The n of this metric in a population is that population's size: its value is never null,
# so every scored row is eligible and carries n in both scopes (2015/16: 1,246 of 1,246; its
# global percentile can still be null by D9). Ratio labels read N from it (ADR 004 §11.5).
POPULATION_REFERENCE_METRIC = "passes_attempted_p90"
GROUP_ABBR_PLURAL: dict[str, str] = {"CB": "CBs", "FB-WB": "FB-WBs", "DM-CM": "DM-CMs",
                                     "AM-Winger": "AM-Wingers", "ST": "STs", "GK": "GKs"}
# The denominator of each ratio, as a noun for "with ≥ k <noun>".
DENOMINATOR_NOUN: dict[str, str] = {
    "pass_completion_pct": "passes", "long_pass_completion_pct": "long passes",
    "team_pass_share": "team open-play passes", "dribble_success_pct": "dribbles",
    "cross_completion_pct": "crosses", "np_xg_per_shot": "non-penalty shots",
    "xg_per_shot": "shots", "tackle_success_pct": "tackles", "aerial_win_pct": "aerial duels",
    "defensive_duel_success_pct": "defensive duels (tackles + times dribbled past)",
}
# A second-group row is scored against a population that does not contain the player, so no
# holder verdict is given against it (user, 2026-10-06).
SECOND_SIGNATURE_TEXT = "not assessed (second group, display only)"
# Every status the percentiles table can hold, as text. An unknown status raises.
STATUS_TEXT: dict[str, str] = {
    "not_in_pool": "league outside the percentile pool",
    "goalkeeper": "goalkeeper: no outfield percentiles",
    "below_gate": "below the minutes gate",
    "no_signal": "no reliable signal for this ratio in this group",
    "no_min_attempts": "no minimum attempts measured (display only)",
    "below_min_attempts": "below the minimum attempts",
    "null_value": "no value",
    "cohort_too_small": "population below the minimum size",
    "d9_undefined": "not testable (league populations too small or unmeasured)",
    "league_inconsistent": "not used: league means differ",
}


def format_pct(p: float) -> str:
    if p == 0:
        return "<1"
    if p == 100:
        return ">99"
    if not 0 < p < 100:
        raise DisplayError(f"percentile out of range: {p}")
    return str(min(99, max(1, math.floor(p + 0.5))))


def format_value(metric: str, v: float | None) -> str:
    if v is None:
        return "—"
    spec = SPECS_BY_COLUMN[metric]
    if metric.endswith("_pct") or spec.unit == "share":
        return f"{v * 100:.1f}%"
    if spec.unit == "ratio":
        return f"{v:.3f}"
    return f"{v:.2f}"


def format_number(v: Any) -> str:
    """Counts, minutes and other identity numbers (never negative; half-up rounding)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
        raise DisplayError(f"negative identity number: {v}")
    if isinstance(v, float):
        return str(math.floor(v + 0.5)) if not v.is_integer() else str(int(v))
    return str(v)


def status_text(status: str) -> str:
    try:
        return STATUS_TEXT[status]
    except KeyError:
        raise DisplayError(f"unknown percentile status {status!r}") from None


def dim_status_text(status: str) -> str:
    try:
        return W.WITHHELD_TEXT[status]
    except KeyError:
        raise DisplayError(f"unknown dimension score status {status!r}") from None


def withheld_reason(family: str, group: str, status: str,
                    derivation: R.Derivation | None = None) -> str:
    """A withheld status as text, with "; underpowered" when the registry notes it for the
    group's Defending status (ADR 004 §13.8)."""
    text = dim_status_text(status)
    if family == "Defending":
        fs = (derivation or R.REGISTRY).defending_status(group)
        if fs.status != status:
            raise DisplayError(f"{group} Defending status {status!r} ≠ the registry's "
                               f"{fs.status!r}: rebuild with build_player_dna")
        if any(n.startswith("underpowered") for n in fs.notes):
            text += "; underpowered"
    return text


def uncoloured(spec: MetricSpec, team_style_biased: bool) -> bool:
    """Style metrics, long pass completion and every team-style-biased cell are never coloured
    good / poor (ADR 004 §10.7, §11.5)."""
    return (spec.direction == "style" or spec.column == "long_pass_completion_pct"
            or team_style_biased)


def adjusted(spec: MetricSpec, p: float) -> float:
    """The direction-adjusted percentile: lower-better as 100 − p (style metrics unchanged)."""
    return 100.0 - p if spec.direction == "lower_better" else p


def percentile_bin(spec: MetricSpec, team_style_biased: bool, p: float | None) -> int | str | None:
    """The DESIGN.md §4 colour bin of a percentile (see the module docstring)."""
    if p is None:
        return None
    if not 0 <= p <= 100:
        raise DisplayError(f"percentile out of range: {p}")
    if uncoloured(spec, team_style_biased):
        return "uncoloured"
    a = adjusted(spec, p)
    if a <= 10:
        return 0
    if a < W.MID_LO:
        return 1
    if a <= W.MID_HI:
        return 2
    return 3 if a < 90 else 4


def near_gate(group: str, nominal_minutes: float) -> bool:
    """The "Fewer minutes: less reliable" flag: only CB and FB-WB, gate ≤ nominal < 1,350."""
    return (group in NEAR_GATE_GROUPS
            and MIN_MINUTES_THRESHOLD - MIN_MINUTES_TOLERANCE <= nominal_minutes < NEAR_GATE_MAX_MINUTES)
