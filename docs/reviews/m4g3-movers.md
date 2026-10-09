# M4g3a — Mover stability (2015/16)

Report only: M4g3 changes no `registry_status`, note or `score_status` (ADR 004 §13.7). The rule, the low-power label and the outcomes below were pre-registered in ADR 004 §13.7 and committed (6574e84) before this run.

- **Run:** `spikes/m4g3_movers.py --registry data/interim/m4g3_registry.json` → `data/interim/m4g3_movers.json`; registry version 930d0019ee11…; seed 20261008, 200 draws per mover, B = 2,000.
- **Independent check:** `spikes/m4g3_movers_reference.py --check-output` (no `eleveniq` imports; same RNG streams). Every field matches within 1e-9, and every low-power label matches the reference's own simulation.
- **Movers:** 24 player-seasons with two stints of ≥ 450 nominal minutes in one group (AM-Winger 7, DM-CM 6, CB 5, ST 4, FB-WB 2), 10 of them cross-league; no third stints. These are the counts pre-stated in §13.7.

## Results

The verdict uses scale (b), the group's league-demeaned SD units. Scale (a), local percentiles, is reported only.

| Metric | DNA in | n | Verdict | f̂ | 95% CI | R_m | Cross-check | P(PASS \| f=0) | S (RMS, b) | S (mean, a) |
|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|
| `aerials_lost_p90` | DM-CM, AM-Winger, ST | 24 | inconclusive | 0.07 | [-0.36, 0.58] | 0.53 | 0.61 | 0.54 (underpowered) | 1.04 | 1.19 |
| `ball_recoveries_p90` | all 5 | 24 | inconclusive | 0.31 | [-0.21, 1.03] | 0.59 | 0.58 | 0.64 (underpowered) | 1.20 | 1.30 |
| `blocks_p90` | DM-CM, AM-Winger, ST | 24 | inconclusive | 0.39 | [-0.40, 1.29] | 0.41 | 0.40 | 0.35 (underpowered) | 1.13 | 1.11 |
| `box_entries_carry_p90` | FB-WB, DM-CM, AM-Winger, ST | 24 | inconclusive | 0.99 | [-0.38, 3.31] | 0.50 | 0.57 | 0.46 (underpowered) | 1.40 | 1.31 |
| `box_entries_pass_p90` | all 5 | 24 | inconclusive | 0.19 | [-0.33, 1.21] | 0.61 | 0.61 | 0.71 (underpowered) | 1.14 | 1.24 |
| `clearances_p90` | all 5 | 24 | inconclusive | 0.42 | [-0.12, 1.03] | 0.56 | 0.57 | 0.56 (underpowered) | 1.24 | 1.77 |
| `dribbled_past_p90` | all 5 | 24 | inconclusive | 0.40 | [-0.41, 1.40] | 0.48 | 0.50 | 0.44 (underpowered) | 1.18 | 1.07 |
| `final_third_entries_pass_p90` | CB, AM-Winger, ST | 24 | inconclusive | 0.27 | [-0.18, 0.84] | 0.71 | 0.72 | 0.90 | 1.29 | 1.11 |
| `fouls_committed_p90` | FB-WB, DM-CM, AM-Winger, ST | 24 | inconclusive | 0.12 | [-0.48, 0.94] | 0.51 | 0.56 | 0.50 (underpowered) | 1.06 | 0.88 |
| `interceptions_p90` | DM-CM, AM-Winger | 24 | inconclusive | 0.76 | [-0.25, 2.07] | 0.35 | 0.40 | 0.28 (underpowered) | 1.19 | 1.41 |
| `long_pass_completion_pct` | CB, FB-WB, DM-CM, AM-Winger | 15 | inconclusive | 0.93 | [0.06, 2.05] | 0.65 | 0.60 | 0.67 (underpowered) | 1.64 | 1.31 |
| `np_shots_on_target_p90` | DM-CM, AM-Winger | 24 | inconclusive | 0.24 | [-0.47, 1.00] | 0.44 | 0.40 | 0.38 (underpowered) | 1.09 | 1.27 |
| `np_xg_p90` | DM-CM | 24 | inconclusive | 0.00 | [-0.62, 0.71] | 0.44 | 0.45 | 0.38 (underpowered) | 1.00 | 1.29 |
| `op_key_passes_p90` | FB-WB, DM-CM, AM-Winger | 24 | inconclusive | -0.14 | [-0.94, 0.85] | 0.40 | 0.43 | 0.35 (underpowered) | 0.95 | 1.04 |
| `pass_completion_pct` | ST | 24 | inconclusive | 0.42 | [0.03, 0.95] | 0.79 | 0.80 | 0.99 | 1.61 | 1.33 |
| `progressive_carries_p90` | all 5 | 24 | inconclusive | 0.25 | [-0.11, 0.74] | 0.78 | 0.81 | 0.98 | 1.37 | 1.09 |
| `progressive_passes_p90` | all 5 | 24 | inconclusive | 0.32 | [-0.15, 1.04] | 0.72 | 0.76 | 0.92 | 1.34 | 1.18 |
| `tackles_won_p90` | — | 24 | inconclusive | 0.12 | [-0.64, 1.23] | 0.49 | 0.49 | 0.46 (underpowered) | 1.06 | 1.08 |
| `team_pass_share` | DM-CM | 24 | inconclusive | 0.42 | [0.04, 1.04] | 0.79 | 0.78 | 0.99 | 1.61 | 1.47 |
| `aerials_won_p90` | all 5 | 24 | PASS | -0.00 | [-0.17, 0.18] | 0.77 | 0.78 | 0.97 | 0.99 | 1.17 |
| `dribbles_attempted_p90` | all 5 | 24 | PASS | -0.12 | [-0.40, 0.19] | 0.69 | 0.71 | 0.85 | 0.86 | 0.91 |
| `final_third_entries_carry_p90` | CB, DM-CM | 24 | PASS | -0.02 | [-0.20, 0.20] | 0.70 | 0.70 | 0.89 | 0.97 | 1.23 |
| `fouls_won_p90` | all 5 | 24 | PASS | -0.20 | [-0.38, -0.03] | 0.64 | 0.63 | 0.75 (underpowered) | 0.80 | 0.91 |
| `long_passes_attempted_p90` | all 5 | 24 | PASS | 0.09 | [-0.08, 0.32] | 0.76 | 0.80 | 0.96 | 1.14 | 1.28 |
| `np_shots_p90` | FB-WB, DM-CM, AM-Winger, ST | 24 | PASS | -0.17 | [-0.42, 0.08] | 0.61 | 0.60 | 0.71 (underpowered) | 0.86 | 0.98 |
| `op_xg_assisted_p90` | DM-CM | 24 | PASS | -1.38 | [-2.82, -0.58] | 0.28 | 0.33 | 0.22 (underpowered) | 0.68 | 1.05 |
| `pressures_p90` | all 5 | 24 | PASS | 0.04 | [-0.25, 0.47] | 0.73 | 0.77 | 0.95 | 1.06 | 0.92 |
| `tackles_attempted_p90` | all 5 | 24 | PASS | -0.04 | [-0.45, 0.44] | 0.57 | 0.58 | 0.59 (underpowered) | 0.97 | 0.96 |
| `aerial_win_pct` | ST | 4 | untestable | | | | | | | |
| `cross_completion_pct` | — | 0 | untestable | | | | | | | |
| `defensive_duel_success_pct` | — | 0 | untestable | | | | | | | |
| `dribble_success_pct` | — | 0 | untestable | | | | | | | |
| `np_xg_per_shot` | — | 2 | untestable | | | | | | | |
| `tackle_success_pct` | — | 0 | untestable | | | | | | | |

