# Plan 005 — Milestone 4: Feature Engineering & Player DNA

**Status:** M4a in progress, branch `m4-player-dna`. The stats-reviewer reviewed the plan on 2026-10-02: approve with conditions (below).
**Inputs:** docs/brief.md §3–§7, §19–§20; docs/milestones.md M4 (all pre-conditions); ADR 002 §5, §6, §12, §14–§17, §22, §26–§27, §29, §31; ADR 003; docs/reviews/pre-m4-review.md.

**Decision mode for M4a (user, 2026-10-02).**
- Each D-item's recommendation is the default.
- After the measurements, only these items go back to the user:
  - (a) the data contradicts the recommendation;
  - (b) the stats-reviewer objects;
  - (c) it is a product choice.
- Everything else is recorded in ADR 004 as decided by default, with its numbers.

### Stats-reviewer conditions (2026-10-02): APPROVE WITH CONDITIONS
These override the D-items below wherever they conflict.
- **Objections (go to the user after the measurements):**
  - **D9 test.** The median-ratio band is replaced by: per metric × group (groups in `applies_to` only), `global_ok` requires every league's mean global percentile to be within 50 ± 10.
  - **D12 scope.** The measurement covers every volume metric, not only defensive ones:
    - leave-one-out team possession share (event-duration and pass-share forms);
    - trigger at |ρ| ≥ 0.4 with the same sign in all 4 leagues;
    - if confirmed, `team_style_biased` on passing volume (spot-check: CB `passes_attempted_p90` ρ 0.80).
    - PAdj, if adopted, cites the published StatsBomb form.
- **D1.**
  - Nominal-equivalent gate (it equals a 945 constant in 2015/16: 1,233 rows, −28).
  - ADR 004 states that the gate and the rate use different clocks, and why.
  - Sensitivity is reported as the P95 and max |percentile shift| and the top-decile Jaccard, not as Spearman.
- **D3.** A deterministic tie-break (`competition_id`). The non-selected row is scored against a population that excludes the player, and the ADR says so.
- **D5.**
  - Ambiguous when the margin is below 5% of minutes (13 gated rows).
  - Also store `position_group_share` (not a gate).
  - The second-group percentile is display-only and never enters DNA, similarity or rarity.
- **D7.** Pass bar: no cohort change and at most 1 rank step.
- **D10.**
  - The basis is fixed per metric by the registry version and named in every explanation.
  - M5 also evaluates pure-local.
  - Robust z declares its population and falls back to IQR/1.349 (or null with a reason) when MAD = 0.
- **D11.**
  - Zero-mass rule: a metric whose zero share in a group exceeds 30% leaves that group's DNA vector (see the product decision below).
  - M6 constraint: rarity defaults to the global group cohort, and a local rarity claim needs `MIN_RARITY_COHORT` (about 100). Counts are stated as "k of n".
- **D13.** Decide whether Injury Clearances also leave `passes_attempted`. A completion denominator that differs from it is stored as its own column.
- **D15.** Add `np_xg_per_shot`.
- **D17.**
  - For attempted/completed pairs, keep the volume and the rate.
  - `xg_assisted_direct` is display-only unless ρ < 0.85.
  - Run on the M4b output.
- **D19.**
  - Odd/even matches in date order (dates from `matches.json`).
  - Correlate within league × group, then average; apply Spearman-Brown.
  - A DNA count metric needs r_full ≥ 0.7 at the gate. Report r by minutes bins (450 / 900 / 1,800).
  - Ratios: beta-binomial reliability n / (n + M); `min_attempts` = the smallest n with reliability ≥ 0.6. Split-half is the cross-check.
- **D20.** The assertion becomes: every scored row meets the gate, and every gated row above P99 is traced to events in M4g.
- **D22.** The limit is max(3 × the worst league rate, 3 cases per competition-season). `events_outside_credited_time` is counted per player-match.
- **Added items:**
  - a provenance stamp in the percentile and DNA parquets (gate, `MIN_COHORT_SIZE`, registry version, input hash);
  - the M6 rarity cohort minimum (D11).
- **Process:** re-run D17 and D19 on the M4b output before the M4c registry review.

### Product decision (user, 2026-10-02): signature traits
Zero-heavy metrics (more than 30% zeros in a position group, D11) leave that group's DNA vector, but they are **not discarded**. They become **signature traits** for that group:
- **Eligibility:** a gated player holds a trait when both hold:
  - his absolute season count of the metric is ≥ a minimum count (`SIGNATURE_TRAIT_MIN_COUNT`, set per metric × group from the M4a measurement);
  - the metric passes the D19 split-half reliability check in that group.
- **Use:**
  - shown on the player page;
  - available to the M6 Gem Finder as a rarity signal, stated as "k of n" in the global group cohort (D11 M6 constraint).
- **Not used** in the DNA vector, similarity distance or dimension scores.
- **Example:** a CB with many box entries by carry (CB `box_entries_carry`: 73% zeros).
- **Measured in M4a**, per zero-heavy metric × group:
  - the zero share;
  - how many gated players clear candidate minimum counts of 5, 10 and 15;
  - the split-half reliability (D19 method).
- The registry (M4c) marks a metric × group as `signature_trait`. The thresholds are recorded in ADR 004.

## Context
Pipeline state (2026-10-02):
- 2,628 season rows at the player-team-competition-season grain, all `source = "statsbomb"`, season 2015/16.
- 2,279 pool rows (4 FULL leagues). The Bundesliga (349 rows) is PARTIAL.
- 1,261 rows pass `in_percentile_pool ∧ is_outfield ∧ meets_min_minutes`:
  - by group: AM-Winger 287, CB 260, DM-CM 298, FB-WB 254, ST 162;
  - local cohorts (league × group) hold 36–87 rows. The smallest are ST: PL 36, Ligue 1 37, La Liga 42, Serie A 47. In a cohort of 36, one rank step is about 2.8 percentile points.
- 99 pool GK rows pass the gate. GKs are out of V1 DNA (ADR 002 §12).

M4 turns these rows into:
1. percentiles per metric, league-local and global, within position groups;
2. a Player DNA vector grouped into dimensions;
3. a validation report that answers brief §20.

M5 (similarity) and M6 (Gem Finder) read the M4 output. They must not re-derive cohorts.

**The core design point:** one module decides who is in a cohort, and every percentile, similarity and rarity computation goes through it. Two roles are kept apart:
- **reference population:** the rows a percentile is computed *against*;
- **scored rows:** the rows that *receive* a percentile.

Most of the decisions below are about one of these two roles.

---

## 1. Open decisions

Every item is settled in **M4a**, which measures it (spike) and records it (ADR 004). The "Data" line says what settles each one. Items marked **[pipeline]** change a stored column, so they go through M4b.

### Gates and cohort membership

**D1. The minutes gate on the period-aware clock.** Since §31, 900 elapsed minutes are about 840 (PL) to 861 (La Liga) nominal minutes, so the gate is looser in the PL.
- Options:
  - (a) keep 900 elapsed;
  - (b) raise it to one league-average constant (e.g. 945);
  - (c) gate on nominal-equivalent minutes per player: `Σ_matches minutes_i × 90 / match_duration_i ≥ 900`.
- **Recommendation: (c) [pipeline].** It is exact per player, needs no league constant, and can be recomputed from stored columns. Per-90s stay on elapsed minutes, because a per-90 is a rate over real time.
- **Data:**
  - gated rows per league under (a), (b) and (c), and which rows move;
  - rank stability under thresholds of 600, 750, 900 and 1,200: Spearman ρ of local percentiles for rows that pass every threshold (brief §20, "how sensitive are rankings");
  - split-half reliability of each p90 (odd vs even matches) as a function of minutes. This shows where per-90s stop being noise.

**D2. Within-competition split seasons** (the M4 open question; ADR 002 §17).
- Options:
  - (a) per-stint gate (current);
  - (b) gate on `player_season_minutes_total`;
  - (c) per-stint, but a stint below the gate is shown with a "below gate" status.
- **Recommendation: (a) + (c).**
  - The stint stays the unit: a player can have different roles at two clubs, and CLAUDE.md forbids merging profiles.
  - (b) would let a 200-minute stint inherit eligibility from the other stint.
- **Data:** the number of players with `player_season_minutes_total` ≥ gate but no stint ≥ gate, listed by name.

**D3. Cross-competition movers: the combined gate and double counting** (ADR 002 §29, ADR 003 §7).
- Options:
  - (a) no combined gate; dedupe global cohorts to one row per `(player_id, season_id, source)`;
  - (b) a combined gate over pool rows only;
  - (c) no dedupe.
- **Recommendation: (a).**
  - Each league row is gated on its own. PARTIAL minutes never count toward anything (already decided).
  - In a **global** reference population, a player with two gated rows (Khazri, Doucouré, Koné) contributes only his row with the most minutes. Both rows are still **scored**.
  - Local populations need no dedupe, because a row belongs to exactly one league.
  - The 5 combined-only players (Lemina, El Kaoutari, Mario Suárez, Bangoura, Zárate) get a "below gate" status.
- **Data:** the 3 double-counted and 5 combined-only players, with minutes; the percentile change when the dedupe is applied.

**D4. Who receives a percentile.**
- Options:
  - (a) only gated pool rows; every other row gets null plus a `percentile_status` reason (`below_gate`, `not_in_pool`, `goalkeeper`, `ambiguous_group`, …);
  - (b) also score sub-gate and Bundesliga rows *against* the pool, flagged.
- **Recommendation: (a) for V1.**
  - It is the CLAUDE.md rule ("low-sample players must not appear as elite profiles") with no exceptions to explain.
  - (b) can be added later without changing the reference populations.
- **Data:** none needed. This is a product rule.

### Position groups

**D5. Near-tie season groups.** Season `position_group` is an argmax of group minutes. In H4, 4 gated rows changed cohort and moved by up to about 84 points. 5 of 1,360 gated rows have a top-two margin under 10 min.
- Options:
  - (a) argmax as is;
  - (b) a flag, and exclusion from cohorts;
  - (c) a flag; the row is a member of its argmax cohort only, and is also *scored* against the second group;
  - (d) a share rule: a top group with less than X% of minutes is "hybrid".
- **Recommendation: (c) [pipeline: margin and flag columns].**
  - Add `position_group_margin_pct` = (top1 − top2) / minutes_played, and `position_group_ambiguous` when it is below a config threshold.
  - Exclusion (b) would drop real hybrid players, which is the opposite of what a scout wants.
- **Data:**
  - the distribution of the margin on gated rows;
  - row counts at 2%, 5% and 10%;
  - for flagged rows, the gap between their percentiles in the two cohorts.

**D6. GK vs outfield mislabels** (Chaigneau 3829440).
- Options:
  - (a) argmax (current);
  - (b) any GK minutes ⇒ GK unless outfield minutes win by a margin;
  - (c) report every season row with both `minutes_gk` ≥ 45 and outfield minutes ≥ 45; the cohort build stops on an unexplained one.
