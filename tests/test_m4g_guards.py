"""
M4g1 run-time guards (code-reviewer, M4g1): the checks that make a spike run fail when text
it quotes no longer matches the data, and the display rule for naming axis ends.
- check_quoted: a quoted loading must round to its quoted value; a "min" / "max" claim must
  still be the axis's most negative / most positive loading; a "no_end" claim must still find
  no loading of that sign reaching END_MIN_LOADING; the CB note needs aerials lost above won;
- pool_facts: the legend's role counts, computed from the roles; no LW / RW rows → no share;
- check_proposal: every figure in the fine-roles proposal, combos counted within the group
  the proposal names;
- end_metrics: loadings compared unrounded against the threshold.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import m4g_fine_roles as fr  # noqa: E402
import m4g_style_map as sm  # noqa: E402
import m4g_style_page as page  # noqa: E402


# ── check_quoted ─────────────────────────────────────────────────────────────

def _maps(cb_pc2: dict[str, float], st_pc2: dict[str, float] | None = None) -> dict:
    def mp(pc2: dict[str, float]) -> dict:
        ms = list(pc2)
        return {"model": {"metrics": ms, "components": [[0.0] * len(ms), [pc2[m] for m in ms]]}}
    out = {"CB": mp(cb_pc2)}
    if st_pc2 is not None:
        out["ST"] = mp(st_pc2)
    return out


CB = {"aerials_lost_p90": 0.651, "aerials_won_p90": 0.578, "long_pass_completion_pct": -0.112,
      "clearances_p90": 0.2}


def _check(monkeypatch, quotes, maps) -> None:
    monkeypatch.setattr(sm, "QUOTED_LOADINGS", quotes)
    sm.check_quoted(maps)


def test_matching_quotes_pass(monkeypatch):
    _check(monkeypatch, (("CB", 1, "value", "aerials_lost_p90", 0.65),
                         ("CB", 1, "min", "long_pass_completion_pct", -0.11),
                         ("CB", 1, "max", "aerials_lost_p90", 0.65),
                         ("CB", 1, "no_end", None, -1)), _maps(CB))


def test_a_drifted_value_fails(monkeypatch):
    with pytest.raises(SystemExit):
        _check(monkeypatch, (("CB", 1, "value", "aerials_lost_p90", 0.64),), _maps(CB))


def test_a_min_claim_fails_when_another_loading_is_lower(monkeypatch):
    maps = _maps({**CB, "clearances_p90": -0.3})
    with pytest.raises(SystemExit):
        _check(monkeypatch, (("CB", 1, "min", "long_pass_completion_pct", -0.11),), maps)


def test_a_max_claim_fails_when_another_loading_is_higher(monkeypatch):
    maps = _maps({**CB, "clearances_p90": 0.7})
    with pytest.raises(SystemExit):
        _check(monkeypatch, (("CB", 1, "max", "aerials_lost_p90", 0.65),), maps)


@pytest.mark.parametrize("neg, fails", [(-0.149, False), (-0.151, True)])
def test_no_end_claim_uses_the_threshold_on_the_claimed_side(monkeypatch, neg, fails):
    maps = _maps({**CB, "long_pass_completion_pct": neg})
    quotes = (("CB", 1, "no_end", None, -1),)
    if fails:
        with pytest.raises(SystemExit):
            _check(monkeypatch, quotes, maps)
    else:
        _check(monkeypatch, quotes, maps)
    # A positive loading of the same size is on the other end and never trips the claim.
    _check(monkeypatch, quotes, _maps({**CB, "long_pass_completion_pct": 0.0,
                                       "clearances_p90": 0.5}))


def test_cb_note_needs_aerials_lost_above_won(monkeypatch):
    with pytest.raises(SystemExit):
        _check(monkeypatch, (), _maps({**CB, "aerials_won_p90": 0.7}))


# ── pool_facts ───────────────────────────────────────────────────────────────

LEAGUES = {11: "La Liga", 12: "Serie A", 2: "Premier League"}


def _r(label: str, pos: dict[str, float]) -> dict:
    return {"label": label, "pos_min": pos}


def test_pool_facts_counts_from_the_roles():
    roles = {
        (1, 1, 11, 27, "s"): _r("AM", {"Center Attacking Midfield": 900.0}),
        (2, 1, 12, 27, "s"): _r("LWB", {"Left Wing Back": 900.0}),
        (3, 1, 12, 27, "s"): _r("RWB", {"Right Wing Back": 900.0}),
        (4, 1, 2, 27, "s"): _r("RWB", {"Right Wing Back": 900.0}),
        (5, 1, 11, 27, "s"): _r("LW", {"Left Wing": 600.0, "Left Midfield": 300.0,
                                       "Center Forward": 100.0}),
        (6, 1, 11, 27, "s"): _r("RW", {"Right Wing": 500.0, "Right Midfield": 100.0}),
        (7, 1, 11, 27, "s"): _r("mixed", {"Left Midfield": 500.0, "Left Back": 500.0}),
    }
    f = sm.pool_facts(roles, LEAGUES)
    assert f["am_pure"] == 1 and f["wb_n"] == 3
    assert f["wb_leagues"] == ["Premier League", "Serie A"]
    # Wide-midfield minutes over LW / RW role minutes of LW / RW rows (striker and mixed rows'
    # minutes excluded): (300 + 100) / (900 + 600).
    assert f["wide_mid_share"] == pytest.approx(400 / 1500)


def test_pool_facts_without_wide_rows_has_no_share():
    f = sm.pool_facts({(1, 1, 11, 27, "s"): _r("CB", {"Center Back": 900.0})}, LEAGUES)
    assert f["wide_mid_share"] is None and f["am_pure"] == 0 and f["wb_leagues"] == []


# ── check_proposal ───────────────────────────────────────────────────────────

def _row(group, label, league, combo=None, central=0.0, pos=None):
    return {"group": group, "label": label, "league": league, "combo": combo,
            "central": central, "pos_min": pos or {}}


ROWS = [
    _row("CB", "CB", "La Liga"), _row("CB", "CB", "Ligue 1"),
    _row("CB", "mixed", "Serie A", "CB/RB"),
    _row("FB-WB", "mixed", "Premier League", "CB/RB"),   # not the CB group's combo
    _row("ST", "ST", "La Liga"), _row("ST", "mixed", "Ligue 1", "RW/ST"),
    _row("DM-CM", "DM", "La Liga"),
    _row("AM-Winger", "LW", "Serie A", pos={"Left Wing": 600.0, "Left Midfield": 400.0}),
    _row("AM-Winger", "RW", "Premier League", pos={"Right Wing": 700.0, "Right Midfield": 300.0}),
    _row("AM-Winger", "mixed", "La Liga", "AM/RW", central=0.8),
]
EXPECTED = {
    "n CB": 2, "n ST": 1, "n DM": 1, "n LB": 0, "n RB": 0, "n CM": 0, "n AM": 0, "n LWB": 0,
    "n RWB": 0, "min league LB": 0, "min league RB": 0, "min league DM": 0, "min league ST": 0,
    "CB share of group %": 67, "ST share of group %": 50,
    "combo AM/RW": 1, "combo AM/LW": 0, "combo LW/RW": 0, "combo CB/RB": 1, "combo RW/ST": 1,
    "central AM-Winger": 1, "DM-CM rows": 1, "midfield combined": 2,
    "midfield min league": 0, "midfield max league": 2, "midfield added %": 50,
    "shared DNA metrics": 20, "wide slot min %": 60, "wide slot max %": 70,
}


def test_proposal_figures_that_match_pass(monkeypatch):
    monkeypatch.setattr(fr, "PROPOSAL_FIGURES", dict(EXPECTED))
    fr.check_proposal(ROWS, 20)


@pytest.mark.parametrize("key, wrong", [
    ("combo CB/RB", 2),          # counted across groups instead of within CB
    ("wide slot min %", 70), ("midfield added %", 51), ("n CB", 3), ("shared DNA metrics", 21),
])
def test_a_drifted_proposal_figure_fails(monkeypatch, key, wrong):
    monkeypatch.setattr(fr, "PROPOSAL_FIGURES", {**EXPECTED, key: wrong})
    with pytest.raises(SystemExit):
        fr.check_proposal(ROWS, 20)


# ── The end-naming threshold ─────────────────────────────────────────────────

def test_end_threshold_compares_unrounded_loadings():
    below, above = -0.14996, -0.15004
    assert f"{below:.3f}" == "-0.150" and f"{above:.3f}" == "-0.150"
    model = {"metrics": ["below", "above", "pos"], "components": [[below, above, 0.5]]}
    assert page.end_metrics(model, 0, -1) == ["above"]
    assert page.end_metrics(model, 0, 1) == ["pos"]
    model_pos = {"metrics": ["below", "above"], "components": [[-below, -above]]}
    assert page.end_metrics(model_pos, 0, 1) == ["above"]


def test_end_threshold_value_and_override():
    assert page.END_MIN_LOADING == 0.15
    model = {"metrics": ["m"], "components": [[-0.11]]}
    assert page.end_metrics(model, 0, -1) == []
    assert page.end_metrics(model, 0, -1, threshold=0.10) == ["m"]
