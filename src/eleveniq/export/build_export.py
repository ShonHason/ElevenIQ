"""
The web data export (ADR 005 §1–§2, plan 006 W1): processed parquets + registry → versioned,
schema-validated JSON under export/<contract major>/.

Python computes; the site displays. Every number, phrase, rank, bin and reason is final here;
each number ships with its display text. The exporter reads the parquets and analytics/ and
never recomputes a statistic the pipeline owns.

Files (contract 1.0.0; schemas in contract/v1/):
- manifest.json: stamps, the exporter's git commit, every file with its sha256;
- search-index.json: one entry per season row (key, names, accent-folded search key, team,
  league, group, minutes, profile status);
- groups/<group>.json: what every row of a position group shares per metric (label, family,
  direction, registry status, notes, reasons, colour and gap rules, withheld-family tag), in
  registry metric order (the gap tie-break); the player files list metrics in the same order;
- players/<key>.json: one per season row: identity and context, every metric's row facts
  (value, local / global percentile with n, status, phrase, colour bin, population), the
  second group's display-only rows, signature traits, the DNA vector and dimension scores,
  and the neighbours;
- style-maps/<group>.json: the axes (the user's names, notes, variance shown), every plotted
  row (coordinates, fine role, lean, top pushes), partial rows, "Played for" and the movers;
- labels.json: group, family and status texts and the shared words.
Compare mode has no file and no pair data (user, 2026-10-09): it reads two player files.

Keys: a row key is "<player_id>-<team_id>-<competition_id>-<season_id>" (ids only; never a
name: plan 006 decision 9). No wall-clock time anywhere: two runs on the same data and commit
are byte-identical.

  .venv/bin/python -m eleveniq.export.build_export [--out export/v1]
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import polars as pl

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics import movers as M
from eleveniq.analytics import pairs as P
from eleveniq.analytics import registry as R
from eleveniq.analytics import style_map as S
from eleveniq.analytics.dna import RowDna, dna_specs
from eleveniq.analytics.registry import REGISTRY, REGISTRY_VERSION, SPECS, is_signature_holder
from eleveniq.analytics.roles import dmcm_slot, fine_role
from eleveniq.export import display as X
from eleveniq.export import words as W

CONTRACT_VERSION = "1.0.0"
CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contract" / "v1"
ROOT = Path(__file__).resolve().parents[3]
PROCESSED = ROOT / "data" / "processed"
SEASON_PATH = PROCESSED / "player_season_stats.parquet"
PERCENTILES_PATH = PROCESSED / "player_season_percentiles.parquet"
DNA_PATH = PROCESSED / "player_dna.parquet"
DIMENSIONS_PATH = PROCESSED / "player_dna_dimensions.parquet"
POSITIONS_PATH = PROCESSED / "player_season_positions.parquet"
MATCH_PATH = ROOT / "data" / "interim" / "player_match_stats.parquet"
OUT_DIR = ROOT / "export" / "v1"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "web"
# The committed fixture (plan 006 W1 item 5; ADR 005 §7): (player_id, team_id, competition_id)
# of the 2015/16 rows the Playwright flows use, plus Kanté's neighbours (flow 1 opens one).
# Only Kanté's neighbours have files: another row's neighbour links point outside the fixture,
# so the web flows (W2–W5) open neighbours from Kanté only; their tests name these same ids.
FIXTURE_ROWS: tuple[tuple[int, int, int], ...] = (
    (3961, 22, 2),      # Kanté (flow 1)
    (5203, 217, 11),    # Busquets
    (4751, 39, 2),      # Carrick
    (4926, 220, 11),    # Isco (nickname)
    (19677, 220, 11),   # Benzema (ST)
    (5503, 217, 11),    # Messi
    (4320, 217, 11),    # Neymar
    (3496, 1, 2),       # Özil
    (3668, 1, 2),       # Walcott (partial DNA row)
    (3244, 29, 2),      # Stones (CB)
    (3813, 22, 2),      # Morgan (CB)
    (3052, 36, 2),      # Zabaleta: an FB-WB near-gate row (900 ≤ nominal < 1,350)
    (13286, 41, 2), (13286, 165, 7),    # Lamine Koné: a mover across leagues (combined dot)
    (3057, 26, 2), (3057, 37, 2),       # Shelvey: a mover within a league (combined dot)
    (2955, 143, 7),     # below the minutes gate
    (3603, 30, 2),      # a second-group (display-only) row
    (2947, 137, 7),     # a goalkeeper
)
FIXTURE_NEIGHBOURS_OF = (3961, 22, 2)
KEY = P.KEY
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
FLOAT_DP = 6
logger = logging.getLogger(__name__)
# The files write() manages under the export directory (and nothing else).
MANAGED_DIRS = ("players", "groups", "style-maps", "gems")
MANAGED_FILES = ("manifest.json", "search-index.json", "labels.json")


class ExportError(ValueError):
    """The inputs cannot produce an honest export (stale stamps, mixed sources, ...)."""


# ── Small helpers ──────────────────────────────────────────────────────────────────

def row_key(k: tuple) -> str:
    """Ids only. The source is left out: load_inputs refuses more than one source (CLAUDE.md);
    a second source needs it in the key (a contract major version)."""
    return f"{k[0]}-{k[1]}-{k[2]}-{k[3]}"


def rnd(v: float | None, dp: int = FLOAT_DP) -> float | None:
    return None if v is None else round(float(v), dp)


def file_sha256(path: Path) -> str:
    return C.file_sha256(path)


def dumps(doc: Any) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        logger.warning("git commit unavailable (%s): the manifest says \"unknown\"", e)
        return "unknown"
    return out + ("-dirty" if dirty else "")


def league_label(cid: int, sid: int) -> str:
    for cs in config.COMPETITION_SEASONS:
        if (cs.competition_id, cs.season_id) == (cid, sid):
            return cs.label
    raise ExportError(f"competition {cid} season {sid} is not in config.COMPETITION_SEASONS")


def display_name(name: str, nickname: str | None) -> str:
    """The nickname when present (stripped; plan 006 G1 decision 2), else the name."""
    nick = (nickname or "").strip()
    return nick or name


# ── Inputs ───────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Inputs:
    season: pl.DataFrame
    pct: pl.DataFrame
    dna: pl.DataFrame
    dims: pl.DataFrame
    positions: pl.DataFrame
    match: pl.DataFrame
    stamps: Mapping[str, Any]


def load_inputs(season_path: Path = SEASON_PATH, percentiles_path: Path = PERCENTILES_PATH,
                dna_path: Path = DNA_PATH, dimensions_path: Path = DIMENSIONS_PATH,
                positions_path: Path = POSITIONS_PATH, match_path: Path = MATCH_PATH) -> Inputs:
    """Read the parquets and refuse any that were not built from each other with this
    registry, source and DNA basis (ADR 004 §12.7; ADR 005 §2)."""
    season = pl.read_parquet(season_path)
    pct = pl.read_parquet(percentiles_path)
    dna = pl.read_parquet(dna_path)
    dims = pl.read_parquet(dimensions_path)
    season_sha = file_sha256(season_path)
    pct_sha = file_sha256(percentiles_path)
    want = {"registry_version": REGISTRY_VERSION, "measurement_source": R.MEASUREMENT_SOURCE,
            "source": R.MEASUREMENT_SOURCE, "season_parquet_sha256": season_sha}
    for name, frame, cols in (("percentiles", pct, want),
                              ("player_dna", dna, {**want, "percentiles_parquet_sha256": pct_sha,
                                                   "dna_percentile_basis": config.DNA_PERCENTILE_BASIS}),
                              ("dimensions", dims, {**want, "percentiles_parquet_sha256": pct_sha,
                                                    "dna_percentile_basis": config.DNA_PERCENTILE_BASIS})):
        for col, value in cols.items():
            got = set(frame[col].unique().to_list())
            if got != {value}:
                raise ExportError(f"{name}: {col} {sorted(map(str, got))} ≠ {str(value)[:16]}…: "
                                  "rebuild the pipeline stages in order")
    if set(season["source"].unique().to_list()) != {R.MEASUREMENT_SOURCE}:
        raise ExportError("season rows from more than one source (CLAUDE.md: one source only)")
    positions = pl.read_parquet(positions_path)
    from eleveniq.pipeline.build_player_season import check_position_minutes
    check_position_minutes(positions, season)
    match = pl.read_parquet(match_path, columns=[*KEY, "match_date", "minutes_played"])
    stamps = {
        "contract_version": CONTRACT_VERSION,
        "registry_version": REGISTRY_VERSION,
        "measurement_source": R.MEASUREMENT_SOURCE,
        "source": R.MEASUREMENT_SOURCE,
        "season_ids": sorted(season["season_id"].unique().to_list()),
        "season_parquet_sha256": season_sha,
        "percentiles_parquet_sha256": pct_sha,
        "dna_parquet_sha256": file_sha256(dna_path),
        "positions_parquet_sha256": file_sha256(positions_path),
        "match_parquet_sha256": file_sha256(match_path),
    }
    return Inputs(season, pct, dna, dims, positions, match, stamps)


# ── Metrics ──────────────────────────────────────────────────────────────────────

SPECS_IN_ORDER = list(SPECS)                    # registry metric order (the gap tie-break)
ORDER = {s.column: i for i, s in enumerate(SPECS_IN_ORDER)}


def _population_text(rows: Mapping[str, Mapping[str, Any]], row: Mapping[str, Any], scope: str,
                     spec: R.MetricSpec, cell: R.Cell | None) -> str | None:
    """The ratio population sentence: "n 64 of 157 STs with ≥ 43 long passes" (ADR 004
    §11.5); for any other metric "n 157"; None without an n."""
    n = row[f"n_{scope}"]
    if n is None:
        return None
    if spec.ratio is None or cell is None or cell.min_attempts is None:
        return f"n {n}"
    ref = rows[X.POPULATION_REFERENCE_METRIC][f"n_{scope}"]
    if ref is None:
        raise ExportError(f"{row['player_id']}: reference metric has no {scope} n")
    return (f"n {n} of {ref} {X.GROUP_ABBR_PLURAL[row['position_group']]} with ≥ "
            f"{cell.min_attempts} {X.DENOMINATOR_NOUN[spec.column]}")


def _scope(rows: Mapping[str, Mapping[str, Any]], row: Mapping[str, Any], scope: str,
           spec: R.MetricSpec, cell: R.Cell | None, biased: bool, zero_tie: bool,
           where: Mapping[str, str]) -> dict:
    # The shown percentile (W0 decision 1, user 2026-10-10): direction-adjusted, so a
    # lower-better metric shows 100 − p as its number, phrase and bar (dispossessed 78 → 22);
    # the raw value is unchanged. At the contract's precision, so text, phrase and bin all read
    # the number the file carries (stats-reviewer W1d).
    raw = row[f"pct_{scope}"]
    p = rnd(X.adjusted(spec, raw)) if raw is not None else None
    status = row[f"status_{scope}"]
    signature = row["registry_status"] == "signature"
    shown = p is not None and not zero_tie and not signature
    # Holder / non-holder, never a percentile (ADR 004 §11.5). Every outfield row's file has
    # the holder section "below"; an unscored row's entry there says "not assessed: <status>".
    if signature:
        text = W.SIGNATURE_ROW if row["cohort_role"] == "primary" else X.SECOND_SIGNATURE_TEXT
    elif zero_tie and p is not None:
        text = W.ZERO_TIE   # replaces a stored percentile only; an unscored row keeps its status
    else:
        text = None if p is not None else X.status_text(status)
    return {
        "pct": p if shown else None,
        "pct_text": X.format_pct(p) if shown else None,
        "phrase": _phrase(spec, biased, p) if shown else None,
        # The band replaces the number in every cell (display round, user 2026-10-10); the
        # number is in `note`, for the popover. A hollow-bar cell takes the neutral band.
        "band": W.band(p, _neutral(spec, biased)) if shown else None,
        "note": (((W.PCT_NOTE_LOCAL if scope == "local" else W.PCT_NOTE_GLOBAL).format(
            ordinal=W.ordinal_percentile(X.format_pct(p)), **where))
            + (f" ({W.LOWER_BETTER_NOTE})" if spec.direction == "lower_better" else ""))
            if shown else None,
        "bin": X.shown_bin(spec, biased, p) if shown else None,
        "n": row[f"n_{scope}"],
        "population_text": _population_text(rows, row, scope, spec, cell),
        "status": status,
        "status_text": text,            # a number or a reason, never neither
    }


def _neutral(spec: R.MetricSpec, biased: bool) -> bool:
    """Hollow bar = neutral words (user, 2026-10-10): every uncoloured cell (style, long pass
    completion, associated with team possession) reads higher than most / around average /
    lower than most, never top / bottom N%, so number, phrase and bar agree."""
    neutral = X.uncoloured(spec, biased)
    if neutral and spec.direction == "lower_better":
        # The neutral words read the shown (flipped) percentile; no such cell exists yet.
        raise ExportError(f"{spec.column}: an uncoloured lower-better cell has no neutral reading")
    return neutral


def _phrase(spec: R.MetricSpec, biased: bool, p: float) -> str:
    """The phrase of a shown percentile: neutral on a hollow-bar cell, else top / bottom N%."""
    return W.style_words(p) if _neutral(spec, biased) else W.pct_words(p)[1]


def _where(row: Mapping[str, Any]) -> dict[str, str]:
    """League and group words for a percentile note."""
    return {"league": league_label(row["competition_id"], row["season_id"]),
            "group": W.GROUP_TEXT.get(row["position_group"], row["position_group"])}


def metric_meta(metric: str, group: str, held: Mapping[str, str]) -> dict:
    """What every row of a group shares for one metric (groups/<group>.json): label, family,
    direction, registry status, notes, reasons, colour and gap rules, the withheld-family tag."""
    spec = X.SPECS_BY_COLUMN[metric]
    cell = REGISTRY.cell(metric, group) if group in GROUPS else None
    biased = bool(cell and cell.team_style_biased)
    status = cell.status if cell else None
    # Notes and display reasons as one list, each short text once (W3 fix round, user): the
    # team-possession reasons (the trigger and the majority rule) and the team-possession note
    # share the short "associated with team possession"; their full texts join in the popover.
    # A fixed_display spec reason is ADR shorthand, not page text: not exported (W3).
    items: list[R.NoteText] = []
    if metric == "long_pass_completion_pct":
        items.append(W.LONG_PASS_NOTE)
    items += [R.note_text(n) for n in (cell.notes if cell else ())]
    if cell is not None and cell.status != "dna" and R.R_FIXED not in cell.reasons:
        items += [W.reason_note(r) for r in cell.reasons]
    notes = W.merge_notes(items)
    fam = spec.family
    return {
        "metric": metric, "label": W.metric_label(metric, group), "family": fam,
        "order": ORDER[metric], "direction": spec.direction,
        # A hollow-bar metric has no good / poor reading, whatever its registry direction.
        "direction_text": (W.NO_READING if X.uncoloured(spec, biased) and spec.direction != "style"
                           else spec.direction.replace("_", " ")),
        "registry_status": status,
        "unit": W.unit_text(metric, spec.unit),
        "definition": W.METRIC_DEFINITIONS[metric],
        "uncoloured": X.uncoloured(spec, biased),
        # Option c on every measured cell (user, 2026-10-09): out when the registry's
        # possession check triggers on the cell's measurement, whatever step it left at.
        "gap_eligible": X.gap_eligible(spec, status, biased or bool(
            group in GROUPS and R.measured_possession_triggered(metric, group))),
        "notes": [{"short": n.short, "full": n.full} for n in notes],
        "withheld_tag": W.withheld_tag(fam, held[fam]) if fam in held else None,
    }


def _total_text(spec: R.MetricSpec, ident: Mapping[str, Any]) -> str | None:
    """The season total next to a value (display round, user): a per-90 metric's season count
    ("176 total"); a ratio's made / attempted counts ("1,812 / 2,215": the numerator over the
    denominator the pipeline divides by). Counts the parquets keep; no new statistic."""
    if spec.ratio is not None:
        r = spec.ratio
        num = ident[r.numerator]
        den = sum(ident[c] for c in r.denominator_plus) - sum(ident[c] for c in r.denominator_minus)
        dec = isinstance(num, float) and r.numerator in W.SUM_COLUMNS
        return f"{X.format_count(num, dec)} / {X.format_count(den)}"
    if spec.count_column:
        return f"{X.format_count(ident[spec.count_column], not spec.integer_count)} {W.TOTAL_WORD}"
    return None


def metric_row(rows: Mapping[str, Mapping[str, Any]], metric: str,
               ident: Mapping[str, Any] | None = None) -> dict:
    """One metric of one row (one cohort role): only what differs between rows. `ident` (the
    season row) adds the season total; second-group rows are called without it (no totals)."""
    row = rows[metric]
    spec = X.SPECS_BY_COLUMN[metric]
    group = row["position_group"]
    cell = REGISTRY.cell(metric, group) if row["registry_status"] else None
    biased = bool(cell and cell.team_style_biased)
    # A zero tie (user, 2026-10-09, option A): the registry's zero-heavy reason, or a measured
    # zero share above its threshold on any cell; it needs a stored percentile to replace (an
    # unscored row is not "tied"); a signature row shows no percentile at all.
    heavy = bool(cell and R.R_ZERO_HEAVY in cell.reasons) or (
        group in GROUPS and R.measured_zero_heavy(metric, group))
    zero_tie = bool(heavy and row["value"] == 0 and row["registry_status"] != "signature"
                    and (row["pct_local"] is not None or row["pct_global"] is not None))
    return {
        "value": rnd(row["value"]),
        "value_text": X.format_value(metric, row["value"]),
        "total_text": _total_text(spec, ident) if ident is not None else None,
        "local": _scope(rows, row, "local", spec, cell, biased, zero_tie, _where(row)),
        "global": _scope(rows, row, "global", spec, cell, biased, zero_tie, _where(row)),
        "basis": row["basis"],
        "zero_tie": zero_tie,
    }


RATE_EXPOSURE = "opponent_open_play_passes_while_on"


def defensive_rates(ident: Mapping[str, Any]) -> list[dict]:
    """The compare-mode defensive rate (user, 2026-10-10): each RATE_METRICS season count per 100
    opponent open-play passes made while the player was on the pitch (the pipeline's exposure
    column). A plain rate: no percentile, no adjustment; numerator and denominator kept beside
    it. None for a goalkeeper; a row without exposure has no rate (null, never 0)."""
    if ident["position_group"] == "GK":
        return []
    exposure = ident[RATE_EXPOSURE]
    out = []
    for c in W.RATE_METRICS:
        count = ident[c]
        rate = 100.0 * count / exposure if exposure else None
        shown = rnd(rate)
        out.append({"metric": f"{c}_p90", "count": count, "exposure": exposure,
                    "rate": shown, "rate_text": None if shown is None else f"{shown:.2f}"})
    return out


# Variants of one base metric (user, 2026-10-10): What stands out lists at most one of them.
SHOT_VARIANTS = frozenset({"shots_p90", "np_shots_p90", "shots_on_target_p90",
                           "np_shots_on_target_p90"})


@functools.cache
def _collinear(group: str) -> frozenset[frozenset[str]]:
    """The group's measured D17 pairs at |ρ| ≥ COLLINEAR_RHO ("ranks players almost the same as
    another metric"; ADR 004 D17), from the committed measurements."""
    return frozenset(frozenset((p["a"], p["b"])) for p in R.load_measurements()["collinear_pairs"]
                     if p["group"] == group and abs(p["rho"]) >= config.COLLINEAR_RHO)


