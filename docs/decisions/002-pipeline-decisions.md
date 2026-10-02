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
`build_player_match._aggregate_events` passes
`outcome=(pass_data.get("outcome") or {}).get("name")`, where `pass_data` is the
event's `pass` object. It is `None` for completed passes and a string for any other
outcome; the `or {}` also covers an explicit `"outcome": null`.

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

> **Superseded by §31 (plan 004 H4b).** `match_duration` is now the elapsed playing
> time on the Half End clock (§31.1), and a dismissal after the match end is
> report-only (§31.2). The text below describes the pre-H4 pipeline.

`compute_match_duration(events)` = max of `event["minute"] + event["second"] / 60`
across all events with `period ≤ 4`. Period 5 (shootout) excluded. **Amended
2026-09-29 (plan 004 H1.6):** there is no 90.0 fallback. The `period` key is required
(a missing one raises `KeyError`), and a match with no `period ≤ 4` event raises
`ValueError`, which fails the league build (`MatchProcessingError`).

Period-5 events are also removed before any metric is counted
(`_process_match`, plan 004 H1.7): a shootout shot is not a shot, goal or xG. The
dismissal lookup ignores them too: a shootout card clamps nothing because every stint has
ended (an extra-time card, periods 3-4, still clamps). A dismissal minute past the match
duration is never capped silently; it is a `dismissal_after_match_end` quality issue that
fails the build (`DataQualityError`). The five 2015/16 leagues have no period-5 events,
so this changes no output.

This captures stoppage time and extra time automatically. It represents the last
*recorded* event timestamp, not the actual whistle — may undercount by 15–60
seconds in edge cases (systematic but negligible per match).

### Player minutes

> **Superseded by §31 (plan 004 H4b):** `compute_player_minutes(positions, clock, *,
> exit_at, treatment_windows)`; the minutes are the union of the stints on the period
> clock (§31.4). The text below describes the pre-H4 pipeline.

`compute_player_minutes(positions, match_duration, *, dismissal_minute, substitution_minute)`:
- Both keywords are **required, with no default** (plan 004 H3a): a forgotten argument
  would silently give uncapped minutes. Pass `None` when the player was not dismissed
  or not substituted off.
- `to = None` → use the exit cap (`stint_end_cap`, below) if set, else `match_duration`.
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
The clamp is therefore applied **only when an exit cap is known**: a dismissal
minute or, from 2026-09-29, the player's own substitution (below).

#### Substitution-off cap (amended 2026-09-29, plan 004 H2)

**Finding.** The H2 spike's structural check (per team-match, summed minutes
≤ 11 × match duration) failed in all five leagues. In 33 player-matches the player
is the subject of a `Substitution` event, but his last lineup stint still runs to
"Final Whistle". Usually he left for treatment ("Player Off"), came back ("Player
On"), and was replaced at that instant, so both he and his replacement were credited
to the whistle. Example: match 3825707, Willian José, Player On 37:02 → Final Whistle,
replaced at 37:02, +56 min. The pipeline and the spike's independent re-implementation
agreed exactly, so both overcounted.

**Decision.** The player's own `Substitution` event is authoritative.
- **Cap.** Every stint end, explicit `to` included, is capped at
  `stint_end_cap(dismissal_minute, substitution_minute)`, the earlier of the two
  (`metrics/minutes.py`). It is the same mechanism as the dismissal cap and is
  shared by `compute_player_minutes` and `_iter_stint_contributions`.
- **Impossible stint.** A stint that starts after the player's own substitution
  fails the build (`stint_after_substitution`, `DataQualityError`). The order is
  `(from_period, from)` vs `(substitution period, minute)`, not the match-absolute
  minute. Period 2 restarts at 45:00, so first-half stoppage (e.g. 46:01, period 1)
  comes before a half-time substitution (45:00, period 2). A raw-minute comparison
  would flag 9 legal half-time subs (e.g. Bale, 3825739). A stint that starts at the
  substitution instant is allowed and gets 0 minutes. `from_period` is required.
- **Fail-loud checks.**
  - More than one substitution off for a player fails the build
    (`multiple_substitutions_off`; 0 occur).
  - Per team-match, summed minutes above `11 × match_duration + 1e-6` fail the build
    (`team_minutes_exceed_ceiling`). After the cap, the measured maximum excess is 0.0.
    No stint with an explicit `to` ends after the last event (the closest is 18 s
    before it), so any wider tolerance would only hide bugs.
- **Report-only counts.**
  - `substitution_capped` lists each capped player and the minutes removed (33).
  - `reversed_period_stint` counts stints with `from_period > to_period` (9: PL 3,
    Serie A 4, Ligue 1 2; e.g. Rodrigo Ely, 3879549, "45:00 p2 → 45:51 p1"). It does
    not fail the build. The stints are **not spurious**: on the match-absolute axis
    each one fills the gap between its neighbours, so the player's stints sum to the
    same total as any teammate's. Removing them would cut 0.5–2.5 min more. Each stint
    is classified below (plan 004 H3b).

**The 9 reversed-period stints, one by one.** "Real" is the player's time on a
period-aware clock (same stints and exit caps, each period ending at its last event).
"Lost" is real − credited. Source: `spikes/measure_first_half_stoppage.py`.

| Match | Player | Kind | Credited | Real | Lost |
|---|---|---|---|---|---|
| 3754217 | Mesut Özil | Position change across half-time | 74.55 | 78.18 | 3.63 |
| 3754217 | Aaron Ramsey | Position change across half-time | 95.63 | 99.27 | 3.63 |
| 3754217 | Gabriel Armando de Abreu | Sent off at (1, 45:44); capped at the card | 45.73 | 45.73 | 0.00 |
| 3879602 | Paolo Sammarco | Position change, ends at "Player Off" (treatment) | 73.85 | 75.57 | 1.72 |
| 3879549 | Rodrigo Ely | **Phantom start**: from (2, 45:00), before his Substitution-On at (1, 45:51) | 50.60 | 52.15 | 1.55 |
| 3879549 | Giacomo Bonaventura | Position change across half-time | 79.78 | 82.18 | 2.40 |
| 3879549 | Andrea Bertolacci | Position change across half-time | 95.27 | 97.67 | 2.40 |
| 3829416 | Alassane Pléa | Position change across half-time | 91.85 | 95.90 | 4.05 |
| 3829416 | Hatem Ben Arfa | Position change across half-time | 62.77 | 66.82 | 4.05 |

- **The total lost is 23.43 min, and none of it is caused by the reversed stints.**
  Each loss equals the first-half stoppage the player was on the pitch for, the same as
  every teammate's (e.g. 3754217's first half ends at 48:38, so 3.63). Sammarco's figure is
  the exception: it is 0.3 min above his match's stoppage (1.42), but that is an
  artefact, not lost time. His lineup stint "31:21 p1 → 45:11 p2" spans the end of
  period 1 on the period-aware clock, so it credits his treatment window,
  (1, 46:07)–(1, 46:25), when the events show him off ("Player Off" / "Player On").
  The period-aware figure overstates his real time by 0.3 min. A period-aware
  conversion must therefore subtract treatment windows, not only reorder by period
  (plan 004 H4).
- **Only Ely's stint is phantom.** Its start comes before he entered the pitch. It
  still gives him exactly his second half (50.6). His real (1, 45:51)–(1, 47:24) is lost,
  like every other player's first-half stoppage.
- **Gabriel loses nothing.** He left before half-time, so his time never overlaps
  period 2 on the match-absolute axis.
- Decision (2026-10-01): docs only, no code change. The limit is systematic (below).

