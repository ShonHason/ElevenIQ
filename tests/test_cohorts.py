"""
Tests for analytics/cohorts.py and the percentile table (pipeline/build_percentiles.py)
on synthetic season rows (ADR 004 §11.1, D3, D4, D5, D6, D19, D21).

The registry cells are built here (a synthetic Derivation), so each test states the
min_attempts, D9 outcome and basis it relies on.
"""
from __future__ import annotations

import polars as pl
import pytest

from eleveniq import config
from eleveniq.analytics import cohorts as C
from eleveniq.analytics.registry import Cell, Derivation, MetricSpec, RatioSpec
from eleveniq.pipeline.build_percentiles import SORT_KEY, percentile_table

GROUPS = ("CB", "FB-WB", "DM-CM", "AM-Winger", "ST")
P90 = MetricSpec(column="x_p90", display_name="X", family="Defending", direction="higher_better",
                 unit="per90", count_column="x")
RATIO = MetricSpec(column="r_pct", display_name="R", family="Passing", direction="higher_better",
                   unit="ratio", ratio=RatioSpec("rn", ("rd",)))
SPECS = (P90, RATIO)
SHA = "a" * 64
VERSION = "b" * 64
MIN_N = config.MIN_COHORT_SIZE


def _cell(col: str, grp: str, *, global_ok: bool | None = True, min_attempts: int | None = None,
          reasons: tuple[str, ...] = ("dna",)) -> Cell:
    return Cell(col, grp, "dna", reasons, global_ok=global_ok,
                basis="global" if global_ok else "local", min_attempts=min_attempts)


def _derivation(**over: Cell) -> Derivation:
    """Every cell global_ok; the ratio's min_attempts 10. `over` replaces cells by 'col|grp'."""
    cells = {}
    for g in GROUPS:
        cells[("x_p90", g)] = _cell("x_p90", g)
        cells[("r_pct", g)] = _cell("r_pct", g, min_attempts=10)
    for k, c in over.items():
        col, grp = k.split("|")
        cells[(col, grp)] = c
    return Derivation(cells=cells, boundary=(), source="statsbomb", version=VERSION)


_MIN_COLS = ("minutes_gk", "minutes_cb", "minutes_fb_wb", "minutes_dm_cm", "minutes_am_winger",
             "minutes_st")
_GROUP_MIN = {"CB": "minutes_cb", "FB-WB": "minutes_fb_wb", "DM-CM": "minutes_dm_cm",
              "AM-Winger": "minutes_am_winger", "ST": "minutes_st", "GK": "minutes_gk"}


def row(pid: int, x: float, *, team: int = 1, comp: int = 11, group: str = "CB",
        minutes: float = 1000.0, pool: bool = True, rn: int = 15, rd: int = 20,
        second: str | None = None, ambiguous: bool = False, source: str = "statsbomb",
        nominal: float | None = None) -> dict:
    nominal = minutes if nominal is None else nominal
    r = {"player_id": pid, "team_id": team, "competition_id": comp, "season_id": 27,
         "source": source, "player_name": f"p{pid}", "position_group": group,
         "second_position_group": second, "position_group_ambiguous": ambiguous,
         "in_percentile_pool": pool, "is_outfield": group != "GK",
         "minutes_played": minutes, "minutes_nominal_equiv": nominal,
         "meets_min_minutes": nominal >= config.MIN_MINUTES_THRESHOLD - config.MIN_MINUTES_TOLERANCE,
         "x_p90": x, "x": int(x), "rn": rn, "rd": rd, "r_pct": rn / rd if rd else None}
    for c in _MIN_COLS:
        r[c] = 0.0
    r[_GROUP_MIN[group]] = minutes
    return r


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None)


def cohort(n: int, *, group: str = "CB", comp: int = 11, start: int = 1, team: int = 1) -> list[dict]:
    """n scored rows with x = 1..n in one league × group."""
    return [row(start + i, float(i + 1), group=group, comp=comp, team=team) for i in range(n)]


