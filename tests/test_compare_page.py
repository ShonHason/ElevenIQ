"""
Fast tests for tools/compare_page.py (plan 005 M4e/M4f4, ADR 004 §11.5, §12) on synthetic
rows and the real registry: display rules, status texts, selectors, input guards, the
signature holder rule, the DNA radar and the dimension scores.
"""
from __future__ import annotations

import html
import math
import re
from typing import Any

import polars as pl
import pytest

from eleveniq.analytics import cohorts as C
from eleveniq.analytics import dna as D
from eleveniq.analytics.registry import (
    MEASUREMENT_SOURCE, REGISTRY, REGISTRY_VERSION, SPECS, is_signature_holder,
)
from eleveniq.pipeline import build_percentiles as B
from tools import compare_page as P

_KEY = dict(player_id=1, team_id=10, competition_id=2, season_id=27, source=MEASUREMENT_SOURCE)


# ── formatting ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, shown", [
    (0.0, "<1"), (0.2, "1"), (0.5, "1"), (1.49, "1"), (1.5, "2"), (49.5, "50"),
    (98.5, "99"), (99.5, "99"), (99.8, "99"), (100.0, ">99"),
])
def test_format_pct(stored, shown):
    assert P.format_pct(stored) == shown


@pytest.mark.parametrize("bad", [-0.1, 100.1])
def test_format_pct_out_of_range_raises(bad):
    with pytest.raises(P.PageError):
        P.format_pct(bad)


def test_format_value_by_unit():
    assert P.format_value("pressures_p90", 12.345) == "12.35"
    assert P.format_value("long_pass_completion_pct", 0.319) == "31.9%"
    assert P.format_value("team_pass_share", 0.105) == "10.5%"
    assert P.format_value("np_xg_per_shot", 0.1234) == "0.123"
    assert P.format_value("pressures_p90", None) == "—"


def test_every_output_status_has_text():
    statuses = {C.NOT_IN_POOL, C.GOALKEEPER, C.BELOW_GATE, C.NO_SIGNAL, C.NO_MIN_ATTEMPTS,
                C.BELOW_MIN_ATTEMPTS, C.NULL_VALUE, B.COHORT_TOO_SMALL, B.D9_UNDEFINED,
                B.LEAGUE_INCONSISTENT}
    assert statuses <= set(P.STATUS_TEXT)
    assert all(P.status_text(s) for s in statuses)
    with pytest.raises(P.PageError):
        P.status_text("made_up")


def test_registry_tables_cover_the_specs():
    ratios = {s.column for s in SPECS if s.ratio is not None}
    assert ratios == set(P.DENOMINATOR_NOUN)
    ref = P.SPECS_BY_COLUMN[P.POPULATION_REFERENCE_METRIC]
    assert ref.ratio is None and ref.unit == "per90"
    assert set(P.FAMILY_LABELS) <= set(P.FAMILY_ORDER)


# ── selectors ─────────────────────────────────────────────────────────────────────

