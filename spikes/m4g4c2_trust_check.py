"""
M4g4c2 trust check on the 7 adjusted Defending cells (ADR 004 §13.8 "M4g4c2 trust check";
plan 005 M4g4c2). Post-hoc motivated (the M4g4c null calibration), specified before any number.

Reads data/interim/m4g4c/possession.json (the M4g4c selection, criterion (3) pass/fail, the
leave-one-league-out β̂) and the gated rows with their proxies (m4g4_possession.Data). Writes
data/interim/m4g4c2/trust.json. Report only: the registry is untouched (M4g4d applies it).
The independent check is spikes/m4g4c2_trust_reference.py. Never imports eleveniq.

Spec (ADR 004 §13.8, folded stats-reviewer conditions C1–C8; user decisions 2026-10-08):
- Frame: a cell's gated group rows (sorted by the season key) with team key, player key,
  minutes, count, opponent passes, team count, both proxies. The selection on a frame and a
  league set L reruns M4g4c: candidates β = 0 → β̂ → team share → β = 1; β̂ for league l is
  fitted on L − {l} (team table: Σ count, Σ minutes, Σ opponent passes per team key; log means,
  demeaned within league, OLS slope; a team with Σ count = 0 fails the fit); criterion (1)
  mean over L of within-league Spearman ρ < 0.1 on both proxies; criterion (2) mean over L of
  |mean Hazen local pct Q5 − Q1| ≤ 10 on both proxies (local population: D3 dedupe by
  (player key, league), most minutes, then league, team); sign flip vs the frame's own β = 0
  means (dead zone 0.05); criterion (3) held at its M4g4c pass/fail per candidate; tie rule
  (12 significant digits) on β-adjusted values. An undefined quantity (fit failure in a
  candidate other than β̂'s ordinary failure, Spearman undefined) makes the result "undefined".
- (a) B = 1,000 resamples, stream m4g3_power.cell_rng("m4g4c2_boot", metric, group). Per
  resample, per league in POOL order: the league's teams sorted by team id, draws
  rng.integers(0, n, n); draw slot j takes that team's rows with team key = league · 1000 + j
  and player key = (player, slot) (no dedupe across slots). Pass: share selecting the M4g4c
  candidate ≥ 0.80. Also reported: same family ({β̂, β = 1} vs team share), β = 0 or none,
  undefined.
- (b) per dropped league, the selection on the other 3. Pass: ≥ 3 of 4 agree.
- (c) stream cell_rng("m4g4c2_perm", metric, group). Iteration 0 is T_obs (identity
  permutation); iterations 1–1,999 permute. Per iteration, per league in POOL order: (if
  permuting) perm = rng.permutation(teams sorted); then jitter = rng.normal(0, 1e-9, rows);
  each row gets its (permuted) team's mean proxy (per proxy, the mean over the team's rows) +
  the same jitter for both proxies. Values are fixed: β = 0 = raw; the selected candidate with
  the M4g4c leave-one-league-out β̂ (β̂ cells), β = 1, or team share. T_ρ = mean over proxies of
  (|mean ρ β = 0| − |mean ρ selected|); T_gap = mean over proxies of (gap β = 0 − gap selected).
  p = (1 + #{T_null ≥ T_obs}) / 2,000. Pass: p < 0.05 on each criterion β = 0 failed in M4g4c.
  The player-level T (observed proxies) is reported only.
- (d) β̂ cells: the selection with every β̂ fit (all-4 and per held-out league) excluding team
  keys with Σ count < 10 (≥ 20 reported); excluded rows stay adjusted and in the populations.
  Pass: still selects β̂. The number of excluded team keys is reported (0 → the refit is the
  M4g4c fit, not a robustness test).
- (e) report only: raw vs selected local percentiles (β̂ at its all-4 fit): 10 largest signed
  moves, median and P90 |shift|, Spearman.

  .venv/bin/python spikes/m4g4c2_trust_check.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g3_power as pw  # noqa: E402  (cell_rng)
import m4g4_possession as eng  # noqa: E402

CRIT = eng.OUT_DIR / "possession.json"
OUT = eng.PROJECT_ROOT / "data" / "interim" / "m4g4c2" / "trust.json"
B_BOOT, N_PERM, JITTER = 1000, 1999, 1e-9
STAB_MIN, LOLO_MIN, P_MAX = 0.80, 3, 0.05
D_MIN, D_REPORT = 10, 20
FAMILY = {"beta_hat": "e_scaling", "beta_1": "e_scaling", "team_share": "team_share"}
UNDEF = "undefined"


@dataclass(frozen=True)
class Frame:
    comp: np.ndarray
    team: np.ndarray
    pkey: np.ndarray          # player key (int); a bootstrap slot relabels it
    minutes: np.ndarray
    count: np.ndarray
    opp: np.ndarray
    team_count: np.ndarray
    proxy: dict               # name → array

    def take(self, idx: np.ndarray) -> "Frame":
        return Frame(self.comp[idx], self.team[idx], self.pkey[idx], self.minutes[idx], self.count[idx],
                     self.opp[idx], self.team_count[idx], {p: v[idx] for p, v in self.proxy.items()})

    @property
    def p90(self) -> np.ndarray:
        return self.count / self.minutes * 90.0

    @property
    def E(self) -> np.ndarray:
        return self.opp / self.minutes * 90.0


def frame_of(c: eng.Cell) -> Frame:
    return Frame(c.comp.copy(), c.team.copy(), c.player.astype(np.int64), c.minutes, c.count, c.opp,
                 c.team_count, {p: c.proxy[p] for p in eng.PROXIES})


# ── the selection on a frame ──────────────────────────────────────────────────

def fit(f: Frame, leagues, min_count: float = 0.0) -> float | None:
    """β̂ on the team table of `leagues`, teams with Σ count < min_count left out; None if a kept
    team's Σ count is 0 or Σx² = 0."""
    sxy = sxx = 0.0
    for lg in leagues:
        s = f.comp == lg
        teams = np.unique(f.team[s])
        xs, ys = [], []
        for t in teams.tolist():
            r = s & (f.team == t)
            m, n, o = f.minutes[r].sum(), f.count[r].sum(), f.opp[r].sum()
            if n < min_count:
                continue
            if n <= 0:
                return None
            xs.append(math.log(o * 90.0 / m))
            ys.append(math.log(n * 90.0 / m))
        if not xs:
            continue
        x, y = np.array(xs) - np.mean(xs), np.array(ys) - np.mean(ys)
        sxy += float((x * y).sum())
        sxx += float((x * x).sum())
    return sxy / sxx if sxx > 0 else None


