"""
M4g4a opponent exposure: an independent recount before any pipeline change (plan 005 M4g4,
ADR 004 §13.8).

Exposure E = the open-play Pass events of the *opponent* whose instant lies in the player's
credited time: the ADR 004 D12 crediting rule (team_open_play_passes_while_on) mirrored onto the
opponent. It re-uses the independent §31 timelines of spikes/verify_core_metrics.py (no eleveniq
import), and adds:

1. Own-team recount per player-match: each player's D12 credits (team.id passes, open play =
   pass.type not a restart, periods 1-4; a teammate is credited when the pass's elapsed instant
   lies in one of his timeline segments [a, b) inside the pass's own period, or is that period's
   end and a segment ends there, within TIME_EPS; the passer always) must equal the pipeline's
   team_open_play_passes_while_on exactly, row by row. This proves the per-player machinery that
   the opponent column mirrors (verify_core_metrics.py checks league totals only).
2. Opponent credits: the same window rule applied to the players of the other lineup team (no
   passer rule: the passer is not on the defending team). Each opponent open-play pass must be
   credited to 7-11 defenders; the distribution is reported and any pass outside 7-11 fails.
   A pass after its period's whistle is clamped to the whistle (Clock.elapsed).
3. Proxy agreement (re-check of the 0.999 the stats-reviewer measured in M4e): proxy = the
   team's (own) or opponent's match open-play passes × minutes_played / match_duration;
   ratio exact / proxy per player-match (minutes > 0) and per season key; median and spread.

4. M4g4b checkpoint 1 (plan 005 M4g4b): the 8 team-action counts (team_<action>_while_on) under the
   same window rule and actor rule (the actor always credited), with the action rules written out
   here (the pipeline's player counts, ACTIONS below): per action the distribution of credited
   players, and separately the events whose actor is outside his own credited window (credited
   anyway), with their teammates-on count, so a credit count of 12 is visible. The same actor
   report for the open-play passes (the existing D12 crediting).

Writes data/interim/m4g4_opponent_exposure.parquet (one row per player-match with minutes > 0:
own and opponent credits) and data/interim/m4g4_exposure_summary.json. Report only.

  .venv/bin/python spikes/m4g_opponent_exposure.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reference_totals import DATA_ROOT, PROJECT_ROOT, load_matches  # noqa: E402
from verify_core_metrics import (  # noqa: E402  (independent §31 timelines; spike to spike)
    MAX_PLAYERS_PER_PASS, MIN_PLAYERS_PER_PASS, RESTART_TYPES, TIME_EPS, Clock, _at,
    match_minutes,
)

MATCH_PARQUET = PROJECT_ROOT / "data" / "interim" / "player_match_stats.parquet"
SEASON_PARQUET = PROJECT_ROOT / "data" / "processed" / "player_season_stats.parquet"
OUT = PROJECT_ROOT / "data" / "interim" / "m4g4_opponent_exposure.parquet"
SUMMARY = PROJECT_ROOT / "data" / "interim" / "m4g4_exposure_summary.json"
POOL = (2, 7, 11, 12)
GATE, GATE_TOL = 900.0, 1e-6


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def holds(segs: list[tuple[float, float, str]], at: float, start: float, end: float) -> bool:
    """The D12 window rule (as verify_core_metrics.team_open_play_pass_credits): the instant lies
    in a segment [a, b) inside the pass's own period, or is the period's end and a segment ends
    there; zero-length segments credit nothing; comparisons within TIME_EPS."""
    return any(b - a > TIME_EPS
               and ((a - TIME_EPS <= at < b - TIME_EPS and start - TIME_EPS <= at < end - TIME_EPS)
                    or (abs(at - end) <= TIME_EPS and abs(b - end) <= TIME_EPS))
               for a, b, _ in segs)


# The pipeline's player counts for the 8 Defending members with a team-share candidate (plan
# 005 M4g4b), written out (no eleveniq import): event type plus the sub-payload condition.
def _block(e: dict) -> bool:
    b = e.get("block") or {}
    return not b.get("offensive") and not b.get("save_block")


ACTIONS = {
    "ball_recoveries": lambda e: e["type"]["name"] == "Ball Recovery"
    and not (e.get("ball_recovery") or {}).get("recovery_failure"),
    "tackles_attempted": lambda e: e["type"]["name"] == "Duel"
    and ((e.get("duel") or {}).get("type") or {}).get("name") == "Tackle",
    "pressures": lambda e: e["type"]["name"] == "Pressure",
    "clearances": lambda e: e["type"]["name"] == "Clearance",
    "dribbled_past": lambda e: e["type"]["name"] == "Dribbled Past",
    "fouls_committed": lambda e: e["type"]["name"] == "Foul Committed",
    "blocks": lambda e: e["type"]["name"] == "Block" and _block(e),
    "interceptions": lambda e: e["type"]["name"] == "Interception",
}


def action_of(e: dict) -> str | None:
    """The Defending action an event counts as (periods 1-4, with a player), or None."""
    if e["period"] > 4 or not e.get("player"):
        return None
    hits = [a for a, rule in ACTIONS.items() if rule(e)]
    if len(hits) > 1:
        fail(f"event {e['id']} counts as {hits}")
    return hits[0] if hits else None


def credit_team(kind: str, e: dict, team_segs: dict, actor: int, at: float, start: float,
                end: float, credits: Counter, dist: Counter, actor_out: list[dict],
                mid: int) -> None:
    """The D12 team rule for one event: the actor always, plus each teammate whose window holds.
    dist[kind][(credited, on_pitch)] counts events by total credited and by players on the pitch
    (teammates holding + the actor iff his own window holds). An actor outside his window is
    recorded in actor_out with the teammates-on count."""
    mates = [pid for pid, segs in team_segs.items() if pid != actor and holds(segs, at, start, end)]
    actor_on = actor in team_segs and holds(team_segs[actor], at, start, end)
    credits[actor] += 1
    for pid in mates:
        credits[pid] += 1
    dist[(kind, 1 + len(mates), len(mates) + actor_on)] += 1
    if not actor_on:
        actor_out.append({"kind": kind, "match_id": mid, "event_id": e["id"], "period": e["period"],
                          "minute": e["minute"], "second": e["second"], "actor": actor,
                          "actor_has_row": actor in team_segs, "teammates_on": len(mates),
                          "credited": 1 + len(mates)})


def team_action_credits(mid: int, events: list[dict], teams: list[dict], clock: Clock,
                        dist: Counter, actor_out: list[dict]) -> tuple[dict, dict]:
    """(team_<action>_while_on credits per player, the player's own action counts), per action."""
    segments = {t["team_id"]: {p["player_id"]: p["segments"] for p in t["players"]} for t in teams}
    team_credits = {a: Counter() for a in ACTIONS}
    own_counts = {a: Counter() for a in ACTIONS}
    for e in events:
        a = action_of(e)
        if a is None:
            continue
        team = e["team"]["id"]
        if team not in segments:
            fail(f"match {mid}: action {e['id']} by team {team}, not a lineup team")
        p = e["period"]
        start = clock.offset[p]
        actor = e["player"]["id"]
        own_counts[a][actor] += 1
        credit_team(a, e, segments[team], actor, clock.elapsed(_at(e)), start,
                    start + clock.length(p), team_credits[a], dist, actor_out, mid)
    return team_credits, own_counts


def is_open_play_pass(e: dict) -> bool:
    return (e["period"] <= 4 and e["type"]["name"] == "Pass" and bool(e.get("player"))
            and ((e.get("pass") or {}).get("type") or {}).get("name") not in RESTART_TYPES)


def match_credits(mid: int, events: list[dict], teams: list[dict], clock: Clock,
                  defenders_per_pass: Counter, dist: Counter | None = None,
                  actor_out: list[dict] | None = None) -> tuple[Counter, Counter, Counter, list[dict]]:
    """(own credits per player, opponent credits per player, open-play passes per team,
    passes credited to a defender count outside 7-11). dist / actor_out: the checkpoint-1
    report for the passing team (credit_team's), when given."""
    if len(teams) != 2:
        fail(f"match {mid}: {len(teams)} lineup teams")
    segments = {t["team_id"]: {p["player_id"]: p["segments"] for p in t["players"]} for t in teams}
    other = {teams[0]["team_id"]: teams[1]["team_id"], teams[1]["team_id"]: teams[0]["team_id"]}
    own, opp, passes = Counter(), Counter(), Counter()
    bad: list[dict] = []
    for e in events:
        if not is_open_play_pass(e):
            continue
        p = e["period"]
        start = clock.offset[p]
        end = start + clock.length(p)
        at = clock.elapsed(_at(e))
        team = e["team"]["id"]
        if team not in other:
            fail(f"match {mid}: pass {e['id']} by team {team}, not a lineup team")
        passes[team] += 1
        passer = e["player"]["id"]
        credit_team("open_play_pass", e, segments[team], passer, at, start, end, own,
                    Counter() if dist is None else dist,
                    [] if actor_out is None else actor_out, mid)
        n = 0
        for pid, segs in segments[other[team]].items():
            if holds(segs, at, start, end):
                opp[pid] += 1
                n += 1
        defenders_per_pass[n] += 1
        if not MIN_PLAYERS_PER_PASS <= n <= MAX_PLAYERS_PER_PASS:
            bad.append({"match_id": mid, "event_id": e["id"], "period": p, "minute": e["minute"],
                        "second": e["second"], "defenders": n})
    return own, opp, passes, bad


def ratio_stats(r: np.ndarray) -> dict:
    q = np.quantile(r, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    return {"n": int(len(r)), "median": float(q[3]), "iqr": [float(q[2]), float(q[4])],
            "p05_p95": [float(q[1]), float(q[5])], "p01_p99": [float(q[0]), float(q[6])],
            "min": float(r.min()), "max": float(r.max())}


def main() -> None:
    new_cols = ["opponent_open_play_passes_while_on", *(f"team_{a}_while_on" for a in ACTIONS)]
    have = set(pl.read_parquet_schema(MATCH_PARQUET))
    pipeline_has_new = all(c in have for c in new_cols)          # after the M4g4b rebuild
    pipe = pl.read_parquet(MATCH_PARQUET, columns=[
        "match_id", "player_id", "team_id", "competition_id", "season_id", "minutes_played",
        "match_duration", "team_open_play_passes_while_on", *ACTIONS,
        *(new_cols if pipeline_has_new else [])])
    comp_seasons = sorted(pipe.select("competition_id", "season_id").unique().iter_rows())
    rows: list[dict] = []
    team_passes: dict[tuple[int, int], int] = {}
    defenders_per_pass: Counter = Counter()
    credit_dist: Counter = Counter()          # (kind, credited, on_pitch) -> events
    actor_out: list[dict] = []
    actors_without_row: list[dict] = []
    bad: list[dict] = []
    for comp, season in comp_seasons:
        matches = load_matches(comp, season)
        for m in matches:
            mid = m["match_id"]
            events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
            lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
            teams, clock = match_minutes(mid, events, lineups, Counter())
            own, opp, passes, b = match_credits(mid, events, teams, clock, defenders_per_pass,
                                                credit_dist, actor_out)
            tcred, town = team_action_credits(mid, events, teams, clock, credit_dist, actor_out)
            bad += b
            for t in teams:
                team_passes[(mid, t["team_id"])] = passes[t["team_id"]]
                for pl_ in t["players"]:
                    pid = pl_["player_id"]
                    rows.append({"match_id": mid, "player_id": pid, "team_id": t["team_id"],
                                 "competition_id": comp, "season_id": season,
                                 "own_credits": own[pid], "opp_credits": opp[pid],
                                 **{f"team_{a}_while_on": tcred[a][pid] for a in ACTIONS},
                                 **{f"own_{a}": town[a][pid] for a in ACTIONS}})

            row_players = {p_["player_id"] for t in teams for p_ in t["players"]}
            actors_without_row.extend({"match_id": mid, "player_id": pid, "action": a,
                                       "count": town[a][pid]}
                                      for a in ACTIONS for pid in sorted(set(town[a]) - row_players))
            # a passer with no credited segment (events outside credited time) still gets own
            # credits; he must be a row player (minutes > 0) or the comparison below fails
        print(f"competition {comp} season {season}: {len(matches)} matches", flush=True)

    rec = pl.DataFrame(rows)
    key = ["match_id", "player_id", "team_id"]
    p = pipe.filter(pl.col("minutes_played") > 0)
    j = p.join(rec, on=key, how="full", coalesce=True)
    missing_rec = j.filter(pl.col("own_credits").is_null())
    missing_pipe = j.filter(pl.col("minutes_played").is_null())
    own_diff = j.filter(pl.col("own_credits").is_not_null() & pl.col("minutes_played").is_not_null()
                        & (pl.col("own_credits") != pl.col("team_open_play_passes_while_on")))
    # zero-minute pipeline rows must have no credits in the pipeline either
    zero = pipe.filter((pl.col("minutes_played") <= 0) & (pl.col("team_open_play_passes_while_on") != 0))

    # proxy (M4e re-check): match passes × minutes share
    tp = pl.DataFrame([{"match_id": k[0], "team_id": k[1], "team_passes": v}
                       for k, v in team_passes.items()])
    opp_tp = (tp.join(tp, on="match_id", suffix="_o").filter(pl.col("team_id") != pl.col("team_id_o"))
              .select("match_id", "team_id", pl.col("team_passes_o").alias("opp_passes")))
    j2 = (j.filter(pl.col("own_credits").is_not_null() & pl.col("minutes_played").is_not_null())
          .join(tp, on=["match_id", "team_id"]).join(opp_tp, on=["match_id", "team_id"])
          .with_columns((pl.col("minutes_played") / pl.col("match_duration")).alias("share")))
    j2 = j2.with_columns((pl.col("team_passes") * pl.col("share")).alias("own_proxy"),
                         (pl.col("opp_passes") * pl.col("share")).alias("opp_proxy"))
    pm_own = j2.filter(pl.col("own_proxy") > 0)
    pm_opp = j2.filter(pl.col("opp_proxy") > 0)
    seasonal = (j2.group_by("player_id", "team_id", "competition_id", "season_id")
                .agg(pl.col("minutes_played").sum(), pl.col("own_credits").sum(),
                     pl.col("opp_credits").sum(), pl.col("own_proxy").sum(), pl.col("opp_proxy").sum()))
    nominal = pl.read_parquet(SEASON_PARQUET, columns=[
        "player_id", "team_id", "competition_id", "season_id", "minutes_nominal_equiv",
        "position_group", "in_percentile_pool", "is_outfield"])
    seasonal = seasonal.join(nominal, on=["player_id", "team_id", "competition_id", "season_id"],
                             how="left")
    gated = seasonal.filter(pl.col("in_percentile_pool") & pl.col("is_outfield")
                            & (pl.col("minutes_nominal_equiv") >= GATE - GATE_TOL))
    gated = gated.with_columns((pl.col("opp_credits") / pl.col("minutes_played") * 90).alias("E"))

    # team-level E per match (the M4e findings: 299 Barcelona ... 534-543, median 440)
    per_team = (tp.join(opp_tp, on=["match_id", "team_id"])
                .join(rec.select("match_id", "team_id", "competition_id").unique(),
                      on=["match_id", "team_id"])
                .filter(pl.col("competition_id").is_in(POOL))
                .group_by("team_id").agg(pl.col("opp_passes").mean().alias("opp_per_match"))
                .sort("opp_per_match"))

    # checkpoint 1 (M4g4b): the written-out action rules must reproduce the pipeline's player
    # counts row by row; the credit distributions and the actor-outside report
    jj = j.filter(pl.col("own_credits").is_not_null() & pl.col("minutes_played").is_not_null())
    rule_diff = {a: jj.filter(pl.col(f"own_{a}") != pl.col(a)).height for a in ACTIONS}
    rule_diff = {a: n for a, n in rule_diff.items() if n}
    team_below_own = {a: jj.filter(pl.col(f"team_{a}_while_on") < pl.col(f"own_{a}")).height
                      for a in ACTIONS}
    kinds = ["open_play_pass", *ACTIONS]
    checkpoint1 = {}
    for k in kinds:
        cells = {(c, o): n for (kk, c, o), n in credit_dist.items() if kk == k}
        outs = [x for x in actor_out if x["kind"] == k]
        checkpoint1[k] = {
            "events": sum(cells.values()),
            "credited": {str(c): sum(n for (cc, _), n in cells.items() if cc == c)
                         for c in sorted({c for c, _ in cells})},
            "on_pitch": {str(o): sum(n for (_, oo), n in cells.items() if oo == o)
                         for o in sorted({o for _, o in cells})},
            "actor_outside_window": len(outs),
            "actor_outside_credited": dict(sorted(Counter(str(x["credited"]) for x in outs).items())),
            "actor_outside_without_row": sum(not x["actor_has_row"] for x in outs),
            "credited_over_11": sum(n for (c, _), n in cells.items() if c > 11),
            "on_pitch_outside_7_11": sum(n for (_, o), n in cells.items() if not 7 <= o <= 11),
            "team_below_own_rows": team_below_own.get(k, 0),
        }
    checkpoint1["actor_outside_examples"] = sorted(actor_out, key=lambda x: -x["credited"])[:25]
    checkpoint1["actors_without_row"] = actors_without_row[:25]
    checkpoint1["actors_without_row_n"] = len(actors_without_row)
    rec_out = rec
    # M4g4b checkpoint 2: the pipeline's new columns against this recount, row by row
    pipeline_check = None
    if pipeline_has_new:
        pairs = [("opponent_open_play_passes_while_on", "opp_credits")]
        pairs += [(f"team_{a}_while_on", f"team_{a}_while_on_rec") for a in ACTIONS]
        jr = p.join(rec.rename({f"team_{a}_while_on": f"team_{a}_while_on_rec" for a in ACTIONS}),
                    on=key, how="inner")
        pipeline_check = {
            "rows_compared": jr.height,
            "diffs": {pc: jr.filter(pl.col(pc) != pl.col(rc)).height for pc, rc in pairs},
            "zero_minute_rows_nonzero": {
                c: pipe.filter((pl.col("minutes_played") <= 0) & (pl.col(c) != 0)).height
                for c in new_cols},
            "league_totals": {c: int(pipe[c].sum()) for c in new_cols},
        }

    summary = {
        "generated_by": "spikes/m4g_opponent_exposure.py",
        "competition_seasons": [list(cs) for cs in comp_seasons],
        "player_match_rows": rec.height,
        "own_recount": {"rows_compared": j2.height, "mismatches": own_diff.height,
                        "rows_missing_in_recount": missing_rec.height,
                        "rows_missing_in_pipeline": missing_pipe.height,
                        "zero_minute_rows_with_credits": zero.height},
        "defenders_per_pass": {str(k): v for k, v in sorted(defenders_per_pass.items())},
        "passes_outside_7_11": len(bad),
        "proxy_ratio_exact_over_proxy": {
            "own_player_match": ratio_stats((pm_own["own_credits"] / pm_own["own_proxy"]).to_numpy()),
            "opp_player_match": ratio_stats((pm_opp["opp_credits"] / pm_opp["opp_proxy"]).to_numpy()),
            "own_season_key": ratio_stats((seasonal["own_credits"] / seasonal["own_proxy"]).to_numpy()),
            "opp_season_key": ratio_stats((seasonal["opp_credits"] / seasonal["opp_proxy"]).to_numpy()),
            "own_gated_pool_rows": ratio_stats((gated["own_credits"] / gated["own_proxy"]).to_numpy()),
            "opp_gated_pool_rows": ratio_stats((gated["opp_credits"] / gated["opp_proxy"]).to_numpy()),
        },
        "E_gated_pool_rows_by_group": {
            g[0]: {"n": d.height, "median": float(d["E"].median()), "min": float(d["E"].min()),
                   "max": float(d["E"].max())}
            for g, d in sorted(gated.group_by("position_group"), key=lambda x: x[0][0])},
        "team_opp_passes_per_match_pool": {
            "n_teams": per_team.height, "min": float(per_team["opp_per_match"].min()),
            "median": float(per_team["opp_per_match"].median()),
            "max": float(per_team["opp_per_match"].max())},
        "outside_7_11_examples": bad[:20],
        "checkpoint1": checkpoint1,
        "pipeline_check": pipeline_check,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec.sort(key).write_parquet(OUT)
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "outside_7_11_examples"}, indent=1))
    errors = []
    if pipeline_check and (any(pipeline_check["diffs"].values())
                           or any(pipeline_check["zero_minute_rows_nonzero"].values())
                           or pipeline_check["rows_compared"] != rec.height):
        errors.append(f"pipeline columns differ from the recount: {pipeline_check}")
    if rule_diff:
        errors.append(f"written-out action rules differ from the pipeline's counts: {rule_diff}")
    if own_diff.height or missing_rec.height or missing_pipe.height or zero.height:
        errors.append("own-team recount differs from the pipeline")
        print(own_diff.head(10), missing_rec.head(5), missing_pipe.head(5), file=sys.stderr)
    if bad:
        errors.append(f"{len(bad)} opponent passes credited outside 7-11 defenders")
    if errors:
        fail("; ".join(errors))
    print(f"OK: wrote {OUT} and {SUMMARY}")


if __name__ == "__main__":
    main()
