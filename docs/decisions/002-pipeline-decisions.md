# ADR 002 — Pipeline Implementation Decisions

**Date:** 2026-09-26
**Status:** Active (Milestone 1 spike)
**Context:** Milestone 1 — StatsBomb Open Data → Player-Season Parquet pipeline.
Sections 1–5 describe what the code **currently does**.
Section 6 records aggregation principles to be implemented in Milestone 2.

---

## 1. Progressive Pass Definition

**File:** `src/eleveniq/metrics/progressive.py`
**Function:** `is_progressive_pass(start, end, *, outcome, threshold=10.0) -> bool`

### ElevenIQ definition

A completed pass is progressive if ALL of the following hold:

1. `outcome` is `None` — i.e. the pass was completed. In StatsBomb, a completed
   pass has no `pass.outcome` field (Python `None`); any non-None string means the
   pass was not completed and is immediately excluded.

2. The pass does NOT start in the attacking team's own defensive 40%
   (`start_x < 48.0` units — first 40% of the 120-unit StatsBomb pitch).

3. The ball moves ≥ 10 units closer to the opponent's goal centre
   (Euclidean to `(120, 40)`), **OR** the pass **enters** the opponent's
   penalty area from outside it:
   `not in_opponent_penalty_area(*start) and in_opponent_penalty_area(*end)`

   Penalty area bounds: `x ≥ 102, 18 ≤ y ≤ 62`.

```python
# Condition 1 — completed pass only
if outcome is not None:
    return False

# Condition 2 — not in defensive 40%
if start[0] < 48.0:
    return False

# Condition 3 — distance rule OR enters penalty area from outside
dist_reduction = _dist_to_goal(*start) - _dist_to_goal(*end)
entered_box = (
    not _in_opponent_penalty_area(*start) and _in_opponent_penalty_area(*end)
)
return dist_reduction >= threshold or entered_box
```

**Why "enters from outside" for the penalty-area OR clause?**
Without the `not in_opponent_penalty_area(*start)` guard, a backward or lateral
pass entirely inside the box would satisfy the OR clause. For example,
`(110,40) → (108,40)` ends inside the penalty area but is a backward pass with
negative distance reduction. It must NOT count as progressive. The fix: both start
AND end must satisfy `start outside box, end inside box`. Passes where both start
and end are inside the box are judged only by the 10-unit distance rule.

**This is our own definition.** It is not identical to FBref's:

| Aspect | ElevenIQ | FBref |
|--------|----------|-------|
| Distance threshold | 10 **units** (~8.75 m) | 10 **metres** (~11.4 units) — FBref is slightly more stringent |
| Start-zone exclusion | start_x < 48 units (start only) | both start AND end in defensive 40% |
| Penalty-area OR clause | Yes — but only passes that cross INTO the box | No |
| Goal kicks | Included (no pass-type knowledge) — caller must filter if needed | Excluded |

### Completion guard

`outcome` is a **required keyword argument** with no default. Callers that omit it
receive a `TypeError` at call time rather than silently counting incomplete passes.
`build_player_match.py` passes `outcome=event.get("pass", {}).get("outcome", {}).get("name")`
— which is `None` for completed passes and a string for any other outcome.

### Key constants
```python
DEFENSIVE_40_THRESHOLD_X  = 48.0    # first 40% of 120-unit pitch
OPP_PENALTY_AREA_X        = 102.0   # ~15.75 m from opponent goal line
OPP_PENALTY_AREA_Y_MIN    = 18.0    # ~15.3 m from left touchline
OPP_PENALTY_AREA_Y_MAX    = 62.0    # ~15.3 m from right touchline
PROG_PASS_THRESHOLD_UNITS = 10.0    # renamed from PROG_PASS_THRESHOLD_YDS (Chunk B)
```

---

## 2. Progressive Carry Definition

**File:** `src/eleveniq/metrics/progressive.py`
**Function:** `is_progressive_carry(start, end, *, threshold=5.0, min_distance=5.0) -> bool`

### ElevenIQ definition

A carry is progressive if **both** hold:

1. Euclidean distance to the opponent's goal centre decreases by ≥ 5 units (~4.4 m).
2. The total carry distance (straight line) is ≥ 5 units (~4.4 m).

```
dist_reduction = dist_to_goal(start) - dist_to_goal(end)   # condition 1
carry_length   = hypot(end - start)                        # condition 2
progressive    = dist_reduction >= 5.0 AND carry_length >= 5.0
```

**This is our own definition.** Carries from any zone of the pitch are eligible —
there is no start-zone exclusion (unlike progressive passes). FBref uses 5 metres
(~5.7 units) for both thresholds; we use 5 units (~4.4 m), making the definition
slightly more permissive.

The minimum-distance floor (condition 2) prevents near-stationary ball-reception
touches that happen to shift slightly toward goal from being counted.

---

## 3. Minutes-Played Computation

**File:** `src/eleveniq/metrics/minutes.py`
**Functions:** `parse_mm_ss`, `compute_match_duration`, `compute_player_minutes`

### Timestamp format

StatsBomb lineup `positions[].from` and `positions[].to` are `"MM:SS"` strings and
are **match-absolute** (confirmed from match 3788741: Cristante's `from="73:10"`
with `from_period=2` is only coherent as match-absolute, not within-period).
`parse_mm_ss` also handles `"MM:SS.mmm"` and `"H:MM:SS"`.

### Match duration

`compute_match_duration(events)` = max of `event["minute"] + event["second"] / 60`
across all events with `period ≤ 4`. Period 5 (shootout) excluded. Falls back to
90.0 if no valid events exist.

This captures stoppage time and extra time automatically. It represents the last
*recorded* event timestamp, not the actual whistle — may undercount by 15–60
seconds in edge cases (systematic but negligible per match).

### Player minutes

`compute_player_minutes(positions, match_duration, *, dismissal_minute=None)`:
- `to = None` → use `dismissal_minute` if set, else `match_duration`.
- Negative stint durations clamped to 0.
- Empty `positions` array → `0.0` (unused substitute).
- Tactical shifts: multiple stints summed correctly.

### Red card / dismissal handling

StatsBomb records dismissal cards (Red Card, Second Yellow) in **two distinct
event types**:

| Source | Path |
|--------|------|
| `Bad Behaviour` | `event.bad_behaviour.card.name` |
| `Foul Committed` | `event.foul_committed.card.name` |

The two sources have **zero overlap** — each dismissal appears in exactly one
type.  In La Liga 2015/16 there were 18 Bad Behaviour dismissals and 91 Foul
Committed dismissals (109 total).  An implementation that checks only Bad
Behaviour misses 83 % of all dismissals.

`find_dismissal_minute(events, player_id)` checks both sources in a single
chronological pass.  The first matching dismissal found is returned.

#### Two StatsBomb lineup data-quality errors requiring the dismissal fix

Beyond the `to=None` (open-ended stint) case described in Milestone 1, a
second pattern was found during the La Liga 2015/16 pipeline run: **StatsBomb
sometimes records explicit `to` timestamps that run past the card minute**.
This happens when the lineup positions were not corrected after an early
dismissal.  The stints effectively describe the end-of-match formation rather
than the dismissed player's actual time on pitch.

