"""
M4g4d: the measurements' `possession` block (schema_version 3; plan 005 M4g4d, ADR 004 §13.8).

spikes/m4_decision_measurements.py --write-json stays the single writer (user, M4g4d plan): it
embeds a projection of the M4g4c / M4g4c2 engine outputs instead of recomputing them.
- data/interim/m4g4c/possession.json (spikes/m4g4_possession.py --stage criteria),
- data/interim/m4g4c/movers.json (--stage movers),
- data/interim/m4g4c2/trust.json (spikes/m4g4c2_trust_check.py).
Their independent references (m4g4_possession_reference.py, m4g4c2_trust_reference.py) check
those files. The block holds numbers and the engines' own rule copies only: every threshold is
applied in eleveniq (analytics/registry.py), which also re-derives the selection and checks it
equals `spike_selected`. The engines' selections under resampling ((a), (b), (d)) cannot be
re-derived from stored numbers, so the registry requires the engines' rule copies to equal its
Rules (the M4g4 STOP rule item).

Pins (fail, never skip): the three files describe one run (same registry_version); their β = 0
values equal the values this writer run stores for the same cells (within-league ρ and its mean,
r_full, the D9 league means; |Δ| ≤ PIN_TOL, the stored rounding); the β = 0 mover entry that
movers.json reused equals this run's movers block entry; the trust cells are exactly the cells
selected with a candidate other than β = 0. Never imports eleveniq.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CANDIDATES = ("beta_0", "beta_hat", "team_share", "beta_1")
PROXIES = ("poss_dur_loo", "poss_pass_loo")
PIN_TOL = 2e-9           # the writer rounds to 9 decimals; the engines store full floats
REUSED_KEYS = ("n", "f_hat", "ci_lo", "ci_hi", "r_m", "s2_rms", "s2_rms_a", "power_at_f0")


class BlockError(ValueError):
    """The engine outputs cannot be embedded (stale, inconsistent or incomplete)."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close(a: Any, b: Any, where: str) -> None:
    if a is None or b is None or abs(float(a) - float(b)) > PIN_TOL:
        raise BlockError(f"{where}: engine {a!r} ≠ this run {b!r} (re-run the M4g4c engines)")


def _pin_beta0(cell: dict, meas_cell: dict, where: str) -> None:
    b0 = cell["candidates"]["beta_0"]
    for proxy in PROXIES:
        got, ref = b0["c1_rho"][proxy], meas_cell["possession"][proxy]
        if set(got["by_league"]) != set(ref["by_league"]):
            raise BlockError(f"{where} {proxy}: leagues {sorted(got['by_league'])} ≠ "
                             f"{sorted(ref['by_league'])}")
        for lg, v in got["by_league"].items():
            _close(v, ref["by_league"][lg], f"{where} β = 0 ρ {proxy} league {lg}")
        _close(got["mean"], ref["rho_within_mean"], f"{where} β = 0 mean ρ {proxy}")
    _close(b0["c3_r_full"]["r_full"], meas_cell["r_full"], f"{where} β = 0 r_full")
    d9 = b0["c4_d9_league_means"]
    ref_d9 = meas_cell["league_mean_global_pct"]
    if set(d9) != set(ref_d9):
        raise BlockError(f"{where}: D9 leagues differ")
    for lg, v in d9.items():
        _close(v, ref_d9[lg], f"{where} β = 0 D9 league {lg}")


def _candidate(c: dict) -> dict:
    if c.get("fit_failed"):          # β̂ only: a team with Σ count = 0 (the candidate fails)
        return {"fit_failed": True}
    return {
        "c1_rho": {p: {"by_league": c["c1_rho"][p]["by_league"], "mean": c["c1_rho"][p]["mean"]}
                   for p in PROXIES},
        "c2_gap": {p: {"by_league": c["c2_gap"][p]["by_league"], "gap": c["c2_gap"][p]["gap"],
                       "teams_q1_q5": c["c2_gap"][p]["teams_q1_q5"]} for p in PROXIES},
        "c3_r_full": c["c3_r_full"]["r_full"],
        "c4_d9_league_means": c["c4_d9_league_means"],
    }


def _mover_entry(r: dict) -> dict:
    """An adjusted run in the movers block's entry shape (spikes/m4g3_movers.movers_block)."""
    return {
        "n": r["n"], "excluded": r["excluded"], "f_hat": r["f_hat"],
        "ci_lo": r["ci"][0], "ci_hi": r["ci"][1], "r_m": r["r_m"],
        "s2_rms": r["s2_rms"], "s2_rms_a": r["s2_rms_a"], "boot_inf": r["boot_inf"],
        "power_at_f0": r["low_power"]["power_at_f0"], "power_se": r["low_power"]["se"],
        "extra_seeds": [{"k": e["k"], "n": e["n"], "ci_lo": e["ci"][0], "ci_hi": e["ci"][1]}
                        for e in r["extra_seeds"]],
    }


