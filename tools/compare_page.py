"""
A static comparison page for 2–3 player-seasons (plan 005 M4e, ADR 004 §11.5).

A visual sanity check before the DNA vector (M4f), not the M9 frontend. It reads
data/processed/player_season_percentiles.parquet and the registry, plus identity and
context columns (name, team, minutes, second group, signature-trait season counts) from
player_season_stats.parquet. It computes nothing statistical: every number on the page is
a parquet cell, a registry field or a config label, wrapped in a span that names its
source (data-src / data-id / data-reg / data-cfg), so a test can trace each one back.

Display rules (plan 005 M4e; ADR 004 §11.5):
- a stored percentile of 100 shows ">99" and 0 shows "<1" (rows outside their population);
  others are rounded half-up to an integer and clamped to 1–99;
- a null percentile shows its status as text, never a blank;
- a ratio percentile names its selected population: "n 64 of 157 STs with ≥ 43 long passes",
  where 157 is the n of POPULATION_REFERENCE_METRIC in the same population;
- a zero in a zero-heavy cell is "tied at zero, not a rank"; signature traits are shown as
  holder / non-holder (ADR 004 §1), never as a percentile;
- long pass completion is worded as team-influenced, and team-style cells are not coloured;
- the second position group is shown only for gated (meets_min_minutes) rows; second-group
  rows (D5) are a separate, display-only section;
- when the players' groups differ, every percentile says which group it ranks against.

Usage:
  python tools/compare_page.py --season-id 27 --player "Kanté" --player Busquets
  python tools/compare_page.py --player 3244 --player 3813@22 --out page.html
A --player is a player_id, a player_id@team_id (a mover's stint) or a name fragment
(case- and accent-insensitive).
"""
from __future__ import annotations

import argparse
import html
import math
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics.registry import (
    MEASUREMENT_SOURCE, REGISTRY, REGISTRY_VERSION, SPECS, MetricSpec,
    is_signature_holder,
)

_ROOT = Path(__file__).resolve().parents[1]
PERCENTILES_PATH = _ROOT / "data" / "processed" / "player_season_percentiles.parquet"
SEASON_PATH = _ROOT / "data" / "processed" / "player_season_stats.parquet"
OUTPUT_DIR = _ROOT / "data" / "processed" / "reports"

KEY: tuple[str, ...] = C.SEASON_KEY
PRIMARY, SECOND = "primary", "second"

# The n of this metric in a population is that population's size: its value is never null,
# so every scored row is eligible and carries n in both scopes (2015/16: 1,246 of 1,246; its
# global percentile can still be null by D9). Ratio labels read N from it (ADR 004 §11.5).
POPULATION_REFERENCE_METRIC = "passes_attempted_p90"

SPECS_BY_COLUMN: dict[str, MetricSpec] = {s.column: s for s in SPECS}
SIGNATURE_COUNT_COLUMNS: tuple[str, ...] = tuple(sorted(
    {s.count_column for s in SPECS if s.count_column
     and any(c.status == "signature" for (col, _), c in REGISTRY.cells.items() if col == s.column)}))
IDENTITY_COLUMNS: tuple[str, ...] = (
    *KEY, "player_name", "team_name", "position_group", "second_position_group",
    "minutes_played", "minutes_nominal_equiv", "meets_min_minutes", *SIGNATURE_COUNT_COLUMNS)

FAMILY_LABELS: dict[str, str] = {"Progression": "Progressive passing",
                                 "Carrying": "Carrying & dribbling"}   # ADR 004 §10.7
FAMILY_ORDER: tuple[str, ...] = tuple(dict.fromkeys(s.family for s in SPECS))
GROUP_PLURAL: dict[str, str] = {"CB": "CBs", "FB-WB": "FB-WBs", "DM-CM": "DM-CMs",
                                "AM-Winger": "AM-Wingers", "ST": "STs", "GK": "GKs"}
