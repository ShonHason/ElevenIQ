# ADR 004 — Cohorts, Percentiles and Player DNA

**Date:** 2026-10-02
**Status:** Accepted 2026-10-02 (plan 005, M4a). Implementation: M4b (pipeline columns), M4c (registry), M4d (percentiles).
- All figures come from `spikes/m4_decision_measurements.py` (2015/16, source `statsbomb`).
- Items marked **decided by user** were brought to the user (§6) and accepted as recommended. Every other item is **decided by default** (plan 005 decision mode).

**Context:** Milestone 4 turns the player-season rows into percentiles, a Player DNA vector and signature traits. Plan 005 lists the decisions D1–D22, and the stats-reviewer's conditions of 2026-10-02 apply to them. The decision mode is:
- each D-item's recommendation is the default;
- the user decides only where the data contradicts the recommendation, the stats-reviewer objects, or the item is a product choice.

**Terms.**
- **Gated:** `in_percentile_pool ∧ is_outfield ∧ minutes_nominal_equiv ≥ 900` (D1).
- **Reference population:** the rows a percentile is computed against.
- **Scored rows:** the rows that receive a percentile.
- **Local:** league × position group. **Global:** position group across the pool leagues.

---

## 1. Signature traits (product decision, user, 2026-10-02)

**Problem.** Some metrics are zero for most players in a position group (e.g. CB `box_entries_carry_p90`: 73% zeros in the gated pool). With average-rank ties, every zero gets the same mid-low percentile, so the percentile scale carries no information below the zero mass. Such a metric distorts a distance-based DNA vector. Yet a player who does it often is exactly what a scout looks for.

**Decision.**
- **Zero-heavy rule (D11).** A metric whose zero share among a group's gated rows exceeds 30% leaves that group's DNA vector, similarity distance and dimension scores.
- **Signature trait.** Such a metric × group is not discarded. It becomes a signature trait for that group. A gated player holds the trait when both hold:
  1. his absolute season count of the metric is ≥ `SIGNATURE_TRAIT_MIN_COUNT` for that metric × group;
  2. the metric passes the D19 split-half reliability check in that group.
- **Use.**
  - Shown on the player page.
  - Available to the M6 Gem Finder as a rarity signal, stated as "k of n" in the global group cohort.
  - Never part of the DNA vector, similarity distance or dimension scores.
- **Registry.** The M4c metric registry marks each zero-heavy metric × group as `signature_trait` and stores its minimum count.

**Measurement (2015/16 gated pool).** Only one metric × group is zero-heavy:

| Metric × group | n | Zeros | Count ≥ 5 | ≥ 10 | ≥ 15 | r_full (D19) | Reliable (≥ 0.7) |
|---|---|---|---|---|---|---|---|
| `box_entries_carry` × CB | 255 | 72.9% | 3 | 0 | 0 | 0.454 | no |

**Decided by user (2026-10-02): keep the rule as written.**
- No signature trait qualifies in 2015/16. The 3 CBs with 5–9 box carries cannot be told apart from noise (r_full 0.45).
- The rule, the registry flag and the measurement stay in place. Re-check with more seasons or a new source; the M4c registry carries CB `box_entries_carry` as `signature_trait` with no qualifying holders.
- `SIGNATURE_TRAIT_MIN_COUNT` = 5 for CB `box_entries_carry`, the lowest candidate. It matters only once the metric passes D19.

---

## 2. Gates and cohort membership

### D1 — Minutes gate: nominal-equivalent minutes (decided by default)
- **Gate:** `minutes_nominal_equiv = Σ_matches minutes_played × nominal_length / match_duration ≥ MIN_MINUTES_THRESHOLD − 1e-6` (900).
  - `nominal_length` is 90 for a match with periods 1–2 and 120 for one with extra time (periods 3–4).
  - The 1e-6 tolerance is needed because a full match often gives 89.99999999999999 rather than 90: 10,804 of 24,021 full player-matches do. Lindsay Rose sums to exactly 900, so summation order alone could otherwise move him across the gate. The spike and the pipeline use the same rule.
  - It is a new stored season column (M4b), so the gate can be recomputed.
  - `meets_min_minutes` is stored on every row. In M4b, 31 rows go down (28 pool outfield, 2 pool GK, 1 Bundesliga outfield) and 0 go up.
- **Two clocks.** The gate measures exposure ("ten full matches' worth"). The per-90 is a rate over real elapsed time (ADR 002 §31). They use different clocks on purpose:
  - an elapsed-minute gate is looser in leagues with more stoppage (900 elapsed ≈ 840 nominal in the PL, 861 in La Liga);
  - a nominal per-90 would drop real playing time from the denominator.
- **Effect** (gated rows, elapsed 900 → nominal 900):

  | League | Elapsed ≥ 900 | Nominal ≥ 900 | Mean full match |
  |---|---|---|---|
  | La Liga | 320 | 318 | 94.11 |
  | Ligue 1 | 316 | 310 | 94.85 |
  | Premier League | 305 | 297 | 96.40 |
  | Serie A | 320 | 308 | 95.25 |

  - Total 1,261 → 1,233 (−28: AM-Winger 10, CB 5, FB-WB 5, DM-CM 4, ST 4).
  - In 2015/16 the nominal gate selects exactly the same rows as a flat 945 elapsed (0 differences). The nominal form is kept because it carries over to a source with different stoppage time.