def is_variant(group: str, a: str, b: str) -> bool:
    """Near-duplicates for What stands out: two shot variants, or a measured D17 pair."""
    return (a in SHOT_VARIANTS and b in SHOT_VARIANTS) or frozenset((a, b)) in _collinear(group)


def stand_out(group: str, rows: list[dict]) -> list[dict]:
    """"What stands out" (W3 fix round, user 2026-10-10): the W.STAND_OUT_N metrics whose shown
    league percentile is furthest from 50, among every metric with a shown league percentile
    (style metrics included, with their neutral phrase and no colour; signature traits and zero
    ties have none); ties by registry metric order. Near-duplicates are left out (user,
    2026-10-10): walking that order, a metric that is a variant (is_variant) of one already
    picked is skipped, unless it is a DNA member and the picked one is not, when it takes that
    one's place. Pairwise, not transitive: one metric with spurious pairs (DM-CM
    defensive_duel_success_pct, ρ ±1 on 3 rows; ADR 004) cannot chain unrelated metrics. Ranked
    in Python (ADR 005 §1). `order` points at the group metadata (label, tag, notes)."""
    if group not in GROUPS:
        return []
    cands = sorted(((-abs(row["local"]["pct"] - 50.0), ORDER[s.column], s, row)
                    for s, row in zip(SPECS_IN_ORDER, rows) if row["local"]["pct"] is not None),
                   key=lambda t: t[:2])
    picked: list[tuple] = []

    def dna(c: tuple) -> bool:
        return REGISTRY.cell(c[2].column, group).status == "dna"
    for c in cands:
        hit = next((i for i, q in enumerate(picked) if is_variant(group, q[2].column, c[2].column)),
                   None)
        if hit is None:
            picked.append(c)
        elif dna(c) and not dna(picked[hit]):
            # The DNA member takes the place; any other pick it pairs with leaves too.
            picked = [q for q in picked if not is_variant(group, q[2].column, c[2].column)]
            picked.append(c)
    picked = sorted(picked, key=lambda t: t[:2])[:W.STAND_OUT_N]
    return [{"metric": s.column, "order": ORDER[s.column], "label": W.metric_label(s.column, group),
             "direction": s.direction, "pct": row["local"]["pct"],
             "pct_text": row["local"]["pct_text"], "phrase": row["local"]["phrase"],
             "bin": row["local"]["bin"]}
            for _, _, s, row in picked]


