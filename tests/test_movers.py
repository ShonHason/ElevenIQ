"""analytics/movers.py (plan 006 W1): stints, notes and the combined dot, from the written
rules (plan 005 M4g3c)."""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from eleveniq.analytics import movers as M
from eleveniq.analytics import style_map as S
from tests.test_style_map import GROUP, SPECS, _rows

K = ("player_id", "team_id", "competition_id", "season_id", "source")


def _season(*rows):
    return pl.DataFrame([dict(zip(K, r[0])) | {"team_name": r[1], "minutes_played": r[2],
                                               "minutes_nominal_equiv": r[2],
                                               "position_group": r[3]} for r in rows])


def _match(*rows):
    return pl.DataFrame([dict(zip(K, k)) | {"match_date": d, "minutes_played": m}
                         for k, d, m in rows])


def test_stints_only_multi_row_seasons_in_date_order():
    a1, a2, b = (1, 10, 2, 27, "statsbomb"), (1, 20, 2, 27, "statsbomb"), (2, 10, 2, 27, "statsbomb")
    season = _season((a1, "A", 900.0, "CB"), (a2, "B", 500.0, "CB"), (b, "A", 900.0, "CB"))
    match = _match((a1, dt.date(2016, 1, 5), 90.0), (a1, dt.date(2015, 8, 9), 90.0),
                   (a1, dt.date(2016, 2, 1), 0.0),                     # no minutes: not counted
                   (a2, dt.date(2016, 2, 6), 90.0), (b, dt.date(2015, 8, 9), 90.0))
    pos = {a1: {"Center Back": 900.0}, a2: {"Left Back": 500.0}, b: {"Center Back": 900.0}}
    out = M.stints(season, match, pos)
    assert list(out) == [(1, 27, "statsbomb")]
    s = out[(1, 27, "statsbomb")]
    assert [x.key for x in s] == [a1, a2]
    assert (s[0].first, s[0].last) == (dt.date(2015, 8, 9), dt.date(2016, 1, 5))
    assert s[1].role.label == "LB"


def test_a_stint_without_a_match_raises():
    a1, a2 = (1, 10, 2, 27, "statsbomb"), (1, 20, 2, 27, "statsbomb")
    season = _season((a1, "A", 900.0, "CB"), (a2, "B", 500.0, "CB"))
    with pytest.raises(M.MoversError, match="no match"):
        M.stints(season, _match((a1, dt.date(2015, 8, 9), 90.0)), {a1: {"Center Back": 1.0},
                                                                    a2: {"Center Back": 1.0}})


def _stint(key, nominal=1000.0, comp=2):
    return M.Stint(key, "T", comp, "CB", nominal, nominal, dt.date(2015, 8, 1),
                   dt.date(2015, 9, 1), None)


def test_stint_notes():
    k = (1, 10, 2, 27, "statsbomb")
    assert M.stint_note(_stint(k), "CB", {k}, {}, {}) == (None, None)
    assert M.stint_note(_stint(k, comp=9), "CB", {k}, {}, {}) == (M.NOTE_PARTIAL, None)
    assert M.stint_note(_stint(k), "CB", set(), {k: "ST"}, {k: "complete"}) == (M.NOTE_OTHER_GROUP, "ST")
    assert M.stint_note(_stint(k), "CB", set(), {k: "CB"}, {k: "partial"}) == (M.NOTE_INCOMPLETE, None)
    assert M.stint_note(_stint(k, nominal=500.0), "CB", set(), {}, {}) == (M.NOTE_UNDER_GATE, None)
    with pytest.raises(M.MoversError, match="no DNA row"):
        M.stint_note(_stint(k, nominal=900.0), "CB", set(), {}, {})


def test_weights_and_changed_most():
    assert M.weights([300.0, 900.0]) == pytest.approx((0.25, 0.75))
    with pytest.raises(M.MoversError):
        M.weights([0.0])


def test_combined_dot_lies_on_the_segment_and_ranks_both_ways():
    rows = _rows()
    keys = sorted(rows)
    # Rows 0 and 1 become one player at two clubs.
    a = (999, 1, 2, 27, "statsbomb")
    b = (999, 2, 2, 27, "statsbomb")
    rows[a], rows[b] = rows.pop(keys[0]), rows.pop(keys[1])
    sm = S.fit(GROUP, SPECS, rows)
    cb = M.combined(sm, rows, [_stint(a, 300.0), _stint(b, 900.0)])
    assert cb.weights == pytest.approx((0.25, 0.75))
    for i in (0, 1):
        assert cb.score[i] == pytest.approx(0.25 * sm.scores[a][i] + 0.75 * sm.scores[b][i])
    sp = cb.pairs[0]
    assert sp.n_a == sp.n_b == len(rows) - 2
    assert 1 <= sp.rank_ab <= sp.n_a + 1 and len(sp.changed) == M.N_CHANGED


def test_goalkeeper_stint_has_no_role_and_other_seasons_do_not_make_a_mover():
    g1, g2 = (5, 10, 2, 27, "statsbomb"), (5, 20, 3, 27, "statsbomb")   # two competitions, one season
    h = (6, 10, 2, 27, "statsbomb")
    h_old = (6, 10, 2, 26, "statsbomb")                                 # other season: one row each
    season = _season((g1, "A", 900.0, "GK"), (g2, "B", 500.0, "GK"), (h, "A", 900.0, "CB"),
                     (h_old, "A", 900.0, "CB"))
    match = _match((g1, dt.date(2016, 1, 1), 90.0), (g2, dt.date(2016, 3, 1), 90.0),
                   (h, dt.date(2016, 1, 1), 90.0), (h_old, dt.date(2015, 1, 1), 90.0))
    out = M.stints(season, match, {g1: {"Goalkeeper": 900.0}, g2: {"Goalkeeper": 500.0}})
    assert list(out) == [(5, 27, "statsbomb")]
    assert [s.role for s in out[(5, 27, "statsbomb")]] == [None, None]


def test_changed_most_ties_by_metric_name():
    from tests.test_pairs import METRICS, _row
    a = _row(50.0)
    b = _row({"m1": 60.0, "m2": 90.0, "m3": 60.0, "m4": 60.0, "m5": 50.0, "m6": 50.0,
              "m7": 50.0, "m8": 50.0})
    assert M.changed_most(a, b, METRICS) == ("m2", "m1", "m3")


def test_more_than_three_plotted_stints_raise():
    rows = _rows()
    ks = sorted(rows)[:4]
    sm = S.fit(GROUP, SPECS, rows)
    with pytest.raises(M.MoversError, match="team colours"):
        M.combined(sm, rows, [_stint(k) for k in ks])


def test_rank_both_counts_incomplete_out_and_partial_in():
    from tests.test_pairs import _row
    a, b = (1, 1, 2, 27, "statsbomb"), (1, 2, 2, 27, "statsbomb")
    rows = {a: _row(50.0), b: _row(52.0), (2, 1, 2, 27, "statsbomb"): _row(51.0),
            (3, 1, 2, 27, "statsbomb"): _row(90.0, status="partial", missing="m8"),
            (4, 1, 2, 27, "statsbomb"): _row(50.0, status="dna_incomplete")}
    rab, na, rba, nb = M.rank_both(a, b, rows)
    assert (na, nb) == (2, 2)            # own player's other row and the incomplete row left out
    assert rab == 2 and rba == 2         # the 51 row is nearer to each than the other stint
