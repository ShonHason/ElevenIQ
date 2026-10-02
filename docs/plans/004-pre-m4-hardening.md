# Plan 004: Pre-M4 hardening

Status: planned; decisions on §8 assists and the downloader CLI recorded 2026-09-29. Three chunks: H1 (code), H2 (verification), H3 (docs/tests).
H1 and H2 each end with `/finish-chunk`. H3 changes docs and tests. Because it touches
`tests/`, it also goes through `/finish-chunk`: the docs-only commit exception in CLAUDE.md
does not cover `tests/`.

## Context
The pre-M4 review ([docs/reviews/pre-m4-review.md](../reviews/pre-m4-review.md)) found four
kinds of problem:
- silent defaults that turn missing or unexpected data into plausible values;
- core DNA metrics that are only checked against the pipeline itself;
- ADR/code drift;
- stale commands.

M4 builds percentiles on these columns, so they need to be trustworthy first.
This plan fixes review items 1, 2, 3 and 5-13. The review items are cited below as R1…R22.

**Out of scope:**
- **R4** (the per-stint vs season-total minutes gate, and `is_primary_stint` for cross-league movers).
  These decisions are **M4's first task**, recorded in an ADR before any cohort code.
- **Notes R14-R22.** The exceptions are R15 (the period-5 filter is in H1) and R17 (the wording
  fix is in H3). The others stay as notes; M4 can pick up R16 (npxG) and R18 (position purity).
- **Metric definitions.** No definition changes. The one possible exception is H1.3, which has a
  stop condition.

## Principles
- **Counters and fail-loud checks do not change the data.** They live in the per-league quality report,
  not in parquet columns, so `player_match_stats` keeps its schema.
- **Failure follows the existing pattern.** A failing check raises a new
  `DataQualityError(RuntimeError)`, and the part is not written. This is the same pattern as
  `StarterCrossCheckError` (build_player_match.py:686-694).
- **Mismatches are reported, not tuned away.** In H2, if a spike and the pipeline disagree, stop
  and report. Do not adjust either side.

---

## H1: pipeline hardening (code)

**Before starting:** run the **stats-reviewer** on H1.3 and H1.5. H1.3 may change a ratio
definition, and H1.5 introduces an allow-list; CLAUDE.md requires the review before implementation.

**Baseline:** copy the current parquets to the scratchpad before any code change:
`data/interim/player_match/*.parquet`, `player_match_stats.parquet` and `player_season_stats.parquet`.

### H1.1 Single UNKNOWN rule (R1)
- In `build_player_match`, collect every team-match row with `minutes_unknown > 0`, together with its
  `position_raw` values. The build fails on any such row.
- `aggregate_player_season` adds `is_outfield = position_group ∈ OUTFIELD_GROUPS`
  (import from `metrics/positions.py`).
- The docs then state one rule: "cohorts filter on `is_outfield`; UNKNOWN minutes fail the build".
  Update ADR 002 §4 (remove "surfaces in the quality report"), ADR 002 §12 (replace
  `!= 'GK'` with `is_outfield`) and ADR 003 §5 (the UNKNOWN gate is now this column).
  Also update the docstring at positions.py:11.
- Keep `test_no_unknown_positions_in_pipeline_output`, and add a season-level
  `minutes_unknown == 0` assertion.

### H1.2 Unknown shot/tackle outcomes and duel types (R2)
- `_aggregate_events` counts:
  - shot outcomes ∉ `KNOWN_SHOT_OUTCOMES`;
  - tackle outcomes ∉ `KNOWN_TACKLE_OUTCOMES`;
  - duel types ∉ `KNOWN_DUEL_TYPES`.
- Each counter records the offending values. The per-league report includes them, and the build fails when
  any counter is above 0.
- Fix the shots.py and tackles.py docstrings so they describe the real check.

### H1.3 Unknown-outcome passes in the cross and long-pass denominators (R5)
- **Step 1, measure only.** Count unknown-outcome crosses and long passes per league in the
  quality report.
- **If both counts are 0 in every league:** the denominators are already correct. Add the counters
  as a fail-loud guard (build fails when > 0) and record the rule in ADR 002 §14/§15/§19.
  Outputs stay identical.