# ── Players ──────────────────────────────────────────────────────────────────────

def _identity(ident: Mapping[str, Any], positions: Mapping[str, float] | None) -> dict:
    k = tuple(ident[c] for c in KEY)
    group = ident["position_group"]
    role = None
    if positions and group != "GK":
        role = W.role_words(fine_role(positions))
    slot = None
    if group == "DM-CM" and positions:
        s = dmcm_slot(positions)
        slot = {"label": s.label, "label_text": W.SLOT_WORDS[s.label],
                "share6": rnd(s.share6), "share6_text": f"{s.share6:.0%}",
                "pivot": s.pivot, "pivot_text": W.PIVOT_WORDS[s.pivot]}
    return {
        "key": row_key(k), "player_id": k[0], "team_id": k[1], "competition_id": k[2],
        "season_id": k[3],
        "name": ident["player_name"], "nickname": (ident["player_nickname"] or "").strip() or None,
        "display_name": display_name(ident["player_name"], ident["player_nickname"]),
        "team": ident["team_name"], "league": league_label(k[2], k[3]),
        "group": group, "group_text": W.GROUP_TEXT[group],
        "second_group": ident["second_position_group"] if ident["meets_min_minutes"] else None,
        "minutes": rnd(ident["minutes_played"]), "minutes_text": X.format_number(ident["minutes_played"]),
        "nominal_minutes": rnd(ident["minutes_nominal_equiv"]),
        "nominal_minutes_text": X.format_number(ident["minutes_nominal_equiv"]),
        "meets_gate": bool(ident["meets_min_minutes"]),
        "near_gate": X.near_gate(group, ident["minutes_nominal_equiv"]) and bool(ident["meets_min_minutes"]),
        "role_text": role,
        "dmcm_slot": slot,
        # The season summary line (display round, user): the pipeline's own counts.
        "summary": [{"key": c, "label": W.summary_label(c, ident[c]),
                     "text": X.format_number(ident[c]) if c == "minutes_played"
                     else X.format_count(ident[c], c in W.SUM_COLUMNS)} for c in W.SUMMARY_LABELS],
    }