# The denominator of each ratio, as a noun for "with ≥ k <noun>".
DENOMINATOR_NOUN: dict[str, str] = {
    "pass_completion_pct": "passes", "long_pass_completion_pct": "long passes",
    "team_pass_share": "team open-play passes", "dribble_success_pct": "dribbles",
    "cross_completion_pct": "crosses", "np_xg_per_shot": "non-penalty shots",
    "xg_per_shot": "shots", "tackle_success_pct": "tackles", "aerial_win_pct": "aerial duels",
}
# Restart deliveries include throw-ins (ADR 004 §10.6): no "set-piece" label until the
# FB-WB throw-in share is measured (plan 005 backlog). Overrides the registry display name.
_RESTARTS = "(corners, free kicks, throw-ins)"
METRIC_LABELS: dict[str, str] = {
    "restart_key_passes_p90": f"Restart key passes {_RESTARTS}",
    "restart_xg_assisted_p90": f"Restart xG assisted {_RESTARTS}",
    "restart_assists_p90": f"Restart assists {_RESTARTS}",
}
# A D5 second-group row is scored against a population that does not contain the player,
# so no holder verdict is given against it (user, 2026-10-06).
SECOND_SIGNATURE_TEXT = "not assessed (second group, display only)"
LONG_PASS_NOTE = ("reflects the team's passing style as much as the player: "
                  "a low value is not a measure of poor individual passing")

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
# Static text that carries a digit; the trace test accepts exactly these.
STATIC_REFS: frozenset[str] = frozenset({"D9", "D5", "ADR 004 §11", "M4e", "M4f", ">99", "<1"})


class PageError(ValueError):
    """The inputs cannot produce an honest page (stale registry, ambiguous player, ...)."""


# ── Formatting (the trace test re-applies these to the parquet cells) ──────────────

def format_pct(p: float) -> str:
    if p == 0:
        return "<1"
    if p == 100:
        return ">99"
    if not 0 < p < 100:
        raise PageError(f"percentile out of range: {p}")
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
        raise PageError(f"negative identity number: {v}")
    if isinstance(v, float):
        return str(math.floor(v + 0.5)) if not v.is_integer() else str(int(v))
    return str(v)


def status_text(status: str) -> str:
    try:
        return STATUS_TEXT[status]
    except KeyError:
        raise PageError(f"unknown percentile status {status!r}") from None


# ── Inputs ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Selector:
    raw: str
    player_id: int | None = None
    team_id: int | None = None
    name: str | None = None


def parse_selector(raw: str) -> Selector:
    head, _, team = raw.partition("@")
    if head.strip().isdigit():
        if team and not team.strip().isdigit():
            raise PageError(f"--player {raw!r}: team after '@' must be a team_id")
        return Selector(raw, int(head), int(team) if team else None)
    if team:
        raise PageError(f"--player {raw!r}: '@team_id' needs a numeric player_id")
    return Selector(raw, name=fold(raw))


def fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch)).casefold().strip()


def resolve(identity: pl.DataFrame, sel: Selector) -> dict[str, Any]:
    rows = identity.to_dicts()
    if sel.player_id is not None:
        hits = [r for r in rows if r["player_id"] == sel.player_id
                and (sel.team_id is None or r["team_id"] == sel.team_id)]
    else:
        assert sel.name is not None
        hits = [r for r in rows if sel.name in fold(r["player_name"])]
        if len({r["player_id"] for r in hits}) > 1:
            raise PageError(f"--player {sel.raw!r} matches several players: "
                            + "; ".join(f"{r['player_id']} {r['player_name']} ({r['team_name']})"
                                        for r in hits[:12]))
    if not hits:
        raise PageError(f"--player {sel.raw!r}: no player-season in this season")
    if len(hits) > 1:
        raise PageError(f"--player {sel.raw!r} has several stints this season; pick one with "
                        "player_id@team_id: " + "; ".join(
                            f"{r['player_id']}@{r['team_id']} ({r['team_name']})" for r in hits))
    return hits[0]


def load_inputs(season_id: int, percentiles_path: Path = PERCENTILES_PATH,
                season_path: Path = SEASON_PATH) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The season's percentile rows and identity rows, after the provenance guards."""
    pct = pl.read_parquet(percentiles_path).filter(pl.col("season_id") == season_id)
    if pct.is_empty():
        raise PageError(f"no percentile rows for season_id {season_id}")
    versions = set(pct["registry_version"].unique().to_list())
    if versions != {REGISTRY_VERSION}:
        raise PageError(f"percentiles were built with registry {sorted(versions)}, the code has "
                        f"{REGISTRY_VERSION}: rebuild with build_percentiles")
    sources = set(pct["source"].unique().to_list()) | set(pct["measurement_source"].unique().to_list())
    if sources != {MEASUREMENT_SOURCE}:
        raise PageError(f"mixed or foreign sources {sorted(sources)}; one source only (CLAUDE.md)")
    shas = set(pct["season_parquet_sha256"].unique().to_list())
    actual = C.file_sha256(season_path)
    if shas != {actual}:
        raise PageError(f"the season parquet ({actual[:12]}…) is not the one the percentiles were "
                        f"built from ({sorted(s[:12] for s in shas)}…)")
    identity = (pl.read_parquet(season_path, columns=list(IDENTITY_COLUMNS))
                .filter(pl.col("season_id") == season_id))
    return pct, identity