**Known limits.** *(Superseded by §31, plan 004 H4b: first-half stoppage is now
credited, and the AC Milan 3879549 collision no longer exists. The reversed-stint table
above stays as history.)*
- **First-half stoppage time cannot be represented.** Period 2 always starts at
  45:00, so on the match-absolute axis every player on the pitch at the end of the
  first half loses its stoppage time. In the Bale-type cases the treatment-return
  stint (e.g. 46:01, period 1) becomes 0 min, although the player really played 1–2
  min. **Measured (plan 004 H3b, `spikes/measure_first_half_stoppage.py`):**
  - 44,769 min in total, net (1.40% of league minutes; La Liga 0.89% to PL 2.17%).
    The gross loss is 44,889 min: the net figure includes −120.6 min from 30 matches
    whose first half's last event is before 45:00.
  - A full-match player loses a mean of 1.32 min per match; a second-half substitute
    loses 0.
  - Per-90 values of the 1,250 gated pool rows are overstated by 0.33–3.32%. The bias
    depends on the league and also differs between players within a league: percentile
    shifts have a local mean of about 0.2–0.3 pts and a local max of about 5–6 pts.
  - 11 pool rows would cross the 900-minute gate upward, and none downward.
  Whether to move to period-aware minutes is an **M4 pre-condition**: docs/milestones.md,
  Milestone 4, "Pre-condition from Plan 004 H3b".
- **Ligue 1 residual (not fixed).** A lineup stint can also run on after a
  "Player Off (Permanent)" with no Substitution event: Sofiane Boufal (3901155,
  ~14.4 min) and Florent Balmont (3901151, ~8.7 min). No check can see these, because
  the team really was down to 10. The "Permanent" label is unreliable: Anthony Weber
  (3900512) has it, then returns and has 94 events. So it must not become a cap rule.

The stats-reviewer approved the change, with conditions. Across all 8,922 Substitution
events: the subject is always a lineup player of the event's team, and the replacement
is always a lineup player of the same team. No explicit `to` is later than the player's
own substitution, so the cap changes no row outside the 33. After the cap, the
on-pitch count per team never exceeds 11 at any instant **in period-aware time**
(`(period, minute)` order, the on-pitch check below). On the match-absolute axis the
stored minutes use, it reaches 12 once: AC Milan 3879549, 45:00–45:51, with
Romagnoli's card ignored (11 with it, against 10 allowed). The cause is Ely's reversed
stint overlapping de Jong's first-half stoppage, a case of the first-half-stoppage limit
above.

#### Per-player ceiling and the `match_duration` column (added 2026-10-01, plan 004 H3a)

- **`match_duration` column.** Every `player_match_stats` row carries its match's
  `match_duration` (Float64, decimal minutes, `compute_match_duration`). It lets the
  integration suite check team minutes against `11 × match_duration` exactly, and lets
  later stages recompute shares of the match without the raw events. It is not
  aggregated into the season file.
