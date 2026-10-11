"""
build_export.build on a synthetic DM-CM world (plan 006 W1c fix round; no data/ needed):
24 complete rows, a mover with two clubs, a partial row, a goalkeeper, a below-gate row and a
second-group (CB) row whose zero-heavy metric is 0.

- every document validates against contract/v1;
- export-wide invariant (user, 2026-10-09): every metric cell scope carries a number or a
  status text, never neither (the zero-tie bug class);
- search-index profiles, the neighbour list, the style map, the mover, signature traits;
- write() deletes only the managed files a previous run left, only inside the export
  directory, and touches nothing else (user, 2026-10-09);
- compare mode's gap order (option c): a team-possession / long-pass metric with the largest
  gap is out of the top 6 but in the full table.
"""
from __future__ import annotations

import datetime as dt
import random
import sys

import polars as pl
import pytest

from eleveniq.analytics import dna as D
from eleveniq.analytics import registry as R
from eleveniq.export import words as W_
from eleveniq.analytics.dna import dna_specs, split_weights
from eleveniq.export import build_export as B
from eleveniq.export import display as X

G = "DM-CM"
SPECS = dna_specs(R.REGISTRY, R.SPECS, G)
DNA_METRICS = [s.column for s in SPECS]
WEIGHTS = split_weights(SPECS, set(DNA_METRICS))
SIG = {s.count_column for s in R.SPECS if s.count_column}
# Every season column a metric's total reads: per-90 counts and ratio terms (display round).
COUNTS = SIG | {t for s in R.SPECS if s.ratio for t in s.ratio.terms} | {
    "appearances", "goals", "assists", "xg", "shots"}
STAMPS = {"contract_version": "1.0.0", "registry_version": R.REGISTRY_VERSION,
          "measurement_source": "statsbomb", "source": "statsbomb", "season_ids": [27],
          **{f"{n}_sha256": "0" * 64 for n in ("season_parquet", "percentiles_parquet",
                                              "dna_parquet", "positions_parquet", "match_parquet",
                                              "pass_mix_parquet")}}


def _key(pid: int, tid: int) -> tuple:
    return (pid, tid, 2, 27, "statsbomb")


def _world():
    rng = random.Random(11)
    season, pct, dna, dims, pos, match = [], [], [], [], [], []
    rows = [(_key(100 + i, 10 + i % 4), "dna", "complete") for i in range(24)]
    rows += [(_key(200, 10), "dna", "complete"), (_key(200, 11), "dna", "complete"),   # mover
             (_key(300, 12), "dna", "partial"),
             (_key(400, 13), "gk", None), (_key(500, 13), "below", None)]
    for k, kind, status in rows:
        group = "GK" if kind == "gk" else G
        minutes = 400.0 if kind == "below" else 1800.0
        season.append({"player_id": k[0], "team_id": k[1], "competition_id": 2, "season_id": 27,
                       "source": "statsbomb", "player_name": f"Player {k[0]}",
                       "player_nickname": " Nick " if k[0] == 100 else None,
                       "team_name": f"Team {k[1]}", "position_group": group,
                       "second_position_group": "CB" if k[0] == 101 else None,
                       "minutes_played": minutes, "minutes_nominal_equiv": minutes * 0.95,
                       "meets_min_minutes": kind != "below", "in_percentile_pool": True,
                       "team_open_play_passes_while_on": 1000 if kind != "below" else 0,
                       **{c: 3 for c in COUNTS},
                       # the compare-mode defensive rate's season counts and exposure
                       "opponent_open_play_passes_while_on": 900 if kind != "below" else 0,
                       **{c: 9 for c in ("tackles_attempted", "interceptions", "ball_recoveries",
                                          "pressures", "blocks", "clearances")}})
        pos.append({**dict(zip(B.KEY, k)), "position": "Goalkeeper" if kind == "gk"
                    else "Center Defensive Midfield", "minutes": minutes / 2})
        if kind != "gk":
            pos.append({**dict(zip(B.KEY, k)), "position": "Center Midfield", "minutes": minutes / 2})
        else:
            pos[-1]["minutes"] = minutes
        month = 1 if k == _key(200, 11) else 8
        match.append({**dict(zip(B.KEY, k)), "match_date": dt.date(2016 if month == 1 else 2015, month, 9),
                      "minutes_played": 90.0})
        vec = {m: rng.uniform(45, 55) for m in DNA_METRICS}
        vec["op_key_passes_p90"] = rng.uniform(5, 95)
        vec["np_xg_p90"] = rng.uniform(20, 80)
        for role, g in (("primary", group), *((("second", "CB"),) if k[0] == 101 else ())):
            for s in R.SPECS:
                cell = R.REGISTRY.cell(s.column, g) if g in B.GROUPS else None
                scored = kind == "dna"
                p = vec.get(s.column, rng.uniform(5, 95)) if scored else None
                value = 0.0 if (role == "second" and s.column == "box_entries_carry_p90") else 1.5
                st = "ok" if scored else ("goalkeeper" if kind == "gk" else "below_gate")
                pct.append({**dict(zip(B.KEY, k)), "position_group": g, "cohort_role": role,
                            "metric": s.column, "value": value,
                            "percentile_status": "scored" if scored else st,
                            "pct_local": p, "n_local": 30 if scored else None, "status_local": st,
                            "pct_global": p, "n_global": 120 if scored else None, "status_global": st,
                            "basis": "local" if scored else None,
                            "registry_status": cell.status if cell else None})
        if kind == "dna":
            for s in SPECS:
                missing = status == "partial" and s.column == "np_xg_p90"
                dna.append({**dict(zip(B.KEY, k)), "position_group": G, "metric": s.column,
                            "family": s.family, "direction": s.direction, "value": 1.5,
                            "pct_local": None if missing else vec[s.column], "n_local": 30,
                            "metric_status": "below_min_attempts" if missing else "ok",
                            "z": None, "z_median": None, "z_scale": None, "z_scale_method": None,
                            "z_status": "ok", "weight": None if missing else WEIGHTS[s.column],
                            "dna_status": status})
            held = D.withheld_for(G)
            for fam in sorted({s.family for s in SPECS}):
                ok = fam not in held
                dims.append({**dict(zip(B.KEY, k)), "position_group": G, "family": fam,
                             "score": 50.0 if ok else None,
                             "score_status": "ok" if ok else held[fam],
                             "members": [s.column for s in SPECS if s.family == fam] if ok else [],
                             "dna_status": status})
    return B.Inputs(pl.DataFrame(season), pl.DataFrame(pct), pl.DataFrame(dna),
                    pl.DataFrame(dims), pl.DataFrame(pos), pl.DataFrame(match), STAMPS)