def possession_block(meas: dict, criteria_path: Path, movers_path: Path, trust_path: Path
                     ) -> dict:
    """The `possession` block for the measurements `meas` this writer run is producing (its
    `metrics` and `movers` already filled). Raises BlockError on any pin failure."""
    crit = json.loads(criteria_path.read_text())
    mov = json.loads(movers_path.read_text())
    trust = json.loads(trust_path.read_text())
    version = crit["registry_version"]
    if mov["registry_version"] != version or trust["possession_json_registry_version"] != version:
        raise BlockError(f"engine outputs from different runs: criteria {version[:12]}, movers "
                         f"{mov['registry_version'][:12]}, trust "
                         f"{trust['possession_json_registry_version'][:12]}")

    cells: dict[str, dict[str, dict]] = {}
    for c in crit["cells"]:
        m, g = c["metric"], c["group"]
        where = f"{m} × {g}"
        try:
            meas_cell = meas["metrics"][m]["groups"][g]
        except KeyError:
            raise BlockError(f"{where}: no per-90 measurement in this run") from None
        _pin_beta0(c, meas_cell, where)
        if set(c["candidates"]) != set(CANDIDATES):
            raise BlockError(f"{where}: candidates {sorted(c['candidates'])}")
        cells.setdefault(m, {})[g] = {
            "directional": c["directional"],
            "e_ref": c["e_ref"],
            "beta_hat": c["beta_hat"],
            "beta_hat_lolo": c["beta_hat_lolo"],
            "beta_hat_ci": c["beta_hat_ci"]["ci"],
            "zero_teams": c["zero_teams"],
            "candidates": {k: _candidate(c["candidates"][k]) for k in CANDIDATES},
            "spike_selected": c["selected"],
        }

    adjusted = {(m, g) for m, gs in cells.items() for g, v in gs.items()
                if v["spike_selected"] not in (None, "beta_0")}
    trust_cells: dict[str, dict[str, dict]] = {}
    for t in trust["cells"]:
        m, g = t["metric"], t["group"]
        if cells.get(m, {}).get(g, {}).get("spike_selected") != t["selected"]:
            raise BlockError(f"trust {m} × {g}: selected {t['selected']!r} ≠ the criteria file's")
        trust_cells.setdefault(m, {})[g] = {
            "a": {"p_same": t["a"]["p_same"], "tally": t["a"]["tally"],
                  "undefined": t["a"]["undefined"]},
            "b": {"selected_without": t["b"]["selected_without"]},
            "c": {"p_rho": t["c"]["p_rho"], "p_gap": t["c"]["p_gap"]},
            # (d) applies to β̂ cells only (None otherwise)
            "d": None if t["d"] is None else {
                k: {"beta_hat_all4": t["d"][k]["beta_hat_all4"], "selected": t["d"][k]["selected"],
                    "teams_excluded": t["d"][k]["teams_excluded"]} for k in ("10", "20")},
        }
    got = {(m, g) for m, gs in trust_cells.items() for g in gs}
    if got != adjusted:
        raise BlockError(f"trust cells {sorted(got)} ≠ the adjusted selections {sorted(adjusted)}")

    metric = mov["metric"]
    reused = mov["beta_0_reused"]
    mine = meas["movers"]["metrics"][metric]
    for k in REUSED_KEYS:
        _close(reused[k], mine[k], f"movers {metric} β = 0 reuse {k}")
    if set(mov["candidates"]) != set(CANDIDATES[1:]):
        raise BlockError(f"movers candidates {sorted(mov['candidates'])}")

    return {
        "spec": "ADR 004 §13.8; plan 005 M4g4c, M4g4c2, M4g4d. Numbers only: the registry applies "
                "every threshold and re-derives the selection",
        "inputs": {
            "criteria": {"path": "data/interim/m4g4c/possession.json",
                         "sha256": _sha256(criteria_path), "generated_by": crit["generated_by"]},
            "movers": {"path": "data/interim/m4g4c/movers.json",
                       "sha256": _sha256(movers_path), "generated_by": mov["generated_by"]},
            "trust": {"path": "data/interim/m4g4c2/trust.json",
                      "sha256": _sha256(trust_path), "generated_by": trust["generated_by"]},
            "registry_version": version,
        },
        "rules": crit["rules"],
        "trust_rules": trust["rules"],
        "k": crit["k"],
        "candidates": list(CANDIDATES),
        "cells": cells,
        "trust": trust_cells,
        "movers": {
            "metric": metric, "seed": mov["seed"], "draws": mov["draws"], "b": mov["b"],
            "stream_keys": mov["stream_keys"],
            "candidates": {k: _mover_entry(mov["candidates"][k]) for k in CANDIDATES[1:]},
        },
    }
