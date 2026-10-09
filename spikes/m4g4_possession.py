"""
M4g4c possession candidates, criteria and movers (plan 005 M4g4 plan + M4g4c, ADR 004 §13.8).

Reads the season and player-match parquets, the raw events (possession proxies), the committed
measurements (β = 0 checks, the M4g3b ball_recoveries verdict) and a registry snapshot (which
Defending metrics are DNA where). Writes data/interim/m4g4c/possession.json (stage "criteria")
and data/interim/m4g4c/movers.json (stage "movers"). Report only: no registry or pipeline change.
The independent check is spikes/m4g4_possession_reference.py. Never imports eleveniq
(tests/test_spikes_independent.py).

Spec (ADR 004 §13.8; plan 005 "M4g4 plan"; M4g4c plan, user 2026-10-08):
- Cells: every Defending DNA metric × outfield group of the snapshot (34 in 2015/16, all per-90s).
- Rows: the gated pool rows (in_percentile_pool, outfield, nominal ≥ 900 − 1e-6) = the primary
  scored rows (asserted against the percentiles parquet). G1: every selection quantity uses only
  these rows.
- G2 (stop, not filter): every gated row has opponent_open_play_passes_while_on ≥ 1,000 and, for
  each Defending DNA action of its group, team_<action>_while_on ≥ 20; else the run fails.
- E = opponent_open_play_passes_while_on / minutes_played × 90; E_ref(g) = median E of the group's
  gated rows (a pooled constant: it changes no rank or local percentile).
- Candidates, fixed order: beta_0 (raw) → beta_hat → team_share → beta_1.
  adj = p90 × (E_ref / E)^β; team_share = count / team_<action>_while_on.
- β̂ (D2): per team (league, team_id) with ≥ 1 gated group row: y = log(Σ count × 90 / Σ minutes),
  x = log(Σ opp × 90 / Σ minutes); both demeaned within league over the fitted teams; unweighted
  OLS slope Σxy / Σx². A team with Σ count = 0 (mean ≤ 0) fails the fit (the candidate fails, no
  drop). Fitted on all 4 leagues (D3: movers, criterion (3), D9) and leave-one-league-out (criteria
  (1), (2)). CI (report only): B = 2,000; stream m4g3_power.cell_rng("m4g4_beta_boot", metric,
  group); per resample, for each league in POOL order, teams sorted by team_id are drawn with
  replacement (rng.integers(0, n_l, n_l)); numpy linear 2.5 / 97.5% quantiles over finite slopes.
- Tie rule: a β-adjusted value (beta_hat, beta_1; criteria, D9 and movers) is rounded to 12
  significant digits, so values equal in exact arithmetic tie however they are computed (β = 1
  makes adj ∝ count / opponent passes). Not applied in criterion (3) (Pearson, no ranks).
- Criterion (1) (C1): per proxy (poss_dur_loo, poss_pass_loo; the M4c definitions), the Spearman
  ρ within each league (polars, average ranks); mean over the 4 leagues; |mean| < 0.1 on both.
  For beta_hat each league uses β̂ fitted without it.
- Sign flip (C2): fails when, on a proxy where |β = 0 mean| ≥ 0.05, the candidate mean has the
  opposite sign and |mean| ≥ 0.05. Flag "inconsistent across leagues": any league |ρ| ≥ 0.3.
- Criterion (2) (C3): Hazen local percentile of the candidate column against the league × group
  population (D3 dedupe: one row per player, most minutes, then competition_id, team_id); rows
  ranked within league × group by the proxy (average ranks), quintile ⌈5 · rank / n⌉; gap_l =
  |mean pct Q5 − mean pct Q1|; mean over the 4 leagues ≤ 10 on both proxies.
- Criterion (3): multi-split r_full ≥ 0.6 on the M4g2 split stream (m4g2_multisplit, its seed and
  stint table, K = the committed K). Halves adjusted with their own exposure and the all-4 β̂;
  team share = Σ count / Σ team count. G3: a β half needs ≥ 500 opponent passes, a share half
  ≥ 10 team actions, else undefined (the row leaves r_k for that split).
- Criterion (4) (D1, report only): D9 league means of the global percentile (D3 dedupe by group).
- Selection: the first candidate passing (1), (2), (3) and the sign-flip rule; none → raw.
- D17 (C5, report only): ρ between each adjusted Defending column and the group's other DNA
  members, raw and adjusted; pairs with |ρ| ≥ 0.7 either side, ≥ 0.85 flagged.
- Movers (D3): ball_recoveries_p90 × {beta_hat, team_share, beta_1} on the M4g3 mover design
  (m4g3_movers.Ctx), each mover adjusted with its group's E_ref / all-4 β̂; G3 on stints and on
  non-mover halves (a failing stint drops the mover; a failing half drops that baseline candidate).
  Scales (a), (b) on the adjusted column's own league × group population. Streams
  cell_rng("m4g4_draws"[#k], metric, candidate) / cell_rng("m4g4_boot"[#k], ...), k = 1–5.
  beta_0 reuses the M4g3b entry and is not re-run.

Usage:
  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g4c_snapshot/registry.json
  .venv/bin/python spikes/m4g4_possession.py --registry data/interim/m4g4c_snapshot/registry.json \
      --stage criteria|movers
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g2_multisplit as ms  # noqa: E402  (the M4g2 split assignment)
import m4g3_movers as mv  # noqa: E402  (the M4g3 mover design)
import m4g3_power as pw  # noqa: E402  (the rule, low power, cell_rng)
from reference_totals import PROJECT_ROOT  # noqa: E402

RAW = PROJECT_ROOT / "data" / "raw" / "statsbomb"
SEASON_PARQUET = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
MATCH_PARQUET = PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
PCT_PARQUET = PROJECT_ROOT / "data" / "processed" / "player_season_percentiles.parquet"
MEASUREMENTS = PROJECT_ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
OUT_DIR = PROJECT_ROOT / "data" / "interim" / "m4g4c"

SEASON_ID = 27
POOL = (2, 7, 11, 12)
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
KEY = ["player_id", "team_id", "competition_id", "season_id", "source"]
GATE, TOL = 900.0, 1e-6
PROXIES = ("poss_dur_loo", "poss_pass_loo")
CANDIDATES = ("beta_0", "beta_hat", "team_share", "beta_1")
G2_TEAM_MIN, G2_OPP_MIN = 20, 1000            # season rows: stop, not filter (user 2026-10-08)
G3_TEAM_MIN, G3_OPP_MIN = 10, 500             # halves (user 2026-10-08)
RHO_MAX, SIGN_DEAD, INCONSISTENT = 0.1, 0.05, 0.3
GAP_MAX, R_FULL_MIN = 10.0, 0.6
BETA_B = 2000
MOVER_METRIC = "ball_recoveries_p90"
MOVER_CANDIDATES = ("beta_hat", "team_share", "beta_1")
EXTRA_SEEDS = 5
OPP = "opponent_open_play_passes_while_on"
ACTIONS = ("ball_recoveries", "tackles_attempted", "pressures", "clearances", "dribbled_past",
           "fouls_committed", "blocks", "interceptions")
# The Defending DNA cells of the M4g4 plan (registry 463d1a4c31ae), written out: the run fails if
# the snapshot differs.
DEFENDING = {
    "CB": ("ball_recoveries_p90", "clearances_p90", "dribbled_past_p90", "pressures_p90",
           "tackles_attempted_p90"),
    "FB-WB": ("ball_recoveries_p90", "clearances_p90", "dribbled_past_p90", "fouls_committed_p90",
              "pressures_p90", "tackles_attempted_p90"),
    "DM-CM": ("ball_recoveries_p90", "blocks_p90", "clearances_p90", "dribbled_past_p90",
              "fouls_committed_p90", "interceptions_p90", "pressures_p90", "tackles_attempted_p90"),
    "AM-Winger": ("ball_recoveries_p90", "blocks_p90", "clearances_p90", "dribbled_past_p90",
                  "fouls_committed_p90", "interceptions_p90", "pressures_p90",
                  "tackles_attempted_p90"),
    "ST": ("ball_recoveries_p90", "blocks_p90", "clearances_p90", "dribbled_past_p90",
           "fouls_committed_p90", "pressures_p90", "tackles_attempted_p90"),
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def count_of(metric: str) -> str:
    return metric[: -len("_p90")]


def team_of(metric: str) -> str:
    return f"team_{count_of(metric)}_while_on"


def canon(x):
    """The tie rule: 12 significant digits (scalar or array)."""
    if isinstance(x, np.ndarray):
        return np.array([float(f"{v:.12g}") for v in x.tolist()])
    return float(f"{x:.12g}")


def hazen(sorted_pop: np.ndarray, x: np.ndarray) -> np.ndarray:
    lo = np.searchsorted(sorted_pop, x, side="left")
    hi = np.searchsorted(sorted_pop, x, side="right")
    return (lo + 0.5 * (hi - lo)) / len(sorted_pop) * 100.0


# ---------------------------------------------------------------- data

def load_registry(path: Path) -> dict:
    reg = json.loads(path.read_text())
    family = {(c["column"], c["group"]): c["family"] for c in reg["cells"]}
    for g in GROUPS:
        got = tuple(sorted(m for m in reg["groups"][g]["dna"] if family[(m, g)] == "Defending"))
        if got != DEFENDING[g]:
            fail(f"snapshot Defending DNA for {g} {got} != the M4g4 plan's {DEFENDING[g]}")
    return reg


def match_dates() -> dict[int, str]:
    """match_id → 'date kick_off' (the M4g2 stint order key, m4_decision_measurements)."""
    out = {}
    for comp in (*POOL, 9):
        p = RAW / "matches" / str(comp) / f"{SEASON_ID}.json"
        if p.exists():
            for m in json.loads(p.read_text()):
                # no kick-off → the date alone, exactly the M4g2 stint-order key
                out[m["match_id"]] = f"{m['match_date']} {m.get('kick_off') or ''}"
    return out


def possession_proxies(match: pl.DataFrame) -> pl.DataFrame:
    """The M4c leave-one-out possession proxies per season key (m4_decision_measurements D12):
    per match row (team − own) / (match − own) for possession duration and passes, then the
    minutes-weighted mean over the key's match rows."""
    own: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    team: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    tot: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for mid in match["match_id"].unique().sort().to_list():
        for e in json.loads((RAW / "events" / f"{mid}.json").read_text()):
            if e.get("period", 0) not in (1, 2, 3, 4):
                continue
            pid = (e.get("player") or {}).get("id")
            pteam = (e.get("possession_team") or {}).get("id")
            tm = (e.get("team") or {}).get("id")
            dur = e.get("duration") or 0.0      # StatsBomb omits duration on instant events (the M4c definition)
            is_pass = e["type"]["name"] == "Pass"
            if pteam is not None:
                team[(mid, pteam)][0] += dur
                tot[mid][0] += dur
            if is_pass and tm is not None:
                team[(mid, tm)][1] += 1
                tot[mid][1] += 1
            if pid is None:
                continue
            if pteam is not None and pteam == tm:
                own[(mid, pid)][0] += dur
            if is_pass:
                own[(mid, pid)][1] += 1
    rows = []
    for mid, pid, tid, mins, k in match.select("match_id", "player_id", "team_id", "minutes_played",
                                              "_k").iter_rows():
        o, t, a = own.get((mid, pid), [0.0, 0.0]), team.get((mid, tid)), tot[mid]
        if t is None:
            fail(f"match {mid}: team {tid} has no event")
        rows.append({"_k": k, "minutes_played": mins,
                     "poss_dur_loo": (t[0] - o[0]) / (a[0] - o[0]),
                     "poss_pass_loo": (t[1] - o[1]) / (a[1] - o[1])})
    df = pl.DataFrame(rows)
    return df.group_by("_k").agg(
        *[((pl.col(p) * pl.col("minutes_played")).sum() / pl.col("minutes_played").sum()).alias(p)
          for p in PROXIES])


