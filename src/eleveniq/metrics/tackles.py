"""
Tackle classification.

In StatsBomb, a tackle attempt is represented as:
    type.name == "Duel"  AND  duel.type.name == "Tackle"

Aerial duels (duel.type.name == "Aerial Lost") do not have an outcome key
and are NOT counted as tackles.

Outcome mapping (observed in StatsBomb open data, match 3788741)
----------------------------------------------------------------
Won:    "Won", "Success In Play", "Success Out"
Lost:   "Lost In Play", "Lost Out"

Unknown/unobserved outcomes are NOT silently classified as won or lost.
is_tackle_won() returns False for any outcome not in TACKLE_WON_OUTCOMES,
including None and any newly seen string. The quality report surfaces
outcome values not in KNOWN_TACKLE_OUTCOMES so we can decide how to
classify them explicitly.

Do not add new outcomes here without verifying their semantics in the
StatsBomb event data specification.
"""
from __future__ import annotations

TACKLE_WON_OUTCOMES: frozenset[str] = frozenset({
    "Won",
    "Success In Play",
    "Success Out",
})

TACKLE_LOST_OUTCOMES: frozenset[str] = frozenset({
    "Lost In Play",
    "Lost Out",
})

KNOWN_TACKLE_OUTCOMES: frozenset[str] = TACKLE_WON_OUTCOMES | TACKLE_LOST_OUTCOMES

KNOWN_DUEL_TYPES: frozenset[str] = frozenset({"Tackle", "Aerial Lost"})


def is_tackle_won(outcome_name: str | None) -> bool:
    """
    Return True only for outcomes in TACKLE_WON_OUTCOMES.

    Returns False for lost outcomes, None, and any unknown value.
    Callers are responsible for separately tracking unknown outcomes.
    """
    return outcome_name in TACKLE_WON_OUTCOMES
