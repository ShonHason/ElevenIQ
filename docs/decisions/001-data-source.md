# ADR 001 — Data Source Selection

## Status
ACCEPTED — V1 built entirely on StatsBomb Open Data (2026-09-26)

## Context
ElevenIQ needs per-player, per-season statistics covering at least 10 leagues (with meaningful representation outside the Big Five) to power its Player DNA profiles, similarity engine, and Gem Finder. The minimum acceptance criteria are: minutes played, position, team, competition, and season for every record; at least 15 per-player metrics spanning at least 5 of the 7 DNA dimensions (Passing, Progression, Creativity/Creation, Ball Carrying, Defending, Goal Threat, Ball Security); and a license that explicitly permits use in a publicly hosted portfolio project with data stored in a local PostgreSQL database. Scraping and UNCLEAR licenses are disqualifying.

---

## Candidates evaluated

### Longlist summary table

| # | Name | Dataset URL | License URL | License type | Leagues covered (brief §8) | Seasons (documented) | Historical players (documented) | Level | Verdict |
|---|------|-------------|-------------|--------------|---------------------------|----------------------|---------------------------------|-------|---------|
| 1 | StatsBomb Open Data | https://github.com/statsbomb/open-data | https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf | StatsBomb Public Data User Agreement — non-commercial; UNCLEAR for portfolio | Big Five: PL (1 season), La Liga (multi-season), Bundesliga (1), Serie A (1), Ligue 1 (3); Non-European: MLS, Argentine Primera | 1973/74–2023/24 (coverage varies; 1–3 seasons per Big Five league) | Pirlo: documented UNCLEAR; Xavi: documented LIKELY YES; Gerrard: documented UNCLEAR | event | shortlist (conditional — license confirmation + league coverage caveat) |
| 2 | Wyscout/Pappalardo (figshare) | https://figshare.com/collections/Soccer_match_event_dataset/4415000/5 | https://creativecommons.org/licenses/by/4.0/ | CC BY 4.0 | Big Five only: PL, La Liga, Bundesliga, Serie A, Ligue 1 | 2017/18 only | Pirlo: documented NO; Xavi: documented NO; Gerrard: documented NO | event | reject: 1 season, Big Five only |
| 3 | transfermarkt-datasets | https://github.com/dcaribou/transfermarkt-datasets | https://github.com/dcaribou/transfermarkt-datasets/blob/master/LICENSE | CC0-1.0 (public domain) | All §8 leagues likely (exact mapping PENDING) | Current; pipeline paused July 2026 | Pirlo/Xavi/Gerrard: documented LIKELY YES | match (appearance-level) | reject: no DNA performance metrics beyond goals |
| 4 | openfootball | https://github.com/openfootball/leagues | Public Domain (stated in repo README) | Public Domain | Wide — all §8 leagues likely covered | 1888–2026/27 | N/A — no per-player performance stats | match results | reject: match results only; no per-player performance metrics |
| 5 | FBref | https://fbref.com/en/ | https://www.sports-reference.com/data_use.html | EXPLICITLY PROHIBITED — scraping forbidden by ToS | All §8 leagues (before Jan 2026 Opta licence loss) | ~2017/18–2025/26 (advanced stats) | Pirlo/Xavi/Gerrard: documented YES | season-agg | reject: ToS explicitly forbids building tools from scraped data; Opta licence lost Jan 2026 |
| 6 | Understat | https://understat.com/ | No official data license found | UNCLEAR | Big Five + Russian Premier League only | 2014/15–present | Gerrard: documented LIKELY YES; Pirlo: documented NO; Xavi: documented NO | season-agg | reject: UNCLEAR license; Big Five + Russia only; missing most DNA dimensions |
| 7 | koenvo/wyscout-soccer-match-event-dataset | https://github.com/koenvo/wyscout-soccer-match-event-dataset | https://creativecommons.org/licenses/by/4.0/ | CC BY 4.0 | Big Five only, 2017/18 — same as Pappalardo | 2017/18 only | Pirlo/Xavi/Gerrard: documented NO | event | reject: duplicate of #2 in different packaging; same coverage limits |
| 8 | SkillCorner Open Data | https://github.com/SkillCorner/opendata | https://github.com/SkillCorner/opendata/blob/master/LICENSE | MIT | Non-European: Australian A-League only (10 matches) | 2024/25 only | Pirlo/Xavi/Gerrard: documented NO | match + season-agg (10 matches) | reject: A-League only, 10 matches, tracking-based data does not map to standard DNA dimensions |
| 9 | DFL/Sportec Open Data | https://github.com/ImpectAPI/open-data | CC BY 4.0 (via Scientific Data 2025 publication) | CC BY 4.0 | Bundesliga + Bundesliga 2 only (7 matches total) | 2022/23 only | Pirlo/Xavi/Gerrard: documented NO | event + tracking | reject: 7 matches in 1 league; cannot meet coverage requirement |
| 10 | datahub.io football-datasets | https://github.com/datasets/football-datasets | http://opendatacommons.org/licenses/pddl/1.0/ | PDDL 1.0 (public domain) | Big Five only | 1993–present | N/A — match results only | match results | reject: match results and standings only; no player-level stats |
| 11 | La Liga Zenodo (Zenodo 7341037) | https://zenodo.org/records/7341037 | https://creativecommons.org/licenses/by-nc/4.0/ | CC BY-NC 4.0 — UNCLEAR for portfolio | La Liga only | 2000/01–2021/22 | Xavi: documented YES; Pirlo: documented NO; Gerrard: documented NO | season-agg | reject: ELO-based player attributes only, no on-ball performance metrics; La Liga only |
| 12 | salimt/football-datasets | https://github.com/salimt/football-datasets | No license file found; source is Transfermarkt (scraping-restricted) | UNCLEAR | 93,000+ players (specific leagues UNCLEAR) | Not stated | Pirlo/Xavi/Gerrard: documented LIKELY YES | season-agg | reject: UNCLEAR license + scraped from ToS-restricted source |
| 13 | schochastics/football-data | https://github.com/schochastics/football-data | https://opendatacommons.org/licenses/by/1-0/ | ODbL-BY (attribution required) | 207 leagues including all §8 leagues | 1888–present | N/A — no player-level data | match results | reject: match results and scores only; no per-player performance metrics |
| 14 | football-data.org API | https://www.football-data.org/ | https://www.football-data.org/documentation/faq | UNCLEAR for storage/redistribution | PL, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Primeira Liga, Brasileirao (free tier) | Current; historical depth not documented | N/A — no per-player performance stats on accessible tiers | match-agg | reject: no per-player performance metrics; UNCLEAR storage/redistribution terms |
| 15 | Kaggle FBref-derived (hubertsidorowicz) | https://www.kaggle.com/datasets/hubertsidorowicz/football-players-stats-2025-2026 | CC BY 4.0 (Kaggle label) — upstream FBref ToS prohibits use | VOID — upstream FBref ToS explicitly forbids building tools from scraped data | Big Five only | 2024/25, 2025/26 | Pirlo/Xavi/Gerrard: documented NO (all retired) | season-agg | reject: upstream data owner ToS prohibition overrides Kaggle label |