- **`player_minutes_exceed_duration` (fails the build).** A player's minutes in one
  match above `match_duration + 1e-6` (`PLAYER_MINUTES_TOLERANCE`) fail the build
  (`DataQualityError`). An explicit `to` is not clamped against `match_duration`
  (above), so this guard is what stops an uncapped `to` past the last event from
  reaching the data. 2015/16: 0 rows; the largest excess is 1.4e-14 (float noise).
  **The guard is expected to trigger on a new source** (e.g. a provider whose last event
  precedes the whistle by more than StatsBomb's). The response is to investigate the
  stint and record a decision here, **not** to widen the tolerance silently.
- **Summary and CLI counts.** `summarise_quality_report` and the per-league CLI line
  print `on_pitch_count_exceeded` and `player_minutes_exceed_duration` (both 0 in all
  five leagues), next to `substitution_capped` and `reversed_period_stints`.

#### On-pitch count check (added 2026-10-01, plan 004 H3a; card rule amended in H3b)

**Rule.** Report-only issue `on_pitch_count_exceeded` (`_on_pitch_excess`). Per
team-match, the number of distinct players on the pitch may not exceed **11 minus the
team's dismissals so far**. Time is ordered by `(period, minute)`. Every stint is capped
at the player's exit (the earlier of his dismissal and substitution). All changes at one
instant are applied before the count is taken. Measured on all five leagues: **0**
team-matches exceed it (also after the H3b amendment below). The check is report-only,
so it changes no parquet value.

**When a card lowers the allowed number (amended 2026-10-01, plan 004 H3b).**
`_card_lowers_allowed`: the dismissed player **has taken the field** by his card (a
non-empty stint starting at or before it) **and was not substituted off before or at
it**. H3a used "on the pitch at the card", which got two cases wrong:
- **Dismissed while off for treatment** ("Player Off" / "Player On" window). Under the
  Laws he is still dismissed and cannot be replaced, so the team is down to 10. H3a kept
  11 allowed and would miss a replacement coming on.
- **Substituted off and booked at the same second.** H3a sorted the card first, so the
  player was still on and the team dropped to 10 while his replacement came on: a false
  excess. A dismissed player cannot be substituted, so the Substitution event proves it
  came first; the card does not lower the number.
A card on the bench (before entering, or after his own substitution, e.g. Bordeaux
below) still does not lower it. A stint with a `to` but no `to_period` raises
`ValueError` (a match failure), because its end cannot be ordered.

**Assumption: the Substitution event is complete.** The rule cannot tell "off for
treatment" from "substituted off" except through the player's own Substitution event.
A player whose stint ended with no Substitution event counts as still on the team's
side of the count. If a source drops a Substitution event, a later card shown to that
player on the bench lowers the allowed number. A replacement already on the pitch then
gives a false `on_pitch_count_exceeded`. This is report-only and 0 in 2015/16, and it is
pinned by `test_stint_end_without_substitution_event_then_card_lowers_allowed`. On a
new source, investigate any `on_pitch_count_exceeded` for this pattern first.

**Exit order differs from `stint_end_cap` (pinned, not aligned).** *(Superseded by
§31.2, plan 004 H4b: `stint_end_cap` now takes the earliest exit by instant, so the two
agree; `TestOnPitchExcessEdges` pins the agreement.)* The check takes the
earlier exit by `(period, minute)`; `stint_end_cap`, which caps the stored minutes, takes
it by match-absolute minute. They disagree only when one exit is in first-half stoppage
(e.g. 46:30, period 1) and the other in period 2 before 46:30. That is 0 cases in
2015/16. Aligning would change the `compute_*` signatures for no measured effect, so both
behaviours are pinned by `TestOnPitchExcessEdges`.

**Scope (what the check does and does not prove).**
- It catches what the 11 × duration ceiling cannot: a team down to 10 **through a
  dismissal** that is still credited with 11 players. A team down to 10 for any other
  reason has no card, so the allowed number stays 11 and the check cannot see it.
  Examples: an injured player leaving with no substitutes left, or the Ligue 1
  "Player Off (Permanent)" residual above.
- It runs on the **raw lineup stints ordered by (period, minute)**, not on the stored
  minutes, which are match-absolute (`iter_stint_bounds`). So "0" means the lineups
  are consistent. It does not mean the stored minutes never credit a 10-man team with
  11 players. With the same stints flattened to match-absolute minutes, one team-match
  exceeds: AC Milan 3879549, 11 on with 10 allowed at 45:00–45:51, under one minute.
  This is the first-half-stoppage collision explained below.

**AC Milan, 3879549: not an excess.** Romagnoli: second yellow at (1, 41:48), so
10 are allowed from then on.
- (1, 41:48)–(1, 45:51): 10 on. Balotelli, Zapata, López, De Sciglio, Bonaventura,
  Calabria, Bertolacci, Montolivo, de Jong, Luiz Adriano.
- (1, 45:51): de Jong off, Ely on (Substitution event). Still 10.
- Ely's first stint "45:00 p2 → 45:51 p1" ends before it starts, so it is empty in
  period-aware time. The same goes for the reversed middle stints of Bonaventura and
  Bertolacci. They count once, through their own other stints.
- From (2, 45:00) to the whistle: 10 on.

Only the match-absolute minute axis shows an extra player at 45:00–45:51. There, de
Jong's period-1 stoppage (to 45:51) and Ely's period-2 start collide on the same numbers:
11 with Romagnoli capped, 12 without his cap. This collision comes from the
first-half-stoppage limit above. Ely's stint is the one phantom among the 9
reversed-period stints, but it adds no minutes: his match-absolute stints sum to
exactly his second half (50.6 min). His real (1, 45:51)–(1, 47:24) is the part that is
lost (classification table above).

**Bordeaux, 3900519: not an excess.** This team-match was a false positive in a first
measurement that lowered the ceiling for every dismissed player with stints.
- Pallois was substituted off injured at (1, 29:10) (→ Plašil). He was then shown a red
  card **on the bench** at (2, 94:09). He was not on the pitch, so the allowed number
  stays 11.
- Khazri: "Player Off" at (2, 92:58), "Player On" at (2, 94:31). The team had 10 on
  between those two instants and 11 after them, within the allowed 11.

Both matches are pinned by tests in `tests/test_build_player_match.py`
(`TestOnPitchExcess`, `test_real_dismissal_matches_have_no_on_pitch_excess`).

**Before/after (H1 output → H2 output), per league:**

| League 2015/16 | Player-matches capped | Minutes removed | League minutes before | After |
|---|---|---|---|---|
| La Liga | 8 | 336.92 | 776,915.75 | 776,578.83 |
| Premier League | 6 | 213.58 | 786,938.17 | 786,724.58 |
| Serie A | 10 | 496.45 | 782,488.20 | 781,991.75 |
| Ligue 1 | 8 | 322.55 | 773,130.97 | 772,808.42 |
| Bundesliga (PARTIAL) | 1 | 23.37 | 69,679.90 | 69,656.53 |
| **Total** | **33** | **1,392.87** | **3,189,152.98** | **3,187,760.12** |

**The 33 player-matches (minutes):**

| League | Match | Player | Before | After | Removed |
|---|---|---|---|---|---|
| La Liga | 3825565 | Álvaro González Soberón | 93.65 | 76.42 | 17.23 |
| La Liga | 3825667 | Eric Bertrand Bailly | 93.43 | 32.68 | 60.75 |
| La Liga | 3825684 | Jaume Vicent Costa Jordá | 90.08 | 45.00 | 45.08 |
| La Liga | 3825699 | Francisco José Molinero Calderón | 92.32 | 81.75 | 10.57 |
| La Liga | 3825707 | Willian José da Silva | 89.40 | 33.45 | 55.95 |
| La Liga | 3825729 | Bernardo José Espinosa Zúñiga | 91.53 | 37.28 | 54.25 |
| La Liga | 3825739 | Gareth Frank Bale | 90.60 | 44.58 | 46.02 |
| La Liga | 3825901 | Aymen Abdennour | 91.07 | 44.00 | 47.07 |
| Premier League | 3754099 | Saido Berahino | 94.05 | 45.00 | 49.05 |
| Premier League | 3754148 | Ryan Mason | 93.25 | 82.77 | 10.48 |
| Premier League | 3754217 | Francis Joseph Coquelin (match group CB → DM-CM) | 92.58 | 44.62 | 47.97 |
| Premier League | 3754274 | Marc Muniesa Martínez | 93.37 | 45.00 | 48.37 |
| Premier League | 3754336 | Séamus Coleman | 93.30 | 75.67 | 17.63 |
| Premier League | 3754339 | Xherdan Shaqiri | 85.08 | 45.00 | 40.08 |
| Serie A | 3878610 | Rafael Márquez Álvarez | 97.18 | 69.18 | 28.00 |
| Serie A | 3879547 | Francesco Totti | 44.07 | 6.75 | 37.32 |
| Serie A | 3879622 | Lorenzo Pasciuti | 94.63 | 45.00 | 49.63 |
| Serie A | 3879639 | Gervais Lombe Yao Kouassi | 93.73 | 23.52 | 70.22 |
| Serie A | 3879690 | Marcos Alonso Mendoza | 93.97 | 73.48 | 20.48 |
| Serie A | 3879731 | Lorenzo Pasciuti | 92.15 | 10.43 | 81.72 |
| Serie A | 3879772 | Giuseppe Vives | 92.40 | 84.43 | 7.97 |
| Serie A | 3879831 | Gregoire Defrel | 91.85 | 27.85 | 64.00 |
| Serie A | 3879863 | Molla Wagué | 89.63 | 17.08 | 72.55 |
| Serie A | 3879874 | Marcos Alonso Mendoza | 90.87 | 26.30 | 64.57 |
| Ligue 1 | 3829420 | Grégory Sertic | 92.82 | 22.83 | 69.98 |
| Ligue 1 | 3900519 | Benjamin André | 95.25 | 45.00 | 50.25 |
| Ligue 1 | 3900529 | Ronaël Pierre-Gabriel | 94.55 | 58.73 | 35.82 |
| Ligue 1 | 3900539 | Edson André Sitoe | 92.43 | 61.20 | 31.23 |
| Ligue 1 | 3900588 | Jérémy Toulalan | 92.43 | 67.32 | 25.12 |
| Ligue 1 | 3900628 | Éderzito António Macedo Lopes | 91.72 | 45.00 | 46.72 |
| Ligue 1 | 3901163 | Mouhamadou Dabo | 92.18 | 47.35 | 44.83 |
| Ligue 1 | 3901259 | Matthieu Saunier | 93.50 | 74.90 | 18.60 |
| Bundesliga | 3890382 | Sebastian Boenisch | 91.28 | 67.92 | 23.37 |

**What changed in the outputs:**
- **Match level:** only these 33 rows changed, and only in `minutes_played`, the
  `minutes_<group>` columns, `position_group` and `position_raw`. Coquelin's match
  group changed. Every other row and column is identical to the H1 output.
- **Season level:** 31 rows changed (Pasciuti and Marcos Alonso have two capped
  matches each), only in `minutes_played`, `minutes_<group>`,
  `player_season_minutes_total` and the `*_p90` columns.
- **Flags and cohorts unchanged:**
  - **`meets_min_minutes` changes for no player.** The closest rows to 900 min were
    already below it: Ryan Mason 871.05 → 860.57, Rafael Márquez 811.10 → 783.10.
  - No `is_primary_stint`, season `position_group`, `is_outfield` or
    `in_percentile_pool` flip.
  - Cohort sizes (`meets_min_minutes ∧ is_outfield`, per league and group) are
    unchanged.
- **Per-90 effect.**
  - In 25 pool rows (`in_percentile_pool ∧ meets_min_minutes ∧ is_outfield`), every
    `*_p90` rises by the factor before/after minutes: median +2.8%, max +8.2%
    (Pasciuti, 1,741.78 → 1,610.43).
  - The largest change outside the pool is Sertic: 92.82 → 22.83 season minutes
    (×4.07 per-90 values), below the gate.
- **Spike agreement.** After the fix, the spike (re-implementing this rule) and the
  pipeline agree on all 20 core columns in every match. The league minutes gap to
  Σ(11 × duration − dismissal losses) is 0.02–0.06%, within the spike's 0.1%
  tolerance. The gap is mostly players who left injured with no substitutes left,
  plus the Ligue 1 residual above.

Totals quoted elsewhere in this ADR that predate this amendment (e.g. §28's
3,189,152.98 minutes) are historical; current totals are in the table above and in
`tests/fixtures/reference_totals/*.json` (`core`).

#### Bench dismissals

Three players in La Liga 2015/16 received Bad Behaviour red cards while on the
bench (empty `positions` arrays).  They are correctly excluded from
`player_match_stats.parquet` as 0-minute entries.  Their card events are real;
they represent genuine bench incidents (violent conduct, dissent).

**Dropped-event check (amended 2026-09-29, plan 004 H1.5).** Every event with a
`player.id` whose player has no output row is recorded in the quality report by
`(event type, reason)`, where the reason is `not_in_lineup` or `zero_minutes`. The only
allowed drop is a `Bad Behaviour` card (any card, yellows included) by a lineup player
whose `positions` array is **empty**. A player with stints that compute to 0 minutes is
not allow-listed, because that would be a minutes bug. Any other drop fails the build
(`DataQualityError`). Allowed drops, 2015/16, after all fixes:

| League | Bench cards | of which red |
|--------|-------------|--------------|
| La Liga | 8 | 3 |
| Serie A | 1 | 1 |
| Ligue 1 | 1 | 0 |
| Premier League, Bundesliga | 0 | 0 |

There are no events by players missing from the lineup. `Pass` and `Carry` events that lack
`location` or `end_location` also fail the build; there are 0 in all five leagues.

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

`UNKNOWN` is the fallback for any unmapped position. **Amended 2026-09-29 (plan 004
H1.1):** any team-match row with `minutes_unknown > 0` fails the build
(`DataQualityError`, with the unmapped `position_raw` names), so no output row has
UNKNOWN minutes.

`OUTFIELD_GROUPS` = `{CB, FB-WB, DM-CM, AM-Winger, ST}` — **GK is explicitly
excluded from all outfield player comparisons.** The season file carries
`is_outfield = position_group ∈ OUTFIELD_GROUPS`. **One rule: cohorts filter on
`is_outfield`; UNKNOWN minutes fail the build.** GK-vs-GK comparisons use only the
`GK` group.

### Season-level position group

At season aggregation, position group is determined by **most minutes played per
group**, not by most appearances.

`compute_minutes_per_group(positions, match_duration, *, dismissal_minute, substitution_minute)`
(both keywords required, as in §3) returns a `dict[str, float]` of group → minutes for one match, respecting
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
`position_group == "UNKNOWN"` rows. `test_no_unknown_minutes_in_season_output` asserts
`minutes_unknown == 0` on the season file. Both are skipped until the pipeline has been run.

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

## 6. Season Aggregation Principles

Recorded before implementation so they were not re-debated. Implemented in
`build_player_season.py` (§6a–§6d) and `build_player_match.py` (§6e).

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

xA is extracted as `xg_assisted` (with `xg_assisted_direct`), from the
`shot.statsbomb_xg` of the shot that followed the pass; see §25 and §26. Goals and
assists are stored with `_p90` columns for display, but are not DNA metrics.

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
started if **any** of their position stints has `from == "00:00"`,
`from_period == 1` and non-zero length (`to != "00:00"`, `_is_kickoff_spell`).
`from_period` is required: an absent one raises `KeyError` (plan 004 H1; it is no
longer defaulted to 1). These players receive `started_inferred = True`.

**After fallback:** if a team still does not have exactly 11 starters, or has
more than 11, no further guessing is applied.  Both cases are recorded in the
quality report under `issue = "starter_count_mismatch"`.  Fallback applications
are recorded under `issue = "started_inferred_fallback"`.

**Starting XI cross-check (fails the build; plan 003).** Per team-match, the set
of starters derived above must equal the player set in the team's `Starting XI`
event (`tactics.lineup`). A missing, duplicate or mismatching Starting XI event
(`starting_xi_missing`, `starting_xi_duplicate`, `starting_xi_mismatch`) raises
`StarterCrossCheckError`, and the league part is not written. Covered by tests on
3754047 (Swansea: 10 of 11 starters inferred) and 3825596 (Sporting Gijón: 3).
2015/16: 0 failures in all five leagues.

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
passes. It is **stored with a `_p90` column (`assists_p90`) and excluded from DNA
per §6c**, the same treatment as `goals` (decided 2026-09-29, plan 004).
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
- Season-level goal totals summed from the parquet are short of the score sum of
  the covered matches by exactly the league's own goals. This is expected, not
  missing data.
  2015/16 (`base.own_goals` in the reference files; the spike reconciles
  `goals + own_goals` with the `matches.json` scores):

  | League | Matches | `goals` | Own goals | `matches.json` score sum |
  |---|---|---|---|---|
  | La Liga | 380 | 1,014 | 29 | 1,043 |
  | Premier League | 380 | 988 | 38 | 1,026 |
  | Serie A | 380 | 951 | 28 | 979 |
  | Ligue 1 | 377 of 380 | 916 | 33 | 949 |
  | Bundesliga (PARTIAL) | 34 | 90 | 6 | 96 |

  The last column is the sum of `home_score + away_score` over the matches StatsBomb
  provides. It is not the official season total: Ligue 1 is missing 3 of its 380
  matches, and the Bundesliga covers only Leverkusen's 34 (ADR 003 §5).
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
similarity matrices, or rarity scores must filter on `is_outfield` (§4) before
computing. `is_outfield` excludes GK and UNKNOWN rows.

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

The same rule applies to `cross_completion_pct` (§15) and `long_pass_completion_pct`
(§19) from 2026-09-29 (plan 004 H1.3). Before that, those two counted unknown outcomes as
failures.

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

### Unknown-outcome crosses (amended 2026-09-29, plan 004 H1.3)

```
cross_completion_pct = crosses_completed / (crosses_attempted − crosses_unknown_outcome)
```

A cross with `outcome.name == "Unknown"` is neither completed nor failed, so it is excluded
from the denominator, as in §14. It still counts in `crosses_attempted` (volume), so
`crosses_attempted_p90` is unchanged. `crosses_unknown_outcome` is stored at match and
season level (display-only, no p90). The ratio is `null` when the adjusted denominator is 0.

| League 2015/16 | Crosses | Unknown outcome |
|----------------|---------|-----------------|
| La Liga | 9,606 | 139 |
| Premier League | 9,545 | 64 |
| Serie A | 9,652 | 182 |
| Ligue 1 | 9,253 | 101 |
| Bundesliga (PARTIAL) | 701 | 3 |

**Before/after:** these figures cover 1,250 pool rows (`in_percentile_pool` ∧
`meets_min_minutes` ∧ `is_outfield`).
- 297 rows rise, by a median of +0.79pp (mean +1.37pp, max +25.0pp on 4 attempts).
- 1 row becomes `null`: its only cross has an unknown outcome.
- Within-position-group percentile shift: mean 1.2 points, p95 3.5, max 20.4.
- Pool mean by group, before → after:
  - AM-Winger: 27.74% → 28.11%
  - CB: 29.65% → 29.94%
  - DM-CM: 27.32% → 27.77%
  - FB-WB: 26.76% → 27.12%
  - ST: 28.22% → 28.52%

**M4 note (stats-reviewer):** a minutes gate does not protect ratio metrics: a player can
reach an extreme percentile from a handful of crosses. M4 needs a minimum known-attempt gate
on `cross_completion_pct`, like `MIN_AERIAL_CONTESTS` does for aerials. `dribble_success_pct`
and `tackle_success_pct` need one too.

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
| Average `cross_completion_pct` (outfield, 900+ min) | ~27% (before the 2026-09-29 unknown-outcome amendment; see the before/after above) |

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

**Minutes definition (plan 004 H4).** Since H4b, `minutes_played` is period-aware and
counts all stoppage time (§31). So 900 minutes is about 840 (PL) to 861 (La Liga) nominal
minutes (§31, Definition note), and 11 pool rows crossed the gate upward in H4, none
downward (§31.7). Whether to keep 900 is an M4 decision.

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

The completion rate above counts unknown outcomes as failures (the pre-amendment
definition).

### Unknown-outcome long passes (amended 2026-09-29, plan 004 H1.3)

```
long_pass_completion_pct = long_passes_completed / (long_passes_attempted − long_passes_unknown_outcome)
```

This follows the same rule as §14 and §15. `long_passes_attempted` and its p90 are unchanged.
`long_passes_unknown_outcome` is display-only. The ratio is `null` when the adjusted
denominator is 0.

Unknown-outcome long passes, 2015/16:

| League | Count |
|--------|-------|
| La Liga | 775 |
| Premier League | 570 |
| Serie A | 826 |
| Ligue 1 | 736 |
| Bundesliga | 91 |

**Before/after:** these figures cover the same 1,250 pool rows as §15.
- 791 rows rise, by a median of +0.68pp (mean +0.80pp, max +4.81pp). None become null.
- Within-group percentile shift: mean 1.4 points, p95 3.7, max 13.5.
- Pool mean by group, before → after:
  - AM-Winger: 53.39% → 53.81%
  - CB: 63.44% → 64.07%
  - DM-CM: 66.00% → 66.47%
  - FB-WB: 47.85% → 48.47%
  - ST: 55.03% → 55.41%

**M4 note (stats-reviewer):** `pass.outcome == "Injury Clearance"` (2,459 passes across the
five leagues) is a deliberate kick out of play, but it is currently counted as incomplete. It
lowers `pass_completion_pct` and probably `long_pass_completion_pct`, likely most for CBs.
Measure it per position group and decide in an ADR before M4 percentiles.

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
the carry window cannot be determined.  These are credited as direct. This is a
**permissive** fallback, not a conservative one: it can only raise
`xg_assisted_direct`, crediting a shot that cannot be proved direct. Accepted because
it has 0 occurrences in all five 2015/16 leagues.  Logged at `WARNING` level.
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
- **5 players have ≥ 900 minutes across leagues but no single row ≥ 900** (updated
  2026-10-02 for the §31 minutes; 8 before H4): Mario Lemina, Abdelhamid El Kaoutari,
  Mario Suárez, Alhassane Bangoura, Mauro Zárate. Timm Klose, Mevlüt Erdinç and
  Alessandro Diamanti left the list: each now has ≥ 900 on one pool row (§31.7). This
  feeds the M4 minutes-gate decision (§17).
- The `build_player_season` `__main__` top-5 diagnostic is filtered on
  `in_percentile_pool` but not deduplicated across leagues.

### Tests
- Unit: Serie A → PL mover; mixed case with a within-league move then a cross-league move
  (3 rows: `multi_team_season` only on the two same-league rows,
  `multi_competition_season` on all three).
- Integration: exactly one `is_primary_stint` per `(player_id, competition_id, season_id, source)`.

---

## 30. Shot Outcomes — Exact StatsBomb Strings; Unknown Values Fail the Build

**Date:** 2026-09-29 (plan 004 H1.2; stats-reviewer approved)
**File:** `src/eleveniq/metrics/shots.py`, `src/eleveniq/pipeline/build_player_match.py`

### Bug
`SHOTS_ON_TARGET_OUTCOMES` had `"Saved To Post"` (capital T). StatsBomb spells it
`"Saved to Post"`. Exact matching meant 127 saved-onto-the-post shots were counted as off
target.

### Decision
- `"Saved to Post"` is **on target** (with `"Goal"` and `"Saved"`).
- `"Saved Off Target"` (146 shots) is **off target**, per StatsBomb's definition: the keeper
  saved a shot that was going wide or high. It is added to `KNOWN_SHOT_OUTCOMES`.
- Matching stays **exact**, never case-insensitive. The fail-loud check below catches any new
  spelling instead of hiding it.
- **Fail loud:** the build fails (`DataQualityError`) on:
  - any shot outcome outside `KNOWN_SHOT_OUTCOMES`;
  - any tackle outcome outside `KNOWN_TACKLE_OUTCOMES`;
  - any duel type outside `KNOWN_DUEL_TYPES`;
  - any shot missing `statsbomb_xg`.

  The quality report records the offending values.

### Categorical scan (5 leagues, 2015/16)
Every categorical value the code matches on was compared with the observed data:
- pass outcome and type;
- shot outcome and type;
- duel type and tackle outcome;
- dribble outcome;
- card names;
- lineup and Starting XI position names.

The only casing or spelling mismatch was `"Saved To Post"`. Some known values never occur in
the data: `"No Touch"`, and the positions "Attacking Midfield", "Defensive Midfield" and
"Secondary Striker". They are harmless and kept.

### Before/after (`shots_on_target`)

| League 2015/16 | Before | After | Δ |
|----------------|--------|-------|---|
| La Liga | 3,230 | 3,263 | +33 |
| Premier League | 3,197 | 3,220 | +23 |
| Serie A | 3,152 | 3,184 | +32 |
| Ligue 1 | 3,073 | 3,103 | +30 |
| Bundesliga (PARTIAL) | 308 | 317 | +9 |

The fix changes `shots_on_target` in 127 player-match rows and 117 season rows. It also
changes `shots_on_target_p90` in 100 of the 1,250 pool rows (median +0.040, max +0.153).
No other column changed.

---

## 31. Period-Aware Minutes (plan 004 H4)

**Status (2026-10-02):** implemented. Rule accepted 2026-10-01 (stats-reviewer: approve
with conditions, folded in below).
- **H4a:** the independent re-implementation is `spikes/verify_core_metrics.py`, and
  the reference totals are written.
- **H4b:** the pipeline (`metrics/minutes.py`, `metrics/positions.py`,
  `build_player_match.py`) and the before/after numbers (§31.7).

§31 supersedes §3 "Match duration", "Player minutes", "Known limits" and the on-pitch
exit-order note; §3 keeps the substitution and dismissal rules, now ordered by instant.

**Why.** Lineup times are match-absolute, and period 2 restarts at 45:00. So on the §3
axis, every player on the pitch at the end of the first half loses its stoppage time:
1.40% of league minutes (PL 2.17%, La Liga 0.89%; §3 "Known limits"). The per-90s are
biased between leagues and between players within a league. Minutes move to a clock
that runs through each period in turn.

### 31.1 Instants and the period clock
- An **instant** is `(period, minute)`.
  - For an event: `(period, minute + second / 60)`.
  - For a lineup stint bound: `(from_period, parse(from))` or `(to_period, parse(to))`.
  - Instants are ordered by period, then minute.
- **Playing periods** are 1–4. Period 5 (the shootout) is excluded as before.
  - `PERIOD_START = {1: 0, 2: 45, 3: 90, 4: 105}`.
  - Nominal ends: `{1: 45, 2: 90, 3: 105, 4: 120}`.
- **Period end.** `end[p]` is the instant of period p's `Half End` event, the referee's
  whistle, not its last event.
  - Events after Half End stay in every metric, but they extend no minutes.
  - 2015/16: in 8 first halves and 13 second halves the last event follows Half End
    (max 2.63 min). These are post-whistle cards, ball receipts and two Player On
    events.
- **The match fails (raises)** when:
  - a playing period has events but no Half End;
  - a playing period does not have **exactly two** Half End events, **one per team**
    (two different `team.id` values; a missing team id fails too). Every period of
    all 1,551 matches in 2015/16 has exactly two;
  - the Half End events of one period differ by more than `HALF_END_AGREE_MINUTES`
    (config, 1 s);
  - the playing periods present are not exactly 1..max;
  - it has no period 1–4 event at all (no playing time; a named failure, not a crash).
- **Period length.** `length(p) = end[p] − PERIOD_START[p]`, as recorded, because lineup
  times share this clock.
  - A period that ends before its nominal end is reported (`period_end_before_nominal`,
    report-only; 31 in 2015/16, at most 1.22 min short).
  - A period that ends more than `PERIOD_SHORT_FAIL_MINUTES` (config, default 5.0) short
    fails the build (`period_too_short`). This catches abandoned or truncated matches.
- **Elapsed time.** `elapsed((p, m)) = Σ_{q<p} length(q) + clamp(m, PERIOD_START[p], end[p]) − PERIOD_START[p]`.
- **Stoppages and halts count as playing time.** The clock runs from kick-off to the
  whistle, so every interruption inside a period is credited to the players on the
  pitch.
  - Examples: Leverkusen–Dortmund 3890456 has a 12.17 min second-half stoppage,
    including a 12.05 min gap between events (the halt when the Leverkusen coach was
    sent to the stands). Aston Villa–Watford 3754178 has 11.18 min.
  - This is intended. ElevenIQ has no reliable "ball in play" signal, and §3 already
    counted second-half stoppage the same way.
  - The cost: longer stoppages lower per-90s. PL per-90s are about 2.4% lower than La
    Liga's from stoppage length alone. That matters only for global percentiles.
- **Match end.** `match_end = (last period, end[last])`.
- **`match_duration`** = Σ length(p), i.e. **elapsed playing time**. Before H4 it was the
  last event's match-absolute minute. The column keeps its name.
- **Clamp guards (fail the build).** The clamp must not hide a bad stint.
  - `stint_end_after_period_end`: a stint `to` later than `end[to_period]`.
  - `stint_time_before_period_start`: a stint `from` or `to` earlier than its period's
    `PERIOD_START`.
  - Both are 0 in 2015/16. A stint that *starts* after its period's end is clamped to
    that end and reported (`stint_after_period_end`, report-only; Bale 3825739 and
    Dembélé 3900571, both returning after the whistle).
- **Extra time.** The constants for periods 3–4 are verified only by unit tests: the
  2015/16 data has no extra time.

### 31.2 Exits
A player's **exit** is the earliest, by instant, of:
1. his first dismissal: a Red Card or Second Yellow, from `bad_behaviour` or
   `foul_committed` (§3), in event-index order;
2. his first `Substitution` event as the player taken off (§3, H2);
3. **an unpaired `Player Off`:** a `Player Off` by him with no later `Player On` by him,
   in event-index order. The `permanent` and `off_camera` labels are ignored; only the
   pairing counts.
   - Weber 3900512 is labelled permanent but returns, and his Off is paired.
   - Any event by the player after his unpaired Off fails the build
     (`event_after_permanent_off`; 0 in 2015/16), **except two event types that do not
     need him on the pitch:**
     - a **`Bad Behaviour`** event (a card of any colour), because a card can be shown
       to a player who is off the pitch;
     - **his own `Substitution`** (he is the event's player, the one taken off). An
       injured player who leaves and is then formally replaced is a normal pattern.
       His exit stays the Off, the earlier instant. In 2015/16 all 588 Substitutions
       that follow the same player's Player Off have a Player On between them, so the
       case does not occur.

     Every other event type still fails, **including `Foul Committed`**, even with a
     card: a foul needs the player on the pitch. Decided 2026-10-02; it makes 31.2,
     31.3 and the exit loss in 31.6 agree.
   - This closes the §3 "Ligue 1 residual": Boufal 3901155 and Balmont 3901151, and
     also Cerci 3879796 (Serie A), who was not listed there.

Every stint end is capped at the exit. With the exit taken by instant order,
`stint_end_cap` and the on-pitch check (§3) no longer order exits differently.

**Exits in an interval.**
- A dismissal after a non-final period's end (a card during the interval) is clamped to
  that period's end and reported (`dismissal_after_period_end`, report-only).
- A dismissal after `match_end` is clamped to `match_end` and reported
  (`dismissal_after_match_end`). It is **report-only from H4**; it used to fail the
  build. A post-match red card is a real football event, and it changes no minutes.
- Both are 0 in 2015/16.

### 31.3 Treatment windows
- A **treatment window** pairs a `Player Off` with the same player's next `Player On`, in
  event-index order. Its time is subtracted from his stints.
- Lineups already split stints at almost all of the 1,535 windows in 2015/16. Only 7
  windows fall inside a stint, e.g. Shaqiri 3754339 (2.0 min) and Sammarco 3879602 (the
  §3 artefact). No player has an event strictly inside his own window.
- **A dismissal during a window** caps at the card. If the player has no `Player On`
  before the card, the Off is unpaired, so the exit is the Off, the earlier instant.
  Such a card is a `Bad Behaviour` event, which 31.2 exempts from
  `event_after_permanent_off`, so the case is valid and does not fail the build. The
  same holds when the player is then substituted: his own Substitution is exempt too,
  and the exit loss runs from the Off to that Substitution (31.6).
- **Two `Player Off` events in a row** by one player, with no `Player On` between them,
  fail the build. The pairing would be ambiguous. 0 in 2015/16.
- **A reversed window** (its `Player On` earlier in elapsed time than its `Player Off`,
  e.g. events out of timestamp order) fails the build. Subtracting it would remove
  nothing, and the leftover would be misreported as `overlapping_stints`. 0 of the
  1,535 windows in 2015/16.
- **The on-pitch check (§3) does not subtract windows.** Under the Laws, a player off for
  treatment still counts on his team's side. The check does use the exit above, unpaired
  Off included.

### 31.4 Stint intervals, union and position
- **Stint interval.** For each stint:
  - `start = (from_period, from)`;
  - `end = (to_period, to)`, or `match_end` when `to` is empty;
  - `end = min(end, exit)`, then both ends are converted with `elapsed()`;
  - the treatment windows (converted the same way) are subtracted;
  - an empty or negative piece contributes nothing. A reversed-period stint
    (`from_period > to_period`) always ends up empty.
- **`minutes_played`** is the length of the **union** of the player's pieces: a player
  cannot be on the pitch twice.
  - On the period-aware clock, 14 player-matches in 2015/16 have overlapping stints
    (15.33 min in total, max 3.02).
  - Each is reported (`overlapping_stints`, report-only), with the minutes counted once.
- **Position at elapsed time t** (inside the union): among the stints with
  `elapsed(start) ≤ t`, the position of the one with the greatest `elapsed(start)`.
  - Two stints with the same elapsed start but different start instants (a clamped
    first-half start vs the second-half kick-off): the later instant wins.
  - **Two stints with the same start instant: a stint whose `start_reason` is a
    Tactical Shift wins; otherwise the one listed first in the lineup wins** (decided
    2026-10-02 and approved by the stats-reviewer).
    - **The cases.** There are 386 same-instant groups in 2015/16 (385 pairs and one
      triple), almost all a substitute coming on with a simultaneous Tactical Shift.
      StatsBomb usually lists a zero-length stint with the shift's position first, then a
      stint from the same instant in the replaced player's slot. Once (Ravel Morrison
      3878553) it lists them in the reverse order.
    - **Why.** A shift at the same instant describes the shape *after* the substitution,
      so the shift's position applies. The player's events agree.
    - **Rejected alternatives.**
      - "Listed first" alone differs only in Ravel, whom it gives a wrong label for the
        whole match (0/126 events agree).
      - The later stint, as the pre-H4 pipeline did, puts a different match-level group on
        202 player-matches (stats-reviewer, 2026-10-02). One example is Chaigneau 3829440, a goalkeeper the pipeline
        credited with 90.7 min as Right Midfield; he is a GK throughout under this rule.
        **Resolved in H4b:** the pipeline now credits him 91.75 min as GK (H3b: 90.68
        Right Midfield), and the case is in `spikes/expected_position_flips.json`
        (cause `tie_rule`).
  - Reversed-period stints take part: their `from` still marks a position change.
  - **The position comes from the latest-starting stint even if that stint has already
    ended** (or is empty), while an earlier stint still covers t. This is intended. A
    lineup stint's `from` records when a position change happened, and that is
    reliable. Its `to` is what breaks around reversed-period and same-instant stints.
    Where stints overlap, the earlier one that is still "open" carries the outdated
    position. Taking the latest start is what matches the events: Ramsey, Pléa and Ben
    Arfa (overlaps), and 99.97–99.999% per-event agreement in every league. It only
    matters in the 14 overlap player-matches; elsewhere the latest-starting stint is
    the one covering t.
  - StatsBomb applied second-half Tactical Shifts before first-half-stoppage shifts, in
    match-absolute order. So the reversed stint carries the true second-half position.
  - Checked against the players' own event positions: Ramsey 3754217 (DM-CM in p2),
    Pléa 3829416 (ST), Ben Arfa 3829416.
- **Group minutes.** `minutes_<group>` sums the union time by the group (§4) of the
  position at t, so Σ groups = `minutes_played`. `position_raw` uses the same timeline.

### 31.5 Bounds
- **Per player:** `minutes_played ≤ match_duration + 1e-6` (§3, unchanged).
- **Per team, per period:** Σ over players of the union time inside period p
  ≤ `11 × length(p) + 1e-6`. Otherwise `team_minutes_exceed_ceiling` (with `period`)
  fails the build.
  - The clamp makes the per-player guard almost unreachable, so this is the main bound,
    together with the clamp guards in 31.1.
- **Dismissal record** (quality report, per dismissed player): the uncapped minutes are
  `minutes_overcounted_without_fix` when they exceed the elapsed time from his entry
  (his first credited instant, `entry_elapsed`) to the card, plus
  `DISMISSAL_TOLERANCE_MINUTES`. Measuring from kick-off would miss an uncapped
  substitute: on at 60, sent off at 70 and credited to 90 has 30 min, under the card's
  70 but over its 10-min span. The integration test
  `test_no_dismissed_player_minutes_above_card_minute` applies the same bound to the
  stored minutes, from the raw Half End events and lineups.

### 31.6 Independent check (H4a)
`spikes/verify_core_metrics.py` re-implements 31.1–31.5 from this text, with no
`eleveniq` imports. It writes `core.minutes_played` and `core.minutes_<group>` (league
totals). It checks:
- **per team, per period:** the ceiling in 31.5;
- **per league:** summed minutes ≈ Σ(11 × match_duration) − exit losses − treatment
  windows. An exit loss counts, on the elapsed clock, only for a dismissal or unpaired Off
  that comes before the player's own substitution: it is (that substitution, or
  match_end when there is none) − exit.
  The tolerance is 0.001% of the league's minutes. The observed gaps are 0.0000–0.0005%:
  the rule reconciles almost exactly once treatment windows are counted.
  - A treatment window is clipped at the player's exit, so a red card inside a window
    that later closes (Player Off, card, Player On) does not deduct the time after the
    card twice (H4b; 0 cases in 2015/16, unit-tested).
  - **The tolerance (1e-5) was set from the observed 2015/16 figures** (the largest gap
    is 4.15 min, Serie A). A future failure is to be investigated, not loosened.
  - **Most of Serie A's gap is Milinković-Savić, Lazio 3879637.** His Player Off is at
    (1, 44:27) and his Player On at (1, 45:16), but the lineup resumes only at
    (2, 45:00). So he is credited nothing from 45:16 to the 48:47 whistle, while 11 of
    his events fall in that span. Whether a Player On should resume a player's time when
    the lineup does not is queued for M4 (docs/milestones.md).
