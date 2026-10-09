"""
A static comparison page for 2–3 player-seasons (plan 005 M4e/M4f4, ADR 004 §11.5, §12).

A visual sanity check, not the M9 frontend. It reads
data/processed/player_season_percentiles.parquet and the registry, identity and context
columns (name, team, minutes, second group, signature-trait season counts) from
player_season_stats.parquet, and the DNA vector and dimension scores from player_dna.parquet
and player_dna_dimensions.parquet. It computes nothing statistical: every number on the page
is a parquet cell, a registry field or a config label, wrapped in a span that names its
source (data-src / data-id / data-reg / data-cfg / data-dim / data-static), so a test can
trace each one back; a radar vertex carries data-dna and sits at its stored pct_local.

Display rules (plan 005 M4e; ADR 004 §11.5):
- a stored percentile of 100 shows ">99" and 0 shows "<1" (rows outside their population);
  others are rounded half-up to an integer and clamped to 1–99;
- a null percentile shows its status as text, never a blank;
- a ratio percentile names its selected population: "n 64 of 157 STs with ≥ 43 long passes",
  where 157 is the n of POPULATION_REFERENCE_METRIC in the same population;
- a zero in a zero-heavy cell is "tied at zero, not a rank"; signature traits are shown as
  holder / non-holder (ADR 004 §1), never as a percentile;
- long pass completion has no good/poor reading (export/words.py), and cells associated with
  team possession are not coloured;
- the second position group is shown only for gated (meets_min_minutes) rows; second-group
  rows (D5) are a separate, display-only section;
- when the players' groups differ, every percentile says which group it ranks against.

DNA (M4f4, ADR 004 §12):
- an inline-SVG radar of the DNA metrics' local percentiles (the one vector basis, §12.1),
  axes grouped by family, each player labelled "vs <league> <group>"; drawn only when the
  players share a position group;
- style axes, long pass completion and axes associated with team possession are uncoloured
  (muted label, dashed spoke): outward means
  more, not better;
- a missing DNA metric (partial row, §12.2) is a gap in the outline with a marker at the
  rim, never a vertex at zero;
- each dimension score shows its member count ("based on 2 metrics"); a withheld family
  shows "withheld (<reason>)": Defending with its D5 status (§13.8; every group in 2015/16),
  Passing where it rests on long pass completion alone, xPass deferred (§12.9, §13.9, §14).

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import (
    MEASUREMENT_SOURCE, REGISTRY, REGISTRY_VERSION, SPECS, MetricSpec,
    is_signature_holder,
)
from eleveniq.export import display as X
from eleveniq.export import words as W
from eleveniq.export.display import (  # noqa: F401  (re-exported: the page's display rules)
    DENOMINATOR_NOUN,
    POPULATION_REFERENCE_METRIC,
    SECOND_SIGNATURE_TEXT,
    SPECS_BY_COLUMN,
    STATUS_TEXT,
    dim_status_text,
    format_number,
    format_pct,
    format_value,
    status_text,
    uncoloured,
    withheld_reason,
)

_ROOT = Path(__file__).resolve().parents[1]
PERCENTILES_PATH = _ROOT / "data" / "processed" / "player_season_percentiles.parquet"
SEASON_PATH = _ROOT / "data" / "processed" / "player_season_stats.parquet"
DNA_PATH = _ROOT / "data" / "processed" / "player_dna.parquet"
DIMENSIONS_PATH = _ROOT / "data" / "processed" / "player_dna_dimensions.parquet"
OUTPUT_DIR = _ROOT / "data" / "processed" / "reports"

KEY: tuple[str, ...] = C.SEASON_KEY
PRIMARY, SECOND = "primary", "second"

# Display rules (formats, status texts, ratio nouns, the population reference metric) live
# in eleveniq.export.display, shared with the web export; the trace test re-applies them.

SIGNATURE_COUNT_COLUMNS: tuple[str, ...] = tuple(sorted(
    {s.count_column for s in SPECS if s.count_column
     and any(c.status == "signature" for (col, _), c in REGISTRY.cells.items() if col == s.column)}))
IDENTITY_COLUMNS: tuple[str, ...] = (
    *KEY, "player_name", "team_name", "position_group", "second_position_group",
    "minutes_played", "minutes_nominal_equiv", "meets_min_minutes", *SIGNATURE_COUNT_COLUMNS)

FAMILY_LABELS: dict[str, str] = {"Progression": "Progressive passing",
                                 "Carrying": "Carrying & dribbling"}   # ADR 004 §10.7
FAMILY_ORDER: tuple[str, ...] = tuple(dict.fromkeys(s.family for s in SPECS))
GROUP_PLURAL: dict[str, str] = X.GROUP_ABBR_PLURAL
# Restart deliveries include throw-ins (ADR 004 §10.6): no "set-piece" label until the
# FB-WB throw-in share is measured (plan 005 backlog). Overrides the registry display name.
METRIC_LABELS: dict[str, str] = dict(W.METRIC_LABELS)
LONG_PASS_NOTE = W.LONG_PASS_NOTE.full

# Static text that carries a digit; the trace test accepts exactly these.
STATIC_REFS: frozenset[str] = frozenset({"D9", "D5", "ADR 004 §11", "ADR 004 §12", "M4e", "M4f4",
                                         ">99", "<1", "25", "50", "75"})


# The inputs cannot produce an honest page (stale registry, ambiguous player, ...); the shared
# display rules raise the same error.
PageError = X.DisplayError


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


def load_dna(season_id: int, percentiles_path: Path = PERCENTILES_PATH,
             season_path: Path = SEASON_PATH, dna_path: Path = DNA_PATH,
             dimensions_path: Path = DIMENSIONS_PATH) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The season's DNA vector and dimension rows, after the provenance guards: built with
    this registry and source, on the configured basis, from these season stats and these
    percentiles (ADR 004 §12.7)."""
    want = {"registry_version": REGISTRY_VERSION, "measurement_source": MEASUREMENT_SOURCE,
            "source": MEASUREMENT_SOURCE, "dna_percentile_basis": config.DNA_PERCENTILE_BASIS,
            "season_parquet_sha256": C.file_sha256(season_path),
            "percentiles_parquet_sha256": C.file_sha256(percentiles_path)}
    out = []
    for path in (dna_path, dimensions_path):
        frame = pl.read_parquet(path).filter(pl.col("season_id") == season_id)
        if frame.is_empty():
            raise PageError(f"no DNA rows for season_id {season_id} in {path.name}")
        for col, value in want.items():
            got = set(frame[col].unique().to_list())
            if got != {value}:
                raise PageError(f"{path.name}: {col} {sorted(map(str, got))} ≠ {str(value)[:16]}…: "
                                "rebuild it with build_player_dna")
        out.append(frame)
    return out[0], out[1]


