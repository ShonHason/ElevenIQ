"""
Player DNA (plan 005 M4f, ADR 004 §10.8, §12). Pure functions; the percentiles come from
the M4d output and the populations from analytics.cohorts.

- Vector: a row's DNA metrics (registry.dna_metrics of its group), each with its
  league-local percentile (§12.1, config.DNA_PERCENTILE_BASIS) and a robust z on the same
  local reference population: z = (x − median) / (1.4826 · MAD); when MAD = 0 the scale is
  IQR / 1.349 (linear-interpolated quartiles); when both are 0, z is null (zero_spread).
  z is stored uncapped; M5 decides its use and cap (§12.6).
- A metric is missing when its local cell is not ok (e.g. below_min_attempts). No
  imputation (§12.2).
- Row weights: each family with a present member gets 1 / (number of such families),
  split equally among its present members; a missing metric has no weight. They sum to 1.
- dna_status: complete → partial (one metric missing, no family emptied) →
  dna_incomplete (more than one missing, or a family emptied). A dna_incomplete row keeps
  its vector, weights and dimension scores; only the distance refuses it.
- Dimension scores, per DNA family of the group: the mean local percentile of the family's
  present directional members (lower_better as 100 − p), naming them in metric-name order.
  A withheld family is null with its status (config.DNA_WITHHELD_DIMENSIONS via
  withheld_for: Defending pending_possession_adjustment in every group; Passing
  pending_mover_stability where it rests on long_pass_completion_pct alone, §12.9); a
  family with no present directional member is null (no_directional_metric).
- Pair distance (the M4f checks' provisional distance; M5 picks the product metric):
  weighted squared Euclidean on percentile / 100, with each family's weight re-split over
  the metrics both rows have, so d(A, B) = d(B, A).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import polars as pl

from eleveniq import config
from eleveniq.analytics.registry import Derivation, MetricSpec

MAD_SCALE = 1.4826     # normal-consistent MAD
IQR_SCALE = 1.349      # normal-consistent IQR

OK = "ok"
ZERO_SPREAD = "zero_spread"
COMPLETE = "complete"
PARTIAL = "partial"
DNA_INCOMPLETE = "dna_incomplete"
NO_DIRECTIONAL_METRIC = "no_directional_metric"
PENDING_POSSESSION_ADJUSTMENT = "pending_possession_adjustment"
PENDING_MOVER_STABILITY = "pending_mover_stability"
WITHHELD_STATUSES: frozenset[str] = frozenset({PENDING_POSSESSION_ADJUSTMENT,
                                               PENDING_MOVER_STABILITY})


class DnaError(ValueError):
    """A distance was asked of rows it is not defined for."""


@dataclass(frozen=True)
class Spread:
    median: float
    scale: float | None          # None: MAD and IQR are both 0 (zero_spread)
    method: str | None           # "mad" | "iqr" | None


@dataclass(frozen=True)
class MetricInput:
    """One DNA metric of one row, as the M4d output stores it, with its population spread
    (None when the local cell is not ok)."""
    value: float | None
    pct_local: float | None
    n_local: int | None
    status_local: str
    spread: Spread | None = None


@dataclass(frozen=True)
class VectorCell:
    family: str
    direction: str
    value: float | None
    pct_local: float | None
    n_local: int | None
    metric_status: str
    z: float | None
    z_median: float | None
    z_scale: float | None
    z_scale_method: str | None
    z_status: str
    weight: float | None


@dataclass(frozen=True)
class Dimension:
    score: float | None
    score_status: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class RowDna:
    group: str
    dna_status: str
    cells: Mapping[str, VectorCell]          # metric -> cell, metric-name order
    dimensions: Mapping[str, Dimension]      # family -> score, family-name order

    def present(self) -> frozenset[str]:
        return frozenset(m for m, c in self.cells.items() if c.metric_status == OK)


def spread(values: pl.Series) -> Spread:
    """Median and robust scale of a population (nulls dropped; it must not be empty)."""
    v = values.drop_nulls().cast(pl.Float64)
    if v.len() == 0:
        raise ValueError("spread of an empty population")
    med = float(v.median())  # type: ignore[arg-type]
    mad = float((v - med).abs().median())  # type: ignore[arg-type]
    if mad > 0:
        return Spread(med, MAD_SCALE * mad, "mad")
    q1 = float(v.quantile(0.25, interpolation="linear"))  # type: ignore[arg-type]
    q3 = float(v.quantile(0.75, interpolation="linear"))  # type: ignore[arg-type]
    if q3 - q1 > 0:
        return Spread(med, (q3 - q1) / IQR_SCALE, "iqr")
    return Spread(med, None, None)


def family_members(specs: Sequence[MetricSpec], present: frozenset[str] | set[str]
                   ) -> dict[str, list[str]]:
    """Family -> its present metrics, in the order of `specs`; families with none left out."""
    out: dict[str, list[str]] = {}
    for s in specs:
        if s.column in present:
            out.setdefault(s.family, []).append(s.column)
    return out


def split_weights(specs: Sequence[MetricSpec], present: frozenset[str] | set[str]
                  ) -> dict[str, float]:
    """1 / (families with a present member), split equally among each family's members."""
    fams = family_members(specs, present)
    return {m: 1.0 / len(fams) / len(ms) for ms in fams.values() for m in ms}


