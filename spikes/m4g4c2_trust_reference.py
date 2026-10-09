"""
Independent reference for the M4g4c2 trust check (ADR 004 §13.8 "M4g4c2 trust check").

Shares the written spec (the ADR text and the docstring of spikes/m4g4c2_trust_check.py), not
code, with spikes/m4g4c2_trust_check.py and spikes/m4g4_possession.py. From
m4g4_possession_reference (itself independent of the engines) it reuses the proxy loader (Ref),
the tie rule (canon) and the RNG stream helper; everything else is its own:
- rows as Python dicts, frames as dicts of arrays rebuilt per subset;
- β̂ by least squares on stacked team logs (Python sums per team);
- Hazen by broadcast counting, average ranks by m4g4_possession_reference.avg_rank, Spearman as the Pearson
  correlation of average ranks;
- the bootstrap, permutation and leave-one-league-out loops written out from the spec, on the
  same streams (cell_rng key "|".join(repr(...)) seeded with 20261008).

  .venv/bin/python spikes/m4g4c2_trust_reference.py --registry data/interim/m4g4c_snapshot/registry.json \
      --check-output data/interim/m4g4c2/trust.json [--write-expected]

--write-expected rewrites spikes/expected_m4g4_flips.json: vector_adjusted_columns keeps only the
cells this reference finds passing, and a trust_check block records each cell's results. The
Defending statuses do not depend on the check (ball_recoveries_p90 is in none of the 7 cells).
No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g4_possession_reference as r4  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CRIT = ROOT / "data" / "interim" / "m4g4c" / "possession.json"
EXPECTED = Path(__file__).resolve().parent / "expected_m4g4_flips.json"
POOL, PROXIES = (2, 7, 11, 12), ("poss_dur_loo", "poss_pass_loo")
CANDS = ("beta_0", "beta_hat", "team_share", "beta_1")
FAMILY = {"beta_hat": "e", "beta_1": "e", "team_share": "s"}
B_BOOT, N_PERM, JITTER = 1000, 1999, 1e-9
STAB_MIN, LOLO_MIN, P_MAX, D_MIN, D_REPORT = 0.80, 3, 0.05, 10, 20
RHO_MAX, DEAD, GAP_MAX = 0.1, 0.05, 10.0
KEY = ("player_id", "team_id", "competition_id", "season_id", "source")
OPP = "opponent_open_play_passes_while_on"
UNDEF = "undefined"
TOL = 1e-9


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)):
        return a == b
    return abs(float(a) - float(b)) <= TOL * max(1.0, abs(float(a)), abs(float(b)))


# ── frames ────────────────────────────────────────────────────────────────────

def frame(rows: list[dict], metric: str) -> dict:
    a = r4.actn(metric)
    return {"comp": np.array([r["competition_id"] for r in rows]),
            "team": np.array([r["team_id"] for r in rows], dtype=np.int64),
            "pkey": np.array([r["player_id"] for r in rows], dtype=np.int64),
            "min": np.array([float(r["minutes_played"]) for r in rows]),
            "n": np.array([float(r[a]) for r in rows]),
            "opp": np.array([float(r[OPP]) for r in rows]),
            "tm": np.array([float(r[f"team_{a}_while_on"]) for r in rows]),
            **{p: np.array([r[p] for r in rows]) for p in PROXIES}}


def sub(f: dict, idx: np.ndarray) -> dict:
    return {k: v[idx] for k, v in f.items()}


def slope(f: dict, leagues, min_count: float = 0.0) -> float | None:
    X, Y = [], []
    for lg in leagues:
        tab: dict = {}
        for c, t, m, n, o in zip(f["comp"], f["team"], f["min"], f["n"], f["opp"]):
            if c == lg:
                acc = tab.setdefault(int(t), [0.0, 0.0, 0.0])
                acc[0] += m
                acc[1] += n
                acc[2] += o
        pts = []
        for m, n, o in tab.values():
            if n < min_count:
                continue
            if n <= 0:
                return None
            pts.append((math.log(o * 90 / m), math.log(n * 90 / m)))
        if pts:
            x = np.array([p[0] for p in pts])
            y = np.array([p[1] for p in pts])
            X.append(x - x.mean())
            Y.append(y - y.mean())
    if not X:
        return None
    X, Y = np.concatenate(X), np.concatenate(Y)
    if float(X @ X) <= 0:
        return None
    return float(np.linalg.lstsq(X[:, None], Y, rcond=None)[0][0])


def col(f: dict, cand: str, betas: dict | None = None) -> np.ndarray:
    p90 = f["n"] * 90.0 / f["min"]
    if cand == "beta_0":
        return p90
    if cand == "team_share":
        return f["n"] / f["tm"]
    E = f["opp"] * 90.0 / f["min"]
    er = float(np.median(E))
    if cand == "beta_1":
        return r4.canon(p90 * er / E)
    return r4.canon(p90 * (er / E) ** np.array([betas[int(c)] for c in f["comp"]]))


def keep_mask(f: dict) -> np.ndarray:
    best: dict = {}
    for i in range(len(f["comp"])):
        k = (int(f["pkey"][i]), int(f["comp"][i]))
        cand = (-f["min"][i], f["comp"][i], f["team"][i], i)
        if k not in best or cand < best[k][0]:
            best[k] = (cand, i)
    m = np.zeros(len(f["comp"]), dtype=bool)
    m[[v[1] for v in best.values()]] = True
    return m


def hazen(pop: np.ndarray, x: np.ndarray) -> np.ndarray:
    less = (pop[None, :] < x[:, None]).sum(1)
    eq = (pop[None, :] == x[:, None]).sum(1)
    return (less + 0.5 * eq) / len(pop) * 100.0


def rho(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = r4.avg_rank(a), r4.avg_rank(b)
    if ra.std() == 0 or rb.std() == 0:
        return math.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def crit(f: dict, v: np.ndarray, leagues, keep: np.ndarray, prox: dict | None = None) -> dict:
    prox = {p: f[p] for p in PROXIES} if prox is None else prox
    out = {p: ([], []) for p in PROXIES}
    for lg in leagues:
        s = f["comp"] == lg
        pct = hazen(v[s & keep], v[s])
        for p in PROXIES:
            x = prox[p][s]
            q = np.ceil(5 * r4.avg_rank(x) / len(x))
            out[p][0].append(rho(x, v[s]))
            out[p][1].append(abs(pct[q == 5].mean() - pct[q == 1].mean()))
    return {p: (sum(r) / len(r), sum(g) / len(g)) for p, (r, g) in out.items()}


def select(f: dict, leagues, c3: dict, min_count: float = 0.0) -> str | None:
    leagues = list(leagues)
    keep = keep_mask(f)
    ref = None
    for cand in CANDS:
        if cand == "beta_hat":
            betas = {lg: slope(f, [x for x in leagues if x != lg], min_count) for lg in leagues}
            if None in betas.values():
                return UNDEF
            v = col(f, cand, betas)
        else:
            v = col(f, cand)
        c = crit(f, v, leagues, keep)
        if any(math.isnan(c[p][0]) for p in PROXIES):
            return UNDEF
        if cand == "beta_0":
            ref = {p: c[p][0] for p in PROXIES}
        ok1 = all(abs(c[p][0]) < RHO_MAX for p in PROXIES)
        ok2 = all(c[p][1] <= GAP_MAX for p in PROXIES)
        flip = any(abs(ref[p]) >= DEAD and abs(c[p][0]) >= DEAD and (c[p][0] > 0) != (ref[p] > 0)
                   for p in PROXIES)
        if ok1 and ok2 and c3[cand] and not flip:
            return cand
    return None


def resample(f: dict, rng) -> dict:
    idx, team, pkey = [], [], []
    for lg in POOL:
        teams = sorted(set(f["team"][f["comp"] == lg].tolist()))
        for j, ti in enumerate(rng.integers(0, len(teams), len(teams)).tolist()):
            rows = [i for i in range(len(f["comp"])) if f["comp"][i] == lg and f["team"][i] == teams[ti]]
            idx += rows
            team += [lg * 1000 + j] * len(rows)
            pkey += [int(f["pkey"][i]) * 100000 + lg * 1000 + j for i in rows]
    g = sub(f, np.array(idx))
    g["team"], g["pkey"] = np.array(team, dtype=np.int64), np.array(pkey, dtype=np.int64)
    return g


# ── checks ────────────────────────────────────────────────────────────────────

def check_cell(f: dict, c: dict) -> dict:
    m, g, target = c["metric"], c["group"], c["selected"]
    c3 = {k: bool(c["candidates"][k]["c3_pass"]) for k in CANDS}
    if select(f, POOL, c3) != target:
        fail(f"{g} {m}: reference selection on the full frame differs from M4g4c")
    rng = r4.stream("m4g4c2_boot", m, g)
    tally: dict = {}
    for _ in range(B_BOOT):
        s = select(resample(f, rng), POOL, c3)
        tally["none" if s is None else s] = tally.get("none" if s is None else s, 0) + 1
    same = tally.get(target, 0) / B_BOOT
    a = {"tally": dict(sorted(tally.items())), "p_same": same,
         "p_same_family": sum(n for k, n in tally.items() if FAMILY.get(k) == FAMILY[target]) / B_BOOT,
         "p_raw_or_none": (tally.get("beta_0", 0) + tally.get("none", 0)) / B_BOOT,
         "undefined": tally.get(UNDEF, 0), "pass": same >= STAB_MIN}
    sel = {str(d): select(sub(f, np.flatnonzero(f["comp"] != d)), [x for x in POOL if x != d], c3) for d in POOL}
    agree = sum(v == target for v in sel.values())
    b = {"selected_without": sel, "agree": agree, "pass": agree >= LOLO_MIN}
    # (c)
    b0 = c["candidates"]["beta_0"]
    failed = {"rho": not b0["c1_pass"], "gap": not b0["c2_pass"]}
    lolo_b = {int(k): v for k, v in c["beta_hat_lolo"].items()}
    keep = keep_mask(f)
    v0, vs = col(f, "beta_0"), (col(f, target, lolo_b) if target == "beta_hat" else col(f, target))
    tmeans = {}
    for lg in POOL:
        teams = sorted(set(f["team"][f["comp"] == lg].tolist()))
        tmeans[lg] = (teams, {p: {t: float(f[p][(f["comp"] == lg) & (f["team"] == t)].mean()) for t in teams}
                              for p in PROXIES})
    prng = r4.stream("m4g4c2_perm", m, g)

    def stat(prox):
        x, y = crit(f, v0, POOL, keep, prox), crit(f, vs, POOL, keep, prox)
        return (sum(abs(x[p][0]) - abs(y[p][0]) for p in PROXIES) / 2,
                sum(x[p][1] - y[p][1] for p in PROXIES) / 2)

    def proxies(permute: bool) -> dict:
        prox = {p: np.zeros(len(f["comp"])) for p in PROXIES}
        for lg in POOL:
            rows = np.flatnonzero(f["comp"] == lg)
            teams, means = tmeans[lg]
            to = dict(zip(teams, prng.permutation(np.array(teams)).tolist() if permute else teams))
            jit = prng.normal(0.0, JITTER, len(rows))
            for p in PROXIES:
                for k, i in enumerate(rows.tolist()):
                    prox[p][i] = means[p][to[int(f["team"][i])]] + jit[k]
        return prox

    t_obs = stat(proxies(False))
    nulls = [stat(proxies(True)) for _ in range(N_PERM)]
    p_rho = (1 + sum(t[0] >= t_obs[0] for t in nulls)) / (N_PERM + 1)
    p_gap = (1 + sum(t[1] >= t_obs[1] for t in nulls)) / (N_PERM + 1)
    judged = [k for k, v in failed.items() if v]
    cc = {"t_rho_obs": t_obs[0], "t_gap_obs": t_obs[1], "p_rho": p_rho, "p_gap": p_gap, "judged": judged,
          "pass": all({"rho": p_rho, "gap": p_gap}[k] < P_MAX for k in judged)}
    d = None
    if target == "beta_hat":
        tot: dict = {}
        for cmp_, t, n in zip(f["comp"], f["team"], f["n"]):
            tot[(int(cmp_), int(t))] = tot.get((int(cmp_), int(t)), 0.0) + n
        d = {str(mc): {"beta_hat_all4": slope(f, POOL, mc), "selected": select(f, POOL, c3, mc),
                       "teams_excluded": int(sum(v < mc for v in tot.values()))}
             for mc in (D_MIN, D_REPORT)}
        d["pass"] = d[str(D_MIN)]["selected"] == "beta_hat"
    ok = a["pass"] and b["pass"] and cc["pass"] and (d is None or d["pass"])
    return {"a": a, "b": b, "c": cc, "d": d, "pass": ok}


def errors_of(o: dict, r: dict, w: str) -> list[str]:
    """One output cell (trust.json) against the reference result."""
    errs: list[str] = []

    def chk(where, x, y):
        if not close(x, y):
            errs.append(f"{w} {where}: output {x!r} != reference {y!r}")

    for k in ("p_same", "p_same_family", "p_raw_or_none", "undefined", "pass"):
        chk(f"a {k}", o["a"][k], r["a"][k])
    if o["a"]["tally"] != r["a"]["tally"]:
        errs.append(f"{w} a tally: output {o['a']['tally']} != reference {r['a']['tally']}")
    if o["b"]["selected_without"] != r["b"]["selected_without"]:
        errs.append(f"{w} b: output {o['b']['selected_without']} != reference {r['b']['selected_without']}")
    chk("b agree", o["b"]["agree"], r["b"]["agree"])
    chk("b pass", o["b"]["pass"], r["b"]["pass"])
    for k in ("t_rho_obs", "t_gap_obs", "p_rho", "p_gap", "pass"):
        chk(f"c {k}", o["c"][k], r["c"][k])
    if o["c"]["judged"] != r["c"]["judged"]:
        errs.append(f"{w} c judged differs")
    if (o["d"] is None) != (r["d"] is None):
        errs.append(f"{w} d presence differs")
    elif r["d"] is not None:
        for mc in (str(D_MIN), str(D_REPORT)):
            chk(f"d {mc} beta", o["d"][mc]["beta_hat_all4"], r["d"][mc]["beta_hat_all4"])
            chk(f"d {mc} selected", o["d"][mc]["selected"], r["d"][mc]["selected"])
            chk(f"d {mc} teams_excluded", o["d"][mc]["teams_excluded"], r["d"][mc]["teams_excluded"])
        chk("d pass", o["d"]["pass"], r["d"]["pass"])
    chk("pass", o["pass"], r["pass"])
    return errs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--check-output", type=Path, required=True)
    ap.add_argument("--write-expected", action="store_true")
    args = ap.parse_args()
    got = json.loads(args.check_output.read_text())
    rules = {"b_boot": B_BOOT, "n_perm": N_PERM, "stab_min": STAB_MIN, "lolo_min": LOLO_MIN,
             "p_max": P_MAX, "d_min": D_MIN, "d_report": D_REPORT, "jitter": JITTER}
    if got["rules"] != rules:
        fail(f"rules {got['rules']} != the written spec {rules}")
    crit_doc = json.loads(CRIT.read_text())
    adjusted = [c for c in crit_doc["cells"] if c["selected"] not in (None, "beta_0")]
    if sorted((c["group"], c["metric"]) for c in got["cells"]) != sorted((c["group"], c["metric"]) for c in adjusted):
        fail("cell set differs from the M4g4c adjusted cells")
    ref = r4.Ref(json.loads(args.registry.read_text()))
    by = {(c["group"], c["metric"]): c for c in got["cells"]}
    errs, results = [], {}
    for c in adjusted:
        rows = sorted((r for r in ref.gated if r["position_group"] == c["group"]), key=lambda r: tuple(r[k] for k in KEY))
        r = check_cell(frame(rows, c["metric"]), c)
        results[(c["group"], c["metric"])] = (c, r)
        errs += errors_of(by[(c["group"], c["metric"])], r, f"{c['group']} {c['metric']}")
        print(f"{c['group']:9s} {c['metric']:22s} {'KEEP' if r['pass'] else 'RAW'}", flush=True)
    if errs:
        print("\n".join(errs[:60]), file=sys.stderr)
        fail(f"{len(errs)} mismatches")
    print(f"OK: {len(adjusted)} cells, every field within {TOL:g}")
    if not args.write_expected:
        return
    exp = json.loads(EXPECTED.read_text())
    keep_cells = {k for k, (_, r) in results.items() if r["pass"]}
    for g, cols in exp["vector_adjusted_columns"].items():
        exp["vector_adjusted_columns"][g] = [x for x in cols if (g, x["metric"]) in keep_cells]
    dm = [x["metric"] for x in exp["vector_adjusted_columns"]["DM-CM"]]
    exp["compare_pairs"] = {"dm_cm_adjusted": dm, "tested": any(m in dm for m in ("interceptions_p90", "ball_recoveries_p90"))}
    exp["trust_check"] = {
        "spec": "ADR 004 §13.8 M4g4c2 (post-hoc motivated; a failing cell stays raw)", "rules": rules,
        "cells": {f"{g} {m}": {"candidate": c["selected"], "keep_adjusted": r["pass"],
                               "a_p_same": r["a"]["p_same"], "b_agree": r["b"]["agree"],
                               "c_p_rho": r["c"]["p_rho"], "c_p_gap": r["c"]["p_gap"], "c_judged": r["c"]["judged"],
                               "d_selected_ge10": "n/a" if r["d"] is None else r["d"][str(D_MIN)]["selected"],
                               "d_teams_excluded_ge10": "n/a" if r["d"] is None else r["d"][str(D_MIN)]["teams_excluded"],
                               "d_teams_excluded_ge20": "n/a" if r["d"] is None else r["d"][str(D_REPORT)]["teams_excluded"]}
                  for (g, m), (c, r) in sorted(results.items())}}
    note = " Updated by spikes/m4g4c2_trust_reference.py --write-expected (M4g4c2 trust check)."
    if note not in exp["description"]:
        exp["description"] += note
    EXPECTED.write_text(json.dumps(exp, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {EXPECTED}")


if __name__ == "__main__":
    main()