Confirmed examples (both Bad Behaviour source):

| Player | Match | Card min | Uncorrected total | Root cause |
|--------|-------|----------|-------------------|------------|
| Mikel San José Domínguez | 3825686 | 17:59 (Red Card) | 84.7 min | Stints continue through 45:00, 84:41, "Final Whistle" |
| Gustavo Daniel Cabral Cáceres | 3825635 | 56:10 (Second Yellow) | 81.5 min | Stints continue through 66:43, 81:27, "Final Whistle" |

**Fix applied (Milestone 3):** `_iter_stint_contributions` now clamps ALL
stint end-times at `min(end, dismissal_minute)` when `dismissal_minute` is
provided — not only `to=None` stints.  Without a dismissal, explicit `to`
values are left as-is (see note below).

```python
# _iter_stint_contributions — key line after the fix
end = min(raw_end, effective_end) if dismissal_minute is not None else raw_end
```

`compute_player_minutes` in `minutes.py` applies the identical logic to keep
the cross-consistency invariant: `sum(compute_minutes_per_group(...)) ==
compute_player_minutes(...)`.

**Why not clamp against match_duration?** `compute_match_duration` returns the
timestamp of the last recorded event, which may be a few seconds before the
real final whistle.  Clamping explicit `to` values against it could silently
truncate a legitimate substitution recorded 1–2 seconds after the last event.
The clamp is therefore applied **only when a dismissal_minute is known**.

#### Bench dismissals

Three players in La Liga 2015/16 received Bad Behaviour red cards while on the
bench (empty `positions` arrays).  They are correctly excluded from
`player_match_stats.parquet` as 0-minute entries.  Their card events are real;
they represent genuine bench incidents (violent conduct, dissent).

#### Verified result — La Liga 2015/16

After both fixes:

| Metric | Value |
|--------|-------|
| Dismissals detected (both sources) | 109 |
| Bench-only (0 min, excluded from parquet) | 3 |
| In-parquet dismissed players | 106 |
| Players with `minutes_fixed > card_min + 1 min` | **0** |

See `src/eleveniq/pipeline/quality_report.py` for `find_dismissal_minute`,
`check_dismissal_event_quality`, `check_dismissed_player_minutes`, and
`DISMISSAL_TOLERANCE_MINUTES`.

Dismissal logic is tested in `tests/test_minutes.py::TestDismissalMinute` and
`tests/test_quality_report.py`.

---

## 4. Position Group Mapping

**File:** `src/eleveniq/metrics/positions.py`
**Constant:** `POSITION_GROUP_MAP`
**Functions:** `assign_position_group`, `compute_minutes_per_group`, `primary_position_group`

### Group taxonomy

| Group | StatsBomb positions |
|-------|---------------------|
| `GK` | Goalkeeper |
| `CB` | Center Back, Left/Right Center Back |
| `FB-WB` | Left/Right Back, Left/Right Wing Back |
| `DM-CM` | Defensive Midfield, **Center Defensive Midfield**, Left/Right Defensive Midfield, Center Midfield, Left/Right Center Midfield |
| `AM-Winger` | Attacking Midfield, Center Attacking Midfield, Left/Right Midfield, Left/Right Wing, **Left/Right Attacking Midfield** |
| `ST` | Center Forward, Left/Right Center Forward, Secondary Striker |

`UNKNOWN` is the fallback for any unmapped position; unknown values surface in the
quality report rather than crashing.

`OUTFIELD_GROUPS` = `{CB, FB-WB, DM-CM, AM-Winger, ST}` — **GK is explicitly
excluded from all outfield player comparisons.** Percentile calculations must
filter to `OUTFIELD_GROUPS` for outfield scouting. GK-vs-GK comparisons use only
the `GK` group.

### Season-level position group

At season aggregation, position group is determined by **most minutes played per
group**, not by most appearances.

`compute_minutes_per_group(positions, match_duration, *, dismissal_minute=None)`
returns a `dict[str, float]` of group → minutes for one match, respecting
multi-stint tactical shifts and dismissals.

`primary_position_group(minutes_per_group)` returns the group with the highest
total minutes. Tie-break: alphabetically first group name (deterministic secondary
rule; rarely applies). Note: `AM-Winger` sorts before `CB`, which sorts before
`DM-CM` — so an exactly equal split between `DM-CM` and `AM-Winger` resolves to
`AM-Winger`. This is documented as a known edge case; in practice, equal minute
splits across different groups are rare.

### Integration test

`tests/test_positions.py::test_no_unknown_positions_in_pipeline_output` is a
post-run integration test that fails if `player_match_stats.parquet` contains any
`position_group == "UNKNOWN"` rows. It is skipped until the pipeline has been run.

---

## 5. Low-Minute Players and Per-90 Values

The raw `player_match_stats.parquet` and `player_season_stats.parquet` files will
contain extreme per-90 values for players with very few minutes (e.g. a 3-minute
substitute who scores once will have `goals_p90 = 30.0`).

This is **correct** arithmetic, not a bug. The minimum-minutes gate (600–900
minutes, configurable — see CLAUDE.md) exists at the percentile-calculation stage,
not at the pipeline output stage. Raw Parquet files are not filtered.

Any downstream query that reads the Parquet directly **must re-apply the
minimum-minutes threshold** before computing percentiles or rankings. Queries that
skip this filter will produce nonsensical scouting results.

Per-90 columns for players with `minutes_played = 0` are `NULL`, not 0 or
infinity (safe division enforced in `build_player_season.py`).

---

## 6. Season Aggregation Principles (to be implemented in build_player_season.py)

These principles are recorded here now so they are not re-debated during
implementation. None of this is in the current code.

### 6a. Per-90 computation from season totals

Season-level per-90 stats are computed from **season totals**, not averaged from
match-level per-90 values.

```
season_goals_p90 = season_goals / season_minutes_played * 90
```

Never compute as `mean(match_goals_p90)` — this gives equal weight to a 3-minute
match and a 90-minute match and produces incorrect season rates.

`build_player_season.py` must include a regression test proving this:

```
Given: player plays two matches
  Match 1: 3 min, 1 goal
  Match 2: 87 min, 0 goals
Expected: season_goals_p90 = 1 / 90 * 90 = 1.0  (not 15.0 from averaging 30.0 and 0.0)
```

### 6b. Storage: totals, minutes, and per-90 side by side

`player_season_stats.parquet` must contain:
- Raw season totals for all count columns (e.g. `goals`, `shots`, `passes_attempted`)
- `minutes_played` (total)
- Per-90 columns for count metrics (e.g. `goals_p90`, `shots_p90`)
- Derived ratios (e.g. `pass_completion_pct`, `xg_per_shot`)

Never store only derived values — always preserve the numerator and denominator
alongside so metrics can be recalculated without re-downloading source data
(CLAUDE.md requirement).

### 6c. DNA metric preference: volume over rare events

Player DNA profiles (Milestone 4) should prefer **volume metrics** over rare events:

- **Preferred:** shots, xG, key passes, xA (xG of shots created by a pass)
- **Avoid as DNA metrics:** goals, assists, cards