# ── View ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Player:
    identity: Mapping[str, Any]
    rows: Mapping[tuple[str, str], Mapping[str, Any]]   # (cohort_role, metric) -> parquet row

    @property
    def key(self) -> tuple[Any, ...]:
        return tuple(self.identity[k] for k in KEY)

    @property
    def group(self) -> str:
        return self.identity["position_group"]

    def row(self, metric: str, role: str = PRIMARY) -> Mapping[str, Any]:
        return self.rows[(role, metric)]

    def has_second(self) -> bool:
        return any(role == SECOND for role, _ in self.rows)


def build_players(pct: pl.DataFrame, identity: pl.DataFrame,
                  selectors: Sequence[Selector]) -> list[Player]:
    if not 2 <= len(selectors) <= 3:
        raise PageError("pick 2 or 3 players")
    players: list[Player] = []
    for sel in selectors:
        ident = resolve(identity, sel)
        key = tuple(ident[k] for k in KEY)
        rows = pct.filter(pl.all_horizontal(pl.col(k) == v for k, v in zip(KEY, key))).to_dicts()
        by = {(r["cohort_role"], r["metric"]): r for r in rows}
        missing = {s.column for s in SPECS} - {m for role, m in by if role == PRIMARY}
        if missing:
            raise PageError(f"{ident['player_name']}: no percentile rows for {sorted(missing)}")
        if any(r["position_group"] != ident["position_group"] for r in rows
               if r["cohort_role"] == PRIMARY):
            raise PageError(f"{ident['player_name']}: position group differs between the parquets")
        players.append(Player(ident, by))
    if len({p.key for p in players}) < len(players):
        raise PageError("the same player-season is picked twice")
    return players


# ── HTML pieces ──────────────────────────────────────────────────────────────────

def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def src_span(row: Mapping[str, Any], column: str, text: str, cls: str = "num") -> str:
    ref = "|".join(str(row[k]) for k in (*KEY, "cohort_role", "metric")) + f"|{column}"
    return f'<span class="{cls}" data-src="{esc(ref)}">{esc(text)}</span>'


def id_span(ident: Mapping[str, Any], column: str, text: str | None = None) -> str:
    ref = "|".join(str(ident[k]) for k in KEY) + f"|{column}"
    shown = format_number(ident[column]) if text is None else text
    return f'<span class="num" data-id="{esc(ref)}">{esc(shown)}</span>'


def reg_span(metric: str, group: str, field: str, text: str) -> str:
    return f'<span data-reg="{esc(metric)}|{esc(group)}|{esc(field)}">{esc(text)}</span>'


def cfg_span(field: str, key: str, text: str) -> str:
    return f'<span data-cfg="{esc(field)}|{esc(key)}">{esc(text)}</span>'


def static(text: str) -> str:
    if text not in STATIC_REFS:
        raise PageError(f"static reference {text!r} is not declared in STATIC_REFS")
    return f'<span data-static="">{esc(text)}</span>'


def competition_label(cid: int, sid: int) -> str:
    for cs in config.COMPETITION_SEASONS:
        if cs.competition_id == cid and cs.season_id == sid:
            return cfg_span("competition_label", f"{cid}|{sid}", cs.label)
    raise PageError(f"competition {cid} season {sid} is not in config.COMPETITION_SEASONS")


def metric_label(spec: MetricSpec) -> str:
    return METRIC_LABELS.get(spec.column, spec.display_name)


