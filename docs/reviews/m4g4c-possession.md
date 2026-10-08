# M4g4c — Possession candidates, criteria and movers (2015/16)

Spikes only: M4g4c changes no registry status, note, vector or `score_status`. The candidates, criteria, selection order, mover rule and the outcomes stated below were pre-registered in ADR 004 §13.8 and plan 005 "M4g4 plan" before any M4g4 measurement. M4g4d applies the result, held to `spikes/expected_m4g4_flips.json`.

## Summary

- **Defending stays withheld in all 5 groups for 2015/16, as predicted.** The power simulation said every expected case was underpowered (P(PASS | f = 0) about 0.40–0.63 at n = 24), and the pre-stated outcome was "withheld in most or all groups". The rule was not relaxed after the results.
  - CB, FB-WB, AM-Winger, ST: `possession_criteria_failed`. No candidate for `ball_recoveries_p90`, the only directional Defending member, passes criteria (1)–(3); criterion (2), the quintile gap, binds in each, and β̂ fixes criterion (1) in CB, AM-Winger and ST.
  - **Read `possession_criteria_failed` as "not shown to be possession-free", not as "possession dependence found".** With no possession effect at all (team possession shuffled within league), criterion (2) fails in 43–70% of shuffles for `ball_recoveries_p90` in these four groups (27% in DM-CM), and in 27–83% across all 34 cells (mean 57%). The observed gaps that stopped these groups lie inside that null range. See "Null calibration" below.
  - DM-CM: `mover_stability_inconclusive` (underpowered at 24). It selects β = 0, as pre-stated, so its verdict is M4g3b's (f̂ 0.31, CI [−0.21, 1.03]).
  - The three adjusted mover runs (β̂, team share, β = 1) are all inconclusive on all six streams and are report only: no group selected them.
- **7 style cells are adjusted** and enter the vector adjusted (D4 = C): DM-CM `clearances_p90` (β̂) and `dribbled_past_p90` (team share); AM-Winger `clearances_p90` (β̂), `dribbled_past_p90` (β̂), `pressures_p90` (team share) and `tackles_attempted_p90` (β = 1); ST `fouls_committed_p90` (team share). Six more style cells pass at β = 0 and stay raw; 16 style cells have no passing candidate and stay raw (as does `ball_recoveries_p90` in CB, FB-WB, AM-Winger and ST).
- **C4: "not tested".** DM-CM adjusts neither `interceptions_p90` nor `ball_recoveries_p90`, the mechanism the M4g1b compare-pair expectation names. M4g4d reports the pair distances alongside.
- **The infrastructure is ready to re-run with more data.** The pipeline columns (M4g4b), the engine, the independent reference, the G2/G3 gates and the expected-flips writer take no 2015/16-specific input beyond the season id, the pool leagues and the written-out Defending cell list (which the run checks against the registry snapshot, so a registry change fails loudly rather than silently). Another full-league season, or more movers, re-runs the same pre-registered test; at n = 24 movers it is underpowered (P(PASS) about 0.6 at f = 0, about 0.2 at f = 0.25).
- **For the M4 sign-off (user, 2026-10-08):**
  - "accept adjustments with |β̂| > 1?" — AM-Winger `clearances_p90` (β̂ 2.23, CI 0.69–3.66) and `dribbled_past_p90` (β̂ 1.34). The 2.23 rests on three AM-Winger team groups with 3, 5 and 9 clearances: without teams below 10 or 20 group clearances it is 1.10 / 1.08 (DM-CM `clearances_p90` stays 0.96 in every refit; AM-Winger `dribbled_past_p90` 1.27–1.34). The pre-registered fit is kept; a group-count gate in the fit is a sign-off / later-ADR decision.
  - "accept the 7 adjusted cells given criterion (2)'s ~50% null failure rate and their large per-player moves?" — evidence: the null calibration below and "Near-threshold selections".

## Runs and checks