def _signature(ident: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    group = ident["position_group"]
    if group not in GROUPS:
        return []
    out = []
    for m in REGISTRY.signature_traits(group):
        spec = X.SPECS_BY_COLUMN[m]
        cell = REGISTRY.cell(m, group)
        row = rows[m]
        count = ident[spec.count_column]
        held = is_signature_holder(cell, count, row["percentile_status"])
        out.append({
            "metric": m, "label": W.metric_label(m, group), "order": ORDER[m],
            "holder": held,
            "holder_text": (W.HOLDER if held else W.NON_HOLDER) if held is not None
            else W.NOT_ASSESSED.format(reason=X.status_text(row["percentile_status"])),
            "season_count": count, "season_count_text": X.format_number(count),
            "minimum": cell.signature_min_count,
            "notes": [{"short": n.short, "full": n.full}
                      for n in (R.note_text(x) for x in cell.notes)],
        })
    return sorted(out, key=lambda e: e["order"])


def _dna(k: tuple, group: str, dna_rows: list[dict], dim_rows: list[dict]) -> dict | None:
    if not dna_rows:
        return None
    status = dna_rows[0]["dna_status"]
    vector = [{"metric": r["metric"], "label": W.metric_label(r["metric"], group),
               "family": r["family"], "direction": r["direction"],
               "pct": rnd(r["pct_local"]), "status": r["metric_status"],
               "weight": rnd(r["weight"])} for r in sorted(dna_rows, key=lambda r: r["metric"])]
    dims = []
    for r in sorted(dim_rows, key=lambda r: r["family"]):
        st = r["score_status"]
        if r["members"] is None:
            raise ExportError(f"{k}: {r['family']} has a null members list")
        entry = {"family": r["family"], "label": W.family_label(r["family"]),
                 "score": rnd(r["score"]),
                 "score_text": X.format_number(r["score"]) if r["score"] is not None else None,
                 "members": list(r["members"]), "status": st,
                 "withheld_short": None, "withheld_full": None, "defending_note": None}
        if st in D.WITHHELD_STATUSES:
            t = W.withheld_text(st)
            full = X.withheld_reason(r["family"], group, st)
            entry["withheld_short"], entry["withheld_full"] = t.short, full
            if r["family"] == "Defending":
                entry["defending_note"] = W.defending_note(group)
        elif st != D.OK:
            entry["withheld_short"] = entry["withheld_full"] = X.dim_status_text(st)
        dims.append(entry)
    return {"status": status, "vector": vector, "dimensions": dims}


def _neighbours(k: tuple, group: str, nb: P.RowNeighbours, rows: Mapping[tuple, RowDna],
                ident_of: Mapping[tuple, Mapping[str, Any]]) -> dict:
    items = []
    for n in nb.neighbours:
        o = ident_of[n.key]
        a, b = rows[k], rows[n.key]

        def phrase(m: str) -> dict:
            # The table's words for the same cell: shown (direction-adjusted) percentile,
            # neutral on a hollow-bar cell.
            spec = X.SPECS_BY_COLUMN[m]
            biased = REGISTRY.cell(m, group).team_style_biased

            def words(pct: float | None) -> str:
                return _phrase(spec, biased, rnd(X.adjusted(spec, pct)))  # type: ignore[arg-type]
            return {"metric": m, "label": W.metric_label(m, group),
                    "a_phrase": words(a.cells[m].pct_local),
                    "b_phrase": words(b.cells[m].pct_local)}
        if not 0 <= n.distance < 1:
            raise ExportError(f"distance {n.distance} outside [0, 1)")
        code = f"{n.distance:.4f}"
        items.append({
            "key": row_key(n.key),
            "display_name": display_name(o["player_name"], o["player_nickname"]),
            "team": o["team_name"], "league": league_label(n.key[2], n.key[3]),
            "distance": rnd(n.distance), "distance_text": code,
            "rank": n.rank, "same_team": n.key[1] == k[1] and n.key[2] == k[2],
            "most_different": [phrase(m) for m in n.picks[:2]],
            "most_alike": [phrase(m) for m in n.picks[P.N_DIFF:]],
        })
    return {"label": W.NEIGHBOURS_LABEL, "n_candidates": nb.n_candidates,
            "items": items}


# ── Style maps ───────────────────────────────────────────────────────────────────

def style_map_doc(group: str, sm: S.StyleMap, rows: Mapping[tuple, RowDna],
                  ident_of: Mapping[tuple, Mapping[str, Any]],
                  positions: Mapping[tuple, Mapping[str, float]],
                  multi: Mapping[tuple, list[M.Stint]], dna_group: Mapping[tuple, str],
                  dna_status: Mapping[tuple, str]) -> dict:
    m = sm.model
    axes = []
    for a in (0, 1):
        name = W.AXIS_NAMES[group][a]
        axes.append({
            "name_positive": name[0] if name else None, "name_negative": name[1] if name else None,
            "title": W.axis_title(name) if name else f"PC{a + 1}",
            "note": W.AXIS_NOTES.get((group, a)),
            "variance_share": rnd(m.evr[a]), "variance_text": f"{m.evr[a]:.0%}",
            "positive_end": [W.metric_label(x, group) for x in S.end_metrics(m, a, 1)],
            "negative_end": [W.metric_label(x, group) for x in S.end_metrics(m, a, -1)],
        })
    points = []
    for k in sm.keys:
        o = ident_of[k]
        pushes = []
        for a in (0, 1):
            top = []
            for metric, push in S.pushes(m, rows[k], a)[:3]:
                adj, words = W.pct_words(rnd(rows[k].cells[metric].pct_local))  # type: ignore[arg-type]
                top.append({"metric": metric, "label": W.metric_label(metric, group),
                            "adjective": adj, "phrase": words,
                            "toward": W.end_name(group, m, a, 1 if push > 0 else -1),
                            "push": rnd(push)})
            pushes.append(top)
        role = fine_role(positions[k])
        points.append({
            "key": row_key(k), "display_name": display_name(o["player_name"], o["player_nickname"]),
            "team": o["team_name"], "league": league_label(k[2], k[3]),
            "minutes_text": X.format_number(o["minutes_played"]),
            "x": rnd(sm.scores[k][0]), "y": rnd(sm.scores[k][1]),
            "role": role.label, "role_text": W.role_words(role),
            "lean": [W.lean(group, m, sm.scores[k][a], a) for a in (0, 1)],
            "pushes": pushes,
        })
    plotted = set(sm.keys)
    played_for: dict[str, list[dict]] = {}
    movers = []
    for ps in sorted(multi):
        stints = multi[ps]
        here = [st for st in stints if st.key in plotted]
        if not here:
            continue
        lines = []
        for st in stints:
            code, other = M.stint_note(st, group, plotted, dna_group, dna_status)
            lines.append({"key": row_key(st.key), "team": st.team,
                          "league": league_label(st.key[2], st.key[3]),
                          "minutes_text": X.format_number(st.minutes),
                          "months": W.month_range(st.first, st.last),
                          "position_text": W.position_words(st.role),
                          "note": W.stint_note(code, other) or None, "note_group": other,
                          "plotted": st.key in plotted})
        for st in here:
            played_for[row_key(st.key)] = lines
        if len(here) >= 2:
            cb = M.combined(sm, rows, here)
            movers.append({
                "player_id": ps[0], "display_name": display_name(
                    ident_of[here[0].key]["player_name"], ident_of[here[0].key]["player_nickname"]),
                "label": W.SEASON_DOT_LABEL,
                "stints": [row_key(x) for x in cb.stints],
                "weights": [rnd(w) for w in cb.weights],
                "weights_text": [f"{w:.0%}" for w in cb.weights],
                "x": rnd(cb.score[0]), "y": rnd(cb.score[1]),
                "lean": [W.lean(group, m, cb.score[a], a) for a in (0, 1)],
                "pairs": [{"distance": rnd(sp.distance), "distance_text": f"{sp.distance:.4f}",
                           "rank_ab": sp.rank_ab, "n_a": sp.n_a, "rank_ba": sp.rank_ba,
                           "n_b": sp.n_b,
                           "changed": [{"metric": c, "label": W.metric_label(c, group)}
                                       for c in sp.changed]} for sp in cb.pairs],
            })
    return {
        "group": group, "group_text": W.GROUP_PLURAL[group], "caption": W.MAP_CAPTION,
        "axes": axes, "variance_text": f"{m.evr[0] + m.evr[1]:.0%}",
        "points": points,
        "partial": [{"key": row_key(k), "display_name": display_name(
            ident_of[k]["player_name"], ident_of[k]["player_nickname"]),
            "team": ident_of[k]["team_name"]} for k in sm.partial],
        "played_for": played_for, "movers": movers,
        "texts": {"role_rule": W.ROLE_RULE, "slot_rule": W.SLOT_RULE,
                  "clock_rule": W.CLOCK_RULE, "weight_note": W.WEIGHT_NOTE},
    }


# ── The whole export ─────────────────────────────────────────────────────────────

def build(inp: Inputs, keys: Iterable[tuple] | None = None,
          style_groups: Iterable[str] = GROUPS) -> dict[str, Any]:
    """Path → document for every file except the manifest. `keys` limits the player files and
    the search index to those rows (the fixture); style maps always cover their whole group.
    `style_groups` limits the style maps (tests on synthetic data; the export draws all five)."""
    season = inp.season
    ident_of = {tuple(r[c] for c in KEY): r for r in season.iter_rows(named=True)}
    wanted = set(ident_of) if keys is None else set(keys)
    if wanted - set(ident_of):
        raise ExportError(f"unknown keys: {sorted(wanted - set(ident_of))[:5]}")
    pos: dict[tuple, dict[str, float]] = {}
    for r in inp.positions.iter_rows(named=True):
        pos.setdefault(tuple(r[c] for c in KEY), {})[r["position"]] = r["minutes"]
    pct_rows: dict[tuple, dict[str, dict[str, dict]]] = {}
    for r in inp.pct.iter_rows(named=True):
        pct_rows.setdefault(tuple(r[c] for c in KEY), {}).setdefault(r["cohort_role"], {})[r["metric"]] = r
    dna_by: dict[tuple, list[dict]] = {}
    for r in inp.dna.iter_rows(named=True):
        dna_by.setdefault(tuple(r[c] for c in KEY), []).append(r)
    dim_by: dict[tuple, list[dict]] = {}
    for r in inp.dims.iter_rows(named=True):
        dim_by.setdefault(tuple(r[c] for c in KEY), []).append(r)
    rows = P.rows_from_dna(inp.dna)
    nbrs = {g: P.group_neighbours(rs, config.DNA_NEIGHBOURS_K) for g, rs in rows.items()}
    dna_group = {k: g for g, rs in rows.items() for k in rs}
    dna_status = {k: r.dna_status for rs in rows.values() for k, r in rs.items()}
    multi = M.stints(season, inp.match, pos)
    stamps = dict(inp.stamps)
    docs: dict[str, Any] = {}
    search = []
    for k in sorted(wanted):
        ident = ident_of[k]
        group = ident["position_group"]
        prim = pct_rows.get(k, {}).get("primary")
        if prim is None or set(prim) != {s.column for s in SPECS}:
            raise ExportError(f"{k}: percentile rows missing")
        second = pct_rows[k].get("second")
        rows_m = [metric_row(prim, s.column, ident) for s in SPECS_IN_ORDER]
        doc = {
            "stamps": stamps,
            "identity": _identity(ident, pos.get(k)),
            "stand_out": stand_out(group, rows_m),
            "defensive_rates": defensive_rates(ident),
            "metrics": rows_m,
            "second_group": ({"group": second[X.POPULATION_REFERENCE_METRIC]["position_group"],
                              "metrics": [metric_row(second, s.column) for s in SPECS_IN_ORDER]}
                             if second else None),
            "signature_traits": _signature(ident, prim),
            "dna": _dna(k, group, dna_by.get(k, []), dim_by.get(k, [])),
            "neighbours": (_neighbours(k, group, nbrs[group][k], rows[group], ident_of)
                           if k in nbrs.get(group, {}) else None),
            "style_map": (f"style-maps/{group}.json" if group in GROUPS
                          and k in rows.get(group, {}) and rows[group][k].dna_status == D.COMPLETE
                          else None),
        }
        docs[f"players/{row_key(k)}.json"] = doc
        idt = doc["identity"]
        search.append({
            "key": idt["key"], "player_id": k[0], "display_name": idt["display_name"],
            "name": idt["name"],
            "search_key": " ".join(dict.fromkeys(
                X.fold(x) for x in (idt["display_name"], idt["name"]) if x)),
            "team": idt["team"], "league": idt["league"], "group": group,
            "minutes_text": idt["minutes_text"],
            "profile": "dna" if doc["dna"] else ("gated" if idt["meets_gate"] else "below_gate"),
        })
    docs["search-index.json"] = {"stamps": stamps, "entries": search}
    for g in GROUPS:
        docs[f"gems/{g}.json"] = {"stamps": stamps, **gems_doc(g, docs)}
    for g in style_groups:
        sm = S.fit(g, dna_specs(REGISTRY, SPECS, g), rows[g])
        docs[f"style-maps/{g}.json"] = {"stamps": stamps, **style_map_doc(
            g, sm, rows[g], ident_of, pos, multi, dna_group, dna_status)}
    for g in (*GROUPS, "GK"):
        held = D.withheld_for(g) if g in GROUPS else {}
        docs[f"groups/{g}.json"] = {
            "stamps": stamps, "group": g,
            "metrics": [metric_meta(s.column, g, held) for s in SPECS_IN_ORDER],
            "defending_note": W.defending_note(g) if g in GROUPS else None,
            "family_definitions": family_definitions(g, held) if g in GROUPS else {},
        }
    docs["labels.json"] = labels_doc(stamps)
    return docs


def gems_doc(group: str, docs: Mapping[str, Any]) -> dict:
    """Gem Finder (plan 006 simple mode, W5): the group's profiled rows with their family scores
    (the exported dimension scores, no new statistic). Every order is computed here (ADR 005 §1:
    the page never sorts by a statistic): per scored family, row indices by score, highest
    first, equal scores (at the exported precision) by display name then key, unscored rows
    last. The page filters by league, team and minutes tier, all exported keys."""
    held = D.withheld_for(group)
    defs = family_definitions(group, held)
    if not defs:
        raise ExportError(f"{group}: no scored family to order Gem Finder by")
    rows = []
    for path in sorted(p for p in docs if p.startswith("players/")):
        d = docs[path]
        idt = d["identity"]
        if idt["group"] != group or not d["dna"]:
            continue
        dims = {x["family"]: x for x in d["dna"]["dimensions"]}
        rows.append({
            "key": idt["key"], "display_name": idt["display_name"],
            "team_id": idt["team_id"], "team": idt["team"],
            "competition_id": idt["competition_id"], "league": idt["league"],
            "minutes_text": idt["minutes_text"], "near_gate": idt["near_gate"],
            # Minutes played, at least each tier (the filter's key; no comparison in the page).
            "minutes_tiers": [str(t) for t in W.GEMS_MINUTES_TIERS if idt["minutes"] >= t],
            "scores": {f: {"score": dims[f]["score"], "score_text": dims[f]["score_text"]}
                       for f in defs},
        })
    order = {}
    for f in defs:
        def key(i: int, f: str = f) -> tuple:
            sc = rows[i]["scores"][f]["score"]
            return (sc is None, -(sc or 0.0), X.fold(rows[i]["display_name"]), rows[i]["key"])
        order[f] = sorted(range(len(rows)), key=key)
    families = sorted({*defs, *held}, key=lambda f: W.family_label(f))
    return {
        "group": group,
        "families": [{"family": f, "label": W.family_label(f), "definition": defs.get(f),
                      "withheld_short": W.withheld_text(held[f]).short if f in held else None,
                      "withheld_full": (" ".join(filter(None, (
                          W.withheld_text(held[f]).full,
                          W.defending_note(group) if f == "Defending" else None)))
                          if f in held else None)}
                     for f in families],
        "rows": rows,
        "order": order,
        "leagues": [{"competition_id": c, "league": lg} for c, lg in sorted(
            {(r["competition_id"], r["league"]) for r in rows}, key=lambda t: t[1])],
        "teams": [{"team_id": t, "team": n, "competition_id": c} for t, n, c in sorted(
            {(r["team_id"], r["team"], r["competition_id"]) for r in rows},
            key=lambda t: (X.fold(t[1]), t[0]))],
        "minutes_tiers": [{"value": str(t), "text": f"{X.format_number(t)}+"}
                          for t in W.GEMS_MINUTES_TIERS],
    }


def scored_members(group: str, family: str) -> tuple[str, ...]:
    """The metrics a family score can average in a group (dna._dimensions: the family's DNA
    metrics with a good / poor direction), in name order."""
    return tuple(sorted(s.column for s in dna_specs(REGISTRY, SPECS, group)
                        if s.family == family and s.direction != "style"))


def family_definitions(group: str, held: Mapping[str, str]) -> dict[str, str]:
    """What each scored family measures (W.FAMILY_DEFINITIONS), checked against the members the
    scores use: a stale text fails the export. Withheld families show their reason instead."""
    defs = W.FAMILY_DEFINITIONS[group]
    scored = sorted(f for f in dict.fromkeys(s.family for s in SPECS)
                    if f not in held and scored_members(group, f))
    if sorted(defs) != scored:
        raise ExportError(f"{group}: family definitions {sorted(defs)} ≠ scored families {scored}")
    for fam, d in defs.items():
        if tuple(sorted(d.members)) != scored_members(group, fam):
            raise ExportError(f"{group} {fam}: definition members {d.members} ≠ "
                              f"{scored_members(group, fam)}")
    return {f: defs[f].text for f in scored}


def labels_doc(stamps: Mapping[str, Any]) -> dict:
    return {
        "stamps": dict(stamps),
        "groups": {g: {"plural": W.GROUP_PLURAL[g], "one": W.GROUP_ONE[g],
                       "abbr_plural": X.GROUP_ABBR_PLURAL[g]} for g in GROUPS},
        "families": {f: W.family_label(f) for f in dict.fromkeys(s.family for s in SPECS)},
        "statuses": dict(X.STATUS_TEXT),
        "dimension_statuses": {s: {"short": W.withheld_text(s).short, "full": W.withheld_text(s).full}
                               for s in W.WITHHELD_TEXT},
        "reasons": dict(R.REASON_TEXT),
        "notes": {n: {"short": t.short, "full": t.full} for n, t in R.NOTE_TEXT.items()},
        "words": {"no_reading": W.NO_READING, "grey_rule": W.GREY_RULE,
                  "grey_rule_table": W.GREY_RULE_TABLE, "near_gate_chip": W.NEAR_GATE_CHIP,
                  "map_caption": W.MAP_CAPTION, "zero_tie": W.ZERO_TIE,
                  "neighbours_label": W.NEIGHBOURS_LABEL, "attribution": W.ATTRIBUTION,
                  "portfolio": W.PORTFOLIO, "profile_shape_title": W.PROFILE_SHAPE_TITLE,
                  "profile_shape_note": W.PROFILE_SHAPE_NOTE, "neighbours_title": W.NEIGHBOURS_TITLE,
                  "around_average": W.AROUND_AVERAGE, "basis_legend": W.BASIS_LEGEND,
                  "stand_out_title": W.STAND_OUT_TITLE, "stand_out_note": W.STAND_OUT_NOTE,
                  "lower_better": W.LOWER_BETTER, "basis_marker": W.BASIS_MARKER,
                  "rate_label": W.RATE_LABEL, "hollow_legend": W.HOLLOW_LEGEND,
                  "compare_league_note": W.COMPARE_LEAGUE_NOTE,
                  "gems_title": W.GEMS_TITLE, "gems_lead": W.GEMS_LEAD,
                  "gems_sort_note": W.GEMS_SORT_NOTE, "gems_like_title": W.GEMS_LIKE_TITLE,
                  "gems_like_note": W.GEMS_LIKE_NOTE, "gems_minutes_any": W.GEMS_MINUTES_ANY, "exact_hint": W.EXACT_HINT, "compare_top_title": W.COMPARE_TOP_TITLE,
                  "compare_top_note": W.COMPARE_TOP_NOTE, "compare_all_title": W.COMPARE_ALL_TITLE,
                  "compare_same_group": W.COMPARE_SAME_GROUP,
                  "most_alike": W.MOST_ALIKE, "most_different": W.MOST_DIFFERENT,
                  "signature_title": W.SIGNATURE_TITLE, "signature_note": W.SIGNATURE_NOTE,
                  **{f"chip_{k}": v for k, v in W.CHIPS.items()},
                  **{f"column_{k}": v for k, v in W.COLUMNS.items()}},
        "metric_order": [s.column for s in SPECS_IN_ORDER],
        "methodology": {"title": W.METHODOLOGY_TITLE,
                        "sections": [{"title": t, "paragraphs": list(ps)} for t, ps in W.METHODOLOGY]},
    }


# ── Validation, manifest, writing ────────────────────────────────────────────────

SCHEMA_OF = {"manifest.json": "manifest", "search-index.json": "search-index",
             "labels.json": "labels"}


def schema_name(path: str) -> str:
    if path in SCHEMA_OF:
        return SCHEMA_OF[path]
    if path.startswith("players/"):
        return "player"
    if path.startswith("style-maps/"):
        return "style-map"
    if path.startswith("groups/"):
        return "group"
    if path.startswith("gems/"):
        return "gems"
    raise ExportError(f"no schema for {path}")


def validate(docs: Mapping[str, Any], contract_dir: Path = CONTRACT_DIR) -> None:
    """Validate every document against contract/v1 (jsonschema, draft 2020-12)."""
    import jsonschema
    from referencing import Registry, Resource
    schemas = {p.name: json.loads(p.read_text()) for p in contract_dir.glob("*.schema.json")}
    registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
    validators = {n.removesuffix(".schema.json"): jsonschema.Draft202012Validator(s, registry=registry)
                  for n, s in schemas.items()}
    for path, doc in docs.items():
        errs = sorted(validators[schema_name(path)].iter_errors(doc), key=lambda e: list(e.path))
        if errs:
            e = errs[0]
            raise ExportError(f"{path}: {e.message} at {'/'.join(map(str, e.path))} "
                              f"({len(errs)} errors)")


def manifest(docs: Mapping[str, bytes], stamps: Mapping[str, Any], commit: str) -> dict:
    return {"stamps": dict(stamps), "exporter_commit": commit,
            "attribution": W.ATTRIBUTION,
            "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
                      for p, b in sorted(docs.items())]}


