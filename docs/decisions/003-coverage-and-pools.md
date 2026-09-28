# ADR 003 — Competition-Season Coverage and Percentile-Pool Eligibility

**Date:** 2026-09-28
**Status:** Draft (Plan 003; C1, with §5 implementation and §7 added in C3)
**Context:** The pipeline is expanding from La Liga 2015/16 to all 2015/16 domestic leagues in StatsBomb Open Data (see `docs/plans/003-multi-league.md`). Some of these competition-seasons are not fully covered in the open set. A partial season is a biased sample: only one team is seen every week, and every opponent is seen once or twice. Its players must not define or distort percentile pools.

---

## 1. Scope

In scope:
- the rule that classifies a competition-season as **FULL** or **PARTIAL**;
- the classification of the 2015/16 leagues;
- the `in_percentile_pool` flag that carries this classification into the data.

Out of scope, deferred to **Milestone 4**:
- percentile-cohort design (league-local vs global cohorts, cohort keys);
- ranking players who played in more than one competition in the same season;
- any league-strength adjustment.

---

## 2. Classification rule

A competition-season is **FULL** when both of the following hold:

1. **Fixture coverage ≥ 95%.** `available_matches / expected_matches ≥ 0.95`, where
   `expected_matches = n_teams × (n_teams − 1)` for a double round-robin league.
2. **Every team appears.** Every team in the competition appears in at least one available match.

Otherwise it is **PARTIAL**.

Notes:
- `n_teams` is the known league size, `expected_teams`, recorded in config next to the
  competition-season entry (2015/16: 20, and 18 for the Bundesliga). It is **not** the number
  of teams seen in the file, because a partial file could hide teams entirely.
- The file is checked against it. Condition 2 means all `expected_teams` teams appear in
  `matches/{comp}/{season}.json`. More distinct teams in the file than `expected_teams` is a
  config error and fails the build. `expected_teams` must be ≥ 2.
- The 95% threshold allows a handful of missing fixtures. Each team then loses at most about
  one match in 38, which does not bias a season profile materially. Anything below the
  threshold is treated as a selective sample.
- The rule applies to league formats only. Cup and tournament competitions (e.g. Champions
  League 2015/16, 1 match) are not ingested for percentile use under this ADR.

---

## 3. Classification — 2015/16 (season_id = 27, source = statsbomb)

| League | comp_id | Teams | Expected | Available | Coverage | All teams appear | Class |
|---|---|---|---|---|---|---|---|
| La Liga | 11 | 20 | 380 | 380 | 100.0% | yes | **FULL** |
| Premier League | 2 | 20 | 380 | 380 | 100.0% | yes | **FULL** |
| Serie A | 12 | 20 | 380 | 380 | 100.0% | yes | **FULL** |
| Ligue 1 | 7 | 20 | 380 | 377 | 99.2% | yes | **FULL** |
| 1. Bundesliga | 9 | 18 | 306 | 34 | 11.1% | yes¹ | **PARTIAL** |

¹ All 18 teams appear, but only because the set is Bayer Leverkusen's 34 league matches. Every
other team appears exactly twice, against Leverkusen. Condition 2 holds, but condition 1 fails
by a wide margin.

Counts come from `data/raw/statsbomb/matches/{comp}/27.json` (downloaded 2026-09-28). All events
and lineups files are present and parse as JSON for every listed match.

---

## 4. Ligue 1 2015/16 — missing fixtures

Three of the 380 fixtures are missing from the open data:

| Match week | Home | Away |
|---|---|---|
| 14 | Bastia | Gazélec Ajaccio |
| 23 | Saint-Étienne | Paris Saint-Germain |
| 36 | Troyes | Bordeaux |

They were found by comparing all 380 home/away pairings of the 20 teams against the file. Match
weeks 14, 23 and 36 each hold 9 matches instead of 10, and in each of those weeks the two teams
of the missing pairing are the only ones without a match.

Consequences:
- The 6 affected teams (Bastia, Gazélec Ajaccio, Saint-Étienne, Paris Saint-Germain, Troyes,
  Bordeaux) each have 37 of 38 matches. At most one match of minutes is missing per player of
  those teams.
- Season totals for Ligue 1 cannot be compared one-to-one with official league totals such as
  total goals. Reference figures (Plan 003 §3) are reconciled only against the 377 available
  matches.
- No imputation is done. Players are profiled on the matches that exist.

---

## 5. The `in_percentile_pool` flag

- **Type / location:** a boolean column on `player_season_stats.parquet`.
- **Derivation:** the value is derived from the competition-season's coverage class, which is
  configured per entry in `COMPETITION_SEASONS` (`coverage: "FULL" | "PARTIAL"`). It is
  `True` if and only if the row's `(competition_id, season_id, source)` is classified FULL.
  It is constant within a competition-season.
- **Meaning:** "this row may be part of a percentile, similarity or rarity reference
  population". The flag says nothing about the player's own minutes, position or other
  qualities.
- **Independence from other gates:** the flag is deliberately separate from
  `meets_min_minutes` (ADR 002 §5), `is_gk` and the UNKNOWN position exclusion. M4 combines
  these gates in one place. Individually, none of them is sufficient.