def withheld_for(group: str, table: Mapping[str, tuple[str, Sequence[str]]] | None = None
                 ) -> dict[str, str]:
    """Family -> score_status of the families whose score is withheld in `group`
    (default table: config.DNA_WITHHELD_DIMENSIONS)."""
    table = config.DNA_WITHHELD_DIMENSIONS if table is None else table
    return {fam: status for fam, (status, groups) in table.items() if group in groups}


def _dimensions(specs: Sequence[MetricSpec], cells: Mapping[str, VectorCell],
                withheld: Mapping[str, str]) -> dict[str, Dimension]:
    out: dict[str, Dimension] = {}
    for fam in sorted({s.family for s in specs}):
        if fam in withheld:
            if withheld[fam] not in WITHHELD_STATUSES:
                raise ValueError(f"{fam}: unknown withheld status {withheld[fam]!r}")
            out[fam] = Dimension(None, withheld[fam], ())
            continue
        used = [s for s in specs if s.family == fam and s.direction != "style"
                and cells[s.column].metric_status == OK]
        if not used:
            out[fam] = Dimension(None, NO_DIRECTIONAL_METRIC, ())
            continue
        vals = []
        for s in used:
            p = cells[s.column].pct_local
            assert p is not None
            if s.direction == "higher_better":
                vals.append(p)
            elif s.direction == "lower_better":
                vals.append(100.0 - p)
            else:
                raise ValueError(f"{s.column}: unknown direction {s.direction!r}")
        out[fam] = Dimension(sum(vals) / len(vals), OK, tuple(s.column for s in used))
    return out


def row_dna(group: str, specs: Sequence[MetricSpec], inputs: Mapping[str, MetricInput], *,
            withheld: Mapping[str, str]) -> RowDna:
    """The DNA of one row. `specs` are the group's DNA metrics; `inputs` holds each one;
    `withheld` maps each family whose score is withheld to its status (withheld_for)."""
    specs = sorted(specs, key=lambda s: s.column)
    if {s.column for s in specs} != set(inputs):
        raise ValueError(f"{group}: inputs {sorted(inputs)} are not the DNA metrics "
                         f"{[s.column for s in specs]}")
    present = frozenset(s.column for s in specs if inputs[s.column].status_local == OK)
    weights = split_weights(specs, present)
    cells: dict[str, VectorCell] = {}
    for s in specs:
        i = inputs[s.column]
        z = med = scale = method = None
        z_status = i.status_local
        if i.status_local == OK:
            if i.spread is None or i.value is None or i.pct_local is None:
                raise ValueError(f"{group} {s.column}: an ok cell needs a value, a percentile "
                                 f"and a spread")
            med, scale, method = i.spread.median, i.spread.scale, i.spread.method
            if scale is None:
                z_status = ZERO_SPREAD
            else:
                z = (i.value - med) / scale
        cells[s.column] = VectorCell(s.family, s.direction, i.value, i.pct_local, i.n_local,
                                     i.status_local, z, med, scale, method, z_status,
                                     weights.get(s.column))
    missing = len(specs) - len(present)
    emptied = {s.family for s in specs} - set(family_members(specs, present))
    status = COMPLETE if missing == 0 else \
        DNA_INCOMPLETE if missing > 1 or emptied else PARTIAL
    return RowDna(group, status, cells, _dimensions(specs, cells, withheld))


def _check_pair(a: RowDna, b: RowDna) -> None:
    if a.group != b.group:
        raise DnaError(f"no distance across position groups ({a.group} vs {b.group})")
    if DNA_INCOMPLETE in (a.dna_status, b.dna_status):
        raise DnaError("a dna_incomplete row has no similarity results (ADR 004 §12.2)")
    if list(a.cells) != list(b.cells):
        raise DnaError(f"{a.group}: the rows hold different DNA metrics")


def pair_weights(a: RowDna, b: RowDna) -> dict[str, float]:
    """Each family's weight re-split over the metrics both rows have (§12.6)."""
    _check_pair(a, b)
    both = a.present() & b.present()
    fams: dict[str, list[str]] = {}
    for m, c in a.cells.items():
        if m in both:
            fams.setdefault(c.family, []).append(m)
    return {m: 1.0 / len(fams) / len(ms) for ms in fams.values() for m in ms}


def pair_distance(a: RowDna, b: RowDna) -> float:
    """Weighted squared Euclidean on local percentile / 100 with pair weights."""
    total = 0.0
    for m, w in pair_weights(a, b).items():
        pa, pb = a.cells[m].pct_local, b.cells[m].pct_local
        assert pa is not None and pb is not None
        total += w * ((pa - pb) / 100.0) ** 2
    return total


def dna_specs(derivation: Derivation, specs: Sequence[MetricSpec], group: str
              ) -> list[MetricSpec]:
    """The group's DNA metrics as specs, in metric-name order."""
    by_col = {s.column: s for s in specs}
    return [by_col[c] for c in derivation.dna_metrics(group)]
