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
  - **Findings.** Opponent open-play passes per match range from 299 (Barcelona) to 534–543 (Caen, Carpi), median 440. **Corrected in M4g4a (2026-10-08):** Carpi 534.2, Angers 523.6, Caen 519.0 (D12 open-play opponent passes per match; the original "534–543 (Caen, Carpi)" is not reproduced by any tested definition; M4g4a outcome below). Team per-match defensive counts barely move with possession (Barcelona's 45.7 recoveries per match sit inside La Liga's 43.9–50.9). The elasticity of the per-90 to opponent passes is well below 1 in most cells: DM-CM interceptions 0.52, recoveries 0.11, tackles 0.09, pressures 0.35; CB recoveries −0.68; AM-Winger interceptions ≈ 1.0. D12's pooled ρ does hide a tail (DM-CM interceptions: top-5%-possession band mean local percentile 33.6, against ≈ 44 implied by ρ −0.13), but that band is 15 rows from 4 teams, and without Barcelona it is 43.4. The clearest tail is `pressures` for high-possession attackers (≈ 25th percentile; `style`, so uncoloured).
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
- **Detailed specs, order and user decisions (M4g0, 2026-10-07):** "M4g — detailed plan" below; it supersedes the items here where they differ. Added there:
  - minute-bucket reliability (report only);
  - `dribbled_past_p90` → `style`, and the defensive duel success candidate;
  - the Defending restore rule;
  - the mover PASS rule, f̂ ≤ 0.5 (amended in M4g3a, ADR 004 §13.7: PASS iff the CI upper bound < 0.5; the point rule is comparison only);
  - the xPass leakage decision (start-based features).
- **Multi-split reliability averaging** (stats-reviewer, user 2026-10-04): r_full averaged over many random match splits instead of one odd/even split, applied to all metrics at once (never to single boundary cells). Then re-derive the registry. It addresses the cells decided by < 0.001 at the 0.6 cut-off, e.g. CB `interceptions` 0.599849 and ST `np_xg` 0.599369 (ADR 004 §10.11).
- **"Majority of leagues above the trigger" possession rule** (stats-reviewer M4c2b review, user 2026-10-04): evaluate a trigger that fires when most pool leagues' within-league |ρ| ≥ 0.4 (with the sign test), next to the current mean rule. Apply it to all metrics at once, never to single cells, then re-derive the registry and report every status change. It addresses ST `np_shots_on_target` (3 of 4 leagues above 0.4, mean 0.395) and ST `np_shots` (2 of 4, mean 0.372), where Serie A masks the others (ADR 004 §10.10).
- **Mover stability check** (user, 2026-10-06): for players with two stints in a season, measure per DNA metric how much the value changes with the team, e.g. Koné `long_pass_completion_pct` 0.595 at Lorient → 0.319 at Sunderland. Applied to all DNA metrics at once; report only. **Superseded (M4g0, 2026-10-07):** M4g3 defines a PASS rule (f̂ ≤ 0.5) that gates the Defending and Passing candidates; for current DNA metrics it stays a report.
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
      - Calibration is also reported within each subset a metric uses (`under_pressure` passes, each position group, long passes). **Superseded (M4g0):** the criterion subsets are height, body part, start third and `under_pressure`; position group and league are report only; long passes is not a subset (its length depends on the outcome). A model miscalibrated on pressured passes would shift every player's execution under pressure.
      - Open-play passes only (the `op_` convention: type ∉ `RESTART_TYPES`).
      - Completed means a null outcome. "Pass Offside" counts as incomplete. "Injury Clearance" and "Unknown" are dropped.
      - The model is a logistic regression with spline terms (scikit-learn). A more flexible model only if calibration fails, and only with the reason recorded.
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
  - **Method:** PCA in scikit-learn (deterministic: `svd_solver="full"`) on the M4f vectors: local percentiles, primary scored rows, one fit per group.
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

## M4g — detailed plan (M4g0, 2026-10-07)

**Goal:** turn the M4g items above into specs the stats-reviewer can review **before any code**, with every rule and threshold fixed before the data that it judges is computed. Report-only items stay report-only.

### User decisions (2026-10-07)
- **Order:** groups and registry come first, because every later measurement is per group and per DNA metric list. M4g1 → M4g6 below.
- **A. Mover sample:**
  - 450 nominal minutes per stint, same-group movers only, pooled across groups (never per group);
  - the non-mover baseline is a within-team date split, with its halves trimmed to the movers' minutes;
  - the threshold comes from the stats-reviewer in M4g0, and the user fixes it before any metric is computed.
  - Counts (2015/16 outfield, both stints ≥ the floor):

    | Floor | Movers | Same group | Cross-league |
    |---|---|---|---|
    | 900 | 6 | 5 | 3 |
    | 600 | 22 | 19 | 8 |
    | **450** | **29** | **24** | 12 |
    | 300 | 42 | 36 | 19 |

    The 24 same-group movers at 450, by group: AM-Winger 7, DM-CM 6, CB 5, ST 4, FB-WB 2 (corrected after the M4g0 review: the earlier 34 counted every stint group of the 29 movers). No player appears in two seasons.
- **B. β out-of-sample: leave-one-league-out.** β̂ is fitted on 3 pool leagues and the criteria are tested on the 4th, rotating. This replaces "fitted on one season, tested on another": only 2015/16 is configured, and StatsBomb Open Data has no second full-league season.
- **C. Majority-of-leagues trigger, fixed before the re-derive:**
  - It fires when ≥ 3 of the 4 pool leagues have within-league |ρ| ≥ 0.4, and those qualifying leagues all share one sign. It is adopted as an OR with the mean rule. The §10.2 no-sign rule (|ρ| < 0.05) applies; the 4th league's sign is not tested (stats-reviewer, M4g0).
  - It can only remove DNA cells. Per-league ρ has an SE of 0.16–0.18 for ST, so the rate of false triggers goes up; accepted.
  - **Known beforehand (§10.10):** it flips ST `np_shots_on_target` (3 of 4 leagues ≥ 0.4) out of ST DNA, and not ST `np_shots` (2 of 4).
  - **Multi-split r_full is adopted unconditionally.** It estimates the same quantity better and is not a new rule.