---

### 1. StatsBomb Open Data

- **Dataset URL**: https://github.com/statsbomb/open-data
- **License URL**: https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf (PDF, content not renderable via web fetch; terms described in README and socceraction docs)
- **License type**: StatsBomb Public Data User Agreement — documented as "freely available for public non-commercial use"; requires attribution and StatsBomb logo. The LICENSE.pdf is not a standard open license (CC/MIT/etc.). Non-commercial restriction confirmed by third-party documentation at https://socceraction.readthedocs.io/en/latest/documentation/data/statsbomb.html. Portfolio use is described as encouraged ("publish your work on social media") but the agreement explicitly states non-commercial; a public portfolio project that attracts future employers sits in a grey area. Mark as UNCLEAR for the purposes of this ADR until the LICENSE.pdf text is read directly.
- **Leagues covered** (brief §8 mapping):
  - Big Five: Premier League (England) — 2015/16, 2003/04; La Liga (Spain) — 1973/74 through 2020/21; Bundesliga (Germany) — 2015/16, 2023/24; Serie A (Italy) — 1986/87, 2015/16; Ligue 1 (France) — 2015/16, 2021/22, 2022/23
  - Strong secondary: None confirmed in open data
  - Development/scouting: None confirmed in open data
  - Non-European: MLS (United States) — documented YES; Argentine Primera (Liga Profesional) — documented YES
  - Additional: Copa América, FIFA World Cup, UEFA Euro, FA WSL, Indian Super League (women's data not in scope)
- **Seasons** (documented): Ranges from single seasons (e.g. Bundesliga 2023/24 only) to multi-season (La Liga back to 1973/74). Most Big Five leagues have only 1–3 seasons in the open set. Data sourced from competitions.json in the GitHub repository.
- **Player count** (documented): Not stated in documentation. Event-level only; player season aggregates require self-aggregation.
- **Level**: event (JSON match events and lineups; NO pre-built season-aggregate player statistics)
- **Historical players** (documented):
  - Pirlo (Serie A) — documented UNCLEAR: Serie A 1986/87 season is in the data but Pirlo played 1995–2001 at Brescia/Inter; no direct confirmation of Pirlo records in open data documentation.
  - Xavi (La Liga) — documented YES likely: La Liga coverage spans 1973/74–2020/21, which includes Xavi's career (1998–2015 at Barça), but no explicit player-level confirmation.
  - Gerrard (Premier League) — documented UNCLEAR: only 2003/04 and 2015/16 seasons in open data; Gerrard played both but no explicit confirmation in docs.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | Passing | Y (event) | Pass events include location, outcome, technique, pressure flag, length |
  | Progression | Y (event) | Carry events include start/end location; progressive passes derivable |
  | Creativity/Creation | Y (event) | Shot-assisting passes, through balls, crosses all in event stream |
  | Ball Carrying | Y (event) | Carry events with distance |
  | Defending | Y (event) | Pressure, tackle, interception, block event types |
  | Goal Threat | Y (event) | Shot events include StatsBomb xG, shot technique, body part |
  | Ball Security | Y (event) | Miscontrol and dispossessed event types |

  All dimensions require custom aggregation from event logs. No pre-built season totals.

- **Metric form** (documented from event JSON samples — files 3788741, 15946, 22912 — and statsbombpy column list):

  | Metric | Field name in source | Form | Numerator & denominator available? |
  |--------|----------------------|------|-------------------------------------|
  | Pass attempts | One `type.name = "Pass"` event per attempt | Raw event count (1 row = 1 attempt) | Yes — count Pass events per player; minutes computed from lineups separately |
  | Pass completions | Pass event where `pass.outcome` is absent (null = success) | Raw event count (filter by outcome field) | Yes |
  | Progressive passes | No native flag — must be derived from `location` + `pass.end_location` coordinates | Derived (pitch-geometry calculation by consumer) | Coordinates available; no pre-flagged field |
  | Key passes / shot-assisting passes | `pass.shot_assist = true` or `pass.goal_assist = true` (`pass_shot_assist`, `pass_goal_assist` in statsbombpy) | Raw boolean flag on Pass event | Yes |
  | Carries | One `type.name = "Carry"` event per carry; sub-object: `carry.end_location` only | Raw event count | Yes |
  | Progressive carries | No native flag — must be derived from `location` + `carry.end_location` | Derived (pitch-geometry calculation by consumer) | Coordinates available; no pre-flagged field |
  | Dribble attempts | `type.name = "Dribble"` event | Raw event count | Yes |
  | Dribble completions | Dribble event where `dribble.outcome.name = "Complete"` | Raw event count (filter by outcome) | Yes |
  | Tackles attempted/won | `type.name = "Tackle"`; `tackle.outcome` distinguishes won/lost | Raw event count; outcome field present | Yes |
  | Interceptions | `type.name = "Interception"`; `interception.outcome` present (confirmed sample 15946 index 92) | Raw event count | Yes |
  | Pressures applied | `type.name = "Pressure"`; `counterpress` boolean also present (confirmed sample 15946 index 62) | Raw event count | Yes |
  | Shots (total) | `type.name = "Shot"` | Raw event count | Yes |
  | Shots on target | Shot event where `shot.outcome.name` ∈ {"Goal", "Saved", "Saved To Post"} | Raw event count (filter by outcome) | Yes |
  | Goals | Shot event where `shot.outcome.name = "Goal"` | Raw event count (filter by outcome) | Yes |
  | xG (per shot) | `shot.statsbomb_xg` (confirmed value 0.7835 in sample 22912) | Model output — per-shot float | Yes — one value per shot; sum gives season total; shot count also preserved |
  | Minutes played | No single field — computed from `positions[].from` / `positions[].to` timestamps in lineups JSON | Derived from timestamp arithmetic across lineup stints per match | Yes — start/end timestamps present per stint; calculation required |
  | Miscontrols | `type.name = "Miscontrol"` (confirmed sample 15946 index 66) | Raw event count | Yes |
  | Dispossessed | `type.name = "Dispossessed"` (documented in statsbombpy column list) | Raw event count | Yes |

  **Numerator/denominator/minutes summary**: StatsBomb fully preserves numerators and denominators because every action is a separate JSON object with player ID, match ID, timestamp, and period — raw counts and rate denominators can be reconstructed exactly from first principles. Minutes played requires consumer-side arithmetic (summing stint durations from the lineup `positions` array), but all required timestamps are present. xG can be summed to a season total and the underlying shot count is also preserved. Progressive pass and carry flags must be derived from pitch coordinates, which are present.

  **Evidence**: https://github.com/statsbomb/open-data (README); https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/3788741.json; https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/15946.json; https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/22912.json; https://raw.githubusercontent.com/statsbomb/open-data/master/data/lineups/3788741.json; https://github.com/statsbomb/statsbombpy (column list); https://github.com/statsbomb/open-data/issues/47 (license framing)

- **Measured evidence**: PENDING (sampling step)
- **Verdict**: shortlist (conditionally) — rich event data covering most DNA dimensions; Big Five coverage only in the open set with very limited seasons per league; license requires direct reading of LICENSE.pdf to confirm portfolio use is permitted before use. Disqualifying concern: very few secondary/development leagues in open data; would not meet ≥10 leagues requirement without supplementation.

---

### 2. Wyscout / Pappalardo Dataset (figshare, 2019)

- **Dataset URL**: https://figshare.com/collections/Soccer_match_event_dataset/4415000/5
- **License URL**: https://creativecommons.org/licenses/by/4.0/ (CC BY 4.0, as stated by Pappalardo et al. in the figshare record and in the associated Scientific Data paper)
- **License type**: CC BY 4.0 — explicitly permits reuse in public projects with attribution; no non-commercial restriction. Data source is citable via DOI: https://doi.org/10.6084/m9.figshare.c.4415000.v5
- **Leagues covered** (brief §8 mapping):
  - Big Five: Premier League, La Liga, Bundesliga, Serie A, Ligue 1 — all documented YES (2017/18 season only)
  - Strong secondary: None
  - Development/scouting: None
  - Non-European: None (2018 FIFA World Cup included but not a domestic league)
- **Seasons** (documented): One season only — 2017/18 (plus World Cup 2018 and Euro 2016)
- **Player count** (documented): Dataset covers all players who appeared in the five leagues' 2017/18 season; exact count not stated in documentation.
- **Level**: event (JSON match event logs; no pre-built season-aggregate player statistics)
- **Historical players** (documented):
  - Pirlo (Serie A) — documented NO: Pirlo retired from Serie A in 2015; 2017/18 season postdates his Italian career.
  - Xavi (La Liga) — documented NO: Xavi left Barça in 2015; 2017/18 season postdates his La Liga career.
  - Gerrard (Premier League) — documented NO: Gerrard retired from Liverpool in 2015.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | Passing | Y (event) | Pass events with location, outcome, body part |
  | Progression | Partial (event) | Carries not a distinct event type in Wyscout format; progressive passes derivable from pass locations |
  | Creativity/Creation | Y (event) | Key passes, crosses, through balls in event stream |
  | Ball Carrying | Partial (event) | Dribble events present; full carry tracking less granular than StatsBomb |
  | Defending | Y (event) | Tackle, interception, clearance, air duel events |
  | Goal Threat | Y (event) | Shot events; no native xG (third-party xG models required) |
  | Ball Security | Partial (event) | Foul-drawing events; dispossessed not explicitly tagged the same way as StatsBomb |

- **Measured evidence**: PENDING (sampling step)
- **Verdict**: reject — only one season (2017/18), Big Five only, no secondary or development leagues; does not meet the ≥10 leagues or multi-season requirements. License is excellent (CC BY 4.0) but coverage is too narrow.

---

### 3. transfermarkt-datasets (dcaribou/transfermarkt-datasets)

- **Dataset URL**: https://github.com/dcaribou/transfermarkt-datasets / https://www.kaggle.com/datasets/davidcariboo/player-scores
- **License URL**: https://github.com/dcaribou/transfermarkt-datasets/blob/master/LICENSE (CC0-1.0 — public domain dedication)
- **License type**: CC0-1.0 — documented as "public domain"; no attribution required; explicitly permits any use including commercial.
- **Leagues covered** (brief §8 mapping): Documentation states "12 joinable tables" and "88,000+ games" but does not enumerate specific leagues on the main page. Data sourced from Transfermarkt which covers all leagues in the brief. Exact mapping to brief §8 leagues requires inspection of the data files (PENDING).
- **Seasons** (documented): "current to 6 July 2026" per GitHub README; automated collection pipeline has been paused since mid-July 2026.
- **Player count** (documented): "50,000+ players" and "1,890,000+ appearances" documented on GitHub.
- **Level**: match (appearance-level — goals, cards, substitutions per game; NOT full per-player per-season aggregate statistics)
- **Historical players** (documented):
  - Pirlo (Serie A) — documented LIKELY YES: Dataset covers historical Transfermarkt records; no explicit confirmation in docs.
  - Xavi (La Liga) — documented LIKELY YES: Same reasoning.
  - Gerrard (Premier League) — documented LIKELY YES: Same reasoning.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | Passing | N | No passing metrics in the dataset |
  | Progression | N | No progressive pass/carry metrics |
  | Creativity/Creation | N | No xA, key passes, or shot-creating actions |
  | Ball Carrying | N | No carry metrics |
  | Defending | N | No tackle, interception, pressure metrics |
  | Goal Threat | Partial | Goals per appearance available; no xG or shot volume |
  | Ball Security | N | No miscontrol or dispossessed metrics |

  Dataset provides player biographical data, transfer records, market values, and appearance-level goals/cards/minutes — not the performance metrics needed for DNA dimensions.

- **Measured evidence**: PENDING (sampling step)
- **Verdict**: reject — no DNA-relevant performance metrics beyond goals; does not meet the ≥15 per-player metrics requirement. Excellent for player identity/career enrichment as a supplementary source.

---

### 4. football.db / openfootball (openfootball GitHub org)

- **Dataset URL**: https://github.com/openfootball/leagues
- **License URL**: https://github.com/openfootball/leagues (README states Public Domain)
- **License type**: Public domain — documented in multiple openfootball repositories; no restrictions.
- **Leagues covered** (brief §8 mapping): Documentation mentions Premier League, Bundesliga, Serie A, La Liga, Ligue 1 and many others worldwide. Wide league coverage documented in the 'europe' and 'world' repos.
- **Seasons** (documented): Match schedules current through 2026/27; historical data documented back to 1888 for some competitions.
- **Player count** (documented): openfootball/players repo contains player biographical data (name, position, DOB) for players in covered leagues; no count stated.
- **Level**: match (results, scores, lineups; no per-player performance statistics)
- **Historical players** (documented): UNCLEAR — player biographical data available; no performance stats.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | Passing | N | No passing statistics |
  | Progression | N | No progression metrics |
  | Creativity/Creation | N | No creativity metrics |
  | Ball Carrying | N | No carry metrics |
  | Defending | N | No defensive action stats |
  | Goal Threat | N | Goals in results but not per-player season aggregates |
  | Ball Security | N | No ball security metrics |

- **Measured evidence**: PENDING (sampling step)
- **Verdict**: reject — match schedule/results database only; no per-player performance metrics whatsoever. Cannot meet DNA dimension requirements.

---

### 5. FBref (Sports-Reference.com) — web data (not officially downloadable)

- **Dataset URL**: https://fbref.com/en/ (no official download; web tables only)
- **License URL**: https://www.sports-reference.com/data_use.html
- **License type**: EXPLICITLY PROHIBITED — Sports-Reference states: "you should not create websites or tools based on data you scrape from Sports Reference or any of our sites." Source: https://www.sports-reference.com/data_use.html (confirmed via search result quoting). Additionally: "most of their data comes from third parties, and as part of their agreements with them they cannot provide the data available as a download on the site." As of January 2026 FBref lost its Opta data licence, removing advanced stats from the site.
- **Leagues covered** (brief §8 mapping): Would cover all target leagues before the licence loss. Now missing advanced stats (xG, progressive passes, etc.) post-January 2026.
- **Seasons** (documented): Extensive historical coverage; advanced stats available from ~2017/18 for Big Five leagues.
- **Player count** (documented): Tens of thousands of players documented.
- **Level**: season-agg (pre-built season aggregate tables per player)
- **Historical players** (documented): documented YES for Pirlo, Xavi, Gerrard.
- **DNA coverage**: Would have covered all 7 dimensions before January 2026 licence loss.
- **Measured evidence**: PENDING (sampling step) — but moot given license status.
- **Verdict**: reject — explicit ToS prohibition on building websites/tools from scraped data; no official download mechanism; advanced stats licence lost January 2026.

---

### 6. Understat (understat.com) — xG and shot data

- **Dataset URL**: https://understat.com/ (web only; Python clients available e.g. https://github.com/amosbastian/understat)
- **License URL**: No official dataset license or terms for data reuse found; website has standard ToS.
- **License type**: UNCLEAR — Understat provides no official data download or explicit license for data reuse. Third-party scrapers exist (e.g. understat Python package) but the source site's ToS does not explicitly permit scraping or data redistribution.
- **Leagues covered** (brief §8 mapping): Premier League, La Liga, Bundesliga, Serie A, Ligue 1 (Big Five all YES); also Russian Premier League. No secondary or development leagues in scope.
- **Seasons** (documented): 2014/15 to present for all six leagues.
- **Player count** (documented): ~562 players per league per season documented.
- **Level**: season-agg (aggregated player season statistics available on web)
- **Historical players** (documented): documented LIKELY YES for Gerrard (played 2014/15, the first season); documented NO for Pirlo and Xavi (Italian/Spanish careers ended before 2014/15 data).
- **DNA coverage**: xG, npxG, xGChain, xGBuildup, goals, assists, shots; no passing detail, progressive passes, carries, pressures, or defensive actions.
- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — UNCLEAR license; scraping would be required; Big Five plus Russia only; missing most DNA dimensions (no passing detail, carrying, defending, or creation sub-metrics).

---

### 7. Wyscout-soccer-match-event-dataset (koenvo repackaging)

- **Dataset URL**: https://github.com/koenvo/wyscout-soccer-match-event-dataset
- **License URL**: https://creativecommons.org/licenses/by/4.0/ (inherits from Pappalardo dataset, CC BY 4.0)
- **License type**: CC BY 4.0 — same as the source Pappalardo figshare dataset; explicitly permits reuse.
- **Leagues covered** (brief §8 mapping): Same as Pappalardo: Big Five only, 2017/18.
- **Seasons** (documented): 2017/18 only.
- **Player count** (documented): Same as Pappalardo dataset.
- **Level**: event (kloppy-loadable JSON restructuring of the Pappalardo data)
- **Historical players** (documented): Same as Pappalardo — Pirlo/Xavi/Gerrard documented NO (2017/18 postdates all three careers).
- **DNA coverage**: Same as Pappalardo — see entry #2.
- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — same dataset as Pappalardo (#2) in a different packaging; same disqualifying coverage limitations.

---

### 8. SkillCorner Open Data

- **Dataset URL**: https://github.com/SkillCorner/opendata
- **License URL**: https://github.com/SkillCorner/opendata/blob/master/LICENSE (MIT License — documented)
- **License type**: MIT — explicitly permits unrestricted use including public portfolio projects and local storage.
- **Leagues covered** (brief §8 mapping):
  - Big Five: None documented in open dataset
  - Strong secondary: None
  - Development/scouting: None
  - Non-European: Australian A-League 2024/25 only (10 matches)
- **Seasons** (documented): 2024/25 A-League season; 10 broadcast tracking matches.
- **Player count** (documented): Players in 10 A-League 2024/25 matches; exact count not stated.
- **Level**: match + season-agg (10 matches with player-season aggregate CSVs for Physical, Off-Ball Runs, and Passing; minimum 60 minutes per match)
- **Historical players** (documented):
  - Pirlo (Serie A) — documented NO: A-League data only.
  - Xavi (La Liga) — documented NO: A-League data only.
  - Gerrard (Premier League) — documented NO: A-League data only.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | Passing | Partial | Passing volume and efficiency metrics; no pass completion %, key passes |
  | Progression | Partial | Off-ball run metrics; progressive passes derivable |
  | Creativity/Creation | N | No xA, shot-creating actions, or crosses |
  | Ball Carrying | N | Physical tracking data; no on-ball carry metrics |
  | Defending | N | No tackle/interception/pressure counts |
  | Goal Threat | N | No xG or shot metrics |
  | Ball Security | N | No miscontrol/dispossessed metrics |

- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — A-League only (10 matches); does not meet league coverage requirement; tracking data does not map to standard DNA dimensions without extensive event data.

---

### 9. DFL/Sportec Open Data (Bundesliga sample)

- **Dataset URL**: https://github.com/ImpectAPI/open-data (referenced; also via kloppy)
- **License URL**: Data provided by DFL under CC BY 4.0 (documented in search results referencing an integrated dataset publication in Scientific Data 2025, https://www.nature.com/articles/s41597-025-04505-y)
- **License type**: CC BY 4.0 — explicitly permits reuse with attribution.
- **Leagues covered** (brief §8 mapping):
  - Big Five: Bundesliga (Germany) — 2 matches; Bundesliga 2 — 5 matches (2022/23)
  - All others: None
- **Seasons** (documented): 2022/23 season only; 7 total matches (2 Bundesliga 1 + 5 Bundesliga 2).
- **Player count** (documented): Players in 7 matches; exact count not stated.
- **Level**: event + tracking (synchronised TRACAB tracking and event data)
- **Historical players** (documented):
  - Pirlo / Xavi / Gerrard — documented NO: German 2022/23 data only; all three had no role in Bundesliga.
- **DNA coverage**: Would cover all dimensions from event data but requires extensive aggregation; only 7 matches total.
- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — 7 matches in one league; cannot meet coverage requirements. Excellent for methodology validation on a small sample but not usable as a primary data source.

---

### 10. datahub.io / datasets/football-datasets

- **Dataset URL**: https://github.com/datasets/football-datasets
- **License URL**: http://opendatacommons.org/licenses/pddl/1.0/ (PDDL 1.0 — Public Domain Dedication and License)
- **License type**: PDDL 1.0 — public domain; no restrictions on use.
- **Leagues covered** (brief §8 mapping):
  - Big Five: Premier League, La Liga, Serie A, Bundesliga, Ligue 1 — all documented YES
  - Strong secondary: None documented
  - Development/scouting: None documented
  - Non-European: None documented
- **Seasons** (documented): 1993 to current; auto-updated daily per documentation.
- **Player count** (documented): Match results, standings, odds data. No player-level performance data.
- **Level**: match (results, odds, standings only; no player-level statistics)
- **Historical players** (documented): UNCLEAR — match results only; no per-player stats.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | All 7 dimensions | N | No per-player performance metrics; match results and odds only |

- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — match results and betting odds; no per-player performance metrics.

---

### 11. Spanish La Liga Dataset — Zenodo (CC BY-NC 4.0)

- **Dataset URL**: https://zenodo.org/records/7341037
- **License URL**: https://creativecommons.org/licenses/by-nc/4.0/
- **License type**: CC BY-NC 4.0 — non-commercial only. A public portfolio project that is shared with potential employers and hosted publicly may be considered non-commercial, but this is UNCLEAR; the restriction is ambiguous for portfolio use.
- **Leagues covered** (brief §8 mapping): La Liga only.
- **Seasons** (documented): 2000/01 through 2021/22 (22 seasons).
- **Player count** (documented): Not stated; spans 22 La Liga seasons.
- **Level**: season-agg (JSON files per season with player attributes)
- **Historical players** (documented): Xavi (La Liga) — documented YES: career spans 1998–2015, covered by 2000/01–2021/22 window.
  - Pirlo (Serie A) — documented NO: La Liga data only.
  - Gerrard (Premier League) — documented NO: La Liga data only.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | All 7 dimensions | N | Player attributes include ELO rating, position, DOB, nationality — not performance metrics. No goals, passes, tackles, or xG. |

- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — ELO-based player attributes only, not performance statistics; La Liga only; NC license is borderline for portfolio use.

---

### 12. salimt/football-datasets (Transfermarkt scrape)

- **Dataset URL**: https://github.com/salimt/football-datasets
- **License URL**: Not found in search results; no explicit license file stated.
- **License type**: UNCLEAR — no license stated; data scraped from Transfermarkt which has its own ToS restricting data extraction.
- **Leagues covered** (brief §8 mapping): Documented as covering 93,000+ players from Transfermarkt; specific league list not stated.
- **Seasons** (documented): Not stated.
- **Player count** (documented): 93,000+ players.
- **Level**: season-agg (player profiles, transfer history, market values)
- **Historical players** (documented): LIKELY YES given scale; not explicitly confirmed.
- **DNA coverage**: Primarily player profiles, market values, transfer records, injury records; no on-ball performance metrics for DNA dimensions.
- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — UNCLEAR license (no stated license + scraped from Transfermarkt ToS-restricted site); no per-player on-ball performance metrics.

---

### 13. schochastics/football-data (match results)

- **Dataset URL**: https://github.com/schochastics/football-data
- **License URL**: https://opendatacommons.org/licenses/by/1-0/ (Open Data Commons Attribution License)
- **License type**: ODbL-BY — attribution required; permits public use and redistribution.
- **Leagues covered** (brief §8 mapping): 207 top-tier domestic leagues documented; would include all brief §8 leagues. Wide coverage.
- **Seasons** (documented): "1,237,935 football games" spanning 1888–present; 20 international tournaments.
- **Player count** (documented): Match results only; no individual player data.
- **Level**: match (results and scores only; no player-level statistics)
- **Historical players** (documented): Not applicable — no player-level data.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | All 7 dimensions | N | Match results only |

- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — match results only; no per-player performance metrics.

---

### 14. football-data.org API (Daniel Freitag)

- **Dataset URL**: https://www.football-data.org/
- **License URL**: https://www.football-data.org/documentation/faq (states attribution required: "Data provided by football-data.org")
- **License type**: UNCLEAR for storage/redistribution — FAQ only states attribution requirement; no explicit ToS clause found permitting local database storage or serving data from a custom API. Developer credentials must not be stored in open-source repositories.
- **Leagues covered** (brief §8 mapping):
  - Big Five: Premier League, La Liga, Bundesliga, Serie A, Ligue 1 — documented YES (free tier)
  - Strong secondary: Eredivisie, Primeira Liga — documented YES (free tier)
  - Development/scouting: None on free tier
  - Non-European: Brasileirao (Série A) — documented YES (free tier)
- **Seasons** (documented): Results and standings current; historical range not specified in public docs.
- **Player count** (documented): Per-player season aggregate statistics NOT available on free or basic plans. Only top scorers (goals) per competition. Deep player data (lineups, squads) requires Deep Data plan at €29/month.
- **Level**: match-agg at best (per competition standings, top scorers; no full player season stats on accessible tiers)
- **Historical players** (documented): Not applicable for performance stats.
- **DNA coverage**:

  | Dimension | Available? | Notes |
  |-----------|------------|-------|
  | All 7 dimensions | N | No per-player aggregate performance metrics available at any tier reviewed; match results and top-scorer goals only |

- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — no per-player performance metrics beyond top-scorer goals; UNCLEAR storage/redistribution terms for portfolio use.

---

### 15. Kaggle FBref-derived datasets (e.g. hubertsidorowicz/football-players-stats-2025-2026)

- **Dataset URL**: https://www.kaggle.com/datasets/hubertsidorowicz/football-players-stats-2025-2026
- **License URL**: Listed as CC BY 4.0 on Kaggle
- **License type**: UNCLEAR — the Kaggle uploader has applied CC BY 4.0 to their derived work, but the upstream data is from FBref, whose Terms of Use explicitly prohibit creating websites or tools from scraped data ("you should not create websites or tools based on data you scrape from Sports Reference or any of our sites" — https://www.sports-reference.com/data_use.html). The CC BY 4.0 label on Kaggle reflects the uploader's intent but cannot override the upstream ToS. The data collection method is confirmed automated scraping.
- **Leagues covered** (brief §8 mapping): Big Five only (Premier League, La Liga, Bundesliga, Serie A, Ligue 1) documented.
- **Seasons** (documented): 2024/25 and 2025/26 seasons documented.
- **Player count** (documented): Not stated explicitly; Big Five leagues combined.
- **Level**: season-agg (250+ columns including advanced metrics like xG, progressive passes, pressures)
- **Historical players** (documented): Pirlo, Xavi, Gerrard — documented NO (2024/25 and 2025/26 only; all three are retired).
- **DNA coverage**: Would cover all 7 dimensions based on FBref's historical metric set; however, FBref lost its Opta advanced data licence in January 2026, removing advanced stats.
- **Measured evidence**: PENDING (sampling step).
- **Verdict**: reject — upstream FBref ToS explicitly prohibits building tools from scraped data; the Kaggle CC BY 4.0 label does not override the upstream data owner's restrictions. Cannot use this source.

---

## Paid API comparison

Estimated-requests calculation: For 1 season × 15 leagues, assuming ~20 players per team × 20 teams per league = 400 players/league, 15 leagues = 6,000 player records. If the API requires one request per player, that is 6,000 requests to collect one season of player stats. In practice, APIs typically paginate at 20–50 players per request, so roughly 120–300 requests for season stats collection. This is a one-time bulk load, not a live streaming use case.

| API | Free-tier cost | Lowest paid tier ($/mo equiv.) | Free-tier request limits | Paid-tier request limits | Estimated requests: 1 season × 15 leagues | Leagues covered (brief §8 by name) | Per-player metrics available | Historical seasons | Portfolio demo allowed? | Local storage/DB allowed? (exact clause + URL) |
|-----|---------------|-------------------------------|--------------------------|--------------------------|-------------------------------------------|------------------------------------|-----------------------------|--------------------|-------------------------|------------------------------------------------|
| API-Football (api-sports.io) | $0 | ~$19/mo (Pro plan) | 100 requests/day | 7,500 requests/day (Pro) | ~300 requests for 15-league season bulk load; feasible on Pro in <1 day; feasible on free in ~3 days. Live polling of 15 leagues is not feasible on free tier. | Big Five: Premier League, La Liga, Bundesliga, Serie A, Ligue 1 — documented YES. Eredivisie, Primeira Liga likely YES (1,200+ leagues claimed). All §8 development/scouting leagues likely YES but data completeness for smaller leagues UNCLEAR. | Goals, assists, shots (total/on-target), passes (total/accuracy), tackles, duels, dribbles, fouls, cards, minutes, position, rating. xG: available from 2024 season onward for top European competitions only. Progressive passes, pressures, carries: NOT available. Approximately 12 metrics covering 4 of 7 DNA dimensions. | Multiple seasons documented; exact historical depth varies by competition. Advanced stats (xG) only from 2024 onward. | UNCLEAR — API-Sports ToS states service is "solely provided for your personal, non-commercial use" and materials "may not be reproduced, distributed, publicly displayed or otherwise used, without the prior consent"; also states "distribution, transfer and storage of the data provided by the Service are allowed." Contradiction between clauses is unresolved. Source: https://apifootball.com/terms_of_use/ | UNCLEAR — "distribution, transfer and storage of data are allowed" per APIfootball ToS but materials "may not be publicly displayed without prior consent" creates conflict. No explicit portfolio/public use permission clause found. URL: https://apifootball.com/terms_of_use/ |
| Sportmonks Football API | €0 (free plan: 2 leagues — Scottish Premiership + Danish Superliga) | €29/mo (Starter: 5 leagues) | 2 leagues; rate limits not published for free tier | 2,000–2,500 calls/entity/hour (Starter/Growth); 3,000 (Pro) | For 15 leagues: requires Growth plan (€99/mo, 30 leagues) or above. With 2,500 calls/hour, 300 requests for season bulk load completes in seconds. Live use: no issue. | Free tier: Danish Superliga (YES — brief §8 Development/scouting), Scottish Premiership (not in brief §8). Growth (€99/mo, 30 leagues): Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie, Primeira Liga + development leagues all likely YES within 30-league selection. Pro (€249/mo): all 120 leagues including all brief §8 leagues. | Goals, assists, shots (on/off target), passes, key passes, crosses, tackles, interceptions, clearances, duels, dribbles, fouls, big chances, minutes, rating, cards. xG add-on: xG, xGot, xPTS, npxG (€29/mo extra). Progressive passes, pressures, ball carries: NOT confirmed in standard API. Approximately 15 metrics covering 5–6 of 7 DNA dimensions (with xG add-on). | Seasonal performance data across all plans documented; historical depth not quantified. | YES (explicitly documented) — "if you use our data to create something based on our data and start earning money from your creation, everything is fine" per Sportmonks ToS. Sportmonks blog explicitly lists "portfolio piece" as an appropriate free-tier use case. | YES (explicitly documented) — "distribution, transfer, and storage of data provided by our services is allowed." Exact quote from Sportmonks ToS at https://www.sportmonks.com/terms-of-service/. Reselling data as a product is prohibited; building a portfolio app is not reselling. |

### API-Football — metric form

All values in the `/v3/players` response are raw seasonal aggregates (no pre-computed per-90). Evidence: https://apifootball.com/documentation (confirmed `player_passes`, `player_passes_acc` field context); https://github.com/hquantran/soccer-analytics (dbt staging model `stg_players.sql` confirms `passes__accuracy` cast as percentage and `games__minutes` used as per-90 denominator `goals * 90 / minutes`); https://github.com/edseldim/api_football_features_etl (confirmed field list).

| Metric | Field name in source | Form | Numerator & denominator available? |
|--------|----------------------|------|-------------------------------------|
| Pass attempts | `statistics[].passes.total` | Raw count (seasonal total) | Yes — `games.minutes` is the denominator |
| Pass completions | Not available as a count — only `statistics[].passes.accuracy` | Ratio (pre-computed percentage, e.g. 75.5) | **No** — accurate-pass count is not exposed; numerator cannot be recovered |
| Progressive passes | Not available | — | No |
| Key passes | `statistics[].passes.key` | Raw count | Yes |
| Carries (progressive) | Not available | — | No |
| Dribble attempts | `statistics[].dribbles.attempts` | Raw count | Yes |
| Dribble completions | `statistics[].dribbles.success` | Raw count | Yes |
| Tackles (total) | `statistics[].tackles.total` | Raw count | Yes |
| Tackles won | Not available — only total, blocks, interceptions | — | No won/attempted split |
| Interceptions | `statistics[].tackles.interceptions` | Raw count | Yes |
| Pressures applied | Not available | — | No |
| Shots (total) | `statistics[].shots.total` | Raw count | Yes |
| Shots on target | `statistics[].shots.on` | Raw count | Yes |
| Goals | `statistics[].goals.total` | Raw count | Yes |
| Assists | `statistics[].goals.assists` | Raw count | Yes |
| xG | Not available (xG exists from 2024 onward for top competitions only; no field in standard player stats endpoint) | — | No |
| Minutes played | `statistics[].games.minutes` | Raw count (seasonal total, confirmed as per-90 denominator) | Yes |
| Miscontrols | Not available | — | No |
| Dispossessed | Not available | — | No |
| Duels (total) | `statistics[].duels.total` | Raw count | Yes |
| Duels won | `statistics[].duels.won` | Raw count | Yes |
| Fouls committed | `statistics[].fouls.committed` | Raw count | Yes |
| Fouls drawn | `statistics[].fouls.drawn` | Raw count | Yes |
| Appearances | `statistics[].games.appearances` | Raw count | Yes |
| Starts | `statistics[].games.lineups` | Raw count | Yes |

**Numerator/denominator/minutes summary**: `games.minutes` is a raw seasonal total and is usable as the per-90 denominator. However, `passes.accuracy` is delivered as a pre-computed percentage with no separate accurate-pass count, which directly violates the CLAUDE.md requirement to preserve numerators alongside derived figures — the accurate-pass numerator cannot be recovered without re-querying the API. No xG, no progressive passes, no carries, and no pressures are available, leaving four of seven DNA dimensions partially or fully uncovered.

---

### Sportmonks — metric form

Sportmonks uses a key-value architecture: the `PlayerStatistic` entity carries a `details` array of `PlayerStatisticDetail` records, each with a `type_id` integer and a `value` object. Consumers filter by `type_id` to retrieve specific metrics. All values are raw seasonal totals. Full stat type registry: https://docs.sportmonks.com/v3/definitions/types/statistics/player-statistics. Additional evidence: https://docs.sportmonks.com/football/endpoints-and-entities/entities/statistic; https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/statistics/players-statistics.

| Metric | Field name in source | Form | Numerator & denominator available? |
|--------|----------------------|------|-------------------------------------|
| Pass attempts | `details[type_id=80].value` (code: `passes`) | Raw count | Yes — `details[type_id=119]` is minutes |
| Pass completions | `details[type_id=116].value` (code: `accurate-passes`) | Raw count | Yes |
| Pass completion % | `details[type_id=1584].value` (code: `accurate-passes-percentage`) | Ratio (percentage) | Redundant — both IDs 80 and 116 available separately |
| Progressive passes | Not in stat type registry | — | No |
| Key passes | `details[type_id=117].value` (code: `key-passes`) | Raw count | Yes |
| Total crosses | `details[type_id=98].value` (code: `total-crosses`) | Raw count | Yes |
| Accurate crosses | `details[type_id=99].value` (code: `accurate-crosses`) | Raw count | Yes |
| Carries (progressive) | Not in stat type registry | — | No |
| Dribble attempts | `details[type_id=108].value` (code: `dribble-attempts`) | Raw count | Yes |
| Dribble completions | `details[type_id=109].value` (code: `successful-dribbles`) | Raw count | Yes |
| Dribbled past | `details[type_id=110].value` (code: `dribbled-past`) | Raw count | Yes |
| Tackles | `details[type_id=78].value` (code: `tackles`) | Raw count | Yes |
| Tackles won | `details[type_id=27267].value` (code: `tackles-won`) | Raw count | Yes |
| Interceptions | `details[type_id=100].value` (code: `interceptions`) | Raw count | Yes |
| Pressures applied | Not in stat type registry | — | No |
| Shots (total) | `details[type_id=42].value` (code: `shots-total`) | Raw count | Yes |
| Shots on target | `details[type_id=86].value` (code: `shots-on-target`) | Raw count | Yes |
| Shots off target | `details[type_id=41].value` (code: `shots-off-target`) | Raw count | Yes |
| Shots blocked | `details[type_id=58].value` (code: `shots-blocked`) | Raw count | Yes |
| Goals | `details[type_id=52].value` (code: `goals`) | Raw count | Yes |
| Assists | `details[type_id=79].value` (code: `assists`) | Raw count | Yes |
| xG (season total) | `details[type_id=5304].value` (code: `expected-goals`) — xG add-on required (€29/mo) | Model output — seasonal sum at player level; no per-shot xG | xG sum available; per-shot breakdown not available |
| Minutes played | `details[type_id=119].value` (code: `minutes-played`, documented as "Total minutes the player played across the season") | Raw count (seasonal total) | Yes — confirmed as per-90 denominator |
| Dispossessed | `details[type_id=94].value` (code: `dispossessed`) | Raw count | Yes |
| Miscontrols | Not in stat type registry | — | No |
| Fouls committed | `details[type_id=56].value` (code: `fouls`) | Raw count | Yes |
| Fouls drawn | `details[type_id=96].value` (code: `fouls-drawn`) | Raw count | Yes |
| Yellow cards | `details[type_id=84].value` (code: `yellowcards`) | Raw count | Yes |
| Red cards | `details[type_id=83].value` (code: `redcards`) | Raw count | Yes |
| Duels (total) | `details[type_id=105].value` (code: `total-duels`) | Raw count | Yes |
| Duels won | `details[type_id=106].value` (code: `duels-won`) | Raw count | Yes |
| Aerial duels won | `details[type_id=107].value` (code: `aeriels-won`) | Raw count | Yes (no aerial-duels-attempted type — won only) |
| Big chances created | `details[type_id=580].value` (code: `big-chances-created`) | Raw count | Yes |
| Big chances missed | `details[type_id=581].value` (code: `big-chances-missed`) | Raw count | Yes |
| Long balls attempted | `details[type_id=122].value` (code: `long-balls`) | Raw count | Yes |
| Long balls won | `details[type_id=123].value` (code: `long-balls-won`) | Raw count | Yes |
| Through balls | `details[type_id=124].value` (code: `through-balls`) | Raw count | Yes |
| Appearances | `details[type_id=321].value` (code: `appearances`) | Raw count | Yes |
| Starts | `details[type_id=322].value` (code: `lineups`) | Raw count | Yes |

**Numerator/denominator/minutes summary**: Sportmonks provides both attempted and completed counts as separate `type_id` entries for all major actions (passes: IDs 80 and 116; dribbles: 108 and 109; tackles: 78 and 27267; shots: 42, 86, 41, 58), fully satisfying the CLAUDE.md requirement to preserve numerators alongside derived figures. Minutes played (type_id 119) is documented as a raw seasonal total, confirming it is usable as the per-90 denominator. The one structural limitation is that xG is only available as a seasonal sum (type_id 5304) at player level — there is no per-shot xG value — and progressive passes, progressive carries, pressures, and miscontrols do not appear in the stat type registry.

---

## Decision
ACCEPTED — V1 built entirely on StatsBomb Open Data.

StatsBomb Open Data is the sole data source for ElevenIQ V1.  A paid provider
(Sportmonks) is the planned V2 extension, not part of V1.

**Rationale:**
- Richest freely available event-level football data; the only open source covering
  all seven DNA dimensions from first principles.
- StatsBomb xG is a well-regarded proprietary model available at no cost in this
  dataset; no other free source offers per-shot xG.
- License is permissive enough for a public non-commercial portfolio project.
- Ingestion pipeline is working: La Liga 2015/16 (380 matches, 10,550 player-match
  rows) validated end-to-end in Milestone 3.
- League coverage (Big Five + MLS + international tournaments) is sufficient for
  an MVP demonstration; the ≥10-leagues target is deferred to V2 via a paid API.

**V2 extension (future):** A paid provider (likely Sportmonks; see Paid API
comparison table above) will be added as a second source to extend league
coverage.  Percentiles, similarity, and rarity are computed within a single
source only — cross-source comparisons are out of scope until a normalisation
strategy is designed and validated.

---

## Known limitations

- Only Big Five domestic leagues plus select competitions are in the open set; no
  Danish Superliga, Dutch Eredivisie, Primeira Liga, or other development/scouting
  leagues in V1.  The ≥10-leagues brief requirement is not met by V1 alone.
- Most leagues have 1–3 seasons of open data; only La Liga has deep multi-season
  coverage (1973/74 through 2020/21). [UNVERIFIED — full/partial season list not
  yet checked against the open-data repo]
- Pirlo: no records in open data (Serie A 1986/87 predates his career at
  Brescia/Inter; no other Serie A seasons in the open set cover his career).
  [UNVERIFIED — no player-level query run against parquet]
- Gerrard: only Premier League 2003/04 and 2015/16 available; the bulk of his
  career (1997–2015) is absent. [UNVERIFIED — PL season list and player presence
  not checked against parquet]
- Xavi: La Liga coverage spans 1973/74–2020/21 which includes his career, but
  only select seasons are in the open set; full coverage not guaranteed.
  [UNVERIFIED — which La Liga seasons are actually in the open-data repo not
  confirmed; player presence not checked against parquet]

## Compromises accepted

- The brief's ≥10 leagues criterion is not met in V1.  Accepted because the
  project's primary purpose is to demonstrate methodology, not to scout across
  every league.  League breadth is a V2 concern.
- StatsBomb's non-commercial license sits in a grey area for a portfolio project
  that is publicly shared to attract employers.  Accepted: StatsBomb explicitly
  encourages publishing work using their data on social media; a non-monetised
  portfolio demonstration is consistent with that intent.
- A single source means no cross-league-strength adjustment is possible in V1.
  Accepted: the ADR 002 rule (percentiles/similarity/rarity within a single source
  only) documents and enforces this boundary.

## Historical/retired player coverage

Pirlo: absent from open data (wrong era; no Serie A season in open data covers his
career). [UNVERIFIED]  Xavi: probably present in La Liga data for his Barça years
(1998–2015), but which seasons are actually in the open set has not been confirmed.
[UNVERIFIED]  Gerrard: partially present (PL 2003/04 and 2015/16). [UNVERIFIED]
Full Historical Legends Mode is a V2 stretch feature dependent on finding
event-level data that covers the relevant seasons.