- **events_outside_credited_time (report-only, H4b):** each period 1–4 event by a player
  whose instant lies outside his credited time (the union of his pieces, closed at both
  ends). Exempt are the types that do not need him on the pitch: `Bad Behaviour`, his
  own `Substitution`, `Player Off` and `Player On`. Counted per event. 2015/16: 15
  (PL 3, La Liga 1, Serie A 11). The Serie A 11 are Milinković-Savić's; the other 4 are
  single `Ball Receipt*` events stamped (1, 0:00) by first-half substitutes who came on
  between 24:19 and 35:30 (Caulker 3754028, Pienaar 3754163, Navas 3754258, Varane
  3825661): mistimed events, not a minutes error.
- **GK identity:** a team has exactly one goalkeeper on the pitch at any time, so league
  GK minutes are about 2 × Σ match_duration, within 0.05%. Observed: 0.000–0.018% short,
  from the following:
  - 16 team-matches have a GK gap of up to 3.25 min: GK treatment windows and GK
    substitutions with a gap.
  - One team-match has an excess: Bournemouth 3754157, 2.27 min, with two goalkeepers.
    Two Tactical Shift events at (2, 45:00) swap Distin and Allsop, the second
    reverting the first. The lineup keeps the first, so Distin is in goal until 47:16
    while Allsop also is. This is pattern B below, and under the tactics check's
    5-minute threshold.

  The check shares the Clock with the implementation, but not the stint arithmetic.
