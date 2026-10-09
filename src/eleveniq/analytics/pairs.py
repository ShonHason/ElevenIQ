"""
Neighbour facts within one position group (plan 006 W1; moved from the M4f neighbour lists, ADR
004 §12, and spikes/m4g_compare.py, plan 005 M4g1b). The spikes stay as reference checks.

- distance: analytics.dna.pair_distance (weighted squared Euclidean on local percentile / 100
  with pair weights; the M4f provisional distance, until M5). Defined only within one group; a
  dna_incomplete row has none (§12.2).
- candidates of a row: every other row of its group except rows of the same player (a club
  change gives one row per club; neighbour lists leave a player's own rows out, M4f3).
- rank: competition rank, 1 + the number of candidates closer by more than TIE_TOL, so equal
  distances (exactly, or within float noise; percentiles are discrete) share a rank.
- neighbours: the first k candidates by (distance, key), each with its rank.
- picks: the N_DIFF metrics with the largest |gap| in local percentile points, then the N_ALIKE
  with the smallest, ties (equal within TIE_TOL) by metric name, over the metrics both rows have (the neighbour
  reasons: "most alike / most different").

Only neighbours are exported: compare mode carries no distance and no pair data (user,
2026-10-09; plan 006 W1).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Mapping, Sequence

import polars as pl

from eleveniq.analytics.dna import DNA_INCOMPLETE, RowDna, VectorCell, pair_distance

TIE_TOL = 1e-12
N_DIFF, N_ALIKE = 5, 3
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")

Key = tuple


@dataclass(frozen=True)
class Neighbour:
    key: Key
    distance: float
    rank: int                     # competition rank in the row's candidate order
    picks: tuple[str, ...]        # N_DIFF most different, then N_ALIKE most alike


@dataclass(frozen=True)
class RowNeighbours:
    n_candidates: int             # N: the candidates of this row (the rank denominator)
    neighbours: tuple[Neighbour, ...]


def rows_from_dna(dna: pl.DataFrame) -> dict[str, dict[Key, RowDna]]:
    """Group -> key -> RowDna, rebuilt from player_dna.parquet (one row per key and metric).
    Dimension scores are not in that parquet and are left empty: distances do not use them."""
    cells: dict[Key, dict[str, VectorCell]] = {}
    meta: dict[Key, tuple[str, str]] = {}
    for r in dna.sort([*KEY, "metric"]).iter_rows(named=True):
        k = tuple(r[c] for c in KEY)
        cells.setdefault(k, {})[r["metric"]] = VectorCell(
            r["family"], r["direction"], r["value"], r["pct_local"], r["n_local"],
            r["metric_status"], r["z"], r["z_median"], r["z_scale"], r["z_scale_method"],
            r["z_status"], r["weight"])
        g = (r["position_group"], r["dna_status"])
        if meta.setdefault(k, g) != g:
            raise ValueError(f"{k}: more than one position group or dna_status")
    out: dict[str, dict[Key, RowDna]] = {}
    for k, (group, status) in meta.items():
        out.setdefault(group, {})[k] = RowDna(group, status, cells[k], {})
    return out


def ranks(cand: Sequence[tuple[float, Key]]) -> dict[Key, int]:
    """Competition ranks: 1 + the number of candidates closer by more than TIE_TOL."""
    ds = sorted(d for d, _ in cand)
    return {k: bisect.bisect_left(ds, d - TIE_TOL) + 1 for d, k in cand}


def picks(a: RowDna, b: RowDna) -> tuple[str, ...]:
    """The N_DIFF largest |Δ local percentile| metrics, then the N_ALIKE smallest, ties by
    metric name, over the metrics both rows have."""
    both = sorted(a.present() & b.present())
    # Gaps equal within TIE_TOL tie (float noise of ~1e-15 must not beat the name rule).
    gap = {m: round(abs(a.cells[m].pct_local - b.cells[m].pct_local) / TIE_TOL)  # type: ignore[operator]
           for m in both}
    diff = sorted(both, key=lambda m: (-gap[m], m))[:N_DIFF]
    alike = sorted(both, key=lambda m: (gap[m], m))[:N_ALIKE]
    return (*diff, *alike)


def group_neighbours(rows: Mapping[Key, RowDna], k: int) -> dict[Key, RowNeighbours]:
    """The first k neighbours of each row of one group (dna_incomplete rows left out), with
    their ranks and picks. Every pair's distance is computed once."""
    keys = sorted(x for x, r in rows.items() if r.dna_status != DNA_INCOMPLETE)
    dist: dict[tuple[Key, Key], float] = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            dist[(a, b)] = pair_distance(rows[a], rows[b])
    out: dict[Key, RowNeighbours] = {}
    for a in keys:
        cand = [(dist[(a, b)] if a < b else dist[(b, a)], b) for b in keys if b[0] != a[0]]
        rk = ranks(cand)
        top = sorted(cand)[:k]
        out[a] = RowNeighbours(len(cand), tuple(
            Neighbour(b, d, rk[b], picks(rows[a], rows[b])) for d, b in top))
    return out
