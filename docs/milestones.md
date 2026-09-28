# ElevenIQ — Milestones

## Milestone 1 — Data Feasibility Spike  ← START HERE

**Goal:** Identify one free/open dataset that is good enough to build the MVP on.
Do NOT write any application code during this milestone.

### What to evaluate
Assess each candidate against the criteria in brief.md §9:
- Number of leagues (target: 10–20 with consistent metrics)
- Non-Big-Five coverage (required)
- Player count (target: thousands)
- Metrics available (target: 20+ useful per-player metrics)
- Minutes played, position, team, competition, season (all required)
- Age / DOB (preferred)
- Historical seasons (preferred: 2–5)
- Data freshness (latest completed season available?)
- Licensing / allowed usage for a public portfolio project
- Missing-value rate per league and metric
- Cross-league metric consistency (same definition everywhere?)
- Stable player / team identifiers
- **Historical / retired players:** which legendary players (e.g. Pirlo, Xavi, Gerrard)
  have full-season league data in the source, using the same metric definitions as current
  seasons? List concrete examples found. Note: tournament-only or cup-only appearances
  with very few matches must NOT be treated as equivalent to a full league season.

### Candidate sources to examine
- StatsBomb Open Data (GitHub) — event-level, requires aggregation; check league and
  season coverage carefully
- FBref-derived datasets only where the license explicitly allows reuse in a public
  portfolio project
- Other open football datasets with clearly stated license and source

### Steps
1. Review documentation and league/season/player counts for each candidate.
2. Download a sample of the top 1–2 candidates. Profile the sample: rows per league,
   completeness rate per metric, position coding, player/team identifier format.
   The decision must be based on measured evidence, not only documentation claims.
3. Evaluate against acceptance criteria below.
4. Write docs/decisions/001-data-source.md.

### Acceptance criteria (all must pass)
1. At least one source provides ≥10 leagues including ≥3 outside the Big Five.
2. For all covered leagues: minutes, position, team, competition, and season are present.
   Plus at least 15 per-player metrics that together cover at least 5 of the brief's
   7 candidate DNA dimensions (Passing, Progression, Creativity/Creation, Ball Carrying,
   Defending, Goal Threat, Ball Security).
3. License explicitly permits use in a public portfolio project (no ToS breach).
4. Missing-value rate for core metrics is <15% across covered leagues.
5. At least one full completed league season available (current or most recent).

### Deliverable
`docs/decisions/001-data-source.md` documenting:
- Candidates evaluated (with evidence — row counts, league list, metric list, missing-
  value rates from actual sample profiling)
- Decision: chosen source (or "no viable source found + next steps")
- Known limitations and how the design accommodates them
- Any compromises accepted and why
- Examples of historical/retired players found (for future stretch feature assessment)

---

## Milestone 2 — Data Model & Pipeline Design  (outline)
Design the database schema (players, teams, competitions, seasons,
player_season_stats, player_percentiles, player_dna, player_rarity) and the full pipeline
architecture (raw → Parquet → Polars → PostgreSQL) before writing any code.

**Open question to resolve in this milestone:**
What does "global position percentile" mean in a system with no league-strength
adjustment? How should it be labelled in the UI so it does not mislead users?

Deliverables: docs/decisions/002-data-model.md + docs/decisions/003-pipeline-design.md.

---

## Milestone 3 — Project Skeleton + Ingestion & Validation Pipeline  (outline)
**Start with tooling setup:** project skeleton, Docker Compose environment, fill in the
Commands section of CLAUDE.md (Makefile targets / run scripts / test commands).
Then implement raw → Parquet → validated Polars pipeline for the chosen dataset.
Schema validation, cleaning, entity normalisation. Tests at each stage.

**Carry-over from Milestone 1 spike:**
- Run the dismissal check (`check_dismissed_player_minutes`) on La Liga 2015/16
  and record the result in ADR 002 (was the `to` field reliably set for dismissed
  players, or did we find mismatches?).
- Un-skip `tests/test_positions.py::test_no_unknown_positions_in_pipeline_output`
  once the pipeline has run and `data/interim/player_match_stats.parquet` exists.

**Tech debt (deferred from Milestone 1 code review):**
- Add timeout to `urllib.request.urlretrieve` in `downloader.py` — currently hangs
  indefinitely on a slow or stalled connection.