class Data:
    """Gated rows with proxies, the M4g2 stint table, and the committed measurements."""

    def __init__(self) -> None:
        self.meas = json.loads(MEASUREMENTS.read_text())
        season = pl.read_parquet(SEASON_PARQUET).filter(pl.col("season_id") == SEASON_ID)
        season = season.with_columns(
            (pl.col("minutes_nominal_equiv") >= GATE - TOL).fill_null(False).alias("gate"))
        if season.filter(pl.col("gate").ne_missing(pl.col("meets_min_minutes"))).height:
            fail("gate ≠ meets_min_minutes")
        self.season = season
        cols = ["minutes_played", OPP, *ACTIONS, *[f"team_{a}_while_on" for a in ACTIONS]]
        nulls = {c: n for c, n in season.select(cols).null_count().row(0, named=True).items() if n}
        if nulls:
            fail(f"null count columns in the season parquet: {nulls}")
        gated = season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield") & pl.col("gate"))
        pct = (pl.read_parquet(PCT_PARQUET, columns=[*KEY, "cohort_role", "percentile_status"])
               .filter((pl.col("cohort_role") == "primary") & (pl.col("percentile_status") == "scored"))
               .select(KEY).unique())
        if sorted(gated.select(KEY).rows()) != sorted(pct.rows()):
            fail("gated rows ≠ the percentiles parquet's primary scored rows")
        gated = gated.sort(KEY).with_row_index("_k")
        dates = match_dates()
        match = (pl.read_parquet(MATCH_PARQUET)
                 .filter(pl.col("competition_id").is_in(POOL) & (pl.col("season_id") == SEASON_ID)))
        self.match = match
        gm = match.join(gated.select(*KEY, "_k"), on=KEY, how="inner")
        prox = possession_proxies(gm)
        self.gated = gated.join(prox, on="_k", how="left")
        if self.gated.select(PROXIES).null_count().sum_horizontal().item():
            fail("a gated row has no possession proxy")
        self.dates = dates

    def g2_check(self) -> dict:
        """G2: stop, not filter."""
        bad = []
        for r in self.gated.iter_rows(named=True):
            if r[OPP] < G2_OPP_MIN:
                bad.append((r["player_name"], r["team_name"], OPP, r[OPP]))
            for m in DEFENDING[r["position_group"]]:
                if r[team_of(m)] < G2_TEAM_MIN:
                    bad.append((r["player_name"], r["team_name"], team_of(m), r[team_of(m)]))
        if bad:
            fail(f"G2: {len(bad)} gated values below the minimum count: {bad[:20]}")
        mins = {OPP: int(self.gated[OPP].min())}
        for g in GROUPS:
            sub = self.gated.filter(pl.col("position_group") == g)
            for m in DEFENDING[g]:
                mins[f"{g} {team_of(m)}"] = int(sub[team_of(m)].min())
        return mins


