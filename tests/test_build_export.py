"""
The web export (plan 006 W1c; ADR 005 §1–§2).
Fast: keys and names, deterministic bytes, the schemas themselves, validation failures, the
group metadata (gap eligibility, option c), the stale-stamp refusal.
Integration (needs data/processed/): the full export validates against contract/v1 and is
byte-identical when built twice; every key is ids-only; neighbours stay in-group.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import polars as pl
import pytest

from eleveniq.analytics import registry as R
from eleveniq.export import build_export as B


def test_row_key_and_display_name():
    assert B.row_key((3961, 22, 2, 27, "statsbomb")) == "3961-22-2-27"
    assert B.display_name("Francisco Román Alarcón Suárez", "Isco") == "Isco"
    assert B.display_name("John Stones", None) == "John Stones"
    assert B.display_name("John Stones", "   ") == "John Stones"
    assert B.display_name("X", " Isco ") == "Isco"            # G1 backlog: stripped


def test_dumps_is_deterministic_and_rejects_nan():
    doc = {"b": 1, "a": [0.1, None, "é"]}
    assert B.dumps(doc) == B.dumps(json.loads(B.dumps(doc)))
    assert B.dumps(doc).decode().endswith("\n") and "é" in B.dumps(doc).decode()
    with pytest.raises(ValueError):
        B.dumps({"x": float("nan")})


@pytest.mark.parametrize("path", sorted(B.CONTRACT_DIR.glob("*.schema.json")), ids=lambda p: p.name)
def test_every_schema_is_a_valid_draft_2020_12_schema(path):
    s = json.loads(path.read_text())
    jsonschema.Draft202012Validator.check_schema(s)
    assert s["$id"].endswith(path.name)


def test_every_file_kind_has_a_schema():
    for p in ("manifest.json", "search-index.json", "labels.json", "players/1-2-3-27.json",
              "style-maps/CB.json", "groups/GK.json"):
        assert (B.CONTRACT_DIR / f"{B.schema_name(p)}.schema.json").exists()
    with pytest.raises(B.ExportError):
        B.schema_name("compare/CB.json")                     # no compare file (user, 2026-10-09)


def test_validate_rejects_a_bad_document():
    doc = B.labels_doc({"contract_version": "1.0.0", "registry_version": "0" * 64,
                        "measurement_source": "statsbomb", "source": "statsbomb", "season_ids": [27],
                        "season_parquet_sha256": "0" * 64, "percentiles_parquet_sha256": "0" * 64,
                        "dna_parquet_sha256": "0" * 64, "positions_parquet_sha256": "0" * 64,
                        "match_parquet_sha256": "0" * 64})
    B.validate({"labels.json": doc})
    doc["extra"] = 1
    with pytest.raises(B.ExportError, match="labels.json"):
        B.validate({"labels.json": doc})


@pytest.mark.parametrize("metric, eligible", [
    ("long_pass_completion_pct", False),        # long pass completion (option c)
    ("progressive_passes_p90", False),          # associated with team possession in DM-CM
    ("restart_key_passes_p90", False),          # a signature trait in DM-CM
    ("interceptions_p90", True),                # a style metric stays eligible
    ("op_key_passes_p90", True),
])
def test_group_metadata_gap_rule_option_c_dm_cm(metric, eligible):
    """Fixed expectations (user, 2026-10-09, option c), DM-CM."""
    from eleveniq.analytics import dna as D
    assert B.metric_meta(metric, "DM-CM", D.withheld_for("DM-CM"))["gap_eligible"] is eligible


@pytest.mark.parametrize("group, metric, eligible", [
    ("CB", "long_pass_completion_pct", False), ("CB", "progressive_passes_p90", False),
    ("FB-WB", "progressive_carries_p90", False), ("AM-Winger", "pass_completion_pct", False),
    ("ST", "np_xg_p90", False), ("ST", "pass_completion_pct", True), ("CB", "clearances_p90", True),
    # W1d option A (user): the measured possession check on every cell, style metrics included
    ("CB", "passes_attempted_p90", False), ("DM-CM", "final_third_entries_pass_p90", False),
    ("AM-Winger", "xg_p90", False), ("ST", "shots_on_target_p90", False),
])
def test_group_metadata_gap_rule_option_c_other_groups(group, metric, eligible):
    """Fixed expectations in the other groups (the registry's team_style_biased cells of the
    stats-reviewer's W1a output review; ST keeps pass completion eligible)."""
    from eleveniq.analytics import dna as D
    assert B.metric_meta(metric, group, D.withheld_for(group))["gap_eligible"] is eligible


def test_group_metadata_tags_and_long_pass_note():
    from eleveniq.analytics import dna as D
    meta = B.metric_meta("long_pass_completion_pct", "CB", D.withheld_for("CB"))
    assert meta["direction_text"] == "no good/poor reading"
    assert meta["withheld_tag"] == "(Passing score withheld: rests on long pass completion alone)"
    assert meta["notes"][0]["short"] == "not read as good or poor passing"
    st = B.metric_meta("pass_completion_pct", "ST", D.withheld_for("ST"))
    assert st["withheld_tag"] is None                         # ST keeps its Passing score


def test_stale_percentiles_are_refused(tmp_path, monkeypatch):
    season = tmp_path / "s.parquet"
    pl.DataFrame({"source": ["statsbomb"]}).write_parquet(season)
    pct = tmp_path / "p.parquet"
    pl.DataFrame({"registry_version": ["stale"], "measurement_source": ["statsbomb"],
                  "source": ["statsbomb"], "season_parquet_sha256": ["x"]}).write_parquet(pct)
    with pytest.raises(B.ExportError, match="registry_version"):
        B.load_inputs(season_path=season, percentiles_path=pct, dna_path=pct, dimensions_path=pct)


# ── Integration: the full export ─────────────────────────────────────────────────

_NEEDS = [B.SEASON_PATH, B.PERCENTILES_PATH, B.DNA_PATH, B.DIMENSIONS_PATH, B.POSITIONS_PATH,
          B.MATCH_PATH]


@pytest.fixture(scope="module")
def full():
    if not all(p.exists() for p in _NEEDS):
        pytest.skip("pipeline outputs missing")
    return B.build(B.load_inputs())


@pytest.mark.integration
def test_full_export_validates_and_is_byte_identical(full):
    B.validate(full)
    again = B.build(B.load_inputs())
    assert {p: B.dumps(d) for p, d in full.items()} == {p: B.dumps(d) for p, d in again.items()}


@pytest.mark.integration
def test_full_export_keys_are_ids_and_neighbours_stay_in_group(full):
    players = {p: d for p, d in full.items() if p.startswith("players/")}
    season = pl.read_parquet(B.SEASON_PATH)
    assert len(players) == season.height
    for p, d in players.items():
        assert p == f"players/{d['identity']['key']}.json"
        if d["neighbours"]:
            g = d["identity"]["group"]
            for n in d["neighbours"]["items"]:
                assert full[f"players/{n['key']}.json"]["identity"]["group"] == g
                assert n["key"].split("-")[0] != str(d["identity"]["player_id"])


@pytest.mark.integration
def test_near_gate_only_cb_fbwb_in_the_export(full):
    for p, d in full.items():
        if p.startswith("players/") and d["identity"]["near_gate"]:
            i = d["identity"]
            assert i["group"] in ("CB", "FB-WB") and 900 - 1e-6 <= i["nominal_minutes"] < 1350


@pytest.mark.integration
def test_full_export_every_metric_cell_has_a_number_or_a_status_text(full):
    """Export-wide invariant (user, 2026-10-09): never neither, in any scope of any row."""
    for p, d in full.items():
        if not p.startswith("players/"):
            continue
        cells = list(d["metrics"]) + (d["second_group"]["metrics"] if d["second_group"] else [])
        for c in cells:
            for scope in ("local", "global"):
                assert (c[scope]["pct_text"] is None) != (c[scope]["status_text"] is None), (p, c)


def test_fixed_display_reasons_are_not_page_text():
    """W3: a fixed_display spec reason is ADR shorthand ("D12: raw volume"); it is not exported.
    The approved reason texts still are."""
    from eleveniq.analytics import dna as D
    fixed = next((m, g) for (m, g), c in R.REGISTRY.cells.items()
                 if g in B.GROUPS and R.R_FIXED in c.reasons)
    assert B.metric_meta(fixed[0], fixed[1], D.withheld_for(fixed[1]))["reasons"] is None
    d17 = next((m, g) for (m, g), c in R.REGISTRY.cells.items()
               if g in B.GROUPS and c.reasons == (R.R_D17,))
    assert B.metric_meta(d17[0], d17[1], D.withheld_for(d17[1]))["reasons"] == R.REASON_TEXT[R.R_D17]


def test_labels_carry_the_player_page_words_and_the_metric_order():
    stamps = {"contract_version": "1.0.0", "registry_version": "0" * 64,
              "measurement_source": "statsbomb", "source": "statsbomb", "season_ids": [27],
              **{f"{n}_sha256": "0" * 64 for n in ("season_parquet", "percentiles_parquet",
                                                  "dna_parquet", "positions_parquet", "match_parquet")}}
    w = B.labels_doc(stamps)["words"]
    for k in ("profile_shape_title", "profile_shape_note", "around_average", "chip_withheld",
              "chip_same_team", "chip_provisional", "column_value", "column_local",
              "column_global", "column_minutes", "neighbours_title", "most_alike", "most_different"):
        assert w[k], k
    assert B.labels_doc(stamps)["metric_order"] == [s.column for s in R.SPECS]


def test_group_metadata_follows_the_metric_order():
    """Player rows and group metadata are paired by index: both follow registry metric order."""
    from eleveniq.analytics import dna as D
    order = [s.column for s in R.SPECS]
    for g in B.GROUPS:
        held = D.withheld_for(g)
        assert [B.metric_meta(m, g, held)["metric"] for m in order] == order
