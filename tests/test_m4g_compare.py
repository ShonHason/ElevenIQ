"""
M4g1b compare-mode helpers (plan 005 M4g1b; spikes/m4g_compare.py), tested from their written
rules with hand-computed expected values:
- ranks: competition ranks, 1 + the number of candidates closer by more than 1e-12, so equal
  distances (exactly, or within float noise) share a rank;
- picks: the 5 largest |gap| then the 3 smallest, ties by metric index (= metric name order);
- pair_index: the row-major index of an unordered pair in the upper triangle;
- dist_code: the 4 decimals of d < 1 as an int; d that prints as 1.0000 or more is rejected
  (it cannot occur: pair weights sum to 1 and Hazen percentiles lie inside (0, 100));
- cell_words: the phrase table and indices, the withheld-family tag and the Defending flag;
- build: candidates exclude the player's own rows (rank 0 between them) and include partial
  rows; N counts the candidates;
- the withheld reason text equals tools/compare_page.DIM_STATUS_TEXT (both read as source text).
"""
from __future__ import annotations

import re

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4f_dna_reference as ref  # noqa: E402
import m4g_compare as cmpm  # noqa: E402


# ── ranks ────────────────────────────────────────────────────────────────────

def test_ranks_exact_tie_shares_a_rank_and_skips_the_next():
    got = cmpm.ranks([(0.5, "x"), (0.5, "y"), (0.1, "z"), (0.7, "w")])
    assert got == {"z": 1, "x": 2, "y": 2, "w": 4}


def test_ranks_float_noise_tie_shares_a_rank():
    # 0.2 and 0.2 + 1e-15 are the same distance computed two ways.
    got = cmpm.ranks([(0.3, "d"), (0.1, "a"), (0.2, "b"), (0.2 + 1e-15, "c")])
    assert got == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_ranks_beyond_the_tolerance_are_distinct():
    assert cmpm.ranks([(0.2 + 1e-9, "c"), (0.2, "b")]) == {"b": 1, "c": 2}


# ── picks ────────────────────────────────────────────────────────────────────

def test_picks_orders_by_absolute_gap():
    pa = [10, 90, 50, 50, 20, 30, 70, 51, 48]
    pb = [15, 40, 60, 50, 50, 50, 30, 50, 50]   # gaps 5, 50, 10, 0, 30, 20, 40, 1, 2
    assert cmpm.picks(pa, pb) == [1, 6, 4, 5, 2, 3, 7, 8]
    assert cmpm.picks(pb, pa) == [1, 6, 4, 5, 2, 3, 7, 8]


def test_picks_ties_go_by_metric_index():
    pa = [50] * 9
    pb = [60, 80, 20, 50, 40, 50, 55, 80, 51]   # gaps 10, 30, 30, 0, 10, 0, 5, 30, 1
    assert cmpm.picks(pa, pb) == [1, 2, 7, 0, 4, 3, 5, 8]


# ── pair_index ───────────────────────────────────────────────────────────────

def test_pair_index_small_table():
    want = {(0, 1): 0, (0, 2): 1, (0, 3): 2, (1, 2): 3, (1, 3): 4, (2, 3): 5}
    for (i, j), p in want.items():
        assert cmpm.pair_index(i, j, 4) == p
        assert cmpm.pair_index(j, i, 4) == p


def test_pair_index_enumerates_the_upper_triangle():
    n, p = 7, 0
    for i in range(n):
        for j in range(i + 1, n):
            assert cmpm.pair_index(i, j, n) == p
            p += 1


# ── dist_code ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("d, want", [(0.0412, 412), (0.0, 0), (0.00004, 0), (0.0025, 25),
                                     (0.9999, 9999)])
def test_dist_code(d, want):
    assert cmpm.dist_code(d) == want


@pytest.mark.parametrize("d", [0.99996, 1.0, 1.5])
def test_dist_code_rejects_one_or_more(d):
    with pytest.raises(SystemExit):
        cmpm.dist_code(d)


# ── cell_words ───────────────────────────────────────────────────────────────

# ADR 004 §13.8 (M4g4d): DM-CM Defending is mover_stability_inconclusive, the others
# possession_criteria_failed.
MOVER = ' <span class="muted">(team-influenced: player-vs-team test on club movers inconclusive)</span>'
POSSESSION = ' <span class="muted">(team-influenced: not shown to be free of team-possession effects)</span>'
PASSING = (' <span class="muted">(team-influenced: depends on the team&#x27;s passing '
           'style)</span>')


def test_cell_words_phrase_table_and_indices():
    got = cmpm.cell_words("DM-CM", [[95.0, 50.0], [3.2, 50.0]])
    assert got["pt"] == ["around average", "bottom 4%", "top 5%"]
    assert got["ph"] == [2, 0, 1, 0]


