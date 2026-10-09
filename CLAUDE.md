# ElevenIQ — CLAUDE.md

## Project Summary
ElevenIQ is a data-driven football scouting platform that lets users search for players,
view statistical "Player DNA" profiles, find statistically similar players, and surface
statistically unusual profiles via Gem Finder. It is a portfolio project built to
demonstrate data engineering, backend engineering, analytics/ML, and practical AI
integration. Production-quality engineering principles apply throughout; the system is
implemented incrementally, one milestone at a time. See docs/brief.md for full context.

---

## Non-Negotiable Rules

### LLM Role
- LLM = intent only. The LLM parses natural-language queries into structured parameters.
- Python / SQL = truth. The LLM never calculates similarity, invents statistics, decides
  rankings, fabricates filters, or generates explanations from general knowledge.
- Any natural-language explanation must be grounded in backend-calculated metrics.

### Data Sources
- V1 uses StatsBomb Open Data exclusively. Do not write any Sportmonks or other
  paid-API ingestion code until a new ADR is approved and the milestone is open.
- Every row in player_match_stats.parquet and player_season_stats.parquet carries a
  `source` column (e.g. `"statsbomb"`). This is the canonical provenance tag.
- **Percentiles, similarity, and rarity are computed within a single source only.**
  Never mix rows from different sources in a percentile cohort, similarity calculation,
  or rarity score. Cross-source comparisons require an explicit normalisation strategy
  and a new ADR before any implementation.

### Statistical Principles
- Use per-90 metrics and percentiles for player comparisons, not raw totals.
- Always preserve numerators, denominators, and minutes alongside derived figures so
  metrics can be recalculated without re-downloading source data.
- Percentiles are calculated within position groups (`GK`, `CB`, `FB-WB`, `DM-CM`,
  `AM-Winger`, `ST`; the names used in the data, configurable), not across all positions.
- A configurable minimum-minutes threshold (default 900 min, `MIN_MINUTES_THRESHOLD` in
  config.py) must gate all percentile calculations; low-sample players must not appear as
  elite profiles.
- Expose league-local and global percentiles as separate columns; do NOT build a
  league-strength adjustment model before the data clearly supports it.
- Season stats are stored at the player-team-competition-season grain (one row per
  `player_id, team_id, competition_id, season_id, source`). Never merge a player's career
  into one profile.

### Data
- Never scrape websites or use data in a way that violates a source's terms of service.
  Only use sources with a clear license that explicitly permits use in a public portfolio
  project.

### Infrastructure
- No Kafka, Redis, vector databases, or other infrastructure unless a concrete requirement
  justifies it. For tens of thousands of players and ~20–30 features, NumPy/scikit-learn
  is sufficient.
- Preferred stack (from brief, can be challenged): Python · FastAPI · PostgreSQL · Polars
  · scikit-learn · Pydantic · Alembic · Docker Compose. Challenge additions; don't accept
  them because they sound impressive.

### Code Organisation
- Package: src/eleveniq/ (imports: `from eleveniq...`). Current modules:
  - statsbomb/  - source adapter: download + load raw StatsBomb files
  - metrics/    - pure metric functions (minutes, progressive, positions, ...)
  - pipeline/   - build stages (player_match, player_season) + quality checks
  - analytics/  - metric registry (registry.py) derived from committed measurements;
                  cohorts.py, percentiles.py, dna.py (DNA vector, dimension scores, pair distance)
- tools/ (outside the package): dev tools that import eleveniq, e.g. compare_page.py
  (M4e comparison page). Counts as logic in /finish-chunk (qa-tester runs).
- Future (when built): api/ -> services/ -> analytics/ -> repositories/,
  all inside src/eleveniq/. No analytics logic inside API endpoints.
- Data: data/raw/ (downloaded, never edited), data/interim/, data/processed/.
  data/ is never committed.
- Typed Python, meaningful naming, configuration over hard-coded values.


### Design (frontend)
- UI work follows docs/DESIGN.md. It overrides any design skill on conflict.
- Skill leadership: our rules (ADR 004 §11.5, N1–N3, the words table, docs/DESIGN.md) lead
  data display; the dataviz skill leads colour scales and charts (every palette passes its
  validator); apple-design leads typography, materials, feedback and panel motion (skip its
  'Initial Response' greeting); frontend-design's anti-default list is a review checklist only
  (restated in our own words in docs/DESIGN.md §10).
- Data must stay readable: no springy or animated data values. Numbers, percentiles, bars,
  radar vertices and map points render at their final value: no count-up, spring or overshoot.
