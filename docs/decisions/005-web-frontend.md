# ADR 005 — Web frontend v1: static React site on an exported data contract

## Status
**Accepted** (user, 2026-10-07), with these exceptions:
- the UI libraries (§4) are **pending**: chosen, but their licences and the bundle budget are verified in W2;
- the first public deploy is blocked by the StatsBomb licence gate (§8).

Decisions: §11. Plan: `docs/plans/006-web-v1.md`.

## Context
- **Request (user, 2026-10-07):** a real, shareable website now, rather than waiting for M9. It is built from what M4 already produces:
  - players and their season rows;
  - local and global percentiles;
  - the DNA vector and dimension scores;
  - the M4f neighbour distances;
  - the M4g1 style maps and the M4g1b compare facts.
- **What exists today** is three Python-rendered static pages:
  - `tools/compare_page.py`;
  - `spikes/m4g_style_map.py` with `m4g_style_page.py` and `m4g_compare.py`.

  Every number on them is precomputed and traced to a parquet cell, a registry field or a config label.
- **This is where the new site starts:** the site keeps the same rule, and moves the rendering to a React app.

Constraints carried over:

| Source | Constraint |
|---|---|
| CLAUDE.md | LLM = intent only; Python / SQL = truth; data/ is never committed; one milestone at a time; no infrastructure without a concrete requirement |
| ADR 004 §11.5, §12.9 | Display rules: >99 / <1, null percentiles show their status, ratio populations named, style and team-influenced metrics uncoloured, withheld families shown as withheld |
| ADR 004 §13.6 | Registry and DNA lists still change in M4g3–M4g6; one-member family weighting is an open decision before M4 sign-off |
| ADR 001 | StatsBomb Open Data under the StatsBomb Public Data User Agreement: non-commercial, attribution and the StatsBomb logo required; `LICENSE.pdf` not yet read directly |
| brief §18 | The frontend must not drive the architecture; React + TypeScript; Discover, Profile, Compare (Gems later) |

## 1. Architecture

```
data/processed/*.parquet ──► src/eleveniq/export/ ──► export/<contract_version>/*.json ──► frontend/ (React, Vite, TS)
   (M4 pipeline)              (Python: every number,     (static files, versioned,          (display only: fetch,
                               word and rank computed)    schema-validated, stamped)          validate, render)
```

- **Python computes; React displays.** The exporter (`src/eleveniq/export/`, a new package) reads the processed parquets and the registry. It writes JSON whose every number, percentile phrase, rank, distance, axis name and reason text is already final.
  - **The frontend does no arithmetic on data:** no distances, ranks, percentiles, sorting by a statistic, aggregation or rounding of statistics.
  - **It may only:** index, filter by an exported key, format an exported value with an exported format rule, and lay out.
  - This is the M4g1b "no math in JS" rule, applied to the whole site.
  - **One exception (user, 2026-10-09; plan 006 W1):** compare mode may order metrics for display.
    - The browser subtracts the two players' exported direction-adjusted league (local) percentiles and sorts by the absolute gap.
    - Ties break by registry metric order, which is exported.
    - Excluded (user, 2026-10-09, option c): metrics without a percentile, signature traits, metrics associated with team possession (`team_style_biased`) and long pass completion. Style metrics stay in. The exporter flags each metric `gap_eligible`.
    - It is used for display order only; nothing is shown from the subtraction.
    - A test compares the browser's order with Python's on the fixture.
- **Static JSON now, an API later (M7).** The frontend reads data through one module (`frontend/src/data/`). It exposes typed functions such as `getPlayer(key)`, `getNeighbours(key)` and `getStyleMap(group)`.
  - v1 implements them as `fetch` calls on the static files.
  - In M7 they call the FastAPI service, which serves the same contract. Pages and components do not change.
