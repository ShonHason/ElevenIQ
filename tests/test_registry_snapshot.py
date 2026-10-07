"""Fast tests for tools/registry_snapshot.py (plan 005 M4g2): the dump that feeds the
before/after registry comparison must be complete, strict JSON and match the registry."""
from __future__ import annotations

import json

from eleveniq.analytics import registry as R
from tools import registry_snapshot as S


def test_snapshot_covers_every_cell_and_group_list_matches_registry():
    snap = S.snapshot()
    assert len(snap["cells"]) == len(R.REGISTRY.cells)
    assert snap["registry_version"] == R.REGISTRY.version
    for g in R.GROUPS:
        assert snap["groups"][g]["dna"] == list(R.REGISTRY.dna_metrics(g))
        assert snap["groups"][g]["display"] == list(R.REGISTRY.display_metrics(g))
    # Majority-admitted cells are dumped (nested dataclasses become dicts).
    assert len(snap["majority_admitted"]) == len(R.REGISTRY.majority_admitted)
    assert all(c.get("family") and c.get("direction") for c in snap["cells"])


def test_main_writes_strict_json(tmp_path):
    out = tmp_path / "sub" / "registry.json"
    assert S.main(["--out", str(out)]) == 0
    # allow_nan=False upstream: re-parsing with a NaN-rejecting hook must succeed.
    def _no_nan(x):
        raise ValueError(x)
    data = json.loads(out.read_text(), parse_constant=_no_nan)
    assert data["source"] == R.REGISTRY.source
