# ElevenIQ — Project Context & Engineering Brief

I want to build **ElevenIQ**, a data-driven football scouting platform.

The main goal of the project is to demonstrate strong **data engineering, backend engineering, analytics/ML, and practical AI integration** skills through a real football analytics product.

This is a portfolio project, but I want it designed with production-quality engineering principles rather than as a quick demo.

Do NOT try to build the entire application immediately.

First understand the product, challenge weak assumptions, help me design the architecture and data model, and then we will implement it incrementally.

---

# 1. Product Vision

ElevenIQ helps users explore football players across many leagues using statistical profiles.

The product has two primary discovery workflows:

## A. Player Search & Similarity

A user should be able to:

- Search for a player.
- View the player's statistical profile.
- View a Player DNA representation.
- Find statistically similar players.
- Filter similar players by:
  - age
  - position
  - league
  - competition
  - minimum minutes played
  - season
- Compare multiple players.
- Understand WHY two players are considered similar.

Example:

> Find players similar to Pedri.

More advanced example:

> Find U23 midfielders similar to Pedri, but prioritize defensive contribution.

The similarity calculation must be deterministic and based on our statistical model.

An LLM must NOT decide which players are similar.

---

# 2. Gem Finder

The second major feature is **Gem Finder**.

Gem Finder should identify players with statistically unusual or rare profiles.

This is NOT intended to predict future stars.

We should not claim that a player is "underrated", "the next superstar", or guaranteed to succeed.

Instead, we identify objectively unusual statistical profiles within a defined comparison population.

Potential signals include:

### Percentile rarity

Example:

- Progressive carries: P96
- Progressive passes: P94
- Chance creation: P89

### Rare combinations

Example:

Only 1.8% of comparable midfielders may simultaneously have:

- progression > P90
- carrying > P90
- defensive activity > P80

### Profile uniqueness

Possible methods:

- K-nearest-neighbor distance
- Local Outlier Factor
- distance from common player-profile clusters
- other appropriate multivariate outlier techniques

We should investigate and validate the statistical methodology before choosing one.

The UI should explain WHY a player was surfaced rather than presenting an unexplained black-box score.

For example:

> Only 7 of 483 comparable midfielders rank above the 85th percentile in progression, carrying and defensive activity.

---

# 3. Player DNA

Player DNA is the analytical foundation shared by Similarity Search and Gem Finder.

A player's profile should be represented by approximately 15–30 underlying metrics grouped into higher-level dimensions.

Possible dimensions:

- Passing
- Progression
- Creativity / Creation
- Ball Carrying
- Defending
- Goal Threat
- Ball Security

These dimensions are NOT finalized.

We must determine the exact metrics based on:

1. football relevance
2. data availability
3. consistency across leagues
4. statistical usefulness
5. position-specific relevance

Do not arbitrarily invent formulas.

We should document exactly how every DNA dimension is calculated.

---

# 4. Statistical Principles

Raw totals should generally NOT be used for player comparisons.

Where appropriate, use:

- per-90 metrics
- ratios
- percentiles
- standardized features

We need to account for sample size.

Players with very few minutes should not appear as elite statistical profiles because of tiny samples.

A configurable minimum-minutes threshold should exist.

A reasonable initial default might be around 600–900 minutes, but this should be configurable.

---

# 5. Position Normalization

Do not compare every football player against the same population.

Initially consider position groups such as:

- CB
- FB / WB
- DM / CM
- AM / Winger
- ST

A midfielder's defensive percentile should generally be calculated relative to relevant midfielders rather than all footballers.

The exact grouping should remain configurable.

---

# 6. League Normalization

This is an important problem.

A 95th-percentile player in a weaker league is not automatically equivalent to a 95th-percentile player in the Premier League.

Initially I want us to preserve multiple views where possible:

- raw/per-90 values
- league + position percentile
- global position percentile
- potentially age + position percentile

Example:

Player X:

- League CM percentile: P97
- Global CM percentile: P84

Do NOT build a complicated league-strength model in V1 unless the data clearly supports it.

But design the system so that league-strength adjustment could be added later.

---

# 7. Seasons

Player statistics should primarily be stored at the **player-season level**.

Do NOT combine a player's entire career into one profile.

Default comparisons should use the latest completed season.

Historical seasons should be preserved when available.

Conceptually:

Player
- 2023/24 DNA
- 2024/25 DNA
- 2025/26 DNA

For the initial project, the latest completed season can be the primary comparison universe.

If historical data is available, ideally retain 2–5 seasons.

This would later allow queries such as:

> Find players similar to Player X's 2023/24 profile.

Do not prioritize recent-form / last-10-match analytics in V1.

---

# 8. League Coverage

League diversity is extremely important.

The project should NOT only contain the Big Five European leagues.

Both Similarity Search and Gem Finder become substantially more interesting when players from development and secondary leagues are included.

Ideally the dataset should eventually include leagues such as:

Big Five:
- England
- Spain
- Germany
- Italy
- France

Strong secondary leagues:
- Netherlands
- Portugal
- Belgium

