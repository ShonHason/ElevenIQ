# W0 — Design plan (decided by the user 2026-10-09)

**Decisions 1–14 approved (user, 2026-10-09), with these conditions:**
- **9, drop Motion:** before W5, confirm the mover dot → stint dots expansion works with CSS / SVG transitions; if not, stop and bring it back to the user.
- **11:** the "Fewer minutes: less reliable" chip appears only for near-gate CB and FB-WB profiles (900–1,350 nominal minutes), where it was measured. The test is pre-registered for W1, where the exported flag is first computed: `tests/test_export_words.py::test_near_gate_chip_only_cb_fbwb_900_1350`, true exactly for CB / FB-WB rows with 900 ≤ nominal < 1,350, false for every other group or minutes. There is no code to test before W1.
- **13 / 14, licences checked:**
  - the Vercel Web Interface Guidelines are MIT (© 2025 Vercel Labs). They are pinned at commit `434b7f9` in `docs/design/web-interface-guidelines.md`, with the licence text kept;
  - the frontend-design list is Apache-2.0; its rules are restated in our own words in `docs/DESIGN.md` §10, with attribution.
- **Preview:** `docs/design/preview.html` (the colour scale, surfaces, a sample percentile table normal and compact, all status chips, light and dark). Screenshots were reviewed before the commit. The preview check changed two details, now in DESIGN.md §4:
  - an around-average bar uses a darker neutral (`#bdbbb4` / `#6b6a64`) on an outlined track; the pale swatch was invisible;
  - style metrics are a hollow bar, distinct by shape.
- **Tools installed:** Playwright CLI (`@playwright/cli`, dev, already present: `package.json`, lockfile; `node_modules/` ignored). Nothing else.

Plan 006, before W1. Read-only research and one decisions document. No code, installs or CLAUDE.md edits until approved. On approval: install only the approved tools (project-local), then one commit with `docs/DESIGN.md` (§4, finalised), this plan with the decisions, the plan 006 changes (§7) and the CLAUDE.md lines (§8).

---

## 1. Inventory

### 1.1 What the plan already fixes (ADR 005, plan 006)
- **Stack:**
  - a static React site (Vite, TypeScript strict) on a versioned, schema-validated JSON export;
  - Python computes, React displays: no arithmetic on data in the browser;
  - every number carries `data-src` and is traced (Vitest and Playwright).
- **Pages (v1):**
  - Find similar players (the landing page: search, 10 neighbours with reasons, compare panel);
  - Player page (identity, DNA radar, dimension scores, percentile tables, signature traits);
  - Style maps (zoom and pan, hover cards, search-to-zoom, fine-role toggles, compare mode, biplot toggle, radar glyphs on zoom, list view).
- **Libraries (ADR 005 §4, pending W2 verification):**

  | Library | Licence | Notes |
  |---|---|---|
  | shadcn/ui on Base UI primitives, with Tailwind CSS | MIT | |
  | Motion | MIT | UI transitions only |
  | `d3-zoom`, `d3-scale` | ISC | |
  | `lucide-react` | ISC | |
  | hand-written SVG radar | — | port of `tools/compare_page.py` |
  | `system-ui` font | — | |
  | visx | MIT | fallback |

  The budget is ≤ 200 KB gzip of first-load JS.
- **Honesty rules carried in:**
  - ADR 004 §11.5 display rules (>99 / <1, null status texts, ratio populations, basis);
  - N1: a CI next to every possession flag;
  - N2: "not shown to be free of team-possession effects" wording only;
  - N3: the second-season clause only for the mover-test groups;
  - plan 006 W4: near-gate CB / FB-WB reliability marks and same-team neighbour marks;
  - the W4 / W5 gate: every withheld text comes from the single words source (`registry.REASON_TEXT`, `export/words.py`);
  - pending: the "influenced" relabel at W1, checked text by text by the stats-reviewer.

### 1.2 Pages rendered today
Screenshots were taken with Playwright CLI at 1440×900 and 390×844, light and dark, from a local server. They are in the session scratchpad and not committed. To regenerate: render the page, serve the folder, `playwright-cli open … ; screenshot`.

