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

StatsBomb should set `positions[].to` to the dismissal minute for sent-off
players. If `to` remains `None`, `compute_player_minutes` would use `match_duration`
(~92 min) instead of the card minute, overcounting minutes and corrupting per-90
stats.

The `dismissal_minute` parameter guards against this. When building player-match
stats, `build_player_match.py` must:

1. Call `find_dismissal_minute(events, player_id)` from
   `src/pipeline/quality_report.py` to detect any dismissal for this player.
2. Pass the result as `dismissal_minute` to `compute_player_minutes`.
3. The quality report then cross-checks: if computed minutes still significantly
   exceed the card minute (tolerance: 1 minute), it is listed as a mismatch.

See `src/eleveniq/pipeline/quality_report.py` for `find_dismissal_minute`,
`check_dismissal_event_quality`, `check_dismissed_player_minutes`, and
`DISMISSAL_TOLERANCE_MINUTES`.

The dismissal check is tested in `tests/test_minutes.py::TestDismissalMinute` and
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
| `DM-CM` | Defensive Midfield, Left/Right Defensive Midfield, Center Midfield, Left/Right Center Midfield |
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

## 8. Open Issues

1. **Goal kick exclusion** — progressive passes include goal kicks (the function
   has no outcome knowledge). A `pass_type` column in `player_match_stats` would
   allow downstream filtering without re-running the pipeline.

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
