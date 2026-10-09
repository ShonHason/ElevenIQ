"""
The committed web fixture (plan 006 W1d; ADR 005 §7): tests/fixtures/web/, written by
`python -m eleveniq.export.build_export --fixture` and regenerated only with --update-fixture
(which prints the diff summary; call the change out in the commit, as for the golden pages).

Fast (the fixture only): every file validates against contract/v1; the manifest lists every
file with its sha256; every metric cell has a number or a status text; the near-gate flag only
on CB / FB-WB rows with 900–1,350 nominal minutes; Kanté's neighbours are in the fixture
(Playwright flow 1); compare mode's gap order (option c) excludes the ineligible metrics on
real fixture pairs, including a pair where an excluded metric's gap would otherwise rank.
Integration: the fixture equals a fresh build of the fixture rows (it is current).
"""
from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path

import pytest

from eleveniq.export import build_export as B
from eleveniq.export import display as X

FIX = B.FIXTURE_DIR


def _load() -> dict[str, dict]:
    return {p.relative_to(FIX).as_posix(): json.loads(p.read_text())
            for p in B.managed_files(FIX) if p.name != "manifest.json"}


@pytest.fixture(scope="module")
def docs():
    if not (FIX / "manifest.json").exists():
        pytest.fail("tests/fixtures/web is missing: run build_export --fixture")
    return _load()


def test_fixture_validates_against_the_contract(docs):
    B.validate(docs)
    B.validate({"manifest.json": json.loads((FIX / "manifest.json").read_text())})


def test_manifest_lists_every_file_with_its_sha256():
    man = json.loads((FIX / "manifest.json").read_text())
    assert man["exporter_commit"] == "fixture"
    listed = {f["path"]: f["sha256"] for f in man["files"]}
    on_disk = {p.relative_to(FIX).as_posix(): p for p in B.managed_files(FIX)
               if p.name != "manifest.json"}
    assert set(listed) == set(on_disk)
    for path, sha in listed.items():
        assert hashlib.sha256(on_disk[path].read_bytes()).hexdigest() == sha, path


def test_fixture_has_the_flow_rows_and_kantes_neighbours(docs):
    for r in B.FIXTURE_ROWS:
        assert f"players/{r[0]}-{r[1]}-{r[2]}-27.json" in docs
    kante = docs["players/3961-22-2-27.json"]
    assert kante["identity"]["display_name"] == "N'Golo Kanté"
    for n in kante["neighbours"]["items"]:
        assert f"players/{n['key']}.json" in docs
    assert docs["players/4926-220-11-27.json"]["identity"]["display_name"] == "Isco"


def test_every_metric_cell_has_a_number_or_a_status_text(docs):
    for p, d in docs.items():
        if not p.startswith("players/"):
            continue
        cells = list(d["metrics"]) + (d["second_group"]["metrics"] if d["second_group"] else [])
        for c in cells:
            for scope in ("local", "global"):
                assert (c[scope]["pct_text"] is None) != (c[scope]["status_text"] is None), p


def test_near_gate_only_cb_fbwb(docs):
    for p, d in docs.items():
        if p.startswith("players/") and d["identity"]["near_gate"]:
            i = d["identity"]
            assert i["group"] in ("CB", "FB-WB") and 900 - 1e-6 <= i["nominal_minutes"] < 1350, p


def test_gap_order_excludes_ineligible_metrics_on_fixture_pairs(docs):
    """Option c (user, 2026-10-09) on real rows: for every same-group pair of the fixture, the
    top 6 holds only eligible metrics both rows show, ordered by gap; at least one pair has an
    excluded metric whose gap is larger than the 6th listed one (so the exclusion bites), and
    that metric is still in the full table."""
    players = {p: d for p, d in docs.items() if p.startswith("players/") and d["dna"]}
    bites = 0
    for (pa, a), (pb, b) in combinations(sorted(players.items()), 2):
        g = a["identity"]["group"]
        if g != b["identity"]["group"]:
            continue
        metas = docs[f"groups/{g}.json"]["metrics"]
        top = X.gap_order(metas, a["metrics"], b["metrics"])
        assert len(top) == 6, (pa, pb)
        meta = {m["metric"]: m for m in metas}
        assert all(meta[m]["gap_eligible"] for m in top)
        idx = {m["metric"]: i for i, m in enumerate(metas)}

        def gap(m: str) -> float | None:
            x, y = a["metrics"][idx[m]]["local"]["pct"], b["metrics"][idx[m]]["local"]["pct"]
            return None if x is None or y is None else abs(x - y)
        sixth = gap(top[-1])
        assert [gap(m) for m in top] == sorted((gap(m) for m in top), reverse=True)
        for m in meta:
            if not meta[m]["gap_eligible"] and meta[m]["registry_status"] != "signature" \
                    and gap(m) is not None and gap(m) > sixth:
                bites += 1
                assert m not in top
                assert a["metrics"][idx[m]]["local"]["pct_text"] is not None     # full table
    assert bites > 0


@pytest.mark.integration
def test_fixture_is_current():
    """A fresh build of the fixture rows equals the committed fixture; on a data or contract
    change rerun `build_export --update-fixture` and call the diff out in the commit."""
    if not all(p.exists() for p in (B.SEASON_PATH, B.PERCENTILES_PATH, B.DNA_PATH,
                                    B.DIMENSIONS_PATH, B.POSITIONS_PATH, B.MATCH_PATH)):
        pytest.skip("pipeline outputs missing")
    inp = B.load_inputs()
    fresh = {p: B.dumps(d) for p, d in B.build(inp, keys=B.fixture_keys(inp)).items()}
    changes = B.diff_summary(FIX, fresh)
    assert not changes, "fixture is stale: " + "; ".join(changes[:10])