| Page | Works | Reads badly |
|---|---|---|
| **Comparison page** (`tools/compare_page.py`, golden pages) | <ul><li>Every value traced</li><li>Honest status texts (withheld reasons, D9, >99)</li><li>Local and global shown side by side with n</li><li>Dark mode exists</li><li>Tabular structure per family</li></ul> | <ul><li>Two paragraphs of method text before any data</li><li>Full legal names ("Sergio Busquets i Burgos"); G1 now provides nicknames</li><li>Each cell stacks value, bar, local and global lines and a population sentence, so rows are tall and repetitive ("n 67 of 67 DM-CMs with ≥ 39 long passes" ×2)</li><li>The notes column is long free text</li><li>The radar has 25 rotated labels, is small, and its legend sits far above it</li><li>On mobile: no navigation, a very long single column, and the radar is not usable</li><li>Several generic-page tells: ALL-CAPS section labels ("DNA METRICS"), meta strings joined with middle dots, a warm cream background</li></ul> |
| **Style maps** (`spikes/m4g_style_map.py`) | <ul><li>Clear scatter</li><li>Axis names in plain words with arrows</li><li>The "position is style, not quality" message</li><li>The map-distance warning</li><li>Mixed-role markers by shape and colour</li><li>Dark mode</li></ul> | <ul><li>The open explainer (about 600 px) pushes the map below the fold on desktop and fully off the first two screens on mobile</li><li>The stat tiles (50% / 31% / 19%) repeat the explainer</li><li>The loadings table sits below the map with technical wording ("component coefficients")</li><li>One label only; the plain blue dots don't say what blue means without the legend</li></ul> |

### 1.3 Design skills (installed or available)

| Skill | Status | Its rules, in short | Where it would pull us |
|---|---|---|---|
| **apple-design** (project, MIT, Emil Kowalski) | installed; CLAUDE.md uses it selectively | <ul><li>respond on pointer-down</li><li>no input lock during transitions</li><li>springs (damping 1.0 default, bounce only after a flick)</li><li>velocity handoff and momentum projection</li><li>rubber-banding</li><li>symmetric enter / exit paths</li><li>translucent materials with solid fallbacks</li><li>reduced-motion / transparency / contrast honoured</li><li>typography: system font, size-specific tracking</li></ul> | Toward gesture physics and springs everywhere, which is wrong for data values. Useful for polish, panels, sheets and typography. |
| **dataviz** (built in) | available | <ul><li>form first, colour last</li><li>colour by job: categorical, sequential, diverging (neutral gray midpoint), status</li><li>run the palette validator, don't eyeball</li><li>one axis, thin marks, legends for ≥ 2 series</li><li>text in text tokens, never series colour</li><li>reserved status colours with icon + label</li><li>a table view always exists; dark mode is its own validated steps</li></ul> | Correct for every chart and colour scale here. It leads colour and charts. |
| **frontend-design** (Anthropic plugin) | available, not installed | <ul><li>a deliberate, distinctive identity</li><li>deliberate typeface choice</li><li>avoid generated-page tells (cream + serif + terracotta, ALL-CAPS eyebrows, middle-dot meta strings, the identical-card kit, "→" links)</li><li>one memorable element, everything else quiet</li><li>motion only when it answers an action</li><li>plan tokens, then critique the plan before building</li></ul> | Toward a bold identity and a custom font, which conflict with our system font and data density. Its anti-default list is useful and adopted as a review list. |
| **gsd-ui-phase / gsd-ui-review / gsd-sketch** (user-level GSD) | installed | <ul><li>a UI-SPEC contract per phase</li><li>a 6-pillar visual audit</li><li>throwaway HTML sketches</li></ul> | Toward the GSD `.planning/` workflow, which this project does not use. Not used. |
| **artifact-design** | built in | claude.ai artifacts only | Not applicable. |