# ── View ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Player:
    identity: Mapping[str, Any]
    rows: Mapping[tuple[str, str], Mapping[str, Any]]   # (cohort_role, metric) -> parquet row
    # player_dna.parquet rows by metric and player_dna_dimensions.parquet rows by family;
    # empty when the row has no DNA vector (not a primary scored row).
    dna: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    dims: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

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


def _of(frame: pl.DataFrame | None, key: tuple[Any, ...]) -> list[dict[str, Any]]:
    if frame is None or frame.is_empty():
        return []
    return frame.filter(pl.all_horizontal(pl.col(k) == v for k, v in zip(KEY, key))).to_dicts()


def _check_dna(ident: Mapping[str, Any], by: Mapping[tuple[str, str], Mapping[str, Any]],
               dna: Mapping[str, Mapping[str, Any]], dims: Mapping[str, Mapping[str, Any]]) -> None:
    """The DNA rows must be the registry's DNA list for the player's group, carry the same
    group, and copy pct_local / n_local from the percentiles (ADR 004 §12.7)."""
    name, group = ident["player_name"], ident["position_group"]
    if not dna and not dims:
        return
    if set(dna) != set(REGISTRY.dna_metrics(group)):
        raise PageError(f"{name}: DNA metrics differ from the registry's {group} list")
    families = {SPECS_BY_COLUMN[m].family for m in dna}
    if set(dims) != families:
        raise PageError(f"{name}: dimension families {sorted(dims)} ≠ DNA families {sorted(families)}")
    for row in (*dna.values(), *dims.values()):
        if row["position_group"] != group:
            raise PageError(f"{name}: position group differs between the DNA and season parquets")
    withheld = D.withheld_for(group)
    for fam, row in dims.items():
        status = row["score_status"]
        if (fam in withheld and status != withheld[fam]) or (
                fam not in withheld and status in D.WITHHELD_STATUSES):
            raise PageError(f"{name}: {fam} score_status {status!r} does not follow the configured "
                            "withheld dimensions: rebuild with build_player_dna")
    for m, row in dna.items():
        p = by[(PRIMARY, m)]
        if (row["pct_local"], row["n_local"]) != (p["pct_local"], p["n_local"]):
            raise PageError(f"{name}: {m} pct_local / n_local differ between the DNA and "
                            "percentiles parquets")


