"""
Download StatsBomb open-data JSON files from GitHub.

Files are cached locally under data/raw/statsbomb/ using the same path structure
as the upstream repository. Any file that already exists on disk is skipped so
the command is idempotent.

Usage (programmatic):
    from eleveniq.statsbomb.downloader import download_competition_season
    download_competition_season(competition_id=11, season_id=27)
"""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_URL = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
)
_DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "raw" / "statsbomb"


def _fetch(remote_path: str, local_path: Path) -> bool:
    """Download remote_path under _BASE_URL to local_path. Returns True if downloaded."""
    if local_path.exists():
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_BASE_URL}/{remote_path}"
    try:
        urllib.request.urlretrieve(url, local_path)
    except Exception as exc:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc
    return True


def download_competitions() -> Path:
    """Download competitions.json. Returns local path."""
    local = _DATA_ROOT / "competitions.json"
    downloaded = _fetch("competitions.json", local)
    if downloaded:
        logger.info("Downloaded competitions.json")
    return local


def download_competition_season(
    competition_id: int,
    season_id: int,
    *,
    data_root: Path | None = None,
) -> dict[str, int]:
    """
    Download all matches, events, and lineups for one competition-season.

    Returns a summary dict:
        {"matches": total, "events_downloaded": n, "events_skipped": m, ...}
    """
    root = data_root or _DATA_ROOT

    # competitions
    comps_local = root / "competitions.json"
    _fetch("competitions.json", comps_local)

    # matches list
    matches_remote = f"matches/{competition_id}/{season_id}.json"
    matches_local = root / "matches" / str(competition_id) / f"{season_id}.json"
    _fetch(matches_remote, matches_local)

    with open(matches_local) as f:
        matches = json.load(f)

    counts = {
        "matches": len(matches),
        "events_downloaded": 0,
        "events_skipped": 0,
        "lineups_downloaded": 0,
        "lineups_skipped": 0,
    }

    for match in matches:
        match_id = match["match_id"]

        event_remote = f"events/{match_id}.json"
        event_local = root / "events" / f"{match_id}.json"
        if _fetch(event_remote, event_local):
            counts["events_downloaded"] += 1
            logger.debug("Downloaded events/%s.json", match_id)
        else:
            counts["events_skipped"] += 1

        lineup_remote = f"lineups/{match_id}.json"
        lineup_local = root / "lineups" / f"{match_id}.json"
        if _fetch(lineup_remote, lineup_local):
            counts["lineups_downloaded"] += 1
            logger.debug("Downloaded lineups/%s.json", match_id)
        else:
            counts["lineups_skipped"] += 1

    logger.info(
        "competition=%d season=%d  matches=%d  "
        "events: %d new / %d cached  lineups: %d new / %d cached",
        competition_id,
        season_id,
        counts["matches"],
        counts["events_downloaded"],
        counts["events_skipped"],
        counts["lineups_downloaded"],
        counts["lineups_skipped"],
    )
    return counts
