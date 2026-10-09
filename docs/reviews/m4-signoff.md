# M4 sign-off — decisions (M4g6b, 2026-10-09)

Plan 005 "M4g6 plan", step M4g6b. **No decision here is recorded in ADR 004 or plan 005 until the user answers.** Each item gives the options, the evidence (with its source), a recommendation and what changes in the build for each option.

Sources:
- `docs/reviews/m4-validation.md`, sections `m4_validation` (M4g6a1) and `m4g6_weighting` (M4g6a2);
- `docs/reviews/m4f-weighting.md`;
- ADR 004 §12–§13.9;
- plan 005.

Registry `91c24b62e53c…`.

---

## 1. Family weighting when a family has one member (required for sign-off)

**Options**

| Option | Rule |
|---|---|
| A. Accept (the build) | Every family 1/\|F\|, split over its members (ADR 004 §10.8, §12.6). |
| B. Half-weight cap | A one-member family gets 0.5/\|F\|; the freed weight is split equally over the uncapped families. |
| C. Merge | Goal threat + Creation → "Attacking output" where either has one member (changes FB-WB and AM-Winger; CB Aerial never merged). |

The one-member families are:
- CB: Aerial;
- FB-WB: Aerial, Creation, Goal threat;
- AM-Winger: Creation;
- ST: Goal threat.

DM-CM has none.

**Evidence** (`m4g6_weighting`, "Variants side by side", "Per-metric share", "Leave-one-out"):

| Group | Variant | Effective metrics | Noise share Σw(1−r) | Top-10 overlap with build (median / P10) |
|---|---|---|---|---|
| CB | A / B / C | 10.9 / 13.3 / 10.9 | 0.221 / 0.219 / 0.221 | – / 9 / 7 / – |
| FB-WB | A / B / C | 11.1 / 14.3 / 12.3 | 0.234 / 0.229 / 0.227 | – / 7 / 6 / 9 / 7 |
| AM-Winger | A / B / C | 15.3 / 17.2 / 17.6 | 0.196 / 0.184 / 0.179 | – / 9 / 8 / 8 / 7 |
| ST | A / B / C | 14.1 / 16.8 / 14.1 | 0.180 / 0.176 / 0.180 | – / 9 / 8 / – |

- **The variants barely move the neighbours.** Most players keep 8–10 of their 10 neighbours, and 7 in FB-WB under the cap. That is about the same churn as moving the minutes gate to 720 (M4g6a1 item 1, median 8–9).
- **One-member families do not dominate retrieval.**
  - Leaving one out moves neighbours like any other family: CB Aerial 6/4, FB-WB Aerial 6/4, Goal threat 7/5, Creation 8/6, AM-Winger Creation 7/5, ST Goal threat 7/4.
  - Neighbours agree on these single metrics only slightly better than noise selection (absolute real / noise 0.79–1.04).
- **The noise criterion separates the options by at most 0.017** (AM-Winger). The reliability case is concrete only for Creation (`op_key_passes_p90`, r 0.667 AM-Winger, 0.684 FB-WB). It is weak for CB Aerial (`aerials_won` r 0.767, about the group median) and modest for ST Goal threat (`np_shots` 0.774 against the group median 0.82).
- **Double counting:** 4 cross-family pairs at |ρ| ≥ 0.7, two of them Creation ~ `box_entries_pass` (FB-WB 0.73, AM-Winger 0.70). So Creation's effective weight is already above nominal there.
- **What the evidence cannot do:** say which neighbours are correct. That needs split-half self-retrieval, deferred to M5 (user, 2026-10-09). The decision is **provisional until M5** under any option.

**Face read: FB-WB and ST (build, family weights; no user read yet).** Under B and C, the names that enter each list are shown after it.

*Craig Dawson (West Bromwich Albion, PL), FB-WB:*
1. Lorenzo De Silvestri (Sampdoria, Serie A)
2. Aritz Elustondo (Real Sociedad, La Liga)
3. Eros Pisano (Hellas Verona, Serie A)
4. Boukary Dramé (Atalanta, Serie A)
5. Elderson Echiejile (Monaco, Ligue 1)
6. Ali Adnan (Udinese, Serie A)
7. Kévin Théophile-Catherine (Saint-Étienne, Ligue 1)
8. Federico Peluso (Sassuolo, Serie A)
9. Joel Ward (Crystal Palace, PL)
10. Juanfran (Deportivo La Coruña, La Liga)

