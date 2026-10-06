"""
Integration tests for tools/compare_page.py on the 2015/16 data (plan 005 M4e, ADR 004 §11.5).
They need data/processed/player_season_stats.parquet and player_season_percentiles.parquet.

- Golden: Kane (ST), Ben Arfa (AM-Winger, with D5 second-group ST rows) and Ighalo (ST,
  below the long-pass min_attempts) render byte-identical to the committed golden page.
  `pytest --update-golden` rewrites it after printing a diff summary; a golden change must
  be called out in the commit report.
- Trace: every number on the page is inside a span naming its source, and each span's text
  equals the documented formatting of that parquet cell, identity cell, registry field or
  config label; no digit is left outside them; every empty percentile shows a reason.
- Reference metric: the n of POPULATION_REFERENCE_METRIC equals the number of scored rows in
  each local and global population after the D3 dedupe.
"""
from __future__ import annotations

import difflib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics.registry import REGISTRY, SPECS
from tools import compare_page as P

_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "tests" / "fixtures" / "m4e_compare" / "golden_kane_benarfa_ighalo.html"
_GOLDEN_PLAYERS = ("10955", "16462", "5458")   # Kane, Ben Arfa, Ighalo (season 27)
_SOURCE_ATTRS = ("data-src", "data-id", "data-reg", "data-cfg", "data-static")

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not (P.PERCENTILES_PATH.exists() and P.SEASON_PATH.exists()),
                                 reason="percentiles or season parquet not built")]


@pytest.fixture(scope="module")
def golden_page() -> str:
    return P.generate(27, _GOLDEN_PLAYERS)[0]


@pytest.fixture(scope="module")
def pct() -> pl.DataFrame:
    return pl.read_parquet(P.PERCENTILES_PATH).filter(pl.col("season_id") == 27)


@pytest.fixture(scope="module")
def season() -> pl.DataFrame:
    return pl.read_parquet(P.SEASON_PATH).filter(pl.col("season_id") == 27)


# ── golden ────────────────────────────────────────────────────────────────────────