- **Engine:** `spikes/m4g4_possession.py --registry data/interim/m4g4c_snapshot/registry.json --stage criteria`, then `--stage movers` → `data/interim/m4g4c/possession.json`, `movers.json`. Registry snapshot 463d1a4c31ae….
- **Independent reference:** `spikes/m4g4_possession_reference.py --check-output … --check-movers … --write-expected` (no `eleveniq` imports, none of the engine's modules): every criterion value, β̂ (all-4, leave-one-league-out, bootstrap CI), flag, selection, G2 minimum and G3 count within 1e-9; the movers on all six streams within 1e-9; power within 4 combined SEs. It wrote `spikes/expected_m4g4_flips.json`.
- **β = 0 equals the committed measurements** (checked by the engine and the reference): possession ρ per league and proxy, r_full at K = 3,200, D9 league means; β = 0 local percentiles equal the pipeline's `pct_local` on every cell row; the 1,233 gated rows are exactly the primary scored rows.
- **`m4g3_movers.Ctx` gained `extra_cols`** (default none): a rerun of `m4g3_movers.py` is identical to the committed output except its registry stamp.

## Decisions fixed before the results (M4g4c plan, user 2026-10-08)

- **G1:** every selection quantity (criteria, β̂, E_ref) uses only the 1,233 gated rows.
- **G2 (stop, not filter):** opponent open-play passes ≥ 1,000 and the team count of each Defending DNA action ≥ 20 on every gated row. No stop: the minimums are 3,569 and 61.
- **G3 (halves):** a β half needs ≥ 500 opponent passes, a share half ≥ 10 team actions. For the `ball_recoveries_p90` movers it excluded 0 stints and 0 of 3,093 baseline halves under every candidate (n = 24 throughout; smallest eligible baseline set 30).
- **G4:** everything within league or league-demeaned (ρ, quintiles, β̂, criterion (3) halves, mover scales); E_ref is a pooled constant that changes no rank.
- **G5, residual risk:** the pipeline's exposure and team columns were checked against `m4g_opponent_exposure` and `verify_core_metrics`, which share the ADR 002 §31 timelines; the M4g4c reference reads those columns. A §31 timeline error common to all of them would not be caught here. One known instance is open on the backlog (Milinković-Savić's treatment re-entry near a period end).

## Deviation found during the build: the tie rule

β-adjusted values (β̂, β = 1) are rounded to 12 significant digits before ranks and percentiles (criteria (1), (2), (4) and the movers; not criterion (3), which is Pearson). Under β = 1 the value is ∝ count / opponent passes, so rows equal in exact arithmetic came out a few ulps apart depending on the order of computation, and the engine and the reference ranked them differently (24 mismatches, all β = 1). Team share needs no rule: an integer quotient is correctly rounded. No selection and no mover verdict changed. Recorded in ADR 004 §13.8.

## Selection: every Defending DNA cell × candidate

Each candidate cell: criterion (1) mean within-league ρ (duration / pass share); criterion (2) mean quintile gap (duration / pass share); criterion (3) multi-split r_full. For β̂, (1) and (2) are on the held-out leagues. Pass: |ρ| < 0.1 both, gap ≤ 10 both, r_full ≥ 0.6, no sign flip. The first passing candidate in the order β = 0 → β̂ → team share → β = 1 is selected.