- **Logic moves into `src/` with tests, and the spikes stay as checks.**
  - The neighbour top-k, the compare facts (pair distance, ranks both ways, the 5 / 3 picked metrics, `pct_words`) and the style-map PCA (with the M4g2 axis pins) live today in spikes. They move to `src/eleveniq/analytics/` (style map, pairs) and `src/eleveniq/export/` (wording).
  - The existing spikes become `--check-output` references on the export: the same pattern as M4d and M4f.
  - **Dependency consequence:** scikit-learn and numpy become runtime dependencies (ADR 004 §13.5 anticipated this for M4g5).
- **Layering:** `export/` reads `analytics/` and the processed parquets. It never recomputes a statistic that `analytics/` or the pipeline owns.

## 2. Data contract (`contract_version` 1.0.0)
- **Format:** JSON Schema (draft 2020-12) files, committed under `contract/v1/`, are the single source of truth.
  - Python validates every export against them in tests and at export time, using `jsonschema` as a dev dependency.
  - The frontend validates every fetched file against the same files (Ajv) in tests and in development builds. Production validates the manifest only, for size and speed.
  - TypeScript types are generated from the schemas (`json-schema-to-typescript`). They are committed, and a test fails if they go stale.
- **Versioning:** semver on the contract.
  - A field addition is minor. A removal or a change of meaning is major, and gets a new `contract/v2/` and `export/v2/`.
  - The frontend declares the major version it reads and refuses others with a visible error.
- **Stamps (`manifest.json`, and repeated in every file):**
  - `contract_version`, `registry_version`, `measurement_source`, `source` (`statsbomb`), `season_ids`;
  - the sha256 of the season, percentiles and DNA parquets;
  - the exporter's git commit;
  - a sha256 per file.
  - There is no wall-clock timestamp, so two exports of the same data are byte-identical. Floats are rounded at a fixed precision, as the measurements file does.
- **Files (sharded so a page loads only what it shows; W1c measured 2015/16: 2,642 files, 77 MB raw in all, every file validated and byte-identical on re-run):**

| File | Content | Approx. size |
|---|---|---|
| `manifest.json` | stamps, file list with sha256, group and league labels, attribution text | < 10 KB |
| `search-index.json` | one entry per season row: key, display name, accent-folded search key (Kanté → kante), team, league, group, minutes, row status | ~250 KB |
| `groups/<group>.json` (W1c) | what every row of a group shares per metric, in registry order: label, family, direction, registry status, notes (short / full), reasons, colour and gap rules, withheld-family tag; the Defending note | ≈ 20 KB each, 6 files (5 groups + GK) |
| `players/<key>.json` | identity and context (display name, near-gate flag, fine role); each registry metric's row facts (value and text, local / global percentile with text, phrase, colour bin, n and population sentence, status); the second group's display-only rows; signature traits; the DNA vector; dimension scores with members and withheld texts; neighbours (below) | **W1c measured: mean 28 KB raw (≈ 2.9 KB gzip), largest 56 KB; 2,628 files** |
| `neighbours` (inside the player file) | top 10 in-group: key, name, team, league, distance (4 dp), rank, and the reasons (the 3 closest and 2 largest-gap metrics as worded phrases, from `pct_words`) | in the player file |
| ~~`compare/<group>.json`~~ | **Dropped (user, 2026-10-09):** compare mode has no distance and no pair data; it reads the two player files | — |
| `style-maps/<group>.json` | per point: PC1 and PC2 coordinates, fine role, lean, top-3 pushes per axis with worded phrases; partial rows; "Played for" and the movers; the axis names, pins, legend notes, variance shares, quoted loadings, the explainer text | **W1c measured: 0.19–0.35 MB per group** |
| `labels.json` | metric display names (including per-group wording such as ST "Dribbled past while pressing"), the registry reason codes as human text (plan 005 backlog, "M9: human-readable registry reason codes"), the status texts | < 30 KB |

- **Traceability, the M4e / M4g contract carried into React:**
  - Every rendered number carries a `data-src` attribute naming its contract path (for example `players/3244…json#metrics.interceptions_p90.pct_local`).
  - A Vitest helper and a Playwright check assert that the visible text equals the contract value under the exported format rule.
  - A digit outside a traced span fails, as in `m4g_compare.check_page`.
