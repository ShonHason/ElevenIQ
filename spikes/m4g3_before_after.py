"""
M4g3b registry before/after with the STOP rule (plan 005 M4g3b, ADR 004 §13.7).

Compares two registry dumps (tools/registry_snapshot.py: the snapshot before the measurement
and registry change, and the dump after it) against spikes/expected_m4g3_flips.json, written by
spikes/m4g3_movers_reference.py --write-expected before any registry code.

STOP (exit 1) on any of:
- any cell change of any kind: a cell added or removed, or any field of an existing cell
  (status, reasons, notes, flags, reliability, basis, D9 fields, min_attempts, family, ...);
- a change in the boundary rows, the majority-admitted set or a per-group status list;
- the before dump already holding mover verdicts, or its version not the expected snapshot's;
- the after dump's verdicts differing from the expected list: the metric set, or any metric's
  verdict, n, underpowered, borderline_seed, below_model_range or extra-seed verdicts;
- a DNA metric (any group, after) without a verdict;
- the measurements' movers block not covering spikes/m4g3_movers.MOVER_METRICS, or its seed and
  stream keys not the written ones;
- the registry thresholds (config.MOVER_*, read from config.py as source text) differing from
  the spike's (m4g3_power THRESHOLD, MIN_MOVERS, LOW_POWER; m4g3_movers EXTRA_SEEDS) or from the
  expected file's rules.

No eleveniq imports (tests/test_spikes_independent.py).

Usage:
  .venv/bin/python spikes/m4g3_before_after.py --before data/interim/m4g3b_snapshot/registry.json \
      --after data/interim/m4g3b_after/registry.json \
      --measurements src/eleveniq/analytics/measurements/statsbomb_27.json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m4g3_movers as mv  # noqa: E402  (MOVER_METRICS, EXTRA_SEEDS, stream_keys)
import m4g3_power as pw  # noqa: E402  (THRESHOLD, MIN_MOVERS, LOW_POWER, SEED)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = Path(__file__).resolve().parent / "expected_m4g3_flips.json"
CONFIG = ROOT / "src" / "eleveniq" / "config.py"
GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
VERDICT_FIELDS = ("verdict", "n", "underpowered", "borderline_seed", "below_model_range")


def config_constants(names: tuple[str, ...]) -> dict[str, object]:
    """Module-level constants of config.py, read as source text (literal values only)."""
    out = {}
    for node in ast.parse(CONFIG.read_text()).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id in names:
            out[target.id] = ast.literal_eval(node.value)
    missing = set(names) - set(out)
    if missing:
        raise SystemExit(f"STOP: config.py lacks {sorted(missing)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--measurements", type=Path, required=True)
    args = ap.parse_args()
    before = json.loads(args.before.read_text())
    after = json.loads(args.after.read_text())
    meas = json.loads(args.measurements.read_text())
    exp = json.loads(EXPECTED.read_text())
    stops: list[str] = []

    # cells, boundary, majority, group lists: unchanged
    key = lambda c: (c["column"], c["group"])  # noqa: E731
    bc = {key(c): c for c in before["cells"]}
    ac = {key(c): c for c in after["cells"]}
    for k in sorted(set(bc) ^ set(ac)):
        stops.append(f"cell {k} {'added' if k in ac else 'removed'}")
    changed_cells = 0
    for k in sorted(set(bc) & set(ac)):
        diff = sorted(f for f in set(bc[k]) | set(ac[k]) if bc[k].get(f) != ac[k].get(f))
        if diff:
            changed_cells += 1
            stops.append(f"cell {k}: {diff} changed")
    for section in ("boundary", "majority_admitted", "groups", "source"):
        if before[section] != after[section]:
            stops.append(f"{section} changed")

    # before / after versions
    if "movers" in before and before["movers"]:
        stops.append("the before dump already holds mover verdicts")
    if before["registry_version"] != exp["snapshot_registry_version"]:
        stops.append(f"before version {before['registry_version'][:12]} is not the expected "
                     f"snapshot's {exp['snapshot_registry_version'][:12]}")
    if after["registry_version"] == before["registry_version"]:
        stops.append("REGISTRY_VERSION did not change")

    # verdicts vs expected
    got, want = after.get("movers", {}), exp["mover_verdicts"]
    if sorted(got) != sorted(want):
        stops.append(f"verdict metric set differs: {sorted(set(got) ^ set(want))}")
    verdict_diffs = 0
    for m in sorted(set(got) & set(want)):
        g, w = got[m], want[m]
        diff = [f for f in VERDICT_FIELDS if g[f] != w[f]]
        if list(g["extra_seed_verdicts"]) != list(w["extra_seed_verdicts"]):
            diff.append("extra_seed_verdicts")
        if diff:
            verdict_diffs += 1
            stops.append(f"verdict {m}: {[(f, g.get(f), w.get(f)) for f in diff]}")
    dna = sorted({m for grp in GROUPS for m in after["groups"][grp]["dna"]})
    missing = [m for m in dna if m not in got]
    if missing:
        stops.append(f"DNA metrics without a verdict: {missing}")

    # measurements block vs the spike's written spec
    block = meas.get("movers", {})
    if list(block.get("metric_list", [])) != list(mv.MOVER_METRICS):
        stops.append("movers block metric_list != m4g3_movers.MOVER_METRICS")
    if block.get("min_movers") != pw.MIN_MOVERS:
        stops.append(f"movers block min_movers {block.get('min_movers')} != spike {pw.MIN_MOVERS}")
    if block.get("seed") != pw.SEED or block.get("stream_keys") != {
            "primary": list(mv.stream_keys(0)),
            "extra": [list(mv.stream_keys(k)) for k in range(1, mv.EXTRA_SEEDS + 1)]}:
        stops.append("movers block seed / stream keys differ from the spike's")

    # thresholds: config (registry Rules defaults) == spike == expected
    cfg = config_constants(("MOVER_F_THRESHOLD", "MOVER_MIN_N", "MOVER_LOW_POWER"))
    trio = {"threshold": (cfg["MOVER_F_THRESHOLD"], pw.THRESHOLD, exp["rules"]["threshold"]),
            "min_movers": (cfg["MOVER_MIN_N"], pw.MIN_MOVERS, exp["rules"]["min_movers"]),
            "low_power": (cfg["MOVER_LOW_POWER"], pw.LOW_POWER, exp["rules"]["low_power"]),
            "extra_seeds": (len(block.get("stream_keys", {}).get("extra", [])), mv.EXTRA_SEEDS,
                            exp["rules"]["extra_seeds"])}
    for name, vals in trio.items():
        if len(set(vals)) != 1:
            stops.append(f"{name}: config / spike / expected = {vals}")

    tally: dict[str, int] = {}
    for v in got.values():
        tally[v["verdict"]] = tally.get(v["verdict"], 0) + 1
    print(f"registry {before['registry_version'][:12]} → {after['registry_version'][:12]}")
    print(f"cells: {len(ac)} (before {len(bc)}); changed {changed_cells}; boundary, majority-admitted, "
          f"group lists unchanged: {all(before[s] == after[s] for s in ('boundary', 'majority_admitted', 'groups'))}")
    print(f"verdicts: {len(got)} metrics {dict(sorted(tally.items()))}; "
          f"underpowered {sum(bool(v['underpowered']) for v in got.values())}; "
          f"borderline {sorted(m for m, v in got.items() if v['borderline_seed'])}; "
          f"below model range {sorted(m for m, v in got.items() if v['below_model_range'])}")
    print(f"verdicts differing from expected: {verdict_diffs}; DNA metrics without a verdict: {len(missing)}")
    print(f"thresholds config = spike = expected: {all(len(set(v)) == 1 for v in trio.values())} {trio}")
    if stops:
        print("\nSTOP:\n  " + "\n  ".join(stops), file=sys.stderr)
        raise SystemExit(1)
    print("PASS: no cell changes; every verdict and flag as expected")


if __name__ == "__main__":
    main()