def test_cell_words_tags_withheld_families_dm_cm():
    got = cmpm.cell_words("DM-CM", [])
    at = {m: i for i, m in enumerate(ref.DNA["DM-CM"])}
    assert got["tag"][at["interceptions_p90"]] == MOVER
    assert got["def"][at["interceptions_p90"]] is True
    assert got["tag"][at["long_pass_completion_pct"]] == PASSING
    assert got["def"][at["long_pass_completion_pct"]] is False
    assert got["tag"][at["np_xg_p90"]] == ""
    assert got["def"][at["np_xg_p90"]] is False


def test_cell_words_st_passing_is_not_withheld():
    got = cmpm.cell_words("ST", [])
    at = {m: i for i, m in enumerate(ref.DNA["ST"])}
    assert got["tag"][at["pass_completion_pct"]] == ""
    assert got["tag"][at["blocks_p90"]] == POSSESSION
    assert got["def"][at["blocks_p90"]] is True


# ── build: candidates, own rows, partial rows ────────────────────────────────

def _row(pid: int, tid: int, p: float, missing: str | None = None) -> dict:
    cells = {m: {"pct_local": None if m == missing else p,
                 "metric_status": "below_min_attempts" if m == missing else "ok"}
             for m in ref.DNA["ST"]}
    return {"key": (pid, tid, 2, 27, "statsbomb"), "group": "ST", "cells": cells,
            "dna_status": "partial" if missing else "complete"}


def test_build_ranks_own_rows_and_partial_rows():
    # Every metric of a row at one percentile, so d = (Δ / 100)² whatever the families
    # (pair weights sum to 1). A and A2 are one player at two clubs; P is partial (not plotted).
    a, a2, b, c = _row(1, 10, 50), _row(1, 11, 55), _row(2, 10, 60), _row(3, 10, 70)
    part = _row(4, 10, 52, missing="aerial_win_pct")
    plotted = [a, a2, b, c]
    out = cmpm.build("ST", plotted, plotted + [part], {m: m for m in ref.DNA["ST"]}, [0.3, 0.2])
    # Distances: A–A2 .0025, A–B .01, A–C .04, A2–B .0025, A2–C .0225, B–C .01;
    # P: A .0004, A2 .0009, B .0064, C .0324.
    assert out["d"] == [25, 100, 400, 25, 225, 100]
    assert out["N"] == [3, 3, 4, 4]          # own rows left out, P counted
    assert out["r"] == [0, 0, 2, 3,          # A: P 1, B 2, C 3 (A2 not ranked)
                        0, 0, 2, 3,          # A2: P 1, B 2, C 3
                        3, 1, 0, 3,          # B: A2 1, P 2, A and C tie at .01 → 3
                        4, 2, 1, 0]          # C: B 1, A2 2, P 3, A 4
    assert out["pt"] == ["around average", "top 30%"]


# ── withheld reason text = the comparison page's ─────────────────────────────

def _string_constants(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    return {n.targets[0].id: n.value.value for n in tree.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)}