- **Rows are still produced for PARTIAL seasons.** Leverkusen 2015/16 players get full
  player-season rows and can be looked up. Whether and how a non-pool row is ranked *against*
  a pool (e.g. showing a percentile relative to the FULL pools) is an M4 decision.
- **Config is the single source of truth.** Coverage class is recorded in config, not
  recomputed at pipeline time. A data-quality check compares the configured class with the §2
  rule applied to the downloaded `matches.json` and raises `CoverageCheckError` on a mismatch,
  failing the build rather than silently changing pool membership.

### Implementation (plan 003 C3)
- `CompetitionSeason.source` is a **required** config field (no default), so an entry can
  never silently inherit StatsBomb provenance. The tag value is one constant,
  `config.SOURCE_STATSBOMB`, used both where rows are written and where checks filter.
  `validate_competition_seasons` rejects duplicate `(competition_id, season_id, source)` keys,
  which would fan out the join, and `expected_teams < 2`.
- `aggregate_player_season` left-joins the configured classes on
  `(competition_id, season_id, source)`. A row whose key is not configured raises
  `ValueError`. There is no default and no `fill_null`. Because config keys are unique, the
  left join cannot add rows.
- `build_player_season` runs three checks (`eleveniq.pipeline.coverage`) before writing,
  and each one fails the build:
  1. **Configured class vs §2 rule.** `classify_coverage` uses n = `expected_teams` from
     config, not the teams seen in the file. FULL requires
     available / (n·(n−1)) ≥ `FULL_COVERAGE_THRESHOLD` and all n teams present. More teams
     in the file than `expected_teams` raises. Fewer means PARTIAL, so a file that hides
     teams cannot pass as FULL.
  2. **Processed vs available matches.** The *set* of `match_id`s per competition-season
     in `player_match_stats` must equal the set in `matches.json`. The error lists missing
     and extra ids, so a pool is never built on dropped or mislabelled matches (a count
     comparison would miss one of each). 2015/16: 380 / 377 / 34 / 380 / 380, sets equal.
  3. **Season key.** Across the configured competition-seasons, StatsBomb `season_id` ↔
     `season_name` must be one-to-one (`competitions.json`). Unconfigured competitions are
     ignored, so an unrelated StatsBomb entry cannot block the build. A configured
     competition-season missing from `competitions.json` fails. See §7.
- Limitation: for a competition-season configured PARTIAL, a wrong `expected_teams` that
  is still ≥ the teams seen is not detected, because the derived class stays PARTIAL.
- 2015/16 result: 2,279 pool rows (La Liga 546, PL 561, Serie A 584, Ligue 1 588) and
  349 Bundesliga rows with `in_percentile_pool = False`.

---

## 6. Consequences

- Four FULL 2015/16 leagues (1,517 matches) are eligible for pools. The Bundesliga contributes
  no pool rows.
- Adding a future competition-season requires a coverage class in config. The data-quality
  check validates it.
- If StatsBomb later publishes the full 2015/16 Bundesliga, only the config entry changes. The
  §2 check confirms the reclassification.

---

## 7. The `multi_competition_season` flag

- **Type / location:** a boolean column on `player_season_stats.parquet`.
- **Derivation:** `True` iff the player's rows cover more than one distinct
  `competition_id` within the same `(player_id, season_id, source)`. Rows stay separate,
  one per player, team, competition and season.
- **Season key.** Plan 003 §6 proposed keying on `season_name`, which is not a parquet
  column. StatsBomb `season_id` is shared across competitions (27 = "2015/2016" in comps
  2, 7, 9, 11, 12 and 16), and `season_id` ↔ `season_name` is one-to-one across all of
  `competitions.json`. The key is therefore `season_id`. The build raises `CoverageCheckError`
  unless the mapping is one-to-one for the configured competition-seasons (§5 check 3).
- **Calendar-year vs split-year seasons.** A calendar-year season (e.g. "2016") and a
  split-year one ("2015/2016") never share a key, so moves between those formats are not
  detected. This matters for a future Danish or Dutch source (new ADR required).
- **PARTIAL competitions count.** The flag describes the player's season, not pool
  eligibility. Two caveats apply:
  - *Observed-only minutes.* In a PARTIAL competition, minutes cover observed matches
    only (Bundesliga 2015/16: Leverkusen's matches). A sum across competitions is not the
    player's true season total.
  - *Lower bound.* `False` means "not seen in another ingested league competition". It
    does not mean the player stayed at one club. Moves to or from non-ingested leagues and
    cups are invisible.
- **Within-competition columns are unchanged.** `multi_team_season`, `is_primary_stint`
  and `player_season_minutes_total` stay per competition (ADR 002 §29).
- **Data-quality check.** A `team_id` must appear in only one competition per
  `(season_id, source)`; the build fails otherwise. Every configured competition is a
  league (§2). Once cups are ingested, this check must be scoped to leagues.
- **2015/16 result:** 47 player_ids, each in exactly 2 competitions (94 rows). 9 of them
  involve the PARTIAL Bundesliga. None is also `multi_team_season`.
- How these players are ranked or combined is deferred to M4 (§1; ADR 002 §29 lists the
  known double-count and minutes-gate cases).