# ---------------------------------------------------------------- per-cell quantities

class Cell:
    """One metric × group: values per candidate on the gated rows, in a fixed row order."""

    def __init__(self, d: Data, metric: str, grp: str) -> None:
        self.metric, self.grp = metric, grp
        sub = d.gated.filter(pl.col("position_group") == grp).sort(KEY)
        self.sub = sub
        self.comp = sub["competition_id"].to_numpy()
        self.team = sub["team_id"].to_numpy()
        self.player = sub["player_id"].to_numpy()
        self.minutes = sub["minutes_played"].to_numpy().astype(float)
        self.count = sub[count_of(metric)].to_numpy().astype(float)
        self.opp = sub[OPP].to_numpy().astype(float)
        self.team_count = sub[team_of(metric)].to_numpy().astype(float)
        self.p90 = self.count / self.minutes * 90.0
        stored = sub[metric].to_numpy()
        if np.max(np.abs(stored - self.p90)) > 1e-9:
            fail(f"{metric} × {grp}: count / minutes × 90 ≠ the stored column")
        self.E = self.opp / self.minutes * 90.0
        self.e_ref = float(np.median(self.E))
        self.share = self.count / self.team_count
        self.proxy = {p: sub[p].to_numpy() for p in PROXIES}
        # D3 local population: one row per player within league × group
        order = sorted(range(len(sub)), key=lambda i: (-self.minutes[i], self.comp[i], self.team[i]))
        seen, keep = set(), np.zeros(len(sub), dtype=bool)
        for i in order:
            k = (self.player[i], self.comp[i])
            if k not in seen:
                seen.add(k)
                keep[i] = True
        self.local_keep = keep
        seen_g, keep_g = set(), np.zeros(len(sub), dtype=bool)
        for i in order:
            if self.player[i] not in seen_g:
                seen_g.add(self.player[i])
                keep_g[i] = True
        self.global_keep = keep_g

    def beta_adj(self, beta: np.ndarray | float) -> np.ndarray:
        return canon(self.p90 * (self.e_ref / self.E) ** beta)

    # β̂
    def team_table(self) -> dict[int, list[tuple[int, float, float]]]:
        """league → [(team_id, x, y)] (raw logs; None y when the team's count is 0)."""
        acc: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        for c, t, m, n, o in zip(self.comp, self.team, self.minutes, self.count, self.opp):
            a = acc[(int(c), int(t))]
            a[0] += m
            a[1] += n
            a[2] += o
        out: dict[int, list] = {c: [] for c in POOL}
        for (c, t), (m, n, o) in sorted(acc.items()):
            out[c].append((t, math.log(o * 90.0 / m), math.log(n * 90.0 / m) if n > 0 else None))
        return out

    def fit(self, leagues) -> tuple[float | None, list[int]]:
        """(slope, zero teams); slope None when a team's mean is ≤ 0."""
        tab = self.team_table()
        zero = [t for c in leagues for t, _, y in tab[c] if y is None]
        if zero:
            return None, zero
        sxy = sxx = 0.0
        for c in leagues:
            xs = np.array([x for _, x, _ in tab[c]])
            ys = np.array([y for _, _, y in tab[c]])
            xs, ys = xs - xs.mean(), ys - ys.mean()
            sxy += float((xs * ys).sum())
            sxx += float((xs * xs).sum())
        return sxy / sxx, []

    def beta_ci(self) -> dict:
        tab = self.team_table()
        if any(y is None for c in POOL for _, _, y in tab[c]):
            return {"ci": None}
        rng = pw.cell_rng("m4g4_beta_boot", self.metric, self.grp)
        arr = {c: (np.array([x for _, x, _ in tab[c]]), np.array([y for _, _, y in tab[c]])) for c in POOL}
        slopes = np.empty(BETA_B)
        for b in range(BETA_B):
            sxy = sxx = 0.0
            for c in POOL:
                xs, ys = arr[c]
                idx = rng.integers(0, len(xs), len(xs))
                x, y = xs[idx] - xs[idx].mean(), ys[idx] - ys[idx].mean()
                sxy += float((x * y).sum())
                sxx += float((x * x).sum())
            slopes[b] = sxy / sxx if sxx > 0 else np.nan
        fin = slopes[np.isfinite(slopes)]
        return {"ci": [float(np.quantile(fin, 0.025)), float(np.quantile(fin, 0.975))],
                "undefined": int((~np.isfinite(slopes)).sum()),
                "teams": {str(c): len(tab[c]) for c in POOL}}

    # criteria (1), (2), (4) on a value vector (any candidate)
    def rho(self, values: np.ndarray) -> dict:
        out = {}
        for p in PROXIES:
            per = {}
            for c in POOL:
                s = self.comp == c
                df = pl.DataFrame({"a": self.proxy[p][s], "b": values[s]})
                per[str(c)] = df.select(pl.corr("a", "b", method="spearman")).item()
            out[p] = {"by_league": per, "mean": float(np.mean(list(per.values())))}
        return out

    def local_pct(self, values: np.ndarray) -> np.ndarray:
        out = np.empty(len(values))
        for c in POOL:
            s = self.comp == c
            pop = np.sort(values[s & self.local_keep])
            out[s] = hazen(pop, values[s])
        return out

    def quintile_gap(self, values: np.ndarray) -> dict:
        pct = self.local_pct(values)
        out = {}
        for p in PROXIES:
            per, teams = {}, {}
            for c in POOL:
                s = np.flatnonzero(self.comp == c)
                rk = pl.Series(self.proxy[p][s]).rank("average").to_numpy()
                q = np.ceil(5 * rk / len(s)).astype(int)
                per[str(c)] = abs(float(pct[s][q == 5].mean()) - float(pct[s][q == 1].mean()))
                teams[str(c)] = [len(set(self.team[s][q == k].tolist())) for k in (1, 5)]
            out[p] = {"by_league": per, "gap": float(np.mean(list(per.values()))),
                      "teams_q1_q5": teams}
        return out

    def d9(self, values: np.ndarray) -> dict:
        pop = np.sort(values[self.global_keep])
        gp = hazen(pop, values)
        return {str(c): float(gp[self.comp == c].mean()) for c in POOL}