Development / scouting markets:
- Austria
- Denmark
- Norway
- Sweden
- Switzerland
- Croatia
- Serbia
- Czech Republic
- Poland

Potential non-European markets:
- Brazil
- Argentina
- Uruguay
- MLS

This is NOT a strict required list.

Data quality and metric consistency are more important than maximizing league count.

For the MVP, approximately **10–20 leagues with consistent metrics** would be much better than 50 leagues with inconsistent data.

We particularly want meaningful coverage OUTSIDE the Big Five.

---

# 9. Data Source Requirements

We have NOT selected the final data source yet.

This is one of the first major tasks.

I strongly prefer a free/open dataset for the portfolio project.

A live API is NOT required.

A high-quality static dataset is perfectly acceptable.

Potential sources may include:

- StatsBomb Open Data
- public football datasets
- appropriately licensed FBref-derived datasets
- other open football datasets

Do NOT scrape websites or violate terms of service.

Before implementing the main pipeline, help me evaluate candidate datasets based on:

- number of leagues
- number of players
- season coverage
- minutes played
- positions
- age / DOB
- available metrics
- consistency across leagues
- data freshness
- licensing / allowed usage
- missing values
- identifiers
- historical seasons

Target dataset characteristics:

- thousands of players
- meaningful representation outside Big Five
- 20+ useful player metrics if possible
- minutes played
- position
- team
- competition
- season
- age/DOB if possible

One consistent statistical provider is preferable to mixing the same metrics from multiple providers with different definitions.

---

# 10. Data Pipeline

I want a proper reproducible data pipeline.

Conceptually:

Raw data
→ ingestion
→ schema validation
→ cleaning
→ entity normalization
→ aggregation
→ feature engineering
→ per-90 metrics
→ position normalization
→ percentiles
→ Player DNA
→ similarity features
→ rarity features
→ curated database
→ API

Each major stage should be independently testable and rerunnable.

Preserve raw inputs whenever practical.

Do not store only derived metrics.

For example, where possible preserve the numerator, denominator and minutes so derived features can be recalculated without downloading the original dataset again.

---

# 11. Suggested Data Architecture

My current preference is:

Raw source files
→ Parquet
→ Polars processing
→ PostgreSQL curated layer
→ FastAPI
→ frontend

But challenge this if there is a better justified design.

PostgreSQL should be the main curated application database because most application queries are relational and analytical.

Potential entities:

- players
- teams
- competitions
- seasons
- player_season_stats
- player_percentiles
- player_dna
- player_rarity

Potential player_season_stats fields:

- player_id
- team_id
- competition_id
- season_id
- position_group
- age
- minutes
- appearances

plus raw and normalized football metrics.

Keep the schema normalized where appropriate without making ordinary analytical queries unnecessarily complicated.

JSONB may be used for flexible/raw attributes when justified.

---

# 12. Backend Architecture

Preferred backend stack:

- Python
- FastAPI
- PostgreSQL
- Polars
- NumPy
- scikit-learn
- Pydantic
- SQLAlchemy or another well-justified database layer
- Alembic for migrations
- Docker / Docker Compose

Do NOT introduce infrastructure just because it sounds impressive.

For example:

- No Kafka unless there is a real streaming requirement.
- No Redis until caching is actually useful.
- No vector database merely because we calculate similarity.

For a dataset containing thousands or tens of thousands of players with roughly 20–30 numerical features, NumPy/scikit-learn should be more than sufficient initially.

pgvector can be considered later if it provides a real benefit.

---

# 13. Backend Code Organization

Keep API routing separate from business logic and analytics.

A possible structure:

```
app/
    api/
        players.py
        scouting.py
        gems.py
    services/
        player_service.py
        scouting_service.py
        gem_service.py
    analytics/
        dna/
        similarity/
        rarity/
        normalization/
    repositories/
    models/
    schemas/
    core/

pipelines/
    ingestion/
    validation/
    transformations/
    features/

tests/
```

Do not put large analytical functions directly inside FastAPI endpoints.

---

# 14. Similarity Engine

Initial similarity can use cosine similarity over appropriately normalized Player DNA/features.

However, we should validate whether cosine similarity is actually appropriate for our feature representation.

The similarity engine should support weighted dimensions.

Example — normal search:

- Passing: 1.0
- Progression: 1.0
- Creativity: 1.0
- Carrying: 1.0
- Defending: 1.0

Query:

> Similar to Pedri but more defensive.

could increase the weight of defensive dimensions.

The exact implementation must remain transparent and deterministic.

The system should be able to explain:

- overall similarity
- strongest similarities
- biggest differences

---

# 15. Natural Language Scout Search

I want an LLM layer, but it must have a clearly defined responsibility.

Principle:

**LLM → intent**
**Python/SQL → truth**

Example input:

> Find U23 midfielders similar to Pedri with stronger defensive numbers and at least 900 minutes.

The LLM may transform this into a structured query such as:

```json
{
  "reference_player": "Pedri",
  "positions": ["DM", "CM"],
  "max_age": 23,
  "min_minutes": 900,
  "weights": {
    "defending": 1.5
  }
}
```

