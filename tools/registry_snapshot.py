"""
Dump the resolved metric registry to JSON, for registry before/after checks (plan 005 M4g2).

Writes every metric × group cell (status, reasons, notes, reliability, min_attempts, basis,
D9 fields, the spec's family and direction), the §10.10 boundary rows, the majority-triggered
cells the allow-list admits (ADR 004 §13.1), the per-group DNA, display and signature lists,
the mover verdicts (ADR 004 §13.7, plan 005 M4g3b; +inf written as "+inf"), the possession
records, the mover verdicts per (metric, candidate) and the Defending D5 status per group (ADR 004
§13.8, plan 005 M4g4d), the source and REGISTRY_VERSION. Floats are written as stored.

The dump is the "before" or "after" side of spikes/m4g2_before_after.py, which compares two
dumps without importing eleveniq. Run it on the old code before a registry change and on the
new code after it.

Usage:
  .venv/bin/python tools/registry_snapshot.py --out data/interim/m4g2_snapshot/registry.json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from eleveniq.analytics import registry


def _json_safe(x: Any) -> Any:
    """+inf / -inf as strings (the dump is written with allow_nan=False)."""
    if isinstance(x, float) and math.isinf(x):
        return "+inf" if x > 0 else "-inf"
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    return x


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
        "movers": {c: _json_safe(asdict(v)) for c, v in sorted(derivation.movers.items())},
        "possession": [asdict(v) for _, v in sorted(derivation.possession.items())],
        "candidate_movers": [_json_safe(asdict(v))
                             for _, v in sorted(derivation.candidate_movers.items())],
        "family_status": {g: asdict(derivation.defending_status(g)) for g in registry.GROUPS},
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