def per_league(cell: Cell, betas: dict[int, float]) -> np.ndarray:
    """adj with each league's own β (leave-one-league-out)."""
    out = np.empty(len(cell.p90))
    for c in POOL:
        s = cell.comp == c
        out[s] = cell.p90[s] * (cell.e_ref / cell.E[s]) ** betas[c]
    return canon(out)


# ---------------------------------------------------------------- criterion (3): multi-split

class Splits:
    """The M4g2 stint table (m4_decision_measurements D19) with the M4g4 columns."""

    def __init__(self, d: Data) -> None:
        base = (d.season.filter(pl.col("in_percentile_pool") & pl.col("is_outfield"))
                .select(*KEY, "position_group", "gate").sort(KEY))
        played = (d.match.filter(pl.col("minutes_played") > 0)
                  .with_columns(pl.col("match_id").replace_strict(d.dates, return_dtype=pl.Utf8)
                                .alias("match_date")))
        rows = played.join(base.select(KEY), on=KEY).sort([*KEY, "match_date", "match_id"])
        stints = base.join(rows.group_by(KEY, maintain_order=True).len("n_matches"), on=KEY, how="left")
        if stints["n_matches"].null_count() or stints["n_matches"].min() < 1:
            fail("a pool outfield stint has no played match")
        self.cols = ["minutes_played", OPP, *ACTIONS, *[f"team_{a}_while_on" for a in ACTIONS]]
        self.vals = rows.select(self.cols).cast(pl.Float64).to_numpy()
        self.n = stints["n_matches"].to_numpy().astype(np.int64)
        self.starts = np.concatenate([[0], np.cumsum(self.n)[:-1]]).astype(np.int64)
        self.group = stints["position_group"].to_numpy().astype(str)
        self.league = stints["competition_id"].to_numpy()
        self.gate = stints["gate"].to_numpy()

    def run(self, cells: dict, params: dict, k: int) -> dict:
        """(metric, group, candidate) → r_full summary over k splits of the M4g2 stream."""
        col = {c: i for i, c in enumerate(self.cols)}
        rng = np.random.default_rng(ms.SEED)
        idx = {g: np.flatnonzero(self.gate & (self.group == g)) for g in GROUPS}
        rf: dict[tuple, list] = defaultdict(list)
        nrow: dict[tuple, list] = defaultdict(list)
        for _ in range(k):
            h0 = ms.half_assignment(self.n, rng)
            m0 = h0.astype(float)[:, None]
            s = (np.add.reduceat(self.vals * m0, self.starts, axis=0),
                 np.add.reduceat(self.vals * (1.0 - m0), self.starts, axis=0))
            c0 = np.add.reduceat(h0.astype(np.int64), self.starts)
            cnt = (c0, self.n - c0)
            for g in GROUPS:
                rows = idx[g]
                names, A, B = [], [], []
                for metric in DEFENDING[g]:
                    for cand in CANDIDATES:
                        if cand == "beta_hat" and params[(metric, g)]["beta_hat"] is None:
                            continue
                        hv = []
                        for h in (0, 1):
                            sh = s[h][rows]
                            num, mins = sh[:, col[count_of(metric)]], sh[:, col["minutes_played"]]
                            opp, tm = sh[:, col[OPP]], sh[:, col[team_of(metric)]]
                            with np.errstate(divide="ignore", invalid="ignore"):
                                if cand == "team_share":
                                    v = np.where(tm >= G3_TEAM_MIN, num / tm, np.nan)
                                else:
                                    p90 = np.where(cnt[h][rows] > 0, num / mins * 90.0, np.nan)
                                    if cand == "beta_0":
                                        v = p90
                                    else:
                                        beta = 1.0 if cand == "beta_1" else params[(metric, g)]["beta_hat"]
                                        e = opp / mins * 90.0
                                        v = np.where(opp >= G3_OPP_MIN,
                                                     p90 * (params[(metric, g)]["e_ref"] / e) ** beta, np.nan)
                            hv.append(v)
                        names.append((metric, g, cand))
                        A.append(hv[0])
                        B.append(hv[1])
                r, nn, _ = ms._pooled_r(np.array(A).T, np.array(B).T, self.league[rows].astype(np.int64))
                rfk = ms.spearman_brown(r)
                for j, nm in enumerate(names):
                    rf[nm].append(float(rfk[j]))
                    nrow[nm].append(int(nn[j]))
        out = {}
        for nm, v in rf.items():
            a = np.array(v)
            if np.isnan(a).any():
                fail(f"r undefined in {int(np.isnan(a).sum())} splits for {nm}")
            out[nm] = {"r_full": float(a.mean()), "sd": float(a.std(ddof=1)),
                       "mc_se": float(a.std(ddof=1) / math.sqrt(len(a))),
                       "n_min": int(min(nrow[nm])), "n_max": int(max(nrow[nm])),
                       "n_full": int(len(idx[nm[1]]))}
        return out


