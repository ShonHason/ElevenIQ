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


def test_lower_better_cells_show_the_direction_adjusted_percentile(docs):
    """W0 decision 1 (user, 2026-10-10): a lower-better metric's number, phrase, bar length and
    bin all read the shown (direction-adjusted) percentile; checked on every such cell."""
    from eleveniq.export import words as W
    n = 0
    for p, d in docs.items():
        if not p.startswith("players/"):
            continue
        metas = docs[f"groups/{d['identity']['group']}.json"]["metrics"]
        for meta, c in zip(metas, d["metrics"]):
            if meta["direction"] != "lower_better":
                continue
            for scope in ("local", "global"):
                s = c[scope]
                if s["pct"] is None:
                    continue
                n += 1
                assert s["pct_text"] == X.format_pct(s["pct"]), p
                assert s["phrase"] == W.pct_words(s["pct"])[1], p
                spec = X.SPECS_BY_COLUMN[meta["metric"]]
                assert s["bin"] == X.percentile_bin(spec, False, 100.0 - s["pct"]), p
    assert n > 20


def test_kante_stands_out_on_his_defensive_volumes(docs):
    """User's check: Kanté's What stands out shows tackles attempted, interceptions, blocks and
    ball recoveries."""
    got = {h["metric"] for h in docs["players/3961-22-2-27.json"]["stand_out"]}
    assert {"tackles_attempted_p90", "interceptions_p90", "blocks_p90", "ball_recoveries_p90"} <= got


GAP_EXPECTED = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "gap-order.expected.json"


def _gap_expectations(docs: dict) -> dict[str, list[str]]:
    players = {p: d for p, d in docs.items() if p.startswith("players/") and d["dna"]}
    out = {}
    for (pa, a), (pb, b) in combinations(sorted(players.items()), 2):
        g = a["identity"]["group"]
        if g == b["identity"]["group"]:
            out[f"{a['identity']['key']}|{b['identity']['key']}"] = X.gap_order(
                docs[f"groups/{g}.json"]["metrics"], a["metrics"], b["metrics"])
    return out


def test_gap_order_expectations_for_the_browser_are_current(docs):
    """Compare mode's top 6 (ADR 005 §1 exception): Python's display.gap_order on every same-group
    fixture pair, committed for the browser's own test (frontend/src/lib/gapOrder.test.ts).
    Regenerate with UPDATE_GAP_ORDER=1 after a fixture change."""
    import os
    want = _gap_expectations(docs)
    if os.environ.get("UPDATE_GAP_ORDER"):
        GAP_EXPECTED.write_text(json.dumps(want, indent=1, sort_keys=True) + "\n")
    assert json.loads(GAP_EXPECTED.read_text()) == want


# ── Display round (user, 2026-10-10): bands, units, totals ──────────────────────────

def _cells(docs):
    for p, d in docs.items():
        if not p.startswith("players/"):
            continue
        metas = docs[f"groups/{d['identity']['group']}.json"]["metrics"]
        for meta, c in zip(metas, d["metrics"]):
            yield p, d, meta, c


# The user's bands (literals: the spec, not read from the code).
BANDS = [(95, "Top 5%"), (90, "Top 10%"), (75, "Top 25%"), (60, "Above average"),
         (40, "Around average"), (25, "Below average"), (10, "Bottom 25%"), (5, "Bottom 10%"),
         (0, "Bottom 5%")]


def test_every_cell_band_agrees_with_its_number(docs):
    n = 0
    for p, _, meta, c in _cells(docs):
        for scope in ("local", "global"):
            s = c[scope]
            if s["pct"] is None:
                assert s["band"] is None and s["note"] is None, p
                continue
            n += 1
            # The band reads the number the popover shows (final text pass).
            shown = {"<1": 0, ">99": 100}.get(s["pct_text"]) if s["pct_text"] in ("<1", ">99") \
                else int(s["pct_text"])
            n_ = s["n"]
            ends = ("Highest in league", "Lowest in league")
            if meta["direction"] == "lower_better":
                ends = ends[::-1]
            member = 0 < s["pct"] < 100           # outsider rows (0 / 100) take no end label
            if scope == "local" and n_ and member and s["pct"] >= 100 * (n_ - 0.5) / n_ - 1e-6:
                want = ends[0]            # the single highest shown row (user, 2026-10-10)
            elif scope == "local" and n_ and member and s["pct"] <= 100 * 0.5 / n_ + 1e-6:
                want = ends[1]
            elif meta["uncoloured"]:            # hollow bar = neutral words (user, 2026-10-10)
                want = ("Around average" if 40 <= shown <= 60
                        else "Higher than most" if shown > 60 else "Lower than most")
            else:
                want = next(label for lo, label in BANDS if shown >= lo)
            assert s["band"] == want, (p, meta["metric"], s["pct"], s["band"])
            assert "percentile among" in s["note"], p                      # the number, in the popover
    assert n > 1000