- Add mid-loop error recovery and per-match retry in `downloader.py` — a single
  bad match file aborts the entire competition-season download.
- Extract shared `_DATA_ROOT` constant to `src/eleveniq/config.py` — currently
  duplicated in `downloader.py` and `loader.py`.
- Guard `stint.get("to")` against empty string `""` in `minutes.py` and
  `positions.py` — the current code treats `""` as a truthy to-value and calls
  `parse_mm_ss("")`, which will raise a `ValueError`.
- Replace `test_aggregation.py` inline expressions with tests against the actual
  `build_player_season.py` module once it exists (currently tests Polars logic
  in isolation with no reference to the real pipeline code).
- Add a cross-consistency test confirming that `sum(compute_minutes_per_group(...).values())`
  equals `compute_player_minutes(...)` for the same positions array and match duration.
- If the real pipeline run finds dismissal events with missing `minute`/`second`,
  derive the timestamp from the StatsBomb `timestamp` field or neighbouring events
  in the same period, and mark it as inferred in the quality report.

**Tech debt (deferred from Milestone 3 code and stats review):**
- `key_passes` is not gated by pass completion (`build_player_match.py`): a pass
  with `shot_assist=True` or `goal_assist=True` is counted as a key pass even if
  the pass has a non-None outcome (i.e. was incomplete). Zero occurrences in La Liga
  2015/16 StatsBomb data, but a latent defect if a future source records assist flags
  on incomplete passes. Documented as current behaviour in `test_aggregation.py`.
- Enforce the minimum-minutes gate in `build_player_season.py` — 2,055 player-match
  rows have `0 < minutes_played < 45`, producing extreme per-90 values (e.g. xg_p90
  up to 9.93). ADR 002 §5 documents that this gate belongs at the percentile stage;
  `build_player_season.py` must not omit it.
- `_iter_stint_contributions` in `positions.py` is the single source of truth for
  minute/position consistency but has no docstring example for the dismissal-clamping
  case. Add a brief inline example showing `dismissal_minute` clamping a stint.
- `started_inferred` fallback mutates row dicts in-place after they are stored in
  `team_entries`. Correct but fragile: if row dicts are ever replaced with dataclasses
  or copied before the second pass, the mutation is silently lost. Consider building
  the final row after the fallback pass instead.
- `_FIXTURE_ROOT_9998` in `tests/test_build_player_match.py` is an alias for
  `_FIXTURE_ROOT`; use the existing constant directly.
- Investigate the 2,465 passes with `outcome="Unknown"` in La Liga 2015/16 (observed
  in the La Liga 2015/16 pipeline run, commit `10751a0`):
  (1) profile their characteristics (length, pass type, pitch zone) — are they
  random noise or concentrated in a specific pattern?
  (2) design an inference rule (e.g. `pass.recipient` present + next `Ball Receipt`
  event by a teammate in the same sequence → treat as completed);
  (3) measure the rule's accuracy on passes WITH known outcomes (must reach ≥95%
  before applying);
  (4) if accuracy ≥95%, add `passes_completed_inferred` column — keep
  `passes_completed` raw and untouched.
  Until then, unknown-outcome passes are excluded from the completion denominator
  (`passes_attempted − passes_unknown_outcome`) and `passes_completed` is accurate.
- `test_incomplete_pass_not_counted_as_key_pass` in `tests/test_aggregation.py`:
  name contradicts its assertion (`assert result["key_passes"] == 1`). Rename to
  `test_key_pass_counted_regardless_of_pass_completion` or similar.
- `test_known_pass_denominator_excludes_unknown`: final arithmetic assertion
  (`known_attempts == 3`) is redundant — the two direct assertions on lines 810–812
  already cover it. Remove the derived assertion.
- `_pass_event` helper: `completed` is silently ignored when `outcome` is explicitly
  set. Either rename `completed` to `_completed` to signal it is secondary, or add a
  test asserting `completed=False` has no effect when `outcome` is given.
- Measure how much `compute_match_duration` undercounts the real final whistle
  (ADR 002 §3 estimates 15–60 seconds per match; it records the last event timestamp,
  not the whistle). Quantify whether the cumulative undercount across a season moves
  any player across the 900-minute `meets_min_minutes` threshold, and record the
  result in ADR 002 §3.
