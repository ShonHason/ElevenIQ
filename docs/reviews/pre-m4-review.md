# Pre-M4 Pipeline Review

**Date:** 2026-09-29
**Scope:** `src/eleveniq/` (all modules), `docs/decisions/002`, `docs/decisions/003`, `CLAUDE.md`, `tests/`
**Mode:** Read-only. Nothing was fixed.
**Fast suite:** `pytest -m "not integration"`: 784 passed, 53 deselected. The full pipeline and the integration tests were not run.

Every finding cites code or docs that were read for this review. Line numbers refer to commit `3efaeaa`.

---

## MUST FIX (before M4)

### 1. UNKNOWN position group has no gate, and the ADRs give three different rules
- **Where:** `src/eleveniq/pipeline/build_player_season.py:362`; `src/eleveniq/metrics/positions.py:11,86`; ADR 002 §4 (L242-248), §12 (L599-601); ADR 003 §5 (L103); `tests/test_positions.py:456`
- **Evidence:**
  - `is_gk = position_group == "GK"`. `POSITION_GROUP_COLUMNS` includes `UNKNOWN`, and `_season_position_group` (build_player_season.py:125) takes the argmax over all keys. So a season row can have `position_group == "UNKNOWN"` with `is_gk = False`.
  - The docs disagree on the rule:
    - ADR 002 §4 says to filter to `OUTFIELD_GROUPS`.
    - ADR 002 §12 says to filter on `position_group != 'GK'`, which would let UNKNOWN rows in.
    - ADR 003 §5 names an "UNKNOWN position exclusion" gate, but no such column or check exists.
  - ADR 002 §4 and positions.py:11 say unknown positions "surface in the quality report". No such check exists.
  - The only guard is `test_no_unknown_positions_in_pipeline_output`. It checks the match-level primary `position_group`. It does not check `minutes_unknown > 0` or the season group.
- **Fix:** Add an `is_outfield` column (`position_group in OUTFIELD_GROUPS`) or fail the build on any `minutes_unknown > 0`. Then make ADR 002 §4 and §12 state the same rule.

### 2. Unknown shot/tackle outcomes and duel types are never surfaced
- **Where:** `src/eleveniq/metrics/shots.py:22-23`; `src/eleveniq/metrics/tackles.py:15-19`; `src/eleveniq/pipeline/build_player_match.py:270,300-305`
- **Evidence:**
  - The docstrings say values outside `KNOWN_SHOT_OUTCOMES` and `KNOWN_TACKLE_OUTCOMES` are surfaced in the quality report.
  - grep shows `KNOWN_SHOT_OUTCOMES`, `KNOWN_TACKLE_OUTCOMES` and `KNOWN_DUEL_TYPES` are used only in `tests/test_tackles.py`, never in `src/`.
  - An unmapped tackle outcome counts as attempted but not won. That silently lowers `tackle_success_pct`.
  - An unmapped shot outcome counts as off target.
  - Duel types other than Tackle and Aerial Lost are silently ignored.
- **Fix:** Count unknown outcome and duel-type values per build, and fail (or report) when any count is above 0.

### 3. Core DNA counts are only checked against the pipeline itself
- **Where:** `tests/fixtures/reference_totals/*.json`; `tests/test_build_player_season.py:905-930` (`TestIntegration`)
- **Evidence:**
  - The reference files are built from raw JSON by the spikes. They hold `matches`, `starts`, `goals`, `own_goals`, crosses, and the chunk A/B metrics.
  - The following columns have no independent reference: `minutes_played`, `shots`, `xg`, `passes_attempted/completed`, `progressive_passes`, `progressive_carries`, `key_passes`, `assists`, tackles, dribbles, `interceptions`, `clearances`, `blocks`, fouls, `carries`.
  - For these columns, the tests only compare season sums with match sums (`test_goals_reconcile` … `test_shots_reconcile`). That is the pipeline checked against itself.
  - ADR 002 §6c names xG and key passes as preferred DNA metrics.
- **Fix:** Extend the spikes to write raw-JSON reference totals for these columns, and assert them in `TestIntegration`.