def test_every_value_shows_its_unit(docs):
    """per 90 / per shot from the metric definition, or a % in the value text itself."""
    for p, _, meta, c in _cells(docs):
        if c["value"] is None:
            continue
        assert meta["unit"] or c["value_text"].endswith("%"), (p, meta["metric"])
        if meta["metric"].endswith("_p90"):
            assert meta["unit"] == "per 90"


def test_every_metric_has_a_definition(docs):
    for g in ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST", "GK"):
        for m in docs[f"groups/{g}.json"]["metrics"]:
            assert m["definition"] and m["definition"].endswith("."), (g, m["metric"])


@pytest.mark.integration
def test_totals_and_counts_trace_to_the_season_parquet(docs):
    """Season totals and made / attempted counts equal the season parquet's own columns."""
    import polars as pl
    season = {(r["player_id"], r["team_id"], r["competition_id"]): r
              for r in pl.read_parquet(B.SEASON_PATH).iter_rows(named=True)}
    specs = X.SPECS_BY_COLUMN
    n = 0
    for p, d, meta, c in _cells(docs):
        i = d["identity"]
        row = season[(i["player_id"], i["team_id"], i["competition_id"])]
        spec = specs[meta["metric"]]
        if spec.ratio:
            r = spec.ratio
            den = sum(row[t] for t in r.denominator_plus) - sum(row[t] for t in r.denominator_minus)
            dec = r.numerator in ("xg", "np_xg")                       # sums: 2 decimals
            want = f"{X.format_count(row[r.numerator], dec)} / {X.format_count(den)}"
        else:
            want = f"{X.format_count(row[spec.count_column], not spec.integer_count)} total"
        assert c["total_text"] == want, (p, meta["metric"])
        n += 1
    for p, d in docs.items():
        if p.startswith("players/"):
            i = d["identity"]
            row = season[(i["player_id"], i["team_id"], i["competition_id"])]
            got = {s["key"]: s["text"] for s in i["summary"]}
            assert got["appearances"] == X.format_count(row["appearances"]), p
            assert got["goals"] == X.format_count(row["goals"]) and got["shots"] == X.format_count(row["shots"])
    assert n > 1000


def test_totals_and_summary_literals_for_kante(docs):
    """Pinned literals (the user's example: progressive passes "4.89 per 90 · 176 total")."""
    d = docs["players/3961-22-2-27.json"]
    order = docs["labels.json"]["metric_order"]
    row = d["metrics"][order.index("progressive_passes_p90")]
    assert (row["value_text"], row["total_text"]) == ("4.89", "176 total")
    s = {x["key"]: (x["text"], x["label"]) for x in d["identity"]["summary"]}
    assert s["appearances"] == ("37", "appearances") and s["minutes_played"] == ("3240", "minutes")
    assert s["goals"] == ("1", "goal") and s["xg"][1] == "xG" and "." in s["xg"][0]


TOP_BOTTOM = ("top ", "bottom ")


def test_no_hollow_bar_cell_carries_a_top_or_bottom_label(docs):
    """Hollow bar = neutral words (user, 2026-10-10): every uncoloured cell, in the table, What
    stands out and the neighbour reasons, reads higher than most / around average / lower than
    most; number, phrase and bar agree."""
    n = 0
    for p, _, meta, c in _cells(docs):
        for scope in ("local", "global"):
            s = c[scope]
            if s["pct"] is None:
                continue
            assert (s["bin"] == "uncoloured") == meta["uncoloured"], (p, meta["metric"])
            if meta["uncoloured"]:
                n += 1
                assert not s["band"].lower().startswith(TOP_BOTTOM), (p, meta["metric"], s["band"])
                assert not s["phrase"].startswith(TOP_BOTTOM), (p, meta["metric"], s["phrase"])
    assert n > 100
    for p, d in docs.items():
        if not p.startswith("players/"):
            continue
        metas = {m["metric"]: m for m in docs[f"groups/{d['identity']['group']}.json"]["metrics"]}
        for h in d["stand_out"]:
            if metas[h["metric"]]["uncoloured"]:
                assert h["bin"] == "uncoloured" and not h["phrase"].startswith(TOP_BOTTOM), (p, h)
        for nb in (d["neighbours"] or {}).get("items", []):
            for r in nb["most_different"] + nb["most_alike"]:
                if metas[r["metric"]]["uncoloured"]:
                    for ph in (r["a_phrase"], r["b_phrase"]):
                        assert not ph.startswith(TOP_BOTTOM), (p, r)


SHOT_TYPES = {"shots_p90", "np_shots_p90", "shots_on_target_p90", "np_shots_on_target_p90"}


def test_stand_out_has_at_most_one_shots_metric(docs):
    """No near-duplicates (user, 2026-10-10): Benzema's What stands out lists at most one
    shots-type metric, and no player lists two."""
    benzema = docs["players/19677-220-11-27.json"]
    assert len([h for h in benzema["stand_out"] if h["metric"] in SHOT_TYPES]) == 1
    for p, d in docs.items():
        if p.startswith("players/"):
            assert len([h for h in d["stand_out"] if h["metric"] in SHOT_TYPES]) <= 1, p


