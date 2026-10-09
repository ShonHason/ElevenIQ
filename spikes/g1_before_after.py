"""
G1 STOP rule: `player_nickname` through the pipeline (plan 006 pre-W1 gate G1, decision 3).

Compares the snapshot taken before the rebuild (data/interim/g1_snapshot/) with the current
outputs. Stops (exit 1) unless:
- player_match_stats.parquet and player_season_stats.parquet keep every existing column
  byte-identical (same values, same dtype, same order relative to each other) and gain exactly
  `player_nickname`;
- the measurements change in provenance only: season_parquet_sha256 / match_parquet_sha256 (both
  equal to the current files) and season_columns gaining exactly `player_nickname`; every other
  section identical;
- the registry snapshots agree on everything except registry_version, which must change;
- the percentiles, DNA and dimensions parquets are identical except their stamp columns
  (registry_version, *_sha256).
Never imports eleveniq.

  .venv/bin/python tools/registry_snapshot.py --out data/interim/g1_after/registry.json
  .venv/bin/python spikes/g1_before_after.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "data" / "interim" / "g1_snapshot"
AFTER_REGISTRY = ROOT / "data" / "interim" / "g1_after" / "registry.json"
MEASUREMENTS = ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json"
NEW = "player_nickname"
PARQUETS = {"player_match_stats.parquet": ROOT / "data" / "interim" / "player_match_stats.parquet",
            "player_season_stats.parquet": ROOT / "data" / "processed" / "player_season_stats.parquet"}
STAMPED = {f: ROOT / "data" / "processed" / f for f in
           ("player_season_percentiles.parquet", "player_dna.parquet", "player_dna_dimensions.parquet")}
PROV_MAY_CHANGE = {"season_parquet_sha256", "match_parquet_sha256", "season_columns"}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def is_stamp(col: str) -> bool:
    return col == "registry_version" or col.endswith("_sha256")


def parquet_errors(name: str, before: pl.DataFrame, after: pl.DataFrame) -> list[str]:
    errs = []
    old, new = before.columns, after.columns
    if [c for c in new if c != NEW] != old:
        errs.append(f"{name}: existing columns lost, added or reordered")
    if NEW not in new:
        errs.append(f"{name}: {NEW} missing")
    if before.height != after.height:
        return errs + [f"{name}: {before.height} → {after.height} rows"]
    for c in old:
        if c in new and not (before[c].dtype == after[c].dtype and before[c].equals(after[c])):
            errs.append(f"{name}: column {c} changed")
    return errs


def stamped_errors(name: str, before: pl.DataFrame, after: pl.DataFrame) -> list[str]:
    if before.columns != after.columns or before.height != after.height:
        return [f"{name}: shape or columns changed"]
    return [f"{name}: column {c} changed" for c in before.columns
            if not is_stamp(c) and not before[c].equals(after[c])]


def measurement_errors(before: dict, after: dict) -> list[str]:
    errs = [f"measurements: section {k!r} changed" for k in sorted(set(before) | set(after))
            if k != "provenance" and before.get(k) != after.get(k)]
    bp, ap = before["provenance"], after["provenance"]
    errs += [f"provenance: {k!r} changed" for k in sorted(set(bp) | set(ap))
             if k not in PROV_MAY_CHANGE and bp.get(k) != ap.get(k)]
    old, new = bp["season_columns"], ap["season_columns"]
    if [c for c in new if c != NEW] != old or NEW not in new:
        errs.append(f"provenance: season_columns must gain exactly {NEW}")
    for key, path in (("season_parquet_sha256", PARQUETS["player_season_stats.parquet"]),
                      ("match_parquet_sha256", PARQUETS["player_match_stats.parquet"])):
        if ap[key] != sha256(path):
            errs.append(f"provenance: {key} is not the current file's sha256")
    return errs


def registry_errors(before: dict, after: dict) -> list[str]:
    errs = [f"registry: {k!r} changed" for k in sorted(set(before) | set(after))
            if k != "registry_version" and before.get(k) != after.get(k)]
    if before.get("registry_version") == after.get("registry_version"):
        errs.append("registry: registry_version did not change")
    return errs


def main() -> None:
    errs: list[str] = []
    for name, path in PARQUETS.items():
        errs += parquet_errors(name, pl.read_parquet(BEFORE / name), pl.read_parquet(path))
    for name, path in STAMPED.items():
        errs += stamped_errors(name, pl.read_parquet(BEFORE / name), pl.read_parquet(path))
    errs += measurement_errors(json.loads((BEFORE / "measurements_old.json").read_text()),
                               json.loads(MEASUREMENTS.read_text()))
    rb = json.loads((BEFORE / "registry.json").read_text())
    ra = json.loads(AFTER_REGISTRY.read_text())
    errs += registry_errors(rb, ra)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        print(f"FAIL: {len(errs)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    season = pl.read_parquet(PARQUETS["player_season_stats.parquet"])
    print(f"OK: both parquets gain only {NEW} ({season[NEW].is_not_null().sum()} of {season.height} "
          f"season rows have one); measurements provenance only; 0 registry changes; percentiles / "
          f"DNA / dimensions stamps only; REGISTRY_VERSION {rb['registry_version'][:12]}… → "
          f"{ra['registry_version'][:12]}…")


if __name__ == "__main__":
    main()