@pytest.fixture(scope="module")
def docs():
    return B.build(_world(), style_groups=(G,))


def test_every_document_validates(docs):
    B.validate(docs)


def test_every_metric_cell_has_a_number_or_a_status_text(docs):
    """Export-wide invariant (user, 2026-10-09): never neither."""
    n = 0
    for path, d in docs.items():
        if not path.startswith("players/"):
            continue
        cells = list(d["metrics"]) + (d["second_group"]["metrics"] if d["second_group"] else [])
        for c in cells:
            for scope in ("local", "global"):
                s = c[scope]
                assert (s["pct_text"] is None) != (s["status_text"] is None), (path, s)
                n += 1
    assert n > 1000


def test_the_zero_tie_says_so(docs):
    d = docs["players/101-11-2-27.json"]
    i = [s.column for s in R.SPECS].index("box_entries_carry_p90")
    c = d["second_group"]["metrics"][i]
    assert c["zero_tie"] and c["local"]["pct_text"] is None
    assert c["local"]["status_text"] == "tied at zero, not a rank"


def test_search_index_profiles_and_display_names(docs):
    e = {x["key"]: x for x in docs["search-index.json"]["entries"]}
    assert e["100-10-2-27"]["display_name"] == "Nick" and e["100-10-2-27"]["profile"] == "dna"
    assert e["400-13-2-27"]["profile"] == "gated" and e["500-13-2-27"]["profile"] == "below_gate"
    assert "nick" in e["100-10-2-27"]["search_key"] and "player 100" in e["100-10-2-27"]["search_key"]


def test_neighbours_style_map_and_mover(docs):
    a = docs["players/200-10-2-27.json"]
    keys = [n["key"] for n in a["neighbours"]["items"]]
    assert len(keys) == 10 and "200-11-2-27" not in keys          # own rows left out
    assert a["neighbours"]["n_candidates"] == 25                    # 24 + the partial row
    sm = docs[f"style-maps/{G}.json"]
    assert len(sm["points"]) == 26 and [p["key"] for p in sm["partial"]] == ["300-12-2-27"]
    assert [m["stints"] for m in sm["movers"]] == [["200-10-2-27", "200-11-2-27"]]
    assert sm["played_for"]["200-11-2-27"][0]["months"] == "Aug"
    assert docs["players/400-13-2-27.json"]["dna"] is None
    assert docs["players/400-13-2-27.json"]["identity"]["group_text"] == "goalkeepers"


