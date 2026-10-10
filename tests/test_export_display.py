"""export/display.py (plan 006 W1c): the shared display rules and the percentile bin."""
from __future__ import annotations

import pytest

from eleveniq.export import display as X

S = X.SPECS_BY_COLUMN


@pytest.mark.parametrize("p, want", [(95.0, 4), (90.0, 4), (89.9, 3), (60.1, 3), (60.0, 2),
                                     (40.0, 2), (39.9, 1), (10.1, 1), (10.0, 0), (0.0, 0)])
def test_bin_higher_better(p, want):
    assert X.percentile_bin(S["progressive_passes_p90"], False, p) == want


def test_bin_lower_better_is_direction_adjusted():
    spec = S["dispossessed_p90"]
    assert spec.direction == "lower_better"
    assert X.percentile_bin(spec, False, 5.0) == 4 and X.percentile_bin(spec, False, 95.0) == 0


def test_bin_uncoloured_and_missing():
    assert X.percentile_bin(S["long_pass_completion_pct"], False, 80.0) == "uncoloured"
    assert X.percentile_bin(S["progressive_passes_p90"], True, 80.0) == "uncoloured"
    style = next(s for s in S.values() if s.direction == "style")
    assert X.percentile_bin(style, False, 80.0) == "uncoloured"
    assert X.percentile_bin(S["progressive_passes_p90"], False, None) is None
    with pytest.raises(X.DisplayError):
        X.percentile_bin(S["progressive_passes_p90"], False, 100.5)


def test_bin_agrees_with_the_percentile_words():
    """The colour bin and the phrase use one set of boundaries (DESIGN.md §4)."""
    from eleveniq.export import words as W
    adj = {"Very low": 0, "Low": 1, "Average": 2, "High": 3, "Very high": 4}
    for p in [x / 10 for x in range(0, 1001)]:
        assert X.percentile_bin(S["progressive_passes_p90"], False, p) == adj[W.pct_words(p)[0]], p


def test_withheld_reason_adds_underpowered_for_dm_cm_defending():
    assert X.withheld_reason("Defending", "DM-CM", "mover_stability_inconclusive") == (
        "player-vs-team test on club movers inconclusive; underpowered")
    with pytest.raises(X.DisplayError, match="rebuild"):
        X.withheld_reason("Defending", "CB", "mover_stability_inconclusive")


def test_gap_eligibility_option_c():
    """Out: signature traits, team-possession-associated cells, long pass completion; style in."""
    from eleveniq.analytics.registry import REGISTRY
    style = next(s for s in S.values() if s.direction == "style")
    assert X.gap_eligible(style, "dna", False)
    assert X.gap_eligible(S["progressive_passes_p90"], "dna", False)
    assert not X.gap_eligible(S["progressive_passes_p90"], "dna", True)
    assert not X.gap_eligible(S["long_pass_completion_pct"], "display", False)
    assert not X.gap_eligible(S["progressive_passes_p90"], "signature", False)
    # the 9 cells with the team-possession note are out
    noted = [(m, g) for (m, g), c in REGISTRY.cells.items() if "team-style influenced" in c.notes]
    assert len(noted) == 9
    for m, g in noted:
        c = REGISTRY.cell(m, g)
        assert not X.gap_eligible(S[m], c.status, c.team_style_biased)


def test_shown_bin_reads_the_shown_value_directly():
    """W0 decision 1: a lower-better cell's bin is the band of its shown percentile; exact at
    the boundaries."""
    spec = S["dispossessed_p90"]
    for shown, want in [(90.0, 4), (89.999999, 3), (60.0, 2), (40.0, 2), (10.0, 0), (21.641791, 1)]:
        assert X.shown_bin(spec, False, shown) == want
    assert X.shown_bin(spec, False, None) is None
