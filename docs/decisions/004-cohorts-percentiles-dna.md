# ADR 004 — Cohorts, Percentiles and Player DNA

**Date:** 2026-10-02
**Status:** Draft (plan 005, M4a). Sections are filled in from `spikes/m4_decision_measurements.py`.
**Context:** Milestone 4 turns the player-season rows into percentiles, a Player DNA vector and signature traits. Plan 005 lists the decisions D1–D22, and the stats-reviewer's conditions of 2026-10-02 apply to them. The decision mode is:
- each D-item's recommendation is the default;
- the user decides only where the data contradicts the recommendation, the stats-reviewer objects, or the item is a product choice.

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

**Measurement (M4a, to be filled in).** Per zero-heavy metric × group:
- the zero share;
- the number of gated players with a count ≥ 5, 10 and 15;
- the split-half reliability (D19 method).

The chosen minimum counts are recorded here.
