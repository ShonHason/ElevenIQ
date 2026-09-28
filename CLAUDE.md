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
- Percentiles are calculated within position groups (GK,CB, FB/WB, DM/CM, AM/Winger, ST —
  configurable), not across all positions.
- A configurable minimum-minutes threshold (default ~600–900 min) must gate all
  percentile calculations; low-sample players must not appear as elite profiles.
- Expose league-local and global percentiles as separate columns; do NOT build a
  league-strength adjustment model before the data clearly supports it.
- Stats are stored at the player-season level. Never merge a player's career into one
  profile.

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
- Future (when built): api/ -> services/ -> analytics/ -> repositories/,
  all inside src/eleveniq/. No analytics logic inside API endpoints.
- Data: data/raw/ (downloaded, never edited), data/interim/, data/processed/.
  data/ is never committed.
- Typed Python, meaningful naming, configuration over hard-coded values.


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

---

## Agents
The main session plans and writes code. Agents review and report; they
never fix or commit. Fixes happen in the main session after my approval.

| Agent | Use when |
|---|---|
| data-auditor | Evaluating or profiling a data source (new source, league or season) |
| stats-reviewer | BEFORE implementing a new metric, definition or methodology; AFTER a pipeline run on new data |
| qa-tester | After code changes: writes tests from the spec (ADRs, docstrings), not from the implementation |
| code-reviewer | Before every commit, on the git diff |

- Always invoke agents explicitly by name. Do not replace an agent's review
  with your own.
- When an agent finds a new kind of bug, add it to that agent's checklist.

## Commands
Always use the project virtualenv, not the system Python.

```bash
# Setup (once)
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Run the pipeline: all leagues in COMPETITION_SEASONS (existing parts skipped; --force rebuilds)
.venv/bin/python -m eleveniq.pipeline.build_player_match

# Single league (e.g. La Liga 2015/16). The concat always covers every configured league and
# leaves the combined parquet untouched while any part is missing; --skip-concat skips it
.venv/bin/python -m eleveniq.pipeline.build_player_match --competition-id 11 --season-id 27

# After pipeline code changes, rebuild with --force: existing parts are reused, not checked
.venv/bin/python -m eleveniq.pipeline.build_player_match --force

# Fast unit tests (default — no I/O, sub-second)
.venv/bin/python -m pytest tests/ -q -m "not integration"

# Integration tests (require pipeline output in data/interim/)
.venv/bin/python -m pytest tests/ -q -m integration

# Tests with coverage
.venv/bin/python -m pytest tests/ -q -m "not integration" --cov=eleveniq --cov-report=term-missing
```