- **If either count is above 0:** fixing the denominators changes `cross_completion_pct` /
  `long_pass_completion_pct` and adds `*_unknown_outcome` columns. **STOP and report the counts and
  the per-player effect** before changing anything. That would be a definition change, and it needs
  an ADR amendment and your sign-off.
  (ADR 002 §14: 0.7% of La Liga passes have an unknown outcome, so a non-zero count is plausible.)
- **Outcome (2026-09-29):** the counts were above 0 in every league (crosses 139/64/182/101/3,
  long passes 775/570/826/736/91). **Decided:** apply the fix. Unknown outcomes leave the
  denominators, `*_unknown_outcome` columns are added (display-only), and the ratio is null
  when the adjusted denominator is 0. ADR 002 §14/§15/§19 are amended, with before/after.

### H1.2a Shot-outcome casing (decided 2026-09-29, found by the H1.2 measurement)
- `"Saved to Post"` (StatsBomb casing) → on target. The code had `"Saved To Post"`, so
  127 shots were counted as off target.
- `"Saved Off Target"` → off target, and added to `KNOWN_SHOT_OUTCOMES`. See ADR 002 §30.
- A scan of every categorical value matched in code found no other mismatch.

### H1.4 Shots with missing xG (R6)
- Keep the existing `shots_missing_xg` column.
- The build fails when any league sums to > 0. Expected: 0.

### H1.5 Dropped-event and missing-location counters (R7)
- **Dropped events.** For every event with a `player.id` that does not end up in an output row, count it
  by `(event type, reason)`. The two reasons are "not in lineup" and "0 minutes".
- **This counter is not expected to be 0.** ADR 002 §3 (L198-203) records bench players with
  Bad Behaviour red cards (3 in La Liga). Allow-list exactly that case: a `Bad Behaviour` event by
  a 0-minute lineup player. Any other dropped event fails the build.
  **Decided 2026-09-29:** any card is allowed, yellows included. On the stats-reviewer's
  condition, it applies only to a lineup player with an **empty** `positions` array.
  Measured drops: La Liga 8, Serie A 1, Ligue 1 1.
  The stats-reviewer confirms the allow-list before implementation.
- **Missing locations.** Count `Pass` and `Carry` events missing `location` or `end_location`.
  The build fails when the count is > 0.

### H1.6 `compute_match_duration` raises (R8)
- Raise `ValueError` when no events have period ≤ 4.
- Require the `period` key; drop the `.get(..., 1)` default.
- Update ADR 002 §3 (L128-130) and `tests/test_minutes.py` (the 90.0 fallback test becomes a raise test).

### H1.7 Period-5 filter (R15)
- In `_process_match`, drop `period == 5` events before building the per-player index and before
  `compute_xg_assisted_per_player`. ~~Dismissal lookup keeps the full list~~ **Amended
  2026-09-29:** the dismissal lookup also ignores period 5, because a shootout card inflated
  open-ended stints past the match duration (found by qa-tester). A dismissal minute past the
  match duration fails the build (`dismissal_after_match_end`), never capped silently.
- Add a unit test: a shootout Shot must not count in `shots` or `goals`.

### H1.8 Downloader CLI (decided 2026-09-29)
- Add a small `__main__` to `statsbomb/downloader.py`.
  - With no arguments, it downloads every entry in `COMPETITION_SEASONS`.
  - `--competition-id` / `--season-id` select a single league. An unconfigured pair exits with an
    error, mirroring `build_player_match._select`.
- `download_competitions()` currently ignores `data_root` (downloader.py:43-46). Leave it as is
  unless the CLI needs it.
- Add a unit test for argument selection. Nothing is downloaded in tests.