def e_ref_of(f: Frame) -> float:
    return float(np.median(f.E))


def values(f: Frame, cand: str, betas: dict | None = None) -> np.ndarray:
    """A candidate's column; betas: league → β for beta_hat."""
    if cand == "beta_0":
        return f.p90
    if cand == "team_share":
        return f.count / f.team_count
    er = e_ref_of(f)
    if cand == "beta_1":
        return eng.canon(f.p90 * er / f.E)
    b = np.array([betas[int(c)] for c in f.comp])
    return eng.canon(f.p90 * (er / f.E) ** b)


def local_keep(f: Frame) -> np.ndarray:
    order = sorted(range(len(f.comp)), key=lambda i: (-f.minutes[i], f.comp[i], f.team[i]))
    seen, keep = set(), np.zeros(len(f.comp), dtype=bool)
    for i in order:
        k = (int(f.pkey[i]), int(f.comp[i]))
        if k not in seen:
            seen.add(k)
            keep[i] = True
    return keep


def local_pct(f: Frame, v: np.ndarray, keep: np.ndarray, leagues) -> np.ndarray:
    out = np.full(len(v), np.nan)
    for lg in leagues:
        s = f.comp == lg
        out[s] = eng.hazen(np.sort(v[s & keep]), v[s])
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    x = pl.DataFrame({"a": a, "b": b}).select(pl.corr("a", "b", method="spearman")).item()
    return math.nan if x is None else float(x)


def gap_of(pct: np.ndarray, prox: np.ndarray) -> float:
    rk = pl.Series(prox).rank("average").to_numpy()
    q = np.ceil(5 * rk / len(prox)).astype(int)
    return abs(float(pct[q == 5].mean()) - float(pct[q == 1].mean()))


