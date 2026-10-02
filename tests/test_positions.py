"""Tests for src/metrics/positions.py"""
from __future__ import annotations
from pathlib import Path
import pytest
from eleveniq.metrics.minutes import PeriodClock, compute_player_minutes, parse_mm_ss
from eleveniq.metrics.positions import (
    assign_position_group,
    compute_minutes_per_group,
    compute_minutes_per_raw_position,
    primary_position_group,
    POSITION_GROUP_MAP,
    UNKNOWN_POSITION_GROUP,
    GK_GROUP,
    OUTFIELD_GROUPS,
    POSITION_GROUP_COLUMNS,
)


# ---------------------------------------------------------------------------
# Adapter for the value tests below. They were written on the match-absolute axis
# with no first-half stoppage: on a clock whose first half ends exactly at 45:00,
# (1, 45:00) and (2, 45:00) are the same elapsed instant, so every value carries
# over unchanged. Period-aware cases (stoppage, windows, overlaps, position rule)
# are in tests/test_minutes.py.
# ---------------------------------------------------------------------------

def _clock(duration: float) -> PeriodClock:
    return PeriodClock({1: 45.0, 2: duration})


def _instant(minute: float | None):
    if minute is None:
        return None
    return (1, minute) if minute < 45 else (2, minute)


def _with_periods(stints: list[dict]) -> list[dict]:
    out = []
    for s in stints:
        start, end = parse_mm_ss(s["from"]), s["to"] and parse_mm_ss(s["to"])
        out.append({**s, "from_period": 1 if start < 45 else 2,
                    "to_period": None if not s["to"] else (1 if end <= 45 else 2)})
    return out


def _kw(dismissal_minute, substitution_minute):
    caps = [m for m in (dismissal_minute, substitution_minute) if m is not None]
    return dict(exit_at=_instant(min(caps)) if caps else None, treatment_windows=())


def _per_group(stints, match_duration, *, dismissal_minute, substitution_minute):
    return compute_minutes_per_group(_with_periods(stints), _clock(match_duration),
                                     **_kw(dismissal_minute, substitution_minute))


def _per_raw(stints, match_duration, *, dismissal_minute, substitution_minute):
    return compute_minutes_per_raw_position(_with_periods(stints), _clock(match_duration),
                                            **_kw(dismissal_minute, substitution_minute))


def _player(stints, match_duration, *, dismissal_minute, substitution_minute):
    return compute_player_minutes(_with_periods(stints), _clock(match_duration),
                                  **_kw(dismissal_minute, substitution_minute))


# ---------------------------------------------------------------------------
# assign_position_group
# ---------------------------------------------------------------------------

class TestAssignPositionGroup:
    def test_goalkeeper(self):
        assert assign_position_group("Goalkeeper") == "GK"

    def test_center_back(self):
        assert assign_position_group("Center Back") == "CB"
        assert assign_position_group("Left Center Back") == "CB"
        assert assign_position_group("Right Center Back") == "CB"

    def test_fullback_wingback(self):
        assert assign_position_group("Left Back") == "FB-WB"
        assert assign_position_group("Right Back") == "FB-WB"
        assert assign_position_group("Left Wing Back") == "FB-WB"
        assert assign_position_group("Right Wing Back") == "FB-WB"

    def test_dm_cm(self):
        assert assign_position_group("Defensive Midfield") == "DM-CM"
        assert assign_position_group("Center Midfield") == "DM-CM"
        assert assign_position_group("Left Center Midfield") == "DM-CM"
        assert assign_position_group("Right Center Midfield") == "DM-CM"

    def test_center_defensive_midfield(self):
        # Added in Milestone 3: Center Defensive Midfield was missing from the
        # map and caused UNKNOWN assignments in La Liga 2015/16.
        # ADR 002 §5 position table: DM-CM includes "Center Defensive Midfield".
        assert assign_position_group("Center Defensive Midfield") == "DM-CM"

    def test_left_right_defensive_midfield(self):
        assert assign_position_group("Left Defensive Midfield") == "DM-CM"
        assert assign_position_group("Right Defensive Midfield") == "DM-CM"

    def test_am_winger(self):
        assert assign_position_group("Left Wing") == "AM-Winger"
        assert assign_position_group("Right Wing") == "AM-Winger"
        assert assign_position_group("Attacking Midfield") == "AM-Winger"
        assert assign_position_group("Center Attacking Midfield") == "AM-Winger"

    def test_left_right_attacking_midfield_added(self):
        # These were missing in the original map — stats reviewer flagged them
        assert assign_position_group("Left Attacking Midfield") == "AM-Winger"
        assert assign_position_group("Right Attacking Midfield") == "AM-Winger"

    def test_striker(self):
        assert assign_position_group("Center Forward") == "ST"
        assert assign_position_group("Secondary Striker") == "ST"

    def test_unknown_position(self):
        assert assign_position_group("Unknown Role XYZ") == UNKNOWN_POSITION_GROUP

    def test_empty_string_is_unknown(self):
        assert assign_position_group("") == UNKNOWN_POSITION_GROUP


