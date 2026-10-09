"""
M4g6a1 validation spike helpers (plan 005 "M4g6a spec", stats-reviewer conditions 1-12):
- the outlier classification takes the first matching class, in the fixed order;
- the club changer's rank admits the other stint (condition 7);
- the mover rule: exactly two stints ≥ 450 nominal in one outfield group;
- the item-7 sample rule (face players, 2 longest per group, 2 movers by combined minutes);
- the per-player recount definitions on synthetic events (entries, long passes, aerials,
  ball recoveries, open-play xG assisted, Unknown outcomes) and the duplicate-event scan;
- the m4d reference `gate` / `min_cohort` parameters (integration: the default reproduces the
  build's statuses, 1,080 is a subset of 900, a waived floor leaves no cohort_too_small).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4_validation as mv  # noqa: E402
import m4g6_recount as rc  # noqa: E402


# ── classification and ranks ─────────────────────────────────────────────────────

@pytest.mark.parametrize("args, want", [
    ((False, 3, False, 0.5, 950.0), "coding error"),
    ((True, 1, False, 0.5, 950.0), "data anomaly"),
    ((True, 0, False, 0.5, 950.0), "single-match spike"),
    ((True, 0, True, 0.5, 950.0), "mixed role"),
    ((True, 0, None, 0.9, 1000.0), "near-gate"),
    ((True, 0, True, 0.9, 2000.0), "genuine"),
    ((True, 0, True, 0.70, 1350.0), "genuine"),           # both bars are strict
])
def test_classify_first_match_wins(args, want):
    assert mv.classify(*args) == want


def test_stint_rank_admits_the_other_stint():
    others = {("a",): 0.10, ("b",): 0.20, ("c",): 0.30}
    assert mv.stint_rank(others, 0.05) == 1
    assert mv.stint_rank(others, 0.25) == 3
    assert mv.stint_rank(others, 0.20) == 2                # ties are not "closer"


def test_overlap_and_summary():
    assert mv.overlap([1, 2, 3], [3, 4, 1]) == 2
    s = mv.summary([1.0, 2.0, 3.0, 4.0])
    assert (s["n"], s["median"], s["max"]) == (4, 2.5, 4.0)
    assert mv.summary([])["median"] is None


# ── movers and the sample rule ────────────────────────────────────────────────────

def _row(pid, team, group, nominal, minutes=None, name=None, comp=2):
    return {"player_id": pid, "team_id": team, "competition_id": comp, "season_id": 27,
            "source": "statsbomb", "position_group": group, "minutes_nominal_equiv": nominal,
            "minutes_played": minutes if minutes is not None else nominal,
            "player_name": name or f"p{pid}"}


def test_mover_rule():
    season = [_row(1, 10, "CB", 900), _row(1, 11, "CB", 500),           # mover
              _row(2, 10, "CB", 900), _row(2, 11, "FB-WB", 900),        # two groups
              _row(3, 10, "ST", 900), _row(3, 11, "ST", 449),           # one stint below 450
              _row(4, 10, "ST", 600), _row(4, 11, "ST", 600), _row(4, 12, "ST", 600),  # three
              _row(5, 10, "GK", 900), _row(5, 11, "GK", 900)]           # goalkeeper
    assert set(mv.movers(season)) == {1}


def test_sample_rule(monkeypatch):
    monkeypatch.setattr(mv.wc, "FACE", (("Face", 99),))
    monkeypatch.setattr(mv.ref, "DNA", {"CB": (), "ST": ()})
    scored = [_row(1, 99, "CB", 3000, name="Face"),
              _row(2, 10, "CB", 2900), _row(3, 10, "CB", 2800), _row(4, 10, "CB", 2700),
              _row(5, 10, "ST", 2500), _row(6, 10, "ST", 2400), _row(7, 10, "ST", 2300),
              _row(8, 10, "ST", 1000), _row(8, 11, "ST", 1200, comp=7),     # mover, 2,200
              _row(9, 10, "CB", 950), _row(9, 11, "CB", 950, comp=7)]       # mover, 1,900
    season = list(scored)
    got = mv.sample(scored, season)
    pids = []
    for r in got:
        if r["player_id"] not in pids:
            pids.append(r["player_id"])
    assert pids == [1, 2, 3, 5, 6, 8, 9]
    assert sum(r["player_id"] == 8 for r in got) == 2       # both scored stints included


def test_sample_fails_on_a_missing_face_player(monkeypatch):
    monkeypatch.setattr(mv.wc, "FACE", (("Nobody", 1),))
    with pytest.raises(SystemExit):
        mv.sample([_row(1, 2, "CB", 1000)], [])


# ── recount definitions ──────────────────────────────────────────────────────────

def _ev(i, kind, pid=7, period=1, ts="00:01:00.000", **kw):
    e = {"id": f"e{i}", "type": {"name": kind}, "period": period, "timestamp": ts,
         "player": {"id": pid}}
    e.update(kw)
    return e


def test_recount_definitions():
    events = [
        _ev(1, "Pass", location=[70, 40]),
        _ev(2, "Pass", location=[70, 40], **{"pass": {"end_location": [85, 40], "length": 31}}),
        _ev(3, "Pass", location=[90, 40], **{"pass": {"end_location": [105, 40], "length": 15}}),
        _ev(4, "Pass", location=[50, 40], **{"pass": {"end_location": [90, 40], "length": 40,
                                                       "outcome": {"name": "Unknown"}}}),
        _ev(5, "Pass", location=[70, 40], **{"pass": {"end_location": [110, 40], "length": 41,
                                                       "type": {"name": "Corner"}}}),
        _ev(6, "Carry", location=[78, 40], carry={"end_location": [104, 30]}),
        _ev(7, "Ball Recovery"), _ev(8, "Ball Recovery", ball_recovery={"recovery_failure": True}),
        _ev(9, "Duel", duel={"type": {"name": "Aerial Lost"}}),
        _ev(10, "Clearance", clearance={"aerial_won": True}),
        _ev(11, "Pressure"), _ev(12, "Dribbled Past"),
        _ev(13, "Shot", pid=8, shot={"key_pass_id": "e3", "statsbomb_xg": 0.25,
                                     "type": {"name": "Open Play"}}),
        _ev(14, "Shot", pid=8, shot={"key_pass_id": "e5", "statsbomb_xg": 0.4,
                                     "type": {"name": "Open Play"}}),
        _ev(15, "Pressure", period=5),
        _ev(16, "Pressure", pid=8),
    ]
    events[0]["pass"] = {"end_location": [75, 40], "length": 5}
    t = rc.player_counts(events, 7)
    assert t["passes_attempted"] == 5
    assert t["long_passes_attempted"] == 2 and t["long_passes_completed"] == 1
    assert t["long_passes_unknown_outcome"] == 1 and t["passes_unknown_outcome"] == 1
    assert (t["final_third_entries_pass"], t["box_entries_pass"]) == (1, 1)
    assert (t["final_third_entries_carry"], t["box_entries_carry"]) == (1, 1)
    assert (t["ball_recoveries"], t["aerials_lost"], t["aerials_won"]) == (1, 1, 1)
    assert (t["pressures"], t["dribbled_past"]) == (1, 1)    # period 5 and player 8 left out
    assert t["op_xg_assisted"] == pytest.approx(0.25)      # the corner-assisted shot is a restart


def test_duplicate_events():
    events = [_ev(1, "Pressure", ts="00:01:00.000"), _ev(2, "Pressure", ts="00:01:00.000"),
              _ev(3, "Pressure", ts="00:01:01.000"), _ev(3, "Pass", ts="00:02:00.000"),
              _ev(4, "Pressure", pid=8, ts="00:01:00.000")]
    assert rc.duplicate_events(events, 7) == 2              # same timestamp + type; reused id


# ── m4d reference gate / floor parameters (integration) ─────────────────────────

def _meas():
    return json.loads((ROOT / "src" / "eleveniq" / "analytics" / "measurements" / "statsbomb_27.json").read_text())


@pytest.mark.integration
def test_gate_parameter_keeps_the_default_and_nests():
    import m4d_percentiles_reference as m4d
    if not m4d.SEASON_PARQUET.exists():
        pytest.skip("season parquet missing")
    meas = _meas()
    base = m4d.load_rows(meas)
    hi = m4d.load_rows(meas, 1080.0)
    lo = m4d.load_rows(meas, 720.0)
    status = {m4d.key_of(r): r["_status"] for r in base}
    assert all(status[m4d.key_of(r)] == r["_status"] for r in m4d.load_rows(meas, 900.0))
    s900 = {m4d.key_of(r) for r in base if r["_status"] == "scored"}
    s1080 = {m4d.key_of(r) for r in hi if r["_status"] == "scored"}
    s720 = {m4d.key_of(r) for r in lo if r["_status"] == "scored"}
    assert s1080 < s900 < s720


@pytest.mark.integration
def test_waived_floor_leaves_no_cohort_too_small():
    import m4d_percentiles_reference as m4d
    if not m4d.SEASON_PARQUET.exists():
        pytest.skip("season parquet missing")
    meas = _meas()
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    rows = m4d.load_rows(meas, 1080.0)
    metrics = m4d.load_metrics(meas)
    rule = m4d.Cohorts(rows, metrics, pool, meas)
    waived = m4d.Cohorts(rows, metrics, pool, meas, min_cohort=0)
    st = [r for r in rule.scored if r["position_group"] == "ST" and r["competition_id"] == 2]
    m = metrics["np_shots_p90"]
    assert st and all(rule.cell(r, m, "ST")["status_local"] == "cohort_too_small" for r in st)
    assert all(waived.cell(r, m, "ST")["status_local"] == "ok" for r in st)


# ── fix round (code-reviewer, M4g6a1) ─────────────────────────────────────────────

def test_value_of_per90_and_ratio():
    import m4d_percentiles_reference as m4d
    per90 = m4d.Metric("x_p90", "per90")
    ratio = m4d.Metric("r_pct", "ratio", "num", ["a"], ["b"])
    assert mv.value_of(per90, 6, 0, 0, 180.0) == pytest.approx(3.0)
    assert mv.value_of(per90, 6, 0, 0, 0.0) is None
    assert mv.value_of(ratio, 3, 10, 4, 0.0) == pytest.approx(0.5)     # 3 / (10 − 4)
    assert mv.value_of(ratio, 3, 4, 4, 0.0) is None                      # empty denominator


def test_top_k_orders_by_distance_then_key_and_respects_allowed():
    dist = {("a",): {("b",): 0.3, ("c",): 0.1, ("d",): 0.1, ("e",): 0.2}}
    assert mv.top_k(dist, ("a",), k=3) == [("c",), ("d",), ("e",)]
    assert mv.top_k(dist, ("a",), allowed={("b",), ("e",)}, k=3) == [("e",), ("b",)]
    assert mv.top_k(dist, ("z",)) == []


def test_same_league_observed_and_expected(monkeypatch):
    monkeypatch.setattr(mv, "K", 1)
    # keys are season keys: index 2 is the competition
    a, b, c = (1, 0, 2), (2, 0, 2), (3, 0, 7)
    dist = {a: {b: 0.1, c: 0.2}, b: {a: 0.1, c: 0.3}, c: {a: 0.2, b: 0.3}}
    obs, exp = mv.same_league(dist, [a, b, c])
    assert obs == 2                    # a ↔ b are each other's nearest; c's nearest is a (other league)
    assert exp == pytest.approx(0.5 + 0.5 + 0.0)


def test_spike_thresholds_equal_their_sources():
    """The spike cannot import eleveniq: each written-out threshold is checked here against the
    value it copies (config, the M4g3 mover spike, the minute-bucket file). MIXED_SHARE (0.70)
    exists only in ADR 004 §12.8 C1, so it has no machine-readable source to check."""
    from eleveniq import config
    import m4d_percentiles_reference as m4d
    import m4g3_movers as m4g3
    assert mv.K == config.DNA_NEIGHBOURS_K
    assert mv.BASE_GATE == config.MIN_MINUTES_THRESHOLD
    assert mv.LOW_RELIABILITY == config.LOW_RELIABILITY_BELOW
    assert m4d.MIN_COHORT_SIZE == config.MIN_COHORT_SIZE
    assert mv.MOVER_FLOOR == m4g3.MOVER_FLOOR
    if mv.BUCKETS.exists():
        assert mv.NEAR_GATE_HI == json.loads(mv.BUCKETS.read_text())["buckets"]["900-1350"]["hi"]


@pytest.mark.integration
def test_waived_floor_never_ranks_an_empty_population():
    import m4d_percentiles_reference as m4d
    if not m4d.SEASON_PARQUET.exists():
        pytest.skip("season parquet missing")
    meas = _meas()
    pool = [str(c) for c in meas["provenance"]["pool_competition_ids"]]
    rows = m4d.load_rows(meas, 1080.0)
    co = m4d.Cohorts(rows, m4d.load_metrics(meas), pool, meas, min_cohort=0)
    co._pops[("np_shots_p90", "ST", 2)] = []                 # an empty league population
    r = next(r for r in co.scored if r["position_group"] == "ST" and r["competition_id"] == 2)
    assert co.cell(r, co.metrics["np_shots_p90"], "ST")["status_local"] == "cohort_too_small"