### H1 verification
1. Run the fast suite, with new unit tests for every counter (fail when > 0; allow-list honoured).
2. Rebuild all leagues with `build_player_match --force`, then run `build_player_season`.
3. Every counter is 0, except the allow-listed bench-card drops. Report those per league.
4. **Column-level equality (amended 2026-09-29):** H1.2a and H1.3 change definitions, so
   outputs are not fully identical. The rule:
   - Every column except the fixed ones must be identical to the baseline (`DataFrame.equals`):
     - every `player_match` part and the combined file;
     - the season file, aligned on its unique key `(player_id, team_id, competition_id,
       season_id, source)`. Its row order was not deterministic; from H1 it is sorted
       on that key, and a unit test checks that two builds are identical row by row.
   - The fixed columns are:
     - `shots_on_target` (match + season);
     - `shots_on_target_p90`;
     - `cross_completion_pct`;
     - `long_pass_completion_pct`.

     Their before/after goes in ADR 002 §15, §19 and §30.
   - The new columns are ignored: `is_outfield`, `crosses_unknown_outcome` and
     `long_passes_unknown_outcome`.
   - The volume columns (`crosses_*`, `long_passes_*` and their p90s) must be identical.
   - `sha256` is also compared for the `player_match` files, as secondary evidence only: parquet
     writer metadata can differ, so a byte mismatch that has frame equality is reported but not treated as a failure.
5. Any value difference means STOP and report.
6. Run the integration suite; it must stay green.
7. `/finish-chunk`.

---

## H2: independent verification (spikes → reference files → tests)

### Scope
Raw-JSON reference totals, per league, for these columns (R3):
`minutes_played, shots, xg, passes_attempted, passes_completed, progressive_passes,
progressive_carries, key_passes, assists, tackles_attempted, tackles_won, dribbles_attempted,
dribbles_completed, interceptions, clearances, blocks, fouls_committed, fouls_won, carries`.

### Approach
- Add a new spike, `spikes/verify_core_metrics.py`.
  - It uses the same CLI and helpers as the existing spikes (`reference_totals.py`), is stdlib only,
    and **never imports `eleveniq`**.
  - Every definition is re-implemented from the ADR text (ADR 002 §1, §2, §3, §8, §13 and
    `_aggregate_events` semantics as documented), not copied from `src/`.
- It writes a new `"core"` section into each of the 5 files in `tests/fixtures/reference_totals/`.
  The existing sections stay untouched.
- **Minutes also get a structural check that does not depend on a second implementation.**
  Per team-match, summed minutes ≤ 11 × match duration (+ tolerance). Summed across the league,
  they equal Σ(11 × duration) minus the dismissal losses, within a stated tolerance. The spike fails when
  this is violated. That makes the minutes reference more than "the same rule coded twice".
- **Limitation, stated honestly:** for the derived metrics (progressive, key passes), the spike is a
  second implementation of the same written spec. It catches coding errors and silent drops, not
  a wrong definition.
- `TestIntegration` gets `test_core_metrics_reconcile`, parametrised over the 5 references:
  - the season sum equals the reference (exact for counts, `abs=0.001` for xg and minutes);
  - the match sum equals the season sum.
- `test_reference_totals.py` adds the unit check "every reference file has a `core` section".

### Stop rule
If a spike total and the pipeline disagree in any league, **STOP and report** the column, the league, both
values and a sample of the differing matches. Do not change the spike or the pipeline to make them agree.
The resolution is your call.

### Outcome (2026-09-29)
- **Columns.** The spike and the pipeline agreed on all 20 columns, plus
  `shots_on_target`. The structural minutes check failed: 33 player-matches kept a
  stint past their own Substitution event.
- **Decided:** the Substitution event is authoritative. Stints are capped at the
  player's own substitution. A stint starting after it, a second substitution off, or
  a team-match above 11 × duration fails the build. The stats-reviewer approved this
  with conditions. ADR 002 §3 has the before/after: 1,392.87 min removed, and no
  `meets_min_minutes` flip.
- **After the fix,** the structural check passes and all 20 columns agree in every
  match. The `core` section and `base.dismissals` are written.

### H2 verification
Run the spike for all 5 leagues (the goals reconciliation must pass), then the integration suite
(green), then `/finish-chunk`.

---

## H3: docs and tests (drift and self-checks)

### ADR 002
- **§1 L72:** quote the real outcome expression (`(pass_data.get("outcome") or {}).get("name")`).
- **§6 header (L293-296):** replace "None of this is in the current code" with "Implemented in
  build_player_season.py".