- **Recommendation: (c).**
  - A rule tuned to one case can mislabel the next one.
  - A loud check with a per-case explanation fits the project's STOP pattern.
- **Data:** every season row with mixed GK and outfield minutes, with its events' positions. 2015/16 is expected to have very few after the §31.4 tie rule.

**D7. Positions from Tactical Shift events** (ADR 002 §31.6, patterns B and C).
- Options:
  - (a) keep the lineup stints and document the 9 exceptions;
  - (b) derive positions from `tactics.lineup` (a definition change for every row, with a full before/after);
  - (c) a reviewed override list.
- **Recommendation: (a) for V1**, unless the data below shows a gated season row or percentile moving.
  - The 4 wrong match labels are a few hundred minutes in season rows of 1,000+.
- **Data:** recompute the season `position_group` and the gated percentiles of Henderson, Coutinho, Polomat, Ketkeophomphone and the other pattern-B/C players with event-implied minutes. If nothing crosses a cohort and no percentile moves by 2 points or more, close as (a).

**D8. A Player On that the lineup does not follow** (Milinković-Savić 3879637, 3.5 min).
- Options:
  - (a) lineup only (current);
  - (b) a Player On resumes time.
- **Recommendation: (a).**
  - There is one case in 1,551 matches.
  - (b) adds a second source of truth for stint time, and every §31 check would need to cover it.
  - Revisit it with a new source.
- **Data:** the count of such Player On events per league (the 2015/16 audit expects 1).

### League comparison

**D9. League-local vs global percentiles: event rates and match length.**
- Known facts:
  - interceptions per 1,000 events: La Liga 9.55 vs 5.73–6.79 elsewhere;
  - 50/50s: 0.31–0.43 (La Liga, PL) vs 1.39–1.57 elsewhere;
  - full-match elapsed minutes: 94.1 (La Liga) to 96.4 (PL). More stoppage means more dead time per elapsed minute, so per-90s are lower in the PL for the same activity.
- Options:
  - (a) local and global for every metric;
  - (b) global only for metrics that pass a league-consistency test; others are local-only, and their global column is null with a reason;
  - (c) per-league standardisation. This is a league adjustment model, out of scope (CLAUDE.md, brief §6).
- **Recommendation: (b).** The registry gets `global_ok`, set from a stated test.
  - Proposed test: within each position group, the ratio of league medians stays inside a band (e.g. 0.8–1.25) for all four leagues, and the per-1,000-event rate shows no league outlier.
  - The band is a proposal for the stats-reviewer.
- **Data:** per metric × group, the league medians and their ratio; event rates per league; events per elapsed minute per league.

**D10. The basis of the DNA vector** (needed by M5/M6, decided now so M4f is not blocked).
- Options:
  - (a) global group percentiles;
  - (b) league-local percentiles: style relative to the player's own league, treating leagues as equal;
  - (c) within-group robust z-scores (median/MAD), which keep magnitude;
  - (d) a mix: global where `global_ok`, local otherwise.
- **Recommendation: (d), with (c) also stored for M5 to test.**
  - Percentiles are bounded and robust to outliers, and they explain well ("P92 in progressive passes").
  - z-scores keep the distance information that similarity may need.
  - M5 picks one with evidence; M4 only stores both.
- **Data:** the M4g neighbour sanity checks run on both bases.

**D11. Percentile method** (not listed yet, but needed).
- Options for the formula: rank / n; (rank − 0.5) / n (Hazen) with average ranks for ties; a kernel-smoothed CDF.
- Options for small cohorts: a minimum cohort size, or none.
- **Recommendation:**
  - Hazen with average ties, which is symmetric and never 0 or 100;
  - `MIN_COHORT_SIZE` in config (proposed 30); a smaller cohort gives null plus a reason;
  - each percentile is stored next to its cohort size `n`, so the UI can show the resolution.
- **Data:** the cohort sizes above. A future smaller league would hit the minimum first.

**D12. Possession adjustment.** Defensive volume (tackles, interceptions, pressures, recoveries) rises when a team has less of the ball.
- Options:
  - (a) none; flag `team_style_biased` (already required by the chunk A review);
  - (b) the published possession-adjusted (PAdj) form for defensive metrics, stored as separate columns;
  - (c) per opponent touch.
- **Recommendation: measure, then (a) or (b) [pipeline if (b)].**
  - Adopt (b) only if, within a group, team possession share explains a large part of a defensive per-90 (|Spearman ρ| ≥ 0.4, threshold for the stats-reviewer).
  - If adopted, the raw columns stay. The PAdj columns are added, not substituted.
- **Data:** team possession share per match from events (`possession_team` and event durations), aggregated to the player's minutes; its correlation with each defensive p90 per group.

### Metric definitions [pipeline]

**D13. Injury Clearance passes** (2,459 passes; currently counted as incomplete).
- Options:
  - (a) keep;
  - (b) exclude from the completion denominators, like Unknown (§14);
  - (c) count as complete.
- **Recommendation: (b)**, plus a display-only count column `passes_injury_clearance`. A kick out of play for an injury is not a pass attempt.
- **Data:** the count and the `pass_completion_pct` / `long_pass_completion_pct` shift per position group (expected largest for CBs).

**D14. Throw-ins in entries** (about 4% of box-entry totals before exclusions).
- Options:
  - (a) keep;
  - (b) add "Throw-in" to `SET_PIECE_TYPES` for entries;
  - (c) count them separately.
- **Recommendation: (b).**
  - A throw-in into the box says little about passing skill and inflates FB-WB.
  - Also check whether `progressive_passes` counts throw-ins, so that the rule is consistent.
- **Data:** throw-in entries per group, and their share of each entry column.

