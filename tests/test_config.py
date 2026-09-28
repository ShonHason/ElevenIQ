"""
Tests for src/eleveniq/config.py.

config.py exports project-wide constants that gate percentile calculations,
similarity, and Gem Finder.  All configurable thresholds must be tested so
that accidental edits are caught immediately and the ADR-documented values
are enforced by the test suite.
"""
from __future__ import annotations

import importlib

import pytest

import eleveniq.config as config_module


# ---------------------------------------------------------------------------
# MIN_MINUTES_THRESHOLD — value and type contract (ADR 002 §5)
# ---------------------------------------------------------------------------

class TestMinMinutesThreshold:
    """
    ADR 002 §5: the minimum-minutes threshold must be 900 and must be an int.
    build_player_season.aggregate_player_season uses it as the default value
    for the min_minutes parameter.
    """

    def test_default_value_is_900(self):
        assert config_module.MIN_MINUTES_THRESHOLD == 900

    def test_is_integer_type(self):
        assert isinstance(config_module.MIN_MINUTES_THRESHOLD, int), (
            "MIN_MINUTES_THRESHOLD must be int so it can be used directly "
            "in >= comparisons without float precision issues"
        )

    def test_is_positive(self):
        assert config_module.MIN_MINUTES_THRESHOLD > 0

    def test_module_is_importable(self):
        """Verify the module can be cleanly re-imported (no import-time side effects)."""
        reloaded = importlib.reload(config_module)
        assert reloaded.MIN_MINUTES_THRESHOLD == 900

    def test_attribute_name_is_exported(self):
        """Constant must be accessible by name — guards against accidental rename."""
        assert hasattr(config_module, "MIN_MINUTES_THRESHOLD")


class TestConfigUsedAsDefault:
    """
    build_player_season.aggregate_player_season must default min_minutes to
    MIN_MINUTES_THRESHOLD, not to a hard-coded literal.  If the constant changes
    the default must follow.
    """

    def test_aggregate_player_season_default_equals_constant(self):
        import inspect
        from eleveniq.pipeline.build_player_season import aggregate_player_season

        sig = inspect.signature(aggregate_player_season)
        param = sig.parameters.get("min_minutes")
        assert param is not None, "aggregate_player_season must have a min_minutes parameter"
        assert param.default == config_module.MIN_MINUTES_THRESHOLD, (
            f"aggregate_player_season default {param.default!r} "
            f"!= MIN_MINUTES_THRESHOLD {config_module.MIN_MINUTES_THRESHOLD!r}; "
            "the default must reference the constant, not a hard-coded value"
        )


# ---------------------------------------------------------------------------
# LONG_PASS_THRESHOLD_UNITS — Chunk B rename from _YDS (ADR 002 §19, §24)
# ---------------------------------------------------------------------------

class TestLongPassThresholdUnits:
    """
    ADR 002 §24 (Chunk B): LONG_PASS_THRESHOLD_YDS was renamed to
    LONG_PASS_THRESHOLD_UNITS.  The value (30) did not change.
    Verify the new name exists, the old name does not, and the value is correct.
    """

    def test_long_pass_threshold_units_exists(self):
        assert hasattr(config_module, "LONG_PASS_THRESHOLD_UNITS"), (
            "LONG_PASS_THRESHOLD_UNITS must exist in config.py after Chunk B rename"
        )

    def test_long_pass_threshold_units_value(self):
        assert config_module.LONG_PASS_THRESHOLD_UNITS == 30, (
            "ADR 002 §19: threshold value must remain 30 after rename"
        )

    def test_old_yds_name_removed(self):
        assert not hasattr(config_module, "LONG_PASS_THRESHOLD_YDS"), (
            "LONG_PASS_THRESHOLD_YDS must be removed; it was renamed to "
            "LONG_PASS_THRESHOLD_UNITS in Chunk B (ADR 002 §24)"
        )


# ---------------------------------------------------------------------------
# XG_ASSISTED_MAX_CARRY_UNITS — new constant in Chunk B (ADR 002 §26)
# ---------------------------------------------------------------------------

