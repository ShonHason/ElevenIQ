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


def is_open_play_pass(e: dict) -> bool:
    return (e["period"] <= 4 and e["type"]["name"] == "Pass" and bool(e.get("player"))
            and ((e.get("pass") or {}).get("type") or {}).get("name") not in RESTART_TYPES)


def match_credits(mid: int, events: list[dict], teams: list[dict], clock: Clock,
                  defenders_per_pass: Counter) -> tuple[Counter, Counter, Counter, list[dict]]:
    """(own credits per player, opponent credits per player, open-play passes per team,
    passes credited to a defender count outside 7-11)."""
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
        own[passer] += 1                                   # the passer always (D12)
        for pid, segs in segments[team].items():
            if pid != passer and holds(segs, at, start, end):
                own[pid] += 1
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
    pipe = pl.read_parquet(MATCH_PARQUET, columns=[
        "match_id", "player_id", "team_id", "competition_id", "season_id", "minutes_played",
        "match_duration", "team_open_play_passes_while_on"])
    comp_seasons = sorted(pipe.select("competition_id", "season_id").unique().iter_rows())
    rows: list[dict] = []
    team_passes: dict[tuple[int, int], int] = {}
    defenders_per_pass: Counter = Counter()
    bad: list[dict] = []
    for comp, season in comp_seasons:
        matches = load_matches(comp, season)
        for m in matches:
            mid = m["match_id"]
            events = json.loads((DATA_ROOT / "events" / f"{mid}.json").read_text())
            lineups = json.loads((DATA_ROOT / "lineups" / f"{mid}.json").read_text())
            teams, clock = match_minutes(mid, events, lineups, Counter())
            own, opp, passes, b = match_credits(mid, events, teams, clock, defenders_per_pass)
            bad += b
            for t in teams:
                team_passes[(mid, t["team_id"])] = passes[t["team_id"]]
                for pl_ in t["players"]:
                    pid = pl_["player_id"]
                    rows.append({"match_id": mid, "player_id": pid, "team_id": t["team_id"],
                                 "competition_id": comp, "season_id": season,
                                 "own_credits": own[pid], "opp_credits": opp[pid]})
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
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec.sort(key).write_parquet(OUT)
    SUMMARY.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "outside_7_11_examples"}, indent=1))
    errors = []
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