- `cross_completion_pct` denominator (`crosses_attempted`) is not cleaned of
  unknown-outcome crosses, unlike `pass_completion_pct` (ADR 002 §14). Effect is
  likely small but asymmetric. Before finalising DNA metrics in M4, count
  unknown-outcome crosses in La Liga 2015/16; if non-zero, apply the same
  exclusion rule as passes.
- `_season_position_group` alphabetical tie-break means GK loses to AM-Winger on
  an exact equal-minutes tie → `is_gk = False` for a goalkeeper. Document the
  known edge case in a test comment; no change needed until GK scouting is in scope.
- Move `DISMISSAL_TOLERANCE_MINUTES` (quality_report.py) and the `_PROJECT_ROOT /
  parents[3]` anchor (build_player_match.py and build_player_season.py) into
  `src/eleveniq/config.py` so all project-wide constants have a single source of
  truth.
- Add a `logger.info(...)` line at the end of `aggregate_player_season()` reporting
  how many rows were produced and how many multi-team stints were found, so callers
  that invoke the pure function directly have observability. (`build_player_season()`
  already logs this; the pure function does not yet.)
- **Set-piece crosses audit:** using `pass.type.name` (corner kick, free-kick delivery),
  count how many `crosses_attempted` events in La Liga 2015/16 come from set pieces
  vs open play. Decide whether `crosses_attempted_p90` and `cross_completion_pct`
  should be split into open-play and set-piece variants, or kept combined with a note.
  Record the decision in ADR 002 §15. Part of the M4 metric coverage audit.
- **UNKNOWN position group exclusion:** players whose season `position_group` is
  `UNKNOWN` must be excluded from DNA / similarity / Gem Finder cohorts, same as GK
  (ADR 002 §12). Currently only `is_gk` is used as a cohort gate; add `is_unknown`
  flag or extend the exclusion logic before M4 percentile computation reads the Parquet.
- **`_season_position_group` default for all-zero minute rows:** when every per-group
  minute column is 0, the function returns `"AM-Winger"` (alphabetically first group)
  instead of `"UNKNOWN"`. In practice `build_player_match.py` excludes 0-minute rows
  before writing the Parquet, so real data cannot trigger this. Fix the initialiser to
  `pl.lit("UNKNOWN")` before M4 so the function is correct by construction.
- **`test_config.py`: remove type/naming tests:** `test_is_integer_type`,
  `test_is_positive`, and `test_attribute_name_is_exported` violate the qa-tester rule
  "don't write tests for formatting, types, naming, or constants' spelling". Remove
  them; `test_default_value_is_900` and `test_module_is_importable` are sufficient.
- **Untested branches in `build_player_match.py` (coverage gap, not exercised by unit tests):**
  - **L423** `cause = None` — `else` branch of `if overcounted:` in the dismissal-quality
    block. Reached when a dismissed player's minutes were *not* overcounted (dismissal
    recorded correctly in the source data). Needs a unit test for `_process_match` with
    a dismissed player whose lineup `to` field is correctly set.
  - **L441** `dismissal_report.append({**quality_issue})` — `check_dismissal_event_quality`
    returned a non-None result for a non-dismissed player, meaning a dismissal event
    exists in the feed but has missing `minute`/`second` timestamp fields. Needs a
    fixture with such an event.
  - **L501–502** `elif starter_count > 11: dismissal_report.append(...)` — branch for a
    team with more than 11 players counted as starters. Does not occur in La Liga
    2015/16. Needs a synthetic fixture with 12+ starter-flagged lineup entries.
  - **L550–552** `except Exception: logger.error(...); errors.append(...)` — per-match
    error handler in `build_competition_season`. Needs a test that injects a bad match
    file (or patches `_process_match` to raise) and asserts the failed match id is
    returned in `failed_ids` while other matches still succeed.

**Multi-league expansion (all 2015/16 leagues, chunks C1–C3):** see [docs/plans/003-multi-league.md](plans/003-multi-league.md).

---

## Milestone 4 — Feature Engineering & Player DNA  (outline)
Per-90 calculation, position-group percentiles, DNA dimension aggregation.
Validate methodology against the questions in brief.md §20 before moving on.

