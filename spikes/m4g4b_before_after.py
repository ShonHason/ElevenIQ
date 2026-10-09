"""
M4g4b STOP rule for the measurement re-write after the pipeline columns (plan 005 M4g4b).

The new count columns change both parquet shas and the season column list, so
spikes/m4_decision_measurements.py --write-json re-runs. Stops (exit 1) unless:
- every measurement section other than `provenance` is identical (cells, ratios, cohorts,
  reliability, movers, ...), and `generated_by` / `schema_version` too;
- in `provenance`, only match_parquet_sha256, season_parquet_sha256 and season_columns
  change; season_columns gains exactly the 9 M4g4b columns and keeps every old column;
  both shas equal the current parquets';
- the registry snapshots (tools/registry_snapshot.py) before and after agree on everything
  except registry_version, which must change (the measurements are hashed into it).

  .venv/bin/python spikes/m4g4b_before_after.py \\
      --before-measurements data/interim/m4g4b_snapshot/measurements_old.json \\
      --after-measurements src/eleveniq/analytics/measurements/statsbomb_27.json \\
      --before-registry data/interim/m4g4b_snapshot/registry.json \\
      --after-registry data/interim/m4g4b_after/registry.json

No eleveniq imports (tests/test_spikes_independent.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ("ball_recoveries", "tackles_attempted", "pressures", "clearances", "dribbled_past",
           "fouls_committed", "blocks", "interceptions")
NEW_COLUMNS = frozenset({"opponent_open_play_passes_while_on",
                         *(f"team_{a}_while_on" for a in ACTIONS)})
MAY_CHANGE = frozenset({"match_parquet_sha256", "season_parquet_sha256", "season_columns"})
# provenance sha key → the provenance key holding that file's path (written out; plan 005 M4g4b
# backlog item 4)
SHA_PATH_KEYS = {"match_parquet_sha256": "match_parquet", "season_parquet_sha256": "season_parquet"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measurement_errors(before: dict, after: dict, shas: dict[str, str] | None = None) -> list[str]:
    """shas: the current parquets' sha256 by provenance key (None skips that check)."""
    errors: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key != "provenance" and before.get(key) != after.get(key):
            errors.append(f"measurements: section {key!r} changed")
    bp, ap = before.get("provenance", {}), after.get("provenance", {})
    for key in sorted(set(bp) | set(ap)):
        if key not in MAY_CHANGE and bp.get(key) != ap.get(key):
            errors.append(f"provenance: {key!r} changed")
    old, new = list(bp.get("season_columns", [])), list(ap.get("season_columns", []))
    if [c for c in new if c not in NEW_COLUMNS] != old:
        errors.append("provenance: season_columns lost, reordered or changed an existing column")
    if set(new) - set(old) != NEW_COLUMNS:
        errors.append(f"provenance: added season columns {sorted(set(new) - set(old))}, "
                      f"expected {sorted(NEW_COLUMNS)}")
    for key, sha in (shas or {}).items():
        if ap.get(key) != sha:
            errors.append(f"provenance: {key} is not the current parquet's sha256")
    return errors


def registry_errors(before: dict, after: dict) -> list[str]:
    errors = [f"registry: {key!r} changed" for key in sorted(set(before) | set(after))
              if key != "registry_version" and before.get(key) != after.get(key)]
    if before.get("registry_version") == after.get("registry_version"):
        errors.append("registry: registry_version did not change (the measurements are hashed into it)")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    for name in ("--before-measurements", "--after-measurements", "--before-registry", "--after-registry"):
        ap.add_argument(name, type=Path, required=True)
    args = ap.parse_args()
    load = lambda p: json.loads(p.read_text())  # noqa: E731
    after = load(args.after_measurements)
    shas = {k: sha256(ROOT / after["provenance"][path_key]) for k, path_key in SHA_PATH_KEYS.items()}
    errors = measurement_errors(load(args.before_measurements), after, shas)
    rb, ra = load(args.before_registry), load(args.after_registry)
    errors += registry_errors(rb, ra)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"FAIL: {len(errors)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: only provenance changed (2 shas, +{len(NEW_COLUMNS)} season columns); movers and "
          f"every other section identical; 0 registry changes; REGISTRY_VERSION "
          f"{rb['registry_version'][:12]}… -> {ra['registry_version'][:12]}…")


if __name__ == "__main__":
    main()