### 1.4 Libraries in the plan, against these needs
- **shadcn/ui on Base UI:** accessible primitives (dialog, popover, tabs, tooltip, toggle group, select); styled by us; no visual identity forced. Fits.
- **Tailwind:** tokens as CSS variables mapped into the theme; fits our token system.
- **Motion:** only for panel and sheet enter / exit. Base UI already exposes `data-starting-style` / `data-ending-style` for CSS transitions. See decision 9.
- **`d3-zoom` / `d3-scale`:** the style map (pan, zoom, pinch, reset); scales only for layout, never for computing statistics.
- **`lucide-react`:** icons for status (withheld, reliability, same team, info).
- **Hand-written SVG radar:** a port of the current radar with gaps and dashed style spokes. Fits.

---

## 2. Candidates

| Candidate | Verdict | Reason |
|---|---|---|
| **Playwright CLI** (`@playwright/cli`, Apache-2.0; installed by the user) | **Keep, dev only** | Agent screenshots for your end-of-W-step visual reviews (light / dark, desktop / mobile); verified working here. It is separate from `@playwright/test`, which W2 adds for the e2e suite. |
| **vercel-labs web-design-guidelines** | **Keep, as a pinned copy, not the live skill** | Its SKILL.md fetches `vercel-labs/web-interface-guidelines/main/command.md` from the internet at every run: unpinned, changing rules from a third party at review time. A dated copy in `docs/design/` (licence to confirm) used as the end-of-W-step accessibility / UX checklist gives the value without the moving target. |
| **taste-skill** (Leonxlnx) | **Reject (confirmed)** | Its SKILL.md defaults to "React or Next.js … Server Components … Tailwind v4 … Motion", VISUAL_DENSITY 4 (of 10), DESIGN_VARIANCE 8. It infers the brief and sets its dials itself, and its scope explicitly excludes "dashboards, dense product UI, data tables". **Refuted in part:** it does not default to Radix; shadcn/ui is one option among design systems. |
| **awesome-design-md** (VoltAgent, MIT) | **Reject as a dependency; browse only** | A collection of DESIGN.md files extracted from real brands' public CSS (Stripe, Vercel, Spotify …). Useful as format examples; copying one would import a brand. We write our own `docs/DESIGN.md`. |

---

## 3. Who leads which concern

| Concern | Lead | Notes / conflicts resolved |
|---|---|---|
| Data display: tables, numbers, percentiles, statuses, honesty wording | **Our rules** (ADR 004 §11.5, N1–N3, words table, `docs/DESIGN.md`) | Win over every skill. |
| Colour scales, charts, legends, CVD safety | **dataviz** | Its validator gates every palette. Its blue↔red diverging pair is replaced by blue↔orange (§4.3: red collides with the status "critical" colour and reads as "bad" on a style metric). |
| Typography, materials, feedback, panel motion, accessibility settings | **apple-design** | Only the polish parts. Springs and gestures only on the style map's pan / zoom (library defaults). Never on data values (CLAUDE.md). |
| Avoiding generic-page tells; restraint | **frontend-design** (as a review list, not installed) | Conflict: it wants a distinctive typeface. We keep `system-ui` (ADR 005, zero font weight, native numerals); distinctiveness comes from the data views. |
| Accessibility / UX audit | **web-design-guidelines (pinned copy)** + axe in Playwright | At the end of each W step. |
| Process | **CLAUDE.md speed rules** | No GSD UI workflow. |

**Precedence:** `docs/DESIGN.md` overrides any design skill on conflict. A skill's advice applies only inside the concern it leads.

---

## 4. Draft `docs/DESIGN.md`

> Becomes `docs/DESIGN.md` on approval, with your decisions applied.

### 4.1 Principles
1. **Data first.** The first screen of every page shows data, not method text. Method text sits behind an "About these numbers" disclosure, collapsed by default.
2. **Honest by construction.** Every number is traced; every withheld or uncertain value says so in words from the words table; colour never carries meaning alone.
3. **Calm.** One accent, neutral surfaces, no decoration that encodes nothing. Motion only answers a user action.
4. **Dense, not cramped.** Tables carry many numbers at tabular figures with a clear row rhythm; a compact mode exists for experts.

### 4.2 Tokens

**Colour: UI neutrals** (the dataviz reference surfaces; not cream):