**Note for DNA feature selection (from La Liga 2015/16 data):**
Crosses account for 0.7–9.1% of progressive passes by position group (CB 0.7%,
DM-CM 2.4%, FB-WB 5.9%, ST 7.0%, AM-Winger 9.1%). Progressive passes are not
dominated by crosses; they measure different aspects of play. Crosses are stored
separately (`crosses_attempted`, `crosses_p90`, `cross_completion_pct`) and
should be treated as an independent style metric in DNA profiles.

**Open question to resolve before implementing percentiles:**
Multi-team season ranking: `player_season_stats.parquet` stores one row per
(player, team, season) with `is_primary_stint` and
`player_season_minutes_total`. Before computing percentiles, decide: should
split-season players be ranked using (a) their per-stint `minutes_played` only,
(b) their `player_season_minutes_total`, or (c) excluded from rankings unless
the primary stint meets the minimum threshold on its own? Record the decision
in ADR 002 §6 or a new ADR.

**Pre-condition from Plan 003 C1 audit (cross-league event rates) — stats-reviewer must decide before computing global percentiles:**

- Event-type rates differ materially between the 2015/16 leagues (`spikes/profile_2015_16_leagues.py`):
  - Interceptions per 1,000 events: La Liga 9.55 vs 5.73–6.79 in the other four.
  - 50/50s per 1,000 events: 0.31–0.43 (La Liga, Premier League) vs 1.39–1.57 (Serie A, Ligue 1, Bundesliga).
  - Pressures per 1,000 events: 88–107.

  Whether this reflects playing style or differences in how StatsBomb collected the data is unknown. League-local percentiles are unaffected. Global percentiles on defensive metrics (interceptions, 50/50s, pressures, ball recoveries) may be distorted. The stats-reviewer must decide whether these metrics get global percentiles at all, a caveat, or league-local only.

**Pre-conditions from chunk B reviews — must be done before computing percentiles:**

- **xg_assisted_direct "no-receipt" fallback not validated:** 6 assisted shots in La Liga 2015/16 had no preceding Ball Receipt* and were counted as direct. Verify that those 6 events are genuine first-touch shots and not data gaps before using xg_assisted_direct in percentile rankings.
- **"Missing shooter identity" fallback direction is questionable:** ADR 002 §26 credits the passer as direct when the shooter cannot be identified. For scouting, the more statistically honest fallback is xg_assisted only (not direct), since nothing can be inferred about the carry window. No occurrences in La Liga 2015/16, but document or reconsider the fallback direction before expanding to other datasets.
- **xg_assisted_direct threshold is arbitrary; check collinearity:** The 10-unit (~8.75 m) threshold for "direct" is a design choice with no external reference. La Liga 2015/16 shows 84.3% of assisted xG is classified as direct, making xg_assisted_direct_p90 likely near-collinear with xg_assisted_p90. Run a correlation test at M4 and decide whether both columns are needed in the DNA feature set.
- ~~**Entry functions do not enforce the completed-pass guard internally**~~ **Resolved in Chunk B:** `is_final_third_entry_pass` and `is_box_entry_pass` now require `outcome` as a keyword argument (same contract as `is_progressive_pass`); omitting it raises `TypeError`.
- **box_entries_pass / progressive_passes overlap not quantified per position group:** The box-entry pass definition is a near-subset of progressive passes. Before M4 feature selection, measure the overlap fraction by position group (expected to be near 100% for FB-WB, AM-Winger, ST) to assess whether both features carry independent signal.
- **Throw-ins included in final_third_entries_pass and box_entries_pass:** Throw-ins are not in SET_PIECE_TYPES and are counted as entries (~4% of pre-exclusion box totals per ADR 002 §27). A right back who takes throw-ins near the attacking third will accumulate entry counts that do not reflect progressive passing skill. Quantify throw-in entries by position group and decide whether to add "Throw-in" to SET_PIECE_TYPES before M4.
- **GK carry-entry per-90 columns are present but meaningless:** box_entries_carry_p90 and final_third_entries_carry_p90 are non-null for GKs who meet the minute threshold. The is_gk gate in DNA/similarity excludes them from cohorts, but analysts reading the Parquet directly may not know this. Add a note to the column schema or the M4 cohort-filter logic that these four columns must be excluded from GK rows.
- **Extreme per-90 xGA values for low-minute players are not validated for the new metric:** xg_assisted_p90 for a player with 10 minutes and one high-xG key pass can be ≥8. The minimum-minutes gate in ADR 002 §5 should eliminate these from rankings, but the distribution of xg_assisted_p90 below the threshold has not been inspected. Verify at M4 that no sub-threshold rows with extreme xg_assisted_p90 survive the meets_min_minutes filter.