# ---------------------------------------------------------------- criteria stage

def evaluate(d: Data, k: int | None = None) -> dict:
    g2 = d.g2_check()
    cells, params = {}, {}
    for g in GROUPS:
        for m in DEFENDING[g]:
            c = Cell(d, m, g)
            cells[(m, g)] = c
            b_all, zero = c.fit(POOL)
            lolo = {}
            for l in POOL:
                bl, _ = c.fit([x for x in POOL if x != l])
                lolo[l] = bl
            params[(m, g)] = {"e_ref": c.e_ref, "beta_hat": b_all, "zero_teams": zero,
                              "lolo": lolo, "ci": c.beta_ci() if b_all is not None else {"ci": None}}
    k = d.meas["reliability"]["k"] if k is None else k
    print(f"criterion (3): {k} splits of the M4g2 stream …", flush=True)
    rel = Splits(d).run(cells, params, k)

    out_cells = []
    for (m, g), c in cells.items():
        p = params[(m, g)]
        stored = d.meas["metrics"][m]["groups"][g]
        cand_out = {}
        ref_rho = None
        for cand in CANDIDATES:
            rec: dict = {}
            if cand == "beta_hat" and (p["beta_hat"] is None or any(v is None for v in p["lolo"].values())):
                rec = {"fit_failed": True, "zero_teams": p["zero_teams"], "passes": False}
                cand_out[cand] = rec
                continue
            if cand == "beta_0":
                vals_crit = vals_all = c.p90
            elif cand == "beta_1":
                vals_crit = vals_all = c.beta_adj(1.0)
            elif cand == "team_share":
                vals_crit = vals_all = c.share
            else:
                vals_crit = per_league(c, p["lolo"])
                vals_all = c.beta_adj(p["beta_hat"])
            rho = c.rho(vals_crit)
            gap = c.quintile_gap(vals_crit)
            r3 = rel[(m, g, cand)]
            if cand == "beta_0":
                ref_rho = {px: rho[px]["mean"] for px in PROXIES}
            c1 = all(abs(rho[px]["mean"]) < RHO_MAX for px in PROXIES)
            c2 = all(gap[px]["gap"] <= GAP_MAX for px in PROXIES)
            c3 = r3["r_full"] >= R_FULL_MIN
            flip = any(abs(ref_rho[px]) >= SIGN_DEAD and abs(rho[px]["mean"]) >= SIGN_DEAD
                       and math.copysign(1, rho[px]["mean"]) != math.copysign(1, ref_rho[px])
                       for px in PROXIES)
            incons = any(abs(v) >= INCONSISTENT for px in PROXIES for v in rho[px]["by_league"].values())
            rec = {"c1_rho": rho, "c1_pass": c1, "c2_gap": gap, "c2_pass": c2,
                   "c3_r_full": r3, "c3_pass": c3, "sign_flip": flip,
                   "inconsistent_across_leagues": incons, "c4_d9_league_means": c.d9(vals_all),
                   "passes": c1 and c2 and c3 and not flip}
            cand_out[cand] = rec
        # β = 0 equals the committed M4c / M4g2 measurements
        b0 = cand_out["beta_0"]
        for px in PROXIES:
            for l, v in b0["c1_rho"][px]["by_league"].items():
                if abs(v - stored["possession"][px]["by_league"][l]) > 5e-9:
                    fail(f"{m} × {g} {px} league {l}: β = 0 ρ {v} ≠ committed "
                         f"{stored['possession'][px]['by_league'][l]}")
        if k == d.meas["reliability"]["k"] and abs(b0["c3_r_full"]["r_full"] - stored["r_full"]) > 5e-9:
            fail(f"{m} × {g}: β = 0 r_full {b0['c3_r_full']['r_full']} ≠ committed {stored['r_full']}")
        for l, v in b0["c4_d9_league_means"].items():
            if abs(v - stored["league_mean_global_pct"][l]) > 5e-9:
                fail(f"{m} × {g} league {l}: β = 0 D9 mean {v} ≠ committed")
        selected = next((cd for cd in CANDIDATES if cand_out[cd]["passes"]), None)
        out_cells.append({
            "metric": m, "group": g, "directional": m == MOVER_METRIC,
            "e_ref": p["e_ref"], "beta_hat": p["beta_hat"], "zero_teams": p["zero_teams"],
            "beta_hat_lolo": {str(l): v for l, v in p["lolo"].items()}, "beta_hat_ci": p["ci"],
            "flags": {"rises_with_own_possession": p["beta_hat"] is not None and p["beta_hat"] < 0,
                      "abs_beta_hat_gt_1": p["beta_hat"] is not None and abs(p["beta_hat"]) > 1},
            "candidates": cand_out, "selected": selected})
    pct_check = check_local_pct(cells)
    return {"generated_by": "spikes/m4g4_possession.py --stage criteria",
            "spec": "ADR 004 §13.8; plan 005 M4g4 plan / M4g4c", "k": k, "g2_minimums": g2,
            "rules": {"rho_max": RHO_MAX, "sign_dead_zone": SIGN_DEAD, "inconsistent": INCONSISTENT,
                      "gap_max": GAP_MAX, "r_full_min": R_FULL_MIN, "g2": [G2_TEAM_MIN, G2_OPP_MIN],
                      "g3": [G3_TEAM_MIN, G3_OPP_MIN], "beta_b": BETA_B},
            "local_pct_check": pct_check, "cells": out_cells}


