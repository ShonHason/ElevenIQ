"""
M4g4 STOP rule for the M4g4d registry write (plan 005 "M4g4 plan" STOP rule, M4g4d; ADR 004 §13.8).

Compares the measurements and the registry snapshots (tools/registry_snapshot.py) before and
after M4g4d against spikes/expected_m4g4_flips.json (written by the independent M4g4c / M4g4c2
references before any registry code). Stops (exit 1) on:
- measurements: any section other than the added `possession` block changed, or
  schema_version not 2 -> 3;
- registry: any cell, boundary row, majority-admitted cell or per-group list changed; any
  existing M4g3b mover field changed, or a mover entry gained a key other than s2_rms_a;
  registry_version unchanged;
- a Defending status, its candidate, mover verdict or underpowered note ≠ the expected file's;
- a selection ≠ the expected `selection`; an applied (vector) column ≠ `vector_adjusted_columns`;
- a trust-check outcome ≠ the expected `trust_check` (keep_adjusted, (a)-(d) values);
- a mover verdict per candidate ≠ `mover_verdicts` (verdict, extra streams, used for restore);
- the engines' rule copies in the possession block, or config.py's thresholds (read with ast,
  no import), ≠ the expected `rules`.

  .venv/bin/python spikes/m4g4_before_after.py \\
      --before-measurements data/interim/m4g4d_snapshot/measurements_old.json \\
      --after-measurements src/eleveniq/analytics/measurements/statsbomb_27.json \\
      --before-registry data/interim/m4g4d_snapshot/registry.json \\
      --after-registry data/interim/m4g4d_after/registry.json

No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "spikes" / "expected_m4g4_flips.json"
CONFIG = ROOT / "src" / "eleveniq" / "config.py"
SAME_SECTIONS = ("cells", "boundary", "majority_admitted", "groups", "source")
# expected `rules` key -> (where it lives: "block" = possession.rules, "trust" = the trust
# rules, "config" = config.py, "movers" = the movers block) and its name there
RULE_SOURCES = {
    "rho_max": [("block", "rho_max"), ("config", "POSSESSION_CRITERION_RHO_MAX")],
    "sign_dead_zone": [("block", "sign_dead_zone"), ("config", "POSSESSION_SIGN_DEAD_ZONE")],
    "inconsistent": [("block", "inconsistent"), ("config", "POSSESSION_INCONSISTENT_RHO")],
    "gap_max": [("block", "gap_max"), ("config", "POSSESSION_GAP_MAX")],
    "r_full_min": [("block", "r_full_min"), ("config", "DNA_RELIABILITY_MIN")],
    "g2": [("block", "g2")],
    "g3": [("block", "g3")],
    "beta_b": [("block", "beta_b")],
    "threshold": [("config", "MOVER_F_THRESHOLD")],
    "min_movers": [("config", "MOVER_MIN_N"), ("movers", "min_movers")],
    "low_power": [("config", "MOVER_LOW_POWER")],
    "extra_seeds": [("movers", "extra_seeds")],
}
TRUST_CONFIG = {"stab_min": "TRUST_STABILITY_MIN", "lolo_min": "TRUST_LOLO_MIN_AGREE",
                "p_max": "TRUST_P_MAX", "d_min": "TRUST_D_MIN_COUNT"}
TOL = 1e-9


def config_constants(path: Path = CONFIG) -> dict:
    """Module-level literal constants of config.py, without importing it."""
    out = {}
    for n in ast.parse(path.read_text()).body:
        target = n.target if isinstance(n, ast.AnnAssign) else (
            n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else None)
        if isinstance(target, ast.Name) and n.value is not None:
            try:
                out[target.id] = ast.literal_eval(n.value)
            except ValueError:
                pass
    return out


def measurement_errors(before: dict, after: dict) -> list[str]:
    errors = [f"measurements: section {k!r} changed" for k in sorted(set(before) | set(after))
              if k not in ("possession", "schema_version") and before.get(k) != after.get(k)]
    if "possession" in before or "possession" not in after:
        errors.append("measurements: the possession block must be new")
    if (before.get("schema_version"), after.get("schema_version")) != (2, 3):
        errors.append(f"measurements: schema_version {before.get('schema_version')} -> "
                      f"{after.get('schema_version')}, expected 2 -> 3")
    return errors


def registry_errors(before: dict, after: dict) -> list[str]:
    errors = [f"registry: {k!r} changed" for k in SAME_SECTIONS if before.get(k) != after.get(k)]
    bm, am = before.get("movers", {}), after.get("movers", {})
    if set(bm) != set(am):
        errors.append(f"registry movers: metrics {sorted(set(bm) ^ set(am))} added or removed")
    for col in sorted(set(bm) & set(am)):
        for k, v in bm[col].items():
            if am[col].get(k) != v:
                errors.append(f"registry movers {col}.{k}: {v!r} -> {am[col].get(k)!r}")
        extra = set(am[col]) - set(bm[col]) - {"s2_rms_a"}
        if extra:
            errors.append(f"registry movers {col}: added keys {sorted(extra)}")
    if before.get("registry_version") == after.get("registry_version"):
        errors.append("registry: registry_version did not change")
    return errors


def expected_errors(after_reg: dict, after_meas: dict, exp: dict, cfg: dict) -> list[str]:
    errors: list[str] = []
    fs = after_reg["family_status"]
    for g, want in exp["defending_score_status"].items():
        got = fs[g]
        detail = exp["status_detail"][g]
        if got["status"] != want:
            errors.append(f"Defending {g}: status {got['status']!r} ≠ expected {want!r}")
        if got["candidate"] != detail["selected"]:
            errors.append(f"Defending {g}: candidate {got['candidate']!r} ≠ {detail['selected']!r}")
        underpowered = any(n.startswith("underpowered") for n in got["notes"])
        if detail["underpowered"] is not None and underpowered != detail["underpowered"]:
            errors.append(f"Defending {g}: underpowered note {underpowered} ≠ {detail['underpowered']}")
    cm = {(m["column"], m["candidate"]): m for m in after_reg["candidate_movers"]}
    for g, detail in exp["status_detail"].items():
        if detail["selected"] is not None:
            got = cm[("ball_recoveries_p90", detail["selected"])]["verdict"]["verdict"]
            if got != detail["mover_verdict"]:
                errors.append(f"Defending {g}: mover verdict {got!r} ≠ {detail['mover_verdict']!r}")

    poss = {(p["column"], p["group"]): p for p in after_reg["possession"]}
    want_cells = {(m, g) for g, ms in exp["selection"].items() for m in ms}
    if set(poss) != want_cells:
        errors.append(f"possession cells {sorted(set(poss) ^ want_cells)} differ from the expected")
    for g, ms in exp["selection"].items():
        for m, want in ms.items():
            if (m, g) in poss and poss[(m, g)]["selected"] != want:
                errors.append(f"selection {m} × {g}: {poss[(m, g)]['selected']!r} ≠ {want!r}")
    for g, cols in exp["vector_adjusted_columns"].items():
        got = sorted(m for (m, gg), p in poss.items() if gg == g and p["applied"] != "beta_0")
        if got != sorted(cols):
            errors.append(f"vector {g}: adjusted {got} ≠ expected {sorted(cols)}")

    for key, t in exp["trust_check"]["cells"].items():
        g, m = key.split(" ")
        p = poss.get((m, g))
        tc = None if p is None else p["trust"]
        if tc is None:
            errors.append(f"trust {key}: no trust record")
            continue
        checks = [("candidate", p["selected"], t["candidate"]),
                  ("keep_adjusted", tc["passes"], t["keep_adjusted"]),
                  ("b_agree", tc["b_agree"], t["b_agree"]),
                  ("c_judged", list(tc["c_judged"]), t["c_judged"])]
        for name, got, want in checks:
            if got != want:
                errors.append(f"trust {key} {name}: {got!r} ≠ {want!r}")
        for name, got, want in (("a_p_same", tc["a_p_same"], t["a_p_same"]),
                                ("c_p_rho", tc["c_p"][0], t["c_p_rho"]),
                                ("c_p_gap", tc["c_p"][1], t["c_p_gap"])):
            if abs(got - want) > TOL:
                errors.append(f"trust {key} {name}: {got!r} ≠ {want!r}")
        d_want = None if p["selected"] != "beta_hat" else t["d_selected_ge10"] == "beta_hat"
        if tc["d_pass"] != d_want:
            errors.append(f"trust {key} d_pass: {tc['d_pass']!r} ≠ {d_want!r}")

    for cand, want in exp["mover_verdicts"].items():
        got = cm.get(("ball_recoveries_p90", cand))
        if got is None:
            errors.append(f"mover {cand}: missing")
            continue
        v = got["verdict"]
        if v["verdict"] != want["verdict"] or list(v["extra_seed_verdicts"]) != want["extra_seed_verdicts"]:
            errors.append(f"mover {cand}: {v['verdict']} {v['extra_seed_verdicts']} ≠ "
                          f"{want['verdict']} {want['extra_seed_verdicts']}")
        if v["n"] != want["n"] or any(abs(a - b) > TOL for a, b in zip(v["ci"], want["ci"])):
            errors.append(f"mover {cand}: n / CI differ from the expected")
        if v["s2_rms_a"] is not None and abs(math.sqrt(v["s2_rms_a"]) - want["sqrt_s2_rms_a"]) > 1e-6:
            errors.append(f"mover {cand}: √s2_rms_a {math.sqrt(v['s2_rms_a'])} ≠ {want['sqrt_s2_rms_a']}")
        if list(got["used_for_restore_in"]) != want["used_for_restore_in"]:
            errors.append(f"mover {cand}: used for restore in {got['used_for_restore_in']} ≠ "
                          f"{want['used_for_restore_in']}")

    block = after_meas["possession"]
    places = {"block": block["rules"], "config": cfg,
              "movers": {"min_movers": after_meas["movers"]["min_movers"],
                         "extra_seeds": len(after_meas["movers"]["stream_keys"]["extra"])}}
    for key, want in exp["rules"].items():
        for where, name in RULE_SOURCES.get(key, []):
            got = places[where].get(name)
            if got != want:
                errors.append(f"rules {key}: {where} {name} = {got!r} ≠ expected {want!r}")
        if key not in RULE_SOURCES:
            errors.append(f"rules {key}: no source to check")
    for key, want in exp["trust_check"]["rules"].items():
        if block["trust_rules"].get(key) != want:
            errors.append(f"trust rules {key}: block {block['trust_rules'].get(key)!r} ≠ {want!r}")
        if key in TRUST_CONFIG and cfg.get(TRUST_CONFIG[key]) != want:
            errors.append(f"trust rules {key}: config {TRUST_CONFIG[key]} = "
                          f"{cfg.get(TRUST_CONFIG[key])!r} ≠ {want!r}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    for name in ("--before-measurements", "--after-measurements", "--before-registry", "--after-registry"):
        ap.add_argument(name, type=Path, required=True)
    ap.add_argument("--expected", type=Path, default=EXPECTED)
    args = ap.parse_args()
    load = lambda p: json.loads(p.read_text())  # noqa: E731
    mb, ma = load(args.before_measurements), load(args.after_measurements)
    rb, ra = load(args.before_registry), load(args.after_registry)
    exp = load(args.expected)
    if rb["registry_version"] != exp["snapshot_registry_version"]:
        print(f"FAIL: the before snapshot {rb['registry_version'][:12]} is not the expected file's "
              f"{exp['snapshot_registry_version'][:12]}", file=sys.stderr)
        raise SystemExit(1)
    errors = measurement_errors(mb, ma) + registry_errors(rb, ra) + \
        expected_errors(ra, ma, exp, config_constants())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"FAIL: {len(errors)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    st = ", ".join(f"{g} {s}" for g, s in exp["defending_score_status"].items())
    print(f"OK: measurements +possession (schema 2 -> 3), every other section identical; 0 cell, "
          f"list or M4g3b mover changes; Defending: {st}; 0 adjusted vector columns; selections, "
          f"trust checks, mover verdicts and rules as expected; REGISTRY_VERSION "
          f"{rb['registry_version'][:12]}… -> {ra['registry_version'][:12]}…")


if __name__ == "__main__":
    main()