Goals and assists are too rare at match level and too noisy at season level for
most positions. xG and xA are better representations of attacking contribution
because they are continuous, higher-volume, and less dependent on finishing luck
or final-touch attribution.

xA is not currently extracted from the pipeline (it requires the `shot.statsbomb_xg`
value of the *shot* that followed a key pass, not a pre-computed field). This
metric is noted here for Milestone 4 scoping; it is out of scope for Milestone 1.

### 6d. Cards excluded from DNA and similarity

Yellow cards, red cards, and second yellows are **never** included in Player DNA
or similarity calculations. They may be stored as display-only fields in the
player profile, but must not enter any distance or ranking computation.

### 6e. `started` and `started_inferred` columns

`player_match_stats.parquet` includes two boolean columns:

| Column | Meaning |
|--------|---------|
| `started` | Player was in the starting XI |
| `started_inferred` | `started` was set via the fallback rule (not from an explicit "Starting XI" entry) |

`build_player_season.py` must sum `started` to produce the `starts` column used
by the display layer (appearances vs. starts breakdown).

#### Two-step rule (implemented in `_process_match`)

**Step 1 — primary:**  `started = True` if the player has any stint with
`start_reason == "Starting XI"` in the StatsBomb lineup.

**Step 2 — team-level fallback** (applied only when step 1 gives < 11 starters
for a team):  a player who was not already marked started is also counted as
started if **any** of their position stints has `from == "00:00"` and
`from_period == 1` (or `from_period` absent, defaulted to 1).  These players
receive `started_inferred = True`.

**After fallback:** if a team still does not have exactly 11 starters, or has
more than 11, no further guessing is applied.  Both cases are recorded in the
quality report under `issue = "starter_count_mismatch"`.  Fallback applications
are recorded under `issue = "started_inferred_fallback"`.

#### StatsBomb data-quality finding — match 3825596

During the La Liga 2015/16 pipeline run, three Sporting Gijón starters were
found with `start_reason = "Tactical Shift"` instead of `"Starting XI"` in the
StatsBomb lineup data:

| Player | Position group | Minutes |
|--------|---------------|---------|
| Alen Halilović | AM-Winger | 66.27 |
| Alberto García Cabrera | GK | 95.15 |
| Alejandro Menéndez Díez | AM-Winger | 95.15 |

All three have `from = "00:00"` in period 1, confirming they started the match.
The fallback rule correctly marks them `started = True`, `started_inferred = True`.

**Double-counting check (match 3825596):**  StatsBomb records duplicate lineup
entries for these players with `to = "00:00"`, immediately followed by their
real stint.  Duration of the first entry = `max(0, 0:00 − 0:00) = 0` minutes —
the entry contributes **zero minutes**.  The team total (1044.38 min) is below
the maximum of 11 × 95.15 = 1046.65 min, consistent with three substitutions
and no double-counting.

---

## 7. Source Folder Layout vs. Brief's Proposed Structure

The brief (§13, `docs/brief.md`) proposed `app/` + `pipelines/`. The Milestone 1
spike uses `src/eleveniq/` instead:

```
src/
  eleveniq/
    metrics/        # pure functions: progressive, shots, tackles, positions, minutes
    pipeline/       # pipeline stages: build_player_match, build_player_season,
                    #   quality_report
    statsbomb/      # data access: downloader, loader
```

The `eleveniq` package name is used instead of a bare `src` namespace to avoid
editable-install collisions with any other project using `src` as its package root.
`pyproject.toml` specifies `where = ["src"]`, `include = ["eleveniq*"]`.

**Reason for not using `app/` yet:** The `app/` layout assumes a running FastAPI
service with a database. Milestone 1 has no API, no database, and no similarity
engine. Introducing that scaffold now creates empty layers with no implementation
behind them.

When the FastAPI layer is introduced (a later milestone), `app/` will be added.
Analytics functions in `src/eleveniq/metrics/` will be imported from the relevant
`app/analytics/` modules.

| Brief layer | Spike equivalent |
|-------------|-----------------|
| `pipelines/ingestion/` | `src/eleveniq/statsbomb/` (downloader + loader) |
| `pipelines/transformations/` | `src/eleveniq/pipeline/build_player_match.py` |
| `pipelines/features/` | `src/eleveniq/pipeline/build_player_season.py` |
| `app/analytics/` | `src/eleveniq/metrics/` |
| `pipelines/validation/` | `src/eleveniq/pipeline/quality_report.py` |

---

## 8. Key Passes Definition

A pass is counted as a key pass if it carries `shot_assist = true` **or**
`goal_assist = true` in the StatsBomb event.

| Flag | Meaning |
|------|---------|
| `pass.shot_assist` | The pass directly created a shot (whether or not a goal resulted) |
| `pass.goal_assist` | The pass directly preceded a goal (the final ball before the goal-scorer received it) |

**Verified: La Liga 2015/16**

| Category | Count |
|----------|-------|
| `shot_assist` only | 5,763 |
| `goal_assist` only | 722 |
| Both flags | 0 |
| **Total key passes** | **6,485** |

The two flags never co-occur in this dataset (verified). The original pipeline
counted only `shot_assist`, missing 722 `goal_assist`-only passes (11.1 % of
all key passes). This was corrected in Milestone 3.

**Rejected concern (stats review, Milestone 3):** combining `shot_assist` and
`goal_assist` into one counter conflates different event qualities
(`shot_assist` = created a shot; `goal_assist` = preceded a goal).  Decision:
the conflation risk is accepted.  The two flags never co-occur, so each pass is
counted exactly once.  A separate `assists` column (see below) lets downstream
consumers split the two signals if needed.

**`assists` column:** a separate `assists` column counts only `goal_assist`
passes (display-only; not used in DNA or similarity calculations in V1).
In La Liga 2015/16: `assists` total = 722.

**Implementation:** `build_player_match._aggregate_events` increments
`key_passes` when `pass_data.get("shot_assist") or pass_data.get("goal_assist")`,
and separately increments `assists` when `pass_data.get("goal_assist")`.

---

## 9. Goals Column and Own-Goal Encoding

**Verified: La Liga 2015/16**

| Source | Count |
|--------|-------|
| `home_score + away_score` from `matches.json` | 1,043 |
| `Shot outcome=Goal` events — our `goals` column | 1,014 |
| Difference | **29** |

The 29-goal gap is fully explained by **own goals**.  StatsBomb encodes each
own goal as two separate events:

| Event type | `player` field | Purpose |
|-----------|---------------|---------|
| `Own Goal For` | **empty** (no attribution) | Credits the goal to the scoring team |
| `Own Goal Against` | the player who put it in their own net | Attribution |

58 own-goal events / 2 = **29 own goals**.  `1,014 + 29 = 1,043`. ✓

**Current pipeline behaviour:** `_aggregate_events` counts `Shot outcome=Goal`
events only.  Own goals are not captured in any column.  The `goals` column in
`player_match_stats.parquet` therefore counts only goals a player scored into
the *opponent's* net.

**Implications for downstream use:**
- Season-level goal totals summed from the parquet will be 29 short of the
  official total.  This is expected and documented.