- **Sensitivity** (local percentiles of the 1,018 rows that pass every threshold, all 34 per-90 metrics, compared with the 900 gate):

  | Threshold | P95 \|shift\| | Max \|shift\| | Mean top-decile Jaccard | Min |
  |---|---|---|---|---|
  | 600 | 4.5 | 14.2 | 0.88 | 0.00 |
  | 750 | 3.5 | 9.8 | 0.91 | 0.00 |
  | 1,200 | 5.1 | 11.8 | 0.87 | 0.25 |

  - The minimum Jaccard of 0 comes from small cohorts, where the top decile is 3–4 players.
  - Reliability by minutes (D19) supports 900. The mean r_full across metrics is clearly lower in the 450–900 bin than at ≥ 900.

### D2 — Within-league split seasons: per-stint gate (decided by default)
- Each stint (team) row is gated on its own minutes. A stint below the gate gets `percentile_status = below_gate`.
- 9 players have a competition total ≥ 900 nominal but no stint ≥ 900:
  - Borriello, Marrone, Cerci, Floro Flores, Spolli, Hernanes, Mancosu (Serie A);
  - Cabot (Ligue 1);
  - Naismith (PL).

### D3 — Movers: no combined gate; one row per player in a population (decided by user: dedupe local populations too)
- **No combined gate.** PARTIAL-competition minutes never count (ADR 003).
- **Six players have two gated rows:**
  - across leagues: Khazri (PL/L1), Doucouré (L1/LL), Koné (PL/L1);
  - **within one league:** Perotti (Genoa/Roma), Quagliarella (Torino/Sampdoria), Shelvey (Swansea/Newcastle).

  Plan 005 assumed that local populations need no dedupe. The within-league movers show they do.
- **Rule, amended:** every reference population, local and global, holds at most one row per `(player_id, season_id, source)`:
  - the row with the most minutes;
  - tie → lowest `competition_id`, then lowest `team_id`.

  All gated rows are still scored. The non-selected row is scored against a population that does not contain the player (an asymmetry, accepted).
- **Effect:** the global percentile change from the dedupe is at most 0.85 pts for the movers and 0.86 for everyone else.
- **Combined-only players** (pool rows ≥ 900 across leagues, no row ≥ 900): Lemina, Diamanti, Mario Suárez, Bangoura, El Kaoutari, Zárate. They get `below_gate`.

### D4 — Who receives a percentile (decided by default)
- Only gated rows. Every other row gets null percentiles and a `percentile_status`: `below_gate`, `not_in_pool`, `goalkeeper` or `cohort_too_small`.