- **D. Defending:**
  - `dribbled_past_p90` → `style`. It rises with how often a player engages: within-group ρ with `tackles_attempted_p90` is 0.48 (CB), 0.55 (FB-WB), 0.48 (DM-CM) and 0.54 (AM-Winger). Local percentiles: Mascherano 88th dribbled past / 86th tackles attempted, Wes Morgan 4th / 31st. As `lower_better`, Defending would score Morgan 55 and Mascherano 32 (also stats-reviewer, ADR 004 §12.6).
  - **New candidate: defensive duel success** = `tackles_won / (tackles_attempted + dribbled_past)`, `higher_better`, under the same rules (reliability, possession criteria, mover stability).
    - **Denominator (user, 2026-10-07, after the M4g0 review):** `tackles_attempted + dribbled_past`, not `tackles_won + dribbled_past`, so a lost tackle counts as a failure.
      - Lost tackles are 39.2% of attempts and are never linked to a Dribbled Past, so the two terms are disjoint.
      - ρ with lost tackles per 90 is −0.10 to −0.27, against +0.04 to +0.16 for the other form.
      - Denominators are 30–45% larger (medians 27–86).
    - It covers all tackles, not only stopping dribblers: 33% of won tackles are against a dribble.
    - **Known prior:** `tackle_success_pct` failed (no row meets `min_attempts`, r_full ≤ 0.36). The larger denominator may or may not rescue it.
  - **Restore rule (amended by the user, 2026-10-07, after the M4g0 review):** a directional Defending member enters the score only if it passes all 4 possession criteria (M4g4) **and** the M4g3 mover rule. Untestable or inconclusive is not a pass.
    - **Why:** Defending was withheld for team style (Barcelona's interception deficit), and the possession criteria do not test team style.
    - Defending is restored in a group when ≥ 1 member passes. Otherwise it stays withheld, with a status saying which test failed (`possession_criteria_failed` / `mover_stability_failed`).
    - These are new `score_status` values for Defending. They replace `pending_possession_adjustment` once M4g4 runs.
    - `pending_mover_stability` stays Passing's code (§12.9) until M4g5. A Passing that fails then gets the same two failure codes.
- **#9 minute-bucket reliability:** added, report only (M4g2).

### M4g1 — Style map + DM-CM role split (spike, report only)
- **Files:** `spikes/m4g_style_map.py` → `data/processed/reports/style_map_<group>.html`, written up in `docs/reviews/m4-validation.md`.
- **Method** as in the M4g item list (the style map section). **Input:** `player_dna.parquet`, primary scored, complete rows.
- **6 / 8 label:**
  - **Source:** per-player-match position minutes from the raw lineups (`positions[]` from/to, the same clock as the pipeline's stint intervals, ADR 002 §31).
  - **Definitions:**
    - 6 = Center / Left / Right Defensive Midfield;
    - 8 = Center / Left / Right Center Midfield;
    - share = 6-minutes / (6-minutes + 8-minutes) for the row's team-season;
    - ≥ 0.7 → 6, ≤ 0.3 → 8, otherwise mixed.
  - **The slot label records formation more than role** (stats-reviewer, M4g0). A double pivot is coded Left / Right Defensive Midfield; a 4-4-2 or 4-3-3 is coded Left / Right Center Midfield.
    - Examples: Kanté and Drinkwater are both 0.42, Yaya Touré 0.71 counts as a 6, Mousa Dembélé 0.78 as a 6, and Kroos 0.66.
    - 37 of 100 teams have a DM-slot share ≥ 0.7.
  - **So a second label is reported:**
    - Center Defensive Midfield (a single pivot);
    - Left / Right Defensive Midfield (a double pivot);
    - Left / Right / Center Center Midfield.

    Each label is assigned by ≥ 0.7 of the row's DM-CM minutes, mixed otherwise. A within-team contrast is reported too: the 6-share difference between teammates in the same season.
  - **Check:** the 6 + 8 minutes reconcile with `minutes_dm_cm`, where those positions are DM-CM's only lineup positions; any gap is reported.
- **Figures reported (the question is answered from these, never the picture):**
  - counts per label; each label's mean and SD on PC1 and PC2; Cohen's d of 6 vs 8 on each;
  - the overlap coefficient of the two histograms on 20 equal-width bins over the pooled range of each PC;
  - **neighbour label purity:** for 6s and 8s, the share of their top-10 M4f neighbours (full vector, not the map) with the same label;
    - b is the label's base rate over all rows, mixed included;
    - the normalised lift = (purity − b) / (1 − b);
  - Isco's, Busquets', Kanté's and Schneiderlin's labels and neighbour labels.
- **Pre-registered reading (stats-reviewer, M4g0):** "6s and 8s form separate regions" requires both:
  - a normalised lift ≥ 0.4 for both labels;
  - |d| ≥ 1.0 on PC1 or PC2.

  The earlier "purity ≥ 2× the base rate" could not pass: b(6) ≈ 0.48, so 2× needs a purity of 0.95 or more. The reading is applied to the 6 / 8 label and reported for the pivot label too.

  The user decides the split; a split is its own chunk (ADR entry, `POSITION_GROUP_MAP`, cohorts, measurements, registry), before M4g2.
- **Outcome (2026-10-07; `docs/reviews/m4-validation.md`):**
  - **Reading: no separate regions.**
    - |d(6, 8)| is 1.28 on PC1 and 1.10 on PC2, so the d bar is met.
    - The lifts fail: 6s 0.30 (purity 0.63, b 0.47) and 8s 0.23 (purity 0.40, b 0.21).
    - Overlap is 0.51 on PC1 and 0.57 on PC2.
    - Within a team, the more-6 teammate sits on the 6 side in 128 of 153 pairs on PC1 and 133 of 153 on PC2.
    - The pivot label separates no better.
  - **Decision (user): DM-CM is not split.** The rule says so, the roles form a continuum, and the DNA neighbours already separate Isco from Busquets.
  - **M5 backlog:** a 6-share context column.
  - **Axis names are the user's** (each checked against the loadings, see the review). They read as the map does (user, 2026-10-07): PC1 left → right and PC2 bottom → top, on the pages, in the legend and in the review:
    - AM-Winger: PC1 "Physical ↔ Creator", PC2 "Distributor ↔ Shooter";
    - CB: PC1 "Stopper ↔ Ball-player", PC2 "Fewer aerial duels ↔ More aerial duels". This axis is aerial volume, not dominance: aerials lost loads above aerials won;
    - FB-WB: PC1 "Conservative ↔ Attacking", PC2 "Distributor ↔ Box presence";
    - DM-CM: PC1 "Holder ↔ Creator", PC2 "Deep playmaker ↔ Box-arriving";
    - ST: PC1 "Technical forward ↔ Aerial target", PC2 "Safe passer ↔ Link-up focal point".
    - The first proposals for CB PC2 ("Ground ↔ Aerial") and ST PC2 ("Runner ↔ …") were flagged: no loading supported the negative-end name.
    - Every loading quoted in the review's verdicts and in the legend is checked on each run; a mismatch fails it.
    - **Display rule, not pre-registered** (chosen after seeing the loadings): an axis end is named or described only by loadings ≥ 0.15 (unrounded), below the 1/√k = 0.20–0.25 of an even spread over 16–24 metrics. Against the earlier 0.10 it changes four legend ends (CB PC2 −, FB-WB PC1 −, FB-WB PC2 −, AM-Winger PC1 −); the review lists them.
  - **Readability pass (user, 2026-10-07, display only):** zoom / pan / pinch, search-to-zoom, plain-language cards with registry display names, an explainer panel, fine-role colours and toggles (`docs/reviews/m4g-fine-roles.md`: no fine-role cohorts or maps).
  - **Section ownership:** each spike writes only its own marked section of the review (`spikes/review_sections.py`, tested).
  - **Hover on every map:**
    - name, team, league and minutes, PC1 and PC2;
    - the top 3 pushes per axis (loading × centred weighted value; all pushes sum to the score), each with its local percentile;
    - every number re-derived from the parquets by the spike;
    - a player search box.

### M4g1b — Compare mode on the style maps (display only; user, 2026-10-07; before M4g2)
- **Scope:** page only. No change to the PCA, percentiles, DNA, the review's figures or `src/`.
  - **Files:** `spikes/m4g_compare.py` (facts, renderer, check), wired into `m4g_style_map.py` / `m4g_style_page.py`.
- **Interaction:**
  - a "Compare mode" toggle: click A, then B;
  - both are ringed (B dashed), labelled, and joined by a line;
  - Esc or "Clear" resets;
  - a page holds one position group, so B is always in A's group. A search that matches nobody on the page names the group page where the player is ("compare works within one position group"), or says he is in this group but not plotted.
- **Panel:**
  - both headers (name · team · league · minutes · role; the hover card's header);
  - **the M4f pair distance** (`m4f_dna_reference.pair_distance`, 4 decimals). Withheld families (Defending everywhere, Passing outside ST) have no dimension score, but their metrics stay in the vector and count in the distance, as in M4f;
  - **ranks both ways:** "B is A's #k most similar of N", and A's rank in B's list.
    - The candidates are the M4f3 neighbour candidates: every other primary row of the group, partial rows included, the player's own rows left out.
    - Rank = 1 + the number strictly closer, so rows at the same distance within 1e-12 share a rank. Exact ties occur (three CB rows): percentiles are discrete, and ordering ties by the key would rank on float noise.
    - Two rows of one player are not ranked against each other.
  - **Decisions (user, 2026-10-07):** the distance stays the M4f distance, withheld families' metrics included; tied distances share a rank.
  - **the map warning:** the map shows about X% of the style differences (PC1 + PC2), so map distance can mislead;
  - **metrics:** the 5 with the largest and the 3 with the smallest gap in local percentile points (ties by metric name), as "Label — A: top 3% · B: bottom 20%". The order is by percentile gap, not by each metric's share of the distance (user, 2026-10-07).
    - **One wording function for the cards and the panel** (`m4g_style_page.pct_words`; user, 2026-10-07). It has a neutral band:

      | Percentile p | Wording |
      |---|---|
      | ≥ 90 | very high, top ⌈100 − p⌉% |
      | 60 < p < 90 | high, top ⌈100 − p⌉% |
      | 40–60 (both edges included) | around average |
      | 10 < p < 40 | low, bottom ⌈p⌉% |
      | ≤ 10 | very low, bottom ⌈p⌉% |

      X is at least 1 and at most 40 outside the band, so "top 49%" cannot appear. The band edges are tested.
    - **Withheld families are tagged:** a metric from a family whose score is withheld in the group reads "(team-influenced: possession adjustment pending)" for Defending, or "(team-influenced: depends on the team's passing style)" for Passing (user, 2026-10-07). The internal xPass note stays in the footnote only.
    - When a Defending metric is listed, one sentence follows the lists: "Defending volume depends heavily on how much the team has the ball."
  - **withheld families for the group, in words** (the comparison page's reason text).
- **No math in JS:** distances, ranks, N, the 8 picked metrics per pair, every percentile phrase (a table of distinct phrases with an index per cell), the tags and the Defending flags are computed in Python and written into the page; the JS indexes and formats.
- **Check (fails the run):**
  - every fact is re-derived from `player_dna.parquet` with separate code (numpy for complete pairs; `pair_distance` only for partial rows) and compared with the page's data;
  - the page's own renderer runs in node for every ordered pair (297,714 panels over the five pages), each compared (sha1) with a Python rendering;
  - a digit outside a traced number span fails.
- **Page size:** +0.36 to +1.07 MB per page (DM-CM 1.39 → 2.46 MB). Mostly ranks (n²), metric picks and distances (n²/2).
- **Verified:** Busquets vs Kanté (distance 0.1430; #100 / #133 of 293) and Carrick vs Busquets (0.1136; #46 / #44), printed on each run.

### M4g2 — Reliability and trigger rules (measurements + registry; re-derive)
- **Multi-split r_full:**
  - **Split:** for each player-stint, its matches are assigned at random to two halves of equal size (odd count: the extra match goes at random).
  - **Split assignment (O4, user 2026-10-07; the reference spike reproduces it draw for draw):**
    - A stint is one season key row (`player_id, team_id, competition_id, season_id, source`) of the pool outfield rows (`in_percentile_pool ∧ is_outfield`), as in the odd/even method. Its matches are its match rows with `minutes_played > 0`, ordered by (`match_date`, `match_id`); n is their count.
    - Stints are visited in ascending key order. One generator, `numpy.random.default_rng(20261007)`, serves every draw, in this order: for split k = 0 … K−1, for each stint, `perm = rng.permutation(n)`, then, **only when n is odd**, `c = rng.integers(0, 2)`.
    - Half 0 takes the matches at positions `perm[:h0]` of the ordered list and half 1 the rest, with h0 = ⌊n/2⌋ + c for odd n, n/2 for even n. So for an odd count the extra match goes to half 0 when c = 1 and to half 1 when c = 0.
    - Every stint draws, n = 1 and below-gate stints included, so the stream does not depend on any later filter (gate, a ratio's attempts, a bucket). A stint with an empty half drops out of that split.
    - **One assignment per split serves every metric, ratio, `team_pass_share` and minute bucket.**
  - **Per split:** the method is the current one (half per-90s demeaned within league × group, pooled Pearson per group, Spearman-Brown).
    - **Spearman-Brown per split, then the mean (user, 2026-10-07):** r_k is the pooled Pearson of split k, r_full,k = 2·r_k / (1 + r_k), and the stored r_full = (1/K)·Σ r_full,k, the mean of the corrected values, not the correction of a mean r. SD and MC SE (= SD / √K) are over the r_full,k.
    - A cell whose r_k is undefined (fewer than 3 rows, or zero variance) in every split stores null, as now. Undefined in some splits but not all fails the run.
      - **Amendment (2026-10-07, found in the first run; user to confirm at the expected-flips checkpoint):** the fail applies to judged gated cells only. Partly-undefined cells at K = 3200: in the gated population only CB `restart_assists_p90` (fixed display; 376 of 3200 splits, zero variance in a half), plus 13 report-only bucket cells, mostly zero-heavy restart columns. Those store their statistics over the defined splits, with `n_undefined`. No judged gated cell is partly undefined.
    - **Demeaning population (stats-reviewer condition):** exactly the rows that enter r_k: after dropping stints with an empty half and, for ratios, after the both-halves-have-attempts filter. This is what `split_half` does now.
    - **Bias (stats-reviewer, measured):** Spearman-Brown is concave, so the mean of corrected values sits slightly below the correction of the mean: −0.0008 to −0.0019 on 4 cells near 0.6. It is systematic and conservative, and smaller than the MC SE. The mean r_k is stored too.
  - **Splits and seed:** K = 200 splits with `numpy.random.default_rng(20261007)`. K is raised if the Monte Carlo SE of any cell's mean r_full exceeds 0.002 (reported).
    - **Raise rule (O4):** K doubles (400, 800, …) and the whole run repeats from the seed until every judged cell's MC SE ≤ 0.002; the final K is stored and reported.
    - **Judged cells:** the 0.002 target applies to cells whose status depends on r, i.e. not fixed display and not D17-dropped. Fixed-display cells report their MC SE without driving K.
    - **Expected (stats-reviewer):** the SD of r_full,k is 0.034–0.054 near 0.6, so K = 200 gives an MC SE of 0.0024–0.0038. Expect K = 800, more for ratios on small halves.
    - **Cap: K = 3200**, fixed before the run. A judged cell still above 0.002 at the cap keeps its mean r_full in the registry, is listed with its MC SE in the review, and gets a boundary row if its mean is within ±0.02 of 0.6. The cap is not raised after the run.
  - **Stored:** the mean r_full over splits, the SD and the MC SE. The odd/even r_full is kept next to it for the before/after.
    - Also stored: the mean r_k and the final K.
    - **Correlation across cells:** one assignment per split serves every cell, so MC errors are correlated across cells. That helps metric-vs-metric comparisons, but "how many cells cross 0.6" is not a count of independent events.
    - **Ratios:** the n per split is reported as min / median / max, plus the share of rows present in every split.
    - **Odd/even comparison (stats-reviewer condition):** each cell reports z = (odd/even − mean) / SD. Odd/even behaves like one random draw: mean z ≈ +0.1 over 9 cells, single cells up to ±1.9 SD (FB-WB `np_shots_on_target` 0.59 → about 0.48; ST `np_xg` 0.585 → about 0.66, a possible flip into DNA). A change is never described as "improved reliability". The new value estimates the same quantity with less split noise.
  - Applies to every metric × group, ratios' split-half cross-checks and `team_pass_share` included.
  - For ratios, the rows with attempts in both halves change per split; the n range is reported.
  - **Limit (stats-reviewer, M4g0):** it removes split-assignment noise only. Sampling error over players (SE ≈ 0.04) remains, so cells near 0.6 stay fragile.
- **Majority trigger (C):**
  - the per-league ρ and sign are already measured; the rule goes into `analytics/registry.py`;
  - a league with |ρ| < 0.05 has no sign (§10.2);
  - both proxies are evaluated as for the mean rule.
  - **Allow-list precedence (O1, user 2026-10-07):** the allow-list admits a majority-triggered cell exactly as it admits a mean-triggered one (its bound stays the max |mean ρ|). This is what the pre-registration assumed: only ST `np_shots_on_target` was named as a flip.
    - Measured on the committed measurements before any code, 8 allow-listed DNA cells are majority-triggered: CB `progressive_passes_p90`, `progressive_carries_p90`, `long_pass_completion_pct`; FB-WB `progressive_passes_p90`, `progressive_carries_p90`; DM-CM `progressive_passes_p90`, `progressive_carries_p90`, `long_pass_completion_pct`.
    - Each is recorded as "majority-triggered, admitted by allow-list" in ADR 004 and in the boundary report, and is re-examined in M4g4.
    - **Stats-reviewer conditions (pre-code review, 2026-10-07):**
      - All 8 already fire the mean rule, so the majority rule changes none of their statuses. The only cells it fires on alone are ST `np_shots_on_target_p90` and ST `shots_on_target_p90` (already fixed display).
      - `derive()` returns the allow-admitted cells that fire the majority rule; a fast test asserts the set equals the 8 above on statsbomb_27. A new source reports its own set; it is never hand-copied.
      - Each recorded row carries, per proxy: the four league ρ, the count of leagues at |ρ| ≥ 0.4, and the max single-league |ρ|. The allow-list bound looks at means only: CB `long_pass_completion_pct` has a PL ρ of 0.715 / 0.750 and DM-CM `progressive_carries_p90` a La Liga ρ of 0.632 / 0.637, both admitted.
  - **Sign (already fixed in M4g0, restated):** the leagues at |ρ| ≥ 0.4 must all share one sign, and a league below 0.4 is not tested. So a league at ≥ 0.4 with the opposite sign blocks the trigger. The §10.2 no-sign rule (0.05) cannot bind here, because a qualifying league is at ≥ 0.4.
  - **Boundary rows (O3, user 2026-10-07; judged value per the stats-reviewer's conditions):**
    - V = the max over s ∈ {+, −} of the 3rd-largest |ρ| among leagues with sign s (|ρ| ≥ 0.05). There is no row when neither sign has 3 leagues.
    - `majority_trigger:{proxy}` when V is within ±`BOUNDARY_MARGIN` (0.02) of 0.4. ST `np_shots_on_target` (duration proxy, V = 0.408) is one.
    - `majority_sign:{proxy}` when V ≥ 0.38 and the largest opposite-sign |ρ| is within ±0.02 of 0.4.
- **Minute-bucket reliability (#9, report only):**
  - multi-split r_full per nominal-minutes bucket: 900–1350, 1350–2000 and ≥ 2000, per metric × group, with the n of each bucket;
  - also pooled over groups per metric;
  - **No status change follows from it.** A gate or note change needs a new ADR entry.
  - Buckets select on minutes, which go with role and stability, so the spread between players also differs by bucket. ST buckets of about 50 rows have an SE ≈ 0.1.
- **Defending registry changes (D):**
  - `dribbled_past_p90` direction → `style`;
  - add `defensive_duel_success_pct` as a ratio (`RatioSpec("tackles_won", ("tackles_attempted", "dribbled_past"))`), with a beta-binomial `min_attempts`, measured in `spikes/m4_decision_measurements.py`;
  - it runs through every registry rule like any ratio (coverage, `no_signal`, D19, D12, D17, D9), and its Defending-score membership waits for M4g3 (mover rule) and M4g4 (possession criteria).
  - ~~A stored season column only if the registry admits it.~~ **Stored unconditionally (O2, user 2026-10-07):** the registry requires every spec column in the season schema, and display ratios get percentiles too. Null on a 0 denominator; a season-stage rebuild, with every existing column byte-identical.
- **Then:**
  - `--write-json`, then re-derive and list every status change as metric × group: old → new, with the rule;
  - new `REGISTRY_VERSION`;
  - rebuild percentiles and DNA; reference checks pass;
  - re-run `spikes/m4f_weighting_checks.py` and report its neighbour-list changes.
- **Fixed before the run:** C and the multi-split adoption. The outcome cannot reopen them.
- **Expected flips approved (user, 2026-10-07)** (`spikes/expected_m4g2_flips.json`, written before the registry code), with the undefined-r amendment. Notes for the rest of the chunk:
  - **Odd/even optimism is recorded as hypotheses, not a finding.** Over 215 per-90 cells, mean z = +0.27 (SD 1.05) and the mean shift is −0.008. Candidate causes:
    - temporal balance: alternating dates spread both halves over the whole season, so form, injuries and coach changes are shared, while random halves can cluster in time;
    - home/away alternation, though on its own it would more likely lower r than raise it.
    No extra analysis now; it goes to the stats-reviewer at the end of the chunk.
  - **ST `dribbled_past_p90` enters DNA as `style`.** On the cards and the legend, strikers get plain words: how often he is dribbled past while pressing, not just "Dribbled past".
  - **Expected map changes:** CB PC2 (`aerials_lost` was its top loading) and FB-WB PC2. Any axis name that loses support comes back to the user with the new loadings.
  - **After the rebuild:** print Benzema's ST hover reasons before → after (`np_shots_on_target` leaves ST DNA).
  - **Display, at the next map regeneration (this chunk):** an ST PC1 legend note like CB's. "Aerial target" measures how often the player contests aerial duels (volume), not heading ability or headed goals.

- **Outcome (2026-10-07; ADR 004 §13.6):**
  - **Reliability:** K = 3200, the cap. 22 expected registry changes and nothing else (STOP rule passed). `REGISTRY_VERSION` aaecf86fa483 → 930d0019ee11.
  - **DNA counts:** CB 16 → 15, FB-WB 19 → 17, DM-CM 24 → 25, AM-Winger 22 (unchanged), ST 20.
  - **New ratio:** `defensive_duel_success_pct` is display in every group.
  - **8 majority-admitted cells:** recorded, for M4g4.
  - **Style maps:**
    - axes pinned to named metrics;
    - CB PC2 and FB-WB PC2 renamed "Fewer ↔ More aerials won", ST PC2 "Safe passer ↔ Box striker";
    - ST notes and the "Dribbled past while pressing" label added.
  - **New files:**
    - `tools/registry_snapshot.py`;
    - `spikes/m4g2_multisplit.py`, `m4g2_reliability_reference.py`, `m4g2_before_after.py`, `m4g2_reliability_review.py`;
    - `spikes/expected_m4g2_flips.json`;
    - `docs/reviews/m4g2-reliability.md`.

### M4g3 — Mover stability (report + the fixed threshold)
- **Population (A):**
  - outfield player-seasons with exactly two stints of ≥ 450 nominal minutes, in the same `position_group`;
  - any league pair;
  - pooled across groups.
  - A third, smaller stint is ignored and listed.
- **Values per stint:** the stint's per-90 or ratio, on two scales:
  - (a) the local percentile against the stint league × group's reference population (Hazen, as M4d), because stints below 900 have no stored percentile;
  - (b) the group's between-player SD units (SD over the pooled primary scored rows, league-demeaned).
  - A ratio stint below `min_attempts` is excluded for that metric (n reported). The same applies to the baseline halves; otherwise the baseline is noisier and pushes S down.
  - **All 24 movers are pooled for every metric, whether or not the metric is DNA in their group.**
  - **Sizes after `min_attempts` on both stints:**
    - `long_pass_completion_pct` 15;
    - `pass_completion_pct` 24;
    - `aerial_win_pct` 4, so untestable.
- **Statistic per metric:** the mover change = mean |Δ| over movers, on both scales.
- **Baseline per metric:**
  - **Pool:** non-movers are single-stint rows of the same group with ≥ 900 nominal minutes.
  - **Matching:** each mover's (m1, m2) is matched by drawing a non-mover with ≥ m1 + m2 minutes. Minutes are nominal-equivalent, the gate's clock. That non-mover's matches are taken in date order: the first ≈ m1 minutes, then the next ≈ m2 minutes (nearest whole match).
    - These are regular starters (up to about 2,860 minutes).
    - Many second stints are at struggling clubs (Sunderland, Carpi, Palermo, Frosinone). Selection like this tends to make team effects look smaller. **Not supported (M4g3a review):** for this estimator a shared drift to weaker clubs inflates mover changes (towards FAIL); direction not established (docs/reviews/m4g3-movers.md).
  - **Draws:** R = 200 draws per mover, from `default_rng(20261008)`. **Deviation (M4g3a, ADR 004 §13.7):** one stream per metric for the draws and one for the bootstrap, seeded from 20261008 and the metric name; set before the run script first executed.
  - **Result:** the baseline change = the mean |Δ| over all draws.
- **Ratio S = mover change / baseline change**, with a paired bootstrap CI over movers (B = 2000, seed fixed): each resample recomputes the movers' |Δ| and their matched baselines.
  - A root-mean-square version is reported next to it. mean |Δ| turns into the decision quantity only if the two |Δ| distributions have the same shape; the RMS version is exact.
- **R_m:** the Pearson correlation of the matched non-mover halves (league × group demeaned, scale b). It is a baseline-only quantity.
- **Threshold (stats-reviewer proposal, fixed by the user 2026-10-07 before any mover change was computed):**
  - **Decision quantity:** the team share of the stable between-player variance, f̂ = (Ŝ² − 1) · (1 − R_m) / R_m, on scale (b) with the RMS form of S. Scale (a) is reported only.
    - f = 0: the value follows the player;
    - f = 1: a mover simply takes on his new team's level.
  - **PASS iff f̂ ≤ 0.5** (point estimate; **amended in M4g3a**, ADR 004 §13.7: PASS iff the CI upper bound < 0.5, FAIL iff the lower bound > 0.5, else inconclusive; the point rule is comparison only), i.e. S ≤ S*_m = √(1 + 0.5 · R_m / (1 − R_m)): at least half of the stable differences between players travel with the player.
  - The paired bootstrap CI is reported. When it contains 0.5 the result is labelled "inconclusive at n".
  - Fewer than 12 eligible movers → `untestable`, which is not a pass (the §11.3 precedent).
  - **Why not a fixed S:** with value = player + team + noise, S² ≈ 1 + σ²_team / σ²_noise. A fixed S would fail reliable ratios on small team effects and pass noisy per-90s with large ones.
  - **Power (n = 24, R_m 0.7):**
    - a pure-player metric is wrongly failed 0.6% of the time;
    - a fully team-driven metric is wrongly passed 8% of the time;
    - f = 0.25 fails 16% of the time; f = 0.75 passes 21%.

    0.33 was considered, but at this n it cannot be told apart from 0.5.
  - Applied to every current DNA metric and, later, to each M4g4 / M4g5 candidate.
- **Koné illustration** (prediction recorded in the M4g item list): `long_pass_completion_pct` 0.595 → 0.319, on both scales.
- **User decisions (2026-10-08, before any mover change is computed; ADR 004 §13.7):**
  - **R_m stays the matched-halves correlation** (pooled over all n × 200 draws, recomputed per bootstrap resample; amended after the M4g3a pre-code review, ADR 004 §13.7), as above. The M4g2 multi-split r_full is not used: it is a full-season, Spearman-Brown value over random halves, so it overstates the reliability of a stint-length half and pushes f̂ down (towards PASS). It is reported next to R_m as a cross-check only, stepped down by Spearman-Brown to the movers' median stint minutes.
  - **"Inconclusive is not a pass" stands.** The effective PASS rule is therefore the CI upper bound < 0.5, stricter than the point rule the M4g0 power figures describe. Before the real run, the spike simulates the effective rule's power (value = player + team + noise; f = 0, 0.25, 0.5, 0.75; R_m 0.7; n = 24 and n = 15) and the figures go into the ADR. If power is low, Defending and Passing may stay withheld for 2015/16; the rule is not relaxed after the results are seen.
  - **Known beforehand:**
    - M4g3 changes no `registry_status`, note or `score_status`. Passing stays `pending_mover_stability` until M4g5.
    - The verdict is per metric (pooled), the same in every group.
    - `defensive_duel_success_pct` is `untestable`: 0 of 24 movers meet `min_attempts` on both stints. Defending's testable directional DNA member is `ball_recoveries_p90` (as M4g4 adjusts it). **Corrected (M4g4 plan, 2026-10-08):** `tackles_won_p90` is `d17_dropped` in all 5 groups, so it is not a DNA member (ADR 004 §13.8).
    - Also `untestable`: `aerial_win_pct` (4), `np_xg_per_shot` (2). Tested: `pass_completion_pct` (24), `long_pass_completion_pct` (15).
    - At a 900-minute floor there are 5 same-group movers (all metrics untestable); the floor stays 450.
  - **Counts re-run 2026-10-08:** 29 movers at 450, 24 same group, 5 in different groups; 10 of the 24 are cross-league (the "12" above counts all 29). No mover has a third stint ≥ 450.
  - **The test is a function**, so M4g4 and M4g5 call it again on adjusted or new columns.
- **Chunks:**
  - **M4g3a (spikes + docs):** `spikes/m4g3_movers.py` (measurements, power simulation, Koné, the list of second stints at struggling clubs) → `docs/reviews/m4g3-movers.md`; `spikes/m4g3_movers_reference.py` (no `eleveniq` imports, `--check-output`, same RNG streams). Before code, the stats-reviewer reviews the R_m cross-check, the power simulation and the pre-stated outcomes. `/finish-chunk`: code-reviewer, stats-reviewer (output); qa-tester skipped (no src/).
  - **M4g3b (registry):** spec in "M4g3b — Mover verdicts in the registry" below. Summary: the reference writes `spikes/expected_m4g3_flips.json` before any registry code (0 status, 0 note changes; one `mover_verdict` per metric). The mover block enters `statsbomb_27.json`; the registry exposes the verdicts with no status effect; `REGISTRY_VERSION` changes. STOP rule: the before/after script, extended to verdicts. Rebuild percentiles and DNA (only stamps change; both reference checks pass). `/finish-chunk`: full tier.
  - **Order (user, 2026-10-08):** M4g3c first, then M4g3a → M4g3b.

### M4g3b — Mover verdicts in the registry (spec; user, 2026-10-08)
No status effect: no `registry_status`, note, reason or `score_status` depends on a mover verdict.

- **Principle: measurements store numbers; the registry applies thresholds** (as every registry rule).
  - **Stored per metric** (new `movers` block in `statsbomb_27.json`):
    - n and the excluded movers;
    - f̂, CI bounds, R_m, Ŝ², `boot_inf`;
    - P(PASS | f = 0) and its SE;
    - the CI bounds on 5 extra seeds;
    - the cross-check R and its f̂ (report only).
  - **Derived in the registry:** the verdict (PASS / FAIL / inconclusive / untestable), `underpowered`, `borderline (seed-sensitive)`, `PASS (below model range)`.
  - **Thresholds live in the registry's `Rules`** (`mover_f_threshold = 0.5`, `mover_min_n = 12`, `mover_low_power = 0.8`), which is hashed into `REGISTRY_VERSION`, so changing one changes the version. The spike's copies (`m4g3_power.THRESHOLD`, `MIN_MOVERS`, `LOW_POWER`) are checked equal to `Rules` by the STOP rule (code-reviewer, M4g3b spec).
  - **Untestable metrics** (n < 12): verdict `untestable`; `underpowered`, `borderline_seed` and `below_model_range` are `None` (not computed; `power_at_f0` = 0 must not read as underpowered).
- **Step 1: measurement side (spikes):**
  - **Refactor first (a change to the reviewed M4g3a spike):** the engine moves out of `main()` into an importable function taking the stream key, and the metric list becomes the written-out `MOVER_METRICS`. The primary-stream output must equal the committed M4g3a output (`data/interim/m4g3_movers.json`, every field) and the reference must pass before anything else changes.
  - **Frozen metric list:** `MOVER_METRICS` is written out in `spikes/m4g3_movers.py` (the 34 metrics of M4g3a), not read from a registry snapshot (that would be circular once the verdicts live in the registry). The STOP rule checks that every DNA metric of every group is in the list.
  - **Seed sensitivity, a rule, not a name** (user, 2026-10-08):
    - the primary stream plus 5 extra seeds, keys `'m4g3_draws#k'` / `'m4g3_boot#k'`, k = 1–5;
    - each extra seed's verdict uses the same rule (threshold 0.5 on the CI bounds; n < 12 untestable);
    - `borderline (seed-sensitive)` iff any extra seed's verdict differs from the primary verdict;
    - the reference reproduces all six streams.
  - **Single writer** (user, 2026-10-08): `spikes/m4_decision_measurements.py --write-json` imports the mover engine and writes the `movers` block, as it embeds the M4g2 multi-split engine. The block records the frozen list and the seeds; `schema_version` is bumped.
  - **Reference:** `spikes/m4g3_movers_reference.py --check-output` also checks the measurement file's `movers` block, all six seeds, within 1e-9.
- **Step 2: expected flips, before any registry code:** `spikes/m4g3_movers_reference.py --write-expected` writes `spikes/expected_m4g3_flips.json` from the new measurements and the current registry snapshot, deriving the §13.7 rule itself (no registry code).
  - **Content:** 0 status changes; 0 note or reason changes; one `mover_verdict` per metric with its verdict and four flags.
  - **Pre-stated from M4g3a:**
    - 9 PASS, 19 inconclusive, 0 FAIL, 6 untestable, 18 underpowered;
    - `below_model_range` on `op_xg_assisted_p90` and `fouls_won_p90`;
    - `borderline` on `pressures_p90`: expected, not guaranteed. The five extra seeds are new streams, not the stats-reviewer's. Others may be flagged; the rule decides, not a list of names.
    - If `pressures_p90` is not flagged, or other metrics are, the checkpoint stops and reports to the user (investigate or amend); it is not accepted silently.
- **Step 3: registry:**
  - `MoverVerdict`, one per metric (pooled; the same in every group): verdict, f̂, CI, R_m, n, `power_at_f0` + SE, `underpowered`, `borderline_seed`, `below_model_range`.
  - `Derivation.movers` with a `mover(column)` accessor. `Cell` is unchanged.
  - **Validation:** the block covers the frozen list; an unknown metric, or a non-finite value (other than f̂ = +∞), raises `RegistryRuleError`.
  - `REGISTRY_VERSION` changes: the measurements are already hashed, and the verdicts are added explicitly.
  - `tools/registry_snapshot.py` dumps the verdicts.
- **Step 4: STOP rule:** `spikes/m4g3_before_after.py` (generalised from `m4g2_before_after.py`) stops on:
  - any cell change of any kind;
  - a verdict or flag different from `expected_m4g3_flips.json`;
  - a DNA metric missing from the verdicts.
- **Step 5: rebuild:**
  - percentiles, then DNA; only the `registry_version` stamps may change (a frame comparison asserts it);
  - `m4d_percentiles_reference.py` and `m4f_dna_reference.py` pass;
  - the M4e golden pages carry the version: `--update-golden` may change only the version-stamp lines (footer and `data-src`), and the commit calls out its diff summary;
  - style maps re-run (stamps; display only).
- **Step 6: docs and review:**
  - ADR 004 §13.7 outcome line: the new version, 0 status changes, verdicts as expected.
  - Full `/finish-chunk`: qa-tester (src/ logic), stats-reviewer (output only; method reviewed before code), code-reviewer, integration tests (rebuild).

### M4g3c — Movers on the style maps (display only; user, 2026-10-08)
- **Scope:** spikes only (`spikes/m4g_movers_display.py`, wired into `m4g_style_map.py` / `m4g_style_page.py`). No change to the PCA fit, percentiles, DNA, neighbours, cohorts or `src/`.
- **Combined dot:** for a player with 2+ stints with a complete DNA row in the same position group, the nominal-minutes-weighted average of the stint map inputs (local percentile / 100 × weight), projected with the existing PCA. It lies on the segment between the stint dots (checked: equals w₁·dot₁ + w₂·dot₂ within 1e-9).
  - Label: "season profile, weighted by minutes (display only)".
  - Never in the PCA fit, neighbour lists, percentile populations or ranks; the check asserts the PCA input is unchanged.
  - **2015/16:** Perotti (Genoa / Roma), Quagliarella (Torino / Sampdoria), Shelvey (Swansea / Newcastle), Koné (Lorient / Sunderland), Khazri (Bordeaux / Sunderland).
- **Default view:** the combined dot replaces the stint dots. A click expands it: the stint dots (team-coloured) appear, joined by an arrow in date order (first match date). Click or Esc collapses. A search for the player lands on the combined dot.
- **Combined-dot hover (user, 2026-10-08):** "Played for", the map position and "click to see each stint". **No `pct_words`:** an average of local percentiles is not a percentile, and for cross-league movers it mixes two leagues' cohorts.
- **Compare mode:** the combined dot is not selectable; expand it, then pick a stint.
- **Expanded panel:** minutes and weight per team; the M4f `pair_distance` between the stints (4 decimals); the 3 metrics with the largest local-percentile change (ties by metric name), in `pct_words` per stint, with "each against its own league" for cross-league movers.
- **"Played for"** on every hover card and the panel: each stint in date order — team · league · minutes · month range (e.g. "Aug–Jan") — plus the main positions in plain words from `m4g_common.position_minutes` and the fine-role rule (≥ 0.7 → "mostly right winger", else the two largest roles).
  - Stints under 900 min: "no profile (under 900 min)".
  - Bundesliga (PARTIAL) stints: listed, marked "partial coverage — minutes undercounted" (user, 2026-10-08).
  - Match dates come from the raw match files.
- **One profiled stint only:** that stint's dot, with "also played N min for Team X — not enough for a profile" (52 player-seasons in 2015/16).
- **Stints in different groups:** each on its own map, with "also on the X map" (Doucouré: AM-Winger / DM-CM).
- **Checks:** every hover and panel number is re-derived with separate code and traced (the M4g1b pattern: Python re-derivation, the page's renderer in node with a sha1 comparison, the digit-trace rule).
- **Backlog (needs an ADR):** a true season row from summed counts across stints.
- `/finish-chunk`: code-reviewer; qa-tester skipped (spikes only); stats-reviewer skipped (no metric, cohort or registry change).

### M4g4 — Possession and Defending (pipeline column, spike first)
- **Exposure:**
  - `opponent_open_play_passes_while_on`: the open-play Pass events of the *opponent* whose instant lies in the player's credited time;
  - the D12b crediting rules mirrored onto the opponent: half-open pieces, closed at the period end, the event's own period, the 1e-9 tolerance, a pass after the whistle clamped to it, and the team key `team.id`;
  - stored per match and season, with the minutes it is credited over.
  - **Check:** each opponent open-play pass is credited to 7–11 players of the defending team.
  - Spike first, an independent recount (`spikes/m4g_opponent_exposure.py`); the proxy agreement (median ratio 0.999) is re-checked.
- **E** = opponent open-play passes per 90 on. **E_ref** = the median E of the group's primary scored rows (single source).
- **Candidates**, evaluated for every Defending DNA metric (style and directional) × group:
  - `adj = p90 × (E_ref / E)^β`, with β = 0, β = 1, and β̂.
  - **β̂:** the team-level log-log OLS slope of the team's minutes-weighted mean per-90 (group players) on log E, both league-demeaned. Fitted leave-one-league-out (B): it is fitted on 3 leagues and judged on the 4th, and the reported β̂ CI comes from a team bootstrap (B = 2000, seed fixed) on all 4 leagues.
  - **Team share:** the player's count / the team's count of that action while he is on (the same crediting machinery, one `team_<action>_while_on` column per action).
- **Criteria (all four, per candidate, every metric × group at once):**
  - (1) the within-league mean |ρ(possession, adjusted)| < 0.1 for both proxies, with the D12 sign test;
  - (2) the gap in mean local percentile between the top and bottom possession quintiles ≤ 10 pts, reported with the team count;
  - (3) multi-split r_full ≥ 0.6;
  - (4) the D9 league means reported before and after.
  - A candidate that flips the sign of ρ fails.
  - For β̂, criteria (1) and (2) are judged on the held-out leagues only.
- **Ratios:** `adj` and team share are not defined for a ratio (`defensive_duel_success_pct`), so only β = 0 applies.
- **β̂ in the pipeline:** the all-4-league fit. Leave-one-league-out serves the criteria only.
- **Selection rule (stats-reviewer, M4g0):** a fixed order, per metric × group. The first candidate that passes is chosen, in the order of least change to the metric: β = 0 → β̂ → team share → β = 1.
  - Choosing the smallest criterion (1) would pick on noise: the SE of a mean within-league ρ is ≈ 0.065 at about 60 rows per league.
- **Recorded limits (stats-reviewer, M4g0):**
  - E_ref is a constant per group, so it changes no local percentile or ρ; it only scales the displayed value.
  - Criterion (1) is close to circular for E-based adjustments (E ≈ 1 / possession); leave-one-league-out helps.
  - Criterion (2)'s quintiles hold about 4 teams per league, so the gap's SE is ≥ 6 pts against a 10-pt bar. Some cells will fail when β = 0 is in fact fine.
  - Team share depends on how many teammates share the role: a lone 6 gets a larger share than one of a double pivot.
- **Restore (D):**
  - The score's members are the directional Defending members whose selected candidate passes all 4 criteria **and** whose metric (as adjusted by that candidate) passes the M4g3 mover rule. Untestable or inconclusive is not a pass.
  - An adjusted member enters the score and the vector adjusted, so weights stay as §12.6.
  - The vector change re-runs the M4f3 checks.
- **Then:** re-run the teammates-among-neighbours count (same pass criteria, all groups); report only.
- **The 8 majority-triggered allow-listed cells (M4g2 O1, user 2026-10-07):** re-examined after the possession adjustment: CB `progressive_passes_p90`, `progressive_carries_p90`, `long_pass_completion_pct`; FB-WB `progressive_passes_p90`, `progressive_carries_p90`; DM-CM `progressive_passes_p90`, `progressive_carries_p90`, `long_pass_completion_pct`.
  - **Before its run (stats-reviewer condition), M4g4 states what it decides for these 8.** If it decides nothing, it says so.
  - **Stated (user, 2026-10-08, before any M4g4 run): M4g4 decides nothing for the 8.** They stay DNA (allow-listed), and their decision moves to the M4 sign-off list (see "M4g4 plan" below). This statement satisfies the M4g2 stats-reviewer condition.
- **Open items, to decide in the M4g4 plan (user, 2026-10-08):** both decided in "M4g4 plan" below (D6, D7).
  - ~~consider requiring a restore-supporting mover PASS to hold across several seeds~~ → D6: PASS on all six streams.
  - ~~apply the pre-registered "PASS (below model range)" label to the M4g4 mover re-run~~ → applied; D7 adds the scale (a) quantity it needs.
- **Compare-mode re-check (user, 2026-10-07; M4g1b pairs):** after the possession adjustment, re-run Busquets vs Kanté and Carrick vs Busquets on the DM-CM compare panel. Report only.
  - **Before (M4g1b, unadjusted):** Busquets–Kanté distance 0.1430, Kanté #100 for Busquets and Busquets #133 for Kanté (of 293). Carrick–Busquets 0.1136, #46 / #44.
  - **Expectation, written before the adjustment:**
    - Busquets–Kanté: the rank improves, because their largest gaps are Defending volumes (interceptions, ball recoveries) that track how much the team has the ball;
    - the long-pass difference remains (Passing is not adjusted by M4g4).
  - **Prediction added in the M4g4 plan (2026-10-08, before any M4g4 run):** see "Pre-stated outcomes" below. Since M4g2 the baseline is 0.1431 (#107 / #140) and 0.1134 (#47 / #42).

### M4g4 plan (user decisions 2026-10-08, before any M4g4 measurement; ADR 004 §13.8)
**Facts read before the decisions** (committed measurements and the M4g3b registry snapshot; no M4g4 quantity computed):
- **`ball_recoveries_p90` is the only directional Defending DNA member in any group.** `tackles_won_p90` is `d17_dropped` in all 5 groups (ρ 0.90–0.93 with `tackles_attempted_p90`); `defensive_duel_success_pct` is display everywhere and `untestable` as a mover. So every group's restore rests on `ball_recoveries_p90`.
- **Its β = 0 possession ρ** (mean within-league, duration / pass share; the M4c measurement, the quantity criterion (1) judges at β = 0):

  | Group | Duration | Pass share | β = 0 on (1) |
  |---|---|---|---|
  | CB | +0.251 | +0.283 | fails |
  | FB-WB | +0.106 | +0.140 | fails (narrowly) |
  | DM-CM | −0.078 | −0.083 | passes |
  | AM-Winger | −0.127 | −0.141 | fails |
  | ST | −0.157 | −0.126 | fails |

  In CB and FB-WB recoveries rise with the team's own possession, so β̂ is expected to be negative there. This sign prediction comes from the table above.

**Stats-reviewer pre-code review (2026-10-08): APPROVE WITH CONDITIONS.** It verified every fact above and reproduced the power table within MC SE. Its conditions are folded in below: decisions C1–C5 (user, 2026-10-08) and wording.

**Criteria, made operational (user decisions C1–C3, 2026-10-08):**
- **C1. Criterion (1)** = |mean over the 4 leagues of the signed within-league ρ| < 0.1, on both proxies. **Reading (a): no sign-test escape.** A cell whose leagues disagree in sign does not pass for that reason.
  - **Known when choosing:** (a) is the stricter reading. At β = 0 it fails AM-Winger `pressures_p90` (leagues −0.25 / +0.07 / −0.30 / −0.29, mean −0.194), AM-Winger `blocks_p90` (mean −0.159 / −0.191) and ST `dribbled_past_p90` (mean −0.120 / −0.122). Reading (b) would have passed them at β = 0 (raw). `ball_recoveries_p90` is unaffected.
  - **Report-only flag "inconsistent across leagues":** any single league |ρ| ≥ 0.3, on either proxy, for the candidate evaluated.
- **C2. Sign-flip rule:** a candidate fails when, on either proxy, its mean ρ has the opposite sign to β = 0's mean ρ and its |mean ρ| ≥ 0.05 (the §10.2 dead zone). The reference is β = 0's signed 4-league mean; for β̂, the candidate side is the held-out mean (C3 wording). If β = 0's own |mean ρ| < 0.05 on a proxy, the rule does not apply on that proxy.
- **C3. Criterion (2):** on both proxies, the same proxy values as criterion (1).
  - Player rows are ranked within each league by the proxy (ties at average rank); quintile = ⌈5 · rank / n⌉.
  - Local percentiles are recomputed on the candidate column (Hazen, within league × group, primary scored rows).
  - The gap is |mean percentile of quintile 5 − mean percentile of quintile 1| per league, averaged over the 4 leagues; it passes at ≤ 10 points on both proxies. The team count per quintile is reported.
- **Leave-one-league-out aggregation (wording):** for β̂, criteria (1) and (2) use, for each league l, β̂ fitted on the other 3 leagues and applied to l. Criterion (1) is the mean over the 4 held-out leagues of ρ_l, the same form as β = 0's 4-league mean. Criterion (2) is the per-league gap averaged over the 4 held-out leagues.
- **β̂ fit inputs (wording):**
  - **Rows:** the group's primary scored rows (≥ 900 nominal, the E_ref population).
  - **Per team:** y = log of the minutes-weighted mean per-90 of those rows; x = log of the minutes-weighted mean E of the same rows. Both are league-demeaned.
  - **Fit:** unweighted OLS over teams. Teams with no group row are dropped. A team mean ≤ 0 fails the fit (no silent drop).
  - **CI:** the team bootstrap resamples teams within each league (B = 2,000, seed fixed; report only).
- **Fixed pre-registered order (wording; replaces "least change"):** with β̂ unconstrained, the order β = 0 → β̂ → team share → β = 1 is a fixed order, not an ordering by size of change. A |β̂| > 1 gets a report flag, as β̂ < 0 does (D2).

**Decisions:**
- **D1. Criterion (4) is report-only.** "Passes all four criteria" means (1)–(3) pass and (4) is reported.
- **D2. β̂ is unconstrained**, as pre-registered. A negative β̂ removes a positive possession link in the right direction; the sign-flip rule still applies. A β̂ < 0 carries the report flag "rises with own possession".
- **D3. Mover re-run on adjusted columns:**
  - **Halves are adjusted with their own exposure:** half p90 × (E_ref / E_half)^β, with E_half from the same matches. Team share: Σ player count / Σ team count over the half's matches. The same applies to the multi-split halves of criterion (3).
  - **β̂ is the fixed all-4-league pipeline fit** for the mover run and for criterion (3); it is not re-fitted per half or split.
  - **Pooled per candidate:** for each (metric, candidate), all 24 movers are adjusted, each with its own group's parameters for that candidate, and one verdict is computed. A group's restore uses the verdict of its selected candidate.
  - **Streams (exact):** `m4g3_power.cell_rng("m4g4_draws", metric, candidate)` and `cell_rng("m4g4_boot", metric, candidate)`. The extra streams replace the first key with `"m4g4_draws#k"` / `"m4g4_boot#k"`, k = 1–5. Candidate tokens: `"beta_hat"`, `"team_share"`, `"beta_1"`.
  - **Scope:** mover runs for `ball_recoveries_p90` (the only directional member) under all three non-zero candidates, whatever each group selects; a verdict for a candidate a group did not select is labelled **"report only, not used for any restore"** everywhere it appears: the review doc, the registry fields (a `used_for_restore` flag, false for an unselected candidate) and every page (user, 2026-10-08). Style members get no mover runs (D4 = C). `power_at_f0` is computed per (metric, candidate) at that candidate's R_m.
  - **Scales:** (a) and (b) use the adjusted season column's own reference population (league × group of the adjusted values).
  - **β = 0 leaves the column unchanged, so it reuses the M4g3b verdict and is not re-run.** A re-run on new streams would be seed-shopping. Its `s2_rms_a` (D7) is back-filled from the M4g3 halves on the M4g3 primary streams, like the other 34 entries.
- **D4 = C. Adjusted columns in the vector:** every Defending DNA member (style or directional) whose selected candidate passes (1)–(3) enters the vector adjusted. The mover rule gates the score only. A member whose selected candidate is β = 0, or which no candidate passes, stays raw.
  - **Why:** the raw columns never needed a mover PASS to be in the vector; requiring one for the adjusted column would set a higher bar for the version with less team effect.
  - Weights stay as §12.6. A vector change re-runs the M4f3 checks and the teammate count in every group whose vector changes.
- **D5. New Defending `score_status` strings** (`dna.WITHHELD_STATUSES`, with tests): `possession_criteria_failed`, `mover_stability_failed`, `mover_stability_inconclusive`, `mover_stability_untestable`, `mover_seed_sensitive`, `mover_below_model_range`.
  - "underpowered at n" is a note on the status, not a status.
  - They replace `pending_possession_adjustment`. A displayed mover verdict always shows its flags (M4g6 requirement).
  - **Status precedence** (one status per group; the first failed test along the restore rule): possession criteria → mover verdict (untestable / FAIL / inconclusive) → seed sensitivity → below model range.
  - **Definitions (wording):**
    - the verdict is the primary stream's;
    - `mover_seed_sensitive` = primary PASS and any extra stream ≠ PASS (the registry's `borderline_seed`, registry.py);
    - `mover_below_model_range` = primary PASS with CI upper bound < 0 and √`s2_rms_a` > 1.
    - With one directional member per group (2015/16), the status is that member's. With more than one, Defending is restored if any member passes. Otherwise the status is that of the member that got furthest along the precedence; ties go to the first metric name.
- **The 8 majority-admitted cells: M4g4 decides nothing.**
  - M4g4's exposure is opponent passing, a defensive exposure. Applying it to on-ball progression volume would test a candidate never pre-registered for them.
  - Their M4g3 verdicts are all inconclusive: `progressive_passes_p90` f̂ 0.32 [−0.15, 1.04], `progressive_carries_p90` 0.25 [−0.11, 0.74], `long_pass_completion_pct` 0.93 [0.06, 2.05], underpowered.
  - They stay DNA (allow-listed) and move to the M4 sign-off list, next to family weighting and the FAIL list. M4g5's xPass outcome is an input for the two `long_pass_completion_pct` cells. **M4g5 is deferred (2026-10-09), so the sign-off decides them without it.**
  - This statement satisfies the M4g2 stats-reviewer condition.
- **D6. A restore-supporting PASS must hold on all six streams**: verdict PASS ∧ ¬`borderline_seed`.
  - **Cost** (n = 24, simulated): 3–4 points of power at f = 0.
  - **Why:** it removes restores that come down to seed luck (`pressures_p90` passed on 6 of 11 streams). At f = 0.5 the model's stream noise is small, so the simulated gain there is small (0.08–0.11 → 0.07–0.10).
  - ≥ 4 of 6 was considered: it costs nothing and protects nothing.
- **D7. "PASS (below model range)" cannot support a restore on its own** (§13.7). The §13.7 exception (scale (a) agrees) needs a stored quantity:
  - `s2_rms_a`, the RMS form of S on local percentiles (the decision quantity's form), is added to the movers block;
  - the registry applies S_a,RMS ≤ 1 (S_a,RMS = √`s2_rms_a`). A second season is not available.

**Power at the adjusted column's expected R_m** (`ball_recoveries_p90`; homogeneous model, 1,000 datasets, B = 2,000; MC SE ≤ 0.016; `spikes/m4g4_power.py` in M4g4a reproduces it):
- **Expected R_m:** unadjusted 0.591 with f̂ 0.31 (CI [−0.21, 1.03], so the point below is uncertain). Removing the whole team share takes stable/noise from 1.445 to ≈ 1.0, so R_m ≈ 0.50.
  - **Corrected (stats-reviewer):** per-half E variation can move R_m either way. It is mostly the fixture schedule, not sampling noise; if β is right, adjusting for it removes variance and raises R_m. Team share: 0.40–0.55 (unknown). β = 0: 0.591 exactly, but D3 fixes that verdict.
  - An E adjustment removes only the possession-explained part of the team effect; team pressing style stays. So the realistic case lies between the f = 0 and f = 0.25 columns below.
  - The reviewer's independent re-run (600 datasets) agrees within MC SE.

  | R_m | f = 0, primary | f = 0, all 6 | f = 0.25, all 6 | f = 0.5, all 6 |
  |---|---|---|---|---|
  | 0.45 | 0.43 | 0.40 | 0.18 | 0.09 |
  | 0.50 | 0.49 | 0.45 | 0.21 | 0.07 |
  | 0.55 | 0.60 | 0.56 | 0.22 | 0.11 |
  | 0.59 | 0.66 | 0.63 | 0.26 | 0.10 |

- A perfectly adjusted, purely individual `ball_recoveries_p90` passes about 40–55% of the time (f = 0), and less with residual team style (f = 0.25: about 20%). Every expected case is "underpowered".

**Pre-stated outcomes (user, 2026-10-08, before any M4g4 run):**
- **The Defending score most likely stays withheld in most or all groups for 2015/16.** The rule is not relaxed after the results are seen (§13.7).
- **If DM-CM selects β = 0 for `ball_recoveries_p90`, DM-CM Defending stays withheld** (`mover_stability_inconclusive`, underpowered at 24). Under D3 the verdict is M4g3b's: f̂ 0.31, CI [−0.21, 1.03].
- **Compare pairs (C4, user 2026-10-08):** under D4 = C any DM-CM Defending style member whose selected candidate is not β = 0 enters the vector adjusted. At β = 0 these fail criterion (1) (mean ρ, duration / pass share):
  - `interceptions_p90` −0.144 / −0.143, `pressures_p90` −0.183 / −0.163, `blocks_p90` −0.216 / −0.219;
  - `clearances_p90` −0.275 / −0.289 and `fouls_committed_p90` −0.122 / −0.094 (missed in the first draft; stats-reviewer).
  - `ball_recoveries_p90` probably selects β = 0 and does not change.
  - **"Not tested" iff neither DM-CM `interceptions_p90` nor `ball_recoveries_p90` is adjusted:** those are the mechanism the M4g1b expectation names. Any other DM-CM adjustment and the pair distances and ranks are reported alongside.
  - If no DM-CM vector member changes, both pairs come out identical (0.1431, #107 / #140; 0.1134, #47 / #42).

**Chunks:**
- **M4g4-0 — reference hardening (the backlog gate; spikes + tests):**
  - the six "M4g3b reference hardening" items;
  - **acceptance:** the committed `statsbomb_27.json` and `m4g3_movers.json` are unchanged and pass the reference; each new check goes red on a tampered copy (one test per item).
  - `/finish-chunk`: code-reviewer; qa-tester skipped (spikes and tests only); stats-reviewer skipped (no method change).
  - **Done (2026-10-08, `5886a06`):** the rerun of `m4g3_movers.py` is byte-identical to the committed output; the reference passes on the committed files with both flags; three tampered copies fail on exactly the tampered fields. Fix round: a missing extra-seed CI is a clean error; the reference's separate label check was unreachable (the power check implies it) and is removed. Code-reviewer PASS, fix delta PASS.
- **M4g4a — exposure spike and power (spikes + docs):**
  - `spikes/m4g_opponent_exposure.py` (independent recount; 7–11 credited defenders per pass; the 0.999 proxy agreement re-checked);
  - `spikes/m4g4_power.py` (the table above).
  - the M4g3b movers block gains `s2_rms_a` (D7) for the 34 metrics, with the reference checking it, before the M4g4c write.
    - **STOP rule (user, 2026-10-08):** the 34 M4g3b mover entries must be identical in every existing field, with `s2_rms_a` as the only added key, and no verdict or flag may change (asserted).
    - **Known beforehand:** the measurements are hashed into `REGISTRY_VERSION`, so this write moves the version (78ceb0dae425… → new). Percentiles and DNA then change in their stamps only, as in M4g3b.
  - **Outcome (2026-10-08):**
    - **Exposure recount** (`spikes/m4g_opponent_exposure.py`, all 5 competition-seasons, 1,346,205 open-play passes; corrected from 1,348,207 after the stats-reviewer's raw recount):
      - the own-team recount equals the pipeline's `team_open_play_passes_while_on` on 43,044 of 43,044 player-match rows (0 missing either side);
      - every opponent pass is credited to 8–11 defenders (11: 1,278,242; 10: 65,322; 9: 2,640; 8: 1);
      - the 8 is Chievo 5–1 Frosinone (3879835), period 2, 71:41, a pass by Bizzarri (Chievo GK): Frosinone were down to 9 (Ajeti red 16:45, Chibsah second yellow 51:43) and Gucher was off for treatment from 71:31 to 71:42. Correct.
    - **Proxy agreement re-checked:** on the 1,233 gated pool rows, exact / proxy has median 1.0013 (own team; 1 / 1.0013 = 0.9987 is the M4e 0.999, which was proxy / exact) and 1.0011 (opponent), 5–95% 0.98–1.02, range 0.90–1.06. Per player-match the 1st–99th percentile is 0.50–1.35 (opponent) and 0.48–1.36 (own team), so the exact column is worth building.
    - **M4e "534–543 (Caen, Carpi)" is not reproduced.** On the D12 open-play definition, opponent passes per match are Carpi 534.2, Angers 523.6, Caen 519.0 (Barcelona 298.9, median 439.9: both M4e anchors reproduced).
      - Caen is 461.9 untyped + 53.8 Recovery + 3.2 Interception passes per match; Carpi 474.7 + 56.7 + 2.8.
      - No tested definition (all passes, open play without Unknown / Injury Clearance, open play plus set pieces, untyped only; per match and per 90) gives Caen ≈ 534 and Carpi ≈ 543, and Angers sits between them on every one.
      - The M4e method was the stats-reviewer's ad hoc run and was not recorded. The pipeline definition is D12's, so the recount stands and the M4e note is corrected here: the top of the range is Carpi 534.2.
    - **Power:** `spikes/m4g4_power.py` reproduces the table above exactly (same seeds).
    - **`s2_rms_a` write:** the STOP rule (`spikes/m4g4a_movers_before_after.py`) passed: every existing field identical, `s2_rms_a` added to the 28 testable metrics only, 0 cell, verdict or flag changes. `REGISTRY_VERSION` 78ceb0dae425… → 86b6389f389f…. The reference reproduces `s2_rms_a` within 1e-9.
      - Rebuild: percentiles and DNA changed in their stamps only (frame comparison); both reference checks pass; the two golden pages and the M4g1 validation section changed in the version string only.
      - **Observation (report only):** both "PASS (below model range)" metrics have √`s2_rms_a` ≤ 1: `fouls_won_p90` 0.818, `op_xg_assisted_p90` 0.964. So scale (a) agrees for both under D7's RMS form, while §13.7's mean-|Δ| form gave 1.05 for `op_xg_assisted_p90`. Neither is a Defending member, so no restore depends on it.
      - **For M4g4d (stats-reviewer, M4g4a output review):** the D7 gate √`s2_rms_a` ≤ 1 is a point estimate with no uncertainty, and S² at n = 24 is noisy. The RMS and mean-|Δ| forms can fall on opposite sides of 1 (`op_xg_assisted_p90` 0.964 vs 1.05), and scale (a) differs from scale (b) on some PASS metrics: `aerials_won_p90` √`s2_rms_a` 1.25 vs √`s2_rms` 0.99 (movers block of `statsbomb_27.json`: `s2_rms_a` 1.569, `s2_rms` 0.985; the quoted figures are S, not S²). D7's exception applies only to a PASS (below model range), i.e. a CI upper bound < 0; this note covers that case for an adjusted `ball_recoveries_p90` and no other, so it does not widen D7. If it arises, report √`s2_rms_a` next to the mean-|Δ| form and flag any case where the two forms fall on opposite sides of 1.
- **M4g4b — pipeline columns:**
  - `opponent_open_play_passes_while_on` and `team_<action>_while_on` for the Defending actions, per match and season;
  - every existing column byte-identical; `--force` rebuild and integration tests.
- **M4g4c — candidates, criteria and movers (spikes):**
  - every Defending DNA metric × group × candidate: criteria (1)–(4), leave-one-league-out β̂, the team-bootstrap β̂ CI (report only) and the selection;
  - the mover runs per (metric, candidate) under D3, with an independent reference that has no `eleveniq` imports;
  - **`spikes/expected_m4g4_flips.json` is written by the reference before any registry code.**
- **M4g4d — registry and DNA:**
  - per-cell `possession_adjustment` (candidate, β, criteria values, mover verdict);
  - the D5 statuses;
  - adjusted percentiles and vector (D4);
  - measurements `schema_version` 3 (the `possession` block, `movers` entries per (metric, candidate), `s2_rms_a`);
  - a new `REGISTRY_VERSION`;
  - rebuild; M4f3 checks, teammate count, compare re-checks, golden pages;
  - full `/finish-chunk`.

**Expected before/after:**
- 0 changes to `registry_status`, note or reason on any existing cell.
- The 34 M4g3b mover entries are identical in every existing field; `s2_rms_a` is the only added key, and no verdict or flag changes (asserted; added in M4g4a, which also moves `REGISTRY_VERSION`).
- 5 Defending `score_status` rows leave `pending_possession_adjustment`, to the values in the expected file.
- **C5 (user, 2026-10-08):**
  - D17 is re-checked on the adjusted columns, report only: a shared team factor raises the ρ between adjusted columns (e.g. `tackles_attempted` vs `dribbled_past`, 0.48–0.55 today).
  - 0 note changes: an adjusted column keeps its D9 note (e.g. "team-style influenced" on `interceptions_p90`). Flagged for the M4 sign-off.
- D4 = C can mix per-90 and share units in one vector. That is fine on percentiles, but the display must label which adjustment each column carries (M4g6).
- Vector columns change only where the expected file lists them.

**STOP rule (`spikes/m4g4_before_after.py`)** stops on:
- any unlisted cell change;
- a Defending status or vector column different from `expected_m4g4_flips.json`;
- any change to an existing M4g3b mover field, verdict or flag, or any added key other than `s2_rms_a`;
- `Rules` thresholds not equal to the spike's copies.

**Process:** the stats-reviewer reviewed this spec before any M4g4 code (2026-10-08, APPROVE WITH CONDITIONS, folded in above). M4g4-0 did not wait on the method.

### M4g4b — Exposure and team-action columns in the pipeline (plan approved by the user, 2026-10-08)
- **Columns (counts only, match and season level; Int32 at match level, season = sum):**
  - `opponent_open_play_passes_while_on`: the M4g4a rule. An open-play pass by team T is credited to every player of the other lineup team whose window holds its instant; there is no passer exception; 7–11 defenders or the build fails.
  - `team_<action>_while_on` for the 8 Defending DNA members with a team-share candidate (registry `86b6389f389f`): `ball_recoveries`, `tackles_attempted`, `pressures`, `clearances`, `dribbled_past` (DNA in all 5 groups), `fouls_committed` (FB-WB, DM-CM, AM-Winger, ST), `blocks` (DM-CM, AM-Winger, ST), `interceptions` (DM-CM, AM-Winger).
  - Not included: `tackles_won_p90` (D17-dropped everywhere); ratios (team share not defined).
  - **The team count includes the player's own actions** (share = player / team). The actor is always credited, so team ≥ own on every row and the share is ≤ 1.
- **Crediting:** the D12 rule (window half-open, closed at the period end, the event's own period, 1e-9, clamped to the whistle; team key `team.id`, a mismatch with the lineup fails the build; periods 1–4).
  - **One shared rule (user decision 1):** `metrics/defensive_actions.py` `defensive_action(event)` decides the 8 actions for both `_aggregate_events` and the team crediting. The all-columns-identical check proves the refactor changes nothing.
  - The opponent column is computed in the `team_open_play_passes_while_on` loop, so the two share the open-play filter.
- **Counts only (user decision 2):** E per 90 and the shares are derived in M4g4c; any stored derived column waits for M4g4d.
- **Checks:**
  - every existing column byte-identical: `spikes/m4b_before_after.py --snapshot data/interim/m4g4b_snapshot --all-columns-identical --expected spikes/expected_m4g4b_flips.json` (empty, written before the rebuild); `--force` rebuild;
  - player-by-player: the opponent column equals the M4g4a recount (0 diffs); the 8 team columns equal the extended recount (0 diffs); team ≥ own;
  - reference totals: `verify_core_metrics.py` adds the 9 league totals to `core` with `CHANGED_BY_CURRENT_CHUNK = frozenset()` (every recorded value must reproduce); `test_core_metrics_reconcile` then checks season = reference = match sums.
- **Downstream:** the measurements pin both parquet shas and the season columns, so `--write-json` re-runs. STOP rule: measurements identical except `provenance`; movers identical; registry identical except `REGISTRY_VERSION`. Percentiles and DNA change in their stamps only; reference checks; golden pages and the M4g1 section (version line).
- **Reviews:** code-reviewer; qa-tester (src/); integration tests; stats-reviewer output-only (user decision 3).
- **Wording notes folded in (code-reviewer, M4g4a fix delta):** see the M4g4d note in the M4g4a outcome.
- **Checkpoint 1 (2026-10-08, `spikes/m4g_opponent_exposure.py` extended; before any pipeline code):**
  - **The written-out action rules reproduce the pipeline's player counts** on all 43,044 player-match rows for all 8 actions (0 diffs); team ≥ own on every row; no actor lacks a match row.
  - **Credited players per event (actor + teammates on), all ≤ 11 and ≥ 8:** 8 occurs once (an open-play pass, the Frosinone case from M4g4a); every action is ≥ 9.

    | Kind | Events | 11 | 10 | 9 | 8 |
    |---|---|---|---|---|---|
    | open-play pass | 1,346,205 | 1,305,911 | 38,983 | 1,310 | 1 |
    | pressures | 503,490 | 483,974 | 18,838 | 678 | 0 |
    | ball recoveries | 154,311 | 148,526 | 5,632 | 153 | 0 |
    | clearances | 74,650 | 71,373 | 3,184 | 93 | 0 |
    | tackles attempted | 61,610 | 59,228 | 2,298 | 84 | 0 |
    | blocks | 59,418 | 56,903 | 2,416 | 99 | 0 |
    | fouls committed | 46,659 | 44,947 | 1,658 | 54 | 0 |
    | interceptions | 38,321 | 36,866 | 1,425 | 30 | 0 |
    | dribbled past | 32,712 | 31,336 | 1,313 | 63 | 0 |

  - **Actor outside his own window (credited anyway): 450 events.**
    - 446 are actions (fouls committed 354, pressures 81, blocks 7, tackles 2, dribbled past 1, ball recoveries 1; clearances and interceptions 0), plus 4 open-play passes.
    - **447 are at the same instant as the actor's own dismissal**: the card ends his credited time there, and the window is half-open. They include all 354 fouls that carry their own red or second-yellow card, and actions at that instant (e.g. Mitrović's pressure at 15:27, 3754205).
    - **3 are one treatment-window edge:** Milinković-Savić, 3879637, period 1, 45:25–45:30. His Player On is at 45:16, but the §31 timeline resumes him only at the period 2 start. The recount and the pipeline agree, so this is existing §31 behaviour, recorded for a separate look; M4g4b does not change it.
    - **No credit count reaches 12:** among actor-outside events at most 10 teammates are on (a dismissed or treated player's team has at most 10 others on), so the count is ≤ 11.
  - **Rule for the 7–11 check: decision C (user, 2026-10-08, after the first proposal met a tested §31 pattern).**
    - **Credits are unchanged:** the actor is always credited, and every teammate whose window holds the instant.
    - **The count** the 7–11 check reads is the credited set with the actor once, except that **an actor at his own exit instant is not counted as an extra player**: his own window does not hold the instant (half-open), but ADR 002 §31 `covers()` does (closed).
    - **Substitution-instant example:** P0 is replaced at 60:00 and commits a foul (second yellow) at 60:00; P11 is on from 60:00. Credited: P0 + 10 teammates + P11 = 12 players; count 11 (P0 at his exit instant). Passes. This is the tested §31 pattern `test_substitution_and_second_yellow_same_second_is_not_an_excess`, which the first proposal (actor once, 12 fails) would have broken.
    - **Dismissal-instant example:** a foul carrying its own red card at 60:00. Credited: the actor + 10 teammates = 11; count 10. Passes. 447 of the 450 actor-outside events in 2015/16 are of this kind (all 354 fouls carrying their own card, plus actions at the same instant).
    - **Strictly after the exit:** an event by P0 at 61:00 with 11 others on counts 12 and fails the build (`team_action_credit_out_of_range`). A real timeline inconsistency stays loud. Below 7 also fails.
    - **Applied to the existing team-pass crediting too** (the same latent case: a pass at the passer's own substitution instant). It changes no 2015/16 value; the all-columns-identical check must show no change.
    - **Implementation:** one helper, `build_player_match._players_for_bounds`, read by both `team_open_play_pass_credits` and `team_action_credits`; `verify_core_metrics.py` applies the same rule independently.
    - The opponent column has no actor; its count is the defenders on, with its own named bounds (`MIN_DEFENDERS_PER_PASS` / `MAX_DEFENDERS_PER_PASS` = 7 / 11).
- **Checkpoint 2 (2026-10-08): the pipeline columns.**
  - `--force` rebuild of all 5 competition-seasons and the season stage: no data-quality failure, so every 7–11 check (team passes with rule C, team actions, defenders) holds on real data.
  - **All columns identical** (`m4b_before_after.py --all-columns-identical`): 79 match-level and 145 season-level columns identical; 0 flag flips; rows unchanged (43,044 / 2,628); the 9 new columns are the only additions.
  - **Player by player:** the opponent column and the 8 team-action columns equal the `m4g_opponent_exposure.py` recount on all 43,044 rows (0 diffs each); rows with no minutes are 0; team ≥ own on every row.
  - **Reference totals:** `verify_core_metrics.py` passes in all 5 leagues with `CHANGED_BY_CURRENT_CHUNK` empty (every recorded value reproduced); the 9 totals are new keys, and their sums equal the pipeline's (opponent passes 14,737,650; ball recoveries 1,691,483). The reconcile tests (season = reference = match) pass.
- **Downstream (2026-10-08):** `--write-json` re-run; STOP rule `spikes/m4g4b_before_after.py` passed: only `provenance` changed (the two parquet shas, plus the 9 season columns), movers and every other section identical, 0 registry changes. `REGISTRY_VERSION` 86b6389f389f… → 463d1a4c31ae…. Percentiles and DNA changed in their stamps only (`registry_version`, `season_parquet_sha256`, `percentiles_parquet_sha256`); both reference checks pass; the golden pages (registry version and season sha, one line each) and the M4g1 validation section (version line) were refreshed.
- **Stats-reviewer, output only (M4g4b `/finish-chunk`): CONCERNS, both fixed in the one fix round** (2 integration tests failed on stale `season_parquet_sha256` pins in the m4d / m4f reference fixtures, refreshed by re-running both reference spikes; the checkpoint 1 "≥ 9" wording, now ≥ 8). The data passed every check it ran, including: in all 3,102 team-matches the largest `team_<action>_while_on` equals the team's summed own actions for all 8 actions, and the largest opponent count equals the opponent's largest team-pass count; credited ÷ events is 10.95–10.96 everywhere.
  - **Notes for M4g4c (no action in M4g4b):** low-minute rows reach extreme values (opponent passes per 90 up to 1,568 at 0.52 minutes; shares of 1.0 at 5–42 minutes), so any share or E per 90 shown outside the gated pool needs the same gate or a minimum count. La Liga records 16.3 interceptions per team-match against 10.2–11.7 elsewhere, so pooled β̂ and team fits must stay league-demeaned (as D2 requires). The two recounts (`m4g_opponent_exposure`, `verify_core_metrics`) share the §31 timelines: independent of the pipeline, not of each other.
  - **Synthetic test fixtures (user, 2026-10-08):** opt-in per test, never autouse. `synthetic_one_team` relaxes the defenders minimum for one-team lineups; `synthetic_small_teams` also relaxes it (never raising a lower value). A default-path test asserts the 7 / 11 bounds for team actions and defenders, so the relaxation cannot leak into a real run. No expected value in any existing test changed.

### M4g4c — Candidates, criteria and movers (plan approved by the user, 2026-10-08)
- **Plan decisions (user, 2026-10-08, before any result):** G1 every selection quantity on the 1,233 gated rows; G2 (stop, not filter) opponent open-play passes ≥ 1,000 and each Defending DNA action's team count ≥ 20 on every gated row; G3 (halves) β half ≥ 500 opponent passes, share half ≥ 10 team actions; everything within league or league-demeaned; criterion (3) on the M4g2 split stream at the committed K (3,200); the residual §31 timeline risk recorded (G5).
- **Stops (user):** (1) the selection table, (2) the mover verdicts, (3) the expected-flips file; each approved on 2026-10-08.
- **Outcome** (`docs/reviews/m4g4c-possession.md`; ADR 004 §13.8):
  - the engine (`spikes/m4g4_possession.py`) and the independent reference (`spikes/m4g4_possession_reference.py`) agree on every field within 1e-9 (power within 4 combined SEs); β = 0 equals the committed measurements (ρ, r_full, D9, `pct_local`);
  - G2 never stops (minimums 3,569 opponent passes, 61 team actions); G3 excludes no `ball_recoveries_p90` mover under any candidate (n = 24);
  - `ball_recoveries_p90`: DM-CM selects β = 0; CB, FB-WB, AM-Winger and ST select none (criterion (2) binds). Read as "not shown to be possession-free": the null calibration fails criterion (2) in 43–70% of no-effect shuffles for these groups;
  - the three adjusted mover runs are inconclusive on all six streams (report only: no group selected them);
  - expected (`spikes/expected_m4g4_flips.json`): Defending withheld in all 5 groups (4 × `possession_criteria_failed`, DM-CM `mover_stability_inconclusive`); 7 style cells enter the vector adjusted; C4 "not tested"; 0 changes to existing cells;
  - **deviation:** the 12-significant-digit tie rule for β-adjusted values (ADR 004 §13.8);
  - added to the M4 sign-off list: "accept adjustments with |β̂| > 1?" (AM-Winger clearances, dribbled_past; with the low-count sensitivity 2.23 → 1.10 / 1.08) and "accept the 7 adjusted cells given criterion (2)'s ~50% null failure rate and their large per-player moves?";
  - `/finish-chunk`: stats-reviewer (output only) CONCERNS → fixed in the one fix round (wording, null calibration spike, sensitivity, power label); code-reviewer PASS with should-fix items, fixed.
- **Backlog in this chunk:** M4g4b leftovers #4 (explicit sha → path map) and #6 (`measurements_sha256` checked in the m4d / m4f fixture tests) done; #1 read-only half done (every lineup player has a timeline by construction; 0 duplicate player IDs in 1,551 matches; the 15,839 lineup players without a player-match row are all unused substitutes with no positions). #1's code change, #2, #3 and #5 stay gated before M4g4d's registry write.

### M4g4c2 — Trust check on the 7 adjusted cells (user 2026-10-08; post-hoc motivated, specified before any new number)
- **Spec:** ADR 004 §13.8 "M4g4c2 trust check" (checks (a)–(e), thresholds, streams, prediction); stats-reviewer design review APPROVE WITH CONDITIONS, C1–C8 folded in; user decisions on the conditions: C4 adopted ((c) judges each criterion β = 0 failed), strict "same candidate" kept for (a).
- **Files:** `spikes/m4g4c2_trust_check.py` (engine, reuses `m4g4_possession`) → `data/interim/m4g4c2/trust.json`; `spikes/m4g4c2_trust_reference.py` (independent; `--check-output`, then `--write-expected` updates `spikes/expected_m4g4_flips.json`); `tests/test_m4g4c2_spikes.py`.
- **Stops (user):** (1) the results table (7 cells × (a)–(e), pass / fail, against the prediction); (2) the updated expected-flips file. Then `/finish-chunk` (code-reviewer; qa-tester skipped, spikes only; stats-reviewer output only).
- **Recorded (user):** (c) is near-circular for E-based candidates, so (a) and (b) carry the weight.
- **Outcome (stop 1 approved, 2026-10-08): all 7 cells stay raw (β = 0) in M4g4d** (`docs/reviews/m4g4c-possession.md`, M4g4c2 section; ADR 004 §13.8). AM-Winger pressures fails on (a) alone; 5 cells fail (b) independently of (a)'s bias. Expected file rewritten by the reference: no vector column adjusted; Defending statuses unchanged; C4 "not tested". M4g4d's write reduces to the D5 statuses, per-cell `possession_adjustment` records (candidate, criteria, trust check) and measurements `schema_version` 3.
- **`/finish-chunk`:** stats-reviewer (output only) CONCERNS, documentation only, fixed in the one round (Ligue 1 statement corrected; (d) excluded-team counts recorded, 0 for two of three cells; "n/a" for a check that does not apply in the expected file; (c) wording; "fails (a)" = not shown stable). Code-reviewer PASS (should-fix items done: direct `c3_pass` indexing, idempotent expected-file note, an engine-vs-reference permutation test, non-vacuous selection tests); fix delta PASS. Nothing left for the backlog.

### M4g4d — Registry statuses, possession records, measurements schema 3 (plan approved by the user, 2026-10-09)
- **Scope after M4g4c2:** the D5 Defending statuses, per-cell `possession_adjustment` records, measurements `schema_version` 3, and the gated M4g4b backlog (#1 code, #2, #3, #5) as step 0. No vector column changes. Standing approval; stops only on a STOP-rule failure, an unexpected change, or the commit proposal.
- **User decision (plan):** the single writer embeds the M4g4c / M4g4c2 engine outputs, with pins, instead of recomputing them.
- **Step 0 (backlog gate):** `_require_timelines` raises for a lineup player without a timeline (no skips); `_lineup_pass_team` is the one team rule of both pass credits; tests for the outside-the-lineups branches and the mismatch dependency; merged `usefixtures`. `build_player_match --force` → both parquets and all 5 parts byte-identical.
- **Outcome:** ADR 004 §13.8 "M4g4d outcome".
  - Defending: CB, FB-WB, AM-Winger and ST are `possession_criteria_failed`; DM-CM is `mover_stability_inconclusive` (underpowered at 24).
  - 0 adjusted columns.
  - The STOP rule passed. `REGISTRY_VERSION` 463d1a4c31ae… → 91c24b62e53c….
  - Dimensions changed in the Defending `score_status` only; vectors and compare pairs are identical.
- **Deviation from the plan text:** the adjusted mover runs are stored under `possession.movers`, not `movers.candidates`, so the M4g3b `movers` block stays byte-identical.
- **`/finish-chunk`:** fast 2,168 and integration 137 pass. qa-tester PASS (1 snapshot test added). stats-reviewer PASS, output only (method reviewed 2026-10-08; notes N1–N3 recorded on the M4 sign-off list and in plan 006 W4). code-reviewer PASS with 6 should-fix items, all applied in the one fix round:
  - `s2_rms_a` is required on a testable mover entry, and a below-model-range PASS without it raises;
  - possession records are read through `_at`, which names the cell and the missing key;
  - a second directional Defending member without candidate mover runs is refused;
  - `withheld_for` with a custom table needs an explicit derivation;
  - `_players_for_bounds` documents its `_require_timelines` precondition;
  - a test that a restored member beats a withheld one.
  The fix delta passed review; fast 2,176 pass; `REGISTRY_VERSION` unchanged.

### M4g5 — xPass (spike, then pipeline): DEFERRED until after web v1 (user, 2026-10-09; ADR 004 §13.9)
- **Decision:** deferred until after web v1 launches. It returns as the first post-launch feature, built primarily as a display metric.
- **On return, step 1 is the go / no-go spike:** execution's within-league possession ρ (both proxies) and multi-split `r_full` per group, before any pipeline work.
- **Why:** `docs/reviews/m4g5-feasibility.md`.
  - About a 20–25% chance that execution restores any Passing family on 2015/16 data (AM-Winger at best), and about 5–10% in CB, FB-WB and DM-CM.
  - D12 and the pooled mover rule bind; a second season roughly doubles the mover power.
- **Passing status:** stays `pending_mover_stability`, which from now on means "xPass deferred, not tested". It is not a failure code. The rename or rewording of the status and its display text is an M4g6 sign-off item.
- The spec below stays pre-registered, unchanged, for the return.

- **Passes:**
  - open play only (`pass.type.name` ∉ `RESTART_TYPES`), periods 1–4;
  - completed = null outcome;
  - "Pass Offside" is incomplete;
  - "Injury Clearance" and "Unknown" are dropped;
  - every configured competition-season of the source.
- **Features (the stats-reviewer's leakage decision, M4g0: option ii):**
  - start x and y;
  - direction as sin and cos of `pass.angle`;
  - height (ground / low / high);
  - body part (foot / head / other);
  - `under_pressure`;
  - through ball (`pass.technique` = Through Ball, decided at the kick: blocked passes carry it 0.60% of the time, others 0.49%).

  **Excluded:** end x and y, `length`, and the cross and switch flags. All four come from the end location. The switch flag is set exactly when |Δy| ≥ 40; 98.1% of crosses end at x ≥ 102.
- **Model:**
  - logistic regression on spline terms (`SplineTransformer` + `LogisticRegression`, scikit-learn);
  - cross-fitted with 5 folds by match, stratified by league so every league appears in every fold (matches assigned in id order within each league), so every pass gets an out-of-fold xP;
  - a more flexible model only if calibration fails, and only with the reason recorded.
- **Model acceptance (stats-reviewer, M4g0):**
  - on out-of-fold predictions, the mean (observed − xP) is within ±0.01 overall and in every criterion subset:
    - height (3);
    - body part;
    - start third;
    - `under_pressure`;
  - the maximum decile calibration gap is ≤ 0.03;
  - each league and each position group: report only. A constant offset there is removed by local, within-group percentiles, and the residual mean of a group is the skill signal itself.
  - **"Long passes" is not a subset:** recorded length depends on the outcome, so a leak-free model fails it by construction (start-only: −0.0247).
  - AUC, Brier and the calibration curves are reported. One player's execution SD is about 2.6–3.2 per 100 passes, so ±0.01 is tight enough.
- **Metrics (stored as counts):**
  - `op_passes_xp_n` (passes), `op_passes_xp_completed` and `op_passes_xp_sum`, the same three for `under_pressure`;
  - execution = 100 · (completed − Σ xP) / n;
  - execution under pressure, the same on pressured passes only, with its own `min_attempts`;
  - ambition = 1 − Σ xP / n (`style`).
  - `min_attempts` by variance components (R1, as `np_xg_per_shot`).
- **End-location leakage: decided by the stats-reviewer (M4g0), option (ii) above.** Measured on 1,346,205 open-play passes, using diagnostic gradient-boosted models, not the final model:
  - **What the end location is:** for a failed pass, where the ball was stopped. 83–86% sit within 2 units of the opponent's first linked event.
  - **What leaks:** `length` and `angle` are computed from it (|length − distance| ≤ 8e-6), so an "intended length" (option i) does not exist.
  - **Blocked passes:** median length 3.0–4.2; 14.8% of incompletes.
  - **Passes out of play:** median length 34.3; 78.7% end on a touchline or goal line.
  - **Mean xP, full model vs start-only:**

    | Passes | Full | Start-only |
    |---|---|---|
    | Blocked | 0.263 | 0.530 |
    | Out | 0.144 | 0.520 |
    | Intercepted | 0.680 | 0.690 |

    The full model excuses exactly the failures the passer is responsible for.
  - **AUC:** 0.912 full vs 0.874 start-only.
  - **Effect on players:** execution ρ between the two models is 0.956 (0.915–0.965 by group). The difference correlates 0.546 with a player's out-of-play share and 0.435 with his blocked share.
  - **Sensitivity run (iii), report only, never selectable:**
    - the full model, adding end x/y, length, cross and switch;
    - per group, Spearman ρ of execution and of ambition between the two models;
    - per group, ρ of the execution difference with the blocked share and the out share.
  - **Known cost:** without length, execution partly rewards safe choices from the same spot. ρ(execution, `pass_completion_pct`) is 0.63–0.77 start-only vs 0.51–0.72 full. ρ(execution, ambition) is reported per group.
  - **Predictions recorded before results (user, 2026-10-07: ambition stays a candidate and the rules decide):**
    - execution may trigger D12, because `pass_completion_pct`'s possession ρ is 0.57–0.69;
    - ambition ≈ 1 − completion (exactly, completion = execution / 100 + 1 − ambition). Its ρ with `pass_completion_pct` is −0.89 to −0.94 within groups, so it is expected to fail D17 against ST's DNA `pass_completion_pct` (0.85), and to trigger D12.
- **Pipeline:**
  - a new stage `pipeline/build_xpass.py` (raw events → `data/interim/pass_xp.parquet`: one row per pass with its fold and xP);
  - `build_player_season` joins the per-stint sums;
  - the fit is deterministic (fixed folds, `lbfgs`, `OMP_NUM_THREADS=1`, xP rounded to 1e-9), and two builds are byte-identical.
  - The model needs every match before it can predict any, so it cannot sit inside the per-match build.
- **Evaluation:** the pre-registered list in the M4g item list (D19 multi-split, `min_attempts`, the zero-heavy rule not applicable to execution, D17 between the two execution metrics, D12 and the possession criteria, D9, mover stability with the Koné illustration).
- **Then the user decides:** whether to restore Passing, and the role of `long_pass_completion_pct`.

### Decision: family weighting when a family has one member (required for M4 sign-off)
- **Open** (stats-reviewer, M4g2 `/finish-chunk`; user 2026-10-07). M4 is not signed off until the user decides it.
- **The problem:**
  - Under the §10.8 family weights, a family reduced to one metric puts its whole family weight on that metric.
  - After M4g2: CB Aerial (`aerials_won`, 0.20); FB-WB Aerial, Goal threat and Creation (43% of the vector on 3 metrics); ST Goal threat (`np_shots`, 0.167).
  - Effective metrics fall by about 3 in CB, FB-WB and ST, and the neighbours depend more on the weighting (FB-WB rows with ≥ 7 of 10 shared: 59% → 37%).
  - ST Goal threat becomes shot volume only (Kane 90 vs Vardy 68), from the same cause.
- **Options:** accept it, cap a single-member family's weight, or merge families.
- **On the M4 sign-off list (stats-reviewer, M4g4d output review; user 2026-10-09): N1–N3.** Display rules in plan 006 W4.
  - **N1:** "rises with own possession" and "|β̂| > 1" are point-estimate flags. Only CB `ball_recoveries_p90` has a β̂ CI that excludes 0 (−0.71 [−1.00, −0.45]); five other β̂ < 0 cells straddle 0. "|β̂| > 1" is also set on cells that selected nothing (AM-Winger `interceptions_p90` 1.21) or did not select β̂ (AM-Winger `tackles_attempted_p90` 1.07 [0.45, 1.72]). Any display shows the CI next to the flag.
  - **N2:** AM-Winger `possession_criteria_failed` rests on near misses (β = 0 |ρ| 0.127 / 0.141 against 0.1; β̂ gap 10.92 against 10), against criterion (2)'s ~57% null failure rate. The wording stays "not shown to be free of team-possession effects", never "possession-dependent". CB is the only group with clear evidence (β = 0 ρ +0.25 / +0.28, gaps 15–22).
  - **N3:** the DM-CM restore is effectively unreachable in 2015/16 (power at f = 0 is 0.639 at n = 24; requiring all six streams costs 3–4 more points). `mover_stability_inconclusive` is the expected state until a second season adds movers, not a negative finding about DM-CM defending.
- **Also on the M4 sign-off list:** the 8 majority-admitted allow-listed cells (M4g4 decides nothing for them; M4g4 plan), and any FAIL on a current DNA metric (§13.7).
- **Resolved by M4g4c2 (all 7 adjusted cells stay raw, 2026-10-08), kept for the record — on the M4 sign-off list (user, 2026-10-08, M4g4c stop 1): "accept adjustments with |β̂| > 1?"** AM-Winger `clearances_p90` (β̂ 2.23, bootstrap CI 0.69–3.66) and `dribbled_past_p90` (β̂ 1.34) are selected with |β̂| > 1 (ADR 004 §13.8). Sensitivity (stats-reviewer): the 2.23 rests on three AM-Winger team groups with 3, 5 and 9 clearances; without teams below 10 / 20 group clearances it is 1.10 / 1.08. The pre-registered fit is kept; a group-count gate in the fit is decided here or in a later ADR.
- **Resolved by M4g4c2 (all 7 stay raw), kept for the record — on the M4 sign-off list (user, 2026-10-08, M4g4c `/finish-chunk`): "accept the 7 adjusted cells given criterion (2)'s ~50% null failure rate and their large per-player moves?"** Evidence: the null calibration (`spikes/m4g4c_null_calibration.py`; criterion (2) fails in 27–83% of no-effect shuffles, mean 57%), 5 of 7 passing near a bar, and raw → adjusted percentile moves (median 4–7.5 points, 90th percentile 13–20) in `docs/reviews/m4g4c-possession.md`.
- **Evidence:** ADR 004 §13.6 ("Stats-reviewer outcome").
- **Timing:** decide after M4g4/M4g5, which may add members (Defending, Passing) and change the picture, and before M4g6. **Updated 2026-10-09:** M4g4 added no member and M4g5 is deferred, so the decision is taken in the M4g6 sign-off.

### M4g6 — Validation write-up (closes M4)
`spikes/m4_validation.py` → `docs/reviews/m4-validation.md`:
- threshold sensitivity;
- reliability per metric (multi-split) and the minute buckets;
- correlation and dominance per dimension;
- the outlier audit (the 243 D20 rows above P99 traced to events);
- multi-position players and club changers;
- mixed-role |z| > 5 with the main-group minute share;
- about 20 players' neighbours on both D10 bases;
- the teammate count after M4g4;
- the M4g1–M4g5 outcomes.

**Requirement (mover verdicts):** a displayed mover verdict always shows its flags next to it: "underpowered at n", "borderline (seed-sensitive)", "PASS (below model range)". Never a bare "PASS" (user, 2026-10-08, from the M4g3b output review: 4 of the 9 PASS verdicts are underpowered, and `op_xg_assisted_p90` is also below model range). This applies to the write-up, the M4 sign-off text and every mover table.

The stats-reviewer signs it off; any failure goes back to its decision.

### M4g6 plan (approved by the user, 2026-10-09)
M4g4 is done and M4g5 is deferred (ADR 004 §13.9), so M4g6 closes M4. Each write-up item answers a brief §20 question. Plan 006 starts only after the sign-off.
- **M4g6a — validation spike:** the evidence, spec below; the stats-reviewer reviews the spec before any code.
- **M4g6b — sign-off decisions document** (`docs/reviews/m4-signoff.md`). Each item gives evidence, options and a recommendation; **stop for the user's decisions**. The items:
  1. family weighting for one-member families;
  2. the 8 majority-admitted allow-listed cells (incl. the 2 `long_pass_completion_pct` cells, decided without M4g5);
  3. the FAIL list (today 0 current DNA metrics);
  4. the Passing status name and the stale "withheld until M4g" display text;
  5. N1–N3 confirmed as accepted display rules;
  6. items out of M4 scope, to confirm (log-OLS vs Poisson β̂ and a group-count gate, the §31 re-entry check, the second-season levers, the M5 z handling C1/C2);
  7. backlog triage (what must close before web v1 or M5).
- **M4g6c — apply decisions that change code** (only if any do): expected file before code, a before/after STOP rule, rebuild, frame comparisons, references, golden pages, M4f3 re-run if the vector changes, full `/finish-chunk`.
- **M4g6d — close M4:** ADR 004 §14 "M4 sign-off"; the stats-reviewer signs off the write-up; M4 status in `milestones.md` and here; milestone report. Merging `m4-player-dna` into `main` locally needs separate user approval; the user pushes.
- **Stops:**
  1. the spec review;
  2. the M4g6a output and commit;
  3. the M4g6b decisions;
  4. the M4g6c commit, if any;
  5. the M4 report and the merge question.

### M4g6a spec (for the stats-reviewer's pre-code review)
`spikes/m4_validation.py` writes its own marked section (`review_sections.write_section`, owner `m4_validation`) of `docs/reviews/m4-validation.md`. It is report only, never imports eleveniq and decides nothing.

**Inputs:**
- the processed parquets (season, percentiles, DNA, dimensions);
- the committed measurements;
- a registry snapshot (`tools/registry_snapshot.py`);
- the raw events (item 4 only).

**Shared definitions:**
- "Primary scored rows": `cohort_role = "primary"`, `percentile_status = "scored"`; 1,233 in 2015/16.
- DNA lists, families, pair weights and the pair distance come from `spikes/m4f_dna_reference.py`.
- Neighbours (M4f3 definition): same group, primary scored rows, any pool league, excluding the same `player_id`; k = 10; ties broken by the season key.

Items:
1. **Minutes threshold sensitivity (gates 720 and 1,080 nominal-equivalent minutes; user 2026-10-09).**
   - **Method:** the gate is `minutes_nominal_equiv ≥ G − 1e-6`, and every other rule stays as in ADR 004 §11. `m4d_percentiles_reference` gains a `gate` parameter (default 900, its output unchanged).
   - **Ratios:** `min_attempts` stays at the values measured at 900, and that is stated in the section.
   - **Per gate, reported:**
     - (a) cohort sizes per league × group;
     - (b) per DNA metric × group, the Spearman ρ between the 900 and the G local percentiles, on rows scored at both gates (G = 720: the 900 rows; G = 1,080: the 1,080 rows);
     - (c) the share of rows in a cell's local top 10% at 900 that are still in the top 10% at G, among rows scored at both;
     - (d) the top-10 neighbour overlap at 900 vs G for the item-7 sample.
   - The lowest-ρ cells and the largest drops are listed.
2. **Reliability:** a table from the committed measurements (multi-split `r_full` per DNA cell and the minute-bucket summary). No recompute.
3. **Correlation and dominance (the family-weighting evidence):**
   - re-run `spikes/m4f_weighting_checks.py` on the current build. It regenerates `docs/reviews/m4f-weighting.md`, which is stale (registry 930d0019ee11, before M4g2). That gives family shares of the squared distance against nominal weights and the noise baseline, and the family vs equal-metric top-10 overlap;
   - add per DNA metric × group the metric's share of the squared distance over all within-group pairs (the same decomposition, per metric), against its nominal weight;
   - add the Spearman matrix of the group's DNA local percentiles, listing every pair with |ρ| ≥ 0.7 (D17 drops at 0.85);
   - add, for each scored (non-withheld) dimension, each directional member's Spearman ρ with its dimension score.
   - If the face-validity lists change from M4f3, the user's recorded read is marked "to be re-read on the current build" and not kept as current.
4. **Outlier audit (D20):**
   - **Which rows:** gated rows strictly above their group's P99 (numpy linear quantile over the group's gated rows, all leagues) on at least one DNA metric of the group. The count over all per-90 and ratio metrics is reported too, next to D20's 243 (an M4a figure, before M4b).
   - **Trace for every DNA cell above P99:** recount the season count from the raw events of that player-team-competition with the independent definitions in `spikes/verify_core_metrics.py` (applied to that player's events only), and compare with the parquet count and minutes. Exact equality is expected.
   - A metric with no independent definition there is listed as "not traced" with its count.
   - Also the 20 largest |z| rows, with the main-group minute share.
5. **Multi-position players and club changers:**
   - D5 rows: per group, the count of `position_group_ambiguous` rows, and for the second-group rows the |primary − second| local percentile difference per DNA metric they share (median, max).
   - Club changers (a player with two gated primary rows in the same group): the DNA distance between his two stints and its percentile within the group's distribution of distances to the 10th neighbour, plus whether each stint is in the other's top 10.
6. **Mixed-role |z| > 5:** comes from item 3's re-run (C1 table with the main-group share); the section reports the count and the share below 0.70 against the other rows.
7. **Neighbours on both bases.**
   - **Sample (rule fixed before computing, user 2026-10-09), 21 players:**
     - the 9 M4f3 face-validity players (Kanté, Busquets, Isco, Özil, Payet, Messi, Neymar, Stones, Morgan);
     - per group, the 2 primary scored rows with the most `minutes_played` not already picked;
     - the 2 same-group movers (the M4g3 list) with the most combined minutes not already picked.
   - **Bases:** (a) all local, the build; (b) "global where defined": `pct_global` where `status_global = "ok"`, else `pct_local`.
   - **Reported:** the top-10 overlap per player, the median overlap, and both lists for the 9 face-validity players.
8. **Teammate count after M4g4:**
   - comes from item 3's re-run (same-team neighbours, observed vs expected), next to the M4f3 figure;
   - the DNA vector is unchanged since M4g2 apart from stamps, which is asserted (the M4g4d frame comparison).
9. **M4g1–M4g5 outcomes:**
   - a summary linking the reviews and ADR sections;
   - every mover verdict shown with its flags ("underpowered at n", "borderline (seed-sensitive)", "PASS (below model range)"), never a bare PASS;
   - M4g5 reads "deferred (ADR 004 §13.9)".

**Tests:** synthetic tests for the new helpers (gate parameter, per-metric share decomposition summing to the distance, P99 selection, sample rule, overlap).

**`/finish-chunk`:**
- code-reviewer;
- qa-tester skipped: spikes and tests only (the `m4d` reference change is a spike);
- the stats-reviewer on the output.

**Stats-reviewer pre-code review (2026-10-09): APPROVE WITH CONDITIONS.** Conditions 1–12 are folded in below (user, 2026-10-09). They replace the matching parts of the spec above.
- **User decisions (fixed before any run):**
  - **Weighting variants for M4g6a2:** (i) the build, family weights; (ii) the half-weight cap: a one-member family gets 0.5/|F|, and the freed weight is split equally over the group's other families; (iii) the merge: Goal threat + Creation become "Attacking output" in every group where either has one member. CB Aerial is never merged (cap only).
  - **Split-half self-retrieval (condition 13) is deferred to the M5 plan.** The M4 sign-off decides weighting on the noise-share and leave-one-out evidence and marks the decision provisional until M5.
  - **Split:**
    - **M4g6a1:** items 1, 2, 4, 5, 7, 9 and the brief §20 map;
    - **M4g6a2:** items 3, 6, 8 and the weighting variants.
    Each gets its own `/finish-chunk` and commit.
- **Item 1 (conditions 1, 2, 3, 12):**
  - (b) is replaced: on rows scored at both gates, Spearman is 1 by construction (both percentiles are monotone in the same value, and the populations are nested). Instead, per DNA cell, the |pct_900 − pct_G| distribution (median, P95, max), and per group the distribution of each row's median |Δd| over its common partners.
  - **Rule-faithful outcome:** rows that lose a score at G (e.g. PL ST is 29 rows at 1,080, below the cohort floor), per league × group, and every row that drops below the gate (incl. the mover stints of Shelvey, Khazri and Perotti at 1,080).
  - **(b), (c) and (d) also run with the cohort floor waived,** with the waived cells flagged.
  - **Held at the 900 registry:** the DNA lists, families, weights, zero-heavy exclusions, `min_attempts` and D9. Local percentiles only; global / D9 is not recomputed.
  - **The m4d reference `gate` parameter replaces only the row-status test.** The guard still checks the stored `meets_min_minutes` against 900.
  - **(c):** "top 10%" is `pct_local` ≥ 90, reported as k of n.
  - **(d) on all rows** (median and P10 overlap per group):
    - at 720: the overlap with candidates restricted to rows scored at both gates (percentile shifts), and the share of new neighbours from the 720–900 band (new candidates);
    - at 1,080: the share of 900 neighbours lost because they fall below the gate.
- **Item 4 (conditions 5, 6, recommendation 11):**
  - **Rows:** P99 for a ratio is taken over its eligible rows only (≥ `min_attempts`).
  - **Recount:** every DNA metric is recounted per player from raw events (periods 1–4). The independent definitions are written out from `verify_core_metrics.py` (`count_events`), `verify_chunk_a_metrics.py` (aerials, pressures, dribbled past, ball recoveries, long passes) and `verify_chunk_b_metrics.py` (final-third and box entries). For a ratio, both numerator and denominator are recounted.
    - Exception, justified: `team_pass_share`'s denominator (`team_open_play_passes_while_on`) is credit-based; its league total is verified by `verify_core_metrics`, so only its numerator is recounted per row.
    - Minutes are verified at league level by the same spike, so they are not re-derived per row.
  - **Per outlier cell:**
    - the top match's share of the season count, and the value without that match (still above P99?);
    - the 900–1,350 bucket flag;
    - the main-group share;
    - duplicate events of that player (same match, timestamp, type).
  - **Classification, first match wins:** coding error (recount ≠) → data anomaly (duplicates) → single-match spike (below P99 without the top match) → mixed role (share < 0.70) → near-gate (900–1,350) → genuine.
  - **Lower tail:** P1 rows for `passes_attempted_p90`, `pressures_p90` and `ball_recoveries_p90`, and player-matches with ≥ 45 minutes and no on-ball event.
- **Item 5 (condition 7):**
  - "In the other's top 10" becomes the rank of the other stint among all same-group rows, with that stint admitted.
  - Benchmarks: the 1st and 10th neighbour distances.
  - n = 5, described without inference.
  - Plus the D2/D3 counts: multi-stint players with no stint ≥ 900 nominal, and those whose combined minutes would pass (no profile).
- **Item 7 (condition 8):**
  - every scored stint of a sampled mover is included, and the row count is stated;
  - both lists, on both bases, for all 21 players;
  - the same-league neighbour share on each basis (all rows, observed vs expected).
- **Write-up (condition 8d):** a brief §20 map, all 14 questions, each pointing to an item or an ADR section:
  - "cosine similarity" is deferred to M5 (M4's distance is weighted squared Euclidean);
  - Gem stability and rare combinations are deferred to M6;
  - totals reconciliation points to the goals + own goals check in the reference spikes.
- **Item 3 for M4g6a2 (conditions 4, 9, 10):**
  - per-metric shares over neighbour pairs against the noise baseline (all-pairs as a check only);
  - leave-one-family-out top-10 overlap, plus leave-one-metric-out for the one-member families and the 8 majority-admitted cells;
  - the three variants: effective metrics 1/Σw², overlap with the build, teammate ratio, same-league share, face lists, and the expected noise share Σ w·(1 − r_full);
  - item–rest correlations (one-member families "not assessable");
  - |ρ| ≥ 0.7 pairs tagged within-family or cross-family.

### M4g6a1 — outcome (2026-10-09)
- **Files:**
  - `spikes/m4_validation.py` → the `m4_validation` section of `docs/reviews/m4-validation.md`, figures in `data/interim/m4g6a1/validation.json`;
  - `spikes/m4g6_recount.py`, the per-player recount;
  - `spikes/m4d_percentiles_reference.py` gains `gate` and `min_cohort` parameters; the defaults reproduce the build, and both references pass.
- **Findings:**
  - **Gate 720:** shifts are small (cell medians about 1 point, 93–97% of the top 10% kept, top-10 overlap median 8–9). The 126 rows of the 720–900 band are 9.3% of the rows, but 1.2× their share of the top-10% cells and 1.9× their share above P99. This is consistent with low-minute noise at the extremes and **supports not lowering the 900-minute gate**; 900 vs 1,080 is decided by the coverage loss (e.g. PL ST).
  - **Gate 1,080:** 137 rows drop out, 6 of them mover stints. Rule-faithful, all 29 PL ST rows lose their cells to the cohort floor.
  - **Reliability caveat:** near-gate CB and FB-WB measures have a median r_full of about 0.60.
  - **Outliers:** 277 cells in 191 rows above P99; 0 recount mismatches and 0 duplicate events. The pre-registered "single-match spike" class (130) reads as marginal exceedance: 128 stay above P95 without the top match. Only 7 cells have a top-match share of 25% or more. Near-gate rows are not over-represented at 900 (24.4% of rows, 24.2% of cells).
  - **Club changers** (n = 5, date order): described only.
  - **D2 / D3:** 15 players would pass on combined minutes but get no profile.
  - **Two bases:** median top-10 overlap 8. On the local basis, same-league neighbours fall below expectation, partly for a mechanical reason.
- **`/finish-chunk`:**
  - qa-tester skipped (spikes, tests and docs only);
  - stats-reviewer, output only: CONCERNS, all fixed in the one round (single-match reading, flag totals, band landing, r caveat, same-league caveat, date order, top-20 |z|, smaller wording);
  - code-reviewer: PASS, should-fix items done (mover-rule note, empty-population guard, hoisted 10th-neighbour list, annotations, clean FAIL on an old snapshot, tests incl. thresholds read from `config`);
  - fix delta: PASS. One fix-delta item was applied because the text it flagged was wrong: the band "about twice" claim now prints the computed ratios (1.2× / 1.9×).
- **Backlog, gated before the next run of `spikes/m4_validation.py` (M4g6a2 or later; code-reviewer, M4g6a1 fix delta; user rule: one fix round):**
  1. `multi_position`: `first_date.get(x, "")` sorts a stint without a date first. Fail, or record it, instead; `first_dates()` should count the match ids it skips.
  2. `first_dates()` hard-codes the season file `27.json`; read the season from the keys.
  3. `test_same_league_observed_and_expected` cannot catch a K-limiting bug (`top_k` binds its default `k` at definition time): use a fixture where K = 1 and K = 2 differ, and pass `k` explicitly.
  4. Share one P99 helper between `outliers()` and `band_landing()`.
  5. Item 9 still prints the old Passing status name `pending_mover_stability` (renamed `pending_xpass_deferred` in M4g6c, ADR 004 §14 item 4); update the text before the next run (stats-reviewer, M4g6c).

### M4g6a2 — outcome (2026-10-09)
- **Files:**
  - `spikes/m4g6_weighting.py` → the `m4g6_weighting` section of `docs/reviews/m4-validation.md`, figures in `data/interim/m4g6a2/weighting.json`;
  - `spikes/m4f_weighting_checks.py` re-run, which regenerated `docs/reviews/m4f-weighting.md` (items 6, 8). Only the header and stamps changed: the figures already matched M4g2. The user's 2026-10-07 read is marked as history, to be re-read.
- **Findings (evidence for sign-off item 1, provisional until M5's self-retrieval):**
  - **Cap and merge stay close to the build.** Median top-10 overlap is 8–10, except FB-WB cap at 7 (P10 6–10). DM-CM is unchanged (no one-member family). ST merge equals the build (no Creation member).
  - **One-member families do not dominate retrieval.** Leaving one out moves neighbours like any other family. Neighbours agree on them only slightly better than noise selection (absolute real / noise 0.79–1.04).
  - **Noise share Σ w (1 − r) differs by at most 0.017** (AM-Winger 0.196 → 0.184 cap / 0.179 merge). A reliability case exists only for Creation (`op_key_passes`, r ≈ 0.67–0.68) in AM-Winger and FB-WB.
  - **Double counting:** 4 cross-family pairs at |ρ| ≥ 0.7, two of them Creation ~ `box_entries_pass`.
  - Defending coherence is untested (one directional member).
  - Item 6: 94 cells in 63 rows, 34.9% mixed. Item 8: CB 47 → 52, FB-WB 47 → 41, DM-CM 28 → 29, AM-Winger 45 → 45, ST 10 → 9.
- **Correction (fix round):** the first run's cap split freed weight into the other capped families. In FB-WB (3 capped families) each ended at 0.667/|F|, against the pre-registered 0.5/|F|, and its test encoded the implementation rather than the rule. The code and the test now follow the rule; only FB-WB changed (cap: effective metrics 13.8 → 14.3, noise share 0.231 → 0.229, overlap 8/7 → 7/6).
- **`/finish-chunk`:**
  - qa-tester skipped (spikes, tests and docs only);
  - stats-reviewer, output only: CONCERNS 1–8, all fixed (cap rule, noise-share label, absolute ratios, "approximately", item–rest labels, FB-WB / ST face lists checked in the data, item 6/8 figures, spread note);
  - code-reviewer: PASS, should-fix items done (majority-admitted cells from the registry snapshot, r estimator labelled, synthetic tests for `Pairs`, the shares, `same_pos` and the sign flip, weight cache);
  - fix delta: PASS.
- **Backlog, gated before the next run of `spikes/m4g6_weighting.py` (code-reviewer, M4g6a2 fix delta; user rule: one fix round):**
  1. `weights()` writes the capped weight as (1 − CAP)/n and the test as CAP/n; they agree only at CAP = 0.5. Use CAP/n in the code, matching "0.5/|F|".
  2. `test_same_pos_counts_observed_and_expected`: the `obs` assertion recomputes the formula; pin a hand-counted value.
  3. `render()`: a metric missing from `abs_vs_noise` prints "0.00" (reads as perfect agreement); print "n/a".
  4. `render()`: guard `z['mixed_share_z_rows']` being None (no |z| > 5 cell).

### M4g6b — sign-off decisions (user, 2026-10-09; ADR 004 §14)
- **Pre-decision record:** `docs/reviews/m4-signoff.md` (`e1838df`).
- **Decisions:**
  1. weighting A, accepted and provisional until M5;
  2. all 8 allow-listed cells kept as DNA (revisit CB / DM-CM long-pass completion at xPass or M5, whichever is first);
  3. FAIL list empty;
  4. rename `pending_mover_stability` → `pending_xpass_deferred` with the new page text, in M4g6c, before plan 006 W1;
  5. N1–N3 confirmed;
  6. the six deferrals confirmed;
  7. backlog triage as proposed.
- The user's face read is recorded verbatim in ADR 004 §14 item 1, with the ST group check (Niang, Muriel, Pléa, Braithwaite, Jovetić: ST by the main-group rule, no mapping error).
- **Next:** M4g6c is the item-4 rename only; then M4g6d (stats-reviewer sign-off of the write-up, milestone report). Stop before the merge.

### M4g6c — outcome (2026-10-09)
- **The rename:** `pending_mover_stability` → `pending_xpass_deferred`. Every withheld family reads "withheld (<reason>)"; Passing reads "team-influenced; xPass deferred until after launch".
- **STOP rule** (`spikes/m4g6c_before_after.py`, expected file written before code): passed. `player_dna.parquet` and the percentiles are byte-identical; only the 1,075 withheld Passing rows changed status; `REGISTRY_VERSION` is unchanged.
- **Golden pages:** text only.
- **`/finish-chunk`:** fast 2,212 and integration 140 pass; qa-tester PASS (2 stale-status refusal tests added); stats-reviewer PASS (2 documentation notes fixed: the rename pointers in ADR §12.9, §13.3 and §13.9, and §14 item 7 aligned to before-W5); code-reviewer PASS.
- **Backlog** (code-reviewer, M4g6c; user 2026-10-09; no gate, the STOP rule has run):
  1. `spikes/m4g6c_before_after.py`: check the expected file's `registry_version` against the data, and fail with a clear "snapshot missing" message when `data/interim/m4g6c_snapshot/` is absent.
  2. `spikes/m4f_dna_reference.py` (about line 292): fix the stale indentation of the continuation line after the `pending_xpass_deferred` count.

### Stats-reviewer review of M4g0 (2026-10-07): APPROVE WITH CONDITIONS
- Folded in above: the leakage decision; the M4g1 lift and pivot label; the M4g3 baseline `min_attempts`, pooling, paired bootstrap and RMS; the M4g4 fixed order, ratios and β̂ fit; the M4g5 subsets and stratified folds; the majority-trigger wording.
- Recorded in ADR 004: `long_pass_completion_pct` has the same length-truncation bias. A long ball that is intercepted or blocked early is recorded short and drops out of the denominator, which inflates the rate (the Koné example's metric).
- **New dependencies (stats-reviewer condition):** numpy and scikit-learn join `pyproject.toml`, needed for the PCA, the xP model and the seeded RNG. Today the project declares polars only. scikit-learn is in the brief's stack. Since M4g1 both are in the `dev` extra (spikes only); they move to runtime dependencies when `src/` first imports them (M4g5).
- **User decisions on the review (2026-10-07):**
  - mover threshold f̂ ≤ 0.5;
  - duel-success denominator `tackles_attempted + dribbled_past`;
  - the Defending restore needs the mover rule as well;
  - ambition kept, with its prediction recorded.

### Process
- M4g0 (this section) is reviewed by the stats-reviewer before any code: the leakage mitigation and the mover threshold were decided, and every earlier proposal (M4g1 reading, M4g4 selection, M4g5 acceptance, the duel-success terms) was confirmed or changed. The outcome is in the review subsection above.
- The user then fixes the mover threshold (done 2026-10-07: f̂ ≤ 0.5), followed by a docs commit.
- Each later chunk's end-of-chunk stats review covers its output only (CLAUDE.md).

---

## Backlog
- **M4g1 spike follow-ups** (code-reviewer, M4g1 fix delta; user 2026-10-07):
  - `spikes/m4g_fine_roles.py` `check_proposal`: guard the LW / RW divide (no wide rows should `fail`, not raise ZeroDivisionError), as `pool_facts` does, with a test;
  - drop the hand-written "four legend ends" count from this plan's M4g1 outcome; the review's list is computed by `legend_changes`;
  - a direct test for `legend_changes` (`spikes/m4g_style_map.py`).
- **M4g1b compare-mode follow-ups** (code-reviewer, M4g1b; user 2026-10-07):
  - **Page size and run time grow with n²:**
    - compare mode adds 0.36–1.07 MB per page: ranks n², picks and distances n²/2;
    - the style-map run takes about 2 minutes: pure-Python pair distances, plus a node render of every ordered pair in the check;
    - revisit before larger pools (more leagues or seasons). Options: vectorised distances, and checking a sample of pairs plus the hand-computed tests;
  - `dist_code` rejects d ≥ 0.99995 (it would print as 1.0000). This cannot occur (weights sum to 1; Hazen percentiles lie inside (0, 100)) and is documented in the function. Switch to an integer round with a range check if the format ever changes;
  - ranks share a rank within 1e-12. The tolerance is not transitive for chains of near-ties. Harmless for display, and the check uses the same rule;
  - `SHOW_PAIRS` (`spikes/m4g_style_map.py`) fails the whole run if a pair is not unique among the plotted rows. A warning would do, since the pairs are display only;
  - `others` (the cross-group search list) holds every row, so a player at two clubs is listed twice. Dedupe by name to shrink each page.
- ~~**M4g3b reference hardening — GATE**~~ **Done in M4g4-0 (`5886a06`, 2026-10-08).** The original list, for the record: (code-reviewer, M4g3b full spike read; user 2026-10-08):
  1. `spikes/m4g3_movers_reference.py`: check the bootstrap size (`block["b"]` and the output's `b` against `B = 2000`), not take it from the file under check.
  2. Same file: when `--check-output` and `--check-measurements` are both given, check the block's `power_at_f0` / `power_se` too (against the output JSON and the reference simulation); today the output's value overrides them.
  3. Same file: check the block's `cross_check_f_hat`, the extra seeds' `k == i + 1` (seeds are paired by position), and that `cross_check_r` is absent when the reference has no cross value.
  4. `spikes/m4g3_movers.py`: move the Koné illustration out of `compute()` into `main()` (a report-only check must not block the measurement writer).
  5. `spikes/m4g3_movers.py`: fail on a null count column instead of letting NaN drop a baseline candidate silently.
  6. `spikes/m4g3_movers_reference.py` `power0` (and check `m4g3_power.low_power`): define the R_m ≤ 0 case (power 0) instead of a math domain error.
- ~~**M4g4b leftovers — GATE: before M4g4d writes any adjusted column to the registry or the vector**~~ **#1–#3 and #5 done in M4g4d step 0 (2026-10-09).** Kept for the record: (one fix round done; code-reviewer, qa-tester and stats-reviewer, M4g4b; user rule 2026-10-08):
  1. `build_player_match`: `opponent_open_play_pass_credits` and `team_action_credits` skip a lineup player with no timeline (`pid in timelines`), as `team_open_play_pass_credits` already did. Confirm that every lineup player has a timeline; if one can be missing, report it rather than skip it. **Confirmation done in M4g4c** (by construction: the lineup loop assigns every player's timeline; 0 duplicate IDs); the report-not-skip change remains.
  2. `opponent_open_play_pass_credits` skips a pass whose `team.id` differs from the passer's lineup team and relies on `team_open_play_pass_credits` to report it and fail the build. Make that dependency explicit (one shared check, or a test that fails if the reporting moves).
  3. The two `continue` branches for an actor or passer outside the lineups (dropped events) are not covered by a test.
  4. ~~`spikes/m4g4b_before_after.py` maps sha keys to parquet paths with `k.replace("_sha256", "")`; use an explicit map.~~ **Done in M4g4c** (`SHA_PATH_KEYS`).
  5. Cosmetic: merge the stacked `usefixtures` decorators in `tests/test_build_player_match.py` into one list.
  6. ~~The m4d / m4f reference fixtures pin `season_parquet_sha256` (checked) and `measurements_sha256` (not checked: it was stale from M4g4a until the M4g4b refresh). Check it, or drop it.~~ **Done in M4g4c:** checked in both integration tests.
- **M4g4c follow-ups** (code-reviewer, fix delta; user rule: one fix round; report-only code, no gate):
  - `spikes/m4g4c_null_calibration.py` has no test: a synthetic one (finite ρ and gap, reproducible for a fixed seed, team labels permuted as whole teams);
  - it repeats the engine's quintile rule (`⌈5 · rank / n⌉`, Q5 − Q1) instead of importing it from `m4g4_possession.Cell`; share one helper so the two cannot drift;
  - `test_tie_rule_ties_equal_ratios_computed_in_different_orders` asserts the raw values differ in the last bit (it shows the rule is needed), which depends on IEEE rounding of that one pair; keep it, or use a pair found at test time;
  - `test_engine_fit_equals_reference_slope_and_fails_on_a_zero_team` builds `Cell` with `object.__new__`; give `Cell` a small constructor from arrays if it grows.
- **§31 treatment re-entry near a period end** (M4g4b checkpoint 1; user 2026-10-08; not part of M4g4b): match 3879637 (Lazio), Milinković-Savić has Player Off 44:27 and Player On 45:16 in period 1, but the ADR 002 §31 timeline (pipeline and `verify_core_metrics` agree) resumes him only at the period 2 start. His events at 45:25–45:30 (a pass, a pressure, a ball recovery) fall outside his credited time, and 45:16 → the 48:47 Half End (about 3.5 minutes) is not credited to him.
  - To do: measure the lost seconds or minutes here, find every other treatment re-entry (Player On) between the nominal period end and the Half End, or otherwise near a period end, across all configured leagues, and report how much credited time each loses;
  - then decide whether §31's window pairing needs an amendment (an ADR 002 change, its own chunk, with the before/after STOP rule).
- **M4g4-0 follow-up** (code-reviewer, fix delta; user rule: one fix round): `tests/test_m4g3_reference_hardening.py::test_ctx_fails_on_a_null_in_either_parquet` asserts only `SystemExit`. Capture stderr and assert "null values in count columns" and the label, since `Ctx()` can `fail()` for other reasons. Verified by hand at the commit (both cases print the null message, and removing the guard turns both red).
- **M4g3a mover-run follow-ups** (code-reviewer, M4g3a; user 2026-10-08): `spikes/m4g3_movers.py` filters the match parquet by competition only; add a `season_id` filter (today a second season would make `replace_strict` raise, so it is safe but implicit).
- **M4g3a power follow-ups** (code-reviewer, M4g3a checkpoint fix delta; user 2026-10-08: no more fix rounds before the real run):
  - `spikes/m4g3_power.py` `main()` builds the grid's low-power labels from the grid cells (their own seed keys), not through `low_power`; the JSON labels are a reference, and a real metric's label comes from `low_power` (≈ 1 pp Monte Carlo difference). Call `low_power` in `main()` or say so in the JSON.
- **M4g3c movers-display follow-ups** (code-reviewer, M4g3c; user 2026-10-08):
  - tests of the check's failure paths (`m4g_movers_display.check_page` fails on a tampered span), and of `for_map` edge cases: more than 3 plotted stints, colour slots, a one-profile player getting "Played for" without a combined dot;
  - `panel` / `rank_both` with 3 stints, and stint order for same-day starts or a player returning to a club: today the order is (first, last, key), so "date order" is not guaranteed for those;
  - `stint_dates_from_matches` loops over every player-match row in Python; filter the frame to the wanted keys first, and fail clearly on an unknown match id;
  - the unused `teams` key in the mover dict;
  - combined dots ignore the role toggles, so a player whose stints are all filtered out still shows his dot;
  - `extra_static` in `m4g_style_page.check_page` has a default; make it required so a caller cannot skip the movers' fixed wording;
  - `test_combined_lies_on_the_segment` builds scores with the same linear map `combined` uses: it checks the weights, not the projection independently.
- **History mode: one player across seasons** (user, 2026-10-07; record only, not built). Show a player's path over seasons on the style map. Prerequisites:
  - full-league, multi-season data from one source (StatsBomb Open Data has one full-league season here, 2015/16);
  - an ADR on aligning maps across seasons. Each season's PCA has its own axes, so positions are not comparable without a fixed projection or an alignment rule.
- **M5: hub-player check in the self-retrieval work** (user face read, 2026-10-09; ADR 004 §14 item 1): Ben Yedder (ST) and Echiejile (FB-WB) each appear in two of the four face lists. In M5's split-half self-retrieval, measure each row's in-degree (how often it is someone's neighbour) and flag hubs, which crowd neighbour lists in high-dimensional distances.
- **M5: revisit `long_pass_completion_pct` (CB, DM-CM)** when xPass returns or at M5, whichever is first (ADR 004 §14 item 2).
- **M5: family weighting is provisional** (ADR 004 §14 item 1): decide it with split-half self-retrieval.
- **M5: DM-CM 6-share context column** (user, 2026-10-07; plan 005 M4g1 outcome).
  - Show each DM-CM row's lineup-slot share (Defensive Midfield slots against Center Midfield slots) next to it as context, never as a cohort.
  - Today it is computed only in `spikes/m4g_style_map.py` from the raw lineups; the pipeline would need per-position minutes.
- **For M6 (Gem / rarity engine): capture "hybrid" players with no single style** (user, 2026-10-09; docs only, nothing built). Candidate methods, to compare in M6:
  1. **Isolation:** distance to the nearest neighbours in the full DNA vector (not the 2D map).
  2. **Rare combination:** high (e.g. top 25%) in families that rarely co-occur, scored against the families' correlation, so a combination is rare only relative to how often those families go together.
  3. **For the map (also plan 006 W5):** per-player 2D reconstruction error of the style-map projection; above a bar, flag "not well represented on this map — see full profile".
  - **Rarity ≠ quality:** rarity is presented next to a profile, never used to rank players.
  - **Methods and thresholds are pre-registered in M6**, before any rarity number is computed.
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
- **Metric candidate: headed finishing for strikers** (user, 2026-10-07): from the shot `body_part`, a headed share of shots and/or headed np_xg per 90. It must pass every registry rule like any new metric. It would complement ST PC1 "Aerial target", which measures aerial-duel volume, not heading ability.
- **Empirical-Bayes shrinkage of ratios** (user, 2026-10-03): shrink each ratio toward its group mean by attempts, then re-evaluate `aerial_win_pct` for CB and FB-WB DNA. CB aerial ability is a known DNA gap: `aerial_win_pct` r 0.499 with 63.5% coverage, so only the aerial volumes are in DNA (ADR 004 §10.9).
  - **Linked: ST DNA has no chance-quality metric.** np_xg 0.599, op_key_passes 0.540 and op_xg_assisted 0.534 are below 0.6, and `np_xg_per_shot` is display (r 0.464, coverage 61.4%).
- **`op_crosses_attempted`** (stats-reviewer, user 2026-10-04; priority): crosses that are not restart deliveries. FB-WB DNA has no crossing metric, because `crosses_attempted` (r 0.787, 0% zeros) includes corners and free kicks and is fixed display (ADR 004 §10.3).
- **Pass value (xT / OBV-style)** (user, 2026-10-07): the value a pass adds (threat at the end location minus threat at the start, or an on-ball-value model), as a later extension of the M4g xPass work. It measures what a pass is worth; xPass measures how likely it was to arrive. Not part of M4g.
- **Direct free-kick shots in `np_xg_per_shot`** (user, 2026-10-04): 1,801 shots (4.7% of non-penalty shots) stay in. Taking free kicks is a team role (the D15 argument), and they pull down designated takers' xG per shot. Removing them needs a D15 amendment.
- **Second-ball creation** (user, 2026-10-03): an open-play key pass within N seconds after a corner or free kick, in the same possession. It measures creation from set-piece second phases, which the key-pass split (ADR 004 §9) counts as open play. It needs its own definition (N, which restarts, same possession) and a measurement. No work now.
- **Return the xG split instead of out-parameter dicts** (user, 2026-10-03): `compute_xg_assisted_per_player` fills `open_play_xga` / `restart_xga` (and `fallbacks`) passed in by the caller. Returning the parts (a small dataclass per player) would be a simpler API.