- Own goals must never be added to the attacking player's `goals` count (they
  are not attacking actions).
- Own goals should not appear in DNA or similarity calculations (per §6d).
- A future `own_goals_against` column (counting `Own Goal Against` events per
  player-match) would allow display of own-goal history on the player profile,
  but is out of scope for Milestone 3.

**Tech debt (Milestone 3):** Add `own_goals_against` column to
`build_player_match.py` by counting `type.name == "Own Goal Against"` events
per player.  The `Own Goal For` event has no player attribution and is ignored.

---

## 10. Data Source Provenance and Single-Source Rule

### `source` column

Every row in `player_match_stats.parquet` (and the forthcoming
`player_season_stats.parquet`) carries a `source` column recording the ingestion
origin of that row.  Current value: `"statsbomb"`.  Future values (e.g.
`"sportmonks"`) will be added when a new ingestion pipeline is implemented.

The `source` column is added at the row-level in `_process_match` and declared in
`_SCHEMA` as `pl.Utf8`.  It is a provenance tag, not a metric, and must never enter
percentile cohorts, similarity distances, or rarity scores.

### Single-source rule

**Percentiles, similarity, and rarity are computed within a single source only.**

Mixing rows from `"statsbomb"` and a future paid source in a single cohort or
distance calculation is forbidden until all of the following are true:

1. A metric-by-metric mapping document confirms that both sources define the same
   metrics in the same way (e.g. "pass attempt" = one event per attempt in both).
2. A normalisation strategy (e.g. z-score shift, scaling factor) has been validated
   against a population of players who appear in both sources in the same season.
3. The strategy is recorded in a new ADR and the milestone is explicitly opened.

Before those conditions are met, every pipeline query that computes percentiles,
similarity, or rarity must include `WHERE source = '<value>'` (SQL) or the Polars
equivalent `filter(pl.col("source") == ...)`.

**Why:** StatsBomb event-level metrics and Sportmonks season-aggregate metrics are
not guaranteed to be numerically equivalent even when named identically (e.g.
StatsBomb "pass" = one event per attempt; Sportmonks "passes" = seasonal count from
a different data vendor).  Mixing them without validation would produce nonsensical
percentile ranks.

---

## 11. Open Issues

1. **Goal kick exclusion** — **RESOLVED.** Verified across all 6,141 goal kicks in
   La Liga 2015/16: every goal kick starts inside the defensive-40% zone (start_x < 48.0),
   so condition 2 of `is_progressive_pass` eliminates all of them. Zero goal kicks are
   counted as progressive. No `pass_type` filter is needed for accuracy.

2. **Penalty-area OR clause may over-count short passes** — a 1-yard shuffle
   inside the box ending at `(103, 40)` is counted as progressive. By design,
   but worth monitoring in the quality report.

3. **`compute_match_duration` unguarded `second` key** — `e["second"]` raises
   `KeyError` on malformed events (no fallback). Low risk with clean StatsBomb
   data; addressable in Milestone 2.

4. **`Secondary Striker` mapped to `ST`** — in some systems this role sits in the
   AM pool. La Liga 2015/16 has few secondary-striker formations; impact is small.

5. **Lexicographic tie-break in `primary_position_group`** — `AM-Winger` always
   wins a tie against `DM-CM`. Documented and accepted; in practice equal-minute
   splits across groups are rare.

---

## 12. V1 Scope — Outfield Players Only for DNA / Similarity / Gem Finder

Player DNA profiles, similarity search, and Gem Finder are **outfield players
only** in V1.  GK profiles display basic stats (minutes, goals-conceded from
the team record, clean sheets) but do not participate in the DNA radar, the
similarity engine, or Gem Finder rankings.

**Rationale:** GK progressive carries are not a meaningful metric (ADR 002 §2
applies no start-zone exclusion to carries; a GK carrying the ball from their
six-yard box registers as a progressive carry — verified: La Liga 2015/16 GKs
average 3.23 progressive carries/match, identical to strikers).  Combining GK
rows with outfield rows in a percentile cohort or similarity distance would
corrupt both groups.  A dedicated GK model is deferred to V2.

**Implementation:** analytics functions that build percentile cohorts,
similarity matrices, or rarity scores must filter `position_group != 'GK'`
before computing.

---

## 13. Blocks Definition — Defensive Blocks Only

The `blocks` column counts **defensive blocks** only: `type.name == "Block"`
events where `block.offensive` is `False` (or absent) **and** `block.save_block`
is `False` (or absent).

| Sub-field | Meaning | Action |
|-----------|---------|--------|
| `block.offensive = true` | Attacking player deflects a clearance | **Excluded** — not a defensive action |
| `block.save_block = true` | Goalkeeper save recorded as a block | **Excluded** — covered by GK save metrics |
| Neither flag set | Outfield player blocks a shot or cross | **Counted** |

**Verified: La Liga 2015/16 (first 20 matches sample)**

| Category | Count |
|----------|-------|
| Total block events | 738 |
| `block.offensive = true` | 19 |
| `block.save_block = true` | 4 |
| **Defensive blocks counted** | **715** |

**Implementation:** `build_player_match._aggregate_events` guards the `blocks`
counter with `not block_data.get("offensive") and not block_data.get("save_block")`.

---

## 14. Pass Completion Denominator and Unknown-Outcome Passes

### Formula

```
pass_completion_pct = passes_completed / (passes_attempted − passes_unknown_outcome)
```

Unknown-outcome passes are **excluded from the denominator** because their completion
status cannot be determined from the StatsBomb event data.  A pass with
`outcome.name == "Unknown"` is neither confirmed completed nor confirmed incomplete.
Including it in the denominator would inflate the completion percentage for players
whose data happens to contain more unknown outcomes.

`null` is returned when the adjusted denominator is zero (i.e. all recorded passes
have unknown outcome).

### Volume — La Liga 2015/16

| Category | Count | Share |
|----------|-------|-------|
| Total passes | 365,288 | 100% |
| Unknown-outcome passes | 2,465 | 0.7% |

At 0.7%, unknown-outcome passes are a minor but non-zero fraction.  Their
characteristics (length, pass type, pitch zone) are tracked as tech debt in
`docs/milestones.md` — the current exclusion approach is correct until an
inference rule can be validated (see §5 and milestones.md tech debt).

---

## 15. Cross Metrics Definition

### "Cross completed" definition

A cross is **completed** when:
- The pass event has `pass.cross == True` (StatsBomb flag), **AND**
- The pass outcome is `None` — i.e. the pass reached a teammate.

In StatsBomb encoding, a completed pass has no `pass.outcome` field (Python `None`);
any non-`None` string means the cross did not reach a teammate (blocked, out of play,
incomplete, etc.).

**Implementation:** `build_player_match._aggregate_events` checks
`pass_data.get("cross")` and `outcome_name is None` for the cross-completed counter.

### Why `crosses_attempted` has a per-90 column but `crosses_completed` does not

`crosses_attempted` is in `_COUNT_COLS` and therefore gets a `crosses_attempted_p90`
column.  `crosses_completed` is in `_DISPLAY_ONLY_COLS` and has no per-90 sibling.

