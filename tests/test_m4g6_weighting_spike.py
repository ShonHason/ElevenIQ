"""
M4g6a2 weighting-variant helpers (plan 005 "M4g6a spec" item 3; user decisions 2026-10-09):
- cap: a one-member family (nominal) gets 0.5/|F| and its freed 0.5/|F| is split equally over
  the pair's other families; weights sum to 1; a group without one-member families is unchanged;
- merge: Goal threat + Creation become one family where either has one member; a group without
  a Creation member (ST) is unchanged; CB Aerial is never merged;
- effective metrics, the noise share Σ w (1 − r) and the item-rest correlation.
No data needed (the DNA lists are the reference spike's constants).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4f_dna_reference as ref  # noqa: E402
import m4g6_weighting as mw  # noqa: E402


def _family_weight(group: str, w: dict[str, float], fam: str, variant: str) -> float:
    return sum(x for m, x in w.items() if mw.family_of(group, m, variant) == fam)


def test_family_weights_are_the_build():
    w = mw.weights("FB-WB", list(ref.DNA["FB-WB"]), "family")
    fams = mw.nominal_families("FB-WB")
    assert sum(w.values()) == pytest.approx(1.0)
    for f in fams:
        assert _family_weight("FB-WB", w, f, "family") == pytest.approx(1 / len(fams))


def test_cap_gives_one_member_families_exactly_half_and_splits_the_rest_over_uncapped():
    """The pre-registered rule (user, 2026-10-09): a one-member family gets 0.5/|F|; the freed
    weight goes equally to the uncapped families. (M4g6a2 fix round: the first version of this
    test expected the capped families to receive freed weight from each other, i.e. it encoded
    the implementation, not the rule.)"""
    g = "FB-WB"                                # 7 families, 3 with one member
    w = mw.weights(g, list(ref.DNA[g]), "cap")
    n, capped = 7, {"Aerial", "Creation", "Goal threat"}
    assert set(mw.nominal_families(g)) >= capped and len(mw.nominal_families(g)) == n
    assert sum(w.values()) == pytest.approx(1.0)
    for f in mw.nominal_families(g):
        want = mw.CAP / n if f in capped else 1 / n + len(capped) * mw.CAP / n / (n - len(capped))
        assert _family_weight(g, w, f, "cap") == pytest.approx(want)


def test_cap_when_every_family_is_capped_keeps_family_weights():
    g = "FB-WB"
    ms = ["aerials_won_p90", "np_shots_p90"]           # two one-member families only
    assert mw.weights(g, ms, "cap") == pytest.approx(mw.weights(g, ms, "family"))


def test_cap_without_one_member_families_is_the_build():
    g = "DM-CM"
    assert mw.weights(g, list(ref.DNA[g]), "cap") == pytest.approx(mw.weights(g, list(ref.DNA[g]), "family"))


def test_cap_uses_the_nominal_family_size_not_the_pair():
    """A family reduced to one shared metric by a missing value is not capped."""
    g = "CB"
    ms = [m for m in ref.DNA[g] if m != "final_third_entries_carry_p90"]   # Carrying left with one
    w = mw.weights(g, ms, "cap")
    assert _family_weight(g, w, "Aerial", "cap") < _family_weight(g, w, "Carrying", "cap")


def test_merge_groups_and_mapping():
    assert mw.merged_groups() == {"FB-WB", "AM-Winger", "ST"}
    assert mw.family_of("FB-WB", "np_shots_p90", "merge") == mw.MERGED
    assert mw.family_of("FB-WB", "op_key_passes_p90", "merge") == mw.MERGED
    assert mw.family_of("DM-CM", "np_shots_p90", "merge") == "Goal threat"
    assert mw.family_of("CB", "aerials_won_p90", "merge") == "Aerial"
    w = mw.weights("FB-WB", list(ref.DNA["FB-WB"]), "merge")
    assert _family_weight("FB-WB", w, mw.MERGED, "merge") == pytest.approx(1 / 6)
    assert mw.weights("ST", list(ref.DNA["ST"]), "merge") == pytest.approx(
        mw.weights("ST", list(ref.DNA["ST"]), "family"))           # no Creation member in ST


def test_effective_metrics_and_noise_share():
    w = {"a": 0.5, "b": 0.25, "c": 0.25}
    assert mw.effective_metrics(w) == pytest.approx(1 / 0.375)
    assert mw.noise_share(w, {"a": 0.8, "b": 0.6, "c": 1.0}) == pytest.approx(0.5 * 0.2 + 0.25 * 0.4)


def test_item_rest():
    assert mw.item_rest({"a": [1.0, 2.0]}) == {"a": None}
    got = mw.item_rest({"a": [1.0, 2.0, 3.0, 4.0, None], "b": [2.0, 1.0, 4.0, 3.0, 5.0]})
    assert got["a"] == pytest.approx(got["b"]) == pytest.approx(0.6)   # rows with a missing value drop



# ── distances, shares, neighbours (code-reviewer, M4g6a2) ────────────────────────

def _rows(pcts: list[tuple[int, int, float, float]]) -> list[dict]:
    """(player, team, pct of metric a, pct of metric b) -> rows of a two-metric toy group."""
    return [{"key": (pid, team, 2, 27, "statsbomb"),
             "cells": {"a_p90": {"pct_local": pa, "metric_status": "ok", "family": "F"},
                       "b_p90": {"pct_local": pb, "metric_status": "ok", "family": "G"}}}
            for pid, team, pa, pb in pcts]


@pytest.fixture
def toy(monkeypatch):
    monkeypatch.setitem(ref.DNA, "TOY", ("a_p90", "b_p90"))
    monkeypatch.setitem(ref.SPEC, "a_p90", ("F", ref.H))
    monkeypatch.setitem(ref.SPEC, "b_p90", ("G", ref.L))
    monkeypatch.setattr(mw, "K", 2)


def test_pairs_exclude_the_same_player_and_order_by_distance(toy):
    rows = _rows([(1, 10, 50, 50), (1, 11, 50, 50), (2, 10, 60, 50), (3, 12, 90, 50)])
    p = mw.Pairs("TOY", rows, ("a_p90", "b_p90"), "family")
    assert p.top[0] == [2, 3]                       # its own other stint (row 1) is excluded
    assert p.dist(0, 2) == pytest.approx(0.5 * 0.1 ** 2)


def test_metric_shares_skip_zero_distances_and_metric_abs(toy):
    rows = _rows([(1, 10, 50, 50), (2, 10, 50, 50), (3, 10, 60, 70)])
    p = mw.Pairs("TOY", rows, ("a_p90", "b_p90"), "family")
    shares = mw.metric_shares(p, [(0, 1), (0, 2)])  # (0, 1) has distance 0: skipped
    assert shares["a_p90"] == pytest.approx(0.01 / 0.05) and shares["b_p90"] == pytest.approx(0.04 / 0.05)
    ab = mw.metric_abs(p, [(0, 1), (0, 2)])
    assert ab["a_p90"] == pytest.approx(0.5 * 0.01 / 2)


def test_same_pos_counts_observed_and_expected(toy):
    rows = _rows([(1, 10, 50, 50), (2, 10, 51, 50), (3, 11, 52, 50), (4, 11, 90, 90)])
    p = mw.Pairs("TOY", rows, ("a_p90", "b_p90"), "family")
    obs, exp = mw.same_pos(p, 1)
    assert obs == sum(rows[j]["key"][1] == rows[i]["key"][1] for i, t in enumerate(p.top) for j in t)
    assert exp == pytest.approx(4 * 2 * (1 / 3))   # each row: 1 teammate among 3 candidates


def test_correlations_flip_lower_better_members(toy, monkeypatch):
    monkeypatch.setitem(ref.SPEC, "b_p90", ("F", ref.L))            # one family, b lower-better
    rows = _rows([(i, 10, float(i), 100.0 - i) for i in range(1, 8)])
    pairs, rest = mw.correlations(rows, "TOY")
    assert pairs[0]["rho"] == pytest.approx(-1.0) and pairs[0]["tag"] == "within-family"
    assert rest["F"]["a_p90"] == pytest.approx(1.0)                 # after the flip they agree