def check_local_pct(cells: dict) -> int:
    """β = 0 local percentiles equal the pipeline's pct_local on every cell row."""
    pct = (pl.read_parquet(PCT_PARQUET, columns=[*KEY, "metric", "cohort_role", "percentile_status",
                                                 "pct_local"])
           .filter((pl.col("cohort_role") == "primary") & (pl.col("percentile_status") == "scored")))
    n = 0
    for (m, g), c in cells.items():
        mine = c.sub.select(KEY).with_columns(pl.Series("mine", c.local_pct(c.p90)))
        j = mine.join(pct.filter(pl.col("metric") == m), on=KEY, how="left")
        if j["pct_local"].null_count() or (j["mine"] - j["pct_local"]).abs().max() > 1e-9:
            fail(f"{m} × {g}: β = 0 local percentile ≠ pipeline pct_local")
        n += j.height
    return n


# ---------------------------------------------------------------- movers stage

EXTRA_COLS = ("ball_recoveries", OPP, "team_ball_recoveries_while_on")


def mover_value(cand: str, prm: dict, sums: dict) -> float:
    """The adjusted value of a stint or half from its sums; NaN when G3 fails."""
    mins, n = sums["minutes_played"], sums["ball_recoveries"]
    if cand == "team_share":
        tm = sums["team_ball_recoveries_while_on"]
        return n / tm if tm >= G3_TEAM_MIN else math.nan
    opp = sums[OPP]
    if opp < G3_OPP_MIN or mins <= 0:
        return math.nan
    beta = 1.0 if cand == "beta_1" else prm["beta_hat"]
    return canon(n / mins * 90.0 * (prm["e_ref"] / (opp / mins * 90.0)) ** beta)


class MoverPop:
    """Scales (a), (b) on the adjusted column's own league × group reference population."""

    def __init__(self, ctx: mv.Ctx, cand: str, params: dict) -> None:
        self.p: dict[str, dict] = {}
        for g in GROUPS:
            prm = params[g]
            by, resid = {}, []
            for comp in POOL:
                vals = [mover_value(cand, prm, r) for r in ctx.local.filter(
                    (pl.col("competition_id") == comp) & (pl.col("position_group") == g)).to_dicts()]
                if any(math.isnan(v) for v in vals):
                    fail(f"G3 fails on a gated reference row ({cand}, {g}); G2 should prevent it")
                a = np.sort(np.array(vals))
                by[comp] = (a, float(a.mean()))
                resid += list(a - a.mean())
            self.p[g] = {"league": by, "sd": float(np.std(resid, ddof=1))}

    def scales(self, g: str, comp: int, v: float) -> tuple[float, float]:
        a, mu = self.p[g]["league"][comp]
        return float(hazen(a, np.array([v]))[0]), (v - mu) / self.p[g]["sd"]


def mover_eligibility(ctx: mv.Ctx, cand: str, params: dict) -> list[dict]:
    """Per mover: G3 on the stints, then the eligible baseline candidates (no RNG)."""
    out = []
    for stints, cand_halves in zip(ctx.movers, ctx.halves):
        g = stints[0]["position_group"]
        prm = params[g]
        sv = [mover_value(cand, prm, s) for s in stints]
        rec = {"player": stints[0]["player_name"], "group": g, "values": sv,
               "stint_g3_fail": any(math.isnan(v) for v in sv), "candidates": len(cand_halves)}
        elig = []
        for c in cand_halves:
            hv = [mover_value(cand, prm, c[h]) for h in ("h1", "h2")]
            if not any(math.isnan(v) for v in hv):
                elig.append((c["comp"], hv[0], hv[1]))
        rec["eligible"] = elig
        rec["dropped_halves"] = len(cand_halves) - len(elig)
        out.append(rec)
    return out


def run_candidate(ctx: mv.Ctx, cand: str, params: dict, k: int = 0, b: int = pw.B,
                  power_datasets: int | None = pw.DATASETS) -> dict:
    sfx = "" if k == 0 else f"#{k}"
    rng_draw = pw.cell_rng("m4g4_draws" + sfx, MOVER_METRIC, cand)
    pop = MoverPop(ctx, cand, params)
    d2, h1s, h2s, absa, absb, base_a, base_b, d2_a, base2_a, ids, excl = ([] for _ in range(11))
    for stints, el in zip(ctx.movers, mover_eligibility(ctx, cand, params)):
        g = stints[0]["position_group"]
        if el["stint_g3_fail"]:
            excl.append(f"{el['player']} (stint below G3)")
            continue
        if not el["eligible"]:
            excl.append(f"{el['player']} (no eligible baseline)")
            continue
        elig = el["eligible"]
        pa = [pop.scales(g, s["competition_id"], v) for s, v in zip(stints, el["values"])]
        pick = rng_draw.integers(0, len(elig), pw.DRAWS)
        hb = np.array([[pop.scales(g, elig[j][0], elig[j][1 + h]) for h in (0, 1)] for j in pick])
        d2.append((pa[1][1] - pa[0][1]) ** 2)
        absa.append(abs(pa[1][0] - pa[0][0]))
        absb.append(abs(pa[1][1] - pa[0][1]))
        h1s.append(hb[:, 0, 1])
        h2s.append(hb[:, 1, 1])
        base_a.append(float(np.mean(np.abs(hb[:, 1, 0] - hb[:, 0, 0]))))
        base_b.append(float(np.mean(np.abs(hb[:, 1, 1] - hb[:, 0, 1]))))
        d2_a.append((pa[1][0] - pa[0][0]) ** 2)
        base2_a.append(float(np.mean((hb[:, 1, 0] - hb[:, 0, 0]) ** 2)))
        ids.append(el["player"])
    n = len(d2)
    rec: dict = {"n": n, "excluded": excl}
    if n:
        rec |= {"mover_mean_abs_a": float(np.mean(absa)), "mover_mean_abs_b": float(np.mean(absb)),
                "baseline_mean_abs_a": float(np.mean(base_a)), "baseline_mean_abs_b": float(np.mean(base_b))}
    if n >= pw.MIN_MOVERS:
        h1a, h2a = np.array(h1s), np.array(h2s)
        _, _, r_m = pw._f_hat(np.float64(1.0), np.float64(1.0), pw._moments(h1a, h2a).sum(0))
        rec["r_m"] = float(r_m)
        if power_datasets is not None:
            rec["low_power"] = pw.low_power(n, float(r_m), power_datasets, b)
        res = pw.mover_rule(np.array(d2), h1a, h2a, pw.cell_rng("m4g4_boot" + sfx, MOVER_METRIC, cand), b=b)
        rec |= {"f_hat": res.f_hat, "s2_rms": res.s2, "s2_rms_a": math.fsum(d2_a) / math.fsum(base2_a),
                "ci": [res.ci_lo, res.ci_hi], "verdict": res.verdict, "point_pass": res.point_pass,
                "boot_inf": res.boot_inf}
    else:
        rec["verdict"] = "untestable"
    rec["movers"] = ids
    return rec