Under B: in Contento, Chris Brunt, Pablo Martínez, Daniel Pavlovic; out Dramé, Ali Adnan, Ward, Juanfran. Under C: in Pablo Martínez, Lucas Digne, Alberto De La Bella; out Dramé, Ward, Juanfran.

*Dennis Appiah (Caen, Ligue 1), FB-WB:*
1. Adam Masina (Bologna, Serie A)
2. Hugo Lopes (Granada, La Liga)
3. Javier Manquillo (Marseille, Ligue 1)
4. Óscar de Marcos (Athletic Club, La Liga)
5. Danilo D'Ambrosio (Inter, Serie A)
6. Elderson Echiejile (Monaco, Ligue 1)
7. Mikel Balenziaga (Athletic Club, La Liga)
8. Samuel Souprayen (Hellas Verona, Serie A)
9. Jacques François Moubandje (Toulouse, Ligue 1)
10. Adrián Marín (Villarreal, La Liga)

Under B: in Molinero, José Holebas; out Souprayen, Moubandje. Under C: in Molinero, Carles Planas, Ben Davies; out Souprayen, Moubandje, Adrián Marín.

*Harry Kane (Tottenham, PL), ST:*
1. Luis Muriel (Sampdoria, Serie A)
2. Wissam Ben Yedder (Toulouse, Ligue 1)
3. Stevan Jovetić (Inter, Serie A)
4. Iago Aspas (Celta Vigo, La Liga)
5. M'Baye Niang (AC Milan, Serie A)
6. Paulo Dybala (Juventus, Serie A)
7. Alassane Pléa (Nice, Ligue 1)
8. Gonzalo Higuaín (Napoli, Serie A)
9. Federico Dionisi (Frosinone, Serie A)
10. Antoine Griezmann (Atlético Madrid, La Liga)

Under B: in Floyd Ayité, Gerard Moreno, Giuseppe Rossi; out Higuaín, Dionisi, Griezmann. Under C: no change.

*Rubén Castro (Real Betis, La Liga), ST:*
1. Jermain Defoe (Sunderland, PL)
2. Carlos Bacca (AC Milan, Serie A)
3. Romelu Lukaku (Everton, PL)
4. Bafétimbi Gomis (Swansea, PL)
5. Massimo Maccarone (Empoli, Serie A)
6. Antonio Di Natale (Udinese, Serie A)
7. Lucas Pérez (Deportivo La Coruña, La Liga)
8. Wissam Ben Yedder (Toulouse, Ligue 1)
9. Alexandre Lacazette (Lyon, Ligue 1)
10. Martin Braithwaite (Toulouse, Ligue 1)

Under B: in Claudio Beauvue; out Braithwaite. Under C: no change.

**Recommendation: A (accept), marked provisional until M5.**
- On this evidence the options are close: no option is shown to retrieve better, and the noise gain is at most 0.017.
- Changing the weights now would mean a vector change and a re-run of every DNA check for a difference M5 is set up to measure properly.
- **If you weigh the Creation reliability point:** C in AM-Winger and FB-WB is the most defensible change. It is a natural family merge, it gives the largest noise gain, and it leaves CB and ST alone.

**What changes in the build**
- **A:** nothing. ADR 004 §14 records "accepted, provisional until M5 (self-retrieval)".
- **B or C** (an M4g6c chunk):
  - `analytics/dna.split_weights` and the pair distance gain the rule, and `spikes/m4f_dna_reference.py` mirrors it;
  - the `weight` column of `player_dna.parquet` changes in the affected groups (B: CB, FB-WB, AM-Winger, ST; C: FB-WB, AM-Winger), with no percentile or registry change;
  - re-run the M4f3 checks, the compare pairs, the golden pages and the style maps;
  - an ADR 004 §12.6 amendment;
  - expected file and STOP rule before code.

---

## 2. The 8 majority-admitted allow-listed cells

These are DNA cells that the majority-of-leagues possession trigger fires on and the allow-list admits (|mean ρ| ≤ 0.55; ADR 004 §13.6 O1). They carry the "team-style influenced" note. M4g4 decided nothing for them, and M4g5 (deferred) was to inform the two `long_pass_completion_pct` cells.

**Options**
- **A.** Keep all 8 as DNA (allow-listed, with the note).
- **B.** Move the two `long_pass_completion_pct` cells (CB, DM-CM) to display; keep the 6 progression cells.
- **C.** Move all 8 to display.

**Evidence** (registry snapshot; measurements; `m4g6_weighting` "Leave-one-out"; ADR 004 §13.7 movers)