def test_withheld_words_match_the_comparison_page():
    statuses = {**_string_constants(ROOT / "src" / "eleveniq" / "analytics" / "dna.py"),
                **_string_constants(ROOT / "src" / "eleveniq" / "analytics" / "registry.py")}
    tree = ast.parse((ROOT / "tools" / "compare_page.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "DIM_STATUS_TEXT")
    assert isinstance(node.value, ast.Dict)
    page_text = {}
    for k, v in zip(node.value.keys, node.value.values):
        assert isinstance(k, ast.Attribute) and isinstance(v, ast.Constant)
        page_text[statuses[k.attr]] = v.value
    for status, words in cmpm.WITHHELD_WORDS.items():
        assert page_text[status] == words
    assert set(cmpm.WITHHELD_WORDS) == set(page_text) - {"no_directional_metric"}
    assert {s for groups in ref.WITHHELD.values() for s in groups.values()} <= set(cmpm.TAG_WORDS)
    assert set(cmpm.TAG_WORDS) == set(cmpm.WITHHELD_WORDS)


def test_withheld_text_carries_the_d5_status_and_the_underpowered_note():
    """ADR 004 §13.8 (M4g4d): DM-CM Defending reads its mover verdict with "; underpowered"
    (the compare page's wording); CB its possession outcome."""
    dm = cmpm.withheld_text("DM-CM")
    assert "Defending (player-vs-team test on club movers inconclusive; underpowered)" in dm
    assert "Passing (team-influenced; xPass deferred until after launch)" in dm
    assert "Defending (not shown to be free of team-possession effects)" in cmpm.withheld_text("CB")
    assert ref.UNDERPOWERED == {"DM-CM"}



# ── Defending wording: N2 / N3 (ADR 004 §14; user 2026-10-09) ────────────────────

N2 = "not shown to be free of team-possession effects"
DM_CM_REASON = "player-vs-team test on club movers inconclusive"
EXPECTED_STATE = ("That is the expected state until a second season adds evidence, not a finding "
                  "about the player.")
# Possession stated as fact (the sentence the M4g6d check found, and its relatives).
FACT_CLAIMS = ("depends heavily", "possession-dependent", "possession dependent",
               "how much the team has the ball", "rises with possession", "driven by possession",
               "possession-driven")


@pytest.mark.parametrize("group", ["CB", "FB-WB", "AM-Winger", "ST"])
def test_defending_note_uses_the_n2_wording(group):
    assert cmpm.defending_note(group) == (
        "Defending scores are withheld: these metrics are not shown to be free of "
        "team-possession effects. " + EXPECTED_STATE)


def test_defending_note_dm_cm_uses_its_own_reason_with_underpowered():
    assert cmpm.defending_note("DM-CM") == (
        "Defending scores are withheld: the player-vs-team test on club movers is inconclusive "
        "(underpowered). " + EXPECTED_STATE)


def test_defending_note_follows_the_reason_code_not_a_fixed_string(monkeypatch):
    monkeypatch.setitem(ref.WITHHELD["Defending"], "CB", "mover_stability_failed")
    assert "the player-vs-team test on club movers is failed" not in (cmpm.defending_note("ST") or "")
    assert cmpm.defending_note("CB").startswith(
        "Defending scores are withheld: the player-vs-team test on club movers is failed.")
    monkeypatch.delitem(ref.WITHHELD["Defending"], "CB")
    assert cmpm.defending_note("CB") is None


def test_defending_tags_use_the_withheld_words_exactly():
    for code, words in cmpm.WITHHELD_WORDS.items():
        if code != "pending_xpass_deferred":
            assert cmpm.TAG_WORDS[code] == words
    assert cmpm.TAG_WORDS["possession_criteria_failed"] == N2


def _defending_texts(page: str) -> list[str]:
    """Every Defending withheld text on a page: tags on Defending metrics are checked through
    the group's tag set, the footnote's "Defending (…)" and the note's clause."""
    import html as h
    t = h.unescape(page)
    return re.findall(r"Defending \(([^)]*)\)", t) + re.findall(r"Defending scores are withheld: ([^.]*)\.", t)


def _check_page(page: str, group: str) -> None:
    import html as h
    t = h.unescape(page).lower()
    for claim in FACT_CLAIMS:
        assert claim not in t, (group, claim)
    reason = N2 if group != "DM-CM" else DM_CM_REASON
    clause = (f"these metrics are {N2}" if group != "DM-CM"
              else "the player-vs-team test on club movers is inconclusive (underpowered)")
    for x in _defending_texts(page):
        assert x in (reason, reason + "; underpowered", clause), (group, x)
    tags = set(re.findall(r"team-influenced: ([^)<]*)", h.unescape(page)))
    assert tags <= {reason, "depends on the team's passing style"}, (group, tags)


@pytest.mark.parametrize("group", ["CB", "FB-WB", "DM-CM", "AM-Winger", "ST"])
def test_statics_and_tags_carry_no_possession_fact_and_the_right_reason(group):
    st = cmpm.statics(group, 20, 49)
    words = cmpm.cell_words(group, [])
    _check_page(st["defn"] + st["foot"] + "".join(words["tag"]), group)


@pytest.mark.integration
@pytest.mark.parametrize("group", ["cb", "fb_wb", "dm_cm", "am_winger", "st"])
def test_rendered_style_maps_carry_no_possession_fact_and_the_right_reason(group):
    path = ROOT / "data" / "processed" / "reports" / f"style_map_{group}.html"
    if not path.exists():
        pytest.skip("style maps not built")
    g = {"cb": "CB", "fb_wb": "FB-WB", "dm_cm": "DM-CM", "am_winger": "AM-Winger", "st": "ST"}[group]
    page = path.read_text()
    _check_page(page, g)
    assert _defending_texts(page)                                 # the check is not vacuous


def test_golden_compare_pages_carry_no_possession_fact():
    import html as h
    for f in (ROOT / "tests" / "fixtures" / "m4e_compare").glob("golden_*.html"):
        t = h.unescape(f.read_text())
        for claim in FACT_CLAIMS:
            assert claim not in t.lower(), (f.name, claim)
        for x in re.findall(r"withheld \(([^)]*)\)", t):
            assert x in (N2, DM_CM_REASON + "; underpowered",
                         "team-influenced; xPass deferred until after launch"), (f.name, x)
