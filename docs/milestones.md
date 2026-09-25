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

---

## Milestone 4 — Feature Engineering & Player DNA  (outline)
Per-90 calculation, position-group percentiles, DNA dimension aggregation.
Validate methodology against the questions in brief.md §20 before moving on.

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