def test_signature_traits_and_withheld_texts(docs):
    d = docs["players/100-10-2-27.json"]
    assert [t["holder_text"] for t in d["signature_traits"]] and all(
        t["holder_text"] in ("holder", "non-holder") for t in d["signature_traits"])
    dims = {x["family"]: x for x in d["dna"]["dimensions"]}
    # Passing follows completion above expected (plan 006 pre-registration): no pass data in
    # this world, so the check fails and Passing stays withheld for that reason.
    p = dims["Passing"]
    assert p["display_score"] is True and p["score"] is None and p["status"] == W_.PASS_MIX_FAILED
    assert p["withheld_short"] == W_.PASS_MIX_FAILED_TEXT.short
    assert not any(x["display_score"] for f, x in dims.items() if f != "Passing")
    assert dims["Defending"]["withheld_full"].endswith("; underpowered")
    assert dims["Defending"]["defending_note"].startswith("Defending scores are withheld")


def test_missing_percentile_rows_raise():
    inp = _world()
    inp = B.Inputs(inp.season, inp.pct.filter(pl.col("player_id") != 105), inp.dna, inp.dims,
                   inp.positions, inp.match, inp.stamps)
    with pytest.raises(B.ExportError, match="percentile rows missing"):
        B.build(inp, keys=[_key(105, 11)], style_groups=())


# ── write(): only managed files, only inside the export directory ──────────────────

def test_write_removes_stale_managed_files_and_nothing_else(docs, tmp_path):
    out = tmp_path / "export" / "v1"
    outside = tmp_path / "export" / "keep.json"
    (out / "players").mkdir(parents=True)
    outside.write_text("{}")
    stale = out / "players" / "999-1-2-27.json"
    stale.write_text("{}")
    note = out / "players" / "README.txt"                  # not a managed name
    note.write_text("x")
    other = out / "frontend-cache" / "a.json"              # not a managed directory
    other.parent.mkdir()
    other.write_text("{}")
    sizes = B.write(docs, out, "fixture")
    assert not stale.exists()
    assert outside.read_text() == "{}" and note.read_text() == "x" and other.read_text() == "{}"
    on_disk = {p.relative_to(out).as_posix() for p in B.managed_files(out)}
    assert on_disk == set(sizes)


def test_the_path_guard_refuses_a_file_outside(tmp_path):
    out = tmp_path / "v1"
    out.mkdir()
    with pytest.raises(B.ExportError, match="outside the export directory"):
        B._inside(out, out / ".." / "x.json")


# ── Compare mode's gap order (ADR 005 §1 exception; option c) ──────────────────────

def test_a_team_possession_or_long_pass_gap_is_out_of_the_top_6_but_in_the_full_table(docs):
    metas = docs[f"groups/{G}.json"]["metrics"]
    order = [m["metric"] for m in metas]
    biased = next(m["metric"] for m in metas
                  if not m["gap_eligible"] and m["metric"] != "long_pass_completion_pct"
                  and m["registry_status"] != "signature")
    a = [dict(c, local=dict(c["local"])) for c in docs["players/102-12-2-27.json"]["metrics"]]
    b = [dict(c, local=dict(c["local"])) for c in docs["players/103-13-2-27.json"]["metrics"]]
    for m, pa in (("long_pass_completion_pct", 1.0), (biased, 2.0)):
        a[order.index(m)]["local"]["pct"] = pa
        b[order.index(m)]["local"]["pct"] = 99.0          # the two largest gaps of the pair
    top = X.gap_order(metas, a, b)
    assert len(top) == 6
    assert "long_pass_completion_pct" not in top and biased not in top
    # The full table is every metric of the group in registry order, these two included,
    # each with its own percentile text in the player file.
    full = docs["players/103-13-2-27.json"]["metrics"]
    for m in ("long_pass_completion_pct", biased):
        assert m in order and full[order.index(m)]["local"]["pct_text"] is not None


def test_gap_order_fixed_expectations():
    metas = [{"metric": m, "order": i, "direction": d, "gap_eligible": e}
             for i, (m, d, e) in enumerate([("a", "higher_better", True), ("b", "lower_better", True),
                                            ("c", "style", True), ("lp", "higher_better", False),
                                            ("d", "higher_better", True), ("e", "higher_better", True)])]
    row = lambda *ps: [{"local": {"pct": p}} for p in ps]  # noqa: E731
    a = row(50, 10, 40, 1, 50, None)
    b = row(70, 30, 90, 99, 50, 90)
    # gaps of the shown percentiles: a 20, b 20, c 50, lp 98 (not eligible), d 0, e no pct
    assert X.gap_order(metas, a, b) == ["c", "a", "b", "d"]


