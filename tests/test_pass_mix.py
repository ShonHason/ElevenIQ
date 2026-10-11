"""Completion above expected (plan 006 pre-registration, 2026-10-10): bucket rules, the pass
counting stage, the cohort / expected-value / check maths and the style tags. Synthetic only."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics import pass_mix as PM
from eleveniq.export import build_export as B
from eleveniq.export import words as W
from eleveniq.metrics import pass_mix as M
from eleveniq.pipeline import build_pass_mix as S

KEY = ("player_id", "team_id", "competition_id", "season_id", "source")


# ── metrics.pass_mix ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("length,want", [
    (0.0, "short"), (14.99, "short"), (15.0, "medium"), (29.99, "medium"),
    (30.0, "long"), (80.0, "long")])
def test_length_buckets_are_lower_inclusive_at_15_and_30(length, want):
    assert M.length_bucket(length) == want
    assert config.LONG_PASS_THRESHOLD_UNITS == 30


def test_negative_length_refused():
    with pytest.raises(ValueError):
        M.length_bucket(-0.1)


@pytest.mark.parametrize("angle,want", [
    (0.0, "forward"), (math.pi / 4, "forward"), (-math.pi / 4, "forward"),
    (math.pi / 4 + 1e-3, "sideways"), (-math.pi / 2, "sideways"), (math.pi / 2, "sideways"),
    (3 * math.pi / 4 - 1e-3, "sideways"), (3 * math.pi / 4, "backward"),
    (-3 * math.pi / 4, "backward"), (math.pi, "backward"),
    (3.1415927, "backward")])         # StatsBomb's 7-decimal rounding of pi exceeds math.pi
def test_direction_buckets_are_symmetric_and_inclusive_at_the_edges(angle, want):
    assert M.direction_bucket(angle) == want


def test_angle_beyond_pi_refused():
    with pytest.raises(ValueError):
        M.direction_bucket(3.2)


@pytest.mark.parametrize("pass_data,want", [
    ({}, (True, True)),
    ({"outcome": None}, (True, True)),
    ({"outcome": {"name": "Incomplete"}}, (True, False)),
    ({"outcome": {"name": "Out"}}, (True, False)),
    ({"outcome": {"name": "Pass Offside"}}, (True, False)),
    ({"outcome": {"name": "Unknown"}}, (False, False)),
    ({"outcome": {"name": "Injury Clearance"}}, (False, False))])
def test_outcome_counts_follow_pass_completion_pct(pass_data, want):
    assert M.outcome_counts(pass_data) == want


# ── pipeline.build_pass_mix.count_match / reconcile ───────────────────────────────────
def _pass(pid, tid, length=10.0, angle=0.0, outcome=None, ptype=None):
    p = {"length": length, "angle": angle}
    if outcome:
        p["outcome"] = {"name": outcome}
    if ptype:
        p["type"] = {"name": ptype}
    return {"type": {"name": "Pass"}, "player": {"id": pid}, "team": {"id": tid}, "pass": p}


def _count(events):
    acc: dict = defaultdict(Counter)
    S.count_match(events, 11, 27, "statsbomb", acc)
    return acc


def test_count_match_buckets_completion_and_leaves_out_unknowns_and_restarts():
    ev = [
        _pass(1, 10, 10, 0.0),                                    # short forward, completed
        _pass(1, 10, 10, 0.0, "Incomplete"),                      # short forward, missed
        _pass(1, 10, 35, math.pi, "Out"),                         # long backward, missed
        _pass(1, 10, 20, math.pi / 2),                            # medium sideways, completed
        _pass(1, 10, 10, 0.0, "Unknown"),                         # open play, not counted
        _pass(1, 10, 10, 0.0, "Injury Clearance"),                # open play, not counted
        _pass(1, 10, 10, 0.0, ptype="Throw-in"),                  # restart: not open play at all
        _pass(1, 10, 10, 0.0, ptype="Corner"),
        {"type": {"name": "Shot"}, "player": {"id": 1}, "team": {"id": 10}},
        {"type": {"name": "Pass"}, "team": {"id": 10}, "pass": {"length": 5, "angle": 0}},
    ]
    c = _count(ev)[(1, 10, 11, 27, "statsbomb")]
    assert c["open_play_all"] == 6
    assert (c["pm_short_forward_att"], c["pm_short_forward_cmp"]) == (2, 1)
    assert (c["pm_long_backward_att"], c["pm_long_backward_cmp"]) == (1, 0)
    assert (c["pm_medium_sideways_att"], c["pm_medium_sideways_cmp"]) == (1, 1)
    assert sum(v for k, v in c.items() if k.endswith("_att")) == 4


def test_count_match_missing_length_is_reported_but_still_open_play():
    ev = [_pass(1, 10, length=None), _pass(1, 10, length=None, outcome="Unknown")]  # unknown: not reported
    ev[0]["pass"].pop("length")
    c = _count(ev)[(1, 10, 11, 27, "statsbomb")]
    assert (c["open_play_all"], c["missing_length"]) == (2, 1)
    assert not any(k.endswith("_att") and v for k, v in c.items())


def test_count_match_missing_angle_stops_the_build():
    """Fix round (stats-reviewer): the pre-registration leaves out only passes without a
    length, so a pass without an angle (none in 2015/16) stops the build for a decision."""
    ev = [_pass(1, 10)]
    ev[0]["pass"].pop("angle")
    with pytest.raises(S.PassMixError):
        _count(ev)


def test_count_match_keeps_a_player_on_two_teams_apart():
    acc = _count([_pass(1, 10), _pass(1, 20), _pass(1, 20)])
    assert acc[(1, 10, 11, 27, "statsbomb")]["open_play_all"] == 1
    assert acc[(1, 20, 11, 27, "statsbomb")]["open_play_all"] == 2


def _season_keys(*rows):
    return pl.DataFrame([dict(zip(KEY, k), open_play_passes_attempted=n) for k, n in rows],
                        schema={**{c: pl.Int64 for c in KEY[:4]}, "source": pl.Utf8,
                                "open_play_passes_attempted": pl.Int64})


def _mix_keys(*rows):
    return pl.DataFrame([dict(zip(KEY, k), pm_open_play_all=n) for k, n in rows],
                        schema={**{c: pl.Int64 for c in KEY[:4]}, "source": pl.Utf8,
                                "pm_open_play_all": pl.Int32})


def test_reconcile_accepts_equal_counts_and_a_zero_pass_season_row_without_a_mix_row():
    k1, k2 = (1, 10, 11, 27, "statsbomb"), (2, 10, 11, 27, "statsbomb")
    S.reconcile(_mix_keys((k1, 5)), _season_keys((k1, 5), (k2, 0)))


@pytest.mark.parametrize("season_n,mix_n", [(5, 4), (5, 6)])
def test_reconcile_refuses_any_difference(season_n, mix_n):
    k = (1, 10, 11, 27, "statsbomb")
    with pytest.raises(S.PassMixError):
        S.reconcile(_mix_keys((k, mix_n)), _season_keys((k, season_n)))


def test_reconcile_refuses_a_mix_row_the_season_parquet_does_not_have_and_vice_versa():
    k1, k2 = (1, 10, 11, 27, "statsbomb"), (2, 10, 11, 27, "statsbomb")
    with pytest.raises(S.PassMixError):
        S.reconcile(_mix_keys((k1, 5), (k2, 3)), _season_keys((k1, 5)))
    with pytest.raises(S.PassMixError):
        S.reconcile(_mix_keys((k1, 5)), _season_keys((k1, 5), (k2, 3)))


# ── analytics.pass_mix.compute / choose ───────────────────────────────────────────────
MINS = {g: 1 for g in PM.GROUPS}
CELLS = [(ln, d) for ln in M.LENGTHS for d in M.DIRECTIONS]


def _frames(players):
    """players: dict(pid, group, cells={(len, dir): (att, cmp)}, pool, gate, share, comp)."""
    season, mix = [], []
    for p in players:
        comp = p.get("comp", 11)
        key = {"player_id": p["pid"], "team_id": 1, "competition_id": comp, "season_id": 27,
               "source": "statsbomb"}
        share = p.get("share", 0.5)
        season.append({**key, "position_group": p["group"],
                       "in_percentile_pool": p.get("pool", True),
                       "meets_min_minutes": p.get("gate", True),
                       "team_open_play_passes_while_on": int(share * 1000),
                       "opponent_open_play_passes_while_on": 1000 - int(share * 1000)})
        if p.get("cells"):
            row = dict(key)
            for ln, d in CELLS:
                a, c = p["cells"].get((ln, d), (0, 0))
                row[M.bucket_column(ln, d, "att")], row[M.bucket_column(ln, d, "cmp")] = a, c
            mix.append(row)
    return pl.DataFrame(season), pl.DataFrame(mix)


def _k(pid, comp=11):
    return (pid, 1, comp, 27, "statsbomb")


@pytest.fixture
def small_cohort(monkeypatch):
    monkeypatch.setattr(config, "MIN_COHORT_SIZE", 3)


def test_expected_completion_value_and_percentile_hand_computed(small_cohort):
    # short rate (9+5)/20 = .7; long rate (5+10)/20 = .75
    ps = [dict(pid=1, group="CB", cells={("short", "forward"): (10, 9)}),
          dict(pid=2, group="CB", cells={("short", "forward"): (10, 5), ("long", "forward"): (10, 5)}),
          dict(pid=3, group="CB", cells={("long", "forward"): (10, 10)})]
    out, _ = PM.compute(*_frames(ps), 1, MINS)
    a, b, c = out[_k(1)], out[_k(2)], out[_k(3)]
    assert (a.actual, a.expected, a.value) == pytest.approx((90.0, 70.0, 20.0))
    assert (b.actual, b.expected, b.value) == pytest.approx((50.0, 72.5, -22.5))
    assert c.value == pytest.approx(25.0)
    assert [r.status for r in (a, b, c)] == ["ok"] * 3
    assert [r.n_local for r in (a, b, c)] == [3, 3, 3]
    assert (c.pct_local, a.pct_local, b.pct_local) == pytest.approx((100 * 2.5 / 3, 50.0, 100 * 0.5 / 3))


def test_spec2_pools_per_length_and_direction_cell(small_cohort):
    """Same passes, spec 2: each cell has a single passer, so everyone is exactly as expected."""
    ps = [dict(pid=1, group="CB", cells={("short", "forward"): (10, 9)}),
          dict(pid=2, group="CB", cells={("short", "sideways"): (10, 5), ("long", "forward"): (10, 5)}),
          dict(pid=3, group="CB", cells={("long", "backward"): (10, 10)})]
    out, _ = PM.compute(*_frames(ps), 2, MINS)
    assert [out[_k(i)].value for i in (1, 2, 3)] == pytest.approx([0.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        PM.buckets(3)


def test_statuses_and_rows_outside_the_population_keep_a_value_but_no_rank(small_cohort):
    base = {("short", "forward"): (10, 10)}
    ps = [dict(pid=i, group="CB", cells={("short", "forward"): (10, 6 + i)}) for i in (1, 2, 3)]
    ps += [
        dict(pid=10, group="CB", gate=False, cells=base),                      # below the gate
        dict(pid=11, group="CB", cells={("short", "forward"): (0, 0)}),        # no counted passes
        dict(pid=12, group="CB", pool=False, cells=base),                      # not in the pool
        dict(pid=13, group="GK", cells=base),
        dict(pid=14, group="CB", gate=False, cells={("long", "forward"): (5, 5)}),  # unseen bucket
        dict(pid=15, group="CB"),                                              # no mix row at all
    ]
    out, _ = PM.compute(*_frames(ps), 1, MINS)
    assert out[_k(10)].status == "below_gate" and out[_k(10)].pct_local is None
    assert out[_k(10)].value == pytest.approx(100.0 - 80.0)     # pooled rate (7+8+9)/30 = .8
    assert out[_k(11)].status == "below_min_attempts"
    assert out[_k(11)].value is None and out[_k(11)].counted == 0
    assert out[_k(12)].status == "not_in_pool" and out[_k(12)].value is None
    assert out[_k(13)].status == "goalkeeper" and out[_k(13)].value is None
    assert out[_k(14)].status == "below_gate" and out[_k(14)].value is None   # bucket never attempted
    assert out[_k(15)].value is None and out[_k(15)].counted == 0
    assert all(out[_k(i)].status == "ok" for i in (1, 2, 3))


def test_attempt_minimum_keeps_a_row_out_of_cohort_pool_and_rank(small_cohort):
    ps = [dict(pid=i, group="CB", cells={("short", "forward"): (10, 5 + i)}) for i in (1, 2, 3)]
    ps.append(dict(pid=9, group="CB", cells={("short", "forward"): (4, 4)}))      # 4 < 5
    out, _ = PM.compute(*_frames(ps), 1, {**MINS, "CB": 5})
    assert out[_k(9)].status == "below_min_attempts" and out[_k(9)].pct_local is None
    # the short row's 4 perfect passes do not enter the pooled rate (6+7+8)/30 = .7
    assert out[_k(1)].expected == pytest.approx(70.0)
    assert out[_k(9)].value == pytest.approx(30.0)


def test_cohort_under_min_size_shows_values_but_no_percentile(monkeypatch):
    monkeypatch.setattr(config, "MIN_COHORT_SIZE", 5)
    ps = [dict(pid=i, group="CB", cells={("short", "forward"): (10, 5 + i)}) for i in (1, 2, 3, 4)]
    out, _ = PM.compute(*_frames(ps), 1, MINS)
    assert all(r.status == "cohort_too_small" and r.pct_local is None and r.value is not None
               for r in out.values())
    assert out[_k(1)].n_local == 4


def test_leagues_and_groups_are_pooled_separately(small_cohort):
    ps = [dict(pid=i, group="CB", comp=11, cells={("short", "forward"): (10, 5 + i)})
          for i in (1, 2, 3)]
    ps += [dict(pid=20 + i, group="CB", comp=2, cells={("short", "forward"): (10, 2 + i)})
           for i in (1, 2, 3)]
    ps += [dict(pid=30 + i, group="ST", comp=11, cells={("short", "forward"): (10, 9)})
           for i in (1, 2, 3)]
    out, _ = PM.compute(*_frames(ps), 1, MINS)
    assert out[_k(1)].expected == pytest.approx(70.0)          # league 11 CB only
    assert out[_k(21, 2)].expected == pytest.approx(40.0)
    assert out[_k(31)].expected == pytest.approx(90.0)


def _four(shares):
    ps = [dict(pid=i + 1, group="CB", share=s, cells={("short", "forward"): (10, 5 + i)})
          for i, s in enumerate(shares)]
    return _frames(ps)


def test_check_fails_when_value_tracks_possession_and_passes_when_unrelated(monkeypatch):
    monkeypatch.setattr(config, "MIN_COHORT_SIZE", 4)
    _, chk = PM.compute(*_four([0.4, 0.5, 0.6, 0.7]), 1, MINS)
    assert chk["CB"].mean_rho == pytest.approx(1.0) and not chk["CB"].passes
    _, chk = PM.compute(*_four([0.7, 0.6, 0.5, 0.4]), 1, MINS)
    assert chk["CB"].mean_rho == pytest.approx(-1.0) and not chk["CB"].passes   # absolute value
    _, chk = PM.compute(*_four([0.5, 0.7, 0.4, 0.6]), 1, MINS)                  # Spearman rho 0
    assert chk["CB"].mean_rho == pytest.approx(0.0, abs=1e-9) and chk["CB"].passes


def test_check_is_not_passed_without_a_defined_rho(monkeypatch):
    """Constant possession (rho undefined), a league too small for rho, and no cohort at all
    never pass by default."""
    monkeypatch.setattr(config, "MIN_COHORT_SIZE", 4)
    _, chk = PM.compute(*_four([0.5] * 4), 1, MINS)
    assert not chk["CB"].passes
    season, mix = _four([0.5, 0.7, 0.4, 0.6])
    _, chk = PM.compute(season.with_columns(pl.lit(False).alias("meets_min_minutes")), mix, 1, MINS)
    assert chk["CB"].rhos == {} and chk["CB"].mean_rho is None and not chk["CB"].passes
    assert chk["ST"].mean_rho is None and not chk["ST"].passes


def test_undefined_rho_is_none_not_nan(monkeypatch):
    """Constant possession share: the Spearman rho is undefined and must read None (NaN would
    leak into the report / JSON and skew the mean over leagues)."""
    monkeypatch.setattr(config, "MIN_COHORT_SIZE", 4)
    _, chk = PM.compute(*_four([0.5] * 4), 1, MINS)
    assert chk["CB"].rhos == {11: None} and chk["CB"].mean_rho is None


def test_possession_share_zero_denominator_and_nulls():
    f = PM.possession_share
    assert f({"team_open_play_passes_while_on": 0, "opponent_open_play_passes_while_on": 0}) is None
    assert f({"team_open_play_passes_while_on": None, "opponent_open_play_passes_while_on": 5}) is None
    assert f({"team_open_play_passes_while_on": 30, "opponent_open_play_passes_while_on": 10}) == 0.75


def _chk(passes):
    return PM.GroupCheck({}, 0.0 if passes else None, passes)


def test_choose_prefers_spec1_computes_spec2_only_on_failure_and_can_choose_none(monkeypatch):
    calls = []

    def fake(season, mix, spec, mins):
        calls.append(spec)
        ok = {1: {"CB": True, "FB-WB": False, "DM-CM": False}, 2: {"FB-WB": True}}[spec]
        return {spec: {}}, {g: _chk(ok.get(g, False)) for g in PM.GROUPS}

    monkeypatch.setattr(PM, "compute", fake)
    choice, results = PM.choose(None, None, MINS)
    assert calls == [1, 2] and set(results) == {1, 2}
    assert choice["CB"].spec == 1 and set(choice["CB"].checks) == {1}
    assert choice["FB-WB"].spec == 2 and set(choice["FB-WB"].checks) == {1, 2}
    assert choice["DM-CM"].spec is None and set(choice["DM-CM"].checks) == {1, 2}

    calls.clear()
    monkeypatch.setattr(PM, "compute", lambda s, m, sp, mn: (calls.append(sp) or {}, {g: _chk(True) for g in PM.GROUPS}))
    choice, results = PM.choose(None, None, MINS)
    assert calls == [1] and set(results) == {1} and all(c.spec == 1 for c in choice.values())


# ── export: style tags ────────────────────────────────────────────────────────────────
def _rows_m(pcts):
    return [{"local": {"pct": pcts.get(s.column),
                       "status": "ok" if pcts.get(s.column) is not None else "below_gate"}}
            for s in B.SPECS_IN_ORDER]


def test_style_tags_skip_rows_not_ranked_in_the_group():
    """Fix round (code-reviewer): an outsider row (status not ok, e.g. a percentile of 100 against
    a population it is not in) gets no "top 10% of <group>" tag."""
    rows = _rows_m({"carries_p90": 100.0})
    rows[[s.column for s in B.SPECS_IN_ORDER].index("carries_p90")]["local"]["status"] = "below_min_attempts"
    assert B.style_tags("CB", rows, IDENT) == []


IDENT = {"competition_id": 11, "season_id": 27, "position_group": "CB"}
TAG_METRICS = [m for m, _ in W.STYLE_TAGS]


def test_style_tags_use_the_shown_whole_number_cap_at_three_and_rank_by_percentile():
    pcts = {TAG_METRICS[0]: 89.5,      # shows 90: included
            TAG_METRICS[1]: 89.4,      # shows 89: out
            TAG_METRICS[2]: 99.0, TAG_METRICS[3]: 95.0, TAG_METRICS[4]: 95.0,
            TAG_METRICS[5]: 97.0, TAG_METRICS[6]: None}
    tags = B.style_tags("CB", _rows_m(pcts), IDENT)
    assert [t["metric"] for t in tags] == [TAG_METRICS[2], TAG_METRICS[5], TAG_METRICS[3]]
    assert len(tags) == W.STYLE_TAG_MAX
    only = B.style_tags("CB", _rows_m({TAG_METRICS[0]: 89.5, TAG_METRICS[1]: 89.4}), IDENT)
    assert [t["metric"] for t in only] == [TAG_METRICS[0]]


def test_style_tags_none_for_goalkeepers_and_no_data():
    assert B.style_tags("GK", _rows_m({TAG_METRICS[0]: 99.0}), IDENT) == []
    assert B.style_tags("CB", _rows_m({}), IDENT) == []


def test_pressing_tag_carries_the_team_defending_caveat():
    tags = B.style_tags("CB", _rows_m({"pressures_p90": 95.0, "carries_p90": 95.0}), IDENT)
    by = {t["metric"]: t["note"] for t in tags}
    assert W.STYLE_TAG_EXTRA["pressures_p90"] in by["pressures_p90"]
    assert W.STYLE_TAG_EXTRA["pressures_p90"] not in by["carries_p90"]


def test_every_style_tag_metric_is_a_style_metric():
    from eleveniq.export import display as X
    assert all(X.SPECS_BY_COLUMN[m].direction == "style" for m in TAG_METRICS)