- **Mixed groups:** a distance and a compare exist only within one position group (ADR 004 §12). The contract offers no cross-group pair. The UI says so, as the style maps do.
- **Compare mode (user, 2026-10-09; plan 006 W1):**
  - same position group only; no distance or similarity score; no pair data in the contract;
  - `compare/<group>.json` above is dropped: compare reads the two player files, showing the top 6 metrics by gap (the §1 exception), then the full table;
  - the style map's mover panel keeps its distance and ranks: it compares one player across two clubs, not two players.

## 3. Pages (v1)
Wayfinding on every page: a persistent header with the site name, "Find similar players", "Style maps", the season selector (2015/16 only for now) and breadcrumbs. Labels are specific ("Similar players", "Style map: centre-backs"), never "Home".

1. **Find similar players** (the landing page).
   - Search box with accent-insensitive prefix and substring matching on the exported search key.
   - Then the 10 nearest players. Each shows its distance, rank and reasons: "most alike: … · most different: …".
   - Selecting one opens the compare panel (the two headers, distance, ranks both ways, the metric lists, the withheld-family tags, the map-distance warning).
   - **Label:** "Statistically similar: DNA distance within position group (provisional until the M5 similarity engine)".
2. **Player page.**
   - Identity and context; the DNA radar (shared-group rules from §12.9: gaps for missing metrics, style axes uncoloured); dimension scores with member counts and withheld status; percentile tables per family with the §11.5 display rules; signature traits as holder / non-holder.
   - Links to the player's style map position and to "Similar players".
3. **Style maps** (one per group): zoom and pan, hover cards, search-to-zoom, fine-role toggles, the explainer, compare mode. This is functional parity with the M4g1 / M4g1b pages, rendered from `style-maps/<group>.json` and `compare/<group>.json`.

Out of v1 (later milestones): Gem Finder (M6), natural-language search (M8), multi-season history, cross-source anything.

## 4. UI libraries (pending: chosen by the user, verified in W2)
**Choice (user, 2026-10-07):**
- **Components:** shadcn/ui on Base UI primitives, with Tailwind CSS.
- ~~**Motion:** for UI transitions only (chrome, panels, sheets), never for data values.~~ **Dropped (W0 decision 9, user 2026-10-09):** CSS transitions with Base UI's starting / ending-style attributes. **Condition:** before W5, confirm the style map's mover dot → stint dots expansion works with CSS / SVG transitions; if not, stop and bring it back to the user.
- **Radar:** a hand-written SVG component, ported from `tools/compare_page.py`.
- **Style map:** SVG with `d3-zoom` and `d3-scale`, a port of the current map logic (`spikes/m4g_style_page.py`).
- **Icons:** `lucide-react`.
- **Font:** `system-ui`.
- **Fallback** if the hand-written charts get heavy: visx.
- **Rejected:** ECharts (bundle size) and Recharts (default animations, weak zoom).

**Pending until W2:** every licence is verified against the rule below and recorded here, and the 200 KB gzip first-load budget is measured on the W2 shell. A failure comes back to the user before any substitution.

Requirements the choice must meet:
- **Licence:** MIT, ISC, BSD-2/3 or Apache-2.0 for anything shipped to the browser. Copyleft (GPL, LGPL, MPL) only in dev or test tooling, and named in the ADR.
- **Radar chart:** n = 15–25 axes; axis grouping by family; per-axis styling (uncoloured style axes, dashed spokes); missing values as gaps with a rim marker, never a vertex at zero; two series; no animation of values; a stable `data-src` hook on every vertex. A hand-written SVG component (as in `tools/compare_page.py`) is an acceptable answer.
- **Scatter:** 300 points (up to about 1,500 for future pools) at 60 fps; wheel, pinch and drag zoom/pan with a reset; hit-testing for hover and click; label culling by zoom level; keyboard access to points and an alternative list view; SVG or canvas with an accessible fallback.
- **General:** TypeScript types; tree-shakeable; no animation of data values by default (or fully disableable); theming through CSS variables (light and dark, high contrast); a bundle budget of ≤ 200 KB gzip of JS for the first load; maintained (a release within 12 months).
- **Component primitives** (dialogs, popovers, tabs): accessible (WAI-ARIA patterns, focus management) and unstyled or themeable.

