"""
Fine roles and the DM-CM slot labels (plan 006 W1; moved from spikes/m4g_common.py and
spikes/m4g_style_map.py, plan 005 M4g1; user 2026-10-07, ADR 004 §13.4). Display only: no score,
rank or population reads them.

Input: one season row's minutes per StatsBomb position (player_season_positions.parquet, plan
006 gate G3; the §31 timeline, reconciled with the group minutes by the build).

- fine role: the role (ROLE_OF) holding ≥ config.ROLE_SHARE of the row's outfield minutes, else
  "mixed" with its two largest roles (ties in the fixed ROLES order) as the combo. Goalkeeper
  minutes are dropped.
- DM-CM slot (DM-CM rows): share6 = 6-slot minutes / DM-CM minutes; ≥ ROLE_SHARE → "6",
  ≤ 1 − ROLE_SHARE → "8", else "mixed". Pivot: single (Center Defensive Midfield), double (Left /
  Right Defensive Midfield) or cm (Left / Right / Center Center Midfield) by ≥ ROLE_SHARE, else
  "mixed".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from eleveniq.config import ROLE_SHARE
from eleveniq.metrics.positions import assign_position_group

ROLES = ("CB", "LB", "RB", "LWB", "RWB", "DM", "CM", "AM", "LW", "RW", "ST")
ROLE_OF: dict[str, str] = {
    "Center Back": "CB", "Left Center Back": "CB", "Right Center Back": "CB",
    "Left Back": "LB", "Right Back": "RB", "Left Wing Back": "LWB", "Right Wing Back": "RWB",
    "Center Defensive Midfield": "DM", "Left Defensive Midfield": "DM",
    "Right Defensive Midfield": "DM", "Defensive Midfield": "DM",
    "Center Midfield": "CM", "Left Center Midfield": "CM", "Right Center Midfield": "CM",
    "Center Attacking Midfield": "AM", "Attacking Midfield": "AM",
    "Left Attacking Midfield": "AM", "Right Attacking Midfield": "AM",
    "Left Wing": "LW", "Left Midfield": "LW", "Right Wing": "RW", "Right Midfield": "RW",
    "Center Forward": "ST", "Left Center Forward": "ST", "Right Center Forward": "ST",
    "Secondary Striker": "ST",
}
GOALKEEPER = "Goalkeeper"
MIXED = "mixed"
SIX = frozenset({"Center Defensive Midfield", "Left Defensive Midfield", "Right Defensive Midfield"})
EIGHT = frozenset({"Center Midfield", "Left Center Midfield", "Right Center Midfield"})
PIVOT: dict[str, frozenset[str]] = {
    "single": frozenset({"Center Defensive Midfield"}),
    "double": frozenset({"Left Defensive Midfield", "Right Defensive Midfield"}),
    "cm": EIGHT,
}


@dataclass(frozen=True)
class FineRole:
    label: str                               # a role, or "mixed"
    combo: tuple[str, str] | None            # the two largest roles when mixed
    top: tuple[tuple[str, float], ...]       # (role, share), largest first, ties in ROLES order


@dataclass(frozen=True)
class DmcmSlot:
    share6: float
    label: str                               # "6", "8" or "mixed"
    pivot: str                               # "single", "double", "cm" or "mixed"


def fine_role(minutes: Mapping[str, float]) -> FineRole:
    """The fine role of one row from its minutes per StatsBomb position. Raises ValueError on
    an unmapped position or a row with no outfield minutes."""
    outfield = {p: v for p, v in minutes.items() if p != GOALKEEPER}
    unknown = set(outfield) - set(ROLE_OF)
    if unknown:
        raise ValueError(f"unmapped positions {sorted(unknown)}")
    total = sum(outfield.values())
    if total <= 0:
        raise ValueError("no outfield position minutes")
    role_min: dict[str, float] = {}
    for p, v in outfield.items():
        role_min[ROLE_OF[p]] = role_min.get(ROLE_OF[p], 0.0) + v
    top = tuple(sorted(((r, v / total) for r, v in role_min.items()),
                       key=lambda t: (-t[1], ROLES.index(t[0]))))
    if top[0][1] >= ROLE_SHARE:
        return FineRole(top[0][0], None, top)
    pair = sorted((t[0] for t in top[:2]), key=ROLES.index)
    combo = (pair[0], pair[1]) if len(pair) == 2 else None
    return FineRole(MIXED, combo, top)


def dmcm_slot(minutes: Mapping[str, float]) -> DmcmSlot:
    """The 6 / 8 label and pivot of a DM-CM row from its minutes per StatsBomb position.
    Raises ValueError on a DM-CM position outside SIX and EIGHT, or no DM-CM minutes."""
    pos = {p: v for p, v in minutes.items() if assign_position_group(p) == "DM-CM"}
    unknown = set(pos) - SIX - EIGHT
    if unknown:
        raise ValueError(f"DM-CM position(s) outside the 6 / 8 definitions: {sorted(unknown)}")
    total = sum(pos.values())
    if total <= 0:
        raise ValueError("no DM-CM minutes")
    share6 = sum(v for p, v in pos.items() if p in SIX) / total
    label = "6" if share6 >= ROLE_SHARE else "8" if share6 <= 1 - ROLE_SHARE else MIXED
    pivot = next((name for name, s in PIVOT.items()
                  if sum(v for p, v in pos.items() if p in s) / total >= ROLE_SHARE), MIXED)
    return DmcmSlot(share6, label, pivot)
