"""Shared M4g spike helpers: per-position minutes, fine roles, registry display names.

Never imports eleveniq (tests/test_spikes_independent.py).

- position_minutes: minutes per lineup position on the ADR 002 §31 clock, from the
  verify_core_metrics spike's second implementation of §31 run with each position as its own
  "group". Callers reconcile the sums with the season parquet.
- fine roles (user, 2026-10-07; docs/reviews/m4g-fine-roles.md): the role holding ≥ 0.7 of a
  row's minutes, else "mixed" with its two largest roles as the combo.
- registry_labels: the metric display names, read from src/eleveniq/analytics/registry.py as
  source text (ast), not imported. The registry stays the single label source; the parser
  accepts only plain strings and f-strings over module-level string constants and fails on
  anything else, so a change in how SPECS is written cannot pass silently.
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_core_metrics as vcm  # noqa: E402  (sibling spike: the §31 minutes clock)
from reference_totals import DATA_ROOT, PROJECT_ROOT, load_matches  # noqa: E402

REGISTRY_SOURCE = PROJECT_ROOT / "src" / "eleveniq" / "analytics" / "registry.py"
POOL = ((2, 27), (7, 27), (11, 27), (12, 27))   # the four pool leagues (ADR 003)
LABEL_SHARE = 0.7

ROLES = ("CB", "LB", "RB", "LWB", "RWB", "DM", "CM", "AM", "LW", "RW", "ST")
ROLE_OF = {
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
# Plain words for each role (page text).
ROLE_WORDS = {
    "CB": "Centre-back", "LB": "Left-back", "RB": "Right-back", "LWB": "Left wing-back",
    "RWB": "Right wing-back", "DM": "Deep midfielder", "CM": "Central midfielder",
    "AM": "Attacking midfielder", "LW": "Left winger or left midfielder",
    "RW": "Right winger or right midfielder", "ST": "Striker",
}


class SpikeError(SystemExit):
    pass


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SpikeError(1)


def position_minutes(keys: set[tuple], comps: tuple[tuple[int, int], ...] = POOL
                     ) -> dict[tuple, Counter]:
    """(player_id, team_id, competition_id) -> lineup position -> minutes, for the given season
    keys, every position counted, over the given (competition_id, season_id) pairs."""
    wanted = {(k[0], k[1], k[2]) for k in keys}
    # The §31 implementation reads the module-level POSITION_GROUP; mapping each position to
    # itself makes its "group minutes" per-position minutes. Swapped and restored here rather
    # than adding a parameter to verify_core_metrics, whose functions and tests are the
    # reference implementation of §31 and stay unchanged. Single-threaded spike use only.
    groups = dict(vcm.POSITION_GROUP)
    out: dict[tuple, Counter] = defaultdict(Counter)
    vcm.POSITION_GROUP = {name: name for name in groups}   # each position its own "group"
    try:
        for comp, season in comps:
            for m in load_matches(comp, season):
                mid = m["match_id"]
                with open(DATA_ROOT / "events" / f"{mid}.json") as f:
                    events = json.load(f)
                with open(DATA_ROOT / "lineups" / f"{mid}.json") as f:
                    lineups = json.load(f)
                teams, _ = vcm.match_minutes(mid, events, lineups, Counter())
                for team in teams:
                    for p in team["players"]:
                        k = (p["player_id"], team["team_id"], comp)
                        if k in wanted:
                            for a, b, pos in p["segments"]:
                                out[k][pos] += b - a
    finally:
        vcm.POSITION_GROUP = groups
    return dict(out)


def group_of(position: str) -> str:
    return vcm.POSITION_GROUP[position]


def fine_role(pos: Counter, played: float, key: tuple) -> dict:
    """The fine role of one row from its outfield position minutes (GK minutes dropped; the
    rest must sum to `played` unless the row has GK minutes)."""
    gk = "Goalkeeper" in pos
    pos = Counter({p: v for p, v in pos.items() if p != "Goalkeeper"})
    unknown = set(pos) - set(ROLE_OF)
    if unknown:
        fail(f"{key}: unmapped positions {sorted(unknown)}")
    total = sum(pos.values())
    if total <= 0:
        fail(f"{key}: no outfield position minutes")
    if not gk and abs(total - played) > 1e-6:
        fail(f"{key}: position minutes {total:.6f} != minutes_played {played:.6f}")
    role_min: Counter = Counter()
    for p, v in pos.items():
        role_min[ROLE_OF[p]] += v
    share = {r: v / total for r, v in role_min.items()}
    top = sorted(share.items(), key=lambda kv: (-kv[1], ROLES.index(kv[0])))
    label = top[0][0] if top[0][1] >= LABEL_SHARE else "mixed"
    combo = "/".join(sorted((t[0] for t in top[:2]), key=ROLES.index)) if label == "mixed" else None
    return {"share": share, "label": label, "combo": combo, "top": top,
            "top2_share": sum(t[1] for t in top[:2]),
            "diff": 0.0 if gk else abs(total - played),     # GK rows are not reconciled
            "pos_min": pos}


def role_words(role: dict) -> str:
    """'Deep midfielder (DM)', or 'Mixed: Deep midfielder / Central midfielder (DM / CM)'."""
    if role["label"] != "mixed":
        return f"{ROLE_WORDS[role['label']]} ({role['label']})"
    a, b = role["combo"].split("/")
    return f"Mixed: {ROLE_WORDS[a]} / {ROLE_WORDS[b]} ({a} / {b})"


def registry_labels(path: Path = REGISTRY_SOURCE) -> dict[str, str]:
    """Metric column -> display name, parsed from the registry's SPECS without importing it."""
    tree = ast.parse(path.read_text())
    env = {n.targets[0].id: n.value.value for n in tree.body
           if isinstance(n, ast.Assign) and len(n.targets) == 1
           and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)
           and isinstance(n.value.value, str)}

    def text(node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(v.value)
                elif (isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
                      and v.value.id in env and v.format_spec is None and v.conversion == -1):
                    parts.append(env[v.value.id])
                else:
                    fail(f"registry SPECS: unsupported f-string part {ast.dump(v)}")
            return "".join(parts)
        fail(f"registry SPECS: unsupported label expression {ast.dump(node)}")
        raise AssertionError

    specs = [n for n in tree.body if isinstance(n, ast.AnnAssign)
             and isinstance(n.target, ast.Name) and n.target.id == "SPECS"]
    if len(specs) != 1 or not isinstance(specs[0].value, ast.Tuple):
        fail("registry: SPECS is not one tuple literal")
    out: dict[str, str] = {}
    for call in specs[0].value.elts:
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            fail(f"registry SPECS: unexpected element {ast.dump(call)[:80]}")
        kw = {k.arg: k.value for k in call.keywords}
        f = call.func.id
        if f == "_p90":
            col, name = text(call.args[0]) + "_p90", text(call.args[1])
        elif f == "_ratio":
            col, name = text(call.args[0]), text(call.args[1])
        elif f == "MetricSpec":
            col, name = text(kw["column"]), text(kw["display_name"])
        else:
            fail(f"registry SPECS: unknown constructor {f}")
        if col in out:
            fail(f"registry SPECS: {col} listed twice")
        out[col] = name
    return out
