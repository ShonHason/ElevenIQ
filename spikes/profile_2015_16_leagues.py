"""Spike: raw-data profile of the five 2015/16 StatsBomb Open Data leagues.

Source of the figures in ADR 003 and the Plan 003 C1 audit (match/team counts,
xG nulls, key-pass resolution, unmapped positions, starter counts, goals
reconciliation against matches.json scores, event-type rates).

Reads raw JSON only; imports only POSITION_GROUP_MAP (never eleveniq.pipeline),
so its numbers are independent of the pipeline.

Usage: .venv/bin/python spikes/profile_2015_16_leagues.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from eleveniq.metrics.positions import POSITION_GROUP_MAP

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "statsbomb"
LEAGUES = {"La Liga": 11, "Premier League": 2, "Serie A": 12, "Ligue 1": 7, "Bundesliga": 9}
SEASON = 27


def load(p: Path):
    with p.open() as f:
        return json.load(f)


results: dict[str, dict] = {}
event_types_by_league: dict[str, Counter] = {}
detail: dict[str, dict] = {}

for league, comp in LEAGUES.items():
    matches = load(RAW / "matches" / str(comp) / f"{SEASON}.json")
    r: dict = {}
    d: dict = defaultdict(list)
    teams = set()
    for m in matches:
        teams.add(m["home_team"]["home_team_name"])
        teams.add(m["away_team"]["away_team_name"])
    r["matches"] = len(matches)
    r["distinct_match_ids"] = len({m["match_id"] for m in matches})
    r["teams"] = len(teams)
    r["match_status"] = dict(Counter(m.get("match_status") for m in matches))
    r["data_version"] = dict(Counter(m.get("metadata", {}).get("data_version") for m in matches))
    r["shot_fidelity"] = dict(Counter(m.get("metadata", {}).get("shot_fidelity_version") for m in matches))
    r["xy_fidelity"] = dict(Counter(m.get("metadata", {}).get("xy_fidelity_version") for m in matches))

    shots = null_xg = goal_assist = shot_assist = 0
    null_xg_by_type: Counter = Counter()
    kp_with = kp_not_found = kp_not_pass = kp_not_related = 0
    unmapped_lineup: Counter = Counter()
    unmapped_events: Counter = Counter()
    unmapped_tactics: Counter = Counter()
    lineup_bad: list = []
    xi_event_bad: list = []
    missing_events_files: list = []
    missing_lineup_files: list = []
    total_score = total_event_goals = 0
    goal_mismatch: list = []
    etypes: Counter = Counter()
    shots_no_loc = events_no_loc_non_meta = 0
    periods: Counter = Counter()
    period_matches: dict = defaultdict(set)
    shot_types: Counter = Counter()
    ga_not_to_goal = 0
    n_events = 0
    missing_xi_event = []
    pens_shootout_shots = 0

    for m in matches:
        mid = m["match_id"]
        score = (m["home_score"] or 0) + (m["away_score"] or 0)
        total_score += score
        ep = RAW / "events" / f"{mid}.json"
        lp = RAW / "lineups" / f"{mid}.json"
        if not ep.exists():
            missing_events_files.append(mid)
            continue
        events = load(ep)
        n_events += len(events)
        by_id = {e["id"]: e for e in events}
        match_goals = 0
        xi_counts = {}
        for e in events:
            t = e["type"]["name"]
            etypes[t] += 1
            periods[e.get("period")] += 1
            period_matches[e.get("period")].add(mid)
            pos = e.get("position", {}).get("name") if e.get("position") else None
            if pos is not None and pos not in POSITION_GROUP_MAP:
                unmapped_events[pos] += 1
            if t in ("Starting XI", "Tactical Shift"):
                for pl in e.get("tactics", {}).get("lineup", []):
                    pn = pl.get("position", {}).get("name")
                    if pn not in POSITION_GROUP_MAP:
                        unmapped_tactics[pn] += 1
                if t == "Starting XI":
                    xi_counts[e["team"]["name"]] = len(e.get("tactics", {}).get("lineup", []))
            if t == "Pass":
                p = e.get("pass", {})
                if p.get("goal_assist") is True:
                    goal_assist += 1
                if p.get("shot_assist") is True:
                    shot_assist += 1
            if t == "Shot":
                s = e.get("shot", {})
                shot_types[s.get("type", {}).get("name")] += 1
                if e.get("period") == 5:
                    pens_shootout_shots += 1
                shots += 1
                if s.get("statsbomb_xg") is None:
                    null_xg += 1
                    null_xg_by_type[s.get("type", {}).get("name")] += 1
                if not e.get("location"):
                    shots_no_loc += 1
                if s.get("outcome", {}).get("name") == "Goal" and e.get("period") != 5:
                    match_goals += 1
                kp = s.get("key_pass_id")
                if kp:
                    kp_with += 1
                    tgt = by_id.get(kp)
                    if tgt is None:
                        kp_not_found += 1
                        d["kp_not_found"].append((mid, e["id"]))
                    elif tgt["type"]["name"] != "Pass":
                        kp_not_pass += 1
                        d["kp_not_pass"].append((mid, tgt["type"]["name"]))
                    if kp not in (e.get("related_events") or []):
                        kp_not_related += 1
                        d["kp_not_related"].append((mid, e["id"]))
            if t == "Own Goal Against":
                match_goals += 1
        total_event_goals += match_goals
        if match_goals != score:
            goal_mismatch.append((mid, score, match_goals))
        if len(xi_counts) != 2:
            missing_xi_event.append((mid, xi_counts))
        for team, n in xi_counts.items():
            if n != 11:
                xi_event_bad.append((mid, team, n))

        if not lp.exists():
            missing_lineup_files.append(mid)
            continue
        lineups = load(lp)
        for team in lineups:
            starters = 0
            for pl in team["lineup"]:
                started = False
                for ps in pl.get("positions", []):
                    pn = ps.get("position")
                    if pn not in POSITION_GROUP_MAP:
                        unmapped_lineup[pn] += 1
                    if ps.get("start_reason") == "Starting XI":
                        started = True
                starters += started
            if starters != 11:
                lineup_bad.append((mid, team["team_name"], starters))
        if len(lineups) != 2:
            lineup_bad.append((mid, "N_TEAMS", len(lineups)))

    r.update(
        events=n_events,
        missing_events_files=missing_events_files,
        missing_lineup_files=missing_lineup_files,
        shots=shots,
        null_xg=null_xg,
        null_xg_by_type=dict(null_xg_by_type),
        shot_types=dict(shot_types),
        shots_period5=pens_shootout_shots,
        shots_no_loc=shots_no_loc,
        goal_assist=goal_assist,
        shot_assist=shot_assist,
        shots_with_key_pass=kp_with,
        kp_not_found=kp_not_found,
        kp_not_pass=kp_not_pass,
        kp_not_related=kp_not_related,
        unmapped_lineup=dict(unmapped_lineup),
        unmapped_events=dict(unmapped_events),
        unmapped_tactics=dict(unmapped_tactics),
        lineup_bad=lineup_bad,
        xi_event_bad=xi_event_bad,
        missing_xi_event=missing_xi_event,
        score_goals=total_score,
        event_goals=total_event_goals,
        goal_mismatch=goal_mismatch,
        periods=dict(periods),
        period_match_counts={k: len(v) for k, v in period_matches.items()},
    )
    results[league] = r
    event_types_by_league[league] = etypes
    detail[league] = {k: v[:10] for k, v in d.items()}

for league, r in results.items():
    print(f"\n===== {league} =====")
    for k, v in r.items():
        print(f"{k}: {v}")
    print("detail samples:", detail[league])

base = set(event_types_by_league["La Liga"])
print("\n===== event types vs La Liga =====")
for league, et in event_types_by_league.items():
    extra = set(et) - base
    missing = base - set(et)
    print(league, "extra:", {k: et[k] for k in extra}, "missing:", sorted(missing))
print("\n===== event type counts per 1000 events (types with notable spread) =====")
all_types = sorted(set().union(*event_types_by_league.values()))
for t in all_types:
    row = []
    for league, et in event_types_by_league.items():
        n = results[league]["events"]
        row.append(f"{et[t]:>7} ({1000*et[t]/n:6.2f})" if n else "-")
    print(f"{t:<22}", " ".join(row))