**Rationale:** the meaningful rate metric for crossing is `cross_completion_pct`
(completed / attempted), not `crosses_completed_p90`.  A player who attempts 3
crosses per 90 and completes 2 (67%) carries different information than a player who
attempts 10 per 90 and completes 3 (30%).  Storing a `crosses_completed_p90` column
alongside `cross_completion_pct` would be redundant and invite misuse (e.g. ranking
by `crosses_completed_p90` without accounting for volume).

`crosses_completed` is retained as a season total so the completion percentage can be
recalculated from raw counts without re-running the pipeline (CLAUDE.md requirement).

### Verified — La Liga 2015/16

| Metric | Value |
|--------|-------|
| `crosses_attempted` total | 9,606 |
| `crosses_completed` total | 2,651 |
| Average `cross_completion_pct` (outfield, 900+ min) | ~27% |

---

## 16. Cross Metrics and Progressive Passes — Intentional Overlap

A completed cross that satisfies the progressive-pass geometry (start_x ≥ 48,
10-yard distance reduction, or enters the penalty area from outside) is counted
simultaneously in both `progressive_passes` **and** `crosses_attempted` /
`crosses_completed`.  This is intentional: a cross is a pass, and if it is also
progressive it is both.

**Implementation:** `build_player_match._aggregate_events` calls
`is_progressive_pass(...)` before the cross-detection branch, so a progressive
cross increments both counters in the same event loop iteration.

**Measured overlap — La Liga 2015/16 (by position group):**

| Group | % of progressive passes that are also crosses |
|-------|-----------------------------------------------|
| CB | 0.7% |
| DM-CM | 2.4% |
| FB-WB | 5.9% |
| ST | 7.0% |
| AM-Winger | 9.1% |

Progressive passes are not dominated by crosses at any position.  Crosses and
progressive passes measure different aspects of play and are stored separately.

**Decision:** No adjustment or de-duplication.  Whether to treat the overlap
differently in DNA feature selection (e.g. subtract crosses from progressive
passes to produce a "non-cross progression" metric) is deferred to Milestone 4.
It must be decided explicitly at that point; the current metrics are correct as
defined.

---

## 17. `meets_min_minutes` — Per-Stint Minutes (Default Until M4)

`meets_min_minutes` in `player_season_stats.parquet` is computed from
`minutes_played`, which is the per-stint total (one team's contribution to the
season).  For a player who transfers mid-season, each stint is evaluated
independently:

- Player at Club A: 500 min → `meets_min_minutes = False` (< 900)
- Player at Club B: 450 min → `meets_min_minutes = False` (< 900)
- Combined via `player_season_minutes_total`: 950 min

Under the current rule, both stints fail the gate even though the player has
meaningful combined-season data.

**This is the default behaviour**, chosen because:

1. The alternative (evaluate against `player_season_minutes_total`) would
   promote stints with very few minutes to eligible status if the player's
   other stint is long.
2. The correct answer depends on how percentile ranking handles split-season
   players (Milestone 4 open question — see `docs/milestones.md`).

**When to revisit:** Milestone 4, before computing percentiles.  The decision
must be recorded in this ADR (extend §6 or add a new section) before any
percentile computation reads `meets_min_minutes`.

`player_season_minutes_total` sums a player's stints **within one competition**
only. Minutes in another league in the same season are not included; see §29.

---

## 18. Cross Metric — Corner Exclusion

`crosses_attempted` is gated on `pass.cross == True` (StatsBomb flag).  Of 3,841
corner kicks in La Liga 2015/16, only **1** carries this flag.  Corners are therefore
de-facto excluded from `crosses_attempted` without any explicit pass-type filter.

**Tech debt — set-piece delivery metric.**  A corner that lands inside the penalty
area (x ≥ 102, 18 ≤ y ≤ 62) is a meaningfully different event from a wasted
corner.  La Liga 2015/16: 3,094 of 3,841 corners (80.6%) end inside the box.
A `corners_in_box` metric is recorded as tech debt in `docs/milestones.md`; it
requires an explicit set-piece delivery model and is not part of the current
player-DNA feature set.

---

## 19. Long-Pass Threshold

A long pass is a pass with `pass.length >= LONG_PASS_THRESHOLD_UNITS` (30 units,
from `config.py`) **and** `pass.type.name NOT IN SET_PIECE_TYPES`
(`{"Corner", "Goal Kick", "Free Kick", "Kick Off"}`).

**Why the set-piece exclusion?**  76.6% of corner kicks and a large fraction of goal
kicks exceed 30 units.  Including them inflates `long_passes_attempted` for wide
players and GKs relative to central players, biasing the metric as a measure of
distribution range.  Kick Off passes are excluded for the same reason: they are
restarts, not distribution actions.

**Chunk B amendment:** "Kick Off" was added to `SET_PIECE_TYPES` when that constant
was promoted from a local variable in `build_player_match.py` to the shared
`entries.py` module.  This removed 89 attempted / 88 completed Kick Off long passes
from the La Liga 2015/16 totals (0.1% of total).

**Data — La Liga 2015/16 (current, after Chunk B):**

| Metric | Value |
|--------|-------|
| `long_passes_attempted` (open play + throw-in) | 65,096 |
| `long_passes_completed` | 37,498 |
| Completion rate | 57.6% |
| Share of all passes | 17.8% |

The 30-unit threshold is a conventional round number near the 78th percentile of
open-play pass lengths.  No external definition is cited; the data-observed share
is the documented justification.  If a different threshold is required for a future
data source, change `LONG_PASS_THRESHOLD_UNITS` in `config.py`; no pipeline logic
needs to change.

---

## 20. Aerial Duel Metrics

**`aerials_won`** — count of events where the sub-event payload contains
`aerial_won=True`.  The flag appears on the winner's own event, not on a separate
duel type.  Relevant event types: Pass, Clearance, Shot, Miscontrol.

**`aerials_lost`** — count of Duel events with `duel.type.name == "Aerial Lost"`.

**Symmetry contract:** `aerials_won.sum() == aerials_lost.sum()` for any complete
competition-season (one winner and one loser per aerial contest).  Enforced by an
integration test.  La Liga 2015/16: 16,526 each.

**`aerial_win_pct`** = aerials_won / (aerials_won + aerials_lost).
Null when denominator < `MIN_AERIAL_CONTESTS` (10, from `config.py`).

`MIN_AERIAL_CONTESTS = 10` is a **starting value** and must be validated at the DNA
milestone by checking: (a) how many outfield 900+ min players fall below the
threshold by position group, and (b) how noisy `aerial_win_pct` is near the
threshold.  If too many eligible players are nulled out, the floor should be
lowered and the revised value recorded here.

---

## 21. Ball Recovery Success

A ball recovery is **successful** when `recovery_failure` is absent from the sub-event
payload.  This includes two cases:

1. The `ball_recovery` key is entirely absent from the event (93.0% of cases).
2. The `ball_recovery` dict is present but contains only `offensive: True` and no
   `recovery_failure` key — attacking players pressing high (116 events in La Liga
   2015/16, 0.3% of recoveries).

`recovery_failure == True` means the player attempted a recovery but did not retain
possession (2,587 events, 6.7%).  `recovery_failure == False` **never appears** in
StatsBomb open data; StatsBomb encodes success by field absence, not an explicit
boolean.