| Cell | r_full | Mean possession ρ (dur / pass) | Leagues ≥ 0.4 | Mover verdict (with flags) | Leave-one-out overlap |
|---|---|---|---|---|---|
| CB `long_pass_completion_pct` | 0.789 | 0.513 / 0.509 | 3 / 3 | inconclusive, f̂ 0.93 [0.06, 2.05]; underpowered at 15 | 7 / 5 |
| CB `progressive_carries_p90` | 0.879 | 0.506 / 0.549 | 3 / 4 | inconclusive, f̂ 0.25 [−0.11, 0.74] | 9 / 8 |
| CB `progressive_passes_p90` | 0.869 | 0.451 / 0.496 | 3 / 4 | inconclusive, f̂ 0.32 [−0.15, 1.04] | 9 / 7 |
| FB-WB `progressive_carries_p90` | 0.891 | 0.407 / 0.440 | 2 / 3 | as above (pooled) | 9 / 8 |
| FB-WB `progressive_passes_p90` | 0.845 | 0.510 / 0.511 | 4 / 4 | as above (pooled) | 8 / 6 |
| DM-CM `long_pass_completion_pct` | 0.795 | 0.506 / 0.515 | 3 / 3 | as above (pooled) | 8 / 6 |
| DM-CM `progressive_carries_p90` | 0.900 | 0.526 / 0.530 | 4 / 4 | as above (pooled) | 9 / 8 |
| DM-CM `progressive_passes_p90` | 0.889 | 0.469 / 0.472 | 3 / 3 | as above (pooled) | 8 / 6 |

- The progression cells are reliable (r 0.85–0.90). Their mover verdicts are inconclusive, with the point estimate below the 0.5 threshold (f̂ 0.25–0.32). Removing one changes little (overlap 8–9).
- `long_pass_completion_pct` leans team-driven: f̂ 0.93, lower CI bound 0.06, only 15 movers. It also carries the recorded length-truncation bias (ADR 004 §13.3). It matters more to the CB neighbours (overlap 7/5).
- **Interaction with item 1:** moving CB `long_pass_completion_pct` leaves CB Passing with one member (`long_passes_attempted_p90`, style), a new one-member family. The Passing score is already withheld in CB and DM-CM, so no score changes.

**Recommendation: A (keep all 8), revisit when M5 or M4g5 returns.**
- The pre-registered rule admitted them and none FAILs the mover test.
- Moving `long_pass_completion_pct` creates a new one-member family in CB and splits the metric by group: it stays DNA in FB-WB and AM-Winger.
- B is defensible on the mover point estimate. It would be argued from an inconclusive verdict, so I would not move on it now.

**What changes in the build**
- **A:** nothing. ADR 004 §14 records the decision and the revisit trigger.
- **B or C** (M4g6c):
  - registry overrides to display (or allow-list entries removed), with an ADR 004 note;
  - `registry_status` changes in the moved cells (an expected file and STOP rule first);
  - the DNA vector changes in CB and DM-CM (B), or in CB, FB-WB and DM-CM (C);
  - re-run percentiles, DNA, both references, M4f3, the compare pairs (C would move DM-CM cells: Busquets–Kanté re-reported), the golden pages and the style maps.

---

## 3. The FAIL list (current DNA metrics that FAIL the mover rule)

**Evidence** (`m4_validation` item 9, the mover table): 0 FAIL among the 34 verdicts. There are 9 PASS (one of them seed-sensitive, `pressures_p90`), 19 inconclusive and 6 untestable.

**Options:** A. confirm "no FAIL, nothing to remove"; B. nothing else applies.

**Recommendation: A.** **What changes:** nothing.

---

## 4. The Passing status name and the stale "until M4g" text

Today:
- Passing is withheld in CB, FB-WB, DM-CM and AM-Winger as `pending_mover_stability`, which since ADR 004 §13.9 means "xPass deferred, not tested";
- the comparison page reads "withheld until M4g (team-influenced; xPass candidate pre-registered)", which is stale once M4 closes.

**Options**
- **A.** Rename the status to `pending_xpass_deferred` and reword the text to "withheld (team-influenced; xPass deferred until after launch)".
- **B.** Keep the status string; reword the text only.
- **C.** Keep both as they are.

**Evidence:** plan 006 W1 builds the contract's player schema and `export/words.py`, which maps every withheld status to its tag and fails on an unknown code. So the status strings pass through the contract layer, and a rename after W1 touches the contract and its word tables. ADR 004 §13.9 already states the meaning.

**Recommendation: A**, before plan 006 W1 freezes the contract. The status name should say why the score is withheld.