**D15. Penalties in shots and xG** (pre-M4 review #16).
- Options: (a) keep; (b) add `np_xg`, `np_shots` and use them in DNA.
- **Recommendation: (b).** Penalty-taker duty is not playing style, and one penalty is about 0.76 xG.
- **Data:** penalty xG share per group, and the top-10 rows by `xg_p90` before and after.

**D16. xG-assisted fallbacks** (ADR 002 §26).
- Validate the 6 "no receipt" shots in La Liga (genuine first-touch shots, or a data gap?) and count them in all leagues.
- **Recommendation:** flip the "missing shooter" fallback to `xg_assisted` only (not direct), which is the honest direction (0 cases in 2015/16, so no numbers move), and add counters for skipped dangling ids.
- **Data:** the event-level listing of these shots.

### Feature selection

**D17. Collinearity and overlap.**
- Known candidates:
  - `xg_assisted` vs `xg_assisted_direct` (84% direct);
  - `box_entries_pass` vs `progressive_passes`;
  - `passes_attempted` vs `passes_completed`;
  - `shots` vs `xg`;
  - `tackles_attempted` vs `tackles_won`;
  - `carries` vs `progressive_carries`.
- Options: keep all; drop one of each pair with |ρ| ≥ threshold; PCA; equal weight per dimension.
- **Recommendation:**
  - Spearman matrices per position group on gated rows;
  - for |ρ| ≥ 0.85, keep the more interpretable metric in DNA and the other as display-only;
  - weight dimensions equally, so a dimension with more metrics does not dominate.
  - No PCA: its components cannot be explained (brief §19).
- **Data:** the correlation matrices; the box-entry ⊂ progressive overlap fraction per group.

**D18. Position-biased and volume-biased metrics** (§22, chunk A review).
- `dispossessed_p90`, `miscontrols_p90` and `switches_p90` are used within groups only, flagged in the registry.
- Touch normalisation (§22) needs a touches column that does not exist. **Recommendation:** defer it, keep these metrics display-only for DNA in V1, and record that in ADR 004.
- **Data:** none needed for the deferral.

**D19. Minimum attempts for ratio metrics** (`*_pct`, `xg_per_shot`; `MIN_AERIAL_CONTESTS = 10` is admittedly loose).
- Options:
  - (a) a fixed minimum per ratio;
  - (b) a minimum from split-half reliability;
  - (c) empirical-Bayes (beta-binomial) shrinkage toward the group mean.
- **Recommendation: (b) for V1, stored as `min_attempts` per ratio in the registry.**
  - Below the minimum, the ratio's percentile is null with a reason.
  - (c) is better statistics but needs explaining in the UI. Record it as the V2 option.
- **Data:** split-half reliability of each ratio as a function of attempts. Pick the attempts at which reliability reaches about 0.6–0.7; the target value is for the stats-reviewer.

**D20. Sub-threshold extremes.** Verify that no row with an extreme `xg_assisted_p90` (or any p90 above the group's P99) survives the gate.
- **Recommendation:** an integration assertion in M4d, plus an outlier audit in M4g.
- **Data:** the p90 distributions on both sides of the gate.

### GK and data-quality hygiene

**D21. GK columns.** `box_entries_carry_p90` and `final_third_entries_carry_p90` (and the other outfield DNA columns) are non-null for GKs.
- **Recommendation:** no nulling of stored data.
  - The registry declares `applies_to` per metric.
  - The cohort module refuses GK rows in V1.
  - The schema doc gets a note.
- **Data:** none needed.

**D22. Thresholds for report-only checks before any new source** [pipeline].
- Count thresholds: for each report-only issue, fail the build when its rate per 100 matches exceeds max(k × the worst 2015/16 league rate, a small floor), with k = 3 proposed. Config, one constant per issue.
- `stint_after_period_end` size limit: fail when the clamped amount exceeds `STINT_LATE_START_FAIL_MINUTES` (proposed 2.0; Bale and Dembélé are both under 1).
- The spike's fail-loud rules (exactly two Half End events per period; reversed treatment window): **defer** to the new-source checklist, as milestones.md allows, because M4 adds no source.
- **Data:** the 2015/16 per-league rates of each issue (ADR 002 §31.7 already has the counts).

---

## 2. Chunks
Branch `m4-player-dna`. Each chunk ends with `/finish-chunk`. I inserted M4b (pipeline amendments): D1, D5, D13–D16 and D22 change stored columns, and they must land, with new references, before any percentile is computed.

### M4a — Measurements and decisions → ADR 004 (docs + spikes only)
1. **The stats-reviewer reviews this plan.** Done 2026-10-02 (conditions at the top). The spike must also produce:
   - raw-event reads for D12 (duration possession), D13, D14, D15 and D16;
   - the `matches.json` dates for D19;
   - the D9 mean-global-percentile table;
   - the zero-share table per metric × group;
   - the leave-one-out possession ρ for every volume metric;
   - the signature-trait table: per zero-heavy metric × group, the players with a count ≥ 5 / 10 / 15, and the split-half reliability.
2. `spikes/m4_decision_measurements.py` produces every "Data" line in §1, one section per D-number:
   - it reads the parquets and raw events; it writes no reference files;
   - its output goes into ADR 004 as tables.
3. The user decides each D-item. ADR 004 "Cohorts, percentiles and Player DNA" records each decision with its numbers. ADR 002 §17 and §29 and milestones.md get pointers, and the M4 pre-conditions are closed or deferred one by one.
4. Commit: docs/ and spikes/ only, so it can go direct after approval (CLAUDE.md exception), or through `/finish-chunk` if preferred.

### M4b — Pipeline amendments (ADR 004)
The M4b plan is in the M4b section at the end of this file.

### M4c — Metric registry
`src/eleveniq/analytics/registry.py`: a typed, frozen table, one entry per metric:
- `column`, `dimension`;
- `direction`: `higher_better` / `lower_better` / `style`. A style metric such as long passes or fouls is shown as a percentile but never coloured as good or bad;
- `applies_to` (groups), `dna` (bool), `display_only`;
- `global_ok`, `position_biased`, `team_style_biased`;
- for ratios: `numerator`, `denominator`, `min_attempts`.

Code enforces the rules; an ADR warning is not enough:
- Goals, assists and cards can never be `dna`.
- Biased metrics need an explicit allow-list to enter DNA.
- Every column exists in the season schema, and every `dna` metric has a dimension.

Dimensions (brief §3): Passing, Progression, Creation, Carrying, Defending, Goal threat, Ball security. Each one lists its metrics per group with the reason. Proposed 15–30 DNA metrics in total; the exact list goes in ADR 004, and the stats-reviewer reviews it **before** the code.

### M4d — Cohorts and percentiles
- `analytics/cohorts.py` is the **only** place that applies the filters:
  - pool, outfield and gate;
  - group, league, season;
  - a single source; a mixed-source cohort raises;
  - the D3 global dedupe;
  - D4 statuses.
- `analytics/percentiles.py`: pure functions (D11 method, registry direction, D19 minimums).
- `pipeline/build_percentiles.py` writes `data/processed/player_season_percentiles.parquet`, keyed by the season-row key:
  - `<metric>_pct_local`, `<metric>_pct_global`;
  - `cohort_n_local`, `cohort_n_global`;
  - `percentile_status`.
- An independent spike recomputes the percentiles from the parquet (no eleveniq imports) and writes reference values for an integration test.
- Integration assertions:
  - every gated row is scored;
  - cohort sizes match;
  - no rows from two sources share a cohort;
  - D20 holds.
- qa-tester writes spec tests from ADR 004, covering ties, small cohorts, the dedupe, GK refusal and null statuses.

**M4d plan (approved 2026-10-05; definitions in ADR 004 §11).**
- **User decisions:** D9-failing and D9-undefined cells get a null global percentile; D3 outsiders keep the spike formula (exact D9 match; 0/100 possible only for rows outside their population, including D5 second-group rows); the D5 second-group percentile is built in M4d (`cohort_role = "second"`); long output format; direction not applied; M4f's one-basis question stays open.
- **Determinism (user):** sorted by (key, `cohort_role`, `metric`), stable floats, fixed write settings; an integration test builds twice and compares the file sha256.
- **M4d1 (spike) done** (commit 427f30a): `spikes/m4d_percentiles_reference.py` and `tests/fixtures/m4d_percentiles/reference.json` (15 hand-picked output rows). Self-check: the D9 means of all 184 `global_ok` cells reproduce the measurements; 53 `league_inconsistent`, 3 `cohort_too_small` and 263 population sizes agree.
- **Stats-reviewer (2026-10-06): approve with conditions.** User: (1) D9 undefined when a pool league population is below 30, in the registry and the spike (ADR 004 §11.3: no status or DNA change, 7 display cells change D9, `REGISTRY_VERSION` changes; self-check now 181 / 51 / 3); (2)–(3) to M4e below; (4) to the M4d2 tests.
- **M4d2 (code):** `config.MIN_COHORT_SIZE`, `config.MIXED_POSITION_MINUTES`; `analytics/cohorts.py`, `analytics/percentiles.py`, `pipeline/build_percentiles.py`; fast tests (hand-computed ties, gate, min_attempts exclusion, 29/30, (0, 100) for members, D3 tie-break and order, guards, statuses), integration tests (fixture, `--check-output`, D20, n, one source, two-build sha256), two mutation checks (gate, tie rule); stats-reviewer on the run; `/finish-chunk`.
  - **Added (stats-reviewer condition 4, user 2026-10-06):** no member of its population is at 0 or 100; no outsider or second-group row shares a `player_id` with a member of its population, except a D3 mover's own other stint; the Ighalo-removal invariance (a row below `min_attempts` removed → no other percentile changes) runs against `cohorts.py`, not only the spike.
  - **Registry version** in the output equals `REGISTRY_VERSION` (the spike checks only its shape; code-reviewer M4d1).
- **M4d2 progress (2026-10-06):** code, fast tests (`tests/test_cohorts.py`, `tests/test_percentiles.py`) and integration tests (`tests/test_build_percentiles.py`) written; the D9 small-league change (§11.3) and the tiered `/finish-chunk` (CLAUDE.md) are folded into this chunk (user: no separate commit). Run figures in ADR 004 §11.4: the spike check passes on every cell, two builds are byte-identical, both mutation checks go red.

### M4e — A comparison page (visual sanity check)
- A generator script writes **one self-contained static HTML file** from the two parquets.
  - It has two player pickers, a radar of the DNA metrics' percentiles (local/global toggle), and a table: p90, percentile, cohort n, status and flags.
  - It has no server and no new runtime dependency.
  - It lives in `spikes/` (or `tools/`) because it is a check, not the M9 frontend.
- Streamlit or FastAPI would add a dependency for a throwaway page, and M7/M9 own those decisions.
- **Display rule (user, 2026-10-05; ADR 004 §11.1):** a stored percentile of exactly 100 is shown as ">99" and exactly 0 as "<1" (only rows outside their population: D3 outsiders and D5 second-group rows).
- **Closed as moot (user, 2026-10-06): display when one scope is `cohort_too_small` and the other has a value.** Under ADR 004 §11.3 the case cannot occur: a cell's global population contains every locally eligible player, so it is never smaller than a local one; a local population below 30 makes D9 undefined, so the global percentile is null too. No M4e display rule is needed; reopen only if §11.3 changes.
  - Measured on the first M4d1 spike output: 183 cells, all with the registry basis holding the value and the other scope null. Example: Kane `long_pass_completion_pct`, basis global, local null (n 12), global 63.28 (n 64). The cells were ST `long_pass_completion_pct` (64 primary + 2 second-group rows) and `np_xg_per_shot` FB-WB (43) and DM-CM (73 + 1), all display-only.
  - After §11.3 those 183 cells have both scopes null (0 cells of either variant).
- **Label selected populations** (stats-reviewer condition 2, user 2026-10-06): a ratio percentile ranks only the rows meeting `min_attempts` (e.g. ST `long_pass_completion_pct` 64 of 157 STs, 40.5%; `np_xg_per_shot` similar). M4e shows n and the condition ("among STs with ≥ 43 long passes").
- **Zero-heavy cells are not ranks** (stats-reviewer condition 3, user 2026-10-06): every zero gets 0.5 × the zero share (CB `box_entries_carry_p90` 36.4 for every zero; CB `restart_key_passes` ≈ 43.8 for every non-taker). M4e must not present such a percentile as a rank; signature traits are shown as holder / non-holder (§1), not as a percentile.
- **From the M4d2 stats review (user, 2026-10-06):**
  - **`long_pass_completion_pct` reads as team style:** a low value must not be worded as poor individual passing. Example: Koné 0.319 at Sunderland, the CB minimum, where Yedlin was 31.5% and Kaboul 43.3%; he was 0.595 at Lorient.
  - **Empty global percentiles show their reason, not a blank:** 3,554 `d9_undefined` and 12,591 `league_inconsistent` global cells in 2015/16. This is the method as designed.
- Used straight away on known 2015/16 pairs for a face-validity pass before the DNA vector, e.g. Kanté vs Busquets, Özil vs Payet, Messi vs Neymar, two CBs from opposite styles.

- **M4e plan (approved 2026-10-06):** `tools/compare_page.py`, CLI-picked 2–3 players for one season, tables per family (DNA first, then display; signature traits separate; second-group section), basis and n per value; rules in ADR 004 §11.5. User decisions: identity from the season parquet with a sha guard; neutral restart key pass label (throw-in check to the backlog); no radar until M4f; `tools/` counts as logic for qa-tester in `/finish-chunk`; mixed-group labels; fixed N reference metric `passes_attempted_p90`; `--update-golden` prints a diff summary and a golden change is called out in the commit report.
- **M4e progress (2026-10-06):** code, fast tests (`tests/test_compare_page.py`) and integration tests (`tests/test_compare_page_integration.py`: golden Kane / Ben Arfa / Ighalo, trace on five pages, reference-metric N) written. Mutation checks (">99" rule; blank reason) go red. Face-validity pages generated (Kanté vs Busquets, Özil vs Payet, Messi vs Neymar, Stones vs Morgan — CBs chosen by the user as a known ball-playing vs clearance-heavy contrast, not from the data); reviewed by the user (below).
- **M4e face-validity observations (user review, 2026-10-06):**
  - **Verifiable counts match the page:** Özil 19 assists / 6 goals, Payet 9 goals, Messi 26 goals, Neymar 24 goals. Messi has 15 assists in raw events, in the pipeline and on the page (official count 16): his passed penalty to Suárez is coded as a shot (ADR 004 §11.5); no change.
  - **Messi vs Neymar:** reads right (user).
  - **Kanté vs Busquets:** Busquets (Barcelona) shows interceptions 1.14 p90, local 11th (n 67), and ball recoveries 4.25 p90, global 15th / local 17th, coloured higher-better, which reads as a weak defender. Kanté is 99th / 98th. **Mainly a Barcelona team effect, not possession** (stats-reviewer, M4e `/finish-chunk`): Barcelona make 9.1 interceptions per match against 13–18 for every other La Liga team, and PSG, at similar possession, sit near the middle (team-mean local interceptions percentile 49.2 vs Barcelona 6.9). A possession adjustment calibrated to remove the possession correlation moves Busquets only 11 → 25 (interceptions) and 17 → 22 (recoveries). See the M4f/M4g item below.
  - **Stones vs Morgan:** Morgan's clearances are 5.99 p90, local 30th (n 59 PL CBs; global `league_inconsistent`). Morgan vs Huth (both Leicester) checks whether that is team-level: Huth 8.30 p90, local 94th. Same team, opposite ends, so it is a split within the pair, not a team effect.
  - **Display fixes from the review:** the D9 status texts drop the leading "global" (the scope label says it); the long pass completion label is "team-influenced"; golden regenerated (0 data-src cells changed each time). From the `/finish-chunk` reviews: a signature trait in a D5 second-group section shows "not assessed (second group, display only)" instead of a percentile; every `team_style_biased` cell (allow-listed DNA and possession-trigger display) is uncoloured; all three restart metrics carry the neutral "Restart … (corners, free kicks, throw-ins)" label.
  - **Deferred:** `player_nickname` (backlog).


### M4f — Player DNA vector
- **Decisions (user, 2026-10-06; ADR 004 §12):**
  - local basis for every vector;
  - the §10.8 missing-ratio proposal (32 partial rows, 0 incomplete in 2015/16);
  - `interceptions` → `style`;
  - weighting checks are report only.
  - Found and fixed in M4f1: `REGISTRY_VERSION` now hashes every `MetricSpec` field (d21458f9… → aaecf86f…).
  - **Method decisions after the M4f1 stats review (user, 2026-10-06; ADR 004 §12.6):**
    - the Defending dimension score is withheld (`pending_possession_adjustment`) until M4g;
    - pair weights are re-split over the shared metrics (symmetric, tested);
    - a per-row Passing score with no directional member is null (`no_directional_metric`, 30 rows);
    - z is stored uncapped, and M5 decides its use and cap;
    - every dimension score names its members.
- **Chunks:**
  - **M4f1** registry, config and ADR (this outcome);
  - **M4f2** `analytics/dna.py` + `pipeline/build_player_dna.py` + `spikes/m4f_dna_reference.py`, after the stats-reviewer reviews the §12.6 method;
    - spike first (ADR 004 §12.7): `--check-output`, hand-picked fixture, the output schema (two long files); checkpoint commit;
    - then the code: two builds byte-identical, 32 `partial` / 0 `dna_incomplete`, row weights sum to 1, d(A, B) = d(B, A), mutation checks shown red;
    - done 2026-10-06 (ADR 004 §12.8): 1,201 complete / 32 partial / 0 `dna_incomplete`, byte-identical builds, spike check passes, 4 mutations red;
    - a synthetic `dna_incomplete` test (2015/16 has none): the row keeps its vector, weights and dimension scores, and the distance refuses it (code-reviewer, spike checkpoint);
  - **M4f3** `spikes/m4f_weighting_checks.py` → `docs/reviews/m4f-weighting.md`, report only, incl. the top-10 neighbours (name, group, team, league) of Kanté, Busquets, Özil, Payet, Messi, Neymar, Stones and Morgan (user; M4g owns validation), plus Isco (user, 2026-10-06: the DM-CM |z| 13.7 outlier, ADR 004 §12.7); the main-group minute share next to every row with |z| > 5 (stats-reviewer C1, ADR 004 §12.8);
    - done 2026-10-07 (`docs/reviews/m4f-weighting.md`, report only, no rule or weight changes):
      - family vs equal metric weights, top-10 overlap 0.63 (AM-Winger) to 0.78 (ST); pure noise in the same family structure gives 0.57–0.73, and 8.5–9.7 of each top 10 fall inside the other weighting's top 20 (the 10th → 11th neighbour gap is a median 1.4–1.9%);
      - without `np_shots_on_target`: overlap 0.82 (AM-Winger) and 0.86 (ST) under family weights;
      - family shares of the distance: all-pairs shares sit near the nominal 1 / |F|; neighbour-pair shares match the pure-noise baseline (Defending ≈ 0.25 is the selection effect of a many-metric family, not a finding; stats-reviewer, M4f3 review);
      - partial rows as neighbours, obs / exp: FB-WB 0.59, AM-Winger 1.11 (DM-CM and ST, 2 rows each, are noise); with the missing metric dropped for every row, FB-WB 0.44 and AM-Winger 0.97, so the deviation is where these players sit, not the missing-data rule;
      - distances: a random pair ≈ 0.155 (39 pts RMS), the 10th neighbour ≈ 0.064–0.072 (25–27 pts), ≈ 0.65× a random pair in RMS terms;
      - |z| > 5: 95 cells in 64 rows, 34.4% with a main-group share below 0.70 (C1 reproduced);
      - **face validity (user, 2026-10-07):** Busquets, Özil, Stones and Neymar lists are near-ideal; Morgan and Stones lists do not overlap (style separated correctly); Isco's list is all 8s (evidence for the DM-CM role split); Kanté's list is ball-winning box-to-box mids; no list contains a clearly wrong-style player;
      - **spot-check, Piqué `progressive_carries_p90` 16.6** (La Liga CB median 5.06, z 5.70): an independent recount from raw events gives 1,652 carries and 492 progressive, equal to the pipeline; Barcelona's other CBs are close (Mascherano 15.0, Bartra 16.3), so it is team style, not a data error;
  - **M4f4** radar (local basis) on the comparison page. Every dimension score shows its member count ("based on 1 metric"), on the radar and in the M4e tables (user, 2026-10-06; ADR 004 §12.8).
    - code and tests 2026-10-07 (ADR 004 §12.9): inline-SVG radar on `player_dna.parquet`, drawn only for a shared group; a missing metric is a gap with a rim cross; style axes uncoloured; dimension scores with member counts, Defending "withheld until M4g"; second golden Walcott vs Özil; 3 mutations red (1 equivalent noted); review pages Kanté vs Busquets, Messi vs Neymar;
    - user decision 2026-10-07: the Passing dimension score is withheld (`pending_mover_stability`) in CB, FB-WB, DM-CM and AM-Winger, where it rests on `long_pass_completion_pct` alone; ST keeps it (ADR 004 §12.9).
- **Per-family contribution report** (stats-reviewer, user 2026-10-04): for each group, each family's share of the distance between neighbours, next to the equal-family-weight sensitivity check (ADR 004 §10.8, §10.11).
- **Sensitivity check with and without `np_shots_on_target`** (stats-reviewer M4c2b review, user 2026-10-04): it correlates 0.79–0.80 with `np_shots` in AM-Winger and ST and halves its weight inside Goal threat. Run the equal vs family weight check both ways and report the top-10 neighbour overlap for AM-Winger and ST.
- **Exclude second-group rows** (M4d2 stats review, user 2026-10-06): M4f and M6 read only `cohort_role = "primary"` rows. D5 second-group percentiles are skewed as expected (9.0% of local values ≥ 95; the 0/100 cells cluster in AM-Winger xG-assisted metrics) and are display-only.
- **Pre-registration (M4f/M4g, user 2026-10-06): defensive volumes and opponent possession.** Analysis by the stats-reviewer in the M4e `/finish-chunk` (2015/16, pool leagues; opponent open-play passes while on, proxied from team match passes × minutes share, checked against the exact `team_open_play_passes_while_on`: median ratio 0.999).
  - **Findings.** Opponent open-play passes per match range from 299 (Barcelona) to 534–543 (Caen, Carpi), median 440. Team per-match defensive counts barely move with possession (Barcelona's 45.7 recoveries per match sit inside La Liga's 43.9–50.9). The elasticity of the per-90 to opponent passes is well below 1 in most cells: DM-CM interceptions 0.52, recoveries 0.11, tackles 0.09, pressures 0.35; CB recoveries −0.68; AM-Winger interceptions ≈ 1.0. D12's pooled ρ does hide a tail (DM-CM interceptions: top-5%-possession band mean local percentile 33.6, against ≈ 44 implied by ρ −0.13), but that band is 15 rows from 4 teams, and without Barcelona it is 43.4. The clearest tail is `pressures` for high-possession attackers (≈ 25th percentile; `style`, so uncoloured).
  - **Rejected: the user's first proposal, a rate per 1,000 opponent passes.** It assumes an elasticity of 1 and over-corrects: within-league ρ(possession, adjusted) becomes +0.40 for DM-CM recoveries and +0.57 for CB recoveries (both would trigger D12 the other way); top-possession DM-CMs move from a mean local percentile of 48.9 to 69.0 on recoveries (Busquets 17 → 83, Iniesta 44 → 95; Kanté 99 → 88).
  - **To pre-register before M4g sees results (not implemented):**
    - exposure: opponent open-play passes while on, exact from the ADR 002 §31 timeline (the D12b machinery mirrored onto the opponent), stored as numerator and denominator;
    - partial adjustment `adj = p90 × (E_ref / E_player)^β`, β per metric × group from a league-demeaned team-level log-log regression with a team-bootstrap CI. The method and the candidates β ∈ {0, 1, β̂} are pre-registered, not a value; β̂ is fitted on one season and tested on another (with one season only, the in-sample limitation is recorded);
    - in parallel: the player's share of his team's defensive actions while on (the true D12b analogue; it also removes team style such as Barcelona's interception deficit);
    - pass criteria, applied to every defensive metric × group at once, never single cells: (1) within-league mean |ρ(possession, adjusted)| < 0.1 for both proxies, with the D12 sign test; (2) top vs bottom possession-quintile mean percentile gap ≤ 10 pts, reported with the team count; (3) r_full ≥ 0.6 retained (D19); (4) D9 league means reported before and after. A variant that flips the sign of ρ fails.
  - **Open for M4f/M9 (user, no change now): the direction of `interceptions`.** It is `higher_better`, fails D9 in every group and is driven by team style (Barcelona); `tackles_attempted`, `pressures`, `clearances` and `blocks` are already `style` (§10.7). Reclassify as `style` or add a team-style note: a user decision.
- **Radar on the comparison page** (user, 2026-10-06): add it to `tools/compare_page.py` once M4f fixes one percentile basis per vector; until then M4e shows tables only.
- `analytics/dna.py` + `pipeline/build_player_dna.py` → `player_dna.parquet`.
- Per scored row:
  - the feature vector on the D10 basis (percentile and robust z, both stored);
  - dimension scores = the mean of the member metrics' percentiles. Equal weights, written in ADR 004; no invented formula;
  - the registry version, so M5 and M6 can check it.
- The D17 drops are applied through the registry, not in this code.

### M4g — Football validation (brief §20)
- **Multi-split reliability averaging** (stats-reviewer, user 2026-10-04): r_full averaged over many random match splits instead of one odd/even split, applied to all metrics at once (never to single boundary cells). Then re-derive the registry. It addresses the cells decided by < 0.001 at the 0.6 cut-off, e.g. CB `interceptions` 0.599849 and ST `np_xg` 0.599369 (ADR 004 §10.11).
- **"Majority of leagues above the trigger" possession rule** (stats-reviewer M4c2b review, user 2026-10-04): evaluate a trigger that fires when most pool leagues' within-league |ρ| ≥ 0.4 (with the sign test), next to the current mean rule. Apply it to all metrics at once, never to single cells, then re-derive the registry and report every status change. It addresses ST `np_shots_on_target` (3 of 4 leagues above 0.4, mean 0.395) and ST `np_shots` (2 of 4, mean 0.372), where Serie A masks the others (ADR 004 §10.10).
- **Mover stability check** (user, 2026-10-06): for players with two stints in a season, measure per DNA metric how much the value changes with the team, e.g. Koné `long_pass_completion_pct` 0.595 at Lorient → 0.319 at Sunderland. Applied to all DNA metrics at once; report only.
  - **Then decide whether to restore the Passing dimension score** in CB, FB-WB, DM-CM and AM-Winger (user, 2026-10-07; ADR 004 §12.9). It is withheld (`pending_mover_stability`) because it rests on `long_pass_completion_pct` alone. The candidate replacements are pre-registered below.
- **Passing pre-registration (user, 2026-10-07; revises the bullet above).** Cohorts are per source (CLAUDE.md), so the passing metric need not be the same in every source. It must, however, pass the same rules in every source. **The stats-reviewer reviews both specs before any code.**
  - **StatsBomb: xPass model and three derived metrics (user, 2026-10-07: the best StatsBomb Passing score the data supports).**
    - **Model:** a per-pass completion probability xP from start and end location, distance, direction, `under_pressure`, pass height and body part. It is fitted on one source's passes and split into fit and test sets by match, never by pass. **Calibration and AUC are reported on the held-out matches.**
    - **Metrics**, each stored as numerator, denominator and pass count:
      - **execution** = (completed − Σ xP) per 100 passes, `higher_better`;
      - **execution under pressure** = the same on `under_pressure` passes only, `higher_better`. It has its own `min_attempts` and is subject to it;
      - **ambition** = mean (1 − xP) per pass, `style`.
    - **Role if the rules pass:**
      - The StatsBomb Passing dimension score = the mean of execution and execution under pressure. Each enters only if it passes the rules.
      - Ambition enters DNA as a `style` metric (in the vector, never in the score, never coloured).
      - `long_pass_completion_pct`'s role is decided with the outcome. If the new members pass, the Passing score no longer rests on it: it may stay in the vector, move to `style`, or move to display.
    - **Open spec items for the stats-reviewer, decided by default until then:**
      - Every pass gets an out-of-fold prediction (cross-fitting by match), so no pass is scored by a model that saw it. Calibration and AUC are reported on these held-out predictions.
      - Calibration is also reported within each subset a metric uses (`under_pressure` passes, each position group, long passes). A model miscalibrated on pressured passes would shift every player's execution under pressure.
      - Open-play passes only (the `op_` convention: type ∉ `RESTART_TYPES`).
      - Completed means a null outcome. "Pass Offside" counts as incomplete. "Injury Clearance" and "Unknown" are dropped.
      - The model is a logistic regression with spline terms (scikit-learn, no new dependency). A more flexible model only if calibration fails, and only with the reason recorded.
      - `min_attempts` uses the variance-components form, because these are continuous per-pass values (R1, as for `np_xg_per_shot`), not the beta-binomial.
    - **Known risk: the end location may leak the outcome.** The stats-reviewer decides the mitigation before any code (user, 2026-10-07). For an incomplete pass, the end location is where the ball was stopped (intercepted, blocked or out of play), not where it was aimed. Distance and end location may therefore partly encode the outcome. Ambition is the most exposed: a failed pass looks shorter and easier. Options include start location and direction only for the distance feature, or a sensitivity run without end-location features.
    - **Other rules to check:**
      - D17 applies: execution and execution under pressure share passes, and if their ρ ≥ 0.85 in a group, one is kept. The Passing score would otherwise count one signal twice.
      - The zero-heavy rule does not apply to execution (a residual with no mass at zero). It is reported as not applicable, not as a pass.
  - **Aggregated sources (Sportmonks): "accuracy vs context".**
    - **Definition:** actual − expected completion, where expected comes from completion ~ long-ball share + team possession + position group. It is fitted per league-season and per source.
    - **Naming:** never named "Passing quality".
    - **Recorded limitations:**
      - (a) It rewards safe passing: aggregated data has no pass direction or difficulty.
      - (b) Controlling for team possession partly removes real skill (over-control): good passers help create possession.
      - (c) Fitting per league-season centres the residual at zero in every league, so D9 passes by construction. D9 is reported but counts as no evidence for this metric (user, 2026-10-07).
    - **Display:** always shown next to an ambition measure (long balls, through balls and key passes per 100 passes).
    - **Text only (user, 2026-10-07).** CLAUDE.md allows no Sportmonks code before the source ADR is approved and its milestone is open. That ADR confirms the inputs exist per player and per team (long balls, through balls, team possession) and their definitions.
  - **The same evaluation for every candidate metric, applied to every group at once, never to single cells:**
    - D19 reliability, r_full ≥ 0.6 (multi-split, above), plus the ratio coverage rule and `min_attempts`;
    - the zero-heavy rule where it applies, and D17 collinearity;
    - the D12 possession trigger (|ρ| ≥ 0.4, sign test) and the M4g possession pass criteria: within-league mean |ρ| < 0.1, top vs bottom possession-quintile gap ≤ 10 pts;
    - D9 league means reported;
    - the mover stability check.
      - **Koné, prediction recorded before results (user):** execution should move him much less between Lorient and Sunderland than `long_pass_completion_pct` does (0.595 → 0.319).
      - To compare two metrics on one scale, the move is measured in local percentile points and in units of the metric's between-player SD in the group.
      - It is reported as an illustration of the group-level check, not as a criterion: one player never decides a metric.
    - **Threshold fixed before any metric is computed (user, 2026-10-07):** the mover stability check is report only today, so it has no pass threshold. The stats-reviewer proposes one (e.g. the stint-to-stint change of movers against a split-half baseline of non-movers), and the user fixes it.
  - **Two candidates only (user, 2026-10-07):** xPass for StatsBomb and "accuracy vs context" for aggregated sources. Team-relative completion (player minus team while on, the 2026-10-04 backlog item) is dropped. Fewer candidates means less chance of choosing a metric after seeing the results.
  - **Outcome:**
    - StatsBomb: the Passing score is restored from the execution metrics that pass, and ambition joins DNA as `style` if it passes.
    - Aggregated sources: "accuracy vs context" may become the directional Passing member in its groups.
    - A metric that fails stays display-only.
    - Either way the decision is recorded in ADR 004.
- **Mixed-role z extremes** (stats-reviewer C1, user 2026-10-06; ADR 004 §12.8): report the main-group minute share next to every row with |z| > 5 (34.4% of them are below 0.70, against 10.0% of the other rows).
- **Teammates among neighbours** (stats-reviewer, M4f3 review, user 2026-10-07): under family weights, CB and FB-WB top-10 neighbours are teammates 1.96× and 2.04× as often as a uniform draw (47 vs 24.0, 47 vs 23.1), AM-Winger 1.51×, DM-CM 0.96×, ST 0.75×; same-league neighbours run at 0.80–0.95×, so local percentiles remove league effects but not team style. Re-run this count after the possession / team-style work (same pass criteria applied to all groups at once); report only.
- **DM-CM role split** (user, 2026-10-06): DM-CM mixes holding 6s and advanced 8s; Isco (Real Madrid's 4-3-3 left-centre-mid, mapped to DM-CM) is the |z| 13.7 outlier on `box_entries_carry_p90` (ADR 004 §12.7). Evaluate whether DM-CM needs a role split, using the M4f3 neighbours and the DM-CM style map (below); no change before then.
- **Style map per group (diagnostic; user, 2026-10-07).** A 2D map of each group's DNA vectors, for reading the main axes of style and for the DM-CM role split. Report only: it changes no rule, weight or neighbour.
  - **Method:** PCA in scikit-learn (no new dependency, deterministic: `svd_solver="full"`) on the M4f vectors: local percentiles, primary scored rows, one fit per group.
    - **Family weights:** each metric's percentile / 100 is multiplied by √(its family weight), so Euclidean distance on the map's input equals the M4f weighted squared distance (ADR 004 §12.6). Multiplying by the weight itself would square it.
    - **No standardisation**, which would undo the weights.
    - **Fixed sign:** each component's sign is set so that its largest absolute loading is positive. Otherwise a re-run could mirror the map.
    - **Partial rows** (32 in 2015/16) are left out of the fit and the plot, because PCA needs every metric and §12.2 forbids imputation. The page lists them by name.
  - **Page:** a static, self-contained HTML/SVG per group, like the comparison page (no server, no external script).
    - Each point is a player-season; hovering shows name and team (SVG `<title>`).
    - The face-validity players are labelled: Kanté, Busquets, Özil, Payet, Messi, Neymar, Stones, Morgan and Isco.
    - **Axes are made readable:**
      - the variance explained by PC1 and PC2 (and their sum, shown prominently);
      - the top-loading metrics per axis with their signs, so an axis can be read as, say, "distributor vs ball-winner";
      - the axis names are the user's reading of those loadings, never generated.
    - **Stated on the page:** a 2D projection distorts distances, and the neighbour list (M4f) is authoritative. Two points close on the map need not be neighbours, and the reverse.
  - **DM-CM role split.** The 6 / 8 label comes from the data, not from the map: each row's share of DM-CM minutes in the StatsBomb lineup positions "Defensive Midfield" (Center / Left / Right Defensive Midfield) against "Center Midfield" (Center / Left / Right Center Midfield).
    - Rows with a share ≥ 0.7 either way are labelled 6 or 8; the rest are mixed.
    - The points are coloured by that label. The report gives each label's mean and spread on PC1 and PC2, and their overlap.
    - The question "do 6s and 8s form separate regions?" is answered from those figures and the M4f3 neighbours together, never from the picture alone.
    - The share needs per-position minutes, which the season parquet does not store (it stores group minutes only). The diagnostic spike reads them from the raw lineups. A new pipeline column waits until a split is decided.
  - **Later options, not M4g:**
    - UMAP: a new dependency, non-deterministic without a fixed seed, and its distances cannot be read;
    - the style map as a product feature, to consider in M5.
Analyses in `spikes/m4_validation.py`, written up in `docs/reviews/m4-validation.md`:
- threshold sensitivity (from D1);
- split-half reliability per metric;
- correlation and dominance per dimension;
- an outlier audit: the top 1% per metric traced to events (is it a data error?);
- multi-position players (D5 flags) and club changers (D2/D3);
- nearest neighbours on both D10 bases for about 20 well-known players, judged by the stats-reviewer for football sense.

M4 closes when this review is signed off. Any failure goes back to the decision that caused it, not to a patch.

---

## 3. Process
- The stats-reviewer reviews this plan **before any code**: now (M4a step 1), again on the registry list (M4c) and on every pipeline run (M4b, M4d).
- The data-auditor is not needed: there is no new source.
- STOP rule as in plan 004: any unexpected flag flip, a spike/pipeline disagreement, or a validation failure is explained case by case, and the user decides.

## Out of scope for M4
- League-strength adjustment.
- Age cohorts (no DOB column yet).
- The similarity metric choice (M5).
- Rarity scores (M6).
- A GK model (V2).
- Touch normalisation (needs a touches column, D18).
- The new-source spike checks (D22, deferred).

---

## M4b — detailed plan (2026-10-02)

**Goal:** add the stored columns and definition changes that ADR 004 decided, before any registry or percentile code. It is split like H4:
- **M4b1:** independent spikes and references first;
- **M4b2:** the pipeline, until it matches them exactly.

### Scope (from ADR 004)
| # | Change | Stored columns | Rows affected |
|---|---|---|---|
| D1 | Gate on nominal-equivalent minutes (`≥ 900 − 1e-6`; nominal length 120 with extra time) | season: `minutes_nominal_equiv`; `meets_min_minutes` now reads it | 31 rows fall below (28 pool outfield, 2 pool GK, 1 Bundesliga); 0 rise |
| D5 | Position margin flags | season: `position_group_margin_pct`, `position_group_share`, `position_group_ambiguous` (config `AMBIGUOUS_POSITION_MARGIN = 0.05`) | 13 gated rows flagged |
| D12 | Team pass share, open play only | match + season: `open_play_passes_attempted`, `team_open_play_passes_while_on`; season: `team_pass_share` | new columns only |
| D13 | Injury Clearance out of the completion denominators | match + season: `passes_injury_clearance`, `long_passes_injury_clearance`; new denominators for `pass_completion_pct` and `long_pass_completion_pct` | small pct shifts |
| D14 | Restarts (`RESTART_TYPES` = set pieces + throw-ins) out of progressive passes, pass entries and long passes | match + season: `long_passes_unknown_outcome` predicate aligned; definition change to `progressive_passes`, `final_third_entries_pass`, `box_entries_pass`, `long_passes_attempted`, `long_passes_completed` | progressive passes −16.1% overall; most FB-WB rows |
| D15 | Non-penalty xG and shots | match + season: `np_xg`, `np_shots`; season: `np_xg_p90`, `np_shots_p90`, `np_xg_per_shot` | new columns only |
| D16 | xGA fallbacks are not direct; the receipt must follow the key pass | definition change to `xg_assisted_direct`; new report-only counters | 6 passers, −0.37 xG in total |
| D22 | Limits for report-only issues | config: per-issue limit per 100 matches (3 × worst 2015/16 rate, floor 3 per competition-season), `STINT_LATE_START_FAIL_MINUTES = 2.0` | 0 failures expected |

Not in M4b:
- D3 (dedupe), D4 (statuses), D9 (`global_ok`), D11 (Hazen, cohort size) and D19 (`min_attempts`) are analytics: M4c and M4d.
- Crosses and switches keep restarts (ADR 004 D14): both are StatsBomb's own flags and display-only in DNA.

### Stats-reviewer conditions on the M4b definitions (2026-10-02): APPROVE WITH CONDITIONS; user decisions applied
These are folded into the definitions below. ADR 004 holds the final text.
- **C1/C2 (`team_open_play_passes_while_on`):**
  - key the team on `team.id`;
  - pieces are half-open, but a piece ending at its period's end is closed there, judged by the event's own period;
  - the player's own passes always count in his denominator (share ≤ 1);
  - add a check that every team open-play pass is credited to 7–11 players (the Laws minimum to the maximum; 2015/16 observed 8–11). The lower bound catches a pass credited to its passer only.
- **C3 (user: approved):** open play only, in both numerator and denominator. New columns `open_play_passes_attempted` and `team_open_play_passes_while_on`. This is the only candidate tested (pre-registered).
- **C4 (user: approved):** one exclusion set, `RESTART_TYPES = SET_PIECE_TYPES ∪ {"Throw-in"}`, for progressive passes, both entry metrics and long passes.
  - The long-pass unknown-outcome and Injury Clearance counts use the identical predicate.
  - Crosses and switches keep restarts.
  - ADR 004 records that the earlier "throw-ins stay in long passes" line was unmeasured and has been reversed.
- **C5 (nominal minutes):**
  - gate at `≥ 900 − 1e-6` (float fragility: Lindsay Rose sums to exactly 900);
  - `nominal_length` is 120 for extra-time matches;
  - add a test with 10 full matches of non-round durations.
- **C6:** the expected `meets_min_minutes` flips are **31 down** (28 pool outfield, 2 pool GK, 1 Bundesliga), 0 up, written from all rows.
- **Second group:** null at 0 minutes, alphabetical ties, never GK for an outfield row.
- **Penalties:** `np_xg` and `np_shots` identities get an integration test.
- **xGA:** the receipt must come after the key pass; otherwise the no-receipt fallback applies.
- **D22:** the late start is measured as `parse(from) − end[from_period]`. Bale and Dembélé are 1 s.
- **Tests to add:**
  - a pass at the final whistle, and one at the period-1 Half End with a half-time substitution;
  - the passer's own pass at a substitution instant;
  - the 7–11 credit check, including a pass credited to its passer only;
  - a long throw-in excluded from attempted, unknown and Injury Clearance alike;
  - the float gate;
  - extra-time nominal minutes;
  - second group null or tied;
  - the `np_xg` identity;
  - the receipt-before-key-pass guard.
- **Known red after M4b1:** the reconcile tests for `progressive_passes`, both pass entries, `long_passes_*`, `long_pass_completion_pct` and `xg_assisted_direct`.

### Definitions (final, after the review; ADR 004 holds the full text)
- **Restarts:** `RESTART_TYPES = SET_PIECE_TYPES ∪ {"Throw-in"}`, one shared constant.
  - Excluded from `progressive_passes`, both pass entry metrics, `long_passes_attempted` / `_completed`, and the open-play pass columns.
  - `long_passes_unknown_outcome` and `long_passes_injury_clearance` use the identical long-pass predicate.
  - `is_progressive_pass` gets a required keyword `pass_type_name`, the same contract as the entry functions: omitting it raises `TypeError`.
- **`open_play_passes_attempted`** = the player's Pass events in periods 1–4 whose type is not in `RESTART_TYPES`, any outcome.
- **`team_open_play_passes_while_on`** = his team's (`team.id`) open-play passes that credit him:
  - the pass's instant lies in his credited time (the union of his ADR 002 §31 pieces);
  - pieces are half-open [start, end), except that a piece ending at its period's end is closed there, judged by the pass's own period;
  - instants are compared within 1e-9;
  - **his own passes always count.**
  - Every pass must credit 7–11 players, otherwise the build fails.
- **`team_pass_share`** = `open_play_passes_attempted / team_open_play_passes_while_on`, from season totals (never an average of match ratios, ADR 002 §6a). It is null when the denominator is 0, and ≤ 1 by construction.
- **Penalty:** `shot.type.name == "Penalty"`. `np_xg` sums the xG of non-penalty shots; a shot without xG behaves as for `xg` (H1.4).
- **Injury Clearance:** `pass.outcome.name == "Injury Clearance"`.
  - `pass_completion_pct = completed / (attempted − unknown − injury_clearance)`; the long-pass ratio follows the same rule.
  - The pass is never completed, so the numerators are unchanged.
- **Nominal minutes:** `Σ_matches minutes_played × nominal_length / match_duration` (90, or 120 with extra time), computed in `build_player_season` from the match parquet. `meets_min_minutes = minutes_nominal_equiv ≥ MIN_MINUTES_THRESHOLD − 1e-6`. Per-90s keep `minutes_played`.

### M4b1 — Spikes and references (spikes/ + tests/fixtures/ + docs)
1. **The stats-reviewer reviews the definitions above.** Its conditions are folded in here, before any code.
2. Extend the independent spikes (stdlib only, no `eleveniq` imports) to the new definitions:
   - `verify_core_metrics.py`: `progressive_passes` without restarts; `np_xg`, `np_shots`; `passes_injury_clearance`; `open_play_passes_attempted`; `team_open_play_passes_while_on` (from its own `player_minutes` pieces, with the 7–11 check);
   - `verify_chunk_a_metrics.py`: long passes without restarts; `long_passes_unknown_outcome`, `long_passes_injury_clearance`;
   - `verify_chunk_b_metrics.py`: the pass entries without restarts, and `xg_assisted_direct` with the new fallback and receipt guard.
3. Rewrite `tests/fixtures/reference_totals/*.json` for the 5 leagues. Every unchanged column must come out identical (an assertion, as in H4a).
4. **Known red state:** the integration reconcile tests for chunk A, chunk B and core fail until M4b2. Recorded in `/finish-chunk`.

**M4b1 outcome (2026-10-02).**
- The 3 spikes ran for all 5 leagues. Every recorded reference value outside the M4b set is unchanged, enforced before any write by `update_reference` (core) and `guard_unchanged` (chunk A `base` and `chunk_a`, and `chunk_b`).
- **Reference changes:**
  - `progressive_passes`: −26,173 over 5 leagues (−16.1%). Throw-ins 16,671, free kicks 5,007, corners 4,478, kick-offs 17. By league: La Liga −6,517, PL −6,028, Serie A −6,046, Ligue 1 −6,871, Bundesliga −711.
  - The pass entries and `long_passes_*` lose their restarts; `xg_assisted_direct` loses its 6 La Liga no-receipt shots.
  - New: `np_xg`, `np_shots`, `passes_injury_clearance`, `open_play_passes_attempted`, `team_open_play_passes_while_on`, `long_passes_unknown_outcome`, `long_passes_injury_clearance`.
- **Pass credit:** 8–11 players per open-play pass in every league (11: 96.2–98.2%). The spike fails below 7, the Laws minimum.
  - A float-tolerance bug in the spike first credited 16–27 second-half kick-off passes per league to the passer alone. It was fixed, recorded in ADR 004 D12, and covered by a unit test.
- **Tests:** the fast suite is green. Integration: exactly the expected 15 red tests (the chunk A, chunk B and core reconcile tests in all 5 leagues). They turn green in M4b2.

### M4b2 — Pipeline (src/ + tests/)
1. **Snapshot** the current parquets to `data/interim/m4a_snapshot/`.
2. **Code:**
   - `metrics/entries.py`: `RESTART_TYPES`; `metrics/progressive.py`: the restart rule and the required `pass_type_name`;
   - `metrics/xg_assisted.py`: fallbacks, receipt guard and counters;
   - `pipeline/build_player_match.py`:
     - new match columns;
     - long passes on `RESTART_TYPES`;
     - `team_open_play_passes_while_on` from `player_timeline` and `PeriodClock.elapsed`. **Every open-play pass must credit 7–11 players (a failing quality issue, the same bound as the spike).**
     - D22 limits checked per competition-season in `build_competition_season`;
     - the late-start limit in `_stint_bound_issues`;
   - `pipeline/build_player_season.py`: nominal minutes, gate, margin flags, np columns and ratios, new pct denominators;
   - `config.py`: the new constants.
3. **Tests**, unit first:
   - throw-in exclusion in each predicate, and the `TypeError` contract;
   - penalty split;
   - Injury Clearance denominators;
   - fallbacks not direct;
   - `team_open_play_passes_while_on`: the final whistle, the half-time whistle (with a full-match teammate), a sub at the pass instant, treatment windows, a dismissal, restarts excluded, float rounding at a second-half kick-off, and the 7–11 bound;
   - nominal gate: a short match gives nominal > elapsed;
   - margin flags, including a 3-way near tie;
   - the D22 limits and the late-start limit.

   qa-tester writes spec tests from ADR 004 in `/finish-chunk`.
4. **Rebuild:** `build_player_match --force`, then `build_player_season`. Expect 0 failing issues.
5. **`spikes/m4b_before_after.py`** (snapshot vs rebuild), per league:
   - Δ per changed column;
   - every flag flip, against `spikes/expected_m4b_flips.json`, which is written **before** the rebuild from all rows: the 31 `meets_min_minutes` rows going down and 0 going up; `position_group`, `is_primary_stint`, `is_outfield` and `in_percentile_pool` unchanged.
   - **STOP rule** as in plan 004: any other flip, or a spike/pipeline mismatch, is explained case by case and the user decides.
6. **Re-run `spikes/m4_decision_measurements.py` on the new output:**
   - D17 and D19 on the M4b columns;
   - a new D12b section: `team_pass_share` |ρ| with leave-one-out possession, and split-half r_full, for DM-CM and CB (entry rule: |ρ| < 0.3 and r_full ≥ 0.6).

   The results go into ADR 004. They feed the M4c registry review.
7. **ADR updates:**
   - ADR 004 gets the M4b before/after;
   - ADR 002 §1, §14, §19, §26 and §27 get "amended by ADR 004" notes.
8. Integration suite green. The stats-reviewer reviews the run output, then `/finish-chunk` (code-reviewer, qa-tester).

**M4b2 outcome (2026-10-03).** Details are in ADR 004 §7.
- **Pipeline vs references:** all 15 reconcile tests pass, with no fixture changes.
- **STOP rule:** exactly the 31 expected `meets_min_minutes` flips and no other flag flip. A tampered list makes `spikes/m4b_before_after.py` exit 1.
- **D12b:** `team_pass_share` enters DNA for DM-CM; it fails for CB (ρ 0.36).
- **New guard:** `tests/test_spikes_independent.py` fails on any eleveniq import in spikes/. The imports were stripped from `m4_decision_measurements.py` and `profile_2015_16_leagues.py`; the profile output is byte-identical.
- **Test fixtures:**
  - The fixture `synthetic_small_teams` (MIN_PLAYERS_PER_PASS = 1, patched in build_player_match) is applied explicitly to the 16 tests on minimal synthetic teams.
  - Two extra-time tests had their input times corrected for the D22 late-start limit; their assertions are unchanged.
- **Fix round (2026-10-03):**
  - a pass outside its passer's credited time is still credited to him and to his teammates on the pitch, and reported as `events_outside_credited_time`. A first version excluded it; the spike's guard caught Serie A moving by −11 (Milinković-Savić, Lazio 3879637), and the user reverted it;
  - a pass after the whistle is clamped to the whistle (tested);
  - the xG-assisted fallback counters get D22 limits;
  - a restored assertion, an end-to-end fallback-report test and wording fixes.
- **Recorded for later (ADR 004 §8):**
  - M4c: `np_xg_per_shot` minimum shots; a `second_position_group` minimum share.
  - **New chunk M4b3 before M4c:** open-play `xg_assisted` / `key_passes` / `assists` (spike and references first), with set-piece delivery as a displayed signature trait. Reason: restarts are 13.4% of PL xG assisted (Payet 41%, Özil 31%).
- **User decisions (2026-10-03, ADR 004 §7):** H1 test reads the references; `np_xg` is out of ST DNA (r_full 0.599 < 0.6, no raw-xG substitute); no allow-list for newly triggered D12 cells; FB-WB `progressive_passes` recorded only; the team key is the event `team.id`, with a failing `pass_team_mismatch`.

### Verification
- Fast suite: `.venv/bin/python -m pytest tests/ -q -m "not integration"`.
- The 3 verify spikes for all 5 leagues: references equal the pipeline totals in every league.
- `spikes/m4b_before_after.py`: no flips outside the expected list.
- Integration suite: `.venv/bin/python -m pytest tests/ -q -m integration`.
- `spikes/m4_decision_measurements.py`: D12b, D17 and D19 recorded in ADR 004.

---

## M4b3 — open-play creation (planned, before M4c)

**Scope** (ADR 004 §8):
- open-play `xg_assisted`, `key_passes` and `assists` (passes not in `RESTART_TYPES`);
- spike and reference totals first, then the pipeline (the M4b1/M4b2 pattern);
- set-piece delivery becomes a displayed signature trait, not a DNA metric.
- **Reason:** restarts are 13.4% of PL 2015/16 xG assisted (Payet 41%, Özil 31%).

**Cleanup list** (code-reviewer notes on M4b2, 2026-10-03; done in this chunk, since it touches the same pass-type code):
1. **One shared `is_restart(pass_data)` helper** in `metrics/pass_types.py`. Both the numerator (`open_play_passes_attempted`, `_aggregate_events`) and the denominator (`team_open_play_passes_while_on`, `team_open_play_pass_credits`) use it, so `team_pass_share` cannot drift above 1. The M4b3 open-play creation columns use it too.
2. **Move the credit-window check into `PlayerTimeline`** (e.g. `credits_pass_at(period, at, period_end)`), next to `covers`. Then the half-open (credit) vs closed (`events_outside_credited_time`) semantics live in one place.
3. **ADR 004 D13:** add a sentence on Injury Clearances in the crosses denominator (`cross_completion_pct` keeps them; probably 0 in practice). Measure the count and state whether that is intended.
4. **ADR 002 §1 and §19:** refresh the stale body text under their "amended by ADR 004" banners: the §1 `is_progressive_pass` signature (now with `pass_type_name`), and §19's `SET_PIECE_TYPES` (now `RESTART_TYPES`).
5. **`build_player_season.py`:** the `_COUNT_COLS` comment should list `np_xg` among the Float64 columns.

**M4b3 progress (2026-10-03):**
- **Definition kept:** the key pass's own type (ADR 004 §9).
- **10% gate dropped (user):** play_pattern labels whole possessions. The disagreement is 271–362% of restart xG assisted, one direction only, and is recorded in ADR 004 §9.
- **Reverse-direction consistency guard** added to both spikes (2015/16: 0).
- **References written:** new keys only.

**M4b3 outcome (2026-10-03):**
- **Pipeline:** the six columns and their p90s; all reconcile tests pass with no fixture changes.
- **Before/after against `m4b2_snapshot`:** every pre-existing column is identical, there are 0 flips, the invariant holds, and the tampered-list demo exits 1.
- **Cleanup items 1–5 done.** Against `m4b3c_snapshot`, every column is identical, with 0 flips, and the references are byte-identical.
- **D13:** 27 Injury Clearance crosses, measured.
- **Step-e measurements:** in ADR 004 §9, for the M4c registry, with no decisions.

**M4c list additions from the M4b3 reviews (user, 2026-10-03; record only):**
- **Naming:** one convention for open-play columns (`op_` vs `open_play_`), resolved in the registry.
- **Display-only:** `op_assists` and `restart_assists` (r_full ≈ 0 for CB; `restart_assists` is 86.5% zeros in the gated pool).
- **UI label** "non-restart delivery" for the `op_` creation metrics (short-corner and short-free-kick routines count as `op_`; ADR 004 §9).
- **Long-throw note:** throw-ins are 5.6% of restart key passes, so `restart_` slightly rewards long-throw specialists.
- **One cohorts function builds every reference population and requires `meets_min_minutes`** (user, 2026-10-03). `in_percentile_pool` is only the coverage flag: 949 pool rows are below 900 nominal minutes and produce absurd per-90s (e.g. 13.6 key passes per 90 from 1 key pass in 6.6 min). Add a test that a below-gate row never enters any reference population. (AM-Winger `op_xg_assisted` possession ρ 0.42 is already on this list via ADR 004 §9.)

---

## M4c — metric registry (2026-10-03)
The detailed plan was approved on 2026-10-03. It has three chunks: M4c1 (second-group minimum share, pipeline), M4c2 (committed measurements and ADR 004 §10, reviewed by the stats-reviewer before code) and M4c3 (registry code and tests).

**M4c1 outcome (2026-10-03).**
- `SECOND_POSITION_MIN_SHARE = 0.10` (user U3).
- `spikes/m4c1_expected_second_group.py` wrote the expected list from `data/interim/m4c1_snapshot` before the change: 987 rows go to null (644 gated pool: 640 outfield, 4 GK).
- Before/after (`spikes/m4b_before_after.py --all-columns-identical`, with the second-group check added):
  - exactly the 987 listed changes;
  - every other column of the 78 match and 142 season columns identical;
  - 0 flag flips; the margin recomputation applies the new label rule.
  - A list with Begović removed exits 1.
  - Command: `.venv/bin/python spikes/m4b_before_after.py --snapshot data/interim/m4c1_snapshot --expected spikes/expected_m4c1_second_group.json --all-columns-identical`.
- Ambiguous rows keep their label by construction (second share > 1/6 − 0.05 ≈ 0.117); `TestSecondPositionShareBound` fails if the threshold exceeds the bound.
- The label is meaningful only for gated rows; M4e shows it only for `meets_min_minutes` rows. No 90-minute floor (user).
- Integration test: a full per-row recomputation of the label (both directions); a mutation (0.12 in the pipeline) makes it fail.

**M4c2 outcome (2026-10-04).** Details in ADR 004 §10.
- **Stats-reviewer on the `np_xg_per_shot` M method: approved with conditions.**
  - Variance components (the beta-binomial generalised), raw scale; the other variants are recorded, not used.
  - Bootstrap interval; the CB minimum is recorded as unstable.
  - `no_signal` gives null percentiles and no fallback.
  - Spike guards on the per-shot data.
- **User (2026-10-04):**
  - a row below `min_attempts` is left out of the reference population (all ratios);
  - the JSON records reference-population sizes, and M4d nulls a cell below `MIN_COHORT_SIZE`;
  - direct free-kick shots go to the backlog;
  - the gate uses `meets_min_minutes` semantics, recomputed and asserted on every row.
- **Stats-reviewer on the registry list: approved with conditions.** All were applied after the user's decisions:
  - **bugs fixed:** `shots_on_target_p90` counts penalties, so it is fixed display (D15); the spike's completion-rate denominators now subtract Injury Clearances (D13);
  - the possession trigger applies to ratios (an amendment after seeing the data, which only removes DNA metrics). `pass_completion_pct` leaves CB, FB-WB, DM-CM and AM-Winger; `long_pass_completion_pct` enters the allow-list (≤ 0.55);
  - the D3 dedupe runs after the group split (D9 161/210; `tackles_attempted` AM-Winger becomes `league_inconsistent`);
  - the signature minimum is max(5, the group's P90 of the count). `restart_key_passes` minimums are 5 / 5 / 13 / 19 / 5, with holders 4 / 20 / 33 / 31 / 5 (CB / FB-WB / DM-CM / AM-Winger / ST);
  - directions: `tackles_attempted`, `dribbles_attempted` and `pressures` are `style`;
  - sign test: |ρ| < 0.05 has no sign;
  - D17 also scans ratios and `team_pass_share` (none ≥ 0.85);
  - `crosses_attempted` is re-cited (restart deliveries);
  - kept D17 metrics must be DNA (validation);
  - exact definitions of ρ, the allow-list max, inclusive bounds and the D9 band;
  - M4f pre-registration: equal family weights, directional-only dimension scores, one percentile basis per vector. The missing-DNA-ratio policy is proposed in ADR 004 §10.8; **the user decides it before M4f**.
- **`spikes/m4_decision_measurements.py --write-json`** writes `src/eleveniq/analytics/measurements/statsbomb_27.json`. Two runs agree within 1e-9.
  - The spike's nominal minutes had been shadowed by the parquet column of the same name. They are now independent and asserted equal (2,628 rows).
- **Code-review round (user, 2026-10-04):**
  - ratio reference populations are built by filtering on `min_attempts`, then deduping (M4d uses the same order);
  - the D17 ratio pairs use rows meeting `min_attempts`;
  - possession ρ needs all 4 pool leagues (per-league n recorded);
  - the trait minimum applies to integer count columns only;
  - the stats-reviewer's concerns are recorded in ADR 004 §10.11, with no rule changes.

  The lists are unchanged.
- **Derived DNA counts (scratch prototype of `derive()`, before M4c2b):** CB 16, FB-WB 19, DM-CM 24, AM-Winger 21, ST 19. They replace the plan's 17 / 20 / 26 / 23 / 20. Every group has ≥ 6.

### M4c2b — `np_shots_on_target` (user, 2026-10-04; before M4c3)
- **Why:** `shots_on_target` counts penalties (376 on target in the pool leagues), which D15 removed from every other shot metric. It is now fixed display.
- **Pattern** (the same as `np_shots`):
  - spike and reference totals first, from raw events, without eleveniq;
  - then the pipeline column `np_shots_on_target` with its p90;
  - then a before/after against a snapshot: 0 flips and identical values on every existing column;
  - identity test: `np_shots_on_target` + penalties on target = `shots_on_target`.
- **Afterwards:** it goes through the registry rules like any metric, with no guaranteed DNA status. Re-run `--write-json`, re-derive, and update ADR 004 §10.6 and the expected counts.
- **Step 1 done (spike checkpoint):** `spikes/verify_core_metrics.py` writes `np_shots_on_target` to the core section. Only a new key is added; every may-change set is empty. The spike fails unless np + penalties on target = `shots_on_target`.

  | League | np on target | Penalties on target | `shots_on_target` |
  |---|---|---|---|
  | La Liga (11) | 3,174 | 89 | 3,263 |
  | Premier League (2) | 3,135 | 85 | 3,220 |
  | Serie A (12) | 3,067 | 117 | 3,184 |
  | Ligue 1 (7) | 3,018 | 85 | 3,103 |
  | Bundesliga (9) | 308 | 9 | 317 |

  Pool leagues: 376 penalties on target, as in ADR 004 §10.2 (Bundesliga 9 is outside the pool; all five leagues: 385). Known red until step 2: `test_core_metrics_reconcile` (5 leagues, column not found).
- **Step 2 done (pipeline):** `np_shots_on_target` in `_aggregate_events` (match) and `_COUNT_COLS` (season, so it gets a p90).
  - **Before/after** against `data/interim/m4c2b_snapshot` (sha256 equal to the M4c2 JSON's provenance), with `spikes/expected_m4c2b_flips.json` (none). Every existing column is identical (78 match, 142 season), and there are 0 flips. New columns: `np_shots_on_target` (match); `np_shots_on_target` and `np_shots_on_target_p90` (season).
  - **Identity:** np + penalties on target = `shots_on_target` on all 43,044 match rows. The before/after spike re-counts penalties on target from the raw events (385). Season sums and the p90 recompute.
  - **`--write-json` re-run:** two runs are identical. Against the M4c2 file, the only changes are the new metric, the parquet hashes, 2 season columns (144) and 18 new D17 report pairs (|ρ| ≥ 0.7); the 211 earlier pairs are unchanged.
  - **Rules:** `np_shots_on_target` is DNA in AM-Winger (r 0.700) and ST (r 0.679, lower reliability), and display in CB, FB-WB and DM-CM (r < 0.6). ADR 004 §10.6.
- **Derived DNA counts after M4c2b:** CB 16, FB-WB 19, DM-CM 24, AM-Winger 22, ST 20. M4c3's `derive()` must reproduce these.
- **M4c3 boundary report** (user, 2026-10-04): `derive()` also reports DNA cells within ±0.02 of the 0.7 lower-reliability bar (11 cells in 2015/16), and each ρ row carries the four within-league values (ADR 004 §10.10).

### M4c3 — registry code (2026-10-04)
- `src/eleveniq/analytics/registry.py`; rule constants in `config.py`.
  - `derive()` applies ADR 004 §10.3 to `statsbomb_27.json`. `REGISTRY` is resolved at import, so a rule-breaking entry fails the import.
  - Surface: `cell`, `dna_metrics`, `display_metrics`, `signature_traits`, `REGISTRY_VERSION` (sha256 of cells, rules and provenance) and `MEASUREMENT_SOURCE`.
- **Reproduces ADR 004 §10.6:**
  - DNA counts CB 16, FB-WB 19, DM-CM 24, AM-Winger 22, ST 20, with the same `*` and (AL) markers;
  - `restart_key_passes` is the only signature trait, minimums 5 / 5 / 13 / 19 / 5;
  - the §10.10 boundary report has exactly its 35 rows, plus the D9 rows below.
- **Carried items:**
  - `MetricSpec.integer_count` (False for the six xG count columns; the integration test checks it against the parquet dtypes);
  - `team_pass_share` D12b measurements are required (spike, registry, test);
  - the spike's `dedupe_global` is renamed `dedupe_population`, since it dedupes local populations too;
  - the 0.7 lower-reliability rows are in the boundary report.
- **D9 for `team_pass_share` (user, 2026-10-05).** The spike writes its league means and `derive()` computes `global_ok` from them.
  - DM-CM is `global_ok` (42.50–56.11), so its DNA cell has a global basis. Before this, its `global_ok` was null.
  - CB fails D9 (PL 39.69); the cell is display.
  - DNA cells failing D9 on their own measurements still exist in every group, so M4f's one-basis-per-vector decision (ADR 004 §10.8) stands.
- **Review fixes (user, 2026-10-05), from the /finish-chunk stats-reviewer and code-reviewer:**
  - **Stable version:** the spike rounds every stored float, including float dict keys, to 9 decimals. Reruns are byte-identical, and `REGISTRY_VERSION` hashes the rounded measurements. An integration test runs the spike twice and checks the bytes and the version.
  - **ADR wording:** the sentence on the regenerated JSON is corrected; HEAD rounded the same way equals it except the two keys.
  - **Override reasons** are `(status, "override: <adr>")`.
  - **D9 rows in the boundary report:** DNA cells within 3 pts (≈ the standard error of a league mean of percentiles). 51 of 101 DNA cells; `Cell.d9_margin` on every cell. Input for M4f.
  - **One D9 band:** `config.GLOBAL_OK_BAND`; the spike's copy is asserted equal.
  - Consistency tests on the JSON now use the 9-decimal storage precision.
- **Tests:**
  - `tests/test_registry.py`: schema; an independent re-check of every DNA and signature cell; derive == REGISTRY; rule-breaking fixtures; threshold edges.
  - `tests/test_registry_spec.py` (qa-tester, from the ADR text).
  - The plan's six rule-breaking fixtures were mutation-checked: each goes red when its guard is disabled. A tampered override fails the import.

---

## Backlog
- **For M5 (similarity) before Gem Finder (M6) uses z** (stats-reviewer, user 2026-10-06; ADR 004 §12.8): (C1) mixed-role rows dominate |z| > 5, so handle the main-group minute share first; (C2) z is heavy-tailed and not comparable across metrics, so M5 decides the cap (§12.6).
- **Carry `player_nickname` through the pipeline** (user, 2026-10-06): bundle with the next pipeline chunk, at the latest before M9. It is only in the raw lineups; adding it changes the season parquet sha256, so it needs a `--write-json` re-run, a new `REGISTRY_VERSION`, a percentiles rebuild and the before/after check. The comparison page keeps `player_name` until then.
- **M9: human-readable registry reason codes** (user, 2026-10-06): the comparison page shows rule codes as they are stored (`d17_dropped`, `d12b_not_preregistered`, `team_style_biased`, `unreliable`, `low_coverage`, `zero_heavy`, `no_signal`, `possession_untestable`, `allow_listed`); fixed-display cells already show their ADR reason text. The M9 frontend needs a fixed code → text table, with unknown codes raising.
- **FB-WB throw-in share of restart key passes** (ADR 004 §10.6, user 2026-10-06): measure the throw-in share among the FB-WB holders from raw events before the trait is labelled "set-piece delivery". Until then the comparison page labels it "Restart key passes (corners, free kicks, throw-ins)".
- **Registry: a clear error for a malformed measurements file** (code-reviewer M4d2, user 2026-10-06): `registry._Judge.d9` and its callers index `meas["cohorts"][group]["ref_n_local"]` directly, so a file missing a group raises a bare `KeyError` rather than `RegistryRuleError`. `ref_n_global` was already read the same way. Add a guard that `cohorts` holds every outfield group.
- **Registry follow-ups from the M4c3 reviews** (user, 2026-10-05):
  - an override to `signature` or `dna` should meet the bars it bypasses: r ≥ 0.6 (R15 / D19) and, for `dna`, no unadmitted possession trigger. Today's only override has r 0.838;
  - ADR 004 §10.3 should state that signature traits are exempt from the possession step (step 3 precedes step 5; set-piece duty is a role, not possession);
  - an override from display to `dna` forces `low_reliability` False, while `reliability` keeps the measured value. Derive the note from `reliability` instead;
  - the registry docstring's step list omits the ratio `no_signal` gate (step 4 in effect).
- **Empirical-Bayes shrinkage of ratios** (user, 2026-10-03): shrink each ratio toward its group mean by attempts, then re-evaluate `aerial_win_pct` for CB and FB-WB DNA. CB aerial ability is a known DNA gap: `aerial_win_pct` r 0.499 with 63.5% coverage, so only the aerial volumes are in DNA (ADR 004 §10.9).
  - **Linked: ST DNA has no chance-quality metric.** np_xg 0.599, op_key_passes 0.540 and op_xg_assisted 0.534 are below 0.6, and `np_xg_per_shot` is display (r 0.464, coverage 61.4%).
- **`op_crosses_attempted`** (stats-reviewer, user 2026-10-04; priority): crosses that are not restart deliveries. FB-WB DNA has no crossing metric, because `crosses_attempted` (r 0.787, 0% zeros) includes corners and free kicks and is fixed display (ADR 004 §10.3).
- **Pass value (xT / OBV-style)** (user, 2026-10-07): the value a pass adds (threat at the end location minus threat at the start, or an on-ball-value model), as a later extension of the M4g xPass work. It measures what a pass is worth; xPass measures how likely it was to arrive. Not part of M4g.
- **Direct free-kick shots in `np_xg_per_shot`** (user, 2026-10-04): 1,801 shots (4.7% of non-penalty shots) stay in. Taking free kicks is a team role (the D15 argument), and they pull down designated takers' xG per shot. Removing them needs a D15 amendment.
- **Second-ball creation** (user, 2026-10-03): an open-play key pass within N seconds after a corner or free kick, in the same possession. It measures creation from set-piece second phases, which the key-pass split (ADR 004 §9) counts as open play. It needs its own definition (N, which restarts, same possession) and a measurement. No work now.
- **Return the xG split instead of out-parameter dicts** (user, 2026-10-03): `compute_xg_assisted_per_player` fills `open_play_xga` / `restart_xga` (and `fallbacks`) passed in by the caller. Returning the parts (a small dataclass per player) would be a simpler API.

