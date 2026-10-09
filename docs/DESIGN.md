# ElevenIQ — DESIGN.md

The design source for the web frontend (plan 006 W2–W5). Decided in W0 (`docs/design/w0-design-plan.md`, user decisions 2026-10-09). **This file overrides any design skill on conflict** (CLAUDE.md, Design). User-facing text comes only from the words table (`registry.REASON_TEXT`, `export/words.py`). Labels quoted here are placeholders until W1 fixes them there.

Visual reference: `docs/design/preview.html`: the colour scale, surfaces, a sample percentile table (normal and compact) and every status chip, in light and dark.

## 1. Principles
1. **Data first.** The first screen of every page shows data, not method text. Method text sits behind "About these numbers", collapsed by default.
2. **Honest by construction.**
   - Every number is traced (`data-src`).
   - Every withheld or uncertain value says so in words from the words table.
   - Colour never carries meaning alone.
3. **Calm.** One accent, neutral surfaces, no decoration that encodes nothing. Motion only answers a user action.
4. **Dense, not cramped.** Tabular figures, a clear row rhythm, and a compact mode for experts.

## 2. Who leads which concern

| Concern | Lead |
|---|---|
| Data display: tables, numbers, percentiles, statuses, wording | **This file + ADR 004 §11.5, N1–N3, the words table** |
| Colour scales, charts, legends, CVD safety | **dataviz** skill; every palette passes its validator |
| Typography, materials, feedback, panel motion, accessibility settings | **apple-design** skill, the polish parts only. No springs on data and no gesture physics, except the map's pan / zoom. |
| Avoiding generic-page tells | §9 below (our own words) |
| Accessibility / UX audit | `docs/design/web-interface-guidelines.md` (pinned) + axe, at the end of each W step |

## 3. Tokens

### 3.1 Colour: UI (surfaces are neutral, not cream)

| Token | Light | Dark |
|---|---|---|
| `--surface-0` page | `#fcfcfb` | `#141413` |
| `--surface-1` card, chart | `#ffffff` | `#1a1a19` |
| `--surface-2` row hover, chip | `#f3f2ef` | `#232321` |
| `--line` hairline | `#e4e3df` | `#33332f` |
| `--ink` text | `#0b0b0b` | `#ffffff` |
| `--ink-2` secondary | `#52514e` | `#c3c2b7` |
| `--ink-3` captions | `#6e6d69` | `#9a998f` |
| `--accent` focus, selection, links | `#2a78d6` | `#3987e5` |
| `--caution` reliability icon | `#9a6500` | `#d9a23a` |

- **The accent is never used on a data mark.**
- **Theme:** follows the system, with a manual light / dark toggle kept in `localStorage`. Dark values are declared under `@media (prefers-color-scheme: dark)` and under `[data-theme="dark"]`. The toggle wins both ways.

### 3.2 Type
- **Font:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`. No web font.
- **Numbers:** `font-variant-numeric: tabular-nums` on every number.

| Step | Size / line height | Weight | Tracking |
|---|---|---|---|
| Display | 24 / 30 | 600 | −0.01em |
| Heading | 17 / 24 | 600 | −0.005em |
| Body | 14 / 20 | 400 | |
| Table | 13 / 18 | 400 (values 500) | |
| Caption | 12 / 16 | 400 | |

- **Case:** sentence case for headings, buttons and labels.

### 3.3 Space, radius, elevation
- **Spacing:** a 4 px base; scale 4 / 8 / 12 / 16 / 24 / 32 / 48; gutter 16 px (mobile), 24 px (desktop); content max 1200 px; tables may run full width.
- **Radius:** 6 px controls, 8 px cards and panels, 999 px chips.
- **Elevation:** hairlines rather than shadows. One shadow for floating layers: `0 8px 24px rgb(0 0 0 / .12)` (dark `/ .5`). The header is translucent (`backdrop-filter`), solid under `prefers-reduced-transparency`.

## 4. The percentile colour scale
One scale for every bar, cell and chart that shows a percentile. It is diverging around "around average", in five bins aligned with the exported percentile phrases.

**Validated with the dataviz script:**
- each arm passes as a one-hue ordinal ramp, light and dark;
- the poles separate by CVD ΔE 23.7 (light) and 26.8 (dark);
- the light inner steps are under 3:1 contrast, so the relief rule applies: every bar prints its value.

| Bin (direction-adjusted percentile) | Light | Dark |
|---|---|---|
| < 10 | `#b5501c` | `#d95926` |
| 10–40 | `#f0a07a` | `#8f3d14` |
| 40–60 around average (neutral) | `#f0efec` | `#383835` |
| 60–90 | `#86b6ef` | `#184f95` |
| > 90 | `#256abf` | `#3987e5` |

- **Direction-adjusted:** a lower-better metric is coloured by 100 − p, so blue always means "more of the good end".
- **Bars vs heat cells:** the neutral swatch (`#f0efec` / `#383835`) is for heat cells. An around-average **bar** uses `#bdbbb4` (light) / `#6b6a64` (dark), on an outlined track. The pale swatch was invisible on the track in the preview check.
- **Style metrics** are a **hollow** bar (an outline in `--ink-3`, no fill) at every value, labelled "style". They differ from "around average" by shape, not colour alone.
- **Withheld or null:** no bar, only the reason text.
- **The bin is exported** with the percentile phrase; the frontend never computes it.
- **A change to this scale** re-runs the validator in both modes and is recorded here.