- **event-position oracle:** in every match, each positioned event of a player is
  compared with his timeline group at the event's instant. Only events between his
  first stint start and his exit count.
  - A player-match with less than 90% agreement fails unless it is listed below.
  - League-wide agreement is 99.97–99.999%. A dominant-group comparison was tried first
    and dropped: it failed on genuine multi-position players (Borja Valero and Frara,
    3879602).
  - With the later-stint tie rule instead, the overall agreement was 99.76%, with 268
    player-matches below 90%.
- **tactics-timeline check (minutes):** each player's position timeline is rebuilt from
  the events alone, in event-index order:
  - the `tactics.lineup` of the Starting XI and Tactical Shift events;
  - for a substitute, the replaced player's position at his Substitution event.

  The lineup timeline is compared with it minute by minute. A player-match with more
  than 5 disagreeing minutes fails unless it is listed below. In 2015/16, 321 of
  3,232,402 minutes disagree, and the only player-matches over 5 min are the
  exceptions below.
- **Limit of both checks:** event positions, tactics and lineups all come from
  StatsBomb's tactics feed. So the checks prove that these agree with each other, not
  that they are true. A wrong Starting XI formation would pass both.

**Exceptions (9 player-matches, 2015/16).** All are lineup errors around Tactical
Shifts, each for part of the match. Each is confirmed against the team's Tactical Shift
events, which agree with the player's events. No rule is added for them yet; the option
is queued for M4 (docs/milestones.md).

