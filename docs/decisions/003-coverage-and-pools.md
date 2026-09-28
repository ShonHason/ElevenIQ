# ADR 003 — Competition-Season Coverage and Percentile-Pool Eligibility

**Date:** 2026-09-28
**Status:** Draft (Plan 003, chunk C1)
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
- `n_teams` is the number of distinct teams in `matches/{comp}/{season}.json`.
- A partial file could hide teams entirely. Where that is possible, `n_teams` must be checked
  against the known league size, which is recorded in config next to the competition-season
  entry. For 2015/16 the known sizes are 20, and 18 for the Bundesliga.
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
  recomputed at pipeline time. A data-quality check asserts that the configured class agrees
  with the §2 rule applied to the downloaded `matches.json`. A mismatch fails the build rather
  than silently changing pool membership.

---

## 6. Consequences

- Four FULL 2015/16 leagues (1,517 matches) are eligible for pools. The Bundesliga contributes
  no pool rows.
- Adding a future competition-season requires a coverage class in config. The data-quality
  check validates it.
- If StatsBomb later publishes the full 2015/16 Bundesliga, only the config entry changes. The
  §2 check confirms the reclassification.
