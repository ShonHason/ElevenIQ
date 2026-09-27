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