def table(rows: list[dict], derivation: Derivation | None = None) -> pl.DataFrame:
    return percentile_table(frame(rows), derivation or _derivation(), season_sha256=SHA, specs=SPECS)


def cell_of(t: pl.DataFrame, pid: int, metric: str, role: str = "primary", team: int = 1) -> dict:
    sub = t.filter((pl.col("player_id") == pid) & (pl.col("team_id") == team)
                   & (pl.col("metric") == metric) & (pl.col("cohort_role") == role))
    assert sub.height == 1
    return sub.row(0, named=True)


def scored_cells(t: pl.DataFrame, exclude_pid: int | None = None) -> dict:
    t = t.filter(pl.col("percentile_status") == "scored")
    if exclude_pid is not None:
        t = t.filter(pl.col("player_id") != exclude_pid)
    return {(r["player_id"], r["team_id"], r["cohort_role"], r["metric"]):
            (r["pct_local"], r["n_local"], r["pct_global"], r["n_global"])
            for r in t.iter_rows(named=True)}


# ── Row status and inputs ──────────────────────────────────────────────────────

class TestRowStatus:
    def test_precedence(self):
        df = frame([row(1, 1, pool=False, group="GK", minutes=10),   # not_in_pool first
                    row(2, 1, group="GK", minutes=10),                 # goalkeeper before below_gate
                    row(3, 1, minutes=10),
                    row(4, 1)])
        assert df.select(C.row_status()).to_series().to_list() == \
            ["not_in_pool", "goalkeeper", "below_gate", "scored"]

    def test_null_flags_count_as_false(self):
        df = frame([row(1, 1)]).with_columns(pl.lit(None, dtype=pl.Boolean).alias("meets_min_minutes"))
        assert df.select(C.row_status()).item() == "below_gate"


class TestCheckInputs:
    def _check(self, rows, sha=SHA):
        C.check_inputs(frame(rows), source="statsbomb", season_sha256=sha, expected_sha256=SHA)

    def test_valid_rows_pass(self):
        self._check(cohort(3))

    def test_a_second_source_raises(self):
        with pytest.raises(C.CohortInputError, match="sources other than"):
            self._check([*cohort(3), row(9, 1, source="sportmonks")])

    def test_parquet_hash_mismatch_raises(self):
        with pytest.raises(C.CohortInputError, match="sha256"):
            self._check(cohort(3), sha="c" * 64)

    def test_d6_mixed_gk_outfield_row_raises(self):
        r = row(1, 1)
        r["minutes_gk"] = config.MIXED_POSITION_MINUTES
        r["minutes_cb"] = config.MIXED_POSITION_MINUTES
        with pytest.raises(C.CohortInputError, match="D6"):
            self._check([r])
        r["minutes_gk"] = config.MIXED_POSITION_MINUTES - 0.1
        self._check([r])

    def test_stale_gate_flag_raises(self):
        r = row(1, 1)
        r["meets_min_minutes"] = False
        with pytest.raises(C.CohortInputError, match="D1 gate"):
            self._check([r])


# ── D3 dedupe and populations ──────────────────────────────────────────────────

class TestDedupe:
    def test_most_minutes_wins(self):
        df = frame([row(1, 1, team=2, minutes=1200), row(1, 5, team=3, minutes=1300)])
        assert C.dedupe_population(df)["team_id"].to_list() == [3]

    def test_equal_minutes_lowest_competition_then_team(self):
        df = frame([row(1, 1, team=5, comp=12), row(1, 2, team=9, comp=2), row(1, 3, team=4, comp=2)])
        kept = C.dedupe_population(df)
        assert kept.select("competition_id", "team_id").rows() == [(2, 4)]

    def test_different_players_are_all_kept(self):
        assert C.dedupe_population(frame(cohort(5))).height == 5