class TestXgAssistedMaxCarryUnits:
    """
    ADR 002 §26: XG_ASSISTED_MAX_CARRY_UNITS is a new constant (10.0) that
    controls the carry-distance threshold for the xg_assisted_direct metric.
    """

    def test_xg_assisted_max_carry_units_exists(self):
        assert hasattr(config_module, "XG_ASSISTED_MAX_CARRY_UNITS"), (
            "XG_ASSISTED_MAX_CARRY_UNITS must be present in config.py (ADR 002 §26)"
        )

    def test_xg_assisted_max_carry_units_value(self):
        assert config_module.XG_ASSISTED_MAX_CARRY_UNITS == 10.0, (
            "ADR 002 §26: threshold must be 10.0 StatsBomb units"
        )

    def test_xg_assisted_max_carry_units_is_float(self):
        assert isinstance(config_module.XG_ASSISTED_MAX_CARRY_UNITS, float), (
            "XG_ASSISTED_MAX_CARRY_UNITS must be float so it can be used in "
            "cumulative carry comparisons without integer truncation"
        )


# ---------------------------------------------------------------------------
# COMPETITION_SEASONS — Plan 003 / ADR 003 §3
# ---------------------------------------------------------------------------

class TestCompetitionSeasons:
    def test_five_2015_16_leagues_with_adr_003_classes(self):
        got = {
            (cs.competition_id, cs.season_id): (cs.expected_teams, cs.coverage)
            for cs in config_module.COMPETITION_SEASONS
        }
        assert got == {
            (11, 27): (20, "FULL"),
            (2, 27): (20, "FULL"),
            (12, 27): (20, "FULL"),
            (7, 27): (20, "FULL"),
            (9, 27): (18, "PARTIAL"),
        }

    def test_keys_unique(self):
        keys = [(cs.competition_id, cs.season_id) for cs in config_module.COMPETITION_SEASONS]
        assert len(keys) == len(set(keys))

    def test_entries_are_immutable(self):
        cs = config_module.COMPETITION_SEASONS[0]
        with pytest.raises(AttributeError):
            cs.coverage = "PARTIAL"  # type: ignore[misc]


class TestCompetitionSeasonSource:
    """ADR 003 §5: source is part of the pool key and has no default."""

    def test_every_entry_is_statsbomb(self):
        assert {cs.source for cs in config_module.COMPETITION_SEASONS} == {"statsbomb"}

    def test_source_is_required(self):
        with pytest.raises(TypeError):
            config_module.CompetitionSeason(1, 1, "X", 2, "FULL")  # type: ignore[call-arg]

    def test_duplicate_keys_rejected(self):
        cs = config_module.CompetitionSeason(1, 1, "X", 2, "FULL", "statsbomb")
        with pytest.raises(ValueError, match="Duplicate"):
            config_module.validate_competition_seasons((cs, cs))

    def test_same_ids_different_source_allowed(self):
        config_module.validate_competition_seasons((
            config_module.CompetitionSeason(1, 1, "X", 2, "FULL", "statsbomb"),
            config_module.CompetitionSeason(1, 1, "X", 2, "FULL", "sportmonks"),
        ))


class TestSourceStatsbombConstant:
    def test_value(self):
        assert config_module.SOURCE_STATSBOMB == "statsbomb"


class TestExpectedTeamsValidation:
    """ADR 003 §2: a league needs n·(n−1) > 0 fixtures, so expected_teams >= 2."""

    @pytest.mark.parametrize("n", [0, 1])
    def test_below_two_rejected(self, n):
        cs = config_module.CompetitionSeason(1, 1, "Tiny", n, "FULL", "statsbomb")
        with pytest.raises(ValueError, match="expected_teams must be >= 2.*Tiny"):
            config_module.validate_competition_seasons((cs,))

    def test_two_accepted(self):
        config_module.validate_competition_seasons(
            (config_module.CompetitionSeason(1, 1, "Pair", 2, "FULL", "statsbomb"),)
        )


class TestCoverageValueValidation:
    """ADR 003 §5: coverage drives in_percentile_pool, so only exact
    "FULL" / "PARTIAL" are accepted (Literal is not enforced at runtime)."""

    def test_miscased_full_rejected(self):
        cs = config_module.CompetitionSeason(1, 1, "Typo", 20, "Full", "statsbomb")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match='coverage must be "FULL" or "PARTIAL".*Typo'):
            config_module.validate_competition_seasons((cs,))

    @pytest.mark.parametrize("coverage", ["FULL", "PARTIAL"])
    def test_valid_values_accepted(self, coverage):
        config_module.validate_competition_seasons(
            (config_module.CompetitionSeason(1, 1, "Ok", 20, coverage, "statsbomb"),)
        )