def _inside(out: Path, path: Path) -> Path:
    """`path` resolved, refused unless it lies inside the export directory `out`."""
    root, p = out.resolve(), path.resolve()
    if p != root and root not in p.parents:
        raise ExportError(f"{path} is outside the export directory {out}")
    return p


def managed_files(out: Path) -> list[Path]:
    """The files write() owns under `out`: the top-level documents and the *.json files of the
    managed sub-directories. Nothing else (no recursion, no other names, no other directory)."""
    found = [out / f for f in MANAGED_FILES if (out / f).is_file()]
    for d in MANAGED_DIRS:
        if (out / d).is_dir():
            found += sorted(p for p in (out / d).iterdir() if p.is_file() and p.suffix == ".json")
    return [_inside(out, p) for p in found]


def write(docs: Mapping[str, Any], out: Path, commit: str) -> dict[str, int]:
    """Validate, then write every file and the manifest (validated too) under `out`, after
    deleting the managed files a previous run left that this one does not write, so the
    directory holds exactly what the manifest lists. Returns the byte size per file."""
    validate(docs)
    blobs = {p: dumps(d) for p, d in docs.items()}
    stamps = next(iter(docs.values()))["stamps"]
    man = manifest(blobs, stamps, commit)
    validate({"manifest.json": man})
    blobs["manifest.json"] = dumps(man)
    targets = {_inside(out, out / p) for p in blobs}
    for p in managed_files(out):
        if p not in targets:
            p.unlink()
    for p, b in blobs.items():
        f = out / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
    return {p: len(b) for p, b in blobs.items()}


