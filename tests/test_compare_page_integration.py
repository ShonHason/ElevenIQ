"""
Integration tests for tools/compare_page.py on the 2015/16 data (plan 005 M4e/M4f4, ADR 004
§11.5, §12). They need data/processed/player_season_stats.parquet,
player_season_percentiles.parquet, player_dna.parquet and player_dna_dimensions.parquet.

- Golden: Kane (ST), Ben Arfa (AM-Winger, with D5 second-group ST rows) and Ighalo (ST,
  below the long-pass min_attempts) render byte-identical to the committed golden page
  (mixed groups: no radar). A second golden, Walcott vs Özil (AM-Winger, Arsenal), carries
  the radar, with Walcott's missing long pass completion as a gap (ADR 004 §12.2).
  `pytest --update-golden` rewrites them after printing a diff summary; a golden change must
  be called out in the commit report.
- Trace: every number on the page is inside a span naming its source, and each span's text
  equals the documented formatting of that parquet cell, identity cell, registry field or
  config label; no digit is left outside them; every empty percentile shows a reason.
  Dimension scores (data-dim) equal their parquet cell, and "based on N" equals the member
  count. Every radar vertex (data-dna) sits at RADAR_R · pct_local / 100 on its axis, and
  every DNA metric is either a vertex or a gap marker.
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
from eleveniq.analytics import registry as R
from eleveniq.analytics.registry import REGISTRY, SPECS
from tools import compare_page as P

_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "tests" / "fixtures" / "m4e_compare" / "golden_kane_benarfa_ighalo.html"
_GOLDEN_PLAYERS = ("10955", "16462", "5458")   # Kane, Ben Arfa, Ighalo (season 27)
_RADAR_GOLDEN = _ROOT / "tests" / "fixtures" / "m4e_compare" / "golden_radar_walcott_ozil.html"
_RADAR_PLAYERS = ("3668", "3496")              # Walcott (partial AM-Winger), Özil
_SOURCE_ATTRS = ("data-src", "data-id", "data-reg", "data-cfg", "data-dim", "data-static")
_PAGES = [_GOLDEN_PLAYERS, _RADAR_PLAYERS, ("N'Golo Kant", "Busquets"), ("Özil", "Payet"),
          ("Lionel Andrés Messi", "Neymar"), ("John Stones", "Wes Morgan")]

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not all(p.exists() for p in (
                  P.PERCENTILES_PATH, P.SEASON_PATH, P.DNA_PATH, P.DIMENSIONS_PATH)),
                  reason="percentiles, season or DNA parquets not built")]


@pytest.fixture(scope="module")
def golden_page() -> str:
    return P.generate(27, _GOLDEN_PLAYERS)[0]


@pytest.fixture(scope="module")
def radar_page() -> str:
    return P.generate(27, _RADAR_PLAYERS)[0]


@pytest.fixture(scope="module")
def dna() -> pl.DataFrame:
    return pl.read_parquet(P.DNA_PATH).filter(pl.col("season_id") == 27)


@pytest.fixture(scope="module")
def dims() -> pl.DataFrame:
    return pl.read_parquet(P.DIMENSIONS_PATH).filter(pl.col("season_id") == 27)


@pytest.fixture(scope="module")
def pct() -> pl.DataFrame:
    return pl.read_parquet(P.PERCENTILES_PATH).filter(pl.col("season_id") == 27)


@pytest.fixture(scope="module")
def season() -> pl.DataFrame:
    return pl.read_parquet(P.SEASON_PATH).filter(pl.col("season_id") == 27)


# ── golden ────────────────────────────────────────────────────────────────────────

def _cells(page: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for attr, ref, text in re.findall(r'data-(src|dim)="([^"]+)">([^<]*)<', page):
        out.setdefault(f"{attr}:{ref}", []).append(text)
    return out


def _diff_summary(old: str, new: str) -> str:
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    a, b = _cells(old), _cells(new)
    changed = [f"  {k}: {a.get(k)} -> {b.get(k)}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return (f"golden diff: +{added} / -{removed} lines; {len(changed)} data-src/data-dim cells "
            "changed\n"
            + "\n".join(changed[:200]))


def _check_golden(path: Path, page: str, update_golden: bool, capsys) -> None:
    if update_golden:
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        with capsys.disabled():
            print(f"\n{path.name}: " + (_diff_summary(old, page) if old != page
                                        else "golden diff: unchanged"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
    assert path.exists(), "no golden page: run pytest -m integration --update-golden"
    expected = path.read_text(encoding="utf-8")
    assert page == expected, _diff_summary(expected, page)


def test_golden_page(golden_page, update_golden, capsys):
    _check_golden(_GOLDEN, golden_page, update_golden, capsys)


def test_radar_golden_page(radar_page, update_golden, capsys):
    _check_golden(_RADAR_GOLDEN, radar_page, update_golden, capsys)


def test_radar_golden_covers_the_dna_rules(radar_page, golden_page):
    assert "No radar: the players are in different position groups" in golden_page
    assert 'data-gap="3668|1|2|27|statsbomb|long_pass_completion_pct"' in radar_page
    text = re.sub(r"<[^>]+>", "", radar_page)
    assert "withheld (" in text and "withheld until" not in text and "based on 1 metric:" in text
    # Passing withheld for both AM-Wingers (§12.9), incl. Walcott who also lacks the metric
    assert "team-influenced; xPass deferred until after launch" in text
    assert "|Passing|score" not in radar_page


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
        if src and tag in ("span", "tspan"):
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
    if field == "reasons":                          # the code, through the one text table (G2)
        return ", ".join(R.reason_text(r) for r in cell.reasons)
    if field == "fixed_display.reason":
        fixed = P.SPECS_BY_COLUMN[metric].fixed_display
        assert fixed is not None
        return fixed.reason
    return str(getattr(cell, field))


def _expected_dim(dims: pl.DataFrame, ref: str) -> str:
    pid, tid, cid, sid, source, family, column = ref.split("|")
    row = dims.filter((pl.col("player_id") == int(pid)) & (pl.col("team_id") == int(tid))
                      & (pl.col("competition_id") == int(cid)) & (pl.col("season_id") == int(sid))
                      & (pl.col("source") == source) & (pl.col("family") == family))
    assert row.height == 1, ref
    v = row[column][0]
    if column == "members":
        return str(len(v))
    assert column == "score", ref
    return P.format_pct(v)


def _expected_cfg(ref: str) -> str:
    field, cid, sid = ref.split("|")
    assert field == "competition_label"
    return next(cs.label for cs in config.COMPETITION_SEASONS
                if cs.competition_id == int(cid) and cs.season_id == int(sid))


@pytest.mark.parametrize("players", _PAGES)
def test_every_number_traces_to_a_source(players, pct, season, dims):
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
        elif attr == "data-dim":
            assert text == _expected_dim(dims, ref), ref
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


@pytest.mark.parametrize("players", _PAGES)
def test_radar_vertices_trace_to_player_dna(players, dna):
    """Each vertex sits at its stored pct_local on its own axis; its tooltip shows the same
    value and n; every DNA metric of every drawn player is a vertex or a gap, never both."""
    page, built = P.generate(27, players)
    groups = {p.group for p in built}
    vertices = re.findall(r'<circle class="rd-v (s\d)" data-dna="([^"]+)" cx="([-\d.]+)" '
                          r'cy="([-\d.]+)" r="[\d.]+"><title>([^<]*)</title>', page)
    gaps = set(re.findall(r'data-gap="([^"]+)"', page))
    if len(groups) > 1:
        assert not vertices and not gaps
        return
    axes = P.radar_axes(next(iter(groups)))
    n = len(axes)
    seen = set()
    for _, ref, x, y, title in vertices:
        pid, tid, cid, sid, source, metric, column = ref.split("|")
        assert column == "pct_local"
        row = dna.filter((pl.col("player_id") == int(pid)) & (pl.col("team_id") == int(tid))
                         & (pl.col("competition_id") == int(cid)) & (pl.col("source") == source)
                         & (pl.col("metric") == metric))
        assert row.height == 1 and row["metric_status"][0] == "ok", ref
        p, n_local = row["pct_local"][0], row["n_local"][0]
        ex, ey = P.radar_point(axes.index(metric), n, p)
        assert abs(float(x) - ex) < 0.006 and abs(float(y) - ey) < 0.006, ref
        assert title.endswith(f": {P.format_pct(p)} (n {n_local})"), title
        seen.add(ref.rsplit("|", 1)[0])
    expected = set()
    for p in built:
        rows = dna.filter((pl.col("player_id") == p.identity["player_id"])
                          & (pl.col("team_id") == p.identity["team_id"])
                          & (pl.col("competition_id") == p.identity["competition_id"]))
        assert set(rows["metric"]) == set(axes)
        expected |= {"|".join(str(r[k]) for k in P.KEY) + f"|{r['metric']}" for r in rows.to_dicts()}
        for r in rows.filter(pl.col("metric_status") != "ok").to_dicts():
            assert "|".join(str(r[k]) for k in P.KEY) + f"|{r['metric']}" in gaps
    assert not seen & gaps
    assert seen | gaps == expected


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