| Pattern | Match | Player | Lineup | Events / Tactical Shift event |
|---|---|---|---|---|
| B. Two shifts at one instant | 3754226 | Henderson, Coutinho, Firmino, Benteke, Lallana (Liverpool) | Two Tactical Shift events at (1, 3:52) and two at (2, 45:00), the second reverting the first. The lineup applies the first: e.g. Henderson Left Midfield from 3:52 to half-time. | Events stay in the original positions (Henderson Right Defensive Midfield, 131 events). Wrong for 3:52 to half-time. Its 2nd-half stints are corrected by the first-listed rule. |
| C. Shift back to the slot has no stint | 3900509 | Polomat (Saint-Étienne) | Left Back zero-length, then Left Center Back from the sub at 53:03 | Shift events: LB at 53:03, LCB at 67:05. Events: LB 28, LCB 62. The lineup has no stint for 67:05, so LB is credited to the end. |
| C | 3829474 | Ketkeophomphone (Angers) | Right Wing zero-length, then Center Forward from 45:00 | Shift: RW at 45:00, CF at 63:28. Events: RW 38, CF 55. |
| C | 3879816 | Totti (Roma) | Center Forward zero-length, then CAM from 45:00 | Shift: CF at 45:00, CAM at 70:33. Events: CF 86, CAM 65. |
| C | 3878543 | Hernanes (Inter) | CAM zero-length, then Right Center Midfield from 45:00 | Shift: CAM at 45:00, RCM at 85:13. Events: CAM 92, RCM 17. |