- User-facing text comes only from the words table (registry.REASON_TEXT, export/words.py).
  No copied strings in pages or components.
- New design skills or UI libraries need my approval before install.

### Out of Scope (MVP)
Transfer prediction, market-value prediction, tactical team-fit, live match analytics,
future-potential prediction. Do not add these without explicit re-scoping.

---

## How We Work
- **One milestone at a time.** Do not begin the next milestone until the current one is
  complete, reviewed, and signed off.
- **Plan before code.** For each milestone: explain the problem → propose the design →
  discuss tradeoffs → get agreement → implement → test → review.
- **Stop and report after each milestone.** Summarise what was built, what was validated,
  and what decisions were made before proceeding.
- **Record methodology decisions.** Every significant architectural or statistical
  decision goes into docs/decisions/ as a numbered ADR (e.g. 001-data-source.md).
- **Challenge weak assumptions.** If a design choice is questionable, say so rather than
  implementing it silently.
- **At the end of each chunk, run /finish-chunk. Never commit outside it**, with one
  exception: a commit that touches only docs/, spikes/ and/or CLAUDE.md (no src/ or
  tests/ changes) may be committed directly after explicit user approval, without
  /finish-chunk. Everything else still goes through /finish-chunk.
- **/finish-chunk is tiered:** see "Speed rules" below (the one place the reviewer tiering
  lives; .claude/skills/finish-chunk/SKILL.md implements it).
- **Claude never pushes.** Claude commits (and merges locally) only after my explicit
  approval; I push manually. This covers every branch, tags and force pushes.
  Remote: `origin` = github.com/ShonHason/ElevenIQ (private).

### Speed rules (from plan 006 on; user, 2026-10-09)
- **Pre-register once.** A milestone's decisions are pre-registered in one document at the
  start of the milestone. The chunks then run under standing approval.
- **Standing approval** covers moving from chunk to chunk within the approved plan. Stop only
  for:
  - a STOP-rule failure;
  - an unexpected change;
  - a decision that is not in the plan;
  - a commit proposal. Commit approval stays typed by me; I push.
- **Reviewers** (`/finish-chunk`, .claude/skills/finish-chunk/SKILL.md):
  - code-reviewer: every chunk, scoped to the chunk's git diff only;
  - qa-tester: only when src/ or tools/ logic changes;
  - stats-reviewer: only when metrics, cohorts, the registry or statistical claims change,
    and at milestone sign-off. UI-only chunks skip it. If the method was reviewed before the
    code, the end-of-chunk run reviews the produced output only;
  - after review fixes: code-reviewer on the fix delta only, no full re-run;
  - fast tests, coverage and (for pipeline changes) integration tests run as before;
  - the report states which agents ran and why each skipped one was skipped.
- **One fix round per chunk;** leftovers go to the gated backlog.
- **Reports:** a results table, the decisions needed, and the commit message. No narration.
- **Web chunks:** Playwright tests on fixtures. I do a visual review from screenshots at the
  end of each W step, not per chunk.
- **Every existing hard rule is unchanged:**
  - Claude never pushes;
  - data/ is never committed;
  - no scraping;
  - expected values are never changed to make tests pass;
  - the LLM never produces statistics;
  - spikes never import eleveniq.

---

## Agents
The main session plans and writes code. Agents review and report; they
never fix or commit. Fixes happen in the main session after my approval.

| Agent | Use when |
|---|---|
| data-auditor | Evaluating or profiling a data source (new source, league or season) |
| stats-reviewer | BEFORE implementing a new metric, definition or methodology; AFTER a pipeline run on new data (output only, if the method was reviewed before code) |
| qa-tester | After src/ logic changes: writes tests from the spec (ADRs, docstrings), not from the implementation |
| code-reviewer | Before every commit, on the chunk's git diff only (after fixes: the fix delta only) |

- Always invoke agents explicitly by name. Do not replace an agent's review
  with your own.
- When an agent finds a new kind of bug, add it to that agent's checklist.

## Commands
Always use the project virtualenv, not the system Python.

