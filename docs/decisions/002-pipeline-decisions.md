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
   (`start_x < 48.0` yards — first 40% of the 120-yard StatsBomb pitch).

3. The ball moves ≥ 10 yards closer to the opponent's goal centre
   (Euclidean to `(120, 40)`), **OR** the pass **enters** the opponent's
   penalty area from outside it:
   `not _in_opponent_penalty_area(*start) and _in_opponent_penalty_area(*end)`

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
Without the `not _in_opponent_penalty_area(*start)` guard, a backward or lateral
pass entirely inside the box would satisfy the OR clause. For example,
`(110,40) → (108,40)` ends inside the penalty area but is a backward pass with
negative distance reduction. It must NOT count as progressive. The fix: both start
AND end must satisfy `start outside box, end inside box`. Passes where both start
and end are inside the box are judged only by the 10-yard distance rule.

**This is our own definition.** It is not identical to FBref's:

| Aspect | ElevenIQ | FBref |
|--------|----------|-------|
| Distance threshold | 10 **yards** | 10 **metres** (~10.9 yds) — we are slightly more permissive |
| Start-zone exclusion | start_x < 48 (start only) | both start AND end in defensive 40% |
| Penalty-area OR clause | Yes — but only passes that cross INTO the box | No |
| Goal kicks | Included (no pass-type knowledge) — caller must filter if needed | Excluded |

### Completion guard

`outcome` is a **required keyword argument** with no default. Callers that omit it
receive a `TypeError` at call time rather than silently counting incomplete passes.
`build_player_match.py` passes `outcome=event.get("pass", {}).get("outcome", {}).get("name")`
— which is `None` for completed passes and a string for any other outcome.

### Key constants
```python
DEFENSIVE_40_THRESHOLD_X = 48.0    # first 40% of 120-yard pitch
OPP_PENALTY_AREA_X       = 102.0   # 18 yards from opponent goal line
OPP_PENALTY_AREA_Y_MIN   = 18.0    # 22 yards left of goal centre
OPP_PENALTY_AREA_Y_MAX   = 62.0    # 22 yards right of goal centre
PROG_PASS_THRESHOLD_YDS  = 10.0
```

---

## 2. Progressive Carry Definition

**File:** `src/eleveniq/metrics/progressive.py`
**Function:** `is_progressive_carry(start, end, *, threshold=5.0, min_distance=5.0) -> bool`

### ElevenIQ definition

A carry is progressive if **both** hold:

1. Euclidean distance to the opponent's goal centre decreases by ≥ 5 yards.
2. The total carry distance (straight line) is ≥ 5 yards.

```
dist_reduction = dist_to_goal(start) - dist_to_goal(end)   # condition 1
carry_length   = hypot(end - start)                        # condition 2
progressive    = dist_reduction >= 5.0 AND carry_length >= 5.0
```

**This is our own definition.** Carries from any zone of the pitch are eligible —
there is no start-zone exclusion (unlike progressive passes). FBref uses 5 metres
(~5.5 yards) for both thresholds; we use 5 yards, making the definition slightly
more permissive.

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