def criteria(f: Frame, v: np.ndarray, leagues, keep: np.ndarray, proxy: dict | None = None) -> dict:
    """Mean ρ and mean gap per proxy over `leagues` (proxy: override the frame's proxies)."""
    proxy = f.proxy if proxy is None else proxy
    pct = local_pct(f, v, keep, leagues)
    out = {}
    for p in eng.PROXIES:
        rho, gap = [], []
        for lg in leagues:
            s = f.comp == lg
            rho.append(spearman(proxy[p][s], v[s]))
            gap.append(gap_of(pct[s], proxy[p][s]))
        out[p] = (float(np.mean(rho)), float(np.mean(gap)))
    return out


def select(f: Frame, leagues, c3: dict, min_count: float = 0.0) -> str | None:
    """The M4g4c selection on a frame and league set; UNDEF if a quantity is undefined."""
    leagues = list(leagues)
    keep = local_keep(f)
    ref = None
    for cand in eng.CANDIDATES:
        if cand == "beta_hat":
            betas = {lg: fit(f, [x for x in leagues if x != lg], min_count) for lg in leagues}
            if any(b is None for b in betas.values()):
                return UNDEF
            v = values(f, cand, betas)
        else:
            v = values(f, cand)
        cr = criteria(f, v, leagues, keep)
        if any(math.isnan(cr[p][0]) for p in eng.PROXIES):
            return UNDEF
        means = {p: cr[p][0] for p in eng.PROXIES}
        if cand == "beta_0":
            ref = means
        c1 = all(abs(means[p]) < eng.RHO_MAX for p in eng.PROXIES)
        c2 = all(cr[p][1] <= eng.GAP_MAX for p in eng.PROXIES)
        flip = any(abs(ref[p]) >= eng.SIGN_DEAD and abs(means[p]) >= eng.SIGN_DEAD
                   and math.copysign(1, means[p]) != math.copysign(1, ref[p]) for p in eng.PROXIES)
        if c1 and c2 and c3[cand] and not flip:
            return cand
    return None


# ── (a) bootstrap ─────────────────────────────────────────────────────────────

def resample(f: Frame, rng: np.random.Generator) -> Frame:
    parts, teams_new, pkeys = [], [], []
    for lg in eng.POOL:
        s = f.comp == lg
        teams = np.unique(f.team[s])
        draw = rng.integers(0, len(teams), len(teams))
        for j, ti in enumerate(draw.tolist()):
            rows = np.flatnonzero(s & (f.team == teams[ti]))
            parts.append(rows)
            teams_new.append(np.full(len(rows), lg * 1000 + j))
            pkeys.append(f.pkey[rows] * 100000 + lg * 1000 + j)
    idx = np.concatenate(parts)
    g = f.take(idx)
    return replace(g, team=np.concatenate(teams_new), pkey=np.concatenate(pkeys))


def stability(f: Frame, c3: dict, metric: str, grp: str, target: str) -> dict:
    rng = pw.cell_rng("m4g4c2_boot", metric, grp)
    tally: dict[str, int] = {}
    for _ in range(B_BOOT):
        s = select(resample(f, rng), eng.POOL, c3)
        key = "none" if s is None else s
        tally[key] = tally.get(key, 0) + 1
    same = tally.get(target, 0) / B_BOOT
    fam = sum(n for k, n in tally.items() if FAMILY.get(k) == FAMILY[target]) / B_BOOT
    return {"tally": dict(sorted(tally.items())), "p_same": same, "p_same_family": fam,
            "p_raw_or_none": (tally.get("beta_0", 0) + tally.get("none", 0)) / B_BOOT,
            "undefined": tally.get(UNDEF, 0), "pass": same >= STAB_MIN}


# ── (b) leave one league out ──────────────────────────────────────────────────

def lolo(f: Frame, c3: dict, target: str) -> dict:
    sel = {}
    for drop in eng.POOL:
        kept = [lg for lg in eng.POOL if lg != drop]
        sel[str(drop)] = select(f.take(np.flatnonzero(f.comp != drop)), kept, c3)
    agree = sum(v == target for v in sel.values())
    return {"selected_without": sel, "agree": agree, "pass": agree >= LOLO_MIN}