- **§6c (L343-345):** xA is extracted as `xg_assisted` (§25).
- **§6e:**
  - an absent `from_period` raises (it no longer defaults to 1);
  - record the Starting XI cross-check that fails the build (from plan 003 L122-124).
- **§8 assists (decided 2026-09-29):** keep `assists_p90`. Amend §8 to read "stored with a
  p90 column; excluded from DNA per §6c", the same treatment as goals. No code change.
- **§26 wording:** crediting unknown-shooter shots as direct is a *permissive* fallback, not a conservative one,
  and it had 0 occurrences. Mirror the change in the xg_assisted.py:114-119 comment and docstring.

### Stale docstrings
- minutes.py:76-80 (`compute_player_minutes` has its own loop).
- quality_report.py:22-23 (the dismissal fix is already applied).
- Remove the now-inaccurate claims left after H1 in shots.py, tackles.py and positions.py.

### CLAUDE.md
- **Add these commands:** `build_player_season`; the downloader CLI from H1.8.
- **Integration-test prerequisites:** they need `data/raw/`, `data/interim/` and `data/processed/`.
- **Stored grain:** player-team-competition-season.
- **Group names:** `FB-WB` and `DM-CM`.
- **Minutes default:** 900.
- Fix the `test_positions.py:453` skip message (`src.cli build-match` does not exist).

### Self-checking tests (R12)
- **test_aggregation.py L61-335** (`TestPassCompletion`, `TestPer90SafeDivision`,
  `TestMultiTeamAggregationKey`): rewrite them to call `aggregate_player_season`, or delete the ones
  already covered in test_build_player_season.py. Close the milestones.md:99-101 tech-debt item.
- **test_quality_report.py:407-451:** add an independent oracle. The number of dismissed in-parquet
  players per league is counted by a spike from raw cards (a new `dismissals` field in the `base`
  section; ADR 002 §3: 109 La Liga cards, 3 of them bench). Keep the existing test as a secondary check.
  The spike runs with H2's spike, so this sub-item's fixture change lands in H2.
- **test_season_identity_edges.py:225-239:** additionally assert the ADR-recorded counts:
  - 68 `multi_team_season` player_ids (ADR 002 §28);
  - 47 `multi_competition_season` player_ids / 94 rows (ADR 003 §7);
  - 2,279 pool rows (ADR 003 §5).

### H1 review follow-ups (code-reviewer, 2026-09-29), in this order
1. **Issue-name constants (first):** `"minutes_unknown"`, `"dropped_event"`,
   `"dismissal_after_match_end"` (and the other quality-issue names) are string literals both
   where they are emitted and in `FAILING_QUALITY_ISSUES`. A typo on either side silently
   disables a fail-loud check. Define them as module constants, like `DROPPED_*`.
2. **One shootout-period definition:** `e["period"] != 5` in `_process_match` and `period <= 4`
   in `compute_match_duration` should share one constant (or an `is_playing_time(event)` helper
   in metrics/minutes.py).
3. **Rename `dismissal_report` / `all_dismissals`** (and the `_process_match` docstring) to
   `quality_report`: they carry every quality issue, not only dismissals.
4. **Test:** a period-5 pass with `shot_assist` / `goal_assist` counts toward neither
   `xg_assisted` nor `key_passes`.

### H2 follow-ups
- **Store `match_duration` in the match-level parquet** so `TestIntegration` can check the team
  minutes sum against 11 × duration exactly, instead of an inferred bound.

### H2 review follow-ups (stats-reviewer, code-reviewer, qa-tester, 2026-10-01), in this order
1. **`substitution_minute` becomes a required keyword argument** in `compute_player_minutes` and
   the positions.py stint functions. A forgotten argument currently gives uncapped minutes without
   any error. The two deliberately uncapped calls in build_player_match.py pass `None` explicitly.
2. **Per-player guard:** a player's minutes in a match greater than the match duration (plus a
   small tolerance) raises `DataQualityError`. Rewrite the team-ceiling tests to use 11 and 12
   distinct players instead of one player with 11 or 12 overlapping stints.
