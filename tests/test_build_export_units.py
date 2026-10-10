"""build_export internals on synthetic rows (plan 006 W1c): population text, zero ties, identity
gating, accent folding, manifest and write. No data/ reads."""
from __future__ import annotations

import hashlib
import json

import pytest

from eleveniq.analytics import registry as R
from eleveniq.export import build_export as B
from eleveniq.export import display as X
from eleveniq.export import words as W

STAMPS = {"contract_version": "1.0.0", "registry_version": "0" * 64, "measurement_source": "statsbomb",
          "source": "statsbomb", "season_ids": [27], "season_parquet_sha256": "0" * 64,
          "percentiles_parquet_sha256": "0" * 64, "dna_parquet_sha256": "0" * 64,
          "positions_parquet_sha256": "0" * 64, "match_parquet_sha256": "0" * 64}


def _row(metric, group, value, pct, n=100, status="ok", reg="signature"):
    return {"player_id": 1, "competition_id": 11, "season_id": 27,
            "position_group": group, "registry_status": reg, "value": value,
            "pct_local": pct, "pct_global": pct, "n_local": n, "n_global": n,
            "status_local": status, "status_global": status, "basis": "season",
            "percentile_status": status, "cohort_role": "primary"}


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
    # W1d fix 1 (user, 2026-10-09): a signature trait shows no percentile in the metric rows,
    # so the zero tie is tested on a display cell with the zero-heavy reason.
    m = "box_entries_carry_p90"
    assert R.R_ZERO_HEAVY in R.REGISTRY.cell(m, "CB").reasons
    assert R.REGISTRY.cell(m, "CB").status == "display"
    rows = {m: _row(m, "CB", 0.0, 37.0, reg="display")}
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
            "meets_min_minutes": meets, "minutes_played": 1000.0, "minutes_nominal_equiv": nominal,
            "appearances": 12, "goals": 1, "assists": 2, "xg": 1.25, "shots": 9}


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


def test_a_signature_row_shows_no_percentile_and_says_so():
    """ADR 004 §11.5: holder / non-holder, never a percentile (W1d fix 1)."""
    m = "restart_key_passes_p90"
    assert R.REGISTRY.cell(m, "ST").status == "signature"
    for value in (0.0, 0.4):
        out = B.metric_row({m: _row(m, "ST", value, 37.0)}, m)
        assert out["zero_tie"] is False
        for scope in ("local", "global"):
            assert out[scope]["pct"] is None and out[scope]["bin"] is None
            assert out[scope]["status_text"] == "signature trait — see below"
    second = dict(_row(m, "ST", 0.4, 37.0), cohort_role="second")
    assert B.metric_row({m: second}, m)["local"]["status_text"] == X.SECOND_SIGNATURE_TEXT


def test_a_measured_zero_share_makes_a_tie_without_the_registry_reason():
    """Option A (user, 2026-10-09): CB restart_assists_p90 has a measured zero share of 0.98
    but no zero-heavy reason (it leaves the registry earlier); a zero there is a tie."""
    m = "restart_assists_p90"
    assert R.R_ZERO_HEAVY not in R.REGISTRY.cell(m, "CB").reasons
    assert R.measured_zero_share(m, "CB") > 0.30
    out = B.metric_row({m: _row(m, "CB", 0.0, 50.0, reg="display")}, m)
    assert out["zero_tie"] and out["local"]["status_text"] == "tied at zero, not a rank"


def _spec(col):
    return X.SPECS_BY_COLUMN[col]


def _lower_better_col():
    return next(s.column for s in X.SPECS_BY_COLUMN.values() if s.direction == "lower_better")


def test_scope_lower_better_shows_flipped_percentile_with_note_and_band():
    col = _lower_better_col()
    spec = _spec(col)
    row = _row(col, "CB", 1.0, 78.0, reg="dna")
    cell = R.REGISTRY.cell(col, "CB")
    got = B._scope({col: row}, row, "local", spec, cell, False, False,
                   {"league": "La Liga 2015/16", "group": "centre-backs"})
    assert got["pct"] == 22.0
    assert got["band"] == "Bottom 25%"
    assert "22nd percentile" in got["note"] and W.LOWER_BETTER_NOTE in got["note"]
    assert got["phrase"] == "bottom 22%"
    # the raw percentile is untouched
    assert row["pct_local"] == 78.0


def test_scope_null_percentile_has_no_band_note_or_bin():
    col = _lower_better_col()
    row = _row(col, "CB", 1.0, None, status="cohort_too_small", reg="dna")
    got = B._scope({col: row}, row, "global", _spec(col), R.REGISTRY.cell(col, "CB"), False,
                   False, {"league": "x", "group": "y"})
    assert got["pct"] is None and got["band"] is None and got["note"] is None
    assert got["bin"] is None and got["phrase"] is None


def test_scope_style_metric_uses_neutral_phrase_and_band():
    col = next(s.column for s in X.SPECS_BY_COLUMN.values() if s.direction == "style")
    row = _row(col, "CB", 1.0, 97.0, reg="dna")
    got = B._scope({col: row}, row, "local", _spec(col), R.REGISTRY.cell(col, "CB"), False,
                   False, {"league": "x", "group": "y"})
    assert got["phrase"] == "higher than most" and got["band"] == "Higher than most"
    assert "lower is better" not in got["note"]


def test_total_text_ratio_and_count():
    # A ratio: numerator over (Σ plus − Σ minus); counts are distinct, so a wrong term shows.
    ratio = next(s for s in X.SPECS_BY_COLUMN.values()
                 if s.ratio is not None and s.ratio.denominator_minus)
    r = ratio.ratio
    ident = {c: 0 for c in r.terms}
    ident[r.numerator] = 1812
    for i, c in enumerate(r.denominator_plus):
        ident[c] = ident.get(c, 0) + 2000 + 100 * i
    for c in r.denominator_minus:
        ident[c] = ident.get(c, 0) + 7
    den = (sum(ident[c] for c in r.denominator_plus)
           - sum(ident[c] for c in r.denominator_minus))
    assert B._total_text(ratio, ident) == f"{X.format_count(ident[r.numerator])} / {X.format_count(den)}"
    per90 = next(s for s in X.SPECS_BY_COLUMN.values() if s.count_column and s.ratio is None
                 and s.integer_count)
    assert B._total_text(per90, {per90.count_column: 1176}) == "1,176 total"


def test_format_count_none_and_decimals():
    assert X.format_count(None) == "—"
    assert X.format_count(0) == "0"
    assert X.format_count(12.5, True) == "12.50"
    assert X.format_count(0.0, True) == "0.00"