```bash
# Setup (once)
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Download raw StatsBomb data into data/raw/ (all configured leagues; existing files skipped)
.venv/bin/python -m eleveniq.statsbomb.downloader
.venv/bin/python -m eleveniq.statsbomb.downloader --competition-id 11 --season-id 27

# Run the pipeline: all leagues in COMPETITION_SEASONS (existing parts skipped; --force rebuilds)
.venv/bin/python -m eleveniq.pipeline.build_player_match

# Single league (e.g. La Liga 2015/16). The concat always covers every configured league and
# leaves the combined parquet untouched while any part is missing; --skip-concat skips it
.venv/bin/python -m eleveniq.pipeline.build_player_match --competition-id 11 --season-id 27

# After pipeline code changes, rebuild with --force: existing parts are reused, not checked
.venv/bin/python -m eleveniq.pipeline.build_player_match --force

# Season aggregation: data/interim/player_match_stats.parquet → data/processed/player_season_stats.parquet
.venv/bin/python -m eleveniq.pipeline.build_player_season

# Percentiles (ADR 004 §11): player_season_stats.parquet → data/processed/player_season_percentiles.parquet.
# Refuses to run unless the season parquet is the one the registry measured (re-run
# spikes/m4_decision_measurements.py --write-json after rebuilding the season stats)
.venv/bin/python -m eleveniq.pipeline.build_percentiles

# Measurements (schema 3, plan 005 M4g4d): the writer embeds the M4g4c / M4g4c2 engine outputs
# (defaults data/interim/m4g4c/ and data/interim/m4g4c2/trust.json; run those engines first)
.venv/bin/python spikes/m4_decision_measurements.py --write-json src/eleveniq/analytics/measurements/statsbomb_27.json

# M4g4 STOP rule: measurements + registry snapshots before/after vs spikes/expected_m4g4_flips.json
.venv/bin/python spikes/m4g4_before_after.py --before-measurements data/interim/m4g4d_snapshot/measurements_old.json --after-measurements src/eleveniq/analytics/measurements/statsbomb_27.json --before-registry data/interim/m4g4d_snapshot/registry.json --after-registry data/interim/m4g4d_after/registry.json

# Independent check of the percentiles output (no eleveniq imports)
.venv/bin/python spikes/m4d_percentiles_reference.py --check-output data/processed/player_season_percentiles.parquet

# DNA (ADR 004 §12): season stats + percentiles → data/processed/player_dna.parquet and
# player_dna_dimensions.parquet. Refuses percentiles from another registry version or season parquet
.venv/bin/python -m eleveniq.pipeline.build_player_dna

# Independent check of the DNA output (no eleveniq imports; reads the dimensions file alongside)
.venv/bin/python spikes/m4f_dna_reference.py --check-output data/processed/player_dna.parquet

# M4g2 multi-split reliability: independent check of the measurements and the cells report;
# --write-expected writes spikes/expected_m4g2_flips.json from a registry snapshot BEFORE a registry change
.venv/bin/python spikes/m4g2_reliability_reference.py --check-output src/eleveniq/analytics/measurements/statsbomb_27.json --snapshot data/interim/m4g2_snapshot

# Registry before/after (STOP rule): dump the registry before and after a change, then compare
.venv/bin/python tools/registry_snapshot.py --out data/interim/m4g2_after/registry.json
.venv/bin/python spikes/m4g2_before_after.py --before data/interim/m4g2_snapshot/registry.json --after data/interim/m4g2_after/registry.json --measurements src/eleveniq/analytics/measurements/statsbomb_27.json

# Style maps (M4g1, re-run after any DNA change) and the M4g2 reliability review
.venv/bin/python spikes/m4g_style_map.py
.venv/bin/python spikes/m4g2_reliability_review.py

# Comparison page (M4e): 2–3 players, one season → data/processed/reports/*.html
# (--player is a player_id, player_id@team_id, or a name fragment)
.venv/bin/python tools/compare_page.py --season-id 27 --player 3244 --player "Wes Morgan"

# Web export (plan 006 W1, ADR 005): parquets + registry → export/v1/*.json (git-ignored),
# validated against contract/v1/*.schema.json, byte-identical on re-run
.venv/bin/python -m eleveniq.export.build_export --out export/v1

# Rewrite the M4e golden page (prints a diff summary; call the change out in the commit)
.venv/bin/python -m pytest tests/test_compare_page_integration.py -q -m integration --update-golden

# Fast unit tests (default — no I/O, sub-second)
.venv/bin/python -m pytest tests/ -q -m "not integration"

# Integration tests (require data/raw/, data/interim/ and data/processed/: download, then
# run both pipeline stages; tests whose inputs are missing are skipped)
.venv/bin/python -m pytest tests/ -q -m integration

# Tests with coverage
.venv/bin/python -m pytest tests/ -q -m "not integration" --cov=eleveniq --cov-report=term-missing
```