**What changes in the build**
- **A** (M4g6c):
  - `dna.PENDING_MOVER_STABILITY` → `PENDING_XPASS_DEFERRED`, the config table, `tools/compare_page.py` and `spikes/m4g_compare.py` texts, the `m4f_dna_reference` constant and fixture, and the tests;
  - `player_dna_dimensions.parquet` `score_status` changes on the 1,075 withheld Passing rows (an expected file and STOP rule), with no vector change;
  - golden pages (text only) and style maps.
- **B:** only the two display texts, the golden pages and the style maps (no data change).
- **C:** nothing, and the stale text stays.

---

## 5. N1–N3 as accepted display rules

Already recorded in plan 006 W4 (user, 2026-10-09):
- N1: a CI next to every possession flag;
- N2: the wording "not shown to be free of team-possession effects" only;
- N3: withheld Defending is the expected state until a second season, not a negative finding.

**Options:** A. confirm as sign-off rules; B. amend.

**Recommendation: A.** **What changes:** nothing now; plan 006 W4 applies them.

---

## 6. Items out of M4 scope, to confirm

Each is recorded and none is built in M4.

| Item | Where it goes |
|---|---|
| log-OLS vs Poisson β̂, and a group-count gate in the β̂ fit (ADR 004 §13.8) | a later ADR, when a season with more data re-runs M4g4c |
| §31 treatment re-entry near a period end (Milinković-Savić, plan 005 backlog) | its own chunk with an ADR 002 change, before a new source or season |
| Second-season levers (movers: DM-CM Defending N3, the xPass mover power) | the next-season milestone |
| M5 z handling (C1 mixed-role rows dominate \|z\| > 5; C2 heavy tails; ADR 004 §12.8) | the M5 plan |
| Split-half self-retrieval for weighting | the M5 plan (user, 2026-10-09) |
| xPass (M4g5) | first post-launch feature (ADR 004 §13.9) |

**Options:** A. confirm all; B. pull an item into M4.

**Recommendation: A.** **What changes:** nothing.

---

## 7. Backlog triage (plan 005 "Backlog" and the M4g6 gated lists)

Proposed placement. Nothing is built by this triage.

**Must close before plan 006 (web v1):**
- `player_nickname` through the pipeline (backlog: "at the latest before M9"; web v1 is an early M9);
- human-readable registry reason codes (M9; the player page shows statuses);
- M4g1b compare-mode follow-ups: page size n² and the style-map run time, before W5 style maps.

**Before M5:**
- the M5 z handling (C1/C2);
- the DM-CM 6-share context column;
- empirical-Bayes shrinkage of ratios (re-evaluates `aerial_win_pct`).

**Before a new source or season** (fail-loud and robustness):
- §31 treatment re-entry;
- the M4g3a `season_id` filter;
- registry: a clear error for a malformed measurements file;
- the M4g4c follow-ups;
- the FB-WB throw-in share check;
- the M4g6a1 gated items (`first_dates` hard-coded season; dateless stints sorted first).

**Before the next run of the spike they belong to** (already gated): the M4g6a1 and M4g6a2 lists, the M4g4-0 follow-up, the M4g3a power follow-up and the M4g1 spike follow-ups.

**Stays in the feature backlog:**
- `op_crosses_attempted` (marked priority);
- headed finishing for strikers;
- pass value (xT / OBV);
- direct free-kick shots in `np_xg_per_shot`;
- second-ball creation;
- the xG-split refactor;
- history mode;
- hybrid players (M6).

**Options:** A. accept this placement; B. move items.

**Recommendation: A.** The three "before web v1" items become gated entries in plan 006.

**What changes:** plan 005 / 006 bookkeeping only.

---

## Summary of recommendations

| # | Item | Recommendation | Build change if picked |
|---|---|---|---|
| 1 | Family weighting | A accept, provisional until M5 (C in AM-Winger / FB-WB if you weigh Creation's reliability) | none |
| 2 | 8 majority-admitted cells | A keep as DNA; revisit at M5 / M4g5 | none |
| 3 | FAIL list | confirm 0 FAIL | none |
| 4 | Passing status / text | A rename to `pending_xpass_deferred` + reword | M4g6c: dimensions `score_status` on 1,075 rows, texts, golden pages |
| 5 | N1–N3 | confirm | none |
| 6 | Out of scope | confirm | none |
| 7 | Backlog triage | accept placement | bookkeeping |

With these picks, M4g6c is the item-4 rename only, followed by M4g6d (ADR 004 §14, the stats-reviewer's sign-off of the write-up, the milestone report).
