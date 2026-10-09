"""
Movers on the style maps (plan 006 W1; moved from spikes/m4g_movers_display.py, plan 005 M4g3c,
ADR 004 §13.7). Display only: nothing here changes a fit, score, neighbour or population.

- stints: every season row of a player-season (player_id, season_id, source) with 2+ rows, any
  competition of the season parquet, in date order (first, then last match with minutes > 0,
  from player_match_stats.match_date, plan 006 gate G3), each with its fine role (roles.py;
  None for a goalkeeper row);
- a stint without a dot on a group's map gets a note code (NOTE_*): a PARTIAL competition (ADR
  003); a DNA row of another group; a partial DNA row of this group; no DNA row (under the
  nominal-minutes gate, ADR 004 D1);
- combined dot: a player-season with 2+ plotted rows in a group gets one dot, the
  nominal-minute-weighted average of its stints' map inputs, projected with the group's model.
  The projection is linear, so the dot lies on the segment between its stint dots. It is never
  part of the fit, neighbours, populations or ranks;
- between consecutive plotted stints: the pair distance (dna.pair_distance), each stint's rank
  in the other's candidate order (the player's own rows left out; pairs.ranks), and the
  N_CHANGED metrics with the largest |Δ local percentile| (ties by metric name).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping, Sequence

import polars as pl

from eleveniq.analytics import pairs, style_map
from eleveniq.analytics.dna import COMPLETE, DNA_INCOMPLETE, RowDna, pair_distance
from eleveniq.analytics.roles import FineRole, fine_role
from eleveniq.config import COMPETITION_SEASONS, MIN_MINUTES_THRESHOLD, MIN_MINUTES_TOLERANCE

Key = tuple
PlayerSeason = tuple            # (player_id, season_id, source)
N_CHANGED = 3
MAX_PLOTTED_STINTS = 3          # team colour slots on the map
SCORE_TOL = 1e-9

NOTE_PARTIAL = "partial_coverage"
NOTE_OTHER_GROUP = "other_group"
NOTE_INCOMPLETE = "profile_incomplete"
NOTE_UNDER_GATE = "under_gate"
NOTE_GOALKEEPER = "goalkeeper"


class MoversError(ValueError):
    """The movers facts disagree with the inputs (a stint without dates, a lost row, ...)."""


@dataclass(frozen=True)
class Stint:
    key: Key
    team: str
    competition_id: int
    group: str                   # the season row's position group
    minutes: float
    nominal: float               # minutes_nominal_equiv, the gate's clock
    first: dt.date
    last: dt.date
    role: FineRole | None        # None for a goalkeeper row


@dataclass(frozen=True)
class StintPair:
    distance: float
    rank_ab: int                 # b's place in a's candidate order
    n_a: int
    rank_ba: int
    n_b: int
    changed: tuple[str, ...]


@dataclass(frozen=True)
class Combined:
    stints: tuple[Key, ...]      # plotted stints, date order
    weights: tuple[float, ...]
    score: tuple[float, float]
    pairs: tuple[StintPair, ...]  # consecutive plotted stints


def partial_competitions() -> frozenset[int]:
    return frozenset(cs.competition_id for cs in COMPETITION_SEASONS if cs.coverage == "PARTIAL")


def stints(season: pl.DataFrame, match: pl.DataFrame,
           positions: Mapping[Key, Mapping[str, float]]) -> dict[PlayerSeason, list[Stint]]:
    """Multi-stint player-seasons, stints in date order (first, last, key)."""
    key_cols = list(pairs.KEY)
    rows = season.select(*key_cols, "team_name", "minutes_played", "minutes_nominal_equiv",
                         "position_group")
    by: dict[PlayerSeason, list[dict]] = {}
    for r in rows.iter_rows(named=True):
        k = tuple(r[c] for c in key_cols)
        by.setdefault((k[0], k[3], k[4]), []).append(r | {"key": k})
    multi = {p: v for p, v in by.items() if len(v) >= 2}
    wanted = {r["key"] for v in multi.values() for r in v}
    dates = (match.filter(pl.col("minutes_played") > 0)
             .group_by(key_cols).agg(pl.col("match_date").min().alias("first"),
                                     pl.col("match_date").max().alias("last")))
    span = {tuple(r[c] for c in key_cols): (r["first"], r["last"])
            for r in dates.iter_rows(named=True)}
    out: dict[PlayerSeason, list[Stint]] = {}
    for p, v in multi.items():
        sts = []
        for r in v:
            k = r["key"]
            if k not in span:
                raise MoversError(f"{k}: no match with minutes")
            if r["position_group"] == "GK":
                role = None
            elif k not in positions:
                raise MoversError(f"{k}: no player_season_positions rows")
            else:
                role = fine_role(positions[k])
            sts.append(Stint(k, r["team_name"], k[2], r["position_group"], r["minutes_played"],
                             r["minutes_nominal_equiv"], *span[k], role))
        out[p] = sorted(sts, key=lambda s: (s.first, s.last, s.key))
    if wanted - {s.key for v in out.values() for s in v}:
        raise MoversError("a multi-stint row was lost")
    return out


def stint_note(st: Stint, group: str, plotted: set[Key], dna_group: Mapping[Key, str],
               dna_status: Mapping[Key, str]) -> tuple[str | None, str | None]:
    """(note code, other group) for a stint on `group`'s map; (None, None) when it has a dot.
    A goalkeeper stint has no DNA row by design (ADR 002 §12): NOTE_GOALKEEPER."""
    k = st.key
    if st.group == "GK":
        return NOTE_GOALKEEPER, None
    if st.competition_id in partial_competitions():
        return NOTE_PARTIAL, None
    if k in plotted:
        return None, None
    if k in dna_group:
        g = dna_group[k]
        if g != group:
            return NOTE_OTHER_GROUP, g
        if dna_status[k] != COMPLETE:
            return NOTE_INCOMPLETE, None
        raise MoversError(f"{k}: complete row of {group} not plotted")
    if st.nominal >= MIN_MINUTES_THRESHOLD - MIN_MINUTES_TOLERANCE:
        raise MoversError(f"{k}: {st.nominal:.1f} nominal minutes in a pool league but no DNA row")
    return NOTE_UNDER_GATE, None


def weights(nominal: Sequence[float]) -> tuple[float, ...]:
    total = sum(nominal)
    if total <= 0:
        raise MoversError("combined dot: no minutes")
    return tuple(m / total for m in nominal)


def changed_most(a: RowDna, b: RowDna, metrics: Sequence[str]) -> tuple[str, ...]:
    """The N_CHANGED metrics with the largest |Δ local percentile|, ties by metric name."""
    gap = {m: abs(a.cells[m].pct_local - b.cells[m].pct_local) for m in metrics}  # type: ignore[operator]
    return tuple(sorted(metrics, key=lambda m: (-gap[m], m))[:N_CHANGED])


def rank_both(a: Key, b: Key, rows: Mapping[Key, RowDna]) -> tuple[int, int, int, int]:
    """(rank of b in a's order, N for a, rank of a in b's order, N for b): where the other stint
    would sit among the candidates: other players' rows of the group with dna_status complete or
    partial (dna_incomplete rows have no distance, ADR 004 §12.2); ties share a rank."""
    out: list[int] = []
    for x, y in ((a, b), (b, a)):
        cand = [(pair_distance(rows[x], rows[c]), c) for c in rows
                if c[0] != x[0] and rows[c].dna_status != DNA_INCOMPLETE]
        rk = pairs.ranks(cand + [(pair_distance(rows[x], rows[y]), y)])[y]
        out += [rk, len(cand)]
    return out[0], out[1], out[2], out[3]


def combined(sm: style_map.StyleMap, rows: Mapping[Key, RowDna], plotted: Sequence[Stint]
             ) -> Combined:
    """The combined dot of one player-season's plotted stints in one group."""
    if len(plotted) > MAX_PLOTTED_STINTS:
        raise MoversError(f"{plotted[0].key}: {len(plotted)} plotted stints; only "
                          f"{MAX_PLOTTED_STINTS} team colours")
    m = sm.model
    ws = weights([st.nominal for st in plotted])
    xs = [style_map.weighted_input(m.metrics, m.weights, rows[st.key]) for st in plotted]
    x = [sum(w * v[j] for w, v in zip(ws, xs)) for j in range(len(m.metrics))]
    score = style_map.project(m, x)
    seg = [sum(w * sm.scores[st.key][a] for w, st in zip(ws, plotted)) for a in (0, 1)]
    if any(abs(s - t) > SCORE_TOL for s, t in zip(score, seg)):
        raise MoversError(f"combined dot {plotted[0].key}: not on the segment of its stints")
    sp = []
    for sa, sb in zip(plotted, plotted[1:]):
        ra, rb = rows[sa.key], rows[sb.key]
        rab, na, rba, nb = rank_both(sa.key, sb.key, rows)
        sp.append(StintPair(pair_distance(ra, rb), rab, na, rba, nb,
                            changed_most(ra, rb, m.metrics)))
    return Combined(tuple(st.key for st in plotted), ws, score, tuple(sp))