## 5. Tables
- **Rows:** 36 px (normal) / 28 px (compact).
- **Numbers:** right-aligned, tabular. The percentile is a number plus a 48 px inline bar in the scale colour.
- **Local and global** are two columns. The basis column carries a "basis" chip.
- **Population sentences** ("n 67 of 67 … with ≥ 39 long passes") go in a tooltip on the n cell and in the column header.
- **Notes:** an info icon with a popover.
- **Layout:** sticky header and first column. On mobile, horizontal scroll inside the table, never on the page.
- **Compact mode:** a header toggle (`localStorage`). It sets 28 px rows, numbers without inline bars, and collapsed captions. It never hides a status, flag or reliability mark.

## 6. Status chips
Icon + short text. The full reason is in a popover. All wording comes from the words table (W1, checked by the stats-reviewer).

| Chip | When | Icon |
|---|---|---|
| Withheld | a withheld dimension score (D5 statuses, `pending_xpass_deferred`) | `circle-slash` |
| Fewer minutes: less reliable | **only** near-gate **CB and FB-WB** profiles with **900–1,350 nominal minutes**, where it was measured (ADR 004 §14 sign-off note 3; median r_full ≈ 0.60). The flag is exported, and a test asserts it never appears for other groups or minutes. | `triangle-alert` in `--caution` |
| Same team | a neighbour from the player's own team (sign-off note 4) | `users` |
| Provisional | the similarity distance (until M5) | `flask-conical` |
| Style | a style metric (no good / bad reading) | none (text label) |
| Basis | the percentile used (local / global) | none |

Possession flags show their CI next to them (N1). Defending reasons use the N2 wording, and N3 applies only to the mover-test groups.

## 7. Motion
- **Chrome only:**
  - panels and sheets 160–200 ms `ease-out`, leaving along the same edge;
  - popovers 120 ms fade and scale from the trigger;
  - pressed states on pointer-down, 100 ms.
- **Implementation:** CSS transitions with Base UI's starting / ending-style attributes. No Motion library (W0 decision 9). **Gate before W5:** confirm the style map's mover dot → stint dots expansion works with CSS / SVG transitions; if not, stop and bring it back to the user.
- **Never on data:** numbers, bars, radar vertices, points and map positions render at their final value. No count-up, overshoot, stagger or idle animation.
- **`prefers-reduced-motion`:** every transition is instant; zoom and pan follow the pointer without easing.

## 8. Components (shadcn on Base UI)

| Need | Component |
|---|---|
| Search with keyboard results | Combobox |
| Compare | Sheet (Dialog): right on desktop, bottom on mobile |
| Notes, population, CI | Popover; Tooltip for short labels |
| Families (mobile) | Tabs |
| Fine roles, theme | Toggle group |
| Compact mode | Switch |
| Chips | Badge (our variant set) |
| Tables | plain `<table>` |
| Radar, map, bars | hand-written SVG, `d3-zoom`, `d3-scale` |
| Icons | `lucide-react` |

- **Profile shape** (W4, plan 006): a polygon of the family dimension scores at the top of the player page.
  - Withheld families are open, dashed vertices, never 0.
  - No overall number.
  - Compare overlays two shapes from the same position group only.
- **Radar:** shown from 768 px up. On phones the profile shape takes the place of the bars-only fallback; the family tables carry the per-metric detail.
- **Style map:** map first; a one-line caption ("Position shows style, not quality"); the explainer, legend and loadings collapsed.

## 9. Page hierarchy
- **Search:** in the header on every page; instant results (name or nickname, team, league, group). URLs and keys use `player_id` only.
- **Similar players** (landing): the player's identity strip, then the 10 neighbours (rank, name, team, league, distance, "most alike / most different" phrases, same-team chip, provisional chip). Compare opens in a sheet.
- **Player page:**
  - first: the identity strip (with the near-gate chip if any), the profile shape (withheld vertices dashed), and the radar from 768 px up;
  - then: family tables, signature traits, and links to Similar players and the map point.
- **Compare:**
  - first: two identity strips, the distance, ranks both ways, and the 5 most different and 3 most alike metrics;
  - then: side-by-side family tables and the two-series radar.
  - Mobile: player tabs A / B.
- **Style map:** the map at full width, with the axis names on the edges. Mobile: map first, explainer in a bottom sheet.

## 10. Review rules: generic-page tells to avoid
Our own wording. The list is inspired by the Anthropic `frontend-design` skill (Apache-2.0); none of its text is copied.
- No ALL-CAPS labels or eyebrow labels above headings; no meta strings joined with middle dots (show separate fields); no "→" appended to links or buttons.
- No warm cream background, no terracotta accent, no serif display face; surfaces are neutral.
- No identical rounded cards for everything. Use cards only for floating layers and identity strips; structure comes from spacing and hairlines.
- No decoration that encodes nothing (gradients, numbered markers on non-sequences).
- Motion only to show what changed after an action.
- Spend boldness in one place: the data views (radar, map). Everything around them stays quiet.

## 11. Accessibility
- WCAG AA text contrast; a visible focus ring (`--accent`, 2 px, offset 2 px);
- keyboard paths for search, results, compare and map points (list view);
- axe with no serious or critical violations;
- `prefers-contrast: more` uses solid surfaces and darker lines;
- forced-colours mode keeps bars readable through their printed numbers;
- the pinned guidelines checklist runs at the end of each W step.
