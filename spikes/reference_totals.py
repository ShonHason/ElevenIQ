"""
Shared helpers for the verification spikes (Plan 003 §3).

Stdlib only and never imports eleveniq.pipeline: reference figures must be
derived from raw StatsBomb JSON independently of the code they verify.

- parse_args():        --competition-id / --season-id CLI.
- load_matches():      matches/{comp}/{season}.json.
- reconcile_goals():   event goals + own goals must equal the sum of
                       home_score + away_score in matches.json, else SystemExit.
- write_reference():   merge a section of totals into
                       tests/fixtures/reference_totals/{comp}_{season}.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data" / "raw" / "statsbomb"
REFERENCE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "reference_totals"


def parse_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--competition-id", type=int, required=True)
    parser.add_argument("--season-id", type=int, required=True)
    parser.add_argument(
        "--no-write", action="store_true",
        help="Print the report only; do not update the reference file",
    )
    return parser.parse_args()


def load_matches(competition_id: int, season_id: int) -> list[dict]:
    path = DATA_ROOT / "matches" / str(competition_id) / f"{season_id}.json"
    with open(path) as f:
        return json.load(f)


def competition_label(matches: list[dict]) -> str:
    m = matches[0]
    return f"{m['competition']['competition_name']} {m['season']['season_name']}"


def reconcile_goals(matches: list[dict], events: list[dict]) -> tuple[int, int]:
    """
    Count Shot-outcome=Goal events and 'Own Goal Against' events, and check
    that their sum equals the scores in matches.json. Raises SystemExit on a
    mismatch so no reference file is written from unreconciled data.
    """
    goals = sum(
        1 for e in events
        if e["type"]["name"] == "Shot"
        and ((e.get("shot") or {}).get("outcome") or {}).get("name") == "Goal"
    )
    own_goals = sum(1 for e in events if e["type"]["name"] == "Own Goal Against")
    official = sum(m["home_score"] + m["away_score"] for m in matches)
    print(
        f"Goal reconciliation: goals {goals:,} + own goals {own_goals:,} = "
        f"{goals + own_goals:,}  vs matches.json scores {official:,}"
    )
    if goals + own_goals != official:
        raise SystemExit(
            f"FAIL: goals + own goals ({goals + own_goals}) != "
            f"sum of matches.json scores ({official})"
        )
    return goals, own_goals


def write_reference(
    competition_id: int, season_id: int, section: str, totals: dict
) -> Path:
    """Merge `totals` under `section` into the reference file (other sections kept)."""
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = REFERENCE_DIR / f"{competition_id}_{season_id}.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data["competition_id"] = competition_id
    data["season_id"] = season_id
    data[section] = totals
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    print(f"Wrote section {section!r} → {path}")
    return path
