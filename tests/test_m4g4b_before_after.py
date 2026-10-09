"""
M4g4b STOP rule (spikes/m4g4b_before_after.py; plan 005 M4g4b): every measurement section
except provenance identical; in provenance only the two shas and season_columns may change,
and season_columns gains exactly the 9 M4g4b columns, keeping every old column in order; the
registry changes only its version, which must change. No data needed.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spikes"))
import m4g4b_before_after as ba  # noqa: E402

NEW = sorted(ba.NEW_COLUMNS)


def before() -> dict:
    return {"movers": {"m": 1}, "cohorts": {"x": 1}, "schema_version": 2,
            "provenance": {"match_parquet_sha256": "a", "season_parquet_sha256": "b",
                           "season_columns": ["player_id", "minutes_played"], "season_id": 27}}


def after() -> dict:
    a = copy.deepcopy(before())
    a["provenance"].update(match_parquet_sha256="c", season_parquet_sha256="d",
                           season_columns=["player_id", *NEW, "minutes_played"])
    return a


def test_only_provenance_moves():
    assert ba.measurement_errors(before(), after()) == []


def test_a_changed_section_or_provenance_key_stops():
    a = after()
    a["movers"]["m"] = 2
    assert ba.measurement_errors(before(), a)
    a = after()
    a["provenance"]["season_id"] = 28
    assert ba.measurement_errors(before(), a)


def test_season_columns_must_add_exactly_the_nine_and_keep_the_old_ones():
    a = after()
    a["provenance"]["season_columns"] = ["player_id", *NEW[:-1], "minutes_played"]
    assert ba.measurement_errors(before(), a)
    a = after()
    a["provenance"]["season_columns"] = [*NEW, "minutes_played", "player_id"]   # reordered
    assert ba.measurement_errors(before(), a)
    a = after()
    a["provenance"]["season_columns"] = ["player_id", *NEW]                       # lost one
    assert ba.measurement_errors(before(), a)


def test_shas_must_be_the_current_parquets():
    assert ba.measurement_errors(before(), after(), {"match_parquet_sha256": "c"}) == []
    assert ba.measurement_errors(before(), after(), {"match_parquet_sha256": "zz"})


def test_registry_only_the_version_may_change():
    r = {"cells": [1], "registry_version": "v1"}
    assert ba.registry_errors(r, {**r, "registry_version": "v2"}) == []
    assert ba.registry_errors(r, r)
    assert ba.registry_errors(r, {"cells": [2], "registry_version": "v2"})