| Group | Metric | Selected | β̂ [CI] | β = 0 | β̂ (held out) | team share | β = 1 | Flags |
|---|---|---|---|---|---|---|---|---|
| CB | `ball_recoveries_p90` | **none** | -0.71 [-1.00, -0.45] | +0.251 / +0.283; 15.4 / 22.0; 0.749 — fails 1, 2 | +0.006 / +0.032; 12.5 / 14.1; 0.737 — fails 2 | +0.255 / +0.291; 15.1 / 21.7; 0.759 — fails 1, 2 | +0.518 / +0.550; 36.9 / 43.6; 0.774 — fails 1, 2 | β̂ < 0; inconsistent: beta_0, team_share, beta_1 |
| CB | `clearances_p90` | **none** | +0.58 [0.37, 0.80] | -0.296 / -0.315; 26.6 / 27.1; 0.657 — fails 1, 2 | -0.040 / -0.054; 16.6 / 13.6; 0.612 — fails 2 | +0.115 / +0.101; 11.2 / 9.0; 0.704 — fails 1, 2, flip | +0.156 / +0.144; 14.6 / 13.6; 0.598 — fails 1, 2, 3, flip | inconsistent: beta_0, team_share, beta_1 |
| CB | `dribbled_past_p90` | **none** | -0.12 [-0.81, 0.54] | +0.049 / +0.064; 10.6 / 9.6; 0.644 — fails 2 | +0.033 / +0.047; 12.9 / 11.3; 0.643 — fails 2 | +0.124 / +0.122; 14.9 / 13.3; 0.610 — fails 1, 2 | +0.237 / +0.255; 21.1 / 24.4; 0.658 — fails 1, 2 | β̂ < 0; inconsistent: beta_1 |
| CB | `pressures_p90` | **none** | +0.02 [-0.36, 0.36] | -0.062 / -0.038; 16.8 / 13.4; 0.861 — fails 2 | -0.064 / -0.043; 19.8 / 16.2; 0.862 — fails 2 | +0.085 / +0.106; 20.8 / 16.6; 0.882 — fails 1, 2, flip | +0.301 / +0.323; 20.2 / 25.7; 0.870 — fails 1, 2, flip | inconsistent: beta_hat, team_share, beta_1 |
| CB | `tackles_attempted_p90` | **none** | -0.15 [-0.57, 0.24] | +0.083 / +0.108; 13.2 / 11.2; 0.715 — fails 1, 2 | +0.040 / +0.065; 14.3 / 11.5; 0.713 — fails 2 | +0.119 / +0.136; 16.3 / 14.2; 0.726 — fails 1, 2 | +0.334 / +0.361; 23.3 / 27.4; 0.733 — fails 1, 2 | β̂ < 0; inconsistent: team_share, beta_1 |
| FB-WB | `ball_recoveries_p90` | **none** | -0.05 [-0.38, 0.29] | +0.106 / +0.140; 13.1 / 17.6; 0.748 — fails 1, 2 | +0.083 / +0.116; 11.2 / 15.6; 0.748 — fails 1, 2 | +0.074 / +0.109; 12.6 / 15.1; 0.736 — fails 1, 2 | +0.436 / +0.470; 39.2 / 43.6; 0.751 — fails 1, 2 | β̂ < 0; inconsistent: beta_1 |
| FB-WB | `clearances_p90` | **none** | +0.31 [-0.07, 0.68] | -0.186 / -0.213; 14.5 / 15.5; 0.725 — fails 1, 2 | -0.091 / -0.118; 6.5 / 7.2; 0.713 — fails 1 | +0.111 / +0.085; 8.9 / 9.8; 0.705 — fails 1, flip | +0.119 / +0.095; 12.0 / 12.4; 0.689 — fails 1, 2, flip |  |
| FB-WB | `dribbled_past_p90` | **beta_0** | -0.10 [-0.57, 0.37] | +0.063 / +0.070; 6.8 / 8.4; 0.635 — **pass** | +0.040 / +0.047; 5.7 / 6.5; 0.634 — **pass** | +0.110 / +0.107; 9.9 / 11.2; 0.643 — fails 1, 2 | +0.295 / +0.303; 24.6 / 27.1; 0.654 — fails 1, 2 | β̂ < 0; inconsistent: beta_1 |
| FB-WB | `fouls_committed_p90` | **beta_0** | +0.15 [-0.24, 0.56] | -0.033 / -0.043; 9.7 / 8.7; 0.621 — **pass** | +0.007 / -0.000; 11.4 / 9.8; 0.620 — fails 2 | +0.076 / +0.060; 8.4 / 6.3; 0.649 — **pass** | +0.226 / +0.224; 18.0 / 16.6; 0.624 — fails 1, 2 | inconsistent: beta_1 |
| FB-WB | `pressures_p90` | **beta_0** | +0.16 [-0.13, 0.45] | -0.080 / -0.071; 7.3 / 6.6; 0.881 — **pass** | -0.024 / -0.015; 5.4 / 6.8; 0.884 — **pass** | +0.068 / +0.074; 5.9 / 9.2; 0.897 — fails flip | +0.285 / +0.295; 21.9 / 25.5; 0.873 — fails 1, 2, flip | inconsistent: beta_1 |
| FB-WB | `tackles_attempted_p90` | **beta_0** | -0.01 [-0.39, 0.35] | -0.025 / -0.042; 9.2 / 8.3; 0.750 — **pass** | -0.027 / -0.042; 11.3 / 10.9; 0.750 — fails 2 | +0.012 / -0.006; 8.9 / 7.0; 0.742 — **pass** | +0.261 / +0.251; 22.5 / 23.5; 0.764 — fails 1, 2 | β̂ < 0; inconsistent: beta_1 |
| DM-CM | `ball_recoveries_p90` | **beta_0** | +0.19 [-0.01, 0.35] | -0.078 / -0.083; 8.7 / 8.9; 0.751 — **pass** | +0.014 / +0.010; 8.3 / 9.9; 0.745 — **pass** | -0.112 / -0.116; 12.6 / 12.5; 0.752 — fails 1, 2 | +0.368 / +0.373; 28.6 / 29.0; 0.736 — fails 1, 2, flip | inconsistent: beta_1 |
| DM-CM | `blocks_p90` | **none** | +0.53 [0.24, 0.78] | -0.216 / -0.219; 20.5 / 21.0; 0.618 — fails 1, 2 | -0.029 / -0.029; 11.4 / 10.1; 0.588 — fails 2, 3 | -0.044 / -0.037; 10.6 / 9.7; 0.617 — fails 2 | +0.153 / +0.157; 14.3 / 12.0; 0.581 — fails 1, 2, 3, flip | inconsistent: beta_0, beta_1 |
| DM-CM | `clearances_p90` | **beta_hat** | +0.96 [0.55, 1.43] | -0.275 / -0.289; 20.8 / 21.6; 0.810 — fails 1, 2 | -0.085 / -0.093; 7.6 / 7.7; 0.780 — **pass** | -0.093 / -0.106; 6.8 / 8.6; 0.840 — fails 1 | -0.080 / -0.088; 6.1 / 6.4; 0.778 — **pass** | inconsistent: beta_0, beta_hat |
| DM-CM | `dribbled_past_p90` | **team_share** | +0.13 [-0.31, 0.61] | -0.062 / -0.046; 10.0 / 11.0; 0.691 — fails 2 | -0.024 / -0.009; 10.4 / 11.4; 0.691 — fails 2 | -0.006 / +0.008; 6.0 / 7.7; 0.702 — **pass** | +0.199 / +0.222; 15.2 / 16.2; 0.704 — fails 1, 2, flip | inconsistent: beta_1 |
| DM-CM | `fouls_committed_p90` | **none** | +0.25 [-0.22, 0.69] | -0.122 / -0.094; 13.3 / 16.7; 0.743 — fails 1, 2 | -0.055 / -0.025; 13.9 / 17.5; 0.738 — fails 2 | +0.023 / +0.043; 10.5 / 13.3; 0.736 — fails 2 | +0.152 / +0.187; 19.6 / 23.0; 0.725 — fails 1, 2, flip | inconsistent: beta_0, beta_hat |
| DM-CM | `interceptions_p90` | **none** | +0.48 [0.02, 0.86] | -0.144 / -0.143; 14.3 / 12.3; 0.673 — fails 1, 2 | -0.019 / -0.017; 13.7 / 7.4; 0.674 — fails 2 | -0.067 / -0.065; 11.7 / 6.3; 0.696 — fails 2 | +0.119 / +0.125; 12.9 / 10.9; 0.675 — fails 1, 2, flip |  |
| DM-CM | `pressures_p90` | **none** | +0.38 [0.15, 0.61] | -0.183 / -0.163; 14.9 / 14.2; 0.868 — fails 1, 2 | +0.005 / +0.032; 14.5 / 17.2; 0.875 — fails 2 | -0.009 / +0.011; 6.2 / 10.0; 0.910 — fails 2 | +0.269 / +0.304; 22.3 / 24.6; 0.871 — fails 1, 2, flip | inconsistent: beta_1 |
| DM-CM | `tackles_attempted_p90` | **beta_0** | +0.09 [-0.22, 0.44] | -0.051 / -0.045; 9.3 / 9.5; 0.780 — **pass** | -0.019 / -0.012; 12.4 / 13.1; 0.779 — fails 2 | -0.015 / -0.015; 4.3 / 4.4; 0.780 — **pass** | +0.241 / +0.255; 19.4 / 20.4; 0.772 — fails 1, 2, flip | inconsistent: beta_1 |
| AM-Winger | `ball_recoveries_p90` | **none** | +0.29 [-0.02, 0.57] | -0.127 / -0.141; 9.1 / 7.1; 0.733 — fails 1 | -0.003 / -0.004; 10.9 / 9.4; 0.727 — fails 2 | -0.169 / -0.168; 9.3 / 9.7; 0.739 — fails 1 | +0.256 / +0.275; 24.9 / 26.8; 0.720 — fails 1, 2, flip | inconsistent: beta_1 |
| AM-Winger | `blocks_p90` | **none** | +0.96 [0.37, 1.55] | -0.159 / -0.191; 16.2 / 15.1; 0.680 — fails 1, 2 | +0.089 / +0.074; 19.9 / 18.7; 0.657 — fails 2, flip | -0.042 / -0.059; 11.6 / 11.9; 0.667 — fails 2 | +0.090 / +0.075; 16.8 / 15.1; 0.657 — fails 2, flip | inconsistent: beta_0, beta_hat, team_share |
| AM-Winger | `clearances_p90` | **beta_hat** | +2.23 [0.69, 3.66] | -0.323 / -0.319; 27.3 / 27.1; 0.754 — fails 1, 2 | -0.057 / -0.028; 6.9 / 7.7; 0.720 — **pass** | -0.224 / -0.208; 18.9 / 18.0; 0.747 — fails 1, 2 | -0.208 / -0.191; 18.6 / 16.9; 0.736 — fails 1, 2 | |β̂| > 1; inconsistent: beta_0, team_share, beta_1 |
| AM-Winger | `dribbled_past_p90` | **beta_hat** | +1.34 [0.78, 1.88] | -0.228 / -0.233; 17.4 / 16.6; 0.735 — fails 1, 2 | +0.035 / +0.045; 9.5 / 8.9; 0.716 — **pass** | -0.198 / -0.208; 15.6 / 15.7; 0.747 — fails 1, 2 | -0.027 / -0.022; 5.4 / 5.5; 0.721 — **pass** | |β̂| > 1; inconsistent: beta_0, team_share |
| AM-Winger | `fouls_committed_p90` | **none** | +0.56 [-0.06, 1.09] | -0.217 / -0.206; 19.1 / 16.9; 0.807 — fails 1, 2 | -0.115 / -0.091; 14.6 / 17.6; 0.799 — fails 1, 2 | -0.132 / -0.131; 11.6 / 10.7; 0.787 — fails 1, 2 | -0.030 / -0.004; 11.5 / 14.5; 0.791 — fails 2 | inconsistent: beta_0, beta_hat, team_share |
| AM-Winger | `interceptions_p90` | **none** | +1.21 [0.55, 1.95] | -0.225 / -0.250; 16.9 / 19.2; 0.626 — fails 1, 2 | +0.021 / +0.019; 7.8 / 6.7; 0.595 — fails 3 | -0.166 / -0.180; 12.7 / 13.4; 0.600 — fails 1, 2, 3 | -0.022 / -0.030; 4.9 / 5.7; 0.599 — fails 3 | |β̂| > 1; inconsistent: beta_0 |
| AM-Winger | `pressures_p90` | **team_share** | +0.77 [0.34, 1.16] | -0.194 / -0.217; 15.2 / 15.0; 0.896 — fails 1, 2 | +0.050 / +0.045; 11.7 / 9.0; 0.904 — fails 2, flip | -0.070 / -0.099; 6.9 / 5.6; 0.929 — **pass** | +0.139 / +0.135; 14.4 / 13.9; 0.899 — fails 1, 2, flip | inconsistent: beta_0, beta_1 |
| AM-Winger | `tackles_attempted_p90` | **beta_1** | +1.07 [0.45, 1.72] | -0.168 / -0.197; 12.8 / 17.1; 0.766 — fails 1, 2 | +0.054 / +0.036; 9.3 / 5.7; 0.751 — fails flip | -0.143 / -0.178; 11.0 / 15.3; 0.764 — fails 1, 2 | +0.041 / +0.022; 6.9 / 2.9; 0.752 — **pass** | |β̂| > 1 |
| ST | `ball_recoveries_p90` | **none** | +0.36 [-0.09, 0.86] | -0.157 / -0.126; 15.7 / 12.5; 0.774 — fails 1, 2 | -0.036 / -0.004; 10.9 / 18.4; 0.771 — fails 2 | -0.162 / -0.132; 16.7 / 12.4; 0.765 — fails 1, 2 | +0.160 / +0.196; 14.3 / 22.3; 0.761 — fails 1, 2, flip | inconsistent: beta_0, beta_hat, team_share, beta_1 |
| ST | `blocks_p90` | **none** | +0.72 [0.07, 1.29] | -0.235 / -0.245; 21.5 / 20.9; 0.611 — fails 1, 2 | -0.057 / -0.062; 13.2 / 13.3; 0.592 — fails 2, 3 | -0.111 / -0.112; 18.0 / 12.6; 0.599 — fails 1, 2, 3 | +0.014 / +0.011; 11.2 / 12.8; 0.589 — fails 2, 3 | inconsistent: beta_0 |
| ST | `clearances_p90` | **beta_0** | -0.25 [-1.53, 0.90] | +0.025 / +0.023; 7.9 / 4.2; 0.728 — **pass** | -0.012 / -0.016; 9.8 / 7.0; 0.725 — **pass** | +0.119 / +0.119; 9.5 / 6.5; 0.736 — fails 1 | +0.147 / +0.144; 9.2 / 9.7; 0.735 — fails 1 | β̂ < 0 |
| ST | `dribbled_past_p90` | **none** | +0.12 [-0.53, 0.80] | -0.120 / -0.122; 18.1 / 15.4; 0.622 — fails 1, 2 | -0.099 / -0.101; 16.8 / 14.1; 0.622 — fails 1, 2 | -0.071 / -0.079; 17.1 / 13.9; 0.618 — fails 2 | +0.072 / +0.072; 8.7 / 10.3; 0.626 — fails 2, flip |  |
| ST | `fouls_committed_p90` | **team_share** | +0.44 [-0.08, 0.93] | -0.224 / -0.224; 16.6 / 17.5; 0.788 — fails 1, 2 | -0.094 / -0.093; 12.4 / 14.2; 0.782 — fails 2 | -0.077 / -0.092; 7.8 / 8.2; 0.766 — **pass** | +0.064 / +0.067; 11.3 / 9.1; 0.771 — fails 2, flip |  |
| ST | `pressures_p90` | **none** | +0.71 [0.23, 1.17] | -0.252 / -0.259; 28.7 / 25.1; 0.895 — fails 1, 2 | -0.033 / -0.034; 22.4 / 18.2; 0.905 — fails 2 | -0.156 / -0.175; 29.4 / 25.8; 0.928 — fails 1, 2 | +0.066 / +0.065; 14.3 / 11.7; 0.900 — fails 2, flip | inconsistent: beta_0, beta_hat, team_share, beta_1 |
| ST | `tackles_attempted_p90` | **none** | +0.20 [-0.69, 1.14] | -0.096 / -0.070; 14.0 / 10.2; 0.728 — fails 2 | -0.047 / -0.021; 12.7 / 11.0; 0.725 — fails 2 | -0.061 / -0.039; 14.2 / 10.9; 0.730 — fails 2 | +0.094 / +0.120; 11.1 / 9.8; 0.715 — fails 1, 2, flip |  |

