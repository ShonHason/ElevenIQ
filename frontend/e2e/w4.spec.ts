import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const expected: Record<string, string[]> = JSON.parse(readFileSync(new URL("../src/lib/gap-order.expected.json", import.meta.url), "utf8"));
const fx = async (page: Page, path: string) => (await page.request.get(`/data/${path}`)).json();
const A = "3961-22-2-27", B = "5203-217-11-27";       // Kanté, Busquets (DM-CM)
const pair = [A, B].sort().join("|");

test("compare: top-6 rows are Python's gap order; no distance on the page", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const rows = page.locator('table[data-table="top"] tbody tr');
  await expect(rows).toHaveCount(6);
  const shown = await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-metric")));
  expect(shown).toEqual(expected[pair]);
  const text = (await page.locator("main").innerText()).toLowerCase();
  for (const j of ["distance", "similarity", "similar"]) expect(text).not.toContain(j);
});

test("compare: excluded metrics never headline but stay in the full table", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const group = await fx(page, "groups/DM-CM.json");
  const top = page.locator('table[data-table="top"]');
  await expect(top.locator("tbody tr")).toHaveCount(6);
  for (const m of group.metrics as { metric: string; gap_eligible: boolean }[]) {
    if (!m.gap_eligible) await expect(top.locator(`tr[data-metric="${m.metric}"]`)).toHaveCount(0);
  }
  await expect(page.locator('table[data-table="all"] tr[data-metric="long_pass_completion_pct"]')).toHaveCount(1);
});

test("compare: both players' cells trace to the fixture", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  for (const key of [A, B]) {
    const doc = await fx(page, `players/${key}.json`);
    const cells = page.locator(`table[data-table="all"] [data-src^="players/${key}.json#metrics."]`);
    const n = await cells.count();
    expect(n).toBeGreaterThan(40);
    for (let i = 0; i < n; i++) {
      const path = (await cells.nth(i).getAttribute("data-src"))!.split("#")[1].split(".");
      let v: unknown = doc;
      for (const p of path) v = (v as Record<string, unknown>)[p];
      await expect(cells.nth(i)).toHaveText(String(v));
    }
  }
});

test("compare: the defensive rate shows with its label and the exported values", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const [a, b] = [await fx(page, `players/${A}.json`), await fx(page, `players/${B}.json`)];
  for (const r of a.defensive_rates as { metric: string; rate_text: string }[]) {
    const rb = (b.defensive_rates as { metric: string; rate_text: string }[]).find((x) => x.metric === r.metric)!;
    const line = page.locator(`table[data-table="all"] [data-rate="${r.metric}"]`);
    await expect(line).toContainText(r.rate_text);
    await expect(line).toContainText(rb.rate_text);
    await expect(line).toContainText("per 100 opponent open-play passes (while on the pitch)");
  }
  const text = (await page.locator("main").innerText()).toLowerCase();
  expect(text).not.toContain("adjusted");
  expect(text).not.toContain("corrected");
});

test("compare: two shapes overlaid with a legend", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const fig = page.locator("figure");
  await expect(fig.locator(`[data-src^="players/${A}.json#dna.dimensions."]`).first()).toBeVisible();
  await expect(fig.locator(`[data-src^="players/${B}.json#dna.dimensions."]`).first()).toBeVisible();
  const b = await fx(page, `players/${B}.json`);
  await expect(fig.getByText(b.identity.display_name, { exact: true }).first()).toBeVisible();
});

test("compare: mixed position groups are refused", async ({ page }) => {
  await page.goto(`/#/compare/${A}/5503-217-11-27`);                      // Messi, AM-Winger
  await expect(page.getByText("Only players in the same position group can be compared.")).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);
});

test("compare picker offers only the same group, then opens the compare page", async ({ page }) => {
  await page.goto(`/#/player/${A}`);
  const picker = page.getByRole("combobox", { name: "Compare with a player in the same position" });
  await picker.fill("messi");
  await expect(page.getByRole("status")).toHaveText("No player found");
  await picker.fill("busq");
  await picker.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#/compare/${A}/${B}$`));
});

for (const scheme of ["light", "dark"] as const) {
  test(`axe compare (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto(`/#/compare/${A}/${B}`);
    await expect(page.locator('table[data-table="top"]')).toBeVisible();
    const r = await new AxeBuilder({ page }).analyze();
    expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
  });
}


test("compare: a player without a profile is refused with their status", async ({ page }) => {
  await page.goto(`/#/compare/${A}/2955-143-7-27`);                       // below the minutes gate
  await expect(page.getByText(/below the minutes gate/)).toBeVisible();
  await expect(page.getByText("Only players in the same position group can be compared.")).toHaveCount(0);
});

test("compare: each rate names its player for screen readers", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const line = page.locator('table[data-table="all"] [data-rate="interceptions_p90"]');
  await expect(line).toContainText("N'Golo Kanté:");
});

test("compare: the league note and the hollow-bar legend", async ({ page }) => {
  await page.goto(`/#/compare/${A}/${B}`);
  const labels = await fx(page, "labels.json");
  await expect(page.locator('[data-note="league"]')).toHaveText(labels.words.compare_league_note);
  await expect(page.locator('[data-legend="hollow"]').first()).toHaveText(labels.words.hollow_legend);
});
