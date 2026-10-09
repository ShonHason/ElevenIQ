# M4g5 feasibility: can xPass execution restore a Passing family? (2026-10-09)

Report only. Written after M4g4d (`c36809e`) to decide whether to build xPass to restore Passing or as a display feature. No spike, pipeline or registry change. The power runs used the committed `spikes/m4g4_power.py` engine (`cell()`, homogeneous model, 6 streams) in a scratchpad and were not committed.

**Outcome (user decision, 2026-10-09):** M4g5 is deferred until after web v1 launches (ADR 004 §13.9; plan 005 "M4g5 — deferred").

## Answer
- **Chance that xPass execution restores Passing in any group on 2015/16 data: about 20–25%.** A restore would almost certainly be AM-Winger only.
- **CB, FB-WB and DM-CM** (where the Passing score matters most): about 5–10%.
- **Recommendation:** build xPass mainly as a display metric. A restore is a possible bonus of the pre-registered evaluation, not the reason to build it.

## Gate chain
A Passing restore needs every gate. Execution must first be a DNA cell:
- D19 `r_full` ≥ 0.6;
- D17;
- D12: not possession-triggered (mean |ρ| ≥ 0.4, or the majority rule), or allow-listed (≤ 0.55);
- D9.

It then needs a mover PASS on all six streams (D6) and, below model range, √`s2_rms_a` ≤ 1 (D7). This is the same mover rule as Defending (plan 005: "a Passing that fails then gets the same two failure codes"). Ambition is `style`, so it cannot restore a score.

### 1. D12 possession: the binding gate for CB, FB-WB and DM-CM
- **`pass_completion_pct` mean within-league possession ρ** (duration / pass share):

  | Group | ρ |
  |---|---|
  | CB | 0.69 / 0.69 |
  | FB-WB | 0.65 / 0.64 |
  | DM-CM | 0.57 / 0.59 |
  | AM-Winger | 0.38 / 0.41 |

  The trigger is 0.4.
- **Rough estimate for execution:** M4g0 measured ρ(execution, completion) at 0.63–0.77 for the start-only model. If possession reaches execution only through what it shares with completion, execution's ρ ≈ 0.7 × completion's: CB ≈ 0.48, FB-WB ≈ 0.45, DM-CM ≈ 0.41, AM-Winger ≈ 0.28. This is a heuristic, not a measurement. The pre-registered prediction (ADR 004 §13.3) already says execution "may trigger D12".
- **The allow-list is not a real escape.** An allow-list entry admits a triggered cell at ≤ 0.55, but a "team-style influenced" Passing score is exactly what §12.9 withheld Passing to avoid.
- **Estimate of P(not triggered):** CB ≈ 0.2, FB-WB ≈ 0.2, DM-CM ≈ 0.4, AM-Winger ≈ 0.75.

### 2. Reliability (D19): probably passes
Completion's `r_full` is 0.86–0.92. Removing stable difficulty differences lowers it, but a regular has hundreds of open-play passes. P ≈ 0.8–0.9.

### 3. Mover rule: the binding gate overall
The verdict is pooled over groups: one verdict per metric, so every group passes or fails it together.

- **Calibration from the 28 testable metrics** (committed movers block):
  - 9 pass on the primary stream and 8 on all six;
  - every one of the 9 has f̂ ≤ 0.09 (aerials, dribbles, carries, shots, fouls won, long-pass volume, pressures, tackles);
  - every passing-quality or passing-share metric is inconclusive: `pass_completion_pct` f̂ 0.42 [0.03, 0.95] (R_m 0.79), `team_pass_share` 0.42, `progressive_passes_p90` 0.32, `final_third_entries_pass_p90` 0.27, `long_pass_completion_pct` 0.93 (n 15).
- **Prior for execution's team share f:** xP removes the pass-difficulty part of the team effect. Teammates' quality, movement and the system remain. So f ≈ 0.1–0.4, centred near 0.25.
- **Power, P(PASS on all six streams)**, n = 24, 400 datasets per cell (MC SE ≤ 0.025):

  | R_m | f = 0 | f = 0.10 | f = 0.25 | f = 0.40 |
  |---|---|---|---|---|
  | 0.60 | 0.63 | 0.46 | 0.22 | 0.15 |
  | 0.70 | 0.85 | 0.66 | 0.31 | 0.17 |
  | 0.80 | 1.00 | 0.85 | 0.46 | 0.15 |
  | 0.90 | 1.00 | 0.98 | 0.56 | 0.21 |

- **Under-pressure execution:** fewer passes, so about n = 16 movers. At R_m 0.8 the power is 0.74 at f = 0.1 and 0.41 at f = 0.25. At R_m 0.6 it is 0.43 and 0.25.
- **Expected P(PASS on all six):** about 0.3 at a realistic R_m of 0.65–0.75, averaged over the f prior.

### Combined
| Group | D12 | D19 | Mover (shared) | ≈ P(restore) |
|---|---|---|---|---|
| AM-Winger | 0.75 | 0.85 | 0.3 | 0.2 |
| DM-CM | 0.4 | 0.85 | 0.3 | 0.1 |
| CB | 0.2 | 0.85 | 0.3 | 0.05 |
| FB-WB | 0.2 | 0.85 | 0.3 | 0.05 |

The mover verdict is shared, so the chance that any family is restored is about 0.2–0.25. Any restore would carry "underpowered at 24" and depend on all six streams agreeing.

## What would change the estimate
- **A second season.** At n = 48 (R_m 0.7), power on all six streams is 0.83 at f = 0.1 and 0.45 at f = 0.25. That roughly doubles the chance in the plausible range. It is the same lever as for DM-CM Defending (plan 005 N3).
- **The M4g5 spike's first outputs, as a cheap go/no-go:** execution's possession ρ and `r_full` per group, before any pipeline work. ρ < 0.4 in CB, FB-WB or DM-CM would make this estimate too pessimistic and reopen the restore case.

## Why build it as display anyway
- Execution answers "does he complete passes others would not, from the same spots?". Ambition answers "does he try difficult passes?". Both are grounded per-player quantities and need no restore.
- They serve the comparison page and LLM-grounded explanations.
- xPass is the base for the later pass-value (xT / OBV-style) backlog item.

## Caveats
- Execution's real R_m, f and possession ρ are unknown until xPass is built. Every figure above is an estimate from the anchors given.
- The power figures use the homogeneous model. The real-minutes model was within 2 pp of it in M4g3.
- The power model's movers are random draws; real moves are not (ADR 004 §13.7, condition 2.9).
