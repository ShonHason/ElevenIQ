"""
Load StatsBomb JSON files from disk into Python dicts / lists.

All functions return plain Python objects (dicts/lists) so that callers in the
pipeline layer can work without any StatsBomb-specific SDK dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "raw" / "statsbomb"


def load_competitions(data_root: Path | None = None) -> list[dict]:
    root = data_root or _DATA_ROOT
    with open(root / "competitions.json") as f:
        return json.load(f)


def load_matches(
    competition_id: int,
    season_id: int,
    data_root: Path | None = None,
) -> list[dict]:
    root = data_root or _DATA_ROOT
    path = root / "matches" / str(competition_id) / f"{season_id}.json"
    with open(path) as f:
        return json.load(f)


def load_events(match_id: int, data_root: Path | None = None) -> list[dict]:
    root = data_root or _DATA_ROOT
    path = root / "events" / f"{match_id}.json"
    with open(path) as f:
        return json.load(f)


def load_lineups(match_id: int, data_root: Path | None = None) -> list[dict]:
    """Returns a list of two team dicts, each with a 'lineup' key."""
    root = data_root or _DATA_ROOT
    path = root / "lineups" / f"{match_id}.json"
    with open(path) as f:
        return json.load(f)