**Implementation guard:** `br = event.get("ball_recovery") or {}; not br.get("recovery_failure")`
evaluates `not <falsy>` = True for both absence cases and `not True` = False only for
the failure case, correctly handling all three scenarios.

**`ball_recoveries_failed`** — display-only column storing the count of failed
recoveries (`recovery_failure == True`, 2,587 events in La Liga 2015/16, 6.7%).
This preserves the denominator alongside `ball_recoveries` so a ball-recovery success
rate can be computed without re-running the pipeline.  No per-90 column is added;
failed recovery rate is a derived ratio, not a raw count metric.

---

## 22. Dispossessed and Miscontrol — Per-90 Position Bias

`dispossessed_p90` and `miscontrols_p90` are arithmetically valid per-90 metrics
but are **position-volume-biased**: strikers and forwards receive more high-pressure
touches and will show higher values regardless of technical quality.

The touch-normalised rate — `miscontrols / (ball_receipts + carries + passes_attempted)`
— is a better quality indicator and is deferred to Milestone 4 when touch-count
columns are available.

**Decision:** `_p90` columns are stored as-is for use within position groups, where
volume bias is partially controlled.  Do **not** use `dispossessed_p90` or
`miscontrols_p90` in cross-position DNA similarity or all-position percentile
ranking without touch normalisation.

---

## 23. Switches

**Definition:** A pass where StatsBomb's own `pass.switch` flag is `True`.
StatsBomb sets this flag; its exact geometric or tactical criterion is not published
in the open-data documentation.  Based on the football meaning, it captures cross-field
passes that change the attacking flank.

**La Liga 2015/16:** 11,615 switches.

**Overlap with `long_passes_attempted`:** Intentional.  A switch is typically a long
pass, so a single event can increment both `switches` and `long_passes_attempted`.
The test `test_long_switch_pass_increments_both_counters` explicitly asserts this
behaviour.  The two metrics capture different aspects of the same pass: `switches` is
about the tactical action; `long_passes_attempted` is about the physical distance.

**Scope:** `switches_p90` is stored for use within position groups.  Do **not** use it
as a proxy for pass length; use `long_passes_attempted_p90` for that purpose.

---

## Stats-Review Decisions — Chunk A

The following concerns raised by the stats-reviewer for Chunk A were reviewed and
rejected.

**Concern #2 — aerial symmetry: only 4 event types checked vs. 9 in the spike.**
The spike searched for `aerial_won=True` across 9 event types as a discovery pass.
The per-type breakdown showed zero counts for Ball Receipt*, Dribble, Goalkeeper,
Block, and Interception in La Liga 2015/16.  Only Pass, Clearance, Shot, and Miscontrol
carry non-zero `aerial_won=True` counts.  The integration test (`aerials_won == 16,526 ==
aerials_lost`) verifies the symmetry contract on the actual data.
**Decision:** The 4-type restriction in `aerials.py` is correct and the symmetry
contract is verified.  No change required.

**Concern #4 — `MIN_AERIAL_CONTESTS = 10` is too low.**
The stats-reviewer correctly notes that the 95% CI around a 0.7 win rate at n=10 is
approximately ±0.28 — wide enough to affect percentile ranking.  This threshold is
deliberately conservative at the pipeline stage: the goal is to preserve data for
players who participate in aerial duels at all, not to guarantee ranking precision.
Percentile ranking precision is enforced separately at the DNA milestone by the
`meets_min_minutes` flag (≥ 900 min) and by minimum-sample validation performed at
that stage.  The threshold value will be revisited and recorded here after Milestone 4
validation.
**Decision:** `MIN_AERIAL_CONTESTS = 10` stands for now.  Validation deferred to the
DNA milestone.

---

## 24. Coordinate Units — Normalised Pitch Units (not yards)

StatsBomb coordinates use a **normalised pitch coordinate system: 120 × 80 units**.
These are not true yards or metres.

**Approximate conversions** on a standard 105 × 68 m pitch:
- 1 unit ≈ 0.875 m lengthwise (105 m / 120 units)
- 1 unit ≈ 0.850 m widthwise (68 m / 80 units)

All distance thresholds in this codebase are expressed in StatsBomb units.
Any user-facing display must convert to metres using the conversions above.
The conversions are approximate because real pitch dimensions vary; do not use
them for precision applications.

**Constant renaming (Chunk B):**
All `*_YDS` suffixes were renamed to `*_UNITS` in `config.py` and
`metrics/progressive.py`.  Threshold **values were not changed**; only names changed.

| Old name | New name | Value |
|---|---|---|
| `LONG_PASS_THRESHOLD_YDS` | `LONG_PASS_THRESHOLD_UNITS` | 30 |
| `PROG_PASS_THRESHOLD_YDS` | `PROG_PASS_THRESHOLD_UNITS` | 10.0 |
| `PROG_CARRY_THRESHOLD_YDS` | `PROG_CARRY_THRESHOLD_UNITS` | 5.0 |
| `PROG_CARRY_MIN_DIST_YDS` | `PROG_CARRY_MIN_DIST_UNITS` | 5.0 |
| *(new)* | `XG_ASSISTED_MAX_CARRY_UNITS` | 10.0 |

---

## 25. xG Assisted

**File:** `src/eleveniq/metrics/xg_assisted.py`
**Function:** `compute_xg_assisted_per_player(events, *, max_carry_units) -> dict[int, tuple[float, float]]`

### Definition

`xg_assisted` for a player = sum of `shot.statsbomb_xg` for all shots in the match
where `pass.assisted_shot_id` resolves to a pass by that player (pass-side traversal).

**Reconciliation:**  When traversed from the shot side via `shot.key_pass_id`, the
total xGA is identical.  La Liga 2015/16: 673.0822 xG from both traversals.

**goal_assist and shot_assist:**  In StatsBomb data, `goal_assist` and `shot_assist`
are mutually exclusive.  Both pass variants carry `assisted_shot_id` — verified on
100% of the 722 `goal_assist`-only passes in La Liga 2015/16.  The join key
`assisted_shot_id` is reliable for both types.

**Collinearity (hypothesis for M4):** `xg_assisted` and `key_passes` share the same
underlying events.  Whether collinearity is severe enough to affect position-group
percentile ranking is a hypothesis to test at Milestone 4 DNA validation, not a
confirmed fact.

---

## 26. xG Assisted Direct

**File:** `src/eleveniq/metrics/xg_assisted.py` (same function, second element of returned tuple)

### Definition

Same as §25 `xg_assisted`, with additional constraints on the shooter's actions after
receiving the pass.  A shot counts as "direct" if ALL of the following hold:

1. The shooter did **not** execute a `Dribble` event between their last
   `"Ball Receipt*"` (exact StatsBomb event type name, asterisk required) and the Shot.
2. The **cumulative distance** of all Carry segments in the window from that last
   "Ball Receipt*" to the Shot (**exclusive on both ends** — the receipt and shot events
   themselves are not included; only events with `receipt_idx < event_idx < shot_idx`,
   same `possession_team.id`) is ≤ `XG_ASSISTED_MAX_CARRY_UNITS` (10 units,
   ~8.75 m lengthwise, from `config.py`).