### 4. Two gate decisions the ADR says must come before percentiles are still open
- **Where:** ADR 002 §17 (L757-759), §29 (L1266-1276); `build_player_season.py:361,389-406`
- **Evidence:**
  - `meets_min_minutes` uses per-stint `minutes_played`. §17 says this decision "must be recorded … before any percentile computation reads `meets_min_minutes`".
  - `is_primary_stint` is not unique per `(player_id, season_id, source)`. This affects 47 players. 3 are double-counted at ≥900 minutes, and 8 reach ≥900 minutes only when their leagues are combined.
- **Fix:** Make both decisions and record them in the ADR as the first M4 task, before any cohort code.

---

## SHOULD FIX

### 5. Unknown-outcome passes count as incomplete for crosses and long passes
- **Where:** `build_player_match.py:240-253`; `build_player_season.py:341-354`; ADR 002 §14
- **Evidence:** `crosses_attempted` and `long_passes_attempted` count `"Unknown"` outcomes as attempts. The completed counts do not include them. So `cross_completion_pct` and `long_pass_completion_pct` keep unknowns in the denominator. §14 removes them from `pass_completion_pct` for exactly this reason.
- **Fix:** Subtract unknowns from these denominators too, or record the asymmetry in ADR 002 §14/§15/§19.

### 6. Shots with missing xG bias the xG rates silently
- **Where:** `build_player_match.py:274-280`; `build_player_season.py:307-313,326-329`
- **Evidence:** A shot without `statsbomb_xg` is counted in `shots` and `shots_missing_xg`, but adds nothing to `xg`. So `xg_per_shot` and `xg_p90` undercount. The only missing-xG check covers assisted shots (`test_chunk_b_xg_assisted_shot_side_reconcile`, test_build_player_season.py:1022).
- **Fix:** Fail the build when `shots_missing_xg > 0`, or set `xg_per_shot` and `xg_p90` to null for affected rows.

### 7. Events are dropped silently
- **Where:** `build_player_match.py:444-450,465,478,550,225-233,255,287`
- **Evidence:**
  - Players with empty `positions` get 0 minutes and are skipped. Their events disappear.
  - Events from players who are not in the lineups are indexed but never read.
  - Passes and carries without `location`/`end_location` skip the progressive and entry checks.
  - None of these cases has a counter. `passes_missing_length`, by contrast, is counted.
  - The reference totals only catch this for the chunk A/B metrics and crosses.
- **Fix:** Count dropped events and missing locations in the quality report.

### 8. `compute_match_duration` has silent defaults
- **Where:** `src/eleveniq/metrics/minutes.py:56-58`; ADR 002 §3 (L128-130)
- **Evidence:** It uses `e.get("period", 1)` and returns `90.0` when no valid events exist. An empty or broken events file would then give every lineup player a plausible 90 minutes. The fallback is documented, but it hides the fault.
- **Fix:** Raise instead of returning 90.0, and require `period`.

### 9. ADR 002 §8 and the code disagree on `assists`
- **Where:** ADR 002 §8 (L472-473); `build_player_season.py:86`
- **Evidence:** §8 says `assists` is display-only. The code lists it in `_COUNT_COLS`, so `assists_p90` is produced.
- **Fix:** Move `assists` to `_DISPLAY_ONLY_COLS`, or amend §8.

### 10. ADR 002 §6e and the code disagree on starters
- **Where:** ADR 002 §6e (L371-379); `build_player_match.py:398,686-694`; `docs/plans/003-multi-league.md:122-124`
- **Evidence:**
  - §6e says an absent `from_period` defaults to 1. `_is_kickoff_spell` indexes `spell["from_period"]`, which raises `KeyError` when the key is absent.
  - §6e says starter mismatches are only reported. The Starting XI cross-check now fails the build, and that rule is recorded only in plan 003, not in an ADR.
- **Fix:** Update §6e to match the code and add the cross-check rule.

### 11. Stale ADR and docstring text
- **Where:** ADR 002 L72, L293-296, L343-345; `minutes.py:76-80`; `quality_report.py:22-23`
- **Evidence:**
  - The §6 header says "None of this is in the current code".
  - §6c says "xA is not currently extracted", but `xg_assisted` exists.
  - §1 quotes an outcome expression the code no longer uses (the actual expression is at build_player_match.py:219).
  - The `compute_player_minutes` docstring says it stays consistent via `_iter_stint_contributions`. It has its own loop (minutes.py:89-104).
  - quality_report.py says the build "must re-run with dismissal_minute". The fix is already applied.
