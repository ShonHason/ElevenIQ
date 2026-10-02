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
- ~~Replace `test_aggregation.py` inline expressions with tests against the actual
  `build_player_season.py` module once it exists~~ — resolved in Plan 004 H3b: the
  inline classes were removed; their contracts are tested against
  `aggregate_player_season` in `test_build_player_season.py`.
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
  - ~~per-match error handler in `build_competition_season`~~ — resolved in Plan 003 C2:
    a failed match now fails the whole league (`MatchProcessingError`, no part written),
    tested by `test_build_competition_season_failed_match_fails_league`.
- `_process_match` returns its quality entries as `dismissal_report`, but the list also
  carries fallback, starter-count and Starting XI cross-check entries (Plan 003 C2).
  Rename it (e.g. `quality_report`) along with its docstring and callers.
- **Plan 003 C3 review leftovers (tests/docs only):**
  - `tests/test_coverage.py::TestCheckProcessedMatchIds::test_equal_counts_pass` still
    has its count-era name; rename it to say equal match_id sets.
  - `check_processed_match_ids` has no test showing that rows from another `source`
    are ignored by the `SOURCE_STATSBOMB` filter.
  - ADR 003 §5 check 2 lists "380 / 377 / 34 / 380 / 380" without league labels, in
    competition_id order rather than config order. Label them.

**Multi-league expansion (all 2015/16 leagues, chunks C1–C3):** see [docs/plans/003-multi-league.md](plans/003-multi-league.md).

---

## Milestone 4 — Feature Engineering & Player DNA  (outline)
Per-90 calculation, position-group percentiles, DNA dimension aggregation.
Validate methodology against the questions in brief.md §20 before moving on.

**Status (2026-10-02):** plan 005. The M4a decisions are recorded in ADR 004 (docs/decisions/004-cohorts-percentiles-dna.md). Each pre-condition below is decided there, or is deferred to the new-source checklist (ADR 004 D22). The text below is kept as history.

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

**Pre-condition from Plan 004 H3b (first-half stoppage minutes) — CLOSED 2026-10-02:** decided and implemented as period-aware minutes, ADR 002 §31 (before/after in §31.7). The measurement below is kept as history; it describes the pre-H4 pipeline.