# ── (c) permutation ───────────────────────────────────────────────────────────

def team_mean_proxy(f: Frame) -> dict:
    """league → (teams sorted, {proxy: team → mean proxy})."""
    out = {}
    for lg in eng.POOL:
        s = f.comp == lg
        teams = np.unique(f.team[s])
        out[lg] = (teams, {p: {int(t): float(f.proxy[p][s & (f.team == t)].mean()) for t in teams.tolist()}
                           for p in eng.PROXIES})
    return out


def permutation(f: Frame, c3_failed: dict, metric: str, grp: str, target: str, lolo_betas: dict) -> dict:
    rng = pw.cell_rng("m4g4c2_perm", metric, grp)
    keep = local_keep(f)
    v0 = values(f, "beta_0")
    vs = values(f, target, lolo_betas) if target == "beta_hat" else values(f, target)
    tm = team_mean_proxy(f)

    def stats(proxy: dict) -> tuple[float, float]:
        a, b = criteria(f, v0, eng.POOL, keep, proxy), criteria(f, vs, eng.POOL, keep, proxy)
        t_rho = float(np.mean([abs(a[p][0]) - abs(b[p][0]) for p in eng.PROXIES]))
        t_gap = float(np.mean([a[p][1] - b[p][1] for p in eng.PROXIES]))
        return t_rho, t_gap

    def draw(permute: bool) -> dict:
        proxy = {p: np.empty(len(f.comp)) for p in eng.PROXIES}
        for lg in eng.POOL:
            s = np.flatnonzero(f.comp == lg)
            teams, means = tm[lg]
            mapping = dict(zip(teams.tolist(), (rng.permutation(teams) if permute else teams).tolist()))
            jit = rng.normal(0.0, JITTER, len(s))
            for p in eng.PROXIES:
                proxy[p][s] = np.array([means[p][mapping[int(t)]] for t in f.team[s].tolist()]) + jit
        return proxy

    t_obs = stats(draw(False))
    null = np.array([stats(draw(True)) for _ in range(N_PERM)])
    p_rho = (1 + int((null[:, 0] >= t_obs[0]).sum())) / (N_PERM + 1)
    p_gap = (1 + int((null[:, 1] >= t_obs[1]).sum())) / (N_PERM + 1)
    judged = [k for k, failed in c3_failed.items() if failed]
    ok = all({"rho": p_rho, "gap": p_gap}[k] < P_MAX for k in judged)
    return {"t_rho_obs": t_obs[0], "t_gap_obs": t_obs[1], "p_rho": p_rho, "p_gap": p_gap,
            "judged": judged, "t_player_level": stats(f.proxy), "pass": ok}


# ── (d), (e) ──────────────────────────────────────────────────────────────────

def teams_below(f: Frame, min_count: float) -> int:
    """Team keys (all leagues) whose Σ count over the frame is below min_count."""
    return sum(1 for lg in eng.POOL for t in np.unique(f.team[f.comp == lg]).tolist()
               if f.count[(f.comp == lg) & (f.team == t)].sum() < min_count)


def low_count(f: Frame, c3: dict) -> dict:
    out = {}
    for mc in (D_MIN, D_REPORT):
        out[str(mc)] = {"beta_hat_all4": fit(f, eng.POOL, mc), "selected": select(f, eng.POOL, c3, mc),
                        "teams_excluded": teams_below(f, mc)}
    out["pass"] = out[str(D_MIN)]["selected"] == "beta_hat"
    return out


def moves(f: Frame, sub: pl.DataFrame, target: str, beta_all: float | None) -> dict:
    keep = local_keep(f)
    raw = local_pct(f, values(f, "beta_0"), keep, eng.POOL)
    adj = local_pct(f, values(f, target, {lg: beta_all for lg in eng.POOL}) if target == "beta_hat"
                    else values(f, target), keep, eng.POOL)
    d = adj - raw
    top = np.argsort(-np.abs(d), kind="mergesort")[:10]
    return {"median_abs": float(np.median(np.abs(d))), "p90_abs": float(np.quantile(np.abs(d), 0.9)),
            "spearman": spearman(raw, adj),
            "top10": [{"player": sub["player_name"][int(i)], "team": sub["team_name"][int(i)],
                       "raw": float(raw[i]), "adjusted": float(adj[i]), "shift": float(d[i])} for i in top]}