class TestGroupConstants:
    def test_gk_group_constant(self):
        assert GK_GROUP == "GK"

    def test_gk_not_in_outfield_groups(self):
        assert "GK" not in OUTFIELD_GROUPS

    def test_all_outfield_groups_present(self):
        assert OUTFIELD_GROUPS == {"CB", "FB-WB", "DM-CM", "AM-Winger", "ST"}

    def test_all_map_values_are_known_groups(self):
        known = OUTFIELD_GROUPS | {GK_GROUP}
        for position, group in POSITION_GROUP_MAP.items():
            assert group in known, f"Position '{position}' maps to unknown group '{group}'"


# ---------------------------------------------------------------------------
# POSITION_GROUP_COLUMNS — canonical group→column mapping (new constant)
# ---------------------------------------------------------------------------

class TestPositionGroupColumns:
    """
    POSITION_GROUP_COLUMNS maps each canonical position group to the column name
    used in player_match_stats.parquet.  The docstring states it is stored in
    alphabetical order so pipeline code can rely on insertion order for the
    lexicographic tie-break rule (AM-Winger first).
    """

    _EXPECTED_GROUPS = frozenset({
        "AM-Winger", "CB", "DM-CM", "FB-WB", "GK", "ST", "UNKNOWN",
    })

    def test_all_canonical_groups_present_as_keys(self):
        # Every group used elsewhere in the pipeline must have an entry.
        assert set(POSITION_GROUP_COLUMNS.keys()) == self._EXPECTED_GROUPS

    def test_no_extra_keys(self):
        # The map must not silently include groups that do not exist in the taxonomy.
        assert set(POSITION_GROUP_COLUMNS.keys()) <= self._EXPECTED_GROUPS

    def test_column_name_format(self):
        # Each value must be "minutes_" followed by the lower-cased, hyphen-stripped
        # group name.  Spot-check every entry.
        expected = {
            "AM-Winger": "minutes_am_winger",
            "CB":        "minutes_cb",
            "DM-CM":     "minutes_dm_cm",
            "FB-WB":     "minutes_fb_wb",
            "GK":        "minutes_gk",
            "ST":        "minutes_st",
            "UNKNOWN":   "minutes_unknown",
        }
        assert dict(POSITION_GROUP_COLUMNS) == expected

    def test_keys_in_alphabetical_order(self):
        # Docstring contract: "Alphabetical order so pipeline code can rely on
        # insertion order for tie-breaks (AM-Winger sorts first)."
        keys = list(POSITION_GROUP_COLUMNS.keys())
        assert keys == sorted(keys), (
            f"POSITION_GROUP_COLUMNS keys are not in alphabetical order: {keys}"
        )

    def test_am_winger_is_first_key(self):
        # AM-Winger must be the first key so that when all group minutes are equal
        # the lexicographic tie-break resolves to AM-Winger (the alphabetically
        # smallest group name).  See ADR 002 §4 and milestones.md tech debt.
        first_key = next(iter(POSITION_GROUP_COLUMNS))
        assert first_key == "AM-Winger", (
            "AM-Winger must be the first key in POSITION_GROUP_COLUMNS "
            "(alphabetical tie-break contract, ADR 002 §4)"
        )

    def test_all_values_are_strings(self):
        for group, col in POSITION_GROUP_COLUMNS.items():
            assert isinstance(col, str), f"Column name for {group!r} must be a str, got {type(col)}"

    def test_all_values_start_with_minutes_prefix(self):
        for group, col in POSITION_GROUP_COLUMNS.items():
            assert col.startswith("minutes_"), (
                f"Column name for {group!r} must start with 'minutes_' but got {col!r}"
            )

    def test_column_names_are_unique(self):
        # Each group must map to a distinct column name — duplicates would corrupt
        # the schema and cause silent merging of different groups' minutes.
        values = list(POSITION_GROUP_COLUMNS.values())
        assert len(values) == len(set(values)), (
            "POSITION_GROUP_COLUMNS has duplicate column names: " +
            str([v for v in values if values.count(v) > 1])
        )

    def test_gk_not_in_outfield_columns(self):
        # OUTFIELD_GROUPS excludes GK (ADR 002 §4); the column minutes_gk must
        # still exist so GK season minutes are stored, but the group key 'GK' is
        # not in OUTFIELD_GROUPS.
        assert "GK" not in OUTFIELD_GROUPS
        assert "GK" in POSITION_GROUP_COLUMNS, "GK must have a column entry"

    def test_exact_count_is_seven(self):
        # The taxonomy has exactly 7 groups: AM-Winger, CB, DM-CM, FB-WB, GK, ST, UNKNOWN.
        # A silent addition or removal of a group without updating the column map
        # would cause _group_minutes_to_columns to raise or silently drop minutes.
        assert len(POSITION_GROUP_COLUMNS) == 7, (
            f"Expected exactly 7 entries in POSITION_GROUP_COLUMNS but found "
            f"{len(POSITION_GROUP_COLUMNS)}: {sorted(POSITION_GROUP_COLUMNS)}"
        )