- **Untestable after the review fixes:** a ratio with no `min_attempts` in a group (CB `cross_completion_pct`, `tackle_success_pct`) now excludes that group's movers instead of applying no gate; both metrics were untestable before and after (n 3 → 0, 5 → 0).
- **Totals:** 9 PASS, 19 inconclusive, 0 FAIL, 6 untestable. 18 of the 28 tested metrics are underpowered (P(PASS | f = 0) < 0.8). No power estimate is within 0.03 of 0.8, so no label depends on Monte Carlo noise.
- **Cross-check:** no |R_m − cross-check| exceeds 0.10 (largest: `box_entries_carry_p90`, −0.08). R_m is below the step-down for 21 of 28 metrics (20 if `final_third_entries_carry_p90`'s −0.0002 counts as 0), the direction expected for date-ordered halves.

## What the results say

### `pass_completion_pct` and ST Passing
- **Result:** inconclusive. f̂ 0.42, CI [0.03, 0.95], n = 24, R_m 0.79, power 0.99 (not underpowered).
- **Pre-stated outcome:** ST Passing stays visible with a note in M4g5. A withholding needs a FAIL; a restore needs a PASS.

### `long_pass_completion_pct`: the motivation for M4g5 xPass
- **Result:** inconclusive. f̂ 0.93, CI [0.06, 2.05], n = 15 (9 movers below `min_attempts` on a stint), power 0.67 (underpowered).
- **The point estimate is the largest of any tested metric:** about all of the stable between-player variance would be team-driven. It would fail even the old point rule.
- **Koné (CB, Lorient → Sunderland, both stints regular):** 0.595 → 0.319.
  - Scale (b): −0.83 → −3.50 SD.
  - Scale (a): 23rd → below the 1st percentile (0.85).
  - His long-ball completion followed the team. The same player is a below-average passer at Lorient and the lowest of the league's CBs at Sunderland.
- **M4g5:** a raw completion rate mixes the passer with the team's pass difficulty and the receivers. This is what M4g5's xPass (completion above expected) is meant to remove. Passing stays `pending_mover_stability` until then (§13.7; the text now means "pending the xPass decision").

### Defending members (information only)
- **Results:**
  - `ball_recoveries_p90`: f̂ 0.31, CI [−0.21, 1.03], power 0.64;
  - `tackles_won_p90`: f̂ 0.12, CI [−0.64, 1.23], power 0.46;
  - both inconclusive and underpowered.
- **What decides the restore:** only the M4g4 re-run on the adjusted columns (§13.7). These verdicts on unadjusted columns decide nothing.
- **Expectation:** at these reliabilities, an M4g4 PASS is unlikely. Defending will very likely stay withheld for 2015/16, as §13.7 anticipated.
- `defensive_duel_success_pct` is untestable (0 movers meet `min_attempts` on both stints).

### PASS verdicts: two caveats

- **`pressures_p90`'s PASS is within Monte Carlo noise of the threshold** (stats-reviewer, read-only re-runs): CI upper bound 0.47; on five other seeds 0.48–0.52, 2 of 5 inconclusive. The committed seed gives the most favourable of the six. The verdict stays PASS (pre-registered seed); M4g3b carries it as a borderline flag in the registry entry. Monte Carlo noise can move verdicts near 0.5, not only power labels. Every other verdict held on all five seeds.
- **Post-hoc observation (stats-reviewer, 2026-10-08, after the real run; no verdict changes).** 7 of the 9 PASS metrics have S (RMS, b) < 1, which the model (f ≥ 0) does not expect in the population. Two have a 95% CI entirely below 0: `op_xg_assisted_p90` (f̂ −1.38, CI [−2.82, −0.58], R_m 0.28, power 0.22) and `fouls_won_p90` (f̂ −0.20, CI [−0.38, −0.03]); `np_shots_p90`'s CI [−0.42, 0.08] crosses 0. On its own this is not evidence of misfit: under the §13.7 model at f = 0 and n = 24, the CI upper bound falls below 0 in about 10% of datasets (17–18% with t(5) noise), consistent with the rule's known narrow upper side. `op_xg_assisted_p90` does show a metric-specific misfit: its S is 0.68 on scale (b) RMS but 1.05 on scale (a), so its PASS comes from heavy-tailed baseline halves on the SD scale, magnified by the low R_m.
- **From M4g4 on (user, 2026-10-08, pre-registered):** a PASS whose CI upper bound is below 0 is reported as "PASS (below model range)". Report-only; it cannot support a restore on its own (ADR 004 §13.7).

### 0 FAILs, as the power simulation predicted
- **Prediction:** the simulation (§13.7) gave P(FAIL) ≤ 0.23 even for a fully team-driven metric at n = 24, and lower at smaller R_m.
- **Result:** with 28 tested metrics, mostly at R_m 0.3–0.8, 0 FAILs is the expected result. It is not evidence that the metrics travel with the player.
- **How to read it:** "inconclusive" here means "not shown to follow the player", nothing more.

### Selection: 8 of 24 second stints are at bottom-five clubs
- **The eight:** Newcastle (18th), Guingamp (16th), Udinese (16th), Sunderland (17th, twice), Norwich (19th), Reims (18th) and Hellas Verona (20th). Ranks come from a points table built from matches.json.
- **Why it matters:** moves are not random team draws. Many January moves go to relegation-threatened clubs, and the model's independent t1, t2 cannot represent this (§13.7 limit).
- **Direction: not established.** For this estimator, a systematic drift to weaker clubs adds a shared shift to t2 − t1. For a metric that depends on team strength, that inflates the mover changes and pushes towards FAIL, not PASS (code-reviewer, M4g3a). Plan 005's earlier remark that such selection makes team effects look smaller is not supported here.

| Player | Group | From (rank) | To (rank) | m1 | m2 | Cross-league |
|---|---|---|---|---:|---:|---|
| Jonjo Shelvey | DM-CM | Swansea City (12) | Newcastle United (18) | 1202 | 1051 |  |
| Milan Biševac | CB | Lyon (2) | Lazio (8) | 456 | 907 | yes |
| Wahbi Khazri | AM-Winger | Bordeaux (14) | Sunderland (17) | 1554 | 1053 | yes |
| Nordin Amrabat | AM-Winger | Málaga (8) | Watford (13) | 985 | 513 | yes |
| Jonathan Martins-Pereira | FB-WB | Troyes (20) | Guingamp (16) | 894 | 1378 |  |
| Gabriel Moisés Antunes da Silva | FB-WB | Carpi (18) | Genoa (10) | 1285 | 777 |  |
| Emil Hallfreðsson | DM-CM | Hellas Verona (20) | Udinese (16) | 1201 | 664 |  |
| Alberto Paloschi | ST | Chievo (9) | Swansea City (12) | 1658 | 658 | yes |
| Stephan El Shaarawy | AM-Winger | AS Monaco (3) | AS Roma (3) | 682 | 1169 | yes |
| Fabio Quagliarella | ST | Torino (12) | Sampdoria (15) | 1334 | 1224 |  |
| Ervin Zukanović | CB | Sampdoria (15) | AS Roma (3) | 1327 | 526 |  |
| Jimmy Cabot | AM-Winger | Troyes (20) | Lorient (15) | 742 | 742 |  |
| Diego Perotti | AM-Winger | Genoa (10) | AS Roma (3) | 1066 | 1203 |  |
| Luca Rigoni | DM-CM | Palermo (17) | Genoa (10) | 674 | 1192 |  |
| Marco Borriello | ST | Carpi (18) | Atalanta (13) | 741 | 862 |  |
| Steven Naismith | AM-Winger | Everton (11) | Norwich City (19) | 456 | 851 |  |
| Steven Fletcher | ST | Sunderland (17) | Olympique de Marseille (12) | 995 | 748 | yes |
| Lamine Koné | CB | Lorient (15) | Sunderland (17) | 1549 | 1313 | yes |
| Augusto Matías Fernández | DM-CM | Celta Vigo (6) | Atlético Madrid (3) | 1335 | 840 |  |
| Abdelhamid El Kaoutari | CB | Palermo (17) | Stade de Reims (18) | 630 | 648 | yes |
| Mario Suárez Mata | DM-CM | Fiorentina (5) | Watford (13) | 462 | 760 | yes |
| Mauro Matías Zárate | AM-Winger | West Ham United (7) | Fiorentina (5) | 781 | 723 | yes |
| Luca Marrone | DM-CM | Carpi (18) | Hellas Verona (20) | 710 | 861 |  |
| Modibo Diakité | CB | Frosinone (19) | Sampdoria (15) | 1619 | 651 |  |