- **Minutes are on the match-absolute axis, so first-half stoppage is lost.** Lineup times are match-absolute and period 2 restarts at 45:00, so a player on the pitch at the end of the first half is not credited its stoppage time (ADR 002 §3, "Known limits"). This is systematic, not limited to the 9 reversed-period stints. Measured on the H3b output against a period-aware clock: same stints, same exit caps, each period ending at its last event (the `match_duration` convention), measurement only. The pipeline is unchanged. Every figure below comes from `spikes/measure_first_half_stoppage.py`.
  - **Per league** (net minutes lost / % of league minutes; median first-half stoppage per match). Net means it includes the −120.6 min from the 30 early-half-end matches below; the gross loss is 44,889 min:

    | League 2015/16 | Minutes lost | % of minutes | Median stoppage |
    |---|---|---|---|
    | La Liga | 6,892 | 0.89% | 0.98 |
    | Premier League | 17,100 | 2.17% | 2.02 |
    | Serie A | 10,283 | 1.31% | 1.03 |
    | Ligue 1 | 9,877 | 1.28% | 1.05 |
    | Bundesliga (PARTIAL) | 617 | 0.89% | 0.92 |
    | **Total** | **44,769** | **1.40%** | |

  - **Per player-match.** Full-match players (24,854): mean 1.32 min lost, median 1.07, max 9.25. In 99.996% of them the loss equals the match's first-half stoppage exactly. By league, the full-match mean ranges from 0.82 (La Liga) to 2.04 (PL). Second-half substitutes (8,529) lose 0.00 (max |diff| 3e-14). Other player-matches (subs before half-time, players off in the second half; 9,661): mean 1.25, median 1.05.
  - **Edge cases.** In 30 matches the first half's last event is before 45:00 (by up to 1.2 min). There the period-aware clock credits *less*, so 659 player-matches show −120.6 min in total. This comes from the last-event convention, not from real time. **H4 must define the half-end rule explicitly, for both halves**, before any period-aware minutes are built:
    - which instant ends a period: the `Half End` event or the last event of the period;
    - what to do when that instant is before the period's nominal end (45:00 for period 1, 90:00 for period 2). For example: clamp, keep the recorded time, or fail the build.
    - The same last-event convention gives `match_duration`, which ends every open stint. So second-half and full-match minutes may also be understated wherever the last event comes before the whistle. Compare `Half End` against the last event for period 2 as well as period 1.
    - Confirm the largest first-half stoppages against the raw `Half End` events (or an injury stoppage) before trusting them: PL 3754339 (9.25 min) and Ligue 1 3829452 (9.03 min), then 3901214 (6.90), 3754340 (6.47) and 3879666 (6.43). These matches give the largest per-match swings if minutes become period-aware.
  - **Per-90 effect.** The current per-90 values are overstated by the ratio of period-aware to credited minutes. This was measured on the 1,250 rows with `in_percentile_pool ∧ is_outfield ∧ meets_min_minutes`: overall 0.33–3.32% (median 1.29%). By league: PL 1.11–3.32% (median 2.19%), Serie A 0.46–2.19% (1.33%), Ligue 1 0.46–2.62% (1.27%), La Liga 0.33–1.43% (0.87%). The largest is Obiang (West Ham) at 3.32%. The bias also differs between players *within* a league: starters lose stoppage time and second-half substitutes lose none. So it reorders league-local cohorts too, not only global ones.
  - **Percentile shift.** This is the shift in percentile points per gated row when per-90 uses period-aware minutes. Local means league × position group; global means position group only.

    | Metric | Local mean | Local max | Local rows ≥ 1 pt | Global mean | Global max | Global rows ≥ 1 pt |
    |---|---|---|---|---|---|---|
    | pressures | 0.29 | 5.71 | 206 | 0.54 | 4.37 | 250 |
    | interceptions | 0.22 | 5.08 | 162 | 0.31 | 3.04 | 104 |
    | xg | 0.15 | 4.26 | 107 | 0.23 | 2.48 | 60 |
    | tackles_won | 0.22 | 4.76 | 172 | 0.36 | 3.11 | 113 |
    | progressive_passes | 0.22 | 3.39 | 166 | 0.41 | 3.57 | 157 |

    The global mean shift is about 1.5–2× the local one; in a global cohort, PL per-90 values are overstated about 2.5× more than La Liga's. The local maximum is larger, because local cohorts are small (about 50–70 rows, so one rank is about 1.5–2 pts).
  - **900-minute gate (result, H4b).** 11 `in_percentile_pool ∧ is_outfield` rows crossed it upward and 0 fell below: PL 5, Serie A 4, Ligue 1 2, e.g. Grealish 882.2 → 900.2, Distin 892.6 → 920.2. No GK row crossed. Erdinç (Ligue 1 907.5) and Klose (PL 910.5) are among them: they now pass on their pool league alone (ADR 002 §31.7).
  - **Decide:** keep match-absolute minutes and document the bias, or move to period-aware minutes (a definition change for nearly every row, with a new ADR and a full before/after). Make the decision before any global percentile or before the gate is applied. Period-aware time would also make the reversed-period stints and the AC Milan 3879549 collision (ADR 002 §3) disappear.

**Pre-conditions from Plan 004 H4 (position groups) — stats-reviewer must decide before computing percentiles:**

- **Positions from Tactical Shift events (option).** ADR 002 §31.6 lists 9 player-matches where the lineup stints disagree with the team's Tactical Shift events and the player's own events.
  - Pattern B: two shifts at one instant, Liverpool 3754226.
  - Pattern C: a shift back to the sub-on slot position has no stint (Polomat, Ketkeophomphone, Totti, Hernanes).

  Taking positions from the Tactical Shift events (`tactics.lineup`) instead of the lineup stints would fix both. The match-level `position_group` is still wrong in 4 of them:
  - Henderson 3754226: AM-Winger, but all his events are DM-CM;
  - Coutinho 3754226: DM-CM, but all his events are AM-Winger;
  - Polomat 3900509: FB-WB, but 62 of 90 events are CB;
  - Ketkeophomphone 3829474: AM-Winger, but 55 of 93 events are ST.

  Decide whether to adopt it before cohorts are built.
- **GK minutes vs one anomalous match.**
  - **Chaigneau (Lorient, 3829440)** was checked (2026-10-02). It is not a lineup label wrong for the whole match. It is the same-instant pattern: a Bouanga → Chaigneau substitution at (1, 2:16) in the Right Midfield slot, then a Tactical Shift in the same second putting him in goal. All his events are Goalkeeper.
  - The pre-H4 pipeline credited him 90.7 min as Right Midfield, and his season row stayed GK only by 1.1 min (91.8 GK vs 90.7). The §31.4 tie rule (H4) makes it 182.4 GK.
  - **Consider a rule** so that a goalkeeper's season GK minutes cannot be outweighed by one anomalous match. For example, a season row with any GK match keeps GK unless the outfield minutes win by a clear margin, or with a check against his own event positions. GK vs outfield decides `is_outfield`, so one mislabel moves a player between incomparable cohorts.