- **Fix:** Rewrite these passages to describe the current code.

### 12. Tests that check the pipeline against itself
- **Where:** `tests/test_aggregation.py:61-335`; `tests/test_quality_report.py:407-451`; `tests/test_season_identity_edges.py:225-239`
- **Evidence:**
  - `TestPassCompletion`, `TestPer90SafeDivision` and `TestMultiTeamAggregationKey` rebuild the Polars expressions inline. They never call `aggregate_player_season`, and their group key has no `source`. This is already listed as tech debt in milestones.md:99-101.
  - The dismissal integration test uses the pipeline's own `find_dismissal_minute` as its oracle.
  - The multi-team and multi-competition flag tests re-derive each flag with the same definition. They do not assert the counts recorded in the ADRs (68 multi-team players, 47 multi-competition players).
- **Fix:** Point these tests at the production code or at independent oracles: the 109 La Liga dismissals from ADR 002 §3, and the counts from ADR 002 §28 and ADR 003 §7.

### 13. CLAUDE.md commands are out of date
- **Where:** `CLAUDE.md` Commands; `build_player_season.py:506`; `statsbomb/downloader.py:8-12`; `tests/test_build_player_season.py:25-26,1022`; `tests/test_positions.py:453`
- **Evidence:**
  - There is no command for `build_player_season`, although it has a `__main__`.
  - There is no download command, and the downloader has no CLI.
  - The integration tests are documented as needing `data/interim/`. They also need `data/processed/` and `data/raw/`.
  - A skip message cites the non-existent command `python -m src.cli build-match`.
- **Fix:** Add the season-build and download commands, and correct the integration-test prerequisites.

---

## NOTE

14. **Per-90 is correct.**
    - `{c}_p90 = season total / season minutes * 90` (build_player_season.py:300-314). It is never averaged.
    - The p90 loop covers only `_COUNT_COLS`, so `_DISPLAY_ONLY_COLS` columns never get a p90.
    - Tests: test_build_player_season.py:209, 1299-1366, 1425-1433.
    - One inconsistency: `passes_completed`, `dribbles_completed` and `tackles_won` have p90 columns, but `crosses_completed` and `long_passes_completed` do not. The ADR 002 §15 rationale is applied unevenly.
15. **Period 5 is only excluded from match duration** (minutes.py:56). `_aggregate_events` does not filter by period, so penalty-shootout shots and goals would count. This is latent: league data has no shootouts, and goals reconcile against matches.json (test_aggregation.py:1674).
16. **Penalties are included in `shots` and `xg`.** There is no npxG. This is a DNA decision for M4 (ADR 002 §6c).
17. **The xg_assisted_direct fallback is not conservative.**
    - xg_assisted.py:114-119 and ADR 002 §26 credit a shot with an unknown shooter or possession as direct and call this "conservative". It actually inflates `xg_assisted_direct`. There were 0 occurrences in 2015/16.
    - A dangling `assisted_shot_id` or a missing assister is skipped with no counter (L92-94, L103-104).
18. **Season `position_group` is an argmax per stint.**
    - All of a hybrid player's minutes go into one cohort. The `minutes_*` columns are kept, so M4 can add a share-of-minutes purity rule.
    - An exact GK/AM-Winger tie gives `is_gk = False` (test_build_player_season.py:1649-1680).
19. **Unused code.** `compute_player_minutes` and `check_dismissed_player_minutes` are not used anywhere in `src/`. They are kept only for tests and ADR references, and they duplicate logic.
20. **Parts are reused without a code-version stamp** (build_player_match.py:794-797). CLAUDE.md documents this. A stale part with the same schema passes the concat silently.
21. **Minor CLAUDE.md mismatches:**
    - It says stats are "stored at the player-season level". The actual grain is player-team-competition-season (build_player_season.py:67-72).
    - Group names are written "FB/WB, DM/CM"; the code uses `FB-WB`, `DM-CM`.
    - The future layout `src/eleveniq/api/` conflicts with ADR 002 §7 (`app/`).
    - The minutes default is given as "~600–900"; the code uses 900.
22. **`in_percentile_pool` is sound.**
    - It is set only from config. It is left-joined with a null check instead of `fill_null` (build_player_season.py:415-423).
    - Three coverage checks run before the file is written.
    - Known limitation: ADR 003 §5 L138-139.