class TestEligibilityAndPopulation:
    def _members(self, rows, group="CB"):
        return C.scored_members(frame(rows), group)

    def test_ratio_filters_on_min_attempts_then_dedupes(self):
        # A mover's most-minutes row misses min_attempts (rd 5 < 10): his other row
        # represents him (§10.2: filter first, then dedupe).
        rows = [*cohort(3, start=10), row(1, 1, team=2, minutes=2000, rd=5, rn=4),
                row(1, 1, team=3, minutes=1000, rd=20, rn=10)]
        pop = C.reference_population(self._members(rows), RATIO, _cell("r_pct", "CB", min_attempts=10))
        assert pop.filter(pl.col("player_id") == 1)["team_id"].to_list() == [3]
        # Dedupe-then-filter would have dropped the player.
        assert C.reference_population(self._members(rows), P90, _cell("x_p90", "CB")) \
            .filter(pl.col("player_id") == 1)["team_id"].to_list() == [2]

    def test_no_signal_and_unmeasured_ratios_have_no_eligible_rows(self):
        df = frame(cohort(3))
        sig = _cell("r_pct", "CB", min_attempts=None, reasons=("display", "no_signal"))
        unm = _cell("r_pct", "CB", min_attempts=None, reasons=("fixed_display",))
        assert set(df.select(C.eligibility(RATIO, sig)).to_series()) == {"no_signal"}
        assert set(df.select(C.eligibility(RATIO, unm)).to_series()) == {"no_min_attempts"}

    def test_below_min_attempts_and_null_value(self):
        df = frame([row(1, 1, rd=9, rn=1), row(2, 1, rd=10, rn=1), row(3, 1, rd=0, rn=0)])
        c = _cell("r_pct", "CB", min_attempts=10)
        assert df.select(C.eligibility(RATIO, c)).to_series().to_list() == \
            ["below_min_attempts", "ok", "below_min_attempts"]
        df = frame([row(1, 1)]).with_columns(pl.lit(None, dtype=pl.Float64).alias("x_p90"))
        assert df.select(C.eligibility(P90, _cell("x_p90", "CB"))).item() == "null_value"

    def test_non_scored_or_other_group_rows_are_refused(self):
        df = frame([*cohort(3), row(9, 1, minutes=10)]).with_columns(C.row_status().alias("_status"))
        with pytest.raises(C.CohortInputError, match="non-scored"):
            C.reference_population(df, P90, _cell("x_p90", "CB"))

    def test_gk_group_is_refused(self):
        with pytest.raises(C.CohortInputError, match="D21"):
            C.reference_population(self._members(cohort(3)), P90, _cell("x_p90", "GK"))


# ── The percentile table ───────────────────────────────────────────────────────

