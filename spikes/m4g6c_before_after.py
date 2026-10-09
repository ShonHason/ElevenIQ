"""
M4g6c STOP rule for the Passing status rename (ADR 004 §14 item 4; plan 005 M4g6b / M4g6c).

Compares the DNA outputs before (data/interim/m4g6c_snapshot/) and after the rebuild against
spikes/expected_m4g6c_flips.json, written before any code. Stops (exit 1) unless:
- player_dna.parquet and player_season_percentiles.parquet are byte-identical;
- player_dna_dimensions.parquet is identical in every column and row except score_status, which
  changes only on the expected family's rows, from the old to the new status, on exactly the
  expected number of rows and groups, and nowhere else.
Never imports eleveniq.

  .venv/bin/python spikes/m4g6c_before_after.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "data" / "interim" / "m4g6c_snapshot"
AFTER = ROOT / "data" / "processed"
EXPECTED = ROOT / "spikes" / "expected_m4g6c_flips.json"
DIMS = "player_dna_dimensions.parquet"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def errors(before: Path, after: Path, exp: dict) -> list[str]:
    out = [f"{f} changed" for f in exp["unchanged_files"] if sha(before / f) != sha(after / f)]
    a, b = pl.read_parquet(before / DIMS), pl.read_parquet(after / DIMS)
    if a.columns != b.columns or a.height != b.height:
        return out + [f"{DIMS}: shape or columns changed"]
    for c in a.columns:
        if c != "score_status" and not a[c].equals(b[c]):
            out.append(f"{DIMS}: column {c} changed")
    d = exp["dimensions"]
    changed = a.with_columns(new=b["score_status"]).filter(pl.col("score_status") != pl.col("new"))
    if changed.filter((pl.col("family") != d["family"]) | (pl.col("score_status") != d["from"])
                      | (pl.col("new") != d["to"])).height:
        out.append(f"{DIMS}: score_status changed outside {d['family']} {d['from']} -> {d['to']}")
    if changed.height != d["rows"]:
        out.append(f"{DIMS}: {changed.height} rows changed, expected {d['rows']}")
    if sorted(changed["position_group"].unique().to_list()) != d["groups"]:
        out.append(f"{DIMS}: changed groups {sorted(changed['position_group'].unique().to_list())}")
    if b.filter(pl.col("score_status") == d["from"]).height:
        out.append(f"{DIMS}: the old status {d['from']} is still present")
    return out


def main() -> None:
    exp = json.loads(EXPECTED.read_text())
    errs = errors(BEFORE, AFTER, exp)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        print(f"FAIL: {len(errs)} unexpected changes", file=sys.stderr)
        raise SystemExit(1)
    d = exp["dimensions"]
    print(f"OK: {', '.join(exp['unchanged_files'])} byte-identical; {DIMS}: only {d['rows']} "
          f"{d['family']} rows {d['from']} -> {d['to']} ({', '.join(d['groups'])})")


if __name__ == "__main__":
    main()