## Movers: `ball_recoveries_p90`

Pooled over the 24 movers, each adjusted with its own group's E_ref and all-4-league β̂. Streams `m4g4_draws` / `m4g4_boot` (+ `#1`–`#5`). Scales (a) and (b) on the adjusted column's own league × group population.

| Candidate | Used for | n | f̂ | 95% CI | R_m | P(PASS \| f = 0), primary stream | √S² RMS (b) | √S² RMS (a) | Verdict, 6 streams |
|---|---|---:|---:|---|---:|---:|---:|---:|---|
| β = 0 (M4g3b, reused) | DM-CM | 24 | 0.308 | [-0.205, 1.026] | 0.591 | 0.64 (underpowered) | 1.202 | 1.135 | inconclusive ×6 |
| beta_hat | report only, not used for any restore | 24 | 0.424 | [-0.158, 1.362] | 0.588 | 0.64 (underpowered) | 1.267 | 1.197 | inconclusive ×6 |
| team_share | report only, not used for any restore | 24 | 0.295 | [-0.161, 0.964] | 0.600 | 0.69 (underpowered) | 1.201 | 1.183 | inconclusive ×6 |
| beta_1 | report only, not used for any restore | 24 | 0.137 | [-0.288, 0.705] | 0.601 | 0.68 (underpowered) | 1.098 | 1.211 | inconclusive ×6 |