**Pre-conditions from chunk A reviews — must be done before computing percentiles:**

- `long_passes_attempted` includes passes with outcome "Unknown"; the same
  denominator-correction rule used for `pass_completion_pct` must be applied
  here. Add `long_passes_unknown_outcome` as a display-only column so the
  denominator imbalance can be diagnosed, and update `long_pass_completion_pct`
  to exclude unknown outcomes before use in DNA.
- Position-biased metrics (`dispossessed_p90`, `miscontrols_p90`,
  `switches_p90`) and team-style-biased metrics (`ball_recoveries_p90`,
  `pressures_p90`) are currently indistinguishable from well-behaved per-90s
  in the schema. Encode the bias in code (e.g. a metric registry with a
  `position_biased` / `team_style_biased` flag) so the DNA stage cannot
  accidentally use them — an ADR text warning is not sufficient.
- Percentile computation must filter to `meets_min_minutes == True` and
  `is_gk == False` (for outfield cohorts) in exactly one place; do not
  scatter this filter across individual queries.

---

## Milestone 5 — Similarity Engine  (outline)
Cosine similarity baseline over DNA features with weighted dimensions.
Implement explainability: **strongest similarities and biggest differences** between any
two players must be derivable from the feature vectors, not from the LLM.
Validate that neighbours are intuitively reasonable; document findings.

---

## Milestone 6 — Gem / Rarity Engine  (outline)
Evaluate ≥2 rarity methods (percentile rarity, rare-combination counting,
LOF / KNN distance). Choose one with documented rationale.
Explainability: surface concrete counts ("7 of 483 comparable midfielders rank above P85
in both progression and defending").

---

## Milestone 7 — API Layer  (outline)
FastAPI endpoints for player search, DNA, similarity, gems, and **player comparison**
(2–3 players side-by-side across DNA dimensions and underlying metrics).
No LLM yet; pure deterministic queries.

---

## Milestone 8 — Natural-Language Scout Interface  (outline)
LLM query-parsing layer (intent → structured params → backend).
Strict grounding: LLM never touches statistics directly.

---

## Milestone 9 — Frontend MVP  (outline)
React/Next.js: Discover, Player Profile, Compare, Gems screens.
Explainability UI for similarity and gem findings.

---

## Future / Stretch Features

### V2: Paid Provider Integration
Add a second data source (likely Sportmonks Football API) to extend league coverage
beyond the Big Five and expand historical depth.

**Motivation:** StatsBomb Open Data covers only Big Five domestic leagues with 1–3
seasons each in the open set.  The brief's ≥10-leagues criterion and development/
scouting league coverage require a paid API.

**Key constraints before implementation:**
- Canonical Parquet schema must remain the contract between ingestion and analytics.
  Add a `source` column (already in `player_match_stats.parquet` since M3) to tag
  every row with its origin (`"statsbomb"`, `"sportmonks"`, …).
- Percentiles, similarity, and rarity must be computed within a single source only
  until a cross-source normalisation strategy is designed and validated.  This rule
  is recorded in CLAUDE.md and ADR 002.
- A new ADR is required before writing any Sportmonks ingestion code, documenting
  which Sportmonks metrics map to which StatsBomb-derived columns and how gaps are
  handled.

**Candidate:** Sportmonks Growth plan (€99/mo) + xG add-on (€29/mo).  See ADR 001
Paid API comparison table for metric-by-metric coverage analysis.

---

### Historical Legends Mode
Allow a retired player's past full league season (e.g. Pirlo 2011/12 Serie A) to be used
as a reference player for similarity search — "find current players similar to Pirlo's
2011/12 profile, but more defensive."

**Constraints:**
- Only full league seasons with the same metric definitions as current seasons are eligible.
  Tournament-only or cup-only samples with few matches are excluded.
- The feature depends entirely on M1 finding a source with consistent historical data for
  known players.

**Open question for M2:** How should a historical season be compared against the current
player population? Should the historical season be re-ranked within the current population
(which ignores era differences), or should it be ranked within its own season's population
and the DNA vectors compared directly? This must be decided before implementation.