def build_players(pct: pl.DataFrame, identity: pl.DataFrame, selectors: Sequence[Selector],
                  dna: pl.DataFrame | None = None, dims: pl.DataFrame | None = None) -> list[Player]:
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
        dna_rows = {r["metric"]: r for r in _of(dna, key)}
        dim_rows = {r["family"]: r for r in _of(dims, key)}
        _check_dna(ident, by, dna_rows, dim_rows)
        players.append(Player(ident, by, dna_rows, dim_rows))
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


def dim_span(row: Mapping[str, Any], column: str, text: str, cls: str = "num") -> str:
    """A player_dna_dimensions.parquet cell; "members" is shown as its count."""
    ref = "|".join(str(row[k]) for k in (*KEY, "family")) + f"|{column}"
    return f'<span class="{cls}" data-dim="{esc(ref)}">{esc(text)}</span>'


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
    """Colour class: direction only colours (ADR 004 §10.7), except uncoloured cells."""
    if uncoloured(spec, team_style_biased):
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
            seen.append(reg_span(metric, group, f"notes|{i}", R.note_text(note).full))
        if cell.status != "dna":
            fixed = SPECS_BY_COLUMN[metric].fixed_display
            why = (reg_span(metric, group, "fixed_display.reason", fixed.reason)
                   if fixed is not None and "fixed_display" in cell.reasons
                   else reg_span(metric, group, "reasons",
                                 ", ".join(R.reason_text(r) for r in cell.reasons)))
            seen.append(f'{esc(GROUP_PLURAL[group])}: {why}')
    if metric == "long_pass_completion_pct":
        seen.insert(0, esc(LONG_PASS_NOTE))
    return "<br>".join(dict.fromkeys(seen))


def direction_label(spec: MetricSpec) -> str:
    """The label under the metric name: long pass completion reads as team style (§11.5),
    so it gets no good/bad direction."""
    if spec.column == "long_pass_completion_pct":
        return W.NO_READING
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
        if section == "dna" and any(family in p.dims for p in players):
            cells = "".join(f'<td class="pc">{dimension_html(p, family, mixed)}</td>' for p in players)
            out.append(f'<tr class="dimrow"><th class="metric">Dimension score'
                       f'<div class="dir">mean local percentile of the directional members</div></th>'
                       f'{cells}<td class="notes"></td></tr>')
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