def group_params(crit: dict) -> dict[str, dict[str, dict]]:
    """candidate → group → {e_ref, beta_hat} for MOVER_METRIC (the all-4 fit)."""
    out: dict[str, dict] = {c: {} for c in MOVER_CANDIDATES}
    for c in crit["cells"]:
        if c["metric"] == MOVER_METRIC:
            for cand in MOVER_CANDIDATES:
                out[cand][c["group"]] = {"e_ref": c["e_ref"], "beta_hat": c["beta_hat"]}
    return out


def g3_exclusions(ctx: mv.Ctx, crit: dict) -> dict:
    """Stop 1 addition: G3 exclusions per candidate, before any verdict."""
    params = group_params(crit)
    out = {}
    for cand in MOVER_CANDIDATES:
        if any(params[cand][g]["beta_hat"] is None for g in GROUPS) and cand == "beta_hat":
            out[cand] = {"fit_failed_groups": [g for g in GROUPS if params[cand][g]["beta_hat"] is None]}
            continue
        el = mover_eligibility(ctx, cand, params[cand])
        stint = [e["player"] for e in el if e["stint_g3_fail"]]
        nobase = [e["player"] for e in el if not e["stint_g3_fail"] and not e["eligible"]]
        n = len(el) - len(stint) - len(nobase)
        out[cand] = {"movers": len(el), "stint_g3_excluded": stint, "no_eligible_baseline": nobase,
                     "n": n, "below_min_movers": n < pw.MIN_MOVERS,
                     "baseline_halves_dropped": sum(e["dropped_halves"] for e in el),
                     "baseline_candidates": sum(e["candidates"] for e in el),
                     "min_eligible_per_mover": min((len(e["eligible"]) for e in el
                                                    if not e["stint_g3_fail"]), default=0)}
    return out


def movers_stage(crit: dict, b: int = pw.B, power_datasets: int = pw.DATASETS,
                 extra_seeds: int = EXTRA_SEEDS) -> dict:
    ctx = mv.Ctx(extra_cols=EXTRA_COLS)
    params = group_params(crit)
    sel = {c["group"]: c["selected"] for c in crit["cells"] if c["metric"] == MOVER_METRIC}
    out = {}
    for cand in MOVER_CANDIDATES:
        if cand == "beta_hat" and any(params[cand][g]["beta_hat"] is None for g in GROUPS):
            fail("beta_hat mover run with a failed group fit: not specified")
        rec = run_candidate(ctx, cand, params[cand], 0, b, power_datasets)
        rec["extra_seeds"] = []
        for k in range(1, extra_seeds + 1):
            r = run_candidate(ctx, cand, params[cand], k, b, None)
            rec["extra_seeds"].append({"k": k, "n": r["n"], "verdict": r["verdict"],
                                       **({"ci": r["ci"]} if "ci" in r else {})})
        rec["used_for_restore_in"] = sorted(g for g in GROUPS if sel[g] == cand)
        out[cand] = rec
        lp = rec.get("low_power", {})
        print(f"{cand:11s} n={rec['n']} {rec['verdict']:12s} "
              + (f"f̂={rec['f_hat']:.3f} CI=[{rec['ci'][0]:.3f}, {rec['ci'][1]:.3f}] R_m={rec['r_m']:.3f} "
                 f"power={lp.get('power_at_f0', float('nan')):.2f} √s2_rms_a={math.sqrt(rec['s2_rms_a']):.3f} "
                 f"seeds={[e['verdict'] for e in rec['extra_seeds']]}" if "f_hat" in rec else ""), flush=True)
    m4g3b = crit_reuse(json.loads(MEASUREMENTS.read_text()))
    return {"generated_by": "spikes/m4g4_possession.py --stage movers", "metric": MOVER_METRIC,
            "seed": pw.SEED, "draws": pw.DRAWS, "b": b, "movers": len(ctx.movers),
            "stream_keys": {"primary": ["m4g4_draws", "m4g4_boot"],
                            "extra": [[f"m4g4_draws#{k}", f"m4g4_boot#{k}"] for k in range(1, extra_seeds + 1)]},
            "selected": sel, "beta_0_reused": m4g3b, "candidates": out}


D17_REPORT, D17_RHO = 0.7, 0.85