def fixture_keys(inp: Inputs) -> list[tuple]:
    """FIXTURE_ROWS as season keys, plus the neighbours of FIXTURE_NEIGHBOURS_OF."""
    season_ids = inp.stamps["season_ids"]
    if len(season_ids) != 1:
        raise ExportError("the fixture is defined for one season")
    keys = [(*r, season_ids[0], R.MEASUREMENT_SOURCE) for r in FIXTURE_ROWS]
    base = build(inp, keys=keys, style_groups=())
    anchor = base[f"players/{row_key((*FIXTURE_NEIGHBOURS_OF, season_ids[0]))}.json"]
    for n in anchor["neighbours"]["items"]:
        pid, tid, cid, sid = (int(x) for x in n["key"].split("-"))
        keys.append((pid, tid, cid, sid, R.MEASUREMENT_SOURCE))
    return sorted(set(keys))


def diff_summary(old: Path, docs: Mapping[str, bytes]) -> list[str]:
    """Files added, removed and changed against the fixture on disk (manifest excluded)."""
    have = {p.relative_to(old).as_posix(): p.read_bytes() for p in managed_files(old)} \
        if old.is_dir() else {}
    have.pop("manifest.json", None)
    added = sorted(set(docs) - set(have))
    removed = sorted(set(have) - set(docs))
    changed = sorted(p for p in set(docs) & set(have) if docs[p] != have[p])
    return ([f"+ {p}" for p in added] + [f"- {p}" for p in removed]
            + [f"~ {p}" for p in changed])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--fixture", action="store_true",
                    help=f"write the fixed subset to {FIXTURE_DIR.relative_to(ROOT)} (refused "
                         "if it would change, unless --update-fixture)")
    ap.add_argument("--update-fixture", action="store_true",
                    help="allow --fixture to rewrite a changed fixture; prints the diff summary")
    args = ap.parse_args()
    inp = load_inputs()
    if args.fixture or args.update_fixture:
        docs = build(inp, keys=fixture_keys(inp))
        changes = diff_summary(FIXTURE_DIR, {p: dumps(d) for p, d in docs.items()})
        if changes and FIXTURE_DIR.is_dir() and any(FIXTURE_DIR.iterdir()) \
                and not args.update_fixture:
            raise SystemExit("the fixture would change (" + str(len(changes)) + " files); "
                             "rerun with --update-fixture and review the diff:\n"
                             + "\n".join(changes[:40]))
        sizes = write(docs, FIXTURE_DIR, "fixture")
        print(f"fixture: {len(sizes)} files ({sum(sizes.values()):,} bytes) → {FIXTURE_DIR}; "
              f"{len(changes)} changed: " + ("; ".join(changes[:20]) or "none"))
        return
    docs = build(inp)
    sizes = write(docs, args.out, git_commit())
    players = [v for p, v in sizes.items() if p.startswith("players/")]
    print(f"wrote {len(sizes)} files ({sum(sizes.values()):,} bytes) → {args.out}; "
          f"{len(players)} player files, largest {max(players):,} bytes; "
          + ", ".join(f"{p} {v:,}" for p, v in sizes.items() if not p.startswith("players/")))


if __name__ == "__main__":
    main()