- **Pattern A** (the same-instant order reversed, Ravel Morrison 3878553) is resolved
  by the Tactical Shift tie rule in 31.4.
- **Pattern B:** there are 6 team-instants with two or more Tactical Shift events in
  2015/16.
- **Pattern C mechanism:** a later Tactical Shift moves the substitute back to his
  sub-on slot position. The lineup's sub-on stint already carries that position, so
  StatsBomb emits no new stint, and the shift position is credited until the end. This is
  a side effect of the same-instant ordering, not missing data at random.
- **The match-level `position_group` stays wrong in 4 player-matches:**

  | Player-match | Pipeline label | Events say |
  |---|---|---|
  | Henderson 3754226 | AM-Winger (43.2 vs 19.5 min) | all DM-CM |
  | Coutinho 3754226 | DM-CM (43.2 vs 41.5 min) | all AM-Winger |
  | Polomat 3900509 | FB-WB | 62 of 90 events CB |
  | Ketkeophomphone 3829474 | AM-Winger | 55 of 93 events ST |
- **Patterns B and C** would be fixed by taking positions from the `Tactical Shift`
  events instead of the lineup stints. This is an option for M4, not adopted.

The before/after is §31.7.

### 31.7 Before/after (H4b, 2026-10-02)
Source: `spikes/period_aware_before_after.py` (no eleveniq imports). It compares the H3b
output (`data/interim/h3b_snapshot/`) with the H4 rebuild and decomposes every row's
change by re-running `spikes/verify_core_metrics.py` with one rule toggled at a time.

**Pipeline vs spike.** In all 43,044 player-matches of the 5 leagues, `minutes_played`,
the six `minutes_<group>` columns and `match_duration` agree exactly (max |diff| 0.0).
**This is not independent evidence:** the pipeline was written after the spike and
follows the same algorithm (elementary segments, latest-start marker), so the match shows
that both code the same reading of this ADR. The independent evidence is still the
structural reconciliation (league gap ≤ 0.0005%), the GK identity (≤ 0.018% short), the
event-position oracle (99.97–99.999%) and the tactics-timeline check (31.6).

**Report-only counts after the rebuild** (all as predicted; 0 failing issues):
`period_end_before_nominal` 31, `stint_after_period_end` 2,
`treatment_window_inside_stint` 7, `overlapping_stints` 14, `unpaired_player_off_capped`
3, `dismissal_after_period_end` 0, `dismissal_after_match_end` 0,
`events_outside_credited_time` 15. `substitution_capped` (33) and
`on_pitch_count_exceeded` (0) are unchanged from H3b.

**Minutes per league.**

