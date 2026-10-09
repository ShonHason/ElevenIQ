"""build_export internals on synthetic rows (plan 006 W1c): population text, zero ties, identity
gating, accent folding, manifest and write. No data/ reads."""
from __future__ import annotations

import hashlib
import json

import pytest

from eleveniq.analytics import registry as R
from eleveniq.export import build_export as B
from eleveniq.export import display as X

STAMPS = {"contract_version": "1.0.0", "registry_version": "0" * 64, "measurement_source": "statsbomb",
          "source": "statsbomb", "season_ids": [27], "season_parquet_sha256": "0" * 64,
          "percentiles_parquet_sha256": "0" * 64, "dna_parquet_sha256": "0" * 64,
          "positions_parquet_sha256": "0" * 64, "match_parquet_sha256": "0" * 64}


def _row(metric, group, value, pct, n=100, status="ok", reg="signature"):
    return {"player_id": 1, "position_group": group, "registry_status": reg, "value": value,
            "pct_local": pct, "pct_global": pct, "n_local": n, "n_global": n,
            "status_local": status, "status_global": status, "basis": "season",
            "percentile_status": status}


def test_fold_removes_accents_and_case():
    assert X.fold("  N'Golo Kanté ") == "n'golo kante"
    assert X.fold("Alarcón") == "alarcon"
    assert X.fold("") == ""


def test_population_text_ratio_vs_plain():
    rows = {"passes_attempted_p90": _row("passes_attempted_p90", "ST", 1, 50, n=157),
            "long_pass_completion_pct": _row("long_pass_completion_pct", "ST", 0.5, 50, n=64)}
    row = rows["long_pass_completion_pct"]
    spec = X.SPECS_BY_COLUMN["long_pass_completion_pct"]
    cell = R.REGISTRY.cell("long_pass_completion_pct", "ST")
    assert B._population_text(rows, row, "local", spec, cell) == \
        f"n 64 of 157 STs with ≥ {cell.min_attempts} long passes"
    plain = X.SPECS_BY_COLUMN["passes_attempted_p90"]
    assert B._population_text(rows, rows["passes_attempted_p90"], "local", plain, None) == "n 157"
    row["n_local"] = None
    assert B._population_text(rows, row, "local", spec, cell) is None


def test_zero_heavy_zero_value_is_a_tie_not_a_rank():
    m = "restart_key_passes_p90"
    assert R.R_ZERO_HEAVY in R.REGISTRY.cell(m, "ST").reasons
    rows = {m: _row(m, "ST", 0.0, 37.0)}
    out = B.metric_row(rows, m)
    assert out["zero_tie"] is True
    for scope in ("local", "global"):
        assert out[scope]["pct"] is None and out[scope]["bin"] is None
        assert out[scope]["phrase"] is None
        assert out[scope]["status_text"] == "tied at zero, not a rank"
    rows[m]["value"] = 0.4
    out = B.metric_row(rows, m)
    assert out["zero_tie"] is False and out["local"]["pct"] == 37.0


def test_null_percentile_carries_status_text_not_a_number():
    m = "passes_attempted_p90"
    rows = {m: _row(m, "ST", 30.0, None, status="below_gate", reg=None)}
    out = B.metric_row(rows, m)
    assert out["local"]["pct"] is None and out["local"]["bin"] is None
    assert out["local"]["status_text"] == X.status_text("below_gate")


def _ident(group="CB", meets=True, nominal=1000.0, second="FB-WB", nick=None):
    return {"player_id": 7, "team_id": 2, "competition_id": 11, "season_id": 27,
            "source": "statsbomb", "position_group": group, "player_name": "A B",
            "player_nickname": nick, "team_name": "T", "second_position_group": second,
            "meets_min_minutes": meets, "minutes_played": 1000.0, "minutes_nominal_equiv": nominal}


def test_identity_hides_second_group_and_near_gate_below_gate():
    below = B._identity(_ident(meets=False, nominal=800.0), None)
    assert below["second_group"] is None and below["near_gate"] is False
    assert below["role_text"] is None and below["meets_gate"] is False


def test_identity_near_gate_boundaries_and_groups():
    assert B._identity(_ident(nominal=900.0), None)["near_gate"] is True
    assert B._identity(_ident(nominal=1350.0), None)["near_gate"] is False
    assert B._identity(_ident(group="ST", nominal=1000.0), None)["near_gate"] is False
    assert B._identity(_ident(nick="  "), None)["nickname"] is None
    assert B._identity(_ident(nick=" Ace "), None)["display_name"] == "Ace"


def test_identity_goalkeeper_has_no_role():
    i = B._identity(_ident(group="GK"), {"Goalkeeper": 900.0})
    assert i["role_text"] is None and i["dmcm_slot"] is None


def test_identity_unknown_league_is_refused():
    with pytest.raises(B.ExportError):
        B.league_label(999, 999)


def test_manifest_hashes_and_write_roundtrip(tmp_path):
    doc = B.labels_doc(STAMPS)
    sizes = B.write({"labels.json": doc}, tmp_path, "abc")
    assert set(sizes) == {"labels.json", "manifest.json"}
    blob = (tmp_path / "labels.json").read_bytes()
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["files"] == [{"path": "labels.json", "sha256": hashlib.sha256(blob).hexdigest(),
                             "bytes": len(blob)}]
    assert man["exporter_commit"] == "abc"
    B.write({"labels.json": doc}, tmp_path / "again", "abc")
    assert (tmp_path / "again" / "manifest.json").read_bytes() == (tmp_path / "manifest.json").read_bytes()


def test_write_refuses_invalid_docs_and_writes_nothing(tmp_path):
    bad = B.labels_doc(STAMPS)
    bad["extra"] = 1
    with pytest.raises(B.ExportError):
        B.write({"labels.json": bad}, tmp_path, "abc")
    assert not list(tmp_path.iterdir())
