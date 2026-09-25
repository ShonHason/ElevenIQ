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

### Statistical Principles
- Use per-90 metrics and percentiles for player comparisons, not raw totals.
- Always preserve numerators, denominators, and minutes alongside derived figures so
  metrics can be recalculated without re-downloading source data.
- Percentiles are calculated within position groups (CB, FB/WB, DM/CM, AM/Winger, ST —
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
- Strict layering: api/ → services/ → analytics/ → repositories/
- No large analytical functions inside FastAPI endpoints.
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

---

## Commands
TODO — tooling not yet chosen. Fill in once Makefile / scripts / CI are established.