| Token | Light | Dark |
|---|---|---|
| `--surface-0` (page) | `#fcfcfb` | `#141413` |
| `--surface-1` (card, chart) | `#ffffff` | `#1a1a19` |
| `--surface-2` (row hover, chip) | `#f3f2ef` | `#232321` |
| `--line` (hairline) | `#e4e3df` | `#33332f` |
| `--ink` (text primary) | `#0b0b0b` | `#ffffff` |
| `--ink-2` (secondary) | `#52514e` | `#c3c2b7` |
| `--ink-3` (muted, captions) | `#6e6d69` | `#9a998f` |
| `--accent` (focus ring, selection, links) | `#2a78d6` | `#3987e5` |

**Status (reserved; always with an icon and a label):** info / withheld = `--ink-2` + an icon; caution (reliability, near-gate) = `#c98500`; never the data colours.

**Type:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`; `font-variant-numeric: tabular-nums` on every number.

| Step | Size / line height | Weight | Tracking |
|---|---|---|---|
| Display (page title) | 24 / 30 | 600 | −0.01em |
| Heading | 17 / 24 | 600 | −0.005em |
| Body | 14 / 20 | 400 | |
| Table | 13 / 18 | 400 (values 500) | |
| Caption | 12 / 16 | 400, `--ink-3` | |

No ALL-CAPS labels, no middle-dot meta strings (use separate fields), no "→" in link text.

**Spacing:** a 4 px base; scale 4 / 8 / 12 / 16 / 24 / 32 / 48; page gutter 16 px (mobile), 24 px (desktop); max content width 1200 px; tables may go full width.

**Radius:** 6 px controls, 8 px cards and panels, 999 px chips. Hierarchy comes from spacing and lines, not from cards everywhere.

**Elevation:** hairlines over shadows. One shadow for floating layers (popover, sheet): `0 8px 24px rgb(0 0 0 / .12)` light, `0 8px 24px rgb(0 0 0 / .5)` dark. The header is translucent (`backdrop-filter`) with a solid fallback under `prefers-reduced-transparency`.

### 4.3 The percentile colour scale (one scale, every chart and bar)
Diverging around "around average", in five bins aligned with the exported percentile phrases. **Validated** with the dataviz script:
- each arm passes as a one-hue ordinal ramp in light and dark;
- the poles separate by CVD ΔE 23.7 (light) and 26.8 (dark);
- the light inner steps sit under 3:1 contrast, so the relief rule applies: every bar prints its value.

| Bin (direction-adjusted percentile) | Light | Dark |
|---|---|---|
| < 10 | `#b5501c` | `#d95926` |
| 10–40 | `#f0a07a` | `#8f3d14` |
| 40–60 (around average, neutral) | `#f0efec` | `#383835` |
| 60–90 | `#86b6ef` | `#184f95` |
| > 90 | `#256abf` | `#3987e5` |

- **Direction-adjusted:** a lower-better metric is coloured by 100 − p, so blue always means "more of the good end".
- **Style metrics stay neutral gray at every value**, labelled "style" in text; the existing rule.
- **Withheld or null:** no bar; italic `--ink-2` reason text from the words table.
- The bin comes from the exported percentile phrase; the frontend never computes it.

### 4.4 Tables
- **Rows:** default 36 px, compact 28 px.
- **Numbers:** right-aligned with tabular figures; percentile shown as a number plus a 48 px inline bar in the scale colour.
- **Local and global** are two columns, not two stacked lines. The basis column is marked with a small "basis" chip.
- **Population sentences** ("n 67 of 67 DM-CMs with ≥ 39 long passes") move to a tooltip on the n cell and to the column header.
- **Notes:** an info icon with a popover, not a free-text column.
- **Sticky first column and header;** horizontal scroll inside the table on mobile, never on the page.

### 4.5 Compact mode
A header toggle, kept in `localStorage`. It sets 28 px rows, hides the inline bars (numbers only) and collapses captions. It never hides a status, flag or reliability mark.

