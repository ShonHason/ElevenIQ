"""
M4g2 registry before/after with the STOP rule (plan 005 M4g2).

Compares two registry dumps (tools/registry_snapshot.py: the snapshot before any registry
change and the dump after it) against spikes/expected_m4g2_flips.json, written by
spikes/m4g2_reliability_reference.py --write-expected before the registry code changed.

STOP (exit 1) on any of:
- a status, low_reliability or team_style_biased change that is not in the expected list, or an
  expected change that did not happen;
- new cells other than the expected ones, or a removed cell;
- a basis, global_ok, d9_margin, min_attempts or signature_min_count change on an existing cell
  (M4g2 changes none of their inputs);
- a reasons change other than the expected cells' or the added "majority_triggered" tag, which
  is allowed only where this script finds the majority rule firing in the measurements;
- a direction or family change other than dribbled_past_p90 → style;
- a per-group DNA list other than the old list ± the expected entries and exits;
- the majority-admitted set or the majority boundary rows differing from the expected ones;
- a stored reliability that is not the new measurements' value.

No eleveniq imports (tests/test_spikes_independent.py).

Usage:
  .venv/bin/python spikes/m4g2_before_after.py --before data/interim/m4g2_snapshot/registry.json \
      --after data/interim/m4g2_after/registry.json \
      --measurements src/eleveniq/analytics/measurements/statsbomb_27.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPECTED = Path(__file__).resolve().parent / "expected_m4g2_flips.json"
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
FLAGS = ("status", "low_reliability", "team_style_biased")
UNCHANGED = ("basis", "global_ok", "d9_margin", "min_attempts", "signature_min_count", "unit")
MAJORITY = "majority_triggered"
RHO_TRIGGER, MAJORITY_LEAGUES = 0.4, 3          # ADR 004 §13.1, written out
PROXIES = ("poss_dur_loo", "poss_pass_loo")
DIRECTION_CHANGES = {"dribbled_past_p90": ("lower_better", "style")}
TOL = 1e-12


def majority_fires(meas: dict, col: str, grp: str) -> bool:
    sec = meas["metrics"] if col in meas["metrics"] else meas["ratios"]
    block = sec.get(col, {}).get("groups", {}).get(grp, {}).get("possession")
    if not block:
        return False
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    for p in PROXIES:
        hit = [block[p]["by_league"][c] for c in pool if abs(block[p]["by_league"][c]) >= RHO_TRIGGER]
        if len(hit) >= MAJORITY_LEAGUES and (all(v > 0 for v in hit) or all(v < 0 for v in hit)):
            return True
    return False


def stored_r(meas: dict, cell: dict) -> float | None:
    col, grp, unit = cell["column"], cell["group"], cell["unit"]
    if unit == "per90":
        return meas["metrics"].get(col, {}).get("groups", {}).get(grp, {}).get("r_full")
    if unit == "ratio":
        return meas["ratios"].get(col, {}).get("groups", {}).get(grp, {}).get("split_half_r_full")
    return meas["team_pass_share"].get(grp, {}).get("split_half_r_full")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--measurements", type=Path, required=True)
    args = ap.parse_args()
    before = json.loads(args.before.read_text())
    after = json.loads(args.after.read_text())
    meas = json.loads(args.measurements.read_text())
    exp = json.loads(EXPECTED.read_text())
    errors: list[str] = []

    if exp["snapshot_registry_version"] != before["registry_version"]:
        errors.append("the expected list was written against another snapshot")
    if meas["reliability"]["k"] != exp["measurements_k"]:
        errors.append("the measurements' K differs from the one the expected list was written from")
    old = {(c["column"], c["group"]): c for c in before["cells"]}
    new = {(c["column"], c["group"]): c for c in after["cells"]}
    want = {(c["column"], c["group"]): c for c in exp["changes"]}

    removed = set(old) - set(new)
    if removed:
        errors.append(f"cells removed: {sorted(removed)}")
    added = set(new) - set(old)
    want_new = {k for k, c in want.items() if c["old"]["status"] is None}
    if added != want_new:
        errors.append(f"new cells {sorted(added)} ≠ expected {sorted(want_new)}")

    seen = set()
    reason_tags = 0
    for key, n in sorted(new.items()):
        o = old.get(key)
        got_new = {f: n[f] for f in FLAGS}
        if o is None:
            if key in want and want[key]["new"] != got_new:
                errors.append(f"{key}: new cell {got_new} ≠ expected {want[key]['new']}")
            seen.add(key)
            continue
        got_old = {f: o[f] for f in FLAGS}
        if got_old != got_new:
            if key not in want:
                errors.append(f"UNEXPECTED {key}: {got_old} → {got_new} {o['reasons']} → {n['reasons']}")
            elif want[key]["old"] != got_old or want[key]["new"] != got_new:
                errors.append(f"{key}: {got_old} → {got_new}, expected {want[key]['old']} → {want[key]['new']}")
            seen.add(key)
        elif key in want:
            errors.append(f"{key}: expected {want[key]['old']} → {want[key]['new']}, unchanged")
        for f in UNCHANGED:
            if o.get(f) != n.get(f):
                errors.append(f"{key}: {f} {o.get(f)} → {n.get(f)}")
        if (o["direction"], n["direction"]) != (o["direction"], o["direction"]):
            if DIRECTION_CHANGES.get(key[0]) != (o["direction"], n["direction"]):
                errors.append(f"{key}: direction {o['direction']} → {n['direction']}")
        if o["family"] != n["family"]:
            errors.append(f"{key}: family {o['family']} → {n['family']}")
        if o["reasons"] != n["reasons"] and key not in want:
            stripped = [r for r in n["reasons"] if r != MAJORITY]
            if stripped == o["reasons"] and MAJORITY in n["reasons"] and majority_fires(meas, *key):
                reason_tags += 1
            else:
                errors.append(f"{key}: reasons {o['reasons']} → {n['reasons']}")
        r = stored_r(meas, n)
        if r is not None and (n["reliability"] is None or abs(n["reliability"] - r) > TOL):
            errors.append(f"{key}: reliability {n['reliability']} ≠ measurement {r}")
    for col, (a, b) in DIRECTION_CHANGES.items():
        dirs = {(new[k]["direction"]) for k in new if k[0] == col}
        if dirs != {b}:
            errors.append(f"{col}: direction {dirs}, expected {b}")
    missing = set(want) - seen
    if missing:
        errors.append(f"expected changes that did not happen: {sorted(missing)}")

    for g in GROUPS:
        exp_dna = (set(before["groups"][g]["dna"]) - set(exp["by_group"][g]["leave_dna"])) \
            | set(exp["by_group"][g]["enter_dna"])
        if set(after["groups"][g]["dna"]) != exp_dna:
            errors.append(f"{g} DNA list: +{sorted(set(after['groups'][g]['dna']) - exp_dna)} "
                          f"−{sorted(exp_dna - set(after['groups'][g]['dna']))} vs expected")

    adm = sorted((a["group"], a["column"]) for a in after["majority_admitted"])
    exp_adm = sorted((a["group"], a["column"]) for a in exp["majority_admitted_by_allow_list"])
    if adm != exp_adm:
        errors.append(f"majority-admitted {adm} ≠ expected {exp_adm}")
    by_cell = {(a["group"], a["column"]): a for a in exp["majority_admitted_by_allow_list"]}
    for a in after["majority_admitted"]:
        e = by_cell.get((a["group"], a["column"]))
        if e is None:
            continue
        for p in a["proxies"]:
            ep = e["per_proxy"][p["proxy"]]
            if (list(p["per_league"]) != ep["by_league"] or p["leagues_at_trigger"] != ep["leagues_at_0.4"]
                    or abs(p["max_single_league"] - ep["max_single_league"]) > TOL):
                errors.append(f"majority-admitted {a['group']} {a['column']} {p['proxy']}: detail differs")
    rows = sorted((b["rule"], b["group"], b["column"], round(b["value"], 9))
                  for b in after["boundary"] if b["rule"].startswith("majority"))
    exp_rows = sorted((b["rule"], b["group"], b["column"], round(b["value"], 9)) for b in exp["boundary_majority"])
    if rows != exp_rows:
        errors.append(f"majority boundary rows {rows} ≠ expected {exp_rows}")
    old_rows = {(b["rule"], b["group"], b["column"]) for b in before["boundary"]}
    new_rows = {(b["rule"], b["group"], b["column"]) for b in after["boundary"]}

    print(f"registry {before['registry_version'][:12]} → {after['registry_version'][:12]}")
    print(f"cells {len(old)} → {len(new)}; expected changes {len(want)}, seen {len(seen)}; "
          f"majority_triggered tags added on unchanged-status cells: {reason_tags}")
    for k in sorted(want, key=lambda k: (GROUPS.index(k[1]), k[0])):
        c = want[k]
        print(f"  {k[1]:10s} {k[0]:30s} {c['old']['status']} → {c['new']['status']}  ({c['why']})")
    for g in GROUPS:
        print(f"  {g:10s} DNA {len(before['groups'][g]['dna'])} → {len(after['groups'][g]['dna'])}  "
              f"+{exp['by_group'][g]['enter_dna']} −{exp['by_group'][g]['leave_dna']}")
    print(f"boundary rows {len(old_rows)} → {len(new_rows)}: added {len(new_rows - old_rows)}, "
          f"removed {len(old_rows - new_rows)}")
    for r in sorted(new_rows - old_rows):
        print(f"  + {r}")
    for r in sorted(old_rows - new_rows):
        print(f"  − {r}")
    if errors:
        print(f"STOP: {len(errors)} unexpected differences:")
        for e in errors:
            print("  " + e)
        return 1
    print("OK: every registry change is in the expected list (STOP rule passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