# ---------------------------------------------------------------------------
# compute_minutes_per_group
# ---------------------------------------------------------------------------

class TestComputeMinutesPerGroup:
    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_single_stint_full_match(self):
        stints = [self._stint("Center Back", "00:00", None)]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"CB": pytest.approx(90.0)}

    def test_single_stint_sub(self):
        stints = [self._stint("Center Forward", "60:00", None)]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"ST": pytest.approx(30.0)}

    def test_tactical_shift_same_group(self):
        stints = [
            self._stint("Left Center Midfield", "00:00", "55:00"),
            self._stint("Left Defensive Midfield", "55:00", None),
        ]
        result = _per_group(stints, match_duration=92.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"DM-CM": pytest.approx(92.0)}

    def test_tactical_shift_different_groups(self):
        stints = [
            self._stint("Center Midfield", "00:00", "70:00"),
            self._stint("Center Attacking Midfield", "70:00", None),
        ]
        result = _per_group(stints, match_duration=92.0, dismissal_minute=None, substitution_minute=None)
        assert result["DM-CM"] == pytest.approx(70.0)
        assert result["AM-Winger"] == pytest.approx(22.0)

    def test_unused_sub_returns_empty(self):
        assert _per_group([], match_duration=90.0, dismissal_minute=None, substitution_minute=None) == {}

    def test_dismissal_caps_open_stint(self):
        stints = [self._stint("Center Back", "00:00", None)]
        result = _per_group(stints, match_duration=92.0, dismissal_minute=15.0, substitution_minute=None)
        assert result == {"CB": pytest.approx(15.0)}

    def test_position_as_dict_handled(self):
        # Robustness: position stored as {"name": "..."} dict form
        stints = [{"position": {"name": "Right Wing"}, "from": "00:00", "to": None}]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"AM-Winger": pytest.approx(90.0)}

    def test_unknown_position_goes_to_unknown_key(self):
        stints = [self._stint("Libero", "00:00", "90:00")]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"UNKNOWN": pytest.approx(90.0)}

    def test_none_position_goes_to_unknown_not_crash(self):
        # StatsBomb JSON with "position": null — must not str(None) → "None"
        stints = [{"position": None, "from": "00:00", "to": "45:00"}]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"UNKNOWN": pytest.approx(45.0)}

    def test_zero_length_stint_omitted(self):
        stints = [self._stint("Left Back", "45:00", "45:00")]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {}

    def test_explicit_to_after_dismissal_clamped(self):
        # Gustavo-Cabral-like: first stint has explicit to=66:43 but card at 56:10.
        # Without clamping the full stint would count as 66.72 min.
        stints = [
            self._stint("Right Center Back", "00:00", "66:43"),
            self._stint("Right Center Midfield", "66:43", None),
        ]
        result = _per_group(stints, match_duration=95.0, dismissal_minute=56.17, substitution_minute=None)
        assert sum(result.values()) == pytest.approx(56.17)

    def test_explicit_to_before_dismissal_unchanged(self):
        # Substituted off at 45:00, card at 60:00 — the 45-min stint must not be extended.
        stints = [self._stint("Center Back", "00:00", "45:00")]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=60.0, substitution_minute=None)
        assert result == {"CB": pytest.approx(45.0)}

    def test_explicit_to_after_the_whistle_is_clamped(self):
        # ADR 002 §31.1: a `to` after its period's Half End is clamped by the clock;
        # the pipeline fails the build on it (stint_end_after_period_end).
        stints = [self._stint("Left Back", "00:00", "90:05")]
        result = _per_group(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {"FB-WB": pytest.approx(90.0)}


# ---------------------------------------------------------------------------
# primary_position_group
# ---------------------------------------------------------------------------

class TestPrimaryPositionGroup:
    def test_single_group(self):
        assert primary_position_group({"CB": 90.0}) == "CB"

    def test_most_minutes_wins(self):
        groups = {"DM-CM": 70.0, "AM-Winger": 20.0}
        assert primary_position_group(groups) == "DM-CM"

    def test_tie_broken_alphabetically_first(self):
        # DM-CM and AM-Winger have equal minutes; AM-Winger sorts before DM-CM
        groups = {"DM-CM": 45.0, "AM-Winger": 45.0}
        assert primary_position_group(groups) == "AM-Winger"

    def test_tie_cb_vs_fb_wb(self):
        # CB < FB-WB alphabetically → CB wins tie
        groups = {"CB": 45.0, "FB-WB": 45.0}
        assert primary_position_group(groups) == "CB"

    def test_empty_returns_unknown(self):
        assert primary_position_group({}) == UNKNOWN_POSITION_GROUP

    def test_three_groups_most_minutes_wins(self):
        groups = {"CB": 20.0, "DM-CM": 50.0, "AM-Winger": 30.0}
        assert primary_position_group(groups) == "DM-CM"

    def test_gk_can_be_primary(self):
        assert primary_position_group({"GK": 90.0}) == "GK"


# ---------------------------------------------------------------------------
# Integration test: no UNKNOWN positions in pipeline output
# ---------------------------------------------------------------------------

_PLAYER_MATCH_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "interim" / "player_match_stats.parquet"
)