def _tone(spec: MetricSpec, team_style_biased: bool, pct: float) -> str:
    """Colour class: direction only colours (ADR 004 §10.7). Style metrics, long pass
    completion and every team-style-biased cell are never coloured (§11.5). The registry
    flag covers both kinds: DNA cells admitted by the allow-list (reasons "allow_listed")
    and display cells moved by the possession trigger (reason "team_style_biased")."""
    if spec.direction == "style" or spec.column == "long_pass_completion_pct" or team_style_biased:
        return "neutral"
    score = pct if spec.direction == "higher_better" else 100 - pct
    return "good" if score >= 67 else "poor" if score <= 33 else "mid"


def _status_html(row: Mapping[str, Any], scope: str, spec: MetricSpec) -> str:
    status = row[f"status_{scope}"]
    if status == "below_min_attempts":
        cell = REGISTRY.cell(spec.column, row["position_group"])
        return (f"below the minimum of ≥ "
                f"{reg_span(spec.column, row['position_group'], 'min_attempts', str(cell.min_attempts))} "
                f"{esc(DENOMINATOR_NOUN[spec.column])}")
    if status == "cohort_too_small":
        return f"{status_text(status)} ({src_span(row, 'min_cohort_size', str(row['min_cohort_size']), 'ref')})"
    if status in ("league_inconsistent", "d9_undefined"):
        return f"{esc(status_text(status))} ({static('D9')})"
    return esc(status_text(status))


def _n_html(player: Player, row: Mapping[str, Any], scope: str, spec: MetricSpec) -> str:
    n = row[f"n_{scope}"]
    if n is None:
        return ""
    out = f"n {src_span(row, f'n_{scope}', str(n))}"
    cell = REGISTRY.cell(spec.column, row["position_group"]) if row["registry_status"] else None
    if spec.ratio is not None and cell is not None and cell.min_attempts is not None:
        ref = player.row(POPULATION_REFERENCE_METRIC, row["cohort_role"])
        if ref[f"n_{scope}"] is None:
            raise PageError(f"{player.identity['player_name']}: reference metric "
                            f"{POPULATION_REFERENCE_METRIC} has no {scope} n")
        out += (f" of {src_span(ref, f'n_{scope}', str(ref[f'n_{scope}']))} "
                f"{esc(GROUP_PLURAL[row['position_group']])} with ≥ "
                f"{reg_span(spec.column, row['position_group'], 'min_attempts', str(cell.min_attempts))} "
                f"{esc(DENOMINATOR_NOUN[spec.column])}")
    return out


def _scope_html(player: Player, row: Mapping[str, Any], scope: str, spec: MetricSpec,
                mixed: bool, zero_tie: bool) -> str:
    is_basis = row["basis"] == scope
    label = "local" if scope == "local" else "global"
    pct = row[f"pct_{scope}"]
    if zero_tie and pct is not None:
        shown = '<span class="reason">tied at zero, not a rank</span>'
    elif pct is not None:
        shown = src_span(row, f"pct_{scope}", format_pct(pct), "num pct")
        if mixed:
            shown += f' <span class="vs">vs {esc(GROUP_PLURAL[row["position_group"]])}</span>'
    else:
        shown = f'<span class="reason">{_status_html(row, scope, spec)}</span>'
    n = _n_html(player, row, scope, spec)
    cls = "scope basis" if is_basis else "scope"
    mark = ' <span class="tag">basis</span>' if is_basis else ""
    return (f'<div class="{cls}"><span class="lab">{label}{mark}</span> {shown}'
            + (f' <span class="n">{n}</span>' if n else "") + "</div>")


def metric_cell(player: Player, metric: str, mixed: bool, section: str | None,
                role: str = PRIMARY) -> str:
    row = player.row(metric, role)
    spec = SPECS_BY_COLUMN[metric]
    reg_status = row["registry_status"]
    cell = REGISTRY.cell(metric, row["position_group"]) if reg_status else None
    if reg_status == "signature":
        text = "signature trait — see below" if role == PRIMARY else SECOND_SIGNATURE_TEXT
        return f'<td class="pc"><span class="reason">{esc(text)}</span></td>'
    zero_tie = bool(cell and "zero_heavy" in cell.reasons and row["value"] == 0)
    value = src_span(row, "value", format_value(metric, row["value"]), "num val")
    if row["value"] is None:
        value += ' <span class="reason">no value</span>'
    tag = ""
    if reg_status and section and reg_status != section:
        tag = (f' <span class="tag">{esc(reg_status)} in '
               f'{esc(GROUP_PLURAL[row["position_group"]])}</span>')
    bar = ""
    basis = row["basis"]
    if basis and row[f"pct_{basis}"] is not None and not zero_tie:
        p = row[f"pct_{basis}"]
        biased = bool(cell and cell.team_style_biased)
        bar = (f'<div class="bar"><div class="fill {_tone(spec, biased, p)}" '
               f'style="width:{p:.1f}%"></div></div>')
    scopes = "".join(_scope_html(player, row, s, spec, mixed, zero_tie) for s in ("local", "global"))
    return f'<td class="pc"><div class="valrow">{value}{tag}</div>{bar}{scopes}</td>'