def test_an_unscored_zero_keeps_its_status_text_not_the_tie():
    """A below-gate row at 0 on a zero-heavy metric is not "tied at zero" (code-reviewer)."""
    base = {"player_id": 1, "team_id": 2, "competition_id": 2, "season_id": 27,
            "source": "statsbomb", "position_group": "CB", "registry_status": "display",
            "basis": None}
    rows = {"box_entries_carry_p90": {**base, "value": 0.0, "pct_local": None, "n_local": None,
                                      "status_local": "below_gate", "pct_global": None,
                                      "n_global": None, "status_global": "below_gate"}}
    c = B.metric_row(rows, "box_entries_carry_p90")
    assert c["zero_tie"] is False
    assert c["local"]["status_text"] == "below the minutes gate" and c["local"]["pct_text"] is None


def test_fixture_diff_summary_lists_added_removed_and_changed(docs, tmp_path):
    out = tmp_path / "web"
    B.write(docs, out, "fixture")
    blobs = {p: B.dumps(d) for p, d in docs.items()}
    assert B.diff_summary(out, blobs) == []                          # unchanged
    first = sorted(p for p in blobs if p.startswith("players/"))[0]
    changed = dict(blobs)
    changed[first] = blobs[first].replace(b'"stamps"', b'"stamps" ', 1)
    del changed["labels.json"]
    changed["players/1-1-1-27.json"] = b"{}\n"
    got = B.diff_summary(out, changed)
    assert got == ["+ players/1-1-1-27.json", "- labels.json", f"~ {first}"]
    assert B.diff_summary(tmp_path / "missing", blobs)[0].startswith("+ ")


def test_fixture_keys_add_the_anchors_neighbours_sorted_and_unique(monkeypatch):
    rows = ((200, 10, 2), (200, 11, 2), (400, 13, 2))
    monkeypatch.setattr(B, "FIXTURE_ROWS", rows)
    monkeypatch.setattr(B, "FIXTURE_NEIGHBOURS_OF", (200, 10, 2))
    inp = _world()
    keys = B.fixture_keys(inp)
    assert keys == sorted(set(keys))
    assert all(k[3] == 27 and k[4] == "statsbomb" for k in keys)
    for r in rows:
        assert (*r, 27, "statsbomb") in keys
    anchor = B.build(inp, keys=[_key(200, 10)], style_groups=())["players/200-10-2-27.json"]
    for n in anchor["neighbours"]["items"]:
        pid, tid, cid, sid = (int(x) for x in n["key"].split("-"))
        assert (pid, tid, cid, sid, "statsbomb") in keys
    assert len(keys) > len(rows)
    B.build(inp, keys=keys, style_groups=())          # every key resolves


def test_fixture_keys_refuse_more_than_one_season(monkeypatch):
    inp = _world()
    two = B.Inputs(inp.season, inp.pct, inp.dna, inp.dims, inp.positions, inp.match,
                   {**inp.stamps, "season_ids": [27, 28]})
    with pytest.raises(B.ExportError, match="one season"):
        B.fixture_keys(two)


def test_fixture_cli_refuses_a_changing_fixture_unless_update_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(B, "FIXTURE_DIR", tmp_path / "web")
    monkeypatch.setattr(B, "FIXTURE_ROWS", ((200, 10, 2),))
    monkeypatch.setattr(B, "FIXTURE_NEIGHBOURS_OF", (200, 10, 2))
    monkeypatch.setattr(B, "load_inputs", lambda: _world())
    monkeypatch.setattr(B, "ROOT", tmp_path)
    real_build = B.build                                       # the synthetic world has one group
    monkeypatch.setattr(B, "build", lambda inp, keys=None, style_groups=(G,): real_build(
        inp, keys=keys, style_groups=style_groups))
    monkeypatch.setattr(sys, "argv", ["x", "--fixture"])
    B.main()                                                   # empty dir: first write allowed
    f = next((tmp_path / "web" / "players").glob("*.json"))
    before = f.read_bytes()
    f.write_bytes(b"{}\n")                                     # drift
    with pytest.raises(SystemExit, match="would change"):
        B.main()
    assert f.read_bytes() == b"{}\n"                           # refused: untouched
    monkeypatch.setattr(sys, "argv", ["x", "--fixture", "--update-fixture"])
    B.main()
    assert f.read_bytes() == before
    assert "changed" in capsys.readouterr().out