# ---------------------------------------------------------------------------
# Cross-consistency: sum(compute_minutes_per_group) == compute_player_minutes
# ---------------------------------------------------------------------------

class TestMinutesConsistency:
    """
    Verifies that the shared player_timeline keeps compute_minutes_per_group
    and compute_player_minutes in sync.
    sum(group minutes) must equal total player minutes for any input.
    """

    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_starter_full_match(self):
        positions = [self._stint("Center Back", "00:00", None)]
        duration = 92.0
        group_total = sum(_per_group(positions, duration, dismissal_minute=None, substitution_minute=None).values())
        assert group_total == pytest.approx(_player(positions, duration, dismissal_minute=None, substitution_minute=None))

    def test_sub_on_halfway(self):
        positions = [self._stint("Center Forward", "60:00", None)]
        duration = 93.0
        group_total = sum(_per_group(positions, duration, dismissal_minute=None, substitution_minute=None).values())
        assert group_total == pytest.approx(_player(positions, duration, dismissal_minute=None, substitution_minute=None))

    def test_tactical_shift_two_groups(self):
        positions = [
            self._stint("Center Midfield", "00:00", "55:00"),
            self._stint("Center Attacking Midfield", "55:00", None),
        ]
        duration = 92.0
        group_total = sum(_per_group(positions, duration, dismissal_minute=None, substitution_minute=None).values())
        assert group_total == pytest.approx(_player(positions, duration, dismissal_minute=None, substitution_minute=None))

    def test_with_dismissal(self):
        positions = [self._stint("Left Back", "00:00", None)]
        duration = 90.0
        d_min = 45.0
        group_total = sum(
            _per_group(positions, duration, dismissal_minute=d_min, substitution_minute=None).values()
        )
        assert group_total == pytest.approx(
            _player(positions, duration, dismissal_minute=d_min, substitution_minute=None)
        )

    def test_with_substitution_off(self):
        """Own substitution caps explicit and open-ended stints alike (ADR 002 §3)."""
        positions = [
            self._stint("Center Forward", "00:00", "31:29"),
            self._stint("Center Forward", "37:02", None),
        ]
        duration, s_min = 93.0, 37 + 2 / 60
        group_total = sum(
            _per_group(positions, duration, dismissal_minute=None, substitution_minute=s_min).values()
        )
        player_total = _player(positions, duration, dismissal_minute=None, substitution_minute=s_min)
        assert group_total == pytest.approx(player_total)
        assert group_total == pytest.approx(31 + 29 / 60)

    def test_dismissal_and_substitution_use_the_earlier_cap(self):
        positions = [self._stint("Left Back", "00:00", "80:00")]
        for d_min, s_min in ((40.0, 60.0), (60.0, 40.0)):
            group_total = sum(_per_group(
                positions, 90.0, dismissal_minute=d_min, substitution_minute=s_min).values())
            player_total = _player(
                positions, 90.0, dismissal_minute=d_min, substitution_minute=s_min)
            assert group_total == pytest.approx(player_total) == pytest.approx(40.0)

    def test_unused_sub_both_zero(self):
        group_total = sum(_per_group([], 90.0, dismissal_minute=None, substitution_minute=None).values())
        assert group_total == pytest.approx(_player([], 90.0, dismissal_minute=None, substitution_minute=None))

    def test_explicit_to_past_dismissal_consistency(self):
        """Consistency holds when explicit to-values are clamped by the dismissal."""
        positions = [
            self._stint("Right Center Back", "00:00", "66:43"),
            self._stint("Right Center Midfield", "66:43", "81:27"),
            self._stint("Left Center Midfield", "81:27", None),
        ]
        duration = 95.0
        d_min = 56.17
        group_total = sum(
            _per_group(positions, duration, dismissal_minute=d_min, substitution_minute=None).values()
        )
        player_total = _player(positions, duration, dismissal_minute=d_min, substitution_minute=None)
        assert group_total == pytest.approx(player_total)
        assert group_total == pytest.approx(d_min)

    def test_raw_position_sum_equals_group_sum(self):
        """compute_minutes_per_raw_position and compute_minutes_per_group share the same generator."""
        positions = [
            self._stint("Center Midfield", "00:00", "70:00"),
            self._stint("Left Wing", "70:00", None),
        ]
        duration = 93.0
        raw_total = sum(_per_raw(positions, duration, dismissal_minute=None, substitution_minute=None).values())
        group_total = sum(_per_group(positions, duration, dismissal_minute=None, substitution_minute=None).values())
        assert raw_total == pytest.approx(group_total)