3. **On-pitch count check (stats-reviewer #3):** a report-only quality issue when more than 11
   players of one team are on the pitch at any instant. It is period-aware and excludes dismissed
   players after their card. This is the strong guard, because a team that lost minutes elsewhere
   can mask an overcount under 11 × duration.
4. **`find_substitutions_off` null type:** an event with `"type": null` raises a clear error
   instead of an `AttributeError`. Also add the missing test: a substituted player whose stint
   has no `from_period` raises (per the `_substitution_off_minute` docstring).
5. **Fractional-second lineup times:** in the stint-after-substitution check, a lineup `from`
   such as `"37:02.400"` against a substitution at second 2 must count as the substitution
   instant, not fail the build. Add a test that pins the allowed equality.
6. **One shared clamp function** for the stint-end clamp loop that is duplicated in
   minutes.py and positions.py, so the two cannot drift.
7. **ADR 002 §3 wording:**
   - narrow the "on-pitch count never exceeds 11" sentence: in the pipeline's match-absolute
     arithmetic it reaches 12 once (AC Milan, match 3879549, 45:00–45:51, caused by Rodrigo Ely's
     reversed-period stint);
   - classify the 9 reversed-period stints individually instead of calling all of them
     "spurious". Some encode first-half-stoppage position changes, such as Bonaventura and
     Bertolacci in 3879549. Only stints that start with a Substitution-On after the reversed
     window, such as Ely's, are phantom.
8. **code-reviewer checklist:** add "minutes taken from lineups without checking against
   Substitution events".

### H3a review follow-ups (qa-tester, stats-reviewer, code-reviewer, 2026-10-01), for H3b
H3a (code) is the review follow-ups above plus `match_duration`. Its reviews found the
items below, which go into H3b (docs/tests) unless an item turns out to need code.
1. **On-pitch check edge cases** (`_on_pitch_excess`):
   - **Dismissal while off for treatment.** A player off for treatment ("Player Off" /
     "Player On") who is sent off during that window does not lower the allowed number.
     Under football law he is still dismissed and cannot be replaced, so a replacement
     coming on would be a real excess the check misses. Decide the rule, then test it.
     The Bordeaux case (card on the bench after his own substitution) stays excluded.
   - **Card and exit at the same second.** Cards sort before exits, so a player
     substituted off and booked in the same second still lowers the allowed number,
     which gives a false excess. Add a test and decide the order.
   - **Stint with `to` but a null `to_period`.** It currently fails with a bare
     `TypeError` (a match failure). Raise a clear error instead.
   - **Two cap rules order time differently.** The check picks the earlier exit in
     (period, minute) order; `stint_end_cap` picks it in match-absolute minutes. They
     differ only when one event is in first-half stoppage and the other in period 2,
     which does not occur in current data. Pin or align them.
2. **Align the 90:05 docstring with the guard.** The `iter_stint_bounds` docstring calls
   an uncapped explicit `to` slightly past `match_duration` legitimate, but the new
   `player_minutes_exceed_duration` guard (tolerance 1e-6) fails the build on it. Real
   data has 0 such rows (largest excess 1.4e-14). Align the docstring and ADR 002, and
   state that the guard is expected to trigger on new sources and that the response is to
   investigate, not to widen the tolerance silently.
3. **Stale ADR 002 docs.**
   - Signatures at L148 and L460: `dismissal_minute` and `substitution_minute` are now
     both required keyword arguments.
   - Document the `match_duration` column in `player_match_stats`.
   - Document the new failing issue `player_minutes_exceed_duration` and the report-only
     issue `on_pitch_count_exceeded`, including their summary/CLI counts.
   - State the own-goal gap in goals (about 28–38 per league below the official totals;
     own goals are not credited to a player), so it is not mistaken for missing data.
4. **Test hygiene.**
   - In `test_quality_issue_names_are_constants_not_literals`, accept an `ast.IfExp` only
     when both branches are `ast.Name`.
   - New tests filter on literal issue strings; use the module constants instead.

### H3 verification
Run the fast and integration suites (green), grep the old stale phrases (expect no hits), then
`/finish-chunk`.

---

## H4: open decisions (queued 2026-10-01 from the H3b reviews)
**Status (2026-10-02).** The items below are decided; the decisions are in ADR 002 §31.
Branch `h4-period-aware-minutes`.

**Chunks.**
- **H4a (spike first; this chunk):** the independent spike, the references and the
  §31 rule text.
- **H4b:** the pipeline, until it matches the spike in every match; then the
  before/after, the data-auditor checklist and closing the M4 pre-condition.

**Decisions.**
- **Half-end rule:** the `Half End` event.
  - A period that ends early is kept as recorded (31 cases, report-only).
  - More than 5 min short fails the build.
  - A post-match card is capped and report-only.
- **Exits and windows.** Treatment windows are subtracted. An unpaired Player Off is an
  exit cap, and an event after it fails the build.
- **Stints.** Minutes are the union of a player's stints.
- **Position at t:** the latest start. At one start instant, a Tactical Shift stint wins,
  then the stint listed first.
- **Checks.** The event-position oracle and the tactics-timeline check, with 9 listed
  exceptions.
- **`check_dismissed_player_minutes`:** removed in H4b.

**H4a outcome.**
- All counts are as predicted: 31 early period ends, 2 stints starting after Half End,
  7 treatment windows inside a stint, 14 overlaps (15.33 min), 3 unpaired-Off caps.
- The league minutes gap is 0.0005% or less in every league.
- Every other core column and the dismissal counts are unchanged.
- `core.minutes_played` and `core.minutes_<group>` are written.

**H4b also (stats-reviewer, 2026-10-02):**
- a unit test for the equal-elapsed tie (later start instant wins; 0 cases in the data);
- tests for the Tactical Shift tie rule (the Ravel Morrison pattern) and the
  Chaigneau case.

**M4 items queued in docs/milestones.md:**
- positions from Tactical Shift events;
- the GK-minutes rule;
- a near-tie season-group flag.

The original queue:
- **`check_dismissed_player_minutes`** (quality_report.py): only tests call it; the
  pipeline applies the dismissal cap itself and records minutes with and without it.
  Either wire it into the integration suite as a second check of the cap, or remove
  it (and its unit tests) so it cannot drift from the cap logic.
- **Half-end rule** (docs/milestones.md, Milestone 4, "Pre-condition from Plan 004 H3b"):
  `Half End` event vs last event for both halves, and what to do when the end is before
  the nominal 45:00 / 90:00. Needed before any period-aware minutes.
- **Period-aware minutes must subtract treatment windows** (stats-reviewer C1, H3b).
  Lineup stints that cross periods can include a "Player Off" / "Player On" window
  (Sammarco, 3879602: 0.3 min credited while off). Reordering by period alone would
  credit it; ADR 002 §3.
- **New-source audit checklist** (stats-reviewer C3, H3b): add to the data-auditor
  checklist that Substitution events must be complete. The on-pitch card rule
  assumes so, and a dropped Substitution event gives a false
  `on_pitch_count_exceeded` (ADR 002 §3, "Assumption: the Substitution event is
  complete").
- **Percentile shift on the full metric set** (stats-reviewer C4, H3b): the
  first-half-stoppage measurement covers only 5 volume metrics (pressures,
  interceptions, xg, tackles_won, progressive_passes). Extend
  spikes/measure_first_half_stoppage.py to every per-90 DNA metric before M4 treats
  the 5.7-point local maximum as the worst case.

---

## Risks
- **H1.3 may not be output-neutral.** It has a stop condition.
- **H1.5 allow-list scope.** If other dropped-event types show up (e.g. events by a player missing from
  the lineup), the build fails by design. Report them; do not widen the allow-list without a decision.
- **Parquet bytes.** Byte equality can fail on writer metadata alone. Frame equality is the gate.
- **H2 is only as independent as the spec.** The minutes structural check and the goals-vs-scores
  reconciliation are the only truly external oracles.

## Order
H1 → H2 → H3. H2 builds its references after H1, so any H1 fail-loud check that fires on real data
is resolved first. Each chunk stops for sign-off before the next begins.