def dimension_html(p: Player, family: str, mixed: bool) -> str:
    """A dimension score with its member count ("based on 2 metrics", ADR 004 §12.8), or the
    reason it is null; a withheld family is "withheld (<reason>)" (§12.9, §13.8, §14)."""
    group = GROUP_PLURAL.get(p.group, p.group)
    if not p.dims:
        return '<span class="reason">no DNA vector for this row</span>'
    if family not in p.dims:
        return f'<span class="reason">not a DNA family for {esc(group)}</span>'
    row = p.dims[family]
    status = row["score_status"]
    if status in D.WITHHELD_STATUSES:
        return f'<span class="reason">withheld ({esc(withheld_reason(family, p.group, status))})</span>'
    if status != "ok":
        return f'<span class="reason">{esc(dim_status_text(status))}</span>'
    if row["score"] is None or not row["members"]:
        raise PageError(f"{p.identity['player_name']}: {family} score ok without a score or members")
    k = len(row["members"])
    vs = f' <span class="vs">vs {esc(group)}</span>' if mixed else ""
    names = ", ".join(metric_label(SPECS_BY_COLUMN[m]) for m in row["members"])
    return (f'{dim_span(row, "score", format_pct(row["score"]), "num pct")}{vs}'
            f'<div class="muted dimn">based on {dim_span(row, "members", str(k))} '
            f'metric{"" if k == 1 else "s"}: {esc(names)}</div>')


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


# ── DNA radar (M4f4, ADR 004 §12) ─────────────────────────────────────────────────

RADAR_R = 190.0          # radius of the 100th-percentile ring, in view-box units
RADAR_C = 405.0          # centre (x = y); the view box is 2·RADAR_C square
RADAR_RINGS: tuple[int, ...] = (25, 50, 75, 100)
SERIES: tuple[str, ...] = ("s1", "s2", "s3")    # per-player colour + dash pattern (CSS)


def radar_axes(group: str) -> tuple[str, ...]:
    """The group's DNA metrics, grouped by family (FAMILY_ORDER), registry order within."""
    order = {s.column: i for i, s in enumerate(SPECS)}
    return tuple(sorted(REGISTRY.dna_metrics(group), key=lambda m: (
        FAMILY_ORDER.index(SPECS_BY_COLUMN[m].family), order[m])))


def radar_angle(i: int, n: int) -> float:
    """Axis i of n, in radians: the first axis points up, then clockwise."""
    return -math.pi / 2 + 2 * math.pi * i / n


def _polar(angle: float, r: float) -> tuple[float, float]:
    return RADAR_C + r * math.cos(angle), RADAR_C + r * math.sin(angle)


def radar_point(i: int, n: int, pct: float) -> tuple[float, float]:
    """The vertex of a stored local percentile on axis i: radius pct / 100 · RADAR_R."""
    return _polar(radar_angle(i, n), RADAR_R * pct / 100)


def _xy(pt: tuple[float, float]) -> str:
    return f"{pt[0]:.2f},{pt[1]:.2f}"


def radar_outline(points: Sequence[tuple[float, float] | None]) -> tuple[str, bool]:
    """The SVG path through the present vertices in axis order, and whether it is closed.
    A missing vertex breaks the line on both sides: a gap, never a vertex at zero
    (ADR 004 §12.2). A lone vertex between two gaps is drawn as its marker only."""
    if all(pt is not None for pt in points):
        return "M" + " L".join(_xy(pt) for pt in points if pt) + " Z", True
    n, start = len(points), next(i for i, pt in enumerate(points) if pt is None)
    runs: list[list[tuple[float, float]]] = [[]]
    for k in range(1, n + 1):
        pt = points[(start + k) % n]
        if pt is None:
            runs.append([])
        else:
            runs[-1].append(pt)
    return " ".join("M" + " L".join(_xy(pt) for pt in run) for run in runs if len(run) > 1), False


def _family_runs(axes: Sequence[str]) -> list[tuple[str, int, int]]:
    runs: list[tuple[str, int, int]] = []
    for i, m in enumerate(axes):
        fam = SPECS_BY_COLUMN[m].family
        if runs and runs[-1][0] == fam:
            runs[-1] = (fam, runs[-1][1], i)
        else:
            runs.append((fam, i, i))
    return runs


def _rotated(x: float, y: float, deg: float, cls: str, anchor: str, text: str) -> str:
    return (f'<text class="{cls}" x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" transform="rotate({deg:.2f} {x:.2f} {y:.2f})">{text}</text>')


