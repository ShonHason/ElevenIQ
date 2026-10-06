"""
Fast tests for tools/compare_page.py (plan 005 M4e, ADR 004 §11.5) on synthetic rows and
the real registry: display rules, status texts, selectors, input guards, the signature
holder rule.
"""
from __future__ import annotations

import html
import re
from typing import Any

import polars as pl
import pytest

from eleveniq.analytics import cohorts as C
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


def _player(group: str, *, key=_KEY, name: str = "Player One", second: str | None = None,
            gated: bool = True, overrides: dict[str, dict[str, Any]] | None = None,
            second_rows: bool = False, restart_count: int = 0) -> P.Player:
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
    return P.Player(ident, rows)


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


def test_long_pass_direction_label_is_team_influenced():
    assert P.direction_label(P.SPECS_BY_COLUMN["long_pass_completion_pct"]) == "team-influenced"
    assert P.direction_label(P.SPECS_BY_COLUMN["pass_completion_pct"]) == "higher better"
    a = _player("CB")
    b = _player("CB", key={**_KEY, "player_id": 2}, name="Player Two")
    row = P.render([a, b]).split("Long pass completion", 1)[1].split("</th>", 1)[0]
    assert "team-influenced" in row and "higher better" not in row


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