## 5. Design
**Moved to `docs/DESIGN.md`** (plan 006 W0, user decisions 2026-10-09), which overrides any design skill on conflict. The rules first recorded here are carried into DESIGN.md: system font, tabular numerals, materials with solid fallbacks, instant feedback, reduced-motion / transparency / contrast, chrome-only transitions, no animated data values, colour never alone. Decision record: `docs/design/w0-design-plan.md`.

## 6. Deployment
- **Static hosting options:**

| Host | Fit |
|---|---|
| Cloudflare Pages | free; direct upload by CLI (no git connection needed); global CDN; custom domain; preview URLs |
| Netlify | free tier; direct deploy by CLI; similar features |
| Vercel | free hobby tier; CLI deploy; geared to Next.js but serves static builds |
| GitHub Pages | free for public repos only. The repo is private, so a paid plan would be needed |

- **How the exported JSON reaches the host without committing data/:**

| Option | How | For | Against |
|---|---|---|---|
| **A. Local build + direct upload** (recommended for v1) | `export` on the user's machine writes `export/v1/`, `npm run build` copies it into `dist/`, and the user runs the host's CLI upload. The manifest sha256s are printed and checked after upload | No data in git; simplest; the user controls every publish (matches "Claude never pushes") | Manual; the deploy is only as reproducible as the local data |
| B. Separate deploy repo or branch | The exported JSON is committed to a separate (private) deploy repo or an orphan branch that the host builds from | Deploy history; host-side CI builds | Derived data in git (only outside main); two repos to keep in step |
| C. CI regenerates from raw | CI downloads the StatsBomb raw files and runs the full pipeline, export and build | Fully reproducible from source | Slow (the raw download and events pass); the raw data is re-downloaded on every deploy; CI secrets and caching |
| D. Object storage + CDN | JSON uploaded to a bucket (e.g. Cloudflare R2); the site fetches from the bucket URL | Data and code deploy separately; JSON can update without a frontend rebuild | One more service; CORS; cache invalidation |

- **Release check (any option):** the deployed `manifest.json` must equal the local one (sha256 per file). A smoke script fetches the manifest and two player files from the live URL.

## 7. Testing
- **Python:**
  - schema validation of every exported file;
  - the spikes' `--check-output` on the export (independent re-derivation);
  - byte-identical re-export;
  - a fixture export: a small, fixed subset (≈ 25 players across the 5 groups, including Kanté, Busquets, Benzema, a partial-DNA row, a mover and a below-gate row) written to `tests/fixtures/web/`, committed.
- **Frontend unit (Vitest):**
  - the data module (fetch, validate, version refusal);
  - formatting helpers (they format exported values; they never compute them);
  - components rendered against the fixture JSON, with the trace check (`data-src` text equals the contract value).
- **Contract tests on both sides:** the same `contract/v1/*.schema.json` files validate the fixture JSON in pytest and in Vitest. The generated TS types match the schemas.
- **End to end (Playwright):**
  - It always runs against `tests/fixtures/web/`, never against data/, so M4 data changes cannot break it. The fixture JSON changes only when the contract changes, with `--update` style regeneration and a diff summary, like the golden pages.
  - **Flows:**
    1. search "Kante" → Kanté's 10 neighbours with reasons → open the compare panel with one of them → the distance and ranks equal the fixture;
    2. style map: hover a point (card text equals the fixture), search a player (zoom lands on the point), zoom in / out and reset, compare mode;
    3. player page: the radar vertices and percentile cells trace to the fixture;
    4. accessibility: axe (`@axe-core/playwright`, MPL-2.0, dev only) on every page with no serious or critical violations, plus a keyboard-only pass of flow 1;
    5. `prefers-reduced-motion` and `prefers-contrast` emulation: no transitions, solid surfaces.
  - **Failure artefacts:** screenshots and traces kept on failure.