def _radar_frame(axes: Sequence[str], group: str) -> str:
    """Family sectors and labels, rings, spokes and axis labels."""
    n, step = len(axes), 2 * math.pi / len(axes)
    out = []
    for k, (fam, i0, i1) in enumerate(_family_runs(axes)):
        a0, a1 = radar_angle(i0, n) - step / 2, radar_angle(i1, n) + step / 2
        if k % 2 == 0:
            large = int(a1 - a0 > math.pi)
            out.append(f'<path class="rd-sec" d="M{_xy((RADAR_C, RADAR_C))} L{_xy(_polar(a0, RADAR_R))} '
                       f'A{RADAR_R:.2f},{RADAR_R:.2f} 0 {large} 1 {_xy(_polar(a1, RADAR_R))} Z"/>')
        mid = (a0 + a1) / 2
        x, y = _polar(mid, RADAR_R + 16)
        deg = math.degrees(mid) + (90 if math.sin(mid) <= 0 else -90)
        out.append(_rotated(x, y, deg, "rd-fam", "middle", esc(FAMILY_LABELS.get(fam, fam))))
    for v in RADAR_RINGS:
        out.append(f'<circle class="rd-ring" cx="{RADAR_C:.2f}" cy="{RADAR_C:.2f}" r="{RADAR_R * v / 100:.2f}"/>')
    for v in RADAR_RINGS[:-1]:
        x, y = _polar(radar_angle(0, n) + step / 2, RADAR_R * v / 100)
        out.append(f'<text class="rd-tick" x="{x:.2f}" y="{y:.2f}" text-anchor="middle" '
                   f'dominant-baseline="middle"><tspan data-static="">{esc(str(v))}</tspan></text>')
    for i, m in enumerate(axes):
        spec = SPECS_BY_COLUMN[m]
        plain = uncoloured(spec, REGISTRY.cell(m, group).team_style_biased)
        cls = " style" if plain else ""
        a = radar_angle(i, n)
        out.append(f'<line class="rd-spoke{cls}" x1="{RADAR_C:.2f}" y1="{RADAR_C:.2f}" '
                   f'x2="{_polar(a, RADAR_R)[0]:.2f}" y2="{_polar(a, RADAR_R)[1]:.2f}"/>')
        label = metric_label(spec) + (" (lower better)" if spec.direction == "lower_better"
                                      and not plain else "")
        x, y = _polar(a, RADAR_R + 32)
        right = math.cos(a) >= -1e-9
        deg = math.degrees(a) if right else math.degrees(a) + 180
        out.append(_rotated(x, y, deg, f"rd-lab{cls}", "start" if right else "end", esc(label)))
    return "".join(out)


def _radar_player(p: Player, idx: int, axes: Sequence[str]) -> tuple[str, list[str]]:
    """One player's outline, vertices and gap markers, and the notes for the gaps."""
    n, cls, name = len(axes), SERIES[idx], p.identity["player_name"]
    points: list[tuple[float, float] | None] = []
    marks, notes = [], []
    for i, m in enumerate(axes):
        row = p.dna[m]
        label = metric_label(SPECS_BY_COLUMN[m])
        if row["metric_status"] == "ok":
            if row["pct_local"] is None:
                raise PageError(f"{name}: {m} is ok without a local percentile")
            pt = radar_point(i, n, row["pct_local"])
            points.append(pt)
            ref = "|".join(str(row[k]) for k in KEY) + f"|{m}|pct_local"
            marks.append(f'<circle class="rd-v {cls}" data-dna="{esc(ref)}" cx="{pt[0]:.2f}" '
                         f'cy="{pt[1]:.2f}" r="4.5"><title>{esc(name)} — {esc(label)}: '
                         f'{esc(format_pct(row["pct_local"]))} (n {esc(row["n_local"])})</title></circle>')
            continue
        points.append(None)
        a = radar_angle(i, n)
        cx, cy = _polar(a, RADAR_R + 5 + 9 * idx)
        ref = "|".join(str(row[k]) for k in KEY) + f"|{m}"
        marks.append(f'<g class="rd-gap {cls}" data-gap="{esc(ref)}"><title>{esc(name)} — '
                     f'{esc(label)}: not plotted ({esc(status_text(row["metric_status"]))})</title>'
                     f'<path d="M{cx - 3.5:.2f},{cy - 3.5:.2f} L{cx + 3.5:.2f},{cy + 3.5:.2f} '
                     f'M{cx - 3.5:.2f},{cy + 3.5:.2f} L{cx + 3.5:.2f},{cy - 3.5:.2f}"/></g>')
        why = _status_html(p.row(m), "local", SPECS_BY_COLUMN[m])
        notes.append(f'<li>{esc(name)} — {esc(label)}: not plotted ({why}). A gap in the '
                     'outline and a cross at the rim, not a zero.</li>')
    path, closed = radar_outline(points)
    line = (f'<path class="rd-line {cls}{" closed" if closed else ""}" d="{path}"/>' if path else "")
    return line + "".join(marks), notes