### 4.6 Motion
- Only on UI chrome: panels and sheets slide 160–200 ms `ease-out` and leave along the same edge; popovers fade and scale from their trigger in 120 ms; pressed states respond on pointer-down in 100 ms.
- **Never** on numbers, bars, radar vertices, points or map positions. No count-ups, springs with overshoot, staggered reveals or idle animation.
- `prefers-reduced-motion`: every transition becomes instant. The style map's zoom and pan follow the pointer with no easing.

### 4.7 Components → shadcn / Base UI

| Need | Component |
|---|---|
| Search with keyboard results | Combobox (Base UI) |
| Compare panel | Sheet (Dialog), right on desktop, bottom on mobile |
| Notes, population, CI details | Popover; Tooltip only for short labels |
| Families on the player page | Tabs (mobile) / sections (desktop) |
| Fine-role and theme toggles | Toggle group |
| Compact mode, theme | Switch, Select |
| Status chips (withheld, near-gate, same team, provisional) | Badge (our variant set, icon + text) |
| Tables | plain `<table>` with our styles (no data-grid library) |
| Radar, style map, bars | hand-written SVG + `d3-zoom` / `d3-scale` |
| Icons | `lucide-react` |

### 4.8 Accessibility
- WCAG AA text contrast; visible focus ring (`--accent`, 2 px, offset 2 px);
- keyboard paths for search, results, compare and map points (the list view);
- axe with no serious or critical violations;
- `prefers-contrast: more` swaps to solid surfaces and 1 px darker lines;
- forced-colours mode keeps the bars visible by their printed numbers.

---

## 5. Page hierarchy

| Page | First screen (desktop) | Then | Mobile |
|---|---|---|---|
| **Search** (in the header on every page; the landing state of Similar players) | Search field with instant results (name or nickname, team, league, group) | Recent searches (`localStorage`) | Full-width field; results as a list |
| **Similar players** (landing) | The selected player's identity strip; 10 neighbours as a table (rank, name, team, league, distance, "most alike / most different" phrases; same-team chip; provisional label) | Compare sheet on row select | Neighbours as cards with the two phrases; compare as a bottom sheet |
| **Player page** | Identity strip (name, team, league, group, minutes, near-gate chip if any); DNA dimension scores row (with withheld chips and N1 / N2 / N3 texts); radar | Percentile tables per family (tabs or sections); signature traits; links to Similar players and the map point | Dimension scores, then family tables; radar replaced by family-grouped bars (decision 6) |
| **Compare** (sheet or full page) | Two identity strips; distance and ranks both ways; the 5 "most different" and 3 "most alike" metrics | Side-by-side family tables; the radar with both series | Stacked; player tabs A / B for tables |
| **Style map** | The map at full width; axis names on the edges; a one-line caption ("Position shows style, not quality") | "How to read this map" collapsed; legend; biplot toggle; list view | Map first, filling the viewport; explainer and legend in a bottom sheet |

---

## 6. Pre-registered decisions

1. **Skill leadership (§3).**
   - A: as proposed: our rules for data, dataviz for colour and charts, apple-design for polish, frontend-design as a review list.
   - B: amend.
   - **Recommendation: A.**
2. **Typeface.**
   - A: `system-ui` stack (ADR 005), no web font.
   - B: one open-licence font (e.g. IBM Plex Sans, ≈ 40 KB).
   - **Recommendation: A.** Zero bytes, native tabular numerals; distinctiveness comes from the data views.
3. **Surfaces.**
   - A: neutral near-white / near-black (§4.2).
   - B: keep the current warm cream.
   - **Recommendation: A.** Cream is a generated-page tell and tints the data colours.
4. **Percentile scale (§4.3).**
   - A: the validated 5-bin blue↔orange diverging, direction-adjusted, style neutral.
   - B: one-hue sequential blue.
   - C: the dataviz blue↔red pair.
   - **Recommendation: A.** The page reads "below / around / above average" at a glance; C collides with the status "critical" colour.
5. **Accent.**
   - A: blue `#2a78d6` / `#3987e5` for focus, selection and links (the same hue as "above average", but never on a data mark).
   - B: a separate non-data hue.
   - **Recommendation: A.** One calm accent; data marks always carry their numbers.