def d17_report(crit: dict, reg: dict) -> dict:
    """C5 (report only): Spearman ρ over each group's gated rows between every adjusted Defending
    column (its selected candidate) and every other DNA member (adjusted where selected), before
    and after; "pairs": |ρ| ≥ 0.7 on either side; "largest": each adjusted column's largest
    |ρ adjusted| partner. Ratios use rows at ≥ min_attempts."""
    meas = json.loads(MEASUREMENTS.read_text())
    season = pl.read_parquet(SEASON_PARQUET).filter(
        (pl.col("season_id") == SEASON_ID) & pl.col("in_percentile_pool") & pl.col("is_outfield")
        & (pl.col("minutes_nominal_equiv") >= GATE - TOL))
    sel = {(c["metric"], c["group"]): c for c in crit["cells"]}
    out = []
    for g in GROUPS:
        sub = season.filter(pl.col("position_group") == g).sort(KEY)
        E = (sub[OPP] / sub["minutes_played"] * 90.0).to_numpy()

        def col(m: str, adjusted: bool) -> np.ndarray:
            v = sub[m].to_numpy().astype(float)
            if m in meas["ratios"]:
                rs = meas["ratios"][m]
                ma = rs["groups"][g]["min_attempts"]
                den = sum(sub[c].to_numpy() for c in rs["denominator_plus"]) \
                    - sum((sub[c].to_numpy() for c in rs["denominator_minus"]), np.zeros(len(v)))
                return np.where(den >= ma, v, np.nan)
            c = sel.get((m, g))
            if not adjusted or c is None or c["selected"] in (None, "beta_0"):
                return v
            if c["selected"] == "team_share":
                return (sub[count_of(m)] / sub[team_of(m)]).to_numpy()
            beta = 1.0 if c["selected"] == "beta_1" else c["beta_hat"]
            return v * (c["e_ref"] / E) ** beta

        dna = sorted(reg["groups"][g]["dna"])
        adj = [m for m in DEFENDING[g] if sel[(m, g)]["selected"] not in (None, "beta_0")]
        for a in adj:
            for b in dna:
                if b == a or (b in adj and b < a):
                    continue
                rho = []
                for adjusted in (False, True):
                    x, y = col(a, adjusted), col(b, adjusted)
                    ok = ~(np.isnan(x) | np.isnan(y))
                    rho.append(pl.DataFrame({"x": x[ok], "y": y[ok]})
                               .select(pl.corr("x", "y", method="spearman")).item())
                out.append({"group": g, "adjusted": a, "candidate": sel[(a, g)]["selected"], "other": b,
                            "rho_raw": rho[0], "rho_adjusted": rho[1], "d17_trigger": abs(rho[1]) >= D17_RHO})
    largest: dict = {}
    for r in out:
        k = (r["group"], r["adjusted"])
        if k not in largest or abs(r["rho_adjusted"]) > abs(largest[k]["rho_adjusted"]):
            largest[k] = r
    return {"pairs": [r for r in out if max(abs(r["rho_raw"]), abs(r["rho_adjusted"])) >= D17_REPORT],
            "largest": list(largest.values())}


def crit_reuse(meas: dict) -> dict:
    """β = 0: the M4g3b entry of the committed movers block (not re-run, D3)."""
    e = meas["movers"]["metrics"][MOVER_METRIC]
    return {k: e[k] for k in ("n", "f_hat", "ci_lo", "ci_hi", "r_m", "s2_rms", "s2_rms_a",
                              "power_at_f0", "extra_seeds")}


# ---------------------------------------------------------------- report

def print_selection(crit: dict, g3: dict | None) -> None:
    print(f"\nK = {crit['k']}; G2 minimums: opponent passes {crit['g2_minimums'][OPP]}; "
          f"smallest team count {min(v for k, v in crit['g2_minimums'].items() if k != OPP)}")
    for c in crit["cells"]:
        print(f"\n{c['group']:9s} {c['metric']:22s} E_ref {c['e_ref']:.1f}  β̂ "
              + (f"{c['beta_hat']:+.3f} CI {c['beta_hat_ci']['ci']} LOLO "
                 f"{[round(v, 3) for v in c['beta_hat_lolo'].values()]}" if c["beta_hat"] is not None
                 else f"fit failed (zero teams {c['zero_teams']})")
              + f"  → selected {c['selected']}")
        for cand in CANDIDATES:
            r = c["candidates"][cand]
            if r.get("fit_failed"):
                print(f"   {cand:10s} fit failed")
                continue
            rho = r["c1_rho"]
            print(f"   {cand:10s} (1) {rho['poss_dur_loo']['mean']:+.3f}/{rho['poss_pass_loo']['mean']:+.3f} "
                  f"{'ok' if r['c1_pass'] else '--'}  (2) {r['c2_gap']['poss_dur_loo']['gap']:5.1f}/"
                  f"{r['c2_gap']['poss_pass_loo']['gap']:5.1f} {'ok' if r['c2_pass'] else '--'}  "
                  f"(3) {r['c3_r_full']['r_full']:.3f} {'ok' if r['c3_pass'] else '--'}"
                  f"{'  SIGN FLIP' if r['sign_flip'] else ''}{'  inconsistent' if r['inconsistent_across_leagues'] else ''}"
                  f"{'  PASS' if r['passes'] else ''}")
    if g3:
        print("\nG3 exclusions, ball_recoveries_p90 movers:")
        for cand, v in g3.items():
            print(f"  {cand}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--stage", choices=("criteria", "movers"), required=True)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--k", type=int, default=None, help="splits (default: the committed M4g2 K)")
    args = ap.parse_args()
    reg = load_registry(args.registry)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    crit_path = args.out_dir / "possession.json"
    if args.stage == "criteria":
        d = Data()
        crit = evaluate(d, args.k)
        crit["registry_version"] = reg["registry_version"]
        crit["g3_mover_exclusions"] = g3_exclusions(mv.Ctx(extra_cols=EXTRA_COLS), crit)
        crit_path.write_text(json.dumps(crit, indent=1))
        print_selection(crit, crit["g3_mover_exclusions"])
        print(f"wrote {crit_path}")
    else:
        crit = json.loads(crit_path.read_text())
        res = movers_stage(crit)
        res["registry_version"] = reg["registry_version"]
        res["d17_report"] = d17_report(crit, reg)
        for r in res["d17_report"]["pairs"] + res["d17_report"]["largest"]:
            print(f"D17 {r['group']:9s} {r['adjusted']:22s} ({r['candidate']}) × {r['other']:28s} "
                  f"ρ {r['rho_raw']:+.3f} → {r['rho_adjusted']:+.3f}{'  ≥ 0.85' if r['d17_trigger'] else ''}")
        p = args.out_dir / "movers.json"
        p.write_text(json.dumps(res, indent=1))
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