3. No Ball Receipt* found before the shot → counted as direct (shooter's first touch
   was the shot; no carry window exists).

**Cumulative carry rule:** Two carries of 6 units each = 12 total → NOT direct.
A single carry of 9 units → direct.  Any Dribble by the shooter → NOT direct,
regardless of carry distance.

**Turnover guard:** Events between the receipt and the shot where
`possession_team.id ≠ shooter's possession_team.id` are skipped.
La Liga 2015/16: shot-side == pass-side to 8 decimal places; zero contamination.

**No receipt found:** 6 of 6,485 assisted shots (0.1%) had no preceding
`"Ball Receipt*"` event for the shooter.  These are counted as direct.
Logged at `INFO` level by the pipeline.

**Missing shooter identity:** If a shot event is missing `player.id` or `possession`,
the carry window cannot be determined.  These are credited as direct (conservative
fallback: cannot prove the shot was indirect).  Logged at `WARNING` level.
La Liga 2015/16: zero occurrences.

**Missing `statsbomb_xg`:** If a shot event has no `statsbomb_xg` value (`None`),
the shot is **excluded** from both `xg_assisted` and `xg_assisted_direct`.
No credit is assigned.  Logged at `WARNING` level.
La Liga 2015/16: zero occurrences.

**La Liga 2015/16 totals:**

| Metric | Value |
|---|---|
| `xg_assisted` | 673.0822 |
| `xg_assisted_direct` | 567.4899 |
| Direct fraction | 84.3% |
| Excluded fraction | 15.7% (105.59 xG) |

**Reference figures (NOT the direct/indirect split):** 47% of assisted shots
(3,049 / 6,485) had any carry or dribble (no threshold); those shots carried 34.6%
of xGA (232.91 xG).  After the 10-unit threshold, only 15.7% (105.59 xG) is
excluded, leaving 567.49 xGA direct.

**Arbitrary threshold:** The 10-unit threshold (~8.75 m) is a design choice with no
external definition.  This ADR is the authoritative record of its arbitrary nature.
The threshold is configurable via `XG_ASSISTED_MAX_CARRY_UNITS` in `config.py`.

**Noise note (hypothesis for M4):** `xg_assisted` is event-rare, especially in
DM-CM.  Whether 900 min is sufficient for stable percentile ranking is a hypothesis
to test at Milestone 4, not a known fact.

---

## 27. Final-Third and Box Entry Metrics

**File:** `src/eleveniq/metrics/entries.py`

### Shared constant

```python
SET_PIECE_TYPES: frozenset[str] = frozenset({"Corner", "Free Kick", "Goal Kick", "Kick Off"})
```

This constant is shared with the long-pass exclusion (§19).  One definition, one import.

### final_third_entries_pass

A completed pass where:
- start x < 80.0 (outside the final third)
- end x ≥ 80.0 (enters the final third)
- `pass.type.name not in SET_PIECE_TYPES`

Throw-in, Recovery, and Interception passes are **included** — they represent
territorial progression, not restarts.

**La Liga 2015/16:** 22,454.  Set-piece passes excluded: 1,498.

### final_third_entries_carry

A carry where:
- start x < 80.0 (outside the final third)
- end x ≥ 80.0 (enters the final third)

No type exclusion for carries.  **La Liga 2015/16:** 11,902.

### box_entries_pass

A completed pass where:
- start is NOT in the opponent's penalty area (using `in_opponent_penalty_area` from
  `metrics/progressive.py`, promoted to public in Chunk B)
- end IS in the opponent's penalty area (`x ≥ 102, 18 ≤ y ≤ 62`)
- `pass.type.name not in SET_PIECE_TYPES`

Throw-ins are included (≈4% of pre-exclusion total).  **La Liga 2015/16:** 6,035.
Set-piece passes excluded: 1,464.

### box_entries_carry

A carry where:
- start is NOT in the opponent's penalty area
- end IS in the opponent's penalty area

**La Liga 2015/16:** 3,751.

### Design notes

- **Overlap with progressive passes:** Intentional.  A pass that enters the box
  satisfies the `entered_box` OR-clause in §1 progressive pass definition.  The two
  metrics capture different questions (box entries vs. overall progression).  Same
  precedent as §16 cross/progressive overlap.
- **Separate columns, not combined:** Pass and carry entries are kept as separate
  columns.  They capture qualitatively different actions and are expected to have
  different position-group distributions.
- **23.7% of box-entry passes (pre-exclusion) are crosses.** (La Liga 2015/16)

---

## Stats-Review Decisions — Chunk B

The following concerns were raised by the stats-reviewer for Chunk B.

**Concern — combined pass+carry column.**
Reviewer suggested a single `final_third_entries` or `box_entries` column combining
both pass and carry counts.
**Decision:** REJECTED.  Pass and carry entries are kept as separate columns.
The two actions differ in their position-group distribution and tactical meaning.
A combined column would lose signal.  See §27.

**Concern — xg_assisted_direct possession-chain contamination.**
Reviewer raised the possibility of events from the opposing team contaminating the
carry window between receipt and shot.
**Decision:** ACCEPTED and implemented.  A turnover guard skips any event where
`possession_team.id ≠ shooter's possession_team.id`.  La Liga 2015/16 verification
confirmed zero contamination cases (shot-side == pass-side to 8 decimal places).

**Concern — goal_assist passes and assisted_shot_id reliability.**
Reviewer questioned whether `goal_assist=True` passes always carry `assisted_shot_id`.
**Decision:** Verified in spike.  722 / 722 goal_assist passes (100%) carry
`assisted_shot_id`.  No code change needed; documented in §25.

**Concern — xg_assisted noise in DM-CM.**
Reviewer noted that xg_assisted is event-rare in defensive midfield, potentially
making per-90 values unstable for percentile ranking.
**Decision:** Recorded as a hypothesis to test at Milestone 4 DNA validation.
The 900-min minimum threshold is unchanged.  See §26.