- **Agents and `/finish-chunk`** (changed in the first frontend chunk, with the user's approval):
  - `.claude/agents/qa-tester.md`: when `frontend/` changes, the qa-tester also writes and runs Playwright tests from the spec (this ADR, the contract) and reports failures with screenshots and trace paths.
  - `/finish-chunk`:
    - qa-tester runs when `src/`, `tools/` or `frontend/` logic changes;
    - Playwright runs whenever `frontend/` or `contract/` / `src/eleveniq/export/` changes;
    - Vitest runs with the fast tests whenever `frontend/` changes;
    - the stats-reviewer runs when the export computes something new (a new reason rule, a new ranking), not for pure display.

## 8. Attribution and licence
- **Every page footer:** "Data: StatsBomb Open Data", with the StatsBomb logo as the user agreement requires and a link to the repository and agreement.
  - It also states that the site is a non-commercial portfolio project, and gives the data season and the registry version.
- The about page explains the methodology in plain words and links to the ADRs.
- **Gate before any public deploy:** read the StatsBomb `LICENSE.pdf` (the Public Data User Agreement) directly and record its terms in ADR 001.
  - It must confirm public hosting of derived per-player statistics and downloadable derived JSON, and the logo and attribution rules.
  - Until then, deploys are private previews only (password or unlisted).

## 9. Fit with the remaining M4 chunks
- **M4g3–M4g6 change data, not the contract.**
  - New Defending or Passing members, restored dimension scores, new `score_status` values or a weighting decision all regenerate the export: same `contract_version`, new `registry_version` stamp.
  - The frontend is unchanged, and the Playwright fixtures are unchanged.
  - The fixture export is regenerated only on a contract change, and its diff is reviewed like a golden page.
- **Status codes and display wording live in `labels.json`.** A new withheld or failure status (`possession_criteria_failed`, `mover_stability_failed`) needs a text entry there. Its absence fails the export (unknown codes raise, as the M9 backlog item asks).
- **Style-map axis names, pins and notes stay the user's.** A DNA change that breaks a quoted loading fails the export, as it fails the map spike today.
- **The site does not sign off M4.** The provisional labels ("provisional until M5", withheld families) stay until the corresponding milestones close.

## 10. Consequences
- New top-level folders: `frontend/` (Node app; lockfile committed; `node_modules/` and `dist/` ignored), `contract/v1/`, `export/` (git-ignored, like data/), `tests/fixtures/web/`.
- New Python runtime dependencies: numpy and scikit-learn. New dev dependency: jsonschema.
- A Node toolchain (version pinned in `.nvmrc`) becomes part of the project setup. CLAUDE.md Commands gains the export, frontend dev, build, test and deploy commands.

## 11. Decisions (user, 2026-10-07)
1. **Milestone order: (a).** W1 starts after M4g6 sign-off, as an early M9 before M5. `docs/milestones.md` is updated when W1 starts.
   - *Rejected:* (b) interleaving with the M4 chunks; (c) pausing M4.
2. **UI libraries: pending** (§4). Chosen, but the licences and the 200 KB budget are verified in W2.
3. **Hosting: option A** (§6). Build locally; the user runs the CLI upload; the live `manifest.json` is checked against the local one after every upload. The host, Cloudflare Pages or Vercel, is decided in W6.
4. **StatsBomb licence: a hard requirement.** The agreement is read before any public deploy, and only private previews are allowed until then. It is a blocking checklist item in plan 006 W6.
5. **Package manager: npm.**
6. **Domain:** the host's subdomain for now; an own domain later.