- **A Player On that the lineup does not follow.** §31 minutes come from the lineup stints, so a Player On resumes a player's time only when the lineup does. Milinković-Savić (Lazio 3879637): Player Off (1, 44:27), Player On (1, 45:16), lineup resumes at (2, 45:00), so 3.5 min and 11 of his events are uncredited (ADR 002 §31.6, `events_outside_credited_time`). Decide whether a Player On should resume a player's time when the lineup does not.
- **Near-tie season groups.** Season `position_group` is the argmax of group minutes (`build_player_season.py`), and near-ties are unstable. Full H4 flips the season group in 4 gated rows, all near-ties, e.g. player 16462: AM-Winger 1461.9 vs ST 1459.0, and player 4906 by a 0.85 min margin. Add a flag for a small margin (e.g. the top two groups within a few % of minutes), and decide how M4 cohorts treat flagged rows. **Decide before any cohort is built:** in H4 (ADR 002 §31.7) these 4 rows moved to another cohort and shifted their percentiles by up to about 84 points; 5 of 1,360 gated rows have a top-two margin under 10 min.
- **A size limit for `stint_after_period_end`.** A stint that starts after its period's Half End is clamped to that end and credited from the next period's start (ADR 002 §31.1). It is report-only with no limit (2 cases in 2015/16, Bale and Dembélé, both returning at the whistle). On a new source, a Player On mistimed by many minutes would be credited silently. Add a fail threshold in the style of `PERIOD_SHORT_FAIL_MINUTES`.
- **Count thresholds for report-only checks on a new source.** The report-only issues (e.g. `events_outside_credited_time`, `overlapping_stints`, `stint_after_period_end`, `treatment_window_inside_stint`, `period_end_before_nominal`, `dismissal_after_match_end`, `unpaired_player_off_capped`) never fail the build, so on a new source or league they could grow a lot without anyone noticing. Before a new source feeds any cohort, set a count or rate threshold per issue (from the 2015/16 baselines in ADR 002 §31.7) that fails the build or is a STOP in the independent spike. Raised by the stats-reviewer at the H4b review (2026-10-02).
- **New-source checklist: the independent spike must enforce the pipeline's fail-loud rules.** `spikes/verify_core_metrics.py` re-implements §31 but does not yet enforce two rules the pipeline added in the H4b fix round: exactly two `Half End` events per playing period, one per team (ADR 002 §31.1), and a reversed treatment window (Player On earlier in elapsed time than its Player Off) failing loud (§31.3). On 2015/16 both are 0 and the spike agrees with the pipeline in every match, but on a new source the pipeline could fail where the reference passes. Add both checks to the spike, with tests in `tests/test_verify_core_metrics_spike.py`, **before any new source is added**. Raised by the code-reviewer at the H4b fix-round review (2026-10-02).

- **The 900-minute gate on the period-aware clock.** §31 minutes count all stoppage time. A full match averages 94.1 (La Liga) to 96.4 (PL) minutes, +4.6% to +7.1% over a nominal 90. So `MIN_MINUTES_THRESHOLD = 900` now means about 840 (PL) to 861 (La Liga) nominal minutes, and differs between leagues (ADR 002 §31, Definition note). Decide whether to keep 900, raise it, or express the gate in nominal-equivalent minutes. Decide this together with the 11 rows that newly cross 900 in H4 (Grealish, Distin, …).

**Pre-conditions from Plan 003 C3 (multi-league flags) — stats-reviewer must decide before computing percentiles:**

- **A combined minutes gate counts only pool rows.** If M4 gates on a player's minutes summed across leagues (ADR 002 §17, §29), the sum includes only rows with `in_percentile_pool == True`. Minutes from a PARTIAL competition are observed-only, so adding them biases the gate. Before H4, Mevlüt Erdinç (Ligue 1 891 + Bundesliga 60) and Timm Klose (PL 890 + Bundesliga 93) crossed 900 only through Bundesliga minutes. With the §31 minutes they pass on their pool league alone (Ligue 1 907.5, PL 910.5), so they no longer test this rule; the remaining cross-league cases are in ADR 002 §29 (M4 note).
- **Cross-league double counting.** `is_primary_stint` is unique per competition, not per `(player_id, season_id, source)`. A global cohort filtered on `is_primary_stint` + `meets_min_minutes` counts Khazri, Doucouré and Koné twice. Decide how to deduplicate before building any global cohort (ADR 002 §29).
- **`multi_competition_season` is a lower bound.** When a PARTIAL league is involved, a stint is seen only if the player faced the covered team. Do not use the flag as a gate or dedup key unless it is scoped to pool rows (ADR 003 §7).

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