def test_synthetic_defensive_rates(docs):
    """9 of 900 opponent open-play passes → 1.00; the below-gate row without exposure → null."""
    rates = docs["players/100-10-2-27.json"]["defensive_rates"]
    assert [r["rate_text"] for r in rates] == ["1.00"] * 6
    assert all(r["rate"] is None for r in docs["players/500-13-2-27.json"]["defensive_rates"])
    assert docs["players/400-13-2-27.json"]["defensive_rates"] == []          # goalkeeper


# ── Gem Finder (plan 006 W5): gems_doc ─────────────────────────────────────────────

def _gem_player(key, name, score, minutes=1800, group="CB", team_id=1, in_dna=True):
    defs = B.family_definitions(group, D.withheld_for(group))
    dims = [{"family": f, "score": score, "score_text": None if score is None else str(score)}
            for f in defs]
    return {"identity": {"key": key, "display_name": name, "group": group, "team_id": team_id,
                         "team": f"T{team_id}", "competition_id": 2, "league": "L",
                         "minutes_text": str(minutes), "minutes": minutes, "near_gate": False},
            "dna": {"dimensions": dims} if in_dna else None}


def test_gems_equal_scores_by_folded_name_then_key_and_unscored_last():
    docs_ = {f"players/{k}.json": _gem_player(k, n, s) for k, n, s in [
        ("5-1-2-27", "Zed", 80.0),
        ("4-1-2-27", "Álvaro", 80.0),          # accent folds to a: before Zed, after nothing higher
        ("3-1-2-27", "Bob", 90.0),
        ("2-1-2-27", "Bob", 80.0),             # same name as key 1 below: key breaks the tie
        ("1-1-2-27", "Bob", 80.0),
        ("6-1-2-27", "Aaron", None),           # unscored: last, even before-alphabetically
        ("7-1-2-27", "Gone", 99.0)]}
    docs_["players/7-1-2-27.json"] = _gem_player("7-1-2-27", "Gone", 99.0, in_dna=False)
    g = B.gems_doc("CB", docs_)
    keys = [r["key"] for r in g["rows"]]
    assert "7-1-2-27" not in keys                          # no profile -> not a Gem row
    for fam, order in g["order"].items():
        assert [keys[i] for i in order] == ["3-1-2-27", "4-1-2-27", "1-1-2-27", "2-1-2-27",
                                            "5-1-2-27", "6-1-2-27"], fam


def test_gems_excludes_other_groups_and_withheld_families_have_no_order(docs):
    g = docs["gems/DM-CM.json"]
    keys = {r["key"] for r in g["rows"]}
    assert "400-13-2-27" not in keys and "500-13-2-27" not in keys    # goalkeeper, below gate
    assert {"200-10-2-27", "200-11-2-27"} <= keys                     # a mover keeps both clubs
    assert {(t["team_id"], t["team"], t["competition_id"]) for t in g["teams"]} == {
        (r["team_id"], r["team"], r["competition_id"]) for r in g["rows"]}
    assert len(g["teams"]) == len({t["team_id"] for t in g["teams"]})
    for f in g["families"]:
        assert (f["family"] in g["order"]) == (f["definition"] is not None)
        if f["definition"] is None:
            assert f["withheld_short"] and f["withheld_full"]
    assert not keys & {r["key"] for r in docs["gems/CB.json"]["rows"]}     # one group per row


def test_gems_minutes_tier_boundary_is_inclusive():
    docs_ = {"players/a.json": _gem_player("1-1-2-27", "A", 1.0, minutes=1800),
             "players/b.json": _gem_player("2-1-2-27", "B", 1.0, minutes=1799.9),
             "players/c.json": _gem_player("3-1-2-27", "C", 1.0, minutes=900)}
    rows = {r["key"]: r["minutes_tiers"] for r in B.gems_doc("CB", docs_)["rows"]}
    assert rows == {"1-1-2-27": ["1350", "1800"], "2-1-2-27": ["1350"], "3-1-2-27": []}


def test_gems_empty_group_is_a_valid_document():
    g = B.gems_doc("CB", {})
    assert g["rows"] == [] and all(o == [] for o in g["order"].values())
    assert g["leagues"] == [] and g["teams"] == []


def test_write_manages_the_gems_directory(docs, tmp_path):
    out = tmp_path / "v1"
    (out / "gems").mkdir(parents=True)
    stale = out / "gems" / "OLD.json"
    stale.write_text("{}")
    sizes = B.write(docs, out, "fixture")
    assert not stale.exists() and "gems/DM-CM.json" in sizes
    assert B.schema_name("gems/CB.json") == "gems"
