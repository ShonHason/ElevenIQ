"""
Dump the resolved metric registry to JSON, for registry before/after checks (plan 005 M4g2).

Writes every metric × group cell (status, reasons, notes, reliability, min_attempts, basis,
D9 fields, the spec's family and direction), the §10.10 boundary rows, the majority-triggered
cells the allow-list admits (ADR 004 §13.1), the per-group DNA, display and signature lists,
the source and REGISTRY_VERSION. Floats are written as stored.

The dump is the "before" or "after" side of spikes/m4g2_before_after.py, which compares two
dumps without importing eleveniq. Run it on the old code before a registry change and on the
new code after it.

Usage:
  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g2_snapshot/registry.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from eleveniq.analytics import registry


def snapshot(derivation: registry.Derivation = registry.REGISTRY,
             specs: Sequence[registry.MetricSpec] = registry.SPECS) -> dict[str, Any]:
    by_col = {s.column: s for s in specs}
    cells = []
    for (col, grp) in sorted(derivation.cells, key=lambda k: (k[0], registry.GROUPS.index(k[1]))):
        c = asdict(derivation.cell(col, grp))
        c["family"] = by_col[col].family
        c["direction"] = by_col[col].direction
        c["unit"] = by_col[col].unit
        cells.append(c)
    return {
        "source": derivation.source,
        "registry_version": derivation.version,
        "cells": cells,
        "boundary": [asdict(b) for b in derivation.boundary],
        "majority_admitted": [asdict(a) for a in derivation.majority_admitted],
        "groups": {g: {"dna": list(derivation.dna_metrics(g)),
                       "display": list(derivation.display_metrics(g)),
                       "signature": list(derivation.signature_traits(g))}
                   for g in registry.GROUPS},
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        json.dump(snapshot(), f, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    print(f"wrote {args.out} (registry {registry.REGISTRY_VERSION[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