- No CI lies below 0.5 or above it, on any stream: no seed sensitivity, no "below model range" case, so the M4g4a √`s2_rms_a` caveat does not arise.
- The power column is the primary stream's (`m4g3_power.low_power`, the label's input). D6's restore needs PASS on all six streams, about 3–4 points lower (ADR 004 §13.8: 0.63 at R_m 0.59). No label changes: every value is below 0.8.
- R_m stayed at 0.59–0.60 rather than falling to the ≈ 0.50 the power section expected; power 0.64–0.69, still underpowered.
- β = 1 removes the most of the possession-linked team effect and has the lowest f̂ (0.14, CI upper 0.705); still inconclusive, and report only.

## Null calibration of criteria (1) and (2) (stats-reviewer, M4g4c output review)

`spikes/m4g4c_null_calibration.py` (report only; seed 20261010, 400 shuffles per cell, raw β = 0 values): within each league the team labels are permuted and each row takes the permuted team's mean proxy, on both proxies at once (1e-9 jitter for within-team ties). Team clustering stays; any possession link is gone. A shuffle fails a criterion when either proxy fails it, as in the selection.

| Group | Metric | n | Median gap, duration proxy only | P(fail (2)), either proxy | P(fail (1)), either proxy |
|---|---|---:|---:|---:|---:|
| CB | `ball_recoveries_p90` | 255 | 9.0 | 0.55 | 0.17 |
| CB | `clearances_p90` | 255 | 10.9 | 0.68 | 0.28 |
| CB | `dribbled_past_p90` | 255 | 11.1 | 0.71 | 0.30 |
| CB | `pressures_p90` | 255 | 11.0 | 0.69 | 0.27 |
| CB | `tackles_attempted_p90` | 255 | 10.3 | 0.66 | 0.22 |
| FB-WB | `ball_recoveries_p90` | 249 | 8.7 | 0.43 | 0.10 |
| FB-WB | `clearances_p90` | 249 | 10.5 | 0.65 | 0.21 |
| FB-WB | `dribbled_past_p90` | 249 | 8.8 | 0.43 | 0.11 |
| FB-WB | `fouls_committed_p90` | 249 | 9.4 | 0.49 | 0.14 |
| FB-WB | `pressures_p90` | 249 | 10.1 | 0.60 | 0.17 |
| FB-WB | `tackles_attempted_p90` | 249 | 8.4 | 0.39 | 0.11 |
| DM-CM | `ball_recoveries_p90` | 294 | 7.2 | 0.27 | 0.08 |
| DM-CM | `blocks_p90` | 294 | 9.0 | 0.47 | 0.13 |
| DM-CM | `clearances_p90` | 294 | 9.2 | 0.48 | 0.15 |
| DM-CM | `dribbled_past_p90` | 294 | 9.0 | 0.51 | 0.15 |
| DM-CM | `fouls_committed_p90` | 294 | 8.6 | 0.41 | 0.14 |
| DM-CM | `interceptions_p90` | 294 | 7.5 | 0.30 | 0.08 |
| DM-CM | `pressures_p90` | 294 | 9.2 | 0.52 | 0.19 |
| DM-CM | `tackles_attempted_p90` | 294 | 7.9 | 0.35 | 0.11 |
| AM-Winger | `ball_recoveries_p90` | 277 | 9.3 | 0.55 | 0.14 |
| AM-Winger | `blocks_p90` | 277 | 10.1 | 0.62 | 0.20 |
| AM-Winger | `clearances_p90` | 277 | 10.2 | 0.63 | 0.23 |
| AM-Winger | `dribbled_past_p90` | 277 | 9.3 | 0.54 | 0.17 |
| AM-Winger | `fouls_committed_p90` | 277 | 9.6 | 0.54 | 0.20 |
| AM-Winger | `interceptions_p90` | 277 | 9.3 | 0.51 | 0.18 |
| AM-Winger | `pressures_p90` | 277 | 8.9 | 0.49 | 0.13 |
| AM-Winger | `tackles_attempted_p90` | 277 | 9.0 | 0.50 | 0.12 |
| ST | `ball_recoveries_p90` | 158 | 10.0 | 0.70 | 0.25 |
| ST | `blocks_p90` | 158 | 12.6 | 0.83 | 0.28 |
| ST | `clearances_p90` | 158 | 12.4 | 0.82 | 0.32 |
| ST | `dribbled_past_p90` | 158 | 11.3 | 0.74 | 0.25 |
| ST | `fouls_committed_p90` | 158 | 11.1 | 0.74 | 0.21 |
| ST | `pressures_p90` | 158 | 11.5 | 0.76 | 0.26 |
| ST | `tackles_attempted_p90` | 158 | 11.9 | 0.81 | 0.29 |

