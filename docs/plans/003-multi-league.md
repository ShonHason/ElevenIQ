# Plan 003: Multi-league expansion (all 2015/16 leagues)

Status: approved, not started. Executed in three chunks (C1–C3). Each chunk ends with `/finish-chunk`.

## Context
The pipeline currently covers only La Liga 2015/16 (comp 11 / season 27). StatsBomb Open Data
has 2015/16 for the other big-five leagues as well. This plan ingests them without changing
metric definitions, and verifies every league against an external reference instead of against
the pipeline itself.

## 1. Inventory (from competitions.json and matches/{comp}/27.json)

| League | comp_id | season_id | Matches | Teams | Class |
|---|---|---|---|---|---|
| La Liga | 11 | 27 | 380 | 20 | FULL (already local) |
| Premier League | 2 | 27 | 380 | 20 | FULL |
| Serie A | 12 | 27 | 380 | 20 | FULL |
| Ligue 1 | 7 | 27 | 377 | 20 | FULL (3 fixtures missing; 6 teams have 37/38) |
| 1. Bundesliga | 9 | 27 | 34 | 18 | PARTIAL (Leverkusen's matches only) |
| Champions League | 16 | 27 | 1 | 2 | Out of scope (final only) |

**Classification rule (ADR 003).** A competition-season is FULL when the data holds at least 95%
of the expected fixtures and every team appears. Otherwise it is PARTIAL. ADR 003 logs the Ligue 1
gap: 3 missing fixtures, with the affected teams listed.

**Pool membership.** PARTIAL seasons are ingested, so Leverkusen players get rows, but they carry
`in_percentile_pool = False`. ADR 003 covers only the coverage classification and this flag.
Percentile-cohort design stays in M4.

## 2. La Liga-specific places

**Code / entry points**
- [build_player_match.py:619-620](../../src/eleveniq/pipeline/build_player_match.py): `__main__` hardcodes `build_competition_season(11, 27)`.
- [build_player_match.py:53](../../src/eleveniq/pipeline/build_player_match.py): a single `OUTPUT_PATH` that is overwritten on every run.
- [downloader.py:10](../../src/eleveniq/statsbomb/downloader.py): the docstring example uses (11, 27). The function itself is already generic.
- [xg_assisted.py:53,173](../../src/eleveniq/metrics/xg_assisted.py): the log message expects "~6 in La Liga". Make it league-neutral.

**Integration tests with hardcoded totals**
- `tests/test_build_player_season.py:835-896`: starts = 8,360; crosses 9,606/2,651; Chunk A and Chunk B totals.
- `tests/test_build_player_match.py:637`: 380 × 2 × 11 starters.
- `tests/test_aggregation.py:1673-1679`: 1,014 + 29 = 1,043 goals, "only season in the parquet".
- `tests/test_quality_report.py:424`: assumes a single season.
- Unit-test factory defaults (`competition_id=11, season_id=27`) are harmless and stay as they are.

**Spikes**
- `spikes/verify_chunk_a_metrics.py:21-22` and `spikes/verify_chunk_b_metrics.py:29-30`: module constants plus "380 matches" / "full La Liga season" text.

**Docs**
- ADR 002 §25-27 reference figures are La Liga-only. Keep them as they are and add per-league figures in a new section.

## 3. Generalising the integration tests
- The spikes take `--competition-id/--season-id` and loop over the config. The raw-JSON counting
  logic stays untouched and never imports `eleveniq.pipeline`.
- **External reconciliation (required, per league).** Each spike reconciles its event-derived
  goals + own goals against the sum of `home_score + away_score` from `matches.json`. A mismatch
  fails the spike. This check replaces eyeball sanity ranges as the gate for accepting reference
  figures.
- The spikes write `tests/fixtures/reference_totals/{comp_id}_{season_id}.json` with: match count,
  starts, goals, own goals, crosses, and the Chunk A and Chunk B totals. These files are committed.
- The integration tests are parametrised over the reference files. Each test filters the parquet
  by `(competition_id, season_id)` and asserts equality.
- Starts use a structural invariant: `starts == n_matches × 2 × 11`, with `n_matches` taken from
  the reference file. The invariant also holds for the partial Bundesliga season.

## 4. Output layout
- One parquet per competition-season: `data/interim/player_match/{comp_id}_{season_id}.parquet`.
- A concat step builds `data/interim/player_match_stats.parquet`, and `build_player_season` reads
  that file unchanged. Its key already includes comp and season.
- Runs are resumable. A league whose file already exists is skipped unless `--force` is passed.
  A failed league does not force a full rebuild; only that league is rerun, followed by the concat.

## 5. Size and runtime estimate
- La Liga locally: ~1.0 GB of events plus 7.6 MB of lineups for 380 matches (~2.7 MB per match).
- New: 380 + 380 + 377 + 34 = **1,171 matches, ~3.1 GB** to download (**~4.2 GB** on disk in total).
- Pipeline runtime is linear in matches, about **4.1× the La Liga run**. Time La Liga once before
  C2 starts. The download is network-bound, so rely on the downloader's skip-if-exists behaviour.

## 6. Risks
- **Mid-season moves between leagues.** The multi-team key is
  `(player_id, competition_id, season_id, source)`
  ([build_player_season.py:225](../../src/eleveniq/pipeline/build_player_season.py)). A player who
  moves from Serie A to the Premier League in January gets two rows, each with
  `multi_team_season=False` and half-season minutes. Add a `multi_competition_season` flag keyed on
  `(player_id, season_name, source)` and keep the league rows separate. stats-reviewer reviews this
  flag. How to rank these players is an M4 question.
- **Competition format.** The Bundesliga has 18 teams and 34 rounds (and is partial here). Any check
  or log range that assumes 38 rounds or 20 teams breaks.
- **Partial-coverage bias.** Leverkusen players only face rotating single opponents. They are
  excluded from pools via `in_percentile_pool`.
- **Missing xG and event fields.** The data-auditor profile in C1 checks each league for:
  - the null rate of `shot.statsbomb_xg`
  - the presence of `pass.goal_assist`
  - key-pass linkage. Correction after the C1 audit: xG-assisted links a shot to its pass
    through `pass.assisted_shot_id` / `shot.key_pass_id`, not through `related_events`. The
    audit found `key_pass_id` is never in `related_events`, in any league (32,605 / 32,605),
    while `assisted_shot_id` resolves for every shot. Nothing may join shots to key passes
    through `related_events`.
  - the position-name vocabulary (an unknown name means a player is classed UNKNOWN)
- **Quality checks tuned to La Liga.** Dismissal and fallback counts may need per-league
  expectations.
- **IDs.** StatsBomb IDs are global. Add an assert that each `team_id` maps to exactly one
  `team_name`.

## 7. Execution chunks

**C1: Data acquisition and audit (no pipeline code changes)**
- Download comps 2, 12, 7 and 9 for season 27.
- data-auditor profiles each league: xG nulls, position vocabulary, key-pass linkage, fixture
  coverage. The profile is reproducible via `spikes/profile_2015_16_leagues.py`.
- Draft ADR 003: coverage classification rule, Ligue 1 gap, `in_percentile_pool` flag.
- `/finish-chunk`.

**C2: Multi-league pipeline and reference figures**
- Add `COMPETITION_SEASONS` to `src/eleveniq/config.py`: comp_id, season_id, label, coverage.
- Build a multi-league entry point with per-league parquet outputs, the concat step, and `--force`.
- Make the `xg_assisted` log message league-neutral and update the downloader docstring.
- Parametrise the spikes, add the goals-vs-scores reconciliation, and generate the reference files.
- **Starter-detection cross-check.** Per team per match, the set of starters derived from lineups
  ("Starting XI" `start_reason` plus the 00:00 fallback) must equal the set of players in the
  Starting XI event's `tactics.lineup`. Any mismatch fails the build. Add unit/integration tests on
  3754047 (Swansea: 10 of 11 starters inferred) and 3825596 (Sporting Gijón: 3 inferred).
- **Fallback guard.** Verify that the 00:00 fallback does not mark a bench player with a
  zero-length 00:00 spell (`from == to == "00:00"`) as a starter. Zero-length spells occur
  13–123 times per league. Add a test with a synthetic bench player carrying such a spell.
- `/finish-chunk`.

**C3: Tests, flags and full run**
- Parametrise the integration tests over the reference files.
- Add the `in_percentile_pool` and `multi_competition_season` flags. stats-reviewer reviews them
  before implementation.
- Run the full pipeline on all five leagues, then run `pytest -m integration`.
- stats-reviewer runs its post-run review.
- `/finish-chunk`.

## Verification
- `pytest -m "not integration"` stays green without data.
- Every spike's goals + own goals match the `matches.json` scores.
- `pytest -m integration` passes for every reference file.
- The quality report shows rows per league, the UNKNOWN position rate, and the xG null rate.