def metric_notes(players: Sequence[Player], metric: str, role: str = PRIMARY) -> str:
    """The registry notes and display reasons of each player's cell, de-duplicated."""
    seen: list[str] = []
    for p in players:
        if (role, metric) not in p.rows:
            continue
        group = p.row(metric, role)["position_group"]
        if not p.row(metric, role)["registry_status"]:
            continue
        cell = REGISTRY.cell(metric, group)
        for i, note in enumerate(cell.notes):
            seen.append(reg_span(metric, group, f"notes|{i}", note))
        if cell.status != "dna":
            fixed = SPECS_BY_COLUMN[metric].fixed_display
            why = (reg_span(metric, group, "fixed_display.reason", fixed.reason)
                   if fixed is not None and "fixed_display" in cell.reasons
                   else reg_span(metric, group, "reasons", ", ".join(cell.reasons)))
            seen.append(f'{esc(GROUP_PLURAL[group])}: {why}')
    if metric == "long_pass_completion_pct":
        seen.insert(0, esc(LONG_PASS_NOTE))
    return "<br>".join(dict.fromkeys(seen))


def direction_label(spec: MetricSpec) -> str:
    """The label under the metric name: long pass completion reads as team style (§11.5),
    so it gets no good/bad direction."""
    if spec.column == "long_pass_completion_pct":
        return "team-influenced"
    return spec.direction.replace("_", " ")


def _family_rows(players: Sequence[Player], family: str, mixed: bool) -> str:
    groups = {p.group for p in players if p.group in GROUP_PLURAL and p.group != "GK"}
    fam = [s.column for s in SPECS if s.family == family]

    def status_in(metric: str, group: str) -> str:
        return REGISTRY.cell(metric, group).status

    dna = [m for m in fam if any(status_in(m, g) == "dna" for g in groups)]
    signature_everywhere = [m for m in fam if groups and all(status_in(m, g) == "signature"
                                                              for g in groups)]
    display = [m for m in fam if m not in dna and m not in signature_everywhere]
    out = []
    for section, metrics in (("dna", dna), ("display", display)):
        if not metrics:
            continue
        title = "DNA metrics" if section == "dna" else "Display metrics"
        out.append(f'<tr class="sec"><th colspan="{len(players) + 2}">{title}</th></tr>')
        for m in metrics:
            spec = SPECS_BY_COLUMN[m]
            cells = "".join(metric_cell(p, m, mixed, section) for p in players)
            out.append(f'<tr><th class="metric">{esc(metric_label(spec))}'
                       f'<div class="dir">{esc(direction_label(spec))}</div></th>'
                       f'{cells}<td class="notes">{metric_notes(players, m)}</td></tr>')
    if not out:
        return ""
    head = "".join(f"<th>{esc(p.identity['player_name'])}</th>" for p in players)
    return (f'<section><h2>{esc(FAMILY_LABELS.get(family, family))}</h2><div class="scroll">'
            f'<table><thead><tr><th>Metric</th>{head}<th>Notes</th></tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table></div></section>')


def header_card(p: Player) -> str:
    i = p.identity
    gated = bool(i["meets_min_minutes"])
    second = ""
    if gated and i["second_position_group"] is not None:
        second = f' · also {id_span(i, "second_position_group", i["second_position_group"])}'
    status = p.row(POPULATION_REFERENCE_METRIC)["percentile_status"]
    status_line = ("scored" if status == "scored"
                   else src_span(p.row(POPULATION_REFERENCE_METRIC), "percentile_status",
                                 status_text(status), "reason"))
    return (f'<div class="card"><h3>{id_span(i, "player_name", i["player_name"])}</h3>'
            f'<div>{id_span(i, "team_name", i["team_name"])} · '
            f'{competition_label(i["competition_id"], i["season_id"])}</div>'
            f'<div>{id_span(i, "position_group", i["position_group"])}{second}</div>'
            f'<div>{id_span(i, "minutes_played")} min played · '
            f'{id_span(i, "minutes_nominal_equiv")} nominal</div>'
            f'<div class="muted">percentiles: {status_line}</div></div>')