The backend then validates the structure and performs the actual database query and similarity calculation.

The LLM must NOT:

- invent player statistics
- calculate similarity itself
- decide rankings from general football knowledge
- invent unavailable filters
- fabricate explanations

Any natural-language explanation generated by an LLM must be grounded in calculated data supplied by the backend.

---

# 16. Gem Search via Natural Language

The same approach can support Gem Finder.

Example:

> Find unusual U21 central midfielders with elite ball progression.

The LLM should only parse this into structured constraints.

The Gem Engine performs the actual ranking.

---

# 17. API Ideas

Potential endpoints:

- GET /players
- GET /players/{player_id}
- GET /players/{player_id}/dna
- GET /players/{player_id}/similar
- GET /players/{player_id}/unique-traits
- POST /scout/search
- POST /gems/search
- GET /gems/{player_id}/explanation
- GET /compare

These are conceptual, not final.

Design the API after the data model and workflows are understood.

---

# 18. Frontend

The frontend is important but should NOT drive the architecture initially.

Likely stack:

- React / Next.js
- TypeScript

Primary screens:

### Discover

Player search and natural-language scouting search.

### Player Profile

- player information
- season
- team
- league
- Player DNA
- percentiles
- key metrics
- similar players

### Compare

Compare 2–3 players across DNA dimensions and underlying metrics.

### Gems

Filters plus ranked unusual profiles with clear statistical explanations.

Possible visualizations:

- radar chart
- percentile bars
- similarity breakdown
- rarity indicators

A PCA/UMAP Player Map could be a strong later feature but is NOT required for the first MVP.

---

# 19. Explainability

This is important throughout the application.

Do not simply display:

Similarity: 89%

Explain it.

For example:

Strong similarities:
- progressive passing
- ball retention
- chance creation

Main differences:
- Player B carries more frequently
- Player A contributes more defensively

Likewise Gem Finder should explain why the profile is statistically unusual.

All explanations must be derived from actual calculated metrics.

---

# 20. Validation

We need to be able to defend the methodology.

During development, explicitly test questions such as:

- Why per 90?
- What happens with low-minute players?
- How sensitive are rankings to the minimum-minutes threshold?
- How are positions grouped?
- How are percentiles calculated?
- What happens when a player changes clubs?
- What happens when a player plays multiple positions?
- How are different leagues compared?
- Which features dominate similarity?
- Are any features highly correlated and therefore effectively double-counted?
- Does cosine similarity produce intuitively reasonable neighbors?
- How stable are Gem results?
- Are outliers caused by data errors?
- How do we validate rare combinations?

Create tests and exploratory analyses for these issues rather than assuming the methodology works.

---

# 21. Engineering Quality

Treat this as a serious engineering project.

I want:

- clear separation of concerns
- typed Python
- meaningful naming
- configuration instead of hard-coded values
- reproducible pipelines
- logging
- validation
- migrations
- unit tests
- integration tests for important workflows
- deterministic analytics where possible
- documented assumptions
- good README/documentation
- Dockerized local environment

Avoid unnecessary abstractions and premature optimization.

---

# 22. Scope Control

The MVP is:

1. ingest football player data
2. clean and validate it
3. create player-season features
4. calculate normalized metrics / percentiles
5. generate Player DNA
6. display player profiles
7. find similar players
8. explain similarity
9. compare players
10. identify statistically unusual profiles with Gem Finder
11. explain why those profiles are unusual
12. support basic natural-language scouting queries

Do NOT add:

- transfer success prediction
- transfer adaptation modeling
- future-potential prediction
- market-value prediction
- tactical team-fit prediction
- live match analytics
- complex recommendation systems

Those may be future versions.

---

# 23. How I Want You to Work With Me

Do not immediately generate hundreds of files.

I want to understand and participate in the engineering decisions.

For each major stage:

1. explain what problem we are solving
2. propose the design
3. explain important tradeoffs
4. challenge assumptions where necessary
5. get the architecture clear
6. implement a small coherent piece
7. test it
8. review the result
9. then continue

If you see a bad architectural or statistical decision, tell me rather than blindly following it.

Do not over-engineer.

Prefer a simple solution that we can explain and validate.

---

# 24. First Task

Do NOT build the application yet.

Start by turning this brief into a concrete technical plan.

I want you to produce:

1. A concise definition of the MVP.
2. The core user flows.
3. The minimum data requirements.
4. A proposed feature/metric taxonomy for each position group.
5. A proposed database model.
6. A proposed data-pipeline architecture.
7. A proposed similarity methodology.
8. 2–3 candidate Gem/rarity methodologies, including their advantages and weaknesses.
9. Key statistical risks and how we should validate them.
10. A recommended repository structure.
11. An implementation plan divided into small milestones.
12. The decisions that MUST be made before writing production code.

Most importantly, identify the **highest-risk unknowns**.

I suspect data availability and metric consistency across 10–20 leagues will be one of them.

If so, the first implementation milestone should probably be a small data feasibility spike rather than application development.

Do not start coding until you have presented the plan and identified what we should validate first.