| League 2015/16 | H3b | H4 | Δ | Δ % | Gap before | Gap after |
|---|---|---|---|---|---|---|
| La Liga | 776,578.8 | 783,440.7 | +6,861.9 | +0.88% | 0.419% | 0.419% |
| Premier League | 786,724.6 | 803,762.1 | +17,037.5 | +2.17% | 0.263% | 0.263% |
| Serie A | 781,991.8 | 792,267.0 | +10,275.2 | +1.31% | 0.498% | 0.500% |
| Ligue 1 | 772,808.4 | 782,658.7 | +9,850.2 | +1.27% | 0.507% | 0.510% |
| Bundesliga (PARTIAL) | 69,656.5 | 70,273.4 | +616.8 | +0.89% | 0.249% | 0.248% |
| **Total** | **3,187,760.1** | **3,232,401.8** | **+44,641.7** | **+1.40%** | | |

"Gap" is Σ(11 × match_duration) − Σ minutes as a share of the first term (exit losses,
treatment windows, gaps between a sub-off and a sub-on); it is essentially unchanged.

**By player-match kind.** Full-match players (24,841): mean +1.31 min. Other
player-matches (9,674): mean +1.24. Second-half substitutes (8,529): −0.002 (below).
Extremes: 3754339 `match_duration` 94.12 → 103.37 (full-match +9.25); 3829452
96.50 → 105.53 (+9.03).

**Gross effect of each rule** (all rows; they add up to the Δ exactly):

| Cause | Minutes | Rows |
|---|---|---|
| Clock (Half End rule, first-half stoppage, reversed stints) | +44,693.48 | 33,018 up, 725 down |
| Overlap counted once (union) | −15.33 | 14 |
| Unpaired Player Off as an exit | −29.17 | 3 |
| Treatment windows subtracted | −7.32 | 7 |
| Exit taken by instant instead of match-absolute minute | 0.00 | 0 |

**Rows whose minutes decrease: 728, −151.68 min.**
- 665 rows, −120.95: the match's first half ends before 45:00 (§31.1, kept as recorded).
- 60 rows, −4.77: second-half substitutes in the 12 matches whose last event follows the
  second-half whistle by 0.02–0.55 min. The pre-H4 clock ran their open stints to that
  event; §31.1 ends them at Half End. Starters lose the same tail but gain more.
- 3 rows, −29.17: the unpaired Player Off caps (Boufal 3901155 −14.37, Balmont 3901151
  −8.65, Cerci 3879796 −6.15).
- The overlap and window rules lower 21 rows, but the first-half stoppage outweighs
  them, so none of those rows decreases net.

**Match-level `position_group` flips: 226**, each listed with its cause in
`spikes/expected_position_flips.json`:
- **201 `tie_rule`:** two stints start at one instant with different groups, and the
  §31.4 same-instant rule (Tactical Shift first) now decides the group; pre-H4, the
  zero-length shift stint got 0 min and the slot stint got everything. Chaigneau
  3829440 is one (above).
  - Not the same comparison as the 202 in §31.4: that figure compares two tie rules on
    the §31 clock; this one compares H3b with §31, and `tie_rule` means the pattern is
    present, not that it alone caused the flip.
- **1 `overlap`:** Ramsey 3754217, AM-Winger → DM-CM (latest start; his events agree).
- **24 `stoppage_near_tie`:** no group loses more than 0.01 min, and the gain (≤ the
  match's first-half stoppage, 1.03–5.45 min) goes to the group he held at half-time,
  overturning a small old margin (e.g. Borini 3754008, 3.65; Mutch 3754236, 5.07).
  - **Coutinho 3754226 regresses:** AM-Winger (which his events support) → DM-CM. It is
    the pattern-B lineup error already listed in 31.6; H3b labelled him right by accident.
- **Ben Arfa 3829416 does not flip** (the plan expected him to): he stays AM-Winger, but
  FB-WB +16.7 and ST −13.7 min. His events agree with the new split to the second.
- 591 player-matches have a `minutes_<group>` shift over 5 min: 326 are a stoppage gain
  in one group; 265 move minutes between groups (258 same-instant stints, 3 overlaps, 2
  unpaired Offs, 2 that lose only 0.02 min).

**Season flags.**
- `meets_min_minutes`: **11 up, 0 down**, all pool outfield rows: PL 5 (Grealish 882.2 →
  900.2, Klose 889.5 → 910.5, Naismith 888.4 → 915.5, Touré 894.0 → 917.7, Distin
  892.6 → 920.2), Ligue 1 2 (Erdinç 890.6 → 907.5, Pastore 899.0 → 908.9), Serie A 4
  (Poli 885.1 → 904.2, Diamanti 892.5 → 909.5, Hernanes (Juventus) 895.8 → 909.7,
  Tachtsidis 896.7 → 910.3).
  - **Klose (Norwich) and Erdinç (Guingamp) now pass on their pool league alone.** This
    is a single-league pass under the per-row gate (§17), not a combined-gate pass: their
    Bundesliga rows (94.9 and 59.8, observed-only) still fail and count toward nothing.
- `position_group`: **9 flips**, all near-ties. Gated: Diouf (Stoke) AM-Winger → ST
  (663.7 / 658.1 before), Bouka Moutou (Angers) AM-Winger → FB-WB (655.7 / 654.3),
  Ntcham (Genoa) DM-CM → AM-Winger (475.8 / 472.8), Ben Arfa (Nice) ST → AM-Winger
  (1461.9 vs 1459.0 after). Ungated: Hernanes (Inter, 59 min), Balogh (Palermo, 64),
  Malbranque (Lyon, 429), Gomis (Nice, 188), Caldirola (Darmstadt, 190).
- `is_primary_stint`: **2 flips, one player:** Simone Colombi, Carpi 94.1 / Palermo 94.0
  → 94.2 / 96.9.
- Unchanged: `is_outfield` (0 flips), `in_percentile_pool` (2,279), `multi_team_season`
  (136), `multi_competition_season` (94), 2,628 season rows.

**Per-90 ratio on the gated pool** (1,261 rows; H3b per-90 / H4 per-90 − 1): 0.33–3.32%,
median 1.30%. PL 1.11–3.32% (median 2.19%), Serie A 0.46–2.19% (1.33%), Ligue 1
0.45–2.62% (1.27%), La Liga 0.33–1.43% (0.87%).

**Percentile shift, every DNA per-90** (34 metrics; `goals_p90` and `assists_p90`
excluded per §6c; there are no card columns). Gated pool, local = league × group,
global = group.
- **(a) Same cohort, new minutes:** local mean 0.20, max 8.57 pts (`passes_completed_p90`;
  3 rank steps in a cohort of 35); global mean 0.35, max 4.58.
- **(b) Cohort membership change:** max about 84 pts, entirely from the 4 gated rows whose
  season group changed (ranked in another cohort). For rows whose group is unchanged,
  (b) is at most 4.10 local and 1.32 global (11 rows joined the cohorts).
- The H3b estimate (local max 5.7) covered only 5 metrics, so it is not comparable.

**Sign-off (2026-10-02).** The flips above were outside the expected set in the H4 plan
(which named only Ramsey and Ben Arfa at match level), so the stop rule applied. Every
case was explained and the stats-reviewer approved with conditions. **The user accepted
the 226 match-level flips, the 9 season `position_group` flips and the Colombi
`is_primary_stint` flip as the intended effect of §31:** 201 from the Tactical Shift tie
rule the user chose, the rest near-ties from the clock change; Coutinho falls into the
lineup error already listed in 31.6. The accepted list is
`spikes/expected_position_flips.json`; any later difference is a STOP in
`spikes/period_aware_before_after.py`.

**Definition note (corrected 2026-10-02).** These minutes count all stoppage time in
both halves. Sources that credit a full match as 90 minutes (FBref/Opta style) do not.
- The 1.4% figure is **§3 → §31**, i.e. first-half stoppage only. §3 already counted
  second-half stoppage.
- Against a nominal 90-minute match, the average §31 full match is 94.11 min (La Liga),
  94.18 (Bundesliga), 94.85 (Ligue 1), 95.25 (Serie A) and 96.40 (PL). That is +4.6%
  (La Liga) to +7.1% (PL).
- So the 900-minute gate equals about **840 (PL) to 861 (La Liga) nominal minutes**.
- Treatment windows are subtracted, which nominal sources do not do, but §3 already
  excluded almost all of them.
- Whether to keep 900 or adjust the gate is an M4 decision (docs/milestones.md). Any
  future cross-source ADR must account for this (CLAUDE.md, single-source rule).