# ---------------------------------------------------------------------------
# compute_minutes_per_raw_position — direct contract tests
# ---------------------------------------------------------------------------

class TestComputeMinutesPerRawPosition:
    """
    compute_minutes_per_raw_position preserves the StatsBomb position name
    as the key (not the group).  Its docstring guarantees sum of values
    equals sum(_per_group(...).values()) for the same inputs.
    """

    def _stint(self, position: str, from_str: str, to_str=None) -> dict:
        return {"position": position, "from": from_str, "to": to_str}

    def test_empty_positions_returns_empty_dict(self):
        # Docstring: "Returns an empty dict for players who never took the field."
        result = _per_raw([], match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result == {}

    def test_keys_are_raw_position_names_not_groups(self):
        # Center Back → group CB, but the raw key must be "Center Back".
        stints = [self._stint("Center Back", "00:00", None)]
        result = _per_raw(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert "Center Back" in result
        assert "CB" not in result

    def test_two_positions_two_keys(self):
        # Tactical shift: two different StatsBomb positions must produce two keys.
        stints = [
            self._stint("Defensive Midfield", "00:00", "60:00"),
            self._stint("Center Midfield", "60:00", None),
        ]
        result = _per_raw(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert "Defensive Midfield" in result
        assert "Center Midfield" in result
        assert result["Defensive Midfield"] == pytest.approx(60.0)
        assert result["Center Midfield"] == pytest.approx(30.0)

    def test_two_stints_same_position_merged(self):
        # Same position in non-contiguous stints must be accumulated into one key.
        stints = [
            self._stint("Left Back", "00:00", "45:00"),
            self._stint("Right Back", "45:00", "75:00"),
            self._stint("Left Back", "75:00", None),
        ]
        result = _per_raw(stints, match_duration=90.0, dismissal_minute=None, substitution_minute=None)
        assert result["Left Back"] == pytest.approx(45.0 + 15.0)
        assert result["Right Back"] == pytest.approx(30.0)

    def test_dismissal_clamping_applied(self):
        # Same dismissal clamping as compute_minutes_per_group must be applied.
        stints = [self._stint("Center Back", "00:00", None)]
        result = _per_raw(
            stints, match_duration=90.0, dismissal_minute=30.0, substitution_minute=None
        )
        assert result == {"Center Back": pytest.approx(30.0)}


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_MATCH_PARQUET.exists(),
    reason="requires pipeline output — run 'python -m eleveniq.pipeline.build_player_match' first",
)
def test_no_unknown_positions_in_pipeline_output():
    """After the pipeline runs, every position_raw in the output must map to a known group."""
    import polars as pl
    df = pl.read_parquet(_PLAYER_MATCH_PARQUET)
    unknowns = (
        df.filter(pl.col("position_group") == "UNKNOWN")["position_raw"]
        .unique()
        .to_list()
    )
    assert unknowns == [], (
        f"position_raw values mapping to UNKNOWN — add them to POSITION_GROUP_MAP: {unknowns}"
    )


_PLAYER_SEASON_PARQUET = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "player_season_stats.parquet"
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _PLAYER_SEASON_PARQUET.exists(),
    reason="requires data/processed/player_season_stats.parquet (run build_player_season)",
)
def test_no_unknown_minutes_in_season_output():
    """UNKNOWN minutes fail the build (plan 004 H1.1), so no season row carries any."""
    import polars as pl
    season = pl.read_parquet(_PLAYER_SEASON_PARQUET)
    assert season["minutes_unknown"].sum() == 0
    assert season.filter(pl.col("position_group") == "UNKNOWN").height == 0


@pytest.mark.parametrize("fn", [compute_minutes_per_group, compute_minutes_per_raw_position])
@pytest.mark.parametrize("missing", ["exit_at", "treatment_windows"])
def test_exit_and_windows_are_required(fn, missing):
    # A forgotten argument must not silently give uncapped minutes (plan 004 H3a).
    kwargs = {"exit_at": None, "treatment_windows": ()}
    del kwargs[missing]
    stints = [{"from": "00:00", "from_period": 1, "to": None, "position": "Center Back"}]
    with pytest.raises(TypeError, match=missing):
        fn(stints, _clock(90.0), **kwargs)


# ---------------------------------------------------------------------------
# ADR 002 §31.4: Σ minutes_<group> = minutes_played on the period clock, also
# with stoppage, overlaps, reversed stints, treatment windows and an exit.
# (TestMinutesConsistency above runs only on a 45:00 first half, no windows.)
# ---------------------------------------------------------------------------

def _pa_stint(frm: float, fp: int, to: float | None = None, tp: int | None = None,
              position: str = "Center Back", reason: str = "Starting XI") -> dict:
    def t(m: float) -> str:
        return f"{int(m):02d}:{round((m - int(m)) * 60):02d}"
    return {"position": position, "from": t(frm), "from_period": fp,
            "to": None if to is None else t(to), "to_period": tp, "start_reason": reason}


class TestGroupSumOnPeriodClock:
    CLOCK = PeriodClock({1: 47.0, 2: 93.0})   # 47 + 48 = 95 min

    @pytest.mark.parametrize("positions, exit_at, windows, expected", [
        # Ramsey/Pléa shape: reversed stint marks CF for the second half; then
        # a window (2, 60)-(2, 62) and an exit at (2, 85) both come out of ST.
        ([_pa_stint(0, 1, 46.5, 1),
          _pa_stint(46, 2, 46.5, 1, "Center Forward", "Tactical Shift"),
          _pa_stint(46.5, 1, position="Left Back", reason="Tactical Shift")],
         (2, 85.0), [((2, 60.0), (2, 62.0))],
         {"CB": 46.5, "FB-WB": 1.5, "ST": 37.0}),
        # Overlap counted once, latest start gives LW from (2, 48); a window in
        # the first half comes out of DM-CM.
        ([_pa_stint(0, 1, 50, 2, "Center Midfield"),
          _pa_stint(48, 2, position="Left Wing", reason="Tactical Shift")],
         None, [((1, 10.0), (1, 12.0))],
         {"DM-CM": 48.0, "AM-Winger": 45.0}),
        # Dismissal in first-half stoppage.
        ([_pa_stint(0, 1, position="Goalkeeper")], (1, 46.0), [], {"GK": 46.0}),
    ], ids=["reversed_window_exit", "overlap_window", "stoppage_card"])
    def test_group_raw_and_total_agree(self, positions, exit_at, windows, expected):
        kw = dict(exit_at=exit_at, treatment_windows=windows)
        groups = compute_minutes_per_group(positions, self.CLOCK, **kw)
        raw = compute_minutes_per_raw_position(positions, self.CLOCK, **kw)
        total = compute_player_minutes(positions, self.CLOCK, **kw)
        assert groups == pytest.approx(expected)
        assert sum(groups.values()) == pytest.approx(total)
        assert sum(raw.values()) == pytest.approx(total)
