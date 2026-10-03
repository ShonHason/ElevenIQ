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

### M4e — A comparison page (visual sanity check)
- A generator script writes **one self-contained static HTML file** from the two parquets.
  - It has two player pickers, a radar of the DNA metrics' percentiles (local/global toggle), and a table: p90, percentile, cohort n, status and flags.
  - It has no server and no new runtime dependency.
  - It lives in `spikes/` (or `tools/`) because it is a check, not the M9 frontend.
- Streamlit or FastAPI would add a dependency for a throwaway page, and M7/M9 own those decisions.
- Used straight away on known 2015/16 pairs for a face-validity pass before the DNA vector, e.g. Kanté vs Busquets, Özil vs Payet, Messi vs Neymar, two CBs from opposite styles.

### M4f — Player DNA vector
- `analytics/dna.py` + `pipeline/build_player_dna.py` → `player_dna.parquet`.
- Per scored row:
  - the feature vector on the D10 basis (percentile and robust z, both stored);
  - dimension scores = the mean of the member metrics' percentiles. Equal weights, written in ADR 004; no invented formula;
  - the registry version, so M5 and M6 can check it.
- The D17 drops are applied through the registry, not in this code.

### M4g — Football validation (brief §20)
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