- **Why criterion (2) fails so often without an effect:** |Q5 − Q1| per league is the absolute value of a noisy difference (a quintile holds about 8–15 rows, often from only a few teams), so its mean over 4 leagues sits near the 10-point bar even at zero effect. ST (n = 158) has a median null gap above 10 in 6 of 7 cells.
- **Criterion (1)** fails 8–32% of the time under the null (SD of the 4-league mean ρ 0.05–0.09).
- **Consequence:** a candidate's failure tells little about possession dependence, and a pass near the bar is close to a coin flip. The pre-registered rule and its outcome are unchanged; this is the evidence for the M4 sign-off question on the adjusted cells.

## Near-threshold selections and per-player moves (stats-reviewer figures, M4g4c output review)

- 5 of the 7 adjusted cells pass within about 0.01 of the ρ bar or about 1 point of the gap bar: AM-Winger `pressures_p90` (team share, ρ −0.099), ST `fouls_committed_p90` (team share, ρ −0.092), DM-CM `clearances_p90` (β̂, ρ −0.093), AM-Winger `dribbled_past_p90` (β̂, gap 9.5), AM-Winger `clearances_p90` (β̂, per-league gaps 0.5 / 7.0 / 2.9 / 17.2). Two β = 0 style cells are near the bar too (FB-WB `fouls_committed_p90` gap 9.7; DM-CM `tackles_attempted_p90` 9.3 / 9.5).
- Raw vs adjusted local percentiles: Spearman 0.92–0.97, median |shift| 4–7.5 points, 90th percentile 13–20; the largest moves include Neymar `dribbled_past` 12 → 52, Bouna Sarr `clearances` 43 → 83, Carlos Sánchez (Aston Villa) 56 → 89, Pasciuti (Carpi) 59 → 23.
- The direction is football-sensible (low opponent passing means fewer chances to be dribbled past), but which candidate wins is fragile for several cells and a second season could reverse it. M4g4d's before/after re-measures the moves on the built vector; the acceptance is on the M4 sign-off list.

