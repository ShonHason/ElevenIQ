import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const fx = async (page: Page, path: string) => (await page.request.get(`/data/${path}`)).json();
const KANTE = "3961-22-2-27";

async function shownKeys(page: Page) {
  return page.locator('table[data-table="gems"] tbody tr[data-key]').evaluateAll((els) => els.map((e) => e.getAttribute("data-key")));
}

test("Gem Finder: rows follow the exporter's order for each family; withheld families never order", async ({ page }) => {
  await page.goto("/#/gems/DM-CM");
  const gems = await fx(page, "gems/DM-CM.json");
  const keysOf = (fam: string) => (gems.order[fam] as number[]).map((i) => gems.rows[i].key);
  const scored = (gems.families as { family: string; definition: string | null }[]).filter((f) => f.definition);
  await expect(page.locator('table[data-table="gems"] tbody tr[data-key]')).toHaveCount(gems.rows.length);
  expect(await shownKeys(page)).toEqual(keysOf(gems.default_order));
  expect(gems.default_order).toBe("Progression");                      // user, DM-CM
  for (const f of scored) {
    await page.locator(`[data-order="${f.family}"]`).click();
    expect(await shownKeys(page)).toEqual(keysOf(f.family));
  }
  for (const f of ["Defending"]) {
    await expect(page.locator(`[data-order="${f}"]`)).toHaveCount(0);
    await expect(page.locator(`[data-withheld-family="${f}"]`)).toBeVisible();
    await expect(page.locator(`[data-filter="order"] option[value="${f}"]`)).toHaveCount(0);
  }
});

test("Gem Finder: league, team and minutes filters keep the order and match exported keys", async ({ page }) => {
  await page.goto("/#/gems/DM-CM");
  const gems = await fx(page, "gems/DM-CM.json");
  const ordered = (gems.order[gems.default_order] as number[]).map((i) => gems.rows[i]);
  await page.locator('[data-filter="league"]').selectOption("2");
  expect(await shownKeys(page)).toEqual(ordered.filter((r: { competition_id: number }) => r.competition_id === 2).map((r: { key: string }) => r.key));
  await page.locator('[data-filter="team"]').selectOption("22");                  // Leicester City
  expect(await shownKeys(page)).toEqual([KANTE]);
  await page.locator('[data-filter="league"]').selectOption("");
  await page.locator('[data-filter="minutes"]').selectOption("2700");
  expect(await shownKeys(page)).toEqual(ordered.filter((r: { minutes_tiers: string[] }) => r.minutes_tiers.includes("2700")).map((r: { key: string }) => r.key));
});

test("Gem Finder: players like Kanté lists his exported neighbours from other teams, in rank order", async ({ page }) => {
  await page.goto("/#/gems/DM-CM");
  const doc = await fx(page, `players/${KANTE}.json`);
  const want = (doc.neighbours.items as { key: string; same_team: boolean }[]).filter((n) => !n.same_team).map((n) => n.key);
  const box = page.getByRole("combobox", { name: "Players like…" });
  await box.fill("Kant");
  await box.press("Enter");
  const rows = page.locator('table[data-table="like"] tbody tr');
  await expect(rows).toHaveCount(want.length);
  expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-key")))).toEqual(want);
});

test("Methodology: every section from labels.json, with the defensive-rate sentence", async ({ page }) => {
  await page.goto("/#/methodology");
  const labels = await fx(page, "labels.json");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(labels.methodology.title);
  for (const s of labels.methodology.sections) {
    await expect(page.locator(`[data-section="${s.title}"] h2`)).toHaveText(s.title);
    for (const p of s.paragraphs) await expect(page.locator(`[data-section="${s.title}"]`)).toContainText(p);
  }
  await expect(page.getByText("does not remove team effects")).toBeVisible();
});

test("navigation reaches Gem Finder and Methodology", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Gem Finder" }).first().click();
  await expect(page).toHaveURL(/#\/gems$/);
  await expect(page.locator('table[data-table="gems"]')).toBeVisible();
  await page.getByRole("link", { name: "Methodology" }).first().click();
  await expect(page).toHaveURL(/#\/methodology$/);
});

for (const scheme of ["light", "dark"] as const) {
  for (const path of ["/#/gems/DM-CM", "/#/methodology"]) {
    test(`axe ${path} (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const r = await new AxeBuilder({ page }).analyze();
      expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
    });
  }
}

// DESIGN.md: no horizontal page scroll (tables scroll inside their own region). Regression:
// screen-reader-only spans escaped an unpositioned scroll region and widened phone pages.
for (const path of ["/#/player/3961-22-2-27", "/#/player/19677-220-11-27", `/#/compare/${KANTE}/5203-217-11-27`,
                    "/#/gems/DM-CM", "/#/methodology"]) {
  test(`no horizontal page scroll on ${path}`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeAttached();
    const [client, scroll] = await page.evaluate(() => [document.documentElement.clientWidth, document.documentElement.scrollWidth]);
    expect(scroll).toBeLessThanOrEqual(client);
  });
}
