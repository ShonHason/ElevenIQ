"""
Style map per position group (plan 006 W1; moved from spikes/m4g_style_map.py and
spikes/m4g_style_page.py, plan 005 M4g1 / M4g2, ADR 004 §13.4). The spikes stay as reference
checks. Display only: the map changes no score, rank or neighbour.

- input: the group's complete DNA rows (partial rows are listed, not plotted: PCA needs every
  metric and ADR 004 §12.2 forbids imputation); each local percentile / 100 times √(its
  per-metric family weight), so the squared Euclidean distance between two rows equals
  dna.pair_distance; no standardisation, which would undo the weights;
- PCA in scikit-learn, svd_solver="full" (deterministic). Each of the two components is signed
  so that its pin metric (AXIS_PINS, user 2026-10-07, M4g2) loads positive, so a solver sign flip
  cannot mirror the map; a pin loading below config.STYLE_END_MIN_LOADING fails;
- push of a metric on an axis = loading × (weighted value − group mean); a row's pushes sum to
  its score;
- an axis end is defined by up to 3 loadings of that sign with |loading| ≥
  config.STYLE_END_MIN_LOADING, compared unrounded;
- lean: z = score / SD (ddof 1) of the plotted scores; |z| < LEAN_NEAR near the middle, <
  LEAN_STRONG slight, else strong;
- every loading quoted in the axis notes (QUOTED_LOADINGS) is checked on each fit; a mismatch
  fails (the axis names are the user's reading of the loadings).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from sklearn.decomposition import PCA

from eleveniq.analytics.dna import COMPLETE, RowDna, split_weights
from eleveniq.analytics.registry import MetricSpec
from eleveniq.config import LEAN_NEAR, LEAN_STRONG, STYLE_END_MIN_LOADING

Key = tuple
TOP_LOADINGS = 6
DOMINANT_SHARE = 0.5

# The sign of each PC (user, 2026-10-07, M4g2): the pin metric's loading is made positive.
AXIS_PINS: dict[str, tuple[str, str]] = {
    "CB": ("progressive_passes_p90", "aerials_won_p90"),
    "FB-WB": ("op_key_passes_p90", "aerials_won_p90"),
    "DM-CM": ("op_key_passes_p90", "np_xg_p90"),
    "AM-Winger": ("op_key_passes_p90", "np_shots_p90"),
    "ST": ("aerials_won_p90", "np_shots_p90"),
}
# Every loading quoted in the axis notes and verdicts (code-reviewer, M4g1): (group, axis,
# claim, metric, value). "value": the loading rounds to it (2 dp); "min" / "max": also the
# axis's most negative / positive loading; "no_end": no loading of sign `value` reaches
# STYLE_END_MIN_LOADING; "dominant": the squared loading is at least DOMINANT_SHARE.
QUOTED_LOADINGS: tuple[tuple[str, int, str, str | None, float], ...] = (
    ("CB", 0, "value", "aerials_won_p90", -0.47), ("CB", 0, "value", "clearances_p90", -0.19),
    ("CB", 0, "value", "final_third_entries_pass_p90", 0.37),
    ("CB", 0, "value", "progressive_passes_p90", 0.36),
    ("CB", 1, "max", "aerials_won_p90", 0.82), ("CB", 1, "dominant", "aerials_won_p90", 0.82),
    ("CB", 1, "no_end", None, -1),
    ("FB-WB", 0, "value", "clearances_p90", -0.16), ("FB-WB", 0, "value", "aerials_won_p90", -0.20),
    ("FB-WB", 1, "max", "aerials_won_p90", 0.91), ("FB-WB", 1, "dominant", "aerials_won_p90", 0.91),
    ("FB-WB", 1, "no_end", None, -1),
    ("ST", 0, "value", "aerials_won_p90", 0.30), ("ST", 0, "value", "aerials_lost_p90", 0.28),
    ("ST", 0, "value", "aerial_win_pct", 0.20), ("ST", 0, "min", "np_shots_p90", -0.40),
    ("ST", 1, "max", "np_shots_p90", 0.57), ("ST", 1, "value", "aerial_win_pct", 0.39),
    ("ST", 1, "value", "aerials_won_p90", 0.33),
    ("ST", 1, "value", "final_third_entries_pass_p90", 0.27),
    ("ST", 1, "min", "pass_completion_pct", -0.37),
)


class StyleMapError(ValueError):
    """The fit cannot be shown honestly (a pin too weak, a quoted loading changed, ...)."""


@dataclass(frozen=True)
class Model:
    group: str
    metrics: tuple[str, ...]                 # metric-name order
    weights: tuple[float, ...]               # per-metric family weight of a complete row
    mean: tuple[float, ...]                  # of the weighted inputs
    components: tuple[tuple[float, ...], tuple[float, ...]]   # PC1, PC2, oriented
    evr: tuple[float, float]                 # explained variance ratio
    sd: tuple[float, float]                  # SD (ddof 1) of the plotted scores


@dataclass(frozen=True)
class StyleMap:
    model: Model
    keys: tuple[Key, ...]                    # plotted (complete) rows, key order
    scores: Mapping[Key, tuple[float, float]]
    partial: tuple[Key, ...]                 # listed, not plotted


def weighted_input(model_metrics: Sequence[str], weights: Sequence[float], row: RowDna
                   ) -> list[float]:
    out = []
    for m, w in zip(model_metrics, weights):
        p = row.cells[m].pct_local
        if p is None:
            raise StyleMapError(f"{m}: no percentile (only complete rows are plotted)")
        out.append(p / 100.0 * math.sqrt(w))
    return out


def orient(components: np.ndarray, metrics: Sequence[str], pins: Sequence[str]) -> np.ndarray:
    """Each component signed so its pin metric loads positive."""
    out = np.array(components, dtype=float, copy=True)
    for a, pin in enumerate(pins):
        c = out[a][list(metrics).index(pin)]
        if abs(c) < STYLE_END_MIN_LOADING:
            raise StyleMapError(f"pin {pin} loads {c:+.3f} on PC{a + 1}, below "
                                f"{STYLE_END_MIN_LOADING}")
        if c < 0:
            out[a] *= -1
    return out


def fit(group: str, specs: Sequence[MetricSpec], rows: Mapping[Key, RowDna]) -> StyleMap:
    """The style map of one group from its DNA rows (`specs`: the group's DNA metrics)."""
    metrics = tuple(sorted(s.column for s in specs))
    w = split_weights(sorted(specs, key=lambda s: s.column), set(metrics))
    weights = tuple(w[m] for m in metrics)
    keys = tuple(sorted(k for k, r in rows.items() if r.dna_status == COMPLETE))
    x = np.array([weighted_input(metrics, weights, rows[k]) for k in keys])
    pca = PCA(svd_solver="full").fit(x)
    comps = orient(pca.components_[:2], metrics, AXIS_PINS[group])
    scores = (x - pca.mean_) @ comps.T
    model = Model(group, metrics, weights, tuple(float(v) for v in pca.mean_),
                  (tuple(float(v) for v in comps[0]), tuple(float(v) for v in comps[1])),
                  (float(pca.explained_variance_ratio_[0]), float(pca.explained_variance_ratio_[1])),
                  (float(np.std(scores[:, 0], ddof=1)), float(np.std(scores[:, 1], ddof=1))))
    check_quoted(model)
    partial = tuple(sorted(k for k, r in rows.items() if r.dna_status != COMPLETE))
    return StyleMap(model, keys, {k: (float(s[0]), float(s[1])) for k, s in zip(keys, scores)},
                    partial)


def project(model: Model, x: Sequence[float]) -> tuple[float, float]:
    """The PC scores of a weighted input vector (the combined dot of a mover)."""
    return tuple(sum(c * (v - mu) for c, v, mu in zip(comp, x, model.mean))  # type: ignore[return-value]
                 for comp in model.components)


def pushes(model: Model, row: RowDna, axis: int) -> list[tuple[str, float]]:
    """Each metric's push along the axis, sorted by |push|, ties by metric name; they sum to
    the row's score."""
    x = weighted_input(model.metrics, model.weights, row)
    out = [(m, c * (v - mu)) for m, v, mu, c in
           zip(model.metrics, x, model.mean, model.components[axis])]
    return sorted(out, key=lambda t: (-abs(t[1]), t[0]))


def end_metrics(model: Model, axis: int, sign: int,
                threshold: float = STYLE_END_MIN_LOADING) -> list[str]:
    """The (up to 3) metrics defining one end: sign · loading ≥ threshold, unrounded."""
    cs = sorted(zip(model.components[axis], model.metrics), key=lambda t: (-sign * t[0], t[1]))
    return [m for c, m in cs[:3] if sign * c >= threshold]


def top_loadings(model: Model, axis: int, n: int = TOP_LOADINGS) -> list[tuple[str, float]]:
    """The n largest |loadings| of an axis, ties by metric name."""
    c = model.components[axis]
    order = sorted(range(len(model.metrics)), key=lambda j: (-abs(c[j]), model.metrics[j]))
    return [(model.metrics[j], c[j]) for j in order[:n]]


def lean_band(score: float, sd: float) -> str:
    """"near", "slight" or "strong" (z = score / sd)."""
    if sd <= 0:
        raise StyleMapError(f"no spread on the axis (sd {sd})")
    z = abs(score / sd)
    return "near" if z < LEAN_NEAR else "slight" if z < LEAN_STRONG else "strong"


def check_quoted(model: Model) -> None:
    """Fail when a quoted loading of this group no longer matches the fit."""
    for g, a, claim, metric, value in QUOTED_LOADINGS:
        if g != model.group:
            continue
        comp = dict(zip(model.metrics, model.components[a]))
        if claim == "no_end":
            hit = [m for m, c in comp.items() if value * c >= STYLE_END_MIN_LOADING]
            if hit:
                raise StyleMapError(f"quoted: {g} PC{a + 1} {'+' if value > 0 else '−'} end "
                                    f"now has {hit}")
            continue
        if metric is None:
            raise StyleMapError(f"quoted: {g} PC{a + 1} {claim} claim without a metric")
        if round(comp[metric], 2) != value:
            raise StyleMapError(f"quoted: {g} PC{a + 1} {metric} is {comp[metric]:+.3f}, the "
                                f"text says {value:+.2f}")
        if claim == "min" and metric != min(comp, key=comp.__getitem__):
            raise StyleMapError(f"quoted: {g} PC{a + 1} {metric} is no longer the most "
                                "negative loading")
        if claim == "max" and metric != max(comp, key=comp.__getitem__):
            raise StyleMapError(f"quoted: {g} PC{a + 1} {metric} is no longer the most "
                                "positive loading")
        if claim == "dominant" and comp[metric] ** 2 < DOMINANT_SHARE:
            raise StyleMapError(f"quoted: {g} PC{a + 1} {metric} carries "
                                f"{comp[metric] ** 2:.0%} of the axis, below {DOMINANT_SHARE:.0%}")