def test_family_definitions_cover_scored_families(docs):
    """Every scored family on a Profile shape has its one-line definition in the group file; a
    withheld family has none (it shows its reason)."""
    for p, d in docs.items():
        if not p.startswith("players/") or not d["dna"]:
            continue
        defs = docs[f"groups/{d['identity']['group']}.json"]["family_definitions"]
        for dim in d["dna"]["dimensions"]:
            if dim["status"] == "ok":
                assert dim["family"] in defs and defs[dim["family"]], (p, dim["family"])
            else:
                assert dim["family"] not in defs, (p, dim["family"])


def test_gems_orders_are_by_score_and_withheld_families_never_order(docs):
    """Gem Finder (W5): each scored family's order lists every row once, highest score first,
    equal scores by folded name; unscored rows last. Withheld families have no order. Rows are
    the group's profiled players; minutes tiers agree with the player file's minutes."""
    from eleveniq.export import display as X
    from eleveniq.export import words as W
    for g in ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST"):
        gem = docs[f"gems/{g}.json"]
        players = {d["identity"]["key"]: d for p, d in docs.items()
                   if p.startswith("players/") and d["identity"]["group"] == g and d["dna"]}
        assert sorted(r["key"] for r in gem["rows"]) == sorted(players)
        withheld = {f["family"] for f in gem["families"] if f["definition"] is None}
        assert withheld and not withheld & set(gem["order"])
        assert set(gem["order"]) == {f["family"] for f in gem["families"]} - withheld
        for fam, order in gem["order"].items():
            assert sorted(order) == list(range(len(gem["rows"])))
            keys = []
            for i in order:
                r = gem["rows"][i]
                sc = r["scores"][fam]["score"]
                dim = next(x for x in players[r["key"]]["dna"]["dimensions"] if x["family"] == fam)
                assert sc == dim["score"]
                keys.append((sc is None, -(sc or 0), X.fold(r["display_name"])))
            assert keys == sorted(keys), (g, fam)
        for r in gem["rows"]:
            mins = players[r["key"]]["identity"]["minutes"]
            assert r["minutes_tiers"] == [str(t) for t in W.GEMS_MINUTES_TIERS if mins >= t]
    # "Players like X": every neighbour of a fixture anchor is a row of the anchor's Gem file.
    kante = docs["players/3961-22-2-27.json"]
    rows = {r["key"] for r in docs["gems/DM-CM.json"]["rows"]}
    assert {n["key"] for n in kante["neighbours"]["items"]} <= rows


def test_methodology_sections_present(docs):
    m = docs["labels.json"]["methodology"]
    titles = [s["title"] for s in m["sections"]]
    assert "Defensive rate" in titles and all(s["paragraphs"] for s in m["sections"])
    rate = next(s for s in m["sections"] if s["title"] == "Defensive rate")["paragraphs"][0]
    assert "does not remove team effects" in rate                   # the approved sentence
    text = " ".join(p for s in m["sections"] for p in s["paragraphs"]).lower()
    for word in ("adjusted rate", "corrected", "overall rating of"):
        assert word not in text


def test_completion_vs_expected_row_and_passing_score(docs):
    """Completion above expected (plan 006 pre-registration): every outfield row has the table
    row; the Passing vertex is its league percentile in a group whose spec passed the check
    (the Methodology says strikers), withheld for that reason elsewhere, where the row is
    hollow with neutral words. Value text = signed points; band agrees with the number."""
    passed = {g for g in ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
              if "Passing" in docs[f"groups/{g}.json"]["family_definitions"]}
    assert passed == {"ST"}                         # the Methodology's claim
    n = 0
    for p, d in docs.items():
        if not p.startswith("players/"):
            continue
        g = d["identity"]["group"]
        c = d["completion_vs_expected"]
        if g == "GK":
            assert c is None
            continue
        meta = docs[f"groups/{g}.json"]["completion_vs_expected"]
        assert meta["uncoloured"] == (g not in passed) and meta["family"] == "Passing"
        if c["value"] is not None:
            assert c["value_text"] == f"{c['value']:+.1f}", p
        loc = c["local"]
        if loc["pct"] is not None:
            assert (loc["bin"] == "uncoloured") == (g not in passed), p
            if g not in passed:
                assert not loc["band"].lower().startswith(("top", "bottom")), p
        if d["dna"]:
            dim = next(x for x in d["dna"]["dimensions"] if x["family"] == "Passing")
            if g in passed:
                n += 1
                assert dim["members"] == ["completion_vs_expected"] and dim["score"] == loc["pct"], p
            else:
                assert dim["score"] is None and dim["status"] == "pass_mix_possession_failed", p
    assert n >= 2                                   # the fixture's profiled strikers