### D5 — Near-tie season groups (decided by default)
- Store `position_group_margin_pct` = (top − second group minutes) / minutes, and `position_group_share` = top group minutes / minutes.
- **Second group (M4b review spec):**
  - the group with the second-most minutes among GK and the 5 outfield groups; `minutes_unknown` is not a group (it is 0, and UNKNOWN minutes fail the build);
  - **null when its minutes are 0** (238 of the 1,233 gated pool outfield rows have one group only, measured on the M4b output; the review's earlier "14" was a mismeasurement), and the margin is then the top group's share: 1.0, unless an outfield row also has GK minutes;
  - ties are broken by the same alphabetical rule as `position_group` (ADR 002 §11.5);
  - for an outfield row, GK is never used as a display-only second cohort.
- `position_group_ambiguous` = margin < 5%: **13 gated rows**.
- Margin quantiles: P1 0.045, P5 0.20, P10 0.39. Rows below 2% / 5% / 10% / 20%: 6 / 13 / 32 / 62. Top-group share below 50%: 9; below 60%: 73.
- An ambiguous row is a member of its top group's cohort. It is also scored against its second group, but that percentile is **display-only**: never in DNA, similarity or rarity.
- The two cohorts differ a lot for these rows: the median per-metric gap is 20–40 pts and the maximum 63–92 pts. Examples: Bouka Moutou FB-WB/AM-Winger, Ben Arfa AM-Winger/ST, Saúl AM-Winger/DM-CM.

### D6 — GK / outfield mislabels (decided by default)
- A season row with both `minutes_gk` ≥ 45 and outfield minutes ≥ 45 stops the cohort build until it is explained. 2015/16: 0 rows. The check stays as a guard.

### D7 — Positions from Tactical Shift events (decided by default: keep the lineup)
- The 9 pattern-B/C player-matches (ADR 002 §31.6) were re-credited by their own event positions:
  - no season `position_group` changes;
  - no cohort changes;
  - the per-90s are unchanged, because total minutes are unchanged.
- The bar (no cohort change, at most 1 rank step) is met. Closed.

### D8 — A Player On that the lineup does not follow (decided by default)
- The lineup stays the only source of stint time. One case in 2015/16 (Milinković-Savić 3879637, 3.5 min; ADR 002 §31.6). Revisit with a new source.

---

## 3. Percentiles

### D9 — Local vs global (decided by user: the reviewer's test)
- The stats-reviewer replaced the median-ratio band with: per metric × group, `global_ok` iff every league's mean global percentile is within 50 ± 10.
- **Result:** 126 of 170 metric × group cells pass. The old 0.8–1.25 median band would pass 85, and the two tests disagree on 57 cells.
- **Fails in every group:** `interceptions_p90` (La Liga's mean global percentile is 70–77, about 25 pts above its share).
- Other notable failures:
  - fouls committed/won fail in 4 groups, with the PL low;
  - pressures fail for CB/DM-CM/ST;
  - clearances fail for CB/FB-WB, with the PL high;
  - aerials fail for CB/ST, with the PL high.
- Events per elapsed minute: 35.9 (PL) to 38.0 (Ligue 1). The match-length and stoppage effect is small next to these gaps.
- A failing cell gets a null global percentile with `percentile_status_global = league_inconsistent`. Its local percentile is unaffected.

### D11 — Method, cohort size and zero mass (decided by default)
- **Formula:** Hazen, (rank − 0.5) / n with average ranks for ties, so values are never 0 or 100.
- **Minimum cohort:** `MIN_COHORT_SIZE = 30`. Each percentile is stored with its cohort size `n`.
- **Cohort sizes** (nominal gate):

  | League | CB | FB-WB | DM-CM | AM-Winger | ST |
  |---|---|---|---|---|---|
  | La Liga | 62 | 66 | 67 | 81 | 42 |
  | Ligue 1 | 67 | 64 | 75 | 69 | 35 |
  | Premier League | 59 | 58 | 68 | 76 | 36 |
  | Serie A | 67 | 61 | 84 | 51 | 45 |

  Global after dedupe: CB 254, FB-WB 249, DM-CM 293, AM-Winger 274, ST 157.
- **Zero-mass rule:** more than 30% zeros in a group → out of that group's DNA (§1). 2015/16: only CB `box_entries_carry`.
- **M6 constraint:** rarity claims default to the global group cohort. A local rarity claim needs `MIN_RARITY_COHORT` ≈ 100 rows, which no 2015/16 local cohort reaches. Counts are stated as "k of n".

### D12 — Possession (decided by user)
- **Proxy:** leave-one-out team possession share per match, in two forms (Σ event duration, and pass share), weighted by the player's minutes.
- **Trigger:** mean within-league |Spearman ρ| ≥ 0.4, with the same sign in all 4 leagues.
- **Defensive per-90s never trigger.** Tackles, interceptions, pressures, recoveries and blocks all have max |ρ| ≤ 0.25, so a PAdj for defensive metrics is not adopted.
- **On-ball volume triggers** (duration proxy):

  | Metric | Groups (ρ) |
  |---|---|
  | `passes_completed_p90` | CB 0.77, FB-WB 0.77, DM-CM 0.70 |
  | `passes_attempted_p90` | CB 0.74, FB-WB 0.73, DM-CM 0.66 |
  | `carries_p90` | CB 0.73, FB-WB 0.73, DM-CM 0.70, AM-Winger 0.42 |
  | `progressive_carries_p90` | DM-CM 0.53, CB 0.51, FB-WB 0.41 |
  | `final_third_entries_pass_p90` | DM-CM 0.49 |
  | `progressive_passes_p90` | DM-CM 0.42 (CB 0.43 on pass share) |

**Decision (user, 2026-10-02):**
- No possession adjustment.
- Every triggered metric × group is flagged `team_style_biased` in the registry.
- Raw pass volume (`passes_attempted`, `passes_completed`) and `carries` stay **out of DNA**. They are display-only.
- `progressive_passes` and `progressive_carries` enter DNA through the explicit allow-list (ρ ≤ 0.55). Their explanations carry a "team-style influenced" note.
- `final_third_entries_pass` for DM-CM follows D17 (it pairs with `progressive_passes` at 0.88).

**Candidate replacement for pass volume (user, 2026-10-02): share of team passes while on the pitch.**
- **Definition (amended after the M4b definitions review, user 2026-10-02): open play only.**
  - Numerator: `open_play_passes_attempted`, the player's Pass events in periods 1–4 whose `pass.type.name` is not in `RESTART_TYPES` (§D14). Every outcome counts, including Unknown and Injury Clearance, because the share measures volume.
  - Denominator: `team_open_play_passes_while_on`, the open-play Pass events of his team (`team.id`) that he is credited with:
    - a pass counts when its instant lies in his credited time (the union of his ADR 002 §31 timeline pieces);
    - pieces are half-open [start, end), except that a piece ending at its period's end is closed there. The test uses the event's own period, so a pass at the half-time whistle counts for the first-half players;
    - **his own passes always count**, even when the pass lies outside his own credited time. That is a timeline inconsistency, not absence: the pass is credited to the passer plus every teammate on the pitch, and the event is reported as `events_outside_credited_time` (ADR 002 §31.6; D22 limits apply; user, 2026-10-03). 1 open-play pass outside the passer's credited time (Serie A, Lazio 3879637); passer still credited, reported.
    - A pass stamped after its period's whistle is clamped to the whistle instant, so it counts for the players on the pitch at the whistle and is not outside anyone's credited time.
    - instants are compared within 1e-9 min: the elapsed instant of a second-half kick-off and the start of a second-half segment come from different float sums (48.633333333333326 vs 48.63333333333333). Without the tolerance, 16–27 passes per league were credited to their passer only (found by the M4b1 spike).
  - `team_pass_share = Σ open_play_passes_attempted / Σ team_open_play_passes_while_on` over the season, never a mean of match ratios. It is null when the denominator is 0. Both totals are stored.
  - Why open play: taking corners, free kicks and throw-ins is a role assignment, not involvement in circulation. Set pieces are 20.6% of FB-WB own passes (median), and including them moves local percentiles by up to 54 pts (reviewer measurement).
  - **Pre-registered:** this is the only candidate tested. The all-types variant is not tested.
- **Team key (user, 2026-10-03):** the event's `team.id`. The passer's lineup team is used only when the event has no `team`. When both exist and differ, the build fails (`pass_team_mismatch`), and the pass is credited to nobody. 2015/16: 0 of 1,512,747 differ (M4b review). The spike keys on `team.id`.
- **Check (fails the build, spike and pipeline):** every team open-play pass is credited to **7–11** players, the Laws minimum to the maximum on the pitch. 2015/16 open play: 8–11 (11 on: 96.2–98.2% by league). The lower bound catches a pass credited to its passer only, the shape of the float bug found in M4b1.
- **Measured in M4b.** It enters DNA for **DM-CM and CB** as the possession-neutral replacement for raw pass volume only if both hold in that group:
  - |ρ| with the leave-one-out team possession share < 0.3 (the D12 method);
  - split-half r_full ≥ 0.6 (the D19 method).
- If it fails in a group, that group has no pass-volume DNA metric.

### D13 — Injury Clearance (decided by default)
- 2,459 passes (La Liga 548, PL 360, Serie A 825, Ligue 1 660, Bundesliga 66).
- **Excluded from the completion denominators** (`pass_completion_pct`, `long_pass_completion_pct`), like Unknown. `passes_attempted` stays a volume count that includes them.
- New stored column: `passes_injury_clearance` (and its long-pass part), so the denominator `attempted − unknown − injury_clearance` can be recomputed.
- **Effect:**
  - mean `pass_completion_pct` +0.1–0.2 pp (max +1.2 pp);
  - local percentile shift max 5.6 pts, 27 rows ≥ 3 pts;
  - long-pass completion: one ST row +15 pp, all other groups ≤ 4 pp.

### D14 — Restarts (decided by user; amended 2026-10-02 after the M4b definitions review)
- **Decision:** one shared exclusion set, `RESTART_TYPES = SET_PIECE_TYPES ∪ {"Throw-in"}` = {Corner, Free Kick, Goal Kick, Kick Off, Throw-in}. A pass of these types counts toward none of:
  - `progressive_passes` (amends ADR 002 §1, which had no type filter: progressive set pieces are now excluded too);
  - `final_third_entries_pass` and `box_entries_pass` (amends §27, which excluded set pieces only);
  - `long_passes_attempted` and `long_passes_completed` (amends §19, which excluded set pieces only). `long_passes_unknown_outcome` and `long_passes_injury_clearance` use the **identical** predicate, length ≥ `LONG_PASS_THRESHOLD_UNITS` ∧ type ∉ `RESTART_TYPES`. 404 of the 2,459 Injury Clearances are throw-ins, so a mismatched filter would subtract passes that are not attempts;
  - the D12 open-play pass columns.
- **Reversal recorded:** the first version of this decision said "throw-ins stay in long passes". **That line was not measured**, and the M4b review measurement reverses it:

  | Group | Throw-in share of `long_passes_attempted` | of `long_passes_completed` |
  |---|---|---|
  | FB-WB | 10.6% (row P90 17.9%, max 32.0%) | 13.1% |
  | CB | 1.0% (max 22.8%) | 0.9% |
  | AM-Winger | 1.0% (max 34.2%) | 0.9% |
  | ST | 0.6% | 0.3% |
  | DM-CM | 0.3% | 0.3% |

  - Top long throw-ins: Fuchs 126 (29.7% of his long passes), Capa 112, Walker 108, Luis Hernández (CB) 89, Rosales 84, Rose 58, Antonio (AM-Winger) 56 (34.2%).
  - The effect on local `long_passes_attempted_p90` percentiles: FB-WB max 27.6 pts (42 rows ≥ 10); Luis Hernández 60 → 19.
  - ADR 002 §19's own reason for excluding set pieces ("restarts, not distribution actions") applies to throw-ins.
- **Kept:** `crosses_*` (≤ 3.3% throw-ins in any group) and `switches` (FB-WB 2.4%, ≤ 0.4% elsewhere) keep restarts. Both are StatsBomb's own flags, and both are display-only in DNA (D18, D19).
- A full before/after goes with the M4b rebuild.
- Throw-ins are counted today in entries, progressive passes and crosses. Their share of each count, by group:

  | Group | Box entries (pass) | Final-third entries (pass) | Progressive passes | Crosses |
  |---|---|---|---|---|
  | FB-WB | 14.6% | 16.5% | **28.2%** | 1.1% |
  | CB | 7.5% | 1.8% | 3.7% | 3.3% |
  | AM-Winger | 1.0% | 1.1% | 2.7% | 0.2% |
  | DM-CM | 0.9% | 0.4% | 1.2% | 0.2% |
  | ST | 0.6% | 0.5% | 1.4% | 0.3% |

### D15 — Penalties (decided by default)
- Add `np_xg`, `np_shots` and `np_xg_per_shot`. DNA uses the non-penalty forms.
  - A penalty is `shot.type.name == "Penalty"` (410 in periods 1–4, none missing xG).
  - `np_xg_per_shot` is null when `np_shots` is 0.
  - Identities, checked in an integration test: `np_xg + penalty xG = xg` and `np_shots + penalty shots = shots`.
- Penalty share of xG: ST 11.2%, AM-Winger 9.1%, DM-CM 7.5%, FB-WB 5.0%, CB 0.9%.
- Top-10 by xG/90: Messi and Agüero leave and Fedor Flores and Benteke enter under np_xg.

### D16 — xG-assisted fallbacks (decided by default)
- Across all 5 leagues there are 6 no-receipt assisted shots, **all in one match, La Liga 3825848**.
  - That match has 401 Ball Receipt events for 910 passes, so it is a data gap. They are not genuine first-touch shots: only 1 of the 6 is `first_time`.
- **Rule:**
  - an assisted shot with no Ball Receipt*, or with no shooter or possession, counts toward `xg_assisted` but **not** `xg_assisted_direct`;
  - new counters for each fallback, for dangling `assisted_shot_id` and for a missing assister;
  - **receipt guard (M4b review):** the shooter's receipt counts only if it comes after the key pass (receipt index > pass index). Otherwise the shot takes the no-receipt fallback. This stops an earlier receipt from silently defining the window. 0 cases in 2015/16.
- 2015/16 effect: `xg_assisted_direct` −0.37 xG in total for 6 passers.
- Dangling ids, missing shooters and missing assisters: 0.

---

## 4. Feature selection

### D17 — Collinearity (decided by default; re-run on M4b output)
Spearman |ρ| ≥ 0.85 within a group (pre-M4b columns):
- `passes_attempted` ~ `passes_completed` (0.94–0.98, all groups);
- `tackles_attempted` ~ `tackles_won` (0.90–0.93);
- `dribbles_attempted` ~ `dribbles_completed` (0.90–0.94);
- `xg_assisted` ~ `xg_assisted_direct` (0.90–0.97) → `xg_assisted_direct` is display-only;
- `passes_*` ~ `carries` (CB 0.97, DM-CM 0.91, FB-WB 0.87): both are mainly touch volume;
- `progressive_carries` ~ `final_third_entries_carry` (AM-Winger, FB-WB, ST 0.86–0.90);
- `progressive_passes` ~ `final_third_entries_pass` (DM-CM 0.88);
- `key_passes` ~ `xg_assisted` (DM-CM 0.88).

The box-entry passes that are also progressive passes: 95–99.9% by group.

Rule:
- one metric of each pair stays in DNA;
- for attempted/completed pairs, the volume plus the rate is preferred, unless the rate fails D19 (see §6).

### D18 — Position-biased metrics (decided by default)
- `dispossessed_p90`, `miscontrols_p90` and `switches_p90` are display-only in V1 DNA. Touch normalisation is deferred.

### D19 — Reliability (decided by user: DNA bar 0.6)
**Decision (user, 2026-10-02):**
- A count metric enters a group's DNA only with r_full ≥ **0.6** at the gate.
- Between 0.6 and 0.7, its explanations carry a "lower reliability" note.
- Below 0.6 it is display-only in that group. This replaces the 0.7 bar in the stats-reviewer's condition.
- Ratios need `min_attempts` (beta-binomial, below); a row under it gets a null ratio percentile.
- `dribble_success_pct`, `tackle_success_pct` and `cross_completion_pct` are **display-only**. The D17 "volume + rate" rule falls back to volume only for those pairs.
- The values are fixed on the M4b output (D17/D19 re-run).
**Method:**
- matches split odd/even in date order;
- half per-90s demeaned within league × group;
- pooled Pearson per group, then Spearman-Brown: r_full = 2r / (1 + r).

**Count metrics:**
- 46 of 170 metric × group cells are below r_full 0.7 at the gate.
- Mean r_full by minutes: 450–900 well below ≥ 900 (e.g. `xg_p90` 0.27 vs 0.62).
- Lowest cells:

  | Metric | Below 0.7 in groups |
  |---|---|
  | `xg_p90` | CB 0.35, FB-WB 0.57, ST 0.64 |
  | `xg_assisted_p90` | CB 0.26, ST 0.58, FB-WB 0.60 |
  | `interceptions_p90` | all groups, 0.41–0.69 |
  | `shots_on_target_p90` | all but AM-Winger |
  | `key_passes_p90` | CB 0.53, ST 0.65 |
  | `tackles_won_p90` | ST 0.49, CB 0.63, FB-WB 0.70 |
  | `blocks_p90` | CB 0.37, FB-WB 0.47, DM-CM 0.61 |

**Ratios:**
- Beta-binomial reliability n / (n + M), with `min_attempts = ⌈1.5·M⌉` (reliability 0.6).
- Split-half r_full at the gate is the cross-check.

| Ratio | min_attempts by group | Gated rows meeting it | Split-half r_full |
|---|---|---|---|
| `pass_completion_pct` | 57–94 | 100% | 0.88–0.93 |
| `long_pass_completion_pct` | 35–60 | 94–100% (ST 29%) | 0.72–0.78 (ST 0.44) |
| `aerial_win_pct` | 31–93 | 64–99% | 0.50–0.85 |
| `dribble_success_pct` | 51–106 | 0–15% | −0.01–0.32 |
| `tackle_success_pct` | 107–195 (CB: no signal) | 0% | −0.11–0.36 |
| `cross_completion_pct` | 173–449 (CB: no signal) | 0% | 0.01–0.35 |

`xg_per_shot` split-half: 0.16–0.50. `MIN_AERIAL_CONTESTS = 10` is far below every group's minimum.

### D20 — Extremes around the gate (decided by default)
- No sub-gate row is scored (D1 assertion).
- 243 distinct gated rows sit above their group's P99 on at least one metric. **They are traced to events in M4g.**
- Sub-gate maxima confirm the gate's purpose: e.g. `xg_assisted_p90` 1.23 below the gate vs 0.52 gated, and `aerials_lost_p90` 54.4 vs 9.8.

### D21 — GK columns (decided by default)
- The registry declares `applies_to` per metric, and the cohort module refuses GK rows. Stored data is not nulled.

---

## 5. Data-quality thresholds

### D22 — Report-only issue limits (decided by default)
**Rule:**
- Per competition-season, an issue fails the build when its count exceeds `max(3 × worst 2015/16 league rate × matches, 3)`.
- `events_outside_credited_time` is counted per player-match.
- `STINT_LATE_START_FAIL_MINUTES = 2.0`, measured as `parse(from) − end[from_period]`. The two 2015/16 cases, Bale 3825739 (1, 46:01) and Dembélé 3900571 (1, 46:33), start **1 s** after their Half End.

**2015/16 counts** (entries / player-matches; the limit is scaled to a 380-match season):

| Issue | La Liga | PL | Serie A | Ligue 1 | Bundesliga | Worst per 100 matches | Limit per 380 |
|---|---|---|---|---|---|---|---|
| `period_end_before_nominal` | 7 | 2 | 17 | 5 | 0 | 4.47 | 51 |
| `stint_after_period_end` | 1 | 0 | 0 | 1 | 0 | 0.27 | 3 |
| `treatment_window_inside_stint` | 1 | 2 | 1 | 3 | 0 | 0.80 | 9 |
| `overlapping_stints` | 1 | 5 | 4 | 4 | 0 | 1.32 | 15 |
| `unpaired_player_off_capped` | 0 | 0 | 1 | 2 | 0 | 0.53 | 6 |
| `dismissal_after_period_end` | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| `dismissal_after_match_end` | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| `events_outside_credited_time` | 1 / 1 | 3 / 3 | 11 / 1 | 0 | 0 | 0.79 | 9 |
| `xga_no_receipt` (assisted shots; added 2026-10-03) | 6 | 0 | 0 | 0 | 0 | 1.58 | 18 |
| `xga_missing_shooter` (assisted shots) | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| `xga_dangling_shot_id` (assisted shots) | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| `xga_missing_assister` (assisted shots) | 0 | 0 | 0 | 0 | 0 | 0 | 3 |

The xG-assisted fallback limits (D16 counters) were added by the user on 2026-10-03, with the same rule: 3 × the worst 2015/16 rate, floor 3. Their counts are assisted shots, summed over the per-match entries.

These limits are a smoke alarm, not a validated tolerance. They come from the 2015/16 data they police, so 2015/16 passes by construction. For a 34-match season, and for the zero-rate issues, the floor of 3 is what applies.

The spike's own fail-loud rules (Half End count, reversed treatment window) are deferred to the new-source checklist (milestones.md).

---

## 6. User decisions (M4a hand-off, 2026-10-02)
These are the items where the data contradicted the recommendation, the stats-reviewer objected, or it was a product choice. **The user accepted all six recommendations as written**, plus the D12 team-pass-share candidate:
- **D3:** the within-league dedupe (data contradicts the plan);
- **D9:** the `global_ok` test (reviewer objection);
- **D12:** possession and on-ball volume (reviewer objection, confirmed by the data);
- **D14:** throw-ins in `progressive_passes` (data widens the scope);
- **D19:** the reliability bar and the unusable ratios (data contradicts the threshold);
- **§1:** signature traits (no trait qualifies in 2015/16).

---

## 7. M4b implementation and before/after (plan 005 M4b2, 2026-10-03)

**Implemented:**
- D1: `minutes_nominal_equiv` and `match_nominal_duration` (90, or 120 with extra time); the gate uses `MIN_MINUTES_TOLERANCE`.
- D5: `second_position_group`, `position_group_share`, `position_group_margin_pct`, `position_group_ambiguous`.
- D12: `open_play_passes_attempted`, `team_open_play_passes_while_on` (failing `pass_credit_out_of_range` outside 7–11), `team_pass_share`.
- D13: `passes_injury_clearance`, `long_passes_injury_clearance`; both leave the completion denominators.
- D14: one `RESTART_TYPES` in `metrics/pass_types.py`. `is_progressive_pass` requires `pass_type_name`.
- D15: `np_shots`, `np_xg`, `np_xg_per_shot`, plus their p90s.
- D16:
  - fallbacks are not direct;
  - the receipt must lie after the key pass;
  - report-only counters `xga_no_receipt`, `xga_missing_shooter`, `xga_dangling_shot_id`, `xga_missing_assister`, each with a D22 limit (user, 2026-10-03).
- D22:
  - `report_only_limit_exceeded` fails the build;
  - `stint_late_start_exceeds_limit` (> 2.0 min) fails the build.
  - 2015/16: 0 failures.

**Pipeline vs independent references:** all 15 reconcile tests pass (chunk A, chunk B and core × 5 leagues) with no change to the fixtures. The match-level values are checked independently there: `spikes/verify_core_metrics.py` rebuilds the per-pass credit (`team_open_play_passes_while_on`) and `np_*` from raw JSON.

`spikes/m4b_before_after.py` checks the **season aggregation step** only. It recomputes `minutes_nominal_equiv`, `team_pass_share`, `np_xg_per_shot` and the margin columns from the match parquet's own columns (`match_nominal_duration`, `match_duration`, the credit counts), without eleveniq, and agrees on every row.

**Flags (STOP rule, `spikes/expected_m4b_flips.json`, written before the rebuild):**
- `meets_min_minutes`: exactly the 31 expected flips, all down. That is 28 pool outfield, 2 pool GK (Agassa, Berisha) and 1 Bundesliga (Bender).
- `position_group`, `is_primary_stint`, `is_outfield`, `is_gk`, `in_percentile_pool`, `multi_team_season` and `multi_competition_season`: 0 flips.
- Minutes and rows: unchanged.
- The rule was shown to fail: with Grealish removed from a scratch copy of the list, the script exits 1 and names him.

**Δ per league** (season totals, after − before):

| League | `progressive_passes` | % | `final_third_entries_pass` | `box_entries_pass` | `long_passes_attempted` | `long_passes_completed` | `long_passes_unknown_outcome` | `xg_assisted_direct` |
|---|---|---|---|---|---|---|---|---|
| La Liga | −6,517 | −16.8% | −1,095 | −303 | −1,845 | −1,087 | −36 | −0.3694 |
| Premier League | −6,028 | −15.1% | −938 | −235 | −1,847 | −1,090 | −26 | 0 |
| Serie A | −6,046 | −15.0% | −924 | −271 | −1,313 | −739 | −20 | 0 |
| Ligue 1 | −6,871 | −17.0% | −1,229 | −333 | −1,585 | −952 | −33 | 0 |
| Bundesliga | −711 | −21.3% | −148 | −34 | −171 | −98 | −4 | 0 |

Pool outfield means, before → after:
- `pass_completion_pct`: 0.7626 → 0.7637
- `long_pass_completion_pct`: 0.5717 → 0.5714
- `progressive_passes_p90`: 4.585 → 3.834
- `long_passes_attempted_p90`: 6.195 → 5.997
- `box_entries_pass_p90`: 0.681 → 0.649
- `final_third_entries_pass_p90`: 2.612 → 2.480

**Gated pool:** 1,233 rows (D1). 13 are ambiguous (D5), as M4a measured.

**D12b (pre-registered):** `team_pass_share` against leave-one-out possession, and its split-half reliability.

| Group | n | ρ (duration) | ρ (pass share) | ρ by league (duration) | r_full | Enters DNA |
|---|---|---|---|---|---|---|
| DM-CM | 294 | −0.017 | −0.032 | 0.10, −0.01, −0.08, −0.07 | 0.936 | **yes** |
| CB | 255 | 0.361 | 0.377 | 0.46, 0.43, 0.22, 0.34 | 0.873 | **no** (|ρ| ≥ 0.3) |

So DM-CM gets `team_pass_share` as its possession-neutral pass-volume metric. CB has no pass-volume DNA metric, as D12 says when the candidate fails.

**D17 and D19 re-run on M4b output** (findings for the M4c registry review):
- `np_xg` ~ `xg` (0.93–1.00) and `np_shots` ~ `shots` (0.99–1.00): the np forms replace the raw ones in DNA (D15).
- Every other D17 pair is as in M4a; `progressive_passes` ~ `final_third_entries_pass` for DM-CM is now 0.891.
- 49 of 180 metric × group cells are below r_full 0.7 at the gate.
- `progressive_passes_p90` (restarts excluded) is 0.84–0.90 in every group.
- `np_xg_p90` r_full: AM-Winger 0.767, DM-CM 0.741, FB-WB 0.644, **ST 0.599**, CB 0.326.
- D9: `progressive_passes_p90`, `np_xg_p90`, the pass entries and long passes are `global_ok` in all 5 groups; `np_shots_p90` in 3 of 5.
- D12 after restarts are excluded:
  - `progressive_passes_p90` possession ρ is CB 0.45–0.50, DM-CM 0.47, and **FB-WB 0.51–0.52 (newly triggered)**. All are ≤ 0.55, inside the D12 allow-list rationale.
  - **`np_xg_p90` for ST is newly triggered: 0.436 (duration), 0.442 (pass share). AM-Winger is triggered on pass share (0.411).**
- Signature traits: unchanged (one zero-heavy cell, no holder).
- D20: 244 gated rows sit above a group P99.

**User decisions on the M4b2 findings (2026-10-03):**
1. **`test_h1_counts_match_adr_and_ratios_recompute`:** it reads `long_passes_unknown_outcome` and `shots_on_target` from the reference totals instead of hard-coded pre-D14 figures.
2. **`np_xg_p90` for ST is not in DNA.** Its r_full is 0.599, below the pre-registered D19 bar of 0.6, and the bar stays strict.
   - Raw `xg_p90` is **not** substituted: penalty-taking is a team role, not style (D15).
   - `np_xg` stays a displayed profile and comparison metric for ST.
   - This is an outcome of the pre-registered rule, not a new decision.
3. **No allow-list for the newly triggered D12 cells.**
   - `np_xg_p90` ST (possession ρ 0.436 duration / 0.442 pass share) is out of DNA; it is already out under item 2.
   - `np_xg_p90` AM-Winger (ρ 0.411 on the pass-share proxy) is flagged `team_style_biased` and stays out of DNA, under the same no-allow-list rule.
   - D12b's `team_pass_share` for CB (ρ 0.36) stays out under the |ρ| < 0.3 entry rule; for DM-CM it enters.
4. **`progressive_passes_p90` FB-WB** (possession ρ 0.51–0.52, newly triggered): recorded only. It stays inside the ≤ 0.55 basis of the existing progressive-passes allow-list.

---

## 8. Recorded for later (user, 2026-10-03)

**M4c (registry), not implemented in M4b:**
- **`np_xg_per_shot` minimum shots.** It gets the same minimum-attempts rule as the other ratios (D19). In the gated pool, 208 rows have fewer than 5 non-penalty shots, and the top value comes from a single shot (0.765, Genevois, a CB).
- **`second_position_group` minimum share.** Below a minimum share of minutes it is null. Today 10 GK rows get an outfield second group from a fraction of a minute (Begović DM-CM 1.03 min, Maignan ST 0.017 min). The ambiguity flag is unaffected (margin ≈ 1.0).

**New chunk M4b3, before M4c: open-play creation.**
- **Why:** penalties left `np_xg` because taking them is a team role (D15), and restarts left progressive passes, entries and long passes for the same reason (D14). But `xg_assisted`, `key_passes` and `assists` still include corners and free kicks. In the PL 2015/16, restarts are **13.4%** of all xG assisted, and for top creators far more: **Payet 41%, Özil 31%**, Lamela 24%, Eriksen 23%. That is as large as the penalty share of strikers' xG (11.2%), which D15 removed.
- **Scope:**
  - open-play `xg_assisted` / `key_passes` / `assists` (passes not in `RESTART_TYPES`);
  - spike and reference totals first, then the pipeline (the M4b1/M4b2 pattern);
  - set-piece delivery becomes a displayed **signature trait** (§1), not a DNA metric.

---

## 9. Open-play vs restart creation (plan 005 M4b3, 2026-10-03)

**Definition (user, 2026-10-03).**
- A chance is **restart** when its key pass's own `pass.type.name` is in `RESTART_TYPES` (§D14); otherwise it is **open play**. Only the key pass decides, never `play_pattern`.
- Split columns: `op_` / `restart_` × `xg_assisted`, `key_passes`, `assists`, each with a p90. Every existing column is unchanged.
- Each split uses its total's linkage:
  - `xg_assisted`: `assisted_shot_id` → a Shot with xG and an assister;
  - `key_passes`: `shot_assist` or `goal_assist`;
  - `assists`: `goal_assist`.
- **Invariant:** open play + restart = total on every row. Counts are exact; xG is checked within 1e-9, because the float sums are computed separately.
- **Set-piece delivery** (the `restart_` columns) is a displayed signature trait (§1), not DNA; the M4c registry decides.

**Reference totals** (independent spikes, 2015/16):

| League | `op_xg_assisted` | `restart_xg_assisted` | Restart share | `op_key_passes` / `restart_key_passes` | `op_assists` / `restart_assists` |
|---|---|---|---|---|---|
| La Liga | 595.7003 | 77.3819 | 11.5% | 5,484 / 1,001 | 640 / 82 |
| Premier League | 575.6716 | 88.7246 | 13.4% | 6,087 / 1,088 | 575 / 94 |
| Serie A | 527.7883 | 73.1273 | 12.2% | 6,147 / 989 | 537 / 72 |
| Ligue 1 | 497.4686 | 84.4978 | 14.5% | 5,167 / 1,072 | 522 / 74 |
| Bundesliga | 52.2684 | 7.8088 | 13.0% | 468 / 102 | 50 / 8 |

**Why not `play_pattern` (measured, user decision 2026-10-03).** The alternative labels a chance as restart when the shot's possession is labelled From Corner / Free Kick / Throw In / Goal Kick / Kick Off. The two definitions disagree in one direction only:
- **Key pass a restart, possession labelled open play: 0** in every league (all key passes and all assisted shots, 5 leagues).
- **Key pass open play, possession labelled a restart:**

  | League | Shots | xG | Share of restart xG assisted (key-pass definition) |
  |---|---|---|---|
  | La Liga | 2,618 | 279.9 | 362% |
  | PL | 2,940 | 269.9 | 304% |
  | Serie A | 2,989 | 249.4 | 341% |
  | Ligue 1 | 2,382 | 228.8 | 271% |
  | Bundesliga | 233 | 23.8 | 304% |

  The same disagreement by `play_pattern`:

  | League | Throw In | Free Kick | Corner | Goal Kick | Kick Off |
  |---|---|---|---|---|---|
  | La Liga | 108.1 xG (1,124) | 89.7 (761) | 50.5 (431) | 25.3 (236) | 6.2 (66) |
  | PL | 120.2 (1,355) | 63.2 (713) | 50.4 (497) | 28.9 (296) | 7.3 (79) |
  | Serie A | 97.5 (1,232) | 79.6 (946) | 41.0 (435) | 24.4 (297) | 6.9 (79) |
  | Ligue 1 | 101.1 (1,104) | 67.8 (644) | 29.3 (308) | 24.6 (254) | 6.0 (72) |
  | Bundesliga | 8.7 (98) | 8.9 (72) | 2.9 (39) | 2.6 (18) | 0.6 (6) |

- **Reason:** `play_pattern` labels the whole possession by how it started, including long open-play sequences after a throw-in or a goal kick. About half of all xG assisted would be "restart" under it. It measures how a possession began, not who delivered the set piece. The plan's 10% agreement gate assumed both definitions measure the same thing; the data shows they do not, so the gate was dropped (user).
- **Consistency guard** (both spikes): a restart key pass in an open-play-labelled possession fails the spike. 2015/16: 0 in all 5 leagues. A positive count on a future source means its restart labelling differs and must be investigated.
- **Second-ball creation** (an open-play key pass shortly after a corner or free kick, in the same possession) is a separate future item (plan 005 backlog).