## Criterion (4): D9 league means, adjusted cells (report only)

| Group | Metric | Candidate | D9 league means, raw | D9 league means, adjusted |
|---|---|---|---|---|
| DM-CM | `clearances_p90` | beta_hat | PL 58.3, L1 50.0, LL 45.9, SA 46.4 | PL 59.4, L1 49.4, LL 46.7, SA 45.5 |
| DM-CM | `dribbled_past_p90` | team_share | PL 58.3, L1 47.4, LL 57.4, SA 39.8 | PL 51.6, L1 52.2, LL 47.6, SA 48.6 |
| AM-Winger | `clearances_p90` | beta_hat | PL 56.1, L1 42.7, LL 51.3, SA 49.0 | PL 58.2, L1 41.0, LL 50.7, SA 49.1 |
| AM-Winger | `dribbled_past_p90` | beta_hat | PL 55.2, L1 40.3, LL 59.1, SA 41.4 | PL 57.3, L1 38.2, LL 59.0, SA 41.4 |
| AM-Winger | `pressures_p90` | team_share | PL 41.4, L1 59.2, LL 46.5, SA 57.1 | PL 48.7, L1 50.2, LL 52.3, SA 49.0 |
| AM-Winger | `tackles_attempted_p90` | beta_1 | PL 49.5, L1 55.7, LL 52.9, SA 40.0 | PL 50.8, L1 54.2, LL 53.0, SA 39.9 |
| ST | `fouls_committed_p90` | team_share | PL 36.5, L1 55.4, LL 48.7, SA 57.2 | PL 52.2, L1 56.4, LL 42.8, SA 49.2 |