class TestTable:
    def test_hand_computed_cohort_with_ties(self):
        rows = cohort(MIN_N)
        rows[0]["x_p90"] = rows[1]["x_p90"] = 2.0          # x = 2, 2, 3, ..., 30
        t = table(rows)
        a, b = cell_of(t, 1, "x_p90"), cell_of(t, 2, "x_p90")
        assert a["pct_local"] == b["pct_local"] == pytest.approx((0 + 0.5 * 2) / MIN_N * 100)
        assert a["n_local"] == a["n_global"] == MIN_N

    def test_below_gate_row_is_in_no_population(self):
        base = [*cohort(MIN_N), *cohort(MIN_N, group="ST", start=100)]
        base[0].update(second_position_group="ST", position_group_ambiguous=True)   # D5 row too
        extreme = row(999, 1e6, minutes=899, rn=99, rd=100)
        extreme_st = row(998, 1e6, minutes=899, group="ST", rn=99, rd=100)
        with_rows = table([*base, extreme, extreme_st])
        assert scored_cells(with_rows) == scored_cells(table(base))
        c = cell_of(with_rows, 999, "x_p90")
        assert c["percentile_status"] == "below_gate" and c["pct_local"] is None \
            and c["status_global"] == "below_gate" and c["value"] == 1e6

    def test_ratio_row_below_min_attempts_is_null_and_absent_from_the_reference(self):
        base = cohort(MIN_N)
        low = row(999, 5, rn=1, rd=9)                            # r_pct 0.11, below every other row
        t = table([*base, low])
        c = cell_of(t, 999, "r_pct")
        assert c["status_local"] == c["status_global"] == "below_min_attempts"
        assert c["pct_local"] is None and c["n_local"] is None
        # Removing it changes no other row's ratio percentile.
        def ratio_cells(tab):
            return {k: v for k, v in scored_cells(tab, exclude_pid=999).items() if k[3] == "r_pct"}
        assert ratio_cells(t) == ratio_cells(table(base))
        # The ratio populations exclude it; the per-90 populations include it (it is gated).
        assert cell_of(t, 1, "r_pct")["n_global"] == MIN_N
        assert cell_of(t, 1, "x_p90")["n_global"] == MIN_N + 1

    def test_cohort_of_29_is_null_and_30_is_scored(self):
        t29 = table(cohort(MIN_N - 1))
        c = cell_of(t29, 1, "x_p90")
        assert c["status_local"] == c["status_global"] == "cohort_too_small"
        assert c["pct_local"] is None and c["n_local"] == MIN_N - 1
        assert cell_of(table(cohort(MIN_N)), 1, "x_p90")["status_local"] == "ok"

    def test_global_population_spans_leagues_and_local_does_not(self):
        rows = [*cohort(MIN_N, comp=11), *cohort(MIN_N, comp=2, start=100, team=2)]
        c = cell_of(table(rows), 1, "x_p90")
        assert c["n_local"] == MIN_N and c["n_global"] == 2 * MIN_N

    def test_members_are_strictly_inside_0_100(self):
        rows = [*cohort(MIN_N), *cohort(MIN_N, comp=2, start=100, team=2)]
        t = table(rows).filter(pl.col("status_local") == "ok")
        assert t["pct_local"].min() > 0 and t["pct_local"].max() < 100
        assert t["pct_global"].min() > 0 and t["pct_global"].max() < 100

    def test_d3_outsider_is_scored_against_a_population_without_it(self):
        # Player 1 has two rows in the league; the 2,000-minute row represents him, and the
        # 950-minute row (x 100, above everyone) is scored as an outsider: exactly 100.
        rows = [*cohort(MIN_N, start=10), row(1, 50, team=2, minutes=2000),
                row(1, 100, team=3, minutes=950)]
        t = table(rows)
        out = cell_of(t, 1, "x_p90", team=3)
        assert out["pct_local"] == 100.0 and out["n_local"] == MIN_N + 1
        member = cell_of(t, 1, "x_p90", team=2)
        assert 0 < member["pct_local"] < 100

    def test_second_group_row_is_scored_against_its_second_group(self):
        cb, st = cohort(MIN_N), cohort(MIN_N, group="ST", start=100)
        cb[0].update(second_position_group="ST", position_group_ambiguous=True, x_p90=15.5)
        t = table([*cb, *st])
        sec = cell_of(t, 1, "x_p90", role="second")
        assert sec["position_group"] == "ST" and sec["n_local"] == MIN_N
        assert sec["pct_local"] == pytest.approx(15 / MIN_N * 100)    # 15 ST rows below 15.5
        # It is not a member: the ST percentiles are those without it.
        assert {k: v for k, v in scored_cells(t).items() if k[0] >= 100} == \
            {k: v for k, v in scored_cells(table([*cohort(MIN_N), *st])).items() if k[0] >= 100}

    def test_non_ambiguous_second_group_gets_no_second_row(self):
        cb = cohort(MIN_N)
        cb[0]["second_position_group"] = "ST"
        assert table([*cb, *cohort(MIN_N, group="ST", start=100)]) \
            .filter(pl.col("cohort_role") == "second").height == 0

    def test_second_group_must_be_outfield(self):
        # Data-quality guard: GK is never a valid D5 second group (ADR 004 D5 — "for an
        # outfield row, GK is never used as a display-only second cohort"); a mislabelled
        # second_position_group must raise, not silently build a GK population.
        cb = cohort(MIN_N)
        cb[0].update(second_position_group="GK", position_group_ambiguous=True)
        with pytest.raises(C.CohortInputError, match="D5 second group is not outfield"):
            table(cb)

    def test_scored_row_outside_a_metrics_applies_to_raises(self):
        # ADR 004 D21: a metric restricted to a subset of groups must never see a scored row
        # of an excluded group reach it — this is what catches a wrong position_group label.
        narrow = MetricSpec(column="x_p90", display_name="X", family="Defending",
                            direction="higher_better", unit="per90", count_column="x",
                            applies_to=("ST",))
        with pytest.raises(C.CohortInputError, match="outside applies_to"):
            percentile_table(frame(cohort(MIN_N, group="CB")), _derivation(),
                             season_sha256=SHA, specs=(narrow,))

    def test_d9_statuses_null_only_the_global_percentile(self):
        d = _derivation(**{"x_p90|CB": _cell("x_p90", "CB", global_ok=False),
                           "r_pct|CB": _cell("r_pct", "CB", global_ok=None, min_attempts=10)})
        t = table(cohort(MIN_N), d)
        x, r = cell_of(t, 1, "x_p90"), cell_of(t, 1, "r_pct")
        assert x["status_global"] == "league_inconsistent" and x["pct_global"] is None
        assert r["status_global"] == "d9_undefined" and r["pct_global"] is None
        assert x["status_local"] == r["status_local"] == "ok"
        assert x["n_global"] == MIN_N and x["basis"] == "local"

    def test_cohort_too_small_precedes_d9(self):
        d = _derivation(**{"x_p90|CB": _cell("x_p90", "CB", global_ok=False)})
        assert cell_of(table(cohort(MIN_N - 1), d), 1, "x_p90")["status_global"] == "cohort_too_small"

    def test_no_signal_cell(self):
        d = _derivation(**{"r_pct|CB": _cell("r_pct", "CB", min_attempts=None,
                                             reasons=("display", "no_signal"))})
        c = cell_of(table(cohort(MIN_N), d), 1, "r_pct")
        assert c["status_local"] == c["status_global"] == "no_signal"
        assert c["value"] == pytest.approx(15 / 20) and c["pct_global"] is None

    def test_unscored_rows_keep_their_value_and_row_status(self):
        t = table([*cohort(MIN_N), row(900, 3, group="GK"), row(901, 3, pool=False)])
        gk, bl = cell_of(t, 900, "x_p90"), cell_of(t, 901, "x_p90")
        assert gk["status_local"] == "goalkeeper" and gk["basis"] is None and gk["value"] == 3.0
        assert bl["status_global"] == "not_in_pool" and bl["basis"] == "global"

    def test_one_row_per_season_row_metric_and_role(self):
        cb = cohort(MIN_N)
        cb[0].update(second_position_group="ST", position_group_ambiguous=True)
        t = table([*cb, *cohort(MIN_N, group="ST", start=100), row(900, 3, group="GK")])
        assert t.height == (2 * MIN_N + 1 + 1) * len(SPECS)
        assert t.select(list(SORT_KEY)).is_unique().all()

    def test_sorted_and_stamped(self):
        rows = cohort(MIN_N)[::-1]
        t = table(rows)
        assert t.select(list(SORT_KEY)).equals(t.select(list(SORT_KEY)).sort(list(SORT_KEY)))
        assert t["registry_version"].unique().to_list() == [VERSION]
        assert t["measurement_source"].unique().to_list() == ["statsbomb"]
        assert t["season_parquet_sha256"].unique().to_list() == [SHA]
        assert t["min_cohort_size"].unique().to_list() == [MIN_N]
        assert t["min_minutes_threshold"].unique().to_list() == [float(config.MIN_MINUTES_THRESHOLD)]
        assert t["polars_version"].unique().to_list() == [pl.__version__]

    def test_input_order_does_not_change_the_table(self):
        rows = [*cohort(MIN_N), *cohort(MIN_N, comp=2, start=100, team=2)]
        assert table(rows).equals(table(rows[::-1]))