def _cells(page: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ref, text in re.findall(r'data-src="([^"]+)">([^<]*)<', page):
        out.setdefault(ref, []).append(text)
    return out


def _diff_summary(old: str, new: str) -> str:
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    a, b = _cells(old), _cells(new)
    changed = [f"  {k}: {a.get(k)} -> {b.get(k)}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return (f"golden diff: +{added} / -{removed} lines; {len(changed)} data-src cells changed\n"
            + "\n".join(changed[:200]))


def test_golden_page(golden_page, update_golden, capsys):
    if update_golden:
        old = _GOLDEN.read_text(encoding="utf-8") if _GOLDEN.exists() else ""
        with capsys.disabled():
            print("\n" + (_diff_summary(old, golden_page) if old != golden_page
                          else "golden diff: unchanged"))
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(golden_page, encoding="utf-8")
    assert _GOLDEN.exists(), "no golden page: run pytest -m integration --update-golden"
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert golden_page == expected, _diff_summary(expected, golden_page)


def test_golden_covers_the_display_rules(golden_page):
    assert "not directly comparable" in golden_page              # mixed groups (ST, AM-Winger)
    assert "&gt;99</span>" in golden_page                         # Ben Arfa second-group cell
    assert "as ST — display only" in golden_page                  # D5 section
    assert "below the minimum of ≥" in golden_page                # Ighalo long passes
    assert "not testable (league populations" in golden_page                   # Kane long passes d9_undefined


# ── trace ─────────────────────────────────────────────────────────────────────────

class _Spans(HTMLParser):
    """Spans carrying a source attribute, and the visible text outside them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.spans: list[tuple[str, str, str]] = []    # (attr, ref, text)
        self.loose: list[str] = []
        self.scopes: list[str] = []                    # the inner HTML marker per scope div
        self._stack: list[tuple[str, str | None, str | None]] = []
        self._skip = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("style", "title"):
            self._skip += 1
        src = next(((k, a[k]) for k in _SOURCE_ATTRS if k in a), None)
        if src and tag == "span":
            self._stack.append((tag, src[0], src[1]))
            self._text = []
        else:
            self._stack.append((tag, None, None))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "title"):
            self._skip -= 1
        while self._stack:
            t, attr, ref = self._stack.pop()
            if attr is not None:
                self.spans.append((attr, ref or "", "".join(self._text)))
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if any(attr for _, attr, _ in self._stack):
            self._text.append(data)
        else:
            self.loose.append(data)


def _expected_src(pct: pl.DataFrame, ref: str) -> str:
    pid, tid, cid, sid, source, role, metric, column = ref.split("|")
    row = pct.filter((pl.col("player_id") == int(pid)) & (pl.col("team_id") == int(tid))
                     & (pl.col("competition_id") == int(cid)) & (pl.col("season_id") == int(sid))
                     & (pl.col("source") == source) & (pl.col("cohort_role") == role)
                     & (pl.col("metric") == metric))
    assert row.height == 1, ref
    v = row[column][0]
    if column == "value":
        return P.format_value(metric, v)
    if column.startswith("pct_"):
        return P.format_pct(v)
    if column == "percentile_status":
        return P.status_text(v)
    return P.format_number(v)


def _expected_id(season: pl.DataFrame, ref: str) -> str:
    pid, tid, cid, sid, source, column = ref.split("|")
    row = season.filter((pl.col("player_id") == int(pid)) & (pl.col("team_id") == int(tid))
                        & (pl.col("competition_id") == int(cid)) & (pl.col("season_id") == int(sid))
                        & (pl.col("source") == source))
    assert row.height == 1, ref
    v = row[column][0]
    return v if isinstance(v, str) else P.format_number(v)


def _expected_reg(ref: str) -> str:
    metric, group, field, *index = ref.split("|")
    cell = REGISTRY.cell(metric, group)
    if field == "notes":
        return cell.notes[int(index[0])]
    if field == "reasons":
        return ", ".join(cell.reasons)
    if field == "fixed_display.reason":
        fixed = P.SPECS_BY_COLUMN[metric].fixed_display
        assert fixed is not None
        return fixed.reason
    return str(getattr(cell, field))


def _expected_cfg(ref: str) -> str:
    field, cid, sid = ref.split("|")
    assert field == "competition_label"
    return next(cs.label for cs in config.COMPETITION_SEASONS
                if cs.competition_id == int(cid) and cs.season_id == int(sid))


@pytest.mark.parametrize("players", [
    _GOLDEN_PLAYERS,
    ("N'Golo Kant", "Busquets"), ("Özil", "Payet"),
    ("Lionel Andrés Messi", "Neymar"), ("John Stones", "Wes Morgan"),
])
def test_every_number_traces_to_a_source(players, pct, season):
    page, _ = P.generate(27, players)
    parser = _Spans()
    parser.feed(page)
    assert parser.spans
    for attr, ref, text in parser.spans:
        if attr == "data-src":
            assert text == _expected_src(pct, ref), ref
        elif attr == "data-id":
            assert text == _expected_id(season, ref), ref
        elif attr == "data-reg":
            assert text == _expected_reg(ref), ref
        elif attr == "data-cfg":
            assert text == _expected_cfg(ref), ref
        else:
            assert text in P.STATIC_REFS, text
    loose = re.findall(r"\d", "".join(parser.loose))
    assert not loose, "digits outside traced spans: " + "".join(parser.loose)[:500]


@pytest.mark.parametrize("players", [_GOLDEN_PLAYERS, ("John Stones", "Wes Morgan")])
def test_every_empty_percentile_shows_a_reason(players):
    page, _ = P.generate(27, players)
    chunks = page.split('<div class="scope')[1:]
    assert chunks
    for chunk in chunks:
        body = chunk.split("</div>")[0]
        has_pct = re.search(r'\|pct_(local|global)">[^<]+<', body)
        reason = re.search(r'class="reason">([^<]+)', body)
        assert has_pct or (reason and reason.group(1).strip()), body[:300]


# ── reference metric N ────────────────────────────────────────────────────────────

def test_reference_metric_n_equals_population_size(pct):
    ref = pct.filter((pl.col("metric") == P.POPULATION_REFERENCE_METRIC)
                     & (pl.col("percentile_status") == "scored"))
    assert ref["n_local"].null_count() == 0 and ref["n_global"].null_count() == 0
    members = ref.filter(pl.col("cohort_role") == "primary")
    local = members.group_by("position_group", "competition_id").agg(
        pl.col("player_id").n_unique().alias("size"))
    glob = members.group_by("position_group").agg(pl.col("player_id").n_unique().alias("size"))
    checked = ref.join(local, on=["position_group", "competition_id"], how="left").join(
        glob, on="position_group", how="left", suffix="_global")
    bad_local = checked.filter(pl.col("n_local") != pl.col("size"))
    bad_global = checked.filter(pl.col("n_global") != pl.col("size_global"))
    assert bad_local.is_empty(), bad_local.head(5)
    assert bad_global.is_empty(), bad_global.head(5)


def test_every_scored_row_has_the_reference_metric(pct):
    scored = pct.filter((pl.col("percentile_status") == "scored")).select(
        "player_id", "team_id", "competition_id", "cohort_role").unique()
    ref = pct.filter(pl.col("metric") == P.POPULATION_REFERENCE_METRIC).select(
        "player_id", "team_id", "competition_id", "cohort_role", "value")
    joined = scored.join(ref, on=["player_id", "team_id", "competition_id", "cohort_role"], how="left")
    assert joined["value"].null_count() == 0
    assert {s.column for s in SPECS} >= {P.POPULATION_REFERENCE_METRIC}
