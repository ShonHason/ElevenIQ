"""
M4g4a power at the adjusted column's expected R_m (plan 005 M4g4 plan, ADR 004 §13.8 "Power").

The M4g3 homogeneous model and rule (spikes/m4g3_power.py: sim parameters, mover_rule), with the
D6 restore requirement added: a dataset is simulated once, then the matching draws and the
bootstrap are drawn again for the primary stream and 5 extra streams (as the real extra seeds
re-draw the matched baselines and the bootstrap on the same data). Reported per (R_m, f):
P(PASS) on the primary stream, on all six streams (D6: PASS ∧ ¬borderline_seed) and on ≥ 4 of 6
(considered, not adopted).

Grid: n = 24; R_m 0.40, 0.45, 0.50, 0.55, 0.59, 0.65; f 0, 0.25, 0.5; 1,000 datasets per cell
(MC SE ≤ 0.016); B = 2,000. Each cell has its own stream, default_rng([20261009, n, 1000·R_m,
100·f]), so the figures recorded in ADR 004 §13.8 (computed before M4g4 with this code in the
scratchpad) reproduce exactly. Writes data/interim/m4g4_power.json. Report only.

  .venv/bin/python spikes/m4g4_power.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g3_power as P  # noqa: E402  (the M4g3 rule and model constants)

SEED = 20261009
N = 24
R_GRID = (0.40, 0.45, 0.50, 0.55, 0.59, 0.65)
F_GRID = (0.0, 0.25, 0.5)
DATASETS = 1000
STREAMS = 6                      # primary + 5 extra (ADR 004 §13.7 / M4g3b)
OUT = P.PROJECT_ROOT / "data" / "interim" / "m4g4_power.json"


def one(rng: np.random.Generator, n: int, rel: float, f: float, streams: int = STREAMS) -> list[str]:
    """One simulated dataset (value = player + team + noise in SD units; pool of N_POOL
    non-movers), judged on `streams` streams: each re-draws the matching and the bootstrap."""
    e_sd = math.sqrt((1 - rel) / rel)
    s = rng.normal(0, 1, P.N_POOL)
    h1p = s + rng.normal(0, e_sd, P.N_POOL)
    h2p = s + rng.normal(0, e_sd, P.N_POOL)
    t = rng.normal(0, math.sqrt(f), (2, n))
    d = t[0] - t[1] + rng.normal(0, e_sd, n) - rng.normal(0, e_sd, n)
    out = []
    for _ in range(streams):
        j = rng.integers(0, P.N_POOL, (n, P.DRAWS))
        out.append(P.mover_rule(d * d, h1p[j], h2p[j], rng).verdict)
    return out


def cell(n: int, rel: float, f: float, datasets: int = DATASETS) -> dict:
    rng = np.random.default_rng([SEED, n, int(rel * 1000), int(f * 100)])
    prim = all6 = maj = 0
    for _ in range(datasets):
        v = one(rng, n, rel, f)
        prim += v[0] == "PASS"
        all6 += all(x == "PASS" for x in v)
        maj += sum(x == "PASS" for x in v) >= 4
    return {"n": n, "r_m": rel, "f": f, "datasets": datasets,
            "p_pass_primary": prim / datasets, "p_pass_all6": all6 / datasets,
            "p_pass_4of6": maj / datasets, "mc_se_max": math.sqrt(0.25 / datasets)}


def main() -> None:
    cells = []
    for rel in R_GRID:
        for f in F_GRID:
            c = cell(N, rel, f)
            cells.append(c)
            print(f"n={N} R_m={rel:.2f} f={f:.2f}  primary {c['p_pass_primary']:.3f}  "
                  f"all 6 {c['p_pass_all6']:.3f}  >=4 of 6 {c['p_pass_4of6']:.3f}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_by": "spikes/m4g4_power.py", "seed": SEED,
                               "b": P.B, "draws": P.DRAWS, "cells": cells}, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