def radar_section(players: Sequence[Player]) -> str:
    """The DNA radar (ADR 004 §12): local percentiles of the group's DNA metrics, drawn only
    when the players share a position group, with the dimension scores under it."""
    head = '<section class="rd"><h2>DNA radar</h2>'
    groups = {p.group for p in players}
    if len(groups) > 1:
        return (head + '<p class="muted">No radar: the players are in different position groups, '
                "and a DNA radar compares one group's metrics on one scale.</p></section>")
    group = next(iter(groups))
    drawn = [p for p in players if p.dna]
    skipped = []
    for p in players:
        if p.dna:
            continue
        ref = p.row(POPULATION_REFERENCE_METRIC)
        if ref["percentile_status"] == "scored":
            raise PageError(f"{p.identity['player_name']}: a scored row without a DNA vector")
        skipped.append(f'<li>{esc(p.identity["player_name"])}: no DNA vector ('
                       f'{src_span(ref, "percentile_status", status_text(ref["percentile_status"]), "reason")})</li>')
    if not drawn:
        return head + f'<ul class="muted">{"".join(skipped)}</ul></section>'
    axes = radar_axes(group)
    body, notes = [], []
    for idx, p in enumerate(drawn):
        svg, gaps = _radar_player(p, idx, axes)
        body.append(svg)
        notes += gaps
    legend = []
    for idx, p in enumerate(drawn):
        i = p.identity
        ref = p.row(POPULATION_REFERENCE_METRIC)
        legend.append(f'<li><svg class="rd-key" viewBox="0 0 30 10" aria-hidden="true"><line class="rd-line '
                      f'{SERIES[idx]}" x1="1" y1="5" x2="29" y2="5"/></svg> '
                      f'<strong>{id_span(i, "player_name", i["player_name"])}</strong> '
                      f'({id_span(i, "team_name", i["team_name"])}) — vs '
                      f'{competition_label(i["competition_id"], i["season_id"])} '
                      f'{esc(GROUP_PLURAL[group])} (n {src_span(ref, "n_local", str(ref["n_local"]))})</li>')
    families = [fam for fam, _, _ in _family_runs(axes)]
    rows = "".join(
        f'<tr><th class="metric">{esc(FAMILY_LABELS.get(f, f))}</th>'
        + "".join(f'<td class="pc">{dimension_html(p, f, False)}</td>' for p in drawn) + "</tr>"
        for f in families)
    names = " vs ".join(p.identity["player_name"] for p in drawn)
    size = 2 * RADAR_C
    return (head + f'<p class="muted">League-local percentiles of the {esc(GROUP_PLURAL[group])}\' '
            f'DNA metrics ({static("ADR 004 §12")}): each player is ranked against the same group in '
            'their own league. Outward means more, not better. Directional axes are in ink (a '
            'lower-better axis says so); ' + esc(W.GREY_RULE) + ' with a dashed spoke and carry no '
            'good/poor reading. Hover a vertex for its value.</p>'
            f'<ul class="rd-legend">{"".join(legend)}</ul>'
            f'<svg class="rd-chart" viewBox="0 0 {size:.0f} {size:.0f}" role="img" '
            f'aria-label="DNA radar: {esc(names)}">{_radar_frame(axes, group)}{"".join(body)}</svg>'
            + (f'<ul class="muted">{"".join(skipped + notes)}</ul>' if skipped or notes else "")
            + '<h3>Dimension scores</h3><div class="scroll"><table><thead><tr><th>Family</th>'
            + "".join(f"<th>{esc(p.identity['player_name'])}</th>" for p in drawn)
            + f"</tr></thead><tbody>{rows}</tbody></table></div></section>")


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e3e1da;--card:#ffffff;--basis:#f1efe6;
--good:#2f7d5b;--mid:#9a8f6a;--poor:#b4513a;--neutral:#7d8590;--tag:#e8e4d6;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a19a;--line:#33322e;
--card:#1f1f1c;--basis:#2a2924;--good:#5fb68c;--mid:#c2b68a;--poor:#e0846c;--neutral:#9aa3ad;--tag:#3a382f;
--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
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
.rd-chart{width:100%;max-width:780px;height:auto;display:block;margin:8px auto}
.rd-sec{fill:var(--basis)}.rd-ring{fill:none;stroke:var(--line)}.rd-spoke{stroke:var(--line)}
.rd-spoke.style{stroke-dasharray:3 3}.rd-lab{font-size:11px;fill:var(--fg)}
.rd-lab.style{fill:var(--muted);font-style:italic}.rd-tick{font-size:10px;fill:var(--muted)}
.rd-fam{font-size:10px;fill:var(--muted);font-weight:600;letter-spacing:.06em;text-transform:uppercase}
.rd-line{fill:none;stroke-width:2;stroke-linejoin:round}.rd-line.closed{fill-opacity:.07}
.rd-line.s2{stroke-dasharray:7 4}.rd-line.s3{stroke-dasharray:2 3}
.rd-line.s1,.rd-gap.s1{stroke:var(--s1)}.rd-line.s2,.rd-gap.s2{stroke:var(--s2)}.rd-line.s3,.rd-gap.s3{stroke:var(--s3)}
.rd-line.closed.s1{fill:var(--s1)}.rd-line.closed.s2{fill:var(--s2)}.rd-line.closed.s3{fill:var(--s3)}
.rd-v{stroke:var(--card);stroke-width:1.5}.rd-v.s1{fill:var(--s1)}.rd-v.s2{fill:var(--s2)}.rd-v.s3{fill:var(--s3)}
.rd-gap{stroke-width:2.5;stroke-linecap:round}
.rd-legend{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 24px}.rd-key{width:30px;height:10px;vertical-align:middle}
.dimn{font-size:12px}tr.dimrow td,tr.dimrow th{background:var(--basis)}
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
              f' · {static("ADR 004 §11")} · {static("ADR 004 §12")} · {static("M4e")} · {static("M4f4")}</footer>')
    legend = ('<p class="muted">Raw value, then the local (league × group) and global (group across '
              'the pool leagues) percentile with its population size n. The registry basis is '
              'highlighted and drawn as the bar. Percentiles rank the raw value (high = more); the '
              'bar colour follows the metric direction; ' + esc(W.GREY_RULE_TABLE)
              + f'. {static(">99")} / {static("<1")} mark rows ranked against a population they are not in. '
              'A family\'s dimension score is the mean local percentile of its directional DNA '
              'members and says how many metrics it rests on.</p>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(names)}</title><style>{CSS}</style></head><body><main>"
            f"<h1>{esc(names)}</h1>{warn}<div class=\"cards\">"
            f"{''.join(header_card(p) for p in players)}</div>{legend}{radar_section(players)}{families}"
            f"{signature_section(players)}{second_group_section(players)}{footer}"
            "</main></body></html>\n")


def generate(season_id: int, selectors: Sequence[str], *, percentiles_path: Path = PERCENTILES_PATH,
             season_path: Path = SEASON_PATH, dna_path: Path = DNA_PATH,
             dimensions_path: Path = DIMENSIONS_PATH) -> tuple[str, list[Player]]:
    pct, identity = load_inputs(season_id, percentiles_path, season_path)
    dna, dims = load_dna(season_id, percentiles_path, season_path, dna_path, dimensions_path)
    players = build_players(pct, identity, [parse_selector(s) for s in selectors], dna, dims)
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