def signature_section(players: Sequence[Player]) -> str:
    rows = []
    traits = sorted({m for p in players if p.group in GROUP_PLURAL and p.group != "GK"
                     for m in REGISTRY.signature_traits(p.group)})
    for m in traits:
        spec = SPECS_BY_COLUMN[m]
        assert spec.count_column is not None
        cells = []
        for p in players:
            row = p.row(m)
            if row["registry_status"] != "signature":
                cells.append('<td><span class="reason">not a signature trait in this group</span></td>')
                continue
            cell = REGISTRY.cell(m, p.group)
            count = p.identity[spec.count_column]
            held = is_signature_holder(cell, count, row["percentile_status"])
            count_html = id_span(p.identity, spec.count_column)
            minimum = reg_span(m, p.group, "signature_min_count", str(cell.signature_min_count))
            if held is None:
                verdict = f'<span class="reason">not assessed: {esc(status_text(row["percentile_status"]))}</span>'
            else:
                verdict = (f'<strong class="{"holder" if held else "nonholder"}">'
                           f'{"holder" if held else "non-holder"}</strong>')
            cells.append(f'<td>{verdict}<div class="muted">season count {count_html} · minimum '
                         f'{minimum} for {esc(GROUP_PLURAL[p.group])}</div></td>')
        rows.append(f'<tr><th class="metric">{esc(metric_label(spec))}</th>'
                    f'{"".join(cells)}<td class="notes">{metric_notes(players, m)}</td></tr>')
    if not rows:
        return ""
    head = "".join(f"<th>{esc(p.identity['player_name'])}</th>" for p in players)
    return ('<section><h2>Signature traits</h2><p class="muted">A holder / non-holder flag, '
            'not a percentile: most players in the group have none, so a rank would mostly '
            'compare zeros.</p><div class="scroll"><table><thead><tr><th>Trait</th>'
            f'{head}<th>Notes</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def second_group_section(players: Sequence[Player]) -> str:
    out = []
    for p in players:
        if not p.has_second():
            continue
        group = p.row(POPULATION_REFERENCE_METRIC, SECOND)["position_group"]
        rows = []
        for family in FAMILY_ORDER:
            for s in (s for s in SPECS if s.family == family):
                rows.append(f'<tr><th class="metric">{esc(metric_label(s))}'
                            f'<div class="dir">{esc(FAMILY_LABELS.get(family, family))} · '
                            f'{esc(p.row(s.column, SECOND)["registry_status"] or "")} in '
                            f'{esc(GROUP_PLURAL[group])}</div></th>'
                            f'{metric_cell(p, s.column, True, None, SECOND)}</tr>')
        out.append(
            f'<section class="second"><h2>{esc(p.identity["player_name"])} as '
            f'{esc(GROUP_PLURAL[group][:-1])} — display only ({static("D5")})</h2>'
            f'<p class="muted">Scored against the {esc(GROUP_PLURAL[group])} populations, which do '
            'not contain the player, so values can sit at the very ends of the scale (shown as '
            f'{static("<1")} / {static(">99")}). '
            'Never used for DNA, similarity or rarity.</p>'
            f'<div class="scroll"><table><thead><tr><th>Metric</th><th>{esc(p.identity["player_name"])}'
            f'</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')
    return "".join(out)


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e3e1da;--card:#ffffff;--basis:#f1efe6;
--good:#2f7d5b;--mid:#9a8f6a;--poor:#b4513a;--neutral:#7d8590;--tag:#e8e4d6}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;
--card:#1f1f1c;--basis:#2a2924;--good:#5fb68c;--mid:#c2b68a;--poor:#e0846c;--neutral:#9aa3ad;--tag:#3a382f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px 16px 64px}
main{max-width:1280px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
h3{margin:0 0 4px;font-size:16px}.muted,.dir,.n,.lab{color:var(--muted)}.dir,.lab,.n{font-size:12px}
.warn{border-left:3px solid var(--poor);padding:8px 12px;background:var(--card);margin:12px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
thead th{font-size:13px;position:sticky;top:0;background:var(--card)}
th.metric{width:200px;font-weight:600}td.pc{min-width:220px}td.notes{font-size:12px;color:var(--muted);max-width:280px}
tr.sec th{background:var(--basis);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.num{font-variant-numeric:tabular-nums}.val{font-weight:600}.pct{font-weight:600}
.scope{font-size:13px;padding:1px 4px;border-radius:4px}.scope.basis{background:var(--basis)}
.tag{font-size:11px;background:var(--tag);border-radius:3px;padding:0 4px;margin-left:4px}
.vs{font-size:11px;color:var(--muted)}.reason{color:var(--muted);font-style:italic}
.bar{height:6px;background:var(--line);border-radius:3px;margin:4px 0;max-width:200px}
.fill{height:6px;border-radius:3px}.fill.good{background:var(--good)}.fill.mid{background:var(--mid)}
.fill.poor{background:var(--poor)}.fill.neutral{background:var(--neutral)}
.holder{color:var(--good)}.nonholder{color:var(--muted)}footer{margin-top:40px;font-size:12px;color:var(--muted);word-break:break-all}
"""


def render(players: Sequence[Player]) -> str:
    groups = [p.group for p in players]
    mixed = len(set(groups)) > 1
    names = " vs ".join(p.identity["player_name"] for p in players)
    warn = ""
    if mixed:
        warn = ('<div class="warn">The players are ranked in different position groups ('
                + ", ".join(esc(GROUP_PLURAL[g]) for g in dict.fromkeys(groups))
                + '). Every percentile says which group it ranks against; percentiles from '
                  'different groups are not directly comparable.</div>')
    families = "".join(_family_rows(players, f, mixed) for f in FAMILY_ORDER)
    ref = players[0].row(POPULATION_REFERENCE_METRIC)
    footer = (f'<footer>Registry {src_span(ref, "registry_version", ref["registry_version"], "ref")} · '
              f'source {src_span(ref, "measurement_source", ref["measurement_source"], "ref")} · '
              f'season parquet {src_span(ref, "season_parquet_sha256", ref["season_parquet_sha256"], "ref")} · '
              f'minimum cohort {src_span(ref, "min_cohort_size", str(ref["min_cohort_size"]), "ref")} · '
              f'minutes gate {src_span(ref, "min_minutes_threshold", format_number(ref["min_minutes_threshold"]), "ref")}'
              f' · {static("ADR 004 §11")} · {static("M4e")}</footer>')
    legend = ('<p class="muted">Raw value, then the local (league × group) and global (group across '
              'the pool leagues) percentile with its population size n. The registry basis is '
              'highlighted and drawn as the bar. Percentiles rank the raw value (high = more); the '
              'bar colour follows the metric direction, and style or team-influenced metrics are '
              f'grey. {static(">99")} / {static("<1")} mark rows ranked against a population they are not in. A '
              f'radar view waits for the one-basis decision ({static("M4f")}).</p>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(names)}</title><style>{CSS}</style></head><body><main>"
            f"<h1>{esc(names)}</h1>{warn}<div class=\"cards\">"
            f"{''.join(header_card(p) for p in players)}</div>{legend}{families}"
            f"{signature_section(players)}{second_group_section(players)}{footer}"
            "</main></body></html>\n")


def generate(season_id: int, selectors: Sequence[str], *, percentiles_path: Path = PERCENTILES_PATH,
             season_path: Path = SEASON_PATH) -> tuple[str, list[Player]]:
    pct, identity = load_inputs(season_id, percentiles_path, season_path)
    players = build_players(pct, identity, [parse_selector(s) for s in selectors])
    return render(players), players


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season-id", type=int, default=27)
    ap.add_argument("--player", action="append", required=True,
                    help="player_id, player_id@team_id, or a name fragment (2–3 times)")
    ap.add_argument("--out", type=Path, help="output HTML (default data/processed/reports/)")
    args = ap.parse_args(argv)
    try:
        page, players = generate(args.season_id, args.player)
    except PageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = args.out or OUTPUT_DIR / ("compare_" + "_".join(
        f"{p.identity['player_id']}-{p.identity['team_id']}" for p in players) + ".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