def _identity(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame([{**_KEY, "player_name": "x", "team_name": "t", **r} for r in rows])


def test_parse_selector():
    assert P.parse_selector("3244") == P.Selector("3244", 3244)
    assert P.parse_selector("3244@29") == P.Selector("3244@29", 3244, 29)
    assert P.parse_selector("Kanté").name == "kante"
    for bad in ("3244@everton", "Stones@29"):
        with pytest.raises(P.PageError):
            P.parse_selector(bad)


def test_resolve_by_accent_insensitive_name():
    ident = _identity([{"player_id": 1, "player_name": "N'Golo Kanté"}])
    assert P.resolve(ident, P.parse_selector("KANTE"))["player_id"] == 1


def test_resolve_ambiguous_name_lists_candidates():
    ident = _identity([{"player_id": 1, "player_name": "Harry Kane"},
                       {"player_id": 2, "player_name": "Eunan O'Kane"}])
    with pytest.raises(P.PageError, match="several players"):
        P.resolve(ident, P.parse_selector("kane"))


def test_resolve_mover_needs_team():
    ident = _identity([{"player_id": 1, "team_id": 10}, {"player_id": 1, "team_id": 11}])
    with pytest.raises(P.PageError, match="player_id@team_id"):
        P.resolve(ident, P.parse_selector("1"))
    assert P.resolve(ident, P.parse_selector("1@11"))["team_id"] == 11


def test_resolve_missing_player():
    with pytest.raises(P.PageError, match="no player-season"):
        P.resolve(_identity([{"player_id": 1}]), P.parse_selector("2"))


# ── signature holder (ADR 004 §1) ─────────────────────────────────────────────────

def test_is_signature_holder_edges():
    cell = REGISTRY.cell("restart_key_passes_p90", "DM-CM")
    m = cell.signature_min_count
    assert m == 13
    assert is_signature_holder(cell, m, "scored") is True
    assert is_signature_holder(cell, m - 1, "scored") is False
    assert is_signature_holder(cell, 40, "below_gate") is None
    with pytest.raises(ValueError):
        is_signature_holder(cell, None, "scored")
    with pytest.raises(ValueError):
        is_signature_holder(REGISTRY.cell("pressures_p90", "DM-CM"), 50, "scored")


# ── rendering on synthetic rows ───────────────────────────────────────────────────

def _row(metric: str, group: str, *, role: str = "primary", key=_KEY, value: float | None = 1.0,
         pct: float | None = 50.0, n: int | None = 60, status: str = "ok",
         row_status: str = "scored") -> dict[str, Any]:
    cell = REGISTRY.cell(metric, group)
    return {**key, "position_group": group, "cohort_role": role, "metric": metric,
            "value": value, "percentile_status": row_status,
            "pct_local": pct, "n_local": n, "status_local": status,
            "pct_global": pct, "n_global": None if n is None else n * 4, "status_global": status,
            "basis": cell.basis, "registry_status": cell.status,
            "registry_version": REGISTRY_VERSION, "measurement_source": MEASUREMENT_SOURCE,
            "min_cohort_size": 30, "min_minutes_threshold": 900.0,
            "season_parquet_sha256": "ab" * 32, "polars_version": "x"}


def _dna(rows: dict[tuple[str, str], dict[str, Any]], group: str, key) -> tuple[dict, dict]:
    """player_dna / dimensions rows copying pct_local and n_local from the percentile rows;
    every family scored on its present directional members, the configured families
    withheld (§12.6, §12.9)."""
    dna = {}
    for m in REGISTRY.dna_metrics(group):
        r, spec = rows[("primary", m)], P.SPECS_BY_COLUMN[m]
        ok = r["status_local"] == "ok"
        dna[m] = {**key, "position_group": group, "metric": m, "family": spec.family,
                  "direction": spec.direction, "value": r["value"], "pct_local": r["pct_local"],
                  "n_local": r["n_local"], "metric_status": r["status_local"],
                  "dna_status": "complete" if ok else "partial"}
    dims = {}
    for fam in sorted({P.SPECS_BY_COLUMN[m].family for m in dna}):
        members = sorted(m for m in dna if P.SPECS_BY_COLUMN[m].family == fam
                         and dna[m]["metric_status"] == "ok"
                         and dna[m]["direction"] in ("higher_better", "lower_better"))
        withheld = D.withheld_for(group).get(fam)
        dims[fam] = {**key, "position_group": group, "family": fam,
                     "score": None if withheld or not members else 61.5,
                     "score_status": withheld if withheld
                     else "ok" if members else "no_directional_metric",
                     "members": [] if withheld else members}
    return dna, dims


def _player(group: str, *, key=_KEY, name: str = "Player One", second: str | None = None,
            gated: bool = True, overrides: dict[str, dict[str, Any]] | None = None,
            second_rows: bool = False, restart_count: int = 0, with_dna: bool = True) -> P.Player:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for s in SPECS:
        r = _row(s.column, group, key=key, **(overrides or {}).get(s.column, {}))
        rows[("primary", s.column)] = r
        if second_rows and second:
            rows[("second", s.column)] = _row(s.column, second, role="second", key=key)
    ident = {**key, "player_name": name, "team_name": "Team", "position_group": group,
             "second_position_group": second, "minutes_played": 2000.4,
             "minutes_nominal_equiv": 1900.6, "meets_min_minutes": gated,
             "restart_key_passes": restart_count}
    dna, dims = _dna(rows, group, key) if with_dna else ({}, {})
    return P.Player(ident, rows, dna, dims)


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", page, flags=re.S)))


def test_extremes_shown_as_gt99_and_lt1():
    a = _player("CB", overrides={"pressures_p90": {"pct": 100.0}, "clearances_p90": {"pct": 0.0}})
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    assert "&gt;99</span>" in page and "&lt;1</span>" in page
    assert not re.search(r'\|pct_(local|global)">(100|0)<', page)


def test_null_percentile_shows_reason_not_blank():
    a = _player("CB", overrides={"interceptions_p90": {"pct": None, "status": "league_inconsistent"}})
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    assert "not used: league means differ" in page
    for chunk in page.split('<div class="scope')[1:]:
        body = chunk.split("</div>")[0]
        assert "|pct_" in body or 'class="reason"' in body


def test_ratio_label_names_selected_population():
    a = _player("ST")
    b = _player("ST", key={**_KEY, "player_id": 2}, name="Player Two")
    text = _text(P.render([a, b]))
    m = REGISTRY.cell("long_pass_completion_pct", "ST").min_attempts
    assert f"n 60 of 60 STs with ≥ {m} long passes" in text


def test_zero_in_zero_heavy_cell_is_not_a_rank():
    a = _player("CB", overrides={"box_entries_carry_p90": {"value": 0.0, "pct": 36.4}})
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    assert "tied at zero, not a rank" in page
    assert 'box_entries_carry_p90|pct_local">36<' not in page


def test_long_pass_note_always_present():
    for group in ("AM-Winger", "ST", "CB"):
        a = _player(group)
        b = _player(group, key={**_KEY, "player_id": 2}, name="Player Two")
        assert html.escape(P.LONG_PASS_NOTE) in P.render([a, b])


# The user's wording (W1, decision C as corrected by the stats-reviewer, 2026-10-09): literals.
NO_READING = "no good/poor reading"
PASSING_WITHHELD = ("rests on long pass completion alone, which is not shown to follow the player "
                    "across clubs; xPass deferred until after launch")


def test_long_pass_direction_label_has_no_good_poor_reading():
    assert P.direction_label(P.SPECS_BY_COLUMN["long_pass_completion_pct"]) == NO_READING
    assert P.direction_label(P.SPECS_BY_COLUMN["pass_completion_pct"]) == "higher better"
    a = _player("CB")
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    row = _row_html(P.render([a, b]), "long_pass_completion_pct").split("</th>", 1)[0]
    assert NO_READING in row and "higher better" not in row


def test_second_group_label_only_for_gated_rows():
    gated = _player("AM-Winger", second="ST")
    ungated = _player("AM-Winger", second="ST", gated=False, key={**_KEY, "player_id": 2},
                      name="Player Two")
    page = P.render([gated, ungated])
    assert page.count("|second_position_group") == 1


def test_second_group_rows_are_a_display_only_section():
    a = _player("AM-Winger", second="ST", second_rows=True)
    b = _player("AM-Winger", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    assert "as ST — display only" in _text(page)
    assert "|second|" in page


def test_mixed_groups_label_every_percentile_and_warn():
    a = _player("ST")
    b = _player("AM-Winger", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    assert "not directly comparable" in page
    pcts = page.count('class="num pct"')
    assert pcts and page.count('class="vs"') == pcts
    same = P.render([a, _player("ST", key={**_KEY, "player_id": 2}, name="Player Two")])
    assert 'class="vs"' not in same and "not directly comparable" not in same


def test_signature_shown_as_holder_not_percentile():
    m = REGISTRY.cell("restart_key_passes_p90", "DM-CM").signature_min_count
    a = _player("DM-CM", restart_count=m)
    b = _player("DM-CM", key={**_KEY, "player_id": 2}, name="Player Two", restart_count=m - 1)
    page = P.render([a, b])
    assert ">holder<" in page and ">non-holder<" in page
    assert "restart_key_passes_p90|pct_" not in page
    assert "throw-ins" in P.METRIC_LABELS["restart_key_passes_p90"]


def test_below_gate_signature_not_assessed():
    a = _player("DM-CM", restart_count=40,
                overrides={"restart_key_passes_p90": {"row_status": "below_gate"}})
    b = _player("DM-CM", key={**_KEY, "player_id": 2}, name="Player Two")
    assert "not assessed: below the minutes gate" in P.render([a, b])


def test_build_players_needs_two_or_three():
    with pytest.raises(P.PageError, match="2 or 3"):
        P.build_players(pl.DataFrame(), pl.DataFrame(), [P.parse_selector("1")])


# ── input guards ──────────────────────────────────────────────────────────────────

def _write_inputs(tmp_path, **pct_overrides) -> tuple[Any, Any]:
    season = tmp_path / "season.parquet"
    pl.DataFrame([{**_KEY, **{c: None for c in P.IDENTITY_COLUMNS if c not in _KEY}}]).write_parquet(season)
    row = {**_row("pressures_p90", "CB"), "season_parquet_sha256": C.file_sha256(season), **pct_overrides}
    pct = tmp_path / "pct.parquet"
    pl.DataFrame([row]).write_parquet(pct)
    return pct, season


def _write_dna(tmp_path, pct, season, **overrides) -> tuple[Any, Any]:
    prov = {**_KEY, "position_group": "CB", "registry_version": REGISTRY_VERSION,
            "measurement_source": MEASUREMENT_SOURCE, "dna_percentile_basis": "local",
            "season_parquet_sha256": C.file_sha256(season),
            "percentiles_parquet_sha256": C.file_sha256(pct)}
    dna, dims = tmp_path / "dna.parquet", tmp_path / "dims.parquet"
    pl.DataFrame([{**prov, "metric": "pressures_p90", "pct_local": 50.0, **overrides}]).write_parquet(dna)
    pl.DataFrame([{**prov, "family": "Defending", "score": None}]).write_parquet(dims)
    return dna, dims


def test_load_inputs_accepts_matching_provenance(tmp_path):
    pct, season = _write_inputs(tmp_path)
    p, ident = P.load_inputs(27, pct, season)
    assert p.height == 1 and ident.height == 1


@pytest.mark.parametrize("override, match", [
    ({"registry_version": "stale"}, "registry"),
    ({"source": "other"}, "source"),
    ({"measurement_source": "other"}, "source"),
    ({"season_parquet_sha256": "00" * 32}, "season parquet"),
])
def test_load_inputs_guards(tmp_path, override, match):
    pct, season = _write_inputs(tmp_path, **override)
    with pytest.raises(P.PageError, match=match):
        P.load_inputs(27, pct, season)


def test_load_inputs_empty_season(tmp_path):
    pct, season = _write_inputs(tmp_path)
    with pytest.raises(P.PageError, match="no percentile rows"):
        P.load_inputs(99, pct, season)


def test_format_number_rejects_negatives():
    assert P.format_number(2829.8) == "2830" and P.format_number(15) == "15"
    with pytest.raises(P.PageError, match="negative"):
        P.format_number(-0.5)


def test_restart_metrics_carry_the_neutral_label():
    restart = {s.column for s in SPECS if s.column.startswith("restart_")}
    assert restart == set(P.METRIC_LABELS)
    assert all(label.startswith("Restart ") and "throw-ins" in label for label in P.METRIC_LABELS.values())
    a = _player("DM-CM", second="ST", second_rows=True)
    b = _player("DM-CM", key={**_KEY, "player_id": 2}, name="Player Two")
    assert "Set-piece" not in P.render([a, b])


def test_second_group_signature_trait_is_not_assessed_not_ranked():
    """ADR 004 §1, §11.5: a signature trait is never a percentile, and a D5 second-group
    row gets no holder verdict against a population it is not in."""
    assert REGISTRY.cell("restart_key_passes_p90", "ST").status == "signature"
    a = _player("AM-Winger", second="ST", second_rows=True)
    b = _player("AM-Winger", key={**_KEY, "player_id": 2}, name="Player Two")
    page = P.render([a, b])
    second = page.split("as ST — display only", 1)[1]
    assert P.SECOND_SIGNATURE_TEXT in second
    assert "|second|restart_key_passes_p90|" not in page


def _row_html(page: str, metric: str) -> str:
    label = html.escape(P.metric_label(P.SPECS_BY_COLUMN[metric]))
    return page.split(f'<th class="metric">{label}<div class="dir">', 1)[1].split("</tr>", 1)[0]


def test_every_team_style_biased_cell_renders_neutral():
    cells = [(c, g, cell.status) for (c, g), cell in REGISTRY.cells.items()
             if cell.team_style_biased]
    # The flag is a superset of the reason code: allow-listed DNA cells carry only the flag.
    assert {k for k, c in REGISTRY.cells.items() if "team_style_biased" in c.reasons} <= {
        (c, g) for c, g, _ in cells}
    assert {"dna", "display"} <= {status for _, _, status in cells}
    for col, group, _ in cells:
        hot = {col: {"pct": 95.0}}
        a = _player(group, overrides=hot)
        b = _player(group, key={**_KEY, "player_id": 2}, name="Player Two", overrides=hot)
        fills = re.findall(r'class="fill (\w+)"', _row_html(P.render([a, b]), col))
        assert fills and set(fills) == {"neutral"}, (col, group, fills)


def test_directional_cell_without_team_style_is_coloured():
    a = _player("CB", overrides={"aerials_won_p90": {"pct": 95.0}})
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    assert not REGISTRY.cell("aerials_won_p90", "CB").team_style_biased
    assert 'class="fill good"' in _row_html(P.render([a, b]), "aerials_won_p90")


def test_load_dna_accepts_matching_provenance(tmp_path):
    pct, season = _write_inputs(tmp_path)
    dna, dims = _write_dna(tmp_path, pct, season)
    v, d = P.load_dna(27, pct, season, dna, dims)
    assert v.height == 1 and d.height == 1


@pytest.mark.parametrize("override, match", [
    ({"registry_version": "stale"}, "registry_version"),
    ({"source": "other"}, "source"),
    ({"dna_percentile_basis": "global"}, "dna_percentile_basis"),
    ({"season_parquet_sha256": "00" * 32}, "season_parquet_sha256"),
    ({"percentiles_parquet_sha256": "00" * 32}, "percentiles_parquet_sha256"),
])
def test_load_dna_guards(tmp_path, override, match):
    """ADR 004 §12.7: the DNA files must come from this registry, source, basis, season stats
    and percentiles; anything else is refused, not drawn."""
    pct, season = _write_inputs(tmp_path)
    dna, dims = _write_dna(tmp_path, pct, season, **override)
    with pytest.raises(P.PageError, match=match):
        P.load_dna(27, pct, season, dna, dims)


# ── DNA radar and dimension scores (M4f4, ADR 004 §12) ────────────────────────────

def _radar(page: str) -> str:
    return page.split('<section class="rd">', 1)[1].split("</section>", 1)[0]


def _vertices(page: str) -> dict[str, tuple[float, float]]:
    """data-dna ref -> (cx, cy) of every radar vertex."""
    return {ref: (float(x), float(y)) for ref, x, y in re.findall(
        r'data-dna="([^"]+)" cx="([-\d.]+)" cy="([-\d.]+)"', page)}


def _pair(group: str, **kw) -> list[P.Player]:
    return [_player(group, **kw),
            _player(group, key={**_KEY, "player_id": 2}, name="Player Two")]


def test_radar_axes_are_the_dna_list_grouped_by_family():
    for group in ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST"):
        axes = P.radar_axes(group)
        assert sorted(axes) == sorted(REGISTRY.dna_metrics(group))
        fams = [P.SPECS_BY_COLUMN[m].family for m in axes]
        runs = [f for i, f in enumerate(fams) if i == 0 or fams[i - 1] != f]
        assert len(runs) == len(set(fams)), (group, fams)     # each family contiguous


def test_radar_vertex_sits_at_its_local_percentile():
    a = _player("DM-CM", overrides={"pressures_p90": {"pct": 80.0}})
    page = P.render([a, _pair("DM-CM")[1]])
    axes = P.radar_axes("DM-CM")
    verts = _vertices(page)
    assert len(verts) == 2 * len(axes)
    i = axes.index("pressures_p90")
    x, y = verts["1|10|2|27|statsbomb|pressures_p90|pct_local"]
    r = math.hypot(x - P.RADAR_C, y - P.RADAR_C)
    assert r == pytest.approx(0.8 * P.RADAR_R, abs=0.01)
    a_exp = P.radar_angle(i, len(axes))
    assert math.atan2(y - P.RADAR_C, x - P.RADAR_C) == pytest.approx(
        math.atan2(math.sin(a_exp), math.cos(a_exp)), abs=1e-3)


def test_missing_dna_metric_is_a_gap_not_a_zero():
    """ADR 004 §12.2: a partial row's missing metric is a visible gap. Plotting it at 0 (a
    vertex at the centre, a closed outline) must fail this test."""
    a = _player("AM-Winger", overrides={"long_pass_completion_pct": {
        "pct": None, "status": "below_min_attempts", "value": 0.45}})
    page = P.render([a, _pair("AM-Winger")[1]])
    radar = _radar(page)
    ref = "1|10|2|27|statsbomb|long_pass_completion_pct"
    assert f'data-dna="{ref}|pct_local"' not in radar
    assert f'data-gap="{ref}"' in radar
    for x, y in _vertices(radar).values():
        assert math.hypot(x - P.RADAR_C, y - P.RADAR_C) > 1e-6     # no vertex at the centre
    outlines = re.findall(r'<path class="rd-line s1( closed)?" d="([^"]+)"', radar)
    assert len(outlines) == 1 and outlines[0][0] == ""             # player one: open
    assert "Z" not in outlines[0][1] and outlines[0][1].count("M") == 1
    assert len(re.findall(r'<path class="rd-line s2 closed"', radar)) == 1
    assert "not plotted (below the minimum of ≥" in _text(radar)
    assert "a gap in the outline" in _text(radar).lower()


def test_radar_outline_breaks_on_both_sides_of_a_gap():
    pts = [(0.0, 0.0), (1.0, 1.0), None, (3.0, 3.0), (4.0, 4.0), None]
    path, closed = P.radar_outline(pts)
    assert not closed
    assert path == "M3.00,3.00 L4.00,4.00 M0.00,0.00 L1.00,1.00"
    path, closed = P.radar_outline([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
    assert closed and path.endswith(" Z")
    assert P.radar_outline([(0.0, 0.0), None, None])[0] == ""      # a lone vertex: marker only


def test_radar_only_when_players_share_a_group():
    page = P.render([_player("ST"), _player("AM-Winger", key={**_KEY, "player_id": 2},
                                            name="Player Two")])
    assert "No radar: the players are in different position groups" in page
    assert "data-dna=" not in page
    assert "data-dna=" in P.render(_pair("ST"))


def test_radar_labels_each_player_vs_their_league_group():
    text = _text(_radar(P.render(_pair("CB"))))
    assert re.search(r"Player One \( Team \) — vs .+ CBs \(n 60 \)", text)
    assert re.search(r"Player Two \( Team \) — vs .+ CBs \(n 60 \)", text)


def test_radar_style_axes_uncoloured():
    """Style and team-influenced axes carry no good/poor reading (ADR 004 §10.7, §11.5)."""
    radar = _radar(P.render(_pair("DM-CM")))
    for m in P.radar_axes("DM-CM"):
        spec = P.SPECS_BY_COLUMN[m]
        label = html.escape(P.metric_label(spec))
        cls = re.search(rf'<text class="(rd-lab[^"]*)"[^>]*>{re.escape(label)}', radar).group(1)
        plain = P.uncoloured(spec, REGISTRY.cell(m, "DM-CM").team_style_biased)
        assert (cls == "rd-lab style") == plain, (m, cls)
    assert P.uncoloured(P.SPECS_BY_COLUMN["interceptions_p90"], False)
    assert P.uncoloured(P.SPECS_BY_COLUMN["long_pass_completion_pct"], False)
    # ADR 004 §13.2 (M4g2): dribbled past is a style axis, so it carries no "lower better" reading.
    assert P.uncoloured(P.SPECS_BY_COLUMN["dribbled_past_p90"], False)
    assert "Dribbled past (lower better)" not in radar
    assert re.search(r'<text class="rd-lab style"[^>]*>Dribbled past<', radar)


def test_radar_needs_a_dna_vector():
    below = _player("CB", with_dna=False, overrides={
        P.POPULATION_REFERENCE_METRIC: {"row_status": "below_gate"}})
    page = P.render([below, _pair("CB")[1]])
    assert "Player One: no DNA vector" in _text(_radar(page))
    assert len(_vertices(page)) == len(P.radar_axes("CB"))
    with pytest.raises(P.PageError, match="scored row without a DNA vector"):
        P.render([_player("CB", with_dna=False), _pair("CB")[1]])


def test_dimension_scores_show_member_count_and_withheld_defending():
    page = P.render(_pair("AM-Winger"))
    text = _text(page)
    assert "based on 1 metric: Key passes (non-restart delivery)" in text    # Creation
    assert re.search(r"based on 2 metrics: ", text)
    assert "withheld (not shown to be free of team-possession effects)" in text   # §13.8 D5
    assert "|Defending|score" not in page                                      # never a number
    # Defending (D5) and Passing (pending), two players, in the radar section and the tables
    # every withheld family reads "withheld (<reason>)"; nothing says "until M4g" (ADR 004 §14)
    assert _radar(page).count("withheld (") == 4 and page.count("withheld (") == 8
    assert "withheld until" not in page and "M4g" not in _text(page)


def test_dm_cm_defending_reads_its_mover_verdict_with_the_underpowered_note():
    text = _text(P.render(_pair("DM-CM")))
    assert "withheld (player-vs-team test on club movers inconclusive; underpowered)" in text


def test_a_defending_status_that_differs_from_the_registry_raises():
    with pytest.raises(P.PageError, match="Defending status"):
        P.withheld_reason("Defending", "CB", "mover_stability_inconclusive")


@pytest.mark.parametrize("group", ["CB", "FB-WB", "DM-CM", "AM-Winger"])
def test_passing_withheld_where_it_rests_on_long_pass_completion(group):
    """ADR 004 §12.9: a one-metric long-pass Passing score would read a team-style value as
    poor individual passing (§11.5), so it is withheld, never a number."""
    page = P.render(_pair(group))
    assert f"withheld ({PASSING_WITHHELD})" in _text(page)
    assert "|Passing|score" not in page and "|Passing|members" not in page


def test_st_keeps_its_passing_score():
    page = P.render(_pair("ST"))
    assert "|Passing|score" in page
    assert "based on 1 metric: Pass completion" in _text(page)
    assert "mover stability" not in page


def test_dimension_score_no_directional_metric():
    a = _player("ST", overrides={"pass_completion_pct": {"pct": None, "status": "below_min_attempts"}})
    assert "no directional metric present in this row" in _text(P.render([a, _pair("ST")[1]]))
    with pytest.raises(P.PageError, match="unknown dimension score status"):
        P.dim_status_text("made_up")


def test_build_players_refuses_dna_that_disagrees_with_percentiles():
    a = _player("CB")
    ident = a.identity
    pct = pl.DataFrame([r for r in a.rows.values()])
    identity = pl.DataFrame([ident])
    dna = pl.DataFrame([{**r, "pct_local": 12.0} if r["metric"] == "pressures_p90" else r
                        for r in a.dna.values()])
    dims = pl.DataFrame(list(a.dims.values()))
    sel = [P.parse_selector("1"), P.parse_selector("2")]
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    pct = pl.concat([pct, pl.DataFrame(list(b.rows.values()))])
    identity = pl.concat([identity, pl.DataFrame([b.identity])])
    with pytest.raises(P.PageError, match="pct_local / n_local differ"):
        P.build_players(pct, identity, sel, dna, dims)
    ok = P.build_players(pct, identity, sel, pl.DataFrame(list(a.dna.values())), dims)
    assert set(ok[0].dna) == set(REGISTRY.dna_metrics("CB")) and not ok[1].dna


def test_build_players_refuses_dims_families_that_differ_from_dna_metrics():
    """ADR 004 §12.7: the dimension families must equal the families of the DNA metrics
    actually present; a family silently dropped from player_dna_dimensions.parquet (a
    data-quality fault, not a withheld score) must be refused, not rendered short a row."""
    a = _player("CB")
    pct = pl.DataFrame(list(a.rows.values()))
    identity = pl.DataFrame([a.identity])
    dna = pl.DataFrame(list(a.dna.values()))
    dims = pl.DataFrame([r for fam, r in a.dims.items() if fam != "Defending"])
    sel = [P.parse_selector("1"), P.parse_selector("2")]
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    pct = pl.concat([pct, pl.DataFrame(list(b.rows.values()))])
    identity = pl.concat([identity, pl.DataFrame([b.identity])])
    with pytest.raises(P.PageError, match="dimension families"):
        P.build_players(pct, identity, sel, dna, dims)


def test_build_players_refuses_dna_position_group_mismatch():
    """A DNA row whose position_group disagrees with the season parquet's (e.g. built from a
    stale or mismatched run) must be refused rather than drawn under the wrong group's axes."""
    a = _player("CB")
    pct = pl.DataFrame(list(a.rows.values()))
    identity = pl.DataFrame([a.identity])
    dna = pl.DataFrame([{**r, "position_group": "FB-WB"} for r in a.dna.values()])
    dims = pl.DataFrame(list(a.dims.values()))
    sel = [P.parse_selector("1"), P.parse_selector("2")]
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    pct = pl.concat([pct, pl.DataFrame(list(b.rows.values()))])
    identity = pl.concat([identity, pl.DataFrame([b.identity])])
    with pytest.raises(P.PageError, match="position group differs"):
        P.build_players(pct, identity, sel, dna, dims)


def test_dimension_html_raises_ok_score_without_score_or_members():
    """A dimension row claiming score_status "ok" but carrying no score or no members is
    contradictory data (ADR 004 §12.6: an "ok" score always names its directional members);
    it must raise, not silently render a blank or a "0" cell."""
    a = _player("CB")
    for bad in ({"score": None}, {"members": []}):
        broken = {**a.dims["Passing"], "score_status": "ok", **bad}
        bad_player = P.Player(a.identity, a.rows, a.dna, {**a.dims, "Passing": broken})
        with pytest.raises(P.PageError, match="score ok without a score or members"):
            P.dimension_html(bad_player, "Passing", False)


def test_radar_with_three_players_draws_three_distinct_series():
    """SERIES has three entries but every other radar test uses two players; a three-way
    comparison must still assign one colour/dash per player and list all three in the legend
    and the dimension-score table (a real risk: an index bug could repeat or skip a series)."""
    players = [_player("CB"),
               _player("CB", key={**_KEY, "player_id": 2}, name="Player Two"),
               _player("CB", key={**_KEY, "player_id": 3}, name="Player Three")]
    page = P.render(players)
    radar = _radar(page)
    axes = P.radar_axes("CB")
    verts = _vertices(page)
    assert len(verts) == 3 * len(axes)
    for s in ("s1", "s2", "s3"):
        assert re.search(rf'class="rd-v {s}"', radar), s
        assert re.search(rf'class="rd-line {s}( closed)?"', radar), s
    assert radar.count("— vs ") == 3
    names = [p.identity["player_name"] for p in players]
    header = radar.split("<h3>Dimension scores</h3>", 1)[1].split("</thead>", 1)[0]
    assert all(name in header for name in names)


def test_radar_three_players_one_skipped_keeps_the_other_two_as_s1_s2():
    """When one of three same-group players has no DNA vector, the drawn two must still be
    s1/s2 (not s1/s3, leaving a gap in the series), and the dimension-score table must list
    only the drawn players, not a blank column for the skipped one."""
    below = _player("CB", with_dna=False, overrides={
        P.POPULATION_REFERENCE_METRIC: {"row_status": "below_gate"}})
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    c = _player("CB", key={**_KEY, "player_id": 3}, name="Player Three")
    page = P.render([below, b, c])
    radar = _radar(page)
    assert re.search(r'class="rd-v s1"', radar) and re.search(r'class="rd-v s2"', radar)
    assert not re.search(r'class="rd-v s3"', radar)
    assert "Player One: no DNA vector" in _text(radar)
    header = radar.split("<h3>Dimension scores</h3>", 1)[1].split("</thead>", 1)[0]
    assert "Player One" not in header and "Player Two" in header and "Player Three" in header


@pytest.mark.parametrize("family, status", [("Passing", "ok"), ("Aerial", "pending_xpass_deferred")])
def test_build_players_refuses_dimensions_built_under_another_withhold_rule(family, status):
    """A dimensions file whose withheld statuses differ from config.DNA_WITHHELD_DIMENSIONS
    (e.g. built before ADR 004 §12.9) is refused, not drawn."""
    a = _player("CB")
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    dims = [{**r, "score_status": status} if r["family"] == family else r for r in a.dims.values()]
    pct = pl.DataFrame([*a.rows.values(), *b.rows.values()])
    identity = pl.DataFrame([a.identity, b.identity])
    with pytest.raises(P.PageError, match="withheld dimensions"):
        P.build_players(pct, identity, [P.parse_selector("1"), P.parse_selector("2")],
                        pl.DataFrame(list(a.dna.values())), pl.DataFrame(dims))


def test_stale_pending_mover_stability_dimensions_are_refused():
    """ADR 004 §14 item 4: the status was renamed; a dimensions parquet built before the rename
    still says pending_mover_stability and must be refused (rebuild), not drawn or half-read."""
    a = _player("CB")
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    dims = [{**r, "score_status": "pending_mover_stability"} if r["family"] == "Passing" else r
            for r in a.dims.values()]
    pct = pl.DataFrame([*a.rows.values(), *b.rows.values()])
    identity = pl.DataFrame([a.identity, b.identity])
    with pytest.raises(P.PageError, match="withheld dimensions"):
        P.build_players(pct, identity, [P.parse_selector("1"), P.parse_selector("2")],
                        pl.DataFrame(list(a.dna.values())), pl.DataFrame(dims))
    with pytest.raises(P.PageError, match="unknown dimension score status"):
        P.dim_status_text("pending_mover_stability")