Team share moves the league means towards 50 in every case (e.g. ST `fouls_committed_p90` 36.5–57.2 → 42.8–56.4); β̂ and β = 1 barely move them.

## D17 re-check on adjusted columns (C5, report only)

No pair reaches |ρ| ≥ 0.7, raw or adjusted. Each adjusted column's largest partner:

| Group | Adjusted column | Candidate | Largest partner | ρ raw | ρ adjusted |
|---|---|---|---|---:|---:|
| DM-CM | `clearances_p90` | beta_hat | `aerials_won_p90` | +0.621 | +0.616 |
| DM-CM | `dribbled_past_p90` | team_share | `pressures_p90` | +0.380 | +0.453 |
| AM-Winger | `clearances_p90` | beta_hat | `aerials_won_p90` | +0.484 | +0.415 |
| AM-Winger | `dribbled_past_p90` | beta_hat | `pressures_p90` | +0.541 | +0.564 |
| AM-Winger | `pressures_p90` | team_share | `tackles_attempted_p90` | +0.632 | +0.625 |
| AM-Winger | `tackles_attempted_p90` | beta_1 | `blocks_p90` | +0.526 | +0.493 |
| ST | `fouls_committed_p90` | team_share | `aerials_lost_p90` | +0.431 | +0.436 |

## Against the pre-stated outcomes

| Pre-stated (ADR 004 §13.8) | Outcome |
|---|---|
| Defending most likely withheld in most or all groups | Withheld in all 5 |
| If DM-CM selects β = 0, it stays withheld as `mover_stability_inconclusive` | DM-CM selected β = 0; `mover_stability_inconclusive` |
| β̂ negative for CB and FB-WB recoveries | CB −0.71, FB-WB −0.05 |
| C4 "not tested" iff neither DM-CM interceptions nor recoveries is adjusted | Neither adjusted: "not tested" |
| 0 changes to any existing cell's status, note or reason | 0 (expected file) |