# ── main ──────────────────────────────────────────────────────────────────────

def run(d: eng.Data, crit: dict) -> dict:
    cells = []
    for c in crit["cells"]:
        target = c["selected"]
        if target in (None, "beta_0"):
            continue
        m, g = c["metric"], c["group"]
        cell = eng.Cell(d, m, g)
        f = frame_of(cell)
        c3 = {k: bool(c["candidates"][k]["c3_pass"]) for k in eng.CANDIDATES}
        full = select(f, eng.POOL, c3)
        if full != target:
            fail(f"{g} {m}: select() on the full frame gives {full}, M4g4c selected {target}")
        b0 = c["candidates"]["beta_0"]
        failed = {"rho": not b0["c1_pass"], "gap": not b0["c2_pass"]}
        lolo_b = {int(k): v for k, v in c["beta_hat_lolo"].items()}
        rec = {"metric": m, "group": g, "selected": target, "beta0_failed": failed,
               "a": stability(f, c3, m, g, target), "b": lolo(f, c3, target),
               "c": permutation(f, failed, m, g, target, lolo_b),
               "d": low_count(f, c3) if target == "beta_hat" else None,
               "e": moves(f, cell.sub, target, c["beta_hat"])}
        rec["pass"] = rec["a"]["pass"] and rec["b"]["pass"] and rec["c"]["pass"] and \
            (rec["d"] is None or rec["d"]["pass"])
        cells.append(rec)
        print_cell(rec)
    return {"generated_by": "spikes/m4g4c2_trust_check.py", "spec": "ADR 004 §13.8 M4g4c2",
            "rules": {"b_boot": B_BOOT, "n_perm": N_PERM, "stab_min": STAB_MIN, "lolo_min": LOLO_MIN,
                      "p_max": P_MAX, "d_min": D_MIN, "d_report": D_REPORT, "jitter": JITTER},
            "cells": cells}


def print_cell(r: dict) -> None:
    a, b, c, dd = r["a"], r["b"], r["c"], r["d"]
    print(f"\n{r['group']:9s} {r['metric']:22s} ({r['selected']}) → {'KEEP' if r['pass'] else 'RAW'}")
    print(f"  (a) same {a['p_same']:.3f} family {a['p_same_family']:.3f} raw/none {a['p_raw_or_none']:.3f} "
          f"undefined {a['undefined']} {a['tally']} {'ok' if a['pass'] else 'FAIL'}")
    print(f"  (b) {b['selected_without']} agree {b['agree']}/4 {'ok' if b['pass'] else 'FAIL'}")
    print(f"  (c) T_ρ {c['t_rho_obs']:+.3f} p {c['p_rho']:.4f}; T_gap {c['t_gap_obs']:+.2f} p {c['p_gap']:.4f}; "
          f"judged {c['judged']} {'ok' if c['pass'] else 'FAIL'}")
    if dd:
        print(f"  (d) ≥10 ({dd['10']['teams_excluded']} teams out): β̂ {dd['10']['beta_hat_all4']} → "
              f"{dd['10']['selected']}; ≥20 ({dd['20']['teams_excluded']} out): β̂ {dd['20']['beta_hat_all4']} → "
              f"{dd['20']['selected']} {'ok' if dd['pass'] else 'FAIL'}")
    e = r["e"]
    print(f"  (e) median |shift| {e['median_abs']:.1f}, P90 {e['p90_abs']:.1f}, ρ {e['spearman']:.3f}; top: "
          + ", ".join(f"{x['player']} {x['raw']:.0f}→{x['adjusted']:.0f}" for x in e["top10"][:4]))


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    crit = json.loads(CRIT.read_text())
    res = run(eng.Data(), crit)
    res["possession_json_registry_version"] = crit.get("registry_version")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
