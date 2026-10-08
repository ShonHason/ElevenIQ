# ADR 004 — Cohorts, Percentiles and Player DNA

**Date:** 2026-10-02
**Status:** Accepted 2026-10-02 (plan 005, M4a). Implementation: M4b (pipeline columns), M4c (registry; decisions in §10), M4d (percentiles; §11).
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

| Metric × group | n | Zeros | Count ≥ 5 | ≥ 10 | ≥ 15 | r_full (D19) | Reliable (≥ 0.7; the D19 bar is now 0.6, §10.2) |
|---|---|---|---|---|---|---|---|
| `box_entries_carry` × CB | 255 | 72.9% | 3 | 0 | 0 | 0.454 | no |

**Decided by user (2026-10-02): keep the rule as written.**
- No signature trait qualifies in 2015/16. The 3 CBs with 5–9 box carries cannot be told apart from noise (r_full 0.45).
- The rule, the registry flag and the measurement stay in place. Re-check with more seasons or a new source; the M4c registry carries CB `box_entries_carry` as `signature_trait` with no qualifying holders.
  - **Amended by §10.2 (R15, user 2026-10-03):** a signature trait needs r_full ≥ 0.6 in its cell, so CB `box_entries_carry` (r 0.454) is display, not `signature_trait`.
- `SIGNATURE_TRAIT_MIN_COUNT` = 5 for CB `box_entries_carry`, the lowest candidate. It matters only once the metric passes D19.
  - **Amended (user, 2026-10-04, §10.2):** the minimum is max(5, the group's P90 of the season count among gated rows).

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
- **Amended (user, 2026-10-04, §10.1):** the dedupe runs within each population (group, or league × group), after the group split. Effect re-measured: 0.65 / 0.66 pts.
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
  - **Amended (user, 2026-10-05, §11.1):** this holds for members of the reference population. A row scored against a population without it (a D3 outsider, or a D5 second-group row) can be exactly 0 or 100; M4e shows those as "<1" and ">99".
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
- **Amended (user, 2026-10-04, §10.2):** the trigger also applies to ratios, a change made after the ratio measurements were seen that can only remove DNA metrics. A league with |ρ| < 0.05 has no sign in the sign test.

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
- **Crosses keep Injury Clearances in their denominator** (measured 2026-10-03, M4b3): 27 crosses are Injury Clearances (La Liga 6, PL 11, Serie A 5, Ligue 1 5, Bundesliga 0) of 38,757 crosses in the 5 leagues (0.07%). `cross_completion_pct` is display-only (D19), so this is left as is and recorded, not changed.
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
  - **M4c2b (2026-10-04):** `np_shots_on_target` (+p90) = the ADR 002 §30 on-target outcomes of non-penalty shots. `np_shots_on_target` + penalties on target = `shots_on_target` on every match row, re-counted from the raw events by `spikes/m4b_before_after.py` (2015/16: 43,044 rows, 385 penalties on target; 376 in the pool leagues). `shots_on_target` keeps penalties and is fixed display (§10.2).
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
- Ratios need `min_attempts` (beta-binomial, below); a row under it gets a null ratio percentile **and is left out of the reference population** (clarification, user 2026-10-04, §10.5). `np_xg_per_shot` uses the variance-components form (§10.5).
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
- D15: `np_shots`, `np_xg`, `np_xg_per_shot`, plus their p90s; `np_shots_on_target` (+p90) in M4c2b.
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
  - **Implemented in M4c1 (user, 2026-10-03): `SECOND_POSITION_MIN_SHARE = 0.10`.** The label is null when the second group's minutes are below 10% of `minutes_played`; the margin, share and ambiguous flag still use the real second-most minutes. 987 rows lose the label (644 gated pool: 640 outfield, 4 GK); none is ambiguous.
    - **Ambiguous rows always keep the label (structural bound).** The six group minute columns sum to `minutes_played` (UNKNOWN minutes fail the build), so the top group holds ≥ 1/6, and an ambiguous row's second group holds > 1/6 − 0.05 ≈ 0.117. A threshold above that bound could blank an ambiguous row's label; a fast test (`TestSecondPositionShareBound`) fails if `SECOND_POSITION_MIN_SHARE` exceeds it. In 2015/16 the lowest second-group share of an ambiguous row is 0.448 in the gated pool (Ntcham) and 0.380 over all rows (Thomas Henry, 37.8 min, not gated).
    - **A null label has two meanings:** no second group (0 minutes), or a second group below 10%. The margin and the per-group minute columns tell them apart.
    - **The label is meaningful only for gated rows** (stats-reviewer, user 2026-10-03). Outside the pool, a short season can be labelled from seconds of play (Yakou Meïté ST from 0.47 of 2.4 min; 136 non-pool rows are labelled from under 45 min). No absolute minute floor is added (user). M4e shows `second_position_group` only for `meets_min_minutes` rows. Every other column is identical and no flag flips (`spikes/expected_m4c1_second_group.json`, `spikes/m4b_before_after.py`; a list with Begović removed exits 1).

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
- **"Open play" here means "not a restart delivery", not the industry's open play** (M4b3 stats review, measured on raw events). A cross after a short corner or a short free kick is a rehearsed set-piece routine, but its key pass is not itself a restart, so it counts as `op_`. Of the 23,353 non-restart key passes in the 5 leagues (27,605 key passes = 23,353 + 4,252 restart), 1,710 are in From Corner possessions and 3,136 in From Free Kick possessions: 4,846, or 20.8% (`spikes/m4b3_short_routine_counts.py`, which fails unless the parts add up to the reference totals). So `op_xg_assisted` includes short-routine chances for designated set-piece takers. An earlier draft quoted 21,937 / 1,509 / 2,933, which could not be reproduced (they do not add up to 27,605). The UI should label the `op_` creation metrics "non-restart delivery" (plan 005 M4c list).
- **Two linkages, one rule** (M4b3 code review). The key-pass and assist split follows the pass's `shot_assist` / `goal_assist` flags, while the xG split follows `pass.assisted_shot_id` → Shot.
  - A pass with a linked shot but neither flag adds its xG to the `op_` or `restart_` xG part (by its type) and to no key-pass count, as the totals already do (unit test `TestXgAssistedLinkWithoutFlags`).
  - 2015/16: **0** such passes (measured by `spikes/m4b3_short_routine_counts.py`). So on real data the two splits agree on every row, which an integration test checks.
- **Throw-ins are restarts** (D14): 240 of the 4,252 restart key passes (5.6%) are throw-ins. So the `restart_` columns slightly reward long-throw specialists, unlike set-piece metrics that leave throw-ins out.

**Pipeline and before/after (M4b3, 2026-10-03).**
- **Reconcile:** the pipeline computes the six columns, plus p90s. All 15 reconcile tests pass, with no fixture changes.
- **Before/after against `data/interim/m4b2_snapshot`** (`spikes/m4b_before_after.py --all-columns-identical`): all 72 pre-existing match columns and 130 season columns are identical. The flag flips match `spikes/expected_m4b3_flips.json` (none). The invariant holds on every row: counts exact, xG max |Δ| 1.8e-15. A tampered expected list exits 1.
- **The M4b3 cleanup** (`is_restart` / `is_restart_type`, `PlayerTimeline.credits_pass_at`) is behaviour-neutral on its own. Against `data/interim/m4b3c_snapshot`, taken after the pipeline step was green, all 78 match and 142 season columns are identical, with 0 flips, and the reference totals are byte-identical.

**Measurements on the M4b3 output (report only, no decision; the M4c registry decides).**
- r_full at the gate (D19 bar 0.6):

  | Metric | AM-Winger | CB | DM-CM | FB-WB | ST |
  |---|---|---|---|---|---|
  | `op_xg_assisted_p90` | 0.623 | 0.185 | 0.715 | 0.551 | 0.534 |
  | `op_key_passes_p90` | 0.726 | 0.449 | 0.693 | 0.714 | 0.540 |
  | `op_assists_p90` | 0.486 | −0.005 | 0.290 | 0.299 | 0.238 |
  | `restart_xg_assisted_p90` | 0.814 | 0.781 | 0.868 | 0.664 | 0.912 |
  | `restart_key_passes_p90` | 0.838 | 0.719 | 0.932 | 0.747 | 0.930 |
  | `restart_assists_p90` | 0.361 | −0.000 | 0.603 | 0.547 | 0.596 |
  | for comparison, `xg_assisted_p90` | 0.706 | 0.261 | 0.806 | 0.597 | 0.578 |

- **Possession (D12, trigger 0.4 with the same sign in all 4 leagues):**
  - `op_xg_assisted_p90` triggers for AM-Winger: mean within-league ρ 0.421 (duration) and 0.419 (pass share).
  - No other split cell triggers. Max |ρ|: `op_key_passes` 0.375, `op_assists` 0.326; the `restart_` columns are ≤ 0.13.
- **Zero-heavy (> 30% zeros, D11):**
  - `restart_xg_assisted` and `restart_key_passes` in CB, DM-CM, FB-WB and ST;
  - `restart_assists` in every group;
  - `op_assists` in CB, DM-CM and FB-WB.

  These are signature-trait candidates (§1). For the xG columns, the trait table's count thresholds are not meaningful, because the count column is xG, not a count.


---

## 10. Metric registry decisions (plan 005 M4c2, 2026-10-04)

**Status.** The M4c registry (`src/eleveniq/analytics/registry.py`, M4c3) derives every metric × group status from pre-registered rules applied to committed measurements. Hand overrides are explicit entries with a reason and an ADR reference. This section records the decisions, the measurements and the derived lists that M4c3's `derive()` must reproduce.
- The stats-reviewer reviewed the `np_xg_per_shot` method and then the list (2026-10-04, both approved with conditions); the user decided the open points the same day.
- M4c2b (`np_shots_on_target`, before M4c3) added one column. It went through these rules like any metric, with no guaranteed status. The lists below are re-derived: it is DNA in AM-Winger and ST (10.6).

### 10.1 Committed measurements
- `spikes/m4_decision_measurements.py --write-json PATH` writes `src/eleveniq/analytics/measurements/statsbomb_27.json`. The spike still imports nothing from eleveniq. The file holds measurements. The registry's thresholds, the sign test and the trait minimum are applied in eleveniq.
- **Exceptions:** these spike-applied outcomes are stored.
  - `global_ok`: the D9 [40, 60] band. **`derive()` recomputes it from `league_mean_global_pct`** and does not trust the stored flag, so a change to the band cannot go stale.
  - `min_attempts` and `no_signal`: they carry reliability 0.6, the factor 1.5 and `RATIO_MIN_N` 10.
  - `coverage` and `ref_n_*`: they depend on `min_attempts`.
  - `count_p90`: the quantile 0.9.

  Changing any of these constants means regenerating the JSON.
- **Provenance:** source `statsbomb`, season 27, pool competitions 11, 2, 12, 7 (Bundesliga 9 is outside the pool). It also holds the sha256 of the season and match parquets and the season column list (144 columns since M4c2b; 142 before).
- **Contents:**
  - per metric × group (43 per-90s since M4c2b): n, zero share, r_full, the P90 of the season count with holders at count ≥ 5/10/15/P90, `global_ok` with each league's mean global percentile, and possession ρ for both proxies (within-league mean, per-league values and per-league n);
  - per ratio × group (7 ratios): M, `min_attempts`, coverage, split-half r_full, reference-population sizes (global and per league) and possession ρ on the global reference population (rows meeting `min_attempts`, deduped);
  - D12b for DM-CM and CB, with the D9 league means (since M4c3);
  - every D17 pair with |ρ| ≥ 0.7 (a reporting floor; the rule is 0.85) over the per-90s, the ratios and `team_pass_share`. A ratio enters a pair only on gated rows that meet its `min_attempts` in that group.
- **Reproducible:** two runs agree within 1e-9 on every value. The np_xg_per_shot bootstrap has a fixed seed. Float sums differ between runs in the last digits (up to 2.4e-13 measured), so the values below are printed to 6 decimals and compared at full precision.
  - **Since M4c3 (user, 2026-10-05) the spike rounds every stored float to 9 decimals** (`JSON_FLOAT_DECIMALS`), including the float keys of `holders_at_count` (the xG count P90s). So two runs write identical bytes.
  - `REGISTRY_VERSION` hashes the rounded measurements, the rules and the resolved cells. It changes only when a measurement, a rule or an entry changes.
  - An integration test runs the spike twice and asserts identical bytes, equal to the committed file, and the same version.
- **Gate (user, 2026-10-04):** "gated" uses `meets_min_minutes` semantics. The spike recomputes `minutes_nominal_equiv ≥ 900 − 1e-6` from the match parquet and fails unless it equals the parquet's `meets_min_minutes` on every row. 2015/16: equal on all 2,628 rows (1,346 true).
  - Until M4c2 the spike's own recomputation was silently shadowed by the parquet column of the same name. The two agree, so no figure changes.
- **Ratio denominators follow the stored columns** (stats-reviewer, M4c2). The completion rates subtract unknown outcomes **and** Injury Clearances (D13), as the pipeline does; crosses keep their Injury Clearances. Until M4c2 the spike left Injury Clearances in, and its rates differed from the stored columns by up to 0.083.
- **D3 dedupe after the group split (user, 2026-10-04).** A reference population is a position group (global) or a league × position group (local). Each holds one row per `(player_id, season_id, source)`: the most minutes, then the lowest `competition_id`, then the lowest `team_id`.
  - Until M4c2 every ADR 004 figure deduped over the whole pool before the split. That dropped Doucouré's only AM-Winger row (Rennes) from the AM-Winger population although it is scored there; his Granada row is DM-CM.
  - **Figures that move:**
    - D3's dedupe effect: 0.65 / 0.66 pts (was 0.85 / 0.86);
    - D9: 161 of 210 `global_ok` (was 162; 166 of 215 after M4c2b, which adds 5 `global_ok` cells). `tackles_attempted_p90` × AM-Winger becomes `league_inconsistent` (Serie A mean 39.99);
    - D11 global AM-Winger: 275 (was 274);
    - local Serie A ST: 44 (D11's table, from before the local dedupe, has 45: Quagliarella, Torino/Sampdoria).
  - No status changes.

### 10.2 User decisions
**2026-10-03 (M4c plan):**
- **U1** AM-Winger `op_xg_assisted` (ρ 0.419 on pass share): `team_style_biased`, display, no substitute (the §7 item 3 precedent). AM-Winger creation in DNA = `op_key_passes`.
- **U2** The set-piece trait is `restart_key_passes` in all five groups; `restart_xg_assisted` is display.
  - AM-Winger `restart_key_passes` is not zero-heavy, so the rules would make it DNA. An explicit override makes it a signature trait (§8: restart columns are never DNA).
  - The minimum count is amended on 2026-10-04 (below).
- **U3** `SECOND_POSITION_MIN_SHARE = 0.10` (M4c1, §8).
- **U4** No rename. `op_` ≡ `open_play_` ≡ pass type ∉ `RESTART_TYPES` (D14).
  - Registry display names carry the meaning: the `op_` creation metrics are labelled "non-restart delivery" (§9).
  - `open_play_passes_attempted` and `team_open_play_passes_while_on` (D12) keep their names.
- **U5** A ratio enters a group's DNA iff split-half r_full ≥ 0.6 **and** ≥ 90% of gated rows meet `min_attempts`.
- **R15** A signature trait requires r_full ≥ 0.6 at the cell level: zero-heavy ∧ r ≥ 0.6 → signature; zero-heavy ∧ r < 0.6 → display.
  - **This amends §1:** CB `box_entries_carry` (r 0.454) is display, not `signature_trait`.
  - §1's table header "Reliable (≥ 0.7)" predates D19; the bar is 0.6.

**2026-10-04 (after the stats-reviewer's registry review):**
- **`shots_on_target_p90` is fixed display (D15).** The pipeline counts penalties in it, which D15 removed from every other shot metric: 376 penalties on target in the pool leagues, and 72 gated players get ≥ 10% of their shots on target from penalties (Candreva 8 of 25). It was DNA in DM-CM, AM-Winger and ST. `np_shots_on_target` was added in chunk M4c2b and is DNA in AM-Winger and ST only (10.6).
- **The possession trigger (step 5) applies to ratios.**
  - D12 defined it for volume per-90s. Its reason, that DNA should describe the player and not his team, does not depend on the unit.
  - **This amendment was made after the data was seen** (§10.9 of the first M4c2 draft measured the ratios), and it **only removes DNA metrics**: it can turn a DNA cell into display, never the reverse.
  - `pass_completion_pct` → display + `team_style_biased` in CB, FB-WB, DM-CM and AM-Winger. Three of those are above the 0.55 allow bound, and AM-Winger gets no new allow-list entry (the §7 item 3 precedent).
  - `long_pass_completion_pct` enters the allow-list in CB, FB-WB and DM-CM (max ρ 0.513, 0.417, 0.515, all within 0.55), with the "team-style influenced" note.
- **D3 dedupe after the group split** (10.1).
- **Sign test:** a league whose |ρ| < 0.05 has no sign. The remaining leagues must share one; the mean is still over all four leagues. No 2015/16 trigger changes (42 triggered per-90 cells either way).
  - Example: ST `np_shots` on pass share has league values 0.585, 0.523, 0.382 and −0.0017 (mean 0.372).
- **Signature-trait minimum count by rule:** `SIGNATURE_TRAIT_MIN_COUNT` per group = max(5, the group's P90 of the season count among gated rows). P90 is the inverse ECDF, i.e. the smallest c with share(count ≤ c) ≥ 0.9. The spike stores the P90; eleveniq applies max(5, ·).
  - Reason: at a flat 5, `restart_key_passes` was held by 47% of AM-Wingers and 22% of DM-CMs, which is a common trait, not a signature.
- **Directions:** `tackles_attempted`, `dribbles_attempted` and `pressures` are `style` (10.7).
- **The trait minimum applies only to integer count columns.** For xG count columns (`np_xg`, `op_xg_assisted`, `restart_xg_assisted`, …) the "count" is an xG sum. The JSON still records their P90, but a zero-heavy xG cell has no count minimum; the registry raises if one would become a signature trait. 2015/16: none does.
- **Ratio reference populations: filter, then dedupe** (user, 2026-10-04). Rows below `min_attempts` are removed first; D3 then keeps one row per player per population.
  - So a mover whose most-minutes row misses `min_attempts` is represented by his other row in the group, if that one meets it.
  - **M4d must build ratio populations in the same order.** 2015/16 effect: ST `long_pass_completion_pct` global 63 → 64, CB `aerial_win_pct` 161 → 162, AM-Winger `aerial_win_pct` 211 → 212.
- **Possession ρ needs all four pool leagues.** The mean is defined only when ρ is defined in all four pool leagues; otherwise the cell is untestable (`possession_untestable`) and is display. The JSON records the rows used per league.
  - 2015/16 untestable cells, all fixed display: `dribble_success_pct` DM-CM / FB-WB / ST, and `restart_assists_p90` CB / ST.
- **D17 ratio pairs** are measured on rows meeting `min_attempts` (10.3).
- **M4f pre-registration** (10.8).

### 10.3 Rules (applied in this order per metric × group; overrides last)
| Step | Rule | Constant |
|---|---|---|
| 1 | Fixed display (an ADR outcome, table below) | — |
| 2 | D17 drop (table below) | — |
| 3 | Per-90s only: zero share > 0.30 → signature if r_full ≥ 0.6, else display | `ZERO_HEAVY_SHARE` 0.30 |
| 4 | r_full < 0.6 → display. A ratio needs split-half r ≥ 0.6 and coverage ≥ 0.90 (U5) | `DNA_RELIABILITY_MIN` 0.6, `RATIO_MIN_COVERAGE` 0.90 |
| 5 | Possession trigger (per-90s and ratios) → `team_style_biased`, display; an allow-list entry admits the cell if its max \|ρ\| ≤ 0.55 | `POSSESSION_RHO_TRIGGER` 0.4, `POSSESSION_SIGN_NEUTRAL` 0.05, `POSSESSION_ALLOW_MAX_RHO` 0.55 |
| 6 | `team_pass_share` (D12b, DM-CM and CB only): DNA iff \|ρ\| < 0.3 on both proxies and r ≥ 0.6; every other group → display | `TEAM_PASS_SHARE_MAX_RHO` 0.3 |
| 7 | Otherwise DNA. 0.6 ≤ r < 0.7 carries a "lower reliability" note | `LOW_RELIABILITY_BELOW` 0.7 |

**Exact definitions** (stats-reviewer, user 2026-10-04):
- **ρ for a proxy** is the mean of the four within-league Spearman ρ (pool leagues), defined only when all four are defined; otherwise the cell is `possession_untestable` → display.
  - Per-90s: computed on the gated rows of the group.
  - Ratios: computed on the global reference population (rows meeting `min_attempts`, then D3 dedupe).
- **Trigger:** for at least one proxy, |mean ρ| ≥ 0.4 and the sign test passes. Sign test: drop leagues with |ρ| < 0.05; at least one league remains and all remaining leagues share a sign.
- **Allow-list "max |ρ|"** is the larger of the two proxies' |mean ρ|, compared ≤ 0.55. Single-league ρ values are not used: CB `progressive_carries` passes at 0.5487 only under this reading.
- **Comparisons:** zero share > 0.30 (strict); r ≥ 0.6, coverage ≥ 0.90, ρ ≥ 0.4 and allow ≤ 0.55 are inclusive. D12b uses \|ρ\| < 0.3 (strict).
- **D9 band:** inclusive, `global_ok` iff every league mean is in [40, 60]. The band is defined once, as `config.GLOBAL_OK_BAND`. The spike cannot import eleveniq, so it writes out its own copy, and a fast test asserts the two are equal.
  - **Amended (user, 2026-10-06, §11.3):** D9 is undefined (`global_ok` None, basis local) when any pool league's reference population for the cell is below `MIN_COHORT_SIZE` (30): `ref_n_local` of the ratio cell, or `cohorts.ref_n_local` of the group for per-90s and `team_pass_share`. An untestable check is not a pass.
- **Validation** (the registry raises `RegistryRuleError`):
  - every metric kept by a D17 choice must itself be DNA in that group; otherwise the drop would silently remove both;
  - every allow-list entry's measured max |ρ| must be within its bound;
  - a zero-heavy xG count cell must not become a signature trait (10.2);
  - an override needs a reason and an ADR reference, must change the derived status, and may never put goals or assists into DNA.
  - **Added in M4c3 (2026-10-04)** (guards only; no 2015/16 status changes):
    - no final DNA cell is a goals/assists or `restart_` column (§8). Without the U2 override, AM-Winger `restart_key_passes` would be DNA and the registry raises;
    - every D17 choice is backed by a measured pair at |ρ| ≥ 0.85 in each of its groups;
    - an allow-list entry may not declare a bound above 0.55;
    - every measured metric and ratio has a registry spec, with the same count column or ratio terms as the measurement;
    - every derived cell has its measurement;
    - the `team_pass_share` (D12b) block is required, for exactly DM-CM and CB. The spike's `--write-json` now fails without the column; it used to write `null`.
  - **D9 for `team_pass_share` (user, 2026-10-05).** The spike now writes its league means: gated rows of the group, scored against the deduped global population, as for the per-90s. `derive()` computes `global_ok` from them like any other cell.
    - DM-CM: 50.43 / 56.11 / 42.50 / 52.75 (La Liga / PL / Serie A / Ligue 1), so `global_ok`. The DNA cell has a global basis, and no DNA cell has an unmeasured D9.
    - CB: the PL is 39.69, so not `global_ok`. The cell is display anyway.
    - **Check:** the file at HEAD (M4c2b) was rounded to 9 decimals the same way and compared recursively with the regenerated file: equal everywhere except the two added `league_mean_global_pct` keys.
    - The raw git diff is large only because every float is now stored at 9 decimals (a one-time change).
  - **`global_ok` is null** (basis local) where D9 is undefined: the unmeasured fixed-display columns, and ratio cells with a pool league without rows (ST `dribble_success_pct`, no Ligue 1 row). Both are display.

**Fixed display:**

| Columns | Reason |
|---|---|
| `xg_p90`, `shots_p90`, `xg_per_shot`, `shots_on_target_p90` | D15: penalties are removed from shot metrics. The np forms replace them: `np_shots`, `np_xg`, `np_xg_per_shot` and (M4c2b) `np_shots_on_target` |
| `xg_assisted_direct_p90` | D17 (~ `xg_assisted` 0.90–0.97) |
| `dispossessed_p90`, `miscontrols_p90`, `switches_p90` | D18 |
| `passes_attempted_p90`, `passes_completed_p90`, `carries_p90` | D12: raw volume |
| `crosses_attempted_p90` | It includes corner and free-kick deliveries (D14 keeps restarts in `crosses_*`). That is the restart contamination R14/§8 removed from creation, and no `op_` form exists yet. Backlog: `op_crosses_attempted` |
| `key_passes_p90`, `xg_assisted_p90` | R14: the `op_` forms replace them (ρ 0.92–0.99 / 0.90–0.99), by the §8 reason |
| `goals_p90`, `assists_p90`, `op_assists_p90`, `restart_assists_p90` | Goals and assists are never DNA (plan 005); restart columns are never DNA (§8) |
| `restart_xg_assisted_p90` | U2 (~ `restart_key_passes` ρ 0.94–1.00) |
| `dribble_success_pct`, `tackle_success_pct`, `cross_completion_pct` | D19 |

**D17 choices (|ρ| ≥ 0.85 on the M4b3 output; scanned over per-90s, ratios and `team_pass_share`):**
- `tackles_attempted` over `tackles_won`, and `dribbles_attempted` over `dribbles_completed`, in every group. The rate is display-only (D19), so the "volume + rate" rule falls back to volume only.
- `progressive_carries` over `final_third_entries_carry` in AM-Winger, FB-WB and ST.
- `progressive_passes` over `final_third_entries_pass` in DM-CM.
- Ratio and `team_pass_share` pairs at |ρ| ≥ 0.85 (ratios on rows meeting `min_attempts`). Each has a member that is display in that group, so none needs a D17 choice:
  - ST, `dribble_success_pct` (4 rows): ~ `dispossessed_p90` 1.000, ~ `final_third_entries_carry_p90` 1.000, ~ `pass_completion_pct` 1.000;
  - FB-WB, `dribble_success_pct` (7 rows): ~ `tackles_attempted_p90` −0.857, ~ `pass_completion_pct` 0.929;
  - FB-WB `np_xg_per_shot` ~ `np_xg_p90` 0.866.

  The `dribble_success_pct` values come from 4–7 rows and are noise, not collinearity. `dribble_success_pct` is fixed display; `np_xg_per_shot` is display by U5 in every group.
- No `team_pass_share` pair reaches 0.85. The registry raises if a pair at |ρ| ≥ 0.85 has both members DNA in a group without a D17 choice.
- `op_key_passes` ~ `op_xg_assisted` is below 0.85 in every group, so both may stay.
- 67 pairs at |ρ| ≥ 0.85 across groups; every one is resolved by the fixed-display table or the choices above. M4c2b added 5: `np_shots_on_target` ~ `shots_on_target` (0.984–0.998) in every group, whose other member is fixed display. `np_shots_on_target` ~ `np_shots` stays below 0.85 (max 0.840, DM-CM), so no D17 choice is needed.

**Allow-list (D12, bound 0.55; one entry per metric × group):**
- `progressive_passes` and `progressive_carries`: every group (D12). The entries matter only where the cell triggers: CB and DM-CM both; FB-WB `progressive_passes` (ρ 0.51) and `progressive_carries` (0.407).
- `long_pass_completion_pct`: CB, FB-WB and DM-CM only, the groups where it triggers (10.2).

**Override:** AM-Winger `restart_key_passes` dna → signature (U2). It is the only override. An overridden cell's reasons are its status and the override's reference, e.g. `('signature', 'override: ADR 004 §8, §10.2 U2')`, not the rule outcome it replaces (M4e shows reasons; user, 2026-10-05).

### 10.4 Outcomes of the re-run (no decision; the rules decide)
- FB-WB `final_third_entries_pass_p90` now triggers D12 (ρ 0.410 duration / 0.402 pass share) → `team_style_biased`, out (no allow-list; §7 item 3 precedent).
- AM-Winger `xg_p90` triggers on pass share (0.406). It is already display (D15).
- AM-Winger `np_xg_p90` triggers on pass share (0.411) → out (§7 item 3).
- D9: 161 of 210 metric × group cells are `global_ok` (10.1); 166 of 215 after M4c2b.

### 10.5 Ratios: minimum attempts and reference populations
**`np_xg_per_shot` M (method reviewed by the stats-reviewer 2026-10-04: approved with conditions).**
- **Model:** one-way random effects. A row's mean over n shots has reliability n / (n + M), with M = σ²_w / τ².
  - σ²_w is the pooled within-row per-shot variance (MSW).
  - τ² = var(row means) − mean(σ²_w / n_i), the same moment form as the beta-binomial.
  - So this is the beta-binomial with p̄(1 − p̄) replaced by a measured per-shot variance, because xG per shot is continuous. Raw xG scale.
- **Population:** pool outfield rows with ≥ 10 non-penalty shots, as for the other ratios. `min_attempts = ⌈1.5·M⌉` non-penalty shots.
- **Per-shot data** comes from the raw events (non-penalty shots, periods 1–4). The spike fails on:
  - any null xG;
  - any shot that maps to no match row, or to a row of another team;
  - any row whose shot count ≠ `np_shots` or whose xG sum differs from `np_xg` by > 1e-9.

  2015/16: 38,318 shots, all reconcile (max |Δ| 3.6e-15).

| Group | Rows | M | min_attempts | Coverage | Split-half r | Bootstrap M (5 / 50 / 95%) | M at n ≥ 5 | ANOVA / row-variance / log-scale M |
|---|---|---|---|---|---|---|---|---|
| CB | 125 | 64.78 | 98 | 0% | 0.175 | 30.4 / 69.5 / ∞ (8.3% no signal) | 25.50 | 63.21 / 56.63 / 13.09 |
| FB-WB | 131 | 13.09 | 20 | 17.3% | 0.224 | 8.7 / 13.3 / 22.8 | 7.88 | 15.67 / 15.14 / 6.29 |
| DM-CM | 267 | 15.37 | 24 | 40.1% | 0.448 | 11.6 / 15.6 / 22.2 | 16.04 | 17.31 / 14.77 / 7.34 |
| AM-Winger | 359 | 19.47 | 30 | 65.7% | 0.542 | 15.2 / 19.6 / 27.1 | 22.89 | 19.80 / 19.21 / 10.84 |
| ST | 223 | 28.12 | 43 | 61.4% | 0.464 | 21.1 / 28.3 / 40.1 | 29.67 | 25.31 / 25.17 / 14.36 |

- Only the moment-form M is used. The other variants are recorded, not used.
- The log scale roughly halves M, but the metric shown is the arithmetic mean of raw xG.
- `np_xg_per_shot` is display-only in every group (U5: r < 0.6). The minimum only nulls display percentiles.
- **The CB minimum is unstable:**
  - M is 25.5 at n ≥ 5 and 64.8 at n ≥ 10;
  - the bootstrap interval is unbounded above.

  Under every variant CB coverage is far below 90%, so in practice CB never shows this percentile.
- **The pooled noise is an average.** Within-row SD rises with the row's mean (correlation 0.80–0.87), so rows with high xG per shot, at the top percentiles, are noisier than σ²_w. At `min_attempts` their reliability is below 0.6.
- **Direct free-kick shots** (1,801, 4.7% of non-penalty shots) stay in `np_xg_per_shot`. They pull down designated takers' values (the D15 team-role argument). Not changed now; plan 005 backlog.
- Shots are assumed independent within a row. Rebounds make the effective n slightly smaller; this is accepted for a display threshold.

**No signal (τ² ≤ 0).** `min_attempts` is null, and every percentile in that cell is null. The raw value is still shown, and the cell carries reason `no_signal`. There is no fallback minimum: the data cannot tell rows apart at any sample size. 2015/16 cases: CB `tackle_success_pct`, CB `cross_completion_pct`.

**D19 clarification (user, 2026-10-04): applies to every ratio.**
- A row below `min_attempts` is **left out of the reference population** as well as unscored. Otherwise single-attempt extremes (the 0.765 one-shot CB) still move everyone else's rank.
- So reference populations differ per ratio × group. The JSON holds each one's size after exclusion and D3 dedupe, global and per league.
- **For M4d:** a cell whose reference population is below `MIN_COHORT_SIZE` (30) gets null percentiles (`cohort_too_small`, D4).

2015/16 cells below 30. Every one is display-only, so no DNA cell is affected:

| Ratio × group | Global | Smallest local |
|---|---|---|
| `cross_completion_pct`, `tackle_success_pct` (all groups) | 0 | 0 |
| `dribble_success_pct` CB / FB-WB / DM-CM / AM-Winger / ST | 0 / 7 / 9 / 41 / 4 | 0 / 1 / 1 / 5 / 0 |
| `long_pass_completion_pct` ST | 64 | 12 |
| `np_xg_per_shot` CB / FB-WB / DM-CM / ST | 0 / 43 / 118 / 96 | 0 / 4 / 23 / 23 |

**Other ratios** (beta-binomial; D13 denominators since M4c2):

| Ratio | min_attempts by group | Coverage | Split-half r | Possession ρ (duration / pass share) |
|---|---|---|---|---|
| `pass_completion_pct` | 57–93 | 100% | 0.875–0.928 | CB 0.687 / 0.686, FB-WB 0.654 / 0.639, DM-CM 0.570 / 0.589, AM-Winger 0.383 / 0.411, ST 0.322 / 0.325 |
| `long_pass_completion_pct` | 33–64 | 94.2–100% (ST 40.5%) | 0.694–0.780 (ST 0.474) | CB 0.513 / 0.509, DM-CM 0.506 / 0.515, FB-WB 0.417 / 0.417, AM-Winger ≤ 0.313, ST ≤ 0.264 |
| `aerial_win_pct` | 31–93 | 63.5–98.7% | 0.499–0.848 | ≤ 0.094 |

### 10.6 Derived lists (2015/16, after M4c2b)
DNA count per group: CB 16, FB-WB 19, DM-CM 24, AM-Winger 22, ST 20 (before M4c2b: AM-Winger 21, ST 19). Markers: * = 0.6 ≤ r < 0.7 ("lower reliability"); (AL) = admitted by the allow-list, `team_style_biased`.
- **CB (16):** aerials_lost*, aerials_won, ball_recoveries, box_entries_pass, clearances*, dribbled_past*, dribbles_attempted*, final_third_entries_carry, final_third_entries_pass, fouls_won*, long_passes_attempted, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct (AL).
- **FB-WB (19):** aerials_lost*, aerials_won, ball_recoveries, box_entries_carry, box_entries_pass, clearances, dribbled_past*, dribbles_attempted, fouls_won, interceptions*, long_passes_attempted, np_shots, np_xg*, op_key_passes, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct* (AL).
- **DM-CM (24):** aerials_lost, aerials_won, ball_recoveries, blocks*, box_entries_carry, box_entries_pass, clearances, dribbled_past, dribbles_attempted, final_third_entries_carry, fouls_committed, fouls_won, interceptions*, long_passes_attempted, np_shots, np_xg, op_key_passes*, op_xg_assisted, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct (AL), team_pass_share.
- **AM-Winger (22):** aerials_lost, aerials_won, ball_recoveries, blocks*, box_entries_carry, box_entries_pass, clearances, dribbled_past, dribbles_attempted, final_third_entries_pass, fouls_committed, fouls_won, interceptions*, long_passes_attempted, np_shots, np_shots_on_target, op_key_passes, pressures, progressive_carries, progressive_passes, tackles_attempted, long_pass_completion_pct.
- **ST (20):** aerials_lost, aerials_won, ball_recoveries, blocks, box_entries_carry, box_entries_pass, clearances, dribbles_attempted, final_third_entries_pass, fouls_committed, fouls_won, long_passes_attempted, np_shots, np_shots_on_target*, pressures, progressive_carries, progressive_passes, tackles_attempted*, pass_completion_pct, aerial_win_pct.
- **M4c2b: `np_shots_on_target_p90` by the rules (no decision).** The re-run changes no other value in the measurements file: the 211 earlier D17 pairs and every other cell are identical within 1e-9, so no other status can move.

  | Group | Zero share | r_full | Max \|mean ρ\| (duration / pass share) | Outcome |
  |---|---|---|---|---|
  | CB | 13.7% | 0.278 | 0.052 / 0.027 | display (step 4) |
  | FB-WB | 16.1% | 0.581 | 0.212 / 0.207 | display (step 4) |
  | DM-CM | 4.4% | 0.593 | 0.157 / 0.147 | display (step 4) |
  | AM-Winger | 0.4% | 0.700 | 0.366 / 0.390 | **DNA** |
  | ST | 0.0% | 0.679 | 0.377 / 0.395 | **DNA*** (lower reliability) |

  - Step 2: the only pairs at |ρ| ≥ 0.85 are with `shots_on_target_p90` (fixed display), so no D17 drop.
  - Every cell is `global_ok`. No cell is zero-heavy, so it is never a signature trait.
  - DM-CM loses `shots_on_target` (§10.2) and does not regain an on-target metric. With penalties out, r_full falls from 0.619 to 0.593.
  - Both DNA cells are below the possession trigger by less than 0.02 on pass share (10.10).
  - AM-Winger's r_full 0.700334 clears the 0.7 lower-reliability note by 0.0003, so it carries no marker.
- **Changes from the first M4c2 draft:**
  - `shots_on_target` leaves DM-CM, AM-Winger and ST;
  - `pass_completion_pct` leaves CB, FB-WB, DM-CM and AM-Winger;
  - `long_pass_completion_pct` stays in CB, FB-WB and DM-CM through the allow-list.
- **Signature trait:** `restart_key_passes` in every group. By rule: CB r 0.719, FB-WB 0.747, DM-CM 0.932, ST 0.930. By override: AM-Winger 0.838.

  | Group | P90 of the count | Minimum count | Holders (gated) |
  |---|---|---|---|
  | CB | 1 | 5 | 4 of 255 |
  | FB-WB | 4 | 5 | 20 of 249 |
  | DM-CM | 13 | 13 | 33 of 294 |
  | AM-Winger | 19 | 19 | 31 of 277 |
  | ST | 1 | 5 | 5 of 158 |

  Restart key passes include throw-ins (5.6% overall, §9). Before the trait is labelled "set-piece delivery", M4e checks the throw-in share among the FB-WB holders.
- Every other metric × group is display, with its reason (rule id) carried per cell.

### 10.7 Dimensions and directions (reviewed by the stats-reviewer)
Directions:
- `higher_better` / `lower_better` colour a percentile as good or bad;
- `style` is shown as a percentile and never coloured (plan 005).

| Dimension | Metric (direction) |
|---|---|
| Passing | pass_completion_pct (higher), long_pass_completion_pct (higher), long_passes_attempted (style), team_pass_share (style), passes_attempted (style), passes_completed (style), switches (style) |
| Progression | progressive_passes (higher), final_third_entries_pass (higher), box_entries_pass (higher) |
| Carrying | progressive_carries (higher), final_third_entries_carry (higher), box_entries_carry (higher), dribbles_attempted (style), dribbles_completed (higher), dribble_success_pct (higher), carries (style), fouls_won (style) |
| Creation | op_key_passes (higher), op_xg_assisted (higher), key_passes (higher), xg_assisted (higher), xg_assisted_direct (higher), crosses_attempted (style), cross_completion_pct (higher), op_assists (higher), assists (higher), restart_key_passes (higher), restart_xg_assisted (higher), restart_assists (higher) |
| Goal threat | np_shots (higher), np_xg (higher), np_shots_on_target (higher), shots_on_target (higher), np_xg_per_shot (higher), shots (higher), xg (higher), xg_per_shot (higher), goals (higher) |
| Defending | tackles_attempted (style), tackles_won (higher), tackle_success_pct (higher), interceptions (style; was higher, §12), ball_recoveries (higher), pressures (style), clearances (style), blocks (style), dribbled_past (lower), fouls_committed (style) |
| Aerial | aerials_won (higher), aerial_win_pct (higher), aerials_lost (style) |
| Ball security | dispossessed (lower), miscontrols (lower) |

- `tackles_attempted` and `dribbles_attempted` are attempts whose success rates are display-only, so they credit no quality: `style`.
- `pressures` is largely a team-pressing measure (it fails D9 in CB, DM-CM and ST): `style`.
- `aerials_lost` is a volume of contests, not a failure rate (`aerial_win_pct` is the rate): `style`.
- Clearances and blocks depend on how much a team defends near its own goal: `style`.
- `dribbled_past` (lower) rises with how often a player engages; its explanation carries that note.
- "Progression" holds pass progression only; carry progression is under Carrying. The UI may label them "Progressive passing" and "Carrying & dribbling" (minor, M4e).
- Every metric applies to the five outfield groups and never to GK (D21).

### 10.8 M4f pre-registration (user, 2026-10-04)
- **Equal weight per family.** Each dimension (family) present in a group's DNA gets the same total weight, split equally among its DNA metrics. Without this, Defending (8 of 24 in DM-CM) and work-rate/aerial volume (ST: Defending 6 + Aerial 3 of 20, Goal threat 2) would dominate the distance. M4f runs a sensitivity check: the top-10 neighbour overlap, equal weights vs family weights.
- **Dimension scores use directional metrics only** (`higher_better` / `lower_better`), never `style` metrics.
- **One percentile basis per vector.** `global_ok` is per cell, so per-cell bases would mix local and global percentiles in one vector. M4f fixes one basis per vector.
- **Missing DNA ratio policy: proposed, not decided** (user decides before M4f). A row below `min_attempts` has a null ratio percentile.
  - The DNA ratios in 2015/16 are `long_pass_completion_pct` (CB, FB-WB, DM-CM, AM-Winger: coverage 94.2–100%), `pass_completion_pct` ST (100%) and `aerial_win_pct` ST (98.7%).
  - **Proposal:** no imputation; imputing 50 would invent a value.
    - The null dimension is left out of that row's distances, and its family weight is re-split among the family's remaining members. A family with no member left is dropped, and the other families' weights are renormalised.
    - A row missing more than one DNA dimension, or a whole family, gets no similarity results (`dna_incomplete`).
    - The distance between two rows uses the dimensions both have.

### 10.9 Known gaps (recorded, no change now)
- **CB aerial ability (user, 2026-10-03).** CB `aerial_win_pct` has r 0.499 and 63.5% coverage. Only the volumes `aerials_won` and `aerials_lost` are in CB DNA.
  - Plan 005 backlog: empirical-Bayes shrinkage of ratios toward the group mean by attempts, then a re-evaluation of `aerial_win_pct` for CB/FB-WB.
- **ST has no chance-quality metric in DNA.** Below 0.6: np_xg 0.599, op_key_passes 0.540, op_xg_assisted 0.534; `np_xg_per_shot` is display. Linked to the same backlog item.
- **FB-WB has no crossing metric in DNA.** `crosses_attempted` (r 0.787, 0% zeros) is fixed display because it includes restart deliveries. Backlog: `op_crosses_attempted`.
- **Passing quality is mostly out of DNA** after step 5 reached the ratios; only `long_pass_completion_pct` (allow-list) and ST `pass_completion_pct` remain. Backlog: a possession-relative completion metric (player completion minus team completion while on), on the D12b pattern. **Dropped (user, 2026-10-07):** M4g pre-registers two candidates only, xPass for StatsBomb and "accuracy vs context" for aggregated sources. Fewer candidates means less chance of choosing after the fact (plan 005 M4g, §12.9).
- **Redundancy below 0.85** inside the lists: `np_shots` ~ `np_xg` 0.79–0.80; `np_shots` ~ `np_shots_on_target` 0.791 (AM-Winger), 0.804 (ST); `progressive_passes` ~ `final_third_entries_pass` 0.81–0.83 (CB, AM-Winger); `op_key_passes` ~ `op_xg_assisted` 0.825 (DM-CM); ST `aerial_win_pct` = won / (won + lost), so its three aerial dimensions carry two degrees of freedom. These are handled by the family weights (10.8), not by D17.

### 10.10 Boundary report: fragile decisions to re-check on a new source
Each value is compared at full precision (printed here to 6 decimals) against the threshold of the rule that judged it, and listed when within ±0.02 of that threshold. M4c3's `derive()` returns this list. The table below has the 35 status and note rows; the 51 D9 rows are described at the end of this section.
- **The 0.7 lower-reliability bar is tracked too** (user, 2026-10-04, after the M4c2b review). It changes no status, only the "lower reliability" note on a DNA cell, but a cell can lose or gain that note on a new source. `derive()` reports DNA cells within ±0.02 of 0.7 in the same list.
- **ρ rows show the four within-league values** (user, 2026-10-04): the rule compares their mean, and the mean can hide a split.

| Rule | Group | Metric | Value | Threshold | Per league (La Liga / PL / Serie A / Ligue 1) | Outcome |
|---|---|---|---|---|---|---|
| r_full | CB | `interceptions_p90` | 0.599849 | 0.6 | — | display |
| r_full | CB | `fouls_committed_p90` | 0.589712 | 0.6 | — | display |
| r_full | CB | `aerials_lost_p90` | 0.612088 | 0.6 | — | dna |
| r_full | FB-WB | `fouls_committed_p90` | 0.597776 | 0.6 | — | display |
| r_full | FB-WB | `aerials_lost_p90` | 0.606793 | 0.6 | — | dna |
| r_full | FB-WB | `dribbled_past_p90` | 0.618266 | 0.6 | — | dna |
| r_full | FB-WB | `np_shots_on_target_p90` | 0.580878 | 0.6 | — | display (M4c2b) |
| r_full | DM-CM | `blocks_p90` | 0.608874 | 0.6 | — | dna |
| r_full | DM-CM | `np_shots_on_target_p90` | 0.592665 | 0.6 | — | display (M4c2b) |
| r_full | ST | `dribbled_past_p90` | 0.597873 | 0.6 | — | display |
| r_full | ST | `np_xg_p90` | 0.599369 | 0.6 | — | display |
| r_full | ST | `tackles_attempted_p90` | 0.612458 | 0.6 | — | dna |
| ρ trigger (duration) | FB-WB | `final_third_entries_pass_p90` | 0.409583 | 0.4 | 0.346 / 0.600 / 0.313 / 0.380 | display (tsb) |
| ρ trigger (pass share) | FB-WB | `final_third_entries_pass_p90` | 0.401950 | 0.4 | 0.386 / 0.581 / 0.302 / 0.339 | display (tsb) |
| ρ trigger (duration) | FB-WB | `progressive_carries_p90` | 0.407480 | 0.4 | 0.324 / 0.385 / 0.508 / 0.413 | dna (AL) |
| ρ trigger (duration) | FB-WB | `long_pass_completion_pct` | 0.417085 | 0.4 | 0.587 / 0.430 / 0.296 / 0.355 | dna (AL) |
| ρ trigger (pass share) | FB-WB | `long_pass_completion_pct` | 0.416738 | 0.4 | 0.568 / 0.433 / 0.295 / 0.370 | dna (AL) |
| ρ trigger (pass share) | AM-Winger | `np_xg_p90` | 0.410867 | 0.4 | 0.323 / 0.435 / 0.401 / 0.484 | display (tsb) |
| ρ trigger (pass share) | AM-Winger | `op_xg_assisted_p90` | 0.419109 | 0.4 | 0.460 / 0.322 / 0.525 / 0.369 | display (tsb, U1) |
| ρ trigger (duration) | AM-Winger | `pass_completion_pct` | 0.383490 | 0.4 | 0.321 / 0.295 / 0.654 / 0.264 | — (pass share triggers) |
| ρ trigger (pass share) | AM-Winger | `pass_completion_pct` | 0.410877 | 0.4 | 0.323 / 0.412 / 0.646 / 0.263 | display (tsb) |
| ρ trigger (pass share) | AM-Winger | `np_shots_on_target_p90` | 0.389822 | 0.4 | 0.412 / 0.306 / 0.364 / 0.477 | dna (M4c2b) |
| ρ trigger (pass share) | ST | `np_shots_on_target_p90` | 0.395111 | 0.4 | 0.449 / 0.467 / 0.126 / 0.538 | dna (M4c2b) |
| Allow-list bound | CB | `progressive_carries_p90` | 0.548655 | 0.55 | 0.459 / 0.620 / 0.561 / 0.556 | dna (AL) |
| Lower-reliability note (r_full) | CB | `box_entries_pass_p90` | 0.707877 | 0.7 | — | dna, no note |
| Lower-reliability note (r_full) | CB | `dribbles_attempted_p90` | 0.698475 | 0.7 | — | dna, note |
| Lower-reliability note (r_full) | FB-WB | `box_entries_pass_p90` | 0.702734 | 0.7 | — | dna, no note |
| Lower-reliability note (r_full) | FB-WB | `op_key_passes_p90` | 0.714213 | 0.7 | — | dna, no note |
| Lower-reliability note (split-half r) | FB-WB | `long_pass_completion_pct` | 0.693778 | 0.7 | — | dna, note |
| Lower-reliability note (r_full) | DM-CM | `fouls_committed_p90` | 0.706483 | 0.7 | — | dna, no note |
| Lower-reliability note (r_full) | DM-CM | `interceptions_p90` | 0.686207 | 0.7 | — | dna, note |
| Lower-reliability note (r_full) | DM-CM | `op_key_passes_p90` | 0.692856 | 0.7 | — | dna, note |
| Lower-reliability note (r_full) | DM-CM | `op_xg_assisted_p90` | 0.714909 | 0.7 | — | dna, no note |
| Lower-reliability note (r_full) | AM-Winger | `np_shots_on_target_p90` | 0.700334 | 0.7 | — | dna, no note |
| Lower-reliability note (r_full) | ST | `blocks_p90` | 0.715456 | 0.7 | — | dna, no note |

- CB `interceptions_p90` is display by 0.00015.
- **Known limitation: ST goal-threat volume is possession-sensitive, and the mean hides it** (stats-reviewer, M4c2b review; user 2026-10-04: recorded, no rule change now).
  - ST `np_shots_on_target_p90`, pass share: 0.449 / 0.467 / 0.126 / 0.538. Three of four leagues are above 0.4, and Serie A pulls the mean to 0.395.
  - ST `np_shots_p90`, pass share: 0.585 / 0.523 / −0.002 / 0.382 (mean 0.372; duration 0.537 / 0.486 / 0.001 / 0.338). Two leagues are above 0.4 and Ligue 1 is just below. Serie A again pulls the mean down. It is outside ±0.02, so it has no row above.
  - Both are ST's only Goal threat DNA metrics, so in ST that family partly measures how much the team has the ball.
  - Plan 005 M4g evaluates a "majority of leagues above the trigger" rule, applied to all metrics at once (never to these cells alone).
- No zero-share, ratio-r, coverage or D12b value is within ±0.02 of a status threshold (the 0.6, 0.90 and 0.3 cut-offs).
- The `shots_on_target_p90` cells of the first draft (DM-CM r 0.619, AM-Winger ρ 0.386, ST ρ 0.382) left the report when the column became fixed display.
- **D9 rows (user, 2026-10-05).** D9 sets the percentile basis (global or local), not the status. `derive()` reports every **DNA** cell whose D9 margin is within **3 pts** (`D9_BOUNDARY_MARGIN_PTS`).
  - **The margin** (`Cell.d9_margin`, on every cell) is the deciding league mean's signed distance to the [40, 60] edge:
    - inside the band, the nearest league mean's distance (positive);
    - outside, the furthest-out league's distance (negative).
  - **The row** shows that league mean, the edge and the four league means.
  - **Why 3 pts:** a league mean of global percentiles over ~80 players has a standard error of about 29/√80 ≈ 3.2 pts (the SD of uniform percentiles is 100/√12 ≈ 29). A margin under 3 pts is within sampling noise.
  - **2015/16: 51 of 101 DNA cells are within 3 pts.** 33 are `global_ok` but could fail, and 18 fail but could pass.
    - Example: DM-CM `team_pass_share` is 2.50 pts inside (Serie A 42.50).
    - A first count of 61 used each league's unsigned distance to either edge. That also counted cells whose D9 is decided by another league far outside the band, e.g. CB `clearances` (PL 73.5, margin −13.5).
  - **Input for the M4f one-basis decision (§10.8):** half the DNA cells' basis is within noise of flipping, so a per-cell basis would not be stable across sources or seasons.
  - Every cell (not only DNA) carries `d9_margin`. About 30 cells have a margin within 0.5 pts. Examples:
    - `miscontrols_p90` FB-WB 40.0014 (ok);
    - `fouls_committed_p90` DM-CM 40.0146 (ok);
    - `tackles_attempted_p90` AM-Winger 39.99 (fails since the per-group dedupe, 10.1).

### 10.11 Recorded concerns (stats-reviewer, M4c2 review; user 2026-10-04: record only, no rule changes)
- **The hard 0.6 cut-off vs its own sampling error.** One fixed odd/even split at n ≈ 250 has a standard error of several hundredths.
  - CB `interceptions_p90` (0.599849) and ST `np_xg_p90` (0.599369) are dropped by < 0.001. Meanwhile CB DNA keeps `pressures`, `fouls_won` and `dribbles_attempted`, and ST Goal threat is `np_shots` alone.
  - Plan 005 M4g: multi-split reliability averaging, applied to all metrics at once.
- **Family weights (10.8)** may give positionally odd similarity: CB has 5 families (Defending 20%), and ST Goal threat (1 metric) weighs the same as Aerial (3 metrics, 2 degrees of freedom). Plan 005 M4f: a per-family contribution report per group.
- **Signature reliability is driven by the few designated takers** (CB 87.5% zeros, r 0.719; ST 80.4%, r 0.930). A high r shows a stable role, not skill at it. A check without the top-k rows, or with a rank-based r, is open.
- **The signature minimum is a season count, not a per-90 rate.** A 900-minute player needs the same count (DM-CM 13, AM-Winger 19) as a 3,400-minute one. This disadvantages low-minute players, against the per-90 principle; accepted.
- **Ratio reliability is judged on a noisier population than the one scored.** The split-half uses every gated row with an attempt in each half, not only rows meeting `min_attempts`. This is conservative (it can only exclude); CB `aerial_win_pct` (0.499) is a candidate for a recheck.
- **M is slightly low, so the minimums are slightly lenient.**
  - The observed variance is taken around the attempt-weighted mean: up to +7.6% (ST `aerial_win_pct`).
  - M pools the leagues while the reliability check demeans within league: within-league M is up to 7% higher (CB `pass_completion_pct` 37.6 pooled vs 40.3).
  - No ratio status changes on these margins.
- **Small local cohorts.** ST local cohorts are 35–44, and ST `aerial_win_pct` local populations 35–42; at n = 35 one rank step is about 2.9 percentile points. About 30 D9 cells sit within 0.5 pts of the band (10.10).

---

## 11. Cohorts and percentiles (plan 005 M4d, 2026-10-05)

`analytics/cohorts.py` is the only place that applies these rules; `analytics/percentiles.py` holds the formula; `pipeline/build_percentiles.py` writes `data/processed/player_season_percentiles.parquet`. `spikes/m4d_percentiles_reference.py` re-implements them without eleveniq and checks the output.

### 11.1 Definitions
- **Row status** (`percentile_status`), first match: `not_in_pool` → `goalkeeper` (not `is_outfield`) → `below_gate` (not `meets_min_minutes`) → `scored` (D4). Only `scored` rows receive a percentile; every other row keeps its raw value with null percentiles.
- **Eligible** for metric m in group g: a scored row of g whose value is non-null and, for a ratio, whose denominator (as stored: Σ plus − Σ minus) is ≥ the cell's `min_attempts` (D19). A ratio cell without `min_attempts` has no eligible row: `no_signal` (τ² ≤ 0, §10.5) or `no_min_attempts` (`xg_per_shot`, unmeasured, fixed display).
- **Reference population** for (m, g, scope): the eligible rows of g (local: and of one league), then the D3 dedupe within that population (most `minutes_played`, then lowest `competition_id`, then lowest `team_id`). Filter first, then dedupe (§10.2).
- **Scored cells:** every eligible row, scored against its population. The rows D3 drops from a population (2015/16: 5 global — Shelvey, Khazri, Quagliarella, Perotti, Koné — and 3 local — Shelvey, Quagliarella, Perotti) are scored but are not reference rows.
- **Formula:** percentile = (less + 0.5 · equal) / n × 100 against the population, n its size. For a member this is Hazen with average ranks (D11), strictly inside (0, 100). **A row scored against a population that does not contain it can be exactly 0 or 100**: a D3 outsider, or a D5 second-group row (user, 2026-10-05: the formula is kept so the D9 league means match the measurements exactly). 2015/16: Shelvey `fouls_won_p90` local 0, Perotti `progressive_carries_p90` local 100, Ben Arfa (second group ST) `dribbles_attempted_p90` local and global 100.
  - **M4e display (user, 2026-10-05):** a stored 100 is shown as ">99" and a stored 0 as "<1". The stored values are unchanged.
- **Direction is not applied:** a percentile is a rank of the raw value (high = more). `higher_better` / `lower_better` only colour it (§10.7), so the D9 means stay comparable.
- **Cell status** per scope (`status_local`, `status_global`), first match: the row status (if not scored) → `no_signal` / `no_min_attempts` → `below_min_attempts` → `null_value` → `cohort_too_small` (n < `MIN_COHORT_SIZE` 30) → global only: `d9_undefined` (`global_ok` None) / `league_inconsistent` (D9 fails) → `ok`. Only `ok` carries a percentile; n is stored whenever the row is eligible.
- **D9 (user, 2026-10-05): a failing cell's global percentile is null**, as D9 says; an undefined D9 (goals/assists p90, `xg_per_shot`, `team_pass_share` outside DM-CM/CB, a pool league without rows, or — §11.3 — a pool league population below 30) is null too. `basis` comes from the registry cell. M4d stores both scopes and does **not** decide M4f's one basis per vector (§10.8).
- **D5 (user, 2026-10-05):** a gated ambiguous row with a `second_position_group` gets a second set of output rows (`cohort_role = "second"`), scored against the second group's populations with that group's `min_attempts`. It is never a member of them, and it is display-only: never DNA, similarity or rarity.
- **Output (user, 2026-10-05): long format**, one row per season row × registry metric × cohort role, sorted by (key, `cohort_role`, `metric`), byte-deterministic for the same inputs and environment. Plan 005 sketched wide columns; ratio populations differ per metric, so n and status are per metric. Provenance columns: `registry_version`, `measurement_source`, `min_cohort_size`, `min_minutes_threshold`, `season_parquet_sha256`, `polars_version` (user, 2026-10-06: the bytes depend on the writer, so a sha256 change after a polars upgrade is explainable).
- **Guards (the build raises):** a row whose `source` ≠ `MEASUREMENT_SOURCE` (CLAUDE.md: one source per cohort); a season parquet whose sha256 differs from the measurements' provenance (re-run `--write-json` after any rebuild, so the registry describes the data scored); a D6 row (`minutes_gk` ≥ 45 and outfield minutes ≥ 45); `meets_min_minutes` ≠ the recomputed nominal gate; a GK row or a group outside `applies_to` reaching a population (D21).

### 11.2 Spike (M4d1)
`spikes/m4d_percentiles_reference.py` counts ranks in plain Python and reads `min_attempts`, `no_signal` and the D9 league means from the measurements file. It fails unless the parquet hash and source match the provenance, the gate recomputes, no D6 row exists, and every stored ratio equals numerator / denominator (`aerial_win_pct` is stored null below `MIN_AERIAL_CONTESTS` 10, below every aerial `min_attempts`).
- **Fixture** `tests/fixtures/m4d_percentiles/reference.json`: 15 output rows × 54 metrics for hand-picked rows, each with its reason asserted by the spike — the movers above (both edges), Khazri (global outsider, local member), a CB tie at `box_entries_carry_p90` = 0 (percentile = 0.5 · zeros / n; CB `tackle_success_pct` no_signal), Ighalo (ST, 40 long passes < 43: null, absent from both populations, and removing him changes no other row), Kane (ST long-pass local `cohort_too_small`, global scored), Naismith (below gate), Schmeichel (GK), Bellarabi (Bundesliga), Ben Arfa (primary AM-Winger and second ST), one plain row per group.
- **Self-check** (the spike's own full output fed to `--check-output`): 142,614 cells; the D9 league means of all 181 `global_ok` cells reproduce the measurements within 1e-8; the 51 `league_inconsistent`, 3 `cohort_too_small` and the `d9_undefined` cells agree with them; 263 population sizes equal the recorded `ref_n_*`. (Before §11.3: 184 / 53.)
- **Review (stats-reviewer, 2026-10-06): approve with conditions.** No member of a population is at 0 or 100; every exact 0 / 100 is a row outside its population (4 primary cells — Shelvey, Perotti ×2, Koné `long_pass_completion_pct` global 0 of 254 — and 47 second-group cells). Conditions: (1) the D9 small-league rule, §11.3; (2)–(3) M4e display items (plan 005 M4e); (4) M4d2 tests (plan 005 M4d).

### 11.3 D9 is undefined on small league populations (user, 2026-10-06)
- **Why** (stats-reviewer condition 1): the standard error of a league mean of percentiles is ≈ 29/√n, 14.5 pts at n = 4 and 8.4 at n = 12, as large as the ±10 band. Three ratio cells passed D9 while a league held fewer than 30 rows, so "pass" meant "could not be tested".
- **Rule:** §10.3 D9 band, amended. Applied in `registry.derive()` (`Rules.min_cohort_size` = `config.MIN_COHORT_SIZE`) and in the M4d spike. No measurement is regenerated: the JSON already holds `ref_n_local`.
- **Effect (2015/16): no status changes and no DNA cell changes.** `REGISTRY_VERSION` changes (f1f2b0b1… → d21458f9…). Seven display cells change D9:

  | Cell | League populations (La Liga / PL / Serie A / Ligue 1) | D9 before → after | Basis |
  |---|---|---|---|
  | ST `long_pass_completion_pct` | 16 / 12 / 19 / 17 | ok → undefined | global → local |
  | FB-WB `np_xg_per_shot` | 4 / 15 / 12 / 12 | ok → undefined | global → local |
  | DM-CM `np_xg_per_shot` | 23 / 27 / 45 / 23 | ok → undefined | global → local |
  | ST `np_xg_per_shot` | 25 / 25 / 23 / 23 | fails → undefined | local |
  | `dribble_success_pct` AM-Winger | 17 / 14 / 5 / 5 | fails → undefined | local |
  | `dribble_success_pct` DM-CM / FB-WB | 2 / 3 / 3 / 1; 3 / 1 / 2 / 1 | fails → undefined | local |

  (The last row holds two cells, so the table has seven.)

  The last four already had a null global percentile. In the M4d output, AM-Winger `dribble_success_pct` and ST `np_xg_per_shot` change from `league_inconsistent` to `d9_undefined`; DM-CM and FB-WB `dribble_success_pct` stay `cohort_too_small` (global populations 9 and 7, checked first). The boundary report is unchanged (86 rows; the D9 rows are DNA cells only).
- **Consequence:** a cell whose local population is below 30 now always has a null global percentile, and a global population is never smaller than a local one (every locally eligible player is eligible globally). So one scope can no longer be `cohort_too_small` while the other has a value: the 183 cells of that kind in the first M4d1 run have both scopes null now.

### 11.4 Implementation and run (M4d2, 2026-10-06)
- **Code:** `analytics/cohorts.py` (row status, input guards, D3 dedupe, eligibility, reference populations, D21 refusal), `analytics/percentiles.py` (`hazen`, `score_cell`), `pipeline/build_percentiles.py` (the long table; `config.MIN_COHORT_SIZE` 30, `config.MIXED_POSITION_MINUTES` 45). The D9 outcome and basis come from the registry cell.
- **Output (2015/16):** 142,614 rows = 2,628 season rows × 54 registry metrics + 13 D5 second-group rows × 54. `ok` cells: 60,789 local, 44,966 global. Two builds write identical bytes (polars 1.44.2, recorded in `polars_version`).
- **Spike `--check-output`:** every cell equals the independent recomputation; the D9 league means of all 181 `global_ok` cells reproduce `statsbomb_27.json` within 1e-8; 51 `league_inconsistent`, 3 `cohort_too_small` and 263 population sizes agree.
- **Exact 0 / 100:** only rows outside their population — primary: Shelvey `fouls_won_p90` local 0, Perotti (Genoa) `progressive_carries_p90` and `team_pass_share` local 100, Koné (Sunderland) `long_pass_completion_pct` global 0; second-group rows: 36 local and 11 global cells. No member of a population is at 0 or 100 (integration test).
- **Mutation checks:** dropping `meets_min_minutes` from the row status turns 4 fast tests red (incl. the below-gate-in-no-population test); scoring ties by minimum rank turns 6 red (incl. the hand-computed tie tests). Both reverted.

### 11.5 M4e display rules (comparison page, 2026-10-06)
`tools/compare_page.py` renders 2–3 player-seasons from `player_season_percentiles.parquet` and the registry; identity and context (name, team, minutes, second group, signature-trait season counts) come from `player_season_stats.parquet`, whose sha256 must equal the percentiles' `season_parquet_sha256` (user). It computes nothing statistical; every number sits in a span naming its parquet cell, registry field or config label, and an integration test traces each one.
- **Percentiles:** a stored 100 is ">99", a stored 0 "<1" (§11.1); any other value is rounded half-up to an integer and clamped to 1–99, so a member is never shown at an extreme.
- **Null percentiles** show their cell status as text (e.g. "not used: league means differ (D9)" under the global label), never a blank.
- **Selected populations:** a ratio cell with `min_attempts` reads "n 64 of 157 STs with ≥ 43 long passes". N is the n of the fixed reference metric `passes_attempted_p90` in the same population: its value is never null, so every scored row carries its n in both scopes (its global percentile can still be null by D9). An integration test checks that its n equals the number of scored rows in every local and global population after the D3 dedupe (user).
- **Zero-heavy cells:** a zero is "tied at zero, not a rank". **Signature traits** are holder / non-holder (`registry.is_signature_holder`, §1), never a percentile. In a D5 second-group section a signature trait is "not assessed (second group, display only)": no verdict against a population the player is not in (user). The three restart metrics are labelled "Restart … (corners, free kicks, throw-ins)" until the FB-WB throw-in share is measured (§10.6; plan 005 backlog).
- **Team style:** `long_pass_completion_pct` always carries a note that it reflects team passing style, and it, `style` metrics and every cell with the registry's `team_style_biased` flag are never coloured good/poor (§10.7). The flag, not the reason code, is checked: allow-listed DNA cells carry the flag with reasons `allow_listed`, possession-trigger display cells carry the reason `team_style_biased` (16 cells in 2015/16: 9 DNA, 7 display).
- **Second position group** is shown only for `meets_min_minutes` rows (§8); **D5 second-group rows** are a separate display-only section.
- **Mixed groups** (user): when the players' primary groups differ, every percentile says the group it ranks against ("vs STs") and the header states that cross-group values are not directly comparable.
- **Family labels:** "Progressive passing" and "Carrying & dribbling" (§10.7). Display reasons and overrides are shown per cell (§10.3).
- **No radar** until M4f fixes one basis per vector (§10.8; user).
- **Observations (M4e face-validity review, user 2026-10-06).** Pages: Kanté vs Busquets, Özil vs Payet, Messi vs Neymar, Stones vs Morgan, and Morgan vs Huth as a check.
  - Every verifiable count on the pages matched (Özil 19 assists / 6 goals, Payet 9 goals, Messi 26 goals, Neymar 24).
  - **Busquets' low defensive volumes are mainly a Barcelona team effect, not possession** (stats-reviewer). He is at the 11th percentile for interceptions and the 17th for ball recoveries, which reads as a weak defender. Barcelona make 9.1 interceptions per match against 13–18 for every other La Liga team, and PSG, at similar possession, sit near the middle. Opponent possession matters with an elasticity well below 1, so a rate per 1,000 opponent passes (the user's first idea) was rejected: it over-corrects and flips the possession correlation. A partial adjustment with a pre-registered β ∈ {0, 1, fitted}, fitted on one season and tested on another, and a team-share variant are pre-registered for M4g in plan 005. The direction of `interceptions` is open for M4f/M9.
  - Morgan's local clearances percentile (30th) is not a Leicester effect: Huth, at the same club, is 94th.
- **Known StatsBomb coding quirk: a passed penalty is a shot** (2026-10-06). In Barcelona 6–1 Celta Vigo (La Liga, 2016-02-14), Messi touched a penalty off to Suárez (80:58). StatsBomb codes the touch as a Shot of type Penalty, not a Pass, and Suárez's 81' goal has no key pass and no `goal_assist`. So Messi has 15 assists in our data against the official 16. Side effect under D15: the touch counts as a missed penalty for Messi (outcome Wayward, xG 0.78), in his `shots` and `xg` but not in `np_shots` or `np_xg` (match row: shots 4 / np 3, xg 0.928 / np 0.144), and Suárez's goal counts as an open-play goal with no assist: his 81' shot is of type Open Play, so it is in his `np_shots`, `np_xg` and goals (one shot of contamination in his non-penalty metrics). No change: we keep StatsBomb's coding.

---

## 12. DNA vector (plan 005 M4f, user decisions 2026-10-06)
The four open M4f items in §10.8 and plan 005 M4f were decided from the 2015/16 data (primary scored rows of the pool leagues, the M4d output). The possession adjustment is not part of M4f; it stays pre-registered for M4g (plan 005).

### 12.1 One percentile basis per vector: local
- **Data:**
  - 29 of 101 DNA cells have a null global percentile, because D9 fails or is undefined (CB 6, FB-WB 7, DM-CM 8, AM-Winger 4, ST 4; `interceptions` in every group where it is DNA);
  - 51 of 101 are within 3 pts of flipping D9 (§10.10);
  - every DNA cell has a local percentile (local n 35–84).
- **Decision (user):** every vector uses league-local percentiles (`config.DNA_PERCENTILE_BASIS`). The robust z is computed on the same local reference population (the D3-deduped population of §11.1).
  - D9 therefore no longer affects the vector.
  - The registry's per-cell `basis` stays a display field (M4e tables).
  - Global percentiles stay in the display tables.
- **Cost, accepted:**
  - leagues count as equal strength (consistent with "no league adjustment", CLAUDE.md);
  - ST local cohorts are small (n 35 ≈ 2.9 pts per rank step, §10.11);
  - an explanation names the league population ("vs La Liga STs").
- The global alternative (dropping the failing cells) was rejected. It would change the DNA lists by 4–8 metrics per group and flip on a new source.

### 12.2 Missing DNA ratio: the §10.8 proposal, adopted
- **Data:** 32 of 1,233 primary scored rows miss exactly one DNA ratio (`below_min_attempts`).
  - `long_pass_completion_pct`: FB-WB 12, AM-Winger 16, DM-CM 2;
  - ST `aerial_win_pct`: 2.
  - No row misses two, and no family is emptied, so `dna_incomplete` does not occur in 2015/16.
- **Decision (user):**
  - no imputation;
  - the missing metric's family weight is re-split among the family's present members;
  - a row missing more than one DNA metric, or a whole family, is `dna_incomplete` (no similarity results);
  - a pair distance uses the metrics both rows have, normalised by the weight they share.

### 12.3 `interceptions` is `style`
- **Data:** it fails D9 in every group, and it tracks team style (Barcelona 9.1 interceptions per match against 13–18 for every other La Liga team, §11.5). Its Defending siblings `tackles_attempted`, `pressures`, `clearances` and `blocks` are already `style` (§10.7).
- **Decision (user):**
  - `higher_better` → `style`, with the note "team-style influenced (league means differ in every group, D9)";
  - it is never coloured, and it leaves the Defending dimension score (directional metrics only, §10.8);
  - the vector and the distance are unchanged (percentiles are not directional, §11.1).
- **No status changes.** The M4e golden page changes only on the interceptions row (direction label, neutral colour, note) and the registry version.

### 12.4 `REGISTRY_VERSION` covers every spec field
`_version` hashed the measurements, the rules and the cells, but not `MetricSpec`, so §12.3 would not have changed it.
- **The fix (user, 2026-10-06):** the hash now includes every `MetricSpec` field, sorted by column. That covers family, direction, unit, display name, count column, ratio terms, `applies_to`, fixed display and notes. A wording-only edit therefore also requires a percentiles rebuild.
- **Tests, each mutation-checked against the earlier (column, family, direction, unit) hash:**
  - with identical cells, a direction-only, a family-only or a display-name-only change alters the version;
  - reordering `SPECS` does not alter it.
- **Ratio terms were already guarded.** `_validate_specs` raises unless a spec's ratio terms equal the measured definition, so a spec-only edit fails, and an edit with a spike re-run changes the measurements and thus the version. A test covers both paths.
- Version: d21458f9… → aaecf86f….
- The percentiles were rebuilt, and `spikes/m4d_percentiles_reference.py --check-output` passes (181 / 51 / 3, 263 populations).

### 12.5 Weighting checks: report only
- Family weights stay as pre-registered (§10.8).
- M4f reports, without a switch rule:
  - the equal vs family weight check;
  - the with/without `np_shots_on_target` check (AM-Winger, ST);
  - the per-family contribution report;
  - (user) the top-10 neighbours of the face-validity players: Kanté, Busquets, Özil, Payet, Messi, Neymar, Stones, Morgan.
- Any weighting change needs a new ADR entry argued from that report. M4g owns validation.

### 12.6 Method (decided by default; reviewed by the stats-reviewer in the M4f1 `/finish-chunk`, user decisions 2026-10-06 below)
- **Robust z:** (x − median) / (1.4826 · MAD) over the local reference population. When MAD = 0, use IQR / 1.349; when both are 0, z is null with reason `zero_spread` (plan 005 D10).
  - **Stored uncapped (user).** |z| reaches 13.7 (Isco, DM-CM `box_entries_carry_p90`); values above 8 are real role outliers.
  - In 2015/16, MAD = 0 and IQR = 0 never occur in the 404 local populations.
  - **M5 decides whether z is used, and how it is capped.**
- **Weights:** each family present in the group's DNA gets 1/|F|, split equally among its present members. In every row the weights sum to 1.
- **Dimension scores:** the mean local percentile of the family's directional members, with `lower_better` entering as 100 − p. Every score names the member metrics it uses.
  - **Per row:** a family with no directional member present in that row is null, reason `no_directional_metric`. In 2015/16 these are the 30 rows missing `long_pass_completion_pct` in FB-WB (12), AM-Winger (16) and DM-CM (2), where it is the only directional Passing member.
  - **Defending is withheld in every group** (null, reason `pending_possession_adjustment`) until M4g settles the possession question.
    - Without `interceptions`, its directional members are `ball_recoveries` and `dribbled_past`. `dribbled_past` rewards not engaging: its local percentile has ρ 0.47–0.65 with `tackles_attempted` and `pressures` (stats-reviewer). `ball_recoveries` is team-driven (Busquets, §11.5).
    - The metrics stay in the vector and the tables, so similarity is unaffected.
- **Checks' provisional distance:** weighted squared Euclidean on local percentile / 100, which decomposes exactly into family shares. M5 chooses the product metric.
- **Pair weights are symmetric (user).** For a pair, each family's weight is re-split over the metrics both rows have, so d(A, B) = d(B, A) (tested with a missing-metric case). One row's own re-split weights would make the distance asymmetric: for an AM-Winger missing `long_pass_completion_pct`, `long_passes_attempted_p90` would weigh 1/7 against 1/14. M4f3 reports whether the 32 partial rows are over-represented among the top-10 neighbours.
- **Single-metric families weigh most per metric** (stats-reviewer). For example, AM-Winger Creation (`op_key_passes`, 1/7) weighs 8× each Defending member (1/56). This is pre-registered (§10.8), and M4f3's contribution report shows it.
- **Neighbours:** same group, primary scored rows, any pool league, excluding the same `player_id`; k = `config.DNA_NEIGHBOURS_K` (10).
- **Rows:** primary only (`cohort_role = "primary"`); D5 second-group rows never enter.


### 12.7 Spike (M4f2, 2026-10-06)
`spikes/m4f_dna_reference.py` re-implements §12 without eleveniq. Row status and the D3-deduped local populations come from the M4d spike (a sibling-spike import, proven equal to the M4d output by its `--check-output`). Medians, quantiles, z, weights, dimension scores and distances are plain Python. The DNA lists, families and directions are written out, and a fast test (M4f2 code chunk) asserts they equal the registry.
- **Output (proposed; the spike's `--check-output` fixes it).** Two long files from one build, both carrying `registry_version`, `measurement_source`, `dna_percentile_basis`, `season_parquet_sha256`, `percentiles_parquet_sha256` and `polars_version`:
  - `player_dna.parquet`: one row per (season key, DNA metric), sorted by (key, metric). Columns: `position_group`, `family`, `direction`, `value`, `pct_local`, `n_local`, `metric_status` (the local cell status), `z`, `z_median`, `z_scale`, `z_scale_method` (`mad` / `iqr`), `z_status` (`ok`, `zero_spread`, or the missing reason), `weight` (the row's own weights; null when missing), `dna_status`.
  - `player_dna_dimensions.parquet`: one row per (season key, DNA family of the group), sorted by (key, family): `position_group`, `score`, `score_status` (`ok`, `no_directional_metric`, `pending_possession_adjustment`), `members` (list of the metrics used, in metric-name order; empty when null), `dna_status`.
  - `pct_local` and `n_local` are copied from `player_season_percentiles.parquet`, never re-ranked; the check compares them exactly.
- **Details decided by default:**
  - the IQR fallback uses linear-interpolated quartiles (Hyndman–Fan type 7); it never triggers in 2015/16;
  - D3 outsiders are DNA rows, scored against the population that excludes them (Shelvey `fouls_won_p90` local 0);
  - a `dna_incomplete` row keeps its vector, weights and dimension scores; only similarity refuses it;
  - dimension scores exist only for the group's DNA families.
- **Fixture** `tests/fixtures/m4f_dna/reference.json`: 8 hand-picked rows, each claim asserted by the spike. Kanté, Busquets (Defending withheld, his low defensive percentiles stay in the vector), Isco, Walcott (partial AM-Winger: `long_passes_attempted_p90` weighs 1/7, Passing `no_directional_metric`), Di Natale (partial ST: Aerial score from `aerials_won_p90` only), Shelvey, Morgan, Messi.
- **Season counts (asserted):** 1,233 primary scored rows; 32 `partial`, 0 `dna_incomplete`; 30 `no_directional_metric` Passing scores; 0 `zero_spread`, and every scale is MAD; the largest |z| is Isco's DM-CM `box_entries_carry_p90`, 13.658; row weights sum to 1 within 1e-12; d(A, B) = d(B, A) exactly, and pair weights sum to 1, over all 156,961 same-group pairs.
- **Isco is a DM-CM (user, 2026-10-06).** Real Madrid's 4-3-3 left-centre-mid maps to DM-CM, and he is the |z| 13.7 outlier: the DM-CM group mixes holding 6s and advanced 8s. M4f3 adds his top-10 neighbours to the face-validity lists; M4g evaluates whether DM-CM needs a role split. No change now.
- **Self-check:** the spike's own result written in the output schema (percentiles copied from the M4d output) passes `--check-output`: 25,121 vector cells, 7,963 dimension scores. A 1e-6 error planted in one z fails it.

### 12.8 Implementation and run (M4f2, 2026-10-06)
- **Code:** `analytics/dna.py` (pure: `spread`, `row_dna`, `pair_weights`, `pair_distance`; the distance raises for a `dna_incomplete` row or a cross-group pair) and `pipeline/build_player_dna.py`. `config.DNA_WITHHELD_FAMILIES` = ("Defending",). The build refuses percentiles whose `registry_version`, `measurement_source` or `season_parquet_sha256` differ from the registry and the season parquet, and any basis other than local. It copies `pct_local` / `n_local` and raises if a z population's size differs from the copied `n_local`.
- **Output (2015/16):** 1,233 rows: 1,201 complete, 32 partial, 0 `dna_incomplete`; 25,121 vector cells, 7,963 dimension scores. Two builds write identical bytes (both files). The spike's `--check-output` passes; d(A, B) = d(B, A) exactly over all 156,961 same-group pairs of the build's rows.
- **Mutation checks** (each reverted): pair weights from row A alone → 2 red (incl. the all-pairs symmetry test); `lower_better` not inverted → 1 red (a fast test only: with Defending withheld, `dribbled_past` is the only `lower_better` DNA metric, so 2015/16 scores do not move); an emptied family ignored in `dna_status` → 1 red; MAD without 1.4826 → 4 red (incl. the spike check).
- **Code-review fix:** `row_dna` takes `withheld` as a required keyword and the build passes `config.DNA_WITHHELD_FAMILIES`; a directional member whose direction is not `higher_better` or `lower_better` raises instead of being inverted. The output bytes are unchanged.
- **Stats review of the output (2026-10-06): pass, two concerns recorded (user, no change now).**
  - **C1: mixed-role rows dominate the extreme z.** Of the 64 rows with any |z| > 5, 34.4% have a primary-group minute share below 0.70, against 10.0% of the other rows. Examples: CB `final_third_entries_carry_p90` for CBs who also played right-back (Simon Francis z 9.5, share 0.525; Guilbert 7.6; Conte 8.1), and Walters (AM-Winger at share 0.504, second group ST) `aerials_won_p90` z 9.6. The values are real but are blended-role signals, and an upstream position-assignment matter, not M4f. M4f3 and M4g show the main-group minute share next to every |z| > 5 row; M5 handles it before Gem Finder (M6) uses z.
  - **C2: z is heavy-tailed.** 2.1% of z cells have |z| > 3, against 0.27% for a normal distribution; 95 exceed 5 and 10 exceed 8, concentrated in sparse per-90 counts (`box_entries_pass`, `aerials_won`, `dribbles_attempted`, `box_entries_carry`, `progressive_passes`). Isco's 13.66 comes from a near-zero scale (median 0.087, scale 0.081). Raw z is not comparable across metrics; M5 decides the cap (§12.6).
  - Also noted: the 32 partial rows keep the raw below-minimum ratio in `value` with null percentile, z and weight, so a display must label it. Several dimension scores rest on one metric (Aerial in CB, FB-WB, DM-CM and AM-Winger; Creation in FB-WB and AM-Winger; Passing in every group, with ST Passing `pass_completion_pct` alone).
- **Display (user, 2026-10-06; M4e page, M4f4 radar):** a dimension score shows its member count ("based on 1 metric").

### 12.9 Radar on the comparison page (M4f4, 2026-10-07)
`tools/compare_page.py` draws an inline-SVG radar from `player_dna.parquet` and shows the dimension scores from `player_dna_dimensions.parquet`. It computes nothing: every value is a parquet cell.
- **Guards:** both DNA files must carry this `REGISTRY_VERSION`, source and `DNA_PERCENTILE_BASIS`, plus the sha256 of the season parquet and of the percentiles parquet the page reads. A player's DNA metrics must equal the registry's list for the group, and `pct_local` / `n_local` must equal the percentiles rows.
- **Radar:**
  - **Axes:** the group's DNA metrics on local percentiles (§12.1), grouped by family (alternate families shaded and labelled).
  - **Players:** one outline per player, told apart by colour and dash pattern, with a legend "vs <league> <group> (n …)".
  - **Only for one shared group:** with mixed groups the page says why there is no radar.
  - **No DNA vector:** a player without one (not a primary scored row) is listed with the row status.
- **Direction:** outward means more, not better (percentiles are not directional, §11.1).
  - Style and uncoloured axes (`style`, long pass completion, `team_style_biased`, the same predicate as the table bars) have a grey italic label and a dashed spoke.
  - Directional axes are in ink, and a `lower_better` axis says "(lower better)".
  - The good/poor colours are not used on the radar, because the player colours carry identity there.
- **Partial rows (§12.2):** a missing metric leaves no vertex. The outline breaks on both sides (an open path instead of a closed one), a cross marks the rim, and a note names the status ("below the minimum of ≥ 33 long passes"). Walcott in the second golden.
- **Dimension scores:** shown in a table under the radar and as a "Dimension score" row at the head of each family's DNA section.
  - An ok score reads "61 · based on 2 metrics: …".
  - Defending reads "withheld until M4g (possession adjustment pending)"; Passing, where withheld (below), reads "withheld until M4g (team-influenced; xPass candidate pre-registered)".
  - A null score reads "no directional metric present in this row".
- **Tests:**
  - **Fast tests** cover axes grouped by family, vertex radius and angle, gap not zero, open outline, no radar for mixed groups, uncoloured style axes, member counts and withheld Defending, and the DNA guards and copy check.
  - **Integration tests:**
    - every `data-dim` span equals its parquet cell, and "based on N" equals the member count;
    - every vertex sits at `RADAR_R · pct_local / 100` on its axis, and its tooltip shows the same value and n;
    - every DNA metric is a vertex or a gap, never both;
    - second golden `golden_radar_walcott_ozil.html`.
  - **Golden diff:** the M4e golden changes by +18 / −3 lines (git; the dimension-score rows, the no-radar note, the legend sentence, the footer and two CSS lines), with 0 `data-src` cells changed.
- **Mutation checks** (each reverted):
  - missing metric plotted as 0 → 1 fast test and 3 integration tests red;
  - style axes coloured → 1 fast test red;
  - axes in the registry's alphabetical order → 2 fast tests red.
  - Dropping the family sort alone is an equivalent mutant, because `SPECS` is already contiguous by family.
- **Passing dimension score withheld where it rests on `long_pass_completion_pct` alone (user, 2026-10-07).**
  - **Why:** in CB, FB-WB, DM-CM and AM-Winger, `long_pass_completion_pct` is the only directional Passing DNA member. §11.5 says a low long-pass completion is never presented as poor individual passing (Koné 0.319 at Sunderland, 0.595 at Lorient). A one-metric Passing score does exactly that (Busquets 98, Kanté 38, Özil 87).
  - **Rule:** treated like Defending (§12.6). The score is null with `score_status` `pending_mover_stability`, and the page reads "withheld until M4g (team-influenced; xPass candidate pre-registered)". ST keeps its Passing score (`pass_completion_pct`). The metric stays in the vector, so weights, distances and neighbours are unchanged.
  - **Code:** `config.DNA_WITHHELD_DIMENSIONS` (family → status and groups) replaces `DNA_WITHHELD_FAMILIES` (§12.8). The build passes `dna.withheld_for(group)` to `row_dna`, and the spike holds the same table written out. A fast test asserts the Passing groups are exactly those whose only directional Passing DNA member is `long_pass_completion_pct`. The page refuses a dimensions file whose withheld statuses differ from the config.
  - **Effect (2015/16):** 1,075 Passing scores withheld (every non-ST row). This includes the 30 rows that were `no_directional_metric` (§12.6): the withhold is checked first, so `no_directional_metric` now occurs 0 times. The 158 ST scores are unchanged, and the vector file's cells are unchanged. The spike's `--check-output` passes (25,121 vector cells, 7,963 dimension scores), and the fixture changes only on the Passing scores of the 7 non-ST rows.
  - **Goldens:** each changes by one line, with 2 `data-dim` cells removed: Ben Arfa Passing 62 in the M4e golden, Özil Passing 87 in the radar golden. 0 `data-src` cells changed. The golden diff summary now counts `data-dim` cells.
  - **Restoring it is an M4g decision**, taken with the mover stability check (plan 005 M4g).
  - **Page wording (user, 2026-10-07, after the M4g pre-registration):** "withheld until M4g (team-influenced; xPass candidate pre-registered)". The status code stays `pending_mover_stability`, because the mover check is still one of the rules the xPass candidate must pass. The candidate metrics are pre-registered there (user, 2026-10-07):
    - for StatsBomb, an xPass model with execution, execution under pressure (the Passing score) and ambition (`style`);
    - for aggregated sources (Sportmonks), "accuracy vs context";
    - all under the same rules, reviewed by the stats-reviewer before code.

---

## 13. M4g pre-registration (plan 005 M4g0, user decisions 2026-10-07)
Fixed before any M4g measurement. The stats-reviewer reviewed the specs before any code (APPROVE WITH CONDITIONS; plan 005 "M4g — detailed plan" holds the full spec and the review figures). Outcomes go into later subsections.

### 13.1 Rules fixed before results
- **Multi-split r_full** replaces the odd/even split for every metric × group, adopted unconditionally. It removes split-assignment noise only; sampling error over players (SE ≈ 0.04) remains.
- **Majority-of-leagues trigger:**
  - It fires when ≥ 3 of the 4 pool leagues have |ρ| ≥ 0.4 with one shared sign among them. It is an OR with the mean rule, and §10.2's no-sign rule applies.
  - **Known beforehand:** it removes ST `np_shots_on_target` from DNA, and not ST `np_shots`.
- **Minute-bucket reliability** (900–1350, 1350–2000, ≥ 2000): report only.
- **Possession candidates:**
  - β is judged leave-one-league-out (fit on 3 pool leagues, criteria on the 4th). The pipeline uses the all-4-league fit.
  - Candidates are taken in a fixed order, the first that passes: β = 0 → β̂ → team share → β = 1. Ratios take β = 0 only.
- **Mover stability:**
  - **Sample:** two stints of ≥ 450 nominal minutes in the same group (24 movers in 2015/16), pooled across groups.
  - **Baseline:** a within-team date split of non-movers, trimmed to the movers' minutes, with `min_attempts` applied to both.
  - **Threshold f̂ = (Ŝ² − 1)(1 − R_m) / R_m vs 0.5** (SD-unit scale, RMS form of S; R_m = the correlation of the matched non-mover halves): at least half of the stable differences between players travel with the player.
  - **Labels:** a paired bootstrap CI containing 0.5 → "inconclusive at n"; fewer than 12 movers → `untestable`, not a pass.
  - **Amended in §13.7:** the verdict comes from the CI, not the point estimate: PASS iff the CI upper bound < 0.5, FAIL iff the lower bound > 0.5, otherwise inconclusive. The point rule f̂ ≤ 0.5 is reported for comparison only.

### 13.2 Defending
- **`dribbled_past_p90` → `style`.** It rises with engagement: ρ with `tackles_attempted_p90` is 0.48–0.55 by group. As `lower_better` it would score Wes Morgan 55 and Mascherano 32 on Defending.
- **New candidate:** `defensive_duel_success_pct = tackles_won / (tackles_attempted + dribbled_past)`, `higher_better`.
  - The denominator terms are disjoint: lost tackles are 39.2% of attempts and are never linked to a Dribbled Past.
  - It covers all tackles, not only stopping dribblers.
  - The rules decide it, like any ratio.
- **Restore rule:**
  - A directional Defending member enters the score only if it passes all 4 possession criteria and the mover rule.
  - Defending is restored in a group when ≥ 1 member passes. Otherwise it stays withheld with a status naming the failed test.

### 13.3 Passing (xPass, StatsBomb)
- **End-location leakage (stats-reviewer decision):** the end location of a failed pass is where the ball was stopped, and length, angle, cross and switch derive from it.
  - The scoring model uses start x and y, direction, height, body part, `under_pressure` and through ball.
  - End location, length and the cross and switch flags are excluded.
  - A full-model run is a report-only sensitivity check.
  - **Cost:** without length, execution partly rewards safe choices from the same spot.
- **Model acceptance:**
  - out-of-fold mean residual within ±0.01 overall and per height, body part, start third and `under_pressure`;
  - maximum decile gap ≤ 0.03;
  - per league and per group: report only.
- **Predictions recorded before results:**
  - ambition ≈ 1 − completion (ρ −0.89 to −0.94 with `pass_completion_pct`), expected to fail D17 in ST and to trigger D12;
  - execution may trigger D12.
- **`long_pass_completion_pct` has the same length-truncation bias (recorded).** A long ball intercepted or blocked early is recorded short and leaves the denominator, which inflates the rate.

### 13.4 DM-CM role split (reading pre-registered)
- **Labels:** the 6 / 8 label (lineup slot share ≥ 0.7) plus a pivot label (single / double pivot / centre midfield), because the slot share records formation more than role.
- **"Separate regions" requires both:**
  - a normalised neighbour-label lift ≥ 0.4 for both labels;
  - |Cohen's d| ≥ 1.0 on PC1 or PC2.
- The user decides any split; a split is its own chunk.
- **Outcome (M4g1, user decision 2026-10-07): no split.**
  - The reading found no separate regions: lifts 0.30 (6) and 0.23 (8) are below 0.4, while |d| is 1.28 and 1.10.
  - The more-6 teammate sits on the 6 side in 84–87% of within-team pairs: a real role gradient, but a continuum.
  - DM-CM stays one group.
  - A 6-share context column goes to M5.

### 13.5 Dependencies
numpy and scikit-learn are in the `dev` extra of `pyproject.toml` since M4g1, where only spikes use them (code-reviewer, M4g1). They become runtime dependencies when `src/` first imports them (M4g5, the xPass stage). xP builds pin `OMP_NUM_THREADS=1` and round xP to 1e-9, so two builds are byte-identical.

### 13.6 M4g2 outcome: reliability and trigger rules (plan 005 M4g2, 2026-10-07)
Implemented as §13.1 and plan 005 M4g2 fix them, with the user's O1–O4 decisions and the stats-reviewer's pre-code conditions. **This section supersedes the §10.6 DNA lists and the §10.10 status and note rows for 2015/16.** §10's tables stay as the M4c2 record; their r values are the odd/even ones, stored next to the new values as `*_odd_even`.

**Method as run.**
- **Multi-split r_full:** K = 3200, which is the cap; seed 20261007.
  - Only ST `defensive_duel_success_pct` (r 0.090) keeps an MC SE above 0.002 (0.0021). It is not within ±0.02 of 0.6, so it gets no boundary row.
  - Every stored cell also carries the odd/even value, SD, MC SE, mean r_k, its n range and `n_undefined`.
- **Undefined r (amendment, user 2026-10-07):** "undefined in some splits fails" applies to judged gated cells only.
  - The one gated cell with a partly undefined r is CB `restart_assists_p90` (fixed display; 376 of 3200 splits).
  - 13 report-only minute-bucket cells, mostly zero-heavy restart columns, are also partly undefined. All of these keep statistics over their defined splits.
- **Independent check:** `spikes/m4g2_reliability_reference.py --check-output` agrees on 13,760 values within 2e-9.
  - It reproduces the K rule on prefixes of the stream, and every committed odd/even value exactly.
  - Its own copy of the §10.3 rules reproduces every pre-M4g2 status.
- **Pre-registration:** `spikes/expected_m4g2_flips.json` was written before the registry code changed. `spikes/m4g2_before_after.py` (the STOP rule) passed: 22 expected changes and nothing else, plus 12 cells whose status is unchanged that gained the `majority_triggered` reason.
- **Registry:** `REGISTRY_VERSION` aaecf86fa483… → 930d0019ee11….

**Status and note changes (metric × group: old → new, rule).** \* = lower-reliability note.

| Group | Metric | Change | Rule (odd/even → multi-split r) |
|---|---|---|---|
| CB | `aerials_lost_p90` | dna\* → display | r 0.612 → 0.557 |
| FB-WB | `aerials_lost_p90` | dna\* → display | r 0.607 → 0.535 |
| FB-WB | `interceptions_p90` | dna\* → display | r 0.623 → 0.585 |
| FB-WB | `np_xg_p90` | dna\* → display | r 0.644 → 0.596 |
| FB-WB | `fouls_committed_p90` | display → dna\* | r 0.598 → 0.621 |
| FB-WB | `op_key_passes_p90` | dna → dna\* | r 0.714 → 0.684 |
| DM-CM | `np_shots_on_target_p90` | display → dna\* | r 0.593 → 0.622 |
| DM-CM | `dribbled_past_p90` | dna → dna\* | r 0.736 → 0.691 |
| DM-CM | `op_xg_assisted_p90` | dna → dna\* | r 0.715 → 0.675 |
| DM-CM | `op_key_passes_p90` | dna\* → dna | r 0.693 → 0.730 |
| AM-Winger | `op_key_passes_p90` | dna → dna\* | r 0.726 → 0.667 |
| AM-Winger | `op_xg_assisted_p90` | display (team_style_biased) → display (unreliable) | r 0.623 → 0.555, so the possession step is no longer reached |
| ST | `np_shots_on_target_p90` | dna\* → display (team_style_biased) | **majority trigger**, as pre-registered |
| ST | `dribbled_past_p90` | display → dna\* (style, §13.2) | r 0.598 → 0.622 |
| ST | `blocks_p90` | dna → dna\* | r 0.715 → 0.611 |
| ST | `tackles_attempted_p90` | dna\* → dna | r 0.612 → 0.728 |
| ST | `np_xg_p90` | display (unreliable) → display (team_style_biased) | r 0.599 → 0.664; the mean rule (0.436 / 0.442) blocks it |
| all 5 | `defensive_duel_success_pct` | new → display | r 0.09–0.38; coverage 0–15% (`min_attempts` 93–562) |

The 5 new cells are counted in the 22. `dribbled_past_p90` → `style` (§13.2) changes no status.
- **`defensive_duel_success_pct` collinear pairs:** it adds 18 pairs at |ρ| ≥ 0.85, all in DM-CM, where only 3 rows meet `min_attempts`, so ρ is ±1 or ±0.866 with most metrics: noise.
  - One more pair is listed at the 0.7 report floor: AM-Winger vs `tackles_won_p90`, 0.734.
  - The ratio is display everywhere, so no D17 choice is needed.

**DNA lists (supersede §10.6).** \* = lower-reliability note; (AL) = team_style_biased, admitted by the allow-list.
- **CB (15):** aerials_won, ball_recoveries, box_entries_pass, clearances\*, dribbled_past\*, dribbles_attempted\*, final_third_entries_carry, final_third_entries_pass, fouls_won\*, long_passes_attempted, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct (AL).
- **FB-WB (17):** aerials_won, ball_recoveries, box_entries_carry, box_entries_pass, clearances, dribbled_past\*, dribbles_attempted, fouls_committed\*, fouls_won, long_passes_attempted, np_shots, op_key_passes\*, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct\* (AL).
- **DM-CM (25):** aerials_lost, aerials_won, ball_recoveries, blocks\*, box_entries_carry, box_entries_pass, clearances, dribbled_past\*, dribbles_attempted, final_third_entries_carry, fouls_committed, fouls_won, interceptions\*, long_passes_attempted, np_shots, np_shots_on_target\*, np_xg, op_key_passes, op_xg_assisted\*, pressures, progressive_carries (AL), progressive_passes (AL), tackles_attempted, long_pass_completion_pct (AL), team_pass_share.
- **AM-Winger (22, unchanged):** aerials_lost, aerials_won, ball_recoveries, blocks\*, box_entries_carry, box_entries_pass, clearances, dribbled_past, dribbles_attempted, final_third_entries_pass, fouls_committed, fouls_won, interceptions\*, long_passes_attempted, np_shots, np_shots_on_target, op_key_passes\*, pressures, progressive_carries, progressive_passes, tackles_attempted, long_pass_completion_pct.
- **ST (20):** aerials_lost, aerials_won, ball_recoveries, blocks\*, box_entries_carry, box_entries_pass, clearances, dribbled_past\*, dribbles_attempted, final_third_entries_pass, fouls_committed, fouls_won, long_passes_attempted, np_shots, pressures, progressive_carries, progressive_passes, tackles_attempted, pass_completion_pct, aerial_win_pct.
- **Signature traits:** unchanged (`restart_key_passes` in every group).
- **DM-CM Goal threat now holds `np_shots`, `np_shots_on_target` and `np_xg`.** `np_shots` ~ `np_shots_on_target` is ρ 0.840 in DM-CM, just under the 0.85 D17 rule. It is handled by the family weights (§10.8, §10.9 pattern), and it is reported here because a new source could cross 0.85.
- **ST Goal threat now holds `np_shots` alone.** It is possession-sensitive (2 of 4 leagues ≥ 0.4, §10.10), and its majority V is 0.382.

**Majority-triggered, admitted by the allow-list (O1; reason `majority_triggered`, `allow_listed`).** All 8 also fire the mean rule.

| Group | Metric | Duration: leagues ≥ 0.4 / max league \|ρ\| / fires | Pass share: leagues ≥ 0.4 / max league \|ρ\| / fires |
|---|---|---|---|
| CB | `long_pass_completion_pct` | 3 / 0.715 / yes | 3 / 0.750 / yes |
| CB | `progressive_carries_p90` | 3 / 0.589 / yes | 4 / 0.620 / yes |
| CB | `progressive_passes_p90` | 3 / 0.509 / yes | 4 / 0.558 / yes |
| FB-WB | `progressive_carries_p90` | 2 / 0.508 / no | 3 / 0.503 / yes |
| FB-WB | `progressive_passes_p90` | 4 / 0.646 / yes | 4 / 0.642 / yes |
| DM-CM | `long_pass_completion_pct` | 3 / 0.574 / yes | 3 / 0.607 / yes |
| DM-CM | `progressive_carries_p90` | 4 / 0.632 / yes | 4 / 0.637 / yes |
| DM-CM | `progressive_passes_p90` | 3 / 0.544 / yes | 3 / 0.541 / yes |

- `derive()` returns this set as `majority_admitted`; a test asserts it on statsbomb_27.
- M4g4 re-examines the 8 and states, before its run, what it decides for them.

**Boundary report (supersedes §10.10's status and note rows).** The value is printed to 6 decimals. Majority rows show V, the 3rd-largest same-sign |ρ| (O3); none has an opposite-sign league within ±0.02 of 0.4, so there are no `majority_sign` rows. The 33 status and note rows are below; the 51 D9 rows are recomputed by `derive()` and not listed.

| Rule | Group | Metric | Value | Threshold | Per league (La Liga / PL / Serie A / Ligue 1) | Outcome |
|---|---|---|---|---|---|---|
| r_full | FB-WB | `interceptions_p90` | 0.585339 | 0.6 | — | display |
| r_full | FB-WB | `np_xg_p90` | 0.595898 | 0.6 | — | display |
| r_full | DM-CM | `blocks_p90` | 0.617896 | 0.6 | — | dna\* |
| r_full | ST | `blocks_p90` | 0.611414 | 0.6 | — | dna\* |
| ρ trigger (duration) | FB-WB | `final_third_entries_pass_p90` | 0.409583 | 0.4 | 0.346 / 0.600 / 0.313 / 0.380 | display (tsb) |
| ρ trigger (pass share) | FB-WB | `final_third_entries_pass_p90` | 0.401950 | 0.4 | 0.386 / 0.581 / 0.302 / 0.339 | display (tsb) |
| ρ trigger (duration) | FB-WB | `progressive_carries_p90` | 0.407480 | 0.4 | 0.324 / 0.385 / 0.508 / 0.413 | dna (AL) |
| ρ trigger (duration) | FB-WB | `long_pass_completion_pct` | 0.417085 | 0.4 | 0.587 / 0.430 / 0.296 / 0.355 | dna\* (AL) |
| ρ trigger (pass share) | FB-WB | `long_pass_completion_pct` | 0.416738 | 0.4 | 0.568 / 0.433 / 0.295 / 0.370 | dna\* (AL) |
| ρ trigger (duration) | AM-Winger | `pass_completion_pct` | 0.383490 | 0.4 | 0.321 / 0.295 / 0.654 / 0.264 | display (tsb) |
| ρ trigger (pass share) | AM-Winger | `pass_completion_pct` | 0.410877 | 0.4 | 0.323 / 0.412 / 0.646 / 0.263 | display (tsb) |
| ρ trigger (pass share) | AM-Winger | `np_xg_p90` | 0.410867 | 0.4 | 0.323 / 0.435 / 0.401 / 0.484 | display (tsb) |
| ρ trigger (pass share) | AM-Winger | `np_shots_on_target_p90` | 0.389822 | 0.4 | 0.412 / 0.306 / 0.364 / 0.477 | dna |
| ρ trigger (pass share) | ST | `np_shots_on_target_p90` | 0.395111 | 0.4 | 0.449 / 0.467 / 0.126 / 0.538 | display (tsb, majority) |
| majority V (duration) | FB-WB | `progressive_carries_p90` | 0.385216 | 0.4 | 0.324 / 0.385 / 0.508 / 0.413 | dna (AL) |
| majority V (duration) | ST | `np_shots_on_target_p90` | 0.407722 | 0.4 | 0.447 / 0.408 / 0.139 / 0.512 | display (tsb, majority) |
| majority V (pass share) | CB | `long_pass_completion_pct` | 0.417317 | 0.4 | 0.364 / 0.750 / 0.504 / 0.417 | dna (AL) |
| majority V (pass share) | FB-WB | `progressive_carries_p90` | 0.419963 | 0.4 | 0.376 / 0.460 / 0.503 / 0.420 | dna (AL) |
| majority V (pass share) | DM-CM | `progressive_passes_p90` | 0.412802 | 0.4 | 0.413 / 0.541 / 0.396 / 0.541 | dna (AL) |
| majority V (pass share) | AM-Winger | `np_xg_p90` | 0.401357 | 0.4 | 0.323 / 0.435 / 0.401 / 0.484 | display (tsb) |
| majority V (pass share) | ST | `np_shots_p90` | 0.381793 | 0.4 | 0.585 / 0.523 / −0.002 / 0.382 | dna |
| Allow-list bound | CB | `progressive_carries_p90` | 0.548655 | 0.55 | 0.459 / 0.620 / 0.561 / 0.556 | dna (AL) |
| Lower-reliability note | CB | `box_entries_pass_p90` | 0.707266 | 0.7 | — | dna, no note |
| Lower-reliability note | CB | `dribbles_attempted_p90` | 0.696195 | 0.7 | — | dna, note |
| Lower-reliability note | CB | `fouls_won_p90` | 0.688642 | 0.7 | — | dna, note |
| Lower-reliability note | CB | `tackles_attempted_p90` | 0.714502 | 0.7 | — | dna, no note |
| Lower-reliability note | FB-WB | `box_entries_pass_p90` | 0.705739 | 0.7 | — | dna, no note |
| Lower-reliability note | FB-WB | `op_key_passes_p90` | 0.683752 | 0.7 | — | dna, note |
| Lower-reliability note | DM-CM | `dribbled_past_p90` | 0.690658 | 0.7 | — | dna, note |
| Lower-reliability note | DM-CM | `np_xg_p90` | 0.702235 | 0.7 | — | dna, no note |
| Lower-reliability note | AM-Winger | `long_pass_completion_pct` | 0.704960 | 0.7 | — | dna, no note |
| Lower-reliability note | AM-Winger | `np_shots_on_target_p90` | 0.710162 | 0.7 | — | dna, no note |
| Lower-reliability note | ST | `box_entries_pass_p90` | 0.719059 | 0.7 | — | dna, no note |

The AM-Winger `op_xg_assisted_p90` ρ row has left (r is now below 0.6, so the possession step is no longer reached).

**Odd/even vs multi-split: hypotheses, not findings (user, 2026-10-07; for the stats-reviewer).**
- Over 215 per-90 gated cells, z = (odd/even − mean) / SD has mean +0.27 and SD 1.05; 13 cells have |z| > 2 and 2 have |z| > 3 (ST `tackles_attempted` −3.29). The mean multi-split − odd/even shift is −0.008. On average the odd/even split was slightly optimistic.
- Candidate causes:
  - temporal balance: alternating dates spread both halves over the whole season, so form, injuries and coach changes are shared, while random halves can cluster in time;
  - home/away alternation, though on its own it would more likely lower r than raise it.
- No change is described as "improved reliability": both methods estimate the same quantity, and the new one has less split noise.
- **Minute buckets** (report only, no status change): `data/processed/reports/m4g2_reliability_cells.json`, summarised in `docs/reviews/m4g2-reliability.md`.

**Rebuild (percentiles, DNA, maps; 2026-10-07).**
- **Percentiles:** every pre-M4g2 cell is identical. The only differences are `registry_status` on the 8 cells whose status changed, and the version and sha stamps; `defensive_duel_success_pct` adds its own rows. Both reference checks pass (`m4d_percentiles_reference`, `m4f_dna_reference --check-output`).
- **DNA:**
  - AM-Winger, whose list is unchanged, is identical: vectors, dimension scores and neighbours. Only the `dribbled_past` direction column and the stamps differ.
  - CB's dimension scores are unchanged. Only the Aerial family weight moved, onto `aerials_won` alone.
  - FB-WB, DM-CM and ST change only where their lists changed.
- **Neighbours (`docs/reviews/m4f-weighting.md`, top-10 overlap with M4f):**
  - Kanté, Özil, Payet, Messi and Neymar 10/10; Busquets and Isco 9/10;
  - Stones 8/10 (Ramos and Bonucci out);
  - Morgan 7/10.
  - The with / without `np_shots_on_target` check now covers DM-CM and AM-Winger, the groups where it is DNA.
- **Compare re-checks (M4g1b pairs), before → after:**

  | Pair | Distance | Ranks (of 293) |
  |---|---|---|
  | Busquets–Kanté | 0.1430 → 0.1431 | #100 / #133 → #107 / #140 |
  | Carrick–Busquets | 0.1136 → 0.1134 | #46 / #44 → #47 / #42 |

- **Golden pages:**
  - Kane / Ben Arfa / Ighalo: ST Goal threat members go from 2 to 1. Scores: Kane 93 → 90, Ighalo 76 → 65.
  - Walcott–Özil: dribbled past becomes an uncoloured style axis without "(lower better)".
  - Both pages also show the new ratio row.
- **DM-CM role split, re-read on the new vector:** still no separate regions.
  - Lifts 0.30 (6) and 0.23 (8); d −1.28 / −1.11; overlap 0.50 / 0.56.
  - The M4g1 decision stands.

**Style-map axes (user decisions, 2026-10-07).**
- **Pins.** PCA signs are arbitrary, and the solver's sign flipped CB PC1 and ST PC1. Each axis is now signed so that a named pin metric loads positive, which keeps every M4g1 name on its screen side. A test checks that a solver sign flip does not move an axis.

  | Group | PC1 pin | PC2 pin |
  |---|---|---|
  | CB | progressive passes | aerials won |
  | FB-WB | open-play key passes | aerials won |
  | DM-CM | open-play key passes | np_xg |
  | AM-Winger | open-play key passes | np shots |
  | ST | aerials won | np shots |

- **New names** (left → right / bottom → top):
  - CB PC2 and FB-WB PC2: "Fewer ↔ More aerials won". The axes are almost entirely aerials won: +0.82 and +0.91, with nothing on the − end.
  - Their legend note: "Almost entirely aerial duels won per 90 (a count, not a win rate). Aerials lost left the DNA in M4g2 (lower reliability), so the Aerial family's weight now sits on this one metric."
  - ST PC2: "Safe passer ↔ Box striker" (shots +0.57, aerial win rate +0.39, aerials won +0.33, final-third passes +0.27 vs pass completion −0.37).
- **Unchanged names:** the other names keep their support.
- **ST PC1 legend note:** "Aerial target" mostly measures how often the striker contests aerial duels (won +0.30, lost +0.28). Aerial win rate is weaker (+0.20), so the note does not say "not heading ability". It is not headed shots or headed goals.
- **ST cards and legend:** they say "Dribbled past while pressing".
- **Quoted loadings:** every loading quoted in a note or verdict is checked on each run, including a "dominant" claim (squared loading ≥ 50%) behind "almost entirely".
- **Benzema (ST hover), before → after:**
  - PC1: −0.333 → −0.333, still "Technical forward" (strong).
  - Top pushes were shots on target / shots / aerials lost; now shots (−0.078) / aerials lost / long passes.
  - PC2: "Link-up focal point" (slight, +0.075) → "Box striker" (slight, +0.086). Top pushes were pass completion / shots / shots on target; now shots / pass completion / aerials won.
  - Shots now carries what was split between shots and shots on target.

**Minute buckets (report only; `docs/reviews/m4g2-reliability.md`).** The mean per-90 r_full rises with minutes in every group, from 900–1350 to ≥ 2000:

| Group | 900–1350 | ≥ 2000 |
|---|---|---|
| CB | 0.35 | 0.65 |
| FB-WB | 0.61 | 0.75 |
| DM-CM | 0.69 | 0.84 |
| AM-Winger | 0.66 | 0.85 |
| ST | 0.64 | 0.82 |

Buckets select on role and stability as well as minutes. No status change follows.

**Flagged for the stats-reviewer (user, 2026-10-07).**
- Single-metric axes (CB PC2, FB-WB PC2). With equal family weights, a family that drops to one metric puts its whole weight on that metric.
- ST Goal threat now has one member (`np_shots`); see the golden scores above.
- DM-CM `np_shots` ~ `np_shots_on_target` ρ 0.84, both in Goal threat.
- The odd/even optimism hypotheses (temporal balance vs home/away).

**Stats-reviewer outcome (`/finish-chunk`, 2026-10-07): APPROVE WITH CONDITIONS, output only.** It reproduced every figure above. Its conditions are recorded here.

- **Open decision for M4 sign-off: family weighting when a family has one member** (user, 2026-10-07; plan 005, before M4g6). The options are to accept it, cap a single-member family's weight, or merge families. Nothing changes until then. The evidence, from complete rows (one family = 1 / number of families, split equally among its members, §10.8):

  | Group | Single-member families after M4g2 | Largest single metric weight | Effective metrics (1 / Σw²), before → after |
  |---|---|---|---|
  | CB | Aerial (`aerials_won`) | 0.20 (was 0.10) | 14.0 → 10.9 |
  | FB-WB | Aerial (`aerials_won`), Goal threat (`np_shots`), Creation (`op_key_passes`, already single in M4f) | 0.143 each, 43% of the vector on 3 metrics | 14.3 → 11.1 |
  | ST | Goal threat (`np_shots`) | 0.167 (was 0.083) | 17.3 → 14.1 |
  | DM-CM | none | 0.071 | 18.4 → 19.7 |
  | AM-Winger | Creation (`op_key_passes`, unchanged since M4f) | 0.143 | 15.3 → 15.3 |

  - Each metric's share of the mean pair distance equals its weight (stats-reviewer, measured).
  - The neighbour lists now depend more on the weighting. The family-weight vs equal-weight top-10 overlap fell as follows (`docs/reviews/m4f-weighting.md` §1):

    | Group | Mean overlap | Rows with ≥ 7 of 10 shared |
    |---|---|---|
    | FB-WB | 0.680 → 0.604 | 58.6% → 37.3% |
    | ST | 0.783 → 0.706 | 88.6% → 65.8% |
    | CB | 0.772 → 0.723 | 90.2% → 74.5% |

  - The upweighted metric leads both map axes in CB (aerials won −0.47 on PC1, +0.82 on PC2) and in ST (np shots −0.40 on PC1, +0.57 on PC2), not only the single-metric PC2s. CB's PC1 + PC2 share rose from 44.8% to 49.8%.
- **ST Goal threat is now shot volume only: the same root cause, under the same open decision.**
  - With `np_shots` alone, the ST Goal threat score ranks shot volume. Example: Kane 90 (3.87 np shots, 0.448 np_xg per 90) vs Vardy 68 (3.02, 0.475), down from 93 and 75.
  - `np_shots` stays in DNA on the mean rule (ρ 0.340 / 0.372), while `np_xg` is blocked (0.436 / 0.442). The SE of a 4-league mean ρ is about 0.075, so the two are statistically indistinguishable in possession sensitivity.
  - `np_shots` is also possession-linked in La Liga (0.585) and the Premier League (0.523), with majority V 0.382.
  - No label change now (user, 2026-10-07).
- **Odd/even optimism, tested read-only by the stats-reviewer** (400 random splits, the engine's split functions). It settles the hypotheses above:
  - **The optimism is real.** The odd/even split hits every cell at once, so the cell z values are correlated. Allowing for that, the cross-cell mean z over random splits has SD 0.111, and odd/even's +0.25–0.27 has P ≈ 0.007.
  - **Temporal balance: confirmed.** A random split balanced in time (randomised within consecutive match pairs) gives mean z +0.175, and odd/even sits inside that distribution. A first-half vs second-half split gives −0.87, so within-season drift is substantial.
  - **Home/away: rejected.** A home vs away split gives z −0.07. Odd/even halves are less home/away-balanced than random ones (mean |Δ home share| 0.29 vs 0.16), which pushes its r down, not up.
  - **Consequence for the estimand:** random splits count within-season drift (form, injuries, coach changes) as error, but for a season aggregate that drift is part of the season's true value. So the multi-split r_full understates a season aggregate's reliability by about 0.004–0.008 on average. This is conservative.
  - The adopted method stands, since it was fixed before the run. A time-stratified random split is the principled alternative, if a later ADR wants one.
- **Other observations (recorded, no change):**
  - **Fragile entries:** the three cells that entered DNA (FB-WB `fouls_committed` 0.621, DM-CM `np_shots_on_target` 0.622, ST `dribbled_past` 0.622) sit 0.021–0.022 above 0.6, just outside the boundary margin, against a player-sampling SE of about 0.04–0.05.
  - **Near the gate:** 29 of 99 DNA metric × group cells fall below 0.6 in the 900–1350 minute bucket. Profiles near the gate are noisier than the pooled r suggests, which is a UI / M5 note.
  - **`defensive_duel_success_pct` produces no percentile anywhere** (min_attempts 93–562).
  - **Collinear-pair noise:** its 18 DM-CM pairs come from 3 rows. A minimum-n floor on the collinear-pair list would stop noise pairs from creating D17 conflicts on a new source.

### 13.7 M4g3 pre-registration: movers (user decisions 2026-10-08, before any mover change is computed)
Adds to §13.1; plan 005 M4g3 / M4g3c hold the full spec.
- **R_m is the matched-halves correlation**, as pre-registered: one Pearson over the matched halves pooled over all n × 200 draws (user, after the M4g3a pre-code review; it replaces "averaged over 200 draws per mover"). It is recomputed in each bootstrap resample from the resampled movers' draws.
  - **Why not the M4g2 multi-split r_full:** it is a full-season, Spearman-Brown value over random halves. It overstates the reliability of a stint-length half, which lowers f̂ and biases the rule towards PASS.
  - **Cross-check (report only):** r_full stepped down by Spearman-Brown per mover, √(r(m1) · r(m2)), averaged over the metric's movers after `min_attempts`. k is minutes ÷ the cell's median minutes for per-90s, attempts ÷ the cell's median attempts for ratios; the cell median is recomputed from the parquet. A gap |R_m − cross-check| > 0.10 gets one explanatory sentence and nothing else. Expected direction: R_m below the step-down (date-ordered halves).
- **"Inconclusive is not a pass" stands.** Three outcomes, from a two-sided 95% percentile paired-bootstrap CI of f̂ (user, after the M4g3a pre-code review):
  - PASS iff the CI upper bound (97.5th percentile) < 0.5;
  - FAIL iff the CI lower bound (2.5th percentile) > 0.5;
  - otherwise "inconclusive at n".
  - Before the real run, the spike simulates the effective rule's power (f = 0, 0.25, 0.5, 0.75; R_m 0.7; n = 24 and n = 15). The figures are recorded here before the results.
  - **If power is low, Defending and Passing may stay withheld for 2015/16. The rule is not relaxed after the results are seen.**
- **Low power (user, 2026-10-08):** computed per metric at its own n and its R_m, written out before f̂ is computed. P(PASS | f = 0) < 0.8 → the label "underpowered at n". It is a label only: it never changes PASS / FAIL / inconclusive.
- **Known beforehand:**
  - M4g3 changes no `registry_status`, note or `score_status`. Passing stays `pending_mover_stability` until M4g5.
  - The verdict is per metric (movers are pooled), the same in every group.
  - `defensive_duel_success_pct` is `untestable`: 0 of 24 movers meet `min_attempts` on both stints. Defending's testable directional members are `ball_recoveries_p90` and `tackles_won_p90`, as M4g4 adjusts them.
  - Also `untestable`: `aerial_win_pct` (4 movers), `np_xg_per_shot` (2). Tested: `pass_completion_pct` (24), `long_pass_completion_pct` (15).
- **Pre-stated outcomes (user, 2026-10-08, before the real run):**
  - **Passing reason text** (`pending_mover_stability`, shown on the compare page) stays unchanged until M4g5. After M4g3 it means "pending the xPass decision".
  - **ST Passing** rests on `pass_completion_pct` alone and is visible today:
    - FAIL → ST Passing is withheld in M4g5;
    - inconclusive → it stays visible, with a note;
    - the asymmetry is deliberate: a restore needs a PASS, a withholding needs a FAIL.
  - **A FAIL on any current DNA metric** changes nothing automatically. It is listed for the M4 sign-off decision, alongside family weighting.
  - **Defending restore** uses only the M4g4 re-run on the adjusted columns. The M4g3 verdicts on the unadjusted `ball_recoveries_p90` and `tackles_won_p90` are information only. The verdict is pooled, so its part in the per-group restore is the same in every group; `untestable` is not a pass (`defensive_duel_success_pct`).
  - **M4g4 note:** the restore rule's statuses naming the failed test (failed / inconclusive / underpowered / untestable) need new strings in `dna.WITHHELD_STATUSES` and tests. Nothing changes in M4g3.
- **Counts (2015/16, re-run):** 29 movers at 450 nominal minutes per stint; 24 in one group (AM-Winger 7, DM-CM 6, CB 5, ST 4, FB-WB 2), 10 of them cross-league; 5 in different groups. At 900: 6 movers, 5 in one group.
- **Registry (M4g3b):** the verdicts enter the measurements and the registry with no status effect. `spikes/expected_m4g3_flips.json` is written before any registry code, and the before/after STOP rule covers the verdicts.
- **Movers on the style maps (M4g3c, display only):** a combined dot per same-group mover, the nominal-minutes-weighted average of the stint map inputs.
  - It is never part of the PCA fit, neighbours, populations or ranks.
  - Its hover shows no percentile wording: an average of local percentiles is not a percentile.
  - It cannot be selected in compare mode.
  - A true season row from summed counts across stints is backlog and needs its own ADR.
- **Power of the effective rule (M4g3a, 2026-10-08, simulated before the real mover run).** `spikes/m4g3_power.py` → `data/interim/m4g3_power.json`; 2,000 simulated datasets per cell (MC SE ≤ 1.1 pp), B = 2,000, 200 draws per mover, seed 20261008.
  - **Rule as simulated:** the three-outcome rule and pooled R_m above. The same function (`mover_rule`) makes the real decision.
  - **Model:** value = player + team + noise in SD units; a mover's two teams are independent draws; homogeneous noise; one pool of 250 non-movers.
  - **P(PASS) under the effective rule** (point rule f̂ ≤ 0.5 in brackets):

    | n | R_m | f = 0 | f = 0.25 | f = 0.5 | f = 0.75 | f = 1 |
    |---|---|---|---|---|---|---|
    | 24 | 0.7 | 0.90 (1.00) | 0.37 (0.89) | 0.10 (0.55) | 0.03 (0.25) | 0.01 (0.10) |
    | 15 | 0.7 | 0.78 (0.99) | 0.34 (0.85) | 0.13 (0.56) | 0.05 (0.31) | 0.02 (0.17) |
    | 24 | 0.5 | 0.48 (0.93) | 0.23 (0.78) | 0.09 (0.56) | 0.05 (0.35) | 0.02 (0.20) |
    | 15 | 0.5 | 0.42 (0.89) | 0.26 (0.75) | 0.12 (0.54) | 0.06 (0.38) | 0.03 (0.26) |

  - **Low power at f = 0 on this grid:** every cell except n = 24 at R_m 0.7 is below 0.8. The real labels come from `power_at_f0` at each metric's own n and R_m.
  - **Re-run (2026-10-08, after the checkpoint review):** each cell is now seeded from its parameters, not its grid position. The largest change from the first run is 0.029 (n = 24, R_m 0.7, f = 0: 0.87 → 0.90, 3 combined SEs); the table above is the re-run.
  - **P(FAIL)** is low on the main grid: at f = 1 it is 0.23 (n = 24, R_m 0.7) and 0.08–0.15 in the other main cells (the R_m 0.85 sensitivity reaches 0.38). Most clearly team-driven metrics come out "inconclusive", not FAIL.
    - So "inconclusive" is not evidence of stability. This matters for ST Passing: its withholding needs a FAIL, which the simulation says is unlikely even for a fully team-driven metric.
  - **At the threshold (f = 0.5) the rule passes 9–13%**, against a nominal 2.5%. With 15–24 movers the percentile CI is too narrow on the upper side (Δ² is skewed). This is a property of the rule, accepted by the user before the real run (2026-10-08): no recalibration. The interval is not a calibrated 95% CI. BCa was considered and not adopted: it costs power at f = 0 and still passes about 6–10%.
  - **Sensitivity (report only):**
    - t(5) noise, R_m 0.7: f = 0 → 0.80 (n = 24), 0.74 (n = 15).
    - R_m 0.85: f = 0 → 1.00 (n = 24), 0.99 (n = 15); f = 0.25 → 0.55, 0.48.
    - The 24 real movers' minutes, each drawing from its own eligible pool (30–238 non-movers): within about 2 pp of the homogeneous figures (f = 0: 0.88 at R_m 0.7, 0.46 at 0.5); stints in date order.
  - **Validation of the simulator:** the point rule gives 0.54–0.56 at f = 0.5 (f̂ is a ratio of sums with a skewed numerator, so it is only approximately unbiased), and the figures agree with the stats-reviewer's independent re-simulation within 3 combined SEs. The M4g0 point-rule figures (0.6% / 8% / 16% / 21%) differ by up to 5 pp: their setup (R_m fixed or estimated, pool modelled or not) was not recorded.
  - **Expected reliability of the tested metrics** (r_full stepped down to the movers' stint minutes, stats-reviewer): `ball_recoveries_p90` 0.58, `tackles_won_p90` 0.49. At R_m ≈ 0.5, a purely individual metric passes about half the time.
  - **Limit:** the model treats the two teams as independent draws. Real moves are not random (many second stints are at struggling clubs), and the model cannot represent that.
