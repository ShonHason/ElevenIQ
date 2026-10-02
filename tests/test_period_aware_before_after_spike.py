"""
The STOP rule of spikes/period_aware_before_after.py (plan 004 H4b, ADR 002
§31.7): any STOP condition must exit non-zero so a rerun cannot miss it. No
data/ files are read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import period_aware_before_after as spike  # noqa: E402


def test_no_stop_condition_returns(capsys):
    spike.report_stop([])
    assert "no STOP condition" in capsys.readouterr().out


def test_stop_condition_exits_1_and_prints_every_stop(capsys):
    with pytest.raises(SystemExit) as exc:
        spike.report_stop(["is_outfield changed", "2 rows fell below the 900-minute gate"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "STOP: is_outfield changed" in out
    assert "STOP: 2 rows fell below the 900-minute gate" in out