6. **The radar on small screens.**
   - A: radar from ≥ 768 px; below, family-grouped percentile bars with the same data.
   - B: radar everywhere.
   - **Recommendation: A.** 15–25 rotated labels are unreadable at 390 px.
7. **Table layout (§4.4).**
   - A: local and global as two columns, population sentences in tooltips, notes as popovers.
   - B: keep the stacked cell of today.
   - **Recommendation: A.** It roughly halves row height; nothing is lost because every value stays traced.
8. **Compact mode (§4.5).**
   - A: yes, a header toggle.
   - B: no.
   - **Recommendation: A.**
9. **Motion library.**
   - A: drop Motion; use CSS transitions with Base UI's starting / ending-style attributes. This is an ADR 005 §4 change and saves about 30 KB.
   - B: keep Motion for sheets.
   - **Recommendation: A.** Our motion is short `ease-out` on chrome only; springs aren't needed.
10. **Style-map explainer.**
    - A: collapsed by default, with a one-line caption above the map; the explainer, legend and loadings in a disclosure or bottom sheet.
    - B: open as today.
    - **Recommendation: A.** The map is the content.
11. **Status chips (wording from the words table, checked by the stats-reviewer at W1).**
    - A: icon + short text chips: "Withheld", "Fewer minutes: less reliable" (near-gate CB / FB-WB), "Same team", "Provisional" (the similarity label), with the full reason in a popover.
    - B: text only.
    - **Recommendation: A.**
12. **Theme.**
    - A: follow the system, plus a manual light / dark toggle kept in `localStorage`.
    - B: system only.
    - **Recommendation: A.**
13. **Tools (§2).** Install Playwright CLI as a dev dependency (already present: `package.json` / lockfile into the W0 commit, `node_modules/` ignored); a pinned copy of the web-interface guidelines (licence checked first) as the W-step audit checklist; reject taste-skill; don't install awesome-design-md.
    - A: as listed.
    - B: amend.
    - **Recommendation: A.**
14. **frontend-design plugin.**
    - A: don't install; its anti-default list is copied into `docs/DESIGN.md` §4.2 as review rules.
    - B: install it.
    - **Recommendation: A.** Installed, it pushes a distinctive identity and custom fonts, against decision 2.

---

## 7. Changes to plan 006 (on approval)
- **New W0 entry:** this plan; `docs/DESIGN.md` is the design source for W2–W5.
- **ADR 005:**
  - §4: Motion removed (decision 9);
  - §5: design rules move to `docs/DESIGN.md`, and §5 becomes a pointer.
- **W2:**
  - the shell implements the `docs/DESIGN.md` tokens (light / dark / contrast / compact) and the status chip set;
  - `@playwright/test` and axe as planned;
  - Playwright CLI already present for screenshots.
- **W3:** neighbour table per §5, same-team chip, provisional chip.
- **W4:**
  - mobile radar → family bars (decision 6);
  - table layout per §4.4;
  - chips per decision 11.
- **W5:** explainer collapsed (decision 10); map-first mobile layout.
- **Each W step ends with:** Playwright CLI screenshots (desktop / mobile, light / dark) for your visual review, plus the pinned guidelines checklist and axe.

## 8. Proposed CLAUDE.md changes (exact lines, on approval)
The "Design (frontend)" section is replaced by:

```markdown
### Design (frontend)
- UI work follows docs/DESIGN.md. It overrides any design skill on conflict.
- Skill leadership: our rules (ADR 004 §11.5, N1–N3, the words table, docs/DESIGN.md) lead
  data display; the dataviz skill leads colour scales and charts (every palette passes its
  validator); apple-design leads typography, materials, feedback and panel motion (skip its
  'Initial Response' greeting); frontend-design's anti-default list is a review checklist only.
- Data must stay readable: no springy or animated data values. Numbers, percentiles, bars,
  radar vertices and map points render at their final value: no count-up, spring or overshoot.
- User-facing text comes only from the words table (registry.REASON_TEXT, export/words.py).
  No copied strings in pages or components.
- New design skills or UI libraries need my approval before install.
```