**Concern — percentile inflation from position-group mixing.**
Reviewer noted that team context (Messi's xGA inflated by being at Barcelona) could
bias percentiles.
**Decision:** Position-group percentiles are the primary mitigation.  Team-context
normalisation is deferred to Milestone 4.  No immediate action required.

---

## 28. Season Rows Keyed by IDs; Canonical Display Names

**Date:** 2026-09-28 (plan 003, C3)
**File:** `src/eleveniq/pipeline/build_player_season.py`

### Problem
The season group-by key included `player_name` and `team_name`. StatsBomb spells some
names differently from match to match, so the first 5-league run split one player's
season into two rows whenever a spelling changed. Their minutes were divided between
the rows (distorting the minutes gate), and both rows were wrongly flagged
`multi_team_season`: the flag covered 123 player_ids but 132 names.

### Decision
- **Group by ids only:** `player_id, team_id, competition_id, season_id, source`.
  Names are display attributes attached after aggregation and never affect row identity.
- **Canonical display name** per `(player_id, source)` and per `(team_id, source)`,
  chosen across all seasons and competitions, so a player has one name everywhere:
  1. Normalise: strip leading/trailing whitespace (including tabs), then fix known
     StatsBomb quoting artifacts: `''` → `'`, then `"` → `'` (`N''Golo Kanté`,
     `David N"Gog`). Normalisation is used only for choosing and displaying the name.
  2. The normalised spelling that appears on the most match rows wins. Raw spellings
     that normalise to the same string pool their counts.
  3. Ties resolve to the alphabetically first spelling, so the result never depends
     on row order.
- **`player_name_variants`**: a sorted list of every *raw* spelling of the player's name,
  unchanged by normalisation (for search later).
- **`multi_team_season`** = more than one distinct `team_id` in the player-season.
  Every report counts `player_id`s, not names.
- **Data-quality check:** `find_name_variants()` lists every player_id / team_id with
  more than one raw name. The build logs each one.

### Findings (5-league 2015/16 run)
- **9 player_ids** had two spellings. Eight were quoting artifacts (`N''Golo Kanté` /
  `N'Golo Kanté`, `David N"Gog` / `David N'Gog`, …); one was a genuine variant
  (`Fernando Luiz Rosa` / `Roza`).
- **2 team_ids** also had two names, a larger source of false splits than the players:
  - team 140: `Stade Malherbe Caen` (29 matches) / `Caen` (9 matches)
  - team 147: `Olympique de Marseille` (27 matches) / `Marseille` (11 matches)

  Every player who appeared under both team spellings was split into two rows and wrongly
  flagged as a multi-team player (24 Caen, 25 Marseille).
- In total, 25 raw player spellings contain `''` or `"`: the 9 variant ids plus 16 players
  spelled with `''` in every match (e.g. `Danilo D''Ambrosio`). All now display with a
  single apostrophe. No team name contains either artifact.

### Before / after

Season totals are unchanged: all 45 summed columns (counts, starts, appearances,
minutes, xG) are identical before and after. Season totals still equal match totals
(goals 3,959; shots 38,728; minutes 3,189,152.98; passes 1,512,747).

| Metric | Before | After |
|---|---|---|
| Season rows | 2,686 | 2,628 |
| Multi-team players | 123 player_ids (132 names) | 68 player_ids (68 names) |
| La Liga rows | 546 | 546 (no La Liga name variants) |
| Premier League rows | 566 | 561 |
| Ligue 1 rows | 641 | 588 |
| Rows ≥ 900 min | 1,363 | 1,366 |

Rows ≥ 900 min per position group:

| Group | Before | After | Cause |
|---|---|---|---|
| AM-Winger | 285 | 288 | +3 crossed 900 once merged (Alessandrini, Thauvin, Sarr) |
| DM-CM | 296 | 299 | +1 crossed (Lucas Silva Borges); +2 moved in from other groups (Barrada from AM-Winger, Adéoti from CB) |
| CB | 263 | 261 | Adéoti moved to DM-CM; N'Koulou was counted twice |
| GK | 101 | 100 | Mandanda was counted twice |
| FB-WB | 255 | 255 | — |
| ST | 163 | 163 | — |

- **Crossed the 900-minute gate after merging (5):** Romain Alessandrini (794 → 1,274),
  Florian Thauvin (567 → 978), Bouna Sarr (821 → 1,067), Lucas Silva Borges
  (650 → 1,082), all Marseille; Hervé Bazile (835 → 1,130), Caen. The "before" figure
  is the larger of the two split rows.
- **Counted twice before (both split rows ≥ 900):** Steve Mandanda (GK, 2,340 + 1,030)
  and Nicolas N'Koulou (CB, 2,122 + 935), both Marseille. Each now counts once.
- **Position group changed** because merged minutes changed the group with the most
  minutes: Abdelaziz Barrada (AM-Winger → DM-CM), Jordan Adéoti (CB → DM-CM).
- None of the 9 player name variants crossed the gate. Total minutes after the merge:
  Kanté 3,179; M'Vila 3,336; N'Doye 2,874; Fernando 2,848; O'Shea 2,116;
  M'Bengue 1,969; N'Gog 882; O'Kane 812; M. N'Diaye 162.

### Outer whitespace (decided 2026-09-28)
One raw name, `Daniel Arnaud N''Di`, carries a trailing tab. **Decision:** strip
leading/trailing whitespace (including tabs) before choosing the canonical name, so it
displays as `Daniel Arnaud N'Di`. `player_name_variants` keeps the raw spelling, tab
included. Internal whitespace is left as is. Any further normalisation rule needs a
recorded decision here first.

The stats-reviewer impact review is deferred to the C3 `/finish-chunk`.

---

## 29. Within-Competition Season Columns; Cross-League Movers

**Date:** 2026-09-28 (plan 003, C3; stats-reviewer design review)
**File:** `src/eleveniq/pipeline/build_player_season.py`

### Decision
`multi_team_season`, `is_primary_stint` and `player_season_minutes_total` are
**within-competition**. They are keyed on `(player_id, competition_id, season_id, source)`,
so "player-season" in these columns means player-competition-season. A player who moved
between leagues in one season has one row per league. Each row has
`multi_competition_season = True` (ADR 003 §7), and each row is its league's primary stint.

Example (Serie A → Premier League in January): 2 rows, both `multi_team_season = False`,
both `is_primary_stint = True`, and `player_season_minutes_total` is that league's minutes
only.

### Why not add a cross-competition primary marker now
The obvious rule, argmax of minutes, is wrong when a PARTIAL competition is involved,
because those minutes are only the matches we observed. Kramarić has 33 observed PL
minutes and 10 observed Bundesliga minutes, but he really played far more for Hoffenheim.
Choosing a player's "main" league across coverage classes and league strengths is the M4
cohort question (ADR 003 §1).

### M4 note (must be resolved before any global cohort is built)
- **`is_primary_stint` is NOT unique per `(player_id, season_id, source)`.** In 2015/16
  there are 47 counterexamples (every `multi_competition_season` player). A global
  (cross-league) cohort filtered on `is_primary_stint` + `meets_min_minutes` counts
  3 players twice, because both of their league rows have ≥ 900 minutes: Wahbi Khazri
  (PL 1,105 / L1 1,623), Abdoulaye Doucouré (L1 997 / LL 1,207) and Lamine Koné
  (PL 1,379 / L1 1,606). Any global cohort must deduplicate or decide explicitly.
- **8 players have ≥ 900 minutes across leagues but no single row ≥ 900:** Mario Lemina,
  Abdelhamid El Kaoutari, Mario Suárez, Alhassane Bangoura, Mauro Zárate, Timm Klose,
  Alessandro Diamanti, Mevlüt Erdinç. The last two sums include observed-only
  Bundesliga minutes. This feeds the M4 minutes-gate decision (§17).
- The `build_player_season` `__main__` top-5 diagnostic is filtered on
  `in_percentile_pool` but not deduplicated across leagues.

### Tests
- Unit: Serie A → PL mover; mixed case with a within-league move then a cross-league move
  (3 rows: `multi_team_season` only on the two same-league rows,
  `multi_competition_season` on all three).
- Integration: exactly one `is_primary_stint` per `(player_id, competition_id, season_id, source)`.
