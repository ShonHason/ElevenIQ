"""
M4g4a STOP rule for the s2_rms_a write (plan 005 M4g4a, ADR 004 §13.8 D7; user 2026-10-08).

Stops (exit 1) unless:
- every measurement section other than `movers` is identical, and the movers block is identical
  apart from its `metrics`;
- each of the 34 M4g3b mover entries is identical in every existing field, with `s2_rms_a` as the
  only added key: present (a finite number > 0) iff the metric is testable (carries f_hat);
- the registry snapshots (tools/registry_snapshot.py) before and after agree on every cell,
  boundary row, group list, majority-admitted cell and mover verdict and flag; only the
  registry_version may differ (the measurements are hashed into it), and it must differ.

  .venv/bin/python spikes/m4g4a_movers_before_after.py \\
      --before-measurements data/interim/m4g4a_snapshot/measurements_old.json \\
      --after-measurements src/eleveniq/analytics/measurements/statsbomb_27.json \\
      --before-registry data/interim/m4g4a_snapshot/registry.json \\
      --after-registry data/interim/m4g4a_after/registry.json

No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ADDED = "s2_rms_a"


def measurement_errors(before: dict, after: dict) -> list[str]:
    errors: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key != "movers" and before.get(key) != after.get(key):
            errors.append(f"measurements: section {key!r} changed")
    bb, ab = before.get("movers", {}), after.get("movers", {})
    for key in sorted(set(bb) | set(ab)):
        if key != "metrics" and bb.get(key) != ab.get(key):
            errors.append(f"movers block: {key!r} changed")
    bm, am = bb.get("metrics", {}), ab.get("metrics", {})
    if sorted(bm) != sorted(am):
        errors.append(f"movers block: metric set changed {sorted(set(bm) ^ set(am))}")
    for metric in sorted(set(bm) & set(am)):
        b, a = bm[metric], am[metric]
        for key in sorted(b):
            if key not in a:
                errors.append(f"{metric}: field {key!r} removed")
            elif a[key] != b[key]:
                errors.append(f"{metric}: field {key!r} changed {b[key]!r} -> {a[key]!r}")
        added = sorted(set(a) - set(b))
        testable = "f_hat" in b
        want = [ADDED] if testable else []
        if added != want:
            errors.append(f"{metric}: added keys {added}, expected {want}")
        if testable and ADDED in a:
            x = a[ADDED]
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0:
                errors.append(f"{metric}: {ADDED} {x!r} is not a finite positive number")
    return errors


def registry_errors(before: dict, after: dict) -> list[str]:
    errors: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key != "registry_version" and before.get(key) != after.get(key):
            errors.append(f"registry: {key!r} changed")
    if before.get("registry_version") == after.get("registry_version"):
        errors.append("registry: registry_version did not change (the measurements are hashed into it)")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    for name in ("--before-measurements", "--after-measurements", "--before-registry", "--after-registry"):
        ap.add_argument(name, type=Path, required=True)
    args = ap.parse_args()
    load = lambda p: json.loads(p.read_text())  # noqa: E731
    errors = measurement_errors(load(args.before_measurements), load(args.after_measurements))
    rb, ra = load(args.before_registry), load(args.after_registry)
    errors += registry_errors(rb, ra)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"FAIL: {len(errors)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    n = sum(ADDED in m for m in load(args.after_measurements)["movers"]["metrics"].values())
    print(f"OK: every existing field identical; {ADDED} added to {n} testable metrics; "
          f"0 cell, verdict or flag changes; REGISTRY_VERSION "
          f"{rb['registry_version'][:12]}… -> {ra['registry_version'][:12]}…")


if __name__ == "__main__":
    main()
